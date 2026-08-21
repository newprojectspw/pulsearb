"""Integridade do livro reconstruído — o teste que transforma corrupção
silenciosa em alarme (M2.2 parte A, recalibrado no M2.5).

O problema que este módulo existe para resolver: o backtest opera sobre um
livro que NÓS reconstruímos aplicando deltas sobre um snapshot. Se um delta se
perde — fila cheia, reconexão, formato de payload diferente do esperado — o
livro passa a divergir do real e **nada avisa**. Todos os números do backtest
continuam saindo, bonitos e errados.

A defesa é gratuita e vem do próprio protocolo: o CLOB manda `best_bid` e
`best_ask` autoritativos em dois lugares — no evento `best_bid_ask` e **dentro
de cada `price_change`** (campos do modelo `PriceChange` do SDK 0.6.0). Ou
seja, a cada delta o servidor diz qual É o topo depois dele. Basta comparar
com o topo que a nossa reconstrução produziu.

## O que o M2.5 mudou, e por quê

A primeira versão reprovou **200 de 200 janelas** da gravação real. O relatório
dela mesma explicava o erro: `p50 = 10 ticks de 0,001` = 0,01 = **exatamente um
tick do mercado**, e o limiar de invalidação também era 0,01. O detector estava
medindo a **corrida** entre `best_bid_ask` e `price_change` e chamando de
corrupção. Prova independente de que o dado estava são: a varredura da âncora
atingiu consistência 1.0 sobre 152 janelas da MESMA gravação.

Quatro correções, todas documentadas em `docs/VEREDITO_M2.md` §2c (escritas
ANTES de olhar quantas janelas sobreviviam):

1. **Alinhamento por carimbo do servidor.** Um `best_bid_ask` afirma o topo
   *no instante dele*, não no instante em que chegou aqui. Comparar contra o
   livro "atual" por ordem de chegada mede a nossa fila, não a corrupção. Agora
   a afirmação espera e é comparada contra o livro **depois de aplicados todos
   os deltas com carimbo ≤ o dela**. As duas contas ficam no relatório lado a
   lado — o antes e o depois são medidos, não prometidos.
2. **Lado vazio não é corrupção por decreto.** São quatro causas e só duas
   invalidam. Ver `MOTIVOS_DE_LADO_VAZIO`.
3. **Invalidação por conjunção**, não por evento isolado: magnitude relevante
   (> K ticks, K ≥ 2) **e** persistência (> 250 ms) **e** fração de tempo
   divergente acima de 1%.
4. **Marca de qualidade** (`alta`/`media`/`baixa`) no lugar da exclusão
   binária, para que o corte seja do leitor e não do detector.

O mesmo monitor roda nos dois lados de propósito: ao vivo no recorder, para
consertar; e offline no backtest, para medir. Duas implementações da mesma
comparação divergiriam com o tempo, e a que mede deixaria de descrever a que
conserta. Por isso `observar()` continua devolvendo a divergência **imediata**
(o recorder precisa reagir agora, e não pode esperar alinhamento); a população
alinhada é acumulada por dentro, para o relatório.
"""

from __future__ import annotations

import math
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Any

from pulsearb.feeds.poly_ws import (
    EVENT_BEST_BID_ASK,
    EVENT_BOOK,
    EVENT_PRICE_CHANGE,
    MudancaDePreco,
    forma_do_book,
    forma_do_price_change,
    iter_mudancas,
)

# Tolerância padrão da comparação de topo, em unidades de preço.
# Meio tick do menor tick observado (0,001) — abaixo disso é ruído de
# arredondamento de string decimal, não perda de delta.
TOLERANCIA_PADRAO = 0.0005

# Tick do mercado real (API_NOTES §12.5). É o denominador que separa ruído de
# corrupção: 0,001 existe como ESTADO em janelas equilibradas (§13.3), mas a
# corrida entre `best_bid_ask` e `price_change` acontece na escala de 0,01.
TICK_MERCADO = 0.01

# Quantos ticks de mercado uma divergência precisa ter para ser CANDIDATA a
# corrupção. Nunca menor que 2: 1 tick é o p50 observado na gravação real, ou
# seja, é o ruído. Ver VEREDITO_M2 §2c.
TICKS_MIN_DIVERGENCIA = 2

# Uma divergência que some na mensagem seguinte é corrida, não perda. 250 ms
# são ~2 ordens de grandeza acima do intervalo entre deltas do CLOB.
PERSISTENCIA_MIN_MS = 250.0

# Fração do tempo observado com livro divergente que ainda deixa o token
# utilizável. 1% de uma janela de 5 min são 3 s.
FRACAO_MEDIA = 0.01
FRACAO_ALTA = 0.001

# Magnitudes persistentes que rebaixam a marca por si sós, sem esperar a
# fração de tempo: meio dime de erro no topo já muda o lado do trade.
MAGNITUDE_GRAVE = 0.05     # derruba de `alta`
MAGNITUDE_CRITICA = 0.10   # derruba para `baixa`

# Divergências guardadas por token para o relatório. O contador é completo; a
# lista é amostra — 72h de gravação não podem virar uma lista sem teto.
MAX_AMOSTRAS = 50
# Magnitudes retidas para os percentis. Reservatório em rodízio, como no
# MonitorDeRelogio: determinístico (o replay tem de reproduzir) e limitado
# (produção mostrou ~21k divergências numa sessão; 72h não podem virar lista
# sem teto).
MAX_MAGNITUDES = 50_000

# Estados de topo retidos por token para o alinhamento por carimbo. 64 cobre
# a desordem observada (milissegundos) com folga; o custo é ~64 tuplas por
# token vivo.
HISTORICO_MAX = 64
# Afirmações de `best_bid_ask` esperando o delta que prova que todos os
# carimbos ≤ o delas já chegaram. Teto para o caso de um token parar de
# receber deltas: acima disto a mais antiga é resolvida com o que houver.
PENDENTES_MAX = 64

#: As quatro causas de "o servidor afirma um topo e nós não temos lado nenhum",
#: e quais invalidam. As duas primeiras são MIOPIA nossa (nossa visão de
#: profundidade é incompleta); as duas últimas são livro furado de verdade.
MOTIVOS_DE_LADO_VAZIO = {
    "vazio_desde_o_snapshot": False,
    "esvaziado_por_delta": False,
    "sem_snapshot": True,
    "apos_perda": True,
}

