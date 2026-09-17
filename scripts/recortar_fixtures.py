"""Recorta eventos REAIS da gravação como fixture dos parsers (auditoria §2.9).

    mkdir -p tests/fixtures/reais
    python scripts/recortar_fixtures.py ~/pulsearb-gravacao \
        --desde 2026-09-09T02:19Z --ate 2026-09-09T03:19Z \
        --saida tests/fixtures/reais

## Por que existe

`tests/fixtures/README.md` diz, desde agosto, que `rtds_*.json` e
`clob_ws_book.json` são *"sintético estrutural — substituir por capturas
reais na primeira rodada do recorder"*. A M2_72H tem 287 milhões de
registros e nenhum teste lê um. Os dois defeitos silenciosos deste projeto
(`price_change` §6.1b e `market_resolved` §12.13) passaram nos testes
porque a fixture era o que imaginávamos que o servidor mandava.

## O que faz

Percorre a gravação e guarda os primeiros N registros de cada tipo — o
registro INTEIRO, como o recorder o escreveu (`ts_mono_ns`, `ts_wall_ns`,
`fonte`, `payload` cru) — num `.jsonl` por tipo:

- `poly_ws`: um arquivo por `event_type` (`book`, `price_change`,
  `last_trade_price`, `tick_size_change`, `best_bid_ask`, `new_market`,
  `market_resolved`). Um registro com lote misto conta para cada tipo que
  contém.
- `rtds`: um arquivo por `topic` (`crypto_prices`, `crypto_prices_twap_sixty`…).

Mais um `MANIFESTO.json` com a origem (nome da gravação, período), a
contagem e o intervalo de carimbos por tipo. O teto de bytes por tipo
existe porque `book` é grande e a fixture vai para o repositório.

O que NÃO faz: não altera nada — um registro que o parser não lê hoje é
exatamente o que a fixture existe para revelar. `tests/test_fixtures_reais.py`
consome o recorte e falha se algum parser ler zero.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pulsearb.backtest.__main__ import caminho_de_leitura
from pulsearb.caminhos import caminho_de_escrita
from pulsearb.feeds.poly_ws import eventos_do_payload
from pulsearb.replay.reader import RecordingReader, ReplayRecord

POR_TIPO_PADRAO = 100
MAX_BYTES_POR_TIPO_PADRAO = 512 * 1024
MANIFESTO = "MANIFESTO.json"
#: Onde o `tests/test_fixtures_reais.py` procura o recorte.
PASTA_PADRAO = "tests/fixtures/reais"


def tipos_do_registro(record: ReplayRecord) -> set[str]:
    """`fonte/tipo` de cada evento que o registro carrega. Vazio = não classificável."""
    if record.fonte == "poly_ws":
        tipos = set()
        for ev in eventos_do_payload(record.payload):
            tipo = ev.get("event_type")
            if isinstance(tipo, str) and tipo:
                tipos.add(f"poly_ws/{tipo}")
        return tipos
    if record.fonte == "rtds" and isinstance(record.payload, dict):
        topic = record.payload.get("topic")
        if isinstance(topic, str) and topic:
            return {f"rtds/{topic}"}
    return set()


def _linha(record: ReplayRecord) -> str:
    return json.dumps(
        {
            "ts_mono_ns": record.ts_mono_ns,
            "ts_wall_ns": record.ts_wall_ns,
            "fonte": record.fonte,
            "payload": record.payload,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def recortar(
    reader: RecordingReader,
    *,
    por_tipo: int = POR_TIPO_PADRAO,
    max_bytes_por_tipo: int = MAX_BYTES_POR_TIPO_PADRAO,
) -> dict[str, dict[str, Any]]:
    """Os primeiros `por_tipo` registros de cada tipo, dentro do teto de bytes.

    Devolve `{tipo: {"linhas": [...], "bytes": n, "ts_wall_ns": (min, max),
    "vistos": total_no_periodo}}`. Pura sobre o reader.
    """
    saida: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"linhas": [], "bytes": 0, "ts_wall_ns": None, "vistos": 0}
    )
    for record in reader.iter_records(incluir_meta=False):
        for tipo in tipos_do_registro(record):
            bloco = saida[tipo]
            bloco["vistos"] += 1
            if len(bloco["linhas"]) >= por_tipo:
                continue
            linha = _linha(record)
            tamanho = len(linha.encode("utf-8")) + 1
            if bloco["linhas"] and bloco["bytes"] + tamanho > max_bytes_por_tipo:
                continue
            bloco["linhas"].append(linha)
            bloco["bytes"] += tamanho
            atual = bloco["ts_wall_ns"]
            bloco["ts_wall_ns"] = (
                (record.ts_wall_ns, record.ts_wall_ns)
                if atual is None
                else (min(atual[0], record.ts_wall_ns), max(atual[1], record.ts_wall_ns))
            )
    return dict(saida)


def nome_do_arquivo(tipo: str) -> str:
    fonte, _, nome = tipo.partition("/")
    return f"{fonte}__{nome}.jsonl"


def gravar(
    recorte: dict[str, dict[str, Any]],
    *,
    pasta: str,
    origem: dict[str, Any],
) -> Path:
    """Escreve um `.jsonl` por tipo e o MANIFESTO. Todos contidos pela raiz de saída."""
    manifesto: dict[str, Any] = {
        "gerado_em": datetime.now(UTC).isoformat(timespec="seconds"),
        "origem": origem,
        "tipos": {},
        "nota": (
            "Registros REAIS, como o recorder os escreveu. Fixture sintética prova "
            "que o parser aceita o formato que imaginamos; esta prova que aceita o "
            "que o servidor mandou. Auditoria 2026-09-17 §2.9."
        ),
    }
    for tipo in sorted(recorte):
        bloco = recorte[tipo]
        destino = caminho_de_escrita(f"{pasta}/{nome_do_arquivo(tipo)}", extensoes=(".jsonl",))
        destino.write_text("\n".join(bloco["linhas"]) + "\n", encoding="utf-8")
        inicio, fim = bloco["ts_wall_ns"]
        manifesto["tipos"][tipo] = {
            "arquivo": destino.name,
            "registros": len(bloco["linhas"]),
            "vistos_no_periodo": bloco["vistos"],
            "bytes": bloco["bytes"],
            "ts_wall_utc": [
                datetime.fromtimestamp(inicio / 1e9, UTC).isoformat(timespec="seconds"),
                datetime.fromtimestamp(fim / 1e9, UTC).isoformat(timespec="seconds"),
            ],
        }
    caminho_manifesto = caminho_de_escrita(f"{pasta}/{MANIFESTO}")
    caminho_manifesto.write_text(
        json.dumps(manifesto, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return caminho_manifesto


def _hora_utc(bruto: str | None) -> datetime | None:
    if not bruto:
        return None
    return datetime.fromisoformat(bruto).replace(tzinfo=UTC)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="recortar_fixtures")
    p.add_argument("recordings", help="diretório (ou arquivo) da gravação")
    p.add_argument("--desde", default=None)
    p.add_argument("--ate", default=None)
    p.add_argument("--por-tipo", type=int, default=POR_TIPO_PADRAO)
    p.add_argument("--max-bytes-por-tipo", type=int, default=MAX_BYTES_POR_TIPO_PADRAO)
    p.add_argument("--saida", default=PASTA_PADRAO, help="pasta RELATIVA, já existente")
    args = p.parse_args(argv)
    try:
        caminho = caminho_de_leitura(args.recordings)
        # Sonda: valida a pasta de saída ANTES de ler 73 GB.
        caminho_de_escrita(f"{args.saida}/{MANIFESTO}")
        desde, ate = _hora_utc(args.desde), _hora_utc(args.ate)
        if args.por_tipo <= 0 or args.max_bytes_por_tipo <= 0:
            raise ValueError("--por-tipo e --max-bytes-por-tipo têm de ser positivos")
    except ValueError as erro:
        print(str(erro), file=sys.stderr)
        return 2

    reader = RecordingReader(caminho, desde=desde, ate=ate)
    recorte = recortar(
        reader, por_tipo=args.por_tipo, max_bytes_por_tipo=args.max_bytes_por_tipo
    )
    if not recorte:
        print("nenhum registro classificável no período — nada gravado", file=sys.stderr)
        return 1
    manifesto = gravar(
        recorte,
        pasta=args.saida,
        origem={
            "gravacao": caminho.name,
            "desde": args.desde,
            "ate": args.ate,
            "por_tipo": args.por_tipo,
            "max_bytes_por_tipo": args.max_bytes_por_tipo,
        },
    )
    print(f"manifesto gravado em {manifesto}")
    for tipo in sorted(recorte):
        b = recorte[tipo]
        print(f"  {tipo:<40} {len(b['linhas']):>4} de {b['vistos']:>9} vistos  {b['bytes']:>8} B")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
