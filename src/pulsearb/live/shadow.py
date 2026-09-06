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
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from pulsearb.caminhos import caminho_de_escrita, caminho_de_relatorio_lido
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
    Sem ela a trava de relógio (item 3.10) **não é exercitada no ensaio**: fora
    do LIVE, fonte ausente não recusa — de propósito, senão o SHADOW não
    ensaiaria nada. O custo do esquecimento não é um diário cheio de recusas; é
    um diário que passou por cima do portão sem dizer, e um `atraso_ms` que
    ninguém mediu até a primeira ordem valer dinheiro.

    O que a fonte instalada ainda recusa em qualquer modo é a fonte **muda**
    (`atraso_ms` devolvendo `None`): não saber custa o mesmo que saber que está
    ruim. O que só recusa em LIVE é o atraso ACIMA do teto — latência de rede
    ao servidor é estrutural (~1,3 s medidos contra a Polymarket) e não é
    deriva de relógio; recusar por ela apagaria o diário inteiro.
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
    return CicloAoVivo(
        motor=motor,
        # O limiar do M1, não o default do módulo. Deixá-lo no default punha o
        # ciclo MAIS permissivo que a configuração: com `stale_after_seconds_twap`
        # em 5 s, `feeds_saudaveis` seguia verdadeiro por mais 5 s e registrava
        # intenção com preço que a própria configuração já declara velho.
        #
        # Por que o limiar e não o `stale` de cada feed: o RTDS é assinado em N
        # conexões redundantes (`rtds_conexoes`), e cada `RtdsFeed` fica `stale`
        # sozinho. Fechar por feed pararia o bot quando UMA das N caísse, que é
        # justamente o caso que a redundância existe para cobrir. "Nenhum tick
        # chegou por X s" é a agregação certa: só é verdade quando TODAS
        # emudecem — e é o que o backtest também enxerga, pela regra do mesmo
        # caminho.
        silencio_do_preco_s=settings.feeds.stale_after_seconds_twap,
        # O feed não filtra o `on_event` — ver `CicloAoVivo.ativos_operados`.
        ativos_operados=frozenset(a.lower() for a in settings.assets),
    )


#: Onde os diários de rodada moram quando `--diario` não é passado.
PASTA_DOS_DIARIOS = "data/shadow"


