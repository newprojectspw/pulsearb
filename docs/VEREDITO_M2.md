# VEREDITO M2 — existe edge líquido?

> ## ✅ A REMEDIAÇÃO RODOU EM 2026-08-30 — e os números de 1.1 a 1.4 e de 2.3 são OUTROS
>
> O `prob_up_twap` tinha um defeito de variância que, somado a outros dois
> erros, subestimava a variância do TWAP de fechamento em **39 a 48 vezes**
> (6,3× no desvio-padrão). A saída não foi corrigir a derivação: foi
> **substituí-la por medição**, do mesmo jeito que a §13.8 fez com a âncora. A
> `V(t)` agora vem de dado real, medida em dia ANTERIOR ao avaliado (curva de
> 23/08 → avaliação de 24/08), com recusa em código se as datas se cruzarem.
>
> **Resultado (§2d-ter, subseção "O RESULTADO"):** o **1.3 passa** nos cinco
> baldes (ECE 0,0126–0,0493, contra 0,207 na banda) — **e a borda some junto**:
> 688 trades, PnL −67,27, `bandas_com_edge: []` e `hit_rate` 0,4172. O taker
> reprova agora por 1.1, 1.4 e 1.5.
>
> **O que é histórico, exatamente:** os números de 1.1 a 1.4 e do 2.3 nas
> seções de veredito abaixo — o **SEGUNDO VEREDITO** (24 h de 24/08) e tudo o
> que vem depois dele até a §2d-ter. Vêm do preditor com variância derivada, e
> ficam no documento porque são o registro de como se chegou aqui. **Inclusive
> o +2,7125 da banda** (ver a delimitação da conclusão causal na §2d-ter).
>
> **Duas partes deste arquivo NÃO são histórico**, apesar de virem depois: a
> subseção "O RESULTADO" da §2d-ter, e o placar corrente em "Placar dos 10
> critérios pré-registrados" — os dois estão rotulados no lugar. Fora daqui, o
> estado corrente está no `ESTADO_PARA_LIVE.md`.
>
> Nunca passaram pelo preditor, e seguem valendo como estão: o **1.5**
> (estrutura de book — o teto de 128 USDC contra 200 continua de pé), o 1.7,
> 1.8 e 1.9 (rota maker), a âncora τ=0 e o `hourly.py`.

**Status: TERCEIRO VEREDITO em 2026-08-30**, sobre as mesmas 24 h limpas mas
com o preditor de variância medida — **1.3 passa, e a borda some**. Está na
§2d-ter, subseção "O RESULTADO". As duas seções logo abaixo são o segundo
veredito (2026-08-26) e o primeiro (2026-08-23), preservados na ordem em que
foram escritos.

Data: 2026-08-16 · atualizado 2026-08-21 (M2.5) · veredito 2026-08-23 ·
reveredito 2026-08-26 sobre 2026-08-24 · defeito de variância achado e
corrigido 2026-08-29 · **remediação medida e rodada 2026-08-30 (§2d-ter)**

---

## SEGUNDO VEREDITO — 24 h de 2026-08-24, captação impecável

Amostra: 2026-08-24, dia inteiro, 24 arquivos, 73 min de processamento com
`--limite-por-token 20000 --niveis-por-lado 10`.
Relatório: `relatorios/M2_24AGO.json`.

**Por que esta rodada vale mais que a primeira.** A de 20 h carregava
`gaps: rtds silencio 837s` e descartou 42% dos snapshots, com resolução
efetiva de ~1,9 s — o suficiente para tornar o cenário de 300 ms
indistinguível. Esta traz `pior_fracao_coberta 1,0` nos oito ativos,
**0 silêncios**, `conexao_inteira 0`, `suspeita_de_assinatura_caducada 0`,
896 janelas conhecidas e 820 com resolução. Não é um dia diferente apenas:
é o primeiro dia em que o instrumento não estava cego em pedaços.

### Âncora — CONFIRMADA de novo, em dia independente

`tau=0` explica **0,9987** das 768 janelas elegíveis (1 discordante), com
distribuição não concentrada (quartis 190/192/196/190). A verificação
anterior deu 0,9984 sobre 640 janelas. Duas amostras independentes, mesma
resposta: **a âncora é o `crypto_prices_twap_sixty` na abertura da janela.**
Isto fecha o item 2.3 do M3.

### TAKER — reprova. O +102,92 não sobrevive

| # | Critério | Exigido | 20 h (23 ago) | 24 h (24 ago) | |
|---|---|---|---|---|---|
| 1.1 | PnL líquido @300 ms | positivo | +102,9227 | **−53,2777** | ❌ |
| 1.2 | Trades | ≥ 200 | 568 | **695** | ✅ |
| 1.3 | Erro de calibração | < 0,05 em ≥ 1 balde AVALIÁVEL | 0,0067 (campo errado) | **0,0694** (balde <30 s, 20 faixas) | ❌ |
| 1.4 | PnL líquido @600 ms | positivo | +101,1759 | **−54,3953** | ❌ |
| 1.5 | Profundidade p50 3 ticks | ≥ 200 USDC | 87,8 / 41,8 / 31,3 / 35,7 | **128,0 / 50,0 / 28,7 / 27,0** | ❌ |

> Esta tabela é a da rodada **IRRESTRITA** (695 trades, bucket `>240s`). Para os
> mesmos critérios remedidos dentro da banda com edge (240-120s, 640 trades),
> onde o 1.4 passa a **+1,3488**, ver §2d-bis — são populações diferentes e não
> devem ser lidas na mesma linha.

O TAKER exige as CINCO. **Reprova em quatro.**

**O sinal não inverteu por pouco: inverteu por 156 USDC**, com 127 trades a
mais e captação melhor. A leitura honesta não é "o dia 24 foi ruim" — é que
o **+102,9227 nunca teve o lastro que aparentava**. Ele saiu de uma gravação
com 837 s de silêncio e 42% dos snapshots jogados fora; buraco de livro não
produz erro simétrico, porque o preenchimento simulado usa o último snapshot
conhecido, que numa lacuna é sistematicamente melhor que o real. O critério
1.1 foi escrito antes dos números justamente para este momento.

### O 1.3 é o achado que explica os outros

Lido no campo certo, o erro de confiabilidade do melhor balde avaliável é
**0,0694** — 39% acima do limiar de 0,05, com 20 faixas ocupadas, então é
avaliável de sobra. O modelo não está calibrado.

E isso fecha a causa do PnL. A taxa do taker é **1,75 centavo/share**: para
lucrar, a probabilidade verdadeira precisa exceder a paga em mais de 0,0175.
O erro médio do próprio modelo, dentro das suas próprias faixas, é 0,0694 —
**quatro vezes a barreira que ele precisa vencer**. Um preditor cujo ruído é
4× o edge exigido não perde por causa do mercado, da latência ou da fila:
perde porque não sabe o que diz saber.

Duas evidências independentes concordam:

- **A curva de threshold não tem tendência.** De 0,010 a 0,120 — doze vezes
  mais exigente — o PnL passeia entre −41,96 e −53,47 sem direção. Se
  houvesse sinal, filtrar mais forte teria de melhorar.
- **A curva de latência também não.** −51,86 a 150 ms, −53,28 a 300, −54,40
  a 600 e **−51,35 a 1000**. O pior caso de atraso é o segundo melhor
  resultado. Borda que decai com latência não faz isso.

**O 1.3 não foi medido em nenhum dos dois vereditos, e a causa é um defeito
do resumo.** O `resumo_m2.py` imprimia o campo `erro` — exatamente o campo
que o relatório manda NÃO ler, por escrito, na chave `calibracao_nota`. O
`erro` compara a probabilidade média prevista com a TAXA-BASE do balde,
então um preditor que cospe uma constante igual à taxa-base tira zero sem
saber nada; foi o que a rodada de 20 h expôs, com previsto 0,514 contra
realizado 0,5073 no balde `<30s` — cara-ou-coroa dos dois lados — e o
critério "passando" com 0,0067. O `−0,0015` do dia 24 é o mesmo campo
errado.

O critério é a CONJUNÇÃO de `calibracao_avaliavel` (≥ 3 faixas com amostra)
com `erro_de_confiabilidade` abaixo do limiar. O resumo agora lê os dois e
imprime, em cada linha, O CAMPO QUE LEU — ler o campo errado é erro
silencioso por natureza, porque o número sai bem formatado de qualquer jeito.
Rodar `scripts/resumo_m2.py` de novo sobre `M2_24AGO.json` fechou este item
sem gravação nova — e o resultado foi ❌, não o ✅ que o campo errado sugeria.

### O −53,28 é entrada múltipla, não inversão de sinal

O mesmo relatório, lido por configuração:

| configuração | PnL |
|---|---|
| `max_1_entradas` | **+2,7125** |
| `max_3_entradas` | −98,3907 |
| `max_10_entradas` | −221,6423 |
| faixa `restrito` | **+2,7125** |
| faixa `irrestrito` | −53,2777 |

`max_1_entradas` e `restrito` dão o mesmo número até a quarta casa: são o
mesmo conjunto de trades. A segunda entrada em diante é justamente a que cai
fora da faixa calibrada. Confirma com 24 h o que o §2h já dizia — *a entrada
múltipla é alavancagem, não edge*.

Mas **+2,71 não resgata nada**: 640 trades para ganhar 2,71 USDC é 0,4
centavo por trade, com drawdown máximo de −108,10. É ruído com sinal
aleatório, e o critério 1.1 exige positivo com threshold ≥ 0,02, onde o
número é −53,28.

### Uma armadilha de comparações múltiplas, pega rodando

O critério 1.7 pede markout "no p50 de pelo menos um recorte". A primeira
versão do resumo corrigido leu isso como *o melhor número da tabela* e
escolheu `hora_utc=01` com **+0,88 centavo** — markout POSITIVO, ou seja,
lucro de adverse selection, que não existe. A tabela tem duas dezenas de
células (total, durações, horas do dia): o máximo entre elas é ruído por
construção. Passou a usar `total`, ou a célula de maior amostra, nunca a
mais favorável — e a contagem de execuções sai impressa junto.

O 1.5 melhorou onde importa (300 s: 87,8 → 128,0) e continua abaixo de 200
em todas as durações. Com `threshold_mordeu: true` e 6 resultados distintos
na curva de edge, a capacidade morde de verdade.

### MAKER — reprova pelo 1.10, com o 1.6 não avaliável

| # | Critério | Exigido | 20 h | 24 h | |
|---|---|---|---|---|---|
| 1.6 | Conta fechada @ desconto 0,3 | positiva | +0,043 ¢/share | **NÃO AVALIÁVEL** | ⚠️ |
| 1.7 | Markout 5 s | ≥ −0,5 ¢/share | −0,307 | **−0,1974** | ✅ |
| 1.8 | Horas de amostra | ≥ 20 h | 40,7 h | **65,9 h** | ✅ |
| 1.9 | Divergência com topo deslocado (emenda abaixo) | < 1 % | 2,89 %¹ | **0,20 %** | ✅ |
| 1.10 | Fórmula de reward na doc oficial | sim | não | **não** | ❌ |

¹ agregada — o relatório de 20 h não tem a decomposição; pela regra da
emenda, relatório sem decomposição continua julgado pelo agregado.

#### Emenda ao 1.9 — registrada em 2026-08-27, DEPOIS de ver os números

O 1.9 passa a ser julgado sobre `com_magnitude_finita / comparacoes` (o topo
deslocado), não sobre a `taxa` agregada. A honestidade metodológica exige
dizer as duas coisas na ordem certa:

- **A emenda é posterior à observação.** O dia 24 mediu 2,82 % agregado e
  0,20 % decomposto; a mudança de população foi decidida com esses números
  na mesa. Quem quiser descontar o 1.9 por isso tem direito.
- **A classificação em que ela se apoia é anterior.** O §2c (M2.5) registrou
  quais divergências invalidam antes de qualquer veredito: lado vazio por
  snapshot incompleto ou truncagem de profundidade **não** invalida — é
  visão incompleta nossa, não livro furado. A `taxa` agregada soma essas
  categorias (92,8 % do total no dia 24) com a corrupção real. A emenda não
  inventa uma população nova; alinha o número julgado à classificação já
  registrada.
- **Salvaguardas**: a agregada continua impressa em todo resumo ao lado da
  julgada; relatório antigo sem decomposição segue julgado pelo agregado,
  com aviso; e se num relatório futuro a decomposição passar enquanto a
  agregada explodir por categoria que o §2c NÃO classificou como
  não-invalidante, o veredito é ❌ até classificar.

**O 1.6 é NÃO AVALIÁVEL por construção, não por falta de amostra.**
`conta_fechada.o_que_falta_para_fechar` é não-vazia em todo relatório que
este backtest produz: faltam `volume_taker_usdc`, o custo de markout em USDC
e o capital imobilizado — os três dependem de posição na fila, que o WS
agregado não entrega. O `+0,043 ¢/share` do primeiro veredito saiu de
`rebate_vs_markout`, que é outro número: `resultado_parcial_usdc` soma
rewards e rebate e **não subtrai o markout**. Não reprova; também não passa.

`janelas_com_pool_de_reward` subiu de 5 (em 599) para **10** (em 896) — de
0,8% para 1,1%. Confirma em vez de derrubar: **os mercados updown não
participam do programa de rewards**, e isso não é ajustável por nós.

O saldo `rebate − markout` melhorou para **+0,1526 ¢/share** (0,35 de rebate
contra 0,1974 de markout), mas continua sendo TETO: o rebate só existe se
alguém nos executar, e a posição na fila não é observável no WS agregado.

### O que este segundo veredito muda na prática

**Nenhuma das duas rotas passa.** O TAKER perdeu o único critério que
sustentava a ideia de ir a dinheiro real; o MAKER continua barrado pela
fórmula de reward não confirmada (1.10) e pela conta que não fecha sem
posição na fila (1.6, não avaliável por construção) — a divergência de
livro saiu da lista de bloqueios com a emenda ao 1.9 (0,20 % na população
que invalida).

O trabalho do M4 — portões de risco, SHADOW, ciclo ao vivo — **não é
perdido**: ele é o que permite medir sem arriscar. Mas a decisão de ligar o
LIVE com a estratégia taker atual deixa de ter base, e o próximo passo
honesto é entender POR QUE o sinal inverte, não procurar um dia em que ele
volte a fechar positivo.

