"""python -m pulsearb.backtest data/recordings --json relatorio.json

Monta as janelas a partir da gravação, roda o modelo, desconta tudo e imprime
o relatório completo do M2.D + as medições do M2.E.

Sem gravação não há relatório. O comando falha com mensagem clara em vez de
produzir números sobre um conjunto vazio — número de backtest sobre zero dado
é a forma mais fácil de se enganar.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import resource
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from pathlib import Path
from typing import Any

from pulsearb.analysis.anchor_sweep import (
    IDADE_MAX_MS,
    JanelaResolvida,
    StreamE18,
    ancora_verificada,
    valor_final,
    varrer,
    veredito_da_ancora,
)
from pulsearb.analysis.integrity import (
    ORDEM_QUALIDADE,
    QUALIDADES,
    MonitorDeIntegridade,
    MonitorDeRelogio,
)
from pulsearb.analysis.measurements import (
    conta_do_maker,
    medir_atraso_liquidacao,
    medir_markout,
    medir_mudanca_de_tick,
    medir_profundidade,
)
from pulsearb.analysis.rewards import simular as simular_rewards
from pulsearb.backtest.book import OrderBook
from pulsearb.backtest.report import curva_de_edge_por_threshold
from pulsearb.backtest.runner import (
    LIMITE_SNAPSHOTS_PADRAO,
    NIVEIS_RETIDOS_PADRAO,
    TAMANHOS_PADRAO,
    BacktestConfig,
    BacktestRunner,
    BookTimeline,
    WindowState,
    sensibilidade_latencia,
    varredura_de_tamanho,
    varredura_de_threshold,
)

# As hipóteses nomeadas continuam importadas porque continuam sendo
# REPORTADAS — como referência histórica. `compute_anchor` saiu do
# caminho de decisão no M2.6 e não é mais importada: a âncora do
# backtest vem de `ancora_verificada`.
from pulsearb.engine.anchor import (
    WindowOutcome,
    evaluate_hypotheses,
    report_anchor_validation,
)
from pulsearb.feeds.poly_ws import (
    EVENT_BOOK,
    EVENT_LAST_TRADE,
    EVENT_PRICE_CHANGE,
    Resolucao,
    normalizar_condition_id,
    resolucao_do_evento,
)
from pulsearb.feeds.rtds import TOPIC_TWAP_60, parse_rtds_event
from pulsearb.markets.discovery import duracao_do_slug, parse_end_date_epoch
from pulsearb.recorder.writer import FONTE_RESOLUCAO_SINTETICA
from pulsearb.replay.reader import RecordingReader, ReplayRecord

#: De quantos em quantos registros o progresso é impresso. 500 mil é ~1 % de
#: uma gravação de 24 h, então dá umas cem linhas por rodada: frequente o
#: bastante para ninguém achar que travou, raro o bastante para não virar
#: ruído nem custar tempo no laço quente.
PASSO_DO_PROGRESSO = 500_000


def _rss_gib() -> float:
    """Memória residente do processo, em GiB.

    `ru_maxrss` vem em BYTES no macOS e em KILOBYTES no Linux. A conta errada
    dá 1024x de diferença — e como a máquina de análise é um Mac e os testes
    rodam em Linux, o erro passaria despercebido nos dois lugares por motivos
    opostos.
    """
    bruto = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return bruto / (1024**3)
    return bruto / (1024**2)


class Progresso:
    """Diz onde a rodada está. Vai para STDERR, e isso não é detalhe.

    O relatório sai por STDOUT. Progresso no mesmo lugar corromperia o JSON —
    quem redireciona `> relatorio.json` receberia um arquivo que não parseia.

    Isto existe porque o backtest passou a vida inteira sem imprimir nada até
    terminar. Numa gravação de 24 h são mais de três horas de silêncio
    absoluto, e a única leitura possível de fora é "travou". `rss_gib` está
    junto porque o modo real de falhar numa máquina de análise não é erro: é
    swap, e swap não parece travamento — parece lentidão sem fim.
    """

    def __init__(self, *, ativo: bool = True) -> None:
        self.ativo = ativo
        self.inicio = time.monotonic()
        self.marco = self.inicio
        self.ultimo_total = 0

    def passada(self, nome: str, *, arquivos: int) -> None:
        if not self.ativo:
            return
        self.marco = time.monotonic()
        self.ultimo_total = 0
        self._linha(f"{nome}: comecando sobre {arquivos} arquivo(s)")

    def talvez(self, nome: str, total: int) -> None:
        """Chame a cada registro; ela decide sozinha quando falar."""
        if not self.ativo or total - self.ultimo_total < PASSO_DO_PROGRESSO:
            return
        agora = time.monotonic()
        decorrido = agora - self.marco
        taxa = (total / decorrido) if decorrido > 0 else 0.0
        self.ultimo_total = total
        self._linha(
            f"{nome}: {total:,} registros"
            f" | {taxa:,.0f}/s"
            f" | {decorrido / 60:.1f} min nesta passada"
            f" | rss {_rss_gib():.2f} GiB"
        )

    def terminou(self, nome: str, total: int) -> None:
        if not self.ativo:
            return
        self._linha(
            f"{nome}: FIM, {total:,} registros em "
            f"{(time.monotonic() - self.marco) / 60:.1f} min"
        )

    @staticmethod
    def _linha(texto: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {texto}", file=sys.stderr, flush=True)


TOKEN_DURACAO_PADRAO = 300


def _fator_de_encolhimento_valido(bruto: str) -> float:
    """Rejeita fator fora de (0, 1] NO PARSE, não horas depois.

    `encolher_para_a_base` valida a mesma faixa, mas só é chamada na
    comparação final — depois de indexar a gravação e rodar os backtests
    crus. Num bloco de 72 h, um `--fator-de-encolhimento 0` estourava
    horas depois do lançamento; e sem nenhuma previsão elegível, nem
    estourava — o fator inválido ia parar num relatório de aparência
    normal. Achado em review.
    """
    valor = float(bruto)
    if not 0.0 < valor <= 1.0:
        raise argparse.ArgumentTypeError(
            f"fator fora de (0, 1]: {bruto!r} — 1.0 é identidade, e "
            "encolher é multiplicar por MENOS que um; 0 apagaria o "
            "preditor inteiro."
        )
    return valor


def caminho_de_leitura(bruto: str) -> Path:
    """Valida um caminho de ENTRADA vindo da linha de comando.

    Resolve para caminho canônico e confirma que existe. Além de fechar o
    caminho para travessia de diretório (o valor vem de fora do programa),
    troca um traceback de `FileNotFoundError` lá na frente por um erro que
    diz o que está errado.
    """
    caminho = Path(bruto).expanduser().resolve(strict=False)
    if not caminho.exists():
        raise ValueError(f"gravação não encontrada: {caminho}")
    return caminho


#: Variável que amplia a raiz permitida para o arquivo de saída.
ENV_RAIZ_DE_SAIDA = "PULSEARB_BACKTEST_OUTPUT_ROOT"


def raiz_de_saida() -> Path:
    """Onde o relatório PODE ser escrito. Diretório de trabalho, por padrão.

    O caminho do `--json` vem de fora do programa — de uma pessoa com pressa,
    de um script, de um agente. Sufixo e diretório-pai existentes não impedem
    `--json /etc/cron.d/qualquer.json`: para isso é preciso **conter** o
    caminho, não só conferir a forma dele.

    O padrão é o diretório de trabalho porque é onde o runbook manda gravar
    (`--json relatorio.json`, `--json relatorios/2026-08-20-13.json`). Quem
    precisa escrever em outro lugar diz isso de propósito, definindo
    `PULSEARB_BACKTEST_OUTPUT_ROOT` — que é diferente de o programa aceitar
    qualquer caminho em silêncio.
    """
    bruto = os.environ.get(ENV_RAIZ_DE_SAIDA)
    if bruto:
        return Path(bruto).expanduser().resolve(strict=False)
    return Path.cwd().resolve()


#: Forma aceita para o `--json`: caminho RELATIVO, segmentos de letras,
#: dígitos, `-`, `_` e `.`, separados por `/`, terminando em `.json`. Sem raiz
#: absoluta, sem `..`, sem `~`, sem caractere exótico.
#:
#: É uma lista de permissões, e é de propósito. Conferir o caminho DEPOIS de
#: montá-lo ("ele caiu dentro da raiz?") funciona, mas continua entregando a
#: string de fora ao sistema de arquivos; a análise de fluxo do SonarCloud
#: aponta isso e está certa em apontar. Validar ANTES contra um padrão fixo e
#: só então montar o caminho a partir de uma raiz confiável não deixa o valor
#: externo chegar ao disco em forma nenhuma.
PADRAO_SAIDA = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*")


def caminho_de_escrita(bruto: str) -> Path:
    """Monta o caminho de SAÍDA a partir da raiz permitida.

    O argumento é lido como caminho **relativo à raiz** (`--json
    relatorio.json`, `--json relatorios/2026-08-20-13.json`), nunca como
    caminho absoluto. Para gravar em outro lugar, mude a RAIZ com
    `PULSEARB_BACKTEST_OUTPUT_ROOT` — assim o destino é sempre uma decisão
    explícita de quem roda, e não um efeito colateral do argumento.

    Um relatório de backtest escrito em local inesperado é pior que um erro:
    some sem ninguém notar.
    """
    relativo = bruto.strip().removeprefix("./")
    if not PADRAO_SAIDA.fullmatch(relativo) or not relativo.endswith(".json"):
        raise ValueError(
            f"nome de saída inválido: {bruto!r}\n"
            "esperado: caminho relativo terminando em .json, com letras, "
            "dígitos, '-', '_' e '.' (ex.: relatorios/2026-08-20-13.json).\n"
            f"para gravar em outra raiz, defina {ENV_RAIZ_DE_SAIDA}."
        )
    raiz = raiz_de_saida()
    caminho = raiz / relativo
    # Cinto e suspensório: o padrão acima já exclui `..` e raiz absoluta, mas
    # a raiz vem de variável de ambiente e pode conter symlink. A contenção
    # depois de resolver custa uma syscall e fecha esse resto.
    #
    # A contenção está escrita na forma canônica que a análise de fluxo do
    # SonarCloud reconhece como sanitização de S2083 (caminho absoluto +
    # `startswith` contra a raiz + separador). `Path.is_relative_to` faz a
    # MESMA conta, mas o motor de taint não o conhece como sanitizador e
    # continuaria marcando o `write_text` lá na frente. O `os.sep` no fim da
    # raiz evita a colisão de prefixo (/raiz versus /raiz2) — e só entra
    # quando a raiz ainda não termina no separador, senão a raiz `/` viraria
    # `//` e rejeitaria todo caminho válido (achado em review).
    raiz_resolvida = raiz.resolve(strict=False)
    resolvido = caminho.resolve(strict=False)
    prefixo = str(raiz_resolvida)
    if not prefixo.endswith(os.sep):
        prefixo += os.sep
    if not str(resolvido).startswith(prefixo):
        raise ValueError(f"saída fora da raiz permitida: {resolvido}")
    if not resolvido.parent.is_dir():
        raise ValueError(f"diretório de saída não existe: {resolvido.parent}")
    if resolvido.is_dir():
        raise ValueError(f"o destino é um diretório: {resolvido}")
    return resolvido


# Quanto tempo ANTES da abertura da janela o book do token ainda interessa.
# `BookTimeline.at(t)` devolve o último snapshot ≤ t, então a primeira
# consulta da janela precisa de um snapshot anterior a ela. O recorder assina
# o token quando a descoberta o encontra, alguns minutos antes; 10 minutos de
# pré-rolo cobrem isso com folga e mantêm a retenção limitada.
PRE_ROLO_S = 600
# E depois do fechamento: a penalidade de latência consulta `t + latência`,
# no máximo 1s à frente (LATENCIAS_MS_PADRAO). 5s de folga.
POS_ROLO_S = 5


class RecordingIndex:
    """Duas passadas sobre a gravação, com memória limitada por construção.

    Por que DUAS passadas, e não uma:

    A passada 1 lê só o que é leve — snapshots de descoberta, ticks do RTDS,
    resoluções, lacunas. Ela é o que define QUAIS tokens existem e em que
    intervalo de tempo cada um importa. Sem essa informação, a passada única
    da versão anterior era obrigada a guardar o book de TODO token em TODO
    instante da gravação, inclusive de janelas que já tinham fechado horas
    antes e nunca seriam avaliadas.

    A passada 2 reconstrói os books, mas só dos tokens conhecidos e só dentro
    do intervalo `[abertura − PRE_ROLO, fechamento + POS_ROLO]`. Reler o
    arquivo custa I/O e descompressão; guardar 12 milhões de books custa a
    máquina inteira. O I/O é o lado barato dessa troca.

    A memória fica limitada por `tokens_de_interesse × limite_por_token`, um
    número que dá para calcular antes de rodar — e que o relatório imprime.
    """

    def __init__(
        self,
        reader: RecordingReader,
        *,
        limite_por_token: int = LIMITE_SNAPSHOTS_PADRAO,
        niveis_retidos: int = NIVEIS_RETIDOS_PADRAO,
        progresso: Progresso | None = None,
    ) -> None:
        self.reader = reader
        # Default ATIVO: o silêncio de três horas foi o defeito. Os testes
        # passam um desligado para não poluir a saída.
        self.progresso = progresso if progresso is not None else Progresso()
        self.limite_por_token = limite_por_token
        self.niveis_retidos = niveis_retidos
        self.streams: dict[str, list[tuple[int, float]]] = defaultdict(list)
        # M2.4: o MESMO stream, mas em inteiros na escala 1e18 do Chainlink e
        # no eixo do carimbo do SERVIDOR (ms). É o que a varredura de τ usa:
        # a decisão Up/Down não pode passar por float (uma falha real tem gap
        # na 9ª casa relativa), e o alinhamento não pode usar a chegada local.
        self.streams_e18: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self.books: dict[str, BookTimeline] = {}
        self.book_atual: dict[str, OrderBook] = {}
        self.snapshots: list[dict[str, Any]] = []   # compactado: ver _on_discovery
        self.n_snapshots = 0
        self.ticks_vistos: Counter[str] = Counter()
        self.janelas_por_slug: dict[str, dict[str, Any]] = {}
        # M2.3: a resolução é indexada pelos DOIS caminhos que o evento
        # oferece — condition id e token. O condition id é a chave primária
        # (identifica o mercado inteiro); o token cobre o fallback sintético
        # da Gamma, que só nomeia um token por vez.
        self.resolucoes_por_condicao: dict[str, Resolucao] = {}
        self.resolucoes_por_token: dict[str, Resolucao] = {}
        self.resolucoes: dict[str, int] = {}   # asset_id → ts_ns da resolução
        self.resolvido_up: dict[str, bool] = {}
        self.eventos_de_resolucao = 0
        self.resolucoes_sinteticas = 0
        # Redundância medida em produção: o recorder grava a MESMA resolução
        # mais de uma vez (o fallback da Gamma escreve um evento por TOKEN —
        # dois por janela — e o gate `resolvidos` dele não reconhece a forma
        # do fio, então polla mesmo quando o evento já chegou). 73 eventos
        # para 26 mercados na gravação de 2026-08-19 19h. O leitor deduplica
        # por mercado e CONTA, em vez de fingir que cada evento é novidade.
        self.resolucoes_redundantes = 0
        # Fio e Gamma discordando sobre o vencedor do MESMO mercado seria
        # gravíssimo — contado à parte, nunca resolvido em silêncio.
        self.resolucoes_conflitantes: list[str] = []
        self.resolucoes_sem_janela: list[str] = []
        self.resolucoes_ambiguas: list[str] = []
        self.gaps: list[dict[str, Any]] = []
        self.janelas_de_interesse: dict[str, tuple[int, int]] = {}  # token → (ini, fim)
        self._ultimo_tick: dict[str, float] = {}
        # M2.2 A.2/A.4: o MESMO monitor que roda ao vivo no recorder, agora
        # sobre a gravação. Se os dois discordarem, o que se perdeu foi entre
        # o fio e o disco.
        self.integridade = MonitorDeIntegridade()
        self.relogio = MonitorDeRelogio()
        # M2.6 BUG 3: silêncios do RTDS, com escopo. Guarda só os
        # silêncios (poucos), nunca a série de carimbos inteira.
        self._silencios: list[dict[str, Any]] = []
        self._ultimo_rtds_ns = 0
        self._ultimo_twap_ns: dict[str, int] = {}
        self._eventos_rtds = 0
        self._eventos_no_ultimo_twap: dict[str, int] = {}
        # M2.10 item 2: eventos de topicos != twap_sixty, e o snapshot desse
        # contador no ultimo tick de cada ativo. E o denominador correto da
        # inferencia "a conexao estava viva".
        self._eventos_de_outros_topicos = 0
        self._outros_topicos_no_ultimo_twap: dict[str, int] = {}
        self._topicos_rtds: Counter[str] = Counter()
        # M2.8: quantos ticks do twap chegaram, e quantos NAO viraram
        # ponto da serie e18 — o descarte que a ancora sofre calada.
        self._twap_vistos = 0
        # M2.9: extremos da gravacao, para detectar silencio que dura ate
        # o FIM — o caso que o detector do M2.6 nao via.
        self._primeiro_record_ns = 0
        self._ultimo_record_ns = 0
        self._e18_descartados: Counter[str] = Counter()
        # M2.2 B.2: execuções observadas, por token.
        self.trades: dict[str, list[tuple[int, float, float, str]]] = defaultdict(list)

    # --------------------------------------------------------------- passadas
    def build(self) -> None:
        self._primeira_passada()
        self.progresso.terminou("passada 1", self.reader.total)
        # M2.5: fecha as afirmações de `best_bid_ask` que ainda esperavam
        # alinhamento e as divergências abertas no último evento. Sem isto os
        # dois erros apontariam para o mesmo lado — o de esconder problema.
        self.integridade.finalizar()
        self._marcar_tokens_de_interesse()
        self._segunda_passada()

    def _primeira_passada(self) -> None:
        """Metadados, preço-verdade e resoluções. Ignora o book por completo."""
        self.progresso.passada("passada 1", arquivos=len(self.reader.files))
        for record in self.reader.iter_records():
            self.progresso.talvez("passada 1", self.reader.total)
            if record.ts_wall_ns > 0:
                if self._primeiro_record_ns == 0:
                    self._primeiro_record_ns = record.ts_wall_ns
                self._ultimo_record_ns = max(
                    self._ultimo_record_ns, record.ts_wall_ns
                )
            if record.fonte == "gap" and isinstance(record.payload, dict):
                self.gaps.append(record.payload)
            elif record.fonte == "discovery_snapshot" and isinstance(record.payload, dict):
                self._on_discovery(record.payload)
            elif record.fonte == "rtds":
                self._on_rtds(record)
            elif record.fonte in ("poly_ws", FONTE_RESOLUCAO_SINTETICA):
                self._on_poly_meta(record)

    def _segunda_passada(self) -> None:
        """Reconstrói os books dos tokens de interesse, dentro da janela deles."""
        if not self.janelas_de_interesse:
            return
        self.progresso.passada("passada 2", arquivos=len(self.reader.files))
        for record in self.reader.iter_records():
            self.progresso.talvez("passada 2", self.reader.total)
            if record.fonte == "poly_ws":
                self._on_poly_book(record)
        self.progresso.terminou("passada 2", self.reader.total)

    # ------------------------------------------------------------- passada 1
    def _on_discovery(self, payload: dict[str, Any]) -> None:
        """Guarda a última visão de cada slug + só as MUDANÇAS de tick.

        A descoberta roda a cada 30s com ~100 janelas: 72h de gravação dariam
        ~900 mil dicts de janela retidos à toa. A medição de tick (M2.E.1) só
        olha transições, então guardar repetição idêntica não acrescenta nada
        — mas a DISTRIBUIÇÃO de tick conta observações, e essa vai à parte,
        no `ticks_vistos`, para não ser falseada pela compactação.
        """
        self.n_snapshots += 1
        janelas = payload.get("janelas")
        if not isinstance(janelas, list):
            return
        mudaram: list[dict[str, Any]] = []
        for janela in janelas:
            if not isinstance(janela, dict):
                continue
            slug = janela.get("slug")
            if not isinstance(slug, str):
                continue
            self.janelas_por_slug[slug] = janela
            tick = janela.get("tick_size")
            if not isinstance(tick, (int, float)) or isinstance(tick, bool):
                continue
            self.ticks_vistos[f"{float(tick):g}"] += 1
            if self._ultimo_tick.get(slug) != float(tick):
                self._ultimo_tick[slug] = float(tick)
                mudaram.append(janela)
        if mudaram:
            self.snapshots.append({"janelas": mudaram})

    def _on_rtds(self, record: ReplayRecord) -> None:
        tick = parse_rtds_event(record.payload, record.ts_mono_ns, record.ts_wall_ns)
        # M2.6 BUG 3.2: o silêncio é detectado aqui, no fluxo, em vez de
        # depois sobre a série — porque a pergunta não é só "quanto tempo
        # ficou mudo", é "MUDOU O QUÊ". Se durante o silêncio do twap_sixty
        # continuaram chegando eventos de OUTRO tópico na mesma conexão,
        # então a conexão estava viva e o que caducou foi a assinatura
        # daquele tópico. Se nada chegou, a conexão inteira emudeceu.
        # As duas causas têm consertos opostos, e o relatório do M2.5 não
        # tinha como distingui-las.
        self._eventos_rtds += 1
        if tick is not None:
            self._topicos_rtds[tick.topic] += 1
            # M2.10 item 2: o contador que SUSTENTA a inferência. O total de
            # eventos RTDS não serve — ele inclui o próprio twap de outros
            # ativos, e um retardatário qualquer fazia uma conexão morta
            # parecer viva. O que prova "a conexão estava viva" é evento de
            # OUTRO tópico chegando na mesma conexão durante o silêncio.
            if tick.topic != TOPIC_TWAP_60:
                self._eventos_de_outros_topicos += 1
        if (
            self._ultimo_rtds_ns
            and record.ts_wall_ns - self._ultimo_rtds_ns > SILENCIO_MIN_NS
        ):
            self._silencios.append(
                {
                    "inicio_ns": self._ultimo_rtds_ns,
                    "fim_ns": record.ts_wall_ns,
                    "duracao_s": round(
                        (record.ts_wall_ns - self._ultimo_rtds_ns) / 1e9, 2
                    ),
                    "escopo": "conexao_inteira",
                    "topico_que_voltou": tick.topic if tick else None,
                }
            )
        self._ultimo_rtds_ns = record.ts_wall_ns

        if tick is not None and tick.topic == TOPIC_TWAP_60:
            self._twap_vistos += 1
            anterior = self._ultimo_twap_ns.get(tick.asset)
            if anterior and record.ts_wall_ns - anterior > SILENCIO_MIN_NS:
                # Silêncio DESTE ativo. Se o contador global de eventos RTDS
                # andou no intervalo, a conexão estava viva.
                andou = self._eventos_rtds - self._eventos_no_ultimo_twap.get(
                    tick.asset, 0
                )
                outros = (
                    self._eventos_de_outros_topicos
                    - self._outros_topicos_no_ultimo_twap.get(tick.asset, 0)
                )
                self._silencios.append(
                    {
                        "inicio_ns": anterior,
                        "fim_ns": record.ts_wall_ns,
                        "duracao_s": round((record.ts_wall_ns - anterior) / 1e9, 2),
                        "escopo": "topico_do_ativo",
                        "asset": tick.asset,
                        "eventos_rtds_durante": andou - 1,
                        "eventos_de_outros_topicos_durante": outros,
                        "base_da_contagem": BASE_DA_CONTAGEM,
                    }
                )
            self._ultimo_twap_ns[tick.asset] = record.ts_wall_ns
            self._eventos_no_ultimo_twap[tick.asset] = self._eventos_rtds
            self._outros_topicos_no_ultimo_twap[tick.asset] = (
                self._eventos_de_outros_topicos
            )
            self.streams[tick.asset].append((record.ts_wall_ns, tick.price))
            # M2.8: o descarte aqui era SILENCIOSO, e e o que explica
            # "janelas com abertura em lacuna" numa gravacao com ZERO
            # silencio do RTDS. `streams` (float) fica denso e
            # `streams_e18` fica ralo — e a ancora usa o segundo.
            valor = _e18_do_payload(record.payload)
            if valor is None:
                self._e18_descartados["sem_valor_exato"] += 1
            elif tick.src_timestamp_ms <= 0:
                self._e18_descartados["sem_carimbo_do_servidor"] += 1
            else:
                self.streams_e18[tick.asset].append(
                    (int(tick.src_timestamp_ms), valor)
                )

    def _on_poly_meta(self, record: ReplayRecord) -> None:
        """Resoluções + integridade. O livro pesado fica para a passada 2.

        A integridade roda AQUI, e não na passada 2, porque ela precisa ver
        TODOS os tokens e TODO o período — inclusive os que a passada 2
        descarta por não pertencerem a janela nenhuma. Um livro corrompido
        num token fora de janela ainda é sinal de que a gravação teve perda.
        """
        for evento in _eventos_do_payload(record.payload):
            carimbo = _numero_bruto(evento.get("timestamp"))
            if carimbo:
                self.relogio.observar(carimbo, record.ts_wall_ns)
            self.integridade.observar(evento, record.ts_wall_ns)
            self._on_resolucao(evento, record.ts_wall_ns)

    def _on_resolucao(self, evento: dict[str, Any], chegada_ns: int) -> None:
        """Indexa a resolução pelos dois caminhos e guarda o instante do evento.

        O instante preferido é o carimbo do SERVIDOR, não a chegada local: o
        que a medição de atraso de liquidação (M2.E.2) quer saber é quanto
        tempo a plataforma levou entre o `endDate` e a publicação do
        resultado, e a latência da nossa rede não faz parte dessa pergunta.
        A chegada local fica como fallback.
        """
        resolucao = resolucao_do_evento(evento)
        if resolucao is None:
            return
        self.eventos_de_resolucao += 1
        if resolucao.sintetico:
            self.resolucoes_sinteticas += 1
        ts_ns = (
            int(resolucao.ts_servidor_ms * 1e6)
            if resolucao.ts_servidor_ms
            else chegada_ns
        )
        if resolucao.condition_id is not None:
            existente = self.resolucoes_por_condicao.get(resolucao.condition_id)
            if existente is not None:
                self.resolucoes_redundantes += 1
                if _discordam(existente, resolucao):
                    self.resolucoes_conflitantes.append(resolucao.condition_id)
                # O evento do fio é mais rico (winning_asset_id) e mais
                # direto (sem a inferência por outcomePrices da Gamma):
                # sintético nunca sobrescreve um evento do fio já visto.
                if resolucao.sintetico and not existente.sintetico:
                    return
            self.resolucoes_por_condicao[resolucao.condition_id] = resolucao
        for token in resolucao.tokens:
            self.resolucoes_por_token[token] = resolucao
            self.resolucoes[token] = ts_ns
        if resolucao.winning_token_id is not None:
            self.resolucoes.setdefault(resolucao.winning_token_id, ts_ns)

        # `resolvido_up` continua significando "o lado Up venceu", e por isso
        # só é preenchido pelo RÓTULO. A identidade do token vencedor decide
        # melhor, mas exige saber qual token é o Up — informação que só a
        # janela tem, e que entra em `janelas()`.
        if isinstance(resolucao.winning_outcome, str):
            venceu_up = resolucao.winning_outcome.strip().lower() == "up"
            for token in resolucao.tokens:
                self.resolvido_up[token] = venceu_up

    def _resolucao_da_janela(
        self, condition_id: str, token_up: str, token_down: str
    ) -> Resolucao | None:
        """Casa a janela com a resolução, por condition id ou por token."""
        chave = normalizar_condition_id(condition_id)
        if chave is not None:
            achada = self.resolucoes_por_condicao.get(chave)
            if achada is not None:
                return achada
        return self.resolucoes_por_token.get(token_up) or self.resolucoes_por_token.get(
            token_down
        )

    def _marcar_tokens_de_interesse(self) -> None:
        for slug, meta in self.janelas_por_slug.items():
            fim_epoch = parse_end_date_epoch({"endDate": meta.get("end_date_iso")})
            if fim_epoch is None:
                continue
            duracao = duracao_do_slug(slug)
            inicio_ns = int((fim_epoch - duracao - PRE_ROLO_S) * 1e9)
            fim_ns = int((fim_epoch + POS_ROLO_S) * 1e9)
            tokens = meta.get("token_id_by_outcome") or {}
            for token in (tokens.get("Up"), tokens.get("Down")):
                if isinstance(token, str):
                    self.janelas_de_interesse[token] = (inicio_ns, fim_ns)

    # ------------------------------------------------------------- passada 2
    def _on_poly_book(self, record: ReplayRecord) -> None:
        # O WS de mercado do CLOB entrega tanto um evento solto quanto um LOTE
        # em array. Tratar só o dict descartaria os lotes em silêncio — e é
        # justamente em rajada de atividade que eles aparecem.
        for evento in _eventos_do_payload(record.payload):
            tipo = evento.get("event_type")
            if tipo == EVENT_LAST_TRADE:
                self._on_trade(evento, record.ts_wall_ns)
                continue
            if tipo not in (EVENT_BOOK, EVENT_PRICE_CHANGE):
                continue
            asset_id = evento.get("asset_id")
            if not isinstance(asset_id, str):
                continue
            intervalo = self.janelas_de_interesse.get(asset_id)
            if intervalo is None or not (intervalo[0] <= record.ts_wall_ns <= intervalo[1]):
                continue
            if tipo == EVENT_BOOK:
                book = OrderBook.from_event(evento)
                if book is None:
                    continue
                self.book_atual[asset_id] = book
            else:
                book = self.book_atual.get(asset_id)
                if book is None:
                    continue
                # Mutação no lugar: o clone por evento existia só para
                # alimentar a timeline, e a timeline agora faz a própria
                # cópia (truncada) quando de fato retém o snapshot.
                book.apply_price_change(evento)
            self._timeline(asset_id).append(book, record.ts_wall_ns)

    def _on_trade(self, evento: dict[str, Any], ts_ns: int) -> None:
        """Execução observada no topo — a matéria-prima do markout (B.2)."""
        asset_id = evento.get("asset_id")
        if not isinstance(asset_id, str) or asset_id not in self.janelas_de_interesse:
            return
        preco = _numero_bruto(evento.get("price"))
        if preco is None:
            return
        self.trades[asset_id].append(
            (
                ts_ns,
                preco,
                _numero_bruto(evento.get("size")) or 0.0,
                str(evento.get("side", "")).upper(),
            )
        )

    def _timeline(self, asset_id: str) -> BookTimeline:
        timeline = self.books.get(asset_id)
        if timeline is None:
            timeline = BookTimeline(
                limite=self.limite_por_token, niveis=self.niveis_retidos
            )
            self.books[asset_id] = timeline
        return timeline

    def resolucoes_resumo(self, janelas: list[WindowState]) -> dict[str, Any]:
        """Quantas resoluções chegaram, quantas casaram, e o que sobrou.

        A contagem de eventos e a de janelas resolvidas são números
        diferentes de propósito: o servidor manda um evento por mercado, e um
        evento pode não corresponder a janela nenhuma que a descoberta viu
        (janela que nasceu antes do recorder subir, por exemplo). Reportar só
        um dos dois esconderia exatamente o defeito que o M2.3 corrigiu.
        """
        casadas = {
            j.slug
            for j in janelas
            if self._resolucao_da_janela(j.condition_id, j.token_up, j.token_down)
            is not None
        }
        condicoes_das_janelas = {
            normalizar_condition_id(j.condition_id)
            for j in janelas
            if normalizar_condition_id(j.condition_id)
        }
        orfas = sorted(
            set(self.resolucoes_por_condicao) - condicoes_das_janelas
        )
        return {
            "eventos_lidos": self.eventos_de_resolucao,
            "eventos_do_fio": self.eventos_de_resolucao - self.resolucoes_sinteticas,
            "sinteticas_via_gamma": self.resolucoes_sinteticas,
            "eventos_redundantes": self.resolucoes_redundantes,
            "conflitos_fio_vs_gamma": sorted(set(self.resolucoes_conflitantes))[:20],
            "mercados_distintos": len(self.resolucoes_por_condicao),
            "janelas_casadas": len(casadas),
            "resolucoes_sem_janela_correspondente": len(orfas),
            "condicoes_orfas": orfas[:20],
            "janelas_com_resolucao_ambigua": self.resolucoes_ambiguas[:20],
            "nota": (
                "Redundância esperada: o fallback da Gamma no recorder grava "
                "um evento por TOKEN (dois por janela) e não reconhece a forma "
                "do fio no seu gate de deduplicação — por isso eventos_lidos "
                "excede mercados_distintos. O leitor deduplica por mercado; "
                "fio vence sintético. `conflitos_fio_vs_gamma` não-vazio "
                "seria anomalia grave. Órfã = resolução de mercado que a "
                "descoberta nunca viu."
            ),
        }

    # ---------------------------------------------------------------- memória
    def silencio_do_rtds(self) -> dict[str, Any]:
        """Os silêncios do feed-verdade, com escopo e leitura (M2.6 BUG 3.2).

        O keepalive do M2.1 resolveu a QUEDA de conexão. Silêncio sem queda é
        outro fenômeno, e este bloco existe para nomeá-lo em vez de deixá-lo
        como "gaps: rtds silencio 837s" no relatório.
        """
        # M2.9: o silencio que dura ate o FIM da gravacao era INVISIVEL. O
        # detector so fecha um silencio quando chega o evento SEGUINTE — e se
        # o topico emudece e nunca mais volta, esse evento nao existe. Foi
        # exatamente o que aconteceu na hora de teste: o twap_sixty parou aos
        # ~30 min e o relatorio disse "0 silencios" com metade da gravacao sem
        # preco-verdade. Um detector que enxerga todo silencio menos o ultimo
        # e pior que nenhum, porque o ultimo e o que mata a gravacao inteira.
        for asset, ultimo in sorted(self._ultimo_twap_ns.items()):
            ocioso_ns = self._ultimo_record_ns - ultimo
            if ocioso_ns > SILENCIO_MIN_NS:
                self._silencios.append(
                    {
                        "inicio_ns": ultimo,
                        "fim_ns": self._ultimo_record_ns,
                        "duracao_s": round(ocioso_ns / 1e9, 2),
                        "escopo": "topico_do_ativo",
                        "asset": asset,
                        "ate_o_fim_da_gravacao": True,
                        "eventos_rtds_durante": self._eventos_rtds
                        - self._eventos_no_ultimo_twap.get(asset, 0),
                        "eventos_de_outros_topicos_durante": (
                            self._eventos_de_outros_topicos
                            - self._outros_topicos_no_ultimo_twap.get(asset, 0)
                        ),
                        "base_da_contagem": BASE_DA_CONTAGEM,
                    }
                )
        # M2.10: o M2.9 consertou METADE. O flush acima percorre so
        # `_ultimo_twap_ns` — escopo `topico_do_ativo`. O detector de
        # `conexao_inteira` (ver `_on_rtds`) continuava fechando silencio so
        # quando chegava o evento seguinte, entao a conexao que emudece e nao
        # volta seguia invisivel. E justamente o escopo que decide o
        # conserto: topico mudo pede reassinatura no recorder, conexao muda
        # pede keepalive/reconexao. Sem este flush o relatorio aponta o
        # conserto errado — foi o que aconteceu na gravacao de 2026-08-22.
        if self._ultimo_rtds_ns:
            ocioso_ns = self._ultimo_record_ns - self._ultimo_rtds_ns
            if ocioso_ns > SILENCIO_MIN_NS:
                self._silencios.append(
                    {
                        "inicio_ns": self._ultimo_rtds_ns,
                        "fim_ns": self._ultimo_record_ns,
                        "duracao_s": round(ocioso_ns / 1e9, 2),
                        "escopo": "conexao_inteira",
                        "topico_que_voltou": None,
                        "ate_o_fim_da_gravacao": True,
                    }
                )
        por_escopo = Counter(str(s["escopo"]) for s in self._silencios)
        conexao = [s for s in self._silencios if s["escopo"] == "conexao_inteira"]
        topico = [s for s in self._silencios if s["escopo"] == "topico_do_ativo"]
        # A separação que decide o conserto: silêncio do tópico COM eventos
        # de outros tópicos chegando = a conexão estava viva.
        #
        # M2.10: `eventos_rtds_durante > 0` sozinho NAO sustenta essa
        # conclusao. Para um silencio que vai ate o fim, o contador e o total
        # de eventos RTDS menos a contagem no ultimo tick daquele ativo —
        # qualquer topico, qualquer ativo. Na gravacao de 2026-08-22 os 8
        # ativos emudeceram dentro de 1s um do outro e o campo veio com 1
        # evento para o btc: o relatorio acusou 7 assinaturas caducadas
        # quando o que houve foi a conexao inteira parando. Um evento
        # retardatario nao e conexao viva.
        #
        # O teste que sustenta a inferencia e a AUSENCIA de silencio de
        # conexao sobreposto: se a conexao ficou muda em qualquer trecho
        # deste silencio, nao da para afirmar que ela estava viva e que o que
        # caducou foi a assinatura.
        #
        # M2.10 item 2 (refinamento): o contador passou a ser
        # `eventos_de_outros_topicos_durante` — evento de topico != twap na
        # MESMA conexao, dentro do intervalo. E o unico que sustenta "a
        # conexao estava viva". `eventos_rtds_durante` fica no relatorio como
        # referencia, mas nao decide mais nada.
        assinatura_caducou = [
            s
            for s in topico
            if (s.get("eventos_de_outros_topicos_durante") or 0) > 0
            and not _tem_sobreposicao(s, conexao)
        ]
        coincidentes = _agrupar_coincidentes(topico)
        return {
            "eventos_rtds": self._eventos_rtds,
            "topicos": dict(self._topicos_rtds),
            "limiar_de_silencio_s": SILENCIO_MIN_NS / 1e9,
            "silencios": len(self._silencios),
            "por_escopo": dict(por_escopo),
            "maior_s": round(
                max((float(s["duracao_s"]) for s in self._silencios), default=0.0), 2
            ),
            # M2.10: UNIAO, nao soma. Os silencios sao por (escopo, ativo) e
            # se sobrepoem: quando os 8 ativos emudecem juntos, somar as
            # oito duracoes contava o MESMO intervalo oito vezes. Na
            # gravacao de 2026-08-22 isso produziu `total_s` de 14.476,91s
            # numa gravacao de 3.600s — um numero que nao pode ser lido como
            # duracao, e que some com a leitura obvia ("quanto tempo fiquei
            # sem preco-verdade?"). A uniao responde essa pergunta.
            "total_s": _duracao_da_uniao_s(self._silencios),
            "total_s_por_escopo": {
                escopo: _duracao_da_uniao_s(
                    [s for s in self._silencios if s["escopo"] == escopo]
                )
                for escopo in sorted(por_escopo)
            },
            "silencios_da_conexao_inteira": conexao[:20],
            "silencios_so_do_topico": topico[:20],
            # M2.10 item 4: oito ativos emudecendo dentro de 1s sao UM evento.
            # Listar oito entradas separadas empurra o leitor para a hipotese
            # de assinatura por ativo, que e a errada.
            "eventos_coincidentes": coincidentes,
            "janela_de_coincidencia_s": JANELA_COINCIDENCIA_NS / 1e9,
            "suspeita_de_assinatura_caducada": len(assinatura_caducou),
            "base_da_contagem_da_suspeita": BASE_DA_CONTAGEM,
            "leitura": (
                "CONEXAO INTEIRA muda = o servidor parou de publicar (ou o "
                "caminho ate ele caiu sem fechar o socket), e o conserto e "
                "keepalive/reconexao. TOPICO DO ATIVO mudo COM "
                "`eventos_rtds_durante` > 0 E SEM silencio de conexao "
                "sobreposto = a conexao estava viva recebendo outros "
                "topicos, logo o que caducou foi a ASSINATURA daquele topico "
                "— e ai o conserto e no recorder (reassinar periodicamente). "
                "M2.10: a sobreposicao entrou na regra porque o contador "
                "sozinho mentia. Ele conta eventos de QUALQUER topico e "
                "QUALQUER ativo depois do ultimo tick, entao um retardatario "
                "fazia uma conexao morta parecer viva — em 2026-08-22 foram "
                "7 assinaturas acusadas com a conexao inteira parada. "
                "`total_s` e UNIAO dos intervalos, nao soma: silencios "
                "simultaneos de 8 ativos sao um intervalo, nao oito. "
                "`suspeita_de_assinatura_caducada` > 0 e o gatilho para essa "
                "correcao. ATENCAO: o recorder NAO pode ser alterado com "
                "gravacao em curso; ver docs/RUNBOOK_VPS.md."
            ),
        }

    def stream_de_ancora(self) -> dict[str, Any]:
        """Quantos ticks do TWAP viraram ponto utilizável pela âncora.

        M2.8 — o achado que a hora de teste do M2.7 revelou. A gravação teve
        **zero** silêncio do RTDS e ainda assim 12 janelas ficaram com a
        abertura "em lacuna" e 20 de 28 sem cobertura do stream. Não é
        contradição: são duas séries diferentes.

        `streams` (float) aceita qualquer tick e fica denso. `streams_e18`
        (inteiro, eixo do carimbo do servidor) — que é a série que a **âncora
        usa** — descarta o tick em dois casos, e até o M2.8 os dois eram
        mudos:

        1. `sem_valor_exato`: sem `full_accuracy_value` e com `value` vindo
           como float. Converter float para e18 seria inventar precisão nos
           dígitos que a varredura de τ existe justamente para enxergar
           (§13.8), então o ponto é recusado — corretamente. O que faltava
           era CONTAR a recusa.
        2. `sem_carimbo_do_servidor`: `timestamp` que não é número (string,
           por exemplo — e o CLOB manda timestamp como string em outros
           eventos, §6.1). Vira 0 e o ponto cai fora.

        Uma taxa de descarte alta aqui explica âncora ausente, janela fora do
        backtest e varredura sem amostra — tudo de uma vez, e sem que o feed
        tenha piscado.
        """
        descartados = sum(self._e18_descartados.values())
        vistos = max(self._twap_vistos, 1)
        pontos = {asset: len(serie) for asset, serie in self.streams_e18.items()}
        return {
            "ticks_twap_vistos": self._twap_vistos,
            "ativos_no_stream": sorted(self.streams_e18),
            "pontos_na_serie_e18": pontos,
            # Todos os ativos, e nao so os que operamos: descobrir que o
            # stream carrega OITO ativos (e nao dois) foi o que explicou a
            # cadencia por ativo ser 2,13s e nao os 0,86s do API_NOTES 13.1
            # — aquele numero e agregado, nao por ativo.
            "cadencia_por_ativo": {
                asset: _cadencia_da_serie(serie)
                for asset, serie in sorted(self.streams_e18.items())
            },
            "idade_maxima_da_amostra_ms": IDADE_MAX_MS,
            "cobertura_da_gravacao": self._cobertura_da_serie(),
            "descartados": dict(self._e18_descartados),
            "descartados_total": descartados,
            "fracao_descartada": round(descartados / vistos, 6),
            "nota": (
                "M2.8. A ancora usa a serie e18 (inteira, eixo do servidor), "
                "nao a serie float. Um tick que chega sadio pelo fio pode nao "
                "virar ponto da serie e18 — e ate agora isso era silencioso. "
                "`fracao_descartada` alta com `silencio_do_rtds.total_s` ZERO "
                "e a explicacao para janela com abertura 'em lacuna' sem que "
                "o feed tenha piscado: a lacuna e da NOSSA serie, nao do feed. "
                "`sem_valor_exato` quer dizer que o payload veio sem "
                "`full_accuracy_value` e com `value` float — recusar e "
                "correto (converter inventaria precisao), mas a consequencia "
                "precisa aparecer. `sem_carimbo_do_servidor` quer dizer "
                "`timestamp` que nao e numero, e ai o conserto e no parser."
            ),
        }

    def _cobertura_da_serie(self) -> dict[str, Any]:
        """Quanto da GRAVAÇÃO a série do preço-verdade cobre (M2.9).

        O número que faltava. Na hora de teste a série e18 tinha 1.687 pontos
        por ativo, cadência de 1 s, **zero** descartes e **zero** buracos
        acima da idade máxima — tudo saudável. E cobria **1.790 s de uma hora
        de gravação**: metade. As 12 janelas sem âncora eram exatamente as que
        abriam depois do último carimbo da série.

        Densidade não é cobertura. Uma série pode ser impecável no trecho que
        existe e simplesmente não existir na outra metade — e todos os
        diagnósticos anteriores olhavam só o trecho que existe.
        """
        span_ns = self._ultimo_record_ns - self._primeiro_record_ns
        if span_ns <= 0 or not self.streams_e18:
            return {"gravacao_s": 0.0}
        gravacao_s = span_ns / 1e9
        # Um ponto vale por `IDADE_MAX_MS` e nada além disso: amostra mais
        # velha que a idade máxima não descreve o instante, e é essa a regra
        # que o resto do relatório já usa para dizer que uma janela abriu "em
        # lacuna". A cobertura tem de usar a MESMA régua.
        idade_max_ms = float(IDADE_MAX_MS)
        por_ativo = {}
        for asset, serie in sorted(self.streams_e18.items()):
            if not serie:
                continue
            carimbos = sorted(ts for ts, _ in serie)
            # Soma dos intervalos, cada um limitado à idade máxima. Um buraco
            # de uma hora entra como 10 s, não como uma hora.
            coberto_ms = 0.0
            buracos_ms = 0.0
            maior_buraco_ms = 0.0
            for antes, depois in pairwise(carimbos):
                delta = float(depois - antes)
                coberto_ms += min(delta, idade_max_ms)
                if delta > idade_max_ms:
                    buracos_ms += delta - idade_max_ms
                    maior_buraco_ms = max(maior_buraco_ms, delta)
            # O primeiro ponto também cobre o seu próprio intervalo de validade.
            coberto_ms += idade_max_ms
            coberto = coberto_ms / 1000.0
            # ms do servidor → ns de parede, para comparar os dois eixos.
            # `carimbos` já está ordenado: indexar as pontas evita duas
            # varreduras completas por ativo, que a 68 mil pontos vezes oito
            # ativos custam mais de um milhão de comparações à toa.
            inicio_ns = carimbos[0] * 1_000_000
            fim_ns = carimbos[-1] * 1_000_000
            por_ativo[asset] = {
                "coberto_s": round(coberto, 1),
                "fracao_da_gravacao": round(min(coberto / gravacao_s, 1.0), 4),
                "silencio_inicial_s": round(
                    max(0.0, (inicio_ns - self._primeiro_record_ns) / 1e9), 1
                ),
                "silencio_final_s": round(
                    max(0.0, (self._ultimo_record_ns - fim_ns) / 1e9), 1
                ),
                "buracos_s": round(buracos_ms / 1000.0, 1),
                "maior_buraco_s": round(maior_buraco_ms / 1000.0, 1),
            }
        pior = min(
            (v["fracao_da_gravacao"] for v in por_ativo.values()), default=0.0
        )
        return {
            "gravacao_s": round(gravacao_s, 1),
            "por_ativo": por_ativo,
            "pior_fracao_coberta": pior,
            "nota": (
                "M2.9. DENSIDADE NAO E COBERTURA. `cadencia_por_ativo` olha o "
                "trecho que EXISTE e pode estar impecavel — cadencia de 1s, "
                "zero descarte, zero buraco — enquanto a serie simplesmente "
                "nao existe na outra metade da gravacao. "
                "`silencio_final_s` alto quer dizer que o topico emudeceu e "
                "NAO voltou ate o fim do arquivo: toda janela que abrir depois "
                "disso fica sem ancora. Foi o caso da hora de teste, com "
                "`fracao_da_gravacao` de 0,50. "
                "M2.13: `coberto_s` e a SOMA dos intervalos, cada um limitado "
                "a `idade_maxima_da_amostra_ms` — nao o span do primeiro ao "
                "ultimo carimbo. A conta antiga contava buraco interno como "
                "coberto: na rodada de 20h ela publicou 1,0 nos oito ativos "
                "enquanto o mesmo relatorio registrava 3.601 s de silencio de "
                "conexao inteira. `buracos_s` e `maior_buraco_s` dizem QUANTO "
                "se perdeu por dentro, e `silencio_inicial_s` fecha a borda "
                "que faltava — `silencio_final_s` sozinho so via a de tras."
            ),
        }

    def uso_de_memoria(self) -> dict[str, Any]:
        """O que foi retido e o que foi descartado — o relatório precisa dizer."""
        retidos = sum(len(t.ts) for t in self.books.values())
        descartados = sum(t.descartados for t in self.books.values())
        raleados = [t for t in self.books.values() if t.raleamentos]
        resolucoes_ms = sorted(
            t.resolucao_ns / 1e6 for t in self.books.values() if t.resolucao_ns
        )
        return {
            "tokens_de_interesse": len(self.janelas_de_interesse),
            "tokens_com_book": len(self.books),
            "snapshots_retidos": retidos,
            "snapshots_descartados": descartados,
            "limite_por_token": self.limite_por_token,
            "niveis_retidos_por_lado": self.niveis_retidos,
            "tokens_raleados": len(raleados),
            "pior_resolucao_ms": round(resolucoes_ms[-1], 1) if resolucoes_ms else 0.0,
            "projecao_de_pico": self._projecao_de_pico(),
            "nota": (
                "Books truncados aos N níveis do topo e raleados ao estourar o "
                "limite por token. `pior_resolucao_ms` acima de 150 significa que "
                "o cenário de latência mais baixo já não é distinguível. "
                "`projecao_de_pico` diz quanto a passada 2 custaria SEM o "
                "raleamento, que é o número que decide se a gravação cabe na "
                "máquina — ver docs/RUNBOOK_VPS.md §7.1."
            ),
        }

    #: Custo medido de um snapshot de book retido, em bytes: a tupla de
    #: (ts, bids, asks) com N níveis por lado, já contando o overhead dos
    #: objetos Python. Aferido no M2.2 (81 MB de pico para ~2M de eventos) e
    #: usado só para PROJETAR — o número real sai do `snapshots_retidos`.
    BYTES_POR_SNAPSHOT_NIVEL = 120

    def _projecao_de_pico(self) -> dict[str, Any]:
        """Quanto a passada 2 custaria com o teto atual, se ele fosse atingido.

        A pergunta que isto responde é a do M2.5 tarefa 6: por que uma
        gravação de ~24 GB com `--limite-por-token 20000` não termina numa
        máquina comum. A conta é direta e o resultado é brutal — teto por
        token × tokens de interesse × níveis × 2 lados. Com 3.700 tokens em
        72h e teto de 20.000, são dezenas de GB só de book.

        O `snapshots_descartados` conta o que o raleamento já jogou fora; se
        ele for grande, o pico projetado é o que a rodada TERIA custado.
        """
        tokens = max(1, len(self.janelas_de_interesse))
        por_token = self.limite_por_token * self.niveis_retidos * 2
        bytes_pico = tokens * por_token * self.BYTES_POR_SNAPSHOT_NIVEL
        return {
            "tokens_de_interesse": tokens,
            "teto_de_snapshots_por_token": self.limite_por_token,
            "bytes_estimados": bytes_pico,
            "gib_estimados": round(bytes_pico / 1024**3, 2),
            "nota": (
                "Estimativa do pico da passada 2 com o teto atual TOTALMENTE "
                "ocupado. Nao e medicao: e o teto de gasto que os flags "
                "autorizam. Acima da RAM da maquina, rode por fatias de hora "
                "com --desde/--ate em vez de subir o teto."
            ),
        }

    # ------------------------------------------------------------------ janelas
    def janelas(self) -> list[WindowState]:
        """Última visão de cada janela nos snapshots, virando WindowState."""
        saida: list[WindowState] = []
        for slug, meta in self.janelas_por_slug.items():
            tokens = meta.get("token_id_by_outcome") or {}
            token_up, token_down = tokens.get("Up"), tokens.get("Down")
            if not isinstance(token_up, str) or not isinstance(token_down, str):
                continue
            fim_epoch = parse_end_date_epoch({"endDate": meta.get("end_date_iso")})
            if fim_epoch is None:
                continue
            achada = self._resolucao_da_janela(
                str(meta.get("condition_id") or ""), token_up, token_down
            )
            resolucao = (
                achada.venceu_up(token_up, token_down) if achada is not None else None
            )
            if achada is not None and resolucao is None:
                # O evento chegou mas não permite decidir o lado. Isso é
                # anomalia, não ausência: some da contagem de resoluções e
                # aparece no relatório com o slug.
                self.resolucoes_ambiguas.append(slug)
            if achada is not None:
                self.resolvido_up[token_up] = bool(resolucao)
            duracao = duracao_do_slug(slug)
            janela = WindowState(
                slug=slug,
                jogo="horario" if meta.get("resolution") == "binance_candle" else "twap",
                asset=str(meta.get("asset") or ""),
                duracao_s=duracao,
                condition_id=str(meta.get("condition_id") or ""),
                token_up=token_up,
                token_down=token_down,
                tick_size=float(meta.get("tick_size") or 0.01),
                min_order_size=float(meta.get("min_order_size") or 5),
                fee_rate=float(meta.get("fee_rate") or 0.0),
                fee_exponent=float(meta.get("fee_exponent") or 1.0),
                fee_rebate_rate=float(meta.get("fee_rebate_rate") or 0.0),
                open_ts_ns=int((fim_epoch - duracao) * 1e9),
                close_ts_ns=int(fim_epoch * 1e9),
                resolveu_up=resolucao,
            )
            janela.books[token_up] = self.books.get(token_up, BookTimeline())
            janela.books[token_down] = self.books.get(token_down, BookTimeline())
            janela.reward_meta = {
                "rewards_daily_rate": meta.get("rewards_daily_rate"),
                "rewards_min_size": meta.get("rewards_min_size"),
                "rewards_max_spread": meta.get("rewards_max_spread"),
                "tick_size": meta.get("tick_size"),
                # M2.7: o cru da lista de rewards, para o relatório distinguir
                # "não participa" de "expirou" de "erramos a chave".
                "rewards_bruto": meta.get("rewards_bruto"),
                # A duração vem da JANELA, não do snapshot: o recorder não
                # grava esse campo, e `meta.get` devolveria None sempre —
                # o que faria a tabela por duração sair inteira em "?".
                "duracao_s": janela.duracao_s,
            }
            janela.trades = sorted(
                self.trades.get(token_up, []) + self.trades.get(token_down, [])
            )
            saida.append(janela)
        return saida


def _discordam(a: Resolucao, b: Resolucao) -> bool:
    """As duas resoluções do mesmo mercado apontam vencedores diferentes?

    Compara o que for comparável: identidade de token quando ambas a têm,
    senão o rótulo. Ausência de informação num dos lados não é conflito.
    """
    if a.winning_token_id and b.winning_token_id:
        return a.winning_token_id != b.winning_token_id
    ra = (a.winning_outcome or "").strip().lower()
    rb = (b.winning_outcome or "").strip().lower()
    return bool(ra and rb and ra != rb)


def _cadencia_da_serie(serie: list[tuple[int, int]]) -> dict[str, Any]:
    """A cadência REAL da série e18 de um ativo, no eixo do servidor.

    M2.8 — o que a hora de teste obrigou a medir. A hipótese de que a série
    estava rala por descarte foi REFUTADA (`fracao_descartada: 0.0`), e ainda
    assim 12 de 28 janelas ficaram com a abertura sem âncora. Sobra uma
    explicação testável: `em()` recusa a amostra mais velha que
    `IDADE_MAX_MS`, e o que importa não é quantos PONTOS a série tem, é
    quantos CARIMBOS DISTINTOS ela tem e como eles se espaçam.

    Se o RTDS republica o mesmo valor da Chainlink várias vezes com o MESMO
    `timestamp` — o que é plausível, porque a TWAP on-chain atualiza no ritmo
    dela e não no ritmo do republicador —, a série pode ter 1.687 pontos e
    poucas centenas de instantes distintos, com buracos de dezenas de
    segundos entre eles. Densa em pontos, rala em tempo.

    `maior_intervalo_s` acima de `IDADE_MAX_MS/1000` é a prova: qualquer
    janela que abra dentro de um buraco desses fica sem âncora, com o feed
    perfeitamente saudável.
    """
    if not serie:
        return {"pontos": 0}
    carimbos = sorted({ts for ts, _ in serie})
    if len(carimbos) < 2:
        return {"pontos": len(serie), "carimbos_distintos": len(carimbos)}
    intervalos = [(b - a) / 1000.0 for a, b in pairwise(carimbos)]
    ordenados = sorted(intervalos)
    span = (carimbos[-1] - carimbos[0]) / 1000.0
    return {
        "pontos": len(serie),
        "carimbos_distintos": len(carimbos),
        "repeticoes_do_mesmo_carimbo": len(serie) - len(carimbos),
        "janela_coberta_s": round(span, 1),
        "intervalo_s": {
            "p50": round(_percentil_simples(ordenados, 50), 3),
            "p90": round(_percentil_simples(ordenados, 90), 3),
            "p99": round(_percentil_simples(ordenados, 99), 3),
            "max": round(ordenados[-1], 3),
        },
        "buracos_acima_da_idade_maxima": sum(
            1 for i in intervalos if i * 1000 > IDADE_MAX_MS
        ),
    }


def _percentil_simples(ordenados: list[float], pct: float) -> float:
    rank = max(1, min(len(ordenados), int(-(-pct * len(ordenados) // 100))))
    return ordenados[rank - 1]


def _e18_do_payload(bruto: Any) -> int | None:
    """O valor do tick em INTEIRO na escala 1e18, sem passar por float.

    Preferência: `full_accuracy_value` (string inteira já escalada — o campo
    que o SDK também prefere). Fallback: `value` decimal, convertido de forma
    EXATA via Decimal — `int(float(x) * 1e18)` erraria os últimos dígitos,
    que são exatamente os que a varredura de τ existe para enxergar.
    """
    if not isinstance(bruto, dict):
        return None
    payload = bruto.get("payload")
    if not isinstance(payload, dict):
        return None
    fav = payload.get("full_accuracy_value")
    if isinstance(fav, str):
        try:
            return int(fav)
        except ValueError:
            pass
    valor = payload.get("value")
    if isinstance(valor, (int, str)) and not isinstance(valor, bool):
        try:
            return int(Decimal(str(valor)).scaleb(18))
        except (InvalidOperation, ValueError):
            return None
    if isinstance(valor, float):
        # Float já perdeu os dígitos finais na origem; converter é criar
        # precisão falsa. Melhor ponto nenhum que ponto mentiroso.
        return None
    return None


def _numero_bruto(valor: Any) -> float | None:
    """O CLOB manda número ora como int, ora como string decimal."""
    if isinstance(valor, bool) or valor is None:
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    if isinstance(valor, str):
        try:
            return float(valor)
        except ValueError:
            return None
    return None


def _eventos_do_payload(payload: Any) -> list[dict[str, Any]]:
    """O CLOB manda ora um evento solto, ora um lote em array."""
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return [e for e in payload if isinstance(e, dict)]
    return []


def _rebate_medio(janelas: list[WindowState]) -> float:
    """Taxa de rebate do maker, lida do dado gravado — nunca constante.

    Sem janela com o campo, devolve 0: melhor uma conta que não credita
    rebate nenhum do que uma que credita um número inventado.
    """
    taxas = [j.fee_rebate_rate for j in janelas if getattr(j, "fee_rebate_rate", None)]
    return sum(taxas) / len(taxas) if taxas else 0.0


def _medio_do_dado(janelas: list[WindowState], campo: str) -> float:
    """Média de um parâmetro de mercado lido das janelas. 0 se ninguém tem.

    Mesmo princípio de `_rebate_medio`: parâmetro de mercado vem do dado
    gravado, nunca de constante no código. Zero é o sinal de "não sei", e
    quem consome decide o que fazer com ele — em vez de receber um default
    plausível que some no meio de uma conta.
    """
    valores = [
        float(getattr(j, campo)) for j in janelas if getattr(j, campo, None)
    ]
    return sum(valores) / len(valores) if valores else 0.0


#: Saída do backtest quando a âncora verificada deixa de explicar as
#: resoluções. Código próprio (não 1, não 2) para que um laço de shell
#: consiga distinguir "deu erro de uso" de "a plataforma mudou a regra".
CODIGO_ANCORA_INVALIDA = 3

#: Teto de tempo restante dos buckets em que o modelo TEM calibração.
#: Medido sobre 4h reais (2026-08-20 12h–15h): erro de −0,008 em
#: 240–120s e −0,240 em >240s. Não é escolha de gosto — é a fronteira
#: onde o modelo passa a errar 24 pontos de probabilidade.
TEMPO_CALIBRADO_MAX_S = 240.0

#: A partir de quanto tempo sem evento do RTDS isso vira "silêncio".
#: A cadência medida é ~0,86s (p50, API_NOTES §13.1); 30s são ~35 vezes
#: isso — folgado o bastante para não confundir respiro de rede com
#: parada, e curto o bastante para pegar as lacunas de 14 e 17 minutos
#: que apareceram em 4h de gravação real.
SILENCIO_MIN_NS = 30 * 10**9


#: M2.10 item 4. Silêncios que começam dentro desta janela um do outro são
#: tratados como UM evento. 5 s: os oito ativos da gravação de 2026-08-22
#: emudeceram dentro de 1,0 s, e uma janela dessa ordem separa "pararam
#: juntos" de "pararam em momentos diferentes" sem depender de sorte no
#: agendamento do publicador.
JANELA_COINCIDENCIA_NS = 5 * 10**9

#: O que `eventos_de_outros_topicos_durante` conta. Vai no próprio campo
#: porque a inferência inteira depende de qual população foi contada — foi
#: exatamente aí que o diagnóstico de 2026-08-22 se perdeu.
BASE_DA_CONTAGEM = (
    "eventos RTDS de topicos != crypto_prices_twap_sixty, na mesma conexao, "
    "dentro do intervalo do silencio. NAO inclui o proprio twap de outros "
    "ativos: quando os oito param juntos, o twap alheio nao prova conexao "
    "viva. `eventos_rtds_durante` (populacao antiga, qualquer topico e "
    "qualquer ativo) fica no relatorio para comparacao e nao decide nada."
)


def _agrupar_coincidentes(
    silencios: list[dict[str, Any]],
    *,
    janela_ns: int = JANELA_COINCIDENCIA_NS,
) -> list[dict[str, Any]]:
    """Silêncios que começam quase juntos são um evento só.

    M2.10 item 4. Na gravação de 2026-08-22 os oito ativos emudeceram entre
    16:29:49,87 (zec) e 16:29:50,83 (doge) — 0,96 s de dispersão. O relatório
    listava oito silêncios separados, e ler oito linhas com oito nomes de
    ativo empurra para a hipótese de assinatura caducando por ativo, que é a
    errada: oito assinaturas não caducam dentro do mesmo segundo.

    As entradas individuais continuam em `silencios_so_do_topico`; isto é uma
    visão por cima, não uma substituição.
    """
    ordenados = sorted(silencios, key=lambda s: int(s.get("inicio_ns") or 0))
    grupos: list[list[dict[str, Any]]] = []
    for silencio in ordenados:
        inicio = int(silencio.get("inicio_ns") or 0)
        if grupos and inicio - int(grupos[-1][0].get("inicio_ns") or 0) <= janela_ns:
            grupos[-1].append(silencio)
        else:
            grupos.append([silencio])
    saida = []
    for grupo in grupos:
        if len(grupo) < 2:
            continue
        inicios = [int(s.get("inicio_ns") or 0) for s in grupo]
        fins = [int(s.get("fim_ns") or 0) for s in grupo]
        saida.append(
            {
                "inicio_ns": min(inicios),
                "fim_ns": max(fins),
                "ativos": sorted(
                    {str(s.get("asset")) for s in grupo if s.get("asset")}
                ),
                "quantos_ativos": len({s.get("asset") for s in grupo}),
                "dispersao_do_inicio_s": round((max(inicios) - min(inicios)) / 1e9, 3),
                "ate_o_fim_da_gravacao": all(
                    bool(s.get("ate_o_fim_da_gravacao")) for s in grupo
                ),
                "leitura": (
                    "Ativos que emudeceram dentro da janela de coincidencia. "
                    "Dispersao de poucos segundos com muitos ativos NAO e "
                    "assinatura caducando por ativo — assinaturas nao caducam "
                    "em bloco. Aponta para uma causa acima do ativo: a "
                    "conexao, o publicador, ou o caminho ate ele."
                ),
            }
        )
    return saida


def _tem_sobreposicao(
    silencio: dict[str, Any], outros: list[dict[str, Any]]
) -> bool:
    """Algum dos `outros` cobre parte do intervalo de `silencio`?

    M2.10. É o teste que sustenta — ou derruba — a inferência de assinatura
    caducada. Dizer "a conexão estava viva, logo caducou a assinatura deste
    tópico" exige que a conexão não tenha ficado muda DENTRO do silêncio do
    tópico. Se ficou, as duas explicações são indistinguíveis, e a honesta é
    não escolher.
    """
    inicio = int(silencio.get("inicio_ns") or 0)
    fim = int(silencio.get("fim_ns") or 0)
    return any(
        int(o.get("inicio_ns") or 0) < fim and int(o.get("fim_ns") or 0) > inicio
        for o in outros
    )


def _duracao_da_uniao_s(silencios: list[dict[str, Any]]) -> float:
    """Quanto tempo esteve coberto por ALGUM destes silêncios, em segundos.

    M2.10. Somar as durações conta o mesmo intervalo uma vez por ativo
    quando todos emudecem juntos — e foi assim que uma gravação de 3.600s
    reportou 14.476,91s de silêncio. A união responde a pergunta que
    alguém realmente faz ao ler o campo: *quanto tempo eu fiquei sem
    preço-verdade?*
    """
    intervalos = sorted(
        (int(s.get("inicio_ns") or 0), int(s.get("fim_ns") or 0))
        for s in silencios
        if int(s.get("fim_ns") or 0) > int(s.get("inicio_ns") or 0)
    )
    total_ns = 0
    fim_corrente = None
    inicio_corrente = 0
    for inicio, fim in intervalos:
        if fim_corrente is None or inicio > fim_corrente:
            if fim_corrente is not None:
                total_ns += fim_corrente - inicio_corrente
            inicio_corrente, fim_corrente = inicio, fim
        elif fim > fim_corrente:
            fim_corrente = fim
    if fim_corrente is not None:
        total_ns += fim_corrente - inicio_corrente
    return round(total_ns / 1e9, 2)


def montar_ancoras(
    resolvidas: list[WindowState], streams_e18: dict[str, list[tuple[int, int]]]
) -> dict[str, Any]:
    """Preenche `janela.ancora` com a âncora VERIFICADA e conta as lacunas.

    M2.6 BUG 1: até o M2.5 o simulador operava com a hipótese sobrevivente
    (`ultimo_antes`, ~90% de acerto), enquanto a varredura no MESMO relatório
    dizia 100% para τ=0 — ou seja, o PnL saía de resoluções que sabíamos
    parcialmente erradas. Agora a fonte é a âncora VERIFICADA (API_NOTES
    §13.8): valor do stream `twap_sixty` no instante da abertura, eixo de
    carimbo do SERVIDOR, inteiro e18.

    As hipóteses nomeadas continuam sendo calculadas e reportadas — como
    REFERÊNCIA HISTÓRICA, para que a diferença entre o que se supunha e o que
    se mediu continue visível. Nenhuma delas decide coisa alguma.

    Devolve o bloco `lacunas_do_stream` do relatório (M2.6 BUG 3).
    """
    series_e18 = {
        asset: StreamE18(amostras) for asset, amostras in streams_e18.items()
    }
    lacunas_na_abertura: list[str] = []
    lacunas_no_fechamento: list[str] = []
    sem_stream: list[str] = []
    for janela in resolvidas:
        serie = series_e18.get(janela.asset)
        if serie is None:
            sem_stream.append(janela.slug)
            continue
        abertura_e18 = ancora_verificada(serie, janela.open_ts_ns // 1_000_000)
        if abertura_e18 is None:
            # Lacuna do RTDS no instante da abertura. A âncora fica `None` e o
            # runner pula a janela — mas isso precisa ser CONTADO, senão a
            # exclusão vira número que some.
            lacunas_na_abertura.append(janela.slug)
            continue
        if valor_final(serie, janela.close_ts_ns // 1_000_000) is None:
            # O fechamento em lacuna não estraga a âncora, mas estraga a
            # calibração: o modelo é medido contra um desfecho cujo
            # preço-verdade não foi gravado.
            lacunas_no_fechamento.append(janela.slug)
            continue
        # e18 → float só aqui, e só para o modelo de probabilidade. A decisão
        # Up/Down inteira mora na varredura; esta conversão não decide nada.
        janela.ancora = abertura_e18 / 1e18

    return {
        "janelas_com_abertura_em_lacuna": len(lacunas_na_abertura),
        "janelas_com_fechamento_em_lacuna": len(lacunas_no_fechamento),
        "janelas_sem_stream_do_ativo": len(sem_stream),
        "idade_maxima_da_amostra_ms": IDADE_MAX_MS,
        "exemplos": {
            "abertura": sorted(lacunas_na_abertura)[:10],
            "fechamento": sorted(lacunas_no_fechamento)[:10],
        },
        "nota": (
            "M2.6 BUG 3. Janela cujo instante critico cai em silencio do RTDS "
            "sai do backtest de fills: sem preco-verdade naquele instante a "
            "ancora seria um valor velho, e o PnL sairia de uma comparacao "
            "que nunca aconteceu. `idade_maxima_da_amostra_ms` e o que define "
            "'lacuna' — amostra mais velha que isso nao descreve o instante."
        ),
    }


def _hora_utc(bruto: str | None) -> datetime | None:
    """`YYYYMMDDHH`, `YYYY-MM-DD`, ou ISO completo. Sempre UTC.

    Fuso implícito seria a pior armadilha possível aqui: a fatia sairia
    deslocada e o relatório continuaria bonito, descrevendo horas erradas.
    """
    if not bruto:
        return None
    texto = bruto.strip()
    for formato in ("%Y%m%d%H", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(texto, formato).replace(tzinfo=UTC)
        except ValueError:
            continue
    try:
        lido = datetime.fromisoformat(texto)
    except ValueError as erro:
        raise ValueError(
            f"hora invalida: {bruto!r}. Use YYYYMMDDHH, YYYY-MM-DD ou ISO 8601."
        ) from erro
    return lido if lido.tzinfo else lido.replace(tzinfo=UTC)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PULSEARB backtest — M2.D + M2.E")
    parser.add_argument("recordings", help="diretório (ou arquivo) da gravação")
    parser.add_argument("--threshold", type=float, default=0.02)
    parser.add_argument("--latencia-ms", type=float, default=300.0)
    parser.add_argument(
        "--json",
        help=(
            "caminho RELATIVO para o relatorio completo, terminando em "
            ".json (ex.: relatorios/2026-08-20-13.json). A raiz e o "
            "diretorio de trabalho; para outra, defina "
            "PULSEARB_BACKTEST_OUTPUT_ROOT. Caminho absoluto e recusado."
        ),
    )
    # LIMITES DE MEMÓRIA — defaults dimensionados para a VPS de 1 GB e
    # deliberadamente conservadores. Na máquina de ANÁLISE eles sufocam a
    # simulação: a rodada real de 2026-08-19 descartou 42% dos snapshots e
    # ficou com resolução efetiva de ~1,9s, o que torna o cenário de latência
    # de 300ms indistinguível. Num Mac com memória de sobra, suba-os
    # (recomendação no runbook §7). Env cobre o caso de quem roda via make ou
    # script sem tocar na linha de comando; o flag explícito vence o env.
    parser.add_argument(
        "--limite-snapshots",
        "--limite-por-token",
        dest="limite_snapshots",
        type=int,
        default=int(
            os.environ.get("PULSEARB_BACKTEST_LIMITE_POR_TOKEN", LIMITE_SNAPSHOTS_PADRAO)
        ),
        help=(
            "teto de snapshots de book por token (memória; ver BookTimeline). "
            f"Default {LIMITE_SNAPSHOTS_PADRAO}, dimensionado para 1 GB de RAM; "
            "na máquina de análise use 20000+. Env: "
            "PULSEARB_BACKTEST_LIMITE_POR_TOKEN"
        ),
    )
    parser.add_argument(
        "--niveis-book",
        "--niveis-por-lado",
        dest="niveis_book",
        type=int,
        default=int(
            os.environ.get("PULSEARB_BACKTEST_NIVEIS_POR_LADO", NIVEIS_RETIDOS_PADRAO)
        ),
        help=(
            "níveis do topo retidos por lado em cada snapshot. Default "
            f"{NIVEIS_RETIDOS_PADRAO}. Env: PULSEARB_BACKTEST_NIVEIS_POR_LADO"
        ),
    )
    parser.add_argument(
        "--fator-de-encolhimento",
        dest="fator_de_encolhimento",
        type=_fator_de_encolhimento_valido,
        default=None,
        help=(
            "correção de escala da calibração: p' = 0,5 + fator*(p - 0,5), "
            "aplicada antes de TUDO (calibração inclusive). Sai como "
            "comparação ao lado do resultado normal, nunca no lugar dele. "
            "O fator deve vir de calibração medida em período ANTERIOR ao "
            "avaliado — ajustá-lo no próprio período é ajuste in-sample."
        ),
    )
    parser.add_argument(
        "--qualidade-minima",
        dest="qualidade_minima",
        choices=("alta", "media", "baixa"),
        default=os.environ.get("PULSEARB_BACKTEST_QUALIDADE_MINIMA", "media"),
        help=(
            "marca minima de qualidade do livro para a janela entrar no "
            "backtest de fills. Default `media`. `baixa` inclui tudo e serve "
            "para medir o custo do corte. Criterios em VEREDITO_M2 §2c. Env: "
            "PULSEARB_BACKTEST_QUALIDADE_MINIMA"
        ),
    )
    parser.add_argument(
        "--ticks-divergencia",
        dest="ticks_divergencia",
        type=int,
        default=int(os.environ.get("PULSEARB_BACKTEST_TICKS_DIVERGENCIA", "2")),
        help=(
            "K: quantos ticks de mercado (0,01) uma divergencia precisa ter "
            "para ser candidata a corrupcao. Nunca menor que 2 — 1 tick e o "
            "p50 observado, ou seja, o ruido da corrida entre best_bid_ask e "
            "price_change. Env: PULSEARB_BACKTEST_TICKS_DIVERGENCIA"
        ),
    )
    # M2.5 tarefa 6: processamento por FATIA de hora. A gravação de 72h dá
    # ~24 GB e a passada 2 não cabe numa máquina comum com o teto de
    # snapshots que a análise exige (ver `memoria.projecao_de_pico`). Cada
    # hora cabe folgada, e as janelas de 5m/15m vivem dentro de uma hora — a
    # margem de ±1h no seletor de arquivos cobre quem cruza a virada.
    parser.add_argument(
        "--desde",
        dest="desde",
        default=None,
        help=(
            "processa so a fatia a partir desta hora UTC (YYYYMMDDHH ou ISO). "
            "Le uma hora a mais de cada lado, porque o nome do arquivo e "
            "aproximacao da hora do evento."
        ),
    )
    parser.add_argument(
        "--ate",
        dest="ate",
        default=None,
        help="fim da fatia, mesmo formato de --desde (inclusivo).",
    )
    # M2.6 BUG 2: faixa de tempo restante em que se pode operar.
    parser.add_argument(
        "--tempo-restante-max",
        dest="tempo_restante_max",
        type=float,
        default=None,
        help=(
            "so opera quando faltarem ATE X segundos para o fechamento. A "
            "calibracao real mede erro de -0,008 em 240-120s contra -0,240 em "
            ">240s: restringir a faixa e operar onde o modelo sabe. Sem o "
            "flag, opera em qualquer instante (comportamento ate o M2.5)."
        ),
    )
    parser.add_argument(
        "--tempo-restante-min",
        dest="tempo_restante_min",
        type=float,
        default=None,
        help=(
            "so opera quando faltarem PELO MENOS X segundos. Util para sair "
            "do fim da janela, onde o book afina e o fill fica caro."
        ),
    )
    # M2.7 tarefa 3: mais de uma entrada por janela.
    parser.add_argument(
        "--max-entradas-por-janela",
        dest="max_entradas",
        type=int,
        default=1,
        help=(
            "quantas entradas a v1 pode fazer por janela. Default 1 "
            "(comportamento ate o M2.6), que produziu 18 trades sobre 1.617 "
            "instantes com sinal — longe dos 200 que o VEREDITO exige."
        ),
    )
    parser.add_argument(
        "--intervalo-entre-entradas",
        dest="intervalo_entradas",
        type=float,
        default=30.0,
        help=(
            "espacamento minimo entre entradas, em segundos. Ticks "
            "consecutivos com sinal sao a MESMA oportunidade vista de novo; "
            "sem espacamento o PnL somaria a mesma aposta repetida."
        ),
    )
    parser.add_argument(
        "--varredura-de-tamanho",
        dest="varredura_de_tamanho",
        default=None,
        help=(
            "lista de tamanhos em shares, separados por virgula, para a curva "
            "de CAPACIDADE (M2.14) — por exemplo 5,10,25,50,100,200. Cada "
            "tamanho e uma passada completa do backtest, entao a rodada fica "
            "N vezes mais longa: fica DESLIGADA por default de proposito. "
            "Use 'padrao' para a grade padrao. Responde ao criterio 1.5 do "
            "VEREDITO_M2 medindo direto se a borda por share sobrevive ao "
            "tamanho, em vez de inferir de um p50 de profundidade."
        ),
    )
    args = parser.parse_args(argv)

    tamanhos_da_varredura: tuple[float, ...] = ()
    if args.varredura_de_tamanho:
        if args.varredura_de_tamanho.strip().lower() == "padrao":
            tamanhos_da_varredura = TAMANHOS_PADRAO
        else:
            try:
                tamanhos_da_varredura = tuple(
                    float(pedaco)
                    for pedaco in args.varredura_de_tamanho.split(",")
                    if pedaco.strip()
                )
            except ValueError:
                parser.error(
                    "--varredura-de-tamanho espera numeros separados por "
                    f"virgula, ou 'padrao'; recebi {args.varredura_de_tamanho!r}"
                )
            if any(t <= 0 for t in tamanhos_da_varredura):
                parser.error("--varredura-de-tamanho so aceita tamanhos positivos")

    try:
        desde = _hora_utc(args.desde)
        ate = _hora_utc(args.ate)
    except ValueError as erro:
        print(str(erro), file=sys.stderr)
        return 2

    try:
        caminho = caminho_de_leitura(args.recordings)
        destino = caminho_de_escrita(args.json) if args.json else None
    except ValueError as erro:
        print(str(erro), file=sys.stderr)
        return 2

    reader = RecordingReader(caminho, desde=desde, ate=ate)
    index = RecordingIndex(
        reader,
        limite_por_token=max(2, args.limite_snapshots),
        niveis_retidos=max(1, args.niveis_book),
    )
    index.integridade.ticks_divergencia = max(2, args.ticks_divergencia)
    index.build()

    if not index.n_snapshots:
        print(
            "nenhum snapshot de descoberta na gravação — sem metadados de janela\n"
            "não há backtest possível. Rode o recorder primeiro:\n"
            "    python -m pulsearb.recorder --duration 72h",
            file=sys.stderr,
        )
        return 1

    janelas = index.janelas()
    # ORDEM DOS FILTROS — foi ela que zerou o M2.3 na primeira rodada real.
    # A versão anterior aplicava o filtro de integridade ANTES de contar as
    # resoluções e de alimentar a âncora: 26 janelas casaram, todas tinham
    # livro invalidado, e o relatório saiu com `janelas_com_resolucao: 0` e a
    # âncora sem amostra nenhuma — dois contadores em contradição no mesmo
    # JSON. A regra correta separa as perguntas:
    #
    # - "a janela tem resultado?" NÃO depende do livro → `resolvidas`
    # - "a âncora bate?" usa stream RTDS + resultado, NUNCA o livro → âncora
    #   consome `resolvidas`
    # - "o fill seria possível?" depende do livro → só o runner e as medições
    #   de book usam `integras`
    resolvidas = [j for j in janelas if j.resolveu_up is not None]
    # A.2: janela cujo livro divergiu do que o servidor afirmou sai do
    # backtest DE FILLS — e só dele. O relatório diz quantas e quais.
    #
    # M2.5: o corte deixou de ser binário. Cada janela recebe a PIOR marca
    # entre os seus dois tokens, e `--qualidade-minima` decide onde cortar.
    # A diferença não é cosmética: o gate anterior reprovava por um tick de
    # divergência e zerou 200 de 200 janelas reais. Agora quem lê o relatório
    # vê quantas janelas cada marca carrega e pode refazer o corte sem rodar
    # de novo. `sem_dado` (token nunca visto no fio) nunca é excluído aqui:
    # ele não tem livro para o runner usar de qualquer forma, e excluí-lo
    # esconderia a janela por um motivo que não é qualidade de livro.
    qualidade_por_slug = {
        j.slug: index.integridade.qualidade_da_janela(j.token_up, j.token_down)
        for j in janelas
    }
    minimo = ORDEM_QUALIDADE[args.qualidade_minima]
    integras = [
        j
        for j in resolvidas
        if ORDEM_QUALIDADE.get(qualidade_por_slug.get(j.slug, "sem_dado"), minimo)
        >= minimo
    ]

    # Âncora: valida as hipóteses contra as resoluções REAIS antes de usar
    # qualquer uma delas no modelo.
    outcomes = [
        WindowOutcome(
            slug=j.slug,
            open_ts_ns=j.open_ts_ns,
            close_ts_ns=j.close_ts_ns,
            samples=tuple(index.streams.get(j.asset, [])),
            resolved_up=bool(j.resolveu_up),
        )
        for j in resolvidas
        if j.jogo == "twap"
    ]
    scores = evaluate_hypotheses(outcomes)
    validacao = report_anchor_validation(scores)
    validacao["janelas_alimentadas"] = len(outcomes)
    # M2.4: as hipóteses nomeadas ficam como referência; a varredura de τ vem
    # além delas — engenharia reversa em inteiros e18, eixo do servidor.
    validacao["varredura_tau"] = varrer(
        [
            JanelaResolvida(
                slug=j.slug,
                asset=j.asset,
                abertura_ms=j.open_ts_ns // 1_000_000,
                fechamento_ms=j.close_ts_ns // 1_000_000,
                resolveu_up=bool(j.resolveu_up),
            )
            for j in resolvidas
            if j.jogo == "twap"
        ],
        dict(index.streams_e18),
    )

    # ---------------------------------------------------------------- âncora
    # M2.6: a âncora deixou de ser hipótese. Até o M2.5 o simulador operava
    # com a hipótese sobrevivente (`ultimo_antes`, ~90% de acerto), enquanto a
    # varredura no MESMO relatório dizia 100% para τ=0 — ou seja, o PnL saía
    # de resoluções que sabíamos parcialmente erradas. Agora a fonte é a
    # âncora VERIFICADA (API_NOTES §13.8): valor do stream `twap_sixty` no
    # instante da abertura, eixo de carimbo do SERVIDOR, inteiro e18.
    #
    # As hipóteses nomeadas continuam sendo calculadas e reportadas — como
    # REFERÊNCIA HISTÓRICA, para que a diferença entre o que se supunha e o
    # que se mediu continue visível. Nenhuma delas decide coisa alguma.
    lacunas = montar_ancoras(resolvidas, dict(index.streams_e18))

    # M2.10 item 5: a cobertura entra NO veredito. A causa de "8 elegiveis"
    # estava no relatorio, noutro bloco, e ninguem cruzou os dois.
    veredito_ancora = veredito_da_ancora(
        validacao["varredura_tau"],
        cobertura=(index.stream_de_ancora() or {}).get("cobertura_da_gravacao"),
    )
    validacao["veredito_da_varredura"] = veredito_ancora
    # O veredito do topo passa a ser o da VARREDURA. O texto antigo dizia
    # "NENHUMA hipótese sobreviveu" no mesmo relatório em que a varredura
    # marcava 100% — dois vereditos contraditórios sobre a mesma pergunta.
    validacao["veredito_das_hipoteses_historico"] = validacao["veredito"]
    validacao["veredito"] = veredito_ancora["veredito"]
    validacao["lacunas_do_stream"] = lacunas

    cfg_base = {
        "threshold_edge": args.threshold,
        "latencia_ms": args.latencia_ms,
        "max_entradas_por_janela": max(1, args.max_entradas),
        "intervalo_min_entre_entradas_s": max(0.0, args.intervalo_entradas),
    }
    restricao_pedida = (
        args.tempo_restante_max is not None or args.tempo_restante_min is not None
    )
    runner = BacktestRunner(
        BacktestConfig(
            **cfg_base,
            tempo_restante_min_s=args.tempo_restante_min,
            tempo_restante_max_s=args.tempo_restante_max,
        )
    )
    report = runner.run(integras, index.streams)

    # M2.6 BUG 2.3: as duas rodadas lado a lado, sempre. Reportar só a
    # restrita esconderia o custo da restrição (menos trades, menos capital
    # movimentado); reportar só a irrestrita foi o que produziu 46 de 48
    # trades no bucket com 24pp de erro de calibração. Quem lê precisa dos
    # dois números para julgar a troca.
    #
    # A comparação roda mesmo sem `--tempo-restante-max`: aí a faixa é a dos
    # buckets calibrados (≤240s), que é a recomendação que sai da medição.
    faixa_comparada = (
        args.tempo_restante_max if restricao_pedida else TEMPO_CALIBRADO_MAX_S
    )
    if restricao_pedida:
        report_livre = BacktestRunner(BacktestConfig(**cfg_base)).run(
            integras, index.streams
        )
        comparacao = {"irrestrito": report_livre.to_dict(), "restrito": report.to_dict()}
    else:
        report_restrito = BacktestRunner(
            BacktestConfig(**cfg_base, tempo_restante_max_s=faixa_comparada)
        ).run(integras, index.streams)
        comparacao = {
            "irrestrito": report.to_dict(),
            "restrito": report_restrito.to_dict(),
        }

    # M2: a variante encolhida, quando pedida. LADO A LADO com a crua, na
    # mesma faixa calibrada e com entrada única — mudar uma coisa por vez.
    # A crua continua sendo a que alimenta os critérios pré-registrados; a
    # encolhida existe para responder se a correção de escala devolve o 1.1,
    # e a resposta só vale se o fator veio de período anterior ao avaliado.
    comparacao_encolhimento = None
    if args.fator_de_encolhimento is not None:
        cfg_encolhimento = {
            "threshold_edge": args.threshold,
            "latencia_ms": args.latencia_ms,
            "tempo_restante_min_s": args.tempo_restante_min,
            "tempo_restante_max_s": faixa_comparada,
            "intervalo_min_entre_entradas_s": max(0.0, args.intervalo_entradas),
            "max_entradas_por_janela": 1,
        }
        comparacao_encolhimento = {
            "fator": args.fator_de_encolhimento,
            "base": 0.5,
            "faixa": {
                "tempo_restante_min_s": args.tempo_restante_min,
                "tempo_restante_max_s": faixa_comparada,
            },
            "comparacao": {
                nome: BacktestRunner(
                    BacktestConfig(**cfg_encolhimento, fator_de_encolhimento=fator)
                )
                .run(integras, index.streams)
                .to_dict()
                for nome, fator in (
                    ("sem_encolher", None),
                    ("encolhido", args.fator_de_encolhimento),
                )
            },
            "nota": (
                "As duas rodadas na faixa registrada em `faixa` (a calibrada, "
                "ou a pedida na linha de comando), entrada unica, mesma "
                "latencia e threshold — a UNICA variavel e o encolhimento. "
                "A calibracao da variante e medida sobre a probabilidade "
                "ENCOLHIDA, ponto a ponto: e o ECE real dela, nao a "
                "aproximacao por faixas do resumo. VALIDADE: o fator deve "
                "ter sido ajustado em periodo anterior ao desta gravacao; "
                "se foi ajustado nesta, o resultado e in-sample e nao "
                "sustenta veredito."
            ),
        }

    # M2.7 tarefa 3: entrada unica x multipla, lado a lado, SEMPRE — e as
    # duas dentro da faixa calibrada, que e a configuracao que produziu o
    # primeiro PnL positivo do projeto. Comparar entrada multipla no regime
    # irrestrito misturaria duas mudancas numa medicao so.
    faixa_para_entradas = (
        args.tempo_restante_max if restricao_pedida else TEMPO_CALIBRADO_MAX_S
    )
    cfg_entradas = {
        "threshold_edge": args.threshold,
        "latencia_ms": args.latencia_ms,
        "tempo_restante_min_s": args.tempo_restante_min,
        "tempo_restante_max_s": faixa_para_entradas,
        "intervalo_min_entre_entradas_s": max(0.0, args.intervalo_entradas),
    }
    comparacao_entradas = {
        f"max_{n}_entradas": BacktestRunner(
            BacktestConfig(**cfg_entradas, max_entradas_por_janela=n)
        )
        .run(integras, index.streams)
        .to_dict()
        for n in (1, 3, 10)
    }

    relatorio: dict[str, Any] = {
        "gravacao": {
            "arquivos": len(reader.files),
            "arquivos_disponiveis": reader.arquivos_disponiveis,
            "fatia": {
                "desde": desde.isoformat() if desde else None,
                "ate": ate.isoformat() if ate else None,
                "nota": (
                    "Fatia de hora (M2.5 tarefa 6). Com --desde/--ate o "
                    "relatorio descreve SO a fatia: `janelas_conhecidas` e os "
                    "agregados nao sao os da gravacao inteira. Somar fatias "
                    "exige agregacao incremental — ver docs/RUNBOOK_VPS.md §7.1."
                ),
            },
            "linhas_corrompidas": reader.corrompidas,
            "arquivos_ilegiveis": reader.arquivos_ilegiveis,
            "snapshots_de_descoberta": index.n_snapshots,
            "janelas_conhecidas": len(janelas),
            "janelas_com_resolucao": len(resolvidas),
            "resolucoes": index.resolucoes_resumo(janelas),
            "gaps": index.gaps,
            "silencio_do_rtds": index.silencio_do_rtds(),
            "stream_de_ancora": index.stream_de_ancora(),
            "memoria": index.uso_de_memoria(),
        },
        "integridade": {
            "divergencia_topo_book": index.integridade.resumo(),
            "offset_relogio_ms": index.relogio.resumo(),
            "janelas_invalidadas": sorted(
                slug
                for slug, marca in qualidade_por_slug.items()
                if marca == "baixa"
            ),
            "janelas_por_qualidade": {
                marca: sum(1 for m in qualidade_por_slug.values() if m == marca)
                for marca in QUALIDADES
            },
            "qualidade_minima_aplicada": args.qualidade_minima,
            "nota": (
                "Divergencia entre o topo que o servidor afirma e o topo que "
                "reconstruimos. `janelas_por_qualidade` e o corte que importa: "
                "a janela herda a PIOR marca dos seus dois tokens, e "
                "`--qualidade-minima` decide onde cortar. Numero calculado "
                "sobre livro furado e pior que numero nenhum, porque parece "
                "bom — mas reprovar por um tick de divergencia (o gate do "
                "M2.2) zerou 200 de 200 janelas reais medindo corrida entre "
                "`best_bid_ask` e `price_change`. Criterios em VEREDITO_M2 "
                "§2c, escritos antes dos numeros."
            ),
        },
        "ancora": {
            **validacao,
            "usada_no_backtest": "stream_twap_sixty_na_abertura",
            "usada_no_backtest_nota": (
                "Ancora VERIFICADA (API_NOTES 13.8), nao mais a hipotese "
                "sobrevivente. `por_hipotese` fica como referencia historica: "
                "nenhuma hipotese nomeada decide coisa alguma desde o M2.6."
            ),
        },
        "backtest": {
            **report.to_dict(),
            "janelas_avaliaveis": len(integras),
            "janelas_excluidas_por_integridade": len(resolvidas) - len(integras),
            "janelas_avaliaveis_por_qualidade": {
                marca: sum(
                    1 for j in integras if qualidade_por_slug.get(j.slug) == marca
                )
                for marca in QUALIDADES
            },
            "faixa_de_tempo_restante": {
                "min_s": args.tempo_restante_min,
                "max_s": args.tempo_restante_max,
                "aplicada": restricao_pedida,
            },
        },
        "entradas_por_janela": {
            "faixa_aplicada_s": faixa_para_entradas,
            "intervalo_minimo_s": max(0.0, args.intervalo_entradas),
            "comparacao": comparacao_entradas,
            "nota": (
                "M2.7 tarefa 3. A v1 entrava UMA vez por janela: 18 trades "
                "sobre 1.617 instantes com sinal em 240-120s, contra os 200 "
                "trades que o VEREDITO_M2 exige para decidir. As tres "
                "versoes rodam na MESMA faixa calibrada, entao a unica "
                "variavel e o numero de entradas. Olhe pnl_liquido_usdc E "
                "max_drawdown_usdc juntos: mais entradas comprando o mesmo "
                "movimento aumenta o PnL e o risco na mesma proporcao, e ai "
                "nao houve ganho nenhum — so alavancagem. O default segue 1; "
                "mudar exige numero, nao intuicao."
            ),
        },
        "encolhimento": comparacao_encolhimento,
        "faixa_de_tempo": {
            "faixa_restrita_s": faixa_comparada,
            "comparacao": comparacao,
            "nota": (
                "M2.6 BUG 2. `irrestrito` opera em qualquer instante; "
                "`restrito` so opera com <= faixa_restrita_s de tempo "
                "restante. A faixa default da comparacao e "
                f"{TEMPO_CALIBRADO_MAX_S:g}s porque e onde a calibracao "
                "medida em 4h reais tem erro de -0,008, contra -0,240 em "
                ">240s. Compare `resumo.pnl_liquido_usdc` e "
                "`resumo.hit_rate` dos dois: se o restrito for melhor com "
                "MENOS trades, o gatilho estava operando onde o modelo nao "
                "sabe. Veja tambem `oportunidades_por_bucket` de cada um."
            ),
        },
        "sensibilidade_latencia": sensibilidade_latencia(
            integras, index.streams, threshold=args.threshold
        ),
        "curva_de_edge": curva_de_edge_por_threshold(
            varredura_de_threshold(
                integras, index.streams, latencia_ms=args.latencia_ms
            )
        ),
        **(
            {
                "curva_de_capacidade": varredura_de_tamanho(
                    integras,
                    index.streams,
                    tamanhos=tamanhos_da_varredura,
                    threshold=args.threshold,
                    latencia_ms=args.latencia_ms,
                )
            }
            if tamanhos_da_varredura
            else {}
        ),
        "medicoes": {
            "tick": medir_mudanca_de_tick(
                index.snapshots, distribuicao_de_tick=dict(index.ticks_vistos)
            ),
            "atraso_liquidacao": medir_atraso_liquidacao(
                [
                    {
                        "slug": j.slug,
                        "jogo": j.jogo,
                        "end_date_ns": j.close_ts_ns,
                        "resolution_ts_ns": index.resolucoes.get(j.token_up, 0),
                    }
                    for j in resolvidas
                ]
            ),
            # Markout e profundidade LEEM o livro reconstruído — janela com
            # livro invalidado sai delas. O atraso de liquidação acima usa só
            # endDate × carimbo do evento, e por isso fica em `resolvidas`.
            "markout": medir_markout(integras),
            "profundidade": medir_profundidade(
                [
                    {
                        "book": book,
                        "duracao_s": j.duracao_s,
                        "tick_size": j.tick_size,
                        "hora_utc": int((j.close_ts_ns / 1e9) // 3600 % 24),
                    }
                    for j in integras
                    for timeline in [j.books.get(j.token_up)]
                    if timeline is not None
                    for book in timeline.books[:50]
                ]
            ),
        },
    }

    # ─── rota maker (M2.2 parte B): medição, nunca implementação ───
    rewards = simular_rewards(integras)
    relatorio["rota_maker"] = {
        "rewards": rewards,
        "markout": relatorio["medicoes"]["markout"],
        "conta_fechada": conta_do_maker(
            rewards=rewards,
            markout=relatorio["medicoes"]["markout"],
            fee_rebate_rate=_rebate_medio(integras),
            fee_rate=_medio_do_dado(integras, "fee_rate"),
            fee_exponent=_medio_do_dado(integras, "fee_exponent") or 1.0,
        ),
        "aviso": (
            "NADA aqui envia ordem. É simulação sobre gravação. A fórmula de "
            "score NÃO foi verificada contra a documentação oficial — ver "
            "`rota_maker.rewards.hipoteses` e docs/API_NOTES.md 15."
        ),
    }

    saida = json.dumps(relatorio, indent=2, ensure_ascii=False, default=str)
    print(saida)
    if destino is not None:
        destino.write_text(saida, encoding="utf-8")
        print(f"\nrelatório gravado em {destino}", file=sys.stderr)

    # M2.6 BUG 1.4: a âncora deixar de explicar as resoluções seria MUDANÇA DE
    # REGRA da plataforma — o tipo de coisa que não pode passar em silêncio
    # num relatório de 3.000 linhas que ninguém lê inteiro. O relatório é
    # gravado do mesmo jeito (o dado é útil para diagnosticar), mas o comando
    # grita no stderr e sai com código próprio, para que um laço de shell
    # pare em vez de seguir acumulando fatias sobre uma âncora morta.
    if veredito_ancora["alerta"]:
        print(
            "\n" + "=" * 70 + "\n"
            "ALERTA: A ÂNCORA VERIFICADA NÃO EXPLICA MAIS AS RESOLUÇÕES\n"
            + "=" * 70 + "\n"
            + veredito_ancora["alerta"]
            + "\n\n"
            f"  tau verificado........: {veredito_ancora['tau_verificado_s']}s\n"
            f"  consistencia medida...: "
            f"{veredito_ancora['consistencia_do_tau_verificado']}\n"
            f"  regiao de 100%........: "
            f"{veredito_ancora['regiao_viavel_100pct'] or 'NENHUMA'}\n"
            f"  janelas elegiveis.....: {veredito_ancora['janelas_elegiveis']}\n\n"
            "Referencia: docs/API_NOTES.md 13.8 · docs/VEREDITO_M2.md 2b\n"
            + "=" * 70,
            file=sys.stderr,
        )
        return CODIGO_ANCORA_INVALIDA
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
