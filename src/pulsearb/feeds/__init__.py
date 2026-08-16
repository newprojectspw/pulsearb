"""Clientes de feed em tempo real (hot path de leitura).

Falam WebSocket direto (websockets + orjson), sem o SDK oficial — decisão
registrada em docs/API_NOTES.md seção 1.4: os protocolos são simples e o
overhead de validação pydantic por evento não cabe no caminho tick→decisão.
"""

from pulsearb.feeds.base import FeedEvent, ReconnectingFeed
from pulsearb.feeds.binance_ws import BinanceWsFeed, BookTicker, Kline
from pulsearb.feeds.poly_ws import PolyMarketWsFeed
from pulsearb.feeds.rtds import RtdsFeed

__all__ = [
    "BinanceWsFeed",
    "BookTicker",
    "FeedEvent",
    "Kline",
    "PolyMarketWsFeed",
    "ReconnectingFeed",
    "RtdsFeed",
]
