"""A deriva entre o nosso relógio e o do servidor, medida no caminho ao vivo.

Existe por causa do item 3.10 do `ESTADO_PARA_LIVE`: a trava de relógio era a
única das três que não tinha FONTE. Feed velho e spread anômalo já eram
medidos; "o relógio derivou" não era medido em lugar nenhum, então o portão
não podia recusar por isso nem quando fosse verdade.

**O que é medido, exatamente.** Cada tick do feed-verdade carrega o carimbo do
SERVIDOR (`src_timestamp_ms`). Quando ele chega, olhamos o nosso relógio. A
diferença é

    atraso = chegada_local − carimbo_do_servidor

e ela NÃO é a deriva do relógio: é deriva **mais** latência de rede, mais
tempo de fila no processo. As três estão somadas e não há como separá-las com
uma fonte só.

**Por que isso ainda serve, e serve bem.** O atraso é um limite SUPERIOR da
deriva: se ele está pequeno, a deriva é pequena, necessariamente. Se está
grande, não sabemos qual das três causas é — e nenhuma delas é aceitável para
quem decide por `seconds_left`, que é a distância entre o NOSSO agora e o
fechamento da janela. Um relógio adiantado em 300 ms e uma rede atrasada em
300 ms produzem o mesmo erro na mesma direção: operar achando que há mais
tempo do que há.

Por isso a métrica publicada se chama `atraso`, e não `deriva`. Chamar de
deriva o que é deriva-mais-latência seria prometer uma decomposição que esta
medição não faz.

**Mediana, não média.** Um GC de meio segundo ou um pico de rede desloca a
média e deixaria o portão fechado por minutos depois de o problema passar. A
mediana de uma janela curta ignora o pico isolado e reage rápido quando o
atraso vira regime.
"""

from __future__ import annotations

from collections import deque
from statistics import median
from typing import Any

#: Quantas amostras entram na mediana. A ~1 tick/s por ativo e 8 ativos, 64
#: amostras são ~8 segundos de feed: curto o bastante para o portão reagir a
#: um relógio que acabou de escorregar, longo o bastante para um pico isolado
#: não decidir sozinho.
AMOSTRAS_NA_JANELA = 64

#: Depois disto a medição não descreve mais o agora. Um bot que ficou sem
#: feed por 30 s e volta a decidir com a última mediana estaria usando um
#: número velho para dizer que o relógio está bom.
IDADE_MAXIMA_MS = 10_000


class RelogioDoServidor:
    """O atraso entre o carimbo do servidor e a nossa chegada, ao vivo.

    Alimente com `anotar()` a cada tick do feed-verdade; consulte com
    `atraso_ms()` antes de cada ordem. `None` significa "não sei" — e quem
    consulta trata não-sei como recusa, nunca como zero.
    """

    __slots__ = ("_amostras", "_fora_de_ordem", "_ultima_chegada_ms", "_vistos")

    def __init__(self) -> None:
        self._amostras: deque[float] = deque(maxlen=AMOSTRAS_NA_JANELA)
        self._ultima_chegada_ms: int | None = None
        self._vistos = 0
        self._fora_de_ordem = 0

    def anotar(self, *, ts_servidor_ms: int, chegada_ms: int) -> None:
        """Uma observação. `chegada_ms` é o nosso relógio quando o tick chegou.

        Atraso NEGATIVO — carimbo do servidor no futuro do nosso relógio — é
        guardado como veio, não zerado. É a assinatura mais limpa que existe
        de relógio local atrasado, e apagá-la esconderia justamente o defeito
        que este módulo procura. Só entra no contador `fora_de_ordem` para
        que o resumo mostre que aconteceu.
        """
        self._vistos += 1
        atraso = float(chegada_ms - ts_servidor_ms)
        if atraso < 0:
            self._fora_de_ordem += 1
        self._amostras.append(atraso)
        self._ultima_chegada_ms = chegada_ms

    def atraso_ms(self, *, agora_ms: int) -> float | None:
        """A mediana do atraso, ou `None` se não há medição utilizável.

        `None` em dois casos, e os dois são "não sei": nunca chegou tick, ou o
        último chegou faz mais que `IDADE_MAXIMA_MS`.
        """
        if not self._amostras or self._ultima_chegada_ms is None:
            return None
        if agora_ms - self._ultima_chegada_ms > IDADE_MAXIMA_MS:
            return None
        return median(self._amostras)

    def resumo(self, *, agora_ms: int) -> dict[str, Any]:
        """O que o diário e o dashboard mostram."""
        atraso = self.atraso_ms(agora_ms=agora_ms)
        return {
            "atraso_mediano_ms": round(atraso, 1) if atraso is not None else None,
            "amostras": len(self._amostras),
            "ticks_vistos": self._vistos,
            "carimbo_no_futuro": self._fora_de_ordem,
            "idade_da_ultima_ms": (
                agora_ms - self._ultima_chegada_ms
                if self._ultima_chegada_ms is not None
                else None
            ),
            "nota": (
                "`atraso_mediano_ms` e deriva de relogio MAIS latencia de rede "
                "MAIS fila no processo, somadas e nao separaveis com esta "
                "fonte. Serve como limite superior da deriva: pequeno prova "
                "relogio bom; grande nao diz qual das tres e, e nenhuma das "
                "tres e aceitavel para quem decide por seconds_left. "
                "`carimbo_no_futuro` > 0 e a assinatura de relogio LOCAL "
                "atrasado — o servidor nao manda evento do futuro."
            ),
        }
