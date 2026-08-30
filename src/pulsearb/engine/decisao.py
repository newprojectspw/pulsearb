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
    prob_up_twap_medido,
)
from pulsearb.engine.variancia import CurvaDeVariancia

#: O alvo do encolhimento. É 0,5 e não a taxa-base MEDIDA de propósito: a
#: taxa realizada do próprio período só se conhece depois dele (usá-la na
#: decisão seria olhar o futuro), e as medidas ficaram em 0,507–0,509 —
#: indistinguíveis de meio a meio para este fim.
BASE_DO_ENCOLHIMENTO = 0.5


def encolher_para_a_base(prob_up: float, fator: float) -> float:
    """A correção de escala do M2: p' = base + fator·(p − base).

    O que ela conserta, medido: o erro de calibração do preditor CRESCE com
    a confiança (−0,0105 a +0,1554 no dia 24), que é a assinatura de excesso
    de confiança — e excesso de confiança se corrige encolhendo a previsão
    em direção à taxa-base. Sobre os quatro baldes de 21–25/08, o fator
    ótimo levou o ECE de 0,058–0,199 para 0,003–0,009.

    O que ela NÃO é: um botão de ajuste do PnL. O fator vem de calibração
    medida em período ANTERIOR ao avaliado; ajustá-lo no próprio período
    é ajuste in-sample, e o relatório imprime essa ressalva onde o número
    aparece.

    Mora aqui — e não no runner — pela regra da casa: o motor ao vivo e o
    backtest precisam produzir a MESMA probabilidade a partir do mesmo
    estado. `fator = 1` é a identidade.
    """
    if not 0.0 < fator <= 1.0:
        raise ValueError(
            f"fator de encolhimento fora de (0, 1]: {fator!r} — acima de 1 "
            "seria INFLAR a confianca de um preditor ja superconfiante"
        )
    return BASE_DO_ENCOLHIMENTO + fator * (prob_up - BASE_DO_ENCOLHIMENTO)


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
    curva: CurvaDeVariancia | None = None,
) -> TwapEstimate:
    """P(Up) neste instante, pelo jogo da janela.

    Os dois jogos são fisicamente diferentes (API_NOTES §13.4): o TWAP tem
    fração da média já travada nos últimos 60 s; o horário compara contra o
    preço de abertura do candle. Só a escolha entre eles mora aqui — quem
    chama não precisa saber qual é.

    `curva` liga o caminho da variância MEDIDA (§2d-ter). Quando ela vem, o
    jogo TWAP usa `prob_up_twap_medido` — sem derivar variância e sem calcular
    média nenhuma, como a §13.8 manda. Quando não vem, o comportamento é o de
    antes, byte a byte.

    **Curva ausente para o ativo não cai no modelo velho em silêncio.** Quem
    escolhe operar com variância medida e não tem a curva daquele ativo recebe
    uma estimativa marcada `confiavel=False`, e o portão de risco recusa. Cair
    no modelo velho misturaria duas físicas no mesmo relatório sem aviso — a
    mesma forma do defeito do 1.4, com duas populações no mesmo número.
    """
    if jogo == JOGO_TWAP and curva is not None:
        return prob_up_twap_medido(
            ancora=ancora,
            spot=preco_spot,
            seconds_left=seconds_left,
            curva=curva,
            sigma_1s=vol.sigma_1s,
            vol_ready=vol.ready,
        )
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
