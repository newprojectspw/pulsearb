"""M2.7 — a gravação estava cega metade do tempo.

8h de gravação real mediram **163.195 segundos de silêncio** do feed-verdade:
48 casos de tópico mudo com a conexão VIVA e 6 de conexão inteira muda, a
maior de 3.796s. 184 de 254 janelas tiveram a abertura em lacuna, e a
varredura da âncora perdeu 75% da amostra.

São dois fenômenos com consertos diferentes, e o ponto destes testes é que
**nenhum mecanismo cobre o outro**: o watchdog conta qualquer mensagem e não
enxerga um tópico caducando; a reassinatura não derruba conexão morta.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from pulsearb.feeds.base import ReconnectingFeed, SilencioDeDados
from pulsearb.feeds.rtds import (
    TOPIC_BINANCE,
    TOPIC_TWAP_60,
    PriceTick,
    RtdsFeed,
)


def _tick(topico: str, idade_s: float) -> PriceTick:
    return PriceTick(
        topic=topico,
        asset="btc",
        price=1.0,
        src_timestamp_ms=0,
        ts_mono_ns=time.monotonic_ns() - int(idade_s * 1e9),
        ts_wall_ns=0,
    )


def _feed(**kwargs) -> RtdsFeed:
    return RtdsFeed(
        url="ws://exemplo",
        user_agent="teste",
        assets=["btc"],
        **kwargs,
    )


# ───────────────────────────── watchdog: conexão viva e MUDA


class _WsMudo:
    """Socket que nunca entrega nada — a conexão de 3.796s de silêncio.

    O ping/pong do protocolo continuaria respondendo; é justamente por isso
    que ela não caía sozinha.
    """

    async def recv(self):
        await asyncio.Event().wait()   # espera para sempre


class _WsFalante:
    def __init__(self, mensagens):
        self._restantes = list(mensagens)

    async def recv(self):
        if not self._restantes:
            await asyncio.Event().wait()
        return self._restantes.pop(0)


async def _consumir(feed, ws, limite=10):
    saida = []
    async for mensagem in feed._mensagens(ws):
        saida.append(mensagem)
        if len(saida) >= limite:
            break
    return saida


@pytest.mark.asyncio
async def test_watchdog_derruba_conexao_viva_e_muda():
    """O caso que o keepalive do M2.1 NÃO cobre.

    Ping/pong prova que o cano está aberto; não prova que a água passa.
    """
    feed = _feed(sem_dados_timeout_s=0.05)

    with pytest.raises(SilencioDeDados):
        await _consumir(feed, _WsMudo())

    assert feed.watchdog_reconexoes == 1


@pytest.mark.asyncio
async def test_watchdog_nao_dispara_com_dado_chegando():
    feed = _feed(sem_dados_timeout_s=0.5)
    ws = _WsFalante(["a", "b", "c"])

    assert await _consumir(feed, ws, limite=3) == ["a", "b", "c"]
    assert feed.watchdog_reconexoes == 0


@pytest.mark.asyncio
async def test_watchdog_desligado_e_o_comportamento_ate_o_m26():
    """`None` preserva o comportamento antigo — quem quer o watchdog pede."""
    feed = _feed(sem_dados_timeout_s=None)
    ws = _WsFalante(["x"])

    assert await _consumir(feed, ws, limite=1) == ["x"]
    assert feed.watchdog_reconexoes == 0


# ──────────────────── reassinatura: tópico mudo com a conexão viva


def test_topico_mudo_dispara_reassinatura_urgente():
    feed = _feed(topico_mudo_s=15.0)
    feed.last_tick_by_key[(TOPIC_TWAP_60, "btc")] = _tick(TOPIC_TWAP_60, 20.0)

    motivo = feed._reassinatura_urgente()
    assert motivo is not None
    assert TOPIC_TWAP_60 in motivo


def test_topico_fresco_nao_dispara():
    feed = _feed(topico_mudo_s=15.0)
    feed.last_tick_by_key[(TOPIC_TWAP_60, "btc")] = _tick(TOPIC_TWAP_60, 1.0)
    feed.last_tick_by_key[(TOPIC_BINANCE, "btc")] = _tick(TOPIC_BINANCE, 1.0)

    assert feed._reassinatura_urgente() is None


def test_um_topico_mudo_com_o_outro_vivo_e_o_caso_que_importa():
    """Os 48 casos medidos. O watchdog da base NÃO pega este: ele conta
    qualquer mensagem, e o outro tópico continuava chegando."""
    feed = _feed(topico_mudo_s=15.0, sem_dados_timeout_s=30.0)
    feed.last_tick_by_key[(TOPIC_BINANCE, "btc")] = _tick(TOPIC_BINANCE, 0.5)
    feed.last_tick_by_key[(TOPIC_TWAP_60, "btc")] = _tick(TOPIC_TWAP_60, 40.0)

    # a visão por tópico enxerga...
    assert feed._reassinatura_urgente() is not None
    # ...e a idade agregada da base NÃO enxergaria: houve mensagem há 0,5s
    idades = feed.idade_por_topico()
    assert idades[TOPIC_BINANCE] < 1.0
    assert idades[TOPIC_TWAP_60] > 30.0


def test_sem_tick_nenhum_nao_e_motivo_de_reassinar():
    """Assinatura que nunca entregou nada é problema de conexão, e disso
    cuida o watchdog. Reassinar aqui esconderia a causa real."""
    feed = _feed(topico_mudo_s=15.0)
    assert feed._reassinatura_urgente() is None


def test_limiar_desligado_nunca_dispara():
    feed = _feed(topico_mudo_s=None)
    feed.last_tick_by_key[(TOPIC_TWAP_60, "btc")] = _tick(TOPIC_TWAP_60, 9999.0)
    assert feed._reassinatura_urgente() is None


@pytest.mark.asyncio
async def test_loop_reassina_por_silencio_antes_do_intervalo():
    """O relógio sozinho não cumpre a meta: 6 caducidades/h x até 300s de
    cegueira seriam 1.800s/h contra uma meta de 60s/h. Quem cumpre é a
    REAÇÃO ao tópico mudo."""
    feed = _feed(topico_mudo_s=0.01, reassinatura_intervalo_s=3600.0)
    feed.PASSO_DE_VERIFICACAO_S = 0.01
    feed.last_tick_by_key[(TOPIC_TWAP_60, "btc")] = _tick(TOPIC_TWAP_60, 5.0)
    enviados = []

    async def _fingir(_ws):
        enviados.append(1)

    feed._reassinar = _fingir
    tarefa = asyncio.create_task(feed._loop_de_reassinatura(object()))
    await asyncio.sleep(0.08)
    tarefa.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tarefa

    # o intervalo era de uma HORA e mesmo assim reassinou
    assert enviados, "não reagiu ao tópico mudo"
    assert feed.reassinaturas_por_silencio > 0


@pytest.mark.asyncio
async def test_reassinatura_desligada_nao_cria_trabalho():
    feed = _feed(reassinatura_intervalo_s=None)
    # retorna de imediato, sem laço
    await asyncio.wait_for(feed._loop_de_reassinatura(object()), timeout=0.5)
    assert feed.reassinaturas == 0


def test_reassinar_do_rtds_e_o_mesmo_frame_do_connect():
    """Reenviar o subscribe é a correção; mandar outra coisa seria inventar
    protocolo."""
    feed = _feed()
    frame = feed.subscribe_frame()
    assert '"action":"subscribe"' in frame.replace(" ", "")
    assert TOPIC_TWAP_60 in frame
    assert isinstance(frame, str), "frame binário derruba o RTDS com 1003"


def test_base_sem_subclasse_nao_reassina_nada():
    """O hook default é no-op: um feed que não sabe reassinar não deve
    inventar um frame."""
    base = ReconnectingFeed(name="t", url="ws://x", user_agent="u")
    assert asyncio.run(base._reassinar(object())) is None
