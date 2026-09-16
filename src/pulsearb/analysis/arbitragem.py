"""Arbitragem por IDENTIDADE — as rotas que não precisam prever nem correr.

## Por que este módulo existe

O quadro tem seis itens ❌ medidos e reprovados, e eles reprovaram por dois
motivos que se repetem:

- **latência**: o 1.11 mediu ZERO episódios de soma-dos-lados sobrevivendo a
  300 ms, e >70 % do lucro de arbitragem em cripto vai para bots abaixo de
  100 ms. Nós medimos p50 218 ms e p99 970 ms (§16). Essa corrida está
  perdida, e mais código não a ganha.
- **previsão**: 1.1, 1.4 e 1.5 reprovaram o edge do taker, que depende de
  acertar a direção.

O que sobra são rotas em que o lucro vem de uma **identidade contábil** e não
de uma opinião sobre o futuro — e em mercados de horizonte longo, onde os
milissegundos não decidem. `docs/OUTROS_BOTS.md` §6 item 3 já tinha medido
que a janela longa bate a curta por duas ordens de grandeza (+143,96 ¢/h
contra +0,63 ¢ por 5 min), e a máquina inteira de descoberta continua
apontada para os slugs `{ativo}-updown-{dur}-{epoch}`, que são exatamente a
família reprovada.

## As duas identidades

**A cesta (neg-risk).** Num conjunto de resultados mutuamente exclusivos e
EXAUSTIVOS, exatamente um resolve em 1,00 e os outros em 0,00. Então uma
cesta com 1 share de cada YES paga **exatamente 1,00**, sempre, sem exceção
e sem hipótese nenhuma sobre o mundo. Comprar a cesta por menos de 1,00 é
lucro travado; a única pergunta é se ela existe e de que tamanho.

**A escada (monotonicidade).** Para dois mercados do mesmo ativo e mesma
data com limiares k₁ < k₂, vale `P(X ≥ k₁) ≥ P(X ≥ k₂)` — quem passa de k₂
passou de k₁. Então YES(k₁) paga sempre ≥ YES(k₂), e comprar YES(k₁) mais
barato do que se vende YES(k₂) trava a diferença.

## O que este módulo NÃO decide

Se o conjunto é mesmo exaustivo. Isso é fato de API e tem de vir
`[VERIFICADO]` da fonte (CLAUDE.md), não de parecer razoável — um conjunto
que perde um resultado não paga 1,00, e a "arbitragem" vira posição
direcional sem que nada acuse. Quem chama afirma, e o chamador de verdade
(`scripts/varredura_de_arbitragem.py`) recusa quando não consegue provar.

E não decide se a oportunidade SOBREVIVE. O 1.11 morreu aí: existir num
instantâneo não é existir quando a ordem chega. Persistência se mede
amostrando, e é o script que amostra.
"""

from __future__ import annotations

from dataclasses import dataclass

from pulsearb.backtest.book import OrderBook, simulate_taker_buy
from pulsearb.engine.fees import fee_pp_por_share

#: O que uma cesta paga, por construção, quando o conjunto é exaustivo.
PAGAMENTO_DA_CESTA = 1.0

#: Toda recusa tem nome — constante, nunca frase livre (CLAUDE.md). Recusa
#: anônima não vira métrica e não distingue "não há arbitragem" de "não
#: consegui olhar".
MOTIVOS = {
    "conjunto_nao_exaustivo": "o chamador não provou que os resultados cobrem "
                              "todo o espaço — sem isso a cesta não paga 1,00",
    "perna_sem_livro": "alguma perna não tem livro legível",
    "livro_raso": "o livro não comporta nem uma cesta inteira",
    "sem_folga": "a cesta custa 1,00 ou mais depois das taxas",
    "escada_fora_de_ordem": "os limiares não estão em ordem crescente",
    "mesma_perna": "os dois lados da escada são o mesmo token",
}


@dataclass(frozen=True)
class Taxa:
    """`rate` e `exponent` por mercado, como o `engine/fees` exige.

    Sem default de propósito: o módulo de fees diz, por escrito, que mercado
    sem fee legível NÃO é operado. Uma taxa presumida aqui viraria lucro
    presumido.
    """

    rate: float
    exponent: float


