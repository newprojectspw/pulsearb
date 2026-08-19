"""Replay determinístico, book/slippage e o backtest de ponta a ponta.

O backtest roda sobre gravação SINTÉTICA (tests/synthetic.py). Isso prova que
o pipeline funciona — não prova nada sobre existir edge. Ver o aviso em
docs/VEREDITO_M2.md.
"""

from __future__ import annotations

import gzip

import orjson
import pytest
from tests.synthetic import gerar_gravacao

from pulsearb.backtest.book import OrderBook, simulate_taker_buy
from pulsearb.backtest.report import BacktestReport, Trade
from pulsearb.backtest.runner import edge_liquido
from pulsearb.recorder.__main__ import parse_duration
from pulsearb.recorder.gaps import GapKind, GapTracker, resumo_gaps
from pulsearb.replay.player import ReplayMode, ReplayPlayer
from pulsearb.replay.reader import RecordingReader


# ------------------------------------------------------------------ duração
def test_parse_duration():
    assert parse_duration("72h") == 259200.0
    assert parse_duration("90s") == 90.0
    assert parse_duration("30m") == 1800.0
    assert parse_duration("7d") == 604800.0
    assert parse_duration("24") == 86400.0  # sem sufixo = horas
    with pytest.raises(ValueError):
        parse_duration("amanhã")


# --------------------------------------------------------------------- gaps
def test_gap_de_desconexao():
    tracker = GapTracker(fonte="rtds", silencio_limiar_s=5.0)
    assert tracker.observe(conectado=True, idade_ultima_msg_s=0.1, agora_wall_ns=0) is None
    assert tracker.observe(conectado=False, idade_ultima_msg_s=0.1, agora_wall_ns=int(1e9)) is None
    fechado = tracker.observe(
        conectado=True, idade_ultima_msg_s=0.1, agora_wall_ns=int(4e9)
    )
    assert fechado is not None
    assert fechado.kind is GapKind.DESCONEXAO
    assert fechado.duracao_s == pytest.approx(3.0)


def test_gap_de_silencio_usa_o_limiar_do_feed():
    tracker = GapTracker(fonte="rtds", silencio_limiar_s=5.0)
    # 3s de silêncio NÃO é lacuna para o TWAP (p99 medido = 2,47s)
    assert tracker.observe(conectado=True, idade_ultima_msg_s=3.0, agora_wall_ns=0) is None
    # 6s é
    assert tracker.observe(conectado=True, idade_ultima_msg_s=6.0, agora_wall_ns=int(1e9)) is None
    fechado = tracker.observe(
        conectado=True, idade_ultima_msg_s=0.1, agora_wall_ns=int(3e9)
    )
    assert fechado is not None and fechado.kind is GapKind.SILENCIO


def test_gap_aberto_no_fim_e_fechado_por_finalizar():
    tracker = GapTracker(fonte="x", silencio_limiar_s=1.0)
    tracker.observe(conectado=False, idade_ultima_msg_s=0, agora_wall_ns=0)
    fechado = tracker.finalizar(int(10e9))
    assert fechado is not None and fechado.duracao_s == pytest.approx(10.0)


def test_resumo_de_cobertura():
    tracker = GapTracker(fonte="rtds", silencio_limiar_s=1.0)
    tracker.observe(conectado=False, idade_ultima_msg_s=0, agora_wall_ns=0)
    tracker.observe(conectado=True, idade_ultima_msg_s=0, agora_wall_ns=int(10e9))
    resumo = resumo_gaps([tracker], duracao_total_s=100.0)
    assert resumo["por_fonte"]["rtds"]["n_gaps"] == 1
    assert resumo["por_fonte"]["rtds"]["cobertura_pct"] == pytest.approx(90.0)


# --------------------------------------------------------------------- book
BOOK_EVENT = {
    "event_type": "book",
    "asset_id": "tok",
    "timestamp": "1786891561000",
    "bids": [{"price": "0.64", "size": "120"}, {"price": "0.63", "size": "300"}],
    "asks": [{"price": "0.66", "size": "80"}, {"price": "0.67", "size": "250"}],
}


