"""Testes da curva de fee — valores do M1.B item 4, com r=0.07 e e=1
(os valores REAIS verificados ao vivo, API_NOTES 12.6)."""

import pytest

from pulsearb.engine.fees import fee_pp_por_share, fee_sobre_capital

R, E = 0.07, 1.0


def test_pico_em_meio_a_meio():
    assert fee_pp_por_share(0.50, rate=R, exponent=E) == pytest.approx(0.0175)
    assert fee_sobre_capital(0.50, rate=R, exponent=E) == pytest.approx(0.035)


def test_lado_caro():
    assert fee_pp_por_share(0.90, rate=R, exponent=E) == pytest.approx(0.0063)
    assert fee_sobre_capital(0.90, rate=R, exponent=E) == pytest.approx(0.007)


def test_lado_barato_castiga_o_capital():
    # A MESMA fee por share que em p=0.90…
    assert fee_pp_por_share(0.10, rate=R, exponent=E) == pytest.approx(0.0063)
    # …custa 9x mais como fração do capital. É por isso que existem duas unidades.
    assert fee_sobre_capital(0.10, rate=R, exponent=E) == pytest.approx(0.063)


def test_simetria_por_share():
    for p in (0.2, 0.35, 0.45):
        assert fee_pp_por_share(p, rate=R, exponent=E) == pytest.approx(
            fee_pp_por_share(1 - p, rate=R, exponent=E)
        )


def test_expoente_diferente_de_um():
    # e=0 → fee constante = r, em qualquer preço (sanidade da fórmula).
    assert fee_pp_por_share(0.5, rate=0.02, exponent=0.0) == pytest.approx(0.02)
    assert fee_pp_por_share(0.1, rate=0.02, exponent=0.0) == pytest.approx(0.02)


def test_preco_invalido_explode():
    for p in (0.0, 1.0, -0.2, 1.7):
        with pytest.raises(ValueError):
            fee_pp_por_share(p, rate=R, exponent=E)


def test_parametros_invalidos_explodem():
    with pytest.raises(ValueError):
        fee_pp_por_share(0.5, rate=-0.1, exponent=1)
    with pytest.raises(ValueError):
        fee_pp_por_share(0.5, rate=0.07, exponent=-1)


def test_sem_default_de_rate():
    # rate/exponent são keyword-only e obrigatórios: não existe default
    # escondido (regra da seção 5.3 do API_NOTES).
    with pytest.raises(TypeError):
        fee_pp_por_share(0.5)  # type: ignore[call-arg]
