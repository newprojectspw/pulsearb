"""Auditoria dos testes do recorder/replay (pós-#197/#198).

Cada cenário da rodada real da VPS reproduzido em segundos, sem rede, sem
relógio de horas: preflight com os números da v3/v4, precedência do env,
desfechos do processo (encerrado / recusado / interrompido / erro), gravação
truncada × arquivo em gravação no diagnóstico, resync no fim da gravação,
relatório final depois de feeds e flush, e os limites 0/1/16 tokens.
"""

from __future__ import annotations

import gzip
import json
import os
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
        return {}

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
    assert _main_com_run(tmp_path, monkeypatch, lambda: {}) == 0


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


# ────────────── relatório final DEPOIS de parar os feeds e drenar o writer


async def test_relatorio_final_e_o_ultimo_registro_depois_dos_feeds_e_do_flush(
    tmp_path, monkeypatch
):
    """`run()` inteiro, sem rede e em frações de segundo: feeds falsos,
    descoberta falsa, laços acelerados. O `recorder_relatorio` só é escrito
    depois de TODOS os feeds pararem, é o último registro do arquivo, o gzip
    fecha íntegro, e o armazenamento é medido depois do flush."""
    for nome in ("DISCOVERY_INTERVAL_SECONDS", "RESOLUTION_POLL_SECONDS", "GAP_POLL_SECONDS"):
        monkeypatch.setattr(recorder_mod, nome, 0.01)

    class _DescobertaVazia:
        def __init__(self, **_kw):
            pass

        async def discover(self):
            return []

    monkeypatch.setattr(recorder_mod, "MarketDiscovery", _DescobertaVazia)

    settings = Settings.load(tmp_path / "inexistente.yaml")
    settings.recorder.output_dir = str(tmp_path)
    settings.recorder.resync_intervalo_s = 0.01
    rec = Recorder(settings)
    ordem: list[str] = []

    for nome, feed in rec._feed_by_name.items():

        async def start(nome=nome):
            ordem.append(f"start:{nome}")

        async def stop(nome=nome):
            ordem.append(f"stop:{nome}")

        feed.start = start
        feed.stop = stop

    write_meta = rec._write_meta

    def espia(fonte, payload, **kw):
        ordem.append(f"meta:{fonte}")
        return write_meta(fonte, payload, **kw)

    rec._write_meta = espia

    relatorio = await rec.run(0.2)

    paradas = [i for i, e in enumerate(ordem) if e.startswith("stop:")]
    assert len(paradas) == len(rec._feed_by_name)
    assert ordem.index("meta:recorder_relatorio") > max(paradas)

    (arquivo,) = sorted(tmp_path.glob("*.jsonl.gz"))
    linhas = [json.loads(x) for x in gzip.decompress(arquivo.read_bytes()).splitlines()]
    assert linhas[-1]["fonte"] == "recorder_relatorio"
    assert relatorio["armazenamento"]["bytes_em_disco"] == arquivo.stat().st_size


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
