"""M4.1 — os portões que decidem se uma ordem pode ser enviada.

Cada teste aqui trava um jeito de perder dinheiro. Nenhum é decorativo:
todos descrevem uma sequência concreta que, sem o portão, sangraria.
"""

from __future__ import annotations

import json

import pytest

from pulsearb.risk import MOTIVOS, Decisao, OrdemPretendida, PortaoDeRisco
from pulsearb.settings import Mode, RiskSettings


def _ordem(slug: str = "btc-updown-5m-1", shares: float = 5.0, preco: float = 0.50):
    return OrdemPretendida(
        slug=slug,
        token_id="tok-up",
        lado_up=True,
        shares=shares,
        preco_limite=preco,
    )


def _portao(tmp_path, modo=Mode.LIVE, **ajustes):
    return PortaoDeRisco(
        RiskSettings(**ajustes),
        modo,
        caminho_do_registro=tmp_path / "registro.json",
        hoje="2026-08-25",
    )


#: Livro sadio: spread de 0,02, dentro do teto de 0,04. Passa em `_avaliar`
#: por padrão para que cada teste exercite o portão que ele nomeia, e não o
#: do livro. Quem quer testar o livro passa o dele.
LIVRO_SADIO = {"melhor_bid": 0.49, "melhor_ask": 0.51}


def _avaliar(portao, ordem, *, feeds_saudaveis=True, **livro):
    return portao.avaliar(
        ordem, feeds_saudaveis=feeds_saudaveis, **(LIVRO_SADIO | livro)
    )


class TestFalhaFechada:
    """Estado desconhecido é motivo de recusa, não de seguir em frente."""

    def test_ordem_de_teste_passa(self, tmp_path):
        # A linha de base: sem ela, um teste que "recusa tudo" passaria em
        # todos os outros sem provar nada.
        assert _avaliar(_portao(tmp_path), _ordem(), feeds_saudaveis=True).pode

    @pytest.mark.parametrize("modo", [Mode.SIM, Mode.SHADOW])
    def test_so_live_envia(self, tmp_path, modo):
        decisao = _avaliar(_portao(tmp_path, modo=modo), _ordem(), feeds_saudaveis=True)
        assert not decisao.pode
        assert decisao.motivo == MOTIVOS.MODO_NAO_OPERA

    def test_feed_parado_recusa(self, tmp_path):
        # Operar com feed velho é operar com preço que já não existe.
        decisao = _avaliar(_portao(tmp_path), _ordem(), feeds_saudaveis=False)
        assert not decisao.pode
        assert decisao.motivo == MOTIVOS.FEED_PARADO

    @pytest.mark.parametrize(
        ("shares", "preco"),
        [(0.0, 0.5), (-1.0, 0.5), (5.0, 0.0), (5.0, 1.0), (5.0, -0.1), (5.0, 1.5)],
    )
    def test_ordem_mal_formada_recusa_antes_de_tudo(self, tmp_path, shares, preco):
        decisao = _avaliar(_portao(tmp_path),
            _ordem(shares=shares, preco=preco), feeds_saudaveis=True
        )
        assert not decisao.pode
        assert decisao.motivo == MOTIVOS.ORDEM_MAL_FORMADA

    def test_registro_ilegivel_arma_o_disjuntor(self, tmp_path):
        # Não dá para distinguir "arquivo corrompido" de "arquivo com o
        # disjuntor armado que não consigo ler". A leitura segura é a segunda.
        caminho = tmp_path / "registro.json"
        caminho.write_text("{isto nao e json", encoding="utf-8")
        portao = PortaoDeRisco(
            RiskSettings(), Mode.LIVE, caminho_do_registro=caminho, hoje="2026-08-25"
        )

        assert portao.registro.disjuntor_armado
        decisao = _avaliar(portao, _ordem(), feeds_saudaveis=True)
        assert decisao.motivo == MOTIVOS.DISJUNTOR_ARMADO


