"""Quais janelas estão abertas AGORA, e quanto tempo falta em cada uma.

O backtest recebe janelas prontas: a gravação já aconteceu, e o índice sabe
onde cada uma começou e terminou. Ao vivo é o contrário — a descoberta devolve
um retrato do instante, janelas de 5 minutos nascem e morrem a cada 5 minutos,
e o bot precisa saber, a cada tick, quais existem e quanto falta em cada uma.

**`seconds_left` é o número mais importante deste módulo.** É ele que escolhe
o balde de calibração, e o M2 mediu que o modelo erra 0,008 na faixa 240–120 s
e 0,240 acima de 240 s — trinta vezes mais. Um `seconds_left` deslocado não
degrada a decisão, ele a toma na faixa errada.

Daí duas escolhas que parecem paranoia e não são:

**Falha fechada em janela sem `endDate` legível.** Sem o fechamento não há
`seconds_left`, e sem `seconds_left` a decisão não tem faixa. A janela sai da
lista em vez de entrar com um palpite.

**A duração vem de `duracao_do_slug`, compartilhada com o backtest.** Se cada
lado tivesse a sua cópia, uma divergência entre SHADOW e backtest pareceria
diferença de mercado quando seria diferença de aritmética — e é justamente a
comparação entre os dois que justifica o SHADOW existir.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pulsearb.engine.decisao import JOGO_HORARIO, JOGO_TWAP
from pulsearb.markets.discovery import (
    DiscoveredMarket,
    duracao_do_slug,
    parse_end_date_epoch,
)
from pulsearb.obs.logging import get_logger

log = get_logger(__name__)

#: Motivos pelos quais uma janela descoberta não entra no rastreador. Como os
#: motivos de recusa do portão, são constantes porque viram contador — e a
#: pergunta "por que o bot não está operando" só tem resposta se cada descarte
#: souber dizer o próprio nome.
DESCARTE_NAO_OPERAVEL = "nao_operavel"
DESCARTE_SEM_FECHAMENTO = "sem_fechamento_legivel"
DESCARTE_SEM_TOKENS = "sem_par_de_tokens"
DESCARTE_JA_FECHADA = "ja_fechada"


@dataclass(frozen=True)
class JanelaAoVivo:
    """Uma janela aberta, com tudo que a decisão e o portão precisam."""

    slug: str
    asset: str
    #: "twap" ou "horario". Os dois jogos sao FISICAMENTE diferentes
    #: (API_NOTES 13.4) e usam modelos diferentes: estimar um com o modelo do
    #: outro nao degrada a previsao, produz outra previsao.
    jogo: str
    condition_id: str
    token_up: str
    token_down: str
    duracao_s: int
    abertura_epoch: float
    fechamento_epoch: float
    tick_size: float
    min_order_size: float
    fee_rate: float
    fee_exponent: float

    def seconds_left(self, agora_epoch: float) -> float:
        return self.fechamento_epoch - agora_epoch

    def esta_aberta(self, agora_epoch: float) -> bool:
        return self.abertura_epoch <= agora_epoch < self.fechamento_epoch


class RastreadorDeJanelas:
    """Retrato vivo das janelas abertas. Alimente com a descoberta, consulte a cada tick.

    Guarda por `condition_id`, não por slug: o slug é derivável e legível, mas
    o `condition_id` é o que a plataforma trata como identidade.
    """

    def __init__(self) -> None:
        self.janelas: dict[str, JanelaAoVivo] = {}
        self.descartes: dict[str, int] = {}

    # ───────────────────────────────────────────────────────────── ingestão
    def atualizar(
        self, mercados: list[DiscoveredMarket], *, agora_epoch: float
    ) -> None:
        """Absorve um ciclo de descoberta. **Não aposenta nada.**

        Achado P1 do Codex no #52, e era violação do contrato que a própria
        `aposentar_fechadas` documenta: ela DEVOLVE as janelas fechadas
        porque quem chama precisa liquidar a exposição delas. Este método
        chamava e **descartava o retorno**.

        A sequência que isso quebrava: a descoberta termina logo depois de uma
        janela operada fechar, mas antes do próximo `tick`. A janela some do
        retrato aqui, o `MotorAoVivo.tick` não a encontra mais, `_liquidar`
        nunca roda, e a exposição sintética fica presa em `gasto_por_janela`.
        Com cinco dessas, o teto de posições recusa toda intenção seguinte
        pelo resto da rodada.

        Quem aposenta é quem liquida — o motor, no `tick`. Uma janela fechada
        pode ficar no retrato por até uma cadência de decisão (1 s), e isso
        não custa nada: `_elegivel` já a recusa por tempo.
        """
        for mercado in mercados:
            janela = self._converter(mercado, agora_epoch=agora_epoch)
            if janela is not None:
                self.janelas[mercado.condition_id] = janela

    def _converter(
        self, mercado: DiscoveredMarket, *, agora_epoch: float
    ) -> JanelaAoVivo | None:
        if not mercado.operable:
            self._descartar(DESCARTE_NAO_OPERAVEL)
            return None

        token_up = mercado.token_id_by_outcome.get("Up")
        token_down = mercado.token_id_by_outcome.get("Down")
        if not token_up or not token_down:
            # Sem os dois lados não dá para operar nem para reconciliar: o
            # backtest sempre pareia Up e Down, e um lado só não é janela.
            self._descartar(DESCARTE_SEM_TOKENS)
            return None

        fechamento = parse_end_date_epoch(mercado.raw_gamma)
        if fechamento is None:
            # Sem fechamento não há `seconds_left`, e sem `seconds_left` a
            # decisão não tem faixa de calibração. Fora.
            self._descartar(DESCARTE_SEM_FECHAMENTO)
            return None

        duracao = duracao_do_slug(mercado.slug)
        janela = JanelaAoVivo(
            slug=mercado.slug,
            asset=mercado.asset,
            jogo=(
                JOGO_HORARIO
                if mercado.resolution == "binance_candle"
                else JOGO_TWAP
            ),
            condition_id=mercado.condition_id,
            token_up=token_up,
            token_down=token_down,
            duracao_s=duracao,
            abertura_epoch=fechamento - duracao,
            fechamento_epoch=fechamento,
            tick_size=mercado.tick_size,
            min_order_size=mercado.min_order_size,
            fee_rate=mercado.fee_rate,
            fee_exponent=mercado.fee_exponent,
        )
        if not janela.esta_aberta(agora_epoch):
            # A descoberta olha uma janela de tempo à frente, então trazer
            # janela futura é o comportamento correto DELA. Aqui ela ainda não
            # serve: entraria com `seconds_left` maior que a própria duração.
            self._descartar(DESCARTE_JA_FECHADA)
            return None
        return janela

    def _descartar(self, motivo: str) -> None:
        self.descartes[motivo] = self.descartes.get(motivo, 0) + 1

    # ───────────────────────────────────────────────────────────── consulta
    def abertas(self, *, agora_epoch: float) -> list[JanelaAoVivo]:
        """As que estão abertas agora, da que fecha primeiro para a última.

        A ordem não é estética: com teto de posições abertas, a janela que
        fecha primeiro é a que devolve capacidade mais cedo.
        """
        return sorted(
            (j for j in self.janelas.values() if j.esta_aberta(agora_epoch)),
            key=lambda j: j.fechamento_epoch,
        )

    def aposentar_fechadas(self, *, agora_epoch: float) -> list[JanelaAoVivo]:
        """Tira do retrato o que já fechou e devolve o que saiu.

        Devolve em vez de só apagar porque quem chama precisa liquidar a
        exposição dessas janelas no portão de risco — janela que fecha e não
        é baixada trava o teto de exposição para sempre.
        """
        fechadas = [
            janela
            for janela in self.janelas.values()
            if agora_epoch >= janela.fechamento_epoch
        ]
        for janela in fechadas:
            del self.janelas[janela.condition_id]
        return fechadas

    def resumo(self, *, agora_epoch: float) -> dict[str, Any]:
        abertas = self.abertas(agora_epoch=agora_epoch)
        return {
            "abertas": len(abertas),
            "rastreadas": len(self.janelas),
            "por_ativo": {
                ativo: sum(1 for j in abertas if j.asset == ativo)
                for ativo in sorted({j.asset for j in abertas})
            },
            "descartes": dict(sorted(self.descartes.items())),
            "nota": (
                "`descartes` acumula desde o inicio e responde a pergunta que "
                "importa quando o bot nao opera: ele nao achou janela, ou "
                "achou e jogou fora? `sem_fechamento_legivel` alto e defeito "
                "de parsing, nao falta de mercado."
            ),
        }
