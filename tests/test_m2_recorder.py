"""Recorder de produção: rotação de assinatura e snapshot da descoberta.

A rotação é o que mantém o recorder vivo por 72h: janelas de 5m nascem a cada
5 minutos, e sem desassinar as encerradas a conexão acumularia centenas de
assinaturas mortas.
"""

from __future__ import annotations

import gzip
import json
from collections import Counter

import pytest
from tests.test_feeds_ws import _wait_for, server  # noqa: F401

from pulsearb.markets.discovery import DiscoveredMarket, ResolutionKind
from pulsearb.recorder.__main__ import Recorder, market_snapshot
from pulsearb.settings import Settings


def _janela(indice: int) -> DiscoveredMarket:
    return DiscoveredMarket(
        slug=f"btc-updown-5m-{indice}",
        condition_id=f"0x{indice}",
        asset="btc",
        resolution=ResolutionKind.TWAP60,
        token_id_by_outcome={"Up": f"up{indice}", "Down": f"dn{indice}"},
        tick_size=0.01,
        min_order_size=5,
        fee_rate=0.07,
        fee_exponent=1,
        fee_taker_only=True,
        fee_rebate_rate=0.2,
        accepting_orders=True,
        end_date_iso="2026-08-16T15:00:00Z",
        operable=True,
        raw_gamma={"umaReward": "0.6", "rewardsMinSize": 50, "rewardsMaxSpread": 1.5},
    )


class FakeDiscovery:
    """Devolve as janelas de cada ciclo, em sequência."""

    def __init__(self, ciclos: list[list[DiscoveredMarket]]) -> None:
        self.ciclos = ciclos
        self.n = 0

    async def discover(self) -> list[DiscoveredMarket]:
        janelas = self.ciclos[min(self.n, len(self.ciclos) - 1)]
        self.n += 1
        return janelas


def test_snapshot_carrega_o_que_o_m2_precisa():
    snapshot = market_snapshot(_janela(1))
    # tick_size é ESTADO (API_NOTES 13.3): sem ele não há medição M2.E.1
    assert snapshot["tick_size"] == 0.01
    # fee do dado, para o backtest não usar constante
    assert snapshot["fee_rate"] == 0.07 and snapshot["fee_exponent"] == 1
    # umaReward: o indício de resolução via UMA no jogo horário
    assert snapshot["uma_reward"] == "0.6"
    # rewards: insumo da medição da rota maker (M2.E.4)
    assert snapshot["rewards_min_size"] == 50
    assert snapshot["rewards_max_spread"] == 1.5
    assert snapshot["end_date_iso"]


@pytest.fixture
def recorder(server, tmp_path):  # noqa: F811
    settings = Settings.load("config.yaml")
    settings.endpoints.rtds_ws = server.url
    settings.endpoints.clob_market_ws = server.url
    settings.recorder.output_dir = str(tmp_path)
    rec = Recorder(settings)
    rec.binance.url = server.url
    return rec


async def test_rotacao_de_assinatura(recorder, tmp_path):
    """Janela encerrada é DESASSINADA — senão a conexão acumula por 72h."""
    fake = FakeDiscovery([[_janela(1), _janela(2)], [_janela(2)]])
    await recorder.writer.start()
    await recorder.poly.start()
    try:
        await _wait_for(lambda: recorder.poly.connected)

        await recorder._discovery_cycle(fake)
        assert set(recorder.poly.token_ids) == {"up1", "dn1", "up2", "dn2"}

        await recorder._discovery_cycle(fake)
        # a janela 1 sumiu da descoberta: suas assinaturas caem junto
        assert set(recorder.poly.token_ids) == {"up2", "dn2"}
    finally:
        await recorder.poly.stop()
        await recorder.writer.stop()


