"""Gerador de gravação SINTÉTICA para exercitar o pipeline do M2 sem rede.

Deixando explícito o que isto é e o que NÃO é:

- **É**: um gerador determinístico (seed fixa) que produz uma gravação com a
  mesma ESTRUTURA da real — snapshots de descoberta, ticks de TWAP, books do
  CLOB, eventos de resolução. Serve para provar que replay, modelo, book,
  descontos e relatórios funcionam de ponta a ponta.
- **NÃO é**: dado de mercado. Nenhum número que sai daqui diz coisa alguma
  sobre existir edge. O veredito do M2 exige gravação real, e o
  `docs/VEREDITO_M2.md` diz isso com todas as letras.

A geração usa caminhada aleatória com volatilidade fixa e um book construído
em torno da probabilidade verdadeira — então o backtest sobre este dado tende
a mostrar edge ~0 depois das taxas, que é o comportamento correto para um
mercado eficiente por construção.
"""

from __future__ import annotations

import gzip
import math
import random
from pathlib import Path
from typing import Any

import orjson

TWAP_WINDOW_S = 60


def _linha(ts_mono_ns: int, ts_wall_ns: int, fonte: str, payload: Any) -> bytes:
    return orjson.dumps(
        {
            "ts_mono_ns": ts_mono_ns,
            "ts_wall_ns": ts_wall_ns,
            "fonte": fonte,
            "payload": payload,
        }
    )


