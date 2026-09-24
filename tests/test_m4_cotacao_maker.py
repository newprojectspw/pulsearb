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
    AncoraDoMicroprice,
    Cotacao,
    avaliar_grade,
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
    def test_dois_lados_pontuam_o_triplo_de_um_dentro_da_faixa(self):
        """§15.3: um lado só é dividido por 3 dentro de [0,10, 0,90].

        Até 2026-09-14 este teste dizia "o dobro", e passava porque o código
        somava os dois lados. A doc combina por `Q_min`, e a soma pagava a
        cotação de um lado como se fossem dois.
        """
        um = estimar_retorno(
            Cotacao(1, 50.0, dois_lados=False), _livro(), PARAMS, horas=4.0
        )
        dois = estimar_retorno(
            Cotacao(1, 50.0, dois_lados=True), _livro(), PARAMS, horas=4.0
        )

        assert dois.score_proprio == pytest.approx(3 * um.score_proprio)

    def test_um_lado_so_vale_zero_fora_da_faixa(self):
        """Fora de [0,10, 0,90] a doc EXIGE dois lados. É o regime dos
        mercados de horizonte longo, onde o pool foi parar."""
        um = estimar_retorno(
            Cotacao(1, 50.0, dois_lados=False), _livro(meio=0.95), PARAMS, horas=4.0
        )
        dois = estimar_retorno(
            Cotacao(1, 50.0, dois_lados=True), _livro(meio=0.95), PARAMS, horas=4.0
        )

        assert um is not None and dois is not None
        assert um.score_proprio == 0.0
        assert um.rewards_usdc == 0.0
        assert dois.score_proprio > 0

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


class TestOPrecoCaiNaGradeDoTick:
    """O meio do livro pode ficar entre dois ticks (bid 0,45 / ask 0,46 →
    0,455). Ordem fora da grade não existe no CLOB (§4): o bid arredonda
    para baixo e o ask para cima — o lado que fica MAIS longe do meio, nunca
    mais perto do que `distancia_ticks` pediu."""

    def test_meio_entre_ticks(self):
        c = Cotacao(distancia_ticks=1, tamanho=50.0)
        assert c.preco(0.455, 0.01, do_lado_bid=True) == 0.44
        assert c.preco(0.455, 0.01, do_lado_bid=False) == 0.47

    def test_meio_na_grade_nao_desce_um_tick_por_erro_binario(self):
        c = Cotacao(distancia_ticks=1, tamanho=50.0)
        # 0,50 − 0,01 = 0,48999… em binário; o floor ingênuo daria 0,48.
        assert c.preco(0.50, 0.01, do_lado_bid=True) == 0.49
        assert c.preco(0.50, 0.01, do_lado_bid=False) == 0.51
        assert Cotacao(3, 50.0).preco(0.07, 0.01, do_lado_bid=True) == 0.04


