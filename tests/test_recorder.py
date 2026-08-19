"""Recorder: envelope, fila assíncrona, rotação e não-bloqueio do hot path."""

from __future__ import annotations

import asyncio
import base64
import gzip
import json

from pulsearb.recorder.writer import JsonlGzipWriter, RecordEnvelope


def _read_all(directory) -> list[dict]:
    lines: list[dict] = []
    for path in sorted(directory.glob("*.jsonl.gz")):
        with gzip.open(path, "rb") as handle:
            lines.extend(json.loads(line) for line in handle if line.strip())
    return lines


def test_envelope_json():
    envelope = RecordEnvelope(1, 2, "rtds", b'{"topic":"x","payload":{"a":1}}')
    line = json.loads(envelope.to_line())
    assert line["ts_mono_ns"] == 1
    assert line["ts_wall_ns"] == 2
    assert line["fonte"] == "rtds"
    assert line["payload"] == {"topic": "x", "payload": {"a": 1}}


def test_envelope_nao_json_vira_base64():
    """PONG e afins não são JSON: preservados em base64, nunca descartados."""
    envelope = RecordEnvelope(1, 2, "poly_ws", b"PONG")
    line = json.loads(envelope.to_line())
    assert base64.b64decode(line["payload"]["_b64"]) == b"PONG"


async def test_escreve_e_le_de_volta(tmp_path):
    writer = JsonlGzipWriter(output_dir=tmp_path)
    await writer.start()
    for i in range(50):
        writer.submit(RecordEnvelope(i, i, "rtds", b'{"n":%d}' % i))
    await asyncio.sleep(0.1)
    await writer.stop()

    lines = _read_all(tmp_path)
    assert len(lines) == 50
    assert writer.written == 50
    assert writer.dropped == 0
    assert [line["payload"]["n"] for line in lines] == list(range(50))


async def test_rotacao_por_janela(tmp_path):
    """Cada janela de rotação abre um arquivo novo."""
    fake_now = [1786891500.0]
    writer = JsonlGzipWriter(
        output_dir=tmp_path, rotate_seconds=3600, clock=lambda: fake_now[0]
    )
    await writer.start()
    writer.submit(RecordEnvelope(1, 1, "rtds", b'{"hora":1}'))
    await asyncio.sleep(0.05)
    fake_now[0] += 3600  # próxima hora
    writer.submit(RecordEnvelope(2, 2, "rtds", b'{"hora":2}'))
    await asyncio.sleep(0.05)
    await writer.stop()

    arquivos = sorted(tmp_path.glob("*.jsonl.gz"))
    assert len(arquivos) == 2
    assert len(_read_all(tmp_path)) == 2


async def test_fila_cheia_descarta_e_conta_sem_bloquear(tmp_path):
    """Disco lento não pode travar o feed: descarta o excesso e CONTA."""
    writer = JsonlGzipWriter(output_dir=tmp_path, queue_max=4)
    # Sem start(): ninguém consome, a fila enche na quinta submissão.
    for i in range(10):
        writer.submit(RecordEnvelope(i, i, "rtds", b"{}"))
    assert writer.dropped == 6
    assert writer.queue.qsize() == 4


async def test_submit_nunca_levanta(tmp_path):
    writer = JsonlGzipWriter(output_dir=tmp_path, queue_max=1)
    for _ in range(100):
        writer.submit(RecordEnvelope(0, 0, "x", b"{}"))  # não deve explodir
    assert writer.dropped == 99


async def test_stop_drena_o_que_falta(tmp_path):
    writer = JsonlGzipWriter(output_dir=tmp_path)
    await writer.start()
    for i in range(200):
        writer.submit(RecordEnvelope(i, i, "rtds", b'{"n":%d}' % i))
    await writer.stop()  # sem sleep antes: o stop precisa drenar
    assert len(_read_all(tmp_path)) == 200


