"""Modelo TWAP endgame — o núcleo analítico do projeto.

Substitui a aproximação genérica do plano original ("normal do log-retorno"),
que ignorava a estrutura que torna este mercado interessante: **parte do
resultado já está travada antes da janela fechar**.

## A intuição

A janela resolve comparando o TWAP de 60s no fechamento contra o preço de
abertura. TWAP de 60s é uma média móvel: quando faltam `t` segundos para o
fim, os primeiros `60 − t` segundos dessa média **já aconteceram e não mudam
mais**. Só os `min(t, 60)` segundos restantes ainda são aleatórios.

    TWAP_final = (soma_travada + soma_futura) / 60

Com t < 60, a fração já determinada é `(60 − t)/60`. Faltando 10s, 83% da
média final já está no bolso — a incerteza colapsa de forma *conhecida*, e é
exatamente aí que o preço do book pode estar errado.

Com t ≥ 60, nada da média final está travado ainda (a janela de 60s do
fechamento ainda nem começou), e o modelo degenera para "para onde o preço
vai andar em t segundos" — que é onde o edge é menor e o ruído é maior.

## O cálculo

Modelamos o preço futuro como caminhada aleatória com volatilidade σ (EWMA de
retornos de 1s). A contribuição futura à média é a integral do preço ao longo
dos segundos restantes; sob caminhada aleatória, a média dessas amostras tem:

    E[média_futura]  = S           (preço atual, martingale)
    Var[média_futura] = σ²·S²·k(m) com k(m) = (m−1)(2m−1)/(6m) para m amostras

O termo k(m) vem de Var[(1/m)·Σ S_i] com S_i = S·(1 + Σ_{j≤i} ε_j): os
retornos se acumulam, então amostras tardias carregam mais variância. Para
m grande, k(m) → m/3 — a média futura tem 1/3 da variância do preço final,
que é a razão de o TWAP ser mais previsível que o spot.

`P(Up) = P(TWAP_final ≥ âncora)` — o **≥** é literal: empate resolve Up
(API_NOTES 12.4).

## O que este modelo NÃO faz

Não modela drift, não modela reversão à média, não usa ML. Se o backtest do
M2 disser que não há edge, o problema não é falta de sofisticação do modelo —
é que a fee de 3,5% do capital no meio do book come qualquer previsão honesta.
Sofisticar o modelo antes de o M2 provar que existe sinal é otimizar o ruído.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

# Janela do TWAP usada pela Polymarket em TODAS as durações observadas
# (API_NOTES 12.3). É parâmetro, não constante mágica: entra por argumento.
TWAP_WINDOW_SECONDS_DEFAULT = 60.0


def norm_cdf(x: float) -> float:
    """Φ(x). math.erf é suficiente e não traz dependência."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def variance_factor(m: float) -> float:
    """k(m) = (m−1)(2m−1)/(6m): variância da média de m passos de uma
    caminhada aleatória, em unidades de σ² por passo.

    Casos de sanidade:
      k(1) = 0    — uma amostra só, tomada agora: sem incerteza
      k(2) = 1/4  — média de agora e do próximo passo
      k(m) → m/3  — a média futura tem ~1/3 da variância do preço final
    """
    if m <= 1.0:
        return 0.0
    return (m - 1.0) * (2.0 * m - 1.0) / (6.0 * m)


@dataclass(slots=True)
class RealizedVol:
    """Volatilidade realizada por EWMA de retornos de ~1s.

    `halflife_s` em segundos: o peso de uma observação cai pela metade a cada
    halflife. 120s por default — longo o bastante para não tremer a cada tick,
    curto o bastante para reagir a mudança de regime dentro de uma janela de 5m.
    """

    halflife_s: float = 120.0
    _var: float = 0.0
    _n: int = 0
    _last_price: float | None = None
    _last_ts_ns: int | None = None

    def update(self, price: float, ts_ns: int) -> None:
        if price <= 0:
            return
        if self._last_price is None or self._last_ts_ns is None:
            self._last_price, self._last_ts_ns = price, ts_ns
            return
        dt_s = (ts_ns - self._last_ts_ns) / 1e9
        if dt_s <= 0:
            return
        ret = math.log(price / self._last_price)
        # Normaliza para retorno de 1s: var escala com o tempo.
        ret_1s_sq = (ret * ret) / dt_s
        alpha = 1.0 - 0.5 ** (dt_s / self.halflife_s)
        self._var = ret_1s_sq if self._n == 0 else (1 - alpha) * self._var + alpha * ret_1s_sq
        self._n += 1
        self._last_price, self._last_ts_ns = price, ts_ns

    @property
    def sigma_1s(self) -> float:
        """Desvio-padrão do log-retorno de 1 segundo."""
        return math.sqrt(self._var) if self._var > 0 else 0.0

    @property
    def ready(self) -> bool:
        """Amostras suficientes para a estimativa valer alguma coisa."""
        return self._n >= 20


