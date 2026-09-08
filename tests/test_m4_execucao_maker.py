"""4.0(c) — o I/O que liga a decisão do `repouso` ao `cliente`.

O teste que dá nome ao arquivo é o do reposicionamento com cancelamento
incerto: se ele enviasse a nova ordem por cima da antiga que talvez ainda
repouse, seriam duas cotações maker no livro com o portão autorizando uma —
a posição dupla, agora entre dois makers.
"""

from __future__ import annotations

import pytest

from pulsearb.execution.cliente import (
    MOTIVOS_DE_RECUSA,
    ClienteDeOrdens,
    ErroDeTransporte,
)
from pulsearb.live.cotacao import Cotacao
from pulsearb.live.execucao_maker import (
    Efeito,
    ResultadoDaAcao,
    aplicar_decisao,
)
from pulsearb.live.repouso import AcaoNaCotacao, CotacaoAberta, Decisao
from pulsearb.risk import OrdemPretendida

CREDENCIAIS_KW = dict(
    api_key="chave", segredo="c2VncmVkbw==", passphrase="frase", endereco="0xabc"
)


class _Construtor:
    def corpo_da_ordem(self, ordem, *, id_do_cliente):
        return {"tokenId": ordem.token_id, "clientId": id_do_cliente}


class _Transporte:
    def __init__(self, *respostas):
        self.respostas = list(respostas)
        self.chamadas: list[tuple[str, str, dict, bytes]] = []

    async def __call__(self, metodo, caminho, cabecalhos, corpo):
        self.chamadas.append((metodo, caminho, cabecalhos, corpo))
        resp = self.respostas.pop(0) if self.respostas else (200, {"success": True})
        if isinstance(resp, Exception):
            raise resp
        return resp


def _cliente(*respostas):
    from pulsearb.execution.auth import CredenciaisL2

    return ClienteDeOrdens(
        CredenciaisL2(**CREDENCIAIS_KW), _Construtor(), _Transporte(*respostas)
    )


def _cotacao(dist=2, tam=5.0):
    return Cotacao(distancia_ticks=dist, tamanho=tam)


def _ordem_da_cotacao(cot: Cotacao) -> OrdemPretendida:
    # Dublê do mapeamento cotação→ordem que quem chama fornece.
    return OrdemPretendida(
        slug="btc-updown-5m-1",
        token_id="tok-up",
        lado_up=True,
        shares=cot.tamanho,
        preco_limite=0.50,
    )


def _aberta(order_id="o-antiga", dist=2):
    return CotacaoAberta(
        cotacao=_cotacao(dist=dist),
        desde_epoch=1000.0,
        id_do_cliente="c-antiga",
        order_id=order_id,
    )


async def _aplicar(decisao, aberta, cliente):
    return await aplicar_decisao(
        decisao,
        aberta,
        cliente=cliente,
        ordem_da_cotacao=_ordem_da_cotacao,
        janela="j1",
        agora_epoch=2000.0,
    )


class TestManter:
    async def test_manter_nao_toca_a_rede(self):
        cliente = _cliente()
        aberta = _aberta()

        efeito = await _aplicar(Decisao(AcaoNaCotacao.MANTER, "estavel"), aberta, cliente)

        assert efeito.resultado is ResultadoDaAcao.MANTIDA
        assert efeito.aberta is aberta
        assert len(cliente.transporte.chamadas) == 0


class TestCancelar:
    async def test_cancelar_tira_do_livro_e_zera_o_aberto(self):
        cliente = _cliente((200, {"canceled": ["o-antiga"], "not_canceled": {}}))

        efeito = await _aplicar(
            Decisao(AcaoNaCotacao.CANCELAR, "atual_nao_pontua_mais"), _aberta(), cliente
        )

        assert efeito.resultado is ResultadoDaAcao.CANCELADA
        assert efeito.aberta is None
        metodo, caminho, _, _ = cliente.transporte.chamadas[0]
        assert metodo == "DELETE" and caminho == "/order"

    async def test_cancelar_incerto_pede_reconciliacao_e_MANTEM_o_aberto(self):
        """A ordem pode ainda repousar: não a damos por fechada, para a
        reconciliação ter o que procurar."""
        cliente = _cliente(ErroDeTransporte("timeout"))
        aberta = _aberta()

        efeito = await _aplicar(
            Decisao(AcaoNaCotacao.CANCELAR, "atual_nao_pontua_mais"), aberta, cliente
        )

        assert efeito.resultado is ResultadoDaAcao.RECONCILIAR
        assert efeito.precisa_reconciliar is True
        assert efeito.aberta is aberta

    async def test_cancelar_sem_order_id_e_reconciliacao_nao_cancelamento_vazio(self):
        """Sem id do servidor não se manda DELETE vazio para o fio — a ordem
        pode existir sob um id que nunca capturamos, e isso é reconciliação."""
        cliente = _cliente()
        aberta = _aberta(order_id="")

        efeito = await _aplicar(
            Decisao(AcaoNaCotacao.CANCELAR, "atual_nao_pontua_mais"), aberta, cliente
        )

        assert efeito.resultado is ResultadoDaAcao.RECONCILIAR
        assert efeito.motivo == "aberta_sem_order_id"
        assert len(cliente.transporte.chamadas) == 0


