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

from typing import Any

import httpx


def fazer_http_get_json(http: httpx.AsyncClient):
    """Adapta um `AsyncClient` ao contrato `HttpGetJson` da descoberta."""

    async def http_get_json(url: str, params: dict[str, Any] | None) -> Any:
        resposta = await http.get(url, params=params)
        if resposta.status_code == 404:
            # Slug candidato sem mercado. Resposta normal da grade, não erro.
            return None
        resposta.raise_for_status()
        return resposta.json()

    return http_get_json
