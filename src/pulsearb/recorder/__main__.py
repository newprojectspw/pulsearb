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
from typing import Any

import httpx
import orjson

from pulsearb.feeds.base import FeedEvent
from pulsearb.feeds.binance_ws import BinanceWsFeed
from pulsearb.feeds.poly_ws import PolyMarketWsFeed
from pulsearb.feeds.rtds import RtdsFeed
from pulsearb.markets.discovery import DiscoveredMarket, MarketDiscovery
from pulsearb.obs import get_logger, setup_logging
from pulsearb.recorder.gaps import GapTracker, resumo_gaps
from pulsearb.recorder.writer import (
    FONTE_DISCOVERY,
    FONTE_GAP,
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

_DURATION_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*$", re.IGNORECASE)
_DURATION_UNITS = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0, "": 3600.0}


def parse_duration(text: str) -> float:
    """'72h' → 259200.0. Sem sufixo = horas (o uso mais comum aqui)."""
    match = _DURATION_PATTERN.match(text)
    if match is None:
        raise ValueError(f"duração inválida: {text!r} (use 90s, 30m, 72h, 7d)")
    return float(match.group(1)) * _DURATION_UNITS[match.group(2).lower()]


def market_snapshot(market: DiscoveredMarket) -> dict[str, Any]:
    """Metadados da janela para o snapshot da descoberta.

    `tick_size` entra de propósito: é ESTADO, não constante (API_NOTES 13.3),
    e a série destes snapshots é o dado bruto da medição M2.E.1.
    """
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

    # ------------------------------------------------------------- hot path
    def _on_event(self, event: FeedEvent) -> None:
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

        # Tokens que DEVEM estar assinados agora. Janela não-operável continua
        # sendo gravada: o motivo da recusa é dado, e o M2 quer medir isso.
        desejados = {
            token for market in markets for token in market.token_id_by_outcome.values()
        }
        atuais = set(self.poly.token_ids)
        novos = sorted(desejados - atuais)
        # Rotação: o que sumiu da descoberta é janela encerrada. Desassinar
        # mantém o número de assinaturas estável ao longo de 72h — sem isso, a
        # conexão acumularia uma janela de 5m nova a cada 5 minutos.
        encerrados = sorted(atuais - desejados)

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
                },
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
            msgs_rtds=self.rtds.message_count,
            msgs_binance=self.binance.message_count,
            msgs_poly=self.poly.message_count,
            gravadas=self.writer.written,
            descartadas=self.writer.dropped,
        )

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
