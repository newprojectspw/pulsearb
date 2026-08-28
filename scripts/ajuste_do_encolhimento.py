#!/usr/bin/env python3
"""Passo 1 do protocolo 2d: o fator de encolhimento, ajustado fora da amostra.

    python scripts/ajuste_do_encolhimento.py relatorios/FIT_21.json \
        relatorios/FIT_22.json relatorios/FIT_23.json

Aceita VÁRIOS relatórios porque o ajuste é sobre um período (21–23/08) que não
cabe numa passada só: uma gravação de três dias multiplica por três a RAM de
pico da passada 2. Rodar dia a dia e somar as curvas aqui dá o mesmo ajuste,
com cada rodada cabendo na máquina.

A soma é por FAIXA, ponderada por `n`: previsto e realizado de cada faixa
viram médias ponderadas entre os relatórios. É a mesma conta que uma rodada
única faria — as faixas são as mesmas, e cada previsão pesa uma.

E a escolha do fator é a REGRA REGISTRADA na §2d, executada em código: o balde
de maior `n` dentro da faixa operada decide; os outros saem como
sensibilidade. Fazer essa escolha a olho, depois de ver os números, é
exatamente o que o pré-registro existe para impedir.
"""

from __future__ import annotations

import importlib.util
import json
import signal
import sys
from pathlib import Path
from typing import Any

from pulsearb.backtest.report import MINIMO_DE_FAIXAS
from pulsearb.engine.decisao import BASE_DO_ENCOLHIMENTO


