"""3.13 — o processo que dá rede ao ciclo.

O que se testa aqui é a MONTAGEM e a CADÊNCIA. Os sockets em si não entram na
suíte: rede num teste torna o resultado dependente do dia. O que entra é tudo
que decide se o processo está ligado certo — e a ligação mais importante do M4
mora na fábrica.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pulsearb.caminhos import ENV_RAIZ_DE_SAIDA
from pulsearb.execution.executor import ExecutorSombra
from pulsearb.live.ciclo import CicloAoVivo
from pulsearb.live.motor import ConfigDoMotor
from pulsearb.live.shadow import (
    ProcessoShadow,
    _curvas,
    montar_ciclo,
)
from pulsearb.settings import FeedSettings, Mode, RiskSettings, Settings

#: V(t) do btc medida em 24 h (relatorios/VARIANCIA_24AGO.json), cortada nos
#: horizontes que bastam para a curva ser avaliável — o que se testa aqui é o
#: caminho até o arquivo, não a forma da curva.
_PONTOS_BTC = (
    (1.0, 2.352126724427579e-10),
    (2.0, 6.766494738216218e-10),
    (5.0, 3.878150567744716e-09),
    (10.0, 1.483584639264751e-08),
    (30.0, 1.1616900502179133e-07),
    (60.0, 3.690323096354988e-07),
    (120.0, 8.898465093595007e-07),
    (180.0, 1.3675723240230315e-06),
)


def _relatorio_de_variancia() -> dict:
    return {
        "por_ativo": {
            "btc": {
                "veredito": {
                    "avaliavel": True,
                    "monotona": True,
                    "linear_no_longo": True,
                    "ha_suavizacao": True,
                },
                "horizontes": [
                    {"horizonte_s": h, "variancia": v, "suficiente": True}
                    for h, v in _PONTOS_BTC
                ],
            }
        }
    }


def _settings(tmp_path, modo=Mode.SHADOW, feeds=None, **risco):
    return Settings(
        mode=modo,
        feeds=feeds or FeedSettings(),
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
        settings = _settings(tmp_path, modo=Mode.LIVE)
        diario = tmp_path / "diario.jsonl"

        with pytest.raises(NotImplementedError) as erro:
            montar_ciclo(settings, caminho_do_diario=diario)

        assert "LIVE NAO autorizado" in str(erro.value)

    def test_o_tamanho_da_ordem_NAO_sai_do_teto_em_USDC(self, tmp_path):
        """São unidades diferentes, e eu as tinha confundido.

        `stake_max_por_trade_usdc` é USDC e quem o aplica é o PORTÃO, sobre
        `shares × preço`. `shares_por_trade` é em SHARES, e o default do
        backtest (5) é o mínimo que o mercado aceita (API_NOTES §12.5).

        Derivar um do outro punha 3 shares num mercado que exige 5 — ordem que
        a corretora rejeita, e que o SHADOW registraria como `pode=true`.
        """
        ciclo = montar_ciclo(
            _settings(tmp_path, stake_max_por_trade_usdc=3.0),
            caminho_do_diario=tmp_path / "diario.jsonl",
        )

        assert ciclo.motor.config.shares_por_trade == 5.0
        assert ciclo.motor.executor.portao.settings.stake_max_por_trade_usdc == 3.0

    def test_o_limiar_de_silencio_sai_da_configuracao(self, tmp_path):
        """Achado do Codex no #52, e procede.

        A fábrica deixava `silencio_do_preco_s` no default do módulo (10 s)
        enquanto o M1 configura 5 s. O ciclo ficava MAIS permissivo que a
        configuração: por 5 s extras `feeds_saudaveis` seguia verdadeiro e o
        SHADOW registrava intenção com preço que a própria configuração já
        declara velho.
        """
        ciclo = montar_ciclo(
            _settings(tmp_path),
            caminho_do_diario=tmp_path / "diario.jsonl",
        )

        assert ciclo.silencio_do_preco_s == 5.0

    def test_um_limiar_ajustado_chega_ao_ciclo(self, tmp_path):
        """Não é o 5.0 que importa: é sair da configuração, qualquer que seja.

        Um teste que só checasse o default passaria se a fábrica tivesse o
        número escrito à mão.
        """
        ciclo = montar_ciclo(
            _settings(tmp_path, feeds=FeedSettings(stale_after_seconds_twap=1.5)),
            caminho_do_diario=tmp_path / "diario.jsonl",
        )

        assert ciclo.silencio_do_preco_s == 1.5

    def test_o_silencio_fecha_no_limiar_configurado(self, tmp_path):
        """O efeito, não só a fiação: 6 s de silêncio com limiar de 5 s fecha.

        Antes do conserto isto passava — 6 < 10 —, que é exatamente a janela
        em que o Codex mostrou o SHADOW decidindo com preço velho.
        """
        ciclo = montar_ciclo(
            _settings(tmp_path),
            caminho_do_diario=tmp_path / "diario.jsonl",
        )
        ciclo.ultimo_preco_ns["btc"] = 0

        assert ciclo.feeds_saudaveis(agora_ns=4_000_000_000) is True
        assert ciclo.feeds_saudaveis(agora_ns=6_000_000_000) is False

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


class TestOsCincoAchadosDaRevisao:
    """Cinco defeitos que a revisão do #52 pegou. Um teste por defeito.

    Nenhum era hipotético: os cinco foram conferidos contra o código antes de
    corrigir, e cada um tinha uma consequência concreta numa rodada de 24 h.
    """

    def test_P1_abre_TODAS_as_conexoes_rtds_configuradas(self, tmp_path):
        """`rtds_conexoes` default é 2, e o processo abria UMA.

        Conexão individual do RTDS já produziu lacunas de 30 a 306 s. Uma
        lacuna aqui que a gravação não tem faria o SHADOW perder ticks de
        âncora que o backtest enxerga — furando a comparação, que é a razão de
        o SHADOW existir.
        """
        settings = _settings(tmp_path)
        ciclo = montar_ciclo(settings, caminho_do_diario=tmp_path / "d.jsonl")
        processo = ProcessoShadow(settings, ciclo)

        assert settings.feeds.rtds_conexoes == 2
        assert len(processo.rtds_feeds) == settings.feeds.rtds_conexoes
        # Rótulos distintos: sem eles as duas logam idêntico e não dá para
        # saber qual conexão reclamava.
        assert len({f.rotulo for f in processo.rtds_feeds}) == 2

    async def test_P2_token_de_janela_encerrada_e_DESASSINADO(self, tmp_path):
        """24 h de descoberta acumulariam milhares de assinaturas.

        Cada reconexão reenvia o conjunto inteiro no frame inicial, e os
        livros ficam retidos. A carência é a MESMA do recorder — se as duas
        divergissem, um pararia de ver o token antes do outro.
        """
        import time as _time

        from pulsearb.tempo import RESOLUTION_GRACE_SECONDS

        class _Poly:
            def __init__(self):
                self.assinados: list[str] = []
                self.desassinados: list[str] = []

            async def subscribe(self, tokens):
                self.assinados.extend(tokens)

            async def unsubscribe(self, tokens):
                self.desassinados.extend(tokens)

        class _Descoberta:
            def __init__(self, fim):
                self.fim = fim

            async def discover(self):
                return [_mercado_falso("0xaa", {"Up": "t-up"}, fecha=self.fim)]

        ciclo = _CicloFalso()
        processo = _processo(tmp_path, ciclo)
        processo.poly = _Poly()

        # Janela aberta: assina.
        await processo._um_ciclo_de_descoberta(
            _Descoberta(_time.time() + 300)
        )
        assert processo.tokens_assinados == {"t-up"}

        # Janela fechada há mais que a carência: desassina.
        await processo._um_ciclo_de_descoberta(
            _Descoberta(_time.time() - RESOLUTION_GRACE_SECONDS - 60)
        )
        assert processo.poly.desassinados == ["t-up"]
        assert processo.tokens_assinados == set()
        # O livro sai junto: `LivrosAoVivo` não expira sozinho, e 24 h de
        # rotação deixariam milhares de `OrderBook` mortos.
        assert ciclo.motor.livros.esquecidos == ["t-up"]

    async def test_P2_dentro_da_carencia_NAO_desassina(self, tmp_path):
        """A resolução não chega no instante do fechamento.

        Desassinar cedo perderia justamente o evento que diz quem ganhou.
        """
        import time as _time

        class _Poly:
            def __init__(self):
                self.desassinados: list[str] = []

            async def subscribe(self, tokens):
                pass

            async def unsubscribe(self, tokens):
                self.desassinados.extend(tokens)

        class _Descoberta:
            async def discover(self):
                return [
                    _mercado_falso(
                        "0xaa", {"Up": "t-up"}, fecha=_time.time() - 60
                    )
                ]

        processo = _processo(tmp_path, _CicloFalso())
        processo.poly = _Poly()

        await processo._um_ciclo_de_descoberta(_Descoberta())

        assert processo.poly.desassinados == []
        assert processo.tokens_assinados == {"t-up"}

    def test_P2_a_duracao_documentada_e_aceita(self):
        """`--duration 24h` era rejeitado por `type=float`.

        A doc anunciava um comando que não inicia. Agora o parser é o mesmo
        do recorder.
        """
        from pulsearb.tempo import parse_duration

        assert parse_duration("24h") == 86400.0
        assert parse_duration("90s") == 90.0
        assert parse_duration("72") == 259200.0  # sem sufixo = horas

    async def test_P2_o_sono_respeita_o_prazo(self):
        """Uma rodada de 10 s bloqueava ~60 s no laço de relato.

        O `gather` espera as três tarefas, e a mais lenta manda: `--duration`
        estourava em quase um minuto. Rodada de fumaça que dura seis vezes o
        pedido não é usada por ninguém.
        """
        import time as _time

        from pulsearb.live.shadow import _dormir_ate

        inicio = _time.monotonic()
        await _dormir_ate(60.0, _time.monotonic() + 0.05)

        assert _time.monotonic() - inicio < 1.0

    async def test_P2_prazo_ja_vencido_nao_dorme(self):
        import time as _time

        from pulsearb.live.shadow import _dormir_ate

        inicio = _time.monotonic()
        await _dormir_ate(60.0, _time.monotonic() - 10)

        assert _time.monotonic() - inicio < 0.5

    def test_P2_o_logging_e_configurado_antes_de_rodar(self, monkeypatch, tmp_path):
        """Sem `setup_logging()` o root fica em WARNING.

        Os relatos de 60 s, os avisos de conexão e os motivos de janela
        ignorada sumiriam — 24 h de rodada entregando só o resumo final.
        """
        chamadas: list[int] = []
        monkeypatch.setattr(
            "pulsearb.live.shadow.setup_logging", lambda: chamadas.append(1)
        )
        monkeypatch.setattr(
            "pulsearb.live.shadow.Settings.load",
            classmethod(lambda cls: _settings(tmp_path, modo=Mode.LIVE)),
        )

        # Em LIVE o `main` sai em 2 — mas só DEPOIS de configurar o log.
        from pulsearb.live.shadow import main

        assert main([]) == 2
        assert chamadas == [1]


class TestOModoEDoProcessoNaoDoArquivo:
    """Achado P2 do Codex no #52, e procede: o `config.yaml` versionado traz
    `mode: SIM`, então o comando documentado do SHADOW criava um executor SIM,
    gravava em `registro_do_dia.sim.json` e relatava modo SIM — desfazendo a
    separação de registro que este mesmo PR criou."""

    @pytest.fixture(autouse=True)
    def _numa_pasta_temporaria(self, tmp_path, monkeypatch):
        """`main` sem `--diario` CRIA o arquivo. Sem isto a suite escreveria
        em `data/shadow/` do repositorio a cada execucao."""
        monkeypatch.chdir(tmp_path)

    def _rodar_main(self, monkeypatch, tmp_path, modo):
        vistos: list = []

        monkeypatch.setattr("pulsearb.live.shadow.setup_logging", lambda: None)
        monkeypatch.setattr(
            "pulsearb.live.shadow.Settings.load",
            classmethod(lambda cls: _settings(tmp_path, modo=modo)),
        )

        def _montar(settings, **kwargs):
            vistos.append(settings.mode)
            return _CicloFalso()

        monkeypatch.setattr("pulsearb.live.shadow.montar_ciclo", _montar)

        class _ProcessoFalso:
            falhou = None

            def __init__(self, *_a, **_k):
                pass

            async def run(self, _duracao):
                return {"passos": 0}

        monkeypatch.setattr("pulsearb.live.shadow.ProcessoShadow", _ProcessoFalso)

        from pulsearb.live.shadow import main

        codigo = main(["--duration", "1s"])
        return codigo, vistos

    def test_config_em_SIM_roda_como_SHADOW(self, monkeypatch, tmp_path, capsys):
        codigo, vistos = self._rodar_main(monkeypatch, tmp_path, Mode.SIM)
        capsys.readouterr()

        assert codigo == 0
        assert vistos == [Mode.SHADOW]

    def test_config_em_SHADOW_segue_SHADOW(self, monkeypatch, tmp_path, capsys):
        codigo, vistos = self._rodar_main(monkeypatch, tmp_path, Mode.SHADOW)
        capsys.readouterr()

        assert codigo == 0
        assert vistos == [Mode.SHADOW]

    def test_LIVE_continua_recusado_e_nao_e_convertido(self, monkeypatch, tmp_path):
        """Forçar não pode virar um jeito de LIVE entrar por baixo."""
        monkeypatch.setattr("pulsearb.live.shadow.setup_logging", lambda: None)
        monkeypatch.setattr(
            "pulsearb.live.shadow.Settings.load",
            classmethod(lambda cls: _settings(tmp_path, modo=Mode.LIVE)),
        )

        from pulsearb.live.shadow import main

        assert main([]) == 2

    def test_rodada_que_falhou_sai_com_codigo_diferente_de_zero(
        self, monkeypatch, tmp_path, capsys
    ):
        """24 h que não gravaram nada não são sucesso — nem para o systemd,
        nem para quem lê o log."""
        monkeypatch.setattr("pulsearb.live.shadow.setup_logging", lambda: None)
        monkeypatch.setattr(
            "pulsearb.live.shadow.Settings.load",
            classmethod(lambda cls: _settings(tmp_path, modo=Mode.SHADOW)),
        )
        monkeypatch.setattr(
            "pulsearb.live.shadow.montar_ciclo", lambda *a, **k: _CicloFalso()
        )

        class _ProcessoQueFalhou:
            falhou = "diario nao gravavel: OSError: No space left on device"

            def __init__(self, *_a, **_k):
                pass

            async def run(self, _duracao):
                return {"falhou": self.falhou}

        monkeypatch.setattr(
            "pulsearb.live.shadow.ProcessoShadow", _ProcessoQueFalhou
        )

        from pulsearb.live.shadow import main

        assert main(["--duration", "1s"]) == 1
        capsys.readouterr()


class TestARodadaQueFalhaTerminaLogo:
    """Achado P2 do Codex no #52, segunda rodada sobre o mesmo defeito.

    O tratamento de falha do diário fazia `laco_de_decisao` RETORNAR, mas
    `asyncio.wait` no default (`ALL_COMPLETED`) deixava descoberta e relato
    seguindo até o prazo original. Uma rodada de 24 h abortada no minuto 5
    manteria sockets e HTTP ativos pelas 23 h restantes antes de devolver o
    código != 0.
    """

    async def test_o_primeiro_laco_a_sair_encerra_a_rodada(self):
        """`FIRST_COMPLETED`, e não `ALL_COMPLETED`."""
        import asyncio as _asyncio
        import time as _time

        cancelada = _asyncio.Event()

        async def curta():
            return "acabei"

        async def longa():
            try:
                await _asyncio.sleep(30)
            except _asyncio.CancelledError:
                cancelada.set()
                raise

        tarefas = [_asyncio.create_task(curta()), _asyncio.create_task(longa())]
        inicio = _time.monotonic()
        await _asyncio.wait(
            tarefas, timeout=30, return_when=_asyncio.FIRST_COMPLETED
        )
        for t in tarefas:
            t.cancel()
        await _asyncio.gather(*tarefas, return_exceptions=True)

        assert _time.monotonic() - inicio < 1.0
        assert cancelada.is_set()

    @pytest.fixture(autouse=True)
    def _numa_pasta_temporaria(self, tmp_path, monkeypatch):
        """`main` sem `--diario` CRIA o arquivo. Sem isto a suite escreveria
        em `data/shadow/` do repositorio a cada execucao."""
        monkeypatch.chdir(tmp_path)

    def test_o_run_usa_FIRST_COMPLETED(self):
        """O teste acima prova a semântica do asyncio; este trava o uso.

        Sem ele, alguém tira o `return_when` achando que é redundante e o
        default volta a segurar a rodada até o prazo.
        """
        import inspect

        from pulsearb.live import shadow

        fonte = inspect.getsource(shadow.ProcessoShadow.run)

        assert "FIRST_COMPLETED" in fonte


class TestUmDiarioPorRodada:
    """Achado P2 do Codex no #52, e procede.

    O default era fixo e `ExecutorSombra._anotar` abre em modo APPEND: uma
    rodada curta de teste seguida do ensaio de 24 h escreviam as duas no mesmo
    arquivo, sem identificador de rodada nem fronteira no JSONL. As contagens
    saíam somadas e a comparação SHADOW × backtest lia duas populações como se
    fossem uma — o mesmo formato do defeito do critério 1.4.
    """

    @pytest.fixture(autouse=True)
    def _numa_pasta_temporaria(self, tmp_path, monkeypatch):
        """O gerador CRIA o arquivo. Sem isto os testes sujariam `data/`."""
        monkeypatch.chdir(tmp_path)

    def test_duas_rodadas_seguidas_nao_dividem_arquivo(self):
        from datetime import UTC, datetime

        from pulsearb.live.shadow import caminho_do_diario_da_rodada

        primeira = caminho_do_diario_da_rodada(datetime(2026, 8, 30, 10, 0, 0, tzinfo=UTC))
        segunda = caminho_do_diario_da_rodada(datetime(2026, 8, 30, 10, 0, 1, tzinfo=UTC))

        assert primeira != segunda

    def test_o_MESMO_instante_ainda_da_arquivos_diferentes(self):
        """Achado P2 do Codex, segunda rodada sobre o mesmo ponto.

        Carimbo de um segundo consertou o caso comum e deixou o estreito:
        dois processos iniciados no mesmo segundo — ou dois `--duration 0`
        seguidos — recebiam o mesmo caminho, e o diário abre em APPEND.

        A garantia não é o relógio: é `O_EXCL`. É o sistema de arquivos
        decidindo, e por isso o teste passa o MESMO instante duas vezes.
        """
        from datetime import UTC, datetime

        from pulsearb.live.shadow import caminho_do_diario_da_rodada

        instante = datetime(2026, 8, 30, 10, 0, 0, tzinfo=UTC)

        primeiro = caminho_do_diario_da_rodada(instante)
        segundo = caminho_do_diario_da_rodada(instante)

        assert primeiro != segundo

    def test_o_arquivo_nasce_criado_e_vazio(self):
        """Ele é a prova de que a rodada começou, mesmo que ela morra antes
        da primeira intenção."""
        from datetime import UTC, datetime
        from pathlib import Path as _Path

        from pulsearb.live.shadow import caminho_do_diario_da_rodada

        caminho = _Path(
            caminho_do_diario_da_rodada(datetime(2026, 8, 30, 10, 0, 0, tzinfo=UTC))
        )

        assert caminho.exists()
        assert caminho.read_text() == ""

    def test_o_carimbo_e_do_instante_de_inicio(self):
        from datetime import UTC, datetime

        from pulsearb.live.shadow import caminho_do_diario_da_rodada

        caminho = caminho_do_diario_da_rodada(
            datetime(2026, 8, 30, 16, 20, 5, tzinfo=UTC)
        )

        assert caminho.startswith("data/shadow/diario-20260830-162005-")

    def test_caminho_explicito_e_respeitado(self, monkeypatch, tmp_path, capsys):
        """Anexar a um caminho dado é como se retoma uma rodada de propósito —
        a correção não pode tirar isso.

        O caminho é RELATIVO à raiz: `--diario` é escrito no disco vindo da
        linha de comando, então passa pela mesma contenção que o `--json` do
        backtest e o `--curva-de-variancia` (#53). Absoluto é recusado.
        """
        vistos = []

        monkeypatch.setattr("pulsearb.live.shadow.setup_logging", lambda: None)
        monkeypatch.setattr(
            "pulsearb.live.shadow.Settings.load",
            classmethod(lambda cls: _settings(tmp_path, modo=Mode.SHADOW)),
        )

        def _montar(settings, *, caminho_do_diario, **kwargs):
            vistos.append(caminho_do_diario)
            return _CicloFalso()

        monkeypatch.setattr("pulsearb.live.shadow.montar_ciclo", _montar)

        class _ProcessoFalso:
            falhou = None

            def __init__(self, *_a, **_k):
                pass

            async def run(self, _duracao):
                return {}

        monkeypatch.setattr("pulsearb.live.shadow.ProcessoShadow", _ProcessoFalso)

        from pulsearb.live.shadow import main

        escolhido = "meu-diario.jsonl"
        main(["--duration", "1s", "--diario", escolhido])
        capsys.readouterr()

        assert vistos == [tmp_path / escolhido]

    def test_sem_a_opcao_cada_rodada_ganha_o_seu(
        self, monkeypatch, tmp_path, capsys
    ):
        vistos = []

        monkeypatch.setattr("pulsearb.live.shadow.setup_logging", lambda: None)
        monkeypatch.setattr(
            "pulsearb.live.shadow.Settings.load",
            classmethod(lambda cls: _settings(tmp_path, modo=Mode.SHADOW)),
        )

        def _montar(settings, *, caminho_do_diario, **kwargs):
            vistos.append(caminho_do_diario)
            return _CicloFalso()

        monkeypatch.setattr("pulsearb.live.shadow.montar_ciclo", _montar)

        class _ProcessoFalso:
            falhou = None

            def __init__(self, *_a, **_k):
                pass

            async def run(self, _duracao):
                return {}

        monkeypatch.setattr("pulsearb.live.shadow.ProcessoShadow", _ProcessoFalso)

        from pulsearb.live.shadow import main

        main(["--duration", "1s"])
        capsys.readouterr()

        assert vistos[0] != Path("data/shadow/diario.jsonl")
        assert "diario-" in vistos[0].name


class TestOsTresAchadosDaSegundaRevisao:
    """Mais três, e os três procedem. Um deles era erro de unidade meu."""

    def test_P1_janela_HORARIA_nao_e_operada(self, tmp_path):
        """O jogo horário resolve pelo candle 1h da Binance.

        A âncora dele é o campo `o` do `kline_1h` (`engine/hourly.py`), não o
        stream `twap_sixty`. Um processo que só assina RTDS não tem essa
        série, e `estimar_prob_up` cairia em `prob_up_hourly` com a âncora do
        observável errado — toda probabilidade horária saindo de uma série que
        não é a que resolve a janela.

        Falha fechada. Sai desta lista quando o feed da Binance estiver ligado
        e roteado, e não antes.
        """
        from pulsearb.engine.decisao import JOGO_TWAP

        ciclo = montar_ciclo(
            _settings(tmp_path), caminho_do_diario=tmp_path / "d.jsonl"
        )

        assert ciclo.motor.config.jogos_operados == frozenset({JOGO_TWAP})

    def test_P1_o_motor_pula_o_jogo_que_nao_opera(self, tmp_path):
        from pulsearb.engine.decisao import JOGO_HORARIO
        from pulsearb.live.motor import PULOU_JOGO_NAO_OPERADO
        from pulsearb.live.rastreador import JanelaAoVivo

        ciclo = montar_ciclo(
            _settings(tmp_path), caminho_do_diario=tmp_path / "d.jsonl"
        )
        janela = JanelaAoVivo(
            slug="btc-updown-1h-1",
            condition_id="0xhh",
            asset="btc",
            jogo=JOGO_HORARIO,
            token_up="h-up",
            token_down="h-down",
            abertura_epoch=1_787_000_000.0,
            fechamento_epoch=1_787_003_600.0,
            duracao_s=3600,
            min_order_size=5.0,
            tick_size=0.01,
            fee_rate=0.0,
            fee_exponent=1.0,
        )
        ciclo.motor.rastreador.janelas["0xhh"] = janela

        ciclo.motor.tick(
            agora_epoch=1_787_003_500.0,
            agora_ns=1_787_003_500_000_000_000,
            feeds_saudaveis=True,
        )

        assert ciclo.motor.pulos.get(PULOU_JOGO_NAO_OPERADO) == 1

    def test_P2_so_os_ativos_OPERADOS_entram_no_feed(self, tmp_path):
        """`all_price_assets` traz os `extra_price_assets`, que existem para
        gravação e backtest futuro.

        Como `feeds_saudaveis` fecha pelo PIOR ativo, um SOL mudo bloquearia
        intenções de BTC/ETH saudáveis — o gate de saúde passaria a depender
        de ativos que o bot nem opera.
        """
        settings = Settings(
            assets=["btc", "eth"],
            extra_price_assets=["sol", "hype"],
            risk=RiskSettings(
                caminho_do_registro=str(tmp_path / "r.json"),
                caminho_do_kill=str(tmp_path / "KILL"),
            ),
        )
        ciclo = montar_ciclo(settings, caminho_do_diario=tmp_path / "d.jsonl")
        processo = ProcessoShadow(settings, ciclo)

        assert set(processo.rtds_feeds[0].assets) == {"btc", "eth"}
        # E — o que realmente importa — o CICLO filtra. O `assets` do feed só
        # afeta o `on_tick`; o `on_event`, por onde o ciclo recebe, é chamado
        # incondicionalmente (`feeds/base.py`). Sem o filtro no ciclo a
        # correção seria só aparente.
        assert ciclo.ativos_operados == frozenset({"btc", "eth"})
        # E os extras seguem existindo na config, para o recorder.
        assert set(settings.all_price_assets) >= {"sol", "hype"}

    def test_P2_ordem_abaixo_do_minimo_do_mercado_nao_vira_intencao(self, tmp_path):
        """O backtest já recusa isto (`sinais_abaixo_do_minimo`).

        Sem a mesma recusa aqui, o SHADOW registraria `pode=true` para uma
        ordem que a corretora rejeitaria, e a população dele divergiria da do
        backtest.
        """
        from pulsearb.engine.decisao import JOGO_TWAP
        from pulsearb.live.motor import PULOU_ABAIXO_DO_MINIMO
        from pulsearb.live.rastreador import JanelaAoVivo

        ciclo = montar_ciclo(
            _settings(tmp_path),
            caminho_do_diario=tmp_path / "d.jsonl",
            config=ConfigDoMotor(shares_por_trade=3.0),
        )
        ciclo.motor.rastreador.janelas["0xaa"] = JanelaAoVivo(
            slug="btc-updown-5m-1",
            condition_id="0xaa",
            asset="btc",
            jogo=JOGO_TWAP,
            token_up="t-up",
            token_down="t-down",
            abertura_epoch=1_787_000_000.0,
            fechamento_epoch=1_787_000_300.0,
            duracao_s=300,
            min_order_size=5.0,
            tick_size=0.01,
            fee_rate=0.0,
            fee_exponent=1.0,
        )

        ciclo.motor.tick(
            agora_epoch=1_787_000_200.0,
            agora_ns=1_787_000_200_000_000_000,
            feeds_saudaveis=True,
        )

        assert ciclo.motor.pulos.get(PULOU_ABAIXO_DO_MINIMO) == 1


class _LivrosFalsos:
    def __init__(self) -> None:
        self.esquecidos: list[str] = []

    def esquecer(self, token_id: str) -> bool:
        self.esquecidos.append(token_id)
        return True


class _CicloFalso:
    """Conta os passos sem decidir nada."""

    def __init__(self, explode: bool = False, erro: Exception | None = None) -> None:
        self.passos = 0
        self.explode = explode
        self.erro = erro
        self.descobertas: list[list] = []
        self.motor = SimpleNamespace(livros=_LivrosFalsos())

    def passo(self, **_):
        self.passos += 1
        if self.erro is not None:
            raise self.erro
        if self.explode:
            raise RuntimeError("livro em formato novo")
        return 0

    def on_descoberta(self, mercados, **_):
        self.descobertas.append(mercados)

    def resumo(self, **_):
        return {"eventos": {}}


def _processo(tmp_path, ciclo):
    return ProcessoShadow(_settings(tmp_path), ciclo)


class TestORunRespeitaOPrazo:
    """Item 3.14 — medido em produção antes de existir teste.

    Os testes de prazo existentes exercitam `_dormir_ate` e cada laço
    ISOLADO. Nenhum exercitava `run()` inteiro, e foi exatamente aí que o
    defeito apareceu: com `--duration 24h`, o processo seguia vivo em 24,6 h
    fazendo HTTP de descoberta depois de o diário parar de crescer.
    """

    def test_o_prazo_vence_pelo_relogio_de_PAREDE_quando_a_maquina_dorme(self):
        """A causa raiz do 3.14, medida nesta máquina.

        `time.monotonic()` no macOS sai de `mach_absolute_time()`, que
        **congela em suspensão**. Medido: 190,8 h de monotonic contra 370,8 h
        de parede desde o boot — **180 h de sono**. Um `--duration 24h` só em
        monotonic vira 24 h + o que a máquina dormir, e foi assim que o ensaio
        do 3.13 seguiu vivo em 24,6 h de parede.

        `caffeinate -i` não cobre: ele impede o sono por INATIVIDADE, não o de
        tampa fechada.
        """
        import time as _time

        from pulsearb.live.shadow import prazo_vencido

        # A máquina "dormiu": o monotonic ainda tem folga, o de parede não.
        assert prazo_vencido(
            deadline=_time.monotonic() + 3600,
            deadline_de_parede=_time.time() - 1,
        )

    def test_o_prazo_vence_pelo_MONOTONICO_quando_o_relogio_salta(self):
        """O outro lado, e a razão de não trocar um pelo outro.

        Um ajuste de NTP para trás faria o relógio de parede nunca vencer. O
        monotônico é imune a isso — é a metade que ele cobre bem, e é por isso
        que os dois ficam.
        """
        import time as _time

        from pulsearb.live.shadow import prazo_vencido

        assert prazo_vencido(
            deadline=_time.monotonic() - 1,
            deadline_de_parede=_time.time() + 86400,
        )

    def test_sem_prazo_de_parede_o_comportamento_e_o_antigo(self):
        """Retrocompatível: quem passa um prazo só continua funcionando."""
        import time as _time

        from pulsearb.live.shadow import prazo_vencido

        assert not prazo_vencido(_time.monotonic() + 60)
        assert prazo_vencido(_time.monotonic() - 1)

    async def test_run_retorna_no_prazo_mesmo_com_descoberta_LENTA(
        self, tmp_path, monkeypatch
    ):
        """O cenário do 3.14: um ciclo de descoberta que demora mais que o
        prazo inteiro.

        Ao vivo, `discover()` faz uma requisição `clob-markets/{id}` por
        mercado — dezenas em sequência, cada uma com timeout de 15 s. Se o
        prazo vence no meio disso, o `cancel()` do `finally` tem de
        interromper. Este teste falha se ele não interromper.
        """
        import time as _time

        monkeypatch.setattr("pulsearb.live.shadow.CADENCIA_DA_DECISAO_S", 0.01)
        monkeypatch.setattr("pulsearb.live.shadow.CADENCIA_DA_DESCOBERTA_S", 0.01)
        monkeypatch.setattr("pulsearb.live.shadow.CADENCIA_DO_RELATO_S", 0.01)

        class _PolyMudo:
            """O `run()` chama `start`/`stop` além de subscribe/unsubscribe."""

            async def start(self):
                return None

            async def stop(self):
                return None

            async def subscribe(self, tokens):
                return None

            async def unsubscribe(self, tokens):
                return None

        processo = _processo(tmp_path, _CicloFalso())
        processo.poly = _PolyMudo()
        processo.rtds_feeds = []

        async def descoberta_lenta():
            # Muito maior que o prazo do teste: se o cancelamento não
            # funcionar, o `run` fica pendurado aqui e o `wait_for` estoura.
            await asyncio.sleep(30.0)
            return []

        class _DescobertaLenta:
            async def discover(self):
                return await descoberta_lenta()

        monkeypatch.setattr(
            "pulsearb.live.shadow.MarketDiscovery",
            lambda **_: _DescobertaLenta(),
        )

        inicio = _time.monotonic()
        await asyncio.wait_for(processo.run(0.2), timeout=10.0)
        decorrido = _time.monotonic() - inicio

        # Folga generosa: o que se mede aqui é "encerrou", não "encerrou
        # rápido". Sem o conserto isto estoura os 10 s do `wait_for`.
        assert decorrido < 5.0, (
            f"run() levou {decorrido:.1f}s para um prazo de 0,2s — o "
            "cancelamento não interrompeu a descoberta (item 3.14)"
        )


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

    async def test_diario_nao_gravavel_ABORTA_a_rodada(self, tmp_path, monkeypatch):
        """Achado P1 do Codex no #52, e é o contrário do teste acima.

        A tolerância existe para um evento de mercado que o parser não
        entende. Diário sem poder escrever é outra coisa: o processo rodaria
        as 24 h, sairia com código 0 e entregaria ZERO intenções — e cada
        janela seria reavaliada para sempre, porque a execução nunca completa.

        Disco cheio e permissão errada chegam como `OSError`, e `passo()` não
        faz outro I/O: o diário é o único.
        """
        import time as _time

        monkeypatch.setattr("pulsearb.live.shadow.CADENCIA_DA_DECISAO_S", 0.001)
        ciclo = _CicloFalso(erro=OSError(28, "No space left on device"))
        processo = _processo(tmp_path, ciclo)
        fim = _time.monotonic() + 0.05

        await asyncio.wait_for(processo.laco_de_decisao(fim), timeout=2.0)

        assert ciclo.passos == 1, "parou no primeiro, nao insistiu por 24 h"
        assert processo.falhou is not None
        assert "No space left" in processo.falhou

    async def test_a_falha_sai_no_estado_e_o_sucesso_tambem(self, tmp_path):
        """`falhou` sai no JSON SEMPRE, inclusive `None`.

        Campo que só aparece quando há erro é campo que ninguém procura
        quando não há.
        """
        processo = _processo(tmp_path, _CicloFalso())

        assert processo.estado()["falhou"] is None

        processo.falhou = "diario nao gravavel"
        assert processo.estado()["falhou"] == "diario nao gravavel"

    async def test_a_descoberta_alimenta_o_ciclo_e_assina_os_tokens(self, tmp_path):
        import time as _time

        class _Descoberta:
            def __init__(self):
                self.chamadas = 0

            async def discover(self):
                self.chamadas += 1
                return [
                    _mercado_falso(
                        "0xaa",
                        {"Up": "t-up", "Down": "t-down"},
                        fecha=_time.time() + 300,
                    )
                ]

        class _Poly:
            def __init__(self):
                self.assinados: list[list[str]] = []

            async def subscribe(self, tokens):
                self.assinados.append(list(tokens))

            async def unsubscribe(self, tokens):
                raise AssertionError("janela aberta não deveria desassinar")

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

        import time as _time

        class _Descoberta:
            async def discover(self):
                return [
                    _mercado_falso(
                        "0xaa",
                        {"Up": "t-up", "Down": "t-down"},
                        fecha=_time.time() + 300,
                    )
                ]

        class _Poly:
            def __init__(self):
                self.chamadas = 0

            async def subscribe(self, tokens):
                self.chamadas += 1

            async def unsubscribe(self, tokens):
                raise AssertionError("janela aberta não deveria desassinar")

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

        import time as _time

        class _Descoberta:
            async def discover(self):
                return [
                    _mercado_falso(
                        "0xbb",
                        {"Up": "n-up", "Down": "n-down"},
                        operable=False,
                        fecha=_time.time() + 300,
                    )
                ]

        class _Poly:
            def __init__(self):
                self.assinados: list[str] = []

            async def subscribe(self, tokens):
                self.assinados.extend(tokens)

            async def unsubscribe(self, tokens):
                raise AssertionError("janela aberta não deveria desassinar")

        processo = _processo(tmp_path, _CicloFalso())
        processo.poly = _Poly()

        await processo._um_ciclo_de_descoberta(_Descoberta())

        assert set(processo.poly.assinados) == {"n-up", "n-down"}


def _mercado_falso(condition_id, tokens, *, operable=True, fecha=1_787_000_300):
    from datetime import UTC, datetime

    from pulsearb.markets.discovery import DiscoveredMarket

    iso = datetime.fromtimestamp(fecha, UTC).isoformat().replace("+00:00", "Z")
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


class TestAAllowlistDeDestino:
    """As URLs da descoberta são montadas por concatenação, e parte do que
    entra nelas vem do FIO. `seguro_na_url` protege UM ponto de construção;
    esta checagem protege o DESTINO, que é o que realmente importa.

    Defesa em profundidade de propósito: um campo novo interpolado numa URL
    amanhã não passa pelo `seguro_na_url` — mas passa por aqui.
    """

    BASES = ("https://gamma-api.polymarket.com", "https://clob.polymarket.com")

    def _adaptador(self, registrar=None):
        from pulsearb.markets.http import fazer_http_get_json

        class _Resposta:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"ok": True}

        class _Http:
            async def get(self, url, params=None):
                if registrar is not None:
                    registrar.append(url)
                return _Resposta()

        return fazer_http_get_json(_Http(), bases=self.BASES)

    async def test_endpoint_configurado_passa(self):
        pedidas: list[str] = []
        adaptador = self._adaptador(pedidas)

        assert await adaptador(
            "https://gamma-api.polymarket.com/markets/slug/btc", None
        ) == {"ok": True}
        assert pedidas == ["https://gamma-api.polymarket.com/markets/slug/btc"]

    @pytest.mark.parametrize(
        ("url", "porque"),
        [
            (
                "https://gamma-api.polymarket.com.exemplo-malicioso.com/x",
                "sufixo de dominio — o truque que a barra final barra",
            ),
            ("http://169.254.169.254/latest/meta-data/", "metadados da nuvem"),
            ("https://exemplo-malicioso.com/x", "host qualquer"),
            ("file:///etc/passwd", "esquema local"),
            ("http://gamma-api.polymarket.com/x", "http em vez de https"),
        ],
    )
    async def test_destino_de_fora_e_RECUSADO(self, url, porque):
        from pulsearb.markets.http import DestinoNaoPermitido

        pedidas: list[str] = []
        adaptador = self._adaptador(pedidas)

        with pytest.raises(DestinoNaoPermitido):
            await adaptador(url, None)
        # E a requisição não chegou a sair.
        assert pedidas == [], porque

    async def test_a_barra_final_e_o_que_barra_o_sufixo(self):
        """Sem ela, `...polymarket.com.evil.com` casaria com o prefixo.

        Este teste existe para que alguém que "simplifique" o `rstrip("/") +
        "/"` veja a consequência aqui, e não em produção.
        """
        from pulsearb.markets.http import DestinoNaoPermitido

        adaptador = self._adaptador()

        with pytest.raises(DestinoNaoPermitido):
            await adaptador("https://gamma-api.polymarket.comX/y", None)

    def test_allowlist_vazia_e_recusada_na_construcao(self):
        """Uma allowlist vazia recusaria tudo em silêncio no primeiro uso.

        Falhar na construção transforma um bot que não descobre nada e não
        diz por quê num erro imediato e legível.
        """
        from pulsearb.markets.http import fazer_http_get_json

        with pytest.raises(ValueError, match="allowlist vazia"):
            fazer_http_get_json(object(), bases=())


class TestOAdaptadorHttpEhCompartilhado:
    """O tratamento de 404 é semântica, não encanação.

    A Gamma responde 404 para slug sem mercado, e isso é resposta NORMAL: a
    grade testa candidatos que podem não existir. Se o recorder tratasse 404
    como `None` e o SHADOW como erro, um veria a janela e o outro não — e a
    divergência apareceria como diferença de mercado.
    """

    async def test_404_vira_none(self):
        from pulsearb.markets.http import fazer_http_get_json

        class _Resposta:
            status_code = 404

            def raise_for_status(self):
                raise AssertionError("404 não deveria levantar")

        class _Http:
            async def get(self, url, params=None):
                return _Resposta()

        adaptador = fazer_http_get_json(_Http(), bases=("https://exemplo/",))
        assert await adaptador("https://exemplo/markets/slug/x", None) is None

    async def test_erro_de_verdade_levanta(self):
        from pulsearb.markets.http import fazer_http_get_json

        class _Resposta:
            status_code = 500

            def raise_for_status(self):
                raise RuntimeError("500")

        class _Http:
            async def get(self, url, params=None):
                return _Resposta()

        adaptador = fazer_http_get_json(_Http(), bases=("https://exemplo/",))
        with pytest.raises(RuntimeError):
            await adaptador("https://exemplo/markets/slug/x", None)


class TestADescobertaNaoAposentaJanela:
    """Achado P1 do Codex no #52, e era violação do contrato que a própria
    `aposentar_fechadas` documenta: ela DEVOLVE as janelas fechadas porque
    quem chama precisa liquidar a exposição delas — e `atualizar` chamava
    descartando o retorno.

    A sequência quebrada: a descoberta termina logo depois de uma janela
    operada fechar, mas antes do próximo `tick`. A janela some do retrato, o
    motor não a encontra mais, `_liquidar` nunca roda, e a exposição
    sintética fica presa. Com cinco dessas o teto de posições recusa tudo
    pelo resto da rodada.
    """

    def _mercado(self, condition_id, fecha):
        return _mercado_falso(condition_id, {"Up": "up", "Down": "dn"}, fecha=fecha)

    #: Quanto falta para fechar, na janela que o teste abre.
    #:
    #: 100 s e não 300: com 300 a abertura cairia EXATAMENTE em `agora`
    #: (`abertura = fechamento − duração`), e o ISO com microssegundos decide
    #: por arredondamento se `abertura <= agora`. Medido: 109 descartes em
    #: 300 execuções. Um teste que falha em um terço das vezes por causa do
    #: relógio não testa nada — ensina a ignorar a suíte.
    FALTA_S = 100

    def test_atualizar_NAO_tira_janela_fechada_do_retrato(self):
        import time as _time

        from pulsearb.live.rastreador import RastreadorDeJanelas

        agora = _time.time()
        rastreador = RastreadorDeJanelas()
        rastreador.atualizar(
            [self._mercado("0xaa", agora + self.FALTA_S)], agora_epoch=agora
        )
        assert "0xaa" in rastreador.janelas

        # A janela fecha, e chega OUTRO ciclo de descoberta que não a traz.
        rastreador.atualizar([], agora_epoch=agora + 400)

        assert "0xaa" in rastreador.janelas, (
            "quem aposenta e quem liquida — o motor, no tick"
        )

    def test_quem_aposenta_devolve_para_o_motor_liquidar(self):
        """A aposentadoria continua existindo; ela é do motor."""
        import time as _time

        from pulsearb.live.rastreador import RastreadorDeJanelas

        agora = _time.time()
        rastreador = RastreadorDeJanelas()
        rastreador.atualizar(
            [self._mercado("0xaa", agora + self.FALTA_S)], agora_epoch=agora
        )

        saidas = rastreador.aposentar_fechadas(agora_epoch=agora + 400)

        assert [j.condition_id for j in saidas] == ["0xaa"]
        assert rastreador.janelas == {}


class TestOCaminhoDaCurva:
    """`--curva-de-variancia` vem de fora e não pode chegar cru ao disco.

    Mesma contenção do `--json` do backtest (M2.5), agora em
    `pulsearb.caminhos`: ler de fora da raiz não sobrescreve nada, mas põe o
    nome do arquivo no `origem` de cada linha do diário — e o SHADOW roda sob
    argumento montado por script e por agente, que é exatamente onde um
    caminho hostil entra sem ninguém digitar.
    """

    def test_sem_argumento_nao_ha_curva(self):
        assert _curvas(None) is None

    @pytest.mark.parametrize(
        "hostil",
        ["/etc/passwd.json", "../fora.json", "~/segredo.json", "sem-sufixo"],
    )
    def test_caminho_fora_da_raiz_e_recusado(self, hostil, tmp_path, monkeypatch):
        monkeypatch.setenv(ENV_RAIZ_DE_SAIDA, str(tmp_path))

        with pytest.raises(ValueError):
            _curvas(hostil)

    def test_curva_dentro_da_raiz_e_lida(self, tmp_path, monkeypatch):
        monkeypatch.setenv(ENV_RAIZ_DE_SAIDA, str(tmp_path))
        (tmp_path / "relatorios").mkdir()
        alvo = tmp_path / "relatorios" / "VARIANCIA.json"
        alvo.write_text(json.dumps(_relatorio_de_variancia()), encoding="utf-8")

        curvas = _curvas("relatorios/VARIANCIA.json")

        assert len(curvas)


class TestOCaminhoDoDiarioNaoVaiCRU:
    """Mesma classe do achado que o #53 fechou no `--curva-de-variancia`, e o
    `--diario` tinha ficado de fora — com ESCRITA em vez de leitura.

    `--diario /etc/cron.d/qualquer.jsonl` era travessia de caminho no processo
    que abre socket. O Sonar aponta isto como S2083.
    """

    @pytest.fixture(autouse=True)
    def _numa_pasta_temporaria(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("pulsearb.live.shadow.setup_logging", lambda: None)
        monkeypatch.setattr(
            "pulsearb.live.shadow.Settings.load",
            classmethod(lambda cls: _settings(tmp_path, modo=Mode.SHADOW)),
        )

    @pytest.mark.parametrize(
        "caminho",
        [
            "/etc/cron.d/qualquer.jsonl",
            "../fora-da-raiz.jsonl",
            "data/../../fora.jsonl",
        ],
    )
    def test_caminho_perigoso_vira_codigo_2_e_nao_traceback(
        self, caminho, capsys
    ):
        """Quem roda o SHADOW por script lê o stderr, não a pilha."""
        from pulsearb.live.shadow import main

        assert main(["--duration", "1s", "--diario", caminho]) == 2

        erro = capsys.readouterr().err
        assert "inválido" in erro or "fora da raiz" in erro

    def test_o_default_gerado_NAO_passa_pela_contencao(self):
        """Ele não vem de fora: é montado a partir de uma raiz literal. Passá-lo
        pela contenção só acoplaria o default à variável de ambiente da raiz."""
        from pulsearb.live.shadow import caminho_do_diario_da_rodada

        assert caminho_do_diario_da_rodada().startswith("data/shadow/diario-")


class TestOSensorDeVigilia:
    """A segunda consequência do 3.14, que ficou nove horas sem sensor.

    O 3.14 usou a divergência entre monotônico e parede para ENCERRAR a
    rodada na hora certa. Mas o mesmo congelamento tem outro efeito: os dois
    laços deste processo correm em tempo monotônico — decisão a cada 1 s,
    relato a cada 60 s —, então congelam JUNTOS. O relato sai sempre com
    +60 passos, e uma rodada suspensa fica indistinguível de uma saudável.

    Medido em 05/09/2026, rodada de 24 h na bateria: 1,16 h acordada em
    9,77 h de parede (**11,9%**), com `feeds_saudaveis: true` do começo ao
    fim — e certo, porque os feeds não têm defeito quando o processo inteiro
    está suspenso. O que faltava não era um alarme de feed: era comparar os
    dois relógios.
    """

    def test_maquina_que_dorme_derruba_o_ciclo_de_trabalho(self, tmp_path, monkeypatch):
        """O defeito de 05/09, reproduzido: parede anda, monotônico não."""
        processo = _processo(tmp_path, _CicloFalso())
        processo.inicio_mono = processo._relato_mono = 1_000.0
        processo.inicio_parede = processo._relato_parede = 50_000.0

        # 600 s de parede, 60 s de monotônico: dormiu 540 s (ciclo 0,1).
        monkeypatch.setattr("pulsearb.live.shadow.time.monotonic", lambda: 1_060.0)
        monkeypatch.setattr("pulsearb.live.shadow.time.time", lambda: 50_600.0)

        rodada = processo._vigilia()["da_rodada"]
        assert rodada["parede_s"] == 600.0
        assert rodada["acordado_s"] == 60.0
        assert rodada["dormiu_s"] == 540.0
        assert rodada["ciclo_de_trabalho"] == 0.1

    def test_maquina_acordada_da_ciclo_de_trabalho_cheio(self, tmp_path, monkeypatch):
        """O contraste que dá sentido ao número: na tomada, 1,0."""
        processo = _processo(tmp_path, _CicloFalso())
        processo.inicio_mono = processo._relato_mono = 1_000.0
        processo.inicio_parede = processo._relato_parede = 50_000.0

        monkeypatch.setattr("pulsearb.live.shadow.time.monotonic", lambda: 1_600.0)
        monkeypatch.setattr("pulsearb.live.shadow.time.time", lambda: 50_600.0)

        rodada = processo._vigilia()["da_rodada"]
        assert rodada["dormiu_s"] == 0.0
        assert rodada["ciclo_de_trabalho"] == 1.0

    def test_a_JANELA_denuncia_a_parada_que_o_acumulado_dilui(
        self, tmp_path, monkeypatch
    ):
        """A razão de existirem duas faixas, e não só o acumulado.

        Nove horas de rodada boa seguidas de uma parada nova dão acumulado
        ainda alto — foi exatamente assim que a rodada de 05/09 pareceu
        saudável enquanto degradava. `desde_o_relato` é quem grita na hora.
        """
        processo = _processo(tmp_path, _CicloFalso())
        # 10 h de parede já decorridas, quase todas acordado.
        processo.inicio_mono = 1_000.0
        processo.inicio_parede = 50_000.0
        # A janela do último relato começou há pouco.
        processo._relato_mono = 36_000.0
        processo._relato_parede = 85_000.0

        # +60 s de parede na janela, mas só 1 s de monotônico: parou agora.
        monkeypatch.setattr("pulsearb.live.shadow.time.monotonic", lambda: 36_001.0)
        monkeypatch.setattr("pulsearb.live.shadow.time.time", lambda: 85_060.0)

        vigilia = processo._vigilia()
        assert vigilia["da_rodada"]["ciclo_de_trabalho"] > 0.9
        assert vigilia["desde_o_relato"]["ciclo_de_trabalho"] < 0.02

    def test_relogio_de_parede_para_TRAS_nao_inventa_ciclo_acima_de_um(
        self, tmp_path, monkeypatch
    ):
        """NTP corrigindo para trás não pode virar 'acordado mais que o tempo'.

        É a mesma incógnita do 3.10: o relógio de parede não é confiável. Um
        `ciclo_de_trabalho` de 1,4 seria pior que inútil — ninguém sabe ler.
        """
        processo = _processo(tmp_path, _CicloFalso())
        processo.inicio_mono = processo._relato_mono = 1_000.0
        processo.inicio_parede = processo._relato_parede = 50_000.0

        # Monotônico andou 600 s; a parede, só 100 s (NTP puxou para trás).
        monkeypatch.setattr("pulsearb.live.shadow.time.monotonic", lambda: 1_600.0)
        monkeypatch.setattr("pulsearb.live.shadow.time.time", lambda: 50_100.0)

        rodada = processo._vigilia()["da_rodada"]
        assert rodada["dormiu_s"] == 0.0
        assert rodada["ciclo_de_trabalho"] == 1.0

    def test_so_o_laco_de_relato_fecha_a_janela(self, tmp_path, monkeypatch):
        """Ler o estado por fora não pode zerar a medida de quem mede.

        O resumo final chama `estado()`, e um teste ou um inspetor também
        podem. Se qualquer leitura avançasse a janela, o próximo relato
        mediria um intervalo que não existiu — e mediria 1,0, porque acabou
        de começar. O sensor mentiria justamente quando consultado.
        """
        processo = _processo(tmp_path, _CicloFalso())
        processo.inicio_mono = processo._relato_mono = 1_000.0
        processo.inicio_parede = processo._relato_parede = 50_000.0

        monkeypatch.setattr("pulsearb.live.shadow.time.monotonic", lambda: 1_060.0)
        monkeypatch.setattr("pulsearb.live.shadow.time.time", lambda: 50_600.0)

        processo._vigilia()
        processo.estado()
        assert processo._relato_mono == 1_000.0
        assert processo._relato_parede == 50_000.0

        processo.estado(avancar_vigilia=True)
        assert processo._relato_mono == 1_060.0
        assert processo._relato_parede == 50_600.0

    def test_a_vigilia_sai_no_estado_SEMPRE(self, tmp_path):
        """Campo que só aparece quando há problema é campo que ninguém procura.

        Mesma regra que o `falhou` já segue neste resumo.
        """
        estado = _processo(tmp_path, _CicloFalso()).estado()
        assert "vigilia" in estado
        for faixa in ("da_rodada", "desde_o_relato"):
            assert set(estado["vigilia"][faixa]) == {
                "parede_s",
                "acordado_s",
                "dormiu_s",
                "ciclo_de_trabalho",
            }
