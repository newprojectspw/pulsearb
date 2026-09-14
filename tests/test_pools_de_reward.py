"""A descoberta dos mercados de reward, e a trava que impede o taker de tocá-los.

O teste que importa mais não é o de parsing: é
`test_o_taker_RECUSA_janela_de_reward`. Sem ele, uma janela de temperatura em
Wellington entraria no motor que aposta DIREÇÃO usando uma âncora (`τ=0` sobre
o TWAP do Chainlink, §13.8) que **não existe** para aquele mercado.
"""

from __future__ import annotations

from pulsearb.engine.decisao import JOGO_TWAP
from pulsearb.live.livros import LivrosAoVivo
from pulsearb.live.motor import PULOU_JOGO_NAO_OPERADO, ConfigDoMotor, MotorAoVivo
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


# ── o lado com rede, com o cliente injetado ──────────────────────────────
class _HttpFake:
    """Dublê do `http_get_json`. Registra o que foi pedido."""

    def __init__(self, paginas: list[dict], mercados: dict[str, dict]) -> None:
        self.paginas = paginas
        self.mercados = mercados
        self.pedidos: list[str] = []

    async def __call__(self, url: str, params):
        self.pedidos.append(url)
        if url.endswith("/rewards/markets/current"):
            n = sum(1 for p in self.pedidos if p.endswith("current")) - 1
            return self.paginas[min(n, len(self.paginas) - 1)]
        cid = url.rsplit("/", 1)[-1]
        return self.mercados.get(cid)


def _pagina(cids: list[str], cursor: str, taxa_base: float = 100.0) -> dict:
    return {
        "data": [
            {
                "condition_id": c,
                "total_daily_rate": taxa_base + i,
                "rewards_max_spread": 4.5,
                "rewards_min_size": 20,
            }
            for i, c in enumerate(cids)
        ],
        "next_cursor": cursor,
    }


async def _descobrir(fake, **kw):
    from pulsearb.markets.pools_de_reward import DescobertaDePools

    d = DescobertaDePools(fake, base_clob="https://clob.example", **kw)
    return d, await d.descobrir(agora_epoch=AGORA)


def test_pagina_ate_o_cursor_final_e_ordena_por_pool() -> None:
    import asyncio

    fake = _HttpFake(
        paginas=[_pagina(["0x1", "0x2"], "c2"), _pagina(["0x3"], "LTE=", taxa_base=500)],
        mercados={c: _mercado() for c in ("0x1", "0x2", "0x3")},
    )
    _d, janelas = asyncio.run(_descobrir(fake))
    assert len(janelas) == 3
    # 0x3 tem pool 500; tem de vir primeiro.
    assert janelas[0].condition_id == "0x3"
    assert janelas[0].reward_daily_rate == 500.0


def test_top_corta_pelos_MAIORES_pools() -> None:
    import asyncio

    fake = _HttpFake(
        paginas=[_pagina(["0x1", "0x2", "0x3"], "LTE=")],
        mercados={c: _mercado() for c in ("0x1", "0x2", "0x3")},
    )
    _, janelas = asyncio.run(_descobrir(fake, top=1))
    assert len(janelas) == 1
    assert janelas[0].reward_daily_rate == 102.0


def test_mercado_nao_cotavel_sai_COM_MOTIVO() -> None:
    """Silêncio aqui seria o defeito que este projeto já pagou.

    "0 janelas" sem causa nomeada manda investigar o mercado quando o
    problema pode ser o nosso leitor.
    """
    import asyncio

    fake = _HttpFake(
        paginas=[_pagina(["0x1", "0x2"], "LTE=")],
        mercados={"0x1": _mercado(closed=True), "0x2": None},
    )
    d, janelas = asyncio.run(_descobrir(fake))
    assert janelas == []
    assert d.descartes["nao_cotavel"] == 1
    assert d.descartes["sem_resposta_do_mercado"] == 1


def test_cursor_que_nao_avanca_NAO_TRAVA_o_processo() -> None:
    """O cursor vem do FIO.

    Um servidor que devolvesse sempre o mesmo cursor faria o laço rodar para
    sempre dentro de um processo de 24 h. Falhar por teto é diagnosticável;
    travar não é — e travar aqui congelaria o SHADOW inteiro.
    """
    import asyncio

    fake = _HttpFake(
        paginas=[_pagina(["0x1"], "sempre-o-mesmo")],
        mercados={"0x1": _mercado()},
    )
    _, janelas = asyncio.run(_descobrir(fake, top=5))
    # Parou pelo teto de páginas, não travou; e não duplicou além do top.
    assert len(janelas) <= 5


