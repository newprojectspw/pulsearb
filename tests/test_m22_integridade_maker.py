"""M2.2 — integridade de dados (parte A) e instrumentação da rota maker (B).

Nenhum teste aqui depende de rede externa (regra do M1).
"""

from __future__ import annotations

import asyncio
import gzip
import json

import orjson
import pytest

from pulsearb.analysis.integrity import (
    MonitorDeIntegridade,
    MonitorDeRelogio,
)
from pulsearb.analysis.measurements import conta_do_maker, medir_markout
from pulsearb.analysis.rewards import (
    OrdemHipotetica,
    ParametrosDeReward,
    fatia_do_pool,
    score_da_ordem,
    score_de_nivel,
    score_do_livro,
    simular,
)
from pulsearb.backtest.book import OrderBook
from pulsearb.backtest.runner import BookTimeline, WindowState
from pulsearb.feeds.poly_ws import (
    forma_do_price_change,
    iter_mudancas,
    tokens_do_evento,
)
from pulsearb.recorder.writer import (
    CANAL_BOOK,
    CANAL_PADRAO,
    JsonlGzipWriter,
    RecordEnvelope,
)

# ════════════════════════════════════════════════ A.1 — canal sem perda


async def test_book_e_preco_vao_por_canais_diferentes(tmp_path):
    """Tick de preço pode ser descartado; delta de livro não."""
    writer = JsonlGzipWriter(output_dir=tmp_path, queue_max=2, queue_max_book=64)
    for i in range(10):
        writer.submit(RecordEnvelope(i, i, "rtds", b'{"n":%d}' % i))
    for i in range(10):
        writer.submit(RecordEnvelope(i, i, "poly_ws", b'{"n":%d}' % i), canal=CANAL_BOOK)

    # a fila padrão (2) transbordou; a de livro (64) não
    assert writer.dropped_por_canal[CANAL_PADRAO] == 8
    assert writer.dropped_por_canal.get(CANAL_BOOK, 0) == 0

    await writer.start()
    await asyncio.sleep(0.05)
    await writer.stop()
    linhas = []
    for path in sorted(tmp_path.glob("*.jsonl.gz")):
        with gzip.open(path, "rb") as handle:
            linhas.extend(json.loads(linha) for linha in handle if linha.strip())
    assert sum(1 for linha in linhas if linha["fonte"] == "poly_ws") == 10


async def test_transbordo_do_canal_de_livro_avisa(tmp_path):
    """Encher a fila sem perda é INCIDENTE: precisa chamar quem conserta.

    Seguir em silêncio produziria um livro plausível e errado, que é
    exatamente o que a parte A do M2.2 existe para impedir.
    """
    perdidos: list[RecordEnvelope] = []
    writer = JsonlGzipWriter(
        output_dir=tmp_path,
        queue_max_book=2,
        ao_perder_book=perdidos.append,
    )
    for i in range(5):
        writer.submit(RecordEnvelope(i, i, "poly_ws", b"{}"), canal=CANAL_BOOK)

    assert len(perdidos) == 3
    assert writer.dropped_por_canal[CANAL_BOOK] == 3


async def test_lote_sai_ordenado_por_ts_mono(tmp_path):
    """Drenar duas filas não pode embaralhar a saída."""
    writer = JsonlGzipWriter(output_dir=tmp_path)
    writer.submit(RecordEnvelope(30, 30, "rtds", b'{"n":30}'))
    writer.submit(RecordEnvelope(10, 10, "poly_ws", b'{"n":10}'), canal=CANAL_BOOK)
    writer.submit(RecordEnvelope(20, 20, "rtds", b'{"n":20}'))
    await writer.start()
    await asyncio.sleep(0.05)
    await writer.stop()

    with gzip.open(min(tmp_path.glob("*.jsonl.gz")), "rb") as handle:
        ordem = [json.loads(linha)["ts_mono_ns"] for linha in handle if linha.strip()]
    assert ordem == [10, 20, 30]


# ═══════════════════════════════════ A.2 — validação cruzada do topo

BOOK = {
    "event_type": "book",
    "asset_id": "tok",
    "bids": [{"price": "0.49", "size": "100"}],
    "asks": [{"price": "0.51", "size": "100"}],
}


