"""M2.4 — engenharia reversa da âncora por varredura de τ.

O gerador destes testes PLANTA uma âncora conhecida (o valor do stream em
`abertura + τ*`) e resolve cada janela de acordo com ela; a varredura tem de
encontrá-la. É o único jeito honesto de testar um buscador: esconder a
resposta e cobrar que ele a ache — nunca conferir contra a própria saída.
"""

from __future__ import annotations

import random

from pulsearb.analysis.anchor_sweep import (
    E18,
    JanelaResolvida,
    StreamE18,
    _como_intervalos,
    _e18_str,
    varrer,
)
from pulsearb.backtest.__main__ import _e18_do_payload

BASE_MS = 1_787_000_000_000


def _stream_e_janelas(
    *,
    tau_verdadeiro_s: int,
    n_janelas: int = 12,
    duracao_s: int = 300,
    seed: int = 7,
) -> tuple[dict[str, list[tuple[int, int]]], list[JanelaResolvida]]:
    """Stream de 1 tick/s + janelas resolvidas pela âncora plantada.

    A resolução compara o valor do stream NO FECHAMENTO com o valor em
    `abertura + τ*` — exatamente a família que a varredura testa, com final
    na definição `stream_no_fechamento`.
    """
    rnd = random.Random(seed)
    total_s = 400 + n_janelas * duracao_s + 400
    valores: list[tuple[int, int]] = []
    preco = 60_000 * E18
    for segundo in range(total_s):
        preco += rnd.randint(-3 * E18, 3 * E18)
        valores.append((BASE_MS + segundo * 1000, preco))
    stream = StreamE18(valores)

    janelas = []
    for indice in range(n_janelas):
        abertura = BASE_MS + (400 + indice * duracao_s) * 1000
        fechamento = abertura + duracao_s * 1000
        ancora = stream.em(abertura + tau_verdadeiro_s * 1000)
        final = stream.em(fechamento)
        assert ancora is not None and final is not None
        janelas.append(
            JanelaResolvida(
                slug=f"btc-updown-5m-{indice}",
                asset="btc",
                abertura_ms=abertura,
                fechamento_ms=fechamento,
                resolveu_up=final >= ancora,   # empate = Up, como no mercado
            )
        )
    return {"btc": valores}, janelas


def test_varredura_encontra_o_tau_plantado():
    streams, janelas = _stream_e_janelas(tau_verdadeiro_s=60)
    saida = varrer(janelas, streams)

    fino = saida["final_stream_no_fechamento"]
    assert any(a <= 60 <= b for a, b in fino["regiao_viavel_100pct"]), (
        "τ*=60 tem de estar na região viável: " + str(fino["regiao_viavel_100pct"])
    )
    assert saida["janelas_elegiveis"] == len(janelas)
    # A curva completa existe e cobre a grade inteira.
    assert len(fino["curva"]) == 361


def test_tau_negativo_tambem_e_encontrado():
    streams, janelas = _stream_e_janelas(tau_verdadeiro_s=-45, seed=11)
    saida = varrer(janelas, streams)
    fino = saida["final_stream_no_fechamento"]
    assert any(a <= -45 <= b for a, b in fino["regiao_viavel_100pct"])


def test_grade_tau_phi_reporta_melhor_celula():
    streams, janelas = _stream_e_janelas(tau_verdadeiro_s=30, seed=3)
    saida = varrer(janelas, streams)
    melhor = saida["grade_tau_phi"]["melhor_celula"]
    assert melhor is not None
    # τ*=30 e φ=0 estão na grade (passo 5), então existe célula perfeita.
    # Outras células podem empatar em 1.0 numa amostra de 12 janelas — o
    # contrato é reportar a melhor, não desempatar sem dado.
    assert melhor["consistencia"] == 1.0
    assert len(saida["grade_tau_phi"]["top"]) >= 1


def test_decisao_e_inteira_um_wei_decide():
    """O caso que float64 não enxerga: diferença de 1 na escala 1e18.

    Em ~2096 (a falha real de ETH), 1 wei é a 21ª casa relativa — float64
    colapsa os dois lados no mesmo número e a desigualdade viraria empate.
    A varredura tem de ver o Down com gap de 1 wei como Down.
    """
    preco = 2096 * E18
    valores = []
    for segundo in range(400):
        # stream constante em `preco`, exceto o fechamento, 1 wei ABAIXO
        v = preco - 1 if segundo == 300 else preco
        valores.append((BASE_MS + segundo * 1000, v))
    janela = JanelaResolvida(
        slug="eth-updown-5m-wei",
        asset="eth",
        abertura_ms=BASE_MS + 240_000,
        fechamento_ms=BASE_MS + 300_000,
        resolveu_up=False,   # final = preco−1 < âncora = preco ⇒ Down
    )
    saida = varrer([janela], {"eth": valores})
    fino = saida["final_stream_no_fechamento"]
    # τ=0 (âncora = preco) explica o Down por exatamente 1 wei
    assert fino["curva"]["0"] == 1.0


