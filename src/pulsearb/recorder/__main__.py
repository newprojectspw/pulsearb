"""Recorder de produção — grava a realidade para o M2 poder medi-la.

    python -m pulsearb.recorder --duration 72h

Fluxos gravados:

- **RTDS**: `crypto_prices_twap_sixty` (preço-verdade de 5m/15m/4h) e
  `crypto_prices` (spot Binance), de TODOS os ativos configurados
- **Binance direto**: `kline_1h` (preço-verdade das janelas horárias — o RTDS
  não entrega candles, e candle tem `open`, que tick nenhum reconstrói depois
  do fato) e `bookTicker`, para btc/eth
- **CLOB market WS**: book completo, price_change e eventos de resolução de
  TODAS as janelas descobertas — os dois jogos — com
  `custom_feature_enabled=true`
- **Snapshot da descoberta** a cada ciclo: metadados completos de cada janela,
  incluindo `tick_size` (para medir a mudança de tick, API_NOTES 13.3),
  `feeSchedule`, `endDate` e `acceptingOrders`

Rotatividade: janelas de 5m nascem e morrem a cada 5 minutos. O recorder
assina as novas e **desassina as encerradas** sem reiniciar, mantendo o número
de assinaturas estável.

Robustez: reconexão com backoff+jitter (herdada dos feeds); lacunas de
gravação registradas com duração e causa; ao encerrar, um relatório de
cobertura por fonte.

RODA NA VPS. O ambiente de desenvolvimento não alcança os endpoints — o que
se testa lá dentro é o pipeline contra servidores locais.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import re
import time
from collections import Counter
from typing import Any

import httpx
import orjson

from pulsearb.analysis.integrity import MonitorDeIntegridade, MonitorDeRelogio
from pulsearb.feeds.base import FeedEvent
from pulsearb.feeds.binance_ws import BinanceWsFeed
from pulsearb.feeds.poly_ws import (
    EVENTOS_DE_LIVRO,
    RESOLUTION_EVENT_TYPES,
    PolyMarketWsFeed,
    tokens_do_evento,
)
from pulsearb.feeds.rtds import RtdsFeed, parse_rtds_event
from pulsearb.markets.discovery import (
    DiscoveredMarket,
    MarketDiscovery,
    parse_end_date_epoch,
)
from pulsearb.obs import get_logger, setup_logging
from pulsearb.recorder.gaps import GapTracker, resumo_gaps
from pulsearb.recorder.writer import (
    CANAL_BOOK,
    CANAL_PADRAO,
    FONTE_DISCOVERY,
    FONTE_GAP,
    FONTE_RESOLUCAO_SINTETICA,
    FONTE_RESYNC,
    JsonlGzipWriter,
    RecordEnvelope,
)
from pulsearb.settings import Settings

log = get_logger("pulsearb.recorder.main")

# Janelas de 5m nascem o tempo todo; 30s é folgado o bastante para não
# martelar a Gamma e apertado o bastante para nunca perder o início de uma.
DISCOVERY_INTERVAL_SECONDS = 30.0
# Polling do watchdog de lacunas. Precisa ser bem menor que o menor limiar.
GAP_POLL_SECONDS = 1.0

# CARÊNCIA DE RESOLUÇÃO — a correção do bug que zerou o primeiro backtest.
# A janela sai da descoberta no endDate, mas o evento de resolução só é
# publicado DEPOIS (o M0 estimava ~2min; no jogo horário, com UMA no caminho,
# pode ser bem mais). Desassinar no endDate desligava a escuta antes do
# resultado existir: 104 janelas conhecidas, ZERO resoluções capturadas.
RESOLUTION_GRACE_SECONDS = 600.0
# Fallback: consultar a Gamma para janelas encerradas cuja resolução não
# chegou pelo WS. Independente do caminho do WS de propósito — se um falhar,
# o outro cobre.
RESOLUTION_POLL_SECONDS = 120.0

_DURATION_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*$", re.IGNORECASE)
_DURATION_UNITS = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0, "": 3600.0}


def parse_duration(text: str) -> float:
    """'72h' → 259200.0. Sem sufixo = horas (o uso mais comum aqui)."""
    match = _DURATION_PATTERN.match(text)
    if match is None:
        raise ValueError(f"duração inválida: {text!r} (use 90s, 30m, 72h, 7d)")
    return float(match.group(1)) * _DURATION_UNITS[match.group(2).lower()]


#: Onde a Gamma pode pôr a lista de rewards. `clobRewards` é o que se viu ao
#: vivo (§12.8); `rewards_config` é o nome no SDK. Aceitar os dois custa uma
#: linha; apostar no errado custou o marco inteiro no `price_change` (§6.1b).
CHAVES_DE_LISTA_DE_REWARD = ("clobRewards", "rewards_config", "rewardsConfig")

#: E como cada entrada pode chamar a taxa diária.
CHAVES_DE_TAXA_DIARIA = (
    "rewardsDailyRate",
    "rewards_daily_rate",
    "dailyRate",
    "daily_rate",
    "totalDailyRate",
    "total_daily_rate",
)


def _lista_de_rewards(gamma: dict[str, Any]) -> tuple[str | None, list[Any]]:
    """A lista de rewards e sob que chave ela veio."""
    for chave in CHAVES_DE_LISTA_DE_REWARD:
        bruto = gamma.get(chave)
        if isinstance(bruto, list):
            return chave, bruto
    return None, []


def _taxa_diaria_de_reward(gamma: dict[str, Any]) -> float | None:
    """Soma as taxas diárias da lista de rewards (VERIFICADO ao vivo, 12.8).

    É lista porque um mercado pode ter mais de uma fonte de reward (nativa e
    patrocinada, que o CLOB expõe como `native_daily_rate` e
    `sponsored_daily_rate`). Somar é o que corresponde ao `total_daily_rate`.

    M2.7: passou a aceitar as grafias alternativas da lista E da taxa. A
    gravação de 8h trouxe 199 janelas com `rewardsMinSize` e
    `rewardsMaxSpread` PRESENTES e taxa diária ausente — o que é estranho
    para mercado que não participa do programa, e é exatamente a assinatura
    de leitor procurando a chave errada. Aceitar as variantes não decide a
    questão sozinho: `forma_dos_rewards` conta qual apareceu de fato, e o
    array cru vai gravado para que a próxima rodada resolva sem palpite.
    """
    _, bruto = _lista_de_rewards(gamma)
    total = 0.0
    achou = False
    for item in bruto:
        if not isinstance(item, dict):
            continue
        for chave in CHAVES_DE_TAXA_DIARIA:
            taxa = _numero(item.get(chave))
            if taxa is not None:
                total += taxa
                achou = True
                break
    return total if achou else None


def _forma_dos_rewards(gamma: dict[str, Any]) -> dict[str, Any]:
    """O que a Gamma REALMENTE mandou sobre reward, sem interpretação.

    M2.7 tarefa 2. Três explicações produzem o mesmo `rewards_daily_rate:
    None`, e elas têm consertos opostos:

    1. **o mercado não participa** — a lista nem existe;
    2. **o nosso leitor erra a chave** — a lista existe e as entradas usam um
       nome de campo que não procurávamos (o defeito do `price_change` de
       novo, §6.1b);
    3. **o programa expirou para aquela janela** — a lista existe, tem taxa, e
       tem `start_date`/`end_date` fora do intervalo da janela.

    Nenhuma das três era distinguível na gravação anterior, porque
    `raw_gamma` nunca chegava ao disco: só três campos derivados chegavam. Sem
    o array cru gravado, a pergunta não tem resposta — e foi por isso que ela
    não teve resposta.
    """
    chave, bruto = _lista_de_rewards(gamma)
    entradas: list[dict[str, Any]] = [i for i in bruto if isinstance(i, dict)]
    return {
        "chave_da_lista": chave,
        "n_entradas": len(entradas),
        "chaves_das_entradas": sorted({k for item in entradas for k in item}),
        # O array CRU, do jeito que veio. É ele que carrega start_date/
        # end_date e permite decidir entre "expirou" e "não participa".
        "entradas": entradas[:4],
    }


def _numero(valor: Any) -> float | None:
    """Número vindo do fio: o CLOB manda timestamp ora int, ora string."""
    if isinstance(valor, bool) or valor is None:
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    if isinstance(valor, str):
        try:
            return float(valor)
        except ValueError:
            return None
    return None


def market_snapshot(
    market: DiscoveredMarket, *, agora_epoch: float | None = None
) -> dict[str, Any]:
    """Metadados da janela para o snapshot da descoberta.

    `tick_size` entra de propósito: é ESTADO, não constante (API_NOTES 13.3),
    e a série destes snapshots é o dado bruto da medição M2.E.1.

    `_seconds_left` é o tempo restante NO MOMENTO da observação. Sem ele a
    medição do tick não sabe em que fase da janela o afinamento aconteceu — e
    era exatamente o que faltava: o campo era lido pela análise mas nunca
    escrito aqui, então todo `seconds_left` saía NaN.
    """
    if agora_epoch is None:
        agora_epoch = time.time()
    fim = parse_end_date_epoch({"endDate": market.end_date_iso})
    return {
        "slug": market.slug,
        "condition_id": market.condition_id,
        "asset": market.asset,
        "resolution": market.resolution.value,
        "token_id_by_outcome": market.token_id_by_outcome,
        "tick_size": market.tick_size,
        "min_order_size": market.min_order_size,
        "fee_rate": market.fee_rate,
        "fee_exponent": market.fee_exponent,
        "fee_taker_only": market.fee_taker_only,
        "fee_rebate_rate": market.fee_rebate_rate,
        "accepting_orders": market.accepting_orders,
        "end_date_iso": market.end_date_iso,
        "operable": market.operable,
        "gate_failures": market.gate_failures,
        "_seconds_left": (fim - agora_epoch) if fim is not None else None,
        "_observado_em_epoch": agora_epoch,
        "rewards_min_size": market.raw_gamma.get("rewardsMinSize"),
        "rewards_max_spread": market.raw_gamma.get("rewardsMaxSpread"),
        # Orçamento do pool. Sem ele a simulação de reward (M2.2 B.1) não tem
        # numerador e a janela sai da conta em vez de receber um default
        # inventado.
        "rewards_daily_rate": _taxa_diaria_de_reward(market.raw_gamma),
        # M2.7: o CRU da lista de rewards. Sem ele, "sem_taxa_diaria" é
        # indistinguível entre não participar, expirar, e o nosso leitor
        # errar a chave — ver `_forma_dos_rewards`.
        "rewards_bruto": _forma_dos_rewards(market.raw_gamma),
        "uma_reward": market.raw_gamma.get("umaReward"),
        "best_bid": market.raw_gamma.get("bestBid"),
        "best_ask": market.raw_gamma.get("bestAsk"),
    }


class Recorder:
    """Orquestra feeds, descoberta, rotação de assinatura e gravação."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.writer = JsonlGzipWriter(
            output_dir=settings.recorder.output_dir,
            rotate_seconds=settings.recorder.rotate_seconds,
            queue_max=settings.recorder.queue_max,
            queue_max_book=settings.recorder.queue_max_book,
            ao_perder_book=self._on_perda_de_book,
        )
        # REDUNDÂNCIA (M2.2 A.5): N conexões ao MESMO endpoint do RTDS. A
        # primeira mensagem a chegar é gravada; as repetidas são contadas e
        # descartadas. Não é paranoia: a primeira gravação real teve o RTDS
        # reconectando em ciclos de 30 a 306 segundos, com lacuna a cada
        # ciclo, e o feed é de poucos KB/s — a banda dobrada é barata.
        self.rtds_feeds: list[RtdsFeed] = [
            RtdsFeed(
                url=settings.endpoints.rtds_ws,
                user_agent=settings.user_agent,
                assets=settings.all_price_assets,
                on_event=self._fazer_callback_rtds(indice),
                stale_after_seconds=settings.feeds.stale_after_seconds_twap,
                reconnect_initial_seconds=settings.feeds.reconnect_initial_seconds,
                reconnect_max_seconds=settings.feeds.reconnect_max_seconds,
                # M2.7: os dois mecanismos contra a cegueira do feed-verdade.
                sem_dados_timeout_s=settings.feeds.rtds_sem_dados_timeout_s,
                topico_mudo_s=settings.feeds.rtds_topico_mudo_s,
                reassinatura_intervalo_s=settings.feeds.rtds_reassinatura_intervalo_s,
            )
            for indice in range(max(1, settings.feeds.rtds_conexoes))
        ]
        self.rtds = self.rtds_feeds[0]
        self.binance = BinanceWsFeed(
            assets=settings.assets,
            user_agent=settings.user_agent,
            on_event=self._on_event,
            stale_after_seconds=settings.feeds.stale_after_seconds_spot,
            reconnect_initial_seconds=settings.feeds.reconnect_initial_seconds,
            reconnect_max_seconds=settings.feeds.reconnect_max_seconds,
        )
        self.poly = PolyMarketWsFeed(
            url=settings.endpoints.clob_market_ws,
            user_agent=settings.user_agent,
            custom_feature_enabled=True,  # best bid/ask + eventos de resolução
            ping_interval_seconds=settings.feeds.clob_ping_interval_seconds,
            pong_stale_seconds=settings.feeds.clob_stale_seconds,
            on_event=self._on_event,
            stale_after_seconds=settings.feeds.stale_after_seconds_book,
            reconnect_initial_seconds=settings.feeds.reconnect_initial_seconds,
            reconnect_max_seconds=settings.feeds.reconnect_max_seconds,
        )
        self.trackers = [
            GapTracker(fonte="rtds", silencio_limiar_s=settings.feeds.stale_after_seconds_twap),
            GapTracker(
                fonte="binance_ws", silencio_limiar_s=settings.feeds.stale_after_seconds_spot
            ),
            GapTracker(
                fonte="poly_ws", silencio_limiar_s=settings.feeds.stale_after_seconds_book
            ),
        ]
        self._feed_by_name = {
            "rtds": self.rtds,
            "binance_ws": self.binance,
            "poly_ws": self.poly,
        }
        self.discovery_cycles = 0
        self.subscribed_ever: set[str] = set()
        # token -> instante (epoch) em que pode ser desassinado. É o endDate
        # da janela MAIS a carência de resolução.
        self.desassinar_apos: dict[str, float] = {}
        # token -> metadados mínimos para o fallback e o relatório
        self.janela_por_token: dict[str, dict[str, Any]] = {}
        # Resoluções já capturadas (por qualquer caminho), para não repolar.
        self.resolvidos: set[str] = set()
        # O que chega do CLOB, por event_type. Torna visível o que está sendo
        # recebido E o que está sendo ignorado por tipo desconhecido — sem
        # isto, "0 resoluções" não distingue "não chegou" de "chegou e foi
        # descartado".
        self.eventos_poly: Counter[str] = Counter()

        # ------------------------------------------------------ integridade
        # A.2/A.3: reconstrói o topo do livro ao vivo e confere contra o topo
        # que o próprio servidor manda em cada delta. Divergiu, o token entra
        # na fila de resync.
        self.integridade = MonitorDeIntegridade()
        self.relogio = MonitorDeRelogio()
        self.a_resincronizar: set[str] = set()
        self.resyncs = 0
        self.motivos_de_resync: Counter[str] = Counter()
        self.incidentes_de_fila = 0

        # A.5: deduplicação entre as conexões redundantes do RTDS.
        self._vistos_rtds: dict[tuple[Any, ...], None] = {}
        self._dedup_janela = max(1, settings.feeds.rtds_dedup_janela)
        self.rtds_primeiro_por_conexao: Counter[int] = Counter()
        self.rtds_duplicados_por_conexao: Counter[int] = Counter()

    # ------------------------------------------------------------- hot path
    def _fazer_callback_rtds(self, indice: int) -> Any:
        """Callback por conexão do RTDS, com deduplicação (M2.2 A.5).

        A chave é (tópico, ativo, timestamp do servidor): é o que identifica
        um tick independentemente de qual conexão o entregou. Mensagem que não
        é tick de preço cai no hash do bruto — deduplicar por conteúdo é pior
        que deduplicar por identidade, mas é melhor que gravar em dobro.

        Quem chega primeiro grava. `rtds_primeiro_por_conexao` mostra o que
        cada conexão de fato acrescentou: se uma delas entregar ~0% primeiro,
        a redundância não está comprando nada e pode ser desligada.
        """

        def callback(event: FeedEvent) -> None:
            chave = self._chave_rtds(event)
            if chave in self._vistos_rtds:
                self.rtds_duplicados_por_conexao[indice] += 1
                return
            self._vistos_rtds[chave] = None
            if len(self._vistos_rtds) > self._dedup_janela:
                # dict preserva ordem de inserção: o mais antigo sai primeiro
                self._vistos_rtds.pop(next(iter(self._vistos_rtds)))
            self.rtds_primeiro_por_conexao[indice] += 1
            self._on_event(event)

        return callback

    def _chave_rtds(self, event: FeedEvent) -> tuple[Any, ...]:
        tick = parse_rtds_event(event.parsed, event.ts_mono_ns, event.ts_wall_ns)
        if tick is not None and tick.src_timestamp_ms > 0:
            self.relogio.observar(tick.src_timestamp_ms, event.ts_wall_ns)
            return (tick.topic, tick.asset, tick.src_timestamp_ms)
        return ("__bruto__", hash(event.raw))

    def _on_perda_de_book(self, envelope: RecordEnvelope) -> None:
        """A fila SEM PERDA encheu. Isto é incidente, não descarte.

        Chamado de dentro do `submit`, ou seja, no hot path: aqui só se marca
        o token: o resync em si é assíncrono. Continuar aplicando deltas sobre
        um livro que já sabemos furado produziria um livro plausível e errado,
        que é exatamente o que a parte A do M2.2 existe para impedir.
        """
        self.incidentes_de_fila += 1
        with contextlib.suppress(orjson.JSONDecodeError):
            payload = orjson.loads(envelope.raw)
            eventos = payload if isinstance(payload, list) else [payload]
            for evento in eventos:
                if not isinstance(evento, dict):
                    continue
                for token in tokens_do_evento(evento):
                    self.integridade.marcar_perda(token)
                    self.a_resincronizar.add(token)
                    self.motivos_de_resync["fila_cheia"] += 1

    def _canal_do_evento(self, event: FeedEvent) -> str:
        """Livro vai pelo canal sem perda; o resto pode ser descartado."""
        if event.source != "poly_ws":
            return CANAL_PADRAO
        payload = event.parsed
        eventos = payload if isinstance(payload, list) else [payload]
        for evento in eventos:
            if isinstance(evento, dict) and evento.get("event_type") in EVENTOS_DE_LIVRO:
                return CANAL_BOOK
        return CANAL_PADRAO
    def _contar_evento_poly(self, event: FeedEvent) -> None:
        """Conta os tipos que chegam do CLOB, inclusive os desconhecidos."""
        payload = event.parsed
        if payload is None:
            self.eventos_poly["__nao_json__"] += 1
            return
        itens = payload if isinstance(payload, list) else [payload]
        for item in itens:
            if not isinstance(item, dict):
                self.eventos_poly["__nao_dict__"] += 1
                continue
            tipo = str(item.get("event_type") or "__sem_event_type__")
            self.eventos_poly[tipo] += 1
            if tipo in RESOLUTION_EVENT_TYPES:
                asset_id = item.get("asset_id")
                if isinstance(asset_id, str):
                    self.resolvidos.add(asset_id)
            carimbo = _numero(item.get("timestamp"))
            if carimbo:
                self.relogio.observar(carimbo, event.ts_wall_ns)
            for divergencia in self.integridade.observar(item, event.ts_wall_ns):
                self.a_resincronizar.add(divergencia.asset_id)
                self.motivos_de_resync["divergencia_de_topo"] += 1

    def _on_event(self, event: FeedEvent) -> None:
        if event.source == "poly_ws":
            self._contar_evento_poly(event)
        self.writer.submit(
            RecordEnvelope(
                ts_mono_ns=event.ts_mono_ns,
                ts_wall_ns=event.ts_wall_ns,
                fonte=event.source,
                raw=event.raw,
            ),
            canal=self._canal_do_evento(event),
        )

    def _write_meta(self, fonte: str, payload: dict[str, Any]) -> None:
        """Grava um registro sintetizado pelo recorder (não veio do fio)."""
        self.writer.submit(
            RecordEnvelope(
                ts_mono_ns=time.monotonic_ns(),
                ts_wall_ns=time.time_ns(),
                fonte=fonte,
                raw=orjson.dumps(payload),
            )
        )

    # ------------------------------------------------------------ descoberta
    async def _discovery_loop(self, discovery: MarketDiscovery, deadline: float) -> None:
        while time.monotonic() < deadline:
            try:
                await self._discovery_cycle(discovery)
            except Exception as exc:
                log.warning("falha na descoberta", erro=f"{type(exc).__name__}: {exc}")
            await asyncio.sleep(DISCOVERY_INTERVAL_SECONDS)

    async def _discovery_cycle(self, discovery: MarketDiscovery) -> None:
        markets = await discovery.discover()
        self.discovery_cycles += 1

        agora = time.time()

        # Tokens que DEVEM estar assinados agora. Janela não-operável continua
        # sendo gravada: o motivo da recusa é dado, e o M2 quer medir isso.
        desejados = {
            token for market in markets for token in market.token_id_by_outcome.values()
        }
        # Registra a carência de cada token visto nesta descoberta.
        for market in markets:
            fim = parse_end_date_epoch({"endDate": market.end_date_iso})
            limite = (fim + RESOLUTION_GRACE_SECONDS) if fim is not None else (
                agora + RESOLUTION_GRACE_SECONDS
            )
            for token in market.token_id_by_outcome.values():
                self.desassinar_apos[token] = limite
                self.janela_por_token[token] = {
                    "slug": market.slug,
                    "condition_id": market.condition_id,
                    "end_date_iso": market.end_date_iso,
                    "outcome": next(
                        (o for o, t in market.token_id_by_outcome.items() if t == token),
                        None,
                    ),
                }

        atuais = set(self.poly.token_ids)
        novos = sorted(desejados - atuais)

        # Rotação COM CARÊNCIA: o token só sai depois que a janela encerrou
        # E a carência de resolução passou. Desassinar no endDate — como era
        # antes — desligava a escuta antes de o resultado ser publicado, e foi
        # por isso que o primeiro backtest real viu 104 janelas e 0 resoluções.
        candidatos = atuais - desejados
        encerrados = sorted(
            token
            for token in candidatos
            if agora >= self.desassinar_apos.get(token, 0.0)
            or token in self.resolvidos
        )
        for token in encerrados:
            self.desassinar_apos.pop(token, None)

        if novos:
            await self.poly.subscribe(novos)
            self.subscribed_ever.update(novos)
        if encerrados:
            await self.poly.unsubscribe(encerrados)

        self._write_meta(
            FONTE_DISCOVERY,
            {
                "ciclo": self.discovery_cycles,
                "janelas": [market_snapshot(m) for m in markets],
                "assinaturas": {
                    "novas": len(novos),
                    "encerradas": len(encerrados),
                    "ativas": len(self.poly.token_ids),
                    "em_carencia": len(candidatos) - len(encerrados),
                },
                "eventos_poly_por_tipo": dict(self.eventos_poly),
                "integridade": self.integridade_resumo(),
            },
        )
        log.info(
            "descoberta",
            ciclo=self.discovery_cycles,
            janelas=len(markets),
            operaveis=sum(1 for m in markets if m.operable),
            novas=len(novos),
            encerradas=len(encerrados),
            assinadas=len(self.poly.token_ids),
            em_carencia=len(candidatos) - len(encerrados),
            resolucoes=len(self.resolvidos),
            msgs_rtds=self.rtds.message_count,
            msgs_binance=self.binance.message_count,
            msgs_poly=self.poly.message_count,
            gravadas=self.writer.written,
            descartadas=self.writer.dropped,
            descartadas_book=self.writer.dropped_por_canal.get(CANAL_BOOK, 0),
            divergencias=self.integridade.divergencias,
            resyncs=self.resyncs,
            offset_relogio_p50_ms=self.relogio.resumo()["p50_ms"],
        )

    # --------------------------------------------------- fallback de resolução
    async def _resolution_poll_loop(
        self, http_get_json: Any, deadline: float
    ) -> None:
        """Confere na Gamma o resultado de janelas encerradas.

        Caminho INDEPENDENTE do WS de propósito: se o evento de resolução não
        chegar (perdido numa reconexão, tipo novo não reconhecido, carência
        curta demais), este laço ainda captura o resultado. Uma resolução
        perdida invalida a janela inteira para o backtest — vale ter dois
        caminhos.

        O que sai daqui é gravado como evento SINTÉTICO, com fonte própria e
        `_sintetico: true`. Nunca se disfarça de evento do fio.
        """
        while time.monotonic() < deadline:
            await asyncio.sleep(RESOLUTION_POLL_SECONDS)
            agora = time.time()
            pendentes = [
                (token, meta)
                for token, meta in self.janela_por_token.items()
                if token not in self.resolvidos
                and (fim := parse_end_date_epoch({"endDate": meta.get("end_date_iso")}))
                is not None
                and agora > fim + 60.0
            ]
            # Só os mais antigos por ciclo, para não martelar a Gamma.
            for token, meta in pendentes[:20]:
                try:
                    await self._consultar_resolucao(http_get_json, token, meta)
                except Exception as exc:
                    log.warning(
                        "falha ao consultar resolução",
                        slug=meta.get("slug"),
                        erro=f"{type(exc).__name__}: {exc}",
                    )

    async def _consultar_resolucao(
        self, http_get_json: Any, token: str, meta: dict[str, Any]
    ) -> None:
        slug = meta.get("slug")
        if not slug:
            return
        gamma = await http_get_json(
            f"{self.settings.endpoints.gamma}/markets/slug/{slug}", None
        )
        if not isinstance(gamma, dict):
            return

        # A Gamma marca o vencedor pelos outcomePrices (1/0 depois de resolver).
        precos = gamma.get("outcomePrices")
        if isinstance(precos, str):
            with contextlib.suppress(orjson.JSONDecodeError):
                precos = orjson.loads(precos)
        vencedor: str | None = None
        if isinstance(precos, list) and len(precos) == 2:
            with contextlib.suppress(TypeError, ValueError):
                up, down = float(precos[0]), float(precos[1])
                if up >= 0.99 and down <= 0.01:
                    vencedor = "Up"
                elif down >= 0.99 and up <= 0.01:
                    vencedor = "Down"
        if vencedor is None:
            return  # ainda não resolveu; tenta no próximo ciclo

        self.resolvidos.add(token)
        self._write_meta(
            FONTE_RESOLUCAO_SINTETICA,
            {
                "_sintetico": True,
                "event_type": "market_resolved",
                "asset_id": token,
                "market": meta.get("condition_id"),
                "slug": slug,
                "winning_outcome": vencedor,
                "outcome_prices": precos,
                "uma_resolution_status": gamma.get("umaResolutionStatus"),
                "closed": gamma.get("closed"),
                "observado_em_epoch": time.time(),
            },
        )
        log.info("resolução capturada via Gamma", slug=slug, vencedor=vencedor)

    # --------------------------------------------------------------- resync
    async def _resync_loop(self, deadline: float) -> None:
        """Refaz a assinatura dos tokens com livro furado, forçando snapshot.

        Não existe "peça o snapshot de novo" no protocolo do WS de mercado: o
        `book` completo chega quando se assina. Desassinar e reassinar é,
        portanto, o resync — feio, mas é o mecanismo que o protocolo oferece.

        Cada resync vira um registro na gravação, com a causa. Sem isso, o
        backtest veria o livro se consertar sozinho no meio da série e não
        teria como saber que houve um buraco antes.
        """
        intervalo = self.settings.recorder.resync_intervalo_s
        while time.monotonic() < deadline:
            await asyncio.sleep(intervalo)
            pendentes = sorted(self.a_resincronizar & set(self.poly.token_ids))
            # Tokens que já saíram da assinatura não têm o que resincronizar.
            self.a_resincronizar.difference_update(
                self.a_resincronizar - set(self.poly.token_ids)
            )
            if not pendentes:
                continue
            self.a_resincronizar.difference_update(pendentes)
            try:
                await self.poly.unsubscribe(pendentes)
                await self.poly.subscribe(pendentes)
            except Exception as exc:
                log.warning(
                    "falha no resync do livro",
                    tokens=len(pendentes),
                    erro=f"{type(exc).__name__}: {exc}",
                )
                self.a_resincronizar.update(pendentes)
                continue
            self.resyncs += len(pendentes)
            for token in pendentes:
                self.integridade.marcar_perda(token)
            self._write_meta(
                FONTE_RESYNC,
                {
                    "_sintetico": True,
                    "tokens": pendentes,
                    "motivos": dict(self.motivos_de_resync),
                    "observado_em_epoch": time.time(),
                },
            )
            log.warning(
                "resync do livro",
                tokens=len(pendentes),
                total=self.resyncs,
                divergencias=self.integridade.divergencias,
                incidentes_de_fila=self.incidentes_de_fila,
            )

    # ----------------------------------------------------------------- gaps
    def _saude_da_fonte(self, fonte: str) -> tuple[bool, float]:
        """Conectado? e há quanto tempo veio a última mensagem?

        Para o RTDS a resposta agrega as conexões redundantes: com duas
        conexões, só há lacuna quando as DUAS estão mudas. Perguntar só à
        primeira registraria lacuna sempre que ela caísse, mesmo com a
        segunda entregando tudo — que é justamente o caso que a redundância
        existe para cobrir.
        """
        if fonte == "rtds":
            return (
                any(feed.connected for feed in self.rtds_feeds),
                min(feed.last_message_age_seconds for feed in self.rtds_feeds),
            )
        feed = self._feed_by_name[fonte]
        return feed.connected, feed.last_message_age_seconds

    async def _gap_loop(self, deadline: float) -> None:
        while time.monotonic() < deadline:
            agora = time.time_ns()
            for tracker in self.trackers:
                conectado, idade = self._saude_da_fonte(tracker.fonte)
                fechado = tracker.observe(
                    conectado=conectado,
                    idade_ultima_msg_s=idade,
                    agora_wall_ns=agora,
                )
                if fechado is not None:
                    self._write_meta(FONTE_GAP, fechado.to_dict())
                    log.warning("lacuna na gravação", **fechado.to_dict())
            await asyncio.sleep(GAP_POLL_SECONDS)

    # ------------------------------------------------------------ relatórios
    def integridade_resumo(self) -> dict[str, Any]:
        """O bloco `integridade` do M2.2, do lado do recorder.

        O do backtest é o mesmo monitor rodado offline sobre a gravação; este
        aqui é o que a VPS viu ao vivo. Os dois têm de bater — divergirem é
        sinal de que a gravação perdeu algo entre o fio e o disco.
        """
        return {
            "divergencia_topo_book": self.integridade.resumo(),
            "offset_relogio_ms": self.relogio.resumo(),
            "resyncs": self.resyncs,
            "motivos_de_resync": dict(self.motivos_de_resync),
            "incidentes_de_fila_sem_perda": self.incidentes_de_fila,
            "tokens_aguardando_resync": len(self.a_resincronizar),
        }

    #: Meta de aceite do M2.7: silêncio total do feed-verdade abaixo disto
    #: por hora de gravação. A medição que motivou o marco deu 163.195s em
    #: 8h — ou seja, ~20.400s/h. Sessenta segundos são 0,017% da hora.
    META_SILENCIO_S_POR_HORA = 60.0

    def saude_do_rtds(self, duracao_s: float) -> dict[str, Any]:
        """Os dois mecanismos do M2.7, medidos — e a meta, conferida.

        Sem este bloco a correção seria uma promessa: "reassinamos" e
        "reconectamos" sem número nenhum são exatamente o tipo de afirmação
        que a gravação de 8h desmentiu. Aqui saem as contagens e o veredito
        contra a meta, no relatório do próprio recorder.
        """
        horas = max(duracao_s / 3600.0, 1e-9)
        feeds = [f for nome, f in self._feed_by_name.items() if nome.startswith("rtds")]
        reassinaturas = sum(f.reassinaturas for f in feeds)
        por_silencio = sum(f.reassinaturas_por_silencio for f in feeds)
        watchdog = sum(f.watchdog_reconexoes for f in feeds)
        erros = sum(f.reassinaturas_com_erro for f in feeds)
        # Custo em segundos de cegueira que os mecanismos ADMITEM: cada
        # disparo do watchdog custa até o seu timeout; cada reassinatura por
        # silêncio custa até o limiar de tópico mudo. É um TETO do silêncio
        # causado pelas falhas que sabemos ter acontecido — não substitui a
        # medição do backtest sobre a gravação, que é a autoridade.
        teto_watchdog = watchdog * max(
            (f.sem_dados_timeout_s or 0.0) for f in feeds
        ) if feeds else 0.0
        teto_topico = por_silencio * max(
            (getattr(f, "topico_mudo_s", 0.0) or 0.0) for f in feeds
        ) if feeds else 0.0
        teto_total = teto_watchdog + teto_topico
        return {
            "conexoes": len(feeds),
            "reassinaturas": reassinaturas,
            "reassinaturas_por_silencio_de_topico": por_silencio,
            "reassinaturas_com_erro": erros,
            "reconexoes_por_watchdog": watchdog,
            "idade_por_topico_s": [
                f.idade_por_topico() for f in feeds if hasattr(f, "idade_por_topico")
            ],
            "silencio_admitido_s": round(teto_total, 1),
            "silencio_admitido_s_por_hora": round(teto_total / horas, 1),
            "meta_s_por_hora": self.META_SILENCIO_S_POR_HORA,
            "meta_atingida": teto_total / horas <= self.META_SILENCIO_S_POR_HORA,
            "nota": (
                "M2.7 tarefa 1. `silencio_admitido_s` e um TETO calculado dos "
                "eventos que os mecanismos detectaram: cada reconexao por "
                "watchdog custa ate o timeout dele, cada reassinatura por "
                "silencio custa ate o limiar de topico mudo. NAO substitui a "
                "medicao real — a autoridade e `gravacao.silencio_do_rtds` do "
                "backtest sobre a gravacao, que le os carimbos e nao depende "
                "de o mecanismo ter percebido. Se este teto disser que a meta "
                "foi atingida e o backtest disser que nao, existe uma terceira "
                "causa de silencio que nenhum dos dois mecanismos cobre, e ela "
                "e o proximo achado."
            ),
        }

    def redundancia_resumo(self) -> dict[str, Any]:
        """Quanto cada conexão do RTDS de fato acrescentou (M2.2 A.5).

        `entregou_primeiro` é a métrica que decide se a redundância se paga:
        uma conexão que nunca chega antes da outra não está cobrindo nada, e
        a segunda conexão pode ser desligada no config.
        """
        return {
            "conexoes": len(self.rtds_feeds),
            "por_conexao": [
                {
                    "indice": indice,
                    "entregou_primeiro": self.rtds_primeiro_por_conexao.get(indice, 0),
                    "duplicadas_descartadas": self.rtds_duplicados_por_conexao.get(
                        indice, 0
                    ),
                    "mensagens": feed.message_count,
                    "quedas": feed.close_count,
                }
                for indice, feed in enumerate(self.rtds_feeds)
            ],
            "janela_de_dedup": self._dedup_janela,
        }

    # ---------------------------------------------------------------- ciclo
    async def run(self, duration_seconds: float) -> dict[str, Any]:
        await self.writer.start()
        inicio_mono = time.monotonic()
        deadline = inicio_mono + duration_seconds

        async with httpx.AsyncClient(
            headers={"User-Agent": self.settings.user_agent}, timeout=15.0
        ) as http:

            async def http_get_json(url: str, params: dict[str, Any] | None) -> Any:
                response = await http.get(url, params=params)
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                return response.json()

            discovery = MarketDiscovery(
                http_get_json=http_get_json,
                gamma_url=self.settings.endpoints.gamma,
                clob_url=self.settings.endpoints.clob,
                assets=self.settings.assets,
                probe_durations_seconds=self.settings.probe_durations_seconds,
            )

            for feed in self.rtds_feeds:
                await feed.start()
            await self.binance.start()
            await self.poly.start()

            tasks = [
                asyncio.create_task(self._discovery_loop(discovery, deadline)),
                asyncio.create_task(self._gap_loop(deadline)),
                asyncio.create_task(
                    self._resolution_poll_loop(http_get_json, deadline)
                ),
                asyncio.create_task(self._resync_loop(deadline)),
            ]
            try:
                await asyncio.gather(*tasks)
            except asyncio.CancelledError:
                raise
            finally:
                for task in tasks:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
                agora = time.time_ns()
                for tracker in self.trackers:
                    pendente = tracker.finalizar(agora)
                    if pendente is not None:
                        self._write_meta(FONTE_GAP, pendente.to_dict())
                for feed in self.rtds_feeds:
                    await feed.stop()
                await self.binance.stop()
                await self.poly.stop()

        duracao = time.monotonic() - inicio_mono
        relatorio = {
            "duracao_s": round(duracao, 1),
            "ciclos_descoberta": self.discovery_cycles,
            "tokens_assinados_no_total": len(self.subscribed_ever),
            "mensagens": {
                "rtds": sum(feed.message_count for feed in self.rtds_feeds),
                "binance_ws": self.binance.message_count,
                "poly_ws": self.poly.message_count,
            },
            "gravadas": self.writer.written,
            "descartadas": self.writer.dropped,
            "descartadas_por_canal": dict(self.writer.dropped_por_canal),
            "integridade": self.integridade_resumo(),
            "eventos_poly_por_tipo": dict(self.eventos_poly),
            "resolucoes_capturadas": len(self.resolvidos),
            "janelas_vistas": len(self.janela_por_token),
            "quedas_por_feed": {
                nome: {
                    "total": feed.close_count,
                    "ultimas": feed.close_reasons[-10:],
                }
                for nome, feed in self._feed_by_name.items()
            },
            "gaps": resumo_gaps(self.trackers, duracao),
            "redundancia_rtds": self.redundancia_resumo(),
            "saude_do_rtds": self.saude_do_rtds(duracao),
        }
        self._write_meta("recorder_relatorio", relatorio)
        await self.writer.stop()
        return relatorio


async def run(settings: Settings, duration_seconds: float) -> dict[str, Any]:
    recorder = Recorder(settings)
    relatorio = await recorder.run(duration_seconds)
    log.info("recorder encerrado", **relatorio)
    return relatorio


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PULSEARB recorder — grava feeds crus")
    parser.add_argument(
        "--duration",
        default="72h",
        help="duração da gravação: 90s, 30m, 72h, 7d (default 72h)",
    )
    parser.add_argument(
        "--hours", type=float, default=None, help="[compat] duração em horas"
    )
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args(argv)

    setup_logging()
    settings = Settings.load(args.config)
    seconds = args.hours * 3600 if args.hours is not None else parse_duration(args.duration)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run(settings, seconds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
