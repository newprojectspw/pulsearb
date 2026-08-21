"""Engenharia reversa da âncora por varredura de τ (M2.4).

O problema que este módulo resolve: as hipóteses NOMEADAS de âncora (último
tick antes da abertura, primeiro depois, mais próximo, interpolado) acertaram
79% na primeira amostra real — longe do acaso, longe de determinístico — e as
falhas são sistemáticas: as mesmas janelas erram em todas as hipóteses. Ou
seja, a âncora é *parecida* com "stream perto da abertura", mas não é nenhum
dos quatro palpites. Em vez de inventar o quinto palpite, inverte-se o
problema:

    cada resolução impõe uma DESIGUALDADE sobre a âncora A:
        resolveu Up   ⇒  TWAP_final ≥ A     (empate = Up, API_NOTES 12.4)
        resolveu Down ⇒  TWAP_final < A

    e a família de candidatas é A(τ) = valor do stream TWAP no instante
    `abertura + τ`. Varre-se τ; o τ verdadeiro satisfaz TODAS as
    desigualdades (menos o lixo residual de cobertura).

Duas definições de "TWAP_final" entram na varredura, porque a dúvida sobre o
final é tão real quanto a dúvida sobre a abertura (a falha decisiva da
primeira amostra tinha 30 pontos de folga e errou):

- **media_60s**: média dos pontos do stream nos últimos 60s da janela — a
  definição que o projeto vinha usando. Repare que o stream JÁ é um TWAP de
  60s: esta média é um TWAP de TWAP, com memória efetiva de ~2 minutos.
  **SUPERADA — a varredura respondeu.**
- **stream_no_fechamento**: o valor do stream em `fechamento + φ` — o que a
  Chainlink publica como TWAP dos últimos 60s, sem re-suavização nossa.
  φ = 0 na varredura fina; a grade conjunta (τ, φ) varre o resto.
  **VENCEDORA — é esta.**

## O RESULTADO (2026-08-21, 152 janelas de gravação real)

```
final_stream_no_fechamento -> consistência 1.0 em τ ∈ [-1, 0, 1, 2]
final_media_60s            -> teto de 0,9648, nenhum τ chega a 1.0
```

A âncora é o valor do stream **na abertura**; o final é o mesmo stream **no
fechamento**. A região de 4 segundos é a cadência do feed (~0,86s p50), ou
seja, a precisão máxima que o dado permite — não frouxidão do método.

**Corolário permanente, e é o achado mais caro do M2:** *não calcule média de
60s nenhuma*. O tópico `crypto_prices_twap_sixty` **já é** a média da
Chainlink, entregue pronta. Os 3,5% que faltavam à `final_media_60s` eram a
NOSSA conta errando — reamostragem, borda de janela e arredondamento por cima
de um número que já vinha certo.

As duas famílias continuam sendo varridas de propósito: a perdedora fica no
relatório como controle. Uma varredura que só testa a hipótese vencedora não
prova nada, e o dia em que a `media_60s` empatar com a outra é o dia de
desconfiar da gravação. Ver `docs/API_NOTES.md` §13.8.

As hipóteses NOMEADAS (`primeiro_depois`, `mais_proximo`, `interpolado`) estão
igualmente **superadas**: acertavam ~79% porque liam o stream na vizinhança
certa da abertura, e erravam no lado do FECHAMENTO — todas usavam o TWAP
recalculado como final. Seguem no relatório como referência histórica, sem uso
em decisão.

PRECISÃO É REGRA, não detalhe: uma das falhas reais tem gap de 0,019 em
~2096,78 — a 9ª casa decimal relativa, onde float64 já mistura arredondamento
com sinal. Toda comparação que decide Up/Down aqui é feita em INTEIROS na
escala 1e18 do Chainlink (`full_accuracy_value`), e a média é comparada por
multiplicação cruzada (`soma ≥ A·n`), sem nenhuma divisão. Float aparece só
na FORMATAÇÃO do relatório.

ALINHAMENTO: o eixo do tempo é o carimbo do SERVIDOR no payload (ms), nunca a
chegada local — a pergunta é sobre o relógio da plataforma. Abertura e
fechamento oficiais vêm do epoch do slug, que é o contrato do mercado.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from typing import Any

# Grade da varredura fina de τ (segundos em torno da abertura), no passo da
# cadência do stream (~1s). ±180s cobre com folga qualquer atraso plausível
# de publicação/consumo.
TAU_MIN_S = -180
TAU_MAX_S = 180
TAU_PASSO_S = 1
# Grade conjunta grossa (τ, φ): o lado do fechamento.
PHI_MIN_S = -60
PHI_MAX_S = 60
GRADE_PASSO_S = 5
# Um ponto do stream só vale como "o valor no instante t" se não estiver
# velho demais: a cadência medida é ~1s (p99 2,5s); 10s de idade é lacuna.
IDADE_MAX_MS = 10_000
# Janela do TWAP de resolução (API_NOTES §7).
TWAP_JANELA_MS = 60_000

E18 = 10**18


@dataclass(frozen=True, slots=True)
class JanelaResolvida:
    """O que a varredura precisa de cada janela: instantes e o resultado."""

    slug: str
    asset: str
    abertura_ms: int          # epoch ms, do slug (contrato do mercado)
    fechamento_ms: int
    resolveu_up: bool


class StreamE18:
    """Série (ts_servidor_ms, valor_e18) de um ativo, consultável por instante."""

    __slots__ = ("ts", "valores")

    def __init__(self, amostras: list[tuple[int, int]]) -> None:
        ordenadas = sorted(amostras)
        self.ts = [t for t, _ in ordenadas]
        self.valores = [v for _, v in ordenadas]

    def em(self, instante_ms: int, *, idade_max_ms: int = IDADE_MAX_MS) -> int | None:
        """Último valor com ts ≤ instante, se fresco o bastante. None = lacuna."""
        indice = bisect_right(self.ts, instante_ms)
        if indice == 0:
            return None
        if instante_ms - self.ts[indice - 1] > idade_max_ms:
            return None
        return self.valores[indice - 1]

    def soma_e_n(self, inicio_ms: int, fim_ms: int) -> tuple[int, int]:
        """Soma inteira e contagem dos pontos em (inicio, fim]."""
        a = bisect_right(self.ts, inicio_ms)
        b = bisect_right(self.ts, fim_ms)
        return sum(self.valores[a:b]), b - a

    def min_max(self, inicio_ms: int, fim_ms: int) -> tuple[int, int] | None:
        a = bisect_right(self.ts, inicio_ms)
        b = bisect_right(self.ts, fim_ms)
        if a >= b:
            return None
        recorte = self.valores[a:b]
        return min(recorte), max(recorte)

    def cobre(self, inicio_ms: int, fim_ms: int) -> bool:
        """Há amostra até `inicio` (ou antes) e amostra até `fim`?

        É o teste de elegibilidade da janela: sem stream dos dois lados do
        intervalo varrido, um τ pode falhar por lacuna e não por estar errado
        — e a varredura contaria a lacuna como evidência.
        """
        return bool(self.ts) and self.ts[0] <= inicio_ms and self.ts[-1] >= fim_ms


def _consistente(
    resolveu_up: bool, soma_final: int, n_final: int, ancora: int
) -> tuple[bool, bool]:
    """(a desigualdade vale?, foi empate exato?) — tudo em inteiros.

    `soma_final ≥ ancora·n_final` ⇔ média ≥ âncora, sem dividir nunca.
    Empate exato resolve Up (API_NOTES 12.4), e é CONTADO: empates frequentes
    seriam sinal de âncora quantizada, o que por si é informação.
    """
    lado_up = soma_final >= ancora * n_final
    empate = soma_final == ancora * n_final
    return (lado_up == resolveu_up, empate)


def varrer(
    janelas: list[JanelaResolvida],
    streams: dict[str, list[tuple[int, int]]],
    *,
    tau_min_s: int = TAU_MIN_S,
    tau_max_s: int = TAU_MAX_S,
    passo_s: int = TAU_PASSO_S,
) -> dict[str, Any]:
    """A varredura completa do M2.4. Devolve o bloco do relatório."""
    series = {asset: StreamE18(amostras) for asset, amostras in streams.items()}

    elegiveis: list[tuple[JanelaResolvida, StreamE18, int, int, int | None]] = []
    sem_cobertura = 0
    for janela in janelas:
        stream = series.get(janela.asset)
        if stream is None or not stream.cobre(
            janela.abertura_ms + tau_min_s * 1000,
            janela.fechamento_ms + PHI_MAX_S * 1000,
        ):
            sem_cobertura += 1
            continue
        soma, n = stream.soma_e_n(
            janela.fechamento_ms - TWAP_JANELA_MS, janela.fechamento_ms
        )
        if n == 0:
            sem_cobertura += 1
            continue
        final_stream = stream.em(janela.fechamento_ms)
        elegiveis.append((janela, stream, soma, n, final_stream))

    taus = list(range(tau_min_s, tau_max_s + 1, passo_s))
    resultado_media = _varredura_fina(
        elegiveis, taus, usar_media=True
    )
    resultado_stream = _varredura_fina(
        elegiveis, taus, usar_media=False
    )
    grade = _grade_tau_phi(elegiveis)
    falhas = _diagnostico_de_falhas(elegiveis, tau_min_s, tau_max_s)

    return {
        "janelas_recebidas": len(janelas),
        "janelas_elegiveis": len(elegiveis),
        "janelas_sem_cobertura_do_stream": sem_cobertura,
        "grade": {
            "tau_s": [tau_min_s, tau_max_s, passo_s],
            "phi_s": [PHI_MIN_S, PHI_MAX_S, GRADE_PASSO_S],
            "idade_max_do_ponto_ms": IDADE_MAX_MS,
        },
        # As duas definições de final lado a lado. RESPONDIDO em 2026-08-21:
        # `stream_no_fechamento` deu 1.0 sobre 152 janelas e `media_60s` não
        # passou de 0,9648 — a média-de-TWAP que o projeto usava ERA parte do
        # erro. A perdedora continua sendo calculada como controle: se um dia
        # as duas empatarem, o suspeito é a gravação.
        "final_media_60s": resultado_media,
        "final_stream_no_fechamento": resultado_stream,
        "grade_tau_phi": grade,
        "falhas_inexplicaveis": falhas,
        "nota": (
            "Comparações em inteiros na escala 1e18 do Chainlink; média por "
            "multiplicação cruzada, sem divisão; eixo do tempo = carimbo do "
            "servidor; abertura/fechamento = epoch do slug. Critérios de "
            "sucesso/falha registrados em VEREDITO_M2.md ANTES desta "
            "varredura existir."
        ),
    }


def _varredura_fina(
    elegiveis: list[tuple[JanelaResolvida, StreamE18, int, int, int | None]],
    taus: list[int],
    *,
    usar_media: bool,
) -> dict[str, Any]:
    curva: dict[str, float] = {}
    detalhes: list[tuple[int, int, int, int]] = []  # (τ, ok, avaliadas, empates)
    for tau in taus:
        ok = avaliadas = empates = 0
        for janela, stream, soma, n, final_stream in elegiveis:
            ancora = stream.em(janela.abertura_ms + tau * 1000)
            if ancora is None:
                continue
            if usar_media:
                consistente, empate = _consistente(
                    janela.resolveu_up, soma, n, ancora
                )
            else:
                if final_stream is None:
                    continue
                consistente, empate = _consistente(
                    janela.resolveu_up, final_stream, 1, ancora
                )
            avaliadas += 1
            ok += consistente
            empates += empate
        taxa = ok / avaliadas if avaliadas else 0.0
        curva[str(tau)] = round(taxa, 4)
        detalhes.append((tau, ok, avaliadas, empates))

    perfeitos = [
        tau for tau, ok, avaliadas, _ in detalhes if avaliadas and ok == avaliadas
    ]
    melhores = sorted(
        detalhes, key=lambda item: (item[1] / item[2] if item[2] else 0.0, -abs(item[0])),
        reverse=True,
    )[:5]
    return {
        "curva": curva,
        "regiao_viavel_100pct": _como_intervalos(perfeitos),
        "melhores_tau": [
            {
                "tau_s": tau,
                "consistencia": round(ok / avaliadas, 4) if avaliadas else 0.0,
                "consistentes": ok,
                "avaliadas": avaliadas,
                "empates_exatos": empates,
            }
            for tau, ok, avaliadas, empates in melhores
        ],
    }


def _grade_tau_phi(
    elegiveis: list[tuple[JanelaResolvida, StreamE18, int, int, int | None]],
) -> dict[str, Any]:
    """Grade grossa (τ, φ): âncora em abertura+τ, final = stream em fechamento+φ.

    Roda sempre — é barata — mas o seu papel é o item 4 do M2.4: se nenhum τ
    da varredura fina passar de ~95%, a resposta pode estar no LADO DO FINAL,
    e é aqui que ela aparece.
    """
    melhor: dict[str, Any] | None = None
    top: list[dict[str, Any]] = []
    for tau in range(TAU_MIN_S, TAU_MAX_S + 1, GRADE_PASSO_S):
        for phi in range(PHI_MIN_S, PHI_MAX_S + 1, GRADE_PASSO_S):
            ok = avaliadas = 0
            for janela, stream, _soma, _n, _final in elegiveis:
                ancora = stream.em(janela.abertura_ms + tau * 1000)
                final = stream.em(janela.fechamento_ms + phi * 1000)
                if ancora is None or final is None:
                    continue
                consistente, _ = _consistente(janela.resolveu_up, final, 1, ancora)
                avaliadas += 1
                ok += consistente
            if not avaliadas:
                continue
            celula = {
                "tau_s": tau,
                "phi_s": phi,
                "consistencia": round(ok / avaliadas, 4),
                "avaliadas": avaliadas,
            }
            top.append(celula)
            if melhor is None or celula["consistencia"] > melhor["consistencia"]:
                melhor = celula
    top.sort(key=lambda c: c["consistencia"], reverse=True)
    return {"melhor_celula": melhor, "top": top[:10]}


def _diagnostico_de_falhas(
    elegiveis: list[tuple[JanelaResolvida, StreamE18, int, int, int | None]],
    tau_min_s: int,
    tau_max_s: int,
) -> list[dict[str, Any]]:
    """As janelas que NENHUMA candidata explica — o teste da fundação.

    Se para uma janela nem o mínimo nem o máximo do stream em
    [abertura−180s, abertura+180s] satisfaz a desigualdade, então NENHUM
    ponto do nosso stream pode ser a âncora daquela janela — a liquidação
    usou uma fonte fora do que gravamos, e o veredito precisa dizer isso com
    todas as letras (critério de falha da fundação, VEREDITO_M2.md).
    """
    falhas: list[dict[str, Any]] = []
    for janela, stream, soma, n, final_stream in elegiveis:
        extremos = stream.min_max(
            janela.abertura_ms + tau_min_s * 1000,
            janela.abertura_ms + tau_max_s * 1000,
        )
        if extremos is None:
            continue
        minimo, maximo = extremos
        # Up exige final ≥ A ⇒ existe A viável sse min ≤ média_final.
        # Down exige final < A ⇒ existe A viável sse máx > média_final.
        # Avaliado nas DUAS definições de final: só é inexplicável se falhar
        # em ambas.
        explicavel = False
        for soma_f, n_f in ((soma, n), ((final_stream or 0), 1 if final_stream else 0)):
            if n_f == 0:
                continue
            if janela.resolveu_up:
                explicavel |= minimo * n_f <= soma_f
            else:
                explicavel |= maximo * n_f > soma_f
        if explicavel:
            continue
        falhas.append(
            {
                "slug": janela.slug,
                "resolveu": "Up" if janela.resolveu_up else "Down",
                "stream_min": _e18_str(minimo),
                "stream_max": _e18_str(maximo),
                "final_media_60s": _e18_str(soma // n) if n else None,
                "final_stream_no_fechamento": (
                    _e18_str(final_stream) if final_stream is not None else None
                ),
                "leitura": (
                    "nenhum ponto do stream em [abertura-180s, abertura+180s] "
                    "poderia ser a âncora desta janela — a liquidação usou "
                    "fonte fora do nosso stream"
                ),
            }
        )
    return falhas


def _como_intervalos(taus: list[int]) -> list[list[int]]:
    """[−3,−2,−1,4,5] → [[−3,−1],[4,5]] — a região viável legível."""
    if not taus:
        return []
    ordenados = sorted(taus)
    saida = [[ordenados[0], ordenados[0]]]
    for tau in ordenados[1:]:
        if tau == saida[-1][1] + 1:
            saida[-1][1] = tau
        else:
            saida.append([tau, tau])
    return saida


def _e18_str(valor: int) -> str:
    """Inteiro e18 → decimal exato como string. Float só na formatação."""
    sinal = "-" if valor < 0 else ""
    inteiro, fracao = divmod(abs(valor), E18)
    return f"{sinal}{inteiro}.{fracao:018d}"
