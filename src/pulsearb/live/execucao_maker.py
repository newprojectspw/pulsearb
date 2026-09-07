"""Item 4.0 (c) — o I/O que o `repouso.py` decide mas não faz.

`repouso.decidir` responde *mexer ou deixar* e devolve uma `Decisao`; ele diz,
no próprio docstring, que **não cancela nem envia**. Este módulo é a outra
metade: pega a `Decisao` e o `ClienteDeOrdens` e executa — cancela, reposiciona
ou mantém —, cuidando do ciclo do `order_id` que separa "decidi cancelar" de
"a ordem saiu do livro".

A REGRA QUE ORGANIZA O MÓDULO
──────────────────────────────
**Reposicionar é cancelar E DEPOIS enviar, e a ordem dos dois não é troca.**
Se o cancelamento da ordem antiga fica INCERTO — timeout, 5xx —, a ordem antiga
PODE continuar repousando. Enviar a nova em cima disso é a posição dupla que o
`cliente.py` inteiro existe para impedir, só que agora entre duas ordens
maker: as duas repousam, as duas podem preencher, e o portão autorizou uma.

Por isso o reposicionamento **para no cancelamento incerto** e devolve o
controle para a reconciliação, em vez de completar às cegas. É a mesma
disciplina do envio (`INCERTA` é terminal), aplicada à sequência de dois passos.

O QUE ESTE MÓDULO NÃO FAZ
──────────────────────────
Não decide (isso é o `repouso`), não sabe montar a ordem a partir de uma
`Cotacao` — quem chama passa `ordem_da_cotacao`, porque virar `Cotacao` em
`OrdemPretendida` precisa do meio, do tick e do token, que são contexto de
mercado que este módulo não tem, exatamente como o `repouso` não sabe onde
cotar. E não lê tempo: o `agora_epoch` entra de fora.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pulsearb.execution.cliente import (
    ClienteDeOrdens,
    EstadoDoCancelamento,
    EstadoDoEnvio,
    OrdemAberta,
)
from pulsearb.live.cotacao import Cotacao
from pulsearb.live.repouso import AcaoNaCotacao, CotacaoAberta, Decisao
from pulsearb.obs.logging import get_logger
from pulsearb.risk import OrdemPretendida

log = get_logger(__name__)

#: Como virar uma `Cotacao` (distância em ticks, tamanho) numa `OrdemPretendida`
#: concreta. Mora fora deste módulo pela razão do cabeçalho: precisa do contexto
#: de mercado. Recebe a cotação, devolve a ordem pronta para `enviar`.
OrdemDaCotacao = Callable[[Cotacao], OrdemPretendida]


class ResultadoDaAcao(StrEnum):
    """O que efetivamente aconteceu — não o que se decidiu fazer.

    A decisão do `repouso` diz a intenção; isto diz o desfecho, que pode
    divergir: um REPOSICIONAR cujo cancelamento ficou incerto NÃO virou uma
    nova cotação, e chamar isso de "reposicionado" esconderia a ordem órfã.
    """

    #: Nada mudou no livro (decisão MANTER, ou nada aberto e sem candidata).
    MANTIDA = "mantida"
    #: A ordem saiu do livro e nada entrou.
    CANCELADA = "cancelada"
    #: Cancelou a antiga (se havia) e a nova está repousando.
    REPOSICIONADA = "reposicionada"
    #: Uma cotação nova entrou sem que houvesse anterior.
    COLOCADA = "colocada"
    #: Parou no meio: o estado do livro ficou INCERTO e reconciliar é o único
    #: caminho seguro. NÃO se tenta completar a ação às cegas.
    RECONCILIAR = "reconciliar"


@dataclass(frozen=True, slots=True)
class Efeito:
    """O desfecho de aplicar uma decisão, com o novo estado do nosso lado."""

    resultado: ResultadoDaAcao
    motivo: str
    #: O que repousa AGORA, do nosso lado, depois desta ação. `None` = nada.
    #: Em `RECONCILIAR`, é o que ACHAMOS que pode repousar — a reconciliação
    #: confere contra o servidor.
    aberta: CotacaoAberta | None = None
    detalhe: dict[str, Any] = field(default_factory=dict)

    @property
    def precisa_reconciliar(self) -> bool:
        return self.resultado is ResultadoDaAcao.RECONCILIAR


async def aplicar_decisao(
    decisao: Decisao,
    aberta: CotacaoAberta | None,
    *,
    cliente: ClienteDeOrdens,
    ordem_da_cotacao: OrdemDaCotacao,
    janela: str,
    agora_epoch: float,
) -> Efeito:
    """Executa a `Decisao` do `repouso` no livro, via `cliente`.

    Devolve o `Efeito` — o desfecho real e o novo `CotacaoAberta` —, que quem
    chama guarda para a próxima rodada de decisão e para o diário.
    """
    if decisao.acao is AcaoNaCotacao.MANTER:
        return Efeito(ResultadoDaAcao.MANTIDA, decisao.motivo, aberta=aberta)

    if decisao.acao is AcaoNaCotacao.CANCELAR:
        return await _cancelar(aberta, cliente=cliente, motivo=decisao.motivo)

    # REPOSICIONAR. `nova` é garantida pelo `repouso` neste ramo, mas conferir
    # aqui evita um `AttributeError` no fio se a invariante mudar lá.
    if decisao.nova is None:
        return Efeito(
            ResultadoDaAcao.MANTIDA,
            "reposicionar_sem_cotacao_nova",
            aberta=aberta,
            detalhe={"aviso": "decisao REPOSICIONAR sem `nova`; nada enviado"},
        )

    # 1) Tira a antiga do livro ANTES de pôr a nova. Um cancelamento incerto
    #    para tudo: a antiga pode repousar, e a nova em cima seria posição
    #    dupla. Ver o cabeçalho.
    if aberta is not None:
        efeito_cancel = await _cancelar(
            aberta, cliente=cliente, motivo=decisao.motivo
        )
        if efeito_cancel.precisa_reconciliar:
            return efeito_cancel

    # 2) Envia a nova como cotação repousada.
    return await _colocar(
        decisao.nova,
        cliente=cliente,
        ordem_da_cotacao=ordem_da_cotacao,
        janela=janela,
        agora_epoch=agora_epoch,
        motivo=decisao.motivo,
        tinha_anterior=aberta is not None,
        ganho=decisao.ganho_estimado_usdc,
    )


async def _cancelar(
    aberta: CotacaoAberta | None,
    *,
    cliente: ClienteDeOrdens,
    motivo: str,
) -> Efeito:
    if aberta is None:
        # Nada a cancelar: a decisão de sair já está satisfeita.
        return Efeito(ResultadoDaAcao.CANCELADA, motivo, aberta=None)

    if not aberta.order_id:
        # Sem id do servidor não há como cancelar por id (§4.4). Isso NÃO é
        # "nada a fazer": a ordem pode estar no livro sob um id que nunca
        # capturamos — é exatamente o que a reconciliação existe para achar.
        log.warning(
            "cotacao aberta sem order_id: reconciliar em vez de cancelar as cegas",
            id_do_cliente=aberta.id_do_cliente,
        )
        return Efeito(
            ResultadoDaAcao.RECONCILIAR,
            "aberta_sem_order_id",
            aberta=aberta,
            detalhe={"id_do_cliente": aberta.id_do_cliente},
        )

    resultado = await cliente.cancelar(aberta.order_id)
    if resultado.estado is EstadoDoCancelamento.INCERTA:
        # Não sabemos se saiu do livro. Manter `aberta` para a reconciliação
        # ter o que procurar; NÃO a damos por fechada.
        return Efeito(
            ResultadoDaAcao.RECONCILIAR,
            "cancelamento_incerto",
            aberta=aberta,
            detalhe={"order_id": aberta.order_id},
        )
    # CANCELADA ou NAO_CANCELADA: nos dois, a ordem não repousa mais do nosso
    # ponto de vista. NAO_CANCELADA inclui "já tinha sumido" (§4.4), que para o
    # objetivo — não ter esta ordem no livro — é tão bom quanto cancelada.
    return Efeito(
        ResultadoDaAcao.CANCELADA,
        motivo,
        aberta=None,
        detalhe={"estado_do_cancelamento": str(resultado.estado)},
    )


async def _colocar(
    nova: Cotacao,
    *,
    cliente: ClienteDeOrdens,
    ordem_da_cotacao: OrdemDaCotacao,
    janela: str,
    agora_epoch: float,
    motivo: str,
    tinha_anterior: bool,
    ganho: float,
) -> Efeito:
    ordem = ordem_da_cotacao(nova)
    resultado = await cliente.enviar(ordem, janela=janela)

    if resultado.estado is EstadoDoEnvio.ACEITA:
        aberta_nova = CotacaoAberta(
            cotacao=nova,
            desde_epoch=agora_epoch,
            id_do_cliente=resultado.id_do_cliente or "",
            order_id=resultado.order_id or "",
        )
        return Efeito(
            ResultadoDaAcao.REPOSICIONADA if tinha_anterior else ResultadoDaAcao.COLOCADA,
            motivo,
            aberta=aberta_nova,
            detalhe={"order_id": aberta_nova.order_id, "ganho_estimado_usdc": ganho},
        )

    if resultado.estado is EstadoDoEnvio.INCERTA:
        # A nova PODE ter entrado. Reconciliar, e sem `aberta` conhecida: só o
        # id do cliente aponta o que procurar. A antiga (se havia) já saiu no
        # passo 1 deste reposicionamento.
        return Efeito(
            ResultadoDaAcao.RECONCILIAR,
            "envio_incerto",
            aberta=CotacaoAberta(
                cotacao=nova,
                desde_epoch=agora_epoch,
                id_do_cliente=resultado.id_do_cliente or "",
            ),
            detalhe={"id_do_cliente": resultado.id_do_cliente},
        )

    # RECUSADA: nada entrou. A antiga já saiu (se havia). O livro fica sem a
    # nossa cotação — correto, e o motivo da recusa vai no detalhe.
    return Efeito(
        ResultadoDaAcao.CANCELADA if tinha_anterior else ResultadoDaAcao.MANTIDA,
        "envio_recusado",
        aberta=None,
        detalhe={"motivo_da_recusa": resultado.motivo},
    )


@dataclass(frozen=True, slots=True)
class Reconciliacao:
    """O que o servidor diz repousar, comparado ao que ACHÁVAMOS que repousava.

    A reconciliação de arranque existe porque um `INCERTA` — de envio ou de
    cancelamento — deixa o nosso lado sem saber o estado do livro, e um reinício
    perde a memória em RAM. Ler o servidor é a única fonte que resolve isso.
    """

    #: Ids que existem nos DOIS lados: a ordem está onde achávamos.
    casadas: tuple[str, ...] = ()
    #: Ordens que o servidor lista e que NÃO esperávamos. Órfã típica: um envio
    #: que ficou `INCERTA` e afinal entrou. É posição real que ninguém está
    #: gerenciando — o alvo número um da reconciliação.
    orfas: tuple[OrdemAberta, ...] = ()
    #: Ids que esperávamos e que o servidor NÃO lista: preencheram ou já foram
    #: canceladas. O nosso lado tem de largar o registro delas.
    fantasmas: tuple[str, ...] = ()

    @property
    def limpa(self) -> bool:
        """Sem órfã nem fantasma: o nosso estado batia com o servidor."""
        return not self.orfas and not self.fantasmas


async def reconciliar(
    cliente: ClienteDeOrdens,
    esperadas: dict[str, CotacaoAberta],
    *,
    token_id: str | None = None,
    market: str | None = None,
) -> Reconciliacao:
    """Casa as ordens abertas no servidor com as que o nosso lado espera.

    `esperadas` é indexado pelo `order_id` do servidor — o mesmo por que se
    cancela. A leitura é fail-closed: se `listar_ordens_abertas` levantar
    `ErroDeLeitura`, ele SOBE. Engolir e devolver "nada aberto" faria a
    reconciliação declarar o livro limpo sem ter olhado — e o arranque
    seguiria como se não houvesse órfã, que é o pior desfecho possível aqui.
    """
    no_servidor = await cliente.listar_ordens_abertas(token_id=token_id, market=market)
    ids_no_servidor = {o.id for o in no_servidor}
    ids_esperados = set(esperadas)

    casadas = tuple(sorted(ids_no_servidor & ids_esperados))
    orfas = tuple(o for o in no_servidor if o.id not in ids_esperados)
    fantasmas = tuple(sorted(ids_esperados - ids_no_servidor))
    return Reconciliacao(casadas=casadas, orfas=orfas, fantasmas=fantasmas)


async def cancelar_orfas(
    cliente: ClienteDeOrdens, reconciliacao: Reconciliacao
) -> dict[str, str]:
    """Cancela as órfãs achadas. É a AÇÃO sobre a reconciliação, separada da
    leitura — quem chama decide se e quando executá-la.

    Cancelar órfã no arranque é a resposta certa por default: é ordem que
    entrou sem que o nosso lado a estivesse gerenciando, e deixá-la repousando
    é exposição que nenhum portão desta sessão autorizou. Devolve o estado do
    cancelamento por id, para o diário — inclusive `INCERTA`, que pede outra
    passada (recancelar é seguro, §4.4).
    """
    desfechos: dict[str, str] = {}
    for orfa in reconciliacao.orfas:
        if not orfa.id:
            continue
        resultado = await cliente.cancelar(orfa.id)
        desfechos[orfa.id] = str(resultado.estado)
        if orfa.size_matched > 0:
            # Meio preenchida: cancelar tira o resto do livro, mas a metade que
            # casou é posição REAL. O log nomeia isso para a reconciliação de
            # PnL não perder que existiu exposição.
            log.warning(
                "orfa meio preenchida cancelada: ha posicao real a reconciliar",
                order_id=orfa.id,
                size_matched=orfa.size_matched,
                original_size=orfa.original_size,
            )
    return desfechos