def test_book_do_evento():
    book = OrderBook.from_event(BOOK_EVENT)
    assert book is not None
    assert book.best_bid == 0.64
    assert book.best_ask == 0.66
    assert book.spread == pytest.approx(0.02)
    assert book.mid == pytest.approx(0.65)


def test_fill_dentro_do_topo_nao_tem_slippage():
    book = OrderBook.from_event(BOOK_EVENT)
    fill = simulate_taker_buy(book, 50)
    assert fill.completo
    assert fill.preco_medio == pytest.approx(0.66)
    assert fill.niveis_atravessados == 1


def test_fill_atravessa_niveis_e_paga_slippage():
    """80 no topo a 0.66, 20 no seguinte a 0.67 → médio 0.662."""
    book = OrderBook.from_event(BOOK_EVENT)
    fill = simulate_taker_buy(book, 100)
    assert fill.completo
    assert fill.niveis_atravessados == 2
    assert fill.preco_medio == pytest.approx((80 * 0.66 + 20 * 0.67) / 100)
    assert fill.preco_medio > 0.66  # slippage real, do book real


def test_fill_parcial_quando_o_book_nao_comporta():
    book = OrderBook.from_event(BOOK_EVENT)
    fill = simulate_taker_buy(book, 10_000)
    assert not fill.completo
    assert fill.shares == pytest.approx(330)  # 80 + 250, tudo que havia


def test_book_vazio_nao_preenche():
    fill = simulate_taker_buy(OrderBook(asset_id="x"), 5)
    assert not fill.preenchido


def test_price_change_remove_nivel_com_size_zero():
    book = OrderBook.from_event(BOOK_EVENT)
    book.apply_price_change(
        {
            "timestamp": "1786891562000",
            "changes": [{"price": "0.66", "size": "0", "side": "SELL"}],
        }
    )
    assert book.best_ask == 0.67


def test_profundidade_em_usdc():
    book = OrderBook.from_event(BOOK_EVENT)
    # 1 tick (0.01) do topo 0.66 → até 0.67: pega os dois níveis
    assert book.depth_usdc(side="ask", ticks=1, tick_size=0.01) == pytest.approx(
        80 * 0.66 + 250 * 0.67
    )
    # 0 ticks: só o topo
    assert book.depth_usdc(side="ask", ticks=0, tick_size=0.01) == pytest.approx(80 * 0.66)


# --------------------------------------------------------------------- edge
def test_edge_liquido_desconta_taxa():
    # prob 0.60, preço 0.55, fee em 0.55 = 0.07*0.55*0.45 = 0.017325
    edge = edge_liquido(prob=0.60, preco=0.55, fee_rate=0.07, fee_exponent=1.0)
    assert edge == pytest.approx(0.60 - 0.55 - 0.017325)
    # A taxa come 35% do edge bruto de 5pp — é esse o problema do jogo.
    assert edge < 0.05


# ------------------------------------------------------------------- replay
def test_replay_le_e_ordena(tmp_path):
    caminho = gerar_gravacao(tmp_path / "rec.jsonl.gz", n_janelas=2)
    reader = RecordingReader(caminho)
    registros = list(reader.iter_records())
    assert registros
    ts = [r.ts_mono_ns for r in registros]
    assert ts == sorted(ts)  # ordem cronológica global
    assert reader.corrompidas == 0


def test_replay_e_deterministico(tmp_path):
    """Duas passadas produzem exatamente a mesma sequência."""
    caminho = gerar_gravacao(tmp_path / "rec.jsonl.gz", n_janelas=2)

    def passada():
        eventos = []
        player = ReplayPlayer(caminho, on_event=lambda e: eventos.append(
            (e.source, e.ts_mono_ns, e.raw)
        ))
        player.run_sync()
        return eventos, player.resumo()

    a, resumo_a = passada()
    b, resumo_b = passada()
    assert a == b
    assert resumo_a == resumo_b
    assert resumo_a["eventos"] > 0


