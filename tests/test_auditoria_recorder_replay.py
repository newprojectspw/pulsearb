"""Auditoria dos testes do recorder/replay (pós-#197/#198).

Cada cenário da rodada real da VPS reproduzido em segundos, sem rede, sem
relógio de horas: preflight com os números da v3/v4, precedência do env,
desfechos do processo (encerrado / recusado / interrompido / erro), gravação
truncada × arquivo em gravação no diagnóstico, resync no fim da gravação,
relatório final depois de feeds e flush, e os limites 0/1/16 tokens.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import os
import signal
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import orjson
import pytest
from scripts.diagnostico_de_reconciliacao import (
    SAIDA_GRAVACAO_NAO_INTEGRA,
    arquivos_da_gravacao,
    diagnosticar,
)
from scripts.diagnostico_de_reconciliacao import main as diagnostico_main

import pulsearb.recorder.__main__ as recorder_mod
from pulsearb.analysis.integrity import MonitorDeIntegridade
from pulsearb.recorder.__main__ import (
    SAIDA_INTERROMPIDO,
    Recorder,
    preflight_de_armazenamento,
    projetar_armazenamento,
)
from pulsearb.recorder.writer import FONTE_RESYNC
from pulsearb.replay.escopo import slugs_no_escopo
from pulsearb.settings import Settings

MS = 1_000_000
GB = 1_000_000_000


# ─────────────────────────────────────────────────────────────── utilidades


@dataclass
class _Reg:
    fonte: str
    payload: Any
    ts_wall_ns: int = 0


def _book(ts_ms: int, carimbo: int, bid: str, ask: str, tok: str = "tok") -> _Reg:
    return _Reg(
        "poly_ws",
        {
            "event_type": "book",
            "asset_id": tok,
            "timestamp": str(carimbo),
            "bids": [{"price": bid, "size": "100"}],
            "asks": [{"price": ask, "size": "100"}],
        },
        ts_ms * MS,
    )


def _delta(ts_ms: int, best_bid: str, best_ask: str, tok: str = "tok") -> _Reg:
    return _Reg(
        "poly_ws",
        {
            "event_type": "price_change",
            "market": "0xabc",
            "timestamp": str(ts_ms),
            "price_changes": [
                {
                    "asset_id": tok,
                    "price": best_bid,
                    "size": "10",
                    "side": "BUY",
                    "best_bid": best_bid,
                    "best_ask": best_ask,
                }
            ],
        },
        ts_ms * MS,
    )


def _marcador(ts_ms: int, tok: str = "tok") -> _Reg:
    return _Reg(
        FONTE_RESYNC,
        {"_sintetico": True, "tokens": [tok], "ts_perda_ns": ts_ms * MS},
        ts_ms * MS,
    )


def _gravar(caminho: Path, registros: list[_Reg], *, cortar_trailer: bool = False) -> Path:
    with gzip.open(caminho, "wb") as f:
        for r in registros:
            f.write(
                orjson.dumps(
                    {
                        "ts_mono_ns": r.ts_wall_ns,
                        "ts_wall_ns": r.ts_wall_ns,
                        "fonte": r.fonte,
                        "payload": r.payload,
                    }
                )
                + b"\n"
            )
    if cortar_trailer:
        # sem os 8 bytes de CRC+tamanho: é assim que fica um arquivo que o
        # recorder ainda escreve, ou que morreu no meio.
        bruto = caminho.read_bytes()
        caminho.write_bytes(bruto[:-8])
    return caminho


def _envelhecer(caminho: Path, segundos: float = 3600.0) -> None:
    antigo = time.time() - segundos
    os.utime(caminho, (antigo, antigo))


def _config(tmp_path: Path, extra: str = "") -> Path:
    saida = tmp_path / "gravacoes"
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f'recorder:\n  output_dir: "{saida}"\n{extra}', encoding="utf-8")
    return cfg


def _disco_livre(monkeypatch: pytest.MonkeyPatch, livre: int) -> None:
    monkeypatch.setattr(
        recorder_mod.shutil, "disk_usage", lambda _p: SimpleNamespace(free=livre)
    )


# ───────────────────────────── preflight: fórmula, limite exato, números da VPS


def test_formula_e_bytes_por_hora_vezes_duracao_vezes_margem():
    proj = projetar_armazenamento(
        bytes_por_hora=123_456_789.0, duracao_s=5400, margem=1.37, livre_bytes=0
    )
    assert proj["projecao_bytes"] == round(123_456_789.0 * 1.5)
    assert proj["exigido_bytes"] == round(123_456_789.0 * 1.5 * 1.37)


def test_limite_exato_de_armazenamento_cabe_e_um_byte_a_menos_nao():
    """v3: 2 GB/h × 6 h × 1,2 = 14,4 GB. Livre == exigido cabe; um byte a menos, não."""
    base = {"bytes_por_hora": 2 * GB * 1.0, "duracao_s": 6 * 3600, "margem": 1.2}
    assert projetar_armazenamento(**base, livre_bytes=14_400_000_000)["cabe"] is True
    assert projetar_armazenamento(**base, livre_bytes=14_399_999_999)["cabe"] is False


def test_preflight_so_vale_a_partir_da_duracao_minima(tmp_path, monkeypatch):
    _disco_livre(monkeypatch, 0)
    settings = Settings.load(_config(tmp_path))
    minimo = settings.recorder.duracao_minima_para_preflight_s
    assert preflight_de_armazenamento(settings, minimo - 1)["aplicavel"] is False
    with pytest.raises(recorder_mod.PreflightRecusado):
        preflight_de_armazenamento(settings, minimo)


def test_v3_6h_recusada_com_2gb_por_hora_e_13_6gb_livres(tmp_path, monkeypatch):
    """A rodada v3 da VPS: projeção 14,4 GB, livre 13,6 GB → RECUSA, código 1,
    nenhum arquivo gravado, `run` nunca chamado."""
    _disco_livre(monkeypatch, 13_600_000_000)
    chamado = []

    async def run_falso(settings, segundos):  # pragma: no cover - não pode rodar
        chamado.append(segundos)
        return {}

    monkeypatch.setattr(recorder_mod, "run", run_falso)
    cfg = _config(tmp_path)

    assert recorder_mod.main(["--duration", "6h", "--config", str(cfg)]) == 1
    assert chamado == []
    assert list((tmp_path / "gravacoes").glob("*.jsonl*")) == []


def test_v4_env_de_200mb_por_hora_ganha_e_a_6h_cabe(tmp_path, monkeypatch):
    """A rodada v4: env 200 MB/h e 16 tokens. 0,2 × 6 × 1,2 = 1,44 GB ≤ 13,6 GB.
    O env tem de VENCER o YAML e não apagar as outras chaves do `recorder`."""
    _disco_livre(monkeypatch, 13_600_000_000)
    monkeypatch.setenv("PULSEARB_RECORDER__BYTES_POR_HORA_ESTIMADOS", "200000000")
    monkeypatch.setenv("PULSEARB_RECORDER__MAX_TOKENS_ASSINADOS", "16")
    recebido: dict[str, Any] = {}

    async def run_falso(settings, segundos):
        recebido["settings"] = settings
        recebido["segundos"] = segundos
        return {"desfecho": "completa"}

    monkeypatch.setattr(recorder_mod, "run", run_falso)
    cfg = _config(tmp_path, "  bytes_por_hora_estimados: 2000000000\n  margem_de_disco: 1.2\n")

    assert recorder_mod.main(["--duration", "6h", "--config", str(cfg)]) == 0
    rec = recebido["settings"].recorder
    assert rec.bytes_por_hora_estimados == 200_000_000
    assert rec.max_tokens_assinados == 16
    assert rec.output_dir == str(tmp_path / "gravacoes")  # do YAML, preservado
    assert recebido["segundos"] == 6 * 3600


def test_env_aninhado_nao_apaga_as_outras_chaves_da_secao(tmp_path, monkeypatch):
    """Precedência: env > YAML só na chave coberta. Antes, qualquer
    `PULSEARB_RECORDER__*` descartava a seção `recorder` INTEIRA do YAML."""
    monkeypatch.setenv("PULSEARB_RECORDER__BYTES_POR_HORA_ESTIMADOS", "200000000")
    cfg = _config(tmp_path, "  margem_de_disco: 1.5\n  rotate_seconds: 1800\n")
    rec = Settings.load(cfg).recorder
    assert rec.bytes_por_hora_estimados == 200_000_000
    assert rec.margem_de_disco == 1.5
    assert rec.rotate_seconds == 1800
    assert rec.output_dir == str(tmp_path / "gravacoes")


@pytest.mark.parametrize("valor", ["1", "0"])
def test_zero_ou_um_token_de_limite_e_recusado(tmp_path, monkeypatch, valor):
    """Uma janela exige Up+Down: limite 0 ou 1 não assina nada — recusa."""
    monkeypatch.setenv("PULSEARB_RECORDER__MAX_TOKENS_ASSINADOS", valor)
    with pytest.raises(ValueError):
        Settings.load(tmp_path / "inexistente.yaml")


# ───────────────────────────────── desfechos do processo (systemd-run --collect)


def _main_com_run(tmp_path, monkeypatch, comportamento) -> Any:
    _disco_livre(monkeypatch, 10**15)

    async def run_falso(settings, segundos):
        return comportamento()

    monkeypatch.setattr(recorder_mod, "run", run_falso)
    return recorder_mod.main(["--duration", "1h", "--config", str(_config(tmp_path))])


def test_rodada_completa_sai_zero(tmp_path, monkeypatch):
    assert _main_com_run(tmp_path, monkeypatch, lambda: {"desfecho": "completa"}) == 0


@pytest.mark.parametrize(
    ("desfecho", "codigo"),
    [("interrompida_sigterm", 143), ("interrompida_sigint", 130), (None, 1), ("?", 1)],
)
def test_desfecho_nao_completo_nunca_sai_zero(tmp_path, monkeypatch, desfecho, codigo):
    """Interrupção sai 128+sinal; desfecho ausente ou desconhecido é falha (1),
    nunca sucesso."""
    relatorio = {} if desfecho is None else {"desfecho": desfecho}
    assert _main_com_run(tmp_path, monkeypatch, lambda: relatorio) == codigo


def test_rodada_interrompida_nao_sai_zero(tmp_path, monkeypatch):
    """Antes: `suppress(KeyboardInterrupt)` → 0, igual a uma rodada completa.
    Com a unidade já coletada pelo systemd, o código de saída no journal é o
    que resta para distinguir — e ele mentia."""

    def interrompe():
        raise KeyboardInterrupt

    assert _main_com_run(tmp_path, monkeypatch, interrompe) == SAIDA_INTERROMPIDO


def test_rodada_com_erro_propaga_a_excecao(tmp_path, monkeypatch):
    def quebra():
        raise RuntimeError("feed caiu de vez")

    with pytest.raises(RuntimeError, match="feed caiu de vez"):
        _main_com_run(tmp_path, monkeypatch, quebra)


# ───────── encerramento do recorder: ordem do relatório, flush e SIGTERM


class _DescobertaVazia:
    def __init__(self, **_kw):
        pass

    async def discover(self):
        return []


class _RecorderSemRede(Recorder):
    """O recorder de verdade com feeds falsos e um diário da ordem do
    encerramento. Nada sai para a rede; `run()` roda inteiro."""

    def __init__(self, settings):
        super().__init__(settings)
        self.diario: list[str] = []
        for nome, feed in self._feed_by_name.items():

            async def start(nome=nome):
                self.diario.append(f"start:{nome}")

            async def stop(nome=nome):
                self.diario.append(f"stop:{nome}")

            feed.start = start
            feed.stop = stop
        finalizar = self.integridade.finalizar

        def finalizar_espia():
            self.diario.append("finalizar")
            finalizar()

        self.integridade.finalizar = finalizar_espia

    def integridade_resumo(self):
        self.diario.append("resumo_integridade")
        return super().integridade_resumo()

    def _write_meta(self, fonte, payload, **kw):
        self.diario.append(f"meta:{fonte}")
        return super()._write_meta(fonte, payload, **kw)

    def _armazenamento_resumo(self, duracao_s):
        fechado = self.writer._task is None and self.writer._file is None
        self.diario.append(f"armazenamento:{'writer_fechado' if fechado else 'writer_aberto'}")
        return super()._armazenamento_resumo(duracao_s)


@pytest.fixture
def sem_rede(monkeypatch):
    for nome in ("DISCOVERY_INTERVAL_SECONDS", "RESOLUTION_POLL_SECONDS", "GAP_POLL_SECONDS"):
        monkeypatch.setattr(recorder_mod, nome, 0.01)
    monkeypatch.setattr(recorder_mod, "MarketDiscovery", _DescobertaVazia)
    return _RecorderSemRede


@pytest.fixture
def sigterm_protegido():
    """Se o recorder NÃO instalar handler (a versão antiga), o SIGTERM do
    teste cai aqui em vez de matar o pytest — e o teste falha nas asserções."""
    recebidos: list[int] = []
    anterior = signal.signal(signal.SIGTERM, lambda sig, _f: recebidos.append(sig))
    yield recebidos
    signal.signal(signal.SIGTERM, anterior)


def _registros_do_arquivo(pasta: Path) -> list[dict[str, Any]]:
    (arquivo,) = sorted(pasta.glob("*.jsonl.gz"))
    # `gzip.decompress` levanta se o trailer faltar: é a prova de gzip íntegro.
    return [json.loads(x) for x in gzip.decompress(arquivo.read_bytes()).splitlines()]


def _settings_sem_rede(pasta: Path) -> Settings:
    settings = Settings.load(pasta / "inexistente.yaml")
    settings.recorder.output_dir = str(pasta)
    settings.recorder.resync_intervalo_s = 0.01
    return settings


async def test_encerramento_relatorio_meta_antes_do_stop_e_bytes_medidos_depois(
    tmp_path, sem_rede
):
    """A ordem do encerramento, sem rede e em frações de segundo:

    1. todos os feeds param;
    2. `MonitorDeIntegridade.finalizar()` roda ANTES do resumo de integridade;
    3. o `recorder_relatorio` é escrito com o writer AINDA aberto — tem de ser,
       senão não entraria no arquivo —, e é o último registro;
    4. o writer drena e fecha; SÓ ENTÃO os bytes em disco são medidos para o
       `armazenamento` do relatório RETORNADO;
    5. o gzip final é íntegro (tem trailer)."""
    rec = sem_rede(_settings_sem_rede(tmp_path))
    relatorio = await rec.run(0.2)
    d = rec.diario

    paradas = [i for i, e in enumerate(d) if e.startswith("stop:")]
    assert len(paradas) == len(rec._feed_by_name)
    assert d.index("finalizar") > max(paradas)
    ultimo_resumo = len(d) - 1 - d[::-1].index("resumo_integridade")
    assert d.index("finalizar") < ultimo_resumo < d.index("meta:recorder_relatorio")
    # a medida do relatório RETORNADO é a última, com o writer já fechado
    assert d[-1] == "armazenamento:writer_fechado"

    linhas = _registros_do_arquivo(tmp_path)
    assert linhas[-1]["fonte"] == "recorder_relatorio"
    assert linhas[-1]["payload"]["desfecho"] == "completa"
    arquivo = next(tmp_path.glob("*.jsonl.gz"))
    assert relatorio["armazenamento"]["bytes_em_disco"] == arquivo.stat().st_size
    assert relatorio["desfecho"] == "completa"


async def test_sigterm_encerra_com_relatorio_gzip_integro_e_desfecho_interrompido(
    tmp_path, sem_rede, sigterm_protegido
):
    """`systemctl stop` = SIGTERM. Antes: morte pelo handler do SO, código 143,
    sem relatório, gzip sem trailer. Agora: encerramento completo, com
    `desfecho = interrompida_sigterm`, em vez de esperar os 30 s."""
    rec = sem_rede(_settings_sem_rede(tmp_path))
    loop = asyncio.get_running_loop()
    loop.call_later(0.2, os.kill, os.getpid(), signal.SIGTERM)

    inicio = time.monotonic()
    relatorio = await rec.run(30.0)

    assert time.monotonic() - inicio < 10.0
    assert sigterm_protegido == []  # o sinal foi do recorder, não do protetor
    assert relatorio["desfecho"] == "interrompida_sigterm"
    d = rec.diario
    assert sum(e.startswith("stop:") for e in d) == len(rec._feed_by_name)
    assert "finalizar" in d
    assert d[-1] == "armazenamento:writer_fechado"
    linhas = _registros_do_arquivo(tmp_path)  # levanta se sem trailer
    assert linhas[-1]["fonte"] == "recorder_relatorio"
    assert linhas[-1]["payload"]["desfecho"] == "interrompida_sigterm"


def test_main_com_sigterm_sai_143_e_nao_loga_recorder_encerrado(
    tmp_path, monkeypatch, sem_rede, sigterm_protegido
):
    """Ponta a ponta pelo `main()`: SIGTERM real no meio de uma rodada de 30 s.
    Sai 143, o log é `recorder INTERROMPIDO` — nunca `recorder encerrado`, que
    é a linha que o operador lê como rodada completa."""
    monkeypatch.setattr(recorder_mod, "Recorder", sem_rede)
    _disco_livre(monkeypatch, 10**15)
    logs: list[tuple[str, str]] = []
    log_real = recorder_mod.log

    class _LogEspia:
        def info(self, evento, **kw):
            logs.append(("info", evento))

        def error(self, evento, **kw):
            logs.append(("error", evento))

        def __getattr__(self, nome):
            return getattr(log_real, nome)

    monkeypatch.setattr(recorder_mod, "log", _LogEspia())
    temporizador = threading.Timer(0.3, os.kill, (os.getpid(), signal.SIGTERM))
    temporizador.start()
    try:
        codigo = recorder_mod.main(
            ["--duration", "30s", "--config", str(_config(tmp_path))]
        )
    finally:
        temporizador.cancel()

    assert codigo == 143
    assert sigterm_protegido == []
    assert ("error", "recorder INTERROMPIDO") in logs
    assert all(evento != "recorder encerrado" for _nivel, evento in logs)
    linhas = _registros_do_arquivo(tmp_path / "gravacoes")
    assert linhas[-1]["payload"]["desfecho"] == "interrompida_sigterm"


_HARNESS_SEM_REDE = """
import pathlib
import sys

