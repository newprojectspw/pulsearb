"""4.1 / 1.6 — o limite inferior do líquido maker, que dispensa a fila.

O 1.6 está travado em três termos que dependem de posição na fila, e a fila
não é observável no WS agregado. Este arquivo cobre a saída: em vez de estimar
o inobservável com um fator, **limitar por baixo** e perguntar se fecha mesmo
no pior caso.

O teste que dá nome ao arquivo é o da falha fechada: sem o dado de execução, a
conta NÃO pode devolver um líquido. Um zero em `shares` daria custo zero e um
"positivo" que seria só a ausência de medida.
"""

from __future__ import annotations

from pulsearb.analysis.measurements import conta_pessimista_do_maker


def _rewards(receita=10.0, horas=8.0, recorte="btc | 5m"):
    return {
        "por_ordem": {
            "ordem_A": {recorte: {"receita_usdc": receita, "horas_de_amostra": horas}}
        }
    }


def _markout(media=-0.1974, minimo=-1.5, recorte="btc | 5m", horizonte="5s", n=42):
    return {
        "markout_centavos_por_share": {
            recorte: {horizonte: {"media": media, "min": minimo, "n": n}}
        }
    }


class TestFalhaFechada:
    def test_sem_o_dado_de_execucao_NAO_devolve_liquido(self):
        """O teste que dá nome ao arquivo.

        Sem `shares_executadas_por_recorte` a conta não é avaliável. Devolver
        um número aqui seria transformar ausência de medida em resultado — o
        mesmo defeito do `cobertura_da_gravacao`, que dava nota máxima ao caso
        em que a medição não existia.
        """
        conta = conta_pessimista_do_maker(rewards=_rewards(), markout=_markout())

        assert conta["avaliavel"] is False
        assert conta["liquido_no_pior_caso_usdc"] is None
        assert conta["fecha_no_pior_caso"] is None
        assert conta["o_que_falta_para_avaliar"]

    def test_o_que_falta_NOMEIA_o_dado_e_de_onde_ele_sai(self):
        """Regra 3: o que falta vai escrito, não é 'parcial'."""
        conta = conta_pessimista_do_maker(rewards=_rewards(), markout=_markout())

        (falta,) = conta["o_que_falta_para_avaliar"]
        assert "shares_executadas_por_recorte" in falta
        assert "varredura" in falta or "varridas" in falta


class TestOLimiteInferior:
    def test_rewards_entram_INTEIROS_porque_nao_dependem_da_fila(self):
        """§15.3: o score é por spread e tamanho, amostrado 1×/min — ganha-se
        por ESTAR no livro. Nenhum haircut de fila se aplica a eles."""
        conta = conta_pessimista_do_maker(
            rewards=_rewards(receita=10.0),
            markout=_markout(),
            shares_executadas_por_recorte={"btc | 5m": 0.0},
        )

        assert conta["total_rewards_usdc"] == 10.0
        # Sem execução não há markout: o líquido no pior caso é o reward inteiro.
        assert conta["liquido_no_pior_caso_usdc"] == 10.0

    def test_o_custo_usa_a_estatistica_ADVERSA_nao_a_media(self):
        """Fim da fila executa só quando o nível é varrido — as ocasiões de
        markout pior. Usar a média subestimaria o custo do pior caso."""
        conta = conta_pessimista_do_maker(
            rewards=_rewards(receita=10.0),
            markout=_markout(media=-0.2, minimo=-2.0),
            shares_executadas_por_recorte={"btc | 5m": 100.0},
        )

        # pior: |−2,0| cent × 100 shares / 100 = 2,00 USDC
        assert conta["total_custo_no_pior_caso_usdc"] == 2.0
        assert conta["liquido_no_pior_caso_usdc"] == 8.0
        # média: |−0,2| × 100 / 100 = 0,20 USDC → líquido 9,80
        assert conta["liquido_no_markout_medio_usdc"] == 9.8

    def test_o_pior_caso_e_SEMPRE_pior_ou_igual_ao_markout_medio(self):
        """A propriedade que faz dele um limite: nunca otimista."""
        conta = conta_pessimista_do_maker(
            rewards=_rewards(receita=10.0),
            markout=_markout(media=-0.2, minimo=-2.0),
            shares_executadas_por_recorte={"btc | 5m": 100.0},
        )

        assert (
            conta["liquido_no_pior_caso_usdc"]
            <= conta["liquido_no_markout_medio_usdc"]
        )

    def test_markout_negativo_vira_CUSTO_e_nao_receita(self):
        """Somar o markout com o sinal que ele tem transformaria perda em
        ganho. O `abs` existe por isso."""
        conta = conta_pessimista_do_maker(
            rewards=_rewards(receita=1.0),
            markout=_markout(media=-0.5, minimo=-0.5),
            shares_executadas_por_recorte={"btc | 5m": 100.0},
        )

        # |−0,5| × 100 / 100 = 0,50 de CUSTO → líquido 0,50, não 1,50
        assert conta["liquido_no_pior_caso_usdc"] == 0.5


