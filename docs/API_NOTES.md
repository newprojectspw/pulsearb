# API_NOTES — PULSEARB

**Data da verificação: 2026-08-16**
**Marco: M0 — Verificação de documentação (antes de qualquer código)**

Este documento é a base factual do projeto. Nada aqui é suposição: cada afirmação
carrega o rótulo `[VERIFICADO]` ou `[NÃO VERIFICADO]` e a fonte exata.

---

## 0. Como este documento foi verificado (e o que ficou de fora)

### 0.1. Limitação do ambiente — leia antes de tudo

O ambiente onde este M0 foi executado tem **egress de rede restrito**. Os
seguintes hosts estão bloqueados pelo proxy (`403` no CONNECT, sem exceção):

| Host | Status |
|---|---|
| `docs.polymarket.com` | **bloqueado** |
| `help.polymarket.com` | **bloqueado** |
| `clob.polymarket.com` | **bloqueado** |
| `gamma-api.polymarket.com` | **bloqueado** |
| `data-api.polymarket.com` | **bloqueado** |
| `api.binance.com` | **bloqueado** |

Portanto **não foi possível ler o site oficial de documentação nem bater em
nenhuma API ao vivo**. Em vez de inventar, a verificação foi feita contra a
**fonte primária mais forte disponível: o código-fonte do SDK oficial**, que é
o que efetivamente define os endpoints em produção.

### 0.2. Fontes primárias efetivamente lidas

| Fonte | Como | Data |
|---|---|---|
| `polymarket-client` **0.6.0** (sdist oficial do PyPI, código-fonte completo) | `pip download --no-binary :all: polymarket-client` | 2026-08-16 |
| Metadados PyPI de `polymarket-client` (`requires_python`, `requires_dist`, releases) | `https://pypi.org/pypi/polymarket-client/json` | 2026-08-16 |
| `Polymarket/py-sdk` README | `raw.githubusercontent.com` | 2026-08-16 |
| `Polymarket/py-clob-client` README + `endpoints.py` + `headers/headers.py` + `constants.py` | `raw.githubusercontent.com` | 2026-08-16 |
| `Polymarket/py-clob-client-v2` README | `raw.githubusercontent.com` | 2026-08-16 |
| `Polymarket/real-time-data-client` README | `raw.githubusercontent.com` | 2026-08-16 |

Citações de código abaixo usam caminhos relativos à raiz do sdist
`polymarket_client-0.6.0/` — todos reproduzíveis com o mesmo comando `pip download`.

### 0.3. Fontes secundárias (imprensa/terceiros) — tratadas como pista, não como verdade

Usadas apenas para **saber o que procurar**, nunca como base de implementação.
Tudo que veio só daqui está marcado `[NÃO VERIFICADO]` e listado na seção 10.

---

## 1. Decisão de SDK

### 1.1. `py-clob-client` (o cliente clássico) está MORTO — não usar

`[VERIFICADO]` — README de `Polymarket/py-clob-client`, primeiras linhas:

> **Warning:** This repository has been archived and is no longer maintained.
> The client is no longer functional and should not be used for new or existing
> integrations. Please migrate to our new unified SDK: https://github.com/Polymarket/py-sdk

Isso encerra a dúvida do enunciado do M0: o fallback **não** é o cliente antigo.

### 1.2. Escolhido: `py-sdk` → pacote PyPI `polymarket-client`

`[VERIFICADO]` — PyPI + README:

- Nome no PyPI: **`polymarket-client`** (o repositório se chama `py-sdk`; o
  pacote importável é `polymarket`)
- Versão corrente: **0.6.0**
- `requires_python`: **`>=3.11`** — compatível com o Python 3.12+ pedido no M1
- Dependências que ele arrasta: `eth-abi`, `eth-account`, `eth-utils`,
  `httpx[http2]`, `msgpack`, `pydantic`, `websockets`
- Extras opcionais: `arrow`, `pandas`, `polars`, `quant` (trazem `pyarrow`) —
  **não vamos usar**, para não inchar o hot path

Ponto positivo relevante para o PULSEARB: o SDK oficial já depende de
`httpx`, `pydantic` e `websockets`, exatamente as bibliotecas da nossa lista
base do M1. Não há conflito de stack.

### 1.3. Riscos aceitos ao adotar o `py-sdk`

`[VERIFICADO]` — README, seção "API Compatibility":

- Está na linha **0.x**: *"minor releases on the 0.x line may include breaking changes"*.
  → **Decisão: pinar versão exata (`polymarket-client==0.6.0`) no `pyproject.toml`
  do M1** e só subir versão de propósito, com releitura destas notas.
- APIs de **Perps são experimentais**. Não tocamos em Perps (fora do escopo v1).

### 1.4. Fallback

`[VERIFICADO]` — README de `py-clob-client-v2`:

> We've released a new unified SDK that combines all our REST APIs and
> WebSockets into one package. We recommend Polymarket/py-sdk for new projects.

`py-clob-client-v2` continua vivo e é o fallback se o `py-sdk` travar. **Motivo
registrado para a escolha do py-sdk:** é o recomendado oficialmente, é o único
que já traz CLOB + Gamma + Data + RTDS + WebSockets num pacote só, e o v2 cobre
apenas o CLOB — usar o v2 exigiria implementar Gamma e RTDS na mão.

**Nota de arquitetura:** mesmo adotando o SDK oficial para *ordens* (assinatura
EIP-712, nonce, auth L1/L2 — coisas que não se deve reimplementar), o hot path
de leitura (`feeds/`) vai falar **WebSocket direto**, com `websockets` + `orjson`,
sem passar pelo SDK. Os protocolos de WS estão documentados na seção 6 e são
simples o bastante para isso. Justificativa: o SDK usa `pydantic` para validar
cada evento, o que é caro demais para o caminho tick→decisão.

---

## 2. Endpoints — todos verificados no código do SDK

`[VERIFICADO]` — `src/polymarket/environments.py`, objeto `PRODUCTION`
(`_EnvironmentConfig`). Estes são os valores literais do SDK oficial 0.6.0:

