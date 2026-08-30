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


class TestOBotaoDeParada:
    """3.11 — o botão de emergência no dashboard.

    A chave já existia como ARQUIVO desde o M4.4, lida a cada ordem pelo
    portão. O que faltava era poder puxá-la sem uma sessão ssh — que é
    justamente a situação em que ela mais importa.
    """

    def _cliente(self, tmp_path):
        estado = DashboardState(mode="SHADOW", caminho_do_kill=tmp_path / "KILL")
        return TestClient(create_app(estado)), estado

    def test_o_botao_cria_o_arquivo_que_o_portao_le(self, tmp_path):
        """O acoplamento que faz o botão valer alguma coisa: é o MESMO
        arquivo que `PortaoDeRisco` consulta a cada ordem."""
        from pulsearb.risk import PortaoDeRisco
        from pulsearb.settings import Mode, RiskSettings

        cliente, _ = self._cliente(tmp_path)
        portao = PortaoDeRisco(
            RiskSettings(), Mode.SHADOW, caminho_do_kill=tmp_path / "KILL"
        )
        assert portao._kill_acionado() is False

        assert cliente.post("/api/kill").json()["ok"] is True

        assert portao._kill_acionado() is True

    def test_apertar_duas_vezes_nao_e_erro(self, tmp_path):
        """Quem aperta o botão num momento de aperto aperta duas vezes."""
        cliente, _ = self._cliente(tmp_path)

        assert cliente.post("/api/kill").json()["ok"] is True
        assert cliente.post("/api/kill").json()["ok"] is True

    def test_o_estado_diz_se_a_chave_esta_puxada(self, tmp_path):
        cliente, _ = self._cliente(tmp_path)

        assert cliente.get("/api/state").json()["kill"]["acionado"] is False
        cliente.post("/api/kill")
        assert cliente.get("/api/state").json()["kill"]["acionado"] is True

    def test_a_chave_puxada_POR_FORA_aparece_na_pagina(self, tmp_path):
        """O `touch KILL` numa sessão ssh não passa por esta página, e a
        página não pode dizer que está tudo bem."""
        cliente, _ = self._cliente(tmp_path)

        (tmp_path / "KILL").touch()

        assert cliente.get("/api/state").json()["kill"]["acionado"] is True

    def test_NAO_existe_rota_que_desarme(self, tmp_path):
        """A assimetria é deliberada, e o teste é o que a preserva.

        Duas razões apontando para o mesmo lado: o que para o bot fica parado
        até uma PESSOA desarmar à mão; e este dashboard não tem autenticação,
        então uma rota que desarma seria uma rota que qualquer um na rede usa
        para religar um bot parado de propósito.
        """
        cliente, _ = self._cliente(tmp_path)
        cliente.post("/api/kill")

        for metodo, rota in (
            ("delete", "/api/kill"),
            ("post", "/api/kill/desarmar"),
            ("post", "/api/desarmar"),
        ):
            resposta = getattr(cliente, metodo)(rota)
            assert resposta.status_code in (404, 405), f"{metodo} {rota}"

        assert (tmp_path / "KILL").exists()

    def test_sem_caminho_configurado_o_botao_recusa_com_motivo(self):
        """Botão morto que finge funcionar é pior que botão ausente."""
        cliente = TestClient(create_app(DashboardState()))

        dado = cliente.post("/api/kill").json()

        assert dado["ok"] is False
        assert "caminho" in dado["erro"]
        assert cliente.get("/api/state").json()["kill"]["disponivel"] is False

    def test_erro_de_leitura_conta_como_ACIONADA(self, tmp_path, monkeypatch):
        """A mesma regra do portão: entre supor que ninguém puxou a chave e
        supor que alguém puxou e o disco não deixa conferir, a segunda é a
        que não perde dinheiro por engano."""
        estado = DashboardState(caminho_do_kill=tmp_path / "KILL")

        def _explode(_self):
            raise OSError("disco indisponivel")

        monkeypatch.setattr("pathlib.Path.exists", _explode)

        assert estado.kill_acionado() is True

    def test_a_pagina_traz_o_botao(self):
        cliente = TestClient(create_app(DashboardState()))

        pagina = cliente.get("/").text

        assert "PARAR TUDO" in pagina
        assert "/api/kill" in pagina
