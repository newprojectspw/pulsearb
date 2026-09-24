"""O item 4.2 lido do relato do SHADOW, e dito em português.

    .venv/bin/python scripts/resumo_da_rodada_maker.py \
        --relatos relatorios/RELATOS_4_2.jsonl \
        --diario data/diarios/shadow-maker-4-2.jsonl

O relato de 60 s sai no log do processo, uma linha JSON por relato:

    journalctl -u pulsearb-shadow-maker -o cat \
        | grep '"msg":"shadow"' > relatorios/RELATOS_4_2.jsonl

## Por que este arquivo existe

Porque o veredito do 4.2 **já está calculado** e não chega a ninguém.
`CaixaDoMaker.resumo()` publica `liquido_pro_rata_usdc` — rewards menos
markout, que é exatamente o "edge líquido medido" que o item exige — a cada
60 s, e nenhum leitor no repositório olha para ele. Quem quisesse o número
teria de abrir o `journalctl` e ler JSON à mão por 14 dias.

Esse é o defeito que o projeto **já pagou duas vezes**, e está escrito no
quadro: o `#96` pôs `fecha_no_pior_caso` no JSON e o `resumo_m2.py` continuou
imprimindo `NAO AVALIAVEL` com o texto antigo; um item que podia ser ❌ ficou
⬜ em toda rodada. O `resumo_das_rotas.py` nasceu dessa lição para o 1.11/1.12
— e o 4.2, que é o item que decide se o bot pode ver dinheiro, seguia sem.

Agora são QUATRO rodadas de 14 dias esperando leitor: a base e uma por regra
experimental (4.0 e/f/g). Sem ele, cada uma custa duas semanas para produzir
um JSON que ninguém soma.

## O que este arquivo NÃO faz

Não recalcula nada. Rewards, markout e o líquido são os números que o motor
ao vivo publicou; aqui só se lê, se confere e se diz. Uma segunda conta seria
uma segunda implementação da §15.3, e a divergência entre as duas apareceria
como diferença de mercado.

## Os quatro jeitos de uma rodada de 14 dias não valer nada

Cada um vira RECUSA com nome, nunca um número bonito:

1. **rodada confundida** — duas regras experimentais ligadas juntas. Uma muda
   QUANDO a ordem sai, outra muda o PREÇO, a terceira muda QUANDO ela volta;
   juntas não se distinguem. O RUNBOOK §10.1 já mandava conferir isso a olho
   no relato; aqui vira conta.
2. **regras mudaram no meio** — a unit anexa ao mesmo diário e um restart
   continua a rodada. Quem editar a unit e reiniciar mistura duas regras nos
   mesmos 14 dias, e nada denunciava.
3. **rodada dormiu** — item 3.14/3.16: os dois laços correm em tempo
   monotônico e congelam com a máquina. 24 h com ciclo 0,12 observaram 2,9 h.
4. **laço maker não subiu** — `laco_de_cotacao` registra "sem caminho de
   diario" e volta; a rodada segue viva, relatando, medindo nada.

## E por que os caminhos são constantes nomeadas

A primeira correção do `limite_pessimista` saiu **sem efeito**: o caminho foi
adivinhado (`rota_maker.conta_fechada.limite_pessimista`) e o bloco era IRMÃO
de `conta_fechada`. `ruff` passou, a saída ficou byte a byte idêntica, e só a
execução mostrou. Por isso cada caminho é uma constante, e há teste que
**percorre** cada uma dentro de um `estado()` que o `ProcessoShadow` de
verdade acabou de produzir. Rename de qualquer lado quebra o teste, em vez de
virar veredito ausente.
"""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from typing import Any

from pulsearb.caminhos import caminho_de_diario_lido, caminho_de_relatorio_lido
from pulsearb.execution.cliente_sombra import PREFIXO_DE_SOMBRA

# ── os caminhos que este arquivo lê, nomeados ────────────────────────────
#: A linha do relato de 60 s, entre as outras linhas de log do processo.
MSG_DO_RELATO = "shadow"

CAMPO_DO_MAKER = "maker"
CAMPO_DAS_REGRAS = "maker.regras"
CAMPO_DOS_MOTIVOS = "maker.motivos"
CAMPO_DAS_REPOUSANDO = "maker.cotacoes_repousando"

