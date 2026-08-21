"""Cliente do WS de mercado do CLOB — wss://ws-subscriptions-clob.polymarket.com/ws/market.

Protocolo verificado em docs/API_NOTES.md seção 6.1:
- frame inicial: {"type":"market","assets_ids":[...],"custom_feature_enabled":true}
  (true para receber best bid/ask e eventos de lifecycle, incluindo resolução)
- subscribe/unsubscribe dinâmicos: {"operation":"subscribe"|"unsubscribe","assets_ids":[...]}
- heartbeat de APLICAÇÃO: texto "PING" a cada 10s; morto após 30s sem "PONG".
  Não é o ping/pong do protocolo WebSocket.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import orjson
import websockets

from pulsearb.feeds.base import FeedEvent, OnEvent, ReconnectingFeed

# Heartbeat de aplicação do CLOB: texto puro, NUNCA binário (API_NOTES 6.1).
PING = "PING"
PONG = "PONG"
# O FeedEvent.raw chega sempre em bytes; esta é a forma comparável.
PONG_BYTES = PONG.encode()

# ---------------------------------------------------------------- event_type
# Valores VERIFICADOS no SDK oficial 0.6.0
# (`models/clob/market_events.py`, os `Literal[...]` de cada evento).
EVENT_BOOK = "book"
EVENT_PRICE_CHANGE = "price_change"
EVENT_LAST_TRADE = "last_trade_price"
EVENT_TICK_SIZE_CHANGE = "tick_size_change"
EVENT_BEST_BID_ASK = "best_bid_ask"
EVENT_NEW_MARKET = "new_market"
EVENT_MARKET_RESOLVED = "market_resolved"

# Tipos que o CLOB usa para anunciar resolução. Conjunto (e não igualdade
# solta) para que um tipo novo apareça na contagem por tipo em vez de sumir.
# "resolution" não aparece no SDK; fica no conjunto por precaução.
RESOLUTION_EVENT_TYPES = frozenset({EVENT_MARKET_RESOLVED, "resolution"})

# Eventos que MUDAM o livro. Perder um destes corrompe o book reconstruído
# dali em diante, em silêncio — por isso eles vão pelo canal sem perda do
# writer (M2.2 A.1), enquanto tick de preço pode ser descartado sob pressão.
EVENTOS_DE_LIVRO = frozenset({EVENT_BOOK, EVENT_PRICE_CHANGE})


@dataclass(frozen=True, slots=True)
class MudancaDePreco:
    """Uma alteração de nível dentro de um evento `price_change`.

    `best_bid`/`best_ask` vêm no PRÓPRIO delta (campos do modelo `PriceChange`
    do SDK). São o topo autoritativo do servidor no instante do delta, e é com
    eles que a validação cruzada do M2.2 A.2 confere o livro que reconstruímos
    — sem custo nenhum, em todo delta, não só nos eventos `best_bid_ask`.
    """

    asset_id: str
    price: float
    size: float
    side: str                  # "BUY" | "SELL"
    best_bid: float | None = None
    best_ask: float | None = None


def iter_mudancas(
    payload: dict[str, Any], *, asset_padrao: str | None = None
) -> Iterator[MudancaDePreco]:
    """Percorre um `price_change`, aceitando as DUAS formas de payload.

    Aqui mora uma dúvida que a gravação seguinte tem de resolver, e que por
    ora é tratada aceitando os dois formatos em vez de apostar em um:

    - **Forma A** (a que este projeto vinha assumindo): `asset_id` no topo do
      evento e a lista em `changes`.
    - **Forma B** (a do SDK oficial 0.6.0, `MarketPriceChangePayload`): sem
      `asset_id` no topo; a lista vem em `price_changes` e **cada entrada**
      traz o seu próprio `asset_id`, mais `best_bid`/`best_ask`.

    A forma B é a que tem evidência primária. A forma A era uma fixture
    SINTÉTICA nossa, nunca confirmada contra o fio — e se o servidor fala B,
    o parser antigo lia `changes`, não achava nada e aplicava ZERO deltas, em
    silêncio, deixando o livro parado no último snapshot. Aceitar as duas
    custa dez linhas; apostar na errada custa a gravação inteira.

    Quem chama conta qual forma apareceu (`forma_do_price_change`), e o
    relatório diz qual o servidor está usando de fato.

    `asset_padrao` cobre o delta que não nomeia token nem no topo nem na
    entrada: quem chamou já sabe de qual livro se trata (é o caso de
    `OrderBook.apply_price_change`, que roteia por livro).
    """
    brutas = payload.get("price_changes")
    asset_do_topo = payload.get("asset_id")
    if not isinstance(brutas, list):
        brutas = payload.get("changes")
    if not isinstance(brutas, list):
        return
    for bruta in brutas:
        if not isinstance(bruta, dict):
            continue
        asset_id = bruta.get("asset_id")
        if not isinstance(asset_id, str):
            asset_id = (
                asset_do_topo if isinstance(asset_do_topo, str) else asset_padrao
            )
        preco = _numero(bruta.get("price"))
        tamanho = _numero(bruta.get("size"))
        if asset_id is None or preco is None or tamanho is None:
            continue
        yield MudancaDePreco(
            asset_id=asset_id,
            price=preco,
            size=tamanho,
            side=str(bruta.get("side", "")).upper(),
            best_bid=_numero(bruta.get("best_bid")),
            best_ask=_numero(bruta.get("best_ask")),
        )


@dataclass(frozen=True, slots=True)
class Resolucao:
    """O resultado de uma janela, já normalizado a partir do evento do fio."""

    condition_id: str | None
    tokens: tuple[str, ...]
    winning_token_id: str | None
    winning_outcome: str | None   # "Up" | "Down" (como veio)
    ts_servidor_ms: float | None
    sintetico: bool = False

    def venceu_up(self, token_up: str, token_down: str) -> bool | None:
        """O lado Up ganhou? None se o evento não permite decidir.

        Duas evidências independentes, nesta ordem de confiança:

        1. `winning_asset_id` — identidade de token, não admite ambiguidade;
        2. `winning_outcome` — a string "Up"/"Down", que depende de o mercado
           ter sido montado com esses rótulos.

        A (1) vem primeiro porque o mapeamento outcome→token é por mercado e
        já mordeu este projeto antes (API_NOTES 12.11: mapear token pelo `o`,
        nunca por posição).
        """
        if self.winning_token_id is not None:
            if self.winning_token_id == token_up:
                return True
            if self.winning_token_id == token_down:
                return False
        if isinstance(self.winning_outcome, str):
            rotulo = self.winning_outcome.strip().lower()
            if rotulo == "up":
                return True
            if rotulo == "down":
                return False
        return None


def normalizar_condition_id(valor: Any) -> str | None:
    """Chave comparável de condition id: minúsculas, sem `0x`, sem espaço.

    A Gamma, o CLOB e o WS não prometem a mesma grafia, e comparar
    `0xABE6…` com `abe6…` falharia em silêncio — que é exatamente o modo de
    falha que este marco existe para eliminar.
    """
    if not isinstance(valor, str):
        return None
    limpo = valor.strip().lower()
    if limpo.startswith("0x"):
        limpo = limpo[2:]
    return limpo or None


def resolucao_do_evento(evento: dict[str, Any]) -> Resolucao | None:
    """Extrai a resolução de um evento, na forma REAL do servidor.

    A forma foi capturada em produção (`tests/fixtures/clob_ws_market_resolved.json`)
    e bate com o `MarketResolvedPayload` do SDK 0.6.0:

        {"event_type": "market_resolved",
         "market": "0xabe6…",                 ← condition id
         "assets_ids": ["6261…", "2511…"],    ← os DOIS tokens
         "winning_asset_id": "6261…",
         "winning_outcome": "Up",
         "timestamp": "1787166722776"}        ← epoch em MILISSEGUNDOS, string

    Repare no que NÃO existe: `asset_id` no singular. O leitor do backtest
    procurava exatamente esse campo e por isso descartava todo evento de
    resolução — 73 gravados, 0 lidos. Mesma família do defeito do
    `price_change` (API_NOTES 6.1b): a forma esperada foi escrita a partir do
    que imaginávamos, não do que o servidor manda.

    Também aceita a forma do fallback sintético que o próprio recorder grava
    quando consulta a Gamma (`asset_id` + `winning_outcome`), marcada com
    `sintetico=True` para que o relatório saiba distinguir as duas origens.
    """
    if evento.get("event_type") not in RESOLUTION_EVENT_TYPES:
        return None

    tokens: list[str] = []
    for chave in ("assets_ids", "asset_ids"):
        valor = evento.get(chave)
        if isinstance(valor, list):
            tokens.extend(item for item in valor if isinstance(item, str))
    asset_id = evento.get("asset_id")
    if isinstance(asset_id, str) and asset_id not in tokens:
        tokens.append(asset_id)

    vencedor_token = evento.get("winning_asset_id") or evento.get("winning_token_id")
    if not isinstance(vencedor_token, str):
        vencedor_token = None
    vencedor_rotulo = evento.get("winning_outcome") or evento.get("outcome")
    if not isinstance(vencedor_rotulo, str):
        vencedor_rotulo = None

    if not tokens and vencedor_token is None and vencedor_rotulo is None:
        return None

    return Resolucao(
        condition_id=normalizar_condition_id(
            evento.get("market") or evento.get("condition_id")
        ),
        tokens=tuple(tokens),
        winning_token_id=vencedor_token,
        winning_outcome=vencedor_rotulo,
        ts_servidor_ms=_numero(evento.get("timestamp")),
        sintetico=bool(evento.get("_sintetico")),
    )


def forma_do_price_change(payload: dict[str, Any]) -> str:
    """Qual das duas formas este evento usa. Ver `iter_mudancas`."""
    if isinstance(payload.get("price_changes"), list):
        return "price_changes"
    if isinstance(payload.get("changes"), list):
        return "changes"
    return "__sem_lista__"


#: Nomes plausíveis para os dois lados de um snapshot de livro. `bids`/`asks`
#: é o que o SDK documenta; os demais entram porque o `price_change` já provou
#: que o fio usa nomes que o SDK não menciona (API_NOTES 6.1b), e descobrir
#: isso pela segunda vez por dedução custou um marco inteiro.
CHAVES_DE_LADO = (
    ("bids", "asks"),
    ("buys", "sells"),
    ("bid", "ask"),
    ("b", "a"),
)


def forma_do_book(payload: dict[str, Any]) -> str:
    """Com que par de chaves este snapshot de livro veio.

    Existe pelo mesmo motivo que `forma_do_price_change`: quando o evento não
    parseia, a diferença entre "o servidor mandou o lado vazio" e "o servidor
    mandou o lado com outro nome" é a diferença entre um achado de mercado e
    um defeito nosso — e as duas coisas produzem exatamente o mesmo zero.
    """
    achadas = [
        f"{compra}+{venda}"
        for compra, venda in CHAVES_DE_LADO
        if isinstance(payload.get(compra), list) or isinstance(payload.get(venda), list)
    ]
    if not achadas:
        # Nenhum par conhecido: reporta as chaves que o evento REALMENTE tem,
        # para que o nome novo apareça no relatório em vez de virar silêncio.
        outras = sorted(
            chave
            for chave, valor in payload.items()
            if isinstance(valor, list) and valor and isinstance(valor[0], dict)
        )
        return f"__desconhecida__:{','.join(outras)}" if outras else "__sem_lista__"
    return "+".join(achadas) if len(achadas) > 1 else achadas[0]


def tokens_do_evento(evento: dict[str, Any]) -> set[str]:
    """Todos os tokens que um evento do CLOB toca.

    Um `price_change` na forma B pode carregar deltas de VÁRIOS tokens no
    mesmo evento — ler só o `asset_id` do topo perderia os demais.
    """
    tokens: set[str] = set()
    asset_id = evento.get("asset_id")
    if isinstance(asset_id, str):
        tokens.add(asset_id)
    if evento.get("event_type") == EVENT_PRICE_CHANGE:
        tokens.update(m.asset_id for m in iter_mudancas(evento))
    for chave in ("assets_ids", "asset_ids"):
        valor = evento.get(chave)
        if isinstance(valor, list):
            tokens.update(item for item in valor if isinstance(item, str))
    return tokens


def _numero(valor: Any) -> float | None:
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


class PolyMarketWsFeed(ReconnectingFeed):
    """Feed do livro CLOB com heartbeat de aplicação e subscribe dinâmico."""

    def __init__(
        self,
        *,
        url: str,
        user_agent: str,
        token_ids: list[str] | None = None,
        custom_feature_enabled: bool = True,
        ping_interval_seconds: float = 10.0,
        pong_stale_seconds: float = 30.0,
        on_event: OnEvent | None = None,
        **kwargs: Any,
    ) -> None:
        # O CLOB tem heartbeat de APLICAÇÃO (PING/PONG texto), então o ping
        # do protocolo WS é redundante aqui — mas só aqui. RTDS e Binance
        # ficam com o keepalive da lib, que é o default da base.
        kwargs.setdefault("ws_ping_interval", None)
        super().__init__(
            name="poly_ws", url=url, user_agent=user_agent, on_event=on_event, **kwargs
        )
        self.token_ids: set[str] = set(token_ids or [])
        self.custom_feature_enabled = custom_feature_enabled
        self.ping_interval_seconds = ping_interval_seconds
        self.pong_stale_seconds = pong_stale_seconds
        self._last_pong_mono: float = 0.0
        self._heartbeat_task: asyncio.Task[None] | None = None
        self.pong_count = 0

    # ------------------------------------------------------------------ frames
    def initial_frame(self) -> str:
        return orjson.dumps(
            {
                "type": "market",
                "assets_ids": sorted(self.token_ids),
                "custom_feature_enabled": self.custom_feature_enabled,
            }
        ).decode()

    @staticmethod
    def subscribe_frame(token_ids: list[str], custom_feature_enabled: bool = True) -> str:
        return orjson.dumps(
            {
                "operation": "subscribe",
                "assets_ids": token_ids,
                "custom_feature_enabled": custom_feature_enabled,
            }
        ).decode()

    @staticmethod
    def unsubscribe_frame(token_ids: list[str]) -> str:
        return orjson.dumps(
            {"operation": "unsubscribe", "assets_ids": token_ids}
        ).decode()

    # -------------------------------------------------------------- subscribes
    async def subscribe(self, token_ids: list[str]) -> None:
        """Adiciona tokens; efetivo já e após qualquer reconexão (estado local)."""
        new = [t for t in token_ids if t not in self.token_ids]
        self.token_ids.update(new)
        if new and self._ws is not None:
            await self.send_frame(self.subscribe_frame(new, self.custom_feature_enabled))

    async def unsubscribe(self, token_ids: list[str]) -> None:
        gone = [t for t in token_ids if t in self.token_ids]
        self.token_ids.difference_update(gone)
        if gone and self._ws is not None:
            await self.send_frame(self.unsubscribe_frame(gone))

    # ------------------------------------------------------------------- ciclo
    async def _on_connected(self, ws: websockets.ClientConnection) -> None:
        self._last_pong_mono = time.monotonic()
        if self.token_ids:
            await self.send_frame(self.initial_frame(), ws)
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat(ws), name="poly-ws-heartbeat"
        )

    async def _receive_loop(self, ws: websockets.ClientConnection) -> None:
        try:
            await super()._receive_loop(ws)
        finally:
            if self._heartbeat_task is not None:
                self._heartbeat_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._heartbeat_task
                self._heartbeat_task = None

    async def _heartbeat(self, ws: websockets.ClientConnection) -> None:
        """PING a cada 10s; 30s sem PONG = conexão morta, força reconexão."""
        while True:
            await asyncio.sleep(self.ping_interval_seconds)
            if time.monotonic() - self._last_pong_mono > self.pong_stale_seconds:
                self.log.warning(
                    "heartbeat morto: sem PONG", limite_s=self.pong_stale_seconds
                )
                await ws.close(code=1000, reason="heartbeat timeout")
                return
            await self.send_frame(PING, ws)

    async def _handle_message(self, event: FeedEvent) -> None:
        if event.raw.strip() == PONG_BYTES:
            self._last_pong_mono = time.monotonic()
            self.pong_count += 1
            return
