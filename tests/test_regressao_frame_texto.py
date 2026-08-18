"""REGRESSÃO: todo frame enviado por qualquer feed precisa ser TEXTO.

O bug que este arquivo existe para impedir (produção, VPS de Londres):

    ConnectionClosedError: received 1003 (unsupported data)
    Binary is not supported; then sent 1003 (unsupported data)

Causa: `orjson.dumps()` devolve `bytes`, e a lib `websockets` decide o tipo
do frame pelo tipo do argumento — `str` vira frame de texto, `bytes` vira
frame BINÁRIO. O RTDS da Polymarket recusa binário e fecha a conexão na
hora, e o recorder entrava em loop de reconexão indefinido.

O detalhe cruel: `scripts/smoke_feeds.py` usava `json.dumps()` (que devolve
`str`) e funcionava perfeitamente contra o MESMO endpoint. O smoke passava,
a produção quebrava, e os dois "falavam o mesmo protocolo".

Três camadas de defesa, e este arquivo cobre as três:
1. os construtores de frame devolvem `str` (checagem de tipo)
2. `ReconnectingFeed.send_frame` recusa não-str (checagem estrutural)
3. o servidor fake confere o tipo do frame que chegou pelo fio (fim a fim)
"""

from __future__ import annotations

import asyncio

import pytest
from tests.test_feeds_ws import _wait_for, server  # noqa: F401

from pulsearb.feeds.binance_ws import BinanceWsFeed
from pulsearb.feeds.poly_ws import PING, PONG, PolyMarketWsFeed
from pulsearb.feeds.rtds import RtdsFeed


# ------------------------------------- 1. construtores devolvem str
def test_frames_do_rtds_sao_str():
    feed = RtdsFeed(url="ws://x", user_agent="ua", assets=["btc"])
    assert isinstance(feed.subscribe_frame(), str)


def test_frames_do_poly_sao_str():
    feed = PolyMarketWsFeed(url="ws://x", user_agent="ua", token_ids=["a"])
    assert isinstance(feed.initial_frame(), str)
    assert isinstance(PolyMarketWsFeed.subscribe_frame(["a"]), str)
    assert isinstance(PolyMarketWsFeed.unsubscribe_frame(["a"]), str)


def test_heartbeat_do_clob_e_texto():
    # A doc do CLOB é explícita: PING/PONG são texto puro (API_NOTES 6.1).
    assert isinstance(PING, str)
    assert isinstance(PONG, str)


# ------------------------------------- 2. send_frame recusa bytes
async def test_send_frame_recusa_bytes(server):  # noqa: F811
    feed = RtdsFeed(url=server.url, user_agent="ua", assets=["btc"])
    await feed.start()
    try:
        await _wait_for(lambda: feed.connected)
        with pytest.raises(TypeError, match="precisa ser str"):
            await feed.send_frame(b'{"action":"subscribe"}')
    finally:
        await feed.stop()


async def test_mensagem_de_erro_aponta_a_causa(server):  # noqa: F811
    """Quem tropeçar de novo tem que ler a solução no erro, não deduzir."""
    feed = RtdsFeed(url=server.url, user_agent="ua", assets=["btc"])
    await feed.start()
    try:
        await _wait_for(lambda: feed.connected)
        with pytest.raises(TypeError) as erro:
            await feed.send_frame(b"{}")
        assert "orjson" in str(erro.value) and "decode" in str(erro.value)
    finally:
        await feed.stop()


# ------------------------------------- 3. fim a fim: o que chega pelo fio
async def test_rtds_manda_apenas_frames_de_texto(server):  # noqa: F811
    """O caso exato que derrubava a produção."""
    feed = RtdsFeed(url=server.url, user_agent="ua", assets=["btc"])
    await feed.start()
    try:
        await _wait_for(lambda: bool(server.received_raw))
        assert server.received_raw, "nenhum frame chegou"
        for frame in server.received_raw:
            assert isinstance(frame, str), (
                f"frame BINÁRIO enviado ({type(frame).__name__}) — "
                "o RTDS fecha a conexão com 1003 ao receber isso"
            )
    finally:
        await feed.stop()


async def test_poly_ws_manda_apenas_frames_de_texto(server):  # noqa: F811
    """Cobre os quatro caminhos de envio: inicial, subscribe, unsubscribe, PING."""
    feed = PolyMarketWsFeed(
        url=server.url,
        user_agent="ua",
        token_ids=["a"],
        ping_interval_seconds=0.05,
    )
    await feed.start()
    try:
        await _wait_for(lambda: bool(server.received_raw))     # frame inicial
        await feed.subscribe(["b"])                            # subscribe
        await feed.unsubscribe(["a"])                          # unsubscribe
        await _wait_for(lambda: feed.pong_count >= 1)          # PING
        assert len(server.received_raw) >= 4
        for frame in server.received_raw:
            assert isinstance(frame, str), (
                f"frame BINÁRIO enviado ({type(frame).__name__})"
            )
    finally:
        await feed.stop()


async def test_binance_nao_manda_frame_nenhum(server):  # noqa: F811
    """Os streams vão na URL — se um dia passar a mandar frame, tem que ser texto."""
    feed = BinanceWsFeed(assets=["btc"], user_agent="ua", base_url=server.url)
    await feed.start()
    try:
        await _wait_for(lambda: feed.connected)
        await asyncio.sleep(0.05)
        assert server.received_raw == []
    finally:
        await feed.stop()


async def test_todos_os_frames_sao_json_valido_em_texto(server):  # noqa: F811
    """Texto sim, mas texto que o servidor consegue parsear."""
    import json

    feed = RtdsFeed(url=server.url, user_agent="ua", assets=["btc"])
    await feed.start()
    try:
        await _wait_for(lambda: bool(server.received_raw))
        frame = server.received_raw[0]
        assert isinstance(frame, str)
        assert json.loads(frame)["action"] == "subscribe"
    finally:
        await feed.stop()
