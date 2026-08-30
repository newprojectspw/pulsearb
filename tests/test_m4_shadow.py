"""M4.3 — o modo SHADOW: decide tudo, envia nada, registra o que teria feito.

O SHADOW só vale alguma coisa se passar pelo MESMO caminho do LIVE. Estes
testes travam as duas metades disso: que ele passa (os portões de risco rodam
de verdade, e a exposição é contabilizada) e que ele não envia (não existe
código de envio aqui, e pedir LIVE falha alto em vez de cair para shadow).
"""

from __future__ import annotations

import json

import pytest

from pulsearb.execution import (
    ExecutorSombra,
    escolher_executor,
)
from pulsearb.execution.executor import carregar_diario
from pulsearb.risk import (
    MOTIVOS,
    OrdemPretendida,
    PortaoDeRisco,
    autorizacao_para_live,
)
from pulsearb.risk.sincronia import Sincronia
from pulsearb.settings import Mode, RiskSettings


def _ordem(slug: str = "btc-updown-5m-1", shares: float = 5.0, preco: float = 0.50):
    return OrdemPretendida(
        slug=slug, token_id="tok-up", lado_up=True, shares=shares, preco_limite=preco
    )


def _sombra(tmp_path, modo=Mode.SHADOW, **ajustes):
    portao = PortaoDeRisco(
        RiskSettings(**ajustes),
        modo,
        caminho_do_registro=tmp_path / "registro.json",
        hoje="2026-08-25",
    )
    return ExecutorSombra(
        portao, caminho_do_diario=tmp_path / "diario.jsonl", modo=modo
    )


def _executar(sombra, ordem, **ajustes):
    padrao = {
        "feeds_saudaveis": True,
        "prob_prevista": 0.64,
        "seconds_left": 200.0,
        "ts_ns": 1_787_000_000_000_000_000,
        # Livro sadio por padrão: spread de 0,02, dentro do teto de 0,04.
        # Sem ele o portão do livro recusaria tudo e cada teste passaria a
        # medir o portão errado.
        "melhor_bid": 0.49,
        "melhor_ask": 0.51,
    }
    return sombra.executar(ordem, **(padrao | ajustes))


class TestOMesmoCaminho:
    """Se o shadow tomar atalho, deixa de ser ensaio e vira simulação."""

    def test_os_portoes_de_risco_rodam_de_verdade(self, tmp_path):
        sombra = _sombra(tmp_path, stake_max_por_trade_usdc=2.0)
        # 5 shares a 0,50 = 2,50 USDC, acima do teto de 2.
        decisao = _executar(sombra, _ordem())

        assert not decisao.pode
        assert decisao.motivo == MOTIVOS.STAKE_ACIMA_DO_TETO

    def test_o_portao_de_MODO_nao_roda_no_shadow(self, tmp_path):
        """Se rodasse, toda intenção sairia como `modo_nao_opera`.

        E aí o diário perderia exatamente a informação que justifica o
        ensaio: qual portão estaria segurando se o modo fosse LIVE.
        """
        decisao = _executar(_sombra(tmp_path), _ordem())

        assert decisao.pode
        assert decisao.motivo != MOTIVOS.MODO_NAO_OPERA

    def test_a_exposicao_e_contabilizada(self, tmp_path):
        """Sem isto, os tetos por janela e de exposição nunca seriam exercitados.

        E aí o ensaio não ensaiaria a parte que mais importa.
        """
        sombra = _sombra(
            tmp_path, stake_max_por_trade_usdc=5.0, stake_max_por_janela_usdc=4.0
        )
        primeira = _executar(sombra, _ordem(shares=5.0))
        segunda = _executar(sombra, _ordem(shares=5.0))

        assert primeira.pode
        assert not segunda.pode
        assert segunda.motivo == MOTIVOS.JANELA_NO_TETO

    def test_recusa_nao_consome_exposicao(self, tmp_path):
        sombra = _sombra(tmp_path, stake_max_por_trade_usdc=1.0)
        _executar(sombra, _ordem(shares=5.0))

        assert sombra.portao.registro.exposicao_total_usdc == 0.0


