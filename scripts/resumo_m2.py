#!/usr/bin/env python3
"""Resumo do relatório do backtest: os dez critérios, com o veredito de cada.

    python scripts/resumo_m2.py relatorios/M2_24AGO.json

O relatório inteiro passa de 4.000 linhas de JSON. Ler o veredito nele exige
saber onde procurar, e foi lendo o campo errado que um diagnóstico já saiu
invertido numa conversa real.

E ERA ISSO QUE ESTE RESUMO CONTINUAVA FAZENDO. A versão anterior imprimia,
para o critério 1.3, o campo `erro` — exatamente o campo que o relatório
manda NÃO ler, por escrito, na chave `calibracao_nota`: o `erro` compara a
probabilidade média prevista com a TAXA-BASE do balde, então um preditor que
cospe uma constante igual à taxa-base tira zero sem saber nada. O critério
1.3 é a CONJUNÇÃO de `calibracao_avaliavel` (pelo menos 3 faixas com
amostra) com `erro_de_confiabilidade` abaixo do limiar. Agora é isso que sai.

Por isso cada linha imprime também O CAMPO QUE FOI LIDO. Ler o campo errado
é erro silencioso por natureza: o número sai bem formatado de qualquer jeito,
e quem confere não tem como saber de onde ele veio. Com o caminho ao lado, a
conferência não exige abrir o JSON.

Três vereditos, e a diferença entre os dois últimos é a que mais importa:

    PASSA          medido, e o número atende o exigido
    REPROVA        medido, e o número não atende
    NAO AVALIAVEL  o relatório não sustenta a conta — o que NÃO é reprovar

Não interpreta nada além disso. Se o número for feio, imprime o número feio —
é a mesma regra do relatório que ele resume.
"""

from __future__ import annotations

import json
import signal
import sys
from itertools import pairwise
from typing import Any, NamedTuple

from pulsearb.backtest.report import MINIMO_DE_FAIXAS
from pulsearb.caminhos import (
    ENV_RAIZ_DE_SAIDA,
    PADRAO_SAIDA,
    raiz_de_saida,
)
from pulsearb.engine.decisao import BASE_DO_ENCOLHIMENTO

# Os limiares do VEREDITO_M2 "Regras de decisão", escritos ANTES dos números.
# Ficam aqui como constantes nomeadas para que mudar um critério seja uma
# edição visível no diff, e não um número trocado no meio de um `if`.
MINIMO_DE_TRADES = 200
LIMIAR_DE_CALIBRACAO = 0.05
PROFUNDIDADE_MINIMA_USDC = 200.0
MARKOUT_MINIMO_CENTAVOS = -0.5
HORAS_MINIMAS_DE_AMOSTRA = 20.0
DIVERGENCIA_MAXIMA = 0.01
FATOR_DE_DESCONTO_PESSIMISTA = "0.3"
SUFIXO_USDC = " USDC"
NOME_DA_CONTA_FECHADA = "Conta fechada do maker"

PASSA = "PASSA"
REPROVA = "REPROVA"
NAO_AVALIAVEL = "NAO AVALIAVEL"


def caminho_do_relatorio(bruto: str):
    """Monta o caminho do relatório a partir da raiz permitida.

    Mesmo tratamento que o `--json` recebeu no M2.5, e pela mesma razão
    registrada lá: conferir o caminho DEPOIS de montá-lo continua entregando
    a string de fora ao sistema de arquivos, e a análise de fluxo do
    SonarCloud aponta isso — com razão. Validar ANTES contra um padrão fixo e
    só então montar a partir de uma raiz confiável não deixa o valor externo
    chegar ao disco em forma nenhuma.

    O relatório é gravado sob essa mesma raiz pelo `--json`, então ler dali é
    simétrico: quem mudou a raiz para gravar usa a mesma variável para ler.
    """
    relativo = bruto.strip().removeprefix("./")
    if not PADRAO_SAIDA.fullmatch(relativo) or not relativo.endswith(".json"):
        raise SystemExit(
            f"nome de relatório inválido: {bruto!r}\n"
            "esperado: caminho relativo terminando em .json, com letras, "
            "dígitos, '-', '_' e '.' (ex.: relatorios/hora_1900.json).\n"
            f"para ler de outra raiz, defina {ENV_RAIZ_DE_SAIDA}."
        )
    raiz = raiz_de_saida()
    resolvido = (raiz / relativo).resolve(strict=False)
    if not resolvido.is_relative_to(raiz.resolve(strict=False)):
        raise SystemExit(f"relatório fora da raiz permitida: {resolvido}")
    if not resolvido.is_file():
        raise SystemExit(f"relatório não encontrado: {resolvido}")
    return resolvido


class Criterio(NamedTuple):
    """Uma linha do veredito. `campo` existe para poder ser conferida."""

    numero: str
    nome: str
    exigido: str
    medido: str
    veredito: str
    campo: str


def _julgar(ok: bool | None) -> str:
    """`None` vira NAO AVALIAVEL, nunca REPROVA.

    A distinção é o ponto: "o relatório não sustenta esta conta" e "a conta
    deu ruim" levam a trabalhos opostos — um manda instrumentar, o outro
    manda desistir da rota.
    """
    if ok is None:
        return NAO_AVALIAVEL
    return PASSA if ok else REPROVA


