"""Base comum dos feeds WS: reconexão com backoff+jitter, watchdog, timestamps.

Regras do hot path aplicadas aqui:
- timestamp MONOTÔNICO de chegada em cada mensagem (time.monotonic_ns());
  time.time_ns() é capturado junto, mas só para registro/gravação
- reconexão com backoff exponencial + jitter, sem teto de tentativas
- watchdog: feed sem mensagem por mais de `stale_after_seconds` marca o feed
  como parado (quem consome decide zerar posição-alvo e pausar entradas)
- nenhum I/O de disco síncrono aqui; consumo via callback ou fila
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import websockets

from pulsearb.obs import get_logger


@dataclass(frozen=True, slots=True)
class FeedEvent:
    """Uma mensagem de feed com os dois relógios capturados na chegada."""

    source: str          # ex.: "rtds", "poly_ws"
    ts_mono_ns: int      # relógio monotônico — medição de latência
    ts_wall_ns: int      # relógio de parede — só registro/gravação
    raw: bytes           # payload cru, como veio do fio
    parsed: Any          # dict do orjson (ou None se não-JSON, ex.: "PONG")


OnEvent = Callable[[FeedEvent], Awaitable[None] | None]


class ReconnectingFeed:
    """Loop de conexão WS com resubscribe automático.

    Subclasses implementam:
      - `_on_connected(ws)`: envia frames de subscribe
      - `_handle_message(event)`: processa uma mensagem (já com FeedEvent)
    """

    def __init__(
        self,
        *,
        name: str,
        url: str,
        user_agent: str,
        stale_after_seconds: float = 2.0,
        reconnect_initial_seconds: float = 0.5,
        reconnect_max_seconds: float = 30.0,
        on_event: OnEvent | None = None,
    ) -> None:
        self.name = name
        self.url = url
        self.user_agent = user_agent
        self.stale_after_seconds = stale_after_seconds
        self.reconnect_initial_seconds = reconnect_initial_seconds
        self.reconnect_max_seconds = reconnect_max_seconds
        self.on_event = on_event
        self.log = get_logger(f"pulsearb.feeds.{name}")

        self._task: asyncio.Task[None] | None = None
        self._ws: websockets.ClientConnection | None = None
        self._stopped = asyncio.Event()
        self._last_msg_mono_ns: int = 0
        self._connected = False
        self.reconnect_count = 0
        self.message_count = 0

    # ------------------------------------------------------------------ estado
    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def last_message_age_seconds(self) -> float:
        """Idade da última mensagem. inf se nunca recebeu nada."""
        if self._last_msg_mono_ns == 0:
            return float("inf")
        return (time.monotonic_ns() - self._last_msg_mono_ns) / 1e9

    @property
    def stale(self) -> bool:
        """Watchdog: True = feed parado, não confiar no dado para decidir."""
        return not self._connected or self.last_message_age_seconds > self.stale_after_seconds

    # ------------------------------------------------------------------ ciclo
    async def start(self) -> None:
        if self._task is not None:
            raise RuntimeError(f"feed {self.name} já iniciado")
        self._stopped.clear()
        self._task = asyncio.create_task(self._run(), name=f"feed-{self.name}")

    async def stop(self) -> None:
        self._stopped.set()
        if self._ws is not None:
            await self._ws.close()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        backoff = self.reconnect_initial_seconds
        while not self._stopped.is_set():
            try:
                async with websockets.connect(
                    self.url,
                    additional_headers={"User-Agent": self.user_agent},
                    max_queue=4096,
                    open_timeout=10,
                    ping_interval=None,  # heartbeat é responsabilidade da subclasse
                ) as ws:
                    self._ws = ws
                    self._connected = True
                    self.log.info("conectado", url=self.url)
                    await self._on_connected(ws)
                    backoff = self.reconnect_initial_seconds  # conexão boa zera o backoff
                    await self._receive_loop(ws)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.log.warning(
                    "conexão caiu", erro=f"{type(exc).__name__}: {exc}", backoff_s=round(backoff, 2)
                )
            finally:
                self._connected = False
                self._ws = None
            if self._stopped.is_set():
                return
            self.reconnect_count += 1
            # jitter uniforme em [0.5, 1.5)x para dessincronizar reconexões
            await asyncio.sleep(backoff * (0.5 + random.random()))
            backoff = min(backoff * 2, self.reconnect_max_seconds)

    async def _receive_loop(self, ws: websockets.ClientConnection) -> None:
        async for message in ws:
            ts_mono_ns = time.monotonic_ns()
            ts_wall_ns = time.time_ns()
            raw = message if isinstance(message, bytes) else message.encode()
            parsed = self._parse(raw)
            event = FeedEvent(
                source=self.name,
                ts_mono_ns=ts_mono_ns,
                ts_wall_ns=ts_wall_ns,
                raw=raw,
                parsed=parsed,
            )
            self._last_msg_mono_ns = ts_mono_ns
            self.message_count += 1
            await self._handle_message(event)
            if self.on_event is not None:
                result = self.on_event(event)
                if result is not None:
                    await result

    @staticmethod
    def _parse(raw: bytes) -> Any:
        import orjson

        try:
            return orjson.loads(raw)
        except orjson.JSONDecodeError:
            return None  # ex.: "PONG" do heartbeat do CLOB

    # ------------------------------------------------------------- envio
    async def send_frame(
        self, frame: str, ws: websockets.ClientConnection | None = None
    ) -> None:
        """Envia um frame SEMPRE como texto.

        Existe para tornar a invariante estrutural, não só convencional: a
        lib `websockets` decide o tipo de frame pelo tipo do argumento —
        `str` vira frame de texto, `bytes` vira BINÁRIO. O RTDS da Polymarket
        fecha a conexão com `1003 unsupported data / Binary is not supported`
        ao receber binário, e o recorder entrava em loop de reconexão em
        produção por causa disso (o `orjson.dumps` devolve bytes).

        Passar bytes aqui é erro de programação, não condição de runtime:
        levanta TypeError em vez de virar uma desconexão silenciosa em
        produção três semanas depois.
        """
        if not isinstance(frame, str):
            raise TypeError(
                f"frame de WS precisa ser str (texto), veio {type(frame).__name__}. "
                "orjson.dumps() devolve bytes — use .decode()."
            )
        destino = ws if ws is not None else self._ws
        if destino is None:
            raise RuntimeError(f"feed {self.name} não está conectado")
        await destino.send(frame)

    # ------------------------------------------------------------ p/ subclasse
    async def _on_connected(self, ws: websockets.ClientConnection) -> None:
        raise NotImplementedError

    async def _handle_message(self, event: FeedEvent) -> None:
        """Hook opcional; o default não faz nada além do on_event."""
