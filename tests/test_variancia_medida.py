"""O modelo com variância MEDIDA — §2d-ter do VEREDITO_M2.

A curva do btc aqui não é sintética: são os números que a medição de 24 h de
2026-08-24 produziu sobre 651.995 ticks. Usá-los no teste é o que faz este
arquivo responder a pergunta que importa — *o modelo para de saturar?* — em
vez de responder se a aritmética fecha.
"""

from __future__ import annotations

import math

import pytest

from pulsearb.engine.decisao import JOGO_TWAP, estimar_prob_up
from pulsearb.engine.twap import (
    RealizedVol,
    TwapTracker,
    prob_up_twap,
    prob_up_twap_medido,
)
from pulsearb.engine.variancia import (
    CurvaDeVariancia,
    CurvasPorAtivo,
    curvas_do_relatorio,
)

#: V(t) do btc, medida em 24 h de 2026-08-24 (relatorios/VARIANCIA_24AGO.json).
PONTOS_BTC = (
    (1.0, 2.352126724427579e-10),
    (2.0, 6.766494738216218e-10),
    (5.0, 3.878150567744716e-09),
    (10.0, 1.483584639264751e-08),
    (30.0, 1.1616900502179133e-07),
    (60.0, 3.690323096354988e-07),
    (120.0, 8.898465093595007e-07),
    (180.0, 1.3675723240230315e-06),
    (240.0, 1.844065257706601e-06),
    (300.0, 2.3559952897436866e-06),
    (600.0, 5.094367183828361e-06),
)


def curva_btc() -> CurvaDeVariancia:
    return CurvaDeVariancia(asset="btc", pontos=PONTOS_BTC, origem="VARIANCIA_24AGO")


# ------------------------------------------------------------- interpolação
def test_curva_devolve_o_medido_nos_pontos_medidos():
    curva = curva_btc()
    for h, v in PONTOS_BTC:
        assert curva.variancia(h) == pytest.approx(v, rel=1e-12)


def test_interpolacao_e_em_log_log_e_fica_entre_os_vizinhos():
    """Entre 60 e 120 s está o joelho — e a banda operada vive nele.

    Em escala linear, o ponto médio entre V(60) e V(120) daria 6,29e-7. A
    curva é uma lei de potência ali, e a interpolação em log-log dá menos —
    o que importa porque 90 s cai dentro da banda que o veredito opera.
    """
    curva = curva_btc()
    meio = curva.variancia(90.0)
    assert 3.690323096354988e-07 < meio < 8.898465093595007e-07
    media_linear = (3.690323096354988e-07 + 8.898465093595007e-07) / 2
    assert meio < media_linear


def test_acima_do_medido_extrapola_LINEAR_em_t():
    """Propriedade 2 da §2d-ter: no regime longo V(t)/t é constante.

    Uma janela de 4 h pergunta por 14.400 s, muito além dos 600 s medidos.
    Extrapolar pela inclinação do último trecho (1,11 em log-log) projetaria
    ~1,9× a mais lá na ponta; a reta é a forma que a própria medição diz valer
    no regime longo.
    """
    curva = curva_btc()
    v600 = curva.variancia(600.0)
    assert curva.variancia(1200.0) == pytest.approx(2 * v600, rel=1e-9)
    assert curva.variancia(14_400.0) == pytest.approx(24 * v600, rel=1e-9)


def test_abaixo_do_medido_segue_a_inclinacao_do_primeiro_trecho():
    """Sublinear no curto — assumir linear ali contradiria a propriedade 3."""
    curva = curva_btc()
    meio_segundo = curva.variancia(0.5)
    assert 0 < meio_segundo < PONTOS_BTC[0][1]
    # Superlinear: cair pela metade no tempo derruba mais que pela metade.
    assert meio_segundo < PONTOS_BTC[0][1] / 2


def test_horizonte_zero_nao_tem_variancia():
    assert curva_btc().variancia(0.0) == 0.0


def test_curva_recusa_ponto_nao_positivo_e_curva_curta_demais():
    with pytest.raises(ValueError):
        CurvaDeVariancia(asset="btc", pontos=((1.0, 1e-10),))
    with pytest.raises(ValueError):
        CurvaDeVariancia(asset="btc", pontos=((1.0, 0.0), (2.0, 1e-10)))


# ------------------------------------------------------- leitura do relatório
def _relatorio(avaliavel: bool = True) -> dict:
    return {
        "por_ativo": {
            "btc": {
                "veredito": {"avaliavel": avaliavel},
                "horizontes": [
                    {"horizonte_s": h, "variancia": v, "suficiente": True}
                    for h, v in PONTOS_BTC
                ],
            }
        }
    }


def test_relatorio_vira_curva():
    curvas = curvas_do_relatorio(_relatorio(), origem="X")
    assert len(curvas) == 1
    assert curvas.para("btc").variancia(240.0) == pytest.approx(1.844065257706601e-06)
    assert curvas.para("eth") is None


