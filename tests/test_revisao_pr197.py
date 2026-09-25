"""Revisão do PR #197: ordem do marcador de resync, escopo real do recorder,
`finalizar()` no relatório, buraco de livro com snapshot atrasado e teto de
assinaturas durante a rotação.

Cada teste aqui reproduz o defeito que a revisão apontou; sem o conserto, ele
falha.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from scripts.diagnostico_de_reconciliacao import _leitura, diagnosticar

from pulsearb.analysis.integrity import MonitorDeIntegridade
from pulsearb.recorder.writer import FONTE_RESYNC
from pulsearb.replay.escopo import slugs_no_escopo

MS = 1_000_000


@dataclass
class _Reg:
    fonte: str
    payload: Any
    ts_wall_ns: int = 0


def _book(ts_ms: int, carimbo: int, bid: str, ask: str, tok: str = "tok") -> _Reg:
    return _Reg(
        fonte="poly_ws",
        payload={
            "event_type": "book",
            "asset_id": tok,
            "timestamp": str(carimbo),
            "bids": [{"price": bid, "size": "100"}],
            "asks": [{"price": ask, "size": "100"}],
        },
        ts_wall_ns=ts_ms * MS,
    )


def _delta(ts_ms: int, *, price: str, best_bid: str, best_ask: str) -> _Reg:
    return _Reg(
        fonte="poly_ws",
        payload={
            "event_type": "price_change",
            "market": "0xabc",
            "timestamp": str(ts_ms),
            "price_changes": [
                {
                    "asset_id": "tok",
                    "price": price,
                    "size": "10",
                    "side": "BUY",
                    "best_bid": best_bid,
                    "best_ask": best_ask,
                }
            ],
        },
        ts_wall_ns=ts_ms * MS,
    )


def _marcador(ts_registro_ms: int, ts_perda_ms: int | None) -> _Reg:
    payload: dict[str, Any] = {"_sintetico": True, "tokens": ["tok"]}
    if ts_perda_ms is not None:
        payload["ts_perda_ns"] = ts_perda_ms * MS
    return _Reg(fonte=FONTE_RESYNC, payload=payload, ts_wall_ns=ts_registro_ms * MS)


def _rodar(registros: list[_Reg]) -> MonitorDeIntegridade:
    monitor = MonitorDeIntegridade()
    for rec in registros:
        if rec.fonte == FONTE_RESYNC:
            monitor.aplicar_marcador_de_resync(rec.payload)
        else:
            monitor.observar(rec.payload, rec.ts_wall_ns)
    monitor.finalizar()
    return monitor


def _estado_final(monitor: MonitorDeIntegridade) -> dict[str, Any]:
    e = monitor.estados["tok"]
    return {
        "com_snapshot": e.com_snapshot,
        "aguardando": sorted(monitor.aguardando_resync),
        "best_bid": e.livro.best_bid,
        "best_ask": e.livro.best_ask,
        "observacoes_sem_snapshot": monitor.observacoes_sem_snapshot,
        "persistentes": e.persistentes,
        "snapshots_fora_de_ordem": e.snapshots_fora_de_ordem,
        "ms_sem_livro": e.ms_sem_livro,
    }


# ─────────────────────────────────────── P1: ordem marcador × book de recuperação

INICIO = [
    _book(1000, 1000, "0.49", "0.51"),
    _delta(2000, price="0.50", best_bid="0.50", best_ask="0.51"),
]
# Recuperação com carimbo ATRASADO (1500 < 2000): o `timestamp` do book é a
# última mutação, não o envio. Chega às 3100 ms; a perda foi marcada às 3000.
RECUPERACAO = _book(3100, 1500, "0.60", "0.62")
DEPOIS = _delta(4000, price="0.61", best_bid="0.61", best_ask="0.62")


def test_marcador_antes_e_depois_do_book_dao_o_mesmo_estado_final():
    """`resync_book → book → price_change` ≡ `book → resync_book → price_change`."""
    ordem_certa = _rodar([*INICIO, _marcador(3000, 3000), RECUPERACAO, DEPOIS])
    ordem_trocada = _rodar([*INICIO, RECUPERACAO, _marcador(3200, 3000), DEPOIS])

    final = _estado_final(ordem_certa)
    assert final == _estado_final(ordem_trocada)
    # e o estado é o da recuperação, não um token cego:
    assert final["com_snapshot"] is True
    assert final["aguardando"] == []
    assert final["best_bid"] == 0.61
    assert final["observacoes_sem_snapshot"] == 0
    assert ordem_trocada.resyncs_ja_recuperados_no_replay == 1


def test_ordem_trocada_com_book_de_carimbo_fresco_tambem_equivale():
    fresco = _book(3100, 3050, "0.60", "0.62")
    ordem_certa = _rodar([*INICIO, _marcador(3000, 3000), fresco, DEPOIS])
    ordem_trocada = _rodar([*INICIO, fresco, _marcador(3200, 3000), DEPOIS])
    assert _estado_final(ordem_certa) == _estado_final(ordem_trocada)
    assert _estado_final(ordem_trocada)["aguardando"] == []


def test_book_de_recuperacao_ja_seguido_de_deltas_nao_e_apagado():
    """Book pós-perda + deltas antes do marcador: a recuperação valeu."""
    fresco = _book(3100, 3050, "0.60", "0.62")
    monitor = _rodar(
        [*INICIO, fresco, DEPOIS, _marcador(4100, 3000)]
    )
    assert monitor.aguardando_resync == set()
    assert monitor.estados["tok"].livro.best_bid == 0.61


def test_marcador_sem_book_posterior_marca_perda():
    """Fail-closed: sem snapshot pós-perda, o token fica aguardando."""
    monitor = _rodar([*INICIO, _marcador(3000, 3000)])
    assert monitor.aguardando_resync == {"tok"}


def test_marcador_legado_marca_perda_e_e_contado():
    """Sem `ts_perda_ns` a ordem é ambígua: marca a perda (conservador) e
    CONTA, para o relatório dizer que o replay daquela gravação é ambíguo."""
    monitor = _rodar([*INICIO, RECUPERACAO, _marcador(3200, None)])
    assert monitor.aguardando_resync == {"tok"}
    assert monitor.marcadores_de_resync_legados == 1


def test_diagnostico_reproduz_as_duas_ordens_igual():
    certa = diagnosticar(
        [*INICIO, _marcador(3000, 3000), RECUPERACAO, DEPOIS], replay_resync=True
    )
    trocada = diagnosticar(
        [*INICIO, RECUPERACAO, _marcador(3200, 3000), DEPOIS], replay_resync=True
    )
    for chave in (
        "tokens_aguardando_resync",
        "observacoes_sem_snapshot",
        "divergencias_persistentes",
        "tokens_comprometidos",
    ):
        assert certa["metricas"][chave] == trocada["metricas"][chave], chave
    assert certa["metricas"]["tokens_aguardando_resync"] == 0


# ────────────────────────────── P2: snapshot atrasado não zera o buraco de livro


def test_snapshot_de_recuperacao_atrasado_nao_zera_o_tempo_sem_livro():
    """Perda às 2000 (último carimbo), book de carimbo 1500, delta fresco às
    4000: o buraco é 2000 ms. Antes, `fechar_sem_livro(1500)` dava ZERO."""
    monitor = _rodar([*INICIO, _marcador(3000, 3000), RECUPERACAO, DEPOIS])
    estado = monitor.estados["tok"]
    assert estado.ms_sem_livro == 2000.0
    assert estado.fracao_ruim > 0


def test_snapshot_de_recuperacao_fresco_fecha_o_buraco_no_proprio_carimbo():
    fresco = _book(3100, 3050, "0.60", "0.62")
    monitor = _rodar([*INICIO, _marcador(3000, 3000), fresco])
    assert monitor.estados["tok"].ms_sem_livro == 1050.0


# ─────────────────────────────────────────────── diagnóstico: leitura e forense


def _metricas(p: int, c: int, a: int, s: int = 0) -> dict[str, int]:
    return {
        "divergencias_persistentes": p,
        "tokens_comprometidos": c,
        "tokens_aguardando_resync": a,
        "snapshots_fora_de_ordem": s,
    }


def test_leitura_nao_chama_de_reducao_o_que_subiu():
    """Os números da VPS: persistentes 12→6, comprometidos 2→6, aguardando 0→40."""
    texto = _leitura(_metricas(12, 2, 0), _metricas(6, 6, 40))
    assert "MAS AUMENTA" in texto
    assert "NÃO é fiel" in texto
    assert "SEM aumentar" not in texto
    assert "comprometidos 2→6" in texto
    assert "aguardando_resync 0→40" in texto
    assert "persistentes 12→6" in texto


def test_leitura_tem_um_veredito_por_caso():
    aumenta = _leitura(_metricas(5, 1, 0), _metricas(7, 1, 0))
    limpa = _leitura(_metricas(5, 3, 0), _metricas(2, 1, 0))
    igual = _leitura(_metricas(5, 1, 0), _metricas(5, 1, 0))
    assert "AUMENTA as divergências persistentes" in aumenta
    assert "SEM aumentar" in limpa
    assert "NÃO muda os persistentes" in igual
    assert len({aumenta, limpa, igual}) == 3


def test_forense_lista_o_pendente_e_nao_trata_o_fim_como_recuperacao():
    d = diagnosticar([*INICIO, _marcador(3000, 3000)], replay_resync=True)
    assert d["metricas"]["tokens_aguardando_resync"] == 1
    (pendente,) = d["forense_pendentes"]
    assert pendente["token"] == "tok"
    assert pendente["ts_ultimo_resync_ns"] == 3000 * MS
    assert pendente["houve_book_posterior"] is False
    assert pendente["ts_book_posterior_ns"] is None
    assert pendente["motivo"] == "sem_book_apos_o_ultimo_resync"


def test_main_traz_detalhe_com_replay(tmp_path, capsys):
    import gzip
    import json

    import orjson
    from scripts.diagnostico_de_reconciliacao import main

    rec = tmp_path / "pulsearb-x.jsonl.gz"
    with gzip.open(rec, "wb") as f:
        for r in [*INICIO, _marcador(3000, 3000)]:
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
    assert main([str(rec)]) == 0
    saida = json.loads(capsys.readouterr().out)
    assert "detalhe_com_replay" in saida
    assert saida["detalhe_com_replay"]["forense_pendentes"][0]["motivo"] == (
        "sem_book_apos_o_ultimo_resync"
    )


# ─────────────────────────────────────────────────── P1: escopo real do recorder


def test_slugs_no_escopo_le_a_lista_ou_devolve_none():
    assert slugs_no_escopo({"escopo": {"slugs_no_escopo": ["a", "b"]}}) == {"a", "b"}
    # gravação antiga: escopo desconhecido, NÃO "tudo"
    assert slugs_no_escopo({"escopo": {"janelas_no_escopo": 3}}) is None
    assert slugs_no_escopo({"escopo": {"slugs_no_escopo": ["a", 3]}}) is None
    assert slugs_no_escopo(None) is None


def _janela_snapshot(slug: str) -> dict[str, Any]:
    return {
        "slug": slug,
        "condition_id": f"0x{slug}",
        "end_date_iso": "2026-08-16T15:00:00Z",
        "tick_size": 0.01,
        "token_id_by_outcome": {"Up": f"up-{slug}", "Down": f"dn-{slug}"},
    }


def test_backtest_exclui_janela_cortada_pelo_escopo(tmp_path):
    from pulsearb.backtest.__main__ import RecordingIndex
    from pulsearb.replay.reader import RecordingReader

    index = RecordingIndex(RecordingReader(tmp_path))
    janelas = [_janela_snapshot("a"), _janela_snapshot("b"), _janela_snapshot("c")]
    index._on_discovery({"janelas": janelas, "escopo": {"slugs_no_escopo": ["a", "b"]}})
    # no ciclo seguinte "b" é cortada: sai inteira (o livro dela tem buraco)
    index._on_discovery({"janelas": janelas, "escopo": {"slugs_no_escopo": ["a", "c"]}})
    assert set(index.janelas_por_slug) == {"a"}
    escopo = index.escopo_do_recorder()
    assert escopo["janelas_fora_do_escopo_excluidas"] == 2
    assert escopo["snapshots_sem_escopo_explicito"] == 0


def test_backtest_com_gravacao_antiga_mantem_as_janelas_e_conta(tmp_path):
    from pulsearb.backtest.__main__ import RecordingIndex
    from pulsearb.replay.reader import RecordingReader

    index = RecordingIndex(RecordingReader(tmp_path))
    index._on_discovery({"janelas": [_janela_snapshot("a"), _janela_snapshot("b")]})
    assert set(index.janelas_por_slug) == {"a", "b"}
    assert index.escopo_do_recorder()["snapshots_sem_escopo_explicito"] == 1
