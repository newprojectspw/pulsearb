#!/usr/bin/env python3
"""Mede V(t) do `twap_sixty` sobre a gravação — o instrumento da §2d-ter.

    python scripts/variancia_de_transicao.py ~/pulsearb-m2 \
        --json relatorios/VARIANCIA_24AGO.json

Uma passada só, e só sobre os ticks do RTDS: não reconstrói book nenhum, não
casa janela com resolução, não decide trade. Por isso custa uma fração do que
o backtest custa — o que ele mede não depende de nada disso.

## Por que este script existe

O `prob_up_twap` DERIVAVA a variância do valor de liquidação, e errou duas
vezes: faltou o tempo antes de a janela de 60 s começar (§2d-ter), e o
observável está trocado — a §13.8 do `API_NOTES.md` verificou que a janela
resolve por UM PONTO do stream `twap_sixty`, sem média nenhuma, e é esse mesmo
stream já suavizado que alimenta o `sigma_1s`.

Este script não deriva nada. Mede `V(t) = Var(T_{s+t}/T_s − 1)` direto do
gravado, que é a mesma metodologia com que a §13.8 achou a âncora.

## Como ler a saída

- `variancia_por_segundo` constante em `t` ⇒ caminhada aleatória, sem
  suavização visível.
- `variancia_por_segundo` CRESCENDO com `t` ⇒ a série é suavizada, e
  `fator_de_suavizacao_medido` diz o tamanho.
- `razao_contra_o_modelo` é quanto o modelo erra a variância naquele
  horizonte. Um significa acertar. A raiz dele é o erro no desvio-padrão, que
  é o que satura a probabilidade.

O veredito por ativo sai junto porque oito ativos concordando é evidência, e
um ativo destoando é defeito de feed daquele ativo — a distinção que o Bloco 0
levou três semanas para aprender a fazer.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from pulsearb.analysis.variancia_de_transicao import (
    HORIZONTES_PADRAO,
    TOLERANCIA_PADRAO_S,
    curva_de_variancia,
    veredito_da_curva,
)
from pulsearb.backtest.__main__ import caminho_de_escrita
from pulsearb.feeds.rtds import TOPIC_TWAP_60, parse_rtds_event
from pulsearb.replay.reader import RecordingReader

#: De quantos em quantos registros o progresso sai. O silêncio de três horas
#: do M2.15 foi caro o bastante para virar regra da casa.
PASSO_DO_PROGRESSO = 500_000


def series_da_gravacao(
    raiz: Path, *, progresso: bool = True
) -> dict[str, list[tuple[int, float]]]:
    """Uma passada, guardando só os ticks de `twap_sixty` por ativo."""
    leitor = RecordingReader(raiz)
    series: dict[str, list[tuple[int, float]]] = defaultdict(list)
    lidos = 0
    for record in leitor.iter_records(incluir_meta=False):
        lidos += 1
        if progresso and lidos % PASSO_DO_PROGRESSO == 0:
            print(
                f"{lidos:,} registros | "
                f"{sum(len(v) for v in series.values()):,} ticks de twap",
                file=sys.stderr,
                flush=True,
            )
        tick = parse_rtds_event(record.payload, record.ts_mono_ns, record.ts_wall_ns)
        if tick is not None and tick.topic == TOPIC_TWAP_60:
            series[tick.asset].append((record.ts_wall_ns, tick.price))
    return dict(series)


def medir(
    series: dict[str, list[tuple[int, float]]],
    *,
    horizontes_s: tuple[float, ...] = HORIZONTES_PADRAO,
    tolerancia_s: float = TOLERANCIA_PADRAO_S,
) -> dict[str, Any]:
    """Curva por ativo, mais a curva agregada de todos os ativos juntos."""
    por_ativo: dict[str, Any] = {}
    for asset, serie in sorted(series.items()):
        if len(serie) < 2:
            continue
        curva = curva_de_variancia(
            serie, horizontes_s=horizontes_s, tolerancia_s=tolerancia_s
        )
        curva["veredito"] = veredito_da_curva(curva)
        por_ativo[asset] = curva

    return {
        "por_ativo": por_ativo,
        "ativos": len(por_ativo),
        "concordam_sobre_suavizacao": _concordancia(por_ativo),
    }


def _concordancia(por_ativo: dict[str, Any]) -> dict[str, Any]:
    """Oito ativos concordando é evidência; um destoando é defeito de feed."""
    vereditos = [c["veredito"]["ha_suavizacao"] for c in por_ativo.values()]
    fatores = [
        c["veredito"]["fator_de_suavizacao_medido"]
        for c in por_ativo.values()
        if c["veredito"]["fator_de_suavizacao_medido"] is not None
    ]
    return {
        "com_suavizacao": sum(vereditos),
        "de": len(vereditos),
        "unanime": bool(vereditos) and (all(vereditos) or not any(vereditos)),
        "fator_minimo": min(fatores) if fatores else None,
        "fator_maximo": max(fatores) if fatores else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raiz", type=Path, help="pasta da gravação")
    parser.add_argument(
        "--json",
        dest="saida",
        help="caminho RELATIVO da saída (contido como no backtest, M2.5)",
    )
    parser.add_argument("--sem-progresso", action="store_true")
    args = parser.parse_args(argv)

    series = series_da_gravacao(args.raiz, progresso=not args.sem_progresso)
    if not series:
        print("nenhum tick de twap_sixty na gravacao", file=sys.stderr)
        return 2

    relatorio = medir(series)
    texto = json.dumps(relatorio, indent=2, ensure_ascii=False)
    if args.saida:
        destino = caminho_de_escrita(args.saida)
        destino.write_text(texto + "\n", encoding="utf-8")
        print(f"relatorio em {destino}", file=sys.stderr)
    else:
        print(texto)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