def test_ativo_sem_veredito_avaliavel_fica_de_fora():
    """Curva pela metade extrapolada para o resto é pior que curva nenhuma.

    Pior porque não se anunciaria: o relatório sairia com probabilidades de
    aparência normal, calculadas sobre uma faixa que ninguém mediu.
    """
    assert len(curvas_do_relatorio(_relatorio(avaliavel=False), origem="X")) == 0


def test_horizonte_insuficiente_nao_entra_na_curva():
    bruto = _relatorio()
    bruto["por_ativo"]["btc"]["horizontes"][0]["suficiente"] = False
    curva = curvas_do_relatorio(bruto, origem="X").para("btc")
    assert curva.pontos[0][0] == 2.0


# ------------------------------------------------------------------- o modelo
def test_modelo_medido_bate_com_a_normal_da_variancia_medida():
    curva = curva_btc()
    spot, t = 118_000.0, 240.0
    ancora = spot * 1.0005
    est = prob_up_twap_medido(ancora=ancora, spot=spot, seconds_left=t, curva=curva)

    desvio = math.sqrt(curva.variancia(t)) * spot
    z = (ancora - spot) / desvio
    esperado = 1.0 - 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
    assert est.prob_up == pytest.approx(esperado, rel=1e-12)


def test_o_desvio_medido_e_6_vezes_o_derivado_na_banda_operada():
    """O número que a §2d-ter existe para produzir.

    Mesmo instante, mesmos dados, só muda de onde vem a variância. Na banda
    operada o modelo derivado usa **2,2 bps** de desvio onde a medição diz
    **13,6 bps** — 6,3 vezes menos. É por isso que o z-score inflava por 6 e
    a probabilidade saturava.
    """
    curva = curva_btc()
    t = 240.0
    sigma_suavizada = math.sqrt(PONTOS_BTC[0][1])

    desvio_medido = math.sqrt(curva.variancia(t))
    # O que o modelo derivado usa: sigma da série suavizada vezes o fator.
    from pulsearb.engine.twap import variance_factor

    desvio_derivado = sigma_suavizada * math.sqrt((t - 60.0) + variance_factor(60.0))

    assert desvio_medido / desvio_derivado == pytest.approx(6.3, abs=0.3)
    assert desvio_medido == pytest.approx(13.6e-4, rel=0.05)
    assert desvio_derivado == pytest.approx(2.2e-4, rel=0.05)


def test_o_modelo_medido_satura_MUITO_menos():
    """A consequência do desvio 6× maior, contada em previsões.

    Numa grade de −50 a +50 bps, o derivado joga quase tudo nos extremos: com
    2,2 bps de desvio, 4 bps já são quase certeza. O medido joga bem menos.

    **Ressalva registrada, para o número não ser lido como promessa:** o
    medido ainda concentra mais da metade dessa grade nos extremos, porque
    50 bps são 3,7 desvios. Isso é a grade ser larga, não o modelo ser
    confiante demais. Se a ECE cai abaixo de 0,05 é pergunta empírica que só
    a rodada de remediação responde — este teste mede o desvio, não a
    calibração.
    """
    curva = curva_btc()
    spot, t = 118_000.0, 240.0
    sigma_suavizada = math.sqrt(PONTOS_BTC[0][1])

    extremos_derivado = 0
    extremos_medido = 0
    for bps in range(-50, 51):
        ancora = spot * (1 + bps / 10_000.0)
        derivado = prob_up_twap(
            ancora=ancora, spot=spot, seconds_left=t, sigma_1s=sigma_suavizada
        ).prob_up
        medido = prob_up_twap_medido(
            ancora=ancora, spot=spot, seconds_left=t, curva=curva
        ).prob_up
        extremos_derivado += derivado < 0.05 or derivado > 0.95
        extremos_medido += medido < 0.05 or medido > 0.95

    assert extremos_derivado >= 90, extremos_derivado
    assert extremos_medido <= 60, extremos_medido
    assert extremos_derivado - extremos_medido >= 30


def test_o_travamento_continua_capturado_pela_curva():
    """A intuição do modelo original sobrevive — medida em vez de calculada.

    A 30 s do fechamento a incerteza é MUITO menor que a 600 s, e não por um
    fator de 20 (a razão dos tempos): é 36 vezes menor por segundo, porque
    metade do valor de liquidação já aconteceu. O `locked_mean_and_weight`
    tentava computar isso dos nossos pontos e não podia; a curva entrega.
    """
    curva = curva_btc()
    por_segundo_curto = curva.variancia(30.0) / 30.0
    por_segundo_longo = curva.variancia(600.0) / 600.0
    assert por_segundo_longo / por_segundo_curto > 2.0

    spot = 118_000.0
    ancora = spot * 1.0005
    perto = prob_up_twap_medido(
        ancora=ancora, spot=spot, seconds_left=30.0, curva=curva
    )
    longe = prob_up_twap_medido(
        ancora=ancora, spot=spot, seconds_left=600.0, curva=curva
    )
    # Mais perto do fim, a mesma distância da âncora é mais decisiva.
    assert perto.prob_up < longe.prob_up


