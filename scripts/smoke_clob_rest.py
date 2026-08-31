#!/usr/bin/env python3
"""Smoke do REST do CLOB — a metade do item 3.5 que NÃO precisa de credencial.

O item 3.5 diz "código completo, nunca falou com o CLOB de verdade". Falar de
verdade com o endpoint de ordens exige credencial e envia dinheiro; falar com
os endpoints PÚBLICOS não exige nem uma coisa nem outra, e já responde três
perguntas que só a rede real responde:

1. **O host resolve, o TLS fecha, e o servidor responde?** Um erro aqui não
   aparece em teste nenhum com `MockTransport`.
2. **Qual é a LATÊNCIA real?** É o número que derrubou a primeira tentativa do
   SHADOW: o portão de relógio recusava tudo porque o atraso medido passava do
   teto de 250 ms. O conserto foi escopar a recusa ao LIVE, mas a latência em
   si nunca foi medida direito — ficou como "~1.278 ms observado no log".
3. **Os endpoints que o projeto documenta continuam existindo?** Campo que
   sumiu ou rota que mudou de forma é o defeito silencioso do §6.1b e do
   §12.13, que este projeto já pagou duas vezes.

O QUE ESTE SCRIPT NÃO FAZ, E DE PROPÓSITO
──────────────────────────────────────────
- **Não envia ordem.** Nenhum POST, em modo nenhum. Só GET público.
- **Não usa credencial.** Não lê `PULSEARB_CHAVE_PRIVADA` nem as do L2.
- **Não fecha o 3.5.** A parte que falta — uma ordem assinada recebendo
  resposta do servidor — continua faltando depois de rodar isto. O que ele
  remove é a incógnita de encanação, não a de autenticação.

Uso:
    python3 scripts/smoke_clob_rest.py
    python3 scripts/smoke_clob_rest.py --amostras 30
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from typing import Any

import httpx

#: `[VERIFICADO]` API_NOTES §2 — a base do CLOB REST.
CLOB = "https://clob.polymarket.com"

#: O mesmo do `smoke_discovery.py`: sem User-Agent de navegador a borda
#: devolve 403 e o diagnóstico sai como "endpoint sumiu".
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) pulsearb-smoke/0.1"

#: Endpoints públicos que o projeto documenta, com a seção que os verificou.
#: `espera_json` diz se o corpo deve parsear — 200 com HTML é proxy, não CLOB.
SONDAS: tuple[tuple[str, str, str], ...] = (
    ("/ok", "sanidade da borda", "-"),
    ("/time", "relógio do servidor", "-"),
    ("/rewards/markets/current?sponsored=false", "pool de rewards", "§15.1"),
)


async def _uma_medida(
    http: httpx.AsyncClient, caminho: str
) -> tuple[int | None, float, str | None]:
    """(status, ms, erro). Nunca levanta: a falha é o dado."""
    inicio = time.perf_counter()
    try:
        resposta = await http.get(CLOB + caminho, headers={"User-Agent": USER_AGENT})
    except Exception as erro:
        # Larga de propósito, como `fazer_transporte`: timeout, DNS, TLS e
        # reset significam a mesma coisa aqui — não houve resposta —, e
        # enumerar as classes do httpx deixaria a de fora estourar o script.
        return None, (time.perf_counter() - inicio) * 1000.0, type(erro).__name__
    return resposta.status_code, (time.perf_counter() - inicio) * 1000.0, None


async def offset_do_relogio(
    http: httpx.AsyncClient, amostras: int
) -> dict[str, Any] | None:
    """O offset do nosso relógio contra o do servidor, por NTP de pobre.

    Esta é a medição que o sensor do item 3.10 **não consegue fazer**, e a
    razão está escrita lá: ele lê `chegada_local − carimbo_servidor`, que é
    `latência + offset` numa subtração só, com as duas parcelas se cancelando.
    Relógio 400 ms atrasado com 400 ms de latência mede ZERO e passa no portão.

    Aqui há ida E volta, então dá para separar. Com `t0` e `t1` locais em volta
    da resposta e `ts` do servidor:

        offset ≈ ts − (t0 + t1) / 2

    A hipótese é caminho simétrico — ida e volta com a mesma duração. Ela é
    falsa em qualquer rede real, e por isso o erro desta estimativa é da ordem
    da assimetria, não zero. É o mesmo compromisso do NTP, e é o motivo de o
    5.4 exigir um daemon de verdade em vez de confiar nisto.

    Devolve `None` se o endpoint não trouxer um horário legível — inventar
    offset zero seria dar nota máxima ao caso em que a medição não existe.
    """
    offsets: list[float] = []
    for _ in range(amostras):
        t0 = time.time()
        try:
            resposta = await http.get(
                CLOB + "/time", headers={"User-Agent": USER_AGENT}
            )
            t1 = time.time()
            bruto = resposta.text.strip().strip('"')
            ts_servidor = float(bruto)
        except Exception:
            # Amostra perdida é amostra descartada, não offset zero: rede,
            # corpo ilegível e formato inesperado significam "não medi".
            continue
        # O endpoint devolve SEGUNDOS (10 dígitos). Se um dia mudar para
        # milissegundos, o número salta 3 ordens de grandeza e a conta sairia
        # absurda em silêncio — daí a conferência de faixa em vez de confiança.
        if ts_servidor > 1e11:
            ts_servidor /= 1000.0
        offsets.append(ts_servidor - (t0 + t1) / 2.0)
    if not offsets:
        return None
    return {
        "n": len(offsets),
        "offset_p50_s": _percentil(offsets, 0.5),
        "offset_min_s": min(offsets),
        "offset_max_s": max(offsets),
    }


def _percentil(valores: list[float], p: float) -> float:
    """p50/p99 sem numpy. Com uma amostra só, devolve ela mesma."""
    if not valores:
        return float("nan")
    ordenados = sorted(valores)
    if len(ordenados) == 1:
        return ordenados[0]
    posicao = min(int(p * len(ordenados)), len(ordenados) - 1)
    return ordenados[posicao]


async def sondar(
    http: httpx.AsyncClient, caminho: str, amostras: int
) -> dict[str, Any]:
    """Mede um endpoint `amostras` vezes, em série.

    Em SÉRIE, e não em paralelo: paralelo mediria a capacidade de enfileirar
    do servidor, não a latência de ida e volta que a decisão paga.
    """
    tempos: list[float] = []
    status_vistos: set[int] = set()
    erros: list[str] = []
    for _ in range(amostras):
        status, ms, erro = await _uma_medida(http, caminho)
        if erro is not None:
            erros.append(erro)
            continue
        tempos.append(ms)
        if status is not None:
            status_vistos.add(status)
    return {
        "caminho": caminho,
        "amostras_ok": len(tempos),
        "erros": erros,
        "status": sorted(status_vistos),
        "p50_ms": _percentil(tempos, 0.50),
        "p99_ms": _percentil(tempos, 0.99),
        "min_ms": min(tempos) if tempos else float("nan"),
        "max_ms": max(tempos) if tempos else float("nan"),
        "media_ms": statistics.fmean(tempos) if tempos else float("nan"),
    }


def _linha(nome: str, r: dict[str, Any]) -> str:
    if not r["amostras_ok"]:
        return f"  {nome:<34} SEM RESPOSTA  ({', '.join(sorted(set(r['erros'])))})"
    status = ",".join(str(s) for s in r["status"])
    return (
        f"  {nome:<34} status={status:<8} n={r['amostras_ok']:<4}"
        f" p50={r['p50_ms']:>7.1f}ms  p99={r['p99_ms']:>7.1f}ms"
    )


async def principal(amostras: int) -> int:
    print("=" * 78)
    print("SMOKE DO REST DO CLOB — so GET publico, nenhuma ordem, nenhuma credencial")
    print("=" * 78)
    print(f"  base: {CLOB}")
    print(f"  amostras por endpoint: {amostras} (em serie)")
    print()

    resultados = []
    async with httpx.AsyncClient(timeout=10.0) as http:
        for caminho, descricao, secao in SONDAS:
            r = await sondar(http, caminho, amostras)
            r["descricao"] = descricao
            r["secao"] = secao
            resultados.append(r)
            print(_linha(f"{descricao} [{secao}]", r))

    vivos = [r for r in resultados if r["amostras_ok"]]
    print()
    if not vivos:
        print("  NENHUM endpoint respondeu. Nesta maquina isso e rede; no sandbox")
        print("  da nuvem o host esta bloqueado por politica (API_NOTES §1).")
        return 1

    todos = [r["p50_ms"] for r in vivos]
    print("=" * 78)
    print("A LATENCIA, QUE E O NUMERO QUE INTERESSA")
    print("=" * 78)
    print(f"  p50 entre endpoints: {_percentil(todos, 0.5):.1f} ms")
    print(f"  pior p99 medido:     {max(r['p99_ms'] for r in vivos):.1f} ms")
    print()
    print("  Compare com `atraso_max_ms` (250 ms, item 3.10). Se a latencia de")
    print("  ida e volta ja passa do teto, o portao de relogio recusaria TODA")
    print("  ordem em LIVE — e foi exatamente isso que apagou o diario da")
    print("  primeira tentativa do SHADOW, antes de a recusa ser escopada ao LIVE.")
    print()
    print("  ATENCAO: ida-e-volta NAO e o atraso que o portao mede. O portao le")
    print("  `carimbo_do_servidor - chegada_local`, que e UMA via mais o offset")
    print("  do relogio (§3.10). Este numero e o teto grosseiro daquele, e serve")
    print("  para dizer a ordem de grandeza, nao para substituir a medicao.")
    print()

    async with httpx.AsyncClient(timeout=10.0) as http:
        offset = await offset_do_relogio(http, amostras)
    print("=" * 78)
    print("OFFSET DO RELOGIO LOCAL  (o que o sensor de uma via NAO separa)")
    print("=" * 78)
    if offset is None:
        print("  /time nao devolveu horario legivel — offset NAO medido.")
        print("  Nao assuma zero: e o defeito do `cobertura_da_gravacao`.")
    else:
        p50 = offset["offset_p50_s"]
        espalhamento = offset["offset_max_s"] - offset["offset_min_s"]
        print(
            f"  n={offset['n']}   offset p50 = {p50:+.3f} s"
            f"   (min {offset['offset_min_s']:+.3f} / max"
            f" {offset['offset_max_s']:+.3f}, espalhamento {espalhamento:.3f} s)"
        )
        print()
        print("  Positivo = nosso relogio ATRASADO; negativo = ADIANTADO.")
        print("  Atrasado e o caso caro: infla `seconds_left`, e o bot opera")
        print("  achando que sobra mais tempo do que sobra (§3.10).")
        print()
        # O aviso sai do DADO. Com latencia de centenas de ms, a assimetria de
        # caminho entra inteira na estimativa; quando o espalhamento passa do
        # proprio p50, o numero nao distingue adiantado de atrasado, e citar o
        # p50 sozinho seria reportar precisao que a medicao nao tem.
        if espalhamento >= abs(p50):
            print(
                f"  !! O espalhamento ({espalhamento:.3f} s) e MAIOR que |p50|"
                f" ({abs(p50):.3f} s).\n"
                "     Esta medicao NAO separa adiantado de atrasado — ela diz"
                " so a ordem\n"
                "     de grandeza (sub-segundo). Nao cite o p50 como se fosse"
                " o offset."
            )
            print()
        print("  Hipotese: caminho simetrico. Falsa em rede real, entao o erro")
        print("  e da ordem da assimetria. NAO substitui o daemon de NTP (5.4).")
    print()
    print("  Nada aqui fecha o 3.5: falta uma ORDEM ASSINADA recebendo resposta.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--amostras", type=int, default=15, help="medidas por endpoint (default: 15)"
    )
    args = parser.parse_args()
    if args.amostras < 1:
        raise SystemExit("--amostras precisa ser >= 1")
    return asyncio.run(principal(args.amostras))


if __name__ == "__main__":
    raise SystemExit(main())
