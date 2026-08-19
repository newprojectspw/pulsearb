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

# Fontes SINTETIZADAS pelo recorder — não vieram do fio. O replay as trata
# como metadados, não como eventos de feed.
FONTE_DISCOVERY = "discovery_snapshot"
FONTE_GAP = "gap"
# Resolução obtida por polling da Gamma, não pelo WS. Fonte própria para que
# ninguém a confunda com um evento que veio do fio (ver recorder/__main__).
FONTE_RESOLUCAO_SINTETICA = "resolucao_via_gamma"
FONTES_META = frozenset(
    {FONTE_DISCOVERY, FONTE_GAP, "recorder_relatorio", FONTE_RESOLUCAO_SINTETICA}
)


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
        flush_a_cada: int = 2000,
        clock: Any = time.time,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.prefix = prefix
        self.rotate_seconds = rotate_seconds
        self.flush_a_cada = max(1, flush_a_cada)
        self.clock = clock
        self.queue: asyncio.Queue[RecordEnvelope] = asyncio.Queue(maxsize=queue_max)
        self.dropped = 0
        self.written = 0
        self.log = get_logger("pulsearb.recorder")
        self._task: asyncio.Task[None] | None = None
        self._file: IO[bytes] | None = None
        self._file_slot: int = -1
        self._desde_flush = 0
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

    def _caminho_livre(self, slot: int) -> Path:
        """Caminho da hora, com sufixo se o arquivo já existir.

        NUNCA reabrir em modo append. Se o processo morreu no meio de uma
        escrita (systemd Restart, OOM), o último membro gzip do arquivo ficou
        truncado; anexar um membro NOVO depois de um truncado produz um
        arquivo que o `gzip -t` recusa inteiro — "format violated". Foi assim
        que 3 de 26 arquivos nasceram inválidos em produção, sempre nas horas
        em que houve reinício.

        Abrindo um arquivo novo (`-002`, `-003`...), o dano fica contido: o
        arquivo truncado perde só a cauda, e o replay o lê até onde dá.
        """
        stamp = time.strftime("%Y%m%d-%H%M", time.gmtime(slot * self.rotate_seconds))
        base = self.output_dir / f"{self.prefix}-{stamp}.jsonl.gz"
        if not base.exists():
            return base
        for sufixo in range(2, 1000):
            alternativo = self.output_dir / f"{self.prefix}-{stamp}-{sufixo:03d}.jsonl.gz"
            if not alternativo.exists():
                self.log.warning(
                    "arquivo da hora já existe (reinício?): abrindo um novo",
                    existente=str(base),
                    novo=str(alternativo),
                )
                return alternativo
        raise RuntimeError(f"arquivos demais para a hora {stamp}")

    def _write(self, envelope: RecordEnvelope) -> None:
        slot = int(self.clock()) // self.rotate_seconds
        if slot != self._file_slot or self._file is None:
            self._close_file()
            path = self._caminho_livre(slot)
            # "wb", não "ab": ver _caminho_livre.
            # gzip nível 1: rápido; o writer não pode virar gargalo.
            self._file = gzip.open(path, "wb", compresslevel=1)
            self._file_slot = slot
            self._desde_flush = 0
            self.log.info("novo arquivo de gravação", arquivo=str(path))
        self._file.write(envelope.to_line() + b"\n")
        self.written += 1
        self._desde_flush += 1
        # Flush periódico: uma morte súbita perde no máximo o último lote, em
        # vez da última hora. O custo é pequeno porque é a cada N linhas, não
        # a cada linha.
        if self._desde_flush >= self.flush_a_cada:
            self._file.flush()
            self._desde_flush = 0

    def _close_file(self) -> None:
        """Fecha finalizando o membro gzip (trailer + CRC).

        Sem o close explícito o arquivo fica sem trailer e o `gzip -t` recusa.
        O `finally` garante que o handle some mesmo se o flush falhar por
        disco cheio — senão a próxima rotação tentaria escrever no mesmo
        handle quebrado.
        """
        if self._file is None:
            return
        try:
            self._file.flush()
        except OSError as erro:
            self.log.warning("falha ao dar flush no fechamento", erro=str(erro))
        finally:
            try:
                self._file.close()
            except OSError as erro:
                self.log.warning("falha ao fechar o arquivo", erro=str(erro))
            self._file = None
            self._desde_flush = 0
