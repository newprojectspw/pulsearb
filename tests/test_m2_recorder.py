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
    assert snapshots[0]["assinaturas"] == {"novas": 4, "encerradas": 0, "ativas": 4}
    assert snapshots[1]["assinaturas"]["encerradas"] == 2
    assert len(snapshots[0]["janelas"]) == 2
    assert snapshots[0]["janelas"][0]["tick_size"] == 0.01


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
