"""M2.11 — tolerância relativa no gate da âncora.

O gate da região de 100% era binário: uma janela discordante, de qualquer
magnitude, apagava um τ. No bloco de 21/08 a única discordante de τ=0 errou
por 2,06e-6 — 0,16 USD num BTC de 78.640, com âncora fresca.

Cada teste aqui trava uma metade da regra. As duas importam: uma tolerância
que só absorve é um alarme desligado, e um gate que só reprova é o problema
que este ciclo veio consertar.
"""

from __future__ import annotations

import pytest

from pulsearb.analysis import anchor_sweep
from pulsearb.analysis.anchor_sweep import (
    E18,
    FOLGA_RELATIVA_DEN,
    FOLGA_RELATIVA_NUM,
    JanelaResolvida,
    StreamE18,
    _consistente,
    _folga_relativa_ppb,
    ancora_verificada,
    valor_final,
    varrer,
)

BASE_MS = 1_787_000_000_000
ABERTURA_MS = BASE_MS + 240_000
FECHAMENTO_MS = BASE_MS + 300_000
# O preço do caso real: btc-updown-5m-1787354400 fechou perto de 78.640 USD.
PRECO = 78_640 * E18


def _stream(final: int, *, ultimos_60s: int | None = None):
    """Stream constante em PRECO, com o fechamento (e opcionalmente a cauda
    de 60s) trocados. Constante fora disso para que τ=0 leia PRECO limpo."""
    valores = []
    for segundo in range(400):
        instante = BASE_MS + segundo * 1000
        if instante == FECHAMENTO_MS:
            valor = final
        elif ultimos_60s is not None and FECHAMENTO_MS - 60_000 <= instante:
            valor = ultimos_60s
        else:
            valor = PRECO
        valores.append((instante, valor))
    return {"btc": valores}


def _janela(resolveu_up: bool, slug: str = "btc-updown-5m-teste"):
    return JanelaResolvida(
        slug=slug,
        asset="btc",
        abertura_ms=ABERTURA_MS,
        fechamento_ms=FECHAMENTO_MS,
        resolveu_up=resolveu_up,
    )