def _fundo(dados: Any, *chaves: str, padrao: Any = None) -> Any:
    """Desce por um caminho de chaves sem estourar em relatório antigo."""
    atual = dados
    for chave in chaves:
        if not isinstance(atual, dict):
            return padrao
        atual = atual.get(chave)
        if atual is None:
            return padrao
    return atual


def _numero(valor: Any, sufixo: str = "") -> str:
    if valor is None:
        return "ausente no relatorio"
    if isinstance(valor, float):
        return f"{valor:g}{sufixo}"
    return f"{valor}{sufixo}"


def _ordem_da_duracao(par: tuple[str, Any]) -> float:
    """`300s` antes de `3600s`. Ordem alfabética poria 14400s primeiro."""
    try:
        return float(par[0].rstrip("s"))
    except ValueError:
        return float("inf")


def _criterio_de_calibracao(backtest: dict[str, Any]) -> Criterio:
    campo = "backtest.calibracao[*].{calibracao_avaliavel,erro_de_confiabilidade}"
    exigido = (
        f"erro_de_confiabilidade < {LIMIAR_DE_CALIBRACAO:g} "
        f"em >= 1 balde AVALIAVEL"
    )
    calibracao = backtest.get("calibracao") or {}
    avaliaveis = {
        balde: dados
        for balde, dados in calibracao.items()
        if dados.get("calibracao_avaliavel")
    }
    if not avaliaveis:
        ocupadas = {
            balde: dados.get("faixas_ocupadas") for balde, dados in calibracao.items()
        }
        return Criterio(
            "1.3",
            "Calibracao",
            exigido,
            f"nenhum balde avaliavel (min {MINIMO_DE_FAIXAS} faixas); "
            f"faixas_ocupadas={ocupadas or 'sem baldes'}",
            NAO_AVALIAVEL,
            campo,
        )
    balde, dados = min(
        avaliaveis.items(),
        key=lambda par: abs(par[1].get("erro_de_confiabilidade") or 1.0),
    )
    erro = dados.get("erro_de_confiabilidade")
    return Criterio(
        "1.3",
        "Calibracao",
        exigido,
        f"{_numero(erro)} no balde {balde} "
        f"({dados.get('faixas_ocupadas')} faixas ocupadas)",
        _julgar(None if erro is None else abs(erro) < LIMIAR_DE_CALIBRACAO),
        campo,
    )


def _criterio_de_profundidade(relatorio: dict[str, Any]) -> Criterio:
    campo = "medicoes.profundidade.criterio_do_veredito.por_duracao[*].p50_3ticks_usdc"
    exigido = f">= {PROFUNDIDADE_MINIMA_USDC:g} USDC em >= 1 duracao"
    por_duracao = (
        _fundo(
            relatorio,
            "medicoes",
            "profundidade",
            "criterio_do_veredito",
            "por_duracao",
        )
        or {}
    )
    valores = {
        duracao: (dados or {}).get("p50_3ticks_usdc")
        for duracao, dados in por_duracao.items()
    }
    medidos = {d: v for d, v in valores.items() if isinstance(v, int | float)}
    if not medidos:
        return Criterio(
            "1.5", "Profundidade p50 3 ticks", exigido,
            "sem duracao medida", NAO_AVALIAVEL, campo,
        )
    return Criterio(
        "1.5",
        "Profundidade p50 3 ticks",
        exigido,
        ", ".join(
            f"{d}: {v:g}" for d, v in sorted(medidos.items(), key=_ordem_da_duracao)
        ),
        _julgar(max(medidos.values()) >= PROFUNDIDADE_MINIMA_USDC),
        campo,
    )


def criterios_do_taker(relatorio: dict[str, Any]) -> list[Criterio]:
    backtest = relatorio.get("backtest") or {}
    resumo = backtest.get("resumo") or {}
    pnl300 = resumo.get("pnl_liquido_usdc")
    trades = resumo.get("trades")
    pnl600 = _fundo(relatorio, "sensibilidade_latencia", "600ms", "pnl_liquido_usdc")
    return [
        Criterio(
            "1.1", "PnL liquido @300ms", "positivo", _numero(pnl300, SUFIXO_USDC),
            _julgar(None if pnl300 is None else pnl300 > 0),
            "backtest.resumo.pnl_liquido_usdc",
        ),
        Criterio(
            "1.2", "Trades", f">= {MINIMO_DE_TRADES}", _numero(trades),
            _julgar(None if trades is None else trades >= MINIMO_DE_TRADES),
            "backtest.resumo.trades",
        ),
        _criterio_de_calibracao(backtest),
        Criterio(
            "1.4", "PnL liquido @600ms", "positivo", _numero(pnl600, SUFIXO_USDC),
            _julgar(None if pnl600 is None else pnl600 > 0),
            "sensibilidade_latencia.600ms.pnl_liquido_usdc",
        ),
        _criterio_de_profundidade(relatorio),
    ]


