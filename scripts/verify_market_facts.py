#!/usr/bin/env python3
"""Fecha as lacunas do M0 lendo a API de verdade, em vez de supor.

O ambiente onde o M0 foi escrito tinha egress bloqueado para a Polymarket, então
docs/API_NOTES.md tem uma lista de itens `[NÃO VERIFICADO]` (seção 10). Este
script vai buscar cada um deles ao vivo:

  - valores reais de `fd.r` e `fd.e` (a fee dinâmica) para BTC/ETH Up/Down
  - tick size e tamanho mínimo de ordem das janelas
  - o padrão real dos slugs
  - o texto das regras e o `resolution.source` — ou seja, QUAL é o preço-verdade
  - `secondsDelay` (o atraso até a liquidação)

Uso:

    python3 scripts/verify_market_facts.py
    python3 scripts/verify_market_facts.py --asset btc --hours 6 --raw

Sem dependências: só stdlib. Usa apenas caminhos já verificados em
docs/API_NOTES.md seção 2 — nada aqui foi inventado.

O script imprime **JSON cru** de propósito. A ideia é você olhar o dado, não
confiar no meu parser.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

# --- Endpoints verificados (polymarket-client 0.6.0, src/polymarket/environments.py)
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"

# Sentinela de fim da paginação keyset (_internal/actions/_cursor.py: END_CURSOR)
END_CURSOR = "LTE="

ASSET_TERMS = {
    "btc": ("btc", "bitcoin"),
    "eth": ("eth", "ethereum"),
}
UPDOWN_TERMS = ("up-or-down", "updown", "up-down", "-up-", "higher-or-lower")
WINDOW_PATTERN = re.compile(r"(?<![a-z0-9])(\d+)\s*(m|min|minute|h|hour|d|day)(?![a-z])")


def get_json(base: str, path: str, params: dict[str, Any] | None = None, timeout: float = 20.0) -> Any:
    query = ""
    if params:
        pairs: list[tuple[str, str]] = []
        for key, value in params.items():
            if value is None:
                continue
            if isinstance(value, bool):
                pairs.append((key, "true" if value else "false"))
            elif isinstance(value, (list, tuple)):
                pairs.extend((key, str(item)) for item in value)
            else:
                pairs.append((key, str(value)))
        query = "?" + urllib.parse.urlencode(pairs)
    url = f"{base}{path}{query}"
    request = urllib.request.Request(url, headers={"User-Agent": "pulsearb-verify/0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def try_get_json(base: str, path: str, params: dict[str, Any] | None = None) -> Any:
    """Igual a get_json, mas devolve o erro como dado em vez de explodir.

    Um endpoint que mudou de forma é exatamente o tipo de coisa que este script
    existe para revelar — então o erro precisa aparecer no relatório, não travar
    a execução.
    """
    try:
        return get_json(base, path, params)
    except urllib.error.HTTPError as exc:
        return {"__erro__": f"HTTP {exc.code}", "url": f"{base}{path}", "corpo": exc.read()[:400].decode(errors="replace")}
    except Exception as exc:
        return {"__erro__": f"{type(exc).__name__}: {exc}", "url": f"{base}{path}"}


def discover_markets(hours: int, max_pages: int, page_size: int) -> list[dict[str, Any]]:
    """Lista mercados abertos que fecham nas próximas `hours` horas.

    Caminho `/markets/keyset` e params `limit` / `after_cursor` / `closed` /
    `end_date_min` / `end_date_max` verificados no SDK (API_NOTES seção 2.2).
    """
    now = datetime.now(timezone.utc)
    params: dict[str, Any] = {
        "closed": False,
        "end_date_min": now.isoformat(),
        "end_date_max": (now + timedelta(hours=hours)).isoformat(),
        "limit": page_size,
    }
    markets: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(max_pages):
        page_params = dict(params)
        if cursor:
            page_params["after_cursor"] = cursor
        payload = try_get_json(GAMMA, "/markets/keyset", page_params)
        if isinstance(payload, dict) and "__erro__" in payload:
            print(f"  ! falha na descoberta: {payload['__erro__']}", file=sys.stderr)
            break
        if not isinstance(payload, dict):
            break
        batch = payload.get("markets") or []
        markets.extend(item for item in batch if isinstance(item, dict))
        cursor = payload.get("next_cursor")
        if not cursor or cursor == END_CURSOR or not batch:
            break
    return markets


def is_updown(market: dict[str, Any], assets: list[str]) -> bool:
    haystack = " ".join(
        str(market.get(key, "")).lower() for key in ("slug", "question", "groupItemTitle")
    )
    if not any(term in haystack for term in UPDOWN_TERMS):
        return False
    terms = [term for asset in assets for term in ASSET_TERMS.get(asset, (asset,))]
    return any(term in haystack for term in terms)


def guess_window(slug: str) -> str:
    match = WINDOW_PATTERN.search(slug.lower())
    if not match:
        return "?"
    unit = match.group(2)[0]
    return f"{match.group(1)}{unit}"


def summarize_market(market: dict[str, Any]) -> dict[str, Any]:
    """Extrai só os campos que o M0 precisa confirmar. Nomes camelCase conforme
    os aliases do modelo Gamma do SDK (models/gamma/market.py)."""
    keys = (
        "id", "slug", "question", "conditionId", "questionId", "clobTokenIds",
        "startDate", "endDate", "active", "closed", "acceptingOrders",
        "enableOrderBook", "negRisk", "feeSchedule", "feesEnabled", "feeType",
        "secondsDelay", "minimumOrderSize", "minimumTickSize", "orderPriceMinTickSize",
        "resolutionSource", "umaResolutionStatus", "resolvedBy", "outcomes",
        "outcomePrices", "bestBid", "bestAsk", "spread", "liquidityNum", "volumeNum",
    )
    out = {key: market[key] for key in keys if key in market}
    out["_janela_inferida"] = guess_window(str(market.get("slug", "")))
    description = market.get("description")
    if isinstance(description, str):
        out["description"] = description
    return out


def token_ids_of(market: dict[str, Any]) -> list[str]:
    raw = market.get("clobTokenIds")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    return [str(item) for item in raw] if isinstance(raw, list) else []


def inspect_clob(market: dict[str, Any]) -> dict[str, Any]:
    """Lê o lado CLOB: a fee real (`fd`), o tick size e o neg-risk."""
    out: dict[str, Any] = {}
    condition_id = market.get("conditionId")
    if condition_id:
        clob_market = try_get_json(CLOB, f"/clob-markets/{condition_id}")
        out["clob_markets_response"] = clob_market
        if isinstance(clob_market, dict) and "fd" in clob_market:
            fee = clob_market["fd"]
            out["FEE_fd"] = fee
            if isinstance(fee, dict):
                try:
                    rate, exponent = float(fee.get("r", 0)), float(fee.get("e", 0))
                    peak = rate * (0.25**exponent)
                    out["FEE_interpretada"] = {
                        "r": rate,
                        "e": exponent,
                        "formula": "fee_usdc = n_shares * r * (p*(1-p))**e",
                        "pico_em_p=0.50_por_share": f"{peak:.4%}",
                        "pico_em_p=0.50_sobre_capital": f"{peak / 0.5:.4%}",
                    }
                except (TypeError, ValueError):
                    out["FEE_interpretada"] = "não foi possível converter r/e para número"
    tokens = token_ids_of(market)
    if tokens:
        out["tick_size"] = try_get_json(CLOB, "/tick-size", {"token_id": tokens[0]})
        out["neg_risk"] = try_get_json(CLOB, "/neg-risk", {"token_id": tokens[0]})
        out["book_token_0"] = try_get_json(CLOB, "/book", {"token_id": tokens[0]})
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Verifica ao vivo os fatos pendentes do M0")
    parser.add_argument("--asset", action="append", default=[], choices=sorted(ASSET_TERMS),
                        help="ativo a inspecionar; pode repetir (default: btc e eth)")
    parser.add_argument("--hours", type=int, default=6,
                        help="olhar janelas que fecham nas próximas N horas (default 6)")
    parser.add_argument("--per-window", type=int, default=1,
                        help="quantos mercados inspecionar por janela (default 1)")
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--raw", action="store_true", help="também despeja o JSON cru completo")
    parser.add_argument("--json", help="grava o relatório completo neste arquivo")
    args = parser.parse_args()

    assets = args.asset or ["btc", "eth"]

    print("=" * 72)
    print("PULSEARB — verificação ao vivo dos fatos pendentes do M0")
    print(f"ativos={','.join(assets)}  janela de busca={args.hours}h  {datetime.now(timezone.utc).isoformat()}")
    print("=" * 72)

    print("\n[1/3] Descobrindo mercados abertos na Gamma...")
    markets = discover_markets(args.hours, args.max_pages, args.page_size)
    print(f"      {len(markets)} mercados abertos que fecham nas próximas {args.hours}h")

    updown = [market for market in markets if is_updown(market, assets)]
    print(f"      {len(updown)} parecem ser Up/Down de {'/'.join(assets)}")

    if not updown:
        print("\n  Nenhum mercado Up/Down encontrado. Isso é informação, não erro:")
        print("  pode ser filtro errado, janela curta demais, ou o produto mudou.")
        print("  Rode com --hours 24 --raw e confira os slugs à mão.")
        if args.raw and markets:
            print("\n  Amostra de slugs encontrados:")
            for market in markets[:40]:
                print(f"    {market.get('slug')}")
        return 1

    by_window: dict[str, list[dict[str, Any]]] = {}
    for market in updown:
        by_window.setdefault(guess_window(str(market.get("slug", ""))), []).append(market)

    print("\n[2/3] Janelas encontradas (padrão de slug — item pendente da seção 10):")
    for window, group in sorted(by_window.items()):
        print(f"      {window:>4}  x{len(group):<4} ex.: {group[0].get('slug')}")

    print("\n[3/3] Lendo fee, tick size e regras de cada janela...")
    report: dict[str, Any] = {
        "gerado_em": datetime.now(timezone.utc).isoformat(),
        "ativos": assets,
        "janelas": {},
    }
    for window, group in sorted(by_window.items()):
        entries: list[dict[str, Any]] = []
        for market in group[: args.per_window]:
            print(f"      · {market.get('slug')}")
            entry = {"gamma": summarize_market(market), "clob": inspect_clob(market)}
            if args.raw:
                entry["gamma_raw"] = market
            entries.append(entry)
        report["janelas"][window] = entries

    print("\n" + "=" * 72)
    print("RESULTADO — cole os campos abaixo em docs/API_NOTES.md e troque os")
    print("[NÃO VERIFICADO] correspondentes por [VERIFICADO] com a data de hoje.")
    print("=" * 72)
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))

    print("\n" + "=" * 72)
    print("O QUE OLHAR, ITEM A ITEM (seção 10 do API_NOTES):")
    print("  1. clob.FEE_interpretada  → confirma r e e. Compare com as duas")
    print("     hipóteses da seção 5.3 (1,56% vs 1,75% de pico).")
    print("  2. gamma.description      → é AQUI que está a fonte de resolução.")
    print("     Procure 'Chainlink', 'TWAP', 'Binance'. Confirma ou derruba a")
    print("     seção 7 (mudança de agosto/2026 para TWAP Chainlink).")
    print("  3. gamma.secondsDelay     → o atraso de liquidação (~2 min?).")
    print("  4. clob.tick_size + gamma.minimumOrderSize → o stake de US$ 5 do M4")
    print("     cabe no mínimo do mercado?")
    print("  5. as chaves de 'janelas'  → o padrão real de slug. Existe janela de 1h?")
    print("=" * 72)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, ensure_ascii=False, default=str)
        print(f"\nRelatório gravado em {args.json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
