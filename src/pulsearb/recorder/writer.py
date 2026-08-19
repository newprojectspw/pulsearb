"""Escrita do recorder: fila assíncrona + JSONL gzip com rotação.

Regra do hot path: ZERO I/O síncrono no caminho de recepção. O callback do
feed só faz queue.put_nowait(); um task separado consome as filas e escreve.

DOIS CANAIS, e a distinção é a correção central do M2.2 (A.1):

- **CANAL_PADRAO** (com descarte): tick de preço, snapshot de descoberta,
  lacuna. Perder um tick sob pressão de disco é aceitável — o seguinte vem
  em ~1s e o dado é auto-suficiente.
- **CANAL_BOOK** (sem perda): `book` e `price_change`. Perder UM delta
  corrompe o livro reconstruído dali em diante, em silêncio, e todo número
  do backtest continua saindo — bonito e errado. Esta fila é muito maior, e
  se ainda assim encher, isso é INCIDENTE: fica registrado e dispara resync
  do token afetado, nunca segue em silêncio.

O lote de cada ciclo é ordenado por `ts_mono_ns` antes de escrever, para que
drenar duas filas não introduza desordem que o replay teria de absorver.

Formato de cada linha (JSON):
    {"ts_mono_ns": ..., "ts_wall_ns": ..., "fonte": "rtds"|"poly_ws",
     "payload": <cru decodificado, ou string base64 se não-JSON>}
"""

from __future__ import annotations

import asyncio
import base64
import gzip
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

import orjson

from pulsearb.obs import get_logger

# Fontes SINTETIZADAS pelo recorder — não vieram do fio. O replay as trata
# como metadados, não como eventos de feed.
FONTE_DISCOVERY = "discovery_snapshot"
FONTE_GAP = "gap"
# Incidente de fila sem perda: gravado como registro próprio para que a perda
# apareça na gravação, e não só num contador que ninguém leu.
FONTE_INCIDENTE = "incidente_gravacao"
# Pedido de snapshot novo depois de perda detectada. Também é sintetizado
# por nós, e por isso tem fonte própria.
FONTE_RESYNC = "resync_book"
# Resolução obtida por polling da Gamma, não pelo WS. Fonte própria para que
# ninguém a confunda com um evento que veio do fio (ver recorder/__main__).
FONTE_RESOLUCAO_SINTETICA = "resolucao_via_gamma"
FONTES_META = frozenset(
    {
        FONTE_DISCOVERY,
        FONTE_GAP,
        FONTE_INCIDENTE,
        FONTE_RESYNC,
        "recorder_relatorio",
        FONTE_RESOLUCAO_SINTETICA,
    }
)

# Canais do writer. Ver o cabeçalho do módulo.
CANAL_PADRAO = "padrao"
CANAL_BOOK = "book"
CANAIS = (CANAL_BOOK, CANAL_PADRAO)


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
        queue_max_book: int = 524288,
        flush_a_cada: int = 2000,
        max_por_ciclo: int = 20000,
        ao_perder_book: Callable[[RecordEnvelope], None] | None = None,
        clock: Any = time.time,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.prefix = prefix
        self.rotate_seconds = rotate_seconds
        self.flush_a_cada = max(1, flush_a_cada)
        self.max_por_ciclo = max(1, max_por_ciclo)
        self.clock = clock
        # A fila sem perda é 8x maior: um livro de 150 tokens em rajada gera
        # muito mais eventos que os ticks de preço, e é justamente a rajada
        # que não pode ser descartada.
        self.queues: dict[str, asyncio.Queue[RecordEnvelope]] = {
            CANAL_BOOK: asyncio.Queue(maxsize=queue_max_book),
            CANAL_PADRAO: asyncio.Queue(maxsize=queue_max),
        }
        self.ao_perder_book = ao_perder_book
        self.dropped = 0
        self.dropped_por_canal: Counter[str] = Counter()
        self.written = 0
        self.log = get_logger("pulsearb.recorder")
        self._task: asyncio.Task[None] | None = None
        self._file: IO[bytes] | None = None
        self._file_slot: int = -1
        self._desde_flush = 0
        self._stopping = False

    # ---------------------------------------------------------------- hot path
    def submit(self, envelope: RecordEnvelope, *, canal: str = CANAL_PADRAO) -> None:
        """Chamado pelo callback do feed. Nunca bloqueia.

        Descarte no CANAL_BOOK não é "perda aceitável": é incidente. O
        callback `ao_perder_book` existe para o recorder marcar o token como
        corrompido e forçar resync — seguir aplicando deltas sobre um livro
        que já sabemos furado produziria um resultado plausível e errado.
        """
        fila = self.queues.get(canal) or self.queues[CANAL_PADRAO]
        try:
            fila.put_nowait(envelope)
        except asyncio.QueueFull:
            self.dropped += 1
            self.dropped_por_canal[canal] += 1
            if canal == CANAL_BOOK and self.ao_perder_book is not None:
                self.ao_perder_book(envelope)

    @property
    def queue(self) -> asyncio.Queue[RecordEnvelope]:
        """Compat: o canal padrão. Havia uma fila só até o M2.2."""
        return self.queues[CANAL_PADRAO]

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

    def _coletar(self) -> list[RecordEnvelope]:
        """Tudo que está disponível nas duas filas, até o teto do ciclo."""
        lote: list[RecordEnvelope] = []
        for canal in CANAIS:
            fila = self.queues[canal]
            while len(lote) < self.max_por_ciclo:
                try:
                    lote.append(fila.get_nowait())
                except asyncio.QueueEmpty:
                    break
        return lote

    async def _drain(self) -> None:
        """Drena as duas filas, em ordem de `ts_mono_ns`.

        A espera é por polling curto em vez de `await queue.get()` porque são
        duas filas: esperar numa delas deixaria a outra parada. 5ms de sono só
        acontece com as duas vazias — sob carga o laço nunca dorme.

        O `sort` por ciclo é o que impede que drenar duas filas embaralhe a
        saída: sem ele, uma rajada de book sairia inteira antes dos ticks de
        preço do mesmo instante, e o buffer de reordenação do replay teria de
        absorver a diferença.
        """
        while True:
            lote = self._coletar()
            if not lote:
                if self._stopping:
                    return
                await asyncio.sleep(0.005)
                continue
            lote.sort(key=lambda envelope: envelope.ts_mono_ns)
            for envelope in lote:
                self._write(envelope)
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
