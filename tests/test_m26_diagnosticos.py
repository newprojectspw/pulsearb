"""M2.6 — diagnósticos que transformam zero silencioso em achado.

Três zeros do relatório real eram indistinguíveis de bug:
`janelas_com_pool_de_reward: 0`, `vazio_desde_o_snapshot` em massa atribuído a
truncagem, e `gaps: rtds silencio 837s` sem escopo. Cada um destes testes
trava o diagnóstico que nomeia a causa.
"""

from __future__ import annotations

from pulsearb.analysis.integrity import MonitorDeIntegridade
from pulsearb.analysis.rewards import simular
from pulsearb.feeds.poly_ws import forma_do_book


class _JanelaFalsa:
    def __init__(self, reward_meta):
        self.reward_meta = reward_meta
        self.books = {}
        self.duracao_s = 300
        self.slug = "x"


# ─────────────────────────────── BUG 5: por que não há pool de reward


def test_zero_pool_diz_a_causa_em_vez_de_ficar_mudo():
    saida = simular(
        [
            _JanelaFalsa({"rewards_daily_rate": None, "rewards_max_spread": 1.5}),
            _JanelaFalsa({"rewards_daily_rate": 0, "rewards_max_spread": 1.5}),
            _JanelaFalsa({"rewards_daily_rate": 100.0, "rewards_max_spread": None}),
            _JanelaFalsa({}),
        ]
    )

    assert saida["janelas_com_pool_de_reward"] == 0
    motivos = saida["janelas_sem_pool_de_reward"]["por_motivo"]
    assert motivos["sem_taxa_diaria"] == 1
    assert motivos["taxa_diaria_zero"] == 1
    assert motivos["sem_max_spread"] == 1
    assert motivos["sem_reward_meta"] == 1


def test_campos_presentes_separam_achado_de_defeito():
    """`sem_taxa_diaria` em massa = os mercados não têm pool (achado sobre o
    programa de rewards). `sem_max_spread` com taxa presente = campo faltando
    (defeito recuperável). O relatório precisa deixar escolher qual é."""
    saida = simular(
        [_JanelaFalsa({"rewards_daily_rate": 100.0, "rewards_max_spread": None})]
    )
    bloco = saida["janelas_sem_pool_de_reward"]

    assert bloco["campos_presentes"]["rewards_daily_rate"] == 1
    assert bloco["campos_ausentes"]["rewards_max_spread"] == 1


# ─────────────────── BUG 4: a forma do snapshot, e a premissa corrigida


def test_forma_do_book_denuncia_chave_com_outro_nome():
    """O defeito do `price_change` (API_NOTES 6.1b) de novo, se acontecer.

    `bids`/`asks` ausentes e o lado vindo com outro nome produz exatamente o
    mesmo zero que "o servidor mandou o lado vazio" — e o conserto é oposto.
    """
    assert forma_do_book({"bids": [{"price": "1"}], "asks": []}) == "bids+asks"
    assert forma_do_book({"buys": [{"price": "1"}]}) == "buys+sells"
    assert forma_do_book({"levels": [{"price": "1", "size": "2"}]}).startswith(
        "__desconhecida__"
    )
    assert forma_do_book({"asset_id": "x"}) == "__sem_lista__"


def test_monitor_mede_niveis_e_recomenda_retencao():
    monitor = MonitorDeIntegridade()
    for i in range(100):
        # snapshots com 2 a 11 níveis por lado
        n = 2 + (i % 10)
        monitor.observar(
            {
                "event_type": "book",
                "asset_id": "tok",
                "timestamp": str(1000 + i),
                "bids": [
                    {"price": f"{0.50 - k * 0.01:.2f}", "size": "10"} for k in range(n)
                ],
                "asks": [
                    {"price": f"{0.51 + k * 0.01:.2f}", "size": "10"} for k in range(n)
                ],
            },
            (1000 + i) * 10**6,
        )
    monitor.finalizar()

    bloco = monitor.resumo()["snapshots_de_livro"]
    assert bloco["eventos"] == 100
    assert bloco["formas"] == {"bids+asks": 100}
    assert bloco["com_bid_vazio"] == 0
    # p99 dos níveis vistos é a recomendação de --niveis-por-lado
    assert bloco["niveis_recomendados_por_lado"] == 11
    assert bloco["niveis_por_lado"]["bid"]["p50"] is not None


def test_lado_vazio_no_snapshot_e_contado_como_tal():
    """A premissa do BUG 4 estava errada e o teste registra a correção:
    `--niveis-por-lado` trunca o BookTimeline da passada 2; este monitor lê o
    evento CRU na passada 1. Lado vazio aqui é o evento parseando vazio."""
    monitor = MonitorDeIntegridade()
    monitor.observar(
        {
            "event_type": "book",
            "asset_id": "tok",
            "timestamp": "1000",
            "bids": [],
            "asks": [{"price": "0.51", "size": "10"}],
        },
        10**9,
    )
    monitor.finalizar()

    bloco = monitor.resumo()["snapshots_de_livro"]
    assert bloco["com_bid_vazio"] == 1
    assert bloco["com_ask_vazio"] == 0
    assert "niveis-por-lado" in bloco["nota"]


def test_desordem_de_carimbo_mede_a_magnitude():
    """"O buffer é insuficiente" precisa de um número. O atraso de carimbo é
    outro eixo que a ordem de chegada, e só a magnitude diz se aumentar o
    buffer do leitor resolveria alguma coisa."""
    monitor = MonitorDeIntegridade()
    monitor.observar(
        {
            "event_type": "book",
            "asset_id": "tok",
            "timestamp": "10000",
            "bids": [{"price": "0.49", "size": "10"}],
            "asks": [{"price": "0.51", "size": "10"}],
        },
        10**9,
    )
    # delta com carimbo 2s ATRÁS do snapshot
    monitor.observar(
        {
            "event_type": "price_change",
            "timestamp": "8000",
            "price_changes": [
                {
                    "asset_id": "tok",
                    "price": "0.49",
                    "size": "5",
                    "side": "BUY",
                    "best_bid": "0.49",
                    "best_ask": "0.51",
                }
            ],
        },
        2 * 10**9,
    )
    monitor.finalizar()

    bloco = monitor.resumo()["desordem_de_carimbo"]
    assert bloco["deltas"] == 1
    assert bloco["atraso_ms"]["max"] == 2000.0
    assert bloco["amostras"] == 1
