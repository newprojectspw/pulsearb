"""M4 — o livro de cada token, ao vivo.

Dois testes aqui travam lições que o M2 pagou caro: silêncio é por TOKEN e não
por feed, e delta sem snapshot é contado em vez de engolido.
"""

from __future__ import annotations

import pytest

from pulsearb.backtest.book import OrderBook
from pulsearb.live.livros import LivrosAoVivo

SEGUNDO = 10**9


def _snapshot(token: str = "tok", bid: str = "0.49", ask: str = "0.51"):
    return {
        "event_type": "book",
        "asset_id": token,
        "bids": [{"price": bid, "size": "500"}],
        "asks": [{"price": ask, "size": "500"}],
    }


def _delta(token: str = "tok", preco: str = "0.52", size: str = "300", lado="SELL"):
    return {
        "event_type": "price_change",
        "asset_id": token,
        "changes": [{"price": preco, "size": size, "side": lado}],
    }


class TestSnapshotEDelta:
    def test_snapshot_torna_o_livro_confiavel(self):
        livros = LivrosAoVivo()
        livros.aplicar(_snapshot(), ts_ns=SEGUNDO)

        assert livros.confiavel("tok", agora_ns=SEGUNDO)
        livro = livros.livro("tok", agora_ns=SEGUNDO)
        assert livro is not None
        assert livro.best_bid == pytest.approx(0.49)
        assert livro.best_ask == pytest.approx(0.51)

    def test_delta_atualiza_e_renova_a_idade(self):
        livros = LivrosAoVivo()
        livros.aplicar(_snapshot(), ts_ns=SEGUNDO)
        livros.aplicar(_delta(preco="0.50", size="100"), ts_ns=5 * SEGUNDO)

        registro = livros.por_token["tok"]
        assert registro.ultimo_evento_ns == 5 * SEGUNDO
        assert registro.deltas_sem_snapshot == 0


class TestDeltaSemSnapshot:
    """A gravação de 20 h mediu 187.452 observações sem snapshot.

    Aplicá-las a um livro vazio inventaria profundidade; ignorá-las em
    silêncio esconderia que o livro está incompleto.
    """

    def test_delta_orfao_nao_cria_livro(self):
        livros = LivrosAoVivo()
        livros.aplicar(_delta(), ts_ns=SEGUNDO)

        assert livros.deltas_orfaos == 1
        assert livros.livro("tok", agora_ns=SEGUNDO) is None
        assert not livros.confiavel("tok", agora_ns=SEGUNDO)

    def test_snapshot_depois_do_orfao_recupera(self):
        livros = LivrosAoVivo()
        livros.aplicar(_delta(), ts_ns=SEGUNDO)
        livros.aplicar(_snapshot(), ts_ns=2 * SEGUNDO)

        assert livros.confiavel("tok", agora_ns=2 * SEGUNDO)
        # O contador NÃO zera: ele mede quanto do fio se perdeu, e isso
        # aconteceu de verdade.
        assert livros.deltas_orfaos == 1


class TestSilencioPorToken:
    """O feed pode estar impecável e um token específico estar mudo.

    É a mesma lição do M2.7/M2.10 no RTDS — tópico mudo com a conexão viva —,
    e o portão `feed_parado` não a cobre porque ele olha o feed.
    """

    def test_token_mudo_deixa_de_ser_confiavel(self):
        livros = LivrosAoVivo(silencio_do_token_s=10.0)
        livros.aplicar(_snapshot(), ts_ns=SEGUNDO)

        assert livros.confiavel("tok", agora_ns=SEGUNDO + 9 * SEGUNDO)
        assert not livros.confiavel("tok", agora_ns=SEGUNDO + 11 * SEGUNDO)
        assert livros.livro("tok", agora_ns=SEGUNDO + 11 * SEGUNDO) is None

    def test_um_token_mudo_nao_contamina_o_vizinho(self):
        livros = LivrosAoVivo(silencio_do_token_s=10.0)
        livros.aplicar(_snapshot("mudo"), ts_ns=SEGUNDO)
        livros.aplicar(_snapshot("vivo"), ts_ns=SEGUNDO)
        livros.aplicar(_delta("vivo"), ts_ns=20 * SEGUNDO)

        agora = 21 * SEGUNDO
        assert not livros.confiavel("mudo", agora_ns=agora)
        assert livros.confiavel("vivo", agora_ns=agora)

    def test_o_resumo_conta_os_mudos_separado_dos_sem_snapshot(self):
        # São diagnósticos diferentes: um livro sem snapshot está incompleto
        # mesmo recém-atualizado; um livro completo e parado descreve um
        # mercado que já mudou.
        livros = LivrosAoVivo(silencio_do_token_s=10.0)
        livros.aplicar(_snapshot("mudo"), ts_ns=SEGUNDO)
        livros.aplicar(_snapshot("vivo"), ts_ns=20 * SEGUNDO)
        livros.aplicar(_delta("orfao"), ts_ns=20 * SEGUNDO)

        resumo = livros.resumo(agora_ns=21 * SEGUNDO)
        assert resumo["confiaveis"] == 1
        assert resumo["mudos"] == 1
        assert resumo["deltas_orfaos"] == 1
        assert "topico mudo com a conexao viva" in resumo["nota"]


