"""As duas identidades, e a trava que impede a arbitragem virar aposta.

## O que estes testes guardam

A aritmética aqui é pura e pode ser testada com livros montados à mão SEM
cair no defeito que o CLAUDE.md nomeia — a fixture não encoda suposição
nenhuma sobre o que o servidor manda, porque a conta é sobre preços e
tamanhos, não sobre a forma de um payload. O que é fato de API (se um
conjunto neg-risk é mesmo exaustivo) está FORA deste módulo de propósito, e
quem chama tem de provar.

A propriedade mais importante não é o lucro: é `conjunto_nao_exaustivo`. Um
conjunto a que falte um resultado não paga 1,00, a conta fecha bonita do
mesmo jeito, e a posição vira DIRECIONAL — exatamente o que estas rotas
existem para não ser. O erro seria silencioso.
"""

from __future__ import annotations

import pytest

from pulsearb.analysis.arbitragem import (
    MOTIVOS,
    Taxa,
    maior_cesta,
    oportunidade_de_cesta,
    oportunidade_de_escada,
)
from pulsearb.backtest.book import OrderBook

#: A taxa verificada ao vivo (§12.6): r=0,07, e=1. Não há default no módulo
#: de fees de propósito — aqui ela é explícita porque o teste é o chamador.
TAXA = Taxa(rate=0.07, exponent=1.0)
#: Uma taxa ZERO para os testes que medem a identidade sem o ruído da fee.
SEM_TAXA = Taxa(rate=0.0, exponent=1.0)


def _livro(asks=(), bids=(), asset_id="tok"):
    return OrderBook(asset_id=asset_id, asks=list(asks), bids=list(bids))


class TestACestaNegRisk:
    def test_cesta_abaixo_de_um_da_lucro_igual_a_folga(self):
        """Três resultados a 0,30 cada: a cesta custa 0,90 e paga 1,00."""
        livros = [_livro(asks=[(0.30, 100.0)], asset_id=f"t{i}") for i in range(3)]

        op, motivo = oportunidade_de_cesta(
            livros, [SEM_TAXA] * 3, shares=10.0, conjunto_exaustivo=True
        )

        assert motivo is None
        assert op.custo_usdc == pytest.approx(9.0)
        assert op.lucro_usdc == pytest.approx(1.0)
        assert op.lucro_por_share == pytest.approx(0.10)

    def test_cesta_acima_de_um_NAO_e_oportunidade(self):
        livros = [_livro(asks=[(0.35, 100.0)], asset_id=f"t{i}") for i in range(3)]

        op, motivo = oportunidade_de_cesta(
            livros, [SEM_TAXA] * 3, shares=10.0, conjunto_exaustivo=True
        )

        assert op is None and motivo == "sem_folga"

    def test_a_taxa_e_DESCONTADA_e_pode_matar_a_folga(self):
        """0,98 de custo com folga de 0,02 — e a fee em p=0,49 come mais que
        isso. Arbitragem que ignora a fee é arbitragem inventada."""
        livros = [_livro(asks=[(0.49, 100.0)], asset_id=f"t{i}") for i in range(2)]

        com, _ = oportunidade_de_cesta(
            livros, [SEM_TAXA] * 2, shares=10.0, conjunto_exaustivo=True
        )
        sem, motivo = oportunidade_de_cesta(
            livros, [TAXA] * 2, shares=10.0, conjunto_exaustivo=True
        )

        assert com is not None and com.lucro_usdc == pytest.approx(0.2)
        assert sem is None and motivo == "sem_folga"

    def test_atravessar_o_livro_encarece_e_o_lucro_por_share_CAI(self):
        """É por isso que o tamanho entra no resultado, e não só a existência."""
        livros = [
            _livro(asks=[(0.30, 5.0), (0.34, 100.0)], asset_id=f"t{i}")
            for i in range(3)
        ]

        pequena, _ = oportunidade_de_cesta(
            livros, [SEM_TAXA] * 3, shares=5.0, conjunto_exaustivo=True
        )
        grande, _ = oportunidade_de_cesta(
            livros, [SEM_TAXA] * 3, shares=10.0, conjunto_exaustivo=True
        )

        assert pequena.lucro_por_share > grande.lucro_por_share
        assert grande.niveis_atravessados > pequena.niveis_atravessados