def test_replay_separa_meta_de_evento(tmp_path):
    caminho = gerar_gravacao(tmp_path / "rec.jsonl.gz", n_janelas=1)
    eventos, metas = [], []
    player = ReplayPlayer(caminho, on_event=eventos.append, on_meta=metas.append)
    player.run_sync()
    # snapshots de descoberta são meta, não evento de feed
    assert all(e.source in ("rtds", "poly_ws") for e in eventos)
    assert all(m.fonte == "discovery_snapshot" for m in metas)
    assert metas


def test_replay_passo_a_passo(tmp_path):
    caminho = gerar_gravacao(tmp_path / "rec.jsonl.gz", n_janelas=1)
    player = ReplayPlayer(caminho, mode=ReplayMode.PASSO)
    primeiro = player.step()
    segundo = player.step()
    assert primeiro is not None and segundo is not None
    assert segundo.ts_mono_ns >= primeiro.ts_mono_ns
    n = 2
    while player.step() is not None:
        n += 1
    assert n == player.emitidos + player.meta_emitidos


async def test_replay_tempo_real_respeita_deltas(tmp_path):
    """Com speed alto o replay não trava, e a ordem se mantém."""
    caminho = gerar_gravacao(tmp_path / "rec.jsonl.gz", n_janelas=1)
    eventos = []
    player = ReplayPlayer(
        caminho, mode=ReplayMode.TEMPO_REAL, speed=100_000.0,
        on_event=lambda e: eventos.append(e.ts_mono_ns),
    )
    await player.run()
    assert eventos == sorted(eventos)


def test_replay_tolera_linha_corrompida(tmp_path):
    caminho = gerar_gravacao(tmp_path / "rec.jsonl.gz", n_janelas=1)
    # Anexa lixo no fim, como faria um recorder morto no meio de uma linha.
    with gzip.open(caminho, "ab") as handle:
        handle.write(b'{"ts_mono_ns": 1, "fonte": incompleto\n')
    reader = RecordingReader(caminho)
    registros = list(reader.iter_records())
    assert registros
    assert reader.corrompidas == 1


def test_reader_expoe_gaps(tmp_path):
    caminho = tmp_path / "rec.jsonl.gz"
    with gzip.open(caminho, "wb") as handle:
        handle.write(
            orjson.dumps(
                {
                    "ts_mono_ns": 1,
                    "ts_wall_ns": 1,
                    "fonte": "gap",
                    "payload": {"fonte": "rtds", "tipo": "desconexao", "duracao_s": 12.5},
                }
            )
            + b"\n"
        )
    assert RecordingReader(caminho).gaps() == [
        {"fonte": "rtds", "tipo": "desconexao", "duracao_s": 12.5}
    ]


# ---------------------------------------------------------------- relatório
def _trade(**kwargs) -> Trade:
    base = dict(
        slug="btc-updown-5m-1",
        jogo="twap",
        asset="btc",
        duracao_s=300,
        bucket_tempo="<30s",
        prob_prevista=0.7,
        preco_pago=0.6,
        shares=5.0,
        custo_usdc=3.0,
        fee_usdc=0.084,
        latencia_ms=300.0,
        resolveu_up=True,
        lado_up=True,
    )
    base.update(kwargs)
    return Trade(**base)


def test_pnl_de_trade_vencedor():
    trade = _trade()
    assert trade.acertou
    assert trade.payout_usdc == 5.0
    assert trade.pnl_usdc == pytest.approx(5.0 - 3.0 - 0.084)


def test_pnl_de_trade_perdedor():
    trade = _trade(resolveu_up=False)
    assert not trade.acertou
    assert trade.payout_usdc == 0.0
    assert trade.pnl_usdc == pytest.approx(-3.084)


def test_relatorio_agrega():
    report = BacktestReport()
    report.janelas_avaliadas = 3
    report.sinais_gerados = 5
    report.add_trade(_trade())
    report.add_trade(_trade(resolveu_up=False, jogo="horario", duracao_s=3600))
    saida = report.to_dict()
    assert saida["resumo"]["trades"] == 2
    assert saida["resumo"]["hit_rate"] == pytest.approx(0.5)
    assert set(saida["por_jogo"]) == {"twap", "horario"}
    assert saida["funil_de_sinais"]["gerados"] == 5


