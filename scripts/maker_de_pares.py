"""Maker de PARES sobre a gravação: bid nos dois lados, nunca vender.

Por que este script existe
--------------------------

O estudo dos bots públicos (`docs/OUTROS_BOTS.md`) apontou uma forma que se
repete em quem ganha dinheiro de verdade nos binários de cripto: bid em Up E
em Down, lote pequeno, nunca vender — o par que executa dos dois lados vale
1,00 na liquidação (ou na fusão via CTF), e o lucro é `1 − (pUp + pDown)`
mais o rebate de maker. O 4.2 deste projeto mede cada perna sozinha, pelo
markout de 5 s, e o markout de uma perna de maker É negativo por construção:
o taker que nos pega sabe algo. O que ele não mede é a economia do PAR, que
é onde o `0x8dxd` e companhia ganham.

Este script mede exatamente isso, sobre a gravação real, com o MESMO modelo
de execução do `CaixaDoMaker` (prints `last_trade_price` com lado SELL do
taker a preço ≤ o nosso bid; "atravessada" se passou abaixo, "no nível" se
parou no nosso preço — aí entra a fila).

O que ele simula, por janela Up/Down
------------------------------------

Para cada janela, desde a abertura até `fim − parar_antes_do_fim_s`:

1. Um bid de `tamanho` shares em cada token, no `best_bid − melhorar_ticks`
   (0 = JUNTAR ao melhor bid, nunca melhorar — regra do poly-maker; 1 = um
   tick atrás). A ordem é sintética: não existe na gravação, então o tamanho
   do nível que a gravação mostra é tudo o que está NA FRENTE dela.
2. A ordem fica parada até (a) executar, (b) o melhor bid subir mais que
   `reprice_ticks` — aí cancela e junta de novo, perdendo a fila —, ou
   (c) acabar o tempo de cotação. Se o melhor bid CAI abaixo do nosso preço,
   a ordem continua lá: é isso que uma ordem real faz, e é exatamente aí que
   a seleção adversa mora.
3. Execução pelos prints, em dois modos que cercam a verdade:
   - `pessimista`: a fila à frente só diminui com prints SELL no nosso preço;
     cancelamento dos outros não conta a nosso favor.
   - `otimista`: a fila à frente é no máximo o tamanho do nível que a
     gravação mostra agora (cancelamentos à frente contam).
   Print abaixo do nosso preço executa, conforme `atravessada`, a perna
   inteira (o que o `CaixaDoMaker` conta) ou no máximo o tamanho do print.
4. Cada perna executa no máximo `tamanho` shares por janela (um lote).
5. Resultado da janela: `pares = min(qUp, qDown)` travam
   `pares × (1 − pUp − pDown)`; o que sobrou de uma perna só vai à
   resolução (`+ (1 − p) × q` se ganhou, `− p × q` se perdeu). Janela sem
   resolução na gravação com perna solta fica fora do P&L e é contada.
6. Rebate: `0,2 × rate × p(1−p) × shares` por execução — é o TETO do
   programa de maker rebates (§12.6), pago pro rata do pool diário. O
   relatório traz o total com e sem ele.

O que NÃO está aqui, e fica dito: o matching Up×Down do CLOB (§4) — uma
compra de Up pode consumir bid de Down sem print no token do Down —, o que
faz destas execuções um LIMITE INFERIOR; e o reward de liquidez (1.12), que
é dinheiro à parte e já tem conta própria.

Uso
---

    python scripts/maker_de_pares.py ~/pulsearb-gravacao --desde ... --ate ... \\
        --json relatorios/PARES_20260913.json
"""

from __future__ import annotations

import argparse
import json
import sys
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import product
from typing import Any

from pulsearb.backtest.__main__ import RecordingIndex, caminho_de_leitura
from pulsearb.backtest.book import OrderBook
from pulsearb.caminhos import caminho_de_escrita
from pulsearb.engine.fees import fee_pp_por_share
from pulsearb.feeds.poly_ws import (
    EVENT_BOOK,
    EVENT_LAST_TRADE,
    EVENT_PRICE_CHANGE,
    eventos_do_payload,
    iter_mudancas,
)
from pulsearb.feeds.rtds import TOPIC_BINANCE, parse_rtds_event
from pulsearb.markets.discovery import duracao_do_slug, parse_end_date_epoch
from pulsearb.replay.reader import RecordingReader, ReplayRecord

