"""Motor do backtest: replay → modelo → book → descontos → PnL.

Ordem de operações por sinal, sem atalho:

1. modelo estima `prob_up` com o dado disponível ATÉ aquele instante
2. aplica-se a **penalidade de latência**: o fill acontece com o book de
   `t + latencia`, não com o book que gerou o sinal. É aqui que a maior parte
   do edge teórico costuma morrer.
3. preenchimento contra o book real daquele instante, atravessando níveis
4. taxa calculada com r/e **lidos do mercado gravado**
5. PnL = payout − custo − taxa

Nada de "assumir que preencheu ao melhor preço". Nada de fee constante.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field
from typing import Any

from pulsearb.backtest.book import OrderBook, simulate_taker_buy
from pulsearb.backtest.report import BacktestReport, Trade
from pulsearb.engine.fees import fee_pp_por_share
from pulsearb.engine.hourly import prob_up_hourly
from pulsearb.engine.twap import RealizedVol, TwapTracker, prob_up_twap

# Defaults do M2.D. Nenhum é constante de mercado: todos são parâmetros de
# cenário, e a sensibilidade é reportada.
LATENCIAS_MS_PADRAO = (150.0, 300.0, 600.0, 1000.0)
LATENCIA_PADRAO_MS = 300.0
THRESHOLDS_PADRAO = (0.01, 0.02, 0.03, 0.05, 0.08, 0.12)


@dataclass(slots=True)
class BookTimeline:
    """Histórico de snapshots do book de um token, para consulta por tempo.

    A penalidade de latência precisa perguntar "como estava o book 300ms
    depois do sinal?" — então os snapshots ficam indexados por timestamp.
    """

    ts: list[int] = field(default_factory=list)
    books: list[OrderBook] = field(default_factory=list)

    def append(self, book: OrderBook, ts_ns: int) -> None:
        self.ts.append(ts_ns)
        self.books.append(book)

    def at(self, ts_ns: int) -> OrderBook | None:
        """Último snapshot com ts ≤ ts_ns. None se não havia book ainda."""
        if not self.ts:
            return None
        idx = bisect_left(self.ts, ts_ns)
        if idx < len(self.ts) and self.ts[idx] == ts_ns:
            return self.books[idx]
        return self.books[idx - 1] if idx > 0 else None


@dataclass(slots=True)
class WindowState:
    """Tudo que o backtest sabe sobre uma janela, montado do dado gravado."""

    slug: str
    jogo: str                # "twap" | "horario"
    asset: str
    duracao_s: int
    condition_id: str
    token_up: str
    token_down: str
    tick_size: float
    min_order_size: float
    fee_rate: float
    fee_exponent: float
    open_ts_ns: int
    close_ts_ns: int
    ancora: float | None = None
    resolveu_up: bool | None = None
    books: dict[str, BookTimeline] = field(default_factory=dict)


@dataclass
class BacktestConfig:
    """Parâmetros de cenário. Pessimista por default."""

    threshold_edge: float = 0.02
    latencia_ms: float = LATENCIA_PADRAO_MS
    shares_por_trade: float = 5.0     # o mínimo do mercado (API_NOTES 12.5)
    buffer_slippage: float = 0.0      # já modelado pelo book; fica em 0
    exigir_fill_completo: bool = True  # FOK: parcial não conta (M4)
    exigir_vol_calibrada: bool = True


def edge_liquido(
    *, prob: float, preco: float, fee_rate: float, fee_exponent: float, buffer: float = 0.0
) -> float:
    """edge = prob − preço − taxa_por_share − buffer.

    Todos os termos em pontos de probabilidade (= valor por share), que é a
    única unidade em que a soma faz sentido. A conversão para fração do
    capital fica com quem dimensiona a posição.
    """
    fee = fee_pp_por_share(preco, rate=fee_rate, exponent=fee_exponent)
    return prob - preco - fee - buffer


class BacktestRunner:
    """Roda um cenário sobre um conjunto de janelas já montadas."""

    def __init__(self, config: BacktestConfig | None = None) -> None:
        self.config = config or BacktestConfig()

    def run(
        self,
        janelas: list[WindowState],
        streams: dict[str, list[tuple[int, float]]],
    ) -> BacktestReport:
        """`streams` mapeia ativo → série (ts_ns, preço) do preço-verdade."""
        report = BacktestReport()
        for janela in janelas:
            if janela.resolveu_up is None or janela.ancora is None:
                continue
            report.janelas_avaliadas += 1
            self._run_window(janela, streams.get(janela.asset, []), report)
        return report

    # ------------------------------------------------------------- interno
    def _run_window(
        self,
        janela: WindowState,
        stream: list[tuple[int, float]],
        report: BacktestReport,
    ) -> None:
        cfg = self.config
        vol = RealizedVol()
        twap = TwapTracker()
        latencia_ns = int(cfg.latencia_ms * 1e6)
        ja_operou = False

        for ts_ns, preco_spot in stream:
            if ts_ns < janela.open_ts_ns or ts_ns > janela.close_ts_ns:
                continue
            vol.update(preco_spot, ts_ns)
            twap.update(preco_spot, ts_ns)
            seconds_left = (janela.close_ts_ns - ts_ns) / 1e9
            if seconds_left <= 0:
                continue

            if janela.jogo == "twap":
                locked_mean, locked_weight = twap.locked_mean_and_weight(seconds_left)
                est = prob_up_twap(
                    ancora=janela.ancora,
                    spot=preco_spot,
                    seconds_left=seconds_left,
                    sigma_1s=vol.sigma_1s,
                    locked_mean=locked_mean,
                    locked_weight=locked_weight,
                    twap_atual=twap.current_twap,
                    vol_ready=vol.ready,
                )
            else:
                est = prob_up_hourly(
                    open_price=janela.ancora,
                    spot=preco_spot,
                    seconds_left=seconds_left,
                    sigma_1s=vol.sigma_1s,
                    vol_ready=vol.ready,
                )

            # Calibração: medida em TODA previsão, não só onde se operou.
            report.add_calibration(est.bucket_tempo, est.prob_up, janela.resolveu_up)

            if ja_operou or (cfg.exigir_vol_calibrada and not est.confiavel):
                continue

            trade = self._tentar_entrada(janela, est, ts_ns, latencia_ns, report)
            if trade is not None:
                report.add_trade(trade)
                # v1 segura até a resolução: uma entrada por janela.
                ja_operou = True

    def _tentar_entrada(
        self,
        janela: WindowState,
        est: Any,
        ts_ns: int,
        latencia_ns: int,
        report: BacktestReport,
    ) -> Trade | None:
        cfg = self.config
        # Os dois lados: comprar Up a P(Up), ou Down a 1−P(Up).
        candidatos = (
            (True, janela.token_up, est.prob_up),
            (False, janela.token_down, 1.0 - est.prob_up),
        )
        for lado_up, token, prob in candidatos:
            timeline = janela.books.get(token)
            if timeline is None:
                continue
            book_sinal = timeline.at(ts_ns)
            if book_sinal is None or book_sinal.best_ask is None:
                continue

            edge = edge_liquido(
                prob=prob,
                preco=book_sinal.best_ask,
                fee_rate=janela.fee_rate,
                fee_exponent=janela.fee_exponent,
                buffer=cfg.buffer_slippage,
            )
            if edge < cfg.threshold_edge:
                continue

            report.sinais_gerados += 1

            # PENALIDADE DE LATÊNCIA: o fill usa o book de t + latência.
            book_fill = timeline.at(ts_ns + latencia_ns)
            if book_fill is None or not book_fill.asks:
                report.sinais_sem_book += 1
                continue
            if cfg.shares_por_trade < janela.min_order_size:
                report.sinais_abaixo_do_minimo += 1
                continue

            fill = simulate_taker_buy(book_fill, cfg.shares_por_trade)
            if not fill.preenchido or (cfg.exigir_fill_completo and not fill.completo):
                report.sinais_nao_preenchiveis += 1
                continue

            fee = fill.shares * fee_pp_por_share(
                fill.preco_medio, rate=janela.fee_rate, exponent=janela.fee_exponent
            )
            return Trade(
                slug=janela.slug,
                jogo=janela.jogo,
                asset=janela.asset,
                duracao_s=janela.duracao_s,
                bucket_tempo=est.bucket_tempo,
                prob_prevista=prob,
                preco_pago=fill.preco_medio,
                shares=fill.shares,
                custo_usdc=fill.custo_usdc,
                fee_usdc=fee,
                latencia_ms=cfg.latencia_ms,
                resolveu_up=bool(janela.resolveu_up),
                lado_up=lado_up,
            )
        return None


def sensibilidade_latencia(
    janelas: list[WindowState],
    streams: dict[str, list[tuple[int, float]]],
    *,
    latencias_ms: tuple[float, ...] = LATENCIAS_MS_PADRAO,
    threshold: float = 0.02,
) -> dict[str, Any]:
    """A tabela de PnL nos quatro cenários de latência (M2.D)."""
    saida: dict[str, Any] = {}
    for latencia in latencias_ms:
        runner = BacktestRunner(
            BacktestConfig(threshold_edge=threshold, latencia_ms=latencia)
        )
        report = runner.run(janelas, streams)
        saida[f"{latencia:.0f}ms"] = {
            "trades": len(report.trades),
            "pnl_liquido_usdc": round(report.pnl_liquido, 4),
            "hit_rate": round(report.hit_rate, 4) if report.trades else None,
        }
    return saida


def varredura_de_threshold(
    janelas: list[WindowState],
    streams: dict[str, list[tuple[int, float]]],
    *,
    thresholds: tuple[float, ...] = THRESHOLDS_PADRAO,
    latencia_ms: float = LATENCIA_PADRAO_MS,
) -> dict[float, BacktestReport]:
    """Um relatório por threshold, para a curva de edge (M2.D)."""
    return {
        threshold: BacktestRunner(
            BacktestConfig(threshold_edge=threshold, latencia_ms=latencia_ms)
        ).run(janelas, streams)
        for threshold in thresholds
    }
