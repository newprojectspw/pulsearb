"""Recorder de produção — grava a realidade para o M2 poder medi-la.

    python -m pulsearb.recorder --duration 72h

Fluxos gravados:

- **RTDS**: `crypto_prices_twap_sixty` (preço-verdade de 5m/15m/4h) e
  `crypto_prices` (spot Binance), de TODOS os ativos configurados
- **Binance direto**: `kline_1h` (preço-verdade das janelas horárias — o RTDS
  não entrega candles, e candle tem `open`, que tick nenhum reconstrói depois
  do fato) e `bookTicker`, para btc/eth
- **CLOB market WS**: book completo, price_change e eventos de resolução de
  TODAS as janelas descobertas — os dois jogos — com
  `custom_feature_enabled=true`
- **Snapshot da descoberta** a cada ciclo: metadados completos de cada janela,
  incluindo `tick_size` (para medir a mudança de tick, API_NOTES 13.3),
  `feeSchedule`, `endDate` e `acceptingOrders`

Rotatividade: janelas de 5m nascem e morrem a cada 5 minutos. O recorder
assina as novas e **desassina as encerradas** sem reiniciar, mantendo o número
de assinaturas estável.

Robustez: reconexão com backoff+jitter (herdada dos feeds); lacunas de
gravação registradas com duração e causa; ao encerrar, um relatório de
cobertura por fonte.

RODA NA VPS. O ambiente de desenvolvimento não alcança os endpoints — o que
se testa lá dentro é o pipeline contra servidores locais.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import re
import time
from collections import Counter
from typing import Any

import httpx
import orjson

from pulsearb.feeds.base import FeedEvent
from pulsearb.feeds.binance_ws import BinanceWsFeed
from pulsearb.feeds.poly_ws import RESOLUTION_EVENT_TYPES, PolyMarketWsFeed
from pulsearb.feeds.rtds import RtdsFeed
from pulsearb.markets.discovery import (
    DiscoveredMarket,
    MarketDiscovery,
    parse_end_date_epoch,
)
from pulsearb.obs import get_logger, setup_logging
from pulsearb.recorder.gaps import GapTracker, resumo_gaps
from pulsearb.recorder.writer import (
    FONTE_DISCOVERY,
    FONTE_GAP,
    FONTE_RESOLUCAO_SINTETICA,
    JsonlGzipWriter,
    RecordEnvelope,
)
from pulsearb.settings import Settings

log = get_logger("pulsearb.recorder.main")

# Janelas de 5m nascem o tempo todo; 30s é folgado o bastante para não
# martelar a Gamma e apertado o bastante para nunca perder o início de uma.
DISCOVERY_INTERVAL_SECONDS = 30.0
# Polling do watchdog de lacunas. Precisa ser bem menor que o menor limiar.
GAP_POLL_SECONDS = 1.0

# CARÊNCIA DE RESOLUÇÃO — a correção do bug que zerou o primeiro backtest.
# A janela sai da descoberta no endDate, mas o evento de resolução só é
# publicado DEPOIS (o M0 estimava ~2min; no jogo horário, com UMA no caminho,
# pode ser bem mais). Desassinar no endDate desligava a escuta antes do
# resultado existir: 104 janelas conhecidas, ZERO resoluções capturadas.
RESOLUTION_GRACE_SECONDS = 600.0
# Fallback: consultar a Gamma para janelas encerradas cuja resolução não
# chegou pelo WS. Independente do caminho do WS de propósito — se um falhar,
# o outro cobre.
RESOLUTION_POLL_SECONDS = 120.0

_DURATION_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*$", re.IGNORECASE)
_DURATION_UNITS = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0, "": 3600.0}


def parse_duration(text: str) -> float:
    """'72h' → 259200.0. Sem sufixo = horas (o uso mais comum aqui)."""
    match = _DURATION_PATTERN.match(text)
    if match is None:
        raise ValueError(f"duração inválida: {text!r} (use 90s, 30m, 72h, 7d)")
    return float(match.group(1)) * _DURATION_UNITS[match.group(2).lower()]


def market_snapshot(
    market: DiscoveredMarket, *, agora_epoch: float | None = None
) -> dict[str, Any]:
    """Metadados da janela para o snapshot da descoberta.

    `tick_size` entra de propósito: é ESTADO, não constante (API_NOTES 13.3),
    e a série destes snapshots é o dado bruto da medição M2.E.1.

    `_seconds_left` é o tempo restante NO MOMENTO da observação. Sem ele a
    medição do tick não sabe em que fase da janela o afinamento aconteceu — e
    era exatamente o que faltava: o campo era lido pela análise mas nunca
    escrito aqui, então todo `seconds_left` saía NaN.
    """
    if agora_epoch is None:
        agora_epoch = time.time()
    fim = parse_end_date_epoch({"endDate": market.end_date_iso})
    return {
        "slug": market.slug,
        "condition_id": market.condition_id,
        "asset": market.asset,
        "resolution": market.resolution.value,
        "token_id_by_outcome": market.token_id_by_outcome,
        "tick_size": market.tick_size,
        "min_order_size": market.min_order_size,
        "fee_rate": market.fee_rate,
        "fee_exponent": market.fee_exponent,
        "fee_taker_only": market.fee_taker_only,
        "fee_rebate_rate": market.fee_rebate_rate,
        "accepting_orders": market.accepting_orders,
        "end_date_iso": market.end_date_iso,
        "operable": market.operable,
        "gate_failures": market.gate_failures,
        "_seconds_left": (fim - agora_epoch) if fim is not None else None,
        "_observado_em_epoch": agora_epoch,
        "rewards_min_size": market.raw_gamma.get("rewardsMinSize"),
        "rewards_max_spread": market.raw_gamma.get("rewardsMaxSpread"),
        "uma_reward": market.raw_gamma.get("umaReward"),
        "best_bid": market.raw_gamma.get("bestBid"),
        "best_ask": market.raw_gamma.get("bestAsk"),
    }


class Recorder:
    """Orquestra feeds, descoberta, rotação de assinatura e gravação."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.writer = JsonlGzipWriter(
            output_dir=settings.recorder.output_dir,
            rotate_seconds=settings.recorder.rotate_seconds,
        )
        self.rtds = RtdsFeed(
            url=settings.endpoints.rtds_ws,
            user_agent=settings.user_agent,
            assets=settings.all_price_assets,
            on_event=self._on_event,
            stale_after_seconds=settings.feeds.stale_after_seconds_twap,
            reconnect_initial_seconds=settings.feeds.reconnect_initial_seconds,
            reconnect_max_seconds=settings.feeds.reconnect_max_seconds,
        )
        self.binance = BinanceWsFeed(
            assets=settings.assets,
            user_agent=settings.user_agent,
            on_event=self._on_event,
            stale_after_seconds=settings.feeds.stale_after_seconds_spot,
            reconnect_initial_seconds=settings.feeds.reconnect_initial_seconds,
            reconnect_max_seconds=settings.feeds.reconnect_max_seconds,
        )
        self.poly = PolyMarketWsFeed(
            url=settings.endpoints.clob_market_ws,
            user_agent=settings.user_agent,
            custom_feature_enabled=True,  # best bid/ask + eventos de resolução
            ping_interval_seconds=settings.feeds.clob_ping_interval_seconds,
            pong_stale_seconds=settings.feeds.clob_stale_seconds,
            on_event=self._on_event,
            stale_after_seconds=settings.feeds.stale_after_seconds_book,
            reconnect_initial_seconds=settings.feeds.reconnect_initial_seconds,
            reconnect_max_seconds=settings.feeds.reconnect_max_seconds,
        )
        self.trackers = [
            GapTracker(fonte="rtds", silencio_limiar_s=settings.feeds.stale_after_seconds_twap),
            GapTracker(
                fonte="binance_ws", silencio_limiar_s=settings.feeds.stale_after_seconds_spot
            ),
            GapTracker(
                fonte="poly_ws", silencio_limiar_s=settings.feeds.stale_after_seconds_book
            ),
        ]
        self._feed_by_name = {
            "rtds": self.rtds,
            "binance_ws": self.binance,
            "poly_ws": self.poly,
        }
        self.discovery_cycles = 0
        self.subscribed_ever: set[str] = set()
        # token -> instante (epoch) em que pode ser desassinado. É o endDate
        # da janela MAIS a carência de resolução.
        self.desassinar_apos: dict[str, float] = {}
        # token -> metadados mínimos para o fallback e o relatório
        self.janela_por_token: dict[str, dict[str, Any]] = {}
        # Resoluções já capturadas (por qualquer caminho), para não repolar.
        self.resolvidos: set[str] = set()
        # O que chega do CLOB, por event_type. Torna visível o que está sendo
        # recebido E o que está sendo ignorado por tipo desconhecido — sem
        # isto, "0 resoluções" não distingue "não chegou" de "chegou e foi
        # descartado".
        self.eventos_poly: Counter[str] = Counter()

    # ------------------------------------------------------------- hot path
    def _contar_evento_poly(self, event: FeedEvent) -> None:
        """Conta os tipos que chegam do CLOB, inclusive os desconhecidos."""
        payload = event.parsed
        if payload is None:
            self.eventos_poly["__nao_json__"] += 1
            return
        itens = payload if isinstance(payload, list) else [payload]
        for item in itens:
            if not isinstance(item, dict):
                self.eventos_poly["__nao_dict__"] += 1
                continue
            tipo = str(item.get("event_type") or "__sem_event_type__")
            self.eventos_poly[tipo] += 1
            if tipo in RESOLUTION_EVENT_TYPES:
                asset_id = item.get("asset_id")
                if isinstance(asset_id, str):
                    self.resolvidos.add(asset_id)

    def _on_event(self, event: FeedEvent) -> None:
        if event.source == "poly_ws":
            self._contar_evento_poly(event)
        self.writer.submit(
            RecordEnvelope(
                ts_mono_ns=event.ts_mono_ns,
                ts_wall_ns=event.ts_wall_ns,
                fonte=event.source,
                raw=event.raw,
            )
        )

    def _write_meta(self, fonte: str, payload: dict[str, Any]) -> None:
        """Grava um registro sintetizado pelo recorder (não veio do fio)."""
        self.writer.submit(
            RecordEnvelope(
                ts_mono_ns=time.monotonic_ns(),
                ts_wall_ns=time.time_ns(),
                fonte=fonte,
                raw=orjson.dumps(payload),
            )
        )

    # ------------------------------------------------------------ descoberta
    async def _discovery_loop(self, discovery: MarketDiscovery, deadline: float) -> None:
        while time.monotonic() < deadline:
            try:
                await self._discovery_cycle(discovery)
            except Exception as exc:
                log.warning("falha na descoberta", erro=f"{type(exc).__name__}: {exc}")
            await asyncio.sleep(DISCOVERY_INTERVAL_SECONDS)

    async def _discovery_cycle(self, discovery: MarketDiscovery) -> None:
        markets = await discovery.discover()
        self.discovery_cycles += 1

        agora = time.time()

        # Tokens que DEVEM estar assinados agora. Janela não-operável continua
        # sendo gravada: o motivo da recusa é dado, e o M2 quer medir isso.
        desejados = {
            token for market in markets for token in market.token_id_by_outcome.values()
        }
        # Registra a carência de cada token visto nesta descoberta.
        for market in markets:
            fim = parse_end_date_epoch({"endDate": market.end_date_iso})
            limite = (fim + RESOLUTION_GRACE_SECONDS) if fim is not None else (
                agora + RESOLUTION_GRACE_SECONDS
            )
            for token in market.token_id_by_outcome.values():
                self.desassinar_apos[token] = limite
                self.janela_por_token[token] = {
                    "slug": market.slug,
                    "condition_id": market.condition_id,
                    "end_date_iso": market.end_date_iso,
                    "outcome": next(
                        (o for o, t in market.token_id_by_outcome.items() if t == token),
                        None,
                    ),
                }

        atuais = set(self.poly.token_ids)
        novos = sorted(desejados - atuais)

        # Rotação COM CARÊNCIA: o token só sai depois que a janela encerrou
        # E a carência de resolução passou. Desassinar no endDate — como era
        # antes — desligava a escuta antes de o resultado ser publicado, e foi
        # por isso que o primeiro backtest real viu 104 janelas e 0 resoluções.
        candidatos = atuais - desejados
        encerrados = sorted(
            token
            for token in candidatos
            if agora >= self.desassinar_apos.get(token, 0.0)
            or token in self.resolvidos
        )
        for token in encerrados:
            self.desassinar_apos.pop(token, None)

        if novos:
            await self.poly.subscribe(novos)
            self.subscribed_ever.update(novos)
        if encerrados:
            await self.poly.unsubscribe(encerrados)

        self._write_meta(
            FONTE_DISCOVERY,
            {
                "ciclo": self.discovery_cycles,
                "janelas": [market_snapshot(m) for m in markets],
                "assinaturas": {
                    "novas": len(novos),
                    "encerradas": len(encerrados),
                    "ativas": len(self.poly.token_ids),
                    "em_carencia": len(candidatos) - len(encerrados),
                },
                "eventos_poly_por_tipo": dict(self.eventos_poly),
            },
        )
        log.info(
            "descoberta",
            ciclo=self.discovery_cycles,
            janelas=len(markets),
            operaveis=sum(1 for m in markets if m.operable),
            novas=len(novos),
            encerradas=len(encerrados),
            assinadas=len(self.poly.token_ids),
            em_carencia=len(candidatos) - len(encerrados),
            resolucoes=len(self.resolvidos),
            msgs_rtds=self.rtds.message_count,
            msgs_binance=self.binance.message_count,
            msgs_poly=self.poly.message_count,
            gravadas=self.writer.written,
            descartadas=self.writer.dropped,
        )

    # --------------------------------------------------- fallback de resolução
    async def _resolution_poll_loop(
        self, http_get_json: Any, deadline: float
    ) -> None:
        """Confere na Gamma o resultado de janelas encerradas.

        Caminho INDEPENDENTE do WS de propósito: se o evento de resolução não
        chegar (perdido numa reconexão, tipo novo não reconhecido, carência
        curta demais), este laço ainda captura o resultado. Uma resolução
        perdida invalida a janela inteira para o backtest — vale ter dois
        caminhos.

        O que sai daqui é gravado como evento SINTÉTICO, com fonte própria e
        `_sintetico: true`. Nunca se disfarça de evento do fio.
        """
        while time.monotonic() < deadline:
            await asyncio.sleep(RESOLUTION_POLL_SECONDS)
            agora = time.time()
            pendentes = [
                (token, meta)
                for token, meta in self.janela_por_token.items()
                if token not in self.resolvidos
                and (fim := parse_end_date_epoch({"endDate": meta.get("end_date_iso")}))
                is not None
                and agora > fim + 60.0
            ]
            # Só os mais antigos por ciclo, para não martelar a Gamma.
            for token, meta in pendentes[:20]:
                try:
                    await self._consultar_resolucao(http_get_json, token, meta)
                except Exception as exc:
                    log.warning(
                        "falha ao consultar resolução",
                        slug=meta.get("slug"),
                        erro=f"{type(exc).__name__}: {exc}",
                    )

    async def _consultar_resolucao(
        self, http_get_json: Any, token: str, meta: dict[str, Any]
    ) -> None:
        slug = meta.get("slug")
        if not slug:
            return
        gamma = await http_get_json(
            f"{self.settings.endpoints.gamma}/markets/slug/{slug}", None
        )
        if not isinstance(gamma, dict):
            return

        # A Gamma marca o vencedor pelos outcomePrices (1/0 depois de resolver).
        precos = gamma.get("outcomePrices")
        if isinstance(precos, str):
            with contextlib.suppress(orjson.JSONDecodeError):
                precos = orjson.loads(precos)
        vencedor: str | None = None
        if isinstance(precos, list) and len(precos) == 2:
            with contextlib.suppress(TypeError, ValueError):
                up, down = float(precos[0]), float(precos[1])
                if up >= 0.99 and down <= 0.01:
                    vencedor = "Up"
                elif down >= 0.99 and up <= 0.01:
                    vencedor = "Down"
        if vencedor is None:
            return  # ainda não resolveu; tenta no próximo ciclo

        self.resolvidos.add(token)
        self._write_meta(
            FONTE_RESOLUCAO_SINTETICA,
            {
                "_sintetico": True,
                "event_type": "market_resolved",
                "asset_id": token,
                "market": meta.get("condition_id"),
                "slug": slug,
                "winning_outcome": vencedor,
                "outcome_prices": precos,
                "uma_resolution_status": gamma.get("umaResolutionStatus"),
                "closed": gamma.get("closed"),
                "observado_em_epoch": time.time(),
            },
        )
        log.info("resolução capturada via Gamma", slug=slug, vencedor=vencedor)

    # ----------------------------------------------------------------- gaps
    async def _gap_loop(self, deadline: float) -> None:
        while time.monotonic() < deadline:
            agora = time.time_ns()
            for tracker in self.trackers:
                feed = self._feed_by_name[tracker.fonte]
                fechado = tracker.observe(
                    conectado=feed.connected,
                    idade_ultima_msg_s=feed.last_message_age_seconds,
                    agora_wall_ns=agora,
                )
                if fechado is not None:
                    self._write_meta(FONTE_GAP, fechado.to_dict())
                    log.warning("lacuna na gravação", **fechado.to_dict())
            await asyncio.sleep(GAP_POLL_SECONDS)

    # ---------------------------------------------------------------- ciclo
    async def run(self, duration_seconds: float) -> dict[str, Any]:
        await self.writer.start()
        inicio_mono = time.monotonic()
        deadline = inicio_mono + duration_seconds

        async with httpx.AsyncClient(
            headers={"User-Agent": self.settings.user_agent}, timeout=15.0
        ) as http:

            async def http_get_json(url: str, params: dict[str, Any] | None) -> Any:
                response = await http.get(url, params=params)
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                return response.json()

            discovery = MarketDiscovery(
                http_get_json=http_get_json,
                gamma_url=self.settings.endpoints.gamma,
                clob_url=self.settings.endpoints.clob,
                assets=self.settings.assets,
                probe_durations_seconds=self.settings.probe_durations_seconds,
            )

            await self.rtds.start()
            await self.binance.start()
            await self.poly.start()

            tasks = [
                asyncio.create_task(self._discovery_loop(discovery, deadline)),
                asyncio.create_task(self._gap_loop(deadline)),
                asyncio.create_task(
                    self._resolution_poll_loop(http_get_json, deadline)
                ),
            ]
            try:
                await asyncio.gather(*tasks)
            except asyncio.CancelledError:
                raise
            finally:
                for task in tasks:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
                agora = time.time_ns()
                for tracker in self.trackers:
                    pendente = tracker.finalizar(agora)
                    if pendente is not None:
                        self._write_meta(FONTE_GAP, pendente.to_dict())
                await self.rtds.stop()
                await self.binance.stop()
                await self.poly.stop()

        duracao = time.monotonic() - inicio_mono
        relatorio = {
            "duracao_s": round(duracao, 1),
            "ciclos_descoberta": self.discovery_cycles,
            "tokens_assinados_no_total": len(self.subscribed_ever),
            "mensagens": {
                "rtds": self.rtds.message_count,
                "binance_ws": self.binance.message_count,
                "poly_ws": self.poly.message_count,
            },
            "gravadas": self.writer.written,
            "descartadas": self.writer.dropped,
            "eventos_poly_por_tipo": dict(self.eventos_poly),
            "resolucoes_capturadas": len(self.resolvidos),
            "janelas_vistas": len(self.janela_por_token),
            "quedas_por_feed": {
                nome: {
                    "total": feed.close_count,
                    "ultimas": feed.close_reasons[-10:],
                }
                for nome, feed in self._feed_by_name.items()
            },
            "gaps": resumo_gaps(self.trackers, duracao),
        }
        self._write_meta("recorder_relatorio", relatorio)
        await self.writer.stop()
        return relatorio


async def run(settings: Settings, duration_seconds: float) -> dict[str, Any]:
    recorder = Recorder(settings)
    relatorio = await recorder.run(duration_seconds)
    log.info("recorder encerrado", **relatorio)
    return relatorio


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PULSEARB recorder — grava feeds crus")
    parser.add_argument(
        "--duration",
        default="72h",
        help="duração da gravação: 90s, 30m, 72h, 7d (default 72h)",
    )
    parser.add_argument(
        "--hours", type=float, default=None, help="[compat] duração em horas"
    )
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args(argv)

    setup_logging()
    settings = Settings.load(args.config)
    seconds = args.hours * 3600 if args.hours is not None else parse_duration(args.duration)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run(settings, seconds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