def _criterio_da_conta_fechada(rota_maker: dict[str, Any]) -> Criterio:
    """1.6 — e o motivo de ele quase nunca poder ser respondido.

    `o_que_falta_para_fechar` lista os termos que a conta não tem. Enquanto
    ela não estiver vazia, o número que existe é PARCIAL por construção:
    `resultado_parcial_usdc` soma rewards e rebate e NÃO subtrai o custo de
    markout. Ler esse parcial como se fosse a conta fechada foi o erro que o
    primeiro veredito cometeu, e é o que esta função existe para impedir.
    """
    campo = "rota_maker.conta_fechada.o_que_falta_para_fechar"
    exigido = f"positiva com fator de desconto {FATOR_DE_DESCONTO_PESSIMISTA}"
    conta = rota_maker.get("conta_fechada") or {}
    falta = conta.get("o_que_falta_para_fechar")
    if falta is None:
        return Criterio("1.6", NOME_DA_CONTA_FECHADA, exigido,
                        "bloco ausente no relatorio", NAO_AVALIAVEL, campo)
    if falta:
        termos = ", ".join(str(t).split(":", 1)[0] for t in falta)
        return Criterio(
            "1.6", NOME_DA_CONTA_FECHADA, exigido,
            f"conta NAO fechada: faltam {len(falta)} termos ({termos})",
            NAO_AVALIAVEL, campo,
        )
    por_fator = rota_maker.get("sensibilidade_ao_fator") or {}
    valores = [
        celulas.get(FATOR_DE_DESCONTO_PESSIMISTA)
        for celulas in por_fator.values()
        if isinstance(celulas, dict)
    ]
    medidos = [v for v in valores if isinstance(v, int | float)]
    if not medidos:
        return Criterio(
            "1.6", NOME_DA_CONTA_FECHADA, exigido,
            f"sem celula no fator {FATOR_DE_DESCONTO_PESSIMISTA}",
            NAO_AVALIAVEL,
            f"rota_maker.sensibilidade_ao_fator[*][{FATOR_DE_DESCONTO_PESSIMISTA}]",
        )
    return Criterio(
        "1.6", NOME_DA_CONTA_FECHADA, exigido,
        _numero(max(medidos), SUFIXO_USDC), _julgar(max(medidos) > 0),
        f"rota_maker.sensibilidade_ao_fator[*][{FATOR_DE_DESCONTO_PESSIMISTA}]",
    )


def _markout_representativo(
    rota_maker: dict[str, Any],
) -> tuple[str, float, int] | None:
    """O recorte que RESPONDE, não o que agrada.

    O critério 1.7 pede "no p50 de pelo menos um recorte", e a leitura
    ingênua disso — pegar o melhor número da tabela — é uma armadilha de
    comparações múltiplas. A tabela tem `total` ao lado de recortes por
    duração e por HORA DO DIA: são duas dezenas de células, e o máximo entre
    elas é ruído por construção. A primeira rodada com esta função escolheu
    `hora_utc=01` com markout de +0,88 centavo — markout POSITIVO, isto é,
    lucro de adverse selection, que não existe: era uma célula pequena.

    Então a ordem é: `total` quando houver, e senão a célula com a MAIOR
    amostra. Nunca a mais favorável. E o `n` sai impresso junto, porque é
    ele que permite ao leitor desconfiar sem abrir o JSON.
    """
    tabela = _fundo(rota_maker, "markout", "markout_centavos_por_share") or {}
    celulas: list[tuple[str, float, int]] = []
    for recorte, horizontes in tabela.items():
        cinco_s = _fundo(horizontes or {}, "5s") or {}
        media = cinco_s.get("media")
        if isinstance(media, int | float):
            celulas.append((recorte, float(media), int(cinco_s.get("n") or 0)))
    if not celulas:
        return None
    total = next((celula for celula in celulas if celula[0] == "total"), None)
    return total or max(celulas, key=lambda celula: celula[2])


def _melhor_amostra(rota_maker: dict[str, Any]) -> tuple[str, float] | None:
    celulas = _fundo(rota_maker, "conta_fechada", "por_ordem_e_recorte") or {}
    horas = [
        (nome, float(dados["horas_de_amostra"]))
        for nome, dados in celulas.items()
        if isinstance((dados or {}).get("horas_de_amostra"), int | float)
    ]
    return max(horas, key=lambda par: par[1]) if horas else None


