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

# ─── M2.11: tolerância relativa, e por que ela não é afrouxamento ─────────
#
# O gate da região de 100% é binário: uma janela discordante, de qualquer
# magnitude, apaga um τ. No bloco de 21/08 a única discordante de τ=0 errou
# por 2,06e-6 — 0,16 USD num BTC de 78.640, com âncora fresca (idade 0 ms).
#
# Isso é o mesmo erro do M2.2, onde um gate binário de 0,01 reprovou 200 de
# 200 janelas por medir CORRIDA e não CORRUPÇÃO. Folga infinitesimal não é
# evidência contra a âncora nem a favor: é ausência de evidência, e o lugar
# dela é FORA do denominador — não somada aos acertos, que inflaria a
# consistência, nem aos erros, que a derrubaria.
#
# O limiar está justificado em VEREDITO_M2 §2b-bis, escrito ANTES de rodar.
# Em resumo: 1e-5 fica 5x acima do ruído observado (2,06e-6) e 5x abaixo do
# que UM intervalo de amostragem do feed produz (~5,4e-5 — o TWAP-60 do btc
# anda ~4 USD/s e o p50 do intervalo é 1,061 s). Abaixo desse teto, "âncora
# errada" é indistinguível de "amostramos o tick vizinho".
#
# NÃO substitui o `LIMIAR_CONSISTENCIA` de 98%: aquele é agregado e cobre
# lacuna de stream e empate mal-carimbado; este é por janela e cobre só
# magnitude. Um filtra por quantidade, o outro por tamanho.
#
# Numerador e denominador SEPARADOS porque a comparação é feita por
# multiplicação cruzada, em inteiros. `folga/escala < 1/100000` vira
# `folga * 100000 < escala`, sem divisão em ponto flutuante em lugar nenhum
# — a mesma regra que vale para o resto do módulo.
FOLGA_RELATIVA_NUM = 1
FOLGA_RELATIVA_DEN = 100_000
#: Resolução do histograma de folgas. Partes por bilhão dá 3 décadas abaixo
#: do limiar, que é onde a massa deve estar se ele estiver no lugar certo.
PPB = 1_000_000_000


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
) -> tuple[bool, bool, bool]:
    """(a desigualdade vale?, empate exato?, indeterminada?) — em inteiros.

    `soma_final ≥ ancora·n_final` ⇔ média ≥ âncora, sem dividir nunca.
    Empate exato resolve Up (API_NOTES 12.4), e é CONTADO: empates frequentes
    seriam sinal de âncora quantizada, o que por si é informação.

    INDETERMINADA (M2.11) é a folga relativa abaixo de `FOLGA_RELATIVA_NUM /
    FOLGA_RELATIVA_DEN`: a diferença existe, mas é pequena demais para
    distinguir âncora errada de arredondamento entre a nossa série e a que
    liquida. Quem chama tira essas janelas do denominador.

    **Empate exato NÃO é indeterminado, e a exceção é deliberada.** Quando os
    dois valores são idênticos não há arredondamento a discutir: a regra
    documentada manda resolver Up, e a resolução observada ou confirma essa
    regra ou a desmente. Marcar empate como indeterminado engoliria
    justamente a evidência sobre o desempate, que `empates_exatos` existe
    para expor.
    """
    escala = ancora * n_final
    diferenca = soma_final - escala
    lado_up = diferenca >= 0
    empate = diferenca == 0
    indeterminada = (
        escala > 0
        and not empate
        and abs(diferenca) * FOLGA_RELATIVA_DEN < escala * FOLGA_RELATIVA_NUM
    )
    return (lado_up == resolveu_up, empate, indeterminada)


def _folga_relativa_ppb(soma_final: int, n_final: int, ancora: int) -> int | None:
    """Folga relativa em partes por bilhão, por divisão INTEIRA.

    `None` quando a escala não é positiva — sem escala não há folga relativa,
    e devolver zero ali diria "coladíssimo" onde a verdade é "não dá para
    dizer".
    """
    escala = ancora * n_final
    if escala <= 0:
        return None
    return abs(soma_final - escala) * PPB // escala


