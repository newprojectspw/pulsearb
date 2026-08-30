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
from pulsearb.markets.discovery import MarketDiscovery
from pulsearb.markets.http import fazer_http_get_json
from pulsearb.obs.logging import get_logger
from pulsearb.risk import PortaoDeRisco
from pulsearb.settings import Mode, Settings

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
        config=config
        or ConfigDoMotor(
            shares_por_trade=settings.risk.stake_max_por_trade_usdc,
            curvas_de_variancia=curvas_de_variancia,
        ),
    )
    return CicloAoVivo(motor=motor)


class ProcessoShadow:
    """Sockets, descoberta e cadência em volta de um `CicloAoVivo`."""

    def __init__(self, settings: Settings, ciclo: CicloAoVivo) -> None:
        self.settings = settings
        self.ciclo = ciclo
        self.tokens_assinados: set[str] = set()
        self.passos = 0
        self.descobertas = 0
        self.rtds = RtdsFeed(
            url=settings.endpoints.rtds_ws,
            user_agent=settings.user_agent,
            assets=settings.all_price_assets,
            on_event=self._on_event,
            stale_after_seconds=settings.feeds.stale_after_seconds_twap,
            reconnect_initial_seconds=settings.feeds.reconnect_initial_seconds,
            reconnect_max_seconds=settings.feeds.reconnect_max_seconds,
            sem_dados_timeout_s=settings.feeds.rtds_sem_dados_timeout_s,
            topico_mudo_s=settings.feeds.rtds_topico_mudo_s,
            reassinatura_intervalo_s=settings.feeds.rtds_reassinatura_intervalo_s,
            reassinaturas_ate_derrubar=settings.feeds.rtds_reassinaturas_ate_derrubar,
            rotulo="rtds[shadow]",
        )
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
            await asyncio.sleep(CADENCIA_DA_DECISAO_S)
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
            await asyncio.sleep(CADENCIA_DA_DESCOBERTA_S)

    async def _um_ciclo_de_descoberta(self, discovery: MarketDiscovery) -> None:
        mercados = await discovery.discover()
        self.descobertas += 1
        self.ciclo.on_descoberta(mercados, agora_epoch=time.time())
        # Assina o que apareceu. Janela não-operável também entra: o motor
        # decide se opera, e o diário quer o motivo — não ver o livro dela
        # trocaria "recusei por X" por "não sei nada sobre ela".
        novos = {
            token
            for mercado in mercados
            for token in mercado.token_id_by_outcome.values()
        } - self.tokens_assinados
        if novos:
            await self.poly.subscribe(sorted(novos))
            self.tokens_assinados |= novos

    async def laco_de_relato(self, deadline: float) -> None:
        while time.monotonic() < deadline:
            await asyncio.sleep(CADENCIA_DO_RELATO_S)
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
                http_get_json=fazer_http_get_json(http),
                gamma_url=self.settings.endpoints.gamma,
                clob_url=self.settings.endpoints.clob,
                assets=self.settings.assets,
                probe_durations_seconds=self.settings.probe_durations_seconds,
            )
            await self.rtds.start()
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
                await self.rtds.stop()
                await self.poly.stop()
        return self.estado()


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
    parser.add_argument("--duration", type=float, default=3600.0, help="segundos")
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