def _criterio_da_divergencia(relatorio: dict[str, Any]) -> Criterio:
    """1.9 — julgado sobre a população que o §2c diz poder invalidar.

    A `taxa` agregada soma duas populações que o próprio relatório separa e
    classifica de forma OPOSTA no bloco `lado_vazio.quais_invalidam`,
    registrado em VEREDITO_M2 §2c antes dos números:

    - `com_lado_vazio` — o servidor afirma um topo e a reconstrução não tem
      o lado. O §2c marca as suas causas como NÃO-invalidantes: é
      profundidade não contada, sinal para subir `--niveis-book`, não livro
      corrompido. No dia 24 são 92,8% da taxa agregada.
    - `com_magnitude_finita` — topo DESLOCADO: os dois livros têm o lado e
      discordam do preço. É esta a corrupção que o critério 1.9 teme,
      porque a conta do maker soma sobre o livro reconstruído.

    Julgar o agregado reprova o livro pela população que o critério de
    invalidação declarou inocente. Isto não é afrouxar o 1.9 depois de ver
    o número: é alinhá-lo à classificação que JÁ estava registrada. A taxa
    agregada continua impressa ao lado, para ninguém achar que sumiu.

    A salvaguarda da emenda (VEREDITO_M2) vale aqui, não só no papel: o
    desconto é POR NOME. Só sai da conta o lado vazio cuja causa o próprio
    relatório classifica como não-invalidante em `quais_invalidam`. Causa
    invalidante, causa que o relatório não classificou, ou lado vazio sem
    decomposição por causa — tudo isso CONTA CONTRA, e reprova até ser
    classificado. Sem isto, um relatório futuro com lado vazio por
    `sem_snapshot` (livro furado de verdade) passaria pelo mesmo desconto
    que inocenta truncagem de profundidade.
    """
    div = _fundo(relatorio, "integridade", "divergencia_topo_book") or {}
    taxa = div.get("taxa")
    comparacoes = div.get("comparacoes")
    deslocadas = div.get("com_magnitude_finita")
    exigido = f"populacao invalidante < {DIVERGENCIA_MAXIMA:.0%} das comparacoes"

    if (
        isinstance(taxa, int | float)
        and isinstance(comparacoes, int | float)
        and comparacoes > 0
        and isinstance(deslocadas, int | float)
    ):
        vazio_total = div.get("com_lado_vazio") or 0
        bloco_vazio = div.get("lado_vazio") or {}
        por_causa = (
            bloco_vazio.get("por_causa") or div.get("lado_vazio_por_causa") or {}
        )
        quais_invalidam = bloco_vazio.get("quais_invalidam") or {}
        inocentadas = sum(
            n
            for causa, n in por_causa.items()
            if isinstance(n, int | float) and quais_invalidam.get(causa) is False
        )
        vazio_contra = max(0, vazio_total - inocentadas)
        taxa_julgada = (deslocadas + vazio_contra) / comparacoes
        detalhe_vazio = (
            f"lado vazio nao inocentado {vazio_contra:,}"
            if vazio_contra
            else f"lado vazio {vazio_total:,} todo inocentado pelo 2c"
        )
        return Criterio(
            "1.9",
            "Divergencia do topo do livro",
            exigido,
            f"julgada {taxa_julgada:.2%} = topo deslocado "
            f"{deslocadas / comparacoes:.2%} + {detalhe_vazio} "
            f"(agregada {taxa:.2%})",
            _julgar(taxa_julgada < DIVERGENCIA_MAXIMA),
            "integridade.divergencia_topo_book.{(com_magnitude_finita"
            " + lado_vazio nao inocentado em quais_invalidam) / comparacoes}",
        )

    # Relatório antigo, sem a decomposição: só o agregado existe, e o
    # veredito é sobre ele — com o aviso de que mistura as populações.
    return Criterio(
        "1.9",
        "Divergencia do topo do livro",
        exigido,
        f"{taxa:.2%} (agregada — relatorio sem decomposicao)"
        if isinstance(taxa, int | float)
        else "ausente no relatorio",
        _julgar(
            None if not isinstance(taxa, int | float) else taxa < DIVERGENCIA_MAXIMA
        ),
        "integridade.divergencia_topo_book.taxa",
    )


def criterios_do_maker(relatorio: dict[str, Any]) -> list[Criterio]:
    rota = relatorio.get("rota_maker") or {}
    markout = _markout_representativo(rota)
    amostra = _melhor_amostra(rota)
    return [
        _criterio_da_conta_fechada(rota),
        Criterio(
            "1.7", "Markout 5s",
            f">= {MARKOUT_MINIMO_CENTAVOS:g} centavo/share em >= 1 recorte",
            # O criterio fala em p50; o relatorio traz `media`. Dizer qual
            # estatistica saiu, e sobre quantas execucoes, evita comparar
            # duas coisas diferentes depois.
            f"{markout[1]:g} (media de {markout[2]} execucoes) "
            f"no recorte {markout[0]}"
            if markout
            else "sem recorte com markout de 5s",
            _julgar(None if markout is None else markout[1] >= MARKOUT_MINIMO_CENTAVOS),
            "rota_maker.markout.markout_centavos_por_share[*].5s.media",
        ),
        Criterio(
            "1.8", "Horas de amostra", f">= {HORAS_MINIMAS_DE_AMOSTRA:g} h",
            f"{amostra[1]:g} h em {amostra[0]}" if amostra else "sem celula",
            _julgar(None if amostra is None else amostra[1] >= HORAS_MINIMAS_DE_AMOSTRA),
            "rota_maker.conta_fechada.por_ordem_e_recorte[*].horas_de_amostra",
        ),
        _criterio_da_divergencia(relatorio),
        # 1.10 nao e medicao: e um fato sobre a documentacao da Polymarket,
        # que o relatorio nao tem como observar. Enquanto a formula for
        # hipotese, ele REPROVA — e o relatorio diz isso de si mesmo no
        # proprio `aviso`, que sai impresso para nao virar palavra minha.
        Criterio(
            "1.10", "Formula de reward confirmada na doc", "sim",
            "nao — segue como hipotese",
            REPROVA,
            "rota_maker.rewards.hipoteses (fato externo ao relatorio)",
        ),
    ]


def _balde_do_diagnostico(backtest: dict[str, Any]) -> tuple[str, dict] | None:
    """O balde que sustenta o critério 1.3 — o mesmo que ele escolheu.

    Diagnosticar um balde diferente do que decidiu o veredito produziria uma
    explicação para um número que ninguém leu.
    """
    calibracao = backtest.get("calibracao") or {}
    avaliaveis = {
        balde: dados
        for balde, dados in calibracao.items()
        if dados.get("calibracao_avaliavel")
    }
    if not avaliaveis:
        return None
    return min(
        avaliaveis.items(),
        key=lambda par: abs(par[1].get("erro_de_confiabilidade") or 1.0),
    )


