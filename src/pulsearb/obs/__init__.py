"""Observabilidade: logging JSON estruturado e histogramas de latência."""

from pulsearb.obs.latency import LatencyHistogram
from pulsearb.obs.logging import get_logger, setup_logging

__all__ = ["LatencyHistogram", "get_logger", "setup_logging"]
