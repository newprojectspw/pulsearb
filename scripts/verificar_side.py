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

## O que a primeira rodada mediu, e por que esta versão alinha pelo servidor

M2_72H no Mac, 2026-09-17, alinhando pela ORDEM DE CHEGADA: 1.801.752
prints, 1.708.080 classificáveis, **0,8274 concordam com taker** —
INDETERMINADO pelo limiar de 0,90. Os discordantes tinham padrão: prints
BUY dois e três ticks ABAIXO do bid em mercado a subir. Não é fill
impossível; é o print a chegar depois de o livro já ter andado. A fixture
real (`tests/fixtures/reais/`) mostra o porquê: o print chega ~45 ms depois
do seu carimbo de servidor, o `price_change` ~800 ms depois do dele. Ordenar
por chegada compara o print com um livro de OUTRO instante.

Esta versão alinha pelo carimbo do servidor (`timestamp`, epoch ms, que os
dois eventos trazem): o topo de um print é o último `price_change` do mesmo
token com carimbo ≤ ao do print. Como o `price_change` pode chegar DEPOIS do
print que descreve, os prints esperam `--espera-s` (3 s de chegada) antes de
serem julgados. Uma segunda medida, `janela`, pergunta se o preço do print
TOCOU o ask (BUY) ou o bid (SELL) em algum topo do último segundo de
servidor — tolera o livro a andar no meio do fill. As duas saem lado a lado.

## O que a v2 mediu, e por que o veredito é o da janela

