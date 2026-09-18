"""`scripts/janela_do_twap.py` — a varredura τ do backtest, por duração.

Auditoria 2026-09-17 §2.2. Os streams aqui são sintéticos no padrão dos
testes da varredura (`test_m211_folga_relativa.py`): inteiros e18, carimbo
do servidor, uma amostra por segundo. O que se prende é a divisão por
duração e a leitura do veredito do backtest — não o servidor.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from pulsearb.analysis.anchor_sweep import JanelaResolvida

RAIZ = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "janela_do_twap", RAIZ / "scripts" / "janela_do_twap.py"
)
assert _spec is not None and _spec.loader is not None
jt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jt)

E18 = 10**18
BASE_MS = 1_788_920_000_000  # 2026-09-09 ~02:13 UTC
PRECO = 78_640 * E18


def _stream(fechos: dict[int, int], *, duracao_total_s: int) -> dict[str, list[tuple[int, int]]]:
    """Uma amostra por segundo, constante em PRECO, com o valor trocado nos
    instantes de fecho pedidos (`{fechamento_ms: valor}`)."""
    return {
        "btc": [
            (BASE_MS + s * 1000, fechos.get(BASE_MS + s * 1000, PRECO))
            for s in range(-300, duracao_total_s + 120)
        ]
    }


def _janelas(duracao_s: int, n: int, *, coerentes: bool) -> tuple[list[JanelaResolvida], dict]:
    """`n` janelas consecutivas de `duracao_s`. Cada fecho tem valor acima ou
    abaixo do PRECO alternadamente; `coerentes` diz se a resolução segue o
    stream (τ=0 explica) ou o contrário (τ=0 desmentido)."""
    janelas, fechos = [], {}
    for i in range(n):
        abertura = BASE_MS + i * duracao_s * 1000
        fecho = abertura + duracao_s * 1000
        sobe = i % 2 == 0
        fechos[fecho] = PRECO + (1 if sobe else -1) * 50 * E18  # folga 0,06 %: decide
        janelas.append(JanelaResolvida(
            slug=f"btc-{duracao_s}-{i}", asset="btc", abertura_ms=abertura,
            fechamento_ms=fecho, resolveu_up=sobe if coerentes else not sobe,
        ))
    return janelas, fechos


class TestComparar:
    def test_duracao_coerente_com_tau0_da_CONFIRMADA(self):
        janelas, fechos = _janelas(300, 24, coerentes=True)
        rel = jt.comparar({300: janelas}, _stream(fechos, duracao_total_s=300 * 24))
        bloco = rel["300"]
        assert bloco["veredito"] == "CONFIRMADA"
        assert bloco["consistencia_tau0"] == 1.0
        assert bloco["janelas_discordantes"] == 0
        assert bloco["janelas_elegiveis"] == 24

    def test_duracao_com_resolucoes_ao_contrario_da_DESMENTIDA(self):
        janelas, fechos = _janelas(900, 24, coerentes=False)
        rel = jt.comparar({900: janelas}, _stream(fechos, duracao_total_s=900 * 24))
        assert rel["900"]["veredito"] == "DESMENTIDA"
        assert rel["900"]["consistencia_tau0"] == 0.0
        assert len(rel["900"]["discordantes_em_tau0"]) > 0

    def test_as_duracoes_nao_se_misturam(self):
        j5, f5 = _janelas(300, 24, coerentes=True)
        j15, f15 = _janelas(900, 24, coerentes=False)
        fechos = {**f5, **f15}
        rel = jt.comparar({300: j5, 900: j15}, _stream(fechos, duracao_total_s=900 * 24))
        assert rel["300"]["veredito"] == "CONFIRMADA"
        assert rel["900"]["veredito"] == "DESMENTIDA"

    def test_poucas_janelas_da_SEM_AMOSTRA_e_nenhuma_da_SEM_DADO(self):
        janelas, fechos = _janelas(14400, 3, coerentes=True)
        rel = jt.comparar({14400: janelas, 60: []}, _stream(fechos, duracao_total_s=14400 * 3))
        assert rel["14400"]["veredito"] == "SEM_AMOSTRA"
        assert rel["60"]["veredito"] == "SEM_DADO"

    def test_todo_veredito_declarado_e_alcancavel(self):
        j5, f5 = _janelas(300, 24, coerentes=True)
        j15, f15 = _janelas(900, 24, coerentes=False)
        j4h, f4h = _janelas(14400, 3, coerentes=True)
        fechos = {**f5, **f15, **f4h}
        rel = jt.comparar(
            {300: j5, 900: j15, 14400: j4h, 60: []},
            _stream(fechos, duracao_total_s=14400 * 3),
        )
        assert {b["veredito"] for b in rel.values()} == set(jt.VEREDITOS)


class TestHoraUtc:
    def test_offset_explicito_e_convertido_e_nao_relabelado(self):
        assert jt._hora_utc("2026-09-09T00:00-03:00").isoformat() == "2026-09-09T03:00:00+00:00"
        assert jt._hora_utc("2026-09-09T02:19Z").isoformat() == "2026-09-09T02:19:00+00:00"
        assert jt._hora_utc(None) is None


class TestMain:
    def test_main_recusa_json_absoluto(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("PULSEARB_BACKTEST_OUTPUT_ROOT", raising=False)
        (tmp_path / "grav").mkdir()
        assert jt.main([str(tmp_path / "grav"), "--json", "/tmp/j.json"]) == 2
        assert jt.main(["nao-existe"]) == 2
        assert "nome de saída inválido" in capsys.readouterr().err

    def test_gravacao_sem_janela_resolvida_devolve_1(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        pasta = tmp_path / "grav"
        pasta.mkdir()
        (pasta / "pulsearb-20260917-1200.jsonl").write_text(
            json.dumps({"ts_mono_ns": 1, "ts_wall_ns": BASE_MS * 10**6, "fonte": "poly_ws",
                        "payload": {}}) + "\n",
            encoding="utf-8",
        )
        assert jt.main([str(pasta)]) == 1
