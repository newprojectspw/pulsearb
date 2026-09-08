"""Item 4.0 (c) — O LAÇO. Cotar, repousar, mexer, sair.

As quatro peças da rota maker existiam e não se falavam:

    cotacao.py        onde cotar          (o que rende mais)
    repouso.py        mexer ou deixar     (histerese dupla)
    execucao_maker    o I/O               (cancelar, repor, reconciliar)
    cliente_sombra    sem enviar nada     (para rodar em SHADOW)

Este módulo é quem as chama, na ordem, uma vez por cadência e por janela
aberta. Ele não decide nada por conta própria — se alguma regra parecer estar
aqui, ela está no lugar errado.

## A cadência é a do repouso, não a da decisão

O laço taker roda a cada 1 s porque a janela fecha em minutos e o edge some com
o atraso. Aqui a pergunta é outra: *vale trocar a cotação que está no livro?* —
e a resposta não muda a cada segundo. O `repouso` já tem tempo mínimo repousada
de 30 s; consultar dez vezes dentro dele só produz dez `repousada_ha_pouco_tempo`
seguidos.

Rodar mais devagar não é economia: é não pagar o custo de avaliar uma decisão
que a histerese vai recusar de qualquer jeito.

## Janela que fecha leva a cotação com ela

Quando uma janela sai da lista de abertas, a cotação dela **tem de ser
cancelada**. Sem isso, cada janela encerrada deixa uma ordem repousando num
mercado que ninguém mais acompanha — e em 24 h isso são dezenas de órfãs que a
reconciliação teria de limpar depois, se alguém lembrasse de rodá-la.

## O portão barra ENTRAR, nunca SAIR

Toda cotação nova passa pelos portões de risco — os MESMOS do taker, por
`avaliar_risco`. Sem isso a rota maker cotaria por fora do kill switch, do
disjuntor e do teto de exposição, e um portão que uma rota inteira contorna
não é portão. Foi assim que o defeito apareceu: com o disjuntor armado, o
taker recusava tudo e o maker cotava normalmente.

**Cancelar NÃO passa pelo portão, e isso não é esquecimento.** Um kill switch
que impedisse cancelar prenderia a cotação no livro exatamente quando alguém
puxou a chave para tirá-la de lá. A trava existe para reduzir exposição; usá-la
para bloquear a saída inverteria o que ela serve. Vale para os quatro portões:
qualquer um deles pode dizer não a uma cotação NOVA, nenhum pode segurar uma
retirada.

Reposicionar é entrar de novo, então passa. Se o portão recusar no meio de um
reposicionamento, o resultado é a cotação antiga cancelada e nenhuma nova — o
livro fica sem a nossa ordem, que é o lado certo de errar quando uma trava de
risco disse não.

## Sem pool, não cota

Janela sem `reward_daily_rate` não tem numerador: cotar nela seria pagar risco
de execução por zero reward, que é exatamente o que o `repouso` recusa quando
uma cotação deixa de pontuar. A diferença é que aqui dá para nem começar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from pulsearb.analysis.rewards import ParametrosDeReward
from pulsearb.live.cotacao import (
    Cotacao,
    escolher_cotacao,
    estimar_retorno,
)
from pulsearb.live.execucao_maker import (
    Efeito,
    OrdemDaCotacao,
    ResultadoDaAcao,
    aplicar_decisao,
)
from pulsearb.live.rastreador import JanelaAoVivo
from pulsearb.live.repouso import AcaoNaCotacao, CotacaoAberta, Decisao, decidir
from pulsearb.obs.logging import get_logger
from pulsearb.risk import OrdemPretendida

log = get_logger(__name__)

#: Cadência do laço maker. Ver o cabeçalho: a decisão é "trocar ou não", e a
#: histerese do `repouso` já tem piso de 30 s repousada.
CADENCIA_DO_MAKER_S = 15.0

#: A grade de distâncias avaliadas, em ticks. `escolher_cotacao` **não inventa
#: candidata** de propósito — quem chama passa a grade —, então ela é explícita
#: aqui, e quem lê o resultado sabe o que foi de fato considerado.
GRADE_DE_TICKS = (1, 2, 3, 4, 5)


class ClienteDeCotacao(Protocol):
    """O que o laço precisa de um cliente. `ClienteSombraDeOrdens` e
    `ClienteDeOrdens` satisfazem os dois — é o mesmo caminho."""

    async def enviar(self, ordem: OrdemPretendida, *, janela: str) -> Any: ...
    async def cancelar(self, order_id: str) -> Any: ...


@dataclass
class LacoMaker:
    """O estado do que está repousando, por janela, e o passo que o move."""

    cliente: ClienteDeCotacao
    tamanho_da_cotacao: float
    #: Os portões de risco. `None` só nos testes que exercitam a mecânica do
    #: laço sem risco nenhum; em produção ele SEMPRE vem, e o `_passo_da_janela`
    #: recusa cotar sem portão em vez de cotar sem trava.
    portao: Any = None
    grade_de_ticks: tuple[int, ...] = GRADE_DE_TICKS
    #: O que repousa, por slug de janela. Uma cotação por janela: duas no mesmo
    #: mercado competiriam entre si pelo mesmo pool.
    abertas: dict[str, CotacaoAberta] = field(default_factory=dict)
    #: Contadores para o relato de 60 s. Mesma disciplina do resto: o que o bot
    #: NÃO fez tem de ser tão legível quanto o que ele fez.
    motivos: dict[str, int] = field(default_factory=dict)

    async def passo(
        self,
        janelas: list[JanelaAoVivo],
        *,
        livro_de,
        agora_epoch: float,
        agora_ns: int,
        feeds_saudaveis: bool = True,
    ) -> list[Efeito]:
        """Uma passada por todas as janelas abertas. Devolve o que mudou.

        `livro_de(token_id, agora_ns)` vem de fora — é o `LivrosAoVivo.livro`,
        injetado para este módulo poder ser testado sem feed.
        """
        efeitos: list[Efeito] = []

        # 1) Janela que fechou leva a cotação junto. ANTES de avaliar as
        #    abertas: se uma fechou e outra abriu no mesmo passo, sair da
        #    fechada primeiro evita cotar duas ao mesmo tempo por um passo.
        abertas_agora = {j.slug for j in janelas}
        for slug in [s for s in self.abertas if s not in abertas_agora]:
            efeito = await self._sair(slug, motivo="janela_fechou")
            efeitos.append(efeito)

        # 2) As abertas.
        for janela in janelas:
            efeito = await self._passo_da_janela(
                janela,
                livro_de=livro_de,
                agora_epoch=agora_epoch,
                agora_ns=agora_ns,
                feeds_saudaveis=feeds_saudaveis,
            )
            if efeito is not None:
                efeitos.append(efeito)
        return efeitos

    async def _passo_da_janela(
        self,
        janela: JanelaAoVivo,
        *,
        livro_de,
        agora_epoch: float,
        agora_ns: int,
        feeds_saudaveis: bool,
    ) -> Efeito | None:
        params = self._parametros(janela)
        if params is None:
            self._contar("sem_pool_de_reward")
            # Se havia cotação e o pool sumiu, sai: ficar seria risco por zero.
            if janela.slug in self.abertas:
                return await self._sair(janela.slug, motivo="pool_sumiu")
            return None

        livro = livro_de(janela.token_up, agora_ns=agora_ns)
        if livro is None:
            # Livro que não serve para decidir é o mesmo caso do portão: quem
            # não sabe não decide. NÃO cancela — o livro pode voltar no passo
            # seguinte, e sair por falta de dado nosso perderia a fila de graça.
            self._contar("livro_indisponivel")
            return None

        horas = max(janela.seconds_left(agora_epoch), 0.0) / 3600.0
        if horas <= 0.0:
            self._contar("janela_sem_tempo")
            return None

        candidatas = [
            Cotacao(distancia_ticks=t, tamanho=self.tamanho_da_cotacao)
            for t in self.grade_de_ticks
        ]
        melhor = escolher_cotacao(candidatas, livro, params, horas=horas)

        aberta = self.abertas.get(janela.slug)
        atual = (
            estimar_retorno(aberta.cotacao, livro, params, horas=horas)
            if aberta is not None
            else None
        )

        decisao = decidir(aberta, melhor, atual, agora_epoch=agora_epoch)
        self._contar(decisao.motivo)

        # O portão, a CADA passada em que haja algo em jogo — e não só quando
        # se vai cotar. Checar apenas no REPOSICIONAR deixava um buraco: com a
        # decisão em MANTER, uma cotação já repousando NUNCA era reavaliada, e
        # um disjuntor que armasse no meio da rodada não a tirava do livro. A
        # trava tem de valer para a exposição que existe agora, não só para a
        # que se vai criar.
        candidata = decisao.nova if decisao.nova is not None else (
            aberta.cotacao if aberta is not None else None
        )
        if candidata is not None:
            recusa = self._portao_recusa(
                candidata, janela, livro=livro, feeds_saudaveis=feeds_saudaveis
            )
            if recusa is not None:
                self._contar(f"portao:{recusa}")
                # Havia cotação e o risco mudou? Sai. Manter uma cotação que o
                # portão não autorizaria HOJE é exposição que ninguém aprovou.
                if aberta is not None:
                    return await self._sair(janela.slug, motivo=f"portao:{recusa}")
                return None

        if decisao.acao is AcaoNaCotacao.MANTER:
            # Nada a fazer no livro. Não chama o I/O: uma passada que não muda
            # nada não pode custar uma ida à rede.
            return None

        return await self._executar(decisao, janela, agora_epoch=agora_epoch)

    def _portao_recusa(
        self, nova: Cotacao, janela: JanelaAoVivo, *, livro, feeds_saudaveis: bool
    ) -> str | None:
        """O motivo da recusa, ou `None` se os portões liberam.

        Usa `avaliar_risco`, e não `avaliar`: o portão de MODO existe para
        impedir envio, e aqui não há envio para impedir. Rodá-lo faria toda
        cotação sair como `modo_nao_opera` e o diário perderia justamente a
        informação que justifica o SHADOW — qual trava seguraria em LIVE. É a
        mesma escolha que o `ExecutorSombra` faz, e pela mesma razão.
        """
        if self.portao is None:
            # Falha fechada: sem portão não se cota. Um laço que cotasse
            # "porque ninguém passou trava" seria o oposto do que a trava serve.
            return "sem_portao"
        ordem = self._ordem_da_cotacao(janela)(nova)
        decisao = self.portao.avaliar_risco(
            ordem,
            feeds_saudaveis=feeds_saudaveis,
            melhor_bid=livro.best_bid,
            melhor_ask=livro.best_ask,
        )
        return None if decisao.pode else (decisao.motivo or "recusado_sem_motivo")

    async def _executar(
        self, decisao: Decisao, janela: JanelaAoVivo, *, agora_epoch: float
    ) -> Efeito:
        efeito = await aplicar_decisao(
            decisao,
            self.abertas.get(janela.slug),
            cliente=self.cliente,
            ordem_da_cotacao=self._ordem_da_cotacao(janela),
            janela=janela.slug,
            agora_epoch=agora_epoch,
        )
        self._guardar(janela.slug, efeito)
        return efeito

    async def _sair(self, slug: str, *, motivo: str) -> Efeito:
        aberta = self.abertas.get(slug)
        efeito = await aplicar_decisao(
            Decisao(AcaoNaCotacao.CANCELAR, motivo),
            aberta,
            cliente=self.cliente,
            # Não vai enviar nada: CANCELAR nunca chama `ordem_da_cotacao`.
            ordem_da_cotacao=_nunca_chamado,
            janela=slug,
            agora_epoch=0.0,
        )
        self._contar(motivo)
        self._guardar(slug, efeito)
        return efeito

    def _guardar(self, slug: str, efeito: Efeito) -> None:
        """O novo estado do nosso lado, depois da ação.

        `RECONCILIAR` MANTÉM o registro: o estado é desconhecido, e esquecer a
        cotação transformaria "não sei" em "não tenho" — que é a suposição que
        cria órfã. É a mesma regra do INCERTA do cliente.
        """
        if efeito.aberta is None:
            self.abertas.pop(slug, None)
        else:
            self.abertas[slug] = efeito.aberta
        if efeito.resultado is ResultadoDaAcao.RECONCILIAR:
            log.warning(
                "cotacao maker em estado desconhecido: reconciliar",
                slug=slug,
                motivo=efeito.motivo,
                **efeito.detalhe,
            )

    def _parametros(self, janela: JanelaAoVivo) -> ParametrosDeReward | None:
        """Os parâmetros do pool desta janela, ou `None` se ela não tem pool.

        Os três campos vêm da `JanelaAoVivo`, lidos do payload cru pela mesma
        função que o recorder usa (`markets/rewards_da_gamma.py`). Aqui só se
        monta o objeto — nenhuma leitura nova, para não haver segunda
        interpretação da mesma Gamma.
        """
        if janela.reward_daily_rate is None or janela.reward_max_spread is None:
            return None
        return ParametrosDeReward(
            daily_rate=janela.reward_daily_rate,
            min_size=janela.reward_min_size or 0.0,
            max_spread=janela.reward_max_spread,
            tick_size=janela.tick_size,
        )

    def _ordem_da_cotacao(self, janela: JanelaAoVivo) -> OrdemDaCotacao:
        """Como esta janela vira ordem. Fecha sobre a janela porque o preço
        depende do tick e do meio DELA."""

        def montar(cotacao: Cotacao) -> OrdemPretendida:
            # Cotamos do lado Up, no bid: a rota maker ganha por repousar, e
            # repousar do lado comprado é o que o `cotacao.py` mede.
            preco = cotacao.preco(
                meio=0.5, tick_size=janela.tick_size, do_lado_bid=True
            )
            return OrdemPretendida(
                slug=janela.slug,
                token_id=janela.token_up,
                lado_up=True,
                shares=cotacao.tamanho,
                preco_limite=preco,
            )

        return montar

    def _contar(self, motivo: str) -> None:
        self.motivos[motivo] = self.motivos.get(motivo, 0) + 1

    def resumo(self) -> dict[str, Any]:
        """O que sai no relato de 60 s do SHADOW."""
        return {
            "cotacoes_repousando": len(self.abertas),
            "por_janela": sorted(self.abertas),
            "motivos": dict(sorted(self.motivos.items())),
            "nota": (
                "`motivos` acumula desde o inicio e responde a pergunta que "
                "importa quando o bot nao cota: ele nao achou onde cotar, ou "
                "achou e a histerese segurou? `sem_pool_de_reward` alto quer "
                "dizer que as janelas descobertas nao participam do programa, "
                "e ai o alvo e a descoberta, nao a cotacao."
            ),
        }


def _nunca_chamado(_: Cotacao) -> OrdemPretendida:
    raise AssertionError(
        "ordem_da_cotacao chamado num CANCELAR — o `execucao_maker` nao deveria"
    )