def _resumo():
    """O `resumo_m2` carregado por caminho — `scripts/` não é pacote.

    Importar de lá em vez de reimplementar é o ponto: o fator sai das
    MESMAS funções que o resumo usa para julgar. Duas implementações da
    mesma conta divergem no dia em que uma delas é corrigida.
    """
    caminho = Path(__file__).resolve().parent / "resumo_m2.py"
    spec = importlib.util.spec_from_file_location("resumo_m2", caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


_RESUMO = _resumo()
LIMIAR_DE_CALIBRACAO = _RESUMO.LIMIAR_DE_CALIBRACAO
caminho_do_relatorio = _RESUMO.caminho_do_relatorio
leitura_do_vies = _RESUMO.leitura_do_vies
varredura_de_encolhimento = _RESUMO.varredura_de_encolhimento
veredito_do_encolhimento = _RESUMO.veredito_do_encolhimento

#: A faixa operada do protocolo: os baldes cujo tempo restante cabe em 240 s.
#: Fora dela o modelo opera onde a própria calibração já dizia não operar.
TEMPO_OPERADO_MAX_S = 240.0


def _teto_do_balde(nome: str) -> float | None:
    """O maior tempo restante que o balde cobre, em segundos.

    Os nomes vêm do relatório: `<30s`, `60-30s`, `120-60s`, `240-120s`,
    `>240s`. O primeiro número é sempre o teto — é assim que o report os
    escreve, do mais longe para o mais perto do fechamento.
    """
    texto = nome.strip().rstrip("s")
    if texto.startswith(">"):
        return float("inf")
    if texto.startswith("<"):
        try:
            return float(texto[1:])
        except ValueError:
            return None
    try:
        return float(texto.split("-")[0])
    except ValueError:
        return None


#: Chaves do acumulador. Nomeadas porque aparecem no acúmulo e na média —
#: e um erro de digitação entre os dois lugares seria um zero silencioso.
SOMA_PREVISTO = "soma_previsto"
SOMA_REALIZADO = "soma_realizado"


def _acumular_curva(
    destino: dict[str, dict[str, float]], curva: dict[str, Any]
) -> None:
    """Soma uma curva no acumulador do balde, ponderando por `n`."""
    for faixa, celula in curva.items():
        n = celula.get("n") or 0
        previsto = celula.get("previsto")
        realizado = celula.get("realizado")
        if not n:
            continue
        if not isinstance(previsto, int | float):
            continue
        if not isinstance(realizado, int | float):
            continue
        atual = destino.setdefault(
            faixa, {"n": 0.0, SOMA_PREVISTO: 0.0, SOMA_REALIZADO: 0.0}
        )
        atual["n"] += n
        atual[SOMA_PREVISTO] += n * previsto
        atual[SOMA_REALIZADO] += n * realizado


def _medias_do_balde(faixas: dict[str, dict[str, float]]) -> dict[str, Any]:
    """O acumulador virando curva: soma ponderada dividida pelo `n` total."""
    saida = {}
    for faixa, dados in sorted(faixas.items()):
        if not dados["n"]:
            continue
        previsto = dados[SOMA_PREVISTO] / dados["n"]
        realizado = dados[SOMA_REALIZADO] / dados["n"]
        saida[faixa] = {
            "n": int(dados["n"]),
            "previsto": previsto,
            "realizado": realizado,
            # `erro` na convencao do relatorio (previsto - realizado): sem ele
            # `leitura_do_vies` descarta a celula e diz "sem faixa com amostra"
            # mesmo com n de milhares. E o campo que separa erro de escala de
            # defeito do preditor — tem de existir na curva somada.
            "erro": previsto - realizado,
        }
    return saida


def curvas_somadas(relatorios: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """As curvas de confiabilidade de vários relatórios, somadas por balde.

    Cada faixa acumula `n`, e `previsto`/`realizado` viram médias ponderadas
    por `n`. Faixa sem amostra em relatório nenhum não aparece.
    """
    acumulado: dict[str, dict[str, dict[str, float]]] = {}
    for relatorio in relatorios:
        calibracao = (relatorio.get("backtest") or {}).get("calibracao") or {}
        for balde, dados in calibracao.items():
            curva = (dados or {}).get("curva_de_confiabilidade") or {}
            _acumular_curva(acumulado.setdefault(balde, {}), curva)
    return {balde: _medias_do_balde(faixas) for balde, faixas in acumulado.items()}


def baldes_da_faixa_operada(curvas: dict[str, dict[str, Any]]) -> dict[str, int]:
    """Os baldes dentro de 240 s, com o `n` de cada um."""
    return {
        balde: sum(celula["n"] for celula in curva.values())
        for balde, curva in curvas.items()
        if (teto := _teto_do_balde(balde)) is not None
        and teto <= TEMPO_OPERADO_MAX_S
        and curva
    }


def main(argv: list[str] | None = None) -> int:
    argumentos = list(sys.argv[1:] if argv is None else argv)
    if not argumentos:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)

    relatorios = []
    for nome in argumentos:
        with caminho_do_relatorio(nome).open(encoding="utf-8") as arquivo:
            relatorios.append(json.load(arquivo))

    curvas = curvas_somadas(relatorios)
    dentro = baldes_da_faixa_operada(curvas)

    print("=" * 72)
    print("  AJUSTE DO FATOR DE ENCOLHIMENTO — protocolo 2d, passo 1")
    print("=" * 72)
    print(f"  relatorios somados: {', '.join(argumentos)}")
    print(f"  base do encolhimento: {BASE_DO_ENCOLHIMENTO}")
    print(f"  faixa operada: tempo restante <= {TEMPO_OPERADO_MAX_S:g} s\n")

    if not dentro:
        print("  NENHUM balde dentro da faixa operada tem amostra.")
        print("  Sem isso o protocolo nao tem passo 1: nao ha o que ajustar.")
        return 1

    print(f"  {'balde':<12}{'n':>10}{'ECE cru':>10}{'fator':>8}{'ECE enc':>10}  leitura")
    resultados: dict[str, tuple[float, float, float]] = {}
    for balde in sorted(dentro, key=lambda b: dentro[b], reverse=True):
        curva = curvas[balde]
        varrida = varredura_de_encolhimento(curva)
        if varrida is None:
            print(
                f"  {balde:<12}{dentro[balde]:>10}"
                f"{'sem estrutura':>28}  "
                f"(< {MINIMO_DE_FAIXAS} faixas: nao se ajusta fator aqui)"
            )
            continue
        sem, fator, com = varrida
        resultados[balde] = varrida
        print(
            f"  {balde:<12}{dentro[balde]:>10}{sem:>10.4f}"
            f"{fator:>8.2f}{com:>10.4f}  {leitura_do_vies(curva)}"
        )

    if not resultados:
        print("\n  Nenhum balde da faixa operada tem estrutura para ajustar.")
        return 1

    escolhido = max(resultados, key=lambda balde: dentro[balde])
    sem, fator, com = resultados[escolhido]
    print()
    print("-" * 72)
    print(f"  FATOR DA REGRA: {fator:.2f}")
    print(
        f"  balde {escolhido}, n={dentro[escolhido]} — o MAIOR da faixa "
        "operada, como a 2d registrou"
    )
    print(f"  ECE do ajuste: {sem:.4f} -> {com:.4f}")
    print(f"  {veredito_do_encolhimento(curvas[escolhido], fator, com)}")
    outros = {b: r[1] for b, r in resultados.items() if b != escolhido}
    if outros:
        faixa_texto = ", ".join(f"{b}={f:.2f}" for b, f in sorted(outros.items()))
        print(f"\n  SENSIBILIDADE (publicada, NAO decide): {faixa_texto}")
        print(
            "  A 2d e explicita: resultado positivo so num extremo da\n"
            "  sensibilidade, e nao no fator da regra, NAO conta como remediacao."
        )
    print("-" * 72)
    print("\n  PASSO 2 — aplique ao dia 24 (fora da amostra do ajuste):")
    print(
        "    python -m pulsearb.backtest ~/pulsearb-m2 --limite-por-token 20000 "
        f"\\\n      --niveis-por-lado 10 --fator-de-encolhimento {fator:.2f} "
        "\\\n      --json relatorios/REMEDIACAO_24AGO.json"
    )
    print("    python scripts/resumo_m2.py relatorios/REMEDIACAO_24AGO.json --encolhido")
    print(
        f"\n  Lembrete do limiar: 1.3 exige < {LIMIAR_DE_CALIBRACAO:g}, e o "
        "veredito da\n  remediacao exige 1.1 positivo E 1.2 >= 200 trades E "
        "1.3 juntos.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