| Serviço | URL | Campo no SDK |
|---|---|---|
| CLOB REST | `https://clob.polymarket.com` | `clob_url` |
| CLOB WS — mercado (público) | `wss://ws-subscriptions-clob.polymarket.com/ws/market` | `clob_market_ws_url` |
| CLOB WS — usuário (autenticado) | `wss://ws-subscriptions-clob.polymarket.com/ws/user` | `clob_user_ws_url` |
| Gamma (metadados de mercado) | `https://gamma-api.polymarket.com` | `gamma_url` |
| Data API | `https://data-api.polymarket.com` | `data_url` |
| **RTDS — Real-Time Data Service** | `wss://ws-live-data.polymarket.com` | `rtds_ws_url` |
| Relayer | `https://relayer-v2.polymarket.com` | `relayer_url` |
| RPC Polygon (default do SDK) | `https://polygon.drpc.org` | `rpc_url` |

**Confirmação dos dois endpoints que o enunciado do M0 mandou checar:** ambos
estão corretos — `https://clob.polymarket.com` e
`wss://ws-subscriptions-clob.polymarket.com/ws/market`. O RTDS
(`wss://ws-live-data.polymarket.com`) é um endpoint **novo, que não estava no
enunciado**, e é provavelmente o mais importante para esta estratégia (seção 5).

`[VERIFICADO]` — Chain e contratos (`environments.py`, `PRODUCTION`):

- `chain_id = 137` (Polygon mainnet)
- Colateral (USDC.e): `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB`
- Conditional Tokens (CTF): `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045`
- Exchange padrão: `0xE111180000d2663C0091e4f400237545B87B996B`
- Exchange neg-risk: `0xe2222d279d744050d28e00520010520000310F59`

### 2.1. Caminhos REST do CLOB usados pelo SDK 0.6.0

`[VERIFICADO]` — `src/polymarket/_internal/actions/clob.py` e
`_internal/actions/orders/*.py`:

Leitura de mercado: `/book`, `/books`, `/price`, `/prices`, `/midpoint`,
`/midpoints`, `/spread`, `/spreads`, `/last-trade-price`, `/last-trades-prices`,
`/prices-history`

Metadados/ordem: `/tick-size` (query `token_id`), `/neg-risk` (query `token_id`),
`/markets-by-token/{token_id}`, `/clob-markets/{condition_id}`,
`/order`, `/orders`, `/cancel-all`, `/cancel-market-orders`,
`/fees/builder-fees/{builder_code}`

Auth: `/auth/api-key`, `/auth/api-keys`, `/auth/derive-api-key`,
`/auth/builder-api-key`

### 2.2. Caminhos da Gamma usados pelo SDK 0.6.0

`[VERIFICADO]` — `_internal/gamma_paths.py` e `_internal/actions/gamma.py`:

- `/markets/{id}` · `/markets/slug/{slug}` · `/markets/{id}/tags`
- `/events/{id}` · `/events/slug/{slug}` · `/events/{id}/tags`
- **`/markets/keyset`** e **`/events/keyset`** ← listagem paginada, é o que
  `markets/discovery.py` vai usar no M1
- `/series/{id}`, `/tags/{id}`, `/tags/slug/{slug}`

Paginação keyset `[VERIFICADO]` (`_internal/dispatch.py`, `_internal/request.py`,
`_internal/actions/_cursor.py`):

- query params: **`limit`** e **`after_cursor`**
- resposta: objeto com a chave da coleção (`markets` / `events`) e **`next_cursor`**
- sentinela de fim: **`next_cursor == "LTE="`**

Filtros aceitos por `/markets/keyset` `[VERIFICADO]` (assinatura de
`list_markets_spec`): `closed`, `slug`, `condition_ids`, `clob_token_ids`,
`question_ids`, `ids`, `tag_id`, `tag_match`, `related_tags`, `include_tag`,
`start_date_min`, `start_date_max`, `end_date_min`, `end_date_max`, `order`,
`ascending`, `liquidity_num_min/max`, `volume_num_min/max`, `rewards_min_size`,
`rfq_enabled`, `cyom`, `decimalized`, `locale`, `game_id`,
`sports_market_types`, `market_maker_address`, `position_ids`,
`uma_resolution_status`.

> Para o PULSEARB, a descoberta de janelas ativas de BTC/ETH Up/Down sai de
> `/markets/keyset` com `closed=false` + filtro por `end_date_min/max` (a janela
> que ainda vai fechar), refinado por `slug`.

---

## 3. Autenticação L1 / L2

`[VERIFICADO]` — `_internal/l1_auth.py`, `_internal/hmac.py` (py-sdk 0.6.0),
com os nomes de header confirmados também em
`py-clob-client/py_clob_client/headers/headers.py`.

Três níveis (`py-clob-client/constants.py`: `L0=0`, `L1=1`, `L2=2`):

- **L0** — sem auth. Livro, preços, tick size, Gamma, RTDS. **É tudo que os
  modos SIM e SHADOW precisam.**
- **L1** — assinatura **EIP-712** com a private key. Serve só para criar/derivar
  as credenciais de API.
  - Typed data `[VERIFICADO]`: `domain = {name: "ClobAuthDomain", version: "1", chainId}`,
    `primaryType = "ClobAuth"`, campos `address`, `timestamp` (string), `nonce` (uint256),
    `message`.
  - Headers: `POLY_ADDRESS`, `POLY_SIGNATURE`, `POLY_TIMESTAMP`, `POLY_NONCE`.
- **L2** — **HMAC-SHA256** com as credenciais de API. É o que assina cada ordem.
  - Assinatura `[VERIFICADO]` (`_internal/hmac.py`): segredo decodificado com
    **base64 urlsafe**, HMAC-SHA256 sobre `timestamp + method + request_path + body`,
    resultado re-codificado em **base64 urlsafe**.
  - Headers: `POLY_ADDRESS`, `POLY_SIGNATURE`, `POLY_TIMESTAMP`, `POLY_API_KEY`,
    `POLY_PASSPHRASE`.
  - Detalhe crítico `[VERIFICADO]`: o corpo assinado precisa ser o **body
    pré-serializado exato** que vai no wire (o SDK guarda `serialized_body` só
    para isso). Reserializar o JSON antes de enviar quebra a assinatura.