def test_drawdown_maximo():
    report = BacktestReport()
    report.add_trade(_trade())                      # +1.916
    report.add_trade(_trade(resolveu_up=False))     # -3.084
    report.add_trade(_trade(resolveu_up=False))     # -3.084
    assert report.max_drawdown() == pytest.approx(-6.168)


def test_calibracao_mede_todas_as_previsoes():
    report = BacktestReport()
    for _ in range(8):
        report.add_calibration("<30s", 0.8, True)
    for _ in range(2):
        report.add_calibration("<30s", 0.8, False)
    bucket = report.calibracao["<30s"]
    assert bucket.prob_media_prevista == pytest.approx(0.8)
    assert bucket.freq_realizada == pytest.approx(0.8)
    assert bucket.erro_calibracao == pytest.approx(0.0)  # perfeitamente calibrado


# ------------------------------------------------- streaming do reader
def _gravacao_multi_arquivo(tmp_path, arquivos=4, por_arquivo=500, jitter_ns=50_000_000):
    """Simula a rotação horária COM a desordem local entre feeds.

    O jitter não é enfeite: três feeds submetem em paralelo, então dentro de
    um arquivo os ts_mono_ns vêm quase ordenados, não ordenados. É isso que o
    buffer de reordenação existe para absorver.
    """
    import random

    rng = random.Random(11)
    ts = 1786891500 * 10**9
    for indice in range(arquivos):
        with gzip.open(tmp_path / f"rec-{indice:02d}.jsonl.gz", "wb") as handle:
            for _ in range(por_arquivo):
                ts += rng.randint(1_000_000, 9_000_000)
                desvio = rng.randint(-jitter_ns, jitter_ns)
                handle.write(
                    orjson.dumps(
                        {
                            "ts_mono_ns": ts + desvio,
                            "ts_wall_ns": ts + desvio,
                            "fonte": rng.choice(["rtds", "poly_ws", "binance_ws"]),
                            "payload": {"n": 1},
                        }
                    )
                    + b"\n"
                )
    return tmp_path


def test_merge_entre_arquivos_sai_ordenado(tmp_path):
    """Arquivo rotacionado não garante ordem GLOBAL: o merge é que garante."""
    diretorio = _gravacao_multi_arquivo(tmp_path)
    reader = RecordingReader(diretorio)
    ts = [r.ts_mono_ns for r in reader.iter_records()]
    assert len(ts) == 2000
    assert ts == sorted(ts)
    assert reader.fora_de_ordem == 0


def test_reader_nao_carrega_tudo_em_memoria(tmp_path):
    """O reader é um GERADOR: consumir 1 registro não pode ler a gravação toda.

    Regressão da versão que fazia sort() sobre a lista completa — com os
    ~400 MB/h reais, aquilo não terminava numa máquina de análise.
    """
    diretorio = _gravacao_multi_arquivo(tmp_path, arquivos=4, por_arquivo=5_000)
    reader = RecordingReader(diretorio, reorder_buffer=100)
    fluxo = reader.iter_records()
    next(fluxo)
    # Só o necessário para encher o buffer foi lido, não os 20.000.
    assert reader.total < 1_000, f"leu {reader.total} registros para emitir 1"


def test_buffer_pequeno_conta_o_que_saiu_fora_de_ordem(tmp_path):
    """Inversão maior que o buffer é CONTADA, não escondida."""
    diretorio = _gravacao_multi_arquivo(tmp_path, arquivos=1, por_arquivo=300)
    reader = RecordingReader(diretorio, reorder_buffer=1)
    ts = [r.ts_mono_ns for r in reader.iter_records()]
    assert ts != sorted(ts)          # com buffer 1 a desordem passa
    assert reader.fora_de_ordem > 0  # e o reader avisa


