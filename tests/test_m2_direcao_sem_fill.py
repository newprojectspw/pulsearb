"""O teste direto de direção — a pendência registrada na §2d-ter.

O `hit_rate` do relatório é medido sobre `trades`, e um trade só existe
quando a ordem PREENCHEU. Trocar a latência troca quais ordens preenchem,
então os cenários de `sensibilidade_latencia` comparam populações diferentes
— e é por isso que a inclinação da latência não separa "não há direção" de
"a execução ficou mais barata".

Cada teste aqui trava uma propriedade que faz `direcao_sem_fill` responder à
pergunta que o `hit_rate` não responde.
"""

from __future__ import annotations

import math

import pytest

from pulsearb.backtest.report import BacktestReport, SinalDirecional


def _sinal(
    slug: str = "btc-updown-5m-1",
    *,
    lado_up: bool = True,
    resolveu_up: bool = True,
    prob: float = 0.7,
    bucket: str = "240-120s",
) -> SinalDirecional:
    return SinalDirecional(
        slug=slug,
        bucket_tempo=bucket,
        lado_up=lado_up,
        prob=prob,
        resolveu_up=resolveu_up,
    )


class TestOSinalRegistraDirecaoEResultado:
    def test_acertou_compara_lado_apostado_com_resultado(self):
        assert _sinal(lado_up=True, resolveu_up=True).acertou
        assert _sinal(lado_up=False, resolveu_up=False).acertou
        assert not _sinal(lado_up=True, resolveu_up=False).acertou
        assert not _sinal(lado_up=False, resolveu_up=True).acertou

    def test_o_sinal_nao_carrega_preco_nem_book(self):
        """O tipo existe para NÃO ter esses campos.

        Se `SinalDirecional` guardasse preço pago ou profundidade, alguém
        acabaria calculando PnL a partir dele — e o PnL depende de fill, que é
        exatamente o que esta medição existe para excluir.
        """
        campos = set(SinalDirecional.__slots__)

        assert not campos & {"preco_pago", "custo_usdc", "shares", "latencia_ms"}


class TestDuasContagens:
    def test_por_janela_conta_o_primeiro_sinal_de_cada_janela(self):
        """240 instantes de uma janela não são 240 observações.

        Eles dividem âncora, preço e resultado — são a mesma observação
        repetida. Contá-los como independentes encolhe o p-valor sem que
        informação nova tenha entrado.
        """
        report = BacktestReport()
        for _ in range(50):
            report.add_sinal_direcional(_sinal("janela-a"))
        report.add_sinal_direcional(_sinal("janela-b"))

        saida = report.direcao_sem_fill()

        assert saida["por_sinal"]["n"] == 51
        assert saida["por_janela"]["n"] == 2

    def test_janelas_distintas_sai_ao_lado_do_n(self):
        """O campo que diz quanto o `n` vale, e sem o qual o p-valor engana.

        Medido na PRIMEIRA rodada real (1 h de 2026-08-24): `por_sinal` deu
        n=1.141 com p=0,000 — sobre QUATRO janelas, e apontando para o lado
        oposto do que `por_janela` dizia. Publicar o p-valor sem
        `janelas_distintas` ao lado convida a ler 1.141 observações
        independentes onde há 4.
        """
        report = BacktestReport()
        for i in range(300):
            report.add_sinal_direcional(_sinal(f"janela-{i % 4}"))

        por_sinal = report.direcao_sem_fill()["por_sinal"]

        assert por_sinal["n"] == 300
        assert por_sinal["janelas_distintas"] == 4

    def test_a_leitura_manda_comparar_n_com_janelas_distintas(self):
        assert "janelas_distintas" in BacktestReport().direcao_sem_fill()["leitura"]

    def test_o_primeiro_sinal_e_o_que_fica_e_nao_o_ultimo(self):
        """A estratégia opera o primeiro gatilho da janela; é ele o que conta.

        Guardar o último deixaria a medição escolher, dentro da janela, o
        instante que já sabe como ela terminou.
        """
        report = BacktestReport()
        report.add_sinal_direcional(_sinal("j", lado_up=True, resolveu_up=True))
        report.add_sinal_direcional(_sinal("j", lado_up=False, resolveu_up=True))

        assert report.primeiro_sinal_da_janela["j"].lado_up is True
        assert report.direcao_sem_fill()["por_janela"]["acertos"] == 1


