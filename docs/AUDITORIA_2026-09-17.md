# Auditoria do código e das estratégias — 2026-09-17

Feita a pedido do operador ("auditoria completa no código, inconsistências
ou melhorias, e estratégias validadas para esses robôs"). Commit auditado:
`443d178` (main). Sessão na nuvem, **sem `data/`** e com `polymarket.com`,
`arxiv.org` e `ssrn.com` bloqueados pelo proxy — os papers entraram por
resumo de busca e pelos repositórios de replicação no GitHub, que abrem.

Cada achado diz **como foi verificado**: `provado` (executei e o resultado
está aqui), `medido` (saída de ferramenta), `lido` (arquivo e linha). O que
não deu para verificar desta sessão está marcado como tal, com o comando
que verifica no Mac ou na VPS.

## 0. Resumo

O que perde dinheiro está bem defendido, e isso foi medido, não presumido:
**zero** `except` largo com corpo vazio em 25.101 linhas (varredura por
AST); os módulos de risco e execução têm cobertura de 96–99 %; a chave
privada não aparece em `repr`, log ou erro (lido, `execution/ordem.py:321-378`);
a trava tripla lista todos os bloqueios de uma vez e recusa por construção
(`risk/autorizacao.py`, `execution/executor.py:243-250`); timeout é
`INCERTA` e nunca recusa (`execution/cliente.py:444-471`); o deploy roda como
usuário próprio com `NoNewPrivileges`, `ProtectSystem=strict` e
`ProtectHome` (`deploy/pulsearb-shadow-maker@.service:122-126`).

Os achados que importam são de outra natureza — **suposições que sustentam
números do quadro e nunca foram verificadas na fonte**, exatamente a classe
do §6.1b e do §12.13:

| # | achado | onde | verificação | o que decide |
|---|---|---|---|---|
| 2.1 | o **sentido** do campo `side` do `last_trade_price` ("lado do taker") está afirmado, não verificado | `live/livros.py:60`, `live/caixa_maker.py:334` | lido; API_NOTES só verifica `side` na struct da ORDEM (§ linha 1074) | o **sinal** de todo markout da rota maker |
| 2.2 | janela do TWAP fixa em **60 s** para 5m/15m/4h; a nota que justifica não tem data e contradiz a mudança pública de 2026-08-07 (5m → 30 s) | `engine/twap.py:74`, `API_NOTES.md:633` vs `:875-882` | lido | `P(Up)` de toda janela de 5m |
| 2.3 | reconciliação é obrigatória em três lugares e **chamada em nenhum** | `execucao_maker.py:396,422` (0 chamadas) | medido (grep + vulture) | pré-requisito de LIVE |
| 2.4 | `value: true` no RTDS vira preço **1,0** | `feeds/rtds.py:149` | **provado** | falha aberta no preço que decide a janela |
| 2.5 | dois `_percentil` com definições diferentes: p50 de 1..10 dá **5** num e **6** no outro | `analysis/measurements.py:37` vs `analysis/anchor_sweep.py:563` | **provado** | comparabilidade entre relatórios |
| 2.6 | `mypy` não roda no CI: **44 erros**, 9 no laço maker; um tipo de retorno mente | `engine/decisao.py:111`, `live/laco_maker.py` | medido | dívida que vira defeito sem aviso |
| 2.7 | **nenhum** dos 13 tetos de `risk.*` está no `config.yaml` — todos valem o default do código | `settings.py` vs `config.yaml` | medido | visibilidade do limite de dinheiro |
| 2.8 | fixtures de RTDS/CLOB-WS são sintéticas, o README manda substituir "na primeira rodada do recorder", e a gravação real existe desde 2026-09-12 | `tests/fixtures/README.md` | lido; nenhum teste lê gravação real | a lição do §6.1b, escrita e não cumprida |

E, na pesquisa (§3): a literatura acadêmica de 2026 **confirma** o que o
quadro mediu, com números independentes — cesta em liquidação rende
migalhas no mercado inteiro, campo grande é iliquidez e não mispricing,
quem ganha provê liquidez com ordem limitada — e aponta **onde** olhar
(campos densos com ≤ 20 resultados; canal de conversão NO→YES do adapter).

## 1. Método

| ferramenta | versão / modo | resultado bruto |
|---|---|---|
| `ruff check` (o que o CI roda) | 0.16.3, C901 ≤ 12 | limpo |
| `pytest` + `coverage` | 1.747 testes, 0 falhas, 4 skips | **90 %** (8.108 stmts, 784 não cobertos) |
| `mypy --ignore-missing-imports` | não roda no CI | **44 erros** em 12 arquivos |
| `vulture` | ≥ 80 % / ≥ 60 % | 0 / 83 (a maioria falso-positivo de FastAPI/pydantic; os reais estão em 2.3 e 2.9) |
| `radon cc` / `radon mi` | — | `backtest/__main__.py`: MI **0,00**, `main` CC **37** |
| `bandit -r` | — | 11 achados, 0 reais (§2.12) |
| varredura por AST | `except Exception/BaseException/bare` com corpo trivial | **0** |
| leitura dirigida | trava tripla, chave, cliente de ordens, portões, caixa, descoberta, deploy | §2 |

## 2. Achados no código

### 2.1 O `side` do `last_trade_price` — o sinal de todo markout se apoia numa frase

`live/livros.py:60`:

> `lado` é o do TAKER — `BUY` comprou dos asks, `SELL` vendeu nos bids

`live/caixa_maker.py:334` decide execução-sombra por `negocio.lado != "SELL"`.
Todo `custo_de_markout`, `execucoes_atravessadas`, `execucoes_no_nivel` e o
`liquido_pro_rata_usdc` do relato de 60 s (item 4.2) descendem disso. O
`markout_dos_pools.py` (item 1.12) usa a mesma leitura.

O que o `API_NOTES.md` verifica: que o campo **existe** no fio (§6.1a, linha
468: `market, asset_id, price, size, side, fee_rate_bps, transaction_hash,
timestamp`). O que ele **não** verifica: o que `side` significa nesse
evento. A única semântica de `side` marcada `[VERIFICADO]` é a da struct da
ordem (linha 1074: `0 = BUY, 1 = SELL`, `_encode_side`) — que é o lado da
**ordem**, não o do agressor do negócio.

Se `side` for o lado da ordem do MAKER, ou o lado da ordem que o servidor
escolheu registrar, a caixa está com o sinal invertido: o que ela conta
como "taker vendeu no nosso bid" pode ser "maker vendeu para um taker que
comprou". O paper de Dubach (§3.2) mede exatamente quão fácil é errar isto
nesta bolsa: direção inferida do feed público concorda com a verdade
on-chain em **~59 %** dos casos.

**Verificação (Mac, gravação M2_72H_20260912):** para cada
`last_trade_price` com `side=BUY`, conferir se no `price_change` do mesmo
instante o nível que diminuiu foi um **ask** (taker comprou) ou um **bid**.
Uma tarde de script; sai um número. Até lá, o sinal dos markouts do quadro
é hipótese.

### 2.2 Janela do TWAP: uma constante, uma nota sem data, e uma mudança pública que a contradiz

`engine/twap.py:74`: `TWAP_WINDOW_SECONDS_DEFAULT = 60.0`. Nada a
sobrescreve por duração — `anchor.py`, `variancia_de_transicao.py` e o
`ReferenciaTwap` usam o mesmo 60. O feed assina só `crypto_prices_twap_sixty`
(`feeds/rtds.py:165`).

`API_NOTES.md` diz as duas coisas:

- linha 633: **"5m usa janela de 30 s; 15m e 4h usam janela de 60 s"**;
- linhas 875-882: *"o anúncio público de agosto/2026 está DESATUALIZADO
  neste ponto: dizia 30s para 5m, mas o dado vivo mostra 60s para todas as
  durações"*.

A segunda nota é a que o código segue, e ela pode estar certa. O problema é
que **não tem data**, e a mudança pública é datada: Chainlink pôs os streams
TWAP de 30 s e 60 s em produção em 2026-07-31 e a Polymarket passou a
liquidar por TWAP em **2026-08-07**, com 30 s para 5m e 60 s para 15m/4h
(§3.5). Se a observação "dado vivo mostra 60 s" foi feita antes de 7 de
agosto, o engine modela a janela de 5m com a janela errada — e `P(Up)`,
`locked_mean_and_weight` e `final_twap` das janelas de 5m saem de uma média
que não é a que resolve o mercado.

**Verificação (Mac):** `anchor_sweep.evaluate_hypotheses` já recebe
`window_seconds`. Rodar sobre a M2_72H (posterior a 7/8) com 30 e com 60 e
ver qual concorda com `market_resolved` nas janelas de 5m. Uma execução,
dois números. E datar a nota do API_NOTES, seja qual for o resultado.

### 2.3 Reconciliação: declarada obrigatória, executada por ninguém

- `execution/cliente.py:15`: *"quem o recebe [INCERTA] não reenvia, reconcilia"*;
- `live/laco_maker.py:947`: log `"cotacao maker em estado desconhecido: reconciliar"`;
- quadro 3.5: *"`INCERTA` é terminal e obriga reconciliação"*.

`live/execucao_maker.py:396 reconciliar` e `:422 cancelar_orfas` existem,
estão testados (2 arquivos), e têm **zero** chamadas em `src/` e `scripts/`
(medido). O mesmo vale para `risk/gates.py:691 retomar` e `:702
desarmar_disjuntor`: o disjuntor arma e não há caminho de código que o
desarme. Em SHADOW nada disso importa; em LIVE, um processo que morre entre
o envio e a resposta deixa ordem no livro que ninguém vai limpar. É
pré-requisito do 3.5/4.x e não está no quadro como tal.

### 2.4 `true` vira preço 1,0 no RTDS — provado

```
>>> parse_rtds_event({"topic": "crypto_prices_chainlink",
...   "payload": {"symbol": "btc/usd", "value": True, "timestamp": ...}}, 1, 1)
('btc', 1.0)
>>> ... "value": False ...
('btc', 0.0)
```

`feeds/rtds.py:149 _as_float` é o único dos **nove** parsers de número do
repositório que não rejeita `bool` (`backtest/book.py:221` e
`markets/discovery.py:456` rejeitam; os seis `_numero` também). Um `true`
num campo numérico é defeito do servidor, e a regra da casa para dado
malformado é recusar, não converter. Dois testes e uma linha.

### 2.5 Dois `_percentil`, duas estatísticas — provado

```
p50 de [1..10]: measurements=5.0  anchor_sweep=6   DIFERENTE
p90 de [1..10]: measurements=9.0  anchor_sweep=10  DIFERENTE
```

`analysis/measurements.py:37` e `analysis/integrity.py:1251` usam
nearest-rank por teto (pct em 0–100); `analysis/anchor_sweep.py:563` usa
índice por piso (fração em 0–1). Os `p50_ppb`/`p90_ppb` do anchor_sweep
não chegam ao quadro hoje (0 ocorrências), então o dano é confinado — mas
"p50" significa duas coisas no mesmo repositório, e um dia alguém compara.

### 2.6 Nove parsers de número, uma função `_percentil` em três lugares

`_numero` ×6 (`pools_de_reward`, `poly_ws`, `recorder`, `columnar`,
`integrity`, `rewards`), `_as_float` ×3, `_percentil` ×3. O princípio
"mesmo caminho" existe para o motor, e vale para utilitários pelo mesmo
motivo: 2.4 e 2.5 são cópias que divergiram. Um `pulsearb/numeros.py` com
`numero()` e `percentil()` e nove imports.

### 2.7 Tipos: um retorno que mente, e `mypy` fora do CI

`engine/decisao.py:8-17` promete `-> TwapEstimate` e na linha 111 devolve
`HourlyEstimate` (`prob_up_hourly`). Hoje nenhum consumidor lê campo
só-TWAP (`peso_travado`, `twap_atual`, `ancora`) numa janela horária —
conferi os dois chamadores, `live/motor.py:357` e `backtest/runner.py:532`.
Amanhã alguém lê, e o erro é `AttributeError` numa janela de 1h em produção.
A anotação certa é `TwapEstimate | HourlyEstimate`.

Os outros 43: 9 em `live/laco_maker.py` (dois são falso-positivo — as
linhas 738/745 estão guardadas por `excluir = ... and anterior is not None`
que o mypy não enxerga; os de 710/908 são `aplicar_decisao` anotado com a
classe concreta `ClienteDeOrdens` em vez de um `Protocol`, e o dublê
`ClienteDeCotacao` passa por duck typing), 9 em `replay/columnar.py`, 6 em
`analysis/variancia_de_transicao.py`. `auth.py:416-418` é falso-positivo
(guardado pelo `faltando`). Pôr `mypy` no `make check` com baseline dos 44
custa uma hora e trava o número de piorar — mesma lógica do C901.

### 2.8 Os tetos de dinheiro não estão no arquivo que o operador lê

28 campos de `Settings` não aparecem no `config.yaml` e valem o default do
código. Entre eles, **todos** os de risco:

```
risk.exposicao_max_usdc      = 50.0
risk.perda_max_diaria_usdc   = 25.0
risk.stake_max_por_trade_usdc = 5.0
risk.stake_max_por_janela_usdc = 15.0
risk.posicoes_max_abertas    = 5
risk.perdas_seguidas_para_pausa = 4
risk.pausa_apos_sequencia_s  = 3600.0
risk.preco_minimo / preco_maximo = 0.05 / 0.95
risk.spread_maximo           = 0.04
risk.atraso_max_ms           = 250.0
risk.caminho_do_kill         = 'data/risco/KILL'
```

Os valores são sensatos. O problema é que quem abre o `config.yaml` para
saber quanto o bot pode perder por dia não encontra a resposta, e quem
muda o default no código muda o teto sem tocar o arquivo versionado que
deveria ser a decisão. Escrever os 13 no `config.yaml` com os mesmos
valores, e um teste em `test_quadro_nao_mente.py` que exija todo `risk.*`
presente ali.

### 2.9 Fixtures sintéticas com prazo de validade vencido

`tests/fixtures/README.md`: `rtds_*.json` e `clob_ws_book.json` são
*"sintético estrutural — substituir por capturas reais na primeira rodada
do recorder"*. A primeira rodada do recorder foi em agosto; a M2_72H
(2026-09-12) tem 287 milhões de registros. Nenhum teste lê gravação real
(medido: 0 referências a `data/recordings` fora de `tmp_path`). Os parsers
do RTDS e do CLOB-WS — `test_rtds_parse.py`, `test_feeds_ws.py` — continuam
provando que aceitamos o formato que **imaginamos**. É a frase do
`CLAUDE.md` ("teste que encoda a suposição não é teste") apontando para os
próprios testes do feed. Um recorte de 100 eventos reais de cada tipo,
commitado como fixture, fecha isto.

Código morto real (vulture ≥ 60 %, conferido a mão): além de 2.3,
`execution/ordem.py:370 assinar_ordem` (o construtor assina por
`assinar_typed_data` direto na linha 462 — duas formas de assinar, uma sem
uso), `execution/executor.py:253 carregar_diario`,
`analysis/rewards.py:299 capital_da_ordem`,
`analysis/arbitragem.py:211 oportunidade_de_escada` (escrita, testada, não
ligada — o quadro 1.13 já diz que a escada falta).

### 2.10 Docstrings da trava desatualizadas

`risk/autorizacao.py:29`: *"Cliente de ordens. Hoje não existe (itens 3.2
e 3.5)"*. `execution/executor.py:246`: *"`cliente_de_ordens_existe` é False
enquanto 3.2/3.5 não existirem"*. O 3.2 está ✅ desde 2026-08-30 e o 3.5
tem `execution/cliente.py` com 71 testes. O `False` fixo continua sendo a
decisão certa (a ordem assinada nunca recebeu resposta), mas o motivo
escrito é outro — e o próximo leitor vai "consertar" o False achando que a
razão sumiu.

