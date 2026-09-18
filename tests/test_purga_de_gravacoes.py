"""As tres travas do `purge_recordings.sh`, que apaga gravacao.

O script existe desde cedo e nunca teve teste. Ele e o unico lugar do
projeto que APAGA gravacao — e gravacao apagada nao volta, porque a hora de
mercado ja passou. As tres conferencias que ele faz antes de remover (existe
aqui, tamanho bate, `gzip -t` abre) sao testadas uma a uma, cada uma isolada
das outras:

  INTEIRO   copia identica e valida            -> APAGA
  CORROMPIDO  mesmo TAMANHO, gzip quebrado     -> mantem (so a trava do gzip pega)
  CRESCENDO   gzip valido, TAMANHO menor       -> mantem (so a trava do tamanho pega)

O `CRESCENDO` e o arquivo da hora corrente: a VPS ainda escreve nele, entao a
copia daqui e menor. Apagar esse seria perder a hora em curso.

O `ssh` e substituido por um dublê que ignora o host e executa o comando
localmente, entao o "remoto" e um diretorio de verdade e a remocao e real.
"""

from __future__ import annotations

import gzip
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "purge_recordings.sh"

# Ignora o host ($1) e roda o comando localmente, com o stdin do chamador.
DUBLE_DE_SSH = '#!/bin/sh\nshift\nexec /bin/bash -c "$*"\n'


def _gravacao(conteudo: bytes) -> bytes:
    return gzip.compress(conteudo)


@pytest.fixture
def cenario(tmp_path: Path) -> dict[str, Path]:
    remoto = tmp_path / "remoto"
    local = tmp_path / "local"
    remoto.mkdir()
    local.mkdir()

    inteiro = _gravacao(b'{"a":1}\n' * 200)
    (remoto / "pulsearb-20260918-1800.jsonl.gz").write_bytes(inteiro)
    (local / "pulsearb-20260918-1800.jsonl.gz").write_bytes(inteiro)

    # Mesmo tamanho, bytes do meio trocados: o tamanho bate e o gzip reprova.
    corrompido = bytearray(inteiro)
    corrompido[len(corrompido) // 2] ^= 0xFF
    (remoto / "pulsearb-20260918-1900.jsonl.gz").write_bytes(inteiro)
    (local / "pulsearb-20260918-1900.jsonl.gz").write_bytes(bytes(corrompido))

    # A hora corrente: o remoto ainda cresce, a copia daqui e valida e MENOR.
    crescendo_local = _gravacao(b'{"b":2}\n' * 50)
    crescendo_remoto = _gravacao(b'{"b":2}\n' * 400)
    (remoto / "pulsearb-20260918-2000.jsonl.gz").write_bytes(crescendo_remoto)
    (local / "pulsearb-20260918-2000.jsonl.gz").write_bytes(crescendo_local)

    binarios = tmp_path / "bin"
    binarios.mkdir()
    duble = binarios / "ssh"
    duble.write_text(DUBLE_DE_SSH)
    duble.chmod(0o755)

    return {"remoto": remoto, "local": local, "binarios": binarios}


def _rodar(cenario: dict[str, Path], *args: str) -> subprocess.CompletedProcess[str]:
    ambiente = dict(os.environ)
    ambiente["PATH"] = f"{cenario['binarios']}:{ambiente['PATH']}"
    ambiente["PULSEARB_REMOTE_DIR"] = str(cenario["remoto"])
    return subprocess.run(
        ["bash", str(SCRIPT), "irrelevante@host", str(cenario["local"]), *args],
        capture_output=True,
        text=True,
        env=ambiente,
        check=False,
    )


def _nomes_no_remoto(cenario: dict[str, Path]) -> set[str]:
    return {p.name for p in cenario["remoto"].iterdir()}


@pytest.mark.skipif(shutil.which("gzip") is None, reason="precisa do gzip")
def test_apaga_so_a_copia_integra_e_preserva_as_outras_duas(cenario) -> None:
    saida = _rodar(cenario, "--apagar")

    assert saida.returncode == 0, saida.stderr
    assert _nomes_no_remoto(cenario) == {
        "pulsearb-20260918-1900.jsonl.gz",  # gzip quebrado aqui
        "pulsearb-20260918-2000.jsonl.gz",  # ainda crescendo la
    }


@pytest.mark.skipif(shutil.which("gzip") is None, reason="precisa do gzip")
def test_sem_apagar_e_o_default_e_nada_e_removido(cenario) -> None:
    antes = _nomes_no_remoto(cenario)

    saida = _rodar(cenario)

    assert saida.returncode == 0, saida.stderr
    assert "nada foi apagado" in saida.stdout
    assert _nomes_no_remoto(cenario) == antes


@pytest.mark.skipif(shutil.which("gzip") is None, reason="precisa do gzip")
def test_arquivo_que_nao_chegou_aqui_nunca_e_apagado(cenario) -> None:
    (cenario["local"] / "pulsearb-20260918-1800.jsonl.gz").unlink()

    saida = _rodar(cenario, "--apagar")

    assert saida.returncode == 0, saida.stderr
    assert "pulsearb-20260918-1800.jsonl.gz" in _nomes_no_remoto(cenario)
