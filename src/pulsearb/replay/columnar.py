"""Pós-processamento da gravação para formato colunar (M2.2 A.6).

    python -m pulsearb.replay.columnar data/recordings --out data/parquet

Por que existe: o JSONL gzip é o formato de CAPTURA — bruto, append-only,
tolerante a morte súbita, e por isso permanece. Mas ele é péssimo para
ANÁLISE: cada passada do backtest sobre 72h descomprime ~34 GB e reparseia
JSON linha a linha, e o backtest faz várias passadas (uma por cenário de
latência, uma por threshold). Ler colunas de parquet troca esse custo por
leitura seletiva: quem só quer `best_ask` não paga pelos 40 níveis do livro.

Regra que este módulo respeita: **o parquet é derivado, nunca a fonte.** Ele
pode ser apagado e regerado a partir do JSONL a qualquer momento. Se os dois
discordarem, o JSONL está certo.

Particionamento hive por FONTE e por DIA (UTC), que é como as consultas caem
na prática: "o livro do dia 18", "os ticks de TWAP da semana". `fonte` e `dia`
existem só como diretório, NÃO como coluna: repetir a chave de partição dentro
do arquivo faz o `pyarrow.parquet.read_table` do diretório-raiz falhar com
`Unable to merge: Field fonte has incompatible types`, porque a partição é
lida como dicionário e a coluna como string. O payload aninhado vira a coluna
`payload_json`, de modo que nada se perde na conversão.

`pyarrow` é um EXTRA opcional (`pip install -e '.[analise]'`), não uma
dependência de runtime: a VPS que grava nunca converte nada, e não faz sentido
ela carregar ~40 MB de biblioteca de análise.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import orjson

from pulsearb.obs import get_logger
from pulsearb.replay.reader import RecordingReader, ReplayRecord

log = get_logger("pulsearb.replay.columnar")

MENSAGEM_SEM_PYARROW = (
    "o formato colunar precisa do extra de análise:\n"
    "    pip install -e '.[analise]'\n"
    "Ele é opcional de propósito — a VPS que grava não converte nada, e\n"
    "carregar ~40 MB de biblioteca de análise lá não compraria nada."
)

# Linhas acumuladas antes de escrever um lote. O ponto do módulo é justamente
# não materializar a gravação inteira: 50 mil linhas de livro são alguns MB.
LOTE_PADRAO = 50_000


def _pyarrow() -> tuple[Any, Any]:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as erro:  # pragma: no cover - depende do ambiente
        raise RuntimeError(MENSAGEM_SEM_PYARROW) from erro
    return pa, pq


def dia_utc(ts_wall_ns: int) -> str:
    """'20260818'. Dia de PAREDE, que é como as consultas pensam."""
    return datetime.fromtimestamp(ts_wall_ns / 1e9, tz=UTC).strftime("%Y%m%d")


def achatar(record: ReplayRecord) -> Iterator[dict[str, Any]]:
    """Um registro do JSONL vira uma ou mais linhas colunares.

    Um `price_change` com deltas de vários tokens vira várias linhas — é o que
    torna a coluna `asset_id` utilizável como filtro. O payload original
    sempre acompanha em `payload_json`, para que a conversão seja
    informacionalmente completa: nenhuma pergunta que o JSONL responde fica
    sem resposta no parquet.
    """
    payload = record.payload
    eventos = payload if isinstance(payload, list) else [payload]
    for evento in eventos:
        base = {
            "ts_mono_ns": record.ts_mono_ns,
            "ts_wall_ns": record.ts_wall_ns,
            "event_type": None,
            "asset_id": None,
            "market": None,
            "topic": None,
            "price": None,
            "size": None,
            "side": None,
            "best_bid": None,
            "best_ask": None,
            "payload_json": orjson.dumps(evento).decode(),
        }
        if not isinstance(evento, dict):
            yield base
            continue
        base["event_type"] = _texto(evento.get("event_type"))
        base["market"] = _texto(evento.get("market"))
        base["topic"] = _texto(evento.get("topic"))
        base["asset_id"] = _texto(evento.get("asset_id"))
        base["price"] = _numero(evento.get("price"))
        base["size"] = _numero(evento.get("size"))
        base["side"] = _texto(evento.get("side"))
        base["best_bid"] = _numero(evento.get("best_bid"))
        base["best_ask"] = _numero(evento.get("best_ask"))

        # RTDS: o preço mora no payload aninhado.
        interno = evento.get("payload")
        if isinstance(interno, dict):
            base["asset_id"] = base["asset_id"] or _texto(interno.get("symbol"))
            base["price"] = base["price"] if base["price"] is not None else _numero(
                interno.get("value")
            )

        mudancas = evento.get("price_changes")
        if not isinstance(mudancas, list):
            mudancas = evento.get("changes")
        if isinstance(mudancas, list) and mudancas:
            for mudanca in mudancas:
                if not isinstance(mudanca, dict):
                    continue
                linha = dict(base)
                linha["asset_id"] = _texto(mudanca.get("asset_id")) or base["asset_id"]
                linha["price"] = _numero(mudanca.get("price"))
                linha["size"] = _numero(mudanca.get("size"))
                linha["side"] = _texto(mudanca.get("side"))
                linha["best_bid"] = _numero(mudanca.get("best_bid"))
                linha["best_ask"] = _numero(mudanca.get("best_ask"))
                yield linha
            continue
        yield base


def esquema() -> Any:
    pa, _ = _pyarrow()
    return pa.schema(
        [
            ("ts_mono_ns", pa.int64()),
            ("ts_wall_ns", pa.int64()),
            ("event_type", pa.string()),
            ("asset_id", pa.string()),
            ("market", pa.string()),
            ("topic", pa.string()),
            ("price", pa.float64()),
            ("size", pa.float64()),
            ("side", pa.string()),
            ("best_bid", pa.float64()),
            ("best_ask", pa.float64()),
            ("payload_json", pa.string()),
        ]
    )


def converter(
    origem: str | Path,
    destino: str | Path,
    *,
    lote: int = LOTE_PADRAO,
    compressao: str = "zstd",
) -> dict[str, Any]:
    """Converte a gravação para parquet particionado por fonte e dia.

    Em STREAMING: um `ParquetWriter` aberto por partição, alimentado em lotes.
    Materializar a gravação para depois escrever seria repetir o erro de
    memória que o M2.1 corrigiu no backtest.
    """
    pa, pq = _pyarrow()
    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    schema = esquema()

    reader = RecordingReader(origem)
    writers: dict[tuple[str, str], Any] = {}
    buffers: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    linhas_por_particao: dict[str, int] = defaultdict(int)

    def descarregar(chave: tuple[str, str]) -> None:
        linhas = buffers[chave]
        if not linhas:
            return
        writer = writers.get(chave)
        if writer is None:
            fonte, dia = chave
            caminho = destino / f"fonte={fonte}" / f"dia={dia}"
            caminho.mkdir(parents=True, exist_ok=True)
            writer = pq.ParquetWriter(
                caminho / "parte-0000.parquet", schema, compression=compressao
            )
            writers[chave] = writer
        writer.write_table(
            pa.Table.from_pylist(linhas, schema=schema)  # type: ignore[arg-type]
        )
        linhas_por_particao[f"{chave[0]}/{chave[1]}"] += len(linhas)
        buffers[chave] = []

    try:
        for record in reader.iter_records():
            chave = (record.fonte, dia_utc(record.ts_wall_ns))
            for linha in achatar(record):
                buffers[chave].append(linha)
            if len(buffers[chave]) >= lote:
                descarregar(chave)
        for chave in list(buffers):
            descarregar(chave)
    finally:
        for writer in writers.values():
            writer.close()

    return {
        "arquivos_lidos": len(reader.files),
        "registros": reader.total,
        "linhas_corrompidas": reader.corrompidas,
        "particoes": dict(sorted(linhas_por_particao.items())),
        "linhas": sum(linhas_por_particao.values()),
        "destino": str(destino),
        "compressao": compressao,
        "nota": (
            "Derivado. O JSONL continua sendo a fonte: se os dois "
            "discordarem, o JSONL está certo e este diretório pode ser "
            "apagado e regerado."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="PULSEARB — converte a gravação JSONL gzip para parquet"
    )
    parser.add_argument("recordings", help="diretório (ou arquivo) da gravação")
    parser.add_argument("--out", required=True, help="diretório de destino")
    parser.add_argument("--lote", type=int, default=LOTE_PADRAO)
    parser.add_argument(
        "--compressao",
        default="zstd",
        choices=("zstd", "snappy", "gzip", "none"),
    )
    args = parser.parse_args(argv)

    origem = Path(args.recordings).expanduser().resolve(strict=False)
    if not origem.exists():
        print(f"gravação não encontrada: {origem}", file=sys.stderr)
        return 2
    try:
        resumo = converter(
            origem,
            Path(args.out).expanduser().resolve(strict=False),
            lote=max(1, args.lote),
            compressao=args.compressao,
        )
    except RuntimeError as erro:
        print(str(erro), file=sys.stderr)
        return 3
    print(orjson.dumps(resumo, option=orjson.OPT_INDENT_2).decode())
    log.info("conversão colunar concluída", **{"linhas": resumo["linhas"]})
    return 0


def _texto(valor: Any) -> str | None:
    return valor if isinstance(valor, str) else None


def _numero(valor: Any) -> float | None:
    if isinstance(valor, bool) or valor is None:
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    if isinstance(valor, str):
        try:
            return float(valor)
        except ValueError:
            return None
    return None


if __name__ == "__main__":
    raise SystemExit(main())
