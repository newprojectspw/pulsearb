"""4.2 — o relógio do SHADOW maker: rewards integrados no tempo, execuções
possíveis pelos prints e o markout delas.

O que os testes prendem, e por quê:

- o reward acumulado é a MESMA conta do laço (`estimar_retorno`) integrada
  no tempo — se as duas divergirem, o SHADOW mede uma estratégia que o laço
  não coloca;
- passada longa (máquina suspensa) NÃO soma o sono como tempo repousando;
- só print SELL a preço ≤ o nosso pega o bid; BUY não; acima não;
- passar abaixo do nosso preço conta a perna inteira; parar no nível conta
  o mínimo entre o print e a perna (a fila decide o resto);
- o markout fecha na passada seguinte ao horizonte, contra o NOSSO preço,
  com o sinal de quem forneceu liquidez;
- cotação que sai do livro zera o relógio dela — a próxima conta do zero;
- o laço passa os prints por `negocios_desde` e os prints chegam ao relato.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from tests.test_4_0c_laco_maker import _janela, _laco, _livro, _livro_de

from pulsearb.analysis.rewards import ParametrosDeReward
from pulsearb.backtest.book import OrderBook
from pulsearb.live.caixa_maker import (
    INTERVALO_MAXIMO_POR_PASSADA_S,
    CaixaDoMaker,
    _atraso_do_print_s,
)
from pulsearb.live.cotacao import FATOR_DE_CAPTURA_PADRAO, Cotacao, estimar_retorno
from pulsearb.live.livros import LivrosAoVivo, Negocio
from pulsearb.live.repouso import CotacaoAberta

PARAMS = ParametrosDeReward(
    daily_rate=100.0, min_size=5.0, max_spread=0.03, tick_size=0.01
)


def _aberta(preco_up=0.49, preco_down=0.49, desde=1000.0, tamanho=50.0):
    return CotacaoAberta(
        cotacao=Cotacao(distancia_ticks=1, tamanho=tamanho),
        desde_epoch=desde,
        order_id="o-up",
        order_id_down="o-down",
        preco_up=preco_up,
        preco_down=preco_down,
    )


def _negocios(*negocios: Negocio):
    """Um `negocios_desde` de mentira: todos os prints, filtrados por ts."""

    def desde(token_id, *, ts_ns):
        return [n for n in negocios if n.ts_ns > ts_ns and n.token == token_id]

    return desde


class TestRewardsIntegradosNoTempo:
    def test_soma_e_a_conta_do_laco_para_o_intervalo(self):
        """`acertar` para 15 s tem de bater com `estimar_retorno(horas=15/3600)`."""
        caixa = CaixaDoMaker()
        aberta = _aberta(desde=1000.0)
        livro = _livro(0.50)

        caixa.acertar("j", aberta, livro, PARAMS, agora_epoch=1015.0)

        esperado = estimar_retorno(aberta.cotacao, livro, PARAMS, horas=15 / 3600)
        assert esperado is not None and esperado.pontua
        assert caixa.rewards_com_captura_usdc == pytest.approx(esperado.rewards_usdc)
        assert caixa.rewards_pro_rata_usdc == pytest.approx(
            esperado.rewards_usdc / FATOR_DE_CAPTURA_PADRAO
        )
        assert caixa.segundos_repousando == pytest.approx(15.0)
        assert caixa.segundos_pontuando == pytest.approx(15.0)

    def test_comeca_a_contar_da_colocacao_e_nao_do_zero(self):
        caixa = CaixaDoMaker()
        caixa.acertar("j", _aberta(desde=1000.0), _livro(), PARAMS, agora_epoch=1010.0)
        caixa.acertar("j", _aberta(desde=1000.0), _livro(), PARAMS, agora_epoch=1025.0)

        assert caixa.segundos_repousando == pytest.approx(25.0)
        assert caixa.acertos == 2

    def test_intervalo_longo_nao_soma_o_sono_da_maquina(self):
        """O 3.14: a máquina dorme, o laço congela, o relógio de parede não."""
        caixa = CaixaDoMaker()
        caixa.acertar("j", _aberta(desde=1000.0), _livro(), PARAMS, agora_epoch=1015.0)
        caixa.acertar(
            "j", _aberta(desde=1000.0), _livro(), PARAMS, agora_epoch=1015.0 + 3 * 3600
        )

        assert caixa.segundos_repousando == pytest.approx(
            15.0 + INTERVALO_MAXIMO_POR_PASSADA_S
        )
        assert caixa.intervalos_truncados == 1

    def test_cotacao_que_nao_pontua_mais_conta_tempo_mas_nao_reward(self):
        """O meio andou 5 ticks: a cotação a 1 tick do meio ANTIGO está fora
        do spread de reward. Repousa (conta tempo), não pontua (zero reward)."""
        caixa = CaixaDoMaker()
        # `Cotacao.preco` é relativo ao meio de AGORA, então para simular a
        # cotação parada usamos um livro cujo spread fecha o reward: bids e
        # asks longe demais (max_spread 0,03 → a 4 ticks não pontua).
        livro = OrderBook(
            asset_id="tok-up", bids=[(0.40, 500.0)], asks=[(0.60, 500.0)]
        )
        est = estimar_retorno(Cotacao(4, 50.0), livro, PARAMS, horas=1.0)
        assert est is not None and not est.pontua

        caixa.acertar(
            "j",
            CotacaoAberta(Cotacao(4, 50.0), desde_epoch=1000.0),
            livro,
            PARAMS,
            agora_epoch=1015.0,
        )
        assert caixa.segundos_repousando == pytest.approx(15.0)
        assert caixa.segundos_pontuando == 0.0
        assert caixa.rewards_pro_rata_usdc == 0.0

    def test_esquecer_zera_o_relogio_do_slug(self):
        caixa = CaixaDoMaker()
        caixa.acertar("j", _aberta(desde=1000.0), _livro(), PARAMS, agora_epoch=1015.0)
        caixa.esquecer("j")
        # Nova cotação colocada em 2000: conta de 2000, não de 1015.
        caixa.acertar("j", _aberta(desde=2000.0), _livro(), PARAMS, agora_epoch=2010.0)

        assert caixa.segundos_repousando == pytest.approx(25.0)


def _n(ts_s, preco, tamanho, lado, perna="tok-up"):
    """Um print com o token junto, para o dublê de `negocios_desde` filtrar
    (o `LivrosAoVivo` de verdade guarda por token; aqui a lista é uma só)."""
    return SimpleNamespace(
        ts_ns=int(ts_s * 1e9), preco=preco, tamanho=tamanho, lado=lado, token=perna
    )


class TestExecucoesPossiveis:
    def _conferir(self, caixa, aberta, *negocios, agora_s=1030.0):
        return caixa.conferir_prints(
            "j",
            aberta,
            token_up="tok-up",
            token_down="tok-down",
            negocios_desde=_negocios(*negocios),
            agora_ns=int(agora_s * 1e9),
        )

    def test_sell_abaixo_do_nosso_bid_leva_a_perna_inteira(self):
        caixa = CaixaDoMaker()
        novas = self._conferir(caixa, _aberta(0.49, 0.49), _n(1010, 0.47, 10.0, "SELL"))

        assert novas == 1
        assert caixa.execucoes_atravessadas == 1
        assert caixa.shares_atravessadas == 50.0

    def test_sell_no_nosso_nivel_conta_o_minimo_entre_print_e_perna(self):
        caixa = CaixaDoMaker()
        self._conferir(caixa, _aberta(0.49, 0.49), _n(1010, 0.49, 10.0, "SELL"))
        assert caixa.execucoes_no_nivel == 1
        assert caixa.shares_no_nivel == 10.0

        self._conferir(caixa, _aberta(0.49, 0.49), _n(1035, 0.49, 900.0, "SELL"), agora_s=1045)
        assert caixa.shares_no_nivel == 60.0

    def test_buy_ou_preco_acima_nao_pega_o_bid(self):
        caixa = CaixaDoMaker()
        novas = self._conferir(
            caixa,
            _aberta(0.49, 0.49),
            _n(1010, 0.47, 10.0, "BUY"),
            _n(1011, 0.50, 10.0, "SELL"),
        )
        assert novas == 0
        assert caixa.prints_vistos == 2

    def test_a_perna_down_olha_o_token_down(self):
        caixa = CaixaDoMaker()
        novas = self._conferir(
            caixa,
            _aberta(preco_up=0.79, preco_down=0.19),
            _n(1010, 0.18, 10.0, "SELL", perna="tok-down"),
            _n(1011, 0.18, 10.0, "SELL", perna="tok-up"),  # no Up, 0,18 < 0,79: pega
        )
        assert novas == 2
        assert {p.lado_up for p in caixa._pendentes} == {True, False}

    def test_print_de_antes_da_colocacao_nao_conta(self):
        caixa = CaixaDoMaker()
        novas = self._conferir(caixa, _aberta(desde=1000.0), _n(999, 0.40, 10.0, "SELL"))
        assert novas == 0

    def test_o_mesmo_print_nao_conta_duas_vezes(self):
        caixa = CaixaDoMaker()
        print_ = _n(1010, 0.47, 10.0, "SELL")
        self._conferir(caixa, _aberta(), print_, agora_s=1015)
        self._conferir(caixa, _aberta(), print_, agora_s=1030)
        assert caixa.execucoes_atravessadas == 1


class TestMarkout:
    def test_fecha_apos_o_horizonte_contra_o_nosso_preco(self):
        caixa = CaixaDoMaker()
        caixa.conferir_prints(
            "j",
            _aberta(0.49, 0.49),
            token_up="tok-up",
            token_down="tok-down",
            negocios_desde=_negocios(_n(1010, 0.47, 10.0, "SELL")),
            agora_ns=int(1012 * 1e9),
        )
        # 2 s depois: ainda não. Horizonte é 5 s.
        assert caixa.medir_markout(_livro_de(_livro(0.50)), agora_ns=int(1012 * 1e9)) == 0

        # 15 s depois, meio a 0,46: compramos a 0,49 → −3 ¢/share.
        medidas = caixa.medir_markout(_livro_de(_livro(0.46)), agora_ns=int(1025 * 1e9))
        assert medidas == 1
        assert caixa.markout_centavos_por_share == pytest.approx(-3.0)
        assert caixa.custo_de_markout_usdc == pytest.approx(-0.03 * 50.0)
        r = caixa.resumo()
        assert r["markout"]["horizonte_medio_s"] == pytest.approx(15.0)
        assert r["liquido_pro_rata_usdc"] == pytest.approx(-1.5)

    def test_sem_livro_espera_e_depois_do_prazo_desiste_contando(self):
        caixa = CaixaDoMaker()
        caixa.conferir_prints(
            "j",
            _aberta(),
            token_up="tok-up",
            token_down="tok-down",
            negocios_desde=_negocios(_n(1010, 0.47, 10.0, "SELL")),
            agora_ns=int(1012 * 1e9),
        )
        assert caixa.medir_markout(_livro_de(None), agora_ns=int(1030 * 1e9)) == 0
        assert len(caixa._pendentes) == 1
        assert caixa.medir_markout(_livro_de(None), agora_ns=int(1300 * 1e9)) == 0
        assert caixa._pendentes == []
        assert caixa.markout_sem_referencia == 1


def _print(asset_id, price, size, side):
    """O `last_trade_price` como o wire manda (§6.1a): decimais em string."""
    return {
        "event_type": "last_trade_price",
        "asset_id": asset_id,
        "price": price,
        "size": size,
        "side": side,
    }


class TestOAtrasoDoPrint:
    def test_atraso_e_agora_menos_o_carimbo_do_servidor(self):
        # 1789366908.437 s de parede, print carimbado 1789366905123 ms → 3,314 s.
        print_ = _n(1789366908.0, 0.5, 1.0, "SELL")
        assert _atraso_do_print_s(print_, 1789366908_437_000_000) is None
        print_.ts_servidor_ms = 1789366905123
        assert _atraso_do_print_s(print_, 1789366908_437_000_000) == pytest.approx(3.314)


class TestLivrosGuardamOsPrints:
    def test_print_de_token_com_livro_fica_e_sai_por_ts(self):
        livros = LivrosAoVivo()
        livros.aplicar(
            {
                "event_type": "book",
                "asset_id": "tok",
                "bids": [["0.49", "10"]],
                "asks": [["0.51", "10"]],
            },
            ts_ns=1,
        )
        livros.aplicar(
            _print("tok", "0.49", "7", "SELL"),
            ts_ns=5,
        )
        livros.aplicar(
            _print("tok", "0.51", "3", "BUY"),
            ts_ns=9,
        )

        assert livros.negocios_recebidos == 2
        assert [n.preco for n in livros.negocios_desde("tok", ts_ns=5)] == [0.51]
        assert livros.negocios_desde("tok", ts_ns=0)[0] == Negocio(5, 0.49, 7.0, "SELL")
        assert livros.resumo(agora_ns=10)["negocios_recebidos"] == 2

    def test_o_carimbo_do_servidor_fica_guardado_quando_vem(self):
        # §6.1a: `timestamp` em ms, como string. Sem ele, `None` — e não zero,
        # que pareceria um print de 1970 com atraso de décadas.
        livros = LivrosAoVivo()
        livros.aplicar(
            {"event_type": "book", "asset_id": "tok", "bids": [], "asks": []}, ts_ns=1
        )
        com = _print("tok", "0.49", "7", "SELL") | {"timestamp": "1789366908123"}
        livros.aplicar(com, ts_ns=5)
        livros.aplicar(_print("tok", "0.49", "7", "SELL"), ts_ns=6)
        livros.aplicar(_print("tok", "0.49", "7", "SELL") | {"timestamp": "x"}, ts_ns=7)

        carimbos = [n.ts_servidor_ms for n in livros.negocios_desde("tok", ts_ns=0)]
        assert carimbos == [1789366908123, None, None]

    def test_print_de_token_sem_livro_e_ignorado(self):
        livros = LivrosAoVivo()
        livros.aplicar(
            _print("x", "0.5", "1", "SELL"),
            ts_ns=5,
        )
        assert livros.negocios_recebidos == 0
        assert livros.eventos_ignorados == 1

    def test_esquecer_solta_os_prints_junto_com_o_livro(self):
        livros = LivrosAoVivo()
        livros.aplicar(
            {
                "event_type": "book",
                "asset_id": "tok",
                "bids": [["0.49", "10"]],
                "asks": [["0.51", "10"]],
            },
            ts_ns=1,
        )
        livros.aplicar(
            _print("tok", "0.49", "7", "SELL"),
            ts_ns=5,
        )
        livros.esquecer("tok")
        assert livros.negocios == {}


class TestOLacoLigaACaixa:
    async def test_passo_soma_rewards_e_conta_a_execucao_no_relato(self, tmp_path):
        laco = _laco(tmp_path)
        livro = _livro(0.50)
        await laco.passo(
            [_janela()], livro_de=_livro_de(livro), agora_epoch=1000.0, agora_ns=int(1000e9)
        )
        aberta = laco.abertas["btc-updown-4h-1"]
        assert aberta.preco_up == pytest.approx(0.49)
        assert aberta.preco_down == pytest.approx(0.49)

        # Segunda passada, 15 s depois, com um print SELL abaixo do nosso bid.
        prints = [_n(1005, 0.47, 10.0, "SELL", perna="tok-up")]
        await laco.passo(
            [_janela()],
            livro_de=_livro_de(livro),
            agora_epoch=1015.0,
            agora_ns=int(1015e9),
            negocios_desde=_negocios(*prints),
        )
        caixa = laco.resumo()["caixa"]
        assert caixa["segundos_repousando"] == pytest.approx(15.0)
        assert caixa["rewards_pro_rata_usdc"] > 0.0
        assert caixa["execucoes_possiveis"]["atravessadas"] == 1

        # Terceira passada: o markout fecha contra o livro de agora.
        await laco.passo(
            [_janela()],
            livro_de=_livro_de(_livro(0.50)),
            agora_epoch=1030.0,
            agora_ns=int(1030e9),
            negocios_desde=_negocios(*prints),
        )
        caixa = laco.resumo()["caixa"]
        assert caixa["markout"]["medidas"] == 1
        assert caixa["markout"]["centavos_por_share"] == pytest.approx(1.0)

    async def test_janela_que_fecha_zera_o_relogio_da_cotacao(self, tmp_path):
        laco = _laco(tmp_path)
        livro = _livro(0.50)
        await laco.passo(
            [_janela()], livro_de=_livro_de(livro), agora_epoch=1000.0, agora_ns=int(1000e9)
        )
        await laco.passo(
            [_janela()], livro_de=_livro_de(livro), agora_epoch=1015.0, agora_ns=int(1015e9)
        )
        await laco.passo([], livro_de=_livro_de(livro), agora_epoch=1030.0, agora_ns=int(1030e9))
        assert laco.abertas == {}
        assert "btc-updown-4h-1" not in laco.caixa._ultimo_acerto_epoch
        assert laco.caixa.segundos_repousando == pytest.approx(15.0)
