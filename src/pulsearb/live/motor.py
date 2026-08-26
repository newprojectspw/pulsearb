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
from pulsearb.engine.decisao import estimar_prob_up
from pulsearb.execution.executor import Executor
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

    def na_faixa(self, seconds_left: float) -> bool:
        if self.tempo_restante_max_s is not None and (
            seconds_left > self.tempo_restante_max_s
        ):
            return False
        return not (
            self.tempo_restante_min_s is not None
            and seconds_left < self.tempo_restante_min_s
        )


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

    def _avaliar(
        self,
        janela: JanelaAoVivo,
        *,
        agora_epoch: float,
        agora_ns: int,
        feeds_saudaveis: bool,
    ) -> bool:
        if janela.condition_id in self.ja_operadas:
            self._pular(PULOU_JA_OPEROU)
            return False

        seconds_left = janela.seconds_left(agora_epoch)
        if not self.config.na_faixa(seconds_left):
            self._pular(PULOU_FORA_DA_FAIXA)
            return False

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

        estimativa = estimar_prob_up(
            jogo=janela.jogo,
            ancora=ancora,
            twap=ativo.twap,
            vol=ativo.vol,
            preco_spot=spot,
            seconds_left=seconds_left,
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