---

## VEREDITO — 20 horas de mercado real

Amostra: 2026-08-23, 00:00 a 20:00 UTC (hora 01:00 excluída, dois fragmentos
corrompidos na origem). `pior_fracao_coberta` 1,0 nos oito ativos, 794 janelas
conhecidas, **568 trades**, 2 h 47 min de processamento.
Relatório: `relatorios/M2_VEREDITO.json`.

### TAKER — 4 dos 5

| # | Critério | Exigido | Medido | |
|---|---|---|---|---|
| 1.1 | PnL líquido @300 ms | positivo | **+102,9227 USDC** | ✅ |
| 1.2 | Trades | ≥ 200 | **568** | ✅ |
| 1.3 | Erro de calibração < 0,05 | em ≥ 1 bucket | 0,0067 | ⚠️ não avaliado |
| 1.4 | PnL líquido @600 ms | positivo | **+101,1759 USDC** | ✅ |
| 1.5 | Profundidade p50 3 ticks | ≥ 200 USDC | 87,8 / 41,8 / 31,3 / 35,7 | ❌ |

### MAKER — reprova

| # | Critério | Exigido | Medido | |
|---|---|---|---|---|
| 1.6 | Conta fechada | positiva | +0,043 ¢/share | ⚠️ |
| 1.7 | Markout 5 s | ≥ −0,5 ¢/share | **−0,307** | ✅ |
| 1.8 | Horas de amostra | ≥ 20 h | **40,7 h** | ✅ |
| 1.9 | Divergência do livro | < 1 % | **2,89 %** | ❌ |
| 1.10 | Fórmula de reward na doc oficial | sim | não | ❌ |

**594 das 599 janelas sem pool de reward** — as 5 que têm são de 4 h. Sem
pool não há receita de maker nestes mercados, e isso não é ajustável.

### O que este veredito NÃO diz

Três ressalvas que fazem parte do resultado, não são notas de rodapé:

1. **O critério 1.3 não foi avaliado.** O `erro` publicado compara a
   probabilidade média prevista com a **taxa-base do balde**, não com a
   acurácia por faixa. Um preditor constante passa. Marcar ✅ seria contar
   artefato de construção como evidência.
2. **A borda não vem de seleção.** `threshold_mordeu` virou `true`, mas os 568
   trades passam em todos os patamares da grade. O lucro nasce de viés
   sistemático (prevê 0,6445, realiza 0,6225 no balde operado), não de
   escolher situações boas.
3. **O 1.5 é teto de capacidade.** +0,18 USDC por trade sobre 2,91 USDC
   movimentados. O livro tem p50 de 87,77 USDC a 3 ticks na duração mais
   líquida.

### A âncora

`0,9984` em τ=0 sobre **640 janelas elegíveis**, 1 discordante, quartis
134/168/170/168, `concentrada: false`. Família de controle (`media_60s`) em
0,9528. **Fechada.**

---

## O veredito honesto de 2026-08-21 (histórico)

> Esta seção valia antes de a gravação de 20 h existir. Fica como estava, de
> propósito: apagar o raciocínio anterior esconderia o que mudou com o dado.

**Não sei, e ninguém sabe, porque a gravação de produção ainda não existe.**

Toda a maquinaria do M2 está construída e testada: recorder, replay
determinístico, modelo TWAP endgame, validação da âncora, backtest com todos
os descontos, e as quatro medições. O que falta é a única coisa que não pode
ser construída — **72h de mercado real**.

Escrever aqui um veredito com números de gravação sintética seria a definição
exata do que o M2 existe para impedir. O ambiente de desenvolvimento não
alcança a Polymarket; os números que aparecem nos testes vêm de um gerador com
seed fixa, cujo book é precificado na probabilidade verdadeira por construção.
Ele prova que o **pipeline** funciona. Não diz nada sobre o mercado.

---

## O que já dá para afirmar, com o que foi medido

Estas foram medidas ao vivo e mudam o quadro antes de qualquer backtest —
a terceira derruba uma hipótese que estava registrada como plausível.

### 1. A taxa é o adversário principal, não a latência

A cadência do feed é de **~1 segundo** (p50 0,86s no TWAP, p99 2,47s —
API_NOTES 13.1). Isso reordena o projeto inteiro:

- **Micro-latência é irrelevante neste regime.** A diferença entre 5ms e 50ms
  de RTT desaparece diante de um feed que atualiza a cada segundo. A escolha
  de Londres é de baixo impacto, e a sensibilidade a latência do backtest
  provavelmente vai sair plana — o que seria uma confirmação, não uma surpresa.
- **A taxa, ao contrário, é enorme.** Em `p = 0,50`, a taxa é 1,75% do valor
  nominal, ou **3,5% do capital**. Um edge de 2 pontos percentuais em
  probabilidade — que já é um edge grande para um mercado com centenas de
  participantes — é quase todo consumido por ela.

O aritmético que decide o projeto:

```
edge_líquido(p) = prob − preço − r·(p·(1−p))^e
```

Com r=0,07 e e=1, em p=0,50 a taxa é 0,0175. **Para entrar com 2pp de edge
líquido é preciso ter 3,75pp de edge bruto** — ou seja, estar certo sobre a
probabilidade com margem de quase 4 pontos contra um book que já viu tudo o
que você viu, no mesmo segundo.

### 2. Onde o edge estaria, se estiver em algum lugar

O modelo TWAP endgame diz onde procurar: nos **últimos 60 segundos** da janela.

Faltando `t < 60s`, a fração `(60−t)/60` da média final **já aconteceu e não
muda mais**. Faltando 10s, 83% do resultado está travado. A incerteza colapsa
de forma *conhecida* — e essa é a única fonte plausível de discordância
sistemática com o book, porque exige reconstruir o TWAP corrente, não só olhar
o preço.

Antes disso (t > 60s), o modelo degenera para "para onde o preço vai andar",
que é onde o book é mais eficiente e o ruído domina. **A hipótese a testar é
que o edge, se existir, está concentrado nos buckets `60-30s` e `<30s`** — e é
por isso que a calibração é reportada por bucket, nunca agregada.

### 2b. A âncora não era nenhuma das hipóteses nomeadas — a varredura do M2.4 a encontrou `[RESOLVIDO]`

Primeira validação real (26 janelas resolvidas, ~14 determináveis, hora 19h de
2026-08-19): as melhores hipóteses nomeadas (`primeiro_depois`,
`mais_proximo`, `interpolado`) acertaram **11/14 ≈ 79%** — longe demais do
acaso para ser coincidência, longe demais de 100% para ser a âncora. E as 3
falhas são AS MESMAS janelas em todas as hipóteses: **erro sistemático, não
ruído**. Numa delas (`btc-updown-15m-1787166900`) a previsão Up tinha 30
pontos de folga e resolveu Down — ou a âncora não é tick próximo da abertura,
ou o nosso "TWAP final" não é o valor de liquidação.

O M2.4 troca o palpite por engenharia reversa: cada resolução impõe uma
desigualdade sobre a âncora (Up ⇒ TWAP_final ≥ A; Down ⇒ TWAP_final < A,
empate = Up), e a varredura testa a família A(τ) = stream em `abertura + τ`
com τ ∈ [−180s, +180s], mais a grade conjunta (τ, φ) para o lado do
fechamento.

#### Critérios do M2.4 — escritos ANTES de rodar a varredura

**SUCESSO (âncora identificada):** existe τ (ou célula τ, φ) com consistência
**≥ 98%** sobre **≥ 100 janelas** com cobertura completa do stream.

Por que 98 e 100, e não outros números:

- **98%, não 100%:** a consistência perfeita seria o ideal teórico, mas a
  amostra real carrega janelas com lacuna de stream fina demais para o nosso
  detector de cobertura pegar (reconexões de segundos) e possíveis empates
  mal-carimbados. Exigir 100% deixaria uma única janela suja vetar a âncora
  certa. 2 falhas em 100 é o orçamento para esse lixo residual.
- **98% separa de verdade:** um τ errado com a taxa observada de ~79% tem
  probabilidade da ordem de 10⁻⁸ de marcar 98/100 (binomial). Não há como um
  impostor passar por sorte.
- **N = 100, não 26:** com 26 janelas, 98% = no máx. 0 falhas e o intervalo
  de confiança da taxa é largo demais (±8pp). ~26 janelas/hora ⇒ 100 janelas
  são ~4h de gravação — barato, e suficiente para o intervalo cair a ±3pp.

**FALHA DA FUNDAÇÃO:** nenhum τ consistente **E** existem janelas cujo
resultado NENHUM ponto do stream em `[abertura−180s, abertura+180s]` poderia
explicar (min/max do stream não cobrem o lado exigido pela desigualdade).
Nesse caso a fonte de liquidação **não é o nosso stream**, o modelo TWAP
endgame perde a premissa central, e **precisa ser refundado antes de qualquer
72h virar veredito** — gravar mais não conserta premissa errada.

**Resultado intermediário (provável):** τ com consistência alta mas < 98%, ou
região viável instável entre horas. Leitura: a âncora é da família testada mas
o alinhamento fino (cadência do stream, arredondamento e18, atraso de
publicação) come as pontas — investigar as falhas UMA A UMA antes de subir N.

As hipóteses nomeadas continuam no relatório como referência; a varredura vem
além delas, nunca no lugar.

#### RESULTADO — âncora IDENTIFICADA `[VERIFICADO 2026-08-21]`

A varredura rodou sobre **6h de gravação real** (2026-08-20, 10h–15h UTC),
**152 janelas elegíveis**, e deu resposta:

```
final_stream_no_fechamento -> tau com consistência 1.0: [-1, 0, 1, 2]
final_media_60s            -> teto de 0.9648, nenhum tau com 1.0
```

**A âncora é o valor do stream `crypto_prices_twap_sixty` no instante da
abertura da janela, e o valor final é o mesmo stream no instante do
fechamento.** Empate resolve Up.

O critério escrito antes pedia ≥ 98% sobre ≥ 100 janelas. O resultado é
**100% sobre 152** — passou com folga, e passou na definição de final que a
teoria previa.

**Por que a região viável tem 4 segundos de largura e isso não é frouxidão:**
a cadência do feed é de ~0,86s (p50, §13.1 do API_NOTES). Entre τ = −1 e
τ = +2 o stream simplesmente não tem outro ponto para discordar — é a
**resolução máxima que o dado permite**, não ambiguidade do método. Nenhuma
gravação mais longa estreita isso; só um feed mais rápido estreitaria.

**Corolário, e é o achado de projeto mais caro deste marco: não devemos
calcular média de 60s nenhuma.** O feed já entrega a média da Chainlink
pronta — `crypto_prices_twap_sixty` **é** o TWAP, não o insumo dele. A
família `final_media_60s`, que refazia essa conta por cima do stream, nunca
passou de **96,48%**. Aqueles 3,5% de erro não eram do mercado nem do
alinhamento: eram **a nossa conta errando** — reamostragem, borda da janela
e arredondamento sobre um valor que já vinha pronto.

O que isso muda no código, em regra permanente:

> **Nenhum componente do PULSEARB recalcula TWAP.** O valor de liquidação e a
> âncora saem do stream, do jeito que chegam, em inteiro e18. Qualquer média
> que apareça no caminho da decisão é bug até prova em contrário.

**Hipóteses nomeadas (`primeiro_depois`, `mais_proximo`, `interpolado`):
SUPERADAS por este resultado.** Elas acertavam 11/14 ≈ 79% porque estavam na
vizinhança certa — todas leem o stream perto da abertura. O que faltava não
era escolher entre elas, era o **lado do fechamento**: as três usavam o nosso
TWAP recalculado como final. Ficam no relatório como referência histórica,
sem uso em decisão.

**O modelo endgame continua válido e agora tem fundação.** Com `t < 60s`, a
fração `(60−t)/60` da média final está travada — e travada num número que
podemos LER, não estimar.

#### O alarme exigia 100% e contradizia este documento `[CORRIGIDO 2026-08-23]`

O alarme do M2.6 — que confere, a cada backtest, se τ=0 ainda explica as
resoluções — nasceu exigindo consistência **1.0**. Isso contradizia o critério
de sucesso escrito acima, e a contradição só apareceu quando a amostra ficou
grande o bastante.

**A medição que forçou a decisão.** 2026-08-23, sobre 5h limpas de 21/08,
**152 janelas elegíveis**, τ=0 em **0,9934** — UMA discordante. O relatório
imprimiu `MUDANÇA DE REGRA (...) NÃO opere com o resultado deste backtest`.

A janela: `btc-updown-5m-1787354400`, resolveu Up, e o final ficou **0,162 USD
abaixo** da âncora num preço de **78.640 USD**.

| Medida | Valor |
|---|---|
| Folga relativa | **2,06 ppm** |
| Em movimento do TWAP-60 do btc (~4 USD/s) | **40 ms** |
| Em intervalos de amostragem do feed (1,061 s) | **3,75% de UM** |
| Contra o limiar de "janela apertada" do projeto (2 bps) | **97× mais apertada** |

Nenhum carimbo desta gravação distingue esses dois valores. E τ=0 **continuou
sendo o argmax** — τ=−1 empatou, dentro da cadência do feed, e nenhum outro
τ ganhou. Se a âncora tivesse se deslocado, algum τ teria ganhado.

A família perdedora também continuou perdendo: `media_60s` marcou 0,9737
contra 0,9934 da vencedora. O módulo diz que o dia em que as duas empatarem é
o dia de desconfiar da gravação — não empataram.

**A propriedade que condena o 1.0 como regra:** exigir consistência perfeita
torna o alarme **mais provável quanto maior a amostra**. Com 24 janelas o 100%
sai fácil; com 152, uma janela na navalha é esperada — há ~10 janelas dentro
de 2 bps nesta amostra. Um detector de regressão que dispara mais com dado
melhor está invertido.

**Decisão:** o código passa a usar o limiar de **98%** definido acima, com
`MINIMO_PARA_ORCAMENTO = 100` para avisar quando o N não sustenta o orçamento.
O teste do M2.6 foi reescrito com este histórico, e 0,79 — a marca das
hipóteses nomeadas erradas — continua disparando o alarme.

**O alarme não foi desligado, foi calibrado.** Mudança de regra DERRUBA a
consistência; ela não a arranha em 2 ppm.

