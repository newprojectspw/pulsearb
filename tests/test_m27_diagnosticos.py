"""M2.7 — os diagnósticos que faltavam para decidir.

Três perguntas do relatório real ficaram sem resposta porque o dado para
respondê-las não era gravado ou não era separado. Cada teste trava uma.
"""

from __future__ import annotations

from pulsearb.analysis.integrity import DERIVA_SUSPEITA_MS, MonitorDeRelogio
from pulsearb.analysis.measurements import medir_mudanca_de_tick
from pulsearb.backtest.runner import BacktestConfig
from pulsearb.recorder.__main__ import (
    _forma_dos_rewards,
    _taxa_diaria_de_reward,
)

HORA_NS = 3_600 * 10**9
BASE_NS = 1_787_000_000 * 10**9


# ─────────────────── tarefa 2: três causas para o mesmo `daily_rate: None`


def test_mercado_sem_lista_de_rewards():
    """Não participa do programa: a lista nem existe."""
    forma = _forma_dos_rewards({"rewardsMinSize": 50})
    assert forma["chave_da_lista"] is None
    assert forma["n_entradas"] == 0
    assert _taxa_diaria_de_reward({"rewardsMinSize": 50}) is None


def test_chave_alternativa_do_sdk_e_aceita_e_nomeada():
    """A hipótese que a gravação de 8h torna plausível.

    199 janelas vieram com `rewardsMinSize` e `rewardsMaxSpread` PRESENTES e
    taxa diária ausente — estranho para mercado que não participa, e a
    assinatura exata de leitor procurando a chave errada. Foi assim que o
    `price_change` custou um marco (API_NOTES §6.1b).
    """
    gamma = {"rewards_config": [{"rewards_daily_rate": 12.5}]}

    assert _taxa_diaria_de_reward(gamma) == 12.5
    forma = _forma_dos_rewards(gamma)
    assert forma["chave_da_lista"] == "rewards_config"
    assert "rewards_daily_rate" in forma["chaves_das_entradas"]


def test_vigencia_do_programa_fica_gravada():
    """`start_date`/`end_date` decidem entre "não participa" e "expirou".

    Eles nunca chegavam ao disco: o recorder gravava três campos derivados e
    descartava o `raw_gamma`. Por isso a pergunta não teve resposta.
    """
    gamma = {
        "clobRewards": [
            {
                "rewardsDailyRate": 5.0,
                "start_date": "2025-01-01",
                "end_date": "2025-02-01",
            }
        ]
    }
    forma = _forma_dos_rewards(gamma)

    assert forma["entradas"][0]["end_date"] == "2025-02-01"
    assert "start_date" in forma["chaves_das_entradas"]


def test_soma_as_fontes_de_reward():
    """Nativa + patrocinada = total_daily_rate (API_NOTES §12.8)."""
    gamma = {"clobRewards": [{"rewardsDailyRate": 10}, {"rewardsDailyRate": 5}]}
    assert _taxa_diaria_de_reward(gamma) == 15.0


def test_entradas_cruas_tem_teto():
    """Gravação de 72h não pode carregar array sem limite por janela."""
    gamma = {"clobRewards": [{"rewardsDailyRate": i} for i in range(50)]}
    assert len(_forma_dos_rewards(gamma)["entradas"]) <= 4
    assert _forma_dos_rewards(gamma)["n_entradas"] == 50


# ────────────────────── tarefa 4.1: seconds_left negativo


def _snapshot(restante, tick):
    return {
        "janelas": [
            {
                "slug": "btc-updown-5m-x",
                "tick_size": tick,
                "_seconds_left": restante,
                "best_ask": 0.5,
            }
        ]
    }


def test_afinamento_apos_o_fechamento_sai_da_estatistica_de_dentro():
    """`min: -2,1586` no relatório real.

    Não é erro de fronteira: o recorder segue assinando a janela depois do
    `endDate` porque a resolução ainda não chegou (carência de 145,9s de p50
    no TWAP). Misturar essas observações com as de dentro da janela sujava a
    pergunta "o tick afina perto do fim?", que só faz sentido dentro dela.
    """
    # a sequência precisa produzir DOIS afinamentos (0,01 -> 0,001): um
    # dentro da janela e um depois do fechamento
    saida = medir_mudanca_de_tick(
        [
            _snapshot(120.0, 0.01),
            _snapshot(40.0, 0.001),    # afinamento dentro da janela
            _snapshot(10.0, 0.01),     # engrossou de novo
            _snapshot(-2.16, 0.001),   # afinamento DEPOIS do fechamento
        ]
    )

    dentro = saida["seconds_left_no_afinamento"]
    fora = saida["afinamentos_apos_o_fechamento"]

    assert dentro["n"] >= 1
    assert dentro["min"] >= 0, "observação de depois do fim vazou para dentro"
    assert fora["n"] == 1
    assert fora["segundos_apos_o_fim"]["max"] == 2.16