# ── fim a fim: descoberta -> rastreador -> laço maker COTA ───────────────
def test_o_laco_maker_COTA_um_mercado_de_reward_descoberto(tmp_path) -> None:
    """A prova de que a rota inteira liga, e é o que o 1.12 aprovou.

    Descoberta (REST dublê) -> `absorver` -> `laco_maker.passo` -> cotação.
    Se qualquer elo quebrar, o `enviadas` fica vazio — e nenhum teste de
    unidade dos elos separados pegaria isso.
    """
    import asyncio

    from pulsearb.backtest.book import OrderBook
    from pulsearb.execution.cliente_sombra import ClienteSombraDeOrdens
    from pulsearb.live.laco_maker import LacoMaker

    fake = _HttpFake(
        paginas=[_pagina(["0x1"], "LTE=", taxa_base=229.0)],
        mercados={"0x1": _mercado()},
    )
    _d, janelas = asyncio.run(_descobrir(fake))
    assert len(janelas) == 1

    rastreador = RastreadorDeJanelas()
    rastreador.absorver(janelas)
    abertas = list(rastreador.abertas(agora_epoch=AGORA + 1))
    assert len(abertas) == 1, "a janela de reward tem de estar ABERTA"

    livro = OrderBook(asset_id="111")
    livro.bids = [(0.49, 500.0), (0.48, 400.0)]
    livro.asks = [(0.51, 500.0), (0.52, 400.0)]

    def _rodar(portao, nome: str):
        # O cliente SOMBRA de verdade, não um dublê meu. Escrevi um dublê
        # primeiro e ele errou o contrato duas vezes (`ok` em vez de
        # `estado`, `aprovada` em vez de `pode`) — o que prova o ponto do
        # `CLAUDE.md`: dois caminhos divergem, e a divergência aparece como
        # comportamento de mercado quando é diferença de código.
        cliente = ClienteSombraDeOrdens(
            caminho_do_diario=tmp_path / f"{nome}.jsonl"
        )
        laco = LacoMaker(
            cliente=cliente, tamanho_da_cotacao=1000.0, portao=portao
        )
        asyncio.run(
            laco.passo(
                abertas,
                livro_de=lambda token, agora_ns: livro,
                agora_epoch=AGORA + 1,
                agora_ns=int((AGORA + 1) * 1e9),
                feeds_saudaveis=True,
            )
        )
        return cliente, laco

    # (1) SEM portão: recusa. É a falha fechada que o próprio módulo
    # documenta — cotar "porque ninguém passou trava" é o oposto do que a
    # trava serve. Achado ao escrever este teste, com o laço parando em
    # `portao:sem_portao` depois de já ter decidido que valia a pena.
    cliente, laco = _rodar(None, "sem_portao")
    assert cliente.repousadas == {}
    assert any("sem_portao" in m for m in laco.motivos)

    # (2) COM portão que aprova: a rota inteira liga e a cotação sai.
    class _PortaoQueAprova:
        def avaliar_risco(self, ordem, **_kw):
            return type("D", (), {"pode": True, "motivo": None})()

    cliente, laco = _rodar(_PortaoQueAprova(), "com_portao")
    assert cliente.repousadas, (
        f"o laço maker não cotou o mercado de reward; motivos={laco.motivos}"
    )
    # Todo id do cliente sombra sai com prefixo `sombra-`: a invariante que
    # impede uma ordem de sombra ser confundida com uma real.
    assert all(oid.startswith("sombra-") for oid in cliente.repousadas)


# ── o interruptor ────────────────────────────────────────────────────────
def test_a_rota_de_pools_e_OPT_IN() -> None:
    """Default `False`, e isso não é timidez.

    Ligar assina ~240 tokens novos e põe o laço maker a cotar mercados que o
    taker nunca viu. A rodada de 24 h que produz o dado do taker não pode
    mudar de comportamento por causa de um default novo — foi assim que o
    projeto perdeu uma rodada inteira antes.
    """
    from pulsearb.settings import Settings

    s = Settings()
    assert s.descobrir_pools_de_reward is False
    # O topo default sai do ótimo MEDIDO (95), com folga para os que fecham.
    assert s.top_de_pools_de_reward == 120


def test_um_ciclo_de_pools_absorve_assina_e_CONTA() -> None:
    """O ciclo alimenta o rastreador, assina os tokens e conta o que fez.

    Assinar importa: sem livro o `_passo_da_janela` sai em `sem_livro`, e
    "não sei nada sobre ela" é pior que "recusei por X".
    """
    import asyncio

    from pulsearb.live.shadow import ProcessoShadow

    fake = _HttpFake(
        paginas=[_pagina(["0x1", "0x2"], "LTE=")],
        mercados={"0x1": _mercado(), "0x2": _mercado(tokens=[
            {"token_id": "333"}, {"token_id": "444"}
        ])},
    )

    class _PolyFake:
        def __init__(self) -> None:
            self.assinados: list[str] = []

        async def subscribe(self, tokens):
            self.assinados.extend(tokens)

    processo = ProcessoShadow.__new__(ProcessoShadow)
    processo.ciclo = type("C", (), {"motor": type("M", (), {
        "rastreador": RastreadorDeJanelas()
    })()})()
    processo.poly = _PolyFake()
    processo.tokens_assinados = set()
    processo.desassinar_apos = {}
    processo.pools_descobertos = 0

    from pulsearb.markets.pools_de_reward import DescobertaDePools

    descoberta = DescobertaDePools(fake, base_clob="https://clob.example")
    asyncio.run(processo._um_ciclo_de_pools(descoberta))

    assert processo.pools_descobertos == 2
    assert len(processo.ciclo.motor.rastreador.janelas) == 2
    assert set(processo.poly.assinados) == {"111", "222", "333", "444"}
    # Segundo ciclo não reassina o que já está assinado: reassinar custa um
    # snapshot de livro por token e não compra nada.
    processo.poly.assinados.clear()
    asyncio.run(processo._um_ciclo_de_pools(descoberta))
    assert processo.poly.assinados == []
