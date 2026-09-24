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

## O preço sai do meio DO LIVRO, e a conta é do lado que se coloca

Achado da rodada SHADOW r4 de 2026-09-14 (`data/shadow/diario-20260914-045201-*`):
todas as 69 cotações saíram entre 0,46 e 0,499 em mercados cujo meio ia de
0,03 a 0,97. O `montar` fechava sobre `meio=0.5` fixo — o preço não olhava o
livro. Num mercado a 0,90 isso é um bid 40 ¢ abaixo do meio, que não pontua e
nunca executa; num mercado a 0,10 é um bid 39 ¢ ACIMA do ask — que executa na
hora como taker, pagando o spread inteiro. O meio vem do `livro.mid`, o
mesmo que o `estimar_retorno` usa; sem meio, não se cota (`livro_sem_meio`).

E a estimativa contava DOIS lados (`Cotacao.dois_lados=True`) enquanto o
`montar` colocava UM — o bid do Up. O §15.3 paga o lado único a um terço
dentro de [0,10, 0,90] e a ZERO fora; contar dois e colocar um inflava o
score três vezes (ou infinitamente) e ainda pontuava fora da faixa. Agora a
cotação tem as DUAS pernas que a conta descreve: bid no Up a `meio − d` e
bid no Down a `(1 − meio) − d` — que no livro do Up é o ask a `meio + d`,
exatamente o preço que `estimar_retorno` pontua do lado ask. As duas passam
pelo portão, entram juntas e saem juntas (`execucao_maker`). Se as duas
preenchem, o par Up+Down custou `1 − 2d` e paga 1: não é a hipótese de
lucro — é só o motivo de as duas pernas não serem posição direcional.

## Sem pool, não cota

Janela sem `reward_daily_rate` não tem numerador: cotar nela seria pagar risco
de execução por zero reward, que é exatamente o que o `repouso` recusa quando
uma cotação deixa de pontuar. A diferença é que aqui dá para nem começar.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from pulsearb.analysis.rewards import ParametrosDeReward, denominador_pessimista
from pulsearb.backtest.book import OrderBook
from pulsearb.live.caixa_maker import CaixaDoMaker, espelho_do_livro
from pulsearb.live.cotacao import (
    AncoraDoMicroprice,
    Cotacao,
    EscolhaDaGrade,
    RetornoEstimado,
    avaliar_grade,
    estimar_retorno_repousando,
)
from pulsearb.live.execucao_maker import (
    Efeito,
    OrdemDaCotacao,
    Reconciliacao,
    ResultadoDaAcao,
    aplicar_decisao,
    cancelar_orfas,
    reconciliar,
)
from pulsearb.live.rastreador import JanelaAoVivo
from pulsearb.live.repouso import (
    AcaoNaCotacao,
    AncoraEmVigor,
    CotacaoAberta,
    Decisao,
    decidir,
)
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


def _cabe_no_livro(ordem: OrdemPretendida) -> bool:
    """O preço desta perna é um preço que EXISTE no livro: `0 < p < 1`.

    Afirmado pelo positivo de propósito. A forma invertida diz o que o preço
    não é, e quem lê tem de negar de cabeça para saber o que se quer.

    Vale para NaN sem caso especial: `0.0 < nan < 1.0` é `False`, então um
    preço que não é número não cabe no livro — que é o lado certo de errar.
    """
    return 0.0 < ordem.preco_limite < 1.0


class ClienteDeCotacao(Protocol):
    """O que o laço precisa de um cliente. `ClienteSombraDeOrdens` e
    `ClienteDeOrdens` satisfazem os dois — é o mesmo caminho."""

    async def enviar(self, ordem: OrdemPretendida, *, janela: str) -> Any: ...
    async def cancelar(self, order_id: str) -> Any: ...


