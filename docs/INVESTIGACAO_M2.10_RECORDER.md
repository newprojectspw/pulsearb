# Investigação M2.10 item 7 — o log do recorder contradiz o dado?

**Resposta curta: o detector não mente. Ele e o arquivo medem populações
diferentes, por dois motivos independentes — e nenhum dos dois é bug de
medição.**

Nada em `src/pulsearb/recorder/` ou `src/pulsearb/feeds/` foi alterado nesta
investigação, conforme o escopo do ciclo. As correções propostas ficam
descritas ao fim, para um ciclo com a gravação parada.

## A evidência

- 20:12:30 UTC, log do recorder: `crypto_prices mudo ha 997.0s (limiar 15.0s)`,
  `total_por_silencio: 2482`, reassinando a cada 5 s.
- `pulsearb-20260822-2000.jsonl.gz`: 1.122 eventos de `crypto_prices` nas
  últimas 200 mil linhas, o mais recente em `ts_wall_ns
  1787429804545497534` = 20:16:44 UTC.

## Causa 1 — são DUAS conexões, e o arquivo é a fusão das duas

`settings.py:56` — `rtds_conexoes: int = 2`. O recorder abre duas conexões ao
**mesmo** endpoint do RTDS (redundância do M2.2 A.5) e grava a primeira
mensagem que chegar, deduplicando por (tópico, ativo, timestamp):

```python
self.rtds_feeds: list[RtdsFeed] = [
    RtdsFeed(..., on_event=self._fazer_callback_rtds(indice), ...)
    for indice in range(max(1, settings.feeds.rtds_conexoes))
]
```

Cada `RtdsFeed` tem o **seu próprio** `last_tick_by_key`, e portanto a sua
própria visão de "quem está mudo". O arquivo, não: ele é a união
deduplicada das duas.

**Consequência direta:** a conexão #0 pode estar muda há 997 s enquanto a #1
entrega normalmente. O log da #0 está certo; o arquivo está certo; a
contradição é aparente e nasce de comparar uma medição **por conexão** com
um arquivo **por recorder**.

## Causa 2 — o que é gravado e o que atualiza o relógio do detector são coisas diferentes

`feeds/base.py`, `_receive_loop`:

```python
await self._handle_message(event)   # popula last_tick_by_key — COM filtros
if self.on_event is not None:
    self.on_event(event)            # GRAVA NO DISCO — sem filtro nenhum
```

E `_handle_message` tem duas saídas antecipadas antes de tocar o relógio:

```python
tick = parse_rtds_event(...)
if tick is None:
    return                                   # tópico ou payload não reconhecido
if self.assets and tick.asset not in self.assets:
    return                                   # ativo fora da lista configurada
self.last_tick_by_key[(tick.topic, tick.asset)] = tick
```

O `subscribe` do RTDS é feito **sem filtro de símbolos** — decisão registrada
em `rtds.py`: *"receber todos e filtrar localmente é mais robusto a grafias de
símbolo divergentes"*. Então todo evento de `crypto_prices` de um símbolo fora
de `all_price_assets` **vai para o disco e nunca atualiza o detector**.

Os 1.122 eventos podem ser exatamente isso.

## Causa 3 (agravante) — o log não diz qual conexão, nem qual tópico

Dois detalhes que tornam o log mais ambíguo do que precisa ser:

1. **`rtds.py:142`** — `super().__init__(name="rtds", ...)`, fixo. O logger é
   `pulsearb.feeds.{name}`, então as duas conexões logam sob o **mesmo nome**,
   sem índice. Não há como saber, pelo log, qual das duas reclamou.
2. **`rtds.py:128`** — `TOPICOS_ASSINADOS = (TOPIC_BINANCE, TOPIC_TWAP_60)`, e
   `_reassinatura_urgente` retorna no **primeiro** tópico atrasado que
   encontra. `crypto_prices` vem primeiro na tupla, então uma mensagem sobre
   `crypto_prices` **não** significa que o `twap_sixty` estava são — ele pode
   estar mudo também, e o log nunca chega a mencioná-lo.