### 2.11 Cobertura: onde os 10 % moram

| módulo | cobertura | leitura |
|---|---|---|
| `main.py` | 0 % (82) | entrypoint do **dashboard** M1, não do trading; ok |
| `replay/columnar.py` | 0 % (131) | extra de análise (pyarrow); ok |
| `replay/ao_vivo.py` | **36 %** | é o adaptador que faz o replay passar pelo `CicloAoVivo` — o mecanismo que torna "mesmo caminho" verificável. Merece mais |
| `recorder/__main__.py` | 57 % | o processo que produz toda a evidência do M2 |
| `engine/hourly.py` | 67 % | a rota horária |
| risco/execução/caixa | 96–99 % | onde tem de estar |

### 2.12 Complexidade e segurança estática

`backtest/__main__.py`: 2.291 linhas, MI 0,00, `main` com CC 37 (radon).
Passa no C901 ≤ 12 do ruff porque o mccabe do ruff e o do radon contam
diferente — não é contradição, é que a trava do ruff é contra piorar, e
este arquivo é o que produz todos os números do M2. Dívida conhecida,
não defeito.

`bandit`: 11 achados, nenhum real — SHA1 em `scripts/benchmark_latency.py:301`
(não é uso de segurança; `usedforsecurity=False` cala), `random` no jitter
de reconexão (`feeds/base.py:221`, correto), `subprocess` na sonda de NTP
(`risk/sincronia.py`, argumentos fixos), `assert` em scripts.