class TestTetos:
    def test_stake_por_trade(self, tmp_path):
        portao = _portao(tmp_path, stake_max_por_trade_usdc=5.0)
        # 12 shares a 0,50 = 6 USDC, acima do teto de 5.
        decisao = _avaliar(portao, _ordem(shares=12.0), feeds_saudaveis=True)
        assert decisao.motivo == MOTIVOS.STAKE_ACIMA_DO_TETO
        assert decisao.detalhe["custo"] == pytest.approx(6.0)

    def test_teto_por_janela_pega_o_que_o_teto_por_trade_nao_pega(self, tmp_path):
        """Três entradas de 5 no MESMO mercado são uma aposta só.

        Cada uma passa no teto por trade. Somadas, são o triplo da exposição
        pretendida no mesmo movimento — e é exatamente isso que o teto por
        janela existe para impedir.
        """
        portao = _portao(
            tmp_path, stake_max_por_trade_usdc=5.0, stake_max_por_janela_usdc=7.0
        )
        ordem = _ordem(shares=10.0)  # 5,00 USDC — passa no teto por trade

        assert _avaliar(portao, ordem, feeds_saudaveis=True).pode
        portao.registrar_envio(ordem)

        segunda = _avaliar(portao, ordem, feeds_saudaveis=True)
        assert segunda.motivo == MOTIVOS.JANELA_NO_TETO
        assert segunda.detalhe["ja_gasto"] == pytest.approx(5.0)

    def test_exposicao_total_soma_janelas_diferentes(self, tmp_path):
        portao = _portao(
            tmp_path,
            stake_max_por_trade_usdc=5.0,
            stake_max_por_janela_usdc=5.0,
            exposicao_max_usdc=9.0,
        )
        portao.registrar_envio(_ordem("janela-a", shares=10.0))

        decisao = _avaliar(portao, _ordem("janela-b", shares=10.0), feeds_saudaveis=True)
        assert decisao.motivo == MOTIVOS.EXPOSICAO_NO_TETO

    def test_posicoes_abertas(self, tmp_path):
        portao = _portao(tmp_path, posicoes_max_abertas=2)
        for nome in ("a", "b"):
            portao.registrar_envio(_ordem(nome, shares=2.0))

        assert _avaliar(portao, _ordem("c", shares=2.0), feeds_saudaveis=True).motivo == (
            MOTIVOS.POSICOES_NO_TETO
        )
        # Reforçar uma janela QUE JÁ TEM posição não abre posição nova.
        assert _avaliar(portao, _ordem("a", shares=2.0), feeds_saudaveis=True).pode

    @pytest.mark.parametrize("preco", [0.02, 0.04, 0.96, 0.99])
    def test_preco_fora_da_faixa(self, tmp_path, preco):
        # Comprar a 0,97 arrisca 0,97 para ganhar 0,03: um erro de modelo
        # pequeno vira perda desproporcional.
        decisao = _avaliar(_portao(tmp_path),
            _ordem(shares=1.0, preco=preco), feeds_saudaveis=True
        )
        assert decisao.motivo == MOTIVOS.PRECO_FORA_DA_FAIXA


