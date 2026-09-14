"""O custo da rota maker NOS MERCADOS QUE PAGAM. A peça que falta no 1.12.

    .venv/bin/python scripts/markout_dos_pools.py --top 60 --duracao 4h \
        --json relatorios/MARKOUT_POOLS.json

## Por que esta medição precisa existir

O 1.12 (§2f) nasceu **NÃO AVALIÁVEL**, e por um motivo só: o markout medido
pelo projeto — −0,2838 c/share sobre 595.537 execuções — vem de janelas Up/Down
de **5 minutos de cripto**. Os mercados que pagam reward são outros: temperatura
em Denver, Emmy, spread da NFL, petróleo. Resolução por oráculo em dias.

Aplicar aquele número aqui seria transportar uma medição para fora do regime em
que ela foi feita — exatamente o erro que o projeto já cometeu uma vez, quando
adotou a banda 240-120s a partir de uma amostra in-sample.

Então: medir de novo, **no regime certo**.

## Por que não dava para usar o recorder

O `pulsearb.recorder` descobre janelas por **grade de slug** Up/Down — ele não
aponta para mercado arbitrário, e ensiná-lo a fazer isso mudaria o caminho que
grava as 116 h de dado que o M2 inteiro usa. Este coletor é separado de
propósito: ele assina os tokens que a varredura de pools escolheu, e só.

## O mesmo caminho, e isso não é slogan aqui

O cálculo NÃO é reimplementado: monta-se `BookTimeline` e a lista de trades
exatamente como a passada 2 do backtest monta, e chama-se `medir_markout` —
a MESMA função que produziu os −0,2838. Se os dois números discordarem, a
diferença é de mercado, não de código. Reimplementar aqui produziria uma
medição que concorda consigo mesma e discorda do bot.

`duracao_s` do objeto que vai à função vira a janela de reward (`3600`), não
a duração do mercado: o recorte `duracao=` da tabela existe para agrupar
regimes comparáveis, e um mercado de eleição com `duracao_s` de 10 milhões de
segundos criaria um recorte por mercado — tabela com n=1 em toda linha.

## `--gravar`: a mesma coleta, guardada para replay

Sem gravar, cada pergunta nova sobre o regime dos pools exige coletar de
novo — horas de espera por pergunta, e nunca a MESMA amostra em duas
perguntas diferentes. Com `--gravar DIR` os eventos crus vão para
`DIR/pools-YYYYmmdd-HH.jsonl.gz` no MESMO formato do recorder (`fonte`
`poly_ws`), precedidos de um registro `pools_snapshot` com o catálogo de
mercados (tokens, tick, pool diário). `scripts/maker_de_pares_nos_pools.py`
lê exatamente isso.

## O que ele NÃO faz

Não envia ordem. Não assina nada. Não recebe credencial. É leitura de socket
público, igual ao recorder.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from pulsearb.analysis.measurements import medir_markout
from pulsearb.backtest.book import OrderBook
from pulsearb.backtest.runner import BookTimeline
from pulsearb.caminhos import caminho_de_escrita
from pulsearb.feeds.poly_ws import (
    EVENT_BOOK,
    EVENT_LAST_TRADE,
    EVENT_PRICE_CHANGE,
    PolyMarketWsFeed,
    eventos_do_payload,
)
from pulsearb.recorder.writer import CANAL_BOOK, JsonlGzipWriter, RecordEnvelope
from pulsearb.settings import Settings

CLOB = "https://clob.polymarket.com"

#: Quantos snapshots de livro reter por token. O markout só precisa do livro
#: NO instante da execução e 1/5/30 s depois — não da série inteira.
LIMITE_SNAPSHOTS = 40_000

#: `duracao_s` sintética. Ver docstring: agrupa todos num recorte só, em vez
#: de criar um recorte por mercado.
DURACAO_SINTETICA_S = 3600

#: `fonte` do registro de catálogo na gravação de pools. Não é `poly_ws`
#: porque não veio do fio: é o que ESTE processo escolheu assinar, e quem lê
#: a gravação precisa saber quais tokens formam par.
FONTE_CATALOGO = "pools_snapshot"


@dataclass
class JanelaColetada:
    """O que `medir_markout` precisa, e nada além disso.

    Duck typing de propósito: construir um `WindowState` de verdade exigiria
    âncora, resolução e metadados de janela que não existem aqui — e inventá-los
    para satisfazer um construtor seria mentir para o tipo.
    """

    slug: str
    tick_size: float
    duracao_s: int
    close_ts_ns: int
    books: dict[str, BookTimeline] = field(default_factory=dict)
    trades: list[tuple[int, float, float, str]] = field(default_factory=list)


class Coletor:
    """Assina os tokens dos mercados com pool e guarda livro + execuções."""

    def __init__(
        self,
        tokens_por_mercado: dict[str, dict[str, Any]],
        *,
        writer: JsonlGzipWriter | None = None,
    ) -> None:
        self.mercados = tokens_por_mercado
        self.writer = writer
        self.dono: dict[str, str] = {}
        for cid, meta in tokens_por_mercado.items():
            for token in meta["tokens"]:
                self.dono[token] = cid
        self.book_atual: dict[str, OrderBook] = {}
        self.timelines: dict[str, BookTimeline] = {}
        self.trades: dict[str, list[tuple[int, float, float, str]]] = {}
        self.eventos = 0
        self.execucoes = 0

    def _timeline(self, token: str) -> BookTimeline:
        tl = self.timelines.get(token)
        if tl is None:
            tl = BookTimeline(limite=LIMITE_SNAPSHOTS, niveis=5)
            self.timelines[token] = tl
        return tl

    def on_event(self, feed_event: Any) -> None:
        """Recebe o `FeedEvent` da base e destrincha o payload.

        O carimbo é `ts_wall_ns` da CHEGADA, capturado pela base — o mesmo
        eixo que o recorder grava e que a passada 2 do backtest usa. Usar
        `time.time_ns()` aqui reintroduziria o atraso do nosso processamento
        dentro da medição de markout, que é medida em milissegundos.
        """
        agora = int(getattr(feed_event, "ts_wall_ns", 0)) or time.time_ns()
        payload = getattr(feed_event, "parsed", None)
        if payload is None:
            return
        if self.writer is not None:
            # O CRU, antes de qualquer filtro nosso: o que se grava é o que o
            # servidor disse, não o que este processo entendeu.
            self.writer.submit(
                RecordEnvelope(
                    ts_mono_ns=int(getattr(feed_event, "ts_mono_ns", 0)) or agora,
                    ts_wall_ns=agora,
                    fonte="poly_ws",
                    raw=getattr(feed_event, "raw", b"") or b"",
                ),
                canal=CANAL_BOOK,
            )
        for evento in eventos_do_payload(payload):
            tipo = evento.get("event_type")
            asset_id = evento.get("asset_id")
            if not isinstance(asset_id, str) or asset_id not in self.dono:
                continue
            self.eventos += 1
            if tipo == EVENT_LAST_TRADE:
                preco = _num(evento.get("price"))
                if preco is None:
                    continue
                self.execucoes += 1
                self.trades.setdefault(asset_id, []).append(
                    (
                        agora,
                        preco,
                        _num(evento.get("size")) or 0.0,
                        str(evento.get("side", "")).upper(),
                    )
                )
                continue
            if tipo == EVENT_BOOK:
                livro = OrderBook.from_event(evento)
                if livro is None:
                    continue
                self.book_atual[asset_id] = livro
            elif tipo == EVENT_PRICE_CHANGE:
                livro = self.book_atual.get(asset_id)
                if livro is None:
                    continue
                livro.apply_price_change(evento)
            else:
                continue
            self._timeline(asset_id).append(self.book_atual[asset_id], agora)

    def janelas(self) -> list[JanelaColetada]:
        saida: list[JanelaColetada] = []
        agora = time.time_ns()
        for cid, meta in self.mercados.items():
            janela = JanelaColetada(
                slug=str(meta.get("slug") or cid),
                tick_size=float(meta.get("tick_size") or 0.01),
                duracao_s=DURACAO_SINTETICA_S,
                close_ts_ns=agora,
            )
            for token in meta["tokens"]:
                tl = self.timelines.get(token)
                if tl is not None:
                    janela.books[token] = tl
                janela.trades.extend(self.trades.get(token, []))
            janela.trades.sort()
            if janela.books:
                saida.append(janela)
        return saida


def _num(valor: Any) -> float | None:
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def escolher_mercados(top: int) -> dict[str, dict[str, Any]]:
    """Os `top` mercados com maior pool, com tokens e tick do CLOB."""
    pools: list[dict[str, Any]] = []
    cursor = ""
    with httpx.Client(timeout=30.0) as http:
        while True:
            params: dict[str, Any] = {"sponsored": "false"}
            if cursor:
                params["next_cursor"] = cursor
            r = http.get(CLOB + "/rewards/markets/current", params=params)
            r.raise_for_status()
            pagina = r.json()
            dados = pagina.get("data") or []
            if not dados:
                break
            pools.extend(d for d in dados if isinstance(d, dict))
            cursor = str(pagina.get("next_cursor") or "")
            if not cursor or cursor == "LTE=":
                break

        pools.sort(key=lambda m: float(m.get("total_daily_rate") or 0), reverse=True)
        saida: dict[str, dict[str, Any]] = {}
        for pool in pools:
            if len(saida) >= top:
                break
            cid = str(pool.get("condition_id") or "")
            if not cid:
                continue
            r = http.get(CLOB + f"/markets/{cid}")
            if r.status_code != 200:
                continue
            mercado = r.json()
            if not mercado.get("accepting_orders") or mercado.get("closed"):
                continue
            tokens = [
                t.get("token_id")
                for t in (mercado.get("tokens") or [])
                if isinstance(t, dict) and isinstance(t.get("token_id"), str)
            ]
            if len(tokens) < 2:
                continue
            saida[cid] = {
                "tokens": tokens,
                "slug": mercado.get("market_slug"),
                "pergunta": mercado.get("question"),
                "tick_size": mercado.get("minimum_tick_size"),
                "daily_rate": float(pool.get("total_daily_rate") or 0),
            }
    return saida


def _envelope_do_catalogo(mercados: dict[str, dict[str, Any]]) -> RecordEnvelope:
    agora = time.time_ns()
    return RecordEnvelope(
        ts_mono_ns=time.monotonic_ns(),
        ts_wall_ns=agora,
        fonte=FONTE_CATALOGO,
        raw=json.dumps(
            {
                "gerado_em_ns": agora,
                "mercados": {
                    cid: {
                        "tokens": meta["tokens"],
                        "slug": meta.get("slug"),
                        "pergunta": meta.get("pergunta"),
                        "tick_size": meta.get("tick_size"),
                        "daily_rate": meta.get("daily_rate"),
                    }
                    for cid, meta in mercados.items()
                },
            }
        ).encode(),
    )


async def coletar(
    coletor: Coletor,
    settings: Settings,
    duracao_s: float,
    *,
    writer: JsonlGzipWriter | None = None,
) -> None:
    tokens = sorted(coletor.dono)
    feed = PolyMarketWsFeed(
        url=settings.endpoints.clob_market_ws,
        user_agent=settings.user_agent,
        token_ids=tokens,
        on_event=coletor.on_event,
    )
    if writer is not None:
        await writer.start()
        # O catálogo ANTES do primeiro evento: quem lê a gravação precisa do
        # par YES/NO para o primeiro book que chegar.
        writer.submit(_envelope_do_catalogo(coletor.mercados))
    await feed.start()
    fim = time.monotonic() + duracao_s
    try:
        while time.monotonic() < fim:
            await asyncio.sleep(min(30.0, max(1.0, fim - time.monotonic())))
            print(
                f"  {coletor.eventos:>10,} eventos | "
                f"{coletor.execucoes:>7,} execuções | "
                f"{int(max(0, fim - time.monotonic())):>6}s restantes",
                file=sys.stderr,
            )
    finally:
        await feed.stop()
        if writer is not None:
            await writer.stop()


def parse_horizontes(bruto: str) -> tuple[float, ...]:
    """`"1,5,30,300,1800"` → `(1.0, 5.0, 30.0, 300.0, 1800.0)`. Vazio ou
    não numérico recusa — um horizonte inventado aqui viraria coluna vazia
    com cara de medida."""
    partes = [p.strip() for p in bruto.split(",") if p.strip()]
    if not partes:
        raise ValueError("--horizontes vazio; esperado lista como 1,5,30,300,1800")
    try:
        valores = tuple(float(p) for p in partes)
    except ValueError:
        raise ValueError(f"--horizontes inválido: {bruto!r}") from None
    if any(v <= 0 for v in valores):
        raise ValueError(f"--horizontes precisa ser positivo: {bruto!r}")
    return valores


def parse_duracao(bruto: str) -> float:
    unidades = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if bruto and bruto[-1] in unidades:
        return float(bruto[:-1]) * unidades[bruto[-1]]
    return float(bruto)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="markout_dos_pools")
    parser.add_argument("--top", type=int, default=60)
    parser.add_argument("--duracao", default="4h")
    parser.add_argument("--json", default=None)
    parser.add_argument("--config", default="config.yaml")
    # 300 e 1800 s não são markout de seleção adversa: são a proxy do custo
    # até conseguir SAIR de um inventário unilateral num mercado que resolve
    # em dias. É o termo que o 1.12 não media (quadro, 2026-09-14).
    parser.add_argument("--horizontes", default="1,5,30,300,1800")
    parser.add_argument(
        "--gravar",
        default=None,
        help=(
            "diretório onde gravar os eventos crus (formato do recorder), "
            "para replay posterior por scripts/maker_de_pares_nos_pools.py"
        ),
    )
    args = parser.parse_args(argv)

    try:
        horizontes = parse_horizontes(args.horizontes)
    except ValueError as erro:
        print(str(erro), file=sys.stderr)
        return 2

    try:
        destino = caminho_de_escrita(args.json) if args.json else None
    except ValueError as erro:
        print(str(erro), file=sys.stderr)
        return 2

    settings = Settings.load(args.config)
    print(f"escolhendo os {args.top} mercados de maior pool…", file=sys.stderr)
    mercados = escolher_mercados(max(1, args.top))
    if not mercados:
        print("nenhum mercado com pool aceitando ordens", file=sys.stderr)
        return 1

    writer = (
        JsonlGzipWriter(output_dir=args.gravar, prefix="pools")
        if args.gravar
        else None
    )
    coletor = Coletor(mercados, writer=writer)
    pool_total = sum(m["daily_rate"] for m in mercados.values())
    print(
        f"{len(mercados)} mercados, {len(coletor.dono)} tokens, "
        f"{pool_total:.0f} USDC/dia de pool. Coletando por {args.duracao}…",
        file=sys.stderr,
    )

    duracao_s = parse_duracao(args.duracao)
    asyncio.run(coletar(coletor, settings, duracao_s, writer=writer))
    if writer is not None:
        print(
            f"gravação: {writer.written:,} registros em {args.gravar} "
            f"({writer.dropped:,} descartados)",
            file=sys.stderr,
        )

    janelas = coletor.janelas()
    # Recorte por mercado LIGADO: aqui a janela é o mercado, e a conta do
    # maker precisa de custo de saída e execuções/hora POR mercado.
    markout = medir_markout(janelas, horizontes_s=horizontes, recorte_por_janela=True)
    relatorio = {
        "regime": {
            "mercados": len(mercados),
            "tokens": len(coletor.dono),
            "pool_total_usdc_por_dia": round(pool_total, 2),
            "duracao_da_coleta": args.duracao,
            "horas_de_coleta": round(duracao_s / 3600.0, 4),
            "horizontes_s": list(horizontes),
            "eventos": coletor.eventos,
            "execucoes_vistas": coletor.execucoes,
            "nota": (
                "Mercados de HORIZONTE LONGO com pool de reward. É o regime "
                "que o 1.12 exige e que a medição de 5 min de cripto não "
                "cobre."
            ),
        },
        "mercados": [
            {
                "pergunta": m.get("pergunta"),
                "slug": m.get("slug"),
                "daily_rate_usdc": m["daily_rate"],
            }
            for m in sorted(
                mercados.values(), key=lambda m: -m["daily_rate"]
            )[:40]
        ],
        "markout": markout,
        "leitura": (
            "Markout NEGATIVO = fomos atropelados; é o custo que o reward "
            "precisa superar. Compare `markout_centavos_por_share.total.5s."
            "media` com a receita por share da varredura de pools — se o "
            "custo for da mesma ordem, a rota não fecha aqui tampouco, e "
            "desta vez o número é DESTE regime."
        ),
    }

    texto = json.dumps(relatorio, indent=2, ensure_ascii=False)
    if destino is not None:
        destino.write_text(texto, encoding="utf-8")
        print(f"\nrelatório gravado em {destino}")
    else:
        print(texto)

    total = markout["markout_centavos_por_share"].get("total", {})
    print("\nmarkout (centavos por share, quem forneceu liquidez):")
    for horizonte, dist in total.items():
        if isinstance(dist, dict):
            print(
                f"  {horizonte:>4}: media={dist.get('media')} "
                f"p50={dist.get('p50')} n={dist.get('n')}"
            )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