Segredos: nenhum padrão de chave/API key no repositório fora de um
`conditionId` público no API_NOTES. `.gitignore` cobre `.env`, `.env.*`,
`data/`, `relatorios/`. RUNBOOK exige `0600` no arquivo de credenciais.

### 2.13 Observação operacional de hoje

`/opt/pulsearb` na VPS está de dono `root` e os `git pull` correm como root
— o RUNBOOK §64 previa `chown pulsearb:pulsearb`. Hoje isso custou um
`Permission denied` e pode custar um serviço que não consegue ler arquivo
que o pull acabou de criar. `sudo chown -R pulsearb:pulsearb /opt/pulsearb`
depois que a coleta em curso terminar.

## 3. Estratégias — o que a literatura de 2026 mede, e o que só afirma

O `OUTROS_BOTS.md` (2026-09-14) leu código de bots públicos e concluiu que
não há bot público lucrativo. Esta seção olha para quem mediu o **mercado**,
não o código. Quatro papers e um estudo de replicação, todos de 2026.

### 3.1 Quem ganha — Akey, Grégoire, Harvie & Martineau

*Who Wins and Who Loses in Prediction Markets? Evidence from Polymarket*
(SSRN 6443103 / CEPR DP 21615). Todos os negócios de nov/2022 a
2026-03-29, **US$ 67 bi** de volume. O **1 % de cima** dos usuários com PnL
positivo captura **76,5 %** dos ganhos (cobertura posterior fala em 84 %).
1.704.601 usuários perderam, **US$ 650 mi** no total. O achado
estrutural: **quem ganha provê liquidez com ordem limitada; quem perde
toma liquidez com ordem a mercado**.

