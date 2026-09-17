"""Número do fio e percentil. Um lugar só, porque onze cópias divergiram.

A auditoria de 2026-09-17 (§2.4–2.6) achou **onze** parsers de número —
seis `_numero`, três `_as_float`, `rewards_da_gamma.numero` e
`backtest._numero_bruto` — e **quatro** percentis. Eram cópias "iguais", e
duas já tinham deixado de ser: o `_as_float` do RTDS aceitava `bool` (e
`true` virava preço 1,0, provado por execução), e o `_percentil` do
`anchor_sweep` usava índice por piso enquanto os outros usavam nearest-rank
por teto — p50 de `[1..10]` dava 5 num e 6 no outro.

O princípio "mesmo caminho" existe para o motor e vale para utilitários pelo
mesmo motivo: uma cópia que diverge parece diferença de dado quando é
diferença de código. Aqui mora a única definição de cada um.
"""

from __future__ import annotations

from typing import Any


def numero(valor: Any) -> float | None:
    """`float` ou `None`. `bool`, `None`, string vazia e lixo viram `None`.

    Nunca zero: zero é valor legítimo ("pool de zero", "preço zero"), e
    confundir "não veio" com "veio zero" apaga exatamente a diferença que o
    portão precisa ver. `bool` é `None` porque `True` é um `int` para o
    Python e viraria 1,0 — dado malformado recusa, não converte.
    """
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    if isinstance(valor, str):
        try:
            return float(valor)
        except ValueError:
            return None
    return None


def percentil(valores: list[float] | list[int], pct: float) -> float | int | None:
    """Percentil por nearest-rank (teto), `pct` em 0–100. Vazio é `None`.

    Ordena internamente: passar lista já ordenada custa O(n log n) a mais e
    não muda o resultado, e a alternativa — exigir ordenação do chamador — é
    o tipo de contrato que uma das quatro cópias já tinha quebrado em
    silêncio. Devolve o elemento da lista (int continua int), sem
    arredondar: arredondamento é formatação, e fica com quem imprime.
    """
    if not valores:
        return None
    ordenados = sorted(valores)
    n = len(ordenados)
    rank = max(1, min(n, int(-(-pct * n // 100))))
    return ordenados[rank - 1]
