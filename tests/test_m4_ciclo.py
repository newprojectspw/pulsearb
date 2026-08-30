"""3.13 — o ciclo que faltava: fio → estado → decisão.

O que se testa aqui é o ROTEAMENTO e a saúde do feed. O modelo, os portões e a
orquestração de janelas já têm os seus testes; este arquivo cobre o pedaço que
não existia: quem recebe o evento do fio e o que acontece com ele.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pulsearb.execution import ExecutorSombra
from pulsearb.feeds.base import FeedEvent
from pulsearb.live.ciclo import SILENCIO_DO_PRECO_S, CicloAoVivo, alimentar
from pulsearb.live.livros import LivrosAoVivo
from pulsearb.live.motor import ConfigDoMotor, MotorAoVivo
from pulsearb.live.precos import PrecosAoVivo
from pulsearb.live.rastreador import RastreadorDeJanelas
from pulsearb.markets.discovery import DiscoveredMarket
from pulsearb.risk import PortaoDeRisco
from pulsearb.settings import Mode, RiskSettings

E18 = 10**18
FECHA_EPOCH = 1_787_000_300.0
ABRE_EPOCH = FECHA_EPOCH - 300
ABRE_MS = int(ABRE_EPOCH * 1000)
ABRE_NS = int(ABRE_EPOCH * 1e9)


def _mercado(asset: str = "btc", condition_id: str = "0xaa"):
    iso = datetime.fromtimestamp(FECHA_EPOCH, UTC).isoformat().replace("+00:00", "Z")
    return DiscoveredMarket(
        slug=f"{asset}-updown-5m-1787000300",
        condition_id=condition_id,
        asset=asset,
        resolution="chainlink_twap",
        token_id_by_outcome={"Up": f"{asset}-up", "Down": f"{asset}-down"},
        tick_size=0.01,
        min_order_size=5.0,
        fee_rate=0.0,
        fee_exponent=1.0,
        fee_taker_only=True,
        fee_rebate_rate=0.2,
        accepting_orders=True,
        end_date_iso=iso,
        operable=True,
        raw_gamma={"endDate": iso},
    )


def _evento_rtds(
    asset: str = "btc",
    *,
    valor_e18: int = 78_000 * E18,
    ts_servidor_ms: int = ABRE_MS,
    chegada_ns: int = ABRE_NS,
    topic: str = "crypto_prices_twap_sixty",
    full_accuracy: bool = True,
):
    payload = {
        "symbol": f"{asset}/usd",
        "timestamp": ts_servidor_ms,
        "value": str(valor_e18 / E18),
    }
    if full_accuracy:
        payload["full_accuracy_value"] = str(valor_e18)
    return FeedEvent(
        source="rtds",
        ts_mono_ns=chegada_ns,
        ts_wall_ns=chegada_ns,
        raw=b"",
        parsed={"topic": topic, "payload": payload},
    )


def _evento_poly(token: str = "btc-up", ask: str = "0.30", chegada_ns: int = ABRE_NS):
    return FeedEvent(
        source="poly_ws",
        ts_mono_ns=chegada_ns,
        ts_wall_ns=chegada_ns,
        raw=b"",
        parsed=[
            {
                "event_type": "book",
                "asset_id": token,
                "bids": [{"price": "0.29", "size": "500"}],
                "asks": [{"price": ask, "size": "500"}],
            }
        ],
    )


@pytest.fixture
def ciclo(tmp_path):
    precos = PrecosAoVivo()
    portao = PortaoDeRisco(
        RiskSettings(),
        Mode.SHADOW,
        caminho_do_registro=tmp_path / "registro.json",
        hoje="2026-08-25",
        relogio_do_servidor=precos.relogio,
    )
    motor = MotorAoVivo(
        rastreador=RastreadorDeJanelas(),
        livros=LivrosAoVivo(),
        precos=precos,
        executor=ExecutorSombra(portao, caminho_do_diario=tmp_path / "diario.jsonl"),
        config=ConfigDoMotor(),
    )
    return CicloAoVivo(motor=motor)


class TestRoteamento:
    def test_tick_de_twap_vira_preco(self, ciclo):
        ciclo.on_feed_event(_evento_rtds())

        assert ciclo.contagem["preco"] == 1
        assert ciclo.motor.precos.por_ativo["btc"].twap.last_price == 78_000.0

    def test_spot_da_binance_NAO_entra(self, ciclo):
        """`crypto_prices` chega pelo mesmo fio e não é a âncora.

        A âncora verificada (§13.8) é definida sobre `twap_sixty`. Misturar os
        dois moveria a âncora para um observável que nunca foi validado — e o
        backtest também só alimenta `streams_e18` com o twap.
        """
        ciclo.on_feed_event(_evento_rtds(topic="crypto_prices"))

        assert ciclo.contagem["rtds_outro_topico"] == 1
        assert "btc" not in ciclo.motor.precos.por_ativo

    def test_evento_de_livro_vira_livro(self, ciclo):
        ciclo.on_feed_event(_evento_poly())

        assert ciclo.contagem["livro"] == 1
        assert ciclo.motor.livros.confiavel("btc-up", agora_ns=ABRE_NS)

    def test_fonte_desconhecida_e_CONTADA(self, ciclo):
        """"Não chegou nada" e "chegou algo que não sei ler" têm consertos
        opostos. Foi essa distinção que faltou no `price_change` (§6.1b)."""
        ciclo.on_feed_event(
            FeedEvent(source="binance_ws", ts_mono_ns=0, ts_wall_ns=0, raw=b"", parsed={})
        )

        assert ciclo.contagem["fonte_desconhecida"] == 1

    def test_evento_sem_valor_exato_e_descartado_e_contado(self, ciclo):
        """Sem o inteiro e18 não dá para casar com a âncora.

        `value` como float já perdeu os dígitos na origem; convertê-lo criaria
        precisão falsa. Mesmo descarte que o backtest conta em
        `sem_valor_exato`.
        """
        evento = _evento_rtds(full_accuracy=False)
        evento.parsed["payload"]["value"] = 78_000.5  # float, não string

        ciclo.on_feed_event(evento)

        assert ciclo.contagem["preco_sem_valor_exato"] == 1
        assert "btc" not in ciclo.motor.precos.por_ativo

    def test_evento_sem_carimbo_do_servidor_e_descartado(self, ciclo):
        ciclo.on_feed_event(_evento_rtds(ts_servidor_ms=0))

        assert ciclo.contagem["preco_sem_carimbo_do_servidor"] == 1

    def test_alimentar_empurra_uma_sequencia(self, ciclo):
        # Carimbos distintos: dois ticks com o MESMO carimbo são o tick
        # repetido da redundância, e a dedupe os conta uma vez só.
        alimentar(
            ciclo,
            [
                _evento_rtds(ts_servidor_ms=ABRE_MS),
                _evento_poly(),
                _evento_rtds(ts_servidor_ms=ABRE_MS + 1000),
            ],
        )

        assert ciclo.contagem["preco"] == 2
        assert ciclo.contagem["livro"] == 1


class TestDeduplicacaoDoTickRepetido:
    """O RTDS é assinado em N conexões redundantes (default 2).

    O preço da redundância é o MESMO tick chegando N vezes. Contá-lo N vezes
    estragaria a volatilidade realizada e o sensor de anomalia de tempo — dois
    "atrasos" por tick.
    """

    def test_o_mesmo_carimbo_duas_vezes_conta_uma(self, ciclo):
        evento = _evento_rtds(ts_servidor_ms=ABRE_MS)
        ciclo.on_feed_event(evento)
        ciclo.on_feed_event(_evento_rtds(ts_servidor_ms=ABRE_MS))

        assert ciclo.contagem["preco"] == 1
        assert ciclo.contagem["preco_repetido"] == 1
        assert len(ciclo.motor.precos.por_ativo["btc"].serie_e18) == 1

    def test_o_repetido_nao_alimenta_o_sensor_de_tempo(self, ciclo):
        """Dois "atrasos" pelo mesmo tick enviesariam a mediana."""
        for _ in range(4):
            ciclo.on_feed_event(_evento_rtds(ts_servidor_ms=ABRE_MS))

        assert ciclo.motor.precos.relogio.resumo(
            agora_ms=ABRE_MS + 100
        )["ticks_vistos"] == 1

    def test_ativos_diferentes_com_o_mesmo_carimbo_NAO_sao_repetidos(self, ciclo):
        """O RTDS entrega os oito ativos com carimbos que podem coincidir."""
        ciclo.on_feed_event(_evento_rtds("btc", ts_servidor_ms=ABRE_MS))
        ciclo.on_feed_event(_evento_rtds("eth", ts_servidor_ms=ABRE_MS))

        assert ciclo.contagem["preco"] == 2
        assert "preco_repetido" not in ciclo.contagem

    def test_tick_FORA_DE_ORDEM_nao_e_descartado(self, ciclo):
        """Só o carimbo EXATO conta como repetido.

        O backtest guarda os fora de ordem (`streams_e18` acumula e a âncora
        resolve por bisect); jogá-los fora aqui faria as duas pontas verem
        séries diferentes.
        """
        ciclo.on_feed_event(_evento_rtds(ts_servidor_ms=ABRE_MS + 2000))
        ciclo.on_feed_event(_evento_rtds(ts_servidor_ms=ABRE_MS + 1000))

        assert ciclo.contagem["preco"] == 2

    def test_a_memoria_e_limitada(self, ciclo):
        """Uma rodada de 24 h não pode acumular 700 mil carimbos por ativo."""
        from pulsearb.live.ciclo import CARIMBOS_LEMBRADOS

        for i in range(CARIMBOS_LEMBRADOS + 50):
            ciclo.on_feed_event(_evento_rtds(ts_servidor_ms=ABRE_MS + i * 1000))

        assert len(ciclo._vistos["btc"]) == CARIMBOS_LEMBRADOS


class TestOSensorDeTempoRecebeAChegada:
    def test_o_tick_alimenta_a_trava_de_relogio(self, ciclo):
        """Sem `chegada_ms` o portão diria "não sei", que é recusa.

        É o elo que o item 3.10 deixou pendente: a fonte existia e nada a
        alimentava.
        """
        for i in range(5):
            ciclo.on_feed_event(
                _evento_rtds(
                    ts_servidor_ms=ABRE_MS + i * 1000,
                    chegada_ns=(ABRE_NS + i * 1_000_000_000) + 40_000_000,
                )
            )

        atraso = ciclo.motor.precos.relogio.atraso_ms(
            agora_ms=(ABRE_NS // 1_000_000) + 5_000
        )
        assert atraso == pytest.approx(40.0)


class TestSaudeDoFeed:
    def test_sem_nenhum_preco_o_feed_NAO_e_saudavel(self, ciclo):
        """Bot recém-subido não sabe nada, e não saber não autoriza."""
        assert ciclo.feeds_saudaveis(agora_ns=ABRE_NS) is False

    def test_com_preco_fresco_e_saudavel(self, ciclo):
        ciclo.on_feed_event(_evento_rtds())

        assert ciclo.feeds_saudaveis(agora_ns=ABRE_NS + 1_000_000_000) is True

    def test_preco_velho_derruba_a_saude(self, ciclo):
        ciclo.on_feed_event(_evento_rtds())
        velho = ABRE_NS + int((SILENCIO_DO_PRECO_S + 1) * 1e9)

        assert ciclo.feeds_saudaveis(agora_ns=velho) is False
        assert "btc" in ciclo.precos_velhos(agora_ns=velho)

    def test_UM_ativo_mudo_derruba_todos(self, ciclo):
        """O buraco que este ciclo fecha, e ele não tinha dono.

        `PrecosAoVivo` devolve o último preço de um ativo SEM olhar a idade
        dele. Um ativo mudo entre sete saudáveis decidiria com preço velho, e
        nada mais no caminho pegaria isso — o portão `feed_parado` olha o
        feed, não o ativo.

        Fechar tudo por causa de um é conservador e é o lado certo para
        errar: com entrada única por janela, o custo de parar é uma janela
        perdida; o de operar com preço velho é uma posição tomada contra um
        mercado que já se moveu.
        """
        saudaveis = ("eth", "sol", "xrp", "doge", "bnb", "hype", "zec")
        agora_ns = ABRE_NS + int(20 * 1e9)
        for asset in saudaveis:
            ciclo.on_feed_event(
                _evento_rtds(asset, chegada_ns=agora_ns - 1_000_000_000)
            )
        ciclo.on_feed_event(_evento_rtds("btc", chegada_ns=ABRE_NS))

        assert ciclo.feeds_saudaveis(agora_ns=agora_ns) is False
        velhos = ciclo.precos_velhos(agora_ns=agora_ns)
        assert set(velhos) == {"btc"}

    def test_o_resumo_NOMEIA_o_ativo_mudo(self, ciclo):
        """"Feed parado" sem dizer qual ativo não é alarme acionável."""
        ciclo.on_feed_event(_evento_rtds("btc", chegada_ns=ABRE_NS))
        ciclo.on_feed_event(_evento_rtds("eth", chegada_ns=ABRE_NS + int(20 * 1e9)))
        agora_ns = ABRE_NS + int(21 * 1e9)

        resumo = ciclo.resumo(agora_epoch=ABRE_EPOCH + 21, agora_ns=agora_ns)

        assert resumo["feeds_saudaveis"] is False
        assert list(resumo["precos_velhos_s"]) == ["btc"]
        assert resumo["ativos_com_preco"] == 2


class TestPassoDeDecisao:
    def test_o_passo_usa_a_saude_calculada(self, ciclo):
        """O motor não recebe `feeds_saudaveis` de fora: o ciclo o calcula.

        Se alguém voltar a passá-lo como parâmetro, o buraco do ativo mudo
        volta junto.
        """
        ciclo.on_descoberta([_mercado()], agora_epoch=ABRE_EPOCH + 1)
        # Sem preço nenhum: a saúde é falsa, e o motor recusa por feed parado.
        tentadas = ciclo.passo(agora_epoch=FECHA_EPOCH - 100, agora_ns=ABRE_NS)

        assert tentadas == 0

    def test_a_descoberta_alimenta_o_rastreador(self, ciclo):
        ciclo.on_descoberta([_mercado()], agora_epoch=ABRE_EPOCH + 1)

        assert ciclo.ativos_em_jogo(agora_epoch=ABRE_EPOCH + 1) == {"btc"}
        assert ciclo.contagem["descoberta"] == 1