def _distribuicao_no_span(
    elegiveis_ms: list[int], todas_ms: list[int], *, quartis: int = 4
) -> dict[str, Any]:
    """Onde, ao longo da gravação, caíram as janelas elegíveis.

    M2.10 item 6. É o que separa **amostra pequena** de **amostra
    enviesada**, e os dois pedem consertos diferentes: pequena se resolve
    gravando mais, enviesada não.

    Na gravação de 2026-08-22 as 8 elegíveis estavam todas na primeira
    metade — porque o stream morreu na metade — e isso só apareceu porque
    alguém cruzou os slugs na mão. Concentração num pedaço do span quer
    dizer que o resto da gravação não contribuiu, e aí "8 janelas" não é uma
    amostra de 8 momentos do mercado.
    """
    if not todas_ms:
        return {"quartis": {}, "nota": "sem janelas"}
    inicio, fim = min(todas_ms), max(todas_ms)
    span = fim - inicio
    contagem = {f"q{i + 1}": 0 for i in range(quartis)}
    for ts in elegiveis_ms:
        indice = 0 if span <= 0 else min(int((ts - inicio) * quartis / span), quartis - 1)
        contagem[f"q{indice + 1}"] += 1
    ocupados = sum(1 for v in contagem.values() if v)
    return {
        "quartis": contagem,
        "quartis_com_janela": ocupados,
        "span_das_janelas_s": round(span / 1000.0, 1),
        "concentrada": bool(elegiveis_ms) and ocupados <= max(1, quartis // 2),
        "nota": (
            "Quartis do span coberto pelas janelas conhecidas. "
            "`concentrada` true = as elegiveis vieram de metade ou menos da "
            "gravacao, entao a amostra e ENVIESADA no tempo e nao apenas "
            "pequena — gravar mais do mesmo jeito nao conserta. Na gravacao "
            "de 2026-08-22 as 8 elegiveis estavam todas na primeira metade, "
            "porque o stream da ancora morreu aos 30 min."
        ),
    }


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
        # M2.10 item 6: amostra pequena e amostra ENVIESADA pedem consertos
        # diferentes, e so a distribuicao no tempo separa as duas. Na
        # gravacao de 2026-08-22 as 8 elegiveis estavam todas na primeira
        # metade — o stream morreu na metade — e isso so apareceu porque
        # alguem cruzou os slugs na mao.
        "distribuicao_das_elegiveis": _distribuicao_no_span(
            [j.abertura_ms for j, *_ in elegiveis],
            [j.abertura_ms for j in janelas],
        ),
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
        # M2.12: as janelas que a ANCORA VERIFICADA (tau=0) nao explica,
        # com o numero de cada uma. VEREDITO_M2 2b manda investigar as
        # falhas uma a uma; sem isto so havia a razao agregada.
        "discordantes_em_tau_verificado": _discordantes_em_tau(
            elegiveis, TAU_VERIFICADO_S
        ),
        # M2.11 item 4: o limiar de folga relativa so pode ser revisto com
        # numero se a distribuicao das folgas estiver no relatorio.
        "distribuicao_das_folgas_relativas": _distribuicao_das_folgas(
            elegiveis, TAU_VERIFICADO_S
        ),
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
    # (τ, ok, avaliadas, empates, indeterminadas)
    detalhes: list[tuple[int, int, int, int, int]] = []
    for tau in taus:
        ok = avaliadas = empates = indeterminadas = 0
        for janela, stream, soma, n, final_stream in elegiveis:
            ancora = stream.em(janela.abertura_ms + tau * 1000)
            if ancora is None:
                continue
            if usar_media:
                consistente, empate, indeterminada = _consistente(
                    janela.resolveu_up, soma, n, ancora
                )
            else:
                if final_stream is None:
                    continue
                consistente, empate, indeterminada = _consistente(
                    janela.resolveu_up, final_stream, 1, ancora
                )
            # M2.11: fora do denominador, não somada a nenhum dos dois lados.
            if indeterminada:
                indeterminadas += 1
                continue
            avaliadas += 1
            ok += consistente
            empates += empate
        taxa = ok / avaliadas if avaliadas else 0.0
        curva[str(tau)] = round(taxa, 4)
        detalhes.append((tau, ok, avaliadas, empates, indeterminadas))

    # A região de 100% ignora indeterminadas de graça: elas nunca entraram em
    # `avaliadas`. É o item 3 do M2.11, e sai da contabilidade em vez de uma
    # segunda regra que poderia divergir dela.
    perfeitos = [
        tau for tau, ok, avaliadas, _, _ in detalhes if avaliadas and ok == avaliadas
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
                "indeterminadas": indeterminadas,
            }
            for tau, ok, avaliadas, empates, indeterminadas in melhores
        ],
        "indeterminadas_em_tau": {
            str(tau): indeterminadas
            for tau, _ok, _av, _emp, indeterminadas in detalhes
            if indeterminadas
        },
        # `curva` devolve 0.0 quando nada foi avaliado, e 0.0 ali significa
        # "sem evidencia", nao "nao explica nenhuma". Quem precisa separar as
        # duas leituras — o veredito — usa esta contagem.
        "avaliadas_em_tau": {
            str(tau): avaliadas for tau, _ok, avaliadas, _emp, _ind in detalhes
        },
        "nota_indeterminadas": (
            "M2.11. Janela com folga relativa abaixo de "
            f"{FOLGA_RELATIVA_NUM}/{FOLGA_RELATIVA_DEN} nao entra em "
            "`avaliadas` nem em `consistentes`: a diferenca existe mas e "
            "pequena demais para distinguir ancora errada de arredondamento. "
            "Ausencia de evidencia nao e evidencia de nenhum dos dois lados. "
            "Empate EXATO nao conta como indeterminado — ali a regra de "
            "desempate esta sendo testada de verdade. Ver VEREDITO_M2 2b-bis."
        ),
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
                consistente, _, indeterminada = _consistente(
                    janela.resolveu_up, final, 1, ancora
                )
                if indeterminada:
                    continue
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


