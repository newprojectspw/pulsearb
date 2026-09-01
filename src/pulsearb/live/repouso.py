"""Item 4.0 (c) — a cotação que REPOUSA, e quando mexer nela.

O taker decide uma vez e acabou: manda, preenche ou não, fim. O maker deixa a
cotação no livro e o livro **anda embaixo dela**. Isso cria uma decisão que
não existia antes — *mexer ou deixar?* — e é ela que este módulo responde.

POR QUE NÃO BASTA RECALCULAR E REPOR
─────────────────────────────────────
A resposta ingênua é recomputar a melhor cotação a cada tick e repor se mudou.
Ela está errada por duas razões que se somam:

**1. Reposicionar custa a fila.** Cancelar e recolocar joga a ordem para o fim
da fila do novo preço. A fila não é observável no WS agregado (é a mesma
limitação que trava o 1.6), então o custo não é mensurável aqui — mas ser
imensurável não é ser zero, e uma política que o ignora vai reposicionar
sempre, porque o ganho aparece no número e o custo não.

**2. O livro pisca.** Níveis aparecem e somem entre snapshots. Uma regra que
reage a toda mudança persegue ruído, e o resultado é uma cotação que passa
mais tempo na fila de trás do que pontuando.

Daí a **histerese**: só mexe quando o ganho estimado passa de um piso, e nunca
antes de um tempo mínimo repousada. As duas travas atacam coisas diferentes —
a primeira filtra melhoria irrelevante, a segunda filtra oscilação rápida —, e
tirar qualquer uma das duas deixa o outro modo de falha aberto.

O QUE ESTE MÓDULO NÃO FAZ
──────────────────────────
Não cancela nem envia: devolve a decisão, e quem tem o cliente executa. Não
sabe a posição da nossa ordem na fila — ninguém sabe. Não decide ONDE cotar,
que é o `live/cotacao.py`; aqui a pergunta é só se vale trocar o que já está
lá pelo que aquele módulo sugere agora.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pulsearb.live.cotacao import Cotacao, RetornoEstimado

#: Ganho líquido mínimo, em USDC, para justificar perder a fila. Abaixo disto
#: a troca é ruído: o ganho é estimado com hipótese de captura, e o custo (a
#: fila) não é medido — trocar por pouco é apostar que a parte estimada vale
#: mais que a parte desconhecida.
GANHO_MINIMO_USDC = 0.50

#: Tempo mínimo repousada antes de considerar mexer. Ataca o piscar do livro,
#: que a trava de ganho sozinha não pega: uma melhoria pode passar do piso e
#: sumir no snapshot seguinte.
SEGUNDOS_MINIMOS_REPOUSADA = 30.0


class AcaoNaCotacao(StrEnum):
    """O que fazer com a cotação que está no livro."""

    MANTER = "manter"
    REPOSICIONAR = "reposicionar"
    #: Sair sem repor: a cotação atual deixou de pontuar e não há candidata
    #: que pontue. Ficar seria pagar risco de execução por zero reward.
    CANCELAR = "cancelar"


@dataclass(frozen=True, slots=True)
class CotacaoAberta:
    """O que está repousando no livro, do nosso lado."""

    cotacao: Cotacao
    #: Epoch em que ela foi colocada. É o relógio de quem chama, não o nosso:
    #: o módulo não lê tempo, para poder ser testado sem esperar.
    desde_epoch: float
    #: Id do cliente, para cancelar. Opaco aqui de propósito.
    id_do_cliente: str = ""


@dataclass(frozen=True, slots=True)
class Decisao:
    """A decisão, com o porquê junto — para o diário do SHADOW."""

    acao: AcaoNaCotacao
    motivo: str
    nova: Cotacao | None = None
    ganho_estimado_usdc: float = 0.0


#: Motivos nomeados. Mesma regra do `risk/gates.py`: recusa (ou ação) sem nome
#: não vira métrica nem alarme, e não distingue "está estável" de "está preso".
MOTIVOS = (
    "sem_candidata_que_pontue",
    "atual_nao_pontua_mais",
    "repousada_ha_pouco_tempo",
    "ganho_abaixo_do_piso",
    "ganho_justifica_perder_a_fila",
    "estavel",
)


def decidir(
    aberta: CotacaoAberta | None,
    melhor_agora: RetornoEstimado | None,
    retorno_da_atual: RetornoEstimado | None,
    *,
    agora_epoch: float,
    ganho_minimo_usdc: float = GANHO_MINIMO_USDC,
    segundos_minimos: float = SEGUNDOS_MINIMOS_REPOUSADA,
) -> Decisao:
    """Mexer na cotação que está no livro, ou deixar?

    `melhor_agora` é o que o `live/cotacao.py` sugere para o livro corrente;
    `retorno_da_atual` é quanto a cotação JÁ COLOCADA renderia neste mesmo
    livro. Comparar as duas no livro de agora é o ponto: comparar contra o
    retorno estimado no momento em que ela foi colocada mediria a mudança do
    livro, não a vantagem de trocar.

    Sem nada aberto, a decisão é entrar (ou não). Com algo aberto, as travas
    de histerese entram na ordem em que aparecem no corpo — e a ordem importa:
    "não pontua mais" precisa vencer "repousada há pouco tempo", senão uma
    cotação morta fica presa pelo tempo mínimo.
    """
    if aberta is None:
        if melhor_agora is None:
            return Decisao(AcaoNaCotacao.MANTER, "sem_candidata_que_pontue")
        return Decisao(
            AcaoNaCotacao.REPOSICIONAR,
            "ganho_justifica_perder_a_fila",
            nova=melhor_agora.cotacao,
            ganho_estimado_usdc=melhor_agora.liquido_usdc,
        )

    # A atual morreu? Sai na frente de qualquer histerese: manter uma cotação
    # que não pontua é pagar risco de execução por zero reward, e esperar o
    # tempo mínimo para descobrir isso é esperar pelo pior dos dois mundos.
    atual_morta = retorno_da_atual is None or not retorno_da_atual.pontua
    if atual_morta:
        if melhor_agora is None:
            return Decisao(AcaoNaCotacao.CANCELAR, "atual_nao_pontua_mais")
        return Decisao(
            AcaoNaCotacao.REPOSICIONAR,
            "atual_nao_pontua_mais",
            nova=melhor_agora.cotacao,
            ganho_estimado_usdc=melhor_agora.liquido_usdc,
        )

    if melhor_agora is None:
        # A atual pontua e não há candidata melhor calculada: ficar é o certo.
        return Decisao(AcaoNaCotacao.MANTER, "estavel")

    if melhor_agora.cotacao == aberta.cotacao:
        return Decisao(AcaoNaCotacao.MANTER, "estavel")

    if agora_epoch - aberta.desde_epoch < segundos_minimos:
        return Decisao(AcaoNaCotacao.MANTER, "repousada_ha_pouco_tempo")

    ganho = melhor_agora.liquido_usdc - retorno_da_atual.liquido_usdc
    if ganho < ganho_minimo_usdc:
        return Decisao(
            AcaoNaCotacao.MANTER, "ganho_abaixo_do_piso", ganho_estimado_usdc=ganho
        )

    return Decisao(
        AcaoNaCotacao.REPOSICIONAR,
        "ganho_justifica_perder_a_fila",
        nova=melhor_agora.cotacao,
        ganho_estimado_usdc=ganho,
    )
