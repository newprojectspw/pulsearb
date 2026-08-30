"""O sensor de anomalia de tempo do item 3.10 — e o que ele NÃO prova.

A primeira versão deste módulo afirmava que o atraso medido é um limite
SUPERIOR da deriva do relógio. É falso, e a revisão do PR #47 pegou. Boa parte
dos testes aqui existe para que essa afirmação não volte: eles travam a
limitação, não só o comportamento.
"""

from __future__ import annotations

import pytest

from pulsearb.live.relogio import (
    AMOSTRAS_NA_JANELA,
    CARENCIA_APOS_SALTO_MS,
    IDADE_MAXIMA_MS,
    LIMIAR_DE_SALTO_MS,
    RelogioDoServidor,
)

BASE_MS = 1_787_000_000_000


class _Maquina:
    """Os dois relógios da máquina, de mentira. O teste move cada um.

    `avancar` mexe nos dois juntos, que é uma máquina saudável. `pular` mexe
    só no de parede, que é o NTP corrigindo de vez.
    """

    def __init__(self) -> None:
        self.mono_s = 1000.0
        self.parede_s = BASE_MS / 1000.0

    def avancar(self, segundos: float) -> None:
        self.mono_s += segundos
        self.parede_s += segundos

    def pular(self, segundos: float) -> None:
        self.parede_s += segundos

    def monotonico(self) -> float:
        return self.mono_s

    def parede(self) -> float:
        return self.parede_s


def _alimentar(relogio, *atrasos, asset="btc", base_ms=BASE_MS, maquina=None):
    """Um tick por atraso pedido, um por segundo — máquina saudável."""
    for i, atraso in enumerate(atrasos):
        servidor = base_ms + i * 1000
        if maquina is not None:
            maquina.avancar(1.0)
        relogio.anotar(
            asset=asset, ts_servidor_ms=servidor, chegada_ms=servidor + atraso
        )
    return base_ms + (len(atrasos) - 1) * 1000 + atrasos[-1]


def _relogio():
    maquina = _Maquina()
    return (
        RelogioDoServidor(monotonico=maquina.monotonico, parede=maquina.parede),
        maquina,
    )


class TestOQueEleMede:
    def test_sem_amostra_diz_nao_sei(self):
        """Zero nunca é resposta para "ainda não medi nada"."""
        assert RelogioDoServidor().atraso_ms(agora_ms=1) is None

    def test_a_mediana_de_uma_serie_estavel(self):
        relogio, maquina = _relogio()
        agora = _alimentar(relogio, 40, 42, 41, 43, 39, maquina=maquina)

        assert relogio.atraso_ms(agora_ms=agora) == 41.0

    def test_um_pico_isolado_nao_decide_sozinho(self):
        """Mediana e não média: um GC de meio segundo num tick só deslocaria
        a média para ~120 ms e deixaria o portão nervoso por vários segundos
        depois de o problema já ter passado."""
        relogio, maquina = _relogio()
        agora = _alimentar(relogio, 40, 41, 700, 42, 39, maquina=maquina)

        assert relogio.atraso_ms(agora_ms=agora) == 41.0

    def test_atraso_negativo_e_guardado_como_veio(self):
        """Carimbo no futuro é o que sobra de sinal de relógio local atrasado.

        Zerar o negativo cegaria o sensor justamente onde ele ainda enxerga
        depois de a cancelação comer o resto.
        """
        relogio, maquina = _relogio()
        agora = _alimentar(relogio, -300, -310, -305, maquina=maquina)

        assert relogio.atraso_ms(agora_ms=agora) == -305.0
        assert relogio.resumo(agora_ms=agora)["carimbo_no_futuro"] == 3


