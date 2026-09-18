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
from typing import Any

import httpx

from pulsearb.analysis.measurements import CHAVE_CUSTO_DE_SAIDA
from pulsearb.caminhos import caminho_de_escrita, caminho_de_relatorio_lido

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


#: Horizonte que serve de proxy do custo até conseguir sair. Quem mudar isto
#: muda o critério de retorno a ✅ do 1.12 — está escrito no quadro.
HORIZONTE_DE_SAIDA = "1800s"


def _custo_de_saida(markout: dict[str, Any] | None) -> dict[str, float] | None:
    """Por mercado: o custo de SAÍDA em centavos por share.

    É o MAIOR entre o markout adverso a 30 min e spread/2 no instante da
    execução — os dois medidos pelo `medir_markout` sobre as mesmas
    execuções, no recorte `mercado=<slug>` que o `markout_dos_pools` liga.
    É o termo que o 1.12 não media: uma execução de um lado só num mercado
    que resolve em dias custa o que custa desfazê-la, não o que o preço andou
    em 5 s.

    Devolve ¢/share, e SÓ isso: quem multiplica é a linha do mercado, pelo
    fluxo taker em shares/h — a MESMA hipótese pessimista da conta de 5 s
    (todo o fluxo nos atropela). Multiplicar por execuções/h seria ¢ por
    trade-hora, e um fill de 1.000 shares contaria como um de uma (achado do
    Codex no PR #114).

    `None` — e não zero — quando o relatório não traz o recorte por mercado
    ou o horizonte de 30 min: cada um desses ausentes viraria "custo zero" e
    fecharia a conta a favor sem ter medido nada.
    """
    if not markout:
        return None
    tabela = markout.get("markout", {}).get("markout_centavos_por_share", {})
    saida: dict[str, float] = {}
    for recorte, dist in tabela.items():
        if not recorte.startswith("mercado=") or not isinstance(dist, dict):
            continue
        longo = dist.get(HORIZONTE_DE_SAIDA) or {}
        spread = dist.get(CHAVE_CUSTO_DE_SAIDA) or {}
        if longo.get("media") is None or spread.get("media") is None:
            continue
        adverso_longo = abs(min(0.0, float(longo["media"])))
        saida[recorte.removeprefix("mercado=")] = max(adverso_longo, float(spread["media"]))
    return saida or None


#: Quanto do FLUXO do recorte precisa de ter custo de saída medido para o
#: número do 1.12d valer. Decidido por Paulo em 2026-09-18, entre três
#: opções postas antes de ele ver qualquer resultado desta regra.
#:
#: Por que uma fracção de SHARES e não de mercados: o que atropela a cotação
#: é fluxo, não contagem. Dez mercados minúsculos sem medida pesam menos que
#: um grande, e um critério por contagem trataria os dois igual.
#:
#: Por que 90 % e não 100 %: com cobertura completa (a regra do Codex no
#: #114) o número saiu `None` em TODO recorte das rodadas de 4 h e 24 h —
#: pool de reward paga para cotar e NÃO ser executado, e mercado sem fill não
#: gera markout de 30 min. Um critério que nunca produz número não recusa
#: nada: só cala. Por que não "só os medidos": aí o recorte passaria com o
#: subconjunto que por acaso teve dado, que é o achado do #114.
COBERTURA_MINIMA_DE_SHARES = 0.90


def _cobertura_de_shares(sub: list[dict[str, Any]], medidos: list[dict[str, Any]]) -> float | None:
    """Fracção do fluxo do recorte que TEM custo de saída medido.

    `None` quando o recorte não tem fluxo nenhum — sem shares não há fracção,
    e devolver 1,0 ali diria "coberto" onde a verdade é "não há o que cobrir".
    """
    total = sum(x["volume"]["shares_por_hora"] for x in sub)
    if total <= 0:
        return None
    return sum(x["volume"]["shares_por_hora"] for x in medidos) / total


