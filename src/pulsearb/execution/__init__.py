"""Execução: quem transforma uma decisão em ordem — ou em registro.

Dois executores, uma interface. É de propósito: a única forma de o SHADOW
provar alguma coisa sobre o LIVE é os dois passarem pelo MESMO caminho de
decisão e divergirem só no último passo.
"""

from pulsearb.execution.executor import (
    Executor,
    ExecutorSombra,
    IntencaoRegistrada,
    escolher_executor,
)

__all__ = [
    "Executor",
    "ExecutorSombra",
    "IntencaoRegistrada",
    "escolher_executor",
]
