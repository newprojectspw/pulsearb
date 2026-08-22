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


def test_cadencia_separa_serie_densa_de_serie_rala_no_tempo():
    """M2.8: 1.687 pontos numa hora e ZERO descartes — e ainda assim 12 de 28
    janelas sem âncora.

    A hipótese do descarte foi refutada pelo dado. Sobra o que este teste
    mede: o que importa para `em()` não é quantos PONTOS a série tem, é
    quantos CARIMBOS DISTINTOS e como eles se espaçam. Uma série pode ser
    densa em pontos e rala em tempo — o republicador reenvia o mesmo valor da
    Chainlink com o mesmo `timestamp`, e a TWAP on-chain atualiza no ritmo
    dela.
    """
    from pulsearb.backtest.__main__ import _cadencia_da_serie

    base = 1_787_000_000_000
    # 60 pontos, mas só 4 instantes distintos, espaçados de 30 s
    serie = [
        (base + bloco * 30_000, 60_000 * 10**18 + bloco)
        for bloco in range(4)
        for _ in range(15)
    ]
    bloco = _cadencia_da_serie(serie)

    assert bloco["pontos"] == 60
    assert bloco["carimbos_distintos"] == 4
    assert bloco["repeticoes_do_mesmo_carimbo"] == 56
    assert bloco["intervalo_s"]["max"] == 30.0
    # 30 s > IDADE_MAX_MS (10 s): toda janela que abrir num desses buracos
    # fica sem âncora, com o feed perfeitamente saudável
    assert bloco["buracos_acima_da_idade_maxima"] == 3


def test_serie_com_cadencia_boa_nao_tem_buraco():
    from pulsearb.backtest.__main__ import _cadencia_da_serie

    base = 1_787_000_000_000
    serie = [(base + i * 2_000, 60_000 * 10**18 + i) for i in range(100)]
    bloco = _cadencia_da_serie(serie)

    assert bloco["carimbos_distintos"] == 100
    assert bloco["repeticoes_do_mesmo_carimbo"] == 0
    assert bloco["intervalo_s"]["p50"] == 2.0
    assert bloco["buracos_acima_da_idade_maxima"] == 0


# ─────────── M2.9: o silêncio que dura até o FIM era invisível


def _gravacao_com_twap_que_para(tmp_path, *, para_em_s, dura_s):
    """RTDS onde o twap_sixty emudece na metade e NÃO volta.

    `crypto_prices` continua o tempo todo — é o que faz o silêncio ser do
    TÓPICO e não da conexão, e é o que o watchdog de dados não pega.
    """
    import gzip

    import orjson

    base = 1_787_000_000
    linhas = []
    for i in range(dura_s):
        ts = base + i
        ns = ts * 10**9
        if i < para_em_s:
            linhas.append(
                {
                    "ts_mono_ns": ns,
                    "ts_wall_ns": ns,
                    "fonte": "rtds",
                    "payload": {
                        "topic": "crypto_prices_twap_sixty",
                        "payload": {
                            "symbol": "btc/usd",
                            "timestamp": ts * 1000,
                            "full_accuracy_value": str((60_000 + i) * 10**18),
                        },
                    },
                }
            )
        # o outro tópico NUNCA para
        linhas.append(
            {
                "ts_mono_ns": ns + 1,
                "ts_wall_ns": ns + 1,
                "fonte": "rtds",
                "payload": {
                    "topic": "crypto_prices",
                    "payload": {
                        "symbol": "btcusdt",
                        "timestamp": ts * 1000,
                        "value": 60_000.0 + i,
                    },
                },
            }
        )
    caminho = tmp_path / "rec.jsonl.gz"
    with gzip.open(caminho, "wb") as handle:
        for linha in linhas:
            handle.write(orjson.dumps(linha) + b"\n")
    return caminho


def _indexar(caminho):
    from pulsearb.backtest.__main__ import RecordingIndex
    from pulsearb.replay.reader import RecordingReader

    index = RecordingIndex(RecordingReader(caminho))
    index.build()
    return index