def _delta(preco: str, size: str, side: str, bid: str, ask: str) -> dict:
    return {
        "event_type": "price_change",
        "market": "0x1",
        "price_changes": [
            {
                "asset_id": "tok",
                "price": preco,
                "size": size,
                "side": side,
                "best_bid": bid,
                "best_ask": ask,
            }
        ],
    }


def test_topo_coerente_nao_gera_divergencia():
    monitor = MonitorDeIntegridade()
    monitor.observar(BOOK, 1)
    # novo melhor bid a 0.50, e o servidor confirma 0.50
    achados = monitor.observar(_delta("0.50", "10", "BUY", "0.50", "0.51"), 2)
    assert achados == []
    assert monitor.divergencias == 0
    assert monitor.comparacoes == 2


def test_delta_perdido_vira_divergencia():
    """O caso que o M2.2 existe para pegar: o livro reconstruído fica para trás.

    Aqui o delta que criaria o bid de 0.50 nunca chega; o próximo evento
    afirma que o topo é 0.50, e a reconstrução ainda diz 0.49.
    """
    monitor = MonitorDeIntegridade()
    monitor.observar(BOOK, 1)
    achados = monitor.observar(_delta("0.30", "10", "BUY", "0.50", "0.51"), 2)

    assert len(achados) == 1
    assert achados[0].lado == "bid"
    assert achados[0].servidor == 0.50
    assert achados[0].reconstruido == 0.49
    assert monitor.token_corrompido("tok") is True


def test_ruido_de_arredondamento_nao_invalida_janela():
    """Meio milésimo de diferença é string decimal, não perda de delta."""
    monitor = MonitorDeIntegridade(tolerancia=0.0005)
    monitor.observar(BOOK, 1)
    achados = monitor.observar(_delta("0.49", "100", "BUY", "0.4902", "0.51"), 2)
    assert achados == []


def test_sem_snapshot_inicial_nao_ha_alarme_falso():
    """Antes do `book` a reconstrução é incompleta por definição."""
    monitor = MonitorDeIntegridade()
    achados = monitor.observar(_delta("0.30", "10", "BUY", "0.50", "0.51"), 1)
    assert achados == []
    assert monitor.comparacoes == 0


def test_perda_conhecida_descarta_o_livro():
    """Fila cheia: melhor livro nenhum que livro plausível e errado."""
    monitor = MonitorDeIntegridade()
    monitor.observar(BOOK, 1)
    monitor.marcar_perda("tok")

    assert "tok" in monitor.aguardando_resync
    # e enquanto não chega snapshot novo, nada é comparado
    assert monitor.observar(_delta("0.30", "10", "BUY", "0.9", "0.95"), 2) == []
    # o snapshot novo reabilita
    monitor.observar(BOOK, 3)
    assert "tok" not in monitor.aguardando_resync


def test_monitor_conta_a_forma_do_price_change():
    """Qual formato o servidor usa é pergunta aberta — e vai ao relatório."""
    monitor = MonitorDeIntegridade()
    monitor.observar(BOOK, 1)
    monitor.observar(_delta("0.50", "10", "BUY", "0.50", "0.51"), 2)
    monitor.observar(
        {"event_type": "price_change", "asset_id": "tok", "changes": []}, 3
    )
    assert monitor.resumo()["formas_de_price_change"] == {
        "price_changes": 1,
        "changes": 1,
    }


def test_best_bid_ask_tambem_confere():
    monitor = MonitorDeIntegridade()
    monitor.observar(BOOK, 1)
    achados = monitor.observar(
        {
            "event_type": "best_bid_ask",
            "asset_id": "tok",
            "best_bid": "0.60",
            "best_ask": "0.61",
        },
        2,
    )
    assert {d.lado for d in achados} == {"bid", "ask"}


def test_parser_aceita_as_duas_formas_de_price_change():
    """A forma antiga era fixture sintética nossa; a nova é a do SDK oficial."""
    forma_a = {
        "event_type": "price_change",
        "asset_id": "t1",
        "changes": [{"price": "0.65", "side": "BUY", "size": "50"}],
    }
    forma_b = _delta("0.60", "5", "SELL", "0.59", "0.61")

    assert [m.asset_id for m in iter_mudancas(forma_a)] == ["t1"]
    assert [m.asset_id for m in iter_mudancas(forma_b)] == ["tok"]
    assert forma_do_price_change(forma_a) == "changes"
    assert forma_do_price_change(forma_b) == "price_changes"
    # a forma B carrega o topo autoritativo; a A, não
    assert next(iter(iter_mudancas(forma_b))).best_bid == 0.59
    assert next(iter(iter_mudancas(forma_a))).best_bid is None


