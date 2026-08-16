"""Motor de decisão. No M1 só existe fees; fair value e sinal são M3."""

from pulsearb.engine.fees import fee_pp_por_share, fee_sobre_capital

__all__ = ["fee_pp_por_share", "fee_sobre_capital"]
