"""As quatro medições do M2.E, sobre a gravação.

Cada uma responde uma pergunta que decide algo:

1. **Mudança de tick** — o tick afina para 0,001 no fim da janela? Se afinar,
   o modelo de preço precisa saber, porque muda a granularidade do edge.
2. **Atraso de liquidação** — quanto tempo entre `endDate` e a resolução, por
   jogo. Define quanto tempo o capital fica preso, e testa a hipótese de o
   horário ser mais lento por usar UMA.
3. **Profundidade do book** — quanto cabe em USDC perto do topo. Define a
   CAPACIDADE: edge que só comporta US$ 20 não paga o trabalho.
4. **Potencial maker** — quanto tempo o topo fica sem ser atravessado, e qual
   seria a receita de rebate + rewards. **Só medição**: nada de market making
   no M2.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import Any

from pulsearb.backtest.book import OrderBook


def _percentil(valores: list[float], pct: float) -> float | None:
    if not valores:
        return None
    ordenados = sorted(valores)
    rank = max(1, min(len(ordenados), int(-(-pct * len(ordenados) // 100))))
    return ordenados[rank - 1]


def _dist(valores: list[float]) -> dict[str, Any]:
    if not valores:
        return {"n": 0}
    return {
        "n": len(valores),
        "min": round(min(valores), 4),
        "p50": round(_percentil(valores, 50) or 0, 4),
        "p90": round(_percentil(valores, 90) or 0, 4),
        "p99": round(_percentil(valores, 99) or 0, 4),
        "max": round(max(valores), 4),
        "media": round(statistics.fmean(valores), 4),
    }


# ---------------------------------------------------------------- M2.E.1
def medir_mudanca_de_tick(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    """O tick afina no fim da janela? Em que condição?

    `snapshots`: lista de registros `discovery_snapshot` já decodificados.
    Cada janela vira uma série temporal de (tempo_restante, tick, preço).
    """
    series: dict[str, list[tuple[float, float, float | None]]] = defaultdict(list)
    for snapshot in snapshots:
        janelas = snapshot.get("janelas")
        if not isinstance(janelas, list):
            continue
        for janela in janelas:
            if not isinstance(janela, dict):
                continue
            slug = janela.get("slug")
            tick = janela.get("tick_size")
            restante = janela.get("_seconds_left")
            preco = janela.get("best_ask")
            if isinstance(slug, str) and isinstance(tick, (int, float)):
                series[slug].append(
                    (
                        float(restante) if isinstance(restante, (int, float)) else float("nan"),
                        float(tick),
                        float(preco) if isinstance(preco, (int, float)) else None,
                    )
                )

    ticks_vistos: dict[str, int] = defaultdict(int)
    mudancas: list[dict[str, Any]] = []
    for slug, pontos in series.items():
        for _, tick, _ in pontos:
            ticks_vistos[f"{tick:g}"] += 1
        distintos = {tick for _, tick, _ in pontos}
        if len(distintos) > 1:
            anterior = pontos[0][1]
            for restante, tick, preco in pontos[1:]:
                if tick != anterior:
                    mudancas.append(
                        {
                            "slug": slug,
                            "de": anterior,
                            "para": tick,
                            "seconds_left": restante,
                            "preco_no_momento": preco,
                        }
                    )
                    anterior = tick

    afinou = [m for m in mudancas if m["para"] < m["de"]]
    tempos = [m["seconds_left"] for m in afinou if not math.isnan(m["seconds_left"])]
    precos = [m["preco_no_momento"] for m in afinou if m["preco_no_momento"] is not None]
    extremos = [p for p in precos if p < 0.10 or p > 0.90]

    return {
        "janelas_observadas": len(series),
        "distribuicao_de_tick": dict(sorted(ticks_vistos.items())),
        "mudancas_detectadas": len(mudancas),
        "afinamentos": len(afinou),
        "seconds_left_no_afinamento": _dist(tempos),
        "preco_no_afinamento": _dist(precos),
        "afinamentos_com_preco_extremo": len(extremos),
        "hipotese_extremos": (
            "sem dado" if not precos
            else f"{len(extremos)}/{len(precos)} afinamentos ocorreram com preço "
                 f"fora de [0.10, 0.90] — "
                 + ("compatível com a hipótese dos extremos (API_NOTES 13.3)"
                    if len(extremos) > len(precos) / 2
                    else "NÃO sustenta a hipótese dos extremos; investigar tempo restante")
        ),
        "exemplos": mudancas[:20],
    }


# ---------------------------------------------------------------- M2.E.2
def medir_atraso_liquidacao(
    resolucoes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Intervalo entre `endDate` e o evento de resolução, POR JOGO.

    `resolucoes`: dicts com `slug`, `jogo`, `end_date_ns`, `resolution_ts_ns`.
    A separação por jogo é o ponto: a hipótese é que o horário seja mais
    lento por passar por UMA (API_NOTES 12.2b).
    """
    por_jogo: dict[str, list[float]] = defaultdict(list)
    for item in resolucoes:
        fim = item.get("end_date_ns")
        res = item.get("resolution_ts_ns")
        jogo = str(item.get("jogo", "?"))
        if isinstance(fim, (int, float)) and isinstance(res, (int, float)) and res > 0:
            por_jogo[jogo].append((res - fim) / 1e9)

    saida = {jogo: _dist(valores) for jogo, valores in sorted(por_jogo.items())}
    twap_p50 = (saida.get("twap") or {}).get("p50")
    hora_p50 = (saida.get("horario") or {}).get("p50")
    if twap_p50 is not None and hora_p50 is not None:
        veredito = (
            f"horário é {hora_p50 / twap_p50:.1f}x mais lento que TWAP "
            f"({hora_p50:.1f}s vs {twap_p50:.1f}s)"
            if twap_p50 > 0
            else "TWAP com p50 ~0s"
        )
    else:
        veredito = "dado insuficiente para comparar os dois jogos"
    return {"por_jogo": saida, "comparacao": veredito}