def leitura_do_vies(curva: dict[str, Any]) -> str:
    """A frase que separa "conserta encolhendo" de "troca o preditor".

    A pergunta acionável não é o sinal do viés médio: é se o erro tem ORDEM.
    Erro que cresce com a probabilidade prevista é excesso de confiança, e
    excesso de confiança tem conserto de uma linha — encolher a previsão em
    direção à taxa-base. Erro sem ordem não tem: qualquer encolhimento que
    acerte uma faixa piora outra, e o problema está no preditor.

    Reportar só "MISTO" quando há 3 faixas otimistas e 1 pessimista esconde
    exatamente o caso mais comum e mais tratável, que é o erro monótono
    passando pelo zero.
    """
    celulas = [
        (celula.get("previsto"), celula.get("erro"))
        for celula in curva.values()
        if isinstance(celula.get("erro"), int | float)
        and isinstance(celula.get("previsto"), int | float)
        and (celula.get("n") or 0) > 0
    ]
    if not celulas:
        return "sem faixa com amostra"
    celulas.sort()
    erros = [erro for _previsto, erro in celulas]
    acima = sum(1 for erro in erros if erro > 0)
    abaixo = sum(1 for erro in erros if erro < 0)

    crescente = all(a <= b for a, b in pairwise(erros))
    if crescente and len(erros) > 1 and erros[-1] > erros[0]:
        return (
            f"OTIMISTA CRESCENTE: o erro sobe de {erros[0]:+.4f} a "
            f"{erros[-1]:+.4f} conforme a confianca sobe. Encolher a previsao "
            "em direcao a taxa-base corrige."
        )
    if acima and abaixo:
        return (
            f"MISTO e SEM ORDEM ({acima} faixa(s) otimista(s), {abaixo} "
            "pessimista(s)) — nao ha encolhimento que acerte todas"
        )
    if acima:
        return f"OTIMISTA nas {acima} faixa(s) com amostra"
    if abaixo:
        return f"PESSIMISTA nas {abaixo} faixa(s) com amostra"
    return "sem viés: previsto igual ao realizado em todas as faixas"


def ece_encolhido(curva: dict[str, Any], base: float, fator: float) -> float | None:
    """ECE aproximado com toda previsão encolhida: p' = base + fator·(p − base).

    A aproximação: cada faixa desloca em bloco (o encolhimento é monótono e
    afim), então a média |previsto − realizado| ponderada pelas faixas
    EXISTENTES continua descrevendo o agrupamento — só os rótulos mudam.
    O que ela não captura é refinamento dentro de uma faixa, que só a
    reavaliação ponto a ponto no backtest dá.
    """
    soma = total = 0.0
    for celula in curva.values():
        n = celula.get("n") or 0
        previsto = celula.get("previsto")
        realizado = celula.get("realizado")
        if n and isinstance(previsto, int | float) and isinstance(realizado, int | float):
            soma += n * abs(base + fator * (previsto - base) - realizado)
            total += n
    return soma / total if total else None


def varredura_de_encolhimento(
    curva: dict[str, Any],
) -> tuple[float, float, float] | None:
    """(ECE sem encolher, melhor fator, ECE no melhor fator).

    A base é a MESMA do backtest (`BASE_DO_ENCOLHIMENTO` = 0,5), não a
    taxa realizada da própria curva. O fator que sai daqui alimenta
    `--fator-de-encolhimento`, que encolhe para 0,5 — otimizar contra
    outra base recomendaria um fator para uma transformação que o
    backtest nunca vai aplicar. E encolher para a taxa realizada do
    próprio período seria resgate in-sample: qualquer curva "calibra"
    quando puxada para a média que ela mesma realizou.

    O preditor constante seria RESGATADO por esta conta se ela rodasse
    nele com taxa-base perto de 0,5: encolher uma constante para 0,5
    produz 0,5, que "acerta" sempre que o mercado fica meio a meio. Por
    isso a varredura exige o mesmo mínimo de faixas ocupadas que torna um
    balde avaliável — sem estrutura na curva, ela devolve `None` em vez
    de um número que parece aprovação.

    O fator varre (0, 1] inteiro, no passo de 0,01: uma curva que precise
    de encolhimento agressivo (fator ~0,05) existe — é o preditor com
    ordem certa e escala absurda — e parar a busca em 0,30 imprimiria
    "continua reprovando" para um modelo que um fator menor salvaria.
    """
    pares = [
        (celula.get("n") or 0, celula.get("realizado"))
        for celula in curva.values()
        if (celula.get("n") or 0) > 0 and isinstance(celula.get("realizado"), int | float)
    ]
    total = sum(n for n, _ in pares)
    if not total or len(pares) < MINIMO_DE_FAIXAS:
        return None

    sem_encolher = ece_encolhido(curva, BASE_DO_ENCOLHIMENTO, 1.0)
    if sem_encolher is None:
        return None
    melhor_fator, melhor_ece = 1.0, sem_encolher
    for centesimos in range(1, 101):
        fator = centesimos / 100
        valor = ece_encolhido(curva, BASE_DO_ENCOLHIMENTO, fator)
        if valor is not None and valor < melhor_ece:
            melhor_fator, melhor_ece = fator, valor
    return (sem_encolher, melhor_fator, melhor_ece)


LARGURA_DA_FAIXA = 0.05


