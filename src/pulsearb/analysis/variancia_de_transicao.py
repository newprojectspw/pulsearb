"""V(t) — a variância de transição do `twap_sixty`, MEDIDA em vez de derivada.

O `prob_up_twap` derivava a variância do valor de liquidação supondo que ele
é a média de 60 amostras de um preço bruto. Duas coisas estavam erradas nessa
suposição, e as duas custaram um veredito:

1. o termo do tempo ANTES de a janela de 60 s começar faltava (§2d-ter);
2. o observável é outro — a §13.8 do `API_NOTES.md` VERIFICOU que a janela
   resolve por **um ponto** do stream `twap_sixty` no fechamento, sem média
   nenhuma, e é esse mesmo stream já suavizado que alimenta o `sigma_1s`.

Este módulo não tenta derivar a variância certa. Ele a **mede**, que é a mesma
metodologia com que a §13.8 achou a âncora: engenharia reversa sobre o dado
gravado. Se a série se comporta como caminhada aleatória, a medição vai dizer
isso; se a suavização deixa marca, a medição mostra a marca.

## O que é medido

    V(t) = Var( T_{s+t}/T_s − 1 )

sobre todos os pares da série gravada separados por `t` segundos. Retorno
RELATIVO, e não em dólares, porque é assim que o modelo usa σ: ele multiplica
por `spot`. Assim `V(t)` entra direto no lugar de `sigma_1s² · fator(t)`.

## As três colunas que decidem

- `variancia_por_segundo` = V(t)/t. Numa caminhada aleatória pura é
  **constante**. Se subir com `t`, a série é suavizada — e o quanto ela sobe
  é o tamanho da suavização.
- `razao_contra_o_modelo` = V(t) / (V(1) · fator_do_modelo(t)). É o fator pelo
  qual o modelo erra a variância no horizonte `t`. Um significa acertar.
- `n_independentes` — a contagem honesta. As janelas se sobrepõem, então `n`
  infla a confiança; `span/t` é quantas observações realmente independentes
  existem naquele horizonte.

## Uma escolha registrada: janelas SOBREPOSTAS

O ponto estimado usa todos os pares separados por `t`, inclusive sobrepostos.
Isso não enviesa a variância — a esperança é a mesma —, só correlaciona as
observações, o que afeta o ERRO da estimativa e não o valor. Por isso o
relatório publica `n_independentes` ao lado de `n`: quem for pôr barra de erro
usa o segundo, e quem for ler a magnitude usa o valor.

## Lacuna de feed não invalida o par

Um par cujos extremos estão a `t` segundos um do outro é uma observação válida
do movimento de `t` segundos mesmo que o feed tenha emudecido no meio: o preço
andou, nós é que não vimos o caminho. O que invalidaria seria aceitar um par
que NÃO está a `t` segundos de distância — e é isso que `tolerancia_s` recusa.
"""

from __future__ import annotations

import bisect
from collections.abc import Sequence
from itertools import pairwise
from typing import Any

from pulsearb.engine.twap import TWAP_WINDOW_SECONDS_DEFAULT, variance_factor

#: Horizontes medidos. Cobrem os cinco baldes de `bucket_tempo` e o 1 s que
#: serve de referência, porque `sigma_1s` é o que o modelo usa como unidade.
HORIZONTES_PADRAO: tuple[float, ...] = (1, 2, 5, 10, 30, 60, 120, 180, 240, 300, 600)

#: Quanto um par pode se afastar do horizonte pedido e ainda contar. A cadência
#: medida do RTDS é ~0,86 s (API_NOTES §13.1), então 1 s admite o vizinho mais
#: próximo sem admitir o horizonte errado.
TOLERANCIA_PADRAO_S = 1.0

#: Abaixo disto o horizonte não é reportado como medido. Não é limiar de
#: veredito — é o piso para a variância amostral significar alguma coisa.
MINIMO_DE_PARES = 200


def fator_do_modelo(
    seconds_left: float, janela_s: float = TWAP_WINDOW_SECONDS_DEFAULT
) -> float:
    """O fator que o `prob_up_twap` aplica hoje, para comparação.

    Reproduzido aqui de propósito a partir das MESMAS peças que o modelo usa
    (`variance_factor` e a janela), para que a comparação não dependa de uma
    segunda cópia da fórmula.
    """
    return max(0.0, seconds_left - janela_s) + variance_factor(
        min(seconds_left, janela_s)
    )


