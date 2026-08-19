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
    """Watchdog por tipo de feed — cadências medidas, não chutadas.

    O M1 usava 2s para tudo. A medição ao vivo (API_NOTES 13.1) mostrou p99
    de 2,47s no TWAP: o limiar único marcaria o feed como parado em operação
    normal, e o bot pausaria entradas sem motivo. Cada feed tem o seu.
    """

    stale_after_seconds_twap: float = 5.0
    stale_after_seconds_spot: float = 3.0
    stale_after_seconds_book: float = 30.0
    reconnect_initial_seconds: float = 0.5
    reconnect_max_seconds: float = 30.0
    clob_ping_interval_seconds: float = 10.0
    clob_stale_seconds: float = 30.0
    # REDUNDÂNCIA DO RTDS (M2.2 A.5). Duas conexões ao mesmo endpoint,
    # consumindo a primeira mensagem que chegar e deduplicando por
    # (tópico, ativo, timestamp). Custa banda dobrada num feed de poucos KB/s
    # e cobre justamente a falha que apareceu em produção: reconexão do RTDS
    # em ciclos de 30-306s, com lacuna a cada ciclo.
    rtds_conexoes: int = 2
    # Teto da janela de deduplicação, em mensagens lembradas. Precisa cobrir a
    # diferença de chegada entre as duas conexões (milissegundos), não a
    # gravação inteira.
    rtds_dedup_janela: int = 20000


class RecorderSettings(BaseModel):
    output_dir: str = "data/recordings"
    rotate_seconds: int = 3600
    # Fila SEM PERDA dos eventos de livro (M2.2 A.1). Encher esta fila é
    # incidente, não descarte aceitável: um delta perdido corrompe o livro
    # reconstruído em silêncio.
    queue_max_book: int = 524288
    queue_max: int = 65536
    # Intervalo do laço que refaz a assinatura dos tokens marcados como
    # corrompidos, para forçar um snapshot novo do livro.
    resync_intervalo_s: float = 5.0


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
        # Env deve vencer YAML. Como o pydantic-settings dá precedência a
        # kwargs de init sobre variáveis de ambiente, a única forma de o env
        # ganhar é REMOVER do YAML a chave que ele cobre.
        #
        # Cobre os dois casos:
        #   PULSEARB_MODE                  → chave de topo `mode`
        #   PULSEARB_RECORDER__OUTPUT_DIR  → chave de topo `recorder` (aninhada)
        #
        # O caso aninhado importa de verdade: o Dockerfile define
        # PULSEARB_RECORDER__OUTPUT_DIR=/data, e sem isto a imagem gravaria no
        # caminho do config.yaml — em silêncio, que é o pior jeito de errar.
        import os

        for key in list(yaml_data):
            prefixo = f"PULSEARB_{key.upper()}"
            if any(
                nome == prefixo or nome.startswith(f"{prefixo}__")
                for nome in os.environ
            ):
                del yaml_data[key]
        return cls(**{**yaml_data, **overrides})