*Leitura para nós:* é a rota maker (1.12/4.2), não a taker (1.1–1.5). O
quadro chegou à mesma conclusão por medida própria; aqui está a
confirmação com US$ 67 bi.

### 3.2 Microestrutura — Dubach

*The Anatomy of a Decentralized Prediction Market* (arXiv 2604.24366,
replicação em `github.com/philippdubach/polymarket-microstructure`).
30 bilhões de eventos do feed público em 52 dias, painel pré-registrado de
600 mercados, cruzado com o registro on-chain. Oito fatos estilizados:
prêmio de spread em longshots; **perfil de profundidade quase uniforme**
(não concentrado no topo); diversidade de makers com cauda concentrada;
spread efetivo varia por categoria; ingestão do feed com mediana < 50 ms e
cauda de segundos; wash trading mediana 1 %.

O resultado que nos toca: **direção de negócio inferida do feed público
concorda com a verdade on-chain em só ~59 %** dos casos (Lee-Ready em ações
dá ~80 %). Meia-spread efetiva troca de sinal em 67 % das comparações. É o
aviso do achado 2.1: nesta bolsa, quem não verificou o lado do agressor
provavelmente está errado em 2 de cada 5.

### 3.3 Arbitragem executável — Gebele, Mutzel & Matthes (TU München)

