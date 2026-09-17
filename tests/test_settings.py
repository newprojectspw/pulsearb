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


def test_ticks_do_microprice_negativo_e_recusado_no_carregamento(tmp_path, monkeypatch):
    """O laço maker roda como tarefa PRÓPRIA: um defeito nele sai no log sem
    derrubar a rodada. Um valor negativo no ambiente mataria em silêncio a
    rota inteira por 14 dias, com o processo vivo e o operador convencido de
    que está medindo (revisão do Codex, #127). O lugar de recusar é aqui."""
    monkeypatch.setenv("PULSEARB_MAKER_TICKS_ABAIXO_DO_MICROPRICE", "-1")
    with pytest.raises(ValueError):
        Settings.load(tmp_path / "inexistente.yaml")

    monkeypatch.setenv("PULSEARB_MAKER_TICKS_ABAIXO_DO_MICROPRICE", "0")
    assert Settings.load(tmp_path / "inexistente.yaml").maker_ticks_abaixo_do_microprice == 0


# ── todo teto de risco tem de estar ESCRITO no config.yaml versionado ─────────
#
# Auditoria de 2026-09-17, §2.8: nenhum dos treze campos de `RiskSettings`
# aparecia no `config.yaml`; todos valiam o default do código. Quem abria o
# arquivo para saber quanto o bot pode perder por dia não achava a resposta,
# e quem mudava o default no código mudava o teto sem tocar no arquivo que
# deveria ser a decisão. Este teste não pina os VALORES — subir um teto é
# decisão legítima, e o lugar dela é o yaml — só exige que cada campo exista
# ali, para que a decisão seja visível.


def test_todo_teto_de_risco_esta_escrito_no_config_yaml():
    from pathlib import Path

    import yaml

    from pulsearb.settings import RiskSettings

    raiz = Path(__file__).resolve().parent.parent
    cfg = yaml.safe_load((raiz / "config.yaml").read_text(encoding="utf-8"))
    escritos = set((cfg.get("risk") or {}).keys())
    esperados = set(RiskSettings.model_fields)

    faltam = sorted(esperados - escritos)
    assert not faltam, (
        f"tetos de risco sem linha no config.yaml: {faltam}. Escreva-os na seção "
        "`risk:` (com o valor que valer) — o teto de dinheiro é decisão do "
        "arquivo versionado, não default escondido no código."
    )
    sobram = sorted(escritos - esperados)
    assert not sobram, f"chaves em `risk:` que RiskSettings não conhece: {sobram}"


def test_o_config_yaml_versionado_carrega_e_os_tetos_batem_com_o_que_esta_escrito():
    """Se o yaml diz 25, `Settings.load` tem de devolver 25 — e não o default
    por um erro de nome de chave que o pydantic ignoraria em silêncio."""
    from pathlib import Path

    import yaml

    from pulsearb.settings import Settings

    raiz = Path(__file__).resolve().parent.parent
    cfg = yaml.safe_load((raiz / "config.yaml").read_text(encoding="utf-8"))
    s = Settings.load(str(raiz / "config.yaml"))

    for campo, valor in cfg["risk"].items():
        assert getattr(s.risk, campo) == valor, campo