def _discordantes_em_tau(
    elegiveis: list[tuple[JanelaResolvida, StreamE18, int, int, int | None]],
    tau: int,
    *,
    limite: int = 20,
) -> list[dict[str, Any]]:
    """As janelas que τ NÃO explica, uma a uma, com o número de cada.

    `VEREDITO_M2` §2b prescreve, para o caso de consistência alta mas abaixo
    de 100%: *"investigar as falhas UMA A UMA antes de subir N"*. Até aqui
    isso não era possível — o relatório dizia `0.9934` e não dizia QUAL
    janela discordou, então a única leitura disponível era o alarme binário.

    A distinção que estes campos permitem, e que a razão sozinha esconde:

    - **`folga_e18` minúscula** perto do limiar: empate na prática, e a
      resolução decidiu para um lado que o arredondamento nosso não
      reproduz. É o "empate mal-carimbado" que §2b orçou.
    - **`idade_da_ancora_ms` alta**: o ponto do stream usado como âncora já
      estava velho — lacuna fina demais para o detector de cobertura pegar,
      o outro caso que §2b orçou.
    - **`folga_e18` grande com âncora fresca**: aí não é lixo. É a âncora
      errada, ou a regra mudou.

    Nenhum destes campos afrouxa o alarme. Eles existem para que a pessoa
    que lê possa fazer o que o documento manda, em vez de escolher entre
    ignorar o alerta e jogar fora a gravação.
    """
    saida: list[dict[str, Any]] = []
    for janela, stream, _soma, _n, final_stream in elegiveis:
        instante = janela.abertura_ms + tau * 1000
        ancora = stream.em(instante)
        if ancora is None or final_stream is None:
            continue
        consistente, empate, indeterminada = _consistente(
            janela.resolveu_up, final_stream, 1, ancora
        )
        # M2.11: indeterminada nao e discordante. Lista-la aqui devolveria
        # pela porta dos fundos o alarme que a tolerancia tirou pela frente.
        if consistente or indeterminada:
            continue
        indice = bisect_right(stream.ts, instante)
        idade = instante - stream.ts[indice - 1] if indice else None
        saida.append(
            {
                "slug": janela.slug,
                "asset": janela.asset,
                "resolveu_up": janela.resolveu_up,
                "ancora_e18": ancora,
                "final_e18": final_stream,
                # Distância até virar o lado. Em e18: 10**18 = 1 unidade do
                # preço, então folga de 10**12 é a 6ª casa decimal.
                "folga_e18": abs(final_stream - ancora),
                # A folga que decide se isto e lixo ou achado e a RELATIVA:
                # 0,16 USD e enorme num token de 1 USD e desprezivel num BTC
                # de 78 mil. Sem ela, quem le compara grandezas incomparaveis.
                "folga_relativa_ppb": _folga_relativa_ppb(final_stream, 1, ancora),
                "empate_exato": empate,
                "idade_da_ancora_ms": idade,
            }
        )
        if len(saida) >= limite:
            break
    return saida


