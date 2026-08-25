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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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

    TODOS = frozenset(
        {
            MODO_NAO_OPERA,
            DISJUNTOR_ARMADO,
            STAKE_ACIMA_DO_TETO,
            JANELA_NO_TETO,
            EXPOSICAO_NO_TETO,
            POSICOES_NO_TETO,
            FEED_PARADO,
            PRECO_FORA_DA_FAIXA,
            ORDEM_MAL_FORMADA,
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
        )


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
        hoje: str | None = None,
    ) -> None:
        self.settings = settings
        self.modo = modo
        self.caminho = caminho_do_registro
        self._hoje = hoje or _hoje_utc()
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
            return RegistroDoDia(
                dia=self._hoje,
                disjuntor_armado=registro.disjuntor_armado,
                disjuntor_motivo=registro.disjuntor_motivo,
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

    # ───────────────────────────────────────────────────────────────── portões
    def avaliar(
        self,
        ordem: OrdemPretendida,
        *,
        feeds_saudaveis: bool,
    ) -> Decisao:
        """Esta ordem pode? Começa em não e só vira sim com todos os portões."""
        if ordem.shares <= 0 or not (0.0 < ordem.preco_limite < 1.0):
            return Decisao(
                False,
                MOTIVOS.ORDEM_MAL_FORMADA,
                {"shares": ordem.shares, "preco_limite": ordem.preco_limite},
            )

        if self.modo is not Mode.LIVE:
            return Decisao(False, MOTIVOS.MODO_NAO_OPERA, {"modo": self.modo.value})

        if self.registro.disjuntor_armado:
            return Decisao(
                False,
                MOTIVOS.DISJUNTOR_ARMADO,
                {"motivo_do_disjuntor": self.registro.disjuntor_motivo},
            )

        if not feeds_saudaveis:
            return Decisao(False, MOTIVOS.FEED_PARADO, {})

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

        return Decisao(True)

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
        """A janela fechou: libera a exposição e acumula o PnL do dia."""
        self.registro.gasto_por_janela.pop(slug, None)
        self.registro.pnl_realizado_usdc += pnl_usdc
        if self.registro.pnl_realizado_usdc <= -abs(
            self.settings.perda_max_diaria_usdc
        ):
            self.armar_disjuntor(
                f"perda do dia em {self.registro.pnl_realizado_usdc:.2f} USDC, "
                f"teto {self.settings.perda_max_diaria_usdc:.2f}"
            )
        else:
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