class TestAAcuracia:
    def test_todos_certos_da_um(self):
        report = BacktestReport()
        for i in range(10):
            report.add_sinal_direcional(_sinal(f"j{i}", lado_up=True, resolveu_up=True))

        assert report.direcao_sem_fill()["por_janela"]["acuracia"] == 1.0

    def test_metade_certa_da_meio_a_meio(self):
        report = BacktestReport()
        for i in range(10):
            report.add_sinal_direcional(
                _sinal(f"j{i}", lado_up=True, resolveu_up=i % 2 == 0)
            )

        assert report.direcao_sem_fill()["por_janela"]["acuracia"] == pytest.approx(0.5)

    def test_sem_sinal_nenhum_a_acuracia_e_nula_e_nao_meio_a_meio(self):
        """Devolver 0,5 — "no meio" — inventaria medição para amostra que não
        existe. É o defeito do `cobertura_da_gravacao`, que o M2 já pagou."""
        saida = BacktestReport().direcao_sem_fill()

        assert saida["por_janela"]["n"] == 0
        assert saida["por_janela"]["acuracia"] is None
        assert saida["por_janela"]["difere_de_meio_a_meio"] is None


class TestOPValor:
    """Sem ele o número não decide nada: 0,52 em 50 e 0,52 em 50 mil são a
    mesma fração e conclusões opostas."""

    def test_amostra_pequena_com_desvio_grande_nao_e_significativa(self):
        report = BacktestReport()
        for i in range(10):
            report.add_sinal_direcional(_sinal(f"j{i}", resolveu_up=i < 7))

        saida = report.direcao_sem_fill()["por_janela"]

        assert saida["acuracia"] == pytest.approx(0.7)
        assert not saida["difere_de_meio_a_meio"]

    def test_amostra_grande_com_desvio_pequeno_e_significativa(self):
        """0,55 em 1.000 decide; 0,7 em 10 não. É essa a inversão que só o
        p-valor produz, e é a razão de ele estar no relatório."""
        report = BacktestReport()
        for i in range(1000):
            report.add_sinal_direcional(_sinal(f"j{i}", resolveu_up=i % 100 < 55))

        saida = report.direcao_sem_fill()["por_janela"]

        assert saida["acuracia"] == pytest.approx(0.55)
        assert saida["difere_de_meio_a_meio"]

    def test_moeda_honesta_nao_acusa_direcao(self):
        report = BacktestReport()
        for i in range(1000):
            report.add_sinal_direcional(_sinal(f"j{i}", resolveu_up=i % 2 == 0))

        saida = report.direcao_sem_fill()["por_janela"]

        assert saida["p_valor"] == pytest.approx(1.0)
        assert not saida["difere_de_meio_a_meio"]

    def test_direcao_ERRADA_tambem_e_significativa(self):
        """Acurácia 0,42 em amostra grande é informação, não ruído — só que
        apontando para o outro lado. Um teste unilateral esconderia isso, e é
        exatamente o número que a rodada de 24 h produziu (0,4172)."""
        report = BacktestReport()
        for i in range(1000):
            report.add_sinal_direcional(_sinal(f"j{i}", resolveu_up=i % 100 < 42))

        saida = report.direcao_sem_fill()["por_janela"]

        assert saida["acuracia"] == pytest.approx(0.42)
        assert saida["difere_de_meio_a_meio"]

    def test_o_p_valor_fica_entre_zero_e_um(self):
        for acertos, n in ((0, 10), (10, 10), (5, 10), (500, 1000), (900, 1000)):
            report = BacktestReport()
            for i in range(n):
                report.add_sinal_direcional(_sinal(f"j{i}", resolveu_up=i < acertos))
            p = report.direcao_sem_fill()["por_janela"]["p_valor"]

            assert 0.0 <= p <= 1.0, f"{acertos}/{n} deu p={p}"
            assert not math.isnan(p)