**Consequência para o PULSEARB:** nada de private key até o M4. Todo o M1–M3
roda em L0.

`[VERIFICADO]` — tipos de assinatura de carteira (README do py-clob-client, ainda
válido conceitualmente e espelhado no py-sdk): `signature_type=0` EOA,
`1` email/Magic, `2` proxy de carteira de navegador. Carteiras proxy exigem
também o `funder` (endereço que segura o dinheiro). Como o M9 manda usar uma
carteira **dedicada**, o caminho natural é EOA (`signature_type=0`) — e nesse
caso é preciso **setar allowances de token manualmente** antes da primeira ordem.

---

## 4. Tipos de ordem, tick size e limites

### 4.1. Tipos de ordem

`[VERIFICADO]` — `src/polymarket/models/clob/orders.py`:

```python
OrderType: TypeAlias = Literal["GTC", "GTD", "FAK", "FOK"]
MarketOrderType: TypeAlias = Literal["FAK", "FOK"]
```

Ou seja: além dos FOK/GTC/GTD do enunciado, existe **FAK** (fill-and-kill,
aceita parcial e cancela o resto). Ordem *a mercado* só aceita **FAK ou FOK**.

**Decisão para o M4:** entrada taker usa **FOK** — tudo ou nada, sem posição
parcial pendurada, exatamente como o enunciado pede. FAK fica anotado como
alternativa para quando a v2 quiser aceitar preenchimento parcial.

### 4.2. Tick size

`[VERIFICADO]` — mesmo arquivo:

```python
TickSize: TypeAlias = Literal["0.1", "0.01", "0.005", "0.0025", "0.001", "0.0001"]
```

O conjunto permitido é reconfirmado em
`_internal/actions/orders/market_data.py` (`_ALLOWED_TICK_SIZES`).
Tick size é **por token**, obtido em `GET /tick-size?token_id=...`.

`[NÃO VERIFICADO]` qual tick size específico os mercados Up/Down de 5m/1h usam
na prática. Fecha com `scripts/verify_market_facts.py` (seção 11).

### 4.3. Tamanho mínimo de ordem

`[VERIFICADO]` — existe campo `minimumOrderSize` (e `minimumTickSize`) no modelo
Gamma `MarketTrading` (`models/gamma/market.py`). O **valor** é por mercado.
`[NÃO VERIFICADO]` — precisa de leitura ao vivo. Isso importa direto para o M4:
o stake default de US$ 5 pode esbarrar no mínimo do mercado.

---

## 5. Estrutura de fees — **o achado que muda o modelo**

### 5.1. A fórmula (verificada no código, não em blog)

`[VERIFICADO]` — `_internal/actions/orders/market.py`, função
`adjust_buy_amount_for_fees`, linha a linha:

```python
effective_rate = fee.rate * ((price * (Decimal(1) - price)) ** fee.exponent)
platform_fee   = (amount / price) * effective_rate
builder_fee    = amount * builder_taker_fee_rate
total_cost     = amount + platform_fee + builder_fee
```

Traduzindo para a notação do M3, com `p` = preço por share e `n` = número de
shares (`n = amount / p`, já que `amount` é o dinheiro gasto):

```
taxa_efetiva = r · (p · (1 − p))^e
fee_em_USDC  = n · r · (p · (1 − p))^e
```

Três consequências que precisam entrar no `engine/fees.py` do M1:

1. **A fee é cobrada por share, não sobre o notional gasto.** Em `p = 0,10`, um
   fee de "1%" custa 1% de *cada share*, o que é **10%** do dinheiro investido.
   Isso é o oposto da intuição do enunciado ("tendendo a 0% nos extremos") — a
   fee em USDC tende a zero nos extremos, mas o **custo relativo ao capital
   arriscado** não. Como a estratégia compra o lado barato, isso é material.
2. **`r` e `e` são parâmetros do mercado, lidos da API — não constantes.**
3. Existe um **builder fee** separado (`builder_taker_fee_rate`), cobrado sobre o
   `amount`. Só se aplica se a ordem for enviada com `builder_code`.
   **Decisão: PULSEARB não usa builder code → builder fee = 0.**

### 5.2. De onde vêm `r` e `e`

`[VERIFICADO]` — duas fontes, ambas no SDK:

- **CLOB**: `GET /clob-markets/{condition_id}` → objeto **`fd`** com as chaves
  **`r`** (rate) e **`e`** (exponent).
  Fonte: `_internal/actions/orders/market_data.py::_parse_platform_fee_info`.
  Se `fd` vier ausente, o SDK assume `rate=0, exponent=0`.
- **Gamma**: campo **`feeSchedule`** no mercado, com
  `exponent`, `rate`, **`takerOnly`** (bool) e **`rebateRate`**.
  Também há `feesEnabled`, `feeType` e `secondsDelay`.
  Fonte: `models/gamma/market.py::FeeSchedule` e `MarketTrading`.

O campo **`takerOnly`** é a confirmação estrutural de que **maker não paga fee**,
e **`rebateRate`** é o rebate do programa de maker rebates. Alinha com o
enunciado ("maker 0% com rebate"), mas os **números** ainda são incógnita.

### 5.3. Valores numéricos — **NÃO VERIFICADOS**

`[NÃO VERIFICADO]` — não consegui bater na API para ler `fd.r` / `fd.e` reais.

Duas hipóteses conflitantes na mesa, e a diferença **não é decorativa**:

| Origem | `r` | `e` | Fee máx. (em `p=0,5`) por share | Sobre o capital investido em `p=0,5` |
|---|---|---|---|---|
| Enunciado do projeto (jan/2026) | ~0,0625 | 1 | ~1,56% | ~3,12% |
| Fonte secundária (ago/2026), categoria cripto | 0,07 | 1 | 1,75% | 3,50% |