CAMPO_DO_LIQUIDO = "maker.caixa.liquido_pro_rata_usdc"
CAMPO_DOS_REWARDS = "maker.caixa.rewards_pro_rata_usdc"
CAMPO_DO_MARKOUT_USDC = "maker.caixa.markout.resultado_usdc"
CAMPO_DO_MARKOUT_CS = "maker.caixa.markout.centavos_por_share"
CAMPO_DAS_MEDIDAS = "maker.caixa.markout.medidas"
CAMPO_DO_REPOUSO_S = "maker.caixa.segundos_repousando"
CAMPO_DAS_ATRAVESSADAS = "maker.caixa.execucoes_possiveis.atravessadas"
CAMPO_DAS_NO_NIVEL = "maker.caixa.execucoes_possiveis.no_nivel"
CAMPO_DOS_REENVIADOS = "maker.caixa.execucoes_possiveis.reenviados"
CAMPO_DOS_PENDENTES = "maker.caixa.execucoes_possiveis.pendentes_de_markout"

CAMPO_DO_CICLO = "vigilia.da_rodada.ciclo_de_trabalho"
CAMPO_DA_PAREDE = "vigilia.da_rodada.parede_s"
CAMPO_DO_SONO = "vigilia.da_rodada.dormiu_s"
CAMPO_DO_FALHOU = "falhou"

#: Marca a linha que o `pulsearb.live.shadow.main` emite DEPOIS de o `run`
#: devolver — a única que traz o tempo de parede completo. O `laco_de_relato`
#: retorna quando o prazo vence, então o último relato de 60 s sai sempre
#: antes do fim: sem esta marca, toda rodada de 14 dias bem sucedida sairia
#: `curta_demais` (revisão do Codex, #131).
CAMPO_DO_FIM = "fim_da_rodada"

#: Os knobs experimentais, e como se lê "ligado" em cada um.
#:
#: `is not None` e `is True`, NUNCA verdade booleana: `ticks_abaixo_do
#: _microprice=0` e `pausa_apos_fill_toxico_s=0.0` são valores LIGADOS
#: válidos (ambos `ge=0` nas configurações) e falsos em Python. Uma rodada
#: com a âncora em 0 ticks passaria por rodada-base, e o que ela mediu
#: entraria no quadro com o nome errado.
#:
#: `fracao_maxima_do_pool` entra aqui porque também MUDA o que é cotado
#: (barra candidata por participação no pool): ligá-la junto com outra regra
#: numa rodada de 14 dias confundiria as duas, que é o que este conjunto
#: existe para recusar. `None` = desligada.
REGRAS_EXPERIMENTAIS = ("recolhe_quando_o_livro_anda", "ticks_abaixo_do_microprice",
                        "pausa_apos_fill_toxico_s", "fracao_maxima_do_pool")

#: 4.2 — "SHADOW ≥ 2 semanas". Em segundos de PAREDE, que é o relógio que
#: conta duas semanas; o monotônico entra separado, no ciclo de trabalho.
DIAS_EXIGIDOS = 14
PAREDE_EXIGIDA_S = DIAS_EXIGIDOS * 24 * 3600

#: Abaixo disto a rodada dormiu o bastante para não ser medida de 14 dias.
#: Não é 1,0: NTP e a granularidade do relato tiram frações sem que a
#: máquina tenha dormido. É 0,99 porque a rodada de referência (3.16, 24 h na
#: tomada com `caffeinate -dimsu`) mediu 1,000 — qualquer coisa abaixo de
#: 0,99 são horas de observação que não existiram.
CICLO_MINIMO = 0.99

PASSA, REPROVA, NAO_AVALIAVEL = "PASSA", "REPROVA", "NAO AVALIAVEL"

#: Toda recusa tem nome — constante, nunca frase livre. Recusa anônima não
#: vira métrica e não distingue "a rodada travou" de "a rodada não achou
#: trade".
MOTIVOS = {
    "sem_relato": "nenhuma linha de relato no arquivo",
    "relato_sem_maker": "o laço maker não subiu — a rodada relatou e não cotou",
    "rodada_confundida": "mais de uma regra experimental ligada na mesma rodada",
    "regras_mudaram_no_meio": "as regras não são as mesmas do começo ao fim",
    "rodada_dormiu": "ciclo de trabalho abaixo do mínimo — horas que não existiram",
    "processo_falhou": "a rodada terminou com `falhou` preenchido",
    "curta_demais": "ainda não completou as duas semanas",
    "campo_ausente": "o relato não traz o campo do veredito",
    "ordem_sem_prefixo_de_sombra": "id de ordem sem `sombra-` no diário",
    "rodada_nao_terminou": "o fluxo não traz a linha de fim — rodada em curso, "
                           "journal capturado no meio, ou processo morto",
    "custo_nao_medido": "zero medidas de markout — o líquido é rewards puros",
    "diario_ilegivel": "o diário foi pedido e não pôde ser lido — o prefixo "
                       "`sombra-` ficou sem conferir",
}