@dataclass(frozen=True)
class Oportunidade:
    """Lucro TRAVADO por cesta, e de que tamanho ele existe.

    `lucro_usdc` já desconta as taxas. `shares` é quantas cestas o livro
    comporta no ponto medido — e ele é o número que separa "existe" de
    "vale a pena", porque atravessar o livro encarece a cesta a cada nível.
    """

    shares: float
    custo_usdc: float
    taxas_usdc: float
    lucro_usdc: float
    niveis_atravessados: int

    @property
    def lucro_por_share(self) -> float:
        return self.lucro_usdc / self.shares if self.shares > 0 else 0.0


def custo_de_comprar(
    livro: OrderBook, shares: float, taxa: Taxa
) -> tuple[float, float] | None:
    """Quanto custa levar `shares` deste livro, e a taxa disso.

    `None` quando o livro não comporta a quantidade INTEIRA. Preenchimento
    parcial não serve numa cesta: uma perna faltando desfaz a identidade e o
    que sobra é posição direcional — o oposto do que esta rota existe para
    ser. É a mesma leitura que o FOK do M4 faz.
    """
    fill = simulate_taker_buy(livro, shares)
    if not fill.completo:
        return None
    # A fee é por share e depende do PREÇO de cada share. O preço médio é o
    # que o livro cobrou de fato; usá-lo aqui é a aproximação de uma casa
    # decimal que o próprio `fee_pp_por_share` permite, porque a curva é
    # suave no intervalo de um punhado de ticks.
    taxas = fill.shares * fee_pp_por_share(
        fill.preco_medio, rate=taxa.rate, exponent=taxa.exponent
    )
    return fill.custo_usdc, taxas


def oportunidade_de_cesta(
    livros: list[OrderBook],
    taxas: list[Taxa],
    *,
    shares: float,
    conjunto_exaustivo: bool,
) -> tuple[Oportunidade | None, str | None]:
    """A cesta neg-risk: comprar 1 share de cada YES e receber 1,00.

    `conjunto_exaustivo` é afirmação do chamador e não tem default. Um
    conjunto que perde um resultado não paga 1,00 — e o erro seria SILENCIOSO,
    porque a conta fecharia bonita e a posição viraria direcional.
    """
    if not conjunto_exaustivo:
        return None, "conjunto_nao_exaustivo"
    if len(livros) != len(taxas) or not livros:
        return None, "perna_sem_livro"
    if shares <= 0:
        return None, "livro_raso"

    custo_total = taxas_totais = 0.0
    niveis = 0
    for livro, taxa in zip(livros, taxas, strict=True):
        if livro is None or not livro.asks:
            return None, "perna_sem_livro"
        conta = custo_de_comprar(livro, shares, taxa)
        if conta is None:
            return None, "livro_raso"
        custo, tx = conta
        custo_total += custo
        taxas_totais += tx
        niveis += simulate_taker_buy(livro, shares).niveis_atravessados

    recebe = PAGAMENTO_DA_CESTA * shares
    lucro = recebe - custo_total - taxas_totais
    if lucro <= 0:
        return None, "sem_folga"
    return (
        Oportunidade(
            shares=shares,
            custo_usdc=custo_total,
            taxas_usdc=taxas_totais,
            lucro_usdc=lucro,
            niveis_atravessados=niveis,
        ),
        None,
    )