async def test_snapshot_e_gravado_a_cada_ciclo(recorder, tmp_path):
    fake = FakeDiscovery([[_janela(1), _janela(2)], [_janela(2)]])
    await recorder.writer.start()
    await recorder.poly.start()
    try:
        await _wait_for(lambda: recorder.poly.connected)
        await recorder._discovery_cycle(fake)
        await recorder._discovery_cycle(fake)
    finally:
        await recorder.poly.stop()
        await recorder.writer.stop()

    linhas = []
    for caminho in sorted(tmp_path.glob("*.jsonl.gz")):
        with gzip.open(caminho, "rb") as handle:
            linhas += [json.loads(linha) for linha in handle if linha.strip()]

    fontes = Counter(linha["fonte"] for linha in linhas)
    assert fontes["discovery_snapshot"] == 2

    snapshots = [item["payload"] for item in linhas if item["fonte"] == "discovery_snapshot"]
    assert snapshots[0]["assinaturas"]["novas"] == 4
    assert snapshots[0]["assinaturas"]["ativas"] == 4
    assert snapshots[1]["assinaturas"]["encerradas"] == 2
    assert len(snapshots[0]["janelas"]) == 2
    assert snapshots[0]["janelas"][0]["tick_size"] == 0.01
    # BUG 3: sem este campo a medição do tick não sabe em que fase da janela
    # o afinamento ocorreu, e todo seconds_left saía NaN.
    assert snapshots[0]["janelas"][0]["_seconds_left"] is not None


async def test_eventos_de_feed_chegam_ao_arquivo(recorder, server, tmp_path):  # noqa: F811
    server.to_send = [
        json.dumps(
            {
                "topic": "crypto_prices_twap_sixty",
                "type": "update",
                "timestamp": 1,
                "payload": {
                    "symbol": "btc/usd",
                    "timestamp": 1,
                    "value": 118432.17,
                    "full_accuracy_value": "118432170000000000000000",
                    "window_s": 60,
                },
            }
        )
    ]
    await recorder.writer.start()
    await recorder.rtds.start()
    try:
        await _wait_for(lambda: recorder.rtds.message_count >= 1)
    finally:
        await recorder.rtds.stop()
        await recorder.writer.stop()

    linhas = []
    for caminho in sorted(tmp_path.glob("*.jsonl.gz")):
        with gzip.open(caminho, "rb") as handle:
            linhas += [json.loads(linha) for linha in handle if linha.strip()]
    rtds = [item for item in linhas if item["fonte"] == "rtds"]
    assert rtds
    assert rtds[0]["payload"]["topic"] == "crypto_prices_twap_sixty"
    # os dois relógios foram capturados na chegada
    assert rtds[0]["ts_mono_ns"] > 0 and rtds[0]["ts_wall_ns"] > 0



# ------------------------------------------ carência de resolução (BUG 1)
def _janela_com_fim(indice: int, fim_epoch: float) -> DiscoveredMarket:
    from datetime import UTC, datetime

    janela = _janela(indice)
    janela.end_date_iso = (
        datetime.fromtimestamp(fim_epoch, tz=UTC).isoformat().replace("+00:00", "Z")
    )
    return janela


async def test_janela_recem_encerrada_fica_em_carencia(recorder, monkeypatch):
    """A janela sai da descoberta no endDate, mas a resolução vem DEPOIS.

    Desassinar na hora foi o que produziu 104 janelas e ZERO resoluções no
    primeiro backtest real.
    """
    import time as _time

    from pulsearb.recorder import __main__ as mod

    agora = _time.time()
    # Janela que acabou de encerrar: dentro da carência.
    viva = _janela_com_fim(1, agora + 300)
    recem_encerrada = _janela_com_fim(2, agora - 10)
    fake = FakeDiscovery([[viva, recem_encerrada], [viva]])

    await recorder.writer.start()
    await recorder.poly.start()
    try:
        await _wait_for(lambda: recorder.poly.connected)
        await recorder._discovery_cycle(fake)
        assert set(recorder.poly.token_ids) == {"up1", "dn1", "up2", "dn2"}

        await recorder._discovery_cycle(fake)
        # A janela 2 sumiu da descoberta, mas a carência ainda protege:
        # continuamos escutando para capturar a resolução.
        assert {"up2", "dn2"} <= set(recorder.poly.token_ids)
        assert recorder.desassinar_apos["up2"] > agora + mod.RESOLUTION_GRACE_SECONDS - 60
    finally:
        await recorder.poly.stop()
        await recorder.writer.stop()