# ---------------------------------------------------------------- M2.E.3
def medir_profundidade(
    amostras: list[dict[str, Any]],
) -> dict[str, Any]:
    """Quanto cabe, em USDC, a 1 e a 3 ticks do topo — por duração e por hora.

    `amostras`: dicts com `book` (OrderBook), `duracao_s`, `tick_size`,
    `hora_utc`.
    """
    por_duracao: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: {"1tick": [], "3ticks": []}
    )
    por_hora: dict[int, list[float]] = defaultdict(list)

    for amostra in amostras:
        book = amostra.get("book")
        if not isinstance(book, OrderBook):
            continue
        tick = float(amostra.get("tick_size") or 0.01)
        duracao = f"{amostra.get('duracao_s', '?')}s"
        d1 = book.depth_usdc(side="ask", ticks=1, tick_size=tick)
        d3 = book.depth_usdc(side="ask", ticks=3, tick_size=tick)
        por_duracao[duracao]["1tick"].append(d1)
        por_duracao[duracao]["3ticks"].append(d3)
        hora = amostra.get("hora_utc")
        if isinstance(hora, int):
            por_hora[hora].append(d3)

    return {
        "por_duracao": {
            duracao: {"1tick_usdc": _dist(v["1tick"]), "3ticks_usdc": _dist(v["3ticks"])}
            for duracao, v in sorted(por_duracao.items())
        },
        "por_hora_utc_3ticks": {
            str(hora): _dist(valores) for hora, valores in sorted(por_hora.items())
        },
        "nota": (
            "Capacidade da estratégia: o p50 de 3ticks é o teto realista por "
            "trade sem mover o mercado. Se for da ordem de dezenas de USDC, "
            "o projeto tem teto de escala independentemente do edge."
        ),
    }


# ---------------------------------------------------------------- M2.E.4
def medir_potencial_maker(
    *,
    duracoes_topo_intacto_s: list[float],
    volume_taker_usdc: float,
    rebate_rate: float,
    rewards_diarios_usdc: float = 0.0,
    participacao_estimada: float = 0.0,
) -> dict[str, Any]:
    """Potencial da rota maker — MEDIÇÃO, não implementação.

    Duas fontes de receita para quem cota em vez de atravessar:
    - **rebate**: `rebate_rate` das taker fees geradas (0.2 = 20%, API_NOTES 12.6)
    - **rewards de liquidez**: pool diário rateado entre quem cota dentro de
      `rewardsMaxSpread` com pelo menos `rewardsMinSize`

    `duracoes_topo_intacto_s` é o tempo que o topo do book sobrevive sem ser
    atravessado — é a proxy do risco de seleção adversa: topo que dura muito
    significa que quem cota fica pendurado; topo que some rápido significa que
    quem cota é executado justamente quando não queria.
    """
    fee_gerada = volume_taker_usdc  # já em USDC de fee, não de notional
    return {
        "tempo_topo_intacto_s": _dist(duracoes_topo_intacto_s),
        "receita_estimada": {
            "rebate_usdc": round(fee_gerada * rebate_rate * participacao_estimada, 4),
            "rewards_usdc": round(rewards_diarios_usdc * participacao_estimada, 4),
            "total_usdc": round(
                fee_gerada * rebate_rate * participacao_estimada
                + rewards_diarios_usdc * participacao_estimada,
                4,
            ),
        },
        "premissas": {
            "rebate_rate": rebate_rate,
            "participacao_estimada": participacao_estimada,
            "volume_taker_usdc": volume_taker_usdc,
            "rewards_diarios_usdc": rewards_diarios_usdc,
        },
        "aviso": (
            "Estimativa de POTENCIAL. Não modela seleção adversa: quem cota é "
            "executado preferencialmente quando o preço está andando contra. "
            "O número aqui é um TETO, não uma expectativa."
        ),
    }
