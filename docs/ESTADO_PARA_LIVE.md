# ESTADO — o que falta para operar com dinheiro real

> ## ✅ BLOCO 1 DESSUSPENSO EM 2026-08-30 — a remediação rodou, e mudou o veredito
>
> O defeito de variância do `prob_up_twap` foi corrigido e, mais que isso,
> **substituído por medição**: a §13.8 já tinha VERIFICADO que a janela resolve
> por um ponto do stream `twap_sixty`, não por uma média que nós calculemos, e
> o modelo derivado subestimava a variância em **39 a 48 vezes** (6,3× no
> desvio) por três erros compostos. A `V(t)` agora é medida em dado real, num
> dia ANTERIOR ao avaliado (curva de 23/08 → avaliação de 24/08), com guarda de
> in-sample em código.
>
> **O que a rodada produziu:** o **1.3 passa** — e a borda some junto. Os itens
> 1.1 a 1.4 desta página estão atualizados com os números do modelo medido; o
> "+2,7125 na banda" foi para o histórico. A atribuição causal dele à
> superconfiança é inferência, não medição — a delimitação está na §2d-ter.

**Semáforo de hoje: 🔴 VERMELHO** — o veredito não mudou, a causa mudou de
novo, e desta vez para pior. Com o preditor consertado
(`relatorios/M2_24AGO_MEDIDO.json`, curva de 23/08 sobre o dia 24/08), o
**1.3 passa nos cinco baldes** — `erro_de_confiabilidade` de **0,0126** a
**0,0493**, todos com 20 faixas ocupadas e `calibracao_avaliavel: true`,
contra 0,207 na banda com o modelo derivado. **E a borda desapareceu no mesmo
movimento:** 688 trades, PnL **−67,27**, `bandas_com_edge: []` — nenhuma das
cinco bandas de horizonte é positiva. Placar do taker: **1.2 e 1.3 ✅ · 1.1,
1.4 e 1.5 ❌**. Como o critério exige as CINCO, **o taker segue reprovado** —
por ausência de borda e por capacidade, não mais por calibração. O MAKER
continua barrado pelo 1.10 e pelo 1.6 (não avaliável por construção).

**A sensibilidade à latência inverteu.** Com o modelo defeituoso o PnL da
banda decaía monotonicamente com a latência (+3,3119 a 150 ms → +0,4736 a
1000 ms), e eu citava esse decaimento como evidência de sinal direcional real.
Com o modelo medido o PnL **melhora** com latência (−67,94 a 150 ms → −55,78 a
1000 ms). O que isso estabelece é que **a evidência que eu invocava antes não
sobrevive** — mesmo instrumento, sinal oposto. O que NÃO estabelece é ausência
de direção: a varredura mantém o sinal fixo e troca só o book do fill, então
ela mexe no preço de entrada e na coorte de trades ao mesmo tempo (ver §2d-ter
do `VEREDITO_M2.md`). Quem carrega a conclusão é o resto — PnL negativo nas
**cinco** bandas e `hit_rate` 0,4172, abaixo de 0,5 no lado escolhido pelo
modelo. **Sobre o +2,7125:** a leitura de que ele vinha do desvio-padrão 6,3×
pequeno demais — `P(Up)` saturado em 0 e 1, amostra pequena fazendo o resto — é
a explicação mais econômica, e não está medida. O preditor corrigido opera
outra coorte de trades, então esta rodada não testa aqueles 640. O que está
medido é que a regra corrigida perde. A delimitação completa está na §2d-ter.

**O 1.5 reprova por motivo independente e continua igual:** p50 de 128,05 USDC
a 3 ticks contra os 200 exigidos. É teto de **capacidade** do book — nenhum
conserto de preditor o resolve, e restringir horizonte não cria liquidez.

Isto NÃO invalida o M4 (portões de risco, SHADOW, ciclo ao vivo): é
exatamente a máquina que permite medir sem arriscar. Invalida a decisão de
ligar o LIVE com a estratégia taker atual.

**Decisão pré-registrada em aberto, de Paulo:** encerrar o taker no registro,
ou virar para a rota maker — hoje travada em 1.10 (fato externo: a fórmula de
pontuação de reward na doc da Polymarket, inalcançável deste ambiente) e em
1.6 (não avaliável sem posição na fila).

Atualizado: 2026-08-30 · fonte dos números correntes:
**`relatorios/M2_24AGO_MEDIDO.json`** — 24 horas de gravação real de
2026-08-24 (126,7 M registros, `pior_fracao_coberta 1,0`, 0 silêncios),
avaliadas com o preditor de variância **medida** sobre a curva de 23/08
(`relatorios/VARIANCIA_23AGO.json`), 688 trades. As rodadas do preditor
derivado — `M2_24AGO.json` (695 trades) e `HORIZONTE_240_120_v2.json`
(640 trades) — passaram a ser **histórico**, e é delas que vêm os números do
**restante do Bloco 1**, da tabela de critérios até o fim daquele bloco. Os
demais blocos são estado corrente. O veredito de 20 h, com 837 s de silêncio e
42% dos snapshots descartados, segue em `docs/VEREDITO_M2.md`.

> Como ler: **✅** passou com dado real · **✅ *na banda*** (só no histórico)
> passou apenas restrito à faixa 240-120 s de tempo restante · **❌** medido e reprovado ·
> **🟡** parcial · **⚠️** não avaliável por construção · **⏳** sem amostra
> suficiente · **⬜** não existe / não começou.
> Um item só vira ✅ com número de gravação real. Número de gravação
> sintética não conta — é a regra que o M2 existe para fazer valer.

---

## Bloco 0 — Captação (bloqueia tudo o que vem depois)

Gravar mais horas antes disso só produz mais horas meio-cegas.

| # | Item | Estado | Evidência |
|---|---|---|---|
| 0.1 | Flush do silêncio de `conexao_inteira` até o fim da gravação | ✅ | M2.10, teste trava; falhava antes |
| 0.2 | `suspeita_de_assinatura_caducada` não acusa conexão morta | ✅ | M2.10, teste trava; acusava 7 falsos |
| 0.3 | `total_s` como união, não soma | ✅ | M2.10; dava 14.476 s numa hora de 3.600 s |
| 0.4 | Reassinatura decide por (tópico, ativo), não por tópico | ✅ | M2.10, teste trava |
| 0.5 | **O recorder rodava com o M2.7?** | ✅ **RESPONDIDO: sim, e a defesa NÃO funcionou** | log da VPS: 2.482 reassinaturas, uma a cada 5 s, sem recuperação |
| 0.6 | **Escalada: derrubar o socket após N reassinaturas sem efeito** | ✅ **deployada** em 2026-08-23 01:31 | M2.11; ainda sem oportunidade de agir — 0 alarmes desde então |
| 0.7 | **Cobertura > 95 % em todos os ativos** | ✅ **0,9994** | hora 23:00, 99,9 % nos 8 ativos, `silencios: 0` |
| 0.8 | 72 h contínuas e limpas | ⏳ contador reiniciou 01:31 UTC | cada restart zera o `--duration 72h` |

### 0.7 FECHOU — a hora das 23:00

```
pior_fracao_coberta  0.9994      silencios  0      conexao_inteira  0
  bnb btc doge eth hype sol xrp zec: 99,9% cada, silencio_final 3,0s
```

E a âncora ganhou veredito pela primeira vez nesta campanha:

> **CONFIRMADA: tau=0 explica 100% das 24 janelas elegiveis.**

`distribuicao_das_elegiveis` em quartis 4/6/8/6, `concentrada: False` — amostra
espalhada pela hora inteira, não amontoada numa rajada.

**Ressalva sobre o mérito:** a hora das 23:00 foi gravada com o código
ANTERIOR ao M2.11. O feed se recuperou sozinho por volta das 20:00, antes de a
escalada entrar em serviço. Ela segue instalada e nunca foi acionada — é
seguro que ainda não foi reclamado.

### A medição que motivou tudo — o feed é INTERMITENTE, não morto

