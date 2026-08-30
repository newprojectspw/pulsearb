"""5.4 — a sonda de NTP. O que ela aceita como resposta, e o que não aceita.

O subprocesso é injetado em todo teste: rodar `timedatectl` de verdade faria o
resultado da suíte depender da máquina que a roda, e ela roda em três (o Mac,
a VPS e o CI).
"""

from __future__ import annotations

import pytest

from pulsearb.risk.sincronia import SONDAS, Sincronia, estado_da_sincronia


def _responde(**por_comando):
    """Um `rodar` de mentira: mapeia o executável para a saída dele.

    Comando ausente do mapa devolve `None`, que é como o módulo enxerga
    "não instalado".
    """

    def rodar(argv):
        return por_comando.get(argv[0])

    return rodar


class TestSystemd:
    def test_sincronizado(self):
        estado = estado_da_sincronia(
            _responde(timedatectl="NTPSynchronized=yes\n")
        )

        assert estado.sincronizado is True
        assert estado.verificada
        assert estado.fonte == "timedatectl"

    def test_nao_sincronizado(self):
        estado = estado_da_sincronia(_responde(timedatectl="NTPSynchronized=no\n"))

        assert estado.sincronizado is False
        assert not estado.verificada


class TestChrony:
    def test_leap_status_normal_e_sincronizado(self):
        saida = (
            "Reference ID    : C0248F82 (time.example.net)\n"
            "Stratum         : 3\n"
            "Leap status     : Normal\n"
        )
        estado = estado_da_sincronia(_responde(chronyc=saida))

        assert estado.sincronizado is True
        assert estado.fonte == "chronyc"

    def test_not_synchronised_e_recusa(self):
        """Daemon rodando mas ainda sem travar numa fonte.

        É diferente de não haver daemon, e igualmente motivo para não operar:
        o relógio ainda não foi corrigido.
        """
        estado = estado_da_sincronia(
            _responde(chronyc="506 Cannot talk to daemon\nNot synchronised\n")
        )

        assert estado.sincronizado is False


class TestMacOS:
    @pytest.mark.parametrize(
        ("saida", "esperado"),
        [("Network Time: On\n", True), ("Network Time: Off\n", False)],
    )
    def test_systemsetup(self, saida, esperado):
        estado = estado_da_sincronia(_responde(systemsetup=saida))

        assert estado.sincronizado is esperado
        assert estado.fonte == "systemsetup"


class TestFalhaFechada:
    def test_nenhum_daemon_da_indeterminado(self):
        estado = estado_da_sincronia(_responde())

        assert estado.sincronizado is None
        assert not estado.verificada
        assert estado.fonte == "nenhuma"

    def test_indeterminado_NAO_e_o_mesmo_que_dessincronizado(self):
        """Os dois recusam, mas o diário precisa distinguir os consertos.

        "Não há daemon" se resolve instalando NTP; "há daemon e ele diz que
        não sincronizou" se resolve investigando a rede ou a fonte de tempo.
        Colapsar os dois em `False` mandaria o operador pelo caminho errado.
        """
        sem_daemon = estado_da_sincronia(_responde())
        com_daemon = estado_da_sincronia(_responde(timedatectl="NTPSynchronized=no\n"))

        assert sem_daemon.sincronizado is None
        assert com_daemon.sincronizado is False

    def test_formato_desconhecido_nao_conta_como_resposta(self):
        """Comando existe, sai com 0, e a saída não tem o campo esperado.

        Isso acontece de verdade quando a ferramenta muda de formato entre
        versões. Aceitar seria inventar; a sonda segue para a próxima.
        """
        estado = estado_da_sincronia(
            _responde(timedatectl="alguma outra coisa\n", chronyc="Leap status : Normal\n")
        )

        assert estado.sincronizado is True
        assert estado.fonte == "chronyc"

    def test_todas_com_formato_desconhecido_da_indeterminado(self):
        estado = estado_da_sincronia(
            _responde(timedatectl="???", chronyc="???", systemsetup="???")
        )

        assert estado.sincronizado is None
        assert "formato_desconhecido" in estado.detalhe

    def test_o_detalhe_diz_o_que_fazer(self):
        """Recusa sem instrução manda o operador adivinhar."""
        detalhe = estado_da_sincronia(_responde()).detalhe

        assert "set-ntp" in detalhe or "NTP" in detalhe
        assert "NAO sincronizado" in detalhe


class TestAOrdemDasSondas:
    def test_systemd_vem_antes_de_chrony(self):
        """A VPS roda systemd; perguntar a ele primeiro evita um subprocesso.

        Se as duas respondem, a primeira ganha — e o teste trava a ordem para
        que uma reordenação acidental apareça aqui.
        """
        estado = estado_da_sincronia(
            _responde(
                timedatectl="NTPSynchronized=yes\n",
                chronyc="Leap status : Not synchronised\n",
            )
        )

        assert estado.fonte == "timedatectl"

    def test_a_ordem_declarada_e_a_esperada(self):
        assert [nome for nome, _, _ in SONDAS] == [
            "timedatectl",
            "chronyc",
            "systemsetup",
        ]


class TestOContrato:
    def test_verificada_so_e_verdadeira_para_True(self):
        """`None` e `False` não passam — é o ponto inteiro do fail-closed."""
        assert Sincronia(True, "x", "").verificada
        assert not Sincronia(False, "x", "").verificada
        assert not Sincronia(None, "x", "").verificada
