"""Feed da Binance: URL de stream, parsers e o candle horário.

Protocolo conferido na doc oficial (binance-spot-api-docs/web-socket-streams.md).
O servidor WS é local (loopback) — nenhum teste toca a Binance de verdade.
"""

from __future__ import annotations

import json

import pytest
from tests.test_feeds_ws import _wait_for, server  # noqa: F401  (reusa o WS fake)

from pulsearb.feeds.binance_ws import (
    BINANCE_STREAM_BASE,
    BinanceWsFeed,
    BookTicker,
    Kline,
    build_stream_url,
    parse_binance_event,
    symbol_for,
)


# ------------------------------------------------------------------ URL/stream
def test_symbol_for():
    assert symbol_for("btc") == "btcusdt"
    assert symbol_for("ETH") == "ethusdt"


def test_url_do_stream_combinado():
    url = build_stream_url(["btc", "eth"])
    assert url.startswith(f"{BINANCE_STREAM_BASE}/stream?streams=")
    streams = url.split("streams=", 1)[1].split("/")
    # bookTicker E kline_1h de cada ativo — o candle é o preço-verdade da 1h
    assert streams == [
        "btcusdt@bookTicker",
        "btcusdt@kline_1h",
        "ethusdt@bookTicker",
        "ethusdt@kline_1h",
    ]


def test_simbolos_sempre_minusculos():
    # A doc oficial é explícita: "All symbols for streams are lowercase".
    url = build_stream_url(["BTC"])
    assert "BTC" not in url
    assert "btcusdt@bookTicker" in url


# -------------------------------------------------------------------- parsers
BOOK_TICKER = {
    "u": 400900217,
    "s": "BTCUSDT",
    "b": "118431.55",
    "B": "0.5",
    "a": "118432.10",
    "A": "1.2",
}

KLINE_ABERTO = {
    "e": "kline",
    "E": 1786891562000,
    "s": "BTCUSDT",
    "k": {
        "t": 1786888800000,
        "T": 1786892399999,
        "s": "BTCUSDT",
        "i": "1h",
        "f": 100,
        "L": 200,
        "o": "118000.00",
        "c": "118432.17",
        "h": "118500.00",
        "l": "117900.00",
        "v": "1000",
        "n": 100,
        "x": False,
        "q": "1.0",
        "V": "500",
        "Q": "0.5",
        "B": "0",
    },
}


def test_parse_book_ticker():
    tick = parse_binance_event(BOOK_TICKER, 111, 222)
    assert isinstance(tick, BookTicker)
    assert tick.asset == "btc"
    assert tick.bid == pytest.approx(118431.55)
    assert tick.ask == pytest.approx(118432.10)
    assert tick.mid == pytest.approx((118431.55 + 118432.10) / 2)
    assert tick.update_id == 400900217
    assert (tick.ts_mono_ns, tick.ts_wall_ns) == (111, 222)


def test_parse_kline_aberto():
    candle = parse_binance_event(KLINE_ABERTO, 1, 2)
    assert isinstance(candle, Kline)
    assert candle.asset == "btc"
    assert candle.interval == "1h"
    assert candle.open == pytest.approx(118000.0)
    assert candle.close == pytest.approx(118432.17)
    assert candle.is_closed is False
    # close > open → projeção Up (ainda não é resultado: is_closed=False)
    assert candle.resolves_up is True


def test_regra_da_janela_horaria_empate_resolve_up():
    """close == open resolve Up (API_NOTES 12.4/12.2b)."""
    empate = json.loads(json.dumps(KLINE_ABERTO))
    empate["k"]["c"] = empate["k"]["o"]
    empate["k"]["x"] = True
    candle = parse_binance_event(empate, 1, 2)
    assert candle.is_closed is True
    assert candle.resolves_up is True


def test_regra_da_janela_horaria_close_abaixo_resolve_down():
    baixa = json.loads(json.dumps(KLINE_ABERTO))
    baixa["k"]["c"] = "117500.00"
    candle = parse_binance_event(baixa, 1, 2)
    assert candle.resolves_up is False


def test_parse_stream_combinado_desembrulha():
    """O stream combinado embrulha em {"stream":..., "data":...}."""
    envelope = {"stream": "btcusdt@bookTicker", "data": BOOK_TICKER}
    tick = parse_binance_event(envelope, 1, 2)
    assert isinstance(tick, BookTicker)
    assert tick.asset == "btc"


def test_parse_lixo_vira_none():
    assert parse_binance_event(None, 1, 2) is None
    assert parse_binance_event({}, 1, 2) is None
    assert parse_binance_event({"e": "kline"}, 1, 2) is None           # sem "k"
    assert parse_binance_event({"e": "kline", "k": {}}, 1, 2) is None  # "k" vazio
    assert parse_binance_event({"s": "BTCUSDT", "b": "x", "a": "1"}, 1, 2) is None
    # evento de outro tipo (a doc lista vários) simplesmente não interessa
    assert parse_binance_event({"e": "trade", "s": "BTCUSDT", "p": "1"}, 1, 2) is None


# ----------------------------------------------------------------------- feed
async def test_feed_recebe_book_e_kline(server):  # noqa: F811
    server.to_send = [
        json.dumps({"stream": "btcusdt@bookTicker", "data": BOOK_TICKER}),
        json.dumps({"stream": "btcusdt@kline_1h", "data": KLINE_ABERTO}),
    ]
    books: list[BookTicker] = []
    klines: list[Kline] = []
    feed = BinanceWsFeed(
        assets=["btc"],
        user_agent="ua",
        base_url=server.url,
        on_book_ticker=books.append,
        on_kline=klines.append,
    )
    await feed.start()
    try:
        await _wait_for(lambda: books and klines)
        assert books[0].asset == "btc"
        assert klines[0].interval == "1h"
        assert feed.last_book_ticker["btc"].bid == pytest.approx(118431.55)
        assert feed.last_kline["btc"].open == pytest.approx(118000.0)
        assert not feed.stale
    finally:
        await feed.stop()


async def test_feed_nao_manda_frame_de_subscribe(server):  # noqa: F811
    """Os streams vão na URL — conectar já é assinar."""
    feed = BinanceWsFeed(assets=["btc"], user_agent="ua", base_url=server.url)
    await feed.start()
    try:
        await _wait_for(lambda: feed.connected)
        import asyncio

        await asyncio.sleep(0.05)
        assert server.received == []
    finally:
        await feed.stop()


async def test_feed_filtra_ativo_de_fora(server):  # noqa: F811
    server.to_send = [json.dumps({"stream": "x", "data": BOOK_TICKER})]  # btc
    books: list[BookTicker] = []
    feed = BinanceWsFeed(
        assets=["eth"], user_agent="ua", base_url=server.url, on_book_ticker=books.append
    )
    await feed.start()
    try:
        await _wait_for(lambda: feed.message_count >= 1)
        assert books == []
    finally:
        await feed.stop()