def test_empate_exato_resolve_up_e_e_contado():
    preco = 50_000 * E18
    valores = [(BASE_MS + s * 1000, preco) for s in range(400)]
    janela = JanelaResolvida(
        slug="btc-updown-5m-empate",
        asset="btc",
        abertura_ms=BASE_MS + 240_000,
        fechamento_ms=BASE_MS + 300_000,
        resolveu_up=True,   # stream constante: final == âncora ⇒ empate ⇒ Up
    )
    saida = varrer([janela], {"btc": valores})
    melhor = saida["final_media_60s"]["melhores_tau"][0]
    assert melhor["consistencia"] == 1.0
    assert melhor["empates_exatos"] == 1


def test_janela_sem_cobertura_nao_entra_na_conta():
    """Stream que começa DEPOIS de abertura−180s: a janela sai, contada.

    Sem isso, um τ negativo falharia por lacuna e a varredura leria a lacuna
    como evidência contra aquele τ.
    """
    valores = [(BASE_MS + s * 1000, 50_000 * E18) for s in range(200, 400)]
    janela = JanelaResolvida(
        slug="btc-updown-5m-lacuna",
        asset="btc",
        abertura_ms=BASE_MS + 240_000,   # abertura−180s < primeiro tick
        fechamento_ms=BASE_MS + 360_000,
        resolveu_up=True,
    )
    saida = varrer([janela], {"btc": valores})
    assert saida["janelas_elegiveis"] == 0
    assert saida["janelas_sem_cobertura_do_stream"] == 1


def test_falha_inexplicavel_denuncia_fonte_fora_do_stream():
    """Resolução que NENHUM ponto do stream explica é o critério de falha
    da fundação (VEREDITO_M2.md) — precisa sair nomeada, com min/max."""
    preco = 50_000 * E18
    valores = [(BASE_MS + s * 1000, preco) for s in range(400)]
    janela = JanelaResolvida(
        slug="btc-updown-5m-impossivel",
        asset="btc",
        abertura_ms=BASE_MS + 240_000,
        fechamento_ms=BASE_MS + 300_000,
        # stream constante ⇒ final == qualquer âncora ⇒ empate ⇒ Up.
        # Down é inexplicável por QUALQUER ponto do stream.
        resolveu_up=False,
    )
    saida = varrer([janela], {"btc": valores})
    falhas = saida["falhas_inexplicaveis"]
    assert len(falhas) == 1
    assert falhas[0]["slug"] == "btc-updown-5m-impossivel"
    assert "fonte fora do nosso stream" in falhas[0]["leitura"]
    assert falhas[0]["stream_min"] == falhas[0]["stream_max"]


def test_janela_explicavel_nao_aparece_como_falha():
    streams, janelas = _stream_e_janelas(tau_verdadeiro_s=0, seed=5)
    saida = varrer(janelas, streams)
    assert saida["falhas_inexplicaveis"] == []


# ─────────────────────────────────────── extração exata do valor e18


def test_e18_prefere_full_accuracy_value():
    bruto = {
        "topic": "crypto_prices_twap_sixty",
        "payload": {
            "symbol": "btc/usd",
            "timestamp": 1787166722000,
            "value": 118432.17,
            "full_accuracy_value": "118432170000000000000001",
        },
    }
    assert _e18_do_payload(bruto) == 118432170000000000000001


def test_e18_fallback_decimal_e_exato():
    """`value` como string decimal converte EXATO; float na origem é recusado.

    `int(float("2096.78") * 1e18)` erraria os últimos dígitos — que são
    exatamente os que a varredura existe para enxergar.
    """
    com_string = {
        "payload": {"timestamp": 1, "value": "2096.78"}
    }
    assert _e18_do_payload(com_string) == 2096_780000000000000000

    com_float = {"payload": {"timestamp": 1, "value": 2096.78}}
    assert _e18_do_payload(com_float) is None


def test_formatacao_e18_sem_float():
    assert _e18_str(2096_780000000000000000) == "2096.780000000000000000"
    assert _e18_str(-1) == "-0.000000000000000001"


def test_intervalos_legiveis():
    assert _como_intervalos([-3, -2, -1, 4, 5]) == [[-3, -1], [4, 5]]
    assert _como_intervalos([]) == []
