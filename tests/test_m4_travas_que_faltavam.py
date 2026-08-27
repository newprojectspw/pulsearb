"""M4 — as três travas que o `ESTADO_PARA_LIVE` listava em branco.

3.8 pausa por sequência de perdas · 3.10 spread anômalo · 3.11 kill switch.

O que elas têm em comum, e é o que cada teste aqui trava: nenhuma depende de
o bot estar são para funcionar. A pausa sobrevive a reinício, o kill é lido a
cada ordem, e o portão do livro recusa também quando o livro não chegou.
"""

from __future__ import annotations

import pytest

from pulsearb.risk import MOTIVOS, OrdemPretendida, PortaoDeRisco
from pulsearb.settings import Mode, RiskSettings

LIVRO_SADIO = {"melhor_bid": 0.49, "melhor_ask": 0.51}


def _ordem(slug: str = "btc-updown-5m-1", shares: float = 5.0, preco: float = 0.50):
    return OrdemPretendida(
        slug=slug, token_id="tok-up", lado_up=True, shares=shares, preco_limite=preco
    )


class _Relogio:
    """Relógio de mentira: o teste manda o tempo passar."""

    def __init__(self, agora: float = 1_787_000_000.0) -> None:
        self.agora = agora

    def __call__(self) -> float:
        return self.agora

    def avancar(self, segundos: float) -> None:
        self.agora += segundos


def _portao(tmp_path, *, relogio=None, kill=None, **ajustes):
    return PortaoDeRisco(
        RiskSettings(**ajustes),
        Mode.LIVE,
        caminho_do_registro=tmp_path / "registro.json",
        caminho_do_kill=kill,
        hoje="2026-08-25",
        relogio=relogio or _Relogio(),
    )


def _avaliar(portao, ordem=None, **livro):
    return portao.avaliar(
        ordem or _ordem(), feeds_saudaveis=True, **(LIVRO_SADIO | livro)
    )


class TestKillSwitch:
    """3.11 — a chave que uma pessoa puxa quando não confia mais no bot."""

    def test_arquivo_presente_recusa_tudo(self, tmp_path):
        kill = tmp_path / "KILL"
        portao = _portao(tmp_path, kill=kill)
        assert _avaliar(portao).pode

        kill.write_text("parar agora", encoding="utf-8")
        decisao = _avaliar(portao)

        assert not decisao.pode
        assert decisao.motivo == MOTIVOS.KILL_ACIONADO

    def test_e_lido_a_cada_ordem_e_nao_na_subida(self, tmp_path):
        """Uma chave lida só no construtor só funciona antes de ser precisa.

        O caso real é o oposto: o bot já está rodando quando alguém decide
        pará-lo.
        """
        kill = tmp_path / "KILL"
        portao = _portao(tmp_path, kill=kill)
        kill.touch()
        assert not _avaliar(portao).pode

        kill.unlink()
        assert _avaliar(portao).pode

    def test_kill_vence_ate_o_disjuntor_no_motivo(self, tmp_path):
        # Os dois recusam. Reportar o kill diz que houve DECISÃO HUMANA, o
        # que mudaria o que a pessoa de plantão vai fazer a seguir.
        kill = tmp_path / "KILL"
        kill.touch()
        portao = _portao(tmp_path, kill=kill)
        portao.armar_disjuntor("teste")

        assert _avaliar(portao).motivo == MOTIVOS.KILL_ACIONADO

    def test_sem_caminho_configurado_nao_inventa_kill(self, tmp_path):
        assert _avaliar(_portao(tmp_path, kill=None)).pode


