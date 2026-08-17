"""Backtest de ponta a ponta sobre gravação sintética.

AVISO QUE VALE PARA O ARQUIVO INTEIRO: a gravação é sintética. Estes testes
provam que o PIPELINE funciona — replay, modelo, book, descontos, relatórios.
Não provam nada sobre existir edge no mercado real. O veredito do M2 depende
de gravação de produção (docs/VEREDITO_M2.md).
"""

from __future__ import annotations

import json

import pytest
from tests.synthetic import gerar_gravacao

from pulsearb.backtest.__main__ import RecordingIndex, main
from pulsearb.backtest.runner import (
    BacktestConfig,
    BacktestRunner,
    sensibilidade_latencia,
    varredura_de_threshold,
)
from pulsearb.engine.anchor import (
    AnchorHypothesis,
    WindowOutcome,
    compute_anchor,
    evaluate_hypotheses,
)
from pulsearb.replay.reader import RecordingReader


@pytest.fixture
def gravacao(tmp_path):
    diretorio = tmp_path / "rec"
    diretorio.mkdir()
    gerar_gravacao(diretorio / "rec.jsonl.gz", n_janelas=8)
    return diretorio


@pytest.fixture
def indexado(gravacao):
    index = RecordingIndex(RecordingReader(gravacao))
    index.build()
    return index


def test_index_monta_tudo(indexado):
    assert indexado.snapshots
    assert indexado.streams["btc"]
    assert indexado.books
    assert indexado.resolvido_up
    janelas = indexado.janelas()
    assert len(janelas) == 8
    assert all(j.jogo == "twap" for j in janelas)
    assert all(j.resolveu_up is not None for j in janelas)
    # fee lida do dado gravado, não constante no código
    assert all(j.fee_rate == 0.07 and j.fee_exponent == 1.0 for j in janelas)


def test_ancora_sintetica_e_reproduzida(indexado):
    """O gerador resolve por 'último antes da abertura'.

    O validador precisa confirmar essa hipótese e FALSIFICAR pelo menos uma
    concorrente — senão não está discriminando nada.
    """
    janelas = [j for j in indexado.janelas() if j.resolveu_up is not None]
    outcomes = [
        WindowOutcome(
            slug=j.slug,
            open_ts_ns=j.open_ts_ns,
            close_ts_ns=j.close_ts_ns,
            samples=tuple(indexado.streams["btc"]),
            resolved_up=bool(j.resolveu_up),
        )
        for j in janelas
    ]
    scores = evaluate_hypotheses(outcomes)
    assert scores[AnchorHypothesis.ULTIMO_ANTES].sobreviveu
    assert scores[AnchorHypothesis.ULTIMO_ANTES].erros == 0
    # o TWAP na abertura é uma âncora diferente e deve ser derrubado
    assert not scores[AnchorHypothesis.TWAP_NA_ABERTURA].sobreviveu


def test_backtest_completo_produz_relatorio(indexado):
    janelas = [j for j in indexado.janelas() if j.resolveu_up is not None]
    for janela in janelas:
        janela.ancora = compute_anchor(
            AnchorHypothesis.ULTIMO_ANTES, indexado.streams["btc"], janela.open_ts_ns
        )
    report = BacktestRunner(BacktestConfig(threshold_edge=0.02)).run(
        janelas, indexado.streams
    )
    saida = report.to_dict()

    assert saida["resumo"]["janelas_avaliadas"] == 8
    # calibração é medida em TODA previsão, não só nas negociadas
    assert sum(b["n"] for b in saida["calibracao"].values()) > len(report.trades)
    assert set(saida["calibracao"]) <= {">240s", "240-120s", "120-60s", "60-30s", "<30s"}
    for trade in report.trades:
        # toda entrada pagou taxa e atravessou o book real
        assert trade.fee_usdc > 0
        assert trade.custo_usdc > 0
        assert trade.shares >= 5.0  # mínimo do mercado


def test_threshold_alto_reduz_trades(indexado):
    janelas = [j for j in indexado.janelas() if j.resolveu_up is not None]
    for janela in janelas:
        janela.ancora = compute_anchor(
            AnchorHypothesis.ULTIMO_ANTES, indexado.streams["btc"], janela.open_ts_ns
        )
    varredura = varredura_de_threshold(
        janelas, indexado.streams, thresholds=(0.01, 0.30)
    )
    assert len(varredura[0.30].trades) <= len(varredura[0.01].trades)


def test_sensibilidade_de_latencia_roda_os_quatro_cenarios(indexado):
    janelas = [j for j in indexado.janelas() if j.resolveu_up is not None]
    for janela in janelas:
        janela.ancora = compute_anchor(
            AnchorHypothesis.ULTIMO_ANTES, indexado.streams["btc"], janela.open_ts_ns
        )
    tabela = sensibilidade_latencia(janelas, indexado.streams)
    assert set(tabela) == {"150ms", "300ms", "600ms", "1000ms"}


def test_cli_completo(gravacao, tmp_path, capsys):
    destino = tmp_path / "relatorio.json"
    assert main([str(gravacao), "--json", str(destino)]) == 0
    relatorio = json.loads(destino.read_text())
    for chave in ("gravacao", "ancora", "backtest", "sensibilidade_latencia",
                  "curva_de_edge", "medicoes"):
        assert chave in relatorio
    for medicao in ("tick", "atraso_liquidacao", "profundidade"):
        assert medicao in relatorio["medicoes"]