def test_contadores_resetam_entre_passadas(tmp_path):
    diretorio = _gravacao_multi_arquivo(tmp_path, arquivos=2, por_arquivo=100)
    reader = RecordingReader(diretorio)
    primeira = sum(1 for _ in reader.iter_records())
    total_primeira = reader.total
    segunda = sum(1 for _ in reader.iter_records())
    assert primeira == segunda == 200
    assert reader.total == total_primeira  # não acumulou


# ---------------------------------------------------------------- M2.1 BUG 5
# A memória do backtest. Um único arquivo de ~450 MB matava o processo com
# `Killed` numa máquina de 1 GB, porque a linha do tempo do book guardava um
# clone completo a cada `price_change` — ~12 milhões por hora de gravação.


def _book(ask: float, size: float = 100.0, ts_ns: int = 0):
    from pulsearb.backtest.book import OrderBook

    return OrderBook(
        asset_id="t",
        bids=[(round(ask - 0.01, 4), size)],
        asks=[(ask, size)],
        ts_ns=ts_ns,
    )


def test_timeline_nao_guarda_snapshot_de_topo_repetido():
    """Deduplicação: se o topo não mudou, `at()` devolveria o mesmo objeto.

    É a defesa que mais economiza, e é LOSSLESS: a maioria dos `price_change`
    mexe em nível fundo, que a truncagem já descarta.
    """
    from pulsearb.backtest.runner import BookTimeline

    timeline = BookTimeline()
    for ts in range(1_000):
        timeline.append(_book(0.60), ts)

    assert len(timeline.ts) == 1
    assert timeline.descartados == 999
    # E o que sobrou responde igual em qualquer instante.
    assert timeline.at(999).best_ask == 0.60


def test_timeline_trunca_aos_niveis_do_topo():
    """Truncagem é perda REAL de informação — por isso explícita e testada."""
    from pulsearb.backtest.book import OrderBook
    from pulsearb.backtest.runner import BookTimeline

    fundo = OrderBook(
        asset_id="t",
        bids=[(0.5 - i * 0.01, 10.0) for i in range(40)],
        asks=[(0.6 + i * 0.01, 10.0) for i in range(40)],
    )
    timeline = BookTimeline(niveis=3)
    timeline.append(fundo, 1)

    guardado = timeline.at(1)
    assert len(guardado.asks) == 3
    assert len(guardado.bids) == 3
    assert guardado.best_ask == 0.6  # o topo, que é o que o backtest lê


def test_timeline_respeita_o_teto_por_token():
    """Teto duro: memória previsível antes de rodar, não descoberta no OOM."""
    from pulsearb.backtest.runner import BookTimeline

    timeline = BookTimeline(limite=100)
    for ts in range(50_000):
        # topo sempre diferente: a deduplicação não ajuda aqui de propósito
        timeline.append(_book(round(0.10 + (ts % 800) * 0.001, 4)), ts * 1_000_000)

    assert len(timeline.ts) <= 100
    assert timeline.raleamentos > 0
    # A cobertura temporal sobrevive ao raleamento: começo e fim continuam lá.
    assert timeline.at(0) is not None
    assert timeline.at(49_999 * 1_000_000) is not None
    # E a resolução efetiva fica reportada, não escondida.
    assert timeline.resolucao_ns > 0


def test_timeline_isola_o_snapshot_da_mutacao_posterior():
    """O indexador agora muta o book NO LUGAR; a timeline faz a própria cópia.

    Se a timeline guardasse a referência, um `price_change` posterior
    reescreveria o passado e o backtest preencheria com um livro que nunca
    existiu naquele instante.
    """
    from pulsearb.backtest.runner import BookTimeline

    timeline = BookTimeline()
    corrente = _book(0.60)
    timeline.append(corrente, 1)
    corrente.apply_price_change(
        {"timestamp": "2", "changes": [{"price": "0.60", "size": "0", "side": "SELL"}]}
    )
    timeline.append(corrente, 2)

    assert timeline.at(1).best_ask == 0.60
    assert timeline.at(2).best_ask is None
