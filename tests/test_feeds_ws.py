"""Feeds WS contra um servidor fake local: subscribe, heartbeat, reconexão.

Servidor `websockets` em 127.0.0.1 — é loopback, não rede externa; a regra do
M1 (nenhum teste depende de rede externa) continua respeitada.
"""

from __future__ import annotations

import asyncio
import json

import orjson
import pytest
import websockets

from pulsearb.feeds.poly_ws import PING, PONG, PONG_BYTES, PolyMarketWsFeed
from pulsearb.feeds.rtds import RtdsFeed


class FakeWsServer:
    """Servidor de teste: guarda o que recebeu, responde PONG, envia o que mandarem."""

    def __init__(self) -> None:
        self.received: list[str] = []
        # Guarda o objeto CRU como o websockets entregou: str = frame de
        # texto, bytes = frame BINÁRIO. É o que permite provar que o cliente
        # nunca manda binário (o RTDS fecha com 1003 se mandar).
        self.received_raw: list[str | bytes] = []
        self.connections: int = 0
        self.server: websockets.Server | None = None
        self.to_send: list[str] = []
        self.drop_next: bool = False
        self._sockets: list[websockets.ServerConnection] = []

    @property
    def url(self) -> str:
        assert self.server is not None
        host, port = self.server.sockets[0].getsockname()[:2]
        return f"ws://{host}:{port}"

    async def start(self) -> None:
        self.server = await websockets.serve(self._handler, "127.0.0.1", 0)

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    async def _handler(self, ws: websockets.ServerConnection) -> None:
        self.connections += 1
        self._sockets.append(ws)
        if self.drop_next:
            self.drop_next = False
            await ws.close()
            return
        for message in self.to_send:
            await ws.send(message)
        try:
            async for message in ws:
                self.received_raw.append(message)
                text = message if isinstance(message, str) else message.decode()
                self.received.append(text)
                if text.strip() == "PING":
                    await ws.send("PONG")
        except websockets.ConnectionClosed:
            pass

    async def broadcast(self, message: str) -> None:
        for ws in list(self._sockets):
            try:
                await ws.send(message)
            except websockets.ConnectionClosed:
                pass


@pytest.fixture
async def server():
    srv = FakeWsServer()
    await srv.start()
    yield srv
    await srv.stop()