async def test_carencia_vencida_desassina(recorder):
    """A carência é uma janela de tempo, não uma assinatura eterna."""
    import time as _time

    from pulsearb.recorder import __main__ as mod

    agora = _time.time()
    viva = _janela_com_fim(1, agora + 300)
    # Encerrada há mais tempo que a carência inteira.
    antiga = _janela_com_fim(2, agora - mod.RESOLUTION_GRACE_SECONDS - 60)
    fake = FakeDiscovery([[viva, antiga], [viva]])

    await recorder.writer.start()
    await recorder.poly.start()
    try:
        await _wait_for(lambda: recorder.poly.connected)
        await recorder._discovery_cycle(fake)
        await recorder._discovery_cycle(fake)
        assert set(recorder.poly.token_ids) == {"up1", "dn1"}
    finally:
        await recorder.poly.stop()
        await recorder.writer.stop()


async def test_resolucao_capturada_libera_a_assinatura(recorder, server):  # noqa: F811
    """Chegou a resolução? Não há por que continuar escutando aquele token."""
    import time as _time

    agora = _time.time()
    viva = _janela_com_fim(1, agora + 300)
    encerrada = _janela_com_fim(2, agora - 10)
    fake = FakeDiscovery([[viva, encerrada], [viva]])

    await recorder.writer.start()
    await recorder.poly.start()
    try:
        await _wait_for(lambda: recorder.poly.connected)
        await recorder._discovery_cycle(fake)
        # O evento de resolução chega pelo WS.
        recorder._contar_evento_poly(
            _evento_poly({"event_type": "market_resolved", "asset_id": "up2",
                          "winning_outcome": "Up"})
        )
        assert "up2" in recorder.resolvidos
        await recorder._discovery_cycle(fake)
        assert "up2" not in recorder.poly.token_ids
    finally:
        await recorder.poly.stop()
        await recorder.writer.stop()


def _evento_poly(payload: dict):
    from pulsearb.feeds.base import FeedEvent

    return FeedEvent(
        source="poly_ws", ts_mono_ns=1, ts_wall_ns=1,
        raw=json.dumps(payload).encode(), parsed=payload,
    )


def test_contagem_por_tipo_inclui_o_desconhecido(recorder):
    """Sem isto, "0 resoluções" não distingue não-chegou de foi-descartado."""
    recorder._contar_evento_poly(_evento_poly({"event_type": "book", "asset_id": "a"}))
    recorder._contar_evento_poly(_evento_poly({"event_type": "price_change", "asset_id": "a"}))
    recorder._contar_evento_poly(_evento_poly({"event_type": "tipo_novo_da_polymarket"}))
    recorder._contar_evento_poly(_evento_poly({"sem": "event_type"}))
    assert recorder.eventos_poly["book"] == 1
    assert recorder.eventos_poly["price_change"] == 1
    # o tipo que não conhecemos aparece pelo nome, não some
    assert recorder.eventos_poly["tipo_novo_da_polymarket"] == 1
    assert recorder.eventos_poly["__sem_event_type__"] == 1


def test_contagem_aceita_lote_em_array(recorder):
    recorder._contar_evento_poly(
        _evento_poly([{"event_type": "book", "asset_id": "a"},
                      {"event_type": "book", "asset_id": "b"}])
    )
    assert recorder.eventos_poly["book"] == 2