@dataclass(frozen=True, slots=True)
class _DadosDaPassada:
    """O que uma passada precisa do livro para decidir. Junta o que os
    portões de dados produzem, para o passo não carregar cinco variáveis
    soltas nem repetir a leitura."""

    livro: OrderBook
    livro_down: OrderBook | None
    meio: float
    horas: float
    ancora: AncoraDoMicroprice | None
    #: A âncora está ligada e o microprice de alguma perna não está à mão.
    #: Não se COTA sob uma regra que não se conseguiu avaliar — mas a passada
    #: continua, porque o que já repousa tem de ser contado e reavaliado.
    sem_microprice: bool = False


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
    #: Cobertura dos livros de pool, por passada avaliada: quantas viram livro,
    #: e quando NÃO viram, se foi a conexão dos pools que caiu ou só o token
    #: que emudeceu. É diagnóstico, não regra — `livro_indisponivel` segue
    #: contando o total e a passada segue sem cotar e sem cancelar. Ver
    #: `_diagnosticar_cobertura`.
    cobertura_dos_pools: dict[str, int] = field(default_factory=dict)
    #: A fração do pool das candidatas que o teto ADMITIU, por AVALIAÇÃO da
    #: grade — não por ordem. Conta toda passada em que o teto deixou uma
    #: candidata passar, inclusive as que viram MANTER e as que o portão ainda
    #: barraria: é o complemento por avaliação das `recusas_por_teto`, e serve
    #: para provar que o teto nunca admite fração acima dele. NÃO é "ordens
    #: colocadas" — para isso ver `_fracao_ordem_*`.
    _fracao_admitida_soma: float = field(default=0.0, repr=False)
    _fracao_admitida_n: int = field(default=0, repr=False)
    _fracao_admitida_max: float = field(default=0.0, repr=False)
    #: A fração do pool das cotações EFETIVAMENTE colocadas/reposicionadas —
    #: atualizada só DEPOIS da ação bem-sucedida (`COLOCADA`/`REPOSICIONADA`),
    #: nunca numa avaliação de MANTER nem numa candidata barrada pelo portão. É
    #: a "fração das cotações efetivamente aceitas" que o item 3 pede.
    _fracao_ordem_soma: float = field(default=0.0, repr=False)
    _fracao_ordem_n: int = field(default=0, repr=False)
    _fracao_ordem_max: float = field(default=0.0, repr=False)
    #: O relógio do 4.2: o que as cotações repousando teriam rendido e quantas
    #: vezes teriam executado. Ver `caixa_maker`.
    caixa: CaixaDoMaker = field(default_factory=CaixaDoMaker)
    #: A regra que os makers dos leaderboards usam e que o `maker_de_pares`
    #: mediu na gravação (2026-09-14): ficar parado com o livro andando contra
    #: era quase toda a perda — −583,54 USDC em 4 h deixando a ordem
    #: descansar, −31,87 recolhendo-a quando o melhor bid cai abaixo dela.
    #: Desligada por padrão para as rodadas em curso não mudarem de
    #: comportamento; a rota de pools liga por `Settings`.
    recolhe_quando_o_livro_anda: bool = False
    #: Quantos ticks ABAIXO do microprice a cotação pode chegar, no máximo
    #: (`None` = sem âncora, o comportamento de sempre). O microprice é o meio
    #: ponderado pelo tamanho do outro lado — para onde o livro vai —, e
    #: `docs/OUTROS_BOTS.md` §6 item 6 mede que cotar a partir dele é o que
    #: vira o sinal do termo determinístico. A âncora só APERTA: nunca puxa a
    #: cotação para mais perto do meio do que a distância já escolhida.
    ticks_abaixo_do_microprice: int | None = None
    #: Segundos sem cotar a janela depois de um fill ATRAVESSADO nela
    #: (`None` = sem pausa, o comportamento de sempre). É o regime EVENT do
    #: `poly-maker` disparado pelo NOSSO fill, e não pelo salto do livro: quem
    #: nos atravessou sabia de algo, e o minuto seguinte é o pior momento para
    #: estar no livro. O `maker_de_pares` mediu em 2026-09-13: base +14,24,
    #: com 30 s **+18,42**, com 90 s −0,52 (mata o fill). E a r7 do SHADOW diz
    #: o mesmo do outro lado: 2 execuções atravessadas de 12 dominaram o
    #: markout (−22,85 USDC).
    pausa_apos_fill_toxico_s: float | None = None
    #: Teto da participação estimada no pool, por cotação (`None` = sem teto, o
    #: comportamento de sempre). Aplicado ANTES da escolha final, em
    #: `avaliar_grade`: candidata cuja `fracao_do_pool` estimada excede o teto
    #: sai da disputa, e se todas as que pontuam excederem não se cota
    #: (`fracao_do_pool_acima_do_teto`). É trava de ENTRADA e de
    #: reposicionamento novo — nunca cancela o que já repousa, porque não é uma
    #: nova leitura do livro que deva criar churn (ver `_passo_da_janela`).
    fracao_maxima_do_pool: float | None = None
    #: Como saber se a CONEXÃO dos pools está de pé, para o diagnóstico de
    #: cobertura separar "conexão de pools indisponível" de "livro/token
    #: indisponível" quando `livro_de` devolve `None` (`None` = sem sinal, e o
    #: diagnóstico só conta `sem_diagnostico`). É observação pura: NÃO muda a
    #: regra — livro indisponível segue sem cancelar e sem cotar, por qualquer
    #: das duas causas. O laço não deve afrouxar silêncio nem portão por causa
    #: dela; ela só torna a causa visível no relato de 60 s.
    conexao_de_pools_ok: Callable[[], bool | None] | None = None
    #: O que a reconciliação de arranque achou, para o relato de 60 s. `None`
    #: até ela rodar — e "não rodou" é diferente de "rodou e achou zero".
    ultima_reconciliacao: dict[str, Any] | None = None
    #: Em SHADOW a nossa ordem NÃO está no livro, então `best_bid < preço` é
    #: o gatilho exato. Em LIVE ela está — e quando o mercado anda para
    #: baixo, ela VIRA o melhor bid: o gatilho passa a ser "somos o topo e
    #: estamos sozinhos nele" (tamanho no nível ≤ o nosso).
    nossa_ordem_esta_no_livro: bool = False
    _negocios_desde: Any = field(default=None, repr=False)
    #: Os tokens de cada janela com cotação, para o recolher entre passadas
    #: (ele não recebe as janelas — só o livro).
    _tokens: dict[str, tuple[str, str]] = field(default_factory=dict, repr=False)
    #: A referência de cada perna para o recolher: `min(preço, melhor bid do
    #: mercado na primeira observação)`, por slug e por `desde_epoch`. Uma
    #: cotação que já MELHORA o topo ao nascer (livro largo: bid 0,40, meio
    #: 0,50, nós a 0,49) veria `best_bid < preço` no primeiro segundo e
    #: cancelaria sem o mercado ter andado (revisão do Codex, #126). O que
    #: recolhe é o mercado cair ABAIXO de onde estava quando entramos.
    _referencia_do_recolher: dict[tuple[str, int], tuple[float | None, float | None]] = (
        field(default_factory=dict, repr=False)
    )
    #: Os parâmetros de reward de cada janela com cotação, para a caixa
    #: acertar o último intervalo ANTES de recolher (o recolher não recebe a
    #: janela — só o livro).
    _params: dict[str, ParametrosDeReward] = field(default_factory=dict, repr=False)

    async def passo(
        self,
        janelas: list[JanelaAoVivo],
        *,
        livro_de,
        agora_epoch: float,
        agora_ns: int,
        feeds_saudaveis: bool = True,
        negocios_desde=None,
    ) -> list[Efeito]:
        """Uma passada por todas as janelas abertas. Devolve o que mudou.

        `livro_de(token_id, agora_ns)` vem de fora — é o `LivrosAoVivo.livro`,
        injetado para este módulo poder ser testado sem feed. `negocios_desde`
        (`LivrosAoVivo.negocios_desde`) idem, para a caixa conferir os prints;
        sem ele a caixa soma rewards e não conta execuções.
        """
        efeitos: list[Efeito] = []
        self._negocios_desde = negocios_desde
        # Fecha o markout das execuções possíveis cujo horizonte já passou,
        # ANTES de olhar prints novos — o livro é o deste instante.
        self.caixa.medir_markout(livro_de, agora_ns=agora_ns)

        # 1) Janela que fechou leva a cotação junto. ANTES de avaliar as
        #    abertas: se uma fechou e outra abriu no mesmo passo, sair da
        #    fechada primeiro evita cotar duas ao mesmo tempo por um passo.
        abertas_agora = {j.slug for j in janelas}
        for slug in [s for s in self.abertas if s not in abertas_agora]:
            efeito = await self._sair(slug, motivo="janela_fechou")
            efeitos.append(efeito)
        # Tokens e parâmetros são de toda janela AVALIADA, cotada ou não —
        # a saída da cotação não os alcança quando nunca houve cotação, e
        # 14 dias de janelas curtas rodando acumulariam (revisão do Codex,
        # #126). Janela que sumiu leva os seus.
        for cache in (self._tokens, self._params):
            for slug in [s for s in cache if s not in abertas_agora]:
                cache.pop(slug, None)

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
        self._tokens[janela.slug] = (janela.token_up, janela.token_down)
        params = self._parametros(janela)
        if params is None:
            self._contar("sem_pool_de_reward")
            # Se havia cotação e o pool sumiu, sai: ficar seria risco por zero.
            # O motivo da SAÍDA tem outro nome de propósito — `sem_pool` conta
            # janela que nunca teve pool, `pool_sumiu` conta cotação perdida.
            if janela.slug in self.abertas:
                return await self._sair(janela.slug, motivo="pool_sumiu")
            return None
        self._params[janela.slug] = params

        aberta = self.abertas.get(janela.slug)
        self._conferir_prints(
            janela, aberta, livro_de=livro_de, agora_ns=agora_ns, params=params
        )

        if self._em_pausa_por_fill_toxico(janela.slug, agora_ns):
            # ANTES do portão de dados: cancelar não precisa de livro, e a
            # pausa que esperasse o livro voltar poderia nunca acontecer
            # (revisão do Codex, #131). Quem sai fecha a conta primeiro, pelo
            # mesmo caminho do `livro_andou_contra`.
            return await self._sair_por_fill_toxico(
                janela.slug, livro_de=livro_de, agora_ns=agora_ns
            )

        dados, recusa = self._dados_da_passada(
            janela, livro_de=livro_de, agora_epoch=agora_epoch, agora_ns=agora_ns
        )
        if dados is None:
            # Todo motivo daqui é falta de dado NOSSO, e nenhum cancela: o
            # livro volta no passo seguinte, e sair perderia a fila de graça.
            self._contar(recusa)
            return None
        livro, livro_down = dados.livro, dados.livro_down
        meio, horas, ancora = dados.meio, dados.horas, dados.ancora

        melhor, fracao_no_teto, encerrar = self._escolher_da_grade(dados, params, aberta)
        if encerrar:
            # Nada a decidir: sem cotação no livro e sem candidata cotável
            # (ausente por microprice, ou barrada pelo teto). Seguir daria
            # `sem_candidata_que_pontue` por um motivo que já tem nome — inflá-lo
            # aqui apagaria a diferença entre travado e sem trade (revisão
            # adversa).
            return None

        atual = None
        if aberta is not None:
            # A cotação que JÁ repousa é avaliada no preço em que foi enviada,
            # não a `distancia_ticks` do meio de agora: é assim que o meio
            # andando a deixa "não pontuar mais" (revisão do #118).
            atual = estimar_retorno_repousando(aberta, livro, params, horas=horas)
            # O relógio do 4.2: o que ESTA cotação rendeu desde a última
            # passada, pela mesma conta que a colocou. Antes da decisão, porque
            # o tempo já correu — sair agora não apaga o que repousou.
            self.caixa.acertar(
                janela.slug, aberta, livro, params, agora_epoch=agora_epoch
            )

        decisao = decidir(
            aberta,
            melhor,
            atual,
            agora_epoch=agora_epoch,
            ancora=self._ancora_em_vigor(janela, melhor, meio=meio, ancora=ancora),
        )
        self._contar(decisao.motivo)

        # O portão, a CADA passada em que haja algo em jogo — e não só quando
        # se vai cotar. Checar apenas no REPOSICIONAR deixava um buraco: com a
        # decisão em MANTER, uma cotação já repousando NUNCA era reavaliada, e
        # um disjuntor que armasse no meio da rodada não a tirava do livro. A
        # trava tem de valer para a exposição que existe agora, não só para a
        # que se vai criar.
        candidata = decisao.nova
        if candidata is None and aberta is not None:
            candidata = aberta.cotacao
        if candidata is not None:
            recusa = self._portao_recusa(
                candidata,
                janela,
                livro=livro,
                meio=meio,
                feeds_saudaveis=feeds_saudaveis,
                ancora=ancora,
            )
            if recusa is not None:
                # Havia cotação e o risco mudou? Sai. Manter uma cotação que o
                # portão não autorizaria HOJE é exposição que ninguém aprovou.
                return await self._recusar(janela.slug, f"portao:{recusa}")

        if decisao.acao is AcaoNaCotacao.MANTER:
            # Nada a fazer no livro. Não chama o I/O: uma passada que não muda
            # nada não pode custar uma ida à rede.
            return None

        if self._perna_down_sem_bids(decisao, livro_down):
            return await self._recusar(janela.slug, "perna_down_sem_bids")

        efeito = await self._executar(
            decisao,
            janela,
            meio=meio,
            agora_epoch=agora_epoch,
            livro=livro,
            livro_down=livro_down,
            ancora=ancora,
        )
        self._registrar_ordem_efetiva(fracao_no_teto, efeito)
        return efeito

    def _conferir_prints(
        self,
        janela: JanelaAoVivo,
        aberta: CotacaoAberta | None,
        *,
        livro_de,
        agora_ns: int,
        params: ParametrosDeReward,
    ) -> None:
        """Os prints desde a última passada, antes de qualquer decisão: o que
        nos pegaria já pegou, com ou sem livro para decidir agora."""
        if aberta is None or self._negocios_desde is None:
            return
        self.caixa.conferir_prints(
            janela.slug,
            aberta,
            token_up=janela.token_up,
            token_down=janela.token_down,
            negocios_desde=self._negocios_desde,
            agora_ns=agora_ns,
            livro_de=livro_de,
            params=params,
        )

    async def ver_prints_entre_passadas(self, livro_de, *, agora_ns: int) -> list[Efeito]:
        """Entre passadas: vê os prints SEMPRE, e tira do livro quem levou um
        fill atravessado SE a pausa estiver ligada.

        **A separação entre esses dois "se" é o ponto, e ela custou uma
        revisão.** Antes, o método inteiro só rodava com a pausa ligada — e
        então os prints eram vistos a cada segundo na rodada da pausa e só a
        cada 15 s nas outras três. A `CaixaDoMaker` carimba `meio_no_fill`
        com o livro DO INSTANTE EM QUE VÊ o print, e mede o markout 5 s
        depois do negócio: um fill logo após uma passada saía medido do
        segundo 1 ao 5 na rodada da pausa, e do livro do segundo 15 até o 16
        nas outras. **O instrumento de markout ficava diferente entre
        controle e tratamento**, e a comparação de 14 dias mediria a
        diferença entre os dois instrumentos junto com a da regra (revisão do
        Codex, #131).

        Ver print não muda nada do que o bot faz — só quando a conta é
        fechada. Cancelar é que é a regra, e só essa parte olha o knob.

        A cadência de 15 s ainda importa para o outro lado: um fill logo
        depois de uma passada deixava a outra perna exposta quase uma
        cadência inteira, e encurtava a pausa medida de 30 s para 15–30 s
        efetivos. A decisão de COTAR segue nos 15 s.
        """
        efeitos: list[Efeito] = []
        for slug, aberta in list(self.abertas.items()):
            tokens = self._tokens.get(slug)
            if tokens is None or self._negocios_desde is None:
                continue
            self.caixa.conferir_prints(
                slug,
                aberta,
                token_up=tokens[0],
                token_down=tokens[1],
                negocios_desde=self._negocios_desde,
                agora_ns=agora_ns,
                livro_de=livro_de,
                params=self._params.get(slug),
            )
            if (
                self.pausa_apos_fill_toxico_s is not None
                and self._em_pausa_por_fill_toxico(slug, agora_ns)
            ):
                saiu = await self._sair_por_fill_toxico(
                    slug, livro_de=livro_de, agora_ns=agora_ns
                )
                if saiu is not None:
                    efeitos.append(saiu)
        return efeitos

    async def _sair_por_fill_toxico(
        self, slug: str, *, livro_de, agora_ns: int
    ) -> Efeito | None:
        """Fecha a conta e tira do livro, ou só conta o motivo se não havia
        cotação — que é o caso de a pausa ainda estar valendo."""
        aberta = self.abertas.get(slug)
        tokens = self._tokens.get(slug)
        if aberta is None or tokens is None:
            self._contar("pausa_por_fill_toxico")
            return None
        livros = [livro_de(token_id, agora_ns=agora_ns) for token_id in tokens]
        return await self._recolher(
            slug, aberta, tokens, livros,
            livro_de=livro_de, agora_ns=agora_ns, motivo="pausa_por_fill_toxico",
        )

    def _em_pausa_por_fill_toxico(self, slug: str, agora_ns: int) -> bool:
        """Esta janela levou um fill ATRAVESSADO há pouco?

        Quem atravessa a nossa cotação está pagando acima do nosso preço para
        entrar AGORA — e normalmente sabe de algo que o livro ainda não
        mostrou. Ficar cotando no minuto seguinte é oferecer a mesma opção de
        graça outra vez. É o regime EVENT do `poly-maker`, disparado pelo
        NOSSO fill em vez do salto do livro.

        A pausa vale mesmo com a cotação já fora do livro: quem chama TIRA a
        que estiver lá e não recoloca enquanto durar. Desligada devolve falso
        sempre — a linha de base não muda.
        """
        if self.pausa_apos_fill_toxico_s is None:
            return False
        ultimo = self.caixa.ultimo_fill_toxico_ns.get(slug)
        if ultimo is None:
            return False
        return agora_ns - ultimo < self.pausa_apos_fill_toxico_s * 1e9

    def _melhor_candidata(
        self,
        dados: _DadosDaPassada,
        params: ParametrosDeReward,
        aberta: CotacaoAberta | None,
    ) -> EscolhaDaGrade | None:
        """O resultado da grade para este livro, ou `None`.

        `None` quando a âncora está ligada e o microprice não está à mão: aí
        não se COTA — cotar sob uma regra que não se conseguiu avaliar é não
        ter a regra —, e só isso. Quem já repousa segue sendo contado pela
        caixa e reavaliado pelo portão, senão um disjuntor que armasse durante
        a falta não tiraria a ordem do livro (revisão do Codex, #127; é a
        propriedade que o 4.0 registra ter custado caro para achar).

        Fora isso devolve o `EscolhaDaGrade` INTEIRO, e não só a escolhida: o
        laço precisa distinguir *nenhuma pontuou* de *o teto barrou todas* para
        nomear o motivo certo no relato. O teto de fração entra aqui, ANTES da
        escolha final — ver `avaliar_grade`.

        Dois lados, porque são DUAS pernas que se colocam. Ver o cabeçalho.
        """
        if dados.sem_microprice:
            self._contar("sem_microprice")
            return None
        candidatas = [
            Cotacao(distancia_ticks=t, tamanho=self.tamanho_da_cotacao)
            for t in self.grade_de_ticks
        ]
        return avaliar_grade(
            candidatas,
            dados.livro,
            params,
            horas=dados.horas,
            ancora=dados.ancora,
            fracao_maxima=self.fracao_maxima_do_pool,
            denominador_para_teto=self._denominador_para_teto(dados.livro, aberta, params),
        )

    def _denominador_para_teto(
        self,
        livro: OrderBook,
        aberta: CotacaoAberta | None,
        params: ParametrosDeReward,
    ) -> float | None:
        """O denominador da fração SEM a nossa ordem repousando, ou `None`.

        Só em LIVE (`nossa_ordem_esta_no_livro`) e num reposicionamento
        (há `aberta`): ali a nossa ordem já está no livro e entra no
        `denominador_pessimista`, mas um substituto a cancela antes de repousar,
        então a fatia dele — a que o teto tem de barrar — é sobre o livro SEM
        ela. Sem o desconto, uma troca simétrica num livro só nosso estimaria
        ~50% e viraria 100% depois do cancelamento, furando o teto (revisão do
        Codex, #193). Em SHADOW a nossa ordem NÃO está no livro (ninguém sabe
        que ela existe), então isto devolve `None` e o teto usa a fatia normal
        — a linha de base não muda. Exclui só a perna Up: ela é o único lado
        nosso que está NESTE livro; a perna Down mora no livro do Down, que não
        entra neste denominador. É a mesma exclusão do recolher
        (`_sem_o_nosso_nivel`), e o meio/preço saem do livro REAL — o teto muda
        só o denominador, nunca o preço avaliado (senão avaliaria um preço e
        enviaria outro, o modo de falha do §6.1b)."""
        if self.fracao_maxima_do_pool is None:
            return None
        if not (self.nossa_ordem_esta_no_livro and aberta is not None):
            return None
        if aberta.preco_up <= 0.0:
            return None
        bids = _sem_o_nosso_nivel(livro.bids, aberta.preco_up, aberta.cotacao.tamanho)
        sem_a_nossa = OrderBook(
            asset_id=livro.asset_id, bids=bids, asks=livro.asks, ts_ns=livro.ts_ns
        )
        return denominador_pessimista(sem_a_nossa, params)

    def _escolher_da_grade(
        self,
        dados: _DadosDaPassada,
        params: ParametrosDeReward,
        aberta: CotacaoAberta | None,
    ) -> tuple[RetornoEstimado | None, float | None, bool]:
        """A cotação escolhida da grade e se a passada pode ENCERRAR aqui.

        Encerra cedo só quando NÃO há cotação no livro e também não há
        candidata cotável — porque o microprice faltou (`sem_microprice`, já
        contado) ou porque o teto de fração barrou todas
        (`fracao_do_pool_acima_do_teto`). Com algo repousando nunca encerra: a
        candidata fica `None` e a decisão cai em MANTER, sem cancelar — o teto
        barra ENTRAR e reposicionar, não cria churn no que já está no livro.

        Registra de passagem a fração aceita (a escolhida) e a recusa por teto,
        para o relato de 60 s ter as duas pontas da trava.
        """
        ha_aberta = aberta is not None
        escolha = self._melhor_candidata(dados, params, aberta)
        if escolha is None:
            # sem_microprice, já contado em `_melhor_candidata`.
            return None, None, not ha_aberta
        if escolha.escolhida is not None:
            # A fração COMO O TETO A VIU (pós-cancelamento em LIVE), não a do
            # `RetornoEstimado`, que em LIVE inclui a ordem velha e subestima.
            self._registrar_fracao_admitida(escolha.fracao_no_teto)
            return escolha.escolhida, escolha.fracao_no_teto, False
        if escolha.bloqueada_por_teto:
            # Havia candidata que pontua, mas TODAS excederiam a nossa
            # participação máxima. Nome próprio, para não virar
            # `sem_candidata_que_pontue`, que diz o oposto — 'não achei onde
            # cotar'.
            self._contar("fracao_do_pool_acima_do_teto")
            return None, None, not ha_aberta
        return None, None, False

    def _dados_da_passada(
        self,
        janela: JanelaAoVivo,
        *,
        livro_de,
        agora_epoch: float,
        agora_ns: int,
    ) -> tuple[_DadosDaPassada | None, str | None]:
        """O que esta passada precisa para decidir, ou o motivo de não decidir.

        Os quatro portões daqui têm o MESMO tratamento, e é por isso que moram
        juntos: nenhum cancela o que já repousa. Livro que não serve, janela
        sem tempo, livro de um lado só e microprice indisponível são todos
        falta de dado NOSSO — quem não sabe não decide, mas sair por isso
        perderia a fila de graça, e o dado volta no passo seguinte.
        """
        livro = livro_de(janela.token_up, agora_ns=agora_ns)
        if livro is None:
            self._diagnosticar_up_ausente()
            return None, "livro_indisponivel"
        horas = max(janela.seconds_left(agora_epoch), 0.0) / 3600.0
        if horas <= 0.0:
            return None, "janela_sem_tempo"
        meio = livro.mid
        if meio is None:
            # Livro de um lado só não tem meio, e sem meio não há onde cotar.
            return None, "livro_sem_meio"
        # O livro do Down é lido AQUI, antes da escolha: a âncora do
        # microprice precisa dele, e a perna Down é um bid no livro dela — não
        # o espelho do Up (a revisão do #126 mostrou que os dois não são
        # complementares).
        livro_down = livro_de(janela.token_down, agora_ns=agora_ns)
        # Cobertura com o Up já disponível: a perna Down é a OUTRA perna de um
        # maker de dois lados, e o Up presente não prova o Down presente. Só
        # diagnóstico — não muda a regra: Down ausente não impede cotar (o
        # score sai do livro do Up), a âncora do microprice é que trata a falta
        # via `sem_microprice`.
        self._diagnosticar_cobertura(down_disponivel=livro_down is not None)
        ancora, sem_microprice = self._ancora_do_microprice(livro, livro_down)
        return (
            _DadosDaPassada(
                livro=livro,
                livro_down=livro_down,
                meio=meio,
                horas=horas,
                ancora=ancora,
                sem_microprice=sem_microprice,
            ),
            None,
        )

    def _ancora_em_vigor(
        self,
        janela: JanelaAoVivo,
        melhor,
        *,
        meio: float,
        ancora: AncoraDoMicroprice | None,
    ) -> AncoraEmVigor | None:
        """O que a âncora impõe a esta passada: o teto de cada perna e o preço
        em que a candidata repousaria.

        Os preços saem da MESMA função que monta a ordem, para o número que o
        repouso compara ser o número que iria para o fio. A perna Down é um
        bid no livro dela, então o microprice dela e o preço vêm da âncora
        espelhada.

        O LIMITE DURO sai mesmo sem candidata: livro que alarga tira toda a
        grade ancorada da faixa de reward, e é aí que a que repousa mais
        precisa dele — ela ainda pontua, e sem ele ficaria acima do microprice
        para sempre (revisão do Codex, #127).
        """
        if ancora is None:
            return None
        espelhada = ancora.no_livro_do_down()
        if ancora.bid is None or espelhada.bid is None:
            return None
        precos = None
        if melhor is not None:
            precos = (
                self._ordem_da_cotacao(janela, meio=meio, ancora=ancora)(
                    melhor.cotacao
                ).preco_limite,
                self._ordem_da_cotacao(janela, meio=meio, lado_up=False, ancora=ancora)(
                    melhor.cotacao
                ).preco_limite,
            )
        return AncoraEmVigor(
            microprice=(ancora.bid, espelhada.bid), precos=precos
        )

    def _ancora_do_microprice(
        self, livro: OrderBook, livro_down: OrderBook | None
    ) -> tuple[AncoraDoMicroprice | None, bool]:
        """A âncora desta passada, e se ela está ligada mas indisponível.

        Devolve `(None, False)` com a regra desligada — o caminho de sempre.
        Com ela ligada e o microprice de alguma perna fora de alcance, devolve
        `(None, True)`: falha fechada para COLOCAR, porque cotar sob uma regra
        que não se conseguiu avaliar é não ter a regra. Só isso — a passada
        segue, e quem já repousa continua a ser contado e reavaliado pelo
        portão (revisão do Codex, #127).

        Cada perna é ancorada pelo microprice do LIVRO DELA: os dois livros
        não são complementares (revisão do #126), e o espelho do Up daria
        âncora sintética contra um livro real.
        """
        if self.ticks_abaixo_do_microprice is None:
            return None, False
        micro_up = livro.microprice
        micro_down = livro_down.microprice if livro_down is not None else None
        if micro_up is None or micro_down is None:
            return None, True
        return (
            AncoraDoMicroprice(
                bid=micro_up,
                ask=1.0 - micro_down,
                ticks=self.ticks_abaixo_do_microprice,
            ),
            False,
        )

    def _perna_down_sem_bids(
        self, decisao: Decisao, livro_down: OrderBook | None
    ) -> bool:
        """A perna Down iria para um livro sem bid nenhum?

        Só importa com o recolher ligado: ele tiraria o par no segundo
        seguinte, e a passada de 15 s o poria de volta — coloca-e-recolhe sem
        fim, diário inflado e repouso nenhum para medir. A simulação medida
        não coloca sem bid (`maker_de_pares.py`). O livro do Up sem bids já
        para antes, em `livro_sem_meio`.
        """
        return (
            self.recolhe_quando_o_livro_anda
            and decisao.nova is not None
            and decisao.nova.dois_lados
            and livro_down is not None
            and not livro_down.bids
        )

    def _portao_recusa(
        self,
        nova: Cotacao,
        janela: JanelaAoVivo,
        *,
        livro,
        meio: float,
        feeds_saudaveis: bool,
        ancora: AncoraDoMicroprice | None = None,
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
        # As duas pernas, cada uma como a ordem que vai para o fio. O portão
        # vê o livro do Up; o do Down é o espelho (bid = 1 − ask), com o mesmo
        # spread — que é a única coisa que o portão lê dele.
        pernas = [self._ordem_da_cotacao(janela, meio=meio, ancora=ancora)(nova)]
        if nova.dois_lados:
            pernas.append(
                self._ordem_da_cotacao(
                    janela, meio=meio, lado_up=False, ancora=ancora
                )(nova)
            )
        # ANTES de perguntar ao portão: uma perna cujo preço saiu de (0, 1)
        # não é ordem, e perguntar por ela devolve `ordem_mal_formada` — um
        # motivo que o próprio `gates.py` documenta como "defeito de quem
        # chamou". Não é defeito nosso: é a FORMA do mercado.
        #
        # Medido em 2026-09-22 (runbook §10.1m): 1.899 recusas assim em 18 h.
        # A perna Down é um bid no livro dela, cujo meio é `1 − meio_up`; num
        # mercado a 0,99 esse meio é 0,01, e recuar UM tick o põe em zero.
        # `Cotacao.preco` não tem piso, e os pools de reward incluem mercados
        # a 0,01–0,02 ("Will X announce bankruptcy by 2029?").
        #
        # Por que renomear em vez de consertar o preço: consertar seria mover
        # a cotação, e mover a cotação é mudar a estratégia. Estas já eram
        # recusadas; o que muda é que a recusa para de vestir o nome de um bug
        # nosso. `ordem_mal_formada` volta a significar o que diz — e
        # `shares <= 0` continua indo para ele, porque AQUELE seria defeito
        # nosso de verdade.
        if not all(_cabe_no_livro(ordem) for ordem in pernas):
            return "sem_espaco_para_recuar"
        for ordem in pernas:
            decisao = self.portao.avaliar_risco(
                ordem,
                feeds_saudaveis=feeds_saudaveis,
                melhor_bid=livro.best_bid,
                melhor_ask=livro.best_ask,
            )
            if not decisao.pode:
                return decisao.motivo or "recusado_sem_motivo"
        return None

    async def _executar(
        self,
        decisao: Decisao,
        janela: JanelaAoVivo,
        *,
        meio: float,
        agora_epoch: float,
        livro: OrderBook | None = None,
        livro_down: OrderBook | None = None,
        ancora: AncoraDoMicroprice | None = None,
    ) -> Efeito:
        anterior = self.abertas.get(janela.slug)
        efeito = await aplicar_decisao(
            decisao,
            anterior,
            cliente=self.cliente,
            ordem_da_cotacao=self._ordem_da_cotacao(janela, meio=meio, ancora=ancora),
            ordem_do_lado_down=self._ordem_da_cotacao(
                janela, meio=meio, lado_up=False, ancora=ancora
            ),
            janela=janela.slug,
            agora_epoch=agora_epoch,
        )
        self._guardar(janela.slug, efeito)
        aberta = self.abertas.get(janela.slug)
        if aberta is not None:
            # A referência do recolher nasce AQUI, do livro que colocou a
            # cotação — não na primeira observação do sono, que perderia um
            # mercado que andou dentro do primeiro segundo (revisão do Codex,
            # #126). Cada perna lê o SEU livro: o do Down não é o espelho do
            # Up, e uma referência sintética (1 − ask do Up) contra o livro
            # real do Down recolheria pares parados. Perna sem livro agora
            # fica sem referência, e o poll a marca na primeira observação.
            # Em LIVE o livro que colocou a cotação NOVA ainda pode ter a
            # ANTERIOR (recotação): o melhor bid externo desconta o nosso
            # nível, senão a referência seria a nossa própria ordem.
            chave = (janela.slug, int(aberta.desde_epoch * 1e6))
            if chave not in self._referencia_do_recolher:
                excluir = self.nossa_ordem_esta_no_livro and anterior is not None
                self._referencia_do_recolher[chave] = (
                    _referencia(
                        livro,
                        aberta.preco_up,
                        excluir=(anterior.preco_up, anterior.cotacao.tamanho)
                        if excluir
                        else None,
                    ),
                    _referencia(
                        livro_down,
                        aberta.preco_down,
                        excluir=(anterior.preco_down, anterior.cotacao.tamanho)
                        if excluir
                        else None,
                    ),
                )
        return efeito

    async def recolher_se_o_livro_andou(self, livro_de, *, agora_ns: int) -> list[Efeito]:
        """Entre passadas: tira do livro a cotação que o mercado deixou exposta.

        Quando o melhor bid cai ABAIXO de onde estava quando entramos (e
        abaixo do nosso preço), o fluxo que vem a seguir nos executa
        primeiro — e é seleção adversa quase pura: o `maker_de_pares` mediu
        −583,54 USDC em 4 h ficando, contra −31,87 recolhendo com 100 ms.
        Aqui a latência é a do sono de 1 s do processo, e o que se mede em
        SHADOW é se 1 s basta.

        Três regras, cada uma por um motivo:
        - **livro indisponível NÃO recolhe** — sair por falta de dado nosso
          perde a fila de graça (mesma regra do `_passo_da_janela`);
        - **lado de bids VAZIO recolhe** — é o caso mais forte de o mercado
          ter ido embora, e é o que a simulação medida faz;
        - **antes de sair, confere os prints do intervalo** — senão a saída
          apaga o cursor da caixa e uma execução entre a passada e o
          recolher some, subcontando justamente a métrica que esta regra
          quer melhorar.
        Basta uma perna para sair, porque as pernas entram e saem juntas.
        """
        if not self.recolhe_quando_o_livro_anda:
            return []
        efeitos: list[Efeito] = []
        for slug, aberta in list(self.abertas.items()):
            tokens = self._tokens.get(slug)
            if tokens is None:
                continue
            livros = [livro_de(token_id, agora_ns=agora_ns) for token_id in tokens]
            if self._o_mercado_andou_contra(slug, aberta, tokens, livros):
                efeitos.append(
                    await self._recolher(
                        slug, aberta, tokens, livros,
                        livro_de=livro_de, agora_ns=agora_ns,
                        motivo="livro_andou_contra",
                    )
                )
        return efeitos

    def _o_mercado_andou_contra(
        self,
        slug: str,
        aberta: CotacaoAberta,
        tokens: tuple[str, str],
        livros: list[OrderBook | None],
    ) -> bool:
        """Alguma perna desta cotação ficou exposta? Basta UMA, porque as
        pernas entram e saem juntas.

        Tem um efeito colateral declarado: a perna cujo livro não estava à mão
        na colocação ganha a referência AQUI, do livro dela — e nessa passada
        ela não decide, porque comparar o livro consigo mesmo nunca acusa
        movimento nenhum.
        """
        chave = (slug, int(aberta.desde_epoch * 1e6))
        referencias = list(self._referencia_do_recolher.get(chave, (None, None)))
        pernas = ((0, aberta.preco_up), (1, aberta.preco_down))
        for i, preco in pernas:
            livro = livros[i]
            if preco <= 0.0 or livro is None:
                continue
            if referencias[i] is None:
                # Em LIVE a nossa ordem já está no livro: sai da conta, senão
                # a referência seria ela mesma.
                referencias[i] = _referencia(
                    livro,
                    preco,
                    excluir=(preco, aberta.cotacao.tamanho)
                    if self.nossa_ordem_esta_no_livro
                    else None,
                )
                self._referencia_do_recolher[chave] = (referencias[0], referencias[1])
                continue
            if _o_livro_andou_contra(
                livro,
                referencias[i],
                preco_nosso=preco,
                tamanho=aberta.cotacao.tamanho,
                nossa_ordem_no_livro=self.nossa_ordem_esta_no_livro,
            ):
                return True
        return False

    async def _recolher(
        self,
        slug: str,
        aberta: CotacaoAberta,
        tokens: tuple[str, str],
        livros: list[OrderBook | None],
        *,
        livro_de,
        agora_ns: int,
        motivo: str,
    ) -> Efeito:
        """Tira a cotação do livro — e fecha a conta dela ANTES de sair.

        A ordem das três coisas importa, e cada uma custou uma revisão:

        1. **os prints do intervalo**, senão `_sair` apaga o cursor da caixa e
           a execução que aconteceu entre a passada e o recolher some,
           subcontando justamente a métrica que esta regra quer melhorar;
        2. **o último intervalo de reward**, senão uma cotação que repousou
           14 s e foi recolhida contribuiria zero — o experimento compara
           reward com execução, e recolher muito empurraria o reward para
           baixo por construção. A caixa conta no livro do Up; se foi o Down
           que disparou e o do Up não está à mão, o do Down serve pelo mesmo
           espelho que a colocação e o portão usam (bid = 1 − ask), e sem
           nenhum dos dois não se chega aqui;
        3. **sair**, com o motivo nomeado — `motivo` porque as DUAS regras
           que tiram a cotação entre passadas (o livro andando contra e a
           pausa por fill tóxico) precisam desta mesma ordem, e ter duas
           cópias dela é como as duas divergem.

        Sem nenhum dos dois livros à mão a conta não fecha — e ainda assim a
        cotação SAI: a pausa por fill tóxico não depende de livro, porque
        cancelar não depende (revisão do Codex, #131).
        """
        if self._negocios_desde is not None:
            self.caixa.conferir_prints(
                slug,
                aberta,
                token_up=tokens[0],
                token_down=tokens[1],
                negocios_desde=self._negocios_desde,
                agora_ns=agora_ns,
                livro_de=livro_de,
                params=self._params.get(slug),
            )
        params = self._params.get(slug)
        livro_up = livros[0]
        if livro_up is None and livros[1] is not None:
            livro_up = espelho_do_livro(livros[1], asset_id=tokens[0])
        if params is not None and livro_up is not None:
            self.caixa.acertar(
                slug, aberta, livro_up, params, agora_epoch=agora_ns / 1e9
            )
        return await self._sair(slug, motivo=motivo)

    async def _recusar(self, slug: str, motivo: str) -> Efeito | None:
        """Não cotar por `motivo` — e tirar do livro o que já repousava.

        Conta UMA vez: `_sair` já conta o motivo com que sai, e contar antes
        dele punha o mesmo motivo duas vezes no relato, justamente quando a
        regra faz o que mais importa — tirar uma cotação que já estava lá.
        Era assim no portão e no `perna_down_sem_bids`.
        """
        if slug in self.abertas:
            return await self._sair(slug, motivo=motivo)
        self._contar(motivo)
        return None

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
        anterior = self.abertas.get(slug)
        if efeito.aberta is None:
            self.abertas.pop(slug, None)
            self.caixa.esquecer(slug)
        else:
            self.abertas[slug] = efeito.aberta
        # A referência do recolher é da cotação, não da janela: cotação que
        # saiu ou foi trocada (novo `desde_epoch`) leva a sua embora. Só o
        # caminho do recolher a apagava, e cada `janela_fechou`, `pool_sumiu`
        # ou recotação deixava uma chave morta para sempre (revisão do
        # Codex, #126).
        if anterior is not None and (
            efeito.aberta is None or efeito.aberta.desde_epoch != anterior.desde_epoch
        ):
            self._referencia_do_recolher.pop(
                (slug, int(anterior.desde_epoch * 1e6)), None
            )
        if efeito.aberta is None:
            self._tokens.pop(slug, None)
            self._params.pop(slug, None)
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

    def _ordem_da_cotacao(
        self,
        janela: JanelaAoVivo,
        *,
        meio: float,
        lado_up: bool = True,
        ancora: AncoraDoMicroprice | None = None,
    ) -> OrdemDaCotacao:
        """Como esta janela vira ordem — uma perna. Fecha sobre a janela e
        sobre o meio DO LIVRO (do Up) desta passada: o preço depende do tick
        dela e de onde o mercado está agora — não de 0,5. Ver o cabeçalho.

        As duas pernas são BIDS: a do Up no livro do Up, a do Down no livro
        do Down, cujo meio é `1 − meio`. É assim que se fica dos dois lados
        sem vender o que não se tem — o ask do Up é o bid do Down.
        """

        # A perna Down é um BID no livro DELA: a âncora vai espelhada, e o
        # espelho é o mesmo que o `meio` usa — é isso que faz o preço avaliado
        # por `estimar_retorno` (que vê a perna Down como ask do Up) e o preço
        # ENVIADO serem o mesmo número.
        ancora_da_perna = (
            ancora
            if ancora is None or lado_up
            else ancora.no_livro_do_down()
        )

        def montar(cotacao: Cotacao) -> OrdemPretendida:
            preco = cotacao.preco(
                meio=meio if lado_up else 1.0 - meio,
                tick_size=janela.tick_size,
                do_lado_bid=True,
                ancora=ancora_da_perna,
            )
            return OrdemPretendida(
                slug=janela.slug,
                token_id=janela.token_up if lado_up else janela.token_down,
                lado_up=lado_up,
                shares=cotacao.tamanho,
                preco_limite=preco,
            )

        return montar

    def _contar(self, motivo: str) -> None:
        self.motivos[motivo] = self.motivos.get(motivo, 0) + 1

    def _registrar_fracao_admitida(self, fracao: float | None) -> None:
        """A fração de uma candidata que o teto ADMITIU numa AVALIAÇÃO da grade
        — não uma ordem. É a fração COMO O TETO A VIU (pós-cancelamento em
        LIVE), não a do `RetornoEstimado`, que em LIVE subestima. Conta toda
        passada com candidata escolhível, inclusive as que viram MANTER e as
        que o portão ainda barra: prova que o teto nunca admite fração acima
        dele. Para ordens de fato colocadas, ver `_registrar_ordem_efetiva`."""
        if fracao is None:
            return
        self._fracao_admitida_soma += fracao
        self._fracao_admitida_n += 1
        self._fracao_admitida_max = max(self._fracao_admitida_max, fracao)

    def _registrar_ordem_efetiva(
        self, fracao: float | None, efeito: Efeito
    ) -> None:
        """A fração da cotação EFETIVAMENTE colocada/reposicionada — só depois
        da ação, e só quando houve ação no livro. `fracao` é a que o teto viu
        (pós-cancelamento em LIVE), não a do livro que ainda incluía a ordem
        velha.

        Uma passada de MANTER não chega aqui (o laço retorna antes do I/O), e
        uma recusa do portão vira `_recusar`, não `_executar` — então nem o
        MANTER repetido nem a candidata barrada inflam este contador, que é o
        defeito que ele existe para não ter. `fracao is None` cobre a saída sem
        candidata (`atual_nao_pontua_mais` sem melhor): ali não há fração de
        ordem nova a registrar."""
        if fracao is None:
            return
        if efeito.resultado not in (
            ResultadoDaAcao.COLOCADA,
            ResultadoDaAcao.REPOSICIONADA,
        ):
            return
        self._fracao_ordem_soma += fracao
        self._fracao_ordem_n += 1
        self._fracao_ordem_max = max(self._fracao_ordem_max, fracao)

    def _diagnosticar_up_ausente(self) -> None:
        """O livro do Up faltou — o caso OPERACIONAL (`livro_indisponivel`, não
        cota, não cancela). Aqui só se torna a causa observável, separando o
        que dá:

        - conexão dos pools caída (`conexao_de_pools_ok()` = `False`) →
          `sem_livro_por_conexao_de_pools`: nenhum token de pool tem livro
          porque o cano fechou;
        - conexão viva → `sem_livro_por_token`: o cano está aberto e ESTE token
          emudeceu — mercado parado, não feed morto;
        - sem sinal da conexão (rota de pools desligada, ou o callback devolveu
          `None`/levantou) → `sem_diagnostico`, porque afirmar a causa sem base
          seria o defeito de `cobertura_da_gravacao` que o M2 já pagou.

        NÃO afrouxa silêncio, portão nem heartbeat: a decisão de não cotar é a
        mesma; só a leitura de 60 s passa a dizer POR QUE não havia livro.
        """
        self._contar_cobertura("livro_up_ausente")
        estado = self._conexao_de_pools_esta_ok()
        if estado is None:
            self._contar_cobertura("sem_diagnostico")
        elif estado:
            self._contar_cobertura("sem_livro_por_token")
        else:
            self._contar_cobertura("sem_livro_por_conexao_de_pools")

    def _diagnosticar_cobertura(self, *, down_disponivel: bool) -> None:
        """Com o livro do Up já em mãos: as DUAS pernas chegaram, ou só o Up?

        Um maker de dois lados precisa dos dois livros; o Up presente não prova
        o Down presente, e marcar 'disponível' logo após o Up esconderia uma
        perna Down sem livro. Só diagnóstico — a regra não muda: Down ausente
        não impede cotar (o score sai do Up) nem cancela; a âncora do
        microprice trata a falta por `sem_microprice`, à parte."""
        if down_disponivel:
            self._contar_cobertura("ambos_disponiveis")
        else:
            self._contar_cobertura("livro_down_ausente")

    def _conexao_de_pools_esta_ok(self) -> bool | None:
        """A conexão dos pools está de pé? `None` quando não há como saber —
        sem callback, callback que devolve `None` (rota de pools desligada), ou
        callback que levantou. `None` NÃO é `False`: um `bool(None)` viraria
        'conexão caída' e atribuiria a causa errada, então o resultado do
        callback passa cru, sem coerção."""
        if self.conexao_de_pools_ok is None:
            return None
        try:
            return self.conexao_de_pools_ok()
        except Exception:
            # O diagnóstico nunca derruba o laço: uma observação que derrubasse
            # a rota seria pior que a ausência dela.
            return None

    def _contar_cobertura(self, chave: str) -> None:
        self.cobertura_dos_pools[chave] = self.cobertura_dos_pools.get(chave, 0) + 1

    async def reconciliar_no_arranque(
        self, *, cancelar_orfas_achadas: bool = True
    ) -> Reconciliacao:
        """Casa o que ACHÁVAMOS repousar com o que o servidor diz repousar.

        Existia desde o 3.5 como função (`execucao_maker.reconciliar`) e como
        contrato em três lugares — "quem recebe INCERTA não reenvia,
        reconcilia" — e **ninguém a chamava** (auditoria 2026-09-17, §2.3). Um
        processo que morre entre o envio e a resposta deixa ordem no livro que
        ninguém gerencia; reiniciar perde a memória em RAM. Ler o servidor é a
        única fonte que resolve isso, e por isso roda no ARRANQUE, antes de
        qualquer cotação nova.

        - **Órfã** (o servidor lista, nós não esperávamos): cancelada por
          default — é exposição que nenhum portão desta sessão autorizou. Um
          cancelamento `INCERTA` fica no relato: recancelar é seguro (§4.4).
        - **Fantasma** (esperávamos, o servidor não lista): preencheu ou já
          foi cancelada; o registro é largado quando TODAS as pernas sumiram.
          Uma perna só sumida é estado desconhecido e fica para o `passo`.
        - **Leitura que falha SOBE** (`ErroDeLeitura`): declarar o livro limpo
          sem ter olhado é o pior desfecho possível aqui.

        Em SHADOW o cliente é o sombra e a leitura vem do diário — o caminho é
        exercitado a cada arranque, sem rede. Em LIVE é o `GET /data/orders`.
        """
        esperadas = {
            order_id: aberta
            for aberta in self.abertas.values()
            for order_id in aberta.order_ids
        }
        rec = await reconciliar(self.cliente, esperadas)
        fantasmas = set(rec.fantasmas)
        largadas = [
            slug
            for slug, aberta in self.abertas.items()
            if aberta.order_ids and all(oid in fantasmas for oid in aberta.order_ids)
        ]
        for slug in largadas:
            self.abertas.pop(slug)
        desfechos: dict[str, str] = {}
        if cancelar_orfas_achadas and rec.orfas:
            desfechos = await cancelar_orfas(self.cliente, rec)
        self.ultima_reconciliacao = {
            "casadas": len(rec.casadas),
            "orfas": len(rec.orfas),
            "fantasmas": len(rec.fantasmas),
            "registros_largados": largadas,
            "cancelamentos": desfechos,
        }
        registrar = log.info if rec.limpa else log.warning
        registrar("reconciliacao do maker no arranque", **self.ultima_reconciliacao)
        return rec

    @staticmethod
    def _stats_de_fracao(soma: float, n: int, maxima: float) -> dict[str, Any]:
        """Média/máxima/contagem de uma fração acumulada. `None` sem amostra —
        zero afirmaria fatia nula, e o que houve foi ausência de medida."""
        return {
            "media": round(soma / n, 4) if n > 0 else None,
            "maxima": round(maxima, 4) if n > 0 else None,
            "n": n,
        }

    def _resumo_do_teto(self) -> dict[str, Any]:
        """A história do teto de fração no relato de 60 s: o teto em vigor, as
        recusas que ele produziu, e DUAS frações que não são a mesma coisa —

        - `avaliacoes_aceitas_pelo_teto`: por AVALIAÇÃO da grade, inclui MANTER
          e candidatas que o portão ainda barra. Prova que o teto nunca admite
          fração acima dele (a máxima fica <= teto).
        - `ordens_efetivas`: só as cotações COLOCADAS/REPOSICIONADAS. É a
          "fração das cotações efetivamente aceitas" — MANTER repetido não a
          infla.
        """
        return {
            "teto": self.fracao_maxima_do_pool,
            "recusas_por_teto": self.motivos.get("fracao_do_pool_acima_do_teto", 0),
            "avaliacoes_aceitas_pelo_teto": self._stats_de_fracao(
                self._fracao_admitida_soma,
                self._fracao_admitida_n,
                self._fracao_admitida_max,
            ),
            "ordens_efetivas": self._stats_de_fracao(
                self._fracao_ordem_soma, self._fracao_ordem_n, self._fracao_ordem_max
            ),
            "nota": (
                "`teto` None e a rota sem trava (linha de base). "
                "`recusas_por_teto` conta as passadas em que TODA candidata que "
                "pontua excedia o teto (motivo `fracao_do_pool_acima_do_teto`). "
                "`avaliacoes_aceitas_pelo_teto` e por AVALIACAO (inclui MANTER e "
                "candidata que o portao ainda barra) — a maxima prova que o teto "
                "nao admite fracao acima dele. `ordens_efetivas` e so o que foi "
                "COLOCADO/REPOSICIONADO — MANTER repetido nao a infla."
            ),
        }

    def _resumo_da_cobertura(self) -> dict[str, Any]:
        """A cobertura dos livros de pool no relato de 60 s — diagnóstico, não
        regra.

        Três estados de disponibilidade por passada avaliada:
        `ambos_disponiveis`, `livro_up_ausente` (o caso operacional que não
        cota — bate com `motivos['livro_indisponivel']`), e `livro_down_ausente`
        (o Up chegou, a perna Down não). E, quando o Up falta, a causa se
        reparte em conexão de pools caída × token mudo, com `sem_diagnostico`
        para o que não deu para atribuir. NADA aqui muda a regra: Up ausente já
        não cotava; Down ausente segue cotando (o score sai do Up)."""
        c = self.cobertura_dos_pools
        return {
            "ambos_disponiveis": c.get("ambos_disponiveis", 0),
            "livro_up_ausente": c.get("livro_up_ausente", 0),
            "livro_down_ausente": c.get("livro_down_ausente", 0),
            "sem_livro_total": self.motivos.get("livro_indisponivel", 0),
            "sem_livro_por_conexao_de_pools": c.get("sem_livro_por_conexao_de_pools", 0),
            "sem_livro_por_token": c.get("sem_livro_por_token", 0),
            "sem_diagnostico": c.get("sem_diagnostico", 0),
            "nota": (
                "Disponibilidade por passada: `ambos_disponiveis`, "
                "`livro_up_ausente` (o Up faltou — NAO cota, e bate com "
                "`motivos['livro_indisponivel']`) e `livro_down_ausente` (o Up "
                "veio, a perna Down nao — ainda cota, o score sai do Up). Quando "
                "o Up falta, a causa se separa em conexao de pools caida x token "
                "mudo (`sem_diagnostico` = sem sinal da conexao). So torna a "
                "causa visivel; nao afrouxa silencio, portao nem heartbeat."
            ),
        }

    def resumo(self) -> dict[str, Any]:
        """O que sai no relato de 60 s do SHADOW."""
        return {
            "cotacoes_repousando": len(self.abertas),
            "por_janela": sorted(self.abertas),
            # As regras EXPERIMENTAIS ligadas nesta rodada, no relato de 60 s.
            # As duas juntas não se distinguem — uma muda QUANDO a ordem sai, a
            # outra muda o PREÇO dela —, e uma rodada de 14 dias confundida não
            # mede nenhuma das duas. Sai aqui para a confusão se denunciar na
            # primeira hora, e não no fim.
            "regras": {
                "recolhe_quando_o_livro_anda": self.recolhe_quando_o_livro_anda,
                "ticks_abaixo_do_microprice": self.ticks_abaixo_do_microprice,
                "pausa_apos_fill_toxico_s": self.pausa_apos_fill_toxico_s,
                "fracao_maxima_do_pool": self.fracao_maxima_do_pool,
            },
            "teto_de_fracao_do_pool": self._resumo_do_teto(),
            "cobertura_dos_pools": self._resumo_da_cobertura(),
            "motivos": dict(sorted(self.motivos.items())),
            "caixa": self.caixa.resumo(),
            "reconciliacao_no_arranque": self.ultima_reconciliacao,
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


#: Tolerância de preço na comparação com o livro (o CLOB manda decimal como
#: string; o nosso preço vem da grade do tick).
_EPS_PRECO = 1e-9


def _referencia(
    livro: OrderBook | None,
    preco: float,
    *,
    excluir: tuple[float, float] | None = None,
) -> float | None:
    """De onde o mercado pode cair: o nosso preço, ou o melhor bid EXTERNO de
    agora se ele já está abaixo (cotação que melhora o topo). `excluir` é a
    nossa ordem que está neste livro (LIVE), `(preço, tamanho)`, e sai da
    conta. Sem livro não há referência — `None`, e quem chama marca depois."""
    if livro is None:
        return None
    bids = livro.bids
    if excluir is not None:
        bids = _sem_o_nosso_nivel(bids, excluir[0], excluir[1])
    if not bids:
        return preco
    return min(preco, bids[0][0])


def _sem_o_nosso_nivel(
    niveis: list[tuple[float, float]], preco_nosso: float, tamanho: float
) -> list[tuple[float, float]]:
    """Os níveis do livro sem a NOSSA ordem: no nosso preço desconta o nosso
    tamanho, e o nível some se não sobra ninguém nele."""
    externos: list[tuple[float, float]] = []
    for preco, quantidade in niveis:
        if abs(preco - preco_nosso) <= _EPS_PRECO:
            quantidade -= tamanho
            if quantidade <= _EPS_PRECO:
                continue
        externos.append((preco, quantidade))
    return externos


def _o_livro_andou_contra(
    livro: OrderBook,
    referencia: float,
    *,
    preco_nosso: float,
    tamanho: float,
    nossa_ordem_no_livro: bool,
) -> bool:
    """O melhor bid do MERCADO caiu abaixo da referência?

    Lado de bids vazio é o caso mais forte de "caiu" — o mercado foi embora.
    Sem a nossa ordem no livro (SHADOW), o melhor bid do livro É o do
    mercado. Com ela (LIVE), o melhor bid do livro pode ser a NOSSA ordem —
    e quando a cotação melhora o topo (livro largo: os pools) ela é o topo
    desde que nasce, e comparar o topo com a referência nunca dispararia
    (revisão do Codex, #126). O que se compara é o melhor bid EXTERNO: o
    livro sem o nosso nível. "Sozinho no topo" é o caso particular em que
    ele fica abaixo do nosso preço.
    """
    bids = livro.bids
    if nossa_ordem_no_livro:
        bids = _sem_o_nosso_nivel(bids, preco_nosso, tamanho)
    if not bids:
        return True
    return bids[0][0] < referencia - _EPS_PRECO