def test_indexador_aceita_lote_em_array(tmp_path):
    """O CLOB entrega tanto evento solto quanto LOTE em array.

    Tratar só o dict descartaria os lotes em silêncio — e é justamente em
    rajada de atividade que eles aparecem, ou seja, quando mais importam.
    """
    import gzip

    import orjson

    from pulsearb.replay.reader import RecordingReader

    def book(asset_id: str, ask: str) -> dict:
        return {
            "event_type": "book",
            "asset_id": asset_id,
            "timestamp": "1786891561000",
            "bids": [{"price": "0.40", "size": "100"}],
            "asks": [{"price": ask, "size": "100"}],
        }

    caminho = tmp_path / "rec.jsonl.gz"
    with gzip.open(caminho, "wb") as handle:
        # um lote em array e um evento solto
        for payload in ([book("tokA", "0.60"), book("tokB", "0.70")], book("tokC", "0.80")):
            handle.write(
                orjson.dumps(
                    {"ts_mono_ns": 1, "ts_wall_ns": 1, "fonte": "poly_ws", "payload": payload}
                )
                + b"\n"
            )

    index = RecordingIndex(RecordingReader(caminho))
    index.build()
    assert set(index.book_atual) == {"tokA", "tokB", "tokC"}
    assert index.book_atual["tokA"].best_ask == 0.60
    assert index.book_atual["tokB"].best_ask == 0.70


def test_clone_do_book_e_independente():
    """price_change não pode mutar o snapshot anterior da timeline."""
    from pulsearb.backtest.book import OrderBook

    original = OrderBook.from_event(
        {
            "event_type": "book",
            "asset_id": "t",
            "timestamp": "1",
            "bids": [{"price": "0.40", "size": "100"}],
            "asks": [{"price": "0.60", "size": "100"}],
        }
    )
    copia = original.clone()
    copia.apply_price_change(
        {"timestamp": "2", "changes": [{"price": "0.60", "size": "0", "side": "SELL"}]}
    )
    assert copia.best_ask is None
    assert original.best_ask == 0.60  # o histórico não foi corrompido


def test_cli_recusa_gravacao_inexistente(tmp_path):
    assert main([str(tmp_path / "nao-existe")]) == 2


def test_validacao_de_caminhos(tmp_path, gravacao):
    """Caminhos vindos da CLI são resolvidos e validados antes de qualquer I/O."""
    from pulsearb.backtest.__main__ import caminho_de_escrita, caminho_de_leitura

    assert caminho_de_leitura(str(gravacao)).is_absolute()
    with pytest.raises(ValueError, match="não encontrada"):
        caminho_de_leitura(str(tmp_path / "nada"))

    ok = caminho_de_escrita(str(tmp_path / "rel.json"))
    assert ok.is_absolute() and ok.suffix == ".json"
    with pytest.raises(ValueError, match=r"\.json"):
        caminho_de_escrita(str(tmp_path / "rel.txt"))
    with pytest.raises(ValueError, match="não existe"):
        caminho_de_escrita(str(tmp_path / "sem" / "esse" / "dir" / "rel.json"))
    # destino que existe e É um diretório (com sufixo .json, para passar da
    # checagem anterior e chegar nesta)
    (tmp_path / "engano.json").mkdir()
    with pytest.raises(ValueError, match="é um diretório"):
        caminho_de_escrita(str(tmp_path / "engano.json"))


def test_cli_recusa_saida_invalida(gravacao, tmp_path):
    assert main([str(gravacao), "--json", str(tmp_path / "x.txt")]) == 2


def test_cli_recusa_gravacao_sem_snapshot(tmp_path, capsys):
    """Sem metadados de janela não há backtest — e o comando diz isso."""
    import gzip

    import orjson

    vazio = tmp_path / "vazio"
    vazio.mkdir()
    with gzip.open(vazio / "rec.jsonl.gz", "wb") as handle:
        handle.write(
            orjson.dumps(
                {"ts_mono_ns": 1, "ts_wall_ns": 1, "fonte": "rtds", "payload": {}}
            )
            + b"\n"
        )
    assert main([str(vazio)]) == 1
    assert "recorder" in capsys.readouterr().err


def test_medicao_de_tick_detecta_o_afinamento(indexado):
    from pulsearb.analysis.measurements import medir_mudanca_de_tick

    medicao = medir_mudanca_de_tick(indexado.snapshots)
    # O gerador afina o tick nos últimos 60s de cada janela.
    assert medicao["afinamentos"] == 8
    assert "0.001" in medicao["distribuicao_de_tick"]
    assert medicao["seconds_left_no_afinamento"]["max"] <= 60


def test_medicao_de_atraso_de_liquidacao(indexado):
    from pulsearb.analysis.measurements import medir_atraso_liquidacao

    janelas = [j for j in indexado.janelas() if j.resolveu_up is not None]
    medicao = medir_atraso_liquidacao(
        [
            {
                "slug": j.slug,
                "jogo": j.jogo,
                "end_date_ns": j.close_ts_ns,
                "resolution_ts_ns": indexado.resolucoes.get(j.token_up, 0),
            }
            for j in janelas
        ]
    )
    # O gerador resolve 90s após o fim da janela.
    assert medicao["por_jogo"]["twap"]["p50"] == pytest.approx(90.0)
    # Sem janelas horárias na gravação sintética, a comparação é honesta:
    assert "insuficiente" in medicao["comparacao"]
