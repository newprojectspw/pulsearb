"""O preço-verdade ao vivo: TWAP corrente, volatilidade, e a âncora de cada janela.

Três coisas que o backtest recebe prontas e que ao vivo precisam ser
construídas enquanto o bot roda.

**A âncora é a mais delicada, e a que mais pode enganar.** O M2 fechou a
questão: âncora é o valor do stream `crypto_prices_twap_sixty` **no instante
da abertura da janela** (τ=0), medido com 0,9984 de consistência sobre 640
janelas. Ao vivo isso tem uma consequência que o backtest não tinha —

**Janela cuja abertura o bot não presenciou NÃO TEM âncora.** Se o processo
subiu às 12:03 e a janela abriu às 12:00, o valor de 12:00 não está em lugar
nenhum: a série começa quando o bot começa. Usar a amostra mais antiga que se
tem seria inventar a âncora, e a janela inteira sairia errada sem ninguém
notar — que é exatamente o que `ancora_verificada` recusa a fazer devolvendo
`None`. Aqui vale igual, e a janela é pulada.

Isso implica um comportamento operacional que precisa ser esperado: **o bot
recém-iniciado não opera nada por até uma janela inteira.** Não é defeito, é a
âncora sendo honesta.
"""

from __future__ import annotations

import time
from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Any

from pulsearb.analysis.anchor_sweep import IDADE_MAX_MS, StreamE18
from pulsearb.engine.twap import RealizedVol, TwapTracker
from pulsearb.live.relogio import RelogioDoServidor
from pulsearb.obs.logging import get_logger

log = get_logger(__name__)

#: Quanto de série guardar, em segundos. A janela mais longa que o projeto
#: opera é de 4 h, e a âncora dela é lida na abertura — então a série precisa
#: alcançar 4 h para trás. A margem cobre atraso de descoberta.
HISTORICO_S = 5 * 3600

#: Motivos pelos quais uma janela fica sem âncora. Como os motivos do portão,
#: são constantes porque viram contador: "o bot não operou" e "o bot não tinha
#: âncora" são diagnósticos diferentes.
SEM_ANCORA_SERIE_CURTA = "serie_nao_alcanca_a_abertura"
SEM_ANCORA_LACUNA = "lacuna_no_instante_da_abertura"
SEM_ANCORA_SEM_ATIVO = "ativo_sem_serie"


@dataclass
class SerieE18AoVivo:
    """Série (ts_servidor_ms → valor e18) que cresce, e responde como a do M2.

    Compõe `StreamE18` em vez de reimplementar a busca: `em()` é a definição
    da âncora, verificada em 640 janelas, e uma segunda cópia dela seria a
    forma mais silenciosa possível de o SHADOW e o backtest discordarem.
    """

    historico_s: float = HISTORICO_S
    _serie: StreamE18 = field(default_factory=lambda: StreamE18([]))
    fora_de_ordem: int = 0

    def anotar(self, ts_ms: int, valor_e18: int) -> None:
        ts = self._serie.ts
        if ts and ts_ms < ts[-1]:
            # Chegada fora de ordem. O M2 mediu 2.129 deltas e 13.889
            # snapshots assim no CLOB; no twap do RTDS a série veio limpa,
            # mas assumir isso seria assumir sorte. Inserir na posição certa
            # mantém a busca binária válida, que é a premissa do `em()`.
            self.fora_de_ordem += 1
            posicao = bisect_right(ts, ts_ms)
            ts.insert(posicao, ts_ms)
            self._serie.valores.insert(posicao, valor_e18)
        else:
            ts.append(ts_ms)
            self._serie.valores.append(valor_e18)
        self._podar(ts_ms)

    def _podar(self, agora_ms: int) -> None:
        corte = agora_ms - int(self.historico_s * 1000)
        ts = self._serie.ts
        if not ts or ts[0] >= corte:
            return
        quantos = bisect_right(ts, corte)
        del ts[:quantos]
        del self._serie.valores[:quantos]

    def em(self, instante_ms: int, *, idade_max_ms: int = IDADE_MAX_MS) -> int | None:
        return self._serie.em(instante_ms, idade_max_ms=idade_max_ms)

    def alcanca(self, instante_ms: int) -> bool:
        """A série existia naquele instante?

        Diferente de `em()` devolver None: aqui a pergunta é se o bot já
        estava ouvindo, e a resposta separa "lacuna do feed" de "cheguei
        depois". Os dois impedem operar, mas só o primeiro é problema.
        """
        return bool(self._serie.ts) and self._serie.ts[0] <= instante_ms

    def __len__(self) -> int:
        return len(self._serie.ts)


@dataclass
class PrecosPorAtivo:
    """Tudo que o modelo precisa de um ativo, mantido ao vivo."""

    asset: str
    twap: TwapTracker = field(default_factory=TwapTracker)
    vol: RealizedVol = field(default_factory=RealizedVol)
    serie_e18: SerieE18AoVivo = field(default_factory=SerieE18AoVivo)

    def anotar(self, *, valor_e18: int, ts_servidor_ms: int) -> None:
        preco = valor_e18 / 1e18
        ts_ns = ts_servidor_ms * 1_000_000
        self.twap.update(preco, ts_ns)
        self.vol.update(preco, ts_ns)
        self.serie_e18.anotar(ts_servidor_ms, valor_e18)


