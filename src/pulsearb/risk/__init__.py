"""Travas de risco. Nada envia ordem sem passar por aqui.

O M4 inteiro é construído em cima deste pacote, e nesta ordem de propósito:
os portões vêm ANTES do cliente de ordens. Um cliente de ordens sem portão é
uma máquina de perder dinheiro que já funciona; um portão sem cliente é um
teste que não custa nada.
"""

from pulsearb.risk.autorizacao import (
    FRASE_DE_ACEITE,
    AutorizacaoParaLive,
    autorizacao_para_live,
)
from pulsearb.risk.gates import (
    MOTIVOS,
    Decisao,
    OrdemPretendida,
    PortaoDeRisco,
    RegistroDoDia,
)

__all__ = [
    "FRASE_DE_ACEITE",
    "MOTIVOS",
    "AutorizacaoParaLive",
    "Decisao",
    "OrdemPretendida",
    "PortaoDeRisco",
    "RegistroDoDia",
    "autorizacao_para_live",
]