#: Décadas do histograma, em ppb. O limiar (1e-5) é 10.000 ppb, então ele cai
#: numa BORDA de balde e não no meio de um: assim dá para ler "quantas ficaram
#: abaixo" sem interpolar nada.
_DECADAS_PPB = (1, 10, 100, 1_000, 10_000, 100_000, 1_000_000)
_ROTULOS_PPB = (
    "exato (0)",
    "1e-9..1e-8",
    "1e-8..1e-7",
    "1e-7..1e-6",
    "1e-6..1e-5",
    "1e-5..1e-4",
    "1e-4..1e-3",
    ">=1e-3",
)


def _percentil(ordenados: list[int], fracao: float) -> int | None:
    if not ordenados:
        return None
    indice = min(int(fracao * len(ordenados)), len(ordenados) - 1)
    return ordenados[indice]


def _distribuicao_das_folgas(
    elegiveis: list[tuple[JanelaResolvida, StreamE18, int, int, int | None]],
    tau: int,
) -> dict[str, Any]:
    """Histograma das folgas relativas de TODAS as janelas, não só das falhas.

    M2.11 item 4, e é o que permite rever o limiar com número em vez de
    opinião. Olhar só as discordantes responde "as que falharam eram
    pequenas?"; a pergunta que decide o limiar é outra — *onde está a massa*.
    Se ela se acumular junto da borda de 1e-5, o limiar está no lugar errado,
    e nenhuma quantidade de argumento sobre as falhas mostraria isso.
    """
    folgas: list[int] = []
    for janela, stream, _soma, _n, final_stream in elegiveis:
        ancora = stream.em(janela.abertura_ms + tau * 1000)
        if ancora is None or final_stream is None:
            continue
        ppb = _folga_relativa_ppb(final_stream, 1, ancora)
        if ppb is not None:
            folgas.append(ppb)

    histograma = dict.fromkeys(_ROTULOS_PPB, 0)
    for ppb in folgas:
        indice = sum(1 for limite in _DECADAS_PPB if ppb >= limite)
        histograma[_ROTULOS_PPB[indice]] += 1
    ordenados = sorted(folgas)
    limiar_ppb = PPB * FOLGA_RELATIVA_NUM // FOLGA_RELATIVA_DEN
    return {
        "tau_s": tau,
        "janelas_com_folga": len(folgas),
        "histograma_ppb": histograma,
        "p50_ppb": _percentil(ordenados, 0.50),
        "p90_ppb": _percentil(ordenados, 0.90),
        "max_ppb": ordenados[-1] if ordenados else None,
        "limiar_ppb": limiar_ppb,
        "abaixo_do_limiar": sum(1 for ppb in folgas if ppb < limiar_ppb),
        "nota": (
            "M2.11 item 4. Folga relativa = |final - ancora| / ancora, em "
            "partes por bilhao, por divisao INTEIRA. Cobre todas as janelas "
            "avaliadas, nao so as discordantes: o que decide se o limiar esta "
            "no lugar certo e ONDE A MASSA ESTA, e massa encostada na borda "
            "de 1e-5 e sinal de limiar mal escolhido. Ver VEREDITO_M2 2b-bis."
        ),
    }


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


# ─────────────────────────────── M2.6: a âncora deixou de ser hipótese

#: Abaixo disto, a varredura não tem amostra para afirmar nem para desmentir.
#: Não é um número mágico: com poucas janelas resolvidas, um τ errado pode
#: acertar 100% por sorte, e o silêncio da varredura seria lido como alarme.
MINIMO_JANELAS_VEREDITO = 20

