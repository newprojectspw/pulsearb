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
import shutil
import signal
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
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
from pulsearb.feeds.rtds import TOPIC_TWAP_30, RtdsFeed, parse_rtds_event
from pulsearb.markets.discovery import (
    DiscoveredMarket,
    MarketDiscovery,
    parse_end_date_epoch,
)
from pulsearb.markets.rewards_da_gamma import (
    forma_dos_rewards,
    taxa_diaria_de_reward,
)
from pulsearb.numeros import numero
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
from pulsearb.replay.escopo import CHAVE_SLUGS_NO_ESCOPO
from pulsearb.settings import Settings
from pulsearb.tempo import RESOLUTION_GRACE_SECONDS, parse_duration

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
# Fallback: consultar a Gamma para janelas encerradas cuja resolução não
# chegou pelo WS. Independente do caminho do WS de propósito — se um falhar,
# o outro cobre.
RESOLUTION_POLL_SECONDS = 120.0



# As leituras de reward moram em `markets/rewards_da_gamma.py` desde que a
# rota maker (4.0) passou a precisar dos MESMOS numeros ao vivo. Copiar
# para o outro lado seria a divergencia que o CLAUDE.md chama de erro
# grave — e aqui seria cara, porque a leitura e ambigua de proposito (tres
# nomes de lista, seis de taxa) e duas copias divergiriam na primeira
# grafia nova.




#: Como a rodada terminou. Vai no relatório final (`desfecho`) e decide o
#: código de saída — interrupção NUNCA sai 0.
DESFECHO_COMPLETA = "completa"
DESFECHO_SIGTERM = "interrompida_sigterm"
DESFECHO_SIGINT = "interrompida_sigint"
DESFECHO_EXCECAO = "excecao"
DESFECHO_PREFLIGHT = "preflight_recusado"

#: Código de saída de uma rodada interrompida (convenção 128 + SIGINT).
SAIDA_INTERROMPIDO = 130
#: 128 + SIGTERM: o mesmo número que o SO daria, agora COM relatório.
SAIDA_SIGTERM = 143
SAIDA_POR_DESFECHO: dict[str | None, int] = {
    DESFECHO_COMPLETA: 0,
    DESFECHO_PREFLIGHT: 1,
    DESFECHO_SIGINT: SAIDA_INTERROMPIDO,
    DESFECHO_SIGTERM: SAIDA_SIGTERM,
}
SINAIS_DE_PARADA = {signal.SIGTERM: DESFECHO_SIGTERM, signal.SIGINT: DESFECHO_SIGINT}


class PreflightRecusado(RuntimeError):
    """O disco não comporta a projeção da rodada. Recusar é a falha fechada:
    começar 72 h para morrer sem espaço no meio invalida a gravação inteira."""


def projetar_armazenamento(
    *, bytes_por_hora: float, duracao_s: float, margem: float, livre_bytes: int
) -> dict[str, Any]:
    """Projeta o espaço de uma rodada e diz se cabe. Função PURA — o teste a
    exercita sem disco, e o preflight injeta o `livre_bytes` real.

    `exigido` é a projeção VEZES a margem: a folga cobre picos e o que não é
    gravação (log, sistema). `cabe` é o veredito; quem chama recusa se falso.
    """
    if bytes_por_hora <= 0:
        raise ValueError("bytes_por_hora deve ser maior que zero")
    if duracao_s < 0:
        raise ValueError("duracao_s não pode ser negativa")
    if margem < 1:
        raise ValueError("margem deve ser maior ou igual a 1")
    if livre_bytes < 0:
        raise ValueError("livre_bytes não pode ser negativo")
    horas = duracao_s / 3600.0
    projetado = bytes_por_hora * horas
    exigido = projetado * margem
    return {
        "bytes_por_hora_estimados": round(bytes_por_hora),
        "horas": round(horas, 3),
        "projecao_bytes": round(projetado),
        "projecao_72h_bytes": round(bytes_por_hora * 72.0),
        "margem": margem,
        "exigido_bytes": round(exigido),
        "livre_bytes": livre_bytes,
        "cabe": livre_bytes >= exigido,
    }