class TestPausaPorSequencia:
    """3.8 — quatro perdas seguidas param o bot por uma hora."""

    def _perder(self, portao, quantas, valor=-1.0):
        for i in range(quantas):
            portao.registrar_resolucao(f"janela-{i}", valor)

    def test_quatro_perdas_pausam(self, tmp_path):
        portao = _portao(tmp_path, perdas_seguidas_para_pausa=4)
        self._perder(portao, 3)
        assert _avaliar(portao).pode

        self._perder(portao, 1)
        decisao = _avaliar(portao)

        assert decisao.motivo == MOTIVOS.PAUSA_POR_SEQUENCIA
        assert decisao.detalhe["segundos_restantes"] == pytest.approx(3600.0, abs=1)

    def test_uma_vitoria_zera_a_sequencia(self, tmp_path):
        portao = _portao(tmp_path, perdas_seguidas_para_pausa=4)
        self._perder(portao, 3)
        portao.registrar_resolucao("ganhou", +0.5)
        self._perder(portao, 3)

        assert _avaliar(portao).pode

    def test_empate_nao_zera_nem_conta(self, tmp_path):
        """Zerar no empate daria à TAXA o poder de limpar o histórico.

        Uma janela que acerta o lado e devolve o lucro inteiro em taxa fecha
        em zero, e isso não é evidência de que o modelo voltou a funcionar.
        """
        portao = _portao(tmp_path, perdas_seguidas_para_pausa=4)
        self._perder(portao, 3)
        portao.registrar_resolucao("empate", 0.0)
        assert portao.registro.perdas_seguidas == 3

        self._perder(portao, 1)
        assert _avaliar(portao).motivo == MOTIVOS.PAUSA_POR_SEQUENCIA

    def test_a_pausa_expira_sozinha(self, tmp_path):
        relogio = _Relogio()
        portao = _portao(
            tmp_path, relogio=relogio, perdas_seguidas_para_pausa=2,
            pausa_apos_sequencia_s=3600.0,
        )
        self._perder(portao, 2)
        assert not _avaliar(portao).pode

        relogio.avancar(3601.0)
        assert _avaliar(portao).pode

    def test_pausar_zera_a_sequencia(self, tmp_path):
        """Senão quatro perdas seguidas viram "uma perda por hora, para sempre".

        A pausa É a resposta àquela sequência; mantê-la faria toda perda
        posterior repausar sem evidência nova.
        """
        relogio = _Relogio()
        portao = _portao(tmp_path, relogio=relogio, perdas_seguidas_para_pausa=2)
        self._perder(portao, 2)
        assert portao.registro.perdas_seguidas == 0

        relogio.avancar(3601.0)
        portao.registrar_resolucao("mais_uma", -1.0)
        assert _avaliar(portao).pode

    def test_sobrevive_a_reinicio(self, tmp_path):
        # Sem persistência a pausa vira limite por vida de processo — e o
        # systemd reinicia o bot em segundos.
        relogio = _Relogio()
        primeiro = _portao(tmp_path, relogio=relogio, perdas_seguidas_para_pausa=2)
        self._perder(primeiro, 2)

        renascido = _portao(tmp_path, relogio=relogio, perdas_seguidas_para_pausa=2)
        assert _avaliar(renascido).motivo == MOTIVOS.PAUSA_POR_SEQUENCIA

    def test_a_virada_do_dia_nao_encurta_a_pausa(self, tmp_path):
        """Pausa de 1h começada 23:40 que evaporasse à meia-noite duraria 20 min.

        O mercado não sabe que o dia virou.
        """
        relogio = _Relogio()
        ontem = PortaoDeRisco(
            RiskSettings(perdas_seguidas_para_pausa=2),
            Mode.LIVE,
            caminho_do_registro=tmp_path / "registro.json",
            hoje="2026-08-25",
            relogio=relogio,
        )
        self._perder(ontem, 2)

        hoje = PortaoDeRisco(
            RiskSettings(perdas_seguidas_para_pausa=2),
            Mode.LIVE,
            caminho_do_registro=tmp_path / "registro.json",
            hoje="2026-08-26",
            relogio=relogio,
        )
        assert hoje.registro.pausado_ate_epoch is not None
        assert _avaliar(hoje).motivo == MOTIVOS.PAUSA_POR_SEQUENCIA

    def test_o_disjuntor_vence_a_pausa_no_motivo(self, tmp_path):
        # Um expira, o outro exige uma pessoa. Reportar a pausa faria alguém
        # esperar uma hora por algo que nunca ia voltar sozinho.
        portao = _portao(
            tmp_path, perdas_seguidas_para_pausa=2, perda_max_diaria_usdc=5.0
        )
        portao.registrar_resolucao("a", -3.0)
        portao.registrar_resolucao("b", -3.0)

        assert _avaliar(portao).motivo == MOTIVOS.DISJUNTOR_ARMADO

    def test_retomar_libera_antes_da_hora(self, tmp_path):
        portao = _portao(tmp_path, perdas_seguidas_para_pausa=2)
        self._perder(portao, 2)
        portao.retomar()

        assert _avaliar(portao).pode


