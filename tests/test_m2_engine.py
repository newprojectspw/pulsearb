"""Modelo TWAP endgame, modelo horário, âncora e volatilidade."""

from __future__ import annotations

import math

import pytest

from pulsearb.engine.anchor import (
    AnchorHypothesis,
    WindowOutcome,
    compute_anchor,
    evaluate_hypotheses,
    report_anchor_validation,
)
from pulsearb.engine.hourly import prob_up_hourly
from pulsearb.engine.twap import (
    RealizedVol,
    TwapTracker,
    norm_cdf,
    prob_up_twap,
    variance_factor,
)


# ------------------------------------------------------------------ básicos
def test_norm_cdf():
    assert norm_cdf(0.0) == pytest.approx(0.5)
    assert norm_cdf(1.96) == pytest.approx(0.975, abs=1e-3)
    assert norm_cdf(-1.96) == pytest.approx(0.025, abs=1e-3)


def test_variance_factor_casos_de_sanidade():
    # Uma amostra só, tomada agora: sem incerteza.
    assert variance_factor(1) == 0.0
    assert variance_factor(0.5) == 0.0
    # Média de dois passos.
    assert variance_factor(2) == pytest.approx(0.25)
    # Assintótico: k(m) → m/3, a média tem 1/3 da variância do preço final.
    assert variance_factor(1000) == pytest.approx(1000 / 3, rel=1e-2)


def _fator_pela_definicao(seconds_left: int, janela: int = 60) -> float:
    """Variância do TWAP de fechamento somando o coeficiente de cada choque.

    Escrita do jeito mais burro possível de propósito: é a DEFINIÇÃO, não a
    forma fechada. O TWAP do fechamento é a média das amostras nos instantes
    `[t − 60, t)`; cada choque de 1 s entra em todas as amostras posteriores
    a ele. Se a forma fechada do modelo divergir daqui, é a forma fechada que
    está errada.

    Devolve o fator em unidades de σ²·S², para comparar direto com o que
    `prob_up_twap` multiplica.
    """
    instantes = list(range(max(0, seconds_left - janela), seconds_left)) or [0]
    n = len(instantes)
    coeficiente: dict[int, int] = {}
    for i in instantes:
        for j in range(1, i + 1):
            coeficiente[j] = coeficiente.get(j, 0) + 1
    return sum((c / n) ** 2 for c in coeficiente.values())


def _fator_usado_pelo_modelo(seconds_left: float, janela: float = 60.0) -> float:
    """O fator que `prob_up_twap` usa, lido de volta do desvio que ela aplica.

    Roda o modelo com σ e S conhecidos e âncora a um desvio-padrão do spot,
    invertendo Φ para recuperar o desvio — assim o teste cobra a função de
    verdade, não uma cópia da fórmula.
    """
    sigma, spot = 1e-3, 100.0
    est = prob_up_twap(
        ancora=spot * 1.001,
        spot=spot,
        seconds_left=seconds_left,
        sigma_1s=sigma,
        window_seconds=janela,
    )
    # prob = 1 − Φ((ancora − spot)/desvio)  ⇒  desvio = (ancora − spot)/Φ⁻¹(1 − prob)
    z = _inversa_normal(1.0 - est.prob_up)
    desvio = (spot * 0.001) / z
    return (desvio / (sigma * spot)) ** 2


def _inversa_normal(p: float) -> float:
    """Φ⁻¹ por bisseção. Só o teste precisa dela; não vale uma dependência."""
    lo, hi = -12.0, 12.0
    for _ in range(200):
        meio = (lo + hi) / 2.0
        if norm_cdf(meio) < p:
            lo = meio
        else:
            hi = meio
    return (lo + hi) / 2.0


