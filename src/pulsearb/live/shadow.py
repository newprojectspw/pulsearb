"""O processo SHADOW: abre os sockets, roda a descoberta, chama o ciclo.

Fecha a parte que faltava do item 3.13. O `CicloAoVivo` sabe decidir mas não
sabe falar com a rede — de propósito, para poder ser alimentado por reprodução
de gravação. Este módulo é quem lhe dá rede.

## A fiação é a MESMA do recorder

`RtdsFeed`, `PolyMarketWsFeed` e `MarketDiscovery` são as classes que gravaram
as 24 h do M2. Reusá-las não é economia de digitação: se o SHADOW abrisse os
sockets por outro caminho, uma diferença de assinatura ou de reconexão faria a
população que ele vê divergir da que o backtest leu — e a comparação entre os
dois perderia o sentido.

## O que este processo NUNCA faz

Enviar ordem. O executor sai de `escolher_executor`, que recusa LIVE pela
autorização (`risk/autorizacao.py`) e devolve `ExecutorSombra` em SIM e SHADOW.
Não há caminho aqui que envie — não por disciplina, mas porque o código que
sabe enviar não existe.

## Duas decisões de cadência, e o que elas custam

**Decisão a cada segundo, não a cada tick.** O feed-verdade entrega ~1,061
tick/s por ativo (M2.10), e o backtest decide a cada instante do stream — ou
seja, ~1/s por janela. Um temporizador de 1 s aproxima isso com custo
constante, em vez de acordar oito vezes por segundo para reavaliar as mesmas
janelas. O preço é até ~1 s de atraso a mais que o backtest, dentro da grade
de latência que o M2 já mediu (150 a 1000 ms).

**Descoberta a cada 30 s.** Janela de 5 min descoberta com 30 s de atraso ainda
sobra 4,5 min — e a faixa operada é os últimos 240 s. Mais frequente só
gastaria Gamma sem mudar decisão.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from pulsearb.execution.executor import escolher_executor
from pulsearb.feeds.base import FeedEvent
from pulsearb.feeds.poly_ws import PolyMarketWsFeed
from pulsearb.feeds.rtds import RtdsFeed
from pulsearb.live.ciclo import CicloAoVivo
from pulsearb.live.livros import LivrosAoVivo
from pulsearb.live.motor import ConfigDoMotor, MotorAoVivo
from pulsearb.live.precos import PrecosAoVivo
from pulsearb.live.rastreador import RastreadorDeJanelas
from pulsearb.markets.discovery import MarketDiscovery, parse_end_date_epoch
from pulsearb.markets.http import fazer_http_get_json
from pulsearb.obs import get_logger, setup_logging
from pulsearb.risk import PortaoDeRisco
from pulsearb.settings import Mode, Settings
from pulsearb.tempo import RESOLUTION_GRACE_SECONDS, parse_duration

log = get_logger(__name__)

#: De quanto em quanto tempo o ciclo decide. Ver o módulo.
CADENCIA_DA_DECISAO_S = 1.0
#: De quanto em quanto tempo a descoberta roda.
CADENCIA_DA_DESCOBERTA_S = 30.0
#: De quanto em quanto tempo o estado sai no log.
CADENCIA_DO_RELATO_S = 60.0


def montar_ciclo(
    settings: Settings,
    *,
    caminho_do_diario: Path,
    curvas_de_variancia: Any = None,
    config: ConfigDoMotor | None = None,
) -> CicloAoVivo:
    """Monta o ciclo inteiro a partir da configuração. Sem rede, sem I/O.

    Fábrica separada do processo de propósito: é aqui que mora a decisão de
    ligação mais importante do M4, e ela precisa ser testável sem abrir socket.

    **A ligação:** o `PortaoDeRisco` recebe `relogio_do_servidor=precos.relogio`.
    Sem isso a trava de relógio (item 3.10) diria "não sei" a cada ordem — que é
    recusa —, e o SHADOW registraria `relogio_nao_monitorado` em toda linha do
    diário em vez de exercitar os portões que interessam.
    """
    precos = PrecosAoVivo()
    portao = PortaoDeRisco(
        settings.risk,
        settings.mode,
        caminho_do_registro=Path(settings.risk.caminho_do_registro),
        caminho_do_kill=Path(settings.risk.caminho_do_kill),
        # O elo do item 3.10. A fonte é alimentada pelo ciclo, tick a tick.
        relogio_do_servidor=precos.relogio,
    )
    motor = MotorAoVivo(
        rastreador=RastreadorDeJanelas(),
        livros=LivrosAoVivo(),
        precos=precos,
        # `escolher_executor` recusa LIVE pela autorização. Não há caminho
        # aqui que envie ordem.
        executor=escolher_executor(
            settings.mode, portao, caminho_do_diario=caminho_do_diario
        ),
        # `shares_por_trade` NÃO sai do teto de risco: são unidades
        # diferentes. O teto é em USDC e quem o aplica é o portão
        # (`stake_acima_do_teto`, sobre `shares × preço`); `shares_por_trade`
        # é em SHARES, e o default do backtest (5) é o mínimo que o mercado
        # aceita (API_NOTES §12.5). Derivar um do outro punha 3 shares num
        # mercado que exige 5 — ordem que a corretora rejeita.
        config=config or ConfigDoMotor(curvas_de_variancia=curvas_de_variancia),
    )
    return CicloAoVivo(motor=motor)


class ProcessoShadow:
    """Sockets, descoberta e cadência em volta de um `CicloAoVivo`."""

    def __init__(self, settings: Settings, ciclo: CicloAoVivo) -> None:
        self.settings = settings
        self.ciclo = ciclo
        self.tokens_assinados: set[str] = set()
        #: Até quando cada token interessa (epoch). Sem isto, 24 h de
        #: descoberta acumulam milhares de assinaturas e livros retidos, e
        #: cada reconexão reenvia o conjunto histórico inteiro.
        self.desassinar_apos: dict[str, float] = {}
        self.passos = 0
        self.descobertas = 0
        # REDUNDÂNCIA, como no recorder: N conexões ao MESMO endpoint. Não é
        # paranoia — conexão individual do RTDS já produziu lacunas de 30 a
        # 306 s, e uma lacuna aqui que a gravação não tem faria o SHADOW perder
        # ticks de âncora que o backtest enxerga. A comparação entre os dois é
        # a razão de o SHADOW existir; furá-la por economizar um socket seria
        # trocar o fim pelo meio.
        #
        # O tick repetido que a redundância produz é descartado pelo ciclo
        # (`CicloAoVivo._repetido`), não aqui: assim vale para qualquer origem.
        self.rtds_feeds: list[RtdsFeed] = [
            RtdsFeed(
                url=settings.endpoints.rtds_ws,
                user_agent=settings.user_agent,
                # Só os ativos OPERADOS. `all_price_assets` traz também os
                # `extra_price_assets`, que existem para gravação e backtest
                # futuro — e como `feeds_saudaveis` fecha pelo pior ativo, um
                # SOL mudo bloquearia intenções de BTC/ETH saudáveis.
                assets=settings.assets,
                on_event=self._on_event,
                stale_after_seconds=settings.feeds.stale_after_seconds_twap,
                reconnect_initial_seconds=settings.feeds.reconnect_initial_seconds,
                reconnect_max_seconds=settings.feeds.reconnect_max_seconds,
                sem_dados_timeout_s=settings.feeds.rtds_sem_dados_timeout_s,
                topico_mudo_s=settings.feeds.rtds_topico_mudo_s,
                reassinatura_intervalo_s=settings.feeds.rtds_reassinatura_intervalo_s,
                reassinaturas_ate_derrubar=(
                    settings.feeds.rtds_reassinaturas_ate_derrubar
                ),
                # O rótulo torna o log atribuível a UMA conexão; sem ele as
                # duas logam idêntico e não dá para saber qual reclamava.
                rotulo=f"rtds[shadow:{indice}]",
            )
            for indice in range(max(1, settings.feeds.rtds_conexoes))
        ]
        self.poly = PolyMarketWsFeed(
            url=settings.endpoints.clob_market_ws,
            user_agent=settings.user_agent,
            custom_feature_enabled=True,
            ping_interval_seconds=settings.feeds.clob_ping_interval_seconds,
            pong_stale_seconds=settings.feeds.clob_stale_seconds,
            on_event=self._on_event,
            stale_after_seconds=settings.feeds.stale_after_seconds_book,
            reconnect_initial_seconds=settings.feeds.reconnect_initial_seconds,
            reconnect_max_seconds=settings.feeds.reconnect_max_seconds,
        )

    # ────────────────────────────────────────────────────────────── ingestão
    def _on_event(self, event: FeedEvent) -> None:
        self.ciclo.on_feed_event(event)

    # ──────────────────────────────────────────────────────────────── laços
    async def laco_de_decisao(self, deadline: float) -> None:
        """Um `passo()` por cadência, até o prazo."""
        while time.monotonic() < deadline:
            await _dormir_ate(CADENCIA_DA_DECISAO_S, deadline)
            try:
                self.passos += 1
                self.ciclo.passo(
                    agora_epoch=time.time(), agora_ns=time.time_ns()
                )
            except Exception as erro:
                # Um passo que levanta NÃO derruba o processo: o SHADOW existe
                # para rodar 24 h e mostrar o que aconteceu. Cair no primeiro
                # evento estranho entregaria zero informação sobre as outras
                # 23 horas. O erro sai nomeado e o laço segue.
                log.warning(
                    "passo de decisao falhou",
                    erro=f"{type(erro).__name__}: {erro}",
                )

    async def laco_de_descoberta(
        self, discovery: MarketDiscovery, deadline: float
    ) -> None:
        while time.monotonic() < deadline:
            try:
                await self._um_ciclo_de_descoberta(discovery)
            except Exception as erro:
                log.warning(
                    "descoberta falhou", erro=f"{type(erro).__name__}: {erro}"
                )
            await _dormir_ate(CADENCIA_DA_DESCOBERTA_S, deadline)

    async def _um_ciclo_de_descoberta(self, discovery: MarketDiscovery) -> None:
        mercados = await discovery.discover()
        self.descobertas += 1
        self.ciclo.on_descoberta(mercados, agora_epoch=time.time())
        # Assina o que apareceu. Janela não-operável também entra: o motor
        # decide se opera, e o diário quer o motivo — não ver o livro dela
        # trocaria "recusei por X" por "não sei nada sobre ela".
        agora = time.time()
        vivos: set[str] = set()
        for mercado in mercados:
            fim = parse_end_date_epoch({"endDate": mercado.end_date_iso})
            limite = (fim if fim is not None else agora) + RESOLUTION_GRACE_SECONDS
            for token in mercado.token_id_by_outcome.values():
                self.desassinar_apos[token] = limite
                vivos.add(token)

        novos = vivos - self.tokens_assinados
        if novos:
            await self.poly.subscribe(sorted(novos))
            self.tokens_assinados |= novos

        # A carência existe porque a resolução não chega no instante do
        # fechamento: desassinar cedo perderia o evento que diz quem ganhou.
        # É a MESMA de `pulsearb.tempo` que o recorder usa — se as duas
        # divergissem, um pararia de ver o token antes do outro.
        encerrados = {
            token
            for token in self.tokens_assinados
            if agora >= self.desassinar_apos.get(token, 0.0)
        }
        if encerrados:
            await self.poly.unsubscribe(sorted(encerrados))
            self.tokens_assinados -= encerrados
            for token in encerrados:
                self.desassinar_apos.pop(token, None)

    async def laco_de_relato(self, deadline: float) -> None:
        while time.monotonic() < deadline:
            await _dormir_ate(CADENCIA_DO_RELATO_S, deadline)
            if time.monotonic() >= deadline:
                # Não relata depois do prazo: o resumo final já sai no `run`.
                return
            log.info("shadow", **self.estado())

    def estado(self) -> dict[str, Any]:
        return {
            "passos": self.passos,
            "descobertas": self.descobertas,
            "tokens_assinados": len(self.tokens_assinados),
            **self.ciclo.resumo(agora_epoch=time.time(), agora_ns=time.time_ns()),
        }

    # ───────────────────────────────────────────────────────────────── run
    async def run(self, duration_seconds: float) -> dict[str, Any]:
        deadline = time.monotonic() + duration_seconds
        async with httpx.AsyncClient(
            headers={"User-Agent": self.settings.user_agent}, timeout=15.0
        ) as http:
            discovery = MarketDiscovery(
                http_get_json=fazer_http_get_json(
                    http,
                    bases=(
                        self.settings.endpoints.gamma,
                        self.settings.endpoints.clob,
                    ),
                ),
                gamma_url=self.settings.endpoints.gamma,
                clob_url=self.settings.endpoints.clob,
                assets=self.settings.assets,
                probe_durations_seconds=self.settings.probe_durations_seconds,
            )
            for feed in self.rtds_feeds:
                await feed.start()
            await self.poly.start()
            tarefas = [
                asyncio.create_task(self.laco_de_descoberta(discovery, deadline)),
                asyncio.create_task(self.laco_de_decisao(deadline)),
                asyncio.create_task(self.laco_de_relato(deadline)),
            ]
            try:
                await asyncio.gather(*tarefas)
            finally:
                for tarefa in tarefas:
                    tarefa.cancel()
                for feed in self.rtds_feeds:
                    await feed.stop()
                await self.poly.stop()
        return self.estado()


async def _dormir_ate(cadencia: float, deadline: float) -> None:
    """Dorme a cadência, ou o que falta do prazo — o que for menor.

    Sem isto, `--duration 10` bloqueava ~60 s no laço de relato e a rodada
    estourava o prazo pedido em quase um minuto: o `gather` espera as três
    tarefas, e a mais lenta manda. Uma rodada de fumaça de 10 s tem de durar
    10 s, senão ninguém a usa.
    """
    await asyncio.sleep(max(0.0, min(cadencia, deadline - time.monotonic())))


def _curvas(caminho: str | None) -> Any:
    """Carrega as curvas medidas. Falha ALTO se pediram e não deu.

    Cair no modelo derivado porque o arquivo não abriu recriaria ao vivo a
    diferença de 39 a 48× que a §2d-ter mediu — e o diário atribuiria a
    divergência ao mercado.
    """
    if not caminho:
        return None
    from pulsearb.engine.variancia import curvas_do_relatorio

    destino = Path(caminho)
    with destino.open(encoding="utf-8") as arquivo:
        curvas = curvas_do_relatorio(json.load(arquivo), origem=destino.name)
    if not len(curvas):
        raise SystemExit(f"{caminho} nao traz curva utilizavel para nenhum ativo")
    return curvas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PULSEARB SHADOW — decide, não envia")
    parser.add_argument(
        "--duration",
        type=parse_duration,
        default="1h",
        help="90s, 30m, 24h, 7d — sem sufixo, horas",
    )
    parser.add_argument(
        "--diario",
        default="data/shadow/diario.jsonl",
        help="onde o diário de intenções é gravado",
    )
    parser.add_argument(
        "--curva-de-variancia",
        help=(
            "relatório de variância medida. TEM de casar com o que o backtest "
            "usou: rodar o SHADOW no modelo derivado depois de validar no "
            "medido recria ao vivo a diferença de 39 a 48x da §2d-ter"
        ),
    )
    args = parser.parse_args(argv)
    # ANTES de qualquer coisa: sem isto o root logger fica em WARNING e os
    # relatos de 60 s, os avisos de conexão e os motivos de janela ignorada
    # somem — 24 h de rodada entregando só o resumo final.
    setup_logging()

    settings = Settings.load()
    if settings.mode is Mode.LIVE:
        # `escolher_executor` levantaria de qualquer forma; falhar aqui dá a
        # mensagem completa antes de abrir socket nenhum.
        print(
            "este processo é o SHADOW e nunca envia ordem. Para LIVE, "
            "ver risk/autorizacao.py — e o cliente de ordens ainda não existe.",
            file=sys.stderr,
        )
        return 2

    ciclo = montar_ciclo(
        settings,
        caminho_do_diario=Path(args.diario),
        curvas_de_variancia=_curvas(args.curva_de_variancia),
    )
    processo = ProcessoShadow(settings, ciclo)
    estado = asyncio.run(processo.run(args.duration))
    print(json.dumps(estado, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
