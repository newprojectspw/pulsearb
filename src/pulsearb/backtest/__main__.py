"""python -m pulsearb.backtest data/recordings --json relatorio.json

Monta as janelas a partir da gravação, roda o modelo, desconta tudo e imprime
o relatório completo do M2.D + as medições do M2.E.

Sem gravação não há relatório. O comando falha com mensagem clara em vez de
produzir números sobre um conjunto vazio — número de backtest sobre zero dado
é a forma mais fácil de se enganar.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from pulsearb.analysis.measurements import (
    medir_atraso_liquidacao,
    medir_mudanca_de_tick,
    medir_profundidade,
)
from pulsearb.backtest.book import OrderBook
from pulsearb.backtest.report import curva_de_edge_por_threshold
from pulsearb.backtest.runner import (
    BacktestConfig,
    BacktestRunner,
    BookTimeline,
    WindowState,
    sensibilidade_latencia,
    varredura_de_threshold,
)
from pulsearb.engine.anchor import (
    AnchorHypothesis,
    WindowOutcome,
    compute_anchor,
    evaluate_hypotheses,
    report_anchor_validation,
)
from pulsearb.feeds.rtds import TOPIC_TWAP_60, parse_rtds_event
from pulsearb.markets.discovery import parse_end_date_epoch
from pulsearb.replay.reader import RecordingReader, ReplayRecord

TOKEN_DURACAO_PADRAO = 300


def caminho_de_leitura(bruto: str) -> Path:
    """Valida um caminho de ENTRADA vindo da linha de comando.

    Resolve para caminho canônico e confirma que existe. Além de fechar o
    caminho para travessia de diretório (o valor vem de fora do programa),
    troca um traceback de `FileNotFoundError` lá na frente por um erro que
    diz o que está errado.
    """
    caminho = Path(bruto).expanduser().resolve(strict=False)
    if not caminho.exists():
        raise ValueError(f"gravação não encontrada: {caminho}")
    return caminho


def caminho_de_escrita(bruto: str) -> Path:
    """Valida um caminho de SAÍDA vindo da linha de comando.

    Exige diretório-pai existente e sufixo .json. Um relatório de backtest
    escrito em local inesperado é pior que um erro: some sem ninguém notar.
    """
    caminho = Path(bruto).expanduser().resolve(strict=False)
    if caminho.suffix != ".json":
        raise ValueError(f"o relatório precisa terminar em .json: {caminho}")
    if not caminho.parent.is_dir():
        raise ValueError(f"diretório de saída não existe: {caminho.parent}")
    if caminho.is_dir():
        raise ValueError(f"o destino é um diretório: {caminho}")
    return caminho


class RecordingIndex:
    """Varre a gravação uma vez e organiza tudo que o backtest precisa."""

    def __init__(self, reader: RecordingReader) -> None:
        self.reader = reader
        self.streams: dict[str, list[tuple[int, float]]] = defaultdict(list)
        self.books: dict[str, BookTimeline] = defaultdict(BookTimeline)
        self.book_atual: dict[str, OrderBook] = {}
        self.snapshots: list[dict[str, Any]] = []
        self.resolucoes: dict[str, int] = {}   # asset_id → ts_ns da resolução
        self.resolvido_up: dict[str, bool] = {}
        self.gaps: list[dict[str, Any]] = []

    def build(self) -> None:
        for record in self.reader.iter_records():
            if record.fonte == "gap" and isinstance(record.payload, dict):
                self.gaps.append(record.payload)
            elif record.fonte == "discovery_snapshot" and isinstance(record.payload, dict):
                self.snapshots.append(record.payload)
            elif record.fonte == "rtds":
                self._on_rtds(record)
            elif record.fonte == "poly_ws":
                self._on_poly(record)

    def _on_rtds(self, record: ReplayRecord) -> None:
        tick = parse_rtds_event(record.payload, record.ts_mono_ns, record.ts_wall_ns)
        # Preço-verdade das janelas TWAP é o twap_sixty; o spot entra para o
        # jogo horário via bookTicker/kline, tratado à parte.
        if tick is not None and tick.topic == TOPIC_TWAP_60:
            self.streams[tick.asset].append((record.ts_wall_ns, tick.price))

    def _on_poly(self, record: ReplayRecord) -> None:
        # O WS de mercado do CLOB entrega tanto um evento solto quanto um LOTE
        # em array. Tratar só o dict descartaria os lotes em silêncio — e é
        # justamente em rajada de atividade que eles aparecem.
        payload = record.payload
        if isinstance(payload, list):
            eventos: list[Any] = payload
        elif isinstance(payload, dict):
            eventos = [payload]
        else:
            return
        for evento in eventos:
            if not isinstance(evento, dict):
                continue
            tipo = evento.get("event_type")
            asset_id = evento.get("asset_id")
            if not isinstance(asset_id, str):
                continue
            if tipo == "book":
                book = OrderBook.from_event(evento)
                if book is not None:
                    self.book_atual[asset_id] = book
                    self.books[asset_id].append(book, record.ts_wall_ns)
            elif tipo == "price_change":
                atual = self.book_atual.get(asset_id)
                if atual is not None:
                    novo = atual.clone()
                    novo.apply_price_change(evento)
                    self.book_atual[asset_id] = novo
                    self.books[asset_id].append(novo, record.ts_wall_ns)
            elif tipo in ("market_resolved", "resolution"):
                self.resolucoes[asset_id] = record.ts_wall_ns
                vencedor = evento.get("winning_outcome") or evento.get("outcome")
                if isinstance(vencedor, str):
                    self.resolvido_up[asset_id] = vencedor.lower() == "up"

    # ------------------------------------------------------------- janelas
    def janelas(self) -> list[WindowState]:
        """Última visão de cada janela nos snapshots, virando WindowState."""
        por_slug: dict[str, dict[str, Any]] = {}
        for snapshot in self.snapshots:
            for janela in snapshot.get("janelas") or []:
                if isinstance(janela, dict) and isinstance(janela.get("slug"), str):
                    por_slug[janela["slug"]] = janela

        saida: list[WindowState] = []
        for slug, meta in por_slug.items():
            tokens = meta.get("token_id_by_outcome") or {}
            token_up, token_down = tokens.get("Up"), tokens.get("Down")
            if not isinstance(token_up, str) or not isinstance(token_down, str):
                continue
            fim_epoch = parse_end_date_epoch({"endDate": meta.get("end_date_iso")})
            if fim_epoch is None:
                continue
            resolucao = self.resolvido_up.get(token_up)
            duracao = _duracao_do_slug(slug)
            janela = WindowState(
                slug=slug,
                jogo="horario" if meta.get("resolution") == "binance_candle" else "twap",
                asset=str(meta.get("asset") or ""),
                duracao_s=duracao,
                condition_id=str(meta.get("condition_id") or ""),
                token_up=token_up,
                token_down=token_down,
                tick_size=float(meta.get("tick_size") or 0.01),
                min_order_size=float(meta.get("min_order_size") or 5),
                fee_rate=float(meta.get("fee_rate") or 0.0),
                fee_exponent=float(meta.get("fee_exponent") or 1.0),
                open_ts_ns=int((fim_epoch - duracao) * 1e9),
                close_ts_ns=int(fim_epoch * 1e9),
                resolveu_up=resolucao,
            )
            janela.books[token_up] = self.books.get(token_up, BookTimeline())
            janela.books[token_down] = self.books.get(token_down, BookTimeline())
            saida.append(janela)
        return saida


def _duracao_do_slug(slug: str) -> int:
    if "-up-or-down-" in slug:
        return 3600
    for sufixo, segundos in (("-5m-", 300), ("-15m-", 900), ("-1h-", 3600), ("-4h-", 14400)):
        if sufixo in slug:
            return segundos
    return TOKEN_DURACAO_PADRAO


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PULSEARB backtest — M2.D + M2.E")
    parser.add_argument("recordings", help="diretório (ou arquivo) da gravação")
    parser.add_argument("--threshold", type=float, default=0.02)
    parser.add_argument("--latencia-ms", type=float, default=300.0)
    parser.add_argument("--json", help="grava o relatório completo neste arquivo")
    args = parser.parse_args(argv)

    try:
        caminho = caminho_de_leitura(args.recordings)
        destino = caminho_de_escrita(args.json) if args.json else None
    except ValueError as erro:
        print(str(erro), file=sys.stderr)
        return 2

    reader = RecordingReader(caminho)
    index = RecordingIndex(reader)
    index.build()

    if not index.snapshots:
        print(
            "nenhum snapshot de descoberta na gravação — sem metadados de janela\n"
            "não há backtest possível. Rode o recorder primeiro:\n"
            "    python -m pulsearb.recorder --duration 72h",
            file=sys.stderr,
        )
        return 1

    janelas = index.janelas()
    resolvidas = [j for j in janelas if j.resolveu_up is not None]

    # Âncora: valida as hipóteses contra as resoluções REAIS antes de usar
    # qualquer uma delas no modelo.
    outcomes = [
        WindowOutcome(
            slug=j.slug,
            open_ts_ns=j.open_ts_ns,
            close_ts_ns=j.close_ts_ns,
            samples=tuple(index.streams.get(j.asset, [])),
            resolved_up=bool(j.resolveu_up),
        )
        for j in resolvidas
        if j.jogo == "twap"
    ]
    scores = evaluate_hypotheses(outcomes)
    validacao = report_anchor_validation(scores)

    # A âncora usada no backtest é a hipótese sobrevivente; havendo empate,
    # o default explícito (e o relatório diz que foi default).
    sobreviventes = [h for h, s in scores.items() if s.sobreviveu]
    escolhida = sobreviventes[0] if sobreviventes else AnchorHypothesis.ULTIMO_ANTES
    for janela in resolvidas:
        janela.ancora = compute_anchor(
            escolhida, index.streams.get(janela.asset, []), janela.open_ts_ns
        )

    runner = BacktestRunner(
        BacktestConfig(threshold_edge=args.threshold, latencia_ms=args.latencia_ms)
    )
    report = runner.run(resolvidas, index.streams)

    relatorio: dict[str, Any] = {
        "gravacao": {
            "arquivos": len(reader.files),
            "linhas_corrompidas": reader.corrompidas,
            "snapshots_de_descoberta": len(index.snapshots),
            "janelas_conhecidas": len(janelas),
            "janelas_com_resolucao": len(resolvidas),
            "gaps": index.gaps,
        },
        "ancora": {**validacao, "usada_no_backtest": escolhida.value},
        "backtest": report.to_dict(),
        "sensibilidade_latencia": sensibilidade_latencia(
            resolvidas, index.streams, threshold=args.threshold
        ),
        "curva_de_edge": curva_de_edge_por_threshold(
            varredura_de_threshold(
                resolvidas, index.streams, latencia_ms=args.latencia_ms
            )
        ),
        "medicoes": {
            "tick": medir_mudanca_de_tick(index.snapshots),
            "atraso_liquidacao": medir_atraso_liquidacao(
                [
                    {
                        "slug": j.slug,
                        "jogo": j.jogo,
                        "end_date_ns": j.close_ts_ns,
                        "resolution_ts_ns": index.resolucoes.get(j.token_up, 0),
                    }
                    for j in resolvidas
                ]
            ),
            "profundidade": medir_profundidade(
                [
                    {
                        "book": book,
                        "duracao_s": j.duracao_s,
                        "tick_size": j.tick_size,
                        "hora_utc": int((j.close_ts_ns / 1e9) // 3600 % 24),
                    }
                    for j in resolvidas
                    for timeline in [j.books.get(j.token_up)]
                    if timeline is not None
                    for book in timeline.books[:50]
                ]
            ),
        },
    }

    saida = json.dumps(relatorio, indent=2, ensure_ascii=False, default=str)
    print(saida)
    if destino is not None:
        destino.write_text(saida, encoding="utf-8")
        print(f"\nrelatório gravado em {destino}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
