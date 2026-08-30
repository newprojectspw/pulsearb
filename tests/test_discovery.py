"""Descoberta de janelas: slugs, classificação, gates e anti-zumbi.

Tudo offline: o cliente HTTP é um fake dirigido por dicionário (regra do M1).
"""

from typing import Any

import pytest
from tests.conftest import NOW_EPOCH_TESTES

from pulsearb.markets.discovery import (
    HOURLY_DURATION_SECONDS,
    MarketDiscovery,
    ResolutionKind,
    build_hourly_slugs,
    build_slug,
    classify_resolution_source,
    extract_market,
    grid_slots,
    parse_end_date_epoch,
    validate_window_match,
)


# --------------------------------------------------------------- slugs (12.1)
def test_grid_alinhada():
    # now=1786891600 está dentro da janela que começou em 1786891500
    assert grid_slots(1786891600, 300, ahead=2) == [1786891500, 1786891800, 1786892100]
    # início exato de janela: a corrente é ela mesma
    assert grid_slots(1786891500, 300, ahead=0) == [1786891500]
    # com janelas passadas
    assert grid_slots(1786891600, 300, ahead=1, behind=1) == [
        1786891200, 1786891500, 1786891800
    ]


def test_build_slug_formatos():
    assert build_slug("btc", 300, 1786891500) == "btc-updown-5m-1786891500"
    assert build_slug("ETH", 900, 1786891500) == "eth-updown-15m-1786891500"
    assert build_slug("btc", 14400, 1786891500) == "btc-updown-4h-1786891500"


# ------------------------------------------------- slug horário (12.2, adendo 2)
def test_slug_horario_no_fuso_de_ny_no_verao():
    # 2026-08-16T14:00:00Z = 10:00 EDT (UTC-4, horário de verão)
    epoch = 1786888800  # 2026-08-16T14:00:00Z
    slugs = build_hourly_slugs("btc", epoch)
    assert slugs == [
        "bitcoin-up-or-down-august-16-2026-10am-et",
        "bitcoin-up-or-down-august-16-10am-et",
    ]


def test_slug_horario_no_inverno_usa_est():
    # 2026-01-15T15:00:00Z = 10:00 EST (UTC-5, sem horário de verão).
    # Offset fixo daria a hora errada — este teste é o guarda do zoneinfo.
    epoch = 1768489200  # 2026-01-15T15:00:00Z
    assert build_hourly_slugs("eth", epoch)[0] == (
        "ethereum-up-or-down-january-15-2026-10am-et"
    )


def test_slug_horario_meia_noite_e_meio_dia():
    # 2026-08-16T04:00:00Z = 00:00 EDT → 12am
    assert "12am-et" in build_hourly_slugs("btc", 1786852800)[0]
    # 2026-08-16T16:00:00Z = 12:00 EDT → 12pm
    assert "12pm-et" in build_hourly_slugs("btc", 1786896000)[0]


def test_slug_horario_dia_sem_zero_a_esquerda():
    # 2026-08-05T14:00:00Z → dia 5, não 05
    assert "august-5-" in build_hourly_slugs("btc", 1785938400)[0]


# ------------------------------------------------- classificação de resolução
def test_classifica_twap(gamma_a1, gamma_fee):
    assert classify_resolution_source(gamma_a1) is ResolutionKind.TWAP60
    assert classify_resolution_source(gamma_fee) is ResolutionKind.TWAP60


def test_classifica_binance_candle_pela_description(gamma_hourly):
    assert classify_resolution_source(gamma_hourly) is ResolutionKind.BINANCE_CANDLE


def test_classifica_binance_candle_so_pelo_slug():
    # Sem resolutionSource e sem description: o padrão de slug horário basta.
    assert classify_resolution_source(
        {"slug": "bitcoin-up-or-down-august-16-2026-10am-et"}
    ) is ResolutionKind.BINANCE_CANDLE