#: Como o recorte é ORDENADO — e portanto quais mercados um LIVE operaria.
#:
#: Era `liquido_no_pior_caso` (receita menos markout de 5 s), e o universo
#: vinha da varredura ordenada por `daily_rate`: os pools maiores. A rodada
#: de 4 h de 2026-09-17 mostrou o que isso escolhe — nos mercados do topo por
#: tamanho o custo de saída comia 66 % da receita, e abaixo deles passava dos
#: 100 %. Pool grande atrai fluxo grande, e é o fluxo que atropela a cotação.
#:
#: `receita_por_mil_shares` é a receita por unidade de fluxo que nos atropela:
#: exactamente o eixo em que os seis mercados sobreviventes se separaram dos
#: que perdem. Trocado por decisão de Paulo em 2026-09-18, ANTES das rodadas
#: SHADOW de 14 dias — se fosse depois, elas mediriam os mercados errados.
#:
#: Mercado sem fluxo medido (`receita_por_mil_shares is None`) vai para o FIM,
#: não para o início: "nenhum trade na amostra" é ausência de dado, não
#: ausência de fluxo, e pô-lo em primeiro seria seleccionar pela falta.
ORDENADO_POR = "receita_por_mil_shares"


def _chave_do_selector(x: dict[str, Any]) -> float:
    return -(x[ORDENADO_POR] or 0.0)