O exemplo que circula nas fontes secundárias — *"100 shares de cripto a US$ 0,50:
fee = 0,07 × 100 × 0,50 × 0,50 = US$ 1,75"* — **encaixa exatamente** na fórmula
verificada no código com `r = 0,07` e `e = 1`. Isso é uma corroboração forte,
mas ainda **não é leitura oficial**, então fica marcado como não verificado.

**Regra operacional adotada:** `engine/fees.py` **nunca** vai ter `r` e `e`
hard-coded. Vai ler `fd.r` e `fd.e` do mercado na descoberta, cachear por
`condition_id`, e **recusar-se a operar** (trava do M4) se não conseguir ler a
fee do mercado. Um default no código seria justamente o tipo de chute que este
M0 existe para evitar. A diferença entre 1,56% e 1,75% é ~0,2pp — em cima de um
threshold de 2pp de edge, é 10% do sinal inteiro.

---

## 6. Feeds em tempo real e protocolos de WebSocket

### 6.1. CLOB Market WS (livro de ofertas)

`[VERIFICADO]` — `_internal/streams/clob/market_protocol.py` e
`_internal/streams/clob/heartbeat.py`.

- URL: `wss://ws-subscriptions-clob.polymarket.com/ws/market`
- Frame inicial (assinatura):
  ```json
  {"type": "market", "assets_ids": ["<token_id>", ...], "custom_feature_enabled": false}
  ```
- Assinar/desassinar depois de conectado:
  ```json
  {"operation": "subscribe",   "assets_ids": ["..."], "custom_feature_enabled": false}
  {"operation": "unsubscribe", "assets_ids": ["..."]}
  ```
- Com `custom_feature_enabled = true` chegam **também** `MarketBestBidAskEvent`,
  `NewMarketEvent` e `MarketResolvedEvent` (docstring de `MarketSpec` em
  `streams/_specs.py`). **Interessa ao PULSEARB**: top-of-book direto e evento de
  resolução, sem polling.
- **Heartbeat de aplicação** `[VERIFICADO]`: cliente manda o texto puro `"PING"`
  a cada **10 s**; servidor responde `"PONG"`; a conexão é considerada morta com
  **30 s** sem PONG. Não é o ping/pong do protocolo WebSocket — é texto na
  camada de aplicação.

### 6.2. RTDS — o feed de preço "verdade"

`[VERIFICADO]` — `_internal/streams/rtds/protocol.py`, `models/rtds_events.py`,
`streams/_specs.py`.

- URL: `wss://ws-live-data.polymarket.com` (sem path)
- Frame de assinatura:
  ```json
  {"action": "subscribe", "subscriptions": [{"topic": "<wire_topic>", "type": "update"}]}
  ```
  (`"action": "unsubscribe"` com a mesma forma para sair)
- Tópicos, com o mapeamento wire ↔ API `[VERIFICADO]`:

| Tópico no wire | Nome na API | Conteúdo |
|---|---|---|
| `crypto_prices` | `prices.crypto.binance` | preço spot da **Binance**, repassado |
| `crypto_prices_chainlink` | `prices.crypto.chainlink` | preço **Chainlink** |
| `crypto_prices_twap_thirty` | `prices.crypto.chainlink.twap` | **TWAP Chainlink, janela de 30 s** |
| `crypto_prices_twap_sixty` | `prices.crypto.chainlink.twap` | **TWAP Chainlink, janela de 60 s** |
| `equity_prices` | `prices.equity.pyth` | ações (fora do escopo) |
| `comments` | — | comentários (fora do escopo) |

- Payload de preço `[VERIFICADO]` (`PriceUpdatePayload`): `symbol`, `timestamp`
  (epoch ms), `value` (decimal). **Nuance do TWAP** (`CryptoPricesChainlinkTwapPayload`):
  o payload traz também `window_s` (30 ou 60) e `full_accuracy_value` — string
  inteira **escalada em 1e18**, que o SDK prefere sobre `value` (conversão em
  `_chainlink_e18_to_decimal`). O evento `crypto_prices_chainlink` simples traz
  só `value` decimal, sem escala.
- Formato do símbolo TWAP `[VERIFICADO]` (docstring de
  `CryptoPricesChainlinkTwapSpec`): *"lowercase slash-delimited pairs such as
  `btc/usd`"*. Sem `symbols`, a assinatura recebe todos.
- Janelas válidas `[VERIFICADO]`: **apenas 30 e 60 segundos**
  (`_CRYPTO_PRICES_CHAINLINK_TWAP_WINDOWS = frozenset({30, 60})`).

**Isso é enorme para o projeto:** o RTDS repassa **tanto o spot da Binance quanto
o TWAP Chainlink** por um WebSocket público, sem credencial. Ou seja, dá para
comparar o preço-verdade com o preço do book **na mesma conexão e sem
credencial de Chainlink**.

---

## 7. Fonte de resolução por tipo de mercado — **mudou em agosto/2026**

Esta é a seção mais sensível do M0, porque define qual é o "preço verdade" de
cada janela. E é onde o enunciado do projeto está **desatualizado**.

### 7.1. O que o código do SDK prova

`[VERIFICADO]`: o SDK oficial 0.6.0 expõe, como cidadãos de primeira classe,
tópicos de **TWAP Chainlink com janelas de exatamente 30 s e 60 s** — e apenas
essas duas. Um SDK não ganha um tipo de assinatura dedicado, com janelas fixas
em 30/60 s, se essas janelas não corresponderem a algo que o produto usa.

### 7.2. O que as fontes secundárias dizem

`[NÃO VERIFICADO — fonte secundária, imprensa]`:

- Desde **7 de agosto de 2026, 00:00 UTC**, os mercados Up/Down de **5m, 15m e
  4h** passaram a resolver por **TWAP calculado pela Chainlink**, não mais por
  snapshot.
