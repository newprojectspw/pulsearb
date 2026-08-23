"""M2.6 — o backtest ignorava a âncora que ele mesmo tinha verificado.

O relatório da primeira rodada real com PnL trazia, no mesmo JSON:

    ancora.usada_no_backtest: "ultimo_antes"        (taxa_acerto 0,9020)
    ancora.varredura_tau...regiao_viavel_100pct: [[-11, 10]]

Ou seja: a varredura confirmava a âncora com 100% sobre 92 janelas — segunda
confirmação independente — e o simulador operava com uma hipótese que erra
~10% das janelas. Todo o PnL saiu de resoluções parcialmente erradas.

Os testes daqui travam as quatro correções: usar a âncora verificada, calar
as hipóteses, fazer o veredito refletir a varredura, e GRITAR quando τ=0
deixar de explicar as resoluções.
"""

from __future__ import annotations

import pytest

from pulsearb.analysis.anchor_sweep import (
    E18,
    MINIMO_JANELAS_VEREDITO,
    StreamE18,
    ancora_verificada,
    valor_final,
    veredito_da_ancora,
)
from pulsearb.backtest.runner import BacktestConfig

BASE_MS = 1_787_000_000_000


# ────────────────────────────────────── a âncora verificada, como fonte


def test_ancora_e_o_valor_do_stream_na_abertura():
    """API_NOTES §13.8: o valor no instante da abertura, sem deslocamento."""
    valores = [(BASE_MS + s * 1000, (60_000 + s) * E18) for s in range(100)]
    serie = StreamE18(valores)

    abertura = BASE_MS + 50_000
    assert ancora_verificada(serie, abertura) == (60_000 + 50) * E18
    # e o final é o MESMO stream, no fechamento — nenhuma média recalculada
    assert valor_final(serie, BASE_MS + 80_000) == (60_000 + 80) * E18


def test_lacuna_na_abertura_nao_vira_ancora_velha():
    """O caso do BUG 3: 14 minutos sem RTDS.

    Devolver o último valor conhecido seria inventar âncora — a janela sairia
    com PnL calculado contra um preço que ninguém observou naquele instante.
    `None` é o que faz o runner pular a janela.
    """
    valores = [(BASE_MS + s * 1000, 60_000 * E18) for s in range(10)]
    serie = StreamE18(valores)

    # 14 minutos depois do último tick: lacuna
    assert ancora_verificada(serie, BASE_MS + 14 * 60 * 1000) is None
    # logo depois do último tick: ainda vale
    assert ancora_verificada(serie, BASE_MS + 9_500) == 60_000 * E18


# ─────────────────────────────────────────────── o veredito e o alarme


def _varredura(consistencia_tau0, regiao, elegiveis):
    return {
        "janelas_elegiveis": elegiveis,
        "final_stream_no_fechamento": {
            "curva": {"0": consistencia_tau0},
            "regiao_viavel_100pct": regiao,
        },
    }


def test_veredito_confirma_quando_tau_zero_explica_tudo():
    saida = veredito_da_ancora(_varredura(1.0, [[-1, 2]], 92))

    assert saida["confirmada"] is True
    assert saida["alerta"] is None
    assert "CONFIRMADA" in saida["veredito"]
    assert "92" in saida["veredito"]


def test_veredito_nao_contradiz_a_varredura():
    """O BUG 1.3 em forma de teste.

    O texto antigo dizia "NENHUMA hipótese sobreviveu" no mesmo relatório em
    que a varredura marcava 100%. Dois vereditos contraditórios sobre a mesma
    pergunta, e o leitor escolhendo em qual acreditar.
    """
    saida = veredito_da_ancora(_varredura(1.0, [[0, 0]], 50))
    assert "NENHUMA" not in saida["veredito"]
    assert "sobreviveu" not in saida["veredito"]


