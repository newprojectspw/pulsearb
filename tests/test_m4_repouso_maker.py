"""Item 4.0 (c) — mexer na cotação que repousa, ou deixar?

Cada teste trava um jeito de a política sair errada de um modo que o número
final não denuncia: perseguir ruído do livro, ficar preso a uma cotação morta
pelo tempo mínimo, ou trocar por um ganho menor que o custo de fila que
ninguém consegue medir.
"""

from __future__ import annotations

import pytest

from pulsearb.live.cotacao import Cotacao, RetornoEstimado
from pulsearb.live.repouso import (
    GANHO_MINIMO_USDC,
    SEGUNDOS_MINIMOS_REPOUSADA,
    AcaoNaCotacao,
    CotacaoAberta,
    decidir,
)

AGORA = 1_000_000.0


def _retorno(cotacao: Cotacao, liquido: float, *, pontua: bool = True):
    """Um `RetornoEstimado` com o líquido que o teste quer.

    As parcelas são preenchidas para somar o líquido pedido; o que os testes
    daqui exercitam é a POLÍTICA, e ela só lê `liquido_usdc` e `pontua`.
    """
    return RetornoEstimado(
        cotacao=cotacao,
        score_proprio=1.0 if pontua else 0.0,
        score_total_do_livro=10.0,
        fracao_do_pool=0.1,
        rewards_usdc=liquido,
        custo_de_markout_usdc=0.0,
        fator_de_captura=0.3,
    )


class TestSemNadaAberto:
    def test_entra_quando_ha_candidata(self):
        d = decidir(None, _retorno(Cotacao(1, 50.0), 5.0), None, agora_epoch=AGORA)

        assert d.acao is AcaoNaCotacao.REPOSICIONAR
        assert d.nova == Cotacao(1, 50.0)

    def test_nao_entra_quando_nenhuma_pontua(self):
        d = decidir(None, None, None, agora_epoch=AGORA)

        assert d.acao is AcaoNaCotacao.MANTER
        assert d.motivo == "sem_candidata_que_pontue"


class TestACotacaoQueMorreu:
    """Manter cotação que não pontua é pagar risco de execução por zero
    reward. Isso vence QUALQUER histerese."""

    def test_atual_que_nao_pontua_mais_e_trocada_mesmo_recem_colocada(self):
        aberta = CotacaoAberta(Cotacao(1, 50.0), desde_epoch=AGORA - 1.0)
        morta = _retorno(Cotacao(1, 50.0), 0.0, pontua=False)

        d = decidir(
            aberta, _retorno(Cotacao(2, 50.0), 3.0), morta, agora_epoch=AGORA
        )

        assert d.acao is AcaoNaCotacao.REPOSICIONAR
        assert d.motivo == "atual_nao_pontua_mais"

    def test_atual_morta_sem_substituta_e_CANCELADA(self):
        """Sair do livro é diferente de trocar de lugar, e o diário precisa
        distinguir os dois."""
        aberta = CotacaoAberta(Cotacao(1, 50.0), desde_epoch=AGORA - 100.0)
        morta = _retorno(Cotacao(1, 50.0), 0.0, pontua=False)

        d = decidir(aberta, None, morta, agora_epoch=AGORA)

        assert d.acao is AcaoNaCotacao.CANCELAR
        assert d.motivo == "atual_nao_pontua_mais"

    def test_retorno_da_atual_ausente_conta_como_morta(self):
        """`None` é "não consigo avaliar", e não avaliar não autoriza ficar."""
        aberta = CotacaoAberta(Cotacao(1, 50.0), desde_epoch=AGORA - 100.0)

        d = decidir(aberta, None, None, agora_epoch=AGORA)

        assert d.acao is AcaoNaCotacao.CANCELAR


class TestHistereseDeTempo:
    """Ataca o piscar do livro: uma melhoria pode passar do piso e sumir no
    snapshot seguinte."""

    def test_nao_mexe_antes_do_tempo_minimo(self):
        aberta = CotacaoAberta(Cotacao(2, 50.0), desde_epoch=AGORA - 5.0)

        d = decidir(
            aberta,
            _retorno(Cotacao(1, 50.0), 100.0),  # ganho enorme
            _retorno(Cotacao(2, 50.0), 1.0),
            agora_epoch=AGORA,
        )

        assert d.acao is AcaoNaCotacao.MANTER
        assert d.motivo == "repousada_ha_pouco_tempo"

    def test_depois_do_tempo_minimo_o_ganho_decide(self):
        aberta = CotacaoAberta(
            Cotacao(2, 50.0), desde_epoch=AGORA - SEGUNDOS_MINIMOS_REPOUSADA - 1
        )

        d = decidir(
            aberta,
            _retorno(Cotacao(1, 50.0), 100.0),
            _retorno(Cotacao(2, 50.0), 1.0),
            agora_epoch=AGORA,
        )

        assert d.acao is AcaoNaCotacao.REPOSICIONAR


