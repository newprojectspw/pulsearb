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

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pulsearb.analysis.rewards import ParametrosDeReward
from pulsearb.backtest.book import OrderBook
from pulsearb.live.cotacao import (
    FATOR_DE_CAPTURA_PADRAO,
    RetornoEstimado,
    estimar_retorno_repousando,
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
    #: O meio do livro do token NO instante do print. O markout é meio-a-meio
    #: (`meio_depois − meio_no_fill`), como o `medir_markout` da análise — e
    #: não `meio_depois − preco_nosso`, que creditava a distância inicial ao
    #: meio como ganho (mercado parado dava +d ticks). `None` = sem livro no
    #: fill; a execução vai a `markout_sem_referencia`, nunca a um número.
    meio_no_fill: float | None = None


def identidade_do_negocio(token_id: str, negocio: Any) -> tuple | None:
    """O que faz deste negócio ELE, e não outro igual.

    `transaction_hash` vem no `last_trade_price` (§6.1a, `[VERIFICADO]`) e é a
    identidade de verdade — mas **não sozinho**: uma transação pode varrer
    vários níveis e render mais de um evento com o mesmo hash e preços
    diferentes. Desduplicar só pelo hash descartaria a segunda perna de uma
    varredura, que é execução legítima nossa.

    `None` quando o hash não veio: aí não há como reconhecer, e quem não
    reconhece DEIXA PASSAR. Os dois erros não são simétricos — contar um
    print de novo infla o custo (erra contra a rota), descartar um print
    legítimo tira custo (erra a favor dela), e o único dos dois que este
    projeto aceita por omissão é o primeiro.
    """
    transacao = getattr(negocio, "transaction_hash", None)
    if not transacao:
        return None
    return (token_id, transacao, negocio.preco, negocio.tamanho, negocio.lado)


def _quando_o_negocio_aconteceu_ns(negocio: Any) -> int:
    """Quando o negócio ACONTECEU, em ns: o carimbo do servidor se houver,
    senão a chegada.

    A diferença entre os dois é o que separa "alguém negociou agora contra a
    nossa ordem" de "a reassinatura reenviou um negócio antigo" — e só o
    primeiro é motivo para tirar a cotação do livro.
    """
    servidor_ms = getattr(negocio, "ts_servidor_ms", None)
    if servidor_ms is None:
        return int(negocio.ts_ns)
    return int(servidor_ms) * 1_000_000


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


#: A partir daqui o modelo está nos dando o pool praticamente inteiro.
#:
#: 0,9 não é um limiar de decisão — nada recusa por causa dele. É o corte do
#: CONTADOR do relato, e existe para separar "somos uma fatia grande" de
#: "somos o livro todo", que são afirmações muito diferentes sobre quanto do
#: resultado é estratégia e quanto é tamanho de cotação.
FATIA_QUASE_INTEIRA = 0.9


@dataclass
class CaixaDoMaker:
    """As somas do 4.2, por rodada. Sem I/O: quem tem o livro injeta."""

    fator_de_captura: float = FATOR_DE_CAPTURA_PADRAO
    horizonte_markout_s: float = HORIZONTE_DE_MARKOUT_S

    rewards_pro_rata_usdc: float = 0.0
    rewards_com_captura_usdc: float = 0.0
    #: A FATIA, e ela é a premissa que domina o número acima.
    #:
    #: `estimar_retorno` calcula
    #: `daily_rate * (horas/24) * fracao * fator_de_captura`, e o `pro_rata`
    #: usa `fator = 1`. Então o resultado do 4.2 é, na prática, uma função de
    #: `fracao` — a nossa fatia estimada do score do livro.
    #:
    #: E o `cotacao.py` declara no cabeçalho que ela é ESTIMATIVA: o WS
    #: entrega níveis agregados, não ordens, então ninguém aqui sabe a posição
    #: na fila. Cotar 1.000 shares (×200 o default do projeto) contra livros
    #: de pool finos aproxima a fatia de 1 — e aí o número diz que levamos o
    #: pool inteiro de dezenas de mercados ao mesmo tempo.
    #:
    #: Até 2026-09-22 nada disso saía em lugar nenhum: nem no relato, nem no
    #: diário (que grava só a ordem). Catorze dias produziriam um número e
    #: nenhuma forma de conferir de onde ele veio — "número sintético não
    #: fecha item" aplicado ao próprio instrumento do item.
    #:
    #: A média é ponderada pelo MESMO intervalo que ponderou o reward. Média
    #: simples por passada seria outro número, e não o que produziu a conta.
    fracao_ponderada_x_segundos: float = 0.0
    segundos_com_fatia: float = 0.0
    fracao_do_pool_maxima: float = 0.0
    #: Passadas em que a fatia passou de `FATIA_QUASE_INTEIRA`. Contagem, e
    #: não média, porque o que se quer saber é *quantas vezes* o modelo nos
    #: deu o pool inteiro — uma média baixa esconderia um punhado delas.
    passadas_com_fatia_quase_inteira: int = 0
    #: Quanto do reward veio DAQUELAS passadas.
    #:
    #: A contagem sozinha não responde a pergunta que decide o 4.2. Uma
    #: passada com fatia 1,0 num pool de `daily_rate` alto pesa muito mais
    #: que a sua fração na contagem — 12% das passadas podem ser 80% do
    #: resultado. Distribuição da premissa e contribuição dela para o número
    #: são perguntas diferentes, e só a segunda diz se o item mede estratégia
    #: ou mede mercado deserto.
    rewards_de_fatia_quase_inteira: float = 0.0
    segundos_repousando: float = 0.0
    segundos_pontuando: float = 0.0
    acertos: int = 0
    intervalos_truncados: int = 0

    #: Quando cada slug levou o último fill ATRAVESSADO, em ns do print.
    #: Sobrevive ao `esquecer`: a pausa por fill tóxico existe justamente
    #: para valer DEPOIS de a cotação sair do livro.
    ultimo_fill_toxico_ns: dict[str, int] = field(default_factory=dict)
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
    #: Prints que bateriam uma perna JÁ consumida na conta-sombra: ignorados
    #: e contados. Uma perna executada saiu do livro; contá-la de novo a cada
    #: print multiplicava execuções, shares e custo pelo número de prints.
    prints_em_perna_consumida: int = 0
    #: Prints cujo carimbo do SERVIDOR é anterior à cotação: reenvio de
    #: reassinatura, não execução nossa. Contado para o relato mostrar quanto
    #: disso o feed manda — o número nunca tinha sido publicado.
    prints_reenviados: int = 0
    #: Passadas em que as duas pernas já estavam consumidas: tempo que NÃO é
    #: repouso, e reward que NÃO se ganha.
    acertos_apos_execucao: int = 0
    #: Cotação aberta sem o preço enviado de nenhuma perna: não há onde
    #: pontuar. Contado e recusado — não se estima "a d ticks do meio de
    #: agora" no lugar, que foi o defeito consertado aqui.
    acertos_sem_preco: int = 0

    _ultimo_acerto_epoch: dict[str, float] = field(default_factory=dict)
    _ultimo_print_ns: dict[str, int] = field(default_factory=dict)
    #: Identidade dos negócios já contados nesta cotação, por slug. Uma
    #: reconexão reenvia o mesmo `last_trade_price`, e sem isto ele consumia a
    #: perna e registrava markout OUTRA VEZ. Some no `esquecer`: a cotação
    #: seguinte é outra ordem, e um negócio da anterior não a executa.
    _negocios_contados: defaultdict[str, set[tuple]] = field(
        default_factory=lambda: defaultdict(set)
    )
    _pendentes: list[ExecucaoPossivel] = field(default_factory=list)
    #: Quanto de cada perna ainda repousa, por slug: (desde_epoch, [up, down]).
    #: `desde_epoch` diferente = cotação nova (reposicionada) = pernas cheias.
    _restante: dict[str, tuple[float, list[float]]] = field(default_factory=dict)

    def _pernas(self, slug: str, aberta: CotacaoAberta) -> list[float]:
        guardado = self._restante.get(slug)
        if guardado is None or guardado[0] != aberta.desde_epoch:
            cheias = [
                aberta.cotacao.tamanho if aberta.preco_up > 0.0 else 0.0,
                (
                    aberta.cotacao.tamanho
                    if aberta.cotacao.dois_lados and aberta.preco_down > 0.0
                    else 0.0
                ),
            ]
            self._restante[slug] = (aberta.desde_epoch, cheias)
        return self._restante[slug][1]

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
        if intervalo <= 0.0:
            # O relógio nunca anda para trás: um acerto no instante de um
            # print anterior ao último acerto não pode reabrir o intervalo.
            return None
        self._ultimo_acerto_epoch[slug] = agora_epoch
        if intervalo > INTERVALO_MAXIMO_POR_PASSADA_S:
            self.intervalos_truncados += 1
            intervalo = INTERVALO_MAXIMO_POR_PASSADA_S

        if aberta.preco_up <= 0.0 and aberta.preco_down <= 0.0:
            self.acertos_sem_preco += 1
            return None
        pernas = self._pernas(slug, aberta)
        if pernas[0] <= 0.0 and pernas[1] <= 0.0:
            # As duas pernas já executaram na conta-sombra: a ordem não está
            # mais no livro. Nem repouso, nem reward — só a contagem.
            self.acertos_apos_execucao += 1
            return None
        # No preço em que a ordem REPOUSA, não a `distancia_ticks` do meio de
        # agora — uma ordem que o meio deixou para trás não pontua, e é isso
        # que tem de aparecer aqui (revisão do #118).
        estimado = estimar_retorno_repousando(
            aberta,
            livro,
            params,
            horas=intervalo / 3600.0,
            fator_de_captura=self.fator_de_captura,
            restante_up=pernas[0],
            restante_down=pernas[1],
        )
        self.acertos += 1
        self.segundos_repousando += intervalo
        if estimado is None:
            return None
        if estimado.pontua:
            self.segundos_pontuando += intervalo
        self.rewards_com_captura_usdc += estimado.rewards_usdc
        pro_rata_da_passada = 0.0
        if self.fator_de_captura > 0.0:
        self.fracao_ponderada_x_segundos += estimado.fracao_do_pool * intervalo
        self.segundos_com_fatia += intervalo
        self.fracao_do_pool_maxima = max(
            self.fracao_do_pool_maxima, estimado.fracao_do_pool
        )
        if estimado.fracao_do_pool >= FATIA_QUASE_INTEIRA:
            self.passadas_com_fatia_quase_inteira += 1
            self.rewards_de_fatia_quase_inteira += pro_rata_da_passada
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
        livro_de: Callable[..., OrderBook | None] | None = None,
        params: ParametrosDeReward | None = None,
    ) -> int:
        """Olha os prints desde a última passada e anota os que nos pegariam.

        Cada perna é CONSUMIDA pelo que executa: atravessada leva o que resta
        dela, no nível leva o mínimo entre o print e o que resta. Perna
        consumida não executa de novo — a ordem saiu do livro — e não ganha
        mais reward (`acertar` lê o restante). `livro_de`, quando vem, dá o
        meio do token no instante do print, para o markout meio-a-meio.

        Com `params`, o reward é acertado NO INSTANTE do print, com as pernas
        de ANTES dele: senão o acerto seguinte aplicaria o restante de depois
        ao intervalo inteiro, e um print no meio de 10 s faria os 10 s
        parecerem de um lado só (revisão do Codex, #126). O livro é o de
        agora — o de então não existe mais — e é o do Up, ou o espelho do
        Down se só ele estiver à mão.
        """
        desde_ns = max(
            self._ultimo_print_ns.get(slug, 0), int(aberta.desde_epoch * 1e9)
        )
        self._ultimo_print_ns[slug] = agora_ns
        restante = self._pernas(slug, aberta)
        pernas = (
            (0, token_up, True, aberta.preco_up),
            (1, token_down, False, aberta.preco_down),
        )
        novas = 0
        for negocio, indice, token_id, lado_up, preco_nosso in _prints_em_ordem(
            pernas, negocios_desde, desde_ns=desde_ns
        ):
            self.prints_vistos += 1
            if negocio.lado != "SELL" or negocio.preco > preco_nosso + EPS:
                continue
            if _quando_o_negocio_aconteceu_ns(negocio) < int(aberta.desde_epoch * 1e9):
                # O negócio aconteceu ANTES de esta cotação entrar no livro:
                # ela não estava lá para ser executada. O filtro de cima é
                # pela CHEGADA, e uma reassinatura reenvia `last_trade_price`
                # antigo — que entrava aqui como execução nossa, consumia a
                # perna e fazia o fill seguinte, real, ser descartado como
                # perna consumida (revisão do Codex, #131).
                self.prints_reenviados += 1
                continue
            identidade = identidade_do_negocio(token_id, negocio)
            if identidade is not None and identidade in self._negocios_contados[slug]:
                # MESMO negócio, chegando de novo. Uma reconexão reenvia o
                # `last_trade_price` de DEPOIS da colocação: carimbo de
                # chegada novo, mesmo `transaction_hash` e mesmo preço — o
                # filtro de cima (que olha o carimbo do SERVIDOR contra a
                # colocação) deixa passar, porque este negócio de fato
                # aconteceu com a cotação no livro. Sem identidade ele
                # consumia a perna OUTRA VEZ e registrava markout em dobro,
                # e ao longo de 14 dias cada reconexão inflava execuções e
                # custo (revisão do Codex, #131).
                self.prints_reenviados += 1
                continue
            if restante[indice] <= 0.0:
                self.prints_em_perna_consumida += 1
                continue
            if identidade is not None:
                self._negocios_contados[slug].add(identidade)
            tipo, shares = self._classificar(negocio, restante[indice], preco_nosso)
            if tipo == "atravessada":
                # O relógio da pausa por fill tóxico (o regime EVENT do
                # `poly-maker`, disparado pelo NOSSO fill e não pelo salto).
                # Guardado sempre, custe ou não — quem decide se pausa é o
                # laço, e a caixa não sabe de regra de operação.
                #
                # Pelo carimbo do SERVIDOR quando ele existe: `ts_ns` é a
                # CHEGADA, e um `last_trade_price` reenviado depois de
                # reassinatura chega agora carregando um negócio de minutos
                # atrás. Armar a pausa por ele tiraria do livro uma cotação
                # contra a qual ninguém negociou (revisão do Codex, #131).
                # Sem carimbo do servidor vale a chegada, que é o que há.
                self.ultimo_fill_toxico_ns[slug] = max(
                    self.ultimo_fill_toxico_ns.get(slug, 0),
                    _quando_o_negocio_aconteceu_ns(negocio),
                )
            if params is not None and livro_de is not None:
                self._acertar_no_print(
                    slug, aberta, params,
                    token_up=token_up, token_down=token_down,
                    livro_de=livro_de, agora_ns=agora_ns, ts_ns=negocio.ts_ns,
                )
            restante[indice] -= shares
            meio_no_fill = _meio_do_fill(livro_de, token_id, agora_ns=agora_ns)
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
                    meio_no_fill=meio_no_fill,
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
            if pendente.meio_no_fill is None:
                # Sem o meio no instante do fill não há de onde medir. Contado,
                # nunca aproximado pelo nosso preço — isso creditava a
                # distância ao meio como ganho.
                self.markout_sem_referencia += 1
                continue
            livro = livro_de(pendente.token_id, agora_ns=agora_ns)
            meio = livro.mid if livro is not None else None
            if meio is None:
                if decorrido_s <= PRAZO_PARA_MEDIR_S:
                    restantes.append(pendente)
                else:
                    self.markout_sem_referencia += 1
                continue
            # Compramos: o meio subindo depois do fill é a nosso favor. Mede-se
            # do meio NO fill ao meio de agora — a mesma conta do
            # `medir_markout` da análise (quem forneceu a liquidez).
            centavos = (meio - pendente.meio_no_fill) * 100.0
            self.markout_medidas += 1
            self.markout_soma_centavos_x_shares += centavos * pendente.shares
            self.markout_shares += pendente.shares
            self.markout_soma_horizonte_s += decorrido_s
            medidas += 1
        self._pendentes = restantes
        return medidas

    def _classificar(
        self, negocio: Negocio, restante: float, preco_nosso: float
    ) -> tuple[str, float]:
        """Como este print nos pegaria, e por quanto — contando a execução.

        Print ABAIXO do nosso preço atravessou o nível: leva o que resta da
        perna. Print NO nosso preço leva o mínimo entre ele e o que resta —
        não dá para supor que a fila inteira era nossa.
        """
        if negocio.preco < preco_nosso - EPS:
            shares = restante
            self.execucoes_atravessadas += 1
            self.shares_atravessadas += shares
            return "atravessada", shares
        shares = min(negocio.tamanho, restante)
        self.execucoes_no_nivel += 1
        self.shares_no_nivel += shares
        return "no_nivel", shares

    def _acertar_no_print(
        self,
        slug: str,
        aberta: CotacaoAberta,
        params: ParametrosDeReward,
        *,
        token_up: str,
        token_down: str,
        livro_de: Callable[..., OrderBook | None],
        agora_ns: int,
        ts_ns: int,
    ) -> None:
        livro_up = livro_de(token_up, agora_ns=agora_ns)
        if livro_up is None:
            livro_down = livro_de(token_down, agora_ns=agora_ns)
            if livro_down is None:
                return
            livro_up = espelho_do_livro(livro_down, asset_id=token_up)
        self.acertar(slug, aberta, livro_up, params, agora_epoch=ts_ns / 1e9)

    def esquecer(self, slug: str) -> None:
        """A cotação saiu do livro: a próxima começa a contar do zero."""
        self._ultimo_acerto_epoch.pop(slug, None)
        self._ultimo_print_ns.pop(slug, None)
        self._restante.pop(slug, None)
        self._negocios_contados.pop(slug, None)

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
            "fracao_do_pool": {
                "media_ponderada": (
                    round(self.fracao_ponderada_x_segundos / self.segundos_com_fatia, 4)
                    if self.segundos_com_fatia > 0
                    else None
                ),
                "maxima": round(self.fracao_do_pool_maxima, 4),
                "passadas_quase_inteiras": self.passadas_com_fatia_quase_inteira,
                "rewards_dessas_passadas": round(
                    self.rewards_de_fatia_quase_inteira, 4
                ),
                "fracao_do_total_que_vem_delas": (
                    round(
                        self.rewards_de_fatia_quase_inteira
                        / self.rewards_pro_rata_usdc,
                        4,
                    )
                    if self.rewards_pro_rata_usdc > 0
                    else None
                ),
                "nota": (
                    "A PREMISSA do numero acima. `rewards` e "
                    "`daily_rate * horas/24 * fracao`, entao o resultado do "
                    "4.2 e uma funcao desta fatia — e ela e ESTIMATIVA: o WS "
                    "da niveis agregados, nao ordens, e ninguem aqui sabe a "
                    "posicao na fila (`cotacao.py`). `media_ponderada` perto "
                    "de 1 quer dizer que o modelo nos atribui o pool inteiro, "
                    "e ai o numero mede o TAMANHO da cotacao, nao a "
                    "estrategia. Media ponderada pelo mesmo intervalo que "
                    "ponderou o reward; `maxima` e o pior caso para a "
                ),
            },
            "segundos_repousando": round(self.segundos_repousando, 1),
            "segundos_pontuando": round(self.segundos_pontuando, 1),
            "acertos": self.acertos,
            "acertos_apos_execucao": self.acertos_apos_execucao,
            "acertos_sem_preco": self.acertos_sem_preco,
            "intervalos_truncados": self.intervalos_truncados,
            "prints_vistos": self.prints_vistos,
            "prints_em_perna_consumida": self.prints_em_perna_consumida,
            "execucoes_possiveis": {
                "reenviados": self.prints_reenviados,
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


def espelho_do_livro(livro: OrderBook, *, asset_id: str) -> OrderBook:
    """O livro do Up visto pelo do Down: bid do Up = 1 − ask do Down, ask do
    Up = 1 − bid do Down. Os asks do Down sobem, logo os bids do espelho
    descem — a ordem que `OrderBook` exige sai de graça."""
    return OrderBook(
        asset_id=asset_id,
        bids=[(1.0 - preco, tamanho) for preco, tamanho in livro.asks],
        asks=[(1.0 - preco, tamanho) for preco, tamanho in livro.bids],
        ts_ns=livro.ts_ns,
    )


def _meio_do_fill(
    livro_de: Callable[..., OrderBook | None] | None, token_id: str, *, agora_ns: int
) -> float | None:
    """O meio do token no instante do fill, para o markout ser meio-a-meio.

    `None` sem livro à mão — e sem ele a execução é contada mas NÃO medida
    (`markout_sem_referencia`), nunca aproximada pelo nosso preço, o que
    creditaria a distância ao meio como ganho.
    """
    if livro_de is None:
        return None
    livro = livro_de(token_id, agora_ns=agora_ns)
    return livro.mid if livro is not None else None


def _prints_em_ordem(
    pernas: tuple[tuple[int, str, bool, float], ...],
    negocios_desde: Callable[..., list[Negocio]],
    *,
    desde_ns: int,
) -> list[tuple[Negocio, int, str, bool, float]]:
    """Os prints das DUAS pernas juntos, em ordem de tempo.

    Perna a perna, um print do Down anterior ao do Up seria visto DEPOIS dele:
    o relógio do acerto já teria avançado, e o Down pareceria aberto até o
    print do Up (revisão do Codex, #126). A ordenação é estável, então prints
    do mesmo instante preservam a ordem de chegada.
    """
    return sorted(
        (
            (negocio, indice, token_id, lado_up, preco_nosso)
            for indice, token_id, lado_up, preco_nosso in pernas
            if preco_nosso > 0.0
            for negocio in negocios_desde(token_id, ts_ns=desde_ns)
        ),
        key=lambda item: item[0].ts_ns,
    )