EPS = 1e-9
#: Fração do fee do taker que volta ao maker (API_NOTES §12.6, `rebateRate`).
REBATE_DO_MAKER = 0.2
TAMANHO_PADRAO = 20.0
REPRICE_TICKS_PADRAO = 2
MODOS = ("pessimista",)
MELHORAR_TICKS = (0,)
PARAR_ANTES_S = (30, 180)
#: Latência entre ver o livro andar contra a ordem e o cancelamento valer no
#: servidor (e entre mandar a ordem e ela existir no livro). `None` = nunca
#: recolhe: a ordem fica parada, como na primeira medição.
LATENCIAS_DE_RECOLHER_MS: tuple[float | None, ...] = (None, 100.0)
MARGEM_DO_PAR = 0.01
#: Salto do spot (RTDS `crypto_prices`, o Binance repassado) que recolhe a
#: cotação — o regime EVENT do poly-maker, com o gatilho no ativo, não no
#: livro: o taker que nos varre reage ao spot; o livro andar já é tarde.
SALTOS_BPS: tuple[float | None, ...] = (None, 3.0, 8.0)
HORIZONTE_DO_SALTO_S = 2.0
COOLOFF_DO_SALTO_S = 5.0
#: Colchão: só descansar quando há ≥ K × o nosso tamanho NA FRENTE no nível
#: (RuneDn), e recolher quando o colchão some. 0 desliga.
COLCHOES: tuple[float, ...] = (0.0, 10.0)
#: Quanto executa um print que passou ABAIXO do nosso preço. `perna_inteira`
#: é o que o `CaixaDoMaker` conta (§4.2) — o taker desceu o livro, logo levou
#: tudo o que estava no caminho. `tamanho_do_print` é o teto de verdade: não
#: se pode comprar mais shares do que foram negociadas. A diferença entre os
#: dois é o quanto do veredito vem da hipótese, e não do mercado.
ATRAVESSADAS = ("perna_inteira", "tamanho_do_print")


def _numero(valor: Any) -> float | None:
    if isinstance(valor, bool) or valor is None:
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    if isinstance(valor, str):
        try:
            return float(valor)
        except ValueError:
            return None
    return None

def _tamanho_no_nivel(book: OrderBook, preco: float) -> float:
    for p, s in book.bids:
        if abs(p - preco) < EPS:
            return s
    return 0.0


@dataclass(frozen=True)
class Estrategia:
    melhorar_ticks: int
    modo: str
    parar_antes_s: int
    #: Recolhe a ordem quando o melhor bid cai abaixo dela (o nível à frente
    #: sumiu — é o momento em que ela vira opção grátis para o taker). A
    #: latência é o tempo até o cancelamento valer; `None` não recolhe.
    recolher_ms: float | None
    #: Trava do par: com uma perna executada a `p`, a outra só descansa a
    #: `≤ 1 − p − margem`, para que o par feche com lucro por construção.
    trava_do_par: bool
    salto_bps: float | None = None
    colchao_x: float = 0.0
    atravessada: str = "perna_inteira"

    @property
    def nome(self) -> str:
        recolhe = "fica" if self.recolher_ms is None else f"recolhe-{int(self.recolher_ms)}ms"
        trava = "trava" if self.trava_do_par else "livre"
        salto = "salto-off" if self.salto_bps is None else f"salto-{self.salto_bps:g}bps"
        return (
            f"junta-{self.melhorar_ticks}t_{self.modo}_para-{self.parar_antes_s}s_"
            f"{recolhe}_{trava}_{salto}_colchao-{self.colchao_x:g}x_"
        )


@dataclass
class Ordem:
    preco: float
    restante: float
    fila_a_frente: float
    colocada_ns: int
    #: A ordem só existe no livro depois da latência de envio.
    ativa_desde_ns: int
    #: Cancelamento já mandado; vale a partir deste instante.
    cancela_em_ns: int | None = None


