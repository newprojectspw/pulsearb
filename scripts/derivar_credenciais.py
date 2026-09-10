"""Passo 6 do RUNBOOK §8.1 — derivar as credenciais L2 a partir da chave L1.

    PULSEARB_CHAVE_PRIVADA=0x… .venv/bin/python scripts/derivar_credenciais.py

## O que ele faz, e o que NÃO faz

Assina o typed data do L1 com a chave privada, pede a credencial ao CLOB
(`POST /auth/api-key`, com queda para `GET /auth/derive-api-key` no 400) e
**escreve um arquivo de ambiente `0600`** com as quatro variáveis que o
`CredenciaisL2.do_ambiente()` lê.

**Não imprime o segredo.** Nem em sucesso, nem em erro, nem em traceback. O
segredo vai do fio direto para o arquivo, e o que sai na tela é o endereço, o
nome do arquivo e as chaves que foram gravadas. Um passo que imprimisse a
credencial poria dinheiro real no histórico do terminal, no log do SSH e na
rolagem de quem estivesse olhando.

**Não autoriza LIVE.** A trava tripla do 3.4 continua sendo a única porta.
Ter com que assinar não é ter permissão para enviar.

## Por que ele é script e não parte do bot

O bot NUNCA deriva credencial: ele lê as quatro variáveis prontas. Derivar é
ato de operador, feito uma vez por carteira, com a chave privada em mãos — e
misturar isso ao caminho quente daria à chave privada um motivo para estar
carregada num processo que roda 24 h sozinho.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import httpx

from pulsearb.execution.auth import ErroDeDerivacao, derivar_credenciais
from pulsearb.execution.ordem import AssinadorLocal
from pulsearb.settings import Settings

#: Onde o arquivo de ambiente é escrito. Relativo ao diretório corrente, para o
#: operador ver onde ficou sem adivinhar caminho absoluto.
DESTINO_PADRAO = ".env.credenciais"


def _fazer_pedido(http: httpx.AsyncClient, base: str):
    """`(metodo, caminho, cabecalhos) -> (status, json)`, no formato que
    `derivar_credenciais` espera. Mesma forma do `Transporte` do cliente."""
    base = base.rstrip("/")

    async def pedir(
        metodo: str, caminho: str, cabecalhos: dict[str, str]
    ) -> tuple[int, Any]:
        resposta = await http.request(metodo, base + caminho, headers=cabecalhos)
        try:
            return resposta.status_code, resposta.json()
        except ValueError:
            return resposta.status_code, None

    return pedir


def _gravar(destino: Path, creds: Any) -> None:
    """Escreve o arquivo `0600`. O `umask` vem ANTES da criação: corrigir a
    permissão depois deixa uma janela em que o segredo está legível."""
    anterior = os.umask(0o077)
    try:
        destino.write_text(
            "\n".join(
                [
                    f"PULSEARB_API_KEY={creds.api_key}",
                    f"PULSEARB_API_SEGREDO={creds.segredo}",
                    f"PULSEARB_API_PASSPHRASE={creds.passphrase}",
                    f"PULSEARB_ENDERECO={creds.endereco}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    finally:
        os.umask(anterior)
    destino.chmod(0o600)


async def _principal(destino: Path) -> int:
    try:
        assinador = AssinadorLocal.do_ambiente()
    except Exception as erro:
        # `type(erro).__name__` e não `erro`: a mensagem de uma chave inválida
        # não repete a chave (ver `AssinadorLocal.__init__`), mas outra exceção
        # poderia — e aqui há chave privada no ambiente.
        print(
            f"nao consegui montar o assinador ({type(erro).__name__}). "
            "A chave vem de PULSEARB_CHAVE_PRIVADA, nunca do config.yaml.",
            file=sys.stderr,
        )
        return 2

    settings = Settings.load()
    print(f"endereco: {assinador.endereco}")
    print(f"clob    : {settings.endpoints.clob}")

    async with httpx.AsyncClient(
        headers={"User-Agent": settings.user_agent}, timeout=20.0
    ) as http:
        try:
            creds = await derivar_credenciais(
                assinador, pedir=_fazer_pedido(http, settings.endpoints.clob)
            )
        except ErroDeDerivacao as erro:
            print(f"\nfalhou: {erro}", file=sys.stderr)
            return 1

    _gravar(destino, creds)
    print(f"\ncredenciais gravadas em {destino} (0600)")
    print("variaveis: PULSEARB_API_KEY, PULSEARB_API_SEGREDO, "
          "PULSEARB_API_PASSPHRASE, PULSEARB_ENDERECO")
    print("\nO SEGREDO NAO FOI IMPRESSO. Ele esta so no arquivo.")
    print(
        "Isto NAO autoriza LIVE: a trava tripla do 3.4 continua sendo a unica "
        "porta (risk/autorizacao.py)."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    destino = Path(args[0]) if args else Path(DESTINO_PADRAO)
    if destino.exists():
        print(
            f"{destino} ja existe. Nao vou sobrescrever credencial existente — "
            "apague ou passe outro caminho.",
            file=sys.stderr,
        )
        return 2
    return asyncio.run(_principal(destino))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
