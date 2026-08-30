#!/usr/bin/env python3
"""Mede V(t) do `twap_sixty` sobre a gravação — o instrumento da §2d-ter.

**Para alimentar o modelo** (é este o comando que serve ao veredito):

    python scripts/variancia_de_transicao.py ~/pulsearb-dados \
        --dia 20260823 --json relatorios/VARIANCIA_23AGO.json

**Sem `--dia`, o relatório é EXPLORATÓRIO e o backtest o recusa.** Não é
capricho: sem o dia medido não há como provar que a curva é de período
anterior ao avaliado, e a §2d-ter exige isso. Serve para olhar a forma da
curva sobre a gravação inteira — foi assim que as três propriedades foram
verificadas —, mas não entra em `--curva-de-variancia`.

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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pulsearb.analysis.variancia_de_transicao import (
    HORIZONTES_PADRAO,
    TOLERANCIA_PADRAO_S,
    curva_de_variancia,
    veredito_da_curva,
)
from pulsearb.backtest.__main__ import caminho_de_leitura
from pulsearb.caminhos import caminho_de_escrita
from pulsearb.feeds.rtds import TOPIC_TWAP_60, parse_rtds_event
from pulsearb.replay.reader import RecordingReader

#: A chave sob a qual o veredito de cada ativo entra no relatório.
CHAVE_VEREDITO = "veredito"

#: De quantos em quantos registros o progresso sai. O silêncio de três horas
#: do M2.15 foi caro o bastante para virar regra da casa.
PASSO_DO_PROGRESSO = 500_000


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
    """Os arquivos que PODEM conter ticks do dia pedido.

    Inclui as horas do próprio dia **mais as duas horas de borda** — a 23h do
    dia anterior e a 00h do seguinte. O nome do arquivo é aproximação: o
    `RecordingReader` documenta que um evento de 13:59:59,9 pode estar no
    arquivo das 14h. Ficar só nos nomes do dia deixaria entrar tick do dia
    seguinte e sairia tick do dia pedido — e é justamente o vazamento que o
    `--dia` existe para impedir.

    Quem decide de fato é o `ticks_do_dia`, pelo relógio de origem. Aqui só se
    escolhe o que abrir, e abrir duas horas a mais custa pouco.

    Filtra a lista que o `RecordingReader` já montou, em vez de fazer o
    próprio `glob` a partir do caminho da linha de comando: um `glob` sobre
    caminho externo é acesso ao disco guiado por entrada de fora, e a raiz
    NÃO pode ser contida numa pasta de trabalho — a gravação mora em
    `~/pulsearb-dados` de propósito. Comparar nomes não abre caminho nenhum.
    """
    dia_utc = _dia_valido(dia)
    marcas = tuple(
        f"pulsearb-{(dia_utc + timedelta(days=delta)).strftime('%Y%m%d')}-"
        for delta in (-1, 0, 1)
    )
    do_dia = f"pulsearb-{dia}-"
    escolhidos = []
    for arquivo in arquivos:
        nome = arquivo.name
        if nome.startswith(do_dia):
            escolhidos.append(arquivo)
        elif nome.startswith(marcas[0]) and nome[len(marcas[0]) :].startswith("23"):
            escolhidos.append(arquivo)
        elif nome.startswith(marcas[2]) and nome[len(marcas[2]) :].startswith("00"):
            escolhidos.append(arquivo)
    return sorted(escolhidos)


def _dia_valido(dia: str) -> datetime:
    """`--dia` vira data de verdade, e recusa o que não for oito dígitos.

    O valor vem da linha de comando e é comparado contra nomes de arquivo.
    Validar contra padrão fixo ANTES de qualquer uso é a regra que o M2.5
    fixou para o `--json`.
    """
    if not PADRAO_DE_DIA.fullmatch(dia):
        raise ValueError(
            f"dia inválido: {dia!r} — esperado YYYYMMDD, oito dígitos "
            "(ex.: 20260823)"
        )
    try:
        return datetime.strptime(dia, "%Y%m%d").replace(tzinfo=UTC)
    except ValueError as erro:
        raise ValueError(f"dia inválido: {dia!r} — não é uma data") from erro


def ticks_do_dia(dia: str, ts_ms: int) -> bool:
    """O tick pertence ao dia pedido, pelo relógio de ORIGEM.

    É o mesmo relógio que a medição usa para espaçar os pares. Usar o nome do
    arquivo aqui misturaria duas réguas — e a que decide tem de ser a que o
    dado carrega, não a que o recorder escolheu na hora de rotacionar.
    """
    return (
        datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC).strftime("%Y%m%d") == dia
    )


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
                f"formato pulsearb-{dia}-HHMM.jsonl.gz"
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
        if dia and not ticks_do_dia(dia, tick.src_timestamp_ms):
            # Veio de um arquivo de borda, mas o relógio de origem diz que é
            # de outro dia. Fora — senão a curva out-of-sample carregaria
            # observações do dia que ela vai calibrar.
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
            "YYYYMMDD: mede só o dia pedido, decidindo pelo relógio de "
            "ORIGEM de cada tick. OBRIGATÓRIO para o relatório servir de "
            "entrada do `--curva-de-variancia`: sem ele o backtest recusa a "
            "curva, porque não há como provar que é de período anterior ao "
            "avaliado (§2d-ter). Sem `--dia` o relatório é exploratório."
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
    if not args.dia:
        # Avisa AGORA, e não três horas depois quando o backtest recusar. O
        # M2.15 já pagou por descobrir tarde o que dava para saber cedo.
        print(
            "AVISO: sem --dia, este relatorio e EXPLORATORIO. O backtest "
            "recusa curva sem `dia_medido`, porque sem ele nao ha como provar "
            "que e de periodo anterior ao avaliado (VEREDITO_M2 2d-ter).",
            file=sys.stderr,
            flush=True,
        )
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