@dataclass
class Perna:
    """Uma perna (token) de uma janela, para UMA estratégia."""

    ordem: Ordem | None = None
    executado: float = 0.0
    custo: float = 0.0
    rebate: float = 0.0
    recolocacoes: int = 0
    execucoes_atravessadas: int = 0
    execucoes_no_nivel: int = 0
    primeira_execucao_ns: int = 0
    cancelada_no_fim: bool = False
    recolhidas: int = 0
    recolhidas_por_salto: int = 0
    recolhidas_por_colchao: int = 0
    execucoes_durante_o_recolher: int = 0

    @property
    def preco_medio(self) -> float:
        return self.custo / self.executado if self.executado > 0 else 0.0


@dataclass
class Janela:
    """Um mercado binário com os dois tokens complementares.

    `token_up`/`token_down` são os nomes do caso que nasceu primeiro (as
    janelas Up/Down de cripto). Num mercado de pool é YES/NO — a aritmética
    do par é a mesma: os dois somam 1,00 na liquidação.
    """

    slug: str
    asset: str
    token_up: str
    token_down: str
    abertura_ns: int
    fim_ns: int
    tick: float
    rate: float
    exponent: float
    pernas: dict[tuple[Estrategia, str], Perna] = field(default_factory=dict)


class MakerDePares(RecordingIndex):
    """`RecordingIndex` que, na passada 2, cota pares em vez de guardar livro.

    Como o `IndiceDaSoma`, NÃO chama `_timeline`: memória constante por token,
    o que deixa varrer um dia inteiro numa máquina de 8 GB.
    """

    def __init__(
        self,
        reader: RecordingReader,
        *,
        tamanho: float,
        reprice_ticks: int,
        estrategias: tuple[Estrategia, ...],
    ) -> None:
        super().__init__(reader)
        self.tamanho = tamanho
        self.reprice_ticks = reprice_ticks
        self.estrategias = estrategias
        self._usa_salto = any(e.salto_bps is not None for e in estrategias)
        self.janela_do_token: dict[str, Janela] = {}
        self.janelas_cotadas: dict[str, Janela] = {}
        # Spot por ativo (passada 1), em duas listas paralelas para o bisect.
        self.spot_ts: dict[str, list[int]] = defaultdict(list)
        self.spot_px: dict[str, list[float]] = defaultdict(list)
        self.bloqueado_ate_ns: dict[tuple[Estrategia, str], int] = {}
        self.prints_vistos = 0
        self.prints_sell = 0
        self.eventos_sem_bid = 0

    # --------------------------------------------------------------- passada 1
    def _on_rtds(self, record: ReplayRecord) -> None:
        super()._on_rtds(record)
        tick = parse_rtds_event(record.payload, record.ts_mono_ns, record.ts_wall_ns)
        if tick is not None and tick.topic == TOPIC_BINANCE and tick.price > 0:
            self.spot_ts[tick.asset].append(record.ts_wall_ns)
            self.spot_px[tick.asset].append(tick.price)

    def _salto_bps(self, asset: str, ts_ns: int) -> float:
        """Quanto o spot andou (em bps, absoluto) nos últimos HORIZONTE_DO_SALTO_S."""
        ts = self.spot_ts.get(asset)
        if not ts:
            return 0.0
        agora = bisect_right(ts, ts_ns) - 1
        if agora < 0:
            return 0.0
        antes = bisect_left(ts, ts_ns - int(HORIZONTE_DO_SALTO_S * 1e9))
        antes = min(antes, agora)
        px = self.spot_px[asset]
        # O maior desvio dentro do horizonte, não só ponta a ponta: um
        # vai-e-volta também varre o livro.
        base = px[antes]
        pior = 0.0
        for i in range(antes, agora + 1):
            desvio = abs(px[i] - base) / base * 1e4
            if desvio > pior:
                pior = desvio
        return pior

    def _marcar_tokens_de_interesse(self) -> None:
        super()._marcar_tokens_de_interesse()
        for slug, meta in self.janelas_por_slug.items():
            tokens = meta.get("token_id_by_outcome") or {}
            up, down = tokens.get("Up"), tokens.get("Down")
            if not isinstance(up, str) or not isinstance(down, str):
                continue
            fim_epoch = parse_end_date_epoch({"endDate": meta.get("end_date_iso")})
            if fim_epoch is None:
                continue
            janela = Janela(
                slug=slug,
                asset=str(meta.get("asset") or "").lower(),
                token_up=up,
                token_down=down,
                abertura_ns=int((fim_epoch - duracao_do_slug(slug)) * 1e9),
                fim_ns=int(fim_epoch * 1e9),
                tick=float(meta.get("tick_size") or 0.01),
                rate=float(meta.get("fee_rate") or 0.0),
                exponent=float(meta.get("fee_exponent") or 1.0),
            )
            self.janela_do_token[up] = self.janela_do_token[down] = janela
            self.janelas_cotadas[slug] = janela

    # --------------------------------------------------------------- passada 2
    def _on_poly_book(self, record: ReplayRecord) -> None:
        ts_ns = record.ts_wall_ns
        for evento in eventos_do_payload(record.payload):
            tipo = evento.get("event_type")
            if tipo == EVENT_LAST_TRADE:
                self._on_print(evento, ts_ns)
                continue
            if tipo == EVENT_BOOK:
                asset_id = evento.get("asset_id")
                if not isinstance(asset_id, str) or asset_id not in self.janela_do_token:
                    continue
                book = OrderBook.from_event(evento)
                if book is None:
                    continue
                self.book_atual[asset_id] = book
                self._cotar(asset_id, ts_ns)
            elif tipo == EVENT_PRICE_CHANGE:
                # O `price_change` pode vir na forma B (§6.1b), com vários
                # tokens no mesmo evento — roteia por token, não pelo topo.
                tocados: set[str] = set()
                topo = evento.get("asset_id")
                if isinstance(topo, str):
                    tocados.add(topo)
                for mudanca in iter_mudancas(evento):
                    if isinstance(mudanca.asset_id, str):
                        tocados.add(mudanca.asset_id)
                for asset_id in tocados:
                    if asset_id not in self.janela_do_token:
                        continue
                    book = self.book_atual.get(asset_id)
                    if book is None:
                        continue
                    book.apply_price_change(evento)
                    self._cotar(asset_id, ts_ns)

    # ------------------------------------------------------------ cotação
    def _cotar(self, token: str, ts_ns: int) -> None:
        janela = self.janela_do_token[token]
        if ts_ns < janela.abertura_ns:
            return
        book = self.book_atual.get(token)
        if book is None:
            return
        outro_token = janela.token_down if token == janela.token_up else janela.token_up
        salto = self._salto_bps(janela.asset, ts_ns) if self._usa_salto else 0.0
        for estrategia in self.estrategias:
            perna = janela.pernas.setdefault((estrategia, token), Perna())
            fim_cotacao_ns = janela.fim_ns - estrategia.parar_antes_s * 10**9
            if ts_ns >= fim_cotacao_ns:
                if perna.ordem is not None:
                    perna.ordem = None
                    perna.cancelada_no_fim = True
                continue
                continue
            ordem = perna.ordem
            if ordem is not None and ordem.cancela_em_ns is not None and ts_ns >= ordem.cancela_em_ns:
                perna.ordem = ordem = None
            if estrategia.salto_bps is not None:
                chave = (estrategia, janela.asset)
                if salto >= estrategia.salto_bps:
                    self.bloqueado_ate_ns[chave] = ts_ns + int(COOLOFF_DO_SALTO_S * 1e9)
                if ts_ns < self.bloqueado_ate_ns.get(chave, 0):
                    if ordem is not None and ordem.cancela_em_ns is None:
                        ordem.cancela_em_ns = ts_ns + int((estrategia.recolher_ms or 0.0) * 1e6)
                        perna.recolhidas += 1
                        perna.recolhidas_por_salto += 1
                    continue
            if not book.bids:
                self.eventos_sem_bid += 1
                if ordem is not None and estrategia.recolher_ms is not None and ordem.cancela_em_ns is None:
                    ordem.cancela_em_ns = ts_ns + int(estrategia.recolher_ms * 1e6)
                    perna.recolhidas += 1
                continue
            melhor, tamanho_no_topo = book.bids[0]
            alvo = melhor - estrategia.melhorar_ticks * janela.tick
            teto = 1.0
            if estrategia.trava_do_par:
                outra = janela.pernas.get((estrategia, outro_token))
                if outra is not None and outra.executado > EPS:
                    teto = 1.0 - outra.preco_medio - MARGEM_DO_PAR
                    alvo = min(alvo, teto)
            if ordem is not None:
                if ordem.cancela_em_ns is not None:
                    continue
                if melhor - ordem.preco > self.reprice_ticks * janela.tick + EPS:
                    # Melhor bid subiu demais: cancela e junta de novo (perde a fila).
                    perna.recolocacoes += 1
                    perna.ordem = ordem = None
                ):
                    ordem.cancela_em_ns = ts_ns + int((estrategia.recolher_ms or 0.0) * 1e6)
                    perna.recolhidas += 1
                    perna.recolhidas_por_colchao += 1
                    continue
                elif estrategia.modo == "otimista":
                    ordem.fila_a_frente = min(
                        ordem.fila_a_frente, _tamanho_no_nivel(book, ordem.preco)
                    )
            if ordem is None:
                if alvo <= EPS or alvo >= 1.0 - EPS:
                    continue
                fila = (
                    tamanho_no_topo
                    if abs(alvo - melhor) < EPS
                    else _tamanho_no_nivel(book, alvo)
                )
                    continue
                envio_ns = 0 if estrategia.recolher_ms is None else int(estrategia.recolher_ms * 1e6)
                perna.ordem = Ordem(
                    preco=alvo,
                    fila_a_frente=fila,
                    colocada_ns=ts_ns,
                    ativa_desde_ns=ts_ns + envio_ns,
                )

    # ------------------------------------------------------------ execução
    def _on_print(self, evento: dict[str, Any], ts_ns: int) -> None:
        token = evento.get("asset_id")
        if not isinstance(token, str) or token not in self.janela_do_token:
            return
        self.prints_vistos += 1
        if str(evento.get("side", "")).upper() != "SELL":
            return
        self.prints_sell += 1
        preco = _numero(evento.get("price"))
        tamanho = _numero(evento.get("size")) or 0.0
        if preco is None or tamanho <= 0:
            return
        janela = self.janela_do_token[token]
        for estrategia in self.estrategias:
            perna = janela.pernas.get((estrategia, token))
            if perna is None or perna.ordem is None:
                continue
            ordem = perna.ordem
            if ts_ns < ordem.ativa_desde_ns:
                continue
            if ordem.cancela_em_ns is not None and ts_ns >= ordem.cancela_em_ns:
                perna.ordem = None
                continue
            if preco > ordem.preco + EPS:
                continue
            if ordem.cancela_em_ns is not None:
                perna.execucoes_durante_o_recolher += 1
            if preco < ordem.preco - EPS:
                shares = (
                    ordem.restante
                    if estrategia.atravessada == "perna_inteira"
                    else min(tamanho, ordem.restante)
                )
                perna.execucoes_atravessadas += 1
            else:
                sobra = tamanho - ordem.fila_a_frente
                ordem.fila_a_frente = max(0.0, ordem.fila_a_frente - tamanho)
                if sobra <= EPS:
                    continue
                shares = min(sobra, ordem.restante)
                perna.execucoes_no_nivel += 1
            self._executar(janela, perna, shares, ts_ns)

    def _executar(self, janela: Janela, perna: Perna, shares: float, ts_ns: int) -> None:
        ordem = perna.ordem
        assert ordem is not None
        perna.executado += shares
        perna.custo += shares * ordem.preco
        if janela.rate > 0:
            perna.rebate += (
                REBATE_DO_MAKER
                * fee_pp_por_share(ordem.preco, rate=janela.rate, exponent=janela.exponent)
                * shares
            )
        if perna.primeira_execucao_ns == 0:
            perna.primeira_execucao_ns = ts_ns
        ordem.restante -= shares
        if ordem.restante <= EPS:
            perna.ordem = None

    # ------------------------------------------------------------ relatório
    def _marcar_perna_solta(
        self, janela: Janela, *, sobra_up: float, sobra_down: float, p_up: float, p_down: float
    ) -> tuple[float | None, bool | None]:
        """Quanto vale a perna que ficou sozinha, e se ela ganhou.

        No caso Up/Down a gravação contém a RESOLUÇÃO: a perna solta vale 1
        se o lado venceu e 0 se perdeu, e não há o que estimar. Quem herda
        para um regime que resolve em dias (os pools do 1.12) sobrescreve —
        lá a perna solta tem de ser marcada a preço de saída, não a resultado.
        """
        resolucao = self.resolucoes_por_token.get(
            janela.token_up
        ) or self.resolucoes_por_token.get(janela.token_down)
        venceu_up = (
            resolucao.venceu_up(janela.token_up, janela.token_down)
            if resolucao is not None
            else None
        )
        if venceu_up is None:
            return None, None
        residual = sobra_up * ((1.0 - p_up) if venceu_up else -p_up) + sobra_down * (
            (1.0 - p_down) if not venceu_up else -p_down
        )
        return residual, venceu_up

    def _resultado_da_janela(self, janela: Janela, estrategia: Estrategia) -> dict[str, Any]:
        up = janela.pernas.get((estrategia, janela.token_up), Perna())
        down = janela.pernas.get((estrategia, janela.token_down), Perna())
        pares = min(up.executado, down.executado)
        travado = pares * (1.0 - up.preco_medio - down.preco_medio) if pares > 0 else 0.0
        sobra_up = up.executado - pares
        sobra_down = down.executado - pares
        residual: float | None = 0.0
        venceu_up: bool | None = None
        if sobra_up > EPS or sobra_down > EPS:
            residual, venceu_up = self._marcar_perna_solta(
                janela,
                sobra_up=sobra_up,
                sobra_down=sobra_down,
                p_up=up.preco_medio,
                p_down=down.preco_medio,
            )
        capital = up.custo + down.custo
        return {
            "slug": janela.slug,
            "asset": janela.asset,
            "duracao_s": duracao_do_slug(janela.slug),
            "q_up": up.executado,
            "q_down": down.executado,
            "p_up": round(up.preco_medio, 4),
            "p_down": round(down.preco_medio, 4),
            "pares": pares,
            "travado": travado,
            "sobra_up": sobra_up,
            "sobra_down": sobra_down,
            "venceu_up": venceu_up,
            "residual": residual,
            "rebate": up.rebate + down.rebate,
            "capital": capital,
            "recolocacoes": up.recolocacoes + down.recolocacoes,
            "recolhidas": up.recolhidas + down.recolhidas,
            "recolhidas_por_salto": up.recolhidas_por_salto + down.recolhidas_por_salto,
            "recolhidas_por_colchao": up.recolhidas_por_colchao + down.recolhidas_por_colchao,
            "execucoes_durante_o_recolher": up.execucoes_durante_o_recolher
            + down.execucoes_durante_o_recolher,
            "atravessadas": up.execucoes_atravessadas + down.execucoes_atravessadas,
            "no_nivel": up.execucoes_no_nivel + down.execucoes_no_nivel,
        }

    def _resumo_da_estrategia(self, estrategia: Estrategia, *, detalhe: bool) -> dict[str, Any]:
        linhas = [self._resultado_da_janela(j, estrategia) for j in self.janelas_cotadas.values()]
        com_fill = [r for r in linhas if r["q_up"] > EPS or r["q_down"] > EPS]
        com_par = [r for r in com_fill if r["pares"] > EPS]
        so_uma = [r for r in com_fill if r["pares"] <= EPS]
        sem_resolucao = [r for r in com_fill if r["residual"] is None]
        contadas = [r for r in com_fill if r["residual"] is not None]

        travado = sum(r["travado"] for r in contadas)
        residual = sum(r["residual"] for r in contadas)
        rebate = sum(r["rebate"] for r in contadas)
        capital = sum(r["capital"] for r in contadas)
        pares = sum(r["pares"] for r in contadas)
        shares = sum(r["q_up"] + r["q_down"] for r in contadas)
        soma_dos_pares = [r["p_up"] + r["p_down"] for r in com_par]

        # Seleção adversa, medida onde ela mora: a perna que ficou sozinha.
        sozinhas_up = [r for r in contadas if r["sobra_up"] > EPS]
        sozinhas_down = [r for r in contadas if r["sobra_down"] > EPS]
        ganhou_up = sum(1 for r in sozinhas_up if r["venceu_up"])
        ganhou_down = sum(1 for r in sozinhas_down if r["venceu_up"] is False)
        preco_medio_sozinha = (
            sum(r["p_up"] for r in sozinhas_up) + sum(r["p_down"] for r in sozinhas_down)
        ) / max(1, len(sozinhas_up) + len(sozinhas_down))

        resumo: dict[str, Any] = {
            "estrategia": {
                "melhorar_ticks": estrategia.melhorar_ticks,
                "modo": estrategia.modo,
                "parar_antes_do_fim_s": estrategia.parar_antes_s,
                "recolher_ms": estrategia.recolher_ms,
                "trava_do_par": estrategia.trava_do_par,
                "salto_bps": estrategia.salto_bps,
                "colchao_x": estrategia.colchao_x,
                "atravessada": estrategia.atravessada,
            },
            "janelas": {
                "cotadas": len(linhas),
                "com_execucao": len(com_fill),
                "com_par": len(com_par),
                "so_uma_perna": len(so_uma),
                "sem_resolucao_excluidas": len(sem_resolucao),
                "contadas_no_pnl": len(contadas),
            },
            "shares_executadas": round(shares, 2),
            "pares_fechados": round(pares, 2),
            "capital_empregado_usdc": round(capital, 2),
            "pnl_usdc": {
                "travado_nos_pares": round(travado, 4),
                "residual_das_pernas_soltas": round(residual, 4),
                "rebate_teto": round(rebate, 4),
                "total_sem_rebate": round(travado + residual, 4),
                "total_com_rebate": round(travado + residual + rebate, 4),
            },
            "por_janela_contada_cents": round(
                100 * (travado + residual + rebate) / max(1, len(contadas)), 3
            ),
            "por_share_cents": round(100 * (travado + residual + rebate) / max(EPS, shares), 4),
            "soma_pup_pdown_nos_pares": {
                "media": round(sum(soma_dos_pares) / len(soma_dos_pares), 4)
                if soma_dos_pares
                else None,
                "min": round(min(soma_dos_pares), 4) if soma_dos_pares else None,
                "max": round(max(soma_dos_pares), 4) if soma_dos_pares else None,
            },
            "pernas_soltas": {
                "n": len(sozinhas_up) + len(sozinhas_down),
                "ganharam": ganhou_up + ganhou_down,
                "fracao_que_ganhou": round(
                    (ganhou_up + ganhou_down) / max(1, len(sozinhas_up) + len(sozinhas_down)), 4
                ),
                "preco_medio_pago": round(preco_medio_sozinha, 4),
                "leitura": (
                    "fração que ganhou < preço médio pago = seleção adversa "
                    "(o taker que nos pegou sabia mais)"
                ),
            },
            "recolocacoes": sum(r["recolocacoes"] for r in linhas),
            "recolhidas": sum(r["recolhidas"] for r in linhas),
            "recolhidas_por_salto": sum(r["recolhidas_por_salto"] for r in linhas),
            "recolhidas_por_colchao": sum(r["recolhidas_por_colchao"] for r in linhas),
            "por_duracao": _quebra(contadas, "duracao_s"),
            "por_ativo": _quebra(contadas, "asset"),
            "execucoes": {
                "atravessadas": sum(r["atravessadas"] for r in linhas),
                "no_nivel": sum(r["no_nivel"] for r in linhas),
                "durante_o_recolher": sum(r["execucoes_durante_o_recolher"] for r in linhas),
            },
        }
        if detalhe:
            resumo["janelas_detalhe"] = [
                {k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()}
                for r in com_fill
            ]
        return resumo

    def relatorio(self, *, detalhe: bool = False) -> dict[str, Any]:
        primeiro = self._primeiro_record_ns
        ultimo = self._ultimo_record_ns

        def _iso(ns: int) -> str | None:
            return datetime.fromtimestamp(ns / 1e9, UTC).isoformat() if ns else None

        duracoes = Counter(duracao_do_slug(s) for s in self.janelas_cotadas)
        return {
            "gerado_em": datetime.now(UTC).isoformat(),
            "gravacao": {
                "arquivos": len(self.reader.files),
                "de": _iso(primeiro),
                "ate": _iso(ultimo),
            },
            "parametros": {
                "tamanho_shares_por_perna": self.tamanho,
                "reprice_ticks": self.reprice_ticks,
                "rebate_do_maker": REBATE_DO_MAKER,
            },
            "janelas_por_duracao_s": dict(sorted(duracoes.items())),
            "prints": {"vistos": self.prints_vistos, "sell": self.prints_sell},
            "eventos_sem_bid": self.eventos_sem_bid,
            "resolucoes": {
                "eventos": self.eventos_de_resolucao,
                "tokens_resolvidos": len(self.resolucoes_por_token),
            },
            "estrategias": [
                self._resumo_da_estrategia(e, detalhe=detalhe) for e in self.estrategias
            ],
        }


