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


def test_as_pecas_dos_bots_entram_na_celula_do_maker_vivo() -> None:
    from dataclasses import asdict

    grade = mpp.estrategias_dos_pools()
    pecas = grade[-6:]
    base = asdict(pecas[0])
    # A célula do laço ao vivo: 1 tick do meio, recolher na latência medida.
    assert base["distancia_ticks_do_meio"] == 1 and base["recolher_ms"] == 245.0
    diferencas = [
        {k for k, v in asdict(e).items() if v != base[k]} for e in pecas[1:]
    ]
    assert diferencas == [
        {"delta_do_microprice"},
        {"pausa_apos_fill_s"},
        {"reprice_ticks"},
        {"histerese_ticks"},
        {"pausa_apos_fill_s", "reprice_ticks"},
    ]


class TestORewardEntraPeloCaminhoDoBotVivo:
    """A receita é integrada por `estimar_retorno`, a função do `laco_maker`.

    O que estes testes prendem não é o VALOR do reward (esse é da função
    compartilhada, e tem testes próprios), e sim quando ele conta: só com as
    duas pernas repousando, só uma vez por mercado, e nunca através de uma
    lacuna de feed.
    """

    def _params(self):
        from pulsearb.analysis.rewards import ParametrosDeReward

        return {
            "vai-chover": ParametrosDeReward(
                daily_rate=1000.0, min_size=5.0, max_spread=0.05, tick_size=0.01
            )
        }

    def _indice_com_reward(self, records: list[ReplayRecord]):
        e = _estrategia(distancia_ticks_do_meio=1)
        indice = mpp.MakerDeParesNosPools(
            LeitorFalso(records),
            tamanho=20.0,
            reprice_ticks=2,
            estrategias=(e,),
            params_por_slug=self._params(),
        )
        indice.progresso = SimpleNamespace(
            passada=lambda *a, **k: None,
            talvez=lambda *a, **k: None,
            terminou=lambda *a, **k: None,
        )
        indice.build()
        return indice, e

    def _livro(self, token: str, ts_ns: int) -> ReplayRecord:
        return _rec(
            ts_ns,
            {
                "event_type": "book",
                "asset_id": token,
                "bids": [{"price": "0.40", "size": "100"}],
                "asks": [{"price": "0.60", "size": "100"}],
                "timestamp": "0",
            },
        )

    def test_com_as_duas_pernas_repousando_o_reward_acumula(self) -> None:
        records = [_catalogo()]
        for i in range(1, 5):
            records.append(self._livro(YES, T0 + i * S))
            records.append(self._livro(NO, T0 + i * S))
        indice, e = self._indice_com_reward(records)
        assert indice.reward_usdc[(e, "vai-chover")] > 0
        # 2 s, e não 3: o último evento da gravação é o FIM da janela, e ali
        # a cotação é cancelada antes de render — o mesmo que o bot faz ao
        # recolher as ordens no fechamento.
        assert abs(indice.cotacao_segundos[(e, "vai-chover")] - 2.0) < 1e-9

    def test_uma_perna_so_nao_rende(self) -> None:
        records = [_catalogo()] + [self._livro(YES, T0 + i * S) for i in range(1, 5)]
        indice, e = self._indice_com_reward(records)
        assert indice.reward_usdc[(e, "vai-chover")] == 0.0

    def test_lacuna_maior_que_60s_e_truncada(self) -> None:
        records = [
            _catalogo(),
            self._livro(YES, T0 + S),
            self._livro(NO, T0 + S),
            self._livro(YES, T0 + 200 * S),
            self._livro(NO, T0 + 200 * S),
        ]
        indice, e = self._indice_com_reward(records)
        assert indice.cotacao_segundos[(e, "vai-chover")] == 0.0
        assert indice.reward_usdc[(e, "vai-chover")] == 0.0

    def test_sem_parametros_do_mercado_o_reward_nao_e_inventado(self) -> None:
        records = [_catalogo()]
        for i in range(1, 4):
            records.append(self._livro(YES, T0 + i * S))
            records.append(self._livro(NO, T0 + i * S))
        e = _estrategia(distancia_ticks_do_meio=1)
        indice = mpp.MakerDeParesNosPools(
            LeitorFalso(records), tamanho=20.0, reprice_ticks=2, estrategias=(e,)
        )
        indice.progresso = SimpleNamespace(
            passada=lambda *a, **k: None,
            talvez=lambda *a, **k: None,
            terminou=lambda *a, **k: None,
        )
        indice.build()
        assert indice.reward_usdc == {}


class TestOsParametrosDeRewardDaGravacao:
    """Gravação nova traz `rewards_max_spread`; e ele vem em CENTAVOS."""

    def test_o_catalogo_novo_dispensa_o_params_e_converte_a_unidade(self) -> None:
        registro = _catalogo()
        registro.payload["mercados"][CID]["rewards_max_spread"] = 3.0
        registro.payload["mercados"][CID]["rewards_min_size"] = 50.0
        indice, _ = _indice([registro])
        params = indice.params_por_slug["vai-chover"]
        assert params.daily_rate == 1234.0
        assert params.min_size == 50.0
        # 3 centavos → 0,03 de fração. Sem a conversão, a banda sairia 100×
        # maior e TUDO pontuaria.
        assert abs(params.max_spread - 0.03) < 1e-12

    def test_catalogo_antigo_sem_a_banda_nao_inventa_parametro(self) -> None:
        indice, _ = _indice([_catalogo()])
        assert indice.params_por_slug == {}