QUALIDADES = ("alta", "media", "baixa", "sem_dado")
ORDEM_QUALIDADE = {"alta": 3, "media": 2, "baixa": 1}


@dataclass(frozen=True, slots=True)
class Divergencia:
    """O topo que o servidor afirmou × o topo que reconstruímos."""

    asset_id: str
    ts_ns: int
    lado: str                    # "bid" | "ask"
    servidor: float | None
    reconstruido: float | None
    magnitude: float             # |servidor − reconstruído|, em preço
    motivo_vazio: str | None = None   # preenchido só quando magnitude = inf
    origem: str = "price_change"      # qual evento fez a afirmação

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "ts_ns": self.ts_ns,
            "lado": self.lado,
            "servidor": self.servidor,
            "reconstruido": self.reconstruido,
            "magnitude": round(self.magnitude, 6),
            "motivo_vazio": self.motivo_vazio,
            "origem": self.origem,
        }


class LivroLeve:
    """Livro por preço→tamanho, só para saber o topo.

    Não é o `OrderBook` do backtest de propósito: o recorder precisa disto no
    hot path, com 150+ tokens vivos, e só lê o melhor bid e o melhor ask.
    Guardar níveis ordenados e reordenar a cada delta seria trabalho jogado
    fora — aqui um dicionário basta, e o topo sai com `max`/`min`.

    `motivo_vazio` é o que o M2.5 acrescentou: quando um lado está vazio, este
    livro sabe **por quê** — já veio vazio no snapshot, ou os deltas
    esvaziaram. As duas coisas parecem iguais no topo e são doenças
    diferentes (VEREDITO_M2 §2c).
    """

    __slots__ = ("asks", "bids", "motivo_vazio", "ts_ns")

    def __init__(self) -> None:
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}
        self.ts_ns = 0
        self.motivo_vazio: dict[str, str | None] = {"bid": None, "ask": None}

    def aplicar_snapshot(self, evento: dict[str, Any]) -> None:
        self.bids = _niveis(evento.get("bids"))
        self.asks = _niveis(evento.get("asks"))
        self.motivo_vazio = {
            "bid": None if self.bids else "vazio_desde_o_snapshot",
            "ask": None if self.asks else "vazio_desde_o_snapshot",
        }

    def aplicar(self, mudanca: MudancaDePreco) -> None:
        lado_nome = "bid" if mudanca.side == "BUY" else "ask"
        lado = self.bids if mudanca.side == "BUY" else self.asks
        if mudanca.size > 0:
            lado[mudanca.price] = mudanca.size
        else:
            lado.pop(mudanca.price, None)
        if lado:
            self.motivo_vazio[lado_nome] = None
        elif self.motivo_vazio[lado_nome] is None:
            # Tínhamos níveis e o delta levou o último: o servidor mostra um
            # nível abaixo que nunca nos foi contado. Truncagem, não perda.
            self.motivo_vazio[lado_nome] = "esvaziado_por_delta"

    @property
    def best_bid(self) -> float | None:
        return max(self.bids) if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return min(self.asks) if self.asks else None


@dataclass(slots=True)
class _Populacao:
    """Uma forma de contar as mesmas comparações.

    Existem duas: `por_chegada` (o método antigo, que compara contra o livro
    atual na ordem em que os eventos chegaram) e `por_carimbo` (o método novo,
    que compara contra o livro como ele estava no instante que o servidor
    afirmou). Manter as duas é o único jeito de mostrar o ganho do
    alinhamento em vez de afirmá-lo.
    """

    comparacoes: int = 0
    divergencias: int = 0
    lados_vazios: int = 0
    magnitudes_total: int = 0
    magnitudes: list[float] = field(default_factory=list)
    por_motivo_vazio: Counter[str] = field(default_factory=Counter)

    def registrar(self, magnitude: float, motivo_vazio: str | None) -> None:
        self.divergencias += 1
        if math.isinf(magnitude):
            self.lados_vazios += 1
            self.por_motivo_vazio[motivo_vazio or "desconhecido"] += 1
            return
        self.magnitudes_total += 1
        if len(self.magnitudes) < MAX_MAGNITUDES:
            self.magnitudes.append(magnitude)
        else:
            self.magnitudes[self.magnitudes_total % MAX_MAGNITUDES] = magnitude

    def resumo(self, tick: float) -> dict[str, Any]:
        ordenadas = sorted(self.magnitudes)
        taxa = self.divergencias / self.comparacoes if self.comparacoes else 0.0
        return {
            "comparacoes": self.comparacoes,
            "divergencias": self.divergencias,
            "taxa": round(taxa, 6),
            "com_magnitude_finita": self.magnitudes_total,
            "com_lado_vazio": self.lados_vazios,
            "magnitude_p50": _percentil(ordenadas, 50),
            "magnitude_p90": _percentil(ordenadas, 90),
            "magnitude_p99": _percentil(ordenadas, 99),
            "magnitude_max": round(max(ordenadas), 6) if ordenadas else None,
            "magnitude_em_ticks_de_0.001": {
                "p50": _em_ticks(_percentil(ordenadas, 50), 0.001),
                "p90": _em_ticks(_percentil(ordenadas, 90), 0.001),
                "p99": _em_ticks(_percentil(ordenadas, 99), 0.001),
            },
            "magnitude_em_ticks_de_mercado": {
                "tick": tick,
                "p50": _em_ticks(_percentil(ordenadas, 50), tick),
                "p90": _em_ticks(_percentil(ordenadas, 90), tick),
                "p99": _em_ticks(_percentil(ordenadas, 99), tick),
            },
            "magnitudes_amostradas": len(ordenadas),
            "lado_vazio_por_causa": dict(self.por_motivo_vazio),
        }