def test_silencio_que_dura_ate_o_fim_da_gravacao_e_detectado(tmp_path):
    """O defeito que a hora de teste expôs.

    O detector só fechava um silêncio quando chegava o evento SEGUINTE. Se o
    tópico emudece e nunca mais volta, esse evento não existe — e o relatório
    dizia "0 silêncios" com metade da gravação sem preço-verdade.

    Um detector que enxerga todo silêncio menos o último é pior que nenhum,
    porque o último é o que mata a gravação inteira.
    """
    index = _indexar(
        _gravacao_com_twap_que_para(tmp_path, para_em_s=300, dura_s=600)
    )
    bloco = index.silencio_do_rtds()

    assert bloco["silencios"] >= 1, "o silêncio até o fim continua invisível"
    ate_o_fim = [
        s for s in bloco["silencios_so_do_topico"] if s.get("ate_o_fim_da_gravacao")
    ]
    assert ate_o_fim, "faltou marcar que o silêncio se estende até o fim"
    assert ate_o_fim[0]["asset"] == "btc"
    assert ate_o_fim[0]["duracao_s"] > 250
    # a conexão estava VIVA: o outro tópico continuou chegando
    assert ate_o_fim[0]["eventos_rtds_durante"] > 0
    assert bloco["suspeita_de_assinatura_caducada"] >= 1


def test_cobertura_denuncia_serie_que_some_na_metade(tmp_path):
    """DENSIDADE NÃO É COBERTURA.

    Na hora de teste a série tinha cadência de 1 s, zero descarte e zero
    buraco acima da idade máxima — impecável no trecho que existia — e cobria
    metade da gravação. Todos os diagnósticos anteriores olhavam só o trecho
    que existe.
    """
    index = _indexar(
        _gravacao_com_twap_que_para(tmp_path, para_em_s=300, dura_s=600)
    )
    bloco = index.stream_de_ancora()

    # o trecho que existe está impecável...
    cadencia = bloco["cadencia_por_ativo"]["btc"]
    assert cadencia["buracos_acima_da_idade_maxima"] == 0
    assert bloco["fracao_descartada"] == 0.0

    # ...e ainda assim metade da gravação não tem preço-verdade
    cobertura = bloco["cobertura_da_gravacao"]["por_ativo"]["btc"]
    assert cobertura["fracao_da_gravacao"] < 0.55
    assert cobertura["silencio_final_s"] > 250


def test_serie_completa_nao_acusa_silencio_final(tmp_path):
    """O caso são: o tópico vai até o fim e nada é acusado."""
    index = _indexar(
        _gravacao_com_twap_que_para(tmp_path, para_em_s=600, dura_s=600)
    )

    assert index.silencio_do_rtds()["silencios"] == 0
    cobertura = index.stream_de_ancora()["cobertura_da_gravacao"]["por_ativo"]["btc"]
    assert cobertura["fracao_da_gravacao"] > 0.95
    assert cobertura["silencio_final_s"] < 5


# ─────────── M2.10: o M2.9 consertou metade, e o rótulo apontava o conserto errado


def _gravacao_com_rtds_que_para(tmp_path, *, para_em_s, dura_s, ativos=("btc", "eth")):
    """RTDS onde a CONEXÃO INTEIRA emudece e não volta.

    Diferente de `_gravacao_com_twap_que_para`: aqui nenhum tópico
    sobrevive. É a forma da gravação de 2026-08-22, onde os 8 ativos
    emudeceram dentro de 1 s um do outro — e o relatório acusou 7
    assinaturas caducadas.

    O `retardatario_s` reproduz o detalhe que enganava o detector: um
    evento avulso logo depois do último tick do twap, que fazia
    `eventos_rtds_durante` sair maior que zero para uma conexão morta.
    """
    import gzip

    import orjson

    base = 1_787_000_000
    linhas = []
    for i in range(dura_s):
        ts = base + i
        ns = ts * 10**9
        if i >= para_em_s:
            continue
        for pos, asset in enumerate(ativos):
            linhas.append(
                {
                    "ts_mono_ns": ns + pos,
                    "ts_wall_ns": ns + pos,
                    "fonte": "rtds",
                    "payload": {
                        "topic": "crypto_prices_twap_sixty",
                        "payload": {
                            "symbol": f"{asset}/usd",
                            "timestamp": ts * 1000,
                            "full_accuracy_value": str((60_000 + i) * 10**18),
                        },
                    },
                }
            )
    # O RETARDATÁRIO: um único evento de outro tópico logo após o último
    # tick do twap. É o que fazia `eventos_rtds_durante > 0` e produzia o
    # diagnóstico errado.
    ns_retardatario = (base + para_em_s) * 10**9 + 500
    linhas.append(
        {
            "ts_mono_ns": ns_retardatario,
            "ts_wall_ns": ns_retardatario,
            "fonte": "rtds",
            "payload": {
                "topic": "crypto_prices",
                "payload": {
                    "symbol": "btcusdt",
                    "timestamp": (base + para_em_s) * 1000,
                    "value": 60_000.0,
                },
            },
        }
    )
    # Um evento NÃO-rtds no fim, para a gravação ter duração além do
    # silêncio sem que isso venha do próprio feed que morreu.
    fim_ns = (base + dura_s) * 10**9
    linhas.append(
        {
            "ts_mono_ns": fim_ns,
            "ts_wall_ns": fim_ns,
            "fonte": "gap",
            "payload": {"fonte": "poly_ws", "tipo": "marcador", "duracao_s": 0.0},
        }
    )
    caminho = tmp_path / "rec.jsonl.gz"
    with gzip.open(caminho, "wb") as handle:
        for linha in linhas:
            handle.write(orjson.dumps(linha) + b"\n")
    return caminho


