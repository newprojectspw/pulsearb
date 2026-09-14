"""O resumo das rotas lê campos que EXISTEM. Verificado percorrendo.

## O defeito que estes testes existem para impedir

Aconteceu duas vezes no projeto, e as duas vezes passou por `ruff` e por
revisão de código:

1. O `#96` pôs `fecha_no_pior_caso` no JSON e o `resumo_m2.py` continuou lendo
   só `o_que_falta_para_fechar`. Um item que podia ser ❌ ficou ⬜ **em toda
   rodada**.
2. A correção disso foi escrita com o caminho **adivinhado**
   (`rota_maker.conta_fechada.limite_pessimista`), e o bloco era IRMÃO de
   `conta_fechada`. Saída byte a byte idêntica à de antes.

Comparar strings não pega nenhum dos dois. O que pega é **percorrer** o caminho
dentro de um relatório que o código produtor acabou de gerar: rename de
qualquer lado quebra o teste, em vez de virar veredito ausente.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]


def _carregar(nome: str):
    """Importa um script de `scripts/` pelo caminho.

    `scripts/` não é pacote instalado — importar por caminho é o que o
    `test_m2_e2e` já faz para o `resumo_m2`.
    """
    spec = importlib.util.spec_from_file_location(nome, RAIZ / "scripts" / f"{nome}.py")
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nome] = modulo
    spec.loader.exec_module(modulo)
    return modulo


rotas = _carregar("resumo_das_rotas")
soma_dos_lados = _carregar("soma_dos_lados")
varredura = _carregar("varredura_de_pools")


def _percorrer(relatorio, caminho: str) -> None:
    """Anda o caminho degrau a degrau, dizendo QUAL degrau faltou."""
    atual = relatorio
    andados: list[str] = []
    for passo in caminho.split("."):
        assert isinstance(atual, dict), (
            f"{'.'.join(andados) or '<raiz>'} não é dict, é {type(atual).__name__}"
        )
        assert passo in atual, (
            f"campo ausente: {passo!r} não está em {'.'.join(andados) or '<raiz>'} "
            f"(tem: {sorted(atual)[:8]})"
        )
        atual = atual[passo]
        andados.append(passo)


# ── 1.11 ─────────────────────────────────────────────────────────────────
def _relatorio_de_soma() -> dict:
    """Um relatório produzido pelo PRÓPRIO `Direcao`, não escrito à mão.

    DUAS observações lucrativas separadas por 1 s, depois uma que fecha. É o
    que constrói um episódio com DURAÇÃO — e escrever isto errado na primeira
    tentativa (uma observação só, duração 0,0 s) foi o teste pegando a minha
    própria confusão: `_Episodio` só estende em observação lucrativa, então
    um instante isolado dura zero por construção. Que é exatamente o que a
    gravação real mostrou em 100% dos casos.
    """
    direcao = soma_dos_lados.Direcao("cunhar_e_vender", latencia_ms=300.0)
    for ts, liquido, idade in (
        (1_000_000_000, 0.02, 0.4),
        (2_000_000_000, 0.03, 0.6),
        (2_500_000_000, -0.03, 900.0),
    ):
        direcao.observar(
            slug="btc-updown-5m-1",
            ts_ns=ts,
            bruto=liquido + 0.03,
            liquido=liquido,
            capacidade=10.0,
            idade_do_outro_ms=idade,
            detalhe={"slug": "btc-updown-5m-1"},
        )
    direcao.finalizar()
    resumo = direcao.resumo()
    return {"cunhar_e_vender": resumo, "comprar_e_fundir": resumo}


@pytest.mark.parametrize(
    "campo",
    [
        rotas.CAMPO_SOMA_VENDER,
        rotas.CAMPO_SOMA_COMPRAR,
        rotas.CAMPO_SOMA_LATENCIA,
        rotas.CAMPO_SOMA_IDADE,
        rotas.CAMPO_SOMA_EPISODIOS,
    ],
)
def test_campo_do_1_11_existe_no_relatorio_de_soma(campo: str) -> None:
    _percorrer(_relatorio_de_soma(), campo)


def test_1_11_passa_quando_um_episodio_sobrevive_a_latencia() -> None:
    """Episódio que DURA mais que a latência é o que conta.

    O episódio construído aqui dura 1 s — acima dos 300 ms —, então 1.11 tem
    de PASSAR. É o ramo positivo: sem ele o teste de ausência sozinho passaria
    com um resumo que nunca diz PASSA.
    """
    relatorio = _relatorio_de_soma()
    assert relatorio["cunhar_e_vender"]["duracao_dos_episodios_s"]["max"] == 1.0
    assert rotas.criterio_1_11(relatorio) == rotas.PASSA


def test_1_11_e_nao_avaliavel_sem_relatorio() -> None:
    """Sem dado, NAO AVALIAVEL — nunca REPROVA.

    A diferença manda instrumentar × manda parar de instrumentar.
    """
    assert rotas.criterio_1_11(None) == rotas.NAO_AVALIAVEL


def test_1_11_reprova_quando_nenhum_episodio_sobrevive() -> None:
    """O caso REAL medido: episódios existem, nenhum dura o bastante."""
    relatorio = _relatorio_de_soma()
    for lado in ("cunhar_e_vender", "comprar_e_fundir"):
        relatorio[lado] = dict(relatorio[lado])
        relatorio[lado]["episodios_que_sobrevivem_a_latencia"] = 0
    assert rotas.criterio_1_11(relatorio) == rotas.REPROVA


# ── 1.12 ─────────────────────────────────────────────────────────────────
def _relatorio_de_pools() -> dict:
    """Consolidação feita pela função de verdade, sobre duas amostras."""
    def amostra(receita: float) -> dict:
        return {
            "condition_id": "0xabc",
            "pergunta": "mercado de teste",
            "daily_rate_usdc": 100.0,
            "score_do_mercado": 50.0,
            "concessao": {"concessao_total_c": 0.0},
            "por_ordem": {
                "1000 shares @ 1 tick(s), 2 lados": {
                    "receita_usdc_por_hora": receita
                }
            },
        }

    ordem = "1000 shares @ 1 tick(s), 2 lados"
    linhas = varredura._consolidar([[amostra(12.0)], [amostra(20.0)]], ordem)
    return {
        "universo": {"repeticoes": 2},
        "mercados": linhas,
        "receita_somada_usdc_por_hora": {
            "pelo_minimo": sum(m["receita_usdc_por_hora_minima"] for m in linhas),
            "pela_mediana": 0.0,
        },
    }


def _relatorio_de_markout() -> dict:
    return {
        "markout": {
            "markout_centavos_por_share": {
                "total": {"5s": {"media": -0.31, "n": 1200}}
            }
        }
    }


@pytest.mark.parametrize(
    "campo", [rotas.CAMPO_POOLS_RECEITA, rotas.CAMPO_POOLS_MERCADOS, rotas.CAMPO_POOLS_REPETICOES]
)
def test_campo_do_1_12_existe_no_relatorio_de_pools(campo: str) -> None:
    _percorrer(_relatorio_de_pools(), campo)


@pytest.mark.parametrize("campo", [rotas.CAMPO_MARKOUT_5S, rotas.CAMPO_MARKOUT_N])
def test_campo_do_1_12_existe_no_relatorio_de_markout(campo: str) -> None:
    _percorrer(_relatorio_de_markout(), campo)


def test_1_12_e_nao_avaliavel_sem_o_markout_deste_regime() -> None:
    """O caso de HOJE, e é o que o critério §2f registrou antes de rodar.

    Receita medida e boa; custo ausente. NAO AVALIAVEL, jamais PASSA — passar
    aqui seria decidir a rota maker com o custo do regime errado, que é
    exatamente o erro que §2f existe para não repetir.
    """
    assert rotas.criterio_1_12(_relatorio_de_pools(), None) == rotas.NAO_AVALIAVEL


def _relatorio_da_conta(liquido_com_saida: float | None, ausente: bool = False) -> dict:
    """O que o conta_do_maker_nos_pools publica desde 2026-09-14."""
    return {
        "markout_usado": {"centavos_por_share_adverso": 0.06},
        "custo_de_saida": {"ausente": ausente},
        "por_recorte": {
            "top_10": {
                "mercados": 10,
                "receita_usdc_por_hora": 44.0,
                "custo_maximo_usdc_por_hora": 1.0,
                "liquido_no_pior_caso_usdc_por_hora": 43.0,
                "liquido_com_custo_de_saida_usdc_por_hora": 5.0,
            },
            "top_95": {
                "mercados": 95,
                "receita_usdc_por_hora": 152.0,
                "custo_maximo_usdc_por_hora": 4.0,
                "liquido_no_pior_caso_usdc_por_hora": 148.0,  # o ÓTIMO
                "liquido_com_custo_de_saida_usdc_por_hora": liquido_com_saida,
            },
        },
        "mercados": [],
    }


def test_1_12_sem_conta_e_nao_avaliavel_desde_o_custo_de_saida() -> None:
    """Até 2026-09-14 três itens fechavam o 1.12. O quarto — custo de saída —
    é o que o critério não media; sem ele, NAO AVALIAVEL, jamais PASSA."""
    assert (
        rotas.criterio_1_12(_relatorio_de_pools(), _relatorio_de_markout())
        == rotas.NAO_AVALIAVEL
    )
    ausente = _relatorio_da_conta(148.0, ausente=True)
    assert (
        rotas.criterio_1_12(_relatorio_de_pools(), _relatorio_de_markout(), ausente)
        == rotas.NAO_AVALIAVEL
    )


def test_1_12_passa_com_os_quatro_itens() -> None:
    assert (
        rotas.criterio_1_12(
            _relatorio_de_pools(), _relatorio_de_markout(), _relatorio_da_conta(+12.5)
        )
        == rotas.PASSA
    )


def test_1_12_reprova_se_o_custo_de_saida_come_o_otimo() -> None:
    """Receita boa, markout de 5 s pequeno, e ainda assim REPROVA: o líquido
    com custo de saída é negativo no recorte que era o ótimo (top_95), mesmo
    que um recorte menor (top_10) continue positivo — o critério é 'nos
    mesmos mercados', não 'em algum recorte'."""
    assert (
        rotas.criterio_1_12(
            _relatorio_de_pools(), _relatorio_de_markout(), _relatorio_da_conta(-30.0)
        )
        == rotas.REPROVA
    )