# ────────────────────── tarefa 4.2: deriva de relógio × artefato de lacuna


def _alimentar(monitor, *, horas, atraso_por_hora, pico_ms=None):
    for h in range(horas):
        for i in range(200):
            chegada = BASE_NS + h * HORA_NS + i * 10**9
            atraso = pico_ms if (pico_ms and i == 0) else atraso_por_hora(h)
            monitor.observar(chegada / 1e6 - atraso, chegada)


def test_pico_isolado_com_mediana_firme_e_artefato_de_lacuna():
    """p99 de 2.535 ms com p50 de 5 ms: quando as mensagens voltam depois de
    uma lacuna, o carimbo do servidor já é velho. Não é o relógio."""
    monitor = MonitorDeRelogio()
    _alimentar(monitor, horas=3, atraso_por_hora=lambda _h: 5.0, pico_ms=2535.0)

    resumo = monitor.resumo()
    assert resumo["p50_ms"] == 5.0
    assert "MEDIANA ESTAVEL" in resumo["deriva"]["veredito"]
    assert "artefato" in resumo["deriva"]["veredito"]


def test_mediana_subindo_ao_longo_da_gravacao_e_deriva_de_relogio():
    """A causa oposta, que exige conserto no NTP e não no feed."""
    monitor = MonitorDeRelogio()
    _alimentar(
        monitor,
        horas=3,
        atraso_por_hora=lambda h: 5.0 + h * (DERIVA_SUSPEITA_MS * 10),
    )

    veredito = monitor.resumo()["deriva"]["veredito"]
    assert "DERIVA" in veredito
    assert "NTP" in veredito


def test_uma_hora_so_nao_permite_separar():
    """Sem duas horas não há tendência para ler — e afirmar seria inventar."""
    monitor = MonitorDeRelogio()
    _alimentar(monitor, horas=1, atraso_por_hora=lambda _h: 5.0)

    assert "sem horas suficientes" in monitor.resumo()["deriva"]["veredito"]


# ────────────────────── tarefa 3: entrada múltipla


def test_default_segue_uma_entrada_por_janela():
    """Mudar o default exige número, e o número ainda não existe."""
    assert BacktestConfig().max_entradas_por_janela == 1


def test_espacamento_minimo_e_padrao_nao_zero():
    """Ticks consecutivos com sinal são a MESMA oportunidade vista de novo.
    Sem espaçamento, o PnL somaria a mesma aposta repetida como se fossem
    apostas independentes."""
    assert BacktestConfig().intervalo_min_entre_entradas_s > 0


# ─────────── M2.8: a serie da ancora pode ficar rala com o feed sadio


def test_descarte_da_serie_e18_e_contado_por_causa(tmp_path):
    """O achado da hora de teste do M2.7: ZERO silêncio do RTDS e ainda
    assim 12 janelas com "abertura em lacuna".

    Não é contradição — são duas séries. `streams` (float) aceita qualquer
    tick; `streams_e18`, que é a que a âncora usa, recusa o tick sem valor
    exato e o tick sem carimbo numérico. Recusar está certo; recusar em
    silêncio é que escondia a causa.
    """
    import gzip

    import orjson

    from pulsearb.backtest.__main__ import RecordingIndex
    from pulsearb.replay.reader import RecordingReader

    def _rtds(ts_s, payload):
        ns = int(ts_s * 10**9)
        return {
            "ts_mono_ns": ns,
            "ts_wall_ns": ns,
            "fonte": "rtds",
            "payload": {"topic": "crypto_prices_twap_sixty", "payload": payload},
        }

    base = 1_787_000_000
    linhas = [
        # bom: full_accuracy_value + timestamp numérico
        _rtds(base, {
            "symbol": "btc/usd",
            "timestamp": base * 1000,
            "full_accuracy_value": str(60_000 * 10**18),
        }),
        # ruim: sem full_accuracy_value, `value` FLOAT
        _rtds(base + 1, {
            "symbol": "btc/usd",
            "timestamp": (base + 1) * 1000,
            "value": 60_001.5,
        }),
        # ruim: valor exato, mas timestamp que não é número
        _rtds(base + 2, {
            "symbol": "btc/usd",
            "timestamp": "nao-numerico",
            "full_accuracy_value": str(60_002 * 10**18),
        }),
    ]
    caminho = tmp_path / "rec.jsonl.gz"
    with gzip.open(caminho, "wb") as handle:
        for linha in linhas:
            handle.write(orjson.dumps(linha) + b"\n")

    index = RecordingIndex(RecordingReader(caminho))
    index.build()
    bloco = index.stream_de_ancora()

    assert bloco["ticks_twap_vistos"] == 3
    # só o primeiro virou ponto utilizável pela âncora
    assert bloco["pontos_na_serie_e18"] == {"btc": 1}
    assert bloco["descartados"]["sem_valor_exato"] == 1
    assert bloco["descartados"]["sem_carimbo_do_servidor"] == 1
    assert bloco["fracao_descartada"] > 0.6


