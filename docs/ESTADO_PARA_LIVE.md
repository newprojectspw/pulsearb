# ESTADO — o que falta para operar com dinheiro real

**Semáforo de hoje: 🟡 AMARELO** — o M2 chegou ao veredito. O TAKER passa em
**4 dos 5** critérios e reprova só no 1.5, que é **teto de capacidade** e não
de borda. O MAKER reprova, e por um motivo que nenhum ajuste resolve: 594 das
599 janelas não têm pool de reward.

Atualizado: 2026-08-23 · fonte dos números: **20 horas** de gravação real de
2026-08-23 (00:00 a 20:00 UTC, hora 01:00 excluída), 568 trades
(`relatorios/M2_VEREDITO.json`)

> Como ler: **✅** passou com dado real · **❌** medido e reprovado ·
> **⏳** sem amostra suficiente · **⬜** não existe / não começou.
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

Amostra: 20 horas contínuas de 2026-08-23, `pior_fracao_coberta` 1,0 nos oito
ativos, 794 janelas conhecidas, 568 trades. A rodada levou 2 h 47 min.

### TAKER — passa em 4 dos 5

| # | Critério | Exigido | Medido (20 h) | |
|---|---|---|---|---|
| 1.1 | PnL líquido a 300 ms, threshold ≥ 0,02 | positivo | **+102,9227 USDC** | ✅ |
| 1.2 | Número de trades | ≥ 200 | **568** | ✅ |
| 1.3 | Calibração: erro < 0,05 em ≥ 1 bucket | sim | 0,0067 em `<30s` | ⚠️ **não avaliado** |
| 1.4 | Positivo também a 600 ms | sim | **+101,1759 USDC** | ✅ |
| 1.5 | Profundidade p50 a 3 ticks | ≥ 200 USDC | **87,8 (5m) · 41,8 (15m) · 31,3 (1h) · 35,7 (4h)** | ❌ |

**1.1 inverteu de sinal, e o sinal novo se sustenta.** Na amostra de 5 h era
−41,57; com 20 h deu +102,92. A diferença é tamanho de amostra, não
arredondamento, e três cortes independentes concordam:

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
não é o mesmo que reprovado. **Falta rodar de novo sobre a gravação para saber
em que pé o modelo está.**

**1.5 é o único critério de borda que reprova, e é o mais duro.** É teto de
CAPACIDADE. O backtest move 1.651,59 USDC em 568 trades — **2,91 USDC por
trade**, e o lucro é de **+0,18 USDC por trade**. A duração mais líquida (5 m)
tem p50 de 87,77 USDC a 3 ticks, **44 % do mínimo de 200** que o critério
fixou antes de existir dado. Nenhuma duração passa.

### MAKER — reprova

| # | Critério | Exigido | Medido (20 h) | |
|---|---|---|---|---|
| 1.6 | Conta fechada com fator 0,3 | positiva | +0,043 ¢/share (fator 0,5) | ⚠️ margem de 4 centésimos |
| 1.7 | Markout 5 s | ≥ −0,5 ¢/share | **−0,307** | ✅ |
| 1.8 | Horas de amostra na célula | ≥ 20 h | **40,7 h** | ✅ |
| 1.9 | Taxa de divergência do livro | < 1 % | **2,89 %** | ❌ |
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

---

## Bloco 2 — M3: modelo e calibração

| # | Item | Estado |
|---|---|---|
| 2.1 | Modelo TWAP endgame | ✅ pronto |
| 2.2 | Modelo horário | ✅ pronto |
| 2.3 | Curva de calibração gerada a partir de gravação real | ⏳ existe por bucket; falta amostra |

---

## Bloco 3 — M4: execução e travas (**nada existe**)

`main.py:153` — *"Trava do M1: só SIM existe. SHADOW/LIVE chegam no M4."*

| # | Item | Estado |
|---|---|---|
| 3.1 | `risk/gates.py` | ⬜ diretório não existe |
| 3.2 | Cliente de ordens, assinatura EIP-712, auth do CLOB | ⬜ |
| 3.3 | Modo SHADOW | ⬜ enum sem implementação |
| 3.4 | Modo LIVE + trava tripla (`MODE=LIVE` + `CONFIRM_LIVE` + `EU ACEITO O RISCO`) | ⬜ |
| 3.5 | Ordens FOK, conexão quente, nonce/idempotência, rejeição e timeout | ⬜ |
| 3.6 | Trava: stake máximo por trade e por janela (US$ 5) | ⬜ |
| 3.7 | Trava: perda diária máxima → kill (US$ 20) | ⬜ |
| 3.8 | Trava: 4 perdas consecutivas → pausa de 1 h | ⬜ |
| 3.9 | Trava: exposição simultânea máxima (2 janelas) | ⬜ |
| 3.10 | Trava: feed velho / relógio > 250 ms / spread anômalo → não opera | ⬜ |
| 3.11 | Kill switch: arquivo `KILL` + botão no dashboard | ⬜ |
| 3.12 | Suíte de testes das travas (uma por trava) | ⬜ |
| 3.13 | SHADOW rodando 24 h sem crash | ⬜ |

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
| 5.3 | **CI que roda `pytest` e `ruff` a cada push** | ⬜ escrito e **provado**, bloqueado no quality gate |

Sobre 5.3: o único check que aparece nos PRs é o SonarCloud, que vem do
GitHub App e faz análise estática — **não executa a suíte**. Os testes só
rodam na máquina de quem está editando, e um commit que quebre o backtest
chega ao `main` com o quality gate verde. Para um projeto que vai mexer com
dinheiro real, "passou no meu ambiente" não é verificação.

O workflow existe e **funcionou**: rodou no PR #19 e passou, com `ruff` limpo
e 395 testes verdes no servidor. Ele roda os mesmos alvos do `make check`
(`ruff check src tests scripts` + `pytest`) e instala o extra `analise` junto
com `dev` de propósito — sem pyarrow o teste de `replay/columnar.py` cai num
`importorskip` e some do relatório, e a CI passaria rodando menos testes que
a máquina do desenvolvedor.

**Por que ainda está ⬜:** o SonarCloud reprovou o PR com
`C Security Rating on New Code`, e o único arquivo novo analisável por regra
de segurança era esse workflow. Adicionar `permissions: contents: read`
(menor privilégio para o `GITHUB_TOKEN`) **não** resolveu, e o ambiente de
desenvolvimento não alcança `sonarcloud.io` para ler qual regra é. O workflow
saiu deste PR para não segurar o trabalho do analisador, e volta num PR
próprio quando o achado estiver em mãos. Hipótese seguinte, não testada:
supply-chain — `actions/checkout@v5` e `actions/setup-python@v6` não fixadas
por commit SHA.

---

## Quando é OK avançar

- **Para gravar 72 h:** basta o Bloco 0 fechar (0.5 a 0.7).
- **Para escrever o M4:** o Bloco 1 precisa dar veredito **positivo** para
  taker ou maker. Se der negativo, **o projeto para — e isso é sucesso**:
  custou 72 h de VPS em vez de meses de capital.
- **Para ligar o LIVE:** Blocos 0 a 4 inteiros, sem exceção.

Hoje nenhum dos três está liberado.