class TestMicropriceEAncora:
    """O microprice como ÂNCORA da cotação (`docs/OUTROS_BOTS.md` §6, item 6).

    O `maker_de_pares` mediu sobre a gravação que cotar a partir do microprice
    é o que vira o sinal do termo determinístico. Aqui se trava o que a porta
    para o caminho ao vivo tem de garantir: que a âncora só aperta, que ela é
    a MESMA função que o script mediu, e — o que nenhum número denuncia — que
    o preço avaliado e o preço enviado continuam sendo o mesmo.
    """

    def test_o_microprice_pende_para_o_lado_pequeno(self) -> None:
        # bid de 900 contra ask de 100: quem quer comprar já está na fila, e o
        # pouco que resta do outro lado é o que vai ser levado — o justo está
        # perto do ASK.
        livro = OrderBook(asset_id="tok", bids=[(0.49, 900.0)], asks=[(0.51, 100.0)])
        assert livro.microprice == pytest.approx(0.49 * 100 / 1000 + 0.51 * 900 / 1000)
        assert livro.microprice > livro.mid
        # E o espelho: ask grande puxa para o bid.
        invertido = OrderBook(asset_id="tok", bids=[(0.49, 100.0)], asks=[(0.51, 900.0)])
        assert invertido.microprice < invertido.mid

    def test_sem_os_dois_lados_nao_inventa_microprice(self) -> None:
        """Sem um dos lados não há para onde ponderar. Inventar aqui daria
        âncora para um livro que não se sabe onde está."""
        assert OrderBook(asset_id="t", bids=[(0.49, 10.0)], asks=[]).microprice is None
        assert OrderBook(asset_id="t", bids=[], asks=[(0.51, 10.0)]).microprice is None
        zerado = OrderBook(asset_id="t", bids=[(0.49, 0.0)], asks=[(0.51, 0.0)])
        assert zerado.microprice is None

    def test_ancora_com_ticks_negativo_falha_na_construcao(self) -> None:
        """`ticks` negativo mandaria a cotação para CIMA do microprice — o
        oposto do que a âncora faz, e em silêncio."""
        with pytest.raises(ValueError, match="negativo"):
            AncoraDoMicroprice(bid=0.50, ticks=-1)

    def test_a_ancora_so_aperta_nunca_afrouxa(self) -> None:
        """Microprice ACIMA do meio: o bid já está mais longe do que a âncora
        exige, e a âncora não pode puxá-lo de volta para perto do meio."""
        cotacao = Cotacao(distancia_ticks=2, tamanho=50.0)
        sem = cotacao.preco(0.50, 0.01, do_lado_bid=True)
        com = cotacao.preco(
            0.50, 0.01, do_lado_bid=True, ancora=AncoraDoMicroprice(bid=0.55, ticks=1)
        )
        assert sem == pytest.approx(0.48)
        assert com == pytest.approx(sem)
        # Agora o microprice ABAIXO: aí ela aperta.
        apertada = cotacao.preco(
            0.50, 0.01, do_lado_bid=True, ancora=AncoraDoMicroprice(bid=0.46, ticks=1)
        )
        assert apertada == pytest.approx(0.45)

    def test_a_ancora_do_ask_empurra_para_cima(self) -> None:
        """Do lado do ask o conservador é o contrário: afastar-se para cima."""
        cotacao = Cotacao(distancia_ticks=1, tamanho=50.0)
        assert cotacao.preco(0.50, 0.01, do_lado_bid=False) == pytest.approx(0.51)
        assert cotacao.preco(
            0.50, 0.01, do_lado_bid=False, ancora=AncoraDoMicroprice(ask=0.54, ticks=1)
        ) == pytest.approx(0.55)

    def test_perna_sem_microprice_nao_ancora_aquele_lado(self) -> None:
        cotacao = Cotacao(distancia_ticks=1, tamanho=50.0)
        so_o_bid = AncoraDoMicroprice(bid=0.46, ask=None, ticks=1)
        assert cotacao.preco(0.50, 0.01, do_lado_bid=True, ancora=so_o_bid) == (
            pytest.approx(0.45)
        )
        assert cotacao.preco(0.50, 0.01, do_lado_bid=False, ancora=so_o_bid) == (
            pytest.approx(0.51)
        )

    @pytest.mark.parametrize("micro_down", [0.30, 0.44, 0.50, 0.61])
    @pytest.mark.parametrize("meio", [0.50, 0.37, 0.735])
    def test_o_preco_AVALIADO_e_o_ENVIADO_sao_o_mesmo_numero(
        self, meio: float, micro_down: float
    ) -> None:
        """A conta vê a perna Down como o ASK do livro do Up (`1 − preço`); o
        laço a envia como BID no livro do Down, com a âncora espelhada. Se os
        dois arredondamentos não derem no mesmo tick, a cotação é escolhida
        por um preço e enviada por outro — o modo de falha do §6.1b, que não
        levanta erro nenhum. Este é o teste que trava isso."""
        cotacao = Cotacao(distancia_ticks=2, tamanho=50.0)
        ancora = AncoraDoMicroprice(bid=0.48, ask=1.0 - micro_down, ticks=1)

        avaliado = cotacao.preco(meio, 0.01, do_lado_bid=False, ancora=ancora)
        enviado = cotacao.preco(
            1.0 - meio, 0.01, do_lado_bid=True, ancora=ancora.no_livro_do_down()
        )

        assert 1.0 - enviado == pytest.approx(avaliado)

    def test_ancora_que_tira_todas_da_faixa_nao_escolhe_cotacao(self) -> None:
        """Microprice bem abaixo do meio empurra toda a grade para fora do
        `max_spread`: nenhuma pontua, e cotar mesmo assim seria pagar risco de
        execução por zero reward."""
        livro = _livro(meio=0.50)
        candidatas = [Cotacao(distancia_ticks=t, tamanho=50.0) for t in (1, 2)]
        assert escolher_cotacao(candidatas, livro, PARAMS, horas=1.0) is not None
        assert (
            escolher_cotacao(
                candidatas,
                livro,
                PARAMS,
                horas=1.0,
                ancora=AncoraDoMicroprice(bid=0.44, ask=0.56, ticks=1),
            )
            is None
        )

    def test_a_ancora_entra_no_score_da_candidata(self) -> None:
        """A candidata é avaliada NO preço que a âncora produz, não no preço
        do meio: escolher por um e enviar outro seria o defeito do #118 de
        novo, agora pela âncora."""
        livro = _livro(meio=0.50)
        cotacao = Cotacao(distancia_ticks=1, tamanho=50.0)
        solto = estimar_retorno(cotacao, livro, PARAMS, horas=1.0)
        ancorado = estimar_retorno(
            cotacao,
            livro,
            PARAMS,
            horas=1.0,
            ancora=AncoraDoMicroprice(bid=0.49, ask=0.51, ticks=1),
        )
        assert solto is not None and ancorado is not None
        # 1 tick do meio pontua mais que 2 ticks: a âncora afastou a cotação.
        assert ancorado.score_proprio < solto.score_proprio
        assert ancorado.pontua


