"""A fatia de execução do maker — os dois limites que a fila não observável
ainda permite medir.

Cada teste trava um jeito de a medição sair otimista: contar execução funda
como se fosse do topo, dar ao último da fila o que só o primeiro pegaria, ou
tratar "não medi" como "medi zero".
"""

from __future__ import annotations

import pytest

from pulsearb.analysis.fila import (
    conta_do_maker,
    medir_fatia_de_execucao,
)
from pulsearb.backtest.book import OrderBook


class _Timeline:
    """Devolve o mesmo book em qualquer instante — o tempo não é o alvo aqui."""

    def __init__(self, book):
        self.book = book
        self.ts = [0]

    def at(self, _ts_ns):
        return self.book


class _Janela:
    def __init__(self, book, trades, tick=0.01):
        self.books = {"tok": _Timeline(book)}
        self.trades = trades
        self.tick_size = tick


def _book(*, ask=0.51, tamanho_ask=100.0, bid=0.49, tamanho_bid=100.0):
    return OrderBook(
        asset_id="tok",
        bids=[(bid, tamanho_bid)],
        asks=[(ask, tamanho_ask)],
        ts_ns=0,
    )


class TestOsDoisLimites:
    def test_primeiro_da_fila_leva_o_que_couber_na_cotacao(self):
        """Consumo de 30 shares num nível de 100: quem está na frente leva
        os 30, limitado ao próprio tamanho."""
        janela = _Janela(_book(), [(1, 0.51, 30.0, "BUY")])

        f = medir_fatia_de_execucao([janela], nossa_cotacao_shares=50.0)

        assert f.execucoes == 1
        assert f.shares_do_fluxo == pytest.approx(30.0)
        assert f.shares_no_pior_caso == pytest.approx(30.0)

    def test_primeiro_da_fila_nao_leva_mais_que_a_propria_cotacao(self):
        janela = _Janela(_book(), [(1, 0.51, 300.0, "BUY")])

        f = medir_fatia_de_execucao([janela], nossa_cotacao_shares=50.0)

        assert f.shares_no_pior_caso == pytest.approx(50.0)

    def test_ultimo_da_fila_NAO_leva_nada_se_o_nivel_nao_esgotou(self):
        """O ponto da medição: 30 de 100 consumidos não chegam em quem está
        atrás dos 100."""
        janela = _Janela(_book(tamanho_ask=100.0), [(1, 0.51, 30.0, "BUY")])

        f = medir_fatia_de_execucao([janela], nossa_cotacao_shares=50.0)

        assert f.shares_no_melhor_caso == 0.0
        assert f.execucoes_que_esgotaram_o_nivel == 0

    def test_ultimo_da_fila_leva_o_que_passou_do_nivel(self):
        """Consumo de 130 num nível de 100: sobram 30 para quem está atrás."""
        janela = _Janela(_book(tamanho_ask=100.0), [(1, 0.51, 130.0, "BUY")])

        f = medir_fatia_de_execucao([janela], nossa_cotacao_shares=50.0)

        assert f.shares_no_melhor_caso == pytest.approx(30.0)
        assert f.execucoes_que_esgotaram_o_nivel == 1

    def test_o_melhor_caso_nunca_passa_do_pior(self):
        """Invariante da medição: estar atrás não pode render mais que estar
        na frente. Se isto quebrar, os dois limites trocaram de lugar."""
        janela = _Janela(
            _book(tamanho_ask=10.0),
            [(1, 0.51, 500.0, "BUY"), (2, 0.51, 5.0, "BUY")],
        )

        f = medir_fatia_de_execucao([janela], nossa_cotacao_shares=50.0)

        assert f.shares_no_melhor_caso <= f.shares_no_pior_caso


class TestOQueNaoEntra:
    def test_execucao_FUNDA_no_livro_nao_e_do_maker_do_topo(self):
        """Quem foi executado a 0,55 com o topo em 0,51 é outra ordem."""
        janela = _Janela(_book(ask=0.51), [(1, 0.55, 30.0, "BUY")])

        f = medir_fatia_de_execucao([janela], nossa_cotacao_shares=50.0)

        assert f.execucoes == 0
        assert f.sem_referencia == 1

    def test_sem_livro_no_instante_conta_como_SEM_REFERENCIA(self):
        """"Não medi" e "medi zero" levam a decisões opostas."""

        class _Vazia:
            def __init__(self):
                self.ts = [0]

            def at(self, _):
                return None

        janela = _Janela(_book(), [(1, 0.51, 30.0, "BUY")])
        janela.books = {"tok": _Vazia()}  # type: ignore[dict-item]

        f = medir_fatia_de_execucao([janela], nossa_cotacao_shares=50.0)

        assert f.execucoes == 0
        assert f.sem_referencia == 1

    def test_lado_SELL_olha_o_bid(self):
        """Taker vendeu → consumiu o bid. Olhar o ask ali mediria o lado
        errado do livro e descartaria a execução como funda."""
        janela = _Janela(_book(bid=0.49), [(1, 0.49, 30.0, "SELL")])

        f = medir_fatia_de_execucao([janela], nossa_cotacao_shares=50.0)

        assert f.execucoes == 1

    def test_sem_execucao_nenhuma_a_fatia_e_NULA_e_nao_zero(self):
        f = medir_fatia_de_execucao([], nossa_cotacao_shares=50.0)

        assert f.fatia_no_pior_caso is None
        assert f.fatia_no_melhor_caso is None


class TestAContaFechada:
    def _fatia(self, *, pior, melhor, fluxo=1000.0):
        from pulsearb.analysis.fila import FatiaDeExecucao

        return FatiaDeExecucao(
            execucoes=10,
            shares_do_fluxo=fluxo,
            shares_no_pior_caso=pior,
            shares_no_melhor_caso=melhor,
            execucoes_que_esgotaram_o_nivel=2,
            sem_referencia=0,
        )

    def test_markout_negativo_vira_CUSTO(self):
        """Invertê-lo transformaria adverse selection em receita — o erro
        mais caro possível nesta conta."""
        c = conta_do_maker(
            self._fatia(pior=100.0, melhor=10.0),
            rewards_usdc=10.0,
            markout_centavos_por_share=-0.1974,
        )

        assert c["pior_caso"]["custo_de_markout_usdc"] > 0
        assert c["pior_caso"]["liquido_usdc"] < c["melhor_caso"]["liquido_usdc"]

    def test_mesmo_sinal_nos_dois_casos_dispensa_a_fila(self):
        """É este o resultado que fecharia o 1.6 sem observar a fila:
        qualquer posição real cai entre os dois extremos."""
        c = conta_do_maker(
            self._fatia(pior=10.0, melhor=1.0),
            rewards_usdc=100.0,  # grande: positivo nos dois
            markout_centavos_por_share=-0.1974,
        )

        assert c["pior_caso"]["liquido_usdc"] > 0
        assert c["melhor_caso"]["liquido_usdc"] > 0
        assert c["a_fila_decide"] is False

    def test_sinais_diferentes_significam_que_a_FILA_decide(self):
        c = conta_do_maker(
            self._fatia(pior=10_000.0, melhor=1.0),
            rewards_usdc=10.0,
            markout_centavos_por_share=-0.1974,
        )

        assert c["pior_caso"]["liquido_usdc"] < 0
        assert c["melhor_caso"]["liquido_usdc"] > 0
        assert c["a_fila_decide"] is True