def _ler(registro: Any, caminho: str) -> Any:
    """Percorre um caminho pontilhado. `None` se qualquer degrau falta.

    `None` e não exceção porque relato de versão antiga é caso normal — o que
    não é normal é o resumo dizer PASSA sobre campo que não existe, e disso
    cuida o `campo_ausente`.
    """
    atual = registro
    for passo in caminho.split("."):
        if not isinstance(atual, dict) or passo not in atual:
            return None
        atual = atual[passo]
    return atual


def _ligada(regras: dict[str, Any], nome: str) -> bool:
    """`True` quando o knob está LIGADO — pelo tipo, não pela verdade.

    Ver a nota de `REGRAS_EXPERIMENTAIS`: zero é ligado.
    """
    valor = regras.get(nome)
    if nome == "recolhe_quando_o_livro_anda":
        return valor is True
    return valor is not None


def regras_da_rodada(relatos: list[dict]) -> tuple[dict[str, Any] | None, str | None]:
    """As regras em vigor, e a recusa se elas não valem para uma medida.

    Confere as DUAS coisas que invalidam a rodada inteira: mais de uma regra
    ligada (as três não se distinguem entre si) e regras diferentes entre o
    primeiro e o último relato (restart com a unit editada — a unit anexa ao
    mesmo diário de propósito, então isso não deixa rastro nenhum hoje).
    """
    vistas = [_ler(x, CAMPO_DAS_REGRAS) for x in relatos]
    if not vistas or any(r is None for r in vistas):
        # RELATO SEM `regras` RECUSA, em vez de ser filtrado fora. Filtrar era
        # um buraco (revisão do Codex, #131): um trecho de versão antiga, ou
        # um trecho em que o maker não subiu, sumia da conferência — e o
        # `relato_da_rodada` somava os rewards e o markout dele assim mesmo,
        # com o veredito validando só a configuração do trecho que TINHA a
        # informação. Medida de regra desconhecida entrava num PASSA.
        return None, "campo_ausente"
    primeira = vistas[0]
    if any(atual != primeira for atual in vistas):
        return primeira, "regras_mudaram_no_meio"
    ligadas = [nome for nome in REGRAS_EXPERIMENTAIS if _ligada(primeira, nome)]
    if len(ligadas) > 1:
        return primeira, "rodada_confundida"
    return primeira, None


def ler_relatos(caminho: str) -> list[dict]:
    """As linhas de relato do arquivo, na ordem em que saíram.

    Linha que não é JSON não derruba a leitura: o `journalctl` intercala
    linhas do systemd com as do processo, e abortar por causa delas obrigaria
    a filtrar antes. Linha que é JSON mas não é relato é descartada pelo
    `msg`.
    """
    destino = caminho_de_relatorio_lido(caminho, extensoes=(".jsonl",))
    relatos = []
    with destino.open(encoding="utf-8") as arquivo:
        for linha in arquivo:
            linha = linha.strip()
            if not linha:
                continue
            try:
                registro = json.loads(linha)
            except json.JSONDecodeError:
                continue
            if isinstance(registro, dict) and registro.get("msg") == MSG_DO_RELATO:
                relatos.append(registro)
    return relatos


def conferir_diario(caminho: str) -> dict[str, Any]:
    """O que o diário sabe e o relato não: quanto cada cotação repousou.

    E a linha do RUNBOOK §10.1 que hoje é conferida a olho — **todo id tem de
    começar com `sombra-`**. Um id sem o prefixo significaria ordem REAL, e a
    instrução ali é parar o serviço. Conferir isso a cada leitura custa uma
    comparação de string e não depende de alguém lembrar.
    """
    destino = caminho_de_diario_lido(caminho)
    colocadas = 0
    repousos: list[float] = []
    sem_prefixo: list[str] = []
    with destino.open(encoding="utf-8") as arquivo:
        for linha in arquivo:
            linha = linha.strip()
            if not linha:
                continue
            try:
                registro = json.loads(linha)
            except json.JSONDecodeError:
                continue
            if not isinstance(registro, dict):
                continue
            order_id = registro.get("order_id")
            if isinstance(order_id, str) and not order_id.startswith(PREFIXO_DE_SOMBRA):
                sem_prefixo.append(order_id)
            if registro.get("evento") == "cotacao_colocada":
                colocadas += 1
            elif registro.get("evento") == "cotacao_cancelada":
                segundos = registro.get("segundos_repousada")
                if isinstance(segundos, int | float):
                    repousos.append(float(segundos))
    repousos.sort()
    return {
        "colocadas": colocadas,
        "encerradas": len(repousos),
        "repouso_s": {
            "p50": _percentil(repousos, 0.50),
            "p90": _percentil(repousos, 0.90),
            "max": repousos[-1] if repousos else None,
        },
        "ids_sem_prefixo_de_sombra": sem_prefixo,
    }


