"""4.0(c) — O LAÇO: as quatro peças da rota maker finalmente conversando.

Os testes cobrem o que só aparece quando elas se compõem, e não quando cada
uma é testada sozinha:

- janela que fecha leva a cotação com ela (senão 24 h viram dezenas de órfãs);
- janela sem pool não recebe cotação (risco de execução por zero reward);
- livro indisponível NÃO cancela (perderia a fila por falta de dado nosso);
- MANTER não custa ida à rede;
- estado desconhecido (RECONCILIAR) NÃO é esquecido.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from pulsearb.backtest.book import OrderBook
from pulsearb.execution.cliente_sombra import ClienteSombraDeOrdens
from pulsearb.live.cotacao import AncoraDoMicroprice, Cotacao
from pulsearb.live.execucao_maker import ResultadoDaAcao
from pulsearb.live.laco_maker import LacoMaker
from pulsearb.live.rastreador import JanelaAoVivo


def _janela(slug="btc-updown-4h-1", com_pool=True, fechamento=10_000.0):
    return JanelaAoVivo(
        slug=slug,
        asset="btc",
        jogo="twap",
        condition_id="cond-1",
        token_up="tok-up",
        token_down="tok-down",
        duracao_s=14_400,
        abertura_epoch=fechamento - 14_400,
        fechamento_epoch=fechamento,
        tick_size=0.01,
        min_order_size=5.0,
        fee_rate=0.0,
        fee_exponent=1.0,
        reward_daily_rate=100.0 if com_pool else None,
        reward_min_size=5.0 if com_pool else None,
        reward_max_spread=0.03 if com_pool else None,
    )


def _livro(mid=0.50):
    """Livro com meio e profundidade suficientes para uma cotação pontuar."""
    return OrderBook(
        asset_id="tok-up",
        bids=[(mid - 0.01, 500.0), (mid - 0.02, 500.0)],
        asks=[(mid + 0.01, 500.0), (mid + 0.02, 500.0)],
    )


class _PortaoDuble:
    """Dublê do `PortaoDeRisco`. Só o método que o laço usa."""

    def __init__(self, pode=True, motivo=None):
        self.decisao = SimpleNamespace(pode=pode, motivo=motivo)
        self.consultas = []

    def avaliar_risco(self, ordem, *, feeds_saudaveis, melhor_bid, melhor_ask):
        self.consultas.append(ordem)
        return self.decisao


def _laco(tmp_path, portao=None, **kw):
    return LacoMaker(
        cliente=ClienteSombraDeOrdens(caminho_do_diario=tmp_path / "d.jsonl"),
        tamanho_da_cotacao=50.0,
        portao=portao if portao is not None else _PortaoDuble(),
        **kw,
    )


def _livro_de(livro):
    def obter(token_id, *, agora_ns):
        return livro

    return obter


class TestColocarECotar:
    async def test_janela_com_pool_recebe_cotacao(self, tmp_path):
        laco = _laco(tmp_path)

        efeitos = await laco.passo(
            [_janela()], livro_de=_livro_de(_livro()), agora_epoch=1000.0, agora_ns=1
        )

        assert efeitos
        assert efeitos[0].resultado is ResultadoDaAcao.COLOCADA
        assert len(laco.abertas) == 1
        # Duas pernas por janela: bid no Up e bid no Down (§15.3).
        assert len(laco.cliente.repousadas) == 2

    async def test_janela_SEM_pool_nao_recebe_cotacao(self, tmp_path):
        """Cotar sem pool é pagar risco de execução por zero reward."""
        laco = _laco(tmp_path)

        efeitos = await laco.passo(
            [_janela(com_pool=False)],
            livro_de=_livro_de(_livro()),
            agora_epoch=1000.0,
            agora_ns=1,
        )

        assert efeitos == []
        assert laco.abertas == {}
        assert laco.motivos.get("sem_pool_de_reward") == 1

    async def test_livro_indisponivel_NAO_cancela_o_que_ja_repousa(self, tmp_path):
        """Sair por falta de dado NOSSO perderia a fila de graça — e o livro
        pode voltar no passo seguinte."""
        laco = _laco(tmp_path)
        await laco.passo(
            [_janela()], livro_de=_livro_de(_livro()), agora_epoch=1000.0, agora_ns=1
        )
        assert len(laco.abertas) == 1

        await laco.passo(
            [_janela()], livro_de=_livro_de(None), agora_epoch=1100.0, agora_ns=2
        )

        assert len(laco.abertas) == 1  # continua repousando
        assert laco.motivos.get("livro_indisponivel") == 1


class TestJanelaQueFecha:
    async def test_janela_que_sai_da_lista_tem_a_cotacao_cancelada(self, tmp_path):
        """Sem isto, cada janela encerrada deixa uma órfã — dezenas em 24 h."""
        laco = _laco(tmp_path)
        await laco.passo(
            [_janela()], livro_de=_livro_de(_livro()), agora_epoch=1000.0, agora_ns=1
        )
        # Duas pernas por janela: bid no Up e bid no Down (§15.3).
        assert len(laco.cliente.repousadas) == 2

        # Passo seguinte sem a janela: ela fechou.
        efeitos = await laco.passo(
            [], livro_de=_livro_de(_livro()), agora_epoch=2000.0, agora_ns=2
        )

        assert efeitos[0].resultado is ResultadoDaAcao.CANCELADA
        assert laco.abertas == {}
        assert laco.cliente.repousadas == {}
        assert laco.motivos.get("janela_fechou") == 1

    async def test_pool_que_some_tira_a_cotacao_do_livro(self, tmp_path):
        laco = _laco(tmp_path)
        await laco.passo(
            [_janela()], livro_de=_livro_de(_livro()), agora_epoch=1000.0, agora_ns=1
        )

        await laco.passo(
            [_janela(com_pool=False)],
            livro_de=_livro_de(_livro()),
            agora_epoch=1100.0,
            agora_ns=2,
        )

        assert laco.abertas == {}
        assert laco.motivos.get("pool_sumiu") == 1


class TestHisterese:
    async def test_MANTER_nao_custa_ida_a_rede(self, tmp_path):
        """A histerese existe para não pagar o custo de trocar. Se o laço
        chamasse o I/O mesmo em MANTER, ela não economizaria nada."""
        laco = _laco(tmp_path)
        await laco.passo(
            [_janela()], livro_de=_livro_de(_livro()), agora_epoch=1000.0, agora_ns=1
        )
        antes = dict(laco.cliente.repousadas)

        # Logo em seguida, dentro do tempo mínimo repousada (30 s).
        efeitos = await laco.passo(
            [_janela()], livro_de=_livro_de(_livro()), agora_epoch=1005.0, agora_ns=2
        )

        assert efeitos == []
        assert laco.cliente.repousadas == antes


class TestOResumo:
    async def test_o_resumo_nomeia_por_que_nao_cotou(self, tmp_path):
        """`motivos` responde a pergunta que importa quando o bot não cota:
        não achou onde, ou achou e a histerese segurou?"""
        laco = _laco(tmp_path)
        await laco.passo(
            [_janela(com_pool=False)],
            livro_de=_livro_de(_livro()),
            agora_epoch=1000.0,
            agora_ns=1,
        )

        resumo = laco.resumo()
        assert resumo["cotacoes_repousando"] == 0
        assert resumo["motivos"]["sem_pool_de_reward"] == 1
        assert "nao cota" in resumo["nota"]


class TestOPortao:
    """O conserto do achado de 2026-09-07: o laço cotava por fora das travas.

    Com o disjuntor armado, o taker recusava tudo e o maker cotava
    normalmente — as duas rotas discordando sobre estar travadas.
    """

    async def test_toda_cotacao_nova_passa_pelo_portao(self, tmp_path):
        portao = _PortaoDuble(pode=True)
        laco = _laco(tmp_path, portao=portao)

        await laco.passo(
            [_janela()], livro_de=_livro_de(_livro()), agora_epoch=1000.0, agora_ns=1
        )

        # As DUAS pernas passam pelo portão, cada uma como a ordem que vai
        # para o fio — a do Down inclusive.
        assert len(portao.consultas) == 2
        assert {o.lado_up for o in portao.consultas} == {True, False}
        assert len(laco.abertas) == 1

    async def test_portao_que_recusa_impede_a_cotacao(self, tmp_path):
        laco = _laco(tmp_path, portao=_PortaoDuble(pode=False, motivo="kill_acionado"))

        efeitos = await laco.passo(
            [_janela()], livro_de=_livro_de(_livro()), agora_epoch=1000.0, agora_ns=1
        )

        assert efeitos == []
        assert laco.abertas == {}
        assert laco.cliente.repousadas == {}
        assert laco.motivos.get("portao:kill_acionado") == 1

    async def test_o_motivo_da_recusa_conta_UMA_vez_quando_ha_cotacao_repousando(
        self, tmp_path
    ):
        """`_sair` já conta o motivo com que sai, e contar antes dele punha o
        mesmo motivo DUAS vezes no relato — justamente quando a trava faz o
        que mais importa, tirar uma cotação que já estava no livro. O relato
        de 60 s é a métrica que diz quantas vezes cada trava agiu."""
        portao = _PortaoDuble()
        laco = _laco(tmp_path, portao=portao)
        await laco.passo(
            [_janela()], livro_de=_livro_de(_livro()), agora_epoch=1000.0, agora_ns=1
        )
        assert len(laco.abertas) == 1

        # o disjuntor arma no meio da rodada, com a cotação repousando
        portao.decisao = SimpleNamespace(pode=False, motivo="kill_acionado")
        efeitos = await laco.passo(
            [_janela()], livro_de=_livro_de(_livro()), agora_epoch=1015.0, agora_ns=2
        )

        assert len(efeitos) == 1 and laco.abertas == {}
        assert laco.motivos["portao:kill_acionado"] == 1

    async def test_SEM_portao_NAO_cota_falha_fechada(self, tmp_path):
        """Um laço que cotasse 'porque ninguém passou trava' seria o oposto do
        que a trava serve."""
        laco = LacoMaker(
            cliente=ClienteSombraDeOrdens(caminho_do_diario=tmp_path / "d.jsonl"),
            tamanho_da_cotacao=50.0,
            portao=None,
        )

        await laco.passo(
            [_janela()], livro_de=_livro_de(_livro()), agora_epoch=1000.0, agora_ns=1
        )

        assert laco.abertas == {}
        assert laco.motivos.get("portao:sem_portao") == 1

    async def test_o_portao_que_fecha_DEPOIS_tira_a_cotacao_do_livro(self, tmp_path):
        """Manter cotação que o portão não autorizaria HOJE é exposição que
        ninguém aprovou."""
        portao = _PortaoDuble(pode=True)
        laco = _laco(tmp_path, portao=portao)
        await laco.passo(
            [_janela()], livro_de=_livro_de(_livro()), agora_epoch=1000.0, agora_ns=1
        )
        assert len(laco.abertas) == 1

        # O risco muda: disjuntor arma no meio da rodada.
        portao.decisao = SimpleNamespace(pode=False, motivo="disjuntor_armado")
        await laco.passo(
            [_janela()], livro_de=_livro_de(_livro()), agora_epoch=1100.0, agora_ns=2
        )

        assert laco.abertas == {}
        assert laco.cliente.repousadas == {}

    async def test_CANCELAR_nao_passa_pelo_portao(self, tmp_path):
        """Um kill switch que impedisse cancelar prenderia a cotação no livro
        exatamente quando alguém puxou a chave para tirá-la de lá."""
        portao = _PortaoDuble(pode=True)
        laco = _laco(tmp_path, portao=portao)
        await laco.passo(
            [_janela()], livro_de=_livro_de(_livro()), agora_epoch=1000.0, agora_ns=1
        )
        consultas_antes = len(portao.consultas)

        # Portão fecha E a janela some: a saída tem de acontecer mesmo assim.
        portao.decisao = SimpleNamespace(pode=False, motivo="kill_acionado")
        efeitos = await laco.passo(
            [], livro_de=_livro_de(_livro()), agora_epoch=2000.0, agora_ns=2
        )

        assert efeitos[0].resultado is ResultadoDaAcao.CANCELADA
        assert laco.cliente.repousadas == {}
        # Nenhuma consulta nova: sair não pede autorização.
        assert len(portao.consultas) == consultas_antes


class TestOPrecoSegueOMeioDoLivro:
    """O achado da rodada r4 de 2026-09-14: 69 cotações entre 0,46 e 0,499 em
    mercados cujo meio ia de 0,03 a 0,97. O `montar` fechava sobre `meio=0.5`
    fixo. Num mercado a 0,90 o bid ficava 40 ¢ abaixo do meio (não pontua,
    não executa); num a 0,10, 39 ¢ ACIMA do ask (executa na hora, como taker).
    """

    async def _pernas(self, tmp_path, mid):
        laco = _laco(tmp_path)
        await laco.passo(
            [_janela()], livro_de=_livro_de(_livro(mid=mid)), agora_epoch=1000.0, agora_ns=1
        )
        assert len(laco.cliente.repousadas) == 2, laco.motivos
        por_lado = {r.ordem.lado_up: r.ordem for r in laco.cliente.repousadas.values()}
        return por_lado[True], por_lado[False]

    async def test_o_bid_do_Up_fica_ABAIXO_do_meio_de_cada_mercado(self, tmp_path):
        up_alto, _ = await self._pernas(tmp_path, mid=0.80)
        up_baixo, _ = await self._pernas(tmp_path, mid=0.30)

        # Abaixo do meio e dentro da grade avaliada (1 a 5 ticks).
        assert 0.75 <= up_alto.preco_limite < 0.80
        assert 0.25 <= up_baixo.preco_limite < 0.30
        assert up_alto.token_id == "tok-up" and up_alto.lado_up

    async def test_o_bid_do_Down_e_o_espelho_do_ask_do_Up(self, tmp_path):
        """Bid no Down a `(1 − meio) − d` = ask no Up a `meio + d`: é o preço
        que `estimar_retorno` pontua do lado ask, e as duas pernas ficam à
        MESMA distância do meio."""
        up, down = await self._pernas(tmp_path, mid=0.80)

        assert down.token_id == "tok-down" and not down.lado_up
        assert down.preco_limite < 0.20
        distancia_up = round(0.80 - up.preco_limite, 6)
        distancia_down = round(0.20 - down.preco_limite, 6)
        assert distancia_up == distancia_down > 0

    async def test_o_portao_ve_os_MESMOS_precos_que_vao_para_o_livro(self, tmp_path):
        portao = _PortaoDuble(pode=True)
        laco = _laco(tmp_path, portao=portao)
        await laco.passo(
            [_janela()], livro_de=_livro_de(_livro(mid=0.80)), agora_epoch=1000.0, agora_ns=1
        )

        vistos = {(o.token_id, o.preco_limite) for o in portao.consultas}
        colocados = {
            (r.ordem.token_id, r.ordem.preco_limite)
            for r in laco.cliente.repousadas.values()
        }
        assert vistos == colocados
        assert all(p < 0.80 for _, p in vistos)

    async def test_fora_da_faixa_de_lado_unico_cota_dos_DOIS_lados(self, tmp_path):
        """§15.3: fora de [0,10, 0,90] um lado só vale ZERO — só a cotação de
        dois lados pontua ali. A 0,95 o bid do Down fica perto de zero e
        continua sendo uma ordem válida."""
        up, down = await self._pernas(tmp_path, mid=0.95)

        assert 0.90 <= up.preco_limite < 0.95
        assert 0.0 < down.preco_limite <= 0.05

    async def test_livro_sem_meio_NAO_cota_e_NAO_cancela(self, tmp_path):
        laco = _laco(tmp_path)
        await laco.passo(
            [_janela()], livro_de=_livro_de(_livro()), agora_epoch=1000.0, agora_ns=1
        )
        assert len(laco.abertas) == 1

        so_bids = OrderBook(asset_id="tok-up", bids=[(0.49, 500.0)], asks=[])
        await laco.passo(
            [_janela()], livro_de=_livro_de(so_bids), agora_epoch=1100.0, agora_ns=2
        )

        assert len(laco.abertas) == 1
        assert laco.motivos.get("livro_sem_meio") == 1

    async def test_o_portao_que_recusa_a_perna_do_Down_barra_a_cotacao_inteira(self, tmp_path):
        class _SoUp(_PortaoDuble):
            def avaliar_risco(self, ordem, **kw):
                self.consultas.append(ordem)
                if ordem.lado_up:
                    return SimpleNamespace(pode=True, motivo=None)
                return SimpleNamespace(pode=False, motivo="preco_fora_da_faixa")

        laco = _laco(tmp_path, portao=_SoUp())
        await laco.passo(
            [_janela()], livro_de=_livro_de(_livro(mid=0.80)), agora_epoch=1000.0, agora_ns=1
        )

        assert laco.cliente.repousadas == {}
        assert laco.motivos.get("portao:preco_fora_da_faixa") == 1

    async def test_janela_que_fecha_cancela_as_DUAS_pernas(self, tmp_path):
        laco = _laco(tmp_path)
        await laco.passo(
            [_janela()], livro_de=_livro_de(_livro()), agora_epoch=1000.0, agora_ns=1
        )
        assert len(laco.cliente.repousadas) == 2

        await laco.passo([], livro_de=_livro_de(_livro()), agora_epoch=2000.0, agora_ns=2)

        assert laco.cliente.repousadas == {}
        assert laco.abertas == {}


# ═══════════════ recolher quando o livro anda contra — a regra dos leaderboards


class TestAncoraDoMicroprice:
    """O microprice como teto da cotação, no laço inteiro.

    A medida está em `docs/OUTROS_BOTS.md` §6 item 6; aqui se trava o que a
    porta para o caminho ao vivo garante: o preço ENVIADO recua, a linha de
    base (knob desligado) não muda, e não se cota sob uma regra que não se
    conseguiu avaliar.
    """

    def _livros(self, *, tamanho_do_bid, tamanho_do_ask):
        """Up desequilibrado e Down no espelho exato dele."""
        up = OrderBook(
            asset_id="tok-up",
            bids=[(0.49, tamanho_do_bid)],
            asks=[(0.51, tamanho_do_ask)],
        )
        down = OrderBook(
            asset_id="tok-down",
            bids=[(0.49, tamanho_do_ask)],
            asks=[(0.51, tamanho_do_bid)],
        )

        def livro_de(token_id, *, agora_ns):
            return down if token_id == "tok-down" else up

        return livro_de, up

    async def test_o_microprice_abaixo_do_meio_recua_o_bid_enviado(self, tmp_path):
        """Ask grande e bid pequeno: o microprice fica ABAIXO do meio (0,492),
        e o bid recua de 0,49 para 0,48. É o preço que VAI PARA O DIÁRIO que
        muda — não só o que a conta imagina."""
        livro_de, up = self._livros(tamanho_do_bid=100.0, tamanho_do_ask=900.0)
        assert up.microprice < up.mid

        base = _laco(tmp_path)
        await base.passo([_janela()], livro_de=livro_de, agora_epoch=1000.0, agora_ns=1)
        assert base.abertas["btc-updown-4h-1"].preco_up == pytest.approx(0.49)

        (tmp_path / "b").mkdir()
        ancorado = _laco(tmp_path / "b", ticks_abaixo_do_microprice=1)
        await ancorado.passo(
            [_janela()], livro_de=livro_de, agora_epoch=1000.0, agora_ns=1
        )
        aberta = ancorado.abertas["btc-updown-4h-1"]
        assert aberta.preco_up == pytest.approx(0.48)
        # e é o preço que foi de fato enviado, não só o registrado
        precos = sorted(r.ordem.preco_limite for r in ancorado.cliente.repousadas.values())
        assert precos[0] == pytest.approx(0.48)

    async def test_microprice_acima_do_meio_nao_puxa_a_cotacao_para_perto(self, tmp_path):
        """A âncora só APERTA: com o microprice acima do meio, a perna do Up
        fica onde a distância do meio a pôs."""
        livro_de, up = self._livros(tamanho_do_bid=900.0, tamanho_do_ask=100.0)
        assert up.microprice > up.mid
        laco = _laco(tmp_path, ticks_abaixo_do_microprice=1)
        await laco.passo([_janela()], livro_de=livro_de, agora_epoch=1000.0, agora_ns=1)
        assert laco.abertas["btc-updown-4h-1"].preco_up == pytest.approx(0.49)

    async def test_a_perna_down_e_ancorada_pelo_microprice_DELA(self, tmp_path):
        """Os dois livros são independentes (revisão do #126): aqui o do Up
        pende para cima e o do Down para baixo, então quem recua é a perna
        Down. Ela é um BID no livro dela, e a âncora vai ESPELHADA — usar a
        do Up faria a conta avaliar um preço e o laço enviar outro."""
        up = OrderBook(asset_id="tok-up", bids=[(0.49, 900.0)], asks=[(0.51, 100.0)])
        down = OrderBook(asset_id="tok-down", bids=[(0.49, 100.0)], asks=[(0.51, 900.0)])

        def livro_de(token_id, *, agora_ns):
            return down if token_id == "tok-down" else up

        laco = _laco(tmp_path, ticks_abaixo_do_microprice=1)
        await laco.passo([_janela()], livro_de=livro_de, agora_epoch=1000.0, agora_ns=1)
        aberta = laco.abertas["btc-updown-4h-1"]
        assert aberta.preco_up == pytest.approx(0.49)  # o Up não foi apertado
        assert aberta.preco_down == pytest.approx(0.48)  # o Down foi
        # e o preço avaliado pela conta (a perna Down é o ask do livro do Up)
        # é o MESMO número que saiu para o fio
        avaliado = Cotacao(distancia_ticks=1, tamanho=50.0).preco(
            0.50,
            0.01,
            do_lado_bid=False,
            ancora=AncoraDoMicroprice(bid=up.microprice, ask=1.0 - down.microprice),
        )
        assert 1.0 - aberta.preco_down == pytest.approx(avaliado)

    async def test_perder_folga_NAO_e_emergencia(self, tmp_path):
        """A ordem continua ABAIXO do microprice, só com menos folga do que se
        pediu. Isso não pode furar a histerese.

        Foi o meu defeito: tratar a folga de `ticks` como se fosse o limite
        duro punha a ordem acima do 'teto' a cada passo de 1 tick do meio, e
        como a trava vence a histerese, o maker trocava a cotação em 39 de 40
        passadas — todas antes do repouso mínimo, contra 2,05 com a regra
        desligada. Reposicionar custa a fila, e nem a simulação do
        `maker_de_pares` nem o SHADOW enxergam esse custo."""
        laco = _laco(tmp_path, ticks_abaixo_do_microprice=1)
        await laco.passo(
            [_janela()], livro_de=self._com_topos(500.0, 500.0),
            agora_epoch=1000.0, agora_ns=1,
        )
        assert laco.abertas["btc-updown-4h-1"].preco_up == pytest.approx(0.49)

        # topo vira para 100/900: microprice 0,492 — a ordem a 0,49 perdeu
        # folga (o teto virou 0,482), mas segue ABAIXO do microprice
        efeitos = await laco.passo(
            [_janela()], livro_de=self._com_topos(100.0, 900.0),
            agora_epoch=1010.0, agora_ns=2,
        )

        assert efeitos == []
        assert laco.motivos.get("acima_do_microprice", 0) == 0
        assert laco.abertas["btc-updown-4h-1"].preco_up == pytest.approx(0.49)

    async def test_nenhuma_troca_antes_do_repouso_minimo_num_passeio_do_meio(
        self, tmp_path
    ):
        """A regressão de churn, travada: o meio passeando 1 tick a cada
        passada de 15 s não pode produzir NENHUMA troca antes dos 30 s de
        repouso mínimo. Com a folga tratada como limite duro eram 39 em 40."""
        laco = _laco(tmp_path, ticks_abaixo_do_microprice=1)
        meio, trocas_cedo = 0.50, 0
        for i in range(40):
            up = OrderBook(
                asset_id="tok-up",
                bids=[(round(meio - 0.01, 4), 500.0)],
                asks=[(round(meio + 0.01, 4), 500.0)],
            )
            down = OrderBook(
                asset_id="tok-down",
                bids=[(round(1 - meio - 0.01, 4), 500.0)],
                asks=[(round(1 - meio + 0.01, 4), 500.0)],
            )
            antes = laco.abertas.get("btc-updown-4h-1")
            agora = 1000.0 + i * 15.0
            await laco.passo(
                [_janela(fechamento=100_000.0)],
                livro_de=lambda t, *, agora_ns, u=up, d=down: d if t == "tok-down" else u,
                agora_epoch=agora,
                agora_ns=i + 1,
            )
            depois = laco.abertas.get("btc-updown-4h-1")
            trocou = antes is not None and (
                depois is None or depois.desde_epoch != antes.desde_epoch
            )
            if trocou and agora - antes.desde_epoch < 30.0:
                trocas_cedo += 1
            meio = round(0.50 + 0.01 * ((i % 4) - 1), 4)

        assert trocas_cedo == 0

    async def test_sem_ancora_a_mesma_troca_de_topo_nao_mexe_na_ordem(self, tmp_path):
        """A linha de base não muda: sem a âncora, `Cotacao` igual é preço
        igual, e não reagir é o que preserva a fila."""
        entrando, _ = self._livros(tamanho_do_bid=900.0, tamanho_do_ask=100.0)
        laco = _laco(tmp_path)
        await laco.passo([_janela()], livro_de=entrando, agora_epoch=1000.0, agora_ns=1)
        virou, _ = self._livros(tamanho_do_bid=100.0, tamanho_do_ask=900.0)
        efeitos = await laco.passo(
            [_janela()], livro_de=virou, agora_epoch=1100.0, agora_ns=2
        )
        assert efeitos == []
        assert laco.motivos["estavel"] >= 1
        assert laco.abertas["btc-updown-4h-1"].preco_up == pytest.approx(0.49)

    def _com_topos(self, tamanho_do_bid, tamanho_do_ask):
        """Livros com topo de tamanho escolhido — é o tamanho que move o
        microprice, com o meio parado em 0,50."""
        up = OrderBook(
            asset_id="tok-up",
            bids=[(0.49, tamanho_do_bid), (0.48, 500.0)],
            asks=[(0.51, tamanho_do_ask), (0.52, 500.0)],
        )
        down = OrderBook(
            asset_id="tok-down",
            bids=[(0.49, tamanho_do_ask), (0.48, 500.0)],
            asks=[(0.51, tamanho_do_bid), (0.52, 500.0)],
        )

        def livro_de(token_id, *, agora_ns):
            return down if token_id == "tok-down" else up

        return livro_de

    async def test_o_teto_que_AFROUXA_tira_a_ordem_do_preco_de_fora(self, tmp_path):
        """A catraca: o teto aperta, a ordem vai para fora, e quando o topo
        engorda de novo ela NÃO volta — a mesma `Cotacao` vence, o atalho do
        `estavel` a dá como igual, e ela fica no preço de fora pontuando menos
        para sempre. Não é risco, é oportunidade: quem decide são a histerese
        e o piso de ganho, mas o atalho nem os deixava opinar (revisão do
        Codex, #127; caso achado por busca sobre o próprio código)."""
        laco = _laco(tmp_path, ticks_abaixo_do_microprice=1)
        # topo magro do lado do bid: microprice 0,494, teto 0,484 → 0,48
        await laco.passo(
            [_janela()], livro_de=self._com_topos(5.0, 20.0),
            agora_epoch=1000.0, agora_ns=1,
        )
        aberta = laco.abertas["btc-updown-4h-1"]
        assert aberta.preco_up == pytest.approx(0.48)
        assert aberta.cotacao.distancia_ticks == 1

        # o topo equilibra: microprice 0,50, teto 0,49 — a MESMA cotação de 1
        # tick agora caberia a 0,49, e a ordem a 0,48 não está acima do teto
        efeitos = await laco.passo(
            [_janela()], livro_de=self._com_topos(5.0, 5.0),
            agora_epoch=1200.0, agora_ns=2,
        )

        # o atalho não fecha mais a porta: quem decide passa a ser o piso de
        # ganho, e neste pool pequeno ele segura — o que é a política certa
        assert laco.motivos.get("estavel", 0) == 0
        assert laco.motivos["ganho_abaixo_do_piso"] == 1
        assert efeitos == []

    async def test_com_pool_que_paga_o_teto_que_afrouxa_reposiciona_de_fato(
        self, tmp_path
    ):
        """O mesmo caso com um pool que paga: aí o ganho vence o piso e a
        ordem volta para perto do meio. É o que a catraca impedia."""
        janela = replace(_janela(), reward_daily_rate=1_000.0)
        laco = _laco(tmp_path, ticks_abaixo_do_microprice=1)
        await laco.passo(
            [janela], livro_de=self._com_topos(5.0, 20.0),
            agora_epoch=1000.0, agora_ns=1,
        )
        assert laco.abertas["btc-updown-4h-1"].preco_up == pytest.approx(0.48)

        efeitos = await laco.passo(
            [janela], livro_de=self._com_topos(5.0, 5.0),
            agora_epoch=1200.0, agora_ns=2,
        )

        assert len(efeitos) == 1
        assert laco.abertas["btc-updown-4h-1"].preco_up == pytest.approx(0.49)

    async def test_sem_candidata_que_pontue_o_teto_ainda_tira_a_ordem(self, tmp_path):
        """O livro ALARGA com o meio parado: toda a grade ancorada cai fora da
        faixa de reward e nenhuma candidata pontua. A que repousa continua
        pontuando — então `atual_nao_pontua_mais` não a pega — e sem o teto
        ela ficaria acima dele para sempre. É justamente quando o teto mais
        importa, e era quando ele não existia (revisão do Codex, #127)."""
        laco = _laco(tmp_path, ticks_abaixo_do_microprice=1)
        await laco.passo(
            [_janela()], livro_de=self._com_topos(500.0, 500.0),
            agora_epoch=1000.0, agora_ns=1,
        )
        assert laco.abertas["btc-updown-4h-1"].preco_up == pytest.approx(0.49)

        # 0,40/0,60 com o ask pesado nos DOIS livros (eles são independentes):
        # microprice ~0,40 em cada um, então o bid ancorado desce para 0,39 e
        # o ask sobe para 0,61 — a grade inteira sai da faixa de 3 ¢, e nenhuma
        # candidata pontua. A ordem a 0,49 continua pontuando.
        largo_up = OrderBook(
            asset_id="tok-up", bids=[(0.40, 5.0)], asks=[(0.60, 5_000.0)]
        )
        largo_down = OrderBook(
            asset_id="tok-down", bids=[(0.40, 5.0)], asks=[(0.60, 5_000.0)]
        )

        def livro_de(token_id, *, agora_ns):
            return largo_down if token_id == "tok-down" else largo_up

        assert largo_up.mid == pytest.approx(0.50)  # o MEIO não andou
        efeitos = await laco.passo(
            [_janela()], livro_de=livro_de, agora_epoch=1200.0, agora_ns=2
        )

        assert laco.motivos["acima_do_microprice"] == 1
        assert len(efeitos) == 1
        assert laco.abertas == {}

    async def test_desligada_por_padrao(self, tmp_path):
        laco = _laco(tmp_path)
        assert laco.ticks_abaixo_do_microprice is None

    async def test_sem_microprice_a_caixa_conta_e_o_portao_ainda_tira_do_livro(
        self, tmp_path
    ):
        """Faltar o microprice impede COTAR, e só. A passada tem de seguir: a
        caixa conta o repouso (senão a falta trunca em silêncio a medida que
        a rodada existe para fazer) e o portão tem de poder tirar do livro o
        que já está lá — um disjuntor que armasse durante a falta não a
        tiraria. É a propriedade que o 4.0 registra ter custado caro para
        achar (revisão do Codex, #127)."""
        livro_de, _ = self._livros(tamanho_do_bid=100.0, tamanho_do_ask=900.0)
        portao = _PortaoDuble()
        laco = _laco(tmp_path, portao=portao, ticks_abaixo_do_microprice=1)
        await laco.passo([_janela()], livro_de=livro_de, agora_epoch=1000.0, agora_ns=1)
        assert len(laco.abertas) == 1

        up = OrderBook(asset_id="tok-up", bids=[(0.49, 100.0)], asks=[(0.51, 900.0)])
        so_bids = OrderBook(asset_id="tok-down", bids=[(0.49, 900.0)], asks=[])

        def sem_o_ask_do_down(token_id, *, agora_ns):
            return so_bids if token_id == "tok-down" else up

        # com o portão aberto: não cota, conta o motivo, e a caixa acerta o
        # tempo repousado
        await laco.passo(
            [_janela()], livro_de=sem_o_ask_do_down, agora_epoch=1015.0, agora_ns=2
        )
        assert laco.motivos["sem_microprice"] == 1
        assert len(laco.abertas) == 1
        assert laco.caixa.acertos == 1
        assert laco.caixa.segundos_repousando == pytest.approx(15.0)

        # o disjuntor arma DURANTE a falta: a ordem sai
        portao.decisao = SimpleNamespace(pode=False, motivo="kill_acionado")
        efeitos = await laco.passo(
            [_janela()], livro_de=sem_o_ask_do_down, agora_epoch=1030.0, agora_ns=3
        )
        assert len(efeitos) == 1 and laco.abertas == {}
        assert laco.motivos["portao:kill_acionado"] == 1

    async def test_sem_microprice_nao_cota_e_nao_cancela_o_que_repousa(self, tmp_path):
        """Com a âncora ligada e o livro do Down de um lado só, não há como
        avaliar a regra — e cotar sob uma regra que não se aplicou é o que a
        falha fechada existe para impedir. O que já repousa FICA: é falta de
        dado nosso, como o `livro_indisponivel`."""
        livro_de, _ = self._livros(tamanho_do_bid=100.0, tamanho_do_ask=900.0)
        laco = _laco(tmp_path, ticks_abaixo_do_microprice=1)
        await laco.passo([_janela()], livro_de=livro_de, agora_epoch=1000.0, agora_ns=1)
        assert len(laco.abertas) == 1

        up = OrderBook(asset_id="tok-up", bids=[(0.49, 100.0)], asks=[(0.51, 900.0)])
        so_bids = OrderBook(asset_id="tok-down", bids=[(0.49, 900.0)], asks=[])

        def sem_o_ask_do_down(token_id, *, agora_ns):
            return so_bids if token_id == "tok-down" else up

        await laco.passo(
            [_janela()], livro_de=sem_o_ask_do_down, agora_epoch=1015.0, agora_ns=2
        )
        assert laco.motivos["sem_microprice"] == 1
        assert len(laco.abertas) == 1


def _livro_com_bids(*bids, asks=((0.51, 500.0), (0.52, 500.0))):
    return OrderBook(asset_id="tok-up", bids=list(bids), asks=list(asks))


class TestRecolherQuandoOLivroAnda:
    async def _com_cotacao(self, tmp_path, **kw):
        laco = _laco(tmp_path, **kw)
        await laco.passo(
            [_janela()], livro_de=_livro_de(_livro(0.50)), agora_epoch=1000.0, agora_ns=1
        )
        assert len(laco.abertas) == 1
        assert laco.abertas["btc-updown-4h-1"].preco_up == 0.49
        return laco

    async def test_melhor_bid_abaixo_da_cotacao_recolhe_com_nome(self, tmp_path):
        laco = await self._com_cotacao(tmp_path, recolhe_quando_o_livro_anda=True)
        # primeira observação só marca a referência (o livro de quando entramos)
        assert await laco.recolher_se_o_livro_andou(_livro_de(_livro(0.50)), agora_ns=2) == []
        # o mercado andou: melhor bid a 0,47, abaixo do nosso 0,49
        efeitos = await laco.recolher_se_o_livro_andou(
            _livro_de(_livro_com_bids((0.47, 500.0))), agora_ns=3
        )
        assert len(efeitos) == 1
        assert laco.abertas == {}
        assert not laco.cliente.repousadas  # as DUAS pernas saíram
        assert laco.motivos["livro_andou_contra"] == 1

    async def test_livro_parado_nao_recolhe(self, tmp_path):
        laco = await self._com_cotacao(tmp_path, recolhe_quando_o_livro_anda=True)
        assert await laco.recolher_se_o_livro_andou(_livro_de(_livro(0.50)), agora_ns=2) == []
        assert await laco.recolher_se_o_livro_andou(_livro_de(_livro(0.50)), agora_ns=3) == []
        assert len(laco.abertas) == 1

    async def test_desligada_por_padrao_nao_recolhe_mesmo_com_o_livro_andando(self, tmp_path):
        laco = await self._com_cotacao(tmp_path)
        assert laco.recolhe_quando_o_livro_anda is False
        assert await laco.recolher_se_o_livro_andou(
            _livro_de(_livro_com_bids((0.47, 500.0))), agora_ns=2
        ) == []
        assert len(laco.abertas) == 1

    async def test_livro_indisponivel_nao_recolhe(self, tmp_path):
        """Sair por falta de dado nosso perde a fila de graça — mesma regra
        do passo."""
        laco = await self._com_cotacao(tmp_path, recolhe_quando_o_livro_anda=True)
        await laco.recolher_se_o_livro_andou(_livro_de(_livro(0.50)), agora_ns=2)
        assert await laco.recolher_se_o_livro_andou(_livro_de(None), agora_ns=3) == []
        assert len(laco.abertas) == 1

    async def test_cotacao_que_ja_melhora_o_topo_nao_e_recolhida_de_saida(self, tmp_path):
        """Livro largo (bid 0,40 / ask 0,60, meio 0,50): a cotação nasce a 0,49,
        ACIMA do melhor bid do mercado. Isso não é o mercado andando contra —
        é o livro em que entramos. Só recolhe se o mercado cair de onde
        estava (revisão do Codex, #126)."""
        largo = _livro_com_bids((0.40, 500.0), asks=((0.60, 500.0),))
        laco = _laco(tmp_path, recolhe_quando_o_livro_anda=True)
        await laco.passo([_janela()], livro_de=_livro_de(largo), agora_epoch=1000.0, agora_ns=1)
        # a grade escolhe a distância pelo líquido; o que importa é que a
        # cotação nasce ACIMA do melhor bid do mercado (0,40)
        assert 0.40 < laco.abertas["btc-updown-4h-1"].preco_up < 0.50
        for n in (2, 3, 4):
            assert await laco.recolher_se_o_livro_andou(_livro_de(largo), agora_ns=n) == []
        assert len(laco.abertas) == 1
        # agora o mercado caiu de 0,40 para 0,38: recolhe
        caiu = _livro_com_bids((0.38, 500.0), asks=((0.60, 500.0),))
        assert len(await laco.recolher_se_o_livro_andou(_livro_de(caiu), agora_ns=5)) == 1
        assert laco.abertas == {}

    async def test_mercado_que_anda_no_primeiro_segundo_e_recolhido_na_primeira_observacao(
        self, tmp_path
    ):
        """A referência nasce na COLOCAÇÃO (livro 0,49/0,51 → referência 0,49),
        não na primeira observação do sono: se o mercado cair para 0,47 dentro
        do primeiro segundo, o primeiro poll já recolhe (revisão do Codex, #126)."""
        laco = await self._com_cotacao(tmp_path, recolhe_quando_o_livro_anda=True)
        chave = ("btc-updown-4h-1", int(laco.abertas["btc-updown-4h-1"].desde_epoch * 1e6))
        assert laco._referencia_do_recolher[chave][0] == pytest.approx(0.49)
        efeitos = await laco.recolher_se_o_livro_andou(
            _livro_de(_livro_com_bids((0.47, 500.0))), agora_ns=2
        )
        assert len(efeitos) == 1 and laco.abertas == {}

    async def test_acerta_o_ultimo_intervalo_de_reward_antes_de_sair(self, tmp_path):
        """Colocada em t=1000 e recolhida em t=1005: os 5 s repousando contam,
        com o livro e os parâmetros da janela — `_sair` apaga o relógio, e sem
        isto recolher muito empurraria o reward para baixo por construção."""
        laco = await self._com_cotacao(tmp_path, recolhe_quando_o_livro_anda=True)
        assert laco.caixa.segundos_repousando == 0.0
        efeitos = await laco.recolher_se_o_livro_andou(
            _livro_de(_livro_com_bids((0.47, 500.0))), agora_ns=int(1005e9)
        )
        assert len(efeitos) == 1
        assert laco.caixa.segundos_repousando == pytest.approx(5.0)
        assert laco.caixa.acertos == 1

    async def test_em_live_cotacao_que_melhora_o_topo_compara_o_bid_externo(self, tmp_path):
        """Livro largo (bid externo 0,40) e a nossa ordem NO livro, acima
        dele: o melhor bid do livro é a nossa própria ordem, e comparar o
        topo com a referência (0,40) nunca dispararia (revisão do Codex,
        #126). O que se compara é o melhor bid EXTERNO — o livro sem o
        nosso nível: 0,40 segura, 0,38 recolhe, e só nós no lado recolhe."""
        largo = _livro_com_bids((0.40, 500.0), asks=((0.60, 500.0),))
        laco = _laco(tmp_path, recolhe_quando_o_livro_anda=True, nossa_ordem_esta_no_livro=True)
        await laco.passo([_janela()], livro_de=_livro_de(largo), agora_epoch=1000.0, agora_ns=1)
        nosso = laco.abertas["btc-updown-4h-1"].preco_up
        assert 0.40 < nosso < 0.50
        com_a_nossa = _livro_com_bids((nosso, 50.0), (0.40, 500.0), asks=((0.60, 500.0),))
        assert await laco.recolher_se_o_livro_andou(_livro_de(com_a_nossa), agora_ns=2) == []
        assert len(laco.abertas) == 1
        caiu = _livro_com_bids((nosso, 50.0), (0.38, 500.0), asks=((0.60, 500.0),))
        assert len(await laco.recolher_se_o_livro_andou(_livro_de(caiu), agora_ns=3)) == 1
        assert laco.abertas == {}
        # só a nossa no lado dos bids: o mercado foi embora
        await laco.passo([_janela()], livro_de=_livro_de(largo), agora_epoch=1010.0, agora_ns=4)
        sozinha = _livro_com_bids((nosso, 50.0), asks=((0.60, 500.0),))
        assert len(await laco.recolher_se_o_livro_andou(_livro_de(sozinha), agora_ns=5)) == 1

    async def test_em_live_a_referencia_da_recotacao_desconta_a_nossa_ordem_anterior(
        self, tmp_path
    ):
        """Na recotação em LIVE, o livro que coloca a cotação nova ainda tem a
        ANTERIOR: sem descontá-la a referência seria a nossa própria ordem."""
        laco = _laco(tmp_path, recolhe_quando_o_livro_anda=True, nossa_ordem_esta_no_livro=True)
        await laco.passo(
            [_janela()], livro_de=_livro_de(_livro(0.50)), agora_epoch=1000.0, agora_ns=1
        )
        antiga = laco.abertas["btc-updown-4h-1"]
        assert antiga.preco_up == 0.49
        # os asks subiram (meio 0,60): o livro mostra a NOSSA 0,49 no topo dos
        # bids e o bid externo 0,48 atrás dela
        com_a_antiga = _livro_com_bids((0.49, 50.0), (0.48, 500.0), asks=((0.71, 500.0),))
        await laco.passo(
            [_janela()], livro_de=_livro_de(com_a_antiga), agora_epoch=1100.0, agora_ns=2
        )
        nova = laco.abertas["btc-updown-4h-1"]
        assert nova.desde_epoch != antiga.desde_epoch and nova.preco_up > 0.49
        chave = ("btc-updown-4h-1", int(nova.desde_epoch * 1e6))
        # 0,48 (externo), não 0,49 (a nossa antiga)
        assert laco._referencia_do_recolher[chave][0] == pytest.approx(0.48)

    async def test_janela_que_nunca_cotou_nao_deixa_tokens_nem_parametros(self, tmp_path):
        """Tokens e parâmetros entram para toda janela AVALIADA — sem pool, com
        o portão fechado, sem livro —, e a limpeza da saída da cotação não os
        alcança quando nunca houve cotação (revisão do Codex, #126). A janela
        sumir tem de levá-los."""
        laco = _laco(tmp_path, portao=_PortaoDuble(pode=False, motivo="fechado"))
        await laco.passo(
            [_janela(), _janela(slug="eth-updown-4h-1", com_pool=False)],
            livro_de=_livro_de(_livro(0.50)), agora_epoch=1000.0, agora_ns=1,
        )
        assert laco.abertas == {}
        assert set(laco._tokens) == {"btc-updown-4h-1", "eth-updown-4h-1"}
        assert set(laco._params) == {"btc-updown-4h-1"}
        await laco.passo([], livro_de=_livro_de(_livro(0.50)), agora_epoch=1001.0, agora_ns=2)
        assert laco._tokens == {} and laco._params == {}

    async def test_a_referencia_do_down_vem_do_livro_do_down_nao_do_espelho_do_up(
        self, tmp_path
    ):
        """Up 0,49/0,51 e Down com bid 0,35 parado: o espelho do Up diria
        referência 0,49 para o Down e o primeiro poll recolheria um par que
        não andou (revisão do Codex, #126). Cada perna lê o SEU livro: a
        referência do Down é 0,35, o par parado fica, e cair para 0,33 recolhe."""
        up = _livro(0.50)
        down = OrderBook(asset_id="tok-down", bids=[(0.35, 500.0)], asks=[(0.51, 500.0)])

        def livro_de(token_id, *, agora_ns, _down=down):
            return _down if token_id == "tok-down" else up

        laco = _laco(tmp_path, recolhe_quando_o_livro_anda=True)
        await laco.passo([_janela()], livro_de=livro_de, agora_epoch=1000.0, agora_ns=1)
        aberta = laco.abertas["btc-updown-4h-1"]
        chave = ("btc-updown-4h-1", int(aberta.desde_epoch * 1e6))
        assert laco._referencia_do_recolher[chave] == (pytest.approx(0.49), pytest.approx(0.35))
        for n in (2, 3):
            assert await laco.recolher_se_o_livro_andou(livro_de, agora_ns=n) == []
        assert len(laco.abertas) == 1
        caiu = OrderBook(asset_id="tok-down", bids=[(0.33, 500.0)], asks=[(0.51, 500.0)])

        def livro_de_caiu(token_id, *, agora_ns):
            return caiu if token_id == "tok-down" else up

        assert len(await laco.recolher_se_o_livro_andou(livro_de_caiu, agora_ns=4)) == 1
        assert laco.motivos["livro_andou_contra"] == 1

    async def test_perna_sem_livro_na_colocacao_marca_a_referencia_na_primeira_observacao(
        self, tmp_path
    ):
        """Down sem livro ao colocar: a perna fica SEM referência (não com a
        do espelho), o primeiro poll com livro do Down marca do livro dele e
        não decide; só o mercado cair dali recolhe."""
        up = _livro(0.50)
        laco = _laco(tmp_path, recolhe_quando_o_livro_anda=True)
        await laco.passo(
            [_janela()],
            livro_de=lambda token_id, *, agora_ns: up if token_id == "tok-up" else None,
            agora_epoch=1000.0, agora_ns=1,
        )
        aberta = laco.abertas["btc-updown-4h-1"]
        chave = ("btc-updown-4h-1", int(aberta.desde_epoch * 1e6))
        assert laco._referencia_do_recolher[chave] == (pytest.approx(0.49), None)

        def com_down(bid):
            down = OrderBook(asset_id="tok-down", bids=[(bid, 500.0)], asks=[(0.51, 500.0)])
            return lambda token_id, *, agora_ns: down if token_id == "tok-down" else up

        assert await laco.recolher_se_o_livro_andou(com_down(0.35), agora_ns=2) == []
        assert laco._referencia_do_recolher[chave][1] == pytest.approx(0.35)
        assert await laco.recolher_se_o_livro_andou(com_down(0.35), agora_ns=3) == []
        assert len(await laco.recolher_se_o_livro_andou(com_down(0.33), agora_ns=4)) == 1

    async def test_perna_down_sem_bids_nao_coloca_o_par_com_a_regra_ligada(self, tmp_path):
        """Down sem bid nenhum: o recolher tiraria o par no segundo seguinte e
        a passada de 15 s o poria de volta — coloca-e-recolhe sem fim (revisão
        do Codex, #126). Com a regra ligada não entra, com nome; desligada,
        entra como sempre (a linha de base não muda)."""
        up = _livro(0.50)
        down_sem_bids = OrderBook(asset_id="tok-down", bids=[], asks=[(0.51, 500.0)])

        def livro_de(token_id, *, agora_ns):
            return down_sem_bids if token_id == "tok-down" else up

        ligada = _laco(tmp_path, recolhe_quando_o_livro_anda=True)
        await ligada.passo([_janela()], livro_de=livro_de, agora_epoch=1000.0, agora_ns=1)
        assert ligada.abertas == {}
        assert ligada.motivos["perna_down_sem_bids"] == 1
        assert not ligada.cliente.repousadas

        desligada = _laco(tmp_path / "b")
        (tmp_path / "b").mkdir()
        await desligada.passo([_janela()], livro_de=livro_de, agora_epoch=1000.0, agora_ns=1)
        assert len(desligada.abertas) == 1

    async def test_o_reward_e_acertado_no_instante_do_print_antes_de_consumir_a_perna(
        self, tmp_path
    ):
        """Par colocado em t=1000, print que consome a perna Up em t=1005,
        recolhido em t=1010: 5 s de dois lados e 5 s de um lado. Sem acertar
        no print, o acerto do recolher aplicaria o restante de DEPOIS aos 10 s
        inteiros, e os 10 s pareceriam de um lado só (revisão do Codex, #126).
        Prova por comparação: o mesmo par com o print em t=1000,5 rende MENOS."""

        async def rodar(pasta, ts_do_print_s):
            pasta.mkdir()
            laco = await self._com_cotacao(pasta, recolhe_quando_o_livro_anda=True)
            print_ = SimpleNamespace(
                ts_ns=int(ts_do_print_s * 1e9), preco=0.47, tamanho=10.0,
                lado="SELL", token="tok-up",
            )
            laco._negocios_desde = lambda token_id, *, ts_ns: (
                [print_] if token_id == "tok-up" and print_.ts_ns > ts_ns else []
            )
            efeitos = await laco.recolher_se_o_livro_andou(
                _livro_de(_livro_com_bids((0.47, 500.0))), agora_ns=int(1010e9)
            )
            assert len(efeitos) == 1
            assert laco.caixa.execucoes_atravessadas == 1
            assert laco.caixa.acertos == 2  # no print e no recolher
            assert laco.caixa.segundos_repousando == pytest.approx(10.0)
            return laco.caixa.rewards_com_captura_usdc

        no_meio = await rodar(tmp_path / "a", 1005.0)
        no_comeco = await rodar(tmp_path / "b", 1000.5)
        assert no_meio > no_comeco > 0.0

    async def test_prints_das_duas_pernas_entram_em_ordem_de_tempo(self, tmp_path):
        """Down executa em t=1002 e Up em t=1005: perna a perna, o print do Up
        seria visto primeiro, o relógio iria a 1005 com as duas pernas ainda
        abertas, e o do Down (1002) seria descartado como tempo para trás —
        dois lados até 1005 em vez de um lado a partir de 1002 (revisão do
        Codex, #126). Em ordem de tempo há um acerto POR PRINT, em qualquer
        ordem de pernas — perna a perna, o segundo print era descartado."""

        async def rodar(pasta, ts_down_s, ts_up_s):
            pasta.mkdir()
            laco = await self._com_cotacao(pasta, recolhe_quando_o_livro_anda=True)
            prints = {
                "tok-down": SimpleNamespace(
                    ts_ns=int(ts_down_s * 1e9), preco=0.47, tamanho=10.0,
                    lado="SELL", token="tok-down",
                ),
                "tok-up": SimpleNamespace(
                    ts_ns=int(ts_up_s * 1e9), preco=0.47, tamanho=10.0,
                    lado="SELL", token="tok-up",
                ),
            }
            laco._negocios_desde = lambda token_id, *, ts_ns: (
                [prints[token_id]] if prints[token_id].ts_ns > ts_ns else []
            )
            efeitos = await laco.recolher_se_o_livro_andou(
                _livro_de(_livro_com_bids((0.47, 500.0))), agora_ns=int(1010e9)
            )
            assert len(efeitos) == 1
            assert laco.caixa.execucoes_atravessadas == 2
            # um acerto por print; o do recolher acha as duas pernas consumidas
            assert laco.caixa.acertos == 2
            assert laco.caixa.acertos_apos_execucao == 1
            assert laco.caixa.segundos_repousando == pytest.approx(5.0)
            assert laco.caixa.rewards_com_captura_usdc > 0.0

        await rodar(tmp_path / "a", 1002.0, 1005.0)  # Down primeiro
        await rodar(tmp_path / "b", 1005.0, 1002.0)  # Up primeiro

    async def test_so_o_livro_do_down_disparando_ainda_acerta_o_reward(self, tmp_path):
        """O livro do Up sumiu e o do Down andou contra: a saída dispara pela
        perna Down, e o reward dos 5 s repousando tem de contar mesmo assim —
        a caixa acerta no ESPELHO do livro do Down (bid = 1 − ask), o mesmo
        espelho da colocação. Sem isto, `_sair` apagava o intervalo inteiro
        (revisão do Codex, #126)."""
        laco = await self._com_cotacao(tmp_path, recolhe_quando_o_livro_anda=True)
        down_caiu = OrderBook(
            asset_id="tok-down", bids=[(0.47, 500.0)], asks=[(0.51, 500.0), (0.52, 500.0)]
        )

        def livro_de(token_id, *, agora_ns):
            return down_caiu if token_id == "tok-down" else None

        efeitos = await laco.recolher_se_o_livro_andou(livro_de, agora_ns=int(1005e9))
        assert len(efeitos) == 1 and laco.abertas == {}
        assert laco.motivos["livro_andou_contra"] == 1
        assert laco.caixa.acertos == 1
        assert laco.caixa.segundos_repousando == pytest.approx(5.0)

    async def test_referencia_e_tokens_saem_com_a_cotacao_por_qualquer_caminho(self, tmp_path):
        """Só o recolher apagava a sua referência; `janela_fechou`, `pool_sumiu`
        e a recotação deixavam a chave morta para sempre (revisão do Codex,
        #126). A referência é da COTAÇÃO: sai com ela, por qualquer porta."""
        laco = await self._com_cotacao(tmp_path, recolhe_quando_o_livro_anda=True)
        assert len(laco._referencia_do_recolher) == 1
        # a janela fechou: a cotação sai por `janela_fechou`
        await laco.passo([], livro_de=_livro_de(_livro(0.50)), agora_epoch=1001.0, agora_ns=2)
        assert laco.abertas == {}
        assert laco.motivos["janela_fechou"] == 1
        assert laco._referencia_do_recolher == {}
        assert laco._tokens == {} and laco._params == {}
        # de novo, agora trocada por recotação: fica só a chave da NOVA cotação
        await laco.passo(
            [_janela()], livro_de=_livro_de(_livro(0.50)), agora_epoch=1002.0, agora_ns=3
        )
        antiga = laco.abertas["btc-updown-4h-1"]
        await laco.passo(
            [_janela()], livro_de=_livro_de(_livro(0.70)), agora_epoch=1003.0, agora_ns=4
        )
        nova = laco.abertas["btc-updown-4h-1"]
        assert nova.desde_epoch != antiga.desde_epoch
        assert set(laco._referencia_do_recolher) == {
            ("btc-updown-4h-1", int(nova.desde_epoch * 1e6))
        }

    async def test_lado_de_bids_vazio_recolhe(self, tmp_path):
        laco = await self._com_cotacao(tmp_path, recolhe_quando_o_livro_anda=True)
        await laco.recolher_se_o_livro_andou(_livro_de(_livro(0.50)), agora_ns=2)
        vazio = OrderBook(asset_id="tok-up", bids=[], asks=[(0.51, 500.0)])
        assert len(await laco.recolher_se_o_livro_andou(_livro_de(vazio), agora_ns=3)) == 1
        assert laco.motivos["livro_andou_contra"] == 1

    async def test_confere_os_prints_do_intervalo_antes_de_sair(self, tmp_path):
        """Um SELL abaixo do nosso bid entre a passada e o recolher é execução
        — e sair apagaria o cursor da caixa antes de a passada seguinte olhar.
        A métrica que esta regra quer melhorar não pode sumir por causa dela."""
        laco = await self._com_cotacao(tmp_path, recolhe_quando_o_livro_anda=True)
        await laco.recolher_se_o_livro_andou(_livro_de(_livro(0.50)), agora_ns=2)
        print_ = SimpleNamespace(
            ts_ns=int(1005e9), preco=0.47, tamanho=10.0, lado="SELL", token="tok-up"
        )
        laco._negocios_desde = lambda token_id, *, ts_ns: (
            [print_] if token_id == "tok-up" and print_.ts_ns > ts_ns else []
        )
        efeitos = await laco.recolher_se_o_livro_andou(
            _livro_de(_livro_com_bids((0.47, 500.0))), agora_ns=int(1010e9)
        )
        assert len(efeitos) == 1
        assert laco.caixa.execucoes_atravessadas == 1

    async def test_em_live_o_gatilho_e_estar_sozinho_no_topo(self, tmp_path):
        """Com a nossa ordem no livro, o melhor bid nunca cai abaixo dela —
        ela VIRA o topo. Sozinha no nível (tamanho ≤ o nosso) = o mercado
        foi embora; acompanhada (500 no nível) = ainda há mercado ali."""
        laco = await self._com_cotacao(
            tmp_path, recolhe_quando_o_livro_anda=True, nossa_ordem_esta_no_livro=True
        )
        sozinhos = _livro_com_bids((0.49, 50.0), (0.47, 500.0))  # 50 = só a nossa
        acompanhados = _livro_com_bids((0.49, 550.0), (0.48, 500.0))
        await laco.recolher_se_o_livro_andou(_livro_de(acompanhados), agora_ns=2)  # referência
        assert await laco.recolher_se_o_livro_andou(_livro_de(acompanhados), agora_ns=3) == []
        assert len(laco.abertas) == 1
        efeitos = await laco.recolher_se_o_livro_andou(_livro_de(sozinhos), agora_ns=4)
        assert len(efeitos) == 1 and laco.abertas == {}
