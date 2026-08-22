"""Base comum dos feeds WS: reconexão com backoff+jitter, watchdog, timestamps.

Regras do hot path aplicadas aqui:
- timestamp MONOTÔNICO de chegada em cada mensagem (time.monotonic_ns());
  time.time_ns() é capturado junto, mas só para registro/gravação
- reconexão com backoff exponencial + jitter, sem teto de tentativas
- watchdog: feed sem mensagem por mais de `stale_after_seconds` marca o feed
  como parado (quem consome decide zerar posição-alvo e pausar entradas)
- nenhum I/O de disco síncrono aqui; consumo via callback ou fila
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import websockets

from pulsearb.obs import get_logger


class SilencioDeDados(RuntimeError):
    """Conexão aberta que parou de publicar (M2.7).

    Exceção própria, e não `TimeoutError` genérico, para que o motivo da
    queda saia nomeado em `close_reasons` — "caiu" sem causa foi o beco sem
    saída que o M2.1 já tinha custado uma investigação inteira.
    """


@dataclass(frozen=True, slots=True)
class FeedEvent:
    """Uma mensagem de feed com os dois relógios capturados na chegada."""

    source: str          # ex.: "rtds", "poly_ws"
    ts_mono_ns: int      # relógio monotônico — medição de latência
    ts_wall_ns: int      # relógio de parede — só registro/gravação
    raw: bytes           # payload cru, como veio do fio
    parsed: Any          # dict do orjson (ou None se não-JSON, ex.: "PONG")


OnEvent = Callable[[FeedEvent], Awaitable[None] | None]


class ReconnectingFeed:
    """Loop de conexão WS com resubscribe automático.

    Subclasses implementam:
      - `_on_connected(ws)`: envia frames de subscribe
      - `_handle_message(event)`: processa uma mensagem (já com FeedEvent)
    """

    #: Quantos motivos de queda guardar. `close_count` conta todas.
    #: De quanto em quanto tempo o laço de reassinatura acorda para
    #: verificar se algum tópico emudeceu. Precisa ser bem menor que o
    #: limiar de tópico mudo, para que a reação não custe um passo
    #: inteiro além do limiar.
    PASSO_DE_VERIFICACAO_S = 5.0

    MAX_CLOSE_REASONS = 200

    def __init__(
        self,
        *,
        name: str,
        url: str,
        user_agent: str,
        stale_after_seconds: float = 2.0,
        reconnect_initial_seconds: float = 0.5,
        reconnect_max_seconds: float = 30.0,
        ws_ping_interval: float | None = 20.0,
        ws_ping_timeout: float | None = 20.0,
        # M2.7: watchdog por AUSÊNCIA DE DADOS. `None` = desligado, que é o
        # comportamento até o M2.6 — quem quer o watchdog pede por ele.
        sem_dados_timeout_s: float | None = None,
        # M2.7: reassinatura periódica. `None` = desligada.
        reassinatura_intervalo_s: float | None = None,
        # M2.11: ESCALADA. Depois de N reassinaturas urgentes seguidas sem o
        # tópico voltar, derruba o socket e deixa o laço de reconexão agir.
        # `None` = desligada (comportamento do M2.7).
        reassinaturas_ate_derrubar: int | None = None,
        # M2.11: rótulo da conexão nos logs. NÃO entra em `name`, que vira
        # `fonte` na gravação — mudar `name` renomearia o campo no disco e
        # quebraria o leitor.
        rotulo: str | None = None,
        on_event: OnEvent | None = None,
    ) -> None:
        self.name = name
        self.url = url
        self.user_agent = user_agent
        self.stale_after_seconds = stale_after_seconds
        self.reconnect_initial_seconds = reconnect_initial_seconds
        self.reconnect_max_seconds = reconnect_max_seconds
        self.ws_ping_interval = ws_ping_interval
        self.ws_ping_timeout = ws_ping_timeout
        self.sem_dados_timeout_s = sem_dados_timeout_s
        self.reassinatura_intervalo_s = reassinatura_intervalo_s
        self.reassinaturas_ate_derrubar = reassinaturas_ate_derrubar
        self.rotulo = rotulo or name
        self.on_event = on_event
        self.log = get_logger(f"pulsearb.feeds.{name}")

        self._task: asyncio.Task[None] | None = None
        self._ws: websockets.ClientConnection | None = None
        self._stopped = asyncio.Event()
        self._last_msg_mono_ns: int = 0
        self._connected = False
        self.reconnect_count = 0
        self.message_count = 0
        # Motivo de cada queda: sem isto, "conexão caiu" é um beco sem saída
        # na investigação. Limitado às últimas MAX_CLOSE_REASONS — numa
        # gravação de 72h uma lista sem teto seria um vazamento lento, e o
        # padrão de queda aparece nas últimas dezenas tanto quanto em todas.
        self.close_reasons: list[dict[str, Any]] = []
        self.close_count = 0
        #: reconexões forçadas pelo watchdog de ausência de dados (M2.7)
        self.watchdog_reconexoes = 0
        #: reassinaturas periódicas enviadas (M2.7)
        self.reassinaturas = 0
        self.reassinaturas_com_erro = 0
        #: reassinaturas disparadas por tópico mudo, não pelo relógio
        self.reassinaturas_por_silencio = 0
        #: M2.11: quedas provocadas pela ESCALADA — reassinatura sem efeito.
        #: Separado de `watchdog_reconexoes` de propósito: o watchdog cobre
        #: "não chega NADA", a escalada cobre "chega tudo menos o que eu
        #: assinei". Somar os dois esconderia qual defesa está trabalhando.
        self.reconexoes_por_escalada = 0

    # ------------------------------------------------------------------ estado
    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def last_message_age_seconds(self) -> float:
        """Idade da última mensagem. inf se nunca recebeu nada."""
        if self._last_msg_mono_ns == 0:
            return float("inf")
        return (time.monotonic_ns() - self._last_msg_mono_ns) / 1e9

    @property
    def stale(self) -> bool:
        """Watchdog: True = feed parado, não confiar no dado para decidir."""
        return not self._connected or self.last_message_age_seconds > self.stale_after_seconds

    # ------------------------------------------------------------------ ciclo
    async def start(self) -> None:
        if self._task is not None:
            raise RuntimeError(f"feed {self.name} já iniciado")
        self._stopped.clear()
        self._task = asyncio.create_task(self._run(), name=f"feed-{self.name}")

    async def stop(self) -> None:
        self._stopped.set()
        if self._ws is not None:
            await self._ws.close()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        backoff = self.reconnect_initial_seconds
        while not self._stopped.is_set():
            try:
                async with websockets.connect(
                    self.url,
                    additional_headers={"User-Agent": self.user_agent},
                    max_queue=4096,
                    open_timeout=10,
                    # KEEPALIVE — a causa da instabilidade do RTDS em produção.
                    # Estava fixo em None ("heartbeat é responsabilidade da
                    # subclasse"), o que vale para o CLOB (que tem PING/PONG de
                    # aplicação) mas deixava RTDS e Binance SEM keepalive
                    # nenhum. O smoke_feeds.py sustentava a conexão porque usa
                    # os defaults da lib (ping a cada 20s) — era essa a
                    # diferença entre os dois caminhos de código.
                    ping_interval=self.ws_ping_interval,
                    ping_timeout=self.ws_ping_timeout,
                ) as ws:
                    self._ws = ws
                    self._connected = True
                    self.log.info("conectado", url=self.url)
                    await self._on_connected(ws)
                    backoff = self.reconnect_initial_seconds  # conexão boa zera o backoff
                    reassinatura = asyncio.create_task(
                        self._loop_de_reassinatura(ws),
                        name=f"reassinatura-{self.name}",
                    )
                    try:
                        await self._receive_loop(ws)
                    finally:
                        reassinatura.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await reassinatura
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                motivo = self._registrar_queda(exc)
                self.log.warning(
                    "conexão caiu",
                    backoff_s=round(backoff, 2),
                    **motivo,
                )
            finally:
                self._connected = False
                self._ws = None
            if self._stopped.is_set():
                return
            self.reconnect_count += 1
            # jitter uniforme em [0.5, 1.5)x para dessincronizar reconexões
            await asyncio.sleep(backoff * (0.5 + random.random()))
            backoff = min(backoff * 2, self.reconnect_max_seconds)

    def _registrar_queda(self, exc: BaseException) -> dict[str, Any]:
        """Extrai código e razão do close — o dado que faltava para diagnosticar.

        Um `ConnectionClosed` do websockets carrega o frame de close com o
        código do RFC 6455 (1000 normal, 1006 anormal, 1011 erro do servidor,
        1013 try again later...). Sem registrar isso, toda queda vira a mesma
        linha de log e a investigação não tem por onde começar.
        """
        codigo: int | None = None
        razao: str | None = None
        recebido = getattr(exc, "rcvd", None)
        enviado = getattr(exc, "sent", None)
        frame = recebido if recebido is not None else enviado
        if frame is not None:
            codigo = getattr(frame, "code", None)
            razao = getattr(frame, "reason", None)
        motivo = {
            "erro": f"{type(exc).__name__}: {exc}",
            "close_code": codigo,
            "close_reason": razao,
            "close_origem": "servidor" if recebido is not None else "cliente",
        }
        self.close_count += 1
        self.close_reasons.append(motivo)
        del self.close_reasons[: -self.MAX_CLOSE_REASONS]
        return motivo

    async def _receive_loop(self, ws: websockets.ClientConnection) -> None:
        async for message in self._mensagens(ws):
            ts_mono_ns = time.monotonic_ns()
            ts_wall_ns = time.time_ns()
            raw = message if isinstance(message, bytes) else message.encode()
            parsed = self._parse(raw)
            event = FeedEvent(
                source=self.name,
                ts_mono_ns=ts_mono_ns,
                ts_wall_ns=ts_wall_ns,
                raw=raw,
                parsed=parsed,
            )
            self._last_msg_mono_ns = ts_mono_ns
            self.message_count += 1
            await self._handle_message(event)
            if self.on_event is not None:
                result = self.on_event(event)
                if result is not None:
                    await result

    async def _mensagens(
        self, ws: websockets.ClientConnection
    ) -> AsyncIterator[str | bytes]:
        """As mensagens da conexão, com watchdog de AUSÊNCIA DE DADOS (M2.7).

        `async for message in ws` espera para sempre. O keepalive de protocolo
        do M2.1 mantém o socket vivo — o servidor responde PING —, então uma
        conexão que parou de PUBLICAR fica aberta e muda indefinidamente, sem
        erro nenhum. Foi o que a gravação de 8h mediu: **6 silêncios de
        conexão inteira, o maior de 3.796 segundos**, com o socket aberto o
        tempo todo.

        Ping/pong prova que o CANO está aberto; não prova que a ÁGUA está
        passando. O watchdog cobre a segunda pergunta: sem mensagem nenhuma
        por `sem_dados_timeout_s`, derruba e reconecta.

        Cuidado que o limiar exige: ele conta QUALQUER mensagem, então não
        pega assinatura de um tópico caducando enquanto outros continuam
        chegando — para isso existe a reassinatura periódica. Os dois
        mecanismos cobrem os dois fenômenos medidos, e nenhum cobre o outro.
        """
        while True:
            if self.sem_dados_timeout_s is None:
                yield await ws.recv()
                continue
            try:
                yield await asyncio.wait_for(
                    ws.recv(), timeout=self.sem_dados_timeout_s
                )
            except TimeoutError as erro:
                self.watchdog_reconexoes += 1
                self.log.warning(
                    "watchdog: conexão viva e MUDA, derrubando para reconectar",
                    sem_dados_s=self.sem_dados_timeout_s,
                    idade_ultima_msg_s=round(self.last_message_age_seconds, 2),
                    watchdog_reconexoes=self.watchdog_reconexoes,
                )
                raise SilencioDeDados(
                    f"{self.name}: sem mensagem por "
                    f"{self.sem_dados_timeout_s}s com a conexão aberta"
                ) from erro

    async def _loop_de_reassinatura(self, ws: websockets.ClientConnection) -> None:
        """Reenvia a assinatura periodicamente enquanto a conexão viver.

        A gravação de 8h mediu **48 casos de tópico mudo com a conexão viva e
        recebendo outros tópicos** — a assinatura caduca do lado do servidor e
        nada avisa. Reenviá-la custa um frame de texto de poucos bytes e
        elimina a classe inteira de falha.

        Erro ao reenviar não derruba a conexão de propósito: se o socket
        morreu, o laço de recepção descobre e reconecta com o motivo certo;
        derrubar aqui trocaria um diagnóstico bom por um genérico.
        """
        intervalo = self.reassinatura_intervalo_s
        if not intervalo:
            return
        # O relógio sozinho não basta, e a aritmética diz por quê: 48
        # caducidades em 8h são 6 por hora, e reassinar a cada 300s deixaria
        # até 300s de cegueira POR caducidade — 1.800s/h contra uma meta de
        # 60s/h. O intervalo é seguro barato; quem cumpre a meta é a REAÇÃO
        # ao tópico que emudeceu.
        passo = min(intervalo, self.PASSO_DE_VERIFICACAO_S)
        desde_a_ultima = 0.0
        # M2.11: quantas reassinaturas urgentes SEGUIDAS sem o tópico voltar.
        sem_efeito = 0
        while True:
            await asyncio.sleep(passo)
            desde_a_ultima += passo
            urgencia = self._reassinatura_urgente()
            if urgencia is None:
                # O tópico voltou (ou nunca esteve mudo): a escalada zera.
                sem_efeito = 0
                if desde_a_ultima < intervalo:
                    continue
            elif await self._escalar_se_sem_efeito(ws, sem_efeito, urgencia):
                return
            try:
                await self._reassinar(ws)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.reassinaturas_com_erro += 1
                self.log.warning(
                    "reassinatura falhou", erro=f"{type(exc).__name__}: {exc}"
                )
                return
            self.reassinaturas += 1
            desde_a_ultima = 0.0
            if urgencia is not None:
                sem_efeito += 1
                self.reassinaturas_por_silencio += 1
                self.log.warning(
                    "tópico mudo com a conexão viva: reassinando",
                    conexao=self.rotulo,
                    motivo=urgencia,
                    tentativas_sem_efeito=sem_efeito,
                    total_por_silencio=self.reassinaturas_por_silencio,
                )
            else:
                self.log.debug("reassinatura periódica", total=self.reassinaturas)

    async def _escalar_se_sem_efeito(
        self, ws: websockets.ClientConnection, sem_efeito: int, urgencia: str
    ) -> bool:
        """Derruba o socket quando reassinar deixou de ser resposta.

        M2.11 — o achado que a gravação de 2026-08-22 tornou impossível
        ignorar: **2.482 reassinaturas, uma a cada 5 s, e o tópico não voltou**.
        A cobertura da série da âncora ficou em 8,1% em duas horas cheias, e o
        watchdog de ausência de dados nunca disparou porque o socket estava
        vivo recebendo outro tráfego.

        Reassinar cobre a assinatura que caducou. Não cobre o servidor que
        parou de publicar AQUELE tópico para AQUELA conexão — e nesse estado
        insistir é só ruído no log. A resposta que sobra é a mais grosseira e
        a única que ainda muda alguma coisa: derrubar e reconectar, o que
        refaz a assinatura do zero, possivelmente contra outro nó.

        Fecha com 1012 (`service restart`), que é o código honesto para
        "reinicie esta conexão" — e o motivo vai no frame, então
        `_registrar_queda` do outro lado do laço grava a causa em vez de um
        `1006` genérico.
        """
        limite = self.reassinaturas_ate_derrubar
        if not limite or sem_efeito < limite:
            return False
        self.reconexoes_por_escalada += 1
        self.log.error(
            "reassinatura sem efeito: derrubando a conexão",
            conexao=self.rotulo,
            motivo=urgencia,
            tentativas=sem_efeito,
            total_por_escalada=self.reconexoes_por_escalada,
        )
        await ws.close(code=1012, reason="topico mudo apos reassinaturas")
        return True

    def _reassinatura_urgente(self) -> str | None:
        """Há motivo para reassinar AGORA, sem esperar o intervalo?

        Subclasse responde. `None` = nada urgente. A string é o motivo, e vai
        para o log — "reassinou" sem causa seria o mesmo beco sem saída que
        "caiu" sem código de close (API_NOTES §13.7).
        """
        return None

    async def _reassinar(self, ws: websockets.ClientConnection) -> None:
        """O que reenviar na reassinatura. Subclasse implementa."""

    @staticmethod
    def _parse(raw: bytes) -> Any:
        import orjson

        try:
            return orjson.loads(raw)
        except orjson.JSONDecodeError:
            return None  # ex.: "PONG" do heartbeat do CLOB

    # ------------------------------------------------------------- envio
    async def send_frame(
        self, frame: str, ws: websockets.ClientConnection | None = None
    ) -> None:
        """Envia um frame SEMPRE como texto.

        Existe para tornar a invariante estrutural, não só convencional: a
        lib `websockets` decide o tipo de frame pelo tipo do argumento —
        `str` vira frame de texto, `bytes` vira BINÁRIO. O RTDS da Polymarket
        fecha a conexão com `1003 unsupported data / Binary is not supported`
        ao receber binário, e o recorder entrava em loop de reconexão em
        produção por causa disso (o `orjson.dumps` devolve bytes).

        Passar bytes aqui é erro de programação, não condição de runtime:
        levanta TypeError em vez de virar uma desconexão silenciosa em
        produção três semanas depois.
        """
        if not isinstance(frame, str):
            raise TypeError(
                f"frame de WS precisa ser str (texto), veio {type(frame).__name__}. "
                "orjson.dumps() devolve bytes — use .decode()."
            )
        destino = ws if ws is not None else self._ws
        if destino is None:
            raise RuntimeError(f"feed {self.name} não está conectado")
        await destino.send(frame)

    # ------------------------------------------------------------ p/ subclasse
    async def _on_connected(self, ws: websockets.ClientConnection) -> None:
        raise NotImplementedError

    async def _handle_message(self, event: FeedEvent) -> None:
        """Hook opcional; o default não faz nada além do on_event."""
