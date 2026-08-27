"""O resumo que decide o veredito — e o campo errado que ele lia.

O `resumo_m2.py` existe porque alguém já leu o campo errado do relatório e
saiu com um diagnóstico invertido. Ele mesmo caiu nessa: imprimia `erro` no
critério 1.3, que é exatamente o campo que o relatório manda NÃO ler.

Cada teste aqui trava uma leitura. Nenhum é decorativo.
"""

from __future__ import annotations

import importlib.util
import typing
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "resumo_m2", RAIZ / "scripts" / "resumo_m2.py"
)
resumo_m2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(resumo_m2)

NAO_AVALIAVEL = resumo_m2.NAO_AVALIAVEL
PASSA = resumo_m2.PASSA
REPROVA = resumo_m2.REPROVA


def _por_numero(criterios):
    return {c.numero: c for c in criterios}


def _relatorio(**ajustes):
    base = {
        "backtest": {
            "resumo": {"pnl_liquido_usdc": 10.0, "trades": 695},
            "calibracao": {
                "240-120s": {
                    "erro": 0.9,
                    "erro_de_confiabilidade": 0.01,
                    "faixas_ocupadas": 4,
                    "calibracao_avaliavel": True,
                }
            },
        },
        "sensibilidade_latencia": {"600ms": {"pnl_liquido_usdc": 9.0}},
        "medicoes": {
            "profundidade": {
                "criterio_do_veredito": {
                    "por_duracao": {"300s": {"p50_3ticks_usdc": 250.0}}
                }
            }
        },
        "integridade": {"divergencia_topo_book": {"taxa": 0.005}},
        "rota_maker": {
            "markout": {
                "markout_centavos_por_share": {"total": {"5s": {"media": -0.2}}}
            },
            "conta_fechada": {
                "por_ordem_e_recorte": {"celula": {"horas_de_amostra": 65.9}},
                "o_que_falta_para_fechar": [],
            },
            "sensibilidade_ao_fator": {"ordem": {"0.3": 1.5}},
        },
    }
    return base | ajustes


class TestOCampoCerto:
    """O defeito real: 1.3 lia `erro`, e `erro` não mede calibração."""

    def test_le_erro_de_confiabilidade_e_nao_erro(self):
        # `erro` 0,9 reprovaria; `erro_de_confiabilidade` 0,01 passa. Se o
        # veredito seguir o 0,9, a leitura voltou para o campo errado.
        criterio = _por_numero(resumo_m2.criterios_do_taker(_relatorio()))["1.3"]

        assert criterio.veredito == PASSA
        assert "0.01" in criterio.medido

    def test_balde_nao_avaliavel_nao_conta_mesmo_com_erro_lindo(self):
        """A armadilha do preditor constante, medida no M2.13.

        Uma faixa só, ECE de 0,0051, e o critério "passa" sem que o modelo
        saiba nada. Com `calibracao_avaliavel` false o balde não entra.
        """
        relatorio = _relatorio()
        relatorio["backtest"]["calibracao"] = {
            "<30s": {
                "erro": -0.0015,
                "erro_de_confiabilidade": 0.0051,
                "faixas_ocupadas": 1,
                "calibracao_avaliavel": False,
            }
        }
        criterio = _por_numero(resumo_m2.criterios_do_taker(relatorio))["1.3"]

        assert criterio.veredito == NAO_AVALIAVEL
        assert criterio.veredito != REPROVA
        assert "faixas_ocupadas" in criterio.medido

    def test_o_campo_lido_sai_impresso(self):
        # É o que permite conferir a leitura sem abrir o JSON — a defesa
        # contra este bug se repetir num campo diferente.
        for criterio in resumo_m2.criterios_do_taker(_relatorio()):
            assert criterio.campo


