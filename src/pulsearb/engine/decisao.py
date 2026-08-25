"""A escolha entre os dois jogos, num lugar só.

Mora aqui, e não dentro do `BacktestRunner`, pela mesma razão que
`duracao_do_slug` saiu do backtest: o motor ao vivo e o backtest precisam
produzir a MESMA probabilidade a partir do mesmo estado. Se cada um tivesse a
sua cópia da escolha, uma divergência entre SHADOW e backtest pareceria
diferença de mercado quando seria diferença de código — e é justamente essa
comparação que justifica o SHADOW existir.
"""

from __future__ import annotations

from pulsearb.engine.hourly import prob_up_hourly
from pulsearb.engine.twap import (
    RealizedVol,
    TwapEstimate,
    TwapTracker,
    prob_up_twap,
)

#: O jogo TWAP: resolve pela média dos últimos 60 s contra a âncora.
JOGO_TWAP = "twap"
#: O jogo horário: resolve pelo candle da Binance contra o preço de abertura.
JOGO_HORARIO = "horario"


def estimar_prob_up(
    *,
    jogo: str,
    ancora: float,
    twap: TwapTracker,
    vol: RealizedVol,
    preco_spot: float,
    seconds_left: float,
) -> TwapEstimate:
    """P(Up) neste instante, pelo jogo da janela.

    Os dois jogos são fisicamente diferentes (API_NOTES §13.4): o TWAP tem
    fração da média já travada nos últimos 60 s; o horário compara contra o
    preço de abertura do candle. Só a escolha entre eles mora aqui — quem
    chama não precisa saber qual é.
    """
    if jogo == JOGO_TWAP:
        locked_mean, locked_weight = twap.locked_mean_and_weight(seconds_left)
        return prob_up_twap(
            ancora=ancora,
            spot=preco_spot,
            seconds_left=seconds_left,
            sigma_1s=vol.sigma_1s,
            locked_mean=locked_mean,
            locked_weight=locked_weight,
            twap_atual=twap.current_twap,
            vol_ready=vol.ready,
        )
    return prob_up_hourly(
        open_price=ancora,
        spot=preco_spot,
        seconds_left=seconds_left,
        sigma_1s=vol.sigma_1s,
        vol_ready=vol.ready,
    )