@dataclass
class PrecosAoVivo:
    """O preço-verdade de todos os ativos, e a âncora de cada janela."""

    por_ativo: dict[str, PrecosPorAtivo] = field(default_factory=dict)
    #: A fonte de atraso do item 3.10, alimentada por todo tick que entra.
    #: Mora aqui porque aqui é onde os ticks passam — uma fonte pendurada em
    #: outro lugar teria de ser alimentada por alguém que lembre de fazê-lo.
    relogio: RelogioDoServidor = field(default_factory=RelogioDoServidor)
    #: Âncoras já resolvidas, por `condition_id`. Uma vez fixada, NÃO muda:
    #: a âncora é o valor da abertura, e a abertura acontece uma vez só.
    ancoras: dict[str, float] = field(default_factory=dict)
    sem_ancora: dict[str, int] = field(default_factory=dict)

    def anotar(
        self,
        asset: str,
        *,
        valor_e18: int,
        ts_servidor_ms: int,
        chegada_ms: int | None = None,
    ) -> None:
        """Um tick do feed-verdade.

        `chegada_ms` é o NOSSO relógio no momento em que o tick chegou, e
        alimenta a trava de relógio (item 3.10). Omitir não inventa um valor:
        a amostra simplesmente não entra, e o portão passa a dizer "não sei" —
        que é recusa. Inventar `agora` aqui daria atraso zero para quem
        reproduz gravação, e atraso zero é a nota máxima do critério que a
        trava existe para reprovar.
        """
        ativo = self.por_ativo.setdefault(asset, PrecosPorAtivo(asset=asset))
        ativo.anotar(valor_e18=valor_e18, ts_servidor_ms=ts_servidor_ms)
        if chegada_ms is not None:
            self.relogio.anotar(
                ts_servidor_ms=ts_servidor_ms, chegada_ms=chegada_ms
            )

    def ancora_da_janela(
        self, *, asset: str, condition_id: str, abertura_epoch: float
    ) -> float | None:
        """A âncora, ou `None` — e `None` NÃO é zero.

        Uma vez resolvida fica guardada: a abertura é um instante, e reler a
        série depois daria outro valor conforme os pontos velhos são podados.
        """
        conhecida = self.ancoras.get(condition_id)
        if conhecida is not None:
            return conhecida

        ativo = self.por_ativo.get(asset)
        if ativo is None:
            self._contar(SEM_ANCORA_SEM_ATIVO)
            return None

        abertura_ms = int(abertura_epoch * 1000)
        if not ativo.serie_e18.alcanca(abertura_ms):
            # O bot subiu depois da janela abrir. Não é lacuna do feed — é
            # ausência de observação, e usar a amostra mais antiga que se tem
            # seria inventar a âncora.
            self._contar(SEM_ANCORA_SERIE_CURTA)
            return None

        valor = ativo.serie_e18.em(abertura_ms)
        if valor is None:
            self._contar(SEM_ANCORA_LACUNA)
            return None

        # e18 → float só aqui, e só para o modelo de probabilidade — a mesma
        # fronteira que o backtest usa. A decisão Up/Down inteira mora na
        # varredura da âncora; esta conversão não decide nada.
        ancora = valor / 1e18
        self.ancoras[condition_id] = ancora
        return ancora

    def esquecer(self, condition_id: str) -> None:
        """Janela fechou: solta a âncora dela para o dicionário não crescer."""
        self.ancoras.pop(condition_id, None)

    def _contar(self, motivo: str) -> None:
        self.sem_ancora[motivo] = self.sem_ancora.get(motivo, 0) + 1

    def resumo(self) -> dict[str, Any]:
        return {
            "ativos": len(self.por_ativo),
            "pontos_por_ativo": {
                nome: len(p.serie_e18) for nome, p in sorted(self.por_ativo.items())
            },
            "vol_pronta": {
                nome: p.vol.ready for nome, p in sorted(self.por_ativo.items())
            },
            "fora_de_ordem": {
                nome: p.serie_e18.fora_de_ordem
                for nome, p in sorted(self.por_ativo.items())
                if p.serie_e18.fora_de_ordem
            },
            "ancoras_fixadas": len(self.ancoras),
            "relogio_do_servidor": self.relogio.resumo(
                agora_ms=int(time.time() * 1000)
            ),
            "sem_ancora": dict(sorted(self.sem_ancora.items())),
            "nota": (
                "`serie_nao_alcanca_a_abertura` alto logo apos subir o bot e "
                "ESPERADO, nao defeito: a serie comeca quando o bot comeca, e "
                "janela cuja abertura ele nao presenciou nao tem ancora. O bot "
                "recem-iniciado nao opera nada por ate uma janela inteira — a "
                "de 4h inclusive. Ja `lacuna_no_instante_da_abertura` "
                "persistente e outra coisa: o feed piscou justamente na "
                "abertura, e ai o alvo e o feed. `vol_pronta` false quer dizer "
                "menos de 20 amostras de retorno; o modelo sai com "
                "confiavel=False e o gatilho nao entra."
            ),
        }