class TestNaoAvaliavelNaoEReprova:
    """Confundir os dois manda para o trabalho errado."""

    def test_julgar_none(self):
        assert resumo_m2._julgar(None) == NAO_AVALIAVEL

    def test_conta_do_maker_aberta_nao_reprova(self):
        """`o_que_falta_para_fechar` é não-vazia em TODO relatório de hoje.

        `resultado_parcial_usdc` soma rewards e rebate e não subtrai markout:
        lê-lo como conta fechada foi o erro do primeiro veredito.
        """
        relatorio = _relatorio()
        relatorio["rota_maker"]["conta_fechada"]["o_que_falta_para_fechar"] = [
            "volume_taker_usdc: exige simular a fila",
            "custo_de_markout em USDC: depende do mesmo numero",
        ]
        criterio = _por_numero(resumo_m2.criterios_do_maker(relatorio))["1.6"]

        assert criterio.veredito == NAO_AVALIAVEL
        assert "volume_taker_usdc" in criterio.medido

    @pytest.mark.parametrize("numero", ["1.1", "1.2", "1.3", "1.4", "1.5"])
    def test_relatorio_vazio_nao_derruba_nem_reprova(self, numero):
        criterio = _por_numero(resumo_m2.criterios_do_taker({}))[numero]
        assert criterio.veredito == NAO_AVALIAVEL


class TestOsLimiares:
    def test_pnl_negativo_reprova(self):
        relatorio = _relatorio()
        relatorio["backtest"]["resumo"]["pnl_liquido_usdc"] = -53.2777
        assert _por_numero(resumo_m2.criterios_do_taker(relatorio))["1.1"].veredito == (
            REPROVA
        )

    def test_profundidade_usa_a_melhor_duracao(self):
        # Uma duração acima do limiar basta: o critério é sobre existir
        # capacidade em algum recorte, não em todos.
        relatorio = _relatorio()
        relatorio["medicoes"]["profundidade"]["criterio_do_veredito"][
            "por_duracao"
        ] = {"300s": {"p50_3ticks_usdc": 250.0}, "3600s": {"p50_3ticks_usdc": 28.7}}
        criterio = _por_numero(resumo_m2.criterios_do_taker(relatorio))["1.5"]

        assert criterio.veredito == PASSA
        # 300s antes de 3600s: ordem alfabetica poria 14400s na frente.
        assert criterio.medido.index("300s") < criterio.medido.index("3600s")

    def test_divergencia_sem_decomposicao_julga_o_agregado(self):
        # Relatorio antigo: so a taxa existe, e o veredito e sobre ela —
        # com o aviso de que mistura populacoes.
        relatorio = _relatorio()
        relatorio["integridade"]["divergencia_topo_book"]["taxa"] = 0.028168
        criterio = _por_numero(resumo_m2.criterios_do_maker(relatorio))["1.9"]

        assert criterio.veredito == REPROVA
        assert "2.82%" in criterio.medido
        assert "sem decomposicao" in criterio.medido

    def test_formula_de_reward_reprova_enquanto_for_hipotese(self):
        # Não é medição: é fato sobre a documentação. Fica REPROVA de
        # propósito — número bem formatado saído de fórmula não confirmada é
        # exatamente o erro que o critério 1.10 existe para impedir.
        criterio = _por_numero(resumo_m2.criterios_do_maker(_relatorio()))["1.10"]
        assert criterio.veredito == REPROVA


