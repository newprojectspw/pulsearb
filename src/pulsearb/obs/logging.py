"""Logging JSON estruturado, uma linha por evento.

Regras:
- orjson para serializar (mesma lib do hot path, sem surpresa de formato)
- time.time_ns() só para REGISTRO; medição de latência é monotônica (obs/latency.py)
- nenhum segredo em log — campos com nomes suspeitos são redigidos por segurança
"""

from __future__ import annotations

import logging
import sys
import time
from typing import Any

import orjson

# Nunca logar valor de campo que pareça segredo, venha de onde vier.
_REDACT_MARKERS = ("key", "secret", "passphrase", "password", "token_secret", "private")


def _redact(extra: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for name, value in extra.items():
        lowered = name.lower()
        if any(marker in lowered for marker in _REDACT_MARKERS):
            clean[name] = "[REDIGIDO]"
        else:
            clean[name] = value
    return clean


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts_ns": time.time_ns(),
            "nivel": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            entry.update(_redact(extra))
        if record.exc_info and record.exc_info[0] is not None:
            entry["exc"] = self.formatException(record.exc_info)
        return orjson.dumps(entry, default=str).decode()


class _StructuredAdapter(logging.LoggerAdapter):
    """Permite logger.info("msg", campo=valor) sem brigar com a stdlib."""

    def process(self, msg: str, kwargs: Any) -> tuple[str, Any]:
        reserved = {"exc_info", "stack_info", "stacklevel"}
        extra_fields = {k: v for k, v in kwargs.items() if k not in reserved}
        passthrough = {k: v for k, v in kwargs.items() if k in reserved}
        passthrough["extra"] = {"extra_fields": extra_fields}
        return msg, passthrough


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


def get_logger(name: str) -> _StructuredAdapter:
    return _StructuredAdapter(logging.getLogger(name), {})