E porque §2b manda *"investigar as falhas UMA A UMA antes de subir N"*, o
relatório passou a trazer `discordantes_em_tau_verificado`: cada janela
discordante com `folga_e18`, `empate_exato` e `idade_da_ancora_ms` — os três
números que separam lixo residual de âncora errada. Sem isso a decisão acima
teria sido palpite.

### 2b-bis. Tolerância relativa no gate da âncora — LIMIAR ESCRITO ANTES DE RODAR

**Data: 2026-08-27. Nenhuma gravação foi reprocessada antes desta seção
existir** — este contêiner não tem as gravações, o que torna a ordem
verificável em vez de prometida.

**O problema.** O gate de "região de 100%" (`regiao_viavel_100pct`) é
binário: uma única janela discordante, de qualquer magnitude, apaga um τ da
região. No bloco de 5 h de 21/08, τ=0 explicou 151 de 152 janelas. A única
discordante:

| campo | valor |
|---|---|
| slug | `btc-updown-5m-1787354400` |
| `folga_e18` | 162.138.224.116.891.648 |
| âncora | 78.640,98 USD |
| folga relativa | **2,06e-6** (2,06 ppm) |
| `empate_exato` | false |
| `idade_da_ancora_ms` | 0 |

Dois centésimos de dólar num BTC de 78 mil, com âncora fresca. Não é mudança
de fonte: mudança de fonte quebraria dezenas de janelas com folgas grandes, e
a família rival (`final_media_60s`, 148 acertos) continuaria atrás — o que ela
continua.

**É o mesmo erro do M2.2.** Lá, um gate binário de 0,01 (um tick de mercado)
reprovou 200 de 200 janelas por medir *corrida* e não *corrupção*; a correção
foi o critério por conjunção de magnitude, persistência e fração. Aqui a
correção análoga é tolerância **relativa**: uma folga infinitesimal não é
evidência contra a âncora nem a favor — é ausência de evidência, e o lugar
dela é fora do denominador.

**Por que isto NÃO duplica o `LIMIAR_CONSISTENCIA` de 98%.** Os dois cobrem
lixos diferentes, e é por isso que convivem:

- O orçamento de 98% é **agregado** e cobre *lacuna de stream* e *empate
  mal-carimbado*. Ele absorveria também uma mudança de regra que atingisse
  1% das janelas — com folgas enormes — sem distinguir.
- A tolerância relativa é **por janela** e cobre só *magnitude
  infinitesimal*. Ela é incapaz de absorver folga grande, por construção.

Um filtra por quantidade, o outro por tamanho. Trocar um pelo outro perderia
metade da cobertura.

**O limiar: 1e-5 (10 ppm).** A faixa defensável tem chão e teto medidos:

- **Chão — 2,06e-6**: a folga observada, o único ruído real que temos.
- **Teto — ~5,4e-5**: o que UM intervalo de amostragem do feed produz. O
  TWAP-60 do btc anda ~4 USD/s e o p50 do intervalo é 1,061 s, então dois
  pontos vizinhos do stream diferem por ~4,24 USD em 78.640 — 5,4e-5. Abaixo
  disso, "a âncora está errada" é indistinguível de "amostramos o tick ao
  lado", e o dado não tem como decidir.

**1e-5 fica dentro da faixa**, 5× acima do ruído observado e 5× abaixo do
limite de resolução do feed. Duas referências externas confirmam a folga:
o próprio projeto chama de "janela apertada" 2 bps = 2e-4 em
`engine/anchor.py` — 20× mais frouxo que este limiar — e uma âncora de fonte
diferente daria folgas de 1e-3 ou mais, 100× acima.

**O que o limiar NÃO pode fazer, e como saber se ele afrouxou.** Se as 4
falhas de `final_media_60s` forem TODAS absorvidas, o limiar está frouxo
demais — porque a `media_60s` é a família *reconhecidamente errada*, e um
limiar que a promove a perfeita apagou a diferença que a varredura existe
para medir. Isso é critério de rejeição, escrito aqui antes de existir
resultado, e há teste travando o caso.

**Como conferir depois com número.** A varredura passa a reportar
`distribuicao_das_folgas_relativas` — histograma em décadas de ppb sobre
TODAS as janelas avaliadas, não só as discordantes. Se a massa se acumular
perto do limiar, ele está no lugar errado, e a revisão terá dado em vez de
opinião.

### 2d. Protocolo de remediação do 1.1 — ESCRITO ANTES DE RODAR (2026-08-27)

O segundo veredito localizou a causa do TAKER no **1.3**: erro de calibração
0,0694 contra uma barreira de taxa de 0,0175. A varredura offline indicou que
o erro é de **escala** — encolher a previsão em direção a 0,5 derruba o ECE em
todos os baldes. Este protocolo testa se a correção de escala devolve o 1.1.

Ele é escrito **antes de rodar** porque o risco aqui não é errar a conta: é
rodar variações até uma dar positiva. Com o protocolo registrado, um resultado
bom fora dele não conta.

**Ajuste (fora da amostra).** O fator sai de `--desde 2026082100 --ate
2026082322`, período ANTERIOR ao avaliado. Fator ajustado no dia 24 e aplicado
ao dia 24 é in-sample e não sustenta veredito — nem a favor, nem contra.

O `22` na hora final é deliberado, e vale explicar porque a primeira redação
deste protocolo escreveu `23` e estava **errada**. `--desde/--ate` filtram
**arquivos**, não janelas, com margem de ±1 h (`arquivos_na_fatia`): com
`--ate ...23` o limite superior vira 24/08 00:00 e a **hora 00 do dia 24 entra
inteira no ajuste** — o fator seria ajustado sobre uma hora do próprio dia
avaliado. Com `22`, o último arquivo lido é o das 23 h de 23/08 e nenhum
arquivo do dia 24 é aberto. A correção foi feita **antes de existir qualquer
número**: nenhuma rodada de ajuste havia terminado.

**O vazamento residual, medido em vez de negado.** Uma janela que ABRE às
23:5x de 23/08 resolve depois da virada, e o arquivo das 23 h é lido tanto por
esta fatia quanto pela do dia 24 (que o alcança pela margem de −1 h). Essas
janelas de fronteira aparecem nos dois conjuntos: são as que abrem na última
hora de 23/08, contra ~72 h de ajuste. É pequeno e é conhecido — e se o
resultado da remediação ficar a uma distância dessa ordem do limiar, ele não
decide nada.

**O ajuste roda dia a dia, e isso não muda o que ele mede.** Três dias numa
passada só multiplicam por três a RAM de pico da passada 2
(`memoria.projecao_de_pico` é linear no número de tokens), e a gravação de
21–23 vive em `~/pulsearb-dados`, não no diretório do dia 24. O passo 1 são
três rodadas — 21, 22 e 23 — somadas por `scripts/ajuste_do_encolhimento.py`:
as faixas são as mesmas, e cada previsão pesa uma, então a soma ponderada por
`n` dá o mesmo ajuste que a rodada única daria. O script também **executa a
regra de escolha abaixo**, em vez de deixá-la para o julgamento de quem lê a
tabela depois de ver os números.

**Qual fator, decidido agora.** O resumo reporta um fator por balde de tempo
restante; o backtest aplica UM fator global. A regra: **o fator do balde de
maior `n` entre os que caem na faixa operada (≤ 240 s)**. Os outros baldes
dessa faixa entram como *sensibilidade* — rodados e publicados, mas o veredito
é o do fator pré-registrado. Resultado positivo só num extremo da
sensibilidade, e não no fator da regra, **não conta como remediação**.

**Aplicação.** Dia 24 (`M2_24AGO`), faixa calibrada, entrada única — a mesma
configuração do bloco `encolhimento`, cuja rodada `sem_encolher` é o controle.
A leitura é pelo `scripts/resumo_m2.py --encolhido`, que aplica os MESMOS
critérios à variante: ler o JSON a olho é como o 1.3 saiu medido no campo
errado em dois vereditos seguidos.

**O que decide, com os limiares que já valem:**

| # | Critério | Exigido |
|---|---|---|
| 1.1 | PnL líquido | **positivo** |
| 1.2 | Trades | ≥ 200 |
| 1.3 | Calibração da variante | `erro_de_confiabilidade` < 0,05 em balde avaliável |

**A remediação é considerada bem-sucedida apenas se as três valerem juntas.**
1.3 sozinho não basta: encolher até a previsão virar ruído calibra e não
opera. 1.1 sozinho também não: PnL positivo com calibração ainda quebrada é
sorte de amostra, e foi assim que o +102,92 apareceu e caiu.

**Falseamento, dito agora.** Se o 1.1 continuar negativo com o 1.3 já
corrigido, a conclusão é que **o defeito não é de escala** — o preditor precisa
mudar, não a sua confiança —, e o M3 começa por aí em vez de por ajuste de
parâmetro. Este resultado é tão publicável quanto o outro.

**Uma ressalva que não é rodapé.** Nem o melhor resultado deste protocolo
autoriza dinheiro real: um único dia avaliado, com o fator vindo de três dias.
O que ele decide é qual trabalho vem a seguir.

### 2d-bis. A remediação FALSIFICOU a escala, e o diagnóstico de horizonte — ESCRITO ANTES DE RODAR (2026-08-29)

O protocolo 2d rodou. Fator **0,62** ajustado fora da amostra em 21–23 (balde
`240-120s`, o de maior `n`), aplicado ao dia 24. As três condições, lidas pelo
`resumo_m2.py --encolhido`:

| # | Exigido | Medido | |
|---|---|---|---|
| 1.1 | PnL positivo | **−62,49 USDC** | ✗ |
| 1.2 | ≥ 200 trades | 695 | ✓ |
| 1.3 | ECE < 0,05 em balde avaliável | **0,0211** em `240-120s` (14 faixas) | ✓ |

É, letra por letra, o **ramo de falseamento** que a 2d escreveu antes de
qualquer número: *1.3 corrigido, 1.1 negativo → o defeito não é de escala*. E o
mecanismo ficou visível na tabela de confiabilidade do balde operado:

- **O erro troca de sinal.** Nas faixas de baixa confiança (0,15–0,50) o
  preditor é *subconfiante* (realizado > previsto); nas de alta (0,55–0,85),
  *superconfiante*. Um fator único de encolhimento conserta uma metade e piora
  a outra — o motor rotula **MISTO e SEM ORDEM**. Encolher levou o `hit_rate`
  de 0,71 (controle) para **0,44**, abaixo do acaso, e o PnL de −53,28 (cru)
  para −62,49.
- **O ECE baixo é, em parte, artefato de massa.** 74 mil das ~80 mil previsões
  caem em dois baldes de ponta (0,15–0,20 e 0,80–0,85), ambos quase perfeitos;
  os baldes do meio, com erros de ±0,18, carregam centenas cada. O viés médio
  ponderado é −0,0074 ≈ 0.
- **Calibração ≠ edge.** Calibração é sobre todas as previsões; PnL é sobre o
  subconjunto que o gatilho escolhe por `|p' − preço|`. Um preditor calibrado
  no agregado ainda perde se a divergência modelo-vs-mercado não prevê o
  desfecho — que é o que 0,44 de `hit_rate` diz.

**A hipótese de escala está rejeitada, com causa.** O M3 muda o preditor, não a
confiança dele. A primeira pergunta a montante, escolhida por Paulo entre três
direções, é de **horizonte**: o edge não existe *em lugar nenhum*, ou existe
*num horizonte que a v1 não opera*?

**O diagnóstico, e a regra de leitura, fixados agora — antes dos números.**

O `por_bucket_tempo` do relatório principal **não responde** isso: a v1 entra
uma vez por janela varrendo da abertura ao fechamento, então opera no primeiro
instante elegível, quase sempre em `>240s`. Ele mede ordem de chegada, não
horizonte. O instrumento é a **`curva_de_horizonte`**: o preditor **cru** (sem
encolhimento — a escala está morta) forçado a operar em CADA banda de tempo
restante como sua própria rodada, restrita àquela faixa. As bandas são as
mesmas do `bucket_tempo`: `>240s`, `240-120s`, `120-60s`, `60-30s`, `<30s`.

Por banda medo: `trades`, `hit_rate`, `pnl_liquido_usdc`, `pnl_por_share`.

**Regra de leitura (registrada antes de ver as bandas):** uma banda **tem
edge** se, e só se, as três valerem juntas —

- `pnl_liquido_usdc` > 0, **e**
- `hit_rate` > 0,5, **e**
- `trades` ≥ **40** (`amostra_suficiente`).

O piso de 40 não é gosto: com `n ≥ 40`, a meia-largura do IC de 95 % do
`hit_rate` em p = 0,5 é 1,96·√(0,25/40) ≈ 0,155, então uma banda que passa de
0,5 com `n ≥ 40` não passou por sorte de amostra. Banda com PnL > 0 e
`hit_rate` > 0,5 mas `n` < 40 é **sinal fraco: publica como sensibilidade, não
decide** — a mesma disciplina do ajuste do encolhimento.

**Decisão do M3 (fixada agora):**

- **Se ALGUMA banda tem edge** → o defeito é de horizonte. O M3 passa a operar
  naquela banda e **remede 1.1–1.5 restrito a ela** (a política de entrada
  muda de "primeiro instante elegível" para "dentro da banda"). É o melhor
  desfecho possível deste diagnóstico.
- **Se NENHUMA banda tem edge** → o preditor cru não tem edge em horizonte
  nenhum. Somado à escala já rejeitada, sobra que **o sinal, e não sua
  confiança nem seu horizonte, é o defeito**. O M3 então **troca o preditor**
  (mira o meio da curva, 0,20–0,80, onde o erro é sem-ordem) **ou re-escopa**
  para outro mercado/horizonte — e a escolha entre esses dois é a próxima
  decisão pré-registrada, de Paulo, não minha.

**A ressalva de sempre.** Um dia avaliado. Uma banda que passe aqui autoriza o
próximo experimento (remedir 1.1–1.5 restrito a ela em dias independentes), não
dinheiro real. O diagnóstico decide **qual trabalho vem a seguir**, como a 2d.

#### Resultado da varredura e da remediação na banda (2026-08-29, 24 h, 126,7 M registros)