def test_sem_amostra_nao_e_alarme():
    """Poucas janelas: a âncora não é confirmada nem desmentida.

    Sem este ramo, toda gravação curta dispararia o alarme de mudança de
    regra — e um alarme que toca à toa deixa de ser lido.
    """
    saida = veredito_da_ancora(_varredura(0.5, [], MINIMO_JANELAS_VEREDITO - 1))

    assert saida["confirmada"] is None
    assert saida["alerta"] is None
    assert saida["veredito"].startswith("SEM AMOSTRA")


def test_tau_zero_falhando_com_outra_regiao_e_deslocamento():
    """Há região de 100%, mas não em τ=0: a âncora se moveu no tempo."""
    saida = veredito_da_ancora(_varredura(0.7, [[30, 35]], 90))

    assert saida["confirmada"] is False
    assert saida["alerta"] is not None
    assert "deslocado" in saida["alerta"]
    assert "[[30, 35]]" in saida["alerta"] or "30" in saida["alerta"]


def test_nenhum_tau_explicando_e_o_alarme_mais_grave():
    saida = veredito_da_ancora(_varredura(0.6, [], 90))

    assert saida["confirmada"] is False
    assert "nenhum tau" in saida["alerta"]
    assert "NAO opere" in saida["alerta"]


@pytest.mark.parametrize("consistencia", [None, 0.0, 0.5, 0.79, 0.97])
def test_abaixo_do_orcamento_dispara(consistencia):
    """Mudança de regra DERRUBA a consistência — é isso que o alarme pega.

    HISTÓRICO, e a razão de este teste ter mudado: ele nasceu no M2.6
    exigindo 1.0, com o argumento de que "99% não é 100%, e o critério não
    afrouxa agora que a resposta é conveniente". A desconfiança era certa; o
    limiar, não. Ele contradizia `VEREDITO_M2` §2b, escrito ANTES de qualquer
    varredura existir:

        "98%, não 100%: (...) Exigir 100% deixaria uma única janela suja
         vetar a âncora certa. 2 falhas em 100 é o orçamento para esse lixo
         residual."

    Em 2026-08-23 o cenário previsto aconteceu, com número: 152 janelas
    elegíveis sobre 5h limpas, τ=0 em 0,9934, UMA discordante
    (`btc-updown-5m-1787354400`) errando por 0,162 USD em 78.640 —

        2,06 ppm  ·  40 ms de movimento do TWAP  ·  3,75% de um intervalo
        de amostragem  ·  97x mais apertada que o limiar de "janela
        apertada" do próprio projeto

    — e τ=0 continuou sendo o argmax. O relatório mandou NÃO OPERAR sobre
    isso. O limiar foi alinhado ao documento, não afrouxado por conveniência:
    0,79 é a marca das hipóteses NOMEADAS erradas, e continua disparando.
    """
    saida = veredito_da_ancora(_varredura(consistencia, [], 90))
    assert saida["confirmada"] is False
    assert saida["alerta"] is not None
    assert "NAO opere" in saida["alerta"]


@pytest.mark.parametrize("consistencia", [0.98, 0.9934, 0.999])
def test_dentro_do_orcamento_confirma_com_lixo_residual(consistencia):
    """O orçamento de `VEREDITO_M2` §2b, agora no código.

    Não é "o alarme foi desligado": é a faixa que o documento reservou para
    lacuna de stream fina e empate mal-carimbado. Quem lê tem
    `discordantes_em_tau_verificado` para conferir o número de cada falha.
    """
    saida = veredito_da_ancora(_varredura(consistencia, [], 152))

    assert saida["confirmada"] is True
    assert saida["alerta"] is None
    assert "LIXO RESIDUAL" in saida["veredito"]
    assert "NAO opere" not in saida["veredito"]


