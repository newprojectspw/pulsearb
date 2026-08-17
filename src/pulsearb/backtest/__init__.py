"""Backtest pessimista por construção: taxa, spread, slippage e latência."""

from pulsearb.backtest.book import FillResult, OrderBook, simulate_taker_buy
from pulsearb.backtest.report import BacktestReport, CalibrationBucket

__all__ = [
    "BacktestReport",
    "CalibrationBucket",
    "FillResult",
    "OrderBook",
    "simulate_taker_buy",
]
