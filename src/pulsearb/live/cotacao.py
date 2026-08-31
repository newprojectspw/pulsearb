"""Item 4.0 — ONDE colocar a cotação maker, e de quanto.

O motor ao vivo (`live/motor.py`) é taker: ele ordena a `best_ask` e paga o
spread. A rota maker é o oposto — deixa a cotação repousando no livro e ganha
por estar lá. Isso exige uma decisão que o taker nunca precisou tomar: **a que
distância do meio, e com que tamanho.**

O TRADE-OFF, QUE É A RAZÃO DE ESTE MÓDULO EXISTIR
──────────────────────────────────────────────────
A fórmula de score é `S(v, s) = ((v − s)/v)² × b` (API_NOTES §15.3,
CONFIRMADA). Ela é decrescente em `s`: quanto mais perto do meio, mais pontua,
e o ganho é **quadrático**. A leitura ingênua é "cote no tick mais próximo
sempre".

O que a leitura ingênua ignora é que cotação perto do meio é a que **executa
primeiro** — e executar como maker significa que alguém quis o outro lado
naquele instante, o que em média é a ponta errada. É o `markout` que o M2 já
mede: **−0,1974 ¢/share** no recorte total (1.7).

Então o retorno de uma cotação tem dois termos de sinais opostos:

    retorno = rewards(distância) − markout × execuções(distância)

e o `distancia_ticks` que maximiza o primeiro é o que maximiza o segundo.

O QUE ESTE MÓDULO **NÃO** SABE, E POR QUE ISSO ESTÁ NO NOME DAS COISAS
───────────────────────────────────────────────────────────────────────
**A posição na fila.** O WS entrega níveis AGREGADOS, não ordens: dá para
saber quanto tamanho existe num preço, não quantas ordens nem em que ordem
elas chegaram. Então `fracao_do_pool` é uma ESTIMATIVA pro-rata — a nossa
fatia do score total —, e o quanto dela vira execução de verdade depende de
onde a nossa ordem está na fila, que ninguém aqui pode afirmar.

Por isso o retorno sai como `RetornoEstimado`, com o `fator_de_captura`
explícito em vez de embutido: quem lê decide se 0,3 é conservador o bastante.
É o mesmo fator com que o 1.6 foi avaliado.

**Nada aqui envia ordem.** É decisão pura, sem I/O — testável no tempo que se
quiser, como o `live/motor.py`.
"""

from __future__ import annotations

from dataclasses import dataclass

from pulsearb.analysis.rewards import (
    ParametrosDeReward,
    score_de_nivel,
    score_do_livro,
)
from pulsearb.backtest.book import OrderBook

#: Fatia do pool que assumimos capturar, por snapshot de pontuação. Não é
#: medida: é a hipótese conservadora com que o critério 1.6 foi avaliado, e
#: existe porque a posição na fila não é observável. Ver o cabeçalho.
FATOR_DE_CAPTURA_PADRAO = 0.3

#: Markout medido no M2 (critério 1.7), em CENTAVOS por share executada.
#: Negativo = perdemos contra quem nos executou. Entra na conta com o sinal
#: que tem; inverter aqui seria transformar um custo em receita.
MARKOUT_CENTAVOS_POR_SHARE = -0.1974


@dataclass(frozen=True, slots=True)
class Cotacao:
    """Uma cotação maker candidata: onde repousar, e de quanto."""

    distancia_ticks: int
    tamanho: float
    dois_lados: bool = True

    def preco(self, meio: float, tick_size: float, *, do_lado_bid: bool) -> float:
        """O preço da cotação. Bid fica ABAIXO do meio; ask, acima."""
        recuo = self.distancia_ticks * tick_size
        return meio - recuo if do_lado_bid else meio + recuo


@dataclass(frozen=True, slots=True)
class RetornoEstimado:
    """O que uma cotação renderia — com as parcelas separadas de propósito.

    Somar antes de publicar esconderia que uma delas é medida (`markout`) e a
    outra é estimativa com hipótese de fila (`rewards`). Quem lê precisa poder
    desconfiar de uma sem desconfiar da outra.
    """

    cotacao: Cotacao
    score_proprio: float
    score_total_do_livro: float
    fracao_do_pool: float
    rewards_usdc: float
    custo_de_markout_usdc: float
    fator_de_captura: float

    @property
    def liquido_usdc(self) -> float:
        return self.rewards_usdc - self.custo_de_markout_usdc

    @property
    def pontua(self) -> bool:
        """A cotação pontua? Tamanho abaixo do mínimo ou fora do spread, não."""
        return self.score_proprio > 0.0


