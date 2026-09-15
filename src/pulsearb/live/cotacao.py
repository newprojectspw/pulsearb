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

import math
from dataclasses import dataclass
from typing import Any

from pulsearb.analysis.rewards import (
    ParametrosDeReward,
    combinar_lados,
    denominador_pessimista,
    score_de_nivel,
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
class AncoraDoMicroprice:
    """Até onde o microprice deixa a cotação chegar, em coordenadas do livro
    do Up: `bid` é o microprice do Up, `ask` é o do Down espelhado
    (`1 − microprice do Down`), e `ticks` é a folga exigida de cada um.

    A âncora só APERTA: o bid nunca fica acima de `bid − ticks`, o ask nunca
    abaixo de `ask + ticks`. Nunca afrouxa o que a distância do meio decidiu —
    é a regra dos três bots públicos, que nunca melhoram o topo
    (`docs/OUTROS_BOTS.md` §6, item 6).

    Perna sem microprice entra como `None` e não ancora aquela perna; quem
    liga a regra decide se isso autoriza cotar (o laço NÃO autoriza).
    """

    bid: float | None = None
    ask: float | None = None
    ticks: int = 1

    def __post_init__(self) -> None:
        if self.ticks < 0:
            # Falha na CONSTRUÇÃO, não no envio: `ticks` negativo mandaria a
            # cotação para CIMA do microprice — o oposto do que a âncora
            # existe para fazer, e em silêncio. Mesma escolha do
            # `tipo_de_ordem` desconhecido no cliente (3.5).
            raise ValueError(f"ticks da ancora nao pode ser negativo: {self.ticks}")

    def limite(self, tick_size: float, *, do_lado_bid: bool) -> float | None:
        """O preço-limite que esta âncora impõe ao lado pedido, ou `None`."""
        micro = self.bid if do_lado_bid else self.ask
        if micro is None:
            return None
        folga = self.ticks * tick_size
        return micro - folga if do_lado_bid else micro + folga

    def no_livro_do_down(self) -> AncoraDoMicroprice:
        """A mesma âncora vista do livro do DOWN, onde a segunda perna é um
        bid: o bid de lá é o ask daqui espelhado. É o mesmo espelho que o
        `meio` e o portão já usam, e é o que mantém o preço avaliado e o
        preço enviado idênticos."""
        return AncoraDoMicroprice(
            bid=None if self.ask is None else 1.0 - self.ask, ticks=self.ticks
        )


@dataclass(frozen=True, slots=True)
class Cotacao:
    """Uma cotação maker candidata: onde repousar, e de quanto."""

    distancia_ticks: int
    tamanho: float
    dois_lados: bool = True

    def preco(
        self,
        meio: float,
        tick_size: float,
        *,
        do_lado_bid: bool,
        ancora: AncoraDoMicroprice | None = None,
    ) -> float:
        """O preço da cotação, JÁ na grade do tick. Bid fica ABAIXO do meio;
        ask, acima.

        O meio do livro pode cair entre dois ticks (bid 0,45 / ask 0,46 →
        meio 0,455), e o CLOB só aceita preço na grade (§4). Um bid a
        `meio − recuo` fora da grade não existe como ordem — então o bid
        arredonda para BAIXO e o ask para CIMA, o lado conservador nos dois:
        a ordem fica a pelo menos `distancia_ticks` do meio, nunca mais perto.
        É aqui, e não em quem envia, para que o score estimado
        (`estimar_retorno`) e a ordem colocada olhem o MESMO preço.

        `ancora`, quando vem, é o microprice: o preço só pode RECUAR dele,
        nunca avançar. É por isso que ela entra aqui dentro — o score e a
        ordem têm de ver o mesmo recuo, senão a cotação seria escolhida por um
        preço e enviada por outro (o modo de falha do §6.1b).
        """
        recuo = self.distancia_ticks * tick_size
        bruto = meio - recuo if do_lado_bid else meio + recuo
        if ancora is not None:
            limite = ancora.limite(tick_size, do_lado_bid=do_lado_bid)
            if limite is not None:
                # A âncora só aperta: o lado conservador de cada perna.
                bruto = min(bruto, limite) if do_lado_bid else max(bruto, limite)
        # O epsilon segura o erro binário (0,50 − 0,01 = 0,48999…) que faria o
        # `floor` descer um tick a mais do que o pedido.
        passos = bruto / tick_size
        na_grade = math.floor(passos + 1e-9) if do_lado_bid else math.ceil(passos - 1e-9)
        return round(na_grade * tick_size, 6)


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
    ancora: AncoraDoMicroprice | None = None,
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
    precos = [0.0, 0.0]
    tamanhos = [0.0, 0.0]
    for i, do_lado_bid in enumerate((True, False)[:lados]):
        precos[i] = cotacao.preco(
            meio, params.tick_size, do_lado_bid=do_lado_bid, ancora=ancora
        )
        tamanhos[i] = cotacao.tamanho
    return _retorno_a_precos(
        cotacao,
        livro,
        params,
        preco_bid=precos[0],
        preco_ask=precos[1],
        tamanho_bid=tamanhos[0],
        tamanho_ask=tamanhos[1],
        horas=horas,
        fator_de_captura=fator_de_captura,
        markout_centavos=markout_centavos,
    )


def estimar_retorno_repousando(
    aberta: Any,
    livro: OrderBook,
    params: ParametrosDeReward,
    *,
    horas: float,
    fator_de_captura: float = FATOR_DE_CAPTURA_PADRAO,
    markout_centavos: float = MARKOUT_CENTAVOS_POR_SHARE,
    restante_up: float | None = None,
    restante_down: float | None = None,
) -> RetornoEstimado | None:
    """O que a cotação JÁ COLOCADA rende AGORA — no preço em que ela repousa.

    `estimar_retorno` avalia uma candidata: reposiciona a cotação a
    `distancia_ticks` do meio DESTE livro. Uma ordem já enviada não anda com
    o meio — ela fica em `aberta.preco_up` (bid no Up) e `aberta.preco_down`
    (bid no Down, que no livro do Up é o ask a `1 − preco_down`). Avaliar a
    ordem repousando com a conta da candidata pagava reward a uma ordem que o
    meio já tinha deixado fora da faixa, e nunca a via "não pontuar mais"
    (revisão do #118). Aqui o score sai dos preços ENVIADOS contra o meio de
    agora — mesma `score_de_nivel`, mesmo `combinar_lados`, mesmo denominador.

    `restante_*` é quanto de cada perna ainda está no livro na conta-sombra
    (a `CaixaDoMaker` consome as pernas pelos prints); `None` = a perna
    inteira. Perna sem preço (0) ou sem restante não pontua.
    """
    meio = livro.mid
    if meio is None:
        return None
    cotacao = aberta.cotacao
    tamanho_up = cotacao.tamanho if restante_up is None else max(0.0, restante_up)
    tamanho_down = cotacao.tamanho if restante_down is None else max(0.0, restante_down)
    preco_bid = float(aberta.preco_up or 0.0)
    preco_ask = 1.0 - float(aberta.preco_down) if aberta.preco_down else 0.0
    if not cotacao.dois_lados:
        preco_ask, tamanho_down = 0.0, 0.0
    return _retorno_a_precos(
        cotacao,
        livro,
        params,
        preco_bid=preco_bid,
        preco_ask=preco_ask,
        tamanho_bid=tamanho_up if preco_bid > 0.0 else 0.0,
        tamanho_ask=tamanho_down if preco_ask > 0.0 else 0.0,
        horas=horas,
        fator_de_captura=fator_de_captura,
        markout_centavos=markout_centavos,
    )


def _retorno_a_precos(
    cotacao: Cotacao,
    livro: OrderBook,
    params: ParametrosDeReward,
    *,
    preco_bid: float,
    preco_ask: float,
    tamanho_bid: float,
    tamanho_ask: float,
    horas: float,
    fator_de_captura: float,
    markout_centavos: float,
) -> RetornoEstimado | None:
    """A conta de `estimar_retorno` a partir dos PREÇOS e TAMANHOS de cada
    lado. É o único lugar onde a fórmula mora: a candidata e a ordem que já
    repousa passam por aqui — mesmo caminho."""
    meio = livro.mid
    if meio is None:
        return None
    por_lado = [0.0, 0.0]
    if tamanho_bid > 0:
        por_lado[0] = score_de_nivel(preco_bid, tamanho_bid, meio=meio, params=params)
    if tamanho_ask > 0:
        por_lado[1] = score_de_nivel(preco_ask, tamanho_ask, meio=meio, params=params)
    lados = sum(1 for tam in (tamanho_bid, tamanho_ask) if tam > 0)
    # Os dois lados NÃO se somam: §15.3 combina por `Q_min`, e cotação de um
    # lado só vale um terço dentro da faixa e ZERO fora dela. Somar era pagar
    # a cotação de um lado como se fossem dois.
    proprio = combinar_lados(por_lado[0], por_lado[1], meio=meio)

    # O denominador inclui o nosso próprio score: entrar no livro aumenta o
    # total, e ignorar isso superestimaria a fatia — o erro fica maior
    # justamente quando a cotação é grande, que é quando ela importa. E é o
    # TETO do que os outros makers somam em `Q_min`, para que a fatia saia
    # como piso — ver `denominador_pessimista`.
    do_livro = denominador_pessimista(livro, params)
    total = do_livro + proprio
    fracao = proprio / total if total > 0 else 0.0

    rewards = params.daily_rate * (horas / 24.0) * fracao * fator_de_captura

    # Execuções assumidas: a mesma fração do pool, aplicada ao tamanho que
    # deixamos exposto. É grosseiro e está declarado como tal — sem posição na
    # fila não há como fazer melhor, e um número mais elaborado aqui daria
    # falsa precisão a uma hipótese.
    shares_executadas = (tamanho_bid + tamanho_ask) * fracao * fator_de_captura
    del lados  # contado só para deixar explícito que é por perna com tamanho
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
    ancora: AncoraDoMicroprice | None = None,
) -> RetornoEstimado | None:
    """A melhor candidata pelo líquido, ou `None` se nenhuma pontua.

    **Não inventa candidata.** Quem chama passa a grade que quer avaliar, e o
    módulo não decide sozinho que uma distância não oferecida seria melhor —
    varrer o espaço inteiro aqui dentro esconderia, de quem lê o resultado,
    qual grade foi de fato considerada.

    Empate resolve pela cotação mais LONGE do meio: mesmo líquido com menos
    exposição a execução adversa é a mesma aposta com menos risco, e o
    markout é medido enquanto a fila é hipótese. Com âncora, várias candidatas
    podem colapsar no MESMO preço (todas presas ao microprice) — aí o empate
    é real e o desempate não muda a ordem que sai.

    **Nenhuma pontuando devolve `None`, também com âncora.** Se o microprice
    empurrar a cotação para fora da faixa de reward, o certo é não cotar: a
    cotação que não pontua paga risco de execução por zero.
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
                ancora=ancora,
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
