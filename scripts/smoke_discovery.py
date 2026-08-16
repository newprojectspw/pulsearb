#!/usr/bin/env python3
"""Smoke da descoberta ao vivo — RODAR FORA DO SANDBOX (Colab/VPS).

Roda a descoberta REAL do projeto (pulsearb.markets.discovery) contra a Gamma
e o CLOB de produção e imprime a tabela das janelas ativas: fonte de
resolução, tick, minSize, fees e gates.

Precisa do pacote instalado (na VPS: `pip install -e .`; no Colab:
`pip install git+https://<token>@github.com/newprojectspw/pulsearb@main`)
e de `httpx`. Se o pulsearb não estiver instalável no ambiente, use
scripts/verify_market_facts.py, que é stdlib pura e cobre o mesmo terreno
com menos detalhe.

Uso:
    python3 smoke_discovery.py
    python3 smoke_discovery.py --asset btc --no-keyset
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

import httpx

try:
    from pulsearb.markets.discovery import HOURLY_DURATION_SECONDS, MarketDiscovery
except ImportError as exc:  # mensagem acionável, não stack trace
    raise SystemExit(
        "pulsearb não está instalado neste ambiente.\n"
        "  VPS:   pip install -e .\n"
        "  Colab: pip install git+https://<token>@github.com/newprojectspw/pulsearb@main\n"
        f"(erro original: {exc})"
    ) from exc

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) pulsearb-smoke/0.1"


async def main_async(args: argparse.Namespace) -> int:
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=15.0) as http:

        async def http_get_json(url: str, params: dict[str, Any] | None) -> Any:
            response = await http.get(url, params=params)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()

        discovery = MarketDiscovery(
            http_get_json=http_get_json,
            gamma_url=GAMMA,
            clob_url=CLOB,
            assets=args.asset or ["btc", "eth"],
            # Os DOIS jogos: 5m/15m/4h (TWAP) e 3600 (horário, resolvido por
            # candle da Binance e com padrão de slug próprio, API_NOTES 12.2b).
            probe_durations_seconds=[300, 900, HOURLY_DURATION_SECONDS, 14400],
        )
        markets = await discovery.discover(keyset_fallback=not args.no_keyset)

    if not markets:
        print("nenhuma janela encontrada — cheque conectividade e o padrão de slug (API_NOTES 12.1)")
        return 1

    print(f"\n{'slug':<32} {'resolução':<15} {'tick':>6} {'min':>5} {'fee r/e':>9} {'operável':>9}")
    print("-" * 88)
    for market in sorted(markets, key=lambda m: m.slug):
        fee = f"{market.fee_rate}/{market.fee_exponent}"
        print(
            f"{market.slug:<32} {market.resolution.value:<15} {market.tick_size:>6} "
            f"{market.min_order_size:>5.0f} {fee:>9} {'SIM' if market.operable else 'NÃO':>9}"
        )
        if not market.operable:
            print(f"{'':<32} gates: {', '.join(market.gate_failures)}")

    operable = sum(1 for m in markets if m.operable)
    horarias = sum(1 for m in markets if "-up-or-down-" in m.slug)
    print(f"\n{len(markets)} janelas, {operable} operáveis, {horarias} do jogo horário")
    print("Confira:")
    print("  - 5m/15m/4h devem sair como twap60 (API_NOTES 12.3)")
    print("  - as horárias devem sair como binance_candle (API_NOTES 12.2b)")
    print("  - 0 janelas horárias = o padrão de slug mudou; olhe o log de descarte")
    print("  - qualquer 'desconhecido' novo merece leitura manual da description")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke da descoberta ao vivo")
    parser.add_argument("--asset", action="append", default=[], choices=["btc", "eth"])
    parser.add_argument("--no-keyset", action="store_true", help="pula o fallback por keyset")
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
