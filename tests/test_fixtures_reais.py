"""Os parsers contra registros REAIS da gravação (auditoria 2026-09-17 §2.9).

`tests/fixtures/reais/` é produzido por `scripts/recortar_fixtures.py` sobre
a gravação do Mac e commitado. Enquanto não existir, estes testes SALTAM com
o motivo escrito — saltar é honesto; passar sobre fixture sintética seria
"teste que encoda a suposição" (CLAUDE.md).

O que cada teste pergunta é a única coisa que uma fixture real responde e
uma sintética não: **o parser lê o que o servidor mandou, ou lê zero?** Os
dois defeitos silenciosos deste projeto (`price_change` §6.1b,
`market_resolved` §12.13) eram exatamente "o parser lia zero e o relatório
saía normal".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pulsearb.feeds.poly_ws import (
    EVENT_BOOK,
    EVENT_LAST_TRADE,
    EVENT_MARKET_RESOLVED,
    EVENT_PRICE_CHANGE,
    eventos_do_payload,
    forma_do_book,
    forma_do_price_change,
    iter_mudancas,
    resolucao_do_evento,
)
from pulsearb.feeds.rtds import TOPIC_TWAP_60, parse_rtds_event
from pulsearb.numeros import numero

PASTA = Path(__file__).resolve().parent / "fixtures" / "reais"
MANIFESTO = PASTA / "MANIFESTO.json"

pytestmark = pytest.mark.skipif(
    not MANIFESTO.exists(),
    reason=(
        "fixture real ainda não commitada — rodar scripts/recortar_fixtures.py "
        "no Mac e commitar tests/fixtures/reais/ (auditoria §2.9, ledger 8)"
    ),
)


def _registros(tipo: str) -> list[dict]:
    manifesto = json.loads(MANIFESTO.read_text(encoding="utf-8"))
    entrada = manifesto["tipos"].get(tipo)
    if entrada is None:
        pytest.skip(f"o recorte não tem registros de {tipo!r} — ver MANIFESTO.json")
    linhas = (PASTA / entrada["arquivo"]).read_text(encoding="utf-8").splitlines()
    assert len(linhas) == entrada["registros"], "manifesto e arquivo discordam"
    return [json.loads(x) for x in linhas]


def _eventos(tipo: str, event_type: str) -> list[dict]:
    return [
        ev
        for r in _registros(tipo)
        for ev in eventos_do_payload(r["payload"])
        if ev.get("event_type") == event_type
    ]


class TestPolyWs:
    def test_todo_registro_poly_ws_rende_ao_menos_um_evento(self):
        for tipo in ("poly_ws/price_change", "poly_ws/book", "poly_ws/last_trade_price"):
            for r in _registros(tipo):
                assert eventos_do_payload(r["payload"]), f"{tipo}: payload sem evento legível"

    def test_price_change_real_tem_forma_conhecida_e_topo_legivel(self):
        eventos = _eventos("poly_ws/price_change", EVENT_PRICE_CHANGE)
        assert eventos
        formas = {forma_do_price_change(ev) for ev in eventos}
        assert "__sem_lista__" not in formas, f"price_change sem lista de mudanças: {formas}"
        mudancas = [m for ev in eventos for m in iter_mudancas(ev)]
        assert mudancas, "iter_mudancas leu ZERO mudanças de price_change reais (§6.1b)"
        com_topo = [m for m in mudancas if m.best_bid is not None and m.best_ask is not None]
        assert com_topo, "nenhuma mudança trouxe best_bid/best_ask — o topo autoritativo sumiu"

    def test_book_real_tem_forma_conhecida(self):
        eventos = _eventos("poly_ws/book", EVENT_BOOK)
        assert eventos
        for ev in eventos:
            forma = forma_do_book(ev)
            assert not forma.startswith("__"), f"book com forma desconhecida: {forma}"

    def test_last_trade_price_real_tem_preco_e_side(self):
        eventos = _eventos("poly_ws/last_trade_price", EVENT_LAST_TRADE)
        assert eventos
        for ev in eventos:
            assert numero(ev.get("price")) is not None, ev
            assert str(ev.get("side", "")).upper() in ("BUY", "SELL"), ev

    def test_market_resolved_real_e_lido_pelo_parser(self):
        eventos = _eventos("poly_ws/market_resolved", EVENT_MARKET_RESOLVED)
        assert eventos
        lidos = [resolucao_do_evento(ev) for ev in eventos]
        assert all(r is not None for r in lidos), (
            "market_resolved real que o parser não lê (§12.13)"
        )


class TestRtds:
    def test_twap_sixty_real_vira_price_tick(self):
        registros = _registros(f"rtds/{TOPIC_TWAP_60}")
        ticks = [
            parse_rtds_event(r["payload"], r["ts_mono_ns"], r["ts_wall_ns"]) for r in registros
        ]
        assert all(t is not None for t in ticks), "twap_sixty real que parse_rtds_event não lê"
        assert all(t.price > 0 for t in ticks if t is not None)
