"""Item 4.0 — a decisão de onde repousar a cotação maker.

Cada teste aqui trava uma forma de a decisão sair errada de um jeito que o
número final não denuncia: score inflado por ignorar o próprio tamanho no
denominador, markout entrando com sinal trocado, ou uma cotação que não
pontua sendo escolhida porque ninguém conferiu se ela pontuava.
"""

from __future__ import annotations

import pytest

from pulsearb.analysis.rewards import ParametrosDeReward
from pulsearb.backtest.book import OrderBook
from pulsearb.live.cotacao import (
    FATOR_DE_CAPTURA_PADRAO,
    MARKOUT_CENTAVOS_POR_SHARE,
    Cotacao,
    escolher_cotacao,
    estimar_retorno,
)

#: `max_spread` de 3 ¢ e tick de 1 ¢: uma cotação a 1 tick do meio fica a 1/3
#: do limite, e a 3 ticks já não pontua. É a geometria dos mercados updown
#: (API_NOTES §4: tick de 1 ¢).
PARAMS = ParametrosDeReward(
    daily_rate=100.0,
    min_size=5.0,
    max_spread=0.03,
    tick_size=0.01,
)


def _livro(*, meio=0.50, tamanho_por_nivel=100.0, niveis=3) -> OrderBook:
    """Livro simétrico em torno de `meio`, com profundidade em cada tick."""
    bids = [(round(meio - 0.01 * (i + 1), 4), tamanho_por_nivel) for i in range(niveis)]
    asks = [(round(meio + 0.01 * (i + 1), 4), tamanho_por_nivel) for i in range(niveis)]
    return OrderBook(asset_id="tok", bids=bids, asks=asks, ts_ns=0)


class TestOScoreVemDaMesmaFuncaoDoBacktest:
    def test_mais_perto_do_meio_pontua_mais(self):
        """A fórmula é decrescente em `s`, e quadrática — 1 tick vale bem
        mais que 2, não um pouco mais."""
        perto = estimar_retorno(
            Cotacao(distancia_ticks=1, tamanho=50.0), _livro(), PARAMS, horas=4.0
        )
        longe = estimar_retorno(
            Cotacao(distancia_ticks=2, tamanho=50.0), _livro(), PARAMS, horas=4.0
        )

        assert perto.score_proprio > longe.score_proprio
        # ((3-1)/3)² = 0,444 contra ((3-2)/3)² = 0,111 — quatro vezes.
        assert perto.score_proprio == pytest.approx(4 * longe.score_proprio, rel=1e-6)

    def test_alem_do_max_spread_NAO_pontua(self):
        """3 ticks com `max_spread` de 3 ¢ cai fora — score zero, não
        negativo, e a cotação não deve ser considerada."""
        r = estimar_retorno(
            Cotacao(distancia_ticks=3, tamanho=50.0), _livro(), PARAMS, horas=4.0
        )

        assert r.score_proprio == 0.0
        assert not r.pontua

    def test_abaixo_do_min_size_NAO_pontua(self):
        r = estimar_retorno(
            Cotacao(distancia_ticks=1, tamanho=4.0), _livro(), PARAMS, horas=4.0
        )

        assert not r.pontua

    def test_sem_meio_no_livro_devolve_None_e_nao_zero(self):
        """Livro sem os dois lados não tem meio. Inventar um produziria score
        para uma cotação que não se sabe onde está."""
        vazio = OrderBook(asset_id="tok", bids=[], asks=[], ts_ns=0)

        assert estimar_retorno(
            Cotacao(1, 50.0), vazio, PARAMS, horas=4.0
        ) is None


class TestODenominadorIncluiONossoProprioScore:
    """Entrar no livro aumenta o total. Ignorar isso superestima a fatia — e
    o erro cresce justamente quando a cotação é grande."""

    def test_a_fracao_nunca_passa_de_um(self):
        gigante = estimar_retorno(
            Cotacao(distancia_ticks=1, tamanho=1_000_000.0),
            _livro(),
            PARAMS,
            horas=4.0,
        )

        assert gigante.fracao_do_pool < 1.0

    def test_cotacao_grande_num_livro_com_gente_domina_mas_nao_estoura(self):
        raso = _livro(tamanho_por_nivel=10.0)  # acima do min_size: pontua
        r = estimar_retorno(Cotacao(1, 500.0), raso, PARAMS, horas=4.0)

        assert 0.9 < r.fracao_do_pool < 1.0

    def test_livro_onde_NINGUEM_atinge_o_min_size_da_fatia_cheia(self):
        """E está certo: se nenhum nível pontua, o pool inteiro é nosso.

        Não é caso de borda inventado — `min_size` de 5 shares com níveis de
        1 share é exatamente o mercado fino em que a rota maker interessaria.
        """
        ninguem_pontua = _livro(tamanho_por_nivel=1.0)
        r = estimar_retorno(Cotacao(1, 500.0), ninguem_pontua, PARAMS, horas=4.0)

        assert r.score_total_do_livro == pytest.approx(r.score_proprio)
        assert r.fracao_do_pool == 1.0

    def test_a_fatia_cai_quando_o_livro_tem_mais_gente(self):
        magro = estimar_retorno(
            Cotacao(1, 50.0), _livro(tamanho_por_nivel=10.0), PARAMS, horas=4.0
        )
        cheio = estimar_retorno(
            Cotacao(1, 50.0), _livro(tamanho_por_nivel=5000.0), PARAMS, horas=4.0
        )

        assert magro.fracao_do_pool > cheio.fracao_do_pool