def faixas_ocupadas_apos_encolher(curva: dict[str, Any], fator: float) -> int:
    """Quantas faixas de 0,05 as previsões ocupariam DEPOIS de encolher.

    Achado em review: um fator agressivo comprime previsões que ocupavam
    três ou mais faixas em uma ou duas — e aí o backtest ponto a ponto
    marca `calibracao_avaliavel` como falso e o 1.3 vira NÃO AVALIÁVEL,
    não PASSA. Um "PASSARIA" baseado só no ECE aproximado seria promessa
    que a remedição não pode cumprir.
    """
    faixas = set()
    for celula in curva.values():
        n = celula.get("n") or 0
        previsto = celula.get("previsto")
        if n and isinstance(previsto, int | float):
            encolhido = BASE_DO_ENCOLHIMENTO + fator * (
                previsto - BASE_DO_ENCOLHIMENTO
            )
            faixas.add(min(int(encolhido / LARGURA_DA_FAIXA), 19))
    return len(faixas)


def veredito_do_encolhimento(
    curva: dict[str, Any], fator: float, ece_encolhido_final: float
) -> str:
    """O texto honesto sobre o que a remedição faria com este fator.

    Este texto NUNCA antecipa o veredito do backtest, por construção: o
    resumo só tem os agregados por faixa (n, previsto, realizado), e uma
    faixa de 0,05 que cavalga uma fronteira pós-encolhimento vai inteira
    para o grupo da sua média — tanto aqui quanto no `ece_encolhido`. O
    corte pela média erra para os DOIS lados (achados em review): pode
    prometer PASSA onde as previsões cruas se dividem pior, e pode
    contar menos faixas do que as previsões cruas realmente ocupam. Por
    isso o melhor caso é condicionado, e a compressão de faixas é
    apontada como RISCO, não como veredito. Só a remedição ponto a
    ponto, com as previsões cruas, decide.
    """
    if ece_encolhido_final >= LIMIAR_DE_CALIBRACAO:
        return "continuaria reprovando — o defeito nao e de escala"
    if faixas_ocupadas_apos_encolher(curva, fator) < MINIMO_DE_FAIXAS:
        return (
            "ECE aproximado abaixo do limiar, MAS as medias encolhidas "
            f"ocupam menos de {MINIMO_DE_FAIXAS} faixas — RISCO de a "
            "remedicao devolver NAO AVALIAVEL; so as previsoes cruas, "
            "no backtest, decidem"
        )
    return (
        f"abaixo do limiar de {LIMIAR_DE_CALIBRACAO:g} NA APROXIMACAO — "
        "quem decide e a remedicao ponto a ponto, que reagrupa as "
        "previsoes cruas"
    )


def _imprimir_diagnostico_da_calibracao(relatorio: dict[str, Any]) -> None:
    """POR QUE a calibração falha, e não só QUE ela falha.

    "Não calibrado" não diz o que consertar. A curva de confiabilidade diz:
    se o previsto passa do realizado nas faixas altas, o modelo é otimista
    onde aposta forte, e o conserto é encolher a confiança — não trocar de
    sinal. Se o viés troca de sentido entre faixas, não há correção monótona
    possível e o problema é o preditor, não a escala.

    Impresso sempre que houver balde avaliável, inclusive quando o 1.3 passa:
    passar com viés sistemático é informação, não silêncio.
    """
    escolhido = _balde_do_diagnostico(relatorio.get("backtest") or {})
    if escolhido is None:
        return
    balde, dados = escolhido
    curva = dados.get("curva_de_confiabilidade") or {}
    if not curva:
        return

    print("=" * 74)
    print(f"POR QUE A CALIBRACAO DA ESSE NUMERO  (balde {balde})")
    print("=" * 74)
    print(f"{'faixa':<14}{'n':>8}{'previsto':>11}{'realizado':>11}{'erro':>10}")
    soma_pesada = 0.0
    total = 0
    for faixa, celula in sorted(curva.items()):
        n = int(celula.get("n") or 0)
        erro = celula.get("erro")
        print(
            f"{faixa:<14}{n:>8}"
            f"{_numero(celula.get('previsto')):>11}"
            f"{_numero(celula.get('realizado')):>11}"
            f"{_numero(erro):>10}"
        )
        if isinstance(erro, int | float) and n:
            soma_pesada += erro * n
            total += n
    vies = soma_pesada / total if total else 0.0
    print()
    print(f"  vies medio ponderado: {vies:+.4f}  {leitura_do_vies(curva)}")
    print(
        "  Erro = previsto - realizado. Erro que CRESCE com a confianca se\n"
        "  corrige encolhendo a previsao em direcao a taxa-base; erro sem\n"
        "  ordem e defeito do preditor, nao de escala."
    )

    varrida = varredura_de_encolhimento(curva)
    if varrida is not None:
        sem, fator, com = varrida
        veredito = veredito_do_encolhimento(curva, fator, com)
        print()
        print("  ENCOLHIMENTO PARA A TAXA-BASE (variante MEDIDA, nao adotada):")
        print(
            f"  ECE {sem:.4f} sem encolher -> {com:.4f} com fator "
            f"{fator:.2f}  => {veredito}"
        )
        print(
            "  Tres ressalvas antes de comemorar: o fator foi ajustado NESTA\n"
            "  amostra (in-sample) — so vale apos repetir em dia independente;\n"
            "  o ECE aqui e aproximado por faixas; e o 1.1 precisa ser\n"
            "  REMEDIDO com a variante, porque o threshold le a probabilidade\n"
            "  encolhida e os trades mudam."
        )
    print()


def _imprimir(rotulo: str, valor: Any) -> None:
    print(f"{rotulo:<38} {valor}")


