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

# Faixas que DEFINEM a hipótese do tick (API_NOTES 13.3a). Ficam nomeadas
# porque não são números de conveniência: mudar qualquer uma muda o que
# "extremo" e "equilibrado" significam, e portanto o que a medição afirma
# sobre a hipótese refutada. O texto do veredito cita as mesmas faixas.
PRECO_EXTREMO_BAIXO = 0.10
PRECO_EXTREMO_ALTO = 0.90
PRECO_EQUILIBRADO_BAIXO = 0.35
PRECO_EQUILIBRADO_ALTO = 0.65


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
def medir_mudanca_de_tick(
    snapshots: list[dict[str, Any]],
    *,
    distribuicao_de_tick: dict[str, int] | None = None,
) -> dict[str, Any]:
    """O tick afina no fim da janela? Em que condição?

    `snapshots`: lista de registros `discovery_snapshot` já decodificados.
    Cada janela vira uma série temporal de (tempo_restante, tick, preço).

    `distribuicao_de_tick`: contagem de observações por tick, quando quem
    chama já a acumulou. O indexador do backtest compacta os snapshots
    (guarda só as transições, para não reter 900 mil dicts numa gravação de
    72h) e nesse caso a contagem tirada da série seria a de TRANSIÇÕES, não a
    de observações. Passar a contagem verdadeira evita esse falseamento.
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
    extremos = [
        p for p in precos if p < PRECO_EXTREMO_BAIXO or p > PRECO_EXTREMO_ALTO
    ]
    equilibrados = [
        p
        for p in precos
        if PRECO_EQUILIBRADO_BAIXO <= p <= PRECO_EQUILIBRADO_ALTO
    ]

    return {
        "janelas_observadas": len(series),
        "distribuicao_de_tick": (
            dict(sorted(distribuicao_de_tick.items()))
            if distribuicao_de_tick is not None
            else dict(sorted(ticks_vistos.items()))
        ),
        "mudancas_detectadas": len(mudancas),
        "afinamentos": len(afinou),
        "seconds_left_no_afinamento": _dist(tempos),
        "preco_no_afinamento": _dist(precos),
        "afinamentos_com_preco_extremo": len(extremos),
        "afinamentos_com_preco_equilibrado": len(equilibrados),
        "hipotese_extremos": _veredito_extremos(precos, extremos, equilibrados),
        "relacao_com_tempo_restante": _veredito_tempo(tempos, len(afinou)),
        "exemplos": mudancas[:20],
    }


def _veredito_extremos(
    precos: list[float], extremos: list[float], equilibrados: list[float]
) -> str:
    """A hipótese dos extremos (API_NOTES 13.3a) foi REFUTADA pela medição.

    A hipótese registrada dizia que o tick afina para 0,001 quando o preço vai
    para os extremos, onde 0,01 é grosso demais para expressar a diferença. A
    primeira medição real deu o contrário: 15 afinamentos, p50 de preço 0,48,
    e apenas 1 dos 15 fora de [0.10, 0.90]. O tick afina em mercado
    EQUILIBRADO, onde a disputa está apertada — não nos extremos.

    Esta função continua reportando a comparação em vez de só afirmar a
    conclusão: a refutação vale para o dado medido, e uma gravação maior tem
    de poder derrubá-la também.
    """
    if not precos:
        return "sem dado"
    fora = (
        f"{len(extremos)}/{len(precos)} afinamentos com preço fora de "
        f"[{PRECO_EXTREMO_BAIXO:.2f}, {PRECO_EXTREMO_ALTO:.2f}]"
    )
    dentro = (
        f"{len(equilibrados)}/{len(precos)} com preço em "
        f"[{PRECO_EQUILIBRADO_BAIXO:.2f}, {PRECO_EQUILIBRADO_ALTO:.2f}]"
    )
    if len(extremos) > len(precos) / 2:
        return (
            f"{fora}; {dentro} — este dado SUSTENTA a hipótese dos extremos, o "
            "que CONTRARIA a medição de 2026-08-18 que a refutou (API_NOTES "
            "13.3a). Reabrir a questão antes de usar qualquer uma das duas."
        )
    return (
        f"{fora}; {dentro} — REFUTA a hipótese dos extremos, confirmando a "
        "medição de 2026-08-18 (API_NOTES 13.3a): o tick afina em mercado "
        "equilibrado, onde a disputa está apertada."
    )


def _veredito_tempo(tempos: list[float], afinamentos: int) -> str:
    """Se não é o preço que explica o afinamento, é o tempo restante?

    Só respondível depois da correção do `_seconds_left` (BUG 3 do M2.1): na
    primeira medição este campo saía NaN em todos os exemplos e a pergunta
    ficou sem resposta.
    """
    if not tempos:
        return (
            "sem dado — nenhum afinamento com `seconds_left` conhecido "
            f"({afinamentos} afinamentos observados)"
        )
    d = _dist(tempos)
    return (
        f"{len(tempos)}/{afinamentos} afinamentos datados: p50 a {d['p50']}s do "
        f"fim, p90 a {d['p90']}s. Concentração perto do fim indica que o "
        "gatilho é o tempo, não o preço; espalhamento indica que nenhum dos "
        "dois explica sozinho."
    )


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


# ---------------------------------------------------------------- M2.2 B.2
def medir_markout(
    janelas: list[Any],
    *,
    horizontes_s: tuple[float, ...] = (1.0, 5.0, 30.0),
) -> dict[str, Any]:
    """Adverse selection: quanto o preço anda CONTRA quem foi executado.

    Esta é a medição que decide a rota maker, e é a que falta em toda
    estimativa otimista de rewards. Quem cota é executado preferencialmente
    quando o mercado está andando contra — o taker que atravessa o topo sabe
    algo (ou chegou primeiro), e o maker fica com o lado errado.

    O markout mede isso diretamente: para cada execução observada no topo,
    quanto o MEIO do livro andou 1s, 5s e 30s depois, na direção que
    prejudica quem estava do outro lado.

    Convenção de sinal, e ela importa: o número é o resultado de **quem
    forneceu a liquidez**, em centavos por share.

    - taker comprou (`BUY`) → nós vendemos → ganhamos se o meio CAI
    - taker vendeu (`SELL`) → nós compramos → ganhamos se o meio SOBE

    Markout **negativo** = fomos atropelados. É o custo que precisa ser menor
    que o reward para a rota maker fechar.

    Só entram execuções cujo preço estava no topo (dentro de meio tick do
    melhor preço do lado): quem foi executado fundo no livro não é o maker
    que estamos simulando.
    """
    por_recorte: dict[str, list[dict[str, float]]] = defaultdict(list)
    total = 0
    fora_do_topo = 0
    sem_referencia = 0

    for janela in janelas:
        tick = float(getattr(janela, "tick_size", 0.01) or 0.01)
        hora = int((janela.close_ts_ns / 1e9) // 3600 % 24)
        timelines = [t for t in janela.books.values() if t is not None and t.ts]
        if not timelines:
            continue
        for ts_ns, preco, _tamanho, lado in getattr(janela, "trades", []):
            total += 1
            book = _primeiro_book(timelines, ts_ns)
            if book is None or book.mid is None:
                sem_referencia += 1
                continue
            melhor = book.best_ask if lado == "BUY" else book.best_bid
            if melhor is None or abs(preco - melhor) > tick / 2:
                fora_do_topo += 1
                continue
            meio_agora = book.mid
            distancia_ticks = (
                round(abs(preco - meio_agora) / tick) if tick > 0 else 0
            )
            amostra: dict[str, float] = {}
            for horizonte in horizontes_s:
                depois = _primeiro_book(timelines, ts_ns + int(horizonte * 1e9))
                if depois is None or depois.mid is None:
                    continue
                # Sinal do ponto de vista de quem FORNECEU a liquidez.
                variacao = meio_agora - depois.mid if lado == "BUY" else depois.mid - meio_agora
                amostra[f"{horizonte:g}s"] = variacao * 100.0  # centavos por share
            if not amostra:
                sem_referencia += 1
                continue
            for recorte in (
                "total",
                f"duracao={janela.duracao_s}s",
                f"hora_utc={hora:02d}",
                f"distancia_ticks={min(distancia_ticks, 5)}",
            ):
                por_recorte[recorte].append(amostra)

    saida = {
        recorte: {
            f"{h:g}s": _dist([a[f"{h:g}s"] for a in amostras if f"{h:g}s" in a])
            for h in horizontes_s
        }
        for recorte, amostras in sorted(por_recorte.items())
    }
    return {
        "execucoes_observadas": total,
        "descartadas_fora_do_topo": fora_do_topo,
        "descartadas_sem_book_de_referencia": sem_referencia,
        "markout_centavos_por_share": saida,
        "convencao": (
            "Sinal do ponto de vista de QUEM FORNECEU a liquidez. Negativo = "
            "o preço andou contra nós depois da execução, ou seja, fomos "
            "atropelados. É o custo que o reward precisa superar."
        ),
    }


def _primeiro_book(timelines: list[Any], ts_ns: int) -> Any:
    """O book de qualquer timeline da janela naquele instante.

    As duas pernas (Up e Down) são espelhos uma da outra; para medir
    deslocamento do meio, a primeira que tiver snapshot serve.
    """
    for timeline in timelines:
        book = timeline.at(ts_ns)
        if book is not None:
            return book
    return None


# ---------------------------------------------------------------- M2.2 B.3
def conta_do_maker(
    *,
    rewards: dict[str, Any],
    markout: dict[str, Any],
    fee_rebate_rate: float,
    volume_taker_usdc: float = 0.0,
    horizonte_markout: str = "5s",
) -> dict[str, Any]:
    """A conta fechada da rota maker, num bloco só (M2.2 B.3).

        resultado = rewards + rebate − custo_de_markout − taxa(0 p/ maker)

    O capital imobilizado entra como AVISO, não como número: dimensionar
    posição é decisão do M3, e inventar aqui um custo de capital sem tamanho
    de posição definido daria um número com cara de precisão e sem conteúdo.

    Cada célula vem com as horas de amostra que a sustentam. Célula com pouca
    amostra continua aparecendo — some seria pior —, mas quem lê vê o `n`.
    """
    por_recorte: dict[str, dict[str, Any]] = {}
    tabela_markout = markout.get("markout_centavos_por_share") or {}
    por_ordem = rewards.get("por_ordem") or {}

    for nome_da_ordem, recortes in por_ordem.items():
        for recorte, dados in recortes.items():
            horas = dados.get("horas_de_amostra") or 0.0
            receita = dados.get("receita_usdc") or 0.0
            markout_recorte = (tabela_markout.get(recorte) or {}).get(horizonte_markout) or {}
            markout_medio_cent = markout_recorte.get("media")
            n_markout = markout_recorte.get("n", 0)
            chave = f"{nome_da_ordem} | {recorte}"
            por_recorte[chave] = {
                "horas_de_amostra": horas,
                "rewards_usdc": round(receita, 6),
                "rebate_usdc": round(volume_taker_usdc * fee_rebate_rate, 6),
                "markout_medio_centavos_por_share": markout_medio_cent,
                "execucoes_no_markout": n_markout,
                "taxa_usdc": 0.0,
                "resultado_parcial_usdc": round(
                    receita + volume_taker_usdc * fee_rebate_rate, 6
                ),
            }

    return {
        "por_ordem_e_recorte": dict(sorted(por_recorte.items())),
        "formula": (
            "resultado = rewards + rebate(fee_rebate_rate * taxa dos takers que "
            "nos executam) - custo_de_markout - taxa(0 para maker) "
            "- capital_imobilizado"
        ),
        "o_que_falta_para_fechar": [
            "volume_taker_usdc: exige simular QUAIS das nossas cotações teriam "
            "sido executadas, o que depende de posição na fila — e a fila não é "
            "observável no WS agregado (ver limitacao_de_fila).",
            "custo_de_markout em USDC: sai de markout * shares executadas, que "
            "depende do mesmo número acima.",
            "capital_imobilizado: dimensionamento de posição é decisão do M3.",
        ],
        "limitacao_de_fila": (
            "O WS entrega níveis AGREGADOS, não ordens individuais. Não dá para "
            "saber quantas ordens dividem um nível nem em que posição a nossa "
            "estaria. Toda simulação de preenchimento maker aqui é, portanto, "
            "OTIMISTA por construção. No pior caso — a nossa ordem sempre no "
            "fim da fila — só seríamos executados quando o nível inteiro fosse "
            "varrido, isto é, exatamente nos casos de markout pior. Ou seja: o "
            "viés não é neutro, ele infla o resultado nas duas pontas (mais "
            "execução boa, menos execução ruim contabilizada)."
        ),
    }
