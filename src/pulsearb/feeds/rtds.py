"""Cliente do RTDS (Real-Time Data Service) — wss://ws-live-data.polymarket.com.

Protocolo verificado em docs/API_NOTES.md seções 6.2 e 12.3:
- subscribe: {"action": "subscribe", "subscriptions": [{"topic": t, "type": "update"}]}
- tópicos usados: crypto_prices (spot Binance repassado) e
  crypto_prices_twap_sixty (TWAP Chainlink 60s — a fonte de resolução real de
  TODAS as durações observadas ao vivo em 2026-08-16)
- símbolos: minúsculos com barra no Chainlink/TWAP ("btc/usd"); o spot binance
  usa o par colado ("btcusdt")
- TWAP: payload tem full_accuracy_value (string inteira escalada 1e18,
  preferida) e window_s
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import orjson
import websockets

from pulsearb.feeds.base import FeedEvent, OnEvent, ReconnectingFeed

TOPIC_BINANCE = "crypto_prices"
TOPIC_TWAP_60 = "crypto_prices_twap_sixty"

_E18 = 10**18


@dataclass(frozen=True, slots=True)
class PriceTick:
    """Um preço normalizado, pronto para o engine/recorder."""

    topic: str           # TOPIC_BINANCE | TOPIC_TWAP_60 | outro
    asset: str           # "btc", "eth", ... (normalizado)
    price: float         # preço em USD
    src_timestamp_ms: int  # timestamp do payload (relógio do servidor)
    ts_mono_ns: int      # chegada local, monotônico
    ts_wall_ns: int      # chegada local, parede (registro)


def normalize_symbol(symbol: str) -> str:
    """'btc/usd' → 'btc'; 'BTCUSDT' → 'btc'; 'eth/usd' → 'eth'."""
    lowered = symbol.lower()
    if "/" in lowered:
        return lowered.split("/", 1)[0]
    for suffix in ("usdt", "usdc", "usd"):
        if lowered.endswith(suffix) and len(lowered) > len(suffix):
            return lowered[: -len(suffix)]
    return lowered


def e18_to_float(value: str) -> float:
    """Converte a string inteira escalada 1e18 do Chainlink para float.

    A divisão inteira antes do float preserva a precisão que importa
    (float64 tem ~15-16 dígitos; 118432.17 cabe com folga).
    """
    scaled = int(value)
    whole, fraction = divmod(abs(scaled), _E18)
    result = float(whole) + fraction / _E18
    return -result if scaled < 0 else result


def parse_rtds_event(parsed: Any, ts_mono_ns: int, ts_wall_ns: int) -> PriceTick | None:
    """Extrai um PriceTick de um evento do RTDS. None = não é evento de preço.

    Tolerante por design: tópico desconhecido ou payload sem os campos
    esperados devolve None — o recorder grava o cru de qualquer forma, e o
    engine simplesmente não consome o que não entende.
    """
    if not isinstance(parsed, dict):
        return None
    topic = parsed.get("topic")
    payload = parsed.get("payload")
    if not isinstance(topic, str) or not isinstance(payload, dict):
        return None
    symbol = payload.get("symbol")
    if not isinstance(symbol, str):
        return None

    price: float | None = None
    if topic == TOPIC_TWAP_60 or topic.startswith("crypto_prices_twap"):
        # full_accuracy_value (1e18) é a fonte preferida — igual ao SDK oficial.
        fav = payload.get("full_accuracy_value")
        if isinstance(fav, str):
            try:
                price = e18_to_float(fav)
            except ValueError:
                price = None
        if price is None:
            price = _as_float(payload.get("value"))
    elif topic in (TOPIC_BINANCE, "crypto_prices_chainlink"):
        price = _as_float(payload.get("value"))
    else:
        return None

    if price is None:
        return None
    src_ts = payload.get("timestamp")
    return PriceTick(
        topic=topic,
        asset=normalize_symbol(symbol),
        price=price,
        src_timestamp_ms=int(src_ts) if isinstance(src_ts, (int, float)) else 0,
        ts_mono_ns=ts_mono_ns,
        ts_wall_ns=ts_wall_ns,
    )


def _as_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


class RtdsFeed(ReconnectingFeed):
    """Feed do RTDS: assina binance + twap60 para os ativos configurados."""

    def __init__(
        self,
        *,
        url: str,
        user_agent: str,
        assets: list[str],
        on_tick: Any = None,  # Callable[[PriceTick], None] | None
        on_event: OnEvent | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(name="rtds", url=url, user_agent=user_agent, on_event=on_event, **kwargs)
        self.assets = [a.lower() for a in assets]
        self.on_tick = on_tick
        self.last_tick_by_key: dict[tuple[str, str], PriceTick] = {}

    def subscribe_frame(self) -> bytes:
        # Sem filtro de symbols: o RTDS aceita filtrar, mas receber todos e
        # filtrar localmente é mais robusto a grafias de símbolo divergentes
        # (custo: alguns KB/s). Os ativos configurados são o filtro local.
        return orjson.dumps(
            {
                "action": "subscribe",
                "subscriptions": [
                    {"topic": TOPIC_BINANCE, "type": "update"},
                    {"topic": TOPIC_TWAP_60, "type": "update"},
                ],
            }
        )

    async def _on_connected(self, ws: websockets.ClientConnection) -> None:
        await ws.send(self.subscribe_frame())

    async def _handle_message(self, event: FeedEvent) -> None:
        tick = parse_rtds_event(event.parsed, event.ts_mono_ns, event.ts_wall_ns)
        if tick is None:
            return
        if self.assets and tick.asset not in self.assets:
            return
        self.last_tick_by_key[(tick.topic, tick.asset)] = tick
        if self.on_tick is not None:
            self.on_tick(tick)