def test_conexao_que_emudece_ate_o_fim_tambem_e_detectada(tmp_path):
    """O M2.9 consertou o escopo do TÓPICO e deixou o da CONEXÃO cego.

    O flush do M2.9 percorre só `_ultimo_twap_ns`. O detector de
    `conexao_inteira` continuava fechando silêncio apenas quando chegava o
    evento seguinte — que, numa conexão que morre e não volta, nunca chega.

    É o escopo que decide o conserto: tópico mudo pede reassinatura no
    recorder, conexão muda pede keepalive/reconexão. Cego nesse escopo, o
    relatório aponta o conserto errado.
    """
    index = _indexar(
        _gravacao_com_rtds_que_para(tmp_path, para_em_s=300, dura_s=900)
    )
    bloco = index.silencio_do_rtds()

    conexao = bloco["silencios_da_conexao_inteira"]
    assert conexao, "silêncio da conexão até o fim continua invisível"
    assert conexao[0]["ate_o_fim_da_gravacao"] is True
    assert conexao[0]["duracao_s"] > 500


def test_conexao_morta_nao_vira_assinatura_caducada(tmp_path):
    """O rótulo errado da gravação de 2026-08-22.

    `eventos_rtds_durante > 0` sozinho não sustenta "a conexão estava
    viva": para um silêncio que vai até o fim, o campo conta eventos de
    QUALQUER tópico e QUALQUER ativo depois do último tick. Um único
    retardatário fazia uma conexão morta parecer viva, e o relatório
    mandava consertar a reassinatura quando o problema era a conexão.
    """
    index = _indexar(
        _gravacao_com_rtds_que_para(tmp_path, para_em_s=300, dura_s=900)
    )
    bloco = index.silencio_do_rtds()

    # o retardatário está lá, e é justamente o que enganava
    ate_o_fim = [
        s for s in bloco["silencios_so_do_topico"] if s.get("ate_o_fim_da_gravacao")
    ]
    assert ate_o_fim, "o silêncio do tópico deveria continuar sendo detectado"
    assert ate_o_fim[0]["eventos_rtds_durante"] > 0

    # ...e ainda assim NÃO é assinatura caducada: a conexão ficou muda dentro
    # do intervalo, então as duas explicações são indistinguíveis.
    assert bloco["suspeita_de_assinatura_caducada"] == 0


def test_total_s_e_uniao_e_nao_soma(tmp_path):
    """`total_s` de 14.476,91 s numa gravação de 3.600 s não é duração.

    Os silêncios são por (escopo, ativo) e se sobrepõem. Somar contava o
    mesmo intervalo uma vez por ativo — e o campo deixava de responder a
    única pergunta que alguém faz ao lê-lo: quanto tempo fiquei sem
    preço-verdade?
    """
    index = _indexar(
        _gravacao_com_rtds_que_para(
            tmp_path, para_em_s=300, dura_s=900, ativos=("btc", "eth", "sol", "xrp")
        )
    )
    bloco = index.silencio_do_rtds()

    # 4 ativos + a conexão emudecem no mesmo instante: são 5 silêncios...
    assert bloco["silencios"] >= 5
    soma = sum(float(s["duracao_s"]) for s in index._silencios)
    # ...cuja soma estoura a gravação inteira, e a união não.
    assert soma > 900, "o cenário precisa ter sobreposição para o teste valer"
    assert bloco["total_s"] <= 900
    assert bloco["total_s"] > 500
    assert bloco["total_s_por_escopo"]["topico_do_ativo"] <= 900