def maior_cesta(
    livros: list[OrderBook],
    taxas: list[Taxa],
    *,
    conjunto_exaustivo: bool,
    teto_de_shares: float,
    passo: float = 1.0,
) -> tuple[Oportunidade | None, str | None]:
    """A MAIOR cesta que ainda dá lucro, não a primeira que dá.

    Atravessar o livro encarece a cesta a cada nível, então o lucro por share
    cai com o tamanho e o lucro total tem um máximo no meio. Reportar só o
    tamanho 1 diria "existe arbitragem" sobre algo que talvez renda centavos;
    reportar o máximo diz quanto ela vale.
    """
    melhor: Oportunidade | None = None
    ultima_recusa = "livro_raso"
    tamanho = passo
    while tamanho <= teto_de_shares:
        op, motivo = oportunidade_de_cesta(
            livros, taxas, shares=tamanho, conjunto_exaustivo=conjunto_exaustivo
        )
        if op is None:
            # Uma vez sem folga, mais tamanho não devolve: o custo por share
            # só cresce. Parar aqui é a conta, não economia de tempo.
            ultima_recusa = motivo or ultima_recusa
            break
        if melhor is None or op.lucro_usdc > melhor.lucro_usdc:
            melhor = op
        tamanho += passo
    return melhor, (None if melhor is not None else ultima_recusa)


def oportunidade_de_escada(
    livro_do_limiar_baixo: OrderBook,
    livro_do_limiar_alto: OrderBook,
    taxa_baixo: Taxa,
    taxa_alto: Taxa,
    *,
    shares: float,
    token_baixo: str,
    token_alto: str,
    limiar_baixo: float,
    limiar_alto: float,
) -> tuple[Oportunidade | None, str | None]:
    """A escada: comprar YES(k₁) e VENDER YES(k₂), com k₁ < k₂.

    `P(X ≥ k₁) ≥ P(X ≥ k₂)` porque quem passa de k₂ passou de k₁. Então
    YES(k₁) paga sempre ≥ YES(k₂), e a diferença travada é o que se recebe
    por k₂ menos o que se paga por k₁. **Nunca é negativa no vencimento** —
    é essa a propriedade que faz disto arbitragem e não aposta em spread.

    `limiar_baixo` e `limiar_alto` entram para SEREM CONFERIDOS: os livros
    não carregam o limiar deles, e trocar os dois inverte a desigualdade sem
    que a conta acuse nada.

    Vender YES(k₂) aqui é atravessar os BIDS dele. O `simulate_taker_buy`
    anda pelos asks, então o lado vendido é lido direto dos níveis — e um
    livro sem bids não vende, o que é `livro_raso` e não lucro zero.
    """
    if token_baixo == token_alto:
        return None, "mesma_perna"
    if not limiar_baixo < limiar_alto:
        # OS LIMIARES ENTRAM E SÃO CONFERIDOS AQUI, e não é zelo: a função
        # recebe LIVROS, e livro não diz de que limiar ele é. Com os dois
        # trocados a desigualdade se inverte — YES(k₂) passa a pagar ≤
        # YES(k₁) — e a conta abaixo devolveria "lucro travado" sobre uma
        # posição que perde exatamente quando o preço anda contra. Seria o
        # mesmo modo de falha do `conjunto_nao_exaustivo`: arbitragem virando
        # aposta sem nada acusar.
        return None, "escada_fora_de_ordem"

    compra = custo_de_comprar(livro_do_limiar_baixo, shares, taxa_baixo)
    if compra is None:
        return None, "livro_raso" if livro_do_limiar_baixo.asks else "perna_sem_livro"
    custo, taxas_compra = compra

    if not livro_do_limiar_alto.bids:
        return None, "perna_sem_livro"
    restante, receita, niveis_venda = shares, 0.0, 0
    for preco, tamanho in livro_do_limiar_alto.bids:
        if restante <= 0:
            break
        levado = min(restante, tamanho)
        receita += levado * preco
        restante -= levado
        niveis_venda += 1
    if restante > 1e-9:
        return None, "livro_raso"

    taxas_venda = shares * fee_pp_por_share(
        receita / shares, rate=taxa_alto.rate, exponent=taxa_alto.exponent
    )
    lucro = receita - custo - taxas_compra - taxas_venda
    if lucro <= 0:
        return None, "sem_folga"
    return (
        Oportunidade(
            shares=shares,
            custo_usdc=custo,
            taxas_usdc=taxas_compra + taxas_venda,
            lucro_usdc=lucro,
            niveis_atravessados=(
                simulate_taker_buy(livro_do_limiar_baixo, shares).niveis_atravessados
                + niveis_venda
            ),
        ),
        None,
    )
