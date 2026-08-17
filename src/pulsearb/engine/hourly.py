"""Modelo da janela horária — P(close ≥ open) do candle 1h da Binance.

Mais simples que o TWAP endgame de propósito: aqui **não existe média
parcialmente travada**. O candle resolve por um único ponto (o close), então
não há colapso progressivo de incerteza — só o encolhimento normal do horizonte
de uma caminhada aleatória.

    P(Up) = P(S_T ≥ open),  S_T = S·exp(σ·√t·Z − σ²t/2)

O `open` vem do próprio candle (campo `o` do kline_1h), não de suposição:
API_NOTES 12.2b. Empate resolve Up, igual ao jogo TWAP.

Consequência prática: a janela horária tem 3600s de horizonte contra 300s da
de 5m, e nenhum mecanismo de travamento. É estruturalmente MENOS previsível —
o que só se paga se o preço do book estiver muito errado.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from pulsearb.engine.twap import norm_cdf


@dataclass(frozen=True, slots=True)
class HourlyEstimate:
    prob_up: float
    spot: float
    open_price: float
    seconds_left: float
    sigma_1s: float
    confiavel: bool

    @property
    def bucket_tempo(self) -> str:
        t = self.seconds_left
        if t > 2400:
            return ">2400s"
        if t > 1200:
            return "2400-1200s"
        if t > 600:
            return "1200-600s"
        if t > 120:
            return "600-120s"
        return "<120s"


def prob_up_hourly(
    *,
    open_price: float,
    spot: float,
    seconds_left: float,
    sigma_1s: float,
    vol_ready: bool = True,
) -> HourlyEstimate:
    """P(close ≥ open) do candle horário."""
    if spot <= 0 or open_price <= 0:
        raise ValueError(f"preços devem ser positivos (spot={spot}, open={open_price})")

    seconds_left = max(0.0, seconds_left)
    if seconds_left <= 0.0 or sigma_1s <= 0.0:
        return HourlyEstimate(
            prob_up=1.0 if spot >= open_price else 0.0,
            spot=spot,
            open_price=open_price,
            seconds_left=seconds_left,
            sigma_1s=sigma_1s,
            confiavel=seconds_left <= 0.0,
        )

    sigma_t = sigma_1s * math.sqrt(seconds_left)
    # log(S_T/S) ~ N(−σ²t/2, σ²t). O drift de Itô é desprezível nestes
    # horizontes, mas custa uma linha e mantém a matemática correta.
    z = (math.log(open_price / spot) + 0.5 * sigma_t**2) / sigma_t
    return HourlyEstimate(
        prob_up=min(max(1.0 - norm_cdf(z), 0.0), 1.0),
        spot=spot,
        open_price=open_price,
        seconds_left=seconds_left,
        sigma_1s=sigma_1s,
        confiavel=vol_ready,
    )
