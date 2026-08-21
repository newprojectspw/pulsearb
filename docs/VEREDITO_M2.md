# VEREDITO M2 — existe edge líquido?

**Status: PENDENTE DE DADO. Nenhum veredito pode ser emitido ainda.**

Data: 2026-08-16 · atualizado 2026-08-21 (M2.4)

---

## O veredito honesto de hoje

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

### 2b. A âncora não é nenhuma das hipóteses nomeadas — e o M2.4 a caça por varredura

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
4. `integridade.divergencia_topo_book.taxa` **abaixo de 1%** na gravação. A
   conta do maker é uma soma sobre o livro reconstruído: se o livro é
   duvidoso, o resultado é decorativo.
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
