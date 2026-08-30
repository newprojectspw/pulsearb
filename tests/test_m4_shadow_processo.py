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

        processo = _processo(tmp_path, _CicloFalso())
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

        assinados = set(processo.rtds_feeds[0].assets)
        assert assinados == {"btc", "eth"}
        assert "sol" not in assinados
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