class TestEventosQueNaoMovemOLivro:
    @pytest.mark.parametrize(
        "tipo", ["last_trade_price", "tick_size_change", "new_market"]
    )
    def test_sao_ignorados_e_contados(self, tipo):
        # Contar é o que permite dizer depois que o silêncio de um token era
        # silêncio de verdade, e não falta de instrumentação.
        livros = LivrosAoVivo()
        livros.aplicar({"event_type": tipo, "asset_id": "tok"}, ts_ns=SEGUNDO)

        assert livros.eventos_ignorados == 1
        assert livros.por_token == {}

    def test_evento_sem_token_nao_derruba(self):
        livros = LivrosAoVivo()
        livros.aplicar({"event_type": "book"}, ts_ns=SEGUNDO)
        livros.aplicar({"event_type": "book", "asset_id": ""}, ts_ns=SEGUNDO)

        assert livros.eventos_ignorados == 2
        assert livros.por_token == {}


class TestAliasing:
    def test_o_livro_devolvido_e_o_VIVO_e_muda_embaixo(self):
        """Documentado de propósito, e travado aqui para ninguém se surpreender.

        Clonar a cada consulta custaria uma cópia por tick por token no
        caminho quente, para proteger um uso que a decisão não faz — ela lê e
        decide no mesmo instante.
        """
        livros = LivrosAoVivo()
        livros.aplicar(_snapshot(), ts_ns=SEGUNDO)
        guardado = livros.livro("tok", agora_ns=SEGUNDO)
        assert guardado is not None
        assert guardado.best_ask == pytest.approx(0.51)

        livros.aplicar(
            _delta(preco="0.50", size="300"), ts_ns=2 * SEGUNDO
        )
        # A referência que ficou na mão de quem consultou mudou sozinha.
        assert guardado.best_ask == pytest.approx(0.50)

    def test_clone_congela_o_instante(self):
        livros = LivrosAoVivo()
        livros.aplicar(_snapshot(), ts_ns=SEGUNDO)
        congelado = livros.livro("tok", agora_ns=SEGUNDO).clone()

        livros.aplicar(_delta(preco="0.50", size="300"), ts_ns=2 * SEGUNDO)
        assert congelado.best_ask == pytest.approx(0.51)


class TestMesmaMedidaDoBacktest:
    def test_a_profundidade_sai_da_MESMA_classe(self):
        """O critério 1.5 mediu 87,8 USDC com `OrderBook.depth_usdc`.

        Se o shadow medisse de outro jeito, a comparação entre os dois não
        diria nada sobre capacidade.
        """
        livros = LivrosAoVivo()
        livros.aplicar(_snapshot(), ts_ns=SEGUNDO)
        livro = livros.livro("tok", agora_ns=SEGUNDO)

        assert isinstance(livro, OrderBook)
        assert livro.depth_usdc(side="ask", ticks=3, tick_size=0.01) > 0


def _delta_forma_b(*tokens, preco: str = "0.52", size: str = "300", lado="SELL"):
    """A forma do SDK: SEM `asset_id` no topo, lista em `price_changes`, e
    cada entrada com o seu token. `[VERIFICADO]` API_NOTES §6.1b."""
    return {
        "event_type": "price_change",
        "market": "0xabc",
        "price_changes": [
            {"asset_id": t, "price": preco, "size": size, "side": lado}
            for t in tokens
        ],
    }


