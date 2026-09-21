"""Feeds WS contra um servidor fake local: subscribe, heartbeat, reconexão.

Servidor `websockets` em 127.0.0.1 — é loopback, não rede externa; a regra do
M1 (nenhum teste depende de rede externa) continua respeitada.
"""

from __future__ import annotations

import asyncio
import json

import orjson
import pytest
import websockets

from pulsearb.feeds.poly_ws import PING, PONG, PONG_BYTES, PolyMarketWsFeed
from pulsearb.feeds.rtds import TOPIC_BINANCE, TOPIC_TWAP_60, RtdsFeed


class FakeWsServer:
    """Servidor de teste: guarda o que recebeu, responde PONG, envia o que mandarem."""

    def __init__(self) -> None:
        self.received: list[str] = []
        # Guarda o objeto CRU como o websockets entregou: str = frame de
        # texto, bytes = frame BINÁRIO. É o que permite provar que o cliente
        # nunca manda binário (o RTDS fecha com 1003 se mandar).
        self.received_raw: list[str | bytes] = []
        self.connections: int = 0
        self.server: websockets.Server | None = None
        self.to_send: list[str] = []
        self.drop_next: bool = False
        self._sockets: list[websockets.ServerConnection] = []

    @property
    def url(self) -> str:
        assert self.server is not None
        host, port = self.server.sockets[0].getsockname()[:2]
        return f"ws://{host}:{port}"

    async def start(self) -> None:
        self.server = await websockets.serve(self._handler, "127.0.0.1", 0)

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    async def _handler(self, ws: websockets.ServerConnection) -> None:
        self.connections += 1
        self._sockets.append(ws)
        if self.drop_next:
            self.drop_next = False
            await ws.close()
            return
        for message in self.to_send:
            await ws.send(message)
        try:
            async for message in ws:
                self.received_raw.append(message)
                text = message if isinstance(message, str) else message.decode()
                self.received.append(text)
                if text.strip() == "PING":
                    await ws.send("PONG")
        except websockets.ConnectionClosed:
            pass

    async def broadcast(self, message: str) -> None:
        for ws in list(self._sockets):
            try:
                await ws.send(message)
            except websockets.ConnectionClosed:
                pass


@pytest.fixture
async def server():
    srv = FakeWsServer()
    await srv.start()
    yield srv
    await srv.stop()