class TestPorFaixaDeConfianca:
    """A quebra que separa "erra em toda parte" de "erra onde opera"."""

    def test_a_faixa_e_a_do_lado_APOSTADO_e_nao_a_do_P_up(self):
        """Apostar Down com `P(Up)=0,1` é uma aposta de confiança 0,9.

        Agrupá-la com as de `P(Up)=0,1` que compraram Up misturaria duas
        apostas opostas na mesma linha, e a leitura sairia invertida em
        metade dos casos.
        """
        report = BacktestReport()
        report.add_sinal_direcional(
            _sinal("j1", lado_up=True, prob=0.9, resolveu_up=True)
        )
        report.add_sinal_direcional(
            _sinal("j2", lado_up=False, prob=0.1, resolveu_up=False)
        )

        faixas = report.direcao_sem_fill()["por_faixa_de_confianca"]

        assert list(faixas) == ["0.90-0.95"]
        assert faixas["0.90-0.95"]["n"] == 2
        assert faixas["0.90-0.95"]["acuracia"] == 1.0

    def test_separa_faixa_confiante_de_faixa_indecisa(self):
        report = BacktestReport()
        for i in range(10):  # confiança 0,9 — acerta sempre
            report.add_sinal_direcional(
                _sinal(f"alta{i}", prob=0.92, resolveu_up=True)
            )
        for i in range(10):  # confiança ~0,5 — cara ou coroa
            report.add_sinal_direcional(
                _sinal(f"meio{i}", prob=0.52, resolveu_up=i % 2 == 0)
            )

        faixas = report.direcao_sem_fill()["por_faixa_de_confianca"]

        assert faixas["0.90-0.95"]["acuracia"] == 1.0
        assert faixas["0.50-0.55"]["acuracia"] == pytest.approx(0.5)

    def test_o_deficit_compara_com_a_confianca_MEDIA_e_nao_com_o_meio(self):
        """O `deficit` é a diferença entre dois números grandes e próximos.

        Comparar com o meio da faixa erraria em até meia largura (0,025), e é
        justamente esse tipo de aproximação que estraga a conta. Aqui as
        apostas têm confiança 0,46 — perto da borda da faixa 0.45-0.50, cujo
        meio é 0,475.
        """
        report = BacktestReport()
        for i in range(100):
            report.add_sinal_direcional(
                _sinal(f"j{i}", prob=0.46, resolveu_up=i < 40)
            )

        celula = report.direcao_sem_fill()["por_faixa_de_confianca"]["0.45-0.50"]

        assert celula["confianca_media"] == pytest.approx(0.46)
        assert celula["acuracia"] == pytest.approx(0.40)
        assert celula["deficit"] == pytest.approx(-0.06)

    def test_deficit_positivo_quando_a_regra_escolhe_bem(self):
        """O campo tem de saber dizer o contrário também, senão não mede nada.

        `prob=0.60` exato é de propósito: ele cai em `0.60-0.65` — e caía em
        `0.55-0.60` antes do conserto de ponto flutuante em
        `faixa_de_probabilidade`, que este teste também exercita de lado.
        """
        report = BacktestReport()
        for i in range(100):
            report.add_sinal_direcional(
                _sinal(f"j{i}", prob=0.60, resolveu_up=i < 80)
            )

        celula = report.direcao_sem_fill()["por_faixa_de_confianca"]["0.60-0.65"]

        assert celula["confianca_media"] == pytest.approx(0.60)
        assert celula["deficit"] == pytest.approx(0.20)

    def test_usa_a_coorte_POR_JANELA_e_nao_todos_os_instantes(self):
        """Mesma razão do bloco principal: instantes da mesma janela são a
        mesma observação repetida, e inflariam cada faixa."""
        report = BacktestReport()
        for _ in range(30):
            report.add_sinal_direcional(_sinal("uma-janela-so", prob=0.9))

        faixas = report.direcao_sem_fill()["por_faixa_de_confianca"]

        assert faixas["0.90-0.95"]["n"] == 1


class TestAPublicacao:
    def test_o_relatorio_traz_o_bloco_com_a_leitura(self):
        report = BacktestReport()
        report.add_sinal_direcional(_sinal())

        bloco = report.to_dict()["direcao_sem_fill"]

        assert set(bloco) == {
            "por_sinal",
            "por_janela",
            "por_faixa_de_confianca",
            "leitura",
        }
        # A leitura diz qual das duas contagens decide. Sem ela, quem lê o
        # JSON cita a de `n` maior, que é a inflada.
        assert "por_janela" in bloco["leitura"]

    def test_a_leitura_separa_direcao_de_lucro(self):
        """Acurácia acima de 0,5 com custo maior que a margem continua
        perdendo dinheiro. O 1.1 e o 1.5 são critérios separados de propósito,
        e o texto do relatório não pode sugerir que este bloco os substitui."""
        leitura = BacktestReport().to_dict()["direcao_sem_fill"]["leitura"]

        assert "nao e evidencia de lucro" in leitura