def test_amostra_magra_confirma_mas_avisa_que_o_orcamento_nao_separa():
    """§2b: "com 26 janelas (...) o intervalo de confiança é largo demais"."""
    saida = veredito_da_ancora(_varredura(0.98, [], 50))

    assert saida["confirmada"] is True
    assert "RESSALVA" in saida["veredito"]


# ──────────────────────────────────────── BUG 2: a faixa de tempo restante


def test_faixa_sem_restricao_aceita_tudo():
    cfg = BacktestConfig()
    for restante in (0.5, 60.0, 240.0, 3600.0):
        assert cfg.na_faixa(restante) is True


def test_faixa_com_teto_recusa_o_comeco_da_janela():
    """O teto é o que tira o gatilho do bucket >240s, onde a calibração
    medida em 4h reais erra 24 pontos de probabilidade."""
    cfg = BacktestConfig(tempo_restante_max_s=240.0)

    assert cfg.na_faixa(241.0) is False
    assert cfg.na_faixa(240.0) is True
    assert cfg.na_faixa(30.0) is True


def test_faixa_com_piso_recusa_o_fim_da_janela():
    cfg = BacktestConfig(tempo_restante_min_s=30.0)

    assert cfg.na_faixa(29.9) is False
    assert cfg.na_faixa(30.0) is True


def test_faixa_com_piso_e_teto_e_intervalo_fechado():
    cfg = BacktestConfig(tempo_restante_min_s=60.0, tempo_restante_max_s=240.0)

    assert cfg.na_faixa(59.0) is False
    assert cfg.na_faixa(60.0) is True
    assert cfg.na_faixa(240.0) is True
    assert cfg.na_faixa(241.0) is False


# ══════════════════════════════ o caminho completo, sobre gravação


def _rodar(tmp_path, monkeypatch, *, argv_extra=(), n_janelas=26):
    import json

    from tests.synthetic import gerar_gravacao

    from pulsearb.backtest.__main__ import main

    grav = tmp_path / "grav"
    grav.mkdir(exist_ok=True)
    gerar_gravacao(grav / "pulsearb-20260820-1000.jsonl.gz", n_janelas=n_janelas)
    monkeypatch.setenv("PULSEARB_BACKTEST_OUTPUT_ROOT", str(tmp_path))
    codigo = main([str(grav), "--json", "rel.json", *argv_extra])
    return codigo, json.loads((tmp_path / "rel.json").read_text())


def test_backtest_usa_a_ancora_verificada_e_confirma(tmp_path, monkeypatch, capsys):
    codigo, rel = _rodar(tmp_path, monkeypatch)
    capsys.readouterr()

    assert codigo == 0
    assert rel["ancora"]["usada_no_backtest"] == "stream_twap_sixty_na_abertura"
    assert rel["ancora"]["veredito_da_varredura"]["confirmada"] is True
    assert rel["ancora"]["veredito"].startswith("CONFIRMADA")
    # as hipóteses continuam reportadas — como referência, não como fonte
    assert "por_hipotese" in rel["ancora"]
    assert "veredito_das_hipoteses_historico" in rel["ancora"]


def test_gerador_sintetico_obedece_a_ancora_verificada(tmp_path, monkeypatch, capsys):
    """A fixture codificava a regra REFUTADA até o M2.6.

    Ela resolvia a janela recalculando uma média de 60s — exatamente a
    família `final_media_60s` que o M2.5 mediu em 96,5% contra 100% da
    leitura direta. O alarme novo do backtest denunciou isso na primeira
    rodada: τ=0 explicava só 89% das janelas sintéticas.

    Um gerador que contradiz o fato verificado transforma todo teste de ponta
    a ponta num teste da regra errada — por isso isto é um teste, e não um
    detalhe do gerador.
    """
    _, rel = _rodar(tmp_path, monkeypatch)
    capsys.readouterr()

    varredura = rel["ancora"]["varredura_tau"]["final_stream_no_fechamento"]
    assert varredura["curva"]["0"] == 1.0


