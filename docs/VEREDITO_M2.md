# VEREDITO M2 — existe edge líquido?

**Status: SEGUNDO VEREDITO em 2026-08-26, sobre 24 h limpas — e ele DERRUBA
o primeiro. Ver a seção logo abaixo.**

Data: 2026-08-16 · atualizado 2026-08-21 (M2.5) · veredito 2026-08-23 ·
**reveredito 2026-08-26 sobre 2026-08-24**

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

E preencha esta tabela com os números do relatório:

| Critério | Fonte no relatório | Resultado |
|---|---|---|
| Âncora de abertura identificada | `ancora.veredito` | _pendente_ |
| PnL líquido total | `backtest.resumo.pnl_liquido_usdc` | _pendente_ |
| PnL por jogo (TWAP × horário) | `backtest.por_jogo` | _pendente_ |
| Hit rate | `backtest.resumo.hit_rate` | _pendente_ |
| Drawdown máximo | `backtest.resumo.max_drawdown_usdc` | _pendente_ |
| Calibração por bucket | `backtest.calibracao` | _pendente_ |
| Melhor threshold | `curva_de_edge.melhor_threshold` | _pendente_ |
| Sensibilidade a latência | `sensibilidade_latencia` | _pendente_ |
| Sinais × preenchíveis | `backtest.funil_de_sinais` | _pendente_ |
| Mudança de tick | `medicoes.tick` | hipótese dos extremos **refutada** (n=15); tempo restante pendente |
| Atraso de liquidação por jogo | `medicoes.atraso_liquidacao` | _pendente_ |
| Profundidade do book | `medicoes.profundidade` | _pendente_ |
| Memória e retenção do backtest | `gravacao.memoria` | _pendente_ |
| **Integridade do livro reconstruído** | `integridade.divergencia_topo_book` | _pendente_ |
| **Offset de relógio** | `integridade.offset_relogio_ms` | _pendente_ |
| **Rewards simulados** | `rota_maker.rewards` | _pendente_ |
| **Markout (seleção adversa)** | `medicoes.markout` | _pendente_ |
| **Conta fechada do maker** | `rota_maker.conta_fechada` | _pendente_ |

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