Cadência medida: **1,061 s por tick** por ativo (mediana 1,0 s, com intervalos
de 2 s e 8 s puxando a média), 8 ativos ⇒ **27.152 por hora cheia** é o
esperado. A tabela abaixo usava 8 ticks/s e subestimava a cobertura das horas
boas em ~5 pontos; os números estão corrigidos.

| Hora (UTC) | twap gravados | span | esperado | cobertura |
|---|---|---|---|---|
| 16:00 | 13.500 | 3.600 s | 27.152 | 49,7 % |
| 17:00 | 2.344 | 3.600 s | 27.152 | **8,6 %** |
| 18:00 | 6.037 | 3.600 s | 27.152 | 22,2 % |
| 19:00 | 2.320 | 3.600 s | 27.152 | **8,5 %** |
| 20:00 | 27.214 | 3.600 s | 27.152 | **100,2 %** |
| 23:00 | 27.304 | 3.600 s | 27.152 | **100,6 %** |
| 00:00 | 27.286 | 3.600 s | 27.152 | **100,5 %** |

Ele funciona em rajadas, e o episódio ruim foi das ~16:29 às ~19:xx. Desde as
20:00 as horas cheias vêm a **~100 %** — três seguidas.

**A defesa do M2.7 falhou, e agora está provado.** O recorder rodava com ela:
2.482 reassinaturas, uma a cada 5 s. As horas 17 e 19 ficaram em 8 % mesmo
assim. O `sem_dados_timeout_s` de 30 s nunca derrubou a conexão — não há
linha de reconexão no log —, ou seja, **o socket estava vivo recebendo outro
tráfego** enquanto o tópico não vinha. Reassinar nesse estado não produziu
efeito nenhum, 2.482 vezes.

Daí o item 0.6: reassinar cobre assinatura caducada; não cobre o servidor que
parou de publicar aquele tópico para aquela conexão. A resposta que sobra é
derrubar e reconectar, refazendo a assinatura do zero.

---

## Bloco 1 — Veredito M2: existe edge líquido?

Critérios escritos **antes** dos números, em `VEREDITO_M2.md`. Não são
negociáveis depois do resultado.

Amostra: **24 horas de 2026-08-24**, `pior_fracao_coberta` 1,0 nos oito
ativos, **0 silêncios**, 896 janelas conhecidas, **688 trades** com o preditor
de variância medida. A amostra anterior — 20 h de 23/08, com 837 s de silêncio
e 42% dos snapshots descartados — segue em `VEREDITO_M2.md` para comparação.

### TAKER — 2 passa, 3 reprova (o critério exige as 5)

Sem restrição de banda: com o preditor corrigido não há banda de horizonte com
edge para restringir a (`bandas_com_edge: []`).

| # | Critério | Exigido | Medido (24 h, modelo MEDIDO) | |
|---|---|---|---|---|
| 1.1 | PnL líquido a 300 ms, threshold ≥ 0,02 | positivo | **−67,2744** · `bandas_com_edge: []` — nenhuma das cinco bandas positiva | ❌ **causa nova** |
| 1.2 | Número de trades | ≥ 200 | **688** | ✅ |
| 1.3 | Calibração: `erro_de_confiabilidade` < 0,05 em ≥ 1 balde avaliável | sim | **os cinco baldes passam**: 0,0126 (`<30s`) · 0,0285 · 0,0319 · 0,0452 · 0,0493, todos com 20 faixas ocupadas | ✅ **resolvido pela §2d-ter** |
| 1.4 | Positivo também a 600 ms | sim | negativo em toda a grade de latência, e **melhorando** com ela (−67,94 a 150 ms → −55,78 a 1000 ms) | ❌ |
| 1.5 | Profundidade p50 a 3 ticks | ≥ 200 USDC | **128,0 (5m) · 50,0 (15m) · 28,7 (1h) · 27,0 (4h)** | ❌ |

**⬇️ Daqui até o fim do Bloco 1 é histórico. A tabela acima é o estado
corrente.** Os números de 1.1 a 1.4 vinham do preditor com a variância
derivada, que subestimava o desvio-padrão em 6,3×. O que vem a seguir — o
"+2,7125 na banda", o decaimento monótono com a latência, o ECE de 0,207 —
descreve aquele preditor, e fica no documento porque é como se chegou aqui.
**O escopo é este bloco só:** os Blocos 2 em diante já trazem os números
correntes, e a tabela de pendências no fim da página também.

**1.1 inverteu duas vezes, e a última leitura é a que tem lastro.** 5 h deram
−41,57; 20 h deram +102,92; 24 h limpas dão **−53,28**. A gravação de 20 h
carregava 837 s de silêncio e descartou 42% dos snapshots — e buraco de livro
não erra para os dois lados, porque o preenchimento simulado usa o último
snapshot conhecido, que numa lacuna é sistematicamente melhor que o real.

**E o −53,28 é entrada múltipla, não borda negativa.** `max_1_entradas` dá
+2,7125, `max_3` dá −98,39 e `max_10` dá −221,64. E +2,71 em 640 trades é
0,4 centavo por trade com drawdown de −108 — o que a §2d-bis depois mostrou é
que esses 640 trades são **exatamente** a banda 240-120 s, e que o número
sobrevive à grade de latência inteira. É edge de direção real; o que ele não é
é taker viável, pelas razões da seção seguinte.

### A banda 240-120 s — o que a §2d-bis achou, e o que ela não salva

A `curva_de_horizonte` forçou o preditor bruto em cada faixa de tempo restante.
Deu edge em **exatamente uma**:

| banda | trades | PnL USDC | hit |
|---|---|---|---|
| > 240 s | 695 | −53,28 | — |
| **240-120 s** | **640** | **+2,7125** | **0,7063** |
| 120-60 s | — | −0,17 | — |
| 60-30 s | — | −49,16 | — |
| < 30 s | — | −5,41 | — |

Dois caminhos independentes batem ao centavo: a célula 240-120 s da curva e o
`faixa_de_tempo.comparacao.restrito` da rodada com `--tempo-restante-min 120
--tempo-restante-max 240`.

Sensibilidade a latência **dentro da banda** — decaimento monótono, positivo em
toda a grade:

| latência | trades | PnL USDC |
|---|---|---|
| 150 ms | 640 | +3,3119 |
| 300 ms | 640 | +2,7125 |
| 600 ms | 640 | **+1,3488** ← o 1.4 |
| 1000 ms | 640 | +0,4736 |

