"""A fatia de execução do maker — o número que decide o 1.6.

O QUE ESTA MEDIÇÃO EXISTE PARA RESPONDER
─────────────────────────────────────────
A conta da rota maker tem dois termos de sinais opostos:

    resultado = rewards − markout × shares_executadas

`rewards` é calculável: sai da fórmula confirmada `S(v,s)=((v−s)/v)²×b` e da
nossa fatia do score, e **não depende da fila**. `markout` está medido:
−0,1974 ¢/share (critério 1.7). O que falta — e o que trava o 1.6 — é
`shares_executadas`, que depende de **quem executa primeiro**.

A aritmética sobre a gravação de 25/08 mostra que o sinal do resultado vira
dentro de uma faixa estreita de fatia de execução:

    50 shares, 2 lados:   positivo abaixo de ~20 % do fluxo
    200 shares, 2 lados:  positivo abaixo de ~30 %

Ou seja: a rota não é "viável" nem "inviável" em abstrato. Ela é uma função de
um número que ninguém tinha medido.

A FILA NÃO É OBSERVÁVEL — MAS OS DOIS LIMITES SÃO
──────────────────────────────────────────────────
O WS entrega níveis **agregados**: dá para saber que um preço tem 100 shares,
não entre quantas ordens nem em que ordem elas chegaram. Então a posição da
nossa cotação na fila é, e continua sendo, desconhecida.

O que **é** observável é o quanto de cada nível foi consumido. E isso delimita
a execução pelos dois lados:

- **PIOR CASO (primeiros da fila).** Toda execução no topo nos pega, até o
  nosso tamanho. É o teto do custo de markout.
- **MELHOR CASO (últimos da fila).** Só nos pega quando o consumo esgota tudo
  o que estava na frente. É o piso.

O resultado real está entre os dois, e **os dois são medidos aqui**. Se nem o
melhor caso fechar a conta, a rota morre sem precisar da fila; se o pior caso
já fechar, ela vive sem precisar da fila. Só a faixa entre eles exige mais.

A ASSIMETRIA QUE ISTO REVELA, E QUE ESTAVA SENDO LIDA AO CONTRÁRIO
──────────────────────────────────────────────────────────────────
O reward não vê a fila; o markout vê. Então **estar atrás na fila ganha o mesmo
reward e paga menos markout**. A fila é obstáculo à MEDIÇÃO, mas o efeito dela
no resultado tem sinal favorável para quem está atrás — o oposto do que o
quadro presumia ao tratá-la só como impedimento.

**Nada aqui envia ordem.** É reconstrução sobre gravação, como todo o
`analysis/`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class FatiaDeExecucao:
    """Os dois limites, e o que separa um do outro."""

    #: Execuções no topo consideradas (as que dá para atribuir a um nível).
    execucoes: int
    #: Shares que o fluxo consumiu no topo, no total.
    shares_do_fluxo: float
    #: Shares que pegaríamos se fôssemos os PRIMEIROS da fila.
    shares_no_pior_caso: float
    #: Shares que pegaríamos se fôssemos os ÚLTIMOS.
    shares_no_melhor_caso: float
    #: Execuções que esgotaram o nível inteiro — as únicas que pegam quem
    #: está no fim da fila.
    execucoes_que_esgotaram_o_nivel: int
    #: Descartadas por não haver livro ou nível no instante. Contadas em vez
    #: de silenciadas: "não medi" e "medi zero" levam a decisões opostas.
    sem_referencia: int

    @property
    def fatia_no_pior_caso(self) -> float | None:
        if self.shares_do_fluxo <= 0:
            return None
        return self.shares_no_pior_caso / self.shares_do_fluxo

    @property
    def fatia_no_melhor_caso(self) -> float | None:
        if self.shares_do_fluxo <= 0:
            return None
        return self.shares_no_melhor_caso / self.shares_do_fluxo


def medir_fatia_de_execucao(
    janelas: list[Any],
    *,
    nossa_cotacao_shares: float,
) -> FatiaDeExecucao:
    """Quanto do fluxo do topo nos executaria, nos dois extremos de fila.

    Para cada execução observada no topo, comparamos o tamanho consumido com o
    que havia no nível:

    - **pior caso:** levamos `min(consumido, nossa_cotacao)` — como se
      estivéssemos na frente de todos;
    - **melhor caso:** só levamos se `consumido > nivel_alheio`, e nesse caso
      levamos `min(consumido − nivel_alheio, nossa_cotacao)` — como se
      estivéssemos atrás de todos.

    `nivel_alheio` é o tamanho que o WS mostra no melhor preço, e é aí que
    mora a única suposição desta medição: **o tamanho publicado não nos
    inclui**. Isso é verdade num backtest (nunca estivemos lá) e seria falso
    ao vivo — ao vivo a nossa própria ordem entra no agregado, e o número
    correto viria de descontá-la. Por isso a função recebe o tamanho como
    parâmetro em vez de lê-lo do livro.
    """
    execucoes = 0
    sem_referencia = 0
    esgotaram = 0
    fluxo = 0.0
    pior = 0.0
    melhor = 0.0

    for janela in janelas:
        tick = float(getattr(janela, "tick_size", 0.01) or 0.01)
        timelines = [t for t in janela.books.values() if t is not None and t.ts]
        if not timelines:
            continue
        for ts_ns, preco, tamanho, lado in getattr(janela, "trades", []):
            book = _book_em(timelines, ts_ns)
            if book is None:
                sem_referencia += 1
                continue
            # O maker que estamos simulando cota no topo. Execução funda no
            # livro é de outra ordem, não da nossa.
            niveis = book.asks if lado == "BUY" else book.bids
            if not niveis:
                sem_referencia += 1
                continue
            melhor_preco, nivel_alheio = niveis[0]
            if abs(preco - melhor_preco) > tick / 2:
                sem_referencia += 1
                continue

            execucoes += 1
            consumido = float(tamanho)
            fluxo += consumido

            # Na frente de todos: levamos o que couber na nossa cotação.
            pior += min(consumido, nossa_cotacao_shares)

            # Atrás de todos: só sobra para nós o que passar do nível alheio.
            sobra = consumido - float(nivel_alheio)
            if sobra > 0:
                esgotaram += 1
                melhor += min(sobra, nossa_cotacao_shares)

    return FatiaDeExecucao(
        execucoes=execucoes,
        shares_do_fluxo=fluxo,
        shares_no_pior_caso=pior,
        shares_no_melhor_caso=melhor,
        execucoes_que_esgotaram_o_nivel=esgotaram,
        sem_referencia=sem_referencia,
    )


def conta_do_maker(
    fatia: FatiaDeExecucao,
    *,
    rewards_usdc: float,
    markout_centavos_por_share: float,
) -> dict[str, Any]:
    """A conta fechada nos dois extremos — o que o 1.6 pede, com a faixa.

    Devolve o líquido no pior e no melhor caso. **Se os dois tiverem o mesmo
    sinal, a fila deixa de importar para a decisão** — e é esse o resultado
    que fecharia o 1.6 sem observar a fila.

    O markout entra com o sinal que tem: negativo é custo. Invertê-lo aqui
    transformaria adverse selection em receita, que é o erro mais caro
    possível nesta conta.
    """
    custo_por_share = -markout_centavos_por_share / 100.0
    custo_pior = fatia.shares_no_pior_caso * custo_por_share
    custo_melhor = fatia.shares_no_melhor_caso * custo_por_share
    liquido_pior = rewards_usdc - custo_pior
    liquido_melhor = rewards_usdc - custo_melhor
    mesmo_sinal = (liquido_pior > 0) == (liquido_melhor > 0)
    return {
        "rewards_usdc": rewards_usdc,
        "markout_centavos_por_share": markout_centavos_por_share,
        "execucoes_no_topo": fatia.execucoes,
        "shares_do_fluxo": fatia.shares_do_fluxo,
        "pior_caso": {
            "posicao": "primeiros da fila",
            "shares_executadas": fatia.shares_no_pior_caso,
            "fatia_do_fluxo": fatia.fatia_no_pior_caso,
            "custo_de_markout_usdc": custo_pior,
            "liquido_usdc": liquido_pior,
        },
        "melhor_caso": {
            "posicao": "ultimos da fila",
            "shares_executadas": fatia.shares_no_melhor_caso,
            "fatia_do_fluxo": fatia.fatia_no_melhor_caso,
            "custo_de_markout_usdc": custo_melhor,
            "liquido_usdc": liquido_melhor,
            "execucoes_que_esgotaram_o_nivel": (
                fatia.execucoes_que_esgotaram_o_nivel
            ),
        },
        "a_fila_decide": not mesmo_sinal,
        "leitura": (
            "Os dois casos com o MESMO sinal fecham o 1.6 sem observar a "
            "fila: qualquer posicao real cai entre eles. Sinais diferentes "
            "significam que a fila decide, e ai o numero que falta e a "
            "posicao — que o WS agregado nao entrega. O markout entra com o "
            "sinal que tem: negativo e custo."
        ),
    }


def _book_em(timelines: list[Any], ts_ns: int) -> Any:
    """O primeiro book disponível naquele instante, entre as pernas."""
    for timeline in timelines:
        book = timeline.at(ts_ns)
        if book is not None:
            return book
    return None
