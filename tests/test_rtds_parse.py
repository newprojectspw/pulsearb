"""Parse dos eventos do RTDS — fixtures estruturais do protocolo verificado."""

import pytest

from pulsearb.feeds.rtds import (
    RtdsFeed,
    e18_to_float,
    normalize_symbol,
    parse_rtds_event,
)


def test_e18_exato():
    # O valor real da fixture: 118432.17 em 1e18.
    assert e18_to_float("118432170000000000000000") == pytest.approx(118432.17)
    assert e18_to_float("1000000000000000000") == 1.0
    assert e18_to_float("0") == 0.0
    assert e18_to_float("-2500000000000000000") == -2.5
    # fração pura
    assert e18_to_float("500000000000000000") == 0.5


def test_e18_lixo_explode():
    with pytest.raises(ValueError):
        e18_to_float("não-é-número")


def test_normalize_symbol():
    assert normalize_symbol("btc/usd") == "btc"
    assert normalize_symbol("ETH/USD") == "eth"
    assert normalize_symbol("btcusdt") == "btc"
    assert normalize_symbol("BTCUSDT") == "btc"
    assert normalize_symbol("hypeusdc") == "hype"
    assert normalize_symbol("sol") == "sol"


def test_parse_twap(rtds_events):
    tick = parse_rtds_event(rtds_events["twap_sixty_btc"], 111, 222)
    assert tick is not None
    assert tick.topic == "crypto_prices_twap_sixty"
    assert tick.asset == "btc"
    # full_accuracy_value (1e18) é a fonte preferida, igual ao SDK oficial
    assert tick.price == pytest.approx(118432.17)
    assert tick.src_timestamp_ms == 1786891560123
    assert tick.ts_mono_ns == 111
    assert tick.ts_wall_ns == 222


def test_parse_chainlink_simples(rtds_events):
    tick = parse_rtds_event(rtds_events["chainlink_eth"], 1, 2)
    assert tick is not None
    assert tick.asset == "eth"
    assert tick.price == pytest.approx(4412.5)


def test_parse_binance(rtds_events):
    tick = parse_rtds_event(rtds_events["binance_btc"], 1, 2)
    assert tick is not None
    assert tick.topic == "crypto_prices"
    assert tick.asset == "btc"
    assert tick.price == pytest.approx(118431.55)


def test_topico_desconhecido_vira_none(rtds_events):
    assert parse_rtds_event(rtds_events["desconhecido"], 1, 2) is None


def test_payload_quebrado_vira_none():
    assert parse_rtds_event(None, 1, 2) is None
    assert parse_rtds_event({"topic": "crypto_prices"}, 1, 2) is None
    assert parse_rtds_event(
        {"topic": "crypto_prices", "payload": {"symbol": "btcusdt", "value": "abc"}}, 1, 2
    ) is None
    # TWAP com full_accuracy_value podre cai para value; sem value → None
    assert parse_rtds_event(
        {"topic": "crypto_prices_twap_sixty", "payload": {"symbol": "btc/usd",
                                                          "full_accuracy_value": "x"}}, 1, 2
    ) is None


def test_feed_filtra_por_ativo(rtds_events):
    feed = RtdsFeed(url="wss://x", user_agent="ua", assets=["eth"])
    ticks = []
    feed.on_tick = ticks.append

    import asyncio

    from pulsearb.feeds.base import FeedEvent

    async def run():
        for name in ("twap_sixty_btc", "chainlink_eth", "binance_btc"):
            event = FeedEvent(
                source="rtds", ts_mono_ns=1, ts_wall_ns=2, raw=b"{}",
                parsed=rtds_events[name],
            )
            await feed._handle_message(event)

    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(run())
    assert [t.asset for t in ticks] == ["eth"]
    assert ("crypto_prices_chainlink", "eth") in feed.last_tick_by_key


def test_subscribe_frame_formato():
    import orjson

    feed = RtdsFeed(url="wss://x", user_agent="ua", assets=["btc"])
    frame = orjson.loads(feed.subscribe_frame())
    assert frame["action"] == "subscribe"
    topics = {s["topic"] for s in frame["subscriptions"]}
    # binance + twap60 — os dois tópicos da estratégia (API_NOTES 12.3)
    assert topics == {"crypto_prices", "crypto_prices_twap_sixty"}
    assert all(s["type"] == "update" for s in frame["subscriptions"])
