"""`scripts/verificar_side.py`: o `side` do `last_trade_price` é o lado do taker?

Auditoria 2026-09-17 §2.1. O script não infere nada do livro reconstruído: usa
o `best_bid`/`best_ask` que o próprio servidor manda em cada `price_change`,
e só classifica prints nas pontas do spread. Estes testes prendem a
classificação, os três vereditos e o "não sei" — com gravação SINTÉTICA, o
que prova o leitor, não o servidor; a prova do servidor é a M2_72H no Mac.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]


def _carregar():
    spec = importlib.util.spec_from_file_location(
        "verificar_side", RAIZ / "scripts" / "verificar_side.py"
    )
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    sys.modules["verificar_side"] = m
    spec.loader.exec_module(m)
    return m


vs = _carregar()

TOK = "tok-a"


def _delta(ts, bid, ask):
    return {
        "ts_mono_ns": ts, "ts_wall_ns": ts, "fonte": "poly_ws",
        "payload": {"event_type": "price_change", "price_changes": [
            {"asset_id": TOK, "price": "0.50", "size": "1", "side": "BUY",
             "best_bid": str(bid), "best_ask": str(ask)}]},
    }


def _print(ts, preco, side):
    return {
        "ts_mono_ns": ts, "ts_wall_ns": ts, "fonte": "poly_ws",
        "payload": {"event_type": "last_trade_price", "asset_id": TOK,
                    "price": str(preco), "side": side, "size": "5", "timestamp": "1"},
    }


def _gravar(tmp_path, registros):
    pasta = tmp_path / "grav"
    pasta.mkdir()
    (pasta / "pulsearb-20260917-1200.jsonl").write_text(
        "\n".join(json.dumps(r) for r in registros) + "\n", encoding="utf-8"
    )
    return pasta


def _reader(pasta):
    from pulsearb.replay.reader import RecordingReader
    return RecordingReader(pasta)


class TestClassificacao:
    def test_no_ask_ou_acima_e_agressor_comprando(self):
        assert vs.classificar(0.52, 0.50, 0.52) == vs.NO_ASK
        assert vs.classificar(0.53, 0.50, 0.52) == vs.NO_ASK

    def test_no_bid_ou_abaixo_e_agressor_vendendo(self):
        assert vs.classificar(0.50, 0.50, 0.52) == vs.NO_BID
        assert vs.classificar(0.49, 0.50, 0.52) == vs.NO_BID

    def test_dentro_do_spread_NAO_classifica(self):
        assert vs.classificar(0.51, 0.50, 0.52) == vs.DENTRO


class TestVeredito:
    def test_taker_quando_quase_todos_concordam(self):
        assert vs.veredito(95, 5) == "TAKER"

    def test_maker_quando_quase_nenhum_concorda(self):
        assert vs.veredito(5, 95) == "MAKER"

    def test_meio_e_INDETERMINADO_e_nao_um_dos_dois(self):
        assert vs.veredito(60, 40) == "INDETERMINADO"

    def test_poucos_prints_e_SEM_AMOSTRA_mesmo_que_todos_concordem(self):
        assert vs.veredito(10, 0) == "SEM_AMOSTRA"

    def test_todo_veredito_declarado_e_alcancavel(self):
        alcancados = {
            vs.veredito(95, 5), vs.veredito(5, 95), vs.veredito(60, 40), vs.veredito(1, 0)
        }
        assert alcancados == set(vs.VEREDITOS)


class TestSobreGravacao:
    def test_gravacao_em_que_side_e_o_taker_da_TAKER(self, tmp_path):
        regs = [_delta(1_000, 0.50, 0.52)]
        # 40 prints: 20 no ask marcados BUY, 20 no bid marcados SELL — a
        # convenção do código.
        for i in range(20):
            regs.append(_print(2_000 + i, 0.52, "BUY"))
            regs.append(_print(3_000 + i, 0.50, "SELL"))
        rel = vs.verificar(_reader(_gravar(tmp_path, regs)))

        assert rel["veredito"] == "TAKER"
        assert rel["classificaveis"] == 40
        assert rel["discorda_de_taker"] == 0

    def test_gravacao_em_que_side_e_o_MAKER_inverte(self, tmp_path):
        regs = [_delta(1_000, 0.50, 0.52)]
        for i in range(20):
            regs.append(_print(2_000 + i, 0.52, "SELL"))  # no ask, marcado SELL
            regs.append(_print(3_000 + i, 0.50, "BUY"))
        rel = vs.verificar(_reader(_gravar(tmp_path, regs)))

        assert rel["veredito"] == "MAKER"
        assert rel["concorda_com_taker"] == 0
        assert len(rel["exemplos_discordantes"]) == 5

    def test_print_dentro_do_spread_e_sem_topo_NAO_entram_na_conta(self, tmp_path):
        regs = [
            _print(500, 0.52, "BUY"),          # antes de qualquer topo
            _delta(1_000, 0.50, 0.52),
            _print(2_000, 0.51, "BUY"),        # dentro do spread
        ]
        rel = vs.verificar(_reader(_gravar(tmp_path, regs)))

        assert rel["posicoes"][vs.SEM_TOPO] == 1
        assert rel["posicoes"][vs.DENTRO] == 1
        assert rel["classificaveis"] == 0
        assert rel["veredito"] == "SEM_AMOSTRA"

    def test_topo_velho_demais_nao_classifica(self, tmp_path):
        regs = [_delta(1_000, 0.50, 0.52), _print(1_000 + int(10e9), 0.52, "BUY")]
        rel = vs.verificar(_reader(_gravar(tmp_path, regs)), tolerancia_topo_s=5.0)

        assert rel["posicoes"][vs.TOPO_VELHO] == 1
        assert rel["classificaveis"] == 0

    def test_main_recusa_json_absoluto_e_grava_json_relativo(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("PULSEARB_RELATORIOS_INPUT_ROOT", raising=False)
        monkeypatch.delenv("PULSEARB_BACKTEST_OUTPUT_ROOT", raising=False)
        _gravar(tmp_path, [_delta(1_000, 0.50, 0.52)])

        # A gravação pode ser absoluta (mora fora da raiz de propósito);
        # o relatório de saída não pode.
        assert vs.main(["--recordings", str(tmp_path / "grav"), "--json", "/tmp/s.json"]) == 2
        assert vs.main(["--recordings", "nao-existe"]) == 2
        assert vs.main(["--recordings", "grav", "--json", "side.json"]) == 0
        assert json.loads((tmp_path / "side.json").read_text())["veredito"] == "SEM_AMOSTRA"
