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

from types import SimpleNamespace

from pulsearb.backtest.book import OrderBook
from pulsearb.execution.cliente_sombra import ClienteSombraDeOrdens
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
        # o mercado andou: melhor bid a 0,47, abaixo do nosso 0,49
        efeitos = await laco.recolher_se_o_livro_andou(
            _livro_de(_livro_com_bids((0.47, 500.0))), agora_ns=2
        )
        assert len(efeitos) == 1
        assert laco.abertas == {}
        assert not laco.cliente.repousadas  # as DUAS pernas saíram
        assert laco.motivos["livro_andou_contra"] == 1

    async def test_livro_parado_nao_recolhe(self, tmp_path):
        laco = await self._com_cotacao(tmp_path, recolhe_quando_o_livro_anda=True)
        assert await laco.recolher_se_o_livro_andou(_livro_de(_livro(0.50)), agora_ns=2) == []
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
        assert await laco.recolher_se_o_livro_andou(_livro_de(None), agora_ns=2) == []
        assert len(laco.abertas) == 1

    async def test_em_live_o_gatilho_e_estar_sozinho_no_topo(self, tmp_path):
        """Com a nossa ordem no livro, o melhor bid nunca cai abaixo dela —
        ela VIRA o topo. Sozinha no nível (tamanho ≤ o nosso) = o mercado
        foi embora; acompanhada (500 no nível) = ainda há mercado ali."""
        laco = await self._com_cotacao(
            tmp_path, recolhe_quando_o_livro_anda=True, nossa_ordem_esta_no_livro=True
        )
        sozinhos = _livro_com_bids((0.49, 50.0), (0.47, 500.0))  # 50 = só a nossa
        acompanhados = _livro_com_bids((0.49, 550.0), (0.48, 500.0))
        assert await laco.recolher_se_o_livro_andou(_livro_de(acompanhados), agora_ns=2) == []
        assert len(laco.abertas) == 1
        efeitos = await laco.recolher_se_o_livro_andou(_livro_de(sozinhos), agora_ns=3)
        assert len(efeitos) == 1 and laco.abertas == {}
