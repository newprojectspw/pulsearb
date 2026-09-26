"""Endurecimento da camada de execução (auditoria de 2026-09-26).

Cada classe aqui prende um defeito achado na auditoria, com o teste que
FALHAVA antes da correção:

- F1  — `NAO_CANCELADA` era lido como "saiu do livro", e o REPOSICIONAR
        seguia para o POST com a antiga possivelmente repousando.
- F5  — `listar_ordens_abertas` devolvia lista PARCIAL ao estourar o teto de
        páginas.
- F7  — `size_matched` ilegível virava 0.0 ("intacta") e a órfã sem id era
        pulada em silêncio.
- F10 — uma exceção depois de o Up ser aceito levava embora a referência ao Up.
- N1  — os motivos do `Efeito` eram literais soltos, sem enumeração.

Os símbolos NOVOS (`MOTIVOS_DO_EFEITO`, `fora_do_livro`, …) são importados
dentro dos testes que os usam, para que os testes de COMPORTAMENTO falhem por
asserção — e não por `ImportError` — quando rodados contra o código antigo.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

import pytest

from pulsearb.execution.cliente import (
    MAX_PAGINAS_DE_ORDENS,
    ClienteDeOrdens,
    ErroDeLeitura,
    ErroDeTransporte,
    EstadoDoCancelamento,
    OrdemAberta,
    ResultadoDoCancelamento,
)
from pulsearb.live.cotacao import Cotacao
from pulsearb.live.execucao_maker import (
    Reconciliacao,
    ResultadoDaAcao,
    aplicar_decisao,
    cancelar_orfas,
    reconciliar,
)
from pulsearb.live.repouso import AcaoNaCotacao, CotacaoAberta, Decisao
from pulsearb.risk import OrdemPretendida

LOGGER_DA_EXECUCAO = "pulsearb.live.execucao_maker"
FONTE_DA_EXECUCAO = (
    Path(__file__).resolve().parents[1] / "src" / "pulsearb" / "live" / "execucao_maker.py"
)


# ───────────────────────────────────────────────────────────── dublês
class _Construtor:
    def corpo_da_ordem(self, ordem, *, id_do_cliente):
        return {"tokenId": ordem.token_id, "clientId": id_do_cliente}


class _Transporte:
    """Fila de respostas; exceção na fila é levantada. Fila vazia = falha do
    teste (nenhum default silencioso que pareça resposta do servidor)."""

    def __init__(self, *respostas):
        self.respostas = list(respostas)
        self.chamadas: list[tuple[str, str, dict, bytes]] = []

    async def __call__(self, metodo, caminho, cabecalhos, corpo):
        self.chamadas.append((metodo, caminho, cabecalhos, corpo))
        if not self.respostas:
            raise AssertionError(f"chamada inesperada ao transporte: {metodo} {caminho}")
        resp = self.respostas.pop(0)
        if isinstance(resp, BaseException):
            raise resp
        return resp


def _cliente(*respostas) -> ClienteDeOrdens:
    from pulsearb.execution.auth import CredenciaisL2

    return ClienteDeOrdens(
        CredenciaisL2(
            api_key="chave", segredo="c2VncmVkbw==", passphrase="frase", endereco="0xabc"
        ),
        _Construtor(),
        _Transporte(*respostas),
    )


def _metodos(cliente) -> list[str]:
    return [c[0] for c in cliente.transporte.chamadas]


def _ordem_up(cot: Cotacao) -> OrdemPretendida:
    return OrdemPretendida(
        slug="btc-updown-5m-1", token_id="tok-up", lado_up=True,
        shares=cot.tamanho, preco_limite=0.50,
    )


def _ordem_down(cot: Cotacao) -> OrdemPretendida:
    return OrdemPretendida(
        slug="btc-updown-5m-1", token_id="tok-down", lado_up=False,
        shares=cot.tamanho, preco_limite=0.48,
    )


def _aberta(order_id="o-antiga") -> CotacaoAberta:
    return CotacaoAberta(
        cotacao=Cotacao(distancia_ticks=2, tamanho=5.0, dois_lados=False),
        desde_epoch=1000.0,
        id_do_cliente="c-antiga",
        order_id=order_id,
    )


def _reposicionar(dois_lados=False) -> Decisao:
    return Decisao(
        AcaoNaCotacao.REPOSICIONAR,
        "ganho_justifica_perder_a_fila",
        nova=Cotacao(distancia_ticks=3, tamanho=5.0, dois_lados=dois_lados),
        ganho_estimado_usdc=0.8,
    )


async def _aplicar(decisao, aberta, cliente, *, com_down=False, ordem_down=_ordem_down):
    return await aplicar_decisao(
        decisao,
        aberta,
        cliente=cliente,
        ordem_da_cotacao=_ordem_up,
        ordem_do_lado_down=ordem_down if com_down else None,
        janela="j1",
        agora_epoch=2000.0,
    )


def _aceita(order_id):
    return (200, {"success": True, "orderID": order_id, "status": "live"})


def _mensagens(caplog, nivel=logging.WARNING) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == LOGGER_DA_EXECUCAO and r.levelno >= nivel
    ]


# ─────────────────────────────────────────────────────────────────── F1
#: Respostas do DELETE em que o servidor RESPONDEU e NÃO provou que o id saiu.
#: A última é a que a §4.4 descreve como "já não existe" — mas sem string
#: VERIFICADA do motivo, ela não se distingue de "existe e não cancelei".
RESPOSTAS_SEM_PROVA = [
    pytest.param((401, None), id="401"),
    pytest.param((403, {"error": "forbidden"}), id="403"),
    pytest.param((400, {"error": "qualquer"}), id="4xx_generico"),
    pytest.param((200, None), id="200_corpo_ilegivel"),
    pytest.param((200, {"success": True}), id="200_sem_mencionar_o_id"),
    pytest.param((200, {"canceled": [], "not_canceled": {}}), id="200_listas_vazias"),
    pytest.param(
        (200, {"canceled": ["outra"], "not_canceled": {}}), id="200_cancelou_outro_id"
    ),
    pytest.param(
        (200, {"canceled": [], "not_canceled": {"o-antiga": "order not found"}}),
        id="200_id_em_not_canceled_motivo_nao_verificado",
    ),
]


class TestF1CancelamentoSoSaiDoLivroComProva:
    @pytest.mark.parametrize("resposta_do_delete", RESPOSTAS_SEM_PROVA)
    async def test_REPOSICIONAR_para_no_DELETE_sem_prova_e_NAO_envia_a_nova(
        self, resposta_do_delete
    ):
        """O defeito: a nova ia para o livro por cima de uma antiga que o
        servidor NÃO disse ter cancelado — posição dupla entre dois makers."""
        cliente = _cliente(resposta_do_delete, _aceita("o-nova"))
        aberta = _aberta()

        efeito = await _aplicar(_reposicionar(), aberta, cliente)

        assert _metodos(cliente) == ["DELETE"]  # nenhum POST
        assert efeito.resultado is ResultadoDaAcao.RECONCILIAR
        assert efeito.motivo == "cancelamento_nao_confirmado"
        assert efeito.aberta is aberta  # mantida para a reconciliação
        assert efeito.detalhe["perna"] == "up"
        assert efeito.detalhe["order_id"] == "o-antiga"

    async def test_CANCELAR_com_401_NAO_da_a_cotacao_por_fechada(self):
        cliente = _cliente((401, None))
        aberta = _aberta()

        efeito = await _aplicar(Decisao(AcaoNaCotacao.CANCELAR, "estavel"), aberta, cliente)

        assert efeito.resultado is ResultadoDaAcao.RECONCILIAR
        assert efeito.motivo == "cancelamento_nao_confirmado"
        assert efeito.aberta is aberta

    async def test_duas_pernas_Down_sem_prova_para_e_mantem_a_cotacao(self):
        cliente = _cliente(
            (200, {"canceled": ["o-up"], "not_canceled": {}}),
            (200, {"canceled": [], "not_canceled": {}}),
        )
        aberta = CotacaoAberta(
            cotacao=Cotacao(2, 5.0),
            desde_epoch=1000.0,
            id_do_cliente="c-up",
            order_id="o-up",
            id_do_cliente_down="c-down",
            order_id_down="o-down",
        )

        efeito = await _aplicar(
            Decisao(AcaoNaCotacao.CANCELAR, "estavel"), aberta, cliente, com_down=True
        )

        assert efeito.resultado is ResultadoDaAcao.RECONCILIAR
        assert efeito.motivo == "cancelamento_nao_confirmado"
        assert efeito.detalhe["perna"] == "down"
        assert efeito.aberta is aberta

    async def test_prova_legitima_id_em_canceled_continua_liberando(self):
        cliente = _cliente(
            (200, {"canceled": ["o-antiga"], "not_canceled": {}}), _aceita("o-nova")
        )

        efeito = await _aplicar(_reposicionar(), _aberta(), cliente)

        assert efeito.resultado is ResultadoDaAcao.REPOSICIONADA
        assert efeito.aberta.order_id == "o-nova"
        assert _metodos(cliente) == ["DELETE", "POST"]

    async def test_prova_legitima_da_sombra_id_que_nao_repousava_continua_liberando(
        self, tmp_path
    ):
        """No SHADOW o dicionário do cliente É o livro: id ausente de lá está
        fora do livro sem margem. O mesmo caminho tem de seguir para a nova."""
        from pulsearb.execution.cliente_sombra import ClienteSombraDeOrdens

        sombra = ClienteSombraDeOrdens(caminho_do_diario=tmp_path / "diario.jsonl")

        efeito = await _aplicar(_reposicionar(), _aberta("sombra-9-desconhecido"), sombra)

        assert efeito.resultado is ResultadoDaAcao.REPOSICIONADA
        assert efeito.aberta is not None
        assert efeito.aberta.order_id in sombra.repousadas

    async def test_o_motivo_da_sombra_e_o_que_o_cliente_reconhece_como_prova(
        self, tmp_path
    ):
        """O `cliente_sombra.py` escreve o motivo como literal; se ele divergir
        da constante, a sombra passaria a parar em todo id desconhecido — e o
        SHADOW deixaria de exercitar o mesmo caminho do LIVE."""
        from pulsearb.execution.cliente import MOTIVO_ID_NAO_REPOUSAVA
        from pulsearb.execution.cliente_sombra import ClienteSombraDeOrdens

        sombra = ClienteSombraDeOrdens(caminho_do_diario=tmp_path / "diario.jsonl")
        r = await sombra.cancelar("nunca-existiu")

        assert r.motivo == MOTIVO_ID_NAO_REPOUSAVA
        assert r.fora_do_livro is True

    def test_fora_do_livro_so_com_prova(self):
        from pulsearb.execution.cliente import MOTIVO_ID_NAO_REPOUSAVA, MOTIVOS_DE_RECUSA

        def r(estado, motivo=None):
            return ResultadoDoCancelamento(estado=estado, order_id="o", motivo=motivo)

        assert r(EstadoDoCancelamento.CANCELADA).fora_do_livro is True
        assert (
            r(EstadoDoCancelamento.NAO_CANCELADA, MOTIVO_ID_NAO_REPOUSAVA).fora_do_livro
            is True
        )
        assert (
            r(EstadoDoCancelamento.NAO_CANCELADA, MOTIVOS_DE_RECUSA.SERVIDOR_RECUSOU)
            .fora_do_livro is False
        )
        assert (
            r(EstadoDoCancelamento.NAO_CANCELADA, MOTIVOS_DE_RECUSA.AUTH_RECUSADA)
            .fora_do_livro is False
        )
        assert r(EstadoDoCancelamento.INCERTA).fora_do_livro is False
        # O motivo de prova só vale com o estado certo.
        assert r(EstadoDoCancelamento.INCERTA, MOTIVO_ID_NAO_REPOUSAVA).fora_do_livro is False

    async def test_cliente_real_nunca_produz_o_motivo_de_prova_da_sombra(self):
        """O motivo de prova é da sombra. Mesmo com o servidor escrevendo
        exatamente essa string em `not_canceled`, o cliente real a guarda no
        detalhe, não no `motivo` — então ela não vira prova por acidente."""
        cliente = _cliente(
            (200, {"canceled": [], "not_canceled": {"o1": "id_nao_repousava"}})
        )

        r = await cliente.cancelar("o1")

        assert r.estado is EstadoDoCancelamento.NAO_CANCELADA
        assert r.fora_do_livro is False


# ─────────────────────────────────────────────────────────────────── F5
class TestF5ListagemNaoDevolveListaParcial:
    async def test_cursor_que_nunca_termina_LEVANTA_em_vez_de_lista_parcial(self):
        respostas = [
            (200, {"data": [{"id": f"o{n}"}], "next_cursor": f"c{n}"})
            for n in range(MAX_PAGINAS_DE_ORDENS + 5)
        ]
        cliente = _cliente(*respostas)

        with pytest.raises(ErroDeLeitura, match="nao terminou"):
            await cliente.listar_ordens_abertas()

        assert len(cliente.transporte.chamadas) == MAX_PAGINAS_DE_ORDENS

    async def test_teto_exato_com_sentinela_na_ultima_pagina_continua_valendo(self):
        respostas = [
            (200, {"data": [{"id": f"o{n}"}], "next_cursor": f"c{n}"})
            for n in range(MAX_PAGINAS_DE_ORDENS - 1)
        ] + [(200, {"data": [{"id": "ultima"}], "next_cursor": "LTE="})]
        cliente = _cliente(*respostas)

        abertas = await cliente.listar_ordens_abertas()

        assert len(abertas) == MAX_PAGINAS_DE_ORDENS
        assert abertas[-1].id == "ultima"

    async def test_reconciliar_com_listagem_sem_fim_SOBE(self):
        respostas = [
            (200, {"data": [], "next_cursor": f"c{n}"})
            for n in range(MAX_PAGINAS_DE_ORDENS)
        ]
        cliente = _cliente(*respostas)

        with pytest.raises(ErroDeLeitura):
            await reconciliar(cliente, {"o1": _aberta("o1")})


# ─────────────────────────────────────────────────────────────────── F7
class TestF7CampoIlegivelNaoViraZeroNemSome:
    @pytest.mark.parametrize(
        "valor", [None, "", "abc", "nan", "inf", {"x": 1}], ids=repr
    )
    def test_size_matched_ilegivel_e_DESCONHECIDO_nao_zero(self, valor):
        item = {"id": "o1"} if valor is None else {"id": "o1", "size_matched": valor}

        assert OrdemAberta.do_payload(item).size_matched is None

    @pytest.mark.parametrize(("valor", "esperado"), [("0", 0.0), ("2.5", 2.5), (3, 3.0)])
    def test_size_matched_legivel_continua_numero(self, valor, esperado):
        assert OrdemAberta.do_payload({"id": "o1", "size_matched": valor}).size_matched == (
            esperado
        )

    async def test_orfa_com_preenchimento_desconhecido_avisa_e_e_cancelada(self, caplog):
        caplog.set_level(logging.WARNING, logger=LOGGER_DA_EXECUCAO)
        cliente = _cliente((200, {"canceled": ["orfa-x"], "not_canceled": {}}))
        rec = Reconciliacao(
            orfas=(OrdemAberta("orfa-x", "tok", "BUY", 0.5, 5.0, None, "LIVE"),)
        )

        desfechos = await cancelar_orfas(cliente, rec)

        assert desfechos == {"orfa-x": "cancelada"}
        assert any("DESCONHECIDO" in m for m in _mensagens(caplog))

    async def test_orfa_sem_id_e_CONTADA_e_LOGADA_nunca_silenciosa(self, caplog):
        caplog.set_level(logging.WARNING, logger=LOGGER_DA_EXECUCAO)
        cliente = _cliente()  # nenhum DELETE pode sair: não há id
        rec = Reconciliacao(
            orfas=(OrdemAberta("", "tok", "BUY", 0.5, 5.0, 2.0, "LIVE"),)
        )

        desfechos = await cancelar_orfas(cliente, rec)

        assert rec.orfas_sem_id == 1
        assert desfechos == {}
        assert cliente.transporte.chamadas == []
        erros = _mensagens(caplog, logging.ERROR)
        assert any("sem id" in m for m in erros)
        # E o aviso de "meio preenchida" não se perde com ela.
        assert any("meio preenchida" in m for m in _mensagens(caplog))

    async def test_reconciliar_conta_e_loga_a_orfa_sem_id(self, caplog):
        caplog.set_level(logging.WARNING, logger=LOGGER_DA_EXECUCAO)
        cliente = _cliente(
            (200, {"data": [{"id": "o1"}, {"asset_id": "tok"}], "next_cursor": "LTE="})
        )

        rec = await reconciliar(cliente, {"o1": _aberta("o1")})

        assert rec.orfas_sem_id == 1
        assert rec.limpa is False
        assert any("sem id" in m for m in _mensagens(caplog, logging.ERROR))


# ────────────────────────────────────────────────────────────────── F10
class TestF10ExcecaoDepoisDoUpAceitoNaoPerdeOUp:
    async def test_Down_levanta_RuntimeError_apos_Up_ACEITA_vira_RECONCILIAR_com_o_Up(
        self, caplog
    ):
        caplog.set_level(logging.ERROR, logger=LOGGER_DA_EXECUCAO)
        cliente = _cliente(_aceita("o-up"), RuntimeError("transporte quebrou"))

        efeito = await _aplicar(_reposicionar(dois_lados=True), None, cliente, com_down=True)

        assert efeito.resultado is ResultadoDaAcao.RECONCILIAR
        assert efeito.motivo == "excecao_na_segunda_perna"
        assert efeito.aberta is not None
        assert efeito.aberta.order_id == "o-up"
        assert "RuntimeError" in efeito.detalhe["erro"]
        # Não engolida: o erro sai no log.
        assert _mensagens(caplog, logging.ERROR)

    async def test_montar_o_Down_levanta_apos_Up_ACEITA_vira_RECONCILIAR_com_o_Up(self):
        def _down_quebrado(cot):
            raise ValueError("tick desconhecido")

        cliente = _cliente(_aceita("o-up"))

        efeito = await _aplicar(
            _reposicionar(dois_lados=True), None, cliente,
            com_down=True, ordem_down=_down_quebrado,
        )

        assert efeito.resultado is ResultadoDaAcao.RECONCILIAR
        assert efeito.motivo == "excecao_na_segunda_perna"
        assert efeito.aberta.order_id == "o-up"
        assert _metodos(cliente) == ["POST"]


# ─────────────────────────────────────────────────────────────────── N1
async def _todo_motivo_produzido() -> set[str]:
    """Um cenário por motivo próprio do módulo. Devolve o que saiu."""
    cenarios = [
        # REPOSICIONAR_SEM_COTACAO_NOVA
        (Decisao(AcaoNaCotacao.REPOSICIONAR, "ganho_justifica_perder_a_fila"),
         None, _cliente(), False),
        # DOIS_LADOS_SEM_ORDEM_DO_LADO_DOWN
        (_reposicionar(dois_lados=True), None, _cliente(), False),
        # ABERTA_SEM_ORDER_ID
        (Decisao(AcaoNaCotacao.CANCELAR, "estavel"), _aberta(""), _cliente(), False),
        # CANCELAMENTO_INCERTO
        (Decisao(AcaoNaCotacao.CANCELAR, "estavel"), _aberta(),
         _cliente(ErroDeTransporte("timeout")), False),
        # CANCELAMENTO_NAO_CONFIRMADO
        (Decisao(AcaoNaCotacao.CANCELAR, "estavel"), _aberta(),
         _cliente((401, None)), False),
        # ENVIO_INCERTO
        (_reposicionar(), None, _cliente(ErroDeTransporte("timeout")), False),
        # ENVIO_RECUSADO
        (_reposicionar(), None, _cliente((400, {"error": "x"})), False),
        # EXCECAO_NA_SEGUNDA_PERNA
        (_reposicionar(dois_lados=True), None,
         _cliente(_aceita("o-up"), RuntimeError("x")), True),
    ]
    produzidos: set[str] = set()
    for decisao, aberta, cliente, com_down in cenarios:
        efeito = await _aplicar(decisao, aberta, cliente, com_down=com_down)
        produzidos.add(efeito.motivo)
    return produzidos


def _motivos_passados(arvore: ast.AST) -> list[tuple[ast.Call, ast.expr]]:
    """Todo argumento `motivo` de `Efeito(...)` (2º posicional ou nomeado) e
    de `_cancelar(..., motivo=...)` na fonte."""
    achados: list[tuple[ast.Call, ast.expr]] = []
    for chamada in ast.walk(arvore):
        if not isinstance(chamada, ast.Call):
            continue
        nome = ast.unparse(chamada.func)
        if nome not in ("Efeito", "_cancelar"):
            continue
        if nome == "Efeito" and len(chamada.args) >= 2:
            achados.append((chamada, chamada.args[1]))
        achados.extend((chamada, kw.value) for kw in chamada.keywords if kw.arg == "motivo")
    return achados


class TestN1MotivosDoEfeitoEnumerados:
    async def test_toda_entrada_declarada_e_ALCANCAVEL_e_todo_motivo_produzido_esta_nela(
        self,
    ):
        from pulsearb.live.execucao_maker import MOTIVOS_DO_EFEITO

        produzidos = await _todo_motivo_produzido()

        assert produzidos == set(MOTIVOS_DO_EFEITO.TODOS), (
            f"nunca devolvidos: {set(MOTIVOS_DO_EFEITO.TODOS) - produzidos}; "
            f"fora da enumeração: {produzidos - set(MOTIVOS_DO_EFEITO.TODOS)}"
        )

    def test_TODOS_bate_com_as_constantes_da_classe(self):
        from pulsearb.live.execucao_maker import MOTIVOS_DO_EFEITO

        constantes = {
            v for k, v in vars(MOTIVOS_DO_EFEITO).items()
            if k.isupper() and isinstance(v, str)
        }
        assert constantes == set(MOTIVOS_DO_EFEITO.TODOS)

    def test_nenhum_Efeito_da_fonte_usa_motivo_literal(self):
        """Inspeção da FONTE: todo `Efeito(...)` e todo `_cancelar(motivo=...)`
        recebe `MOTIVOS_DO_EFEITO.X` ou REPASSA o motivo da decisão (`motivo`,
        `decisao.motivo`, que têm dono no `repouso.MOTIVOS`). Literal solto é
        exatamente a recusa anônima que a regra do `CLAUDE.md` proíbe."""
        from pulsearb.live.execucao_maker import MOTIVOS_DO_EFEITO

        arvore = ast.parse(FONTE_DA_EXECUCAO.read_text(encoding="utf-8"))
        repasses = {"motivo", "decisao.motivo"}
        problemas: list[str] = []
        referenciadas: set[str] = set()

        def _conferir(no: ast.expr, onde: int) -> None:
            if (
                isinstance(no, ast.Attribute)
                and isinstance(no.value, ast.Name)
                and no.value.id == "MOTIVOS_DO_EFEITO"
            ):
                if no.attr not in vars(MOTIVOS_DO_EFEITO):
                    problemas.append(f"linha {onde}: MOTIVOS_DO_EFEITO.{no.attr} inexistente")
                referenciadas.add(no.attr)
                return
            if ast.unparse(no) in repasses:
                return
            problemas.append(f"linha {onde}: motivo {ast.unparse(no)!r}")

        for chamada, motivo in _motivos_passados(arvore):
            _conferir(motivo, chamada.lineno)

        assert not problemas, problemas
        # E toda constante declarada é USADA por algum `Efeito`/`_cancelar`.
        declaradas = {
            k for k, v in vars(MOTIVOS_DO_EFEITO).items()
            if k.isupper() and isinstance(v, str)
        }
        assert declaradas <= referenciadas, declaradas - referenciadas
