"""3.4 e 5.4 — quem autoriza dinheiro real a se mover.

A trava tripla existe porque uma não basta e duas se copiam juntas. Cada teste
aqui trava uma forma de a autorização sair por engano.
"""

from __future__ import annotations

import pytest

from pulsearb.risk import FRASE_DE_ACEITE, autorizacao_para_live
from pulsearb.risk.autorizacao import (
    BLOQUEIO_ACEITE,
    BLOQUEIO_CLIENTE,
    BLOQUEIO_CONFIRMACAO,
    BLOQUEIO_MODO,
    BLOQUEIO_RELOGIO,
    ENV_ACEITE,
    ENV_CONFIRMACAO,
    AutorizacaoParaLive,
)
from pulsearb.risk.sincronia import Sincronia
from pulsearb.settings import Mode

SINCRONIZADO = Sincronia(sincronizado=True, fonte="timedatectl", detalhe="ok")
NAO_SINCRONIZADO = Sincronia(
    sincronizado=False, fonte="chronyc", detalhe="Leap status: Not synchronised"
)
INDETERMINADO = Sincronia(
    sincronizado=None, fonte="nenhuma", detalhe="nenhum daemon respondeu"
)

ENV_COMPLETO = {ENV_CONFIRMACAO: "1", ENV_ACEITE: FRASE_DE_ACEITE}


def _autorizar(**ajustes):
    kwargs = {
        "modo": Mode.LIVE,
        "env": dict(ENV_COMPLETO),
        "sincronia": SINCRONIZADO,
        "cliente_de_ordens_existe": True,
        **ajustes,
    }
    modo = kwargs.pop("modo")
    return autorizacao_para_live(modo, **kwargs)


class TestTudoPresente:
    def test_com_as_cinco_condicoes_autoriza(self):
        """O caminho positivo existe e é alcançável — senão os testes de
        recusa não provariam nada, já que tudo recusaria de qualquer jeito."""
        licenca = _autorizar()

        assert licenca.autorizado
        assert licenca.bloqueios == ()
        assert "autorizado" in licenca.explicar().lower()


class TestATravaTripla:
    def test_modo_diferente_de_live_bloqueia(self):
        assert BLOQUEIO_MODO in _autorizar(modo=Mode.SHADOW).bloqueios

    def test_sem_a_segunda_variavel_bloqueia(self):
        """`MODE=LIVE` sozinho não basta: um `.env` copiado de outra máquina
        traria o modo junto, e a segunda variável é o que separa "esta é a
        máquina de produção" de "este arquivo veio de algum lugar"."""
        env = {ENV_ACEITE: FRASE_DE_ACEITE}
        assert BLOQUEIO_CONFIRMACAO in _autorizar(env=env).bloqueios

    def test_sem_a_frase_bloqueia(self):
        env = {ENV_CONFIRMACAO: "1"}
        assert BLOQUEIO_ACEITE in _autorizar(env=env).bloqueios

    @pytest.mark.parametrize(
        "frase",
        [
            "eu aceito o risco",
            "Eu Aceito O Risco",
            "EU ACEITO O RISC",
            "EU ACEITO O RISCO!",
            "true",
            "1",
            "sim",
        ],
    )
    def test_a_frase_e_comparada_EXATAMENTE(self, frase):
        """Aceitar variação desfaria o propósito da terceira trava.

        Ela existe para que a última etapa seja impossível por acidente. Uma
        comparação que aceita `true` ou caixa diferente vira booleano — e
        booleano se digita sem pensar, que é exatamente o que a frase impede.
        """
        env = {ENV_CONFIRMACAO: "1", ENV_ACEITE: frase}
        assert BLOQUEIO_ACEITE in _autorizar(env=env).bloqueios

    def test_espaco_nas_pontas_e_tolerado(self):
        """Espaço é artefato de terminal e de `.env`, não descuido do operador."""
        env = {ENV_CONFIRMACAO: "1", ENV_ACEITE: f"  {FRASE_DE_ACEITE}  "}
        assert BLOQUEIO_ACEITE not in _autorizar(env=env).bloqueios


