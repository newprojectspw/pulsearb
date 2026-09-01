#!/usr/bin/env python3
"""Mede a fatia de execução do maker sobre uma gravação — o número do 1.6.

    python scripts/mede_fatia_de_execucao.py ~/pulsearb-m2-25 --shares 50

A conta da rota maker é `rewards − markout × shares_executadas`. Os dois
primeiros termos estão medidos; o terceiro depende da posição na fila, que o
WS agregado não entrega. Este script mede os **dois extremos** da fila e diz
se eles têm o mesmo sinal — porque, se tiverem, a fila deixa de importar para
a decisão e o 1.6 fecha sem ela.

Ver `pulsearb/analysis/fila.py` para o porquê de cada limite.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pulsearb.analysis.fila import conta_do_maker, medir_fatia_de_execucao

#: Markout medido no critério 1.7, em centavos por share. Negativo = custo.
MARKOUT_PADRAO = -0.1974


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", help="pasta com os .jsonl.gz da gravação")
    parser.add_argument(
        "--shares",
        type=float,
        default=50.0,
        help="tamanho da NOSSA cotação, por lado (default: 50)",
    )
    parser.add_argument(
        "--rewards",
        type=float,
        required=True,
        help="rewards em USDC no período, do relatório do backtest",
    )
    parser.add_argument(
        "--markout",
        type=float,
        default=MARKOUT_PADRAO,
        help=f"markout em centavos/share (default: {MARKOUT_PADRAO})",
    )
    args = parser.parse_args()

    stage = Path(args.stage).expanduser()
    if not stage.is_dir():
        raise SystemExit(f"pasta não encontrada: {stage}")

    # Importa aqui, e não no topo: o backtest puxa metade do projeto, e um
    # `--help` não deveria pagar por isso.
    from pulsearb.backtest.__main__ import RecordingIndex, RecordingReader

    arquivos = sorted(stage.glob("*.jsonl.gz"))
    if not arquivos:
        raise SystemExit(f"nenhum .jsonl.gz em {stage}")
    print(f"lendo {len(arquivos)} arquivo(s) de {stage}…", file=sys.stderr)

    # A MESMA leitura do backtest, com os mesmos defaults de retenção: uma
    # segunda forma de ler a gravação faria esta medição e o relatório
    # discordarem sobre o que estava no livro.
    index = RecordingIndex(RecordingReader(stage))
    index.build()
    janelas = [j for j in index.janelas() if j.resolveu_up is not None]
    print(f"{len(janelas)} janelas com resolução", file=sys.stderr)

    fatia = medir_fatia_de_execucao(janelas, nossa_cotacao_shares=args.shares)
    conta = conta_do_maker(
        fatia,
        rewards_usdc=args.rewards,
        markout_centavos_por_share=args.markout,
    )

    print("=" * 74)
    print(f"FATIA DE EXECUCAO DO MAKER — cotacao de {args.shares:g} shares/lado")
    print("=" * 74)
    print(f"  execucoes no topo:        {fatia.execucoes:,}")
    print(f"  shares do fluxo:          {fatia.shares_do_fluxo:,.0f}")
    print(f"  descartadas (sem livro):  {fatia.sem_referencia:,}")
    print()
    for chave, rotulo in (("pior_caso", "PIOR"), ("melhor_caso", "MELHOR")):
        c = conta[chave]
        f = c["fatia_do_fluxo"]
        print(f"  {rotulo} caso — {c['posicao']}:")
        print(
            f"    executariamos {c['shares_executadas']:,.0f} shares"
            f"  ({'—' if f is None else f'{100 * f:.1f}%'} do fluxo)"
        )
        print(
            f"    rewards {conta['rewards_usdc']:+.2f}"
            f"   markout {-c['custo_de_markout_usdc']:+.2f}"
            f"   =>  LIQUIDO {c['liquido_usdc']:+.2f} USDC"
        )
        print()
    if conta["a_fila_decide"]:
        print("  >> OS SINAIS DIFEREM: a fila decide, e ela nao e observavel.")
        print("     O 1.6 continua nao avaliavel — falta a posicao na fila.")
    else:
        sinal = "POSITIVO" if conta["pior_caso"]["liquido_usdc"] > 0 else "NEGATIVO"
        print(f"  >> OS DOIS EXTREMOS DAO {sinal}.")
        print("     Qualquer posicao real cai entre eles, entao a FILA NAO")
        print("     DECIDE — e o 1.6 pode ser avaliado sem observa-la.")
    print()
    print("  Um periodo nao e veredito: repita em dia independente.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