@dataclass(slots=True)
class _EstadoDoToken:
    """Tudo que se sabe sobre a saúde do livro de UM token."""

    livro: LivroLeve = field(default_factory=LivroLeve)
    com_snapshot: bool = False
    aguardando_resync: bool = False
    #: (ts_servidor_ms, best_bid, best_ask) depois de cada evento aplicado
    historico: deque[tuple[float, float | None, float | None]] = field(
        default_factory=lambda: deque(maxlen=HISTORICO_MAX)
    )
    #: afirmações de `best_bid_ask` esperando alinhamento
    pendentes: deque[tuple[float, float | None, float | None, int]] = field(
        default_factory=lambda: deque(maxlen=PENDENTES_MAX)
    )
    #: lado → (ts_ms de início, maior magnitude) da divergência relevante aberta
    abertas: dict[str, tuple[float, float]] = field(default_factory=dict)
    #: `None` = ainda não observado. Zero NÃO serve de sentinela aqui: um
    #: carimbo legítimo de 0 seria indistinguível de "nunca vi este token", e
    #: a comparação com 0.0 em ponto flutuante é frágil por natureza.
    ts_primeiro_ms: float | None = None
    ts_ultimo_ms: float | None = None
    ts_max_servidor_ms: float = 0.0
    ms_divergentes: float = 0.0
    persistentes: int = 0
    magnitude_persistente_max: float = 0.0
    deltas_fora_de_ordem: int = 0
    snapshots_fora_de_ordem: int = 0
    divergencias: int = 0
    pior_magnitude: float = 0.0
    #: tempo em que o token foi observado SEM livro válido (antes do
    #: primeiro snapshot, ou depois de uma perda conhecida). É medido em
    #: tempo, e não em eventos, pelo mesmo motivo que a divergência: um
    #: token que ficou 200 ms sem livro no começo da vida não é o mesmo
    #: que um que passou a janela inteira sem.
    ms_sem_livro: float = 0.0
    #: `None` = há livro. Mesmo motivo da sentinela acima.
    sem_livro_desde: float | None = None
    teve_snapshot: bool = False

    def marcar_tempo(self, ts_ms: float) -> None:
        if self.ts_primeiro_ms is None:
            self.ts_primeiro_ms = ts_ms
        self.ts_ultimo_ms = (
            ts_ms if self.ts_ultimo_ms is None else max(self.ts_ultimo_ms, ts_ms)
        )

    @property
    def ms_observados(self) -> float:
        if self.ts_primeiro_ms is None or self.ts_ultimo_ms is None:
            return 0.0
        return max(0.0, self.ts_ultimo_ms - self.ts_primeiro_ms)

    @property
    def fracao_divergente(self) -> float:
        janela = self.ms_observados
        return self.ms_divergentes / janela if janela > 0 else 0.0

    @property
    def fracao_sem_livro(self) -> float:
        janela = self.ms_observados
        return self.ms_sem_livro / janela if janela > 0 else 0.0

    @property
    def fracao_ruim(self) -> float:
        """Fração do tempo em que o livro não descreveu o mercado.

        Soma as duas doenças porque para quem vai entrar dá no mesmo: ou o
        topo estava errado, ou não havia topo.
        """
        janela = self.ms_observados
        if janela <= 0:
            return 0.0
        return (self.ms_divergentes + self.ms_sem_livro) / janela

    def abrir_sem_livro(self, carimbo: float | None) -> None:
        if carimbo is not None and self.sem_livro_desde is None:
            self.sem_livro_desde = carimbo

    def fechar_sem_livro(self, carimbo: float | None) -> None:
        if self.sem_livro_desde is not None and carimbo is not None:
            self.ms_sem_livro += max(0.0, carimbo - self.sem_livro_desde)
            self.sem_livro_desde = None


