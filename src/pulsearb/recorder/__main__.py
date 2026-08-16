"""python -m pulsearb.recorder --hours N

Grava os feeds crus (RTDS: binance + twap60 de todos os ativos; books CLOB
das janelas descobertas) em JSONL gzip com rotação horária.

RODA FORA DO SANDBOX (Colab/VPS): este processo precisa de rede real.
No sandbox de desenvolvimento os endpoints estão bloqueados — o que se testa
aqui dentro é o pipeline com feeds falsos (tests/test_recorder.py).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import time

import httpx

from pulsearb.feeds.base import FeedEvent
from pulsearb.feeds.poly_ws import PolyMarketWsFeed
from pulsearb.feeds.rtds import RtdsFeed
from pulsearb.markets.discovery import MarketDiscovery
from pulsearb.obs import get_logger, setup_logging
from pulsearb.recorder.writer import JsonlGzipWriter, RecordEnvelope
from pulsearb.settings import Settings

log = get_logger("pulsearb.recorder.main")

# Rediscoberta periódica: janelas de 5m nascem o tempo todo.
DISCOVERY_INTERVAL_SECONDS = 60.0


async def run(settings: Settings, hours: float) -> None:
    writer = JsonlGzipWriter(
        output_dir=settings.recorder.output_dir,
        rotate_seconds=settings.recorder.rotate_seconds,
    )
    await writer.start()

    def on_event(event: FeedEvent) -> None:
        writer.submit(
            RecordEnvelope(
                ts_mono_ns=event.ts_mono_ns,
                ts_wall_ns=event.ts_wall_ns,
                fonte=event.source,
                raw=event.raw,
            )
        )

    rtds = RtdsFeed(
        url=settings.endpoints.rtds_ws,
        user_agent=settings.user_agent,
        assets=settings.all_price_assets,
        on_event=on_event,
        stale_after_seconds=settings.feeds.stale_after_seconds,
        reconnect_initial_seconds=settings.feeds.reconnect_initial_seconds,
        reconnect_max_seconds=settings.feeds.reconnect_max_seconds,
    )
    poly = PolyMarketWsFeed(
        url=settings.endpoints.clob_market_ws,
        user_agent=settings.user_agent,
        ping_interval_seconds=settings.feeds.clob_ping_interval_seconds,
        pong_stale_seconds=settings.feeds.clob_stale_seconds,
        on_event=on_event,
        stale_after_seconds=settings.feeds.stale_after_seconds,
        reconnect_initial_seconds=settings.feeds.reconnect_initial_seconds,
        reconnect_max_seconds=settings.feeds.reconnect_max_seconds,
    )

    async with httpx.AsyncClient(
        headers={"User-Agent": settings.user_agent}, timeout=15.0
    ) as http:

        async def http_get_json(url: str, params: dict | None) -> object:
            response = await http.get(url, params=params)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()

        discovery = MarketDiscovery(
            http_get_json=http_get_json,
            gamma_url=settings.endpoints.gamma,
            clob_url=settings.endpoints.clob,
            assets=settings.assets,
            probe_durations_seconds=settings.probe_durations_seconds,
        )

        await rtds.start()
        await poly.start()
        deadline = time.monotonic() + hours * 3600

        try:
            while time.monotonic() < deadline:
                try:
                    markets = await discovery.discover()
                    tokens = [
                        token
                        for market in markets
                        for token in market.token_id_by_outcome.values()
                    ]
                    if tokens:
                        await poly.subscribe(tokens)
                    log.info(
                        "descoberta",
                        janelas=len(markets),
                        operaveis=sum(1 for m in markets if m.operable),
                        tokens_assinados=len(poly.token_ids),
                        msgs_rtds=rtds.message_count,
                        msgs_poly=poly.message_count,
                        gravadas=writer.written,
                        descartadas=writer.dropped,
                    )
                except Exception as exc:
                    log.warning("falha na descoberta", erro=f"{type(exc).__name__}: {exc}")
                await asyncio.sleep(DISCOVERY_INTERVAL_SECONDS)
        finally:
            await rtds.stop()
            await poly.stop()
            await writer.stop()
            log.info("recorder encerrado", gravadas=writer.written, descartadas=writer.dropped)


def main() -> int:
    parser = argparse.ArgumentParser(description="PULSEARB recorder — grava feeds crus")
    parser.add_argument(
        "--hours", type=float, default=24.0, help="duração da gravação (default 24)"
    )
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    setup_logging()
    settings = Settings.load(args.config)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run(settings, args.hours))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
