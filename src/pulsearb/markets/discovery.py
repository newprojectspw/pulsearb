"""Descoberta de janelas Up/Down via Gamma, com metadados do CLOB.

Fatos que dirigem este módulo (docs/API_NOTES.md seção 12):
- Slug TWAP: `{ativo}-updown-{dur}-{epoch_do_INÍCIO}`, grade alinhada (início
  múltiplo da duração). Confirmado para 5m/15m/4h. (12.1)
- Slug 1h: `{ativo_por_extenso}-up-or-down-{mês}-{dia}[-{ano}]-{h}{am|pm}-et`,
  em America/New_York COM horário de verão (zoneinfo, nunca offset fixo), nas
  duas variantes (com e sem ano). (12.2)
- Mapa de verdade: 5m/15m/4h → twap60; 1h → binance_candle. (12.2b)
- Fonte de resolução é OBRIGATORIAMENTE classificada por mercado; janela com
  fonte desconhecida é ignorada e logada. (12.3, seção 7.4)
- GATE: mercado sem feeSchedule legível não entra na lista operável. (5.2)
- Token é mapeado pelo campo `o` do CLOB compacto, nunca por posição. (12.11)
- MERCADOS-ZUMBI: a Gamma tem janelas de 2025 ainda com closed=false. NUNCA
  confiar em closed=false isolado — filtro triplo: end_date_min/max na query
  (agora até +2h), acceptingOrders=true e endDate no futuro checado
  localmente. (12.12)
- SLUG RESOLVE MERCADO ANTIGO: a Gamma devolve HTTP 200 para slug de janela
  antiga sem sinalizar. Todo slug resolvido é validado contra a janela que foi
  PEDIDA (endDate bate, dentro da tolerância, e está no futuro). (12.12b)
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

import orjson

from pulsearb.obs import get_logger

# Sentinela de fim da paginação keyset (verificado, API_NOTES 2.2).
END_CURSOR = "LTE="

# Assinatura do "cliente HTTP": é injetável para os testes rodarem sem rede
# (regra do M1) e para os smokes usarem httpx. Devolve o JSON decodificado,
# ou None para 404.
HttpGetJson = Callable[[str, dict[str, Any] | None], Awaitable[Any]]


class ResolutionKind(StrEnum):
    TWAP60 = "twap60"
    BINANCE_CANDLE = "binance_candle"
    DESCONHECIDO = "desconhecido"


def classify_resolution_source(market: dict[str, Any]) -> ResolutionKind:
    """Classifica a fonte de resolução pelo resolutionSource + description.

    Conservador de propósito: só classifica o que reconhece com segurança.
    Qualquer coisa fora dos padrões conhecidos = DESCONHECIDO = não opera.
    """
    source = str(market.get("resolutionSource") or "").lower()
    description = str(market.get("description") or "").lower()
    slug = str(market.get("slug") or "").lower()
    haystack = f"{source} {description}"

    if "twap-60s" in source or (
        "chainlink" in haystack and "twap" in haystack and "60" in haystack
    ):
        return ResolutionKind.TWAP60
    if "binance" in haystack and (
        "candle" in haystack or "kline" in haystack or "hourly" in haystack or "1h" in haystack
    ):
        return ResolutionKind.BINANCE_CANDLE
    # Janela horária reconhecida pelo padrão de slug próprio (API_NOTES 12.2):
    # resolve por candle 1h da Binance, fechamento >= abertura.
    if "-up-or-down-" in slug and slug.endswith("-et"):
        return ResolutionKind.BINANCE_CANDLE
    return ResolutionKind.DESCONHECIDO


def grid_slots(
    now_epoch: int, duration_seconds: int, *, ahead: int = 2, behind: int = 0
) -> list[int]:
    """Epochs de INÍCIO de janela na grade alinhada.

    `behind` janelas passadas (a corrente conta como índice 0) e `ahead`
    janelas futuras. Ex.: now=1000, dur=300 → corrente começa em 900.
    """
    current_start = (now_epoch // duration_seconds) * duration_seconds
    return [
        current_start + offset * duration_seconds for offset in range(-behind, ahead + 1)
    ]


def build_slug(asset: str, duration_seconds: int, start_epoch: int) -> str:
    """Padrão verificado ao vivo (12.1): {ativo}-updown-{dur}-{epoch_início}.

    Vale para as janelas TWAP (5m/15m/4h). A de 1h usa outro padrão —
    ver build_hourly_slugs.
    """
    if duration_seconds % 3600 == 0:
        dur = f"{duration_seconds // 3600}h"
    elif duration_seconds % 60 == 0:
        dur = f"{duration_seconds // 60}m"
    else:
        dur = f"{duration_seconds}s"
    return f"{asset.lower()}-updown-{dur}-{start_epoch}"


# Fuso das janelas horárias — America/New_York COM horário de verão.
# Offset fixo daria slug errado metade do ano (API_NOTES 12.2).
NY_TZ = ZoneInfo("America/New_York")

# Slug horário usa o nome por extenso do ativo.
ASSET_LONG_NAME = {"btc": "bitcoin", "eth": "ethereum"}

HOURLY_DURATION_SECONDS = 3600


def build_hourly_slugs(asset: str, start_epoch: int) -> list[str]:
    """Slugs da janela de 1h que começa em `start_epoch` (API_NOTES 12.2).

    Formato: {ativo_por_extenso}-up-or-down-{mês}-{dia}[-{ano}]-{h}{am|pm}-et
    Devolve as DUAS variantes (com e sem ano) — ambas foram observadas.
    """
    long_name = ASSET_LONG_NAME.get(asset.lower(), asset.lower())
    local = datetime.fromtimestamp(start_epoch, tz=NY_TZ)
    month = local.strftime("%B").lower()
    day = local.day  # sem zero à esquerda
    hour12 = local.hour % 12 or 12
    meridiem = "am" if local.hour < 12 else "pm"
    base = f"{long_name}-up-or-down-{month}-{day}"
    tail = f"{hour12}{meridiem}-et"
    return [f"{base}-{local.year}-{tail}", f"{base}-{tail}"]


@dataclass(slots=True)
class DiscoveredMarket:
    """Uma janela descoberta, com tudo que o engine precisa cacheado."""

    slug: str
    condition_id: str
    asset: str
    resolution: ResolutionKind
    token_id_by_outcome: dict[str, str]  # {"Up": ..., "Down": ...} — pelo campo `o`
    tick_size: float
    min_order_size: float
    fee_rate: float          # fd.r / feeSchedule.rate
    fee_exponent: float      # fd.e / feeSchedule.exponent
    fee_taker_only: bool
    fee_rebate_rate: float | None
    accepting_orders: bool
    end_date_iso: str | None
    operable: bool           # passou em TODOS os gates
    gate_failures: list[str] = field(default_factory=list)
    raw_gamma: dict[str, Any] = field(default_factory=dict)


def _parse_maybe_json_list(value: Any) -> list[str]:
    """Gamma devolve listas como STRING JSON ('["Up","Down"]'). Aceita ambos."""
    if isinstance(value, str):
        try:
            value = orjson.loads(value)
        except orjson.JSONDecodeError:
            return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def parse_end_date_epoch(gamma: dict[str, Any]) -> float | None:
    """endDate ISO-8601 → epoch. None se ausente/ilegível."""
    raw = gamma.get("endDate")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# Tolerância ao comparar o endDate com a janela pedida. Precisa ser bem menor
# que a menor duração (300s) para não aceitar a janela vizinha por engano.
WINDOW_MATCH_TOLERANCE_SECONDS = 60.0


def validate_window_match(
    gamma: dict[str, Any],
    *,
    expected_end_epoch: float,
    now_epoch: float,
    tolerance_seconds: float = WINDOW_MATCH_TOLERANCE_SECONDS,
) -> str | None:
    """Confere se o mercado devolvido é MESMO a janela que foi pedida.

    Existe porque a Gamma resolve slugs de mercados antigos com HTTP 200 e sem
    sinalizar (API_NOTES 12.12b): pedir `bitcoin-up-or-down-august-16-2pm-et`
    pode devolver a janela homônima de 2025. Sem esta checagem, a descoberta
    aceitaria um mercado morto como se fosse a janela corrente.

    Devolve o motivo da recusa, ou None se o mercado confere.
    """
    end_epoch = parse_end_date_epoch(gamma)
    if end_epoch is None:
        return "end_date_ausente_ou_ilegivel"
    if end_epoch <= now_epoch:
        return "slug_resolveu_janela_no_passado"
    if abs(end_epoch - expected_end_epoch) > tolerance_seconds:
        return (
            f"slug_resolveu_janela_errada (esperado fim ~{int(expected_end_epoch)}, "
            f"veio {int(end_epoch)})"
        )
    return None


def extract_market(
    gamma: dict[str, Any],
    clob_compact: dict[str, Any] | None,
    *,
    now_epoch: float | None = None,
) -> DiscoveredMarket:
    """Combina o mercado Gamma com o CLOB compacto e aplica os gates.

    Preferências:
    - token↔outcome: campo `o` do CLOB compacto (nunca por posição); fallback
      para o pareamento outcomes×clobTokenIds da Gamma (única opção lá)
    - fee: feeSchedule (Gamma) e fd (CLOB) — se ambos existem, precisam bater
    - tick/min: CLOB (mts/mos) com fallback Gamma
    - anti-zumbi (12.12): closed=false NÃO basta; exige acceptingOrders=true
      e endDate no futuro
    """
    if now_epoch is None:
        now_epoch = time.time()
    gates: list[str] = []
    slug = str(gamma.get("slug") or "")
    condition_id = str(gamma.get("conditionId") or "")
    asset = slug.split("-", 1)[0] if slug else ""

    resolution = classify_resolution_source(gamma)
    if resolution is ResolutionKind.DESCONHECIDO:
        gates.append("fonte_de_resolucao_desconhecida")

    # --- anti-zumbi (API_NOTES 12.12): endDate no futuro, checado localmente.
    end_epoch = parse_end_date_epoch(gamma)
    if end_epoch is None:
        gates.append("end_date_ausente_ou_ilegivel")
    elif end_epoch <= now_epoch:
        gates.append("zumbi_end_date_no_passado")

    # --- token por outcome
    token_by_outcome: dict[str, str] = {}
    if clob_compact and isinstance(clob_compact.get("t"), list):
        for entry in clob_compact["t"]:
            if isinstance(entry, dict) and "t" in entry and "o" in entry:
                token_by_outcome[str(entry["o"])] = str(entry["t"])
    if not token_by_outcome:
        outcomes = _parse_maybe_json_list(gamma.get("outcomes"))
        token_ids = _parse_maybe_json_list(gamma.get("clobTokenIds"))
        if outcomes and len(outcomes) == len(token_ids):
            token_by_outcome = dict(zip(outcomes, token_ids, strict=True))
    if set(token_by_outcome) != {"Up", "Down"}:
        gates.append("tokens_up_down_incompletos")

    # --- fee (GATE central: sem fee legível, não opera)
    fee_rate: float | None = None
    fee_exponent: float | None = None
    fee_taker_only = False
    fee_rebate: float | None = None

    schedule = gamma.get("feeSchedule")
    if isinstance(schedule, dict):
        fee_rate = _as_float(schedule.get("rate"))
        fee_exponent = _as_float(schedule.get("exponent"))
        fee_taker_only = bool(schedule.get("takerOnly", False))
        fee_rebate = _as_float(schedule.get("rebateRate"))

    fd = (clob_compact or {}).get("fd")
    if isinstance(fd, dict):
        clob_rate = _as_float(fd.get("r"))
        clob_exp = _as_float(fd.get("e"))
        if fee_rate is None:
            fee_rate, fee_exponent = clob_rate, clob_exp
            fee_taker_only = bool(fd.get("to", False))
        elif clob_rate is not None and (clob_rate != fee_rate or clob_exp != fee_exponent):
            # Divergência entre Gamma e CLOB é sinal de dado podre: não opera.
            gates.append("fee_divergente_gamma_vs_clob")

    if fee_rate is None or fee_exponent is None:
        gates.append("fee_schedule_ilegivel")
        fee_rate = float("nan")
        fee_exponent = float("nan")

    # --- tick e mínimo (CLOB preferido)
    tick = _as_float((clob_compact or {}).get("mts")) or _as_float(
        gamma.get("orderPriceMinTickSize")
    )
    min_size = _as_float((clob_compact or {}).get("mos")) or _as_float(gamma.get("orderMinSize"))
    if tick is None:
        gates.append("tick_size_ausente")
        tick = float("nan")
    if min_size is None:
        gates.append("min_order_size_ausente")
        min_size = float("nan")

    # acceptingOrders: Gamma e CLOB precisam CONCORDAR que está aceitando.
    # Discordância entre as duas fontes é bandeira vermelha (mesmo tratamento
    # da fee divergente): na dúvida, não opera. Parte do filtro anti-zumbi
    # (12.12) — closed=false isolado não significa mercado vivo.
    sinais = [
        bool(value)
        for value in (gamma.get("acceptingOrders"), (clob_compact or {}).get("ao"))
        if value is not None
    ]
    accepting = bool(sinais) and all(sinais)
    if not accepting:
        gates.append("nao_aceitando_ordens")

    return DiscoveredMarket(
        slug=slug,
        condition_id=condition_id,
        asset=asset,
        resolution=resolution,
        token_id_by_outcome=token_by_outcome,
        tick_size=tick,
        min_order_size=min_size,
        fee_rate=fee_rate,
        fee_exponent=fee_exponent,
        fee_taker_only=fee_taker_only,
        fee_rebate_rate=fee_rebate,
        accepting_orders=accepting,
        end_date_iso=gamma.get("endDate"),
        operable=not gates,
        gate_failures=gates,
        raw_gamma=gamma,
    )


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


class MarketDiscovery:
    """Descoberta em duas fases: grade de slugs + fallback por keyset.

    O cliente HTTP é injetado (regra offline-first do M1): os testes passam um
    fake; os smokes/produção passam httpx. Cache por conditionId.
    """

    def __init__(
        self,
        *,
        http_get_json: HttpGetJson,
        gamma_url: str,
        clob_url: str,
        assets: list[str],
        probe_durations_seconds: list[int],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.http_get_json = http_get_json
        self.gamma_url = gamma_url.rstrip("/")
        self.clob_url = clob_url.rstrip("/")
        self.assets = [a.lower() for a in assets]
        self.probe_durations = list(probe_durations_seconds)
        self.clock = clock
        self.log = get_logger("pulsearb.discovery")
        self.cache: dict[str, DiscoveredMarket] = {}  # por conditionId

    async def discover(
        self, *, ahead: int = 2, keyset_fallback: bool = True
    ) -> list[DiscoveredMarket]:
        """Descobre janelas atuais/próximas. Devolve TODAS (operáveis ou não);
        quem consome filtra por .operable. Janela não-operável é logada."""
        found: dict[str, DiscoveredMarket] = {}

        # Fase 1: grade de slugs (barata, endereça direto o mercado).
        now = int(self.clock())
        for asset in self.assets:
            for duration in self.probe_durations:
                for start in grid_slots(now, duration, ahead=ahead):
                    # A janela de 1h tem padrão de slug próprio (API_NOTES 12.2):
                    # nominal, em America/New_York, nas variantes com e sem ano.
                    candidates = (
                        build_hourly_slugs(asset, start)
                        if duration == HOURLY_DURATION_SECONDS
                        else [build_slug(asset, duration, start)]
                    )
                    expected_end = float(start + duration)
                    for slug in candidates:
                        gamma = await self._get_gamma_by_slug(slug)
                        if gamma is None:
                            continue
                        # A Gamma devolve 200 para slug de mercado antigo sem
                        # sinalizar (12.12b): confere se é MESMO esta janela.
                        motivo = validate_window_match(
                            gamma, expected_end_epoch=expected_end, now_epoch=float(now)
                        )
                        if motivo is not None:
                            self.log.info(
                                "slug descartado: não é a janela pedida",
                                slug_pedido=slug,
                                slug_devolvido=gamma.get("slug"),
                                end_date=gamma.get("endDate"),
                                motivo=motivo,
                            )
                            break  # a variante existe, mas está morta: não insista
                        market = await self._assemble(gamma)
                        if market.condition_id:
                            found[market.condition_id] = market
                        break  # variante encontrada: não testa a outra

        # Fase 2: fallback por keyset — pega durações fora da grade (ex.: a 1h
        # com padrão de slug ainda desconhecido, API_NOTES 12.2).
        if keyset_fallback:
            async for gamma in self._iter_keyset_updown():
                condition_id = str(gamma.get("conditionId") or "")
                if condition_id and condition_id not in found:
                    market = await self._assemble(gamma)
                    if market.condition_id:
                        found[market.condition_id] = market

        for market in found.values():
            if not market.operable:
                self.log.info(
                    "janela ignorada",
                    slug=market.slug,
                    motivos=market.gate_failures,
                )
        self.cache.update(found)
        return list(found.values())

    # ------------------------------------------------------------------ interno
    async def _get_gamma_by_slug(self, slug: str) -> dict[str, Any] | None:
        data = await self.http_get_json(f"{self.gamma_url}/markets/slug/{slug}", None)
        return data if isinstance(data, dict) else None

    async def _assemble(self, gamma: dict[str, Any]) -> DiscoveredMarket:
        condition_id = str(gamma.get("conditionId") or "")
        clob_compact: dict[str, Any] | None = None
        if condition_id:
            data = await self.http_get_json(
                f"{self.clob_url}/clob-markets/{condition_id}", None
            )
            if isinstance(data, dict):
                clob_compact = data
        return extract_market(gamma, clob_compact, now_epoch=self.clock())

    def _is_updown_slug(self, slug: str) -> bool:
        """Reconhece os DOIS padrões de slug (API_NOTES 12.1 e 12.2)."""
        if not slug:
            return False
        lowered = slug.lower()
        for asset in self.assets:
            short = asset.lower()
            long_name = ASSET_LONG_NAME.get(short, short)
            if lowered.startswith(f"{short}-updown-"):
                return True
            if lowered.startswith(f"{long_name}-up-or-down-") and lowered.endswith("-et"):
                return True
        return False

    async def _iter_keyset_updown(self, page_limit: int = 100, max_pages: int = 20) -> Any:
        """Itera /markets/keyset filtrando updown dos ativos configurados.

        Anti-zumbi (12.12): a query já restringe end_date a [agora, +2h] —
        closed=false isolado NÃO é confiável (há janelas de 2025 abertas).
        O checque local de endDate em extract_market continua valendo.
        """
        now = self.clock()
        end_min = datetime.fromtimestamp(now, tz=UTC).isoformat()
        end_max = datetime.fromtimestamp(now + 2 * 3600, tz=UTC).isoformat()
        cursor: str | None = None
        for _ in range(max_pages):
            params: dict[str, Any] = {
                "limit": page_limit,
                "closed": "false",
                "end_date_min": end_min,
                "end_date_max": end_max,
            }
            if cursor:
                params["after_cursor"] = cursor
            payload = await self.http_get_json(f"{self.gamma_url}/markets/keyset", params)
            if not isinstance(payload, dict):
                return
            for market in payload.get("markets") or []:
                if not isinstance(market, dict):
                    continue
                if self._is_updown_slug(str(market.get("slug") or "")):
                    yield market
            cursor = payload.get("next_cursor")
            if not cursor or cursor == END_CURSOR:
                return
