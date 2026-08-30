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


class _RelogioFalso:
    """Dublê da fonte de atraso do item 3.10. O teste manda o número."""

    def __init__(self, atraso_ms: float | None = 12.0) -> None:
        self.valor = atraso_ms
        self.perguntas = 0

    def atraso_ms(self, *, agora_ms: int) -> float | None:
        self.perguntas += 1
        return self.valor


def _portao(tmp_path, modo=Mode.LIVE, *, relogio_do_servidor=..., **ajustes):
    # Fonte de relógio SADIA por padrão, pelo mesmo motivo do `LIVRO_SADIO`:
    # cada teste exercita o portão que nomeia, não o do relógio. Quem quer
    # testar o relógio passa o dele — inclusive `None`, para o caso de a
    # trava não estar instalada.
    return PortaoDeRisco(
        RiskSettings(**ajustes),
        modo,
        caminho_do_registro=tmp_path / "registro.json",
        hoje="2026-08-25",
        relogio_do_servidor=(
            _RelogioFalso() if relogio_do_servidor is ... else relogio_do_servidor
        ),
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


class TestPortaoDoRelogio:
    """3.10 — a terceira trava, que até 2026-08-30 não tinha fonte.

    Feed velho e spread anômalo já eram medidos. "O relógio derivou" não era
    medido em lugar nenhum, então o portão não podia recusar por isso nem
    quando fosse verdade. Cada teste aqui trava uma parte do contrato novo.
    """

    def test_atraso_dentro_do_teto_deixa_passar(self, tmp_path):
        portao = _portao(tmp_path, relogio_do_servidor=_RelogioFalso(120.0))
        assert _avaliar(portao, _ordem()).pode

    def test_atraso_acima_do_teto_recusa(self, tmp_path):
        portao = _portao(tmp_path, relogio_do_servidor=_RelogioFalso(300.0))
        decisao = _avaliar(portao, _ordem())

        assert not decisao.pode
        assert decisao.motivo == MOTIVOS.RELOGIO_DERIVADO
        assert decisao.detalhe["atraso_ms"] == 300.0
        assert decisao.detalhe["teto_ms"] == 250.0

    def test_carimbo_no_futuro_recusa_igual(self, tmp_path):
        """Atraso NEGATIVO é relógio local atrasado, e custa o mesmo.

        O servidor não manda evento do futuro. Se o carimbo dele está à
        frente do nosso relógio, quem está errado somos nós — e o
        `seconds_left` sai errado na direção oposta, mas igualmente cara.
        Comparar só o lado positivo deixaria essa metade passar.
        """
        decisao = _avaliar(
            _portao(tmp_path, relogio_do_servidor=_RelogioFalso(-400.0)), _ordem()
        )

        assert decisao.motivo == MOTIVOS.RELOGIO_DERIVADO
        assert decisao.detalhe["atraso_ms"] == -400.0

    def test_fonte_muda_recusa_por_nao_saber(self, tmp_path):
        """Fonte instalada devolvendo `None` é "não sei", e não sei é recusa.

        Acontece quando nunca chegou tick, ou quando o último é velho demais.
        Tratar não-sei como zero seria dar nota máxima ao caso em que a
        medição parou de existir — o defeito do `cobertura_da_gravacao` que o
        M2 já pagou uma vez.
        """
        decisao = _avaliar(
            _portao(tmp_path, relogio_do_servidor=_RelogioFalso(None)), _ordem()
        )

        assert decisao.motivo == MOTIVOS.RELOGIO_NAO_MONITORADO
        assert decisao.detalhe["atraso_ms"] is None

    def test_em_LIVE_sem_fonte_instalada_recusa_tudo(self, tmp_path):
        """A decisão menos confortável do arquivo, e a mais importante.

        Uma trava que se auto-desativa quando ninguém a ligou não é trava. Se
        este teste for "consertado" afrouxando o portão, o bot volta a poder
        operar em LIVE com o relógio sem vigilância nenhuma.
        """
        decisao = _avaliar(_portao(tmp_path, relogio_do_servidor=None), _ordem())

        assert decisao.motivo == MOTIVOS.RELOGIO_NAO_MONITORADO
        assert decisao.detalhe["fonte"] is None

    def test_fora_do_LIVE_a_ausencia_da_fonte_nao_recusa(self, tmp_path):
        """O SHADOW existe para ensaiar, e recusar tudo ali apaga o ensaio.

        Mesma razão pela qual `avaliar_risco` não roda o portão de modo: o
        diário do shadow precisa dizer qual portão SEGURARIA se fosse LIVE,
        e um `relogio_nao_monitorado` em toda linha esconderia isso.
        """
        portao = _portao(tmp_path, modo=Mode.SHADOW, relogio_do_servidor=None)
        decisao = portao.avaliar_risco(
            _ordem(), feeds_saudaveis=True, **LIVRO_SADIO
        )

        assert decisao.pode

    def test_o_relogio_e_conferido_no_shadow_quando_ha_fonte(self, tmp_path):
        """Com fonte instalada, o SHADOW confere igual — senão não ensaia."""
        portao = _portao(
            tmp_path, modo=Mode.SHADOW, relogio_do_servidor=_RelogioFalso(900.0)
        )
        decisao = portao.avaliar_risco(
            _ordem(), feeds_saudaveis=True, **LIVRO_SADIO
        )

        assert decisao.motivo == MOTIVOS.RELOGIO_DERIVADO

    def test_feed_parado_vence_relogio_derivado(self, tmp_path):
        """Ordem entre os dois: o feed é a causa mais geral.

        Feed parado explica por que o relógio parece derivado (a última
        amostra é velha); o contrário não vale. Registrar o relógio ali
        mandaria quem lê o diário investigar o relógio quando o problema é o
        feed.
        """
        portao = _portao(tmp_path, relogio_do_servidor=_RelogioFalso(900.0))
        decisao = _avaliar(portao, _ordem(), feeds_saudaveis=False)

        assert decisao.motivo == MOTIVOS.FEED_PARADO


class TestOEnsaioNaoContaminaORegistroReal:
    """Achado P1 do Codex no #52.

    `ExecutorSombra` chama `registrar_envio` de propósito — sem isso os tetos
    por janela e de exposição nunca seriam exercitados, e o ensaio não
    ensaiaria a parte que mais importa. Só que `registrar_envio` GRAVA, e o
    caminho vinha do mesmo `caminho_do_registro` que o LIVE usará.
    """

    def test_LIVE_escreve_no_caminho_configurado(self, tmp_path):
        """O dinheiro real fica onde a configuração mandou. Sem sufixo."""
        portao = _portao(tmp_path, modo=Mode.LIVE)

        assert portao.caminho == tmp_path / "registro.json"

    @pytest.mark.parametrize("modo", [Mode.SHADOW, Mode.SIM])
    def test_ensaio_ganha_arquivo_proprio(self, tmp_path, modo):
        portao = _portao(tmp_path, modo=modo)

        assert portao.caminho == tmp_path / f"registro.{modo.value.lower()}.json"

    def test_o_ensaio_NAO_toca_no_arquivo_do_LIVE(self, tmp_path):
        """O teste que importa: contaminação, não nomenclatura.

        A sequência concreta: SHADOW aprova intenção, `registrar_envio` grava,
        o processo morre antes de a janela fechar. `_liquidar` só roda com o
        processo vivo — depois de reiniciar, aquele slug já passou e nunca
        mais será liquidado. Com um arquivo só, a exposição sintética ficaria
        presa no registro do dinheiro real, para sempre, e o teto passaria a
        recusar intenção legítima.
        """
        real = tmp_path / "registro.json"
        ensaio = _portao(tmp_path, modo=Mode.SHADOW)

        ensaio.registrar_envio(_ordem(shares=5.0, preco=0.50))

        assert not real.exists()
        assert ensaio.caminho is not None and ensaio.caminho.exists()
        assert ensaio.registro.exposicao_total_usdc == pytest.approx(2.5)

    def test_o_LIVE_nao_herda_a_exposicao_do_ensaio(self, tmp_path):
        """E o outro lado da mesma moeda, que é o pior dos dois.

        PnL, sequência de perdas e disjuntor de um ensaio entrando no registro
        durável do dinheiro real corromperiam o histórico de segurança.
        """
        ensaio = _portao(tmp_path, modo=Mode.SHADOW)
        ensaio.registrar_envio(_ordem(shares=5.0, preco=0.50))

        real = _portao(tmp_path, modo=Mode.LIVE)

        assert real.registro.exposicao_total_usdc == 0.0
        assert real.registro.gasto_por_janela == {}

    def test_o_KILL_continua_compartilhado(self, tmp_path):
        """A assimetria é de propósito.

        A chave de emergência existe para parar TUDO. Um KILL por modo faria
        `touch KILL` numa sessão ssh parar metade do que está rodando —
        exatamente o contrário do que a chave promete.
        """
        kill = tmp_path / "KILL"
        portoes = [
            PortaoDeRisco(
                RiskSettings(),
                modo,
                caminho_do_registro=tmp_path / "registro.json",
                caminho_do_kill=kill,
                hoje="2026-08-25",
                relogio_do_servidor=_RelogioFalso(),
            )
            for modo in (Mode.LIVE, Mode.SHADOW, Mode.SIM)
        ]

        assert {p.caminho_do_kill for p in portoes} == {kill}

    def test_sem_caminho_configurado_segue_sem_caminho(self, tmp_path):
        """`None` é memória, e memória não contamina ninguém."""
        portao = PortaoDeRisco(RiskSettings(), Mode.SHADOW, hoje="2026-08-25")

        assert portao.caminho is None
