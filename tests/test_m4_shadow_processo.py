"""3.13 — o processo que dá rede ao ciclo.

O que se testa aqui é a MONTAGEM e a CADÊNCIA. Os sockets em si não entram na
suíte: rede num teste torna o resultado dependente do dia. O que entra é tudo
que decide se o processo está ligado certo — e a ligação mais importante do M4
mora na fábrica.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pulsearb.execution.executor import ExecutorSombra
from pulsearb.live.ciclo import CicloAoVivo
from pulsearb.live.motor import ConfigDoMotor
from pulsearb.live.shadow import (
    ProcessoShadow,
    montar_ciclo,
)
from pulsearb.settings import Mode, RiskSettings, Settings


def _settings(tmp_path, modo=Mode.SHADOW, **risco):
    return Settings(
        mode=modo,
        risk=RiskSettings(
            caminho_do_registro=str(tmp_path / "registro.json"),
            caminho_do_kill=str(tmp_path / "KILL"),
            **risco,
        ),
    )


class TestAFabrica:
    def test_o_portao_recebe_a_fonte_de_relogio_do_ciclo(self, tmp_path):
        """A ligação mais importante do M4, e a mais fácil de esquecer.

        A trava de relógio (3.10) tem fonte desde o #48 e o ciclo a alimenta
        desde o #51 — mas se o portão for construído sem ela, ele diria "não
        sei" a cada ordem, que é recusa. O SHADOW registraria
        `relogio_nao_monitorado` em toda linha do diário e não exercitaria
        portão nenhum.

        Tem de ser a MESMA instância: uma cópia não recebe os ticks.
        """
        ciclo = montar_ciclo(
            _settings(tmp_path), caminho_do_diario=tmp_path / "diario.jsonl"
        )
        portao = ciclo.motor.executor.portao

        assert portao.relogio_do_servidor is ciclo.motor.precos.relogio

    def test_em_shadow_o_executor_e_sombra(self, tmp_path):
        ciclo = montar_ciclo(
            _settings(tmp_path), caminho_do_diario=tmp_path / "diario.jsonl"
        )

        assert isinstance(ciclo.motor.executor, ExecutorSombra)

    def test_em_LIVE_a_fabrica_recusa(self, tmp_path):
        """Não há caminho aqui que envie ordem.

        `escolher_executor` recusa LIVE pela autorização; a fábrica não tem
        como contorná-lo porque é dele que o executor vem.
        """
        with pytest.raises(NotImplementedError) as erro:
            montar_ciclo(
                _settings(tmp_path, modo=Mode.LIVE),
                caminho_do_diario=tmp_path / "diario.jsonl",
            )

        assert "LIVE NAO autorizado" in str(erro.value)

    def test_o_stake_sai_do_teto_de_risco(self, tmp_path):
        """Duas fontes para o tamanho da ordem virariam duas verdades.

        O portão recusa acima de `stake_max_por_trade_usdc`; o motor pediria
        outro número e toda ordem sairia recusada por `stake_acima_do_teto`
        sem que nada estivesse errado.
        """
        ciclo = montar_ciclo(
            _settings(tmp_path, stake_max_por_trade_usdc=3.0),
            caminho_do_diario=tmp_path / "diario.jsonl",
        )

        assert ciclo.motor.config.shares_por_trade == 3.0

    def test_a_curva_de_variancia_chega_ao_motor(self, tmp_path):
        """Rodar o SHADOW no derivado depois de validar no medido recria ao
        vivo a diferença de 39 a 48× da §2d-ter."""
        sentinela = object()
        ciclo = montar_ciclo(
            _settings(tmp_path),
            caminho_do_diario=tmp_path / "diario.jsonl",
            curvas_de_variancia=sentinela,
        )

        assert ciclo.motor.config.curvas_de_variancia is sentinela

    def test_config_explicita_vence(self, tmp_path):
        config = ConfigDoMotor(threshold_edge=0.05)
        ciclo = montar_ciclo(
            _settings(tmp_path),
            caminho_do_diario=tmp_path / "diario.jsonl",
            config=config,
        )

        assert ciclo.motor.config is config

    def test_o_kill_switch_e_ligado(self, tmp_path):
        """A chave existe para ser puxada com o bot rodando.

        Se a fábrica não passar o caminho, `_kill_acionado` devolve False
        sempre e a chave vira decoração.
        """
        kill = tmp_path / "KILL"
        ciclo = montar_ciclo(
            _settings(tmp_path), caminho_do_diario=tmp_path / "diario.jsonl"
        )
        portao = ciclo.motor.executor.portao

        assert portao.caminho_do_kill == Path(str(kill))


class _CicloFalso:
    """Conta os passos sem decidir nada."""

    def __init__(self, explode: bool = False) -> None:
        self.passos = 0
        self.explode = explode
        self.descobertas: list[list] = []

    def passo(self, **_):
        self.passos += 1
        if self.explode:
            raise RuntimeError("livro em formato novo")
        return 0

    def on_descoberta(self, mercados, **_):
        self.descobertas.append(mercados)

    def resumo(self, **_):
        return {"eventos": {}}


def _processo(tmp_path, ciclo):
    return ProcessoShadow(_settings(tmp_path), ciclo)


class TestACadencia:
    async def test_o_laco_decide_e_para_no_prazo(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pulsearb.live.shadow.CADENCIA_DA_DECISAO_S", 0.01)
        ciclo = _CicloFalso()
        processo = _processo(tmp_path, ciclo)

        await asyncio.wait_for(
            processo.laco_de_decisao(asyncio.get_running_loop().time() + 0),
            timeout=2.0,
        )

        # Prazo já vencido: o laço não roda nem uma vez.
        assert ciclo.passos == 0

    async def test_um_passo_que_levanta_NAO_derruba_o_processo(
        self, tmp_path, monkeypatch
    ):
        """O SHADOW existe para rodar 24 h e mostrar o que aconteceu.

        Cair no primeiro evento estranho entregaria zero informação sobre as
        outras 23 horas. O erro sai nomeado no log e o laço segue — e este
        teste é o que impede alguém de "limpar" o try/except achando que ele
        esconde bug.
        """
        import time as _time

        monkeypatch.setattr("pulsearb.live.shadow.CADENCIA_DA_DECISAO_S", 0.001)
        ciclo = _CicloFalso(explode=True)
        processo = _processo(tmp_path, ciclo)
        fim = _time.monotonic() + 0.05

        await asyncio.wait_for(processo.laco_de_decisao(fim), timeout=2.0)

        assert ciclo.passos > 1  # seguiu depois do primeiro erro

    async def test_a_descoberta_alimenta_o_ciclo_e_assina_os_tokens(self, tmp_path):
        class _Descoberta:
            def __init__(self):
                self.chamadas = 0

            async def discover(self):
                self.chamadas += 1
                return [_mercado_falso("0xaa", {"Up": "t-up", "Down": "t-down"})]

        class _Poly:
            def __init__(self):
                self.assinados: list[list[str]] = []

            async def subscribe(self, tokens):
                self.assinados.append(list(tokens))

        ciclo = _CicloFalso()
        processo = _processo(tmp_path, ciclo)
        processo.poly = _Poly()
        descoberta = _Descoberta()

        await processo._um_ciclo_de_descoberta(descoberta)

        assert len(ciclo.descobertas) == 1
        assert processo.poly.assinados == [["t-down", "t-up"]]
        assert processo.tokens_assinados == {"t-up", "t-down"}

    async def test_token_ja_assinado_nao_e_reassinado(self, tmp_path):
        """Reassinar o mesmo token a cada 30 s gastaria banda e poluiria o
        log de assinatura sem mudar nada."""

        class _Descoberta:
            async def discover(self):
                return [_mercado_falso("0xaa", {"Up": "t-up", "Down": "t-down"})]

        class _Poly:
            def __init__(self):
                self.chamadas = 0

            async def subscribe(self, tokens):
                self.chamadas += 1

        processo = _processo(tmp_path, _CicloFalso())
        processo.poly = _Poly()
        descoberta = _Descoberta()

        await processo._um_ciclo_de_descoberta(descoberta)
        await processo._um_ciclo_de_descoberta(descoberta)

        assert processo.poly.chamadas == 1
        assert processo.descobertas == 2

    async def test_janela_NAO_operavel_tambem_e_assinada(self, tmp_path):
        """O motor decide se opera; o diário quer o motivo.

        Não ver o livro de uma janela recusada trocaria "recusei por X" por
        "não sei nada sobre ela" — e é exatamente o que o M2 quer medir.
        """

        class _Descoberta:
            async def discover(self):
                return [
                    _mercado_falso(
                        "0xbb", {"Up": "n-up", "Down": "n-down"}, operable=False
                    )
                ]

        class _Poly:
            def __init__(self):
                self.assinados: list[str] = []

            async def subscribe(self, tokens):
                self.assinados.extend(tokens)

        processo = _processo(tmp_path, _CicloFalso())
        processo.poly = _Poly()

        await processo._um_ciclo_de_descoberta(_Descoberta())

        assert set(processo.poly.assinados) == {"n-up", "n-down"}


def _mercado_falso(condition_id, tokens, *, operable=True):
    from datetime import UTC, datetime

    from pulsearb.markets.discovery import DiscoveredMarket

    iso = datetime.fromtimestamp(1_787_000_300, UTC).isoformat().replace("+00:00", "Z")
    return DiscoveredMarket(
        slug="btc-updown-5m-1787000300",
        condition_id=condition_id,
        asset="btc",
        resolution="chainlink_twap",
        token_id_by_outcome=tokens,
        tick_size=0.01,
        min_order_size=5.0,
        fee_rate=0.0,
        fee_exponent=1.0,
        fee_taker_only=True,
        fee_rebate_rate=0.2,
        accepting_orders=True,
        end_date_iso=iso,
        operable=operable,
        raw_gamma={"endDate": iso},
    )


class TestOEstado:
    def test_o_estado_junta_processo_e_ciclo(self, tmp_path):
        ciclo = montar_ciclo(
            _settings(tmp_path), caminho_do_diario=tmp_path / "diario.jsonl"
        )
        processo = ProcessoShadow(_settings(tmp_path), ciclo)

        estado = processo.estado()

        assert estado["passos"] == 0
        assert estado["descobertas"] == 0
        assert "feeds_saudaveis" in estado
        assert isinstance(ciclo, CicloAoVivo)
