"""O que roda ao vivo entre a descoberta e o executor.

O backtest recebe janelas prontas de uma gravação. Ao vivo elas nascem,
envelhecem e morrem enquanto o bot roda — janela de 5 minutos vira outra a
cada 5 minutos. Este pacote é quem sabe disso.
"""

from pulsearb.live.rastreador import JanelaAoVivo, RastreadorDeJanelas

__all__ = ["JanelaAoVivo", "RastreadorDeJanelas"]