class TestOMarkoutRepresentativo:
    """A armadilha de comparações múltiplas, pega rodando de verdade.

    A primeira versão pegava o melhor número da tabela de markout. Sobre o
    relatório real ela escolheu `hora_utc=01` com **+0,88 centavo** — markout
    positivo, ou seja, lucro de adverse selection, que não existe. Era uma
    célula pequena entre duas dezenas.
    """

    def _com_tabela(self, tabela):
        relatorio = _relatorio()
        relatorio["rota_maker"]["markout"]["markout_centavos_por_share"] = tabela
        return _por_numero(resumo_m2.criterios_do_maker(relatorio))["1.7"]

    def test_total_ganha_da_celula_mais_favoravel(self):
        criterio = self._com_tabela(
            {
                "total": {"5s": {"media": -0.1974, "n": 246504}},
                "hora_utc=01": {"5s": {"media": 0.8801, "n": 312}},
            }
        )
        assert "-0.1974" in criterio.medido
        assert "246504" in criterio.medido
        assert "hora_utc=01" not in criterio.medido

    def test_sem_total_vence_a_MAIOR_AMOSTRA_e_nao_a_melhor(self):
        criterio = self._com_tabela(
            {
                "hora_utc=01": {"5s": {"media": 0.88, "n": 312}},
                "300s": {"5s": {"media": -0.40, "n": 80000}},
            }
        )
        assert "-0.4" in criterio.medido
        assert "300s" in criterio.medido

    def test_o_n_sai_impresso(self):
        # É o que permite desconfiar da célula sem abrir o JSON.
        criterio = self._com_tabela({"total": {"5s": {"media": -0.2, "n": 5}}})
        assert "5 execucoes" in criterio.medido


class TestLeituraDoVies:
    """"Não calibrado" não diz o que consertar. A ordem do erro diz.

    Erro que cresce com a probabilidade prevista é excesso de confiança, e
    tem conserto de uma linha: encolher a previsão em direção à taxa-base.
    Erro sem ordem não tem — qualquer encolhimento que acerte uma faixa
    piora outra.
    """

    def test_erro_crescente_aponta_o_conserto(self):
        # O caso do dia 24: -0,0105 a +0,1554, subindo com a confianca.
        curva = {
            "0.45-0.50": {"n": 120, "previsto": 0.4812, "erro": -0.0105},
            "0.50-0.55": {"n": 340, "previsto": 0.5231, "erro": 0.0148},
            "0.70-0.75": {"n": 55, "previsto": 0.7190, "erro": 0.1554},
        }
        leitura = resumo_m2.leitura_do_vies(curva)

        assert "OTIMISTA CRESCENTE" in leitura
        assert "taxa-base" in leitura

    def test_misto_sem_ordem_nao_finge_ter_conserto(self):
        curva = {
            "0.45-0.50": {"n": 100, "previsto": 0.48, "erro": 0.09},
            "0.50-0.55": {"n": 100, "previsto": 0.52, "erro": -0.07},
            "0.70-0.75": {"n": 100, "previsto": 0.71, "erro": 0.11},
        }
        leitura = resumo_m2.leitura_do_vies(curva)

        assert "SEM ORDEM" in leitura
        assert "taxa-base" not in leitura

    def test_tres_otimistas_e_uma_pessimista_EM_ORDEM_nao_viram_misto(self):
        """A leitura que a primeira versão errava.

        Contar sinais diria "MISTO", escondendo o caso mais comum e mais
        tratável: o erro monótono que passa pelo zero.
        """
        curva = {
            "a": {"n": 10, "previsto": 0.45, "erro": -0.01},
            "b": {"n": 10, "previsto": 0.55, "erro": 0.02},
            "c": {"n": 10, "previsto": 0.65, "erro": 0.05},
            "d": {"n": 10, "previsto": 0.75, "erro": 0.09},
        }
        assert "CRESCENTE" in resumo_m2.leitura_do_vies(curva)

    def test_faixa_sem_amostra_nao_entra(self):
        curva = {
            "vazia": {"n": 0, "previsto": 0.9, "erro": -9.0},
            "cheia": {"n": 50, "previsto": 0.55, "erro": 0.03},
        }
        assert "OTIMISTA nas 1 faixa" in resumo_m2.leitura_do_vies(curva)

    def test_curva_vazia_nao_derruba(self):
        assert resumo_m2.leitura_do_vies({}) == "sem faixa com amostra"

    def test_diagnostica_o_MESMO_balde_que_o_criterio_escolheu(self):
        # Diagnosticar outro balde explicaria um numero que ninguem leu.
        relatorio = _relatorio()
        relatorio["backtest"]["calibracao"] = {
            "ruim": {
                "erro_de_confiabilidade": 0.20,
                "faixas_ocupadas": 5,
                "calibracao_avaliavel": True,
            },
            "melhor": {
                "erro_de_confiabilidade": 0.01,
                "faixas_ocupadas": 4,
                "calibracao_avaliavel": True,
            },
        }
        criterio = _por_numero(resumo_m2.criterios_do_taker(relatorio))["1.3"]
        balde, _ = resumo_m2._balde_do_diagnostico(relatorio["backtest"])

        assert balde == "melhor"
        assert balde in criterio.medido