class TestOMarkoutEntraComOSinalQueTem:
    def test_markout_negativo_vira_CUSTO_e_nao_receita(self):
        """`MARKOUT_CENTAVOS_POR_SHARE` é −0,1974: perdemos contra quem nos
        executa. Se isso entrasse somando, a rota pareceria melhor quanto
        pior o markout — que é o erro mais caro possível aqui."""
        r = estimar_retorno(Cotacao(1, 50.0), _livro(), PARAMS, horas=4.0)

        assert MARKOUT_CENTAVOS_POR_SHARE < 0
        assert r.custo_de_markout_usdc > 0
        assert r.liquido_usdc < r.rewards_usdc

    def test_markout_zero_deixa_o_liquido_igual_aos_rewards(self):
        r = estimar_retorno(
            Cotacao(1, 50.0), _livro(), PARAMS, horas=4.0, markout_centavos=0.0
        )

        assert r.custo_de_markout_usdc == 0.0
        assert r.liquido_usdc == pytest.approx(r.rewards_usdc)

    def test_as_parcelas_ficam_SEPARADAS_no_resultado(self):
        """Uma é medida (markout, 1.7) e a outra é estimativa com hipótese de
        fila. Somar antes de publicar esconderia essa diferença."""
        r = estimar_retorno(Cotacao(1, 50.0), _livro(), PARAMS, horas=4.0)

        assert r.rewards_usdc > 0
        assert r.custo_de_markout_usdc > 0
        assert r.fator_de_captura == FATOR_DE_CAPTURA_PADRAO


class TestAEscolha:
    def test_escolhe_a_de_maior_liquido(self):
        candidatas = [Cotacao(1, 50.0), Cotacao(2, 50.0)]

        melhor = escolher_cotacao(candidatas, _livro(), PARAMS, horas=4.0)

        assert melhor.cotacao.distancia_ticks == 1

    def test_ignora_as_que_nao_pontuam(self):
        """3 ticks está fora do `max_spread`; 4 shares está abaixo do mínimo."""
        candidatas = [Cotacao(3, 50.0), Cotacao(1, 4.0), Cotacao(2, 50.0)]

        melhor = escolher_cotacao(candidatas, _livro(), PARAMS, horas=4.0)

        assert melhor.cotacao == Cotacao(2, 50.0)

    def test_nenhuma_pontua_devolve_None(self):
        melhor = escolher_cotacao(
            [Cotacao(3, 50.0), Cotacao(1, 1.0)], _livro(), PARAMS, horas=4.0
        )

        assert melhor is None

    def test_empate_resolve_pela_MAIS_LONGE_do_meio(self):
        """Mesmo líquido com menos exposição a execução adversa é a mesma
        aposta com menos risco — e o markout é medido enquanto a fila é
        hipótese."""
        # markout zero achata o líquido: as duas distâncias rendem o mesmo
        # por unidade de score, então o desempate é o que decide.
        candidatas = [Cotacao(1, 50.0), Cotacao(1, 50.0, dois_lados=True)]
        melhor = escolher_cotacao(
            candidatas, _livro(), PARAMS, horas=4.0, markout_centavos=0.0
        )

        assert melhor is not None

    def test_lista_vazia_nao_estoura(self):
        assert escolher_cotacao([], _livro(), PARAMS, horas=4.0) is None


class TestUmLadoContraDoisLados:
    def test_dois_lados_pontuam_o_dobro_de_um(self):
        um = estimar_retorno(
            Cotacao(1, 50.0, dois_lados=False), _livro(), PARAMS, horas=4.0
        )
        dois = estimar_retorno(
            Cotacao(1, 50.0, dois_lados=True), _livro(), PARAMS, horas=4.0
        )

        assert dois.score_proprio == pytest.approx(2 * um.score_proprio)

    def test_dois_lados_tambem_dobram_a_execucao_esperada(self):
        """O score dobra, mas a exposição a markout também. Publicar só o
        primeiro faria dois lados parecer sempre melhor."""
        um = estimar_retorno(
            Cotacao(1, 50.0, dois_lados=False), _livro(), PARAMS, horas=4.0
        )
        dois = estimar_retorno(
            Cotacao(1, 50.0, dois_lados=True), _livro(), PARAMS, horas=4.0
        )

        assert dois.custo_de_markout_usdc > um.custo_de_markout_usdc


class TestOHorizonteEscala:
    def test_o_dobro_de_horas_da_o_dobro_de_rewards(self):
        quatro = estimar_retorno(Cotacao(1, 50.0), _livro(), PARAMS, horas=4.0)
        oito = estimar_retorno(Cotacao(1, 50.0), _livro(), PARAMS, horas=8.0)

        assert oito.rewards_usdc == pytest.approx(2 * quatro.rewards_usdc)

    def test_o_markout_NAO_escala_com_o_horizonte(self):
        """O custo de markout vem do tamanho exposto, não do tempo. Se ele
        escalasse junto, a conta de 24 h ficaria pessimista por construção."""
        quatro = estimar_retorno(Cotacao(1, 50.0), _livro(), PARAMS, horas=4.0)
        oito = estimar_retorno(Cotacao(1, 50.0), _livro(), PARAMS, horas=8.0)

        assert oito.custo_de_markout_usdc == pytest.approx(
            quatro.custo_de_markout_usdc
        )
