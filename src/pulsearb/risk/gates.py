"""Os portões que decidem se uma ordem pode ser enviada.

TRÊS REGRAS DE PROJETO, e todas as três existem porque a falha delas custa
dinheiro real:

**1. Falha fechada.** `avaliar()` começa negando e só libera se TODOS os
portões passarem. Estado desconhecido — feed sem carimbo, janela sem preço,
registro do dia ilegível — é motivo de recusa, não de seguir em frente. Um
portão que "não sabe" e deixa passar não é portão.

**2. O disjuntor gruda.** Quando a perda do dia estoura, ele NÃO volta
sozinho porque o número melhorou depois. Fica armado até alguém desarmar à
mão, e sobrevive a reinício porque é gravado em disco. A armadilha que isto
cobre é concreta: bot perde, processo cai, systemd reinicia, contador zera,
bot perde de novo. Sem persistência o disjuntor vira um limite por vida de
processo, que não é limite nenhum.

**3. Cada recusa se nomeia.** `Decisao.motivo` é uma constante de `MOTIVOS`,
não uma frase livre. Recusa sem nome não vira métrica, não vira alarme, e
não dá para distinguir "o bot está travado" de "o bot não achou trade".

O que este módulo NÃO faz: não envia ordem, não fala com a rede, não decide
tamanho. Ele responde uma pergunta só — *esta ordem pode?* — e a resposta é
auditável.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from pulsearb.settings import Mode, RiskSettings

# ─────────────────────────────────────────────────────────── motivos de recusa
#
# São constantes porque viram rótulo de métrica. Frase livre não agrega.


class MOTIVOS:
    """Todo `nao` tem um destes. Nenhum outro texto é aceito como motivo."""

    MODO_NAO_OPERA = "modo_nao_opera"
    DISJUNTOR_ARMADO = "disjuntor_armado"
    STAKE_ACIMA_DO_TETO = "stake_acima_do_teto"
    JANELA_NO_TETO = "janela_no_teto"
    EXPOSICAO_NO_TETO = "exposicao_no_teto"
    POSICOES_NO_TETO = "posicoes_no_teto"
    FEED_PARADO = "feed_parado"
    PRECO_FORA_DA_FAIXA = "preco_fora_da_faixa"
    ORDEM_MAL_FORMADA = "ordem_mal_formada"
    #: A chave de emergencia foi puxada por uma pessoa.
    KILL_ACIONADO = "kill_acionado"
    #: Sequencia de perdas em curso; o bot esta de molho ate a hora marcada.
    PAUSA_POR_SEQUENCIA = "pausa_por_sequencia"
    #: Atravessar o livro custaria mais que o edge exigido.
    SPREAD_ANOMALO = "spread_anomalo"
    #: Sem topo de livro nao da para saber o que a ordem custaria.
    LIVRO_DESCONHECIDO = "livro_desconhecido"
    #: O nosso agora discorda do agora do servidor por mais que o teto.
    #: ATENCAO ao que isto NAO significa: o sensor mede latencia MAIS offset
    #: de relogio, e as duas se cancelam. Este motivo prova que algo esta
    #: grande; a AUSENCIA dele nao prova relogio bom — ver `live/relogio.py`.
    RELOGIO_DERIVADO = "relogio_derivado"
    #: Modo LIVE sem fonte de deriva instalada, ou com a fonte muda. Nao e o
    #: mesmo que RELOGIO_DERIVADO: la sabemos que derivou, aqui nao sabemos
    #: nada — e nao saber tem de custar o mesmo que saber que esta ruim.
    RELOGIO_NAO_MONITORADO = "relogio_nao_monitorado"

    TODOS = frozenset(
        {
            MODO_NAO_OPERA,
            KILL_ACIONADO,
            PAUSA_POR_SEQUENCIA,
            SPREAD_ANOMALO,
            LIVRO_DESCONHECIDO,
            DISJUNTOR_ARMADO,
            STAKE_ACIMA_DO_TETO,
            JANELA_NO_TETO,
            EXPOSICAO_NO_TETO,
            POSICOES_NO_TETO,
            FEED_PARADO,
            PRECO_FORA_DA_FAIXA,
            ORDEM_MAL_FORMADA,
            RELOGIO_DERIVADO,
            RELOGIO_NAO_MONITORADO,
        }
    )


@dataclass(frozen=True)
class OrdemPretendida:
    """O que se quer enviar. Ainda não é ordem — é um pedido de licença."""

    slug: str
    token_id: str
    lado_up: bool
    shares: float
    preco_limite: float

    @property
    def custo_usdc(self) -> float:
        """Capital em risco. Share de prediction market custa `preco` e paga 1."""
        return self.shares * self.preco_limite


@dataclass(frozen=True)
class Decisao:
    """Sim ou não, com o motivo nomeado e os números que o justificam."""

    pode: bool
    motivo: str | None = None
    detalhe: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.pode and self.motivo is not None:
            raise ValueError("decisão positiva não carrega motivo de recusa")
        if not self.pode and self.motivo not in MOTIVOS.TODOS:
            raise ValueError(
                f"motivo de recusa desconhecido: {self.motivo!r}. "
                "Toda recusa precisa de um nome em MOTIVOS — recusa anônima "
                "não vira métrica nem alarme."
            )


@dataclass
class RegistroDoDia:
    """Quanto já se arriscou hoje, e se o disjuntor está armado.

    Persistido em disco porque o disjuntor precisa sobreviver a reinício.
    O arquivo é pequeno e reescrito inteiro a cada mudança: não vale a pena
    otimizar algo que muda algumas vezes por hora e cuja perda custa caro.
    """

    dia: str
    gasto_por_janela: dict[str, float] = field(default_factory=dict)
    pnl_realizado_usdc: float = 0.0
    disjuntor_armado: bool = False
    disjuntor_motivo: str | None = None
    #: Perdas seguidas ATE AGORA. Zera na primeira janela vencedora.
    perdas_seguidas: int = 0
    #: Ate quando o bot esta de molho, em epoch. `None` = nao esta.
    pausado_ate_epoch: float | None = None

    @property
    def exposicao_total_usdc(self) -> float:
        return sum(self.gasto_por_janela.values())

    @property
    def posicoes_abertas(self) -> int:
        return sum(1 for valor in self.gasto_por_janela.values() if valor > 0)

    def como_dict(self) -> dict[str, Any]:
        return {
            "dia": self.dia,
            "gasto_por_janela": dict(self.gasto_por_janela),
            "pnl_realizado_usdc": self.pnl_realizado_usdc,
            "disjuntor_armado": self.disjuntor_armado,
            "disjuntor_motivo": self.disjuntor_motivo,
            "perdas_seguidas": self.perdas_seguidas,
            "pausado_ate_epoch": self.pausado_ate_epoch,
        }

    @classmethod
    def de_dict(cls, dado: dict[str, Any]) -> RegistroDoDia:
        return cls(
            dia=str(dado["dia"]),
            gasto_por_janela={
                str(k): float(v)
                for k, v in dict(dado.get("gasto_por_janela") or {}).items()
            },
            pnl_realizado_usdc=float(dado.get("pnl_realizado_usdc") or 0.0),
            disjuntor_armado=bool(dado.get("disjuntor_armado")),
            disjuntor_motivo=dado.get("disjuntor_motivo") or None,
            perdas_seguidas=int(dado.get("perdas_seguidas") or 0),
            pausado_ate_epoch=(
                float(dado["pausado_ate_epoch"])
                if dado.get("pausado_ate_epoch") is not None
                else None
            ),
        )


class FonteDeAtraso(Protocol):
    """O contrato mínimo que o portão do relógio precisa (item 3.10).

    Protocolo, e não import direto de `live.relogio`: o pacote de risco não
    depende do de captação, e o teste passa um dublê de três linhas.
    """

    def atraso_ms(self, *, agora_ms: int) -> float | None:
        """Mediana do atraso servidor→chegada, ou `None` para "não sei"."""
        ...


def _hoje_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


class PortaoDeRisco:
    """Consulte antes de CADA ordem. Não há caminho legítimo que o contorne."""

    def __init__(
        self,
        settings: RiskSettings,
        modo: Mode,
        *,
        caminho_do_registro: Path | None = None,
        caminho_do_kill: Path | None = None,
        hoje: str | None = None,
        relogio: Callable[[], float] = time.time,
        relogio_do_servidor: FonteDeAtraso | None = None,
    ) -> None:
        self.settings = settings
        self.modo = modo
        self.caminho = caminho_do_registro
        self.caminho_do_kill = caminho_do_kill
        self._hoje = hoje or _hoje_utc()
        self._relogio = relogio
        #: A fonte de atraso do item 3.10. `None` = trava NÃO instalada, e em
        #: LIVE isso é recusa: ver `_portao_do_relogio`.
        self.relogio_do_servidor = relogio_do_servidor
        self.registro = self._carregar()

    # ───────────────────────────────────────────────────────────── persistência
    def _carregar(self) -> RegistroDoDia:
        """Lê o registro do dia. Arquivo ilegível ARMA o disjuntor.

        Não dá para distinguir "arquivo corrompido" de "arquivo com o
        disjuntor armado que não consigo ler". Entre supor que estava tudo
        bem e supor que estava tudo mal, a segunda é a que não perde
        dinheiro por engano.
        """
        if self.caminho is None or not self.caminho.exists():
            return RegistroDoDia(dia=self._hoje)
        try:
            dado = json.loads(self.caminho.read_text(encoding="utf-8"))
            registro = RegistroDoDia.de_dict(dado)
        except (OSError, ValueError, KeyError, TypeError) as erro:
            registro = RegistroDoDia(dia=self._hoje)
            registro.disjuntor_armado = True
            registro.disjuntor_motivo = f"registro do dia ilegivel: {erro}"
            return registro

        if registro.dia != self._hoje:
            # Dia virou: gasto e PnL zeram, mas o DISJUNTOR não. Se ele
            # estava armado, quem desarma é uma pessoa — a virada de data
            # não é revisão de nada.
            # A pausa e a sequencia atravessam a meia-noite junto com o
            # disjuntor, e pela mesma razao: o mercado nao sabe que o dia
            # virou. Uma pausa de 1h iniciada 23:40 que evaporasse a
            # 00:00 seria uma pausa de 20 minutos.
            return RegistroDoDia(
                dia=self._hoje,
                disjuntor_armado=registro.disjuntor_armado,
                disjuntor_motivo=registro.disjuntor_motivo,
                perdas_seguidas=registro.perdas_seguidas,
                pausado_ate_epoch=registro.pausado_ate_epoch,
            )
        return registro

    def _gravar(self) -> None:
        if self.caminho is None:
            return
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        temporario = self.caminho.with_suffix(self.caminho.suffix + ".tmp")
        temporario.write_text(
            json.dumps(self.registro.como_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # Rename atômico: um corte de energia no meio da escrita deixaria o
        # registro pela metade, e registro pela metade arma o disjuntor.
        temporario.replace(self.caminho)

    def _kill_acionado(self) -> bool:
        """Lido a CADA avaliação, nunca cacheado no construtor.

        A chave existe para ser puxada com o bot rodando. Ler uma vez na
        subida daria uma chave que só funciona antes de o bot precisar dela.

        Erro de leitura conta como acionada: entre supor que ninguém puxou a
        chave e supor que alguém puxou e o disco não deixa conferir, a
        segunda é a que não perde dinheiro por engano — a mesma regra do
        registro do dia ilegível.
        """
        if self.caminho_do_kill is None:
            return False
        try:
            return self.caminho_do_kill.exists()
        except OSError:
            return True

    def _portao_do_spread(
        self, melhor_bid: float | None, melhor_ask: float | None
    ) -> Decisao | None:
        """Atravessar o livro pode custar mais que o edge que se exige dele.

        O critério 1.1 do VEREDITO_M2 pede edge ≥ 0,02, e quem toma paga
        meio spread contra o meio do livro. Com spread de 0,04 o custo de
        atravessar iguala o edge exigido: o trade não pode ganhar, e não é
        questão de sorte.

        Livro ausente é recusa SEPARADA de livro largo. "Não sei o que isto
        custaria" e "sei, e é caro demais" são estados diferentes, e um
        SHADOW que os misture não diz se falta instrumentação ou liquidez.
        """
        if melhor_bid is None or melhor_ask is None:
            return Decisao(
                False,
                MOTIVOS.LIVRO_DESCONHECIDO,
                {"melhor_bid": melhor_bid, "melhor_ask": melhor_ask},
            )
        # ARREDONDAR ANTES DE COMPARAR, e não é preciosismo: `0.52 - 0.48`
        # dá 0,040000000000000036 em float64, então um spread de exatamente
        # um teto de 0,04 seria RECUSADO — e recusado só em alguns níveis de
        # preço, porque o ruído depende dos operandos. Um portão que decide
        # diferente em 0,52/0,48 e em 0,51/0,47 não tem contrato nenhum.
        # Os preços chegam em tick de 0,01; seis casas são folga de sobra.
        spread = round(melhor_ask - melhor_bid, 6)
        if spread > self.settings.spread_maximo:
            return Decisao(
                False,
                MOTIVOS.SPREAD_ANOMALO,
                {"spread": spread, "teto": self.settings.spread_maximo},
            )
        return None

    def _portao_do_relogio(self) -> Decisao | None:
        """O nosso agora ainda concorda com o do servidor? (item 3.10)

        A decisão inteira se apoia em `seconds_left`, que é a distância entre
        o NOSSO relógio e o fechamento da janela. Se os dois relógios
        divergem, o modelo opera com um horizonte que não existe.

        **O que este portão NÃO garante.** A fonte mede latência MAIS offset
        de relógio numa subtração só, e as duas parcelas se cancelam: relógio
        400 ms atrasado com 400 ms de latência mede ZERO e passa aqui, com o
        `seconds_left` errado em 400 ms. Passar neste portão é ausência de
        alarme deste sensor, não certificado de relógio — a garantia de
        sincronia é pré-condição de deploy (NTP/chrony verificado), e está
        registrada como tal em `live/relogio.py` e no ESTADO_PARA_LIVE.

        **Sem fonte instalada, em LIVE, é recusa.** É a decisão menos
        confortável deste arquivo, e é deliberada: uma trava que se
        auto-desativa quando ninguém a ligou não é trava, é decoração. Fora do
        LIVE a ausência não recusa — o SHADOW existe para ensaiar, e recusar
        tudo ali apagaria a informação que justifica o ensaio (a mesma razão
        pela qual o portão de modo não roda em `avaliar_risco`).
        """
        fonte = self.relogio_do_servidor
        if fonte is None:
            if self.modo is Mode.LIVE:
                return Decisao(False, MOTIVOS.RELOGIO_NAO_MONITORADO, {"fonte": None})
            return None
        atraso = fonte.atraso_ms(agora_ms=int(self._relogio() * 1000))
        if atraso is None:
            # Fonte instalada e muda: ou nunca chegou tick, ou o último é
            # velho demais. Não saber custa o mesmo que saber que está ruim.
            return Decisao(
                False, MOTIVOS.RELOGIO_NAO_MONITORADO, {"atraso_ms": None}
            )
        if abs(atraso) > self.settings.atraso_max_ms:
            # ABS, e não só o positivo: carimbo do servidor no futuro do nosso
            # relógio significa relógio LOCAL atrasado, que estraga o
            # `seconds_left` na direção oposta e igualmente cara.
            return Decisao(
                False,
                MOTIVOS.RELOGIO_DERIVADO,
                {"atraso_ms": round(atraso, 1), "teto_ms": self.settings.atraso_max_ms},
            )
        return None

    def _pausa_em_curso(self) -> float | None:
        """Segundos restantes da pausa, ou `None` se não há pausa."""
        ate = self.registro.pausado_ate_epoch
        if ate is None:
            return None
        restante = ate - self._relogio()
        return restante if restante > 0 else None

    # ───────────────────────────────────────────────────────────────── portões
    def avaliar(
        self,
        ordem: OrdemPretendida,
        *,
        feeds_saudaveis: bool,
        melhor_bid: float | None,
        melhor_ask: float | None,
    ) -> Decisao:
        """Esta ordem pode SER ENVIADA? Portão de modo mais todos os de risco.

        É esta que o caminho de envio real chama. O portão de modo vem
        primeiro de propósito: em SIM e SHADOW a resposta é não antes de
        qualquer outra consideração.
        """
        if self.modo is not Mode.LIVE:
            # A ordem mal formada é checada antes até aqui: um pedido inválido
            # é defeito de quem chamou, e o motivo certo é esse, não o modo.
            if ordem.shares <= 0 or not (0.0 < ordem.preco_limite < 1.0):
                return Decisao(
                    False,
                    MOTIVOS.ORDEM_MAL_FORMADA,
                    {"shares": ordem.shares, "preco_limite": ordem.preco_limite},
                )
            return Decisao(False, MOTIVOS.MODO_NAO_OPERA, {"modo": self.modo.value})
        return self.avaliar_risco(
            ordem,
            feeds_saudaveis=feeds_saudaveis,
            melhor_bid=melhor_bid,
            melhor_ask=melhor_ask,
        )

    def avaliar_risco(
        self,
        ordem: OrdemPretendida,
        *,
        feeds_saudaveis: bool,
        melhor_bid: float | None,
        melhor_ask: float | None,
    ) -> Decisao:
        """Os portões de RISCO, sem o portão de modo. Começa em não.

        Existe separado por causa do SHADOW. O portão de modo serve para
        impedir ENVIO, e no shadow não há envio para impedir — rodá-lo ali
        faria toda intenção sair como `modo_nao_opera` e o diário perderia
        exatamente a informação que justifica o ensaio: qual portão estaria
        segurando se o modo fosse LIVE.

        Quem envia ordem NÃO chama esta função: chama `avaliar()`.
        """
        if ordem.shares <= 0 or not (0.0 < ordem.preco_limite < 1.0):
            return Decisao(
                False,
                MOTIVOS.ORDEM_MAL_FORMADA,
                {"shares": ordem.shares, "preco_limite": ordem.preco_limite},
            )

        # ORDEM DOS GRUPOS, e ela é deliberada: primeiro o que impede operar
        # de todo (chave, disjuntor, pausa, feed, livro), depois o que impede
        # ESTA ordem (preço, stake, tetos). O diário fica mais útil quando o
        # motivo registrado é o mais geral que se aplica.
        #
        # `is not None` em vez de `or`: uma recusa é um objeto `Decisao` cujo
        # campo principal é `pode=False`. Encadear por veracidade funcionaria
        # hoje e inverteria TODOS os portões no dia em que alguém desse a
        # `Decisao` um `__bool__` que devolvesse `pode` — falha silenciosa, no
        # arquivo onde ela é menos aceitável.
        recusa = self._portoes_do_sistema(feeds_saudaveis, melhor_bid, melhor_ask)
        if recusa is not None:
            return recusa
        recusa = self._portoes_da_ordem(ordem)
        if recusa is not None:
            return recusa
        return Decisao(True)

    def _portoes_do_sistema(
        self,
        feeds_saudaveis: bool,
        melhor_bid: float | None,
        melhor_ask: float | None,
    ) -> Decisao | None:
        """O que impede operar de todo, independente da ordem pedida."""
        # A chave de emergência vem antes de tudo que é automático: se uma
        # pessoa puxou, a razão dela vale mais que qualquer conta nossa.
        if self._kill_acionado():
            return Decisao(
                False, MOTIVOS.KILL_ACIONADO, {"arquivo": str(self.caminho_do_kill)}
            )

        if self.registro.disjuntor_armado:
            return Decisao(
                False,
                MOTIVOS.DISJUNTOR_ARMADO,
                {"motivo_do_disjuntor": self.registro.disjuntor_motivo},
            )

        restante = self._pausa_em_curso()
        if restante is not None:
            return Decisao(
                False,
                MOTIVOS.PAUSA_POR_SEQUENCIA,
                {
                    "segundos_restantes": round(restante, 1),
                    "perdas_que_dispararam": self.settings.perdas_seguidas_para_pausa,
                },
            )

        if not feeds_saudaveis:
            return Decisao(False, MOTIVOS.FEED_PARADO, {})

        recusa = self._portao_do_relogio()
        if recusa is not None:
            return recusa

        return self._portao_do_spread(melhor_bid, melhor_ask)

    def _portoes_da_ordem(self, ordem: OrdemPretendida) -> Decisao | None:
        """O que impede ESTA ordem: preço, tamanho e os três tetos."""
        if not (
            self.settings.preco_minimo <= ordem.preco_limite <= self.settings.preco_maximo
        ):
            return Decisao(
                False,
                MOTIVOS.PRECO_FORA_DA_FAIXA,
                {
                    "preco": ordem.preco_limite,
                    "minimo": self.settings.preco_minimo,
                    "maximo": self.settings.preco_maximo,
                },
            )

        custo = ordem.custo_usdc
        if custo > self.settings.stake_max_por_trade_usdc:
            return Decisao(
                False,
                MOTIVOS.STAKE_ACIMA_DO_TETO,
                {"custo": custo, "teto": self.settings.stake_max_por_trade_usdc},
            )

        ja_na_janela = self.registro.gasto_por_janela.get(ordem.slug, 0.0)
        if ja_na_janela + custo > self.settings.stake_max_por_janela_usdc:
            return Decisao(
                False,
                MOTIVOS.JANELA_NO_TETO,
                {
                    "slug": ordem.slug,
                    "ja_gasto": ja_na_janela,
                    "pedido": custo,
                    "teto": self.settings.stake_max_por_janela_usdc,
                },
            )

        total = self.registro.exposicao_total_usdc
        if total + custo > self.settings.exposicao_max_usdc:
            return Decisao(
                False,
                MOTIVOS.EXPOSICAO_NO_TETO,
                {
                    "exposicao_atual": total,
                    "pedido": custo,
                    "teto": self.settings.exposicao_max_usdc,
                },
            )

        # Só conta como posição NOVA se a janela ainda não tem exposição.
        if (
            ja_na_janela <= 0
            and self.registro.posicoes_abertas >= self.settings.posicoes_max_abertas
        ):
            return Decisao(
                False,
                MOTIVOS.POSICOES_NO_TETO,
                {
                    "abertas": self.registro.posicoes_abertas,
                    "teto": self.settings.posicoes_max_abertas,
                },
            )

        return None

    # ──────────────────────────────────────────────────────────── contabilidade
    def registrar_envio(self, ordem: OrdemPretendida) -> None:
        """Chame DEPOIS de a ordem ser aceita pela corretora.

        Antes do envio o capital ainda não está em risco; depois da rejeição
        também não. Registrar no lugar errado infla a exposição e trava o bot
        sozinho — que é o modo de falhar seguro, mas ainda assim é falhar.
        """
        atual = self.registro.gasto_por_janela.get(ordem.slug, 0.0)
        self.registro.gasto_por_janela[ordem.slug] = atual + ordem.custo_usdc
        self._gravar()

    def registrar_resolucao(self, slug: str, pnl_usdc: float) -> None:
        """A janela fechou: libera a exposição, acumula o PnL, conta a sequência.

        Empate (`pnl == 0`) não quebra a sequência nem a alimenta. Zerar nele
        daria à taxa o poder de limpar o histórico de perdas: uma janela que
        acerta o lado e devolve o lucro inteiro em taxa fecha em zero, e não
        é evidência de que o modelo voltou a funcionar.
        """
        self.registro.gasto_por_janela.pop(slug, None)
        self.registro.pnl_realizado_usdc += pnl_usdc

        if pnl_usdc < 0:
            self.registro.perdas_seguidas += 1
        elif pnl_usdc > 0:
            self.registro.perdas_seguidas = 0

        if self.registro.pnl_realizado_usdc <= -abs(
            self.settings.perda_max_diaria_usdc
        ):
            # O disjuntor vence a pausa: ele gruda, ela expira. Armar os dois
            # e deixar a pausa por cima faria a pausa parecer o motivo, e
            # alguém esperaria uma hora por algo que exige decisão humana.
            self.armar_disjuntor(
                f"perda do dia em {self.registro.pnl_realizado_usdc:.2f} USDC, "
                f"teto {self.settings.perda_max_diaria_usdc:.2f}"
            )
            return

        if self.registro.perdas_seguidas >= self.settings.perdas_seguidas_para_pausa:
            self._pausar()
            return

        self._gravar()

    def _pausar(self) -> None:
        """Põe o bot de molho e ZERA a sequência.

        Zerar é deliberado: a pausa É a resposta àquela sequência. Mantê-la
        faria toda perda posterior repausar sem evidência nova, e quatro
        perdas seguidas viraria "uma perda por hora, para sempre".
        """
        self.registro.pausado_ate_epoch = (
            self._relogio() + self.settings.pausa_apos_sequencia_s
        )
        self.registro.perdas_seguidas = 0
        self._gravar()

    def retomar(self) -> None:
        """Encerra a pausa antes da hora. Só uma pessoa chama isto."""
        self.registro.pausado_ate_epoch = None
        self._gravar()

    def armar_disjuntor(self, motivo: str) -> None:
        """Trava tudo. NÃO desarma sozinho — nem no dia seguinte."""
        self.registro.disjuntor_armado = True
        self.registro.disjuntor_motivo = motivo
        self._gravar()

    def desarmar_disjuntor(self) -> None:
        """Só uma pessoa chama isto, e de caso pensado."""
        self.registro.disjuntor_armado = False
        self.registro.disjuntor_motivo = None
        self._gravar()
