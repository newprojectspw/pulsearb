#!/usr/bin/env python3
"""Mede V(t) do `twap_sixty` sobre a gravação — o instrumento da §2d-ter.

    python scripts/variancia_de_transicao.py ~/pulsearb-m2 \
        --json relatorios/VARIANCIA_24AGO.json

Uma passada só, e só sobre os ticks do RTDS: não reconstrói book nenhum, não
casa janela com resolução, não decide trade. Por isso custa uma fração do que
o backtest custa — o que ele mede não depende de nada disso.

## Por que este script existe

O `prob_up_twap` DERIVAVA a variância do valor de liquidação, e errou duas
vezes: faltou o tempo antes de a janela de 60 s começar (§2d-ter), e o
observável está trocado — a §13.8 do `API_NOTES.md` verificou que a janela
resolve por UM PONTO do stream `twap_sixty`, sem média nenhuma, e é esse mesmo
stream já suavizado que alimenta o `sigma_1s`.

Este script não deriva nada. Mede `V(t) = Var(T_{s+t}/T_s − 1)` direto do
gravado, que é a mesma metodologia com que a §13.8 achou a âncora.

## Como ler a saída

- `variancia_por_segundo` constante em `t` ⇒ caminhada aleatória, sem
  suavização visível.
- `variancia_por_segundo` CRESCENDO com `t` ⇒ a série é suavizada, e
  `fator_de_suavizacao_medido` diz o tamanho.
- `razao_contra_o_modelo` é quanto o modelo erra a variância naquele
  horizonte. Um significa acertar. A raiz dele é o erro no desvio-padrão, que
  é o que satura a probabilidade.

O veredito por ativo sai junto porque oito ativos concordando é evidência, e
um ativo destoando é defeito de feed daquele ativo — a distinção que o Bloco 0
levou três semanas para aprender a fazer.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from pulsearb.analysis.variancia_de_transicao import (
    HORIZONTES_PADRAO,
    TOLERANCIA_PADRAO_S,
    curva_de_variancia,
    veredito_da_curva,
)
from pulsearb.backtest.__main__ import caminho_de_escrita, caminho_de_leitura
from pulsearb.feeds.rtds import TOPIC_TWAP_60, parse_rtds_event
from pulsearb.replay.reader import RecordingReader

#: A chave sob a qual o veredito de cada ativo entra no relatório.
CHAVE_VEREDITO = "veredito"

#: De quantos em quantos registros o progresso sai. O silêncio de três horas
#: do M2.15 foi caro o bastante para virar regra da casa.
PASSO_DO_PROGRESSO = 500_000


#: Como o recorder nomeia as horas: `pulsearb-20260823-0000.jsonl.gz`.
PADRAO_DO_DIA = "pulsearb-{dia}-[0-9][0-9][0-9][0-9].jsonl*"

#: Forma aceita para `--dia`: oito dígitos, e só.
#:
#: O valor vem da linha de comando e é INTERPOLADO num glob. Sem esta trava,
#: `--dia ../../etc` produziria o padrão `pulsearb-../../etc-[0-9]...` e a
#: busca sairia da raiz — a mesma travessia que o M2.5 fechou no `--json` e
#: que a contenção do `--curva-de-variancia` fechou na leitura. Validar
#: ANTES contra um padrão fixo é o que impede o valor externo de chegar ao
#: sistema de arquivos em forma nenhuma.
PADRAO_DE_DIA = re.compile(r"[0-9]{8}")


def arquivos_do_dia(arquivos: list[Path], dia: str) -> list[Path]:
    """Os arquivos de UM dia, por nome exato — sem a margem de ±1 h.

    Filtra uma lista que o `RecordingReader` já montou, em vez de fazer o
    próprio `glob` a partir do caminho da linha de comando. A diferença é de
    superfície: um `glob` sobre caminho externo é acesso ao disco guiado por
    entrada de fora, e o padrão fixo do `--dia` fecha só metade disso — a
    outra metade é a raiz, que NÃO pode ser contida numa pasta de trabalho
    porque a gravação mora em `~/pulsearb-dados` de propósito. Filtrar por
    nome é comparação de string: não abre caminho nenhum.

    O `RecordingReader` recorta por fatia de hora com uma hora de margem de
    cada lado, e faz certo: o nome do arquivo é aproximação, e uma janela que
    abre às 13:58 precisa do book da hora anterior.

    Aqui a margem seria dano. Esta medição é estatística de PARES sobre uma
    série longa — não tem borda de janela para preservar —, e a curva existe
    para calibrar um veredito de OUTRO dia. Uma hora do dia avaliado dentro
    da curva que o calibra é exatamente o vazamento in-sample que a §2d
    proibiu. Uma hora em 23 é pouco; a regra não é sobre quanto, é sobre se.
    """
    if not PADRAO_DE_DIA.fullmatch(dia):
        raise ValueError(
            f"dia inválido: {dia!r} — esperado YYYYMMDD, oito dígitos "
            "(ex.: 20260823)"
        )
    marca = f"pulsearb-{dia}-"
    return sorted(p for p in arquivos if p.name.startswith(marca))


def series_da_gravacao(
    raiz: Path, *, progresso: bool = True, dia: str | None = None
) -> tuple[dict[str, list[tuple[int, float]]], dict[str, int]]:
    """Uma passada, guardando só os ticks de `twap_sixty` por ativo.

    **O relógio é o do SERVIDOR (`src_timestamp_ms`), não o da chegada local.**
    Aqui isso não é preferência de estilo: a medição é toda sobre DISTÂNCIA
    entre observações, e `ts_wall_ns` carrega a latência da rede, a pausa do
    processo e qualquer ajuste do relógio local. Um par de 240 s que chegou com
    1 s de atraso viraria um par de 241 s — e a `tolerancia_s` recusaria, ou
    pior, aceitaria o vizinho errado. O `live/precos.py` já anota por
    `ts_servidor_ms`; o instrumento seguia o relógio errado.

    Tick sem timestamp de origem (`src_timestamp_ms == 0`, que é o que o
    `parse_rtds_event` põe quando o payload não traz `timestamp`) é
    DESCARTADO e CONTADO. Aproveitá-lo caindo para a chegada local misturaria
    duas réguas na mesma série, e o descarte silencioso é o defeito que o M2.8
    já pagou.
    """
    leitor = RecordingReader(raiz)
    if dia:
        # Recorta a lista que o leitor montou. `files` é o que ele itera, e
        # trocá-la aqui é o mesmo que tê-lo construído com ela — sem um
        # segundo caminho de descoberta que pudesse divergir do primeiro.
        leitor.files = arquivos_do_dia(leitor.files, dia)
        if not leitor.files:
            raise SystemExit(
                f"nenhum arquivo de {dia} na gravação — esperado nome no "
                f"formato {PADRAO_DO_DIA.format(dia=dia)}"
            )
    series: dict[str, list[tuple[int, float]]] = defaultdict(list)
    descartes: dict[str, int] = defaultdict(int)
    lidos = 0
    for record in leitor.iter_records(incluir_meta=False):
        lidos += 1
        if progresso and lidos % PASSO_DO_PROGRESSO == 0:
            print(
                f"{lidos:,} registros | "
                f"{sum(len(v) for v in series.values()):,} ticks de twap",
                file=sys.stderr,
                flush=True,
            )
        tick = parse_rtds_event(record.payload, record.ts_mono_ns, record.ts_wall_ns)
        if tick is None or tick.topic != TOPIC_TWAP_60:
            continue
        if tick.src_timestamp_ms <= 0:
            descartes[tick.asset] += 1
            continue
        series[tick.asset].append((tick.src_timestamp_ms * 1_000_000, tick.price))
    return dict(series), dict(descartes)


def medir(
    series: dict[str, list[tuple[int, float]]],
    *,
    horizontes_s: tuple[float, ...] = HORIZONTES_PADRAO,
    tolerancia_s: float = TOLERANCIA_PADRAO_S,
) -> dict[str, Any]:
    """Curva por ativo, mais a curva agregada de todos os ativos juntos."""
    por_ativo: dict[str, Any] = {}
    for asset, serie in sorted(series.items()):
        if len(serie) < 2:
            continue
        curva = curva_de_variancia(
            serie, horizontes_s=horizontes_s, tolerancia_s=tolerancia_s
        )
        curva[CHAVE_VEREDITO] = veredito_da_curva(curva)
        por_ativo[asset] = curva

    return {
        "por_ativo": por_ativo,
        "ativos": len(por_ativo),
        "concordam_sobre_suavizacao": _concordancia(por_ativo),
    }


def _concordancia(por_ativo: dict[str, Any]) -> dict[str, Any]:
    """Oito ativos concordando é evidência; um destoando é defeito de feed."""
    # Só entra na conta o ativo cujo veredito foi AVALIÁVEL. Um ativo com
    # gravação curta demais para medir os dois regimes não é evidência de
    # "não há suavização" — é ausência de evidência, e somar as duas coisas
    # produziria `unanime: true` sobre zero medições.
    avaliaveis = [
        c[CHAVE_VEREDITO]
        for c in por_ativo.values()
        if c[CHAVE_VEREDITO]["avaliavel"]
    ]
    vereditos = [v["ha_suavizacao"] for v in avaliaveis]
    fatores = [
        f for v in avaliaveis if (f := v["fator_de_suavizacao_medido"]) is not None
    ]
    return {
        "com_suavizacao": sum(vereditos),
        "avaliados": len(avaliaveis),
        "sem_amostra_para_avaliar": len(por_ativo) - len(avaliaveis),
        "unanime": bool(vereditos) and (all(vereditos) or not any(vereditos)),
        "fator_minimo": min(fatores) if fatores else None,
        "fator_maximo": max(fatores) if fatores else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raiz", type=Path, help="pasta da gravação")
    parser.add_argument(
        "--json",
        dest="saida",
        help="caminho RELATIVO da saída (contido como no backtest, M2.5)",
    )
    parser.add_argument(
        "--dia",
        default=None,
        help=(
            "YYYYMMDD: mede só os arquivos daquele dia, por nome exato e sem "
            "a margem de ±1 h. Use quando a curva vai calibrar o veredito de "
            "OUTRO dia — misturar os dois é o vazamento in-sample que a §2d "
            "proibiu para o fator de encolhimento."
        ),
    )
    parser.add_argument("--sem-progresso", action="store_true")
    args = parser.parse_args(argv)

    # A pasta da gravação também vem de fora do programa, e daqui ela vai
    # parar num `glob`. `caminho_de_leitura` é o tratamento que o backtest já
    # dá ao MESMO argumento (`recordings`): resolve para caminho canônico e
    # confirma que existe, sem contê-lo numa raiz — a gravação mora em
    # `~/pulsearb-dados` de propósito, e contê-la no diretório de trabalho
    # quebraria o runbook. Reusar em vez de escrever um terceiro tratamento é
    # a regra da casa: duas cópias divergem no dia em que uma é corrigida.
    raiz = caminho_de_leitura(str(args.raiz))
    series, descartes = series_da_gravacao(
        raiz, progresso=not args.sem_progresso, dia=args.dia
    )
    if not series:
        print("nenhum tick de twap_sixty com timestamp de origem", file=sys.stderr)
        return 2

    relatorio = medir(series)
    relatorio["ticks_sem_timestamp_de_origem"] = descartes
    # O dia sai no relatório para que a pergunta "esta curva calibra o
    # veredito de que dia?" seja respondível pelo arquivo, e não pela memória
    # de quem rodou.
    relatorio["dia_medido"] = args.dia
    texto = json.dumps(relatorio, indent=2, ensure_ascii=False)
    if args.saida:
        destino = caminho_de_escrita(args.saida)
        destino.write_text(texto + "\n", encoding="utf-8")
        print(f"relatorio em {destino}", file=sys.stderr)
    else:
        print(texto)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
