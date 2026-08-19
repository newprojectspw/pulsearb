# PULSEARB

Bot de arbitragem de latência/mispricing para os mercados **Up/Down de cripto
da Polymarket**. Modo simulação por padrão; modo real só com trava tripla.

> **Estado atual: M2 implementado, veredito PENDENTE de dado.** Existem
> feeds, descoberta, recorder de produção, replay determinístico, modelo TWAP
> endgame e backtest com todos os descontos. **Não existe execução** — isso é
> M4. O bot não envia ordem nenhuma e não toca em dinheiro.
>
> O M2 responde uma pergunta: *existe edge líquido depois de taxa, spread,
> slippage e latência?* A resposta exige 72h de gravação real — ver
> [`docs/VEREDITO_M2.md`](docs/VEREDITO_M2.md) e
> [`docs/RUNBOOK_VPS.md`](docs/RUNBOOK_VPS.md).

---

## Estrutura

```
src/pulsearb/
├── main.py              # entrypoint: python -m pulsearb
├── settings.py          # config.yaml + .env (pydantic-settings)
├── feeds/
│   ├── base.py          # reconexão backoff+jitter, watchdog, timestamps
│   ├── rtds.py          # RTDS: spot Binance + TWAP 60s Chainlink
│   ├── binance_ws.py    # Binance direto: bookTicker + kline_1h
│   └── poly_ws.py       # book do CLOB + heartbeat PING/PONG
├── markets/discovery.py # janelas ativas, fonte de resolução, gates
├── engine/
│   ├── fees.py          # curva de taker fee (as duas unidades)
│   ├── twap.py          # modelo TWAP endgame — o núcleo analítico
│   ├── hourly.py        # modelo do candle horário
│   └── anchor.py        # validação empírica da âncora de abertura
├── recorder/            # gravação JSONL gzip, rotação, lacunas
├── replay/              # reprodução determinística das gravações
├── backtest/            # book real, slippage, latência, relatórios
├── analysis/            # as quatro medições do M2.E
├── ui/server.py         # dashboard FastAPI + WebSocket
└── obs/                 # logging JSON + histogramas de latência
```

A base factual do projeto — endpoints, protocolos, fees, fonte de resolução —
está em **[`docs/API_NOTES.md`](docs/API_NOTES.md)**. Nada aqui foi inventado:
cada afirmação lá tem fonte e rótulo `[VERIFICADO]` ou `[PENDENTE]`.

---

## Instalação

Python **3.12+**.

```bash
make venv          # cria .venv e instala com as dependências pinadas
make check         # ruff + pytest — precisa ficar verde antes de qualquer commit
```

Sem `make`:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
```

---

## Rodar o dashboard

```bash
python -m pulsearb --mode sim                 # feeds reais (precisa de rede)
python -m pulsearb --mode sim --fake-feeds    # ticks sintéticos, sem rede
```

Abre em `http://127.0.0.1:8080`. Banner azul = SIM.

`--fake-feeds` existe porque o ambiente de desenvolvimento não alcança a
Polymarket. Os ticks são sintéticos e a tela diz isso (contador `fake_ticks`) —
nunca se passam por dado real.

`SHADOW` e `LIVE` ainda não existem: pedir qualquer um deles cai para SIM com
aviso no log. As travas chegam no M4.

---

## Recorder — comece a gravar hoje

Cada dia sem gravação é dado de backtest perdido.

```bash
python -m pulsearb.recorder --duration 72h
```

Na VPS, sob systemd: [`docs/RUNBOOK_VPS.md`](docs/RUNBOOK_VPS.md).

**~470 MB/h comprimido — medido em produção, ~100x a estimativa original.**
72h ≈ 34 GB; cada 1 GB livre compra ~2h de gravação. Peça **80 GB** de disco,
ou 50 GB com descarga periódica das horas já baixadas. O volume vem do book do
CLOB: uma única janela ativa gera mais de 300 eventos `price_change` por
segundo. Dimensionamento, descarga e volume extra na
[§6 do runbook](docs/RUNBOOK_VPS.md).