def _percentil(ordenados: list[float], q: float) -> float | None:
    if not ordenados:
        return None
    indice = min(len(ordenados) - 1, int(q * len(ordenados)))
    return round(ordenados[indice], 1)


#: Campos CUMULATIVOS desde a subida do processo: o último relato de um
#: trecho é o total daquele trecho, e a soma dos trechos é o total da rodada.

CAMPOS_QUE_SOMAM = (
    CAMPO_DOS_REWARDS, CAMPO_DO_MARKOUT_USDC, CAMPO_DAS_MEDIDAS,
    CAMPO_DO_REPOUSO_S, CAMPO_DAS_ATRAVESSADAS, CAMPO_DAS_NO_NIVEL,
    CAMPO_DOS_REENVIADOS, CAMPO_DO_LIQUIDO, CAMPO_DA_PAREDE, CAMPO_DO_SONO,
    # `pendentes_de_markout` TAMBÉM soma, e eu tinha argumentado o contrário.
    # Ele é instantâneo dentro de um trecho — mas o que se lê aqui é o ÚLTIMO
    # relato de cada trecho, e para um trecho que MORREU esse número são
    # execuções cujo markout nunca vai fechar: a `CaixaDoMaker` some com o
    # processo. Somar não conta duas vezes (cada trecho tem caixa própria) e
    # é a única forma de o fill perdido num trecho antigo aparecer na
    # ressalva quando o último trecho termina com zero (revisão do Codex,
    # #131).
    CAMPO_DOS_PENDENTES,
)


def segmentos(relatos: list[dict]) -> list[list[dict]]:
    """Corta o fluxo onde `parede_s` CAI — cada trecho é uma subida do processo.

    Dentro de um trecho `parede_s` só cresce; uma queda é o `run` refazendo
    `inicio_parede`. É a assinatura do restart, e não há outra: nada mais no
    relato muda quando o processo volta.
    """
    if not relatos:
        return []
    trechos: list[list[dict]] = [[relatos[0]]]
    anterior = _ler(relatos[0], CAMPO_DA_PAREDE)
    for relato in relatos[1:]:
        atual = _ler(relato, CAMPO_DA_PAREDE)
        if anterior is not None and atual is not None and atual < anterior:
            trechos.append([])
        trechos[-1].append(relato)
        anterior = atual
    return trechos