def test_oportunidade_por_bucket_denuncia_onde_o_gatilho_nao_chega(
    tmp_path, monkeypatch, capsys
):
    """O BUG 2, medido em vez de suposto.

    46 de 48 trades caíram em `>240s`, o bucket com 24pp de erro de
    calibração. A leitura tentadora é "o sinal só existe no começo". A
    medição diz outra coisa: o bucket calibrado tem MAIS instantes com sinal
    e ZERO trades — a v1 entra uma vez por janela e varre da abertura para o
    fechamento, então ela opera onde chega primeiro, não onde é melhor.
    """
    _, rel = _rodar(tmp_path, monkeypatch)
    capsys.readouterr()

    oportunidades = rel["backtest"]["oportunidades_por_bucket"]
    assert oportunidades, "sem oportunidades não há o que diagnosticar"
    calibrado = oportunidades.get("240-120s") or {}
    comeco = oportunidades.get(">240s") or {}

    # o sinal EXISTE na faixa calibrada...
    assert calibrado.get("instantes_com_sinal", 0) > 0
    # ...e ainda assim nenhum trade cai lá, sem restrição de faixa
    assert calibrado.get("trades", 0) == 0
    assert comeco.get("trades", 0) > 0


def test_restricao_de_faixa_move_os_trades_para_o_bucket_calibrado(
    tmp_path, monkeypatch, capsys
):
    _, rel = _rodar(
        tmp_path, monkeypatch, argv_extra=("--tempo-restante-max", "240")
    )
    capsys.readouterr()

    buckets = {k: v["n"] for k, v in rel["backtest"]["por_bucket_tempo"].items()}
    assert ">240s" not in buckets, f"a faixa não foi respeitada: {buckets}"
    assert buckets, "a restrição não pode zerar a operação nesta amostra"
    assert rel["backtest"]["faixa_de_tempo_restante"]["aplicada"] is True


def test_relatorio_traz_as_duas_rodadas_lado_a_lado(tmp_path, monkeypatch, capsys):
    """Reportar só a restrita esconderia o custo da restrição; só a
    irrestrita foi o que produziu o PnL sobre o bucket errado."""
    _, rel = _rodar(tmp_path, monkeypatch)
    capsys.readouterr()

    comparacao = rel["faixa_de_tempo"]["comparacao"]
    assert set(comparacao) == {"irrestrito", "restrito"}
    for lado in comparacao.values():
        assert "resumo" in lado
        assert "por_bucket_tempo" in lado
    # a restrita não opera no bucket descalibrado
    assert ">240s" not in comparacao["restrito"]["por_bucket_tempo"]


def test_alarme_de_ancora_sai_com_codigo_proprio(tmp_path, monkeypatch, capsys):
    """Mudança de regra da plataforma não pode sair com código 0.

    Um laço de shell processando 24 fatias de hora seguiria acumulando
    relatórios sobre uma âncora morta, e o erro só apareceria no fim — se
    alguém lesse as 3.000 linhas do JSON.
    """
    from pulsearb.backtest import __main__ as mod

    def fingir_veredito(_varredura, **_kwargs):
        return {
            "veredito": "MUDANCA DE REGRA: teste",
            "alerta": "MUDANCA DE REGRA: teste",
            "confirmada": False,
            "tau_verificado_s": 0,
            "consistencia_do_tau_verificado": 0.5,
            "regiao_viavel_100pct": [],
            "janelas_elegiveis": 90,
        }

    monkeypatch.setattr(mod, "veredito_da_ancora", fingir_veredito)
    codigo, rel = _rodar(tmp_path, monkeypatch)
    saida = capsys.readouterr()

    assert codigo == mod.CODIGO_ANCORA_INVALIDA
    assert "ALERTA" in saida.err
    # o relatório é gravado do mesmo jeito: o dado serve para diagnosticar
    assert rel["ancora"]["veredito_da_varredura"]["alerta"] is not None