class TestDivergenciaDecomposta:
    """O 1.9 julgado sobre a população que o §2c diz poder invalidar.

    No dia 24: taxa agregada 2,82%, mas 92,8% dela é `lado_vazio`, que o
    bloco `quais_invalidam` — registrado antes dos números — marca como
    não-invalidante. O topo DESLOCADO, a corrupção que o critério teme, é
    0,20%.
    """

    def _com_divergencia(self, **campos):
        relatorio = _relatorio()
        relatorio["integridade"]["divergencia_topo_book"] = campos
        return _por_numero(resumo_m2.criterios_do_maker(relatorio))["1.9"]

    QUAIS_INVALIDAM: typing.ClassVar[dict[str, bool]] = {
        "vazio_desde_o_snapshot": False,
        "esvaziado_por_delta": False,
        "sem_snapshot": True,
        "apos_perda": True,
    }

    def test_o_dia_24_passa_na_populacao_que_invalida(self):
        criterio = self._com_divergencia(
            taxa=0.028168,
            comparacoes=372_162_674,
            com_magnitude_finita=759_621,
            com_lado_vazio=9_723_487,
            lado_vazio={
                "por_causa": {
                    "vazio_desde_o_snapshot": 6_000_000,
                    "esvaziado_por_delta": 3_723_487,
                },
                "quais_invalidam": self.QUAIS_INVALIDAM,
            },
        )

        assert criterio.veredito == PASSA
        assert "0.20%" in criterio.medido
        # A agregada nao some: fica impressa ao lado para ninguem achar
        # que o numero foi varrido para baixo do tapete.
        assert "2.82%" in criterio.medido

    def test_causa_invalidante_ou_nao_classificada_conta_contra(self):
        """A salvaguarda da emenda, no código e não só no papel.

        `sem_snapshot` é livro furado de verdade (o §2c marca True) e uma
        causa desconhecida não foi classificada — nenhuma das duas pode
        pegar carona no desconto do lado vazio. Achado em review: sem
        isto, a decomposição inocentava o lado vazio POR CATEGORIA, não
        por classificação.
        """
        criterio = self._com_divergencia(
            taxa=0.03,
            comparacoes=1_000_000,
            com_magnitude_finita=2_000,
            com_lado_vazio=28_000,
            lado_vazio={
                "por_causa": {
                    "vazio_desde_o_snapshot": 10_000,
                    "sem_snapshot": 12_000,
                    "causa_nova_sem_classificacao": 6_000,
                },
                "quais_invalidam": self.QUAIS_INVALIDAM,
            },
        )
        # julgada = (2_000 deslocadas + 18_000 nao inocentadas) / 1M = 2%
        assert criterio.veredito == REPROVA
        assert "nao inocentado" in criterio.medido

    def test_lado_vazio_sem_classificacao_conta_todo_contra(self):
        # Relatorio com decomposicao mas sem o bloco `lado_vazio`: nada
        # foi inocentado por nome, entao tudo conta contra ate classificar.
        criterio = self._com_divergencia(
            taxa=0.028168,
            comparacoes=372_162_674,
            com_magnitude_finita=759_621,
            com_lado_vazio=9_723_487,
        )
        assert criterio.veredito == REPROVA

    def test_topo_deslocado_acima_de_1pct_reprova_mesmo_decomposto(self):
        criterio = self._com_divergencia(
            taxa=0.05, comparacoes=1_000_000, com_magnitude_finita=15_000,
            com_lado_vazio=35_000,
        )
        assert criterio.veredito == REPROVA

    def test_decomposicao_sem_comparacoes_cai_no_agregado(self):
        criterio = self._com_divergencia(taxa=0.005, com_magnitude_finita=10)
        assert criterio.veredito == PASSA
        assert "sem decomposicao" in criterio.medido