def preflight_de_armazenamento(settings: Settings, duracao_s: float) -> dict[str, Any]:
    """Confere o disco ANTES de gravar. Levanta `PreflightRecusado` se não
    cabe e a rodada é longa o bastante para o preflight valer.

    Rodada curta (< `duracao_minima_para_preflight_s`) não é barrada: uma hora
    de teste não precisa de espaço para 72 h. O diretório é criado antes da
    medida — `disk_usage` de um caminho inexistente levantaria."""
    rec = settings.recorder
    Path(rec.output_dir).mkdir(parents=True, exist_ok=True)
    livre = shutil.disk_usage(rec.output_dir).free
    proj = projetar_armazenamento(
        bytes_por_hora=rec.bytes_por_hora_estimados,
        duracao_s=duracao_s,
        margem=rec.margem_de_disco,
        livre_bytes=livre,
    )
    proj["aplicavel"] = duracao_s >= rec.duracao_minima_para_preflight_s
    if proj["aplicavel"] and not proj["cabe"]:
        raise PreflightRecusado(
            "disco insuficiente para a gravação: projeção "
            f"{proj['exigido_bytes'] / 1e9:.1f} GB (com margem {rec.margem_de_disco}), "
            f"livre {livre / 1e9:.1f} GB. Libere disco, reduza `--duration`, ou "
            "limite o escopo (`recorder.max_tokens_assinados`) e recalibre "
            "`recorder.bytes_por_hora_estimados` pela taxa medida no relatório."
        )
    return proj


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
        "rewards_daily_rate": taxa_diaria_de_reward(market.raw_gamma),
        # M2.7: o CRU da lista de rewards. Sem ele, "sem_taxa_diaria" é
        # indistinguível entre não participar, expirar, e o nosso leitor
        # errar a chave — ver `_forma_dos_rewards`.
        "rewards_bruto": forma_dos_rewards(market.raw_gamma),
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
                # M2.11: escalada, e o rotulo que torna o log atribuivel a
                # UMA das conexoes. Sem ele as duas logavam identicas e nao
                # dava para saber qual reclamava.
                reassinaturas_ate_derrubar=(
                    settings.feeds.rtds_reassinaturas_ate_derrubar
                ),
                # §2.2: o TWAP de 30 s só entra quando a gravação é para isso.
                topicos_extra=(
                    (TOPIC_TWAP_30,) if settings.feeds.rtds_assinar_twap_thirty else ()
                ),
                rotulo=f"rtds[{indice}]",
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
        #: `None` = nenhum sinal de parada; senão, qual (ver `SINAIS_DE_PARADA`).
        self.desfecho_por_sinal: str | None = None

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
            carimbo = numero(item.get("timestamp"))
            if carimbo:
                self.relogio.observar(carimbo, event.ts_wall_ns)
            # A divergência é observada SEMPRE (a telemetria não some), mas o
            # resync passou a obedecer a política do monitor: só a comparação
            # ALINHADA por carimbo, e só divergência MATERIAL na hora ou
            # SUB-material que PERSISTE — nunca a corrida de um tick entre
            # `best_bid_ask` e `price_change` (M2.5). O gatilho antigo
            # ("resync a cada divergência") era a causa da tempestade de
            # resyncs que invalidou a gravação.
            self.integridade.observar(item, event.ts_wall_ns)
        for asset_id, motivo in self.integridade.consumir_resync().items():
            self.a_resincronizar.add(asset_id)
            self.motivos_de_resync[motivo] += 1

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

    def _write_meta(
        self, fonte: str, payload: dict[str, Any], *, canal: str = CANAL_PADRAO
    ) -> None:
        """Grava um registro sintetizado pelo recorder (não veio do fio)."""
        self.writer.submit(
            RecordEnvelope(
                ts_mono_ns=time.monotonic_ns(),
                ts_wall_ns=time.time_ns(),
                fonte=fonte,
                raw=orjson.dumps(payload),
            ),
            canal=canal,
        )

    # ------------------------------------------------------------ descoberta
    async def _discovery_loop(self, discovery: MarketDiscovery, deadline: float) -> None:
        while time.monotonic() < deadline:
            try:
                await self._discovery_cycle(discovery)
            except Exception as exc:
                log.warning("falha na descoberta", erro=f"{type(exc).__name__}: {exc}")
            await asyncio.sleep(DISCOVERY_INTERVAL_SECONDS)

    def _aplicar_escopo(
        self,
        markets: list[DiscoveredMarket],
        *,
        retidos_em_carencia: int = 0,
    ) -> tuple[list[DiscoveredMarket], dict[str, Any]]:
        """Corta a descoberta ao escopo configurado, sem esconder o corte.

        Tokens de janelas fechadas ainda em carência permanecem assinados,
        mas ocupam o teto antes de novas janelas serem escolhidas. Assim,
        `max_tokens_assinados` limita o total ativo durante a rotação, e não
        apenas o conjunto recém-descoberto.
        """
        if retidos_em_carencia < 0:
            raise ValueError("retidos_em_carencia não pode ser negativo")
        limite = self.settings.recorder.max_tokens_assinados
        # A LISTA explícita dos slugs no escopo vai no snapshot, não só a
        # contagem: backtest e replay filtram por ela (`replay/escopo.py`) e
        # nunca tratam como gravada uma janela que o corte deixou de fora
        # (revisão do PR #197, P1).
        if limite is None:
            return markets, {
                "limite_de_tokens": None,
                "janelas_descobertas": len(markets),
                "janelas_no_escopo": len(markets),
                "janelas_cortadas": 0,
                "tokens_no_escopo": sum(
                    len(m.token_id_by_outcome) for m in markets
                ),
                "tokens_retidos_em_carencia": retidos_em_carencia,
                CHAVE_SLUGS_NO_ESCOPO: sorted(m.slug for m in markets),
            }

        disponivel = max(0, limite - retidos_em_carencia)
        no_escopo: list[DiscoveredMarket] = []
        tokens = 0
        for market in sorted(markets, key=lambda m: m.slug):
            n = len(market.token_id_by_outcome)
            if tokens + n > disponivel:
                continue
            no_escopo.append(market)
            tokens += n
        return no_escopo, {
            "limite_de_tokens": limite,
            "janelas_descobertas": len(markets),
            "janelas_no_escopo": len(no_escopo),
            "janelas_cortadas": len(markets) - len(no_escopo),
            "tokens_no_escopo": tokens,
            "tokens_retidos_em_carencia": retidos_em_carencia,
            "tokens_ativos_estimados": retidos_em_carencia + tokens,
            CHAVE_SLUGS_NO_ESCOPO: sorted(m.slug for m in no_escopo),
        }

    async def _discovery_cycle(self, discovery: MarketDiscovery) -> None:
        markets = await discovery.discover()
        self.discovery_cycles += 1

        agora = time.time()
        atuais = set(self.poly.token_ids)
        descobertos = {
            token for market in markets for token in market.token_id_by_outcome.values()
        }
        # Tokens de janelas FECHADAS ainda dentro da carência: continuam no ar
        # e consomem o teto do escopo (revisão P1 do PR #195).
        retidos_em_carencia = {
            token
            for token in atuais
            if token not in descobertos
            and agora < self.desassinar_apos.get(token, 0.0)
            and token not in self.resolvidos
        }
        no_escopo, escopo = self._aplicar_escopo(
            markets, retidos_em_carencia=len(retidos_em_carencia)
        )

        # Tokens que DEVEM estar assinados agora. Janela não-operável continua
        # sendo gravada: o motivo da recusa é dado, e o M2 quer medir isso.
        # Só as janelas DENTRO do escopo entram na assinatura; as cortadas
        # ficam registradas no snapshot (bloco `escopo`), não sumem.
        desejados = {
            token
            for market in no_escopo
            for token in market.token_id_by_outcome.values()
        }
        # Registra a carência de cada token visto nesta descoberta.
        for market in no_escopo:
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

        novos = sorted(desejados - atuais)

        # Rotação COM CARÊNCIA: o token só sai depois que a janela encerrou
        # E a carência de resolução passou. Desassinar no endDate — como era
        # antes — desligava a escuta antes de o resultado ser publicado, e foi
        # por isso que o primeiro backtest real viu 104 janelas e 0 resoluções.
        #
        # EXCEÇÃO do escopo: um token cuja janela SEGUE aberta (`descobertos`)
        # mas que o escopo CORTOU sai agora — senão o teto seria furado por
        # tokens abertos fora do escopo. A carência só protege janela FECHADA
        # (revisão P1 do PR #195).
        candidatos = atuais - desejados
        encerrados = sorted(
            token
            for token in candidatos
            if token in descobertos
            or agora >= self.desassinar_apos.get(token, 0.0)
            or token in self.resolvidos
        )
        for token in encerrados:
            self.desassinar_apos.pop(token, None)

        # SAI antes de ENTRAR (revisão do PR #197, P2): assinar os novos antes
        # de desassinar os encerrados furava `max_tokens_assinados` durante a
        # rotação — o teto valia no fim do ciclo, não em todo instante.
        if encerrados:
            await self.poly.unsubscribe(encerrados)
        if novos:
            await self.poly.subscribe(novos)
            self.subscribed_ever.update(novos)

        self._write_meta(
            FONTE_DISCOVERY,
            {
                "ciclo": self.discovery_cycles,
                # TODAS as janelas descobertas — inclusive as cortadas pelo
                # escopo. Documentar o corte é o que o impede de ser silencioso.
                "janelas": [market_snapshot(m) for m in markets],
                "escopo": escopo,
                # Os tokens EFETIVAMENTE assinados ao fim do ciclo — inclui os
                # retidos em carência, que não estão em `slugs_no_escopo`.
                "tokens_assinados": sorted(self.poly.token_ids),
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
            no_escopo=escopo["janelas_no_escopo"],
            cortadas=escopo["janelas_cortadas"],
            # `markets` inclui janelas cortadas pelo escopo. Contar essas
            # janelas como operáveis faria o log prometer mais cobertura do
            # que foi efetivamente assinada; o snapshot continua registrando
            # a descoberta completa e o corte separadamente.
            operaveis=sum(1 for m in no_escopo if m.operable),
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
            # marca_perda ANTES de reassinar, e não depois: o `subscribe`
            # dispara o book de RECUPERAÇÃO, que chega pela tarefa do WS. Se a
            # perda fosse marcada DEPOIS do subscribe, um book de recuperação
            # que chegasse no meio seria aplicado e então APAGADO pela
            # marcar_perda — o token perderia a recuperação e ficaria cego
            # (corrida ws×resync, revisão do PR #195). Entre o unsubscribe e o
            # subscribe não chega book nenhum, então marcar aqui é seguro.
            #
            # O MARCADOR também vai ANTES do subscribe, e pelo canal SEM perda
            # (revisão do PR #197, P1): gravado depois, como era, o `book` de
            # recuperação podia aparecer no arquivo ANTES do `resync_book`, e o
            # replay apagava uma recuperação já aplicada — fabricando
            # `aguardando_resync`. No CANAL_BOOK (FIFO, o mesmo do `book`) e
            # enfileirado antes do subscribe, ele precede o snapshot que o
            # subscribe dispara; no canal padrão podia ser descartado ou
            # drenado num ciclo posterior. `ts_perda_ns` é a fronteira que o
            # replay respeita (`aplicar_marcador_de_resync`) mesmo em ordem
            # trocada. O marcador diz "perda marcada", o que é verdade mesmo
            # se o subscribe falhar: o token fica aguardando, como deve.
            try:
                await self.poly.unsubscribe(pendentes)
                ts_perda_ns = time.time_ns()
                for token in pendentes:
                    self.integridade.marcar_perda(token)
                self._write_meta(
                    FONTE_RESYNC,
                    {
                        "_sintetico": True,
                        "tokens": pendentes,
                        "ts_perda_ns": ts_perda_ns,
                        "motivos": dict(self.motivos_de_resync),
                        "observado_em_epoch": time.time(),
                    },
                    canal=CANAL_BOOK,
                )
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
        erros_do_servidor = sum(getattr(f, "erros_do_servidor", 0) for f in feeds)
        por_recusa = sum(f.reconexoes_por_recusa for f in feeds)
        ultimos = [
            f.ultimo_erro_do_servidor
            for f in feeds
            if getattr(f, "ultimo_erro_do_servidor", None) is not None
        ]
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
            # 2026-09-17: o servidor RESPONDE à reassinatura, e a resposta
            # pode ser não (API_NOTES §6.2b). Antes ninguém lia.
            "erros_do_servidor": erros_do_servidor,
            "reconexoes_por_recusa_do_servidor": por_recusa,
            "ultimo_erro_do_servidor": (
                {"status_code": ultimos[-1].status_code, "mensagem": ultimos[-1].mensagem[:200]}
                if ultimos
                else None
            ),
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

    def _armazenamento_resumo(self, duracao_s: float) -> dict[str, Any]:
        """A taxa de bytes/hora MEDIDA e a projeção para 72 h (req 13).

        É o número que calibra o preflight: a estimativa configurada é um
        chute conservador; esta é a taxa real desta rodada, em bytes de DISCO
        (gzip). Os bytes do arquivo aberto ainda não fechado entram
        subcontados — é estimativa, não contabilidade."""
        horas = max(duracao_s / 3600.0, 1e-9)
        bytes_disco = self.writer.bytes_em_disco
        por_hora = bytes_disco / horas
        return {
            "bytes_em_disco": bytes_disco,
            "arquivos": len(self.writer.arquivos_escritos),
            "bytes_por_hora_medido": round(por_hora),
            "projecao_72h_bytes": round(por_hora * 72.0),
            "estimativa_configurada_por_hora": (
                self.settings.recorder.bytes_por_hora_estimados
            ),
            "nota": (
                "req 13. `bytes_por_hora_medido` e a taxa REAL em disco (gzip) "
                "desta rodada; use-a para calibrar "
                "`recorder.bytes_por_hora_estimados`, que o preflight usa para "
                "RECUSAR uma rodada de 72 h que nao cabe. O arquivo aberto no "
                "fim entra subcontado."
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
    def _relatorio_final(self, duracao: float) -> dict[str, Any]:
        """O relatório de encerramento, gravado como `recorder_relatorio`."""
        # Fecha pendências e divergências ABERTAS antes de resumir (revisão do
        # PR #197, P1): sem isto, uma divergência ainda aberta no último evento
        # nunca contava tempo nem entrava em `divergencias_persistentes` — o
        # relatório final saía mais saudável do que a gravação foi.
        self.integridade.finalizar()
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
            "armazenamento": self._armazenamento_resumo(duracao),
        }
        return relatorio

    async def run(self, duration_seconds: float) -> dict[str, Any]:
        """Grava por `duration_seconds` ou até SIGTERM/SIGINT — e nos dois
        casos encerra do MESMO jeito: feeds parados, monitor finalizado,
        relatório final com `desfecho`, writer drenado e gzip fechado.

        Antes, `systemctl stop` (SIGTERM) matava o processo com o handler
        padrão do SO: código 143, sem relatório, último arquivo sem trailer.
        Uma exceção também encerra assim, e SOBE depois do relatório gravado.
        """
        await self.writer.start()
        inicio_mono = time.monotonic()
        deadline = inicio_mono + duration_seconds
        parada = asyncio.Event()
        restaurar_sinais = self._instalar_sinais(parada)
        erro: Exception | None = None
        try:
            await self._coletar(deadline, parada)
        except Exception as exc:
            erro = exc
        finally:
            restaurar_sinais()

        duracao = time.monotonic() - inicio_mono
        relatorio = self._relatorio_final(duracao)
        relatorio["desfecho"] = self._desfecho(erro)
        if erro is not None:
            relatorio["erro"] = f"{type(erro).__name__}: {erro}"
        self._write_meta("recorder_relatorio", relatorio)
        await self.writer.stop()
        # Só DEPOIS de o writer drenar as filas e FECHAR os arquivos os bytes
        # em disco (gzip) estão completos. Medir antes subestimava a taxa —
        # filas e buffer gzip não drenados ficavam de fora — e calibraria o
        # preflight para MENOS, podendo aprovar uma rodada que não cabe
        # (revisão P2 do PR #195). Vai no relatório RETORNADO (o que a linha
        # `recorder encerrado` loga), não no meta embutido: esse é escrito
        # ANTES do `writer.stop()` — senão não entraria no arquivo — e o
        # `armazenamento` dele é a medida de antes do flush.
        relatorio["armazenamento"] = self._armazenamento_resumo(duracao)
        if erro is not None:
            raise erro
        return relatorio

    def _desfecho(self, erro: Exception | None) -> str:
        if erro is not None:
            return DESFECHO_EXCECAO
        return self.desfecho_por_sinal or DESFECHO_COMPLETA

    def _instalar_sinais(self, parada: asyncio.Event) -> Callable[[], None]:
        """SIGTERM/SIGINT viram PEDIDO de parada, não morte do processo.

        Onde o loop não aceita handler (Windows, thread não-principal) o sinal
        segue o padrão do SO — e o desfecho não é fingido: sem relatório, o
        código de saída do sistema é o que fica."""
        loop = asyncio.get_running_loop()
        anteriores: dict[signal.Signals, Any] = {}
        for sinal, desfecho in SINAIS_DE_PARADA.items():
            anterior = signal.getsignal(sinal)
            try:
                loop.add_signal_handler(sinal, self._pedir_parada, parada, desfecho)
            except (NotImplementedError, RuntimeError, ValueError):
                continue
            anteriores[sinal] = anterior

        def restaurar() -> None:
            for sinal, anterior in anteriores.items():
                loop.remove_signal_handler(sinal)
                signal.signal(sinal, anterior)

        return restaurar

    def _pedir_parada(self, parada: asyncio.Event, desfecho: str) -> None:
        if self.desfecho_por_sinal is None:
            self.desfecho_por_sinal = desfecho
        log.warning("sinal de parada: encerrando com relatório final", desfecho=desfecho)
        parada.set()

    async def _coletar(self, deadline: float, parada: asyncio.Event) -> None:
        """Os laços de gravação, até o prazo, uma falha ou o pedido de parada."""
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
            coleta = asyncio.gather(*tasks)
            aviso = asyncio.create_task(parada.wait())
            try:
                await asyncio.wait({coleta, aviso}, return_when=asyncio.FIRST_COMPLETED)
                if coleta.done():
                    coleta.result()  # levanta a falha de um laço, se houve
            finally:
                await self._encerrar_coleta(tasks, coleta, aviso)

    async def _encerrar_coleta(
        self,
        tasks: list[asyncio.Task[None]],
        coleta: asyncio.Future[Any],
        aviso: asyncio.Task[Any],
    ) -> None:
        await _cancelar_e_aguardar(aviso)
        for task in tasks:
            await _cancelar_e_aguardar(task)
        if not coleta.done():
            await _cancelar_e_aguardar(coleta)
        agora = time.time_ns()
        for tracker in self.trackers:
            pendente = tracker.finalizar(agora)
            if pendente is not None:
                self._write_meta(FONTE_GAP, pendente.to_dict())
        for feed in self.rtds_feeds:
            await feed.stop()
        await self.binance.stop()
        await self.poly.stop()


async def _cancelar_e_aguardar(tarefa: asyncio.Future[Any]) -> None:
    """Cancela e espera uma tarefa SEM deixar a falha dela escapar daqui.

    Um laço que já falhou relança a exceção ao ser aguardado com `await` — e,
    no encerramento, isso pulava o `stop` dos feeds e o relatório final. A
    falha já subiu por `coleta.result()`; aqui ela só é registrada.

    `asyncio.wait` em vez de `await` + `except CancelledError`: ele espera a
    tarefa terminar sem relançar o cancelamento NEM a exceção DELA, e deixa
    passar o cancelamento de QUEM espera — engolir esse seria esconder um
    cancelamento do próprio encerramento."""
    tarefa.cancel()
    await asyncio.wait([tarefa])
    if tarefa.cancelled():
        return
    falha = tarefa.exception()
    if falha is not None:
        nome = tarefa.get_name() if isinstance(tarefa, asyncio.Task) else "coleta"
        log.warning(
            "laço do recorder terminou com falha",
            laco=nome,
            erro=f"{type(falha).__name__}: {falha}",
        )


async def run(settings: Settings, duration_seconds: float) -> dict[str, Any]:
    recorder = Recorder(settings)
    relatorio = await recorder.run(duration_seconds)
    if relatorio["desfecho"] == DESFECHO_COMPLETA:
        log.info("recorder encerrado", **relatorio)
    else:
        # Nunca `recorder encerrado` para uma rodada que não chegou ao fim: é
        # a linha que o operador procura no journal como "rodada completa".
        log.error("recorder INTERROMPIDO", **relatorio)
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

    # PREFLIGHT DE ARMAZENAMENTO (req 12): recusa ANTES de gravar se o disco
    # não comporta a projeção. Falha fechada — começar 72 h para morrer sem
    # espaço no meio invalida a gravação inteira.
    try:
        projecao = preflight_de_armazenamento(settings, seconds)
    except PreflightRecusado as erro:
        log.error(
            "preflight de armazenamento RECUSOU a gravação",
            desfecho=DESFECHO_PREFLIGHT,
            motivo=str(erro),
        )
        return SAIDA_POR_DESFECHO[DESFECHO_PREFLIGHT]
    log.info("preflight de armazenamento", **projecao)

    # Um código de saída por desfecho — é o que o journal guarda quando a
    # unidade `systemd-run --collect` já sumiu do `systemctl`:
    #   0   → `completa`: rodou a duração inteira (`recorder encerrado`);
    #   1   → `preflight_recusado`: nada foi gravado;
    #   130 → `interrompida_sigint`; 143 → `interrompida_sigterm`: relatório
    #         final gravado, gzip fechado, mas a rodada NÃO chegou ao fim;
    #   exceção → sobe com traceback (1), depois de gravar o relatório.
    # Desfecho desconhecido é tratado como falha (1), nunca como sucesso.
    try:
        relatorio = asyncio.run(run(settings, seconds))
    except KeyboardInterrupt:
        # Só antes de o handler do loop existir: não houve relatório.
        log.error(
            "recorder INTERROMPIDO antes do handler de sinal: sem relatório "
            "final; o último arquivo pode estar sem trailer gzip",
            desfecho=DESFECHO_SIGINT,
        )
        return SAIDA_INTERROMPIDO
    return SAIDA_POR_DESFECHO.get(relatorio.get("desfecho"), 1)

if __name__ == "__main__":
    raise SystemExit(main())