@dataclass
class MonitorDeIntegridade:
    """Reconstrói o topo e confere contra o que o servidor afirma.

    Uso: `observar(evento, ts_ns)` para cada evento do CLOB, na ordem. Devolve
    as divergências **imediatas** daquele evento (normalmente nenhuma) — é o
    que o recorder usa para forçar resync na hora. O julgamento alinhado, que
    é o que decide a marca de qualidade, é acumulado por dentro e sai em
    `resumo()`; chame `finalizar()` antes de ler, para fechar as pendências.
    """

    tolerancia: float = TOLERANCIA_PADRAO
    tick_mercado: float = TICK_MERCADO
    ticks_divergencia: int = TICKS_MIN_DIVERGENCIA
    persistencia_min_ms: float = PERSISTENCIA_MIN_MS
    fracao_media: float = FRACAO_MEDIA
    fracao_alta: float = FRACAO_ALTA
    max_amostras: int = MAX_AMOSTRAS

    estados: dict[str, _EstadoDoToken] = field(default_factory=dict)
    amostras: list[Divergencia] = field(default_factory=list)
    por_chegada: _Populacao = field(default_factory=_Populacao)
    por_carimbo: _Populacao = field(default_factory=_Populacao)
    #: token → quantas vezes divergiu (população alinhada)
    divergencias_por_token: Counter[str] = field(default_factory=Counter)
    #: qual forma de `price_change` o servidor está usando de fato
    formas_de_price_change: Counter[str] = field(default_factory=Counter)
    #: afirmações que chegaram antes de existir qualquer estado com carimbo ≤
    #: o delas — não dá para alinhar, caem na conta bruta e são contadas aqui
    sem_estado_alinhado: int = 0
    observacoes_sem_snapshot: int = 0
    #: causa → quantas observações caíram em livro inexistente. Fica FORA
    #: das populações de propósito: não é 'a reconstrução errou', é 'não
    #: havia reconstrução'. Contá-las como divergência foi o que encheu o
    #: relatório do M2.2 com 2,7 milhões de 'lado vazio'.
    sem_livro_por_causa: Counter[str] = field(default_factory=Counter)
    #: M2.6 BUG 4: com que par de chaves os snapshots de livro chegaram, e
    #: quantos níveis traziam. `vazio_desde_o_snapshot` em massa é OU o
    #: servidor mandando o lado vazio, OU o nome do campo mudando — e as
    #: duas coisas produzem o mesmo zero. Só isto separa as duas.
    formas_de_book: Counter[str] = field(default_factory=Counter)
    books_observados: int = 0
    books_com_bid_vazio: int = 0
    books_com_ask_vazio: int = 0
    niveis_bid: list[float] = field(default_factory=list)
    niveis_ask: list[float] = field(default_factory=list)
    #: atraso, em ms, de eventos cujo carimbo do servidor veio ANTES do
    #: maior já visto para o token. É o que dimensiona o buffer de
    #: reordenação do leitor (M2.6 BUG 4.3).
    atrasos_de_carimbo_ms: list[float] = field(default_factory=list)

    # ------------------------------------------------------ compatibilidade
    # Estes três eram atributos na versão do M2.2 e são lidos pelo recorder e
    # pelos testes. Continuam significando a mesma coisa, agora derivados.
    @property
    def livros(self) -> dict[str, LivroLeve]:
        return {token: e.livro for token, e in self.estados.items()}

    @property
    def com_snapshot(self) -> set[str]:
        return {t for t, e in self.estados.items() if e.com_snapshot}

    @property
    def aguardando_resync(self) -> set[str]:
        return {t for t, e in self.estados.items() if e.aguardando_resync}

    @property
    def comparacoes(self) -> int:
        return self.por_carimbo.comparacoes

    @property
    def divergencias(self) -> int:
        return self.por_carimbo.divergencias

    @property
    def magnitudes(self) -> list[float]:
        return self.por_carimbo.magnitudes

    @property
    def lados_vazios(self) -> int:
        return self.por_carimbo.lados_vazios

    @property
    def pior_por_token(self) -> dict[str, float]:
        return {t: e.pior_magnitude for t, e in self.estados.items()}

    @property
    def magnitude_minima(self) -> float:
        """Abaixo disto a divergência é ruído de corrida, não perda."""
        return max(self.ticks_divergencia, TICKS_MIN_DIVERGENCIA) * self.tick_mercado

    # ------------------------------------------------------------- observação
    def observar(self, evento: dict[str, Any], ts_ns: int) -> list[Divergencia]:
        tipo = evento.get("event_type")
        carimbo = _carimbo_ms(evento, ts_ns)

        if tipo == EVENT_BOOK:
            asset_id = evento.get("asset_id")
            self.formas_de_book[forma_do_book(evento)] += 1
            self.books_observados += 1
            bids = _niveis(evento.get("bids"))
            asks = _niveis(evento.get("asks"))
            if not bids:
                self.books_com_bid_vazio += 1
            if not asks:
                self.books_com_ask_vazio += 1
            _amostrar(self.niveis_bid, len(bids), self.books_observados)
            _amostrar(self.niveis_ask, len(asks), self.books_observados)
            if isinstance(asset_id, str):
                estado = self._estado(asset_id)
                self._resolver_pendentes(asset_id, estado, carimbo)
                if carimbo < estado.ts_max_servidor_ms:
                    # Snapshot mais VELHO que o estado que já temos. Ele é
                    # autoritativo para o instante dele, não para agora:
                    # aplicá-lo rebobinaria o livro e a corrupção seria nossa.
                    # Conta e ignora.
                    estado.snapshots_fora_de_ordem += 1
                    self._anotar_atraso(estado, carimbo)
                    return []
                estado.livro.aplicar_snapshot(evento)
                estado.livro.ts_ns = ts_ns
                estado.com_snapshot = True
                estado.teve_snapshot = True
                estado.aguardando_resync = False
                estado.marcar_tempo(carimbo)
                estado.fechar_sem_livro(carimbo)
                estado.ts_max_servidor_ms = max(estado.ts_max_servidor_ms, carimbo)
                self._anotar_historico(estado, carimbo)
            return []

        if tipo == EVENT_PRICE_CHANGE:
            self.formas_de_price_change[forma_do_price_change(evento)] += 1
            return self._observar_price_change(evento, ts_ns, carimbo)

        if tipo == EVENT_BEST_BID_ASK:
            asset_id = evento.get("asset_id")
            if not isinstance(asset_id, str):
                return []
            estado = self._estado(asset_id)
            estado.marcar_tempo(carimbo)
            afirmado_bid = _numero(evento.get("best_bid"))
            afirmado_ask = _numero(evento.get("best_ask"))
            # (a) conta BRUTA, na hora, contra o livro atual — é o que o
            #     recorder precisa para reagir, e é a conta antiga do M2.2.
            imediatas = self._conferir(
                asset_id,
                estado,
                ts_ns,
                carimbo,
                afirmado_bid,
                afirmado_ask,
                estado.livro.best_bid,
                estado.livro.best_ask,
                origem="best_bid_ask",
                alinhado=False,
            )
            # (b) conta ALINHADA, adiada até que um evento com carimbo maior
            #     prove que todos os deltas ≤ este carimbo já passaram.
            estado.pendentes.append((carimbo, afirmado_bid, afirmado_ask, ts_ns))
            if len(estado.pendentes) == PENDENTES_MAX:
                self._resolver_uma(asset_id, estado, forcada=True)
            return imediatas

        return []

    def _observar_price_change(
        self, evento: dict[str, Any], ts_ns: int, carimbo: float
    ) -> list[Divergencia]:
        """Aplica TODAS as mudanças da mensagem e só então confere o topo.

        Isto é o miolo do M2.5 tarefa 1, e foi o defeito mais caro do M2.2.
        Uma mensagem `price_change` traz VÁRIAS mudanças de nível, e o
        `best_bid`/`best_ask` que ela carrega descrevem o livro **depois de
        todas elas**. A versão anterior conferia depois de CADA mudança, ou
        seja, comparava o topo afirmado contra estados intermediários que
        nunca existiram no servidor.

        O erro tem assinatura reconhecível: quando a mensagem move o topo um
        nível (remove o antigo, insere o novo), o estado intermediário fica
        exatamente **um tick** fora. É o `p50 = 10 ticks de 0,001` = 0,01 = um
        tick de mercado do relatório de produção — 4 milhões de divergências
        que eram a nossa forma de conferir, não perda de dado.

        Uma mensagem pode tocar mais de um token; por isso a conferência é
        agrupada por `asset_id`, com o último topo afirmado de cada um.
        """
        tocados: dict[str, tuple[float | None, float | None]] = {}
        fora_de_ordem: set[str] = set()
        for mudanca in iter_mudancas(evento):
            estado = self._estado(mudanca.asset_id)
            if mudanca.asset_id not in tocados:
                self._resolver_pendentes(mudanca.asset_id, estado, carimbo)
                # Delta com carimbo anterior ao maior já visto está sendo
                # aplicado por cima de estado mais novo: aplicar é o melhor
                # que dá (é a ordem que o recorder gravou), mas comparar
                # seria comparar contra um livro do futuro. Conta e cala.
                if carimbo < estado.ts_max_servidor_ms:
                    estado.deltas_fora_de_ordem += 1
                    self._anotar_atraso(estado, carimbo)
                    fora_de_ordem.add(mudanca.asset_id)
            estado.livro.aplicar(mudanca)
            estado.livro.ts_ns = ts_ns
            estado.marcar_tempo(carimbo)
            estado.ts_max_servidor_ms = max(estado.ts_max_servidor_ms, carimbo)
            tocados[mudanca.asset_id] = (mudanca.best_bid, mudanca.best_ask)

        achados: list[Divergencia] = []
        for asset_id, (best_bid, best_ask) in tocados.items():
            estado = self._estado(asset_id)
            self._anotar_historico(estado, carimbo)
            if asset_id in fora_de_ordem:
                continue
            # O topo autoritativo vem DENTRO da própria mensagem: mesmo
            # carimbo, estado final. Aqui as duas contas coincidem por
            # construção — o alinhamento não tem o que corrigir.
            achados.extend(
                self._conferir(
                    asset_id,
                    estado,
                    ts_ns,
                    carimbo,
                    best_bid,
                    best_ask,
                    estado.livro.best_bid,
                    estado.livro.best_ask,
                    origem="price_change",
                    alinhado=True,
                )
            )
        return achados

    def marcar_perda(self, asset_id: str) -> None:
        """Perda CONHECIDA (fila cheia, reconexão): o livro deixa de valer.

        Não adianta continuar aplicando deltas sobre um livro que já sabemos
        estar furado — o resultado seria plausível e errado. Ele é descartado
        e o token entra em `aguardando_resync` até chegar um snapshot novo.
        """
        estado = self._estado(asset_id)
        estado.livro = LivroLeve()
        estado.com_snapshot = False
        estado.aguardando_resync = True
        estado.historico.clear()
        estado.pendentes.clear()
        estado.abertas.clear()
        estado.abrir_sem_livro(estado.ts_ultimo_ms)

    def finalizar(self) -> None:
        """Fecha pendências e divergências abertas. Chame antes de `resumo()`.

        Sem isto, uma afirmação que chegou no último evento da gravação nunca
        seria comparada, e uma divergência aberta no fim nunca contaria tempo
        — os dois erros na direção de esconder problema.
        """
        for asset_id, estado in self.estados.items():
            while estado.pendentes:
                self._resolver_uma(asset_id, estado, forcada=True)
            # `list()` NÃO é supérfluo: `_fechar_aberta` faz `pop` em
            # `estado.abertas`, e iterar um dict enquanto ele encolhe levanta
            # RuntimeError. A cópia das chaves é o que torna o laço legal.
            for lado in list(estado.abertas):
                self._fechar_aberta(estado, lado, estado.ts_ultimo_ms)
            estado.fechar_sem_livro(estado.ts_ultimo_ms)

    # ---------------------------------------------------------------- interno
    def _estado(self, asset_id: str) -> _EstadoDoToken:
        estado = self.estados.get(asset_id)
        if estado is None:
            estado = _EstadoDoToken()
            self.estados[asset_id] = estado
        return estado

    def _anotar_atraso(self, estado: _EstadoDoToken, carimbo: float) -> None:
        """Quanto este evento chegou atrasado, no eixo do SERVIDOR.

        Dimensiona o buffer de reordenação do leitor: ele ordena por
        `ts_mono_ns` (chegada), e a desordem que sobra é a do carimbo. Sem
        esta medida, "o buffer é insuficiente" é palpite.
        """
        atraso = estado.ts_max_servidor_ms - carimbo
        if atraso > 0:
            _amostrar(
                self.atrasos_de_carimbo_ms,
                atraso,
                len(self.atrasos_de_carimbo_ms) + 1,
            )

    @staticmethod
    def _anotar_historico(estado: _EstadoDoToken, carimbo: float) -> None:
        estado.historico.append(
            (carimbo, estado.livro.best_bid, estado.livro.best_ask)
        )

    def _resolver_pendentes(
        self, asset_id: str, estado: _EstadoDoToken, carimbo_novo: float
    ) -> None:
        """Resolve as afirmações cujo carimbo já ficou para trás.

        A prova de que "todos os deltas com carimbo ≤ T já chegaram" é ver um
        evento do mesmo token com carimbo > T. É a hipótese de gravação
        quase-ordenada, que o `RecordingReader` já mede e reporta.
        """
        while estado.pendentes and estado.pendentes[0][0] < carimbo_novo:
            self._resolver_uma(asset_id, estado, forcada=False)

    def _resolver_uma(
        self, asset_id: str, estado: _EstadoDoToken, *, forcada: bool
    ) -> None:
        carimbo, afirmado_bid, afirmado_ask, ts_ns = estado.pendentes.popleft()
        nosso_bid, nosso_ask, achou = self._estado_em(estado, carimbo)
        if not achou:
            self.sem_estado_alinhado += 1
        self._conferir(
            asset_id,
            estado,
            ts_ns,
            carimbo,
            afirmado_bid,
            afirmado_ask,
            nosso_bid,
            nosso_ask,
            origem="best_bid_ask" + ("_forcada" if forcada else ""),
            alinhado=True,
        )

    @staticmethod
    def _estado_em(
        estado: _EstadoDoToken, carimbo: float
    ) -> tuple[float | None, float | None, bool]:
        """Topo reconstruído como estava no carimbo do servidor `carimbo`.

        Percorre de trás para frente porque o alvo é quase sempre um dos
        últimos estados — o histórico tem 64 entradas e a busca linear é mais
        barata que manter estrutura ordenada no hot path.
        """
        for ts_ms, bid, ask in reversed(estado.historico):
            if ts_ms <= carimbo:
                return bid, ask, True
        # Nenhum estado com carimbo ≤ o da afirmação: ou o token ainda não
        # tinha snapshot, ou a desordem passou do histórico. Cai no livro
        # atual, que é a conta antiga — e isso é CONTADO, não escondido.
        return estado.livro.best_bid, estado.livro.best_ask, False

    def _conferir(
        self,
        asset_id: str,
        estado: _EstadoDoToken,
        ts_ns: int,
        carimbo: float,
        best_bid: float | None,
        best_ask: float | None,
        nosso_bid: float | None,
        nosso_ask: float | None,
        *,
        origem: str,
        alinhado: bool,
    ) -> list[Divergencia]:
        populacao = self.por_carimbo if alinhado else self.por_chegada
        achados: list[Divergencia] = []
        for lado, afirmado, nosso in (
            ("bid", best_bid, nosso_bid),
            ("ask", best_ask, nosso_ask),
        ):
            if afirmado is None:
                continue
            if not estado.com_snapshot:
                # Sem snapshot inicial não há reconstrução: comparar seria
                # comparar contra chute. Não entra em `comparacoes` nem em
                # `divergencias` — não é a reconstrução errando, é a ausência
                # dela. Vira TEMPO sem livro, que é o que a marca de
                # qualidade cobra.
                if alinhado:
                    self.observacoes_sem_snapshot += 1
                    self.sem_livro_por_causa[
                        "apos_perda" if estado.aguardando_resync else "sem_snapshot"
                    ] += 1
                    estado.abrir_sem_livro(carimbo)
                continue
            populacao.comparacoes += 1
            if nosso is not None and abs(afirmado - nosso) <= self.tolerancia:
                if alinhado:
                    self._fechar_aberta(estado, lado, carimbo)
                    estado.fechar_sem_livro(carimbo)
                continue
            magnitude = abs(afirmado - nosso) if nosso is not None else float("inf")
            motivo = estado.livro.motivo_vazio[lado] if nosso is None else None
            divergencia = Divergencia(
                asset_id=asset_id,
                ts_ns=ts_ns,
                lado=lado,
                servidor=afirmado,
                reconstruido=nosso,
                magnitude=magnitude,
                motivo_vazio=motivo,
                origem=origem,
            )
            populacao.registrar(magnitude, motivo)
            if alinhado:
                self.divergencias_por_token[asset_id] += 1
                estado.divergencias += 1
                if math.isinf(magnitude):
                    if MOTIVOS_DE_LADO_VAZIO.get(motivo or "", True):
                        estado.abrir_sem_livro(carimbo)
                    else:
                        # Truncagem de profundidade: o livro existe e está
                        # certo até onde vai. Não conta tempo contra o token.
                        estado.fechar_sem_livro(carimbo)
                    # Lado vazio não conta tempo divergente: é outra doença,
                    # e misturar as duas populações foi o erro do M2.2.
                    self._fechar_aberta(estado, lado, carimbo)
                else:
                    estado.pior_magnitude = max(estado.pior_magnitude, magnitude)
                    if magnitude > self.magnitude_minima:
                        self._abrir_ou_estender(estado, lado, carimbo, magnitude)
                    else:
                        self._fechar_aberta(estado, lado, carimbo)
            if len(self.amostras) < self.max_amostras:
                self.amostras.append(divergencia)
            achados.append(divergencia)
        return achados

    @staticmethod
    def _abrir_ou_estender(
        estado: _EstadoDoToken, lado: str, carimbo: float, magnitude: float
    ) -> None:
        aberta = estado.abertas.get(lado)
        if aberta is None:
            estado.abertas[lado] = (carimbo, magnitude)
        else:
            estado.abertas[lado] = (aberta[0], max(aberta[1], magnitude))

    def _fechar_aberta(
        self, estado: _EstadoDoToken, lado: str, carimbo: float
    ) -> None:
        aberta = estado.abertas.pop(lado, None)
        if aberta is None:
            return
        inicio, magnitude = aberta
        duracao = max(0.0, carimbo - inicio)
        if duracao > self.persistencia_min_ms:
            estado.persistentes += 1
            estado.ms_divergentes += duracao
            estado.magnitude_persistente_max = max(
                estado.magnitude_persistente_max, magnitude
            )

    # ----------------------------------------------------------- interpretação
    def qualidade_do_token(self, asset_id: str) -> str:
        """`alta` | `media` | `baixa` | `sem_dado` — os critérios do §2c.

        A pergunta que isto responde não é "houve divergência?" (houve sempre,
        e a maioria é corrida de milissegundos) e sim "este livro descreveu o
        mercado por tempo suficiente para eu confiar num preço de entrada?".
        """
        estado = self.estados.get(asset_id)
        if estado is None or estado.ts_ultimo_ms is None:
            return "sem_dado"
        if not estado.teve_snapshot:
            # Nunca recebeu livro inicial: não há reconstrução para julgar.
            return "baixa"
        if estado.magnitude_persistente_max > MAGNITUDE_CRITICA:
            return "baixa"
        fracao = estado.fracao_ruim
        if fracao > self.fracao_media:
            return "baixa"
        if (
            fracao <= self.fracao_alta
            and estado.magnitude_persistente_max <= MAGNITUDE_GRAVE
        ):
            return "alta"
        return "media"

    def token_corrompido(self, asset_id: str) -> bool:
        """O livro deste token é confiável o bastante para o backtest?

        Compatibilidade: continua sendo o gate binário que o recorder e o
        backtest chamavam no M2.2/M2.3 — só que agora "corrompido" quer dizer
        `qualidade == baixa`, e não "divergiu um tick uma vez".
        """
        return self.qualidade_do_token(asset_id) == "baixa"

    def qualidade_da_janela(self, *tokens: str) -> str:
        """A pior marca entre os tokens da janela — Up e Down são um par.

        Não adianta o livro do Up estar impecável se o do Down está furado: a
        entrada precisa dos dois lados para ter preço.
        """
        marcas = [self.qualidade_do_token(t) for t in tokens if t]
        conhecidas = [m for m in marcas if m in ORDEM_QUALIDADE]
        if not conhecidas:
            return "sem_dado"
        return min(conhecidas, key=lambda m: ORDEM_QUALIDADE[m])

    def _resumo_dos_books(self) -> dict[str, Any]:
        """O que os snapshots de livro traziam de verdade (M2.6 BUG 4).

        Responde a duas perguntas com uma medição só:

        1. **A forma está certa?** `formas` diz com que par de chaves o
           servidor manda os lados. Qualquer coisa fora de `bids+asks` explica
           `vazio_desde_o_snapshot` em massa sem que exista lado vazio nenhum
           — seria o defeito do `price_change` (API_NOTES 6.1b) de novo.
        2. **Quantos níveis reter?** Os percentis de níveis POR LADO no evento
           cru. O p99 é a resposta direta a "quantos níveis fazem o lado vazio
           por truncagem cair abaixo de 1%" — abaixo do p99, 1% dos snapshots
           já chega com menos níveis do que se quer reter.
        """
        bids = sorted(self.niveis_bid)
        asks = sorted(self.niveis_ask)
        recomendado = max(
            int(_percentil(bids, 99) or 0), int(_percentil(asks, 99) or 0)
        )
        return {
            "eventos": self.books_observados,
            "formas": dict(self.formas_de_book),
            "com_bid_vazio": self.books_com_bid_vazio,
            "com_ask_vazio": self.books_com_ask_vazio,
            "niveis_por_lado": {
                "bid": {
                    "p50": _percentil(bids, 50),
                    "p90": _percentil(bids, 90),
                    "p99": _percentil(bids, 99),
                    "max": max(bids) if bids else None,
                },
                "ask": {
                    "p50": _percentil(asks, 50),
                    "p90": _percentil(asks, 90),
                    "p99": _percentil(asks, 99),
                    "max": max(asks) if asks else None,
                },
            },
            "niveis_recomendados_por_lado": recomendado or None,
            "nota": (
                "M2.6 BUG 4. ATENCAO A PREMISSA: `--niveis-por-lado` NAO "
                "influencia `vazio_desde_o_snapshot`. Aquele flag trunca o "
                "`BookTimeline` da passada 2; este monitor le o evento CRU na "
                "passada 1, com todos os niveis. Entao lado vazio aqui e o "
                "evento gravado parseando vazio — ou porque veio vazio, ou "
                "porque a chave tem outro nome (veja `formas`). "
                "`niveis_recomendados_por_lado` e o p99 dos niveis vistos: e "
                "a recomendacao para `--niveis-por-lado`, que afeta o "
                "SIMULADOR de fills, nao este contador."
            ),
        }

    def _resumo_da_desordem(
        self, deltas: int, snapshots: int
    ) -> dict[str, Any]:
        """Quanto o carimbo do servidor chega fora de ordem, e o que fazer.

        O leitor ordena por `ts_mono_ns` (chegada local) com um buffer
        limitado; o que sobra é desordem de CARIMBO, que buffer nenhum
        conserta — são eixos diferentes. Medir a magnitude é o que separa
        "aumentar o buffer resolve" de "não resolve".
        """
        atrasos = sorted(self.atrasos_de_carimbo_ms)
        return {
            "deltas": deltas,
            "snapshots": snapshots,
            "atraso_ms": {
                "p50": _percentil(atrasos, 50),
                "p90": _percentil(atrasos, 90),
                "p99": _percentil(atrasos, 99),
                "max": round(max(atrasos), 3) if atrasos else None,
            },
            "amostras": len(atrasos),
            "nota": (
                "M2.6 BUG 4.3. `atraso_ms` e quanto o carimbo do servidor "
                "veio atras do maior ja visto NAQUELE token. Se o p99 for de "
                "poucas centenas de ms, a desordem e do FIO (o servidor "
                "publica fora de ordem) e aumentar o buffer de reordenacao "
                "do leitor nao muda nada — ele ordena por chegada local, "
                "outro eixo. Se for de dezenas de segundos, ha reordenacao "
                "grosseira e o buffer merece revisao. O leitor ja conta "
                "`fora_de_ordem` no eixo dele; os dois numeros respondem "
                "perguntas diferentes e nao devem ser somados."
            ),
        }

    def resumo(self) -> dict[str, Any]:
        alinhado = self.por_carimbo.resumo(self.tick_mercado)
        bruto = self.por_chegada.resumo(self.tick_mercado)
        contagem_qualidade: Counter[str] = Counter()
        for token in self.estados:
            contagem_qualidade[self.qualidade_do_token(token)] += 1
        fora_de_ordem = sum(e.deltas_fora_de_ordem for e in self.estados.values())
        snapshots_fora = sum(
            e.snapshots_fora_de_ordem for e in self.estados.values()
        )
        persistentes = sum(e.persistentes for e in self.estados.values())
        ms_sem_livro = sum(e.ms_sem_livro for e in self.estados.values())
        sem_livro_max = max(
            (e.fracao_sem_livro for e in self.estados.values()), default=0.0
        )
        return {
            **alinhado,
            "alinhamento": {
                "por_carimbo_do_servidor": alinhado,
                "por_chegada_local": bruto,
                "afirmacoes_sem_estado_alinhado": self.sem_estado_alinhado,
                "deltas_com_carimbo_fora_de_ordem": fora_de_ordem,
                "snapshots_com_carimbo_fora_de_ordem": snapshots_fora,
                "nota": (
                    "M2.5 tarefa 1. `por_chegada_local` e' a conta do M2.2: o "
                    "topo afirmado contra o livro 'atual' na ordem em que os "
                    "eventos chegaram aqui. `por_carimbo_do_servidor` compara "
                    "contra o livro depois de aplicados todos os deltas com "
                    "carimbo <= o da afirmacao. A diferenca entre as duas so "
                    "aparece em `best_bid_ask`: no `price_change` o topo "
                    "autoritativo vem na MESMA mensagem, ja alinhado por "
                    "construcao. Se as duas contas baterem, a desordem local "
                    "nao era a causa das divergencias. "
                    "`*_com_carimbo_fora_de_ordem` alto e um achado por si so: "
                    "a gravacao chegou ao disco fora de ordem alem do que o "
                    "buffer de reordenacao do leitor absorve, e nenhum "
                    "alinhamento conserta livro reconstruido de tras para "
                    "frente — o que conserta e subir o buffer."
                ),
            },
            "lado_vazio": {
                "por_causa": alinhado["lado_vazio_por_causa"],
                "quais_invalidam": MOTIVOS_DE_LADO_VAZIO,
                "sem_livro_por_causa": dict(self.sem_livro_por_causa),
                "ms_sem_livro_total": round(ms_sem_livro, 1),
                "pior_fracao_sem_livro": round(sem_livro_max, 6),
                "nota": (
                    "M2.5 tarefa 2. `vazio_desde_o_snapshot` e "
                    "`esvaziado_por_delta` NAO invalidam: sao truncagem de "
                    "profundidade, o servidor mostra um nivel que nunca nos "
                    "foi contado. Sao sinal de que --niveis-book deve ser "
                    "maior, nao de livro furado. `sem_snapshot` e `apos_perda` "
                    "nao sao divergencia nenhuma — sao AUSENCIA de livro, "
                    "contadas em `sem_livro_por_causa` e cobradas em TEMPO "
                    "(`ms_sem_livro_total`), pelo mesmo criterio de fracao que "
                    "a divergencia. Um token que ficou 200 ms sem livro no "
                    "comeco da vida nao e o mesmo que um que passou a janela "
                    "inteira sem."
                ),
            },
            "criterio_de_invalidacao": {
                "magnitude_minima": round(self.magnitude_minima, 6),
                "ticks_de_mercado": max(self.ticks_divergencia, TICKS_MIN_DIVERGENCIA),
                "tick_mercado": self.tick_mercado,
                "persistencia_min_ms": self.persistencia_min_ms,
                "fracao_max_media": self.fracao_media,
                "fracao_max_alta": self.fracao_alta,
                "magnitude_grave": MAGNITUDE_GRAVE,
                "magnitude_critica": MAGNITUDE_CRITICA,
                "divergencias_persistentes": persistentes,
                "nota": (
                    "M2.5 tarefa 3, registrado em VEREDITO_M2 §2c ANTES de "
                    "rodar. Invalida so a conjuncao: magnitude > K ticks de "
                    "mercado E persistencia > limite E fracao de tempo "
                    "divergente acima do teto. O limiar antigo (0,01) era "
                    "exatamente UM tick de mercado e reprovou 200 de 200 "
                    "janelas medindo corrida, nao corrupcao."
                ),
            },
            "snapshots_de_livro": self._resumo_dos_books(),
            "desordem_de_carimbo": self._resumo_da_desordem(fora_de_ordem, snapshots_fora),
            "qualidade_dos_tokens": dict(contagem_qualidade),
            "tokens_divergentes": len(self.divergencias_por_token),
            "tokens_corrompidos": sorted(
                t for t in self.estados if self.token_corrompido(t)
            )[:50],
            "tokens_aguardando_resync": len(self.aguardando_resync),
            "observacoes_sem_snapshot": self.observacoes_sem_snapshot,
            "limiar_invalidacao": round(self.magnitude_minima, 6),
            "tolerancia": self.tolerancia,
            "formas_de_price_change": dict(self.formas_de_price_change),
            "amostras": [d.to_dict() for d in self.amostras],
            "nota": (
                "Duas populacoes, dois diagnosticos. `com_magnitude_finita` = "
                "topo deslocado: p50/p99 em ticks distinguem ruido de timing "
                "(~1 tick de mercado) de corrupcao real (muitos ticks). "
                "`com_lado_vazio` = o servidor afirma um topo e a reconstrucao "
                "nao tem lado NENHUM — ver bloco `lado_vazio` para a causa, "
                "que decide se invalida. Os numeros no topo deste bloco sao os "
                "ALINHADOS por carimbo do servidor; a conta antiga fica em "
                "`alinhamento.por_chegada_local` para comparacao."
            ),
        }