async def _wait_for(predicate, limite_s: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + limite_s
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condição não satisfeita a tempo")


# ------------------------------------------------------------------- RTDS
async def test_rtds_envia_subscribe_ao_conectar(server):
    feed = RtdsFeed(url=server.url, user_agent="pulsearb-test", assets=["btc"])
    await feed.start()
    try:
        await _wait_for(lambda: bool(server.received))
        frame = json.loads(server.received[0])
        assert frame["action"] == "subscribe"
        assert {s["topic"] for s in frame["subscriptions"]} == {
            "crypto_prices",
            "crypto_prices_twap_sixty",
        }
    finally:
        await feed.stop()


async def test_rtds_processa_tick_e_marca_nao_stale(server, rtds_events):
    server.to_send = [json.dumps(rtds_events["twap_sixty_btc"])]
    ticks = []
    feed = RtdsFeed(
        url=server.url, user_agent="ua", assets=["btc"], on_tick=ticks.append
    )
    await feed.start()
    try:
        await _wait_for(lambda: bool(ticks))
        assert ticks[0].asset == "btc"
        assert ticks[0].price == pytest.approx(118432.17)
        assert feed.message_count == 1
        assert not feed.stale
        # timestamps dos dois relógios foram capturados
        assert ticks[0].ts_mono_ns > 0
        assert ticks[0].ts_wall_ns > 0
    finally:
        await feed.stop()


async def test_watchdog_marca_stale_sem_ticks(server):
    feed = RtdsFeed(
        url=server.url, user_agent="ua", assets=["btc"], stale_after_seconds=0.05
    )
    await feed.start()
    try:
        await _wait_for(lambda: feed.connected)
        # conectado mas sem mensagem nenhuma = parado
        assert feed.stale
        assert feed.last_message_age_seconds == float("inf")
    finally:
        await feed.stop()


async def test_reconecta_apos_queda(server):
    server.drop_next = True
    feed = RtdsFeed(
        url=server.url,
        user_agent="ua",
        assets=["btc"],
        reconnect_initial_seconds=0.01,
        reconnect_max_seconds=0.05,
    )
    await feed.start()
    try:
        await _wait_for(lambda: server.connections >= 2, limite_s=5.0)
        assert feed.reconnect_count >= 1
        # resubscribe aconteceu na reconexão
        await _wait_for(lambda: bool(server.received))
    finally:
        await feed.stop()


# ---------------------------------------------------------------- poly_ws
async def test_poly_ws_frame_inicial(server):
    feed = PolyMarketWsFeed(
        url=server.url, user_agent="ua", token_ids=["tokenA", "tokenB"]
    )
    await feed.start()
    try:
        await _wait_for(lambda: bool(server.received))
        frame = json.loads(server.received[0])
        assert frame["type"] == "market"
        assert sorted(frame["assets_ids"]) == ["tokenA", "tokenB"]
        # true para receber best bid/ask e evento de resolução (API_NOTES 6.1)
        assert frame["custom_feature_enabled"] is True
    finally:
        await feed.stop()


async def test_poly_ws_sem_tokens_nao_manda_frame_inicial(server):
    feed = PolyMarketWsFeed(url=server.url, user_agent="ua")
    await feed.start()
    try:
        await _wait_for(lambda: feed.connected)
        await asyncio.sleep(0.05)
        assert server.received == []
    finally:
        await feed.stop()


async def test_poly_ws_subscribe_dinamico(server):
    feed = PolyMarketWsFeed(url=server.url, user_agent="ua", token_ids=["a"])
    await feed.start()
    try:
        await _wait_for(lambda: bool(server.received))
        await feed.subscribe(["b", "c"])
        await _wait_for(lambda: len(server.received) >= 2)
        frame = json.loads(server.received[1])
        assert frame["operation"] == "subscribe"
        assert sorted(frame["assets_ids"]) == ["b", "c"]
        assert feed.token_ids == {"a", "b", "c"}

        await feed.unsubscribe(["a"])
        await _wait_for(lambda: len(server.received) >= 3)
        frame = json.loads(server.received[2])
        assert frame["operation"] == "unsubscribe"
        assert frame["assets_ids"] == ["a"]
        assert feed.token_ids == {"b", "c"}
    finally:
        await feed.stop()


async def test_subscribe_nao_reenvia_token_ja_assinado(server):
    feed = PolyMarketWsFeed(url=server.url, user_agent="ua", token_ids=["a"])
    await feed.start()
    try:
        await _wait_for(lambda: bool(server.received))
        await feed.subscribe(["a"])
        await asyncio.sleep(0.05)
        assert len(server.received) == 1
    finally:
        await feed.stop()


async def test_heartbeat_ping_pong(server):
    feed = PolyMarketWsFeed(
        url=server.url,
        user_agent="ua",
        token_ids=["a"],
        ping_interval_seconds=0.05,
        pong_stale_seconds=5.0,
    )
    await feed.start()
    try:
        await _wait_for(lambda: feed.pong_count >= 2, limite_s=3.0)
        assert PING in server.received
        # PONG não polui a contagem de ticks de mercado? Conta como mensagem
        # recebida, mas é tratado e não vira evento de book.
        assert feed.pong_count >= 2
    finally:
        await feed.stop()


async def test_pong_atualiza_watchdog_de_heartbeat(server):
    feed = PolyMarketWsFeed(
        url=server.url, user_agent="ua", token_ids=["a"], ping_interval_seconds=0.05
    )
    await feed.start()
    try:
        await _wait_for(lambda: feed.pong_count >= 1)
        antes = feed._last_pong_mono
        await _wait_for(lambda: feed.pong_count >= 3, limite_s=3.0)
        assert feed._last_pong_mono > antes
    finally:
        await feed.stop()


async def test_book_event_chega_ao_callback(server, clob_ws_events):
    server.to_send = [json.dumps(clob_ws_events["book_snapshot"])]
    eventos = []
    feed = PolyMarketWsFeed(
        url=server.url, user_agent="ua", token_ids=["a"], on_event=eventos.append
    )
    await feed.start()
    try:
        await _wait_for(lambda: bool(eventos))
        assert eventos[0].parsed["event_type"] == "book"
        assert eventos[0].source == "poly_ws"
    finally:
        await feed.stop()


async def test_pong_nao_quebra_o_parser(server):
    """PONG é texto puro, não JSON — o parser devolve None sem explodir."""
    server.to_send = [PONG]
    eventos = []
    feed = PolyMarketWsFeed(url=server.url, user_agent="ua", on_event=eventos.append)
    await feed.start()
    try:
        await _wait_for(lambda: bool(eventos))
        assert eventos[0].parsed is None
        assert eventos[0].raw == PONG_BYTES
        assert feed.pong_count == 1
    finally:
        await feed.stop()


def test_frames_sao_json_valido():
    assert orjson.loads(
        PolyMarketWsFeed.subscribe_frame(["x"])
    ) == {"operation": "subscribe", "assets_ids": ["x"], "custom_feature_enabled": True}
    assert orjson.loads(PolyMarketWsFeed.unsubscribe_frame(["x"])) == {
        "operation": "unsubscribe",
        "assets_ids": ["x"],
    }