#: Consistência mínima para a âncora seguir CONFIRMADA. Vem de VEREDITO_M2
#: §2b, escrito ANTES de qualquer varredura rodar:
#:
#:   "98%, não 100%: a amostra real carrega janelas com lacuna de stream fina
#:    demais para o nosso detector de cobertura pegar (reconexões de segundos)
#:    e possíveis empates mal-carimbados. Exigir 100% deixaria uma única
#:    janela suja vetar a âncora certa. 2 falhas em 100 é o orçamento para
#:    esse lixo residual."
#:
#: O alarme do M2.6 nasceu exigindo 1.0 e ignorou esse orçamento. A decisão
#: de alinhar o código ao documento foi tomada em 2026-08-23, com medição:
#:
#:   152 janelas elegíveis sobre 5h limpas, tau=0 em 0,9934 — UMA discordante,
#:   `btc-updown-5m-1787354400`, errando por 0,162 USD em 78.640 USD:
#:
#:     2,06 ppm de folga relativa
#:     40 ms de movimento do TWAP-60 do btc (que anda ~4 USD/s)
#:     3,75% de UM intervalo de amostragem do feed (1,061 s)
#:     97x mais apertada que o limiar de "janela apertada" do próprio
#:       projeto (2 bps, engine/anchor.py)
#:
#: Nenhum carimbo desta gravação distingue esses dois valores. E tau=0
#: continuou sendo o argmax: se a âncora tivesse se deslocado, outro tau
#: ganharia — nenhum ganhou.
#:
#: Há ainda uma propriedade que condena o 1.0 como regra: exigir consistência
#: perfeita torna o alarme MAIS provável quanto MAIOR a amostra. Com 24
#: janelas o 100% sai fácil; com 152, uma janela na navalha é esperada. Um
#: detector de regressão que dispara mais com dado melhor está invertido.
#:
#: O alarme não foi desligado. Mudança de regra DERRUBA a consistência — as
#: hipóteses nomeadas erradas marcavam ~79% — e é essa ordem de grandeza que
#: ele continua pegando.
LIMIAR_CONSISTENCIA = 0.98

#: N mínimo para o orçamento de 98% separar lixo residual de impostor
#: (VEREDITO_M2 §2b): "com 26 janelas, 98% = no máx. 0 falhas e o intervalo
#: de confiança da taxa é largo demais (±8pp)". Abaixo disso a âncora segue
#: confirmada, mas o veredito diz que o N não sustenta o orçamento.
MINIMO_PARA_ORCAMENTO = 100

#: O τ da âncora verificada (API_NOTES §13.8): o valor do stream no instante
#: da abertura, sem deslocamento.
TAU_VERIFICADO_S = 0


def ancora_verificada(
    serie: StreamE18, abertura_ms: int, *, idade_max_ms: int = IDADE_MAX_MS
) -> int | None:
    """A âncora que a varredura confirmou, em inteiro e18.

    É o valor do stream `crypto_prices_twap_sixty` no instante da ABERTURA,
    no eixo de carimbo do SERVIDOR — a definição registrada em API_NOTES
    §13.8, confirmada duas vezes de forma independente (1.0 sobre 152 janelas
    no M2.4; 1.0 sobre 92 no M2.6).

    `None` quer dizer **lacuna do stream no instante da abertura**, e não
    "zero": a última amostra é velha demais para descrever aquele instante.
    Devolver o valor velho seria inventar âncora, e a janela inteira sairia
    errada sem ninguém notar.
    """
    return serie.em(abertura_ms, idade_max_ms=idade_max_ms)


def valor_final(
    serie: StreamE18, fechamento_ms: int, *, idade_max_ms: int = IDADE_MAX_MS
) -> int | None:
    """O valor de liquidação: o MESMO stream no instante do fechamento.

    Existe como função separada da âncora só para o nome dizer o que é. A
    simetria é o achado: âncora e final são leituras do mesmo stream, e
    **nenhuma média é recalculada** — o feed já entrega a média da Chainlink
    pronta (API_NOTES §13.8).
    """
    return serie.em(fechamento_ms, idade_max_ms=idade_max_ms)


def _porque_caiu(
    recebidas: int, sem_cobertura: int, pior_fracao: float | None
) -> str:
    """A frase que liga 'poucas elegiveis' a 'o stream estava morto'.

    M2.10 item 5. Sem ela o leitor procura a explicacao no mercado, que e
    onde ela nao esta.
    """
    if not sem_cobertura:
        return ""
    frase = (
        f" CAUSA: {sem_cobertura} de {recebidas} janelas cairam por AUSENCIA "
        "DE STREAM da ancora, nao por nada do mercado."
    )
    if isinstance(pior_fracao, (int, float)):
        frase += (
            f" O pior ativo teve {pior_fracao:.1%} da gravacao coberta — "
            "gravar mais horas so ajuda se a captacao estiver sa."
        )
    return frase


