"""A fonte de deriva do item 3.10 — o que ela mede e o que ela NÃO mede.

O módulo publica `atraso`, não `deriva`, e a diferença entre as duas palavras
é o contrato inteiro: o número é deriva de relógio MAIS latência de rede MAIS
fila no processo, somadas e não separáveis com uma fonte só. Serve como limite
superior da deriva, que é o que o portão precisa.
"""

from __future__ import annotations

from pulsearb.live.relogio import (
    AMOSTRAS_NA_JANELA,
    IDADE_MAXIMA_MS,
    RelogioDoServidor,
)


def _alimentar(relogio, *atrasos, base_ms=1_787_000_000_000):
    """Anota um tick por atraso pedido, um por segundo."""
    for i, atraso in enumerate(atrasos):
        servidor = base_ms + i * 1000
        relogio.anotar(ts_servidor_ms=servidor, chegada_ms=servidor + atraso)
    return base_ms + (len(atrasos) - 1) * 1000 + atrasos[-1]


class TestOQueEleMede:
    def test_sem_amostra_diz_nao_sei(self):
        """Zero nunca é resposta para "ainda não medi nada"."""
        assert RelogioDoServidor().atraso_ms(agora_ms=1) is None

    def test_a_mediana_de_uma_serie_estavel(self):
        relogio = RelogioDoServidor()
        agora = _alimentar(relogio, 40, 42, 41, 43, 39)

        assert relogio.atraso_ms(agora_ms=agora) == 41.0

    def test_um_pico_isolado_nao_decide_sozinho(self):
        """Mediana e não média, e o teste diz por quê.

        Um GC de meio segundo num tick só deslocaria a média para ~120 ms e
        deixaria o portão nervoso por vários segundos depois de o problema
        já ter passado.
        """
        relogio = RelogioDoServidor()
        agora = _alimentar(relogio, 40, 41, 700, 42, 39)

        assert relogio.atraso_ms(agora_ms=agora) == 41.0

    def test_atraso_negativo_e_guardado_como_veio(self):
        """Carimbo no futuro é a assinatura de relógio LOCAL atrasado.

        Zerar o negativo apagaria exatamente o defeito que este módulo
        procura, e o portão nunca veria essa metade.
        """
        relogio = RelogioDoServidor()
        agora = _alimentar(relogio, -300, -310, -305)

        assert relogio.atraso_ms(agora_ms=agora) == -305.0
        assert relogio.resumo(agora_ms=agora)["carimbo_no_futuro"] == 3


class TestAJanelaEAValidade:
    def test_a_janela_e_deslizante(self):
        """Amostra velha sai: um relógio que escorregou e voltou não fica
        marcado para sempre."""
        relogio = RelogioDoServidor()
        agora = _alimentar(relogio, *([900] * AMOSTRAS_NA_JANELA), *([40] * AMOSTRAS_NA_JANELA))

        assert relogio.atraso_ms(agora_ms=agora) == 40.0

    def test_medicao_velha_vira_nao_sei(self):
        """O bot ficou sem feed e voltou: a última mediana não descreve o agora.

        Sem esta regra, um bot que perdeu o feed por meio minuto voltaria a
        decidir usando um número de meio minuto atrás para afirmar que o
        relógio está bom.
        """
        relogio = RelogioDoServidor()
        agora = _alimentar(relogio, 40, 41, 42)

        assert relogio.atraso_ms(agora_ms=agora + IDADE_MAXIMA_MS - 1) is not None
        assert relogio.atraso_ms(agora_ms=agora + IDADE_MAXIMA_MS + 1) is None

    def test_o_resumo_nao_promete_o_que_nao_mede(self):
        """A nota do resumo tem de dizer que o número é uma soma.

        Se alguém renomear o campo para `deriva_ms`, ou apagar a ressalva,
        este teste quebra — e quebra de propósito: o relatório passaria a
        prometer uma decomposição que a medição não faz.
        """
        relogio = RelogioDoServidor()
        agora = _alimentar(relogio, 40, 41)
        resumo = relogio.resumo(agora_ms=agora)

        assert "atraso_mediano_ms" in resumo
        assert "deriva_ms" not in resumo
        assert "latencia" in resumo["nota"]
        assert resumo["ticks_vistos"] == 2


class TestOCaminhoCompleto:
    """Feed → `PrecosAoVivo` → `RelogioDoServidor` → portão, sem cola no meio.

    Cada peça já tem os seus testes. Estes provam que elas COMPÕEM — que o
    número que sai do feed é o mesmo que o portão lê, sem ninguém precisar
    lembrar de copiá-lo de um lado para o outro.
    """

    def _precos_com(self, *atrasos, base_ms=1_787_000_000_000):
        from pulsearb.live.precos import PrecosAoVivo

        precos = PrecosAoVivo()
        for i, atraso in enumerate(atrasos):
            servidor = base_ms + i * 1000
            precos.anotar(
                "btc",
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

    def test_feed_saudavel_atravessa_ate_o_portao(self, tmp_path):
        precos, agora = self._precos_com(40, 41, 42)
        decisao = self._portao(tmp_path, precos, agora).avaliar(
            self._ordem(),
            feeds_saudaveis=True,
            melhor_bid=0.49,
            melhor_ask=0.51,
        )

        assert decisao.pode

    def test_feed_atrasado_fecha_o_portao(self, tmp_path):
        from pulsearb.risk import MOTIVOS

        precos, agora = self._precos_com(600, 610, 620)
        decisao = self._portao(tmp_path, precos, agora).avaliar(
            self._ordem(),
            feeds_saudaveis=True,
            melhor_bid=0.49,
            melhor_ask=0.51,
        )

        assert decisao.motivo == MOTIVOS.RELOGIO_DERIVADO
        assert decisao.detalhe["atraso_ms"] == 610.0

    def test_tick_sem_chegada_nao_alimenta_a_trava(self, tmp_path):
        """Reprodução de gravação não pode dar nota máxima ao relógio.

        Quem chama sem `chegada_ms` não tem o dado; inventar `agora` daria
        atraso ~0 e o portão passaria achando que mediu. A amostra não entra,
        o portão diz "não sei", e não sei é recusa.
        """
        from pulsearb.live.precos import PrecosAoVivo
        from pulsearb.risk import MOTIVOS

        precos = PrecosAoVivo()
        precos.anotar("btc", valor_e18=70_000 * 10**18, ts_servidor_ms=1_787_000_000_000)
        decisao = self._portao(tmp_path, precos, 1_787_000_000_040).avaliar(
            self._ordem(),
            feeds_saudaveis=True,
            melhor_bid=0.49,
            melhor_ask=0.51,
        )

        assert decisao.motivo == MOTIVOS.RELOGIO_NAO_MONITORADO