def test_janela_fechada_resolve_pelo_ponto_e_empate_e_up():
    curva = curva_btc()
    comum = dict(spot=100.0, seconds_left=0.0, curva=curva)
    assert prob_up_twap_medido(ancora=100.0, **comum).prob_up == 1.0
    assert prob_up_twap_medido(ancora=100.001, **comum).prob_up == 0.0


# ---------------------------------------------------------------- o despacho
def test_estimar_prob_up_usa_a_curva_quando_ela_vem():
    curva = curva_btc()
    vol, twap = RealizedVol(), TwapTracker()
    comum = dict(
        jogo=JOGO_TWAP,
        ancora=118_050.0,
        twap=twap,
        vol=vol,
        preco_spot=118_000.0,
        seconds_left=240.0,
    )
    com = estimar_prob_up(**comum, curva=curva)
    sem = estimar_prob_up(**comum)

    esperado = prob_up_twap_medido(
        ancora=118_050.0, spot=118_000.0, seconds_left=240.0, curva=curva
    )
    assert com.prob_up == pytest.approx(esperado.prob_up)
    assert com.prob_up != sem.prob_up


def test_sem_curva_o_caminho_antigo_fica_identico():
    """`curva=None` tem de ser o comportamento de antes, sem exceção.

    É a mesma trava que a `FaixaDeOperacao` ganhou: a rodada que não pede a
    novidade não pode mudar de resposta, senão nenhuma comparação com o
    histórico vale.
    """
    vol, twap = RealizedVol(), TwapTracker()
    for ts in range(40):
        vol.update(118_000.0 + ts, int(ts * 1e9))
        twap.update(118_000.0 + ts, int(ts * 1e9))
    comum = dict(
        jogo=JOGO_TWAP,
        ancora=118_050.0,
        twap=twap,
        vol=vol,
        preco_spot=118_000.0,
        seconds_left=240.0,
    )
    assert estimar_prob_up(**comum).prob_up == estimar_prob_up(**comum, curva=None).prob_up


def test_curvas_por_ativo_devolve_none_para_ativo_nao_medido():
    curvas = CurvasPorAtivo(por_ativo={"btc": curva_btc()}, origem="X")
    assert curvas.para("btc") is not None
    assert curvas.para("doge") is None


# ------------------------------------------------ o runner: falha FECHADA
def test_janela_de_ativo_sem_curva_e_pulada_e_CONTADA():
    """Falha fechada, e barulhenta.

    A janela sai INTEIRA — inclusive da calibração, que o runner mede antes do
    portão de confiabilidade. Deixá-la entrar pelo modelo derivado envenenaria
    exatamente o número que o critério 1.3 lê, e o relatório não diria que
    metade das previsões veio de outra física.
    """
    import tempfile
    from pathlib import Path

    from tests.synthetic import gerar_gravacao

    import pulsearb.backtest.runner as runner_mod
    from pulsearb.backtest.__main__ import RecordingIndex
    from pulsearb.engine.anchor import AnchorHypothesis, compute_anchor
    from pulsearb.replay.reader import RecordingReader

    with tempfile.TemporaryDirectory() as tmp:
        diretorio = Path(tmp) / "rec"
        diretorio.mkdir()
        gerar_gravacao(diretorio / "rec.jsonl.gz", n_janelas=8)
        index = RecordingIndex(RecordingReader(diretorio))
        index.build()
        janelas = [j for j in index.janelas() if j.resolveu_up is not None]
        for janela in janelas:
            janela.ancora = compute_anchor(
                AnchorHypothesis.ULTIMO_ANTES, index.streams["btc"], janela.open_ts_ns
            )
        assert janelas and all(j.asset == "btc" for j in janelas)

        # Curvas que NÃO cobrem o btc: toda janela tem de sair.
        so_eth = CurvasPorAtivo(
            por_ativo={"eth": CurvaDeVariancia(asset="eth", pontos=PONTOS_BTC)},
            origem="teste",
        )
        cfg = runner_mod.BacktestConfig(curvas_de_variancia=so_eth)
        report = runner_mod.BacktestRunner(cfg).run(janelas, index.streams)

        assert report.janelas_sem_curva["btc"] == len(janelas)
        assert not report.trades
        # E, o que mais importa: nada entrou na calibração.
        assert report.to_dict()["janelas_sem_curva_de_variancia"] == {
            "btc": len(janelas)
        }

        # Com a curva do btc presente, as janelas voltam a ser avaliadas.
        com_btc = CurvasPorAtivo(por_ativo={"btc": curva_btc()}, origem="teste")
        report2 = runner_mod.BacktestRunner(
            runner_mod.BacktestConfig(curvas_de_variancia=com_btc)
        ).run(janelas, index.streams)
        assert not report2.janelas_sem_curva
        assert report2.janelas_avaliadas > 0
