#!/usr/bin/env python3
"""Mede a latência do PULSEARB até a Polymarket, para escolher a região da VPS.

Rode o mesmo comando de cada candidata (Amsterdã, Londres) e compare — a decisão
sai do p99, nunca da média.

    python3 scripts/benchmark_latency.py --label amsterdam --json out-ams.json

O que é medido:

1. REST (https://clob.polymarket.com) — DNS, TCP connect, TLS handshake e
   time-to-first-byte a frio; depois 100 requisições em conexão quente, que é o
   regime real do hot path.
2. WS RTDS (wss://ws-live-data.polymarket.com) — tempo do início da conexão até
   a primeira mensagem de preço chegar.
3. WS CLOB market — a mesma medida para o livro de ofertas (precisa de
   --token-id).
4. PING/PONG de aplicação no WS do CLOB — 100 pings. É a melhor aproximação
   disponível do custo real de decisão→ack sem enviar ordem.

Sem dependências: só a stdlib, porque isso precisa rodar numa VPS recém-criada
antes de existir qualquer projeto instalado.

Endpoints e protocolos conferidos em docs/API_NOTES.md (seções 2 e 6), extraídos
do código-fonte do SDK oficial polymarket-client 0.6.0.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import sys
import time
from typing import Any
from urllib.parse import urlparse

# --- Endpoints verificados (polymarket-client 0.6.0, src/polymarket/environments.py)
CLOB_REST = "https://clob.polymarket.com"
CLOB_MARKET_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
RTDS_WS = "wss://ws-live-data.polymarket.com"

# Heartbeat de aplicação do CLOB (_internal/streams/clob/heartbeat.py):
# texto puro "PING", resposta "PONG", intervalo de 10s, morto após 30s.
CLOB_PING = "PING"
CLOB_PONG = "PONG"

DEFAULT_SAMPLES = 100


# --------------------------------------------------------------------------
# Estatística
# --------------------------------------------------------------------------
def percentile(values: list[float], pct: float) -> float:
    """Percentil por nearest-rank. Sem interpolação: com 100 amostras o p99 deve
    ser uma amostra real, não uma média inventada entre duas."""
    if not values:
        return float("nan")
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(-(-pct * len(ordered) // 100))))
    return ordered[rank - 1]


def summarize(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "min_ms": round(min(values), 3),
        "p50_ms": round(percentile(values, 50), 3),
        "p90_ms": round(percentile(values, 90), 3),
        "p99_ms": round(percentile(values, 99), 3),
        "max_ms": round(max(values), 3),
        "mean_ms": round(sum(values) / len(values), 3),
    }


def ms_since(start_ns: int) -> float:
    return (time.monotonic_ns() - start_ns) / 1e6


def connect_any(host: str, port: int, timeout: float) -> tuple[socket.socket, str, float]:
    """Conecta tentando cada endereço do getaddrinfo, na ordem.

    Não é preciosismo: VPS barata costuma anunciar AAAA e não ter rota IPv6.
    Ficar no primeiro endereço faria o benchmark falhar por rede, e a gente
    interpretaria como latência.
    """
    last_error: Exception | None = None
    for family, socktype, proto, _canon, sockaddr in socket.getaddrinfo(
        host, port, proto=socket.IPPROTO_TCP
    ):
        sock = socket.socket(family, socktype, proto)
        sock.settimeout(timeout)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        started = time.monotonic_ns()
        try:
            sock.connect(sockaddr)
        except OSError as exc:
            last_error = exc
            sock.close()
            continue
        return sock, str(sockaddr[0]), ms_since(started)
    raise last_error or OSError(f"não foi possível conectar em {host}:{port}")


# --------------------------------------------------------------------------
# HTTP mínimo sobre socket cru (para separar DNS / TCP / TLS / TTFB)
# --------------------------------------------------------------------------
class RawHttpsConnection:
    """Conexão HTTPS keep-alive com cada etapa do estabelecimento cronometrada."""

    def __init__(self, host: str, port: int = 443, timeout: float = 10.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock: ssl.SSLSocket | None = None
        self.buffer = b""
        self.timings: dict[str, float] = {}

    def connect(self) -> None:
        t0 = time.monotonic_ns()
        socket.getaddrinfo(self.host, self.port, proto=socket.IPPROTO_TCP)
        self.timings["dns_ms"] = round(ms_since(t0), 3)

        raw, resolved_ip, connect_ms = connect_any(self.host, self.port, self.timeout)
        self.timings["resolved_ip"] = resolved_ip
        self.timings["tcp_connect_ms"] = round(connect_ms, 3)

        ctx = ssl.create_default_context()
        t2 = time.monotonic_ns()
        self.sock = ctx.wrap_socket(raw, server_hostname=self.host)
        self.timings["tls_handshake_ms"] = round(ms_since(t2), 3)
        self.timings["tls_version"] = self.sock.version() or "?"
        self.timings["connect_total_ms"] = round(ms_since(t0), 3)

    def request(self, path: str) -> tuple[int, float, float, int]:
        """Faz um GET e devolve (status, ttfb_ms, total_ms, tamanho_do_corpo)."""
        if self.sock is None:
            raise RuntimeError("connect() antes de request()")
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {self.host}\r\n"
            "User-Agent: pulsearb-benchmark/0\r\n"
            "Accept: application/json\r\n"
            "Connection: keep-alive\r\n\r\n"
        ).encode()

        t0 = time.monotonic_ns()
        self.sock.sendall(req)

        # time-to-first-byte: primeiro byte de resposta que sai do fio
        if not self.buffer:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("conexão fechada pelo servidor")
            self.buffer += chunk
        ttfb = ms_since(t0)

        head, body = self._read_head()
        status = int(head.split(b" ", 2)[1])
        headers = self._parse_headers(head)

        if headers.get("transfer-encoding", "").lower() == "chunked":
            body = self._read_chunked(body)
        else:
            length = int(headers.get("content-length", "0"))
            while len(body) < length:
                chunk = self.sock.recv(65536)
                if not chunk:
                    break
                body += chunk
            body, self.buffer = body[:length], body[length:]

        total = ms_since(t0)
        if headers.get("connection", "").lower() == "close":
            self.close()
        return status, ttfb, total, len(body)

    def _read_head(self) -> tuple[bytes, bytes]:
        assert self.sock is not None
        while b"\r\n\r\n" not in self.buffer:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("conexão fechada durante os headers")
            self.buffer += chunk
        head, _, rest = self.buffer.partition(b"\r\n\r\n")
        self.buffer = b""
        return head, rest

    @staticmethod
    def _parse_headers(head: bytes) -> dict[str, str]:
        headers: dict[str, str] = {}
        for line in head.split(b"\r\n")[1:]:
            name, _, value = line.partition(b":")
            if value:
                headers[name.decode().strip().lower()] = value.decode().strip()
        return headers

    def _read_chunked(self, body: bytes) -> bytes:
        assert self.sock is not None
        out = b""
        while True:
            while b"\r\n" not in body:
                chunk = self.sock.recv(65536)
                if not chunk:
                    return out
                body += chunk
            size_line, _, body = body.partition(b"\r\n")
            size = int(size_line.split(b";")[0], 16)
            if size == 0:
                self.buffer = b""
                return out
            while len(body) < size + 2:
                chunk = self.sock.recv(65536)
                if not chunk:
                    return out
                body += chunk
            out += body[:size]
            body = body[size + 2 :]

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None


# --------------------------------------------------------------------------
# WebSocket mínimo (RFC 6455) — só o que o benchmark precisa
# --------------------------------------------------------------------------
OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x1, 0x2, 0x8, 0x9, 0xA
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class MiniWebSocket:
    """Cliente WebSocket enxuto: handshake, texto mascarado, ping/pong de
    protocolo. Existe para não exigir `pip install` numa VPS nova."""

    def __init__(self, url: str, timeout: float = 15.0) -> None:
        parsed = urlparse(url)
        self.secure = parsed.scheme == "wss"
        self.host = parsed.hostname or ""
        self.port = parsed.port or (443 if self.secure else 80)
        self.path = parsed.path or "/"
        if parsed.query:
            self.path += "?" + parsed.query
        self.timeout = timeout
        self.sock: socket.socket | None = None
        self.buffer = b""
        self.timings: dict[str, float] = {}

    def connect(self) -> None:
        t0 = time.monotonic_ns()
        socket.getaddrinfo(self.host, self.port, proto=socket.IPPROTO_TCP)
        self.timings["dns_ms"] = round(ms_since(t0), 3)

        sock, resolved_ip, connect_ms = connect_any(self.host, self.port, self.timeout)
        self.timings["resolved_ip"] = resolved_ip
        self.timings["tcp_connect_ms"] = round(connect_ms, 3)

        if self.secure:
            ctx = ssl.create_default_context()
            t2 = time.monotonic_ns()
            sock = ctx.wrap_socket(sock, server_hostname=self.host)
            self.timings["tls_handshake_ms"] = round(ms_since(t2), 3)
        self.sock = sock

        key = base64.b64encode(os.urandom(16)).decode()
        t3 = time.monotonic_ns()
        sock.sendall(
            (
                f"GET {self.path} HTTP/1.1\r\n"
                f"Host: {self.host}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n"
                "User-Agent: pulsearb-benchmark/0\r\n\r\n"
            ).encode()
        )
        while b"\r\n\r\n" not in self.buffer:
            chunk = sock.recv(65536)
            if not chunk:
                raise ConnectionError("conexão fechada durante o handshake do WS")
            self.buffer += chunk
        head, _, rest = self.buffer.partition(b"\r\n\r\n")
        self.buffer = rest
        self.timings["ws_handshake_ms"] = round(ms_since(t3), 3)

        status_line = head.split(b"\r\n")[0].decode(errors="replace")
        if " 101" not in status_line:
            raise ConnectionError(f"upgrade recusado: {status_line}")

        expected = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        accept = ""
        for line in head.split(b"\r\n")[1:]:
            name, _, value = line.partition(b":")
            if name.decode().strip().lower() == "sec-websocket-accept":
                accept = value.decode().strip()
        if accept != expected:
            raise ConnectionError("Sec-WebSocket-Accept inválido")

        self.timings["connect_total_ms"] = round(ms_since(t0), 3)

    def send_text(self, text: str) -> None:
        self._send_frame(OP_TEXT, text.encode())

    def send_json(self, payload: dict[str, Any]) -> None:
        self.send_text(json.dumps(payload, separators=(",", ":")))

    def _send_frame(self, opcode: int, data: bytes) -> None:
        if self.sock is None:
            raise RuntimeError("connect() antes de enviar")
        header = bytearray([0x80 | opcode])
        length = len(data)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack("!H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", length)
        mask = os.urandom(4)  # cliente é obrigado a mascarar (RFC 6455 §5.3)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(bytes(header) + masked)

    def _recv_exactly(self, n: int, deadline: float) -> bytes:
        if self.sock is None:
            raise RuntimeError("connect() antes de receber")
        while len(self.buffer) < n:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("timeout aguardando dados do WS")
            self.sock.settimeout(remaining)
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("conexão fechada pelo servidor")
            self.buffer += chunk
        out, self.buffer = self.buffer[:n], self.buffer[n:]
        return out

    def recv_message(self, timeout: float | None = None) -> tuple[int, bytes]:
        """Devolve (opcode, payload) da próxima mensagem de dados.

        Frames de controle são tratados por dentro: PING de protocolo é
        respondido com PONG e a espera continua.
        """
        deadline = time.monotonic() + (timeout if timeout is not None else self.timeout)
        while True:
            first, second = self._recv_exactly(2, deadline)
            fin = bool(first & 0x80)
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._recv_exactly(2, deadline))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exactly(8, deadline))[0]
            payload = self._recv_exactly(length, deadline) if length else b""

            if opcode == OP_PING:
                self._send_frame(OP_PONG, payload)
                continue
            if opcode == OP_PONG:
                continue
            if opcode == OP_CLOSE:
                raise ConnectionError("servidor enviou close")
            if not fin:
                # Fragmentação: junta as continuações até o FIN.
                data = payload
                while not fin:
                    first, second = self._recv_exactly(2, deadline)
                    fin = bool(first & 0x80)
                    length = second & 0x7F
                    if length == 126:
                        length = struct.unpack("!H", self._recv_exactly(2, deadline))[0]
                    elif length == 127:
                        length = struct.unpack("!Q", self._recv_exactly(8, deadline))[0]
                    data += self._recv_exactly(length, deadline) if length else b""
                return opcode, data
            return opcode, payload

    def close(self) -> None:
        if self.sock is not None:
            try:
                self._send_frame(OP_CLOSE, b"")
            except OSError:
                pass
            finally:
                self.sock.close()
                self.sock = None


# --------------------------------------------------------------------------
# Medições
# --------------------------------------------------------------------------
def bench_rest(base_url: str, path: str, samples: int) -> dict[str, Any]:
    parsed = urlparse(base_url)
    host = parsed.hostname or ""
    port = parsed.port or 443
    result: dict[str, Any] = {"url": base_url + path}
    conn = RawHttpsConnection(host, port)
    try:
        conn.connect()
        result["cold"] = dict(conn.timings)

        status, ttfb, total, size = conn.request(path)
        result["cold"]["first_request_ttfb_ms"] = round(ttfb, 3)
        result["cold"]["first_request_total_ms"] = round(total, 3)
        result["http_status"] = status
        result["body_bytes"] = size
        if status >= 400:
            result["aviso"] = (
                f"HTTP {status} — o caminho {path} pode ter mudado; "
                "confira antes de confiar nos números"
            )

        warm: list[float] = []
        errors = 0
        for _ in range(samples):
            try:
                if conn.sock is None:
                    conn = RawHttpsConnection(host, port)
                    conn.connect()
                _status, _ttfb, total, _size = conn.request(path)
                warm.append(total)
            except (OSError, ConnectionError, ValueError):
                errors += 1
                conn.close()
        result["warm_keepalive"] = summarize(warm)
        result["errors"] = errors
    except Exception as exc:  # noqa: BLE001 — o benchmark reporta, não trata
        result["erro"] = f"{type(exc).__name__}: {exc}"
    finally:
        conn.close()
    return result


def bench_ws_first_message(
    url: str, subscribe: dict[str, Any] | None, timeout: float, label: str
) -> dict[str, Any]:
    result: dict[str, Any] = {"url": url, "alvo": label}
    ws = MiniWebSocket(url, timeout=timeout)
    t0 = time.monotonic_ns()
    try:
        ws.connect()
        result.update(ws.timings)
        if subscribe is not None:
            ws.send_json(subscribe)
            result["subscribe_enviado"] = subscribe
        t_sub = time.monotonic_ns()
        _opcode, payload = ws.recv_message(timeout=timeout)
        result["first_message_after_subscribe_ms"] = round(ms_since(t_sub), 3)
        result["first_message_total_ms"] = round(ms_since(t0), 3)
        result["first_message_bytes"] = len(payload)
        result["first_message_preview"] = payload[:220].decode(errors="replace")
    except Exception as exc:  # noqa: BLE001
        result["erro"] = f"{type(exc).__name__}: {exc}"
    finally:
        ws.close()
    return result


def bench_clob_ping_pong(
    token_ids: list[str], samples: int, timeout: float
) -> dict[str, Any]:
    """100 pings de aplicação no WS do CLOB.

    O heartbeat do CLOB é texto puro: manda "PING", recebe "PONG"
    (verificado em _internal/streams/clob/heartbeat.py do SDK 0.6.0).
    """
    result: dict[str, Any] = {"url": CLOB_MARKET_WS, "samples_pedidos": samples}
    ws = MiniWebSocket(CLOB_MARKET_WS, timeout=timeout)
    try:
        ws.connect()
        result.update(ws.timings)
        if token_ids:
            ws.send_json(
                {
                    "type": "market",
                    "assets_ids": token_ids,
                    "custom_feature_enabled": False,
                }
            )
            result["subscrito_em_tokens"] = len(token_ids)

        rtts: list[float] = []
        ignoradas = 0
        timeouts = 0
        for _ in range(samples):
            t0 = time.monotonic_ns()
            ws.send_text(CLOB_PING)
            try:
                while True:
                    _opcode, payload = ws.recv_message(timeout=timeout)
                    if payload.strip() == CLOB_PONG.encode():
                        rtts.append(ms_since(t0))
                        break
                    # Mensagem de mercado chegando no meio do ping: descarta e
                    # segue esperando o PONG. Contamos para o relatório.
                    ignoradas += 1
                    if time.monotonic_ns() - t0 > timeout * 1e9:
                        timeouts += 1
                        break
            except (TimeoutError, ConnectionError):
                timeouts += 1
                break
        result["ping_pong"] = summarize(rtts)
        result["mensagens_de_mercado_ignoradas"] = ignoradas
        result["timeouts"] = timeouts
        if not rtts:
            result["aviso"] = (
                "nenhum PONG recebido — o servidor pode exigir assinatura ativa "
                "antes de responder ao heartbeat; tente com --token-id"
            )
    except Exception as exc:  # noqa: BLE001
        result["erro"] = f"{type(exc).__name__}: {exc}"
    finally:
        ws.close()
    return result


# --------------------------------------------------------------------------
# Relatório
# --------------------------------------------------------------------------
def print_block(title: str, data: dict[str, Any]) -> None:
    print(f"\n{title}")
    print("-" * len(title))
    if "erro" in data:
        print(f"  FALHOU: {data['erro']}")
    for key, value in data.items():
        if key == "erro":
            continue
        if isinstance(value, dict):
            print(f"  {key}:")
            for sub_key, sub_value in value.items():
                print(f"      {sub_key:<32} {sub_value}")
        else:
            print(f"  {key:<36} {value}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark de latência PULSEARB → Polymarket",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Compare regiões pelo p99 do ping/pong do CLOB e pelo TLS handshake.\n"
            "Média engana; o que mata trade é cauda."
        ),
    )
    parser.add_argument("--label", default=socket.gethostname(), help="nome do ponto de medição")
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES, help="amostras (default 100)")
    parser.add_argument("--timeout", type=float, default=15.0, help="timeout por operação (s)")
    parser.add_argument(
        "--rest-path",
        default="/time",
        help="caminho REST a medir (default /time; use / se o servidor recusar)",
    )
    parser.add_argument(
        "--token-id",
        action="append",
        default=[],
        help="token_id do CLOB para medir o WS de mercado; pode repetir",
    )
    parser.add_argument(
        "--twap-window",
        type=int,
        choices=(30, 60),
        default=30,
        help="janela do TWAP Chainlink no RTDS: 30 (mercados 5m) ou 60 (15m/4h)",
    )
    parser.add_argument("--skip-rest", action="store_true")
    parser.add_argument("--skip-ws", action="store_true")
    parser.add_argument("--json", help="grava o resultado completo neste arquivo")
    args = parser.parse_args()

    report: dict[str, Any] = {
        "label": args.label,
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "samples": args.samples,
        "python": sys.version.split()[0],
    }

    print("=" * 72)
    print(f"PULSEARB — benchmark de latência   [{args.label}]   {report['timestamp_utc']}")
    print("=" * 72)

    if not args.skip_rest:
        report["rest_clob"] = bench_rest(CLOB_REST, args.rest_path, args.samples)
        print_block(f"1. REST {CLOB_REST}{args.rest_path}", report["rest_clob"])

    if not args.skip_ws:
        # Tópico do RTDS conforme a janela (protocol.py: _twap_wire_topic).
        topic = "crypto_prices_twap_thirty" if args.twap_window == 30 else "crypto_prices_twap_sixty"
        report["ws_rtds"] = bench_ws_first_message(
            RTDS_WS,
            {"action": "subscribe", "subscriptions": [{"topic": topic, "type": "update"}]},
            args.timeout,
            f"TWAP Chainlink {args.twap_window}s",
        )
        print_block(f"2. WS RTDS (primeira mensagem, topic={topic})", report["ws_rtds"])

        if args.token_id:
            report["ws_clob_market"] = bench_ws_first_message(
                CLOB_MARKET_WS,
                {
                    "type": "market",
                    "assets_ids": args.token_id,
                    "custom_feature_enabled": False,
                },
                args.timeout,
                "livro CLOB",
            )
            print_block("3. WS CLOB market (primeira mensagem)", report["ws_clob_market"])
        else:
            print("\n3. WS CLOB market (primeira mensagem)")
            print("-" * 40)
            print("  PULADO — passe --token-id <token>. Descubra um token ativo com:")
            print("           python3 scripts/verify_market_facts.py")

        report["ws_clob_ping"] = bench_clob_ping_pong(args.token_id, args.samples, args.timeout)
        print_block(f"4. WS CLOB PING/PONG ({args.samples} pings)", report["ws_clob_ping"])

    print("\n" + "=" * 72)
    ping = report.get("ws_clob_ping", {}).get("ping_pong", {})
    rest = report.get("rest_clob", {}).get("warm_keepalive", {})
    print("VEREDITO (o que comparar entre regiões):")
    print(f"  REST quente   p50={rest.get('p50_ms', '?')} ms   p99={rest.get('p99_ms', '?')} ms")
    print(f"  WS ping/pong  p50={ping.get('p50_ms', '?')} ms   p99={ping.get('p99_ms', '?')} ms")
    print("  Escolha a região pelo MENOR p99 do ping/pong. Empate técnico (<2ms)?")
    print("  Então decida por preço e estabilidade do provedor, não por latência.")
    print("=" * 72)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, ensure_ascii=False)
        print(f"\nJSON completo gravado em {args.json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
