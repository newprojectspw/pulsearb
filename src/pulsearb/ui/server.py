"""Dashboard stub do M1: banner de modo, status dos feeds, últimos ticks.

HTML/JS puro, sem framework — a página inteira vive em INDEX_HTML e é servida
pelo próprio FastAPI. Atualização por WebSocket (push a cada segundo).
O conteúdo de verdade (PnL, janelas, log de decisões) chega no M5.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from typing import Any

import orjson
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse


@dataclass
class FeedStatus:
    connected: bool = False
    stale: bool = True
    message_count: int = 0
    last_message_age_s: float = float("inf")


@dataclass
class DashboardState:
    """Estado exposto ao dashboard. Os feeds/engine escrevem; a UI só lê."""

    mode: str = "SIM"
    started_mono: float = field(default_factory=time.monotonic)
    feeds: dict[str, FeedStatus] = field(default_factory=dict)
    last_ticks: dict[str, dict[str, Any]] = field(default_factory=dict)  # chave: topic:asset
    counters: dict[str, int] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "uptime_s": round(time.monotonic() - self.started_mono, 1),
            "feeds": {
                name: {
                    "connected": status.connected,
                    "stale": status.stale,
                    "message_count": status.message_count,
                    "last_message_age_s": (
                        None
                        if status.last_message_age_s == float("inf")
                        else round(status.last_message_age_s, 2)
                    ),
                }
                for name, status in self.feeds.items()
            },
            "last_ticks": self.last_ticks,
            "counters": self.counters,
        }


INDEX_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PULSEARB</title>
<style>
  :root { color-scheme: dark; }
  body { margin:0; font-family: ui-monospace, monospace; background:#0d1117; color:#e6edf3; }
  #banner { padding:18px; text-align:center; font-size:1.6rem; font-weight:bold; letter-spacing:.2em; }
  .SIM    { background:#1f6feb; }
  .SHADOW { background:#9e6a03; }
  .LIVE   { background:#da3633; }
  .off    { background:#484f58; }
  main { padding:16px; max-width:720px; margin:0 auto; }
  h2 { font-size:.9rem; text-transform:uppercase; color:#8b949e; border-bottom:1px solid #21262d; padding-bottom:4px; }
  table { width:100%; border-collapse:collapse; font-size:.9rem; }
  td, th { padding:4px 8px; text-align:left; border-bottom:1px solid #21262d; }
  .ok   { color:#3fb950; }
  .bad  { color:#f85149; }
  #status { font-size:.8rem; color:#8b949e; padding:8px 16px; }
</style>
</head>
<body>
<div id="banner" class="off">PULSEARB — conectando…</div>
<main>
  <h2>Feeds</h2>
  <table id="feeds"><tbody></tbody></table>
  <h2>Últimos ticks</h2>
  <table id="ticks"><tbody></tbody></table>
  <h2>Contadores</h2>
  <table id="counters"><tbody></tbody></table>
</main>
<div id="status"></div>
<script>
  const banner = document.getElementById("banner");
  function row(cells) { return "<tr>" + cells.map(c => `<td>${c}</td>`).join("") + "</tr>"; }
  function connect() {
    const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws");
    ws.onmessage = (msg) => {
      const s = JSON.parse(msg.data);
      banner.textContent = "PULSEARB — MODO " + s.mode;
      banner.className = s.mode;
      document.querySelector("#feeds tbody").innerHTML = Object.entries(s.feeds).map(([name, f]) =>
        row([name,
             f.connected ? '<span class="ok">conectado</span>' : '<span class="bad">desconectado</span>',
             f.stale ? '<span class="bad">PARADO</span>' : '<span class="ok">vivo</span>',
             f.message_count + " msgs",
             (f.last_message_age_s ?? "—") + " s"])).join("");
      document.querySelector("#ticks tbody").innerHTML = Object.entries(s.last_ticks).map(([key, t]) =>
        row([key, t.price, t.age_s + " s atrás"])).join("");
      document.querySelector("#counters tbody").innerHTML = Object.entries(s.counters).map(([key, v]) =>
        row([key, v])).join("");
      document.getElementById("status").textContent = "uptime " + s.uptime_s + " s";
    };
    ws.onclose = () => {
      banner.textContent = "PULSEARB — sem conexão com o processo";
      banner.className = "off";
      setTimeout(connect, 1000);
    };
  }
  connect();
</script>
</body>
</html>"""


def create_app(state: DashboardState) -> FastAPI:
    app = FastAPI(title="PULSEARB", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return INDEX_HTML

    @app.get("/api/state")
    async def api_state() -> Any:
        return state.snapshot()

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            while True:
                await websocket.send_bytes(orjson.dumps(state.snapshot()))
                await asyncio.sleep(1.0)
        except WebSocketDisconnect:
            pass
        except Exception:
            with contextlib.suppress(Exception):
                await websocket.close()

    return app
