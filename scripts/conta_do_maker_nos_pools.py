"""A conta fechada da rota maker, nos mercados que pagam. Fecha o 1.6/1.12.

    .venv/bin/python scripts/conta_do_maker_nos_pools.py \
        --pools relatorios/POOLS_PERSIST_20260913.json \
        --markout relatorios/MARKOUT_POOLS_20260913.json \
        --json relatorios/CONTA_MAKER.json

## O dado que faltava desde o 1.6, e onde ele estava

O 1.6 é NÃO AVALIÁVEL há semanas por **um** dado: `shares_executadas` — sem
ele, `custo = |markout| × shares` não tem o segundo fator, e o quadro dizia que
obtê-lo exigia contar varreduras de nível na gravação.

Para os mercados de reward ele não exige gravação nenhuma:
`GET data-api.polymarket.com/trades?market=<conditionId>` devolve o histórico
**público** de execuções, com `size`, `price`, `side` e `timestamp`. O volume
taker realizado é justamente o teto de quantas shares nossas poderiam ter sido
varridas — não dá para ser executado mais do que o mercado negociou.

## Por que o limite é SUPERIOR de custo, e portanto INFERIOR de lucro

A conta assume que **todo** o fluxo taker nos atropela. É falso e é de
propósito: com 1.000 shares cotadas ao lado de concorrentes no livro, nós
pegaríamos uma fração. Assumir a totalidade **superestima o custo**, e como o
reward **não depende de fila** (§15.3 — pontua-se por ESTAR no livro, amostrado
1×/min), o erro entra só de um lado.

É a mesma assimetria de `conta_pessimista_do_maker`, aplicada a outro regime:
receita medida, custo no máximo. Se o líquido sai positivo aqui, sai positivo
de verdade.

## A regra de seleção muda: receita por unidade de VOLUME

O primeiro cálculo exploratório escondia isto — 73% do volume dos 20 melhores
vinha de UM mercado (`US x Iran Effective Ceasefire`, 4.733 shares/h) que
pagava 2,15 USDC/h. Volume é o que gera custo. Ordenar por receita bruta põe
no topo exatamente os mercados que mais custam.

Daí `receita_por_mil_shares`: quanto o mercado paga por unidade de fluxo que
nos atropela. É o número de seleção — o análogo do
`orcamento_por_unidade_de_score` para o lado do custo.

## O que esta conta NÃO resolve

O **1.5**. Capacidade continua sendo o que o livro comporta, e nenhuma das
rotas escapa disso.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

DATA_API = "https://data-api.polymarket.com"

#: Teto de trades por mercado numa chamada. Truncar NÃO enviesa a taxa: o
#: cálculo normaliza pelo span observado (`max(ts) - min(ts)`), então um
#: mercado movimentado devolve menos horas e a mesma taxa por hora.
LIMITE_DE_TRADES = 1000

#: Piso do span, em horas. Um mercado com dois trades em 30 s produziria taxa
#: por hora absurda; o piso transforma isso em subestimativa de taxa, que é o
#: lado seguro para uma conta que quer limitar o LUCRO por baixo... e o lado
#: ERRADO para o custo. Por isso o piso é pequeno e vai impresso.
PISO_DO_SPAN_H = 0.5


def volume_taker(http: httpx.Client, condition_id: str) -> dict[str, Any] | None:
    """Shares/h e USDC/h negociados, do histórico público."""
    try:
        r = http.get(
            DATA_API + "/trades",
            params={"market": condition_id, "limit": LIMITE_DE_TRADES},
        )
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    try:
        trades = r.json()
    except ValueError:
        return None
    if not isinstance(trades, list) or not trades:
        return {"trades": 0, "shares_por_hora": 0.0, "usdc_por_hora": 0.0, "span_h": 0.0}

    carimbos = [t.get("timestamp") for t in trades if isinstance(t.get("timestamp"), (int, float))]
    if not carimbos:
        return None
    span_h = max(PISO_DO_SPAN_H, (max(carimbos) - min(carimbos)) / 3600.0)
    shares = sum(float(t.get("size") or 0) for t in trades)
    usdc = sum(float(t.get("size") or 0) * float(t.get("price") or 0) for t in trades)
    return {
        "trades": len(trades),
        "span_h": round(span_h, 3),
        "truncado": len(trades) >= LIMITE_DE_TRADES,
        "shares_por_hora": round(shares / span_h, 3),
        "usdc_por_hora": round(usdc / span_h, 3),
        "idade_do_ultimo_trade_h": round((time.time() - max(carimbos)) / 3600.0, 2),
    }


def _markout_adverso(markout: dict[str, Any] | None) -> tuple[float | None, int]:
    """|markout| em centavos por share, e quantas execuções o sustentam.

    Usa a MÉDIA de 5 s do recorte `total`, que é o mesmo horizonte e o mesmo
    recorte que o 1.7 usa. Markout positivo (o preço andou a nosso favor) vira
    custo ZERO, nunca crédito: creditar ganho de seleção adversa numa conta que
    quer limitar por baixo seria somar a hipótese otimista dos dois lados.
    """
    if not markout:
        return None, 0
    total = (
        markout.get("markout", {})
        .get("markout_centavos_por_share", {})
        .get("total", {})
        .get("5s", {})
    )
    media = total.get("media")
    if media is None:
        return None, 0
    return abs(min(0.0, float(media))), int(total.get("n") or 0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="conta_do_maker_nos_pools")
    parser.add_argument("--pools", required=True)
    parser.add_argument("--markout", default=None)
    parser.add_argument("--top", type=int, default=60)
    parser.add_argument("--json", default=None)
    args = parser.parse_args(argv)

    pools = json.loads(Path(args.pools).read_text(encoding="utf-8"))
    markout = (
        json.loads(Path(args.markout).read_text(encoding="utf-8"))
        if args.markout and Path(args.markout).exists()
        else None
    )
    custo_c, n_exec = _markout_adverso(markout)

    persistentes = [
        m
        for m in pools.get("mercados", [])
        if m.get("fracao_que_pontua", 0) >= 0.5
        and (m.get("receita_usdc_por_hora_minima") or 0) > 0
    ]
    persistentes.sort(
        key=lambda m: -(m.get("receita_usdc_por_hora_minima") or 0)
    )
    alvo = persistentes[: max(1, args.top)]

    linhas: list[dict[str, Any]] = []
    with httpx.Client(timeout=30.0) as http:
        for i, m in enumerate(alvo, 1):
            vol = volume_taker(http, m["condition_id"])
            if vol is None:
                continue
            receita = float(m["receita_usdc_por_hora_minima"])
            shares_h = vol["shares_por_hora"]
            custo_h = (
                None if custo_c is None else round(shares_h * custo_c / 100.0, 4)
            )
            linhas.append(
                {
                    "pergunta": m.get("pergunta"),
                    "condition_id": m["condition_id"],
                    "daily_rate_usdc": m.get("daily_rate_usdc"),
                    "receita_usdc_por_hora": round(receita, 4),
                    "volume": vol,
                    "custo_maximo_usdc_por_hora": custo_h,
                    "liquido_no_pior_caso_usdc_por_hora": (
                        None if custo_h is None else round(receita - custo_h, 4)
                    ),
                    # O número de SELEÇÃO: paga-se por unidade de fluxo que
                    # nos atropela, não por hora bruta.
                    "receita_por_mil_shares": (
                        round(receita / shares_h * 1000, 4) if shares_h > 0 else None
                    ),
                }
            )
            if i % 20 == 0:
                print(f"  {i}/{len(alvo)} mercados…", file=sys.stderr)

    com_conta = [x for x in linhas if x["liquido_no_pior_caso_usdc_por_hora"] is not None]
    com_conta.sort(key=lambda x: -x["liquido_no_pior_caso_usdc_por_hora"])

    def _soma(quantos: int) -> dict[str, Any]:
        sub = com_conta[:quantos]
        return {
            "mercados": len(sub),
            "receita_usdc_por_hora": round(
                sum(x["receita_usdc_por_hora"] for x in sub), 4
            ),
            "custo_maximo_usdc_por_hora": round(
                sum(x["custo_maximo_usdc_por_hora"] for x in sub), 4
            ),
            "liquido_no_pior_caso_usdc_por_hora": round(
                sum(x["liquido_no_pior_caso_usdc_por_hora"] for x in sub), 4
            ),
            "volume_shares_por_hora": round(
                sum(x["volume"]["shares_por_hora"] for x in sub), 1
            ),
        }

    relatorio = {
        "markout_usado": {
            "centavos_por_share_adverso": custo_c,
            "execucoes": n_exec,
            "horizonte": "5s",
            "recorte": "total",
            "ausente": custo_c is None,
            "nota": (
                "Markout POSITIVO vira custo zero, nunca crédito: creditar "
                "ganho de seleção adversa numa conta que limita por baixo "
                "seria somar a hipótese otimista dos dois lados."
            ),
        },
        "mercados_avaliados": len(linhas),
        # Os recortes existem para expor o ÓTIMO: passar de certo ponto
        # PIORA o líquido, porque volume (que é custo) cresce mais rápido que
        # receita. `sorted(set(...))` para não publicar `top_50` e `top_40`
        # com o mesmo conteúdo quando há menos de 50 mercados.
        "por_recorte": {
            f"top_{n}": _soma(n)
            for n in sorted({5, 10, 25, 50, len(com_conta)})
            if 0 < n <= len(com_conta)
        },
        "nota_do_otimo": (
            "Compare os recortes: se `liquido` CAI ao incluir mais mercados, "
            "existe um ótimo, e ele não é 'todos'. Os mercados marginais são "
            "os de muito volume e pouca receita — pagam pouco e atropelam "
            "muito. `receita_por_mil_shares` é o número que os separa."
        ),
        "mercados": com_conta,
        "por_que_e_um_limite_inferior": (
            "A conta assume que TODO o fluxo taker nos atropela. É falso e é "
            "de propósito: com 1.000 shares ao lado de concorrentes, nós "
            "pegaríamos uma fração. Assumir a totalidade SUPERESTIMA o custo, "
            "e como o reward não depende de fila (§15.3), o erro entra só de "
            "um lado. Líquido positivo aqui é positivo de verdade."
        ),
        "o_que_isto_nao_resolve": (
            "O 1.5. Capacidade continua sendo o que o livro comporta."
        ),
    }

    texto = json.dumps(relatorio, indent=2, ensure_ascii=False)
    if args.json:
        from pulsearb.caminhos import caminho_de_escrita

        destino = caminho_de_escrita(args.json)
        destino.write_text(texto, encoding="utf-8")
        print(f"relatório gravado em {destino}")

    if custo_c is None:
        print(
            "\nMARKOUT AUSENTE — a conta não fecha. Rode "
            "scripts/markout_dos_pools.py e passe --markout.",
            file=sys.stderr,
        )
        return 1

    print(f"\nmarkout usado: -{custo_c} c/share (5s, {n_exec} execuções)\n")
    cab = ("mercado", "receita/h", "custo/h", "liq/h", "USDC/1k sh")
    print(f"{cab[0]:<42} {cab[1]:>9} {cab[2]:>9} {cab[3]:>9} {cab[4]:>10}")
    for x in com_conta[:15]:
        print(
            f"{str(x['pergunta'])[:42]:<42} "
            f"{x['receita_usdc_por_hora']:>9.3f} "
            f"{x['custo_maximo_usdc_por_hora']:>9.3f} "
            f"{x['liquido_no_pior_caso_usdc_por_hora']:>+9.3f} "
            f"{x['receita_por_mil_shares']!s:>10}"
        )
    print("\nsomas (pior caso de custo):")
    for nome, s in relatorio["por_recorte"].items():
        print(
            f"  {nome:<10} {s['mercados']:>3} mercados: "
            f"receita {s['receita_usdc_por_hora']:>8.2f} - "
            f"custo {s['custo_maximo_usdc_por_hora']:>8.2f} = "
            f"LÍQUIDO {s['liquido_no_pior_caso_usdc_por_hora']:>+8.2f} USDC/h"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
