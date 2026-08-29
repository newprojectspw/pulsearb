"""Diagnóstico de horizonte (VEREDITO_M2 §2d-bis).

A remediação da escala falhou como o pré-registro previu; a pergunta do M3
virou horizonte. Estes testes travam a regra de leitura REGISTRADA ANTES dos
números — o lugar onde "escolher a banda depois de ver o resultado" entraria —
e a fiação que força o preditor a operar em cada banda em vez de onde chega
primeiro.
"""

from __future__ import annotations

import importlib.util
import io
from contextlib import redirect_stdout
from pathlib import Path

import pytest
from tests.synthetic import gerar_gravacao

from pulsearb.backtest.__main__ import RecordingIndex
from pulsearb.backtest.report import curva_de_horizonte
from pulsearb.backtest.runner import (
    BANDAS_DE_HORIZONTE,
    MINIMO_DE_TRADES_POR_BANDA,
    BacktestConfig,
    BacktestRunner,
    varredura_de_horizonte,
)
from pulsearb.engine.anchor import AnchorHypothesis, compute_anchor
from pulsearb.replay.reader import RecordingReader

# `scripts/` não é pacote: carrega o resumo por caminho, como test_resumo_m2.
_RAIZ = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "resumo_m2", _RAIZ / "scripts" / "resumo_m2.py"
)
resumo_m2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(resumo_m2)


class TestARegraDeLeitura:
    """`curva_de_horizonte` é onde a §2d-bis vira código. Uma banda só tem
    edge se PnL > 0 E hit > 0,5 E n >= 40 — as três, juntas."""

    def test_as_tres_juntas_dao_edge(self):
        v = curva_de_horizonte(
            {"240-120s": {"trades": 50, "pnl_liquido_usdc": 1.5,
                          "hit_rate": 0.55, "amostra_suficiente": True}}
        )
        assert v["bandas_com_edge"] == ["240-120s"]
        assert v["alguma_banda_com_edge"] is True
        assert v["sinal_fraco"] == []
        assert "ALGUMA banda tem edge" in v["nota"]

    def test_pnl_positivo_e_hit_bom_mas_amostra_pequena_e_sinal_fraco(self):
        # O caso que o piso de 40 existe para barrar: hit acima de 0,5 com
        # amostra pequena demais para distinguir de cara-ou-coroa.
        v = curva_de_horizonte(
            {"<30s": {"trades": 12, "pnl_liquido_usdc": 0.4,
                      "hit_rate": 0.66, "amostra_suficiente": False}}
        )
        assert v["bandas_com_edge"] == []
        assert v["sinal_fraco"] == ["<30s"]
        assert v["alguma_banda_com_edge"] is False

    def test_hit_exatamente_meio_a_meio_nao_e_edge(self):
        # 0,5 não é > 0,5: o gatilho não bate o acaso.
        v = curva_de_horizonte(
            {"120-60s": {"trades": 200, "pnl_liquido_usdc": 3.0,
                         "hit_rate": 0.5, "amostra_suficiente": True}}
        )
        assert v["bandas_com_edge"] == []
        assert v["alguma_banda_com_edge"] is False

    def test_pnl_zero_ou_negativo_nao_e_edge_mesmo_com_hit_alto(self):
        # Hit alto e PnL não-positivo = ganha barato e perde caro. Não é edge.
        v = curva_de_horizonte(
            {"240-120s": {"trades": 500, "pnl_liquido_usdc": -10.0,
                          "hit_rate": 0.72, "amostra_suficiente": True},
             ">240s": {"trades": 500, "pnl_liquido_usdc": 0.0,
                       "hit_rate": 0.8, "amostra_suficiente": True}}
        )
        assert v["bandas_com_edge"] == []
        assert v["alguma_banda_com_edge"] is False

    def test_nenhuma_banda_diz_troca_o_preditor(self):
        v = curva_de_horizonte(
            {"240-120s": {"trades": 695, "pnl_liquido_usdc": -62.49,
                          "hit_rate": 0.44, "amostra_suficiente": True}}
        )
        assert v["alguma_banda_com_edge"] is False
        assert "NENHUMA banda tem edge" in v["nota"]
        assert "troca o preditor" in v["nota"]

    def test_banda_sem_amostra_com_campos_nulos_nao_quebra(self):
        v = curva_de_horizonte(
            {"<30s": {"trades": 0, "pnl_liquido_usdc": 0.0,
                      "pnl_por_share": None, "hit_rate": None,
                      "amostra_suficiente": False}}
        )
        assert v["alguma_banda_com_edge"] is False
        assert v["sinal_fraco"] == []

    def test_o_piso_de_amostra_cobre_o_ic_do_hit(self):
        # 1,96*sqrt(0,25/40) ≈ 0,155: com 40 trades o IC de 95% do hit em
        # p=0,5 não cruza 0,5 por acaso. O número não pode escorregar sem
        # este teste gritar.
        import math
        meia_largura = 1.96 * math.sqrt(0.25 / MINIMO_DE_TRADES_POR_BANDA)
        assert meia_largura == pytest.approx(0.155, abs=5e-3)