def test_fonte_desconhecida_e_o_default():
    assert classify_resolution_source({}) is ResolutionKind.DESCONHECIDO
    assert classify_resolution_source(
        {"resolutionSource": "https://exemplo.com/oraculo-novo", "description": "algo"}
    ) is ResolutionKind.DESCONHECIDO


# ------------------------------------------------------- extract_market/gates
def test_extract_com_anexos_reais(gamma_fee, clob_a2):
    market = extract_market(gamma_fee, clob_a2, now_epoch=NOW_EPOCH_TESTES)
    assert market.operable, market.gate_failures
    assert market.resolution is ResolutionKind.TWAP60
    assert market.tick_size == 0.01
    assert market.min_order_size == 5
    assert market.fee_rate == 0.07
    assert market.fee_exponent == 1
    assert market.fee_taker_only is True
    assert market.fee_rebate_rate == 0.2
    # Token mapeado pelo campo `o`, NUNCA por posição (12.11)
    assert market.token_id_by_outcome["Up"] == (
        "115543018378895345799767279703129188075410082315278422945791152216841808006857"
    )
    assert market.token_id_by_outcome["Down"] == (
        "36789946801536470064845681725741526992078687825165782678144377364414275993728"
    )


def test_gate_fee_ilegivel(gamma_a1):
    # O anexo A1 real NÃO tem feeSchedule e não recebeu CLOB compacto.
    market = extract_market(gamma_a1, None, now_epoch=NOW_EPOCH_TESTES)
    assert not market.operable
    assert "fee_schedule_ilegivel" in market.gate_failures


def test_fee_do_clob_supre_ausencia_na_gamma(gamma_a1, clob_a2):
    market = extract_market(gamma_a1, clob_a2, now_epoch=NOW_EPOCH_TESTES)
    assert market.fee_rate == 0.07
    assert "fee_schedule_ilegivel" not in market.gate_failures


def test_gate_fee_divergente(gamma_fee, clob_a2):
    podre = dict(clob_a2)
    podre["fd"] = {"r": 0.09, "e": 1, "to": True}
    market = extract_market(gamma_fee, podre, now_epoch=NOW_EPOCH_TESTES)
    assert not market.operable
    assert "fee_divergente_gamma_vs_clob" in market.gate_failures


def test_fallback_de_token_por_posicao_quando_nao_ha_clob(gamma_fee):
    market = extract_market(gamma_fee, None, now_epoch=NOW_EPOCH_TESTES)
    assert set(market.token_id_by_outcome) == {"Up", "Down"}


def test_gate_tokens_incompletos(gamma_fee):
    quebrado = dict(gamma_fee)
    quebrado["clobTokenIds"] = '["so-um-token"]'
    market = extract_market(quebrado, None, now_epoch=NOW_EPOCH_TESTES)
    assert "tokens_up_down_incompletos" in market.gate_failures


def test_gate_fonte_desconhecida_bloqueia(gamma_fee, clob_a2):
    estranho = dict(gamma_fee)
    estranho["resolutionSource"] = "https://exemplo.com/novo"
    estranho["description"] = "resolve por magia"
    market = extract_market(estranho, clob_a2, now_epoch=NOW_EPOCH_TESTES)
    assert not market.operable
    assert "fonte_de_resolucao_desconhecida" in market.gate_failures


# ----------------------------------------------------- anti-zumbi (12.12)
def test_parse_end_date():
    assert parse_end_date_epoch({"endDate": "2026-08-16T14:50:00Z"}) == 1786891800.0
    assert parse_end_date_epoch({}) is None
    assert parse_end_date_epoch({"endDate": "não é data"}) is None


def test_zumbi_e_rejeitado(gamma_zombie, clob_a2):
    """closed=false NÃO basta: endDate de 2025 e acceptingOrders=false."""
    market = extract_market(gamma_zombie, clob_a2, now_epoch=NOW_EPOCH_TESTES)
    assert not market.operable
    assert "zumbi_end_date_no_passado" in market.gate_failures
    assert "nao_aceitando_ordens" in market.gate_failures


