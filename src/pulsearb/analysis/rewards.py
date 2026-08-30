"""Simulador do score de liquidity rewards (M2.2 B.1 e B.5) — MEDIÇÃO.

Nada aqui envia ordem. É tudo reconstrução sobre gravação: "se tivéssemos
cotado X shares a Y ticks do topo durante esta janela, que fatia do pool
teríamos capturado?".

Por que vale a pena medir isto antes de qualquer coisa: o edge do taker é
**preditivo** — depende de a nossa estimativa de probabilidade bater melhor
que a do book, o que é especulativo por natureza. O reward do maker é
**mecânico**: é uma fórmula sobre o livro, com um orçamento diário conhecido,
dividido pro-rata. Sobre gravação, a segunda é simulável com fidelidade muito
maior que a primeira.

═══════════════════════════════════════════════════════════════════════════
O QUE ESTÁ VERIFICADO E O QUE ESTÁ ASSUMIDO — leia antes de usar o número
═══════════════════════════════════════════════════════════════════════════

**VERIFICADO** (fonte primária: código do SDK oficial `polymarket-client`
0.6.0 + leitura ao vivo da Gamma/CLOB, ver `docs/API_NOTES.md` 12.8 e 15):

- os parâmetros existem e são POR MERCADO: `rewardsMinSize`,
  `rewardsMaxSpread`, e uma lista `rewards_config` com `rate_per_day`,
  `start_date`/`end_date` e `asset_address`
- há `native_daily_rate`, `sponsored_daily_rate` e `total_daily_rate`
- existe `/order-scoring`, que responde **se** uma ordem pontua — booleano,
  sem dizer quanto
- existe um `market_competitiveness` por mercado, e um `earning_percentage`
  por usuário/mercado/dia

**CONFIRMADO em 2026-08-30** (fonte: docs.polymarket.com/programs/liquidity-rewards,
lido diretamente — ver `docs/API_NOTES.md` §15.3):

- **fórmula**: `S(v, s) = ((v - s) / v)² × b`, onde `v` = `rewardsMaxSpread`
  em centavos, `s` = spread real do ponto médio ajustado em centavos, `b` = 1
- **cadência**: 1 amostra/minuto → 10.080 amostras/epoch (1 semana)
- **`rewardsMaxSpread` em CENTAVOS**: confirmado (1.5¢, 4.5¢)
- **dois lados exigidos** com penalidade assimétrica (Q_min)
- **pool agosto 2026**: $1M para TWAP 5-min/15-min/4h em BTC, ETH, SOL, XRP, HYPE, BNB, DOGE

A fórmula exponencial por tick que estava aqui antes estava ERRADA. A
quadrática por spread (em centavos) é a correta. O M2.2 maker precisa re-rodar
com os novos parâmetros.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from pulsearb.backtest.book import OrderBook

# `rewardsMaxSpread` observado ao vivo: 1.5 e 4.5. Confirmado em 2026-08-30
# que a unidade é CENTAVOS (1,5¢ = 0,015 de probabilidade).
HIPOTESE_MAX_SPREAD_EM_CENTAVOS = True


@dataclass(frozen=True, slots=True)
class ParametrosDeReward:
    """Parâmetros de um mercado. Os verificados vêm do dado; os outros são
    hipótese, e estão aqui em vez de dentro da conta justamente por isso."""

    daily_rate: float           # VERIFICADO: rewardsDailyRate / rate_per_day
    min_size: float             # VERIFICADO: rewardsMinSize
    max_spread: float           # VERIFICADO: rewardsMaxSpread / 100 (centavos → fração)
    tick_size: float            # VERIFICADO
    exige_dois_lados: bool = False  # CONFIRMADO: Q_min exige dois lados

    @classmethod
    def do_mercado(
        cls,
        meta: dict[str, Any],
    ) -> ParametrosDeReward | None:
        """Lê os parâmetros do snapshot gravado. Sem eles, não simula.

        Devolver `None` em vez de cair para um default é deliberado: um
        mercado sem `rewardsDailyRate` no dado é um mercado sem pool, e
        inventar um número aqui produziria receita onde não há nenhuma.
        """
        taxa = _numero(meta.get("rewards_daily_rate"))
        if taxa is None or taxa <= 0:
            return None
        bruto_spread = _numero(meta.get("rewards_max_spread"))
        if bruto_spread is None:
            return None
        max_spread = bruto_spread / 100.0 if HIPOTESE_MAX_SPREAD_EM_CENTAVOS else bruto_spread
        return cls(
            daily_rate=taxa,
            min_size=_numero(meta.get("rewards_min_size")) or 0.0,
            max_spread=max_spread,
            tick_size=_numero(meta.get("tick_size")) or 0.01,
        )


#: Os campos que uma janela precisa ter para a simulação de reward existir.
CHAVES_DE_REWARD = ("rewards_daily_rate", "rewards_max_spread", "rewards_min_size")


def _forma_do_bruto(meta: dict[str, Any]) -> str:
    """Que forma o `rewards_bruto` gravado tem — a chave do diagnóstico.

    M2.7 tarefa 2. Antes deste marco o recorder gravava só três campos
    derivados e jogava fora o `raw_gamma`, então `rewards_daily_rate: None`
    era um beco sem saída: não dava para saber se a lista não existia, se
    existia com outro nome de campo, ou se existia com vigência expirada.

    `__nao_gravado__` é o que aparece em gravação feita ANTES do M2.7 — e
    dizer isso é melhor que fingir que a ausência é informação sobre o
    mercado.
    """
    bruto = meta.get("rewards_bruto")
    if not isinstance(bruto, dict):
        return "__nao_gravado__"
    chave = bruto.get("chave_da_lista")
    if chave is None:
        return "sem_lista_de_rewards"
    n = bruto.get("n_entradas") or 0
    if not n:
        return f"{chave}:lista_vazia"
    chaves = bruto.get("chaves_das_entradas") or []
    tem_data = any("date" in str(k).lower() for k in chaves)
    return f"{chave}:{n}_entradas{':com_vigencia' if tem_data else ''}"


def _motivo_sem_pool(meta: dict[str, Any]) -> str:
    """Por que esta janela ficou de fora da conta de reward.

    A ordem das perguntas é a ordem em que `do_mercado` desiste, para o motivo
    reportado ser o motivo real e não o primeiro que der match.
    """
    if not meta:
        return "sem_reward_meta"
    taxa = _numero(meta.get("rewards_daily_rate"))
    if taxa is None:
        return "sem_taxa_diaria"
    if taxa <= 0:
        return "taxa_diaria_zero"
    if _numero(meta.get("rewards_max_spread")) is None:
        return "sem_max_spread"
    return "desconhecido"


@dataclass(frozen=True, slots=True)
class OrdemHipotetica:
    """A cotação que teríamos deixado no livro. Nunca enviada."""

    tamanho: float
    distancia_ticks: int = 1
    dois_lados: bool = True

    @property
    def nome(self) -> str:
        lados = "2 lados" if self.dois_lados else "1 lado"
        return f"{self.tamanho:g} shares @ {self.distancia_ticks} tick(s), {lados}"


# ─── o score ──────────────────────────────────────────────────────────────
def score_de_nivel(
    preco: float,
    tamanho: float,
    *,
    meio: float | None,
    params: ParametrosDeReward,
) -> float:
    """S(v, s) = ((v - s) / v)² × tamanho — fórmula confirmada em 2026-08-30.

    `v` = params.max_spread (fração, já convertida de centavos).
    `s` = distância do preço ao ponto médio (mesma unidade).

    Portões (confirmados):
    - abaixo de `min_size`: não pontua;
    - além de `max_spread` do meio: não pontua (score seria zero ou negativo).
    """
    if tamanho < params.min_size:
        return 0.0
    if params.max_spread <= 0:
        return 0.0
    spread = abs(preco - meio) if meio is not None else 0.0
    if spread >= params.max_spread:
        return 0.0
    ratio = spread / params.max_spread
    return (1.0 - ratio) ** 2 * tamanho


def score_do_livro(book: OrderBook, params: ParametrosDeReward) -> float:
    """Score de TODOS os makers já presentes no livro, os dois lados.

    Limitação estrutural, e ela é grande: o WS entrega níveis AGREGADOS, não
    ordens. Então este número é o score do nível inteiro, sem saber entre
    quantos participantes ele se divide — o que basta para o denominador
    pro-rata (a soma é a mesma), mas impede qualquer afirmação sobre posição
    na fila. Ver B.4 em `docs/VEREDITO_M2.md`.
    """
    meio = book.mid
    total = 0.0
    for niveis in (book.bids, book.asks):
        for preco, tamanho in niveis:
            total += score_de_nivel(preco, tamanho, meio=meio, params=params)
    return total


def score_da_ordem(
    ordem: OrdemHipotetica, book: OrderBook, params: ParametrosDeReward
) -> float:
    """O score que a NOSSA cotação teria, ao lado das que já estão no livro."""
    meio = book.mid
    if meio is None:
        return 0.0
    total = 0.0
    lados = (
        (book.best_bid, -1),
        (book.best_ask, +1),
    )
    for melhor, sentido in lados:
        if melhor is None:
            continue
        preco = melhor + sentido * ordem.distancia_ticks * params.tick_size
        if not 0.0 < preco < 1.0:
            continue
        total += score_de_nivel(preco, ordem.tamanho, meio=meio, params=params)
        if not ordem.dois_lados:
            break
    if params.exige_dois_lados and (book.best_bid is None or book.best_ask is None):
        return 0.0
    return total


def fatia_do_pool(
    nosso_score: float, score_do_mercado: float
) -> float:
    """`nosso / (mercado + nosso)`. Zero se não pontuamos."""
    denominador = score_do_mercado + nosso_score
    if denominador <= 0 or nosso_score <= 0:
        return 0.0
    return nosso_score / denominador


# ─── a simulação sobre a gravação ─────────────────────────────────────────
@dataclass
class ResultadoDeReward:
    """Acumulador de uma combinação (ordem, recorte)."""

    segundos: float = 0.0
    receita_usdc: float = 0.0
    score_do_mercado: float = 0.0
    nosso_score: float = 0.0
    amostras: int = 0
    orcamento_por_score: list[float] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        horas = self.segundos / 3600.0
        return {
            "horas_de_amostra": round(horas, 3),
            "receita_usdc": round(self.receita_usdc, 6),
            "receita_usdc_por_hora": (
                round(self.receita_usdc / horas, 6) if horas > 0 else None
            ),
            "score_medio_do_mercado": (
                round(self.score_do_mercado / self.amostras, 3) if self.amostras else 0.0
            ),
            "nosso_score_medio": (
                round(self.nosso_score / self.amostras, 3) if self.amostras else 0.0
            ),
            "fatia_media": (
                round(self.nosso_score / (self.score_do_mercado + self.nosso_score), 6)
                if (self.score_do_mercado + self.nosso_score) > 0
                else 0.0
            ),
            "amostras": self.amostras,
        }


def simular_serie(
    amostras: list[tuple[int, OrderBook]],
    params: ParametrosDeReward,
    ordem: OrdemHipotetica,
) -> ResultadoDeReward:
    """Percorre os snapshots de um token e acumula receita.

    Cada snapshot vale o intervalo até o próximo (integração por retângulos à
    esquerda). Com a decimação do `BookTimeline`, os snapshots não são
    equiespaçados, e é por isso que o peso é o intervalo real e não 1 —
    tratar todos como iguais superestimaria os períodos calmos, em que o topo
    não muda e quase não há snapshot.
    """
    resultado = ResultadoDeReward()
    if not amostras:
        return resultado
    por_segundo = params.daily_rate / 86400.0
    for indice, (ts_ns, book) in enumerate(amostras):
        proximo = amostras[indice + 1][0] if indice + 1 < len(amostras) else None
        if proximo is None:
            break
        intervalo_s = max(0.0, (proximo - ts_ns) / 1e9)
        if intervalo_s <= 0:
            continue
        mercado = score_do_livro(book, params)
        nosso = score_da_ordem(ordem, book, params)
        fatia = fatia_do_pool(nosso, mercado)
        resultado.segundos += intervalo_s
        resultado.receita_usdc += fatia * por_segundo * intervalo_s
        resultado.score_do_mercado += mercado
        resultado.nosso_score += nosso
        resultado.amostras += 1
        if mercado > 0:
            resultado.orcamento_por_score.append(por_segundo / mercado)
    return resultado


@dataclass(slots=True)
class _SemPool:
    """A contabilidade das janelas que NÃO têm pool de reward.

    M2.6 BUG 5: `janelas_com_pool_de_reward: 0` saía sem dizer por quê, e zero
    silencioso é indistinguível de bug. Cada recusa nomeia a causa, e os
    campos vistos vão junto — se algum mudou de nome no fio, é aqui que
    aparece.
    """

    motivos: Counter[str] = field(default_factory=Counter)
    formas: Counter[str] = field(default_factory=Counter)
    presentes: Counter[str] = field(default_factory=Counter)
    ausentes: Counter[str] = field(default_factory=Counter)
    duracoes: Counter[str] = field(default_factory=Counter)

    def registrar(self, meta: dict[str, Any]) -> None:
        self.motivos[_motivo_sem_pool(meta)] += 1
        for chave in CHAVES_DE_REWARD:
            if meta.get(chave) is not None:
                self.presentes[chave] += 1
            else:
                self.ausentes[chave] += 1
        self.formas[_forma_do_bruto(meta)] += 1
        self.duracoes[str(meta.get("duracao_s") or "?")] += 1


def simular(
    janelas: list[Any],
    *,
    ordens: tuple[OrdemHipotetica, ...] = (),
) -> dict[str, Any]:
    """A simulação completa (B.1) mais a seleção de mercado (B.5).

    `janelas` são `WindowState` do backtest, já com `books` preenchido e com
    os parâmetros de reward lidos do snapshot de descoberta.
    """
    ordens = ordens or ORDENS_PADRAO
    saida: dict[str, Any] = {
        "hipoteses": {
            "max_spread_em_centavos": HIPOTESE_MAX_SPREAD_EM_CENTAVOS,
            "cadencia_amostras_por_min": 1,
            "aviso": (
                "Fórmula CONFIRMADA em 2026-08-30 — ver docs/API_NOTES.md §15.3. "
                "S(v,s)=((v-s)/v)^2 x tamanho, v=rewardsMaxSpread em centavos. "
                "A integral aqui é contínua (intervalo real dos snapshots), não "
                "discreta de 1/min — isso aproxima para cima em intervalos longos."
            ),
        }
    }

    por_ordem: dict[str, dict[str, ResultadoDeReward]] = defaultdict(
        lambda: defaultdict(ResultadoDeReward)
    )
    competicao: dict[str, list[float]] = defaultdict(list)
    janelas_com_pool = 0
    sem_pool = _SemPool()
    duracoes_com_pool: Counter[str] = Counter()

    for janela in janelas:
        meta = getattr(janela, "reward_meta", None) or {}
        base = ParametrosDeReward.do_mercado(meta)
        if base is None:
            sem_pool.registrar(meta)
            continue
        janelas_com_pool += 1
        duracoes_com_pool[str(meta.get('duracao_s') or '?')] += 1
        recortes = _recortes(janela)
        for token in (janela.token_up, janela.token_down):
            timeline = janela.books.get(token)
            if timeline is None or not timeline.ts:
                continue
            amostras = list(zip(timeline.ts, timeline.books, strict=False))
            for ordem in ordens:
                resultado = simular_serie(amostras, base, ordem)
                for recorte in recortes:
                    _somar(por_ordem[ordem.nome][recorte], resultado)
                if resultado.orcamento_por_score:
                    for recorte in recortes:
                        competicao[recorte].extend(resultado.orcamento_por_score)

    saida["janelas_com_pool_de_reward"] = janelas_com_pool
    saida["duracoes_com_pool"] = dict(duracoes_com_pool)
    saida["janelas_sem_pool_de_reward"] = {
        "total": sum(sem_pool.motivos.values()),
        "por_motivo": dict(sem_pool.motivos),
        "campos_presentes": dict(sem_pool.presentes),
        "campos_ausentes": dict(sem_pool.ausentes),
        "forma_do_rewards_bruto": dict(sem_pool.formas),
        "duracoes_sem_pool": dict(sem_pool.duracoes),
        "nota": (
            "M2.6 BUG 5. A cadeia do dado esta INTEIRA e foi conferida: a "
            "descoberta guarda `raw_gamma`, o recorder extrai "
            "`rewards_daily_rate` de `clobRewards` (somando as fontes, "
            "API_NOTES 12.8) mais `rewardsMinSize`/`rewardsMaxSpread`, e o "
            "backtest le os tres para `reward_meta`. Entao zero janela com "
            "pool NAO e campo que ninguem le. Sobram duas leituras, e "
            "`por_motivo` separa as duas: `sem_taxa_diaria` em massa quer "
            "dizer que os mercados updown gravados nao participam do "
            "programa de rewards — que e um ACHADO sobre o programa, nao um "
            "defeito nosso, e derruba a rota maker como fonte de receita "
            "nestes mercados. Ja `sem_max_spread` com taxa presente quer "
            "dizer campo faltando na Gamma, e ai a conta e recuperavel."
        ),
    }
    saida["por_ordem"] = {
        nome: {recorte: r.to_dict() for recorte, r in sorted(recortes.items())}
        for nome, recortes in sorted(por_ordem.items())
    }
    saida["selecao_de_mercado"] = {
        recorte: {
            "orcamento_por_unidade_de_score_p50": _p50(valores),
            "amostras": len(valores),
        }
        for recorte, valores in sorted(competicao.items())
    }
    saida["nota_selecao_de_mercado"] = (
        "B.5: orçamento por unidade de score (USDC/s por ponto de score). É "
        "quanto o mercado paga por unidade de liquidez — quanto MAIOR, mais "
        "fraca a concorrência ali. O tamanho do pool sozinho não diz nada: "
        "pool grande com muito score concorrente paga menos por share que "
        "pool pequeno e vazio."
    )
    return saida


ORDENS_PADRAO = (
    OrdemHipotetica(tamanho=50.0, distancia_ticks=1, dois_lados=True),
    OrdemHipotetica(tamanho=50.0, distancia_ticks=3, dois_lados=True),
    OrdemHipotetica(tamanho=200.0, distancia_ticks=1, dois_lados=True),
    OrdemHipotetica(tamanho=50.0, distancia_ticks=1, dois_lados=False),
)


def _recortes(janela: Any) -> tuple[str, ...]:
    """Os recortes que o relatório pede: total, duração, ativo e faixa horária."""
    hora = int((janela.close_ts_ns / 1e9) // 3600 % 24)
    return (
        "total",
        f"duracao={janela.duracao_s}s",
        f"ativo={janela.asset or '?'}",
        f"hora_utc={hora:02d}",
    )


def _somar(destino: ResultadoDeReward, origem: ResultadoDeReward) -> None:
    destino.segundos += origem.segundos
    destino.receita_usdc += origem.receita_usdc
    destino.score_do_mercado += origem.score_do_mercado
    destino.nosso_score += origem.nosso_score
    destino.amostras += origem.amostras


def _p50(valores: list[float]) -> float | None:
    if not valores:
        return None
    ordenados = sorted(valores)
    return round(ordenados[len(ordenados) // 2], 9)


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
