"""O cliente HTTP que a descoberta usa. Um só, para as duas pontas.

`MarketDiscovery` recebe o cliente injetado (regra offline-first do M1): os
testes passam um fake, produção passa httpx. Esta função é o adaptador de
produção — e mora aqui, e não dentro de cada processo, porque o tratamento de
**404** é semântica, não encanação.

A Gamma responde 404 para slug que não existe, e isso é resposta normal: a
grade de slugs testa candidatos que podem não ter mercado. Se o recorder
tratasse 404 como `None` e o SHADOW o tratasse como erro, um veria a janela e o
outro não — e a divergência apareceria como diferença de mercado entre os dois.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx


class DestinoNaoPermitido(ValueError):
    """Pediram uma URL fora dos endpoints configurados.

    Exceção própria para que o motivo saia nomeado no log em vez de virar um
    `ValueError` genérico no meio de um laço de descoberta.
    """


def fazer_http_get_json(http: httpx.AsyncClient, *, bases: Sequence[str]):
    """Adapta um `AsyncClient` ao contrato `HttpGetJson`, com allowlist.

    ## Por que a allowlist

    As URLs da descoberta são montadas por concatenação, e parte do que entra
    nelas vem do FIO — `conditionId` é campo da resposta da Gamma. O
    `seguro_na_url` já barra caractere que escapa do caminho, mas ele protege
    UM ponto de construção. Esta checagem protege o destino, que é o que
    realmente importa: aconteça o que acontecer na montagem, a requisição só
    sai para Gamma ou CLOB.

    Defesa em profundidade de propósito. Um campo novo interpolado numa URL
    amanhã não passa pelo `seguro_na_url` — mas passa por aqui.

    ## A barra final não é detalhe

    Os prefixos permitidos terminam em `/`. Sem ela,
    `https://gamma-api.polymarket.com.exemplo-malicioso.com/x` casaria com o
    prefixo `https://gamma-api.polymarket.com` e passaria — o truque de
    sufixo de domínio mais comum que existe.
    """
    permitidos = tuple(base.rstrip("/") + "/" for base in bases if base)
    if not permitidos:
        raise ValueError("allowlist vazia: nenhuma requisição poderia sair")

    async def http_get_json(url: str, params: dict[str, Any] | None) -> Any:
        if not url.startswith(permitidos):
            raise DestinoNaoPermitido(
                f"URL fora dos endpoints configurados: {url[:120]!r}"
            )
        resposta = await http.get(url, params=params)
        if resposta.status_code == 404:
            # Slug candidato sem mercado. Resposta normal da grade, não erro.
            return None
        resposta.raise_for_status()
        return resposta.json()

    return http_get_json