class TestATravaQueImpedeViraraAposta:
    """Sem a exaustividade, a cesta não paga 1,00 e isso NÃO dá erro sozinho."""

    def test_conjunto_nao_exaustivo_RECUSA_mesmo_com_conta_lucrativa(self):
        livros = [_livro(asks=[(0.30, 100.0)], asset_id=f"t{i}") for i in range(3)]

        op, motivo = oportunidade_de_cesta(
            livros, [SEM_TAXA] * 3, shares=10.0, conjunto_exaustivo=False
        )

        assert op is None and motivo == "conjunto_nao_exaustivo"

    def test_perna_que_nao_enche_RECUSA_em_vez_de_encher_parcial(self):
        """Uma perna faltando desfaz a identidade: o que sobra é posição
        direcional, o oposto do que esta rota existe para ser."""
        livros = [
            _livro(asks=[(0.30, 100.0)], asset_id="t0"),
            _livro(asks=[(0.30, 2.0)], asset_id="t1"),
        ]

        op, motivo = oportunidade_de_cesta(
            livros, [SEM_TAXA] * 2, shares=10.0, conjunto_exaustivo=True
        )

        assert op is None and motivo == "livro_raso"

    def test_perna_sem_livro_RECUSA(self):
        livros = [_livro(asks=[(0.30, 100.0)], asset_id="t0"), _livro(asset_id="t1")]

        op, motivo = oportunidade_de_cesta(
            livros, [SEM_TAXA] * 2, shares=10.0, conjunto_exaustivo=True
        )

        assert op is None and motivo == "perna_sem_livro"


class TestAMaiorCesta:
    def test_devolve_o_MAXIMO_e_nao_o_primeiro_tamanho_que_da_lucro(self):
        """O lucro por share cai com o tamanho, mas o TOTAL sobe até o livro
        acabar. Reportar tamanho 1 diria 'existe' sobre algo que rende
        centavos."""
        livros = [
            _livro(asks=[(0.30, 20.0), (0.40, 100.0)], asset_id=f"t{i}")
            for i in range(3)
        ]

        op, motivo = maior_cesta(
            livros, [SEM_TAXA] * 3, conjunto_exaustivo=True, teto_de_shares=50.0
        )

        assert motivo is None
        # Aos 20 a cesta custa 0,90; passar disso puxa o nível de 0,40 e a
        # cesta passa de 1,00 — o máximo está na virada.
        assert op.shares == pytest.approx(20.0)
        assert op.lucro_usdc == pytest.approx(2.0)

    def test_sem_oportunidade_nenhuma_devolve_motivo(self):
        livros = [_livro(asks=[(0.50, 100.0)], asset_id=f"t{i}") for i in range(3)]

        op, motivo = maior_cesta(
            livros, [SEM_TAXA] * 3, conjunto_exaustivo=True, teto_de_shares=10.0
        )

        assert op is None and motivo == "sem_folga"


