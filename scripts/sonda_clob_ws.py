"""Sondas do CLOB WS — as medidas por trás da seção "As quedas do CLOB WS".

    .venv/bin/python scripts/sonda_clob_ws.py 1008
    .venv/bin/python scripts/sonda_clob_ws.py custo  --custo-us 150 --duracao 240
    .venv/bin/python scripts/sonda_clob_ws.py rajada --duracao 240

`1008`: quatro cenários de 16 s (conexão vazia só com PING; vazia + subscribe
dinâmico; frame inicial + dinâmico; frame inicial com todos os tokens). Foi o
que mostrou que o `1008 invalid subscription payload` é o PING chegando antes
da primeira assinatura.

`custo`: consumidor com custo ARTIFICIAL por mensagem (busy-wait de N µs).
Rodar três ao mesmo tempo, com custos diferentes, foi o que mostrou que o
`1013 slow consumer` cai no mesmo instante nos três — é rajada do servidor,
não lentidão nossa.

`rajada`: bytes por janela de 100 ms, pior segundo, e a concentração por
mercado (dois mercados de 5 min eram 54 % dos bytes).

Os tokens vêm da descoberta Up/Down do próprio bot (`MarketDiscovery`, os
mesmos ativos do `config.yaml`) ou de `--tokens arquivo.json` (lista de ids).
Não grava nada; não assina ordem; não toca em `data/`.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import sys
import time

import httpx

from pulsearb.feeds.poly_ws import PolyMarketWsFeed
from pulsearb.markets.discovery import MarketDiscovery
from pulsearb.markets.http import fazer_http_get_json
from pulsearb.obs import setup_logging
from pulsearb.settings import Settings


def _ler_tokens(arquivo: str) -> list[str]:
    with open(arquivo, encoding="utf-8") as f:
        return list(json.load(f))


async def _tokens(settings: Settings, arquivo: str | None) -> list[str]:
    if arquivo:
        return _ler_tokens(arquivo)
    async with httpx.AsyncClient(
        headers={"User-Agent": settings.user_agent}, timeout=15.0
    ) as http:
        discovery = MarketDiscovery(
            http_get_json=fazer_http_get_json(
                http, bases=(settings.endpoints.gamma, settings.endpoints.clob)
            ),
            gamma_url=settings.endpoints.gamma,
            clob_url=settings.endpoints.clob,
            assets=settings.assets,
            probe_durations_seconds=settings.probe_durations_seconds,
        )
        mercados = await discovery.discover()
    return sorted(
        {t for m in mercados for t in m.token_id_by_outcome.values()}
    )


def _feed(settings: Settings, tokens: list[str], on_event) -> PolyMarketWsFeed:
    return PolyMarketWsFeed(
        url=settings.endpoints.clob_market_ws,
        user_agent=settings.user_agent,
        token_ids=tokens,
        on_event=on_event,
    )


async def sonda_1008(settings: Settings, tokens: list[str]) -> None:
    async def cenario(nome: str, iniciais: list[str], dinamicos: list[str]) -> None:
        n = 0

        async def on_event(_e) -> None:
            nonlocal n
            n += 1

        feed = _feed(settings, iniciais, on_event)
        await feed.start()
        await asyncio.sleep(1.0)
        if dinamicos:
            await feed.subscribe(dinamicos)
        await asyncio.sleep(15.0)
        await feed.stop()
        print(f"{nome}: eventos={n} reconexoes={feed.reconnect_count}", flush=True)

    await cenario("A vazio, so PING", [], [])
    await cenario("B vazio + subscribe dinamico", [], tokens[:4])
    await cenario("C inicial 4 + dinamico 4", tokens[:4], tokens[4:8])
    await cenario("D inicial todos, nada mais", tokens, [])


async def sonda_custo(
    settings: Settings, tokens: list[str], custo_us: float, duracao: float
) -> None:
    n = 0
    total = 0

    async def on_event(e) -> None:
        nonlocal n, total
        n += 1
        total += len(e.raw)
        t = time.perf_counter()
        while (time.perf_counter() - t) * 1e6 < custo_us:
            pass

    feed = _feed(settings, tokens, on_event)
    await feed.start()
    t0 = time.monotonic()
    ultimo = 0
    while time.monotonic() - t0 < duracao:
        await asyncio.sleep(10)
        print(
            f"t={time.monotonic() - t0:.0f}s msgs/s={(n - ultimo) / 10:.0f} "
            f"MB={total / 1e6:.1f} reconexoes={feed.reconnect_count}",
            flush=True,
        )
        ultimo = n
    await feed.stop()


async def sonda_rajada(settings: Settings, tokens: list[str], duracao: float) -> None:
    bins: collections.Counter[int] = collections.Counter()
    por_mercado: collections.Counter[str] = collections.Counter()
    t0 = time.monotonic()

    async def on_event(e) -> None:
        bins[int((time.monotonic() - t0) * 10)] += len(e.raw)
        itens = e.parsed if isinstance(e.parsed, list) else [e.parsed]
        for item in itens:
            if isinstance(item, dict):
                chave = item.get("market") or item.get("asset_id") or "?"
                por_mercado[str(chave)] += len(e.raw) // max(1, len(itens))

    feed = _feed(settings, tokens, on_event)
    await feed.start()
    await asyncio.sleep(duracao)
    await feed.stop()

    total = sum(bins.values()) or 1
    print(f"total MB={total / 1e6:.1f} media MB/s={total / 1e6 / duracao:.2f} "
          f"reconexoes={feed.reconnect_count}")
    print("picos (t_s, MB em 100 ms):",
          [(b / 10, round(v / 1e6, 2)) for b, v in bins.most_common(8)])
    ordenado = sorted(bins.items())
    pior = max(
        (sum(v for _, v in ordenado[i:i + 10]), ordenado[i][0] / 10)
        for i in range(len(ordenado))
    )
    print(f"pior segundo: {pior[0] / 1e6:.2f} MB em t={pior[1]}s")
    acumulado = 0
    for chave, v in por_mercado.most_common(8):
        acumulado += v
        print(f"  {chave[:16]:18} {100 * v / total:5.1f}%  acumulado {100 * acumulado / total:5.1f}%")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sonda", choices=("1008", "custo", "rajada"))
    parser.add_argument("--tokens", help="JSON com a lista de token ids")
    parser.add_argument("--custo-us", type=float, default=0.0)
    parser.add_argument("--duracao", type=float, default=240.0)
    args = parser.parse_args(argv)

    setup_logging()
    settings = Settings.load()

    async def _rodar() -> None:
        tokens = await _tokens(settings, args.tokens)
        print(f"tokens={len(tokens)}", flush=True)
        if args.sonda == "1008":
            await sonda_1008(settings, tokens)
        elif args.sonda == "custo":
            await sonda_custo(settings, tokens, args.custo_us, args.duracao)
        else:
            await sonda_rajada(settings, tokens, args.duracao)

    asyncio.run(_rodar())
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
