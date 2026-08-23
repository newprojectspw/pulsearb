# ESTADO — o que falta para operar com dinheiro real

**Semáforo de hoje: 🔴 VERMELHO** — mas o bloqueio mudou de lugar. A captação
foi consertada e **o Bloco 0 fechou**. O que reprova agora é o M2: 26 trades
contra os 200 exigidos, e a profundidade abaixo do mínimo em toda duração
medida.

Atualizado: 2026-08-23 · fonte dos números: gravação real de 2026-08-22 23:00
UTC, hora cheia e limpa (`relatorios/hora_2300.json`)

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

### TAKER VIÁVEL — exige as 5

Medições da hora 23:00 — a primeira hora limpa (cobertura 99,9 %).

| # | Critério | Exigido | Medido (1 h limpa) | |
|---|---|---|---|---|
| 1.1 | PnL líquido a 300 ms, threshold ≥ 0,02 | positivo | +4,227 USDC | ⏳ |
| 1.2 | Número de trades | ≥ 200 | **26** | ❌ |
| 1.3 | Calibração: erro < 0,05 em ≥ 1 bucket | sim | **0,0098** em `<30s` | ✅ |
| 1.4 | Positivo também a 600 ms | sim | +4,1807 USDC | ⏳ |
| 1.5 | Profundidade p50 a 3 ticks | ≥ 200 USDC | **85,2 (5m) / 140,2 (15m)** | ❌ |

1.1 e 1.4 ficam ⏳ e não ✅ por dois motivos independentes: 26 trades não
sustentam sinal de PnL (o próprio 1.2 diz que abaixo de 200 é ruído), e a
curva de edge continua degenerada — ver a ressalva abaixo.

**1.5 é o problema estrutural.** É teto de CAPACIDADE, e edge nenhum resolve.
Piorou em relação à hora da tarde (121,97 → 85,2 na duração de 5 m), o que é
consistente com a nota do M2.7 sobre liquidez de madrugada. Nenhuma duração
medida até hoje passou dos 200 USDC.

**A 26 trades por hora, os 200 exigidos saem de ~8 horas limpas.** Isso é o
que o Bloco 0 ter fechado torna possível — e é só esperar, não consertar.

**Ressalva do M2.10 sobre o critério 1.1 — confirmada duas vezes.** Na hora
23:00, como na hora 16:00 antes dela, `threshold_mordeu: false` com **1
resultado distinto**: os seis thresholds da grade (0,01 a 0,12) deram os
**mesmos** trades e o **mesmo** PnL. O modelo previa ~0,83 contra um book
perto de 0,50,
então a entrada já nascia com edge acima do teto da grade. O limiar nunca
excluiu sinal nenhum, e o `melhor_threshold: 0.01` publicado era desempate de
`max()`, não escolha.

O relatório agora diz isso em `curva_de_edge.threshold_mordeu`. Enquanto ele
for `false`, **avaliar 1.1 é medir outra coisa** — o resultado não carrega
informação sobre threshold. Antes de concluir qualquer coisa sobre limiar de
entrada, subir a grade com `--thresholds`.

Vale reparar no que essa degenerescência também insinua: um modelo que
discorda do book em ~33 pontos de probabilidade, sistematicamente, contra
participantes que viram o mesmo dado no mesmo segundo. Isso é grande demais
para ser edge e merece explicação antes de virar veredito.

### SÓ MAKER VIÁVEL — exige as 5

| # | Critério | Exigido | 16:00 (suja) | 23:00 (limpa) | |
|---|---|---|---|---|---|
| 1.6 | Conta fechada com fator 0,3 | positiva | −0,2395 | **+0,2579** ¢/share | ⏳ |
| 1.7 | Markout 5 s | ≥ −0,5 ¢/share | −0,5895 | **+0,0921** | ⏳ |
| 1.8 | Horas de amostra na célula | ≥ 20 h | 1 h | **1 h** | ❌ |
| 1.9 | Taxa de divergência do livro | < 1 % | 3,27 % | **3,22 %** | ❌ |
| 1.10 | Fórmula de reward confirmada na doc | sim | não | **não** | ❌ |

1.6 e 1.7 **inverteram de sinal** entre as duas horas — de −0,24 para +0,26, e
de −0,59 para +0,09. Ficam ⏳ e não ✅ exatamente por isso: duas horas que
discordam do sinal não sustentam veredito nenhum. É o critério 1.8 (≥ 20 h)
existindo para o que ele existe.

**O achado que pode encerrar a rota, agora em DUAS gravações independentes:**
**0 janelas com pool de reward** — 0 de 24 na hora 16:00, 0 de 28 na hora
23:00, esta última com 104 janelas conhecidas. `rewards_daily_rate` ausente em
todas. Se persistir, estes mercados updown não participam do programa de
rewards, e não há o que ajustar: a rota maker fica sem fonte de receita.

1.10 não pode ser resolvido daqui: `docs.polymarket.com` é inalcançável neste
ambiente. Precisa de uma máquina que chegue lá.

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