@dataclass(slots=True)
class TwapTracker:
    """Reconstrói o TWAP corrente a partir do stream de preços gravado.

    Guarda as amostras da janela de lookback e devolve tanto a média corrente
    quanto a **soma já travada** que vai entrar no TWAP do fechamento.
    """

    window_seconds: float = TWAP_WINDOW_SECONDS_DEFAULT
    samples: deque[tuple[int, float]] = field(default_factory=deque)  # (ts_ns, preço)

    def update(self, price: float, ts_ns: int) -> None:
        self.samples.append((ts_ns, price))
        corte = ts_ns - int(self.window_seconds * 1e9)
        while self.samples and self.samples[0][0] < corte:
            self.samples.popleft()

    @property
    def current_twap(self) -> float | None:
        if not self.samples:
            return None
        return sum(price for _, price in self.samples) / len(self.samples)

    @property
    def last_price(self) -> float | None:
        return self.samples[-1][1] if self.samples else None

    def locked_mean_and_weight(self, seconds_left: float) -> tuple[float, float]:
        """Parte da média final que já está determinada.

        Devolve `(média_das_amostras_travadas, peso)`, com peso em [0, 1]:
        a fração do TWAP do fechamento que essas amostras representam.

        Faltando `t` segundos, o TWAP do fechamento cobre o intervalo
        `[fim − 60, fim]`. As amostras já observadas que caem nesse intervalo
        são as dos últimos `60 − t` segundos.
        """
        if seconds_left >= self.window_seconds or not self.samples:
            return (0.0, 0.0)
        travados_s = self.window_seconds - seconds_left
        agora_ns = self.samples[-1][0]
        corte = agora_ns - int(travados_s * 1e9)
        travadas = [price for ts, price in self.samples if ts >= corte]
        if not travadas:
            return (0.0, 0.0)
        return (sum(travadas) / len(travadas), travados_s / self.window_seconds)


@dataclass(frozen=True, slots=True)
class TwapEstimate:
    prob_up: float
    twap_atual: float | None
    spot: float
    ancora: float
    seconds_left: float
    peso_travado: float      # fração da média final já determinada
    sigma_1s: float
    confiavel: bool          # False = vol não calibrada, não operar

    @property
    def bucket_tempo(self) -> str:
        """Bucket de tempo restante — a calibração é reportada por bucket
        porque a precisão do modelo muda de regime ao longo da janela."""
        t = self.seconds_left
        if t > 240:
            return ">240s"
        if t > 120:
            return "240-120s"
        if t > 60:
            return "120-60s"
        if t > 30:
            return "60-30s"
        return "<30s"


def prob_up_twap(
    *,
    ancora: float,
    spot: float,
    seconds_left: float,
    sigma_1s: float,
    locked_mean: float = 0.0,
    locked_weight: float = 0.0,
    window_seconds: float = TWAP_WINDOW_SECONDS_DEFAULT,
    twap_atual: float | None = None,
    vol_ready: bool = True,
) -> TwapEstimate:
    """P(TWAP_final ≥ âncora).

    `ancora` é o preço de abertura da janela — a semântica exata dessa âncora
    é validada empiricamente contra as resoluções gravadas
    (`engine/anchor.py`), não assumida.
    """
    if spot <= 0 or ancora <= 0:
        raise ValueError(f"preços devem ser positivos (spot={spot}, ancora={ancora})")

    seconds_left = max(0.0, seconds_left)
    locked_weight = min(max(locked_weight, 0.0), 1.0)
    futuro_weight = 1.0 - locked_weight

    # Janela fechada: o resultado é o TWAP corrente contra a âncora, e o
    # empate resolve Up.
    if seconds_left <= 0.0:
        final = twap_atual if twap_atual is not None else spot
        return TwapEstimate(
            prob_up=1.0 if final >= ancora else 0.0,
            twap_atual=twap_atual,
            spot=spot,
            ancora=ancora,
            seconds_left=0.0,
            peso_travado=1.0,
            sigma_1s=sigma_1s,
            confiavel=True,
        )

    # Média esperada do TWAP final = parte travada + parte futura (martingale).
    esperado = locked_weight * locked_mean + futuro_weight * spot

    # Quantas amostras futuras entram na média final.
    m = min(seconds_left, window_seconds)
    var_futuro = (sigma_1s**2) * (spot**2) * variance_factor(m)
    # Só a parte futura carrega incerteza; a travada é constante.
    desvio = futuro_weight * math.sqrt(max(var_futuro, 0.0))

    if desvio <= 0.0:
        prob = 1.0 if esperado >= ancora else 0.0
    else:
        # P(X ≥ ancora) com X ~ N(esperado, desvio²).
        prob = 1.0 - norm_cdf((ancora - esperado) / desvio)

    return TwapEstimate(
        prob_up=min(max(prob, 0.0), 1.0),
        twap_atual=twap_atual,
        spot=spot,
        ancora=ancora,
        seconds_left=seconds_left,
        peso_travado=locked_weight,
        sigma_1s=sigma_1s,
        confiavel=vol_ready and sigma_1s > 0.0,
    )