@pytest.mark.parametrize("seconds_left", [2, 10, 30, 59, 60, 61, 120, 180, 240, 300, 600])
def test_variancia_do_twap_bate_com_a_definicao(seconds_left):
    """O defeito de calibração de 2026-08-29, travado.

    Até aqui o modelo usava `k(min(t, 60))` e nada mais. Com `t > 60` a janela
    de fechamento ainda não começou — o preço caminha `t − 60` segundos antes
    da primeira amostra —, e esse deslocamento é comum às 60 amostras, então
    entra INTEIRO na variância da média. Sem ele o desvio ficava congelado no
    valor de 60 s: 31 % do real a 240 s, 27 % a 300 s.

    O efeito não era de PnL, era de calibração: com o desvio 3× menor que o
    real, P(Up) satura em 0 e 1. Era isso que o critério 1.3 media como
    superconfiança, e é isso que explica o viés "MISTO e SEM ORDEM" da §2d —
    o tamanho do erro depende de `seconds_left`, então nenhum fator único de
    encolhimento podia corrigir todas as faixas de uma vez.
    """
    assert _fator_usado_pelo_modelo(seconds_left) == pytest.approx(
        _fator_pela_definicao(seconds_left), rel=1e-6
    )


def test_correcao_da_variancia_nao_toca_no_regime_abaixo_da_janela():
    """Abaixo de 60 s a fórmula é a de antes — o termo novo é zero ali.

    Importa porque `<30s` e `60-30s` são os baldes onde o TWAP tem fração
    travada, que é a tese do modelo. Se a correção os movesse, ela estaria
    consertando uma coisa e quebrando outra.
    """
    for t in (5, 15, 30, 45, 59):
        assert _fator_usado_pelo_modelo(t) == pytest.approx(variance_factor(t), rel=1e-6)


def test_previsao_deixa_de_saturar_nos_horizontes_longos():
    """O sintoma que o 1.3 mediu: previsões despejadas nos extremos.

    Com σ congelado no valor de 60 s, um desvio de 20 bps a 240 s do
    fechamento dava P(Up) ≈ 0,99. Com a variância certa ele vale bem menos —
    a mesma informação, sem a confiança que o modelo não tinha como ter.
    """
    comum = dict(spot=100.0, sigma_1s=2e-4, seconds_left=240.0)
    alta = prob_up_twap(ancora=100.0 * (1 - 0.002), **comum)
    baixa = prob_up_twap(ancora=100.0 * (1 + 0.002), **comum)

    assert 0.5 < alta.prob_up < 0.95, alta.prob_up
    assert 0.05 < baixa.prob_up < 0.5, baixa.prob_up
    # Continua informativo: 20 bps a favor ainda vale bem mais que 20 contra.
    assert alta.prob_up - baixa.prob_up > 0.3


# ------------------------------------------------------------ volatilidade
def test_realized_vol_estima_sigma():
    vol = RealizedVol(halflife_s=60)
    sigma_real = 1e-3
    preco = 100.0
    rng = __import__("random").Random(7)
    for i in range(2000):
        preco *= math.exp(rng.gauss(0, sigma_real))
        vol.update(preco, int(i * 1e9))
    assert vol.ready
    # EWMA de amostra finita não crava o valor; ordem de grandeza é o que importa.
    assert vol.sigma_1s == pytest.approx(sigma_real, rel=0.5)


def test_vol_nao_fica_pronta_com_poucas_amostras():
    vol = RealizedVol()
    for i in range(5):
        vol.update(100.0 + i, int(i * 1e9))
    assert not vol.ready


def test_vol_ignora_lixo():
    vol = RealizedVol()
    vol.update(-1.0, 0)          # preço negativo
    vol.update(100.0, int(1e9))
    vol.update(100.0, int(1e9))  # dt = 0
    assert vol.sigma_1s == 0.0


# ------------------------------------------------------------ TwapTracker
def test_tracker_media_e_janela():
    tracker = TwapTracker(window_seconds=60)
    for i in range(120):
        tracker.update(100.0 + i, int(i * 1e9))
    # Só os últimos 60s ficam
    assert len(tracker.samples) == 61
    assert tracker.last_price == 219.0
    assert tracker.current_twap == pytest.approx(sum(range(159, 220)) / 61)