def caminho_do_diario_da_rodada(agora: datetime | None = None) -> str:
    """Um arquivo por rodada, e a unicidade é PROVADA, não presumida.

    Achado P2 do Codex no #52, duas rodadas seguidas sobre o mesmo ponto. O
    default fixo somava rodadas porque `ExecutorSombra._anotar` abre em modo
    **append**; carimbar com segundos consertou o caso comum e deixou o
    estreito: dois processos iniciados no mesmo segundo — ou dois
    `--duration 0` seguidos — recebiam o mesmo caminho e voltavam a somar.

    Três camadas, e a terceira é a que garante:

    1. microssegundos no carimbo, em vez de segundos;
    2. o PID, que separa dois processos do mesmo instante;
    3. **criação exclusiva** (`O_EXCL`): o arquivo é criado aqui, e se já
       existir tenta-se o próximo sufixo. É o sistema de arquivos decidindo,
       não o relógio — e é o único jeito de a unicidade não depender de
       suposição sobre precisão de tempo.

    O arquivo nasce vazio, e isso é útil por si: ele é a prova de que a
    rodada começou, mesmo que ela morra antes da primeira intenção.
    """
    instante = agora or datetime.now(UTC)
    carimbo = instante.strftime("%Y%m%d-%H%M%S-%f")
    pasta = Path(PASTA_DOS_DIARIOS)
    pasta.mkdir(parents=True, exist_ok=True)

    for tentativa in range(100):
        sufixo = "" if tentativa == 0 else f"-{tentativa}"
        caminho = pasta / f"diario-{carimbo}-{os.getpid()}{sufixo}.jsonl"
        try:
            os.close(os.open(caminho, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644))
        except FileExistsError:
            continue
        return str(caminho)

    raise RuntimeError(
        f"nao consegui criar um diario exclusivo em {pasta} apos 100 tentativas — "
        "rodar sem diario proprio somaria esta rodada com outra"
    )


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
        #: Motivo pelo qual a rodada foi abortada, ou `None`. Quando existe,
        #: `main` sai com código != 0: uma rodada sem saída não é sucesso.
        self.falhou: str | None = None
        #: Os dois relógios no arranque. O 3.14 usou a divergência entre eles
        #: para ENCERRAR a rodada na hora certa; aqui ela vira MEDIDA, porque
        #: encerrar no prazo não diz quanto do prazo o bot passou acordado.
        #: `run` os reposiciona — construir o processo e rodá-lo não é o
        #: mesmo instante, e nos testes pode ser muito antes.
        self.inicio_mono = time.monotonic()
        self.inicio_parede = time.time()
        #: Marcas do relato anterior, para medir a JANELA e não só o
        #: acumulado. Ver `_vigilia`.
        self._relato_mono = self.inicio_mono
        self._relato_parede = self.inicio_parede
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
    async def laco_de_decisao(
        self, deadline: float, deadline_de_parede: float | None = None
    ) -> None:
        """Um `passo()` por cadência, até o prazo."""
        while not prazo_vencido(deadline, deadline_de_parede):
            await _dormir_ate(CADENCIA_DA_DECISAO_S, deadline)
            try:
                self.passos += 1
                self.ciclo.passo(
                    agora_epoch=time.time(), agora_ns=time.time_ns()
                )
            except OSError as erro:
                # Achado P1 do Codex no #52. I/O do diário NÃO é "evento
                # estranho": é a saída da rodada sumindo.
                #
                # A tolerância abaixo existe para um evento de mercado que o
                # parser não entende — segue-se, e as outras 23 h continuam
                # valendo. Diário sem poder escrever é o contrário disso: o
                # processo rodaria as 24 h inteiras, sairia com código 0, e
                # entregaria ZERO intenções. Pior, cada janela seria
                # reavaliada para sempre, porque a execução nunca completa.
                #
                # Disco cheio e permissão errada chegam aqui como `OSError`,
                # e `passo()` não faz outro I/O — o diário é o único.
                self.falhou = f"diario nao gravavel: {type(erro).__name__}: {erro}"
                log.error(
                    "rodada interrompida: a saida nao esta sendo gravada",
                    erro=self.falhou,
                )
                return
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
        self,
        discovery: MarketDiscovery,
        deadline: float,
        deadline_de_parede: float | None = None,
    ) -> None:
        while not prazo_vencido(deadline, deadline_de_parede):
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
                # O livro também sai: `LivrosAoVivo` não expira sozinho, e
                # 24 h de rotação deixariam milhares de `OrderBook` mortos.
                self.ciclo.motor.livros.esquecer(token)

    async def laco_de_relato(
        self, deadline: float, deadline_de_parede: float | None = None
    ) -> None:
        while not prazo_vencido(deadline, deadline_de_parede):
            await _dormir_ate(CADENCIA_DO_RELATO_S, deadline)
            if time.monotonic() >= deadline:
                # Não relata depois do prazo: o resumo final já sai no `run`.
                return
            # `avancar_vigilia`: só ESTE chamador fecha a janela de medida. O
            # resumo final e quem inspecionar o estado por fora leem sem
            # mexer nela — senão uma leitura extra zeraria a janela e o
            # próximo relato mediria um intervalo que não existiu.
            log.info("shadow", **self.estado(avancar_vigilia=True))

    def _vigilia(self, *, avancar: bool = False) -> dict[str, Any]:
        """Quanto do tempo de PAREDE o processo passou de fato acordado.

        O 3.14 mediu que `time.monotonic()` congela quando a máquina dorme e
        usou isso para encerrar a rodada na hora certa. Mas o mesmo
        congelamento tem uma segunda consequência, que ficou sem sensor: os
        DOIS laços deste processo correm em tempo monotônico — decisão a cada
        1 s, relato a cada 60 s —, então eles congelam JUNTOS. O relato sai
        sempre com +60 passos, e uma rodada dormindo tem exatamente a mesma
        cara de uma rodada saudável.

        Medido em 05/09/2026: uma rodada de 24 h na bateria ficou 1,16 h
        acordada em 9,77 h de parede — **11,9%** — e nada no relatório disse
        isso. `feeds_saudaveis` estava `true` o tempo todo, e estava certo: os
        feeds não têm defeito nenhum quando o processo inteiro está suspenso.
        Nove horas se passaram antes que alguém comparasse os dois relógios.

        Duas janelas, e as duas importam:

        - `da_rodada` responde "esta rodada de 24 h vale como medida de 24 h?"
        - `desde_o_relato` responde "e AGORA, está andando?". Sem ela, uma
          parada nova entra diluída em horas de rodada boa e só aparece no
          acumulado quando já custou o ensaio.

        `avancar=False` deixa o método sem efeito colateral: o resumo final e
        os testes podem chamá-lo sem mexer na janela do laço de relato.
        """
        mono, parede = time.monotonic(), time.time()

        def faixa(desde_mono: float, desde_parede: float) -> dict[str, Any]:
            decorrido = parede - desde_parede
            acordado = mono - desde_mono
            # Relógio de parede corrigido para TRÁS por NTP produz decorrido
            # menor que acordado — e um `ciclo_de_trabalho` acima de 1 diria
            # que o bot ficou acordado mais tempo do que existiu. Prender em
            # 1.0 é honesto: a medida não distingue "não dormiu" de "o
            # relógio andou para trás", e nenhuma das duas é dormir.
            dormiu = max(0.0, decorrido - acordado)
            return {
                "parede_s": round(decorrido, 1),
                "acordado_s": round(acordado, 1),
                "dormiu_s": round(dormiu, 1),
                "ciclo_de_trabalho": (
                    round(min(1.0, acordado / decorrido), 3) if decorrido > 0 else None
                ),
            }

        vigilia = {
            "da_rodada": faixa(self.inicio_mono, self.inicio_parede),
            "desde_o_relato": faixa(self._relato_mono, self._relato_parede),
            "nota": (
                "CICLO DE TRABALHO, e o unico numero que separa 'a rodada"
                " esta parada' de 'a rodada esta saudavel e o mercado esta"
                " quieto'. Os dois lacos correm em tempo MONOTONICO, que"
                " congela no sono da maquina (3.14): eles congelam juntos, o"
                " relato sai sempre com +60 passos e nada mais no resumo"
                " denuncia a suspensao. Valor abaixo de 1 quer dizer que o"
                " processo esteve suspenso, e uma rodada de 24 h com ciclo"
                " 0,12 observou 2,9 h de mercado — nao fecha item que exija"
                " medida sobre 24 h. Na tomada e com `caffeinate -dimsu` o"
                " valor fica em 1,0; tampa fechada dorme de qualquer forma."
            ),
        }
        if avancar:
            self._relato_mono, self._relato_parede = mono, parede
        return vigilia

    def estado(self, *, avancar_vigilia: bool = False) -> dict[str, Any]:
        return {
            "passos": self.passos,
            "descobertas": self.descobertas,
            "tokens_assinados": len(self.tokens_assinados),
            # Sai no JSON SEMPRE, inclusive `None`. Um campo que só aparece
            # quando há erro é um campo que ninguém procura quando não há.
            "falhou": self.falhou,
            "vigilia": self._vigilia(avancar=avancar_vigilia),
            **self.ciclo.resumo(agora_epoch=time.time(), agora_ns=time.time_ns()),
        }

    # ───────────────────────────────────────────────────────────────── run
    async def run(self, duration_seconds: float) -> dict[str, Any]:
        # DOIS PRAZOS, e encerra no primeiro que vencer. Item 3.14.
        #
        # `time.monotonic()` no macOS **não conta o tempo em suspensão** — ele
        # sai de `mach_absolute_time()`, que congela quando a máquina dorme.
        # Medido nesta máquina: 190,8 h de monotonic contra 370,8 h de relógio
        # de parede desde o boot, ou seja **180 h de sono**. Um `--duration
        # 24h` calculado só em monotonic vira 24 h + o que a máquina dormir, e
        # foi assim que o ensaio do 3.13 seguiu vivo em 24,6 h de parede.
        #
        # `caffeinate -i` não resolve: ele impede o sono por INATIVIDADE, não
        # o de tampa fechada nem o forçado.
        #
        # Só relógio de parede também não serve: um ajuste de NTP para frente
        # encerraria a rodada antes da hora, e é justamente o relógio que o
        # item 3.10 diz não poder assumir estável.
        #
        # Com os dois, o pior caso é encerrar no menor dos prazos — que para
        # um ensaio de medição é o lado certo de errar: rodada curta demais se
        # descobre no resumo, rodada que não termina se descobre no dia
        # seguinte.
        deadline = time.monotonic() + duration_seconds
        deadline_de_parede = time.time() + duration_seconds
        # A vigília mede a RODADA, não o objeto. Entre construir o
        # `ProcessoShadow` e chegar aqui houve descoberta, sockets e — nos
        # testes — o que o teste quiser; contar isso como tempo de rodada
        # inflaria `dormiu_s` com trabalho de arranque.
        self.inicio_mono = self._relato_mono = time.monotonic()
        self.inicio_parede = self._relato_parede = time.time()
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
                asyncio.create_task(
                    self.laco_de_descoberta(
                        discovery, deadline, deadline_de_parede
                    )
                ),
                asyncio.create_task(
                    self.laco_de_decisao(deadline, deadline_de_parede)
                ),
                asyncio.create_task(
                    self.laco_de_relato(deadline, deadline_de_parede)
                ),
            ]
            try:
                # `wait` com prazo, e não `gather`: uma descoberta em voo pode
                # ficar pendurada em vários HTTP de 15 s em sequência, e o
                # `gather` esperaria a tarefa mais lenta terminar sozinha —
                # estourando `--duration` por minutos se a Gamma estiver lenta.
                # O prazo aqui é duro: passou, cancela.
                # `FIRST_COMPLETED`, e não o default `ALL_COMPLETED`.
                # Achado P2 do Codex no #52: o tratamento de falha do diário
                # faz `laco_de_decisao` RETORNAR, mas com o default os laços
                # de descoberta e de relato seguiam até o prazo original — uma
                # rodada de 24 h abortada no minuto 5 manteria sockets e HTTP
                # ativos pelas 23 h restantes antes de devolver o código != 0.
                #
                # No caminho normal os três terminam juntos (todos são
                # `while monotonic() < deadline`), então cancelar os outros
                # quando o primeiro sai não perde nada: o prazo já passou.
                await asyncio.wait(
                    tarefas,
                    timeout=max(0.0, deadline - time.monotonic()),
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                for tarefa in tarefas:
                    tarefa.cancel()
                # Espera o cancelamento assentar antes de fechar os sockets:
                # tarefa cancelada no meio de um `await` da rede reclamaria em
                # cima de um feed já parado.
                await asyncio.gather(*tarefas, return_exceptions=True)
                for feed in self.rtds_feeds:
                    await feed.stop()
                await self.poly.stop()
        return self.estado()


def prazo_vencido(deadline: float, deadline_de_parede: float | None = None) -> bool:
    """Venceu QUALQUER um dos dois relógios? — item 3.14.

    `deadline` é monotônico e `deadline_de_parede` é `time.time()`. Os dois
    existem porque cada um falha de um jeito, e em direções opostas:

    - **monotônico** não conta suspensão (no macOS, `mach_absolute_time`).
      Máquina que dorme 3 h estica um ensaio de 24 h para 27 h de parede.
    - **parede** salta com NTP. Uma correção para frente encerraria a rodada
      antes da hora.

    Encerrar no PRIMEIRO limita o estrago dos dois: o pior caso vira "a
    rodada foi mais curta que o pedido", que aparece no resumo, em vez de "a
    rodada não terminou", que só aparece no dia seguinte.

    `deadline_de_parede=None` mantém o comportamento antigo — é o que os
    testes de laço isolado usam, e o que faz esta função ser retrocompatível
    com quem passa um prazo só.
    """
    if time.monotonic() >= deadline:
        return True
    return deadline_de_parede is not None and time.time() >= deadline_de_parede


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

    O caminho vem da linha de comando, então passa pela mesma contenção que o
    backtest usa no `--curva-de-variancia` (`pulsearb.caminhos`): validado
    contra o padrão fixo ANTES de virar caminho, montado a partir da raiz
    permitida e conferido depois de resolver. Sem isso, `--curva-de-variancia
    /etc/qualquer/coisa.json` leria de fora da raiz e devolveria o nome do
    arquivo no `origem` de cada linha do diário.
    """
    if not caminho:
        return None
    from pulsearb.engine.variancia import curvas_do_relatorio

    destino = caminho_de_relatorio_lido(caminho)
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
        default=None,
        help=(
            "onde o diário de intenções é gravado. Sem esta opção, cada "
            "rodada ganha um arquivo próprio, carimbado com o instante de "
            "início. Passar um caminho explícito ANEXA a ele — é assim que se "
            "retoma uma rodada de propósito"
        ),
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

    # Achado P2 do Codex no #52. Este processo É o SHADOW; a identidade dele
    # não pode sair de um arquivo de configuração. O `config.yaml` versionado
    # traz `mode: SIM`, então o comando documentado aqui criava um executor
    # SIM, gravava em `registro_do_dia.sim.json` e relatava modo SIM —
    # desfazendo a separação de registro que o próprio PR acabou de criar e
    # misturando este ensaio de feed ao vivo com simulações sem relação.
    #
    # LIVE já foi recusado acima, com mensagem. Qualquer outro modo vira
    # SHADOW, e a troca sai no log: silenciá-la esconderia de quem lê o
    # relatório que o `config.yaml` dele não foi obedecido.
    if settings.mode is not Mode.SHADOW:
        log.info(
            "modo forcado para SHADOW neste processo",
            modo_da_configuracao=str(settings.mode),
        )
        settings = settings.model_copy(update={"mode": Mode.SHADOW})

    # Mesma forma do backtest: caminho recusado vira mensagem e código 2, não
    # traceback. Quem roda o SHADOW por script lê o stderr, não a pilha.
    try:
        curvas = _curvas(args.curva_de_variancia)
        # O `--diario` é ESCRITO no disco vindo da linha de comando —
        # exatamente o mesmo padrão que o #53 fechou no `--curva-de-variancia`
        # e o M2.5 no `--json` do backtest. `--diario /etc/cron.d/qualquer` era
        # travessia de caminho no processo que abre socket, e com escrita em
        # vez de leitura.
        #
        # O default gerado não passa por aqui de propósito: ele não vem de
        # fora, é montado por `caminho_do_diario_da_rodada` a partir de uma
        # raiz literal.
        caminho_do_diario = (
            caminho_de_escrita(args.diario, extensoes=(".jsonl",))
            if args.diario
            else Path(caminho_do_diario_da_rodada())
        )
    except ValueError as erro:
        print(str(erro), file=sys.stderr)
        return 2

    ciclo = montar_ciclo(
        settings,
        caminho_do_diario=caminho_do_diario,
        curvas_de_variancia=curvas,
    )
    processo = ProcessoShadow(settings, ciclo)
    estado = asyncio.run(processo.run(args.duration))
    print(json.dumps(estado, indent=2, ensure_ascii=False, default=str))
    # Rodada sem saída NÃO é sucesso. Sair com 0 depois de 24 h que não
    # gravaram nada faria o systemd (e quem lê o log) tratar como bem
    # sucedida uma rodada que não produziu o único artefato que ela existe
    # para produzir.
    return 1 if processo.falhou else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
