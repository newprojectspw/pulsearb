"""Escopo do recorder e preflight de armazenamento (req 11, 12, 13).

Duas travas operacionais que a gravação invalidada da VPS pediu:

- o escopo pode ser limitado, mas o corte NUNCA é silencioso — ele sai no
  snapshot de descoberta;
- uma gravação de 72 h RECUSA iniciar se o disco não comporta a projeção, e o
  relatório publica a taxa medida para calibrar a estimativa.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from pulsearb.recorder.__main__ import (
    PreflightRecusado,
    Recorder,
    preflight_de_armazenamento,
    projetar_armazenamento,
)
from pulsearb.settings import Settings


@dataclass
class _MarketFalso:
    slug: str
    token_id_by_outcome: dict[str, str]


def _markets(n: int) -> list[_MarketFalso]:
    return [
        _MarketFalso(slug=f"m{i:03d}", token_id_by_outcome={"Up": f"u{i}", "Down": f"d{i}"})
        for i in range(n)
    ]


def _recorder(tmp_path, **rec_kw) -> Recorder:
    settings = Settings(mode="SIM")
    settings.recorder.output_dir = str(tmp_path)
    for chave, valor in rec_kw.items():
        setattr(settings.recorder, chave, valor)
    return Recorder(settings)


# ───────────────────────────────────────────────────────────── escopo (req 11)


def test_sem_limite_o_escopo_e_toda_a_descoberta(tmp_path):
    rec = _recorder(tmp_path)  # max_tokens_assinados = None
    no_escopo, escopo = rec._aplicar_escopo(_markets(76))

    assert len(no_escopo) == 76
    assert escopo["limite_de_tokens"] is None
    assert escopo["janelas_cortadas"] == 0
    assert escopo["tokens_no_escopo"] == 152


def test_limite_corta_janelas_inteiras_e_reporta_o_corte(tmp_path):
    """20 tokens = 10 janelas inteiras (Up+Down é um par). O corte aparece."""
    rec = _recorder(tmp_path, max_tokens_assinados=20)
    no_escopo, escopo = rec._aplicar_escopo(_markets(76))

    assert len(no_escopo) == 10
    assert escopo["tokens_no_escopo"] == 20
    assert escopo["janelas_descobertas"] == 76
    assert escopo["janelas_no_escopo"] == 10
    assert escopo["janelas_cortadas"] == 66


def test_o_corte_e_deterministico_por_slug(tmp_path):
    """O replay tem de reproduzir a MESMA seleção — ordem por slug, estável."""
    rec = _recorder(tmp_path, max_tokens_assinados=6)
    import random

    baralhado = _markets(20)
    random.Random(1).shuffle(baralhado)
    no_escopo, _ = rec._aplicar_escopo(baralhado)

    assert [m.slug for m in no_escopo] == ["m000", "m001", "m002"]


# ────────────────────────────────────────────── armazenamento (req 12 e 13)


def test_projecao_cabe_quando_ha_espaco():
    proj = projetar_armazenamento(
        bytes_por_hora=2_000_000_000.0, duracao_s=72 * 3600, margem=1.2,
        livre_bytes=200_000_000_000,
    )
    assert proj["projecao_72h_bytes"] == 144_000_000_000
    assert proj["exigido_bytes"] == 172_800_000_000
    assert proj["cabe"] is True


def test_projecao_nao_cabe_quando_falta_espaco():
    proj = projetar_armazenamento(
        bytes_por_hora=2_000_000_000.0, duracao_s=72 * 3600, margem=1.2,
        livre_bytes=100_000_000_000,
    )
    assert proj["cabe"] is False


def test_preflight_recusa_72h_sem_disco(tmp_path):
    """Req 12: a gravação longa RECUSA iniciar sem espaço — falha fechada."""
    rec = _recorder(
        tmp_path,
        bytes_por_hora_estimados=1e18,  # absurdo, para não caber em disco nenhum
        margem_de_disco=1.2,
    )
    with pytest.raises(PreflightRecusado):
        preflight_de_armazenamento(rec.settings, 72 * 3600)


def test_preflight_nao_barra_rodada_curta(tmp_path):
    """Uma hora de teste não precisa de espaço para 72 h: não é aplicável."""
    rec = _recorder(tmp_path, bytes_por_hora_estimados=1e18)
    proj = preflight_de_armazenamento(rec.settings, 3600)  # 1 h < mínimo

    assert proj["aplicavel"] is False  # não levanta, mesmo sem caber


@pytest.mark.parametrize(
    "argumentos",
    [
        {"bytes_por_hora": 0, "duracao_s": 3600, "margem": 1.2, "livre_bytes": 1},
        {"bytes_por_hora": -1, "duracao_s": 3600, "margem": 1.2, "livre_bytes": 1},
        {"bytes_por_hora": 1, "duracao_s": 3600, "margem": 0.99, "livre_bytes": 1},
        {"bytes_por_hora": 1, "duracao_s": -1, "margem": 1.2, "livre_bytes": 1},
    ],
)
def test_projecao_rejeita_entrada_invalida(argumentos):
    with pytest.raises(ValueError):
        projetar_armazenamento(**argumentos)


def test_relatorio_traz_projecao_de_armazenamento(tmp_path):
    """Req 13: taxa medida de bytes/hora e projeção 72 h no relatório."""
    rec = _recorder(tmp_path)
    resumo = rec._armazenamento_resumo(3600.0)

    assert "bytes_por_hora_medido" in resumo
    assert "projecao_72h_bytes" in resumo
    assert resumo["estimativa_configurada_por_hora"] == 2_000_000_000.0


# ───────────── escopo desconta tokens retidos em carência (revisão P1 #195)


def test_escopo_desconta_retidos_em_carencia(tmp_path):
    """Tokens de janelas fechadas ainda em carência consomem o teto — senão
    `limite=4` com 2 retidos + 2 novos daria 4 ativos e o teto não seria teto.
    """
    rec = _recorder(tmp_path, max_tokens_assinados=4)
    no_escopo, escopo = rec._aplicar_escopo(_markets(10), retidos_em_carencia=2)

    assert escopo["orcamento_para_novos"] == 2   # 4 − 2 retidos
    assert len(no_escopo) == 1                    # só 1 janela nova cabe
    assert escopo["tokens_ativos_estimados"] == 4  # 2 novos + 2 retidos = teto


def test_escopo_com_carencia_cheia_nao_assina_nada_novo(tmp_path):
    """Se os retidos já enchem o teto, nenhuma janela nova entra."""
    rec = _recorder(tmp_path, max_tokens_assinados=2)
    no_escopo, escopo = rec._aplicar_escopo(_markets(10), retidos_em_carencia=2)

    assert escopo["orcamento_para_novos"] == 0
    assert no_escopo == []
    assert escopo["tokens_ativos_estimados"] == 2