def _imprimir_criterios(titulo: str, criterios: list[Criterio], exige: str) -> None:
    print("=" * 74)
    print(titulo)
    print("=" * 74)
    for c in criterios:
        print(f"{c.veredito:<14} {c.numero:<5} {c.nome}")
        print(f"{'':14} exigido: {c.exigido}")
        print(f"{'':14} medido:  {c.medido}")
        print(f"{'':14} campo:   {c.campo}")
    reprovados = [c.numero for c in criterios if c.veredito == REPROVA]
    sem_conta = [c.numero for c in criterios if c.veredito == NAO_AVALIAVEL]
    print()
    if reprovados:
        print(f"  >> {exige} — REPROVA em {', '.join(reprovados)}")
    elif sem_conta:
        print(f"  >> {exige} — NAO CONCLUSIVO: {', '.join(sem_conta)} sem conta")
    else:
        print(f"  >> {exige} — PASSA em todos")
    if reprovados and sem_conta:
        print(f"     (sem conta, e portanto NAO reprovado: {', '.join(sem_conta)})")
    print()


#: Ordem de leitura das bandas: do mais longe do fechamento ao mais perto.
_ORDEM_DAS_BANDAS = (">240s", "240-120s", "120-60s", "60-30s", "<30s")


def _imprimir_horizonte(relatorio: dict[str, Any]) -> None:
    """A curva de horizonte: o preditor CRU forcado a operar em cada banda.

    Responde a pergunta do M3 escolhida por Paulo — o edge nao existe em lugar
    nenhum, ou existe num horizonte que a v1 nao opera? A regra de leitura ja
    esta no relatorio (`alguma_banda_com_edge`), computada pela §2d-bis; aqui
    so a torno legivel.
    """
    curva = relatorio.get("curva_de_horizonte") or {}
    por_banda = curva.get("por_banda") or {}
    if not por_banda:
        return
    print("=" * 74)
    print("EDGE POR HORIZONTE  (preditor CRU, uma rodada por banda — §2d-bis)")
    print("=" * 74)
    print(f"  {'banda':<12}{'trades':>8}{'hit':>9}{'PnL USDC':>12}"
          f"{'PnL/share':>12}  edge?")
    ordenadas = [b for b in _ORDEM_DAS_BANDAS if b in por_banda]
    ordenadas += [b for b in por_banda if b not in _ORDEM_DAS_BANDAS]
    com_edge = set(curva.get("bandas_com_edge") or [])
    fraco = set(curva.get("sinal_fraco") or [])
    for banda in ordenadas:
        d = por_banda[banda]
        hit = d.get("hit_rate")
        pnl = d.get("pnl_liquido_usdc")
        pps = d.get("pnl_por_share")
        marca = (
            "SIM" if banda in com_edge
            else "fraco (n<40)" if banda in fraco
            else "nao"
        )
        print(
            f"  {banda:<12}{d.get('trades', 0):>8}"
            f"{(f'{hit:.4f}' if hit is not None else '   -'):>9}"
            f"{(f'{pnl:.4f}' if pnl is not None else '   -'):>12}"
            f"{(f'{pps:.6f}' if pps is not None else '   -'):>12}  {marca}"
        )
    print()
    if curva.get("alguma_banda_com_edge"):
        print(
            f"  >> EDGE EM {', '.join(sorted(com_edge))}: o defeito e de "
            "HORIZONTE. O M3 opera nessa(s)\n     banda(s) e remede 1.1-1.5 "
            "restrito a ela(s)."
        )
    else:
        print(
            "  >> NENHUMA banda tem edge (pnl>0 E hit>0,5 E n>=40). Somado a\n"
            "     escala ja rejeitada (§2d), o preditor CRU nao tem edge em\n"
            "     horizonte nenhum — o M3 troca o preditor ou re-escopa."
        )
    if fraco:
        print(
            f"  sinal fraco (publica, NAO decide): {', '.join(sorted(fraco))} "
            "— pnl>0 e hit>0,5\n     mas n<40, dentro do ruido do proprio "
            "hit_rate."
        )
    print()


def _imprimir_captacao(relatorio: dict[str, Any]) -> None:
    gravacao = relatorio.get("gravacao") or {}
    print("=" * 74)
    print("CAPTACAO  (bloco 0 — decide se a gravacao presta)")
    print("=" * 74)
    cobertura = _fundo(gravacao, "stream_de_ancora", "cobertura_da_gravacao") or {}
    _imprimir("pior_fracao_coberta", cobertura.get("pior_fracao_coberta"))
    for ativo, v in sorted((cobertura.get("por_ativo") or {}).items()):
        _imprimir(
            f"  {ativo}",
            f"{v['fracao_da_gravacao']:.1%}  silencio_final={v['silencio_final_s']}s",
        )
    silencio = gravacao.get("silencio_do_rtds") or {}
    _imprimir("silencios", silencio.get("silencios"))
    _imprimir("total_s (uniao)", silencio.get("total_s"))
    _imprimir(
        "conexao_inteira", len(silencio.get("silencios_da_conexao_inteira") or [])
    )
    _imprimir(
        "suspeita_de_assinatura_caducada",
        silencio.get("suspeita_de_assinatura_caducada"),
    )
    for evento in silencio.get("eventos_coincidentes") or []:
        _imprimir(
            "  evento coincidente",
            f"{evento['quantos_ativos']} ativos, "
            f"dispersao {evento['dispersao_do_inicio_s']}s",
        )
    _imprimir(
        "janelas_conhecidas / com_resolucao",
        f"{gravacao.get('janelas_conhecidas')} / "
        f"{gravacao.get('janelas_com_resolucao')}",
    )
    print()


