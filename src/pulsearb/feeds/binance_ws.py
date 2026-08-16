"""Feed direto da Binance — o preço-verdade das janelas HORÁRIAS.

Por que existe, se o RTDS já repassa o spot da Binance: a janela de 1h resolve
pelo **candle 1h BTC/USDT**, comparando `close >= open` (docs/API_NOTES.md
12.2b). O RTDS entrega ticks de preço, não candles — o `open` da hora e o
estado do candle corrente só vêm do stream `kline_1h` da própria Binance.
O `bookTicker` entra junto porque é o melhor preço disponível em tempo real
(atualização a cada mudança de topo de livro), e é ele que aproxima o `close`
enquanto a hora não fecha.

Protocolo VERIFICADO na documentação oficial da Binance
(github.com/binance/binance-spot-api-docs, `web-socket-streams.md`, lido em
2026-08-16):

- endpoint base: `wss://stream.binance.com:9443` (ou :443)
- stream combinado: `/stream?streams=<a>/<b>/<c>`; eventos vêm embrulhados
  como `{"stream": "<nome>", "data": <payload>}`
- símbolos sempre em MINÚSCULAS
- nomes: `<symbol>@bookTicker` e `<symbol>@kline_<intervalo>` (`1h` é válido)
- klines de `kline_1h` abrem e fecham em **UTC+0**
- o servidor manda um `ping frame` a cada 20s e espera `pong` em até 1 min —
  aqui isso é delegado à lib `websockets` (ping_interval=None desliga o ping
  do CLIENTE, mas responder ping do servidor é automático)
- uma conexão vale no máximo **24h**: a queda é esperada, não é erro. O
  ReconnectingFeed já reconecta com backoff.

Alinhamento de fuso: a janela horária da Polymarket é nominal em
America/New_York, cujo offset é sempre um número inteiro de horas. Logo as
fronteiras de hora de NY coincidem com as de UTC, e `kline_1h` (UTC) é o
candle certo — sem necessidade da variante com offset de fuso.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import websockets

from pulsearb.feeds.base import FeedEvent, OnEvent, ReconnectingFeed

# Endpoint verificado na doc oficial (web-socket-streams.md, "General WSS information").
BINANCE_STREAM_BASE = "wss://stream.binance.com:9443"

# Par de cotação usado pelas regras de resolução da Polymarket: BTC/USDT.
QUOTE = "usdt"
KLINE_INTERVAL = "1h"


def symbol_for(asset: str) -> str:
    """'btc' → 'btcusdt' (minúsculo, como a doc exige)."""
    return f"{asset.lower()}{QUOTE}"


def build_stream_url(assets: list[str], *, base: str = BINANCE_STREAM_BASE) -> str:
    """URL do stream combinado com bookTicker + kline_1h de cada ativo."""
    streams: list[str] = []
    for asset in assets:
        symbol = symbol_for(asset)
        streams.append(f"{symbol}@bookTicker")
        streams.append(f"{symbol}@kline_{KLINE_INTERVAL}")
    return f"{base}/stream?streams={'/'.join(streams)}"


@dataclass(frozen=True, slots=True)
class BookTicker:
    """Melhor bid/ask do spot — aproxima o `close` da hora em andamento."""

    asset: str
    bid: float
    ask: float
    bid_qty: float
    ask_qty: float
    update_id: int
    ts_mono_ns: int
    ts_wall_ns: int

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0


@dataclass(frozen=True, slots=True)
class Kline:
    """Candle 1h. É a fonte de verdade da janela horária.

    `is_closed` (campo `x`) marca o candle definitivo: enquanto False, `close`
    é o preço corrente e muda a cada atualização.
    """

    asset: str
    interval: str
    open_time_ms: int
    close_time_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool
    ts_mono_ns: int
    ts_wall_ns: int

    @property
    def resolves_up(self) -> bool:
        """Regra da Polymarket para a janela horária: close >= open → Up.

        Empate resolve Up nos dois jogos (API_NOTES 12.4/12.2b). Enquanto
        `is_closed` é False isto é uma PROJEÇÃO, não o resultado.
        """
        return self.close >= self.open


def parse_binance_event(
    parsed: Any, ts_mono_ns: int, ts_wall_ns: int
) -> BookTicker | Kline | None:
    """Extrai BookTicker ou Kline de um evento do stream combinado.

    Tolerante: formato inesperado devolve None. O recorder grava o cru de
    qualquer jeito; o engine só consome o que entende.
    """
    if not isinstance(parsed, dict):
        return None
    # Stream combinado embrulha em {"stream": ..., "data": ...}.
    data = parsed.get("data") if "data" in parsed else parsed
    if not isinstance(data, dict):
        return None

    if data.get("e") == "kline":
        return _parse_kline(data, ts_mono_ns, ts_wall_ns)
    # bookTicker não tem campo "e"; identifica-se por b/a/s.
    if {"b", "a", "s"} <= data.keys():
        return _parse_book_ticker(data, ts_mono_ns, ts_wall_ns)
    return None


def _asset_from_symbol(symbol: str) -> str:
    lowered = symbol.lower()
    return lowered[: -len(QUOTE)] if lowered.endswith(QUOTE) else lowered


def _parse_book_ticker(data: dict[str, Any], mono: int, wall: int) -> BookTicker | None:
    try:
        return BookTicker(
            asset=_asset_from_symbol(str(data["s"])),
            bid=float(data["b"]),
            ask=float(data["a"]),
            bid_qty=float(data.get("B", 0) or 0),
            ask_qty=float(data.get("A", 0) or 0),
            update_id=int(data.get("u", 0) or 0),
            ts_mono_ns=mono,
            ts_wall_ns=wall,
        )
    except (KeyError, TypeError, ValueError):
        return None


def _parse_kline(data: dict[str, Any], mono: int, wall: int) -> Kline | None:
    candle = data.get("k")
    if not isinstance(candle, dict):
        return None
    try:
        return Kline(
            asset=_asset_from_symbol(str(candle["s"])),
            interval=str(candle["i"]),
            open_time_ms=int(candle["t"]),
            close_time_ms=int(candle["T"]),
            open=float(candle["o"]),
            high=float(candle["h"]),
            low=float(candle["l"]),
            close=float(candle["c"]),
            volume=float(candle.get("v", 0) or 0),
            is_closed=bool(candle.get("x", False)),
            ts_mono_ns=mono,
            ts_wall_ns=wall,
        )
    except (KeyError, TypeError, ValueError):
        return None


class BinanceWsFeed(ReconnectingFeed):
    """Stream combinado bookTicker + kline_1h dos ativos configurados.

    Os streams vão na URL, então não há frame de subscribe: conectar já é
    assinar. Isso também significa que trocar de ativo exige reconectar — o
    que é aceitável, porque a lista de ativos é de configuração e não muda em
    tempo de execução.
    """

    def __init__(
        self,
        *,
        assets: list[str],
        user_agent: str,
        base_url: str = BINANCE_STREAM_BASE,
        on_book_ticker: Any = None,  # Callable[[BookTicker], None] | None
        on_kline: Any = None,        # Callable[[Kline], None] | None
        on_event: OnEvent | None = None,
        **kwargs: Any,
    ) -> None:
        self.assets = [a.lower() for a in assets]
        super().__init__(
            name="binance_ws",
            url=build_stream_url(self.assets, base=base_url),
            user_agent=user_agent,
            on_event=on_event,
            **kwargs,
        )
        self.on_book_ticker = on_book_ticker
        self.on_kline = on_kline
        self.last_book_ticker: dict[str, BookTicker] = {}
        self.last_kline: dict[str, Kline] = {}

    async def _on_connected(self, ws: websockets.ClientConnection) -> None:
        # Nada a enviar: os streams já estão na URL (doc oficial, "Combined
        # streams are accessed at /stream?streams=...").
        return

    async def _handle_message(self, event: FeedEvent) -> None:
        item = parse_binance_event(event.parsed, event.ts_mono_ns, event.ts_wall_ns)
        if item is None:
            return
        if self.assets and item.asset not in self.assets:
            return
        if isinstance(item, BookTicker):
            self.last_book_ticker[item.asset] = item
            if self.on_book_ticker is not None:
                self.on_book_ticker(item)
        else:
            self.last_kline[item.asset] = item
            if self.on_kline is not None:
                self.on_kline(item)
