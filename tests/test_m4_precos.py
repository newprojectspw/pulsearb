"""M4 — o preço-verdade ao vivo, e a âncora de cada janela.

A âncora é o número mais delicado deste ciclo: o M2 a fixou em τ=0 com 0,9984
de consistência sobre 640 janelas, e ao vivo ela ganha um jeito novo de faltar
— o bot pode ter subido depois de a janela abrir.
"""

from __future__ import annotations

import pytest

from pulsearb.analysis.anchor_sweep import StreamE18
from pulsearb.live.precos import (
    SEM_ANCORA_LACUNA,
    SEM_ANCORA_SEM_ATIVO,
    SEM_ANCORA_SERIE_CURTA,
    PrecosAoVivo,
    SerieE18AoVivo,
)

E18 = 10**18
BASE_MS = 1_787_000_000_000


def _alimentar(precos, *, inicio_ms=BASE_MS, n=90, passo_ms=1000, preco=78_000):
    for i in range(n):
        precos.anotar(
            "btc", valor_e18=int(preco * E18), ts_servidor_ms=inicio_ms + i * passo_ms
        )


class TestAncoraFalhaFechada:
    """`None` não é zero. Inventar âncora erra a janela inteira em silêncio."""

    def test_janela_aberta_antes_do_bot_subir_nao_tem_ancora(self):
        # O caso que só existe ao vivo: a série começa quando o bot começa.
        precos = PrecosAoVivo()
        _alimentar(precos, inicio_ms=BASE_MS)

        ancora = precos.ancora_da_janela(
            asset="btc", condition_id="0x1", abertura_epoch=(BASE_MS - 60_000) / 1000
        )
        assert ancora is None
        assert precos.sem_ancora == {SEM_ANCORA_SERIE_CURTA: 1}

    def test_lacuna_na_abertura_e_outro_diagnostico(self):
        """Série alcança a abertura, mas o feed piscou justo naquele instante.

        `serie_nao_alcanca_a_abertura` é esperado ao subir o bot;
        `lacuna_no_instante_da_abertura` persistente aponta para o feed.
        """
        precos = PrecosAoVivo()
        precos.anotar("btc", valor_e18=78_000 * E18, ts_servidor_ms=BASE_MS)
        # Salto de 60s: a idade máxima da amostra é 10s.
        precos.anotar("btc", valor_e18=78_100 * E18, ts_servidor_ms=BASE_MS + 60_000)

        ancora = precos.ancora_da_janela(
            asset="btc", condition_id="0x1", abertura_epoch=(BASE_MS + 30_000) / 1000
        )
        assert ancora is None
        assert precos.sem_ancora == {SEM_ANCORA_LACUNA: 1}

    def test_ativo_sem_serie(self):
        precos = PrecosAoVivo()
        assert (
            precos.ancora_da_janela(
                asset="doge", condition_id="0x1", abertura_epoch=BASE_MS / 1000
            )
            is None
        )
        assert precos.sem_ancora == {SEM_ANCORA_SEM_ATIVO: 1}


class TestAncoraFixada:
    def test_a_abertura_acontece_uma_vez_so(self):
        """Resolvida, fica. Reler depois daria outro valor com a poda."""
        precos = PrecosAoVivo()
        _alimentar(precos, n=30, preco=78_000)
        abertura = (BASE_MS + 10_000) / 1000

        primeira = precos.ancora_da_janela(
            asset="btc", condition_id="0x1", abertura_epoch=abertura
        )
        # O preço anda muito depois; a âncora não pode andar junto.
        _alimentar(precos, inicio_ms=BASE_MS + 40_000, n=30, preco=99_000)
        segunda = precos.ancora_da_janela(
            asset="btc", condition_id="0x1", abertura_epoch=abertura
        )

        assert primeira == pytest.approx(78_000.0)
        assert segunda == primeira

    def test_esquecer_solta_a_ancora(self):
        precos = PrecosAoVivo()
        _alimentar(precos, n=30)
        precos.ancora_da_janela(
            asset="btc", condition_id="0x1", abertura_epoch=(BASE_MS + 10_000) / 1000
        )
        assert precos.ancoras

        precos.esquecer("0x1")
        assert precos.ancoras == {}


class TestSerieAoVivo:
    def test_a_busca_e_a_MESMA_do_M2(self):
        # Uma segunda cópia de `em()` seria a forma mais silenciosa possível
        # de o SHADOW e o backtest discordarem sobre a âncora.
        viva = SerieE18AoVivo()
        for i in range(10):
            viva.anotar(BASE_MS + i * 1000, (78_000 + i) * E18)

        batch = StreamE18([(BASE_MS + i * 1000, (78_000 + i) * E18) for i in range(10)])
        alvo = BASE_MS + 5_000
        assert viva.em(alvo) == batch.em(alvo)

    def test_chegada_fora_de_ordem_e_inserida_no_lugar(self):
        # `em()` faz busca binária: sem a ordem, ele responde errado.
        viva = SerieE18AoVivo()
        viva.anotar(BASE_MS + 3000, 3 * E18)
        viva.anotar(BASE_MS + 1000, 1 * E18)  # atrasada

        assert viva.fora_de_ordem == 1
        assert viva.em(BASE_MS + 1500) == 1 * E18
        assert viva.em(BASE_MS + 3500) == 3 * E18

    def test_a_poda_nao_come_o_alcance_necessario(self):
        # A janela mais longa é de 4h e a âncora dela é lida na abertura.
        viva = SerieE18AoVivo(historico_s=100.0)
        for i in range(200):
            viva.anotar(BASE_MS + i * 1000, (78_000 + i) * E18)

        assert len(viva) <= 101
        assert not viva.alcanca(BASE_MS)          # podado
        assert viva.alcanca(BASE_MS + 150_000)    # ainda lá


class TestResumo:
    def test_diz_por_que_nao_ha_ancora(self):
        precos = PrecosAoVivo()
        _alimentar(precos, n=30)
        precos.ancora_da_janela(
            asset="btc", condition_id="0x1", abertura_epoch=(BASE_MS - 9999) / 1000
        )

        resumo = precos.resumo()
        assert resumo["ativos"] == 1
        assert resumo["pontos_por_ativo"]["btc"] == 30
        assert resumo["sem_ancora"] == {SEM_ANCORA_SERIE_CURTA: 1}
        assert "recem-iniciado nao opera nada" in resumo["nota"]

    def test_vol_precisa_de_amostra(self):
        # Abaixo de 20 retornos o modelo sai com confiavel=False.
        precos = PrecosAoVivo()
        _alimentar(precos, n=5)
        assert precos.resumo()["vol_pronta"]["btc"] is False

        _alimentar(precos, inicio_ms=BASE_MS + 10_000, n=40)
        assert precos.resumo()["vol_pronta"]["btc"] is True
