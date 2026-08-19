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
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from pulsearb.analysis.integrity import MonitorDeIntegridade, MonitorDeRelogio
from pulsearb.analysis.measurements import (
    conta_do_maker,
    medir_atraso_liquidacao,
    medir_markout,
    medir_mudanca_de_tick,
    medir_profundidade,
)
from pulsearb.analysis.rewards import simular as simular_rewards
from pulsearb.backtest.book import OrderBook
from pulsearb.backtest.report import curva_de_edge_por_threshold
from pulsearb.backtest.runner import (
    LIMITE_SNAPSHOTS_PADRAO,
    NIVEIS_RETIDOS_PADRAO,
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
from pulsearb.feeds.poly_ws import (
    EVENT_BOOK,
    EVENT_LAST_TRADE,
    EVENT_PRICE_CHANGE,
    RESOLUTION_EVENT_TYPES,
)
from pulsearb.feeds.rtds import TOPIC_TWAP_60, parse_rtds_event
from pulsearb.markets.discovery import parse_end_date_epoch
from pulsearb.recorder.writer import FONTE_RESOLUCAO_SINTETICA
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


# Quanto tempo ANTES da abertura da janela o book do token ainda interessa.
# `BookTimeline.at(t)` devolve o último snapshot ≤ t, então a primeira
# consulta da janela precisa de um snapshot anterior a ela. O recorder assina
# o token quando a descoberta o encontra, alguns minutos antes; 10 minutos de
# pré-rolo cobrem isso com folga e mantêm a retenção limitada.
PRE_ROLO_S = 600
# E depois do fechamento: a penalidade de latência consulta `t + latência`,
# no máximo 1s à frente (LATENCIAS_MS_PADRAO). 5s de folga.
POS_ROLO_S = 5


class RecordingIndex:
    """Duas passadas sobre a gravação, com memória limitada por construção.

    Por que DUAS passadas, e não uma:

    A passada 1 lê só o que é leve — snapshots de descoberta, ticks do RTDS,
    resoluções, lacunas. Ela é o que define QUAIS tokens existem e em que
    intervalo de tempo cada um importa. Sem essa informação, a passada única
    da versão anterior era obrigada a guardar o book de TODO token em TODO
    instante da gravação, inclusive de janelas que já tinham fechado horas
    antes e nunca seriam avaliadas.

    A passada 2 reconstrói os books, mas só dos tokens conhecidos e só dentro
    do intervalo `[abertura − PRE_ROLO, fechamento + POS_ROLO]`. Reler o
    arquivo custa I/O e descompressão; guardar 12 milhões de books custa a
    máquina inteira. O I/O é o lado barato dessa troca.

    A memória fica limitada por `tokens_de_interesse × limite_por_token`, um
    número que dá para calcular antes de rodar — e que o relatório imprime.
    """

    def __init__(
        self,
        reader: RecordingReader,
        *,
        limite_por_token: int = LIMITE_SNAPSHOTS_PADRAO,
        niveis_retidos: int = NIVEIS_RETIDOS_PADRAO,
    ) -> None:
        self.reader = reader
        self.limite_por_token = limite_por_token
        self.niveis_retidos = niveis_retidos
        self.streams: dict[str, list[tuple[int, float]]] = defaultdict(list)
        self.books: dict[str, BookTimeline] = {}
        self.book_atual: dict[str, OrderBook] = {}
        self.snapshots: list[dict[str, Any]] = []   # compactado: ver _on_discovery
        self.n_snapshots = 0
        self.ticks_vistos: Counter[str] = Counter()
        self.janelas_por_slug: dict[str, dict[str, Any]] = {}
        self.resolucoes: dict[str, int] = {}   # asset_id → ts_ns da resolução
        self.resolvido_up: dict[str, bool] = {}
        self.gaps: list[dict[str, Any]] = []
        self.janelas_de_interesse: dict[str, tuple[int, int]] = {}  # token → (ini, fim)
        self._ultimo_tick: dict[str, float] = {}
        # M2.2 A.2/A.4: o MESMO monitor que roda ao vivo no recorder, agora
        # sobre a gravação. Se os dois discordarem, o que se perdeu foi entre
        # o fio e o disco.
        self.integridade = MonitorDeIntegridade()
        self.relogio = MonitorDeRelogio()
        # M2.2 B.2: execuções observadas, por token.
        self.trades: dict[str, list[tuple[int, float, float, str]]] = defaultdict(list)

    # --------------------------------------------------------------- passadas
    def build(self) -> None:
        self._primeira_passada()
        self._marcar_tokens_de_interesse()
        self._segunda_passada()

    def _primeira_passada(self) -> None:
        """Metadados, preço-verdade e resoluções. Ignora o book por completo."""
        for record in self.reader.iter_records():
            if record.fonte == "gap" and isinstance(record.payload, dict):
                self.gaps.append(record.payload)
            elif record.fonte == "discovery_snapshot" and isinstance(record.payload, dict):
                self._on_discovery(record.payload)
            elif record.fonte == "rtds":
                self._on_rtds(record)
            elif record.fonte in ("poly_ws", FONTE_RESOLUCAO_SINTETICA):
                self._on_poly_meta(record)

    def _segunda_passada(self) -> None:
        """Reconstrói os books dos tokens de interesse, dentro da janela deles."""
        if not self.janelas_de_interesse:
            return
        for record in self.reader.iter_records():
            if record.fonte == "poly_ws":
                self._on_poly_book(record)

    # ------------------------------------------------------------- passada 1
    def _on_discovery(self, payload: dict[str, Any]) -> None:
        """Guarda a última visão de cada slug + só as MUDANÇAS de tick.

        A descoberta roda a cada 30s com ~100 janelas: 72h de gravação dariam
        ~900 mil dicts de janela retidos à toa. A medição de tick (M2.E.1) só
        olha transições, então guardar repetição idêntica não acrescenta nada
        — mas a DISTRIBUIÇÃO de tick conta observações, e essa vai à parte,
        no `ticks_vistos`, para não ser falseada pela compactação.
        """
        self.n_snapshots += 1
        janelas = payload.get("janelas")
        if not isinstance(janelas, list):
            return
        mudaram: list[dict[str, Any]] = []
        for janela in janelas:
            if not isinstance(janela, dict):
                continue
            slug = janela.get("slug")
            if not isinstance(slug, str):
                continue
            self.janelas_por_slug[slug] = janela
            tick = janela.get("tick_size")
            if not isinstance(tick, (int, float)) or isinstance(tick, bool):
                continue
            self.ticks_vistos[f"{float(tick):g}"] += 1
            if self._ultimo_tick.get(slug) != float(tick):
                self._ultimo_tick[slug] = float(tick)
                mudaram.append(janela)
        if mudaram:
            self.snapshots.append({"janelas": mudaram})

    def _on_rtds(self, record: ReplayRecord) -> None:
        tick = parse_rtds_event(record.payload, record.ts_mono_ns, record.ts_wall_ns)
        # Preço-verdade das janelas TWAP é o twap_sixty; o spot entra para o
        # jogo horário via bookTicker/kline, tratado à parte.
        if tick is not None and tick.topic == TOPIC_TWAP_60:
            self.streams[tick.asset].append((record.ts_wall_ns, tick.price))

    def _on_poly_meta(self, record: ReplayRecord) -> None:
        """Resoluções + integridade. O livro pesado fica para a passada 2.

        A integridade roda AQUI, e não na passada 2, porque ela precisa ver
        TODOS os tokens e TODO o período — inclusive os que a passada 2
        descarta por não pertencerem a janela nenhuma. Um livro corrompido
        num token fora de janela ainda é sinal de que a gravação teve perda.
        """
        for evento in _eventos_do_payload(record.payload):
            carimbo = _numero_bruto(evento.get("timestamp"))
            if carimbo:
                self.relogio.observar(carimbo, record.ts_wall_ns)
            self.integridade.observar(evento, record.ts_wall_ns)
            tipo = evento.get("event_type")
            if tipo not in RESOLUTION_EVENT_TYPES:
                continue
            asset_id = evento.get("asset_id")
            if not isinstance(asset_id, str):
                continue
            self.resolucoes[asset_id] = record.ts_wall_ns
            vencedor = evento.get("winning_outcome") or evento.get("outcome")
            if isinstance(vencedor, str):
                self.resolvido_up[asset_id] = vencedor.lower() == "up"

    def _marcar_tokens_de_interesse(self) -> None:
        for slug, meta in self.janelas_por_slug.items():
            fim_epoch = parse_end_date_epoch({"endDate": meta.get("end_date_iso")})
            if fim_epoch is None:
                continue
            duracao = _duracao_do_slug(slug)
            inicio_ns = int((fim_epoch - duracao - PRE_ROLO_S) * 1e9)
            fim_ns = int((fim_epoch + POS_ROLO_S) * 1e9)
            tokens = meta.get("token_id_by_outcome") or {}
            for token in (tokens.get("Up"), tokens.get("Down")):
                if isinstance(token, str):
                    self.janelas_de_interesse[token] = (inicio_ns, fim_ns)

    # ------------------------------------------------------------- passada 2
    def _on_poly_book(self, record: ReplayRecord) -> None:
        # O WS de mercado do CLOB entrega tanto um evento solto quanto um LOTE
        # em array. Tratar só o dict descartaria os lotes em silêncio — e é
        # justamente em rajada de atividade que eles aparecem.
        for evento in _eventos_do_payload(record.payload):
            tipo = evento.get("event_type")
            if tipo == EVENT_LAST_TRADE:
                self._on_trade(evento, record.ts_wall_ns)
                continue
            if tipo not in (EVENT_BOOK, EVENT_PRICE_CHANGE):
                continue
            asset_id = evento.get("asset_id")
            if not isinstance(asset_id, str):
                continue
            intervalo = self.janelas_de_interesse.get(asset_id)
            if intervalo is None or not (intervalo[0] <= record.ts_wall_ns <= intervalo[1]):
                continue
            if tipo == EVENT_BOOK:
                book = OrderBook.from_event(evento)
                if book is None:
                    continue
                self.book_atual[asset_id] = book
            else:
                book = self.book_atual.get(asset_id)
                if book is None:
                    continue
                # Mutação no lugar: o clone por evento existia só para
                # alimentar a timeline, e a timeline agora faz a própria
                # cópia (truncada) quando de fato retém o snapshot.
                book.apply_price_change(evento)
            self._timeline(asset_id).append(book, record.ts_wall_ns)

    def _on_trade(self, evento: dict[str, Any], ts_ns: int) -> None:
        """Execução observada no topo — a matéria-prima do markout (B.2)."""
        asset_id = evento.get("asset_id")
        if not isinstance(asset_id, str) or asset_id not in self.janelas_de_interesse:
            return
        preco = _numero_bruto(evento.get("price"))
        if preco is None:
            return
        self.trades[asset_id].append(
            (
                ts_ns,
                preco,
                _numero_bruto(evento.get("size")) or 0.0,
                str(evento.get("side", "")).upper(),
            )
        )

    def _timeline(self, asset_id: str) -> BookTimeline:
        timeline = self.books.get(asset_id)
        if timeline is None:
            timeline = BookTimeline(
                limite=self.limite_por_token, niveis=self.niveis_retidos
            )
            self.books[asset_id] = timeline
        return timeline

    # ---------------------------------------------------------------- memória
    def uso_de_memoria(self) -> dict[str, Any]:
        """O que foi retido e o que foi descartado — o relatório precisa dizer."""
        retidos = sum(len(t.ts) for t in self.books.values())
        descartados = sum(t.descartados for t in self.books.values())
        raleados = [t for t in self.books.values() if t.raleamentos]
        resolucoes_ms = sorted(
            t.resolucao_ns / 1e6 for t in self.books.values() if t.resolucao_ns
        )
        return {
            "tokens_de_interesse": len(self.janelas_de_interesse),
            "tokens_com_book": len(self.books),
            "snapshots_retidos": retidos,
            "snapshots_descartados": descartados,
            "limite_por_token": self.limite_por_token,
            "niveis_retidos_por_lado": self.niveis_retidos,
            "tokens_raleados": len(raleados),
            "pior_resolucao_ms": round(resolucoes_ms[-1], 1) if resolucoes_ms else 0.0,
            "nota": (
                "Books truncados aos N níveis do topo e raleados ao estourar o "
                "limite por token. `pior_resolucao_ms` acima de 150 significa que "
                "o cenário de latência mais baixo já não é distinguível."
            ),
        }

    # ------------------------------------------------------------------ janelas
    def janelas(self) -> list[WindowState]:
        """Última visão de cada janela nos snapshots, virando WindowState."""
        saida: list[WindowState] = []
        for slug, meta in self.janelas_por_slug.items():
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
                fee_rebate_rate=float(meta.get("fee_rebate_rate") or 0.0),
                open_ts_ns=int((fim_epoch - duracao) * 1e9),
                close_ts_ns=int(fim_epoch * 1e9),
                resolveu_up=resolucao,
            )
            janela.books[token_up] = self.books.get(token_up, BookTimeline())
            janela.books[token_down] = self.books.get(token_down, BookTimeline())
            janela.reward_meta = {
                "rewards_daily_rate": meta.get("rewards_daily_rate"),
                "rewards_min_size": meta.get("rewards_min_size"),
                "rewards_max_spread": meta.get("rewards_max_spread"),
                "tick_size": meta.get("tick_size"),
            }
            janela.trades = sorted(
                self.trades.get(token_up, []) + self.trades.get(token_down, [])
            )
            saida.append(janela)
        return saida


