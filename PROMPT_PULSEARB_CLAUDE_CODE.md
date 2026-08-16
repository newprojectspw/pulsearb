# PROMPT PARA O CLAUDE CODE — PULSEARB v1

(Cole este arquivo inteiro como primeira mensagem no Claude Code, dentro da pasta vazia do repositório `pulsearb`.)

---

## 0. Papel e postura

Você é um engenheiro sênior de infraestrutura quantitativa, especialista em sistemas de baixa latência e execução em mercados cripto. Vai construir o **PULSEARB**: um bot de arbitragem de latência/mispricing para os mercados Up/Down de cripto da Polymarket, com modo simulação por padrão e modo real ativado por trava tripla.

Regras de postura, sem exceção:

- **Nunca invente endpoint, método ou parâmetro.** Antes de codar qualquer integração, leia a documentação oficial e registre o que verificou em `docs/API_NOTES.md`, com data.
- Trabalhe por marcos (M0 a M6). Ao fim de cada marco: rode os testes, faça commit + push, e me mostre um resumo antes de seguir.
- Peça aprovação antes de adicionar dependência fora da lista base.
- Código em inglês; comentários, logs de decisão e README em português.
- Prefira simplicidade que roda a sofisticação que quebra.

---

## 1. MARCO M0 — Verificação de documentação (antes de qualquer código)

1. Leia e resuma em `docs/API_NOTES.md`:
   - https://docs.polymarket.com — CLOB API (REST e WebSocket), autenticação L1/L2, tipos de ordem (FOK/GTC/GTD), tick size, rate limits, fees, allowances de token
   - SDK oficial unificado: https://github.com/Polymarket/py-sdk (recomendado pela Polymarket para projetos novos). Se estiver instável ou incompleto, use `py-clob-client-v2` como fallback e registre o motivo
   - Gamma Markets API — metadados de mercado: janelas ativas, token_ids, slugs (padrão `btc-updown-5m-{timestamp}` e variantes de 15m/1h/4h), regras de resolução
   - Endpoints conhecidos a confirmar: REST `https://clob.polymarket.com`, WS `wss://ws-subscriptions-clob.polymarket.com/ws/market`
2. Registre a **estrutura de fees vigente**. Referência de janeiro/2026: taker fee dinâmica com pico de aproximadamente 1,56% em odds 0,50, tendendo a 0% nos extremos; maker 0% com rebate. Confirme os valores atuais na documentação.
3. Registre a **fonte de resolução por tipo de mercado**. Exemplos verificados em 2026: mercados horários de ETH resolvem pelo candle de 1h ETH/USDT da Binance; janelas de 5m e 4h usam Chainlink Data Streams; liquidação ocorre cerca de 2 minutos após o fechamento da janela. Isso define qual feed é o "preço verdade" de cada mercado. Nunca assuma: leia as rules de cada mercado via API.
4. Crie `scripts/benchmark_latency.py` que mede: RTT ao REST, tempo até a primeira mensagem do WS, e p50/p99 de 100 pings. Vou rodar de Amsterdã e de Londres para decidir a região da VPS com dado medido.

**DoD M0:** `API_NOTES.md` completo e datado + benchmark rodando.

---

## 2. MARCO M1 — Esqueleto do projeto

```
pulsearb/
├── pyproject.toml            # Python 3.12+
├── .env.example              # NUNCA commitar .env real
├── config.yaml               # parâmetros de estratégia e risco
├── Dockerfile
├── deploy/pulsearb.service   # unit systemd
├── docs/API_NOTES.md
├── scripts/
├── src/pulsearb/
│   ├── main.py               # entrypoint, seleção de modo
│   ├── settings.py           # pydantic-settings (.env + config.yaml)
│   ├── feeds/
│   │   ├── binance_ws.py     # bookTicker/aggTrade spot
│   │   ├── chainlink.py      # se aplicável ao mercado (ver M0)
│   │   └── poly_ws.py        # order book CLOB
│   ├── markets/
│   │   ├── discovery.py      # Gamma: janelas ativas, token_ids, rules, fonte de resolução
│   │   └── clock.py          # tempo restante da janela, relógio monotônico, checagem NTP
│   ├── engine/
│   │   ├── fair_value.py     # probabilidade implícita
│   │   ├── signal.py         # edge líquido = prob − preço − fee − buffer
│   │   └── fees.py           # curva de taker fee dinâmica
│   ├── exec/
│   │   ├── executor.py       # despacho por modo: SIM | SHADOW | LIVE
│   │   ├── sim_fill.py       # simulador realista de fills
│   │   └── live_client.py    # SDK oficial, conexão quente reutilizada
│   ├── risk/gates.py         # todas as travas
│   ├── store/db.py           # SQLite WAL, escrita fora do hot path (fila assíncrona)
│   ├── ui/                   # FastAPI + página única com WebSocket
│   └── obs/                  # logging JSON estruturado + histogramas de latência
└── tests/
```

Dependências base: `uvloop`, `orjson`, `websockets`, `httpx`, `pydantic`, `pydantic-settings`, `fastapi`, `uvicorn`, `aiosqlite`, `pytest`, `pytest-asyncio`, `ruff`. SDK da Polymarket conforme decidido no M0.

**Regras do hot path** (feeds → signal → exec):

- asyncio + uvloop, um processo; nenhum thread pool no caminho crítico
- `orjson` para parse; zero I/O de disco síncrono; banco alimentado por fila assíncrona
- `time.monotonic_ns()` para medição de latência; `time.time_ns()` só para registro
- reconexão de WS com backoff exponencial + jitter; watchdog: feed sem tick por mais de 2s = zera posição-alvo e pausa entradas
- medir e registrar histograma de tick→decisão e decisão→ack da ordem

