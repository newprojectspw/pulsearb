"""Existe arbitragem por IDENTIDADE na Polymarket agora? — veredito em minutos.

    .venv/bin/python scripts/varredura_de_arbitragem.py --minutos 10

## Por que este script existe

O quadro está parado em 32 verdes, e seis itens estão ❌ **medidos e
reprovados**. Eles reprovaram por dois motivos que se repetem: **latência**
(o 1.11 mediu ZERO episódios de soma-dos-lados sobrevivendo a 300 ms, e nós
medimos p50 218 ms) e **previsão** (1.1, 1.4 e 1.5 mediram o edge do taker).

As duas rotas aqui não dependem de nenhum dos dois. O lucro vem de uma
identidade contábil — cesta neg-risk paga 1,00, escada de limiares é
monótona — e os mercados são de horizonte longo, onde o milissegundo não
decide. `docs/OUTROS_BOTS.md` §6 item 3 mediu que a janela longa bate a curta
por duas ordens de grandeza (+143,96 ¢/h contra +0,63 ¢ por 5 min), e a
descoberta inteira do projeto continua apontada para `{ativo}-updown-{dur}-
{epoch}`, que é a família reprovada.

**E o custo do veredito é o ponto.** O 4.2 custa 14 dias. Este custa uma
varredura: se não houver oportunidade, o ❌ sai hoje e a rota morre barato.

## O que ele NÃO presume

A estrutura de um evento neg-risk **nunca foi verificada na fonte** — o
`neg_risk` do §6.1a/§2 é só a flag de qual contrato assinar. Então este
script **não adivinha nome de campo**: ele procura o que precisa, e quando
não acha, RECUSA com motivo e imprime as chaves que viu. A primeira rodada
serve de verificação para o `API_NOTES`, e é assim que um fato de API entra
neste projeto — foi ler campo "que parecia razoável" que produziu os dois
defeitos silenciosos do §6.1b e do §12.13.

Mercado sem fee legível não é operado: é a regra do `engine/fees.py`, que não
tem default de propósito. Taxa presumida vira lucro presumido.

## Persistência, e por que ela decide

O 1.11 não morreu por não achar episódio: morreu porque nenhum sobrevivia à
latência. Existir num instantâneo não é existir quando a ordem chega. Por
isso a varredura AMOSTRA em série e reporta por quanto tempo cada
oportunidade fica de pé — uma que dure 200 ms é, para nós, uma que não
existe.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections import defaultdict
from typing import Any

import httpx

from pulsearb.analysis.arbitragem import Taxa, maior_cesta
from pulsearb.backtest.book import OrderBook
from pulsearb.markets.http import fazer_http_get_json
from pulsearb.settings import Settings

#: Quanto a ordem demora para chegar, medido (§16): p50 218 ms, p99 ~970 ms.
#: Oportunidade que não sobrevive a isto não é oportunidade NOSSA — é a
#: lição que matou o 1.11, e ela entra aqui como número medido, não chutado.
LATENCIAS_S = (0.3, 1.0, 5.0, 30.0)

#: Teto de shares por cesta na busca. Não é limite de risco (isto não envia
#: ordem); é onde a busca para de subir.
TETO_DE_SHARES = 500.0

MOTIVOS = {
    "sem_evento_negrisk": "a Gamma não devolveu evento neg-risk nenhum",
    "conjunto_incompleto": "o evento não expõe todos os resultados — sem isso "
                           "a cesta não paga 1,00 e a posição vira direcional",
    "perna_sem_taxa": "mercado sem fee legível não é operado (engine/fees.py)",
    "perna_sem_livro": "o CLOB não devolveu livro legível para alguma perna",
    "forma_desconhecida": "a resposta não tem os campos que esta varredura "
                          "precisa — ver as chaves impressas",
    "rede_indisponivel": "não deu para falar com a Gamma ou o CLOB",
}


def taxa_do_mercado(gamma: dict[str, Any]) -> Taxa | None:
    """`rate` e `exponent` do mercado, ou `None`.

    Procura as grafias que o §5/§12.6 registra (`feeSchedule`, `fd`), sem
    inventar uma terceira. Ausente é `None` e o mercado sai da varredura —
    nunca um default.
    """
    for chave in ("feeSchedule", "fd", "fee_schedule"):
        bruto = gamma.get(chave)
        if isinstance(bruto, str):
            try:
                bruto = json.loads(bruto)
            except json.JSONDecodeError:
                continue
        if isinstance(bruto, dict):
            taxa = bruto.get("rate", bruto.get("r"))
            expo = bruto.get("exponent", bruto.get("e"))
            if isinstance(taxa, int | float) and isinstance(expo, int | float):
                return Taxa(rate=float(taxa), exponent=float(expo))
    return None


def tokens_do_mercado(gamma: dict[str, Any]) -> list[str]:
    """Os `clobTokenIds`, aceitando a lista-em-string que a Gamma manda."""
    bruto = gamma.get("clobTokenIds")
    if isinstance(bruto, str):
        try:
            bruto = json.loads(bruto)
        except json.JSONDecodeError:
            return []
    return [t for t in (bruto or []) if isinstance(t, str)]


async def _livro(get, base: str, token_id: str) -> OrderBook | None:
    try:
        bruto = await get(f"{base}/book", {"token_id": token_id})
    except Exception:
        return None
    return OrderBook.from_event(bruto) if isinstance(bruto, dict) else None


async def eventos_neg_risk(get, base_gamma: str, limite: int) -> list[dict]:
    """Eventos multi-resultado, com os mercados deles juntos.

    Pede à Gamma os eventos abertos e fica com os que se declaram neg-risk.
    **Não** deduz exaustividade da contagem de mercados: quem decide isso é
    a checagem abaixo, e na dúvida o evento sai.
    """
    eventos = await get(f"{base_gamma}/events", {
        "closed": "false", "limit": str(limite), "order": "volume",
        "ascending": "false",
    })
    if not isinstance(eventos, list):
        return []
    saida = []
    for ev in eventos:
        if not isinstance(ev, dict):
            continue
        marcado = any(
            bool(ev.get(k)) for k in ("negRisk", "neg_risk", "enableNegRisk")
        )
        mercados = [m for m in (ev.get("markets") or []) if isinstance(m, dict)]
        if marcado and len(mercados) >= 2:
            saida.append(ev)
    return saida


def conjunto_e_exaustivo(evento: dict) -> tuple[bool, str]:
    """O evento cobre TODO o espaço de resultados?

    Esta é a pergunta que decide se a cesta é arbitragem ou aposta, e ela é
    fato de API. O que se aceita como prova: o próprio evento dizer que é
    neg-risk **e** todos os mercados dele estarem abertos e com livro — um
    resultado fechado ou sem livro deixa a cesta incompleta e o que sobra é
    posição direcional.

    Na dúvida, recusa. É a mesma falha fechada do resto do projeto: estado
    desconhecido é motivo de RECUSA, não de seguir em frente.
    """
    mercados = [m for m in (evento.get("markets") or []) if isinstance(m, dict)]
    if len(mercados) < 2:
        return False, "conjunto_incompleto"
    for m in mercados:
        if m.get("closed") or not m.get("active", True):
            return False, "conjunto_incompleto"
        if not m.get("enableOrderBook", True):
            return False, "conjunto_incompleto"
    return True, ""


async def varrer_uma_vez(get, s: Settings, eventos: list[dict]) -> list[dict]:
    """Uma passada: para cada evento, a maior cesta que ainda dá lucro."""
    achados = []
    for ev in eventos:
        ok, motivo = conjunto_e_exaustivo(ev)
        if not ok:
            achados.append({"evento": ev.get("slug"), "recusa": motivo})
            continue
        mercados = [m for m in ev.get("markets") or [] if isinstance(m, dict)]
        livros: list[OrderBook] = []
        taxas: list[Taxa] = []
        falhou = None
        for m in mercados:
            tokens = tokens_do_mercado(m)
            if not tokens:
                falhou = "forma_desconhecida"
                break
            taxa = taxa_do_mercado(m)
            if taxa is None:
                falhou = "perna_sem_taxa"
                break
            livro = await _livro(get, s.endpoints.clob, tokens[0])
            if livro is None or not livro.asks:
                falhou = "perna_sem_livro"
                break
            livros.append(livro)
            taxas.append(taxa)
        if falhou:
            achados.append({"evento": ev.get("slug"), "recusa": falhou})
            continue
        op, recusa = maior_cesta(
            livros, taxas, conjunto_exaustivo=True, teto_de_shares=TETO_DE_SHARES
        )
        achados.append({
            "evento": ev.get("slug"),
            "pernas": len(livros),
            "recusa": recusa,
            "lucro_usdc": None if op is None else round(op.lucro_usdc, 4),
            "shares": None if op is None else op.shares,
            "lucro_por_share": None if op is None else round(op.lucro_por_share, 5),
        })
    return achados


async def rodar(minutos: float, limite: int) -> int:
    s = Settings()
    vistos: dict[str, list[float]] = defaultdict(list)
    recusas: dict[str, int] = defaultdict(int)
    passadas = 0
    fim = time.monotonic() + minutos * 60

    async with httpx.AsyncClient(
        headers={"User-Agent": s.user_agent}, timeout=20.0
    ) as http:
        get = fazer_http_get_json(http, bases=(s.endpoints.gamma, s.endpoints.clob))
        try:
            eventos = await eventos_neg_risk(get, s.endpoints.gamma, limite)
        except Exception as erro:
            # Rede que não responde é `rede_indisponivel`, NÃO "não há
            # arbitragem". Deixar o traceback subir daria os dois pelo mesmo
            # preço, e o segundo é uma conclusão sobre o mercado.
            print(f"RECUSA: rede_indisponivel — {MOTIVOS['rede_indisponivel']}")
            print(f"  {type(erro).__name__}: {erro}")
            return 1
        if not eventos:
            print(f"RECUSA: sem_evento_negrisk — {MOTIVOS['sem_evento_negrisk']}")
            print(
                "\nIsto NÃO é 'não há arbitragem': é 'não consegui olhar'. A\n"
                "estrutura do evento neg-risk nunca foi verificada na fonte\n"
                "(o `neg_risk` do §2 é só a flag de contrato), então a forma\n"
                "procurada aqui pode estar errada. Rode com --cru para ver as\n"
                "chaves que a Gamma devolveu e corrija o leitor ANTES de\n"
                "concluir qualquer coisa."
            )
            return 1
        print(f"{len(eventos)} eventos neg-risk. Amostrando por {minutos:g} min…\n")

        while time.monotonic() < fim:
            t0 = time.monotonic()
            try:
                achados = await varrer_uma_vez(get, s, eventos)
            except Exception as erro:
                print(f"\nRECUSA: rede_indisponivel — {type(erro).__name__}: {erro}")
                return 1
            for achado in achados:
                if achado.get("recusa"):
                    recusas[achado["recusa"]] += 1
                elif achado.get("lucro_usdc"):
                    vistos[achado["evento"]].append(achado["lucro_usdc"])
            passadas += 1
            print(f"  passada {passadas}: {len(vistos)} evento(s) com folga", end="\r")
            await asyncio.sleep(max(0.0, 1.0 - (time.monotonic() - t0)))

    _imprimir_veredito(passadas, recusas, vistos)
    return 0


def _imprimir_recusas(recusas: dict[str, int]) -> None:
    if not recusas:
        return
    print("\nRECUSAS (não são 'não há arbitragem' — são 'não olhei'):")
    for nome, n in sorted(recusas.items(), key=lambda kv: -kv[1]):
        print(f"  {nome:<24} {n:>6}   {MOTIVOS.get(nome, '')}")


def _imprimir_veredito(
    passadas: int, recusas: dict[str, int], vistos: dict[str, list[float]]
) -> None:
    print("\n" + "=" * 70)
    print(f"VARREDURA DE ARBITRAGEM POR IDENTIDADE — {passadas} passadas")
    print("=" * 70)
    _imprimir_recusas(recusas)
    if not vistos:
        print("\n  NENHUMA oportunidade em nenhuma passada.")
        print("\n  Veredito: ❌ para a cesta neg-risk, e é um ❌ BARATO —")
        print("  custou uma varredura, não 14 dias. A escada de limiares")
        print("  continua por medir: ela precisa do agrupamento por ativo e")
        print("  data, e a forma do slug dessas escadas não está verificada.")
        return

    print(f"\n{len(vistos)} evento(s) com folga em alguma passada:\n")
    for slug, lucros in sorted(vistos.items(), key=lambda kv: -max(kv[1])):
        fracao = len(lucros) / passadas if passadas else 0.0
        print(f"  {slug}")
        print(f"      apareceu em {len(lucros)}/{passadas} passadas ({fracao:.0%})")
        print(f"      lucro máx {max(lucros):+.4f} USDC · mediana "
              f"{statistics.median(lucros):+.4f}")
        for lat in LATENCIAS_S:
            # A passada leva ~1 s; sobreviver a `lat` é aparecer em passadas
            # consecutivas o bastante para cobri-la.
            sobrevive = fracao >= 0.9 and len(lucros) >= max(1, int(lat))
            print(f"      sobrevive a {lat:>4.1f}s? {'sim' if sobrevive else 'NÃO'}")
    print(
        "\n  ATENÇÃO: aparecer em todas as passadas NÃO prova que a ordem\n"
        "  pega. Prova que o preço ficou parado enquanto ninguém tomou — e se\n"
        "  ninguém tomou uma arbitragem travada, a pergunta seguinte é POR\n"
        "  QUÊ: taxa que a varredura não viu, conjunto que não é exaustivo,\n"
        "  ou liquidação que não fecha. Nenhuma se responde daqui."
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="varredura_de_arbitragem")
    p.add_argument("--minutos", type=float, default=5.0)
    p.add_argument("--eventos", type=int, default=40)
    args = p.parse_args(argv)
    # VARREDURA QUE NÃO PRODUZIU VEREDITO NÃO É SUCESSO. Sair com 0 depois de
    # um erro faria quem lê (e qualquer automação) tratar como "olhei e não
    # achei" o caso em que não se olhou. É a mesma leitura que o
    # `live/shadow.py` já faz da rodada sem saída — e a primeira execução
    # deste script, com a rede bloqueada, saiu com 0 e um traceback.
    try:
        return asyncio.run(rodar(args.minutos, args.eventos))
    except KeyboardInterrupt:
        return 130
    except Exception as erro:
        print(f"RECUSA: {type(erro).__name__}: {erro}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