def relato_da_rodada(relatos: list[dict]) -> dict[str, Any]:
    """Os trechos somados num relato só, com a forma do original.

    **Por que somar em vez de ler o último.** `Restart=on-failure` devolve a
    rodada depois de uma queda de rede, mas a `CaixaDoMaker` não persiste
    nada e cada subida conta do zero: ler só o último relato reportaria
    apenas o trecho desde a última volta, como se fosse a rodada inteira.

    **Por que isto não é uma segunda conta.** Nada aqui recalcula reward nem
    markout: os totais de cada trecho são os que o motor publicou, e o que se
    faz é somá-los. O markout em ¢/share e o ciclo de trabalho não somam —
    são médias —, então entram ponderados pelo que o próprio motor publicou
    ao lado deles (shares e tempo de parede).

    **O que se perde, e é perda de verdade:** os segundos entre a queda e a
    volta não foram observados, e por isso não contam para as duas semanas.
    A rodada leva mais calendário do que 14 dias para fechar 14 dias de
    medida — que é o número certo.
    """
    trechos = segmentos(relatos)
    if len(trechos) <= 1:
        return relatos[-1] if relatos else {}

    ultimos = [t[-1] for t in trechos]
    somado = deepcopy(ultimos[-1])

    for campo in CAMPOS_QUE_SOMAM:
        parcelas = [_ler(u, campo) for u in ultimos]
        if any(p is None for p in parcelas):
            # PARCELA QUE FALTA ZERA O CAMPO, não deixa o valor do último
            # trecho. Deixar era um buraco de verdade (revisão do Codex,
            # #131): um trecho de 13 dias em que o maker não subiu, mais um
            # dia medido, somava 14 dias de `parede_s` e ficava com os
            # rewards e o markout DO ÚLTIMO DIA — 14 dias no relógio, um dia
            # na conta, veredito PASSA.
            #
            # `None` cai no `campo_ausente` do `_julgar`, que é o que a
            # situação é: a rodada não tem esse número.
            _escrever(somado, campo, None)
            continue
        _escrever(somado, campo, sum(parcelas))

    _escrever(somado, CAMPO_DO_MARKOUT_CS, _media_ponderada(
        [(_ler(u, CAMPO_DO_MARKOUT_CS), _ler(u, "maker.caixa.markout.shares"))
         for u in ultimos]
    ))
    _escrever(somado, CAMPO_DO_CICLO, _media_ponderada(
        [(_ler(u, CAMPO_DO_CICLO), _ler(u, CAMPO_DA_PAREDE)) for u in ultimos]
    ))
    # `motivos` também é cumulativo por trecho, e é contagem: soma por chave.
    motivos: dict[str, int] = {}
    for ultimo in ultimos:
        for nome, n in (_ler(ultimo, CAMPO_DOS_MOTIVOS) or {}).items():
            motivos[nome] = motivos.get(nome, 0) + n
    _escrever(somado, CAMPO_DOS_MOTIVOS, dict(sorted(motivos.items())))
    return somado


def _media_ponderada(pares: list[tuple[Any, Any]]) -> float | None:
    """Média dos trechos, pelo peso que o motor publicou ao lado de cada uma.

    Somar médias seria inventar número; e uma média simples daria o mesmo
    peso a um trecho de 5 minutos e a um de 13 dias.
    """
    validos = [
        (valor, peso) for valor, peso in pares
        if valor is not None and isinstance(peso, int | float) and peso > 0
    ]
    if not validos:
        return None
    total = sum(peso for _, peso in validos)
    return round(sum(valor * peso for valor, peso in validos) / total, 4)


def _escrever(registro: dict, caminho: str, valor: Any) -> None:
    """Grava num caminho pontilhado que JÁ EXISTE — não cria degrau.

    Criar degrau esconderia o campo que sumiu do motor, que é exatamente o
    que o `campo_ausente` existe para pegar.
    """
    passos = caminho.split(".")
    atual = registro
    for passo in passos[:-1]:
        if not isinstance(atual, dict) or passo not in atual:
            return
        atual = atual[passo]
    if isinstance(atual, dict) and passos[-1] in atual:
        atual[passos[-1]] = valor