def _quebra(linhas: list[dict[str, Any]], chave: str) -> dict[str, Any]:
    grupos: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for r in linhas:
        grupos[r[chave]].append(r)
    saida: dict[str, Any] = {}
    for valor, grupo in sorted(grupos.items(), key=lambda kv: str(kv[0])):
        total = sum(r["travado"] + r["residual"] + r["rebate"] for r in grupo)
        saida[str(valor)] = {
            "janelas_contadas": len(grupo),
            "com_par": sum(1 for r in grupo if r["pares"] > EPS),
            "pnl_com_rebate": round(total, 4),
            "por_janela_cents": round(100 * total / len(grupo), 3),
        }
    return saida
def estrategias_padrao() -> tuple[Estrategia, ...]:
    base = [
        Estrategia(
            melhorar_ticks=0,
            modo="pessimista",
            parar_antes_s=parar,
            recolher_ms=None,
            trava_do_par=False,
        )
        for parar in PARAR_ANTES_S
    ]
    grade = [
        Estrategia(
            melhorar_ticks=0,
            modo="pessimista",
            parar_antes_s=parar,
            recolher_ms=100.0,
            trava_do_par=trava,
            salto_bps=salto,
            colchao_x=colchao,
            atravessada=atravessada,
        )
        for parar, trava, salto, colchao, atravessada in product(
            PARAR_ANTES_S, (False, True), SALTOS_BPS, COLCHOES, ATRAVESSADAS
        )
    ]
    return tuple(base + grade)


