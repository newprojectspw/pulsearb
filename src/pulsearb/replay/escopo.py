"""O escopo REAL do recorder, lido do `discovery_snapshot`.

O recorder descobre mais janelas do que assina quando `max_tokens_assinados`
corta a descoberta: o snapshot grava TODAS as janelas descobertas (o corte não
pode ser silencioso), mas só as do escopo têm livro gravado. Um consumidor que
lesse todas as janelas do snapshot trataria como gravada uma janela que nunca
foi assinada (revisão do PR #197, P1).

Função única para o backtest e o replay ao vivo (mesmo caminho): se cada um
filtrasse do seu jeito, uma divergência entre os dois pareceria diferença de
mercado quando é diferença de código.
"""

from __future__ import annotations

from typing import Any

#: chave, dentro do bloco `escopo` do snapshot, com a lista EXPLÍCITA dos slugs
#: assinados naquele ciclo. As contagens (`janelas_no_escopo`, inteiro) já
#: existiam; a lista é o que permite filtrar.
CHAVE_SLUGS_NO_ESCOPO = "slugs_no_escopo"


def slugs_no_escopo(payload: Any) -> frozenset[str] | None:
    """Os slugs que o recorder assinou neste ciclo, ou `None` se o snapshot não
    traz a lista (gravação anterior a ela — escopo desconhecido).

    `None` NÃO quer dizer "tudo está no escopo": quem consome decide, e deve
    relatar que o escopo daquela gravação não é verificável. Lista malformada
    também é `None` — nunca vira "tudo" nem "nada" em silêncio.
    """
    if not isinstance(payload, dict):
        return None
    escopo = payload.get("escopo")
    if not isinstance(escopo, dict):
        return None
    lista = escopo.get(CHAVE_SLUGS_NO_ESCOPO)
    if not isinstance(lista, list) or not all(isinstance(s, str) for s in lista):
        return None
    return frozenset(lista)