def _julgar(
    relatos: list[dict], diario: dict[str, Any] | None = None
) -> tuple[str, str | None, dict[str, Any]]:
    """O veredito do 4.2, o motivo quando não há veredito, e o que foi lido.

    A ordem das recusas é a ordem em que elas invalidam. Primeiro a que não
    é sobre a medida: id de ordem sem `sombra-` significaria ordem REAL, e
    isso vale mesmo sem relato nenhum. Depois as que dizem que a rodada não é
    uma medida (sem relato, sem maker, confundida, não terminou, dormiu,
    falhou), e só então a conta. Uma rodada confundida com líquido positivo
    não é um PASSA com ressalva — é um número sem item.
    """
    if diario is not None and diario["ids_sem_prefixo_de_sombra"]:
        return NAO_AVALIAVEL, "ordem_sem_prefixo_de_sombra", {}
    if not relatos:
        return NAO_AVALIAVEL, "sem_relato", {}
    # O relato da RODADA, não o último trecho dela: `Restart=on-failure`
    # devolve o processo depois de uma queda, e cada subida conta do zero.
    ultimo = relato_da_rodada(relatos)
    lido = {campo: _ler(ultimo, campo) for campo in (
        CAMPO_DO_LIQUIDO, CAMPO_DOS_REWARDS, CAMPO_DO_MARKOUT_USDC,
        CAMPO_DO_MARKOUT_CS, CAMPO_DAS_MEDIDAS, CAMPO_DO_REPOUSO_S,
        CAMPO_DAS_ATRAVESSADAS, CAMPO_DAS_NO_NIVEL, CAMPO_DOS_REENVIADOS,
        CAMPO_DAS_REPOUSANDO, CAMPO_DO_CICLO, CAMPO_DA_PAREDE, CAMPO_DO_SONO,
        CAMPO_DOS_PENDENTES,
    )}

    if _ler(ultimo, CAMPO_DO_MAKER) is None:
        return NAO_AVALIAVEL, "relato_sem_maker", lido
    _, recusa = regras_da_rodada(relatos)
    if recusa is not None:
        return NAO_AVALIAVEL, recusa, lido
    if ultimo.get(CAMPO_DO_FALHOU) is not None:
        return NAO_AVALIAVEL, "processo_falhou", lido

    ciclo, parede = lido[CAMPO_DO_CICLO], lido[CAMPO_DA_PAREDE]
    liquido = lido[CAMPO_DO_LIQUIDO]
    if ciclo is None or parede is None or liquido is None:
        return NAO_AVALIAVEL, "campo_ausente", lido
    if ciclo < CICLO_MINIMO:
        # ANTES do `rodada_nao_terminou`, e de propósito: uma rodada em curso
        # que está dormindo tem as duas recusas ao mesmo tempo, e só esta é
        # acionável AGORA. "Não terminou" no meio de uma rodada de 14 dias é
        # a notícia esperada; "dormiu" é o que faz esperar não adiantar.
        return NAO_AVALIAVEL, "rodada_dormiu", lido
    if ultimo.get(CAMPO_DO_FIM) is not True:
        # Sem a linha de fim, o que se tem é um corte do fluxo — e o tempo de
        # parede do último relato de 60 s não é o da rodada. Vale para os
        # três casos: rodada em curso, journal capturado no meio, e processo
        # morto pelo systemd. Vem antes do `curta_demais` porque sem o fim da
        # rodada não há tempo de rodada para comparar com as duas semanas.
        return NAO_AVALIAVEL, "rodada_nao_terminou", lido
    if parede < PAREDE_EXIGIDA_S:
        return NAO_AVALIAVEL, "curta_demais", lido
    if not lido[CAMPO_DAS_MEDIDAS]:
        # Rewards puros nao sao a conta: o custo da rota e o markout de quem
        # forneceu liquidez, e zero medidas quer dizer que ele NAO FOI
        # OBSERVADO — que e diferente de ter sido observado zero. E a mesma
        # distincao do `sem_recortes` no 1.6, e ela estava escrita no rodape
        # da conta e ausente do veredito (revisao do Codex, #131).
        return NAO_AVALIAVEL, "custo_nao_medido", lido
    return (PASSA if liquido > 0 else REPROVA), None, lido


# ── o que sai na tela ────────────────────────────────────────────────────
def _titulo(texto: str) -> None:
    print("\n" + "=" * 74)
    print(texto)
    print("=" * 74)


def _dias(segundos: float | None) -> str:
    return "—" if segundos is None else f"{segundos / 86400:.2f} d"


def _usdc(valor: float | None) -> str:
    return "—" if valor is None else f"{valor:+.4f} USDC"


def _imprimir_regras(regras: dict[str, Any] | None) -> None:
    print("\nREGRA EM MEDIDA")
    if regras is None:
        print("  — o relato não traz `maker.regras` (versão antiga do motor?)")
        return
    ligadas = [nome for nome in REGRAS_EXPERIMENTAIS if _ligada(regras, nome)]
    if not ligadas:
        print("  nenhuma — esta é a rodada BASE, e é ela que dá o de-para")
    for nome in ligadas:
        print(f"  {nome} = {regras.get(nome)!r}")
    desligadas = [n for n in REGRAS_EXPERIMENTAIS if n not in ligadas]
    if desligadas:
        print(f"  desligadas: {', '.join(desligadas)}")