class TestOPriceChangeDaFormaDoSDK:
    """Achado P1 do Codex no #52, e era o defeito mais caro do PR.

    O `price_change` do SDK **não tem `asset_id` no topo**. `aplicar` lia o
    topo, não achava token, recusava o evento como sem token — e os livros
    ficavam parados no snapshot inicial. Depois de 10 s nenhum era confiável,
    e o SHADOW rodaria as 24 h **sem avaliar nada**, entregando um relatório
    que diria "nenhuma oportunidade".

    Por que os testes não pegaram: eles usavam a forma A (`_delta`), que é a
    fixture SINTÉTICA que o §6.1b avisa nunca ter sido confirmada contra o
    fio. O teste encodava a suposição em vez de checá-la.

    E o backtest **não** tinha o defeito, porque já roteava por
    `iter_mudancas`. A divergência apareceria como "o mercado estava
    diferente" — exatamente o que a regra do mesmo caminho existe para
    impedir.
    """

    def test_a_forma_do_SDK_move_o_livro(self):
        livros = LivrosAoVivo()
        livros.aplicar(_snapshot("tok"), ts_ns=SEGUNDO)

        livros.aplicar(_delta_forma_b("tok"), ts_ns=2 * SEGUNDO)

        assert livros.eventos_ignorados == 0
        assert livros.por_token["tok"].ultimo_evento_ns == 2 * SEGUNDO

    def test_o_livro_segue_confiavel_depois_do_silencio_do_snapshot(self):
        """O sintoma que o usuário veria: livro parado, tudo velho, zero
        avaliações — sem nenhum erro no log."""
        livros = LivrosAoVivo()
        livros.aplicar(_snapshot("tok"), ts_ns=SEGUNDO)
        assert not livros.confiavel("tok", agora_ns=30 * SEGUNDO)

        livros.aplicar(_delta_forma_b("tok"), ts_ns=29 * SEGUNDO)

        assert livros.confiavel("tok", agora_ns=30 * SEGUNDO)

    def test_UMA_mensagem_pode_cobrir_VARIOS_tokens(self):
        """`price_changes` é uma lista e as entradas podem ser de tokens
        diferentes — Up e Down do mesmo mercado, tipicamente."""
        livros = LivrosAoVivo()
        livros.aplicar(_snapshot("up"), ts_ns=SEGUNDO)
        livros.aplicar(_snapshot("dn"), ts_ns=SEGUNDO)

        livros.aplicar(_delta_forma_b("up", "dn"), ts_ns=3 * SEGUNDO)

        assert livros.por_token["up"].ultimo_evento_ns == 3 * SEGUNDO
        assert livros.por_token["dn"].ultimo_evento_ns == 3 * SEGUNDO

    def test_token_sem_snapshot_continua_contado_como_orfao(self):
        """A trava de delta órfão não pode ser perdida no conserto: aplicar
        delta a livro vazio inventaria profundidade."""
        livros = LivrosAoVivo()

        livros.aplicar(_delta_forma_b("desconhecido"), ts_ns=SEGUNDO)

        assert livros.deltas_orfaos == 1

    def test_um_token_conhecido_entre_dois_ainda_e_aplicado(self):
        """Mensagem mista não pode ser descartada inteira por causa do token
        que não acompanhamos — perderíamos o delta do que acompanhamos."""
        livros = LivrosAoVivo()
        livros.aplicar(_snapshot("up"), ts_ns=SEGUNDO)

        livros.aplicar(_delta_forma_b("up", "de-outro-mercado"), ts_ns=4 * SEGUNDO)

        assert livros.por_token["up"].ultimo_evento_ns == 4 * SEGUNDO
        assert livros.deltas_orfaos == 1

    def test_a_forma_A_continua_funcionando(self):
        """As duas formas são aceitas até a gravação dizer qual o servidor
        fala (§6.1b). Consertar B não pode quebrar A."""
        livros = LivrosAoVivo()
        livros.aplicar(_snapshot("tok"), ts_ns=SEGUNDO)

        livros.aplicar(_delta("tok"), ts_ns=2 * SEGUNDO)

        assert livros.por_token["tok"].ultimo_evento_ns == 2 * SEGUNDO
        assert livros.eventos_ignorados == 0

    def test_price_change_sem_entrada_nenhuma_e_contado(self):
        """Zero silencioso é indistinguível de bug."""
        livros = LivrosAoVivo()
        livros.aplicar(
            {"event_type": "price_change", "market": "0xabc"}, ts_ns=SEGUNDO
        )

        assert livros.eventos_ignorados == 1
