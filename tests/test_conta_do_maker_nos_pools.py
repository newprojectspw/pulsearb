"""A conta do maker nos pools: as regras que a mantêm um LIMITE INFERIOR.

Uma conta que limita por baixo só serve se cada aproximação errar para o
mesmo lado. Estes testes prendem as três que importam — e a primeira já foi
violada uma vez no projeto, no `limite_pessimista`, onde omitir o rebate foi
decisão deliberada pelo mesmo motivo.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]


def _carregar(nome: str):
    spec = importlib.util.spec_from_file_location(nome, RAIZ / "scripts" / f"{nome}.py")
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nome] = modulo
    spec.loader.exec_module(modulo)
    return modulo


conta = _carregar("conta_do_maker_nos_pools")


def _markout(media: float, n: int = 500) -> dict:
    return {
        "markout": {
            "markout_centavos_por_share": {"total": {"5s": {"media": media, "n": n}}}
        }
    }


def test_markout_negativo_vira_custo_positivo() -> None:
    custo, n = conta._markout_adverso(_markout(-0.31))
    assert custo == 0.31
    assert n == 500


def test_markout_POSITIVO_vira_custo_zero_e_nao_credito() -> None:
    """A regra que mantém a conta um limite INFERIOR.

    Markout positivo quer dizer que o preço andou a nosso favor depois da
    execução. Creditar isso somaria a hipótese otimista dos DOIS lados —
    receita estimada com fila generosa E custo virando lucro. O limite
    inferior exige que cada aproximação erre para o mesmo lado.
    """
    custo, _ = conta._markout_adverso(_markout(+0.42))
    assert custo == 0.0


def test_sem_markout_a_conta_nao_finge() -> None:
    """Ausência devolve `None`, nunca 0,0.

    Zero seria custo nenhum — a conta fecharia positiva por falta de dado,
    que é o modo de falha mais caro que existe aqui.
    """
    custo, n = conta._markout_adverso(None)
    assert custo is None and n == 0
    custo, _ = conta._markout_adverso({"markout": {}})
    assert custo is None


def test_volume_trunca_sem_enviesar_a_taxa(monkeypatch) -> None:
    """Truncar em 1.000 trades não infla nem desinfla shares/h.

    A taxa é normalizada pelo span OBSERVADO, então um mercado movimentado
    devolve menos horas e a mesma taxa. Se fosse normalizada por um span fixo,
    truncar viraria subestimativa de volume — e volume é CUSTO, então
    subestimá-lo quebraria o limite inferior.
    """

    class _Resp:
        status_code = 200

        def __init__(self, trades):
            self._t = trades

        def json(self):
            return self._t

    base = 1_000_000_000
    # 200 trades de 10 shares, um a cada 36 s -> ~1.000 shares/h
    completo = [
        {"timestamp": base + i * 36, "size": 10, "price": 0.5} for i in range(200)
    ]
    # o MESMO fluxo, mas só a metade final sobreviveu ao corte de 1.000
    truncado = completo[100:]

    class _Http:
        def __init__(self, trades):
            self._t = trades

        def get(self, *_a, **_k):
            return _Resp(self._t)

    a = conta.volume_taker(_Http(completo), "0x1")
    b = conta.volume_taker(_Http(truncado), "0x1")
    assert a is not None and b is not None
    # Tolerância RELATIVA: o span vai do primeiro ao último trade, então N
    # trades cobrem N-1 intervalos e as duas taxas diferem por discretização,
    # não por viés. Exigir igualdade exata reprovaria a aritmética correta.
    relativo = abs(a["shares_por_hora"] - b["shares_por_hora"]) / a["shares_por_hora"]
    assert relativo < 0.02, (a["shares_por_hora"], b["shares_por_hora"])


def test_mercado_sem_trade_nao_vira_custo_infinito() -> None:
    """Lista vazia é volume ZERO, não erro nem divisão por zero."""

    class _Resp:
        status_code = 200

        def json(self):
            return []

    class _Http:
        def get(self, *_a, **_k):
            return _Resp()

    vol = conta.volume_taker(_Http(), "0x1")
    assert vol == {
        "trades": 0,
        "shares_por_hora": 0.0,
        "usdc_por_hora": 0.0,
        "span_h": 0.0,
    }


# ── o caminho de entrada é contido (S2083) — e os testes vão por `main()` ──
#
# Os testes acima chamam funções puras; nenhum passava por `main()`, então a
# contenção dos caminhos de `--pools`/`--markout` não tinha teste que a
# provasse. "A suíte passa" era verdade e não era evidência.


def _raiz_de_leitura(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PULSEARB_RELATORIOS_INPUT_ROOT", raising=False)
    monkeypatch.delenv("PULSEARB_BACKTEST_OUTPUT_ROOT", raising=False)
    (tmp_path / "pools.json").write_text('{"mercados": []}', encoding="utf-8")


def test_pools_absoluto_sai_com_2_e_diz_por_que(tmp_path, monkeypatch, capsys) -> None:
    """Caminho absoluto funcionava antes da contenção e agora recusa — é a
    contenção fazendo o trabalho dela, e a mensagem diz qual variável abre."""
    _raiz_de_leitura(tmp_path, monkeypatch)
    codigo = conta.main(["--pools", str(tmp_path / "pools.json")])
    assert codigo == 2
    erro = capsys.readouterr().err
    assert "inválido" in erro
    assert "PULSEARB_RELATORIOS_INPUT_ROOT" in erro


def test_pools_inexistente_sai_com_2(tmp_path, monkeypatch, capsys) -> None:
    _raiz_de_leitura(tmp_path, monkeypatch)
    assert conta.main(["--pools", "nao-existe.json"]) == 2
    assert "não existe" in capsys.readouterr().err


def test_markout_invalido_avisa_e_a_conta_ainda_recusa(tmp_path, monkeypatch, capsys) -> None:
    """O markout é opcional de propósito: inválido vira aviso, não aborto. Mas
    ausente a conta NÃO fecha — sai com 1 e diz MARKOUT AUSENTE, em vez de
    publicar receita sem custo."""
    _raiz_de_leitura(tmp_path, monkeypatch)
    codigo = conta.main(["--pools", "pools.json", "--markout", "/etc/hosts.json"])
    saida = capsys.readouterr()
    assert "aviso:" in saida.err
    assert "MARKOUT AUSENTE" in saida.err
    assert codigo == 1


# ── custo de saída: max(markout 30 min, spread/2) × execuções/h, ou AUSENTE ─


def _markout_com_saida(media_1800: float, spread_meio: float, n: int = 12) -> dict:
    return {
        "regime": {"horas_de_coleta": 4.0},
        "markout": {
            "markout_centavos_por_share": {
                "total": {"5s": {"media": -0.06, "n": n}},
                "mercado=lac-9-5": {
                    "5s": {"media": -0.06, "n": n},
                    "1800s": {"media": media_1800, "n": n},
                    "custo_de_saida_centavos_por_share": {"media": spread_meio, "n": n},
                },
            }
        },
    }


def test_custo_de_saida_e_o_maior_entre_markout_longo_e_spread_meio() -> None:
    # spread/2 = 5,5 c vence um markout a 30 min de −0,9 c
    assert conta._custo_de_saida(_markout_com_saida(-0.9, 5.5)) == {"lac-9-5": 5.5}
    # markout longo pior que o spread vence
    assert conta._custo_de_saida(_markout_com_saida(-8.0, 5.5)) == {"lac-9-5": 8.0}
    # markout POSITIVO a 30 min não vira crédito: fica o spread/2
    assert conta._custo_de_saida(_markout_com_saida(+3.0, 5.5)) == {"lac-9-5": 5.5}


def test_custo_de_saida_ausente_e_none_nunca_zero() -> None:
    """Sem recorte por mercado ou sem horizonte de 30 min: None. Zero
    fecharia a conta a favor sem medida."""
    assert conta._custo_de_saida(None) is None
    sem_longo = _markout_com_saida(-0.9, 5.5)
    del sem_longo["markout"]["markout_centavos_por_share"]["mercado=lac-9-5"]["1800s"]
    assert conta._custo_de_saida(sem_longo) is None
    so_total = {"markout": {"markout_centavos_por_share": {"total": {"5s": {"media": -0.06}}}}}
    assert conta._custo_de_saida(so_total) is None


def _mercado(receita: float, shares_h: float, custo_saida_h: float | None) -> dict:
    return {
        "receita_usdc_por_hora": receita,
        "custo_maximo_usdc_por_hora": 0.5,
        "liquido_no_pior_caso_usdc_por_hora": receita - 0.5,
        "volume": {"shares_por_hora": shares_h},
        "custo_de_saida_usdc_por_hora": custo_saida_h,
        "liquido_com_custo_de_saida_usdc_por_hora": (
            None if custo_saida_h is None else receita - custo_saida_h
        ),
    }


def test_custo_de_saida_multiplica_por_shares_e_nao_por_trades() -> None:
    """¢/share × shares/h. Um fill de 1.000 shares não é um de uma."""
    m = {"receita_usdc_por_hora_minima": 10.0, "condition_id": "0x1", "slug": "s"}
    vol = {"shares_por_hora": 2000.0}
    linha = conta._linha_do_mercado(m, vol, custo_c=0.06, custo_saida_c=5.5)
    assert linha["custo_de_saida_usdc_por_hora"] == pytest.approx(2000 * 5.5 / 100)
    assert linha["liquido_com_custo_de_saida_usdc_por_hora"] == pytest.approx(10 - 110)


def test_recorte_so_soma_custo_de_saida_com_cobertura_completa() -> None:
    """Um mercado sem medida no recorte → líquido com custo de saída None,
    e o relatório diz quantos faltam. Somar só os medidos deixaria o recorte
    passar com um subconjunto."""
    completo = conta._soma_do_recorte([_mercado(10, 100, 2.0), _mercado(8, 50, 1.0)])
    assert completo["liquido_com_custo_de_saida_usdc_por_hora"] == pytest.approx(15.0)
    assert completo["mercados_sem_custo_de_saida"] == 0
    parcial = conta._soma_do_recorte([_mercado(10, 100, 2.0), _mercado(8, 50, None)])
    assert parcial["liquido_com_custo_de_saida_usdc_por_hora"] is None
    assert parcial["mercados_sem_custo_de_saida"] == 1
    assert conta._soma_do_recorte([])["liquido_com_custo_de_saida_usdc_por_hora"] is None