def _imprimir_conta(lido: dict[str, Any]) -> None:
    print("\nA CONTA DO 4.2 — o que o motor publicou, somado aqui por ninguém")
    print(f"  rewards pro-rata        {_usdc(lido.get(CAMPO_DOS_REWARDS))}")
    medidas = lido.get(CAMPO_DAS_MEDIDAS)
    centavos = lido.get(CAMPO_DO_MARKOUT_CS)
    sufixo = (
        f"   ({medidas} medidas · {centavos} c/share)"
        if medidas else "   (NENHUMA medida de markout)"
    )
    print(f"  markout                 {_usdc(lido.get(CAMPO_DO_MARKOUT_USDC))}{sufixo}")
    print("  " + "─" * 46)
    print(f"  líquido pro-rata        {_usdc(lido.get(CAMPO_DO_LIQUIDO))}")
    print(
        f"\n  execuções possíveis     atravessadas {lido.get(CAMPO_DAS_ATRAVESSADAS)}"
        f" · no nível {lido.get(CAMPO_DAS_NO_NIVEL)}"
        f" · reenviados {lido.get(CAMPO_DOS_REENVIADOS)}"
    )
    pendentes = lido.get(CAMPO_DOS_PENDENTES)
    if pendentes:
        print(
            f"\n  {pendentes} EXECUÇÃO(ÕES) SEM MARKOUT FECHADO no fim da rodada.\n"
            "  O markout fecha 5 s depois do fill; as que caíram no último\n"
            "  intervalo não tiveram os 5 s. O custo delas NÃO está no líquido\n"
            "  acima, então ele é um LIMITE SUPERIOR por exatamente essas — e\n"
            "  uma perna de 1.000 shares não é ruído. O mesmo vale para a\n"
            "  última cadência de 15 s, cujos prints o laço não chegou a olhar\n"
            "  antes de o processo encerrar: essas execuções não aparecem nem\n"
            "  como pendentes (revisão do Codex, #131 — o conserto de verdade\n"
            "  é o motor fechar a conta antes de sair, e ele não está feito)."
        )
    if not medidas:
        print(
            "\n  SEM MARKOUT MEDIDO o líquido é rewards puros, e rewards puros\n"
            "  são a metade otimista da conta: o custo da rota é o markout de\n"
            "  quem forneceu liquidez, e ele não foi observado nesta rodada.\n"
            "  Isso não é `medi custo zero` — é `não medi custo`."
        )


def _imprimir_tempo(lido: dict[str, Any]) -> None:
    print("\nTEMPO — o 3.14/3.16, que separa rodada parada de mercado quieto")
    print(f"  parede                  {_dias(lido.get(CAMPO_DA_PAREDE))}"
          f" de {DIAS_EXIGIDOS} d exigidos")
    print(f"  dormiu                  {_dias(lido.get(CAMPO_DO_SONO))}")
    print(f"  ciclo de trabalho       {lido.get(CAMPO_DO_CICLO)}"
          f"   (mínimo {CICLO_MINIMO})")
    # COTAÇÃO-horas, e o rótulo importa: `segundos_repousando` soma o tempo
    # de TODAS as cotações, então com 60 no livro ele passa de longe o tempo
    # de parede. Imprimi-lo em dias ao lado da parede convidaria a lê-lo como
    # fração da rodada — é a unidade que o quadro usa nas rodadas r7/r8.
    repouso_s = lido.get(CAMPO_DO_REPOUSO_S)
    horas = "—" if repouso_s is None else f"{repouso_s / 3600:.1f} cotação-horas"
    print(f"  tempo repousando        {horas}"
          f"  ({lido.get(CAMPO_DAS_REPOUSANDO)} cotações no livro agora)")


def _imprimir_motivos(relatos: list[dict], quantos: int = 10) -> None:
    # Do relato SOMADO: `motivos` é cumulativo desde a subida, então o último
    # trecho sozinho contaria só as passadas desde a última volta.
    motivos = _ler(relato_da_rodada(relatos), CAMPO_DOS_MOTIVOS) if relatos else None
    print("\nPOR QUE NÃO COTOU — acumulado desde o início da rodada")
    if not motivos:
        print("  — sem motivos no relato")
        return
    for nome, n in sorted(motivos.items(), key=lambda kv: -kv[1])[:quantos]:
        print(f"  {nome:<34} {n}")
    if "sem_pool_de_reward" in motivos and len(motivos) == 1:
        print(
            "\n  MOTIVO ÚNICO `sem_pool_de_reward`: o opt-in não pegou\n"
            "  (`PULSEARB_DESCOBRIR_POOLS_DE_REWARD` ausente na unit). A\n"
            "  rodada está viva e não mede a rota — RUNBOOK §10.1."
        )


