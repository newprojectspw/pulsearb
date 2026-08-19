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
import heapq
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

    # Registros mantidos em memória para corrigir a desordem local entre
    # feeds. A desordem observada é de milissegundos; a ~200 eventos/s medidos,
    # 50ms de inversão são ~10 registros. 5.000 cobre isso com três ordens de
    # grandeza de folga.
    #
    # O tamanho importa mais do que parece: um evento de book com 40 níveis
    # ocupa ~12 KB como objeto Python, então cada 1.000 de buffer custa ~12 MB.
    # Um buffer de 50k (o primeiro que tentei) custava 600 MB — limitado, sim,
    # mas com teto alto demais para o ganho.
    REORDER_BUFFER_PADRAO = 5_000

    def __init__(
        self,
        paths: str | Path | list[Path],
        *,
        reorder_buffer: int = REORDER_BUFFER_PADRAO,
    ) -> None:
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
        self.fora_de_ordem = 0
        self.reorder_buffer = max(1, reorder_buffer)

    def __iter__(self) -> Iterator[ReplayRecord]:
        return self.iter_records()

    def _iter_file_records(self, path: Path) -> Iterator[ReplayRecord]:
        """Registros válidos de UM arquivo, contando os corrompidos."""
        for record, corrompida in _iter_file(path):
            self.total += 1
            if corrompida:
                self.corrompidas += 1
                continue
            yield record

    def iter_records(self, *, incluir_meta: bool = True) -> Iterator[ReplayRecord]:
        """Todos os registros, ordenados por ts_mono_ns — em STREAMING.

        A versão anterior carregava tudo em memória para um `sort()` final,
        com um comentário meu dizendo que merge incremental "seria otimização
        prematura". A premissa era a estimativa de disco do runbook (~5 MB/h).
        A gravação real veio com **~400 MB/h**: 10 horas dão ~4 GB
        comprimidos, uns 20 GB de objetos Python. A versão antiga simplesmente
        não termina numa máquina de análise comum.

        Agora: `heapq.merge` sobre os arquivos (cada um lido preguiçosamente)
        mais um buffer de reordenação limitado. A memória fica proporcional ao
        buffer, não à gravação.

        Por que o buffer é necessário: dentro de um arquivo os registros vêm
        na ordem em que a fila do recorder drenou, que é *quase* a ordem de
        `ts_mono_ns` — três feeds submetem em paralelo, então há inversões
        locais de alguns milissegundos. O `heapq.merge` exige entradas
        ordenadas; o buffer absorve essa desordem local. Inversão maior que o
        buffer sairia fora de ordem, e por isso ela é CONTADA e reportada em
        vez de ignorada.
        """
        self.total = 0
        self.corrompidas = 0
        self.fora_de_ordem = 0

        fluxo = heapq.merge(
            *(self._iter_file_records(path) for path in self.files),
            key=lambda r: r.ts_mono_ns,
        )

        buffer: list[tuple[int, int, ReplayRecord]] = []
        contador = 0  # desempata sem comparar o ReplayRecord (não é ordenável)
        ultimo_emitido = -1

        def emitir(record: ReplayRecord) -> ReplayRecord:
            nonlocal ultimo_emitido
            if record.ts_mono_ns < ultimo_emitido:
                self.fora_de_ordem += 1
            else:
                ultimo_emitido = record.ts_mono_ns
            return record

        for record in fluxo:
            if not incluir_meta and record.is_meta:
                continue
            heapq.heappush(buffer, (record.ts_mono_ns, contador, record))
            contador += 1
            if len(buffer) > self.reorder_buffer:
                yield emitir(heapq.heappop(buffer)[2])
        while buffer:
            yield emitir(heapq.heappop(buffer)[2])

        if self.corrompidas:
            log.warning(
                "linhas corrompidas ignoradas",
                n=self.corrompidas,
                total=self.total,
                arquivos=len(self.files),
            )
        if self.fora_de_ordem:
            log.warning(
                "registros emitidos fora de ordem: inversão maior que o buffer",
                n=self.fora_de_ordem,
                buffer=self.reorder_buffer,
            )

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
