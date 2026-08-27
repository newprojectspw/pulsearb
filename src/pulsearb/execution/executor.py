"""SHADOW: decide tudo, envia nada, registra o que teria feito.

**Por que o SHADOW vem antes do cliente de ordens.** O backtest diz o que
teria acontecido sobre gravação. O SHADOW diz o que teria acontecido *ao
vivo*, com o feed real chegando no tempo real, a decisão saindo com a latência
real, e o livro no estado em que estava naquele instante. A diferença entre os
dois é a única medida honesta de quanto do resultado do backtest é artefato de
olhar o passado com calma.

**A regra que faz o SHADOW valer alguma coisa: MESMO CAMINHO.** Se o SHADOW
tomar um atalho — pular um portão, usar outro preço, decidir em outro momento
— ele deixa de ser ensaio e vira simulação, e simulação já temos. Por isso o
executor é uma interface com duas implementações que divergem **só no último
passo**: uma escreve numa fila de rede, a outra escreve num arquivo.

**O que o SHADOW NÃO prova.** Ele não prova que a ordem seria preenchida.
Ninguém do outro lado sabe que ela existe, então não há fila, não há
concorrência pelo mesmo nível, e o mercado não reage. O registro guarda o topo
do livro no instante da decisão justamente para que essa conta possa ser feita
depois — mas ela é uma conta, não uma observação.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from pulsearb.obs.logging import get_logger
from pulsearb.risk import Decisao, OrdemPretendida, PortaoDeRisco
from pulsearb.settings import Mode

log = get_logger(__name__)


@dataclass(frozen=True)
class IntencaoRegistrada:
    """Uma decisão de operar, com tudo que a torna reconciliável depois.

    Os campos não são um dump de conveniência: cada um responde a uma
    pergunta que se faz ao comparar o SHADOW com o backtest.

    - `ts_ns` e `latencia_da_decisao_ms`: *quando* se decidiu, e quanto tempo
      o pipeline levou. O backtest assume uma latência; aqui ela é medida.
    - `melhor_ask` / `melhor_bid` / `profundidade_no_topo`: o livro no
      instante. Sem isso não dá para estimar preenchimento depois, e o SHADOW
      vira uma lista de intenções sem consequência.
    - `prob_prevista`: a saída do modelo. É o que permite refazer a curva de
      confiabilidade sobre dado ao vivo, e não só sobre gravação.
    - `decisao_do_portao`: passou ou não, e por quê. Um SHADOW que só registra
      o que passou esconde exatamente o número que interessa quando o bot não
      opera: qual portão está segurando.
    """

    ts_ns: int
    slug: str
    token_id: str
    lado_up: bool
    shares: float
    preco_limite: float
    prob_prevista: float
    seconds_left: float
    melhor_bid: float | None
    melhor_ask: float | None
    profundidade_no_topo: float | None
    latencia_da_decisao_ms: float
    pode: bool
    motivo: str | None
    detalhe: dict[str, Any] = field(default_factory=dict)

    def como_linha(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, separators=(",", ":"))


class Executor(Protocol):
    """O contrato. Quem chama não sabe se está em SHADOW ou LIVE."""

    modo: Mode
    #: O portão fica exposto porque quem orquestra precisa dar baixa na
    #: exposição quando uma janela fecha. Sem isso o teto de exposição trava
    #: para sempre e o bot passa a recusar tudo sem nada de errado no mercado.
    portao: PortaoDeRisco

    def executar(
        self,
        ordem: OrdemPretendida,
        *,
        feeds_saudaveis: bool,
        prob_prevista: float,
        seconds_left: float,
        ts_ns: int,
        melhor_bid: float | None = None,
        melhor_ask: float | None = None,
        profundidade_no_topo: float | None = None,
        latencia_da_decisao_ms: float = 0.0,
    ) -> Decisao: ...


class ExecutorSombra:
    """Roda o caminho inteiro e escreve num arquivo em vez de na rede.

    Não abre socket, não assina nada, não tem credencial. Instanciar isto com
    chave de produção no ambiente não envia ordem, porque não existe código
    aqui que saiba enviar.
    """

    def __init__(
        self,
        portao: PortaoDeRisco,
        *,
        caminho_do_diario: Path,
        modo: Mode = Mode.SHADOW,
    ) -> None:
        if modo is Mode.LIVE:
            raise ValueError(
                "ExecutorSombra nunca roda como LIVE. Se a intenção é operar "
                "de verdade, o executor é outro — e ele ainda não existe."
            )
        self.portao = portao
        self.modo = modo
        self.caminho = caminho_do_diario
        self.intencoes: list[IntencaoRegistrada] = []

    def executar(
        self,
        ordem: OrdemPretendida,
        *,
        feeds_saudaveis: bool,
        prob_prevista: float,
        seconds_left: float,
        ts_ns: int,
        melhor_bid: float | None = None,
        melhor_ask: float | None = None,
        profundidade_no_topo: float | None = None,
        latencia_da_decisao_ms: float = 0.0,
    ) -> Decisao:
        # Os portões de RISCO rodam iguais aos do LIVE. O portão de MODO não:
        # ele existe para impedir envio, e aqui não há envio para impedir.
        # Rodá-lo aqui faria todo registro sair como `modo_nao_opera`, e o
        # diário perderia justamente a informação que justifica o SHADOW —
        # qual portão estaria segurando se o modo fosse LIVE.
        decisao = self.portao.avaliar_risco(
            ordem,
            feeds_saudaveis=feeds_saudaveis,
            melhor_bid=melhor_bid,
            melhor_ask=melhor_ask,
        )

        intencao = IntencaoRegistrada(
            ts_ns=ts_ns,
            slug=ordem.slug,
            token_id=ordem.token_id,
            lado_up=ordem.lado_up,
            shares=ordem.shares,
            preco_limite=ordem.preco_limite,
            prob_prevista=prob_prevista,
            seconds_left=seconds_left,
            melhor_bid=melhor_bid,
            melhor_ask=melhor_ask,
            profundidade_no_topo=profundidade_no_topo,
            latencia_da_decisao_ms=latencia_da_decisao_ms,
            pode=decisao.pode,
            motivo=decisao.motivo,
            detalhe=decisao.detalhe,
        )
        self._anotar(intencao)

        if decisao.pode:
            # A contabilidade de exposição roda TAMBÉM no shadow. Sem isso os
            # tetos por janela e de exposição nunca seriam exercitados, e o
            # ensaio não ensaiaria a parte que mais importa.
            self.portao.registrar_envio(ordem)

        return decisao

    def _anotar(self, intencao: IntencaoRegistrada) -> None:
        self.intencoes.append(intencao)
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        with self.caminho.open("a", encoding="utf-8") as arquivo:
            arquivo.write(intencao.como_linha() + "\n")

    # ───────────────────────────────────────────────────────────────── resumo
    def resumo(self) -> dict[str, Any]:
        """O que o ensaio viu. `por_motivo` é a parte acionável.

        Um SHADOW que roda a noite inteira e registra zero intenções aprovadas
        não é um bot sem oportunidade — é um bot com um portão fechado, e
        `por_motivo` diz qual.
        """
        por_motivo: dict[str, int] = {}
        for intencao in self.intencoes:
            if intencao.motivo:
                por_motivo[intencao.motivo] = por_motivo.get(intencao.motivo, 0) + 1

        aprovadas = [i for i in self.intencoes if i.pode]
        return {
            "modo": self.modo.value,
            "intencoes": len(self.intencoes),
            "aprovadas": len(aprovadas),
            "recusadas": len(self.intencoes) - len(aprovadas),
            "por_motivo": dict(sorted(por_motivo.items())),
            "capital_que_teria_sido_movimentado_usdc": round(
                sum(i.shares * i.preco_limite for i in aprovadas), 4
            ),
            "diario": str(self.caminho),
            "nota": (
                "NADA foi enviado. `aprovadas` e o que TERIA sido enviado se o "
                "modo fosse LIVE, e nem isso e promessa de preenchimento: "
                "ninguem do outro lado sabe que a ordem existe, entao nao ha "
                "fila, nao ha concorrencia pelo nivel e o mercado nao reage. "
                "`melhor_bid`/`melhor_ask`/`profundidade_no_topo` ficam no "
                "diario para que a conta de preenchimento possa ser feita "
                "depois — e ela e uma CONTA, nao uma observacao. "
                "`por_motivo` e a parte acionavel: shadow que registra zero "
                "aprovadas nao e falta de oportunidade, e um portao fechado."
            ),
        }


def escolher_executor(
    modo: Mode,
    portao: PortaoDeRisco,
    *,
    caminho_do_diario: Path,
) -> Executor:
    """SIM e SHADOW ensaiam. LIVE ainda não existe, e falha alto.

    Cair para SHADOW quando pedem LIVE seria a falha silenciosa mais cara
    possível: o operador acredita que está operando, o dinheiro não se move,
    e a descoberta vem quando alguém for conferir o saldo.
    """
    if modo is Mode.LIVE:
        raise NotImplementedError(
            "modo LIVE ainda nao existe — falta o cliente de ordens (M4 item "
            "3.2), FOK e idempotencia (3.5) e a trava tripla (3.4). Este erro "
            "e deliberado: cair para SHADOW em silencio faria o operador "
            "acreditar que esta operando enquanto nada acontece."
        )
    return ExecutorSombra(portao, caminho_do_diario=caminho_do_diario, modo=modo)


def carregar_diario(caminho: Path) -> list[dict[str, Any]]:
    """Lê o diário do shadow. Linha quebrada é PULADA e contada, não fatal.

    O diário é escrito por append durante uma sessão que pode ser morta a
    qualquer momento — a última linha pode estar pela metade, e isso é
    esperado. Recusar o arquivo inteiro por causa dela perderia a sessão.
    """
    linhas: list[dict[str, Any]] = []
    quebradas = 0
    with caminho.open(encoding="utf-8") as arquivo:
        for linha in arquivo:
            if not linha.strip():
                continue
            try:
                linhas.append(json.loads(linha))
            except json.JSONDecodeError:
                quebradas += 1
    if quebradas:
        log.warning(
            "linhas quebradas no diario do shadow",
            caminho=str(caminho),
            quebradas=quebradas,
        )
    return linhas


def agora_ns() -> int:
    return int(datetime.now(UTC).timestamp() * 1e9)