async def test_cria_diretorio_inexistente(tmp_path):
    destino = tmp_path / "fundo" / "do" / "poco"
    writer = JsonlGzipWriter(output_dir=destino)
    await writer.start()
    writer.submit(RecordEnvelope(1, 1, "rtds", b"{}"))
    await asyncio.sleep(0.05)
    await writer.stop()
    assert destino.exists()
    assert len(_read_all(destino)) == 1


# ---------------------------------------------------------------- M2.1 BUG 4
# 3 de 26 arquivos da primeira gravação real nasceram inválidos (`gzip -t`
# recusava: "format violated"), sempre nas horas em que houve reinício. Causa:
# o arquivo da hora era reaberto em append, e anexar um membro gzip novo
# depois de um membro TRUNCADO invalida o arquivo inteiro.


def _gzip_valido(path) -> bool:
    """O equivalente em Python ao `gzip -t`: lê até o fim, CRC e trailer."""
    try:
        with gzip.open(path, "rb") as handle:
            while handle.read(1 << 16):
                pass
    except (OSError, EOFError):
        return False
    return True


async def test_todo_arquivo_rotacionado_passa_no_gzip_t(tmp_path):
    """Regressão: cada arquivo fechado por rotação tem trailer e CRC válidos."""
    fake_now = [1786891500.0]
    writer = JsonlGzipWriter(
        output_dir=tmp_path, rotate_seconds=3600, clock=lambda: fake_now[0]
    )
    await writer.start()
    for hora in range(5):
        for i in range(300):
            writer.submit(RecordEnvelope(i, i, "rtds", b'{"hora":%d}' % hora))
        await asyncio.sleep(0.05)
        fake_now[0] += 3600
    await writer.stop()

    arquivos = sorted(tmp_path.glob("*.jsonl.gz"))
    assert len(arquivos) == 5
    invalidos = [p.name for p in arquivos if not _gzip_valido(p)]
    assert invalidos == []
    assert len(_read_all(tmp_path)) == 1500


async def test_arquivo_truncado_nao_e_reaberto_em_append(tmp_path):
    """Reinício na mesma hora: abre arquivo NOVO, não anexa ao quebrado.

    Simula o que o systemd fazia — subir de novo dentro da mesma hora de
    rotação, com o arquivo anterior truncado por ter morrido no meio de uma
    escrita. O dano tem que ficar contido no arquivo velho.
    """
    fake_now = [1786891500.0]
    writer = JsonlGzipWriter(
        output_dir=tmp_path, rotate_seconds=3600, clock=lambda: fake_now[0]
    )
    await writer.start()
    writer.submit(RecordEnvelope(1, 1, "rtds", b'{"antes":1}'))
    await asyncio.sleep(0.05)
    await writer.stop()

    # morte súbita: o arquivo fica com a cauda cortada
    caminho = sorted(tmp_path.glob("*.jsonl.gz"))[0]
    bruto = caminho.read_bytes()
    caminho.write_bytes(bruto[: len(bruto) - 4])
    assert not _gzip_valido(caminho)

    # reinício DENTRO da mesma hora
    writer2 = JsonlGzipWriter(
        output_dir=tmp_path, rotate_seconds=3600, clock=lambda: fake_now[0]
    )
    await writer2.start()
    writer2.submit(RecordEnvelope(2, 2, "rtds", b'{"depois":1}'))
    await asyncio.sleep(0.05)
    await writer2.stop()

    arquivos = sorted(tmp_path.glob("*.jsonl.gz"))
    assert len(arquivos) == 2, "o reinício tem que abrir um arquivo novo"
    novos = [p for p in arquivos if p != caminho]
    assert len(novos) == 1
    novo = novos[0]
    assert _gzip_valido(novo), "o arquivo novo não pode herdar o dano do velho"
    with gzip.open(novo, "rb") as handle:
        assert json.loads(handle.readline())["payload"] == {"depois": 1}
