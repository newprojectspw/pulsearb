"""Pode entrar em LIVE? (item 3.4, e o 5.4 junto)

Quem responde "sim" a esta pergunta autoriza dinheiro real a se mover. Por
isso ela não é um `if` espalhado: é um lugar só, que **nomeia todos os
bloqueios de uma vez**.

## A trava é tripla, e as três são de tipos diferentes

| Trava | O que é | O que ela impede |
|---|---|---|
| `MODE=LIVE` | configuração | o default nunca opera |
| `PULSEARB_CONFIRM_LIVE=1` | segunda variável, independente | um `.env` copiado de outra máquina |
| a frase exata | digitada à mão | automação e engano de dedo |

Três porque uma não basta e duas se copiam juntas. A frase existe para que a
última etapa seja impossível de fazer por acidente: ninguém digita
`EU ACEITO O RISCO` sem saber o que está fazendo, e nenhum script de deploy a
carrega por descuido.

## E duas travas que não são de intenção, mas de estado

**Relógio sincronizado (5.4).** A decisão inteira se apoia em `seconds_left`,
a distância entre o NOSSO agora e o fechamento da janela. O sensor por tick
(`live/relogio.py`) NÃO fecha isso — ele mede `latencia + offset` numa
subtração só, e as duas se cancelam. Sincronia verificada vem do daemon de
NTP, que faz medição de duas vias. Não determinado conta como não
sincronizado.

**Cliente de ordens.** Hoje não existe (itens 3.2 e 3.5). Ele entra nesta
lista como qualquer outro bloqueio, e sai dela quando existir.

## Por que TODOS os bloqueios, e não o primeiro

Reportar só o primeiro faria o operador consertar um, rodar de novo, descobrir
o segundo, e assim por diante — cada volta com a expectativa de que fosse a
última. Pior: a trava tripla nunca seria exercitada enquanto o cliente de
ordens não existisse, porque o "cliente ausente" apareceria primeiro e
esconderia as outras. Uma lista completa diz a verdade sobre a distância até
o LIVE.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pulsearb.risk.sincronia import Sincronia, estado_da_sincronia
from pulsearb.settings import Mode

#: A segunda trava. Nome próprio, separado do `PULSEARB_MODE`, para que copiar
#: um `.env` inteiro não traga as duas juntas.
ENV_CONFIRMACAO = "PULSEARB_CONFIRM_LIVE"

#: A terceira. O valor é a frase, não um booleano: `true` se digita sem
#: pensar.
ENV_ACEITE = "PULSEARB_ACEITO_O_RISCO"
FRASE_DE_ACEITE = "EU ACEITO O RISCO"

#: Cada bloqueio tem nome próprio, como os motivos de recusa do portão —
#: bloqueio anônimo não vira alarme nem métrica.
BLOQUEIO_MODO = "modo_nao_e_live"
BLOQUEIO_CONFIRMACAO = "sem_confirmacao_explicita"
BLOQUEIO_ACEITE = "sem_aceite_do_risco"
BLOQUEIO_RELOGIO = "relogio_nao_sincronizado"
BLOQUEIO_CLIENTE = "sem_cliente_de_ordens"

TODOS_OS_BLOQUEIOS = frozenset(
    {
        BLOQUEIO_MODO,
        BLOQUEIO_CONFIRMACAO,
        BLOQUEIO_ACEITE,
        BLOQUEIO_RELOGIO,
        BLOQUEIO_CLIENTE,
    }
)


@dataclass(frozen=True)
class AutorizacaoParaLive:
    """Sim ou não, com TODOS os motivos e os detalhes de cada um."""

    autorizado: bool
    bloqueios: tuple[str, ...] = ()
    detalhe: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        desconhecidos = set(self.bloqueios) - TODOS_OS_BLOQUEIOS
        if desconhecidos:
            raise ValueError(
                f"bloqueio sem nome registrado: {sorted(desconhecidos)}. "
                "Todo bloqueio precisa de um nome em TODOS_OS_BLOQUEIOS."
            )
        if self.autorizado and self.bloqueios:
            raise ValueError("autorização positiva não carrega bloqueio")

    def como_dict(self) -> dict[str, Any]:
        return {
            "autorizado": self.autorizado,
            "bloqueios": list(self.bloqueios),
            "detalhe": self.detalhe,
        }

    def explicar(self) -> str:
        """Uma mensagem para o operador, com o que falta em cada linha."""
        if self.autorizado:
            return "LIVE autorizado: trava tripla completa e relógio sincronizado."
        linhas = ["LIVE NAO autorizado. Falta:"]
        for bloqueio in self.bloqueios:
            linhas.append(f"  - {bloqueio}: {self.detalhe.get(bloqueio, '')}")
        return "\n".join(linhas)


def autorizacao_para_live(
    modo: Mode,
    *,
    env: Mapping[str, str] | None = None,
    sincronia: Sincronia | None = None,
    cliente_de_ordens_existe: bool = False,
    sonda_de_sincronia: Callable[[], Sincronia] = estado_da_sincronia,
) -> AutorizacaoParaLive:
    """Junta as cinco condições e devolve todas as que faltam.

    `sincronia` pronta evita a sonda (subprocesso) quando quem chama já a
    obteve na subida — é o caso normal, porque a sonda é de deploy e não de
    caminho quente. Omitida, ela é consultada aqui.

    `cliente_de_ordens_existe` é parâmetro e não constante de propósito: no dia
    em que o cliente existir, quem o constrói passa `True`, e este módulo não
    precisa saber quem ele é.
    """
    ambiente = os.environ if env is None else env
    bloqueios: list[str] = []
    detalhe: dict[str, Any] = {}

    if modo is not Mode.LIVE:
        bloqueios.append(BLOQUEIO_MODO)
        detalhe[BLOQUEIO_MODO] = f"modo atual e {modo.value}; exige LIVE"

    confirmacao = ambiente.get(ENV_CONFIRMACAO, "")
    if confirmacao.strip() not in {"1", "true", "TRUE", "yes", "sim"}:
        bloqueios.append(BLOQUEIO_CONFIRMACAO)
        detalhe[BLOQUEIO_CONFIRMACAO] = f"defina {ENV_CONFIRMACAO}=1"

    # Comparação EXATA, sem normalizar caixa nem acento: a frase existe para
    # ser digitada com atenção, e aceitar variações desfaria o propósito.
    # Espaço nas pontas é tolerado porque é artefato de terminal, não descuido.
    if ambiente.get(ENV_ACEITE, "").strip() != FRASE_DE_ACEITE:
        bloqueios.append(BLOQUEIO_ACEITE)
        detalhe[BLOQUEIO_ACEITE] = f'defina {ENV_ACEITE}="{FRASE_DE_ACEITE}" (exato)'

    estado = sincronia if sincronia is not None else sonda_de_sincronia()
    if not estado.verificada:
        bloqueios.append(BLOQUEIO_RELOGIO)
        detalhe[BLOQUEIO_RELOGIO] = estado.detalhe
    detalhe["sincronia"] = estado.como_dict()

    if not cliente_de_ordens_existe:
        bloqueios.append(BLOQUEIO_CLIENTE)
        detalhe[BLOQUEIO_CLIENTE] = (
            "o cliente de ordens nao existe (ESTADO_PARA_LIVE 3.2 e 3.5). "
            "Nenhuma trava de intencao substitui codigo que saiba enviar."
        )

    return AutorizacaoParaLive(
        autorizado=not bloqueios,
        bloqueios=tuple(bloqueios),
        detalhe=detalhe,
    )
