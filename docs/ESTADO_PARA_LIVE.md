# ESTADO — o que falta para operar com dinheiro real

**Semáforo de hoje: 🔴 VERMELHO.** Não é decisão de gosto: três dos cinco
critérios do maker estão medidos e reprovados, o taker não tem amostra, e o
marco de execução (M4) não existe em código.

Atualizado: 2026-08-22 · fonte dos números: gravação real de 2026-08-22 16:00
UTC (1h) + `relatorios/teste_m29.json`

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
| 0.6 | **Escalada: derrubar o socket após N reassinaturas sem efeito** | ✅ escrita e testada — ⏳ **aguarda deploy** | M2.11; exige parar a gravação |
| 0.7 | **Cobertura > 95 % em todos os ativos** | ❌ **28,5 % medidos** | contagem de `crypto_prices_twap_sixty` por hora |
| 0.8 | 72 h contínuas e limpas | ⬜ | mínimo definido no VEREDITO_M2 |

### A medição de 0.7 — o feed é INTERMITENTE, não morto

Cadência medida: ~1 tick/s por ativo, 8 ativos ⇒ **8 ticks por segundo de
gravação** é o esperado.

| Hora (UTC) | twap gravados | span | esperado | cobertura |
|---|---|---|---|---|
| 15:00 | 2.368 | 300 s (começou 15:55) | 2.400 | **98,7 %** |
| 16:00 | 13.500 | 3.600 s | 28.800 | 46,9 % |
| 17:00 | 2.344 | 3.600 s | 28.800 | **8,1 %** |
| 18:00 | 6.037 | 3.600 s | 28.800 | 21,0 % |
| 19:00 | 2.320 | 3.600 s | 28.800 | **8,1 %** |
| 20:00 | 9.185 | 1.004 s (até 20:16:44) | 8.032 | **114 %** |
| **Total** | **35.754** | 15.704 s | 125.632 | **28,5 %** |

Ele funciona em rajadas: os 5 primeiros minutos a 99 %, a hora das 20:00 a
114 %, e duas horas cheias a 8 %.

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

| # | Critério | Exigido | Medido (1 h real) | |
|---|---|---|---|---|
| 1.1 | PnL líquido a 300 ms, threshold ≥ 0,02 | positivo | +3,36 USDC | ⏳ |
| 1.2 | Número de trades | ≥ 200 | **11** | ❌ |
| 1.3 | Calibração: erro < 0,05 em ≥ 1 bucket | sim | −0,013 em `<30s` | ✅ |
| 1.4 | Positivo também a 600 ms | sim | +3,06 USDC | ⏳ |
| 1.5 | Profundidade p50 a 3 ticks | ≥ 200 USDC | **121,97 / 61,22** | ❌ |

1.1 e 1.4 ficam ⏳ e não ✅ porque 11 trades não sustentam sinal de PnL —
o próprio critério 1.2 diz que abaixo de 200 é ruído. **1.5 é teto de
capacidade: edge nenhum resolve.**

**Ressalva do M2.10 sobre o critério 1.1.** Na gravação real os seis
thresholds da grade (0,01 a 0,12) deram os **mesmos** 11 trades e o **mesmo**
PnL: o modelo previa ~0,83 de probabilidade contra um book perto de 0,50,
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

| # | Critério | Exigido | Medido (1 h real) | |
|---|---|---|---|---|
| 1.6 | Conta fechada com fator de desconto 0,3 | positiva | **−0,2395 ¢/share** | ❌ |
| 1.7 | Markout 5 s | ≥ −0,5 ¢/share | **−0,5895** (total) | ❌ |
| 1.8 | Horas de amostra na célula que sustenta | ≥ 20 h | **1 h** | ❌ |
| 1.9 | Taxa de divergência do livro | < 1 % | **3,27 %** | ❌ |
| 1.10 | Fórmula de reward confirmada na doc oficial | sim | nunca verificada | ❌ |

**Achado que pode encerrar a rota:** 0 de 24 janelas com pool de reward
(`rewards_daily_rate` ausente nas 24). Se confirmar em mais amostra, estes
mercados updown não participam do programa — e aí não há o que ajustar.

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
