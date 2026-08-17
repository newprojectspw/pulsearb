"""Leitura das gravações JSONL gzip, em ordem cronológica global.

Os arquivos são rotacionados por hora, mas o nome do arquivo NÃO garante
ordem entre fontes: um evento gravado às 13:59:59.9 pode estar em outro
arquivo que um de 14:00:00.1. A ordenação canônica é `ts_mono_ns`, e este
módulo faz o merge das horas em ordem global.

Regras:
- `ts_mono_ns` é o relógio de ordenação; `ts_wall_ns` é só para datar
- registros meta (snapshot de descoberta, lacunas) vêm marcados, nunca
  misturados com eventos de feed sem aviso
- arquivo corrompido no fim (recorder morto no meio de uma linha) é
  tolerado com contagem, não com exceção: gravação de 72h não pode ser
  perdida por causa da última linha
"""

from __future__ import annotations

import gzip
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import orjson

from pulsearb.obs import get_logger
from pulsearb.recorder.writer import FONTES_META

log = get_logger("pulsearb.replay.reader")


@dataclass(frozen=True, slots=True)
class ReplayRecord:
    ts_mono_ns: int
    ts_wall_ns: int
    fonte: str
    payload: Any

    @property
    def is_meta(self) -> bool:
        """Snapshot de descoberta, lacuna, relatório — não veio do fio."""
        return self.fonte in FONTES_META


def _iter_file(path: Path) -> Iterator[tuple[ReplayRecord, bool]]:
    """Itera um arquivo. O bool é True quando a linha estava corrompida."""
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as handle:  # type: ignore[operator]
        for line in handle:
            if not line.strip():
                continue
            try:
                entry = orjson.loads(line)
                yield (
                    ReplayRecord(
                        ts_mono_ns=int(entry["ts_mono_ns"]),
                        ts_wall_ns=int(entry["ts_wall_ns"]),
                        fonte=str(entry["fonte"]),
                        payload=entry.get("payload"),
                    ),
                    False,
                )
            except (orjson.JSONDecodeError, KeyError, TypeError, ValueError):
                yield (
                    ReplayRecord(ts_mono_ns=0, ts_wall_ns=0, fonte="__corrompido__", payload=None),
                    True,
                )


class RecordingReader:
    """Lê um diretório (ou lista de arquivos) de gravação em ordem global."""

    def __init__(self, paths: str | Path | list[Path]) -> None:
        if isinstance(paths, list):
            self.files = sorted(paths)
        else:
            root = Path(paths)
            if root.is_file():
                self.files = [root]
            else:
                self.files = sorted(
                    [*root.glob("*.jsonl.gz"), *root.glob("*.jsonl")]
                )
        self.corrompidas = 0
        self.total = 0

    def __iter__(self) -> Iterator[ReplayRecord]:
        return self.iter_records()

    def iter_records(self, *, incluir_meta: bool = True) -> Iterator[ReplayRecord]:
        """Todos os registros, ordenados por ts_mono_ns.

        Carrega em memória para ordenar. Uma gravação de 72h cabe: a estimativa
        do runbook é da ordem de dezenas de MB/h comprimidos, e o processo de
        backtest é offline — trocar memória por simplicidade aqui é barato, e
        ordenação incremental por heap seria otimização prematura.
        """
        registros: list[ReplayRecord] = []
        for path in self.files:
            for record, corrompida in _iter_file(path):
                self.total += 1
                if corrompida:
                    self.corrompidas += 1
                    continue
                if not incluir_meta and record.is_meta:
                    continue
                registros.append(record)
        if self.corrompidas:
            log.warning(
                "linhas corrompidas ignoradas",
                n=self.corrompidas,
                total=self.total,
                arquivos=len(self.files),
            )
        registros.sort(key=lambda r: r.ts_mono_ns)
        yield from registros

    def gaps(self) -> list[dict[str, Any]]:
        """As lacunas que o recorder registrou. O backtest precisa vê-las."""
        return [
            record.payload
            for record in self.iter_records()
            if record.fonte == "gap" and isinstance(record.payload, dict)
        ]

    def discovery_snapshots(self) -> list[ReplayRecord]:
        return [r for r in self.iter_records() if r.fonte == "discovery_snapshot"]


def iter_records(paths: str | Path | list[Path], *, incluir_meta: bool = True) -> Iterator[
    ReplayRecord
]:
    """Atalho funcional para quem só quer varrer uma gravação."""
    yield from RecordingReader(paths).iter_records(incluir_meta=incluir_meta)