- **5m usa janela de 30 s**; **15m e 4h usam janela de 60 s**.
- Preço de abertura **e** de liquidação saem do mesmo feed TWAP.
- O RTDS entrou no ar em **4 de agosto de 2026**.
- Motivação: estudo (Stanford/SMU) apontando manipulação por rajadas no spot da
  Binance nos segundos finais das janelas de 5 minutos.

O casamento perfeito entre "5m→30s, 15m/4h→60s" da imprensa e as janelas
`{30, 60}` verificadas no código é uma corroboração forte. Mas continua
**não verificado oficialmente**.

### 7.3. Impacto direto na estratégia — precisa ser dito

O enunciado do projeto assume que o preço-verdade é o **spot da Binance** (M1
prevê `feeds/binance_ws.py` como feed principal). Se a mudança de agosto/2026 se
confirmar ao vivo, isso está **errado para 5m/15m/4h**:

- Prever "fechamento ≥ abertura" olhando spot da Binance quando a liquidação sai
  de um **TWAP Chainlink de 30 s** introduz um erro de base — o TWAP é
  *suavizado*, então o spot instantâneo nos últimos segundos vale muito menos do
  que o modelo do M3 assumiria.
- Por outro lado, **o TWAP é parcialmente previsível**: nos últimos segundos da
  janela, boa parte da média já está formada. Isso é uma fonte de edge
  *melhor* que a original, não pior — mas só se o modelo for construído em cima
  do feed certo.

**Decisão registrada para o M1/M3** (revisada pela verificação ao vivo, ver
12.2b): o preço-verdade é **por duração**, e os dois feeds são de primeira
classe — TWAP 60s da Chainlink para 5m/15m/4h, spot da Binance para 1h. Ambos
chegam pelo **RTDS**, num único WebSocket (`crypto_prices_twap_sixty` e
`crypto_prices`), então `feeds/binance_ws.py` não é necessário: o RTDS já
repassa o spot da Binance.

### 7.4. Regra permanente

`[VERIFICADO]` que a API dá o caminho para nunca ter que chutar: o modelo Gamma
tem `MarketResolution` com o campo **`source`**, além de `questionId`,
`umaResolutionStatus` e `resolvedBy`, e o `description` do mercado carrega as
regras em texto (`models/gamma/market.py`).

**Regra do projeto, sem exceção:** `markets/discovery.py` lê `resolution.source`
+ `description` de **cada** janela e classifica a fonte de verdade **por
mercado**. Se a classificação falhar, a janela é ignorada — nunca operada por
suposição.

---

## 8. Rate limits

`[NÃO VERIFICADO]` — `docs.polymarket.com` bloqueado. Os números abaixo vêm de
fontes secundárias e servem só para dimensionar; **não** vão virar constante no
código sem confirmação:

- REST global: ~15.000 req / 10 s
- CLOB geral: ~9.000 req / 10 s
- `POST /order`: burst de ~3.500 / 10 s e sustentado de ~36.000 / 10 min (~60/s)
- Enforcement por Cloudflare — pedidos excedentes tendem a ser **enfileirados**,
  não rejeitados na hora (o que se manifesta como *latência*, o pior modo de
  falha possível para nós)
- WebSocket **não** consome cota de REST

`[VERIFICADO]` — o SDK trata `HTTP 429` como `RateLimitError` e lê o atraso
sugerido do header **`Retry-After`** ou do campo **`retry_after_seconds`** do
corpo (`clients/_transport.py`, `errors.py`). É esse contrato que o M1 vai
implementar no cliente REST.

**Postura do PULSEARB:** viver de WebSocket. REST só para descoberta de janelas
(a cada poucos segundos) e envio de ordem. Nunca fazer polling de livro.

---

## 9. Slugs e descoberta de janelas

`[NÃO VERIFICADO]` — o padrão `btc-updown-5m-{timestamp}` citado no enunciado
**não pôde ser confirmado**: exige uma consulta ao vivo na Gamma.

`[VERIFICADO]` — a infraestrutura para descobrir existe e está mapeada:
`/markets/keyset` aceita `slug` (aceita lista), `end_date_min` / `end_date_max`,
`closed`, `tag_id` e `related_tags`; e `/markets/slug/{slug}` busca um mercado
específico. Fechar essa lacuna é o primeiro passo do M1, com
`scripts/verify_market_facts.py`.

`[NÃO VERIFICADO]` — o atraso de ~2 min entre fechamento da janela e liquidação.
Existe um campo `secondsDelay` em `MarketTrading` `[VERIFICADO]` que é o
candidato natural a carregar esse valor; o **valor** precisa de leitura ao vivo.

---

## 10. Placar honesto: o que ficou verificado e o que não

### Verificado (fonte primária: código do SDK oficial 0.6.0)

- [x] SDK a usar: `polymarket-client` 0.6.0; `py-clob-client` está arquivado e
      quebrado; `py-clob-client-v2` é o fallback
- [x] Todos os endpoints REST e WS de produção, inclusive o RTDS
- [x] Mecânica de auth L1 (EIP-712 `ClobAuthDomain`) e L2 (HMAC-SHA256 urlsafe)
      e os nomes exatos dos headers
- [x] Tipos de ordem (`GTC`/`GTD`/`FAK`/`FOK`; mercado só `FAK`/`FOK`)
- [x] Conjunto de tick sizes permitidos
- [x] **Fórmula** da fee dinâmica e de onde `r`/`e` são lidos
- [x] Existência de `takerOnly` e `rebateRate` (maker sem fee + rebate)
- [x] Protocolo dos dois WebSockets, incluindo o heartbeat `PING`/`PONG` de 10 s
- [x] Tópicos do RTDS, com TWAP Chainlink de 30 s e 60 s
- [x] Campos de resolução da Gamma (`resolution.source`, `description`)
- [x] Paginação keyset (`limit`, `after_cursor`, `next_cursor`, sentinela `LTE=`)
- [x] Contrato de rate limit no cliente (429 + `Retry-After`)

### Não verificado — pendente de execução na VPS