**DoD M1:** `python -m pulsearb --mode sim` sobe, conecta os feeds públicos, loga ticks e o dashboard abre em `:8080` mostrando modo e status dos feeds. Testes de settings passando.

---

## 3. MARCO M2 — Gravador e replay (a fundação honesta)

- `recorder`: grava ticks do spot e do book da Polymarket em parquet ou jsonl, com timestamp de chegada local
- `replay`: reproduz gravações contra o engine para backtest determinístico
- O backtest **sempre** desconta: taker fee dinâmica, slippage modelado pela profundidade real do book gravado, e penalidade de latência configurável (default 150ms) entre sinal e fill

**DoD M2:** 24h de gravação reproduzível; relatório de backtest com PnL líquido, hit rate e drawdown máximo.

---

## 4. MARCO M3 — Fair value e sinal

Modelo v1, simples e calibrável, sem ML:

- `prob_up` = P(fechamento ≥ abertura | preço atual, tempo restante), via aproximação normal do log-retorno com volatilidade realizada rolling (retornos de 1s do spot, EWMA)
- `edge_liquido` = prob_estimada − melhor_ask − taker_fee(preço) − buffer_slippage
- Entra se `edge_liquido ≥ threshold` (config; default conservador de 2 pontos percentuais)
- v1 segura até a resolução; venda antecipada fica anotada como v2
- Calibração: script que compara probabilidade prevista × frequência real nas gravações e salva a curva de calibração em `docs/`

**DoD M3:** backtest do M2 rodando com o modelo e curva de calibração gerada.

---

## 5. MARCO M4 — Execução e travas

**Modos**, via config + env:

- `SIM` (default): fills simulados pelo `sim_fill` contra o book real ao vivo
- `SHADOW`: decide ao vivo, não envia nada, registra "teria feito X"
- `LIVE`: envia ordens reais. Só liga se TODAS as condições forem verdadeiras: `MODE=LIVE` no `.env` **+** arquivo `CONFIRM_LIVE` presente na raiz **+** digitar a frase `EU ACEITO O RISCO` no prompt de inicialização. Faltou qualquer uma, o sistema cai para SIM com aviso vermelho no dashboard.

**Ordens em LIVE:** FOK para entradas taker (tudo ou nada, sem fill parcial pendurado), conexão quente reutilizada, idempotência/controle de nonce, tratamento explícito de rejeição e timeout.

**Travas de risco** (`risk/gates.py`) — todas configuráveis, todas logadas quando disparam, cada uma com teste próprio:

- stake máximo por trade e por janela (default US$ 5 no início do LIVE)
- perda diária máxima → kill (default US$ 20)
- N perdas consecutivas → pausa de 1h (default 4)
- exposição simultânea máxima (default 2 janelas)
- feed velho, relógio dessincronizado (>250ms vs NTP) ou spread anômalo → não opera
- kill switch: arquivo `KILL` na raiz OU botão no dashboard → cancela ordens abertas e para tudo

**DoD M4:** suíte de testes das travas completa e SHADOW rodando 24h sem crash.

---

## 6. MARCO M5 — Interface simples e explicativa

FastAPI servindo página única (HTML + JS puro ou HTMX, sem framework pesado), atualização via WebSocket:

- Banner gigante do modo: SIM (azul) / SHADOW (amarelo) / LIVE (vermelho)
- PnL do dia e acumulado, hit rate, número de trades
- Tabela de janelas ativas: mercado, tempo restante, prob estimada, preço do book, edge líquido
- **Log de decisões em português claro**, uma linha por evento, incluindo por que NÃO entrou. Exemplo: `14:32:07 ENTREI Up BTC-5m: prob 86% vs ask 0,71; fee 0,9%; edge 4,1pp` / `14:33:02 PASSEI ETH-1h: edge 0,8pp abaixo do threshold`
- Latência p50/p99 de tick→decisão e decisão→ack
- Botões: kill switch, pausar entradas, retomar

**DoD M5:** dashboard funcional em SIM, legível no celular.

---

## 7. MARCO M6 — Empacotamento e runbook

- Dockerfile slim + unit systemd (`Restart=always`, `EnvironmentFile`)
- `README.md` como runbook em português: subir a VPS, clonar, configurar `.env`, rodar SIM, interpretar o dashboard, critérios para LIVE, como parar tudo
- `make check`: ruff + pytest + mypy básico

**DoD M6:** deploy documentado de ponta a ponta, testável por alguém que nunca viu o projeto.

---

## 8. Critérios para ligar o LIVE (escrever no README)

1. Recorder por 72h ou mais e backtest com resultado líquido positivo já com a penalidade de latência realista
2. SHADOW por 2 semanas ou mais com edge líquido positivo **medido**, não estimado
3. LIVE começa com o stake mínimo e os limites default do M4; aumento de stake só após 100 trades com expectativa positiva

Sem atalho nesses três. Se o SHADOW não mostrar edge, o mercado já fechou essa janela e o projeto vira aprendizado, não prejuízo.

---

## 9. Segurança

- Carteira **dedicada** ao bot, contendo apenas o capital de operação em USDC na Polygon. Nunca a carteira principal
- Private key e credenciais só no `.env` da VPS; `.gitignore` cobre `.env`, `CONFIRM_LIVE`, `KILL` e os dados gravados
- Usuário não-root; `ufw` liberando só SSH, com a porta 8080 restrita ao meu IP
- Nenhum segredo aparece em log

---

## 10. O que NÃO fazer

- Não usar LLM no caminho de decisão
- Não otimizar micro-latência antes do M2 provar que existe edge
- Não implementar market making (modo maker) na v1; anotar como v2
- Não adicionar moeda além de BTC e ETH nem outra plataforma na v1

Comece agora pelo M0.
