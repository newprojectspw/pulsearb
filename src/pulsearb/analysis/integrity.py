"""Integridade do livro reconstruído — o teste que transforma corrupção
silenciosa em alarme (M2.2 parte A).

O problema que este módulo existe para resolver: o backtest opera sobre um
livro que NÓS reconstruímos aplicando deltas sobre um snapshot. Se um delta se
perde — fila cheia, reconexão, formato de payload diferente do esperado — o
livro passa a divergir do real e **nada avisa**. Todos os números do backtest
continuam saindo, bonitos e errados.

A defesa é gratuita e vem do próprio protocolo: o CLOB manda `best_bid` e
`best_ask` autoritativos em dois lugares — no evento `best_bid_ask` e **dentro
de cada `price_change`** (campos do modelo `PriceChange` do SDK 0.6.0). Ou
seja, a cada delta o servidor diz qual É o topo depois dele. Basta comparar
com o topo que a nossa reconstrução produziu.

Divergiu, temos três informações de uma vez: que houve perda, quando, e em
qual token. Aí o recorder força resync (pede snapshot novo) e o backtest
invalida a janela.

O mesmo monitor roda nos dois lados de propósito: ao vivo no recorder, para
consertar; e offline no backtest, para medir. Duas implementações da mesma
comparação divergiriam com o tempo, e a que mede deixaria de descrever a que
conserta.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from pulsearb.feeds.poly_ws import (
    EVENT_BEST_BID_ASK,
    EVENT_BOOK,
    EVENT_PRICE_CHANGE,
    MudancaDePreco,
    forma_do_price_change,
    iter_mudancas,
)

# Tolerância padrão da comparação de topo, em unidades de preço.
# Meio tick do menor tick observado (0,001) — abaixo disso é ruído de
# arredondamento de string decimal, não perda de delta.
TOLERANCIA_PADRAO = 0.0005

# Acima disto a janela é considerada CORROMPIDA e sai do backtest. Um tick
# inteiro de erro no topo já muda o preço de entrada e, portanto, o edge.
LIMIAR_INVALIDACAO_PADRAO = 0.01

# Divergências guardadas por token para o relatório. O contador é completo; a
# lista é amostra — 72h de gravação não podem virar uma lista sem teto.
MAX_AMOSTRAS = 50


@dataclass(frozen=True, slots=True)
class Divergencia:
    """O topo que o servidor afirmou × o topo que reconstruímos."""

    asset_id: str
    ts_ns: int
    lado: str                    # "bid" | "ask"
    servidor: float | None
    reconstruido: float | None
    magnitude: float             # |servidor − reconstruído|, em preço

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "ts_ns": self.ts_ns,
            "lado": self.lado,
            "servidor": self.servidor,
            "reconstruido": self.reconstruido,
            "magnitude": round(self.magnitude, 6),
        }


class LivroLeve:
    """Livro por preço→tamanho, só para saber o topo.

    Não é o `OrderBook` do backtest de propósito: o recorder precisa disto no
    hot path, com 150+ tokens vivos, e só lê o melhor bid e o melhor ask.
    Guardar níveis ordenados e reordenar a cada delta seria trabalho jogado
    fora — aqui um dicionário basta, e o topo sai com `max`/`min`.
    """

    __slots__ = ("asks", "bids", "ts_ns")

    def __init__(self) -> None:
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}
        self.ts_ns = 0

    def aplicar_snapshot(self, evento: dict[str, Any]) -> None:
        self.bids = _niveis(evento.get("bids"))
        self.asks = _niveis(evento.get("asks"))

    def aplicar(self, mudanca: MudancaDePreco) -> None:
        lado = self.bids if mudanca.side == "BUY" else self.asks
        if mudanca.size > 0:
            lado[mudanca.price] = mudanca.size
        else:
            lado.pop(mudanca.price, None)

    @property
    def best_bid(self) -> float | None:
        return max(self.bids) if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return min(self.asks) if self.asks else None


@dataclass
class MonitorDeIntegridade:
    """Reconstrói o topo e confere contra o que o servidor afirma.

    Uso: `observar(evento, ts_ns)` para cada evento do CLOB, na ordem. Devolve
    as divergências encontradas naquele evento (normalmente nenhuma).
    """

    tolerancia: float = TOLERANCIA_PADRAO
    limiar_invalidacao: float = LIMIAR_INVALIDACAO_PADRAO
    max_amostras: int = MAX_AMOSTRAS

    livros: dict[str, LivroLeve] = field(default_factory=dict)
    #: tokens que já receberam um snapshot; antes dele não há o que conferir
    com_snapshot: set[str] = field(default_factory=set)
    comparacoes: int = 0
    divergencias: int = 0
    amostras: list[Divergencia] = field(default_factory=list)
    magnitudes: list[float] = field(default_factory=list)
    #: token → maior magnitude vista. É o que invalida a janela.
    pior_por_token: dict[str, float] = field(default_factory=dict)
    #: token → quantas vezes divergiu
    divergencias_por_token: Counter[str] = field(default_factory=Counter)
    #: qual forma de `price_change` o servidor está usando de fato
    formas_de_price_change: Counter[str] = field(default_factory=Counter)
    #: tokens cujo livro foi descartado à espera de snapshot novo
    aguardando_resync: set[str] = field(default_factory=set)

    # ------------------------------------------------------------- observação
    def observar(self, evento: dict[str, Any], ts_ns: int) -> list[Divergencia]:
        tipo = evento.get("event_type")
        if tipo == EVENT_BOOK:
            asset_id = evento.get("asset_id")
            if isinstance(asset_id, str):
                livro = self._livro(asset_id)
                livro.aplicar_snapshot(evento)
                livro.ts_ns = ts_ns
                self.com_snapshot.add(asset_id)
                self.aguardando_resync.discard(asset_id)
            return []

        if tipo == EVENT_PRICE_CHANGE:
            self.formas_de_price_change[forma_do_price_change(evento)] += 1
            achados: list[Divergencia] = []
            for mudanca in iter_mudancas(evento):
                livro = self._livro(mudanca.asset_id)
                livro.aplicar(mudanca)
                livro.ts_ns = ts_ns
                # O topo autoritativo vem DENTRO do próprio delta.
                achados.extend(
                    self._conferir(
                        mudanca.asset_id, ts_ns, mudanca.best_bid, mudanca.best_ask
                    )
                )
            return achados

        if tipo == EVENT_BEST_BID_ASK:
            asset_id = evento.get("asset_id")
            if not isinstance(asset_id, str):
                return []
            return self._conferir(
                asset_id,
                ts_ns,
                _numero(evento.get("best_bid")),
                _numero(evento.get("best_ask")),
            )

        return []

    def marcar_perda(self, asset_id: str) -> None:
        """Perda CONHECIDA (fila cheia, reconexão): o livro deixa de valer.

        Não adianta continuar aplicando deltas sobre um livro que já sabemos
        estar furado — o resultado seria plausível e errado. Ele é descartado
        e o token entra em `aguardando_resync` até chegar um snapshot novo.
        """
        self.livros.pop(asset_id, None)
        self.com_snapshot.discard(asset_id)
        self.aguardando_resync.add(asset_id)

    # ---------------------------------------------------------------- interno
    def _livro(self, asset_id: str) -> LivroLeve:
        livro = self.livros.get(asset_id)
        if livro is None:
            livro = LivroLeve()
            self.livros[asset_id] = livro
        return livro

    def _conferir(
        self,
        asset_id: str,
        ts_ns: int,
        best_bid: float | None,
        best_ask: float | None,
    ) -> list[Divergencia]:
        # Sem snapshot inicial não há reconstrução para comparar: o livro está
        # incompleto por definição, e acusar divergência aqui seria alarme
        # falso.
        if asset_id not in self.com_snapshot:
            return []
        livro = self.livros.get(asset_id)
        if livro is None:
            return []
        achados: list[Divergencia] = []
        for lado, afirmado, nosso in (
            ("bid", best_bid, livro.best_bid),
            ("ask", best_ask, livro.best_ask),
        ):
            if afirmado is None:
                continue
            self.comparacoes += 1
            if nosso is not None and abs(afirmado - nosso) <= self.tolerancia:
                continue
            magnitude = abs(afirmado - nosso) if nosso is not None else float("inf")
            divergencia = Divergencia(
                asset_id=asset_id,
                ts_ns=ts_ns,
                lado=lado,
                servidor=afirmado,
                reconstruido=nosso,
                magnitude=magnitude,
            )
            self.divergencias += 1
            self.divergencias_por_token[asset_id] += 1
            if magnitude != float("inf"):
                self.magnitudes.append(magnitude)
            anterior = self.pior_por_token.get(asset_id, 0.0)
            self.pior_por_token[asset_id] = max(anterior, magnitude)
            if len(self.amostras) < self.max_amostras:
                self.amostras.append(divergencia)
            achados.append(divergencia)
        return achados

    # ----------------------------------------------------------- interpretação
    def token_corrompido(self, asset_id: str) -> bool:
        """O livro deste token é confiável o bastante para o backtest?"""
        return self.pior_por_token.get(asset_id, 0.0) > self.limiar_invalidacao

    def resumo(self) -> dict[str, Any]:
        taxa = self.divergencias / self.comparacoes if self.comparacoes else 0.0
        ordenadas = sorted(self.magnitudes)
        return {
            "comparacoes": self.comparacoes,
            "divergencias": self.divergencias,
            "taxa": round(taxa, 6),
            "magnitude_p50": _percentil(ordenadas, 50),
            "magnitude_p99": _percentil(ordenadas, 99),
            "magnitude_max": round(max(ordenadas), 6) if ordenadas else 0.0,
            "tokens_divergentes": len(self.divergencias_por_token),
            "tokens_corrompidos": sorted(
                t for t in self.pior_por_token if self.token_corrompido(t)
            )[:50],
            "tokens_aguardando_resync": len(self.aguardando_resync),
            "limiar_invalidacao": self.limiar_invalidacao,
            "tolerancia": self.tolerancia,
            "formas_de_price_change": dict(self.formas_de_price_change),
            "amostras": [d.to_dict() for d in self.amostras],
            "nota": (
                "`formas_de_price_change` diz qual formato o servidor usa de "
                "fato: `price_changes` (a do SDK oficial) ou `changes` (a que "
                "este projeto assumiu até o M2.2 sem nunca confirmar). Se vier "
                "`changes`, os deltas NÃO trazem best_bid/best_ask e a "
                "validação cruzada só acontece nos eventos `best_bid_ask`."
            ),
        }


class MonitorDeRelogio:
    """Offset entre o relógio local e o carimbo do servidor (M2.2 A.4).

    O modelo endgame depende de `seconds_left`: com 60s de janela, 2s de
    deriva de relógio erram em 3% a fração de TWAP já travada, e o erro entra
    no backtest como se fosse sinal. Deriva não avisa — por isso é medida.

    Só o offset RELATIVO importa aqui. Latência de rede e offset de relógio
    entram somados nesta conta e não são separáveis com um carimbo só; por
    isso o número é lido como teto do erro, não como o erro do relógio.
    """

    __slots__ = ("amostras", "max_amostras", "total")

    def __init__(self, max_amostras: int = 20_000) -> None:
        self.amostras: list[float] = []
        self.max_amostras = max_amostras
        self.total = 0

    def observar(self, carimbo_servidor_ms: float, chegada_wall_ns: int) -> None:
        if carimbo_servidor_ms <= 0:
            return
        self.total += 1
        offset_ms = chegada_wall_ns / 1e6 - carimbo_servidor_ms
        if len(self.amostras) < self.max_amostras:
            self.amostras.append(offset_ms)
        else:
            # Reservatório simples e determinístico: substitui em rodízio, o
            # que preserva a cauda recente sem depender de aleatoriedade
            # (que quebraria a reprodutibilidade do replay).
            self.amostras[self.total % self.max_amostras] = offset_ms

    def resumo(self) -> dict[str, Any]:
        ordenadas = sorted(self.amostras)
        return {
            "amostras": self.total,
            "p50_ms": _percentil(ordenadas, 50),
            "p99_ms": _percentil(ordenadas, 99),
            "min_ms": round(min(ordenadas), 3) if ordenadas else None,
            "max_ms": round(max(ordenadas), 3) if ordenadas else None,
            "nota": (
                "chegada_local - carimbo_do_servidor. Inclui latencia de rede, "
                "então é TETO do erro de relógio, não o erro em si. Valor "
                "estável e pequeno = relógio são; deriva crescente ao longo da "
                "gravação = NTP ausente ou quebrado (ver runbook §4.1)."
            ),
        }


def _niveis(bruto: Any) -> dict[float, float]:
    saida: dict[float, float] = {}
    if not isinstance(bruto, list):
        return saida
    for item in bruto:
        if not isinstance(item, dict):
            continue
        preco = _numero(item.get("price"))
        tamanho = _numero(item.get("size"))
        if preco is not None and tamanho is not None and tamanho > 0:
            saida[preco] = tamanho
    return saida


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


def _percentil(ordenadas: list[float], pct: float) -> float | None:
    if not ordenadas:
        return None
    rank = max(1, min(len(ordenadas), int(-(-pct * len(ordenadas) // 100))))
    return round(ordenadas[rank - 1], 6)
