"""O `side` do `last_trade_price` é o lado do TAKER? Verificado na gravação.

    .venv/bin/python scripts/verificar_side.py --recordings data/recordings \\
        [--desde 2026-09-09T02:19Z --ate 2026-09-12T02:19Z] [--json relatorios/SIDE.json]

Por que existe (auditoria 2026-09-17, §2.1). `live/livros.py:60` afirma que
`lado` "é o do TAKER — BUY comprou dos asks, SELL vendeu nos bids", e a caixa
(`caixa_maker.py:334`) decide execução-sombra por `negocio.lado != "SELL"`;
`analysis/measurements.py:495` escolhe `best_ask` ou `best_bid` pelo mesmo
lado. **Todo markout do quadro** — inclusive o custo de saída medido em
2026-09-17 — descende dessa frase. O `API_NOTES.md` verifica que o campo
EXISTE no fio (§6.1a), mas a única semântica de `side` marcada
`[VERIFICADO]` é a da struct da ORDEM (0 = BUY, 1 = SELL), que é o lado da
ordem, não do agressor. Dubach (arXiv 2604.24366) mede quão fácil é errar
isto nesta bolsa: direção inferida do feed concorda com a verdade on-chain
em ~59 % dos casos.

Como verifica, sem inferir nada do livro reconstruído: cada `price_change`
traz `best_bid`/`best_ask` — o topo AUTORITATIVO do servidor naquele instante
(`feeds/poly_ws.py::MudancaDePreco`). Guarda-se o último topo visto por token
ANTES de cada `last_trade_price`. Um print no ask (ou acima) só pode ser um
agressor COMPRANDO; um print no bid (ou abaixo) só pode ser um agressor
VENDENDO. Print dentro do spread não diz nada e é contado como ambíguo — não
entra na conta. Topo ausente ou velho demais também não entra.

Se `side == "BUY"` coincidir com "agressor comprou" em quase todos os prints
classificáveis, `side` é o lado do TAKER e a convenção do código está certa.
Se coincidir em quase nenhum, é o lado do MAKER e o sinal de todo markout
está invertido. No meio, o campo não é o que nenhuma das duas hipóteses diz,
e a resposta é "não sei" — que aqui é motivo de recusa, não de seguir.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any

from pulsearb.backtest.__main__ import caminho_de_leitura
from pulsearb.caminhos import caminho_de_escrita
from pulsearb.feeds.poly_ws import (
    EVENT_LAST_TRADE,
    EVENT_PRICE_CHANGE,
    eventos_do_payload,
    iter_mudancas,
)
from pulsearb.numeros import numero
from pulsearb.replay.reader import RecordingReader

#: Um print a menos de um tick do topo é "no topo": tolerância de arredondamento,
#: não de julgamento. O menor tick da bolsa é 0,001.
EPS = 1e-6
#: Topo mais velho que isto não classifica o print: o livro pode ter andado.
TOLERANCIA_TOPO_S_PADRAO = 5.0
#: Abaixo disto o veredito é SEM_AMOSTRA — não se decide o sinal de um quadro
#: inteiro com meia dúzia de prints.
MINIMO_DE_AMOSTRA = 30
#: Fração de concordância que fecha cada lado. O meio é "não sei".
LIMIAR_TAKER = 0.90
LIMIAR_MAKER = 0.10

VEREDITOS = {
    "TAKER": "`side` é o lado do TAKER: a convenção de livros.py:60 está certa",
    "MAKER": "`side` é o lado do MAKER: o sinal de TODO markout está invertido",
    "INDETERMINADO": "o campo não coincide com nenhuma das duas hipóteses — "
                     "não sei, e não sei recusa",
    "SEM_AMOSTRA": f"menos de {MINIMO_DE_AMOSTRA} prints classificáveis",
}

#: Onde cada print caiu em relação ao topo autoritativo anterior a ele.
NO_ASK = "no_ask_ou_acima"
NO_BID = "no_bid_ou_abaixo"
DENTRO = "dentro_do_spread"
SEM_TOPO = "sem_topo"
TOPO_VELHO = "topo_velho"


def _hora_utc(texto: str | None) -> datetime | None:
    if not texto:
        return None
    return datetime.fromisoformat(texto.replace("Z", "+00:00")).astimezone(UTC)


def classificar(preco: float, bid: float, ask: float) -> str:
    """Onde o print caiu. Só as duas pontas classificam; o meio é ambíguo."""
    if preco >= ask - EPS:
        return NO_ASK
    if preco <= bid + EPS:
        return NO_BID
    return DENTRO


def veredito(concorda: int, discorda: int) -> str:
    n = concorda + discorda
    if n < MINIMO_DE_AMOSTRA:
        return "SEM_AMOSTRA"
    fracao = concorda / n
    if fracao >= LIMIAR_TAKER:
        return "TAKER"
    if fracao <= LIMIAR_MAKER:
        return "MAKER"
    return "INDETERMINADO"


def _situar_print(
    ev: dict[str, Any],
    topo: dict[str, tuple[float, float, int]],
    ts_ns: int,
    tol_ns: int,
) -> tuple[str, dict[str, Any] | None]:
    """Posiciona um last_trade_price contra o topo. Devolve (posicao, detalhe).

    `detalhe` so existe quando o print e classificavel (NO_ASK/NO_BID/DENTRO);
    nas outras posicoes vem None e a posicao explica por que nao classificou.
    """
    asset = ev.get("asset_id")
    preco = numero(ev.get("price"))
    lado = str(ev.get("side", "")).upper()
    if not isinstance(asset, str) or preco is None or lado not in ("BUY", "SELL"):
        return "print_ilegivel", None
    visto = topo.get(asset)
    if visto is None:
        return SEM_TOPO, None
    bid, ask, ts_topo = visto
    if ts_ns - ts_topo > tol_ns:
        return TOPO_VELHO, None
    posicao = classificar(preco, bid, ask)
    return posicao, {"asset_id": asset[:16] + "…", "preco": preco, "bid": bid,
                     "ask": ask, "side": lado, "posicao": posicao}


def verificar(
    reader: RecordingReader,
    *,
    tolerancia_topo_s: float = TOLERANCIA_TOPO_S_PADRAO,
    exemplos: int = 5,
) -> dict[str, Any]:
    """Percorre a gravação e devolve o relatório. Pura sobre o reader."""
    topo: dict[str, tuple[float, float, int]] = {}
    posicoes: Counter[str] = Counter()
    concorda = discorda = 0
    por_lado: dict[str, Counter[str]] = defaultdict(Counter)
    discordantes: list[dict[str, Any]] = []
    prints = 0
    tol_ns = int(tolerancia_topo_s * 1e9)

    for record in reader.iter_records(incluir_meta=False):
        if record.fonte != "poly_ws":
            continue
        for ev in eventos_do_payload(record.payload):
            tipo = ev.get("event_type")
            if tipo == EVENT_PRICE_CHANGE:
                for m in iter_mudancas(ev):
                    if m.best_bid is not None and m.best_ask is not None:
                        topo[m.asset_id] = (m.best_bid, m.best_ask, record.ts_wall_ns)
                continue
            if tipo != EVENT_LAST_TRADE:
                continue
            prints += 1
            posicao, detalhe = _situar_print(ev, topo, record.ts_wall_ns, tol_ns)
            posicoes[posicao] += 1
            if detalhe is None:
                continue
            por_lado[detalhe["side"]][posicao] += 1
            if posicao == DENTRO:
                continue
            agressor_comprou = posicao == NO_ASK
            if (detalhe["side"] == "BUY") == agressor_comprou:
                concorda += 1
            else:
                discorda += 1
                if len(discordantes) < exemplos:
                    discordantes.append(detalhe)

    classificaveis = concorda + discorda
    resultado = veredito(concorda, discorda)
    return {
        "veredito": resultado,
        "o_que_significa": VEREDITOS[resultado],
        "prints": prints,
        "classificaveis": classificaveis,
        "concorda_com_taker": concorda,
        "discorda_de_taker": discorda,
        "fracao_taker": round(concorda / classificaveis, 4) if classificaveis else None,
        "posicoes": dict(sorted(posicoes.items())),
        "por_lado": {lado: dict(sorted(c.items())) for lado, c in sorted(por_lado.items())},
        "exemplos_discordantes": discordantes,
        "tolerancia_topo_s": tolerancia_topo_s,
        "limiares": {"taker": LIMIAR_TAKER, "maker": LIMIAR_MAKER, "minimo": MINIMO_DE_AMOSTRA},
        "nota": (
            "Topo = best_bid/best_ask do ultimo price_change do MESMO token antes "
            "do print (topo autoritativo do servidor, feeds/poly_ws.py). Print no "
            "ask ou acima so pode ser agressor comprando; no bid ou abaixo, "
            "vendendo; dentro do spread nao classifica. Auditoria 2026-09-17 §2.1."
        ),
    }


def _imprimir(rel: dict[str, Any]) -> None:
    print(f"prints: {rel['prints']}  classificáveis: {rel['classificaveis']}  "
          f"concorda(taker): {rel['concorda_com_taker']}  discorda: {rel['discorda_de_taker']}  "
          f"fração taker: {rel['fracao_taker']}")
    print("posições:", rel["posicoes"])
    for lado, c in rel["por_lado"].items():
        print(f"  side={lado}: {c}")
    if rel["exemplos_discordantes"]:
        print("exemplos discordantes:")
        for e in rel["exemplos_discordantes"]:
            print(f"  {e}")
    print(f"\nVEREDITO: {rel['veredito']} — {rel['o_que_significa']}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="verificar_side")
    p.add_argument("--recordings", required=True)
    p.add_argument("--desde", default=None)
    p.add_argument("--ate", default=None)
    p.add_argument("--tolerancia-topo-s", type=float, default=TOLERANCIA_TOPO_S_PADRAO)
    p.add_argument("--exemplos", type=int, default=5)
    p.add_argument("--json", default=None)
    args = p.parse_args(argv)
    try:
        caminho = caminho_de_leitura(args.recordings)
        destino = caminho_de_escrita(args.json) if args.json else None
        desde, ate = _hora_utc(args.desde), _hora_utc(args.ate)
    except ValueError as erro:
        print(str(erro), file=sys.stderr)
        return 2
    reader = RecordingReader(caminho, desde=desde, ate=ate)
    rel = verificar(reader, tolerancia_topo_s=args.tolerancia_topo_s, exemplos=args.exemplos)
    if destino is not None:
        destino.write_text(json.dumps(rel, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"relatório gravado em {destino}")
    _imprimir(rel)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
