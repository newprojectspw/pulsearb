"""Os critérios 1.11 e 1.12, lidos dos relatórios e ditos em português.

    .venv/bin/python scripts/resumo_das_rotas.py \
        --soma relatorios/SOMA_72H_20260913.json \
        --pools relatorios/POOLS_PERSIST_20260913.json \
        --markout relatorios/MARKOUT_POOLS_20260913.json

## Por que este arquivo existe

Porque o projeto já pagou por não tê-lo. O `#96` pôs `fecha_no_pior_caso` no
JSON e o `resumo_m2.py` — que é o que alguém de fato lê — continuou imprimindo
`NAO AVALIAVEL` com o texto antigo. **Um item que podia ser ❌ ficou ⬜ em toda
rodada**, e a diferença entre os dois manda instrumentar × manda parar de
instrumentar.

1.11 e 1.12 nascem em relatórios NOVOS, de scripts novos. Sem um leitor, eles
nascem com o mesmo defeito no berço.

## E por que os caminhos são constantes nomeadas

A primeira correção do `limite_pessimista` saiu **sem efeito**: o caminho foi
adivinhado (`rota_maker.conta_fechada.limite_pessimista`) e o bloco era IRMÃO
de `conta_fechada`, não filho. `ruff` passou, a saída ficou byte a byte
idêntica, e só a execução mostrou.

Por isso cada caminho é uma constante, e há teste que **percorre** cada uma
dentro de um relatório produzido pelo código de verdade. Rename de qualquer
lado quebra o teste, em vez de virar veredito ausente.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from pulsearb.caminhos import caminho_de_relatorio_lido

# ── os caminhos que este arquivo lê, nomeados ────────────────────────────
CAMPO_SOMA_VENDER = "cunhar_e_vender.episodios_que_sobrevivem_a_latencia"
CAMPO_SOMA_COMPRAR = "comprar_e_fundir.episodios_que_sobrevivem_a_latencia"
CAMPO_SOMA_LATENCIA = "cunhar_e_vender.latencia_exigida_s"
CAMPO_SOMA_IDADE = "cunhar_e_vender.idade_do_outro_lado_nos_lucrativos_ms"
CAMPO_SOMA_EPISODIOS = "cunhar_e_vender.episodios"

CAMPO_POOLS_RECEITA = "receita_somada_usdc_por_hora.pelo_minimo"
CAMPO_POOLS_MERCADOS = "mercados"
CAMPO_POOLS_REPETICOES = "universo.repeticoes"

CAMPO_MARKOUT_5S = "markout.markout_centavos_por_share.total.5s.media"
CAMPO_MARKOUT_N = "markout.markout_centavos_por_share.total.5s.n"

CAMPO_CONTA_RECORTES = "por_recorte"
CAMPO_CONTA_MERCADOS = "mercados"
CAMPO_CONTA_MARKOUT = "markout_usado.centavos_por_share_adverso"

#: 1.11 — latência que uma ordem leva para chegar (1.1/1.4 mediram 300/600 ms).
LATENCIA_EXIGIDA_S = 0.3

#: 1.12 — receita mínima somada, e persistência mínima por mercado.
RECEITA_MINIMA_USDC_POR_HORA = 10.0
PERSISTENCIA_MINIMA = 0.5

PASSA, REPROVA, NAO_AVALIAVEL = "PASSA", "REPROVA", "NAO AVALIAVEL"


def _ler(relatorio: Any, caminho: str) -> Any:
    """Percorre um caminho pontilhado. `None` se qualquer degrau falta.

    `None` e não exceção porque relatório de versão antiga é caso normal — o
    que não é normal é o resumo dizer PASSA sobre campo que não existe, e
    disso cuida `_julgar`.
    """
    atual = relatorio
    for passo in caminho.split("."):
        if not isinstance(atual, dict) or passo not in atual:
            return None
        atual = atual[passo]
    return atual


def _julgar(ok: bool | None) -> str:
    if ok is None:
        return NAO_AVALIAVEL
    return PASSA if ok else REPROVA


def _linha(veredito: str, num: str, nome: str, exigido: str, medido: str, campo: str) -> None:
    print(f"{veredito:<14} {num:<5} {nome}")
    print(f"               exigido: {exigido}")
    print(f"               medido:  {medido}")
    print(f"               campo:   {campo}")


def _titulo(texto: str) -> None:
    print("\n" + "=" * 74)
    print(texto)
    print("=" * 74)


# ── 1.11 ─────────────────────────────────────────────────────────────────
def criterio_1_11(soma: dict[str, Any] | None) -> str:
    _titulo("1.11 — ARBITRAGEM DE SOMA-DOS-LADOS (VEREDITO_M2 §2e)")
    if soma is None:
        _linha(
            NAO_AVALIAVEL, "1.11", "Arbitragem de soma-dos-lados tomável",
            f">= 1 episódio que sobrevive a {LATENCIA_EXIGIDA_S}s",
            "relatório não informado (--soma)",
            CAMPO_SOMA_VENDER,
        )
        return NAO_AVALIAVEL

    vender = _ler(soma, CAMPO_SOMA_VENDER)
    comprar = _ler(soma, CAMPO_SOMA_COMPRAR)
    if vender is None or comprar is None:
        _linha(
            NAO_AVALIAVEL, "1.11", "Arbitragem de soma-dos-lados tomável",
            f">= 1 episódio que sobrevive a {LATENCIA_EXIGIDA_S}s",
            "campo ausente no relatório — versão antiga do medidor?",
            CAMPO_SOMA_VENDER,
        )
        return NAO_AVALIAVEL

    latencia = _ler(soma, CAMPO_SOMA_LATENCIA) or LATENCIA_EXIGIDA_S
    brutos = _ler(soma, CAMPO_SOMA_EPISODIOS) or 0
    veredito = _julgar((vender + comprar) >= 1)
    _linha(
        veredito, "1.11", "Arbitragem de soma-dos-lados tomável",
        f">= 1 episódio que sobrevive a {latencia}s de latência",
        f"cunhar_e_vender={vender}, comprar_e_fundir={comprar} "
        f"(de {brutos} episódios brutos em cunhar_e_vender)",
        f"{CAMPO_SOMA_VENDER} + {CAMPO_SOMA_COMPRAR}",
    )

    idade = _ler(soma, CAMPO_SOMA_IDADE)
    if isinstance(idade, dict) and idade:
        print("\n  A IDADE DO OUTRO LADO nos instantes lucrativos:")
        total = sum(idade.values())
        for faixa, n in sorted(idade.items()):
            marca = "  <== não é simultâneo" if not faixa.startswith("a:") else ""
            print(f"    {faixa:<14} {n:>8}  ({100 * n / total:5.1f}%){marca}")
        velhos = sum(n for f, n in idade.items() if f.startswith("e:"))
        if velhos:
            print(
                f"\n  {velhos} de {total} ({100 * velhos / total:.0f}%) tinham o livro do par\n"
                f"  com MAIS de 300 ms — mais velho que a latência do próprio bot.\n"
                f"  Ali a soma compara o preço de antes com o preço de agora."
            )
    return veredito


# ── 1.12 ─────────────────────────────────────────────────────────────────
def _conta_fechada(conta: dict[str, Any]) -> None:
    """A conta com os dois lados medidos, e o ÓTIMO impresso.

    O ótimo existe e não é "todos os mercados": volume é custo, e os mercados
    marginais são os de muito volume e pouca receita. Imprimir só o total
    esconderia isso — e foi exatamente o que o primeiro cálculo fez.
    """
    recortes = _ler(conta, CAMPO_CONTA_RECORTES)
    mercados = _ler(conta, CAMPO_CONTA_MERCADOS)
    custo_c = _ler(conta, CAMPO_CONTA_MARKOUT)
    if not isinstance(recortes, dict) or not isinstance(mercados, list):
        return

    print(
        f"\n  A CONTA FECHADA (markout -{custo_c} c/share; custo no PIOR caso,\n"
        "  assumindo que TODO o fluxo taker nos atropela):\n"
    )
    for nome, r in recortes.items():
        print(
            f"    {nome:<10} {r['mercados']:>3} mercados: "
            f"receita {r['receita_usdc_por_hora']:>7.2f} "
            f"- custo {r['custo_maximo_usdc_por_hora']:>7.2f} "
            f"= LIQUIDO {r['liquido_no_pior_caso_usdc_por_hora']:>+8.2f} USDC/h"
        )

    rec = cus = 0.0
    melhor_n, melhor_liq = 0, float("-inf")
    for i, m in enumerate(mercados, 1):
        rec += m["receita_usdc_por_hora"]
        cus += m["custo_maximo_usdc_por_hora"]
        if rec - cus > melhor_liq:
            melhor_n, melhor_liq = i, rec - cus
    capital = 1000 * melhor_n
    print(
        f"\n  OTIMO: {melhor_n} mercados, {melhor_liq:+.2f} USDC/h "
        f"= {melhor_liq * 24:.0f} USDC/dia"
        f"\n         capital estimado ~{capital:,} USDC -> "
        f"{100 * melhor_liq * 24 / capital:.2f}%/dia"
    )
    print(
        "\n  O OTIMO NAO E 'TODOS': volume e custo, e os mercados marginais\n"
        "  sao os de muito volume e pouca receita. Passar dele PIORA."
    )


def criterio_1_12(
    pools: dict[str, Any] | None,
    markout: dict[str, Any] | None,
    conta: dict[str, Any] | None = None,
) -> str:
    _titulo("1.12 — EXISTE RECORTE ONDE A ROTA MAKER SE PAGA (§2f)")

    receita = _ler(pools, CAMPO_POOLS_RECEITA) if pools else None
    mercados = _ler(pools, CAMPO_POOLS_MERCADOS) if pools else None
    repeticoes = _ler(pools, CAMPO_POOLS_REPETICOES) if pools else None
    custo = _ler(markout, CAMPO_MARKOUT_5S) if markout else None
    n_exec = _ler(markout, CAMPO_MARKOUT_N) if markout else None

    persistentes = None
    if isinstance(mercados, list):
        persistentes = [
            m
            for m in mercados
            if isinstance(m, dict)
            and (m.get("fracao_que_pontua") or 0) >= PERSISTENCIA_MINIMA
            and (m.get("receita_usdc_por_hora_minima") or 0) > 0
        ]

    # item 1 — persistência
    _linha(
        _julgar(None if persistentes is None else len(persistentes) >= 1),
        "1.12a", "Persistência da cotação",
        f"pontua em >= {PERSISTENCIA_MINIMA:.0%} das amostras, em >= 1 mercado",
        (
            "relatório não informado (--pools)"
            if persistentes is None
            else f"{len(persistentes)} mercado(s) de {len(mercados)}, "
            f"em {repeticoes} amostra(s)"
        ),
        f"{CAMPO_POOLS_MERCADOS}[*].fracao_que_pontua",
    )

    # item 2 — receita
    _linha(
        _julgar(None if receita is None else receita >= RECEITA_MINIMA_USDC_POR_HORA),
        "1.12b", "Receita somada (pelo MÍNIMO das amostras)",
        f">= {RECEITA_MINIMA_USDC_POR_HORA} USDC/h",
        "relatório não informado (--pools)" if receita is None else f"{receita} USDC/h",
        CAMPO_POOLS_RECEITA,
    )

    # item 3 — custo NESTE regime
    _linha(
        _julgar(None if custo is None else True),
        "1.12c", "Markout medido NESTE regime",
        "existir — não transportado das janelas de 5 min de cripto",
        (
            "AUSENTE — sem ele o 1.12 não é avaliável"
            if custo is None
            else f"{custo} c/share em 5s ({n_exec} execuções)"
        ),
        CAMPO_MARKOUT_5S,
    )

    if receita is None or persistentes is None or custo is None:
        print(
            "\n  >> 1.12 NAO AVALIAVEL: os três itens são CONJUNÇÃO, e falta pelo\n"
            "     menos um. NAO AVALIAVEL não é o mesmo que REPROVADO."
        )
        return NAO_AVALIAVEL

    ok = (
        len(persistentes) >= 1
        and receita >= RECEITA_MINIMA_USDC_POR_HORA
    )
    veredito = _julgar(ok)
    print(f"\n  >> 1.12 {veredito} — os três itens são CONJUNÇÃO.")
    print(
        "\n  Os dois lados no MESMO regime, pela primeira vez:\n"
        f"    receita  {receita:+.4f} USDC/h somados (pelo mínimo das amostras)\n"
        f"    markout  {custo:+.4f} c/share em 5s, sobre {n_exec} execuções"
    )
    if conta is not None:
        _conta_fechada(conta)
    else:
        print(
            "\n  Os dois NÃO se somam direto: a receita é por hora e por\n"
            "  carteira, o markout é por share EXECUTADA. Passe --conta\n"
            "  (scripts/conta_do_maker_nos_pools.py) para fechar."
        )
    return veredito


def _carregar(caminho: str | None) -> dict[str, Any] | None:
    if not caminho:
        return None
    # O caminho vem de fora do programa (--soma/--pools/--markout/--conta) e
    # vai direto ao sistema de arquivos; contê-lo aqui é o que fecha a
    # travessia de caminho (S2083). Ausente ou inválido segue como veredito
    # NÃO AVALIÁVEL, que é o comportamento antigo — nunca aborta.
    try:
        destino = caminho_de_relatorio_lido(caminho)
    except ValueError as erro:
        print(f"aviso: {erro}", file=sys.stderr)
        return None
    return json.loads(destino.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="resumo_das_rotas")
    parser.add_argument("--soma", default=None)
    parser.add_argument("--pools", default=None)
    parser.add_argument("--markout", default=None)
    parser.add_argument("--conta", default=None)
    args = parser.parse_args(argv)

    v11 = criterio_1_11(_carregar(args.soma))
    v12 = criterio_1_12(
        _carregar(args.pools), _carregar(args.markout), _carregar(args.conta)
    )

    _titulo("AS DUAS ROTAS QUE NÃO DEPENDEM DO PREDITOR")
    print(f"  1.11 soma-dos-lados : {v11}")
    print(f"  1.12 rota maker     : {v12}")
    print(
        "\n  Nenhuma das duas prevê nada, então as duas escapam de 1.1, 1.3 e\n"
        "  1.4. NENHUMA escapa do 1.5: capacidade continua sendo o que o livro\n"
        "  comporta, e isso nenhum edge resolve."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
