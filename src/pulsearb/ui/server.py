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
from pathlib import Path
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
    #: Onde a chave de emergência mora. `None` = o botão não existe nesta
    #: instância, e a página diz isso em vez de mostrar um botão morto.
    caminho_do_kill: Path | None = None
    feeds: dict[str, FeedStatus] = field(default_factory=dict)
    last_ticks: dict[str, dict[str, Any]] = field(default_factory=dict)  # chave: topic:asset
    counters: dict[str, int] = field(default_factory=dict)

    def kill_acionado(self) -> bool:
        """A chave está puxada? Lido do DISCO a cada consulta.

        Nunca cacheado, pela mesma razão do `PortaoDeRisco._kill_acionado`: a
        chave existe para ser puxada com o bot rodando, e pode ser puxada por
        `touch` numa sessão ssh — sem passar por esta página.

        Erro de leitura conta como ACIONADA. Entre supor que ninguém puxou e
        supor que alguém puxou e o disco não deixa conferir, a segunda é a que
        não perde dinheiro por engano.
        """
        if self.caminho_do_kill is None:
            return False
        try:
            return self.caminho_do_kill.exists()
        except OSError:
            return True

    def acionar_kill(self) -> bool:
        """Cria o arquivo. Devolve `False` se não há caminho configurado.

        Idempotente: puxar a chave já puxada não é erro. Quem aperta o botão
        num momento de aperto vai apertar duas vezes.
        """
        if self.caminho_do_kill is None:
            return False
        self.caminho_do_kill.parent.mkdir(parents=True, exist_ok=True)
        self.caminho_do_kill.touch()
        return True

    def snapshot(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "kill": {
                "disponivel": self.caminho_do_kill is not None,
                "acionado": self.kill_acionado(),
                "caminho": (
                    str(self.caminho_do_kill) if self.caminho_do_kill else None
                ),
            },
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
  #kill-caixa { margin:24px 0 8px; padding:14px; border:1px solid #30363d; border-radius:6px; }
  #kill-botao { width:100%; padding:14px; font:inherit; font-weight:bold; letter-spacing:.15em;
                background:#da3633; color:#fff; border:0; border-radius:4px; cursor:pointer; }
  #kill-botao:hover:not(:disabled) { background:#f85149; }
  #kill-botao:disabled { background:#484f58; cursor:not-allowed; }
  #kill-nota { font-size:.75rem; color:#8b949e; margin-top:8px; line-height:1.5; }
  .armado { color:#f85149; font-weight:bold; }
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

  <h2>Parada de emergência</h2>
  <div id="kill-caixa">
    <button id="kill-botao" disabled>PARAR TUDO</button>
    <div id="kill-nota">carregando…</div>
  </div>
</main>
<div id="status"></div>
<script>
  const banner = document.getElementById("banner");
  const killBotao = document.getElementById("kill-botao");
  const killNota = document.getElementById("kill-nota");
  function row(cells) { return "<tr>" + cells.map(c => `<td>${c}</td>`).join("") + "</tr>"; }

  function pintarKill(kill) {
    if (!kill || !kill.disponivel) {
      killBotao.disabled = true;
      killBotao.textContent = "INDISPONÍVEL";
      killNota.textContent = "este processo subiu sem caminho de kill configurado.";
      return;
    }
    if (kill.acionado) {
      killBotao.disabled = true;
      killBotao.textContent = "PARADO";
      // Quem desarma é uma pessoa na máquina, de propósito: o botão só arma.
      killNota.innerHTML = '<span class="armado">A chave está puxada.</span> ' +
        "Nenhuma ordem passa. Para religar, apague o arquivo na máquina:<br><code>rm " +
        kill.caminho + "</code>";
      return;
    }
    killBotao.disabled = false;
    killBotao.textContent = "PARAR TUDO";
    killNota.textContent = "Cria " + kill.caminho +
      ". O portão lê o arquivo a cada ordem. Só uma pessoa na máquina religa.";
  }

  killBotao.addEventListener("click", async () => {
    // Confirmação porque o efeito é imediato e o desfazer não é por aqui.
    if (!confirm("Puxar a chave de emergência? Nenhuma ordem passa até alguém apagar o arquivo NA MÁQUINA.")) return;
    killBotao.disabled = true;
    try {
      const r = await fetch("/api/kill", { method: "POST" });
      const dado = await r.json();
      if (dado.ok) { pintarKill(dado.kill); } else { killNota.textContent = dado.erro; }
    } catch (e) {
      // Falhar aqui NÃO significa que o bot está parado.
      killNota.innerHTML = '<span class="armado">A chamada falhou.</span> ' +
        "Não confie nesta página: puxe a chave na máquina com <code>touch KILL</code>.";
      killBotao.disabled = false;
    }
  });
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
      pintarKill(s.kill);
    };
    ws.onclose = () => {
      banner.textContent = "PULSEARB — sem conexão com o processo";
      banner.className = "off";
      // Sem conexão não sabemos o estado da chave, e "não sei" não pode
      // parecer "está tudo bem".
      killBotao.disabled = true;
      killNota.textContent = "sem conexão — o estado da chave é desconhecido.";
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

    @app.post("/api/kill")
    async def api_kill() -> Any:
        """Puxa a chave de emergência. Item 3.11.

        **Só ARMA. Não desarma, e a assimetria é deliberada.**

        Duas razões que apontam para o mesmo lado:

        1. É a mesma regra do disjuntor: o que para o bot fica parado até uma
           PESSOA desarmar à mão (`rm KILL`). Uma chave que se desfaz do
           mesmo lugar de onde foi puxada não é chave de emergência — é um
           interruptor.
        2. Este dashboard não tem autenticação. Uma rota que ARMA falha para
           o lado seguro no pior caso (alguém para o bot). Uma rota que
           DESARMA seria uma rota que qualquer um na rede usa para religar
           um bot que foi parado de propósito.

        Pelo mesmo motivo, não há proteção contra CSRF aqui e isso é aceito
        conscientemente: o efeito de um POST forjado é parar de operar.
        """
        if not state.acionar_kill():
            return {
                "ok": False,
                "erro": (
                    "sem caminho de kill configurado nesta instancia — "
                    "o processo precisa passar `caminho_do_kill`"
                ),
            }
        return {"ok": True, "kill": state.snapshot()["kill"]}

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