def test_serie_densa_nao_descarta_nada(tmp_path):
    """O caso são: nenhum descarte, e a fração fica em zero."""
    import gzip

    import orjson

    from pulsearb.backtest.__main__ import RecordingIndex
    from pulsearb.replay.reader import RecordingReader

    base = 1_787_000_000
    caminho = tmp_path / "rec.jsonl.gz"
    with gzip.open(caminho, "wb") as handle:
        for i in range(10):
            ns = int((base + i) * 10**9)
            handle.write(
                orjson.dumps(
                    {
                        "ts_mono_ns": ns,
                        "ts_wall_ns": ns,
                        "fonte": "rtds",
                        "payload": {
                            "topic": "crypto_prices_twap_sixty",
                            "payload": {
                                "symbol": "btc/usd",
                                "timestamp": (base + i) * 1000,
                                "full_accuracy_value": str((60_000 + i) * 10**18),
                            },
                        },
                    }
                )
                + b"\n"
            )

    index = RecordingIndex(RecordingReader(caminho))
    index.build()
    bloco = index.stream_de_ancora()

    assert bloco["descartados_total"] == 0
    assert bloco["fracao_descartada"] == 0.0
    assert bloco["pontos_na_serie_e18"]["btc"] == 10


def test_rebate_vs_markout_encontra_a_tabela_de_verdade():
    """M2.8: eu tinha errado a chave, e o erro saía como `null`.

    `_rebate_vs_markout` procurava o recorte "geral"; o produtor grava
    "total". Um `.get` que erra a chave devolve `None` com exatamente a mesma
    cara de "não há dado" — e era a conta que eu havia apontado como a única
    confiável do bloco, porque não depende da fórmula de rewards não
    verificada.

    O teste amarra produtor e consumidor pela MESMA constante, que é o que
    impede os dois de divergirem de novo.
    """
    from pulsearb.analysis.measurements import RECORTE_GERAL, conta_do_maker

    saida = conta_do_maker(
        rewards={},
        markout={
            "markout_centavos_por_share": {
                RECORTE_GERAL: {"5s": {"media": -0.59, "n": 9153}}
            }
        },
        fee_rebate_rate=0.2,
        fee_rate=0.07,
        fee_exponent=1.0,
    )
    bloco = saida["rebate_vs_markout"]

    assert bloco["markout_centavos_por_share"] == -0.59
    assert bloco["execucoes_medidas"] == 9153
    # rebate 0,35 c/share contra custo 0,59 → a rota maker não se paga
    assert bloco["rebate_centavos_por_share"] == 0.35
    assert bloco["saldo_centavos_por_share"] == round(0.35 - 0.59, 4)
    assert bloco["saldo_centavos_por_share"] < 0


def test_markout_ausente_continua_dizendo_ausente():
    """A defesa contra o remédio: sem tabela, `None` continua sendo `None` —
    e não um zero que pareceria saldo neutro."""
    from pulsearb.analysis.measurements import conta_do_maker

    bloco = conta_do_maker(
        rewards={},
        markout={},
        fee_rebate_rate=0.2,
        fee_rate=0.07,
        fee_exponent=1.0,
    )["rebate_vs_markout"]

    assert bloco["markout_centavos_por_share"] is None
    assert bloco["saldo_centavos_por_share"] is None