Grava em `data/recordings/pulsearb-YYYYmmdd-HHMM.jsonl.gz` (rotação horária,
coberto pelo `.gitignore`). Cada linha:

```json
{"ts_mono_ns": ..., "ts_wall_ns": ..., "fonte": "rtds", "payload": {...}}
```

- `ts_mono_ns`: relógio monotônico da chegada — é o que serve para medir latência
- `ts_wall_ns`: relógio de parede — só para registro
- `payload`: o payload **cru**, sem interpretação nossa

O que é gravado:

- **RTDS** — spot Binance repassado + TWAP 60s da Chainlink, para **todos** os
  ativos (`assets` + `extra_price_assets`)
- **Binance direto** — `bookTicker` + `kline_1h` dos ativos operáveis; é o
  preço-verdade das janelas horárias
- **CLOB** — book de cada janela descoberta, incluindo as horárias, com
  `custom_feature_enabled=true` (traz best bid/ask e os eventos de resolução)

A descoberta roda a cada 60s, então janelas novas de 5m entram sozinhas.

**Rode fora do ambiente de desenvolvimento** (VPS/Colab): a Polymarket é
inalcançável de dentro dele.

Em background numa VPS:

```bash
nohup python -m pulsearb.recorder --hours 168 > recorder.log 2>&1 &
```

Se o disco engasgar, o writer **descarta** o excesso e conta em `descartadas`
no log — perder tick de gravação é aceitável, travar o feed não é.

---

## Scripts (rodar fora do ambiente de desenvolvimento)

Os dois primeiros são **stdlib pura** (Python 3.10+, sem `pip install`):

| Script | Para quê |
|---|---|
| `scripts/benchmark_latency.py` | Escolher a região da VPS com dado medido. Compare pelo **p99**, não pela média. |
| `scripts/verify_market_facts.py` | Ler fee, tick, slugs e regras ao vivo e conferir contra o `API_NOTES`. |
| `scripts/smoke_feeds.py` | 60s de RTDS + book: contagem por tópico e **cadência do TWAP** (dado de estratégia). Precisa de `websockets`. |
| `scripts/smoke_discovery.py` | Roda a descoberta real do projeto e imprime a tabela das janelas. Precisa do pacote instalado + `httpx`. |

```bash
python3 scripts/benchmark_latency.py --label amsterdam --json out-ams.json
python3 scripts/verify_market_facts.py --raw
python3 scripts/smoke_feeds.py --auto-discover --seconds 60
python3 scripts/smoke_discovery.py
```

---

## Backtest

Sobre uma gravação já coletada:

```bash
python -m pulsearb.backtest data/recordings --json relatorio.json
```

Sai um relatório JSON com PnL líquido por jogo/ativo/duração, hit rate,
drawdown, curva de calibração por bucket de tempo restante, curva de edge por
threshold, sensibilidade a latência (150/300/600/1000ms), o funil de sinais
(quantos existiram × quantos seriam preenchíveis) e as quatro medições do
M2.E.

Ele é **pessimista por construção**:

- **taxa** com `r` e `e` lidos do mercado gravado, nunca constantes
- **slippage** atravessando o book real, nível a nível — não é parâmetro
  chutado, é o que o livro dizia
- **latência** entre sinal e fill: o preenchimento usa o book de
  `t + latência`, não o que gerou o sinal
- só conta preenchido o que o book **comportava** naquele instante

Sem gravação, o comando falha com mensagem clara em vez de produzir número
sobre conjunto vazio.

## Configuração

`config.yaml` — parâmetros. `.env` (copiado de `.env.example`) — modo e, a
partir do M4, segredos. **O `.env` real nunca é commitado.**

Três decisões que estão no config e têm motivo:

- **`durations: auto`** — durações são descobertas por dados, nunca fixadas.
  Pôr uma lista fixa aí é recusado com erro. Motivo: a janela de 1h usa um
  padrão de slug completamente diferente do das outras
  ([API_NOTES 12.2](docs/API_NOTES.md)); código que assume grade uniforme
  quebra em silêncio.
- **`user_agent`** — a Cloudflare devolve `403 error code: 1010` para request
  sem User-Agent de navegador. Todo cliente HTTP/WS do projeto manda o dele.
- **`extra_price_assets`** — ativos que só têm preço gravado, sem operação.
  Dado barato hoje é backtest possível amanhã.

---

## Duas coisas que o código se recusa a fazer

**Não opera janela cuja fee não consegue ler.** `r` e `e` da curva de fee vêm
da API por mercado, nunca de constante no código. Sem fee legível — ou com
divergência entre Gamma e CLOB — a janela é marcada não-operável e logada.

**Não confia em `closed=false`.** A Gamma tem mercados-zumbi: janelas de 2025
ainda abertas. O filtro é triplo — `end_date` na query, `acceptingOrders=true`
concordando nas duas fontes, e `endDate` no futuro checado localmente
([API_NOTES 12.12](docs/API_NOTES.md)).

**Não confia em "o slug resolveu".** A Gamma responde HTTP 200 para slug de
janela antiga, devolvendo o mercado do ano passado sem sinalizar nada — e o
slug horário é nominal (`august-16-2pm` se repete todo ano). Todo slug
resolvido é validado contra a janela que foi **pedida**: `endDate` tem que
bater, dentro de 60s, e estar no futuro
([API_NOTES 12.12b](docs/API_NOTES.md)).

---

## Fee: a distinção que muda a estratégia

```
fee_por_share(p) = r · (p · (1 − p))^e        # r=0.07, e=1 ao vivo
```

A mesma fee, em duas unidades:

| preço | por share | sobre o capital |
|---|---|---|
| 0,50 | 0,0175 | **3,5%** |
| 0,90 | 0,0063 | 0,7% |
| 0,10 | 0,0063 | **6,3%** |

"A fee tende a zero nos extremos" só vale **por share**. Sobre o capital,
comprar o lado barato é proporcionalmente mais caro — e é o lado barato que
esta estratégia compra. `engine/fees.py` expõe as duas funções separadas de
propósito.

---

## São dois jogos, não um

Confundir os dois é o erro mais caro possível aqui — daria para operar a janela
horária com o preço errado e nunca perceber.

| | 5m / 15m / 4h | 1h (horária) |
|---|---|---|
| Slug | `btc-updown-5m-{epoch}` | `bitcoin-up-or-down-august-16-2026-2pm-et` |
| Resolve por | TWAP 60s da Chainlink | candle 1h da Binance (`close ≥ open`) |
| Feed | RTDS `crypto_prices_twap_sixty` | **Binance `kline_1h`** + RTDS `crypto_prices` |
| Fees, tick, mínimo | idênticos nos dois | idênticos nos dois |

Empate resolve Up nos dois.

O feed da Binance é de primeira classe, não conveniência: o RTDS repassa o
*spot*, mas a janela horária resolve por **candle** — e candle tem `open`, que
tick nenhum reconstrói depois do fato. Se não estiver gravando enquanto a hora
acontece, o `open` se perde.

A descoberta classifica cada janela individualmente; fonte não reconhecida é
ignorada, nunca operada por suposição.

---

## Testes

```bash
make check
```

Nenhum teste toca rede externa. Os clientes WS são testados contra um servidor
`websockets` em loopback; a descoberta contra um cliente HTTP falso; os parsers
contra fixtures em `tests/fixtures/`, das quais duas são **capturas reais** de
produção (ver `tests/fixtures/README.md` — está documentado ali o que é captura
real e o que é estrutura sintética).