*Executable Arbitrage and Market Efficiency in Prediction Markets* (arXiv
2608.00666, 2026-08-01). Separa não-arbitragem **no espaço de payoff** (a
identidade que o 1.13 usa) de não-arbitragem **executável pelo protocolo**.
O NegRisk Adapter só opera a direção **NO → YES** antes da liquidação.
Reconstruindo valor executável com profundidade e cruzando com histórico
por ator e traces de conversão on-chain, medem **US$ 1,12 mi** de lucro de
arbitragem no total: **US$ 1,086 mi via conversão** e **US$ 32 mil via
formação de cesta na liquidação**.

*Leitura para nós:* o 1.13 modela a cesta na liquidação — o canal de
US$ 32 mil no mercado inteiro. O dinheiro está no canal do conversor, que
exige interagir com o adapter on-chain (escopo novo, fora do CLOB).

### 3.4 Coerência multi-resultado — replicação independente

`github.com/ArtBreguez/polymarket-coherence`: 36 eventos negRisk (1.757
mercados-filho), painel de 10 dias (2026-08-20 a 30, 964 snapshots, lote de
100 shares), **andando o livro L2 inteiro**. Mid-prices somam **0,998**
(coerente); as "violações" aparentes moram na banda bid-ask. **56 % dos
campos pequenos (≤ 20 resultados) são completos e traváveis; 0 % dos
grandes (> 20)**. Custo executável mediano de travar o campo: **1,022**;
uma única janela abaixo de 1,00 em 10 dias (0,973, +2,7 % bruto).
Conclusão do autor: campo grande é **iliquidez** (resultados sem preço),
não mispricing.

