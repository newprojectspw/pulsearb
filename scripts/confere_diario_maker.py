"""Confere, contra o livro DE AGORA, onde as cotações-sombra do maker ficaram.

Para cada `cotacao_colocada` do diário do SHADOW, busca `GET /book` do token
cotado e mede a distância entre o `preco_limite` e o meio atual, em ticks.
É a verificação do achado da rodada r4 (2026-09-14): ali o preço não olhava
o livro (0,46–0,499 em mercados de 0,03 a 0,97). Depois do conserto, cada
perna tem de estar ABAIXO do meio do SEU token, a 1–5 ticks.

O livro é o de agora, não o do instante da colocação: em mercados de
horizonte longo o meio anda pouco em minutos, e a diferença que se procura
(dezenas de ticks) é ordens de grandeza maior que esse deslocamento. Uma
perna com distância negativa (preço ACIMA do meio) ou muito grande é o
sintoma; poucas fora por um ou dois ticks é o meio que andou.

    python scripts/confere_diario_maker.py data/shadow/diario-<ts>.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx

from pulsearb.backtest.book import OrderBook
from pulsearb.markets.http import fazer_http_get_json
from pulsearb.settings import Settings

#: Onde o SHADOW grava os diários. O caminho da linha de comando tem de cair
#: aqui dentro: o script só lê diário, e um argumento que apontasse para
#: fora (`../../...`) seria erro de quem chamou, não pedido a atender.
PASTA_DOS_DIARIOS = Path("data/shadow").resolve()


def _caminho_do_diario(arg: str) -> Path:
    caminho = Path(arg).resolve()
    if PASTA_DOS_DIARIOS not in caminho.parents or caminho.suffix != ".jsonl":
        raise SystemExit(f"esperava um diário .jsonl dentro de {PASTA_DOS_DIARIOS}: {arg}")
    return caminho


def _ler_colocadas(caminho: Path) -> list[dict]:
    colocadas = []
    with caminho.open(encoding="utf-8") as f:
        for linha in f:
            registro = json.loads(linha)
            if registro.get("evento") == "cotacao_colocada":
                colocadas.append(registro)
    return colocadas


async def _conferir(colocadas: list[dict], *, tick: float) -> list[dict]:
    settings = Settings()
    saida = []
    async with httpx.AsyncClient(
        headers={"User-Agent": settings.user_agent}, timeout=15.0
    ) as http:
        get = fazer_http_get_json(http, bases=(settings.endpoints.clob,))
        for c in colocadas:
            bruto = await get(
                f"{settings.endpoints.clob}/book", {"token_id": c["token_id"]}
            )
            livro = OrderBook.from_event(bruto) if isinstance(bruto, dict) else None
            meio = livro.mid if livro is not None else None
            ticks = (
                round((meio - c["preco_limite"]) / tick, 1) if meio is not None else None
            )
            saida.append(
                {
                    "slug": c["slug"],
                    "lado_up": c["lado_up"],
                    "preco_limite": c["preco_limite"],
                    "meio_agora": meio,
                    "ticks_abaixo_do_meio": ticks,
                }
            )
    return saida


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("diario")
    parser.add_argument("--tick", type=float, default=0.01)
    args = parser.parse_args(argv)

    colocadas = _ler_colocadas(_caminho_do_diario(args.diario))
    linhas = asyncio.run(_conferir(colocadas, tick=args.tick))
    for linha in linhas:
        print(json.dumps(linha, ensure_ascii=False))

    medidas = [x["ticks_abaixo_do_meio"] for x in linhas if x["ticks_abaixo_do_meio"] is not None]
    acima = sum(1 for t in medidas if t < 0)
    longe = sum(1 for t in medidas if t > 10)
    resumo = {
        "cotacoes": len(linhas),
        "com_livro": len(medidas),
        "acima_do_meio": acima,
        "mais_de_10_ticks_abaixo": longe,
        "ticks_min": min(medidas) if medidas else None,
        "ticks_max": max(medidas) if medidas else None,
    }
    print(json.dumps({"resumo": resumo}, ensure_ascii=False), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
