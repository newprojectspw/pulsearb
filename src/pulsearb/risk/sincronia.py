"""O relógio da máquina está sincronizado? (item 5.4 do ESTADO_PARA_LIVE)

Mora em `risk/` e não em `live/` por duas razões: não depende de feed nenhum
— é pergunta sobre a MÁQUINA —, e o seu único consumidor é a autorização para
LIVE, aqui ao lado. Tentar deixá-la em `live/` criou import circular
(`risk` → `live` → `execution` → `risk`), que foi o aviso de que estava no
pacote errado.

Esta é a metade que o `live/relogio.py` **não** consegue fechar, e o motivo
está escrito lá: o sensor de anomalia mede `latencia + offset` numa subtração
só, e as duas parcelas se cancelam — relógio 400 ms atrasado com 400 ms de
latência mede zero e passa no portão. Medição de uma via não vira medição de
duas vias por esforço de software.

O que fecha é sincronia verificada por quem já faz medição de duas vias: o
daemon de NTP da própria máquina. Este módulo apenas **pergunta a ele**, e a
pergunta é deliberadamente simples: "você está sincronizado agora?".

## Fail-closed, e por quê aqui em especial

`sincronizado` é `bool | None`, e `None` significa **não consegui determinar** —
daemon ausente, comando inexistente, saída em formato desconhecido, timeout.
Quem decide trata `None` como "não sincronizado", nunca como "provavelmente
está bem". Um relógio não verificado tem exatamente o mesmo efeito no
`seconds_left` que um relógio errado; a diferença é só a nossa ignorância, e
ignorância não é motivo para arriscar dinheiro.

## Por que subprocess, e as três defesas

Não há API portátil em Python para o estado do NTP: os daemons publicam por
ferramenta de linha de comando. Rodar subprocesso num caminho de decisão é
coisa que dá errado, então:

1. **Timeout curto e obrigatório** em toda chamada. Um `chronyc` pendurado
   travaria o processo que decide.
2. **Nunca levanta.** Qualquer exceção vira `None` com o motivo registrado —
   a falha da sonda não pode ser mais grave que o que ela investiga.
3. **Não roda no caminho quente.** É verificação de SUBIDA, respondida uma vez
   e guardada. O sensor por tick é o `live/relogio.py`; este aqui responde uma
   pergunta de deploy.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

#: Nenhuma sonda pode segurar a subida por mais que isto.
TIMEOUT_S = 3.0


@dataclass(frozen=True, slots=True)
class Sincronia:
    """A resposta do daemon, com a procedência junto.

    `sincronizado is None` = não determinado. Ver o módulo: quem decide trata
    isso como não sincronizado.
    """

    sincronizado: bool | None
    fonte: str
    detalhe: str

    @property
    def verificada(self) -> bool:
        """Só `True` fecha o item 5.4. `None` e `False` não passam."""
        return self.sincronizado is True

    def como_dict(self) -> dict[str, Any]:
        return {
            "sincronizado": self.sincronizado,
            "fonte": self.fonte,
            "detalhe": self.detalhe,
        }


#: Como cada daemon responde "estou sincronizado". Cada entrada é
#: (nome, argv, função que lê a saída). A ordem é a de preferência: systemd
#: primeiro porque é o que a VPS roda, chrony depois, macOS por último.
def _le_timedatectl(saida: str) -> bool | None:
    """`timedatectl show -p NTPSynchronized` devolve `NTPSynchronized=yes|no`."""
    for linha in saida.splitlines():
        if "=" in linha:
            chave, _, valor = linha.partition("=")
            if chave.strip() == "NTPSynchronized":
                return valor.strip().lower() in {"yes", "true", "1"}
    return None


def _le_chronyc(saida: str) -> bool | None:
    """`chronyc tracking` traz `Leap status : Normal` quando sincronizado.

    `Not synchronised` aparece no lugar quando o daemon está rodando mas ainda
    não travou numa fonte — que é diferente de não haver daemon, e igualmente
    motivo para não operar.
    """
    for linha in saida.splitlines():
        if linha.lower().startswith("leap status"):
            _, _, valor = linha.partition(":")
            return valor.strip().lower() == "normal"
    if "not synchronised" in saida.lower():
        return False
    return None


def _le_systemsetup(saida: str) -> bool | None:
    """macOS: `systemsetup -getusingnetworktime` → `Network Time: On|Off`.

    Responde se o serviço está LIGADO, não se ele já travou numa fonte — é
    mais fraco que as outras duas sondas, e por isso é a última. Ainda assim
    separa "há sincronia automática" de "o relógio é o que alguém digitou".
    """
    baixa = saida.lower()
    if "network time: on" in baixa:
        return True
    if "network time: off" in baixa:
        return False
    return None


SONDAS: tuple[tuple[str, tuple[str, ...], Callable[[str], bool | None]], ...] = (
    ("timedatectl", ("timedatectl", "show", "-p", "NTPSynchronized"), _le_timedatectl),
    ("chronyc", ("chronyc", "tracking"), _le_chronyc),
    ("systemsetup", ("systemsetup", "-getusingnetworktime"), _le_systemsetup),
)


def _rodar(argv: Sequence[str]) -> str | None:
    """A saída do comando, ou `None` se ele não existe, falhou ou demorou."""
    if shutil.which(argv[0]) is None:
        return None
    try:
        concluido = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if concluido.returncode != 0:
        return None
    return concluido.stdout


def estado_da_sincronia(
    rodar: Callable[[Sequence[str]], str | None] = _rodar,
) -> Sincronia:
    """Pergunta ao primeiro daemon que souber responder.

    "Souber responder" é estrito: o comando existe, sai com código 0, e a
    saída contém o campo esperado. Uma sonda que roda e devolve formato
    desconhecido NÃO conta como resposta — segue para a próxima, e se nenhuma
    responder o resultado é `None`, que é recusa.

    `rodar` é injetável para os testes: subprocesso de verdade num teste
    tornaria o resultado dependente da máquina que roda a suíte.
    """
    tentadas: list[str] = []
    for nome, argv, ler in SONDAS:
        saida = rodar(argv)
        if saida is None:
            tentadas.append(f"{nome}:ausente")
            continue
        resposta = ler(saida)
        if resposta is None:
            tentadas.append(f"{nome}:formato_desconhecido")
            continue
        return Sincronia(
            sincronizado=resposta,
            fonte=nome,
            detalhe=(
                f"{nome} respondeu "
                f"{'sincronizado' if resposta else 'NAO sincronizado'}"
            ),
        )
    return Sincronia(
        sincronizado=None,
        fonte="nenhuma",
        detalhe=(
            "nenhum daemon de tempo soube responder ("
            + ", ".join(tentadas or ["nenhuma sonda tentada"])
            + "). Instale e habilite NTP: `timedatectl set-ntp true` no "
            "systemd, `chronyd` no chrony, Preferencias > Data e Hora no "
            "macOS. Nao determinado conta como NAO sincronizado."
        ),
    )