@pytest.fixture
def janelas_indexadas(tmp_path):
    diretorio = tmp_path / "rec"
    diretorio.mkdir()
    gerar_gravacao(diretorio / "rec.jsonl.gz", n_janelas=8)
    index = RecordingIndex(RecordingReader(diretorio))
    index.build()
    janelas = [j for j in index.janelas() if j.resolveu_up is not None]
    for janela in janelas:
        janela.ancora = compute_anchor(
            AnchorHypothesis.ULTIMO_ANTES, index.streams["btc"], janela.open_ts_ns
        )
    return janelas, index.streams


class TestAVarredura:
    def test_devolve_as_cinco_bandas_do_bucket_tempo(self, janelas_indexadas):
        janelas, streams = janelas_indexadas
        v = varredura_de_horizonte(janelas, streams)
        assert set(v) == {nome for nome, _, _ in BANDAS_DE_HORIZONTE}
        for dados in v.values():
            assert set(dados) >= {
                "trades", "pnl_liquido_usdc", "hit_rate", "amostra_suficiente"
            }

    def test_cada_banda_so_conta_trade_dentro_dela(self, janelas_indexadas):
        # A prova de que a restrição morde: uma rodada com max=30s só produz
        # trades no bucket <30s. É o que separa "medir horizonte" de "medir
        # ordem de chegada".
        janelas, streams = janelas_indexadas
        report = BacktestRunner(
            BacktestConfig(tempo_restante_max_s=30.0)
        ).run(janelas, streams)
        assert all(t.bucket_tempo == "<30s" for t in report.trades)


class TestNoRelatorio:
    def test_o_relatorio_traz_a_curva_com_veredito(self, tmp_path, monkeypatch):
        import json

        from pulsearb.backtest.__main__ import main

        diretorio = tmp_path / "rec"
        diretorio.mkdir()
        gerar_gravacao(diretorio / "rec.jsonl.gz", n_janelas=8)
        monkeypatch.setenv("PULSEARB_BACKTEST_OUTPUT_ROOT", str(tmp_path))
        assert main([str(diretorio), "--json", "r.json"]) == 0

        curva = json.loads((tmp_path / "r.json").read_text())["curva_de_horizonte"]
        assert set(curva["por_banda"]) == {
            nome for nome, _, _ in BANDAS_DE_HORIZONTE
        }
        assert isinstance(curva["alguma_banda_com_edge"], bool)
        assert "§2d-bis" in curva["nota"]


class TestNoResumo:
    def test_o_resumo_le_o_veredito_pronto(self):
        # O resumo não recomputa a regra: lê `alguma_banda_com_edge` do
        # relatório. Se o JSON diz "nenhuma", o texto diz "troca o preditor".
        relatorio = {
            "curva_de_horizonte": {
                "por_banda": {
                    "240-120s": {"trades": 695, "pnl_liquido_usdc": -62.49,
                                 "pnl_por_share": -0.018, "hit_rate": 0.44,
                                 "amostra_suficiente": True},
                },
                "bandas_com_edge": [],
                "alguma_banda_com_edge": False,
                "sinal_fraco": [],
            }
        }
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            resumo_m2._imprimir_horizonte(relatorio)
        saida = buffer.getvalue()
        assert "NENHUMA banda tem edge" in saida
        assert "troca o preditor" in saida
        assert "240-120s" in saida
