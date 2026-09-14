"""`scripts/maker_de_pares_nos_pools.py`: o mesmo motor, outro regime.

O que muda em relação ao Up/Down é o começo e o fim: as janelas vêm do
catálogo gravado (`pools_snapshot`), e a perna que fica sozinha é marcada a
PREÇO DE SAÍDA, porque um mercado de pool não resolve dentro da gravação.
São exatamente esses dois pontos que estes testes prendem — o miolo (fila,
recolher, trava, colchão) já está preso em `test_maker_de_pares.py`.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

from pulsearb.replay.reader import ReplayRecord

RAIZ = Path(__file__).resolve().parents[1]


def _carregar(nome: str):
    spec = importlib.util.spec_from_file_location(nome, RAIZ / "scripts" / f"{nome}.py")
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nome] = modulo
    spec.loader.exec_module(modulo)
    return modulo


mpp = _carregar("maker_de_pares_nos_pools")
mp = sys.modules["maker_de_pares"]

YES, NO = "tok-yes", "tok-no"
CID = "0xcond"
T0 = 1_000_000_000_000_000_000
S = 10**9


def _catalogo(ts_ns: int = T0) -> ReplayRecord:
    return ReplayRecord(
        ts_mono_ns=ts_ns,
        ts_wall_ns=ts_ns,
        fonte=mpp.FONTE_CATALOGO,
        payload={
            "mercados": {
                CID: {
                    "tokens": [YES, NO],
                    "slug": "vai-chover",
                    "tick_size": 0.01,
                    "daily_rate": 1234.0,
                }
            }
        },
    )


def _book(token: str, bids: list[tuple[float, float]]) -> dict:
    return {
        "event_type": "book",
        "asset_id": token,
        "bids": [{"price": str(p), "size": str(s)} for p, s in bids],
        "asks": [],
        "timestamp": "0",
    }


def _print(token: str, price: float, size: float) -> dict:
    return {
        "event_type": "last_trade_price",
        "asset_id": token,
        "price": str(price),
        "size": str(size),
        "side": "SELL",
        "timestamp": "0",
    }


def _rec(ts_ns: int, payload: dict) -> ReplayRecord:
    return ReplayRecord(ts_mono_ns=ts_ns, ts_wall_ns=ts_ns, fonte="poly_ws", payload=payload)


class LeitorFalso:
    """Um `RecordingReader` do tamanho do que o índice de fato usa."""

    def __init__(self, records: list[ReplayRecord]) -> None:
        self.records = records
        self.files: list[Path] = []
        self.total = len(records)

    def iter_records(self):
        return iter(self.records)


def _estrategia(**kw) -> mp.Estrategia:
    base = dict(
        melhorar_ticks=0,
        modo="pessimista",
        parar_antes_s=0,
        recolher_ms=None,
        trava_do_par=False,
        salto_bps=None,
        colchao_x=0.0,
    )
    base.update(kw)
    return mp.Estrategia(**base)


def _indice(records: list[ReplayRecord], *, rate_de_saida: float = 0.0):
    e = _estrategia()
    indice = mpp.MakerDeParesNosPools(
        LeitorFalso(records),
        tamanho=20.0,
        reprice_ticks=2,
        estrategias=(e,),
        rate_de_saida=rate_de_saida,
    )
    indice.progresso = SimpleNamespace(
        passada=lambda *a, **k: None,
        talvez=lambda *a, **k: None,
        terminou=lambda *a, **k: None,
    )
    indice.build()
    return indice, e


class TestOCatalogo:
    def test_os_dois_tokens_do_catalogo_viram_uma_janela_cotada(self) -> None:
        indice, _ = _indice([_catalogo()])
        assert set(indice.janela_do_token) == {YES, NO}
        janela = indice.janelas_cotadas["vai-chover"]
        assert (janela.token_up, janela.token_down) == (YES, NO)
        assert janela.tick == 0.01
        # Sem taxa publicada por mercado, o rebate sai ZERO em vez de estimado.
        assert janela.rate == 0.0

    def test_gravacao_sem_catalogo_nao_cota_nada(self) -> None:
        indice, _ = _indice([_rec(T0, _book(YES, [(0.40, 100)]))])
        assert indice.janelas_cotadas == {}


class TestAPernaSoltaVaiAPrecoDeSaida:
    def _com_uma_perna(self, saida_bids: list[tuple[float, float]], **kw):
        records = [
            _catalogo(),
            _rec(T0 + S, _book(YES, [(0.40, 100)])),
            _rec(T0 + 2 * S, _print(YES, 0.38, 5)),
            _rec(T0 + 3 * S, _book(YES, saida_bids)),
        ]
        return _indice(records, **kw)

    def test_marcada_pelo_melhor_bid_do_fim_e_sem_vencedor(self) -> None:
        indice, e = self._com_uma_perna([(0.30, 100)])
        r = indice._resultado_da_janela(indice.janelas_cotadas["vai-chover"], e)
        assert r["q_up"] == 20.0 and r["pares"] == 0.0
        # Comprou a 0,40, sai a 0,30: −0,10 × 20 shares.
        assert abs(r["residual"] - (-2.0)) < 1e-9
        assert r["venceu_up"] is None

    def test_sem_bid_no_fim_a_janela_sai_do_pnl(self) -> None:
        indice, e = self._com_uma_perna([])
        r = indice._resultado_da_janela(indice.janelas_cotadas["vai-chover"], e)
        assert r["residual"] is None
        resumo = indice._resumo_da_estrategia(e, detalhe=False)
        assert resumo["janelas"]["sem_resolucao_excluidas"] == 1
        assert resumo["janelas"]["contadas_no_pnl"] == 0

    def test_o_fee_de_taker_da_saida_entra_quando_pedido(self) -> None:
        indice, e = self._com_uma_perna([(0.30, 100)], rate_de_saida=0.07)
        r = indice._resultado_da_janela(indice.janelas_cotadas["vai-chover"], e)
        fee = 0.07 * 0.30 * 0.70
        assert abs(r["residual"] - 20 * (0.30 - fee - 0.40)) < 1e-9


class TestOParNosPools:
    def test_par_fechado_trava_um_menos_a_soma_como_no_updown(self) -> None:
        records = [
            _catalogo(),
            _rec(T0 + S, _book(YES, [(0.40, 100)])),
            _rec(T0 + S, _book(NO, [(0.55, 100)])),
            _rec(T0 + 2 * S, _print(YES, 0.38, 5)),
            _rec(T0 + 2 * S, _print(NO, 0.53, 5)),
        ]
        indice, e = _indice(records)
        r = indice._resultado_da_janela(indice.janelas_cotadas["vai-chover"], e)
        assert r["pares"] == 20.0
        assert abs(r["travado"] - 20 * (1 - 0.40 - 0.55)) < 1e-9
        assert r["residual"] == 0.0
        # Sem taxa conhecida no regime, o rebate é omitido (limite inferior).
        assert r["rebate"] == 0.0


def test_a_grade_dos_pools_nao_tem_eixo_de_salto() -> None:
    grade = mpp.estrategias_dos_pools()
    assert grade and all(e.salto_bps is None for e in grade)
    assert len({e.nome for e in grade}) == len(grade)
