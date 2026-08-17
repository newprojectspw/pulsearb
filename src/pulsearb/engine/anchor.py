"""Validação empírica da âncora de abertura — fecha a pendência do M0.

## O problema

A regra diz: "Up se o TWAP do intervalo ≥ **preço no início do intervalo**".
Mas "preço no início do intervalo" é ambíguo, e a diferença entre as leituras
é da ordem de alguns dólares em BTC — o suficiente para inverter o resultado
em janelas apertadas, que são justamente onde o edge estaria.

Hipóteses concorrentes, todas plausíveis pela leitura do texto:

| Hipótese | Definição |
|---|---|
| `ultimo_antes` | último valor do stream com ts ≤ abertura |
| `primeiro_depois` | primeiro valor com ts ≥ abertura |
| `mais_proximo` | o valor cujo ts é o mais próximo da abertura |
| `twap_na_abertura` | o TWAP de 60s calculado NO instante da abertura |
| `interpolado` | interpolação linear entre o anterior e o posterior |

## O método

Isto não se resolve lendo documentação — se resolvesse, o M0 já teria
resolvido. Resolve-se por **falsificação**: para cada janela resolvida na
gravação, calcula-se o resultado que CADA hipótese produziria e compara-se
com a resolução real. A hipótese que reproduz 100% das resoluções é a
verdadeira; qualquer uma que erre uma única vez está morta.

Janelas apertadas (onde as hipóteses discordam entre si) valem muito mais que
janelas óbvias — o relatório separa as duas coisas, porque 99% de acerto em
janelas que qualquer hipótese acertaria não prova nada.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from pulsearb.engine.twap import TWAP_WINDOW_SECONDS_DEFAULT


class AnchorHypothesis(StrEnum):
    ULTIMO_ANTES = "ultimo_antes"
    PRIMEIRO_DEPOIS = "primeiro_depois"
    MAIS_PROXIMO = "mais_proximo"
    TWAP_NA_ABERTURA = "twap_na_abertura"
    INTERPOLADO = "interpolado"


ALL_HYPOTHESES = tuple(AnchorHypothesis)


def compute_anchor(
    hypothesis: AnchorHypothesis,
    samples: Sequence[tuple[int, float]],
    open_ts_ns: int,
    *,
    window_seconds: float = TWAP_WINDOW_SECONDS_DEFAULT,
) -> float | None:
    """Valor da âncora sob uma hipótese. None = dado insuficiente.

    `samples` deve estar ordenado por timestamp.
    """
    if not samples:
        return None

    antes = [(ts, p) for ts, p in samples if ts <= open_ts_ns]
    depois = [(ts, p) for ts, p in samples if ts >= open_ts_ns]

    if hypothesis is AnchorHypothesis.ULTIMO_ANTES:
        return antes[-1][1] if antes else None

    if hypothesis is AnchorHypothesis.PRIMEIRO_DEPOIS:
        return depois[0][1] if depois else None

    if hypothesis is AnchorHypothesis.MAIS_PROXIMO:
        candidatos = []
        if antes:
            candidatos.append(antes[-1])
        if depois:
            candidatos.append(depois[0])
        if not candidatos:
            return None
        return min(candidatos, key=lambda s: abs(s[0] - open_ts_ns))[1]

    if hypothesis is AnchorHypothesis.TWAP_NA_ABERTURA:
        corte = open_ts_ns - int(window_seconds * 1e9)
        janela = [p for ts, p in samples if corte <= ts <= open_ts_ns]
        return sum(janela) / len(janela) if janela else None

    # INTERPOLADO — sem os dois lados não há o que interpolar; cai para o
    # único lado disponível.
    if not antes:
        return depois[0][1] if depois else None
    if not depois:
        return antes[-1][1]
    ts0, p0 = antes[-1]
    ts1, p1 = depois[0]
    if ts1 == ts0:
        return p0
    frac = (open_ts_ns - ts0) / (ts1 - ts0)
    return p0 + frac * (p1 - p0)


@dataclass(frozen=True, slots=True)
class WindowOutcome:
    """Uma janela resolvida, com o stream que a cercou."""

    slug: str
    open_ts_ns: int
    close_ts_ns: int
    samples: tuple[tuple[int, float], ...]  # (ts_ns, preço), ordenado
    resolved_up: bool                        # a resolução REAL, do CLOB


@dataclass
class HypothesisScore:
    hypothesis: AnchorHypothesis
    acertos: int = 0
    erros: int = 0
    indeterminados: int = 0
    acertos_apertados: int = 0
    erros_apertados: int = 0
    falhas: list[str] = field(default_factory=list)

    @property
    def total_avaliado(self) -> int:
        return self.acertos + self.erros

    @property
    def taxa_acerto(self) -> float:
        return self.acertos / self.total_avaliado if self.total_avaliado else float("nan")

    @property
    def sobreviveu(self) -> bool:
        """Uma hipótese verdadeira acerta TUDO. Um erro já a falsifica."""
        return self.erros == 0 and self.total_avaliado > 0


def final_twap(
    outcome: WindowOutcome, *, window_seconds: float = TWAP_WINDOW_SECONDS_DEFAULT
) -> float | None:
    """TWAP no instante do fechamento — o lado esquerdo da comparação."""
    corte = outcome.close_ts_ns - int(window_seconds * 1e9)
    janela = [p for ts, p in outcome.samples if corte <= ts <= outcome.close_ts_ns]
    return sum(janela) / len(janela) if janela else None


def evaluate_hypotheses(
    outcomes: Sequence[WindowOutcome],
    *,
    window_seconds: float = TWAP_WINDOW_SECONDS_DEFAULT,
    margem_apertada_bps: float = 2.0,
) -> dict[AnchorHypothesis, HypothesisScore]:
    """Confronta cada hipótese com as resoluções REAIS.

    `margem_apertada_bps`: uma janela é "apertada" quando |TWAP_final − âncora|
    é menor que essa fração do preço. São essas que separam as hipóteses; as
    demais qualquer uma acerta.
    """
    scores = {h: HypothesisScore(hypothesis=h) for h in ALL_HYPOTHESES}

    for outcome in outcomes:
        twap_fim = final_twap(outcome, window_seconds=window_seconds)
        if twap_fim is None:
            for score in scores.values():
                score.indeterminados += 1
            continue

        for hypothesis, score in scores.items():
            ancora = compute_anchor(
                hypothesis, outcome.samples, outcome.open_ts_ns, window_seconds=window_seconds
            )
            if ancora is None or ancora <= 0:
                score.indeterminados += 1
                continue
            # Empate resolve Up: o >= é literal (API_NOTES 12.4).
            previsto_up = twap_fim >= ancora
            apertada = abs(twap_fim - ancora) / ancora * 10_000 < margem_apertada_bps
            if previsto_up == outcome.resolved_up:
                score.acertos += 1
                if apertada:
                    score.acertos_apertados += 1
            else:
                score.erros += 1
                if apertada:
                    score.erros_apertados += 1
                if len(score.falhas) < 10:
                    score.falhas.append(
                        f"{outcome.slug}: previu {'Up' if previsto_up else 'Down'}, "
                        f"resolveu {'Up' if outcome.resolved_up else 'Down'} "
                        f"(twap {twap_fim:.4f} vs âncora {ancora:.4f})"
                    )
    return scores


def report_anchor_validation(
    scores: dict[AnchorHypothesis, HypothesisScore],
) -> dict[str, object]:
    """Relatório pronto para o VEREDITO — inclusive quando é inconclusivo."""
    sobreviventes = [h.value for h, s in scores.items() if s.sobreviveu]
    apertadas = sum(s.acertos_apertados + s.erros_apertados for s in scores.values()) / max(
        len(scores), 1
    )
    if len(sobreviventes) == 1:
        veredito = f"âncora identificada: {sobreviventes[0]}"
    elif not sobreviventes:
        veredito = (
            "NENHUMA hipótese sobreviveu — a âncora não é nenhuma das testadas, "
            "ou a gravação do stream tem lacunas nos instantes de abertura"
        )
    else:
        veredito = (
            f"INCONCLUSIVO: {len(sobreviventes)} hipóteses sobreviveram "
            f"({', '.join(sobreviventes)}). Faltam janelas apertadas para separá-las."
        )
    return {
        "veredito": veredito,
        "janelas_apertadas_medias": round(apertadas, 1),
        "por_hipotese": {
            h.value: {
                "acertos": s.acertos,
                "erros": s.erros,
                "indeterminados": s.indeterminados,
                "taxa_acerto": (
                    None if s.total_avaliado == 0 else round(s.taxa_acerto, 6)
                ),
                "apertadas_certas": s.acertos_apertados,
                "apertadas_erradas": s.erros_apertados,
                "sobreviveu": s.sobreviveu,
                "exemplos_de_falha": s.falhas,
            }
            for h, s in scores.items()
        },
    }
