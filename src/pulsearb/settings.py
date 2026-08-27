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
    # ─────────────────────────── M2.7: a gravação estava cega metade do tempo
    # 8h de gravação real mediram 163.195s de silêncio do RTDS: 48 casos de
    # tópico mudo com a conexão VIVA e 6 de conexão inteira muda, a maior de
    # 3.796s. São dois fenômenos distintos, e cada default abaixo ataca um.
    #
    # WATCHDOG DE AUSÊNCIA DE DADOS — cobre a conexão inteira muda. O
    # ping/pong do M2.1 prova que o cano está aberto, não que a água está
    # passando: o servidor responde PING e a conexão fica aberta e muda para
    # sempre. 30s são ~12x o p99 de cadência medido (2,47s, API_NOTES 13.1) e
    # ~35x o p50 — nunca se observou lacuna legítima dessa ordem. O custo de
    # um falso positivo é uma reconexão (~1s); o de um falso negativo foi
    # medido em 3.796s de cegueira.
    rtds_sem_dados_timeout_s: float = 30.0
    # TÓPICO MUDO — cobre a assinatura caducando. O watchdog acima NÃO pega
    # este caso, porque conta qualquer mensagem e o outro tópico continuava
    # chegando. 15s são ~6x o p99 de cadência; com o passo de verificação de
    # 5s, a reação sai em no máximo 20s.
    rtds_topico_mudo_s: float = 15.0
    # REASSINATURA PERIÓDICA — seguro barato, não o mecanismo principal. A
    # aritmética: 6 caducidades/h x até 300s de cegueira cada seriam 1.800s/h
    # contra a meta de 60s/h, então o relógio sozinho não cumpre a meta —
    # quem cumpre é a reação por tópico mudo. Isto cobre a caducidade que não
    # produz silêncio observável e custa um frame de texto a cada 5 min.
    rtds_reassinatura_intervalo_s: float = 300.0
    # ESCALADA (M2.11) — quantas reassinaturas urgentes seguidas antes de
    # derrubar o socket. A gravacao de 2026-08-22 fez 2.482 reassinaturas, uma
    # a cada 5s, e a cobertura da serie da ancora ficou em 8,1% em duas horas
    # cheias: reassinar deixou de ser resposta muito antes da tentativa 2.482.
    # 3 tentativas com passo de 5s = ~15s insistindo, ~30s de cegueira total
    # somando o limiar de topico mudo. Contra os 997s observados num unico
    # alarme, e outra ordem de grandeza.
    rtds_reassinaturas_ate_derrubar: int = 3


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


class RiskSettings(BaseModel):
    """Os tetos. Todos em USDC, todos pequenos de proposito.

    Estes numeros nao sao chute de conforto: saem do que o M2 mediu. O
    backtest de 20h moveu 2,91 USDC por trade e ganhou 0,18; a profundidade
    mediana a 3 ticks na duracao mais liquida foi de 87,8 USDC. Operar
    acima disso e apostar contra uma medicao que ja existe.

    O default e o menor conjunto de numeros com que faz sentido ligar o bot
    com dinheiro real. Subir qualquer um deles e uma decisao consciente, e
    deve vir depois de a curva de capacidade (M2.14) dizer onde o teto esta.
    """

    #: Teto por ordem. 5 USDC e o valor que o projeto carrega desde o M1 —
    #: pequeno o bastante para uma sequencia de erros custar menos que uma
    #: pizza, e grande o bastante para o resultado nao ser so ruido de taxa.
    stake_max_por_trade_usdc: float = 5.0
    #: Teto por janela de mercado. Tres entradas de 5 no mesmo mercado ainda
    #: sao uma aposta so no mesmo movimento — o teto por trade nao cobre isso.
    stake_max_por_janela_usdc: float = 15.0
    #: Teto de capital simultaneamente em risco, somando todas as janelas.
    exposicao_max_usdc: float = 50.0
    #: Quantas janelas podem ter posicao aberta ao mesmo tempo.
    posicoes_max_abertas: int = 5
    #: Disjuntor. Ao estourar, ele GRUDA — nao desarma sozinho no dia
    #: seguinte, e sobrevive a reinicio do processo.
    perda_max_diaria_usdc: float = 25.0
    #: Faixa de preco em que se aceita operar. Fora dela o payoff assimetrico
    #: transforma um erro de modelo em perda desproporcional: comprar a 0,97
    #: arrisca 0,97 para ganhar 0,03.
    preco_minimo: float = 0.05
    preco_maximo: float = 0.95
    #: Sequencia de perdas que dispara a pausa. Nao e superticao sobre
    #: "maré": e o menor sinal barato de que o modelo parou de valer para o
    #: regime atual. Quatro seguidas com hit rate de 0,59 tem probabilidade
    #: de ~2,8% de acontecer por acaso — raro o bastante para investigar,
    #: comum o bastante para nao travar o bot a semana inteira.
    perdas_seguidas_para_pausa: int = 4
    #: Quanto dura a pausa. Uma hora cobre a janela mais longa (4h nao, mas
    #: as de 5m/15m/1h sim) e devolve o bot ao mercado no mesmo dia.
    pausa_apos_sequencia_s: float = 3600.0
    #: Spread acima do qual nao se opera. NAO e chute: o criterio 1.1 exige
    #: edge >= 0,02, e o taker paga meio spread contra o meio do livro. Com
    #: spread de 0,04 o custo de atravessar iguala o edge exigido, e o trade
    #: nao pode ganhar por construcao.
    spread_maximo: float = 0.04
    #: Onde o registro do dia mora. Precisa sobreviver a reinicio.
    caminho_do_registro: str = "data/risco/registro_do_dia.json"
    #: A chave de emergencia. Enquanto este arquivo existir, nenhuma ordem
    #: passa. E arquivo, e nao flag, porque tem de poder ser acionada por
    #: alguem que nao consegue falar com o processo — inclusive por `touch`
    #: numa sessao ssh com o bot travado.
    caminho_do_kill: str = "data/risco/KILL"


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
    risk: RiskSettings = Field(default_factory=RiskSettings)

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