def _amostrar(reservatorio: list[float], valor: float, total: int) -> None:
    """Reservatório em rodízio, com teto. Determinístico de propósito.

    O replay precisa reproduzir o mesmo relatório duas vezes; amostragem
    aleatória quebraria isso. Ao encher, substitui em rodízio — preserva a
    cauda recente sem crescer sem limite em 72h de gravação.
    """
    if len(reservatorio) < MAX_MAGNITUDES:
        reservatorio.append(float(valor))
    else:
        reservatorio[total % MAX_MAGNITUDES] = float(valor)


def _carimbo_ms(evento: dict[str, Any], ts_ns: int) -> float:
    """Carimbo do SERVIDOR em ms, com a chegada local como último recurso.

    O alinhamento existe justamente porque a chegada local não serve; mas um
    evento sem `timestamp` não pode derrubar a comparação inteira, então ele
    cai para a chegada e vira, no pior caso, o comportamento antigo.
    """
    valor = _numero(evento.get("timestamp"))
    if valor is not None and valor > 0:
        return valor
    return ts_ns / 1e6


def _em_ticks(magnitude: float | None, tick: float) -> float | None:
    if magnitude is None or tick <= 0:
        return None
    return round(magnitude / tick, 1)


class MonitorDeRelogio:
    """Offset entre o relógio local e o carimbo do servidor (M2.2 A.4).

    O modelo endgame depende de `seconds_left`: com 60s de janela, 2s de
    deriva de relógio erram em 3% a fração de TWAP já travada, e o erro entra
    no backtest como se fosse sinal. Deriva não avisa — por isso é medida.

    Só o offset RELATIVO importa aqui. Latência de rede e offset de relógio
    entram somados nesta conta e não são separáveis com um carimbo só; por
    isso o número é lido como teto do erro, não como o erro do relógio.
    """

    __slots__ = ("amostras", "max_amostras", "total")

    def __init__(self, max_amostras: int = 20_000) -> None:
        self.amostras: list[float] = []
        self.max_amostras = max_amostras
        self.total = 0

    def observar(self, carimbo_servidor_ms: float, chegada_wall_ns: int) -> None:
        if carimbo_servidor_ms <= 0:
            return
        self.total += 1
        offset_ms = chegada_wall_ns / 1e6 - carimbo_servidor_ms
        if len(self.amostras) < self.max_amostras:
            self.amostras.append(offset_ms)
        else:
            # Reservatório simples e determinístico: substitui em rodízio, o
            # que preserva a cauda recente sem depender de aleatoriedade
            # (que quebraria a reprodutibilidade do replay).
            self.amostras[self.total % self.max_amostras] = offset_ms

    def resumo(self) -> dict[str, Any]:
        ordenadas = sorted(self.amostras)
        return {
            "amostras": self.total,
            "p50_ms": _percentil(ordenadas, 50),
            "p99_ms": _percentil(ordenadas, 99),
            "min_ms": round(min(ordenadas), 3) if ordenadas else None,
            "max_ms": round(max(ordenadas), 3) if ordenadas else None,
            "nota": (
                "chegada_local - carimbo_do_servidor. Inclui latencia de rede, "
                "então é TETO do erro de relógio, não o erro em si. Valor "
                "estável e pequeno = relógio são; deriva crescente ao longo da "
                "gravação = NTP ausente ou quebrado (ver runbook §4.1)."
            ),
        }


def _niveis(bruto: Any) -> dict[float, float]:
    saida: dict[float, float] = {}
    if not isinstance(bruto, list):
        return saida
    for item in bruto:
        if not isinstance(item, dict):
            continue
        preco = _numero(item.get("price"))
        tamanho = _numero(item.get("size"))
        if preco is not None and tamanho is not None and tamanho > 0:
            saida[preco] = tamanho
    return saida


def _numero(valor: Any) -> float | None:
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


def _percentil(ordenadas: list[float], pct: float) -> float | None:
    if not ordenadas:
        return None
    rank = max(1, min(len(ordenadas), int(-(-pct * len(ordenadas) // 100))))
    return round(ordenadas[rank - 1], 6)