## O que isso muda no diagnóstico

**Os 2.482 alarmes NÃO são prova de que a defesa do M2.7 falhou.** Eles são
compatíveis com uma conexão muda enquanto a outra carregava o dado — cenário
em que a defesa fez o que devia na conexão viva e o arquivo nunca sofreu.

O que os alarmes **de fato** mostram é outra coisa, e continua valendo:
reassinar a cada 5 s por ~16 minutos não produziu recuperação visível naquela
conexão. Reassinar não revive conexão morta. Isso é a lacuna de escalada já
anotada como fora de escopo — derrubar o socket e reconectar depois de N
tentativas.

### E isto NÃO explica a hora das 16:00

Na hora das 16:00 os oito ativos emudeceram **no arquivo** — e o arquivo é a
fusão das duas conexões. Para o twap sumir do arquivo, as **duas** conexões
precisaram parar de entregar dentro do mesmo segundo.

Dois sockets independentes, ao mesmo endpoint, parando junto, aponta para
**cima**: o publicador ou o caminho até ele. Não é assinatura por ativo (oito
não caducam no mesmo segundo) e não é uma conexão azarada. É o motivo de o
`suspeita_de_assinatura_caducada` ter passado a exigir ausência de silêncio de
conexão sobreposto — a inferência antiga apontava o conserto errado.

## Como decidir empiricamente, sem tocar no recorder

**1. Qual conexão estava muda.** O recorder já publica isso por conexão
(`recorder/__main__.py:819`):

```
"entregou_primeiro": rtds_primeiro_por_conexao[indice]
```

Se uma das conexões estiver com `entregou_primeiro` perto de zero no período,
a Causa 1 está confirmada.

**2. Se os 1.122 eventos são de ativos configurados.** No arquivo da hora:

```bash
zcat pulsearb-20260822-2000.jsonl.gz \
  | jq -r 'select(.fonte=="rtds" and .payload.topic=="crypto_prices")
           | .payload.payload.symbol' \
  | sort | uniq -c | sort -rn | head -30
```

Se os símbolos forem majoritariamente de fora de `all_price_assets`, a Causa 2
está confirmada e o detector estava certo o tempo todo.

**3. Se o twap também estava mudo naquele momento.** Como o log só nomeia o
primeiro tópico atrasado, o arquivo é a única fonte:

```bash
zcat pulsearb-20260822-2000.jsonl.gz \
  | jq -r 'select(.fonte=="rtds")
           | [(.ts_wall_ns/1000000000|floor), .payload.topic] | @tsv' \
  | awk '$1 >= 1787428800 && $1 <= 1787429804' \
  | sort | uniq -c
```

## Consertos propostos — para um ciclo com a gravação PARADA

Nenhum destes foi aplicado. Todos são em `feeds/` ou `recorder/`.

| # | Conserto | Por quê |
|---|---|---|
| A | `RtdsFeed` recebe um índice e loga como `rtds[0]` / `rtds[1]` | Sem isso não há como atribuir um alarme a uma conexão, e toda investigação recomeça do zero |
| B | `_reassinatura_urgente` reportar **todos** os tópicos atrasados, não o primeiro | Hoje um alarme sobre `crypto_prices` esconde que o `twap_sixty` também estava mudo |
| C | Escalada: após N reassinaturas sem tick novo, derrubar o socket e reconectar | Reassinar não revive conexão morta — 2.482 tentativas em ~3,4 h são a prova |
| D | Contar e reportar os eventos **descartados** por `parse_rtds_event`/filtro de ativo | Hoje eles somem entre o fio e o relógio do detector, e é isso que faz o log parecer mentira |

Fora de escopo, já anotado: `idade_por_topico` (`feeds/rtds.py:205`) reduz os
ativos do tópico pelo **menor** tempo — um ativo vivo mascara sete mortos.

> **Atenção:** o conserto do `idade_por_topico` **já está no `main`** (PR #18,
> commit `a266425`). Ele não afeta a gravação em curso, porque merge não é
> deploy — mas **não faça `git pull` + restart do recorder antes das 72 h
> fecharem**.
