"""Item 4.2 — o RELÓGIO da rota maker em SHADOW: o que a cotação-sombra
teria rendido, e quantas vezes teria executado.

O SHADOW do maker, até aqui, respondia *onde cotou e por que não cotou*
(`LacoMaker.motivos`). Não respondia a pergunta do 4.2 — *edge líquido
MEDIDO* — porque nada somava o que as cotações repousando rendiam nem
olhava se alguém as teria executado. Duas semanas de SHADOW sem essas duas
somas são duas semanas de relógio parado.

O QUE SE SOMA AQUI, E COM QUE HONESTIDADE
─────────────────────────────────────────
**1. Rewards, pela MESMA função do laço.** A cada passada, para cada cotação
que repousa, `estimar_retorno` é chamada com `horas = tempo desde a última
passada`, sobre o livro DESTE instante — a mesma fórmula (§15.3, `score_de_
nivel`/`combinar_lados`/`denominador_pessimista`) que decidiu colocar a
cotação. Não é uma segunda conta: é a conta do laço, integrada no tempo.
Publica-se em duas colunas, de propósito:

- `rewards_pro_rata_usdc`: a fatia do pool × taxa diária × tempo. É o que o
  §15.3 paga pelo score, e o reward NÃO depende de posição na fila.
- `rewards_com_captura_usdc`: a mesma soma × `FATOR_DE_CAPTURA_PADRAO`
  (0,3), a hipótese conservadora com que o 1.6 e o 1.12 foram avaliados.

Quem lê escolhe a coluna; o módulo não escolhe por ele. E o denominador é o
`denominador_pessimista` — TETO do que os outros somam —, então a fatia é
PISO, nas duas colunas.

**2. Execuções possíveis, pelos prints de negócio.** O WS entrega
`last_trade_price` (§6.1a): preço, tamanho e o lado do TAKER. Um `SELL` no
token onde temos bid a `P`, a preço `≤ P`, é um taker que desceu os bids até
o nosso nível ou além — por prioridade de preço, o nosso bid teria sido
consumido (inteiro, se o print passou ABAIXO de `P`; parcial e dependente
da fila, se parou EM `P`). Conta-se as duas, separadas, porque a segunda
carrega a hipótese de fila que a primeira não carrega.

É LIMITE INFERIOR do que executaria: só se olha o print no token da perna,
e o *matching* do CLOB também casa Up contra Down (§4) — uma compra de Up
pode consumir um bid de Down sem print no token do Down. Fica dito.

**3. Markout das execuções possíveis.** Para cada uma, o meio do livro do
token na passada seguinte em que já se passaram `horizonte_markout_s`
(5 s), contra o NOSSO preço: `(meio − P) × 100` centavos por share — como
compramos a `P`, positivo é o meio subindo a nosso favor, negativo é
atropelamento. A cadência do laço é 15 s, então o horizonte REAL fica entre
5 e ~20 s e sai publicado (`horizonte_medio_s`) em vez de se chamar "5 s".
Mesma convenção de sinal de `medir_markout` (quem forneceu a liquidez).

O QUE NÃO SE PROMETE
────────────────────
Nada aqui prova lucro. O que este módulo faz é deixar o SHADOW **capaz de
reprovar** a rota maker com número — que é o que faltava para o 4.2 poder
começar a contar.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pulsearb.analysis.rewards import ParametrosDeReward
from pulsearb.backtest.book import OrderBook
from pulsearb.live.cotacao import (
    FATOR_DE_CAPTURA_PADRAO,
    RetornoEstimado,
    estimar_retorno,
)
from pulsearb.live.livros import Negocio
from pulsearb.live.repouso import CotacaoAberta
from pulsearb.obs.logging import get_logger

log = get_logger(__name__)

#: Quanto tempo depois do print se mede o meio. Igual ao horizonte central do
#: `medir_markout` (1 s / 5 s / 30 s) para os números serem comparáveis.
HORIZONTE_DE_MARKOUT_S = 5.0

#: Execução possível que fique sem livro para medir por mais que isto é
#: descartada e contada (`markout_sem_referencia`), não medida com livro velho.
PRAZO_PARA_MEDIR_S = 120.0

#: Passada com intervalo maior que isto desde a última (máquina suspensa,
#: laço travado) NÃO acumula o intervalo inteiro: acumula só o teto. O 3.14
#: mostrou que o sono da máquina congela o laço sem congelar o relógio de
#: parede, e somar horas de sono como horas repousando inflaria o reward.
INTERVALO_MAXIMO_POR_PASSADA_S = 60.0

#: Tolerância na comparação de preço com o print (o CLOB manda decimal como
#: string; `float` de "0.48" e a nossa grade podem diferir no último bit).
EPS = 1e-9


@dataclass(slots=True)
class ExecucaoPossivel:
    """Um print que teria consumido uma perna nossa — à espera do markout."""

    slug: str
    token_id: str
    lado_up: bool
    preco_nosso: float
    preco_do_print: float
    shares: float
    #: `atravessada`: o print passou ABAIXO do nosso preço. `no_nivel`: parou
    #: no nosso preço — a fila decide quanto foi nosso.
    tipo: str
    ts_ns: int


def _atraso_do_print_s(negocio: Any, agora_ns: int) -> float | None:
    """Quanto tempo depois do carimbo DO SERVIDOR o print foi lido aqui.

    Um print com atraso de minutos numa conexão viva é reenvio de
    reassinatura, não negócio de agora — e a caixa o teria contado como
    execução. Fica no log para a leitura do diário separar os dois; não
    filtra, porque o quanto de atraso é "antigo" ainda não foi medido.
    """
    servidor_ms = getattr(negocio, "ts_servidor_ms", None)
    if servidor_ms is None:
        return None
    return round(agora_ns / 1e9 - servidor_ms / 1e3, 3)


@dataclass
class CaixaDoMaker:
    """As somas do 4.2, por rodada. Sem I/O: quem tem o livro injeta."""

    fator_de_captura: float = FATOR_DE_CAPTURA_PADRAO
    horizonte_markout_s: float = HORIZONTE_DE_MARKOUT_S

    rewards_pro_rata_usdc: float = 0.0
    rewards_com_captura_usdc: float = 0.0
    segundos_repousando: float = 0.0
    segundos_pontuando: float = 0.0
    acertos: int = 0
    intervalos_truncados: int = 0

    execucoes_atravessadas: int = 0
    execucoes_no_nivel: int = 0
    shares_atravessadas: float = 0.0
    shares_no_nivel: float = 0.0
    prints_vistos: int = 0

    markout_medidas: int = 0
    markout_soma_centavos_x_shares: float = 0.0
    markout_shares: float = 0.0
    markout_soma_horizonte_s: float = 0.0
    markout_sem_referencia: int = 0

    _ultimo_acerto_epoch: dict[str, float] = field(default_factory=dict)
    _ultimo_print_ns: dict[str, int] = field(default_factory=dict)
    _pendentes: list[ExecucaoPossivel] = field(default_factory=list)

    # ─────────────────────────────────────────────────────────────── rewards
    def acertar(
        self,
        slug: str,
        aberta: CotacaoAberta,
        livro: OrderBook,
        params: ParametrosDeReward,
        *,
        agora_epoch: float,
    ) -> RetornoEstimado | None:
        """Soma o que `aberta` rendeu desde a última passada, sobre `livro`."""
        desde = max(self._ultimo_acerto_epoch.get(slug, 0.0), aberta.desde_epoch)
        intervalo = agora_epoch - desde
        self._ultimo_acerto_epoch[slug] = agora_epoch
        if intervalo <= 0.0:
            return None
        if intervalo > INTERVALO_MAXIMO_POR_PASSADA_S:
            self.intervalos_truncados += 1
            intervalo = INTERVALO_MAXIMO_POR_PASSADA_S

        estimado = estimar_retorno(
            aberta.cotacao,
            livro,
            params,
            horas=intervalo / 3600.0,
            fator_de_captura=self.fator_de_captura,
        )
        self.acertos += 1
        self.segundos_repousando += intervalo
        if estimado is None:
            return None
        if estimado.pontua:
            self.segundos_pontuando += intervalo
        self.rewards_com_captura_usdc += estimado.rewards_usdc
        if self.fator_de_captura > 0.0:
            self.rewards_pro_rata_usdc += estimado.rewards_usdc / self.fator_de_captura
        return estimado

    # ────────────────────────────────────────────────────────────── execuções
    def conferir_prints(
        self,
        slug: str,
        aberta: CotacaoAberta,
        *,
        token_up: str,
        token_down: str,
        negocios_desde: Callable[..., list[Negocio]],
        agora_ns: int,
    ) -> int:
        """Olha os prints desde a última passada e anota os que nos pegariam."""
        desde_ns = max(
            self._ultimo_print_ns.get(slug, 0), int(aberta.desde_epoch * 1e9)
        )
        self._ultimo_print_ns[slug] = agora_ns
        pernas = (
            (token_up, True, aberta.preco_up),
            (token_down, False, aberta.preco_down),
        )
        novas = 0
        for token_id, lado_up, preco_nosso in pernas:
            if preco_nosso <= 0.0:
                continue
            for negocio in negocios_desde(token_id, ts_ns=desde_ns):
                self.prints_vistos += 1
                if negocio.lado != "SELL" or negocio.preco > preco_nosso + EPS:
                    continue
                if negocio.preco < preco_nosso - EPS:
                    tipo = "atravessada"
                    shares = aberta.cotacao.tamanho
                    self.execucoes_atravessadas += 1
                    self.shares_atravessadas += shares
                else:
                    tipo = "no_nivel"
                    shares = min(negocio.tamanho, aberta.cotacao.tamanho)
                    self.execucoes_no_nivel += 1
                    self.shares_no_nivel += shares
                self._pendentes.append(
                    ExecucaoPossivel(
                        slug=slug,
                        token_id=token_id,
                        lado_up=lado_up,
                        preco_nosso=preco_nosso,
                        preco_do_print=negocio.preco,
                        shares=shares,
                        tipo=tipo,
                        ts_ns=negocio.ts_ns,
                    )
                )
                novas += 1
                log.info(
                    "cotacao sombra teria executado",
                    slug=slug,
                    lado="up" if lado_up else "down",
                    tipo=tipo,
                    preco_nosso=preco_nosso,
                    preco_do_print=negocio.preco,
                    shares=shares,
                    ts_servidor_ms=getattr(negocio, "ts_servidor_ms", None),
                    atraso_s=_atraso_do_print_s(negocio, agora_ns),
                )
        return novas

    def medir_markout(self, livro_de: Callable[..., OrderBook | None], *, agora_ns: int) -> int:
        """Fecha as execuções possíveis cujo horizonte já passou."""
        if not self._pendentes:
            return 0
        medidas = 0
        restantes: list[ExecucaoPossivel] = []
        for pendente in self._pendentes:
            decorrido_s = (agora_ns - pendente.ts_ns) / 1e9
            if decorrido_s < self.horizonte_markout_s:
                restantes.append(pendente)
                continue
            livro = livro_de(pendente.token_id, agora_ns=agora_ns)
            meio = livro.mid if livro is not None else None
            if meio is None:
                if decorrido_s <= PRAZO_PARA_MEDIR_S:
                    restantes.append(pendente)
                else:
                    self.markout_sem_referencia += 1
                continue
            # Compramos a `preco_nosso`: o meio acima dele é a nosso favor.
            centavos = (meio - pendente.preco_nosso) * 100.0
            self.markout_medidas += 1
            self.markout_soma_centavos_x_shares += centavos * pendente.shares
            self.markout_shares += pendente.shares
            self.markout_soma_horizonte_s += decorrido_s
            medidas += 1
        self._pendentes = restantes
        return medidas

    def esquecer(self, slug: str) -> None:
        """A cotação saiu do livro: a próxima começa a contar do zero."""
        self._ultimo_acerto_epoch.pop(slug, None)
        self._ultimo_print_ns.pop(slug, None)

    # ─────────────────────────────────────────────────────────────── relato
    @property
    def markout_centavos_por_share(self) -> float | None:
        if self.markout_shares <= 0.0:
            return None
        return self.markout_soma_centavos_x_shares / self.markout_shares

    @property
    def custo_de_markout_usdc(self) -> float:
        """Só das execuções MEDIDAS; negativo aqui é custo, positivo é ganho."""
        return self.markout_soma_centavos_x_shares / 100.0

    def resumo(self) -> dict[str, Any]:
        horas = self.segundos_repousando / 3600.0
        markout = self.markout_centavos_por_share
        return {
            "rewards_pro_rata_usdc": round(self.rewards_pro_rata_usdc, 4),
            "rewards_com_captura_usdc": round(self.rewards_com_captura_usdc, 4),
            "fator_de_captura": self.fator_de_captura,
            "rewards_pro_rata_usdc_por_hora_repousando": (
                round(self.rewards_pro_rata_usdc / horas, 4) if horas > 0 else None
            ),
            "segundos_repousando": round(self.segundos_repousando, 1),
            "segundos_pontuando": round(self.segundos_pontuando, 1),
            "acertos": self.acertos,
            "intervalos_truncados": self.intervalos_truncados,
            "prints_vistos": self.prints_vistos,
            "execucoes_possiveis": {
                "atravessadas": self.execucoes_atravessadas,
                "no_nivel": self.execucoes_no_nivel,
                "shares_atravessadas": round(self.shares_atravessadas, 2),
                "shares_no_nivel": round(self.shares_no_nivel, 2),
                "pendentes_de_markout": len(self._pendentes),
            },
            "markout": {
                "medidas": self.markout_medidas,
                "centavos_por_share": round(markout, 4) if markout is not None else None,
                "shares": round(self.markout_shares, 2),
                "horizonte_medio_s": (
                    round(self.markout_soma_horizonte_s / self.markout_medidas, 1)
                    if self.markout_medidas
                    else None
                ),
                "sem_referencia": self.markout_sem_referencia,
                "resultado_usdc": round(self.custo_de_markout_usdc, 4),
            },
            "liquido_pro_rata_usdc": round(
                self.rewards_pro_rata_usdc + self.custo_de_markout_usdc, 4
            ),
            "nota": (
                "O relogio do 4.2. `rewards_*` sao a conta do laco "
                "(`estimar_retorno`, secao 15.3) integrada no tempo em que a "
                "cotacao repousou, sobre o livro de cada passada: pro_rata e o "
                "que o programa paga pelo score; com_captura multiplica pelo "
                "fator do 1.6. `execucoes_possiveis` conta prints SELL no token "
                "da perna a preco <= o nosso: atravessada = passou abaixo "
                "(nossa perna inteira), no_nivel = parou no nosso preco (fila "
                "decide). E limite inferior: o matching Up x Down nao deixa "
                "print no token da perna. `markout` e o meio da passada "
                "seguinte (>= horizonte) contra o nosso preco, sinal de quem "
                "forneceu liquidez. Nada aqui e lucro: e o numero que pode "
                "REPROVAR a rota."
            ),
        }
