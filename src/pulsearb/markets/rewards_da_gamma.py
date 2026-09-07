"""Ler os parâmetros de reward do payload CRU da Gamma. Uma implementação só.

## Por que este módulo existe, e por que ele não é um util

Estas funções moravam dentro do `recorder/__main__.py`, e só o recorder as
usava — o caminho ao vivo nunca leu reward nenhum. Quando a rota maker (4.0)
precisou dos mesmos números para decidir onde cotar, havia duas saídas: copiar
a leitura para o lado ao vivo, ou compartilhar.

Copiar seria o erro que o `CLAUDE.md` nomeia como grave:

    *"O motor ao vivo e o backtest usam as MESMAS funções. Se cada um tiver a
    sua cópia, uma divergência entre SHADOW e backtest parece diferença de
    mercado quando é diferença de código."*

E aqui a cópia seria especialmente cara, porque **a leitura é ambígua de
propósito**: a Gamma põe a lista de rewards sob três nomes possíveis e a taxa
diária sob seis. Duas cópias divergiriam no dia em que uma aprendesse uma
grafia nova e a outra não — e a divergência apareceria como "o SHADOW achou
pool onde o backtest não achou", que se investiga no mercado antes de se
investigar no código.

## A ambiguidade não é frescura: ela já custou um marco

`[VERIFICADO]` §6.1b: apostar numa chave única a partir do que parecia
razoável fez o parser ler ZERO eventos, com o relatório saindo normal. Daí
aceitar as variantes em vez de escolher uma — custa uma linha, e a alternativa
já custou o marco inteiro.

E daí, também, `forma_dos_rewards` existir: quando a taxa sai `None`, três
explicações com consertos OPOSTOS produzem o mesmo `None` (o mercado não
participa · o nosso leitor erra a chave · o programa expirou para a janela).
Sem o array cru, a pergunta não tem resposta — e foi por isso que ela ficou
sem resposta uma vez.
"""

from __future__ import annotations

from typing import Any

#: Onde a Gamma pode pôr a lista de rewards. `clobRewards` é o que se viu ao
#: vivo (§12.8); `rewards_config` é o nome no SDK. Aceitar os três custa uma
#: linha; apostar no errado custou o marco inteiro no `price_change` (§6.1b).
CHAVES_DE_LISTA_DE_REWARD = ("clobRewards", "rewards_config", "rewardsConfig")

#: E como cada entrada pode chamar a taxa diária.
CHAVES_DE_TAXA_DIARIA = (
    "rewardsDailyRate",
    "rewards_daily_rate",
    "dailyRate",
    "daily_rate",
    "totalDailyRate",
    "total_daily_rate",
)

#: Onde a Gamma põe o tamanho mínimo e o spread máximo que pontuam.
CHAVE_MIN_SIZE = "rewardsMinSize"
CHAVE_MAX_SPREAD = "rewardsMaxSpread"


def numero(valor: Any) -> float | None:
    """`float` ou `None` — string vazia e lixo viram `None`, não zero.

    Zero seria um valor legítimo de taxa ("pool de zero"), então confundir
    "não veio" com "veio zero" apagaria a diferença entre um mercado sem
    programa e um com programa vazio.
    """
    if valor is None or isinstance(valor, bool):
        return None
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def lista_de_rewards(gamma: dict[str, Any]) -> tuple[str | None, list[Any]]:
    """A lista de rewards e sob que chave ela veio."""
    for chave in CHAVES_DE_LISTA_DE_REWARD:
        bruto = gamma.get(chave)
        if isinstance(bruto, list):
            return chave, bruto
    return None, []


def taxa_diaria_de_reward(gamma: dict[str, Any]) -> float | None:
    """Soma as taxas diárias da lista de rewards (`[VERIFICADO]` ao vivo, §12.8).

    É lista porque um mercado pode ter mais de uma fonte de reward (nativa e
    patrocinada, que o CLOB expõe como `native_daily_rate` e
    `sponsored_daily_rate`). Somar é o que corresponde ao `total_daily_rate`.

    Devolve `None`, e não `0.0`, quando nenhuma entrada trouxe taxa: sem
    numerador não há pool, e a janela tem de SAIR da conta em vez de receber
    um default inventado.
    """
    _, bruto = lista_de_rewards(gamma)
    total = 0.0
    achou = False
    for item in bruto:
        if not isinstance(item, dict):
            continue
        for chave in CHAVES_DE_TAXA_DIARIA:
            taxa = numero(item.get(chave))
            if taxa is not None:
                total += taxa
                achou = True
                break
    return total if achou else None


def forma_dos_rewards(gamma: dict[str, Any]) -> dict[str, Any]:
    """O que a Gamma REALMENTE mandou sobre reward, sem interpretação.

    Três explicações produzem o mesmo `rewards_daily_rate: None`, e elas têm
    consertos OPOSTOS:

    1. **o mercado não participa** — a lista nem existe;
    2. **o nosso leitor erra a chave** — a lista existe e as entradas usam um
       nome que não procurávamos (o defeito do `price_change`, §6.1b);
    3. **o programa expirou para aquela janela** — a lista existe, tem taxa, e
       tem `start_date`/`end_date` fora do intervalo.

    Sem o array cru gravado, a pergunta não tem resposta.
    """
    chave, bruto = lista_de_rewards(gamma)
    entradas: list[dict[str, Any]] = [i for i in bruto if isinstance(i, dict)]
    return {
        "chave_da_lista": chave,
        "n_entradas": len(entradas),
        "chaves_das_entradas": sorted({k for item in entradas for k in item}),
        # O array CRU, do jeito que veio. É ele que carrega start_date/
        # end_date e permite decidir entre "expirou" e "não participa".
        "entradas": entradas[:4],
    }


def parametros_de_reward(gamma: dict[str, Any]) -> tuple[float, float, float] | None:
    """`(daily_rate, min_size, max_spread_em_FRACAO)`, ou `None` sem pool.

    O que o caminho ao vivo precisa para montar `ParametrosDeReward` sem
    reimplementar nada. `None` quando falta taxa ou spread máximo — os dois
    sem os quais a conta de reward não existe, e inventá-los produziria
    receita onde não há nenhuma.

    **O `max_spread` sai em CENTAVOS e vai embora em FRAÇÃO.** É a mesma
    conversão que o `ParametrosDeReward.do_mercado` faz (`/100`), e ela mora
    aqui para que os dois lados não possam divergir sobre a unidade — dividir
    duas vezes, ou nenhuma, dá um pool 100× errado sem levantar exceção.
    """
    taxa = taxa_diaria_de_reward(gamma)
    if taxa is None or taxa <= 0:
        return None
    bruto_spread = numero(gamma.get(CHAVE_MAX_SPREAD))
    if bruto_spread is None:
        return None
    min_size = numero(gamma.get(CHAVE_MIN_SIZE)) or 0.0
    return taxa, min_size, bruto_spread / 100.0