class TestAEscadaDeLimiares:
    """`P(X ≥ k₁) ≥ P(X ≥ k₂)`: quem passa de k₂ passou de k₁."""

    def test_comprar_o_limiar_BAIXO_e_vender_o_ALTO_trava_a_diferenca(self):
        baixo = _livro(asks=[(0.40, 100.0)], asset_id="k1")
        alto = _livro(bids=[(0.55, 100.0)], asset_id="k2")

        op, motivo = oportunidade_de_escada(
            baixo, alto, SEM_TAXA, SEM_TAXA,
            shares=10.0, token_baixo="k1", token_alto="k2",
            limiar_baixo=100_000.0, limiar_alto=110_000.0,
        )

        assert motivo is None
        assert op.lucro_usdc == pytest.approx(1.5)

    def test_escada_em_ordem_CORRETA_nao_e_oportunidade(self):
        """O caso normal: o limiar baixo vale MAIS que o alto."""
        baixo = _livro(asks=[(0.60, 100.0)], asset_id="k1")
        alto = _livro(bids=[(0.40, 100.0)], asset_id="k2")

        op, motivo = oportunidade_de_escada(
            baixo, alto, SEM_TAXA, SEM_TAXA,
            shares=10.0, token_baixo="k1", token_alto="k2",
            limiar_baixo=100_000.0, limiar_alto=110_000.0,
        )

        assert op is None and motivo == "sem_folga"

    def test_o_MESMO_token_dos_dois_lados_RECUSA(self):
        """Comprar e vender o mesmo token não é escada — é ruído de
        agrupamento, e daria 'lucro' do spread invertido."""
        livro = _livro(asks=[(0.40, 100.0)], bids=[(0.55, 100.0)], asset_id="k")

        op, motivo = oportunidade_de_escada(
            livro, livro, SEM_TAXA, SEM_TAXA,
            shares=10.0, token_baixo="k", token_alto="k",
            limiar_baixo=100_000.0, limiar_alto=110_000.0,
        )

        assert op is None and motivo == "mesma_perna"

    def test_limiares_TROCADOS_recusam_em_vez_de_inventar_lucro(self):
        """A conta devolveria "lucro travado" sobre posição que perde.

        Os livros não carregam o limiar deles: com os dois trocados a
        desigualdade se inverte e o que sobra é aposta em spread. Mesmo modo
        de falha do `conjunto_nao_exaustivo`.
        """
        baixo = _livro(asks=[(0.40, 100.0)], asset_id="k1")
        alto = _livro(bids=[(0.55, 100.0)], asset_id="k2")

        op, motivo = oportunidade_de_escada(
            baixo, alto, SEM_TAXA, SEM_TAXA,
            shares=10.0, token_baixo="k1", token_alto="k2",
            limiar_baixo=110_000.0, limiar_alto=100_000.0,
        )

        assert op is None and motivo == "escada_fora_de_ordem"

    def test_limiares_IGUAIS_tambem_recusam(self):
        """Mesmo limiar não é escada: não há desigualdade a explorar."""
        baixo = _livro(asks=[(0.40, 100.0)], asset_id="k1")
        alto = _livro(bids=[(0.55, 100.0)], asset_id="k2")

        op, motivo = oportunidade_de_escada(
            baixo, alto, SEM_TAXA, SEM_TAXA,
            shares=10.0, token_baixo="k1", token_alto="k2",
            limiar_baixo=100_000.0, limiar_alto=100_000.0,
        )

        assert op is None and motivo == "escada_fora_de_ordem"

    def test_sem_bids_no_limiar_alto_nao_ha_o_que_vender(self):
        baixo = _livro(asks=[(0.40, 100.0)], asset_id="k1")
        alto = _livro(asks=[(0.55, 100.0)], asset_id="k2")

        op, motivo = oportunidade_de_escada(
            baixo, alto, SEM_TAXA, SEM_TAXA,
            shares=10.0, token_baixo="k1", token_alto="k2",
            limiar_baixo=100_000.0, limiar_alto=110_000.0,
        )

        assert op is None and motivo == "perna_sem_livro"

    def test_venda_que_nao_enche_RECUSA(self):
        baixo = _livro(asks=[(0.40, 100.0)], asset_id="k1")
        alto = _livro(bids=[(0.55, 3.0)], asset_id="k2")

        op, motivo = oportunidade_de_escada(
            baixo, alto, SEM_TAXA, SEM_TAXA,
            shares=10.0, token_baixo="k1", token_alto="k2",
            limiar_baixo=100_000.0, limiar_alto=110_000.0,
        )

        assert op is None and motivo == "livro_raso"


def test_todo_motivo_declarado_e_ALCANCAVEL():
    """Mesmo guarda do leitor da rodada: motivo decorativo não vira métrica.

    Dois motivos declarados e nunca devolvidos foi defeito real neste projeto
    duas vezes (o `limite_pessimista`, e depois o `custo_nao_medido` e o
    `ordem_sem_prefixo_de_sombra` no leitor do 4.2).
    """
    from pathlib import Path

    fonte = (
        Path(__file__).resolve().parents[1]
        / "src" / "pulsearb" / "analysis" / "arbitragem.py"
    ).read_text(encoding="utf-8")
    corpo = fonte.split("MOTIVOS = {", 1)[1].split("\n}\n", 1)[1]

    nao_usados = [nome for nome in MOTIVOS if f'"{nome}"' not in corpo]

    assert not nao_usados, f"motivos declarados e nunca devolvidos: {nao_usados}"
