"""Os parsers contra registros REAIS da gravação (auditoria 2026-09-17 §2.9).

`tests/fixtures/reais/` é produzido por `scripts/recortar_fixtures.py` sobre
a gravação do Mac e commitado. Enquanto a PASTA não existir, estes testes
SALTAM com o motivo escrito — saltar é honesto; passar sobre fixture
sintética seria "teste que encoda a suposição" (CLAUDE.md). Mas um manifesto
commitado que NÃO tenha um tipo obrigatório FALHA, não salta: senão um
recorte curto deixaria um parser sem prova e a suíte verde (Codex, #149).

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

from pulsearb.backtest.book import OrderBook
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
from pulsearb.feeds.rtds import TOPIC_BINANCE, TOPIC_TWAP_60, erro_do_servidor, parse_rtds_event
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


def _manifesto() -> dict:
    return json.loads(MANIFESTO.read_text(encoding="utf-8"))


def _registros(tipo: str, *, obrigatorio: bool = True) -> list[dict]:
    entrada = _manifesto()["tipos"].get(tipo)
    if entrada is None:
        if not obrigatorio:
            return []
        pytest.fail(
            f"o recorte commitado não tem registros de {tipo!r} — re-extrair com um "
            "período que o contenha (ver MANIFESTO.json)"
        )
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


class TestRecorte:
    def test_todo_arquivo_do_manifesto_esta_no_repositorio(self):
        """`*.jsonl` está no .gitignore: sem a excepção, o `git add` leva só o
        manifesto e a suíte inteira cai com FileNotFoundError. Aconteceu em
        2026-09-17 (commit ce3cc37). A mensagem diz o que fazer."""
        em_falta = [
            e["arquivo"] for e in _manifesto()["tipos"].values()
            if not (PASTA / e["arquivo"]).is_file()
        ]
        assert not em_falta, (
            f"manifesto cita arquivos que não estão no repositório: {em_falta} — "
            "`git add -f tests/fixtures/reais/*.jsonl` (ver .gitignore)"
        )

    def test_nenhum_registro_do_fio_ficou_sem_classificar(self):
        """Um envelope que nenhum parser lê é o defeito, não ruído.

        A primeira rodada real (2026-09-17) falhou AQUI com 344 + 6 registros:
        PONG gravado, frames vazios e recusas de assinatura do RTDS que
        ninguém lia (API_NOTES §6.2b). Cada um ganhou categoria e teste.
        """
        sem_classe = {
            tipo: e["vistos_no_periodo"]
            for tipo, e in _manifesto()["tipos"].items()
            if tipo.endswith("/_nao_classificado")
        }
        assert not sem_classe, f"registros do fio que o parser de envelope não lê: {sem_classe}"


class TestOQueMaisChegaPeloFio:
    """As formas não-dado medidas na M2_72H. Forma EXATA, senão é achado novo."""

    def test_pong_do_clob_e_gravado_e_e_so_pong(self):
        for r in _registros("poly_ws/_pong", obrigatorio=False):
            assert r["payload"] == {"_b64": "UE9ORw=="}, r

    def test_frame_vazio_do_rtds_e_so_vazio(self):
        for r in _registros("rtds/_frame_vazio", obrigatorio=False):
            assert r["payload"] == {"_b64": ""}, r

    def test_erro_do_servidor_real_e_lido_com_codigo_e_mensagem(self):
        """A recusa que o feed passa a ler e a transformar em close (0.6)."""
        for r in _registros("rtds/_erro_do_servidor", obrigatorio=False):
            erro = erro_do_servidor(r["payload"])
            assert erro is not None, r
            assert erro.status_code >= 400
            assert erro.mensagem, "recusa sem mensagem: o feed não saberia dizer por quê"


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

    def test_book_real_tem_forma_conhecida_e_o_parser_de_producao_le_todos_os_niveis(self):
        """`forma_do_book` aceita `buys`/`sells`; `OrderBook.from_event` só lê
        `bids`/`asks`. Um livro real na forma alternativa passaria no primeiro e
        sairia VAZIO do segundo — o parser-lê-zero que este arquivo existe para
        apanhar (Codex, #149). Por isso a contagem de níveis tem de bater."""
        eventos = _eventos("poly_ws/book", EVENT_BOOK)
        assert eventos
        niveis_lidos = 0
        for ev in eventos:
            forma = forma_do_book(ev)
            assert not forma.startswith("__"), f"book com forma desconhecida: {forma}"
            book = OrderBook.from_event(ev)
            assert book is not None, f"from_event recusou um book real: {ev.get('asset_id')}"
            crus = [
                n for lado in ("bids", "asks") for n in (ev.get(lado) or [])
                if isinstance(n, dict) and numero(n.get("size")) not in (None, 0)
            ]
            assert len(book.bids) + len(book.asks) == len(crus), (
                f"from_event leu {len(book.bids) + len(book.asks)} níveis de {len(crus)} "
                f"(forma {forma}): o parser de produção não vê o que o servidor mandou"
            )
            niveis_lidos += len(crus)
        assert niveis_lidos > 0, "nenhum book real trouxe nível algum"

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
    def _ticks(self, topic: str):
        registros = _registros(f"rtds/{topic}")
        return [parse_rtds_event(r["payload"], r["ts_mono_ns"], r["ts_wall_ns"]) for r in registros]

    def test_twap_sixty_real_vira_price_tick(self):
        ticks = self._ticks(TOPIC_TWAP_60)
        assert all(t is not None for t in ticks), "twap_sixty real que parse_rtds_event não lê"
        assert all(t.price > 0 for t in ticks if t is not None)

    def test_crypto_prices_binance_real_vira_price_tick(self):
        """O ramo `TOPIC_BINANCE` do parser é outro; fixture real para ele também."""
        ticks = self._ticks(TOPIC_BINANCE)
        assert all(t is not None for t in ticks), "crypto_prices real que parse_rtds_event não lê"
        assert all(t.price > 0 for t in ticks if t is not None)
