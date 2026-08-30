"""Contenção de caminhos vindos de fora do programa.

Argumento de linha de comando, variável de ambiente, campo de configuração:
todos chegam como texto que alguém — ou algum agente — escreveu, e entregá-los
ao sistema de arquivos do jeito que chegam é travessia de caminho (S2083). O
M2.5 fechou isso no `--json` do backtest; este módulo é aquele mesmo
tratamento, num lugar em que o SHADOW também alcança.

Por que módulo próprio e não `import` do backtest: `pulsearb.backtest.__main__`
puxa o runner, o book e a análise inteira. O processo ao vivo não pode pagar
isso — nem carregar, no processo que fala com a rede, código que só existe para
reprocessar gravação. A regra é pequena e não depende de nada do pacote; o
lugar dela é aqui.

O que NÃO mora aqui é o `caminho_de_leitura` do backtest (a raiz das
gravações), que é frouxo de propósito: a gravação mora fora do diretório de
trabalho e contê-la quebraria o runbook. Juntar as duas regras afrouxaria a
mais estrita.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

#: Variável que amplia a raiz permitida para o arquivo de saída.
ENV_RAIZ_DE_SAIDA = "PULSEARB_BACKTEST_OUTPUT_ROOT"


def raiz_de_saida() -> Path:
    """Onde o relatório PODE ser escrito. Diretório de trabalho, por padrão.

    O caminho do `--json` vem de fora do programa — de uma pessoa com pressa,
    de um script, de um agente. Sufixo e diretório-pai existentes não impedem
    `--json /etc/cron.d/qualquer.json`: para isso é preciso **conter** o
    caminho, não só conferir a forma dele.

    O padrão é o diretório de trabalho porque é onde o runbook manda gravar
    (`--json relatorio.json`, `--json relatorios/2026-08-20-13.json`). Quem
    precisa escrever em outro lugar diz isso de propósito, definindo
    `PULSEARB_BACKTEST_OUTPUT_ROOT` — que é diferente de o programa aceitar
    qualquer caminho em silêncio.
    """
    bruto = os.environ.get(ENV_RAIZ_DE_SAIDA)
    if bruto:
        return Path(bruto).expanduser().resolve(strict=False)
    return Path.cwd().resolve()


#: Forma aceita para o `--json`: caminho RELATIVO, segmentos de letras,
#: dígitos, `-`, `_` e `.`, separados por `/`, terminando em `.json`. Sem raiz
#: absoluta, sem `..`, sem `~`, sem caractere exótico.
#:
#: É uma lista de permissões, e é de propósito. Conferir o caminho DEPOIS de
#: montá-lo ("ele caiu dentro da raiz?") funciona, mas continua entregando a
#: string de fora ao sistema de arquivos; a análise de fluxo do SonarCloud
#: aponta isso e está certa em apontar. Validar ANTES contra um padrão fixo e
#: só então montar o caminho a partir de uma raiz confiável não deixa o valor
#: externo chegar ao disco em forma nenhuma.
PADRAO_SAIDA = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*")


def caminho_de_escrita(bruto: str, *, extensoes: tuple[str, ...] = (".json",)) -> Path:
    """Monta o caminho de SAÍDA a partir da raiz permitida.

    `extensoes` existe porque o mesmo tratamento vale para o diário do SHADOW
    (`--diario`, `.jsonl`), que é escrito no disco vindo da linha de comando
    exatamente como o `--json` do backtest. O default mantém os chamadores
    antigos idênticos.

    O argumento é lido como caminho **relativo à raiz** (`--json
    relatorio.json`, `--json relatorios/2026-08-20-13.json`), nunca como
    caminho absoluto. Para gravar em outro lugar, mude a RAIZ com
    `PULSEARB_BACKTEST_OUTPUT_ROOT` — assim o destino é sempre uma decisão
    explícita de quem roda, e não um efeito colateral do argumento.

    Um relatório de backtest escrito em local inesperado é pior que um erro:
    some sem ninguém notar.
    """
    relativo = bruto.strip().removeprefix("./")
    if not PADRAO_SAIDA.fullmatch(relativo) or not relativo.endswith(extensoes):
        esperadas = " ou ".join(extensoes)
        raise ValueError(
            f"nome de saída inválido: {bruto!r}\n"
            f"esperado: caminho relativo terminando em {esperadas}, com letras, "
            "dígitos, '-', '_' e '.' (ex.: relatorios/2026-08-20-13.json).\n"
            f"para gravar em outra raiz, defina {ENV_RAIZ_DE_SAIDA}."
        )
    raiz = raiz_de_saida()
    caminho = raiz / relativo
    # Cinto e suspensório: o padrão acima já exclui `..` e raiz absoluta, mas
    # a raiz vem de variável de ambiente e pode conter symlink. A contenção
    # depois de resolver custa uma syscall e fecha esse resto.
    #
    # A contenção está escrita na forma canônica que a análise de fluxo do
    # SonarCloud reconhece como sanitização de S2083 (caminho absoluto +
    # `startswith` contra a raiz + separador). `Path.is_relative_to` faz a
    # MESMA conta, mas o motor de taint não o conhece como sanitizador e
    # continuaria marcando o `write_text` lá na frente. O `os.sep` no fim da
    # raiz evita a colisão de prefixo (/raiz versus /raiz2) — e só entra
    # quando a raiz ainda não termina no separador, senão a raiz `/` viraria
    # `//` e rejeitaria todo caminho válido (achado em review).
    raiz_resolvida = raiz.resolve(strict=False)
    resolvido = caminho.resolve(strict=False)
    prefixo = str(raiz_resolvida)
    if not prefixo.endswith(os.sep):
        prefixo += os.sep
    if not str(resolvido).startswith(prefixo):
        raise ValueError(f"saída fora da raiz permitida: {resolvido}")
    if not resolvido.parent.is_dir():
        raise ValueError(f"diretório de saída não existe: {resolvido.parent}")
    if resolvido.is_dir():
        raise ValueError(f"o destino é um diretório: {resolvido}")
    return resolvido


def caminho_de_relatorio_lido(bruto: str) -> Path:
    """Monta o caminho de um relatório de ENTRADA a partir da raiz permitida.

    Espelho do `caminho_de_escrita`, e pelo mesmo motivo: o argumento de
    `--curva-de-variancia` vem de fora do programa, e entregá-lo ao sistema
    de arquivos do jeito que chega é a mesma travessia de caminho que o M2.5
    fechou no `--json`. Ler `/etc/qualquer/coisa.json` não sobrescreve nada,
    mas expõe conteúdo de fora da raiz na mensagem de erro e no relatório
    (o nome do arquivo sai em `origem`).

    **Por que não reusa o `caminho_de_leitura`** (que ficou em
    `pulsearb.backtest.__main__`). Aquele serve ao argumento
    `recordings`, que é uma pasta fora da raiz DE PROPÓSITO — a gravação mora
    em `~/pulsearb-m2`, e contê-la no diretório de trabalho quebraria o
    runbook. Este aqui lê um relatório que o próprio projeto escreveu sob a
    raiz, então a contenção do `--json` se aplica inteira. São duas regras
    diferentes porque são dois tipos de entrada diferentes, e juntá-las
    afrouxaria a mais estrita.

    A contenção está na forma canônica que a análise de fluxo reconhece como
    sanitização de S2083 — validar ANTES contra o padrão fixo, montar a
    partir da raiz confiável, e conferir o prefixo depois de resolver. Vale
    a mesma nota do `caminho_de_escrita` sobre `Path.is_relative_to`: faz a
    mesma conta e o motor de taint não o conhece.
    """
    relativo = bruto.strip().removeprefix("./")
    if not PADRAO_SAIDA.fullmatch(relativo) or not relativo.endswith(".json"):
        raise ValueError(
            f"nome de entrada inválido: {bruto!r}\n"
            "esperado: caminho relativo terminando em .json, com letras, "
            "dígitos, '-', '_' e '.' (ex.: relatorios/VARIANCIA_23AGO.json).\n"
            f"para ler de outra raiz, defina {ENV_RAIZ_DE_SAIDA}."
        )
    raiz = raiz_de_saida()
    caminho = raiz / relativo
    raiz_resolvida = raiz.resolve(strict=False)
    resolvido = caminho.resolve(strict=False)
    prefixo = str(raiz_resolvida)
    if not prefixo.endswith(os.sep):
        prefixo += os.sep
    if not str(resolvido).startswith(prefixo):
        raise ValueError(f"entrada fora da raiz permitida: {resolvido}")
    if not resolvido.is_file():
        raise ValueError(f"arquivo de entrada não existe: {resolvido}")
    return resolvido