class TestOsDoisLadosDoLimiar:
    def test_abaixo_do_limiar_vira_indeterminada(self):
        """O caso real: 1e-6 de folga, resolução para o outro lado.

        Sem tolerância isto é uma discordante e apaga τ=0 da região de 100%.
        """
        # final < ancora (aponta Down) mas resolveu Up: inconsistente.
        saida = varrer([_janela(True)], _stream(PRECO - PRECO // 1_000_000))
        fino = saida["final_stream_no_fechamento"]

        assert fino["indeterminadas_em_tau"]["0"] == 1
        assert saida["discordantes_em_tau_verificado"] == []
        assert fino["avaliadas_em_tau"]["0"] == 0

    def test_acima_do_limiar_continua_discordante(self):
        """1e-4 é dez vezes o limiar — e é o que a tolerância NÃO pode comer.

        Uma âncora de fonte diferente daria folgas desta ordem ou maiores.
        """
        saida = varrer([_janela(True)], _stream(PRECO - PRECO // 10_000))
        fino = saida["final_stream_no_fechamento"]

        assert fino["indeterminadas_em_tau"].get("0") is None
        discordantes = saida["discordantes_em_tau_verificado"]
        assert len(discordantes) == 1
        assert discordantes[0]["folga_relativa_ppb"] == pytest.approx(100_000, rel=0.01)
        assert fino["curva"]["0"] == 0.0

    def test_a_fronteira_e_estrita(self):
        # Exatamente no limiar NÃO é indeterminado: `<`, não `<=`. Sem isto,
        # o limiar publicado e o limiar aplicado diferem por um caso.
        escala = PRECO
        no_limiar = escala * FOLGA_RELATIVA_NUM // FOLGA_RELATIVA_DEN
        _, _, indeterminada = _consistente(True, escala - no_limiar, 1, escala)
        assert not indeterminada

        _, _, logo_abaixo = _consistente(True, escala - no_limiar + 1, 1, escala)
        assert logo_abaixo


class TestEmpateExato:
    """Empate exato não é folga pequena: é folga NENHUMA.

    A regra documentada manda resolver Up (API_NOTES 12.4). Se a resolução
    disser Down, isso é evidência sobre o desempate, e engoli-la como
    "indeterminada" apagaria justamente o que `empates_exatos` mede.
    """

    def test_empate_nao_e_indeterminado(self):
        consistente, empate, indeterminada = _consistente(True, PRECO, 1, PRECO)
        assert empate
        assert consistente
        assert not indeterminada

    def test_empate_que_contradiz_a_regra_continua_discordante(self):
        # Empate resolve Up; esta janela resolveu Down.
        saida = varrer([_janela(False)], _stream(PRECO))
        discordantes = saida["discordantes_em_tau_verificado"]

        assert len(discordantes) == 1
        assert discordantes[0]["empate_exato"] is True
        assert discordantes[0]["folga_relativa_ppb"] == 0


class TestAFamiliaPerdedoraNaoPodeSerPROMOVIDA:
    """O critério de rejeição do limiar, escrito antes de haver resultado.

    Se as falhas da `media_60s` — a família reconhecidamente errada — forem
    todas absorvidas, o limiar apagou a diferença que a varredura existe para
    medir. O relatório não pode deixar isso passar em silêncio.
    """

    def test_regiao_perfeita_nunca_aparece_sem_a_contagem_que_a_produziu(self):
        # Cauda de 60s um tico abaixo: a média fica ~1e-6 abaixo da âncora e
        # aponta Down, enquanto o fechamento empata e aponta Up.
        saida = varrer(
            [_janela(True)], _stream(PRECO, ultimos_60s=PRECO - PRECO // 1_000_000)
        )
        media = saida["final_media_60s"]

        if media["regiao_viavel_100pct"]:
            # Foi promovida — então a contagem que a promoveu tem de estar
            # ao lado, no mesmo bloco, para quem lê poder desconfiar.
            assert media["indeterminadas_em_tau"], (
                "familia perfeita sem indeterminadas registradas: a promocao "
                "ficou invisivel, que e exatamente o modo de falha do limiar"
            )

    def test_a_distribuicao_das_folgas_permite_rever_o_limiar(self):
        saida = varrer([_janela(True)], _stream(PRECO - PRECO // 1_000_000))
        distribuicao = saida["distribuicao_das_folgas_relativas"]

        assert distribuicao["janelas_com_folga"] == 1
        assert distribuicao["limiar_ppb"] == 10_000
        assert distribuicao["abaixo_do_limiar"] == 1
        # 1e-6 = 1.000 ppb, na década imediatamente abaixo do limiar.
        assert distribuicao["histograma_ppb"]["1e-6..1e-5"] == 1
        assert distribuicao["max_ppb"] == pytest.approx(1_000, rel=0.01)


class TestONumeroDoBacktestNaoMuda:
    """A tolerância vive na VARREDURA. O caminho que decide trade não a lê.

    Se ela vazasse para `ancora_verificada` ou `valor_final`, mudaria PnL,
    trades e profundidade — e este ciclo teria alterado um resultado sem
    dizer, que é o pior desfecho possível para uma mudança de instrumento.
    """

    def test_ancora_e_final_ignoram_a_tolerancia(self, monkeypatch):
        serie = StreamE18(_stream(PRECO - PRECO // 1_000_000)["btc"])
        antes = (
            ancora_verificada(serie, ABERTURA_MS),
            valor_final(serie, FECHAMENTO_MS),
        )

        # Tolerância de 100%: se houvesse qualquer leitura dela no caminho do
        # backtest, este monkeypatch mudaria o resultado.
        monkeypatch.setattr(anchor_sweep, "FOLGA_RELATIVA_DEN", 1)
        depois = (
            ancora_verificada(serie, ABERTURA_MS),
            valor_final(serie, FECHAMENTO_MS),
        )

        assert antes == depois
        assert antes[0] == PRECO


class TestAFolgaRelativa:
    def test_e_divisao_inteira_sem_ponto_flutuante(self):
        # 1 wei em 2096: 4,8e-22, que em ppb arredonda para 0 — e 0 aqui quer
        # dizer "abaixo da resolucao do histograma", nao "sem folga". Quem
        # distingue os dois e `empate_exato`.
        assert _folga_relativa_ppb(2096 * E18 - 1, 1, 2096 * E18) == 0

    def test_escala_nao_positiva_devolve_none(self):
        # Zero diria "coladissimo" onde a verdade e "nao da para dizer".
        assert _folga_relativa_ppb(10, 1, 0) is None
        assert _folga_relativa_ppb(10, 0, PRECO) is None