- [ ] **Valores** de `fd.r` e `fd.e` para mercados cripto Up/Down
- [ ] Tick size e `minimumOrderSize` reais das janelas de 5m/15m/1h/4h
- [ ] Padrão real dos slugs (`btc-updown-5m-...`?)
- [ ] Texto das `rules` / `resolution.source` de cada tipo de janela — **e com
      isso a confirmação da mudança para TWAP Chainlink**
- [ ] Se ainda existe janela de **1h** e qual a fonte de resolução dela (a
      imprensa cita 5m/15m/4h; o enunciado cita 1h resolvendo por candle Binance)
- [ ] Valor de `secondsDelay` (o atraso de liquidação)
- [ ] Números oficiais de rate limit
- [ ] `rebateRate` do programa de maker rebates

**Como fechar:** rodar `python3 scripts/verify_market_facts.py` de uma máquina
com acesso à rede. Ele consulta só os endpoints verificados nesta seção 2,
imprime os campos crus e gera um bloco pronto para colar aqui.

---

## 11. Scripts entregues neste marco

Ambos são **stdlib pura** — sem `pip install`, sem `venv`. Rodam em qualquer
VPS com Python 3.10+ (verificado em 3.10, 3.11 e 3.12), o que é o ponto: dá
para medir latência de Amsterdã e de
Londres antes de existir projeto.

### `scripts/benchmark_latency.py`

Decide a região da VPS com dado medido, não com achismo.

```bash
python3 scripts/benchmark_latency.py --label amsterdam --json out-ams.json
python3 scripts/benchmark_latency.py --label london    --json out-lon.json
```

Mede:
1. **REST** — separa DNS, TCP connect, TLS handshake e time-to-first-byte, e
   depois roda **100 requisições em conexão quente** (é esse número que importa
   para o hot path) com p50/p90/p99/máx.
2. **WS RTDS** — tempo até a **primeira mensagem** depois do subscribe.
3. **WS CLOB market** — mesma medida (precisa de `--token-id`).
4. **PING/PONG** — **100 pings** de aplicação no WS do CLOB, p50/p90/p99.
   Este é o número que mais se aproxima do custo real de decisão→ack.

O que olhar ao comparar as duas regiões: **p99 do PING/PONG do CLOB** e
**TLS handshake**. Média não decide nada; cauda decide.

### `scripts/verify_market_facts.py`

Fecha as lacunas da seção 10 lendo a API de verdade.

```bash
python3 scripts/verify_market_facts.py --asset btc --asset eth
```

Só usa caminhos verificados na seção 2. Imprime JSON cru — de propósito: o
objetivo é você **ver o dado**, não confiar no meu parser.

---

## 12. Verificação ao vivo — 2026-08-16

Verificação executada pelo Paulo contra a Gamma e o CLOB **de produção** (via
Colab, IP dos EUA), em 2026-08-16. Os fatos abaixo são **dados primários** e
substituem qualquer suposição anterior deste documento onde houver conflito.
As duas respostas cruas estão versionadas em `tests/fixtures/` (Gamma:
`gamma_market_btc_updown_5m.json`; CLOB: `clob_market_compact.json`).

### 12.1. Slugs e ciclo de vida das janelas `[VERIFICADO ao vivo]`

- Padrão: **`{ativo}-updown-{dur}-{epoch_do_INÍCIO_da_janela}`**, com a grade
  **alinhada** (início múltiplo da duração — ex.: janelas de 5m começam em
  epochs múltiplos de 300).
- Confirmado para **btc** e **eth** nas durações **5m, 15m e 4h**.
- Mercados são criados **~24h antes** do início da janela;
  `acceptingOrdersTimestamp` fica ~24h antes do início.

### 12.2. A duração 1h usa OUTRO padrão de slug `[VERIFICADO ao vivo — adendo 2]`

`btc-updown-1h-{epoch}` → **404**: a 1h **não** segue o padrão epoch. O padrão
real é nominal e em fuso de Nova York:

```
{ativo_por_extenso}-up-or-down-{mês}-{dia}[-{ano}]-{hora}{am|pm}-et
ex.: bitcoin-up-or-down-august-16-2026-10am-et
```

- calculado em **America/New_York com horário de verão** — usar `zoneinfo`,
  **nunca offset fixo**;
- ativo por extenso: `bitcoin`, `ethereum` (não `btc`/`eth`);
- existem as duas variantes, com e sem o ano. **A variante COM ano é a
  atual e tem prioridade**; a sem ano **colide com anos anteriores** e pode
  devolver o mercado de 2025 (12.12b). A descoberta tenta a com ano primeiro
  e só cai para a sem ano se a primeira der 404.

**Consequência de projeto mantida: durações NUNCA hardcoded** — a grade de
sondagem é config e o fallback por keyset pega qualquer padrão novo.

### 12.2b. Mapa consolidado dos DOIS jogos `[VERIFICADO ao vivo]`

Não é "uma família de mercados com durações diferentes": são **dois produtos
distintos**, com slug, oráculo e feed próprios. Confundir os dois é o erro
mais caro possível aqui — daria para operar a janela horária com o preço
errado e nunca perceber.

| | Janelas **5m / 15m / 4h** | Janela **1h** (horária) |
|---|---|---|
| Slug | `{ativo}-updown-{dur}-{epoch_do_INÍCIO}` | `bitcoin-up-or-down-{mês}-{dia}-{ANO}-{hora}{am\|pm}-et` (America/New_York) |
| Fonte de resolução | Stream **TWAP 60s da Chainlink**, um por ativo | **Candle 1h BTC/USDT da Binance** (`close ≥ open`) |
| Feed correspondente | RTDS `crypto_prices_twap_sixty` | **Binance direto (`kline_1h`)** + RTDS `crypto_prices` |
| Fees | `crypto_fees_v2`: r=0.07, e=1, takerOnly, rebateRate=0.2 | **Idênticas** |
| Tick / mínimo | 0.01 / 5 shares | 0.01 / 5 shares |
| Resolução | Automatizada (Chainlink) | Indício de **UMA** (`umaReward: "0.6"`) |

**Empate resolve Up nos dois jogos.**

