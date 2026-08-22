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
| 0.5 | **Descobrir se o recorder da VPS rodava com o M2.7** | ⬜ | `grep "tópico mudo com a conexão viva: reassinando"` no log |
| 0.6 | Aplicar 0.4 na VPS (exige parar a gravação) | ⬜ | RUNBOOK_VPS.md |
| 0.7 | **Uma hora com cobertura > 95 % em todos os ativos** | ⬜ | `gravacao.stream_de_ancora.cobertura_da_gravacao.pior_fracao_coberta` |
| 0.8 | 72 h de gravação contínua e limpa | ⬜ | mínimo definido no VEREDITO_M2 |

Última medição de 0.7: **0,4969** — metade da gravação sem preço-verdade.

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

## Segurança (antes de qualquer credencial existir)

| # | Item | Estado |
|---|---|---|
| 5.1 | Carteira **dedicada**, só com o capital de operação em USDC na Polygon | ⬜ |
| 5.2 | Imagem Docker efetivamente construída | ⬜ nunca foi — `VEREDITO_M2.md` marca como não verificado |

---

## Quando é OK avançar

- **Para gravar 72 h:** basta o Bloco 0 fechar (0.5 a 0.7).
- **Para escrever o M4:** o Bloco 1 precisa dar veredito **positivo** para
  taker ou maker. Se der negativo, **o projeto para — e isso é sucesso**:
  custou 72 h de VPS em vez de meses de capital.
- **Para ligar o LIVE:** Blocos 0 a 4 inteiros, sem exceção.

Hoje nenhum dos três está liberado.