class TestReposicionar:
    async def test_reposicionar_cancela_a_antiga_e_coloca_a_nova(self):
        cliente = _cliente(
            (200, {"canceled": ["o-antiga"], "not_canceled": {}}),  # cancelar
            (200, {"success": True, "orderID": "o-nova"}),  # enviar
        )

        efeito = await _aplicar(
            Decisao(
                AcaoNaCotacao.REPOSICIONAR,
                "ganho_justifica_perder_a_fila",
                nova=_cotacao(dist=3),
                ganho_estimado_usdc=0.8,
            ),
            _aberta(),
            cliente,
        )

        assert efeito.resultado is ResultadoDaAcao.REPOSICIONADA
        assert efeito.aberta is not None
        assert efeito.aberta.order_id == "o-nova"
        assert efeito.aberta.cotacao.distancia_ticks == 3
        # Ordem dos dois passos: DELETE antes de POST.
        metodos = [c[0] for c in cliente.transporte.chamadas]
        assert metodos == ["DELETE", "POST"]

    async def test_reposicionar_com_cancelamento_INCERTO_NAO_envia_a_nova(self):
        """O teste que dá nome ao arquivo. Antiga incerta + nova enviada =
        duas cotações no livro. Tem de parar no cancelamento."""
        cliente = _cliente(ErroDeTransporte("timeout no cancelamento"))

        efeito = await _aplicar(
            Decisao(
                AcaoNaCotacao.REPOSICIONAR,
                "ganho_justifica_perder_a_fila",
                nova=_cotacao(dist=3),
            ),
            _aberta(),
            cliente,
        )

        assert efeito.resultado is ResultadoDaAcao.RECONCILIAR
        assert efeito.motivo == "cancelamento_incerto"
        # SÓ o cancelamento saiu; a nova NÃO foi enviada.
        assert len(cliente.transporte.chamadas) == 1
        assert cliente.transporte.chamadas[0][0] == "DELETE"

    async def test_reposicionar_sem_nada_aberto_e_COLOCADA(self):
        cliente = _cliente((200, {"success": True, "orderID": "o-nova"}))

        efeito = await _aplicar(
            Decisao(
                AcaoNaCotacao.REPOSICIONAR,
                "ganho_justifica_perder_a_fila",
                nova=_cotacao(dist=3),
            ),
            None,
            cliente,
        )

        assert efeito.resultado is ResultadoDaAcao.COLOCADA
        assert efeito.aberta.order_id == "o-nova"
        assert [c[0] for c in cliente.transporte.chamadas] == ["POST"]

    async def test_envio_da_nova_INCERTO_pede_reconciliacao(self):
        """A antiga já saiu; a nova pode ter entrado. Reconciliar com o id do
        cliente como pista."""
        cliente = _cliente(
            (200, {"canceled": ["o-antiga"], "not_canceled": {}}),  # cancelar ok
            ErroDeTransporte("timeout no envio"),  # enviar incerto
        )

        efeito = await _aplicar(
            Decisao(
                AcaoNaCotacao.REPOSICIONAR,
                "ganho_justifica_perder_a_fila",
                nova=_cotacao(dist=3),
            ),
            _aberta(),
            cliente,
        )

        assert efeito.resultado is ResultadoDaAcao.RECONCILIAR
        assert efeito.motivo == "envio_incerto"
        assert efeito.aberta is not None
        assert efeito.aberta.id_do_cliente  # a pista para procurar

    async def test_envio_da_nova_RECUSADO_deixa_o_livro_sem_cotacao(self):
        cliente = _cliente(
            (200, {"canceled": ["o-antiga"], "not_canceled": {}}),  # cancelar ok
            (400, {"error": "tick invalido"}),  # enviar recusado
        )

        efeito = await _aplicar(
            Decisao(
                AcaoNaCotacao.REPOSICIONAR,
                "ganho_justifica_perder_a_fila",
                nova=_cotacao(dist=3),
            ),
            _aberta(),
            cliente,
        )

        assert efeito.resultado is ResultadoDaAcao.CANCELADA
        assert efeito.aberta is None
        assert efeito.detalhe["motivo_da_recusa"] == MOTIVOS_DE_RECUSA.SERVIDOR_RECUSOU