class TestFechaNoPiorCaso:
    def test_positivo_no_pior_caso_fecha(self):
        """A resposta que o 4.1 pede: positivo aqui é positivo SEM nenhuma
        hipótese de fila."""
        conta = conta_pessimista_do_maker(
            rewards=_rewards(receita=10.0),
            markout=_markout(minimo=-1.0),
            shares_executadas_por_recorte={"btc | 5m": 100.0},
        )

        assert conta["liquido_no_pior_caso_usdc"] == 9.0
        assert conta["fecha_no_pior_caso"] is True

    def test_negativo_no_pior_caso_NAO_fecha(self):
        """E é achado, não fracasso: quer dizer que a lucratividade depende de
        posição na fila, que é justamente o que não se mede."""
        conta = conta_pessimista_do_maker(
            rewards=_rewards(receita=1.0),
            markout=_markout(minimo=-5.0),
            shares_executadas_por_recorte={"btc | 5m": 100.0},
        )

        assert conta["liquido_no_pior_caso_usdc"] < 0
        assert conta["fecha_no_pior_caso"] is False

    def test_o_rebate_fica_de_fora_e_o_texto_diz_por_que(self):
        """Omitir termo POSITIVO preserva o limite inferior. Incluí-lo exigiria
        o volume desconhecido e quebraria a garantia."""
        conta = conta_pessimista_do_maker(
            rewards=_rewards(),
            markout=_markout(),
            shares_executadas_por_recorte={"btc | 5m": 10.0},
        )

        assert "rebate" not in str(conta["por_ordem_e_recorte"]).lower()
        assert "OMITIDO" in conta["formula"]
        # A propriedade do limite, dita em texto: o real só pode ser melhor.
        assert "so pode ser melhor" in conta["por_que_e_um_limite_inferior"]


class TestRewardZeroDecideSozinho:
    """Quando rewards é zero, o dado que falta deixa de ser necessário.

    O limite é `líquido = rewards − custo`, e o custo nunca é negativo: sai de
    `|markout| × shares`, com os dois fatores ≥ 0. Então rewards = 0 implica
    líquido ≤ 0 qualquer que seja o número de shares varridas — nenhum valor
    dele muda o SINAL.

    Isto não afrouxa a falha fechada: ela existe para não inventar número
    ausente, e aqui nada é inventado. Continuar devolvendo `avaliavel: false`
    esconderia um veredito que a medida já sustenta.
    """

    def test_reward_zero_decide_MESMO_sem_o_dado_de_execucao(self):
        conta = conta_pessimista_do_maker(
            rewards=_rewards(receita=0.0),
            markout=_markout(),
            # sem `shares_executadas_por_recorte`
        )

        assert conta["avaliavel"] is True
        assert conta["decidido_por_reward_zero"] is True
        assert conta["fecha_no_pior_caso"] is False

    def test_reward_POSITIVO_sem_o_dado_continua_NAO_avaliavel(self):
        """O ramo novo vale só quando o sinal já está determinado. Com receita
        positiva, o custo decide — e o custo depende do dado que falta."""
        conta = conta_pessimista_do_maker(
            rewards=_rewards(receita=10.0), markout=_markout()
        )

        assert conta["avaliavel"] is False
        assert conta["decidido_por_reward_zero"] is False
        assert conta["fecha_no_pior_caso"] is None

    def test_SEM_RECORTE_NENHUM_nao_decide_nada(self):
        """A distinção que impede o defeito do `cobertura_da_gravacao`: sem
        nenhum recorte o total também é 0,0, mas aí o zero é AUSÊNCIA de
        medida, não medida de ausência."""
        conta = conta_pessimista_do_maker(rewards={"por_ordem": {}}, markout={})

        assert conta["avaliavel"] is False
        assert conta["decidido_por_reward_zero"] is False
        assert conta["fecha_no_pior_caso"] is None

    def test_com_o_dado_presente_o_ramo_novo_nao_interfere(self):
        """Tendo shares, a conta normal roda e o ramo especial fica de fora."""
        conta = conta_pessimista_do_maker(
            rewards=_rewards(receita=10.0),
            markout=_markout(minimo=-1.0),
            shares_executadas_por_recorte={"btc | 5m": 100.0},
        )

        assert conta["decidido_por_reward_zero"] is False
        assert conta["fecha_no_pior_caso"] is True