class TestVarreduraDeEncolhimento:
    """A pergunta que decide o M3: o erro de calibração é de ESCALA?

    Se for, um fator < 1 leva o ECE abaixo do limiar e o 1.1 merece
    remedição. Se não for, o preditor precisa mudar.
    """

    def test_excesso_de_confianca_monotono_responde_ao_fator(self):
        # O formato real dos baldes: massa nos extremos, superconfiante.
        # base = 0.5, extremos realizados em 0.11/0.89 -> fator ~0.78.
        curva = {
            "0.00-0.05": {"n": 10_000, "previsto": 0.001, "realizado": 0.11},
            "0.45-0.50": {"n": 300, "previsto": 0.475, "realizado": 0.48},
            "0.95-1.00": {"n": 10_000, "previsto": 0.999, "realizado": 0.89},
        }
        sem, fator, com = resumo_m2.varredura_de_encolhimento(curva)

        assert sem > 0.05
        assert com < 0.02
        assert 0.70 < fator < 0.85

    def test_preditor_constante_nao_e_salvo_pelo_encolhimento(self):
        """A armadilha que a primeira versão deixava passar.

        Encolher uma constante em direção à taxa-base produz a taxa-base,
        que "acerta" por definição — a varredura RESGATARIA o preditor que
        nada sabe. Com menos faixas que o mínimo de avaliabilidade ela
        devolve `None`: sem estrutura, não há número.
        """
        curva = {"0.50-0.55": {"n": 5_000, "previsto": 0.514, "realizado": 0.62}}
        assert resumo_m2.varredura_de_encolhimento(curva) is None

    def test_erro_sem_ordem_nao_passa(self):
        # Vies misto: faixas erradas para os dois lados sem relacao com a
        # confianca. O encolhimento ajuda pouco e nao cruza o limiar.
        curva = {
            "a": {"n": 1000, "previsto": 0.2, "realizado": 0.65},
            "b": {"n": 1000, "previsto": 0.5, "realizado": 0.2},
            "c": {"n": 1000, "previsto": 0.8, "realizado": 0.6},
        }
        _sem, _fator, com = resumo_m2.varredura_de_encolhimento(curva)
        assert com > 0.05

    def test_curva_vazia_devolve_none(self):
        assert resumo_m2.varredura_de_encolhimento({}) is None

    def test_fator_agressivo_abaixo_de_030_e_encontrado(self):
        """Achado em review: a busca parava em 0,30.

        Ordem certa com escala absurda pede fator ~0,05; a varredura
        antiga imprimia "continua reprovando" para um modelo que um fator
        menor salvaria — desencorajando exatamente o experimento que o
        bloco existe para motivar.
        """
        curva = {
            "a": {"n": 1000, "previsto": 0.1, "realizado": 0.48},
            "b": {"n": 1000, "previsto": 0.5, "realizado": 0.50},
            "c": {"n": 1000, "previsto": 0.9, "realizado": 0.52},
        }
        _sem, fator, com = resumo_m2.varredura_de_encolhimento(curva)
        assert fator < 0.30
        assert com < 0.05

    def test_a_base_e_a_do_backtest_e_nao_a_taxa_realizada(self):
        """Achado em review: a base era a média realizada da própria curva.

        Duas doenças numa: o fator recomendado alimentava
        `--fator-de-encolhimento`, que encolhe para 0,5 — otimizava-se uma
        transformação que o backtest nunca aplica; e puxar para a média
        que o próprio período realizou é resgate in-sample — TODA curva
        "calibra" contra a sua própria média. Uma curva com realizado
        ~0,70 em todas as faixas não pode sair daqui com ECE ~0.
        """
        assert resumo_m2.BASE_DO_ENCOLHIMENTO == pytest.approx(0.5)

        curva = {
            "a": {"n": 1000, "previsto": 0.2, "realizado": 0.70},
            "b": {"n": 1000, "previsto": 0.5, "realizado": 0.70},
            "c": {"n": 1000, "previsto": 0.8, "realizado": 0.70},
        }
        _sem, _fator, com = resumo_m2.varredura_de_encolhimento(curva)
        # Com a base do backtest (0,5) nenhum fator alcanca 0,70 em todas
        # as faixas; com a base antiga (media = 0,70) o ECE despencava
        # para ~0 — a aprovacao fabricada que este teste proibe.
        assert com > 0.05