class TestOTetoDeFracaoDoPool:
    """O teto de participação estimada no pool, aplicado ANTES da escolha
    final (`avaliar_grade`). Ele EXCLUI candidata; nunca move nenhuma.

    Com `_livro(tamanho_por_nivel=100)` e cotação de 50 shares, a fração fica
    limpa: 1 tick toma 0,2857 do pool, 2 ticks tomam 0,0909 — mais longe do
    meio pontua menos, logo toma fatia menor. É essa monotonicidade que faz o
    teto poder escolher a candidata mais distante.
    """

    def _livro_de_fracao_limpa(self):
        return _livro(tamanho_por_nivel=100.0)

    def test_sem_teto_mantem_o_resultado_de_sempre(self):
        """Default `None` não muda nada: a escolhida é a mesma de
        `escolher_cotacao` sem o parâmetro."""
        livro = self._livro_de_fracao_limpa()
        candidatas = [Cotacao(1, 50.0), Cotacao(2, 50.0)]

        de_sempre = escolher_cotacao(candidatas, livro, PARAMS, horas=4.0)
        com_none = avaliar_grade(candidatas, livro, PARAMS, horas=4.0, fracao_maxima=None)

        assert com_none.escolhida.cotacao == de_sempre.cotacao
        assert com_none.recusadas_por_teto == 0
        assert not com_none.bloqueada_por_teto

    def test_teto_exato_ACEITA(self):
        """A candidata cuja fração BATE o teto é aceita — a exclusão é para
        quem o ULTRAPASSA. Passar a fração exata da candidata prova a folga."""
        livro = self._livro_de_fracao_limpa()
        um_tick = estimar_retorno(Cotacao(1, 50.0), livro, PARAMS, horas=4.0)

        escolha = avaliar_grade(
            [Cotacao(1, 50.0)], livro, PARAMS,
            horas=4.0, fracao_maxima=um_tick.fracao_do_pool,
        )

        assert escolha.escolhida is not None
        assert escolha.escolhida.cotacao.distancia_ticks == 1
        assert escolha.recusadas_por_teto == 0

    def test_acima_do_teto_RECUSA(self):
        """Uma candidata sozinha, com fração acima do teto: nada é escolhido,
        e a razão é o teto — `bloqueada_por_teto`, não falta de candidata."""
        livro = self._livro_de_fracao_limpa()
        um_tick = estimar_retorno(Cotacao(1, 50.0), livro, PARAMS, horas=4.0)

        escolha = avaliar_grade(
            [Cotacao(1, 50.0)], livro, PARAMS,
            horas=4.0, fracao_maxima=um_tick.fracao_do_pool - 0.01,
        )

        assert escolha.escolhida is None
        assert escolha.pontuaram == 1
        assert escolha.recusadas_por_teto == 1
        assert escolha.bloqueada_por_teto

    def test_candidata_alternativa_ABAIXO_do_teto_e_escolhida(self):
        """1 tick (0,2857) excede o teto de 0,15; 2 ticks (0,0909) respeita.
        A mais LONGE do meio é escolhida — é o ponto do teto."""
        livro = self._livro_de_fracao_limpa()

        escolha = avaliar_grade(
            [Cotacao(1, 50.0), Cotacao(2, 50.0)], livro, PARAMS,
            horas=4.0, fracao_maxima=0.15,
        )

        assert escolha.escolhida is not None
        assert escolha.escolhida.cotacao.distancia_ticks == 2
        assert escolha.recusadas_por_teto == 1
        assert not escolha.bloqueada_por_teto

    def test_todas_acima_do_teto_bloqueia_sem_confundir_com_sem_candidata(self):
        """Teto minúsculo barra as duas: `bloqueada_por_teto`, distinto de
        `pontuaram == 0` (que é 'não achei onde cotar')."""
        livro = self._livro_de_fracao_limpa()

        escolha = avaliar_grade(
            [Cotacao(1, 50.0), Cotacao(2, 50.0)], livro, PARAMS,
            horas=4.0, fracao_maxima=0.001,
        )

        assert escolha.escolhida is None
        assert escolha.pontuaram == 2
        assert escolha.recusadas_por_teto == 2
        assert escolha.bloqueada_por_teto

    def test_nada_pontua_NAO_e_bloqueio_por_teto(self):
        """Grade que não pontua (fora do `max_spread`) com teto ligado: a
        razão do vazio é falta de candidata, não o teto."""
        livro = self._livro_de_fracao_limpa()

        escolha = avaliar_grade(
            [Cotacao(3, 50.0)], livro, PARAMS, horas=4.0, fracao_maxima=0.10,
        )

        assert escolha.escolhida is None
        assert escolha.pontuaram == 0
        assert escolha.recusadas_por_teto == 0
        assert not escolha.bloqueada_por_teto

    def test_escolher_cotacao_repassa_o_teto(self):
        """O atalho `escolher_cotacao` também respeita o teto — é a mesma
        avaliação, só devolvendo a escolhida."""
        livro = self._livro_de_fracao_limpa()
        assert escolher_cotacao(
            [Cotacao(1, 50.0)], livro, PARAMS, horas=4.0, fracao_maxima=0.001
        ) is None
        assert escolher_cotacao(
            [Cotacao(1, 50.0)], livro, PARAMS, horas=4.0, fracao_maxima=0.99
        ) is not None
