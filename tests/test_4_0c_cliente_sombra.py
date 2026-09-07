"""4.0(c) — o cliente que deixa a rota maker rodar em SHADOW sem enviar nada.

O teste que dá nome ao arquivo é o do mesmo caminho: `aplicar_decisao` e
`reconciliar` — as funções que rodariam em LIVE — têm de rodar contra este
cliente SEM NENHUMA alteração. Se o SHADOW precisasse de uma cópia da lógica,
uma divergência entre os dois pareceria diferença de mercado quando fosse
diferença de código, e é essa comparação que justifica o SHADOW existir.
"""

from __future__ import annotations

import pytest

from pulsearb.execution.cliente_sombra import (
    PREFIXO_DE_SOMBRA,
    ClienteSombraDeOrdens,
)
from pulsearb.live.cotacao import Cotacao
from pulsearb.live.execucao_maker import (
    ResultadoDaAcao,
    aplicar_decisao,
    reconciliar,
)
from pulsearb.live.repouso import AcaoNaCotacao, Decisao
from pulsearb.risk import OrdemPretendida
from pulsearb.settings import Mode


def _cliente(tmp_path):
    return ClienteSombraDeOrdens(caminho_do_diario=tmp_path / "diario.jsonl")


def _ordem_da_cotacao(cot: Cotacao) -> OrdemPretendida:
    return OrdemPretendida(
        slug="btc-updown-5m-1",
        token_id="tok-up",
        lado_up=True,
        shares=cot.tamanho,
        preco_limite=0.50,
    )


def _decisao_de_colocar(dist=2):
    return Decisao(
        AcaoNaCotacao.REPOSICIONAR,
        "ganho_justifica_perder_a_fila",
        nova=Cotacao(distancia_ticks=dist, tamanho=5.0),
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


class TestNuncaEnvia:
    def test_recusa_ser_construido_como_LIVE(self):
        """Cair para sombra quando pedem LIVE é a falha silenciosa mais cara:
        o operador acredita que opera e o dinheiro não se move."""
        with pytest.raises(ValueError, match="nunca roda como LIVE"):
            ClienteSombraDeOrdens(caminho_do_diario=None, modo=Mode.LIVE)

    def test_nao_tem_credencial_nem_transporte(self):
        """A garantia é estrutural, não disciplinar: não há aqui campo por onde
        uma credencial ou um socket entrem."""
        cliente = _cliente_sem_disco()

        assert not hasattr(cliente, "credenciais")
        assert not hasattr(cliente, "transporte")

    def test_o_order_id_e_reconhecivel_como_sombra(self, tmp_path):
        """Um id de sombra que vazasse para caminho real seria recusado pelo
        servidor em vez de casar por acidente."""
        import asyncio

        cliente = _cliente(tmp_path)
        r = asyncio.run(cliente.enviar(_ordem_da_cotacao(Cotacao(2, 5.0)), janela="j1"))

        assert r.order_id.startswith(PREFIXO_DE_SOMBRA)


def _cliente_sem_disco():
    from pathlib import Path

    return ClienteSombraDeOrdens(caminho_do_diario=Path("/dev/null"))


class TestMesmoCaminho:
    """O teste que dá nome ao arquivo."""

    async def test_aplicar_decisao_roda_SEM_ALTERACAO_contra_a_sombra(self, tmp_path):
        cliente = _cliente(tmp_path)

        efeito = await _aplicar(_decisao_de_colocar(), None, cliente)

        assert efeito.resultado is ResultadoDaAcao.COLOCADA
        assert efeito.aberta.order_id.startswith(PREFIXO_DE_SOMBRA)
        assert len(cliente.repousadas) == 1

    async def test_reposicionar_cancela_a_antiga_e_poe_a_nova(self, tmp_path):
        cliente = _cliente(tmp_path)

        primeiro = await _aplicar(_decisao_de_colocar(dist=2), None, cliente)
        segundo = await _aplicar(
            _decisao_de_colocar(dist=3), primeiro.aberta, cliente
        )

        assert segundo.resultado is ResultadoDaAcao.REPOSICIONADA
        # A antiga saiu do livro; só a nova repousa.
        assert len(cliente.repousadas) == 1
        assert segundo.aberta.order_id in cliente.repousadas
        assert primeiro.aberta.order_id not in cliente.repousadas

    async def test_cancelar_tira_do_livro(self, tmp_path):
        cliente = _cliente(tmp_path)
        colocada = await _aplicar(_decisao_de_colocar(), None, cliente)

        efeito = await _aplicar(
            Decisao(AcaoNaCotacao.CANCELAR, "atual_nao_pontua_mais"),
            colocada.aberta,
            cliente,
        )

        assert efeito.resultado is ResultadoDaAcao.CANCELADA
        assert cliente.repousadas == {}

    async def test_reconciliar_roda_SEM_ALTERACAO_e_ve_o_que_repousa(self, tmp_path):
        cliente = _cliente(tmp_path)
        colocada = await _aplicar(_decisao_de_colocar(), None, cliente)
        aberta = colocada.aberta

        rec = await reconciliar(cliente, {aberta.order_id: aberta})

        assert rec.limpa is True
        assert rec.casadas == (aberta.order_id,)

    async def test_reconciliar_acha_ORFA_que_o_nosso_lado_esqueceu(self, tmp_path):
        """O caso real: a cotação entrou e o nosso lado perdeu o registro."""
        cliente = _cliente(tmp_path)
        await _aplicar(_decisao_de_colocar(), None, cliente)

        rec = await reconciliar(cliente, {})

        assert len(rec.orfas) == 1
        assert rec.limpa is False


class TestODiario:
    async def test_colocar_e_cancelar_deixam_rastro_no_MESMO_arquivo(self, tmp_path):
        """Dois arquivos obrigariam a intercalar por carimbo depois — que é
        onde uma reconstrução erra."""
        import json

        cliente = _cliente(tmp_path)
        colocada = await _aplicar(_decisao_de_colocar(), None, cliente)
        await _aplicar(
            Decisao(AcaoNaCotacao.CANCELAR, "atual_nao_pontua_mais"),
            colocada.aberta,
            cliente,
        )

        linhas = [
            json.loads(x)
            for x in (tmp_path / "diario.jsonl").read_text().splitlines()
            if x.strip()
        ]
        eventos = [linha["evento"] for linha in linhas]
        assert eventos == ["cotacao_colocada", "cotacao_cancelada"]
        assert all(linha["sombra"] is True for linha in linhas)

    async def test_nao_simula_preenchimento(self, tmp_path):
        """Fingir preenchimento exigiria a posição na fila que não se observa,
        e o número entraria no diário com cara de medida."""
        cliente = _cliente(tmp_path)
        await _aplicar(_decisao_de_colocar(), None, cliente)

        (aberta,) = await cliente.listar_ordens_abertas()
        assert aberta.size_matched == 0.0