class TestVereditoDoEncolhimento:
    """O texto do diagnóstico não pode prometer o que a remedição nega.

    Achado em review: fator agressivo comprime previsões de 3+ faixas em
    1-2, o backtest ponto a ponto marca `calibracao_avaliavel` falso, e o
    1.3 vira NÃO AVALIÁVEL — um "PASSARIA" baseado só no ECE aproximado
    seria promessa falsa.
    """

    CURVA_AGRESSIVA: typing.ClassVar[dict] = {
        "a": {"n": 1000, "previsto": 0.1, "realizado": 0.48},
        "b": {"n": 1000, "previsto": 0.5, "realizado": 0.50},
        "c": {"n": 1000, "previsto": 0.9, "realizado": 0.52},
    }

    def test_fator_agressivo_comprime_as_faixas(self):
        # 0.1/0.5/0.9 com fator 0.05 -> 0.48/0.50/0.52: duas faixas de 0.05.
        assert (
            resumo_m2.faixas_ocupadas_apos_encolher(self.CURVA_AGRESSIVA, 0.05) < 3
        )
        # Sem encolher, as tres previsoes ocupam tres faixas distintas.
        assert resumo_m2.faixas_ocupadas_apos_encolher(self.CURVA_AGRESSIVA, 1.0) == 3

    def test_ece_bom_com_faixas_comprimidas_nao_promete_passa(self):
        _sem, fator, com = resumo_m2.varredura_de_encolhimento(
            self.CURVA_AGRESSIVA
        )
        assert com < 0.05  # o ECE aproximado passaria...
        veredito = resumo_m2.veredito_do_encolhimento(
            self.CURVA_AGRESSIVA, fator, com
        )
        # ...mas o texto aponta o RISCO de nao-avaliabilidade sem cravar o
        # veredito do backtest: a media por faixa erra para os dois lados,
        # e so as previsoes cruas decidem (achados em review, rodadas 5-6).
        assert "RISCO" in veredito
        assert "NAO AVALIAVEL" in veredito
        assert "PASSARIA" not in veredito

    def test_fator_moderado_aponta_sem_prometer(self):
        """Nem no melhor caso o texto promete PASSA (achado em review).

        A aproximação agrupa cada faixa pela média — uma faixa de 0,05
        que cavalga a fronteira pós-encolhimento esconde a divisão real.
        Só o backtest, com as previsões cruas, reagrupa; o veredito da
        aproximação é sempre condicionado a ele.
        """
        curva = {
            "0.00-0.05": {"n": 10_000, "previsto": 0.001, "realizado": 0.11},
            "0.45-0.50": {"n": 300, "previsto": 0.475, "realizado": 0.48},
            "0.95-1.00": {"n": 10_000, "previsto": 0.999, "realizado": 0.89},
        }
        _sem, fator, com = resumo_m2.varredura_de_encolhimento(curva)
        veredito = resumo_m2.veredito_do_encolhimento(curva, fator, com)
        assert "NA APROXIMACAO" in veredito
        assert "PASSARIA" not in veredito

    def test_ece_ruim_continua_reprovando_sem_olhar_faixas(self):
        veredito = resumo_m2.veredito_do_encolhimento(self.CURVA_AGRESSIVA, 1.0, 0.20)
        assert "reprovando" in veredito
