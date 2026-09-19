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

PRECISA_DO_GZIP = pytest.mark.skipif(
    shutil.which("gzip") is None, reason="precisa do gzip"
)

# Ignora o host ($1) e roda o comando localmente, com o stdin do chamador.
DUBLE_DE_SSH = '#!/bin/sh\nshift\nexec /bin/bash -c "$*"\n'

# O que o ssh faz quando o host nao resolve, a chave e recusada ou a rede cai.
DUBLE_DE_SSH_QUE_FALHA = (
    '#!/bin/sh\n'
    'echo "ssh: Could not resolve hostname seu_ip" >&2\n'
    'exit 255\n'
)


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


@PRECISA_DO_GZIP
def test_apaga_so_a_copia_integra_e_preserva_as_outras_duas(cenario) -> None:
    saida = _rodar(cenario, "--apagar")

    assert saida.returncode == 0, saida.stderr
    assert _nomes_no_remoto(cenario) == {
        "pulsearb-20260918-1900.jsonl.gz",  # gzip quebrado aqui
        "pulsearb-20260918-2000.jsonl.gz",  # ainda crescendo la
    }


@PRECISA_DO_GZIP
def test_sem_apagar_e_o_default_e_nada_e_removido(cenario) -> None:
    antes = _nomes_no_remoto(cenario)

    saida = _rodar(cenario)

    assert saida.returncode == 0, saida.stderr
    assert "nada foi apagado" in saida.stdout
    assert _nomes_no_remoto(cenario) == antes


@PRECISA_DO_GZIP
def test_arquivo_que_nao_chegou_aqui_nunca_e_apagado(cenario) -> None:
    (cenario["local"] / "pulsearb-20260918-1800.jsonl.gz").unlink()

    saida = _rodar(cenario, "--apagar")

    assert saida.returncode == 0, saida.stderr
    assert "pulsearb-20260918-1800.jsonl.gz" in _nomes_no_remoto(cenario)


@PRECISA_DO_GZIP
def test_ssh_que_falha_recusa_em_vez_de_dizer_que_nao_ha_gravacao(cenario) -> None:
    """A falha que o log escondeu em 2026-09-18.

    Com o `|| true` que havia aqui, `ssh` caindo dava saida VAZIA e status
    ZERO — indistinguivel de "a VPS nao tem gravacao nenhuma". A rotina
    imprimia sucesso sem ter falado com a VPS, e o marcador que o RUNBOOK §6
    manda conferir (`=== fim ===`) saia no log de um run que falhou inteiro.
    """
    (cenario["binarios"] / "ssh").write_text(DUBLE_DE_SSH_QUE_FALHA)
    (cenario["binarios"] / "ssh").chmod(0o755)
    antes = _nomes_no_remoto(cenario)

    saida = _rodar(cenario, "--apagar")

    assert saida.returncode == 2, saida.stdout
    assert "consegui falar com" in saida.stderr
    assert "NADA foi apagado" in saida.stderr
    assert _nomes_no_remoto(cenario) == antes
