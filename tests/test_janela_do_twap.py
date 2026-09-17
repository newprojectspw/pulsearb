"""`scripts/janela_do_twap.py` — auditoria 2026-09-17 §2.2.

A gravação sintética aqui NÃO é a forma do servidor: é só o stream que o
`WindowOutcome` já recebe do índice. O que se testa é a comparação — se um
stream construído para resolver pela janela de 30 s é reconhecido como tal,
e o simétrico — e que todo veredito declarado é alcançável.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "janela_do_twap", RAIZ / "scripts" / "janela_do_twap.py"
)
assert _spec is not None and _spec.loader is not None
jt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jt)

from pulsearb.engine.anchor import WindowOutcome  # noqa: E402

S = 1_000_000_000
ABERTURA = 1_000 * S
FECHO = ABERTURA + 300 * S


def _stream_em_degrau(base: float, ultimos_30_s: float, primeiros_30_dos_60: float):
    """Uma amostra por segundo. Nos 60 s finais, dois patamares distintos.

    TWAP-30 = `ultimos_30_s`; TWAP-60 = média dos dois patamares. Com a
    âncora em `base`, o sinal de (twap − âncora) pode ser diferente nos dois.
    """
    amostras: list[tuple[int, float]] = []
    for t in range(-120, 301):
        ts = ABERTURA + t * S
        if t > 270:
            preco = ultimos_30_s
        elif t > 240:
            preco = primeiros_30_dos_60
        else:
            preco = base
        amostras.append((ts, preco))
    return tuple(amostras)


def _janela(slug: str, samples, resolved_up: bool) -> WindowOutcome:
    return WindowOutcome(
        slug=slug, open_ts_ns=ABERTURA, close_ts_ns=FECHO, samples=samples, resolved_up=resolved_up
    )


# TWAP-30 = 101 (> 100: Up); TWAP-60 = (101 + 98)/2 = 99,5 (< 100: Down).
DISCORDANTE = _stream_em_degrau(100.0, 101.0, 98.0)


class TestVeredito:
    def test_stream_que_resolve_pela_janela_de_30_da_JANELA_30(self):
        rel = jt.comparar({300: [_janela("btc-5m-a", DISCORDANTE, True)] * 3})
        bloco = rel["300"]
        assert bloco["veredito"] == "JANELA_30"
        assert bloco["por_janela"]["JANELA_30"]["erros_minimos"] == 0
        assert bloco["por_janela"]["JANELA_60"]["erros_minimos"] == 3

    def test_stream_que_resolve_pela_janela_de_60_da_JANELA_60(self):
        rel = jt.comparar({300: [_janela("btc-5m-a", DISCORDANTE, False)] * 3})
        assert rel["300"]["veredito"] == "JANELA_60"
        assert rel["300"]["menos_erros"] == "JANELA_60"

    def test_stream_em_que_as_duas_concordam_da_AMBAS(self):
        concordante = _stream_em_degrau(100.0, 105.0, 105.0)
        rel = jt.comparar({900: [_janela("btc-15m-a", concordante, True)]})
        assert rel["900"]["veredito"] == "AMBAS"
        assert rel["900"]["menos_erros"] is None

    def test_resolucao_contraria_as_duas_da_NENHUMA_e_diz_quem_errou_menos(self):
        concordante = _stream_em_degrau(100.0, 105.0, 105.0)  # as duas dizem Up
        janelas = [_janela("a", concordante, False), _janela("b", DISCORDANTE, True)]
        # 30 s: erra `a`, acerta `b` → 1 erro; 60 s: erra as duas → 2 erros.
        rel = jt.comparar({300: janelas})
        assert rel["300"]["veredito"] == "NENHUMA"
        assert rel["300"]["menos_erros"] == "JANELA_30"

    def test_sem_amostras_da_SEM_DADO(self):
        rel = jt.comparar({14400: [_janela("btc-4h-a", (), True)]})
        assert rel["14400"]["veredito"] == "SEM_DADO"

    def test_todo_veredito_declarado_e_alcancavel(self):
        concordante = _stream_em_degrau(100.0, 105.0, 105.0)
        rel = jt.comparar(
            {
                300: [_janela("a", DISCORDANTE, True)],
                900: [_janela("b", DISCORDANTE, False)],
                14400: [_janela("c", concordante, True)],
                1: [_janela("d", concordante, False), _janela("e", DISCORDANTE, True)],
                2: [_janela("f", (), True)],
            }
        )
        assert {b["veredito"] for b in rel.values()} == set(jt.VEREDITOS)

    def test_as_duracoes_nao_se_misturam(self):
        rel = jt.comparar(
            {300: [_janela("a", DISCORDANTE, True)], 900: [_janela("b", DISCORDANTE, False)]}
        )
        assert rel["300"]["veredito"] == "JANELA_30"
        assert rel["900"]["veredito"] == "JANELA_60"


class TestMain:
    def test_main_recusa_json_absoluto_e_janelas_invalidas(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("PULSEARB_BACKTEST_OUTPUT_ROOT", raising=False)
        (tmp_path / "grav").mkdir()
        assert jt.main([str(tmp_path / "grav"), "--json", "/tmp/j.json"]) == 2
        assert jt.main([str(tmp_path / "grav"), "--janelas", "30,0"]) == 2
        assert jt.main(["nao-existe"]) == 2
        assert "nome de saída inválido" in capsys.readouterr().err

    def test_gravacao_sem_janela_resolvida_devolve_1(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        pasta = tmp_path / "grav"
        pasta.mkdir()
        (pasta / "pulsearb-20260917-1200.jsonl").write_text(
            json.dumps({"ts_wall_ns": ABERTURA, "fonte": "poly_ws", "payload": {}}) + "\n",
            encoding="utf-8",
        )
        assert jt.main([str(pasta)]) == 1