import pulsearb.recorder.__main__ as m

pronto = pathlib.Path(sys.argv[1])


class _DescobertaVazia:
    def __init__(self, **_kw):
        pass

    async def discover(self):
        return []


class _RecorderSemRede(m.Recorder):
    def __init__(self, settings):
        super().__init__(settings)
        for feed in self._feed_by_name.values():
            async def parado():
                return None
            feed.start = parado
            feed.stop = parado

        async def ultimo_feed_no_ar():
            # O handler de sinal já está instalado quando os feeds sobem
            # (`run` o instala antes de `_coletar`): daqui em diante o SIGTERM
            # é do recorder. O arquivo avisa o processo pai.
            pronto.write_text("ok")

        self.poly.start = ultimo_feed_no_ar


for nome in ("DISCOVERY_INTERVAL_SECONDS", "RESOLUTION_POLL_SECONDS", "GAP_POLL_SECONDS"):
    setattr(m, nome, 0.01)
m.MarketDiscovery = _DescobertaVazia
m.Recorder = _RecorderSemRede
raise SystemExit(m.main(sys.argv[2:]))
"""


def test_sigterm_num_processo_controlado_sai_143_com_gzip_integro(tmp_path):
    """`systemctl stop` de verdade: o recorder roda num PROCESSO FILHO (o
    `main()` real, sem rede), o pai espera ele avisar que está no ar — sem
    `sleep` adivinhado — e manda SIGTERM ao PID dele.

    Prova: código 143 (não 0), `recorder INTERROMPIDO` no log e NENHUM
    `recorder encerrado`, e o gzip com trailer, com o relatório final de
    `desfecho = interrompida_sigterm` como último registro."""
    import subprocess
    import sys

    harness = tmp_path / "harness.py"
    harness.write_text(_HARNESS_SEM_REDE, encoding="utf-8")
    pronto = tmp_path / "pronto"
    cfg = _config(tmp_path)
    ambiente = {k: v for k, v in os.environ.items() if not k.startswith("PULSEARB_")}

    filho = subprocess.Popen(
        [sys.executable, str(harness), str(pronto), "--duration", "60s", "--config", str(cfg)],
        cwd=tmp_path,  # longe do `.env` do repositório
        env=ambiente,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        limite = time.monotonic() + 30.0
        while not pronto.exists():
            assert filho.poll() is None, filho.communicate()[0]
            assert time.monotonic() < limite, "o recorder filho não subiu em 30 s"
            time.sleep(0.02)
        filho.send_signal(signal.SIGTERM)
        saida, _ = filho.communicate(timeout=30)
    finally:
        if filho.poll() is None:
            filho.kill()

    assert filho.returncode == 143, saida
    eventos = [
        json.loads(linha).get("msg") for linha in saida.splitlines() if linha.startswith("{")
    ]
    assert "recorder INTERROMPIDO" in eventos, saida
    assert "recorder encerrado" not in eventos, saida
    linhas = _registros_do_arquivo(tmp_path / "gravacoes")  # levanta sem trailer
    assert linhas[-1]["fonte"] == "recorder_relatorio"
    assert linhas[-1]["payload"]["desfecho"] == "interrompida_sigterm"


async def test_excecao_num_laco_grava_relatorio_e_sobe(tmp_path, sem_rede):
    """Falha de verdade: o relatório final sai com `desfecho = excecao` e o
    gzip fecha — e a exceção SOBE, para o processo não sair 0."""
    rec = sem_rede(_settings_sem_rede(tmp_path))

    async def quebra(_deadline):
        raise RuntimeError("laço de lacunas quebrou")

    rec._gap_loop = quebra
    with pytest.raises(RuntimeError, match="laço de lacunas quebrou"):
        await rec.run(30.0)

    linhas = _registros_do_arquivo(tmp_path)
    assert linhas[-1]["payload"]["desfecho"] == "excecao"
    assert "laço de lacunas quebrou" in linhas[-1]["payload"]["erro"]
    assert sum(e.startswith("stop:") for e in rec.diario) == len(rec._feed_by_name)


# ──────────── diagnóstico: arquivo em gravação × gravação truncada × replay


def _registros_basicos() -> list[_Reg]:
    return [_book(1000, 1000, "0.49", "0.51"), _delta(2000, "0.50", "0.51")]


def test_arquivo_em_gravacao_e_excluido_e_o_diagnostico_sai_integro(tmp_path, capsys):
    fechado = _gravar(tmp_path / "pulsearb-20260925-1300.jsonl.gz", _registros_basicos())
    _envelhecer(fechado)
    _gravar(
        tmp_path / "pulsearb-20260925-1400.jsonl.gz",
        [_delta(3000, "0.50", "0.51")],
        cortar_trailer=True,
    )  # mtime agora: ainda em gravação

    assert diagnostico_main([str(tmp_path)]) == 0
    saida = json.loads(capsys.readouterr().out)
    leitura = saida["leitura_da_gravacao"]
    assert leitura["arquivos_em_gravacao_excluidos"] == ["pulsearb-20260925-1400.jsonl.gz"]
    assert leitura["arquivos_lidos"] == ["pulsearb-20260925-1300.jsonl.gz"]
    assert leitura["arquivos_truncados_ou_ilegiveis"] == []
    assert leitura["integra"] is True
    # a exclusão é declarada como HEURÍSTICA, não como validação:
    assert leitura["criterio_de_exclusao"].startswith("heurística")


def test_incluir_forca_a_leitura_do_arquivo_presumido_em_gravacao(tmp_path, capsys):
    """Opção explícita: o operador sabe que o arquivo recente e sem trailer é
    uma cópia de um que morreu no meio, e quer lê-lo. Ele entra — e, por ser
    truncado, a gravação sai NÃO íntegra (código 2), não limpa."""
    fechado = _gravar(tmp_path / "pulsearb-20260925-1300.jsonl.gz", _registros_basicos())
    _envelhecer(fechado)
    ativo = _gravar(
        tmp_path / "pulsearb-20260925-1400.jsonl.gz",
        [_delta(3000, "0.50", "0.51")],
        cortar_trailer=True,
    )

    codigo = diagnostico_main([str(tmp_path), "--incluir", ativo.name])
    leitura = json.loads(capsys.readouterr().out)["leitura_da_gravacao"]
    assert codigo == SAIDA_GRAVACAO_NAO_INTEGRA
    assert leitura["arquivos_lidos"] == [fechado.name, ativo.name]
    assert leitura["arquivos_em_gravacao_excluidos"] == []
    assert leitura["criterio_de_exclusao"] is None
    assert [a["arquivo"] for a in leitura["arquivos_truncados_ou_ilegiveis"]] == [ativo.name]


def test_varios_arquivos_so_o_mais_novo_ativo_sai(tmp_path):
    antigos = []
    for hora in ("1100", "1200", "1300"):
        caminho = _gravar(tmp_path / f"pulsearb-20260925-{hora}.jsonl.gz", _registros_basicos())
        _envelhecer(caminho)
        antigos.append(caminho)
    ativo = _gravar(
        tmp_path / "pulsearb-20260925-1400.jsonl.gz",
        _registros_basicos(),
        cortar_trailer=True,
    )
    lidos, excluidos = arquivos_da_gravacao(tmp_path, agora=time.time(), quieto_s=120)
    assert lidos == antigos
    assert excluidos == [ativo.name]


def test_em_gravacao_e_o_mais_novo_por_mtime_nao_por_nome(tmp_path):
    """`-1400-002` ordena ANTES de `-1400` pelo nome, mas é o mais novo."""
    velho = _gravar(tmp_path / "pulsearb-20260925-1400.jsonl.gz", _registros_basicos())
    _envelhecer(velho)
    novo = _gravar(
        tmp_path / "pulsearb-20260925-1400-002.jsonl.gz",
        _registros_basicos(),
        cortar_trailer=True,
    )

    lidos, excluidos = arquivos_da_gravacao(tmp_path, agora=time.time(), quieto_s=120)
    assert excluidos == [novo.name]
    assert lidos == [velho]


def test_gravacao_recem_encerrada_le_o_ultimo_arquivo_fechado(tmp_path, capsys):
    """Revisão do #199: recência NÃO prova arquivo aberto. Diagnosticar logo
    depois do fim da gravação: o último arquivo é recente mas FECHADO (trailer
    gzip, relatório final dentro) — tem de ser lido, com o resync pendente
    dele contado, e não excluído com `integra: true`."""
    velho = _gravar(tmp_path / "pulsearb-20260925-1300.jsonl.gz", _registros_basicos())
    _envelhecer(velho)
    ultimo = _gravar(
        tmp_path / "pulsearb-20260925-1400.jsonl.gz",
        [
            _marcador(3000),
            _Reg("recorder_relatorio", {"duracao_s": 7200.0}, 3100 * MS),
        ],
    )  # mtime agora, mas fechado

    lidos, excluidos = arquivos_da_gravacao(tmp_path, agora=time.time(), quieto_s=120)
    assert excluidos == []
    assert lidos == [velho, ultimo]

    assert diagnostico_main([str(tmp_path)]) == 0
    saida = json.loads(capsys.readouterr().out)
    assert saida["leitura_da_gravacao"]["arquivos_lidos"] == [velho.name, ultimo.name]
    assert saida["leitura_da_gravacao"]["integra"] is True
    # a última rotação entrou nas métricas: o resync dela está pendente.
    assert saida["com_replay_de_resync"]["tokens_aguardando_resync"] == 1


def test_gzip_truncado_antigo_e_rejeitado_como_gravacao_nao_como_replay(tmp_path, capsys):
    """Truncado e PARADO há uma hora = o recorder morreu. Sai código 2, o
    arquivo e o erro aparecem em `leitura_da_gravacao`, e o replay ainda
    roda sobre o que foi lido — sem exceção, sem se passar por limpo."""
    truncado = _gravar(
        tmp_path / "pulsearb-20260925-1300.jsonl.gz",
        [*_registros_basicos(), _marcador(2500)],
        cortar_trailer=True,
    )
    _envelhecer(truncado)

    assert diagnostico_main([str(tmp_path)]) == SAIDA_GRAVACAO_NAO_INTEGRA
    saida = json.loads(capsys.readouterr().out)
    leitura = saida["leitura_da_gravacao"]
    assert leitura["integra"] is False
    assert [a["arquivo"] for a in leitura["arquivos_truncados_ou_ilegiveis"]] == [
        truncado.name
    ]
    assert saida["leitura"].startswith("GRAVAÇÃO NÃO ÍNTEGRA")
    # o que veio antes da quebra foi lido e diagnosticado:
    assert saida["com_replay_de_resync"]["tokens_aguardando_resync"] == 1


def test_registro_fora_de_ordem_torna_a_gravacao_nao_integra(tmp_path, capsys):
    """Regra: inversão maior que o buffer do leitor sai na ordem ERRADA, e o
    monitor aplica deltas na ordem em que os recebe — o replay deixa de ser o
    que o recorder viu. Sai código 2 com a contagem, nunca 0 sem aviso."""
    registros = [_Reg("rtds", {}, ts) for ts in range(1, 5003)]
    registros.append(_Reg("rtds", {}, 0))  # chega depois de 5.002 mais novos
    arquivo = _gravar(tmp_path / "pulsearb-20260925-1300.jsonl.gz", registros)
    _envelhecer(arquivo)

    assert diagnostico_main([str(tmp_path)]) == SAIDA_GRAVACAO_NAO_INTEGRA
    saida = json.loads(capsys.readouterr().out)
    assert saida["leitura_da_gravacao"]["registros_fora_de_ordem"] == 1
    assert saida["leitura_da_gravacao"]["integra"] is False
    assert "1 registro(s) fora de ordem" in saida["leitura"]


def test_gravacao_em_ordem_e_deterministica_e_integra(tmp_path, capsys):
    """O outro lado da regra: sem inversão, duas leituras dão o MESMO
    diagnóstico, e a gravação sai íntegra (código 0)."""
    arquivo = _gravar(tmp_path / "pulsearb-20260925-1300.jsonl.gz", _registros_basicos())
    _envelhecer(arquivo)
    assert diagnostico_main([str(tmp_path)]) == 0
    primeira = json.loads(capsys.readouterr().out)
    assert diagnostico_main([str(tmp_path)]) == 0
    segunda = json.loads(capsys.readouterr().out)
    assert primeira == segunda
    assert primeira["leitura_da_gravacao"]["registros_fora_de_ordem"] == 0


def test_diretorio_sem_arquivo_fechado_nao_e_diagnostico(tmp_path, capsys):
    """O único arquivo está em gravação (recente, sem trailer): nada a ler."""
    _gravar(
        tmp_path / "pulsearb-20260925-1400.jsonl.gz",
        _registros_basicos(),
        cortar_trailer=True,
    )
    assert diagnostico_main([str(tmp_path)]) == SAIDA_GRAVACAO_NAO_INTEGRA
    assert json.loads(capsys.readouterr().out)["leitura"].startswith("NENHUM arquivo")


# ─────────────────── resync no fim da gravação: pendente honesto, sem falsa falha


def test_resync_sem_book_posterior_no_fim_nao_vira_divergencia_nem_comprometido():
    """O token fica pendente (o fim não é recuperação), mas NÃO vira
    divergência persistente nem token comprometido — e o forense diz que a
    gravação acabou 0 ms depois do resync."""
    d = diagnosticar([*_registros_basicos(), _marcador(2500)], replay_resync=True)
    m = d["metricas"]
    assert m["tokens_aguardando_resync"] == 1
    assert m["divergencias_persistentes"] == 0
    assert m["tokens_comprometidos"] == 0
    (pendente,) = d["forense_pendentes"]
    assert pendente["motivo"] == "sem_book_apos_o_ultimo_resync"
    assert pendente["ms_do_resync_ao_fim_da_gravacao"] == 0.0


def test_forense_mede_quanto_a_gravacao_correu_depois_do_resync():
    registros = [
        *_registros_basicos(),
        _book(1000, 1000, "0.30", "0.40", tok="outro"),
        _marcador(2500),
        _delta(7500, "0.31", "0.40", tok="outro"),  # a gravação segue 5 s
    ]
    (pendente,) = diagnosticar(registros, replay_resync=True)["forense_pendentes"]
    assert pendente["ms_do_resync_ao_fim_da_gravacao"] == 5000.0


def test_resync_seguido_de_book_recupera_o_token():
    d = diagnosticar(
        [*_registros_basicos(), _marcador(2500), _book(2600, 2550, "0.50", "0.52")],
        replay_resync=True,
    )
    assert d["metricas"]["tokens_aguardando_resync"] == 0
    assert d["forense_pendentes"] == []


# ────────────────────────────────────────────── snapshot fora de ordem (direto)


def test_snapshot_velho_sobre_livro_valido_e_rejeitado_e_contado():
    monitor = MonitorDeIntegridade()
    for r in [_book(1000, 1000, "0.49", "0.51"), _delta(2000, "0.50", "0.51")]:
        monitor.observar(r.payload, r.ts_wall_ns)
    velho = _book(2100, 1500, "0.10", "0.90")
    monitor.observar(velho.payload, velho.ts_wall_ns)

    estado = monitor.estados["tok"]
    assert estado.snapshots_fora_de_ordem == 1
    assert estado.livro.best_bid == 0.50  # o livro NÃO foi rebobinado
    monitor.finalizar()
    assert monitor.resumo()["alinhamento"]["snapshots_com_carimbo_fora_de_ordem"] == 1


# ─────────────────────────────────────────────── 0, 1 e 16 tokens no escopo


@dataclass
class _Janela:
    slug: str
    token_id_by_outcome: dict[str, str]


def _janelas(n: int) -> list[_Janela]:
    return [
        _Janela(slug=f"m{i:03d}", token_id_by_outcome={"Up": f"u{i}", "Down": f"d{i}"})
        for i in range(n)
    ]


def _recorder(tmp_path, limite: int | None) -> Recorder:
    settings = Settings(mode="SIM")
    settings.recorder.output_dir = str(tmp_path)
    settings.recorder.max_tokens_assinados = limite
    return Recorder(settings)


def test_16_tokens_sao_8_janelas_e_a_lista_explicita_vai_no_escopo(tmp_path):
    no_escopo, escopo = _recorder(tmp_path, 16)._aplicar_escopo(_janelas(76))
    assert len(no_escopo) == 8
    assert escopo["tokens_no_escopo"] == 16
    assert escopo["slugs_no_escopo"] == [f"m{i:03d}" for i in range(8)]
    assert slugs_no_escopo({"escopo": escopo}) == frozenset(escopo["slugs_no_escopo"])


def test_16_tokens_com_uma_janela_em_carencia_cabem_so_7_novas(tmp_path):
    no_escopo, escopo = _recorder(tmp_path, 16)._aplicar_escopo(
        _janelas(76), retidos_em_carencia=2
    )
    assert len(no_escopo) == 7
    assert escopo["tokens_ativos_estimados"] == 16


def test_zero_janelas_da_lista_vazia_explicita_e_nao_escopo_desconhecido(tmp_path):
    """Descoberta vazia: `[]` é "nada no escopo", não "escopo desconhecido"."""
    _, escopo = _recorder(tmp_path, 16)._aplicar_escopo([])
    assert escopo["slugs_no_escopo"] == []
    assert slugs_no_escopo({"escopo": escopo}) == frozenset()


def test_janela_de_um_token_so_conta_um(tmp_path):
    uma = [_Janela(slug="m000", token_id_by_outcome={"Up": "u0"})]
    no_escopo, escopo = _recorder(tmp_path, 16)._aplicar_escopo(uma)
    assert len(no_escopo) == 1
    assert escopo["tokens_no_escopo"] == 1


def test_diagnostico_de_zero_e_de_um_token_nao_quebra():
    vazio = diagnosticar([], replay_resync=True)["metricas"]
    assert vazio["tokens_aguardando_resync"] == 0
    assert vazio["divergencias_persistentes"] == 0
    um = diagnosticar(_registros_basicos(), replay_resync=True)["metricas"]
    assert um["tokens_comprometidos"] == 0
    assert um["tokens_aguardando_resync"] == 0
