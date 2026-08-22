#!/usr/bin/env python3
"""Resumo do relatório do backtest: só os campos que decidem o M2.

    python scripts/resumo_m2.py relatorios/hora_1900.json

O relatório inteiro passa de 4.000 linhas de JSON. Ler o veredito nele exige
saber onde procurar, e foi lendo o campo errado que um diagnóstico já saiu
invertido numa conversa real. Este resumo mostra, em uma tela: se a captação
prestou, se a âncora teve amostra, e os dez critérios de VEREDITO_M2 §"Regras
de decisão" com o exigido ao lado do medido.

Não interpreta nada. Se o número for feio, imprime o número feio — é a mesma
regra do relatório que ele resume.
"""

from __future__ import annotations

import json
import signal
import sys

from pulsearb.backtest.__main__ import (
    ENV_RAIZ_DE_SAIDA,
    PADRAO_SAIDA,
    raiz_de_saida,
)


def caminho_do_relatorio(bruto: str):
    """Monta o caminho do relatório a partir da raiz permitida.

    Mesmo tratamento que o `--json` recebeu no M2.5, e pela mesma razão
    registrada lá: conferir o caminho DEPOIS de montá-lo continua entregando
    a string de fora ao sistema de arquivos, e a análise de fluxo do
    SonarCloud aponta isso — com razão. Validar ANTES contra um padrão fixo e
    só então montar a partir de uma raiz confiável não deixa o valor externo
    chegar ao disco em forma nenhuma.

    O relatório é gravado sob essa mesma raiz pelo `--json`, então ler dali é
    simétrico: quem mudou a raiz para gravar usa a mesma variável para ler.
    """
    relativo = bruto.strip().removeprefix("./")
    if not PADRAO_SAIDA.fullmatch(relativo) or not relativo.endswith(".json"):
        raise SystemExit(
            f"nome de relatório inválido: {bruto!r}\n"
            "esperado: caminho relativo terminando em .json, com letras, "
            "dígitos, '-', '_' e '.' (ex.: relatorios/hora_1900.json).\n"
            f"para ler de outra raiz, defina {ENV_RAIZ_DE_SAIDA}."
        )
    raiz = raiz_de_saida()
    resolvido = (raiz / relativo).resolve(strict=False)
    if not resolvido.is_relative_to(raiz.resolve(strict=False)):
        raise SystemExit(f"relatório fora da raiz permitida: {resolvido}")
    if not resolvido.is_file():
        raise SystemExit(f"relatório não encontrado: {resolvido}")
    return resolvido

def main() -> None:
    """Imprime o resumo do relatório nomeado no argumento."""
    if len(sys.argv) != 2:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        raise SystemExit(2)

    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)

    with caminho_do_relatorio(sys.argv[1]).open(encoding="utf-8") as handle:
        d = json.load(handle)
    g, i, a = d.get("gravacao", {}), d.get("integridade", {}), d.get("ancora", {})
    b, m, r = d.get("backtest", {}), d.get("medicoes", {}), d.get("rota_maker", {})


    def p(rotulo, valor):
        print(f"{rotulo:<38} {valor}")


    def _saida_curta(_sinal, _frame):
        """`| head` fecha o cano e o Python morre com BrokenPipeError.

        Um resumo que estoura quando alguém o pipeia é um resumo que não se pode
        pipeiar. Sair em silêncio é o comportamento de qualquer ferramenta de
        linha de comando.
        """
        raise SystemExit(0)


    print("=" * 68)
    print("CAPTACAO  (bloco 0 — decide se a gravacao presta)")
    print("=" * 68)
    cob = (g.get("stream_de_ancora") or {}).get("cobertura_da_gravacao") or {}
    p("pior_fracao_coberta", cob.get("pior_fracao_coberta"))
    for ativo, v in sorted((cob.get("por_ativo") or {}).items()):
        p(f"  {ativo}", f"{v['fracao_da_gravacao']:.1%}  silencio_final={v['silencio_final_s']}s")
    sil = g.get("silencio_do_rtds") or {}
    p("silencios", sil.get("silencios"))
    p("total_s (uniao)", sil.get("total_s"))
    p("conexao_inteira", len(sil.get("silencios_da_conexao_inteira") or []))
    p("suspeita_de_assinatura_caducada", sil.get("suspeita_de_assinatura_caducada"))
    for ev in sil.get("eventos_coincidentes") or []:
        p("  evento coincidente", f"{ev['quantos_ativos']} ativos, dispersao {ev['dispersao_do_inicio_s']}s")
    p("janelas_conhecidas / com_resolucao", f"{g.get('janelas_conhecidas')} / {g.get('janelas_com_resolucao')}")

    print()
    print("=" * 68)
    print("ANCORA")
    print("=" * 68)
    v = a.get("veredito_da_varredura") or {}
    p("elegiveis / recebidas", f"{v.get('janelas_elegiveis')} / {v.get('janelas_recebidas')}  (min {v.get('minimo_para_veredito')})")
    p("sem_cobertura_do_stream", v.get("janelas_sem_cobertura_do_stream"))
    p("consistencia em tau=0", v.get("consistencia_do_tau_verificado"))
    dist = v.get("distribuicao_das_elegiveis") or {}
    p("distribuicao (quartis)", dist.get("quartis"))
    p("concentrada?", dist.get("concentrada"))
    print(f"\n  {v.get('veredito')}\n")

    print("=" * 68)
    print("OS 5 CRITERIOS DO TAKER")
    print("=" * 68)
    res = b.get("resumo") or {}
    p("1. pnl_liquido_usdc @300ms", res.get("pnl_liquido_usdc"))
    p("2. trades  (exige >= 200)", res.get("trades"))
    cal = b.get("calibracao") or {}
    melhor = min(((abs(x["erro"]), k, x["erro"]) for k, x in cal.items()), default=None)
    p("3. melhor erro de calibracao", f"{melhor[2]} no bucket {melhor[1]}" if melhor else "-")
    p("4. pnl @600ms", ((d.get("sensibilidade_latencia") or {}).get("600ms") or {}).get("pnl_liquido_usdc"))
    crit = ((m.get("profundidade") or {}).get("criterio_do_veredito") or {})
    p("5. profundidade p50 3ticks (>=200)", {k: x["p50_3ticks_usdc"] for k, x in (crit.get("por_duracao") or {}).items()})
    ce = d.get("curva_de_edge") or {}
    p("   threshold MORDEU?", f"{ce.get('threshold_mordeu')}  ({ce.get('resultados_distintos')} resultados distintos)")

    print()
    print("=" * 68)
    print("OS 5 CRITERIOS DO MAKER")
    print("=" * 68)
    rv = (r.get("conta_fechada") or {}).get("rebate_vs_markout") or {}
    p("1. saldo centavos/share", rv.get("saldo_centavos_por_share"))
    p("2. markout 5s (>= -0,5)", rv.get("markout_centavos_por_share"))
    p("3. janelas com pool de reward", (r.get("rewards") or {}).get("janelas_com_pool_de_reward"))
    p("4. taxa de divergencia (< 1%)", ((i.get("divergencia_topo_book") or {}).get("taxa")))
    p("   janelas por qualidade", i.get("janelas_por_qualidade"))


if __name__ == "__main__":
    main()
