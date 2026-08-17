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
  stale_after_seconds_twap: 7.5
ui:
  port: 9090
""",
    )
    settings = Settings.load(config)
    assert settings.assets == ["btc"]
    assert settings.user_agent == "teste/1.0"
    assert settings.feeds.stale_after_seconds_twap == 7.5
    assert settings.ui.port == 9090


def test_watchdog_por_tipo_de_feed():
    """Limiar único de 2s era bug: p99 do TWAP medido é 2,47s (API_NOTES 13.2)."""
    feeds = Settings.load("inexistente.yaml").feeds
    assert feeds.stale_after_seconds_twap == 5.0
    assert feeds.stale_after_seconds_spot == 3.0
    assert feeds.stale_after_seconds_book == 30.0
    # Cada limiar precisa ficar ACIMA do p99 medido do seu feed.
    assert feeds.stale_after_seconds_twap > 2.47
    assert feeds.stale_after_seconds_spot > 1.20


def test_env_vence_yaml(tmp_path, monkeypatch):
    config = _write_config(tmp_path, "mode: SIM\nuser_agent: do-yaml\n")
    monkeypatch.setenv("PULSEARB_MODE", "shadow")
    monkeypatch.setenv("PULSEARB_USER_AGENT", "do-env")
    settings = Settings.load(config)
    assert settings.mode is Mode.SHADOW
    assert settings.user_agent == "do-env"


def test_env_aninhada_vence_yaml(tmp_path, monkeypatch):
    """PULSEARB_RECORDER__OUTPUT_DIR precisa sobrepor o config.yaml.

    É o que o Dockerfile usa para mandar as gravações para o volume. Sem
    isto a imagem grava no caminho do YAML, em silêncio.
    """
    config = _write_config(
        tmp_path, "recorder:\n  output_dir: do-yaml\n  rotate_seconds: 3600\n"
    )
    assert Settings.load(config).recorder.output_dir == "do-yaml"
    monkeypatch.setenv("PULSEARB_RECORDER__OUTPUT_DIR", "/data")
    settings = Settings.load(config)
    assert settings.recorder.output_dir == "/data"
    # e o que a env NÃO cobre continua vindo do default do modelo
    assert settings.recorder.rotate_seconds == 3600


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
