#!/usr/bin/env python3
"""Calibração em faixas de 10 %, Brier e Kelly — SÓ a partir de relatórios já gravados.

Não reprocessa gravação nenhuma. Lê `backtest.calibracao` dos JSON em
`relatorios/` (as 20 faixas de 5 % por balde de tempo que o backtest já
publica), junta as faixas em 10 % e deriva:

- a tabela previsto × realizado por faixa, com `n`;
- o ECE nas 10 faixas e nas 20 originais;
- o Brier pela decomposição de Murphy (confiabilidade − resolução +
  incerteza) sobre as faixas. **É uma cota, não o Brier exato**: dentro de
  cada faixa as previsões são substituídas pela média da faixa. O erro
  dessa troca é limitado pela variância intra-faixa (≤ 0,05²/12 ≈ 0,0002
  com 20 faixas) mais um termo cruzado que, no pior caso, chega a ~0,015.
  O Brier exato exige previsão a previsão, que nenhum relatório guarda.
- a fração de Kelly que sairia das probabilidades registradas pelo SHADOW
  e o crescimento esperado se a probabilidade real for a que o backtest
  REALIZOU na faixa, não a prometida.

Uso:

    .venv/bin/python scripts/calibracao_dos_relatorios.py
    .venv/bin/python scripts/calibracao_dos_relatorios.py relatorios/X.json ...
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PADRAO = [
    ("24/08 — curva MEDIDA de 23/08", "relatorios/M2_DIRECAO_20260824.json"),
    ("25/08 (5 h) — curva MEDIDA de 23/08", "relatorios/M2_DIA_INDEPENDENTE_25AGO.json"),
    ("24/08 — modelo DERIVADO", "relatorios/DIA_20260824.json"),
    ("12-15/09 (72 h) — modelo DERIVADO", "relatorios/M2_72H_20260912.json"),
]


def _faixa_de_10(rotulo: str) -> int:
    inicio = float(rotulo.split("-")[0])
    return min(int(inicio * 10 + 1e-9), 9)


def _agregar(calibracao: dict, largura: int) -> dict[str, list[float]]:
    """{faixa: [n, soma_previsto, soma_realizado]} somando os baldes de tempo."""
    agg: dict[str, list[float]] = {}
    for balde in calibracao.values():
        for rotulo, faixa in balde["curva_de_confiabilidade"].items():
            chave = (
                f"{_faixa_de_10(rotulo) * 10:>3}-{_faixa_de_10(rotulo) * 10 + 10:<3}%"
                if largura == 10
                else rotulo
            )
            acc = agg.setdefault(chave, [0, 0.0, 0.0])
            n = faixa["n"]
            acc[0] += n
            acc[1] += faixa["previsto"] * n
            acc[2] += faixa["realizado"] * n
    return agg


def _murphy(agg: dict[str, list[float]]) -> tuple[float, float, float, float, float]:
    total = sum(a[0] for a in agg.values())
    o_barra = sum(a[2] for a in agg.values()) / total
    conf = sum(a[0] * (a[1] / a[0] - a[2] / a[0]) ** 2 for a in agg.values() if a[0]) / total
    res = sum(a[0] * (a[2] / a[0] - o_barra) ** 2 for a in agg.values() if a[0]) / total
    inc = o_barra * (1 - o_barra)
    ece = sum(abs(a[1] - a[2]) for a in agg.values()) / total
    return conf, res, inc, ece, o_barra


def relatorio(nome: str, caminho: Path) -> None:
    dados = json.loads(caminho.read_text())
    calibracao = dados["backtest"]["calibracao"]
    modelo = dados.get("modelo_de_variancia") or {}
    agg10 = _agregar(calibracao, 10)
    agg20 = _agregar(calibracao, 5)
    total = sum(a[0] for a in agg10.values())
    conf, res, inc, ece10, o_barra = _murphy(agg10)
    conf20, res20, _, ece20, _ = _murphy(agg20)
    print(f"\n===== {nome}  [{caminho.name}]")
    print(
        f"  curva medida={modelo.get('medida')} origem={modelo.get('origem')}  "
        f"N={total}  taxa-base Up={o_barra:.4f}"
    )
    print(f"  ECE: {ece10:.4f} (10 faixas) · {ece20:.4f} (20 faixas)")
    print(
        f"  Brier (cota, 20 faixas) = {conf20:.4f} - {res20:.4f} + {inc:.4f} = "
        f"{conf20 - res20 + inc:.4f}   [10 faixas: {conf - res + inc:.4f}]"
    )
    print(f"  {'faixa':>9} {'n':>8} {'% N':>6} {'previsto':>9} {'realizado':>9} {'erro':>8}")
    for chave in sorted(agg10, key=lambda c: int(c.split("-")[0])):
        n, sp, sr = agg10[chave]
        if not n:
            continue
        print(
            f"  {chave:>9} {int(n):>8} {100 * n / total:>5.1f}% "
            f"{sp / n:>9.4f} {sr / n:>9.4f} {sp / n - sr / n:>+8.4f}"
        )
    extremo = agg20.get("0.95-1.00")
    if extremo and extremo[0]:
        print(
            f"  faixa 0,95-1,00: n={int(extremo[0])} previsto={extremo[1] / extremo[0]:.4f} "
            f"realizado={extremo[2] / extremo[0]:.4f}"
        )


def kelly(prob_prometida: float, prob_real: float, precos: list[float]) -> None:
    """f* = (p − preço)/(1 − preço). Crescimento = p·ln(1+f·b) + (1−p)·ln(1−f)."""
    print(
        f"\n===== Kelly com p prometida {prob_prometida:.4f} e p real {prob_real:.4f}"
    )
    print(f"  {'preço':>6} {'f* (banca)':>11} {'edge real':>10} {'ln-cresc/trade':>15} {'trades p/ metade':>17}")
    for preco in precos:
        b = (1 - preco) / preco
        f = max(0.0, (prob_prometida - preco) / (1 - preco))
        g = prob_real * math.log1p(f * b) + (1 - prob_real) * math.log1p(-f) if f < 1 else float("-inf")
        metade = math.log(0.5) / g if g < 0 else float("inf")
        print(f"  {preco:>6.2f} {f:>11.3f} {prob_real - preco:>+10.3f} {g:>15.3f} {metade:>17.1f}")


def main(argv: list[str]) -> int:
    alvos = (
        [(Path(a).name, Path(a)) for a in argv]
        if argv
        else [(nome, RAIZ / rel) for nome, rel in PADRAO]
    )
    for nome, caminho in alvos:
        if not caminho.exists():
            print(f"  (ausente: {caminho})")
            continue
        relatorio(nome, caminho)
    # p50 das 7.216 aprovadas do SHADOW de 15 dias na VPS: 0,9911 (medido
    # pelo coordenador por streaming do diário). p real: o que a faixa
    # 0,95–1,00 REALIZOU nos dois relatórios do modelo derivado.
    for p_real in (0.8014, 0.7647):
        kelly(0.9911, p_real, [0.80, 0.90, 0.95])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
