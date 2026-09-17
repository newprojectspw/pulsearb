"""Qual janela do TWAP resolve cada duração: 30 s ou 60 s? Medido, não lido.

    python scripts/janela_do_twap.py ~/pulsearb-gravacao \
        --desde 2026-09-09T02:19Z --ate 2026-09-12T02:19Z \
        --json relatorios/JANELA_TWAP_M2_72H.json

## Por que existe (auditoria 2026-09-17 §2.2)

`engine/twap.py` usa `TWAP_WINDOW_SECONDS_DEFAULT = 60` para TODAS as
durações. O API_NOTES diz as duas coisas: §7 "5m usa 30 s", §12.3 "o dado
vivo mostra 60 s para todas" — e a nota do §12.3 NÃO TEM DATA. A mudança
pública é datada: a Polymarket passou a liquidar por TWAP Chainlink em
2026-08-07, com 30 s para 5m e 60 s para 15m/4h. Se a observação do §12.3
foi feita antes disso, o engine modela as janelas de 5m com a janela errada.

## O que mede

Para cada duração (300, 900, 14400 s) e cada janela candidata (30 e 60 s),
roda `evaluate_hypotheses` — o MESMO código do backtest (regra "mesmo
caminho") — sobre as janelas resolvidas da gravação e conta quantas
resoluções reais cada janela reproduz. A janela verdadeira reproduz todas;
a errada erra nas apertadas. O veredito por duração tem nome:

| veredito | quando |
|---|---|
| `JANELA_30` | só a de 30 s tem hipótese sobrevivente |
| `JANELA_60` | só a de 60 s tem hipótese sobrevivente |
| `AMBAS` | as duas sobrevivem — faltam janelas apertadas para separar |
| `NENHUMA` | nenhuma sobrevive — lacuna no stream ou âncora fora das testadas; o relatório diz qual errou menos |
| `SEM_DADO` | nenhuma janela resolvida dessa duração foi avaliada |

O relatório inteiro de cada (duração, janela) vai no JSON, hipótese a
hipótese, para quem quiser ler o que sobreviveu e o que não.

## O que NÃO decide

Este script não muda o engine. Se der `JANELA_30` para 5m, a mudança é
`TWAP_WINDOW_SECONDS_DEFAULT` por duração e a nota do §12.3 ganha data —
mas isso é outro commit, com o número deste relatório citado.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from pulsearb.backtest.__main__ import Progresso, RecordingIndex, caminho_de_leitura
from pulsearb.caminhos import caminho_de_escrita
from pulsearb.engine.anchor import (
    WindowOutcome,
    evaluate_hypotheses,
    report_anchor_validation,
)
from pulsearb.replay.reader import RecordingReader

JANELAS_PADRAO: tuple[float, ...] = (30.0, 60.0)

VEREDITOS: dict[str, str] = {
    "JANELA_30": "só a janela de 30 s reproduz todas as resoluções desta duração",
    "JANELA_60": "só a janela de 60 s reproduz todas as resoluções desta duração",
    "AMBAS": "as duas janelas sobrevivem — faltam janelas apertadas para separá-las",
    "NENHUMA": (
        "nenhuma janela reproduz todas as resoluções — lacuna no stream ou "
        "âncora fora das hipóteses; ver `menos_erros`"
    ),
    "SEM_DADO": "nenhuma janela resolvida desta duração foi avaliada",
}


def _rotulo(janela_s: float) -> str:
    return f"JANELA_{int(janela_s)}"


def _menos_erros(por_janela: dict[str, dict[str, Any]]) -> str | None:
    """A janela cuja MELHOR hipótese errou menos. None se nada foi avaliado."""
    candidatas = [
        (r["erros_minimos"], rotulo)
        for rotulo, r in por_janela.items()
        if r["erros_minimos"] is not None
    ]
    if not candidatas:
        return None
    candidatas.sort()
    if len(candidatas) > 1 and candidatas[0][0] == candidatas[1][0]:
        return None  # empate: não há "menos"
    return candidatas[0][1]


def _veredito(por_janela: dict[str, dict[str, Any]], janelas_s: Sequence[float]) -> str:
    avaliadas = [r for r in por_janela.values() if r["avaliadas"] > 0]
    if not avaliadas:
        return "SEM_DADO"
    vivas = [rotulo for rotulo, r in por_janela.items() if r["sobreviventes"]]
    if len(vivas) == 1 and len(janelas_s) == 2 and vivas[0] in ("JANELA_30", "JANELA_60"):
        return vivas[0]
    if len(vivas) >= 2:
        return "AMBAS"
    if len(vivas) == 1:
        return "AMBAS"  # janelas fora do par 30/60: o nome fixo não se aplica
    return "NENHUMA"


def comparar(
    por_duracao: dict[int, Sequence[WindowOutcome]],
    *,
    janelas_s: Sequence[float] = JANELAS_PADRAO,
) -> dict[str, Any]:
    """Roda as hipóteses com cada janela, por duração. Pura."""
    saida: dict[str, Any] = {}
    for duracao_s in sorted(por_duracao):
        outcomes = por_duracao[duracao_s]
        por_janela: dict[str, dict[str, Any]] = {}
        for janela_s in janelas_s:
            scores = evaluate_hypotheses(outcomes, window_seconds=janela_s)
            relatorio = report_anchor_validation(scores)
            avaliados = [s.total_avaliado for s in scores.values()]
            erros = [s.erros for s in scores.values() if s.total_avaliado > 0]
            por_janela[_rotulo(janela_s)] = {
                "janela_s": janela_s,
                "avaliadas": max(avaliados, default=0),
                "sobreviventes": [h.value for h, s in scores.items() if s.sobreviveu],
                "erros_minimos": min(erros) if erros else None,
                "relatorio": relatorio,
            }
        veredito = _veredito(por_janela, janelas_s)
        saida[str(duracao_s)] = {
            "duracao_s": duracao_s,
            "janelas_alimentadas": len(outcomes),
            "veredito": veredito,
            "o_que_significa": VEREDITOS[veredito],
            "menos_erros": _menos_erros(por_janela),
            "por_janela": por_janela,
        }
    return saida


def _hora_utc(bruto: str | None) -> datetime | None:
    if not bruto:
        return None
    return datetime.fromisoformat(bruto).replace(tzinfo=UTC)


def _janelas_csv(bruto: str) -> tuple[float, ...]:
    valores = tuple(float(x) for x in bruto.split(","))
    if not valores or any(v <= 0 for v in valores):
        raise ValueError(f"--janelas espera segundos positivos separados por vírgula: {bruto!r}")
    return valores


def _imprimir(rel: dict[str, Any]) -> None:
    for bloco in rel.values():
        print(
            f"\nduração {bloco['duracao_s']:>6} s: {bloco['janelas_alimentadas']} janelas "
            f"resolvidas → {bloco['veredito']} ({bloco['o_que_significa']})"
        )
        for rotulo, r in bloco["por_janela"].items():
            sobreviventes = ", ".join(r["sobreviventes"]) or "nenhuma"
            print(
                f"  {rotulo:>9}: avaliadas={r['avaliadas']:<5} erros mínimos="
                f"{r['erros_minimos']!s:<5} sobreviventes: {sobreviventes}"
            )
        if bloco["menos_erros"]:
            print(f"  menos erros: {bloco['menos_erros']}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="janela_do_twap")
    p.add_argument("recordings", help="diretório (ou arquivo) da gravação")
    p.add_argument("--desde", default=None)
    p.add_argument("--ate", default=None)
    p.add_argument("--janelas", default="30,60", help="segundos, separados por vírgula")
    p.add_argument("--json", default=None)
    args = p.parse_args(argv)
    try:
        caminho = caminho_de_leitura(args.recordings)
        destino = caminho_de_escrita(args.json) if args.json else None
        desde, ate = _hora_utc(args.desde), _hora_utc(args.ate)
        janelas_s = _janelas_csv(args.janelas)
    except ValueError as erro:
        print(str(erro), file=sys.stderr)
        return 2

    reader = RecordingReader(caminho, desde=desde, ate=ate)
    # Só o stream e as resoluções interessam: livro retido no mínimo.
    index = RecordingIndex(reader, limite_por_token=1, niveis_retidos=1, progresso=Progresso())
    index.build()

    por_duracao: dict[int, list[WindowOutcome]] = defaultdict(list)
    for j in index.janelas():
        if j.jogo != "twap" or j.resolveu_up is None:
            continue
        por_duracao[j.duracao_s].append(
            WindowOutcome(
                slug=j.slug,
                open_ts_ns=j.open_ts_ns,
                close_ts_ns=j.close_ts_ns,
                samples=tuple(index.streams.get(j.asset, [])),
                resolved_up=bool(j.resolveu_up),
            )
        )
    if not por_duracao:
        print("nenhuma janela TWAP resolvida na gravação — nada a comparar", file=sys.stderr)
        return 1

    rel = comparar(por_duracao, janelas_s=janelas_s)
    if destino is not None:
        destino.write_text(json.dumps(rel, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"relatório gravado em {destino}")
    _imprimir(rel)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