# ─────────── M2.10: a curva de edge pode não morder, e isso precisa aparecer


class _RelatorioFalso:
    """O mínimo que `curva_de_edge_por_threshold` lê de um BacktestReport."""

    def __init__(self, trades, pnl):
        self.trades = list(range(trades))
        self.pnl_liquido = pnl
        self.hit_rate = 0.5


def test_curva_degenerada_avisa_que_o_threshold_nao_mordeu():
    """O caso da gravação real de 2026-08-22.

    Os seis thresholds — de 0,01 a 0,12 — deram os mesmos 11 trades e o mesmo
    PnL. O modelo previa ~0,83 contra um book perto de 0,50, então a entrada
    já nascia com edge acima do teto da grade. `max()` desempatou pelo
    primeiro e o relatório publicou `melhor_threshold: 0.01` — que se lê como
    escolha quando é artefato.
    """
    from pulsearb.backtest.report import curva_de_edge_por_threshold

    curva = curva_de_edge_por_threshold(
        {t: _RelatorioFalso(11, 3.3626) for t in (0.01, 0.02, 0.03, 0.05, 0.08, 0.12)}
    )

    assert curva["threshold_mordeu"] is False
    assert curva["resultados_distintos"] == 1
    assert "nao excluiu sinal nenhum" in curva["nota"] or "nunca excluiu" in curva["nota"]


def test_curva_que_separa_resultados_e_comparacao_de_verdade():
    """O contraste: grade que morde continua sendo leitura legítima."""
    from pulsearb.backtest.report import curva_de_edge_por_threshold

    curva = curva_de_edge_por_threshold(
        {
            0.01: _RelatorioFalso(40, 17.218),
            0.02: _RelatorioFalso(39, 8.9041),
            0.12: _RelatorioFalso(37, 20.9593),
        }
    )

    assert curva["threshold_mordeu"] is True
    assert curva["resultados_distintos"] == 3
    assert curva["melhor_threshold"] == 0.12


# ─────────── M2.10 ciclo 2: a forma exata da gravação de 2026-08-22


OITO_ATIVOS = ("bnb", "btc", "doge", "eth", "hype", "sol", "xrp", "zec")


def _gravacao_forma_2026_08_22(
    tmp_path, *, para_em_s=300, dura_s=900, outro_topico_sobrevive=False
):
    """Oito ativos do twap emudecendo dentro de 1 s, e não voltando.

    Reproduz a forma medida: zec às 16:29:49,87 e doge às 16:29:50,83 —
    0,96 s de dispersão entre o primeiro e o último. `crypto_prices` para
    junto (o caso real) ou sobrevive (o contraste), conforme o parâmetro.
    """
    import gzip

    import orjson

    base = 1_787_000_000
    linhas = []
    for i in range(dura_s):
        ts = base + i
        ns = ts * 10**9
        for pos, asset in enumerate(OITO_ATIVOS):
            # o último tick de cada ativo cai espalhado dentro de ~0,9 s
            desloca_ns = pos * 120_000_000
            if i < para_em_s:
                linhas.append(
                    {
                        "ts_mono_ns": ns + desloca_ns,
                        "ts_wall_ns": ns + desloca_ns,
                        "fonte": "rtds",
                        "payload": {
                            "topic": "crypto_prices_twap_sixty",
                            "payload": {
                                "symbol": f"{asset}/usd",
                                "timestamp": ts * 1000,
                                "full_accuracy_value": str((60_000 + i) * 10**18),
                            },
                        },
                    }
                )
        if i < para_em_s or outro_topico_sobrevive:
            linhas.append(
                {
                    "ts_mono_ns": ns + 950_000_000,
                    "ts_wall_ns": ns + 950_000_000,
                    "fonte": "rtds",
                    "payload": {
                        "topic": "crypto_prices",
                        "payload": {
                            "symbol": "btcusdt",
                            "timestamp": ts * 1000,
                            "value": 60_000.0 + i,
                        },
                    },
                }
            )
    fim_ns = (base + dura_s) * 10**9
    linhas.append(
        {
            "ts_mono_ns": fim_ns,
            "ts_wall_ns": fim_ns,
            "fonte": "gap",
            "payload": {"fonte": "poly_ws", "tipo": "marcador", "duracao_s": 0.0},
        }
    )
    caminho = tmp_path / "rec.jsonl.gz"
    with gzip.open(caminho, "wb") as handle:
        for linha in linhas:
            handle.write(orjson.dumps(linha) + b"\n")
    return caminho


