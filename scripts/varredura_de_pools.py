"""Onde o programa de rewards paga, e quanto dele sobraria para nós.

    .venv/bin/python scripts/varredura_de_pools.py --top 200 \
        --json relatorios/POOLS_20260913.json

## A pergunta, e por que ela não é "qual pool é maior"

A rodada de 24 h fez 44.430 avaliações maker e nenhuma cotação repousou, com
motivo ÚNICO `sem_pool_de_reward`: os mercados Up/Down que este bot observa
não participam do programa. O reward existe — só não está onde o bot opera.

Mas **pool grande não é mercado bom**. O reward é rateado pro-rata pelo score
(§15.3: `S(v,s) = ((v-s)/v)² × tamanho`), então o que decide é

    receita = (nosso score / score total) × pool

e um pool de 1.000 USDC/dia dividido com 50 market makers profissionais paga
menos por share que um de 50 USDC/dia num livro vazio. `orcamento_por_unidade_
de_score` é essa razão: **quanto o mercado paga por unidade de liquidez**.
Quanto MAIOR, mais fraca a concorrência.

## De onde vem cada número

- `GET /rewards/markets/current` — a lista do PRÓPRIO CLOB dos mercados no
  programa, com `total_daily_rate`, `rewards_max_spread` e `rewards_min_size`.
  É a fonte autoritativa; a varredura anterior amostrou a Gamma e viu uma
  fração do universo.
- `GET /markets/{condition_id}` — tokens e `minimum_tick_size`.
- `GET /book?token_id=` — o livro, de onde sai o score concorrente.

A conta de score usa `analysis/rewards.score_do_livro` e `score_da_ordem` — as
MESMAS funções do backtest e do motor ao vivo. Reimplementá-las aqui faria uma
varredura que concorda consigo mesma e discorda do bot.

## O que esta medição NÃO diz

**Ela mede receita, nunca lucro.** O custo do maker é a seleção adversa
(markout), e o markout medido — −0,2838 c/share sobre 595.537 execuções — vem
de mercados Up/Down de 5 minutos. Em mercado de horizonte longo a seleção
adversa é OUTRA, e não foi medida. Aplicar aquele número aqui seria transportar
uma medição para fora do regime em que ela foi feita.

Então o que sai é um **teto**, com a mesma honestidade do rebate no 1.6: a
receita é estimada com hipótese de fila (o WS entrega níveis agregados, não
ordens), e o custo está ausente. Um número alto aqui autoriza MEDIR o markout
naquele regime — não autoriza operar.

## Uma foto não decide: `--repeticoes`

A primeira rodada deu 76%/dia sobre o capital. Absurdo na cara, e o motivo era
**foto instantânea de um livro que se move**: os dois maiores da rodada
valiam ZERO na rodada seguinte, porque eram livros momentaneamente apertados
que voltaram a abrir 26 centavos.

Com `--repeticoes N --intervalo S` cada mercado é amostrado N vezes, e o que
sai é a **fração das amostras em que a cotação pontuaria** mais a receita
**MÍNIMA** observada — não a média. Mínimo, e não média, porque a média deixa
uma foto boa carregar dez ruins, que é exatamente o erro que isto corrige.

`amostras_que_pontuam` é o número que decide: mercado que paga em 1 de 10
amostras não é mercado que paga.

## O pool sem dono não é de graça: `concessao_para_pontuar`

Os maiores pools do programa (NFL, 11.657 USDC/dia) aparecem com
`score_do_mercado = 0` — **ninguém qualifica**. Parece dinheiro largado. Não é.

Medido no `Spread: LAC (-9.5)`: o livro está 0,23 / 0,34 (spread de **11
centavos**) e o `max_spread` do programa é **2,5 centavos**. Para pontuar é
preciso cotar entre 0,26 e 0,31 — **dentro** do spread, num preço que ninguém
está oferecendo. O reward ali é pagamento por fornecer liquidez que o mercado
recusa a fornecer àquele preço.

Então a varredura publica `concessao_para_pontuar_c`: **quantos centavos é
preciso melhorar o topo do livro** para entrar na faixa que pontua. Zero
significa "basta entrar na fila". Três centavos significa que se está pagando
três centavos de markout imediato por cada share, à vista, para receber o
reward — e aí a conta do 1.6 volta, com outro sinal.

`score_da_ordem` (a função compartilhada com o motor ao vivo e o backtest)
modela **entrar na fila**: ela põe a ordem a N ticks do topo, para fora. Em
mercado largo isso nunca pontua, e é por isso que a receita dela sai zero nos
pools grandes. Não a altero — mudá-la mudaria o comportamento do bot. A
concessão é medida ao lado, e as duas contas aparecem juntas.

## Os dois livros de um mercado binário

Bid em Up a 0,40 é ask em Down a 0,60: os dois livros são espelhos, e somá-los
contaria a mesma liquidez duas vezes. A conta usa o livro de UM token (que já
traz os dois lados) e publica o score do outro ao lado, para que a hipótese do
espelho possa ser conferida em vez de assumida.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

import httpx

from pulsearb.analysis.rewards import (
    OrdemHipotetica,
    ParametrosDeReward,
    fatia_do_pool,
    score_da_ordem,
    score_do_livro,
)
from pulsearb.backtest.book import OrderBook
from pulsearb.caminhos import caminho_de_escrita

CLOB = "https://clob.polymarket.com"

#: Quantas requisições simultâneas. Modesto de propósito: a varredura é
#: leitura de terceiro, e derrubar o rate limit produziria buraco silencioso
#: na amostra — mercados sem livro que pareceriam mercados sem concorrência.
CONCORRENCIA = 8

#: As cotações hipotéticas avaliadas. Nunca enviadas.
#:
#: **1.000 shares está aqui porque a primeira versão era CEGA para o dinheiro
#: grande.** `rewards_min_size` chega a 1.000 em 98 mercados, e esses 98
#: carregam 39.287 USDC/dia — 21% do programa inteiro. Uma cotação de 100
#: shares não qualifica lá, então a varredura publicava zero e parecia que
#: aqueles mercados não pagavam. Eles pagam; nós é que não entrávamos.
ORDENS = (
    OrdemHipotetica(tamanho=100.0, distancia_ticks=1, dois_lados=True),
    OrdemHipotetica(tamanho=500.0, distancia_ticks=1, dois_lados=True),
    OrdemHipotetica(tamanho=1000.0, distancia_ticks=1, dois_lados=True),
    OrdemHipotetica(tamanho=2000.0, distancia_ticks=1, dois_lados=True),
)


async def _json(
    http: httpx.AsyncClient, caminho: str, params: dict[str, Any] | None = None
) -> Any | None:
    """GET que devolve `None` em falha em vez de derrubar a varredura.

    Um mercado que some entre a listagem e a consulta é evento normal (ele
    fechou). Derrubar tudo por causa dele perderia os outros 199.
    """
    try:
        r = await http.get(CLOB + caminho, params=params)
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None


async def listar_pools(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Todos os mercados no programa, paginando pelo cursor do CLOB."""
    saida: list[dict[str, Any]] = []
    cursor = ""
    while True:
        params: dict[str, Any] = {"sponsored": "false"}
        if cursor:
            params["next_cursor"] = cursor
        pagina = await _json(http, "/rewards/markets/current", params)
        if not isinstance(pagina, dict):
            break
        dados = pagina.get("data") or []
        if not dados:
            break
        saida.extend(d for d in dados if isinstance(d, dict))
        cursor = str(pagina.get("next_cursor") or "")
        # "LTE=" é o base64 de "-1": o cursor de fim do CLOB.
        if not cursor or cursor == "LTE=":
            break
    return saida


