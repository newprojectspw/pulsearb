"""Descoberta dos mercados que PAGAM reward. A rota que o 1.12 aprovou.

## Por que esta descoberta existe ao lado da outra, e não dentro dela

`markets/discovery.py` acha janelas Up/Down por **grade de slug**: ele monta
candidatos (`btc-updown-5m-<epoch>`), pergunta à Gamma e fica com os que
existem. É o certo para o jogo TWAP, onde o slug é previsível.

Os mercados de reward não têm slug previsível — são tênis, temperatura em
Shanghai, Emmy, spread da NFL. A única lista que os enumera é a do próprio
CLOB (`GET /rewards/markets/current`), e ela é indexada por `condition_id`.

Ensinar a grade de slugs a fazer isto mudaria o caminho que descobre as
janelas que o M2 inteiro mediu. Esta descoberta é separada de propósito.

## A diferença que importa: aqui não se prevê nada

A janela Up/Down carrega âncora, resolução e faixa de calibração porque o
taker precisa disso. **A rota maker não precisa de nada disso** — o
`laco_maker` usa `slug`, `token_up`, `seconds_left`, `tick_size` e os três
parâmetros de reward. Por isso a `JanelaAoVivo` sai montada aqui direto, em
vez de passar pelo `_converter` do rastreador, que exigiria `Up`/`Down` nos
outcomes e duração derivável do slug — dois requisitos que estes mercados não
cumprem e não deveriam precisar cumprir.

## A trava que impede o taker de tocar nestes mercados

`jogo=JOGO_REWARD` e o `MotorAoVivo` só opera `config.jogos_operados`, cujo
default é `{JOGO_TWAP}`. Uma janela destas é recusada com
`PULOU_JOGO_NAO_OPERADO` — motivo NOMEADO, não silêncio.

Isto não é conveniência: o taker apostaria direção usando um preditor cuja
âncora (`τ=0` sobre o TWAP do Chainlink, §13.8) **não existe** para "vai
chover em Wellington". Ele falharia fechado por desenho, e a trava está aqui
escrita para que ninguém a remova sem ler o motivo.

## O que ela NÃO faz

Não envia ordem. Não assina. É leitura de REST público, e o cliente de ordens
do SHADOW continua sendo o sombra.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from pulsearb.analysis.rewards import (
    OrdemHipotetica,
    ParametrosDeReward,
    score_da_ordem,
)
from pulsearb.backtest.book import OrderBook
from pulsearb.live.rastreador import JanelaAoVivo
from pulsearb.numeros import numero

#: O `jogo` destas janelas. Valor próprio, e não `twap`, porque é ele que o
#: `jogos_operados` do taker usa para recusar. Reaproveitar `twap` aqui faria
#: o taker aceitar apostar direção em mercado sem âncora.
JOGO_REWARD = "reward"

#: Motivos de descarte do filtro de livro. Contadores, como os do rastreador.
DESCARTE_SEM_LIVRO = "sem_livro"
DESCARTE_NAO_PONTUA = "nao_pontua_com_este_tamanho"

#: Quantos mercados considerar, do maior pool para o menor. O 1.12 mediu o
#: ótimo em 95 mercados — acima disso o líquido CAI, porque entram os de
#: volume enorme e receita mínima. 120 dá folga para os que fecharem.
TOP_PADRAO = 120

#: Quantos trades públicos olhar por mercado para estimar fluxo. É
#: deliberadamente menor que o script offline (`conta_do_maker_nos_pools.py`):
#: aqui isso roda dentro do SHADOW/LIVE, então serve para ordenar candidatos,
#: não para publicar veredito.
LIMITE_DE_TRADES_PARA_RANKING = 200

#: Cursor de fim do CLOB (base64 de "-1").
CURSOR_FINAL = "LTE="


@dataclass(frozen=True, slots=True)
class MercadoComPool:
    """O que a lista do CLOB diz sobre um mercado do programa."""

    condition_id: str
    daily_rate: float
    min_size: float
    max_spread_centavos: float

    @property
    def max_spread_fracao(self) -> float:
        """Em FRAÇÃO. A conversão mora aqui pelo mesmo motivo que em
        `rewards_da_gamma`: dividir duas vezes, ou nenhuma, dá pool 100×
        errado sem levantar exceção."""
        return self.max_spread_centavos / 100.0




def ler_pagina_de_pools(pagina: Any) -> tuple[list[MercadoComPool], str]:
    """`(mercados, proximo_cursor)` de uma página de `/rewards/markets/current`.

    Função pura, separada do I/O, para que o teste não precise de rede — é a
    mesma separação que `markets/http.py` faz pela descoberta Up/Down.

    Mercado sem `total_daily_rate` ou sem `rewards_max_spread` fica de fora:
    são os dois sem os quais a conta de reward não existe, e inventá-los
    produziria pool onde não há nenhum (mesma regra de
    `rewards_da_gamma.parametros_de_reward`).
    """
    if not isinstance(pagina, dict):
        return [], ""
    saida: list[MercadoComPool] = []
    for bruto in pagina.get("data") or []:
        if not isinstance(bruto, dict):
            continue
        cid = bruto.get("condition_id")
        taxa = numero(bruto.get("total_daily_rate"))
        spread = numero(bruto.get("rewards_max_spread"))
        if not isinstance(cid, str) or not cid or taxa is None or spread is None:
            continue
        if taxa <= 0 or spread <= 0:
            continue
        saida.append(
            MercadoComPool(
                condition_id=cid,
                daily_rate=taxa,
                min_size=numero(bruto.get("rewards_min_size")) or 0.0,
                max_spread_centavos=spread,
            )
        )
    cursor = str(pagina.get("next_cursor") or "")
    return saida, "" if cursor == CURSOR_FINAL else cursor


def janela_do_mercado(
    pool: MercadoComPool,
    mercado: dict[str, Any],
    *,
    agora_epoch: float | None = None,
) -> JanelaAoVivo | None:
    """Monta a `JanelaAoVivo` da rota maker, ou `None` se não dá para cotar.

    Recusa — cada uma com motivo, e nenhuma silenciosa por default:

    - mercado fechado ou não aceitando ordens;
    - menos de dois tokens (sem os dois lados não há mercado binário);
    - sem `minimum_tick_size` (sem tick não há onde pôr a cotação).

    `fechamento_epoch` sai do `end_date_iso` quando ele existe e é FUTURO.
    Quando não existe — e mercado de horizonte longo às vezes não traz —, usa
    um horizonte sintético. `seconds_left` só entra na conta de reward como
    duração da exposição; ele NÃO é usado para prever nada aqui, então um
    horizonte aproximado não contamina decisão nenhuma.
    """
    if not mercado.get("accepting_orders") or mercado.get("closed"):
        return None
    tokens = [
        t
        for t in (mercado.get("tokens") or [])
        if isinstance(t, dict) and isinstance(t.get("token_id"), str)
    ]
    if len(tokens) < 2:
        return None
    tick = numero(mercado.get("minimum_tick_size"))
    if tick is None or tick <= 0:
        return None

    agora = time.time() if agora_epoch is None else agora_epoch
    fechamento = _fechamento(mercado, agora)

    return JanelaAoVivo(
        slug=str(mercado.get("market_slug") or pool.condition_id),
        asset=str(mercado.get("question") or "")[:80],
        jogo=JOGO_REWARD,
        condition_id=pool.condition_id,
        token_up=tokens[0]["token_id"],
        token_down=tokens[1]["token_id"],
        duracao_s=int(max(0.0, fechamento - agora)),
        abertura_epoch=agora,
        fechamento_epoch=fechamento,
        tick_size=tick,
        min_order_size=numero(mercado.get("minimum_order_size")) or 5.0,
        # Fee do taker: o maker não paga (`takerOnly`, §15.1), mas o campo
        # existe na janela e zerá-lo mentiria sobre o mercado.
        fee_rate=0.0,
        fee_exponent=1.0,
        reward_daily_rate=pool.daily_rate,
        reward_min_size=pool.min_size,
        reward_max_spread=pool.max_spread_fracao,
    )


#: Horizonte usado quando o mercado não traz `end_date_iso` utilizável.
#: 24 h e não "infinito": a cotação tem de ser reavaliada, e um fechamento no
#: ano 2500 faria `seconds_left` inflar a conta de exposição sem limite.
HORIZONTE_SINTETICO_S = 86400.0


def _fechamento(mercado: dict[str, Any], agora: float) -> float:
    bruto = mercado.get("end_date_iso")
    if isinstance(bruto, str) and bruto:
        from datetime import datetime

        try:
            quando = datetime.fromisoformat(bruto.replace("Z", "+00:00"))
        except ValueError:
            return agora + HORIZONTE_SINTETICO_S
        epoch = quando.timestamp()
        # Passado ou absurdamente distante caem no sintético: os dois
        # produziriam `seconds_left` que não descreve a exposição real.
        if agora < epoch < agora + 365 * 86400:
            return epoch
    return agora + HORIZONTE_SINTETICO_S


# ─────────────────────────────────────────────────────── o lado com rede ──
class DescobertaDePools:
    """Enumera os mercados do programa e devolve `JanelaAoVivo` prontas.

    O cliente HTTP é **injetado**, como em `MarketDiscovery` — é a regra
    offline-first do M1: os testes passam um dublê, produção passa httpx. Sem
    isso, testar esta classe exigiria rede, e teste que exige rede não roda
    no CI e por isso não roda nunca.
    """

    def __init__(
        self,
        http_get_json: Any,
        *,
        base_clob: str,
        base_data: str | None = None,
        top: int = TOP_PADRAO,
        tamanho_da_cotacao: float | None = None,
    ) -> None:
        self._get = http_get_json
        self._base = base_clob.rstrip("/")
        self._base_data = base_data.rstrip("/") if base_data else None
        self.top = top
        #: Com tamanho, a descoberta só devolve mercado onde a NOSSA cotação
        #: pontua hoje — medido com o mesmo `score_da_ordem` que a varredura
        #: do 1.12 usou, sobre o livro REST de agora. Sem tamanho, devolve
        #: todos (é o que os testes de montagem exercitam).
        #:
        #: O filtro existe por uma medição, não por gosto: a primeira rodada
        #: SHADOW com os 60 maiores pools assinou 272 tokens, e o CLOB
        #: derrubou a conexão a cada 3 s com `1013 slow consumer` — os pools
        #: grandes são jogos ao vivo com spread de 11 c, que nunca pontuam
        #: (§2f) e inundam o fio. Assinar só quem pontua corta os dois.
        self.tamanho_da_cotacao = tamanho_da_cotacao
        #: Contadores de recusa, por motivo. O diário quer o motivo — "0
        #: janelas" sem causa nomeada é o tipo de silêncio que este projeto
        #: já pagou para não ter.
        self.descartes: dict[str, int] = {}

    def _descartar(self, motivo: str) -> None:
        self.descartes[motivo] = self.descartes.get(motivo, 0) + 1

    async def listar(self) -> list[MercadoComPool]:
        """Mercados com pool, cortados no topo.

        Sem `base_data`, mantém o fallback antigo: maior pool primeiro. Com
        `base_data`, aplica a decisão do quadro de 2026-09-18: dentro de um
        universo candidato, preferir reward por unidade de fluxo taker
        (`receita_por_mil_shares`) em vez de pool bruto. Pool grande costuma
        atrair justamente o fluxo que nos atropela; o selector novo evita
        confundir receita alta com lucro alto.
        """
        todos: list[MercadoComPool] = []
        cursor = ""
        # Teto de páginas: o cursor vem do FIO, e um servidor que devolvesse
        # sempre o mesmo cursor faria este laço rodar para sempre dentro de um
        # processo de 24 h. Falhar por teto é diagnosticável; travar não é.
        for _ in range(200):
            params: dict[str, Any] = {"sponsored": "false"}
            if cursor:
                params["next_cursor"] = cursor
            pagina = await self._get(f"{self._base}/rewards/markets/current", params)
            mercados, cursor = ler_pagina_de_pools(pagina)
            todos.extend(mercados)
            if not cursor or not mercados:
                break
        todos.sort(key=lambda m: m.daily_rate, reverse=True)
        candidatos = todos[: max(self.top, self.top * 3)]
        if self._base_data:
            return await self._ordenar_por_reward_por_fluxo(candidatos)
        return todos[: self.top]

    async def _ordenar_por_reward_por_fluxo(
        self, mercados: list[MercadoComPool]
    ) -> list[MercadoComPool]:
        chaves: list[tuple[float, float, MercadoComPool]] = []
        for mercado in mercados:
            fluxo = await self._shares_por_hora(mercado.condition_id)
            # Sem fluxo medido vai para o fim, nunca para o começo. "Não vi
            # trade" é ausência de dado, não prova de custo zero.
            eficiencia = (
                -1.0
                if fluxo is None or fluxo <= 0
                else mercado.daily_rate / 24.0 / (fluxo / 1000.0)
            )
            chaves.append((eficiencia, mercado.daily_rate, mercado))
        chaves.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [mercado for _, _, mercado in chaves[: self.top]]

    async def _shares_por_hora(self, condition_id: str) -> float | None:
        if not self._base_data:
            return None
        try:
            bruto = await self._get(
                f"{self._base_data}/trades",
                {"market": condition_id, "limit": LIMITE_DE_TRADES_PARA_RANKING},
            )
        except Exception:
            return None
        if not isinstance(bruto, list) or not bruto:
            return None
        carimbos: list[float] = []
        for t in bruto:
            if not isinstance(t, dict):
                continue
            ts = t.get("timestamp")
            if isinstance(ts, (int, float)):
                carimbos.append(float(ts))
        if len(carimbos) < 2:
            return None
        span_h = (max(carimbos) - min(carimbos)) / 3600.0
        if span_h <= 0:
            return None
        shares = sum(
            float(t.get("size") or 0.0)
            for t in bruto
            if isinstance(t, dict)
        )
        return shares / span_h

    async def descobrir(self, *, agora_epoch: float | None = None) -> list[JanelaAoVivo]:
        """As janelas cotáveis, já montadas."""
        self.descartes.clear()
        janelas: list[JanelaAoVivo] = []
        for pool in await self.listar():
            mercado = await self._get(f"{self._base}/markets/{pool.condition_id}", None)
            if not isinstance(mercado, dict):
                self._descartar("sem_resposta_do_mercado")
                continue
            janela = janela_do_mercado(pool, mercado, agora_epoch=agora_epoch)
            if janela is None:
                self._descartar("nao_cotavel")
                continue
            if self.tamanho_da_cotacao is not None and not await self._pontua(
                janela, pool
            ):
                continue
            janelas.append(janela)
        return janelas

    async def _pontua(self, janela: JanelaAoVivo, pool: MercadoComPool) -> bool:
        """A nossa cotação a 1 tick do topo, nos dois lados, pontua neste
        livro? Mesma pergunta e mesma função (`score_da_ordem`) da varredura
        que fechou o 1.12 — nunca uma segunda leitura da fórmula."""
        bruto = await self._get(
            f"{self._base}/book", {"token_id": janela.token_up}
        )
        livro = OrderBook.from_event(bruto) if isinstance(bruto, dict) else None
        if livro is None:
            self._descartar(DESCARTE_SEM_LIVRO)
            return False
        params = ParametrosDeReward(
            daily_rate=pool.daily_rate,
            min_size=pool.min_size,
            max_spread=pool.max_spread_fracao,
            tick_size=janela.tick_size,
        )
        ordem = OrdemHipotetica(
            tamanho=self.tamanho_da_cotacao or 0.0, distancia_ticks=1, dois_lados=True
        )
        if score_da_ordem(ordem, livro, params) <= 0.0:
            self._descartar(DESCARTE_NAO_PONTUA)
            return False
        return True
