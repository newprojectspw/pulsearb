"""Resync sem snapshot: o token não pode ficar cego depois de um resync.

Rodada v5 na VPS: 26 tokens terminaram sem `book` depois do último resync;
24 resolveram (não é falha), 2 seguiram ATIVOS com `book_apos_resync = 0`. A
causa: `_resync_loop` tirava o token de `a_resincronizar` ANTES do
desassina→reassina. Se o servidor aceitava o `subscribe` e não mandava o
snapshot, ninguém tentava de novo.

Aqui: o acompanhamento pós-resync (prazo + backoff + reenvio com motivo
`resync_sem_snapshot`), a saída por `book`/resolução/assinatura, o teto de
assinaturas durante os reenvios, a ordem marcador→snapshot no arquivo, a
leitura do `market_resolved` REAL (`assets_ids`) e a separação resolvido ×
cego no diagnóstico offline.

Sem rede externa: o WS é o servidor falso em 127.0.0.1 de `test_feeds_ws`, e o
relógio do acompanhamento é injetado (`_passo_de_resync(agora_mono)`) — nada
dorme esperando prazo, exceto um teste de ponta a ponta de décimos de segundo.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from scripts.diagnostico_de_reconciliacao import diagnosticar
from tests.test_feeds_ws import _wait_for, server  # noqa: F401
from tests.test_m2_recorder import FakeDiscovery, _janela

from pulsearb.analysis.integrity import MonitorDeIntegridade
from pulsearb.feeds.base import FeedEvent
from pulsearb.recorder import __main__ as recorder_main
from pulsearb.recorder.__main__ import Recorder
from pulsearb.recorder.writer import (
    CANAL_BOOK,
    FONTE_RESOLUCAO_SINTETICA,
    FONTE_RESYNC,
)
from pulsearb.settings import Settings

FIXTURE_RESOLVIDO = Path(__file__).parent / "fixtures" / "clob_ws_market_resolved.json"


@pytest.fixture
def recorder(server, tmp_path):  # noqa: F811
    settings = Settings.load("config.yaml")
    settings.endpoints.rtds_ws = server.url
    settings.endpoints.clob_market_ws = server.url
    settings.recorder.output_dir = str(tmp_path)
    # prazo curto e teto baixo: o relógio é injetado, então os números são
    # só a escala do teste
    settings.recorder.timeout_snapshot_pos_resync_s = 1.0
    settings.recorder.timeout_snapshot_pos_resync_max_s = 4.0
    rec = Recorder(settings)
    rec.binance.url = server.url
    return rec


def _book(token: str, ts_ms: int = 1000) -> dict[str, Any]:
    return {
        "event_type": "book",
        "asset_id": token,
        "timestamp": str(ts_ms),
        "bids": [{"price": "0.49", "size": "100"}],
        "asks": [{"price": "0.51", "size": "100"}],
    }


def _evento(payload: Any) -> FeedEvent:
    return FeedEvent(
        source="poly_ws",
        ts_mono_ns=time.monotonic_ns(),
        ts_wall_ns=time.time_ns(),
        raw=json.dumps(payload).encode(),
        parsed=payload,
    )


def _resolvido_real() -> dict[str, Any]:
    """A linha CAPTURADA em produção (API_NOTES §6.1c) — não uma forma
    imaginada: `assets_ids` no plural, sem `asset_id`."""
    bruto = json.loads(FIXTURE_RESOLVIDO.read_text(encoding="utf-8"))
    return bruto["linha_jsonl"]["payload"]


def _linhas(recorder: Recorder) -> list[dict[str, Any]]:
    linhas: list[dict[str, Any]] = []
    for caminho in sorted(recorder.writer.arquivos_escritos):
        with gzip.open(caminho, "rb") as handle:
            linhas.extend(json.loads(linha) for linha in handle if linha.strip())
    return linhas


def _frames_de_subscribe(server, token: str) -> int:  # noqa: F811
    return sum(
        1
        for texto in server.received
        if texto.strip() not in ("PING", "")
        and json.loads(texto).get("operation") == "subscribe"
        and token in json.loads(texto).get("assets_ids", [])
    )


# ───────────────────────────────── prazo vencido sem book: volta para a fila


async def test_subscribe_aceito_sem_book_volta_para_a_fila_e_o_reenvio_sai(
    recorder, server  # noqa: F811
):
    """O subscribe "dá certo" (o frame sai), mas o `book` nunca vem. Vencido o
    prazo, o token volta a `a_resincronizar` com `resync_sem_snapshot` e o
    reenvio SAI no mesmo passo: novo marcador, novo subscribe no fio."""
    # o nome do motivo é contrato: relatório e runbook o leem
    assert recorder_main.MOTIVO_RESYNC_SEM_SNAPSHOT == "resync_sem_snapshot"
    await recorder.writer.start()
    await recorder.poly.start()
    try:
        await _wait_for(lambda: recorder.poly.connected)
        await recorder.poly.subscribe(["tokA"])
        await _wait_for(lambda: _frames_de_subscribe(server, "tokA") == 1)
        recorder.a_resincronizar.add("tokA")

        await recorder._passo_de_resync(0.0)
        assert recorder.resyncs == 1
        assert recorder.aguardando_snapshot["tokA"].tentativa == 1
        assert "tokA" not in recorder.a_resincronizar
        await _wait_for(lambda: _frames_de_subscribe(server, "tokA") == 2)

        # antes do prazo: nada acontece
        await recorder._passo_de_resync(0.99)
        assert recorder.resyncs == 1
        assert recorder.motivos_de_resync[recorder_main.MOTIVO_RESYNC_SEM_SNAPSHOT] == 0

        # prazo vencido, sem book e sem resolução: reenfileira E reassina
        await recorder._passo_de_resync(1.0)
        assert recorder.motivos_de_resync[recorder_main.MOTIVO_RESYNC_SEM_SNAPSHOT] == 1
        assert recorder.reenvios_sem_snapshot == 1
        assert recorder.resyncs == 2
        assert recorder.tentativas_de_resync["tokA"] == 2
        acompanhamento = recorder.aguardando_snapshot["tokA"]
        assert acompanhamento.tentativa == 2
        assert acompanhamento.timeout_s == 2.0  # o prazo dobrou
        assert "tokA" in recorder.poly.token_ids
        await _wait_for(lambda: _frames_de_subscribe(server, "tokA") == 3)

        resumo = recorder.integridade_resumo()
        assert resumo["tokens_aguardando_snapshot_pos_resync"] == 1
        assert resumo["reenvios_por_resync_sem_snapshot"] == 1
        assert resumo["encerrados_por_resolucao_sem_book"] == 0
        assert resumo["tentativas_pendentes_por_token"] == {"tokA": 2}
        assert resumo["max_tentativas_de_resync_por_token"] == 2
        assert resumo["motivos_de_resync"][recorder_main.MOTIVO_RESYNC_SEM_SNAPSHOT] == 1
    finally:
        await recorder.poly.stop()
        await recorder.writer.stop()

    marcadores = [li["payload"] for li in _linhas(recorder) if li["fonte"] == FONTE_RESYNC]
    assert [m["tokens"] for m in marcadores] == [["tokA"], ["tokA"]]
    assert [m["tentativa_por_token"] for m in marcadores] == [{"tokA": 1}, {"tokA": 2}]
    assert marcadores[0]["ts_perda_ns"] < marcadores[1]["ts_perda_ns"]


async def test_laco_real_reenvia_quando_o_book_nao_vem(recorder, server):  # noqa: F811
    """Ponta a ponta pelo `_resync_loop` de verdade, com prazos de décimos de
    segundo. Com o código de antes o token era reassinado UMA vez e esquecido
    — `resyncs == 1` para sempre. O teto superior prova o backoff: sem ele,
    prazo fixo de 50 ms em 0,4 s dariam ~8 reenvios."""
    recorder.settings.recorder.resync_intervalo_s = 0.01
    recorder.settings.recorder.timeout_snapshot_pos_resync_s = 0.05
    recorder.settings.recorder.timeout_snapshot_pos_resync_max_s = 1.0
    await recorder.writer.start()
    await recorder.poly.start()
    try:
        await _wait_for(lambda: recorder.poly.connected)
        await recorder.poly.subscribe(["tokA"])
        recorder.a_resincronizar.add("tokA")
        await recorder._resync_loop(time.monotonic() + 0.4)
    finally:
        await recorder.poly.stop()
        await recorder.writer.stop()

    # 0,05 → 0,1 → 0,2: no máximo 4 envios em 0,4 s (+1 de folga de agenda)
    assert 2 <= recorder.resyncs <= 5
    assert recorder.motivos_de_resync[recorder_main.MOTIVO_RESYNC_SEM_SNAPSHOT] >= 1
    assert "tokA" in recorder.poly.token_ids
    assert "tokA" in recorder.aguardando_snapshot


# ───────────────────────────────────────── book chegou: sai sem reenvio


async def test_book_apos_o_resync_encerra_o_acompanhamento_sem_reenvio(
    recorder, server  # noqa: F811
):
    """O snapshot de recuperação chega pelo fio e o monitor o aplica: o token
    sai do acompanhamento e NÃO é reenfileirado, mesmo com o prazo vencido."""
    await recorder.writer.start()
    await recorder.poly.start()
    try:
        await _wait_for(lambda: recorder.poly.connected)
        await recorder.poly.subscribe(["tokA"])
        recorder.a_resincronizar.add("tokA")
        await recorder._passo_de_resync(0.0)
        ts_perda = recorder.aguardando_snapshot["tokA"].ts_perda_ns
        assert "tokA" in recorder.integridade.aguardando_resync

        await server.broadcast(json.dumps(_book("tokA")))
        await _wait_for(lambda: recorder.integridade.recuperado_desde("tokA", ts_perda))

        await recorder._passo_de_resync(50.0)  # muito depois do prazo
    finally:
        await recorder.poly.stop()
        await recorder.writer.stop()

    assert "tokA" not in recorder.aguardando_snapshot
    assert "tokA" not in recorder.a_resincronizar
    assert recorder.recuperados_apos_resync == 1
    assert recorder.resyncs == 1
    assert recorder.motivos_de_resync[recorder_main.MOTIVO_RESYNC_SEM_SNAPSHOT] == 0
    assert recorder.integridade_resumo()["tokens_aguardando_snapshot_pos_resync"] == 0


def test_book_ANTERIOR_a_perda_nao_conta_como_recuperacao():
    """O critério é o do monitor, e ele é fail-closed: um `book` chegado
    antes do instante da perda não prova que o resync valeu."""
    monitor = MonitorDeIntegridade()
    monitor.observar(_book("tokA"), 1_000)
    assert monitor.recuperado_desde("tokA", 1_000) is True
    monitor.marcar_perda("tokA")
    assert monitor.recuperado_desde("tokA", 2_000) is False  # perda descartou o livro
    monitor.observar(_book("tokA", ts_ms=2000), 1_500)  # chegou ANTES de 2_000
    assert monitor.recuperado_desde("tokA", 2_000) is False
    monitor.marcar_perda("tokA")
    monitor.observar(_book("tokA", ts_ms=3000), 2_500)
    assert monitor.recuperado_desde("tokA", 2_000) is True
    assert monitor.recuperado_desde("nunca_visto", 0) is False


# ───────────────────────────────────── resolução sem book: sai sem reenvio


def test_market_resolved_real_do_fio_marca_os_dois_tokens_como_resolvidos(recorder):
    """A linha capturada tem `assets_ids` (plural) e NÃO tem `asset_id`. O
    recorder lia só `asset_id`: pelo WS, nada virava resolvido."""
    evento = _resolvido_real()
    assert "asset_id" not in evento
    recorder._contar_evento_poly(_evento(evento))
    assert set(evento["assets_ids"]) <= recorder.resolvidos


async def test_resolucao_sem_book_encerra_sem_nova_tentativa(recorder):
    """Mercado resolvido não manda mais `book`: o token sai do acompanhamento
    e da fila, e não é reassinado — nem por prazo, nem por pedido pendente."""
    evento = _resolvido_real()
    token = evento["assets_ids"][0]
    await recorder.poly.subscribe([token])  # dublê: sem conexão, só estado
    recorder.a_resincronizar.add(token)
    await recorder._passo_de_resync(0.0)
    assert recorder.resyncs == 1

    recorder._contar_evento_poly(_evento(evento))  # a resolução chega, sem book
    recorder.a_resincronizar.add(token)  # e até um pedido de resync pendente
    await recorder._passo_de_resync(100.0)  # muito depois do prazo

    assert token not in recorder.aguardando_snapshot
    assert token not in recorder.a_resincronizar
    assert recorder.encerrados_por_resolucao_sem_book == 1
    assert recorder.resyncs == 1
    assert recorder.motivos_de_resync[recorder_main.MOTIVO_RESYNC_SEM_SNAPSHOT] == 0
    assert recorder.integridade_resumo()["encerrados_por_resolucao_sem_book"] == 1


async def test_token_que_sai_da_assinatura_sai_do_acompanhamento(recorder):
    await recorder.poly.subscribe(["tokA"])
    recorder.a_resincronizar.add("tokA")
    await recorder._passo_de_resync(0.0)
    await recorder.poly.unsubscribe(["tokA"])  # a rotação o desassinou

    await recorder._passo_de_resync(100.0)

    assert "tokA" not in recorder.aguardando_snapshot
    assert "tokA" not in recorder.a_resincronizar
    assert "tokA" not in recorder.poly.token_ids  # e não é reassinado
    assert recorder.acompanhamentos_fora_da_assinatura == 1
    assert recorder.resyncs == 1


# ─────────────────────────────────────── sem laço agressivo, sem furar o teto


async def test_backoff_limita_os_reenvios_e_o_teto_nunca_fura(recorder):
    """Com o `book` NUNCA chegando, 20 s de relógio com passos de 0,25 s: o
    prazo 1 → 2 → 4 (teto) espaça os envios em 0, 1, 3, 7, 11, 15, 19 s — sete
    por token, e não 21 (prazo fixo) nem 81 (um por passo). E o resync troca a
    assinatura do MESMO token: o total nunca passa de `max_tokens_assinados`."""
    recorder.settings.recorder.max_tokens_assinados = 2
    await recorder._discovery_cycle(FakeDiscovery([[_janela(1)]]))
    assert set(recorder.poly.token_ids) == {"up1", "dn1"}

    pico = {"max": 0}
    envios: list[tuple[float, str]] = []
    subscribe_original = recorder.poly.subscribe
    relogio = {"agora": 0.0}

    async def espia(tokens):
        pico["max"] = max(pico["max"], len(set(recorder.poly.token_ids) | set(tokens)))
        envios.extend((relogio["agora"], token) for token in tokens)
        return await subscribe_original(tokens)

    recorder.poly.subscribe = espia
    recorder.a_resincronizar.update({"up1", "dn1"})
    for passo in range(81):
        relogio["agora"] = passo * 0.25
        await recorder._passo_de_resync(relogio["agora"])
        assert len(recorder.poly.token_ids) <= 2

    assert pico["max"] <= 2
    assert [t for t, token in envios if token == "up1"] == [0, 1, 3, 7, 11, 15, 19]
    assert recorder.tentativas_de_resync == {"up1": 7, "dn1": 7}
    assert recorder.reenvios_sem_snapshot == 12
    assert recorder.aguardando_snapshot["up1"].timeout_s == 4.0  # parou no teto


async def test_descoberta_nao_intercala_com_o_resync_e_o_teto_nao_fura(recorder):
    """A rotação chega NO MEIO de um resync (entre o unsubscribe e o
    subscribe, que têm `await`). Sem a trava, a descoberta via o escopo sem os
    tokens em resync, assinava a janela nova, e o subscribe do resync
    devolvia a antiga por cima: 4 tokens com teto 2."""
    recorder.settings.recorder.max_tokens_assinados = 2
    descoberta = FakeDiscovery([[_janela(1)], [_janela(2)]])  # endDate passado
    await recorder._discovery_cycle(descoberta)
    assert set(recorder.poly.token_ids) == {"up1", "dn1"}

    pico = {"max": 0}
    subscribe_original = recorder.poly.subscribe
    unsubscribe_original = recorder.poly.unsubscribe

    async def espia_subscribe(tokens):
        await asyncio.sleep(0)  # ponto de suspensão, como o frame no fio
        pico["max"] = max(pico["max"], len(set(recorder.poly.token_ids) | set(tokens)))
        return await subscribe_original(tokens)

    async def espia_unsubscribe(tokens):
        await asyncio.sleep(0)
        return await unsubscribe_original(tokens)

    recorder.poly.subscribe = espia_subscribe
    recorder.poly.unsubscribe = espia_unsubscribe
    recorder.a_resincronizar.update({"up1", "dn1"})

    await asyncio.gather(
        recorder._passo_de_resync(0.0), recorder._discovery_cycle(descoberta)
    )
    assert pico["max"] <= 2
    assert set(recorder.poly.token_ids) == {"up2", "dn2"}

    await recorder._passo_de_resync(100.0)
    assert recorder.aguardando_snapshot == {}
    assert recorder.acompanhamentos_fora_da_assinatura == 2
    assert recorder.a_resincronizar == set()


# ──────────────────────────── ordem no arquivo: marcador de cada tentativa antes


async def test_marcador_de_cada_tentativa_precede_o_snapshot_no_arquivo(
    recorder, server  # noqa: F811
):
    """Tentativa 1 sem book, tentativa 2 (reenvio) e o book que ela dispara.
    No arquivo, os dois `resync_book` vêm ANTES do `book`, pelo canal sem
    perda, e o replay com `aplicar_marcador_de_resync` termina com o livro
    recuperado — e sem o book, termina aguardando, como deve."""
    canais: list[str] = []
    submit_original = recorder.writer.submit

    def espia_submit(envelope, *, canal="padrao"):
        if envelope.fonte == FONTE_RESYNC:
            canais.append(canal)
        return submit_original(envelope, canal=canal)

    recorder.writer.submit = espia_submit
    await recorder.writer.start()
    await recorder.poly.start()
    try:
        await _wait_for(lambda: recorder.poly.connected)
        await recorder.poly.subscribe(["tokA"])
        recorder.a_resincronizar.add("tokA")
        await recorder._passo_de_resync(0.0)
        await recorder._passo_de_resync(1.0)  # prazo vencido: reenvio
        assert recorder.resyncs == 2
        ts_perda = recorder.aguardando_snapshot["tokA"].ts_perda_ns

        await server.broadcast(json.dumps(_book("tokA")))
        await _wait_for(lambda: recorder.integridade.recuperado_desde("tokA", ts_perda))
        await recorder._passo_de_resync(2.0)
        assert recorder.recuperados_apos_resync == 1
    finally:
        await recorder.poly.stop()
        await recorder.writer.stop()

    assert canais == [CANAL_BOOK, CANAL_BOOK]
    linhas = [
        li
        for li in _linhas(recorder)
        if li["fonte"] == FONTE_RESYNC
        or (li["fonte"] == "poly_ws" and li["payload"].get("event_type") == "book")
    ]
    assert [li["fonte"] for li in linhas] == [FONTE_RESYNC, FONTE_RESYNC, "poly_ws"]
    assert linhas[1]["payload"]["ts_perda_ns"] <= linhas[2]["ts_wall_ns"]

    def replay(registros: list[dict[str, Any]]) -> MonitorDeIntegridade:
        monitor = MonitorDeIntegridade()
        for li in registros:
            if li["fonte"] == FONTE_RESYNC:
                monitor.aplicar_marcador_de_resync(li["payload"])
            else:
                monitor.observar(li["payload"], li["ts_wall_ns"])
        return monitor

    completo = replay(linhas)
    assert "tokA" not in completo.aguardando_resync
    assert "tokA" in completo.com_snapshot
    sem_book = replay(linhas[:2])
    assert "tokA" in sem_book.aguardando_resync


# ─────────────────────────────── diagnóstico: resolvido depois × cego de verdade


@dataclass
class _Reg:
    fonte: str
    payload: Any
    ts_wall_ns: int = 0


def _ms(ts_ms: int) -> int:
    return ts_ms * 1_000_000


def _registros_pendentes() -> list[_Reg]:
    """Quatro tokens com livro, perda (marcador) e NENHUM book depois.

    A: resolve depois pela Gamma (registro sintético `resolucao_via_gamma`);
    B: resolve depois pelo WS, na forma REAL (`assets_ids`);
    C: não resolve — o cego de verdade da rodada v5;
    D: só teve resolução ANTES do último resync (recorder antigo)."""
    registros = [
        _Reg("poly_ws", _book(t, 1000), _ms(1000)) for t in ("A", "B", "C", "D")
    ]
    resolvido = dict(_resolvido_real())
    resolvido["assets_ids"] = ["D", "D2"]
    registros.append(_Reg("poly_ws", resolvido, _ms(1500)))
    registros.append(
        _Reg(FONTE_RESYNC, {"tokens": ["A", "B", "C", "D"], "ts_perda_ns": _ms(2000)}, _ms(2000))
    )
    registros.append(
        _Reg(
            FONTE_RESOLUCAO_SINTETICA,
            {
                "_sintetico": True,
                "event_type": "market_resolved",
                "asset_id": "A",
                "winning_outcome": "Up",
            },
            _ms(3000),
        )
    )
    resolvido_b = dict(_resolvido_real())
    resolvido_b["assets_ids"] = ["B", "B2"]
    registros.append(_Reg("poly_ws", resolvido_b, _ms(4000)))
    return registros


def test_diagnostico_separa_pendente_resolvido_do_sem_book_e_as_contagens_batem():
    d = diagnosticar(_registros_pendentes(), replay_resync=True)
    m = d["metricas"]
    por_token = {p["token"]: p for p in d["forense_pendentes"]}

    # ninguém sai da lista: a contagem total continua honesta
    assert m["tokens_aguardando_resync"] == 4
    assert set(por_token) == {"A", "B", "C", "D"}

    assert por_token["A"]["resolvido_depois"] is True
    assert por_token["A"]["ts_resolucao_ns"] == _ms(3000)
    assert por_token["B"]["resolvido_depois"] is True
    assert por_token["B"]["ts_resolucao_ns"] == _ms(4000)
    assert por_token["C"]["resolvido_depois"] is False
    assert por_token["C"]["ts_resolucao_ns"] is None
    assert por_token["C"]["motivo"] == "sem_book_apos_o_ultimo_resync"
    assert por_token["D"]["resolvido_depois"] is False
    assert por_token["D"]["resolvido_antes_do_ultimo_resync"] is True
    assert por_token["D"]["ts_resolucao_ns"] == _ms(1500)

    assert m["pendentes_resolvidos_depois"] == 2
    assert m["pendentes_sem_book_nao_resolvidos"] == 1
    # D fica fora das duas categorias, mas dentro do total
    assert (
        m["pendentes_resolvidos_depois"] + m["pendentes_sem_book_nao_resolvidos"] + 1
        == m["tokens_aguardando_resync"]
    )


def test_diagnostico_sem_pendente_tem_as_contagens_zeradas():
    d = diagnosticar(_registros_pendentes(), replay_resync=False)
    assert d["metricas"]["tokens_aguardando_resync"] == 0
    assert d["metricas"]["pendentes_resolvidos_depois"] == 0
    assert d["metricas"]["pendentes_sem_book_nao_resolvidos"] == 0