def concessao_para_pontuar(
    livro: OrderBook, params: ParametrosDeReward
) -> dict[str, Any] | None:
    """Quanto é preciso melhorar o topo do livro para a cotação pontuar.

    Pontuar exige `|preço - meio| < max_spread`. Num livro mais largo que o
    dobro do `max_spread`, o preço que pontua está DENTRO do spread: ninguém o
    oferece, e assumi-lo é conceder.

    Devolve a concessão em centavos por lado, e o preço menos agressivo que
    ainda pontua. `0` do lado quer dizer que basta entrar na fila.
    """
    meio = livro.mid
    melhor_bid, melhor_ask = livro.best_bid, livro.best_ask
    if meio is None or melhor_bid is None or melhor_ask is None:
        return None
    passo = params.tick_size
    if passo <= 0:
        return None

    # O primeiro tick ESTRITAMENTE dentro da faixa que pontua, de cada lado.
    bruto_bid = meio - params.max_spread
    alvo_bid = (int(bruto_bid / passo) + 1) * passo
    bruto_ask = meio + params.max_spread
    alvo_ask = (-(-bruto_ask // passo) - 1) * passo

    return {
        "preco_bid_que_pontua": round(alvo_bid, 6),
        "preco_ask_que_pontua": round(alvo_ask, 6),
        # Positivo = temos de melhorar o topo. Negativo vira 0: já pontuaria
        # entrando na fila, e "concessão negativa" seria número sem sentido.
        "concessao_bid_c": round(max(0.0, alvo_bid - melhor_bid) * 100, 3),
        "concessao_ask_c": round(max(0.0, melhor_ask - alvo_ask) * 100, 3),
        "concessao_total_c": round(
            (max(0.0, alvo_bid - melhor_bid) + max(0.0, melhor_ask - alvo_ask))
            * 100,
            3,
        ),
        "spread_do_livro_c": round((melhor_ask - melhor_bid) * 100, 3),
        "max_spread_c": round(params.max_spread * 100, 3),
    }


def _taxa(m: dict[str, Any]) -> float:
    try:
        return float(m.get("total_daily_rate") or 0.0)
    except (TypeError, ValueError):
        return 0.0


async def medir(
    http: httpx.AsyncClient, pool: dict[str, Any], sem: asyncio.Semaphore
) -> dict[str, Any] | None:
    """Score concorrente e receita hipotética de um mercado."""
    cid = str(pool.get("condition_id") or "")
    if not cid:
        return None
    async with sem:
        mercado = await _json(http, f"/markets/{cid}")
    if not isinstance(mercado, dict):
        return None
    if not mercado.get("accepting_orders") or mercado.get("closed"):
        return None
    tokens = [t for t in (mercado.get("tokens") or []) if isinstance(t, dict)]
    if len(tokens) < 2:
        return None

    params = ParametrosDeReward.do_mercado(
        {
            "rewards_daily_rate": _taxa(pool),
            "rewards_max_spread": pool.get("rewards_max_spread"),
            "rewards_min_size": pool.get("rewards_min_size"),
            "tick_size": mercado.get("minimum_tick_size"),
        }
    )
    if params is None:
        return None

    livros: list[tuple[str, OrderBook]] = []
    for token in tokens[:2]:
        tid = token.get("token_id")
        if not isinstance(tid, str):
            continue
        async with sem:
            bruto = await _json(http, "/book", {"token_id": tid})
        if not isinstance(bruto, dict):
            continue
        livro = OrderBook.from_event(bruto)
        if livro is not None:
            livros.append((str(token.get("outcome") or "?"), livro))
    if not livros:
        return None

    # O livro de referência é o do PRIMEIRO token. O do segundo entra como
    # conferência da hipótese do espelho, nunca somado.
    _, referencia = livros[0]
    score_mercado = score_do_livro(referencia, params)
    espelho = score_do_livro(livros[1][1], params) if len(livros) > 1 else None

    por_ordem: dict[str, Any] = {}
    for ordem in ORDENS:
        nosso = score_da_ordem(ordem, referencia, params)
        fatia = fatia_do_pool(nosso, score_mercado)
        por_ordem[ordem.nome] = {
            "nosso_score": round(nosso, 3),
            "fatia_do_pool": round(fatia, 6),
            "receita_usdc_por_hora": round(fatia * params.daily_rate / 24.0, 6),
            "receita_usdc_por_dia": round(fatia * params.daily_rate, 4),
        }

    concessao = concessao_para_pontuar(referencia, params)

    return {
        "condition_id": cid,
        "slug": mercado.get("market_slug"),
        "pergunta": mercado.get("question"),
        "fim": mercado.get("end_date_iso"),
        "daily_rate_usdc": params.daily_rate,
        "max_spread_frac": params.max_spread,
        "min_size": params.min_size,
        "tick_size": params.tick_size,
        "score_do_mercado": round(score_mercado, 3),
        "score_do_livro_espelho": (
            round(espelho, 3) if espelho is not None else None
        ),
        # USDC por segundo, por ponto de score. Quanto MAIOR, mais fraca a
        # concorrência — é o número de seleção de mercado (B.5).
        "orcamento_por_unidade_de_score": (
            round(params.daily_rate / 86400.0 / score_mercado, 10)
            if score_mercado > 0
            else None
        ),
        "concessao": concessao,
        "por_ordem": por_ordem,
    }


def _consolidar(
    rodadas: list[list[dict[str, Any]]], ordem: str
) -> list[dict[str, Any]]:
    """Junta as amostras de cada mercado numa linha só.

    A receita reportada é o MÍNIMO entre as amostras, e `amostras_que_pontuam`
    diz quantas delas pontuaram. Média esconderia o mercado que paga uma vez
    em dez — que é o padrão que esta função existe para expor.
    """
    por_mercado: dict[str, list[dict[str, Any]]] = {}
    for rodada in rodadas:
        for linha in rodada:
            por_mercado.setdefault(linha["condition_id"], []).append(linha)

    saida: list[dict[str, Any]] = []
    for amostras in por_mercado.values():
        receitas = [a["por_ordem"][ordem]["receita_usdc_por_hora"] for a in amostras]
        scores = [a["score_do_mercado"] for a in amostras]
        base = dict(amostras[-1])
        base["amostras"] = len(amostras)
        base["amostras_que_pontuam"] = sum(1 for r in receitas if r > 0)
        base["fracao_que_pontua"] = round(
            sum(1 for r in receitas if r > 0) / len(receitas), 4
        )
        base["receita_usdc_por_hora_minima"] = round(min(receitas), 6)
        base["receita_usdc_por_hora_mediana"] = round(
            sorted(receitas)[len(receitas) // 2], 6
        )
        base["receita_usdc_por_hora_maxima"] = round(max(receitas), 6)
        base["score_do_mercado_mediano"] = round(
            sorted(scores)[len(scores) // 2], 3
        )
        concessoes = [
            a["concessao"]["concessao_total_c"]
            for a in amostras
            if a.get("concessao")
        ]
        base["concessao_total_c_mediana"] = (
            round(sorted(concessoes)[len(concessoes) // 2], 3)
            if concessoes
            else None
        )
        saida.append(base)
    saida.sort(key=lambda m: m["receita_usdc_por_hora_minima"], reverse=True)
    return saida


async def rodar(
    top: int, repeticoes: int, intervalo_s: float
) -> dict[str, Any]:
    limites = httpx.Limits(max_connections=CONCORRENCIA * 2)
    rodadas: list[list[dict[str, Any]]] = []
    async with httpx.AsyncClient(timeout=30.0, limits=limites) as http:
        print("listando mercados no programa…", file=sys.stderr)
        pools = await listar_pools(http)
        pools.sort(key=_taxa, reverse=True)
        total_diario = sum(_taxa(p) for p in pools)
        alvo = pools[:top]
        print(
            f"{len(pools)} mercados com pool, {total_diario:.0f} USDC/dia. "
            f"Medindo os {len(alvo)} maiores, {repeticoes}x…",
            file=sys.stderr,
        )
        sem = asyncio.Semaphore(CONCORRENCIA)
        for n in range(repeticoes):
            if n:
                await asyncio.sleep(intervalo_s)
            medidos = await asyncio.gather(*(medir(http, p, sem) for p in alvo))
            rodada = [m for m in medidos if m is not None]
            rodadas.append(rodada)
            print(
                f"  amostra {n + 1}/{repeticoes}: {len(rodada)} mercados medidos",
                file=sys.stderr,
            )

    # Referência = 1.000 shares: é o menor tamanho que qualifica em TODOS os
    # `rewards_min_size` observados, então é o único que não esconde um
    # recorte do universo atrás de um zero.
    ordem_principal = ORDENS[2].nome
    linhas = _consolidar(rodadas, ordem_principal)
    return {
        "universo": {
            "mercados_com_pool": len(pools),
            "soma_usdc_por_dia": round(total_diario, 2),
            "medidos": len(linhas),
            "pedidos": len(alvo),
            "nao_medidos": len(alvo) - len(linhas),
            "repeticoes": repeticoes,
            "intervalo_entre_amostras_s": intervalo_s,
            "nota_nao_medidos": (
                "mercado que fechou entre a listagem e a consulta, ou que não "
                "aceita ordens, ou sem livro. Sai da conta em vez de entrar "
                "como concorrência zero — que o faria parecer o melhor."
            ),
        },
        "ordem_de_referencia": ordem_principal,
        "mercados": linhas,
        "receita_somada_usdc_por_hora": {
            "pelo_minimo": round(
                sum(m["receita_usdc_por_hora_minima"] for m in linhas), 4
            ),
            "pela_mediana": round(
                sum(m["receita_usdc_por_hora_mediana"] for m in linhas), 4
            ),
            "nota": (
                "`pelo_minimo` é o número que vale: conta cada mercado pelo "
                "pior que ele mostrou. A distância entre os dois mede quanto "
                "do achado é livro piscando."
            ),
        },
        "leitura": (
            "RECEITA, NUNCA LUCRO. O custo do maker é o markout, e o markout "
            "medido (-0,2838 c/share) vem de janelas Up/Down de 5 min — outro "
            "regime. Número alto aqui autoriza MEDIR o markout nesses "
            "mercados; não autoriza operar. `orcamento_por_unidade_de_score` "
            "é o número de seleção: quanto MAIOR, mais fraca a concorrência."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="varredura_de_pools")
    parser.add_argument("--top", type=int, default=200)
    parser.add_argument("--repeticoes", type=int, default=1)
    parser.add_argument("--intervalo", type=float, default=300.0)
    parser.add_argument("--json", default=None)
    args = parser.parse_args(argv)

    try:
        destino = caminho_de_escrita(args.json) if args.json else None
    except ValueError as erro:
        print(str(erro), file=sys.stderr)
        return 2

    relatorio = asyncio.run(
        rodar(max(1, args.top), max(1, args.repeticoes), max(0.0, args.intervalo))
    )
    texto = json.dumps(relatorio, indent=2, ensure_ascii=False)
    if destino is not None:
        destino.write_text(texto, encoding="utf-8")
        print(f"relatório gravado em {destino}")
    else:
        print(texto)

    soma = relatorio["receita_somada_usdc_por_hora"]
    print(
        f"\nreceita somada: {soma['pelo_minimo']:.2f} USDC/h pelo MÍNIMO, "
        f"{soma['pela_mediana']:.2f} pela mediana"
    )
    print("\nos 15 melhores pelo MÍNIMO das amostras:")
    for m in relatorio["mercados"][:15]:
        conc = m.get("concessao_total_c_mediana")
        print(
            f"  {m['receita_usdc_por_hora_minima']:8.4f} USDC/h  "
            f"pontua em {m['amostras_que_pontuam']:2d}/{m['amostras']:2d}  "
            f"pool={m['daily_rate_usdc']:6.0f}/d  "
            f"concessao={'?' if conc is None else f'{conc:5.1f}c'}  "
            f"{str(m['pergunta'])[:40]}"
        )
    print(
        "\n`concessao` = centavos que é preciso melhorar o topo do livro para "
        "pontuar.\nZero = basta entrar na fila. Alto = paga-se markout "
        "imediato pelo reward."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
