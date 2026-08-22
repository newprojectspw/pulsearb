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
from collections.abc import Iterator
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

# Teto de memória do backtest — ver BookTimeline.
#
# O custo foi MEDIDO, não estimado: com 5 níveis por lado, cada snapshot
# retido custa ~1,3 KB (as tuplas de nível dominam, ~270 B cada). O orçamento
# é `tokens_vivos × LIMITE × 1,3 KB`; com os ~150 tokens simultâneos que a
# gravação real mostrou, 1.500 dão ~300 MB — cabe na VPS de 1 GB, que é onde
# o backtest morreu com `Killed` da primeira vez.
#
# 1.500 snapshots dão ~5/s numa janela de 5m: resolução de 200ms, suficiente
# para separar os cenários de latência de 300ms, 600ms e 1s. O de 150ms fica
# no limite, e é por isso que `pior_resolucao_ms` vai no relatório.
LIMITE_SNAPSHOTS_PADRAO = 1_500
# 5 níveis cobrem o que o backtest lê: o fill de 5 shares (o mínimo do
# mercado) raramente sai do topo, e `depth_usdc(ticks=3)` alcança no máximo 4
# níveis. Subir isto custa ~270 B por nível por snapshot.
NIVEIS_RETIDOS_PADRAO = 5


@dataclass(slots=True)
class BookTimeline:
    """Histórico de snapshots do book de um token, para consulta por tempo.

    A penalidade de latência precisa perguntar "como estava o book 300ms
    depois do sinal?" — então os snapshots ficam indexados por timestamp.

    MEMÓRIA É O PROBLEMA CENTRAL DESTA CLASSE. A versão anterior guardava um
    clone completo do book a cada `price_change`. Medido na gravação real:
    ~3.300 eventos/s, ~12 milhões de eventos por hora de gravação. Doze
    milhões de `OrderBook` com duas listas de níveis não cabem em 1 GB — o
    backtest morria com `Killed` (OOM) num único arquivo de 450 MB.

    Três defesas, nesta ordem:

    1. **Truncagem** (`niveis`): guarda só os N níveis do topo de cada lado.
       O backtest compra `min_order_size` = 5 shares e mede profundidade a 3
       ticks; nível 11 para baixo nunca é lido. Truncar é perda REAL de
       informação e por isso é explícita e configurável — não é "otimização".
    2. **Deduplicação**: se o topo não mudou, não há snapshot novo para
       guardar. `at()` devolveria o anterior, que é idêntico. Esta defesa é
       LOSSLESS dado (1), e é a que mais economiza: a maioria dos
       `price_change` mexe em níveis fundos.
    3. **Raleamento adaptativo** (`limite`): teto duro de snapshots por token.
       Ao estourar, descarta um a cada dois e passa a exigir um intervalo
       mínimo entre snapshots. A resolução temporal cai pela metade a cada
       raleamento, e `resolucao_ns` reporta a resolução efetiva — quem lê o
       relatório precisa saber se a penalidade de 150ms ainda é distinguível.
    """

    ts: list[int] = field(default_factory=list)
    books: list[OrderBook] = field(default_factory=list)
    limite: int = LIMITE_SNAPSHOTS_PADRAO
    niveis: int = NIVEIS_RETIDOS_PADRAO
    intervalo_min_ns: int = 0
    descartados: int = 0
    raleamentos: int = 0

    def append(self, book: OrderBook, ts_ns: int) -> None:
        bids = book.bids[: self.niveis]
        asks = book.asks[: self.niveis]
        if self.ts:
            if ts_ns - self.ts[-1] < self.intervalo_min_ns:
                self.descartados += 1
                return
            anterior = self.books[-1]
            if anterior.bids == bids and anterior.asks == asks:
                self.descartados += 1
                return
        self.ts.append(ts_ns)
        self.books.append(
            OrderBook(asset_id=book.asset_id, bids=bids, asks=asks, ts_ns=book.ts_ns)
        )
        if len(self.ts) > self.limite:
            self._ralear()

    def _ralear(self) -> None:
        """Descarta um snapshot a cada dois e sobe o intervalo mínimo.

        O intervalo novo é `span // limite` — metade do espaçamento médio
        resultante — para a série voltar a preencher o orçamento em vez de
        ralear de novo no evento seguinte.
        """
        self.descartados += len(self.ts) - len(self.ts[::2])
        self.ts = self.ts[::2]
        self.books = self.books[::2]
        self.raleamentos += 1
        span = self.ts[-1] - self.ts[0] if len(self.ts) > 1 else 0
        if span > 0:
            self.intervalo_min_ns = max(self.intervalo_min_ns * 2, span // self.limite)

    @property
    def resolucao_ns(self) -> int:
        """Intervalo mínimo efetivo entre snapshots retidos (0 = sem perda)."""
        return self.intervalo_min_ns

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
    # Fração da taxa do taker que volta para o maker que o executou
    # (API_NOTES 12.6). Lida do dado, nunca constante.
    fee_rebate_rate: float = 0.0
    books: dict[str, BookTimeline] = field(default_factory=dict)
    # Parâmetros de reward lidos do snapshot (M2.2 B.1). Fica como dict cru
    # de propósito: quem simula decide o que sabe ler, e um campo novo do
    # mercado não exige mudar esta classe.
    reward_meta: dict[str, Any] = field(default_factory=dict)
    # Execuções observadas no topo, para o markout (M2.2 B.2):
    # (ts_ns, preço, tamanho, lado)
    trades: list[tuple[int, float, float, str]] = field(default_factory=list)


@dataclass
class BacktestConfig:
    """Parâmetros de cenário. Pessimista por default."""

    threshold_edge: float = 0.02
    latencia_ms: float = LATENCIA_PADRAO_MS
    shares_por_trade: float = 5.0     # o mínimo do mercado (API_NOTES 12.5)
    buffer_slippage: float = 0.0      # já modelado pelo book; fica em 0
    exigir_fill_completo: bool = True  # FOK: parcial não conta (M4)
    exigir_vol_calibrada: bool = True
    # M2.6 BUG 2: faixa de tempo restante em que se PODE operar. `None` nos
    # dois = sem restrição, que é o comportamento até o M2.5.
    #
    # Por que isto existe: a calibração medida sobre 4h reais tem erro de
    # −0,008 no bucket 240–120s e −0,240 em >240s, e 46 dos 48 trades caíram
    # em >240s. O modelo é quase perfeito onde quase não opera. A faixa
    # permite operar onde ele sabe, em vez de onde ele chega primeiro.
    tempo_restante_min_s: float | None = None
    tempo_restante_max_s: float | None = None
    # M2.7 BUG/tarefa 3: quantas entradas a v1 pode fazer por janela. 1 é o
    # comportamento até o M2.6 — e é o que produziu 18 trades sobre 1.617
    # instantes com sinal, muito abaixo dos 200 que o VEREDITO_M2 exige.
    max_entradas_por_janela: int = 1
    # Espaçamento mínimo entre entradas. NÃO é um botão de gosto: ticks
    # consecutivos com sinal são a MESMA oportunidade observada de novo, não
    # oportunidades novas. Sem espaçamento, uma janela com sinal contínuo
    # viraria centenas de trades sobre o mesmo movimento — e o PnL somaria a
    # mesma aposta repetida como se fossem independentes.
    intervalo_min_entre_entradas_s: float = 30.0

    def na_faixa(self, seconds_left: float) -> bool:
        """Este instante está na faixa de tempo restante autorizada?"""
        if self.tempo_restante_max_s is not None and (
            seconds_left > self.tempo_restante_max_s
        ):
            return False
        return not (
            self.tempo_restante_min_s is not None
            and seconds_left < self.tempo_restante_min_s
        )


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


def _instantes_da_janela(
    janela: WindowState,
    stream: list[tuple[int, float]],
    vol: RealizedVol,
    twap: TwapTracker,
) -> Iterator[tuple[int, float, float]]:
    """Os instantes do stream que pertencem à janela, já com tempo restante.

    Os estimadores são alimentados AQUI, e não no laço de decisão, porque
    `vol` e `twap` precisam ver todo tick de dentro da janela — inclusive os
    que não geram decisão nenhuma. Separar o "quais instantes contam" do "o
    que fazer em cada um" deixa o laço de decisão com uma responsabilidade
    só, e põe a regra de fronteira num lugar com nome.
    """
    for ts_ns, preco_spot in stream:
        if ts_ns < janela.open_ts_ns or ts_ns > janela.close_ts_ns:
            continue
        vol.update(preco_spot, ts_ns)
        twap.update(preco_spot, ts_ns)
        seconds_left = (janela.close_ts_ns - ts_ns) / 1e9
        if seconds_left <= 0:
            continue
        yield ts_ns, preco_spot, seconds_left


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
        entradas = 0
        ultima_entrada_ns = 0

        for ts_ns, preco_spot, seconds_left in _instantes_da_janela(
            janela, stream, vol, twap
        ):
            est = self._estimar(janela, twap, vol, preco_spot, seconds_left)

            # Calibração: medida em TODA previsão, não só onde se operou.
            report.add_calibration(est.bucket_tempo, est.prob_up, janela.resolveu_up)

            if cfg.exigir_vol_calibrada and not est.confiavel:
                continue

            candidatos = self._candidatos_com_edge(janela, est, ts_ns)
            if candidatos:
                # Contado ANTES de qualquer gate de execução: a pergunta aqui
                # é "o sinal existiu neste instante?", não "operamos?".
                report.add_oportunidade(est.bucket_tempo, janela.slug)

            if not candidatos or not self._pode_entrar(
                seconds_left=seconds_left,
                entradas=entradas,
                ultima_entrada_ns=ultima_entrada_ns,
                ts_ns=ts_ns,
            ):
                continue

            trade = self._tentar_entrada(
                janela, est, ts_ns, latencia_ns, report, candidatos
            )
            if trade is not None:
                report.add_trade(trade)
                entradas += 1
                ultima_entrada_ns = ts_ns

    def _pode_entrar(
        self,
        *,
        seconds_left: float,
        entradas: int,
        ultima_entrada_ns: int,
        ts_ns: int,
    ) -> bool:
        """Este instante está autorizado a virar entrada?

        Três regras, todas do M2.6/M2.7, e todas sobre QUANDO se pode operar —
        nenhuma sobre se o sinal é bom, que é pergunta de
        `_candidatos_com_edge`:

        1. a faixa de tempo restante (o modelo só tem calibração dentro dela);
        2. o teto de entradas por janela;
        3. o espaçamento mínimo — ticks consecutivos com sinal são a MESMA
           oportunidade vista de novo, e contá-los como novas somaria a mesma
           aposta repetida.
        """
        cfg = self.config
        if not cfg.na_faixa(seconds_left):
            return False
        if entradas >= cfg.max_entradas_por_janela:
            return False
        return not (
            ultima_entrada_ns
            and (ts_ns - ultima_entrada_ns) < cfg.intervalo_min_entre_entradas_s * 1e9
        )

    @staticmethod
    def _estimar(
        janela: WindowState,
        twap: TwapTracker,
        vol: RealizedVol,
        preco_spot: float,
        seconds_left: float,
    ) -> Any:
        """A probabilidade do modelo neste instante, pelo jogo da janela.

        Os dois jogos são fisicamente diferentes (§13.4 do API_NOTES): o TWAP
        tem fração da média já travada nos últimos 60s; o horário compara
        contra o preço de abertura do candle. Só a escolha entre eles mora
        aqui — o resto do laço não precisa saber qual é.
        """
        if janela.jogo == "twap":
            locked_mean, locked_weight = twap.locked_mean_and_weight(seconds_left)
            return prob_up_twap(
                ancora=janela.ancora,
                spot=preco_spot,
                seconds_left=seconds_left,
                sigma_1s=vol.sigma_1s,
                locked_mean=locked_mean,
                locked_weight=locked_weight,
                twap_atual=twap.current_twap,
                vol_ready=vol.ready,
            )
        return prob_up_hourly(
            open_price=janela.ancora,
            spot=preco_spot,
            seconds_left=seconds_left,
            sigma_1s=vol.sigma_1s,
            vol_ready=vol.ready,
        )

    def _candidatos_com_edge(
        self, janela: WindowState, est: Any, ts_ns: int
    ) -> list[tuple[bool, str, float]]:
        """Lados cujo edge líquido passa do threshold NESTE instante.

        Só o SINAL: não olha latência, fill, nem tamanho mínimo. Separar isto
        da execução é o que permite contar oportunidade mesmo depois de a
        janela já ter operado (M2.6 BUG 2) sem duplicar a regra do gatilho em
        dois lugares — que é como as duas contas passariam a divergir.

        A ordem é preservada (Up antes de Down) porque a execução depende
        dela: se o Up não preenche, a tentativa cai para o Down.
        """
        cfg = self.config
        # Os dois lados: comprar Up a P(Up), ou Down a 1−P(Up).
        saida: list[tuple[bool, str, float]] = []
        for lado_up, token, prob in (
            (True, janela.token_up, est.prob_up),
            (False, janela.token_down, 1.0 - est.prob_up),
        ):
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
            if edge >= cfg.threshold_edge:
                saida.append((lado_up, token, prob))
        return saida

    def _tentar_entrada(
        self,
        janela: WindowState,
        est: Any,
        ts_ns: int,
        latencia_ns: int,
        report: BacktestReport,
        candidatos: list[tuple[bool, str, float]],
    ) -> Trade | None:
        cfg = self.config
        for lado_up, token, prob in candidatos:
            timeline = janela.books.get(token)
            if timeline is None:
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
