"""3.5 — uma ordem ASSINADA recebendo resposta do CLOB, sem mover dinheiro.

    PULSEARB_SMOKE_ORDEM="EU ACEITO ENVIAR UMA ORDEM REAL" \\
    PULSEARB_CHAVE_PRIVADA=0x… \\
      .venv/bin/python scripts/smoke_ordem_assinada.py

## O que este script prova, e por que ele existe

O item 3.5 está 🟡 desde sempre esperando *"uma ordem assinada recebendo
resposta"*. Todo o caminho — auth L2, struct EIP-712, construtor, cliente — foi
verificado contra o SDK e coberto por testes com dublês. Nada disso prova que o
**servidor** aceita o que produzimos.

Uma resposta do CLOB a uma ordem assinada separa três coisas que os dublês não
conseguem separar:

| resposta | o que ela prova |
|---|---|
| `401` | a assinatura L2 ou a credencial estão erradas |
| `4xx` de negócio | assinatura ACEITA, corpo bem formado, recusa por saldo |
| `200` | entrou no livro — e aqui isso seria um DEFEITO deste script |

O desfecho esperado é o do meio.

## AS TRÊS TRAVAS, e por que cada uma existe

Este script manda algo real para um exchange real. As travas não são
cerimônia — cada uma fecha um jeito diferente de isto custar dinheiro.

**1. Confirmação explícita.** `PULSEARB_SMOKE_ORDEM` com a frase exata. É a
mesma forma da trava tripla do 3.4: rodar por engano, ou por um script que
chame este, não pode ser possível.

**2. Recusa se a carteira tiver SALDO.** Consulta o saldo antes e aborta se for
maior que zero. Sem saldo a ordem não tem como executar, aconteça o que
acontecer com as outras travas. Com saldo, ela poderia — e aí este script
deixaria de ser um teste.

**3. FOK a preço que NÃO cruza.** `FOK` é tudo-ou-nada: não repousa no livro,
nunca. E o preço sai bem abaixo do melhor bid, então não há nada para cruzar.
As duas juntas significam que, mesmo que a trava 2 falhasse, a ordem morreria
no mesmo instante em vez de virar posição.

## O que ele NÃO faz

Não autoriza LIVE, não liga o bot, não deixa nada repousando. A trava tripla do
3.4 continua sendo a única porta para o modo LIVE, e este script não a toca —
ele fala com o CLOB diretamente, uma vez, e termina.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

import httpx

from pulsearb.execution.auth import CredenciaisL2, assinar_l2
from pulsearb.execution.cliente import (
    MOTIVOS_DE_RECUSA,
    ClienteDeOrdens,
    EstadoDoEnvio,
    fazer_transporte,
)
from pulsearb.execution.ordem import AssinadorLocal, ConstrutorDeOrdemLocal
from pulsearb.markets.discovery import MarketDiscovery
from pulsearb.markets.http import fazer_http_get_json
from pulsearb.risk import OrdemPretendida
from pulsearb.settings import Settings

#: A frase exata. Igual à do 3.4: aproximada não serve.
ENV_DA_CONFIRMACAO = "PULSEARB_SMOKE_ORDEM"
FRASE_EXATA = "EU ACEITO ENVIAR UMA ORDEM REAL"

#: `[VERIFICADO]` SDK 0.6.0 `_internal/actions/account.py::build_balance_allowance_request`
#: — `GET /balance-allowance` com `asset_type` e `signature_type` OBRIGATÓRIOS.
#: A assinatura L2 cobre o path PELADO; a query vai fora dela (§4.5).
CAMINHO_SALDO = "/balance-allowance"

#: `[VERIFICADO]` `models/clob/account.py::BalanceAllowance` — `balance` vem em
#: UNIDADES-BASE (inteiro), não em USDC decimal. Para a trava deste script isso
#: não muda nada (zero é zero nas duas escalas), mas tratar o número como USDC
#: em qualquer outro lugar erraria por 10^6.
ASSINATURA_EOA_NO_SALDO = 0

#: Quão longe do topo a ordem sai. 1 centavo é o piso do tick; usamos um preço
#: absoluto baixo para não depender de o livro estar de um jeito específico.
PRECO_QUE_NAO_CRUZA = 0.01


def _confirmado() -> bool:
    return os.environ.get(ENV_DA_CONFIRMACAO, "") == FRASE_EXATA


async def _saldo_de_colateral(
    http: httpx.AsyncClient, base: str, credenciais: CredenciaisL2
) -> float | None:
    """O saldo de colateral da conta, ou `None` se não deu para ler.

    `None` NÃO é zero, e o chamador trata os dois de forma oposta: zero libera,
    não-sei aborta. Um saldo que não foi lido não pode autorizar o envio — é a
    mesma falha-fechada do `listar_ordens_abertas`.
    """
    caminho = (
        f"{CAMINHO_SALDO}?asset_type=COLLATERAL"
        f"&signature_type={ASSINATURA_EOA_NO_SALDO}"
    )
    # O corpo assinado e descartado: no GET ele e `b""`, e o httpx nao manda
    # corpo em GET. O que importa da assinatura sao os cabecalhos.
    cabecalhos, _ = assinar_l2(
        credenciais, metodo="GET", caminho=CAMINHO_SALDO, corpo=None
    )
    # `request` e nao `get`: o `get` do httpx NAO aceita `content=`, e a
    # primeira versao deste script passava o corpo assinado para ele. O
    # `TypeError` caia no `except` largo abaixo e virava "nao consegui ler o
    # saldo" — a leitura nunca tocou a rede. Ver o commit que conserta isto.
    #
    # O corpo vai vazio de proposito: GET nao tem corpo, e `assinar_l2` ja
    # devolve `b""` para corpo None. Mandar `b""` ou nao mandar nada da no
    # mesmo no fio; nao mandar e o que o httpx espera.
    try:
        resposta = await http.request(
            "GET", base.rstrip("/") + caminho, headers=cabecalhos
        )
    except httpx.HTTPError:
        # SO falha de REDE vira "nao sei". Um erro de PROGRAMACAO (TypeError,
        # AttributeError) tem de subir: engoli-lo aqui faz o script relatar
        # "nao consegui ler o saldo" quando a verdade e "o codigo esta
        # quebrado" — duas causas opostas com a mesma mensagem, e a segunda
        # fica invisivel ate alguem depurar na mao. Foi exatamente o que
        # aconteceu na primeira execucao real.
        return None
    if resposta.status_code != 200:
        return None
    try:
        dado = resposta.json()
    except ValueError:
        return None
    bruto = dado.get("balance") if isinstance(dado, dict) else None
    try:
        return float(bruto)
    except (TypeError, ValueError):
        return None


async def _um_token_vivo(http: httpx.AsyncClient, settings: Settings) -> Any:
    """Um token de mercado aberto, para a ordem ter destino real."""
    discovery = MarketDiscovery(
        http_get_json=fazer_http_get_json(
            http, bases=(settings.endpoints.gamma, settings.endpoints.clob)
        ),
        gamma_url=settings.endpoints.gamma,
        clob_url=settings.endpoints.clob,
        assets=settings.assets,
        probe_durations_seconds=settings.probe_durations_seconds,
    )
    for mercado in await discovery.discover():
        token = mercado.token_id_by_outcome.get("Up")
        if token and mercado.operable:
            return mercado.slug, token, mercado.tick_size
    return None, None, None


async def _principal() -> int:
    settings = Settings.load()
    assinador = AssinadorLocal.do_ambiente()
    credenciais = CredenciaisL2.do_ambiente()

    print(f"endereco: {assinador.endereco}")
    print(f"clob    : {settings.endpoints.clob}\n")

    async with httpx.AsyncClient(
        headers={"User-Agent": settings.user_agent}, timeout=20.0
    ) as http:
        # TRAVA 2 — saldo. Antes de qualquer outra coisa.
        saldo = await _saldo_de_colateral(http, settings.endpoints.clob, credenciais)
        if saldo is None:
            print(
                "ABORTADO: nao consegui LER o saldo da carteira.\n"
                "Nao sei nao autoriza enviar — se a leitura falha, o estado e "
                "desconhecido, e ordem com saldo desconhecido pode executar.",
                file=sys.stderr,
            )
            return 2
        print(f"saldo de colateral (unidades-base): {saldo}")
        if saldo > 0:
            print(
                f"\nABORTADO: a carteira tem saldo ({saldo}).\n"
                "Este script so roda em carteira VAZIA — com saldo, a ordem "
                "poderia executar, e ai ele deixa de ser um teste.",
                file=sys.stderr,
            )
            return 2

        slug, token, tick = await _um_token_vivo(http, settings)
        if not token:
            print("ABORTADO: nenhum mercado operavel agora.", file=sys.stderr)
            return 2
        print(f"mercado : {slug}\ntick    : {tick}")

        # TRAVA 3 — FOK (default do cliente) a preco que nao cruza.
        cliente = ClienteDeOrdens(
            credenciais,
            ConstrutorDeOrdemLocal(
                assinador, credenciais, tick_size=str(tick)
            ),
            fazer_transporte(http, base_do_clob=settings.endpoints.clob),
        )
        ordem = OrdemPretendida(
            slug=slug,
            token_id=token,
            lado_up=True,
            shares=settings.risk.stake_max_por_trade_usdc,
            preco_limite=PRECO_QUE_NAO_CRUZA,
        )
        print(
            f"\nenviando FOK, {ordem.shares} shares @ {PRECO_QUE_NAO_CRUZA} "
            "(preco que NAO cruza; FOK nunca repousa)\n"
        )
        resultado = await cliente.enviar(ordem, janela=f"smoke-{slug}")

    print(f"estado : {resultado.estado}")
    print(f"motivo : {resultado.motivo}")
    print(f"detalhe: {resultado.detalhe}")

    if resultado.estado is EstadoDoEnvio.RECUSADA:
        if resultado.motivo == MOTIVOS_DE_RECUSA.AUTH_RECUSADA:
            # A distincao que a primeira versao errou: ela imprimia "desfecho
            # esperado" e logo abaixo dizia "se o motivo nao for
            # auth_recusada" — texto condicional impresso incondicionalmente.
            # Recusa por AUTH nao fecha o 3.5: ela nao prova que o corpo
            # estava bem formado, porque o servidor parou antes de olhar.
            print(
                "\nNAO fecha o 3.5. A recusa foi de AUTENTICACAO/PERMISSAO, "
                "entao o servidor nao chegou a avaliar o corpo da ordem.\n\n"
                "Se a leitura de saldo acima funcionou, a assinatura L2 esta "
                "CERTA — ela usa o mesmo mecanismo. Um 403 aqui aponta para "
                "permissao da CONTA para a acao (sem colateral, sem allowance, "
                "ou conta nao habilitada), nao para a assinatura.\n"
                "O corpo da resposta esta em `detalhe` acima.",
                file=sys.stderr,
            )
            return 1
        print(
            "\nE O DESFECHO ESPERADO. Recusa e RESPOSTA: o servidor leu a "
            "ordem assinada e disse nao, por motivo de NEGOCIO. Isso prova que "
            "a assinatura L2 foi aceita e o corpo estava bem formado — que e "
            "exatamente o que o 3.5 pedia."
        )
        return 0
    if resultado.estado is EstadoDoEnvio.ACEITA:
        print(
            "\nATENCAO: a ordem foi ACEITA. Com carteira vazia e FOK a preco "
            "que nao cruza isso nao deveria acontecer — confira o estado da "
            "conta ANTES de rodar qualquer outra coisa.",
            file=sys.stderr,
        )
        return 1
    print(
        "\nINCERTA: a resposta nao chegou. A ordem PODE ter entrado. Reconcilie "
        "com `listar_ordens_abertas` antes de repetir — nao reenvie.",
        file=sys.stderr,
    )
    return 1


def main() -> int:
    # TRAVA 1 — confirmacao explicita, com a frase exata.
    if not _confirmado():
        print(
            f"Este script ENVIA UMA ORDEM REAL, assinada, para o CLOB.\n\n"
            f"Para rodar, defina {ENV_DA_CONFIRMACAO} com a frase exata:\n"
            f'    {ENV_DA_CONFIRMACAO}="{FRASE_EXATA}"\n\n'
            "Ele so prossegue com a carteira VAZIA, manda FOK (que nunca "
            "repousa) a um preco que nao cruza, e espera RECUSA como sucesso.",
            file=sys.stderr,
        )
        return 2
    return asyncio.run(_principal())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
