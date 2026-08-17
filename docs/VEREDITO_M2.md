# VEREDITO M2 — existe edge líquido?

**Status: PENDENTE DE DADO. Nenhum veredito pode ser emitido ainda.**

Data: 2026-08-16

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

Duas coisas foram medidas ao vivo e mudam o quadro antes de qualquer backtest.

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

### 3. A capacidade pode importar mais que o edge

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
| Mudança de tick | `medicoes.tick` | _pendente_ |
| Atraso de liquidação por jogo | `medicoes.atraso_liquidacao` | _pendente_ |
| Profundidade do book | `medicoes.profundidade` | _pendente_ |

### Regras de decisão, definidas ANTES de ver os números

Definir os critérios agora é o que impede de racionalizar o resultado depois.

**TAKER VIÁVEL** exige TODAS:

1. PnL líquido positivo com latência de **300ms** e threshold ≥ 0,02
2. Pelo menos **200 trades** na amostra (menos que isso é ruído)
3. Calibração com erro < 0,05 em pelo menos um bucket de tempo restante
4. Positivo também em **600ms** (se só sobrevive a 150ms, é miragem)
5. Profundidade a 3 ticks ≥ **US$ 200** no p50 (senão não escala)

**SÓ MAKER VIÁVEL**: taker negativo, mas a medição M2.E.4 mostra rebate +
rewards superando o risco de seleção adversa. Isso viraria um projeto
diferente — market making é v2, e o M2 só mede o potencial.

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

**Bloqueio único: gravação de produção.** Próximo passo em
`docs/RUNBOOK_VPS.md`.

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