def test_um_price_change_pode_tocar_varios_tokens():
    """Ler só o `asset_id` do topo perderia os demais deltas do lote."""
    evento = {
        "event_type": "price_change",
        "market": "0x1",
        "price_changes": [
            {"asset_id": "a", "price": "0.5", "size": "1", "side": "BUY"},
            {"asset_id": "b", "price": "0.5", "size": "1", "side": "SELL"},
        ],
    }
    assert tokens_do_evento(evento) == {"a", "b"}


def test_book_aplica_so_os_deltas_do_proprio_token():
    livro = OrderBook(asset_id="a", bids=[(0.4, 10.0)], asks=[(0.6, 10.0)])
    livro.apply_price_change(
        {
            "event_type": "price_change",
            "price_changes": [
                {"asset_id": "b", "price": "0.55", "size": "9", "side": "SELL"},
                {"asset_id": "a", "price": "0.58", "size": "7", "side": "SELL"},
            ],
        }
    )
    assert livro.best_ask == 0.58
    assert (0.55, 9.0) not in livro.asks


# ════════════════════════════════════════════════════ A.4 — relógio


def test_offset_de_relogio_mede_a_diferenca():
    monitor = MonitorDeRelogio()
    # carimbo do servidor 1000ms, chegada local 1250ms → offset 250ms
    monitor.observar(1000.0, 1_250_000_000)
    monitor.observar(2000.0, 2_250_000_000)
    resumo = monitor.resumo()
    assert resumo["amostras"] == 2
    assert resumo["p50_ms"] == 250.0


def test_relogio_ignora_carimbo_ausente():
    monitor = MonitorDeRelogio()
    monitor.observar(0.0, 1_000_000_000)
    assert monitor.resumo()["amostras"] == 0


def test_relogio_tem_teto_de_amostras():
    """72h de gravação não podem virar uma lista sem teto."""
    monitor = MonitorDeRelogio(max_amostras=10)
    for i in range(1, 1001):
        monitor.observar(float(i), i * 1_000_000 + 5_000_000)
    assert len(monitor.amostras) == 10
    assert monitor.resumo()["amostras"] == 1000


# ═══════════════════════════════════════════ A.6 — formato colunar


def test_conversao_colunar(tmp_path):
    pytest.importorskip("pyarrow", reason="extra de análise: pip install -e '.[analise]'")
    import pyarrow.parquet as pq

    from pulsearb.replay.columnar import converter

    origem = tmp_path / "rec.jsonl.gz"
    with gzip.open(origem, "wb") as handle:
        handle.write(
            orjson.dumps(
                {
                    "ts_mono_ns": 1,
                    "ts_wall_ns": 1_786_891_500_000_000_000,
                    "fonte": "poly_ws",
                    "payload": _delta("0.50", "10", "BUY", "0.50", "0.51"),
                }
            )
            + b"\n"
        )
        handle.write(
            orjson.dumps(
                {
                    "ts_mono_ns": 2,
                    "ts_wall_ns": 1_786_891_501_000_000_000,
                    "fonte": "rtds",
                    "payload": {
                        "topic": "crypto_prices",
                        "payload": {"symbol": "btcusdt", "value": 100.5},
                    },
                }
            )
            + b"\n"
        )

    resumo = converter(origem, tmp_path / "pq")
    assert resumo["linhas"] == 2

    tabela = pq.read_table(tmp_path / "pq")
    # `fonte` e `dia` vêm da partição, não da coluna — repetir a chave dentro
    # do arquivo quebra a leitura do diretório-raiz.
    assert "fonte" in tabela.column_names
    dados = tabela.to_pydict()
    assert set(dados["fonte"]) == {"poly_ws", "rtds"}
    assert 0.50 in [p for p in dados["price"] if p is not None]


# ═══════════════════════════════════════════ B.1 — score de rewards

PARAMS = ParametrosDeReward(
    daily_rate=86400.0,   # 1 USDC por segundo, para a conta sair redonda
    min_size=50.0,
    max_spread=0.03,
    tick_size=0.01,
    fator_desconto=0.5,
)