def veredito_da_ancora(
    varredura: dict[str, Any],
    *,
    minimo_janelas: int = MINIMO_JANELAS_VEREDITO,
    cobertura: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A varredura confirma a âncora verificada, ou a plataforma mudou?

    Este é o alarme que o M2.6 pede: a âncora deixou de ser hipótese e virou
    fato registrado, então o backtest a usa direto. Mas usar sem conferir
    seria trocar uma suposição por outra — e uma mudança de regra da
    plataforma passaria calada, com o PnL saindo bonito e errado.

    A conferência é a mesma varredura, lida ao contrário: em vez de procurar
    qual τ explica as resoluções, pergunta se **τ=0 continua explicando
    todas**. Se não, o alerta é ruidoso de propósito.
    """
    fino = varredura.get("final_stream_no_fechamento") or {}
    curva = fino.get("curva") or {}
    regiao = fino.get("regiao_viavel_100pct") or []
    elegiveis = int(varredura.get("janelas_elegiveis") or 0)
    consistencia = curva.get(str(TAU_VERIFICADO_S))

    # M2.10 item 5: por que as janelas caíram fica NO veredito.
    # "SEM AMOSTRA: 8 janelas elegiveis" sem dizer que 20 morreram por falta
    # de stream convida a explicar o numero pelo mercado — e foi o que
    # aconteceu numa conversa real, onde a geometria das janelas virou
    # explicacao antes de ser desmentida. A causa estava no relatorio, em
    # outro bloco, e ninguem cruzou os dois.
    sem_cobertura = int(varredura.get("janelas_sem_cobertura_do_stream") or 0)
    recebidas = int(varredura.get("janelas_recebidas") or 0)
    pior_fracao = (cobertura or {}).get("pior_fracao_coberta")
    base = {
        "tau_verificado_s": TAU_VERIFICADO_S,
        "consistencia_do_tau_verificado": consistencia,
        "regiao_viavel_100pct": regiao,
        "janelas_elegiveis": elegiveis,
        "janelas_recebidas": recebidas,
        "janelas_sem_cobertura_do_stream": sem_cobertura,
        "pior_fracao_coberta": pior_fracao,
        "distribuicao_das_elegiveis": varredura.get("distribuicao_das_elegiveis"),
        "minimo_para_veredito": minimo_janelas,
        "referencia": "docs/API_NOTES.md §13.8",
    }

    if elegiveis < minimo_janelas:
        return {
            **base,
            "confirmada": None,
            "alerta": None,
            "veredito": (
                f"SEM AMOSTRA: {elegiveis} janelas elegiveis, abaixo do minimo "
                f"de {minimo_janelas}. A ancora verificada segue em uso — "
                "ausencia de amostra nao e desmentido —, mas esta gravacao nao "
                "a confirma nem a contradiz."
                + _porque_caiu(recebidas, sem_cobertura, pior_fracao)
            ),
        }

    # Quantas janelas discordaram, e não só a razão. "1 de 152" se lê;
    # "0.9934" precisa de conta mental para virar informação.
    discordantes = (
        round((1.0 - consistencia) * elegiveis)
        if isinstance(consistencia, (int, float))
        else None
    )
    indeterminadas = int(
        (fino.get("indeterminadas_em_tau") or {}).get(str(TAU_VERIFICADO_S)) or 0
    )
    avaliadas_no_tau = (fino.get("avaliadas_em_tau") or {}).get(
        str(TAU_VERIFICADO_S)
    )
    base = {
        **base,
        "janelas_discordantes": discordantes,
        "limiar_de_consistencia": LIMIAR_CONSISTENCIA,
        # M2.11: separado de acertos E de erros. Somar a qualquer um dos dois
        # seria dar peso de evidencia a uma folga que nao distingue nada.
        "janelas_indeterminadas": indeterminadas,
        "janelas_avaliadas_em_tau_verificado": avaliadas_no_tau,
        "limiar_de_folga_relativa": f"{FOLGA_RELATIVA_NUM}/{FOLGA_RELATIVA_DEN}",
    }

    # M2.11: `curva` marca 0.0 quando NADA foi avaliado, porque a divisao nao
    # existe. Deixar isso cair no alarme diria "tau=0 nao explica nada" onde a
    # verdade e "nenhuma janela teve folga suficiente para opinar" — o mesmo
    # erro que a tolerancia veio corrigir, uma casa acima.
    if avaliadas_no_tau == 0:
        return {
            **base,
            "confirmada": None,
            "alerta": None,
            "veredito": (
                f"SEM EVIDENCIA EM tau=0: as {elegiveis} janelas elegiveis "
                f"ficaram todas INDETERMINADAS ({indeterminadas} com folga "
                f"relativa abaixo de {FOLGA_RELATIVA_NUM}/"
                f"{FOLGA_RELATIVA_DEN}). A ancora verificada segue em uso — "
                "ausencia de evidencia nao e desmentido —, mas esta gravacao "
                "nao a confirma nem a contradiz. Confira "
                "`distribuicao_das_folgas_relativas`: massa toda abaixo do "
                "limiar e sinal de limiar frouxo demais, nao de mercado calmo."
            ),
        }

    if consistencia is not None and consistencia >= 1.0:
        return {
            **base,
            "confirmada": True,
            "alerta": None,
            "veredito": (
                f"CONFIRMADA: tau=0 explica 100% das {elegiveis} janelas "
                "elegiveis. A ancora usada no backtest e a verificada "
                "(valor do stream twap_sixty na abertura, API_NOTES 13.8)."
            ),
        }

    # A FAIXA DO ORCAMENTO (VEREDITO_M2 2b). Duas causas de lixo residual
    # estao orcadas ali: lacuna de stream fina demais para o detector de
    # cobertura, e empate mal-carimbado. Tratar isso como mudanca de regra
    # e o erro que o proprio documento mandou nao cometer.
    #
    # Quem le deve conferir `discordantes_em_tau_verificado`: folga minuscula
    # ou ancora velha confirmam lixo; folga grande com ancora fresca seria
    # outra historia, e ai o numero agregado ja teria caido abaixo do limiar.
    if consistencia is not None and consistencia >= LIMIAR_CONSISTENCIA:
        magro = elegiveis < MINIMO_PARA_ORCAMENTO
        return {
            **base,
            "confirmada": True,
            "alerta": None,
            "veredito": (
                f"CONFIRMADA COM LIXO RESIDUAL: tau=0 explica {consistencia} "
                f"das {elegiveis} janelas elegiveis — {discordantes} "
                f"discordante(s), dentro do orcamento de "
                f"{LIMIAR_CONSISTENCIA:.0%} que VEREDITO_M2 2b reservou para "
                "lacuna de stream fina e empate mal-carimbado. NAO e mudanca "
                "de regra: mudanca de regra DERRUBA a consistencia, nao a "
                "arranha. Confira `discordantes_em_tau_verificado` para ver "
                "o numero de cada falha."
                + (
                    f" RESSALVA: {elegiveis} janelas estao abaixo das "
                    f"{MINIMO_PARA_ORCAMENTO} que o criterio pede para o "
                    "orcamento separar lixo de impostor — o intervalo de "
                    "confianca ainda e largo."
                    if magro
                    else ""
                )
            ),
        }

    # Daqui para baixo é alarme. Duas formas, e a diferença importa para o
    # diagnóstico: nenhum τ funciona (o modelo do jogo mudou) ou outro τ
    # funciona (a âncora deslocou no tempo).
    if regiao:
        alerta = (
            f"MUDANCA DE REGRA: tau=0 explica {consistencia} das resolucoes "
            f"({discordantes} de {elegiveis}), ABAIXO do limiar de "
            f"{LIMIAR_CONSISTENCIA:.0%}, e a regiao de 100% existe em "
            f"{regiao}. A ancora parece ter se deslocado no tempo. NAO opere "
            "com o resultado deste backtest ate reconfirmar a ancora e "
            "atualizar API_NOTES 13.8."
        )
    else:
        alerta = (
            f"MUDANCA DE REGRA: tau=0 explica {consistencia} das "
            f"{elegiveis} janelas elegiveis ({discordantes} discordantes), "
            f"ABAIXO do limiar de {LIMIAR_CONSISTENCIA:.0%}, e nenhum tau "
            "chega a 100%. Nem a ancora verificada nem qualquer deslocamento "
            "dela reproduzem as resolucoes — o jogo pode ter mudado de fonte. "
            "NAO opere com o resultado deste backtest."
        )
    return {**base, "confirmada": False, "alerta": alerta, "veredito": alerta}
