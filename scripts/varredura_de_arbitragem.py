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

#: Recusas que valem para o EVENTO inteiro, não para uma passada — elas não
#: mudam de uma amostra para a outra, e contá-las por passada inflava o número
#: até esconder quantos eventos existiam.
RECUSAS_DE_CONJUNTO = frozenset({
    "conjunto_pequeno_demais",
    "conjunto_sem_livro",
    "fechado_sem_resolucao_legivel",
    "evento_ja_decidido",
})

MOTIVOS = {
    "sem_evento_negrisk": "a Gamma não devolveu evento neg-risk nenhum",
    "conjunto_pequeno_demais": "o evento tem menos de 2 resultados",
    "fechado_sem_resolucao_legivel": "há resultado fechado cuja resolução não "
                                    "dá para ler — não sei se ele resolveu SIM",
    "evento_ja_decidido": "um resultado fechado resolveu SIM: o evento acabou "
                          "e os abertos valem zero",
    "conjunto_sem_livro": "há resultado sem livro de ofertas habilitado — não "
                          "dá para comprar a perna dele",
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


def _lista(bruto: Any) -> list | None:
    """A lista que a Gamma manda ora como lista, ora como string JSON."""
    if isinstance(bruto, str):
        try:
            bruto = json.loads(bruto)
        except json.JSONDecodeError:
            return None
    return bruto if isinstance(bruto, list) else None


def resolveu_nao(mercado: dict) -> bool | None:
    """Este resultado FECHADO resolveu NÃO? `None` = não deu para ler.

    Lê o par `outcomes`/`outcomePrices` — o preço final do resultado "Yes" é
    1 quando ele venceu e 0 quando perdeu. **Casado pelo nome, nunca pela
    posição**: presumir que o índice 0 é o "Yes" é exatamente o campo assumido
    a partir do que parecia razoável que produziu os defeitos do §6.1b e do
    §12.13. Não achou o par, ou não achou "Yes" nele: devolve `None`, e quem
    chama recusa.
    """
    nomes = _lista(mercado.get("outcomes"))
    precos = _lista(mercado.get("outcomePrices"))
    if nomes is None or precos is None or len(nomes) != len(precos):
        return None
    for nome, preco in zip(nomes, precos, strict=True):
        if str(nome).strip().lower() in ("yes", "sim"):
            try:
                return float(preco) < 0.5
            except (TypeError, ValueError):
                return None
    return None


def _falta_resolucao(mercado: Any) -> bool:
    """É um resultado FECHADO cuja resolução ainda não dá para ler?"""
    if not isinstance(mercado, dict):
        return False
    fechado = mercado.get("closed") or not mercado.get("active", True)
    return bool(fechado) and resolveu_nao(mercado) is None


def _rota_do_mercado(base_gamma: str, ident: str) -> str:
    """`/markets/{id}` para id numérico, `/markets/slug/{slug}` para slug.

    As duas estão no §2; qual usar sai da forma do identificador, não de
    tentativa e erro.
    """
    if ident.isdigit():
        return f"{base_gamma}/markets/{ident}"
    return f"{base_gamma}/markets/slug/{ident}"


async def _mercado_cheio(get, base_gamma: str, ident: str, cache: dict) -> dict:
    """O mercado completo, buscado UMA vez por identificador.

    Resultado fechado não volta a abrir, então a resolução dele não muda —
    o cache é da natureza do dado, não otimização. Sem ele seriam 18 buscas
    por passada × 599 passadas.
    """
    if ident not in cache:
        try:
            cheio = await get(_rota_do_mercado(base_gamma, ident), {})
        except Exception:
            cheio = None
        cache[ident] = cheio if isinstance(cheio, dict) else {}
    return cache[ident]


async def enriquecer_fechados(
    get, base_gamma: str, evento: dict, cache: dict[str, dict]
) -> None:
    """Busca a resolução dos resultados FECHADOS que o evento não trouxe.

    A rodada de 2026-09-16 na VPS mostrou o alvo: **18 dos 22 eventos** caíram
    em `fechado_sem_resolucao_legivel`, e outros 2 resolveram limpo — ou seja,
    a listagem `/events` traz `outcomes`/`outcomePrices` do mercado aninhado
    ÀS VEZES. Quando não traz, o dado existe em `/markets/{id}` (§2), e pedir
    por ele é uma requisição, não uma suposição.

    Escreve no próprio dicionário do mercado, para o `conjunto_e_exaustivo`
    continuar sendo uma função pura sobre o que está ali. Busca que falha
    deixa o mercado como estava, e a recusa de pé: não achar a resolução é
    estado DESCONHECIDO, nunca "provavelmente resolveu não".
    """
    for mercado in evento.get("markets") or []:
        if not _falta_resolucao(mercado):
            continue
        ident = mercado.get("id") or mercado.get("slug")
        if not isinstance(ident, str | int):
            continue
        cheio = await _mercado_cheio(get, base_gamma, str(ident), cache)
        for campo in ("outcomes", "outcomePrices"):
            if campo not in mercado and campo in cheio:
                mercado[campo] = cheio[campo]


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
        return False, "conjunto_pequeno_demais"
    # A ORDEM DAS CHECAGENS É A ORDEM DO QUE SE APRENDE COM ELAS. "Incompleto"
    # foi o que a primeira rodada devolveu para 20 dos 22 eventos, e não dizia
    # o que fazer: fechado e sem-livro pedem coisas diferentes.
    # RESULTADO FECHADO NÃO MATA A CESTA — e recusar o evento por causa dele
    # foi o que deixou 20 dos 22 eventos da primeira rodada sem medição.
    # Se todos os fechados resolveram NÃO, exatamente um dos ABERTOS vai
    # vencer, e a cesta sobre os abertos paga 1,00 do mesmo jeito.
    #
    # As duas saídas ruins ficam separadas porque pedem coisas diferentes: um
    # fechado que resolveu SIM encerra o evento (os abertos valem zero), e um
    # fechado ilegível é estado DESCONHECIDO — que aqui recusa, como em todo
    # o resto do projeto.
    fechados = [m for m in mercados if m.get("closed") or not m.get("active", True)]
    for m in fechados:
        resposta = resolveu_nao(m)
        if resposta is None:
            return False, "fechado_sem_resolucao_legivel"
        if not resposta:
            return False, "evento_ja_decidido"

    abertos = [m for m in mercados if m not in fechados]
    if len(abertos) < 2:
        return False, "conjunto_pequeno_demais"
    if any(not m.get("enableOrderBook", True) for m in abertos):
        return False, "conjunto_sem_livro"
    return True, ""


def mercados_da_cesta(evento: dict) -> list[dict]:
    """Só os ABERTOS: um resultado que já resolveu NÃO custa zero e não se
    compra. Comprá-lo seria pagar por um bilhete que já perdeu."""
    return [
        m for m in (evento.get("markets") or [])
        if isinstance(m, dict) and not (m.get("closed") or not m.get("active", True))
    ]


async def _pernas_do_evento(
    get, s: Settings, mercados: list[dict]
) -> tuple[list[OrderBook], list[Taxa], str | None]:
    """Livro e taxa de cada perna, ou o motivo de não dar para montar a cesta.

    Sai na PRIMEIRA perna que falta: a cesta é tudo-ou-nada, e uma perna a
    menos desfaz a identidade — o que sobra é posição direcional.
    """
    livros: list[OrderBook] = []
    taxas: list[Taxa] = []
    for m in mercados:
        tokens = tokens_do_mercado(m)
        if not tokens:
            return livros, taxas, "forma_desconhecida"
        taxa = taxa_do_mercado(m)
        if taxa is None:
            return livros, taxas, "perna_sem_taxa"
        livro = await _livro(get, s.endpoints.clob, tokens[0])
        if livro is None or not livro.asks:
            return livros, taxas, "perna_sem_livro"
        livros.append(livro)
        taxas.append(taxa)
    return livros, taxas, None


async def varrer_uma_vez(
    get, s: Settings, eventos: list[dict], cache_de_fechados: dict[str, dict]
) -> list[dict]:
    """Uma passada: para cada evento, a maior cesta que ainda dá lucro."""
    achados = []
    for ev in eventos:
        await enriquecer_fechados(get, s.endpoints.gamma, ev, cache_de_fechados)
        ok, motivo = conjunto_e_exaustivo(ev)
        if not ok:
            achados.append({"evento": ev.get("slug"), "recusa": motivo})
            continue
        livros, taxas, falhou = await _pernas_do_evento(
            get, s, mercados_da_cesta(ev)
        )
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


def _contabilizar(
    achados: list[dict],
    por_evento: dict[str, str],
    recusas: dict[str, int],
    vistos: dict[str, list[float]],
) -> None:
    """Onde cada achado de uma passada entra na conta.

    **Recusa de CONJUNTO é sobre o evento, não sobre a passada.** Contá-la por
    passada dava 11.980 onde havia 20 eventos, e o número grande escondia que
    quase nada tinha sido olhado — foi o que fez a primeira rodada imprimir ❌
    sobre 2 eventos de 22.
    """
    for achado in achados:
        slug = achado.get("evento") or "?"
        if achado.get("recusa") in RECUSAS_DE_CONJUNTO:
            por_evento[slug] = achado["recusa"]
            continue
        por_evento.setdefault(slug, "avaliado")
        if achado.get("recusa"):
            recusas[achado["recusa"]] += 1
        elif achado.get("lucro_usdc"):
            vistos[slug].append(achado["lucro_usdc"])


async def _imprimir_cru(eventos: list[dict], quantos: int = 3) -> None:
    """A forma dos eventos, para o `neg_risk` virar `[VERIFICADO]` no API_NOTES.

    Imprime os RECUSADOS primeiro: são eles que ninguém entendeu ainda. Roda
    DEPOIS do `enriquecer_fechados`, senão mostraria a forma de antes da busca
    e mandaria consertar o que já está consertado.
    """
    recusados = [e for e in eventos if not conjunto_e_exaustivo(e)[0]]
    print(f"\n--- FORMA CRUA de {min(quantos, len(recusados))} evento(s) "
          f"recusado(s), de {len(recusados)} ---")
    for ev in recusados[:quantos]:
        mercados = [m for m in (ev.get("markets") or []) if isinstance(m, dict)]
        print(f"\n  slug={ev.get('slug')!r}  mercados={len(mercados)}")
        print(f"  chaves do evento: {sorted(ev)}")
        for m in mercados[:6]:
            estado = {
                k: m.get(k) for k in
                ("closed", "active", "enableOrderBook", "umaResolutionStatus",
                 "outcomes", "outcomePrices", "groupItemTitle", "id", "slug")
                if k in m
            }
            print(f"    - {estado}")
        if mercados:
            print(f"  chaves de um mercado: {sorted(mercados[0])}")


async def rodar(minutos: float, limite: int, *, cru: bool = False) -> int:
    s = Settings()
    vistos: dict[str, list[float]] = defaultdict(list)
    recusas: dict[str, int] = defaultdict(int)
    #: Um desfecho por EVENTO: "avaliado" ou o motivo que o tirou da conta.
    por_evento: dict[str, str] = {}
    #: Resolução dos resultados fechados, buscada uma vez. Fechado não reabre.
    cache_de_fechados: dict[str, dict] = {}
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
        if cru:
            for ev in eventos:
                await enriquecer_fechados(
                    get, s.endpoints.gamma, ev, cache_de_fechados
                )
            await _imprimir_cru(eventos)
            return 0
        print(f"{len(eventos)} eventos neg-risk. Amostrando por {minutos:g} min…\n")

        while time.monotonic() < fim:
            t0 = time.monotonic()
            try:
                achados = await varrer_uma_vez(get, s, eventos, cache_de_fechados)
            except Exception as erro:
                print(f"\nRECUSA: rede_indisponivel — {type(erro).__name__}: {erro}")
                return 1
            _contabilizar(achados, por_evento, recusas, vistos)
            passadas += 1
            print(f"  passada {passadas}: {len(vistos)} evento(s) com folga", end="\r")
            await asyncio.sleep(max(0.0, 1.0 - (time.monotonic() - t0)))

    _imprimir_veredito(passadas, recusas, vistos, por_evento)
    return 0


def _imprimir_recusas(recusas: dict[str, int]) -> None:
    if not recusas:
        return
    print("\nRECUSAS (não são 'não há arbitragem' — são 'não olhei'):")
    for nome, n in sorted(recusas.items(), key=lambda kv: -kv[1]):
        print(f"  {nome:<24} {n:>6}   {MOTIVOS.get(nome, '')}")


def _imprimir_eventos(por_evento: dict[str, str]) -> list[str]:
    """Quantos eventos existiam, quantos chegaram à conta, e por que os outros
    não chegaram. É a linha que separa medir o mercado de medir o leitor."""
    avaliados = [e for e, d in por_evento.items() if d == "avaliado"]
    print(f"\nEVENTOS: {len(avaliados)} avaliado(s) de {len(por_evento)}")
    fora: dict[str, int] = defaultdict(int)
    for desfecho in por_evento.values():
        if desfecho != "avaliado":
            fora[desfecho] += 1
    for nome, n in sorted(fora.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>3} fora por {nome:<30} {MOTIVOS.get(nome, '')}")
    return avaliados


def _imprimir_veredito(
    passadas: int,
    recusas: dict[str, int],
    vistos: dict[str, list[float]],
    por_evento: dict[str, str],
) -> None:
    print("\n" + "=" * 70)
    print(f"VARREDURA DE ARBITRAGEM POR IDENTIDADE — {passadas} passadas")
    print("=" * 70)
    _imprimir_recusas(recusas)
    avaliados = _imprimir_eventos(por_evento)

    if not vistos:
        # O VEREDITO OLHA QUANTOS FORAM AVALIADOS, e não só se houve lucro.
        # A primeira rodada de verdade (VPS, 2026-09-16, 599 passadas) avaliou
        # 2 de 22 eventos e este script imprimiu ❌ assim mesmo — depois de
        # escrever, três linhas acima, que recusa não é 'não há arbitragem'.
        # É a distinção do `sem_recortes` no 1.6 (medida de ausência × ausência
        # de medida), e eu a violei no meu próprio arquivo.
        if len(avaliados) * 2 < len(por_evento):
            print(
                f"\n  Veredito: NÃO AVALIÁVEL — só {len(avaliados)} de "
                f"{len(por_evento)} eventos chegaram à conta.\n"
                "  Isto NÃO é '❌ não há arbitragem'. É 'a maior parte do\n"
                "  universo não foi olhada', e o que destrava está na tabela\n"
                "  acima. Rode com --cru para ver a forma dos recusados."
            )
            return
        print("\n  NENHUMA oportunidade, e a MAIORIA dos eventos foi avaliada.")
        print("  Veredito: ❌ para a cesta neg-risk — e é um ❌ BARATO,")
        print("  custou uma varredura e não 14 dias. A escada de limiares")
        print("  continua por medir: precisa do agrupamento por ativo e data,")
        print("  e a forma do slug dessas escadas não está verificada.")
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
    p.add_argument(
        "--cru",
        action="store_true",
        help=(
            "imprime a FORMA dos eventos recusados (chaves e estado de cada "
            "resultado) e sai. É assim que um fato de API entra neste projeto: "
            "olhando o que o servidor manda, não o que parecia razoável"
        ),
    )
    args = p.parse_args(argv)
    # VARREDURA QUE NÃO PRODUZIU VEREDITO NÃO É SUCESSO. Sair com 0 depois de
    # um erro faria quem lê (e qualquer automação) tratar como "olhei e não
    # achei" o caso em que não se olhou. É a mesma leitura que o
    # `live/shadow.py` já faz da rodada sem saída — e a primeira execução
    # deste script, com a rede bloqueada, saiu com 0 e um traceback.
    try:
        return asyncio.run(rodar(args.minutos, args.eventos, cru=args.cru))
    except KeyboardInterrupt:
        return 130
    except Exception as erro:
        print(f"RECUSA: {type(erro).__name__}: {erro}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
