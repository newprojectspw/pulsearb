"""Escrita do recorder: fila assíncrona + JSONL gzip com rotação.

Regra do hot path: ZERO I/O síncrono no caminho de recepção. O callback do
feed só faz queue.put_nowait(); um task separado consome a fila e escreve.
Se a fila encher (disco lento), o dado mais novo é descartado e CONTADO —
perder tick de gravação é aceitável, travar o feed não é.

Formato de cada linha (JSON):
    {"ts_mono_ns": ..., "ts_wall_ns": ..., "fonte": "rtds"|"poly_ws",
     "payload": <cru decodificado, ou string base64 se não-JSON>}
"""

from __future__ import annotations

import asyncio
import base64
import gzip
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

import orjson

from pulsearb.obs import get_logger


@dataclass(frozen=True, slots=True)
class RecordEnvelope:
    ts_mono_ns: int
    ts_wall_ns: int
    fonte: str
    raw: bytes

    def to_line(self) -> bytes:
        try:
            payload: Any = orjson.loads(self.raw)
        except orjson.JSONDecodeError:
            payload = {"_b64": base64.b64encode(self.raw).decode()}
        return orjson.dumps(
            {
                "ts_mono_ns": self.ts_mono_ns,
                "ts_wall_ns": self.ts_wall_ns,
                "fonte": self.fonte,
                "payload": payload,
            }
        )


class JsonlGzipWriter:
    """Consome a fila e escreve JSONL gzip, um arquivo por janela de rotação.

    Nome do arquivo: {prefixo}-{YYYYmmdd-HH}.jsonl.gz (UTC).
    """

    def __init__(
        self,
        *,
        output_dir: str | Path,
        prefix: str = "pulsearb",
        rotate_seconds: int = 3600,
        queue_max: int = 65536,
        clock: Any = time.time,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.prefix = prefix
        self.rotate_seconds = rotate_seconds
        self.clock = clock
        self.queue: asyncio.Queue[RecordEnvelope] = asyncio.Queue(maxsize=queue_max)
        self.dropped = 0
        self.written = 0
        self.log = get_logger("pulsearb.recorder")
        self._task: asyncio.Task[None] | None = None
        self._file: IO[bytes] | None = None
        self._file_slot: int = -1
        self._stopping = False

    # ---------------------------------------------------------------- hot path
    def submit(self, envelope: RecordEnvelope) -> None:
        """Chamado pelo callback do feed. Nunca bloqueia."""
        try:
            self.queue.put_nowait(envelope)
        except asyncio.QueueFull:
            self.dropped += 1

    # ------------------------------------------------------------------- ciclo
    async def start(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._stopping = False
        self._task = asyncio.create_task(self._drain(), name="recorder-writer")

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            await self._task
            self._task = None
        self._close_file()

    async def _drain(self) -> None:
        while True:
            try:
                envelope = await asyncio.wait_for(self.queue.get(), timeout=0.5)
            except TimeoutError:
                if self._stopping:
                    return
                continue
            self._write(envelope)
            # Esvazia rajadas sem voltar ao event loop a cada linha.
            while True:
                try:
                    self._write(self.queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            await asyncio.sleep(0)  # cede o loop

    def _write(self, envelope: RecordEnvelope) -> None:
        slot = int(self.clock()) // self.rotate_seconds
        if slot != self._file_slot or self._file is None:
            self._close_file()
            stamp = time.strftime(
                "%Y%m%d-%H%M", time.gmtime(slot * self.rotate_seconds)
            )
            path = self.output_dir / f"{self.prefix}-{stamp}.jsonl.gz"
            # gzip nível 1: rápido; a taxa de dados dos feeds é baixa o
            # suficiente para o writer não virar gargalo.
            self._file = gzip.open(path, "ab", compresslevel=1)
            self._file_slot = slot
            self.log.info("novo arquivo de gravação", arquivo=str(path))
        self._file.write(envelope.to_line() + b"\n")
        self.written += 1

    def _close_file(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
