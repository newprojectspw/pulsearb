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
import re
import sys
from collections import defaultdict
from datetime import UTC, datetime
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
#: Forma aceite para `event_type` e `topic`: viram nome de arquivo, e vêm do
#: CONTEÚDO da gravação. Um tipo fora disto não é classificado — nem gravado.
NOME_DE_TIPO = re.compile(r"[a-z0-9_]+")
#: Registro de `poly_ws`/`rtds` cujo conteúdo o parser de envelope não lê.
#: Vai para o recorte com este nome em vez de ser descartado: um envelope
#: desconhecido é EXATAMENTE o que a fixture existe para revelar, e o
#: `tests/test_fixtures_reais.py` falha se esta categoria aparecer.
NAO_CLASSIFICADO = "_nao_classificado"


def tipos_do_registro(record: ReplayRecord) -> set[str]:
    """`fonte/tipo` de cada evento que o registro carrega.

    `poly_ws` e `rtds` são as fontes do fio: um registro delas que não
    classifica vai para `fonte/_nao_classificado`, nunca para o lixo. Outras
    fontes (meta, binance_ws) devolvem vazio e ficam de fora do recorte.
    """
    if record.fonte == "poly_ws":
        tipos = set()
        for ev in eventos_do_payload(record.payload):
            tipo = ev.get("event_type")
            if isinstance(tipo, str) and NOME_DE_TIPO.fullmatch(tipo):
                tipos.add(f"poly_ws/{tipo}")
        return tipos or {f"poly_ws/{NAO_CLASSIFICADO}"}
    if record.fonte == "rtds":
        topic = record.payload.get("topic") if isinstance(record.payload, dict) else None
        if isinstance(topic, str) and NOME_DE_TIPO.fullmatch(topic):
            return {f"rtds/{topic}"}
        return {f"rtds/{NAO_CLASSIFICADO}"}
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
    desde_ns: int | None = None,
    ate_ns: int | None = None,
) -> dict[str, dict[str, Any]]:
    """Os primeiros `por_tipo` registros de cada tipo, dentro do teto de bytes.

    `desde_ns`/`ate_ns` cortam por `ts_wall_ns` de CADA registro. O
    `RecordingReader` usa `desde`/`ate` só para escolher os arquivos-hora,
    com uma hora de margem de cada lado; sem este corte o manifesto rotularia
    como "do período" registros de fora dele (revisão do Codex no #149).

    Devolve `{tipo: {"linhas": [...], "bytes": n, "ts_wall_ns": (min, max),
    "vistos": total_no_periodo}}`. Pura sobre o reader.
    """
    saida: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"linhas": [], "bytes": 0, "ts_wall_ns": None, "vistos": 0}
    )
    for record in reader.iter_records(incluir_meta=False):
        if desde_ns is not None and record.ts_wall_ns < desde_ns:
            continue
        if ate_ns is not None and record.ts_wall_ns > ate_ns:
            continue
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


def arquivos_do_recorte(
    recorte: dict[str, dict[str, Any]],
    *,
    origem: dict[str, Any],
) -> list[tuple[str, str]]:
    """`(nome_do_arquivo, texto)` de cada tipo e do MANIFESTO. Pura: não escreve.

    Quem escreve é `main`, chamando `caminho_de_escrita` para CADA nome, no
    mesmo lugar em que o argumento `--saida` entra. Sanitizar numa função e
    escrever noutra é o que a análise de fluxo do Sonar não segue (S2083,
    lição do #142 e do próprio #149). Os nomes vêm de `NOME_DE_TIPO`, que só
    admite `[a-z0-9_]`, e de constantes.
    """
    arquivos: list[tuple[str, str]] = []
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
        nome = nome_do_arquivo(tipo)
        arquivos.append((nome, "\n".join(bloco["linhas"]) + "\n"))
        inicio, fim = bloco["ts_wall_ns"]
        manifesto["tipos"][tipo] = {
            "arquivo": nome,
            "registros": len(bloco["linhas"]),
            "vistos_no_periodo": bloco["vistos"],
            "bytes": bloco["bytes"],
            "ts_wall_utc": [
                datetime.fromtimestamp(inicio / 1e9, UTC).isoformat(timespec="seconds"),
                datetime.fromtimestamp(fim / 1e9, UTC).isoformat(timespec="seconds"),
            ],
        }
    arquivos.append((MANIFESTO, json.dumps(manifesto, indent=2, ensure_ascii=False) + "\n"))
    return arquivos


def _hora_utc(bruto: str | None) -> datetime | None:
    """ISO 8601 → UTC. Offset explícito é CONVERTIDO, não relabelado.

    `replace(tzinfo=UTC)` sobre `2026-09-09T00:00-03:00` dava 00:00 UTC em
    vez de 03:00 UTC, e o reader escolhia os arquivos-hora errados (Codex,
    #149). Sem offset, a hora é lida como UTC — é o que o runbook manda.
    """
    if not bruto:
        return None
    lido = datetime.fromisoformat(bruto.strip().replace("Z", "+00:00"))
    return (lido if lido.tzinfo else lido.replace(tzinfo=UTC)).astimezone(UTC)


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
        # Sonda: a pasta de saída é validada ANTES de ler 73 GB.
        caminho_de_escrita(f"{args.saida}/{MANIFESTO}")
        desde, ate = _hora_utc(args.desde), _hora_utc(args.ate)
        if args.por_tipo <= 0 or args.max_bytes_por_tipo <= 0:
            raise ValueError("--por-tipo e --max-bytes-por-tipo têm de ser positivos")
    except ValueError as erro:
        print(str(erro), file=sys.stderr)
        return 2

    reader = RecordingReader(caminho, desde=desde, ate=ate)
    recorte = recortar(
        reader,
        por_tipo=args.por_tipo,
        max_bytes_por_tipo=args.max_bytes_por_tipo,
        desde_ns=int(desde.timestamp() * 1e9) if desde else None,
        ate_ns=int(ate.timestamp() * 1e9) if ate else None,
    )
    if not recorte:
        print("nenhum registro classificável no período — nada gravado", file=sys.stderr)
        return 1
    origem = {
        "gravacao": caminho.name,
        "desde": args.desde,
        "ate": args.ate,
        "por_tipo": args.por_tipo,
        "max_bytes_por_tipo": args.max_bytes_por_tipo,
    }
    # Sanitizador e escrita no MESMO lugar, para cada arquivo (S2083).
    for nome, texto in arquivos_do_recorte(recorte, origem=origem):
        destino = caminho_de_escrita(f"{args.saida}/{nome}", extensoes=(".jsonl", ".json"))
        destino.write_text(texto, encoding="utf-8")
    print(f"manifesto gravado em {caminho_de_escrita(f'{args.saida}/{MANIFESTO}')}")
    for tipo in sorted(recorte):
        b = recorte[tipo]
        print(f"  {tipo:<40} {len(b['linhas']):>4} de {b['vistos']:>9} vistos  {b['bytes']:>8} B")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
