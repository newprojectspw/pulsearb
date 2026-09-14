"""A descoberta dos mercados de reward, e a trava que impede o taker de tocá-los.

O teste que importa mais não é o de parsing: é
`test_o_taker_RECUSA_janela_de_reward`. Sem ele, uma janela de temperatura em
Wellington entraria no motor que aposta DIREÇÃO usando uma âncora (`τ=0` sobre
o TWAP do Chainlink, §13.8) que **não existe** para aquele mercado.
"""

from __future__ import annotations

from pulsearb.engine.decisao import JOGO_TWAP
from pulsearb.live.motor import PULOU_JOGO_NAO_OPERADO, ConfigDoMotor, MotorAoVivo
from pulsearb.live.livros import LivrosAoVivo
from pulsearb.live.precos import PrecosAoVivo
from pulsearb.live.rastreador import RastreadorDeJanelas
from pulsearb.markets.pools_de_reward import (
    JOGO_REWARD,
    MercadoComPool,
    janela_do_mercado,
    ler_pagina_de_pools,
)

AGORA = 1_789_000_000.0


class _ExecutorQueNuncaEChamado:
    """Executor que EXPLODE se usado.

    O teste da trava afirma que o taker recusa a janela. Um dublê que
    aceitasse chamadas em silêncio deixaria o teste passar mesmo se a recusa
    sumisse — o executor tem de ser hostil para a asserção valer.
    """

    def __getattr__(self, nome: str):  # pragma: no cover - é o ponto
        raise AssertionError(
            f"o taker NAO pode chamar o executor para janela de reward "
            f"(tentou {nome!r})"
        )


def _pool(**kw) -> MercadoComPool:
    base = dict(
        condition_id="0xabc",
        daily_rate=229.0,
        min_size=20.0,
        max_spread_centavos=4.5,
    )
    base.update(kw)
    return MercadoComPool(**base)


def _mercado(**kw) -> dict:
    base = {
        "market_slug": "temperatura-wellington",
        "question": "Will the highest temperature in Wellington be 18-19C?",
        "accepting_orders": True,
        "closed": False,
        "minimum_tick_size": 0.01,
        "minimum_order_size": 5,
        "end_date_iso": "2026-09-20T00:00:00Z",
        "tokens": [
            {"token_id": "111", "outcome": "Yes"},
            {"token_id": "222", "outcome": "No"},
        ],
    }
    base.update(kw)
    return base


# ── parsing da lista do CLOB ─────────────────────────────────────────────
def test_le_pagina_e_converte_spread_para_fracao() -> None:
    mercados, cursor = ler_pagina_de_pools(
        {
            "data": [
                {
                    "condition_id": "0x1",
                    "total_daily_rate": 229.0,
                    "rewards_max_spread": 4.5,
                    "rewards_min_size": 20,
                }
            ],
            "next_cursor": "abc",
        }
    )
    assert cursor == "abc"
    assert len(mercados) == 1
    assert mercados[0].max_spread_centavos == 4.5
    # A conversão mora num lugar só: dividir duas vezes daria pool 100x errado
    # sem levantar exceção.
    assert mercados[0].max_spread_fracao == 0.045


def test_cursor_de_fim_vira_string_vazia() -> None:
    """`LTE=` é o base64 de -1. Devolver ele cru faria a paginação girar."""
    _, cursor = ler_pagina_de_pools({"data": [{}], "next_cursor": "LTE="})
    assert cursor == ""


def test_mercado_sem_taxa_ou_sem_spread_fica_de_fora() -> None:
    """Sem os dois, a conta de reward não existe — inventá-los produziria
    pool onde não há nenhum. Mesma regra de `rewards_da_gamma`."""
    mercados, _ = ler_pagina_de_pools(
        {
            "data": [
                {"condition_id": "0x1", "rewards_max_spread": 4.5},
                {"condition_id": "0x2", "total_daily_rate": 10.0},
                {"condition_id": "0x3", "total_daily_rate": 0, "rewards_max_spread": 4.5},
                {"condition_id": "0x4", "total_daily_rate": 10.0, "rewards_max_spread": 0},
            ]
        }
    )
    assert mercados == []


