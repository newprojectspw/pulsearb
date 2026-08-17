"""Livro de ofertas reconstruído e preenchimento simulado contra ele.

Regra do M2.D, sem exceção: **só é preenchido o que o book realmente
comportava naquele instante.** Nada de assumir liquidez infinita ao topo, nada
de preencher ao melhor preço um tamanho que exigiria atravessar três níveis.

O preenchimento atravessa níveis de verdade: se você quer 500 shares e o topo
tem 120, você paga o topo por 120 e sobe para o próximo nível pelo resto. O
preço médio resultante É o slippage — não é um parâmetro chutado, é o que o
livro gravado dizia.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class OrderBook:
    """Snapshot do book de um token. bids em ordem decrescente, asks crescente."""

    asset_id: str
    bids: list[tuple[float, float]] = field(default_factory=list)  # (preço, tamanho)
    asks: list[tuple[float, float]] = field(default_factory=list)
    ts_ns: int = 0

    @property
    def best_bid(self) -> float | None:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0][0] if self.asks else None

    @property
    def spread(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid

    @property
    def mid(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / 2.0

    def depth_usdc(self, *, side: str, ticks: int, tick_size: float) -> float:
        """Quanto cabe, em USDC, dentro de N ticks do topo.

        É a medida de CAPACIDADE da estratégia (M2.E.3): não adianta ter edge
        se só cabem US$ 20 antes de o preço andar.
        """
        niveis = self.asks if side == "ask" else self.bids
        if not niveis:
            return 0.0
        topo = niveis[0][0]
        limite = topo + ticks * tick_size if side == "ask" else topo - ticks * tick_size
        total = 0.0
        for preco, tamanho in niveis:
            dentro = preco <= limite if side == "ask" else preco >= limite
            if not dentro:
                break
            total += preco * tamanho
        return total

    @classmethod
    def from_event(cls, payload: dict[str, Any]) -> OrderBook | None:
        """Constrói a partir de um evento `book` do WS de mercado do CLOB."""
        asset_id = payload.get("asset_id")
        if not isinstance(asset_id, str):
            return None
        book = cls(asset_id=asset_id, ts_ns=_ts_ns(payload.get("timestamp")))
        book.bids = _levels(payload.get("bids"), reverse=True)
        book.asks = _levels(payload.get("asks"), reverse=False)
        return book

    def apply_price_change(self, payload: dict[str, Any]) -> None:
        """Aplica um evento `price_change` ao book corrente.

        size = 0 remove o nível — é assim que o CLOB sinaliza cancelamento.
        """
        changes = payload.get("changes")
        if not isinstance(changes, list):
            return
        self.ts_ns = _ts_ns(payload.get("timestamp")) or self.ts_ns
        for change in changes:
            if not isinstance(change, dict):
                continue
            preco = _as_float(change.get("price"))
            tamanho = _as_float(change.get("size"))
            lado = str(change.get("side", "")).upper()
            if preco is None or tamanho is None:
                continue
            niveis = self.bids if lado == "BUY" else self.asks
            restantes = [(p, s) for p, s in niveis if p != preco]
            if tamanho > 0:
                restantes.append((preco, tamanho))
            restantes.sort(key=lambda item: item[0], reverse=(lado == "BUY"))
            if lado == "BUY":
                self.bids = restantes
            else:
                self.asks = restantes


@dataclass(frozen=True, slots=True)
class FillResult:
    """Resultado do preenchimento simulado."""

    shares: float          # quanto FOI preenchido (pode ser 0)
    custo_usdc: float      # sem taxa
    preco_medio: float     # custo/shares
    niveis_atravessados: int
    completo: bool         # o book comportava tudo que se pediu?

    @property
    def preenchido(self) -> bool:
        return self.shares > 0


def simulate_taker_buy(
    book: OrderBook, shares_desejadas: float, *, max_price: float = 1.0
) -> FillResult:
    """Compra taker atravessando o book gravado, nível a nível.

    `max_price` protege contra atravessar o livro inteiro: níveis acima disso
    não são consumidos. Em FOK (o modo do M4) preenchimento parcial não vale —
    quem consome decide, olhando `completo`.
    """
    if shares_desejadas <= 0:
        return FillResult(0.0, 0.0, 0.0, 0, False)

    restante = shares_desejadas
    custo = 0.0
    niveis = 0
    for preco, tamanho in book.asks:
        if preco > max_price or restante <= 0:
            break
        levado = min(restante, tamanho)
        custo += levado * preco
        restante -= levado
        niveis += 1

    preenchido = shares_desejadas - restante
    if preenchido <= 0:
        return FillResult(0.0, 0.0, 0.0, 0, False)
    return FillResult(
        shares=preenchido,
        custo_usdc=custo,
        preco_medio=custo / preenchido,
        niveis_atravessados=niveis,
        completo=restante <= 1e-9,
    )


def _levels(raw: Any, *, reverse: bool) -> list[tuple[float, float]]:
    if not isinstance(raw, list):
        return []
    saida: list[tuple[float, float]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        preco = _as_float(item.get("price"))
        tamanho = _as_float(item.get("size"))
        if preco is not None and tamanho is not None and tamanho > 0:
            saida.append((preco, tamanho))
    saida.sort(key=lambda item: item[0], reverse=reverse)
    return saida


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _ts_ns(value: Any) -> int:
    """O CLOB manda timestamp em ms, às vezes como string."""
    ms = _as_float(value)
    return int(ms * 1e6) if ms else 0