**A lacuna de medição que invalidava o 1.4, achada e fechada (PR #41).** O 1.4
lê `sensibilidade_latencia.600ms`, e esse bloco — junto com `curva_de_edge` e
`curva_de_capacidade` — rodava a própria configuração só com threshold e
latência, **ignorando `--tempo-restante-*`**. Numa rodada restrita o 1.1 saía da
banda e o 1.4 de `>240s`: dois critérios do mesmo relatório sobre populações
diferentes, sem aviso. O 1.4 publicava −54,3953 — os 695 trades de `>240s`, não
os 640 da banda —, e por isso **nunca era de fato remedido**, como a §2d-bis
mandava. Corrigido em `FaixaDeOperacao` (runner): os três diagnósticos herdam a
banda operada, e a rodada irrestrita fica idêntica à de antes (travado em
`test_m2_e2e`). Com a correção, **o 1.4 passa: +1,3488**.

**Ressalva de sobreajuste.** O `curva_de_edge` restrito é positivo de 0,01 a
0,05, melhor em 0,03 (+3,0489), e negativo a partir de 0,08. Esse 0,03 foi
escolhido OLHANDO esta amostra; adotá-lo exige repetir em dia independente,
senão é sobreajuste. O threshold registrado segue **0,02**.

**E um dia não é veredito.** Tudo acima é 2026-08-24. Uma banda que passa aqui
autoriza o próximo experimento — remedir 1.1-1.5 restrito a ela em **dias
independentes** —, não dinheiro real.

O texto abaixo é da leitura de 20 h e fica como registro do que se acreditava
então:

- faixa calibrada (240–120 s): **+91,58** com drawdown **menor** (−43,07
  contra −60,84)
- sensibilidade a latência plana: 103,39 (150 ms) → 100,41 (1000 ms)
- `threshold_mordeu: **true**`, 5 resultados distintos

**A degenerescência do threshold acabou.** Nas horas isoladas de 22/08 a grade
inteira dava um resultado só, e isso invalidava a leitura de 1.1. Agora ela
separa (97,71 a 102,97, melhor em 0,03). O que a grade mostra, porém, é que o
limiar quase não discrimina: 568 trades passam em todos os patamares. **A
borda não vem de escolher situações boas — vem de um viés sistemático.** O
modelo prevê 0,6445 no balde operado e realiza 0,6225; a lucratividade nasce
dessa diferença contra o preço, não de seleção.

**1.3 está marcado ⚠️ de propósito, e não ✅.** O `erro` publicado é
`|prob_média_prevista − freq_realizada|`, e `freq_realizada` é a **taxa-base do
balde**, não a acurácia da previsão. No balde `<30s`: previu 0,514, realizou
0,5073 — cara-ou-coroa dos dois lados. Um preditor que cospe 0,51 constante
tira nota máxima nesse critério. Enquanto a medição for essa, 1.3 não carrega
informação, e tratá-lo como aprovado seria contar como evidência o que é
artefato de construção.

**Conserto feito (M2.13):** o relatório agora publica `curva_de_confiabilidade`
por faixa de probabilidade prevista, mais `erro_de_confiabilidade` (ECE) e
`faixas_ocupadas`. Medido contra três preditores sintéticos de 20 mil
observações:

| Preditor | `erro` (antigo) | ECE | `faixas_ocupadas` |
|---|---|---|---|
| constante 0,51 num mundo 50/50 | +0,0051 | **0,0051** | **1** |
| bem calibrado | −0,0007 | 0,0070 | 18 |
| otimista em 15 pontos | +0,1487 | **0,1487** | 15 |

**O ECE sozinho não resolve** — o constante passa nele também, porque cai todo
numa faixa só. Quem o denuncia é `faixas_ocupadas`. Por isso 1.3 virou
CONJUNÇÃO: `calibracao_avaliavel` (≥ 3 faixas com amostra) **e** ECE abaixo do
limiar. Com `calibracao_avaliavel` false o critério fica **não avaliado**, que
não é o mesmo que reprovado.

**Rodou, e o veredito é reprovação, não "não avaliado".** `calibracao_avaliavel`
veio **true** — 20 faixas ocupadas no melhor balde, 20 na banda operada —, então
a conjunção foi de fato exercida, e o ECE ficou em 0,0694 no melhor balde e
**0,207 na banda 240-120 s**. O preditor não é constante disfarçado; ele é
**confiante e errado**, com ~75 mil das ~79 mil previsões nos extremos. O 1.3
reprova com informação, que é o que o M2.13 existia para garantir.

**1.5 é o único critério de borda que reprova, e é o mais duro.** É teto de
CAPACIDADE. O backtest move 1.651,59 USDC em 568 trades — **2,91 USDC por
trade**, e o lucro é de **+0,18 USDC por trade**. A duração mais líquida (5 m)
tem p50 de 87,77 USDC a 3 ticks, **44 % do mínimo de 200** que o critério
fixou antes de existir dado. Nenhuma duração passa.

### MAKER — reprova

| # | Critério | Exigido | Medido (20 h) | |
|---|---|---|---|---|
| 1.6 | Conta fechada com fator 0,3 | positiva | **NÃO AVALIÁVEL** — a conta não fecha sem posição na fila | ⚠️ |
| 1.7 | Markout 5 s | ≥ −0,5 ¢/share | **−0,1974** (246.504 execuções) | ✅ |
| 1.8 | Horas de amostra na célula | ≥ 20 h | **65,9 h** | ✅ |
| 1.9 | Divergência com topo deslocado (emenda no VEREDITO_M2) | < 1 % | **0,20 %** (agregada: 2,82 %) | ✅ |
| 1.10 | Fórmula de reward confirmada na doc | sim | não | ❌ |

**O achado que encerra a rota, agora em amostra grande: 594 das 599 janelas
sem pool de reward.** As 5 que têm são todas de 4 h. Em 5 min, 15 min e 1 h
não há uma sequer. `rewards_daily_rate` ausente, com `rewards_max_spread` e
`rewards_min_size` presentes — ou seja, o campo existe na Gamma e vem vazio,
não é campo que ninguém lê. Estes mercados updown **não participam do programa
de rewards**, e sem pool a rota maker não tem fonte de receita.

O 1.6 positivo (+0,043 ¢/share) não salva: ele sai de `rewards + rebate −
markout`, e o rebate é um **teto** — só existe quando alguém nos executa, e a
probabilidade disso depende da posição na fila, que o WS agregado não mostra.
O relatório diz isso em `limitacao_de_fila`: o viés infla o resultado nas duas
pontas.

1.10 não pode ser resolvido daqui: `docs.polymarket.com` é inalcançável neste
ambiente. Precisa de uma máquina que chegue lá.

### A âncora está fechada

| | |
|---|---|
| Consistência em τ=0 | **0,9984** |
| Janelas elegíveis | **640** (de 647 recebidas) |
| Discordantes | 1 |
| Quartis | 134 / 168 / 170 / 168 · `concentrada: false` |
| Família de controle (`media_60s`) | 0,9528 |

Amostra grande, bem espalhada no tempo, e a família perdedora continuou
perdendo por 4,5 pontos. **Não é mais pergunta em aberto.**

### Um defeito de instrumento achado nesta rodada

`cobertura_da_gravacao` reporta **1,0** em todos os oito ativos, e o mesmo
relatório registra um silêncio de `conexao_inteira` de **3.601 s**. Os dois não
podem estar certos.

Neste caso o silêncio é benigno — é o buraco da hora 01:00, que foi excluída de
propósito (dois dos três fragmentos vieram corrompidos da origem). Por isso
`suspeita_de_assinatura_caducada` deu 0 e os oito ativos "emudeceram" com
0,152 s de dispersão: é a borda entre arquivos, não o feed. Mas **a métrica de
cobertura não descontou o buraco**, e é justamente ela que o veredito da
âncora consome desde o M2.9.

**Conserto feito (M2.13):** `coberto_s` passou a ser a SOMA dos intervalos,
cada um limitado a `idade_maxima_da_amostra_ms` — a mesma régua que o resto do
relatório usa para dizer que uma janela abriu "em lacuna". No caso desta
rodada a conta nova dá **0,9526** onde a antiga dava 1,0000, e o novo
`maior_buraco_s` sai em **3.601,0 s**, que bate com o `intervalo_s.max` e com o
silêncio de conexão inteira de 3.600,67 s. Os números agora concordam entre si.
O relatório também ganhou `buracos_s` e `silencio_inicial_s` — a borda da
frente, que só o `silencio_final_s` não via.

**⬆️ Fim do histórico do Bloco 1.** Daqui para baixo, estado corrente.

---

## Bloco 2 — M3: modelo e calibração

| # | Item | Estado |
|---|---|---|
| 2.1 | Modelo TWAP endgame | ✅ `engine/twap.py` — variância agora **medida** (`engine/variancia.py`), não derivada; a derivada errava por 39–48× |
| 2.2 | Modelo horário | ✅ `engine/hourly.py` |
| 2.3 | Curva de calibração sobre gravação real | ✅ **MEDIDA e CALIBRADA** — ECE de **0,0126 a 0,0493** nos cinco baldes, 20 faixas ocupadas em cada. *(Com o preditor derivado eram 0,0694 no melhor balde e 0,207 na banda operada, viés MISTO e SEM ORDEM — ver abaixo.)* |

### O 2.3 mudou de natureza, não só de estado

Até 2026-08-25 este item estava marcado "⏳ existe por bucket; falta amostra".
As duas metades da frase envelheceram, e por motivos opostos.

**O que existia não media calibração.** O `erro` publicado era
`|prob_média_prevista − freq_realizada|`, e `freq_realizada` é a **taxa-base do
balde**. Um preditor que cospe uma constante igual à taxa-base tirava nota
máxima sem saber nada — foi o que a rodada de 20 h expôs no balde `<30s`:
previsto 0,514 contra realizado 0,5073, cara-ou-coroa dos dois lados, e o
critério "passou" com 0,0067.

**O M2.13 trocou o instrumento.** Agora o relatório publica
`curva_de_confiabilidade` por faixa de probabilidade prevista,
`erro_de_confiabilidade` (ECE) e `faixas_ocupadas`. O critério virou
CONJUNÇÃO — `calibracao_avaliavel` (≥ 3 faixas com amostra) **e** ECE abaixo do
limiar —, porque o ECE sozinho também não pega o preditor constante: ele cai
todo numa faixa só.

**E a amostra deixou de faltar.** São 24 horas contínuas e limpas do dia 24,
mais 23 do dia 23 e 5 do dia 25.

Ou seja: o que faltava no 2.3 **não era código nem dado** — era rodar o
backtest com o instrumento novo sobre a gravação que já existia. **Rodou.** A
mesma rodada fechou o 2.3 do M3 e tirou o critério 1.3 do M2 do limbo do "não
avaliado": ele ficou **avaliado e reprovado**, com `calibracao_avaliavel` true
e ECE 0,207 na banda operada.

**E em 30/08 o 1.3 deixou de reprovar.** O ECE de 0,207 não era defeito do
sinal: era a variância derivada subestimando o desvio-padrão em 6,3×, o que
saturava `P(Up)` nos extremos — exatamente as ~75 mil previsões de ~79 mil
citadas acima. Com a `V(t)` medida em dia anterior ao avaliado (§2d-ter do
`VEREDITO_M2.md`), o ECE cai para 0,0126–0,0493 nos cinco baldes. Os números
dos três parágrafos anteriores descrevem o instrumento antigo e ficam como
registro de como se chegou aqui.

---

### O analisador ficava mudo por três horas — M2.15

Descoberto em 2026-08-26, do pior jeito possível: a rodada de 24 h foi
lançada, o operador rodou `tail -f` no log e viu um arquivo **vazio**. A
leitura de fora foi "travou".

Não estava travado. O backtest tem 6 chamadas de `print` no arquivo inteiro, e
todas são de erro ou do JSON final — ele não imprimia **nada** enquanto
processava. Três horas e meia de silêncio absoluto, por construção.

Agora ele diz onde está, a cada 500 mil registros:

```
[04:12:07] passada 1: comecando sobre 24 arquivo(s)
[04:13:41] passada 1: 500,000 registros | 5,319/s | 1.6 min nesta passada | rss 1.84 GiB
```

Duas decisões que não são detalhe:

**Vai para STDERR.** O relatório sai por stdout; progresso ali corromperia o
JSON, e quem redirecionasse `> relatorio.json` receberia um arquivo que não
parseia. Há teste travando isso.

**`rss` está junto porque o modo real de falhar numa máquina de análise não é
erro — é swap.** E swap não parece travamento: parece lentidão sem fim. Sem o
número, não há como distinguir "está devagar" de "não vai terminar".

Uma armadilha apareceu na implementação: `ru_maxrss` vem em **bytes no macOS**
e em **kilobytes no Linux**. A conta errada dá 1024× de diferença — e como a
máquina de análise é um Mac e os testes rodam em Linux, o erro passaria
despercebido nos dois lugares por motivos opostos. Há teste parametrizado nas
duas plataformas.

---

### `scripts/analisa_dia.sh` — a colagem saiu do caminho

Três caracteres do zsh interativo morderam esta campanha, cada um custando
uma rodada:

| | O que acontece |
|---|---|
| `#` | sem `interactive_comments`, vira `command not found: #` e a linha seguinte roda solta |
| `!` | expansão de histórico, **ativa mesmo dentro de aspas duplas** — `echo "PID $!"` prende o terminal em `dquote>` |
| `&` | numa colagem de várias linhas, muda o que roda em primeiro e segundo plano |

O custo do `!` não foi o incômodo: foi uma rodada de 24 h que **nunca
começou** e ninguém percebeu, porque o log que provaria isso também nunca foi
criado. O operador esperou horas por um processo inexistente.

Agora é um comando curto, sem caractere especial nenhum:

```
./scripts/analisa_dia.sh 20260824
```

Ele monta os links **só das horas cujo gzip abre inteiro** — a hora corrente e
a que morreu no meio de uma escrita reprovam, e incluí-las envenenaria a
rodada por causa de um arquivo, depois de três horas de processamento.

**E ele prova que subiu antes de dizer que subiu.** Espera a linha
`passada 1: comecando` aparecer no log; se o processo morrer antes, imprime o
log e sai com erro.

Isso não é paranoia de projeto — os dois testes do próprio script pegaram dois
defeitos nele:

1. A primeira versão checava só "arquivo não vazio", e **anunciou `rodando`
   para um processo que tinha acabado de morrer** com `ModuleNotFoundError`.
   Mensagem de erro também enche o arquivo.
2. A segunda passava caminho **absoluto** no `--json`, que a contenção de
   saída do M2.5 recusa. O próprio guard do script mostrou o erro.

---

## Bloco 3 — M4: execução e travas (**nada existe**)

`main.py:153` — *"Trava do M1: só SIM existe. SHADOW/LIVE chegam no M4."*

| # | Item | Estado |
|---|---|---|
| 3.1 | `risk/gates.py` | ✅ **M4.1** — 8 portões, 28 testes |
| 3.2 | Cliente de ordens, assinatura EIP-712, auth do CLOB | ✅ **2026-08-30** — `execution/auth.py` (25 testes: L2 HMAC-SHA256 + typed data do L1) e `execution/ordem.py` (33 testes: struct EIP-712, valores, `AssinadorLocal`). Travado por **conferência diferencial** contra o `polymarket-client==0.6.0`: sete casos de valores, o typed data e a assinatura, byte a byte. Fatos em API_NOTES §12.14. Falta só falar com o servidor de verdade |
| 3.3 | Modo SHADOW | ✅ **M4.3** — decide tudo, envia nada, 14 testes |
| 3.4 | Modo LIVE + trava tripla (`MODE=LIVE` + `CONFIRM_LIVE` + `EU ACEITO O RISCO`) | ✅ **2026-08-30** — `risk/autorizacao.py`, 22 testes. A frase é comparada EXATAMENTE; `escolher_executor` só chega em LIVE por ela, e a recusa lista TODOS os bloqueios |
| 3.5 | Ordens FOK, conexão quente, nonce/idempotência, rejeição e timeout | 🟡 **código completo, nunca falou com o CLOB de verdade** — `execution/cliente.py` (47 testes), com o transporte `httpx` já escrito e testado contra `MockTransport`: FOK, id determinístico, e **timeout ≠ recusa** (três estados; `INCERTA` é terminal e obriga reconciliação). Falta **uma resposta do servidor de verdade** — o que exige rede e credencial, não código. O `content=` (e não `json=`) é o que preserva os bytes assinados; falha de rede vira `INCERTA` e nunca recusa, porque recusa autorizaria reenviar uma ordem que talvez esteja no livro |
| 3.6 | Trava: stake máximo por trade e por janela (US$ 5) | ✅ **M4.1** — mais exposição total, posições e disjuntor |

### 3.1 e 3.6 fecharam — os portões vêm ANTES do cliente de ordens

Ordem deliberada: um cliente de ordens sem portão é uma máquina de perder
dinheiro que já funciona; um portão sem cliente é um teste que não custa nada.

| Portão | Recusa quando |
|---|---|
| `modo_nao_opera` | modo não é LIVE — SIM e SHADOW nunca enviam |
| `disjuntor_armado` | a perda do dia estourou, ou o registro estava ilegível |
| `feed_parado` | algum feed está velho — preço velho é preço que já não existe |
| `ordem_mal_formada` | shares ≤ 0, ou preço fora de (0, 1) |
| `preco_fora_da_faixa` | fora de [0,05 · 0,95] — a 0,97 arrisca-se 0,97 para ganhar 0,03 |
| `stake_acima_do_teto` | a ordem passa de 5 USDC |
| `janela_no_teto` | o acumulado no MESMO mercado passa de 15 USDC |
| `exposicao_no_teto` | o capital simultâneo em risco passa de 50 USDC |
| `posicoes_no_teto` | mais de 5 janelas com posição aberta |

Três decisões de projeto, cada uma cobrindo uma forma concreta de sangrar:

**Falha fechada.** `avaliar()` começa negando. Registro do dia ilegível **arma
o disjuntor** em vez de assumir que estava tudo bem — não dá para distinguir
"arquivo corrompido" de "arquivo com o disjuntor armado que não consigo ler".

**O disjuntor gruda.** Não desarma porque o número melhorou depois (perdeu 11,
armou, ganhou 5 → continua armado), não desarma na virada de data, e
**sobrevive a reinício** porque é gravado em disco com rename atômico. Sem
persistência ele viraria um limite por vida de processo — que não é limite
nenhum: bot perde, processo cai, systemd reinicia, contador zera, bot perde de
novo.

**Toda recusa se nomeia.** `Decisao.motivo` é constante de `MOTIVOS`;
construir uma recusa com frase livre levanta `ValueError`. Recusa anônima não
vira métrica nem alarme, e não distingue "o bot está travado" de "o bot não
achou trade".

Os tetos não são chute de conforto — saem do que o M2 mediu: 2,91 USDC
movimentados por trade, 0,18 de lucro, e profundidade mediana de 87,8 USDC na
duração mais líquida. Subir qualquer um deles deve esperar a curva de
capacidade (M2.14) dizer onde o teto está.
| 3.7 | Trava: perda diária máxima → disjuntor | ✅ **M4.1** — código usa **US$ 25**, este doc dizia 20; decisão sua |
| 3.8 | Trava: 4 perdas consecutivas → pausa de 1 h | ✅ **M4.4** — persiste, atravessa a meia-noite, 9 testes |
| 3.9 | Trava: exposição simultânea máxima | ✅ **M4.1** — código usa **5 janelas / US$ 50**, este doc dizia 2; decisão sua |
| 3.10 | Trava: feed velho / relógio > 250 ms / spread anômalo | 🟡 **2 de 3 + sensor parcial** *(a metade que faltava — sincronia verificada — virou o 5.4, e ele está ✅)* — feed ✅ (M4.1), spread ✅ (M4.4, teto 0,04), relógio 🟡 `live/relogio.py` detecta **anomalia** (pior ativo, 250 ms) e **salto** de relógio, e recusa em LIVE sem fonte. **Não certifica sincronia**: latência e offset se cancelam. Falta NTP verificado como pré-condição — ver abaixo |
| 3.11 | Kill switch: arquivo `KILL` + botão no dashboard | ✅ **2026-08-30** — arquivo ✅ **M4.4** (lido a cada ordem, ilegível = acionado); botão ✅ `ui/server.py`, 8 testes. **Só arma, não desarma**: o que para o bot fica parado até uma pessoa apagar o arquivo na máquina, e o dashboard não tem autenticação — uma rota que desarmasse seria uma rota para religar um bot parado de propósito. Chave puxada por `touch` fora da página aparece nela |
| 3.12 | Suíte de testes das travas (uma por trava) | ✅ — **83**: 36 no portão, 27 nas travas novas, 20 no relógio |
| 3.13 | SHADOW rodando 24 h sem crash | 🟡 **o processo existe** desde 2026-08-30 (`live/shadow.py` + `live/ciclo.py`, 28 testes). `python -m pulsearb.live.shadow --duration 24h`, 53 testes. **A resolução alimenta o disjuntor**: sem ela `perdas_seguidas` e `pnl_realizado_usdc` ficavam em zero e o ensaio aprovaria o que o LIVE já teria recusado. **Cada rodada grava o seu próprio diário** (carimbado no instante de início): o default fixo somava a rodada de teste com o ensaio, e a comparação SHADOW × backtest lia duas populações como uma. Falta **rodar** as 24 h e ver o resultado — o que exige máquina, não código |

### A terceira trava do 3.10: o que ela pega, e o que ela NÃO pega

Feed velho e spread anômalo eram medidos desde o M4.1/M4.4. "Relógio > 250 ms"
estava no item desde a especificação e **nunca teve fonte**. Agora tem uma — e
a parte mais importante deste registro é o limite dela, porque a primeira
versão prometeu mais do que entrega e a revisão do PR #47 pegou.

**A medição.** Cada tick do feed-verdade traz o carimbo do servidor; na
chegada, olhamos o nosso relógio. Chamando `offset` a diferença entre o nosso
relógio e o verdadeiro (negativo = estamos atrasados):

    atraso = chegada_local − carimbo_servidor = latencia + offset

**Duas incógnitas numa equação só**, medidas de UMA via.

**O erro que eu cometi.** Escrevi que isso é um *limite superior* da deriva —
"pequeno prova relógio bom". É falso, e falso na direção perigosa, porque as
parcelas se cancelam:

| offset | latência | medido | portão 250 ms | erro real no `seconds_left` |
|---|---|---|---|---|
| **−400 ms** | **400 ms** | **0 ms** | **passa** | **400 ms** |
| −100 ms | 120 ms | +20 ms | passa | 100 ms |
| +300 ms | 80 ms | +380 ms | recusa | 300 ms |

Relógio local atrasado é justamente o caso que infla o `seconds_left` — o bot
opera achando que sobra mais tempo do que sobra — e é justamente o caso que a
latência positiva mascara. Um `abs()` depois não recupera nada: o valor já saiu
zerado da subtração.

**O que a trava vale, então.** É **detector de anomalia**, não certificado de
relógio. `|atraso|` grande prova que algo está grande; `|atraso|` pequeno é
ausência de alarme deste sensor, e nada mais.

**As duas metades que fecham o buraco:**

1. **NTP/chrony verificado é PRÉ-CONDIÇÃO de deploy**, não algo que software
   nosso possa provar com um feed de uma via. Entra na lista do Bloco 5, ao
   lado da carteira dedicada e da imagem Docker.
2. **O salto do relógio esse dá para pegar**, e é o modo de falhar mais comum:
   o NTP corrige de vez no meio da operação. `_Saltos` compara o avanço do
   relógio de parede com o do monótono — lendo os dois por conta própria, não
   o `chegada_ms` do chamador, que carrega o espaçamento entre ticks e faria
   um feed reproduzido parecer relógio pulando. Salto detectado recusa por
   30 s: as medianas anteriores foram calculadas com o relógio antigo.

**Uma janela por ativo, e o portão lê a pior** — também da revisão. Com janela
única, um ativo continuamente atrasado entre oito saudáveis fica abaixo da
mediana global; os ticks dele chegam sem parar, então a checagem de feed velho
também não acusa, e uma ordem naquele ativo sairia com preço velho e o portão
dizendo que está tudo bem.

**Três recusas, todas deliberadas:**

- **fonte muda** (sem tick, amostra velha, ou salto recente) → recusa. Não
  saber custa o mesmo que saber que está ruim; tratar não-sei como zero é o
  defeito do `cobertura_da_gravacao`, que o M2 já pagou uma vez.
- **em LIVE sem fonte instalada** → recusa tudo. Uma trava que se auto-desativa
  quando ninguém a ligou não é trava. Fora do LIVE a ausência não recusa —
  o SHADOW existe para ensaiar.
- **carimbo no futuro** → recusa igual, em módulo. É o que resta de sinal de
  relógio local atrasado depois que a cancelação come o resto.

**Como a trava se provou real:** ao ligá-la, **todos** os testes de caminho
LIVE passaram a recusar por `relogio_nao_monitorado`. Os testes passaram a
instalar a fonte, como o ciclo ao vivo terá de fazer.

**O que ainda falta:** (a) o NTP verificado do item 1 acima; (b) o ciclo ao
vivo construir o `PortaoDeRisco` passando a fonte — não há ponto de construção
hoje, é o mesmo buraco do 3.13. Enquanto não existir, o LIVE recusaria tudo,
que é o lado certo para errar.

### 3.4 e 5.4 fecharam — a autorização para LIVE existe antes do cliente

Mesma ordem que fechou o 3.1 e o 3.6 antes do cliente de ordens: **a licença
vem antes da máquina**. Uma trava tripla escrita às pressas no dia em que o
cliente chegar é uma trava que ninguém testou.

`risk/autorizacao.py` responde uma pergunta só — *pode entrar em LIVE?* — e a
responde com **todos** os bloqueios de uma vez:

| Bloqueio | O que é | O que impede |
|---|---|---|
| `modo_nao_e_live` | configuração | o default nunca opera |
| `sem_confirmacao_explicita` | `PULSEARB_CONFIRM_LIVE=1` | um `.env` copiado de outra máquina |
| `sem_aceite_do_risco` | a frase exata, digitada | automação e engano de dedo |
| `relogio_nao_sincronizado` | o daemon de NTP (5.4) | operar com `seconds_left` errado |
| `sem_cliente_de_ordens` | 3.2 e 3.5 | acreditar que trava substitui código |

**Três travas de intenção porque uma não basta e duas se copiam juntas.** A
frase é comparada **exatamente**: `true`, `sim` e `eu aceito o risco` são
recusados, e há sete testes parametrizados travando isso. Um booleano se digita
sem pensar — é justamente o que a terceira trava existe para impedir. Espaço
nas pontas passa, porque é artefato de terminal e não descuido.

**Todos os bloqueios, e não o primeiro.** Reportar um por vez faria o operador
consertar, rodar, descobrir o próximo — cada volta achando que era a última.
Pior: enquanto o cliente de ordens não existisse, ele apareceria primeiro e a
trava tripla nunca seria exercitada.

**O 5.4 fecha a metade que o sensor de tempo não fecha.** `live/relogio.py`
mede `latencia + offset` numa subtração só e as duas se cancelam — ver 3.10.
Sincronia verificada vem de quem faz medição de duas vias: o daemon de NTP da
máquina. `risk/sincronia.py` pergunta ao systemd, ao chrony e ao macOS, nessa
ordem, e distingue três respostas em vez de duas:

- **sincronizado** → passa
- **daemon respondeu que não** → recusa, e o conserto é investigar rede/fonte
- **não determinado** (sem daemon, formato desconhecido, timeout) → recusa, e o
  conserto é instalar e habilitar NTP

Colapsar os dois últimos em "não" mandaria o operador pelo caminho errado. E
não determinado recusa igual: um relógio não verificado tem o mesmo efeito no
`seconds_left` que um relógio errado — a diferença é só a nossa ignorância.

Três defesas no subprocesso, porque rodar comando externo num processo que
decide é coisa que dá errado: timeout obrigatório de 3 s, nunca levanta
exceção, e **não roda no caminho quente** — é verificação de subida, e há um
teste que quebra se alguém a chamar com a sincronia já em mãos.

**O que isto NÃO faz:** autorizar. Com `cliente_de_ordens_existe=False`, que é
o estado real, a autorização nunca sai positiva. O caminho positivo existe e é
testado para que os testes de recusa provem alguma coisa — se tudo recusasse
de qualquer jeito, eles não provariam nada.

### O ciclo de decisão ao vivo existe — `live/ciclo.py`

Esta seção dizia, até 2026-08-30: *"o executor existe e está testado, mas não
há ciclo de decisão ao vivo para alimentá-lo"*. As peças listadas abaixo
estavam todas prontas; faltava **a orquestração**, e é ela que entrou.

`CicloAoVivo` recebe `FeedEvent`s, roteia para o estado e chama o motor:

| Evento | Vai para | Guarda |
|---|---|---|
| `rtds` + `crypto_prices_twap_sixty` | `precos.anotar` | só este tópico; e18 exato e carimbo do servidor obrigatórios |
| `rtds` + outro tópico | contado, ignorado | `crypto_prices` (spot) não é a âncora |
| `poly_ws` | `livros.aplicar` | mesmo `OrderBook` do critério 1.5 |
| outra fonte | **contada** | "não chegou nada" e "não sei ler" têm consertos opostos |

**Nenhum parser novo.** Cada evento é lido pela MESMA função do backtest —
`parse_rtds_event`, `e18_do_evento`, `eventos_do_payload`, `LivrosAoVivo`. As
duas últimas eram privadas dentro do `backtest/__main__.py` e mudaram de casa
para `feeds/`; duas cópias fariam uma divergência de parsing aparecer como
diferença de mercado, que é o que a comparação SHADOW×backtest existe para
detectar.

**Sem rede aqui dentro.** O ciclo não abre socket, não faz HTTP e não dorme.
Isso não é elegância: é o que permite alimentá-lo com uma **reprodução de
gravação** e rodar SHADOW e backtest sobre o MESMO dado. Um ciclo que só
soubesse falar com a rede não poderia ser confrontado com nada — e a
confrontação é a razão de o SHADOW existir.

#### Um buraco que apareceu ao montar o ciclo, e não tinha dono

`PrecosAoVivo` devolve o último preço de um ativo **sem olhar a idade dele**.
Um ativo mudo entre sete saudáveis decidiria com preço velho, e nada mais no
caminho pegaria: `livros` mede silêncio por token, mas preço não; o portão
`feed_parado` olha o feed, não o ativo.

O ciclo fecha isso calculando `feeds_saudaveis` **pelo pior ativo** — a mesma
escolha do sensor de relógio, e pela mesma razão. Fechar tudo por causa de um é
conservador e é o lado certo para errar: com entrada única por janela, o custo
de parar é uma janela perdida; o de operar com preço velho é uma posição tomada
contra um mercado que já se moveu. `precos_velhos_s` **nomeia** quais ativos
estão velhos — "feed parado" sem dizer qual não é alarme acionável.

Sem nenhum preço ainda, a saúde é `false`: bot recém-subido não sabe nada, e
não saber não autoriza.

#### E o processo que lhe dá rede — `live/shadow.py`

O ciclo não abre socket por design. `ProcessoShadow` é quem abre, e reusa a
fiação do recorder — `RtdsFeed`, `PolyMarketWsFeed`, `MarketDiscovery`, as
mesmas classes que gravaram as 24 h do M2. Se o SHADOW abrisse os sockets por
outro caminho, uma diferença de assinatura ou de reconexão faria a população
que ele vê divergir da que o backtest leu, e a comparação entre os dois
perderia o sentido.

```
python -m pulsearb.live.shadow --duration 24h \
    --curva-de-variancia relatorios/VARIANCIA_23AGO.json
```

**A ligação que a fábrica faz, e que é a mais fácil de esquecer:** o
`PortaoDeRisco` recebe `relogio_do_servidor=precos.relogio` — a MESMA
instância que o ciclo alimenta tick a tick. Sem ela a trava de relógio diria
"não sei" a cada ordem, o diário sairia com `relogio_nao_monitorado` em toda
linha, e nenhum dos portões que interessam seria exercitado. Há teste travando
a identidade da instância, porque uma cópia não recebe os ticks.

**Redundância no RTDS, como no recorder.** `rtds_conexoes` (default 2)
conexões ao mesmo endpoint. Conexão individual já produziu lacunas de 30 a
306 s, e uma lacuna aqui que a gravação não tem faria o SHADOW perder ticks de
âncora que o backtest enxerga — furando exatamente a comparação que justifica o
SHADOW. O tick repetido que a redundância produz é descartado **no ciclo**, não
no processo, para valer também na reprodução de gravação; e é **contado**
(`preco_repetido`), porque perto de zero com duas conexões significa que a
redundância não está funcionando.

**Só o jogo TWAP é operado.** A janela horária resolve pelo candle 1h da
Binance, e a âncora dela é o campo `o` do `kline_1h` (`engine/hourly.py`) — não
o `twap_sixty`. Um processo que só assina RTDS não tem essa série, e
`estimar_prob_up` cairia em `prob_up_hourly` com a âncora do observável errado:
toda probabilidade horária sairia de uma série que não resolve aquela janela.
`jogos_operados` recusa, conta (`jogo_sem_feed_proprio`) e só se amplia quando
o feed da Binance estiver ligado e roteado.

**Só os ativos OPERADOS entram no feed.** `all_price_assets` inclui os
`extra_price_assets`, que existem para gravação e backtest futuro. Como
`feeds_saudaveis` fecha pelo pior ativo, um SOL mudo bloquearia intenções de
BTC/ETH saudáveis — o gate de saúde passaria a depender de ativos que o bot nem
opera.

**O tamanho da ordem não sai do teto em USDC.** São unidades diferentes:
`stake_max_por_trade_usdc` é USDC e quem o aplica é o portão, sobre
`shares × preço`; `shares_por_trade` é em SHARES, e o default (5) é o mínimo
que o mercado aceita (§12.5). E o motor passou a recusar ordem abaixo desse
mínimo, como o backtest já fazia — sem isso o SHADOW registraria `pode=true`
para ordem que a corretora rejeitaria.

**Assinaturas rodam, não acumulam.** Token de janela encerrada é desassinado
depois da carência de resolução — a MESMA de `pulsearb.tempo` que o recorder
usa. Sem isso, 24 h de descoberta acumulariam milhares de assinaturas, e cada
reconexão reenviaria o conjunto histórico inteiro no frame inicial.

**Três cadências, e cada uma tem um número por trás:**

| Laço | Cadência | Por quê |
|---|---|---|
| decisão | 1 s | o feed entrega 1,061 tick/s por ativo; decidir mais rápido que o dado chega não muda nada |
| descoberta | 30 s | janela de 5 min descoberta com 30 s de atraso ainda sobra 4,5 min, e a faixa operada são os últimos 240 s |
| relato | 60 s | estado no log sem poluir |

O custo da primeira é até ~1 s de atraso a mais que o backtest — dentro da
grade de latência que o M2 já mediu (150 a 1000 ms).

**Um passo que levanta NÃO derruba o processo.** O SHADOW existe para rodar
24 h e mostrar o que aconteceu; cair no primeiro evento estranho entregaria
zero informação sobre as outras 23 horas. O erro sai nomeado no log e o laço
segue — e há um teste que trava isso, para que ninguém "limpe" o `try/except`
achando que ele esconde bug.

**Janela não-operável também é assinada.** O motor decide se opera e o diário
quer o motivo; não ver o livro de uma janela recusada trocaria "recusei por X"
por "não sei nada sobre ela", que é justamente o que o M2 quer medir.

**O que ainda falta para o 3.13:** rodar. As 24 h contínuas exigem máquina e
tempo, não código.

### As peças que o ciclo casa

*Escrito enquanto elas eram construídas, e mantido: cada uma carrega uma
decisão de projeto que continua valendo. O que mudou em 2026-08-30 é que o
componente que as casa passou a existir — a seção acima.*

O que ainda descreve o estado: o `main.py` é dashboard mais feeds, quem tem a
fiação de rede é o recorder (957 linhas: descoberta, rotação de janelas,
assinatura, livro), e quem tem a lógica de decisão sobre gravação é o
`BacktestRunner`. O `CicloAoVivo` é o consumidor da primeira e o par ao vivo da
segunda.

Primeira peça pronta: **`live/rastreador.py`** — quais janelas estão abertas
agora e quanto falta em cada uma.

`seconds_left` é o número que ele existe para produzir, e ele decide mais do
que parece: escolhe o balde de calibração, e o M2 mediu erro de **0,008** na
faixa 240–120 s contra **0,240** acima de 240 s. Trinta vezes. Um
`seconds_left` deslocado não degrada a decisão — toma a decisão na faixa
errada.

Daí `duracao_do_slug` ter saído do backtest para `markets/discovery.py`: as
duas pontas passaram a usar **a mesma função**, e há um teste que compara as
identidades. Com duas cópias, uma divergência entre SHADOW e backtest
pareceria diferença de mercado quando seria diferença de aritmética — e é
justamente essa comparação que justifica o SHADOW existir.

Falha fechada em quatro casos, cada um com nome próprio em `descartes`:
`nao_operavel`, `sem_fechamento_legivel`, `sem_par_de_tokens` e `ja_fechada`.
O contador responde a pergunta que importa quando o bot não opera: *ele não
achou janela, ou achou e jogou fora?*

Segunda peça pronta: **`live/livros.py`** — o livro de cada token, ao vivo.

Fino de propósito: reusa `OrderBook`, a MESMA classe com que o critério 1.5
mediu os 87,8 USDC. Se o shadow medisse profundidade de outro jeito, a
comparação entre os dois não diria nada sobre capacidade.

Carrega duas defesas que o M2 pagou caro para aprender:

**Silêncio é por TOKEN, não por feed.** É a lição do M2.7/M2.10 no RTDS —
tópico mudo com a conexão viva. O feed do CLOB pode estar impecável enquanto o
livro de um token não recebe nada há minutos, e o portão `feed_parado` olha o
feed, não o token. `resumo()["mudos"]` é o número que nenhum outro alarme daria.

**Delta sem snapshot é contado, não engolido.** A gravação de 20 h mediu
187.452 dessas observações. Aplicá-las a um livro vazio inventaria
profundidade; ignorá-las em silêncio esconderia que o livro está incompleto.

Uma escolha registrada: `livro()` devolve o **objeto vivo**, não uma cópia — o
próximo delta o reescreve embaixo de quem o guardar. Clonar a cada consulta
custaria uma cópia por tick por token no caminho quente, para proteger um uso
que a decisão não faz. Está no docstring e há dois testes travando o
comportamento, incluindo o de `.clone()` para quem precisar congelar.

Terceira peça pronta: **`live/precos.py`** — TWAP corrente, volatilidade e a
âncora de cada janela.

**A âncora ganha ao vivo um jeito de faltar que o backtest não tinha.** O M2 a
fixou em τ=0 — valor do stream no instante da abertura, 0,9984 sobre 640
janelas. Mas se o processo subiu às 12:03 e a janela abriu às 12:00, o valor de
12:00 não existe em lugar nenhum: a série começa quando o bot começa. Usar a
amostra mais antiga disponível seria inventar a âncora e errar a janela inteira
em silêncio — exatamente o que `ancora_verificada` recusa fazer.

**Consequência operacional que precisa ser esperada:** o bot recém-iniciado
**não opera nada por até uma janela inteira** — a de 4 h inclusive. Não é
defeito, é a âncora sendo honesta. `sem_ancora` separa os dois diagnósticos:
`serie_nao_alcanca_a_abertura` é normal ao subir; `lacuna_no_instante_da_abertura`
persistente aponta para o feed.

A busca é a **mesma** do M2: `SerieE18AoVivo` compõe `StreamE18` em vez de
reimplementar `em()`, e há um teste comparando as duas respostas. Uma segunda
cópia dessa busca seria a forma mais silenciosa possível de o SHADOW e o
backtest discordarem sobre a âncora.

Âncora resolvida fica **fixada**: a abertura é um instante, e reler a série
depois daria outro valor conforme os pontos velhos são podados.

Quarta e última peça: **`live/motor.py`** — o laço.

O que sobra de novo nele é só a **orquestração**. Cada etapa reusa o que o
backtest usa, e essa é a regra que faz o SHADOW valer alguma coisa:

| Etapa | Compartilhado |
|---|---|
| duração da janela | `markets.discovery.duracao_do_slug` |
| âncora | `analysis.anchor_sweep.StreamE18.em` |
| probabilidade | `engine.decisao.estimar_prob_up` (extraído do `BacktestRunner`) |
| edge | `backtest.runner.edge_liquido` |
| livro e profundidade | `backtest.book.OrderBook` |
| portões | `risk.PortaoDeRisco.avaliar_risco` |

Quando falta uma peça a resposta é sempre a mesma — não opera, e conta o
motivo. `pulos` responde à pergunta operacional que mais vai ser feita: *o bot
está vivo e não opera, por quê?*

```
sem_ancora                  esperado logo após subir
volatilidade_nao_calibrada  some depois de 20 retornos
sem_livro_confiavel         token mudo, não mercado parado
fora_da_faixa_de_tempo      o gatilho chegando cedo — foi o BUG 2 do M2.6
edge_abaixo_do_threshold    só este fala sobre a BORDA
```

O motor é **síncrono e sem I/O**: dá para simular seis horas de mercado num
teste sem esperar seis horas e sem fingir rede.

Três defeitos apareceram ao escrevê-lo, e valem registro. Eu tinha fixado
`JOGO_TWAP` no motor — janela horária seria estimada com o modelo errado, e os
dois jogos são fisicamente diferentes (API_NOTES §13.4). `JanelaAoVivo` passou
a carregar o jogo. `feeds_saudaveis` estava chumbado em `True` e virou
parâmetro do `tick()` — o motor não decide sobre saúde de feed, ele repassa e o
portão recusa. E o `Executor` não declarava `portao`, que o laço precisa para
dar baixa na exposição.

**O ciclo está fechado.** O que falta agora é o cliente de ordens (3.2), que
exige credencial, e a trava tripla do LIVE (3.4).

### 3.3 fechou — o SHADOW ensaia o caminho inteiro

O backtest diz o que teria acontecido sobre gravação. O SHADOW diz o que
teria acontecido **ao vivo**: feed real no tempo real, decisão com a latência
real, livro no estado em que estava. A diferença entre os dois é a única
medida honesta de quanto do resultado do backtest é artefato de olhar o
passado com calma.

**A regra que o faz valer: mesmo caminho.** Executor é uma interface com duas
implementações que divergem só no último passo — uma escreveria na rede, a
outra escreve num arquivo. Os portões de risco rodam iguais, e a exposição é
contabilizada, senão os tetos por janela nunca seriam exercitados.

O portão de **modo** é a única exceção, e de propósito: ele existe para
impedir envio, e no shadow não há envio para impedir. Rodá-lo faria toda
intenção sair como `modo_nao_opera` e o diário perderia justamente o que
justifica o ensaio — *qual portão estaria segurando se o modo fosse LIVE*. Daí
`avaliar_risco()` existir separado de `avaliar()`; quem envia chama a segunda.

**Pedir LIVE falha alto**, com `NotImplementedError`. Cair para SHADOW em
silêncio seria a falha mais cara possível: o operador acredita que está
operando, o dinheiro não se move, e a descoberta vem quando alguém for
conferir o saldo.

**O que o SHADOW não prova:** que a ordem seria preenchida. Ninguém do outro
lado sabe que ela existe — não há fila, não há concorrência pelo nível, o
mercado não reage. O diário guarda `melhor_bid`, `melhor_ask` e
`profundidade_no_topo` do instante para que essa conta possa ser feita depois.
Ela é uma **conta**, não uma observação.

`resumo()["por_motivo"]` é a parte acionável: um shadow que roda a noite
inteira com zero aprovadas não é falta de oportunidade, é um portão fechado —
e ali está o nome dele.

---

## Bloco 4 — As três condições para ligar o LIVE

Definidas na seção 8 do prompt do projeto. Sem atalho.

| # | Condição | Estado |
|---|---|---|
| 4.1 | Recorder ≥ 72 h + backtest líquido positivo com latência realista | ⬜ |
| 4.2 | **SHADOW ≥ 2 semanas** com edge líquido *medido* | ⬜ |
| 4.3 | LIVE começa no stake mínimo; aumentar só após 100 trades com expectativa positiva | ⬜ |

**Piso de tempo:** mesmo que a captação seja consertada hoje e o veredito venha
positivo, são ~3 dias de gravação + M4 construído + 2 semanas de SHADOW antes
do primeiro dólar real.

---

## Segurança e infraestrutura (antes de qualquer credencial existir)

| # | Item | Estado |
|---|---|---|
| 5.1 | Carteira **dedicada**, só com o capital de operação em USDC na Polygon | ⬜ |
| 5.2 | Imagem Docker efetivamente construída | ⬜ nunca foi — `VEREDITO_M2.md` marca como não verificado |
| 5.3 | **CI que roda `pytest` e `ruff` a cada push** | ✅ **RODANDO** — `.github/workflows/ci.yml`, check `testes` verde nos PRs #41/#42/#43 |
| 5.4 | **NTP/chrony verificado na máquina que opera** | ✅ **2026-08-30** — `risk/sincronia.py` pergunta ao daemon (systemd, chrony, macOS), 14 testes. **Não determinado conta como não sincronizado**, e o LIVE recusa por isso. Falta só habilitar NTP na VPS |

Sobre 5.3: até o PR #40 o único check que aparecia era o SonarCloud, que vem
do GitHub App e faz análise estática — **não executa a suíte**. Os testes só
rodavam na máquina de quem estava editando, e um commit que quebrasse o
backtest chegava ao `main` com o quality gate verde. Para um projeto que vai
mexer com dinheiro real, "passou no meu ambiente" não é verificação.

**Agora roda.** `.github/workflows/ci.yml` executa os mesmos alvos do
`make check` (`ruff check src tests scripts` + `pytest`) a cada push, e o check
`testes` saiu verde nos PRs #41, #42 e #43. Ele instala o extra `analise` junto
com `dev` de propósito — sem pyarrow o teste de `replay/columnar.py` cai num
`importorskip` e some do relatório, e a CI passaria rodando menos testes que a
máquina do desenvolvedor.

O achado que segurou o workflow antes — `C Security Rating on New Code` no
SonarCloud, com `permissions: contents: read` não resolvendo — não voltou a
aparecer. Fica o registro da hipótese nunca testada: supply-chain,
`actions/checkout@v5` e `actions/setup-python@v6` não fixadas por commit SHA.

---

## Quando é OK avançar

- **Para gravar 72 h:** basta o Bloco 0 fechar — hoje só o 0.8 falta, e ele é
  contador de tempo, não trabalho.
- **Para escrever o M4:** o Bloco 1 precisa dar veredito **positivo** para
  taker ou maker. Se der negativo, **o projeto para — e isso é sucesso**:
  custou 72 h de VPS em vez de meses de capital. (O M4 foi escrito assim mesmo,
  e isso está certo: ele é a máquina de medir sem arriscar. O que ele não
  autoriza é ligar o LIVE.)
- **Para ligar o LIVE:** Blocos 0 a 4 inteiros, sem exceção.

Hoje nenhum dos três está liberado.

## As pendências que realmente travam, e o que destrava cada uma

Todo o resto do quadro é ou trabalho já feito, ou trabalho que só depende de
tempo de máquina. Estas não — a primeira linha fica na tabela riscada, porque
saber o que deixou de travar é parte do estado:

| Pendência | Natureza | O que destrava |
|---|---|---|
| ~~**1.3 calibração**~~ **RESOLVIDO em 30/08** (ECE 0,0126–0,0493 nos cinco baldes) | era defeito de **variância**, não do sinal: 39–48× na variância, 6,3× no desvio | feito — a `V(t)` passou a ser MEDIDA em dia anterior ao avaliado (§2d-ter). **E com o conserto a borda sumiu:** `bandas_com_edge: []`, o que move a reprovação para 1.1 e 1.4 |
| **1.1 / 1.4 ausência de borda** (as cinco bandas negativas, de −22,54 a −113,64; irrestrito −67,27, `hit_rate` 0,4172) | resultado de **medição**, não defeito | nada conhecido. Falta um teste direto de direção — acurácia ou markout sobre coorte pareada; a inclinação da latência não serve, porque muda preço de fill e coorte junto (§2d-ter) |
| **1.5 profundidade** (p50 128 USDC contra 200) | teto de **capacidade** do book | nada sob nosso controle — é liquidez do mercado. Só cabe contestar o limiar com a `curva_de_capacidade`, e 128 contra 200 não sugere que contestaria |
| **1.10 fórmula de reward** | **fato externo** | a doc de liquidity rewards da Polymarket (`docs.polymarket.com`), inalcançável deste ambiente. Ver API_NOTES §15.2 para a lista exata do que hoje é suposição: expoente do desconto por tick, fator 0,5, cadência de 1 s, unidade do `rewardsMaxSpread`, exigência de cotar dos dois lados, e o que é `market_competitiveness` |

E uma última, que é metodológica e vale para qualquer resultado acima: **um dia
não é veredito**. A rodada de 30/08 é o primeiro número desta página com
separação de dias — a curva de variância vem de 23/08 e a avaliação é de 24/08,
com recusa em código se as datas se cruzarem. Isso cobre o preditor; não cobre
o resto. Qualquer edge que apareça daqui em diante continua precisando de dias
independentes para separar borda de sobreajuste.