def test_item2_conexao_morta_zera_a_suspeita_de_assinatura(tmp_path):
    """ITEM 2. O denominador passa a ser o que sustenta a inferência.

    `eventos_rtds_durante` conta qualquer tópico e qualquer ativo — inclusive
    o próprio twap dos outros sete, que não prova conexão viva quando todos
    param juntos. O campo novo conta só evento de OUTRO tópico na mesma
    conexão, dentro do intervalo.
    """
    bloco = _indexar(_gravacao_forma_2026_08_22(tmp_path)).silencio_do_rtds()

    terminais = [
        s for s in bloco["silencios_so_do_topico"] if s.get("ate_o_fim_da_gravacao")
    ]
    assert terminais, "os silêncios terminais precisam continuar sendo detectados"

    # O contador ANTIGO enxerga muitos eventos — o twap dos outros sete
    # ativos entra na conta e infla a impressão de "conexão viva".
    antigo = max(s["eventos_rtds_durante"] for s in terminais)
    novo = max(s["eventos_de_outros_topicos_durante"] for s in terminais)
    assert antigo > novo, f"o denominador novo tem de ser mais estrito ({antigo} vs {novo})"

    # O NOVO enxerga só o retardatário: reproduz o `eventos_rtds_durante: 1`
    # do btc na gravação real. Um evento avulso NÃO é conexão viva — e é
    # exatamente por isso que a contagem sozinha nunca ia bastar.
    assert novo <= 1
    assert all(s["base_da_contagem"] for s in terminais)

    # Quem derruba a inferência é o silêncio da CONEXÃO se sobrepondo (item
    # 1): se a conexão ficou muda dentro do intervalo, as duas explicações
    # são indistinguíveis e a honesta é não escolher.
    assert bloco["silencios_da_conexao_inteira"], "faltou o silêncio de conexão"
    assert bloco["suspeita_de_assinatura_caducada"] == 0


def test_item2_conexao_viva_ainda_acusa_assinatura_caducada(tmp_path):
    """ITEM 2, o contraste: assinatura caducando de verdade continua visível.

    Sem isto o conserto seria só desligar o alarme.
    """
    bloco = _indexar(
        _gravacao_forma_2026_08_22(tmp_path, outro_topico_sobrevive=True)
    ).silencio_do_rtds()

    terminais = [
        s for s in bloco["silencios_so_do_topico"] if s.get("ate_o_fim_da_gravacao")
    ]
    assert all(s["eventos_de_outros_topicos_durante"] > 0 for s in terminais)
    assert bloco["suspeita_de_assinatura_caducada"] == len(terminais)


def test_item3_total_s_bate_com_a_uniao_e_nao_com_a_soma(tmp_path):
    """ITEM 3. Oito silêncios sobrepostos de ~600 s somam ~4.800 s."""
    index = _indexar(_gravacao_forma_2026_08_22(tmp_path))
    bloco = index.silencio_do_rtds()

    soma = sum(float(s["duracao_s"]) for s in index._silencios)
    assert soma > 3_000, "o cenário precisa ter sobreposição"
    assert bloco["total_s"] <= 900, "total_s não pode passar da gravação"
    assert 550 <= bloco["total_s"] <= 900


def test_item4_oito_ativos_em_um_segundo_viram_um_evento(tmp_path):
    """ITEM 4. Listar oito linhas empurra para a hipótese errada.

    Oito assinaturas não caducam dentro do mesmo segundo. Agrupar torna isso
    legível sem esconder as entradas individuais.
    """
    bloco = _indexar(_gravacao_forma_2026_08_22(tmp_path)).silencio_do_rtds()

    eventos = bloco["eventos_coincidentes"]
    assert len(eventos) == 1, f"esperado um evento coincidente, veio {len(eventos)}"
    evento = eventos[0]
    assert evento["quantos_ativos"] == 8
    assert set(evento["ativos"]) == set(OITO_ATIVOS)
    assert evento["dispersao_do_inicio_s"] < 1.5
    assert evento["ate_o_fim_da_gravacao"] is True
    # as entradas individuais continuam acessíveis
    assert len(bloco["silencios_so_do_topico"]) >= 8