def _parse_dt(texto: str | None) -> datetime | None:
    if texto is None:
        return None
    dt = datetime.fromisoformat(texto)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("recordings")
    parser.add_argument("--json", default=None)
    parser.add_argument("--desde", default=None)
    parser.add_argument("--ate", default=None)
    parser.add_argument("--tamanho", type=float, default=TAMANHO_PADRAO)
    parser.add_argument("--reprice-ticks", type=int, default=REPRICE_TICKS_PADRAO)
    parser.add_argument("--detalhe", action="store_true")
    args = parser.parse_args(argv)

    try:
        caminho = caminho_de_leitura(args.recordings)
        destino = caminho_de_escrita(args.json) if args.json else None
        desde, ate = _parse_dt(args.desde), _parse_dt(args.ate)
    except ValueError as erro:
        print(str(erro), file=sys.stderr)
        return 2

    reader = RecordingReader(caminho, desde=desde, ate=ate)
    index = MakerDePares(
        reader,
        tamanho=args.tamanho,
        reprice_ticks=args.reprice_ticks,
    )
    index.build()

    if not index.janelas_cotadas:
        print("nenhuma janela Up/Down com metadados — nada a cotar", file=sys.stderr)
        return 1

    relatorio = index.relatorio(detalhe=args.detalhe)
    texto = json.dumps(relatorio, indent=2, ensure_ascii=False)
    if destino is not None:
        destino.write_text(texto, encoding="utf-8")
        print(f"\nrelatório gravado em {destino}")
    else:
        print(texto)

    print(
        est = e["estrategia"]
        recolhe = "fica" if est["recolher_ms"] is None else f"rec-{int(est['recolher_ms'])}ms"
        salto = "salto-off" if est["salto_bps"] is None else f"salto-{est['salto_bps']:g}"
        nome = (
            f"para-{est['parar_antes_do_fim_s']}s {recolhe:<9} "
            f"{'trava' if est['trava_do_par'] else 'livre'} {salto:<9} "
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