def test_zumbi_com_accepting_orders_ainda_e_rejeitado(gamma_zombie):
    """Mesmo que acceptingOrders mentisse, o endDate no passado derruba."""
    mentiroso = dict(gamma_zombie)
    mentiroso["acceptingOrders"] = True
    market = extract_market(mentiroso, None, now_epoch=NOW_EPOCH_TESTES)
    assert not market.operable
    assert "zumbi_end_date_no_passado" in market.gate_failures


def test_discordancia_de_accepting_orders_bloqueia(gamma_fee, clob_a2):
    """Gamma diz não, CLOB diz sim: na dúvida, não opera."""
    gamma_nao = dict(gamma_fee)
    gamma_nao["acceptingOrders"] = False
    assert clob_a2["ao"] is True
    market = extract_market(gamma_nao, clob_a2, now_epoch=NOW_EPOCH_TESTES)
    assert not market.operable
    assert "nao_aceitando_ordens" in market.gate_failures


def test_accepting_orders_so_do_clob_basta(gamma_fee, clob_a2):
    """Se a Gamma não traz o campo, o CLOB decide sozinho."""
    sem_campo = {k: v for k, v in gamma_fee.items() if k != "acceptingOrders"}
    market = extract_market(sem_campo, clob_a2, now_epoch=NOW_EPOCH_TESTES)
    assert market.accepting_orders
    assert "nao_aceitando_ordens" not in market.gate_failures


def test_sem_sinal_de_accepting_orders_e_bloqueio(gamma_fee):
    """Ausência total de sinal não é 'pode operar'."""
    sem_campo = {k: v for k, v in gamma_fee.items() if k != "acceptingOrders"}
    market = extract_market(sem_campo, None, now_epoch=NOW_EPOCH_TESTES)
    assert not market.accepting_orders
    assert "nao_aceitando_ordens" in market.gate_failures


def test_gate_end_date_ausente(gamma_fee, clob_a2):
    sem_data = {k: v for k, v in gamma_fee.items() if k != "endDate"}
    market = extract_market(sem_data, clob_a2, now_epoch=NOW_EPOCH_TESTES)
    assert not market.operable
    assert "end_date_ausente_ou_ilegivel" in market.gate_failures


# -------------------------- slug resolvendo mercado antigo (12.12b, adendo 3)
def test_janela_correta_passa(gamma_fee):
    """endDate 2026-08-16T15:00Z bate com a janela 15m que começou em ...14:45."""
    assert validate_window_match(
        gamma_fee, expected_end_epoch=1786892400.0, now_epoch=NOW_EPOCH_TESTES
    ) is None


def test_slug_resolvendo_janela_de_2025_e_recusado(gamma_stale_slug):
    """O caso real: 200 OK, mas é a janela homônima do ano passado."""
    motivo = validate_window_match(
        gamma_stale_slug,
        expected_end_epoch=1786896000.0,  # 2026-08-16T16:00Z
        now_epoch=NOW_EPOCH_TESTES,
    )
    assert motivo == "slug_resolveu_janela_no_passado"


def test_janela_futura_mas_errada_e_recusada(gamma_fee):
    """Fim no futuro, porém de OUTRA janela: também não serve."""
    motivo = validate_window_match(
        gamma_fee,
        expected_end_epoch=1786892400.0 + 3600,  # uma hora depois do real
        now_epoch=NOW_EPOCH_TESTES,
    )
    assert motivo is not None
    assert motivo.startswith("slug_resolveu_janela_errada")


