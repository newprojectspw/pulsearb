"""`scripts/maker_de_pares.py`: o modelo de execução do maker de pares.

O que estes testes prendem é a ARITMÉTICA do simulador, não o resultado
sobre a gravação — esse vai no quadro, com o número. Cada teste monta uma
janela Up/Down à mão, empurra eventos `book`/`price_change`/`last_trade_price`
na forma do fio (§6.1a) e confere o que a estratégia teria feito.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

from pulsearb.feeds.poly_ws import Resolucao
from pulsearb.replay.reader import ReplayRecord

RAIZ = Path(__file__).resolve().parents[1]


def _carregar(nome: str):
    spec = importlib.util.spec_from_file_location(nome, RAIZ / "scripts" / f"{nome}.py")
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nome] = modulo
    spec.loader.exec_module(modulo)
    return modulo


mp = _carregar("maker_de_pares")

UP, DOWN = "tok-up", "tok-down"
SLUG = "btc-updown-5m-1000"
T0 = 1_000_000_000_000_000_000  # ns
S = 10**9


def _estrategia(**kw) -> mp.Estrategia:
    base = dict(
        melhorar_ticks=0,
        modo="pessimista",
        parar_antes_s=30,
        recolher_ms=None,
        trava_do_par=False,
    )
    base.update(kw)
    return mp.Estrategia(**base)


def _indice(*estrategias: mp.Estrategia, tamanho: float = 20.0) -> mp.MakerDePares:
    reader = SimpleNamespace(files=[], total=0)
    indice = mp.MakerDePares(
        reader, tamanho=tamanho, reprice_ticks=2, estrategias=tuple(estrategias)
    )
    indice.progresso = SimpleNamespace(
        passada=lambda *a, **k: None, talvez=lambda *a, **k: None, terminou=lambda *a, **k: None
    )
    janela = mp.Janela(
        slug=SLUG,
        asset="btc",
        token_up=UP,
        token_down=DOWN,
        abertura_ns=T0,
        fim_ns=T0 + 300 * S,
        tick=0.01,
        rate=0.07,
        exponent=1.0,
    )
    indice.janela_do_token[UP] = indice.janela_do_token[DOWN] = janela
    indice.janelas_cotadas[SLUG] = janela
    return indice


def _rec(ts_ns: int, payload: dict) -> ReplayRecord:
    return ReplayRecord(ts_mono_ns=ts_ns, ts_wall_ns=ts_ns, fonte="poly_ws", payload=payload)


def _book(token: str, bids: list[tuple[float, float]], asks: list[tuple[float, float]]) -> dict:
    return {
        "event_type": "book",
        "asset_id": token,
        "bids": [{"price": str(p), "size": str(s)} for p, s in bids],
        "asks": [{"price": str(p), "size": str(s)} for p, s in asks],
        "timestamp": "0",
    }


def _print(token: str, price: float, size: float, side: str = "SELL") -> dict:
    return {
        "event_type": "last_trade_price",
        "asset_id": token,
        "price": str(price),
        "size": str(size),
        "side": side,
        "timestamp": "0",
    }


def _resolver(indice: mp.MakerDePares, *, venceu_up: bool) -> None:
    resolucao = Resolucao(
        condition_id="c",
        tokens=(UP, DOWN),
        winning_token_id=UP if venceu_up else DOWN,
        winning_outcome="Up" if venceu_up else "Down",
        ts_servidor_ms=None,
    )
    indice.resolucoes_por_token[UP] = indice.resolucoes_por_token[DOWN] = resolucao


def _resultado(indice: mp.MakerDePares, estrategia: mp.Estrategia) -> dict:
    return indice._resultado_da_janela(indice.janelas_cotadas[SLUG], estrategia)


class TestOParFechaEmUm:
    def test_print_abaixo_do_bid_executa_o_lote_e_o_par_trava_1_menos_a_soma(self) -> None:
        e = _estrategia()
        i = _indice(e)
        t = T0 + 10 * S
        i._on_poly_book(_rec(t, _book(UP, [(0.60, 100)], [(0.62, 100)])))
        i._on_poly_book(_rec(t, _book(DOWN, [(0.38, 100)], [(0.40, 100)])))
        # Varredura que passou ABAIXO dos dois bids: executa inteiro, nos dois.
        i._on_poly_book(_rec(t + S, _print(UP, 0.58, 5)))
        i._on_poly_book(_rec(t + S, _print(DOWN, 0.36, 5)))
        r = _resultado(i, e)
        assert r["q_up"] == r["q_down"] == 20.0
        assert r["pares"] == 20.0
        assert abs(r["travado"] - 20 * (1 - 0.60 - 0.38)) < 1e-9
        assert r["residual"] == 0.0
        assert r["atravessadas"] == 2
        # Rebate: 0,2 × 0,07 × p(1−p) × shares, por perna.
        esperado = sum(0.2 * 0.07 * p * (1 - p) * 20 for p in (0.60, 0.38))
        assert abs(r["rebate"] - esperado) < 1e-9

    def test_print_de_compra_nao_executa_bid(self) -> None:
        e = _estrategia()
        i = _indice(e)
        t = T0 + 10 * S
        i._on_poly_book(_rec(t, _book(UP, [(0.60, 100)], [(0.62, 100)])))
        i._on_poly_book(_rec(t + S, _print(UP, 0.50, 50, side="BUY")))
        assert _resultado(i, e)["q_up"] == 0.0


class TestAFilaNoNivel:
    def test_print_no_nivel_so_executa_depois_de_consumir_a_fila(self) -> None:
        e = _estrategia()
        i = _indice(e)
        t = T0 + 10 * S
        i._on_poly_book(_rec(t, _book(UP, [(0.60, 100)], [(0.62, 100)])))
        i._on_poly_book(_rec(t + S, _print(UP, 0.60, 60)))
        assert _resultado(i, e)["q_up"] == 0.0
        # Sobram 40 na fila; um print de 50 consome os 40 e nos pega em 10.
        i._on_poly_book(_rec(t + 2 * S, _print(UP, 0.60, 50)))
        r = _resultado(i, e)
        assert r["q_up"] == 10.0
        assert r["no_nivel"] == 1

    def test_modo_otimista_encolhe_a_fila_com_o_nivel(self) -> None:
        e = _estrategia(modo="otimista")
        i = _indice(e)
        t = T0 + 10 * S
        i._on_poly_book(_rec(t, _book(UP, [(0.60, 100)], [(0.62, 100)])))
        # Cancelamentos à frente: o nível cai para 5.
        i._on_poly_book(_rec(t + S, _book(UP, [(0.60, 5)], [(0.62, 100)])))
        i._on_poly_book(_rec(t + 2 * S, _print(UP, 0.60, 15)))
        assert _resultado(i, e)["q_up"] == 10.0


class TestRecolherComLatencia:
    def test_print_dentro_da_latencia_ainda_pega_e_depois_nao(self) -> None:
        e = _estrategia(recolher_ms=100.0)
        i = _indice(e)
        t = T0 + 10 * S
        i._on_poly_book(_rec(t, _book(UP, [(0.60, 100)], [(0.62, 100)])))
        # A ordem só existe no livro depois da latência de envio.
        i._on_poly_book(_rec(t + 50_000_000, _print(UP, 0.50, 5)))
        assert _resultado(i, e)["q_up"] == 0.0
        # O nível à frente sumiu: cancelamento mandado em t+1s, vale em t+1,1s.
        i._on_poly_book(_rec(t + S, _book(UP, [(0.55, 100)], [(0.62, 100)])))
        i._on_poly_book(_rec(t + S + 50_000_000, _print(UP, 0.50, 5)))
        r = _resultado(i, e)
        assert r["q_up"] == 20.0
        assert r["execucoes_durante_o_recolher"] == 1
        assert r["recolhidas"] == 1

    def test_depois_da_latencia_a_ordem_nao_esta_mais_la(self) -> None:
        e = _estrategia(recolher_ms=100.0)
        i = _indice(e)
        t = T0 + 10 * S
        i._on_poly_book(_rec(t, _book(UP, [(0.60, 100)], [(0.62, 100)])))
        i._on_poly_book(_rec(t + S, _book(UP, [(0.55, 100)], [(0.62, 100)])))
        i._on_poly_book(_rec(t + S + 200_000_000, _print(UP, 0.50, 5)))
        assert _resultado(i, e)["q_up"] == 0.0


class TestAPernaSolta:
    def test_vai_a_resolucao_e_sem_resolucao_fica_fora_do_pnl(self) -> None:
        e = _estrategia()
        i = _indice(e)
        t = T0 + 10 * S
        i._on_poly_book(_rec(t, _book(UP, [(0.60, 100)], [(0.62, 100)])))
        i._on_poly_book(_rec(t + S, _print(UP, 0.58, 5)))
        r = _resultado(i, e)
        assert r["pares"] == 0.0 and r["sobra_up"] == 20.0
        assert r["residual"] is None
        resumo = i._resumo_da_estrategia(e, detalhe=False)
        assert resumo["janelas"]["sem_resolucao_excluidas"] == 1
        assert resumo["janelas"]["contadas_no_pnl"] == 0

        _resolver(i, venceu_up=False)
        assert abs(_resultado(i, e)["residual"] - (-0.60 * 20)) < 1e-9
        _resolver(i, venceu_up=True)
        assert abs(_resultado(i, e)["residual"] - (0.40 * 20)) < 1e-9

    def test_a_fracao_que_ganhou_e_medida_contra_o_preco_pago(self) -> None:
        e = _estrategia()
        i = _indice(e)
        t = T0 + 10 * S
        i._on_poly_book(_rec(t, _book(DOWN, [(0.45, 100)], [(0.47, 100)])))
        i._on_poly_book(_rec(t + S, _print(DOWN, 0.40, 5)))
        _resolver(i, venceu_up=False)
        soltas = i._resumo_da_estrategia(e, detalhe=False)["pernas_soltas"]
        assert soltas == {
            "n": 1,
            "ganharam": 1,
            "fracao_que_ganhou": 1.0,
            "preco_medio_pago": 0.45,
            "leitura": soltas["leitura"],
        }


class TestATravaDoPar:
    def test_a_segunda_perna_desce_para_1_menos_p_menos_margem(self) -> None:
        e = _estrategia(trava_do_par=True)
        i = _indice(e)
        t = T0 + 10 * S
        i._on_poly_book(_rec(t, _book(UP, [(0.60, 100)], [(0.62, 100)])))
        i._on_poly_book(_rec(t + S, _print(UP, 0.58, 5)))
        # O Down subiu para 0,80: sem trava juntaríamos lá e o par somaria 1,40.
        i._on_poly_book(_rec(t + 2 * S, _book(DOWN, [(0.80, 100)], [(0.82, 100)])))
        janela = i.janelas_cotadas[SLUG]
        ordem = janela.pernas[(e, DOWN)].ordem
        assert ordem is not None
        assert abs(ordem.preco - 0.39) < 1e-9

    def test_sem_trava_junta_ao_melhor_bid_novo(self) -> None:
        e = _estrategia(trava_do_par=False)
        i = _indice(e)
        t = T0 + 10 * S
        i._on_poly_book(_rec(t, _book(UP, [(0.60, 100)], [(0.62, 100)])))
        i._on_poly_book(_rec(t + S, _print(UP, 0.58, 5)))
        i._on_poly_book(_rec(t + 2 * S, _book(DOWN, [(0.80, 100)], [(0.82, 100)])))
        ordem = i.janelas_cotadas[SLUG].pernas[(e, DOWN)].ordem
        assert ordem is not None and abs(ordem.preco - 0.80) < 1e-9


class TestOSaltoDoSpot:
    def test_spot_que_saltou_recolhe_e_bloqueia_pelo_cooloff(self) -> None:
        e = _estrategia(recolher_ms=100.0, salto_bps=5.0)
        i = _indice(e)
        t = T0 + 10 * S
        i.spot_ts["btc"] = [t - S, t + S]
        i.spot_px["btc"] = [100_000.0, 100_100.0]  # +10 bps em 2 s
        i._on_poly_book(_rec(t, _book(UP, [(0.60, 100)], [(0.62, 100)])))
        assert i.janelas_cotadas[SLUG].pernas[(e, UP)].ordem is not None
        i._on_poly_book(_rec(t + S, _book(UP, [(0.60, 90)], [(0.62, 100)])))
        perna = i.janelas_cotadas[SLUG].pernas[(e, UP)]
        assert perna.recolhidas_por_salto == 1
        # Dentro do cooloff, mesmo com o spot parado, não volta a cotar.
        i.spot_ts["btc"].append(t + 3 * S)
        i.spot_px["btc"].append(100_100.0)
        i._on_poly_book(_rec(t + 4 * S, _book(UP, [(0.60, 90)], [(0.62, 100)])))
        assert perna.ordem is None
        i._on_poly_book(_rec(t + 7 * S, _book(UP, [(0.60, 90)], [(0.62, 100)])))
        assert perna.ordem is not None

    def test_o_salto_e_o_maior_desvio_dentro_do_horizonte(self) -> None:
        i = _indice(_estrategia(salto_bps=1.0))
        t = T0
        i.spot_ts["btc"] = [t, t + S // 2, t + S]
        i.spot_px["btc"] = [100_000.0, 100_200.0, 100_000.0]  # vai e volta
        assert abs(i._salto_bps("btc", t + S) - 20.0) < 1e-9
        assert i._salto_bps("btc", t - S) == 0.0
        assert i._salto_bps("eth", t) == 0.0


class TestOColchao:
    def test_sem_colchao_a_frente_nao_descansa(self) -> None:
        e = _estrategia(recolher_ms=100.0, colchao_x=10.0)
        i = _indice(e)
        t = T0 + 10 * S
        i._on_poly_book(_rec(t, _book(UP, [(0.60, 150)], [(0.62, 100)])))
        perna = i.janelas_cotadas[SLUG].pernas[(e, UP)]
        assert perna.ordem is None
        i._on_poly_book(_rec(t + S, _book(UP, [(0.60, 250)], [(0.62, 100)])))
        assert perna.ordem is not None
        # O colchão encolheu: recolhe.
        i._on_poly_book(_rec(t + 2 * S, _book(UP, [(0.60, 50)], [(0.62, 100)])))
        assert perna.recolhidas_por_colchao == 1


class TestOFimDaCotacao:
    def test_parar_antes_do_fim_cancela_e_nao_recoloca(self) -> None:
        e = _estrategia(parar_antes_s=30)
        i = _indice(e)
        t = T0 + 10 * S
        i._on_poly_book(_rec(t, _book(UP, [(0.60, 100)], [(0.62, 100)])))
        perna = i.janelas_cotadas[SLUG].pernas[(e, UP)]
        assert perna.ordem is not None
        i._on_poly_book(_rec(T0 + 271 * S, _book(UP, [(0.60, 100)], [(0.62, 100)])))
        assert perna.ordem is None and perna.cancelada_no_fim
        i._on_poly_book(_rec(T0 + 272 * S, _print(UP, 0.50, 5)))
        assert perna.executado == 0.0


def test_a_grade_padrao_tem_a_linha_base_que_fica_parada() -> None:
    grade = mp.estrategias_padrao()
    assert any(e.recolher_ms is None and e.salto_bps is None for e in grade)
    assert len({e.nome for e in grade}) == len(grade)


class TestAAtravessadaLimitadaPeloPrint:
    """A hipótese que mais infla fill: print pequeno abaixo do nosso preço."""

    def test_perna_inteira_leva_tudo_e_tamanho_do_print_leva_o_print(self) -> None:
        inteira = _estrategia(atravessada="perna_inteira")
        pelo_print = _estrategia(atravessada="tamanho_do_print")
        i = _indice(inteira, pelo_print)
        t = T0 + 10 * S
        i._on_poly_book(_rec(t, _book(UP, [(0.60, 100)], [(0.62, 100)])))
        i._on_poly_book(_rec(t + S, _print(UP, 0.55, 3)))
        assert _resultado(i, inteira)["q_up"] == 20.0
        assert _resultado(i, pelo_print)["q_up"] == 3.0

    def test_o_resto_continua_no_livro_e_executa_no_print_seguinte(self) -> None:
        e = _estrategia(atravessada="tamanho_do_print")
        i = _indice(e)
        t = T0 + 10 * S
        i._on_poly_book(_rec(t, _book(UP, [(0.60, 100)], [(0.62, 100)])))
        i._on_poly_book(_rec(t + S, _print(UP, 0.55, 3)))
        i._on_poly_book(_rec(t + 2 * S, _print(UP, 0.55, 50)))
        r = _resultado(i, e)
        assert r["q_up"] == 20.0
        assert r["atravessadas"] == 2


class TestAsPecasDosBotsQueLucram:
    """Lote pequeno, viés de inventário e microprice — as três que faltavam.

    São as peças que o estudo dos bots públicos apontou e que este projeto
    ainda não tinha testado: o lote descartável do `poly-maker`, o
    `r = fv − γ·σ·u` de inventário, e cotar a partir do MICROPRICE em vez de
    juntar ao topo. Nenhuma delas pode melhorar o topo do livro.
    """

    def test_o_lote_da_estrategia_manda_no_tamanho_da_perna(self) -> None:
        pequeno = _estrategia(tamanho=5.0)
        grande = _estrategia(tamanho=100.0)
        i = _indice(pequeno, grande, tamanho=20.0)
        t = T0 + 10 * S
        i._on_poly_book(_rec(t, _book(UP, [(0.60, 100)], [(0.62, 100)])))
        i._on_poly_book(_rec(t + S, _print(UP, 0.55, 500)))
        assert _resultado(i, pequeno)["q_up"] == 5.0
        assert _resultado(i, grande)["q_up"] == 100.0

    def test_o_viés_de_inventario_desce_o_bid_do_lado_comprado(self) -> None:
        e = _estrategia(skew_ticks=2)
        i = _indice(e, tamanho=20.0)
        t = T0 + 10 * S
        i._on_poly_book(_rec(t, _book(UP, [(0.60, 100)], [(0.62, 100)])))
        # Executa METADE do lote: o skew entra proporcional (2 × 0,5 = 1 tick).
        i._on_poly_book(_rec(t + S, _print(UP, 0.60, 110)))
        i._on_poly_book(_rec(t + 2 * S, _book(UP, [(0.60, 100)], [(0.62, 100)])))
        perna = i.janelas_cotadas[SLUG].pernas[(e, UP)]
        assert perna.executado == 10.0
        assert perna.ordem is not None
        assert abs(perna.ordem.preco - 0.59) < 1e-9

    def test_sem_viés_a_ordem_volta_ao_topo(self) -> None:
        e = _estrategia(skew_ticks=0)
        i = _indice(e, tamanho=20.0)
        t = T0 + 10 * S
        i._on_poly_book(_rec(t, _book(UP, [(0.60, 100)], [(0.62, 100)])))
        i._on_poly_book(_rec(t + S, _print(UP, 0.60, 110)))
        i._on_poly_book(_rec(t + 2 * S, _book(UP, [(0.60, 100)], [(0.62, 100)])))
        ordem = i.janelas_cotadas[SLUG].pernas[(e, UP)].ordem
        assert ordem is not None and abs(ordem.preco - 0.60) < 1e-9

    def test_o_microprice_puxa_o_alvo_para_baixo_mas_nunca_melhora_o_topo(self) -> None:
        e = _estrategia(delta_do_microprice=1)
        i = _indice(e, tamanho=20.0)
        t = T0 + 10 * S
        # Bid grande, ask pequeno: o microprice fica perto do ASK (0,655), e
        # 1 tick abaixo dele ainda está ACIMA do topo — o alvo continua sendo
        # o topo, porque melhorar o topo é proibido.
        i._on_poly_book(_rec(t, _book(UP, [(0.60, 900)], [(0.66, 100)])))
        ordem = i.janelas_cotadas[SLUG].pernas[(e, UP)].ordem
        assert ordem is not None and abs(ordem.preco - 0.60) < 1e-9
        # Agora o inverso: bid pequeno, ask grande → microprice perto do BID,
        # e o alvo desce abaixo do topo.
        i._on_poly_book(_rec(t + S, _book(UP, [(0.60, 100)], [(0.66, 900)])))
        ordem = i.janelas_cotadas[SLUG].pernas[(e, UP)].ordem
        assert ordem is not None and ordem.preco < 0.60 - 1e-9

    def test_sem_ask_o_microprice_nao_inventa_alvo(self) -> None:
        e = _estrategia(delta_do_microprice=1)
        i = _indice(e, tamanho=20.0)
        i._on_poly_book(_rec(T0 + 10 * S, _book(UP, [(0.60, 100)], [])))
        assert i.janelas_cotadas[SLUG].pernas[(e, UP)].ordem is None