def test_score_cai_com_a_distancia_do_topo():
    no_topo = score_de_nivel(0.50, 100, melhor_preco=0.50, meio=0.50, params=PARAMS)
    um_tick = score_de_nivel(0.49, 100, melhor_preco=0.50, meio=0.50, params=PARAMS)
    dois = score_de_nivel(0.48, 100, melhor_preco=0.50, meio=0.50, params=PARAMS)
    assert no_topo == 100
    assert um_tick == 50
    assert dois == 25


def test_abaixo_do_min_size_nao_pontua():
    assert score_de_nivel(0.50, 49, melhor_preco=0.50, meio=0.50, params=PARAMS) == 0.0


def test_fora_do_max_spread_nao_pontua():
    """0.46 está a 0.04 do meio, além do max_spread de 0.03."""
    assert score_de_nivel(0.46, 100, melhor_preco=0.50, meio=0.50, params=PARAMS) == 0.0


def test_parametros_sem_pool_nao_viram_default_inventado():
    """Mercado sem `rewardsDailyRate` é mercado sem pool, não mercado padrão."""
    assert ParametrosDeReward.do_mercado({"rewards_max_spread": 1.5}) is None
    assert ParametrosDeReward.do_mercado({"rewards_daily_rate": 0}) is None


def test_max_spread_e_lido_como_centavos():
    """1.5 só faz sentido como 1,5¢; como fração seria 150% de spread."""
    params = ParametrosDeReward.do_mercado(
        {"rewards_daily_rate": 100, "rewards_max_spread": 1.5, "tick_size": 0.01}
    )
    assert params is not None
    assert params.max_spread == 0.015


def test_fatia_do_pool_e_pro_rata():
    assert fatia_do_pool(nosso_score=50, score_do_mercado=150) == 0.25
    assert fatia_do_pool(nosso_score=0, score_do_mercado=150) == 0.0


def test_ordem_hipotetica_pontua_dos_dois_lados():
    livro = OrderBook(
        asset_id="tok",
        bids=[(0.49, 500.0)],
        asks=[(0.51, 500.0)],
    )
    dois = score_da_ordem(
        OrdemHipotetica(tamanho=100, distancia_ticks=1, dois_lados=True), livro, PARAMS
    )
    um = score_da_ordem(
        OrdemHipotetica(tamanho=100, distancia_ticks=1, dois_lados=False), livro, PARAMS
    )
    assert dois == pytest.approx(2 * um)


def test_score_do_livro_soma_os_dois_lados():
    livro = OrderBook(asset_id="tok", bids=[(0.49, 100.0)], asks=[(0.51, 100.0)])
    assert score_do_livro(livro, PARAMS) == 200.0


def _janela_com_livro(slug: str = "j1") -> WindowState:
    timeline = BookTimeline()
    for segundo in range(10):
        timeline.append(
            OrderBook(
                asset_id="up",
                bids=[(0.49 - segundo * 0.0001, 500.0)],
                asks=[(0.51 + segundo * 0.0001, 500.0)],
            ),
            segundo * 1_000_000_000,
        )
    janela = WindowState(
        slug=slug,
        jogo="twap",
        asset="btc",
        duracao_s=300,
        condition_id="0x1",
        token_up="up",
        token_down="down",
        tick_size=0.01,
        min_order_size=5,
        fee_rate=0.07,
        fee_exponent=1.0,
        open_ts_ns=0,
        close_ts_ns=10_000_000_000,
    )
    janela.books["up"] = timeline
    janela.reward_meta = {
        "rewards_daily_rate": 86400.0,
        "rewards_min_size": 50,
        "rewards_max_spread": 3.0,
        "tick_size": 0.01,
    }
    return janela


def test_simulacao_produz_receita_e_varre_o_fator():
    saida = simular([_janela_com_livro()])
    assert saida["janelas_com_pool_de_reward"] == 1
    total = saida["por_ordem"]["50 shares @ 1 tick(s), 2 lados"]["total"]
    assert total["receita_usdc"] > 0
    assert 0 < total["fatia_media"] < 1

    # A varredura do fator é obrigatória: é ela que mostra o quanto a
    # conclusão depende de um parâmetro que NÃO foi verificado.
    sensibilidade = saida["sensibilidade_ao_fator"]["50 shares @ 1 tick(s), 2 lados"]
    assert set(sensibilidade) == {"0.3", "0.5", "0.7", "0.9"}
    assert sensibilidade["0.9"] > sensibilidade["0.3"]


