#!/usr/bin/env python3
"""Smoke dos feeds ao vivo — RODAR FORA DO SANDBOX (Colab/VPS).

Conecta por 60s (configurável) ao RTDS e ao WS de book do CLOB e imprime:
- contagem de mensagens por tópico
- intervalo entre mensagens do TWAP p50/p99 (a cadência do TWAP é dado de
  estratégia: define quanto da média já está formada perto do fim da janela)
- p50/p99 do book

Dependência única além da stdlib: `websockets` (pip install websockets).

Uso:
    python3 smoke_feeds.py                       # só RTDS
    python3 smoke_feeds.py --token-id <id> ...   # RTDS + book do CLOB
    python3 smoke_feeds.py --auto-discover       # acha uma janela ativa sozinho
    python3 smoke_feeds.py --seconds 120

Endpoints e protocolos: docs/API_NOTES.md seções 2, 6 e 12.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import urllib.request

import websockets

RTDS_WS = "wss://ws-live-data.polymarket.com"
CLOB_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
GAMMA = "https://gamma-api.polymarket.com"

# Cloudflare: sem User-Agent explícito = 403 error 1010 (API_NOTES 12.10).
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) pulsearb-smoke/0.1"


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(-(-pct * len(ordered) // 100))))
    return ordered[rank - 1]


def auto_discover_token() -> list[str]:
    """Acha uma janela updown ativa via grade de slugs (API_NOTES 12.1)."""
    now = int(time.time())
    for asset in ("btc", "eth"):
        for dur, tag in ((300, "5m"), (900, "15m"), (14400, "4h")):
            start = (now // dur) * dur
            slug = f"{asset}-updown-{tag}-{start}"
            url = f"{GAMMA}/markets/slug/{slug}"
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    market = json.loads(response.read())
            except Exception:
                continue
            raw = market.get("clobTokenIds")
            if isinstance(raw, str):
                raw = json.loads(raw)
            if isinstance(raw, list) and raw:
                print(f"  descoberta automática: {slug}")
                return [str(token) for token in raw]
    return []


async def watch_rtds(seconds: float, stats: dict) -> None:
    async with websockets.connect(
        RTDS_WS, additional_headers={"User-Agent": USER_AGENT}
    ) as ws:
        await ws.send(json.dumps({
            "action": "subscribe",
            "subscriptions": [
                {"topic": "crypto_prices", "type": "update"},
                {"topic": "crypto_prices_twap_sixty", "type": "update"},
            ],
        }))
        deadline = time.monotonic() + seconds
        last_by_topic: dict[str, float] = {}
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                message = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except TimeoutError:
                break
            now = time.monotonic()
            try:
                data = json.loads(message)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            topic = data.get("topic", "?")
            symbol = (data.get("payload") or {}).get("symbol", "?")
            key = f"rtds {topic}"
            stats.setdefault(key, {"n": 0, "intervals": [], "symbols": set()})
            stats[key]["n"] += 1
            stats[key]["symbols"].add(symbol)
            interval_key = f"{topic}:{symbol}"
            if interval_key in last_by_topic:
                stats[key]["intervals"].append(now - last_by_topic[interval_key])
            last_by_topic[interval_key] = now


async def watch_clob(seconds: float, token_ids: list[str], stats: dict) -> None:
    async with websockets.connect(
        CLOB_WS, additional_headers={"User-Agent": USER_AGENT}
    ) as ws:
        await ws.send(json.dumps({
            "type": "market",
            "assets_ids": token_ids,
            "custom_feature_enabled": True,
        }))
        deadline = time.monotonic() + seconds
        last_msg: float | None = None
        next_ping = time.monotonic() + 10.0
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                message = await asyncio.wait_for(
                    ws.recv(), timeout=min(remaining, next_ping - time.monotonic())
                )
            except TimeoutError:
                if time.monotonic() >= next_ping:
                    await ws.send("PING")  # heartbeat de aplicação (API_NOTES 6.1)
                    next_ping = time.monotonic() + 10.0
                continue
            now = time.monotonic()
            text = message if isinstance(message, str) else message.decode(errors="replace")
            if text.strip() == "PONG":
                stats.setdefault("clob PONG", {"n": 0, "intervals": [], "symbols": set()})
                stats["clob PONG"]["n"] += 1
                continue
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            events = data if isinstance(data, list) else [data]
            for event in events:
                kind = event.get("event_type", "?") if isinstance(event, dict) else "?"
                key = f"clob {kind}"
                stats.setdefault(key, {"n": 0, "intervals": [], "symbols": set()})
                stats[key]["n"] += 1
                if last_msg is not None:
                    stats[key]["intervals"].append(now - last_msg)
            last_msg = now


async def main_async(args: argparse.Namespace) -> int:
    stats: dict = {}
    token_ids = list(args.token_id)
    if args.auto_discover and not token_ids:
        token_ids = auto_discover_token()
        if not token_ids:
            print("  ! descoberta automática não achou janela ativa; seguindo só com RTDS")

    tasks = [watch_rtds(args.seconds, stats)]
    if token_ids:
        tasks.append(watch_clob(args.seconds, token_ids, stats))

    print(f"conectando por {args.seconds:.0f}s… (RTDS{' + CLOB' if token_ids else ' apenas'})")
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for result in results:
        if isinstance(result, Exception):
            print(f"  ! falha: {type(result).__name__}: {result}")

    print(f"\n{'fluxo':<40} {'msgs':>6} {'int p50':>9} {'int p99':>9}  símbolos")
    print("-" * 88)
    for key in sorted(stats):
        entry = stats[key]
        intervals = entry["intervals"]
        p50 = f"{percentile(intervals, 50):.2f}s" if intervals else "—"
        p99 = f"{percentile(intervals, 99):.2f}s" if intervals else "—"
        symbols = ", ".join(sorted(entry["symbols"]))[:30] if entry["symbols"] else ""
        print(f"{key:<40} {entry['n']:>6} {p50:>9} {p99:>9}  {symbols}")

    print("\nO que olhar:")
    print("  - rtds crypto_prices_twap_sixty: intervalo p50 é a cadência do TWAP.")
    print("    Isso alimenta o modelo do M3 (quanto da média já está formada).")
    print("  - clob book/price_change: silêncio total = token errado ou janela morta.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke dos feeds ao vivo (rodar fora do sandbox)")
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--token-id", action="append", default=[],
                        help="token do book CLOB; pode repetir")
    parser.add_argument("--auto-discover", action="store_true",
                        help="acha uma janela updown ativa e usa os tokens dela")
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