def test_item4_silencios_distantes_nao_sao_agrupados():
    """ITEM 4, o contraste: eventos separados no tempo continuam separados."""
    from pulsearb.backtest.__main__ import _agrupar_coincidentes

    longe = [
        {"inicio_ns": 0, "fim_ns": 10**9, "asset": "btc"},
        {"inicio_ns": 600 * 10**9, "fim_ns": 700 * 10**9, "asset": "eth"},
    ]
    assert _agrupar_coincidentes(longe) == []


def test_item5_veredito_diz_por_que_as_janelas_cairam():
    """ITEM 5. "SEM AMOSTRA: 8 elegiveis" sem a causa convida a inventá-la.

    Numa conversa real isso levou a explicar o número pela geometria das
    janelas — hipótese errada, registrada como conclusão antes de ser
    desmentida. A causa estava no relatório, em outro bloco, e ninguém
    cruzou os dois.
    """
    from pulsearb.analysis.anchor_sweep import veredito_da_ancora

    veredito = veredito_da_ancora(
        {
            "janelas_recebidas": 28,
            "janelas_elegiveis": 8,
            "janelas_sem_cobertura_do_stream": 20,
            "final_stream_no_fechamento": {"curva": {"0": 0.875}, "regiao_viavel_100pct": []},
        },
        cobertura={"pior_fracao_coberta": 0.4969},
    )

    texto = veredito["veredito"]
    assert "SEM AMOSTRA" in texto
    assert "CAUSA" in texto, "o veredito precisa dizer por que as janelas caíram"
    assert "20" in texto and "28" in texto
    assert "49.7%" in texto or "49,7" in texto or "AUSENCIA DE STREAM" in texto
    assert veredito["janelas_sem_cobertura_do_stream"] == 20
    assert veredito["pior_fracao_coberta"] == 0.4969


def test_item5_sem_perda_por_stream_nao_inventa_causa():
    """ITEM 5, o contraste: amostra pequena com captação sã não ganha desculpa."""
    from pulsearb.analysis.anchor_sweep import veredito_da_ancora

    veredito = veredito_da_ancora(
        {
            "janelas_recebidas": 8,
            "janelas_elegiveis": 8,
            "janelas_sem_cobertura_do_stream": 0,
            "final_stream_no_fechamento": {"curva": {"0": 1.0}, "regiao_viavel_100pct": []},
        },
        cobertura={"pior_fracao_coberta": 0.99},
    )

    assert "CAUSA" not in veredito["veredito"]


def test_item6_distribuicao_denuncia_amostra_concentrada():
    """ITEM 6. Amostra pequena e amostra ENVIESADA pedem consertos diferentes.

    As 8 elegíveis da gravação real estavam todas na primeira metade, porque
    o stream morreu aos 30 min. Gravar mais do mesmo jeito não conserta isso.
    """
    from pulsearb.analysis.anchor_sweep import _distribuicao_no_span

    base = 1_787_000_000_000
    todas = [base + i * 300_000 for i in range(12)]   # 12 janelas no span
    primeira_metade = todas[:4]                        # elegíveis só no começo

    dist = _distribuicao_no_span(primeira_metade, todas)

    assert dist["concentrada"] is True
    assert dist["quartis"]["q1"] + dist["quartis"]["q2"] == 4
    assert dist["quartis"]["q3"] == 0
    assert dist["quartis"]["q4"] == 0


def test_item6_distribuicao_espalhada_nao_acusa_vies():
    """ITEM 6, o contraste: elegíveis por toda a gravação não são enviesadas."""
    from pulsearb.analysis.anchor_sweep import _distribuicao_no_span

    base = 1_787_000_000_000
    todas = [base + i * 300_000 for i in range(12)]

    dist = _distribuicao_no_span(todas, todas)

    assert dist["concentrada"] is False
    assert dist["quartis_com_janela"] == 4
