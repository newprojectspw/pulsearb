"""Entrypoint: python -m pulsearb --mode sim [--fake-feeds]

No M1 o processo sobe os feeds (ou geradores sintéticos com --fake-feeds),
alimenta o estado do dashboard e serve a página em :8080. Sinal, execução e
travas chegam no M3/M4.

--fake-feeds existe porque o sandbox de desenvolvimento não alcança os
endpoints reais: gera ticks sintéticos para demonstrar o dashboard. É
explícito na tela (contador fake_ticks) — nunca se passa por dado real.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import random
import sys
import time
from pathlib import Path

import uvicorn

from pulsearb.feeds.poly_ws import PolyMarketWsFeed
from pulsearb.feeds.rtds import RtdsFeed
from pulsearb.obs import get_logger, setup_logging
from pulsearb.settings import Mode, Settings
from pulsearb.ui.server import DashboardState, FeedStatus, create_app

log = get_logger("pulsearb.main")


async def _update_dashboard_from_feeds(
    state: DashboardState, feeds: dict[str, RtdsFeed | PolyMarketWsFeed]
) -> None:
    while True:
        for name, feed in feeds.items():
            state.feeds[name] = FeedStatus(
                connected=feed.connected,
                stale=feed.stale,
                message_count=feed.message_count,
                last_message_age_s=feed.last_message_age_seconds,
            )
            if isinstance(feed, RtdsFeed):
                now_ns = time.monotonic_ns()
                for (topic, asset), tick in feed.last_tick_by_key.items():
                    state.last_ticks[f"{topic}:{asset}"] = {
                        "price": tick.price,
                        "age_s": round((now_ns - tick.ts_mono_ns) / 1e9, 1),
                    }
        await asyncio.sleep(0.5)


async def _fake_feed_loop(state: DashboardState) -> None:
    """Ticks sintéticos para demonstração do dashboard sem rede."""
    state.feeds["rtds (fake)"] = FeedStatus(connected=True, stale=False)
    state.feeds["poly_ws (fake)"] = FeedStatus(connected=True, stale=False)
    prices = {"btc": 118_000.0, "eth": 4_400.0}
    n = 0
    while True:
        for asset, price in prices.items():
            prices[asset] = price * (1 + random.gauss(0, 2e-4))
            state.last_ticks[f"crypto_prices_twap_sixty:{asset}"] = {
                "price": round(prices[asset], 2),
                "age_s": 0.0,
            }
        n += 1
        state.counters["fake_ticks"] = n
        for status in state.feeds.values():
            status.message_count = n
            status.last_message_age_s = 0.0
        await asyncio.sleep(1.0)


async def run(settings: Settings, fake_feeds: bool) -> None:
    state = DashboardState(
        mode=settings.mode.value,
        # Sem isto o botão do 3.11 nasce indisponível: a página mostraria a
        # caixa de parada e ela não pararia nada.
        caminho_do_kill=Path(settings.risk.caminho_do_kill),
    )
    app = create_app(state)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=settings.ui.host,
            port=settings.ui.port,
            log_level="warning",
        )
    )

    tasks: list[asyncio.Task[None]] = []
    feeds: dict[str, RtdsFeed | PolyMarketWsFeed] = {}
    if fake_feeds:
        tasks.append(asyncio.create_task(_fake_feed_loop(state)))
    else:
        rtds = RtdsFeed(
            url=settings.endpoints.rtds_ws,
            user_agent=settings.user_agent,
            assets=settings.all_price_assets,
            stale_after_seconds=settings.feeds.stale_after_seconds_twap,
            reconnect_initial_seconds=settings.feeds.reconnect_initial_seconds,
            reconnect_max_seconds=settings.feeds.reconnect_max_seconds,
            # M2.7: mesma defesa do recorder. O dashboard ao vivo sofria da
            # mesma cegueira — só não tinha como medi-la.
            sem_dados_timeout_s=settings.feeds.rtds_sem_dados_timeout_s,
            topico_mudo_s=settings.feeds.rtds_topico_mudo_s,
            reassinatura_intervalo_s=settings.feeds.rtds_reassinatura_intervalo_s,
            reassinaturas_ate_derrubar=(
                settings.feeds.rtds_reassinaturas_ate_derrubar
            ),
        )
        poly = PolyMarketWsFeed(
            url=settings.endpoints.clob_market_ws,
            user_agent=settings.user_agent,
            ping_interval_seconds=settings.feeds.clob_ping_interval_seconds,
            pong_stale_seconds=settings.feeds.clob_stale_seconds,
            stale_after_seconds=settings.feeds.stale_after_seconds_book,
            reconnect_initial_seconds=settings.feeds.reconnect_initial_seconds,
            reconnect_max_seconds=settings.feeds.reconnect_max_seconds,
        )
        feeds = {"rtds": rtds, "poly_ws": poly}
        await rtds.start()
        await poly.start()
        tasks.append(asyncio.create_task(_update_dashboard_from_feeds(state, feeds)))

    log.info(
        "pulsearb iniciado",
        modo=settings.mode.value,
        dashboard=f"http://{settings.ui.host}:{settings.ui.port}",
        fake_feeds=fake_feeds,
    )
    try:
        await server.serve()
    finally:
        for task in tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for feed in feeds.values():
            await feed.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pulsearb")
    parser.add_argument("--mode", default=None, help="SIM | SHADOW | LIVE (default: config/.env)")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument(
        "--fake-feeds",
        action="store_true",
        help="gera ticks sintéticos (demonstração sem rede; explícito na tela)",
    )
    args = parser.parse_args(argv)

    setup_logging()
    overrides = {}
    if args.mode is not None:
        overrides["mode"] = args.mode.upper()
    settings = Settings.load(args.config, **overrides)

    if settings.mode is not Mode.SIM:
        # Trava do M1: só SIM existe. SHADOW/LIVE chegam no M4 com as travas.
        log.warning("modo indisponível no M1 — caindo para SIM", pedido=settings.mode.value)
        settings = Settings.load(args.config, mode=Mode.SIM)

    # uvloop no processo real (regra do hot path). Instalação aqui, não no
    # import, para os testes rodarem no loop padrão do pytest-asyncio.
    if sys.platform != "win32":
        import uvloop

        uvloop.install()

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run(settings, args.fake_feeds))
    return 0