def _numero_bruto(valor: Any) -> float | None:
    """O CLOB manda número ora como int, ora como string decimal."""
    if isinstance(valor, bool) or valor is None:
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    if isinstance(valor, str):
        try:
            return float(valor)
        except ValueError:
            return None
    return None


def _eventos_do_payload(payload: Any) -> list[dict[str, Any]]:
    """O CLOB manda ora um evento solto, ora um lote em array."""
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return [e for e in payload if isinstance(e, dict)]
    return []


def _rebate_medio(janelas: list[WindowState]) -> float:
    """Taxa de rebate do maker, lida do dado gravado — nunca constante.

    Sem janela com o campo, devolve 0: melhor uma conta que não credita
    rebate nenhum do que uma que credita um número inventado.
    """
    taxas = [j.fee_rebate_rate for j in janelas if getattr(j, "fee_rebate_rate", None)]
    return sum(taxas) / len(taxas) if taxas else 0.0


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
    parser.add_argument(
        "--limite-snapshots",
        type=int,
        default=LIMITE_SNAPSHOTS_PADRAO,
        help="teto de snapshots de book por token (memória; ver BookTimeline)",
    )
    parser.add_argument(
        "--niveis-book",
        type=int,
        default=NIVEIS_RETIDOS_PADRAO,
        help="níveis do topo retidos por lado em cada snapshot",
    )
    args = parser.parse_args(argv)

    try:
        caminho = caminho_de_leitura(args.recordings)
        destino = caminho_de_escrita(args.json) if args.json else None
    except ValueError as erro:
        print(str(erro), file=sys.stderr)
        return 2

    reader = RecordingReader(caminho)
    index = RecordingIndex(
        reader,
        limite_por_token=max(2, args.limite_snapshots),
        niveis_retidos=max(1, args.niveis_book),
    )
    index.build()

    if not index.n_snapshots:
        print(
            "nenhum snapshot de descoberta na gravação — sem metadados de janela\n"
            "não há backtest possível. Rode o recorder primeiro:\n"
            "    python -m pulsearb.recorder --duration 72h",
            file=sys.stderr,
        )
        return 1

    janelas = index.janelas()
    # A.2: janela cujo livro divergiu do que o servidor afirmou é descartada.
    # O backtest continua rodando sobre as demais, e o relatório diz quantas
    # e quais saíram — silenciar seria o mesmo que confiar no livro furado.
    integras = [
        j
        for j in janelas
        if not index.integridade.token_corrompido(j.token_up)
        and not index.integridade.token_corrompido(j.token_down)
    ]
    resolvidas = [j for j in integras if j.resolveu_up is not None]

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
            "snapshots_de_descoberta": index.n_snapshots,
            "janelas_conhecidas": len(janelas),
            "janelas_com_resolucao": len(resolvidas),
            "gaps": index.gaps,
            "memoria": index.uso_de_memoria(),
        },
        "integridade": {
            "divergencia_topo_book": index.integridade.resumo(),
            "offset_relogio_ms": index.relogio.resumo(),
            "janelas_invalidadas": sorted(
                j.slug
                for j in janelas
                if index.integridade.token_corrompido(j.token_up)
                or index.integridade.token_corrompido(j.token_down)
            ),
            "nota": (
                "Divergência entre o topo que o servidor afirma e o topo que "
                "reconstruímos. Acima do limiar, a janela sai do backtest: "
                "número calculado sobre livro furado é pior que número "
                "nenhum, porque parece bom."
            ),
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
            "tick": medir_mudanca_de_tick(
                index.snapshots, distribuicao_de_tick=dict(index.ticks_vistos)
            ),
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
            "markout": medir_markout(resolvidas),
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

    # ─── rota maker (M2.2 parte B): medição, nunca implementação ───
    rewards = simular_rewards(resolvidas)
    relatorio["rota_maker"] = {
        "rewards": rewards,
        "markout": relatorio["medicoes"]["markout"],
        "conta_fechada": conta_do_maker(
            rewards=rewards,
            markout=relatorio["medicoes"]["markout"],
            fee_rebate_rate=_rebate_medio(resolvidas),
        ),
        "aviso": (
            "NADA aqui envia ordem. É simulação sobre gravação. A fórmula de "
            "score NÃO foi verificada contra a documentação oficial — ver "
            "`rota_maker.rewards.hipoteses` e docs/API_NOTES.md 15."
        ),
    }

    saida = json.dumps(relatorio, indent=2, ensure_ascii=False, default=str)
    print(saida)
    if destino is not None:
        destino.write_text(saida, encoding="utf-8")
        print(f"\nrelatório gravado em {destino}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
