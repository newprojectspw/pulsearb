"""Cliente do WS de mercado do CLOB — wss://ws-subscriptions-clob.polymarket.com/ws/market.

Protocolo verificado em docs/API_NOTES.md seção 6.1:
- frame inicial: {"type":"market","assets_ids":[...],"custom_feature_enabled":true}
  (true para receber best bid/ask e eventos de lifecycle, incluindo resolução)
- subscribe/unsubscribe dinâmicos: {"operation":"subscribe"|"unsubscribe","assets_ids":[...]}
- heartbeat de APLICAÇÃO: texto "PING" a cada 10s; morto após 30s sem "PONG".
  Não é o ping/pong do protocolo WebSocket.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

import orjson
import websockets

from pulsearb.feeds.base import FeedEvent, OnEvent, ReconnectingFeed

# Heartbeat de aplicação do CLOB: texto puro, NUNCA binário (API_NOTES 6.1).
PING = "PING"
PONG = "PONG"
# O FeedEvent.raw chega sempre em bytes; esta é a forma comparável.
PONG_BYTES = PONG.encode()

# Tipos que o CLOB usa para anunciar resolução. Conjunto (e não igualdade
# solta) para que um tipo novo apareça na contagem por tipo em vez de sumir.
RESOLUTION_EVENT_TYPES = frozenset({"market_resolved", "resolution"})


class PolyMarketWsFeed(ReconnectingFeed):
    """Feed do livro CLOB com heartbeat de aplicação e subscribe dinâmico."""

    def __init__(
        self,
        *,
        url: str,
        user_agent: str,
        token_ids: list[str] | None = None,
        custom_feature_enabled: bool = True,
        ping_interval_seconds: float = 10.0,
        pong_stale_seconds: float = 30.0,
        on_event: OnEvent | None = None,
        **kwargs: Any,
    ) -> None:
        # O CLOB tem heartbeat de APLICAÇÃO (PING/PONG texto), então o ping
        # do protocolo WS é redundante aqui — mas só aqui. RTDS e Binance
        # ficam com o keepalive da lib, que é o default da base.
        kwargs.setdefault("ws_ping_interval", None)
        super().__init__(
            name="poly_ws", url=url, user_agent=user_agent, on_event=on_event, **kwargs
        )
        self.token_ids: set[str] = set(token_ids or [])
        self.custom_feature_enabled = custom_feature_enabled
        self.ping_interval_seconds = ping_interval_seconds
        self.pong_stale_seconds = pong_stale_seconds
        self._last_pong_mono: float = 0.0
        self._heartbeat_task: asyncio.Task[None] | None = None
        self.pong_count = 0

    # ------------------------------------------------------------------ frames
    def initial_frame(self) -> str:
        return orjson.dumps(
            {
                "type": "market",
                "assets_ids": sorted(self.token_ids),
                "custom_feature_enabled": self.custom_feature_enabled,
            }
        ).decode()

    @staticmethod
    def subscribe_frame(token_ids: list[str], custom_feature_enabled: bool = True) -> str:
        return orjson.dumps(
            {
                "operation": "subscribe",
                "assets_ids": token_ids,
                "custom_feature_enabled": custom_feature_enabled,
            }
        ).decode()

    @staticmethod
    def unsubscribe_frame(token_ids: list[str]) -> str:
        return orjson.dumps(
            {"operation": "unsubscribe", "assets_ids": token_ids}
        ).decode()

    # -------------------------------------------------------------- subscribes
    async def subscribe(self, token_ids: list[str]) -> None:
        """Adiciona tokens; efetivo já e após qualquer reconexão (estado local)."""
        new = [t for t in token_ids if t not in self.token_ids]
        self.token_ids.update(new)
        if new and self._ws is not None:
            await self.send_frame(self.subscribe_frame(new, self.custom_feature_enabled))

    async def unsubscribe(self, token_ids: list[str]) -> None:
        gone = [t for t in token_ids if t in self.token_ids]
        self.token_ids.difference_update(gone)
        if gone and self._ws is not None:
            await self.send_frame(self.unsubscribe_frame(gone))

    # ------------------------------------------------------------------- ciclo
    async def _on_connected(self, ws: websockets.ClientConnection) -> None:
        self._last_pong_mono = time.monotonic()
        if self.token_ids:
            await self.send_frame(self.initial_frame(), ws)
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat(ws), name="poly-ws-heartbeat"
        )

    async def _receive_loop(self, ws: websockets.ClientConnection) -> None:
        try:
            await super()._receive_loop(ws)
        finally:
            if self._heartbeat_task is not None:
                self._heartbeat_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._heartbeat_task
                self._heartbeat_task = None

    async def _heartbeat(self, ws: websockets.ClientConnection) -> None:
        """PING a cada 10s; 30s sem PONG = conexão morta, força reconexão."""
        while True:
            await asyncio.sleep(self.ping_interval_seconds)
            if time.monotonic() - self._last_pong_mono > self.pong_stale_seconds:
                self.log.warning(
                    "heartbeat morto: sem PONG", limite_s=self.pong_stale_seconds
                )
                await ws.close(code=1000, reason="heartbeat timeout")
                return
            await self.send_frame(PING, ws)

    async def _handle_message(self, event: FeedEvent) -> None:
        if event.raw.strip() == PONG_BYTES:
            self._last_pong_mono = time.monotonic()
            self.pong_count += 1
            return
