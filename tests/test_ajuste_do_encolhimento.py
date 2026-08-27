"""O passo 1 do protocolo 2d: somar curvas e aplicar a REGRA, não o gosto.

O ajuste roda dia a dia (três dias não cabem numa passada), então as curvas
precisam somar. E a escolha do fator é a regra pré-registrada em código —
escolher a olho, depois de ver os números, é o que o pré-registro proíbe.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "ajuste_do_encolhimento", RAIZ / "scripts" / "ajuste_do_encolhimento.py"
)
ajuste = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ajuste)


def _relatorio(curvas):
    return {"backtest": {"calibracao": {
        balde: {"curva_de_confiabilidade": curva} for balde, curva in curvas.items()
    }}}


class TestSomaDasCurvas:
    def test_n_soma_e_medias_sao_ponderadas(self):
        a = _relatorio({"240-120s": {"0.9": {"n": 100, "previsto": 0.9, "realizado": 0.6}}})
        b = _relatorio({"240-120s": {"0.9": {"n": 300, "previsto": 0.9, "realizado": 0.8}}})

        curvas = ajuste.curvas_somadas([a, b])
        celula = curvas["240-120s"]["0.9"]

        assert celula["n"] == 400
        # Media ponderada: (100*0,6 + 300*0,8)/400 = 0,75 — nao 0,7, que
        # seria a media simples e daria peso igual a dias desiguais.
        assert celula["realizado"] == pytest.approx(0.75)

    def test_faixa_que_so_existe_num_relatorio_sobrevive(self):
        a = _relatorio({"<30s": {"0.1": {"n": 10, "previsto": 0.1, "realizado": 0.2}}})
        b = _relatorio({"<30s": {"0.9": {"n": 10, "previsto": 0.9, "realizado": 0.8}}})
        assert set(ajuste.curvas_somadas([a, b])["<30s"]) == {"0.1", "0.9"}

    def test_faixa_sem_amostra_nao_entra(self):
        a = _relatorio({"<30s": {"0.5": {"n": 0, "previsto": 0.5, "realizado": 0.5}}})
        assert ajuste.curvas_somadas([a]) == {"<30s": {}}


class TestFaixaOperada:
    @pytest.mark.parametrize(
        ("balde", "dentro"),
        [
            ("<30s", True),
            ("60-30s", True),
            ("120-60s", True),
            ("240-120s", True),
            (">240s", False),
        ],
    )
    def test_so_ate_240s_entra(self, balde, dentro):
        curvas = {balde: {"0.9": {"n": 10, "previsto": 0.9, "realizado": 0.6}}}
        assert (balde in ajuste.baldes_da_faixa_operada(curvas)) is dentro

    def test_o_teto_do_balde_e_o_primeiro_numero(self):
        # `240-120s` cobre ate 240s: e o teto que decide se opera na faixa.
        assert ajuste._teto_do_balde("240-120s") == pytest.approx(240.0)
        assert ajuste._teto_do_balde(">240s") == float("inf")
        assert ajuste._teto_do_balde("<30s") == pytest.approx(30.0)


class TestARegraDecide:
    """O fator vem do maior `n`, não do melhor ECE."""

    def _com_dois_baldes(self):
        # `120-60s` tem n MENOR e responderia melhor ao encolhimento;
        # `240-120s` tem n MAIOR e e quem decide, pela regra.
        return _relatorio({
            "240-120s": {
                "0.05": {"n": 5000, "previsto": 0.02, "realizado": 0.30},
                "0.50": {"n": 200, "previsto": 0.50, "realizado": 0.50},
                "0.95": {"n": 5000, "previsto": 0.98, "realizado": 0.70},
            },
            "120-60s": {
                "0.05": {"n": 50, "previsto": 0.02, "realizado": 0.12},
                "0.50": {"n": 10, "previsto": 0.50, "realizado": 0.50},
                "0.95": {"n": 50, "previsto": 0.98, "realizado": 0.88},
            },
        })

    def test_escolhe_o_balde_de_maior_n(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("PULSEARB_BACKTEST_OUTPUT_ROOT", str(tmp_path))
        (tmp_path / "f.json").write_text(json.dumps(self._com_dois_baldes()))

        assert ajuste.main(["f.json"]) == 0
        saida = capsys.readouterr().out

        assert "FATOR DA REGRA" in saida
        assert "balde 240-120s" in saida
        # O outro balde aparece, mas rotulado como o que NAO decide.
        assert "SENSIBILIDADE" in saida
        assert "NAO conta como remediacao" in saida

    def test_o_comando_do_passo_2_sai_com_o_fator_da_regra(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setenv("PULSEARB_BACKTEST_OUTPUT_ROOT", str(tmp_path))
        (tmp_path / "f.json").write_text(json.dumps(self._com_dois_baldes()))
        ajuste.main(["f.json"])
        saida = capsys.readouterr().out

        # O fator impresso na regra e o mesmo do comando — se divergirem,
        # o operador roda um numero e reporta outro.
        linha = next(
            linha for linha in saida.splitlines() if "FATOR DA REGRA" in linha
        )
        fator = linha.split(":")[1].strip()
        assert f"--fator-de-encolhimento {fator}" in saida
        assert "--encolhido" in saida

    def test_sem_balde_na_faixa_operada_nao_inventa_fator(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setenv("PULSEARB_BACKTEST_OUTPUT_ROOT", str(tmp_path))
        (tmp_path / "f.json").write_text(json.dumps(_relatorio({
            ">240s": {"0.9": {"n": 900, "previsto": 0.9, "realizado": 0.6}}
        })))

        assert ajuste.main(["f.json"]) == 1
        assert "NENHUM balde" in capsys.readouterr().out

    def test_sem_argumento_morre_com_2(self):
        assert ajuste.main([]) == 2