*Leitura para nós:* é o achado de hoje (PR #136 — 77 e 76 vagas
reservadas em 128) medido por outra pessoa, com outro método, dez dias
antes. E diz onde olhar: **campos com ≤ 20 resultados**. A descoberta do
`varredura_de_arbitragem.py` deveria filtrar por isso antes de contar.

### 3.5 Taxas e liquidação — o que mudou em 2026 e se o repositório sabe

- **Taxa dinâmica** `fee = C × rate × p × (1 − p)`, por categoria; cripto
  `rate = 0,07` (pico US$ 1,75 por 100 shares a 50 ¢), rebate ao maker
  **20 %** em cripto (15 % esportes, 25 % o resto). Estrutura V2 desde
  2026-03-30, exchange-wide desde 2026-07-01. **Bate com o API_NOTES §12.6**
  (`crypto_fees_v2`, 0,07, 20 %). Consistente.
- **Liquidação por TWAP** desde **2026-08-07**: 30 s para 5m, 60 s para
  15m/4h, via Chainlink Data Streams (em produção desde 2026-07-31). É o
  achado 2.2.
- A imprensa registra que a taxa de ~3,15 % a 50 ¢ foi introduzida
  explicitamente para matar a arbitragem de latência nos mercados curtos de
  cripto — o que o quadro mediu em 1.1/1.4/1.11 por conta própria.

### 3.6 Entre bolsas — semantic non-fungibility

arXiv 2601.01706: ~6 % dos eventos estão listados em mais de uma
plataforma, com desvios executáveis persistentes de **2–4 %**. O canal é
real e capital-intensivo (posições travadas até resolver). Kalshi é
regulada nos EUA e não é acessível daqui; PredictIt idem. Registrado, não
acionável.

### 3.7 O que a imprensa afirma e não prova

"14 das 20 carteiras mais lucrativas são bots", "US$ 40 mi extraídos por
arbitragem num ano", "bot com 52,8 % de acerto e US$ 200 mil sobre
US$ 26 mi de volume", "`0x8dxd`" — nada disso tem dado verificável ou
método publicado. Estrutura observada, como o `OUTROS_BOTS.md` já marcava.
Não entra em decisão.

### 3.8 O que isto diz para o PULSEARB

1. **1.12 (maker nos pools, horizonte de dias)** é a única rota com conta
   positiva medida (+148 USDC/h em 95 mercados) e é a que a literatura diz
   que ganha (§3.1). O veredito com custo de saída está sendo colhido
   **agora** (4 h na VPS). Antes de qualquer LIVE nela: o achado 2.1, porque
   o sinal do markout é o sinal do custo.