class TestALimitacaoQueNaoPodeSerEsquecida:
    """A cancelação entre offset e latência. Estes testes travam o DEFEITO."""

    def test_offset_grande_pode_medir_zero(self):
        """Relógio 400 ms atrasado + 400 ms de latência = medição ZERO.

        Este teste não descreve um comportamento desejável: descreve o limite
        real do sensor. Se alguém um dia "consertar" o módulo para que este
        caso acuse, terá encontrado uma medição de duas vias — e aí este
        teste deve ser reescrito, não deletado em silêncio.

            atraso = (T + latencia + offset) − T = latencia + offset
                   = 400 + (−400) = 0
        """
        relogio, maquina = _relogio()
        agora = _alimentar(relogio, 0, 0, 0, maquina=maquina)

        assert relogio.atraso_ms(agora_ms=agora) == 0.0

    def test_a_nota_do_resumo_declara_a_limitacao(self):
        """Se a ressalva sumir, o relatório volta a prometer o que não mede.

        Foi exatamente esse o erro da primeira versão: a nota dizia "limite
        superior da deriva", e quem lesse concluiria que passar no portão
        significa relógio bom.
        """
        relogio, maquina = _relogio()
        agora = _alimentar(relogio, 40, 41, maquina=maquina)
        nota = relogio.resumo(agora_ms=agora)["nota"]

        assert "CANCELAM" in nota
        assert "NAO CERTIFICADO" in nota
        assert "limite superior" not in nota.lower()

    def test_o_campo_nao_se_chama_deriva(self):
        """`deriva_ms` prometeria uma decomposição que a medição não faz."""
        relogio, maquina = _relogio()
        resumo = relogio.resumo(agora_ms=_alimentar(relogio, 40, maquina=maquina))

        assert "pior_atraso_ms" in resumo
        assert "deriva_ms" not in resumo


class TestUmaJanelaPorAtivo:
    """Um ativo atrasado entre oito saudáveis não pode se esconder na média."""

    def test_o_pior_ativo_manda(self):
        relogio, maquina = _relogio()
        for asset in ("bnb", "doge", "eth", "hype", "sol", "xrp", "zec"):
            _alimentar(relogio, 40, 41, 42, asset=asset, maquina=maquina)
        agora = _alimentar(relogio, 900, 910, 905, asset="btc", maquina=maquina)

        assert relogio.atraso_ms(agora_ms=agora) == 905.0
        assert relogio.resumo(agora_ms=agora)["por_ativo_ms"]["eth"] == 41.0

    def test_o_pior_e_por_modulo(self):
        """Um ativo com carimbo muito no futuro é tão ruim quanto um atrasado."""
        relogio, maquina = _relogio()
        _alimentar(relogio, 40, 41, 42, asset="eth", maquina=maquina)
        agora = _alimentar(relogio, -800, -810, -805, asset="btc", maquina=maquina)

        assert relogio.atraso_ms(agora_ms=agora) == -805.0

    def test_ativo_com_amostra_velha_sai_da_conta(self):
        """Ele não some do resumo por decreto — some por não ter dado fresco."""
        relogio, maquina = _relogio()
        _alimentar(relogio, 900, 910, 905, asset="btc", maquina=maquina)
        agora = _alimentar(
            relogio, 40, 41, 42, asset="eth", base_ms=BASE_MS + 60_000, maquina=maquina
        )

        assert relogio.atraso_ms(agora_ms=agora) == 41.0
        assert "btc" not in relogio.resumo(agora_ms=agora)["por_ativo_ms"]


class TestAJanelaEAValidade:
    def test_a_janela_e_deslizante(self):
        """Um relógio que escorregou e voltou não fica marcado para sempre."""
        relogio, maquina = _relogio()
        agora = _alimentar(
            relogio,
            *([900] * AMOSTRAS_NA_JANELA),
            *([40] * AMOSTRAS_NA_JANELA),
            maquina=maquina,
        )

        assert relogio.atraso_ms(agora_ms=agora) == 40.0

    def test_medicao_velha_vira_nao_sei(self):
        """Sem esta regra, um bot que perdeu o feed por meio minuto voltaria
        a decidir usando um número de meio minuto atrás para afirmar que o
        relógio está bom."""
        relogio, maquina = _relogio()
        agora = _alimentar(relogio, 40, 41, 42, maquina=maquina)

        assert relogio.atraso_ms(agora_ms=agora + IDADE_MAXIMA_MS - 1) is not None
        assert relogio.atraso_ms(agora_ms=agora + IDADE_MAXIMA_MS + 1) is None


