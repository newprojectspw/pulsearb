# O que os outros bots fazem — e o que deu certo de verdade

Estudo feito em 2026-09-14 a pedido do operador ("analise o que os outros
bots estão fazendo e dando certo"). Código clonado e lido; o que está aqui
cita arquivo e linha do clone, na revisão indicada. Nada foi executado —
ver a seção sobre o repositório com executável fechado.

| repositório | revisão lida | o que é |
|---|---|---|
| `warproxxx/poly-maker` | `4f32103`, 2026-07-09 | maker de rewards, pure-core + reconciler, com diário de sessão real (`TIPS.md`) |
| `terrytrl100/polymarket-automated-mm` | `849cda4`, 2025-12-07 | maker de rewards com planilha Google como painel; o mais copiado no GitHub |
| `RuneDn/polymarket-liquidity-bot` | `0cfcd36`, 2025-03-05 | um arquivo, bid-only, feito para *não* executar |
| `polymarket-liquidity-rewards-bot` | — | **sem código de estratégia**: executável fechado + chave privada em `.env`. Não rodar. |

## 1. O que se repete em quem ganha

Não há bot público lucrativo. Os três com código são farmers de reward, e
o único que publica resultado real (`poly-maker/TIPS.md:74-77`) fechou a
sessão em **−$15,51**, com a maior perda vinda de UM fill adverso num livro
fino. O que ganha dinheiro está nos leaderboards, não no GitHub, e o que se
vê deles é estrutura, não código:

- **`0x8dxd` e afins nos binários de cripto de 5/15 min**: ~94 % das
  ordens são compras simétricas de YES e NO, ~40 mil negócios/dia, lotes de
  $3–5, viés de inventário, **nunca prevê a direção**. Quem compra os dois
  lados abaixo de 1,00 e deixa o par liquidar ganha `1 − (pUp + pDown)` mais
  o rebate — o modelo é o de "maker de pares".
- **Makers de Economics**: centenas de compras e zero vendas; lucro de seis
  dígitos vindo de rebate + prêmio de liquidez, não de acerto.
- Menos de 8 % das carteiras são lucrativas; >70 % do lucro de arbitragem em
  cripto vai para bots com latência <100 ms.

A pergunta que este estudo devolve ao projeto: **a nossa cotação sintética
dos dois lados (4.2) já tem essa forma. Nunca medimos a economia do PAR** —
medimos cada perna pelo markout de 5 s, que é negativo por construção para
um maker. `scripts/maker_de_pares.py` foi escrito para fechar essa conta
sobre a gravação real; o resultado está na linha 4.2/1.6 do quadro.

## 2. `poly-maker` — o mais completo

Núcleo puro e determinístico (`src/polymaker/strategy/quoting.py`,
`regime.py`), reconciler separado (`execution/reconciler.py`). É o único
com desenho que se parece com o nosso (motor sem I/O, mesmo caminho para
replay e ao vivo — eles próprios recomendam gravar o WS e replayar,
`TIPS.md:92-105`).

**Cotação** (`quoting.py:7-8, 37-39, 82-83`):

- valor justo = microprice ponderado por profundidade (3 níveis) + `0,5 ·
  flow_z · tick`;
- preço de reserva `r = fv − γ · σ_curta · u`, `u = shares líquidas / q_max`
  (viés de inventário: quanto mais comprado, mais baixo o bid);
- meio-spread `δ = delta_min_ticks · tick + c_vol · σ + c_tox · toxicidade`,
  **cortado no `rewards_max_spread`** no regime QUIET (fora da banda não
  pontua);
- bid YES em `r − δ`, bid NO em `(1 − r) − δ`; **junta ao melhor bid, nunca
  melhora o topo**; camadas; tamanho ≥ `rewards_min_size ×
  reward_size_mult` (`quoting.py:77`), escalado por tendência, toxicidade,
  folga de risco e `(1 − u)`;
- saída só passiva (SELL descansando); **funde YES+NO via CTF
  `mergePositions`** quando `min(YES, NO) ≥ 20` (`merge.py:32-47, 130`).

**Regime** (`regime.py:51-57, 73-77`): salto do fv ≥ `event_jump_ticks` ou
varredura → EVENT: **cancela todas as cotações**, cooloff 30–90 s. TRENDING
(σ curta/σ longa) corta o tamanho pela metade. HALT só por conexão do WS,
não por idade do livro (`TIPS.md:40-43` — a versão anterior se auto-parava
em mercado quieto).

**Reconciler anti-churn** (`reconciler.py:34-54`): só reprecifica se o
alvo andou mais que `reprice_ticks`, só redimensiona se mudou mais que
`resize_frac`. Fills pelo WS de usuário (otimista no MATCHED), reconciliação
REST a cada 20 s, post-only GTC, heartbeat, token bucket.

**O que custou dinheiro, na ordem deles** (`TIPS.md:25-56`):

1. seleção adversa em livro fino/com buraco — um fill de 159 shares numa
   varredura 0,478→0,442 (**→ descansar só o `rewardsMinSize` em mercado
   fino**);
2. ordens que não pontuam: abaixo do `rewardsMinSize` (que **muda ao vivo**,
   50→100) ou fora da banda — refrescar o Gamma periodicamente;
3. HALT/TRENDING falsos em mercado quieto;
4. churn: reprecificar a cada poucos segundos perde a fila e sai da amostra
   do reward — "descansar > reagir";
5. em tick de 0,001 o spread por share é fração de centavo: o lucro é
   **rewards + rebates**, não captura de spread.

Parâmetros "ajustados por intuição com dinheiro real"; sem backtester.

## 3. `polymarket-automated-mm` — o mais copiado, e por que perde

`trading.py`: distância-alvo `0,15 × max_spread` do meio, tenta ser topo
(`best_bid + tick`), **continua comprando até 2 × max_size**, sem viés de
inventário nem hedge; stop-loss em −2 % vende só `trade_size` e dorme 1 h
(`trading.py:500`, `update_hyperparameters.py:24-51`); cancela tudo se o
preço andou ≥ 0,15 (`trading.py:586-589`); funde quando `min(pos) > 20`;
pontuação de mercado `reward_por_100 / (volatilidade + 1)` com volatilidade
vinda de planilha horária. Corridas no rastreio de fills (`set_position` no
MATCHED e `update_positions` no CONFIRMED). O autor de um fork (Substack)
relata o mesmo que o `poly-maker`: sem lucro líquido, movimentos adversos de
30–40 % apagam o ganho, bugs de inventário.

## 4. `RuneDn/polymarket-liquidity-bot` — cotar para NÃO executar

`app.py`: bid-only no token abaixo de 50 ¢; **nunca cria nível**; junta ao
5.º–9.º nível dentro da banda, só se houver ≥ 1,5 × o próprio tamanho na
frente (`app.py:179`); `max_spread −= 0,005` de folga (`app.py:37`); ajuste
de paridade na borda da banda (`app.py:65`); reprecifica só quando um preço
dentro da banda muda. Ignora que cotação de um lado só pontua ⅓ (a fórmula
do programa favorece dois lados).

É a técnica de "reward sem fill" na forma mais pura, e é a que menos
depende de latência. Cabe como filtro do 4.2: só descansar onde há colchão
à frente.

## 5. O repositório com executável fechado

`polymarket-liquidity-rewards-bot`: stubs Python, binário fechado, pede a
chave privada em `.env`. Sem código de estratégia para ler. **Padrão de
drenagem de carteira; nunca rodar**, nem em carteira vazia.

## 6. O que cabe portar — agora com número (medido em 2026-09-13)

`scripts/maker_de_pares.py` rodou sobre o dia inteiro (1.000 janelas
Up/Down, lote de 20 shares). O que a medida diz, em ordem de tamanho do
efeito:

1. **Recolher a cotação quando o livro anda contra é quase tudo.** *(Portado
   em 2026-09-14 — quadro 4.0 (e), knob `maker_recolhe_quando_o_livro_anda`.)*
   Sem
   recolher: **−4.227,56 USDC no dia**. Recolhendo 100 ms depois de o melhor
   bid cair abaixo da nossa ordem: **−79,76** no termo determinístico. É o
   regime EVENT do `poly-maker` reduzido ao gatilho mais barato que existe, e
   cabe no laço do 4.2, que já acorda a cada segundo para fechar markout.
2. **O gatilho pelo SPOT ajuda um pouco mais** (salto de 3 bps em 2 s,
   cooloff de 5 s): o taker que nos varre reage ao Binance, e esperar o livro
   andar já é tarde. Melhora o termo determinístico e reduz o fill tóxico.
3. **Janela longa > janela curta:** +143,96 ¢ por janela de 1 h contra
   **+0,63 ¢** por janela de 5 min. As janelas curtas de cripto são o pior
   lugar para esta estrutura — e são justamente as do taker.
4. **A trava do par PIORA** (345 → 184 pares, termo determinístico junto): a
   perna que sobra é a cara.
5. **O colchão do RuneDn quase elimina o fill** (4 pares em 1.000 janelas).
   Numa amostra de 4 h ele parecia o melhor de todos; com o dia inteiro,
   some. Serve como filtro de *reward sem fill*, não como estratégia de par.
6. **Cotar a partir do MICROPRICE é o que vira o sinal** — medido no mesmo
   dia: o termo determinístico sai de −34,67 (juntando ao topo, lote 20)
   para **+30,68** cotando 1 tick abaixo do microprice, e a soma paga do par
   cai de 1,015 para 0,995 — o par passa a custar menos de 1,00. Com lote
   100, +137,84. O viés de inventário soma no lote pequeno (+36,09) e
   subtrai no grande. Três ticks abaixo do microprice mata o fill.
   É a peça central do `poly-maker` (`quoting.py:37-39`), e era a que
   faltava aqui. **Portado em 2026-09-14** como ÂNCORA do laço ao vivo
   (quadro 4.0 (f)): `OrderBook.microprice` é agora a única implementação —
   este script chama a mesma —, e o knob `maker_ticks_abaixo_do_microprice`
   liga o teto. Desligado por padrão, e a rodada que o medir tem de ser
   separada da do item 1: as duas juntas não se distinguem.
7. **Só com posição real, isto é, depois do LIVE:** fusão YES+NO via CTF
   `mergePositions`, e `rewards_min_size` refrescado do Gamma como tamanho em
   pool fino.

O que a medida NÃO autoriza a dizer: que a rota maker morreu. Ela foi medida
nas janelas Up/Down de cripto; os pools do 1.12 são outro regime, têm reward
por estar no livro, e estão sendo medidos com o mesmo motor
(`scripts/maker_de_pares_nos_pools.py`).

## 7. Fontes

- `poly-maker`: <https://github.com/warproxxx/poly-maker> (`TIPS.md`, `README.md`, `src/polymaker/strategy/`, `execution/reconciler.py`, `merge.py`)
- `polymarket-automated-mm`: <https://github.com/terrytrl100/polymarket-automated-mm> (`trading.py`, `update_hyperparameters.py`, `BOT_OVERVIEW.md`)
- `polymarket-liquidity-bot`: <https://github.com/RuneDn/polymarket-liquidity-bot> (`app.py`)
- Programa de maker rebates: <https://docs.polymarket.com/programs/maker-rebates> (verificado em `API_NOTES.md` §12.6: `crypto_fees_v2`, rate 0,07, rebate 20 %)
- Leaderboards/relatos de `0x8dxd` e dos makers de Economics: leitura de reportagens e threads públicas, sem código — tratados aqui como **estrutura observada**, não como fato verificado na fonte.
