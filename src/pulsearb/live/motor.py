"""O laço: junta janelas, livros e preços, chama o modelo, entrega ao executor.

Este é o pedaço que faz o SHADOW valer alguma coisa, e a regra que o governa é
uma só: **cada etapa reusa o que o backtest usa.**

| Etapa | De onde vem | Por que compartilhado |
|---|---|---|
| duração da janela | `markets.discovery.duracao_do_slug` | desloca `seconds_left` inteiro |
| âncora | `analysis.anchor_sweep.StreamE18.em` | é a definição verificada em 640 janelas |
| probabilidade | `engine.decisao.estimar_prob_up` | os dois jogos escolhidos igual |
| edge | `backtest.runner.edge_liquido` | mesma conta de taxa por share |
| livro e profundidade | `backtest.book.OrderBook` | mesma medida dos 87,8 USDC do 1.5 |
| portões | `risk.PortaoDeRisco.avaliar_risco` | mesmos tetos |

O que sobra de novo aqui é só a **orquestração**: quem pergunta o quê, em que
ordem, e o que fazer quando falta uma peça. E quando falta uma peça, a resposta
é sempre a mesma — não opera, e conta o motivo.

**Uma entrada por janela**, como no backtest (`max_entradas_por_janela=1`). O
M2.7 mediu que subir isso aumenta PnL e drawdown na mesma proporção: é
alavancagem, não borda. O default fica em 1 e mudar exige número, não intuição.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pulsearb.backtest.runner import edge_liquido
from pulsearb.engine.decisao import JOGO_TWAP, estimar_prob_up
from pulsearb.engine.fees import fee_pp_por_share
from pulsearb.execution.executor import Executor
from pulsearb.feeds.poly_ws import Resolucao, normalizar_condition_id
from pulsearb.live.livros import LivrosAoVivo
from pulsearb.live.precos import PrecosAoVivo
from pulsearb.live.rastreador import JanelaAoVivo, RastreadorDeJanelas
from pulsearb.obs.logging import get_logger
from pulsearb.risk import OrdemPretendida

log = get_logger(__name__)

#: Por que uma janela aberta não virou nem sequer uma tentativa. Nomeados
#: porque a pergunta operacional — "o bot está vivo e não opera, por quê?" —
#: só tem resposta se cada porta fechada souber se identificar.
PULOU_SEM_ANCORA = "sem_ancora"
PULOU_SEM_PRECO = "sem_preco_do_ativo"
PULOU_VOL_CRUA = "volatilidade_nao_calibrada"
PULOU_SEM_LIVRO = "sem_livro_confiavel"
PULOU_SEM_EDGE = "edge_abaixo_do_threshold"
PULOU_JA_OPEROU = "ja_operou_nesta_janela"
PULOU_FORA_DA_FAIXA = "fora_da_faixa_de_tempo"
#: Configurado para a variância MEDIDA e não há curva para este ativo, ou a
#: janela é de um jogo que a curva não cobre.
#:
#: Falha fechada, e não queda para o modelo derivado: os dois diferem por 39 a
#: 48 vezes na variância (§2d-ter). O SHADOW existe para comparar com o
#: backtest; se ele decidisse por outra física, a divergência apareceria como
#: diferença de mercado quando seria diferença de modelo — que é exatamente o
#: que a regra do "mesmo caminho" existe para impedir.
PULOU_SEM_CURVA = "sem_curva_de_variancia"
#: O instante pede horizonte maior que o maior MEDIDO na curva.
#:
#: Acontece nos primeiros minutos de uma janela de 15 min ou de 4 h. Não é
#: defeito: é a curva sendo honesta sobre onde ela foi medida. O backtest
#: recorta no mesmo ponto, e recortar em lugares diferentes faria os dois
#: divergirem por construção.
PULOU_ALEM_DA_CURVA = "alem_do_horizonte_medido"
#: A janela é de um jogo que este processo não está equipado para operar.
#:
#: O jogo HORÁRIO resolve pelo candle 1h da Binance, e a âncora dele é o campo
#: `o` do `kline_1h` (`engine/hourly.py`) — não o stream `twap_sixty`. Um
#: processo que só assina RTDS não tem essa série, e `estimar_prob_up` cairia
#: em `prob_up_hourly` com a âncora do observável errado: toda probabilidade
#: horária sairia de uma série que não é a que resolve a janela.
#:
#: Falha fechada em vez de operar errado. Sai desta lista quando o feed da
#: Binance estiver ligado e roteado — e não antes.
PULOU_JOGO_NAO_OPERADO = "jogo_sem_feed_proprio"
#: `shares_por_trade` abaixo do mínimo que o mercado aceita.
#:
#: O backtest já recusa isto (`sinais_abaixo_do_minimo`). Sem a mesma recusa
#: aqui, o SHADOW registraria `pode=true` para uma ordem que a corretora
#: rejeitaria, e a população dele divergiria da do backtest.
PULOU_ABAIXO_DO_MINIMO = "abaixo_do_minimo_do_mercado"


@dataclass
class ConfigDoMotor:
    """Os mesmos parâmetros do `BacktestConfig` que afetam a decisão."""

    threshold_edge: float = 0.02
    shares_por_trade: float = 5.0
    buffer_slippage: float = 0.0
    #: Faixa de tempo restante em que se opera. `None` = sem restrição.
    #: O M2 mediu erro de calibração de 0,008 em 240–120 s contra 0,240 acima
    #: de 240 s — trinta vezes. Operar fora da faixa calibrada é operar onde o
    #: modelo não sabe.
    tempo_restante_max_s: float | None = 240.0
    tempo_restante_min_s: float | None = None
    #: Que jogos este processo opera. Default: só o TWAP.
    #:
    #: É o único cuja âncora foi VERIFICADA (§13.8, τ=0 em 152 janelas) e o
    #: único cujo feed este processo assina. Ampliar exige ligar o feed do
    #: jogo novo primeiro.
    jogos_operados: frozenset[str] = field(
        default_factory=lambda: frozenset({JOGO_TWAP})
    )
    #: As curvas V(t) medidas, por ativo. `None` = o modelo derivado.
    #:
    #: TEM de casar com o que o backtest usou. Rodar o SHADOW no derivado
    #: depois de validar a estratégia no medido recria ao vivo a diferença de
    #: 39 a 48× que a §2d-ter mediu — e o diário do shadow atribuiria a
    #: divergência ao mercado.
    curvas_de_variancia: Any = None

    def na_faixa(self, seconds_left: float) -> bool:
        if self.tempo_restante_max_s is not None and (
            seconds_left > self.tempo_restante_max_s
        ):
            return False
        return not (
            self.tempo_restante_min_s is not None
            and seconds_left < self.tempo_restante_min_s
        )


def _chave(condition_id: str) -> str:
    """A grafia comparável do condition id.

    A Gamma, o CLOB e o WS não prometem a mesma: `0xABE6…` e `abe6…` são o
    mesmo mercado. Comparar sem normalizar falha em SILÊNCIO — que é o modo
    de falha que `normalizar_condition_id` foi escrita para eliminar.
    """
    return normalizar_condition_id(condition_id) or condition_id


@dataclass(frozen=True)
class PosicaoAberta:
    """O que se comprou numa janela, guardado até a resolução chegar.

    Existe porque o PnL só se conhece depois do fechamento, e sem memória do
    lado e do preço não há como convertê-lo — o disjuntor ficaria cego.
    """

    slug: str
    token_up: str
    token_down: str
    lado_up: bool
    shares: float
    preco_pago: float
    fee_usdc: float

    def pnl_usdc(self, *, venceu_up: bool) -> float:
        """A MESMA conta do backtest (`report.py: Trade.pnl_usdc`).

        Share vencedora paga 1,00; perdedora paga 0. Duas contas diferentes
        fariam o SHADOW e o backtest discordarem sobre o próprio resultado.
        """
        acertou = self.lado_up == venceu_up
        payout = self.shares if acertou else 0.0
        return payout - self.shares * self.preco_pago - self.fee_usdc


@dataclass
class MotorAoVivo:
    """Um `tick()` por vez. Sem rede, sem asyncio: só decisão.

    Manter o motor síncrono e sem I/O é o que o torna testável no tempo que se
    quiser — dá para simular seis horas de mercado num teste sem esperar seis
    horas, e sem fingir rede.
    """

    rastreador: RastreadorDeJanelas
    livros: LivrosAoVivo
    precos: PrecosAoVivo
    executor: Executor
    config: ConfigDoMotor = field(default_factory=ConfigDoMotor)

    #: Janelas em que já se entrou, por `condition_id`.
    ja_operadas: set[str] = field(default_factory=set)
    #: A posição de cada janela operada, até a resolução chegar. Ver
    #: `resolver`: sem isto o disjuntor do SHADOW nunca arma.
    #:
    #: **A chave é o condition id NORMALIZADO**, e a distinção é a mesma que
    #: `normalizar_condition_id` existe para impedir: a Gamma entrega `0xAA…`
    #: e o WS entrega `aa…`. Indexar pela grafia crua fazia toda resolução
    #: cair em `resolucoes_sem_posicao` — em silêncio, e com o disjuntor
    #: parado em zero exatamente como antes do conserto.
    posicoes: dict[str, PosicaoAberta] = field(default_factory=dict)
    #: Resoluções que chegaram para janela que não operamos. Contador, não
    #: erro: assinamos o livro de janela recusada de propósito.
    resolucoes_sem_posicao: int = 0
    pulos: dict[str, int] = field(default_factory=dict)
    tentativas: int = 0

    def tick(
        self, *, agora_epoch: float, agora_ns: int, feeds_saudaveis: bool
    ) -> int:
        """Avalia todas as janelas abertas. Devolve quantas viraram tentativa.

        Aposenta as fechadas ANTES de decidir: janela que fechou e não foi
        baixada trava o teto de exposição, e o portão passaria a recusar tudo
        com `exposicao_no_teto` sem que nada estivesse errado no mercado.
        """
        for fechada in self.rastreador.aposentar_fechadas(agora_epoch=agora_epoch):
            self._liquidar(fechada)

        tentadas = 0
        for janela in self.rastreador.abertas(agora_epoch=agora_epoch):
            if self._avaliar(
                janela,
                agora_epoch=agora_epoch,
                agora_ns=agora_ns,
                feeds_saudaveis=feeds_saudaveis,
            ):
                tentadas += 1
        return tentadas

    def _liquidar(self, janela: JanelaAoVivo) -> None:
        """Solta a exposição e a âncora de uma janela que fechou.

        O PnL real só se conhece quando a resolução chega; aqui a baixa é de
        EXPOSIÇÃO, para o capital voltar a ficar disponível. Registrar PnL
        adivinhado aqui alimentaria o disjuntor com número inventado.
        """
        self.executor.portao.registrar_resolucao(janela.slug, 0.0)
        self.precos.esquecer(janela.condition_id)
        self.ja_operadas.discard(janela.condition_id)
        # `posicoes` NÃO é limpo aqui: a resolução chega DEPOIS do fechamento,
        # e é ela que traz o PnL. Apagar aqui seria apagar a única memória de
        # que houve posição — ver `resolver`.

    def resolver(self, resolucao: Resolucao) -> bool:
        """A resolução chegou: converte a posição em PnL e alimenta o portão.

        Achado P1 do Codex no #52, e era um buraco no ensaio inteiro.
        `_liquidar` fecha toda janela com `pnl=0.0` — correto, porque no
        fechamento o resultado ainda não se conhece. Só que **nada** chamava
        `registrar_resolucao` com o PnL de verdade depois: `perdas_seguidas` e
        `pnl_realizado_usdc` ficavam parados em zero para sempre.

        Consequência: a pausa por sequência de perdas e o disjuntor de perda
        do dia **nunca armavam no SHADOW**. Depois das primeiras entradas
        perdedoras, o ensaio aprovaria intenções que o estado de risco
        equivalente em LIVE já teria recusado — e o ensaio existe justamente
        para dizer o que o LIVE faria.

        A conta é a MESMA do backtest (`report.py: Trade.pnl_usdc`):
        `payout − custo − fee`, com a share vencedora pagando 1,00. Duas
        contas diferentes fariam o SHADOW e o backtest discordarem sobre o
        próprio resultado.

        Chamar duas vezes para a mesma janela é seguro: a posição sai de
        `posicoes` na primeira.
        """
        condition_id = resolucao.condition_id
        if condition_id is None:
            self.resolucoes_sem_posicao += 1
            return False

        posicao = self.posicoes.pop(condition_id, None)
        if posicao is None:
            # Assinamos o livro de janela recusada de propósito, então
            # resolução sem posição é o caso NORMAL, não um erro.
            self.resolucoes_sem_posicao += 1
            return False

        venceu_up = resolucao.venceu_up(posicao.token_up, posicao.token_down)
        if venceu_up is None:
            # Evento que não permite decidir o lado. Devolver a posição para
            # `posicoes` deixa a próxima resolução (ou a consulta à Gamma)
            # ainda poder liquidá-la — perder o PnL seria pior.
            self.posicoes[condition_id] = posicao
            self.resolucoes_sem_posicao += 1
            return False

        self.executor.portao.registrar_resolucao(
            posicao.slug, posicao.pnl_usdc(venceu_up=venceu_up)
        )
        return True

    def _elegivel(self, janela: JanelaAoVivo, *, agora_epoch: float) -> bool:
        """Esta janela merece ser avaliada? Cada não já sai contado.

        São as perguntas que não dependem de preço nem de livro — respondê-las
        antes evita gastar modelo com janela que nunca viraria ordem, e mantém
        `pulos` respondendo à pergunta operacional de sempre: o bot está vivo e
        não opera, por quê?
        """
        if janela.condition_id in self.ja_operadas:
            self._pular(PULOU_JA_OPEROU)
            return False

        if janela.jogo not in self.config.jogos_operados:
            self._pular(PULOU_JOGO_NAO_OPERADO)
            return False

        if self.config.shares_por_trade < janela.min_order_size:
            # Mesma recusa do backtest (`sinais_abaixo_do_minimo`). Registrar
            # intenção que a corretora rejeitaria encheria o diário de linhas
            # que nunca virariam ordem.
            self._pular(PULOU_ABAIXO_DO_MINIMO)
            return False

        if not self.config.na_faixa(janela.seconds_left(agora_epoch)):
            self._pular(PULOU_FORA_DA_FAIXA)
            return False

        return True

    def _avaliar(
        self,
        janela: JanelaAoVivo,
        *,
        agora_epoch: float,
        agora_ns: int,
        feeds_saudaveis: bool,
    ) -> bool:
        if not self._elegivel(janela, agora_epoch=agora_epoch):
            return False

        seconds_left = janela.seconds_left(agora_epoch)

        ancora = self.precos.ancora_da_janela(
            asset=janela.asset,
            condition_id=janela.condition_id,
            abertura_epoch=janela.abertura_epoch,
        )
        if ancora is None:
            self._pular(PULOU_SEM_ANCORA)
            return False

        ativo = self.precos.por_ativo.get(janela.asset)
        spot = ativo.twap.last_price if ativo else None
        if ativo is None or spot is None or spot <= 0:
            self._pular(PULOU_SEM_PRECO)
            return False

        curva = None
        if self.config.curvas_de_variancia is not None:
            if janela.jogo != JOGO_TWAP:
                self._pular(PULOU_SEM_CURVA)
                return False
            curva = self.config.curvas_de_variancia.para(janela.asset)
            if curva is None:
                self._pular(PULOU_SEM_CURVA)
                return False
            # Além do maior horizonte MEDIDO a curva extrapola. O backtest
            # recorta no mesmo ponto, por instante; recortar em lugares
            # diferentes faria SHADOW e backtest divergirem por construção —
            # que é o que a regra do "mesmo caminho" existe para impedir.
            if seconds_left > curva.horizonte_maximo_s:
                self._pular(PULOU_ALEM_DA_CURVA)
                return False

        estimativa = estimar_prob_up(
            jogo=janela.jogo,
            ancora=ancora,
            twap=ativo.twap,
            vol=ativo.vol,
            preco_spot=spot,
            seconds_left=seconds_left,
            curva=curva,
        )
        if not estimativa.confiavel:
            # `vol_ready=False`: menos de 20 retornos observados. O modelo
            # devolve probabilidade, mas ela não descreve nada ainda.
            self._pular(PULOU_VOL_CRUA)
            return False

        return self._tentar(
            janela,
            prob_up=estimativa.prob_up,
            seconds_left=seconds_left,
            agora_ns=agora_ns,
            feeds_saudaveis=feeds_saudaveis,
        )

    def _tentar(
        self,
        janela: JanelaAoVivo,
        *,
        prob_up: float,
        seconds_left: float,
        agora_ns: int,
        feeds_saudaveis: bool,
    ) -> bool:
        """Os dois lados, na mesma ordem do backtest: Up antes de Down."""
        sem_livro = True
        for lado_up, token, prob in (
            (True, janela.token_up, prob_up),
            (False, janela.token_down, 1.0 - prob_up),
        ):
            livro = self.livros.livro(token, agora_ns=agora_ns)
            if livro is None or livro.best_ask is None:
                continue
            sem_livro = False

            edge = edge_liquido(
                prob=prob,
                preco=livro.best_ask,
                fee_rate=janela.fee_rate,
                fee_exponent=janela.fee_exponent,
                buffer=self.config.buffer_slippage,
            )
            if edge < self.config.threshold_edge:
                continue

            ordem = OrdemPretendida(
                slug=janela.slug,
                token_id=token,
                lado_up=lado_up,
                shares=self.config.shares_por_trade,
                preco_limite=livro.best_ask,
            )
            decisao = self.executor.executar(
                ordem,
                feeds_saudaveis=feeds_saudaveis,
                prob_prevista=prob,
                seconds_left=seconds_left,
                ts_ns=agora_ns,
                melhor_bid=livro.best_bid,
                melhor_ask=livro.best_ask,
                profundidade_no_topo=livro.depth_usdc(
                    side="ask", ticks=3, tick_size=janela.tick_size
                ),
            )
            self.tentativas += 1
            if decisao.pode:
                # Uma entrada por janela: o M2.7 mediu que mais entradas
                # sobem PnL e drawdown na mesma proporção — alavancagem, não
                # borda.
                self.ja_operadas.add(janela.condition_id)
                self.posicoes[_chave(janela.condition_id)] = PosicaoAberta(
                    slug=janela.slug,
                    token_up=janela.token_up,
                    token_down=janela.token_down,
                    lado_up=lado_up,
                    shares=ordem.shares,
                    preco_pago=livro.best_ask,
                    fee_usdc=ordem.shares
                    * fee_pp_por_share(
                        livro.best_ask,
                        rate=janela.fee_rate,
                        exponent=janela.fee_exponent,
                    ),
                )
            return True

        self._pular(PULOU_SEM_LIVRO if sem_livro else PULOU_SEM_EDGE)
        return False

    def _pular(self, motivo: str) -> None:
        self.pulos[motivo] = self.pulos.get(motivo, 0) + 1

    def resumo(self, *, agora_epoch: float, agora_ns: int) -> dict[str, Any]:
        """Um retrato só, para responder 'o bot está vivo e não opera, por quê?'."""
        return {
            "tentativas": self.tentativas,
            "janelas_ja_operadas": len(self.ja_operadas),
            "pulos": dict(sorted(self.pulos.items())),
            "janelas": self.rastreador.resumo(agora_epoch=agora_epoch),
            "livros": self.livros.resumo(agora_ns=agora_ns),
            "precos": self.precos.resumo(),
            "nota": (
                "Leia `pulos` de cima para baixo antes de suspeitar do "
                "modelo. `sem_ancora` logo apos subir e esperado; "
                "`volatilidade_nao_calibrada` some depois de 20 retornos; "
                "`sem_livro_confiavel` alto com o feed do CLOB conectado quer "
                "dizer token mudo, nao mercado parado; `fora_da_faixa_de_tempo` "
                "alto e o gatilho chegando cedo demais, que foi o BUG 2 do "
                "M2.6. So depois de todos esses zerarem e que "
                "`edge_abaixo_do_threshold` fala sobre a borda."
            ),
        }
