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

from pulsearb.live.rastreador import JanelaAoVivo

#: O `jogo` destas janelas. Valor próprio, e não `twap`, porque é ele que o
#: `jogos_operados` do taker usa para recusar. Reaproveitar `twap` aqui faria
#: o taker aceitar apostar direção em mercado sem âncora.
JOGO_REWARD = "reward"

#: Quantos mercados considerar, do maior pool para o menor. O 1.12 mediu o
#: ótimo em 95 mercados — acima disso o líquido CAI, porque entram os de
#: volume enorme e receita mínima. 120 dá folga para os que fecharem.
TOP_PADRAO = 120

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


def _numero(valor: Any) -> float | None:
    if valor is None or isinstance(valor, bool):
        return None
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


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
        taxa = _numero(bruto.get("total_daily_rate"))
        spread = _numero(bruto.get("rewards_max_spread"))
        if not isinstance(cid, str) or not cid or taxa is None or spread is None:
            continue
        if taxa <= 0 or spread <= 0:
            continue
        saida.append(
            MercadoComPool(
                condition_id=cid,
                daily_rate=taxa,
                min_size=_numero(bruto.get("rewards_min_size")) or 0.0,
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
    tick = _numero(mercado.get("minimum_tick_size"))
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
        min_order_size=_numero(mercado.get("minimum_order_size")) or 5.0,
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
