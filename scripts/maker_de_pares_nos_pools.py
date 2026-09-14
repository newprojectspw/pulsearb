"""O maker de pares NO REGIME QUE PAGA — os pools de reward do 1.12.

`scripts/maker_de_pares.py` mediu a estrutura "bid nos dois lados, nunca
vender" nas janelas Up/Down de cripto de 5 min, e ela sai negativa: quando o
par executa dos dois lados, a soma paga fica acima de 1,00. Mas aquele NÃO é
o regime da rota viva. O 1.12 passa nos pools de reward — mercados de
temperatura, política, esporte — onde o markout medido é **4,7× menor**
(−0,0606 c/share contra −0,2838) e onde existe reward pago por estar no
livro. Transportar o veredito de um regime para o outro é o erro que este
projeto já cometeu uma vez (a banda 240-120 s, in-sample).

Então: medir de novo, no regime certo, com o MESMO motor.

## De onde vem o dado

`scripts/markout_dos_pools.py --gravar DIR` assina os tokens dos maiores
pools e grava os eventos crus no formato do recorder, precedidos de um
registro `pools_snapshot` com o catálogo (os dois tokens de cada mercado, o
tick, o pool diário). Este script lê essa gravação.

## O que muda em relação ao Up/Down, e por quê

**A perna solta não tem resolução.** Um mercado de pool resolve em dias ou
meses; a gravação dura horas. Marcar a perna sozinha "a resultado" seria
inventar o resultado. Ela é marcada a **preço de saída**: o melhor bid do
token no fim da gravação, menos o fee de taker — é o que se receberia
vendendo agora, e é o mesmo termo de custo de saída que o `markout_dos_pools`
já mede em 300/1800 s. Isso é PESSIMISTA de propósito: quem fica com a perna
pode esperar, e o mercado pode voltar.

**Não há spot.** O eixo de salto (RTDS) fica desligado: um pool de
temperatura não tem preço de Binance. O gatilho de recolher aqui é o livro.

Uso
---

    python scripts/maker_de_pares_nos_pools.py ~/pulsearb-pools \\
        --json relatorios/PARES_POOLS_20260914.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from itertools import product
from pathlib import Path
from typing import Any
from pulsearb.replay.reader import RecordingReader

# O motor mora no script irmão. `scripts/` não é pacote (e transformá-lo em
# um mudaria como TODOS os outros são invocados), então o diretório entra no
# caminho de import aqui, uma vez.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from maker_de_pares import (
    Estrategia,
    Janela,
    MakerDePares,
    caminho_de_leitura,
)

FONTE_CATALOGO = "pools_snapshot"
#: Taxa do CLOB nos mercados que não são de cripto. A gravação de pools não
#: traz `fee_rate` por mercado (o catálogo do `/markets/{cid}` não publica),
#: então o rebate sai como ZERO aqui em vez de estimado: omitir termo
#: positivo mantém a conta um limite inferior, que é como o 1.6 já trata.
RATE_DESCONHECIDA = 0.0


class MakerDeParesNosPools(MakerDePares):
    """O mesmo motor, com janelas vindas do catálogo e liquidação por saída.

    A passada 1 aqui é MUITO mais leve que a do `RecordingIndex`: não há
    grade de slugs, RTDS nem resolução — só o catálogo. É reescrita inteira
    de propósito; chamar a do pai leria o arquivo para preencher estruturas
    que este regime não tem.
    """

    def __init__(
    ) -> None:
        super().__init__(reader, **kw)
        self.catalogo: dict[str, dict[str, Any]] = {}
        self.rate_de_saida = rate_de_saida

    # --------------------------------------------------------------- passada 1
    def _primeira_passada(self) -> None:
        self.progresso.passada("passada 1", arquivos=len(self.reader.files))
        for record in self.reader.iter_records():
            self.progresso.talvez("passada 1", self.reader.total)
            if record.ts_wall_ns > 0:
                if self._primeiro_record_ns == 0:
                    self._primeiro_record_ns = record.ts_wall_ns
                self._ultimo_record_ns = max(self._ultimo_record_ns, record.ts_wall_ns)
            if record.fonte != FONTE_CATALOGO or not isinstance(record.payload, dict):
                continue
            for cid, meta in (record.payload.get("mercados") or {}).items():
                if isinstance(meta, dict):
                    self.catalogo[str(cid)] = meta
    def _marcar_tokens_de_interesse(self) -> None:
        for cid, meta in self.catalogo.items():
            tokens = [t for t in (meta.get("tokens") or []) if isinstance(t, str)]
            if len(tokens) < 2:
                continue
            janela = Janela(
                slug=str(meta.get("slug") or cid),
                asset="",
                token_up=tokens[0],
                token_down=tokens[1],
                # A "janela" é a gravação inteira: o mercado não abre nem
                # fecha dentro dela.
                abertura_ns=0,
                fim_ns=self._ultimo_record_ns or (1 << 62),
                tick=float(meta.get("tick_size") or 0.01),
                rate=RATE_DESCONHECIDA,
                exponent=1.0,
            )
            for token in tokens[:2]:
                self.janela_do_token[token] = janela
                # `_segunda_passada` do pai só roda se isto estiver preenchido.
                self.janelas_de_interesse[token] = (0, 1 << 62)
            self.janelas_cotadas[janela.slug] = janela

    # ------------------------------------------------------------ liquidação
    def _preco_de_saida(self, token: str) -> float | None:
        """O melhor bid do fim da gravação, menos o fee de taker.

        Sem bid no fim, não há preço: devolve `None`, e a janela sai do P&L
        em vez de ser marcada por um número inventado.
        """
        book = self.book_atual.get(token)
        if book is None or not book.bids:
            return None
        preco = book.bids[0][0]
        if not 0.0 < preco < 1.0:
            return None
        if self.rate_de_saida > 0:
            preco -= fee_pp_por_share(preco, rate=self.rate_de_saida, exponent=1.0)
        return preco

    def _marcar_perna_solta(
        self,
        janela: Janela,
        *,
        sobra_up: float,
        sobra_down: float,
        p_up: float,
        p_down: float,
    ) -> tuple[float | None, bool | None]:
        residual = 0.0
        for token, sobra, pago in (
            (janela.token_up, sobra_up, p_up),
            (janela.token_down, sobra_down, p_down),
        ):
            if sobra <= EPS:
                continue
            saida = self._preco_de_saida(token)
            if saida is None:
                return None, None
            residual += sobra * (saida - pago)
        # Não existe "ganhou" num mercado que não resolveu: o campo que o
        # resumo usa para medir seleção adversa fica indefinido aqui, e o
        # relatório diz isso em vez de fingir 50%.
        return residual, None

    return tuple(
        Estrategia(
            melhorar_ticks=0,
            modo="pessimista",
            ("perna_inteira", "tamanho_do_print"),
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("recordings")
    parser.add_argument("--json", default=None)
    parser.add_argument("--tamanho", type=float, default=20.0)
    parser.add_argument("--reprice-ticks", type=int, default=2)
    parser.add_argument(
        "--rate-de-saida",
        type=float,
        default=0.0,
        help=(
            "rate do fee de taker a descontar do preço de saída da perna "
            "solta (0 = sem desconto; os pools não publicam a taxa por "
            "mercado, então o default omite o termo em vez de estimá-lo)"
        ),
    )
    parser.add_argument("--detalhe", action="store_true")
    args = parser.parse_args(argv)

    try:
        caminho = caminho_de_leitura(args.recordings)
        destino = caminho_de_escrita(args.json) if args.json else None
    except ValueError as erro:
        print(str(erro), file=sys.stderr)
        return 2

    reader = RecordingReader(caminho)
    index = MakerDeParesNosPools(
        reader,
        tamanho=args.tamanho,
        reprice_ticks=args.reprice_ticks,
        estrategias=estrategias_dos_pools(),
        rate_de_saida=args.rate_de_saida,
    )
    index.build()

    if not index.janelas_cotadas:
        print(
            "nenhum catálogo `pools_snapshot` na gravação — "
            "colete com `markout_dos_pools.py --gravar`",
            file=sys.stderr,
        )
        return 1

    relatorio = index.relatorio(detalhe=args.detalhe)
    relatorio["regime"] = {
        "mercados_no_catalogo": len(index.catalogo),
        "pool_diario_usdc": round(
            sum(float(m.get("daily_rate") or 0.0) for m in index.catalogo.values()), 2
        ),
        "perna_solta": "marcada a preço de saída (melhor bid do fim), NÃO a resolução",
        "reward_nao_entra": "o reward tem número próprio no 1.12; aqui só o custo",
    }
    texto = json.dumps(relatorio, indent=2, ensure_ascii=False)
    if destino is not None:
        destino.write_text(texto, encoding="utf-8")
        print(f"\nrelatório gravado em {destino}")
    else:
        print(texto)

    print(
        "\nestratégia                                janelas  par 1perna"
        "  pnl s/reb  pnl c/reb   ¢/jan  soma_par"
    )
    for e in relatorio["estrategias"]:
        est, j, p = e["estrategia"], e["janelas"], e["pnl_usdc"]
        recolhe = "fica" if est["recolher_ms"] is None else "rec-100ms"
        nome = (
            f"para-{est['parar_antes_do_fim_s']}s {recolhe:<9} "
            f"{'trava' if est['trava_do_par'] else 'livre'} "
            f"colc-{est['colchao_x']:g} {est['atravessada'][:5]}"
        )
        soma = e["soma_pup_pdown_nos_pares"]["media"]
        print(
            f"{nome:<41} {j['cotadas']:>6} {j['com_par']:>4} {j['so_uma_perna']:>6} "
            f"{p['total_sem_rebate']:>10.2f} {p['total_com_rebate']:>10.2f} "
            f"{e['por_janela_contada_cents']:>7.2f}  "
            f"{soma if soma is not None else float('nan'):>7.3f}"
        )
    print(f"\ngerado em {datetime.now(UTC).isoformat()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
