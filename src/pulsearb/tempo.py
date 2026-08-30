"""Durações e carências. Um lugar só, porque as duas pontas precisam bater.

`parse_duration` e `RESOLUTION_GRACE_SECONDS` nasceram dentro do recorder e
mudaram de casa quando o processo SHADOW passou a precisar dos dois. O motivo
é o mesmo que já moveu `e18_do_evento` e `eventos_do_payload`: se a carência de
resolução do SHADOW fosse diferente da do recorder, ele desassinaria um token
antes (ou depois) de o recorder parar de gravá-lo, e a diferença apareceria
como janela que um viu e o outro não.
"""

from __future__ import annotations

import re

_DURATION_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*$", re.IGNORECASE)
_DURATION_UNITS = {"": 3600.0, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}

#: Quanto tempo depois do fechamento um token continua interessando. A
#: resolução não chega no instante do fechamento, e desassinar cedo demais
#: perderia justamente o evento que diz quem ganhou.
RESOLUTION_GRACE_SECONDS = 600.0


def parse_duration(text: str) -> float:
    """'72h' → 259200.0. Sem sufixo = horas (o uso mais comum aqui)."""
    match = _DURATION_PATTERN.match(text)
    if match is None:
        raise ValueError(f"duração inválida: {text!r} (use 90s, 30m, 72h, 7d)")
    return float(match.group(1)) * _DURATION_UNITS[match.group(2).lower()]