def _pares_no_horizonte(
    ts_ns: Sequence[int],
    valores: Sequence[float],
    horizonte_s: float,
    tolerancia_s: float,
) -> list[float]:
    """Retornos relativos de todos os pares separados por `horizonte_s`."""
    alvo_ns = int(horizonte_s * 1e9)
    # A tolerância nunca passa de meio horizonte. Sem esse teto, um alvo de 2 s
    # com tolerância de 1 s aceitaria o vizinho de 1 s e mediria o horizonte
    # errado — o instrumento reprovou nisso na primeira versão, contra a
    # caminhada de resposta conhecida.
    tol_ns = int(min(tolerancia_s, horizonte_s / 2.0) * 1e9)
    retornos: list[float] = []
    for i, t0 in enumerate(ts_ns):
        j = bisect.bisect_left(ts_ns, t0 + alvo_ns, lo=i + 1)
        # O mais próximo do alvo é j ou j−1. Escolhe pelo MENOR erro, não pelo
        # primeiro que couber na tolerância.
        melhor: int | None = None
        menor_erro = tol_ns + 1
        for cand in (j - 1, j):
            if cand <= i or cand >= len(ts_ns):
                continue
            erro = abs(ts_ns[cand] - t0 - alvo_ns)
            if erro <= tol_ns and erro < menor_erro:
                melhor, menor_erro = cand, erro
        if melhor is not None and valores[i] > 0:
            retornos.append(valores[melhor] / valores[i] - 1.0)
    return retornos


def curva_de_variancia(
    serie: Sequence[tuple[int, float]],
    *,
    horizontes_s: Sequence[float] = HORIZONTES_PADRAO,
    tolerancia_s: float = TOLERANCIA_PADRAO_S,
    minimo_de_pares: int = MINIMO_DE_PARES,
) -> dict[str, Any]:
    """Mede V(t) sobre uma série `(ts_ns, valor)` ordenada no tempo."""
    ordenada = sorted(serie)
    ts_ns = [t for t, _ in ordenada]
    valores = [v for _, v in ordenada]
    span_s = (ts_ns[-1] - ts_ns[0]) / 1e9 if len(ts_ns) > 1 else 0.0

    referencia: float | None = None
    linhas: list[dict[str, Any]] = []
    for h in horizontes_s:
        retornos = _pares_no_horizonte(ts_ns, valores, h, tolerancia_s)
        n = len(retornos)
        if n < minimo_de_pares:
            linhas.append(
                {"horizonte_s": h, "n": n, "suficiente": False, "variancia": None}
            )
            continue
        media = sum(retornos) / n
        variancia = sum((r - media) ** 2 for r in retornos) / (n - 1)
        if referencia is None and variancia > 0 and h > 0:
            # `RealizedVol` estima sigma_1s como `retorno²/dt` na cadência do
            # tick, então a referência honesta é V(h)/h no MENOR horizonte que
            # tem amostra — o mesmo estimador, não uma idealização de 1 s que
            # a cadência de ~0,86 s do RTDS pode nem oferecer.
            referencia = variancia / h
        linha: dict[str, Any] = {
            "horizonte_s": h,
            "n": n,
            "n_independentes": int(span_s // h) if h > 0 else 0,
            "suficiente": True,
            "media": media,
            "variancia": variancia,
            "variancia_por_segundo": variancia / h if h > 0 else None,
            "razao_contra_o_modelo": None,
        }
        if referencia:
            esperado = referencia * fator_do_modelo(h)
            linha["razao_contra_o_modelo"] = variancia / esperado if esperado > 0 else None
        linhas.append(linha)

    return {
        "amostras": len(ts_ns),
        "span_s": round(span_s, 1),
        "variancia_de_1s": referencia,
        "horizonte_da_referencia_s": next(
            (x["horizonte_s"] for x in linhas if x.get("suficiente")), None
        ),
        "horizontes": linhas,
        "tolerancia_s": tolerancia_s,
    }


def veredito_da_curva(curva: dict[str, Any]) -> dict[str, Any]:
    """As três propriedades da §2d-ter, julgadas em código.

    Escritas ANTES da medição, no protocolo. Aqui elas viram teste, para que a
    leitura não dependa de olhar a tabela com boa vontade.
    """
    medidos = [linha for linha in curva["horizontes"] if linha.get("suficiente")]
    variancias = [(linha["horizonte_s"], linha["variancia"]) for linha in medidos]

    monotona = all(b >= a for (_, a), (_, b) in pairwise(variancias))

    # Sublinear no curto prazo: V(t)/t deve CRESCER com t se há suavização.
    por_segundo = [
        (linha["horizonte_s"], linha["variancia_por_segundo"]) for linha in medidos
    ]
    curtos = [v for h, v in por_segundo if h <= 60]
    longos = [v for h, v in por_segundo if h >= 240]
    suavizacao = (
        max(longos) / min(curtos) if curtos and longos and min(curtos) > 0 else None
    )

    return {
        "monotona": monotona,
        "fator_de_suavizacao_medido": suavizacao,
        "ha_suavizacao": bool(suavizacao and suavizacao > 1.5),
        "horizontes_medidos": len(medidos),
    }