class TestHistereseDeGanho:
    """Filtra melhoria irrelevante: o ganho é estimado com hipótese de
    captura, e o custo (a fila) não é medido."""

    def test_ganho_abaixo_do_piso_nao_justifica_perder_a_fila(self):
        aberta = CotacaoAberta(Cotacao(2, 50.0), desde_epoch=AGORA - 1000.0)

        d = decidir(
            aberta,
            _retorno(Cotacao(1, 50.0), 1.0 + GANHO_MINIMO_USDC / 2),
            _retorno(Cotacao(2, 50.0), 1.0),
            agora_epoch=AGORA,
        )

        assert d.acao is AcaoNaCotacao.MANTER
        assert d.motivo == "ganho_abaixo_do_piso"

    def test_ganho_acima_do_piso_justifica(self):
        aberta = CotacaoAberta(Cotacao(2, 50.0), desde_epoch=AGORA - 1000.0)

        d = decidir(
            aberta,
            _retorno(Cotacao(1, 50.0), 1.0 + GANHO_MINIMO_USDC * 2),
            _retorno(Cotacao(2, 50.0), 1.0),
            agora_epoch=AGORA,
        )

        assert d.acao is AcaoNaCotacao.REPOSICIONAR
        assert d.motivo == "ganho_justifica_perder_a_fila"
        assert d.ganho_estimado_usdc == pytest.approx(GANHO_MINIMO_USDC * 2)

    def test_o_ganho_e_contra_a_ATUAL_no_livro_de_agora(self):
        """E não contra o que ela valia quando foi colocada.

        Comparar com o valor antigo mediria a mudança do LIVRO, não a
        vantagem de trocar — e mandaria reposicionar toda vez que o mercado
        piorasse para todo mundo por igual.
        """
        aberta = CotacaoAberta(Cotacao(2, 50.0), desde_epoch=AGORA - 1000.0)
        # As duas caíram junto: a nova rende 2, a atual rende 1,9. Diferença
        # de 0,1 está abaixo do piso, mesmo que a atual valesse 50 antes.
        d = decidir(
            aberta,
            _retorno(Cotacao(1, 50.0), 2.0),
            _retorno(Cotacao(2, 50.0), 1.9),
            agora_epoch=AGORA,
        )

        assert d.acao is AcaoNaCotacao.MANTER


class TestEstabilidade:
    def test_a_melhor_de_agora_ser_a_mesma_nao_mexe_em_nada(self):
        aberta = CotacaoAberta(Cotacao(1, 50.0), desde_epoch=AGORA - 1000.0)

        d = decidir(
            aberta,
            _retorno(Cotacao(1, 50.0), 5.0),
            _retorno(Cotacao(1, 50.0), 5.0),
            agora_epoch=AGORA,
        )

        assert d.acao is AcaoNaCotacao.MANTER
        assert d.motivo == "estavel"

    def test_atual_pontua_e_nao_ha_candidata_calculada_mantem(self):
        aberta = CotacaoAberta(Cotacao(1, 50.0), desde_epoch=AGORA - 1000.0)

        d = decidir(
            aberta, None, _retorno(Cotacao(1, 50.0), 5.0), agora_epoch=AGORA
        )

        assert d.acao is AcaoNaCotacao.MANTER
        assert d.motivo == "estavel"


class TestOMotivoSempreTemNome:
    """Mesma regra do `risk/gates.py`: ação sem nome não vira métrica, e não
    distingue "está estável" de "está preso"."""

    @pytest.mark.parametrize(
        ("aberta", "melhor", "atual"),
        [
            (None, None, None),
            (None, "boa", None),
            ("velha", None, None),
            ("velha", "boa", "viva"),
            ("nova", "boa", "viva"),
        ],
    )
    def test_toda_decisao_sai_com_motivo(self, aberta, melhor, atual):
        from pulsearb.live.repouso import MOTIVOS

        ab = None
        if aberta:
            idade = 1.0 if aberta == "nova" else 1000.0
            ab = CotacaoAberta(Cotacao(2, 50.0), desde_epoch=AGORA - idade)
        m = _retorno(Cotacao(1, 50.0), 9.0) if melhor else None
        a = _retorno(Cotacao(2, 50.0), 1.0) if atual else None

        d = decidir(ab, m, a, agora_epoch=AGORA)

        assert d.motivo in MOTIVOS, f"motivo sem nome: {d.motivo!r}"
