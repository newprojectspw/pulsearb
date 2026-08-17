"""Reprodutor de gravações — a mesma interface dos feeds ao vivo.

O ponto central: **o código de modelo não sabe se está em replay ou em
produção.** Ele recebe `FeedEvent` de um callback, e pronto. Sem ramificação
`if replay:` em lugar nenhum — porque toda ramificação dessas é uma chance de
o backtest medir um código que não é o que vai rodar.

Três modos:
- `acelerado` (default): despeja tudo o mais rápido possível
- `tempo_real`: respeita os deltas de `ts_mono_ns` entre eventos
- `passo`: um evento por chamada de `step()`, para depuração
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from enum import StrEnum
from pathlib import Path
from typing import Any

import orjson

from pulsearb.feeds.base import FeedEvent
from pulsearb.replay.reader import RecordingReader, ReplayRecord


class ReplayMode(StrEnum):
    ACELERADO = "acelerado"
    TEMPO_REAL = "tempo_real"
    PASSO = "passo"


OnRecord = Callable[[FeedEvent], None]
OnMeta = Callable[[ReplayRecord], None]


class ReplayPlayer:
    """Reproduz uma gravação entregando FeedEvent, igual aos feeds ao vivo."""

    def __init__(
        self,
        paths: str | Path | list[Path],
        *,
        mode: ReplayMode = ReplayMode.ACELERADO,
        speed: float = 1.0,
        on_event: OnRecord | None = None,
        on_meta: OnMeta | None = None,
    ) -> None:
        if speed <= 0:
            raise ValueError("speed deve ser positivo")
        self.reader = RecordingReader(paths)
        self.mode = mode
        self.speed = speed
        self.on_event = on_event
        self.on_meta = on_meta
        self.emitidos = 0
        self.meta_emitidos = 0
        self.primeiro_ts_mono_ns: int | None = None
        self.ultimo_ts_mono_ns: int | None = None
        self._iterador: Iterator[ReplayRecord] | None = None

    # --------------------------------------------------------------- interno
    @staticmethod
    def _to_feed_event(record: ReplayRecord) -> FeedEvent:
        """Reconstrói o FeedEvent como o feed ao vivo o teria produzido.

        `raw` é re-serializado a partir do payload. Para o consumidor isso é
        indistinguível do original: o parse do hot path é orjson.loads, que é
        insensível a espaçamento e ordem de chaves.
        """
        payload = record.payload
        if isinstance(payload, dict) and "_b64" in payload:
            import base64

            raw = base64.b64decode(payload["_b64"])
            parsed = None
        else:
            raw = orjson.dumps(payload)
            parsed = payload
        return FeedEvent(
            source=record.fonte,
            ts_mono_ns=record.ts_mono_ns,
            ts_wall_ns=record.ts_wall_ns,
            raw=raw,
            parsed=parsed,
        )

    def _emit(self, record: ReplayRecord) -> None:
        if self.primeiro_ts_mono_ns is None:
            self.primeiro_ts_mono_ns = record.ts_mono_ns
        self.ultimo_ts_mono_ns = record.ts_mono_ns
        if record.is_meta:
            self.meta_emitidos += 1
            if self.on_meta is not None:
                self.on_meta(record)
            return
        self.emitidos += 1
        if self.on_event is not None:
            self.on_event(self._to_feed_event(record))

    # ---------------------------------------------------------------- síncrono
    def run_sync(self) -> None:
        """Modo acelerado sem event loop — é o caminho do backtest."""
        for record in self.reader.iter_records():
            self._emit(record)

    # ---------------------------------------------------------------- passo
    def step(self) -> ReplayRecord | None:
        """Emite UM registro. None quando a gravação acabou."""
        if self._iterador is None:
            self._iterador = self.reader.iter_records()
        record = next(self._iterador, None)
        if record is not None:
            self._emit(record)
        return record

    # ------------------------------------------------------------ assíncrono
    async def run(self) -> None:
        """Modo tempo real: respeita os deltas originais entre eventos."""
        if self.mode is ReplayMode.PASSO:
            raise RuntimeError("modo passo usa step(), não run()")
        anterior: int | None = None
        for record in self.reader.iter_records():
            if self.mode is ReplayMode.TEMPO_REAL and anterior is not None:
                delta_s = (record.ts_mono_ns - anterior) / 1e9 / self.speed
                if delta_s > 0:
                    await asyncio.sleep(delta_s)
            anterior = record.ts_mono_ns
            self._emit(record)
            if self.mode is ReplayMode.ACELERADO:
                # Cede o loop periodicamente para não travar o processo inteiro.
                if self.emitidos % 1000 == 0:
                    await asyncio.sleep(0)

    # ---------------------------------------------------------------- resumo
    def resumo(self) -> dict[str, Any]:
        span_s = (
            (self.ultimo_ts_mono_ns - self.primeiro_ts_mono_ns) / 1e9
            if self.primeiro_ts_mono_ns is not None and self.ultimo_ts_mono_ns is not None
            else 0.0
        )
        return {
            "eventos": self.emitidos,
            "meta": self.meta_emitidos,
            "span_s": round(span_s, 3),
            "linhas_corrompidas": self.reader.corrompidas,
            "arquivos": len(self.reader.files),
        }