def _imprimir_reinicios(relatos: list[dict]) -> None:
    """Quantas vezes o processo voltou, e o que isso custou à medida.

    `Restart=on-failure` devolve a rodada depois de uma queda de rede, mas a
    `CaixaDoMaker` não persiste nada e o `run` refaz `inicio_parede`: cada
    subida conta do zero. A conta acima já vem SOMADA por trecho, então os
    rewards e o markout da rodada inteira estão lá — o que não está, e não
    tem como estar, é o que aconteceu com o processo fora do ar.

    Por isso as duas semanas levam mais de 14 dias de calendário para fechar:
    o que conta é tempo OBSERVADO.
    """
    trechos = segmentos(relatos)
    if len(trechos) <= 1:
        return
    paredes = [_ler(t[-1], CAMPO_DA_PAREDE) for t in trechos]
    medidos = [f"{(p or 0) / 86400:.2f}" for p in paredes]
    print(
        f"\n  O PROCESSO VOLTOU {len(trechos) - 1}x — a conta acima é a SOMA de"
        f" {len(trechos)} trechos.\n"
        f"  medidos, em dias: {' + '.join(medidos)}\n"
        "  A caixa não persiste, então cada subida conta do zero e o que se\n"
        "  soma é o total publicado por trecho. O tempo fora do ar não foi\n"
        "  observado e NÃO conta para as duas semanas — a rodada leva mais de\n"
        "  14 dias de calendário para fechar 14 dias de medida."
    )


def _imprimir_diario(diario: dict[str, Any]) -> None:
    print("\nO DIÁRIO — quantas cotações repousaram, e por quanto (§10.2)")
    print(f"  colocadas               {diario['colocadas']}")
    print(f"  encerradas              {diario['encerradas']}")
    repouso = diario["repouso_s"]
    print(f"  repouso p50/p90/máx     {repouso['p50']} / {repouso['p90']}"
          f" / {repouso['max']} s")
    sem_prefixo = diario["ids_sem_prefixo_de_sombra"]
    if sem_prefixo:
        print(
            f"\n  *** {len(sem_prefixo)} id(s) SEM o prefixo `{PREFIXO_DE_SOMBRA}` ***\n"
            f"  primeiro: {sem_prefixo[0]!r}\n"
            "  RUNBOOK §10.1: isto significaria ORDEM REAL. `systemctl stop`\n"
            "  no serviço e abrir issue antes de qualquer outra coisa."
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="resumo_da_rodada_maker")
    parser.add_argument("--relatos", required=True)
    parser.add_argument("--diario", default=None)
    args = parser.parse_args(argv)

    try:
        relatos = ler_relatos(args.relatos)
    except ValueError as erro:
        print(f"aviso: {erro}", file=sys.stderr)
        relatos = []

    plural = "relato" if len(relatos) == 1 else "relatos"
    _titulo(f"RODADA DO MAKER — item 4.2 ({len(relatos)} {plural} de 60 s)")
    regras, _ = regras_da_rodada(relatos) if relatos else (None, None)
    _imprimir_regras(regras)

    # O diário entra ANTES do julgamento: a conferência do prefixo `sombra-`
    # é recusa, não aviso, e uma recusa impressa depois do veredito seria um
    # veredito tomado sem ela.
    diario, diario_ilegivel = None, False
    if args.diario:
        try:
            diario = conferir_diario(args.diario)
        except ValueError as erro:
            # Pediram o diário e ele não foi lido: isso NÃO é o mesmo que não
            # ter pedido. Seguir sem ele pularia em silêncio a conferência do
            # prefixo `sombra-` — o ensaio sairia declarado válido sem que
            # ninguém tivesse olhado o artefato de segurança dele (revisão do
            # Codex, #131).
            print(f"aviso: {erro}", file=sys.stderr)
            diario_ilegivel = True

    veredito, motivo, lido = _julgar(relatos, diario)
    if diario_ilegivel:
        veredito, motivo = NAO_AVALIAVEL, "diario_ilegivel"
    if relatos:
        _imprimir_tempo(lido)
        _imprimir_reinicios(relatos)
        _imprimir_motivos(relatos)
        _imprimir_conta(lido)

    if diario is not None:
        _imprimir_diario(diario)

    _titulo(f"4.2 — SHADOW >= {DIAS_EXIGIDOS} DIAS COM EDGE LIQUIDO MEDIDO")
    print(f"  {veredito}" + (f"   ({motivo}: {MOTIVOS[motivo]})" if motivo else ""))
    print(
        "\n  E o que este resumo NÃO diz: que a rota pode ir a LIVE. O 4.2 é um\n"
        "  item entre os do quadro, a trava tripla do 3.4 continua fechada, e\n"
        "  um líquido positivo aqui é SOMBRA — intenção anotada, não execução."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
