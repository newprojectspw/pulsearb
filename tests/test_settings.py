"""Settings: YAML + env, precedência e guard-rails."""

import pytest

from pulsearb.settings import Mode, Settings


def _write_config(tmp_path, content: str):
    path = tmp_path / "config.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_defaults_sem_arquivo(tmp_path):
    settings = Settings.load(tmp_path / "inexistente.yaml")
    assert settings.mode is Mode.SIM
    assert settings.assets == ["btc", "eth"]
    assert settings.durations == "auto"
    assert settings.endpoints.clob == "https://clob.polymarket.com"


def test_carrega_yaml(tmp_path):
    config = _write_config(
        tmp_path,
        """
assets: [btc]
extra_price_assets: [sol, xrp]
user_agent: "teste/1.0"
feeds:
  stale_after_seconds: 3.5
ui:
  port: 9090
""",
    )
    settings = Settings.load(config)
    assert settings.assets == ["btc"]
    assert settings.user_agent == "teste/1.0"
    assert settings.feeds.stale_after_seconds == 3.5
    assert settings.ui.port == 9090


def test_env_vence_yaml(tmp_path, monkeypatch):
    config = _write_config(tmp_path, "mode: SIM\nuser_agent: do-yaml\n")
    monkeypatch.setenv("PULSEARB_MODE", "shadow")
    monkeypatch.setenv("PULSEARB_USER_AGENT", "do-env")
    settings = Settings.load(config)
    assert settings.mode is Mode.SHADOW
    assert settings.user_agent == "do-env"


def test_override_de_codigo_vence_tudo(tmp_path, monkeypatch):
    config = _write_config(tmp_path, "mode: SIM\n")
    monkeypatch.setenv("PULSEARB_MODE", "shadow")
    settings = Settings.load(config, mode="SIM")
    assert settings.mode is Mode.SIM


def test_duracao_fixa_e_recusada(tmp_path):
    # Guard-rail do API_NOTES 12.2: durações nunca hardcoded.
    config = _write_config(tmp_path, "durations: [300, 900]\n")
    with pytest.raises(Exception):  # noqa: B017 — ValidationError do pydantic
        Settings.load(config)


def test_all_price_assets_sem_duplicata(tmp_path):
    config = _write_config(
        tmp_path, "assets: [btc, eth]\nextra_price_assets: [sol, BTC, xrp]\n"
    )
    settings = Settings.load(config)
    assert settings.all_price_assets == ["btc", "eth", "sol", "xrp"]


def test_modo_case_insensitive(tmp_path):
    settings = Settings.load(tmp_path / "x.yaml", mode="shadow")
    assert settings.mode is Mode.SHADOW
