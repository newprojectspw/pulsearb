"""Detecção e relatório de lacunas (gaps) na gravação.

Uma gravação com buracos não anunciados é pior que gravação nenhuma: o
backtest roda, produz número, e o número está errado sem ninguém saber. O
recorder registra cada lacuna com duração e causa, e o replay recusa-se a
ignorá-las em silêncio.

Duas fontes de lacuna:
- **Desconexão**: o feed caiu e reconectou. Duração = tempo sem conexão.
- **Silêncio**: conectado, mas sem mensagem por mais que o limiar do feed
  (que agora vem da cadência medida — API_NOTES 13.1/13.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class GapKind(StrEnum):
    DESCONEXAO = "desconexao"
    SILENCIO = "silencio"


@dataclass(frozen=True, slots=True)
class Gap:
    fonte: str
    kind: GapKind
    inicio_wall_ns: int
    fim_wall_ns: int

    @property
    def duracao_s(self) -> float:
        return (self.fim_wall_ns - self.inicio_wall_ns) / 1e9

    def to_dict(self) -> dict[str, object]:
        return {
            "fonte": self.fonte,
            "tipo": self.kind.value,
            "inicio_wall_ns": self.inicio_wall_ns,
            "fim_wall_ns": self.fim_wall_ns,
            "duracao_s": round(self.duracao_s, 3),
        }


@dataclass
class GapTracker:
    """Acompanha um feed e fecha lacunas quando a normalidade volta.

    Alimentado por polling (o recorder chama `observe` a cada ciclo), não por
    evento — assim uma lacuna é detectada mesmo que NADA chegue, que é
    exatamente o caso interessante.
    """

    fonte: str
    silencio_limiar_s: float
    gaps: list[Gap] = field(default_factory=list)
    _gap_aberto: tuple[GapKind, int] | None = None

    def observe(
        self, *, conectado: bool, idade_ultima_msg_s: float, agora_wall_ns: int
    ) -> Gap | None:
        """Registra o estado atual. Devolve a lacuna FECHADA neste instante."""
        anormal: GapKind | None = None
        if not conectado:
            anormal = GapKind.DESCONEXAO
        elif idade_ultima_msg_s > self.silencio_limiar_s:
            anormal = GapKind.SILENCIO

        if anormal is not None:
            if self._gap_aberto is None:
                self._gap_aberto = (anormal, agora_wall_ns)
            elif self._gap_aberto[0] is not anormal:
                # Mudou de tipo (ex.: silêncio virou desconexão): fecha e reabre,
                # para o relatório não misturar causas distintas.
                fechado = self._fechar(agora_wall_ns)
                self._gap_aberto = (anormal, agora_wall_ns)
                return fechado
            return None

        return self._fechar(agora_wall_ns) if self._gap_aberto is not None else None

    def _fechar(self, agora_wall_ns: int) -> Gap | None:
        if self._gap_aberto is None:
            return None
        kind, inicio = self._gap_aberto
        self._gap_aberto = None
        gap = Gap(fonte=self.fonte, kind=kind, inicio_wall_ns=inicio, fim_wall_ns=agora_wall_ns)
        self.gaps.append(gap)
        return gap

    def finalizar(self, agora_wall_ns: int) -> Gap | None:
        """Fecha lacuna que ainda estava aberta no fim da gravação."""
        return self._fechar(agora_wall_ns)

    @property
    def total_gap_s(self) -> float:
        return sum(gap.duracao_s for gap in self.gaps)


def resumo_gaps(trackers: list[GapTracker], duracao_total_s: float) -> dict[str, object]:
    """Relatório agregado — vai para o log e para o arquivo de gravação."""
    por_fonte: dict[str, object] = {}
    for tracker in trackers:
        por_fonte[tracker.fonte] = {
            "n_gaps": len(tracker.gaps),
            "total_s": round(tracker.total_gap_s, 2),
            "maior_s": round(max((g.duracao_s for g in tracker.gaps), default=0.0), 2),
            "cobertura_pct": (
                round(100.0 * (1 - tracker.total_gap_s / duracao_total_s), 3)
                if duracao_total_s > 0
                else None
            ),
        }
    return {"duracao_total_s": round(duracao_total_s, 1), "por_fonte": por_fonte}