class TestNaoEnvia:
    def test_pedir_live_falha_alto(self, tmp_path):
        """Cair para shadow em silêncio é a falha silenciosa mais cara possível.

        O operador acredita que está operando, o dinheiro não se move, e a
        descoberta vem quando alguém for conferir o saldo.

        A mensagem passou a vir da autorização (`risk/autorizacao.py`), que
        lista TODOS os bloqueios. Duas asserções e as duas são independentes
        de máquina: o cliente de ordens não existe hoje em nenhuma, e a
        recusa é sempre a mesma exceção.
        """
        portao = PortaoDeRisco(RiskSettings(), Mode.LIVE)
        with pytest.raises(NotImplementedError) as erro:
            escolher_executor(
                Mode.LIVE, portao, caminho_do_diario=tmp_path / "d.jsonl"
            )

        assert "LIVE NAO autorizado" in str(erro.value)
        assert "sem_cliente_de_ordens" in str(erro.value)

    def test_a_recusa_de_live_lista_a_trava_tripla(self, tmp_path):
        """Sem as variáveis de ambiente, os três bloqueios de intenção saem
        juntos — e é isso que impede o operador de descobri-los um por um."""
        portao = PortaoDeRisco(RiskSettings(), Mode.LIVE)
        licenca = autorizacao_para_live(
            Mode.LIVE,
            env={},
            sincronia=Sincronia(sincronizado=True, fonte="teste", detalhe="ok"),
        )
        with pytest.raises(NotImplementedError) as erro:
            escolher_executor(
                Mode.LIVE,
                portao,
                caminho_do_diario=tmp_path / "d.jsonl",
                autorizacao=licenca,
            )

        mensagem = str(erro.value)
        assert "sem_confirmacao_explicita" in mensagem
        assert "sem_aceite_do_risco" in mensagem
        assert "sem_cliente_de_ordens" in mensagem
        # O relógio estava bom neste cenário: não deve aparecer.
        assert "relogio_nao_sincronizado" not in mensagem

    def test_executor_sombra_recusa_ser_construido_como_live(self, tmp_path):
        portao = PortaoDeRisco(RiskSettings(), Mode.LIVE)
        with pytest.raises(ValueError, match="nunca roda como LIVE"):
            ExecutorSombra(
                portao, caminho_do_diario=tmp_path / "d.jsonl", modo=Mode.LIVE
            )

    @pytest.mark.parametrize("modo", [Mode.SIM, Mode.SHADOW])
    def test_sim_e_shadow_ensaiam(self, tmp_path, modo):
        portao = PortaoDeRisco(RiskSettings(), modo)
        executor = escolher_executor(
            modo, portao, caminho_do_diario=tmp_path / "d.jsonl"
        )
        assert isinstance(executor, ExecutorSombra)
        assert executor.modo is modo


class TestDiario:
    def test_registra_aprovadas_E_recusadas(self, tmp_path):
        """Um shadow que só registra o que passou esconde o número que interessa.

        Quando o bot não opera, a pergunta é *qual portão está segurando* — e
        ela só tem resposta se as recusas forem gravadas.
        """
        sombra = _sombra(tmp_path, stake_max_por_trade_usdc=3.0)
        _executar(sombra, _ordem(shares=4.0))   # 2,00 — passa
        _executar(sombra, _ordem(shares=10.0))  # 5,00 — recusa

        linhas = carregar_diario(tmp_path / "diario.jsonl")
        assert len(linhas) == 2
        assert [linha["pode"] for linha in linhas] == [True, False]
        assert linhas[1]["motivo"] == MOTIVOS.STAKE_ACIMA_DO_TETO

    def test_guarda_o_livro_do_instante(self, tmp_path):
        """Sem o topo do livro, o diário vira lista de intenções sem consequência.

        É ele que permite estimar preenchimento depois.
        """
        sombra = _sombra(tmp_path)
        _executar(
            sombra,
            _ordem(),
            melhor_bid=0.49,
            melhor_ask=0.51,
            profundidade_no_topo=120.0,
            latencia_da_decisao_ms=42.5,
        )

        linha = carregar_diario(tmp_path / "diario.jsonl")[0]
        assert linha["melhor_bid"] == 0.49
        assert linha["melhor_ask"] == 0.51
        assert linha["profundidade_no_topo"] == 120.0
        assert linha["latencia_da_decisao_ms"] == 42.5
        assert linha["prob_prevista"] == 0.64

    def test_linha_quebrada_no_fim_nao_perde_a_sessao(self, tmp_path):
        """O diário é append durante uma sessão que pode ser morta a qualquer hora.

        A última linha pela metade é esperada; recusar o arquivo inteiro por
        causa dela perderia tudo que veio antes.
        """
        sombra = _sombra(tmp_path)
        _executar(sombra, _ordem())
        with (tmp_path / "diario.jsonl").open("a", encoding="utf-8") as arquivo:
            arquivo.write('{"ts_ns": 178700000000')  # morreu no meio

        linhas = carregar_diario(tmp_path / "diario.jsonl")
        assert len(linhas) == 1

    def test_o_diario_e_json_por_linha(self, tmp_path):
        sombra = _sombra(tmp_path)
        _executar(sombra, _ordem())

        bruto = (tmp_path / "diario.jsonl").read_text(encoding="utf-8")
        assert bruto.endswith("\n")
        assert json.loads(bruto.strip())["slug"] == "btc-updown-5m-1"


class TestResumo:
    def test_por_motivo_diz_qual_portao_esta_segurando(self, tmp_path):
        sombra = _sombra(tmp_path, stake_max_por_trade_usdc=3.0)
        _executar(sombra, _ordem("a", shares=4.0))
        _executar(sombra, _ordem("b", shares=10.0))
        _executar(sombra, _ordem("c", shares=10.0))
        _executar(sombra, _ordem("d", shares=4.0), feeds_saudaveis=False)

        resumo = sombra.resumo()
        assert resumo["intencoes"] == 4
        assert resumo["aprovadas"] == 1
        assert resumo["recusadas"] == 3
        assert resumo["por_motivo"] == {
            MOTIVOS.FEED_PARADO: 1,
            MOTIVOS.STAKE_ACIMA_DO_TETO: 2,
        }
        assert resumo["capital_que_teria_sido_movimentado_usdc"] == pytest.approx(2.0)

    def test_o_resumo_avisa_que_nada_foi_enviado(self, tmp_path):
        # O aviso não é decoração: `aprovadas` não é promessa de preenchimento,
        # porque ninguém do outro lado sabe que a ordem existe.
        resumo = _sombra(tmp_path).resumo()
        assert "NADA foi enviado" in resumo["nota"]
        assert "nao ha fila" in resumo["nota"]