def test_tolerancia_aceita_desvio_pequeno_e_recusa_janela_vizinha(gamma_fee):
    real = 1786892400.0
    # 30s de desvio: dentro da tolerância de 60s
    assert validate_window_match(
        gamma_fee, expected_end_epoch=real + 30, now_epoch=NOW_EPOCH_TESTES
    ) is None
    # 300s = a janela de 5m vizinha. A tolerância NÃO pode aceitar isso.
    assert validate_window_match(
        gamma_fee, expected_end_epoch=real + 300, now_epoch=NOW_EPOCH_TESTES
    ) is not None


def test_sem_end_date_e_recusado():
    assert validate_window_match(
        {}, expected_end_epoch=1786892400.0, now_epoch=NOW_EPOCH_TESTES
    ) == "end_date_ausente_ou_ilegivel"


def test_slug_com_data_certa_mas_fechado_e_recusado(gamma_hourly_current):
    """endDate confere, mas acceptingOrders=false: não serve (M1.1 item 3)."""
    fechado = dict(gamma_hourly_current)
    fechado["acceptingOrders"] = False
    assert validate_window_match(
        fechado, expected_end_epoch=1786906800.0, now_epoch=NOW_EPOCH_TESTES
    ) == "slug_resolveu_janela_que_nao_aceita_ordens"


def test_par_de_slugs_horarios_do_mesmo_nome(gamma_hourly_current, gamma_stale_slug):
    """O caso completo da colisão de ano, lado a lado.

    Os dois têm a MESMA question ("Bitcoin Up or Down - August 16, 2PM ET") e
    o mesmo horário nominal. Só o ano no slug — e o endDate — distinguem.
    """
    assert gamma_hourly_current["question"] == gamma_stale_slug["question"]
    esperado = 1786906800.0  # 2026-08-16T19:00:00Z

    assert validate_window_match(
        gamma_hourly_current, expected_end_epoch=esperado, now_epoch=NOW_EPOCH_TESTES
    ) is None
    assert validate_window_match(
        gamma_stale_slug, expected_end_epoch=esperado, now_epoch=NOW_EPOCH_TESTES
    ) == "slug_resolveu_janela_no_passado"


def test_horaria_atual_e_operavel(gamma_hourly_current, clob_a2):
    market = extract_market(
        gamma_hourly_current, clob_a2, now_epoch=NOW_EPOCH_TESTES
    )
    assert market.operable, market.gate_failures
    assert market.resolution is ResolutionKind.BINANCE_CANDLE
    assert market.tick_size == 0.01
    assert market.min_order_size == 5
    # Fees do horário são IDÊNTICAS às das janelas TWAP (API_NOTES 12.2b)
    assert (market.fee_rate, market.fee_exponent) == (0.07, 1)
    assert market.fee_taker_only is True
    assert market.fee_rebate_rate == 0.2


