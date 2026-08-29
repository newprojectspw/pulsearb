"""V(t) medida — a variância de transição do `twap_sixty`, pronta para o modelo.

O `prob_up_twap` DERIVAVA a variância do valor de liquidação, e errou três
vezes de uma vez (§2d-ter do `VEREDITO_M2.md`):

1. aplicava uma redução por média que a liquidação real não tem — a §13.8 do
   `API_NOTES.md` verificou que a janela resolve por **um ponto** do stream no
   fechamento, sem média nenhuma;
2. ignorava o tempo que o preço caminha ANTES de a janela de 60 s começar;
3. usava `sigma_1s` medido sobre a série já suavizada, que vale ~1/36 da
   volatilidade do subjacente.

Medido em 24 h de 24/08, os três compostos davam **39 a 48 vezes** de erro na
variância na banda operada — 6,3 a 6,9 vezes no desvio-padrão. Com o desvio
seis vezes menor que o real, `P(Up)` satura em 0 e 1, e é isso que o critério
1.3 media como superconfiança.

Este módulo não conserta a derivação: **substitui a derivação pela medição**.
É a mesma metodologia com que a §13.8 achou a âncora — engenharia reversa
sobre o gravado, em vez de suposição sobre o processo.

## A regra de método que vem junto

A curva tem de vir de um período **anterior** ao avaliado. Medir e avaliar no
mesmo dia é ajuste in-sample, que é exatamente o que a §2d proibiu para o
fator de encolhimento. Este módulo não sabe de que dia veio o arquivo — quem
sabe é quem passa o caminho, e o relatório do backtest publica a origem.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

#: Acima do maior horizonte medido, V(t) é extrapolada LINEAR em t.
#:
#: Não é escolha de conveniência: é a propriedade 2 da §2d-ter, medida. No
#: regime longo `V(t)/t` fica praticamente constante (variação de 1,10× entre
#: 240 e 600 s no btc) — é a caminhada aleatória do subjacente, que a
#: suavização de 60 s não altera. Extrapolar pela inclinação do último trecho
#: medido (1,11 em log-log) pareceria mais fiel, mas projetada até as 4 h de
#: uma janela longa ela superestima; a reta é a forma que a própria medição
#: diz valer lá.
EXPOENTE_NO_LONGO = 1.0


@dataclass(frozen=True, slots=True)
class CurvaDeVariancia:
    """V(t) de um ativo, interpolada entre os horizontes medidos.

    `pontos` são `(horizonte_s, variancia_do_retorno_relativo)`, ordenados. A
    variância é do retorno RELATIVO, então entra no modelo como `V(t)·S²`.
    """

    asset: str
    pontos: tuple[tuple[float, float], ...]
    origem: str = "desconhecida"

    def __post_init__(self) -> None:
        if len(self.pontos) < 2:
            raise ValueError(
                f"curva de {self.asset!r} precisa de ao menos 2 horizontes "
                f"medidos, veio com {len(self.pontos)}"
            )
        for h, v in self.pontos:
            if h <= 0 or v <= 0:
                raise ValueError(
                    f"curva de {self.asset!r} tem ponto não positivo: ({h}, {v})"
                )

    def variancia(self, seconds_left: float) -> float:
        """V(t) no horizonte pedido, em unidades de retorno relativo ao quadrado.

        Interpolação em log-log porque a curva é uma lei de potência em cada
        regime: expoente ~1,8 no curto (a suavização ainda domina) e ~1 no
        longo. Em escala linear a interpolação entre 60 s e 120 s erraria o
        joelho, que é justamente onde a banda operada vive.
        """
        t = max(seconds_left, 0.0)
        if t <= 0.0:
            return 0.0

        primeiro_h, primeiro_v = self.pontos[0]
        ultimo_h, ultimo_v = self.pontos[-1]

        if t <= primeiro_h:
            # Abaixo do menor horizonte medido, segue a inclinação do primeiro
            # trecho. Não é chute: é a única informação que a medição dá sobre
            # essa região, e o alternativo (assumir linear) contradiria a
            # propriedade 3.
            return primeiro_v * (t / primeiro_h) ** self._inclinacao(0, 1)
        if t >= ultimo_h:
            return ultimo_v * (t / ultimo_h) ** EXPOENTE_NO_LONGO

        for i in range(len(self.pontos) - 1):
            h0, v0 = self.pontos[i]
            h1, v1 = self.pontos[i + 1]
            if h0 <= t <= h1:
                peso = math.log(t / h0) / math.log(h1 / h0)
                return v0 * (v1 / v0) ** peso
        raise AssertionError("horizonte dentro da curva sem trecho — curva desordenada")

    def _inclinacao(self, i: int, j: int) -> float:
        h0, v0 = self.pontos[i]
        h1, v1 = self.pontos[j]
        return math.log(v1 / v0) / math.log(h1 / h0)

    def desvio(self, seconds_left: float, spot: float) -> float:
        """Desvio-padrão do valor de liquidação, em unidades de preço."""
        return math.sqrt(self.variancia(seconds_left)) * spot


@dataclass(frozen=True, slots=True)
class CurvasPorAtivo:
    """As curvas de todos os ativos, como o relatório da medição as entrega.

    `para()` devolve `None` quando o ativo não foi medido — e quem chama TEM
    de tratar isso como "não opera", nunca como "usa o modelo velho". Misturar
    os dois modelos na mesma rodada produziria um relatório em que metade das
    probabilidades vem de uma física e metade de outra, sem aviso: é a mesma
    forma do defeito que a §2d-bis achou no 1.4, com duas populações no mesmo
    número.
    """

    por_ativo: dict[str, CurvaDeVariancia]
    origem: str = "desconhecida"
    #: O dia YYYYMMDD em que a curva foi MEDIDA, vindo do relatório.
    #:
    #: `None` quer dizer que o relatório não declarou — e um relatório que não
    #: declara não pode provar que é anterior ao avaliado. Nome de arquivo é
    #: convenção, não fato: `VARIANCIA_23AGO.json` pode conter qualquer coisa.
    dia_medido: str | None = None

    def para(self, asset: str) -> CurvaDeVariancia | None:
        return self.por_ativo.get(asset)

    def __len__(self) -> int:
        return len(self.por_ativo)


def curvas_do_relatorio(relatorio: dict[str, Any], *, origem: str) -> CurvasPorAtivo:
    """Lê a saída de `scripts/variancia_de_transicao.py`.

    Só entram horizontes com `suficiente: true`. Ativo cujo veredito não foi
    `avaliavel` fica de FORA — sem os dois regimes medidos a curva não cobre a
    faixa que o modelo consulta, e uma curva pela metade extrapolada para o
    resto seria pior que não ter curva, porque não se anunciaria.
    """
    curvas: dict[str, CurvaDeVariancia] = {}
    for asset, dados in (relatorio.get("por_ativo") or {}).items():
        if not (dados.get("veredito") or {}).get("avaliavel"):
            continue
        pontos = tuple(
            (float(linha["horizonte_s"]), float(linha["variancia"]))
            for linha in dados.get("horizontes", [])
            if linha.get("suficiente") and (linha.get("variancia") or 0) > 0
        )
        if len(pontos) >= 2:
            curvas[asset] = CurvaDeVariancia(asset=asset, pontos=pontos, origem=origem)
    dia = relatorio.get("dia_medido")
    return CurvasPorAtivo(
        por_ativo=curvas,
        origem=origem,
        dia_medido=dia if isinstance(dia, str) else None,
    )