def test_1_12_reprova_com_receita_abaixo_do_minimo() -> None:
    """Receita baixa REPROVA — não vira NAO AVALIAVEL.

    Os dois vereditos mandam coisas opostas, e confundi-los foi o defeito do
    1.6 que custou semanas.
    """
    pools = _relatorio_de_pools()
    pools["receita_somada_usdc_por_hora"]["pelo_minimo"] = 0.5
    assert (
        rotas.criterio_1_12(pools, _relatorio_de_markout(), _relatorio_da_conta(+12.5))
        == rotas.REPROVA
    )


def test_a_receita_consolidada_usa_o_MINIMO_e_nao_a_media() -> None:
    """Mínimo, não média: média deixa uma foto boa carregar dez ruins.

    Foi exatamente o defeito que fez a primeira varredura publicar 76%/dia.
    """
    relatorio = _relatorio_de_pools()
    mercado = relatorio["mercados"][0]
    assert mercado["receita_usdc_por_hora_minima"] == 12.0
    assert mercado["receita_usdc_por_hora_maxima"] == 20.0
    assert mercado["amostras_que_pontuam"] == 2
    assert mercado["fracao_que_pontua"] == 1.0


# ── `_carregar` contém o caminho (S2083) e segue NÃO AVALIÁVEL ────────────


def test_carregar_recusa_caminho_hostil_e_devolve_none(tmp_path, monkeypatch, capsys):
    """Ausente ou inválido é veredito NÃO AVALIÁVEL — nunca aborto, nunca
    leitura fora da raiz. E a mensagem nomeia a variável de LEITURA."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PULSEARB_RELATORIOS_INPUT_ROOT", raising=False)
    monkeypatch.delenv("PULSEARB_BACKTEST_OUTPUT_ROOT", raising=False)
    for hostil in ("/etc/passwd", "../fora.json", "nao-existe.json"):
        assert rotas._carregar(hostil) is None
    assert "PULSEARB_RELATORIOS_INPUT_ROOT" in capsys.readouterr().err
    assert rotas._carregar(None) is None


def test_carregar_le_relatorio_dentro_da_raiz(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PULSEARB_RELATORIOS_INPUT_ROOT", raising=False)
    monkeypatch.delenv("PULSEARB_BACKTEST_OUTPUT_ROOT", raising=False)
    (tmp_path / "soma.json").write_text('{"ok": true}', encoding="utf-8")
    assert rotas._carregar("soma.json") == {"ok": True}
