"""Helpers de teste. Nenhum teste toca rede externa (regra do M1)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def gamma_a1() -> dict[str, Any]:
    """Anexo A1: mercado 5m real, SEM feeSchedule (exercita o gate de fee)."""
    return load_fixture("gamma_market_btc_updown_5m.json")


@pytest.fixture
def gamma_fee() -> dict[str, Any]:
    """Segundo mercado do A1, com feeSchedule completo."""
    return load_fixture("gamma_market_with_feeschedule.json")


@pytest.fixture
def gamma_zombie() -> dict[str, Any]:
    """Mercado-zumbi (API_NOTES 12.12): closed=false mas morto desde 2025."""
    return load_fixture("gamma_market_zombie.json")


@pytest.fixture
def gamma_hourly() -> dict[str, Any]:
    """Janela de 1h: slug nominal em NY, resolução por candle Binance (12.2)."""
    return load_fixture("gamma_market_hourly_binance.json")


@pytest.fixture
def gamma_hourly_current() -> dict[str, Any]:
    """Anexo A3: a janela horária ATUAL (variante COM ano)."""
    return load_fixture("gamma_market_hourly_current.json")


@pytest.fixture
def gamma_stale_slug() -> dict[str, Any]:
    """Slug pedido resolve com 200 numa janela homônima de 2025 (12.12b)."""
    return load_fixture("gamma_market_stale_slug_resolution.json")


@pytest.fixture
def clob_a2() -> dict[str, Any]:
    """Anexo A2: resposta íntegra do CLOB compacto."""
    return load_fixture("clob_market_compact.json")


@pytest.fixture
def rtds_events() -> dict[str, Any]:
    return load_fixture("rtds_events.json")


@pytest.fixture
def clob_ws_events() -> dict[str, Any]:
    return load_fixture("clob_ws_book.json")


# Época "agora" dos testes de descoberta: meio da janela 5m do anexo A1
# (início 1786891500, fim 1786891800).
NOW_EPOCH_TESTES = 1786891600
