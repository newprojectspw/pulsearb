"""Agregação dos resultados do backtest.

Tudo aqui é descritivo: nenhuma métrica é "ajustada", nenhum trade é
descartado por ser inconveniente. Se o número for feio, o relatório mostra o
número feio — é para isso que o M2 existe.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Trade:
    """Um trade simulado, com tudo que foi descontado explícito."""

    slug: str
    jogo: str            # "twap" | "horario"
    asset: str
    duracao_s: int
    bucket_tempo: str
    prob_prevista: float
    preco_pago: float    # médio, já com slippage do book real
    shares: float
    custo_usdc: float
    fee_usdc: float
    latencia_ms: float
    resolveu_up: bool
    lado_up: bool        # comprou Up?

    @property
    def acertou(self) -> bool:
        return self.lado_up == self.resolveu_up

    @property
    def payout_usdc(self) -> float:
        """Share vencedora paga 1.00; perdedora paga 0."""
        return self.shares if self.acertou else 0.0

    @property
    def pnl_usdc(self) -> float:
        return self.payout_usdc - self.custo_usdc - self.fee_usdc


@dataclass(frozen=True, slots=True)
class SinalDirecional:
    """Um instante em que o gatilho disparou — **preenchido ou não**.

    A diferença para `Trade` é a única coisa que este tipo existe para ter: o
    `Trade` só nasce quando a ordem preenche, e por isso a coorte dele MUDA
    quando muda a latência, o book ou o teto de entradas. Um `hit_rate` sobre
    ela não separa "o sinal não tem direção" de "os cenários operaram trades
    diferentes" — que é a ressalva registrada na §2d-ter do VEREDITO_M2.

    Aqui a coorte é fixa por construção: o sinal é calculado em `t` e não
    depende de nada a jusante. É o que permite perguntar *há informação
    direcional?* sem que a resposta carregue a execução junto.
    """

    slug: str
    bucket_tempo: str
    lado_up: bool
    prob: float
    resolveu_up: bool
    #: `ask` do lado apostado e o do lado oposto, no instante do sinal.
    #:
    #: Existem para responder "e se a regra comprasse o outro lado?" **sem
    #: mexer na execução**. Inverter a regra de verdade exigiria mudar o
    #: caminho que decide e arriscar um bug no motor de medição; guardar os
    #: dois preços permite a mesma conta como observação. `None` quando o
    #: livro do oposto não existia no instante — que é informação, não zero.
    preco_do_lado: float | None = None
    preco_do_oposto: float | None = None

    @property
    def acertou(self) -> bool:
        return self.lado_up == self.resolveu_up

    def pnl_por_share(self, *, invertido: bool = False) -> float | None:
        """PnL por share desta aposta, ou da oposta. Sem taxa.

        A share vencedora paga 1,00 — a mesma conta de `Trade.pnl_usdc`, sem
        o `fee` porque aqui o que se compara é a DIREÇÃO, e a taxa incide
        igual nos dois lados.
        """
        preco = self.preco_do_oposto if invertido else self.preco_do_lado
        if preco is None:
            return None
        acertou = (not self.acertou) if invertido else self.acertou
        return (1.0 if acertou else 0.0) - preco


def _p_valor_binomial(acertos: int, n: int) -> float:
    """P-valor bilateral contra a hipótese de moeda honesta (p = 0,5).

    Aproximação normal com correção de continuidade. Não usa `scipy` de
    propósito: a dependência não existe no projeto, e para `n` desta ordem a
    aproximação e o exato concordam nas casas que importam para a decisão.

    Sem o p-valor o número não decide nada. `hit_rate` 0,52 em 50 sinais e
    0,52 em 50 mil sinais são a mesma fração e conclusões opostas, e é
    exatamente esse engano que um "melhor que 0,5" sem amostra produz.
    """
    if n <= 0:
        return float("nan")
    desvio = math.sqrt(0.25 * n)
    if desvio == 0:  # pragma: no cover - n > 0 garante desvio > 0
        return float("nan")
    # A correção de 0,5 evita p-valor otimista demais em amostra pequena.
    excesso = abs(acertos - 0.5 * n) - 0.5
    if excesso <= 0:
        return 1.0
    return math.erfc(excesso / desvio / math.sqrt(2.0))


#: Largura das faixas da curva de confiabilidade. 0,05 dá 20 faixas entre 0
#: e 1 — fino o bastante para separar um modelo calibrado de um constante, e
#: grosso o bastante para cada faixa ter amostra.
LARGURA_DA_FAIXA = 0.05

#: Faixas ocupadas abaixo disto = o preditor não varia o bastante para a
#: curva de confiabilidade significar alguma coisa. Três é o mínimo que
#: distingue "varia" de "cospe uma constante com ruído de arredondamento".
MINIMO_DE_FAIXAS = 3


#: Compensa o erro de representação binária na divisão. `0.60 / 0.05` dá
#: **11,999999999999998**, não 12 — então `int()` truncava para 11 e o valor
#: 0,60 caía no rótulo `0.55-0.60`, que mente sobre o próprio conteúdo. O
#: mesmo acontecia com 0,70 e 0,95; com 0,55, 0,65, 0,75, 0,80, 0,85 e 0,90,
#: não. Ou seja: o defeito atingia ALGUMAS bordas, o que é pior que atingir
#: todas — o padrão não aparece numa conferência rápida.
#:
#: 1e-9 é folgado para o erro real (da ordem de 1e-15) e pequeno demais para
#: puxar um valor legítimo da faixa vizinha: 0,5999999 continua em `0.55-0.60`.
_EPSILON_DA_FAIXA = 1e-9


def faixa_de_probabilidade(prob: float) -> str:
    """Rótulo da faixa de `prob`, no formato `0.60-0.65`.

    O piso é INCLUSIVO e o teto exclusivo: 0,60 pertence a `0.60-0.65`. Sem o
    epsilon isso era falso justamente nos valores redondos, que são os que
    alguém escreve num teste ou confere à mão.
    """
    prob = min(max(prob, 0.0), 1.0)
    bruto = int(prob / LARGURA_DA_FAIXA + _EPSILON_DA_FAIXA)
    indice = min(bruto, int(1 / LARGURA_DA_FAIXA) - 1)
    piso = indice * LARGURA_DA_FAIXA
    return f"{piso:.2f}-{piso + LARGURA_DA_FAIXA:.2f}"


@dataclass
class ContagemDeProbabilidade:
    """Previsões acumuladas e o que aconteceu com elas.

    Base comum do balde de tempo e da faixa de probabilidade. As duas
    acumulam a MESMA coisa — quantas previsões, a soma delas, e quantas
    resolveram Up — e diferem só em como o dado é FATIADO: o balde por
    tempo restante, a faixa por probabilidade prevista. Manter as três
    contas em um lugar só evita que uma seja consertada e a outra não.
    """

    n: int = 0
    soma_prob: float = 0.0
    acertos_up: int = 0

    @property
    def prob_media_prevista(self) -> float:
        return self.soma_prob / self.n if self.n else float("nan")

    @property
    def freq_realizada(self) -> float:
        """Fração que resolveu Up. NO BALDE isto é a taxa-base, não acurácia.

        Na FAIXA é outra coisa: ali as previsões são todas parecidas entre
        si, então comparar com `prob_media_prevista` mede calibração de
        verdade. Mesma conta, significados diferentes — é a razão de a
        curva de confiabilidade existir.
        """
        return self.acertos_up / self.n if self.n else float("nan")

    @property
    def erro(self) -> float:
        """Previsto − realizado. Positivo = otimista demais."""
        return self.prob_media_prevista - self.freq_realizada

    def somar(self, prob_up: float, resolveu_up: bool) -> None:
        self.n += 1
        self.soma_prob += prob_up
        if resolveu_up:
            self.acertos_up += 1


@dataclass
class FaixaDeConfiabilidade(ContagemDeProbabilidade):
    """Uma faixa de probabilidade PREVISTA e o que aconteceu nela."""

    faixa: str = ""


@dataclass
class CalibrationBucket(ContagemDeProbabilidade):
    bucket: str = ""
    faixas: dict[str, FaixaDeConfiabilidade] = field(default_factory=dict)

    @property
    def erro_calibracao(self) -> float:
        """Previsto − realizado, AGREGADO. NÃO é medida de calibração.

        Aqui `freq_realizada` é a taxa-base do balde, não a acurácia por
        faixa. Um preditor que cospe uma constante igual à taxa-base tira
        zero neste número sem saber nada — foi o que a rodada de 20 h
        expôs: no balde `<30s`, previsto 0,514 contra realizado 0,5073,
        cara-ou-coroa dos dois lados, e o critério 1.3 do VEREDITO_M2
        "passou" com 0,0067.

        Fica no relatório porque é barato e diz se o modelo tem viés de
        nível. Quem decide calibração é `erro_de_confiabilidade`.
        """
        return self.erro

    @property
    def erro_de_confiabilidade(self) -> float:
        """ECE: média dos |previsto − realizado| por faixa, ponderada por n.

        SOZINHO ELE NÃO BASTA, e isso foi medido, não suposto. Contra três
        preditores sintéticos de 20 mil observações:

        | preditor | `erro` | ECE | `faixas_ocupadas` |
        |---|---|---|---|
        | constante 0,51 num mundo 50/50 | +0,0051 | **0,0051** | **1** |
        | bem calibrado | −0,0007 | 0,0070 | 18 |
        | otimista em 15 pontos | +0,1487 | **0,1487** | 15 |

        O constante passa no ECE também: ele cai todo numa faixa só, e
        dentro dela previsto e realizado quase coincidem. O que o ECE
        acrescenta é separar o otimista sistemático do bem calibrado — o
        número antigo já fazia isso, mas por sorte, porque ali o viés de
        nível e o erro por faixa coincidem.

        Quem denuncia o preditor constante é `faixas_ocupadas`. Por isso
        o critério é a CONJUNÇÃO: ver `calibracao_avaliavel`.
        """
        vivas = [f for f in self.faixas.values() if f.n]
        total = sum(f.n for f in vivas)
        if not total:
            return float("nan")
        return sum(abs(f.erro) * f.n for f in vivas) / total

    @property
    def faixas_ocupadas(self) -> int:
        """Quantas faixas têm amostra. 1 = preditor efetivamente constante."""
        return sum(1 for f in self.faixas.values() if f.n)

    @property
    def calibracao_avaliavel(self) -> bool:
        """O balde tem variação suficiente para o ECE querer dizer algo?

        Sem espalhamento na probabilidade prevista não há curva para medir,
        e um ECE baixo é artefato de construção. Falso aqui significa
        **critério 1.3 não avaliado** — que é diferente de reprovado.
        """
        return self.faixas_ocupadas >= MINIMO_DE_FAIXAS


@dataclass
class BacktestReport:
    """Coletor. Alimente com `add_trade`/`add_calibration`, depois `to_dict`."""

    trades: list[Trade] = field(default_factory=list)
    calibracao: dict[str, CalibrationBucket] = field(default_factory=dict)
    sinais_gerados: int = 0
    sinais_sem_book: int = 0
    sinais_abaixo_do_minimo: int = 0
    sinais_nao_preenchiveis: int = 0
    janelas_avaliadas: int = 0
    #: bucket → instantes em que ALGUM lado passou do threshold, contados
    #: mesmo depois de a janela já ter operado. É a medição que responde ao
    #: BUG 2 do M2.6: `por_bucket_tempo` mostra onde se OPEROU, e como a v1
    #: entra uma vez por janela e varre o stream da abertura para o
    #: fechamento, ela opera onde chega primeiro — não onde o sinal é melhor.
    #: Comparar os dois separa "o sinal não existe aqui" de "o simulador
    #: nunca chegou aqui".
    oportunidades_por_bucket: Counter[str] = field(default_factory=Counter)
    #: bucket → janelas distintas com ao menos uma oportunidade
    janelas_com_oportunidade: dict[str, set[str]] = field(
        default_factory=lambda: defaultdict(set)
    )
    #: ativo → janelas puladas por não haver curva de variância medida para
    #: ele. Contado e publicado em vez de cair no modelo derivado: as duas
    #: físicas no mesmo relatório seriam duas populações no mesmo número, que
    #: é a forma exata do defeito que a §2d-bis achou no 1.4.
    janelas_sem_curva: Counter[str] = field(default_factory=Counter)
    #: ativo → janelas puladas por serem de um JOGO que a curva não cobre.
    #:
    #: A V(t) medida é da série `twap_sixty`, e descreve a liquidação do jogo
    #: TWAP. A janela horária resolve pelo candle da Binance contra o preço de
    #: abertura — outro observável. Deixá-la cair no `prob_up_hourly` numa
    #: rodada marcada `medida: true` poria as duas físicas no mesmo PnL de
    #: manchete, que é o defeito que esta rodada existe para não cometer.
    janelas_de_jogo_sem_curva: Counter[str] = field(default_factory=Counter)
    #: ativo → INSTANTES pulados por pedirem horizonte além do maior medido
    #: na curva. Por instante, e não por janela: uma janela de 15 min ou de
    #: 4 h é operada só nos últimos 240 s, que a curva de 600 s cobre —
    #: descartá-la inteira jogaria fora a população que a estratégia usa, e o
    #: motor ao vivo continuaria operando ali. Cobrir horizontes maiores exige
    #: MEDIR horizontes maiores, não esticar os que existem.
    instantes_alem_da_curva: Counter[str] = field(default_factory=Counter)
    #: TODO sinal que cruzou o threshold, preenchido ou não — a coorte fixa do
    #: teste de direção. Ver `SinalDirecional` para por que ela não pode ser a
    #: lista de `trades`.
    sinais_direcionais: list[SinalDirecional] = field(default_factory=list)
    #: slug → o PRIMEIRO sinal daquela janela. A janela é a unidade que se
    #: aproxima de independente: dentro de uma, os instantes compartilham
    #: âncora, preço e resultado, então 240 instantes de uma janela não são
    #: 240 observações — são uma, repetida. Contar todos infla `n` e encolhe o
    #: p-valor sem que nenhuma informação nova tenha entrado.
    primeiro_sinal_da_janela: dict[str, SinalDirecional] = field(
        default_factory=dict
    )
    #: Instantes em que OS DOIS lados passaram do threshold. Nesses, o lado
    #: registrado é Up por ordem da lista, não por decisão — então é aqui que
    #: mora o único viés conhecido de `direcao_sem_fill`. Publicado ao lado do
    #: resultado para que a leitura possa descontá-lo em vez de confiar que é
    #: desprezível.
    sinais_com_ambos_os_lados: int = 0

    # ------------------------------------------------------------- coleta
    def add_trade(self, trade: Trade) -> None:
        self.trades.append(trade)

    def add_oportunidade(self, bucket: str, slug: str) -> None:
        """Um instante em que o gatilho dispararia, tenha operado ou não."""
        self.oportunidades_por_bucket[bucket] += 1
        self.janelas_com_oportunidade[bucket].add(slug)

    def add_sinal_direcional(self, sinal: SinalDirecional) -> None:
        """Registra a direção apostada e o resultado, ANTES de qualquer fill.

        Chamado no mesmo ponto de `add_oportunidade` e pela mesma razão: a
        pergunta é "o sinal existiu, e apontou para o lado certo?", não
        "operamos?".
        """
        self.sinais_direcionais.append(sinal)
        self.primeiro_sinal_da_janela.setdefault(sinal.slug, sinal)

    def add_calibration(self, bucket: str, prob_up: float, resolveu_up: bool) -> None:
        """Calibração é medida sobre TODAS as previsões, não só as negociadas.

        Medir só onde se operou enviesaria a curva: operou-se justamente onde
        o modelo estava mais confiante.
        """
        entry = self.calibracao.setdefault(bucket, CalibrationBucket(bucket=bucket))
        entry.somar(prob_up, resolveu_up)

        rotulo = faixa_de_probabilidade(prob_up)
        entry.faixas.setdefault(
            rotulo, FaixaDeConfiabilidade(faixa=rotulo)
        ).somar(prob_up, resolveu_up)

    # ------------------------------------------------------------ métricas
    @property
    def pnl_liquido(self) -> float:
        return sum(t.pnl_usdc for t in self.trades)

    @property
    def hit_rate(self) -> float:
        return (
            sum(1 for t in self.trades if t.acertou) / len(self.trades)
            if self.trades
            else float("nan")
        )

    def direcao_sem_fill(self) -> dict[str, Any]:
        """O teste direto de direção — o que a §2d-ter registra como pendente.

        Responde UMA pergunta: dos instantes em que o gatilho disparou, em que
        fração o lado escolhido foi o que venceu? Sem book, sem latência, sem
        teto de entradas — nada que a execução mude entra aqui.

        **Por que isso não é o `hit_rate`.** O `hit_rate` é medido sobre
        `trades`, e um trade só existe quando a ordem preencheu. Trocar a
        latência troca quais ordens preenchem, então os cenários de
        `sensibilidade_latencia` comparam populações diferentes. Aqui a coorte
        é a mesma em qualquer cenário, porque o sinal é calculado em `t` e não
        olha para frente.

        **Duas contagens, e a que decide é a segunda.** `por_sinal` usa todos
        os instantes; `por_janela` usa o primeiro de cada janela. Dentro de uma
        janela os instantes dividem âncora, preço e resultado — são a mesma
        observação repetida, e contá-los como independentes encolhe o p-valor
        sem que informação nova tenha entrado. `por_janela` é a leitura
        conservadora, e é a que deve ser citada.

        **O que este teste NÃO responde:** se a estratégia é lucrativa.
        Direção acima de 0,5 com custo de execução maior que a margem continua
        perdendo dinheiro — o 1.5 (capacidade) e o 1.1 (PnL) são critérios
        separados de propósito.
        """

        def _bloco(sinais: list[SinalDirecional]) -> dict[str, Any]:
            n = len(sinais)
            # SEMPRE publicado, e ao lado de `n`, porque é ele que diz quanto
            # o `n` vale. A primeira rodada real desta medição deu, numa hora,
            # `por_sinal` com n=1.141 e p=0,000 — sobre QUATRO janelas. Sem
            # este campo ao lado, o p-valor lê-se como evidência forte.
            janelas = len({s.slug for s in sinais})
            if n == 0:
                # Sem sinal não há acurácia. Devolver 0,5 aqui — "no meio" —
                # seria inventar uma medição para uma amostra que não existe.
                return {
                    "n": 0,
                    "janelas_distintas": 0,
                    "acertos": 0,
                    "acuracia": None,
                    "p_valor": None,
                    "difere_de_meio_a_meio": None,
                }
            acertos = sum(1 for s in sinais if s.acertou)
            p = _p_valor_binomial(acertos, n)
            return {
                "n": n,
                "janelas_distintas": janelas,
                "acertos": acertos,
                "acuracia": acertos / n,
                "p_valor": p,
                # 0,05 escrito aqui, e não escolhido depois de ver o número.
                "difere_de_meio_a_meio": bool(p < 0.05),
            }

        def _por_faixa(sinais: list[SinalDirecional]) -> dict[str, Any]:
            """A mesma acurácia, quebrada pela confiança do modelo.

            Responde a pergunta que o número agregado deixa em aberto: a regra
            erra em toda parte, ou erra **onde ela opera**? São diagnósticos
            diferentes. Se a acurácia acompanha a faixa — baixa onde o modelo
            diz 0,5, alta onde ele diz 0,9 —, o preditor discrimina e o defeito
            é de escolha de momento. Se for ruim em todas, é o sinal.

            A faixa é a do lado APOSTADO, não a do `P(Up)` cru: apostar Down
            com `P(Up)=0,1` é uma aposta de confiança 0,9, e agrupá-la com as
            de `P(Up)=0,1` que compraram Up misturaria duas coisas opostas.
            """
            grupos: dict[str, list[SinalDirecional]] = defaultdict(list)
            for s in sinais:
                confianca = s.prob if s.lado_up else 1.0 - s.prob
                grupos[faixa_de_probabilidade(confianca)].append(s)
            saida = {}
            for faixa, itens in sorted(grupos.items()):
                acertos = sum(1 for s in itens if s.acertou)
                # A confiança MÉDIA das apostas da faixa, não o meio dela.
                # É contra este número que a acurácia tem de ser lida: um
                # modelo calibrado NAS APOSTAS acertaria `confianca_media`.
                # Usar o meio da faixa erraria em até meia largura, e o
                # `deficit` é justamente uma diferença pequena entre dois
                # números grandes — o tipo de conta que a aproximação estraga.
                media = sum(
                    s.prob if s.lado_up else 1.0 - s.prob for s in itens
                ) / len(itens)
                saida[faixa] = {
                    "n": len(itens),
                    "janelas_distintas": len({s.slug for s in itens}),
                    "acertos": acertos,
                    "acuracia": acertos / len(itens),
                    "confianca_media": media,
                    # Negativo = o modelo acerta MENOS nas apostas do que
                    # promete. Com o modelo calibrado no agregado, isso é a
                    # assinatura de seleção adversa: a regra escolhe, dentro
                    # da faixa, justamente os instantes em que ele falha.
                    "deficit": acertos / len(itens) - media,
                }
            return saida

        def _por_limiar(sinais: list[SinalDirecional]) -> dict[str, Any]:
            """E se a regra exigisse convicção mínima para operar?

            É a primeira correção que ocorre a quem lê o déficit: se a regra
            erra onde o modelo não sabe, que ela só opere onde ele sabe. A
            varredura responde antes que alguém implemente e descubra depois.

            O ponto de atenção é o `n`: subir o limiar corta amostra rápido, e
            uma acurácia bonita sobre 20 apostas não é resultado. Por isso o
            p-valor sai junto de cada linha.
            """
            saida = {}
            for passo in range(10, 17):  # 0,50 a 0,80
                limiar = passo * LARGURA_DA_FAIXA
                elegiveis = [
                    s
                    for s in sinais
                    if (s.prob if s.lado_up else 1.0 - s.prob) >= limiar
                ]
                if not elegiveis:
                    continue
                acertos = sum(1 for s in elegiveis if s.acertou)
                saida[f"{limiar:.2f}"] = {
                    "apostas": len(elegiveis),
                    "acertos": acertos,
                    "acuracia": acertos / len(elegiveis),
                    "p_valor": _p_valor_binomial(acertos, len(elegiveis)),
                }
            return saida

        def _regra_invertida(sinais: list[SinalDirecional]) -> dict[str, Any]:
            """E se a regra comprasse o OUTRO lado? A conta, com o aviso.

            A direção erra com significância, então a inversão dela acerta na
            mesma medida — 1 − 0,4157. Isso é aritmética, e sozinho não diz
            nada sobre lucro: inverter compra o token oposto, a **outro
            preço**, e é o preço que decide se acertar 58 % paga.

            Por isso o que sai aqui é PnL por share nos dois sentidos, sobre a
            mesma coorte e com a mesma conta. Sem taxa: ela incide igual nos
            dois lados, e o que se compara é a direção.

            **INVERTER UMA REGRA QUE PERDEU É A FORMA MAIS FÁCIL DE
            SOBREAJUSTAR.** Se o número der positivo, ele não autoriza nada —
            exige repetir em dia independente, porque escolher a inversão
            DEPOIS de ver o resultado é escolher pelo resultado.
            """
            com_preco = [s for s in sinais if s.preco_do_lado is not None]
            invertiveis = [s for s in sinais if s.preco_do_oposto is not None]
            direto = [s.pnl_por_share() for s in com_preco]
            oposto = [s.pnl_por_share(invertido=True) for s in invertiveis]
            return {
                "n_com_preco": len(com_preco),
                "n_invertivel": len(invertiveis),
                "pnl_por_share_como_esta": (
                    sum(direto) / len(direto) if direto else None
                ),
                "pnl_por_share_invertido": (
                    sum(oposto) / len(oposto) if oposto else None
                ),
                "aviso": (
                    "Inverter uma regra que perdeu e a forma mais facil de "
                    "sobreajustar: a escolha da inversao vem DEPOIS de ver o "
                    "resultado. Numero positivo aqui NAO autoriza adotar — "
                    "exige repetir em dia independente. E PnL por share sem "
                    "taxa nem slippage: nao e o 1.1, que mede o liquido."
                ),
            }

        por_janela = list(self.primeiro_sinal_da_janela.values())
        total = len(self.sinais_direcionais)
        return {
            "por_sinal": _bloco(self.sinais_direcionais),
            "por_janela": _bloco(por_janela),
            "por_faixa_de_confianca": _por_faixa(por_janela),
            "se_exigisse_confianca_minima": _por_limiar(por_janela),
            "se_comprasse_o_outro_lado": _regra_invertida(por_janela),
            # O único viés conhecido desta medição, publicado como número.
            "vies_de_ambos_os_lados": {
                "instantes": self.sinais_com_ambos_os_lados,
                "fracao_dos_sinais": (
                    self.sinais_com_ambos_os_lados / total if total else None
                ),
                "nota": (
                    "instantes em que OS DOIS lados passaram do threshold. "
                    "Ali o lado registrado e Up por ordem da lista, nao por "
                    "decisao. Fracao alta invalida a leitura de direcao; "
                    "fracao perto de zero deixa o vies desprezivel — e o "
                    "numero esta aqui para que isso seja conferido, e nao "
                    "assumido."
                ),
            },
            "leitura": (
                "por_janela e a leitura conservadora e e a que decide: dentro "
                "de uma janela os instantes dividem ancora, preco e resultado, "
                "entao contar todos infla n sem informacao nova. Acuracia > 0,5 "
                "com p < 0,05 e evidencia de direcao; nao e evidencia de lucro, "
                "que depende de execucao e capacidade (1.1 e 1.5). "
                "Compare n com janelas_distintas em por_sinal: quando os dois "
                "estao longe, o p-valor de por_sinal e espurio — foi o que a "
                "primeira rodada real mostrou, com n=1141 sobre 4 janelas."
            ),
        }

    @property
    def fees_pagas(self) -> float:
        return sum(t.fee_usdc for t in self.trades)

    @property
    def capital_movimentado(self) -> float:
        return sum(t.custo_usdc for t in self.trades)

    def max_drawdown(self) -> float:
        """Maior queda do pico da curva de PnL acumulado, em USDC."""
        pico = 0.0
        acumulado = 0.0
        pior = 0.0
        for trade in self.trades:
            acumulado += trade.pnl_usdc
            pico = max(pico, acumulado)
            pior = min(pior, acumulado - pico)
        return pior

    def _grupo(self, chave) -> dict[str, dict[str, Any]]:
        buckets: dict[Any, list[Trade]] = defaultdict(list)
        for trade in self.trades:
            buckets[chave(trade)].append(trade)
        return {
            str(nome): {
                "n": len(grupo),
                "pnl_usdc": round(sum(t.pnl_usdc for t in grupo), 4),
                "hit_rate": round(sum(1 for t in grupo if t.acertou) / len(grupo), 4),
                "pnl_medio": round(sum(t.pnl_usdc for t in grupo) / len(grupo), 5),
                "fees_usdc": round(sum(t.fee_usdc for t in grupo), 4),
            }
            for nome, grupo in sorted(buckets.items(), key=lambda kv: str(kv[0]))
        }

    # ------------------------------------------------------------ saída
    def to_dict(self) -> dict[str, Any]:
        n = len(self.trades)
        return {
            "janelas_sem_curva_de_variancia": dict(self.janelas_sem_curva),
            "janelas_de_jogo_sem_curva": dict(self.janelas_de_jogo_sem_curva),
            "instantes_alem_da_curva": dict(self.instantes_alem_da_curva),
            "resumo": {
                "janelas_avaliadas": self.janelas_avaliadas,
                "sinais_gerados": self.sinais_gerados,
                "trades": n,
                "pnl_liquido_usdc": round(self.pnl_liquido, 4),
                "pnl_medio_por_trade": round(self.pnl_liquido / n, 5) if n else None,
                "hit_rate": round(self.hit_rate, 4) if n else None,
                "max_drawdown_usdc": round(self.max_drawdown(), 4),
                "fees_pagas_usdc": round(self.fees_pagas, 4),
                "capital_movimentado_usdc": round(self.capital_movimentado, 2),
                "retorno_sobre_capital": (
                    round(self.pnl_liquido / self.capital_movimentado, 5)
                    if self.capital_movimentado
                    else None
                ),
            },
            "direcao_sem_fill": self.direcao_sem_fill(),
            "funil_de_sinais": {
                "gerados": self.sinais_gerados,
                "descartados_sem_book": self.sinais_sem_book,
                "descartados_abaixo_do_minimo": self.sinais_abaixo_do_minimo,
                "descartados_nao_preenchiveis": self.sinais_nao_preenchiveis,
                "viraram_trade": n,
                "taxa_de_conversao": (
                    round(n / self.sinais_gerados, 4) if self.sinais_gerados else None
                ),
            },
            "por_jogo": self._grupo(lambda t: t.jogo),
            "por_ativo": self._grupo(lambda t: t.asset),
            "por_duracao": self._grupo(lambda t: f"{t.duracao_s}s"),
            "por_bucket_tempo": self._grupo(lambda t: t.bucket_tempo),
            "oportunidades_por_bucket": {
                bucket: {
                    "instantes_com_sinal": self.oportunidades_por_bucket[bucket],
                    "janelas_distintas": len(self.janelas_com_oportunidade[bucket]),
                    "trades": sum(
                        1 for t in self.trades if t.bucket_tempo == bucket
                    ),
                }
                for bucket in sorted(
                    set(self.oportunidades_por_bucket)
                    | {t.bucket_tempo for t in self.trades}
                )
            },
            "oportunidades_nota": (
                "M2.6 BUG 2. `instantes_com_sinal` conta TODO instante em que "
                "algum lado passou do threshold, inclusive depois de a janela "
                "ja ter operado; `trades` conta onde a entrada realmente "
                "caiu. A v1 entra UMA vez por janela e varre o stream da "
                "abertura para o fechamento, entao ela opera no primeiro "
                "instante elegivel — que por construcao esta no comeco, no "
                "bucket >240s. Se `instantes_com_sinal` for alto nos buckets "
                "calibrados e `trades` for ~0 la, o gatilho nao esta "
                "escolhendo o bucket ruim: ele nunca chega no bom. Use "
                "--tempo-restante-max para forcar a faixa."
            ),
            "calibracao": {
                bucket: {
                    "n": entry.n,
                    "prob_media_prevista": round(entry.prob_media_prevista, 4),
                    "freq_realizada": round(entry.freq_realizada, 4),
                    "erro": round(entry.erro_calibracao, 4),
                    "erro_de_confiabilidade": round(
                        entry.erro_de_confiabilidade, 4
                    ),
                    "faixas_ocupadas": entry.faixas_ocupadas,
                    "calibracao_avaliavel": entry.calibracao_avaliavel,
                    "curva_de_confiabilidade": {
                        faixa.faixa: {
                            "n": faixa.n,
                            "previsto": round(faixa.prob_media_prevista, 4),
                            "realizado": round(faixa.freq_realizada, 4),
                            "erro": round(faixa.erro, 4),
                        }
                        for faixa in sorted(
                            (f for f in entry.faixas.values() if f.n),
                            key=lambda f: f.faixa,
                        )
                    },
                }
                for bucket, entry in sorted(self.calibracao.items())
            },
            "calibracao_nota": (
                "M2.13. LEIA `erro_de_confiabilidade`, NAO `erro`. O `erro` "
                "compara a probabilidade media prevista com a TAXA-BASE do "
                "balde, entao um preditor que cospe uma constante igual a "
                "taxa-base tira zero sem saber nada. Foi o que a rodada de "
                "20h expos: no balde `<30s`, previsto 0,514 contra realizado "
                "0,5073 — cara-ou-coroa dos dois lados — e o criterio 1.3 do "
                "VEREDITO_M2 'passou' com 0,0067. "
                "`erro_de_confiabilidade` e o ECE sobre `curva_de_"
                "confiabilidade`: media dos |previsto - realizado| por faixa "
                "de probabilidade PREVISTA, ponderada pelo tamanho da faixa. "
                "MAS O ECE SOZINHO NAO BASTA, e isso foi medido: o preditor "
                "constante tira 0,0051 nele tambem, porque cai todo numa "
                "faixa so. Quem o denuncia e `faixas_ocupadas`. "
                "O criterio 1.3 e a CONJUNCAO: `calibracao_avaliavel` true "
                "(pelo menos 3 faixas com amostra) E `erro_de_"
                "confiabilidade` abaixo do limiar. Com `calibracao_avaliavel` "
                "false o criterio fica NAO AVALIADO — que nao e o mesmo que "
                "reprovado."
            ),
        }


def curva_de_horizonte(por_banda: dict[str, Any]) -> dict[str, Any]:
    """O veredito de horizonte sobre a varredura, com a regra registrada na §2d-bis.

    `por_banda` é a saída de `varredura_de_horizonte`: cada banda de tempo
    restante com `trades`, `pnl_liquido_usdc`, `hit_rate`, `amostra_suficiente`.
    A regra de leitura, fixada ANTES dos números: uma banda TEM EDGE se, e só
    se, `pnl_liquido_usdc > 0` E `hit_rate > 0.5` E `amostra_suficiente`
    (n >= 40). Banda que passa em PnL e hit mas não em amostra é sinal fraco —
    entra em `sinal_fraco`, publicada como sensibilidade, e NÃO decide.
    """
    com_edge: list[str] = []
    fraco: list[str] = []
    for banda, dados in por_banda.items():
        pnl = dados.get("pnl_liquido_usdc")
        hit = dados.get("hit_rate")
        if pnl is None or hit is None or pnl <= 0 or hit <= 0.5:
            continue
        if dados.get("amostra_suficiente"):
            com_edge.append(banda)
        else:
            fraco.append(banda)
    algum = bool(com_edge)
    return {
        "por_banda": por_banda,
        "bandas_com_edge": com_edge,
        "alguma_banda_com_edge": algum,
        "sinal_fraco": fraco,
        "nota": (
            "Diagnostico de horizonte (VEREDITO_M2 §2d-bis, registrado antes "
            "dos numeros). Cada banda e o preditor CRU (sem encolhimento, que "
            "foi rejeitado na §2d) forcado a operar so naquela faixa de tempo "
            "restante — remove o vies de primeira-chegada do `por_bucket_tempo`, "
            "que mede onde a v1 OPEROU e nao onde o edge vive. Regra de leitura: "
            "uma banda TEM EDGE sse pnl_liquido_usdc > 0 E hit_rate > 0,5 E "
            "amostra_suficiente (n >= 40, para o IC de 95% do hit_rate nao "
            "cruzar 0,5 por acaso). Banda com pnl>0 e hit>0,5 mas n<40 e sinal "
            "fraco: publica como sensibilidade, NAO decide. "
            + (
                "ALGUMA banda tem edge: o defeito e de horizonte, e o M3 opera "
                "naquela banda e remede 1.1-1.5 restrito a ela."
                if algum
                else "NENHUMA banda tem edge: somado a escala ja rejeitada, o "
                "preditor cru nao tem edge em horizonte nenhum — o M3 troca o "
                "preditor ou re-escopa."
            )
        ),
    }


def curva_de_edge_por_threshold(
    trades_por_threshold: dict[float, BacktestReport],
) -> dict[str, Any]:
    """Qual threshold de entrada maximiza o resultado LÍQUIDO."""
    linhas = {
        f"{threshold:.3f}": {
            "trades": len(report.trades),
            "pnl_liquido_usdc": round(report.pnl_liquido, 4),
            "hit_rate": round(report.hit_rate, 4) if report.trades else None,
            "pnl_medio": (
                round(report.pnl_liquido / len(report.trades), 5) if report.trades else None
            ),
        }
        for threshold, report in sorted(trades_por_threshold.items())
    }
    melhor = max(
        trades_por_threshold.items(),
        key=lambda kv: kv[1].pnl_liquido if kv[1].trades else -math.inf,
        default=(None, None),
    )
    # M2.10: a grade pode não MORDER. Na gravação real de 2026-08-22 os seis
    # thresholds — de 0,01 a 0,12 — deram os mesmos 11 trades e o mesmo PnL
    # de 3,3626: o modelo previa ~0,83 de probabilidade contra um book perto
    # de 0,50, então a entrada já nascia com edge acima do teto da grade e
    # subir o limiar não excluía nada. `max()` desempatou pelo primeiro e o
    # relatório publicou `melhor_threshold: 0.01`, que se lê como escolha
    # quando é artefato.
    #
    # O criterio 1 do VEREDITO_M2 ("PnL positivo com threshold >= 0,02")
    # avaliado sobre uma curva degenerada mede outra coisa: o resultado nao
    # tem informacao nenhuma sobre threshold. Dizer isso e o minimo.
    distintos = {
        (len(report.trades), round(report.pnl_liquido, 4))
        for report in trades_por_threshold.values()
    }
    mordeu = len(distintos) > 1
    return {
        "por_threshold": linhas,
        "melhor_threshold": melhor[0],
        "melhor_pnl_usdc": (
            round(melhor[1].pnl_liquido, 4) if melhor[1] is not None else None
        ),
        "threshold_mordeu": mordeu,
        "resultados_distintos": len(distintos),
        "nota": (
            "`threshold_mordeu` false = TODOS os thresholds da grade "
            "produziram o mesmo conjunto de trades e o mesmo PnL, logo o "
            "limiar nunca excluiu sinal nenhum e `melhor_threshold` e "
            "desempate de `max()`, nao escolha. Ler a curva como se fosse "
            "otimizacao seria inventar um resultado. Duas causas possiveis, "
            "e as duas importam: a grade e baixa demais para este sinal (o "
            "edge nasce acima de 0,12), ou ha pouco trade e a grade nao tem "
            "o que separar. Suba a grade com --thresholds antes de concluir "
            "qualquer coisa sobre limiar de entrada."
            if not mordeu
            else "`threshold_mordeu` true = a grade separou resultados, "
            "entao `melhor_threshold` e comparacao de verdade."
        ),
    }