A `curva_de_horizonte` deu edge em **exatamente uma banda**, a **240-120s**
(n = 640, `hit_rate` 0,7063, `pnl_liquido_usdc` +2,7125, `pnl_por_share`
+0,000848), e em nenhuma outra: >240s −53,28, 120-60s −0,17, 60-30s −49,16,
<30s −5,41. Caiu, portanto, no galho **"ALGUMA banda tem edge → o defeito é de
horizonte"**. A rodada restrita à banda (`--tempo-restante-min 120
--tempo-restante-max 240`) confirmou o número por um segundo caminho: o
`faixa_de_tempo.comparacao.restrito` bate com a célula 240-120s da curva ao
centavo (640 trades, +2,7125, 0,7063). O edge é real e robusto à fiação.

**Mas o edge de direção não vira taker viável.** Remedindo 1.1–1.5 restrito à
banda:

| crit. | medido na banda 240-120s | veredito |
|---|---|---|
| 1.1 PnL @300 ms | +2,7125 USDC | ✅ |
| 1.2 trades | 640 (≥ 200) | ✅ |
| 1.3 calibração | melhor balde 0,0694; a própria banda 0,207; viés **MISTO e SEM ORDEM** | ❌ |
| 1.4 PnL @600 ms | **+1,3488 USDC** (remedido na banda) | ✅ |
| 1.5 profundidade p50 3t | melhor 128 USDC (300 s), < 200 exigidos | ❌ |

O 1.3 e o 1.5 reprovam por causas que **não são de horizonte nem de escala**,
e por isso não têm conserto nesta rota:

- **1.3** é defeito **estrutural do preditor**. Na banda ele despeja ~75 mil das
  ~79 mil previsões nos extremos (0–0,05 e 0,95–1,00) e erra o alvo em 0,207 de
  ECE; acerta a direção ~71 % das vezes, mas os *números de probabilidade que
  cospe são ruído*. O "SEM ORDEM" fecha a saída da escala: não há encolhimento
  que acerte todas as faixas (11 otimistas, 9 pessimistas). Confirma a 2d.
- **1.5** é teto de **capacidade** (profundidade de book), independente de banda
  — restringir o horizonte não cria liquidez. Só a `curva_de_capacidade`
  (`--varredura-de-tamanho`) poderia contestar o limiar, e o p50 de 128 contra
  200 não sugere que contestaria.

**Lacuna de medição encontrada, FECHADA e REMEDIDA.** O 1.4 lê
`sensibilidade_latencia.600ms`, e esse bloco — junto com `curva_de_edge` e
`curva_de_capacidade` — rodava a sua **própria** configuração só com threshold e
latência, **ignorando `--tempo-restante-*`**. Numa rodada restrita, o 1.1 saía da
banda e o 1.4 de `>240s`: dois critérios do mesmo relatório sobre populações
diferentes, sem aviso. Ou seja, a §2d-bis mandava remedir 1.4 na banda e o 1.4
**nunca era remedido** — ele publicava −54,3953 USDC, que são os 695 trades de
`>240s`, não os 640 da banda. Corrigido em `FaixaDeOperacao` (runner): os três
diagnósticos herdam a banda operada; rodada irrestrita fica idêntica à de antes
(travado em `test_m2_e2e`).

**Com a correção, o 1.4 PASSA na banda** (rodada `HORIZONTE_240_120_v2`), e a
sensibilidade de latência ganha uma forma que o número velho escondia:

| latência | trades | PnL USDC |
|---|---|---|
| 150 ms | 640 | +3,3119 |
| 300 ms | 640 | +2,7125 |
| 600 ms | 640 | +1,3488 |
| 1000 ms | 640 | +0,4736 |

**Decaimento monótono e positivo em toda a grade** — a assinatura de um edge de
direção real sendo corroído por latência, não de ruído. O `curva_de_edge`
restrito também passou a fazer sentido: positivo de 0,01 a 0,05 (melhor em 0,03,
+3,0489) e negativo a partir de 0,08. **Ressalva:** esse 0,03 foi escolhido
OLHANDO esta amostra; adotá-lo exige repetir em dia independente, senão é
sobreajuste — o threshold registrado segue 0,02.

**O placar final do taker na banda é 3 PASSA / 2 REPROVA** (1.1, 1.2, 1.4 ✅;
1.3, 1.5 ❌). Como o critério exige as CINCO, o veredito não muda — mas a causa
ficou mais nítida: o que reprova não é PnL nem latência, é **calibração** e
**capacidade**.

**Desfecho.** A banda 240-120s tem edge de direção — positivo em toda a grade de
latência e com 640 trades —, porém a rota **taker** reprova mesmo nela, em 1.3 e
1.5. E as duas causas são de natureza diferente do que a §2d-bis podia consertar:
calibração é **defeito do sinal** (o preditor acerta a direção e erra a
probabilidade) e profundidade é **teto de capacidade** (liquidez do book). Nenhuma
das duas se resolve escolhendo horizonte ou escala, que eram as duas hipóteses
pré-registradas. O taker está esgotado como rota nestes mercados. A próxima
decisão pré-registrada — encerrar o taker no registro, ou virar para a rota
maker (hoje travada em 1.6 NÃO AVALIÁVEL e 1.10 REPROVA, docs bloqueadas) — é de
Paulo, não minha.

### 2d-ter. O 1.3 tinha causa mecânica, e ela era um defeito — ESCRITO ANTES DE RODAR (2026-08-29)

A §2d-bis fechou dizendo que o 1.3 é "defeito estrutural do preditor" e que a
saída seria modelo novo. **Estava certa no diagnóstico e errada na conclusão:**
o preditor não precisa ser trocado, precisa ser consertado. A superconfiança
tem uma causa única, mecânica e localizável numa linha.

#### O defeito

`prob_up_twap` calculava a variância do TWAP de fechamento assim:

```
m = min(seconds_left, 60)
var_futuro = σ² · S² · k(m)
```

`k(m)` é a variância da média de `m` passos de uma caminhada **que começa
agora**. Isso está certo enquanto a janela de fechamento já começou, ou seja
`t ≤ 60`. Com `t > 60` ela ainda NÃO começou: as 60 amostras que formam o TWAP
final acontecem entre `t − 60` e `t` segundos daqui, e o preço caminha os
`t − 60` segundos anteriores antes da primeira delas. Esse deslocamento é
**comum às 60 amostras** — entra inteiro na variância da média, não dividido
por 60.

O fator correto é `(t − 60) + k(60)` para `t > 60`, e continua sendo `k(t)`
para `t ≤ 60`. Medido:

| `seconds_left` | fator usado | fator correto | desvio usado / real |
|---|---|---|---|
| 30 s | 9,51 | 9,51 | 1,00 |
| 60 s | 19,50 | 19,50 | 1,00 |
| 120 s | 19,50 | 79,50 | **0,50** |
| 240 s | 19,50 | 199,50 | **0,31** |
| 300 s | 19,50 | 259,50 | **0,27** |

Ou seja: acima de 60 s o desvio ficava **congelado no valor de 60 s**, qualquer
que fosse o horizonte. Na banda operada 240-120 s o modelo usava entre 31 % e
50 % do desvio real.

#### Por que isto explica exatamente o que o 1.3 mediu

Com o desvio 2 a 3,6 vezes menor que o real, o z-score infla na mesma
proporção e `P(Up)` satura. Numa grade de desvios spot-âncora de −50 a +50 bps:

| `seconds_left` | previsões nos extremos (< 0,05 ou > 0,95), de 101 |
|---|---|
| 120 s | 72 hoje · 42 corrigido |
| 240 s | 72 hoje · **8** corrigido |
| 300 s | 72 hoje · **0** corrigido |

O modelo despejava a mesma proporção nos extremos em **qualquer** horizonte,
porque o σ não dependia do horizonte. É a assinatura das ~75 mil de ~79 mil
previsões nos extremos que a §2d-bis registrou.

**E fecha a conta do "MISTO e SEM ORDEM" da §2d.** O tamanho do erro depende de
`seconds_left` — 2× a 120 s, 3,2× a 240 s. Um fator único de encolhimento não
podia acertar todas as faixas de probabilidade ao mesmo tempo, porque cada
faixa mistura horizontes com graus diferentes de superconfiança. A §2d não
falhou por falta de escala: falhou porque o erro não era de escala. **O
encolhimento seguiu corretamente rejeitado, e agora se sabe por quê.**

#### Por que isto NÃO é ajuste post-hoc

Esta é a pergunta que decide se o conserto vale ou se é sobreajuste com outro
nome. Três razões, todas verificáveis sem olhar resultado nenhum:

1. **A correção vem da matemática do próprio modelo,** não de um número que se
   queria melhorar. O docstring do `twap.py` sempre disse que a variância é a
   da média das amostras futuras; a implementação é que largava o
   deslocamento até o início da janela.
2. **É conferível contra a definição.** `test_variancia_do_twap_bate_com_a_definicao`
   soma o coeficiente de cada choque de 1 s — a definição, não a forma
   fechada — em 11 horizontes, e cobra a igualdade. Com o defeito de volta,
   falha em 120, 180, 240, 300 e 600 s e passa em todos os `t ≤ 60`.
   Confirmado também por Monte Carlo de 200 mil caminhos.
3. **Nenhum parâmetro foi escolhido.** Não há fator, limiar ou constante nova.
   A fórmula corrigida é idêntica à antiga para `t ≤ 60`.

#### O que esta correção invalida — e é quase tudo

Registrado antes de rodar, para que não haja escolha depois:

**Todos os números de 1.1 a 1.4 e do 2.3 caem.** `P(Up)` muda para todo
`t > 60`, o que inclui o balde `>240s` inteiro **e a banda operada 240-120 s
inteira**. Muda a probabilidade, muda o edge, muda quais instantes passam do
threshold, muda quais trades existem. Não há como reaproveitar nenhuma
medição.

**Em particular, o +2,7125 da banda não é mais um resultado.** A banda
240-120 s foi *escolhida* por uma `curva_de_horizonte` calculada com o
preditor defeituoso. A escolha herda o defeito. A banda pode continuar sendo a
melhor, pode deixar de ser, e pode deixar de haver banda alguma.

**O que NÃO muda:** o 1.5 (profundidade) mede estrutura de book e não passa
pelo preditor — o teto de 128 USDC contra 200 continua de pé. O 1.7, 1.8 e 1.9
são da rota maker e não usam `prob_up_twap`. A âncora τ=0 é anterior ao
modelo. E o `hourly.py` está correto: usa `σ·√t` com o horizonte inteiro.

#### Critérios da rodada de remediação — ESCRITOS AGORA, ANTES DE RODAR

Sobre a mesma gravação de 2026-08-24, irrestrita, para ser comparável ao
`M2_24AGO.json`:

1. **O 1.3 passa** se `calibracao_avaliavel` for true **e**
   `erro_de_confiabilidade` < 0,05 em ao menos um balde. Sem emenda de limiar
   depois de ver o número.
2. **Concentração nos extremos.** As duas faixas extremas da
   `curva_de_confiabilidade` devem sair de ~95 % das previsões para **menos de
   60 %**. Se continuarem acima disso, o conserto não pegou a causa e a §2d-bis
   volta a valer como estava.
3. **O viés deve ganhar ORDEM.** Se o `erro_de_confiabilidade` cair mas o viés
   seguir MISTO e SEM ORDEM, sobra defeito de sinal além deste, e aí sim é
   modelo novo.
4. **A banda é re-derivada do zero.** A `curva_de_horizonte` roda de novo e a
   banda operada é a que ELA apontar, não a 240-120 s por herança. Se nenhuma
   banda tiver edge, o resultado é esse.
5. **O PnL não é critério de sucesso do conserto.** Uma variância maior
   aproxima as previsões de 0,5, o que **reduz** o edge medido e pode derrubar
   1.1 e 1.4. Isso seria um resultado honesto, não um fracasso do conserto: o
   edge anterior podia ser confiança que o modelo não tinha como ter. O
   conserto se julga pelos itens 1 a 3.
6. **Um dia continua não sendo veredito.** O que passar aqui autoriza repetir
   em dia independente, não dinheiro real.

**O galho que este experimento decide.** Se 1.3 passar e a banda sobreviver, a
rota taker volta a ter 4 ou 5 dos 5 e o 1.5 vira o único bloqueio — que é teto
de capacidade e não de sinal. Se 1.3 passar e o edge sumir, o veredito fica
mais limpo do que era: não havia borda, havia superconfiança. Se 1.3 não
passar, a §2d-bis está confirmada e a conclusão é modelo novo.

#### O defeito maior, achado na revisão do PR #44 — a RODADA ACIMA ESTÁ SUSPENSA

O conserto da variância está certo e continua no lugar, mas ele é **necessário
e não suficiente**. A revisão automática apontou a premissa que eu não tinha
conferido, e ela derruba mais coisa do que o termo que faltava.

**O fato, verificável em três linhas de código.** O que alimenta o modelo não é
preço bruto — é o próprio `twap_sixty`, que já vem suavizado:

```
backtest/runner.py:254-258   for ts_ns, preco_spot in stream:  vol.update(...)  twap.update(...)
backtest/__main__.py:509,538 if tick.topic == TOPIC_TWAP_60:   self.streams[tick.asset].append((ts, tick.price))
live/precos.py:113-116       self.twap.update(preco, ts_ns);   self.vol.update(preco, ts_ns)
```

A variável se chama `preco_spot`, e o nome é parte da causa: ela carrega o
valor do stream `crypto_prices_twap_sixty`, não o spot. O `sigma_1s` é a
volatilidade da série **já suavizada**, e o `spot` passado ao `prob_up_twap` é
um ponto dessa mesma série.

**E a §13.8 do `API_NOTES.md` já tinha resolvido isso — em 2026-08-21, com
critério escrito antes e 152 janelas.** Ela diz, literalmente:

> Janela TWAP resolve **Up** se o valor do stream `crypto_prices_twap_sixty`
> **no instante do fechamento** for ≥ o valor do mesmo stream **no instante da
> abertura**.
>
> **Não calcule média de 60 s. Nenhuma.** O tópico já é a média de 60 s da
> Chainlink, entregue pronta.

E mediu as duas famílias: `final_stream_no_fechamento` deu **1,0** de
consistência; `final_media_60s` — média de 60 s recalculada por nós — deu
**0,9648**, e nenhum τ chegou a 1,0.

**O `prob_up_twap` faz exatamente o que a §13.8 proíbe.** `variance_factor` e
`locked_mean_and_weight` modelam a liquidação como a média de 60 amostras
futuras de um preço bruto. A liquidação real é **um ponto** de uma série que
já é média. O modelo, portanto, não erra só a magnitude da variância: erra o
observável.

**A intuição do travamento é física e verdadeira — e não é computável com o
dado que temos.** A 30 s do fechamento, o valor de liquidação cobre
`[fecha−60, fecha]`, e metade disso já aconteceu: isso é real. Mas a parte
travada é a média do **preço subjacente** naquele intervalo, e nós não
observamos o subjacente. Fazer a média das nossas amostras de `twap_sixty`
sobre os últimos 30 s é média de média, que é outro número. O travamento
existe; o `locked_mean_and_weight` não o mede.

**Consequência imediata, e é por isso que esta subseção existe:** a rodada de
remediação registrada acima **não deve ser executada**. Ela levaria ~3,5 h
para medir a calibração de um modelo ainda mal especificado, e o resultado não
julgaria nem o conserto nem o preditor. Os seis critérios continuam válidos
como estão escritos, para quando o observável estiver certo — **não foram
afrouxados nem reescritos depois de ver número nenhum, porque não há número.**

**O que o conserto da variância continua valendo.** Ele é uma derivação correta
conferida contra a definição, e a rodada futura precisa dele de qualquer jeito
para os horizontes acima de 60 s. Fica. O que muda é a alegação: ele era "a
causa" do 1.3 e passa a ser **uma** das causas, e provavelmente não a maior.

#### O caminho proposto — medir a variância em vez de derivá-la

Registrado como proposta, não como decisão tomada.

Dado o observável certo, o modelo vira `P(T_fecha ≥ âncora)` com `T` a própria
série `twap_sixty`. O que falta é uma coisa só: **V(t) = Var(T_{agora+t} −
T_agora)**, a variância de transição da série no horizonte `t`.

Ela não precisa ser derivada, e derivá-la é justamente o que produziu os dois
defeitos acima. Ela é **medível direto da gravação** — é a mesma metodologia
que a §13.8 usou para a âncora: engenharia reversa sobre o dado gravado, em
vez de suposição sobre o processo. E ela captura de graça o colapso de
incerteza perto do fechamento, sem precisar decompor em travado e futuro.

Três propriedades que a medição tem de exibir para a proposta se sustentar, e
que a falsificam se não exibir:

1. `V(t)` cresce com `t` de forma monótona.
2. Para `t` grande, `V(t)` cresce aproximadamente linear em `t` — é o regime de
   caminhada aleatória do subjacente, que a suavização de 60 s não altera.
3. Para `t` pequeno, `V(t)` cresce **mais devagar que linear** — é a assinatura
   da suavização, e é a versão medida do "travamento".

Se as três aparecerem, `V(t)` substitui `variance_factor` e o
`locked_mean_and_weight` sai do caminho da decisão. Se não aparecerem, a
suposição de caminhada aleatória do subjacente também está errada, e aí a
conclusão da §2d-bis — modelo novo — volta a valer, agora por um motivo
medido.

#### RESULTADO da medição de V(t) — 2026-08-29, 24 h de 24/08, 651.995 ticks

**As três propriedades apareceram, nos oito ativos, sem exceção.**
`relatorios/VARIANCIA_24AGO.json`, com `sem_amostra_para_avaliar: 0` e
`ticks_sem_timestamp_de_origem: {}` — nenhum ativo ficou sem veredito e nenhum
tick foi descartado por falta de relógio de origem.

| ativo | avaliável | linear no longo | há suavização | fator |
|---|---|---|---|---|
| bnb | ✅ | ✅ | ✅ | 34,84 |
| btc | ✅ | ✅ | ✅ | 36,10 |
| doge | ✅ | ✅ | ✅ | 38,81 |
| eth | ✅ | ✅ | ✅ | 34,13 |
| hype | ✅ | ✅ | ✅ | 45,65 |
| sol | ✅ | ✅ | ✅ | 35,79 |
| xrp | ✅ | ✅ | ✅ | 34,58 |
| zec | ✅ | ✅ | ✅ | 38,43 |

`unanime: true`, fator entre **34,1 e 45,6**. Oito ativos concordando não é
coincidência de um feed.

A curva do btc, que é o ativo operado:

| t (s) | V(t) | V(t)/t | razão contra o modelo |
|---|---|---|---|
| 1 | 2,352e-10 | 2,352e-10 | — (referência) |
| 2 | 6,766e-10 | 3,383e-10 | 11,51 |
| 5 | 3,878e-09 | 7,756e-10 | 13,74 |
| 10 | 1,484e-08 | 1,484e-09 | 22,13 |
| 30 | 1,162e-07 | 3,872e-09 | 51,96 |
| 60 | 3,690e-07 | 6,151e-09 | **80,45** |
| 120 | 8,898e-07 | 7,415e-09 | 47,59 |
| 180 | 1,368e-06 | 7,598e-09 | 41,68 |
| **240** | 1,844e-06 | 7,684e-09 | **39,30** |
| 300 | 2,356e-06 | 7,853e-09 | 38,60 |
| 600 | 5,094e-06 | 8,491e-09 | 38,71 |

**Propriedade 1 — monótona:** V(t) cresce de 2,35e-10 a 5,09e-6 sem uma
inversão.

**Propriedade 2 — linear no regime longo:** V(t)/t em 240, 300 e 600 s dá
7,684 · 7,853 · 8,491 (e-9), variação de **1,10×** entre os extremos. É o
regime de caminhada aleatória do subjacente, que a suavização de 60 s não
altera. Passa com folga no limite de 1,5.

**Propriedade 3 — sublinear no curto:** V(1)/1 é **36× menor** que V(600)/600.
É a marca da suavização, e é a versão MEDIDA do travamento que o
`locked_mean_and_weight` tentava calcular e não podia.

#### O tamanho do erro, agora medido em vez de suposto

Na banda operada, o modelo subestima a variância em **39 a 48 vezes** —
**6,3 a 6,9 vezes no desvio-padrão**:

| `seconds_left` | razão na variância | erro no desvio |
|---|---|---|
| 120 s | 47,6× | 6,9× |
| 240 s | 39,3× | 6,3× |
| 300 s | 38,6× | 6,2× |

Com o desvio 6 vezes menor que o real, o z-score infla 6 vezes e `P(Up)`
satura em 0 e 1. **É a explicação completa da superconfiança que o 1.3 mede** —
e o conserto da variância da §2d-ter, sozinho, cobria só uma fatia dela.

**São três erros compostos, e a medição os resolve de uma vez:**

1. O `variance_factor` aplica uma REDUÇÃO por média que não existe na
   liquidação real. A §13.8 já dizia: a janela resolve por um ponto, não por
   uma média que nós calculemos. Por isso a razão já é 11,5× a **2 segundos**,
   onde o termo de espera da §2d-ter nem entra: ali o modelo acha que a
   variância é 0,25 de um movimento de 1 s, quando é 2,9 vezes ele.
2. Faltava o tempo de espera antes da janela de fechamento (§2d-ter). O pico de
   **80×** em 60 s é exatamente onde `k(60)` é mais errado.
3. O `sigma_1s` é medido sobre a série já suavizada, e vale ~1/36 da
   volatilidade do subjacente.

#### A restrição que sobra, e ela é de método

A curva foi medida em **24/08**, que é o mesmo dia que o veredito avalia. Usar
os dois no mesmo dia é ajuste in-sample — exatamente o que a §2d proibiu para
o fator de encolhimento ("o fator vem de calibração medida em período ANTERIOR
ao avaliado").

Então a `V(t)` que entra no modelo tem de ser medida em **outro dia**. Há 23 h
de 23/08 gravadas. A regra registrada agora, antes de rodar: **mede em 23/08,
avalia em 24/08.** A curva de 24/08 fica como controle — se as duas
discordarem muito, a própria estabilidade de V(t) entre dias vira pergunta
aberta, e isso é resultado, não obstáculo.

#### E ela FOI medida — a curva de 23/08, e uma previsão antes da rodada

> **Ordem de leitura, porque este documento é o registro do experimento:**
> tudo acima desta linha foi escrito ANTES de a curva de 23/08 existir. O
> que vem a seguir é o resultado dela, mais uma previsão feita antes de a
> rodada de remediação existir. A cronologia é o que dá valor ao
> pré-registro; misturá-la apagaria a prova de que a regra veio primeiro.

*(A previsão abaixo está no commit `f59dc79` deste repositório, e o resultado
só entrou em `4760669`, dois commits depois — há revisão do repositório que
contém a previsão sem o desfecho. A ordem das linhas é conveniência de leitura;
a prova é o histórico.)*

`relatorios/VARIANCIA_23AGO.json`, 653.680 ticks, `dia_medido: 20260823`, oito
ativos avaliáveis e unânimes, nenhum tick sem relógio de origem, fator de
suavização entre **33,5 e 38,5** (contra 34,1 a 45,6 no dia 24). Dois arquivos
do dia 23 vieram com o gzip quebrado e foram abandonados; sobraram ~81,7 mil
amostras por ativo, contra ~81,4 mil no dia 24.

**Comparando as duas curvas do btc, horizonte a horizonte:**

| t (s) | V(23)/V(24) |
|---|---|
| 1 | 0,463 |
| 2 | 0,499 |
| 5 | 0,498 |
| 10 | 0,505 |
| 30 | 0,517 |
| 60 | 0,521 |
| 120 | 0,518 |
| 180 | 0,524 |
| 240 | **0,541** |
| 300 | 0,534 |
| 600 | 0,456 |

Média **0,507**, e a razão varia apenas **1,19×** ao longo de 600× de faixa de
horizonte. Isso é um achado por si:

> **A FORMA de V(t) é estável entre dias; o NÍVEL não.** O dia 23 foi cerca de
> metade do dia 24 em variância, na mesma proporção em todo horizonte.

**A previsão, registrada AGORA, antes de a rodada existir.** Usando a curva de
23/08 para avaliar 24/08, o modelo vai subestimar a variância em ~1/0,51 ≈
**1,97×** — ou seja, ~**1,40×** no desvio-padrão. Isso é uma melhora enorme
sobre os 6,3× do modelo derivado, mas não é zero, e ela empurra na direção da
superconfiança.

**O que essa previsão torna falsificável:**

- Se o 1.3 passar, o nível residual não bastou para reprovar, e o conserto
  está completo para o que o critério cobra.
- Se o 1.3 reprovar com ECE compatível com ~1,4× de excesso de confiança, o
  diagnóstico é **de NÍVEL, não de forma** — e o passo seguinte é um estimador
  de nível (a curva dá a forma, a volatilidade recente dá a escala), não um
  modelo novo.
- Se o 1.3 reprovar com viés MISTO e SEM ORDEM de novo, a forma também está
  errada, e aí sim a conclusão da §2d-bis volta inteira.

**Não vou ajustar o nível antes de rodar.** A curva de 24/08 existe e daria um
fator melhor — e usá-la seria exatamente o ajuste in-sample que esta seção
inteira existe para impedir. O estimador de nível, se for preciso, é o próximo
experimento, com protocolo próprio.




As três são julgadas em CÓDIGO (`veredito_da_curva`), não a olho na tabela, e
duas ressalvas ficam registradas porque a revisão do PR #44 as cobrou:

- **Sem os dois regimes medidos não há veredito.** Se a gravação não alcança
  dois horizontes longos, `ha_suavizacao` sai **nulo**, e não falso. "Não deu
  para medir" saindo como "medi e não há" é o defeito do
  `cobertura_da_gravacao` que reportava 1,0 num relatório com 3.601 s de
  silêncio, e o do `erro` que um preditor constante gabaritava. A concordância
  entre ativos também não conta quem não foi avaliado.
- **A propriedade 2 é o que separa suavização de outro processo.** Uma série
  com momento (retornos autocorrelacionados) tem `V(t)/t` crescendo em todo
  horizonte e daria razão longo/curto acima de 400 sem ter nada de suavizada.
  O limite conhecido: um processo de memória curta (~100 s) já está no regime
  assintótico entre 240 e 600 s e passa na linearidade. A checagem separa o
  que se parece com suavização **no horizonte medido**; não prova a origem
  física do achatamento.

E a medição usa o relógio do SERVIDOR (`src_timestamp_ms`), não o da chegada
local — a conta é toda sobre distância entre observações, e `ts_wall_ns`
carrega latência de rede, pausa do processo e ajuste do relógio da máquina.
Tick sem timestamp de origem é descartado e CONTADO no relatório.

#### O RESULTADO — 1.3 passa, e a borda some junto (2026-08-30)

> Escrito DEPOIS da rodada. Tudo acima desta linha é anterior a ela.

`relatorios/M2_24AGO_MEDIDO.json`: curva de 23/08 sobre o dia 24/08, a regra
da seção cumprida em código (`modelo_de_variancia.medida: true`,
`dia_medido: "20260823"`, oito ativos, guarda de in-sample satisfeita porque o
dia da curva é estritamente anterior ao primeiro dia avaliado). As portas
fail-closed não engoliram nada em silêncio: `janelas_sem_curva_de_variancia` e
`janelas_de_jogo_sem_curva` vazios, e `instantes_alem_da_curva` contando o que
ficou fora do alcance de 600 s (btc 89.517, eth 62.303) em vez de extrapolar.

**O 1.3 passa, e passa nos cinco buckets:**

| bucket | `erro_de_confiabilidade` | faixas ocupadas |
|---|---|---|
| <30s | **0,0126** | 20 |
| 60–30s | 0,0285 | 20 |
| 240–120s | 0,0319 | 20 |
| 120–60s | 0,0452 | 20 |
| >240s | 0,0493 | 20 |

Todos com `calibracao_avaliavel: true`. Contra **0,207** na banda operada com o
modelo derivado — uma redução de 4 a 16 vezes. O critério pede um bucket
avaliável abaixo de 0,05; saíram cinco. **A causa mecânica do 1.3 era o
defeito de variância, e a medição o fecha.** É a primeira das três previsões
registradas acima: o nível residual de ~1,40× no desvio não bastou para
reprovar.

**E a borda desapareceu no mesmo movimento.** 688 trades, PnL **−67,27**, hit
0,4172, e `curva_de_horizonte` com `bandas_com_edge: []`:

| banda | PnL |
|---|---|
| >240s | −67,27 |
| 240–120s | −87,56 |
| 120–60s | −113,64 |
| 60–30s | −31,90 |
| <30s | −22,54 |

Nenhuma positiva. O +2,7125 que a §2d-bis registrou na banda de horizonte não
sobrevive à correção do preditor.

**A sensibilidade à latência inverteu.** Com o modelo derivado, o PnL decaía
monotonicamente com a latência, e eu citei esse decaimento como evidência de
borda direcional real. Com o modelo medido, o PnL **melhora** com latência
(−67,94 a 150 ms, −55,78 a 1000 ms). O que isso estabelece com segurança é que
**a evidência que eu invocava antes não sobrevive**: era o mesmo instrumento,
lido nos dois sentidos, e ele agora aponta para o lado oposto.

**O que isso NÃO estabelece, e a revisão do PR #47 pegou.** A varredura de
latência não é um teste de direção. Em `_tentar_entrada` o sinal é calculado
em `t` e fica FIXO; o que a latência muda é o book usado no fill,
`timeline.at(t + latência)`. Isso mexe em duas coisas de uma vez: o preço de
entrada, e se a ordem preenche. Se o ask melhora no intervalo, o PnL sobe com
latência mesmo com sinal direcional; e fill que falha empurra a entrada para
outro instante, mudando a COORTE de trades entre os cenários. Uma inclinação
positiva, sozinha, não separa "não há direção" de "a execução ficou mais
barata" nem de "as coortes são outras".

**Então o que carrega a conclusão de ausência de borda é o resto**, não a
inclinação: PnL negativo nas **cinco** bandas de horizonte, e `hit_rate` de
0,4172 — abaixo de 0,5 no lado que o modelo escolheu. **O teste direto continua
por fazer**, e fica registrado como próximo passo: acurácia direcional ou
markout sobre coorte pareada entre latências (o relatório já publica `trades` e
`hit_rate` por cenário em `sensibilidade_latencia`; falta a comparação por
coorte). Até ele existir, a inclinação da latência é indício, não prova.

> **O instrumento foi construído em 2026-08-31 — e ainda não foi rodado sobre
> gravação real.** `direcao_sem_fill` registra, para cada instante em que o
> gatilho disparou, a direção escolhida e o resultado da janela — **antes de
> qualquer gate de execução**, então nem book, nem latência, nem teto de
> entradas entram. A coorte é fixa por construção, e há teste travando que ela
> sai **idêntica** a 150 ms e a 1000 ms enquanto o `hit_rate` varia: é
> exatamente a comparação pareada que faltava.
>
> Publica duas contagens. `por_sinal` usa todos os instantes; `por_janela` usa
> o primeiro de cada janela e **é a que decide** — dentro de uma janela os
> instantes dividem âncora, preço e resultado, então são a mesma observação
> repetida, e contá-los como independentes encolhe o p-valor sem informação
> nova ter entrado. O p-valor é binomial bilateral, e bilateral de propósito:
> 0,4172 em amostra grande é informação apontando para o outro lado, e um teste
> unilateral a esconderia.
>
> **O que ele NÃO responde:** se a estratégia lucra. Direção acima de 0,5 com
> custo de execução maior que a margem continua perdendo — por isso ele é
> diagnóstico e não um décimo primeiro critério. Os dez foram escritos antes
> dos números e não ganham companhia depois.
>
> **A primeira rodada real justificou o desenho, e por pouco.** Sobre 1 h de
> 2026-08-24 (20:00 UTC, 4 janelas), as duas contagens deram respostas
> **opostas**:
>
> | contagem | n | janelas | acurácia | p | difere de 0,5 |
> |---|---|---|---|---|---|
> | `por_janela` | 4 | 4 | 1,000 | 0,134 | não |
> | `por_sinal` | 1.141 | **4** | 0,321 | 1,4e−33 | "sim" |
>
> Publicado sozinho, o `por_sinal` lê-se como *sinal forte, na direção
> contrária, com p praticamente zero*. São **quatro janelas**. O primeiro sinal
> de cada uma acertou; ao longo delas o gatilho virou de lado e passou a errar,
> e cada instante virou uma "observação".
>
> Duas correções entraram por causa disso: `janelas_distintas` passou a sair
> **ao lado de `n`** em cada contagem, e o `resumo_m2.py` imprime um aviso
> quando `n ≥ 10 × janelas_distintas` — tirado do dado, não escrito como
> rodapé fixo. Sem isso, o número mais chamativo do bloco era também o mais
> enganoso.
>
> **E o que essa rodada NÃO mede:** nada sobre borda. Quatro janelas de uma
> hora não sustentam veredito — ela existiu para validar o instrumento, e é só
> isso que ela estabelece.

### §2d-quater. O teste rodou, e a causa da reprovação mudou de nome

`relatorios/M2_DIRECAO_20260824.json` — 24 h de 2026-08-24, 126,7 M registros,
**688 janelas independentes** (uma observação por janela, sem repetição).

| medida | valor | |
|---|---|---|
| `direcao_sem_fill.por_janela` | **0,4157** | n=688, janelas=688, p=**1,16e−05** |
| `direcao_sem_fill.por_sinal` | 0,3101 | n=149.448 sobre as mesmas 688 janelas |
| taxa-base (Up vence) | **0,5043** | 247.306 previsões |
| calibração, cinco baldes | ECE **0,0126 a 0,0493** | 20 faixas ocupadas em todos |

**Não é ausência de sinal. É seleção pior que o acaso.** A pergunta que este
instrumento existia para responder era binária, e a resposta veio do lado que
eu não tinha antecipado: a direção escolhida **erra sistematicamente**, com
significância, sobre a coorte que não muda com o fill.

**E o preditor não é o culpado — ele está calibrado.** Em 247 mil previsões o
`P(Up)` médio fica entre 0,4945 e 0,5012 contra realizado de 0,4984 a 0,5128, e
o ECE não passa de 0,0493 em balde nenhum, com as 20 faixas ocupadas. Um modelo
que erra a probabilidade não produz esses números.

Quem escolhe é a regra `edge = prob − preço > threshold`. Ela seleciona 688
momentos de 247 mil e acerta 0,4157, quando **sortear daria 0,50**. A regra
não é neutra: ela é anti-informativa.

**A leitura econômica — que é inferência, e está marcada como tal.** Quando o
modelo discorda muito do preço, a explicação mais barata é que o mercado sabe
algo que o modelo não sabe, e não que apareceu oportunidade. Entrar exatamente
onde a discordância é maior é escolher a dedo os casos em que o modelo falha.
É seleção adversa, e o formato do número — calibrado no agregado, ruim no
selecionado — é a assinatura dela. **Não está medido que a causa seja essa.**

**O que muda de lugar:** o conserto do 1.1/1.4 não é trocar o preditor.
Trocá-lo por outro igualmente calibrado daria o mesmo resultado, porque o
defeito está em QUANDO se entra, não em QUANTO se prevê.

**Três coisas que este número NÃO estabelece**, e nenhuma é detalhe:

1. **Que inverter a regra dá lucro.** Não foi testado. Inverter é comprar o que
   o modelo acha caro, e isso não tem razão a priori de funcionar — a
   simetria de uma regra ruim não é uma regra boa.
**O que isso significa, na regra que eu mesmo registrei antes de rodar:** "se
1.3 passar e o edge sumir, o veredito fica mais limpo do que era: não havia
borda, havia superconfiança". Foi essa a ramificação que ocorreu.

**Mas o alcance da conclusão é menor do que a frase sugere, e vale delimitar.**
O que está medido é: **a regra corrigida perde neste dia**, em todas as cinco
bandas, com `hit_rate` 0,4172. Não está medido que os 640 trades antigos não
carregavam informação. As duas rodadas não operam a mesma população: o `P(Up)`
corrigido muda quais instantes cruzam o threshold e pode mudar de que lado se
compra, então os 688 trades novos não são os 640 antigos com preço melhor — são
outro conjunto. Atribuir o +2,7125 *causalmente* à superconfiança é a
explicação mais econômica que temos (a banda foi escolhida pela curva de
horizonte do preditor defeituoso, e o desvio era 6,3× pequeno demais), mas
segue sendo inferência, não medição.

**O que fecharia isso é o mesmo teste que falta acima:** coorte pareada — os
mesmos instantes, os dois preditores, comparando direção e markout em vez de
PnL agregado. Enquanto ele não existir, o registro é "a regra corrigida perde",
não "não havia informação nenhuma ali".

**O 1.5 continua reprovando, e por motivo independente.** Profundidade a 3
ticks, p50 = 128,05 USDC em 300 s, contra os 200 exigidos. Capacidade não é
questão de modelo; nenhum conserto de preditor a resolve.

**Placar da rota taker depois desta rodada:** o 1.3 sai da lista de reprovações
e entram/permanecem 1.1 (sem PnL positivo em nenhuma banda), 1.4 e 1.5. A rota
taker reprova por *ausência de borda medida* e por *capacidade*, não mais por
calibração. É uma reprovação melhor fundamentada do que a anterior, e é pior
para a estratégia.


### 2c. Critérios de invalidação de livro — escritos ANTES dos números (M2.5)

O primeiro backtest sobre a gravação real excluiu **200 de 200 janelas** por
integridade. Antes de mexer em qualquer limiar, o que o relatório disse:

```
integridade.divergencia_topo_book:
  comparacoes: 117.445.854   divergencias: 4.023.803   taxa: 0,0343
  com_magnitude_finita: 1.320.824
  com_lado_vazio:       2.702.979
  magnitude_em_ticks_de_0.001: p50=10, p90=20, p99=999
  limiar_invalidacao: 0,01