Três consequências de projeto:

1. **`feeds/binance_ws.py` é de primeira classe**, não conveniência. O RTDS
   repassa o *spot* da Binance, mas a janela horária resolve por **candle** —
   e candle tem `open`, que tick nenhum reconstrói depois do fato. O `open`
   da hora corrente só existe se estiver sendo gravado enquanto acontece.
   Stream: `<symbol>@kline_1h` (abre e fecha em UTC+0).
2. **Alinhamento de fuso não é problema.** O offset de America/New_York é
   sempre um número inteiro de horas, então as fronteiras de hora de NY
   coincidem com as de UTC. `kline_1h` em UTC é o candle certo.
3. **O atraso de liquidação precisa ser medido por jogo, separadamente**
   (M2). O `umaReward` só aparece no horário: hipótese de que a resolução
   passa por UMA, o que implicaria latência de liquidação bem maior que a do
   caminho automatizado da Chainlink. **Hipótese, não fato** — o número sai
   da medição, não daqui.

### 12.3. Fonte de resolução das janelas TWAP `[VERIFICADO ao vivo]` — corrige a seção 7

Para **5m, 15m e 4h** (btc e eth): **streams TWAP de 60 segundos da Chainlink**
— `https://data.chain.link/streams/btc-usd-twap-60s-streams` e equivalente eth.
**Um stream por ativo cobre todas as durações.**

> **O anúncio público de agosto/2026 está DESATUALIZADO neste ponto**: dizia
> janela de 30s para mercados de 5m, mas o dado vivo mostra **60s para todas
> as durações**. O tópico RTDS correspondente é **`crypto_prices_twap_sixty`**.
> O tópico `crypto_prices_twap_thirty` existe no protocolo mas não corresponde
> a nenhum mercado observado.

### 12.4. Regra de resolução (texto capturado) `[VERIFICADO ao vivo, semântica PENDENTE]`

**Up** se o TWAP do intervalo do título ≥ preço no início do intervalo; senão
**Down**. **Empate resolve Up.**

A semântica exata da âncora de abertura ("preço no início do intervalo" é o
TWAP no instante de abertura? o último update antes? o primeiro depois?) fica
**PENDENTE — validar empiricamente no M2**: gravar o stream e comparar com as
resoluções reais.

### 12.5. Tick e mínimo `[VERIFICADO ao vivo]`

- Tick: **0.01** (Gamma `orderPriceMinTickSize`; CLOB `mts=0.01`)
- Mínimo: **5 shares** (Gamma `orderMinSize`; CLOB `mos=5`)
- O stake inicial de US$ 5 do M4 é viável: 5 shares a p≤1.00 custam ≤ US$ 5.

### 12.6. Fees `[VERIFICADO ao vivo]` — fecha a pendência da seção 5.3

Gamma `feeSchedule` e CLOB `fd` são **idênticos**:

- `feeType = "crypto_fees_v2"`
- **`rate = 0.07`**, **`exponent = 1`**, `takerOnly = true`, `rebateRate = 0.2`
- Com a fórmula verificada na seção 5.1: pico em p=0,50 → **0,0175 USDC/share**
  (= 1,75% do valor nominal; **3,5% do capital investido**). A hipótese da
  fonte secundária (seção 5.3) estava certa; a referência de jan/2026 do
  enunciado original (1,56%) está desatualizada.

### 12.7. Campos de significado NÃO confirmado — proibido usar em cálculo

- `makerBaseFee = 1000`, `takerBaseFee = 1000` (Gamma; CLOB `mbf`/`tbf`)
- `makerRebatesFeeShareBps = 10000`

Ver investigação na seção 12.13 (tarefa M1.T). Até lá, nenhum desses números
entra em `engine/fees.py`.

### 12.8. Rewards de liquidez (rota maker, v2) `[VERIFICADO ao vivo]`

- `rewardsMinSize = 50` shares (CLOB `r.mi`)
- `rewardsMaxSpread`: observado **1.5** (15m, 4h e um 5m ativo) e **4.5**
  (um 5m pré-abertura) (CLOB `r.ma`)
- No 4h: `clobRewards.rewardsDailyRate = 1666.666667` com
  `assetAddress = 0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174` — o **USDC.e
  real da Polygon**. Relevante para a rota maker (anotada como v2).
- CLOB `r.moas = 1`: significado **desconhecido** — registrado como tal.

### 12.9. `restricted = true` em todos os mercados cripto lidos `[PENDENTE]`

Candidato: restrição regional/regulatória. **Não bloqueia M1–M3** (leitura é
L0 pública). Registrado para investigação antes do M4/LIVE.

### 12.10. Cloudflare exige User-Agent `[VERIFICADO ao vivo]`

Request sem User-Agent de navegador → **`403 error code: 1010`**.
**REGRA PERMANENTE: todo cliente HTTP/WS do projeto envia User-Agent explícito
e configurável** (`user_agent` no `config.yaml`).

### 12.11. Glossário do CLOB compacto (`GET /clob-markets/{conditionId}`)

| Campo | Significado |
|---|---|
| `c` | conditionId |
| `t` | lista `[{"t": tokenId, "o": outcome}]` — **mapear token pelo `o`, nunca por posição** |
| `mos` | minOrderSize |
| `mts` | minTickSize |
| `mbf` / `tbf` | maker/takerBaseFee — **significado pendente** (M1.T) |
| `ao` | acceptingOrders |
| `aot` | acceptingOrdersTimestamp |
| `fd` | `{r: rate, e: exponent, to: takerOnly}` |
| `r` | `{mi: rewardsMinSize, ma: rewardsMaxSpread, e: enabled, moas: **desconhecido**}` |
| `itode` | **desconhecido** — registrado como tal |

### 12.12. Mercados-zumbi na Gamma `[VERIFICADO ao vivo]`

A Gamma contém **mercados-zumbi**: janelas updown de dez/2025 e jan/2026
ainda com `closed=false`. Regra obrigatória em `markets/discovery.py`:
**nunca confiar em `closed=false` isolado**. Filtro sempre triplo:

