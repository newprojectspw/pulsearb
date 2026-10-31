"""Auditor diário do agente: lê o que o bot DECIDIU nas últimas 24 h e grita.

    python scripts/auditor_diario.py data/shadow/diario-20260924-044201-802197-41092.jsonl

Sai em no máximo 10 linhas, e a primeira começa com `ALERTA` sempre que há
decisão fora da política de risco, sinal de que o agente persegue o objetivo
de um jeito indesejado, ou estado que não dá para apurar. Código de saída
diferente de zero nos três casos, para o cron/systemd notar.

## As três fontes, e por que são estas

1. **O diário da rodada** (`data/shadow/*.jsonl`, `data/diarios/*.jsonl`) —
   o que o bot decidiu, linha por linha. Tem DUAS formas de linha, escritas
   por dois produtores diferentes, e o auditor precisa das duas: a decisão do
   portão de risco (sem a chave `evento`) e a cotação da rota maker em sombra
   (`cotacao_colocada`/`cotacao_cancelada`, de `execution/cliente_sombra.py`).
   Ler só uma delas daria um relatório que some com metade do dia.
2. **O registro do dia** (`data/risco/registro_do_dia*.json`) — exposição por
   janela, PnL, disjuntor e pausa. Lido por `RegistroDoDia.de_dict`, nunca por
   um segundo parser: o registro é o estado durável do risco, e duas leituras
   dele divergiriam no dia em que uma fosse corrigida.
3. **A política** (seção `risk:` do `config.yaml`, via `Settings.load`). Nenhum
   teto é cravado aqui. Um teto cravado no auditor mente no dia em que alguém
   sobe o do `config.yaml` — e mente dizendo que está tudo bem.

## O que o auditor é, e o que ele não é

Ele **não** recalcula a decisão. A pergunta não é "o portão decidiu certo?" —
é mais estreita e mais útil: *o que passou cabia na política que estava
escrita?* Um `pode: true` com custo acima do teto por trade é defeito do
portão ou política trocada no meio; nos dois casos quem lê o relatório tem de
ver o número.

## Falha fechada, e aqui ela é o produto

Diário ausente, linha que não parseia, registro ilegível, dia que não dá para
apurar: cada um vira RECUSA com nome de `MOTIVOS_DO_AUDITOR` e saída 2. Um
auditor que "não sabe" e diz que está tudo bem é pior que não ter auditor,
porque alguém decide LIVE a partir dele.

## Mesmo caminho

As contas da rota maker (colocadas, encerradas, repouso, e a conferência do
prefixo `sombra-` do RUNBOOK §10.1) saem de `conferir_diario` do
`scripts/resumo_da_rodada_maker.py` — a MESMA função que o resumo da rodada
usa. Um segundo leitor faria a divergência entre auditor e resumo parecer
achado quando seria diferença de código.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pulsearb.caminhos import caminho_de_diario_lido, caminho_de_relatorio_lido
from pulsearb.risk.gates import RegistroDoDia, caminho_do_registro_do_modo
from pulsearb.settings import RiskSettings, Settings


def _resumo() -> Any:
    """O `resumo_da_rodada_maker` carregado por caminho — `scripts/` não é pacote.

    Mesmo recurso que o `scripts/ajuste_do_encolhimento.py` usa para o
    `resumo_m2`, e pelo mesmo motivo: reaproveitar a função de leitura em vez
    de reimplementá-la é o que mantém auditor e resumo contando a mesma coisa.
    """
    caminho = Path(__file__).resolve().parent / "resumo_da_rodada_maker.py"
    spec = importlib.util.spec_from_file_location("resumo_da_rodada_maker", caminho)
    if spec is None or spec.loader is None:  # pragma: no cover - instalação quebrada
        raise RuntimeError(f"não consegui carregar {caminho}")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


_RESUMO = _resumo()
conferir_diario = _RESUMO.conferir_diario


# ──────────────────────────────────────────────────── motivos do auditor
#
# São constantes porque viram rótulo de métrica e linha de alarme no cron.
# Alerta em frase livre não vira nenhum dos dois: não se conta, não se
# compara entre dias, e não distingue "o bot raspou o teto" de "o bot não
# achou trade". Espelha a `MOTIVOS` de `risk/gates.py`, inclusive no `TODOS`.


class MOTIVOS_DO_AUDITOR:
    """Todo alerta e toda recusa do auditor têm um destes. Nenhum outro texto."""

    # ── decisão fora da política de risco (pergunta 3) ────────────────
    #: Custo da ordem liberada acima de `stake_max_por_trade_usdc`.
    STAKE_ACIMA_DO_TETO = "alerta_stake_acima_do_teto"
    #: Preço liberado fora de [`preco_minimo`, `preco_maximo`].
    PRECO_FORA_DA_FAIXA = "alerta_preco_fora_da_faixa"
    #: Spread no topo do livro acima de `spread_maximo` na hora da liberação.
    SPREAD_ACIMA_DO_TETO = "alerta_spread_acima_do_teto"
    #: `latencia_da_decisao_ms` acima de `atraso_max_ms`.
    LATENCIA_ACIMA_DO_TETO = "alerta_latencia_acima_do_teto"
    #: Soma liberada numa janela acima de `stake_max_por_janela_usdc`.
    JANELA_ACIMA_DO_TETO = "alerta_janela_acima_do_teto"
    #: Exposição do registro do dia acima de `exposicao_max_usdc`.
    EXPOSICAO_ACIMA_DO_TETO = "alerta_exposicao_acima_do_teto"
    #: Janelas com posição aberta acima de `posicoes_max_abertas`.
    POSICOES_ACIMA_DO_TETO = "alerta_posicoes_acima_do_teto"
    #: PnL do dia abaixo de −`perda_max_diaria_usdc`.
    PERDA_ACIMA_DO_TETO = "alerta_perda_acima_do_teto"
    #: O disjuntor está armado — o bot deveria estar parado.
    DISJUNTOR_ARMADO = "alerta_disjuntor_armado"
    #: Sequência de perdas no teto, ou pausa ainda em curso.
    PAUSA_EM_CURSO = "alerta_pausa_em_curso"
    #: `order_id` sem o prefixo `sombra-` — significaria ORDEM REAL (RUNBOOK
    #: §10.1, e a instrução ali é parar o serviço).
    ORDEM_REAL_NO_DIARIO = "alerta_ordem_real_no_diario"

    # ── mudança de estratégia (pergunta 4) ────────────────────────────
    #: O tamanho das ordens mudou no meio da janela E algum dos tamanhos
    #: usados estoura o teto por trade. Mudança que a política não permitia.
    MUDANCA_DE_ESTRATEGIA_NAO_PERMITIDA = "alerta_mudanca_de_estrategia_nao_permitida"

    # ── perseguição indesejada do objetivo (pergunta 5) ───────────────
    #: Raspou o teto por trade repetidamente, em vez de uma vez por acidente.
    RASPANDO_O_TETO = "alerta_raspando_o_teto"
    #: Concentrou o dia numa janela só.
    CONCENTRACAO_NUMA_JANELA = "alerta_concentracao_numa_janela"
    #: Colocou e cancelou sem deixar a cotação repousar.
    CHURN_DE_COTACOES = "alerta_churn_de_cotacoes"

    # ── falha fechada: o auditor não consegue apurar ──────────────────
    RECUSA_DIARIO_AUSENTE = "recusa_diario_ausente"
    RECUSA_DIARIO_ILEGIVEL = "recusa_diario_ilegivel"
    RECUSA_LINHA_CORROMPIDA = "recusa_linha_corrompida"
    RECUSA_REGISTRO_AUSENTE = "recusa_registro_ausente"
    RECUSA_REGISTRO_ILEGIVEL = "recusa_registro_ilegivel"
    RECUSA_DIA_INDETERMINADO = "recusa_dia_indeterminado"
    RECUSA_POLITICA_ILEGIVEL = "recusa_politica_ilegivel"

    ALERTAS = frozenset(
        {
            STAKE_ACIMA_DO_TETO,
            PRECO_FORA_DA_FAIXA,
            SPREAD_ACIMA_DO_TETO,
            LATENCIA_ACIMA_DO_TETO,
            JANELA_ACIMA_DO_TETO,
            EXPOSICAO_ACIMA_DO_TETO,
            POSICOES_ACIMA_DO_TETO,
            PERDA_ACIMA_DO_TETO,
            DISJUNTOR_ARMADO,
            PAUSA_EM_CURSO,
            ORDEM_REAL_NO_DIARIO,
            MUDANCA_DE_ESTRATEGIA_NAO_PERMITIDA,
            RASPANDO_O_TETO,
            CONCENTRACAO_NUMA_JANELA,
            CHURN_DE_COTACOES,
        }
    )

    RECUSAS = frozenset(
        {
            RECUSA_DIARIO_AUSENTE,
            RECUSA_DIARIO_ILEGIVEL,
            RECUSA_LINHA_CORROMPIDA,
            RECUSA_REGISTRO_AUSENTE,
            RECUSA_REGISTRO_ILEGIVEL,
            RECUSA_DIA_INDETERMINADO,
            RECUSA_POLITICA_ILEGIVEL,
        }
    )

    TODOS = ALERTAS | RECUSAS


def validar_motivo(nome: str) -> str:
    """Devolve `nome` se ele estiver em `MOTIVOS_DO_AUDITOR.TODOS`; senão levanta.

    A trava existe pelo mesmo motivo que o `__post_init__` da `Decisao` do
    portão: alerta anônimo não vira métrica nem alarme, e o lugar de descobrir
    isso é aqui, não no painel vazio seis meses depois.
    """
    if nome not in MOTIVOS_DO_AUDITOR.TODOS:
        raise ValueError(
            f"motivo de auditoria desconhecido: {nome!r}. Todo alerta precisa "
            "de um nome em MOTIVOS_DO_AUDITOR — alerta anônimo não vira "
            "métrica nem alarme."
        )
    return nome


class RecusaDoAuditor(Exception):
    """Estado que não dá para apurar. Carrega o motivo nomeado e os números."""

    def __init__(self, motivo: str, detalhe: dict[str, Any] | None = None) -> None:
        self.motivo = validar_motivo(motivo)
        if self.motivo not in MOTIVOS_DO_AUDITOR.RECUSAS:
            raise ValueError(f"{motivo!r} é alerta, não recusa")
        self.detalhe = detalhe or {}
        super().__init__(f"{self.motivo}: {self.detalhe}")


# ──────────────────────────────────────────────────────────── parâmetros
#
# Nenhum teto de risco mora aqui — esses vêm do `config.yaml`. O que mora
# aqui é a SENSIBILIDADE do auditor: o quanto de raspagem, concentração e
# recolocação ele trata como sinal. São nomeados e ajustáveis pela linha de
# comando porque são escolha de quem audita, não política de risco.

#: A janela auditada. "Diário" é isto.
HORAS_DA_JANELA = 24.0

#: Custo a partir desta fração do teto por trade conta como "raspou o teto".
FRACAO_DE_RASPAGEM = 0.95
#: Quantas raspagens fazem "repetidamente". Uma é acidente; três é padrão.
RASPAGENS_PARA_ALERTA = 3

#: Fração do movido numa única janela a partir da qual há concentração.
FRACAO_DE_CONCENTRACAO = 0.60
#: Abaixo deste número de janelas não há distribuição de que falar — o alerta
#: de concentração ficaria sempre aceso numa rodada curta, e alerta sempre
#: aceso é alerta que ninguém lê.
MINIMO_DE_JANELAS_PARA_CONCENTRACAO = 3

#: Cotação cancelada antes disto não repousou: foi colocada e retirada.
REPOUSO_MINIMO_S = 10.0
#: Fração dos cancelamentos sem repouso a partir da qual há churn.
FRACAO_DE_CHURN = 0.25

#: Folga para comparação de float. `5.0 * 0.95` não é `4.75` em binário, e um
#: alerta que dispara por 2e-16 acima do teto é ruído, não achado.
TOLERANCIA = 1e-9

#: O relatório tem teto de linhas de propósito: quem o lê é um cron às 6 da
#: manhã, e um relatório que não cabe na tela é um relatório não lido.
MAX_LINHAS_DO_RELATORIO = 10

#: O que vai na coluna de motivo quando o bot NÃO recusou. O portão grava
#: `motivo: null` nesse caso, e `null` na coluna de motivo lê-se como campo
#: ausente.
MOTIVO_LIBERADA = "liberada"

#: Rótulo da rota de cada decisão. São dois produtores no mesmo arquivo.
ROTA_PORTAO = "portao"
ROTA_MAKER = "maker"

SAIDA_LIMPA = 0
#: Há decisão fora da política, ou sinal de perseguição indesejada.
SAIDA_ALERTA = 1
#: O auditor não conseguiu apurar. Não é "limpo" nem "sujo": é desconhecido,
#: e desconhecido tem de custar o mesmo que ruim.
SAIDA_RECUSA = 2

#: As chaves que uma decisão do portão TEM. Exigi-las todas é o que faz o
#: auditor quebrar quando o produtor muda de forma, em vez de ler zero
#: decisões e imprimir um relatório normal — que é exatamente o que os dois
#: defeitos silenciosos deste projeto (`price_change` §6.1b, `market_resolved`
#: §12.13) fizeram.
CHAVES_DA_DECISAO = frozenset(
    {
        "ts_ns",
        "slug",
        "token_id",
        "lado_up",
        "shares",
        "preco_limite",
        "prob_prevista",
        "seconds_left",
        "melhor_bid",
        "melhor_ask",
        "profundidade_no_topo",
        "pode",
        "motivo",
        "detalhe",
        "latencia_da_decisao_ms",
    }
)

#: Idem para a cotação colocada pela rota maker em sombra.
CHAVES_DA_COLOCADA = frozenset(
    {
        "evento",
        "sombra",
        "order_id",
        "id_do_cliente",
        "janela",
        "slug",
        "token_id",
        "lado_up",
        "shares",
        "preco_limite",
        "ts",
    }
)

#: E para o cancelamento, que não repete preço nem tamanho.
CHAVES_DA_CANCELADA = frozenset(
    {
        "evento",
        "sombra",
        "order_id",
        "id_do_cliente",
        "janela",
        "slug",
        "segundos_repousada",
        "ts",
    }
)

EVENTO_COLOCADA = "cotacao_colocada"
EVENTO_CANCELADA = "cotacao_cancelada"


# ─────────────────────────────────────────────────────────── o que se lê


@dataclass(frozen=True)
class Decisao:
    """Uma decisão que comprometeu capital, de qualquer uma das duas rotas."""

    ts_s: float
    janela: str
    rota: str
    shares: float
    preco_limite: float
    motivo: str
    #: `None` nas cotações maker, que não carregam o topo do livro.
    melhor_bid: float | None = None
    melhor_ask: float | None = None
    latencia_ms: float | None = None

    @property
    def custo_usdc(self) -> float:
        """Capital em risco. Share de prediction market custa `preco` e paga 1.

        Mesma conta da `OrdemPretendida.custo_usdc` do portão, de propósito:
        é o número contra o qual `stake_max_por_trade_usdc` é escrito.
        """
        return self.shares * self.preco_limite

    @property
    def spread(self) -> float | None:
        if self.melhor_bid is None or self.melhor_ask is None:
            return None
        return self.melhor_ask - self.melhor_bid


@dataclass(frozen=True)
class LeituraDoDiario:
    """O diário inteiro, já na janela auditada."""

    #: Só as decisões que o bot LIBEROU — as que moveram dinheiro.
    liberadas: list[Decisao]
    #: Tudo, inclusive recusa, para a pergunta das 3 maiores.
    todas: list[Decisao]
    #: Quantas decisões do portão recusaram, por motivo do portão.
    recusas_do_portao: dict[str, int]
    #: Repouso de cada cotação encerrada, em segundos.
    repousos_s: list[float]
    #: O carimbo mais novo do arquivo — o "agora" do diário.
    fim_s: float


def _numero(registro: dict[str, Any], chave: str) -> float | None:
    valor = registro.get(chave)
    return float(valor) if isinstance(valor, int | float) else None


def _decisao_do_portao(registro: dict[str, Any], numero_da_linha: int) -> Decisao:
    faltando = CHAVES_DA_DECISAO - set(registro)
    if faltando:
        raise RecusaDoAuditor(
            MOTIVOS_DO_AUDITOR.RECUSA_LINHA_CORROMPIDA,
            {"linha": numero_da_linha, "faltando": sorted(faltando)},
        )
    shares = _numero(registro, "shares")
    preco = _numero(registro, "preco_limite")
    ts_ns = _numero(registro, "ts_ns")
    if shares is None or preco is None or ts_ns is None:
        raise RecusaDoAuditor(
            MOTIVOS_DO_AUDITOR.RECUSA_LINHA_CORROMPIDA,
            {"linha": numero_da_linha, "campo": "ts_ns/shares/preco_limite"},
        )
    return Decisao(
        ts_s=ts_ns / 1e9,
        janela=str(registro["slug"]),
        rota=ROTA_PORTAO,
        shares=shares,
        preco_limite=preco,
        motivo=str(registro["motivo"] or MOTIVO_LIBERADA),
        melhor_bid=_numero(registro, "melhor_bid"),
        melhor_ask=_numero(registro, "melhor_ask"),
        latencia_ms=_numero(registro, "latencia_da_decisao_ms"),
    )


def _cotacao_do_maker(registro: dict[str, Any], numero_da_linha: int) -> Decisao:
    faltando = CHAVES_DA_COLOCADA - set(registro)
    if faltando:
        raise RecusaDoAuditor(
            MOTIVOS_DO_AUDITOR.RECUSA_LINHA_CORROMPIDA,
            {"linha": numero_da_linha, "faltando": sorted(faltando)},
        )
    shares = _numero(registro, "shares")
    preco = _numero(registro, "preco_limite")
    ts = _numero(registro, "ts")
    if shares is None or preco is None or ts is None:
        raise RecusaDoAuditor(
            MOTIVOS_DO_AUDITOR.RECUSA_LINHA_CORROMPIDA,
            {"linha": numero_da_linha, "campo": "ts/shares/preco_limite"},
        )
    return Decisao(
        ts_s=ts,
        janela=str(registro["janela"]),
        rota=ROTA_MAKER,
        shares=shares,
        preco_limite=preco,
        motivo=EVENTO_COLOCADA,
    )


def _repouso_do_cancelamento(registro: dict[str, Any], numero_da_linha: int) -> float:
    faltando = CHAVES_DA_CANCELADA - set(registro)
    if faltando:
        raise RecusaDoAuditor(
            MOTIVOS_DO_AUDITOR.RECUSA_LINHA_CORROMPIDA,
            {"linha": numero_da_linha, "faltando": sorted(faltando)},
        )
    segundos = _numero(registro, "segundos_repousada")
    if segundos is None:
        raise RecusaDoAuditor(
            MOTIVOS_DO_AUDITOR.RECUSA_LINHA_CORROMPIDA,
            {"linha": numero_da_linha, "campo": "segundos_repousada"},
        )
    return segundos


def _registros_do_diario(destino: Path) -> list[tuple[int, dict[str, Any]]]:
    """As linhas do diário, numeradas, já como dicionário.

    Linha que não é JSON NÃO é pulada, ao contrário do que o leitor do resumo
    faz. A diferença é de propósito e vem do que cada um promete: o resumo
    tolera a linha do `journalctl` intercalada porque mede a rodada; o auditor
    responde "o dia inteiro cabia na política?", e uma linha ilegível é
    justamente a decisão que ele não viu.
    """
    try:
        texto = destino.read_text(encoding="utf-8")
    except OSError as erro:
        raise RecusaDoAuditor(
            MOTIVOS_DO_AUDITOR.RECUSA_DIARIO_ILEGIVEL,
            {"diario": str(destino), "erro": erro.strerror},
        ) from erro
    saida: list[tuple[int, dict[str, Any]]] = []
    for numero, linha in enumerate(texto.splitlines(), start=1):
        if not linha.strip():
            continue
        try:
            registro = json.loads(linha)
        except json.JSONDecodeError as erro:
            raise RecusaDoAuditor(
                MOTIVOS_DO_AUDITOR.RECUSA_LINHA_CORROMPIDA,
                {"linha": numero, "erro": erro.msg},
            ) from erro
        if not isinstance(registro, dict):
            raise RecusaDoAuditor(
                MOTIVOS_DO_AUDITOR.RECUSA_LINHA_CORROMPIDA,
                {"linha": numero, "erro": "linha não é objeto JSON"},
            )
        saida.append((numero, registro))
    return saida


def caminho_do_diario(bruto: str) -> Path:
    """O diário contido à raiz permitida, ou RECUSA com nome.

    A contenção é a de `pulsearb.caminhos` — a mesma do `#206` (S2083), e não
    uma segunda regra. O que muda aqui é só a forma de reclamar: o auditor
    roda no cron, e `SystemExit` com texto solto não vira linha de alarme.
    """
    try:
        return caminho_de_diario_lido(bruto)
    except ValueError as erro:
        motivo = (
            MOTIVOS_DO_AUDITOR.RECUSA_DIARIO_AUSENTE
            if "não existe" in str(erro)
            else MOTIVOS_DO_AUDITOR.RECUSA_DIARIO_ILEGIVEL
        )
        raise RecusaDoAuditor(motivo, {"diario": bruto, "erro": str(erro)}) from erro


def ler_diario(bruto: str, *, fim_s: float | None, horas: float) -> LeituraDoDiario:
    """As decisões das últimas `horas`, das DUAS formas de linha.

    `fim_s` ausente significa "o agora do diário": o carimbo mais novo do
    arquivo. É o default porque é o que acerta nos dois usos — no cron, o
    diário está sendo escrito agora e o carimbo mais novo é agora; auditando
    um diário guardado, o relógio de parede devolveria zero decisões e um
    relatório limpo sobre um dia que ninguém olhou.
    """
    destino = caminho_do_diario(bruto)
    todas: list[Decisao] = []
    repousos: list[tuple[float, float]] = []
    for numero, registro in _registros_do_diario(destino):
        evento = registro.get("evento")
        if evento == EVENTO_COLOCADA:
            todas.append(_cotacao_do_maker(registro, numero))
        elif evento == EVENTO_CANCELADA:
            ts = _numero(registro, "ts")
            if ts is None:
                raise RecusaDoAuditor(
                    MOTIVOS_DO_AUDITOR.RECUSA_LINHA_CORROMPIDA,
                    {"linha": numero, "campo": "ts"},
                )
            repousos.append((ts, _repouso_do_cancelamento(registro, numero)))
        elif evento is None:
            todas.append(_decisao_do_portao(registro, numero))
        else:
            raise RecusaDoAuditor(
                MOTIVOS_DO_AUDITOR.RECUSA_LINHA_CORROMPIDA,
                {"linha": numero, "evento_desconhecido": str(evento)},
            )

    carimbos = [d.ts_s for d in todas] + [ts for ts, _ in repousos]
    if not carimbos:
        raise RecusaDoAuditor(
            MOTIVOS_DO_AUDITOR.RECUSA_DIA_INDETERMINADO,
            {"diario": str(destino), "erro": "nenhum carimbo no diário"},
        )
    fim = max(carimbos) if fim_s is None else fim_s
    inicio = fim - horas * 3600.0

    na_janela = [d for d in todas if inicio < d.ts_s <= fim]
    if not na_janela:
        raise RecusaDoAuditor(
            MOTIVOS_DO_AUDITOR.RECUSA_DIA_INDETERMINADO,
            {"fim_s": fim, "horas": horas, "decisoes_na_janela": 0},
        )
    recusas: dict[str, int] = {}
    for decisao in na_janela:
        if decisao.rota == ROTA_PORTAO and decisao.motivo != MOTIVO_LIBERADA:
            recusas[decisao.motivo] = recusas.get(decisao.motivo, 0) + 1
    return LeituraDoDiario(
        liberadas=[
            d
            for d in na_janela
            if d.motivo in (MOTIVO_LIBERADA, EVENTO_COLOCADA)
        ],
        todas=na_janela,
        recusas_do_portao=recusas,
        repousos_s=[r for ts, r in repousos if inicio < ts <= fim],
        fim_s=fim,
    )


def ler_registro(bruto: str) -> RegistroDoDia:
    """O registro do dia, por `RegistroDoDia.de_dict`, ou RECUSA com nome.

    Ausente também RECUSA. Sem ele não se sabe se o disjuntor está armado nem
    qual é o PnL do dia — e um auditor que não sabe isso e assina "tudo bem"
    é o portão que não sabe e deixa passar.
    """
    try:
        destino = caminho_de_relatorio_lido(bruto)
    except ValueError as erro:
        motivo = (
            MOTIVOS_DO_AUDITOR.RECUSA_REGISTRO_AUSENTE
            if "não existe" in str(erro)
            else MOTIVOS_DO_AUDITOR.RECUSA_REGISTRO_ILEGIVEL
        )
        raise RecusaDoAuditor(motivo, {"registro": bruto, "erro": str(erro)}) from erro
    try:
        dado = json.loads(destino.read_text(encoding="utf-8"))
        return RegistroDoDia.de_dict(dado)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as erro:
        raise RecusaDoAuditor(
            MOTIVOS_DO_AUDITOR.RECUSA_REGISTRO_ILEGIVEL,
            {"registro": str(destino), "erro": f"{type(erro).__name__}: {erro}"},
        ) from erro


# ─────────────────────────────────────────────────────── as conferências


def _contar(alertas: dict[str, int], motivo: str, quantos: int = 1) -> None:
    """Soma um alerta, validando o nome dele na hora de somar."""
    if quantos > 0:
        alertas[validar_motivo(motivo)] = alertas.get(motivo, 0) + quantos


def conferir_politica(
    liberadas: list[Decisao], risk: RiskSettings
) -> dict[str, int]:
    """Quantas decisões LIBERADAS não cabiam na política escrita.

    A pergunta não é se o portão decidiu bem — é se o que passou cabia nos
    tetos que estavam no `config.yaml`. Um `pode: true` acima do teto é
    defeito do portão ou política trocada no meio, e nos dois casos quem lê
    precisa do número.
    """
    alertas: dict[str, int] = {}
    por_janela: dict[str, float] = {}
    for d in liberadas:
        if d.custo_usdc > risk.stake_max_por_trade_usdc + TOLERANCIA:
            _contar(alertas, MOTIVOS_DO_AUDITOR.STAKE_ACIMA_DO_TETO)
        if not (
            risk.preco_minimo - TOLERANCIA
            <= d.preco_limite
            <= risk.preco_maximo + TOLERANCIA
        ):
            _contar(alertas, MOTIVOS_DO_AUDITOR.PRECO_FORA_DA_FAIXA)
        spread = d.spread
        if spread is not None and spread > risk.spread_maximo + TOLERANCIA:
            _contar(alertas, MOTIVOS_DO_AUDITOR.SPREAD_ACIMA_DO_TETO)
        if d.latencia_ms is not None and d.latencia_ms > risk.atraso_max_ms:
            _contar(alertas, MOTIVOS_DO_AUDITOR.LATENCIA_ACIMA_DO_TETO)
        por_janela[d.janela] = por_janela.get(d.janela, 0.0) + d.custo_usdc
    estouradas = sum(
        1
        for gasto in por_janela.values()
        if gasto > risk.stake_max_por_janela_usdc + TOLERANCIA
    )
    _contar(alertas, MOTIVOS_DO_AUDITOR.JANELA_ACIMA_DO_TETO, estouradas)
    return alertas


def conferir_registro(
    registro: RegistroDoDia, risk: RiskSettings, *, fim_s: float
) -> dict[str, int]:
    """O estado durável do risco contra os tetos do dia.

    `fim_s` é o fim da janela auditada, e não `time.time()`: a pausa tem de
    ser julgada contra o instante do dia que se audita, senão auditar o dia
    de ontem diria que a pausa de ontem já passou.
    """
    alertas: dict[str, int] = {}
    if registro.exposicao_total_usdc > risk.exposicao_max_usdc + TOLERANCIA:
        _contar(alertas, MOTIVOS_DO_AUDITOR.EXPOSICAO_ACIMA_DO_TETO)
    if registro.posicoes_abertas > risk.posicoes_max_abertas:
        _contar(alertas, MOTIVOS_DO_AUDITOR.POSICOES_ACIMA_DO_TETO)
    if registro.pnl_realizado_usdc <= -risk.perda_max_diaria_usdc + TOLERANCIA:
        _contar(alertas, MOTIVOS_DO_AUDITOR.PERDA_ACIMA_DO_TETO)
    if registro.disjuntor_armado:
        _contar(alertas, MOTIVOS_DO_AUDITOR.DISJUNTOR_ARMADO)
    pausado = (
        registro.pausado_ate_epoch is not None and registro.pausado_ate_epoch > fim_s
    )
    if pausado or registro.perdas_seguidas >= risk.perdas_seguidas_para_pausa:
        _contar(alertas, MOTIVOS_DO_AUDITOR.PAUSA_EM_CURSO)
    return alertas


@dataclass(frozen=True)
class Estrategia:
    """O que o diário revela sobre o tamanho das ordens, por rota."""

    tamanhos_por_rota: dict[str, list[float]] = field(default_factory=dict)

    @property
    def mudou(self) -> bool:
        return any(len(t) > 1 for t in self.tamanhos_por_rota.values())


def conferir_estrategia(
    liberadas: list[Decisao], risk: RiskSettings
) -> tuple[Estrategia, dict[str, int]]:
    """Mudou de estratégia no meio do dia, e a mudança era permitida?

    O que o diário mede de estratégia é o TAMANHO da ordem — é o único
    parâmetro de decisão que cada linha carrega em todas as formas. Mais de
    um tamanho numa rota é mudança de regime no meio da janela; ela era
    permitida se TODO tamanho usado manteve o custo da ordem dentro de
    `stake_max_por_trade_usdc`.

    O que isto NÃO mede: troca de regra experimental (4.0 e/f/g), que vive no
    relato de 60 s e é julgada por `regras_da_rodada` do resumo da rodada. O
    diário não a carrega, e inferi-la daqui seria inventar.
    """
    por_rota: dict[str, list[float]] = {}
    for d in liberadas:
        tamanhos = por_rota.setdefault(d.rota, [])
        if d.shares not in tamanhos:
            tamanhos.append(d.shares)
    estrategia = Estrategia({rota: sorted(t) for rota, t in por_rota.items()})
    alertas: dict[str, int] = {}
    estourou = any(
        d.custo_usdc > risk.stake_max_por_trade_usdc + TOLERANCIA for d in liberadas
    )
    if estrategia.mudou and estourou:
        _contar(alertas, MOTIVOS_DO_AUDITOR.MUDANCA_DE_ESTRATEGIA_NAO_PERMITIDA)
    return estrategia, alertas


@dataclass(frozen=True)
class Perseguicao:
    """Os números por trás da pergunta 5, para o relatório poder citá-los."""

    raspagens: int
    fracao_da_maior_janela: float
    janelas: int
    churn: float


def conferir_perseguicao(
    leitura: LeituraDoDiario,
    risk: RiskSettings,
    *,
    repouso_minimo_s: float = REPOUSO_MINIMO_S,
) -> tuple[Perseguicao, dict[str, int]]:
    """O agente está perseguindo o objetivo de um jeito indesejado?

    Três sinais, e nenhum deles é "o bot perdeu dinheiro" — todos são
    "o bot mudou de comportamento para encostar no limite":

    - **raspando o teto**: custo a 95 % do teto por trade, repetidas vezes.
      Uma vez é o tamanho que deu; três vezes é mira no teto.
    - **concentrando numa janela**: um mercado só com a maior parte do dia.
      É a forma barata de aumentar a aposta sem estourar nenhum teto por
      trade.
    - **churn**: colocar e cancelar sem deixar repousar. Cotação que não
      repousa não é cotação: é ruído no livro, e é como um maker aparenta
      volume sem oferecer liquidez.
    """
    teto = risk.stake_max_por_trade_usdc
    raspagens = sum(
        1 for d in leitura.liberadas if d.custo_usdc >= teto * FRACAO_DE_RASPAGEM
    )
    por_janela: dict[str, float] = {}
    for d in leitura.liberadas:
        por_janela[d.janela] = por_janela.get(d.janela, 0.0) + d.custo_usdc
    movido = sum(por_janela.values())
    fracao = max(por_janela.values()) / movido if movido > 0 else 0.0
    sem_repouso = sum(1 for r in leitura.repousos_s if r < repouso_minimo_s)
    churn = sem_repouso / len(leitura.repousos_s) if leitura.repousos_s else 0.0

    alertas: dict[str, int] = {}
    if raspagens >= RASPAGENS_PARA_ALERTA:
        _contar(alertas, MOTIVOS_DO_AUDITOR.RASPANDO_O_TETO, raspagens)
    if (
        len(por_janela) >= MINIMO_DE_JANELAS_PARA_CONCENTRACAO
        and fracao > FRACAO_DE_CONCENTRACAO
    ):
        _contar(alertas, MOTIVOS_DO_AUDITOR.CONCENTRACAO_NUMA_JANELA)
    if churn > FRACAO_DE_CHURN:
        _contar(alertas, MOTIVOS_DO_AUDITOR.CHURN_DE_COTACOES)
    return (
        Perseguicao(raspagens, round(fracao, 3), len(por_janela), round(churn, 3)),
        alertas,
    )


# ────────────────────────────────────────────────────────── o relatório


@dataclass(frozen=True)
class Relatorio:
    """O que o auditor apurou. Vira texto em `linhas_do_relatorio`."""

    dia: str
    leitura: LeituraDoDiario
    registro: RegistroDoDia
    risk: RiskSettings
    estrategia: Estrategia
    perseguicao: Perseguicao
    alertas: dict[str, int]
    ids_sem_prefixo_de_sombra: list[str]

    @property
    def movido_usdc(self) -> float:
        return sum(d.custo_usdc for d in self.leitura.liberadas)


def _dia_utc(epoch_s: float) -> str:
    return datetime.fromtimestamp(epoch_s, tz=UTC).strftime("%Y-%m-%d")


def auditar(
    *,
    diario: str,
    registro_bruto: str,
    settings: Settings,
    fim_s: float | None = None,
    horas: float = HORAS_DA_JANELA,
    repouso_minimo_s: float = REPOUSO_MINIMO_S,
) -> Relatorio:
    """Lê as três fontes e devolve o apurado. Levanta `RecusaDoAuditor`.

    Garante que todo motivo em `Relatorio.alertas` está em
    `MOTIVOS_DO_AUDITOR.ALERTAS` — `_contar` valida cada um ao somá-lo.
    """
    risk = settings.risk
    leitura = ler_diario(diario, fim_s=fim_s, horas=horas)
    registro = ler_registro(registro_bruto)
    dia = _dia_utc(leitura.fim_s)
    if registro.dia != dia:
        # Juntar os números de um dia com as decisões de outro produziria um
        # relatório coerente e errado — o pior formato possível.
        raise RecusaDoAuditor(
            MOTIVOS_DO_AUDITOR.RECUSA_DIA_INDETERMINADO,
            {"dia_do_diario": dia, "dia_do_registro": registro.dia},
        )

    alertas = conferir_politica(leitura.liberadas, risk)
    for motivo, quantos in conferir_registro(
        registro, risk, fim_s=leitura.fim_s
    ).items():
        _contar(alertas, motivo, quantos)
    estrategia, da_estrategia = conferir_estrategia(leitura.liberadas, risk)
    for motivo, quantos in da_estrategia.items():
        _contar(alertas, motivo, quantos)
    perseguicao, da_perseguicao = conferir_perseguicao(
        leitura, risk, repouso_minimo_s=repouso_minimo_s
    )
    for motivo, quantos in da_perseguicao.items():
        _contar(alertas, motivo, quantos)

    # A rota maker é lida pela MESMA função do resumo da rodada — ver o
    # cabeçalho deste arquivo sobre "mesmo caminho".
    do_resumo = conferir_diario(diario)
    sem_prefixo = list(do_resumo["ids_sem_prefixo_de_sombra"])
    _contar(alertas, MOTIVOS_DO_AUDITOR.ORDEM_REAL_NO_DIARIO, len(sem_prefixo))

    return Relatorio(
        dia=dia,
        leitura=leitura,
        registro=registro,
        risk=risk,
        estrategia=estrategia,
        perseguicao=perseguicao,
        alertas=alertas,
        ids_sem_prefixo_de_sombra=sem_prefixo,
    )


def _linha_das_maiores(relatorio: Relatorio) -> str:
    maiores = sorted(relatorio.leitura.todas, key=lambda d: -d.custo_usdc)[:3]
    partes = [
        f"#{i} {d.janela[:28]} {d.custo_usdc:.2f} {d.rota}/{d.motivo}"
        for i, d in enumerate(maiores, start=1)
    ]
    return "maiores " + " | ".join(partes)


def _linha_dos_alertas(relatorio: Relatorio) -> str:
    if not relatorio.alertas:
        return "fora_da_politica nenhuma"
    nomes = " ".join(
        f"{motivo}={quantos}" for motivo, quantos in sorted(relatorio.alertas.items())
    )
    return f"fora_da_politica {nomes}"


def linhas_do_relatorio(relatorio: Relatorio) -> list[str]:
    """O relatório, em no máximo `MAX_LINHAS_DO_RELATORIO` linhas.

    A primeira começa com `ALERTA` sempre que há alerta, e o teto de linhas é
    garantido aqui — não é promessa do formato.
    """
    risk = relatorio.risk
    leitura = relatorio.leitura
    registro = relatorio.registro
    p = relatorio.perseguicao
    cabeca = "ALERTA" if relatorio.alertas else "LIMPO"
    tamanhos = " ".join(
        f"{rota}={[round(t, 4) for t in valores]}"
        for rota, valores in sorted(relatorio.estrategia.tamanhos_por_rota.items())
    )
    linhas = [
        f"{cabeca} auditor_diario dia={relatorio.dia} janela_h="
        f"{HORAS_DA_JANELA:.0f} decisoes={len(leitura.todas)} "
        f"alertas={sum(relatorio.alertas.values())}",
        f"movido {relatorio.movido_usdc:.2f} USDC em "
        f"{len(leitura.liberadas)} decisoes liberadas; "
        f"teto_exposicao={risk.exposicao_max_usdc:.2f} "
        f"teto_por_trade={risk.stake_max_por_trade_usdc:.2f} "
        f"teto_por_janela={risk.stake_max_por_janela_usdc:.2f}",
        f"registro gasto={registro.exposicao_total_usdc:.2f}/"
        f"{risk.exposicao_max_usdc:.2f} posicoes={registro.posicoes_abertas}/"
        f"{risk.posicoes_max_abertas} pnl={registro.pnl_realizado_usdc:+.2f}/"
        f"-{risk.perda_max_diaria_usdc:.2f} disjuntor="
        f"{'ARMADO' if registro.disjuntor_armado else 'nao'} "
        f"perdas_seguidas={registro.perdas_seguidas}/"
        f"{risk.perdas_seguidas_para_pausa}",
        _linha_das_maiores(relatorio),
        _linha_dos_alertas(relatorio),
        f"recusas_do_portao {leitura.recusas_do_portao or 'nenhuma'}",
        f"estrategia tamanhos {tamanhos} mudou="
        f"{'sim' if relatorio.estrategia.mudou else 'nao'} permitida="
        f"{'nao' if MOTIVOS_DO_AUDITOR.MUDANCA_DE_ESTRATEGIA_NAO_PERMITIDA in relatorio.alertas else 'sim'}",
        f"perseguicao raspagens={p.raspagens}/{RASPAGENS_PARA_ALERTA} "
        f"maior_janela={p.fracao_da_maior_janela:.2f}/{FRACAO_DE_CONCENTRACAO:.2f} "
        f"janelas={p.janelas} churn={p.churn:.2f}/{FRACAO_DE_CHURN:.2f}",
        f"ids_sem_prefixo_de_sombra {len(relatorio.ids_sem_prefixo_de_sombra)}",
    ]
    return linhas[:MAX_LINHAS_DO_RELATORIO]


def linhas_da_recusa(recusa: RecusaDoAuditor) -> list[str]:
    """A recusa também começa com `ALERTA`: não apurar é motivo de olhar.

    Um auditor que não conseguiu ler o dia e imprime algo que não parece
    alarme é o portão que "não sabe" e deixa passar.
    """
    return [
        f"ALERTA auditor_diario RECUSA {recusa.motivo}",
        f"detalhe {json.dumps(recusa.detalhe, ensure_ascii=False, sort_keys=True)}",
    ][:MAX_LINHAS_DO_RELATORIO]


def registro_do_modo(settings: Settings) -> str:
    """O registro do dia DO MODO que rodou — não o do dinheiro real.

    `caminho_do_registro_do_modo` é a mesma função que o portão usa para
    escolher onde gravar (achado do `#52`): SHADOW grava em
    `registro_do_dia.shadow.json`, e um auditor que lesse o arquivo do LIVE
    auditaria um dia que ninguém operou, com zero em tudo e nenhum alerta.
    """
    caminho = caminho_do_registro_do_modo(
        Path(settings.risk.caminho_do_registro), settings.mode
    )
    assert caminho is not None, "risk.caminho_do_registro não é opcional"
    return str(caminho)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("diario", help="data/shadow/*.jsonl ou data/diarios/*.jsonl")
    parser.add_argument(
        "--registro",
        default=None,
        help="o registro do dia; o default sai do config.yaml (risk.caminho_do_registro)",
    )
    parser.add_argument(
        "--horas", type=float, default=HORAS_DA_JANELA, help="tamanho da janela"
    )
    parser.add_argument(
        "--fim-epoch",
        type=float,
        default=None,
        help="fim da janela em epoch; o default é o carimbo mais novo do diário",
    )
    parser.add_argument(
        "--repouso-minimo-s",
        type=float,
        default=REPOUSO_MINIMO_S,
        help="abaixo disto a cotação não repousou; alimenta o alerta de churn",
    )
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args(argv)

    try:
        settings = Settings.load(args.config)
    # Amplo de propósito: `config.yaml` ilegível, YAML inválido ou teto com
    # tipo errado têm de sair como RECUSA NOMEADA, não como traceback — o cron
    # não lê traceback, e um auditor que morre é um auditor que não auditou.
    except Exception as erro:
        return _imprimir(
            linhas_da_recusa(
                RecusaDoAuditor(
                    MOTIVOS_DO_AUDITOR.RECUSA_POLITICA_ILEGIVEL,
                    {"config": args.config, "erro": f"{type(erro).__name__}: {erro}"},
                )
            ),
            SAIDA_RECUSA,
        )

    registro_bruto = args.registro or registro_do_modo(settings)
    try:
        relatorio = auditar(
            diario=args.diario,
            registro_bruto=registro_bruto,
            settings=settings,
            fim_s=args.fim_epoch,
            horas=args.horas,
            repouso_minimo_s=args.repouso_minimo_s,
        )
    except RecusaDoAuditor as recusa:
        return _imprimir(linhas_da_recusa(recusa), SAIDA_RECUSA)

    saida = SAIDA_ALERTA if relatorio.alertas else SAIDA_LIMPA
    return _imprimir(linhas_do_relatorio(relatorio), saida)


def _imprimir(linhas: list[str], codigo: int) -> int:
    assert len(linhas) <= MAX_LINHAS_DO_RELATORIO
    for linha in linhas:
        print(linha)
    return codigo


if __name__ == "__main__":
    raise SystemExit(main())
