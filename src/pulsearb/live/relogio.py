"""Anomalia de tempo no caminho ao vivo: o que dá para medir, e o que não dá.

Existe por causa do item 3.10 do `ESTADO_PARA_LIVE`: a trava de relógio era a
única das três sem FONTE. Feed velho e spread anômalo já eram medidos; "o
relógio derivou" não era medido em lugar nenhum.

## O que a medição É

Cada tick do feed-verdade traz o carimbo do SERVIDOR. Quando ele chega,
olhamos o nosso relógio. Chamando `offset` a diferença entre o nosso relógio e
o verdadeiro (negativo = estamos atrasados) e `latencia` o tempo de trânsito:

    atraso_medido = chegada_local − carimbo_servidor = latencia + offset

São **duas incógnitas numa equação só**, e é medição de UMA via: não há como
separá-las.

## O que ela NÃO é, e isto foi um erro meu — pego na revisão do PR #47

A primeira versão deste módulo afirmava que o atraso é um **limite superior**
da deriva: "pequeno prova relógio bom". **É falso, e falso na direção
perigosa.** As duas parcelas têm sinais independentes e se CANCELAM:

| offset | latência | medido | portão de 250 ms | erro real no `seconds_left` |
|---|---|---|---|---|
| −400 ms | 400 ms | **0 ms** | **passa** | **400 ms** |
| −100 ms | 120 ms | +20 ms | passa | 100 ms |
| +300 ms | 80 ms | +380 ms | recusa | 300 ms |

Relógio local ATRASADO é exatamente o caso que infla o `seconds_left` — o bot
opera achando que sobra mais tempo do que sobra — e é exatamente o caso que a
latência positiva mascara. Um `abs()` depois não recupera nada: o valor já
saiu zerado da subtração.

## Então o que esta trava vale

Ela é **detector de anomalia**, não certificado de relógio:

- `|atraso|` grande PROVA que algo está grande — latência, offset, ou os dois.
  Recusar aí está certo.
- `|atraso|` pequeno **não prova nada** sobre o offset. Não é permissão; é
  ausência de alarme deste sensor.

Para fechar a metade que falta há duas peças, e as duas ficam registradas
porque nenhuma cabe aqui dentro:

1. **Sincronia verificada (NTP/chrony) é PRÉ-CONDIÇÃO de deploy**, não algo
   que este módulo possa provar. Um feed de uma via não vira medição de duas
   vias por esforço de software.
2. **O salto do relógio, esse dá para pegar aqui** — e é o modo de falhar mais
   comum na prática: o NTP corrige de vez e o relógio pula no meio da
   operação. `_Saltos` compara o avanço do relógio de parede com o do relógio
   monótono entre dois ticks, lendo os DOIS por conta própria; se discordam,
   o de parede pulou. Isso o sensor de uma via esconde, e o monótono não.

## Duas decisões de forma

**Mediana, não média.** Um GC de meio segundo desloca a média e deixaria o
portão fechado por minutos depois de o problema passar.

**Uma janela POR ATIVO, e o portão lê a PIOR** — também da revisão do #47. Com
uma janela só, um ativo continuamente atrasado entre oito saudáveis fica
abaixo da mediana global: os ticks dele chegam sem parar, então a checagem de
feed velho também não acusa, e uma ordem naquele ativo usaria preço velho com
o portão dizendo que está tudo bem.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from statistics import median
from typing import Any

#: Quantas amostras entram na mediana de cada ativo. A ~1 tick/s, 64 amostras
#: são ~1 minuto: curto o bastante para o portão reagir, longo o bastante para
#: um pico isolado não decidir sozinho.
AMOSTRAS_NA_JANELA = 64

#: Depois disto a medição não descreve mais o agora. Um bot que ficou sem feed
#: e volta a decidir com a última mediana estaria usando número velho para
#: afirmar que o relógio está bom.
IDADE_MAXIMA_MS = 10_000

#: Discordância entre o avanço do relógio de parede e o do monótono a partir
#: da qual se chama de salto. Folgada de propósito: o monótono e o de parede
#: não são lidos no mesmo instante, e o escorregão normal do NTP é contínuo e
#: pequeno. 100 ms separa o ajuste fino do pulo.
LIMIAR_DE_SALTO_MS = 100.0

#: Por quanto tempo um salto detectado continua recusando. O salto acontece num
#: instante, mas invalida a janela de amostras inteira — as medianas anteriores
#: foram calculadas com o relógio antigo.
CARENCIA_APOS_SALTO_MS = 30_000


class _Saltos:
    """Detecta que o relógio de PAREDE pulou, usando o monótono como régua.

    O monótono não é ajustado por NTP nem por mão humana: entre duas leituras,
    ele avança o tempo que passou de verdade. Se o de parede avançou muito
    mais (ou menos), quem se mexeu foi ele.
    """

    __slots__ = (
        "_anterior",
        "_monotonico",
        "_parede",
        "_pior_ms",
        "_saltos",
        "_ultimo_salto_ms",
    )

    def __init__(
        self,
        monotonico: Callable[[], float] = time.monotonic,
        parede: Callable[[], float] = time.time,
    ) -> None:
        self._monotonico = monotonico
        self._parede = parede
        #: (parede_ms, monotonico_ms) da última observação.
        self._anterior: tuple[float, float] | None = None
        self._ultimo_salto_ms: float | None = None
        self._saltos = 0
        self._pior_ms = 0.0

    def observar(self) -> None:
        """Lê os DOIS relógios aqui dentro, no mesmo instante.

        Não recebe o `chegada_ms` do chamador de propósito, e a primeira
        versão recebia — era defeito. O espaçamento entre ticks e o jitter da
        rede entram no `chegada_ms`, e não no monótono: comparar um com o
        outro fazia um feed reproduzido (carimbos sintéticos de segundo em
        segundo) parecer relógio pulando. A pergunta aqui é sobre a MÁQUINA,
        não sobre o feed, e a máquina se responde sozinha.
        """
        parede_ms = self._parede() * 1000.0
        mono_ms = self._monotonico() * 1000.0
        if self._anterior is not None:
            parede_antes, mono_antes = self._anterior
            desvio = abs((parede_ms - parede_antes) - (mono_ms - mono_antes))
            if desvio > LIMIAR_DE_SALTO_MS:
                self._saltos += 1
                self._pior_ms = max(self._pior_ms, desvio)
                self._ultimo_salto_ms = parede_ms
        self._anterior = (parede_ms, mono_ms)

    def em_carencia(self, *, agora_ms: int) -> bool:
        """O salto foi recente o bastante para invalidar a janela?

        `agora_ms` vem de quem pergunta (o portão), e o carimbo do salto é do
        relógio de parede DEPOIS do pulo — os dois estão na mesma escala.
        """
        if self._ultimo_salto_ms is None:
            return False
        return abs(agora_ms - self._ultimo_salto_ms) <= CARENCIA_APOS_SALTO_MS

    def resumo(self) -> dict[str, Any]:
        return {
            "saltos": self._saltos,
            "pior_salto_ms": round(self._pior_ms, 1) if self._saltos else None,
        }


class RelogioDoServidor:
    """Sensor de anomalia de tempo. NÃO é certificado de relógio — ver o módulo.

    Alimente com `anotar()` a cada tick do feed-verdade; consulte com
    `atraso_ms()` antes de cada ordem. `None` significa "não sei", e quem
    consulta trata não-sei como recusa, nunca como zero.
    """

    __slots__ = ("_fora_de_ordem", "_por_ativo", "_saltos", "_ultima_ms", "_vistos")

    def __init__(
        self,
        monotonico: Callable[[], float] = time.monotonic,
        parede: Callable[[], float] = time.time,
    ) -> None:
        self._por_ativo: dict[str, deque[float]] = {}
        self._ultima_ms: dict[str, int] = {}
        self._saltos = _Saltos(monotonico, parede)
        self._vistos = 0
        self._fora_de_ordem = 0

    def anotar(self, *, asset: str, ts_servidor_ms: int, chegada_ms: int) -> None:
        """Uma observação. `chegada_ms` é o nosso relógio quando o tick chegou.

        Atraso NEGATIVO — carimbo do servidor à frente do nosso relógio — é
        guardado como veio, não zerado. É a assinatura mais limpa que resta de
        relógio local atrasado depois que a cancelação come o resto, e apagá-la
        cegaria o sensor justamente onde ele ainda enxerga.
        """
        self._vistos += 1
        atraso = float(chegada_ms - ts_servidor_ms)
        if atraso < 0:
            self._fora_de_ordem += 1
        janela = self._por_ativo.get(asset)
        if janela is None:
            janela = deque(maxlen=AMOSTRAS_NA_JANELA)
            self._por_ativo[asset] = janela
        janela.append(atraso)
        self._ultima_ms[asset] = chegada_ms
        self._saltos.observar()

    def por_ativo_ms(self, *, agora_ms: int) -> dict[str, float]:
        """A mediana de cada ativo com amostra fresca."""
        return {
            asset: median(janela)
            for asset, janela in self._por_ativo.items()
            if janela and agora_ms - self._ultima_ms[asset] <= IDADE_MAXIMA_MS
        }

    def atraso_ms(self, *, agora_ms: int) -> float | None:
        """O PIOR atraso entre os ativos frescos, ou `None` para "não sei".

        Pior, e não médio: um ativo atrasado entre oito saudáveis é um preço
        velho a caminho de uma ordem, e a média o esconderia. Fechar tudo por
        causa de um é o lado certo para errar.

        `None` em três casos, e os três são "não sei": nenhum ativo com
        amostra, todos com amostra velha, ou salto de relógio recente — depois
        de um salto as medianas foram calculadas com o relógio antigo.
        """
        if self._saltos.em_carencia(agora_ms=agora_ms):
            return None
        frescos = self.por_ativo_ms(agora_ms=agora_ms)
        if not frescos:
            return None
        return max(frescos.values(), key=abs)

    def resumo(self, *, agora_ms: int) -> dict[str, Any]:
        """O que o diário e o dashboard mostram."""
        frescos = self.por_ativo_ms(agora_ms=agora_ms)
        pior = self.atraso_ms(agora_ms=agora_ms)
        return {
            "pior_atraso_ms": round(pior, 1) if pior is not None else None,
            "por_ativo_ms": {a: round(v, 1) for a, v in sorted(frescos.items())},
            "ativos_frescos": len(frescos),
            "ativos_vistos": len(self._por_ativo),
            "ticks_vistos": self._vistos,
            "carimbo_no_futuro": self._fora_de_ordem,
            "relogio_de_parede": self._saltos.resumo(),
            "nota": (
                "SENSOR DE ANOMALIA, NAO CERTIFICADO DE RELOGIO. O numero e "
                "latencia MAIS offset do relogio, duas incognitas numa "
                "equacao so, e elas se CANCELAM: relogio 400 ms atrasado com "
                "400 ms de latencia mede ZERO e passa no portao, com o "
                "seconds_left errado em 400 ms. Valor grande prova que algo "
                "esta grande; valor pequeno NAO prova relogio bom. A metade "
                "que falta e sincronia verificada (NTP/chrony) como "
                "PRE-CONDICAO de deploy — feed de uma via nao vira medicao de "
                "duas vias. `relogio_de_parede.saltos` pega o modo de falhar "
                "mais comum, que e o NTP corrigir de vez no meio da operacao: "
                "compara o avanco do relogio de parede com o do monotono."
            ),
        }