class TestDisjuntor:
    def test_perda_do_dia_arma(self, tmp_path):
        portao = _portao(tmp_path, perda_max_diaria_usdc=10.0)
        portao.registrar_envio(_ordem("a", shares=4.0))
        portao.registrar_resolucao("a", -10.5)

        assert portao.registro.disjuntor_armado
        assert _avaliar(portao, _ordem("b"), feeds_saudaveis=True).motivo == (
            MOTIVOS.DISJUNTOR_ARMADO
        )

    def test_disjuntor_nao_desarma_quando_o_numero_melhora(self, tmp_path):
        """A armadilha: perdeu 11, disjuntor arma, ganha 5, PnL vai a −6.

        Se o disjuntor olhasse só o número atual, ele voltaria a operar com
        o mesmo modelo que acabou de perder. Ele gruda de propósito.
        """
        portao = _portao(tmp_path, perda_max_diaria_usdc=10.0)
        portao.registrar_resolucao("a", -11.0)
        assert portao.registro.disjuntor_armado

        portao.registrar_resolucao("b", +5.0)
        assert portao.registro.pnl_realizado_usdc == pytest.approx(-6.0)
        assert portao.registro.disjuntor_armado

    def test_disjuntor_sobrevive_a_reinicio(self, tmp_path):
        """Bot perde, processo cai, systemd reinicia, contador zera, perde de novo.

        Sem persistência o disjuntor vira um limite por VIDA DE PROCESSO, que
        não é limite nenhum.
        """
        caminho = tmp_path / "registro.json"
        primeiro = PortaoDeRisco(
            RiskSettings(perda_max_diaria_usdc=10.0),
            Mode.LIVE,
            caminho_do_registro=caminho,
            hoje="2026-08-25",
        )
        primeiro.registrar_resolucao("a", -12.0)

        renascido = PortaoDeRisco(
            RiskSettings(perda_max_diaria_usdc=10.0),
            Mode.LIVE,
            caminho_do_registro=caminho,
            hoje="2026-08-25",
        )
        assert renascido.registro.disjuntor_armado
        assert _avaliar(renascido, _ordem(), feeds_saudaveis=True).motivo == (
            MOTIVOS.DISJUNTOR_ARMADO
        )

    def test_virada_de_dia_zera_o_gasto_mas_nao_o_disjuntor(self, tmp_path):
        caminho = tmp_path / "registro.json"
        ontem = PortaoDeRisco(
            RiskSettings(perda_max_diaria_usdc=10.0),
            Mode.LIVE,
            caminho_do_registro=caminho,
            hoje="2026-08-25",
        )
        ontem.registrar_envio(_ordem("a", shares=4.0))
        ontem.registrar_resolucao("a", -12.0)

        hoje = PortaoDeRisco(
            RiskSettings(perda_max_diaria_usdc=10.0),
            Mode.LIVE,
            caminho_do_registro=caminho,
            hoje="2026-08-26",
        )
        assert hoje.registro.pnl_realizado_usdc == 0.0
        assert hoje.registro.exposicao_total_usdc == 0.0
        # A data virou; a decisão de parar não foi revista por ninguém.
        assert hoje.registro.disjuntor_armado

    def test_so_desarme_explicito_libera(self, tmp_path):
        portao = _portao(tmp_path, perda_max_diaria_usdc=10.0)
        portao.registrar_resolucao("a", -12.0)
        portao.desarmar_disjuntor()

        assert _avaliar(portao, _ordem(), feeds_saudaveis=True).pode


class TestContabilidade:
    def test_resolucao_libera_a_exposicao(self, tmp_path):
        portao = _portao(tmp_path, exposicao_max_usdc=6.0)
        portao.registrar_envio(_ordem("a", shares=10.0))
        assert portao.registro.exposicao_total_usdc == pytest.approx(5.0)

        portao.registrar_resolucao("a", +1.0)
        assert portao.registro.exposicao_total_usdc == 0.0
        assert _avaliar(portao, _ordem("b", shares=10.0), feeds_saudaveis=True).pode

    def test_registro_e_gravado_de_forma_legivel(self, tmp_path):
        portao = _portao(tmp_path)
        portao.registrar_envio(_ordem("a", shares=4.0))

        gravado = json.loads((tmp_path / "registro.json").read_text(encoding="utf-8"))
        assert gravado["dia"] == "2026-08-25"
        assert gravado["gasto_por_janela"]["a"] == pytest.approx(2.0)
        assert gravado["disjuntor_armado"] is False


class TestMotivoNomeado:
    def test_recusa_sem_nome_e_erro_de_programacao(self):
        # Recusa anônima não vira métrica nem alarme, e não distingue
        # "o bot está travado" de "o bot não achou trade".
        with pytest.raises(ValueError, match="motivo de recusa desconhecido"):
            Decisao(False, "porque sim")

    def test_decisao_positiva_nao_carrega_motivo(self):
        with pytest.raises(ValueError, match="não carrega motivo"):
            Decisao(True, MOTIVOS.FEED_PARADO)