M2_72H, 2026-09-18: alinhamento estrito **0,8368**; janela de 1 s
**0,9932** (1.770.661 concordam, 12.180 discordam, 18.800 sem toque). Os
discordantes do estrito, agora com carimbo, mostram o mecanismo: o print
de 0,69 BUY tem como "último topo ≤ carimbo" `bid 0,69 / ask 0,70`,
**1 ms antes** — é o livro DEPOIS de a compra ter esvaziado o ask de 0,69,
e o servidor emite esse `price_change` antes do `last_trade_price` do
negócio que o causou. O alinhamento estrito compara o print com o livro
pós-negócio, onde o preço do fill aparece do lado oposto. É viés
sistemático da medida, não do campo. A janela de 1 s vê o livro
pré-negócio e é a medida correcta para esta ordem de emissão. O veredito
passa a ser o dela; o estrito continua no relatório como diagnóstico.
"""

from __future__ import annotations

import argparse
import json
import sys
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
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
#: Quanto tempo de CHEGADA um print espera antes de ser julgado, para que os
#: `price_change` com carimbo anterior ao dele (que chegam ~0,8 s atrasados)
#: já tenham entrado. Medido na fixture real; 3 s dá folga.
ESPERA_S_PADRAO = 3.0
#: Janela (ms de servidor) em que o preço do print pode ter TOCADO o topo.
JANELA_MS_PADRAO = 1000
#: Quanto histórico de topos por token fica em memória (ms de servidor).
RETENCAO_MS = 30_000
#: Faixas do histograma de atraso (chegada − carimbo do servidor), em ms.
FAIXAS_DE_ATRASO_MS = (50, 200, 1000, 5000)
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


@dataclass(slots=True)
class _Topos:
    """Topos de UM token, ordenados pelo carimbo do servidor."""

    ts: list[int] = field(default_factory=list)
    bid: list[float] = field(default_factory=list)
    ask: list[float] = field(default_factory=list)

    def ingerir(self, ts_ms: int, bid: float, ask: float) -> None:
        i = bisect_right(self.ts, ts_ms)
        self.ts.insert(i, ts_ms)
        self.bid.insert(i, bid)
        self.ask.insert(i, ask)
        corte = self.ts[-1] - RETENCAO_MS
        n = bisect_left(self.ts, corte)
        if n:
            del self.ts[:n], self.bid[:n], self.ask[:n]

    def em(self, ts_ms: int) -> tuple[int, float, float] | None:
        """O último topo com carimbo ≤ ts_ms. None = nenhum."""
        i = bisect_right(self.ts, ts_ms)
        return None if i == 0 else (self.ts[i - 1], self.bid[i - 1], self.ask[i - 1])

    def tocou(self, ts_ms: int, janela_ms: int, preco: float) -> tuple[bool, bool]:
        """(tocou o ask, tocou o bid) em algum topo em vigor durante [ts−janela, ts].

        Inclui o topo que já estava em vigor no início da janela: um livro
        parado há dez segundos continua a ser o livro do último segundo.
        """
        a = max(0, bisect_right(self.ts, ts_ms - janela_ms) - 1)
        b = bisect_right(self.ts, ts_ms)
        asks = self.ask[a:b]
        bids = self.bid[a:b]
        return (
            any(abs(x - preco) <= EPS for x in asks),
            any(abs(x - preco) <= EPS for x in bids),
        )


@dataclass(slots=True)
class _Print:
    chegada_ns: int
    ts_ms: int
    asset: str
    preco: float
    lado: str


def _faixa(atraso_ms: int) -> str:
    for teto in FAIXAS_DE_ATRASO_MS:
        if atraso_ms <= teto:
            return f"<={teto}ms"
    return f">{FAIXAS_DE_ATRASO_MS[-1]}ms"


def _ler_print(ev: dict[str, Any], chegada_ns: int) -> _Print | None:
    asset = ev.get("asset_id")
    preco = numero(ev.get("price"))
    ts = numero(ev.get("timestamp"))
    lado = str(ev.get("side", "")).upper()
    if not isinstance(asset, str) or preco is None or ts is None or lado not in ("BUY", "SELL"):
        return None
    return _Print(chegada_ns=chegada_ns, ts_ms=int(ts), asset=asset, preco=preco, lado=lado)


class _Conta:
    """O que se acumula ao julgar prints. Separado do laço para o manter legível."""

    def __init__(self, *, tolerancia_topo_ms: int, janela_ms: int, exemplos: int) -> None:
        self.tolerancia_topo_ms = tolerancia_topo_ms
        self.janela_ms = janela_ms
        self.exemplos = exemplos
        self.posicoes: Counter[str] = Counter()
        self.por_lado: dict[str, Counter[str]] = defaultdict(Counter)
        self.concorda = self.discorda = 0
        self.janela: Counter[str] = Counter()
        self.discordantes: list[dict[str, Any]] = []

    def julgar(self, pr: _Print, topos: _Topos | None) -> None:
        visto = topos.em(pr.ts_ms) if topos is not None else None
        if visto is None:
            self.posicoes[SEM_TOPO] += 1
            return
        ts_topo, bid, ask = visto
        # A janela julga-se sempre que há topo: ela olha para o livro em vigor
        # no último segundo, e um livro parado há mais tempo continua em vigor.
        self._julgar_janela(pr, topos)
        if pr.ts_ms - ts_topo > self.tolerancia_topo_ms:
            self.posicoes[TOPO_VELHO] += 1
            return
        posicao = classificar(pr.preco, bid, ask)
        self.posicoes[posicao] += 1
        self.por_lado[pr.lado][posicao] += 1
        if posicao == DENTRO:
            return
        agressor_comprou = posicao == NO_ASK
        if (pr.lado == "BUY") == agressor_comprou:
            self.concorda += 1
            return
        self.discorda += 1
        if len(self.discordantes) < self.exemplos:
            self.discordantes.append(
                {"asset_id": pr.asset[:16] + "…", "ts_ms": pr.ts_ms, "preco": pr.preco,
                 "bid": bid, "ask": ask, "topo_ts_ms": ts_topo, "side": pr.lado,
                 "posicao": posicao}
            )

    def _julgar_janela(self, pr: _Print, topos: _Topos) -> None:
        tocou_ask, tocou_bid = topos.tocou(pr.ts_ms, self.janela_ms, pr.preco)
        compra = pr.lado == "BUY"
        if (compra and tocou_ask) or (not compra and tocou_bid):
            self.janela["concorda"] += 1
        elif (compra and tocou_bid) or (not compra and tocou_ask):
            self.janela["discorda"] += 1
        else:
            self.janela["sem_toque"] += 1


def _ingerir_price_change(
    ev: dict[str, Any], topos: dict[str, _Topos], chegada_ns: int, atrasos: Counter[str]
) -> None:
    ts = numero(ev.get("timestamp"))
    if ts is None:
        atrasos["sem_carimbo"] += 1
        return
    ts_ms = int(ts)
    atrasos[_faixa(chegada_ns // 1_000_000 - ts_ms)] += 1
    for m in iter_mudancas(ev):
        if m.best_bid is not None and m.best_ask is not None:
            topos.setdefault(m.asset_id, _Topos()).ingerir(ts_ms, m.best_bid, m.best_ask)


def verificar(
    reader: RecordingReader,
    *,
    tolerancia_topo_s: float = TOLERANCIA_TOPO_S_PADRAO,
    espera_s: float = ESPERA_S_PADRAO,
    janela_ms: int = JANELA_MS_PADRAO,
    exemplos: int = 5,
) -> dict[str, Any]:
    """Percorre a gravação e devolve o relatório. Pura sobre o reader."""
    topos: dict[str, _Topos] = {}
    conta = _Conta(
        tolerancia_topo_ms=int(tolerancia_topo_s * 1000), janela_ms=janela_ms, exemplos=exemplos
    )
    pendentes: deque[_Print] = deque()
    atrasos: dict[str, Counter[str]] = {"prints": Counter(), "price_changes": Counter()}
    prints = 0
    espera_ns = int(espera_s * 1e9)

    for record in reader.iter_records(incluir_meta=False):
        if record.fonte != "poly_ws":
            continue
        while pendentes and record.ts_wall_ns - pendentes[0].chegada_ns >= espera_ns:
            pr = pendentes.popleft()
            conta.julgar(pr, topos.get(pr.asset))
        for ev in eventos_do_payload(record.payload):
            tipo = ev.get("event_type")
            if tipo == EVENT_PRICE_CHANGE:
                _ingerir_price_change(ev, topos, record.ts_wall_ns, atrasos["price_changes"])
                continue
            if tipo != EVENT_LAST_TRADE:
                continue
            prints += 1
            pr = _ler_print(ev, record.ts_wall_ns)
            if pr is None:
                conta.posicoes["print_ilegivel"] += 1
                continue
            atrasos["prints"][_faixa(record.ts_wall_ns // 1_000_000 - pr.ts_ms)] += 1
            pendentes.append(pr)
    while pendentes:
        pr = pendentes.popleft()
        conta.julgar(pr, topos.get(pr.asset))

    classificaveis = conta.concorda + conta.discorda
    na_janela = conta.janela["concorda"] + conta.janela["discorda"]
    resultado = veredito(conta.janela["concorda"], conta.janela["discorda"])
    estrito = veredito(conta.concorda, conta.discorda)
    return {
        "veredito": resultado,
        "o_que_significa": VEREDITOS[resultado],
        "base_do_veredito": "janela_de_toque",
        "veredito_estrito": estrito,
        "alinhamento": "carimbo_do_servidor",
        "prints": prints,
        "classificaveis": classificaveis,
        "concorda_com_taker": conta.concorda,
        "discorda_de_taker": conta.discorda,
        "fracao_taker": round(conta.concorda / classificaveis, 4) if classificaveis else None,
        "posicoes": dict(sorted(conta.posicoes.items())),
        "por_lado": {
            lado: dict(sorted(c.items())) for lado, c in sorted(conta.por_lado.items())
        },
        "janela": {
            "janela_ms": janela_ms,
            "concorda": conta.janela["concorda"],
            "discorda": conta.janela["discorda"],
            "sem_toque": conta.janela["sem_toque"],
            "fracao_taker": round(conta.janela["concorda"] / na_janela, 4) if na_janela else None,
        },
        "atrasos_chegada_menos_servidor": {k: dict(sorted(v.items())) for k, v in atrasos.items()},
        "exemplos_discordantes": conta.discordantes,
        "tolerancia_topo_s": tolerancia_topo_s,
        "espera_s": espera_s,
        "limiares": {"taker": LIMIAR_TAKER, "maker": LIMIAR_MAKER, "minimo": MINIMO_DE_AMOSTRA},
        "nota": (
            "VEREDITO pela `janela`: o preco do print tocou o ask (BUY) ou o bid "
            "(SELL) em algum topo em vigor no ultimo segundo de servidor. O "
            "alinhamento ESTRITO (ultimo price_change com carimbo <= ao do print) "
            "fica como diagnostico: o servidor emite o livro POS-negocio antes do "
            "print, e ai o preco do fill aparece do lado oposto — vies da medida, "
            "nao do campo (M2_72H: estrito 0,8368, janela 0,9932). Auditoria "
            "2026-09-17 §2.1; API_NOTES §6.1c."
        ),
    }


def _imprimir(rel: dict[str, Any]) -> None:
    print(f"prints: {rel['prints']}  classificáveis: {rel['classificaveis']}  "
          f"concorda(taker): {rel['concorda_com_taker']}  discorda: {rel['discorda_de_taker']}  "
          f"fração taker: {rel['fracao_taker']}")
    print("posições:", rel["posicoes"])
    for lado, c in rel["por_lado"].items():
        print(f"  side={lado}: {c}")
    print("janela de toque:", rel["janela"])
    print("atrasos (chegada - servidor):", rel["atrasos_chegada_menos_servidor"])
    if rel["exemplos_discordantes"]:
        print("exemplos discordantes:")
        for e in rel["exemplos_discordantes"]:
            print(f"  {e}")
    print(
        f"\nVEREDITO (janela de toque): {rel['veredito']} — {rel['o_que_significa']}\n"
        f"  alinhamento estrito, só diagnóstico: {rel['veredito_estrito']} "
        f"(fração {rel['fracao_taker']})"
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="verificar_side")
    p.add_argument("--recordings", required=True)
    p.add_argument("--desde", default=None)
    p.add_argument("--ate", default=None)
    p.add_argument("--tolerancia-topo-s", type=float, default=TOLERANCIA_TOPO_S_PADRAO)
    p.add_argument("--espera-s", type=float, default=ESPERA_S_PADRAO)
    p.add_argument("--janela-ms", type=int, default=JANELA_MS_PADRAO)
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
    rel = verificar(
        reader,
        tolerancia_topo_s=args.tolerancia_topo_s,
        espera_s=args.espera_s,
        janela_ms=args.janela_ms,
        exemplos=args.exemplos,
    )
    if destino is not None:
        destino.write_text(json.dumps(rel, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"relatório gravado em {destino}")
    _imprimir(rel)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
