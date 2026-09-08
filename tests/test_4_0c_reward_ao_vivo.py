"""4.0(c) — os parâmetros de reward chegando ao caminho AO VIVO.

Sem eles `escolher_cotacao` não roda, e o laço maker não tem o que calcular.
O recorder já os lia para o backtest; o caminho ao vivo nunca leu.

O teste que dá nome ao arquivo é o do MESMO CAMINHO: recorder e ao vivo têm de
ler pelas mesmas funções. A leitura é ambígua de propósito (três nomes para a
lista, seis para a taxa), e duas cópias divergiriam na primeira grafia nova —
com a divergência aparecendo como "o SHADOW achou pool onde o backtest não
achou", que se investiga no mercado antes de se investigar no código.
"""

from __future__ import annotations

from pulsearb.markets.rewards_da_gamma import (
    forma_dos_rewards,
    parametros_de_reward,
    taxa_diaria_de_reward,
)


def _gamma(taxa=12.5, min_size=50, max_spread=3.0, chave="clobRewards"):
    return {
        chave: [{"rewardsDailyRate": taxa}],
        "rewardsMinSize": min_size,
        "rewardsMaxSpread": max_spread,
    }


class TestLeituraCompartilhada:
    def test_o_recorder_usa_o_MODULO_e_nao_uma_copia(self):
        """O teste que dá nome ao arquivo: se o recorder voltasse a ter cópia
        própria, os dois lados poderiam divergir em silêncio."""
        import pulsearb.recorder.__main__ as rec

        assert rec.taxa_diaria_de_reward is taxa_diaria_de_reward
        assert rec.forma_dos_rewards is forma_dos_rewards

    def test_aceita_as_tres_grafias_da_lista(self):
        """Apostar numa chave só custou o marco inteiro no `price_change`."""
        for chave in ("clobRewards", "rewards_config", "rewardsConfig"):
            assert taxa_diaria_de_reward(_gamma(chave=chave)) == 12.5

    def test_soma_as_fontes_de_reward(self):
        """Nativa + patrocinada = `total_daily_rate`."""
        gamma = {"clobRewards": [{"dailyRate": 10.0}, {"dailyRate": 5.0}]}

        assert taxa_diaria_de_reward(gamma) == 15.0

    def test_sem_taxa_devolve_None_e_nao_zero(self):
        """Zero é taxa legítima ('pool de zero'); confundir apagaria a
        diferença entre mercado sem programa e programa vazio."""
        assert taxa_diaria_de_reward({"rewardsMinSize": 50}) is None


class TestParametrosParaOAoVivo:
    def test_devolve_os_tres_com_o_spread_em_FRACAO(self):
        """A Gamma manda o spread em centavos; a conta usa fração. A conversão
        mora no leitor para os dois lados não divergirem sobre a unidade."""
        lidos = parametros_de_reward(_gamma(taxa=12.5, min_size=50, max_spread=3.0))

        assert lidos is not None
        taxa, min_size, max_spread = lidos
        assert (taxa, min_size) == (12.5, 50.0)
        assert max_spread == 0.03  # 3 centavos -> fração

    def test_sem_pool_devolve_None_e_nao_um_default(self):
        """Inventar taxa produziria receita onde não há nenhuma."""
        assert parametros_de_reward({"rewardsMaxSpread": 3.0}) is None

    def test_taxa_zero_tambem_e_None(self):
        """Pool de zero não financia cotação nenhuma."""
        assert parametros_de_reward(_gamma(taxa=0.0)) is None

    def test_sem_max_spread_devolve_None(self):
        """Ter taxa sem spread máximo não permite calcular reward nenhum."""
        gamma = {"clobRewards": [{"rewardsDailyRate": 12.5}]}

        assert parametros_de_reward(gamma) is None


class TestAJanelaAoVivo:
    def test_a_janela_carrega_os_tres_campos(self):
        from pulsearb.live.rastreador import JanelaAoVivo

        campos = JanelaAoVivo.__dataclass_fields__
        assert "reward_daily_rate" in campos
        assert "reward_min_size" in campos
        assert "reward_max_spread" in campos

    def test_os_tres_andam_juntos_ou_nenhum(self):
        """Deixar um preenchido e outro vazio convidaria quem lê a achar que
        dá para usar metade."""
        from pulsearb.live.rastreador import _campos_de_reward

        cheios = _campos_de_reward(_gamma())
        assert all(v is not None for v in cheios.values())

        vazios = _campos_de_reward({"rewardsMinSize": 50})
        assert all(v is None for v in vazios.values())