class TestSaltoDoRelogioDeParede:
    """O modo de falhar que o sensor de uma via esconde e o monótono pega."""

    def test_pulo_do_relogio_de_parede_e_detectado(self):
        """O NTP corrigiu de vez no meio da operação.

        O relógio de parede anda 5 s enquanto o monótono anda 1 s: o tempo
        real que passou foi 1 s, então o de parede pulou 4 s. O sensor de uma
        via não veria nada — os atrasos continuam consistentes entre si.
        """
        relogio, maquina = _relogio()
        _alimentar(relogio, 40, 41, maquina=maquina)

        maquina.avancar(1.0)
        maquina.pular(4.0)  # o NTP corrigiu 4 s de vez
        salto_ms = int(maquina.parede_s * 1000)
        relogio.anotar(asset="btc", ts_servidor_ms=salto_ms - 40, chegada_ms=salto_ms)

        assert relogio.atraso_ms(agora_ms=salto_ms) is None
        assert relogio.resumo(agora_ms=salto_ms)["relogio_de_parede"]["saltos"] == 1
        assert relogio.resumo(agora_ms=salto_ms)["relogio_de_parede"][
            "pior_salto_ms"
        ] == pytest.approx(4000.0)

    def test_a_carencia_expira(self):
        """A recusa dura o suficiente para a janela se renovar, e não mais."""
        relogio, maquina = _relogio()
        _alimentar(relogio, 40, 41, maquina=maquina)
        maquina.avancar(1.0)
        maquina.pular(4.0)
        salto_ms = int(maquina.parede_s * 1000)
        relogio.anotar(asset="btc", ts_servidor_ms=salto_ms - 40, chegada_ms=salto_ms)

        assert relogio.atraso_ms(agora_ms=salto_ms + CARENCIA_APOS_SALTO_MS - 1) is None
        # Passada a carência a recusa POR SALTO acaba — mas a amostra também
        # precisa estar fresca, e 30 s depois ela não está. As duas guardas
        # são independentes, e este teste mostra as duas agindo.
        assert relogio.atraso_ms(agora_ms=salto_ms + CARENCIA_APOS_SALTO_MS + 1) is None
        assert not relogio._saltos.em_carencia(
            agora_ms=salto_ms + CARENCIA_APOS_SALTO_MS + 1
        )

    @pytest.mark.parametrize("desvio_ms", [0.0, LIMIAR_DE_SALTO_MS - 1])
    def test_escorregao_normal_do_ntp_nao_e_salto(self, desvio_ms):
        """O ajuste fino do NTP é contínuo e pequeno; acusar salto nele
        deixaria o bot recusando o dia inteiro numa máquina saudável."""
        relogio, maquina = _relogio()
        _alimentar(relogio, 40, 41, maquina=maquina)
        maquina.avancar(1.0)
        maquina.pular(desvio_ms / 1000.0)
        chegada = int(maquina.parede_s * 1000)
        relogio.anotar(asset="btc", ts_servidor_ms=chegada - 40, chegada_ms=chegada)

        assert relogio.resumo(agora_ms=chegada)["relogio_de_parede"]["saltos"] == 0