class TestORelogio:
    def test_relogio_dessincronizado_bloqueia(self):
        assert BLOQUEIO_RELOGIO in _autorizar(sincronia=NAO_SINCRONIZADO).bloqueios

    def test_relogio_INDETERMINADO_bloqueia_igual(self):
        """Não saber vale o mesmo que saber que está errado.

        Um relógio não verificado tem exatamente o mesmo efeito no
        `seconds_left` que um relógio errado; a diferença é só a nossa
        ignorância, e ignorância não é motivo para arriscar dinheiro.
        """
        licenca = _autorizar(sincronia=INDETERMINADO)

        assert BLOQUEIO_RELOGIO in licenca.bloqueios
        assert licenca.detalhe["sincronia"]["sincronizado"] is None

    def test_a_sincronia_entra_no_detalhe_mesmo_quando_passa(self):
        """Quem lê o diário precisa saber QUAL daemon respondeu, e o quê."""
        licenca = _autorizar()

        assert licenca.detalhe["sincronia"]["fonte"] == "timedatectl"
        assert licenca.detalhe["sincronia"]["sincronizado"] is True

    def test_a_sonda_nao_roda_quando_a_sincronia_ja_veio_pronta(self):
        """A sonda usa subprocesso; o caminho de decisão não pode chamá-la.

        Quem já perguntou na subida passa o resultado. Se este teste quebrar,
        um subprocesso entrou num caminho que roda a cada ordem.
        """
        def explodir():
            raise AssertionError("a sonda não deveria ter sido chamada")

        licenca = autorizacao_para_live(
            Mode.LIVE,
            env=dict(ENV_COMPLETO),
            sincronia=SINCRONIZADO,
            cliente_de_ordens_existe=True,
            sonda_de_sincronia=explodir,
        )

        assert licenca.autorizado


class TestOClienteDeOrdens:
    def test_sem_cliente_bloqueia_mesmo_com_tudo_o_mais(self):
        """Nenhuma trava de intenção substitui código que saiba enviar."""
        licenca = _autorizar(cliente_de_ordens_existe=False)

        assert not licenca.autorizado
        assert licenca.bloqueios == (BLOQUEIO_CLIENTE,)

    def test_hoje_o_default_e_sem_cliente(self):
        """O default do parâmetro reflete o estado real do repositório.

        Quando 3.2 e 3.5 existirem, quem constrói o cliente passa `True` — e
        este teste deve ser atualizado junto, de propósito.
        """
        licenca = autorizacao_para_live(
            Mode.LIVE, env=dict(ENV_COMPLETO), sincronia=SINCRONIZADO
        )

        assert BLOQUEIO_CLIENTE in licenca.bloqueios


class TestTodosOsBloqueiosDeUmaVez:
    def test_com_nada_configurado_lista_os_cinco(self):
        """Reportar só o primeiro faria o operador consertar um por vez, cada
        volta achando que era a última. Pior: a trava tripla nunca seria
        exercitada enquanto o cliente não existisse."""
        licenca = autorizacao_para_live(
            Mode.SIM, env={}, sincronia=INDETERMINADO, cliente_de_ordens_existe=False
        )

        assert set(licenca.bloqueios) == {
            BLOQUEIO_MODO,
            BLOQUEIO_CONFIRMACAO,
            BLOQUEIO_ACEITE,
            BLOQUEIO_RELOGIO,
            BLOQUEIO_CLIENTE,
        }

    def test_cada_bloqueio_tem_detalhe_acionavel(self):
        """Bloqueio sem instrução manda o operador adivinhar."""
        licenca = autorizacao_para_live(
            Mode.SIM, env={}, sincronia=INDETERMINADO, cliente_de_ordens_existe=False
        )

        for bloqueio in licenca.bloqueios:
            assert licenca.detalhe.get(bloqueio), f"{bloqueio} sem detalhe"

    def test_bloqueio_anonimo_e_recusado_na_construcao(self):
        """Mesma regra dos MOTIVOS do portão: recusa sem nome não vira alarme."""
        with pytest.raises(ValueError, match="sem nome registrado"):
            AutorizacaoParaLive(autorizado=False, bloqueios=("motivo_inventado",))

    def test_autorizacao_positiva_com_bloqueio_e_recusada(self):
        with pytest.raises(ValueError, match="não carrega bloqueio"):
            AutorizacaoParaLive(autorizado=True, bloqueios=(BLOQUEIO_MODO,))
