"""`pulsearb.numeros`: a única definição de `numero` e `percentil`.

Auditoria de 2026-09-17, §2.4–2.6. Eram onze parsers e quatro percentis;
dois já tinham divergido em silêncio. Estes testes prendem o contrato que
todas as cópias diziam ter.
"""

from __future__ import annotations

import pytest

from pulsearb.numeros import numero, percentil, percentil_nao_vazio


class TestNumero:
    @pytest.mark.parametrize("bruto", [None, True, False, "", "abc", [], {}, object()])
    def test_o_que_nao_e_numero_vira_None_e_NUNCA_zero(self, bruto):
        assert numero(bruto) is None

    @pytest.mark.parametrize(
        ("bruto", "esperado"),
        [(3, 3.0), (2.5, 2.5), ("1.5", 1.5), ("0", 0.0), ("-2", -2.0), (0, 0.0)],
    )
    def test_int_float_e_string_decimal_viram_float(self, bruto, esperado):
        assert numero(bruto) == esperado
        assert isinstance(numero(bruto), float)

    def test_bool_e_None_mesmo_sendo_int_para_o_python(self):
        """`True` é `int` — `float(True)` dá 1,0. Era o defeito do RTDS."""
        assert isinstance(True, int)
        assert numero(True) is None


class TestPercentil:
    def test_vazio_e_None(self):
        assert percentil([], 50) is None

    def test_nearest_rank_por_teto_em_1_a_10(self):
        """A definição que TODAS as cópias diziam ter — e o `anchor_sweep`
        não tinha (dava 6 e 10 aqui)."""
        dados = list(range(1, 11))
        assert percentil(dados, 50) == 5
        assert percentil(dados, 90) == 9
        assert percentil(dados, 99) == 10
        assert percentil(dados, 100) == 10

    def test_ordena_internamente(self):
        assert percentil([10, 1, 5, 3, 7, 2, 9, 4, 8, 6], 50) == 5

    def test_um_elemento(self):
        assert percentil([42.0], 1) == 42.0
        assert percentil([42.0], 99) == 42.0

    def test_int_continua_int_e_nao_arredonda(self):
        assert percentil([3, 1, 2], 50) == 2
        assert isinstance(percentil([3, 1, 2], 50), int)
        assert percentil([1.123456789, 2.0], 50) == 1.123456789


class TestPercentilNaoVazio:
    def test_devolve_o_numero_sem_Optional(self):
        assert percentil_nao_vazio([3, 1, 2], 50) == 2

    def test_vazia_LEVANTA_em_vez_de_virar_zero(self):
        """`or 0` transformaria ausência em zero — o padrão que o projeto trata
        como defeito. Quem prometeu não-vazia e mentiu recebe a exceção."""
        with pytest.raises(ValueError, match="vazia"):
            percentil_nao_vazio([], 50)
