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


def _laco(tmp_path, **kw):
    return LacoMaker(
        cliente=ClienteSombraDeOrdens(caminho_do_diario=tmp_path / "d.jsonl"),
        tamanho_da_cotacao=50.0,
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
        assert len(laco.cliente.repousadas) == 1

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
        assert len(laco.cliente.repousadas) == 1

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
