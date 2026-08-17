"""Observabilidade (logging JSON, histogramas) e o stub do dashboard."""

from __future__ import annotations

import json
import logging
import math

from fastapi.testclient import TestClient

from pulsearb.obs.latency import LatencyHistogram
from pulsearb.obs.logging import JsonFormatter, get_logger, setup_logging
from pulsearb.ui.server import DashboardState, FeedStatus, create_app


# ------------------------------------------------------------------ logging
def test_log_e_json_de_uma_linha(capsys):
    setup_logging(logging.INFO)
    get_logger("teste").info("subiu", modo="SIM", janelas=3)
    captured = capsys.readouterr().out.strip()
    assert "\n" not in captured
    entry = json.loads(captured)
    assert entry["msg"] == "subiu"
    assert entry["modo"] == "SIM"
    assert entry["janelas"] == 3
    assert entry["nivel"] == "INFO"
    assert entry["ts_ns"] > 0


def test_nenhum_segredo_em_log(capsys):
    setup_logging(logging.INFO)
    get_logger("teste").info(
        "auth",
        api_key="SEGREDO",
        api_secret="SEGREDO",
        passphrase="SEGREDO",
        private_key="SEGREDO",
        slug="btc-updown-5m-1",
    )
    entry = json.loads(capsys.readouterr().out.strip())
    for campo in ("api_key", "api_secret", "passphrase", "private_key"):
        assert entry[campo] == "[REDIGIDO]"
    assert entry["slug"] == "btc-updown-5m-1"  # o que não é segredo, passa


def test_excecao_vai_para_o_json():
    record = logging.LogRecord("t", logging.ERROR, "f", 1, "falhou", None, None)
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record.exc_info = sys.exc_info()
    entry = json.loads(JsonFormatter().format(record))
    assert "ValueError: boom" in entry["exc"]


# --------------------------------------------------------------- histograma
def test_histograma_vazio():
    hist = LatencyHistogram("tick_para_decisao")
    assert hist.count == 0
    assert math.isnan(hist.percentile_us(50))


def test_histograma_percentis():
    hist = LatencyHistogram("x")
    for _ in range(99):
        hist.observe_ns(150_000)  # 150µs → bucket 200
    hist.observe_ns(3_000_000)  # 3ms → bucket 5000
    assert hist.count == 100
    assert hist.percentile_us(50) == 200.0
    assert hist.percentile_us(99) == 200.0
    assert hist.percentile_us(100) == 5000.0


def test_histograma_overflow():
    hist = LatencyHistogram("x")
    hist.observe_ns(60_000_000_000)  # 60s: acima do último bucket
    assert hist.count == 1
    assert hist.percentile_us(50) == float("inf")
    assert hist.snapshot()["overflow"] == 1


def test_snapshot_tem_o_essencial():
    hist = LatencyHistogram("decisao_para_ack")
    hist.observe_ns(1_000)
    snapshot = hist.snapshot()
    assert snapshot["nome"] == "decisao_para_ack"
    assert snapshot["n"] == 1


# ---------------------------------------------------------------- dashboard
def test_index_serve_html():
    client = TestClient(create_app(DashboardState()))
    response = client.get("/")
    assert response.status_code == 200
    assert "PULSEARB" in response.text
    assert "<script>" in response.text


def test_api_state_reflete_o_estado():
    state = DashboardState(mode="SIM")
    state.feeds["rtds"] = FeedStatus(
        connected=True, stale=False, message_count=42, last_message_age_s=0.3
    )
    state.counters["janelas"] = 7
    client = TestClient(create_app(state))
    payload = client.get("/api/state").json()
    assert payload["mode"] == "SIM"
    assert payload["feeds"]["rtds"]["message_count"] == 42
    assert payload["feeds"]["rtds"]["stale"] is False
    assert payload["counters"]["janelas"] == 7


def test_feed_sem_mensagem_serializa_sem_infinito():
    """inf não é JSON válido — vira null."""
    state = DashboardState()
    state.feeds["rtds"] = FeedStatus()
    client = TestClient(create_app(state))
    payload = client.get("/api/state").json()
    assert payload["feeds"]["rtds"]["last_message_age_s"] is None


def test_websocket_empurra_snapshot():
    state = DashboardState(mode="SIM")
    state.counters["ticks"] = 1
    client = TestClient(create_app(state))
    with client.websocket_connect("/ws") as ws:
        payload = json.loads(ws.receive_bytes())
        assert payload["mode"] == "SIM"
        assert payload["counters"]["ticks"] == 1
