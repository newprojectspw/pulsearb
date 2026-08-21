"""M2.5 — recalibração do detector de integridade.

O que estes testes protegem, em uma frase: o detector do M2.2 reprovou **200
de 200 janelas** da gravação real medindo a corrida entre `best_bid_ask` e
`price_change`, e chamando isso de corrupção. Cada teste aqui fixa uma das
quatro correções — e, onde cabe, prova o ANTES e o DEPOIS na mesma rodada,
porque "o alinhamento melhorou" é afirmação, não medição.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import orjson
import pytest

from pulsearb.analysis.integrity import (
    MAGNITUDE_CRITICA,
    MonitorDeIntegridade,
)
from pulsearb.replay.reader import RecordingReader

TOKEN = "tok"


def _book(ts_ms: int, bid: str = "0.49", ask: str = "0.51") -> dict:
    return {
        "event_type": "book",
        "asset_id": TOKEN,
        "timestamp": str(ts_ms),
        "bids": [{"price": bid, "size": "100"}],
        "asks": [{"price": ask, "size": "100"}],
    }


def _delta(
    ts_ms: int,
    *,
    price: str,
    size: str,
    side: str,
    best_bid: str,
    best_ask: str,
) -> dict:
    return {
        "event_type": "price_change",
        "market": "0xabc",
        "timestamp": str(ts_ms),
        "price_changes": [
            {
                "asset_id": TOKEN,
                "price": price,
                "size": size,
                "side": side,
                "best_bid": best_bid,
                "best_ask": best_ask,
            }
        ],
    }


def _bba(ts_ms: int, best_bid: str, best_ask: str) -> dict:
    return {
        "event_type": "best_bid_ask",
        "asset_id": TOKEN,
        "timestamp": str(ts_ms),
        "best_bid": best_bid,
        "best_ask": best_ask,
    }


# ────────────────────────────── tarefa 1: alinhamento por carimbo do servidor


def test_alinhamento_por_carimbo_desfaz_divergencia_de_corrida():
    """O caso exato que produziu os 4 milhões de divergências.

    Ordem de CHEGADA: snapshot, delta que sobe o bid para 0.60, e só então um
    `best_bid_ask` que afirma 0.49 — porque ele descreve um instante ANTERIOR
    ao delta. Comparado contra o livro "atual", ele diverge por 0.11. Contra o
    livro como estava no carimbo dele, ele bate na mosca.

    As duas contas saem lado a lado de propósito: sem o número velho não há
    como saber se o alinhamento resolveu alguma coisa.
    """
    monitor = MonitorDeIntegridade()
    monitor.observar(_book(1000), 1_000_000_000)
    monitor.observar(
        _delta(2000, price="0.60", size="10", side="BUY",
               best_bid="0.60", best_ask="0.51"),
        2_000_000_000,
    )
    # chega atrasado, falando de t=1500
    monitor.observar(_bba(1500, "0.49", "0.51"), 3_000_000_000)
    monitor.finalizar()

    resumo = monitor.resumo()
    bruto = resumo["alinhamento"]["por_chegada_local"]
    alinhado = resumo["alinhamento"]["por_carimbo_do_servidor"]

    assert bruto["divergencias"] == 1, "a conta antiga TEM de acusar — é o bug"
    assert alinhado["divergencias"] == 0, "a conta nova não pode acusar corrida"
    assert monitor.qualidade_do_token(TOKEN) == "alta"


def test_alinhamento_nao_esconde_perda_de_verdade():
    """A defesa contra o remédio virar veneno.

    Se o alinhamento apagasse divergência real, ele seria só uma forma
    elegante de maquiar. Aqui o `best_bid_ask` é posterior a tudo e afirma um
    topo que a reconstrução não tem em instante nenhum: as duas contas acusam.
    """
    monitor = MonitorDeIntegridade()
    monitor.observar(_book(1000), 1_000_000_000)
    monitor.observar(_bba(5000, "0.80", "0.81"), 5_000_000_000)
    monitor.finalizar()

    resumo = monitor.resumo()
    assert resumo["alinhamento"]["por_chegada_local"]["divergencias"] == 2
    assert resumo["alinhamento"]["por_carimbo_do_servidor"]["divergencias"] == 2


def test_delta_com_carimbo_fora_de_ordem_e_contado_nao_comparado():
    """Delta antigo aplicado sobre estado novo: comparar seria comparar contra
    o futuro. Ele entra no livro (é a ordem que o recorder gravou) e o
    cruzamento é pulado, com contador próprio."""
    monitor = MonitorDeIntegridade()
    monitor.observar(_book(1000), 1_000_000_000)
    monitor.observar(
        _delta(3000, price="0.60", size="10", side="BUY",
               best_bid="0.60", best_ask="0.51"),
        3_000_000_000,
    )
    monitor.observar(
        _delta(2000, price="0.55", size="10", side="BUY",
               best_bid="0.60", best_ask="0.51"),
        4_000_000_000,
    )
    monitor.finalizar()

    resumo = monitor.resumo()
    assert resumo["alinhamento"]["deltas_com_carimbo_fora_de_ordem"] == 1


# ──────────────────────────────── tarefa 2: lado vazio tem quatro causas


def test_lado_esvaziado_por_delta_e_truncagem_nao_corrupcao():
    """O delta leva o último nível que tínhamos e o servidor mostra um nível
    abaixo, que nunca nos foi contado. Isso é a NOSSA visão de profundidade
    acabando — não o livro furando."""
    monitor = MonitorDeIntegridade()
    monitor.observar(_book(1000), 1_000_000_000)
    monitor.observar(
        _delta(2000, price="0.49", size="0", side="BUY",
               best_bid="0.48", best_ask="0.51"),
        2_000_000_000,
    )
    monitor.finalizar()

    resumo = monitor.resumo()
    assert resumo["lado_vazio"]["por_causa"] == {"esvaziado_por_delta": 1}
    assert monitor.qualidade_do_token(TOKEN) != "baixa"


def test_lado_vazio_desde_o_snapshot_tambem_nao_invalida():
    monitor = MonitorDeIntegridade()
    snapshot = _book(1000)
    snapshot["bids"] = []
    monitor.observar(snapshot, 1_000_000_000)
    monitor.observar(
        _delta(2000, price="0.51", size="10", side="SELL",
               best_bid="0.48", best_ask="0.51"),
        2_000_000_000,
    )
    monitor.finalizar()

    resumo = monitor.resumo()
    assert resumo["lado_vazio"]["por_causa"] == {"vazio_desde_o_snapshot": 1}
    assert monitor.qualidade_do_token(TOKEN) != "baixa"


def test_token_sem_snapshot_nenhum_e_baixa():
    """Sem livro inicial não há reconstrução: há chute. E chute não entra em
    `divergencias` — contá-lo como divergência foi o que encheu o relatório
    do M2.2 com 2,7 milhões de 'lado vazio'."""
    monitor = MonitorDeIntegridade()
    for ts in range(1000, 5000, 500):
        monitor.observar(
            _delta(ts, price="0.49", size="10", side="BUY",
                   best_bid="0.49", best_ask="0.51"),
            ts * 1_000_000,
        )
    monitor.finalizar()

    resumo = monitor.resumo()
    assert resumo["divergencias"] == 0, "ausência de livro não é divergência"
    assert resumo["lado_vazio"]["sem_livro_por_causa"]["sem_snapshot"] > 0
    assert monitor.qualidade_do_token(TOKEN) == "baixa"


def test_perda_conhecida_conta_tempo_sem_livro_e_o_resync_estanca():
    monitor = MonitorDeIntegridade()
    monitor.observar(_book(1000), 1_000_000_000)
    monitor.observar(
        _delta(2000, price="0.49", size="10", side="BUY",
               best_bid="0.49", best_ask="0.51"),
        2_000_000_000,
    )
    monitor.marcar_perda(TOKEN)
    monitor.observar(
        _delta(3000, price="0.49", size="10", side="BUY",
               best_bid="0.49", best_ask="0.51"),
        3_000_000_000,
    )
    monitor.observar(_book(4000), 4_000_000_000)
    monitor.finalizar()

    resumo = monitor.resumo()
    assert resumo["lado_vazio"]["sem_livro_por_causa"]["apos_perda"] > 0
    # o snapshot novo fecha a conta: 2000 ms de perda, não a gravação inteira
    assert resumo["lado_vazio"]["ms_sem_livro_total"] == pytest.approx(2000.0)


# ─────────────────────────── tarefa 3: invalidação por conjunção de critérios


def test_um_tick_isolado_nao_condena_o_token():
    """O erro de calibração do M2.2, em forma de teste.

    0,01 de magnitude é UM tick de mercado — o p50 observado na gravação
    real. Um episódio desses, que some na mensagem seguinte, é corrida.
    """
    monitor = MonitorDeIntegridade()
    monitor.observar(_book(0), 0)
    monitor.observar(
        _delta(1000, price="0.49", size="10", side="BUY",
               best_bid="0.50", best_ask="0.51"),
        1_000_000_000,
    )
    for ts in range(1200, 100_000, 5000):
        monitor.observar(
            _delta(ts, price="0.49", size="10", side="BUY",
                   best_bid="0.49", best_ask="0.51"),
            ts * 1_000_000,
        )
    monitor.finalizar()

    assert monitor.divergencias == 1, "a divergência é DETECTADA"
    assert monitor.token_corrompido(TOKEN) is False, "mas não condena"
    assert monitor.qualidade_do_token(TOKEN) == "alta"


def test_divergencia_relevante_mas_efemera_nao_e_persistente():
    """4 ticks de magnitude, 200 ms de duração: passa no critério de
    magnitude e reprova no de persistência. A conjunção existe para isto."""
    monitor = MonitorDeIntegridade()
    monitor.observar(_book(0), 0)
    monitor.observar(
        _delta(1000, price="0.49", size="10", side="BUY",
               best_bid="0.53", best_ask="0.51"),
        1_000_000_000,
    )
    monitor.observar(
        _delta(1200, price="0.49", size="10", side="BUY",
               best_bid="0.49", best_ask="0.51"),
        1_200_000_000,
    )
    monitor.finalizar()

    resumo = monitor.resumo()
    assert resumo["criterio_de_invalidacao"]["divergencias_persistentes"] == 0
    assert monitor.qualidade_do_token(TOKEN) == "alta"


def test_divergencia_relevante_e_persistente_condena():
    """4 ticks mantidos por 2 segundos numa observação de 3: corrupção."""
    monitor = MonitorDeIntegridade()
    monitor.observar(_book(0), 0)
    for ts in range(1000, 3001, 200):
        monitor.observar(
            _delta(ts, price="0.49", size="10", side="BUY",
                   best_bid="0.53", best_ask="0.51"),
            ts * 1_000_000,
        )
    monitor.finalizar()

    resumo = monitor.resumo()
    assert resumo["criterio_de_invalidacao"]["divergencias_persistentes"] == 1
    assert monitor.qualidade_do_token(TOKEN) == "baixa"
    assert monitor.token_corrompido(TOKEN) is True


def test_magnitude_critica_condena_sem_esperar_fracao():
    """Meio dime de erro no topo muda o lado do trade. Não há fração de tempo
    que torne isso aceitável."""
    monitor = MonitorDeIntegridade()
    monitor.observar(_book(0), 0)
    grande = f"{0.49 + MAGNITUDE_CRITICA + 0.02:.2f}"
    for ts in (1000, 1500):
        monitor.observar(
            _delta(ts, price="0.49", size="10", side="BUY",
                   best_bid=grande, best_ask="0.51"),
            ts * 1_000_000,
        )
    for ts in range(2000, 1_000_000, 50_000):
        monitor.observar(
            _delta(ts, price="0.49", size="10", side="BUY",
                   best_bid="0.49", best_ask="0.51"),
            ts * 1_000_000,
        )
    monitor.finalizar()

    # fração de tempo minúscula — e ainda assim baixa
    assert monitor.qualidade_do_token(TOKEN) == "baixa"


def test_k_nunca_desce_abaixo_de_dois_ticks():
    """`--ticks-divergencia 1` reporia exatamente o limiar que zerou 200
    janelas. O piso é do código, não da boa vontade de quem chama."""
    monitor = MonitorDeIntegridade(ticks_divergencia=1)
    assert monitor.magnitude_minima == pytest.approx(0.02)


# ─────────────────────────────────── tarefa 4: marca de qualidade, não gate


def test_as_tres_marcas_existem_e_sao_ordenadas_por_fracao_de_tempo():
    """Mesma doença, três doses. É a fração de tempo que separa as marcas."""

    def _monitor(fim_divergencia_ms: int, fim_observacao_ms: int) -> str:
        monitor = MonitorDeIntegridade()
        monitor.observar(_book(0), 0)
        for ts in range(1000, fim_divergencia_ms + 1, 100):
            monitor.observar(
                _delta(ts, price="0.49", size="10", side="BUY",
                       best_bid="0.53", best_ask="0.51"),
                ts * 1_000_000,
            )
        for ts in range(fim_divergencia_ms + 100, fim_observacao_ms, 10_000):
            monitor.observar(
                _delta(ts, price="0.49", size="10", side="BUY",
                       best_bid="0.49", best_ask="0.51"),
                ts * 1_000_000,
            )
        monitor.finalizar()
        return monitor.qualidade_do_token(TOKEN)

    assert _monitor(1300, 1_000_000) == "alta"     # 300 ms em 1000 s
    assert _monitor(1600, 100_000) == "media"      # 600 ms em 100 s
    assert _monitor(3000, 10_000) == "baixa"       # 2 s em 10 s


def test_janela_herda_a_pior_marca_dos_dois_tokens():
    """Não adianta o livro do Up estar impecável se o do Down está furado: a
    entrada precisa dos dois lados para ter preço."""
    monitor = MonitorDeIntegridade()
    bom = _book(0)
    bom["asset_id"] = "up"
    monitor.observar(bom, 0)
    monitor.observar(
        {**_delta(1000, price="0.49", size="10", side="BUY",
                  best_bid="0.49", best_ask="0.51"),
         "price_changes": [{"asset_id": "up", "price": "0.49", "size": "10",
                            "side": "BUY", "best_bid": "0.49",
                            "best_ask": "0.51"}]},
        1_000_000_000,
    )
    ruim = _book(0)
    ruim["asset_id"] = "down"
    monitor.observar(ruim, 0)
    for ts in range(1000, 3001, 200):
        monitor.observar(
            {**_delta(ts, price="0.49", size="10", side="BUY",
                      best_bid="0.53", best_ask="0.51"),
             "price_changes": [{"asset_id": "down", "price": "0.49",
                                "size": "10", "side": "BUY",
                                "best_bid": "0.53", "best_ask": "0.51"}]},
            ts * 1_000_000,
        )
    monitor.finalizar()

    assert monitor.qualidade_do_token("up") == "alta"
    assert monitor.qualidade_do_token("down") == "baixa"
    assert monitor.qualidade_da_janela("up", "down") == "baixa"


def test_token_nunca_visto_e_sem_dado_e_nao_corrompido():
    """`sem_dado` não é acusação: é ausência de observação. Tratá-lo como
    corrompido esconderia a janela por um motivo que não é qualidade."""
    monitor = MonitorDeIntegridade()
    assert monitor.qualidade_do_token("fantasma") == "sem_dado"
    assert monitor.token_corrompido("fantasma") is False
    assert monitor.qualidade_da_janela("fantasma", "outro") == "sem_dado"


# ───────────────────────────────── tarefa 5: arquivo ilegível não derruba tudo


def _gz(path: Path, linhas: int, ts_base: int) -> None:
    corpo = b"".join(
        orjson.dumps(
            {
                "ts_mono_ns": ts_base + i,
                "ts_wall_ns": ts_base + i,
                "fonte": "poly_ws",
                "payload": {"event_type": "book", "asset_id": TOKEN},
            }
        )
        + b"\n"
        for i in range(linhas)
    )
    path.write_bytes(gzip.compress(corpo))


def test_gzip_quebrado_nao_aborta_a_corrida(tmp_path):
    """`zlib.error: invalid literal/length/distance code` abortava a rodada
    inteira. Em 72h de gravação, um arquivo ilegível não pode custar os
    outros 71 — ele é abandonado, contado, e a leitura segue."""
    bom = tmp_path / "pulsearb-20260820-1000.jsonl.gz"
    _gz(bom, 5, 1_000)
    ruim = tmp_path / "pulsearb-20260820-1100.jsonl.gz"
    _gz(ruim, 200, 2_000)
    bruto = bytearray(ruim.read_bytes())
    bruto[40] ^= 0xFF   # corrompe o fluxo deflate depois do cabeçalho
    ruim.write_bytes(bytes(bruto))
    outro_bom = tmp_path / "pulsearb-20260820-1200.jsonl.gz"
    _gz(outro_bom, 5, 3_000)

    reader = RecordingReader(tmp_path)
    registros = list(reader.iter_records())

    assert len(reader.arquivos_ilegiveis) == 1
    assert reader.arquivos_ilegiveis[0]["arquivo"] == ruim.name
    assert "zlib" in reader.arquivos_ilegiveis[0]["erro"].lower() or (
        "error" in reader.arquivos_ilegiveis[0]["erro"].lower()
    )
    # os dois arquivos sãos continuam inteiros
    assert sum(1 for r in registros if r.ts_mono_ns < 2_000) == 5
    assert sum(1 for r in registros if r.ts_mono_ns >= 3_000) == 5


def test_arquivo_ilegivel_nao_conta_como_linha_corrompida(tmp_path):
    """Duas doenças, dois contadores. Somar as duas faria 72h de gravação
    parecer sã por diluição."""
    ruim = tmp_path / "pulsearb-20260820-1100.jsonl.gz"
    ruim.write_bytes(b"nao sou gzip coisa nenhuma")

    reader = RecordingReader(tmp_path)
    assert list(reader.iter_records()) == []
    assert len(reader.arquivos_ilegiveis) == 1
    assert reader.corrompidas == 0


# ─────────── o defeito mais caro do M2.2: conferir DENTRO da mensagem


def test_topo_e_conferido_depois_de_toda_a_mensagem_nao_a_cada_nivel():
    """A assinatura de "1 tick" dos 4 milhões de divergências de produção.

    Uma mensagem `price_change` move o topo de 0.49 para 0.50: insere o nível
    novo e remove o antigo, e o `best_bid` que ela carrega descreve o livro
    **depois das duas coisas**. Conferindo a cada mudança, o estado
    intermediário (com os dois níveis presentes) fica exatamente UM tick fora
    — e era assim que o M2.2 contava.

    O teste falha se alguém voltar a conferir por mudança: a divergência
    reaparece com magnitude 0.01.
    """
    monitor = MonitorDeIntegridade()
    monitor.observar(_book(1000, bid="0.49", ask="0.51"), 1_000_000_000)
    evento = {
        "event_type": "price_change",
        "timestamp": "2000",
        "price_changes": [
            # insere o novo topo primeiro: o estado intermediário tem 0.49 E
            # 0.50, e o topo intermediário (0.50) até coincide...
            {"asset_id": TOKEN, "price": "0.50", "size": "100", "side": "BUY",
             "best_bid": "0.50", "best_ask": "0.51"},
            {"asset_id": TOKEN, "price": "0.49", "size": "0", "side": "BUY",
             "best_bid": "0.50", "best_ask": "0.51"},
        ],
    }
    monitor.observar(evento, 2_000_000_000)

    # ...e agora a ordem inversa, que é a que denuncia: remover primeiro
    # deixa o topo intermediário em 0.48, um tick fora do afirmado.
    # Dois níveis no bid, para que remover o topo deixe um topo intermediário
    # REAL (0.48) um tick fora — e não um lado vazio, que seria outra doença.
    fundo = _book(3000, bid="0.49", ask="0.51")
    fundo["bids"] = [
        {"price": "0.49", "size": "100"},
        {"price": "0.48", "size": "300"},
    ]
    monitor.observar(fundo, 3_000_000_000)
    inverso = {
        "event_type": "price_change",
        "timestamp": "4000",
        "price_changes": [
            {"asset_id": TOKEN, "price": "0.49", "size": "0", "side": "BUY",
             "best_bid": "0.50", "best_ask": "0.51"},
            {"asset_id": TOKEN, "price": "0.50", "size": "100", "side": "BUY",
             "best_bid": "0.50", "best_ask": "0.51"},
        ],
    }
    achados = monitor.observar(inverso, 4_000_000_000)
    monitor.finalizar()

    assert achados == [], "o estado intermediário não é o estado do servidor"
    assert monitor.divergencias == 0
    assert monitor.qualidade_do_token(TOKEN) == "alta"


def test_uma_mensagem_pode_tocar_dois_tokens_e_cada_um_e_conferido():
    """A conferência é agrupada por `asset_id`: juntar os dois numa só
    comparação trocaria o topo de um pelo do outro."""
    monitor = MonitorDeIntegridade()
    up = _book(1000, bid="0.49", ask="0.51")
    up["asset_id"] = "up"
    down = _book(1000, bid="0.29", ask="0.31")
    down["asset_id"] = "down"
    monitor.observar(up, 1_000_000_000)
    monitor.observar(down, 1_000_000_000)

    evento = {
        "event_type": "price_change",
        "timestamp": "2000",
        "price_changes": [
            {"asset_id": "up", "price": "0.50", "size": "100", "side": "BUY",
             "best_bid": "0.50", "best_ask": "0.51"},
            {"asset_id": "up", "price": "0.49", "size": "0", "side": "BUY",
             "best_bid": "0.50", "best_ask": "0.51"},
            {"asset_id": "down", "price": "0.28", "size": "100", "side": "BUY",
             "best_bid": "0.29", "best_ask": "0.31"},
        ],
    }
    assert monitor.observar(evento, 2_000_000_000) == []
    monitor.finalizar()
    assert monitor.divergencias == 0
    assert monitor.qualidade_do_token("up") == "alta"
    assert monitor.qualidade_do_token("down") == "alta"
