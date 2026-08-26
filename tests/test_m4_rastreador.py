"""M4 — o rastreador de janelas ao vivo.

`seconds_left` escolhe o balde de calibração, e o M2 mediu erro de 0,008 na
faixa 240–120 s contra 0,240 acima de 240 s — trinta vezes mais. Um
`seconds_left` deslocado não degrada a decisão: toma a decisão na faixa
errada. Estes testes travam a aritmética que o produz.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from pulsearb.backtest.__main__ import duracao_do_slug as duracao_usada_pelo_backtest
from pulsearb.live.rastreador import (
    DESCARTE_JA_FECHADA,
    DESCARTE_NAO_OPERAVEL,
    DESCARTE_SEM_FECHAMENTO,
    DESCARTE_SEM_TOKENS,
    RastreadorDeJanelas,
)
from pulsearb.markets.discovery import DiscoveredMarket, duracao_do_slug


def _mercado(
    slug: str = "btc-updown-5m-1787000000",
    *,
    operable: bool = True,
    tokens: dict[str, str] | None = None,
    end_date: str | None = "2026-08-25T12:00:00Z",
    condition_id: str = "0xaa",
) -> DiscoveredMarket:
    return DiscoveredMarket(
        slug=slug,
        condition_id=condition_id,
        asset="btc",
        resolution="chainlink_twap",
        token_id_by_outcome=(
            tokens if tokens is not None else {"Up": "tok-up", "Down": "tok-down"}
        ),
        tick_size=0.01,
        min_order_size=5.0,
        fee_rate=0.07,
        fee_exponent=1.0,
        fee_taker_only=True,
        fee_rebate_rate=0.2,
        accepting_orders=True,
        end_date_iso=end_date,
        operable=operable,
        raw_gamma={"endDate": end_date} if end_date else {},
    )


def _epoch(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


FECHAMENTO = _epoch("2026-08-25T12:00:00Z")


class TestDuracaoCompartilhada:
    """Se o motor ao vivo e o backtest discordarem, a comparação entre eles morre.

    Uma divergência de aritmética pareceria diferença de mercado — e é
    justamente essa comparação que justifica o SHADOW existir.
    """

    @pytest.mark.parametrize(
        ("slug", "esperado"),
        [
            ("btc-updown-5m-1787000000", 300),
            ("eth-updown-15m-1787000000", 900),
            ("btc-updown-1h-1787000000", 3600),
            ("eth-updown-4h-1787000000", 14400),
            ("bitcoin-up-or-down-august-25-2026-3am-et", 3600),
        ],
    )
    def test_duracao_por_familia_de_slug(self, slug, esperado):
        assert duracao_do_slug(slug) == esperado

    def test_o_backtest_usa_a_MESMA_funcao(self):
        # Não é teste de importação por burocracia: uma segunda cópia é
        # exatamente o defeito que este teste existe para impedir.
        assert duracao_usada_pelo_backtest is duracao_do_slug


class TestSecondsLeft:
    def test_abertura_sai_de_fechamento_menos_duracao(self):
        rastreador = RastreadorDeJanelas()
        rastreador.atualizar([_mercado()], agora_epoch=FECHAMENTO - 100)

        janela = rastreador.abertas(agora_epoch=FECHAMENTO - 100)[0]
        assert janela.duracao_s == 300
        assert janela.fechamento_epoch == pytest.approx(FECHAMENTO)
        assert janela.abertura_epoch == pytest.approx(FECHAMENTO - 300)
        assert janela.seconds_left(FECHAMENTO - 100) == pytest.approx(100.0)

    def test_a_faixa_calibrada_cai_onde_deve(self):
        # 240–120s é a faixa em que o M2 mediu erro de 0,008.
        rastreador = RastreadorDeJanelas()
        agora = FECHAMENTO - 180
        rastreador.atualizar([_mercado()], agora_epoch=agora)

        janela = rastreador.abertas(agora_epoch=agora)[0]
        assert 120.0 < janela.seconds_left(agora) <= 240.0


class TestFalhaFechada:
    def test_sem_fechamento_legivel_a_janela_nao_entra(self):
        # Sem fechamento não há seconds_left, e sem seconds_left a decisão
        # não tem faixa. Fora — não entra com palpite.
        rastreador = RastreadorDeJanelas()
        rastreador.atualizar([_mercado(end_date=None)], agora_epoch=FECHAMENTO - 100)

        assert rastreador.abertas(agora_epoch=FECHAMENTO - 100) == []
        assert rastreador.descartes[DESCARTE_SEM_FECHAMENTO] == 1

    def test_nao_operavel_nao_entra(self):
        rastreador = RastreadorDeJanelas()
        rastreador.atualizar([_mercado(operable=False)], agora_epoch=FECHAMENTO - 100)

        assert rastreador.abertas(agora_epoch=FECHAMENTO - 100) == []
        assert rastreador.descartes[DESCARTE_NAO_OPERAVEL] == 1

    @pytest.mark.parametrize(
        "tokens", [{}, {"Up": "tok-up"}, {"Down": "tok-down"}]
    )
    def test_um_lado_so_nao_e_janela(self, tokens):
        # O backtest sempre pareia Up e Down; um lado só não dá para operar
        # nem para reconciliar depois.
        rastreador = RastreadorDeJanelas()
        rastreador.atualizar([_mercado(tokens=tokens)], agora_epoch=FECHAMENTO - 100)

        assert rastreador.descartes[DESCARTE_SEM_TOKENS] == 1

    def test_janela_futura_ainda_nao_serve(self):
        """A descoberta olha à frente — trazer janela futura é o certo PARA ELA.

        Aqui ela entraria com `seconds_left` maior que a própria duração, o
        que colocaria a decisão numa faixa que não existe.
        """
        rastreador = RastreadorDeJanelas()
        rastreador.atualizar([_mercado()], agora_epoch=FECHAMENTO - 900)

        assert rastreador.abertas(agora_epoch=FECHAMENTO - 900) == []
        assert rastreador.descartes[DESCARTE_JA_FECHADA] == 1


class TestCicloDeVida:
    def test_aposentar_devolve_o_que_saiu(self):
        """Janela que fecha e não é baixada trava o teto de exposição para sempre.

        Por isso `aposentar_fechadas` devolve em vez de só apagar: quem chama
        precisa liquidar a exposição delas no portão.
        """
        rastreador = RastreadorDeJanelas()
        rastreador.atualizar([_mercado()], agora_epoch=FECHAMENTO - 100)

        saiu = rastreador.aposentar_fechadas(agora_epoch=FECHAMENTO + 1)
        assert [j.slug for j in saiu] == ["btc-updown-5m-1787000000"]
        assert rastreador.janelas == {}

    def test_ordem_e_a_que_fecha_primeiro(self):
        # Com teto de posições abertas, a que fecha antes devolve capacidade
        # mais cedo — a ordem é operacional, não estética.
        cedo = _mercado(
            slug="btc-updown-5m-a", condition_id="0xa", end_date="2026-08-25T12:00:00Z"
        )
        tarde = _mercado(
            slug="btc-updown-5m-b", condition_id="0xb", end_date="2026-08-25T12:04:00Z"
        )
        # 11:59:30 é o único instante em que as DUAS estão abertas: a vai de
        # 11:55 a 12:00, b de 11:59 a 12:04.
        agora = _epoch("2026-08-25T11:59:30Z")

        rastreador = RastreadorDeJanelas()
        rastreador.atualizar([tarde, cedo], agora_epoch=agora)

        assert [j.slug for j in rastreador.abertas(agora_epoch=agora)] == [
            "btc-updown-5m-a",
            "btc-updown-5m-b",
        ]

    def test_redescoberta_atualiza_em_vez_de_duplicar(self):
        rastreador = RastreadorDeJanelas()
        agora = FECHAMENTO - 100
        for _ in range(3):
            rastreador.atualizar([_mercado()], agora_epoch=agora)

        assert len(rastreador.abertas(agora_epoch=agora)) == 1


class TestResumo:
    def test_descartes_respondem_por_que_o_bot_nao_opera(self):
        # "Não achou janela" e "achou e jogou fora" são diagnósticos
        # diferentes, com consertos diferentes.
        rastreador = RastreadorDeJanelas()
        rastreador.atualizar(
            [
                _mercado(condition_id="0x1"),
                _mercado(condition_id="0x2", operable=False),
                _mercado(condition_id="0x3", end_date=None),
            ],
            agora_epoch=FECHAMENTO - 100,
        )

        resumo = rastreador.resumo(agora_epoch=FECHAMENTO - 100)
        assert resumo["abertas"] == 1
        assert resumo["por_ativo"] == {"btc": 1}
        assert resumo["descartes"] == {
            DESCARTE_NAO_OPERAVEL: 1,
            DESCARTE_SEM_FECHAMENTO: 1,
        }