def test_peso_travado_cresce_no_fim_da_janela():
    tracker = TwapTracker(window_seconds=60)
    for i in range(120):
        tracker.update(100.0, int(i * 1e9))

    # Faltando 60s ou mais: nada da média final está travado ainda.
    _, peso = tracker.locked_mean_and_weight(60.0)
    assert peso == 0.0
    _, peso = tracker.locked_mean_and_weight(300.0)
    assert peso == 0.0

    # Faltando 10s: 50/60 da média já aconteceu.
    media, peso = tracker.locked_mean_and_weight(10.0)
    assert peso == pytest.approx(50 / 60)
    assert media == pytest.approx(100.0)

    # Faltando 0s: tudo travado.
    _, peso = tracker.locked_mean_and_weight(0.0)
    assert peso == pytest.approx(1.0)


# --------------------------------------------------------------- prob_up_twap
def test_no_dinheiro_e_meio_a_meio():
    est = prob_up_twap(ancora=100.0, spot=100.0, seconds_left=120, sigma_1s=1e-3)
    assert est.prob_up == pytest.approx(0.5, abs=1e-6)


def test_spot_acima_da_ancora_favorece_up():
    est = prob_up_twap(ancora=100.0, spot=101.0, seconds_left=120, sigma_1s=1e-3)
    assert est.prob_up > 0.5


def test_empate_resolve_up_na_janela_fechada():
    """O ≥ é literal: TWAP exatamente igual à âncora resolve Up."""
    est = prob_up_twap(
        ancora=100.0, spot=100.0, seconds_left=0.0, sigma_1s=1e-3, twap_atual=100.0
    )
    assert est.prob_up == 1.0
    est = prob_up_twap(
        ancora=100.0, spot=100.0, seconds_left=0.0, sigma_1s=1e-3, twap_atual=99.999
    )
    assert est.prob_up == 0.0


def test_certeza_cresce_com_a_media_travada():
    """O coração do modelo: com metade da média travada acima da âncora,
    a mesma distância de preço vale muito mais."""
    sem_trava = prob_up_twap(
        ancora=100.0, spot=100.5, seconds_left=30, sigma_1s=1e-3, locked_weight=0.0
    )
    com_trava = prob_up_twap(
        ancora=100.0,
        spot=100.5,
        seconds_left=30,
        sigma_1s=1e-3,
        locked_mean=100.5,
        locked_weight=0.5,
    )
    assert com_trava.prob_up > sem_trava.prob_up


def test_media_travada_abaixo_da_ancora_derruba_a_prob():
    est = prob_up_twap(
        ancora=100.0,
        spot=100.2,
        seconds_left=10,
        sigma_1s=1e-3,
        locked_mean=99.0,   # já ficou muito abaixo
        locked_weight=0.83,
    )
    assert est.prob_up < 0.1


def test_prob_fica_entre_zero_e_um():
    for spot in (50.0, 99.0, 100.0, 101.0, 200.0):
        est = prob_up_twap(ancora=100.0, spot=spot, seconds_left=60, sigma_1s=1e-2)
        assert 0.0 <= est.prob_up <= 1.0


def test_buckets_de_tempo():
    buckets = [
        prob_up_twap(ancora=100, spot=100, seconds_left=t, sigma_1s=1e-3).bucket_tempo
        for t in (300, 200, 90, 45, 10)
    ]
    assert buckets == [">240s", "240-120s", "120-60s", "60-30s", "<30s"]


def test_preco_invalido_explode():
    with pytest.raises(ValueError):
        prob_up_twap(ancora=0.0, spot=100.0, seconds_left=60, sigma_1s=1e-3)


def test_vol_nao_calibrada_marca_nao_confiavel():
    est = prob_up_twap(
        ancora=100.0, spot=100.0, seconds_left=60, sigma_1s=1e-3, vol_ready=False
    )
    assert not est.confiavel


# ------------------------------------------------------------------ horário
def test_hourly_no_dinheiro():
    est = prob_up_hourly(open_price=100.0, spot=100.0, seconds_left=1800, sigma_1s=1e-4)
    assert est.prob_up == pytest.approx(0.5, abs=0.01)


