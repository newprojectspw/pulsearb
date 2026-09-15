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
    #: Nosso id determinístico, para o diário e a reconciliação. Opaco aqui.
    id_do_cliente: str = ""
    #: Id da ordem no lado DELES — é por ele que se cancela (`cancelar`, §4.4).
    #: Vazio enquanto o envio não voltou `ACEITA` com um `orderID`: uma cotação
    #: sem este id não pode ser cancelada por id, e a `execucao_maker` trata
    #: isso explicitamente em vez de mandar um cancelamento vazio para o fio.
    order_id: str = ""
    #: A segunda perna, quando a cotação é de DOIS lados (§15.3): o bid no
    #: token Down, que no livro do Up aparece como o ask. Vazios numa cotação
    #: de um lado só. Mesma regra do `order_id`: perna com `id_do_cliente` e
    #: sem `order_id` é perna em estado desconhecido, não perna inexistente.
    id_do_cliente_down: str = ""
    order_id_down: str = ""
    #: O preço a que cada perna foi ENVIADA (o `preco_limite` da ordem), e não
    #: recalculado do meio: é contra ele que a caixa-sombra (`caixa_maker`)
    #: confere os prints de negócio — um print no nosso preço ou através dele
    #: é execução que teria acontecido. Zero numa cotação sem a perna.
    preco_up: float = 0.0
    preco_down: float = 0.0

    @property
    def order_ids(self) -> tuple[str, ...]:
        """Os ids que repousam no servidor — por eles se cancela e reconcilia."""
        return tuple(i for i in (self.order_id, self.order_id_down) if i)


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
    #: Só com a âncora do microprice ligada (4.0 (f)).
    "acima_do_microprice",
)


#: Tolerância de preço (o nosso preço vem da grade do tick; o do livro, de
#: decimal em string).
_EPS_PRECO = 1e-9


@dataclass(frozen=True, slots=True)
class AncoraEmVigor:
    """O que a âncora do microprice impõe a ESTA passada, em duas metades que
    NÃO são a mesma coisa.

    `microprice` é o de cada perna, no preço DELA: é o **limite duro** —
    repousar acima do microprice é ser a opção grátis que o taker leva, e é o
    estado que a âncora existe para não produzir. `precos` é onde a candidata
    repousaria, `ticks` abaixo dele: é a **preferência de colocação**, e
    perder folga não é emergência nenhuma.

    Separar as duas foi o conserto de um defeito meu: tratar a folga como se
    fosse o limite duro punha a ordem acima do "teto" a CADA passo de 1 tick
    do meio, e como essa trava vence a histerese, o maker trocava a cotação
    em 39 de 40 passadas de 15 min — todas antes do repouso mínimo, contra
    2,05 com a regra desligada (medido, `/tmp/churn.py`). Reposicionar custa a
    fila, e nem a simulação do `maker_de_pares` nem o SHADOW enxergam esse
    custo: seria pagá-lo às cegas.
    """

    microprice: tuple[float, float]
    precos: tuple[float, float] | None = None


def _acima_do_microprice(
    ancora: AncoraEmVigor | None, aberta: CotacaoAberta
) -> bool:
    """A ordem que repousa ficou ACIMA do microprice — o limite duro?

    Sem âncora isto é sempre falso e nada muda.

    Acima do microprice a ordem é a opção grátis: o livro está indo para lá, e
    quem nos executa leva o movimento. Continua pontuando — logo
    `atual_nao_pontua_mais` não a pega — e a candidata ancorada pontua MENOS
    que ela, então o piso de ganho jamais aprovaria a troca. Por isso esta
    trava vence a histerese, como o "não pontua mais": deixá-la para o piso
    seria deixá-la nunca (revisão do Codex, #127).

    O que ela NÃO faz é vigiar a folga de `ticks`: uma ordem 1 tick menos
    folgada do que se queria continua abaixo do microprice, e tratá-la como
    emergência era o que fazia o maker trocar a cotação a cada tick do meio.
    """
    if ancora is None:
        return False
    return (
        aberta.preco_up > ancora.microprice[0] + _EPS_PRECO
        or aberta.preco_down > ancora.microprice[1] + _EPS_PRECO
    )


def _a_ancora_mudou_o_preco(
    ancora: AncoraEmVigor | None, aberta: CotacaoAberta
) -> bool:
    """A MESMA cotação repousaria hoje noutro preço?

    O caso inverso do `_acima_do_teto`: o teto AFROUXA (o topo do livro
    engorda de novo), a ordem que repousa continua abaixo dele — então não há
    risco a corrigir — mas a candidata volta a caber mais perto do meio. Sem
    isto o atalho do `estavel` daria a mesma `Cotacao` como igual e a ordem
    ficaria presa no preço de fora para sempre: uma catraca que só anda para
    longe do meio, pontuando menos a cada aperto (revisão do Codex, #127;
    caso achado por busca sobre o próprio código, 186 combinações de topo).

    Isto só ABRE o caminho. Aqui não há risco, só oportunidade — então quem
    decide são a histerese de tempo e o piso de ganho, como em qualquer outra
    troca.
    """
    if ancora is None or ancora.precos is None:
        return False
    return (
        abs(ancora.precos[0] - aberta.preco_up) > _EPS_PRECO
        or abs(ancora.precos[1] - aberta.preco_down) > _EPS_PRECO
    )


def decidir(
    aberta: CotacaoAberta | None,
    melhor_agora: RetornoEstimado | None,
    retorno_da_atual: RetornoEstimado | None,
    *,
    agora_epoch: float,
    ganho_minimo_usdc: float = GANHO_MINIMO_USDC,
    segundos_minimos: float = SEGUNDOS_MINIMOS_REPOUSADA,
    ancora: AncoraEmVigor | None = None,
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

    `ancora` é o que a âncora do microprice impõe a esta passada — o limite
    duro de cada perna e o preço em que a candidata repousaria —, e só vem
    quando ela está ligada. Ver `_acima_do_microprice` e
    `_a_ancora_mudou_o_preco`.
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

    # A âncora é regra de SEGURANÇA, não de ganho: uma ordem ACIMA do
    # microprice é a opção grátis que ela existe para não dar. Vence a
    # histerese e o piso, como o "não pontua mais" logo acima, e pela mesma
    # razão de ordem — deixá-la para o piso de ganho seria deixá-la nunca,
    # porque a candidata ancorada pontua menos que a que já repousa. A FOLGA
    # de `ticks` não entra aqui: ver `_acima_do_microprice`.
    if _acima_do_microprice(ancora, aberta):
        if melhor_agora is None:
            return Decisao(AcaoNaCotacao.CANCELAR, "acima_do_microprice")
        return Decisao(
            AcaoNaCotacao.REPOSICIONAR,
            "acima_do_microprice",
            nova=melhor_agora.cotacao,
            ganho_estimado_usdc=melhor_agora.liquido_usdc,
        )

    if melhor_agora is None:
        # A atual pontua e não há candidata melhor calculada: ficar é o certo.
        return Decisao(AcaoNaCotacao.MANTER, "estavel")

    if melhor_agora.cotacao == aberta.cotacao and not _a_ancora_mudou_o_preco(
        ancora, aberta
    ):
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