# ------------------------------------------------------ MarketDiscovery (fake)
class FakeHttp:
    """Cliente HTTP falso: dicionário url→resposta. 404 vira None."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    async def __call__(self, url: str, params: dict[str, Any] | None) -> Any:
        self.calls.append(url)
        return self.routes.get(url)


def _discovery(routes: dict[str, Any], **kwargs: Any) -> tuple[MarketDiscovery, FakeHttp]:
    http = FakeHttp(routes)
    now = kwargs.pop("now", float(NOW_EPOCH_TESTES))
    discovery = MarketDiscovery(
        http_get_json=http,
        gamma_url="https://gamma.test",
        clob_url="https://clob.test",
        assets=kwargs.pop("assets", ["btc"]),
        probe_durations_seconds=kwargs.pop("durations", [300]),
        clock=lambda: now,
    )
    return discovery, http


async def test_discover_acha_pela_grade(gamma_fee, clob_a2):
    # gamma_fee é a janela de 15m que começa em 1786891500 e fecha em ...892400.
    routes = {
        f"https://gamma.test/markets/slug/{gamma_fee['slug']}": gamma_fee,
        f"https://clob.test/clob-markets/{gamma_fee['conditionId']}": clob_a2,
    }
    discovery, _ = _discovery(routes, durations=[900])
    markets = await discovery.discover(ahead=0, keyset_fallback=False)
    assert len(markets) == 1
    assert markets[0].operable
    assert discovery.cache[gamma_fee["conditionId"]].slug == gamma_fee["slug"]


async def test_grade_recusa_mercado_de_outra_duracao(gamma_fee, clob_a2):
    """Slug de 5m devolvendo a janela de 15m: a duração não confere, recusa.

    Este caso apareceu sozinho, num teste que mapeava a fixture errada — e é
    exatamente a classe de erro que o adendo 3 pede para pegar.
    """
    routes = {
        "https://gamma.test/markets/slug/btc-updown-5m-1786891500": gamma_fee,
        f"https://clob.test/clob-markets/{gamma_fee['conditionId']}": clob_a2,
    }
    discovery, _ = _discovery(routes, durations=[300])
    assert await discovery.discover(ahead=0, keyset_fallback=False) == []


async def test_discover_tenta_as_duas_variantes_do_slug_horario(gamma_hourly, clob_a2):
    """A variante COM ano é testada primeiro; achando, não testa a sem ano."""
    hourly = dict(gamma_hourly)
    routes = {
        "https://gamma.test/markets/slug/bitcoin-up-or-down-august-16-2026-10am-et": hourly,
        f"https://clob.test/clob-markets/{hourly['conditionId']}": clob_a2,
    }
    discovery, http = _discovery(routes, durations=[HOURLY_DURATION_SECONDS])
    markets = await discovery.discover(ahead=0, keyset_fallback=False)
    assert len(markets) == 1
    assert markets[0].resolution is ResolutionKind.BINANCE_CANDLE
    assert not any("august-16-10am" in url for url in http.calls)


async def test_discover_cai_para_variante_sem_ano(gamma_hourly, clob_a2):
    hourly = dict(gamma_hourly)
    hourly["slug"] = "bitcoin-up-or-down-august-16-10am-et"
    routes = {
        "https://gamma.test/markets/slug/bitcoin-up-or-down-august-16-10am-et": hourly,
        f"https://clob.test/clob-markets/{hourly['conditionId']}": clob_a2,
    }
    discovery, http = _discovery(routes, durations=[HOURLY_DURATION_SECONDS])
    markets = await discovery.discover(ahead=0, keyset_fallback=False)
    assert len(markets) == 1
    # a variante com ano foi tentada e deu 404
    assert any("2026-10am" in url for url in http.calls)


async def test_discover_descarta_slug_que_resolveu_mercado_antigo(
    gamma_stale_slug, clob_a2
):
    """O caso do adendo 3: a Gamma responde 200 com a janela de 2025.

    Sem validação, este mercado morto entraria na lista como se fosse a janela
    corrente — com preço 1.00/0.00 e acceptingOrders=false.
    """
    routes = {
        "https://gamma.test/markets/slug/bitcoin-up-or-down-august-16-2026-2pm-et": (
            gamma_stale_slug
        ),
        f"https://clob.test/clob-markets/{gamma_stale_slug['conditionId']}": clob_a2,
    }
    discovery, http = _discovery(routes, durations=[HOURLY_DURATION_SECONDS])
    markets = await discovery.discover(ahead=0, keyset_fallback=False)
    assert markets == []
    # e não gastou request no CLOB para um mercado que já sabia estar morto
    assert not any("clob-markets" in url for url in http.calls)


async def test_slug_morto_nao_faz_tentar_a_outra_variante(gamma_stale_slug):
    """Se a variante com ano existe mas está morta, não adianta tentar a sem ano
    — é o mesmo mercado antigo. Uma resposta ruim não vira duas requisições."""
    routes = {
        "https://gamma.test/markets/slug/bitcoin-up-or-down-august-16-2026-2pm-et": (
            gamma_stale_slug
        ),
        "https://gamma.test/markets/slug/bitcoin-up-or-down-august-16-2pm-et": (
            gamma_stale_slug
        ),
    }
    discovery, http = _discovery(routes, durations=[HOURLY_DURATION_SECONDS])
    await discovery.discover(ahead=0, keyset_fallback=False)
    assert not any(url.endswith("august-16-2pm-et") for url in http.calls)


async def test_discover_aceita_janela_que_confere(gamma_hourly, clob_a2):
    """Contraprova: com endDate batendo com a janela pedida, entra normalmente."""
    routes = {
        "https://gamma.test/markets/slug/bitcoin-up-or-down-august-16-2026-10am-et": (
            gamma_hourly
        ),
        f"https://clob.test/clob-markets/{gamma_hourly['conditionId']}": clob_a2,
    }
    discovery, _ = _discovery(routes, durations=[HOURLY_DURATION_SECONDS])
    markets = await discovery.discover(ahead=0, keyset_fallback=False)
    assert len(markets) == 1
    assert markets[0].slug == gamma_hourly["slug"]


async def test_colisao_de_ano_no_discover(gamma_hourly_current, gamma_stale_slug, clob_a2):
    """A variante COM ano é tentada primeiro e é a que vale.

    Se a ordem invertesse, a descoberta pegaria o mercado de 2025 — que
    responde 200 no slug sem ano.
    """
    routes = {
        "https://gamma.test/markets/slug/bitcoin-up-or-down-august-16-2026-2pm-et": (
            gamma_hourly_current
        ),
        "https://gamma.test/markets/slug/bitcoin-up-or-down-august-16-2pm-et": (
            gamma_stale_slug
        ),
        f"https://clob.test/clob-markets/{gamma_hourly_current['conditionId']}": clob_a2,
    }
    # "agora" dentro da janela das 14h ET (18:00-19:00Z de 2026-08-16)
    discovery, http = _discovery(
        routes, durations=[HOURLY_DURATION_SECONDS], now=1786905000.0
    )
    markets = await discovery.discover(ahead=0, keyset_fallback=False)
    assert len(markets) == 1
    assert markets[0].slug == "bitcoin-up-or-down-august-16-2026-2pm-et"
    assert markets[0].resolution is ResolutionKind.BINANCE_CANDLE
    # a variante sem ano (a de 2025) nem chegou a ser consultada
    assert not any(url.endswith("august-16-2pm-et") for url in http.calls)


async def test_discover_slug_404_nao_quebra():
    discovery, _ = _discovery({})
    assert await discovery.discover(ahead=1, keyset_fallback=False) == []


async def test_keyset_fallback_filtra_os_dois_padroes(gamma_fee, gamma_hourly, clob_a2):
    outro = {"slug": "trump-wins-2028", "conditionId": "0xdead"}
    routes = {
        "https://gamma.test/markets/keyset": {
            "markets": [gamma_fee, gamma_hourly, outro],
            "next_cursor": "LTE=",
        },
        f"https://clob.test/clob-markets/{gamma_fee['conditionId']}": clob_a2,
        f"https://clob.test/clob-markets/{gamma_hourly['conditionId']}": clob_a2,
    }
    discovery, _ = _discovery(routes, assets=["btc"])
    markets = await discovery.discover(ahead=0, keyset_fallback=True)
    slugs = {m.slug for m in markets}
    assert gamma_fee["slug"] in slugs
    assert gamma_hourly["slug"] in slugs
    assert "trump-wins-2028" not in slugs


async def test_keyset_para_no_cursor_sentinela(gamma_fee, clob_a2):
    routes = {
        "https://gamma.test/markets/keyset": {"markets": [], "next_cursor": "LTE="},
    }
    discovery, http = _discovery(routes)
    await discovery.discover(ahead=0, keyset_fallback=True)
    assert http.calls.count("https://gamma.test/markets/keyset") == 1


async def test_zumbi_vindo_do_keyset_e_marcado_nao_operavel(gamma_zombie, clob_a2):
    routes = {
        "https://gamma.test/markets/keyset": {
            "markets": [gamma_zombie],
            "next_cursor": "LTE=",
        },
        f"https://clob.test/clob-markets/{gamma_zombie['conditionId']}": clob_a2,
    }
    discovery, _ = _discovery(routes)
    markets = await discovery.discover(ahead=0, keyset_fallback=True)
    assert len(markets) == 1
    assert not markets[0].operable


async def test_keyset_manda_filtro_de_data(gamma_fee):
    """A query precisa restringir end_date — closed=false não é confiável."""
    capturado: dict[str, Any] = {}

    async def http_get_json(url: str, params: dict[str, Any] | None) -> Any:
        if url.endswith("/markets/keyset"):
            capturado.update(params or {})
            return {"markets": [], "next_cursor": "LTE="}
        return None

    discovery = MarketDiscovery(
        http_get_json=http_get_json,
        gamma_url="https://gamma.test",
        clob_url="https://clob.test",
        assets=["btc"],
        probe_durations_seconds=[300],
        clock=lambda: float(NOW_EPOCH_TESTES),
    )
    await discovery.discover(ahead=0, keyset_fallback=True)
    assert capturado["closed"] == "false"
    assert "end_date_min" in capturado
    assert "end_date_max" in capturado
    # janela de +2h
    assert capturado["end_date_min"] < capturado["end_date_max"]


@pytest.mark.parametrize(
    "slug,esperado",
    [
        ("btc-updown-5m-1", True),
        ("bitcoin-up-or-down-august-16-10am-et", True),
        ("eth-updown-15m-1", False),  # asset não configurado
        ("bitcoin-up-or-down-august-16", False),  # não termina em -et
        ("", False),
    ],
)
def test_reconhecimento_de_slug(slug, esperado):
    discovery, _ = _discovery({}, assets=["btc"])
    assert discovery._is_updown_slug(slug) is esperado


class TestConditionIdNaUrl:
    """O `conditionId` vem do FIO e ia cru para dentro de um caminho de URL.

    `f"{clob_url}/clob-markets/{condition_id}"` sem passar por nada. Um valor
    com `../`, `?`, `#` ou `//` mudaria o caminho, a query ou o host da
    requisição. Não é hipótese sobre má-fé da Polymarket: uma resposta
    malformada, um proxy no meio ou um campo renomeado bastam.
    """

    @pytest.mark.parametrize(
        "valor",
        [
            "0xabc123def456",
            "ABC123DEF456",
            "abc123",
            "0XABC",
        ],
    )
    def test_hash_hexadecimal_passa(self, valor):
        from pulsearb.markets.discovery import seguro_na_url

        assert seguro_na_url(valor)

    @pytest.mark.parametrize(
        "valor",
        [
            "../admin",
            "abc/def",
            "abc?token=x",
            "abc#frag",
            "//evil.example.com/x",
            "abc def",
            "abc%2f..",
            "",
            "0x",
            "naohex",
        ],
    )
    def test_qualquer_coisa_fora_do_hex_e_recusada(self, valor):
        from pulsearb.markets.discovery import seguro_na_url

        assert not seguro_na_url(valor)

    def test_a_checagem_e_do_CONJUNTO_e_nao_do_comprimento(self):
        """Exigir 64 dígitos exatos faria o dia em que o servidor mudar o
        formato virar "nenhuma janela existe" — pior que o problema."""
        from pulsearb.markets.discovery import seguro_na_url

        assert seguro_na_url("ab")
        assert seguro_na_url("a" * 200)

    def test_normalizar_condition_id_NAO_serve_para_isto(self):
        """Ela normaliza a grafia para comparação e deixa passar qualquer
        caractere. Usá-la como validação seria o engano fácil."""
        from pulsearb.feeds.poly_ws import normalizar_condition_id
        from pulsearb.markets.discovery import seguro_na_url

        assert normalizar_condition_id("../admin") == "../admin"
        assert not seguro_na_url("../admin")