```

**O detector estava errado, não o dado.** Duas provas independentes:

1. `p50 = 10 ticks de 0,001` = **0,01** = exatamente **um tick do mercado
   real**. O limiar de invalidação também era 0,01. Um detector que reprova
   por um tick de diferença está medindo a corrida entre `best_bid_ask` e
   `price_change`, não corrupção.
2. A varredura da âncora atingiu **1.0 sobre 152 janelas** usando a MESMA
   gravação. Dado corrompido não produz consistência perfeita.

Os limiares abaixo ficam registrados **antes** de olhar quantas janelas
sobrevivem. Se o resultado vier ruim com estes números, o que muda é o
diagnóstico — não o limiar.

**Uma divergência só invalida se as TRÊS condições valerem juntas:**

| Critério | Valor | Por quê |
|---|---|---|
| **Magnitude relevante** | `> 2 ticks de 0,01` (= 0,02) | 1 tick é o p50 observado, ou seja, o ruído de corrida. 2 ticks é a primeira magnitude que o ruído não explica. `K` é configurável (`--ticks-divergencia`), nunca menor que 2. |
| **Persistência** | sobrevive à próxima observação autoritativa **e** dura `> 250 ms` | Corrida se resolve na mensagem seguinte. 250 ms são ~2 ordens de grandeza acima do intervalo entre deltas do CLOB — folga suficiente para não confundir latência com perda. |
| **Fração de tempo** | token passa `> 1%` do tempo observado com livro divergente | Um episódio isolado não corrompe uma janela de 5 minutos. 1% de 5 min = 3 s: acima disso o livro deixou de descrever o mercado por tempo que importa para uma entrada. |

**Lado vazio deixa de ser corrupção por decreto.** São quatro causas
distintas e só duas invalidam:

| Categoria | Invalida? | Leitura |
|---|---|---|
| `vazio_desde_o_snapshot` | **não** | o lado já veio vazio no snapshot: nossa visão de profundidade é incompleta, o livro não está furado |
| `esvaziado_por_delta` | **não** | deltas removeram todos os níveis que tínhamos e o servidor mostra um nível abaixo que nunca nos foi contado — **truncagem de profundidade**, sinal de que `N` deve ser maior |
| `sem_snapshot` | **sim, por tempo** | o token não tinha livro inicial: não há reconstrução, há chute |
| `apos_perda` | **sim, por tempo** | perda conhecida (fila cheia, reconexão) sem resync: o livro é sabidamente furado |

As duas primeiras não são doença, são **miopia nossa** — e a resposta certa é
subir `--niveis-book`, não jogar a janela fora.

As duas últimas nem sequer são divergência: são **ausência de livro**, e
contá-las como divergência foi o que produziu os 2,7 milhões de "lado vazio"
do relatório do M2.2. Elas saem das populações e viram **tempo sem livro**,
cobrado pelo mesmo teto de fração que a divergência. A razão é a mesma que
vale para tudo aqui: um token que ficou 200 ms sem livro no começo da vida
não é o mesmo que um que passou a janela inteira sem, e um detector que não
distingue os dois reprova os dois.

A marca de qualidade cobra a **soma** das duas doenças — tempo divergente
mais tempo sem livro — porque, para quem vai entrar, dá no mesmo: ou o topo
estava errado, ou não havia topo. Um token que **nunca** recebeu snapshot é
`baixa` direto, sem conta de fração: não existe reconstrução para julgar.

**Marca de qualidade, no lugar da exclusão binária.** Cada token recebe
`qualidade_do_livro`, e a janela herda a **pior** das duas pontas:

| Marca | Condição |
|---|---|
| `alta` | fração divergente ≤ 0,1%, nenhuma divergência persistente acima de 0,05, snapshot presente, sem resync pendente |
| `media` | fração divergente ≤ 1% |
| `baixa` | fração > 1%, **ou** sem snapshot, **ou** resync pendente, **ou** divergência persistente acima de 0,10 |

O relatório passa a trazer o corte por marca (`janelas_por_qualidade`) e o
backtest aceita `--qualidade-minima` (padrão `media`). Assim o número nunca
depende de uma decisão escondida: quem lê vê quantas janelas cada marca
carrega e pode refazer o corte.

**O que faria eu mudar estes limiares:** se com `K = 2` a fração de janelas
`baixa` continuar acima de 20% **e** as amostras mostrarem magnitudes
concentradas em poucos ticks, o problema é alinhamento, não corrupção — e a
correção é no alinhamento (M2.5 tarefa 1), não em subir `K` até o número
ficar bonito.

#### O que o alinhamento encontrou — e não era o que eu esperava

A tarefa 1 foi escrita para corrigir a corrida entre `best_bid_ask` e
`price_change`. Ela corrigiu — e, ao ser medida, denunciou uma causa maior,
que estava dentro do `price_change`:

> **`best_bid`/`best_ask` descrevem o livro depois de TODA a mensagem, não
> depois de cada mudança de nível.** O M2.2 conferia a cada mudança, contra
> estados intermediários que nunca existiram no servidor.

Quando a mensagem move o topo um nível — insere o novo, remove o antigo — o
estado do meio fica **exatamente um tick** fora. É a assinatura do relatório:
`p50 = 10 ticks de 0,001` = 0,01 = um tick de mercado. Os 4 milhões de
divergências eram, em boa parte, **a nossa forma de conferir**.

Medição do antes e do depois, sobre 20.000 `best_bid_ask` atrasados em 150 ms
(a corrida real) mais os deltas correspondentes:

| Método | Comparações | Divergências | Taxa | p50 em ticks de mercado |
|---|---|---|---|---|
| Sem alinhamento (M2.2) | 40.000 | **26.358** | 0,659 | 1,0 |
| Com alinhamento (M2.5) | 66.358 | **0** | 0,000 | — |

Duas honestidades sobre esta tabela:

1. **Não é a gravação real.** É um cenário construído para conter a corrida
   e nada além dela. O que ele prova é que o método antigo acusa 66% onde não
   há perda nenhuma, e que o novo não acusa. O número da gravação real sai
   quando a gravação real rodar — e o relatório traz as duas contas lado a
   lado justamente para não ter de acreditar em mim.
2. **O alinhamento não conserta tudo.** Ele conserta corrida de
   milissegundos. Não conserta gravação que chegou ao disco fora de ordem
   além do buffer de reordenação do leitor — livro reconstruído de trás para
   frente está errado e nenhum carimbo o desembaralha. Por isso o relatório
   passou a trazer `deltas_com_carimbo_fora_de_ordem` e
   `snapshots_com_carimbo_fora_de_ordem`: se esses números forem altos na
   gravação real, o achado é outro, e a correção é subir o buffer.

Isto apareceu porque a fixture sintética do projeto tinha **19% dos registros
fora de ordem** — ela montava o arquivo por TIPO de evento (todos os books da
janela, depois todos os deltas), com inversões de até 300 segundos, enquanto o
recorder real drena uma fila e escreve quase ordenado. O detector condenava um
livro que só estava embaralhado pela fixture. A fixture foi corrigida para
escrever em ordem cronológica; o detector estava certo.

### 2d. O primeiro PnL real, e por que ele não valia (M2.6)

A primeira rodada com PnL sobre gravação real (4h, 2026-08-20 12h–15h UTC,
130 janelas resolvidas, 70 avaliáveis) deu **−7,01 USDC em 48 trades**,
retorno de −4,7%. O número foi descartado, e por dois motivos que se somam.

**O simulador ignorava a âncora que ele mesmo havia verificado.** No mesmo
relatório:

```
ancora.usada_no_backtest: "ultimo_antes"            (taxa_acerto 0,9020)
ancora.varredura_tau...regiao_viavel_100pct: [[-11, 10]]   (τ=0 → 1.0, 92/92)
```

A varredura confirmava a âncora com 100% sobre 92 janelas — segunda
confirmação independente — e o simulador operava com uma hipótese que erra
uma janela em dez. Todo o PnL saiu de resoluções parcialmente erradas. Desde
o M2.6 a fonte é a âncora verificada (API_NOTES §13.8); as hipóteses nomeadas
continuam no relatório como referência histórica e não decidem nada.

**O gatilho operava onde o modelo não sabe.** A calibração por tempo restante:

| bucket | n | erro |
|---|---|---|
| 240–120s | 4.769 | **−0,008** |
| 120–60s | 2.470 | −0,030 |
| <30s | 1.289 | −0,051 |
| 60–30s | 1.309 | −0,065 |
| **>240s** | 9.309 | **−0,240** |

E 46 dos 48 trades caíram em `>240s`. O modelo é quase perfeito em 240–120s e
foi usado quase só onde degenera.

**A causa não é a que parece.** A leitura tentadora é "o sinal só existe no
começo da janela". A medição diz o contrário: a instrumentação nova conta
`instantes_com_sinal` por bucket, independentemente de se operou, e o bucket
calibrado tem **mais** sinal que o bucket onde os trades caem, com **zero**
trades. A explicação é estrutural: a v1 entra **uma vez por janela** e varre o
stream da abertura para o fechamento, então opera no primeiro instante
elegível — que por construção está no começo. O gatilho não escolhe o bucket
ruim; ele nunca chega no bom.

Daí `--tempo-restante-max`, e daí o relatório passar a trazer as duas rodadas
lado a lado sempre: reportar só a restrita esconderia o custo da restrição
(menos trades, menos capital movimentado), e reportar só a irrestrita foi o
que produziu o número de cima.

### 2e. Três zeros que eram indistinguíveis de bug (M2.6)

Um zero silencioso é a pior saída possível de uma medição: parece resultado.
Os três do relatório real agora nomeiam a causa.

**`janelas_com_pool_de_reward: 0`.** A cadeia do dado foi conferida inteira e
está correta — a descoberta guarda `raw_gamma`, o recorder extrai
`rewards_daily_rate` de `clobRewards` somando as fontes (§12.8) mais
`rewardsMinSize`/`rewardsMaxSpread`, e o backtest lê os três. Então não é
campo que ninguém lê. Sobram duas leituras, e o relatório passa a separá-las:
`sem_taxa_diaria` em massa significa que os mercados updown **não participam
do programa de rewards** — o que é um achado sobre o programa, não um defeito
nosso, e derruba a rota maker como fonte de receita nestes mercados. Já
`sem_max_spread` com taxa presente seria campo faltando, e aí a conta é
recuperável.

**`vazio_desde_o_snapshot: 1.777.814` (99% do lado vazio).** A premissa de
que isso era truncagem de níveis está **errada**, e vale registrar porque a
correção seria no lugar errado: `--niveis-por-lado` trunca o `BookTimeline` da
passada 2, enquanto o monitor de integridade lê o evento **cru** na passada 1,
com todos os níveis. Subir o flag não mexe nesse contador. Lado vazio ali é o
evento gravado *parseando* vazio — ou porque veio vazio, ou porque a chave tem
outro nome, que é o defeito do `price_change` (§6.1b) de novo. As duas
produzem o mesmo zero e têm consertos opostos, então o relatório passa a
trazer `snapshots_de_livro.formas`, que diz com que par de chaves o servidor
manda os lados. A recomendação de `--niveis-por-lado` sai do p99 de níveis
observados, medido no mesmo bloco.

**`gaps: rtds silencio 837s`.** Silêncio sem escopo não diz o que consertar. O
keepalive do M2.1 resolveu a *queda* de conexão; silêncio sem queda é outro
fenômeno. O relatório passa a separar: silêncio da **conexão inteira** (o
servidor parou de publicar) de silêncio **só do tópico** com outros tópicos
chegando na mesma conexão — que é assinatura caducando, e o conserto é
reassinar periodicamente no recorder. `suspeita_de_assinatura_caducada > 0` é
o gatilho dessa correção, que fica **pendente para a próxima janela de
manutenção** (a gravação em curso não pode ser interrompida).

### 2f. O que o M2.6 preserva como resultado bom

**Markout medido:** 30.763 execuções, média de **−0,33 centavos/share em 5s**
(−0,16 em 1s). Custo de adverse selection pequeno.

**A conta que isso permite fechar**, agora explícita no relatório
(`rota_maker.conta_fechada.rebate_vs_markout`): em p = 0,50 a taxa do taker é
máxima (1,75 c/share), e o rebate de 20% dá **0,35 c/share** — mesma ordem de
grandeza do markout. Ou seja, na melhor hipótese a rota maker se paga e não
sobra margem. **E isso antes da fila:** o rebate só existe quando alguém nos
executa, e a probabilidade de execução depende da posição na fila, que este
backtest não modela (§15). O custo é observado; a receita é um teto.

**Atraso de liquidação:** TWAP p50 145,9s contra 336,5s do horário — 2,3× mais
lento. Capital preso, medido.

**Profundidade a 3 ticks (p50):** 137 USDC (300s), 79 (900s), 204 (3600s). O
critério deste documento exige ≥ 200, então **5m e 15m reprovam e só a duração
de 1h passa**. O critério passou a sair no relatório
(`medicoes.profundidade.criterio_do_veredito`) em vez de depender de quem lê
lembrar do número. É um limite de **capacidade**, e edge nenhum o resolve.

### 2g. Dois sinais positivos, e por que ainda não valem (M2.7)

**A restrição de faixa funciona.** 8h reais (2026-08-22 04h–11h UTC), âncora
verificada, mesmas janelas, mudando só *quando* opera:

| | trades | PnL | hit | drawdown |
|---|---|---|---|---|
| irrestrito | 18 | −4,79 | 0,556 | −9,81 |
| `--tempo-restante-max 240` | 18 | **+10,41** | **0,833** | −2,94 |

É o primeiro PnL positivo do projeto, e não vem de mais operações — vem das
mesmas 18, colocadas onde o modelo tem calibração. **Mas 18 trades não
decidem nada:** o critério deste documento pede 200, e continua pedindo.

**Os rewards existem, só não onde procurávamos.** Duas janelas com pool,
ambas de 14400 s (4h); 199 sem, todas por `sem_taxa_diaria`. Na melhor ordem
medida, 13,2 USDC/hora de receita com fatia média de 24%. Isso reabre a rota
maker — num mercado específico e sobre **duas** janelas.

**O que impede decidir os dois: a gravação está cega.** Nas mesmas 8 horas,
163.195 s de silêncio do feed-verdade, 184 de 254 janelas com a abertura em
lacuna, e a varredura da âncora perdendo 75% da amostra. Nenhum dos dois
sinais pode ser confirmado nem descartado com dado assim — por isso o M2.7 é
um marco de captação, e não de estratégia.

### 2h. A entrada múltipla é alavancagem, não edge (M2.7)

A v1 entra uma vez por janela: 18 trades sobre 1.617 instantes com sinal na
faixa calibrada. O caminho óbvio para os 200 trades seria entrar mais vezes.
Medido, na mesma faixa e com espaçamento mínimo de 30 s:

| entradas máx. | trades | PnL | hit | drawdown |
|---|---|---|---|---|
| 1 | 37 | +0,10 | 0,595 | −12,1 |
| 3 | 103 | +22,68 | 0,631 | −24,6 |
| 10 | 187 | −16,82 | 0,562 | −71,1 |

*(amostra sintética — os valores absolutos não dizem nada sobre edge; a forma
da curva diz.)*

O PnL sobe de 1 para 3 e **o drawdown sobe junto**; de 3 para 10 o PnL vira
negativo e o drawdown sextuplica. Comprar o mesmo movimento mais vezes é
alavancagem: multiplica ganho e perda na mesma proporção, e não cria edge
nenhum. O espaçamento mínimo não é botão de gosto — sem ele, ticks
consecutivos com sinal seriam contados como oportunidades independentes e o
PnL somaria a mesma aposta repetida.

**O default segue 1.** Chegar aos 200 trades por entrada múltipla seria
inflar a amostra, não medir mais mercado. O caminho honesto é gravar mais
tempo — que é exatamente o que o M2.7 destrava.

### 3. A hipótese do tick nos extremos foi REFUTADA

A primeira gravação real produziu **zero trades** — os seis bugs do M2.1 —, mas
produziu **uma** medição aproveitável, e ela derrubou uma hipótese que estava
registrada como plausível.

**Hipótese registrada em 2026-08-16:** o `tick_size` afina de 0,01 para 0,001
quando o preço encosta nos extremos, onde 0,01 seria grosso demais.

**Medido (1h, 15 afinamentos):** preço p50 no afinamento = **0,48**; apenas
**1 dos 15** fora de [0,10; 0,90]. É o oposto do previsto. O tick afina em
mercado **equilibrado**, onde a disputa está apertada.

Por que isso entra no veredito e não só nas notas técnicas: a granularidade
fina aparece exatamente onde a **taxa é máxima** (p ≈ 0,50; 3,5% do capital,
item 1 acima). Tick fino ali não é convite — é sinal de que o mercado está
caro de atravessar justamente onde parece mais disputado. A hipótese antiga
teria mandado procurar oportunidade nos extremos por causa do tick, que é o
lugar errado.

**Ressalva, e ela é grande:** n = 15 numa única hora, e a hipótese concorrente
— *o gatilho é o tempo restante, não o preço* — **não foi testada**, porque
`seconds_left` saía NaN (BUG 3 do M2.1, corrigido). A próxima gravação decide
entre as duas. Detalhe completo em API_NOTES 13.3a.

### 4. A capacidade pode importar mais que o edge

Mesmo com edge positivo, a medição de profundidade (M2.E.3) pode inviabilizar
o projeto por outro caminho: se o book só comporta dezenas de USDC a 3 ticks
do topo, o retorno absoluto não paga o trabalho, por melhor que seja o
percentual. Esse número sai da mesma gravação.

---

## Como o veredito será decidido

Quando a gravação existir, rode:

```bash
python -m pulsearb.backtest data/recordings --json relatorio.json
```

Tabela preenchida com a rodada **`HORIZONTE_240_120_v2`** (2026-08-29, 24 h,
126.724.222 registros, banda 240-120s). Todas as medições estão **FEITAS**; o
que resta pendente não é medição, é **fato externo** ou **decisão**.

| Critério | Fonte no relatório | Resultado |
|---|---|---|
| Âncora de abertura identificada | `ancora.veredito` | ✅ **CONFIRMADA**, τ=0 explica 100% de 768 janelas |
| PnL líquido total | `backtest.resumo.pnl_liquido_usdc` | ✅ medido: **+2,7125 USDC** (640 trades) |
| PnL por jogo (TWAP × horário) | `backtest.por_jogo` | ✅ medido: só `twap` (n=640); horário sem trade na banda |
| Hit rate | `backtest.resumo.hit_rate` | ✅ medido: **0,7063** |
| Drawdown máximo | `backtest.resumo.max_drawdown_usdc` | ✅ medido: **−50,1547 USDC** |
| Calibração por bucket | `backtest.calibracao` | ✅ medido: 5 baldes, todos avaliáveis; melhor ECE 0,0694 |
| Melhor threshold | `curva_de_edge.melhor_threshold` | ✅ medido: **0,03** (+3,0489) — *in-sample*, não adotado |
| Sensibilidade a latência | `sensibilidade_latencia` | ✅ medido **na banda**: +3,31 / +2,71 / +1,35 / +0,47 |
| Sinais × preenchíveis | `backtest.funil_de_sinais` | ✅ medido: 640 → 640, conversão **1,0** (zero descarte) |
| Mudança de tick | `medicoes.tick` | ✅ medido: extremos **refutados** (n=485); p50 a 68,8 s do fim |
| Atraso de liquidação por jogo | `medicoes.atraso_liquidacao` | ✅ medido: twap p50 147,3 s; horário p50 166,1 s (1,1×) |
| Profundidade do book | `medicoes.profundidade` | ✅ medido: p50 3t **128,0 / 50,0 / 28,7 / 27,0** — nenhuma ≥ 200 |
| Memória e retenção do backtest | `gravacao.memoria` | ✅ medido: 1,97 M snapshots, 0 raleamentos, resolução 0,0 ms |
| **Integridade do livro reconstruído** | `integridade.divergencia_topo_book` | ✅ medido: julgada **0,20%** (< 1%); 767 janelas alta / 46 média |
| **Offset de relógio** | `integridade.offset_relogio_ms` | ✅ medido: p50 **2,80 ms**, mediana estável — sem deriva |
| **Rewards simulados** | `rota_maker.rewards` | ⚠️ medido **sob hipótese** — fórmula não confirmada (ver 1.10) |
| **Markout (seleção adversa)** | `medicoes.markout` | ✅ medido: **−0,1974 c/share** @5s (246.504 execuções) |
| **Conta fechada do maker** | `rota_maker.conta_fechada` | ❌ **NÃO FECHÁVEL** — faltam 3 termos (fila não observável) |

### Placar dos 10 critérios pré-registrados

**Corrente — preditor de variância MEDIDA (2026-08-30, `M2_24AGO_MEDIDO.json`):**

| | Taker | Maker |
|---|---|---|
| ✅ **PASSA** | **2** — 1.2 (688 trades), 1.3 (ECE 0,0126–0,0493) | **3** — 1.7 (−0,1974), 1.8 (65,9 h), 1.9 (0,20%) |
| ❌ **REPROVA** | **3** — 1.1 (−67,27, nenhuma banda), 1.4 (negativo em toda a grade), 1.5 (profundidade) | **1** — 1.10 (fórmula não confirmada) |
| ⏳ **NÃO AVALIÁVEL** | 0 | **1** — 1.6 (conta não fecha) |

**Total: 5 de 10 verdes, 4 reprovados, 1 não avaliável.** Como cada rota exige as
CINCO do seu bloco, **nenhuma das duas está viável** — mas as reprovas têm
naturezas diferentes, e é isso que decide o que vem depois.

*Histórico — preditor de variância DERIVADA, o mesmo placar antes da §2d-ter:*

| | Taker (na banda 240-120s) | Maker |
|---|---|---|
| ✅ PASSA | 3 — 1.1 (+2,7125), 1.2 (640), 1.4 (+1,3488) | 3 — 1.7, 1.8, 1.9 |
| ❌ REPROVA | 2 — 1.3 (calibração), 1.5 (profundidade) | 1 — 1.10 |
| ⏳ NÃO AVALIÁVEL | 0 | 1 — 1.6 |

*Dava 6 de 10 verdes. A diferença inteira entre os dois placares é o conserto do
preditor: o 1.3 saiu da coluna de reprovas e 1.1 e 1.4 entraram nela.*

### O que ainda está pendente — e por quê

Nenhuma pendência é de medição. Sobraram três, todas fora do alcance de rodar o
backtest de novo:

1. **1.10 — fórmula de reward (fato externo).** `docs.polymarket.com` está
   bloqueado neste ambiente. Enquanto não for confirmada, todo o bloco
   `rota_maker.rewards` (e o 1.6 que depende dele) permanece **hipótese**, não
   medição. Destrava com acesso à documentação oficial, não com mais dado.
2. **1.6 — conta fechada do maker (limite estrutural).** Faltam três termos —
   `volume_taker_usdc`, `custo_de_markout` em USDC e `capital_imobilizado` — e os
   dois primeiros exigem saber QUAIS cotações teriam sido executadas, o que
   depende de **posição na fila**. O WS entrega níveis agregados, não ordens: a
   fila não é observável na gravação. Fechar exige outra fonte de dado ou um
   modelo de fila assumido (e declarado).
3. **Repetição em dia independente (validade).** A avaliação ainda é de **um
   dia**. O que mudou em 30/08 é que o *preditor* deixou de ser ajustado nele:
   a curva de variância vem de 23/08 e a avaliação é de 24/08, com recusa em
   código se as datas se cruzarem (§2d-ter). Isso cobre a variância; não cobre
   o threshold nem qualquer banda que venha a ser escolhida. Nada disso vira
   decisão de dinheiro sem repetir em gravação independente — a ressalva de
   sempre da §2d-bis.

### Regras de decisão, definidas ANTES de ver os números

Definir os critérios agora é o que impede de racionalizar o resultado depois.

**TAKER VIÁVEL** exige TODAS:

1. PnL líquido positivo com latência de **300ms** e threshold ≥ 0,02
2. Pelo menos **200 trades** na amostra (menos que isso é ruído)
3. Calibração com erro < 0,05 em pelo menos um bucket de tempo restante
4. Positivo também em **600ms** (se só sobrevive a 150ms, é miragem)
5. Profundidade a 3 ticks ≥ **US$ 200** no p50 (senão não escala)

**SÓ MAKER VIÁVEL** — critérios do M2.2, escritos ANTES de ver os números.
Exige TODAS:

1. `rota_maker.conta_fechada` positiva com o fator de desconto em **0,3** —
   o extremo pessimista da varredura. Positivo só em 0,9 é resultado do
   palpite, não do mercado.
2. **Markout de 5s ≥ −0,5 centavo por share** no p50 de pelo menos um recorte
   (duração ou faixa horária). Markout pior que isso come qualquer reward
   plausível: com pool de ~1.667 USDC/dia rateado, a fatia de quem cota 50
   shares é da ordem de centavos por hora.
3. Pelo menos **20 horas de amostra** na célula que sustenta a conclusão —
   `horas_de_amostra` vem em cada célula justamente para isto.
4. Divergência com topo deslocado (`com_magnitude_finita / comparacoes`)
   **abaixo de 1%** na gravação — ver a emenda ao 1.9, que rege este
   critério e obriga a agregada impressa ao lado. A conta do maker é uma
   soma sobre o livro reconstruído: se o livro é duvidoso, o resultado é
   decorativo.
5. A fórmula de reward **confirmada** contra a documentação oficial
   (API_NOTES §15.2). Enquanto for palpite, o veredito maker é "promissor,
   não verificado" — nunca "viável".

O critério 5 é o que impede o erro mais fácil de cometer aqui: tratar um
número bem formatado, saído de uma fórmula que ninguém confirmou, como se
fosse medição.

**Por que o maker entra no veredito e o M2 não o implementa:** a economia dos
dois lados é estruturalmente diferente. O edge do taker é PREDITIVO — depende
de a nossa probabilidade bater melhor que a do book, contra participantes que
viram o mesmo dado no mesmo segundo. O reward do maker é MECÂNICO — é uma
fórmula sobre o livro, com orçamento diário conhecido e rateio pro-rata. Sobre
gravação, o segundo é simulável com fidelidade muito maior. Isso não o torna
melhor; torna-o **mensurável antes de arriscar capital**, que é o que o M2
existe para fazer. Implementar market making continua sendo v2.

**NENHUM VIÁVEL**: taker negativo e maker sem margem. **Neste caso o projeto
para, e isso é sucesso.** O M2 existe justamente para custar 72h de VPS em vez
de meses de capital. Um "não" fundamentado em 72h de dado real vale mais que
um "talvez" sustentado por otimismo.

### O que precisaria ser verdade para eu mudar de ideia

Se o veredito for negativo, estas são as saídas legítimas — e a única forma de
distingui-las de teimosia é ter dito antes:

- **Amostra pequena demais**: < 200 trades. Solução: gravar mais, não afrouxar
  o critério.
- **Edge concentrado num bucket específico** (ex.: só `<30s`), diluído no
  agregado. Isso é resultado positivo, não negativo — muda a estratégia para
  entrada tardia, e o relatório por bucket mostra isso direto.
- **Fee mudou**: `r` e `e` vêm do dado gravado. Se a Polymarket reduzir a taxa
  de cripto, refaz o backtest sobre a mesma gravação e o veredito muda sem
  código novo.
- **Rota maker**: `takerOnly=true` significa que quem cota **não paga taxa** e
  ainda recebe rebate. O jogo econômico do maker é estruturalmente diferente —
  se o taker morrer na taxa, é o caminho a investigar.

O que **não** é razão para mudar de ideia: "o modelo é simples demais".
Sofisticar o modelo antes de haver sinal é otimizar ruído. Se o edge bruto não
aparecer nem com o modelo endgame — que já usa a estrutura mais explorável
deste mercado — o problema não é o modelo.

---

## A limitação que infla o resultado do maker — leia antes do número

O WS de mercado entrega **níveis agregados, não ordens individuais**. Do
tamanho de 500 shares em 0,49 não dá para saber se são duas ordens ou
cinquenta, nem em que posição a nossa entraria.

Consequência direta: **a posição na fila é inobservável**, e toda simulação de
preenchimento maker aqui é **otimista por construção**.

E o viés não é neutro — ele erra para o mesmo lado nas duas pontas:

- **Superestima a execução boa.** A simulação assume que seríamos executados
  sempre que o topo é atravessado. Na fila real, atrás de 500 shares, muitas
  dessas execuções não aconteceriam.
- **Subestima a execução ruim.** No pior caso — a nossa ordem sempre no fim da
  fila — só somos executados quando o nível INTEIRO é varrido. Varrer o nível
  inteiro é exatamente o evento de informação: é o caso de markout pior. Ou
  seja, a fila real nos daria proporcionalmente MAIS das execuções ruins e
  MENOS das boas.

**Hipótese de fila usada:** nenhuma — a simulação de reward não modela
execução, só presença no livro (que é o que a fórmula de reward pontua). O
markout é medido sobre execuções REAIS observadas no topo, não simuladas. A
conta de B.3 fica, por isso, deliberadamente **incompleta**: `rewards` e
`rebate` têm número; `custo_de_markout` em USDC e `capital_imobilizado` estão
listados em `o_que_falta_para_fechar` em vez de preenchidos com um palpite.

Quanto isso infla, no pior caso: se cotássemos 50 shares atrás de 500 e
fôssemos executados só nas varridas completas, o markout efetivo seria o da
cauda ruim da distribuição — o p10, não a média. A diferença entre os dois na
tabela de markout **é** a margem de erro desta simulação. Está reportada.

---

## Estado da implementação

| Item | Estado |
|---|---|
| M2.A Recorder de produção | pronto, com rotação de assinatura e relatório de lacunas |
| M2.B Replay determinístico | pronto; determinismo verificado por teste |
| M2.C Modelo TWAP endgame | pronto |
| M2.C Modelo horário | pronto |
| M2.C Validação da âncora | pronta; falsifica hipóteses contra resoluções reais |
| M2.D Backtest com descontos | pronto (taxa do dado, slippage do book, latência) |
| M2.E Medições | prontas; **sem dado para responder** |
| M2.F Empacotamento | Dockerfile, systemd, runbook, script de coleta |
| M2.2 A Integridade de dados | canal sem perda, validação cruzada de topo, resync, relógio, RTDS redundante, parquet |
| M2.2 B Instrumentação do maker | score de rewards, markout, conta fechada — **medição, nada implementado** |

**Bloqueio único: gravação de produção.** Próximo passo em
`docs/RUNBOOK_VPS.md`.

### A primeira tentativa de gravação (2026-08-18) e o que ela custou

Uma hora de mercado real, 104 janelas conhecidas, **zero trades**. Nenhum dos
seis motivos era o mercado:

| # | Defeito | Efeito |
|---|---|---|
| 1 | assinatura cancelada no `endDate` | 0 resoluções capturadas → 0 janelas avaliáveis |
| 2 | RTDS sem keepalive de protocolo | reconexão a cada 30–306s a hora inteira |
| 3 | `seconds_left` NaN nos snapshots | medição de tick sem eixo de tempo |
| 4 | arquivo da hora reaberto em append | 3 de 26 gzips inválidos |
| 5 | indexador do backtest acumulando todo book | `Killed` (OOM) num arquivo só |
| 6 | estimativa de disco 100x abaixo | dimensionamento da VPS errado |

Todos corrigidos no M2.1, com teste onde cabia teste. **A lição operacional:
"o serviço subiu" e "o serviço está gravando" são afirmações diferentes** — é
o que a §5.1 do runbook passou a verificar. A gravação recomeça do zero.

### O que foi verificado de fato, e como

| Verificação | Como |
|---|---|
| Rotação de assinatura | recorder real contra WS local: janela some da descoberta → tokens desassinados |
| Snapshot com `tick_size`, fee e `umaReward` | lido de volta do gzip gravado |
| Determinismo do replay | duas passadas sobre a mesma gravação produzem sequência idêntica |
| Tolerância a arquivo truncado | linha corrompida anexada; replay conta e segue |
| Slippage atravessando níveis | book de 2 níveis, fill de 100 shares → preço médio ponderado |
| Fee do dado gravado | `r`/`e` vêm do snapshot, não de constante |
| Falsificação da âncora | hipótese errada é derrubada por uma única janela discordante |
| Instalação limpa | `pip install .` em venv novo, `python -m pulsearb.recorder --help` |
| Unit systemd | `Restart=always`, ExecStart correto |
| `fetch_recordings.sh` | `bash -n` |

**Não verificado:** o build do Docker (não há daemon no ambiente de
desenvolvimento). O Dockerfile é convencional e a instalação limpa equivalente
foi validada, mas a imagem em si não foi construída — construa antes de
confiar nela na VPS.

Um defeito real que essa verificação pegou: `PULSEARB_RECORDER__OUTPUT_DIR`
(a variável que o Dockerfile usa para mandar as gravações ao volume) **não
sobrepunha o `config.yaml`**. A imagem gravaria no caminho errado, em
silêncio, e a descoberta só viria ao procurar os arquivos que não existiam.
Corrigido, com teste.