def _imprimir_ancora(relatorio: dict[str, Any]) -> None:
    veredito = _fundo(relatorio, "ancora", "veredito_da_varredura") or {}
    print("=" * 74)
    print("ANCORA")
    print("=" * 74)
    _imprimir(
        "elegiveis / recebidas",
        f"{veredito.get('janelas_elegiveis')} / {veredito.get('janelas_recebidas')}"
        f"  (min {veredito.get('minimo_para_veredito')})",
    )
    _imprimir(
        "sem_cobertura_do_stream", veredito.get("janelas_sem_cobertura_do_stream")
    )
    _imprimir("consistencia em tau=0", veredito.get("consistencia_do_tau_verificado"))
    distribuicao = veredito.get("distribuicao_das_elegiveis") or {}
    _imprimir("distribuicao (quartis)", distribuicao.get("quartis"))
    _imprimir("concentrada?", distribuicao.get("concentrada"))
    print(f"\n  {veredito.get('veredito')}\n")


FLAG_ENCOLHIDO = "--encolhido"


def relatorio_da_variante_encolhida(relatorio: dict[str, Any]) -> dict[str, Any] | None:
    """O relatório relido como se a variante encolhida fosse O backtest.

    A remedição do 1.1 precisa dos MESMOS critérios aplicados à variante —
    e aplicá-los a olho, lendo o JSON, é como o 1.3 passou dois vereditos
    sendo medido no campo errado. Trocando o bloco, o motor de critérios
    é um só: mesma leitura de campo, mesmos limiares, mesma tabela.

    As contagens de janela (`janelas_*`) vêm do bloco original de
    propósito: descrevem a GRAVAÇÃO, que é a mesma nas duas rodadas.
    """
    bloco = relatorio.get("encolhimento") or {}
    encolhido = (bloco.get("comparacao") or {}).get("encolhido")
    if not encolhido:
        return None
    original = relatorio.get("backtest") or {}
    da_gravacao = {
        chave: valor
        for chave, valor in original.items()
        if chave.startswith("janelas_")
    }
    return {**relatorio, "backtest": {**encolhido, **da_gravacao}}


def _imprimir_cabecalho_da_variante(relatorio: dict[str, Any]) -> None:
    """O aviso que impede a variante de ser lida como resultado."""
    bloco = relatorio.get("encolhimento") or {}
    faixa = bloco.get("faixa") or {}
    print("=" * 72)
    print("  VARIANTE ENCOLHIDA — NAO E O RESULTADO PRE-REGISTRADO")
    print("=" * 72)
    print(f"  fator {bloco.get('fator')}  base {bloco.get('base')}")
    print(
        f"  faixa: min {faixa.get('tempo_restante_min_s')} s, "
        f"max {faixa.get('tempo_restante_max_s')} s, entrada unica"
    )
    print(
        "  VALIDADE: so vale se o fator foi ajustado em periodo ANTERIOR ao\n"
        "  desta gravacao. Ajustado nesta, e in-sample e nao sustenta\n"
        "  veredito nenhum — nem a favor, nem contra."
    )
    print()


def main(argv: list[str] | None = None) -> None:
    """Imprime o resumo do relatório nomeado no argumento."""
    argumentos = list(sys.argv[1:] if argv is None else argv)
    variante_encolhida = FLAG_ENCOLHIDO in argumentos
    if variante_encolhida:
        argumentos.remove(FLAG_ENCOLHIDO)
    if len(argumentos) != 1:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        print(
            f"  {FLAG_ENCOLHIDO}: julga a variante do bloco `encolhimento` "
            "em vez do backtest cru.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    # `| head` fecha o cano e o Python morre com BrokenPipeError. Um resumo
    # que estoura quando alguém o pipeia é um resumo que não se pode pipeiar;
    # o default do sistema mata o processo em silêncio, que é o que qualquer
    # ferramenta de linha de comando faz.
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)

    with caminho_do_relatorio(argumentos[0]).open(encoding="utf-8") as arquivo:
        relatorio = json.load(arquivo)

    # O diagnostico de horizonte e sobre o preditor CRU e mora no topo do
    # relatorio; `--encolhido` reatribui `relatorio` a variante encolhida, que
    # nao o tem. Guardo o topo antes de trocar.
    relatorio_topo = relatorio

    if variante_encolhida:
        variante = relatorio_da_variante_encolhida(relatorio)
        if variante is None:
            print(
                "erro: este relatorio nao tem bloco `encolhimento` — rode o "
                "backtest com --fator-de-encolhimento.",
                file=sys.stderr,
            )
            raise SystemExit(2)
        _imprimir_cabecalho_da_variante(relatorio)
        relatorio = variante

    _imprimir_captacao(relatorio)
    _imprimir_ancora(relatorio)
    _imprimir_criterios(
        "OS 5 CRITERIOS DO TAKER",
        criterios_do_taker(relatorio),
        "TAKER VIAVEL exige as CINCO",
    )
    _imprimir_diagnostico_da_calibracao(relatorio)
    if not variante_encolhida:
        _imprimir_horizonte(relatorio_topo)
    _imprimir_criterios(
        "OS 5 CRITERIOS DO MAKER",
        criterios_do_maker(relatorio),
        "SO MAKER VIAVEL exige as CINCO",
    )
    print((relatorio.get("rota_maker") or {}).get("aviso", ""))


if __name__ == "__main__":
    main()
