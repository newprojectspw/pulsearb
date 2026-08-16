"""Histogramas de latência com buckets fixos.

Esqueleto do M1: a estrutura e os percentis existem e são testados; a
população de verdade (tick→decisão, decisão→ack) acontece no M2+.

Medição SEMPRE com time.monotonic_ns() — nunca relógio de parede.
"""

from __future__ import annotations

# Buckets em microssegundos, cobrindo de 50µs a 10s. Escala ~logarítmica:
# resolução fina onde o hot path vive (sub-milissegundo a dezenas de ms).
DEFAULT_BUCKETS_US: tuple[int, ...] = (
    50, 100, 200, 500,
    1_000, 2_000, 5_000, 10_000, 20_000, 50_000,
    100_000, 200_000, 500_000, 1_000_000, 2_000_000, 5_000_000, 10_000_000,
)


class LatencyHistogram:
    """Histograma cumulativo simples, O(1) por observação, sem alocação."""

    __slots__ = ("_buckets_us", "_counts", "_overflow", "_sum_us", "name")

    def __init__(self, name: str, buckets_us: tuple[int, ...] = DEFAULT_BUCKETS_US) -> None:
        self.name = name
        self._buckets_us = buckets_us
        self._counts = [0] * len(buckets_us)
        self._overflow = 0
        self._sum_us = 0

    def observe_ns(self, elapsed_ns: int) -> None:
        elapsed_us = elapsed_ns // 1_000
        self._sum_us += elapsed_us
        for i, limit in enumerate(self._buckets_us):
            if elapsed_us <= limit:
                self._counts[i] += 1
                return
        self._overflow += 1

    @property
    def count(self) -> int:
        return sum(self._counts) + self._overflow

    def percentile_us(self, pct: float) -> float:
        """Estimativa por bucket (limite superior). Suficiente para dashboard."""
        total = self.count
        if total == 0:
            return float("nan")
        target = max(1, int(-(-pct * total // 100)))
        cumulative = 0
        for i, bucket_count in enumerate(self._counts):
            cumulative += bucket_count
            if cumulative >= target:
                return float(self._buckets_us[i])
        return float("inf")  # caiu no overflow: acima do último bucket

    def snapshot(self) -> dict[str, float | int | str]:
        return {
            "nome": self.name,
            "n": self.count,
            "p50_us": self.percentile_us(50),
            "p99_us": self.percentile_us(99),
            "overflow": self._overflow,
        }
