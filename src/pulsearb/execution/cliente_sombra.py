"""4.0(c) — o cliente de ordens que NÃO envia, para a rota maker rodar em SHADOW.

## Por que este arquivo precisa existir antes do laço

O `live/execucao_maker.py` sabe cancelar, reposicionar e reconciliar, mas fala
com um `ClienteDeOrdens` — que abre socket, assina com credencial e manda
ordem de verdade. Ligar aquilo ao `shadow.py` como está violaria a invariante
que dá nome ao processo, escrita no topo do `live/shadow.py`:

    *"O que este processo NUNCA faz: enviar ordem."*

A saída é a mesma que o caminho taker já usa há tempo. O `ExecutorSombra` roda
o caminho inteiro e **escreve num arquivo em vez de na rede**; este é o
equivalente para a rota maker. Não abre socket, não assina nada, não recebe
credencial: instanciar isto com chave de produção no ambiente não envia ordem,
porque não existe aqui código que saiba enviar.

## Mesmo caminho, e é o ponto

`aplicar_decisao` e `reconciliar` rodam **sem nenhuma alteração** contra este
cliente. Isso não é economia de digitação — é a regra do "mesmo caminho" do
`CLAUDE.md`: se o SHADOW usasse uma cópia da lógica de repouso, uma divergência
entre SHADOW e LIVE pareceria diferença de mercado quando fosse diferença de
código, e é justamente essa comparação que justifica o SHADOW existir.

## O que ele simula, e o que NÃO simula

Simula o **protocolo**: envio aceito devolve um `order_id`, cancelamento tira
do livro, listagem devolve o que está repousando. Isso basta para exercitar o
ciclo do `order_id`, a trava do cancelamento incerto e a reconciliação.

**NÃO** simula preenchimento, fila nem markout. Uma cotação daqui repousa para
sempre até ser cancelada. Fingir preenchimento exigiria justamente a posição na
fila que não é observável (1.6), e um número inventado ali entraria no diário
com cara de medida — o defeito que o `cobertura_da_gravacao` já produziu neste
projeto. O que preenche ou não é pergunta do backtest sobre gravação, com o
limite pessimista do `conta_pessimista_do_maker`, não deste cliente.

## O id de sombra é reconhecível de propósito

Todo `order_id` daqui começa com `sombra-`. Um id de sombra que vazasse para um
caminho real seria recusado pelo servidor em vez de casar por acidente com
alguma ordem — e, mais importante, quem lê o diário distingue na hora uma
rodada de ensaio de uma rodada real.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pulsearb.execution.cliente import (
    EstadoDoCancelamento,
    EstadoDoEnvio,
    OrdemAberta,
    ResultadoDoCancelamento,
    ResultadoDoEnvio,
    id_do_cliente,
)
from pulsearb.obs.logging import get_logger
from pulsearb.risk import OrdemPretendida
from pulsearb.settings import Mode

log = get_logger(__name__)

#: Prefixo de todo id devolvido aqui. Ver o cabeçalho.
PREFIXO_DE_SOMBRA = "sombra-"


@dataclass(frozen=True, slots=True)
class CotacaoRepousada:
    """Uma cotação que, no ensaio, está no livro."""

    order_id: str
    id_do_cliente: str
    ordem: OrdemPretendida
    janela: str
    desde_epoch: float

    def como_ordem_aberta(self) -> OrdemAberta:
        """A mesma forma que `listar_ordens_abertas` do cliente real devolve,
        para a reconciliação não saber a diferença."""
        return OrdemAberta(
            id=self.order_id,
            token_id=self.ordem.token_id,
            side="BUY" if self.ordem.lado_up else "SELL",
            price=self.ordem.preco_limite,
            original_size=self.ordem.shares,
            # Sem preenchimento simulado: ver o cabeçalho.
            size_matched=0.0,
            status="LIVE",
        )


@dataclass
class ClienteSombraDeOrdens:
    """A interface do `ClienteDeOrdens`, gravando em disco em vez de na rede."""

    caminho_do_diario: Path
    modo: Mode = Mode.SHADOW
    #: O que está "repousando", por `order_id`.
    repousadas: dict[str, CotacaoRepousada] = field(default_factory=dict)
    _sequencia: int = 0

    def __post_init__(self) -> None:
        if self.modo is Mode.LIVE:
            # Mesma recusa do `ExecutorSombra`, e pela mesma razão: cair para
            # sombra quando pedem LIVE é a falha silenciosa mais cara possível
            # — o operador acredita que está operando e o dinheiro não se move.
            raise ValueError(
                "ClienteSombraDeOrdens nunca roda como LIVE. Se a intenção é "
                "cotar de verdade, o cliente é o `ClienteDeOrdens` — e a rota "
                "maker ainda não tem autorização para isso (risk/autorizacao.py)."
            )

    # ------------------------------------------------------------------ envio
    async def enviar(
        self, ordem: OrdemPretendida, *, janela: str
    ) -> ResultadoDoEnvio:
        """Aceita e registra. Nunca recusa por mercado — não há mercado aqui.

        Recusar por conta própria inventaria uma taxa de rejeição que o ensaio
        não mediu, e ela apareceria no diário como se fosse do servidor.
        """
        identificador = id_do_cliente(ordem, janela=janela)
        self._sequencia += 1
        order_id = f"{PREFIXO_DE_SOMBRA}{self._sequencia}-{identificador[:12]}"
        agora = time.time()

        self.repousadas[order_id] = CotacaoRepousada(
            order_id=order_id,
            id_do_cliente=identificador,
            ordem=ordem,
            janela=janela,
            desde_epoch=agora,
        )
        self._registrar(
            "cotacao_colocada",
            order_id=order_id,
            id_do_cliente=identificador,
            janela=janela,
            slug=ordem.slug,
            token_id=ordem.token_id,
            lado_up=ordem.lado_up,
            shares=ordem.shares,
            preco_limite=ordem.preco_limite,
            ts=agora,
        )
        return ResultadoDoEnvio(
            estado=EstadoDoEnvio.ACEITA,
            order_id=order_id,
            id_do_cliente=identificador,
            detalhe={"sombra": True},
        )

    # ------------------------------------------------------------ cancelamento
    async def cancelar(self, order_id: str) -> ResultadoDoCancelamento:
        """Tira do livro de ensaio. Id desconhecido vira `NAO_CANCELADA`.

        É o mesmo desfecho que o servidor daria para um id que já não existe
        (§4.4: volta em `not_canceled` com motivo), então a `execucao_maker`
        segue o mesmo ramo aqui e lá.
        """
        repousada = self.repousadas.pop(order_id, None)
        if repousada is None:
            self._registrar(
                "cancelamento_de_id_desconhecido", order_id=order_id, ts=time.time()
            )
            return ResultadoDoCancelamento(
                estado=EstadoDoCancelamento.NAO_CANCELADA,
                order_id=order_id,
                motivo="id_nao_repousava",
                detalhe={"sombra": True},
            )

        self._registrar(
            "cotacao_cancelada",
            order_id=order_id,
            id_do_cliente=repousada.id_do_cliente,
            janela=repousada.janela,
            slug=repousada.ordem.slug,
            segundos_repousada=round(time.time() - repousada.desde_epoch, 3),
            ts=time.time(),
        )
        return ResultadoDoCancelamento(
            estado=EstadoDoCancelamento.CANCELADA,
            order_id=order_id,
            detalhe={"sombra": True},
        )

    # ------------------------------------------------------------- reconciliar
    async def listar_ordens_abertas(
        self, *, token_id: str | None = None, market: str | None = None
    ) -> list[OrdemAberta]:
        """O que está repousando no ensaio, filtrado como o real filtra.

        Não levanta `ErroDeLeitura`: aqui a leitura não pode falhar, porque não
        há rede. A reconciliação continua exercitada — só nunca entra no ramo
        de falha, que é coberto pelos testes do cliente real.
        """
        abertas = [r.como_ordem_aberta() for r in self.repousadas.values()]
        if token_id:
            abertas = [o for o in abertas if o.token_id == token_id]
        return abertas

    # ------------------------------------------------------------------ diário
    def _registrar(self, evento: str, **campos: Any) -> None:
        """Uma linha por evento, no MESMO arquivo do diário de intenções.

        Mesmo arquivo de propósito: quem lê a rodada quer a sequência real de
        decisões, e dois arquivos obrigariam a intercalar por carimbo depois —
        que é exatamente onde uma reconstrução erra.
        """
        linha = {"evento": evento, "sombra": True, **campos}
        try:
            with self.caminho_do_diario.open("a", encoding="utf-8") as arquivo:
                arquivo.write(json.dumps(linha, ensure_ascii=False, default=str) + "\n")
        except OSError as erro:
            # O diário sumindo é a saída da rodada sumindo — mesma leitura que
            # o `laco_de_decisao` faz do erro de I/O. Sobe.
            log.error(
                "falha ao gravar evento maker no diario",
                evento=evento,
                erro=f"{type(erro).__name__}: {erro}",
            )
            raise