def _soma_do_recorte(sub: list[dict[str, Any]]) -> dict[str, Any]:
    """A soma de um recorte, com TRÊS líquidos com custo de saída, de propósito.

    - `liquido_com_custo_de_saida_usdc_por_hora`: cobertura COMPLETA ou
      `None` (regra do Codex, #114). Fica como diagnóstico: é o mais estrito.
    - `liquido_com_custo_de_saida_nos_medidos_usdc_por_hora`: sobre quem tem
      medida, sem exigir nada. Fica como diagnóstico: é o mais frouxo.
    - `liquido_do_1_12d_usdc_por_hora`: **o que decide o item 1.12d** desde
      2026-09-18 — sobre os medidos, mas só se eles cobrirem
      `COBERTURA_MINIMA_DE_SHARES` do fluxo. Os outros dois continuam
      publicados para que a escolha do critério continue auditável.
    """
    sem_saida = [x for x in sub if x["liquido_com_custo_de_saida_usdc_por_hora"] is None]
    medidos = [x for x in sub if x["liquido_com_custo_de_saida_usdc_por_hora"] is not None]
    cobertura = _cobertura_de_shares(sub, medidos)
    return {
        "mercados": len(sub),
        "receita_usdc_por_hora": round(sum(x["receita_usdc_por_hora"] for x in sub), 4),
        "custo_maximo_usdc_por_hora": round(
            sum(x["custo_maximo_usdc_por_hora"] for x in sub), 4
        ),
        "liquido_no_pior_caso_usdc_por_hora": round(
            sum(x["liquido_no_pior_caso_usdc_por_hora"] for x in sub), 4
        ),
        "volume_shares_por_hora": round(
            sum(x["volume"]["shares_por_hora"] for x in sub), 1
        ),
        # O critério de retorno a ✅ do 1.12: cobertura COMPLETA ou nada.
        "mercados_sem_custo_de_saida": len(sem_saida),
        "liquido_com_custo_de_saida_usdc_por_hora": (
            None
            if sem_saida or not sub
            else round(
                sum(x["liquido_com_custo_de_saida_usdc_por_hora"] for x in sub), 4
            )
        ),
        # O MESMO líquido, só sobre os mercados que TÊM a medida — publicado ao
        # lado do `None`, nunca no lugar dele. A rodada de 4 h de 2026-09-17
        # mediu o custo de saída em 45 de 214 mercados (top_5: 4 de 5): pool de
        # reward paga para cotar e NÃO ser executado, e mercado sem fill não
        # gera markout de 30 min. Com a regra de cobertura completa o veredito
        # saiu `None` em todo recorte, e o número que responderia a pergunta
        # ficou nos JSONs sem ninguém somar. O subconjunto aqui é definido por
        # DISPONIBILIDADE DE DADO, não por resultado — é o que um LIVE faria
        # ("só se opera onde o custo de saída foi medido"). Trocar o critério
        # 1.12d para este número é decisão do quadro, não deste script.
        "mercados_com_custo_de_saida": len(medidos),
        "liquido_com_custo_de_saida_nos_medidos_usdc_por_hora": (
            None
            if not medidos
            else round(
                sum(x["liquido_com_custo_de_saida_usdc_por_hora"] for x in medidos), 4
            )
        ),
        "receita_nos_medidos_usdc_por_hora": (
            None if not medidos else round(sum(x["receita_usdc_por_hora"] for x in medidos), 4)
        ),
        # ─────────────────────────── O NÚMERO QUE DECIDE O 1.12d (2026-09-18)
        # Líquido sobre os mercados medidos, mas SÓ se eles cobrirem
        # `COBERTURA_MINIMA_DE_SHARES` do fluxo do recorte. Abaixo disso o
        # recorte não responde — e `cobertura_de_shares_medidas` diz quanto
        # faltou, para a recusa ter número em vez de silêncio.
        "cobertura_de_shares_medidas": (
            None if cobertura is None else round(cobertura, 4)
        ),
        "cobertura_minima_exigida": COBERTURA_MINIMA_DE_SHARES,
        "liquido_do_1_12d_usdc_por_hora": (
            None
            if cobertura is None or cobertura < COBERTURA_MINIMA_DE_SHARES or not medidos
            else round(sum(x["liquido_com_custo_de_saida_usdc_por_hora"] for x in medidos), 4)
        ),
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


def _ler_entradas(
    caminho_pools: str, caminho_markout: str | None
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Lê `--pools` (obrigatório) e `--markout` (opcional), contidos.

    Os dois vêm de fora do programa e vão direto ao sistema de arquivos;
    entregá-los ao `read_text` do jeito que chegam é travessia de caminho
    (S2083). A contenção é a mesma do `--json` de escrita, no espelho de
    leitura já usado pelo backtest e pelo SHADOW.

    `--pools` inválido levanta `ValueError` (o `main` sai com 2). O markout é
    opcional de propósito — o 1.6 ficou NÃO AVALIÁVEL por semanas justamente
    por faltar este dado —, então ausente ou inválido avisa e segue `None`,
    em vez de abortar a conta.
    """
    pools = json.loads(caminho_de_relatorio_lido(caminho_pools).read_text(encoding="utf-8"))
    if not caminho_markout:
        return pools, None
    try:
        entrada = caminho_de_relatorio_lido(caminho_markout)
    except ValueError as erro:
        print(f"aviso: {erro}", file=sys.stderr)
        return pools, None
    return pools, json.loads(entrada.read_text(encoding="utf-8"))


def _linha_do_mercado(
    m: dict[str, Any],
    vol: dict[str, Any],
    custo_c: float | None,
    custo_saida_c: float | None,
) -> dict[str, Any]:
    """A conta de UM mercado: receita, custo no pior caso, custo de saída."""
    receita = float(m["receita_usdc_por_hora_minima"])
    shares_h = vol["shares_por_hora"]
    custo_h = None if custo_c is None else round(shares_h * custo_c / 100.0, 4)
    # Custo de saída por hora: o MESMO fluxo (shares/h do taker, hipótese
    # pessimista de que todo ele nos atropela) × max(markout 30 min, spread/2)
    # em ¢/share. `None` quando não medido — nunca zero.
    custo_saida_h = (
        None if custo_saida_c is None else round(shares_h * custo_saida_c / 100.0, 4)
    )
    return {
        "pergunta": m.get("pergunta"),
        "condition_id": m["condition_id"],
        "daily_rate_usdc": m.get("daily_rate_usdc"),
        "receita_usdc_por_hora": round(receita, 4),
        "volume": vol,
        "custo_maximo_usdc_por_hora": custo_h,
        "liquido_no_pior_caso_usdc_por_hora": (
            None if custo_h is None else round(receita - custo_h, 4)
        ),
        "custo_de_saida_usdc_por_hora": custo_saida_h,
        "liquido_com_custo_de_saida_usdc_por_hora": (
            None if custo_saida_h is None else round(receita - custo_saida_h, 4)
        ),
        # O número de SELEÇÃO: paga-se por unidade de fluxo que nos atropela,
        # não por hora bruta.
        "receita_por_mil_shares": (
            round(receita / shares_h * 1000, 4) if shares_h > 0 else None
        ),
    }


#: O que a coluna vazia diz quando não há medida. NUNCA `0.000`: zero é um
#: custo medido e baixo, ausente é custo desconhecido, e a conta do 1.12
#: fecha por 3% de custo — a diferença entre os dois decide o item.
SEM_MEDIDA = "—"


def _imprimir_custo_de_saida(
    relatorio: dict[str, Any], saida_por_mercado: dict[str, float] | None
) -> None:
    """O termo que decide o 1.12, ou o aviso de que ele não foi medido.

    O aviso vai para o **stdout**, não para o stderr. Ele não é erro de
    programa: é o achado mais importante da rodada. No stderr ele some num
    `> arquivo.txt` e o que sobra gravado é uma conta que termina em
    `LÍQUIDO +293,46 USDC/h` sem nada dizendo que o custo que a reprova nunca
    entrou — medida de ausência virando ausência de medida pelo canal de
    saída (rodada de 2026-09-17 na VPS).
    """
    if saida_por_mercado is None:
        print(
            "CUSTO DE SAÍDA AUSENTE — o 1.12 continua 🟡, e as somas abaixo "
            "NÃO são o veredito: elas param no markout de 5 s, que é "
            "justamente o que o quadro julgou insuficiente em 2026-09-14. "
            "O markout precisa vir com recorte por mercado e horizonte de "
            "30 min — o que exige uma coleta MAIOR que 30 min "
            "(markout_dos_pools.py --duracao 4h)."
        )
        return
    for nome, s in relatorio["por_recorte"].items():
        liq = s["liquido_com_custo_de_saida_usdc_por_hora"]
        nos_medidos = s["liquido_com_custo_de_saida_nos_medidos_usdc_por_hora"]
        print(
            f"  {nome:<10} líquido COM custo de saída: {liq!s:>10} USDC/h "
            f"({s['mercados_sem_custo_de_saida']} de {s['mercados']} sem medida; "
            f"nos {s['mercados_com_custo_de_saida']} medidos: "
            f"{_celula(nos_medidos, 9, '+')} USDC/h, receita "
            f"{_celula(s['receita_nos_medidos_usdc_por_hora'], 8)})"
        )


def _celula(valor: Any, largura: int, sinal: str = "") -> str:
    """Número formatado, ou `—` quando a medida não existe."""
    if valor is None:
        return f"{SEM_MEDIDA:>{largura}}"
    return f"{valor:>{sinal}{largura}.3f}"


def _imprimir_por_mercado(com_conta: list[dict[str, Any]]) -> None:
    """A tabela por mercado, com a coluna do custo de SAÍDA ao lado.

    Ela existe porque `liq/h` sozinho é a conta de 5 s, e quem lê a tabela não
    tem como saber disso pela tabela.
    """
    print()
    cab = ("mercado", "receita/h", "custo/h", "liq/h 5s", "liq/h saída", "USDC/1k sh")
    print(
        f"{cab[0]:<42} {cab[1]:>9} {cab[2]:>9} {cab[3]:>9} {cab[4]:>11} {cab[5]:>10}"
    )
    for x in com_conta[:15]:
        print(
            f"{str(x['pergunta'])[:42]:<42} "
            f"{_celula(x['receita_usdc_por_hora'], 9)} "
            f"{_celula(x['custo_maximo_usdc_por_hora'], 9)} "
            f"{_celula(x['liquido_no_pior_caso_usdc_por_hora'], 9, '+')} "
            f"{_celula(x['liquido_com_custo_de_saida_usdc_por_hora'], 11, '+')} "
            f"{x['receita_por_mil_shares']!s:>10}"
        )


def _imprimir_somas(
    relatorio: dict[str, Any], saida_por_mercado: dict[str, float] | None
) -> None:
    """As somas, com o cabeçalho dizendo DE QUAL conta elas são.

    "pior caso de custo" era ambíguo: o pior caso é o do FLUXO (todo ele nos
    atropela), não o do custo — e com o custo de saída ausente a linha lia
    como se fosse o resultado final.
    """
    qual = "markout 5 s" if saida_por_mercado is None else "markout 5 s e saída"
    print(f"\nsomas (pior caso de FLUXO; {qual}):")
    for nome, s in relatorio["por_recorte"].items():
        linha = (
            f"  {nome:<10} {s['mercados']:>3} mercados: "
            f"receita {s['receita_usdc_por_hora']:>8.2f} - "
            f"custo {s['custo_maximo_usdc_por_hora']:>8.2f} = "
            f"LÍQUIDO {s['liquido_no_pior_caso_usdc_por_hora']:>+8.2f} USDC/h"
        )
        if saida_por_mercado is None:
            linha += f"  (custo de saída: {SEM_MEDIDA})"
        print(linha)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="conta_do_maker_nos_pools")
    parser.add_argument("--pools", required=True)
    parser.add_argument("--markout", default=None)
    parser.add_argument("--top", type=int, default=60)
    parser.add_argument("--json", default=None)
    args = parser.parse_args(argv)

    try:
        pools, markout = _ler_entradas(args.pools, args.markout)
    except ValueError as erro:
        print(str(erro), file=sys.stderr)
        return 2
    custo_c, n_exec = _markout_adverso(markout)
    saida_por_mercado = _custo_de_saida(markout)

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
            saida = (saida_por_mercado or {}).get(str(m.get("slug")))
            linhas.append(_linha_do_mercado(m, vol, custo_c, saida))
            if i % 20 == 0:
                print(f"  {i}/{len(alvo)} mercados…", file=sys.stderr)

    com_conta = [x for x in linhas if x["liquido_no_pior_caso_usdc_por_hora"] is not None]
    com_conta.sort(key=_chave_do_selector)

    def _soma(quantos: int) -> dict[str, Any]:
        return _soma_do_recorte(com_conta[:quantos])

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
        "custo_de_saida": {
            "ausente": saida_por_mercado is None,
            "horizonte": HORIZONTE_DE_SAIDA,
            "regra": (
                "max(|markout adverso a 30 min|, spread/2 no fill) em c/share "
                "x shares/h do fluxo taker (mesma hipotese pessimista da conta de 5 s). "
                "O 1.12d le `liquido_do_1_12d_usdc_por_hora`: soma sobre os mercados "
                f"MEDIDOS, valida so com >= {COBERTURA_MINIMA_DE_SHARES:.0%} das shares "
                "do recorte cobertas. Os outros dois liquidos (cobertura completa e "
                "medidos sem exigencia) ficam publicados ao lado, como diagnostico"
            ),
            "nota": (
                "É o termo que o 1.12 não media. Ausente aqui significa que o "
                "markout veio sem recorte por mercado ou sem o horizonte de 30 "
                "min — rode markout_dos_pools.py da versão de 2026-09-14 ou "
                "posterior."
            ),
        },
        "mercados_avaliados": len(linhas),
        "ordenado_por": ORDENADO_POR,
        "nota_do_selector": (
            "Os recortes sao os N primeiros nesta ordem — e portanto os "
            "mercados que um LIVE operaria. Ate 2026-09-18 a ordem era o "
            "liquido no pior caso sobre um universo escolhido por tamanho de "
            "pool, e a rodada de 4 h mostrou que isso apanha justamente os "
            "mercados em que o custo de saida come a receita."
        ),
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

    print(f"\nmarkout usado: -{custo_c} c/share (5s, {n_exec} execuções)")
    _imprimir_custo_de_saida(relatorio, saida_por_mercado)
    _imprimir_por_mercado(com_conta)
    _imprimir_somas(relatorio, saida_por_mercado)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
