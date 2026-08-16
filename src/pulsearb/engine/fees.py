"""Curva de taker fee dinâmica da Polymarket (crypto_fees_v2).

Fórmula VERIFICADA no código do SDK oficial e confirmada ao vivo em
2026-08-16 (docs/API_NOTES.md seções 5 e 12.6):

    fee_por_share(p) = r · (p · (1 − p))^e

com r (rate) e e (exponent) lidos POR MERCADO — Gamma `feeSchedule` ou CLOB
`fd`. Valores observados ao vivo: r=0.07, e=1, takerOnly=true. Este módulo
NÃO tem default de r/e: mercado sem fee legível não é operado (gate da
descoberta).

AS DUAS UNIDADES — a distinção que muda a estratégia (API_NOTES 5.1):

- `fee_pp_por_share(p)`: fee em USDC por share (= pontos percentuais do valor
  nominal de 1.00). Pico em p=0.50: 0.07·0.25 = 0.0175/share.
- `fee_sobre_capital(p)`: a MESMA fee expressa como fração do capital gasto
  (quem compra a p gasta p por share). fee_pp/p. Em p=0.50 → 3.5% do capital;
  em p=0.10 → 6.3% do capital, apesar de a fee/share ser menor.

A intuição "fee tende a zero nos extremos" só vale por share. Sobre o
capital, comprar o lado barato é proporcionalmente MAIS caro — e é o lado
barato que esta estratégia compra. O sinal do M3 desconta a fee na unidade
certa: edge é medido em probabilidade (por share), mas o risco/retorno do
caixa é sobre capital.
"""

from __future__ import annotations


def fee_pp_por_share(p: float, *, rate: float, exponent: float) -> float:
    """Fee em USDC por share, no preço p ∈ (0, 1).

    p fora de (0,1) é erro de chamador — levanta ValueError em vez de
    devolver número plausível e errado.
    """
    if not 0.0 < p < 1.0:
        raise ValueError(f"preço deve estar em (0,1), veio {p}")
    if rate < 0 or exponent < 0:
        raise ValueError(f"rate/exponent inválidos: r={rate}, e={exponent}")
    return rate * (p * (1.0 - p)) ** exponent


def fee_sobre_capital(p: float, *, rate: float, exponent: float) -> float:
    """A mesma fee, como fração do capital investido (compra a p)."""
    return fee_pp_por_share(p, rate=rate, exponent=exponent) / p