def test_simulacao_avisa_que_a_formula_nao_foi_verificada():
    saida = simular([_janela_com_livro()])
    assert "NÃO FOI VERIFICADA" in saida["hipoteses"]["aviso"]


def test_selecao_de_mercado_reporta_orcamento_por_score():
    saida = simular([_janela_com_livro()])
    total = saida["selecao_de_mercado"]["total"]
    assert total["orcamento_por_unidade_de_score_p50"] > 0


# ═══════════════════════════════════════════════════ B.2 — markout


def _janela_com_trade(lado: str, mid_depois: float) -> WindowState:
    janela = _janela_com_livro()
    timeline = BookTimeline()
    # t=0: meio em 0.50; t=5s: meio em `mid_depois`
    timeline.append(
        OrderBook(asset_id="up", bids=[(0.49, 100.0)], asks=[(0.51, 100.0)]), 0
    )
    timeline.append(
        OrderBook(
            asset_id="up",
            bids=[(round(mid_depois - 0.01, 4), 100.0)],
            asks=[(round(mid_depois + 0.01, 4), 100.0)],
        ),
        5_000_000_000,
    )
    janela.books = {"up": timeline}
    janela.trades = [(0, 0.51 if lado == "BUY" else 0.49, 10.0, lado)]
    return janela


def test_markout_negativo_quando_o_preco_anda_contra_o_maker():
    """Taker COMPROU de nós; o preço subiu depois. Fomos atropelados."""
    resultado = medir_markout([_janela_com_trade("BUY", 0.55)], horizontes_s=(5.0,))
    media = resultado["markout_centavos_por_share"]["total"]["5s"]["media"]
    assert media < 0


def test_markout_positivo_quando_o_preco_anda_a_favor():
    """Taker COMPROU de nós; o preço caiu depois. Ganhamos o spread."""
    resultado = medir_markout([_janela_com_trade("BUY", 0.45)], horizontes_s=(5.0,))
    media = resultado["markout_centavos_por_share"]["total"]["5s"]["media"]
    assert media > 0


def test_markout_inverte_o_sinal_para_o_outro_lado():
    """Taker VENDEU para nós; preço subindo agora é a nosso favor."""
    resultado = medir_markout([_janela_com_trade("SELL", 0.55)], horizontes_s=(5.0,))
    media = resultado["markout_centavos_por_share"]["total"]["5s"]["media"]
    assert media > 0


def test_markout_descarta_execucao_fora_do_topo():
    janela = _janela_com_trade("BUY", 0.55)
    janela.trades = [(0, 0.80, 10.0, "BUY")]  # bem acima do melhor ask
    resultado = medir_markout([janela], horizontes_s=(5.0,))
    assert resultado["descartadas_fora_do_topo"] == 1
    assert resultado["markout_centavos_por_share"] == {}


# ═══════════════════════════════════════════ B.3 — a conta do maker


def test_conta_do_maker_junta_rewards_markout_e_rebate():
    janela = _janela_com_livro()
    rewards = simular([janela])
    markout = medir_markout([_janela_com_trade("BUY", 0.55)], horizontes_s=(5.0,))
    conta = conta_do_maker(rewards=rewards, markout=markout, fee_rebate_rate=0.2)

    chave = "50 shares @ 1 tick(s), 2 lados | total"
    assert chave in conta["por_ordem_e_recorte"]
    celula = conta["por_ordem_e_recorte"][chave]
    assert celula["rewards_usdc"] > 0
    assert celula["taxa_usdc"] == 0.0
    # A amostra que sustenta cada célula acompanha a célula.
    assert celula["horas_de_amostra"] > 0


def test_conta_do_maker_declara_o_que_falta_e_o_vies_da_fila():
    """B.4: a simulação maker é otimista por construção, e isso vai escrito."""
    conta = conta_do_maker(rewards={}, markout={}, fee_rebate_rate=0.2)
    assert "OTIMISTA" in conta["limitacao_de_fila"]
    assert any("fila" in item for item in conta["o_que_falta_para_fechar"])