1. `end_date_min`/`end_date_max` na query (agora até +2h);
2. `acceptingOrders = true`;
3. `endDate` no futuro (checado localmente, não só na query).

Um mercado-zumbi real está em `tests/fixtures/gamma_market_zombie.json` como
teste negativo.

### 12.12b. A Gamma resolve slug de mercado antigo com 200, sem sinalizar `[VERIFICADO ao vivo]`

Pior que o zumbi da listagem: **buscar um slug por nome pode devolver a janela
homônima de outro ano**. Caso observado —
`GET /markets/slug/bitcoin-up-or-down-august-16-2pm-et` respondeu **HTTP 200**
com `endDate` de **2025** e `feesEnabled: false`. Nada na resposta indica que
não é a janela pedida: o slug devolvido é idêntico ao pedido.

Isso derruba a suposição implícita de que "slug resolveu ⇒ é a janela que eu
pedi". É especialmente perigoso no padrão horário (12.2), que é **nominal** —
`august-16-2pm` se repete todo ano; o padrão por epoch (12.1) é imune por
construção, mas a validação vale para os dois.

**Regra obrigatória:** depois de resolver qualquer slug, validar que o
`endDate` corresponde à janela **pedida** (dentro de uma tolerância de
segundos), está no **futuro**, e que `acceptingOrders = true`. Fora disso:
descartar e logar.

Implementado em `markets/discovery.py::validate_window_match`, com tolerância
de 60s — bem abaixo dos 300s da menor janela, para não aceitar a vizinha por
engano.

O par de fixtures mostra o problema inteiro, lado a lado — mesma `question`
("Bitcoin Up or Down - August 16, 2PM ET"), mesmo horário nominal, só o ano
distingue:

| Fixture | Slug | `endDate` | Veredito |
|---|---|---|---|
| `gamma_market_hourly_current.json` | `...-august-16-2026-2pm-et` | 2026-08-16T19:00Z | aceita |
| `gamma_market_stale_slug_resolution.json` | `...-august-16-2pm-et` | **2025**-08-16T19:00Z | **descarta** |

Observação registrada mas **não** usada como gate: o mercado antigo veio com
`feesEnabled: false`. É um correlato do problema, não um critério confiável —
gatear por ele poderia recusar mercado legítimo no futuro.

### 12.13. M1.T — o que o SDK oficial diz sobre os campos pendentes

Investigação executada em 2026-08-16 sobre o código-fonte de
`polymarket-client==0.6.0` (sdist do PyPI, mesmo método da seção 0.2).

**Pergunta 1: o que são `makerBaseFee`/`takerBaseFee` = 1000? Como o SDK os
usa ao montar/assinar uma ordem?**

Resposta: **o SDK 0.6.0 não lê esses campos em lugar nenhum.** Não existe
ocorrência de `makerBaseFee`, `takerBaseFee`, `mbf` ou `tbf` no código
(`grep` em todo o sdist devolve zero). O modelo Gamma (`models/gamma/market.py`)
não os declara, e o parser do CLOB compacto
(`_internal/actions/orders/market_data.py`) lê apenas `fd`, `mos`-equivalentes,
`t` e `neg_risk` — ignora `mbf`/`tbf`.

Mais forte ainda: **a struct EIP-712 da ordem (CTF Exchange v2) não tem campo
de fee nenhum.** Os campos assinados são exatamente `salt, maker, signer,
tokenId, makerAmount, takerAmount, side, signatureType, timestamp, metadata,
builder` (`_internal/actions/orders/typed_data.py`, `_ORDER_TYPE_STRING` /
`_ORDER_FIELDS`). O `feeRateBps` do protocolo v1 antigo sumiu da ordem. A fee
de plataforma é aplicada pelo servidor e apenas *reportada* de volta: o nome
`fee_rate_bps` só aparece em modelos de resposta — trades
(`models/clob/account.py`), eventos de mercado (`models/clob/market_events.py`)
e eventos de usuário (`models/clob/user_events.py`).

Interpretação registrada: `mbf`/`tbf`=1000 são metadados do modelo de fees
antigo do CLOB (bps — 1000 bps = 10%), presentes na resposta mas **não usados**
pelo caminho `crypto_fees_v2`, cujo cálculo real vem de `fd.r`/`fd.e`
(fórmula da seção 5.1). **Proibido usá-los em cálculo** até que alguma fonte
oficial diga o contrário.

**Pergunta 2: o que é `makerRebatesFeeShareBps = 10000`?**

Resposta: **o SDK 0.6.0 não menciona esse campo** (zero ocorrências de
`makerRebatesFeeShareBps` no sdist). O que existe de rebate no SDK: o campo
`rebateRate` do `feeSchedule` da Gamma (`models/gamma/market.py`, alias
`rebateRate`) e os tipos de atividade `MAKER_REBATE`/`TAKER_REBATE` na Data
API (`_internal/actions/data.py`) — ou seja, rebates aparecem como *histórico
de pagamento*, não como parâmetro de ordem. Leitura plausível — registrada
como hipótese, não como fato: 10000 bps = 100% → fração do pool de rebates
atribuída àquele mercado. O único campo de rebate que o projeto usa é
`rebateRate = 0.2`, e mesmo esse só na rota maker (v2).

---

## 13. Fontes

Primárias:
- `polymarket-client` 0.6.0, sdist oficial — https://pypi.org/project/polymarket-client/
- https://github.com/Polymarket/py-sdk
- https://github.com/Polymarket/py-clob-client (arquivado)
- https://github.com/Polymarket/py-clob-client-v2
- https://github.com/Polymarket/real-time-data-client

Secundárias (usadas só como pista, tudo marcado `[NÃO VERIFICADO]`):
- Cobertura de imprensa sobre a migração para TWAP Chainlink (ago/2026)
- Guias de terceiros sobre fees e rate limits da Polymarket (2026)

Bloqueadas neste ambiente, a reler na VPS: `docs.polymarket.com`,
`help.polymarket.com`.