2. **1.13 (cesta)**: restringir a campos ≤ 20 resultados (§3.4) — o custo é
   um filtro na varredura. O canal de conversão (§3.3) é onde há dinheiro
   medido, e é escopo novo (adapter on-chain), a decidir.
3. **Taker de 5m/15m**: quadro ❌ por medida própria, e a literatura (§3.1,
   §3.5) diz o mesmo. Fica ❌.

## 4. Ordem de conserto proposta

| # | o quê | onde roda | custo | fecha |
|---|---|---|---|---|
| 1 | verificar o `side` do `last_trade_price` contra o `price_change` do mesmo instante na M2_72H | Mac | 1 script, 1 tarde | 2.1 — e valida ou inverte todo markout do quadro. **Rodou no Mac em 2026-09-17, por ordem de chegada: 1.708.080 classificáveis, 0,8274 concordam com taker → INDETERMINADO** (limiar 0,90; MAKER seria ≤ 0,10 — não está invertido). Discordantes com padrão de atraso: o `price_change` chega ~800 ms depois do carimbo, o print ~45 ms (fixture real). **✅ v2 em 2026-09-18: TAKER.** Estrito 0,8368, janela de toque de 1 s **0,9932**; o estrito lê o livro pós-negócio que o servidor emite 1 ms antes do print (API_NOTES §6.1c). O sinal de todo o markout do quadro está confirmado |
| 2 | `anchor_sweep` com `window_seconds=30` vs `60` nas janelas de 5m da M2_72H; datar a nota do API_NOTES | Mac | 1 execução | 2.2 — **Script pronto (2026-09-17): `scripts/janela_do_twap.py ~/pulsearb-gravacao --desde 2026-09-09T02:19Z --ate 2026-09-12T02:19Z --json relatorios/JANELA_TWAP_M2_72H.json`** **Rodou em 2026-09-18 e MEDIU A COISA ERRADA:** comparava a MÉDIA das amostras do stream em 30 vs 60 s, a definição de final que o M2.6 já mostrou perder (0,9648 vs 1,0); deu 55 vs 102 "erros" nas 5m — ruído da definição, não evidência. **v2 (#PR) usa o mesmo caminho da varredura τ do backtest (valor do stream no fecho, e18, carimbo do servidor), por duração** — falta correr: `nohup .venv/bin/python scripts/janela_do_twap.py ~/pulsearb-gravacao --desde 2026-09-09T02:19Z --ate 2026-09-12T02:19Z --json relatorios/JANELA_TWAP_M2_72H_v2.json > relatorios/twap2.log 2>&1 &`. A prova FINAL de "5m usa 30 s" exige gravar `crypto_prices_twap_thirty`: **opção `feeds.rtds_assinar_twap_thirty` pronta (desligada por defeito)** — ligar numa gravação de 72 h dedicada e correr a varredura τ com o stream de 30 s |
| 3 | ligar `reconciliar` + `cancelar_orfas` no arranque do SHADOW (a leitura é GET; a ação fica atrás de flag) e pôr no quadro como sub-item do 3.5 | nuvem | 1 PR | 2.3 — **✅ feito 2026-09-17** (`LacoMaker.reconciliar_no_arranque`, chamado por `ProcessoShadow.run`; 8 testes, fiação guardada) |
| 4 | `_as_float` rejeita `bool` + 2 testes | nuvem | 1 PR pequeno | 2.4 — **✅ feito 2026-09-17** (5 testes, mutação verificada) |
| 5 | `pulsearb/numeros.py` com `numero()` e `percentil()`; nove imports | nuvem | 1 PR | 2.5, 2.6 — **✅ feito 2026-09-17** (eram onze parsers e quatro percentis, não nove e três; 12 testes, mutações verificadas) |
| 6 | `mypy` no `make check` com baseline; corrigir `decisao.py:17` | nuvem | 1 PR | 2.7 — **✅ feito 2026-09-17** (baseline `ignore_errors` em 12 módulos, lista só encolhe; `decisao.py:17` fica na lista, a corrigir ao tirá-lo) |
| 7 | 13 tetos `risk.*` explícitos no `config.yaml` + teste de presença | nuvem | 1 PR | 2.8 — **✅ feito 2026-09-17** (2 testes; mutação: apagar uma linha do yaml derruba) |
| 8 | recorte de eventos reais como fixture dos parsers | Mac → commit | 1 PR | 2.9 — **Extractor e consumidor prontos (2026-09-17):** `mkdir -p tests/fixtures/reais && .venv/bin/python scripts/recortar_fixtures.py ~/pulsearb-gravacao --desde 2026-09-09T02:19Z --ate 2026-09-09T03:19Z` e commitar `tests/fixtures/reais/`; `tests/test_fixtures_reais.py` passa a correr (hoje salta). **Rodou no Mac em 2026-09-17: 7 de 8 parsers lêem tudo o que o servidor mandou** (price_change, book nível a nível, last_trade_price, market_resolved, twap_sixty, crypto_prices). **O oitavo teste ACHOU:** PONG gravado (344/h), frames vazios do RTDS (2/h) e **4 recusas de assinatura do RTDS que ninguém lia** — API_NOTES §6.2b, sub-item do 0.6 (#151). **✅ 2026-09-17: segunda rodada 11 de 11, pasta commitada (`ce3cc37` + os `.jsonl` forçados por cima do `.gitignore`)** |
| 9 | docstrings de `autorizacao.py:29` e `executor.py:246` | nuvem | junto com 4 | 2.10 — **✅ feito 2026-09-17** |
| 10 | filtro "≤ 20 resultados" na `varredura_de_arbitragem.py` | nuvem | 1 PR | §3.4 — **✅ feito 2026-09-17** (`MAX_RESULTADOS_DA_CESTA`, motivo `campo_grande_demais`, 2 testes) |

Os itens 1 e 2 vêm antes de tudo porque são os únicos que podem **mudar
um número que já está no quadro**. Os de nuvem (3–7, 9, 10) não dependem
de dado e podem sair hoje.

## 5. Fontes

- Akey, P., Grégoire, V., Harvie, N., Martineau, C. — *Who Wins and Who Loses in Prediction Markets? Evidence from Polymarket* — <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6443103> · CEPR DP 21615 <https://ideas.repec.org/p/cpr/ceprdp/21615.html>
- Dubach, P. D. — *The Anatomy of a Decentralized Prediction Market: Microstructure Evidence from the Polymarket Order Book* — <https://arxiv.org/abs/2604.24366> · replicação <https://github.com/philippdubach/polymarket-microstructure>
- Gebele, J., Mutzel, T., Matthes, F. — *Executable Arbitrage and Market Efficiency in Prediction Markets* — <https://arxiv.org/abs/2608.00666>
- Breguez, A. — *polymarket-coherence* (estudo reproduzível) — <https://github.com/ArtBreguez/polymarket-coherence>
- *Semantic Non-Fungibility and Violations of the Law of One Price in Prediction Markets* — <https://arxiv.org/abs/2601.01706>
- *Fill-Side Non-Retail Trading on Polymarket* — <https://arxiv.org/abs/2605.11640> (não lido; bloqueado)
- Taxas V2 e rebates: <https://docs.polymarket.us/fees> · <https://marketmath.io/blog/polymarket-fees-explained>
- TWAP 2026-08-07: <https://predictionnews.com/story/polymarket-upgrades-crypto-markets-with-twap-pricing-model> · <https://genfinity.io/2026/08/12/chainlink-twap-data-streams-polymarket-crypto-markets/> · <https://leolabs.me/blog/polymarket-twap-counterparty/en/>
- Taxa dinâmica contra latência: <https://www.financemagnates.com/cryptocurrency/polymarket-introduces-dynamic-fees-to-curb-latency-arbitrage-in-short-term-crypto-markets/>
- Imprensa (não verificável, §3.7): <https://www.quicknode.com/builders-guide/best/top-10-polymarket-trading-bots> · <https://www.financemagnates.com/trending/prediction-markets-are-turning-into-a-bot-playground/>
