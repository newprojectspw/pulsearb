"""M2 — a correção de escala da calibração, do jeito que ela pode ser usada.

O erro do preditor cresce com a confiança (medido: −0,0105 a +0,1554 no dia
24). A correção é `p' = 0,5 + fator·(p − 0,5)`, e estes testes travam as
regras que a impedem de virar botão de ajuste de PnL.
"""

from __future__ import annotations

import pytest
from tests.synthetic import gerar_gravacao

from pulsearb.backtest.__main__ import RecordingIndex
from pulsearb.backtest.runner import BacktestConfig, BacktestRunner
from pulsearb.engine.anchor import AnchorHypothesis, compute_anchor
from pulsearb.engine.decisao import BASE_DO_ENCOLHIMENTO, encolher_para_a_base
from pulsearb.replay.reader import RecordingReader


class TestAFuncao:
    def test_fator_um_e_identidade(self):
        for prob in (0.0, 0.31, 0.5, 0.97):
            assert encolher_para_a_base(prob, 1.0) == pytest.approx(prob)

    def test_encolhe_em_direcao_a_meio_a_meio(self):
        # O caso real: o modelo cospe 0,9994 e acerta 0,887. Com fator 0,77
        # a previsao vai para ~0,885 — onde a realidade estava.
        assert encolher_para_a_base(0.9994, 0.77) == pytest.approx(0.8845, abs=1e-3)
        assert encolher_para_a_base(0.0006, 0.77) == pytest.approx(0.1155, abs=1e-3)

    def test_a_base_e_meio_e_nao_a_taxa_medida(self):
        # A taxa realizada do proprio periodo so se conhece DEPOIS dele:
        # usa-la na decisao seria olhar o futuro.
        assert BASE_DO_ENCOLHIMENTO == pytest.approx(0.5)

    @pytest.mark.parametrize("fator", [0.0, -0.5, 1.01, 2.0])
    def test_fator_fora_de_zero_um_e_erro(self, fator):
        # Acima de 1 INFLARIA a confianca de um preditor ja superconfiante;
        # zero apagaria o preditor. Nenhum dos dois e correcao de escala.
        with pytest.raises(ValueError, match="fator de encolhimento"):
            encolher_para_a_base(0.7, fator)

    def test_simetria_up_down(self):
        # O edge do lado Down e 1 - p'. Encolher p preserva a simetria:
        # nenhum lado ganha vantagem pela transformacao.
        prob = 0.83
        encolhido = encolher_para_a_base(prob, 0.6)
        assert 1.0 - encolhido == pytest.approx(
            encolher_para_a_base(1.0 - prob, 0.6)
        )


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


class TestNoRunner:
    """O encolhimento entra ANTES de tudo — calibração inclusive."""

    def test_a_calibracao_reportada_e_a_do_preditor_encolhido(
        self, janelas_indexadas
    ):
        janelas, streams = janelas_indexadas
        cru = BacktestRunner(BacktestConfig()).run(janelas, streams).to_dict()
        encolhido = (
            BacktestRunner(BacktestConfig(fator_de_encolhimento=0.5))
            .run(janelas, streams)
            .to_dict()
        )

        for balde, dados in encolhido["calibracao"].items():
            previsto_cru = cru["calibracao"][balde]["prob_media_prevista"]
            previsto_enc = dados["prob_media_prevista"]
            # A media prevista do encolhido e a do cru puxada para 0,5 —
            # sinal de que a calibracao mediu o preditor que operou, nao o
            # original. (Igualdade exata nao vale: as faixas reagrupam.)
            assert abs(previsto_enc - 0.5) <= abs(previsto_cru - 0.5) + 1e-9

    def test_o_threshold_le_a_probabilidade_encolhida(self, janelas_indexadas):
        """Um fator agressivo derruba edges que o cru enxergava.

        E o inverso do teste de threshold: aqui o sinal encolhe, o limiar
        fica parado, e trades somem. Se o numero de trades nao puder cair,
        o encolhimento nao esta no caminho do gatilho.
        """
        janelas, streams = janelas_indexadas
        cru = BacktestRunner(BacktestConfig()).run(janelas, streams)
        quase_constante = BacktestRunner(
            BacktestConfig(fator_de_encolhimento=0.01)
        ).run(janelas, streams)

        # Com p' ~ 0,5 para tudo, o edge |p' - preco| raramente passa de
        # 0,02: o gatilho tem de operar menos (ou nada).
        assert len(quase_constante.trades) <= len(cru.trades)

    def test_none_reproduz_o_cru_byte_a_byte(self, janelas_indexadas):
        """`None` e o default e significa DESLIGADO — o caminho nem roda.

        Nao e `fator=1.0` de proposito: `0,5 + 1,0*(p - 0,5)` nao devolve
        `p` bit a bit para todo float (a subtracao arredonda nos extremos), e
        um "desligado" que passa pela formula mudaria resultado de criterio
        pre-registrado por ruido de arredondamento. Se este teste quebrar, o
        ciclo mudou o cru sem dizer.
        """
        janelas, streams = janelas_indexadas
        a = BacktestRunner(BacktestConfig()).run(janelas, streams).to_dict()
        b = (
            BacktestRunner(BacktestConfig(fator_de_encolhimento=None))
            .run(janelas, streams)
            .to_dict()
        )
        assert a == b


class TestNoCLI:
    def test_o_flag_produz_a_comparacao_lado_a_lado(
        self, tmp_path, monkeypatch
    ):
        """A variante sai AO LADO do resultado, nunca no lugar dele.

        O `backtest` principal alimenta os critérios pré-registrados e tem
        de continuar cru; a resposta sobre o encolhimento mora no bloco
        `encolhimento.comparacao`, com a ressalva de validade impressa.
        """
        from pulsearb.backtest.__main__ import main

        diretorio = tmp_path / "rec"
        diretorio.mkdir()
        gerar_gravacao(diretorio / "rec.jsonl.gz", n_janelas=8)
        monkeypatch.setenv("PULSEARB_BACKTEST_OUTPUT_ROOT", str(tmp_path))

        assert main(
            [str(diretorio), "--json", "r.json", "--fator-de-encolhimento", "0.7"]
        ) == 0

        import json

        relatorio = json.loads((tmp_path / "r.json").read_text())
        bloco = relatorio["encolhimento"]
        assert bloco["fator"] == pytest.approx(0.7)
        assert set(bloco["comparacao"]) == {"sem_encolher", "encolhido"}
        assert "in-sample" in bloco["nota"]

    def test_sem_o_flag_o_bloco_e_nulo(self, tmp_path, monkeypatch):
        from pulsearb.backtest.__main__ import main

        diretorio = tmp_path / "rec"
        diretorio.mkdir()
        gerar_gravacao(diretorio / "rec.jsonl.gz", n_janelas=8)
        monkeypatch.setenv("PULSEARB_BACKTEST_OUTPUT_ROOT", str(tmp_path))
        assert main([str(diretorio), "--json", "r.json"]) == 0

        import json

        relatorio = json.loads((tmp_path / "r.json").read_text())
        assert relatorio["encolhimento"] is None
