"""Item 3.5 — o cliente de ordens: FOK, idempotência, rejeição e timeout.

A REGRA QUE ORGANIZA O MÓDULO INTEIRO
──────────────────────────────────────
**Timeout não é recusa.** É a distinção mais cara deste arquivo, e a mais
fácil de errar, porque nas duas o `POST` terminou sem resposta útil:

- **recusada**: o servidor respondeu, e disse não. Não há posição. Sabemos.
- **incerta**: a resposta não chegou. A ordem pode ter sido aceita e
  preenchida do outro lado. **Não sabemos.**

Tratar incerta como recusada e reenviar é o caminho para posição dupla — o
bot acha que não tem nada, o mercado acha que tem duas. Por isso `EstadoDoEnvio`
tem três valores e não dois, e por isso `INCERTA` é um estado **terminal**:
quem o recebe não reenvia, reconcilia. A reconciliação é leitura (`GET`), que
é segura de repetir; o envio não é.

POR QUE FOK, E SÓ FOK
──────────────────────
`[VERIFICADO]` API_NOTES §4.1: ordem a mercado aceita FAK ou FOK. A decisão do
M4 é **FOK** — tudo ou nada. FAK aceitaria preenchimento parcial, e parcial
numa janela de 5 minutos é a pior das posições: exposição real com tamanho
diferente do que o portão autorizou, e sem tempo de corrigir antes da
resolução. FAK fica anotado para a v2, quando houver como gerenciar o resto.

O QUE ESTE MÓDULO NÃO FAZ
──────────────────────────
Não decide se opera — isso é o motor e o portão. Não abre socket. Não sabe o
que é uma janela. Ele responde uma pergunta só: *esta ordem, já autorizada,
chegou ao livro?* — e a resposta distingue os três estados acima.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from pulsearb.execution.auth import CredenciaisL2, assinar_l2
from pulsearb.obs.logging import get_logger
from pulsearb.risk import OrdemPretendida

log = get_logger(__name__)

#: `[VERIFICADO]` API_NOTES §4.1 — tudo ou nada. Ver o cabeçalho.
TIPO_DE_ORDEM = "FOK"

#: `[VERIFICADO]` API_NOTES §2.1 — o caminho REST de envio de ordem.
CAMINHO_DA_ORDEM = "/order"

#: Teto de espera de um envio. Acima disto a resposta não chega a tempo de
#: ser útil: a janela opera nos últimos 240 s e o edge some com o atraso.
#: Estourar não significa que a ordem não foi aceita — ver `EstadoDoEnvio`.
TIMEOUT_DO_ENVIO_S = 5.0

#: Quantas ordens enviadas lembrar, para a trava de idempotência. Cobre uma
#: sessão inteira com folga: a 5 minutos por janela, 512 são ~42 h.
ENVIOS_LEMBRADOS = 512


class EstadoDoEnvio(StrEnum):
    """Três estados, não dois. Ver o cabeçalho do módulo."""

    ACEITA = "aceita"
    RECUSADA = "recusada"
    #: A resposta não chegou. Pode ter sido aceita. **Não reenviar.**
    INCERTA = "incerta"


class MOTIVOS_DE_RECUSA:
    """Toda recusa tem um destes. Recusa anônima não vira métrica nem alarme —
    a mesma regra do `MOTIVOS` do portão de risco."""

    #: O servidor respondeu com erro de negócio (saldo, allowance, tick).
    SERVIDOR_RECUSOU = "servidor_recusou"
    #: A ordem não passou na conferência local antes de sair.
    ORDEM_MAL_FORMADA = "ordem_mal_formada"
    #: Já enviamos esta ordem nesta sessão. Ver `ClienteDeOrdens.enviar`.
    JA_ENVIADA = "ja_enviada"
    #: Autenticação falhou. Nunca é caso de tentar de novo às cegas.
    AUTH_RECUSADA = "auth_recusada"


@dataclass(frozen=True)
class ResultadoDoEnvio:
    """O que aconteceu com um envio, sem ambiguidade."""

    estado: EstadoDoEnvio
    #: Id da ordem no lado deles. Só existe quando `ACEITA`.
    order_id: str | None = None
    #: Nosso id determinístico. Existe SEMPRE, inclusive em `INCERTA` — é por
    #: ele que a reconciliação encontra a ordem que talvez tenha entrado.
    id_do_cliente: str | None = None
    motivo: str | None = None
    detalhe: dict[str, Any] = field(default_factory=dict)

    @property
    def precisa_reconciliar(self) -> bool:
        """`True` obriga a olhar o lado deles antes de qualquer novo envio."""
        return self.estado is EstadoDoEnvio.INCERTA


class ErroDeTransporte(Exception):
    """O transporte não conseguiu dizer se a ordem chegou.

    Separada de qualquer erro de resposta de propósito: é ela que vira
    `INCERTA`, e confundir as duas é o defeito que o módulo previne.
    """


#: O transporte, injetado. Recebe (caminho, cabeçalhos, corpo em bytes) e
#: devolve (status, json). Protocolo estreito pela mesma razão do
#: `FonteDeAtraso`: o teste passa um dublê e nenhuma rede entra na suíte.
Transporte = Callable[
    [str, dict[str, str], bytes], Awaitable[tuple[int, dict[str, Any] | None]]
]


class ConstrutorDeOrdem(Protocol):
    """Quem transforma uma `OrdemPretendida` no corpo assinado do CLOB.

    Fica fora deste módulo porque envolve a struct EIP-712 da ordem
    (`salt, maker, signer, tokenId, makerAmount, takerAmount, side,
    signatureType, timestamp, metadata, builder` — `[VERIFICADO]` API_NOTES
    §12.13) e, com ela, a chave privada. O cliente orquestra; a chave nunca
    passa por aqui.
    """

    def corpo_da_ordem(
        self, ordem: OrdemPretendida, *, id_do_cliente: str
    ) -> dict[str, Any]:
        """O corpo já assinado, pronto para ir no fio."""
        ...


def id_do_cliente(ordem: OrdemPretendida, *, janela: str) -> str:
    """Id determinístico: a MESMA ordem produz sempre o MESMO id.

    É isso que torna o reenvio seguro do lado deles e a reconciliação possível
    do nosso: depois de um timeout, procuramos por este id em vez de adivinhar
    pelo horário.

    A janela entra na conta porque o resto não basta para identificar: o mesmo
    slug, token, lado, tamanho e preço podem legitimamente repetir em janelas
    diferentes, e um id que colidisse entre elas faria a segunda ordem — real
    e distinta — ser recusada como duplicata.
    """
    semente = "|".join(
        [
            janela,
            ordem.slug,
            ordem.token_id,
            "up" if ordem.lado_up else "down",
            f"{ordem.shares:.6f}",
            f"{ordem.preco_limite:.6f}",
        ]
    )
    return hashlib.sha256(semente.encode("utf-8")).hexdigest()[:32]


def conferir_ordem(ordem: OrdemPretendida, *, minimo_de_shares: float) -> str | None:
    """A conferência local, antes de gastar uma ida à rede.

    Devolve o motivo da recusa, ou `None` se a ordem confere. São as condições
    que o servidor recusaria de qualquer jeito — verificá-las aqui troca um
    timeout de rede por uma mensagem que diz o que está errado.
    """
    if ordem.shares <= 0:
        return f"shares precisa ser positivo: {ordem.shares}"
    if ordem.shares < minimo_de_shares:
        return (
            f"shares {ordem.shares} abaixo do minimo do mercado "
            f"{minimo_de_shares} (API_NOTES §12.5)"
        )
    if not 0.0 < ordem.preco_limite < 1.0:
        return (
            f"preco_limite fora de (0, 1): {ordem.preco_limite} — share de "
            "prediction market custa entre 0 e 1 e paga 1"
        )
    if not ordem.token_id:
        return "token_id vazio"
    return None


class ClienteDeOrdens:
    """Envia ordens FOK ao CLOB. Uma ordem por chamada, sem laço interno.

    Sem `retry` embutido de propósito. Reenviar é a decisão mais perigosa
    deste caminho e ela não pode morar numa política default: quem chama vê o
    `EstadoDoEnvio` e decide, com a informação de janela que este módulo não
    tem.
    """

    def __init__(
        self,
        credenciais: CredenciaisL2,
        construtor: ConstrutorDeOrdem,
        transporte: Transporte,
        *,
        minimo_de_shares: float = 5.0,
        envios_lembrados: int = ENVIOS_LEMBRADOS,
        timeout_s: float = TIMEOUT_DO_ENVIO_S,
    ) -> None:
        self.credenciais = credenciais
        self.construtor = construtor
        self.transporte = transporte
        self.minimo_de_shares = minimo_de_shares
        self.envios_lembrados = envios_lembrados
        self.timeout_s = timeout_s
        #: Ids já enviados nesta sessão, em ordem de envio. É a trava de
        #: idempotência do NOSSO lado; a do lado deles é o próprio id.
        self._enviados: dict[str, EstadoDoEnvio] = {}

    def ja_enviada(self, id_cliente: str) -> EstadoDoEnvio | None:
        """Em que estado esta ordem ficou, se já tentamos enviá-la."""
        return self._enviados.get(id_cliente)

    def _lembrar(self, id_cliente: str, estado: EstadoDoEnvio) -> None:
        self._enviados[id_cliente] = estado
        while len(self._enviados) > self.envios_lembrados:
            self._enviados.pop(next(iter(self._enviados)))

    async def enviar(
        self, ordem: OrdemPretendida, *, janela: str
    ) -> ResultadoDoEnvio:
        """Envia UMA ordem FOK. Nunca reenvia por conta própria.

        A trava de idempotência recusa a segunda tentativa da mesma ordem
        **inclusive quando a primeira ficou incerta** — sobretudo nesse caso.
        Incerta significa que ela pode estar no livro; reenviar seria a
        posição dupla que este módulo existe para impedir.
        """
        identificador = id_do_cliente(ordem, janela=janela)

        anterior = self.ja_enviada(identificador)
        if anterior is not None:
            return ResultadoDoEnvio(
                estado=EstadoDoEnvio.RECUSADA,
                id_do_cliente=identificador,
                motivo=MOTIVOS_DE_RECUSA.JA_ENVIADA,
                detalhe={"estado_anterior": str(anterior)},
            )

        problema = conferir_ordem(ordem, minimo_de_shares=self.minimo_de_shares)
        if problema is not None:
            # NÃO entra em `_enviados`: nada saiu, e travar o id impediria a
            # mesma ordem de ser enviada depois de corrigida a configuração.
            return ResultadoDoEnvio(
                estado=EstadoDoEnvio.RECUSADA,
                id_do_cliente=identificador,
                motivo=MOTIVOS_DE_RECUSA.ORDEM_MAL_FORMADA,
                detalhe={"problema": problema},
            )

        corpo = dict(self.construtor.corpo_da_ordem(ordem, id_do_cliente=identificador))
        corpo["orderType"] = TIPO_DE_ORDEM
        cabecalhos, bytes_do_corpo = assinar_l2(
            self.credenciais,
            metodo="POST",
            caminho=CAMINHO_DA_ORDEM,
            corpo=corpo,
        )

        # RESERVA ANTES DO `await`. Achado P1 do Codex no #52, e procede.
        #
        # `_lembrar` só depois da resposta deixava uma janela entre a consulta
        # a `ja_enviada` e o registro: duas corrotinas com a mesma ordem viam
        # as duas o dicionário vazio, passavam as duas pelo `await`, e a
        # ordem saía DUAS VEZES — exatamente o que a trava existe para
        # impedir. Reservar antes fecha a janela porque não há `await` entre
        # a leitura e a escrita, e o laço de eventos só troca de tarefa num
        # ponto de suspensão.
        #
        # Reserva-se como INCERTA, e não como um estado "em voo" qualquer: se
        # o processo morrer entre o envio e a resposta, INCERTA é exatamente o
        # que aconteceu — a ordem pode estar no livro e ninguém sabe.
        self._lembrar(identificador, EstadoDoEnvio.INCERTA)

        try:
            status, resposta = await asyncio.wait_for(
                self.transporte(CAMINHO_DA_ORDEM, cabecalhos, bytes_do_corpo),
                timeout=self.timeout_s,
            )
        except (ErroDeTransporte, TimeoutError) as erro:
            # O timeout do transporte é a razão de `TIMEOUT_DO_ENVIO_S`
            # existir, e ele não estava ligado (achado P2 do Codex). Um
            # transporte que ENGASGA em vez de levantar — CLOB lento, proxy de
            # rate-limit enfileirando o POST — deixava `enviar` pendurado para
            # sempre e o `INCERTA` obrigatório nunca saía.
            #
            # `asyncio.TimeoutError` é `TimeoutError` desde o 3.11, então a
            # captura cobre as duas grafias.
            self._lembrar(identificador, EstadoDoEnvio.INCERTA)
            log.error(
                "envio incerto: reconciliar antes de qualquer novo envio",
                id_do_cliente=identificador,
                slug=ordem.slug,
                erro=f"{type(erro).__name__}: {erro}",
            )
            return ResultadoDoEnvio(
                estado=EstadoDoEnvio.INCERTA,
                id_do_cliente=identificador,
                detalhe={"erro": f"{type(erro).__name__}: {erro}"},
            )

        return self._resultado(status, resposta, identificador=identificador)

    def _resultado(
        self,
        status: int,
        resposta: dict[str, Any] | None,
        *,
        identificador: str,
    ) -> ResultadoDoEnvio:
        """Classifica a resposta. A faixa 5xx é INCERTA, não recusa.

        Um 502 de gateway pode ter sido devolvido depois de a ordem passar
        para o motor de casamento. Só a faixa 4xx prova que ela não entrou:
        aí o servidor examinou o pedido e disse não.
        """
        if status >= 500:
            self._lembrar(identificador, EstadoDoEnvio.INCERTA)
            return ResultadoDoEnvio(
                estado=EstadoDoEnvio.INCERTA,
                id_do_cliente=identificador,
                detalhe={"status": status, "resposta": resposta},
            )

        if status in (401, 403):
            self._lembrar(identificador, EstadoDoEnvio.RECUSADA)
            return ResultadoDoEnvio(
                estado=EstadoDoEnvio.RECUSADA,
                id_do_cliente=identificador,
                motivo=MOTIVOS_DE_RECUSA.AUTH_RECUSADA,
                detalhe={"status": status},
            )

        if status >= 400 or not resposta or not resposta.get("success", True):
            self._lembrar(identificador, EstadoDoEnvio.RECUSADA)
            return ResultadoDoEnvio(
                estado=EstadoDoEnvio.RECUSADA,
                id_do_cliente=identificador,
                motivo=MOTIVOS_DE_RECUSA.SERVIDOR_RECUSOU,
                detalhe={"status": status, "resposta": resposta},
            )

        self._lembrar(identificador, EstadoDoEnvio.ACEITA)
        return ResultadoDoEnvio(
            estado=EstadoDoEnvio.ACEITA,
            order_id=resposta.get("orderID") or resposta.get("orderId"),
            id_do_cliente=identificador,
            detalhe={"status": status},
        )
