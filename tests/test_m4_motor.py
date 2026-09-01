"""M4 — o laço que junta tudo e entrega ao executor.

O que se testa aqui não é o modelo nem os portões: é a ORQUESTRAÇÃO. Quem
pergunta o quê, em que ordem, e o que acontece quando falta uma peça. A
resposta a "falta uma peça" é sempre a mesma — não opera, e conta o motivo — e
é isso que faz `pulos` responder à pergunta operacional que mais vai ser feita:
o bot está vivo e não opera, por quê?
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pulsearb.execution import ExecutorSombra
from pulsearb.live.livros import LivrosAoVivo
from pulsearb.live.motor import (
    PULOU_FORA_DA_FAIXA,
    PULOU_JA_OPEROU,
    PULOU_SEM_ANCORA,
    PULOU_SEM_LIVRO,
    PULOU_VOL_CRUA,
    ConfigDoMotor,
    MotorAoVivo,
)
from pulsearb.live.precos import PrecosAoVivo
from pulsearb.live.rastreador import RastreadorDeJanelas
from pulsearb.markets.discovery import DiscoveredMarket
from pulsearb.risk import PortaoDeRisco
from pulsearb.settings import Mode, RiskSettings

E18 = 10**18
FECHA_EPOCH = 1_787_000_300.0          # janela de 5m: abre em 1_787_000_000
ABRE_EPOCH = FECHA_EPOCH - 300
ABRE_MS = int(ABRE_EPOCH * 1000)


def _mercado():
    iso = datetime.fromtimestamp(FECHA_EPOCH, UTC).isoformat().replace("+00:00", "Z")
    return DiscoveredMarket(
        slug="btc-updown-5m-1787000300",
        condition_id="0xaa",
        asset="btc",
        resolution="chainlink_twap",
        token_id_by_outcome={"Up": "tok-up", "Down": "tok-down"},
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


def _snapshot(token: str, ask: str = "0.30"):
    return {
        "event_type": "book",
        "asset_id": token,
        "bids": [{"price": "0.29", "size": "500"}],
        "asks": [{"price": ask, "size": "500"}],
    }


class Cenario:
    """Um mercado montado à mão, com o tempo sob controle do teste."""

    def __init__(self, tmp_path, *, config=None, **risco):
        self.rastreador = RastreadorDeJanelas()
        self.livros = LivrosAoVivo()
        self.precos = PrecosAoVivo()
        self.portao = PortaoDeRisco(
            RiskSettings(**risco),
            Mode.SHADOW,
            caminho_do_registro=tmp_path / "registro.json",
            hoje="2026-08-25",
        )
        self.executor = ExecutorSombra(
            self.portao, caminho_do_diario=tmp_path / "diario.jsonl"
        )
        self.motor = MotorAoVivo(
            rastreador=self.rastreador,
            livros=self.livros,
            precos=self.precos,
            executor=self.executor,
            config=config or ConfigDoMotor(),
        )
        self.rastreador.atualizar([_mercado()], agora_epoch=ABRE_EPOCH + 1)

    def alimentar_precos(self, *, n=40, desde_ms=ABRE_MS, preco=78_000, passo=100):
        """Preços a partir da ABERTURA, para a âncora existir."""
        for i in range(n):
            self.precos.anotar(
                "btc",
                valor_e18=int((preco + i * passo) * E18),
                ts_servidor_ms=desde_ms + i * 1000,
            )

    def alimentar_livros(self, *, ts_ns, ask_up="0.30", ask_down="0.30"):
        self.livros.aplicar(_snapshot("tok-up", ask_up), ts_ns=ts_ns)
        self.livros.aplicar(_snapshot("tok-down", ask_down), ts_ns=ts_ns)

    def tick(self, *, faltam=100.0, feeds_saudaveis=True):
        agora = FECHA_EPOCH - faltam
        return self.motor.tick(
            agora_epoch=agora,
            agora_ns=int(agora * 1e9),
            feeds_saudaveis=feeds_saudaveis,
        )


class TestCaminhoFeliz:
    def test_com_tudo_no_lugar_o_motor_tenta(self, tmp_path):
        cenario = Cenario(tmp_path)
        cenario.alimentar_precos()
        cenario.alimentar_livros(ts_ns=int((FECHA_EPOCH - 100) * 1e9))

        assert cenario.tick(faltam=100.0) == 1
        assert cenario.executor.intencoes
        intencao = cenario.executor.intencoes[0]
        assert intencao.slug == "btc-updown-5m-1787000300"
        assert intencao.seconds_left == pytest.approx(100.0)
        assert intencao.profundidade_no_topo is not None

    def test_uma_entrada_por_janela(self, tmp_path):
        """O M2.7 mediu que mais entradas sobem PnL e drawdown na MESMA
        proporção: é alavancagem, não borda."""
        cenario = Cenario(tmp_path)
        cenario.alimentar_precos()
        cenario.alimentar_livros(ts_ns=int((FECHA_EPOCH - 100) * 1e9))

        assert cenario.tick(faltam=100.0) == 1
        cenario.alimentar_livros(ts_ns=int((FECHA_EPOCH - 95) * 1e9))
        assert cenario.tick(faltam=95.0) == 0
        assert cenario.motor.pulos[PULOU_JA_OPEROU] == 1


class TestPortasFechadas:
    """Cada peça que falta tem nome próprio — senão a pergunta não tem resposta."""

    def test_sem_ancora_nao_opera(self, tmp_path):
        # Preços só a partir de DEPOIS da abertura: o bot subiu tarde.
        cenario = Cenario(tmp_path)
        cenario.alimentar_precos(desde_ms=ABRE_MS + 120_000)
        cenario.alimentar_livros(ts_ns=int((FECHA_EPOCH - 100) * 1e9))

        assert cenario.tick(faltam=100.0) == 0
        assert cenario.motor.pulos[PULOU_SEM_ANCORA] == 1

    def test_volatilidade_crua_nao_opera(self, tmp_path):
        # Menos de 20 retornos: o modelo devolve número, mas ele não descreve
        # nada ainda.
        cenario = Cenario(tmp_path)
        cenario.alimentar_precos(n=5)
        cenario.alimentar_livros(ts_ns=int((FECHA_EPOCH - 100) * 1e9))

        assert cenario.tick(faltam=100.0) == 0
        assert cenario.motor.pulos[PULOU_VOL_CRUA] == 1

    def test_livro_mudo_nao_opera(self, tmp_path):
        # Livro alimentado e depois calado: o feed pode estar ótimo.
        cenario = Cenario(tmp_path)
        cenario.alimentar_precos()
        cenario.alimentar_livros(ts_ns=int((FECHA_EPOCH - 200) * 1e9))

        assert cenario.tick(faltam=100.0) == 0
        assert cenario.motor.pulos[PULOU_SEM_LIVRO] == 1

    def test_fora_da_faixa_calibrada_nao_opera(self, tmp_path):
        """240s é onde o M2 mediu erro de 0,008; acima disso, 0,240."""
        cenario = Cenario(tmp_path)
        cenario.alimentar_precos()
        cenario.alimentar_livros(ts_ns=int((FECHA_EPOCH - 280) * 1e9))

        assert cenario.tick(faltam=280.0) == 0
        assert cenario.motor.pulos[PULOU_FORA_DA_FAIXA] == 1

    def test_feed_parado_chega_ate_o_portao(self, tmp_path):
        # O motor não decide sobre saúde de feed: ele repassa, e o portão
        # recusa. Uma regra, um dono.
        cenario = Cenario(tmp_path)
        cenario.alimentar_precos()
        cenario.alimentar_livros(ts_ns=int((FECHA_EPOCH - 100) * 1e9))

        cenario.tick(faltam=100.0, feeds_saudaveis=False)
        assert cenario.executor.intencoes[0].motivo == "feed_parado"
        assert not cenario.executor.intencoes[0].pode


class TestJanelaQueFecha:
    def test_fechar_libera_a_exposicao(self, tmp_path):
        """Janela que fecha e não é baixada trava o teto de exposição.

        O bot passaria a recusar tudo com `exposicao_no_teto` sem que nada
        estivesse errado no mercado.
        """
        cenario = Cenario(tmp_path, exposicao_max_usdc=2.0)
        cenario.alimentar_precos()
        cenario.alimentar_livros(ts_ns=int((FECHA_EPOCH - 100) * 1e9))
        cenario.tick(faltam=100.0)
        assert cenario.portao.registro.exposicao_total_usdc > 0

        # Passa do fechamento.
        cenario.motor.tick(
            agora_epoch=FECHA_EPOCH + 1,
            agora_ns=int((FECHA_EPOCH + 1) * 1e9),
            feeds_saudaveis=True,
        )
        assert cenario.portao.registro.exposicao_total_usdc == 0.0
        assert cenario.precos.ancoras == {}

    def test_nao_inventa_pnl_ao_fechar(self, tmp_path):
        """A baixa é de EXPOSIÇÃO. PnL adivinhado alimentaria o disjuntor com
        número inventado — e o disjuntor gruda."""
        cenario = Cenario(tmp_path)
        cenario.alimentar_precos()
        cenario.alimentar_livros(ts_ns=int((FECHA_EPOCH - 100) * 1e9))
        cenario.tick(faltam=100.0)

        cenario.motor.tick(
            agora_epoch=FECHA_EPOCH + 1,
            agora_ns=int((FECHA_EPOCH + 1) * 1e9),
            feeds_saudaveis=True,
        )
        assert cenario.portao.registro.pnl_realizado_usdc == 0.0
        assert not cenario.portao.registro.disjuntor_armado


class TestResumo:
    def test_o_resumo_junta_as_quatro_camadas(self, tmp_path):
        cenario = Cenario(tmp_path)
        cenario.alimentar_precos(n=5)
        cenario.tick(faltam=100.0)

        resumo = cenario.motor.resumo(
            agora_epoch=FECHA_EPOCH - 100, agora_ns=int((FECHA_EPOCH - 100) * 1e9)
        )
        assert "pulos" in resumo
        assert resumo["janelas"]["abertas"] == 1
        assert "livros" in resumo
        assert "precos" in resumo
        assert "de cima para baixo antes de suspeitar do modelo" in resumo["nota"]


def _resolucao(vencedor: str = "tok-up", condition_id: str = "0xaa"):
    """A forma REAL do servidor (`assets_ids` no plural, sem `asset_id`)."""
    return {
        "event_type": "market_resolved",
        "market": condition_id,
        "assets_ids": ["tok-up", "tok-down"],
        "winning_asset_id": vencedor,
        "winning_outcome": "Up" if vencedor == "tok-up" else "Down",
        "timestamp": "1787166722776",
    }


class TestAResolucaoAlimentaODisjuntor:
    """Achado P1 do Codex no #52, e era um buraco no ensaio inteiro.

    `_liquidar` fecha toda janela com `pnl=0.0` — correto, porque no
    fechamento o resultado ainda não se conhece. Só que **nada** chamava
    `registrar_resolucao` com o PnL de verdade depois. `perdas_seguidas` e
    `pnl_realizado_usdc` ficavam em zero para sempre, então a pausa por
    sequência e o disjuntor de perda do dia **nunca armavam no SHADOW** — e o
    ensaio aprovaria intenções que o LIVE equivalente já teria recusado.
    """

    def _operar(self, tmp_path, **risco):
        from pulsearb.feeds.poly_ws import resolucao_do_evento

        cenario = Cenario(tmp_path, **risco)
        cenario.alimentar_precos()
        cenario.alimentar_livros(ts_ns=int((FECHA_EPOCH - 100) * 1e9))
        cenario.tick(faltam=100.0)
        return cenario, resolucao_do_evento

    def test_a_posicao_e_indexada_pela_grafia_NORMALIZADA(self, tmp_path):
        """Erro meu, e o código já documentava a armadilha.

        A Gamma entrega `0xAA…` e o WS entrega `aa…`. Eu indexei pela grafia
        crua, então TODA resolução caía em `resolucoes_sem_posicao` — em
        silêncio, e com o disjuntor parado em zero exatamente como antes do
        conserto. É o modo de falha que `normalizar_condition_id` foi escrita
        para eliminar.
        """
        cenario, _ = self._operar(tmp_path)

        assert "aa" in cenario.motor.posicoes
        assert "0xaa" not in cenario.motor.posicoes
        posicao = cenario.motor.posicoes["aa"]
        assert posicao.slug == "btc-updown-5m-1787000300"
        assert posicao.preco_pago == pytest.approx(0.30)

    def test_perder_incrementa_a_SEQUENCIA_de_perdas(self, tmp_path):
        """O número que arma a pausa. Sem isto ele fica em zero para sempre."""
        cenario, parse = self._operar(tmp_path)
        posicao = cenario.motor.posicoes["aa"]
        perdedor = "tok-down" if posicao.lado_up else "tok-up"

        assert cenario.motor.resolver(parse(_resolucao(perdedor))) is True

        assert cenario.portao.registro.perdas_seguidas == 1
        assert cenario.portao.registro.pnl_realizado_usdc < 0

    def test_ganhar_zera_a_sequencia_e_soma_PnL(self, tmp_path):
        cenario, parse = self._operar(tmp_path)
        posicao = cenario.motor.posicoes["aa"]
        vencedor = "tok-up" if posicao.lado_up else "tok-down"
        cenario.portao.registro.perdas_seguidas = 2

        cenario.motor.resolver(parse(_resolucao(vencedor)))

        assert cenario.portao.registro.perdas_seguidas == 0
        assert cenario.portao.registro.pnl_realizado_usdc > 0

    def test_a_conta_e_a_MESMA_do_backtest(self, tmp_path):
        """`payout − custo − fee`, share vencedora paga 1,00.

        Duas contas diferentes fariam o SHADOW e o backtest discordarem sobre
        o próprio resultado — e a comparação entre os dois é o que justifica o
        SHADOW existir.
        """
        cenario, parse = self._operar(tmp_path)
        posicao = cenario.motor.posicoes["aa"]
        vencedor = "tok-up" if posicao.lado_up else "tok-down"

        cenario.motor.resolver(parse(_resolucao(vencedor)))

        esperado = posicao.shares - posicao.shares * posicao.preco_pago
        assert cenario.portao.registro.pnl_realizado_usdc == pytest.approx(
            esperado - posicao.fee_usdc
        )

    def test_o_preco_pago_ATRAVESSA_o_livro_como_no_backtest(self, tmp_path):
        """A outra metade do "mesmo caminho", e ela custou dinheiro no papel.

        Aqui ficava `preco_pago = livro.best_ask`: o topo, como se a ordem
        inteira coubesse no primeiro nível. O backtest sempre atravessou o
        livro com `simulate_taker_buy`. Com topo raso as duas contas divergem,
        e a divergência tem sinal — o SHADOW saía mais barato, sempre.

        Um SHADOW otimista por aritmética faz o ensaio de 2 semanas (item 4.2)
        aprovar uma estratégia que o backtest reprova, e a diferença pareceria
        "o mercado ao vivo é melhor".
        """
        from pulsearb.backtest.book import simulate_taker_buy

        cenario = Cenario(tmp_path)
        cenario.alimentar_precos()
        # Topo RASO: 2 shares a 0,30 e o resto a 0,40. Uma ordem de 5 shares
        # não cabe no primeiro nível — é onde as duas contas divergem.
        ts_ns = int((FECHA_EPOCH - 100) * 1e9)
        cenario.livros.aplicar(
            {
                "event_type": "book",
                "asset_id": "tok-up",
                "bids": [{"price": "0.29", "size": "500"}],
                "asks": [
                    {"price": "0.30", "size": "2"},
                    {"price": "0.40", "size": "500"},
                ],
            },
            ts_ns=ts_ns,
        )
        cenario.livros.aplicar(_snapshot("tok-down", "0.30"), ts_ns=ts_ns)
        cenario.tick(faltam=100.0)

        posicao = cenario.motor.posicoes["aa"]
        assert posicao.lado_up, "o cenário precisa entrar em Up para medir o ask raso"
        livro = cenario.livros.livro("tok-up", agora_ns=ts_ns)
        esperado = simulate_taker_buy(livro, posicao.shares)

        assert posicao.preco_pago == pytest.approx(esperado.preco_medio)
        assert posicao.preco_pago > livro.best_ask, (
            "com topo raso o preço médio TEM de ficar acima do best_ask — "
            "se não ficou, o motor voltou a ler só o topo"
        )

    def test_resolver_duas_vezes_NAO_conta_duas_vezes(self, tmp_path):
        """A resolução pode chegar por mais de um caminho (evento e consulta
        à Gamma). Somar duas vezes inflaria o disjuntor."""
        cenario, parse = self._operar(tmp_path)
        posicao = cenario.motor.posicoes["aa"]
        perdedor = "tok-down" if posicao.lado_up else "tok-up"

        cenario.motor.resolver(parse(_resolucao(perdedor)))
        primeiro = cenario.portao.registro.pnl_realizado_usdc

        assert cenario.motor.resolver(parse(_resolucao(perdedor))) is False
        assert cenario.portao.registro.pnl_realizado_usdc == primeiro
        assert cenario.portao.registro.perdas_seguidas == 1

    def test_resolucao_de_janela_que_nao_operamos_e_contada_e_nao_erro(
        self, tmp_path
    ):
        """Assinamos o livro de janela recusada de propósito, então este é o
        caso NORMAL."""
        cenario, parse = self._operar(tmp_path)

        assert (
            cenario.motor.resolver(parse(_resolucao("tok-up", "0xoutro"))) is False
        )
        assert cenario.motor.resolucoes_sem_posicao == 1

    def test_a_posicao_SOBREVIVE_ao_fechamento_da_janela(self, tmp_path):
        """A resolução chega DEPOIS do fechamento. Se `_liquidar` apagasse a
        posição, o PnL se perderia — que era o defeito."""
        cenario, parse = self._operar(tmp_path)

        cenario.tick(faltam=-10.0)  # a janela fechou: `_liquidar` roda

        assert "aa" in cenario.motor.posicoes
        assert cenario.motor.resolver(parse(_resolucao("tok-down"))) is True

    def test_evento_que_nao_decide_o_lado_devolve_a_posicao(self, tmp_path):
        """Perder o PnL seria pior que esperar: a posição volta para a fila e
        a próxima resolução ainda pode liquidá-la."""
        cenario, parse = self._operar(tmp_path)
        indeciso = _resolucao("tok-up")
        indeciso["winning_asset_id"] = "token-de-outro-mercado"
        indeciso["winning_outcome"] = None

        assert cenario.motor.resolver(parse(indeciso)) is False
        assert "aa" in cenario.motor.posicoes
