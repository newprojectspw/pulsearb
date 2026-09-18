"""Parse dos eventos do RTDS — fixtures estruturais do protocolo verificado."""

import json
from pathlib import Path

import pytest

from pulsearb.feeds.rtds import (
    RtdsFeed,
    e18_to_float,
    erro_do_servidor,
    normalize_symbol,
    parse_rtds_event,
)

#: Duas respostas REAIS do RTDS, verbatim, gravadas em 2026-09-09 entre 02:19 e
#: 03:19 UTC (M2_72H). Não são o que imaginamos que o servidor manda: são o que
#: ele mandou. Ficheiro partilhado com `test_m27_saude_do_feed.py`.
_RECUSAS = json.loads(
    (Path(__file__).parent / "fixtures" / "rtds_recusas_reais.json").read_text(encoding="utf-8")
)
RECUSA_500 = _RECUSAS["recusa_500"]
RECUSA_400 = _RECUSAS["recusa_400"]


class TestErroDoServidor:
    def test_le_as_duas_recusas_reais_e_sabe_que_sao_de_assinatura(self):
        for bruto, status in ((RECUSA_500, 500), (RECUSA_400, 400)):
            erro = erro_do_servidor(bruto)
            assert erro is not None
            assert erro.status_code == status
            assert erro.e_de_assinatura
            assert "AddSubscriptions" in erro.mensagem

    def test_erro_que_nao_e_de_assinatura_e_lido_mas_nao_e_recusa(self):
        erro = erro_do_servidor({"body": {"message": "rate limited"}, "statusCode": 429})
        assert erro is not None and erro.status_code == 429
        assert not erro.e_de_assinatura

    def test_evento_de_preco_e_lixo_nao_sao_erro(self, rtds_events):
        assert erro_do_servidor(rtds_events["twap_sixty_btc"]) is None
        assert erro_do_servidor({"_b64": ""}) is None
        assert erro_do_servidor({"statusCode": "500", "body": {}}) is None
        assert erro_do_servidor({"statusCode": True, "body": {}}) is None
        assert erro_do_servidor(None) is None

    def test_a_recusa_nao_vira_price_tick(self):
        assert parse_rtds_event(RECUSA_400, 1, 2) is None


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


def test_twap_thirty_so_entra_por_opcao_e_e_vigiado_como_os_outros():
    """Auditoria §2.2: a prova directa da janela das 5m exige GRAVAR o tópico
    de 30 s. Desligado por defeito; ligado, entra no frame E na lista que o
    detector de tópico mudo vigia — assinar sem vigiar gravaria silêncio."""
    import orjson

    from pulsearb.feeds.rtds import TOPIC_TWAP_30

    sem = RtdsFeed(url="wss://x", user_agent="ua", assets=["btc"])
    assert TOPIC_TWAP_30 not in sem.topicos_assinados

    com = RtdsFeed(url="wss://x", user_agent="ua", assets=["btc"], topicos_extra=(TOPIC_TWAP_30,))
    frame = orjson.loads(com.subscribe_frame())
    topics = {s["topic"] for s in frame["subscriptions"]}
    assert topics == {"crypto_prices", "crypto_prices_twap_sixty", "crypto_prices_twap_thirty"}
    assert com.topicos_assinados == (*RtdsFeed.TOPICOS_ASSINADOS, TOPIC_TWAP_30)
    # Repetir um tópico já assinado não o duplica no frame.
    dup = RtdsFeed(url="wss://x", user_agent="ua", assets=["btc"], topicos_extra=("crypto_prices",))
    assert dup.topicos_assinados == RtdsFeed.TOPICOS_ASSINADOS

    # E o evento do tópico de 30 s vira PriceTick pelo mesmo parser (startswith twap).
    tick = parse_rtds_event(
        {"topic": TOPIC_TWAP_30, "payload": {"symbol": "btc/usd", "timestamp": 1,
                                             "full_accuracy_value": "78640000000000000000000"}},
        1, 2,
    )
    assert tick is not None and tick.topic == TOPIC_TWAP_30 and tick.asset == "btc"


# ── `bool` no campo numérico é dado malformado, não preço (auditoria §2.4) ──


@pytest.mark.parametrize("topic", ["crypto_prices_chainlink", "crypto_prices"])
@pytest.mark.parametrize("valor", [True, False])
def test_bool_no_value_NAO_vira_preco(topic, valor):
    """`true` virava 1,0 e `false` virava 0,0 — provado na auditoria de
    2026-09-17. É o preço que decide a janela; malformado recusa."""
    evento = {
        "topic": topic,
        "payload": {"symbol": "btc/usd", "value": valor, "timestamp": 1758000000000},
    }

    assert parse_rtds_event(evento, 1, 1) is None


def test_bool_no_full_accuracy_value_cai_no_value_e_tambem_recusa():
    """No TWAP, `full_accuracy_value` não-string cai para `value`; se os dois
    forem bool, continua sem preço."""
    evento = {
        "topic": "crypto_prices_twap_sixty",
        "payload": {"symbol": "btc/usd", "full_accuracy_value": True, "value": True},
    }

    assert parse_rtds_event(evento, 1, 1) is None