class TestPortaoDoLivro:
    """3.10 (metade) — spread que come o edge, e livro que não chegou."""

    def test_spread_acima_do_teto_recusa(self, tmp_path):
        # 0,45/0,55 = 0,10 de spread. O critério 1.1 exige edge de 0,02 e o
        # taker paga meio spread: 0,05 de custo contra 0,02 de edge.
        decisao = _avaliar(_portao(tmp_path), melhor_bid=0.45, melhor_ask=0.55)

        assert decisao.motivo == MOTIVOS.SPREAD_ANOMALO
        assert decisao.detalhe["spread"] == pytest.approx(0.10)

    def test_o_teto_sai_da_conta_do_edge(self, tmp_path):
        # Exatamente 0,04 passa; acima disso o custo de atravessar iguala ou
        # supera o edge exigido, e o trade nao pode ganhar por construcao.
        portao = _portao(tmp_path, spread_maximo=0.04)
        assert _avaliar(portao, melhor_bid=0.48, melhor_ask=0.52).pode
        assert not _avaliar(portao, melhor_bid=0.48, melhor_ask=0.53).pode

    @pytest.mark.parametrize(
        ("bid", "ask"),
        [(0.48, 0.52), (0.47, 0.51), (0.29, 0.33), (0.71, 0.75), (0.06, 0.10)],
    )
    def test_o_teto_nao_depende_do_NIVEL_do_preco(self, tmp_path, bid, ask):
        """`0.52 - 0.48` dá 0,040000000000000036 em float64.

        Sem arredondar antes de comparar, um spread de exatamente um teto
        seria recusado em alguns níveis de preço e aceito em outros — um
        portão que decide diferente em 0,52/0,48 e em 0,51/0,47 não tem
        contrato nenhum.
        """
        portao = _portao(tmp_path, spread_maximo=0.04)
        assert _avaliar(portao, melhor_bid=bid, melhor_ask=ask).pode

    @pytest.mark.parametrize(
        ("bid", "ask"), [(None, 0.51), (0.49, None), (None, None)]
    )
    def test_livro_ausente_recusa_com_motivo_PROPRIO(self, tmp_path, bid, ask):
        """"Não sei o que isto custaria" não é "sei, e é caro demais".

        Um SHADOW que misture os dois não diz se falta instrumentação ou
        falta liquidez, e os consertos são opostos.
        """
        decisao = _avaliar(_portao(tmp_path), melhor_bid=bid, melhor_ask=ask)

        assert decisao.motivo == MOTIVOS.LIVRO_DESCONHECIDO
        assert decisao.motivo != MOTIVOS.SPREAD_ANOMALO

    def test_o_livro_e_conferido_no_shadow_tambem(self, tmp_path):
        # `avaliar_risco` e o caminho do SHADOW. Se o portao do livro so
        # existisse em `avaliar`, o ensaio nao ensaiaria esta trava.
        decisao = _portao(tmp_path).avaliar_risco(
            _ordem(), feeds_saudaveis=True, melhor_bid=0.40, melhor_ask=0.60
        )
        assert decisao.motivo == MOTIVOS.SPREAD_ANOMALO
