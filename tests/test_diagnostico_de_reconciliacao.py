"""A ferramenta de diagnóstico offline da reconciliação (req 6).

Sem o `.jsonl.gz` da VPS no checkout, o núcleo é exercitado com registros
sintéticos que reproduzem os dois fenômenos que o total confunde: uma
divergência material que PERSISTE, e o resync que o backtest não reproduz.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from scripts.diagnostico_de_reconciliacao import diagnosticar

from pulsearb.recorder.writer import FONTE_RESYNC


@dataclass
class _Reg:
    fonte: str
    payload: Any
    ts_wall_ns: int = 0


def _poly(evento: dict, ts_ms: int) -> _Reg:
    return _Reg(fonte="poly_ws", payload=evento, ts_wall_ns=ts_ms * 1_000_000)


def _book(ts_ms: int, bid: str, ask: str) -> _Reg:
    return _poly(
        {
            "event_type": "book",
            "asset_id": "tok",
            "timestamp": str(ts_ms),
            "bids": [{"price": bid, "size": "100"}],
            "asks": [{"price": ask, "size": "100"}],
        },
        ts_ms,
    )


def _delta(ts_ms: int, *, best_bid: str, best_ask: str) -> _Reg:
    return _poly(
        {
            "event_type": "price_change",
            "market": "0xabc",
            "timestamp": str(ts_ms),
            "price_changes": [
                {
                    "asset_id": "tok",
                    "price": "0.50",
                    "size": "10",
                    "side": "BUY",
                    "best_bid": best_bid,
                    "best_ask": best_ask,
                }
            ],
        },
        ts_ms,
    )


def _registros_com_resync() -> list[_Reg]:
    """Book, duas divergências materiais persistentes, o resync do recorder, e
    o book de recuperação com carimbo ATRASADO (< o último delta)."""
    return [
        _book(1000, "0.49", "0.51"),
        # servidor afirma bid 0,70; reconstrução fica em 0,50 — 20 ticks fora.
        _delta(2000, best_bid="0.70", best_ask="0.51"),
        _delta(2600, best_bid="0.70", best_ask="0.51"),  # persiste > 250 ms
        _Reg(fonte=FONTE_RESYNC, payload={"tokens": ["tok"]}, ts_wall_ns=2650 * 10**6),
        _book(2400, "0.70", "0.71"),  # recuperação, carimbo < 2600
    ]


def test_sem_replay_a_divergencia_material_PERSISTE():
    d = diagnosticar(_registros_com_resync(), replay_resync=False)
    assert d["metricas"]["divergencias_persistentes"] >= 1
    assert d["metricas"]["tokens_comprometidos"] >= 1


def test_com_replay_de_resync_a_reconstrucao_re_ancora():
    """Reproduzir o resync (marcar_perda) + o book de recuperação (aplicado
    mesmo com carimbo atrasado) zera a persistência — é o descompasso item 4."""
    sem = diagnosticar(_registros_com_resync(), replay_resync=False)
    com = diagnosticar(_registros_com_resync(), replay_resync=True)

    assert com["metricas"]["divergencias_persistentes"] < sem["metricas"][
        "divergencias_persistentes"
    ]
    # o token de fato recuperou o livro (não ficou aguardando resync eterno).
    assert com["metricas"]["tokens_aguardando_resync"] == 0


def test_token_sem_snapshot_e_classificado_por_motivo():
    """O relatório por token diz POR QUE cada comprometido é baixa — aqui,
    nunca recebeu book inicial."""
    registros = [
        _delta(ts, best_bid="0.49", best_ask="0.51")
        for ts in range(1000, 5000, 500)
    ]
    d = diagnosticar(registros, replay_resync=False)
    motivos = d["motivos_dos_comprometidos"]
    assert motivos.get("nunca_teve_snapshot", 0) >= 1


def test_metricas_trazem_formas_e_niveis_para_achar_truncagem():
    """Sem redução ao reproduzir resync, o diagnóstico aponta truncagem: as
    formas e os níveis por lado têm de estar no relatório para checar isso."""
    d = diagnosticar([_book(1000, "0.49", "0.51")], replay_resync=False)
    assert "formas_de_book" in d["metricas"]
    assert "niveis_por_lado" in d["metricas"]
    assert "formas_de_price_change" in d["metricas"]


def test_json_com_caminho_absoluto_e_recusado(tmp_path):
    """Revisão Sonar (S2083): o `--json` passa por `caminho_de_escrita`, então
    um caminho absoluto/não sanitizado é RECUSADO."""
    import gzip

    import orjson
    import pytest
    from scripts.diagnostico_de_reconciliacao import main

    rec = tmp_path / "pulsearb-x.jsonl.gz"
    with gzip.open(rec, "wb") as f:
        f.write(
            orjson.dumps(
                {
                    "ts_mono_ns": 1,
                    "ts_wall_ns": 1_000_000,
                    "fonte": "poly_ws",
                    "payload": {
                        "event_type": "book",
                        "asset_id": "tok",
                        "timestamp": "1000",
                        "bids": [{"price": "0.49", "size": "100"}],
                        "asks": [{"price": "0.51", "size": "100"}],
                    },
                }
            )
            + b"\n"
        )
    with pytest.raises(ValueError):
        main([str(rec), "--json", "/tmp/evil.json"])


def test_json_relativo_e_gravado_na_raiz_permitida(tmp_path, monkeypatch):
    """O destino validado é o que chega ao write, nunca o argumento cru."""
    import gzip

    import orjson
    from scripts.diagnostico_de_reconciliacao import main

    rec = tmp_path / "pulsearb-seguro.jsonl.gz"
    with gzip.open(rec, "wb") as f:
        f.write(
            orjson.dumps(
                {
                    "ts_mono_ns": 1,
                    "ts_wall_ns": 1_000_000,
                    "fonte": "poly_ws",
                    "payload": {
                        "event_type": "book",
                        "asset_id": "tok",
                        "timestamp": "1000",
                        "bids": [{"price": "0.49", "size": "100"}],
                        "asks": [{"price": "0.51", "size": "100"}],
                    },
                }
            )
            + b"\n"
        )

    monkeypatch.chdir(tmp_path)
    assert main([str(rec), "--json", "relatorio.json"]) == 0
    assert (tmp_path / "relatorio.json").is_file()
