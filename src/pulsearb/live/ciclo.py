"""O ciclo ao vivo: fio → estado → decisão. É o que faltava para o SHADOW.

O `ESTADO_PARA_LIVE` dizia, no item 3.13: *"o executor existe e está testado,
mas não há ciclo de decisão ao vivo para alimentá-lo"*. As peças estavam todas
prontas — `rastreador`, `livros`, `precos`, `motor`, `executor`, `gates` — e o
que faltava era **a orquestração**: quem recebe o evento do fio, para onde ele
vai, e quando o motor decide.

## A regra que governa este arquivo

**Nenhum parser novo.** Cada evento é lido pela MESMA função que o backtest
usa: `parse_rtds_event` e `e18_do_evento` para preço, `LivrosAoVivo.aplicar`
para livro (que por dentro é o mesmo `OrderBook` do critério 1.5). Se o SHADOW
lesse o fio por outro caminho, uma divergência de parsing entre ele e o
backtest apareceria como diferença de mercado — e é justamente a comparação
entre os dois que justifica o SHADOW existir.

## Só `twap_sixty` alimenta o preço

O backtest alimenta `streams_e18` apenas com `TOPIC_TWAP_60`; `crypto_prices`
(spot Binance) chega pelo mesmo fio e NÃO entra. A âncora verificada (§13.8) é
definida sobre esse stream, e misturar os dois moveria a âncora para um
observável que nunca foi validado.

## Sem rede aqui dentro

`CicloAoVivo` não abre socket, não faz HTTP e não dorme. Ele recebe
`FeedEvent`s de quem os tiver — o WS de verdade, ou uma **reprodução de
gravação**. Essa segunda porta é o ponto: é ela que permite rodar SHADOW e
backtest sobre o MESMO dado e comparar as duas decisões. Um ciclo que só
soubesse falar com a rede não poderia ser confrontado com nada.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from pulsearb.feeds.base import FeedEvent
from pulsearb.feeds.poly_ws import eventos_do_payload, resolucao_do_evento
from pulsearb.feeds.rtds import TOPIC_TWAP_60, e18_do_evento, parse_rtds_event
from pulsearb.live.motor import MotorAoVivo
from pulsearb.markets.discovery import DiscoveredMarket

#: Silêncio do feed-verdade a partir do qual ele conta como PARADO.
#:
#: A cadência medida é de 1,061 s por tick por ativo (M2.10). Dez segundos são
#: ~9 ticks perdidos: já não é jitter. É o mesmo número do `IDADE_MAXIMA_MS` do
#: sensor de relógio, de propósito — dois limiares diferentes para "o feed
#: parou" fariam o diário dizer que o relógio está velho e o feed está bom no
#: mesmo instante.
SILENCIO_DO_PRECO_S = 10.0

#: Fontes que o ciclo entende. Evento de outra fonte é contado e ignorado —
#: contado porque "não chegou nada" e "chegou algo que não sei ler" têm
#: consertos opostos, e foi essa distinção que faltou no `price_change` (§6.1b).
FONTE_RTDS = "rtds"
FONTE_POLY = "poly_ws"

#: Quantos carimbos recentes lembrar por ativo, para deduplicar.
#:
#: O RTDS é assinado em N conexões redundantes (`rtds_conexoes`, default 2)
#: porque conexão individual já produziu lacunas de 30 a 306 s. O preço disso é
#: o MESMO tick chegando N vezes, e contá-lo N vezes estragaria a volatilidade
#: realizada e o sensor de anomalia de tempo — dois "atrasos" por tick.
#:
#: A dedupe mora aqui, e não no processo, para valer qualquer que seja a
#: origem: N sockets, reprodução de gravação, ou um teste que empurre a mesma
#: lista duas vezes.
#:
#: 256 a ~1 tick/s por ativo são ~4 min de memória: folgado para cobrir a
#: diferença de chegada entre conexões, curto para não pesar.
CARIMBOS_LEMBRADOS = 256


@dataclass
class CicloAoVivo:
    """Roteia eventos para o estado e chama o motor. Sem I/O.

    Manter isto síncrono é o que permite testar seis horas de mercado num teste
    que roda em milissegundos, e reproduzir uma gravação inteira sem fingir
    rede.
    """

    motor: MotorAoVivo
    silencio_do_preco_s: float = SILENCIO_DO_PRECO_S
    #: Ativos que este ciclo opera. `None` = aceita todos.
    #:
    #: NÃO é redundante com o `assets` do `RtdsFeed`: aquele filtra o
    #: `on_tick`, e o `on_event` — que é por onde o ciclo recebe — é chamado
    #: INCONDICIONALMENTE depois (`feeds/base.py`). Sem este filtro aqui, um
    #: ativo que o bot nem opera entra no `feeds_saudaveis`, que fecha pelo
    #: pior, e emudecer bloquearia intenções de BTC/ETH saudáveis.
    ativos_operados: frozenset[str] | None = None

    #: Última chegada de tick de `twap_sixty`, por ativo (ns de parede).
    ultimo_preco_ns: dict[str, int] = field(default_factory=dict)
    #: Carimbos de servidor já vistos, por ativo. Ver `CARIMBOS_LEMBRADOS`.
    _vistos: dict[str, deque[int]] = field(default_factory=dict)
    #: O que entrou, por tipo. Zero silencioso é indistinguível de bug.
    contagem: dict[str, int] = field(default_factory=dict)

    # ────────────────────────────────────────────────────────────── ingestão
    def on_feed_event(self, event: FeedEvent) -> None:
        """Um evento do fio. Fonte desconhecida é contada, não engolida."""
        if event.source == FONTE_RTDS:
            self._on_rtds(event)
        elif event.source == FONTE_POLY:
            self._on_poly(event)
        else:
            self._contar("fonte_desconhecida")

    def _on_rtds(self, event: FeedEvent) -> None:
        tick = parse_rtds_event(event.parsed, event.ts_mono_ns, event.ts_wall_ns)
        if tick is None:
            self._contar("rtds_nao_e_preco")
            return
        if self.ativos_operados is not None and tick.asset not in self.ativos_operados:
            # O RTDS transmite todos os símbolos; só os operados importam.
            self._contar("preco_de_outro_ativo")
            return
        if tick.topic != TOPIC_TWAP_60:
            # `crypto_prices` (spot Binance) chega pelo mesmo fio. Não entra:
            # a âncora verificada é definida sobre `twap_sixty`.
            self._contar("rtds_outro_topico")
            return
        valor = e18_do_evento(event.parsed)
        if valor is None:
            # Mesmo descarte que o backtest conta em `sem_valor_exato`: sem o
            # inteiro exato não dá para casar com a âncora.
            self._contar("preco_sem_valor_exato")
            return
        if tick.src_timestamp_ms <= 0:
            self._contar("preco_sem_carimbo_do_servidor")
            return
        if self._repetido(tick.asset, tick.src_timestamp_ms):
            # Segunda conexão entregando o mesmo tick. Contado e não engolido:
            # `preco_repetido` perto de zero com `rtds_conexoes > 1` significa
            # que a redundância não está funcionando.
            self._contar("preco_repetido")
            return

        self.motor.precos.anotar(
            tick.asset,
            valor_e18=valor,
            ts_servidor_ms=tick.src_timestamp_ms,
            # A chegada alimenta o sensor de anomalia de tempo (item 3.10).
            # Sem ela o portão diria "não sei", que é recusa.
            chegada_ms=event.ts_wall_ns // 1_000_000,
        )
        self.ultimo_preco_ns[tick.asset] = event.ts_wall_ns
        self._contar("preco")

    def _repetido(self, asset: str, carimbo: int) -> bool:
        """Este (ativo, carimbo) já passou? Marca como visto se não.

        Só o carimbo EXATO conta como repetido. Tick fora de ordem NÃO é
        descartado: o backtest também os guarda (`streams_e18` acumula e a
        âncora resolve por bisect), e jogá-los fora aqui faria as duas pontas
        verem séries diferentes.
        """
        janela = self._vistos.get(asset)
        if janela is None:
            janela = deque(maxlen=CARIMBOS_LEMBRADOS)
            self._vistos[asset] = janela
        if carimbo in janela:
            return True
        janela.append(carimbo)
        return False

    def _on_poly(self, event: FeedEvent) -> None:
        vistos = 0
        for evento in eventos_do_payload(event.parsed):
            # Achado P1 do Codex no #52. A resolução tem de chegar ao PORTÃO,
            # não só ao livro: `_liquidar` fecha toda janela com `pnl=0.0`
            # (correto — no fechamento o resultado ainda não se conhece), e
            # se ninguém trouxer o PnL depois, `perdas_seguidas` e
            # `pnl_realizado_usdc` ficam em zero para sempre. A pausa por
            # sequência e o disjuntor de perda do dia NUNCA armariam no
            # SHADOW, e o ensaio aprovaria o que o LIVE já teria recusado.
            resolucao = resolucao_do_evento(evento)
            if resolucao is not None:
                if self.motor.resolver(resolucao):
                    self._contar("resolucao")
                else:
                    self._contar("resolucao_sem_posicao")
                vistos += 1
                continue
            self.motor.livros.aplicar(evento, ts_ns=event.ts_wall_ns)
            vistos += 1
        if vistos:
            self._contar("livro")
        else:
            self._contar("poly_sem_evento")

    def on_descoberta(
        self, mercados: list[DiscoveredMarket], *, agora_epoch: float
    ) -> None:
        """Um ciclo de descoberta. Quem o roda é quem tem rede."""
        self.motor.rastreador.atualizar(mercados, agora_epoch=agora_epoch)
        self._contar("descoberta")

    # ───────────────────────────────────────────────────────────────── decisão
    def passo(self, *, agora_epoch: float, agora_ns: int) -> int:
        """Um passo de decisão. Devolve quantas janelas viraram tentativa."""
        return self.motor.tick(
            agora_epoch=agora_epoch,
            agora_ns=agora_ns,
            feeds_saudaveis=self.feeds_saudaveis(agora_ns=agora_ns),
        )

    def ativos_em_jogo(self, *, agora_epoch: float) -> set[str]:
        """Os ativos com janela aberta agora — os únicos que importam."""
        return {
            janela.asset
            for janela in self.motor.rastreador.abertas(agora_epoch=agora_epoch)
        }

    def precos_velhos(self, *, agora_ns: int) -> dict[str, float]:
        """Idade em segundos de cada ativo cujo preço passou do limite.

        Vazio = todos frescos. É este dicionário que o diário mostra quando o
        bot não opera: nomear QUAL ativo emudeceu é a diferença entre um
        alarme acionável e um "feed parado" que não diz nada.
        """
        limite = self.silencio_do_preco_s
        idades = {
            asset: (agora_ns - visto) / 1e9
            for asset, visto in self.ultimo_preco_ns.items()
        }
        return {a: round(i, 2) for a, i in idades.items() if i > limite}

    def feeds_saudaveis(self, *, agora_ns: int) -> bool:
        """O feed-verdade está fresco para TODOS os ativos que ele já trouxe?

        **Pelo pior ativo, não pela média** — a mesma escolha do sensor de
        relógio, e pela mesma razão. `PrecosAoVivo` devolve o último preço de
        um ativo sem olhar a idade dele: um ativo mudo entre sete saudáveis
        decidiria com preço velho, e nada mais no caminho pegaria isso. O
        portão `feed_parado` olha o feed, não o ativo.

        Fechar tudo por causa de um ativo é conservador e é o lado certo para
        errar: com entrada única por janela, o custo de parar é uma janela
        perdida; o custo de operar com preço velho é uma posição tomada contra
        um mercado que já se moveu.

        Sem NENHUM preço ainda é `False`: bot recém-subido não sabe nada, e
        não saber não autoriza.
        """
        if not self.ultimo_preco_ns:
            return False
        return not self.precos_velhos(agora_ns=agora_ns)

    # ────────────────────────────────────────────────────────────────── estado
    def _contar(self, chave: str) -> None:
        self.contagem[chave] = self.contagem.get(chave, 0) + 1

    def resumo(self, *, agora_epoch: float, agora_ns: int) -> dict[str, Any]:
        """O que o diário e o dashboard mostram sobre o ciclo."""
        velhos = self.precos_velhos(agora_ns=agora_ns)
        return {
            "eventos": dict(sorted(self.contagem.items())),
            "feeds_saudaveis": self.feeds_saudaveis(agora_ns=agora_ns),
            "precos_velhos_s": velhos,
            "ativos_com_preco": len(self.ultimo_preco_ns),
            "ativos_em_jogo": sorted(self.ativos_em_jogo(agora_epoch=agora_epoch)),
            "janelas": self.motor.rastreador.resumo(agora_epoch=agora_epoch),
            "livros": self.motor.livros.resumo(agora_ns=agora_ns),
            "precos": self.motor.precos.resumo(),
            "motor": {
                "tentativas": self.motor.tentativas,
                "pulos": dict(sorted(self.motor.pulos.items())),
            },
            "nota": (
                "`feeds_saudaveis` e pelo PIOR ativo que ja trouxe preco: um "
                "ativo mudo entre sete saudaveis decidiria com preco velho, e "
                "PrecosAoVivo nao olha idade. `precos_velhos_s` nomeia quais. "
                "Sem nenhum preco ainda o valor e false — bot recem-subido nao "
                "sabe nada, e nao saber nao autoriza."
            ),
        }


def alimentar(ciclo: CicloAoVivo, eventos: Iterable[FeedEvent]) -> None:
    """Empurra uma sequência inteira de eventos. Útil para reprodução."""
    for evento in eventos:
        ciclo.on_feed_event(evento)