def test_hourly_encurta_horizonte_aumenta_certeza():
    longe = prob_up_hourly(open_price=100.0, spot=101.0, seconds_left=3000, sigma_1s=1e-4)
    perto = prob_up_hourly(open_price=100.0, spot=101.0, seconds_left=60, sigma_1s=1e-4)
    assert perto.prob_up > longe.prob_up


def test_hourly_empate_resolve_up():
    est = prob_up_hourly(open_price=100.0, spot=100.0, seconds_left=0, sigma_1s=1e-4)
    assert est.prob_up == 1.0


# ------------------------------------------------------------------- âncora
SAMPLES = tuple((int(i * 1e9), 100.0 + i) for i in range(10))  # 100..109


def test_hipoteses_de_ancora_dao_valores_distintos():
    abertura = int(4.5e9)  # entre a amostra 4 (104) e a 5 (105)
    assert compute_anchor(AnchorHypothesis.ULTIMO_ANTES, SAMPLES, abertura) == 104.0
    assert compute_anchor(AnchorHypothesis.PRIMEIRO_DEPOIS, SAMPLES, abertura) == 105.0
    assert compute_anchor(AnchorHypothesis.INTERPOLADO, SAMPLES, abertura) == pytest.approx(104.5)
    # mais_proximo desempata pelo delta; aqui é meio a meio, pega o primeiro
    assert compute_anchor(AnchorHypothesis.MAIS_PROXIMO, SAMPLES, abertura) in (104.0, 105.0)


def test_ancora_com_dado_insuficiente():
    assert compute_anchor(AnchorHypothesis.ULTIMO_ANTES, (), 0) is None
    # abertura antes de qualquer amostra: não há "antes"
    assert compute_anchor(AnchorHypothesis.ULTIMO_ANTES, SAMPLES, -1) is None


def test_falsificacao_mata_hipotese_errada():
    """Uma janela em que as hipóteses divergem e a resolução decide."""
    # TWAP final = média das amostras dos últimos 60s = média de 100..109 = 104.5
    outcome = WindowOutcome(
        slug="teste",
        open_ts_ns=int(4.5e9),
        close_ts_ns=int(9e9),
        samples=SAMPLES,
        resolved_up=True,  # 104.5 >= 104 (último antes) → Up
    )
    scores = evaluate_hypotheses([outcome])
    # último_antes (104) prevê Up: sobrevive
    assert scores[AnchorHypothesis.ULTIMO_ANTES].sobreviveu
    # primeiro_depois (105) preveria Down: falsificada
    assert not scores[AnchorHypothesis.PRIMEIRO_DEPOIS].sobreviveu
    assert scores[AnchorHypothesis.PRIMEIRO_DEPOIS].erros == 1


def test_relatorio_de_ancora_reporta_inconclusivo():
    """Janela óbvia: todas as hipóteses acertam, nada é separado."""
    obvio = WindowOutcome(
        slug="obvio",
        open_ts_ns=0,
        close_ts_ns=int(9e9),
        samples=SAMPLES,
        resolved_up=True,  # 104.5 >= 100, qualquer hipótese acerta
    )
    relatorio = report_anchor_validation(evaluate_hypotheses([obvio]))
    assert "INCONCLUSIVO" in relatorio["veredito"]


def test_relatorio_identifica_ancora_unica():
    outcome = WindowOutcome(
        slug="apertado",
        open_ts_ns=int(4.5e9),
        close_ts_ns=int(9e9),
        samples=SAMPLES,
        resolved_up=True,
    )
    relatorio = report_anchor_validation(evaluate_hypotheses([outcome]))
    # ultimo_antes e interpolado sobrevivem aqui; o relatório precisa dizer
    # que está inconclusivo em vez de escolher um por conta própria.
    assert "INCONCLUSIVO" in relatorio["veredito"] or "identificada" in relatorio["veredito"]
    assert relatorio["por_hipotese"]["primeiro_depois"]["sobreviveu"] is False