class TestOCaminhoCompleto:
    """Feed → `PrecosAoVivo` → sensor → portão, sem cola no meio."""

    def _precos_com(self, *atrasos, asset="btc", base_ms=BASE_MS):
        from pulsearb.live.precos import PrecosAoVivo

        precos = PrecosAoVivo()
        for i, atraso in enumerate(atrasos):
            servidor = base_ms + i * 1000
            precos.anotar(
                asset,
                valor_e18=70_000 * 10**18,
                ts_servidor_ms=servidor,
                chegada_ms=servidor + atraso,
            )
        return precos, base_ms + (len(atrasos) - 1) * 1000 + atrasos[-1]

    def _portao(self, tmp_path, precos, agora_ms):
        from pulsearb.risk import PortaoDeRisco
        from pulsearb.settings import Mode, RiskSettings

        return PortaoDeRisco(
            RiskSettings(),
            Mode.LIVE,
            caminho_do_registro=tmp_path / "registro.json",
            hoje="2026-08-25",
            relogio=lambda: agora_ms / 1000,
            relogio_do_servidor=precos.relogio,
        )

    def _ordem(self):
        from pulsearb.risk import OrdemPretendida

        return OrdemPretendida(
            slug="btc-updown-5m-1",
            token_id="tok-up",
            lado_up=True,
            shares=5.0,
            preco_limite=0.50,
        )

    def _decidir(self, tmp_path, precos, agora):
        return self._portao(tmp_path, precos, agora).avaliar(
            self._ordem(), feeds_saudaveis=True, melhor_bid=0.49, melhor_ask=0.51
        )

    def test_feed_saudavel_atravessa_ate_o_portao(self, tmp_path):
        precos, agora = self._precos_com(40, 41, 42)
        assert self._decidir(tmp_path, precos, agora).pode

    def test_feed_atrasado_fecha_o_portao(self, tmp_path):
        from pulsearb.risk import MOTIVOS

        precos, agora = self._precos_com(600, 610, 620)
        decisao = self._decidir(tmp_path, precos, agora)

        assert decisao.motivo == MOTIVOS.RELOGIO_DERIVADO
        assert decisao.detalhe["atraso_ms"] == 610.0

    def test_um_ativo_atrasado_fecha_o_portao_de_todos(self, tmp_path):
        """A razão de ser da janela por ativo, ponta a ponta.

        Sete ativos saudáveis e um atrasado: com janela única a mediana
        global ficaria em ~41 ms e o portão abriria, deixando uma ordem no
        ativo doente sair com preço velho.
        """
        from pulsearb.live.precos import PrecosAoVivo
        from pulsearb.risk import MOTIVOS

        precos = PrecosAoVivo()
        saudaveis = ("bnb", "doge", "eth", "hype", "sol", "xrp", "zec")
        agora = BASE_MS
        for asset, atrasos in (
            *((a, (40, 41, 42)) for a in saudaveis),
            ("btc", (900, 910, 905)),
        ):
            for i, atraso in enumerate(atrasos):
                servidor = BASE_MS + i * 1000
                precos.anotar(
                    asset,
                    valor_e18=70_000 * 10**18,
                    ts_servidor_ms=servidor,
                    chegada_ms=servidor + atraso,
                )
                agora = max(agora, servidor + atraso)

        # Com janela única a mediana global ficaria em ~42 ms e o portão
        # abriria; o btc é o único doente e é ele quem manda.
        assert self._decidir(tmp_path, precos, agora).motivo == MOTIVOS.RELOGIO_DERIVADO

    def test_tick_sem_chegada_nao_alimenta_a_trava(self, tmp_path):
        """Reprodução de gravação não pode dar nota máxima ao relógio.

        Quem chama sem `chegada_ms` não tem o dado; inventar `agora` daria
        atraso ~0 e o portão passaria achando que mediu.
        """
        from pulsearb.live.precos import PrecosAoVivo
        from pulsearb.risk import MOTIVOS

        precos = PrecosAoVivo()
        precos.anotar("btc", valor_e18=70_000 * 10**18, ts_servidor_ms=BASE_MS)
        decisao = self._decidir(tmp_path, precos, BASE_MS + 40)

        assert decisao.motivo == MOTIVOS.RELOGIO_NAO_MONITORADO