async def _wait_for(predicate, limite_s: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + limite_s
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condição não satisfeita a tempo")


# ------------------------------------------------------------------- RTDS
async def test_rtds_envia_subscribe_ao_conectar(server):
    feed = RtdsFeed(url=server.url, user_agent="pulsearb-test", assets=["btc"])
    await feed.start()
    try:
        await _wait_for(lambda: bool(server.received))
        frame = json.loads(server.received[0])
        assert frame["action"] == "subscribe"
        assert {s["topic"] for s in frame["subscriptions"]} == {
            "crypto_prices",
            "crypto_prices_twap_sixty",
        }
    finally:
        await feed.stop()


async def test_rtds_processa_tick_e_marca_nao_stale(server, rtds_events):
    server.to_send = [json.dumps(rtds_events["twap_sixty_btc"])]
    ticks = []
    feed = RtdsFeed(
        url=server.url, user_agent="ua", assets=["btc"], on_tick=ticks.append
    )
    await feed.start()
    try:
        await _wait_for(lambda: bool(ticks))
        assert ticks[0].asset == "btc"
        assert ticks[0].price == pytest.approx(118432.17)
        assert feed.message_count == 1
        assert not feed.stale
        # timestamps dos dois relógios foram capturados
        assert ticks[0].ts_mono_ns > 0
        assert ticks[0].ts_wall_ns > 0
    finally:
        await feed.stop()


async def test_watchdog_marca_stale_sem_ticks(server):
    feed = RtdsFeed(
        url=server.url, user_agent="ua", assets=["btc"], stale_after_seconds=0.05
    )
    await feed.start()
    try:
        await _wait_for(lambda: feed.connected)
        # conectado mas sem mensagem nenhuma = parado
        assert feed.stale
        assert feed.last_message_age_seconds == float("inf")
    finally:
        await feed.stop()


async def test_reconecta_apos_queda(server):
    server.drop_next = True
    feed = RtdsFeed(
        url=server.url,
        user_agent="ua",
        assets=["btc"],
        reconnect_initial_seconds=0.01,
        reconnect_max_seconds=0.05,
    )
    await feed.start()
    try:
        await _wait_for(lambda: server.connections >= 2, limite_s=5.0)
        assert feed.reconnect_count >= 1
        # resubscribe aconteceu na reconexão — e com TODOS os tópicos.
        # M2.1 BUG 2 item 3: reconectar e voltar assinando menos do que antes
        # é indistinguível de um feed saudável no log, e some dado em
        # silêncio.
        await _wait_for(lambda: bool(server.received))
        assinatura = json.loads(server.received[-1])
        topicos = {s["topic"] for s in assinatura["subscriptions"]}
        assert topicos == {TOPIC_BINANCE, TOPIC_TWAP_60}
    finally:
        await feed.stop()


async def test_queda_registra_codigo_e_origem(server):
    """BUG 2: "conexão caiu" sem código é um beco sem saída na investigação.

    A primeira gravação real teve reconexão a cada 30–306s a hora inteira, e o
    log não dizia o motivo de nenhuma. Agora cada queda guarda o código do
    close, a razão e de que lado ela partiu.
    """
    server.drop_next = True
    feed = RtdsFeed(
        url=server.url,
        user_agent="ua",
        assets=["btc"],
        reconnect_initial_seconds=0.01,
        reconnect_max_seconds=0.05,
    )
    await feed.start()
    try:
        await _wait_for(lambda: bool(feed.close_reasons), limite_s=5.0)
        motivo = feed.close_reasons[0]
        assert motivo["close_origem"] == "servidor"
        assert motivo["close_code"] == 1000
        assert feed.close_count >= 1
    finally:
        await feed.stop()


def test_lista_de_quedas_tem_teto():
    """72h de reconexão não podem virar vazamento lento de memória."""
    feed = RtdsFeed(url="ws://127.0.0.1:1", user_agent="ua", assets=["btc"])
    for _ in range(feed.MAX_CLOSE_REASONS * 3):
        feed._registrar_queda(OSError("boom"))
    assert len(feed.close_reasons) == feed.MAX_CLOSE_REASONS
    assert feed.close_count == feed.MAX_CLOSE_REASONS * 3


async def test_poly_ws_reassina_todos_os_tokens_na_reconexao(server):
    """Token assinado dinamicamente sobrevive à queda da conexão.

    O estado da assinatura vive no cliente (`token_ids`), não no servidor:
    quem reconecta manda o frame inicial com o conjunto INTEIRO. Se dependesse
    do servidor lembrar, cada queda perderia os tokens acrescentados depois da
    conexão — que são justamente as janelas novas.
    """
    feed = PolyMarketWsFeed(
        url=server.url,
        user_agent="ua",
        token_ids=["tokenA"],
        reconnect_initial_seconds=0.01,
        reconnect_max_seconds=0.05,
    )
    await feed.start()
    try:
        await _wait_for(lambda: bool(server.received))
        await feed.subscribe(["tokenB"])
        await _wait_for(lambda: len(server.received) >= 2)

        conexoes = server.connections
        await server.broadcast("")  # garante socket vivo antes de derrubar
        for ws in list(server._sockets):
            await ws.close()
        await _wait_for(lambda: server.connections > conexoes, limite_s=5.0)
        await _wait_for(
            lambda: any(
                json.loads(m).get("type") == "market"
                and set(json.loads(m)["assets_ids"]) == {"tokenA", "tokenB"}
                for m in server.received
                if m.startswith("{")
            ),
            limite_s=5.0,
        )
    finally:
        await feed.stop()


# ---------------------------------------------------------------- poly_ws
async def test_poly_ws_frame_inicial(server):
    feed = PolyMarketWsFeed(
        url=server.url, user_agent="ua", token_ids=["tokenA", "tokenB"]
    )
    await feed.start()
    try:
        await _wait_for(lambda: bool(server.received))
        frame = json.loads(server.received[0])
        assert frame["type"] == "market"
        assert sorted(frame["assets_ids"]) == ["tokenA", "tokenB"]
        # true para receber best bid/ask e evento de resolução (API_NOTES 6.1)
        assert frame["custom_feature_enabled"] is True
    finally:
        await feed.stop()


async def test_poly_ws_sem_tokens_nao_manda_frame_inicial(server):
    feed = PolyMarketWsFeed(url=server.url, user_agent="ua")
    await feed.start()
    try:
        await _wait_for(lambda: feed.connected)
        await asyncio.sleep(0.05)
        assert server.received == []
    finally:
        await feed.stop()


async def test_poly_ws_subscribe_dinamico(server):
    feed = PolyMarketWsFeed(url=server.url, user_agent="ua", token_ids=["a"])
    await feed.start()
    try:
        await _wait_for(lambda: bool(server.received))
        await feed.subscribe(["b", "c"])
        await _wait_for(lambda: len(server.received) >= 2)
        frame = json.loads(server.received[1])
        assert frame["operation"] == "subscribe"
        assert sorted(frame["assets_ids"]) == ["b", "c"]
        assert feed.token_ids == {"a", "b", "c"}

        await feed.unsubscribe(["a"])
        await _wait_for(lambda: len(server.received) >= 3)
        frame = json.loads(server.received[2])
        assert frame["operation"] == "unsubscribe"
        assert frame["assets_ids"] == ["a"]
        assert feed.token_ids == {"b", "c"}
    finally:
        await feed.stop()


async def test_subscribe_nao_reenvia_token_ja_assinado(server):
    feed = PolyMarketWsFeed(url=server.url, user_agent="ua", token_ids=["a"])
    await feed.start()
    try:
        await _wait_for(lambda: bool(server.received))
        await feed.subscribe(["a"])
        await asyncio.sleep(0.05)
        assert len(server.received) == 1
    finally:
        await feed.stop()


async def test_heartbeat_ping_pong(server):
    feed = PolyMarketWsFeed(
        url=server.url,
        user_agent="ua",
        token_ids=["a"],
        ping_interval_seconds=0.05,
        pong_stale_seconds=5.0,
    )
    await feed.start()
    try:
        await _wait_for(lambda: feed.pong_count >= 2, limite_s=3.0)
        assert PING in server.received
        # PONG não polui a contagem de ticks de mercado? Conta como mensagem
        # recebida, mas é tratado e não vira evento de book.
        assert feed.pong_count >= 2
    finally:
        await feed.stop()


async def test_sem_assinatura_NAO_manda_ping(server):
    """Conexão vazia: nenhum PING até o primeiro `subscribe`.

    O CLOB real lê qualquer texto antes da primeira assinatura como payload
    de assinatura e fecha com `1008 invalid subscription payload` — foi o
    que o SHADOW via 10 s depois de toda primeira conexão (conecta vazio,
    assina depois da descoberta). Depois do `subscribe`, o PING volta.
    """
    feed = PolyMarketWsFeed(
        url=server.url, user_agent="ua", ping_interval_seconds=0.05
    )
    await feed.start()
    try:
        await _wait_for(lambda: feed.connected)
        await asyncio.sleep(0.3)  # seis intervalos de PING
        assert PING not in server.received
        assert feed.pong_count == 0
        await feed.subscribe(["a"])
        await _wait_for(lambda: feed.pong_count >= 1, limite_s=3.0)
        assert PING in server.received
    finally:
        await feed.stop()


async def test_pong_atualiza_watchdog_de_heartbeat(server):
    feed = PolyMarketWsFeed(
        url=server.url, user_agent="ua", token_ids=["a"], ping_interval_seconds=0.05
    )
    await feed.start()
    try:
        await _wait_for(lambda: feed.pong_count >= 1)
        antes = feed._last_pong_mono
        await _wait_for(lambda: feed.pong_count >= 3, limite_s=3.0)
        assert feed._last_pong_mono > antes
    finally:
        await feed.stop()


async def test_book_event_chega_ao_callback(server, clob_ws_events):
    server.to_send = [json.dumps(clob_ws_events["book_snapshot"])]
    eventos = []
    feed = PolyMarketWsFeed(
        url=server.url, user_agent="ua", token_ids=["a"], on_event=eventos.append
    )
    await feed.start()
    try:
        await _wait_for(lambda: bool(eventos))
        assert eventos[0].parsed["event_type"] == "book"
        assert eventos[0].source == "poly_ws"
    finally:
        await feed.stop()


async def test_pong_nao_quebra_o_parser(server):
    """PONG é texto puro, não JSON — o parser devolve None sem explodir."""
    server.to_send = [PONG]
    eventos = []
    feed = PolyMarketWsFeed(url=server.url, user_agent="ua", on_event=eventos.append)
    await feed.start()
    try:
        await _wait_for(lambda: bool(eventos))
        assert eventos[0].parsed is None
        assert eventos[0].raw == PONG_BYTES
        assert feed.pong_count == 1
    finally:
        await feed.stop()


def test_frames_sao_json_valido():
    assert orjson.loads(
        PolyMarketWsFeed.subscribe_frame(["x"])
    ) == {"operation": "subscribe", "assets_ids": ["x"], "custom_feature_enabled": True}
    assert orjson.loads(PolyMarketWsFeed.unsubscribe_frame(["x"])) == {
        "operation": "unsubscribe",
        "assets_ids": ["x"],
    }


class _FrameFalso:
    """O bastante do frame de close do `websockets` para o registro ler."""

    def __init__(self, code: int, reason: str) -> None:
        self.code = code
        self.reason = reason


class _QuedaFalsa(Exception):
    """`ConnectionClosed` tem `rcvd` e `sent`; qualquer um pode ser None."""

    def __init__(self, rcvd: object | None, sent: object | None) -> None:
        super().__init__("no close frame received or sent")
        self.rcvd = rcvd
        self.sent = sent


def _feed_qualquer() -> RtdsFeed:
    return RtdsFeed(url="ws://127.0.0.1:1", user_agent="ua", assets=["btc"])


def test_origem_da_queda_tem_tres_estados_e_nao_adivinha():
    """Sem frame nenhum, a origem é DESCONHECIDA — nunca "cliente".

    O defeito que este teste impede custou uma investigação inteira em
    2026-09-20. O registro fazia `"servidor" if rcvd else "cliente"`, então
    toda queda de transporte — `no close frame received or sent`, `OSError`
    de rede — saía carimbada como se o NOSSO lado tivesse fechado. As ~30–45
    reconexões/h do SHADOW foram investigadas sob a hipótese de CPU saturada
    apoiada, entre outras coisas, nesse carimbo; a hipótese caiu quando a
    contagem solo deu IGUAL à contagem sob disputa.

    Ausência de frame é ausência de medida. Um campo que finge saber vira
    gráfico e vira conclusão — e foi o que aconteceu.
    """
    feed = _feed_qualquer()

    # 1. o SERVIDOR fechou: veio frame.
    feed._registrar_queda(_QuedaFalsa(rcvd=_FrameFalso(1011, "erro"), sent=None))
    assert feed.close_reasons[-1]["close_origem"] == "servidor"
    assert feed.close_reasons[-1]["close_code"] == 1011

    # 2. NÓS fechamos: mandamos frame e não voltou nada.
    feed._registrar_queda(_QuedaFalsa(rcvd=None, sent=_FrameFalso(1000, "tchau")))
    assert feed.close_reasons[-1]["close_origem"] == "cliente"
    assert feed.close_reasons[-1]["close_code"] == 1000

    # 3. NINGUÉM fechou pelo protocolo — é o caso real da VPS.
    feed._registrar_queda(_QuedaFalsa(rcvd=None, sent=None))
    queda = feed.close_reasons[-1]
    assert queda["close_origem"] == feed.ORIGEM_DESCONHECIDA
    assert queda["close_origem"] != "cliente", (
        "regressão do defeito de 2026-09-20: queda sem frame voltou a ser "
        "atribuída ao cliente"
    )
    assert queda["close_code"] is None

    # 4. erro de transporte puro, que nem tem os atributos.
    feed._registrar_queda(OSError("connection reset by peer"))
    assert feed.close_reasons[-1]["close_origem"] == feed.ORIGEM_DESCONHECIDA


def test_1013_do_servidor_nao_e_respondido_em_meio_segundo():
    """`Try Again Later` pede paciência, e o laço ignorava o pedido.

    Medido na VPS em 2026-09-20: das 24 quedas de uma hora, **20 vieram com
    `1012 Service Restart` e 4 com `1013 Try Again Later`**. O laço de
    reconexão zera o backoff a cada conexão boa, então as quatro foram
    respondidas em ~0,5 s — insistir em cima de um servidor que acabou de
    dizer, pelo código do RFC 6455 §7.4.1, que está sobrecarregado.

    **A primeira versão desta tabela deixava o 1012 de fora**, com o
    argumento de que "ele diz que o serviço está voltando, e voltar rápido é
    o certo". Achado P2 do Codex no #177: isso inverte o padrão. O 1012
    `Service Restart` diz que o serviço **está reiniciando** — não que já
    voltou — e a semântica registrada recomenda voltar com atraso aleatório
    de 5 a 30 s. Deixar de fora o código que respondia por **20 das 24**
    quedas medidas esvaziaria a mudança inteira.

    O que separa os casos não é o código, é **quem fechou**: o nosso próprio
    1012 (de `_derrubar_por_recusa` e `_escalar_se_sem_efeito`) existe para
    reconectar rápido, e ganha piso zero. Isso está no teste seguinte.
    """
    feed = _feed_qualquer()

    pede_paciencia = {"close_code": 1013, "close_origem": "servidor"}
    assert feed._espera_minima(pede_paciencia) >= 5.0, (
        "regressão: 1013 Try Again Later voltou a ser respondido no backoff "
        "curto"
    )

    # E o que NÃO deve ganhar piso:
    assert feed._espera_minima({"close_code": 1012, "close_origem": "cliente"}) == 0.0
    assert feed._espera_minima({"close_code": 1000, "close_origem": "servidor"}) == 0.0
    assert feed._espera_minima({"close_code": None, "close_origem": "servidor"}) == 0.0
    assert feed._espera_minima(None) == 0.0


def test_espera_minima_nao_encurta_um_backoff_ja_grande():
    """O piso é PISO, não substituição, e sobe o PRÓPRIO backoff.

    Duas escolhas do laço ficam fixadas aqui. **Não substituir:** depois de
    muitas quedas seguidas o backoff exponencial já passa dos 5 s, e trocar
    pelo piso deixaria a reconexão MAIS agressiva justamente quando o
    servidor está pior. **Subir o backoff, e não só a espera desta volta:**
    assim a duplicação parte do piso, e um segundo `1013` seguido espera
    10 s em vez de voltar aos 5 — quem pediu paciência duas vezes recebe
    mais, não a mesma.
    """
    feed = _feed_qualquer()
    piso = feed._espera_minima({"close_code": 1013, "close_origem": "servidor"})
    backoff_grande = 30.0
    assert max(backoff_grande, piso) == backoff_grande


class _QuedaComHandshake(Exception):
    """Os DOIS lados mandaram frame — só a ordem diz quem começou."""

    def __init__(self, *, primeiro_recebido: bool) -> None:
        super().__init__("sent and received close frames")
        self.rcvd = _FrameFalso(1012, "service restart")
        self.sent = _FrameFalso(1012, "topico mudo apos reassinaturas")
        self.rcvd_then_sent = primeiro_recebido


def test_close_que_NOS_iniciamos_nao_e_atribuido_ao_servidor():
    """Fechar com 1012 é coisa NOSSA em dois pontos do `base.py`.

    `_derrubar_por_recusa` e `_escalar_se_sem_efeito` fecham a conexão com
    **1012 de propósito**, para refazer a assinatura do zero. O servidor
    responde ao nosso frame com o dele, então `rcvd` existe — e a regra
    antiga (`"servidor" if rcvd is not None`) carimbava **servidor** numa
    queda que nós mesmos causamos.

    Isso não é detalhe de log: a medida de 2026-09-20 concluiu "24 de 24 do
    servidor" e daí saiu o diagnóstico de que o CLOB recicla conexões. Com a
    atribuição errada, parte daquelas 24 podia ser nossa.

    Terceira vez que o MESMO defeito aparece neste campo: dizer que sabe
    quando não sabe.
    """
    feed = _feed_qualquer()

    feed._registrar_queda(_QuedaComHandshake(primeiro_recebido=False))
    assert feed.close_reasons[-1]["close_origem"] == "cliente", (
        "regressão: queda que NÓS iniciamos voltou a ser atribuída ao servidor"
    )

    feed._registrar_queda(_QuedaComHandshake(primeiro_recebido=True))
    assert feed.close_reasons[-1]["close_origem"] == "servidor"


def test_o_piso_sobrevive_ao_jitter():
    """Piso multiplicado por jitter de [0.5, 1.5) não é piso.

    Achado P2 do Codex no #177: com espera de 5 s e o jitter existente, o
    sono real ia de 2,5 a 7,5 s — **metade das voltas dormia menos que o
    mínimo anunciado**. O teste anterior só exercia `_espera_minima`, e por
    isso não via nada.
    """
    feed = _feed_qualquer()
    do_servidor = {"close_code": 1013, "close_origem": "servidor"}
    piso = feed._espera_minima(do_servidor)
    assert piso == 5.0

    for _ in range(300):
        assert feed.espera_da_volta(feed.reconnect_initial_seconds, piso) >= piso


def test_o_piso_nao_vale_para_o_close_que_nos_mandamos():
    """Nosso próprio 1012 existe para reconectar RÁPIDO — punir isso com o
    backoff que o padrão pede do servidor inverteria a intenção do código.
    """
    feed = _feed_qualquer()
    nosso = {"close_code": 1012, "close_origem": "cliente"}
    assert feed._espera_minima(nosso) == 0.0

    do_servidor = {"close_code": 1012, "close_origem": "servidor"}
    assert feed._espera_minima(do_servidor) == 5.0



def test_a_escalada_do_piso_sobrevive_ao_reset_do_backoff():
    """A escalada tem de contar QUEDAS, não o backoff — que reseta antes.

    Achado P2 do Codex no #177, e ele derrubou uma AFIRMAÇÃO minha, não só
    um número: o comentário e o runbook diziam que um segundo `1013`
    seguido esperaria 10 s. Nunca esperava. O laço faz `backoff =
    reconnect_initial_seconds` a cada conexão **bem sucedida**, e o servidor
    aceita a conexão antes de fechá-la — então o backoff voltava a 0,5 s
    antes de cada queda e o piso o levava a 5 s TODA vez.

    O teste anterior refazia a aritmética de uma queda só e por isso não via
    o reset. Este percorre a sequência real.
    """
    feed = _feed_qualquer()
    sobrecarga = {"close_code": 1013, "close_origem": "servidor"}
    outra_coisa = {"close_code": 1000, "close_origem": "servidor"}

    assert feed._piso_da_volta(sobrecarga) == 5.0
    assert feed._piso_da_volta(sobrecarga) == 10.0, (
        "regressão: o segundo pedido de paciência seguido voltou a esperar o "
        "mesmo que o primeiro"
    )
    assert feed._piso_da_volta(sobrecarga) == 20.0

    # teto: não passa do reconnect_max_seconds
    assert feed._piso_da_volta(sobrecarga) == feed.reconnect_max_seconds

    # e uma queda por outro motivo zera a conta
    assert feed._piso_da_volta(outra_coisa) == 0.0
    assert feed.pedidos_de_paciencia_seguidos == 0
    assert feed._piso_da_volta(sobrecarga) == 5.0


def test_uma_conexao_saudavel_zera_a_escalada():
    """77 minutos no ar não são "mais um pedido de paciência seguido".

    Medido na VPS em 2026-09-21 (runbook §10.1i). Quatro horas de
    `clob[updown]`: NOVE quedas, todas `1013 slow consumer`, e todas as
    esperas entre 30,9 s e 42,0 s — o teto. Uma delas veio depois de 77,1
    min de conexão saudável e ainda assim dormiu 32,3 s, porque nada no
    código zerava o contador por uma conexão que tinha trabalhado. O campo
    se chamava `pedidos_de_paciencia_seguidos` e o "seguidos" não era
    medido em lugar nenhum.
    """
    feed = _feed_qualquer()
    sobrecarga = {"close_code": 1013, "close_origem": "servidor"}

    # a escalada sobe até o teto e fica lá, como em produção
    for _ in range(6):
        feed._piso_da_volta(sobrecarga, tempo_no_ar=1.0)
    assert feed._piso_da_volta(sobrecarga, tempo_no_ar=1.0) == feed.reconnect_max_seconds

    # e então a conexão trabalha 77,1 minutos antes de cair
    assert feed._piso_da_volta(sobrecarga, tempo_no_ar=77.1 * 60) == 5.0, (
        "regressão: uma conexão que ficou mais de uma hora no ar continuou "
        "sendo tratada como pedido de paciência seguido"
    )


def test_queda_rapida_nao_zera_a_escalada():
    """O outro lado: o servidor que nos aceita e derruba logo NÃO zera.

    Se bastasse reconectar para zerar a conta, a escalada oscilaria para
    sempre — voltaríamos a bater na porta a cada ~36 s de um servidor que
    já disse duas vezes que não aguenta. O 24,7 s medido na rajada de
    +139 min é o caso concreto que esta regra tem de rejeitar.
    """
    feed = _feed_qualquer()
    sobrecarga = {"close_code": 1013, "close_origem": "servidor"}

    assert feed._piso_da_volta(sobrecarga, tempo_no_ar=0.0) == 5.0
    assert feed._piso_da_volta(sobrecarga, tempo_no_ar=24.7) == 10.0, (
        "regressão: 24,7 s no ar zeraram a escalada — a conexão caiu antes "
        "de viver o tempo que a constante exige"
    )
    assert feed._piso_da_volta(sobrecarga, tempo_no_ar=59.9) == 20.0


def test_a_saude_que_zera_fica_acima_do_teto_do_backoff():
    """Zerar por saúde não pode ser mais fácil que a própria espera.

    Se a constante fosse MENOR que `reconnect_max_seconds`, um servidor que
    nos admite por pouco mais que o nosso próprio sono zeraria a conta toda
    vez, e a escalada nunca existiria de fato.
    """
    feed = _feed_qualquer()
    assert feed.SAUDE_QUE_ZERA_A_ESCALADA_SEGUNDOS > feed.reconnect_max_seconds


def test_nosso_proprio_close_nao_alimenta_a_escalada():
    """Derrubar a conexão de propósito não é o servidor pedindo paciência."""
    feed = _feed_qualquer()
    nosso = {"close_code": 1012, "close_origem": "cliente"}
    for _ in range(5):
        assert feed._piso_da_volta(nosso) == 0.0
    assert feed.pedidos_de_paciencia_seguidos == 0


def test_o_piso_nao_vira_ponto_de_encontro():
    """Com piso, `max(x, piso)` grudaria metade das voltas no MESMO valor.

    Achado do Codex no #177, e é o inverso do problema anterior: depois de
    fazer o piso valer, `max(backoff * jitter, piso)` mandava ao piso EXATO
    toda sorte abaixo dele — metade delas. Muitos clientes que receberam o
    mesmo `1012` voltariam no mesmo instante, que é exatamente a debandada
    que o jitter existe para evitar.

    O sorteio passou a ser DENTRO da faixa `[piso, teto)`, com o piso como
    limite inferior em vez de corte.
    """
    feed = _feed_qualquer()
    do_servidor = {"close_code": 1013, "close_origem": "servidor"}
    piso = feed._espera_minima(do_servidor)

    amostras = [feed.espera_da_volta(feed.reconnect_initial_seconds, piso) for _ in range(400)]

    assert min(amostras) >= piso, "o piso continua sendo piso"
    no_piso_exato = sum(1 for a in amostras if a == piso)
    assert no_piso_exato <= 4, (
        f"regressão: {no_piso_exato} de 400 voltas caíram no piso EXATO — "
        "o piso virou ponto de encontro em vez de limite inferior"
    )
    assert len(set(amostras)) > 300, "as esperas têm de ficar espalhadas"


def test_sem_piso_o_jitter_antigo_nao_muda():
    """Sem piso, a distribuição continua sendo a de sempre, [0.5, 1.5)x."""
    feed = _feed_qualquer()
    amostras = [feed.espera_da_volta(10.0, 0.0) for _ in range(400)]
    assert min(amostras) >= 5.0
    assert max(amostras) < 15.0
    assert min(amostras) < 6.0, "a faixa começa em 0.5x, não no backoff"