# ── montagem da janela ───────────────────────────────────────────────────
def test_monta_janela_com_o_jogo_QUE_O_TAKER_RECUSA() -> None:
    janela = janela_do_mercado(_pool(), _mercado(), agora_epoch=AGORA)
    assert janela is not None
    assert janela.jogo == JOGO_REWARD
    assert janela.jogo != JOGO_TWAP
    assert janela.token_up == "111"
    assert janela.reward_daily_rate == 229.0
    assert janela.reward_max_spread == 0.045


def test_recusa_mercado_fechado_ou_sem_ordens() -> None:
    assert janela_do_mercado(_pool(), _mercado(closed=True), agora_epoch=AGORA) is None
    assert (
        janela_do_mercado(_pool(), _mercado(accepting_orders=False), agora_epoch=AGORA)
        is None
    )


def test_recusa_sem_dois_tokens_e_sem_tick() -> None:
    um_token = _mercado(tokens=[{"token_id": "111"}])
    assert janela_do_mercado(_pool(), um_token, agora_epoch=AGORA) is None
    sem_tick = _mercado(minimum_tick_size=0)
    assert janela_do_mercado(_pool(), sem_tick, agora_epoch=AGORA) is None


def test_fechamento_absurdo_vira_horizonte_sintetico() -> None:
    """`end_date` no ano 2500 existe nestes mercados.

    Aceitá-lo faria `seconds_left` inflar a conta de exposição sem limite —
    e `seconds_left` aqui só mede exposição, nunca prevê.
    """
    longe = janela_do_mercado(
        _pool(), _mercado(end_date_iso="2500-12-31T00:00:00Z"), agora_epoch=AGORA
    )
    assert longe is not None
    assert longe.fechamento_epoch == AGORA + 86400.0

    passado = janela_do_mercado(
        _pool(), _mercado(end_date_iso="2020-01-01T00:00:00Z"), agora_epoch=AGORA
    )
    assert passado is not None
    assert passado.fechamento_epoch == AGORA + 86400.0

    quebrado = janela_do_mercado(
        _pool(), _mercado(end_date_iso="nao-e-data"), agora_epoch=AGORA
    )
    assert quebrado is not None
    assert quebrado.fechamento_epoch == AGORA + 86400.0


# ── a trava ──────────────────────────────────────────────────────────────
def test_rastreador_absorve_janela_pronta() -> None:
    rastreador = RastreadorDeJanelas()
    janela = janela_do_mercado(_pool(), _mercado(), agora_epoch=AGORA)
    assert janela is not None
    rastreador.absorver([janela])
    assert rastreador.janelas["0xabc"] is janela


def test_o_taker_RECUSA_janela_de_reward() -> None:
    """A trava que dá sentido a tudo isto.

    O taker aposta DIREÇÃO com um preditor cuja âncora (τ=0 sobre o TWAP do
    Chainlink) não existe para "vai chover em Wellington". Ele tem de recusar,
    e com MOTIVO NOMEADO — silêncio aqui seria pior que erro.
    """
    motor = MotorAoVivo(
        rastreador=RastreadorDeJanelas(),
        livros=LivrosAoVivo(),
        precos=PrecosAoVivo(),
        executor=_ExecutorQueNuncaEChamado(),
        config=ConfigDoMotor(),
    )
    assert JOGO_REWARD not in motor.config.jogos_operados

    janela = janela_do_mercado(_pool(), _mercado(), agora_epoch=AGORA)
    assert janela is not None
    assert motor._elegivel(janela, agora_epoch=AGORA) is False
    assert motor.pulos.get(PULOU_JOGO_NAO_OPERADO, 0) >= 1


def test_ampliar_jogos_operados_e_ato_EXPLICITO() -> None:
    """A trava é configuração, não acidente — e sair dela exige dizer o nome.

    Este teste existe para que remover a trava seja visível: se alguém puser
    JOGO_REWARD no default, ele quebra.
    """
    padrao = ConfigDoMotor()
    assert padrao.jogos_operados == frozenset({JOGO_TWAP})