def gerar_gravacao(
    destino: Path,
    *,
    n_janelas: int = 6,
    duracao_s: int = 300,
    inicio_epoch: int = 1786891500,
    preco_inicial: float = 118_000.0,
    sigma_1s: float = 2e-4,
    seed: int = 42,
    tick_size: float = 0.01,
    fee_rate: float = 0.07,
    fee_exponent: float = 1.0,
    afinar_tick_no_fim: bool = True,
) -> Path:
    """Escreve uma gravação sintética e devolve o caminho do arquivo."""
    rng = random.Random(seed)
    destino.parent.mkdir(parents=True, exist_ok=True)

    linhas: list[bytes] = []

    # UMA série global, contínua. Gerar por janela reiniciava o histórico e
    # deixava a âncora obsoleta — a verdade sintética ficava inconsistente com
    # qualquer hipótese de âncora, e o validador (corretamente) rejeitava todas.
    serie: list[tuple[int, float]] = []
    preco = preco_inicial
    inicio_serie = inicio_epoch - TWAP_WINDOW_S  # aquece o TWAP antes da 1ª janela
    fim_serie = inicio_epoch + n_janelas * duracao_s
    for ts_epoch in range(inicio_serie, fim_serie + 1):
        preco *= math.exp(rng.gauss(0, sigma_1s))
        ts_ns = int(ts_epoch * 1e9)
        serie.append((ts_ns, preco))
        linhas.append(
            _linha(
                ts_ns,
                ts_ns,
                "rtds",
                {
                    "topic": "crypto_prices_twap_sixty",
                    "type": "update",
                    "timestamp": int(ts_epoch * 1000),
                    "payload": {
                        "symbol": "btc/usd",
                        "timestamp": int(ts_epoch * 1000),
                        "value": round(preco, 6),
                        "full_accuracy_value": str(int(preco * 10**18)),
                        "window_s": 60,
                    },
                },
            )
        )

    for indice in range(n_janelas):
        janela_inicio = inicio_epoch + indice * duracao_s
        janela_fim = janela_inicio + duracao_s
        slug = f"btc-updown-5m-{janela_inicio}"
        condition_id = f"0x{indice:064x}"
        token_up = f"{indice}0000000000000000000000000000001"
        token_down = f"{indice}0000000000000000000000000000002"

        # --- verdade sintética: âncora = último valor com ts <= abertura
        abertura_ns = int(janela_inicio * 1e9)
        fecho_ns = int(janela_fim * 1e9)
        antes = [p for ts, p in serie if ts <= abertura_ns]
        ancora = antes[-1]
        corte = fecho_ns - TWAP_WINDOW_S * 10**9
        janela_twap = [p for ts, p in serie if corte <= ts <= fecho_ns]
        twap_final = sum(janela_twap) / len(janela_twap)
        resolveu_up = twap_final >= ancora

        # --- snapshots de descoberta, um por 30s de janela
        for offset in range(0, duracao_s, 30):
            ts_ns = int((janela_inicio + offset) * 1e9)
            restante = duracao_s - offset
            tick = 0.001 if (afinar_tick_no_fim and restante <= 60) else tick_size
            linhas.append(
                _linha(
                    ts_ns,
                    ts_ns,
                    "discovery_snapshot",
                    {
                        "ciclo": indice * 100 + offset,
                        "janelas": [
                            {
                                "slug": slug,
                                "condition_id": condition_id,
                                "asset": "btc",
                                "resolution": "twap60",
                                "token_id_by_outcome": {"Up": token_up, "Down": token_down},
                                "tick_size": tick,
                                "min_order_size": 5,
                                "fee_rate": fee_rate,
                                "fee_exponent": fee_exponent,
                                "fee_taker_only": True,
                                "fee_rebate_rate": 0.2,
                                "accepting_orders": True,
                                "end_date_iso": _iso(janela_fim),
                                "operable": True,
                                "gate_failures": [],
                                "_seconds_left": restante,
                                "best_ask": 0.5,
                            }
                        ],
                        "assinaturas": {"novas": 0, "encerradas": 0, "ativas": 2},
                    },
                )
            )

        # --- books do CLOB, um por 5s, precificados perto da prob verdadeira
        for offset in range(0, duracao_s, 5):
            ts_ns = int((janela_inicio + offset) * 1e9)
            restante = duracao_s - offset
            spot = next((p for ts, p in reversed(serie) if ts <= ts_ns), preco_inicial)
            prob = _prob_verdadeira(spot, ancora, restante, sigma_1s)
            for token, p in ((token_up, prob), (token_down, 1 - prob)):
                mid = min(max(p, 0.02), 0.98)
                linhas.append(
                    _linha(
                        ts_ns,
                        ts_ns,
                        "poly_ws",
                        {
                            "event_type": "book",
                            "asset_id": token,
                            "market": condition_id,
                            "timestamp": str(int(ts_ns / 1e6)),
                            "bids": [
                                {"price": f"{max(mid - 0.01, 0.01):.3f}", "size": "200"},
                                {"price": f"{max(mid - 0.02, 0.01):.3f}", "size": "500"},
                            ],
                            "asks": [
                                {"price": f"{min(mid + 0.01, 0.99):.3f}", "size": "200"},
                                {"price": f"{min(mid + 0.02, 0.99):.3f}", "size": "500"},
                            ],
                        },
                    )
                )

        # --- evento de resolução, ~90s depois do fim
        res_ns = fecho_ns + 90 * 10**9
        for token, venceu in ((token_up, resolveu_up), (token_down, not resolveu_up)):
            linhas.append(
                _linha(
                    res_ns,
                    res_ns,
                    "poly_ws",
                    {
                        "event_type": "market_resolved",
                        "asset_id": token,
                        "market": condition_id,
                        "timestamp": str(int(res_ns / 1e6)),
                        "winning_outcome": "Up" if resolveu_up else "Down",
                        "_venceu": venceu,
                    },
                )
            )

    with gzip.open(destino, "wb", compresslevel=1) as handle:
        for linha in linhas:
            handle.write(linha + b"\n")
    return destino


def _prob_verdadeira(spot: float, ancora: float, restante: float, sigma_1s: float) -> float:
    """Prob de referência para precificar o book sintético."""
    if restante <= 0:
        return 1.0 if spot >= ancora else 0.0
    desvio = sigma_1s * math.sqrt(min(restante, TWAP_WINDOW_S) / 3.0) * spot
    if desvio <= 0:
        return 1.0 if spot >= ancora else 0.0
    z = (ancora - spot) / desvio
    return 1.0 - 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _iso(epoch: int) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(epoch, tz=UTC).isoformat().replace("+00:00", "Z")