def estimar_retorno(
    cotacao: Cotacao,
    livro: OrderBook,
    params: ParametrosDeReward,
    *,
    horas: float,
    fator_de_captura: float = FATOR_DE_CAPTURA_PADRAO,
    markout_centavos: float = MARKOUT_CENTAVOS_POR_SHARE,
) -> RetornoEstimado | None:
    """Quanto esta cotação renderia, em USDC, no período dado.

    Devolve `None` quando o livro não tem meio — sem meio não há distância ao
    meio, e inventar uma produziria score para uma cotação que não se sabe
    onde está.

    **O score sai da MESMA função do backtest** (`score_de_nivel`), e não de
    uma cópia: duas implementações da fórmula fariam a decisão ao vivo e a
    medição sobre gravação discordarem sobre o próprio score — o defeito que a
    regra do *mesmo caminho* existe para impedir.
    """
    meio = livro.mid
    if meio is None:
        return None

    lados = 2 if cotacao.dois_lados else 1
    proprio = 0.0
    for do_lado_bid in (True, False)[:lados]:
        preco = cotacao.preco(meio, params.tick_size, do_lado_bid=do_lado_bid)
        proprio += score_de_nivel(
            preco, cotacao.tamanho, meio=meio, params=params
        )

    # O denominador inclui o nosso próprio score: entrar no livro aumenta o
    # total, e ignorar isso superestimaria a fatia — o erro fica maior
    # justamente quando a cotação é grande, que é quando ela importa.
    do_livro = score_do_livro(livro, params)
    total = do_livro + proprio
    fracao = proprio / total if total > 0 else 0.0

    rewards = params.daily_rate * (horas / 24.0) * fracao * fator_de_captura

    # Execuções assumidas: a mesma fração do pool, aplicada ao tamanho que
    # deixamos exposto. É grosseiro e está declarado como tal — sem posição na
    # fila não há como fazer melhor, e um número mais elaborado aqui daria
    # falsa precisão a uma hipótese.
    shares_executadas = cotacao.tamanho * lados * fracao * fator_de_captura
    custo = -markout_centavos / 100.0 * shares_executadas

    return RetornoEstimado(
        cotacao=cotacao,
        score_proprio=proprio,
        score_total_do_livro=total,
        fracao_do_pool=fracao,
        rewards_usdc=rewards,
        custo_de_markout_usdc=custo,
        fator_de_captura=fator_de_captura,
    )


def escolher_cotacao(
    candidatas: list[Cotacao],
    livro: OrderBook,
    params: ParametrosDeReward,
    *,
    horas: float,
    fator_de_captura: float = FATOR_DE_CAPTURA_PADRAO,
    markout_centavos: float = MARKOUT_CENTAVOS_POR_SHARE,
) -> RetornoEstimado | None:
    """A melhor candidata pelo líquido, ou `None` se nenhuma pontua.

    **Não inventa candidata.** Quem chama passa a grade que quer avaliar, e o
    módulo não decide sozinho que uma distância não oferecida seria melhor —
    varrer o espaço inteiro aqui dentro esconderia, de quem lê o resultado,
    qual grade foi de fato considerada.

    Empate resolve pela cotação mais LONGE do meio: mesmo líquido com menos
    exposição a execução adversa é a mesma aposta com menos risco, e o
    markout é medido enquanto a fila é hipótese.
    """
    avaliadas = [
        r
        for c in candidatas
        if (
            r := estimar_retorno(
                c,
                livro,
                params,
                horas=horas,
                fator_de_captura=fator_de_captura,
                markout_centavos=markout_centavos,
            )
        )
        is not None
        and r.pontua
    ]
    if not avaliadas:
        return None
    return max(
        avaliadas,
        key=lambda r: (r.liquido_usdc, r.cotacao.distancia_ticks),
    )
