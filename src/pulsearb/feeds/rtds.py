"""Cliente do RTDS (Real-Time Data Service) — wss://ws-live-data.polymarket.com.

Protocolo verificado em docs/API_NOTES.md seções 6.2 e 12.3:
- subscribe: {"action": "subscribe", "subscriptions": [{"topic": t, "type": "update"}]}
- tópicos usados: crypto_prices (spot Binance repassado) e
  crypto_prices_twap_sixty (TWAP Chainlink 60s — a fonte de resolução real de
  TODAS as durações observadas ao vivo em 2026-08-16)
- símbolos: minúsculos com barra no Chainlink/TWAP ("btc/usd"); o spot binance
  usa o par colado ("btcusdt")
- TWAP: payload tem full_accuracy_value (string inteira escalada 1e18,
  preferida) e window_s
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import orjson
import websockets

from pulsearb.feeds.base import FeedEvent, OnEvent, ReconnectingFeed

TOPIC_BINANCE = "crypto_prices"
TOPIC_TWAP_60 = "crypto_prices_twap_sixty"

_E18 = 10**18


@dataclass(frozen=True, slots=True)
class PriceTick:
    """Um preço normalizado, pronto para o engine/recorder."""

    topic: str           # TOPIC_BINANCE | TOPIC_TWAP_60 | outro
    asset: str           # "btc", "eth", ... (normalizado)
    price: float         # preço em USD
    src_timestamp_ms: int  # timestamp do payload (relógio do servidor)
    ts_mono_ns: int      # chegada local, monotônico
    ts_wall_ns: int      # chegada local, parede (registro)


def normalize_symbol(symbol: str) -> str:
    """'btc/usd' → 'btc'; 'BTCUSDT' → 'btc'; 'eth/usd' → 'eth'."""
    lowered = symbol.lower()
    if "/" in lowered:
        return lowered.split("/", 1)[0]
    for suffix in ("usdt", "usdc", "usd"):
        if lowered.endswith(suffix) and len(lowered) > len(suffix):
            return lowered[: -len(suffix)]
    return lowered


def e18_to_float(value: str) -> float:
    """Converte a string inteira escalada 1e18 do Chainlink para float.

    A divisão inteira antes do float preserva a precisão que importa
    (float64 tem ~15-16 dígitos; 118432.17 cabe com folga).
    """
    scaled = int(value)
    whole, fraction = divmod(abs(scaled), _E18)
    result = float(whole) + fraction / _E18
    return -result if scaled < 0 else result


def parse_rtds_event(parsed: Any, ts_mono_ns: int, ts_wall_ns: int) -> PriceTick | None:
    """Extrai um PriceTick de um evento do RTDS. None = não é evento de preço.

    Tolerante por design: tópico desconhecido ou payload sem os campos
    esperados devolve None — o recorder grava o cru de qualquer forma, e o
    engine simplesmente não consome o que não entende.
    """
    if not isinstance(parsed, dict):
        return None
    topic = parsed.get("topic")
    payload = parsed.get("payload")
    if not isinstance(topic, str) or not isinstance(payload, dict):
        return None
    symbol = payload.get("symbol")
    if not isinstance(symbol, str):
        return None

    price: float | None = None
    if topic == TOPIC_TWAP_60 or topic.startswith("crypto_prices_twap"):
        # full_accuracy_value (1e18) é a fonte preferida — igual ao SDK oficial.
        fav = payload.get("full_accuracy_value")
        if isinstance(fav, str):
            try:
                price = e18_to_float(fav)
            except ValueError:
                price = None
        if price is None:
            price = _as_float(payload.get("value"))
    elif topic in (TOPIC_BINANCE, "crypto_prices_chainlink"):
        price = _as_float(payload.get("value"))
    else:
        return None

    if price is None:
        return None
    src_ts = payload.get("timestamp")
    return PriceTick(
        topic=topic,
        asset=normalize_symbol(symbol),
        price=price,
        src_timestamp_ms=int(src_ts) if isinstance(src_ts, (int, float)) else 0,
        ts_mono_ns=ts_mono_ns,
        ts_wall_ns=ts_wall_ns,
    )


def _as_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


class RtdsFeed(ReconnectingFeed):
    """Feed do RTDS: assina binance + twap60 para os ativos configurados."""

    #: Os tópicos que este feed assina, e portanto os que devem estar
    #: chegando. Ficar mudo além do limiar é sinal de assinatura caducada.
    TOPICOS_ASSINADOS = (TOPIC_BINANCE, TOPIC_TWAP_60)

    def __init__(
        self,
        *,
        url: str,
        user_agent: str,
        assets: list[str],
        on_tick: Any = None,  # Callable[[PriceTick], None] | None
        on_event: OnEvent | None = None,
        topico_mudo_s: float | None = None,
        **kwargs: Any,
    ) -> None:
        self.topico_mudo_s = topico_mudo_s
        super().__init__(name="rtds", url=url, user_agent=user_agent, on_event=on_event, **kwargs)
        self.assets = [a.lower() for a in assets]
        self.on_tick = on_tick
        self.last_tick_by_key: dict[tuple[str, str], PriceTick] = {}

    def subscribe_frame(self) -> str:
        # Sem filtro de symbols: o RTDS aceita filtrar, mas receber todos e
        # filtrar localmente é mais robusto a grafias de símbolo divergentes
        # (custo: alguns KB/s). Os ativos configurados são o filtro local.
        # .decode() NÃO é cosmético: orjson devolve bytes, e bytes vira frame
        # BINÁRIO no websockets — que é o que derrubava o RTDS com 1003.
        return orjson.dumps(
            {
                "action": "subscribe",
                "subscriptions": [
                    {"topic": TOPIC_BINANCE, "type": "update"},
                    {"topic": TOPIC_TWAP_60, "type": "update"},
                ],
            }
        ).decode()

    async def _on_connected(self, ws: websockets.ClientConnection) -> None:
        await self.send_frame(self.subscribe_frame(), ws)

    async def _reassinar(self, ws: websockets.ClientConnection) -> None:
        """Reenvia a assinatura. É o mesmo frame do `_on_connected`.

        M2.7: em 8h de gravação real, **48 tópicos ficaram mudos com a conexão
        viva**, recebendo outros tópicos — a assinatura caduca do lado do
        servidor e nada avisa. O `subscribe` do RTDS é idempotente pelo
        protocolo (assinar o que já está assinado não duplica entrega), então
        reenviar é barato e seguro.

        Se algum dia o servidor passar a duplicar, a dedup por
        (tópico, ativo, timestamp) que já existe para a redundância de
        conexões (M2.2 A.5) absorve — mas isso apareceria em
        `rtds_duplicadas` no relatório, e não em silêncio.
        """
        await self.send_frame(self.subscribe_frame(), ws)

    def _reassinatura_urgente(self) -> str | None:
        """Algum tópico assinado ficou mudo além do limiar?

        Esta é a resposta ao fenômeno medido: 48 tópicos mudos em 8h com a
        conexão viva. O watchdog da base não pega, porque ele conta QUALQUER
        mensagem — e o outro tópico continuava chegando. Só a visão por
        tópico enxerga, e a reação é reassinar, não derrubar: derrubar a
        conexão custaria o tópico que ainda estava são.

        Antes do primeiro tick de um tópico não há o que julgar: `None`. Uma
        assinatura que nunca entregou nada é problema de conexão, e disso
        cuida o watchdog.

        M2.10 — a visão por tópico tinha um ponto cego que a gravação de
        2026-08-22 tornou concreto: `idade_por_topico` reduz os ativos pelo
        MENOR tempo, então **um ativo vivo mascara os outros sete**. O tópico
        aparecia jovem enquanto btc, eth e mais seis não davam sinal, e a
        cobertura por ativo do M2.9 media exatamente essa metade faltante.
        A urgência passa a ser julgada por (tópico, ativo).

        Ser mais estrito produz mais reassinaturas do que a visão por
        tópico — e isso é aceitável de propósito: reenviar a assinatura é um
        frame de texto idempotente (ver `_reassinar`), enquanto o falso
        NEGATIVO custou metade de uma gravação. Só se julga par que já
        entregou pelo menos um tick; ativo que nunca falou é problema de
        conexão, e disso cuida o watchdog.
        """
        if not self.topico_mudo_s:
            return None
        idades = self.idade_por_topico_e_ativo()
        assinados = set(self.TOPICOS_ASSINADOS)
        mudos = sorted(
            (idade, topico, asset)
            for (topico, asset), idade in idades.items()
            if topico in assinados and idade > self.topico_mudo_s
        )
        if not mudos:
            return None
        pior = mudos[-1]
        quantos = f" (+{len(mudos) - 1} outro(s))" if len(mudos) > 1 else ""
        return (
            f"{pior[1]}/{pior[2]} mudo ha {pior[0]:.1f}s "
            f"(limiar {self.topico_mudo_s}s){quantos}"
        )

    def idade_por_topico_e_ativo(
        self, agora_mono_ns: int | None = None
    ) -> dict[tuple[str, str], float]:
        """Segundos desde a última mensagem de cada par (tópico, ativo).

        M2.10. É a visão que `idade_por_topico` perde ao reduzir pelo menor
        valor. O relatório continua mostrando a visão por tópico porque ela
        é legível; quem DECIDE reassinar usa esta.
        """
        agora = agora_mono_ns if agora_mono_ns is not None else time.monotonic_ns()
        return {
            chave: round((agora - tick.ts_mono_ns) / 1e9, 3)
            for chave, tick in self.last_tick_by_key.items()
        }

    def idade_por_topico(self, agora_mono_ns: int | None = None) -> dict[str, float]:
        """Segundos desde a última mensagem de cada tópico.

        `last_message_age_seconds` da base conta QUALQUER mensagem, então não
        enxerga um tópico caducando enquanto o outro continua chegando — que
        é exatamente a falha que este marco conserta. Esta é a visão por
        tópico, e é ela que o relatório do recorder precisa mostrar.
        """
        agora = agora_mono_ns if agora_mono_ns is not None else time.monotonic_ns()
        idades: dict[str, float] = {}
        for (topico, _asset), tick in self.last_tick_by_key.items():
            idade = (agora - tick.ts_mono_ns) / 1e9
            anterior = idades.get(topico)
            if anterior is None or idade < anterior:
                idades[topico] = idade
        return {topico: round(idade, 3) for topico, idade in sorted(idades.items())}

    async def _handle_message(self, event: FeedEvent) -> None:
        tick = parse_rtds_event(event.parsed, event.ts_mono_ns, event.ts_wall_ns)
        if tick is None:
            return
        if self.assets and tick.asset not in self.assets:
            return
        self.last_tick_by_key[(tick.topic, tick.asset)] = tick
        if self.on_tick is not None:
            self.on_tick(tick)
