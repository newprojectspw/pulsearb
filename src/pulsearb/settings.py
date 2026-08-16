"""Configuração do PULSEARB: config.yaml (parâmetros) + .env (segredos/modo).

Precedência, da mais fraca para a mais forte:
  defaults do código < config.yaml < variáveis de ambiente (PULSEARB_*) < .env

Segredos nunca vão para o config.yaml; parâmetros de estratégia nunca vão
para o .env. O .env real é coberto pelo .gitignore.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Mode(StrEnum):
    SIM = "SIM"
    SHADOW = "SHADOW"
    LIVE = "LIVE"


class Endpoints(BaseModel):
    """Endpoints de produção — verificados em docs/API_NOTES.md seção 2."""

    gamma: str = "https://gamma-api.polymarket.com"
    clob: str = "https://clob.polymarket.com"
    clob_market_ws: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    rtds_ws: str = "wss://ws-live-data.polymarket.com"


class FeedSettings(BaseModel):
    stale_after_seconds: float = 2.0
    reconnect_initial_seconds: float = 0.5
    reconnect_max_seconds: float = 30.0
    clob_ping_interval_seconds: float = 10.0
    clob_stale_seconds: float = 30.0


class RecorderSettings(BaseModel):
    output_dir: str = "data/recordings"
    rotate_seconds: int = 3600


class UiSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8080


class Settings(BaseSettings):
    """Configuração completa. Instancie com Settings.load()."""

    model_config = SettingsConfigDict(
        env_prefix="PULSEARB_",
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    mode: Mode = Mode.SIM

    # Ativos a OPERAR (M4+) vs. ativos só de gravação de preço.
    assets: list[str] = Field(default_factory=lambda: ["btc", "eth"])
    extra_price_assets: list[str] = Field(default_factory=list)

    # Durações: "auto" = dirigidas por dados (API_NOTES 12.2). Nunca fixas.
    durations: str = "auto"
    # Grade de sondagem para gerar slugs candidatos quando durations=auto.
    probe_durations_seconds: list[int] = Field(default_factory=lambda: [300, 900, 14400])

    # Cloudflare: sem User-Agent explícito = 403 error 1010 (API_NOTES 12.10).
    user_agent: str = "Mozilla/5.0 (X11; Linux x86_64) pulsearb/0.1"

    endpoints: Endpoints = Field(default_factory=Endpoints)
    feeds: FeedSettings = Field(default_factory=FeedSettings)
    recorder: RecorderSettings = Field(default_factory=RecorderSettings)
    ui: UiSettings = Field(default_factory=UiSettings)

    @field_validator("durations")
    @classmethod
    def _durations_only_auto(cls, value: str) -> str:
        # Guard-rail consciente: qualquer tentativa de fixar durações no config
        # deve falhar alto, não ser aceita em silêncio.
        if value != "auto":
            raise ValueError(
                "durations só aceita 'auto' — durações são descobertas por dados, "
                "nunca fixadas (docs/API_NOTES.md seção 12.2)"
            )
        return value

    @field_validator("mode", mode="before")
    @classmethod
    def _mode_upper(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value

    @property
    def all_price_assets(self) -> list[str]:
        """Ativos cujo preço é assinado/gravado: operáveis + extras, sem duplicata."""
        seen: dict[str, None] = {}
        for asset in [*self.assets, *self.extra_price_assets]:
            seen.setdefault(asset.lower())
        return list(seen)

    @classmethod
    def load(cls, config_path: str | Path = "config.yaml", **overrides: Any) -> Settings:
        """Carrega config.yaml e deixa o pydantic-settings aplicar .env/ambiente.

        Valores do YAML entram como defaults (init kwargs perdem para env vars?
        Não: no pydantic-settings, init kwargs GANHAM de env. Por isso o YAML é
        aplicado como default de classe via _yaml_defaults, e overrides
        explícitos de código continuam com a palavra final).
        """
        yaml_data: dict[str, Any] = {}
        path = Path(config_path)
        if path.exists():
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                yaml_data = loaded
        # Env deve vencer YAML: remove do YAML as chaves presentes no ambiente.
        import os

        for key in list(yaml_data):
            if f"PULSEARB_{key.upper()}" in os.environ:
                del yaml_data[key]
        return cls(**{**yaml_data, **overrides})