class TestEfeito:
    def test_precisa_reconciliar_so_no_estado_reconciliar(self):
        assert Efeito(ResultadoDaAcao.RECONCILIAR, "x").precisa_reconciliar is True
        assert Efeito(ResultadoDaAcao.MANTIDA, "x").precisa_reconciliar is False


class TestReconciliar:
    """Casar o servidor com o que achávamos que repousava. Fail-closed."""

    def _servidor(self, *ids_ou_ordens):
        from pulsearb.execution.cliente import OrdemAberta

        respostas_data = []
        for x in ids_ou_ordens:
            if isinstance(x, OrdemAberta):
                respostas_data.append(
                    {"id": x.id, "asset_id": x.token_id, "size_matched": x.size_matched}
                )
            else:
                respostas_data.append({"id": x})
        return _cliente((200, {"data": respostas_data, "next_cursor": "LTE="}))

    async def test_tudo_casado_e_limpa(self):
        from pulsearb.live.execucao_maker import reconciliar

        cliente = self._servidor("o1", "o2")
        esperadas = {
            "o1": _aberta(order_id="o1"),
            "o2": _aberta(order_id="o2"),
        }

        rec = await reconciliar(cliente, esperadas)

        assert rec.limpa is True
        assert set(rec.casadas) == {"o1", "o2"}

    async def test_ordem_no_servidor_que_nao_esperavamos_e_ORFA(self):
        """O caso que a reconciliação existe para pegar: um envio que ficou
        INCERTA e afinal entrou. Posição real que ninguém gerencia."""
        from pulsearb.live.execucao_maker import reconciliar

        cliente = self._servidor("o1", "orfa-x")
        esperadas = {"o1": _aberta(order_id="o1")}

        rec = await reconciliar(cliente, esperadas)

        assert rec.limpa is False
        assert [o.id for o in rec.orfas] == ["orfa-x"]

    async def test_ordem_que_esperavamos_e_sumiu_e_FANTASMA(self):
        from pulsearb.live.execucao_maker import reconciliar

        cliente = self._servidor("o1")
        esperadas = {"o1": _aberta(order_id="o1"), "o2": _aberta(order_id="o2")}

        rec = await reconciliar(cliente, esperadas)

        assert rec.fantasmas == ("o2",)
        assert rec.limpa is False

    async def test_falha_de_leitura_SOBE_nao_vira_livro_limpo(self):
        """Fail-closed: um timeout na leitura nao pode virar 'nada aberto'."""
        from pulsearb.execution.cliente import ErroDeLeitura
        from pulsearb.live.execucao_maker import reconciliar

        cliente = _cliente(ErroDeTransporte("timeout"))

        with pytest.raises(ErroDeLeitura):
            await reconciliar(cliente, {"o1": _aberta(order_id="o1")})

    async def test_cancelar_orfas_cancela_cada_uma(self):
        from pulsearb.execution.cliente import OrdemAberta
        from pulsearb.live.execucao_maker import Reconciliacao, cancelar_orfas

        cliente = _cliente(
            (200, {"canceled": ["orfa-x"], "not_canceled": {}}),
            (200, {"canceled": ["orfa-y"], "not_canceled": {}}),
        )
        rec = Reconciliacao(
            orfas=(
                OrdemAberta("orfa-x", "tok", "BUY", 0.5, 5.0, 0.0, "LIVE"),
                OrdemAberta("orfa-y", "tok", "BUY", 0.5, 5.0, 0.0, "LIVE"),
            )
        )

        desfechos = await cancelar_orfas(cliente, rec)

        assert set(desfechos) == {"orfa-x", "orfa-y"}
        metodos = [c[0] for c in cliente.transporte.chamadas]
        assert metodos == ["DELETE", "DELETE"]
