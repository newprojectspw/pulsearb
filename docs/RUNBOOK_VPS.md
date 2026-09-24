# Runbook — recorder na VPS de Londres

Do zero até uma gravação de 72h rodando. Testável por quem nunca viu o
projeto.

Este runbook cobre **gravação e SHADOW**. A §8.1 prepara a carteira, mas o
host LIVE precisa passar antes pelo passo 0 dela: esta VPS é recusada por
região pelo CLOB.

**Região: Londres — e esta máquina serve para GRAVAR e para SHADOW, não
para LIVE.** `[MEDIDO 2026-09-18]` O CLOB recusa ordens vindas daqui com
`403 Trading restricted in your region` (API_NOTES §17). A recusa é anterior
a qualquer validação do corpo da ordem: não é defeito a consertar com código,
nem credencial, nem allowance. Enquanto o host LIVE for este, **não fundeie a
carteira** — ver §8.1, passo 0.

Para o que esta máquina faz — gravar e rodar SHADOW — Londres continua boa, e
a escolha é de baixo impacto: a cadência medida do feed (p50 ~0,9s, API_NOTES
13.1) torna a latência de rede praticamente irrelevante para esta estratégia.
Se o backtest mostrar sensibilidade real a latência, revisita-se; enquanto não
mostrar, é ruído.

---

## 1. Droplet

Qualquer VPS pequena serve. O recorder é I/O de rede e escrita sequencial:

| Recurso | Mínimo | Por quê |
|---|---|---|
| vCPU | 1 | o processo passa a vida esperando socket |
| RAM | 1 GB | fila assíncrona + buffers de WS |
| Disco | **80 GB** (mín. 50 GB com descarga periódica) | ~470 MB/h comprimido (ver §6); 72h ≈ 34 GB |
| Região | Londres | grava e roda SHADOW; **não** opera LIVE — ver acima |

Ubuntu 24.04 LTS. Ao criar, adicione sua chave SSH.

## 2. Usuário e firewall

```bash
ssh root@SEU_IP

adduser --disabled-password --gecos "" pulsearb
mkdir -p /home/pulsearb/.ssh
cp ~/.ssh/authorized_keys /home/pulsearb/.ssh/
chown -R pulsearb:pulsearb /home/pulsearb/.ssh
chmod 700 /home/pulsearb/.ssh && chmod 600 /home/pulsearb/.ssh/authorized_keys

ufw allow OpenSSH
ufw --force enable
```

O recorder **não abre porta nenhuma** — só faz conexões de saída. O dashboard
(porta 8080) é do M5 e não roda aqui; quando rodar, restrinja ao seu IP.

## 3. Deploy key e clone

Na sua máquina:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/pulsearb_deploy -N "" -C "pulsearb-vps"
cat ~/.ssh/pulsearb_deploy.pub
```

Cole a chave pública em **Settings → Deploy keys** do repositório, **sem**
permissão de escrita (a VPS só precisa ler).

Copie a chave privada para a VPS e clone:

```bash
scp ~/.ssh/pulsearb_deploy pulsearb@SEU_IP:~/.ssh/id_ed25519
ssh pulsearb@SEU_IP 'chmod 600 ~/.ssh/id_ed25519'

ssh pulsearb@SEU_IP
sudo mkdir -p /opt/pulsearb && sudo chown pulsearb:pulsearb /opt/pulsearb
git clone git@github.com:newprojectspw/pulsearb.git /opt/pulsearb
```

## 4. Instalação

```bash
sudo apt update && sudo apt install -y python3.12 python3.12-venv
cd /opt/pulsearb
python3.12 -m venv .venv
.venv/bin/pip install -e .
mkdir -p data/recordings
```

### 4.1. Relógio — NÃO pule

O modelo endgame depende de `seconds_left`. Numa janela em que os últimos 60
segundos decidem, **2 segundos de deriva de relógio erram em 3% a fração de
TWAP já travada** — e esse erro entra no backtest como se fosse sinal, sem
nada avisar. Deriva de relógio é silenciosa por natureza: o `date` continua
mostrando uma hora plausível.

```bash
sudo apt install -y chrony
sudo systemctl enable --now chrony

# a fonte tem que estar sincronizada, e o offset em MILISSEGUNDOS
chronyc tracking | grep -E "Reference ID|System time|Leap status"
chronyc sources -v | head -20
```

O que precisa estar verdadeiro:

| Campo | Valor aceitável |
|---|---|
| `Leap status` | `Normal` |
| `System time` | offset **< 50 ms** do relógio de referência |
| `chronyc sources` | pelo menos uma fonte com `^*` (a selecionada) |

Se `Leap status` for `Not synchronised`, espere alguns minutos e repita. Se
não sincronizar, o provedor pode estar bloqueando NTP na saída (UDP 123) —
resolva **antes** de gravar 72h, não depois.

O recorder mede isso continuamente por conta própria: o relatório traz
`integridade.offset_relogio_ms` (p50/p99), que é a diferença entre a chegada
local e o carimbo do servidor. Ele inclui latência de rede, então é **teto**
do erro de relógio, não o erro em si — mas se o p50 crescer ao longo da
gravação, é deriva, e não latência.

Antes de deixar rodando, confirme que a VPS **enxerga** os endpoints:

```bash
python3 scripts/smoke_feeds.py --auto-discover --seconds 60
python3 scripts/smoke_discovery.py
```

Se `smoke_discovery` mostrar 0 janelas, pare aqui: ou o padrão de slug mudou,
ou a VPS está bloqueada por região. Não adianta gravar 72h de nada.

## 5. systemd

```bash
sudo cp /opt/pulsearb/deploy/pulsearb-recorder.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pulsearb-recorder

systemctl status pulsearb-recorder
journalctl -u pulsearb-recorder -f
```

O que olhar no log (uma linha JSON por evento):

- `"msg":"descoberta"` a cada 30s — `janelas`, `operaveis`, `novas`,
  `encerradas`, `assinadas`. **`assinadas` deve ficar estável**, não crescer
  sem parar: se crescer, a rotação de assinatura quebrou.
- `"msg":"lacuna na gravação"` — cada lacuna com duração e causa. Algumas por
  dia é normal; muitas ou longas significam problema de rede.
- `descartadas` > 0 no relatório — disco lento demais para a fila.

### 5.1. Verificação pós-start — NÃO pule

Subir sem erro **não** significa que está gravando. Um feed pode conectar,
ser recusado no protocolo e reconectar em loop indefinidamente, com o
`systemctl status` mostrando `active (running)` o tempo todo. Foi exatamente
isso que aconteceu no primeiro deploy real: o RTDS fechava a conexão com
`1003 unsupported data` a cada tentativa, e o serviço parecia saudável.

Espere **60 segundos** depois do start e rode:

```bash
# 1. Quantas vezes cada feed caiu no último minuto?
journalctl -u pulsearb-recorder --since "60 seconds ago" \
  | grep -c "conexão caiu"
```

**O número tem que ser 0 ou 1.** Qualquer coisa acima disso é loop de
reconexão — pare e investigue antes de deixar rodando 72h.

```bash
# 2. Os três feeds estão recebendo mensagem?
journalctl -u pulsearb-recorder --since "60 seconds ago" \
  | grep '"msg":"descoberta"' | tail -1 | python3 -m json.tool
```

Espere ver, no último ciclo:

| Campo | O que significa | Valor saudável após 60s |
|---|---|---|
| `msgs_rtds` | ticks de preço (TWAP + spot) | **centenas** — a cadência é ~1/s por ativo |
| `msgs_binance` | bookTicker + kline_1h | **dezenas ou mais** |
| `msgs_poly` | book do CLOB | **> 0** assim que houver janelas assinadas |
| `janelas` / `operaveis` | descoberta funcionando | dezenas, e `operaveis` próximo de `janelas` |
| `assinadas` | tokens no WS do CLOB | 2× o número de janelas |
| `descartadas` | eventos perdidos por disco lento | **0** |

**Qualquer `msgs_*` em 0 depois de 60s é falha**, mesmo sem erro no log.
Um feed que conecta e não recebe nada é indistinguível de um feed morto para
efeito de gravação.

```bash
# 3. O arquivo está crescendo?
ls -la /opt/pulsearb/data/recordings/
sleep 60 && ls -la /opt/pulsearb/data/recordings/
```

O `.jsonl.gz` corrente tem que estar maior na segunda listagem.

```bash
# 4. O livro que estamos reconstruindo bate com o que o servidor afirma?
journalctl -u pulsearb-recorder --since "60 seconds ago" \
  | grep '"msg":"descoberta"' | tail -1 | python3 -m json.tool \
  | grep -E "divergencias|resyncs|descartadas_book|offset_relogio"
```

| Campo | Valor saudável | O que significa se estourar |
|---|---|---|
| `descartadas_book` | **0**, sempre | a fila SEM PERDA transbordou: delta de livro se perdeu. Disco ou CPU insuficientes. |
| `divergencias` | pode ser ALTO e estável | é a contagem de TODAS as divergências alinhadas, e a maioria é a corrida de um tick entre `best_bid_ask` e `price_change` — NORMAL (M2.5). Sozinho não condena a gravação; o que condena é `divergencias_persistentes` e `tokens_corrompidos`. |
| `resyncs` | **poucos e explicáveis** | desde 2026-09-24 o resync NÃO dispara mais a cada divergência: só divergência MATERIAL (> 2 ticks, delta perdido) ou de um tick que PERSISTE (> 250 ms, ≥ 2 obs). Milhares de resyncs = a fonte real do problema, não corrida. Veja `integridade.divergencia_topo_book.politica_de_resync` no relatório: `por_divergencia_material`, `por_persistencia`, `transientes_ignoradas`. |
| `offset_relogio_p50_ms` | estável, dezenas de ms | crescendo ao longo da gravação = NTP quebrado (§4.1). |

**O sinal que condena a gravação NÃO é `divergencias` alto** — foi esse o
engano que invalidou a gravação de 2026-09-24, com 542 mil resyncs disparados
por corridas de um tick. O que condena é
`integridade.divergencia_topo_book.divergencias_persistentes` > 0 e
`tokens_corrompidos` não vazio no relatório final. Confira também
`formas_de_price_change` (tem de ser `price_changes`) e o bloco
`politica_de_resync` (as corridas têm de aparecer em `transientes_ignoradas`,
não em resyncs).

Só depois desses quatro checks a gravação está de fato iniciada.

### 5.2. A tempestade de resyncs de um tick — causa raiz e conserto (2026-09-24)

**Sintoma medido na VPS (2 vCPU / 4 GB, ~1 h, 76 janelas):** 6,39 M msgs
poly, 392 mil divergências, **542 mil disparos de resync**, 9.488 resyncs
efetivos, ~1,4 M ms de token `apos_perda`, 24 `slow consumer`, ~2 GB/h. A
gravação foi invalidada.

**Causa raiz.** O `MonitorDeIntegridade` (M2.5) já classificava a QUALIDADE do
livro por conjunção (magnitude > 2 ticks **e** persistência > 250 ms **e**
fração de tempo), mas o RECORDER resincronizava a cada divergência devolvida
por `observar()` — **sem nenhum desses limiares**. A corrida de um tick entre
`best_bid_ask` e `price_change` (que o M2.5 documenta como NORMAL) virava
resync destrutivo: cada resync `marca_perda` (livro descartado → tempo
`apos_perda`) e reassina (unsub+sub → enxurrada de snapshots de book →
mais tráfego → `slow consumer` → mais quedas → mais divergências). Um laço que
se realimentava.

**Comportamento anterior:** `for divergencia in observar(...): a_resincronizar.add(...)`.

**Comportamento corrigido:** o monitor decide o resync por uma política
explícita (`_politica_de_resync`), e o recorder drena com `consumir_resync()`:

- só a comparação **alinhada por carimbo** pede resync (a por chegada mede a
  nossa fila, não corrupção);
- divergência **material** (> 2 ticks, na mesma mensagem = delta perdido) →
  resync imediato (motivo `divergencia_material`);
- divergência de **um tick** → telemetria (`transientes_ignoradas`), sem
  resync — a menos que **persista** (≥ 2 observações E > 250 ms), aí resync
  com motivo `divergencia_persistente`;
- perda de fila (`fila_cheia`), snapshot ausente após perda e corrupção
  persistente continuam forçando resync — a falha fechada não afrouxou.

Nenhuma tolerância foi aumentada e nenhuma divergência é escondida: o número
de divergências pode continuar alto (são as corridas), mas agora elas não
viram resync.

### 5.3. Hora de teste — critérios de aceite (rode ANTES das 72 h)

```bash
# uma hora de gravação (o preflight de disco não barra rodada curta)
python -m pulsearb.recorder --duration 1h
# leia o relatório final:
journalctl -u pulsearb-recorder --since "70 min ago" \
  | grep '"msg":"recorder encerrado"' | tail -1 | python3 -m json.tool
```

A hora PASSA quando, no relatório final:

| Critério | Onde | Aceite |
|---|---|---|
| descarte de livro | `descartadas_por_canal.book` | **0** |
| resyncs | `integridade.resyncs` | poucos e explicáveis (dezenas, não milhares) |
| corridas viram telemetria | `integridade...politica_de_resync.transientes_ignoradas` | **> 0** (as corridas foram vistas e NÃO resincronizaram) |
| divergências persistentes | `integridade...criterio_de_invalidacao.divergencias_persistentes` | **0** |
| tokens corrompidos | `integridade...tokens_corrompidos` | **[]** |
| forma do price_change | `integridade...formas_de_price_change` | `{"price_changes": N}` |
| slow consumer recorrente | `quedas_por_feed.poly_ws` | sem `1013` repetido |
| offset do relógio | `integridade.offset_relogio_ms.p50_ms` | estável, dezenas de ms |
| projeção de disco | `armazenamento.bytes_por_hora_medido` | projeção 72 h cabe no disco |
| relatório | linha `recorder encerrado` | presente e completo |

### 5.4. Escopo do recorder (opcional) e preflight de armazenamento

**Escopo.** Se a descoberta trouxer janelas demais para a banda/CPU da
máquina, limite o número de tokens assinados no `config.yaml`:

```yaml
recorder:
  max_tokens_assinados: 40   # par >= 2; 20 janelas inteiras; null = tudo
```

O corte é determinístico (por slug), mantém janelas inteiras (Up+Down) e
**aparece no snapshot de descoberta** (bloco `escopo`:
`janelas_descobertas`, `janelas_no_escopo`, `janelas_cortadas`). Cobertura
nunca cai em silêncio.

**Preflight de disco (req 12).** Antes de gravar, o recorder projeta o espaço
e **recusa iniciar** se o disco não comporta:

```yaml
recorder:
  bytes_por_hora_estimados: 2000000000   # 2 GB/h — calibre pela taxa MEDIDA
  margem_de_disco: 1.2
```

Uma rodada de 72 h com disco insuficiente sai com código 1 e a linha de log
`preflight de armazenamento RECUSOU a gravação`. Rodada curta (< 6 h) não é
barrada. Depois de uma hora de teste, use
`armazenamento.bytes_por_hora_medido` do relatório para calibrar
`bytes_por_hora_estimados`.

### 5.5. Critérios objetivos para aceitar uma gravação de 72 h

Tudo da §5.3 (medido sobre as 72 h), MAIS: as duas metades do 0.8 (saúde do
feed **e** dano às medidas — ver `docs/ESTADO_PARA_LIVE.md` item 0.8), maior
silêncio ≤ 60 s, e `armazenamento` projetado que de fato coube (a rodada não
morreu por disco). `divergencias_persistentes` = 0 e `tokens_corrompidos` = []
sobre as 72 h inteiras — não numa hora boa.

Alternativa com Docker:

```bash
docker build -f deploy/Dockerfile -t pulsearb-recorder .

# O chown NÃO é opcional. O container roda como `pulsearb` (UID 10001,
# fixado no Dockerfile); num bind mount quem manda é o dono do lado do
# HOST, e um diretório criado por root deixa o recorder sem escrita.
sudo mkdir -p /opt/pulsearb/data
sudo chown 10001:10001 /opt/pulsearb/data

docker run -d --restart=always --name pulsearb-recorder \
  -v /opt/pulsearb/data:/data pulsearb-recorder --duration 72h
```

**Como esse erro se apresenta, se o `chown` for pulado:** o container sobe,
falha na primeira escrita com `PermissionError`, e o `--restart=always` o
reinicia em laço. O `docker ps` mostra o container "rodando" — reiniciando é
um estado que se parece com rodando à distância — e nenhum `.jsonl.gz`
aparece. Confira sempre com:

```bash
docker logs --tail 30 pulsearb-recorder
ls -la /opt/pulsearb/data          # tem que ter arquivo CRESCENDO
```

*(Escrito em 2026-08-31 a partir da leitura do Dockerfile, **sem build de
verificação** — não há Docker na máquina de análise. O item 5.2 segue 🟡 por
isso: o primeiro build ainda vai acontecer, e é nele que isto se confirma ou
se desmente.)*

## 6. Uso de disco — MEDIDO em produção

| | Estimativa original | **Real (medido 2026-08-18)** |
|---|---|---|
| Comprimido | ~5 MB/h | **~470 MB/h** (média de 24 h; **699 MiB/h medidos**
  numa hora de tarde, 2026-09-18 — dimensione pelo pico, não pela média) |
| Por dia | ~0,12 GB | **~11 GB** |
| **72h** | ~0,35 GB | **~34 GB** |
| Semana | ~0,82 GB | ~77 GB |

**A estimativa original estava errada por quase 100x.** Ela modelava ~30
snapshots de book por token por hora. Na prática o livro do CLOB atualiza a
cada poucos segundos em 150+ tokens simultâneos, e **uma única janela ativa
gera mais de 300 eventos `price_change` por segundo**. São esses eventos que
dominam o volume — não os ticks de preço, não os snapshots de descoberta.

Consequência prática, e é séria: com disco de 10 GB — o que este runbook
recomendava — a gravação **morre por disco cheio em ~21 horas**, no meio das
72h, sem completar.

### A VPS ATUAL não comporta 72 h — medido em 2026-09-09

O droplet de Londres (`46.101.73.186`, 1 vCPU / 1 GB) tem **23,17 GB de disco
total**, com ~4,75 GB em uso — restam **~18,4 GB**.

Pela regra de bolso acima, 18 GB compram **~36 horas**. Uma gravação de 72 h
lançada nesta máquina morre por disco cheio **no meio**, sem completar, que é
exatamente o modo de falha que a tabela acima existe para evitar.

Antes de subir o serviço aqui, uma das três:

1. **volume extra** (§6, "Descarga periódica ou volume extra") — 80 GB para
   72 h sem tocar na máquina;
2. **redimensionar o droplet**;
3. **descarga a cada ~12 h** com `scripts/purge_recordings.sh`, que ainda
   exige mais que os 23 GB atuais.

**Decisão de 2026-09-09:** a gravação de 72 h daquele momento rodou no **Mac**
(98 GB livres, em container Docker), e não aqui. A VPS foi atualizada e ficou
pronta, mas sem serviço ativo — o disco é o que falta, não o software.

### Quanto disco pedir

| Objetivo | Disco | Precisa de descarga durante a gravação? |
|---|---|---|
| 72h sem tocar na máquina | **80 GB** | não |
| 72h com descarga a cada ~12h | **50 GB** | sim, ver abaixo |
| 24h de teste | 20 GB | não |

Regra de bolso: **cada 1 GB livre compra ~2h de gravação.**

Confira na primeira hora, não no fim:

```bash
df -h /opt/pulsearb/data
du -sh /opt/pulsearb/data/recordings
```

Do Mac, sem abrir sessão na VPS, o mesmo diagnóstico mais o estado do código
e do serviço saem de uma vez:

```bash
./scripts/estado_vps.sh root@SEU_IP
```

Guardando o host uma vez (`echo root@SEU_IP > ~/.pulsearb-vps`), passa a ser
só `./scripts/estado_vps.sh`. Item que não deu para apurar aparece como
`DESCONHECIDO` e o script sai com código 4 — nunca como "ok".

Se a primeira hora fechada não estiver na casa das centenas de MB, algo está
errado — provavelmente um feed calado (§5.1).

### O `disk-guard.sh` PARA o recorder, e ninguém avisa `[MEDIDO 2026-09-18]`

Existe na VPS um script que **não está neste repositório** e que nenhuma
secção mencionava até hoje:

```bash
# /usr/local/bin/disk-guard.sh, no cron do root: */10 * * * *
USO=$(df / | awk 'NR==2{print $5}' | tr -d '%')
if [ "$USO" -ge 85 ]; then
  systemctl stop pulsearb-recorder
  echo "$(date) disco em ${USO}% — recorder parado" >> /var/log/disk-guard.log
fi
```

**O que ele acerta:** escolheu PARAR em vez de apagar. Nenhuma gravação foi
destruída por ele — os 15 GB de 09/09 a 11/09 estavam inteiros quando a
máquina foi inspeccionada em 18/09. Para um guarda de disco, essa é a
escolha certa: dado de medição não se apaga para caber mais dado.

**O que ele erra, e custou uma semana:** ele é MUDO. `systemctl stop` não é
falha, então `Restart=always` não reergue; a única marca fica numa linha de
`/var/log/disk-guard.log` que ninguém lê; e nenhuma das verificações deste
runbook olhava para lá. Entre 11 e 18/09/2026 o recorder ficou parado sem
nada denunciar. O log confirma, linha por linha:

```
Fri Sep 11 05:50:01 UTC 2026 disco em 85% — recorder parado
...
Fri Sep 18 15:30:01 UTC 2026 disco em 85% — recorder parado
```

`05:50:01` é o segundo em que o journal do systemd registou `Stopping`. E ele
não parou de disparar: uma linha a cada 10 minutos durante a semana toda,
porque o disco nunca desceu dos 85 %. Há dois episódios anteriores no mesmo
log, 20–21/08 e 25/08.

**E ele truncou gravação, mesmo sem apagar nada.** Matar o processo no meio
da hora deixa o `.jsonl.gz` daquela hora sem o fim. O guarda disparou em
**25/08 05:40** e o `pulsearb-20260825-0500.jsonl.gz` falha no `gzip -t` —
é a hora que estava a ser escrita. Não apagar não é o mesmo que não
estragar: a hora interrompida vai-se de qualquer maneira, e é por isso que
libertar disco ANTES vale mais do que confiar no guarda.

**Três arquivos estão perdidos por isto, e a perda é definitiva**
`[MEDIDO 2026-09-18]`: `pulsearb-20260823-0100.jsonl.gz`, o
`-002` da mesma hora e o `pulsearb-20260825-0500.jsonl.gz` falham no
`gzip -t` **na VPS**, não só na cópia. Foi por causa deles que a verificação
de integridade do §7 deixou de ser opcional: eles falharam primeiro do lado
do Mac, o que parecia transferência interrompida, e só o teste na origem
mostrou que não havia de onde recuperar.

**O que isso obriga antes de qualquer `systemctl start`:**

```bash
df -h /                      # abaixo de 85 %, ou o guarda derruba em ≤10 min
tail -5 /var/log/disk-guard.log
```

Subir o recorder com o disco em 85 % não dá gravação curta: dá gravação de
até dez minutos, repetida, cada uma com o seu arquivo. Libere disco (§7 e as
duas saídas abaixo) **primeiro**.

### Descarga periódica (disco menor) ou volume extra

Duas saídas quando o disco é o limite. Escolha uma **antes** de começar as
72h, não no meio.

**a) Descarga periódica.** Baixe e apague as horas já transferidas conforme
avança, em vez de esperar o fim.

**Primeiro, dê um apelido à VPS — uma vez só.** Todo comando daqui em diante
usa o apelido, e assim nenhum bloco para colar contém um `SEU_IP` que alguém
cola literal. (Aconteceu três vezes em 2026-09-18, e a terceira produziu o
falso verde descrito abaixo.)

```bash
cat >> ~/.ssh/config <<'EOF'

Host pulsearb-vps
  HostName 203.0.113.10        # <- o IP REAL da VPS, editado uma vez
  User root
EOF

ssh pulsearb-vps true && echo "a VPS responde"
```

**Depois**, na máquina de análise (o Mac), a cada ~12 h enquanto a gravação
longa correr:

```bash
cd ~/pulsearb-code
./scripts/descarga_periodica.sh pulsearb-vps ~/pulsearb-dados
```

Ele compõe `fetch_recordings.sh` (baixa e reprova se algum `gzip -t` falhar)
com `purge_recordings.sh` (refaz as conferências e só apaga o que passou nas
três: **existe aqui**, **o tamanho bate com o da VPS**, **o gzip abre**). As
três travas estão travadas por teste desde 2026-09-18
(`tests/test_purga_de_gravacoes.py`), cada uma verificada por mutação:
desligar qualquer uma derruba um teste que apaga gravação que não devia.

Ele baixa **ontem e hoje**, não só hoje, porque uma rodada depois da
meia-noite UTC deixaria as horas do dia anterior para trás — e o purge não
apaga o que não chegou, então elas ficariam na VPS para sempre.

**A receita que estava aqui até 2026-09-18 apagava gravação boa e ruim por
igual**, e fica registada porque o modo de falha é instrutivo:

```bash
# ERRADO — não use. O `rm` abaixo corre mesmo que o `gzip -t` acima reprove.
for f in ~/pulsearb-dados/pulsearb-*.jsonl.gz; do gzip -t "$f" || echo "RUIM: $f"; done
ssh root@SEU_IP 'ls -t .../*.jsonl.gz | tail -n +2 | xargs rm -f'
```

O `gzip -t` **imprimia** `RUIM` e seguia; o `ssh ... rm` apagava por posição
na lista, não por integridade. Um arquivo que baixou pela metade tem o nome
certo e some do mesmo jeito. Foi assim que se perderam três horas de agosto
(§7).

**O que pode fazer esta rotina falhar em silêncio, e como ver:** o Mac
dormindo na hora do agendamento. Registe a saída e confira que ela tem uma
entrada nova a cada 12 h:

```bash
# cada linha do crontab do Mac, com log:
# 0 */12 * * * cd ~/pulsearb-code && ./scripts/descarga_periodica.sh \
#   pulsearb-vps ~/pulsearb-dados >> ~/descarga.log 2>&1

tail -20 ~/descarga.log            # tem de terminar com '=== fim ... ==='
grep -c '=== fim' ~/descarga.log   # uma por rodada bem sucedida
```

**`=== fim ===` só sai quando a rotina de facto falou com a VPS, e isso
precisou de conserto.** Na primeira versão, um host que não resolvia fazia o
`rsync` falhar, o tratamento dizia *"sem arquivos deste dia"*, o purge não
achava nada e a rodada terminava imprimindo `=== fim ===` — a corrida inteira
falhava e o log dizia que tinha corrido, no mesmo marcador que esta secção
manda conferir. Hoje há duas travas:

- `descarga_periodica.sh` sonda o host (`ssh -o BatchMode=yes … true`) **antes
  de tudo** e sai com 2 sem imprimir `=== fim ===` se não houver resposta;
- `purge_recordings.sh` distingue *«a VPS não tem gravação»* de *«não falei
  com a VPS»*, e no segundo caso sai com 2 sem apagar nada.

As duas estão travadas por teste (`tests/test_purga_de_gravacoes.py`,
verificado por mutação). Não saber é motivo de recusa, nunca de seguir.

Se o Mac dorme, ou `sudo pmset -a sleep 0` durante a gravação, ou
`caffeinate -i` numa aba aberta. Uma descarga que não corre leva o disco aos
85 % e o `disk-guard` para o recorder — calado, como em 11/09.

**b) Volume extra.** Se preferir não depender de rotina manual, anexe um
volume e aponte o recorder para ele — o caminho de saída é configurável por
variável de ambiente, e o override de ambiente vence o `config.yaml`:

```bash
# na Digital Ocean: Volumes → Create, depois
sudo mkdir -p /mnt/pulsearb-dados
sudo mount /dev/disk/by-id/scsi-0DO_Volume_pulsearb /mnt/pulsearb-dados
sudo chown pulsearb:pulsearb /mnt/pulsearb-dados
echo '/dev/disk/by-id/scsi-0DO_Volume_pulsearb /mnt/pulsearb-dados ext4 defaults,nofail,discard 0 0' \
  | sudo tee -a /etc/fstab
```

E no service (`deploy/pulsearb-recorder.service`), acrescente o override e
libere o caminho no sandbox — sem as duas linhas o systemd falha com
`226/NAMESPACE`:

```ini
Environment=PULSEARB_RECORDER__OUTPUT_DIR=/mnt/pulsearb-dados/recordings
ReadWritePaths=/mnt/pulsearb-dados
```

Depois `sudo systemctl daemon-reload && sudo systemctl restart pulsearb-recorder`
e confirme pela §5.1 que o arquivo está crescendo **no caminho novo**.

## 7. Coletar as gravações

Da máquina de análise:

```bash
./scripts/fetch_recordings.sh pulsearb@SEU_IP                 # hoje
./scripts/fetch_recordings.sh pulsearb@SEU_IP 2026-08-17      # um dia
```

O script verifica a integridade de cada gzip e imprime a contagem de linhas.

Sem o repositório clonado na máquina de análise, o `rsync` direto faz o mesmo
(as aspas são necessárias: sem elas o zsh tenta expandir o `*` localmente):

```bash
rsync -avz --partial --progress \
  'root@SEU_IP:/opt/pulsearb/data/recordings/pulsearb-20260818-*.jsonl.gz' \
  ~/pulsearb-dados/
```

Use **rsync, não scp**: com arquivos de ~470 MB num link doméstico a
transferência cai, e o `scp` recomeça do zero enquanto o `rsync --partial`
retoma de onde parou.

Depois:

```bash
python -m pulsearb.backtest data/recordings --json relatorio.json
```

A memória do backtest é **limitada por construção** desde o M2.1: o leitor é
streaming e o indexador retém no máximo `--limite-snapshots` (1.500) snapshots
de book por token, só dos tokens que pertencem a alguma janela conhecida e só
dentro do intervalo da janela. Antes disso, um único arquivo de 450 MB matava
o processo com `Killed` numa máquina de 1 GB.

O orçamento é calculável antes de rodar:

```
memória ≈ tokens_simultâneos × --limite-snapshots × --niveis-book × 270 B
        ≈ 150 × 1500 × 5 × 270 B ≈ 300 MB
```

Medido: 2 milhões de eventos `price_change` sobre 40 tokens → **81 MB de
pico** (50 mil snapshots retidos, 1,95 milhão descartados), contra o `Killed` da versão anterior. O preço é uma segunda passada
sobre o arquivo (a primeira só lê metadados, e é ela que descobre quais tokens
importam) e a truncagem dos books aos `--niveis-book` do topo.

### Antes de QUALQUER análise: o tamanho de cada hora

`gzip -t` prova que o arquivo não está corrompido. Não prova que ele tem
dado dentro — um arquivo de 255 bytes passa no teste e lê, no backtest, como
uma hora de mercado sem nenhum evento. É ausência de medida com a cara de
medida de ausência, que é a confusão que este projeto persegue em todo lado.

Uma hora saudável desta gravação pesa ~300 MB (§6). Liste as que destoam
antes de concluir seja o que for:

```bash
ls -l ~/pulsearb-dados/pulsearb-*.jsonl.gz \
  | awk '{n=$NF; sub(/.*\//,"",n);
          if ($5 < 52428800) printf "%8.2f MB  %s\n", $5/1048576, n}'
```

**E nome igual dos dois lados NÃO é cópia boa.** Antes de apagar da VPS,
compare os nomes E teste a cópia — a comparação de nomes passou em
2026-09-18 com os 38 arquivos da VPS presentes no Mac, e **três deles
estavam corrompidos do lado do Mac**. Teste com uma trava que impeça o
falso verde do lado vazio:

```bash
# o lado da VPS TEM de ter linhas; zero linhas faz o `comm` passar por engano
ssh <host> 'ls /opt/pulsearb/data/recordings/' | sort > /tmp/vps.txt
wc -l /tmp/vps.txt                       # se der 0, a comparação NÃO rodou
ls ~/pulsearb-dados/ | grep '\.jsonl\.gz$' | sort -u > /tmp/mac.txt
comm -23 /tmp/vps.txt /tmp/mac.txt       # vazio = todos os nomes chegaram
while read -r f; do
  gzip -t ~/pulsearb-dados/"$f" 2>/dev/null || echo "RUIM: $f"
done < /tmp/vps.txt                      # vazio = todas as cópias prestam
```

**Medido em 2026-09-18 sobre a gravação de 11 a 15/09:** 12 horas abaixo de
50 MB, em dois blocos (12/09 11:00–16:00 e 14/09 13:00 + 20:00–23:00 +
15/09 00:00), **duas delas com 255 bytes** — o arquivo criado e nada
escrito. O último arquivo da série está truncado, que é o que fica quando o
processo morre a escrever. Essas horas não entram em medida nenhuma sem
serem contadas como lacuna.

Precedente, e é por isso que esta secção existe: em 18/08 dois arquivos
saíram corrompidos e o dia inteiro deu **732 janelas conhecidas e ZERO com
resolução**. Ninguém sabia até alguém olhar (quadro 4.1).

### O relatório já conta os arquivos ilegíveis — leia esse campo

`reader.arquivos_ilegiveis` conta todo arquivo que o leitor não conseguiu
abrir, e o backtest escreve a contagem em `gravacao.arquivos_ilegiveis`.
Ele não sobe erro e não pára: conta e segue para o próximo. Isso é
deliberado — uma hora ilegível não pode matar a leitura das outras 71 —
mas significa que **um relatório com arquivo ilegível parece um relatório
normal** se ninguém olhar o campo.

```bash
for f in relatorios/DIA_*.json; do
  printf "%-42s " "$(basename "$f")"
  jq -c '{ilegiveis: (.gravacao.arquivos_ilegiveis|length),
          conhecidas: .gravacao.janelas_conhecidas,
          com_resolucao: .gravacao.janelas_com_resolucao}' "$f"
done
```

A **taxa de resolução** (`com_resolucao / conhecidas`) é o segundo olhar, e
apanha o estrago que a contagem sozinha não mede. Medido em 2026-09-18 sobre
os sete dias de agosto: os dias sãos ficam entre **0,888 e 0,916**, e o dia
19/08 deu **164 de 670 = 0,2448** — três em cada quatro janelas sem
resolução, com apenas UM arquivo ilegível. Um arquivo perdido não custa uma
hora: custa o que aquela hora encadeava.

O dia 18/08 é o caso extremo: 3 ilegíveis, 732 janelas conhecidas, **zero**
com resolução. Esse foi notado na altura; os outros nove arquivos
corrompidos da mesma pasta só apareceram um mês depois, numa varredura
`gzip -t` completa — e os relatórios diziam a contagem certa o tempo todo.

### Antes do backtest longo: converta para colunar

Sobre 72h de JSONL, cada cenário do backtest reparseia o arquivo inteiro. A
conversão colunar é feita uma vez e paga em todas as passadas seguintes:

```bash
pip install -e '.[analise]'        # extra opcional, só na máquina de análise
python -m pulsearb.replay.columnar ~/pulsearb-dados --out ~/pulsearb-parquet
```

Sai particionado por fonte e por dia (`fonte=poly_ws/dia=20260818/...`), o que
permite carregar só o que interessa:

```python
import pyarrow.parquet as pq
t = pq.read_table("~/pulsearb-parquet", columns=["ts_wall_ns", "asset_id", "best_ask"],
                  filters=[("fonte", "=", "poly_ws")])
```

O parquet é **derivado**: pode ser apagado e regerado do JSONL a qualquer
momento. Se os dois discordarem, o JSONL está certo.

### Na máquina de análise: solte os limites de memória

Os defaults do backtest são dimensionados para a VPS de 1 GB — e na máquina
de análise eles **sufocam a simulação**. A rodada real de 2026-08-19 descartou
42% dos snapshots e ficou com resolução efetiva de ~1,9s, o que torna o
cenário de latência de 300ms indistinguível.

Num Mac com 16 GB+, rode com:

```bash
python -m pulsearb.backtest ~/pulsearb-dados \
  --limite-por-token 20000 --niveis-por-lado 10 --json relatorio.json
```

Ou por ambiente, para não repetir em cada invocação:

```bash
export PULSEARB_BACKTEST_LIMITE_POR_TOKEN=20000
export PULSEARB_BACKTEST_NIVEIS_POR_LADO=10
```

Regra de bolso do orçamento (a mesma fórmula da seção anterior):
`tokens × limite × níveis × 270 B`. Com 150 tokens, 20.000 snapshots e 10
níveis ≈ **8 GB** — confortável num Mac, impossível na VPS. Depois de rodar,
confira `gravacao.memoria.pior_resolucao_ms`: com os limites soltos ele deve
ficar **abaixo de 150** e os quatro cenários de latência voltam a ser
distinguíveis.

### A varredura da âncora (M2.4) — em que horas rodar

A validação da âncora agora tem duas camadas: as hipóteses nomeadas
(referência) e a **varredura de τ** (`ancora.varredura_tau`), que testa
A(τ) = stream em `abertura + τ` para τ ∈ [−180s, +180s], em aritmética
inteira 1e18 e no relógio do servidor.

Regras de amostra:

- **Use as horas em que o recorder já estava vivo em TODA abertura** — da
  20h de 2026-08-19 em diante. Na hora de subida, as primeiras janelas
  abrem antes do stream existir e caem em `janelas_sem_cobertura_do_stream`.
- A amostra cresce **~26 janelas/hora**. O critério de sucesso do
  VEREDITO_M2 pede ≥ 100 janelas com cobertura: **~4 horas de gravação**.
- O que decide está em `ancora.varredura_tau`:

| Campo | Leitura |
|---|---|
| `regiao_viavel_100pct` | os τ que explicam TODAS as janelas. Vazio com amostra grande = a família A(τ) não é a âncora. |
| `final_media_60s` vs `final_stream_no_fechamento` | qual definição de TWAP final domina. Se a segunda vencer, a média-de-TWAP que o projeto usava era parte do erro. |
| `grade_tau_phi.melhor_celula` | a resposta quando o problema está no LADO DO FINAL. |
| `falhas_inexplicaveis` | janelas que NENHUM ponto do stream explica. Não-vazio e recorrente = fonte de liquidação fora do nosso stream = critério de falha da fundação (VEREDITO_M2). |
| `janelas_sem_cobertura_do_stream` | quanto da amostra foi descartado por lacuna — se for grande, olhe as quedas do RTDS antes de concluir qualquer coisa. |

### O que olhar no relatório

O relatório imprime o que foi retido e o que foi descartado em
`gravacao.memoria`. Olhe dois campos:

| Campo | O que fazer |
|---|---|
| `pior_resolucao_ms` > 150 | algum token estourou o teto e foi raleado; o cenário de latência de 150ms já não é distinguível dele. Suba `--limite-snapshots` se houver RAM. |
| `tokens_com_book` muito menor que `tokens_de_interesse` | a gravação não cobre as janelas que a descoberta conhecia — provavelmente feed do CLOB caindo (§5.1). |

E o bloco `integridade`, que decide se o resto do relatório vale alguma coisa:

| Campo | Leitura |
|---|---|
| `divergencia_topo_book` | a população que invalida é `com_magnitude_finita` MAIS o lado vazio não inocentado por nome em `lado_vazio.quais_invalidam`; acima de 1% das `comparacoes` invalida a conta do maker. A `taxa` agregada sozinha NÃO decide — ela soma truncagem de profundidade, que o §2c inocenta (emenda ao 1.9 no `VEREDITO_M2.md`) |
| `janelas_invalidadas` | janelas que saíram do backtest por livro furado — se for a maioria, o número agregado não significa nada |
| `formas_de_price_change` | qual formato o servidor usa de fato (API_NOTES 6.1b). É a resposta que a primeira gravação não deu. |
| `offset_relogio_ms.p50` | teto do erro de relógio; dezenas de ms é normal, segundos não |
| `janelas_por_qualidade` | o corte que importa (M2.5): `alta`/`media`/`baixa`/`sem_dado`. Se `baixa` for a maioria, olhe ANTES `alinhamento.*_fora_de_ordem` — livro embaralhado não é livro furado |
| `alinhamento.por_chegada_local` vs `por_carimbo_do_servidor` | se as duas contas baterem, a desordem local não era a causa das divergências. Se a primeira for muito maior, era |
| `lado_vazio.por_causa` | `esvaziado_por_delta` alto = suba `--niveis-book`, não é corrupção |

### 7.1. Gravação grande demais para uma passada só

72h dão ~24 GB. A passada 2 do backtest (reconstrução dos books) não cabe numa
máquina comum com o teto de snapshots que a análise exige — o relatório diz
quanto custaria em `gravacao.memoria.projecao_de_pico`:

```
teto por token × tokens de interesse × níveis × 2 lados
```

Com ~3.700 tokens em 72h e `--limite-por-token 20000`, são dezenas de GiB só
de book. Subir o teto não resolve; **fatiar resolve**, porque as janelas de
5m/15m vivem dentro de uma hora:

```bash
mkdir -p relatorios
for h in $(seq -w 0 23); do
  python -m pulsearb.backtest data/recordings \
    --desde 20260820$h --ate 20260820$h \
    --limite-por-token 20000 \
    --json relatorios/2026-08-20-$h.json
done
```

> **Onde o `--json` pode gravar.** O argumento é um caminho **relativo** a
> uma raiz confiável — o diretório de trabalho, por padrão. Caminho absoluto
> é recusado, e `..`, `~` e caractere de shell também: o nome é validado
> contra um padrão fixo ANTES de virar caminho, e só então montado a partir
> da raiz.
>
> Sufixo `.json` e diretório-pai existente não bastariam: `/etc/cron.d/x.json`
> passa nos dois. Para gravar em outro lugar, mude a RAIZ —
> `PULSEARB_BACKTEST_OUTPUT_ROOT=/caminho/permitido`, e aí `--json rel.json`
> grava lá. Assim o destino é sempre decisão explícita de quem roda. Os
> exemplos deste runbook usam caminhos relativos e não precisam de nada.

O seletor lê **uma hora a mais de cada lado**, porque o nome do arquivo é
aproximação da hora do evento — sem essa margem, uma janela que abre às 13:58
perderia o book do começo.

**O que ainda falta, e é a parte honesta:** cada fatia produz um relatório
próprio. Somar os relatórios exige **agregação incremental** — trivial para os
contadores (linhas, divergências, janelas por qualidade), correto por soma
ponderada para as médias, e **não trivial para percentis e para a varredura de
τ**, que precisam da amostra inteira. O caminho proposto, quando isso virar
gargalo de verdade:

1. cada fatia grava, além do relatório, um **estado parcial** (contadores
   brutos + reservatório de magnitudes + as janelas resolvidas com âncora e
   final em e18);
2. um passo de `merge` soma os contadores, funde os reservatórios e roda a
   varredura de τ **uma vez** sobre a união das janelas — que é barata,
   porque é uma lista de janelas, não de books.

A varredura de τ, aliás, já é imune ao problema: ela consome stream RTDS e
resoluções, nunca o book. As 152 janelas do M2.4 saíram sem tocar na passada 2.


## 7.2 Deploy do M2.7 — a gravação SERÁ parada

Diferente dos marcos anteriores, o M2.7 muda o recorder. A gravação em curso
precisa ser parada, e a nova só começa depois da hora de teste passar.

### O que mudou, e por quê

8h de gravação mediram **163.195 s de silêncio** do feed-verdade — dois
fenômenos distintos, dois mecanismos:

| Fenômeno | Medido | Mecanismo |
|---|---|---|
| Tópico mudo, conexão viva | 48 casos | reassinatura ao detectar silêncio (`rtds_topico_mudo_s`, 15 s) |
| Conexão inteira muda | 6 casos, maior 3.796 s | watchdog por ausência de dados (`rtds_sem_dados_timeout_s`, 30 s) |

Nenhum cobre o outro: o watchdog conta **qualquer** mensagem e não enxerga um
tópico caducando enquanto o outro chega; a reassinatura não derruba conexão
morta. A reassinatura periódica (`rtds_reassinatura_intervalo_s`, 300 s) é
seguro barato, não o mecanismo principal — a aritmética não fecha sem a
reação: 6 caducidades/h × até 300 s seriam 1.800 s/h contra a meta de 60 s/h.

### Sequência

```bash
# 1. parar a gravação em curso
sudo systemctl stop pulsearb-recorder
sudo systemctl status pulsearb-recorder     # confirmar 'inactive (dead)'

# 2. atualizar e reinstalar — em /opt/pulsearb, que é o que o systemd roda
cd /opt/pulsearb
sudo git pull origin main
sudo .venv/bin/pip install -e .
sudo chown -R pulsearb:pulsearb /opt/pulsearb

# 3. a unit INSTALADA tem de ser a do repositório
diff /etc/systemd/system/pulsearb-recorder.service \
     /opt/pulsearb/deploy/pulsearb-recorder.service && echo IDENTICAS

# 4. UMA HORA de teste — não pule
cd /opt/pulsearb && sudo -u pulsearb .venv/bin/python -m pulsearb.recorder --duration 1h

# 5. conferir a meta de aceite (ver abaixo) ANTES da gravação longa
```

**`/opt/pulsearb`, e nunca `~/pulsearb`.** Escrito assim porque a versão
anterior desta sequência dizia `cd ~/pulsearb`: como ela se roda com `sudo`,
o `~` é `/root`, e o comando cria (ou atualiza) um **segundo clone** que o
systemd não usa. Encontrado na VPS em 2026-09-18 — havia um `/root/pulsearb`
de 100 MB, parado num commit antigo, ao lado do `/opt/pulsearb` de verdade.
`git pull` nele atualiza nada que rode, e `.venv/bin/pip install -e .` ali
instala noutro venv. O passo 3 existe pela mesma razão: a unit instalada
tinha `User=root` onde o repositório diz `User=pulsearb`, e quem lê só o
repositório não descobre isso.

**O passo 4 precisa de espaço em disco.** Uma hora custa ~470 MB (§6). Se
`df -h /` mostrar menos de ~1 GB livre, recolha as gravações antigas (§7)
antes — não comece pelo teste e descubra o disco cheio no meio dele.

### A meta de aceite, e onde lê-la

**Silêncio total abaixo de 60 s na hora de teste** (a medição que motivou o
marco deu ~20.400 s/h).

Duas fontes, e a diferença entre elas é informação:

```bash
# (a) o que o recorder ACHA que aconteceu — teto calculado dos eventos que
#     os mecanismos detectaram. Ele NAO grava um arquivo à parte: sai na
#     linha final do log e vai para dentro da própria gravação, como meta
#     `recorder_relatorio` (ver `_write_meta` em recorder/__main__.py).
grep '"recorder encerrado"' /tmp/teste1h.log | tail -1 | jq '.saude_do_rtds'

# (b) a AUTORIDADE — lê os carimbos da gravação, não depende de o mecanismo
#     ter percebido. O `--json` exige caminho RELATIVO dentro do diretório
#     de trabalho (é a trava de escrita; `/tmp/...` é recusado).
mkdir -p relatorios && chown pulsearb:pulsearb relatorios
sudo -u pulsearb .venv/bin/python -m pulsearb.backtest data/recordings \
  --json relatorios/teste1h.json
jq '{ilegiveis: (.gravacao.arquivos_ilegiveis|length),
     conhecidas: .gravacao.janelas_conhecidas,
     com_resolucao: .gravacao.janelas_com_resolucao,
     silencio: (.gravacao.silencio_do_rtds
                | {total_s, silencios, por_escopo,
                   suspeita_de_assinatura_caducada})}' relatorios/teste1h.json
```

Os dois comandos acima estavam errados até 2026-09-18, e o erro é do tipo que
só a execução mostra: o (a) mandava ler `relatorio_do_recorder.json`, um
arquivo que o recorder **nunca escreveu** — `jq` responde *No such file or
directory* e quem estivesse com pressa leria isso como gravação que falhou. O
(b) passava `teste.json` (e, pior, `/tmp/teste.json`), que a trava de caminho
recusa: *nome de saída inválido*. Nenhum dos dois roda antes de a hora
FECHAR, porque o relatório do (a) só existe no fim.

Se **(a) disser que a meta foi atingida e (b) disser que não**, existe uma
terceira causa de silêncio que nenhum dos dois mecanismos cobre — e ela é o
próximo achado, não um detalhe. Não inicie a gravação longa nesse caso.

### Se a meta não for atingida

Não afrouxe o limiar. Os números para olhar, nesta ordem:

1. `saude_do_rtds.reconexoes_por_watchdog` alto → a conexão morre muito;
   olhar `quedas_por_feed` para o `close_code`
2. `reassinaturas_por_silencio_de_topico` alto e o silêncio continua → a
   reassinatura não está sendo aceita pelo servidor; capturar a resposta
3. os dois em zero e o silêncio continua → a terceira causa; o escopo em
   `silencio_do_rtds.por_escopo` diz se é conexão ou tópico

### Só então

```bash
sudo systemctl start pulsearb-recorder
sudo systemctl status pulsearb-recorder
# e a verificação pós-start da §5.1, que continua obrigatória
```

**E confira que ela ficou de pé.** `systemctl stop` não é falha, então
`Restart=always` não a reergue: uma gravação parada à mão fica parada para
sempre, calada. Foi o que aconteceu entre 11 e 18/09/2026 — o passo 1 desta
sequência correu, os outros não, e o recorder passou **uma semana inteira**
sem gravar sem nada denunciar. Se você parar aqui, anote onde parou.

## 8. Parar

```bash
sudo systemctl stop pulsearb-recorder     # para agora
sudo systemctl disable pulsearb-recorder  # não sobe no boot
```

O recorder fecha o arquivo corrente e grava o relatório final de cobertura
antes de sair. Matar com `kill -9` perde, no máximo, a última linha — o replay
tolera isso e conta quantas foram.

## 8.1 A carteira dedicada — item 5.1

**Nada aqui é executado por software deste repositório.** É procedimento de
pessoa, e está escrito para que a decisão do item 5.1 não precise ser tomada
com a credencial já na mão.

**Por que dedicada, e não a carteira de uso pessoal.** O bot assina com uma
chave que fica numa máquina que roda 24 h sem ninguém olhando. O prejuízo
máximo de uma chave comprometida é o saldo daquela carteira — e é isso, e
só isso, que a separação compra. Carteira compartilhada transforma um
defeito de bot em perda de patrimônio.

**O caminho é EOA (`signature_type=0`)** `[VERIFICADO]` API_NOTES §3. Carteira
proxy (`signature_type=2`) exigiria também o `funder`, o endereço que segura o
dinheiro — e o ponto de uma carteira dedicada é que quem assina e quem segura
sejam o mesmo endereço.

**A armadilha do EOA, e ela não avisa:** `[VERIFICADO]` API_NOTES §3 — é
preciso **setar as allowances de token manualmente antes da primeira ordem**.
Sem elas a ordem é aceita pelo CLOB e falha na liquidação. Não é erro de
assinatura, e a mensagem não fala em allowance.

Ordem dos passos:

0. **Conferir que o host pode NEGOCIAR — antes de pôr dinheiro em qualquer
   lugar.** `[MEDIDO 2026-09-18]` O CLOB recusa ordens por região, e a VPS de
   Londres é uma das recusadas (API_NOTES §17).

   Este passo é uma **porta**, e a numeração zero é só para dizer o que ela
   guarda: ele precisa das credenciais, então corre **depois de 1, 2, 6 e 7**
   — que não custam nada nem expõem capital — e **antes de 3, 4 e 5**, que
   põem dinheiro e aprovações numa carteira quente. Rode-o do host que vai
   operar LIVE, com a carteira ainda **vazia**: é para isso que o smoke exige
   saldo zero.

   ```bash
   cd /home/pulsearb && set -a && . /home/pulsearb/.env.credenciais && set +a \
     && PULSEARB_SMOKE_ORDEM="EU ACEITO ENVIAR UMA ORDEM REAL" \
     /opt/pulsearb/.venv/bin/python \
     /opt/pulsearb/scripts/smoke_ordem_assinada.py
   ```

   (A frase é a do smoke, `PULSEARB_SMOKE_ORDEM`, e **não** é a do item 3.4:
   este script envia uma ordem, não liga o modo LIVE.)

   - `motivo: auth_recusada` com `403 Trading restricted in your region` →
     **pare aqui.** Este host não opera LIVE com código nenhum. Não execute os
     passos 3, 4 e 5: fundear e aprovar allowances deixaria dinheiro e
     aprovações numa carteira que não pode enviar ordem.
   - recusa de NEGÓCIO (saldo insuficiente, allowance em falta) → o host
     negocia; siga para 3.

   Onde o bot pode operar legalmente é decisão de quem o opera, e é de
   conformidade antes de ser técnica. Este runbook regista o facto medido e
   manda verificar antes de gastar; contornar o bloqueio não é caminho que
   este projeto tome.

1. **Criar a carteira nova.** Chave privada gerada offline, na máquina que vai
   operar. Não importar chave que já existiu em outro lugar.
2. **Anotar o endereço** (público — pode ir para o `.env` e para este runbook).
3. **Fundear com o capital de operação em USDC na Polygon**, e nada além
   disso. O teto de exposição do portão é 50 USDC (item 3.9); financiar muito
   acima disso é guardar na carteira quente um dinheiro que as travas nunca
   deixariam usar.
4. **Deixar MATIC para gás.** Pouco, mas não zero: sem gás não se assina
   allowance nem se retira nada.
5. **Setar as allowances** dos contratos do CLOB (passo 8.1.1 abaixo).
6. **Derivar as credenciais de API** pelo L1 — é o único uso da chave privada
   fora de assinar ordem (API_NOTES §3). Use `scripts/derivar_credenciais.py`,
   que grava o arquivo `0600` e **nunca imprime o segredo**.

   **MEDIDO em 2026-09-11: este passo NÃO precisa de carteira fundeada.** O
   CLOB aceitou a assinatura L1 de uma carteira criada no dia anterior, sem
   USDC, sem MATIC e sem allowance nenhuma. Ou seja, **6 e 7 podem ser feitos
   ANTES de 3, 4 e 5** — e vale fazer, porque descobrem de graça se a
   assinatura L1 é aceita. Travar no passo 6 depois de fundear seria dinheiro
   parado numa carteira quente esperando um conserto.

   O script exige que o arquivo de saída fique **dentro do diretório de
   trabalho** — rode a partir de onde o arquivo deve nascer:

   ```bash
   cd /home/pulsearb && PULSEARB_CHAVE_PRIVADA="$(cat ~/.pulsearb-chave)" \
     /opt/pulsearb/.venv/bin/python \
     /opt/pulsearb/scripts/derivar_credenciais.py .env.credenciais
   ```
7. **Colocar no ambiente do serviço**, nunca no `config.yaml`, que é
   versionado:

```bash
PULSEARB_CHAVE_PRIVADA=0x…      # a chave. Nunca sai da máquina.
PULSEARB_API_KEY=…              # as três de baixo saem do passo 6
PULSEARB_API_SEGREDO=…          # base64 URLSAFE — o `-` e o `_` importam
PULSEARB_API_PASSPHRASE=…
PULSEARB_ENDERECO=0x…           # o do passo 2
```

`AssinadorLocal.do_ambiente()` lê a primeira; `CredenciaisL2.do_ambiente()` lê
as quatro restantes e **nomeia todas as que faltarem de uma vez** — reportar
uma por vez faria o operador descobrir a próxima a cada volta, com credencial
de dinheiro real na mão.

O arquivo de ambiente é `0600` e pertence ao usuário do serviço. Em systemd,
`EnvironmentFile=` e **não** `Environment=`: o segundo aparece em
`systemctl show`, que qualquer usuário local lê.

**O que confirma que deu certo, e o que não confirma.** Nenhum dos passos
acima autoriza o LIVE. A trava tripla do item 3.4 continua exigindo
`MODE=LIVE` **mais** `PULSEARB_CONFIRM_LIVE` **mais** a frase exata, e
`escolher_executor` recusa sem as três. Ter com que assinar não é ter
autorização para enviar — e é de propósito que as duas coisas sejam separadas.

### 8.1.1 As allowances

Duas aprovações, uma vez por carteira, antes da primeira ordem:

- **USDC** → contrato de troca do CLOB (é o que permite gastar o colateral)
- **CTF (ERC-1155)** → `setApprovalForAll` para o mesmo contrato (é o que
  permite entregar as shares quando a posição fecha)

Os endereços dos contratos vêm da doc da Polymarket, e **vão para o
`API_NOTES.md` com o carimbo `[VERIFICADO]` e a fonte** antes de qualquer
transação — endereço de contrato copiado de memória ou de um resultado de
busca é como se perde uma carteira inteira numa transação só. Este runbook
não os lista justamente por isso.

Aprovar o valor exato do capital, e não `uint256` infinito: allowance
infinita é a diferença entre perder o saldo do dia e perder tudo que a
carteira vier a receber depois.

**Checklist antes da primeira ordem real:**

- [ ] **o host NÃO é recusado por região** — passo 0 da §8.1 rodado NESTA
      máquina, com a carteira vazia, e a recusa NÃO foi `403 Trading
      restricted in your region` (API_NOTES §17). Esta linha é a primeira
      porque é a única que, se falhar, condena as outras: não há allowance,
      capital nem credencial que faça um host bloqueado enviar ordem.
- [ ] carteira nova, chave nunca usada em outro lugar
- [ ] só o capital de operação em USDC, e MATIC para gás
- [ ] allowance de USDC setada, no valor do capital e não infinita
- [ ] `setApprovalForAll` do CTF setada
- [ ] endereços dos contratos conferidos na doc e anotados no API_NOTES
- [ ] as 5 variáveis no `EnvironmentFile`, arquivo `0600`
- [ ] `chronyc tracking` verde (item 5.4 recusa LIVE sem NTP)
- [ ] o endereço público anotado onde a equipe vê; a chave privada em lugar nenhum
- [ ] **o venv tem `eth-account`** — `.venv/bin/python -c "import eth_account"`

  **Medido em 2026-09-18, e o achado é a razão desta linha existir.** O venv da
  VPS tinha todas as dependências menos essa, e `eth-account` é a biblioteca
  que ASSINA a ordem (`execution/ordem.py`, API_NOTES §1.4). Nada quebrou
  durante semanas porque o recorder e o SHADOW nunca assinam nada: o
  `ModuleNotFoundError` só aparece na primeira ordem de verdade. Um ambiente
  que roda 24 h e só falha no dia do dinheiro é o pior tipo de falha.

  A causa: o venv foi criado antes de `eth-account` entrar no `pyproject.toml`
  e nunca mais foi sincronizado. O conserto é `pip install -e .`, que é seguro
  porque todas as versões estão fixadas.

## 9. Checklist da primeira hora

- [ ] `smoke_discovery` achou janelas dos **dois** jogos (TWAP e horário)
- [ ] `systemctl status` mostra `active (running)`
- [ ] **§4.1 passou**: `chronyc tracking` com `Leap status: Normal`
- [ ] **§5.1 passou**: 0-1 "conexão caiu" em 60s, e os três `msgs_*` > 0
- [ ] `descartadas_book` em **0** e `divergencias` sem crescer
- [ ] o log tem `descoberta` a cada 30s, com `assinadas` estável
- [ ] `data/recordings/` tem um `.jsonl.gz` crescendo
- [ ] `du -sh` bate com a ordem de grandeza da §6 (~470 MB na primeira hora)
- [ ] o plano de disco da §6 está decidido: 80 GB, ou 50 GB **com** descarga agendada
- [ ] `descartadas` está em 0

## 10. SHADOW da rota maker nos pools — o relógio do 4.2

O 4.2 pede **duas semanas de SHADOW com edge líquido medido**, e o relógio
dele nunca começou: até 31/08 o motor errava o preço pago, e depois disso a
única rota viva mudou — deixou de ser o taker nos updown e passou a ser o
maker nos pools de horizonte longo (1.12). Este passo liga essa rota em
SHADOW na VPS. **Não envia ordem.** O cliente é o sombra, a trava tripla do
LIVE fica fechada, e nada aqui pede `PULSEARB_CONFIRM_LIVE`.

**Por que na VPS e não no Mac:** o SHADOW não grava o stream — grava um diário
de intenções — então os 18,4 GB livres que não comportam 72 h de recorder
comportam isto. E a máquina que grava não pode rodar análise pesada (§7): o
SHADOW é leve, mas o Mac dorme, e `time.monotonic()` congela com ele
(item 3.14).

```bash
cd /opt/pulsearb && git pull --ff-only
sudo cp deploy/pulsearb-shadow-maker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pulsearb-shadow-maker
journalctl -u pulsearb-shadow-maker -f
```

A unit já traz `PULSEARB_MODE=SHADOW` e `PULSEARB_DESCOBRIR_POOLS_DE_REWARD=true`
(o opt-in — sem ele a rodada é a do taker, que está medida e reprovada), e
anexa ao diário `data/diarios/shadow-maker-4-2.jsonl`: um restart continua a
mesma rodada.

**E traz o perfil do ensaio por escrito**, porque com os defaults do projeto
(os do taker: 5 shares, tetos 5/15/50 USDC, spread 0,04) esta rota não cota
nada — a descoberta descarta todo pool cujo `rewards_min_size` passa de 5, e
o portão recusa 1.000 shares. Os valores são os das rodadas r4–r8:
`TAMANHO_DA_COTACAO_MAKER_SHARES=1000`, `TOP_DE_POOLS_DE_REWARD=60`,
`RISK__STAKE_MAX_POR_TRADE_USDC=1000`, `RISK__STAKE_MAX_POR_JANELA_USDC=2000`,
`RISK__EXPOSICAO_MAX_USDC=120000`, `RISK__POSICOES_MAX_ABERTAS=120`,
`RISK__SPREAD_MAXIMO=0.06`, e registro de risco próprio
(`RISK__CAMINHO_DO_REGISTRO=data/risco/registro_maker_4_2.json`), para as
perdas sintéticas do taker não pausarem o maker. **Valem só em SHADOW** —
o bot não sobe tetos sozinho; a unit é o operador escrevendo. Para LIVE
nenhum destes números serve sem decisão nova, com capital real conferido.

### 10.1. O que o relato de 60 s tem de mostrar — na primeira hora

| campo | esperado | se não |
|---|---|---|
| `pools_descobertos` | > 0 em até 5 min (o ciclo é de 300 s) | 0 depois de 10 min: a descoberta não achou pool — conferir se `GET /rewards/markets/current` responde da VPS (§16 do API_NOTES) |
| `maker.motivos` | `repousada` / `manter` aparecendo; **`sem_pool_de_reward` NÃO pode ser o motivo único** | motivo único `sem_pool_de_reward` = o opt-in não pegou (variável ausente na unit) |
| `desde_o_relato` | andando a cada 60 s | congelado = 3.14 |
| `maker.motivos.livro_andou_contra` | > 0 ao longo da primeira hora (o livro anda; a regra recolhe) | 0 com o livro andando = a variável `MAKER_RECOLHE_QUANDO_O_LIVRO_ANDA` não pegou |
| `maker.regras` | `recolhe_quando_o_livro_anda: true` e `ticks_abaixo_do_microprice: null` nesta rodada | as DUAS ligadas = rodada confundida: uma muda quando a ordem sai, a outra muda o preço dela, e nenhuma das duas fica medida. Parar, corrigir a unit e recomeçar a contagem dos 14 dias |
| `maker.motivos.sem_microprice` | **ausente** nesta rodada — a âncora do microprice (4.0 (f)) está desligada aqui de propósito | aparecer quer dizer que alguém ligou `MAKER_TICKS_ABAIXO_DO_MICROPRICE` sem desligar o recolher: as duas na mesma rodada não se distinguem, e a rodada não mede nenhuma das duas. A rodada da âncora troca uma pela outra — `MAKER_RECOLHE_QUANDO_O_LIVRO_ANDA=false` na mesma edição |
| id das ordens no diário | todo id com prefixo `sombra-` | qualquer id sem `sombra-` é **PARAR AGORA**: `systemctl stop` e abrir issue — significaria ordem real |

### 10.1b. E o mesmo relato lido por um programa

A tabela acima é para a primeira hora, a olho. Para a rodada inteira — e no
fim dela — quem responde é o leitor, que soma o que o motor publicou e diz o
veredito do 4.2:

```bash
journalctl -u pulsearb-shadow-maker -o cat \
    | grep '"msg":"shadow"' > relatorios/RELATOS_4_2.jsonl

.venv/bin/python scripts/resumo_da_rodada_maker.py \
    --relatos relatorios/RELATOS_4_2.jsonl \
    --diario data/diarios/shadow-maker-4-2.jsonl
```

**Rode isso na primeira hora também, e não só no dia 14.** Ele confere de
uma vez as quatro maneiras de a rodada não valer nada — e três delas não
aparecem em nenhuma linha da tabela acima:

| recusa | o que aconteceu |
|---|---|
| `rodada_confundida` | duas regras experimentais ligadas juntas (a linha `maker.regras` da tabela, virada conta) |
| `regras_mudaram_no_meio` | alguém editou a unit e reiniciou. A unit **anexa ao mesmo diário de propósito**, então isso mistura duas regras nos mesmos 14 dias e o último relato sozinho parece uma rodada limpa |
| `rodada_dormiu` | ciclo de trabalho abaixo de 0,99 (item 3.16) |
| `relato_sem_maker` | o laço maker não subiu; a rodada segue viva, relatando, medindo nada |

O leitor **não recalcula** rewards nem markout: os números são os que o motor
publicou. E o `order_id` sem `sombra-` — a última linha da tabela acima, hoje
conferida a olho — **derruba o veredito**, não vira aviso: um ensaio que pode
ter mandado ordem real não é um SHADOW válido.

**`rodada_nao_terminou` no meio da rodada é o esperado, não defeito.** O
relato de 60 s sai sempre antes do prazo vencer; o tempo de parede completo
só existe na linha que o processo emite ao encerrar, marcada com
`fim_da_rodada`. Rodando no dia 3, o leitor diz `rodada_nao_terminou` e está
certo — é assim que ele distingue rodada em curso de processo morto pelo
systemd.

**Se ele disser que o PROCESSO VOLTOU, a conta já vem somada.** A
`CaixaDoMaker` não persiste nada e o `run` refaz o relógio, então cada subida
conta do zero — mas os campos da caixa são cumulativos desde a subida, e o
leitor corta o fluxo onde `parede_s` cai e soma os trechos. O que não entra é
o tempo fora do ar: ele não foi observado, e por isso **a rodada leva mais de
14 dias de calendário para fechar 14 dias de medida**. Não recomece a rodada
por causa disso; espere o tempo medido chegar.

**Capture o journal inteiro, não só o fim.** A soma reconstrói a rodada a
partir dos relatos, então um `--since` que corte trechos antigos perde a
medida deles. Se o journal rotacionar durante as duas semanas
(`journalctl --vacuum-*`, `SystemMaxUse`), o trecho rotacionado some com ele:
vale exportar `relatorios/RELATOS_<rodada>.jsonl` de tempos em tempos e
concatenar, em vez de contar com o journal no dia 14.

**E a unit é `Restart=on-failure`, não `always`.** Uma rodada que terminou
fica terminada: com `always`, ela reiniciava 10 s depois de encerrar bem e
começava outra sozinha, anexando ao mesmo diário, para sempre.

### 10.1c. As quatro rodadas correm JUNTAS, não uma depois da outra

São três regras experimentais (quadro 4.0 e/f/g) mais a base, e cada uma
precisa da sua rodada porque juntas não se distinguem. Em sequência isso
custa **56 dias** — mas o tempo é o menor dos dois problemas.

O grande é que **quatro rodadas em semanas diferentes comparam regra com
mercado.** A liquidez, a volatilidade e o próprio conjunto de pools de reward
mudam de semana para semana, e essa diferença entraria no resultado com o
nome da regra. É a mesma confusão que a unit já recusa dentro de uma rodada,
espalhada no tempo — onde é mais difícil de ver, porque cada rodada, sozinha,
parece limpa.

**Primeiro pare a unit de uma rodada só**, se o §10 acima já a subiu. Ela
liga o perfil do `recolher`, então deixá-la no ar deixaria **cinco** processos
rodando, com o `@recolher` em duplicata — um assinante de feed a mais que a
medida de capacidade desta seção não contou.

```bash
sudo systemctl disable --now pulsearb-shadow-maker    # a de uma rodada só
sudo cp deploy/pulsearb-shadow-maker@.service /etc/systemd/system/

# O PRAZO, e ele é o mesmo para as quatro: é o que faz elas cobrirem o mesmo
# intervalo de mercado. Sem ele a unit não sobe (sai com 2, e o
# `RestartPreventExitStatus=2` impede o laço de restart).
FIM=$(date -u -d '+14 days' +%Y-%m-%dT%H:%M:%SZ)
sudo sed -i "s|^PULSEARB_RODADA_TERMINA_EM=.*|PULSEARB_RODADA_TERMINA_EM=$FIM|" \
    /opt/pulsearb/deploy/rodadas/comum.env

sudo systemctl daemon-reload
for r in base recolher ancora pausa; do
    sudo systemctl enable --now pulsearb-shadow-maker@$r
done
```

**Por que prazo absoluto e não `--duration 14d`:** o `Restart=on-failure`
reexecuta o comando, e um prazo relativo daria 14 dias NOVOS. Uma instância
que caísse no dia 13 observaria 27 dias e terminaria 13 dias depois das
irmãs — os rewards e o markout dela deixariam de cobrir o mesmo intervalo de
mercado que a comparação exige. O instante absoluto sobrevive ao restart sem
persistir nada.

O nome depois do `@` é o arquivo em `deploy/rodadas/`, e é ele que dá à
instância o diário, o registro de risco e **a regra**. Cada `.env` escreve as
três regras, inclusive as duas desligadas: regra ausente por decisão não pode
parecer regra ausente por esquecimento.

**O que é próprio de cada instância, e por quê:**

| coisa | por quê |
|---|---|
| diário `data/diarios/shadow-maker-%i.jsonl` | duas rodadas no mesmo arquivo somam — é o que o `caminho_do_diario_da_rodada` já fecha com `O_EXCL` |
| registro `data/risco/registro_maker_%i.json` | o `_gravar` do portão monta o `.tmp` a partir do caminho do registro: duas rodadas no MESMO registro escrevem o mesmo temporário, e o rename atômico pode publicar uma mistura |
| `deploy/rodadas/%i.env` | a regra **e o registro de risco**. **Sem o `-` no `EnvironmentFile`**: arquivo ausente derruba a unit, porque com `-` o systemd o ignoraria em silêncio e as quatro subiriam como rodada BASE, todas verdes, por 14 dias |

**E a unit não tem nenhuma linha `Environment=`.** O `systemd.exec(5)` diz que
`EnvironmentFile=` vence `Environment=` **sempre**, não por ordem: qualquer
valor do ensaio escrito na unit seria silenciável por um `/opt/pulsearb/.env`
de máquina, nas quatro instâncias, por 14 dias. Tudo que precisa ser a palavra
final mora em arquivo de ambiente carregado depois do `.env` —
`deploy/rodadas/comum.env` para o perfil (igual nas quatro) e
`deploy/rodadas/%i.env` para o registro e a regra. **Se precisar mudar o
perfil do ensaio, mude o `comum.env` e não a unit.**

O `KILL` é **compartilhado de propósito** — a chave existe para parar tudo.

**O que NÃO está medido aqui, e a primeira hora mede:** se a VPS carrega
quatro processos. Disco do diário já era o item em aberto do §10.2 com um
processo; com quatro, meça `du -sh data/diarios/` na primeira hora e
multiplique por 336 antes de deixar rodando. Memória, CPU e o limite de
conexões WS do CLOB com quatro assinantes também não estão medidos. Se não
couber, a saída não é voltar para 56 dias em sequência: é rodar **base +
uma** por vez, que preserva a comparação contra o mesmo mercado.

### 10.1d. A medida das primeiras horas — feita em 2026-09-20

As quatro subiram em `2026-09-19 22:15:50..54 UTC` na VPS de **1 vCPU /
961 MiB / 24 GB**. Medida às `01:50 UTC` do dia seguinte, **3 h 34 min**
depois. **Ela responde uma das três perguntas do §10.1c e deixa duas em
aberto** — o que está aberto vai escrito abaixo, não resumido em "parcial".

| pergunta | medida | veredito |
|---|---|---|
| disco do diário | 11.368.842 B somados em 3 h 34 → **3,04 MiB/h nas quatro** | ✅ **336 h ≈ 1,0 GiB**, contra 18 GB livres |
| o processo sobrevive | `NRestarts=0` nas quatro, `active running` | ✅ nas primeiras 3 h 34 |
| nível ERRO no diário | `"nivel":"ERRO…"` na última hora: **0** | ✅ |
| **memória** | 787 MiB usados, **174 disponíveis, swap 0** | ❓ **não resolvido** |
| **CPU** | carga **4,00 / 4,00 / 4,01 em 1 vCPU** | ❓ **não resolvido** |
| **WS do CLOB** | **601 linhas/h** casando `erro\|falhou\|Traceback` nas quatro | ❓ **não resolvido** |

Os tamanhos por instância, para conferir que nenhuma parou de escrever:
`ancora 3.472.902`, `base 2.527.711`, `pausa 2.286.741`,
`recolher 3.081.488`.

**Por que 601 não é o mesmo número que 0.** O grep largo casa toda linha que
carrega um campo `"erro"`, e as 601 são WARNING de `conexão caiu` —
`ConnectionClosedError: no close frame received or sent`,
`"close_origem":"cliente"`. Nenhuma é nível ERRO. Mas 601/h nas quatro é
**uma reconexão a cada ~24 s por processo**, contra as 11/h medidas no §7.2
com um processo só. O salto é de 13×, e ele não está explicado.

**As duas hipóteses para o salto, e elas se distinguem.** (a) Os quatro
processos não recebem CPU para responder o ping a tempo, e é o CLIENTE que
derruba a conexão — o que `"close_origem":"cliente"` e a carga 4,00
sustentam. (b) O CLOB limita quatro assinantes do mesmo IP. **Enquanto não
se distinguir, o ensaio de 14 dias está sob suspeita**, porque se for (a) a
fila do escalonador entra no `cotacoes_repousando` e no `meio_no_fill` com o
nome do mercado — o mesmo tipo de confusão que o §10.1c existe para impedir,
só que vindo da máquina em vez do calendário.

**Carga 4,00 sozinha não prova saturação de CPU.** A carga do Linux conta
processos em R **e** em D (espera de disco). Para separar:

```bash
ps -o pid,stat,pcpu,pmem,etimes,args -C python --sort=-pcpu | head -6
vmstat 5 4            # colunas r (fila) e wa (espera de E/S)
grep -c 'Out of memory' /var/log/syslog || true
```

Leia assim: `%CPU` somando perto de 100 e `r` ≥ 4 com `wa` baixo é a
hipótese (a) — CPU saturada. `wa` alto com `%CPU` baixo é disco, e aí a
carga 4,00 não contamina a medida. **Foi rodado, e deu (a) — o §10.1e traz
os números e o que eles obrigam.**

**O que fazer com o veredito:**

- **CPU saturada (a)** → o ensaio de quatro rodadas não cabe nesta máquina.
  A saída do §10.1c vale: **base + uma regra** por vez, dois processos, 28
  dias em vez de 14 — ou uma VPS maior, que preserva os 14.
- **Limite do CLOB (b)** → a reconexão é do feed, não do relógio; siga, mas
  registre a taxa no relato final, porque ela entra na cobertura.

**A gravação de 72 h NÃO sobe junto enquanto isso estiver em aberto.** O
disco até comporta (§6: ~699 MiB/h, com a descarga de 12 h o pico é ~8,2
GiB, e sobram 18 GB), mas **a memória não**: 174 MiB disponíveis e **zero
swap** não acomodam um quinto processo Python. Um OOM aqui mata uma das
rodadas, e uma rodada com buraco que as irmãs não têm é exatamente a
comparação que o §10.1c recusa.

### 10.1e. O desempate foi feito: é (a), CPU saturada

Rodado em `2026-09-20 ~04:10 UTC`, 5 h 55 min depois de as quatro subirem.
**Não sobrou ambiguidade.**

```
    PID STAT %CPU %MEM ELAPSED COMMAND
 105231 Rsl  24.5 14.9   21282 …shadow --diario …-recolher.jsonl
 105177 Rsl  24.5 14.6   21283 …shadow --diario …-base.jsonl
 105343 Rsl  24.5 14.4   21279 …shadow --diario …-pausa.jsonl
 105285 Rsl  24.4 14.6   21281 …shadow --diario …-ancora.jsonl

 r  b   swpd   free  …   in   cs us sy id wa
 4  0      0  83936  … 2058 1080 97  3  0  0
 4  0      0  85868  … 1996 1056 97  3  0  0
 4  0      0  87396  … 2230 1064 97  3  0  0
```

(A **primeira** linha do `vmstat` é média desde o boot e se descarta sempre;
as três acima são as amostras de 5 s.)

**Os quatro sinais dizem a mesma coisa:**

| sinal | valor | o que significa |
|---|---|---|
| `STAT` | **R** nos quatro | rodando/prontos, nenhum em D (espera de disco) |
| `%CPU` | 24,5 / 24,5 / 24,5 / 24,4 | **somam 97,9% de 1 vCPU** — dividem um núcleo em quatro |
| `id` | **0** | **zero ocioso**: não há CPU sobrando em instante nenhum |
| `wa` | **0** | não é disco. A hipótese (b) do §10.1d não se sustenta por aqui |
| `r` | **4** constante | os quatro estão **sempre** prontos, sempre esperando vez |
| OOM | **0** | ainda não matou nada — mas `free` em ~85 MiB e swap zero |

**O que `r = 4` com `id = 0` prova, e é o essencial:** se cada processo
precisasse mesmo só de 24,5%, ele dormiria depois de trabalhar e a fila
média cairia para perto de 1. Ela não cai. Os quatro estão permanentemente
prontos — **cada um quer mais CPU do que recebe**. Os 24,5% não são o custo
de uma rodada, são o teto que o escalonador impõe a ela.

> ⚠️ **ESTA SEÇÃO AFIRMAVA QUE A CPU EXPLICAVA AS RECONEXÕES. A MEDIDA
> DESMENTIU.** O texto dizia que o laço de eventos não voltava a tempo de
> responder o ping e que por isso o nosso lado fechava a conexão. Contando
> `conexão caiu` na MESMA unidade, com o MESMO `grep` e janelas de 20 min:
> **8 com as quatro disputando** (03:30–03:50) contra **9 e 10** nas leituras
> feitas depois de parar três. **A reconexão não cai quando sobra CPU** — ela
> tem causa própria, ainda não achada, e **máquina maior não vai resolvê-la**.
> A comparação com janela solo inteiramente limpa está no fim do §10.1f e
> ainda precisa ser rodada; as leituras de 9 e 10 pegaram a transição.
>
> De onde veio o engano: as "601/h" do §10.1d saíram de
> `grep -ci "erro\|falhou\|Traceback"` sobre as QUATRO unidades, que casa
> muito mais linha do que só reconexão. Contra as 8 por 20 min medidas
> depois, o salto de 13× que eu inferi **nunca existiu** — eu comparei duas
> contas diferentes e chamei a diferença de fenômeno.

**A consequência sobre o ensaio NÃO depende disso, e segue de pé: o ensaio
de 14 dias iniciado em 2026-09-19 22:15 UTC não produz dado válido.** Ela se
apoia só no `id = 0` com `r = 4` acima: o `cotacoes_repousando` e o
`meio_no_fill` das quatro carregam fila de escalonador junto com mercado.
Não é um ensaio ruim que se corrige na análise — é um instrumento medindo a
si mesmo.

**O que esta medida AINDA não diz, e é o que decide a saída:** quanto UMA
rodada consome sozinha. Com `id = 0` não dá para inferir — o teto esconde a
demanda. Sem esse número não se sabe se **base + uma** (a saída do §10.1c)
cabe, nem que tamanho de máquina comprar.

**O ensaio que responde, e responde duas coisas de uma vez** — 10 minutos:

```bash
# Para três, deixa a base sozinha. O ensaio já está invalidado; parar não
# perde nada que se fosse aproveitar.
for r in recolher ancora pausa; do
    sudo systemctl stop pulsearb-shadow-maker@$r
done
sleep 120     # deixa assentar

ps -o pid,stat,pcpu,pmem,args -C python --sort=-pcpu | head -3
vmstat 5 4
journalctl -u pulsearb-shadow-maker@base --since '-8min' --no-pager \
  | grep -c 'conexão caiu'
```

Leia os dois resultados assim:

- **A demanda de uma rodada sozinha.** Ela sai do `vmstat`, **não** do
  `%CPU` do `ps` — ver a armadilha no §10.1f. Divida 100 por ela para saber
  quantas rodadas cabem por núcleo, e some folga, porque um núcleo a 100% é
  onde a reconexão começou.
- **A contagem de `conexão caiu` com UM processo.** Se despencar, era CPU
  (confirma (a) por um segundo caminho). Se continuar alta, há **também**
  um limite do CLOB por IP, e aí nem uma máquina maior resolve sozinha —
  isso precisaria entrar no relato de cobertura.

### 10.1f. Uma rodada sozinha: média 42%, **pico 66%** — medido por PID

Rodado em `2026-09-20 ~04:25 UTC`, com `recolher`, `ancora` e `pausa`
paradas e a `base` sozinha:

```
    PID STAT %CPU %MEM COMMAND
 105177 Ssl  24.6 14.8 …shadow --diario …-base.jsonl

 r  b   swpd    free  …   in   cs us sy id wa st
 1  0      0  504880  … 1845  933 34  4 61  0  1
 0  0      0  504880  … 1836  916 34  4 62  0  0
 0  0      0  504880  … 2161  937 43  5 52  0  1
```

**A ARMADILHA, e ela quase inverte a leitura: o `%CPU` do `ps` é a média de
TODA a vida do processo**, não o instante — tempo de CPU acumulado dividido
pelo tempo decorrido desde o `exec`. Este processo passou 5 h 55 min
disputando o núcleo a 24,5% e só 2 min sozinho, então os **24,6% que o `ps`
mostra são o passado sob disputa**, quase inalterado. Quem lê o `ps` aqui
conclui que a rodada custa 24,5% com ou sem concorrência — e compraria
máquina errada. **A demanda real está no `vmstat`, que mede o intervalo.**

**O que o `vmstat` diz:** `us + sy` = 38, 38, 48 com `id` entre 52 e 62%. E
os dois sinais que definiam a saturação viraram: `STAT` foi de **R** para
**S** (o processo volta a dormir, não fica mais sempre pronto) e `r` caiu de
4 constante para 0–1. **Esses dois sinais bastam para o veredito de
saturação** — eles são da máquina, e é da máquina que se quer saber.

**Mas os 38–48% NÃO são, ainda, o custo de uma rodada** (achado P2 do Codex
na revisão do #170). O `us`/`sy` do `vmstat` é da máquina INTEIRA: parar as
outras três não prova que o que sobrou pertence à `base`. Qualquer outro
serviço do host entra na conta, e multiplicar esse número por quatro
dimensiona VPS errado. **Estender a amostra para 30–60 min não corrige isso
— é erro de atribuição, não de ruído.**

**Como medir o custo de UMA rodada de verdade.** Precisa ser por PID e por
intervalo. Por intervalo porque o `%CPU` do `ps` é média de vida (a
armadilha acima); por PID porque o `vmstat` é da máquina:

```bash
# 1. a linha de base: o host SEM nenhuma rodada
sudo systemctl stop 'pulsearb-shadow-maker@*'
vmstat 5 6          # us+sy aqui é o custo do host, e sai da conta

# 2. a rodada sozinha, por PID
sudo systemctl start pulsearb-shadow-maker@base
sleep 120
pidstat -p "$(pgrep -f 'shadow.*-base\.jsonl' | head -1)" 5 12
```

Sem `pidstat` (pacote `sysstat`), o mesmo pela contabilidade do kernel —
`utime + stime` do `/proc`, que é exatamente o que o `pidstat` lê. **Com o
mesmo intervalo curto, e guardando CADA amostra:**

```bash
pid=$(pgrep -f 'shadow.*-base\.jsonl' | head -1)
tick=$(getconf CLK_TCK)
ler() { awk '{print $14+$15}' /proc/"$pid"/stat; }
a=$(ler)
for i in $(seq 12); do
    sleep 5
    b=$(ler)
    echo "amostra $i: $(( (b - a) * 100 / (5 * tick) ))%"
    a=$b
done
```

**Por que 12 × 5 s e não um delta de 60 s.** Um único intervalo de um minuto
devolve UMA média, e repetir só dá mais médias de minuto: o pico de 5 s
desaparece dentro delas. É o pico que satura, e as próprias amostras de 5 s
do `vmstat` já variaram de 38 a 48 — uma média de minuto teria escondido
essa variação e subdimensionado a máquina (achado P2 do Codex, segunda
rodada do #170). O `pidstat 5 12` acima tem exatamente essa forma; o
fallback tem de ter a mesma.

O número que dimensiona hardware é **esse**, não o `us+sy` — e é o **maior**
das amostras, não a média delas.

**Memória:** `free` subiu de ~85 MiB para ~505 MiB. Três rodadas a menos
liberaram ~420 MiB, ou seja **~140 MiB por rodada** — bate com os 14,6% de
`%MEM` sobre 961 MiB.

**A conta de capacidade, então:**

| arranjo | CPU pedida | veredito |
|---|---|---|
| 1 rodada | **≤ 38–48%** — teto do host, não atribuído | ✅ cabe com folga, `id` 52–62% |
| **base + uma regra** (a saída do §10.1c) | ~80%, pico extrapolado ~96% | ❓ **NÃO MEDIDO** — ver abaixo |
| as quatro em paralelo | ~160% | ❌ **medido e reprovado**: `id=0`, `r=4` |

**A linha do meio é extrapolação, e extrapolação não reprova nada.** A
primeira versão desta seção a marcava ❌ com o argumento de que 80%
sustentado e picos de 96% são "a mesma beira" que derrubou as quatro. Isso
não se sustenta: o regime que falhou pedia ~160% de um núcleo e mostrava
`id = 0` **sem folga nenhuma**; 80–96% ainda tem CPU sobrando, e a 96% o
laço de eventos continua sendo escalonado. O 96% saiu de dobrar a **maior**
das três amostras de 5 s, que é a extrapolação mais frouxa possível.

Isso importa porque esta tabela decide o experimento: marcar o plano B como
impossível, sem medir, empurra para o sequencial de 56 dias (que o §10.1c
recusa) ou para comprar máquina — **duas saídas caras escolhidas a partir de
uma conta, não de uma medida.** Vale a regra do quadro nos dois sentidos:
❌ é "medido e reprovado", não "estimado e reprovado".

**O ensaio que decide — suba a SEGUNDA rodada e meça:**

```bash
sudo systemctl start pulsearb-shadow-maker@pausa   # base + uma regra
sleep 600                                          # 10 min, não 2

vmstat 5 12
pidstat -p "$(pgrep -f 'shadow.*-base\.jsonl' | head -1)" 5 12
```

> ⚠️ **O CRITÉRIO DE REPROVA QUE EU TINHA ESCRITO AQUI ERA INSUFICIENTE.** Ele
> dizia: reprova se `id` encostar em 0 ou `r` ficar em 2 constante. **O ensaio
> foi rodado e NENHUM dos dois aconteceu — e mesmo assim reprovou.** Num
> núcleo só, dois processos que querem CPU no MESMO instante formam fila
> mesmo havendo ocioso entre as rajadas: o `id` médio continua bonito e a
> espera acontece dentro das janelas que importam. **A coluna que mede isso
> é `%wait` do `pidstat`** — tempo do processo na fila de execução, pronto e
> sem receber CPU. É ela que reprova, e ela não aparece no `vmstat`.

**Critério correto:** compare o `%wait` do `pidstat` com o da janela solo do
§10.1f. Solo, ele fica em ~0. Se subir para dois dígitos, o escalonador
entrou na medida — e entrou exatamente nos momentos de mercado agitado, que
são os que decidem a cotação.

#### A medida por PID foi feita, e o pico é o que muda tudo

`2026-09-20 14:57 UTC`, `base` sozinha havia ~10 h. **Dois métodos
independentes, o `pidstat` e o laço do `/proc`, em janelas diferentes:**

| | amostras de 5 s (%CPU) | média | **pico** |
|---|---|---|---|
| `pidstat 5 12` | 48,0 · 42,8 · 32,8 · 37,2 · 32,2 · 39,6 · 50,0 · 46,8 · 40,8 · **66,2** · 40,8 · 33,8 | **42,6%** | **66,2%** |
| `/proc`, janela seguinte | 23 · 35 · 24 · 21 · 32 · 30 · 19 · 60 · **61** · 49 · 37 · 45 | ~36% | **61%** |

**Isso fecha a pergunta de atribuição** (achado P2 do Codex, quarta rodada
do #170): o número agora é por PID, não da máquina. E os 38–48% que o
`vmstat` tinha dado eram mesmo quase todos da rodada — o host contribui
pouco.

**Mas a medida por PID trouxe o que a de três amostras escondia: a rodada
varia de 19% a 66%.** Dimensionar pela média de 42% subdimensiona em quase
60%. É o pico que satura, e o pico é **66%**.

**E os picos das rodadas tendem a coincidir.** As quatro assinam os MESMOS
mercados e reagem aos MESMOS eventos de livro: quando o mercado se mexe,
todas trabalham mais ao mesmo tempo. Isso é raciocínio sobre o desenho, não
medida — mas é o lado conservador, e é o que vale para dimensionar.

**O que JÁ está decidido, porque é aritmética e não extrapolação:** quatro
rodadas em paralelo pedem mais de um núcleo, e nenhuma máquina de 1 vCPU
entrega isso — isso não depende de atribuição nenhuma, já que o regime de
quatro foi medido direto com `id = 0` e `r = 4`.

**O tamanho da máquina, com o pico medido:**

| | pela média (42%) | **pelo pico (66%)** |
|---|---|---|
| 4 rodadas pedem | 168% | **264%** |
| 2 vCPU (200%) | ✅ caberia | ❌ **corta nos picos** |
| 4 vCPU (400%) | folga grande | ✅ **66% de utilização no pico** |

**São 4 vCPU, e a dúvida de "folgado demais" morreu aqui:** dimensionar
pelos 42% de média levaria a 2 vCPU, e 2 vCPU não segura 264%. Memória:
~560 MiB de RSS somados, 2 GiB bastam e o swap deixa de ser zero.

**E o mesmo pico muda o prognóstico de duas rodadas:** 2 × 66% = **132%**,
que não cabe em 1 vCPU. Isso **não** reprova o plano B — extrapolação não
reprova nada, e é a terceira vez que este runbook diz isso — mas diz o que
procurar no ensaio do §10.1f: não olhe só a média do `id`, olhe se ele
**encosta em 0 nos picos**. Uma média confortável com estouro nos momentos
de mercado agitado é o pior caso possível, porque é exatamente nesses
momentos que a cotação decide.

**O que NÃO está medido aqui, e precisa estar antes de comprar:** os 38–48%
saem de **três amostras de 5 s** da máquina inteira. Faltam as duas coisas:
a **atribuição** (por PID, como acima) e a **variação** (o pico, não a
média). Sem as duas, o número que multiplica por quatro é chute com cara de
medida.

**A comparação de reconexões ainda NÃO está feita, e o número de 4 não a
faz.** Aquele `grep -c 'conexão caiu'` pegou uma janela de 8 min que
cobria ~5,7 min com as quatro de pé e ~2,3 min com uma só — está misturada.
E as 601/h do §10.1d vieram de outro `grep` (`erro\|falhou\|Traceback`, nas
quatro unidades), que casa mais linhas que só as de reconexão. **Os dois
números não se comparam.** A comparação limpa é a mesma unidade, o mesmo
`grep` e a mesma duração, numa janela de cada regime:

> ⚠️ **ORDEM IMPORTA, e `--since '-20min'` aqui é uma armadilha** (achado
> P2 do Codex, segunda rodada do #170). Quem lê este runbook de cima para
> baixo já subiu a segunda rodada no ensaio do §10.1f e religou as duas no
> §10.1g antes de chegar aqui — uma janela RELATIVA pegaria dois processos e
> reinícios, e a comparação "solo" mediria outra coisa. **Faça esta medida
> ANTES de subir a segunda rodada, e com limites ABSOLUTOS.**

```bash
# 1. com UMA rodada só de pé, marque o início e NÃO suba a segunda
date -u +'%Y-%m-%d %H:%M:%S'
# anote o que saiu; a sessão pode cair e este valor não se recupera
```

Volte 20 minutos depois — sem `sleep` longo, que já derrubou sessão aqui — e
use os dois limites:

```bash
INICIO_SOLO='2026-09-20 04:25:00'      # o que você anotou
FIM_SOLO='2026-09-20 04:45:00'         # 20 min depois

# solo
journalctl -u pulsearb-shadow-maker@base \
  --since "$INICIO_SOLO" --until "$FIM_SOLO" --no-pager \
  | grep -c 'conexão caiu'

# disputa — a MESMA unidade, o MESMO grep, 20 min também
journalctl -u pulsearb-shadow-maker@base \
  --since '2026-09-20 03:30' --until '2026-09-20 03:50' --no-pager \
  | grep -c 'conexão caiu'
```

A janela de disputa está dentro do intervalo em que as quatro rodavam
(19/09 22:15 até 20/09 ~04:23), então ela é boa como está.

Se o solo for muito menor, a reconexão era CPU. Se os dois forem parecidos,
há uma causa independente da carga, e máquina maior não a resolve.

### 10.1f-bis. O ensaio de duas rodadas REPROVOU — e não pelo `id`

Rodado em `2026-09-20 15:17` e repetido às `15:30`, com `base` + `pausa`.
**Os dois sinais que eu tinha eleito como critério passaram:**

| sinal | esperado para reprovar | medido |
|---|---|---|
| `id` do `vmstat` | encostar em 0 | **mínimo 21%** — nunca zerou |
| `r` do `vmstat` | 2 constante | **0 a 4**, quase sempre 0–2 |

**E mesmo assim o regime reprova, pela coluna que eu não tinha olhado:**

| `%wait` do `pidstat` sobre a `base` | mínimo | máximo | média |
|---|---|---|---|
| **sozinha** (14:57) | 0,00 | 1,20 | **0,38%** |
| **com a `pausa`** (15:17) | 9,60 | 23,40 | **16,70%** |
| **com a `pausa`** (15:30) | 23,20 | 50,20 | **35,97%** |

**`%wait` é o tempo em que o processo está PRONTO e não recebe CPU** — fila
de escalonador pura. Ele saiu de ~0,4% para **36%**: quase cem vezes. Na
segunda rodada de amostras, **metade do tempo** em alguns intervalos.

**Por que o `id` não pegou isso, e a lição vale além deste caso.** `id` é
média sobre 5 s de máquina inteira; `%wait` é do processo, e conta o
instante. Duas rodadas que reagem ao MESMO evento de livro acordam JUNTAS:
nesses milissegundos uma espera a outra, e entre as rajadas sobra ocioso que
enche o `id` de volta. **Média boa com fila nas rajadas é o pior caso** —
porque as rajadas são exatamente quando a cotação decide, e é a latência
delas que o `cotacoes_repousando` e o `meio_no_fill` medem.

**Veredito: ❌ base + uma regra NÃO cabe em 1 vCPU. Medido, com o número que
reprovou: `%wait` de 0,38% para 16,70% e 35,97%.** O plano B do §10.1h morre
aqui, e com ele a última saída gratuita.

**O que sobra para o item 4.2, sem eufemismo:**

| caminho | custo | o que entrega |
|---|---|---|
| **VPS de 4 vCPU** | ~1 mês de aluguel | as quatro em paralelo, 14 dias, todas as comparações limpas |
| uma rodada por vez | 56 dias | ❌ o §10.1c já recusa: compara regra com mercado |
| baratear a rodada | trabalho não medido | 40% de um núcleo para cotar em 60 mercados em SHADOW é muito; `in` ~2.000–3.000/s e `cs` ~1.000/s sugerem que há o que cortar. **Mas isso é uma investigação inteira, não um ajuste** — e nada garante que caiba em 1 vCPU no fim |

**A recomendação era a primeira linha**, e a razão era de risco, não de
preferência: as outras duas gastam semanas para talvez chegar ao mesmo
lugar, e o 4.2 é pré-requisito de LIVE.

> ⚠️ **O operador RECUSOU a máquina maior em 2026-09-21.** A recomendação
> acima fica registrada como foi feita — não se apaga recomendação por ela
> não ter sido seguida —, mas ela **não é mais o plano**. O que sobrou está
> no §10.1j, e a tabela desta seção continua valendo: com a rodada do
> tamanho atual, o 4.2 não tem caminho nesta máquina.

### 10.1f-ter. A reconexão NÃO é CPU — agora com janelas limpas

Mesma unidade, mesmo `grep`, quatro janelas de 20 min:

| janela | regime | `conexão caiu` |
|---|---|---|
| 03:30–03:50 | **quatro rodadas disputando** | **8** |
| 05:00–05:20 | uma rodada sozinha | **15** |
| 09:00–09:20 | uma rodada sozinha | **14** |
| 13:00–13:20 | uma rodada sozinha | **8** |

**Solo é igual ou MAIOR que sob disputa.** A hipótese de que a CPU derrubava
o feed está descartada com janelas limpas, e não por inferência.

E a variação entre as janelas solo (8 a 15) é **maior** que a diferença para
o regime de disputa. Ou seja: o que move a taxa de reconexão é outra coisa —
hora do dia ou agitação do mercado são os suspeitos óbvios, nenhum medido.

**Isto virou item próprio, e ele não sai de graça com hardware:** ~30–45
reconexões/h por processo, com `no close frame received or sent`. Enquanto
não tiver causa, entra como ressalva no relato de cobertura de qualquer
rodada de 14 dias.

> ⚠️ **O `close_origem: cliente` que aparece nessas linhas era um DEFEITO do
> registro, não um dado.** `feeds/base.py` fazia
> `"servidor" if rcvd is not None else "cliente"` — carimbava **cliente**
> sempre que não havia frame recebido, **inclusive quando não havia frame
> nenhum**, que é exatamente o caso de `no close frame received or sent` e o
> de um `OSError` de rede. Ninguém fechou pelo protocolo ali: a conexão
> morreu por baixo dele.
>
> Isso sustentou parte da hipótese de CPU saturada que as janelas limpas
> depois derrubaram. **Ausência de frame é ausência de medida** — e um campo
> que finge saber vira gráfico e vira conclusão. Corrigido para três estados
> (`servidor`, `cliente`, `desconhecida`), com teste que falha se voltar.
>
> **Para a investigação, isso muda o ponto de partida:** o suspeito passa a
> ser a camada de transporte — corte de TCP/TLS por intermediário, rede da
> VPS, ou o servidor derrubando sem close frame — e não o nosso laço de
> eventos. Nos registros novos, `close_origem: desconhecida` é o carimbo a
> procurar.

**O que já está DESCARTADO, para ninguém reinvestigar:**

| hipótese | como caiu |
|---|---|
| CPU saturada derruba o feed | contagem solo (15, 14, 8 em 20 min) **igual ou maior** que sob disputa das quatro (8) — §10.1f-ter |
| somos nós reconectando para reassinar | `subscribe`/`unsubscribe` do `poly_ws.py` mandam **frame na conexão viva**, não reconectam. **Cuidado com o que isto NÃO diz:** o giro de mercado continua sendo a causa — só que pelo lado do servidor, ver §10.1f-quater |
| o heartbeat de aplicação estourou | o `_heartbeat` fecha com `code=1000, reason="heartbeat timeout"` e loga `"heartbeat morto: sem PONG"` — as quedas observadas não têm código nem essa linha |

**A próxima medida, depois de a VPS rodar o código novo**, responde duas
coisas de uma vez: a origem verdadeira e se há periodicidade (periodicidade
é assinatura de timeout de intermediário):

```bash
echo "=== de onde partiu a queda ==="
journalctl -u pulsearb-shadow-maker@base --since '-60min' --no-pager \
  | grep 'conexão caiu' | grep -o '"close_origem":"[a-z]*"' | sort | uniq -c

echo "=== os intervalos entre quedas, em segundos ==="
journalctl -u pulsearb-shadow-maker@base --since '-60min' --no-pager -o short-unix \
  | grep 'conexão caiu' | cut -d. -f1 \
  | awk 'NR>1{print $1-a} {a=$1}' | sort -n | uniq -c
```

Leia assim: `desconhecida` na maioria confirma transporte. E se os
intervalos se concentrarem num valor (60 s, 300 s, 3600 s), **é timeout de
intermediário** — aí a saída é keepalive de TCP ou um PING de aplicação mais
frequente, e não tem a ver com o CLOB. Intervalos espalhados apontam para
rede instável, que é outra conversa.

### 10.1f-quater. Achado: é o SERVIDOR que fecha, a cada ~300 s

Medido em `2026-09-20 ~21:30 UTC`, 60 min com a `base` sozinha rodando o
código do #174. **As duas distribuições respondem de uma vez.**

**De onde partiu a queda:**

```
     24 "close_origem":"servidor"
```

**Vinte e quatro de vinte e quatro.** Nenhuma `cliente`, nenhuma
`desconhecida`. O servidor manda frame de close — ele encerra, e avisa.

**Os intervalos entre quedas, em segundos:**

```
     10 0        ← rajada: várias conexões caem no MESMO segundo
      1 1
      1 7
      1 102
      1 204
      1 299   ┐
      2 305   ├─ NOVE intervalos na faixa de 5 minutos
      5 306   ┘
      1 592        ← ~2 × 296: uma queda que não foi registrada no meio
```

**Nove dos treze intervalos não-nulos caem entre 299 e 306 s, e um é o
dobro disso.** Isso é período, não dispersão. E o período é **~300 s — os
cinco minutos exatos do ciclo `updown`**.

**A leitura, e ela inverte o diagnóstico:** o CLOB encerra a conexão quando
o mercado de 5 min acaba. Não é rede instável, não é timeout de
intermediário, não é defeito nosso. **É o ciclo do produto.** As rajadas de
`0 s` são várias conexões caindo no mesmo instante — todas no mesmo
fechamento de mercado.

> ⚠️ **E isto corrige uma frase que eu tinha escrito na tabela acima.** Eu
> havia registrado "o giro de mercado do `updown` a cada 5 min não derruba
> nada", com base em ler que `subscribe`/`unsubscribe` não reconectam. A
> leitura do NOSSO código estava certa; a conclusão sobre o fenômeno estava
> errada, porque eu tinha olhado só um dos dois lados da conexão. **Não
> reconectamos — mas o servidor fecha.** É o mesmo defeito de método que já
> apareceu duas vezes hoje: concluir sobre o que não se mediu.

**O que isto muda para o item da reconexão:**

- **Deixa de ser defeito e vira característica.** ~30–45 quedas/h é o que o
  ciclo de 5 min produz, e nenhuma máquina, rede ou keepalive muda isso.
- **Continua entrando na cobertura**, porque o tempo de reconexão é tempo
  sem livro — mas a ressalva agora tem nome e causa, em vez de ser um
  número solto.
- **A pergunta útil deixou de ser "por que cai" e passou a ser "quanto
  custa".** O que fecha o item é medir o tempo entre a queda e a primeira
  mensagem depois dela, somado na hora: é ISSO que entra no relato de
  cobertura das 14 dias.

**O que ainda falta medir, e são dois números:**

```bash
# 1. o CÓDIGO do close — diz se é encerramento limpo do mercado (1000/1001)
#    ou outra coisa que só parece periódica
journalctl -u pulsearb-shadow-maker@base --since '-60min' --no-pager \
  | grep 'conexão caiu' | grep -o '"close_code":[0-9]*' | sort | uniq -c

# 2. as quedas caem em múltiplos de 300 no relógio? (confirma o ciclo)
journalctl -u pulsearb-shadow-maker@base --since '-60min' --no-pager -o short-unix \
  | grep 'conexão caiu' | cut -d. -f1 | awk '{print $1 % 300}' | sort -n | uniq -c
```

O segundo é o que fecha a causa: se os restos se concentrarem num valor, as
quedas estão **presas ao relógio do mercado**, e não ao tempo de vida da
conexão. Resto espalhado significaria que é a conexão que envelhece — outra
causa, mesmo período aparente.

### 10.1f-quinquies. NÃO é o relógio do mercado — é `1012`, e é a conexão que envelhece

Os dois comandos que o §10.1f-quater deixou pendentes foram rodados
(`2026-09-20 ~22:00 UTC`, 60 min). **Os dois derrubam a conclusão daquela
seção, e o segundo era o teste que eu tinha escrito justamente para isso.**

**O código do close:**

```
     20 "close_code":1012
      4 "close_code":1013
```

Nenhum 1000, nenhum 1001. Pelo RFC 6455 §7.4.1:

| código | o que significa |
|---|---|
| **1012 Service Restart** | o servidor está **reiniciando** — 20 de 24 |
| **1013 Try Again Later** | o servidor está **sobrecarregado** e pede para voltar mais tarde — 4 de 24 |

Fim normal de mercado seria `1000` ou `1001`. **Não é isso que o CLOB manda.**

**Os carimbos módulo 300:**

```
 2 1 · 1 49 · 1 51 · 1 71 · 1 177 · 1 183 · 1 209 · 1 210 · 2 215
 2 221 · 1 226 · 1 232 · 2 283 · 1 288 · 2 289 · 2 293 · 2 295
```

**Espalhados por toda a faixa de 0 a 299.** Se as quedas estivessem presas
ao relógio do mercado, os restos se concentrariam num valor. Não se
concentram.

> ⚠️ **Correção da conclusão do §10.1f-quater.** Aquela seção afirmava que
> "o CLOB encerra a conexão quando o mercado de 5 min acaba". **Está
> errado.** O período de ~300 s é real, mas ele é o **tempo de vida da
> conexão**, não o calendário do mercado — que é exatamente a alternativa
> que o §10.1f-quater nomeou ("conexão que envelhece, outra causa com o
> mesmo período aparente") e mandou distinguir pelo módulo. O teste
> funcionou; a conclusão anterior não sobreviveu a ele.

**O diagnóstico que fica:** a borda do CLOB recicla conexões por idade, com
vida de ~5 min, e anuncia isso como `1012 Service Restart`. A fase é a de
cada conexão, por isso não alinha com o relógio. Quatro vezes por hora ela
está sobrecarregada e manda `1013`.

**Nada disso é nosso defeito, e nada disso melhora com máquina maior** — as
duas conclusões do §10.1f-ter seguem de pé. Mas apareceu uma coisa que É
nossa, abaixo.

#### O que ERA nosso: respondíamos `1013` em meio segundo

O laço de `_run` zera o backoff a cada conexão boa (`backoff =
self.reconnect_initial_seconds`), e **não olhava o código do close**. Como
cada conexão vive ~5 min e conecta bem, o backoff estava sempre em 0,5 s
quando a queda chegava. Resultado: nas quatro vezes em que o servidor disse
**"estou sobrecarregado, volte mais tarde"**, nós voltamos em meio segundo.

Corrigido com um **piso de espera por código**, e a assimetria é o ponto:

| código | piso | por quê |
|---|---|---|
| `1013` Try Again Later | **5 s** | o servidor pediu paciência; insistir é ignorar o pedido |
| `1012` Service Restart | **5 s**, dobrando | o padrão diz que o serviço ESTÁ REINICIANDO e pede 5–30 s aleatórios |
| qualquer um, fechado por NÓS | **nenhum** | `_derrubar_por_recusa` e `_escalar_se_sem_efeito` fecham para reconectar rápido |

É `max(backoff, piso)`, não substituição: depois de muitas quedas seguidas
o backoff exponencial já passa do piso, e trocar por ele deixaria a
reconexão **mais** agressiva justamente quando o servidor está pior. E o
piso vale **depois** do jitter, não antes: multiplicado por `[0.5, 1.5)`,
um piso de 5 s deixava metade das voltas dormindo menos que o mínimo.

**A escalada conta QUEDAS, não o backoff, e a razão é um erro que eu havia
escrito aqui.** A versão anterior desta seção prometia que um segundo
`1013` seguido esperaria 10 s. Não esperava: o laço faz `backoff =
reconnect_initial_seconds` a cada conexão **bem sucedida**, e o servidor
aceita a conexão antes de fechá-la — então o backoff voltava a 0,5 s antes
de cada queda e o piso o levava a 5 s toda vez. Um contador próprio
(`pedidos_de_paciencia_seguidos`) sobrevive ao reset: 5 s, 10 s, 20 s, até
o `reconnect_max_seconds`. Zera em qualquer queda que não seja pedido de
paciência. Os
dois testes fixam as duas coisas, e o primeiro foi verificado por mutação.

> ⚠️ **O DIAGNÓSTICO ACIMA ESTÁ SOB SUSPEITA, e a suspeita é minha.** A
> revisão do #177 achou que `_registrar_queda` atribuía a origem por
> `"servidor" if rcvd is not None`. Num close que **NÓS** iniciamos o
> servidor responde com o frame dele, então `rcvd` existe — e a queda saía
> carimbada **servidor**. E nós fechamos com **1012 de propósito** em dois
> pontos (`_derrubar_por_recusa` e `_escalar_se_sem_efeito`).
>
> Ou seja: parte das "20 de 24 com 1012 do servidor" pode ter sido nossa.
> Corrigido com `rcvd_then_sent`, que é o atributo do `websockets` que diz
> quem mandou o frame primeiro — **terceira vez que este mesmo campo diz
> saber o que não sabe**.
>
> **O teste que decide, e não precisa de código novo:** as duas quedas
> nossas carregam razão própria no frame.
>
> ```bash
> journalctl -u pulsearb-shadow-maker@base --since '-60min' --no-pager \
>   | grep 'conexão caiu' | grep -o '"close_reason":"[^"]*"' | sort | uniq -c
>
> journalctl -u pulsearb-shadow-maker@base --since '-60min' --no-pager \
>   | grep -c 'derrubando a conexão'
> ```
>
> `topico mudo apos reassinaturas` ou `assinatura recusada pelo servidor` na
> primeira saída, ou qualquer número acima de zero na segunda, e as quedas
> são nossas. Vazio nas duas e o diagnóstico do ciclo do servidor se
> sustenta.

**O que segue em aberto:** o custo. ~24 quedas/h × tempo até a primeira
mensagem depois de cada uma = tempo sem livro por hora, e é esse número que
entra no relato de cobertura das 14 dias. Ainda não foi medido.

### 10.1f-sexies. ERA NOSSO: 22 das 30 quedas são o `_derrubar_por_recusa`

O `grep` em `close_reason` foi rodado (`2026-09-20 ~22:20 UTC`, 60 min) e
**derruba o diagnóstico das duas seções anteriores**:

```
     22 "close_reason":"assinatura recusada pelo servidor"
      8 "close_reason":"slow consumer: send buffer full"
     22   ← grep -c 'derrubando a conexão'
```

| razão | de quem | o que é |
|---|---|---|
| `assinatura recusada pelo servidor` — **22** | **NOSSA** | `_derrubar_por_recusa`, que fecha com 1012 de propósito quando `_recusa_de_assinatura()` acusa. As 22 linhas de `derrubando a conexão` confirmam uma a uma |
| `slow consumer: send buffer full` — **8** | **do servidor** | já documentado em `live/shadow.py:340`, medido em 2026-09-14: acontece **com consumidor vazio** e no mesmo instante em três processos paralelos, então **não é lentidão nossa** |

**O que cai:**

- ❌ "24 de 24 `close_origem: servidor`" — era o carimbo quebrado. A maioria
  das quedas foi nossa.
- ❌ "o CLOB recicla conexões por idade e anuncia como `1012`" — os `1012`
  eram **os nossos**, mandados pelo `_derrubar_por_recusa`.
- ❌ O período de ~300 s como "tempo de vida da conexão". Ele continua a ser
  explicado, mas agora sobre outro fenômeno.

**O que fica de pé:** que não é CPU (§10.1f-ter, janelas limpas), e que os
`slow consumer` do `clob[updown]` são do servidor e conhecidos.

**O fenômeno de verdade, e ele tem nome desde sempre:** o servidor recusa a
nossa assinatura **22 vezes por hora**, e nós respondemos derrubando a
conexão para refazê-la do zero — que é o comportamento que o §6 do
`API_NOTES` descreve e que o contador `reconexoes_por_recusa` existe para
medir. **Não é um defeito de reconexão. É um defeito de assinatura, com a
reconexão como sintoma.**

**Um detalhe que muda onde procurar:** `_recusa_de_assinatura` é
implementado em `feeds/rtds.py`, não no `poly_ws`. Então as 22 quedas são
provavelmente do **RTDS**, e não do CLOB — e toda a conversa das seções
anteriores tratou as quedas como se fossem de um feed só. **Isto ainda não
está medido.**

**A próxima medida, e ela separa os feeds:**

```bash
echo "=== quedas por conexão ==="
journalctl -u pulsearb-shadow-maker@base --since '-60min' --no-pager \
  | grep 'conexão caiu' | grep -o '"conexao":"[^"]*"' | sort | uniq -c

echo "=== e o que o servidor respondeu à assinatura ==="
journalctl -u pulsearb-shadow-maker@base --since '-60min' --no-pager \
  | grep 'servidor recusou a assinatura' | tail -3
```

A segunda saída traz o campo `motivo`, que é a recusa crua. É ela que diz
se é o `500 __subscriptions does not exist` já conhecido (§6 do
`API_NOTES`, "o servidor quebrado do lado dele") ou algo novo.

### 10.1f-septies. A causa tem nome: `42P01` no banco do RTDS

Medido em `2026-09-20 ~23:20 UTC`, 60 min, com o código do #178.

**Por conexão** (duas amostras seguidas):

```
      8 "conexao":"clob[updown]"       ← os `slow consumer`, do servidor, conhecidos
     13 "conexao":"rtds[shadow:0]"
     12 "conexao":"rtds[shadow:1]"
```

**Não é o CLOB. São as duas conexões do RTDS**, ~25 quedas/h somadas. Todas
as seções anteriores trataram as quedas como se fossem de um feed só, e
metade do raciocínio foi feito olhando o feed errado.

**E a recusa crua, verbatim:**

```
statusCode 500: leger AddSubscriptions error: rpc error: code = Internal
desc = ERROR #42P01 relation "__subscriptions" does not exist
```

`42P01` é `undefined_table` do PostgreSQL. **A tabela de assinaturas não
existe no banco do RTDS** — é o servidor quebrado do lado deles, e o
`API_NOTES` §6.2b já registrava exatamente esta mensagem desde a gravação
M2_72H de **2026-09-09**. Ela está em `tests/fixtures/rtds_recusas_reais.json`
como `recusa_500`.

**Então a cadeia inteira é esta:** o RTDS responde 500 → `e_de_assinatura`
classifica como recusa → `_derrubar_por_recusa` fecha a conexão com 1012 →
o carimbo (antes de ser consertado) dizia "servidor" → eu li como "o CLOB
recicla conexões". **Nenhum elo era o que eu disse que era, e o primeiro é
um defeito do servidor deles, de onze dias atrás.**

#### A pergunta que sobra, e ela NÃO está respondida

Derrubar a conexão a cada 500 resolve alguma coisa? O argumento a favor:
se a tabela não existe, a assinatura não se registra, o tópico vai calar —
então derrubar e refazer é a única coisa que ainda muda algo, que é o
raciocínio escrito em `_registrar_erro_do_servidor` e fixado no teste
`test_as_duas_recusas_reais_pedem_derrubar`.

O argumento contra: **`42P01` é determinístico**. Reconectar não cria
tabela, e entre uma recusa e outra o feed continua entregando dado — o que
sugere que a assinatura inicial funciona e só a REASSINATURA periódica bate
no caminho quebrado. Se for isso, estamos derrubando 25 conexões saudáveis
por hora por causa de um seguro que falhou.

**Não dá para decidir isso pelo log**, e é por isso que este runbook não
traz a mudança: seria trocar uma decisão deliberada, testada e ancorada em
fixture real por uma preferência minha. **A segunda linha de defesa já
existe** — se a assinatura caducar de verdade, o tópico cala,
`_reassinatura_urgente` detecta e `_escalar_se_sem_efeito` derruba.

**O ensaio que decide, e ele precisa de uma rodada de teste, não da que
vale:** subir uma instância com `PULSEARB_RTDS_REASSINATURA_INTERVALO_S`
desligado (ou muito longo), de forma que a reassinatura periódica não
rode, e medir por uma hora:

```bash
journalctl -u <unidade-de-teste> --since '-60min' --no-pager \
  | grep -c 'conexão caiu'
journalctl -u <unidade-de-teste> --since '-60min' --no-pager \
  | grep -c 'tópico mudo com a conexão viva'
```

Se as quedas sumirem **e** o tópico não calar, o 500 vinha só da
reassinatura e derrubar por causa dele é dano puro. Se o tópico calar, a
decisão atual está certa e o item fecha como limitação do servidor.

**Enquanto isso, o custo entra na cobertura:** ~25 quedas/h × o tempo até a
primeira mensagem depois de cada uma. Esse número continua sem medida, e é
ele que precisa aparecer no relato das 14 dias.

### 10.1f-octies. O custo da reconexão: 23,6 s/h — LINHA DE BASE

Medido em `2026-09-21 ~01:37 UTC`, 60 min. **É o código ANTERIOR ao #177**:
a janela cobre 00:37–01:37 e o restart com o código novo foi às 01:36, então
cinquenta e nove dos sessenta minutos são do comportamento antigo.

| conexão | quedas | tempo sem livro |
|---|---|---|
| `clob[updown]` | 7 | 9,2 s |
| `rtds[shadow:0]` | 12 | 7,3 s |
| `rtds[shadow:1]` | 11 | 7,1 s |
| **hora inteira** | **30** | **23,6 s = 0,65% da hora** |

Média de 0,79 s por reconexão, pior caso 5,2 s.

**O que este número é:** limite inferior. Conta da queda até o `conectado`,
e depois ainda há a ida e volta da assinatura antes de o dado voltar.

**O que ele NÃO é:** o comportamento atual. O #177 pôs piso de 5 s nas
quedas em que o SERVIDOR fecha com 1012/1013. Das 30 quedas, ~22 são nossas
(piso zero) e ~8 são `1013 slow consumer` do servidor no `clob[updown]`
(piso 5 s). **Previsão, não medida:** o tempo sem livro sobe para ~70 s/h,
uns 2% da hora — o triplo. Se isso é bom troco, ainda não se sabe: a perda
é no `updown`, que tem conexão própria justamente para não levar o livro
dos pools junto, e é o livro dos pools que o maker cota.

**Para fechar, repita a mesma conta com a hora inteira no código novo.** O
comando está em `/root/custo_reconexao.sh` na VPS; a saída vai para
`/root/custo_reconexao.txt`.

```bash
journalctl -u pulsearb-shadow-maker@base --since '-60min' --no-pager -o short-unix \
  | grep -E 'conexão caiu|"msg": *"conectado"' \
  | awk '{
      t = $1 + 0
      conexao = "?"
      if (match($0, /"conexao": *"[^"]*"/)) conexao = substr($0, RSTART, RLENGTH)
      if ($0 ~ /conexão caiu/) { caiu[conexao] = t }
      else if (conexao in caiu) {
          d = t - caiu[conexao]; delete caiu[conexao]
          total += d; n++; soma[conexao] += d; vezes[conexao]++
          if (d > pior) pior = d
      }
  } END {
      for (c in soma) printf "%-28s %3d quedas, %7.1f s\n", c, vezes[c], soma[c]
      printf "\n%d reconexões, %.1f s (%.2f%% da hora), pior %.1f s\n",
             n, total, total/36, pior
  }'
```

### 10.1g. Medir a capacidade NÃO inicia o ensaio — os artefatos vão fora

Achado P1 do Codex na revisão do #170, e ao conferir no código ele é pior do
que o relatado. **Quem passar direto da medida de capacidade para o ensaio
de 14 dias soma dado inválido no resultado, em silêncio.**

**O mecanismo, verificado na fonte.** A unit passa um caminho FIXO
(`ExecStart … --diario data/diarios/shadow-maker-%i.jsonl`), então a
unicidade por `O_EXCL` de `caminho_do_diario_da_rodada` **não vale aqui** —
ela é do caminho default. Com `--diario` explícito o `_anotar` abre em
**append**. E o leitor (`scripts/resumo_da_rodada_maker.py`) corta o diário
em trechos onde `parede_s` CAI, uma subida do processo por trecho, e
**SOMA os trechos** — de propósito, para que um fill perdido num trecho
morto não suma da ressalva.

Junte as duas: reiniciar a `base` no mesmo arquivo cria um trecho novo que é
**somado** às ~6 h de quatro processos que já estão lá — as mesmas 6 h que
o §10.1e declarou inválidas. O número final sai contaminado sem nenhum campo
dizendo isso. E a `pausa`, parada e religada, carrega um buraco que a `base`
não tem: **cobertura desigual entre rodadas**, que é justamente o que o
§10.1c existe para impedir.

**Então, depois da medida de capacidade e ANTES do ensaio valer:**

```bash
cd /opt/pulsearb
sudo systemctl stop 'pulsearb-shadow-maker@*'

# AFASTE, não apague: estes arquivos são a prova da medida de capacidade.
q=data/invalidado-$(date -u +%Y%m%dT%H%M%SZ)
sudo -u pulsearb mkdir -p "$q"
sudo -u pulsearb mv data/diarios/shadow-maker-*.jsonl "$q"/ 2>/dev/null || true
sudo -u pulsearb mv data/risco/registro_maker_*.json  "$q"/ 2>/dev/null || true
ls -l "$q"

# PRAZO NOVO, e o mesmo para todas as que subirem.
FIM=$(date -u -d '+14 days' +%Y-%m-%dT%H:%M:%SZ)
sudo sed -i "s|^PULSEARB_RODADA_TERMINA_EM=.*|PULSEARB_RODADA_TERMINA_EM=$FIM|" \
    deploy/rodadas/comum.env
grep '^PULSEARB_RODADA_TERMINA_EM=' deploy/rodadas/comum.env    # confira

sudo systemctl daemon-reload
REGRA=pausa          # a regra DESTA janela — ver abaixo, são três
for r in base "$REGRA"; do sudo systemctl start pulsearb-shadow-maker@$r; done

# O relógio só começou se os diários nasceram AGORA e vazios.
sleep 20 && ls -l data/diarios/

# E o PORTÃO tem de estar aberto. Acrescentado em 2026-09-21 (§10.1l): a
# conferência acima olhava só os diários, e uma rodada com o disjuntor
# armado passava por ela cotando ZERO em silêncio por dois dias.
for r in base pausa ancora recolher; do
  f=data/risco/registro_maker_$r.shadow.json
  [ -f "$f" ] || continue
  python3 -c "
import json, sys
d = json.load(open('$f'))
if d.get('disjuntor_armado'):
    print('DISJUNTOR ARMADO em $r:', d.get('disjuntor_motivo'))
    sys.exit(1)
print('$r: portao aberto')
"
done
```

O registro de risco vai junto porque o ensaio do 4.2 sobe com registro
limpo — é o procedimento das rodadas r7 em diante, e um registro com
exposição herdada da medida de capacidade faria o portão recusar por um
motivo que não é do ensaio.

**A conferência que fecha isto são DUAS**, e a segunda entrou em
2026-09-21 porque a primeira sozinha deixou passar uma rodada que não cotava
(§10.1l): o laço acima não pode imprimir nenhum `DISJUNTOR ARMADO`. E
`ls -l data/diarios/` logo depois tem de
mostrar arquivos novos e pequenos. Diário grande ali é diário antigo que não
foi afastado — e o resultado de 14 dias sairia somado com a medida de
capacidade.

### 10.1h. O plano B são TRÊS janelas de 14 dias, não uma

Achado P1 do Codex, quarta rodada do #170. A primeira versão do §10.1g subia
`base` e `pausa` e parava por aí — e `recolher` e `ancora`, paradas na medida
de capacidade, **nunca voltavam a ser agendadas**. Seguir aquele
procedimento daria resultado de 14 dias para **uma** das três regras e
deixaria o 4.2 incompleto, sem nada no runbook dizendo que faltava.

O §10.1c define **três** regras experimentais (`recolher`, `ancora`,
`pausa`), e cada uma precisa de uma `base` correndo no MESMO intervalo. Duas
por vez, portanto, são **três janelas**:

| janela | sobe | dias |
|---|---|---|
| 1 | `base` + `pausa` | 14 |
| 2 | `base` + `ancora` | 14 |
| 3 | `base` + `recolher` | 14 |
| | | **42 no total** |

**Cada janela repete o §10.1g inteiro** — afastar diários e registros,
prazo novo, conferir que os diários nasceram vazios — trocando só o
`REGRA=`. A `base` de cada janela é uma base NOVA, e é isso que faz a
comparação valer: a regra é medida contra um controle do seu próprio
intervalo de mercado.

**O que o plano B dá e o que ele NÃO dá.** Dá cada regra contra a sua
própria base, que é a comparação que o 4.2 exige e é o motivo de o §10.1c
aceitar esta saída. **Não dá regra contra regra:** `pausa` e `recolher`
terão corrido com 28 dias de distância, e ranquear uma contra a outra
mediria o mercado, não a regra — a mesma confusão que o §10.1c recusa,
apenas empurrada para um nível acima.

**A escolha era esta — e era sua, não minha** (decidida em 2026-09-21
contra a máquina maior; ver §10.1j):

| | plano B nesta VPS | máquina maior |
|---|---|---|
| tempo | **42 dias** | **14 dias** |
| custo | zero | o preço da VPS por ~1 mês |
| regra vs. base | ✅ limpo nas três | ✅ limpo nas três |
| regra vs. regra | ❌ 28 dias de distância | ✅ mesmo intervalo |

E as duas dependem do ensaio de duas rodadas do §10.1f passar: se ele
reprovar, o plano B cai junto e sobra só a máquina maior.

### 10.1i. O custo das reconexões no código novo — MEDIDO em 2026-09-21

Primeira medida do custo de reconexão **depois** do #177/#178 (piso e
escalada). A linha de base, código antigo, está no §10.1f: 30 reconexões,
23,6 s sem livro, 0,65% da hora, pior 5,2 s.

Uma hora de `pulsearb-shadow-maker@base`, 1 vCPU, quatro rodadas no ar:

| conexão | quedas | s sem livro | média |
|---|---|---|---|
| `clob[updown]` | 3 | **100,7** | 33,6 s |
| `rtds[shadow:0]` | 11 | 7,4 | 0,67 s |
| `rtds[shadow:1]` | 13 | 8,6 | 0,66 s |
| **total** | **27** | **116,8** | 4,32 s |

**3,24% da hora, pior buraco 37,1 s.**

**O que a média esconde.** 86% do custo veio de TRÊS quedas. As 24 quedas do
RTDS custaram 16 s — 0,67 s de média, contra 0,79 s da linha de base. O
escopo que o #178 testou vale em produção: o 1012 que **nós** mandamos em
`_derrubar_por_recusa` tem piso zero, então o conserto do RTDS não pagou
pedágio nenhum.

**A previsão errou.** Estava escrito, antes da medida, ~70 s/h. Veio 116,8
s/h — 67% a mais — e errou sobretudo o LUGAR: previa o custo espalhado pelas
quedas do RTDS, e ele se concentrou no CLOB.

**O que o CLOB disse.** As três quedas são uma rajada de 3,3 min e trazem
todas o mesmo carimbo:

```
"close_code":1013 "close_origem":"servidor" "close_reason":"slow consumer: send buffer full"
```

O 1013 do RFC 6455 quer dizer "estou sobrecarregado"; o `reason` fala em
buffer de envio cheio. Esperar 30 s não conserta isso: a conexão aguentou
24,7 s e depois 70,0 s antes de cair outra vez.

> ❌ **CORRIGIDO em 2026-09-21.** A primeira versão deste parágrafo dizia que
> o buffer enchia **porque o nosso cliente não drenava o socket**, e chamava
> isso de "a starvation de CPU do §10.1f aparecendo na ponta do WebSocket".
> **Está errado, e o projeto já tinha a medida que o refuta** — de
> 2026-09-14, escrita em `live/shadow.py` junto de `clob[updown]`: a queda
> **acontece igual com consumidor vazio**, e no mesmo instante em três
> processos paralelos. Não é o nosso processo. O que a causa é o tráfego dos
> Up/Down: **dois mercados de 5 min são 54% do tráfego, em rajadas de
> 4 MB/s**, contra 50–120 msg/s dos tokens de pool, que nunca caíram.
>
> Eu deduzi a causa do nome do campo em vez de procurar o que já estava
> medido — o erro que o CLAUDE.md chama de "fato de API assumido a partir do
> que parecia razoável". O conserto do #181 (reset da escalada por saúde)
> **não depende disto e continua de pé**: ele foi medido contra o sono real,
> não contra a causa da queda.

**O número que vale para a rota maker não é 3,24%.** Durante a rajada foram
**100,7 s cegos em 195,6 s de relógio — 51% do tempo sem livro do
`updown`**, com um buraco de 37,1 s. Um maker com ordens no livro nesses 37 s
não sabe o que aconteceu com elas.

#### O defeito que a janela de 4 h revelou

Nove quedas de `clob[updown]` em quatro horas, e o sono que veio depois de
cada uma:

| queda | saúde antes | dormiu |
|---|---|---|
| +0,00 min | — | 31,9 s |
| +34,64 min | **34,6 min** | 30,9 s |
| +51,39 min | 16,2 min | 32,4 s |
| +52,53 min | 0,6 min | 35,0 s |
| +59,33 min | 6,2 min | 41,7 s |
| +62,05 min | 2,0 min | 42,0 s |
| +139,19 min | **77,1 min** | 32,3 s |
| +140,14 min | 0,4 min | 31,3 s |
| +141,82 min | 1,2 min | 37,1 s |

Nenhum sono abaixo de 30 s. Com `reconnect_max_seconds: 30`, a faixa de
sorteio `[30, 45)` tem média 37,5 — ou seja, o contador
`pedidos_de_paciencia_seguidos` **já estava no teto** e nunca mais desceu.
Uma conexão que trabalhou 77,1 min, caiu uma vez, e esperou 32,3 s.

`_piso_da_volta` só zerava numa queda SEM piso. **Nada media o "seguidos" do
nome.** Consertado com `SAUDE_QUE_ZERA_A_ESCALADA_SEGUNDOS` (60 s, acima do
teto de propósito) e dois testes verificados por mutação. A medida decide só
os extremos — 34,6 min e 77,1 min têm de zerar, 24,7 s não pode zerar; o
caso de 70,0 s é escolha, e a constante o põe do lado que zera.

**O que NÃO foi mexido, e por quê.** O piso, o teto e a tabela de códigos
ficam como estão. A causa do 1013 aqui é literalmente a lentidão desta
máquina, que já está medida e condenada no §10.1f. Calibrar a política de
backoff com o número produzido pela causa que vamos remover seria consertar
o sintoma com o dado contaminado. O reset por saúde é outra coisa: é defeito
de mecanismo, e a evidência (77,1 min ainda no teto) não depende de CPU
nenhuma.

#### Repetir na máquina de 4 vCPU

Com os mesmos comandos, uma hora limpa:

```bash
journalctl -u pulsearb-shadow-maker@base --since '-60min' --no-pager -o short-unix \
  | grep -E 'conexão caiu|"msg": *"conectado"' \
  | awk '{
      t = $1 + 0
      conexao = "?"
      if (match($0, /"conexao": *"[^"]*"/)) conexao = substr($0, RSTART, RLENGTH)
      if ($0 ~ /conexão caiu/) { caiu[conexao] = t }
      else if (conexao in caiu) {
          d = t - caiu[conexao]; delete caiu[conexao]
          total += d; n++; soma[conexao] += d; vezes[conexao]++
          if (d > pior) pior = d
      }
  } END {
      for (c in soma) printf "%-28s %3d quedas, %7.1f s sem livro\n", c, vezes[c], soma[c]
      printf "%d reconexões, %.1f s sem livro (%.2f%% da hora), pior %.1f s\n",
             n, total, total/36, pior
  }'
```

**O que a repetição decide:** se o `slow consumer` sumir, ele era CPU e a
política de backoff nunca foi o assunto. Se persistir em 4 vCPU, aí a
política volta à mesa — e o suspeito seguinte é o teto de 30 s, não o piso.

### 10.1j. A máquina maior foi recusada — o que sobra para o 4.2

Decisão do operador em **2026-09-21**: a VPS não será aumentada. Isto não é
uma revisão da medida do §10.1f; a medida continua de pé. É uma restrição
nova, e ela fecha, de uma vez, os três caminhos que o §10.1f listou:

| caminho | estado |
|---|---|
| quatro rodadas em paralelo, 14 dias | ❌ `id=0`, `r=4` — medido (§10.1e) |
| plano B: `base` + 1 regra, três janelas | ❌ `%wait` 0,38% → 16,70% → 35,97% (§10.1f) |
| VPS de 4 vCPU | ❌ **recusada pelo operador** |
| uma rodada por vez, 56 dias | ❌ o §10.1c recusa: compara regra com mercado |

**Dito sem eufemismo: o 4.2, do jeito que está especificado, não tem caminho
em 1 vCPU.** Quem ler este runbook procurando como rodá-lo nesta máquina tem
de encontrar esta frase, não uma sequência de passos que termina em dado
inválido.

#### O que NÃO estava na tabela do §10.1f

A tabela tratou "a rodada" como um bloco de tamanho fixo. Ela não é. O
`comum.env` cota em `PULSEARB_TOP_DE_POOLS_DE_REWARD=60`, e é esse número
que compra os ~40% de núcleo por rodada.

Baixá-lo **nas duas rodadas da janela** não quebra o que o 4.2 exige. O
próprio `comum.env` diz por quê: o perfil tem de ser igual **entre** as
rodadas, porque o que se compara é a REGRA. Um perfil menor, igual nas duas,
no mesmo intervalo de mercado, mantém a regra medida contra um controle
contemporâneo — que é a condição do §10.1c, e a única que o plano B
conseguia cumprir.

**O preço disso, escrito antes de pagá-lo:**

1. **O veredito muda de escopo.** O 4.2 passaria a dizer "a regra ajuda nos
   N melhores pools de reward", não nos 60. Isso precisa sair no relatório,
   não só aqui.
2. **Menos pools, menos cotações.** Catorze dias podem não carregar o mesmo
   peso estatístico. A conta sai dos diários das rodadas r4–r8, no Mac:
   cotações por pool por dia. **Não medido** — e é o que decide se a janela
   continua sendo de 14 dias ou precisa ser maior.
3. **Não está medido que 20 pools cabem.** A relação entre número de pools e
   CPU pode não ser linear — descoberta, assinaturas e livro escalam
   diferente do laço de cotação.

#### A medida de 20 minutos que decide

Custa zero e separa três futuros. É `%wait`, a mesma coluna que reprovou o
plano B, com a régua do §10.1f (sozinha 0,38%; com a `pausa` a 60 pools
16,70% e 35,97%):

```bash
cd /opt/pulsearb
sudo systemctl stop 'pulsearb-shadow-maker@*'

# TEMPORÁRIO — isto é medida, não ensaio. Não commitar ainda.
sudo sed -i 's/^PULSEARB_TOP_DE_POOLS_DE_REWARD=.*/PULSEARB_TOP_DE_POOLS_DE_REWARD=20/' \
    deploy/rodadas/comum.env
FIM=$(date -u -d '+1 day' +%Y-%m-%dT%H:%M:%SZ)
sudo sed -i "s|^PULSEARB_RODADA_TERMINA_EM=.*|PULSEARB_RODADA_TERMINA_EM=$FIM|" \
    deploy/rodadas/comum.env
sudo systemctl daemon-reload
for r in base pausa; do sudo systemctl start pulsearb-shadow-maker@$r; done

sleep 180   # estabilizar
PID=$(systemctl show -p MainPID --value pulsearb-shadow-maker@base)
pidstat -u -p $PID 5 24
```

| `%wait` previsto | o que significaria |
|---|---|
| ~0,4% | caberia. 4.2 em três janelas de 14 dias, escopo reduzido, custo zero |
| 15–35% | 20 pools não resolveu, e a conta não é linear — sobra a investigação abaixo |
| entre os dois | não decidiria sozinho; precisaria de um segundo ponto (ex.: 35 pools) |

> **Rodou em 2026-09-21 e caiu na segunda linha: 28,59%.** A tabela fica
> como foi escrita, ANTES do resultado — é assim que se vê que a leitura não
> foi ajustada depois de conhecer o número. O veredito está logo abaixo.

**Depois da medida, o §10.1g vale inteiro** — afastar diários e registros,
prazo novo, conferir que nasceram vazios. Medir capacidade não inicia
ensaio, e esta medida suja os diários como qualquer outra.

#### ❌ A medida rodou, e 20 pools REPROVOU — 2026-09-21

`base` + `pausa`, `PULSEARB_TOP_DE_POOLS_DE_REWARD=20`, 24 amostras de 5 s:

| `%wait` da `base` | mínimo | máximo | média |
|---|---|---|---|
| sozinha, 60 pools (§10.1f) | 0,00 | 1,20 | **0,38%** |
| com a `pausa`, 60 pools (§10.1f) | 23,20 | 50,20 | **35,97%** |
| **com a `pausa`, 20 pools** | 3,80 | 47,00 | **28,59%** |

**A variável pegou** — conferido, não suposto: `"msg":"descoberta de
pools","janelas":20`. (`systemctl show -p Environment` vir vazio é esperado
e não desmente nada: ele só lista diretivas `Environment=`, e o valor vem do
`EnvironmentFile`, que é o motivo de o `comum.env` existir.)

**Três vezes menos pools, o mesmo custo:** `%CPU` 38,35% contra os ~40%
medidos a 60. A proposta desta seção — encolher o escopo para caber —
**está morta, e teria custado um veredito mais estreito por nada.**

#### O que a reprovação ensinou, e que a proposta errada não sabia

Duas leituras do mesmo `pidstat`, que juntas mudam o alvo:

1. **O custo não escala com pools.** Não está no laço que percorre mercados.
2. **`%usr` 36,07% contra `%system` 2,27%.** É cálculo em Python no espaço
   de usuário — não syscall, não I/O, não rede.

Sobra o que acontece **por evento de livro**, não por mercado. E aí entra o
que a tabela do §10.1f também não via: `base` e `pausa` são dois processos
separados que descobrem os MESMOS pools, assinam os MESMOS tokens e
processam o MESMO livro, cada um por conta própria. **O trabalho caro é
feito duas vezes.** É também a explicação de por que o `id` do `vmstat`
nunca pegou a contenção que o `%wait` pegou: as duas acordam no mesmo evento
e disputam o mesmo milissegundo.

**A hipótese que isso levanta** — um processo, uma assinatura de livro, as
regras em paralelo dentro dele — sairia de graça em hardware e em escopo, e
deixaria a comparação MAIS limpa que hoje, porque as variantes veriam byte a
byte a mesma entrada. **Mas é hipótese**, e a hipótese anterior desta mesma
seção (encolher os pools) reprovou. Não se reescreve a topologia do ensaio
por dedução.

#### A medida que decide: perguntar ao processo

```bash
/opt/pulsearb/.venv/bin/python -m pip install py-spy
PID=$(systemctl show -p MainPID --value pulsearb-shadow-maker@base)
/opt/pulsearb/.venv/bin/py-spy top --pid $PID --duration 60 --nonblocking
```

| topo do perfil | o que significa |
|---|---|
| parsing de livro nos feeds | a hipótese está certa: um processo com feed compartilhado é o caminho |
| motor de cotação / decisão | a hipótese está errada; o custo é por mercado de outra forma, e o alvo é outro |
| espalhado, sem topo claro | não há alvo — e aí o 4.2 fica parado nesta máquina, sem eufemismo |

#### O alvo que apareceu ao ler o código, e que vale mais que a hipótese

Lendo `live/shadow.py` para interpretar o perfil, apareceu o que nenhuma das
medidas anteriores tinha olhado: **cada rodada carrega a rota taker
inteira**, e ela não é opt-in.

| o que sobe | condicional? |
|---|---|
| `self.poly.start()` — conexão `clob[updown]` | ❌ incondicional |
| `laco_de_descoberta` (mercados do taker) | ❌ incondicional |
| `laco_de_decisao` (`passo()` do taker) | ❌ incondicional |
| `self.poly_pools.start()` + `laco_de_descoberta_de_pools` | ✅ opt-in |

E o `clob[updown]` é, pela medida de 2026-09-14 gravada no próprio código,
**54% do tráfego, em rajadas de 4 MB/s**, contra 50–120 msg/s dos pools.

**Junte com a reprovação dos 20 pools e ela para de ser um mistério:**
`TOP_DE_POOLS_DE_REWARD` mexe só na conexão `clob[pools]`. A medida cortou a
metade PEQUENA do tráfego e deixou a grande intacta. Que o custo não tenha
mudado é, agora, o resultado esperado — e a minha leitura de que "o custo
não está no processamento de livro" foi longe demais: ela não foi testada.

**O que isso significa para o 4.2:** as duas rodadas pagam a rota taker
inteira — a conexão mais pesada da casa — **para medir a rota maker**. E o
taker está medido e REPROVADO (quadro 1.1/1.4/1.5, −195,25 USDC em 2.069
trades). É custo pago por um dado que já existe e já reprovou.

#### A medida que decide isto, sem tocar em código

Comparar a MESMA rodada, sozinha, com e sem a rota de pools. Os dois números
separam o custo do taker do custo do maker:

```bash
cd /opt/pulsearb
medir() {
  sudo systemctl stop 'pulsearb-shadow-maker@*'
  sudo sed -i "s/^PULSEARB_DESCOBRIR_POOLS_DE_REWARD=.*/PULSEARB_DESCOBRIR_POOLS_DE_REWARD=$1/" \
      deploy/rodadas/comum.env
  sudo systemctl daemon-reload
  sudo systemctl start pulsearb-shadow-maker@base
  sleep 180
  PID=$(systemctl show -p MainPID --value pulsearb-shadow-maker@base)
  echo "=== pools=$1 ==="
  pidstat -u -p $PID 5 24 | tail -1
}
medir true
medir false
```

| leitura | o que significa |
|---|---|
| `pools=false` ≈ `pools=true` | o taker é quase todo o custo. Uma rodada **maker-only** seria barata, e o 4.2 volta a caber em 1 vCPU — ao preço de escrever a trava que desliga a rota taker |
| `pools=false` ≪ `pools=true` | o maker é que é caro; desligar o taker não salva, e o alvo volta a ser o laço de cotação |
| os dois altos e parecidos com `%CPU` ~40% | o custo é fixo e não é de nenhuma das rotas — feeds de base, RTDS, relógio |

**Ao terminar, o `comum.env` tem de voltar ao que está versionado**
(`PULSEARB_DESCOBRIR_POOLS_DE_REWARD=true`,
`PULSEARB_TOP_DE_POOLS_DE_REWARD=60`), e o §10.1g vale inteiro antes de
qualquer ensaio valer.

#### ✅ A medida rodou — a rota maker custa 2% de um núcleo — 2026-09-21

`base` sozinha, 24 amostras de 5 s em cada regime:

| regime | `%usr` | `%CPU` | `%wait` |
|---|---|---|---|
| `pools=true` (taker + maker) | 37,55 | **39,87** | 0,22 |
| `pools=false` (só taker) | 35,89 | **37,88** | 0,18 |
| **diferença = a rota maker inteira** | 1,66 | **1,99** | — |

**A rota que o item 4.2 existe para medir — conexão `clob[pools]` própria,
descoberta de pools e laço de cotação — custa 1,99 ponto percentual.** Os
outros ~37,9% são o que sobe sem ninguém pedir.

**E a leitura que eu tinha escrito para esta linha estava errada.** A tabela
acima dizia, para `pools=false ≈ pools=true`: "o taker é quase todo o
custo". Não segue. Os 37,9% são taker **mais** RTDS **mais** maquinaria fixa,
e esta medida não os separa. O que ela prova é o outro lado: **o maker é
barato**, que é uma afirmação diferente e mais útil.

#### O que a aritmética passa a dizer

As quatro rodadas do 4.2 diferem em **três escalares** e no caminho do
registro de risco — nada mais (`deploy/rodadas/*.env`):

| rodada | `RECOLHE_QUANDO_O_LIVRO_ANDA` | `TICKS_ABAIXO_DO_MICROPRICE` | `PAUSA_APOS_FILL_TOXICO_S` |
|---|---|---|---|
| `base` | false | — | — |
| `pausa` | false | — | 30 |
| `ancora` | false | 1 | — |
| `recolher` | true | — | — |

Hoje cada uma paga os ~38% de infraestrutura por conta própria: **4 × ~40% =
~160% de um núcleo**, que é o dimensionamento que pediu 4 vCPU. Mas os 38%
são trabalho IDÊNTICO — os mesmos mercados, os mesmos livros, as mesmas
assinaturas — feito quatro vezes.

**Se as quatro variantes dividissem um processo**, paga-se a infraestrutura
uma vez: ~38%, mais os ~2% da rota de pools (que também é compartilhada:
uma conexão `clob[pools]`, uma descoberta), mais o laço de cotação de cada
variante. **Estimativa: ~40–46% de um núcleo para as quatro.**

> ⚠️ Os 2% são MEDIDOS. Os 40–46% são **estimativa derivada deles**, não
> medida — e o marginal por variante é menor que 2%, porque os 2% incluem a
> conexão e a descoberta, que seriam pagas uma vez só. A estimativa não vira
> ✅ no quadro; o que a fecharia é a medida sobre o processo já escrito.

**Isto é o inverso do que o §10.1f concluiu.** Lá, o 4.2 não cabia em 1 vCPU
e a recomendação era comprar máquina. A conclusão continua correta **para a
topologia atual** — quatro processos. O que mudou é que a topologia deixou
de ser um dado do problema: ela é uma escolha, e ninguém tinha medido o que
ela custa.

#### O preço desta saída, antes de pagá-lo

1. **É mudança de código de verdade**, não configuração: N instâncias de
   `LacoMaker` com os seus três botões, o seu registro de risco e o seu
   diário, sobre um `Ciclo` e feeds compartilhados.
2. **Perde-se isolamento.** Hoje uma variante que morre não leva as outras.
   Num processo só, leva — e 14 dias de ensaio morrem juntos.
3. **Risco de contaminação cruzada.** Um estado compartilhado por engano
   entre variantes faria uma regra parecer melhor por bug, não por mérito —
   e é justamente uma comparação entre regras que está em jogo.
4. **Em troca, a comparação fica MAIS limpa que hoje:** as variantes veriam
   byte a byte a mesma entrada de livro, no mesmo instante, em vez de quatro
   conexões que recebem o mesmo mercado com microdiferenças de chegada.

#### Se 20 pools não couber

Resta a terceira linha do §10.1f, a que ele chamou de investigação inteira:
por que uma rodada custa 40% de um núcleo. Os sinais de que há o que cortar
são `in` ~2.000–3.000/s e `cs` ~1.000/s.

> A primeira versão citava aqui um terceiro sinal — o `1013 slow consumer`
> como prova de que o processo não drena o socket. **Foi retirado: é falso**,
> ver a correção no §10.1i. O sinal que ficou no lugar dele é melhor, e está
> logo abaixo.

#### O que dá para fechar sem nada disso

Uma `base` sozinha roda limpa nesta máquina (`%wait` 0,38%). Catorze dias
dela entregam o que o §10.2 lista: que a rota sobrevive 24 h × 14 sem
derrubar o processo, quantas cotações repousam e por quanto tempo, e qual
fração das janelas com pool o portão deixa cotar. **É parte do que o 4.2
exige e não existe hoje** — e não decide regra nenhuma. Serve como piso, não
como conclusão, e começá-la agora conflita com o escopo reduzido (a `base`
de 60 pools não compara com uma janela de 20). Por isso ela espera a medida
acima.

### 10.1k. O #181 no ar — a regra funciona, e o resultado piorou

Primeira janela com o reset da escalada por saúde (#181) em produção.
`clob[updown]`, 48 minutos, 22 reconexões.

**A regra faz o que foi escrito.** Saúde acima de
`SAUDE_QUE_ZERA_A_ESCALADA_SEGUNDOS` zera e o sono volta à faixa de 5–7,5 s;
saúde curta escala 5 → 10 → 20 → 30:

| queda | saúde antes | dormiu | antes do #181 daria |
|---|---|---|---|
| +11,57 min | 9,7 min | **7,2 s** | ~32 s |
| +18,16 min | 30 s | 14,5 s | ~32 s |
| +18,93 min | 32 s | 22,6 s | ~32 s |
| +26,25 min | 6,9 min | **6,5 s** | ~32 s |

**E o resultado piorou:**

| código | quedas/h | s sem livro/h | % da hora |
|---|---|---|---|
| #177/#178 (janela de 4 h, §10.1i) | 2,25 | 78,7 | 2,2% |
| **#181 (janela de 48 min)** | **27,2** | **390,6** | **10,8%** |

O total de sono é quase idêntico nas duas janelas (314,6 s contra 315,6 s);
o que mudou foi a compressão — o mesmo tempo cego em um oitavo do relógio.

#### A leitura que desfavorece o meu próprio conserto

Os sonos de 30–45 s estavam **suprimindo as quedas**. Voltar em 5 s significa
reassinar no meio da rajada de 4 MB/s, não drenar, e levar outro `1013`. A
paciência involuntária estava fazendo trabalho real, e o #181 a removeu.

O que sustenta isso é um número que já estava no repositório antes de
qualquer piso existir: em **2026-09-14**, o `clob[updown]` caía **até 6
vezes em 7 min** — ~51/h. Os 27/h de agora estão DENTRO da faixa nativa
desta conexão. **Os 2,25/h da janela de 4 h é que eram a anomalia**,
produzida pelo contador travado no teto.

#### Por que o #181 fica

O defeito que ele consertou é real e independente disto: um contador chamado
`pedidos_de_paciencia_seguidos` em que nada media "seguidos", e que por isso
dormia 32 s depois de 77 minutos de conexão saudável. Manter um acerto pelo
efeito colateral seria guardar a coisa certa pelo motivo errado — e o motivo
errado não sobrevive à próxima mudança que o toque.

**O que se pode fazer com esta medida, se ela se confirmar,** é tratar a
espera como o que ela é nesta conexão: não um pedido de paciência do
servidor, mas o tempo que o nosso lado precisa para não voltar no meio da
rajada. Isso é outro mecanismo, com outro nome e outro teste — não é
reverter o #181.

#### ❓ O que falta antes de decidir qualquer coisa

**O maker NÃO cota no `clob[updown]`.** Ele cota no `clob[pools]`, que ganhou
conexão própria em 2026-09-14 exatamente para não pagar isto, e naquela
medida os tokens de pool "nunca caíram" (`live/shadow.py`, junto de
`self.poly_pools`).

Se `clob[pools]` estiver limpo nesta mesma janela, os 390 s/h são custo do
**taker** — medido, reprovado, e que o plano do 4.2 prevê desligar. Se
`clob[pools]` também estiver caindo, o assunto passa a ser do 4.2 e volta à
mesa com prioridade.

#### ✅ O agregado chegou: o livro do maker não caiu uma vez

Mesma janela, por conexão:

| conexão | quedas | s sem livro |
|---|---|---|
| `clob[updown]` | 23 | **322,6** |
| `rtds[shadow:0]` | 13 | 8,1 |
| `rtds[shadow:1]` | 11 | 7,4 |
| **`clob[pools]`** | **0** | **0,0** |

**95% do tempo cego está na conexão do taker.** O RTDS somou 24 quedas por
15,5 s — 0,65 s de média, o mesmo regime de sempre, e mais uma confirmação
de que o nosso próprio 1012 tem piso zero.

**E o zero do `clob[pools]` é MEDIDO, não uma linha que faltou.** A
diferença importa: um agregado fica idêntico quer a conexão tenha ficado
perfeita, quer nunca tenha subido. Conferido:

- `grep -c 'clob\[pools\]'` na hora = **1** — uma única linha, o
  `conectado` das 18:40:42. Se tivesse caído, haveria `conexão caiu` com
  esse rótulo;
- a descoberta segue publicando: `janelas: 53` e `52`, com
  `descartes: {nao_pontua_com_este_tamanho: 7}`.

#### O que isto conclui

A separação de conexões feita em 2026-09-14 está entregando o que prometeu:
os Up/Down levam as quedas, e o livro que o maker cota fica de fora. **O
efeito colateral do #181 não toca o 4.2** — ele encarece a rota taker, que
está medida, reprovada (quadro 1.1/1.4/1.5) e que o plano do 4.2 prevê
desligar.

**Isso reforça a fase 1 do `PLANO_4_2_NUM_PROCESSO.md`:** desligar o taker
tira de uma vez três coisas — o acoplamento de orçamento no portão de risco,
os 54% de tráfego, e estes 322,6 s cegos por hora.

> ❓ **O que este agregado NÃO prova:** que o livro de pool está *chegando*.
> Ele prova que a conexão não caiu. Conexão viva e muda produz o mesmo zero,
> e o projeto tem watchdog para isso (`stale_after_seconds_book`) justamente
> porque esse caso existe. O que fecharia: o relato de 60 s do maker, com
> cotações publicadas em vez de recusas por `sem_livro`.

### 10.1l. ❌ O disjuntor estava armado há dois dias, e o 4.2 não cotava

Descoberto em 2026-09-21 ao conferir o relato de 60 s. A `base` rodava,
`falhou: null`, feeds saudáveis — e **`cotacoes_repousando: 0`**.

O que o número sozinho não diria, e o dicionário `motivos` disse:

```
ganho_justifica_perder_a_fila    6489
portao:disjuntor_armado          6489
sem_pool_de_reward                772
```

Os dois primeiros batem porque são a mesma passada: `repouso.py` devolve
`ganho_justifica_perder_a_fila` com ação `REPOSICIONAR` — **a estratégia
achou onde cotar 6489 vezes** — e o portão recusou todas.

**Isto é a nota do `resumo()` fazendo o seu trabalho:** *"`motivos` responde
a pergunta que importa quando o bot não cota: ele não achou onde cotar, ou
achou e a histerese segurou?"*. Sem ela, `cotacoes_repousando: 0` durante 14
dias passaria por mercado quieto.

#### O registro, lido ANTES da limpeza

```json
{ "dia": "2026-09-21", "pnl_realizado_usdc": 0.0,
  "disjuntor_armado": true,
  "disjuntor_motivo": "perda do dia em -25.35 USDC, teto 25.00",
  "pausado_ate_epoch": 1789864357.0091906 }
```

`pausado_ate_epoch` converte para **2026-09-20 ~00:32 UTC**: o disjuntor
estava armado havia quase dois dias. `pnl_realizado_usdc` é 0,0 porque o dia
virou — e o disjuntor **gruda** por projeto (`gates.py`, cabeçalho). O motivo
gravado é o único registro de por quê, e ele some quando o arquivo é
afastado: por isso foi lido antes.

#### A causa: o perfil do ensaio sobe quatro tetos e esquece o quinto

| teto | default (taker) | `comum.env` | fator |
|---|---|---|---|
| `stake_max_por_trade_usdc` | 5 | 1000 | ×200 |
| `stake_max_por_janela_usdc` | 15 | 2000 | ×133 |
| `exposicao_max_usdc` | 50 | 120000 | ×2400 |
| `posicoes_max_abertas` | 5 | 120 | ×24 |
| **`perda_max_diaria_usdc`** | **25** | **ausente** | **×1** |

O `comum.env` diz de si mesmo: *"o bot não sobe os tetos sozinho; quem sobe
é o operador, e este arquivo é o operador escrevendo."* O operador subiu
quatro e não subiu o disjuntor. Com cotação de 1000 shares, 25 USDC é uma
execução ruim.

**A limpeza do §10.1g NÃO conserta isto** — ela zera o registro, e o teto
continua onde estava. A rodada seguinte arma o disjuntor de novo.

#### As duas travas que faltam

1. **O teto tem de ser escolhido, não herdado.** É número de risco, e a
   decisão é do operador (quadro 4.0 (c)). Enquanto ele não for escrito em
   `comum.env`, nenhuma janela de 14 dias vale.
2. **Disjuntor armado tem de ser conferido no arranque.** A verificação do
   §10.1g olha se os diários nasceram vazios; não olha o portão. Uma rodada
   que sobe com o disjuntor armado produz zeros que parecem mercado.

```bash
# ACRESCENTE à conferência do §10.1g, depois de subir as instâncias:
for r in base pausa ancora recolher; do
  f=data/risco/registro_maker_$r.shadow.json
  [ -f "$f" ] && python3 -c "
import json,sys
d=json.load(open('$f'))
if d.get('disjuntor_armado'):
    print('DISJUNTOR ARMADO em $r:', d.get('disjuntor_motivo'))
    sys.exit(1)
print('$r ok')
"
done
```

**Nenhuma janela do 4.2 começa com um `DISJUNTOR ARMADO` nessa saída.**

#### ✅ As duas travas, fechadas em 2026-09-21

1. **O teto foi escolhido pelo operador: 5000 USDC**, escrito em `comum.env`
   com a razão junto. Não é número novo — o default de 25 é
   `5 x stake_max_por_trade` de 5 USDC ("cinco trades ruins e para"), e 5000
   é a **mesma razão** sobre o stake de 1000 deste perfil.
2. **A conferência do disjuntor entrou no §10.1g**, ao lado da dos diários.

E o defeito ganhou teste — `tests/test_perfil_do_ensaio.py`, três, todos
verificados por mutação:

| mutação | o que reprova |
|---|---|
| tirar a linha do `comum.env` (o defeito original) | `KeyError` com a mensagem apontando o §10.1l |
| pôr o default do taker, 25 | `assert 25.0 >= (5.0 * 1000.0)` |
| pôr 999999 — subir virando desligar | teto acima da exposição não arma nunca |

A terceira existe porque o conserto tem um jeito errado de ser feito: um teto
alto o bastante para nunca armar **não exercita o portão**, e exercitá-lo é
metade do motivo de o SHADOW existir (`caminho_do_registro_do_modo`, achado
do Codex no #52).

### 10.1m. 1.899 recusas com o nome errado — 2026-09-22

Achado ao ler o `motivos` da rodada de 18 h. Entre 451.116 passadas:

```
portao:ordem_mal_formada   1899
portao:preco_fora_da_faixa  409
```

`ORDEM_MAL_FORMADA` dispara com `shares <= 0` **ou**
`not (0.0 < preco_limite < 1.0)`, e o comentário do `risk/gates.py` diz o
que ele significa: **"um pedido inválido é defeito de quem chamou"**.

**Não era defeito nosso — era a forma do mercado**, e a aritmética
reproduz:

| meio do Up | tick | perna Up | perna Down |
|---|---|---|---|
| 0,99 | 0,01 | 0,98 ✅ | **0,00 ❌** |
| 0,999 | 0,001 | 0,998 ✅ | **0,00 ❌** |

A perna Down é um bid no livro DELA, cujo meio é `1 − meio_up`. Num mercado
a 0,99 esse meio é 0,01, e recuar **um tick** o põe em zero. `Cotacao.preco`
não tem piso. E os pools de reward incluem exatamente esses mercados — a
lista do relato traz *"Will Anthropic announce bankruptcy by December 31,
2027?"*, *"Will NuScale announce bankruptcy…"*, *"Xi Jinping out before
2027?"*, que negociam a 0,01–0,02.

**Por que isso importa mais do que parece.** `ORDEM_MAL_FORMADA` é checado
ANTES de `PRECO_FORA_DA_FAIXA`, então essas 1.899 nunca chegavam ao motivo
que as descreveria. Quem lesse um relato de 14 dias com 1.899 "defeitos de
quem chamou" iria caçar um bug de construção de ordem que não existe. É a
regra do CLAUDE.md invertida: aqui a recusa TEM nome, e o nome está errado —
o que é pior que anônimo, porque manda a investigação para o lado oposto.

#### O conserto, e o que ele deliberadamente NÃO faz

O maker passa a reconhecer a perna que saiu de `(0, 1)` **antes** de
perguntar ao portão, e conta `sem_espaco_para_recuar`.

**Nenhum mercado passa a ser cotado ou deixa de ser.** Estas cotações já
eram recusadas; o que muda é o nome. E `ordem_mal_formada` volta a
significar o que diz — `shares <= 0` continua indo para ele, porque AQUELE
seria defeito nosso de verdade.

**Não se consertou o preço**, e a razão está no próprio `Cotacao.preco`: pôr
um piso ali **move a cotação**, e mover a cotação é mudar a estratégia. O
score (`estimar_retorno`) e a ordem enviada têm de ver o mesmo número — é a
razão de o preço ser calculado lá e não em quem envia (§6.1b).

Três testes em `test_4_0c_laco_maker.py::TestPernaQueSaiDoLivro`, dois
verificados por mutação:

| mutação | o que reprova |
|---|---|
| tirar a trava (o estado de antes) | `assert None == 'sem_espaco_para_recuar'` |
| trava larga demais (`if True`) | `'sem_espaco_para_recuar' != 'ordem_mal_formada'` |

O terceiro guarda o **mecanismo**: se `Cotacao.preco` ganhar piso algum dia,
ele falha — e essa falha é o lembrete de que a cotação se moveu.

### 10.1n. O primeiro dado do 4.2, e a premissa que não saía em lugar nenhum

2026-09-22, 19 min de `base` com o teto de 5000 e o registro limpo — a
primeira vez que a rota cota desde que o disjuntor armou:

| campo | valor |
|---|---|
| cotações repousando | 48 |
| segundos repousando (soma por cotação) | 43.201,8 |
| segundos **pontuando** | 43.201,8 — **100%** |
| rodada no ar (parede) | 0,317 h, ciclo de trabalho **1,0** |
| rewards pro-rata | 41,43 USDC |
| **por hora de PAREDE** | **130,81 USDC/h** |
| markout | **6 medidas**, +0,4665 c/share |

130,81 USDC/h é **13× a barra do 1.12** (≥ 10 USDC/h). E é por ser tão bom
que ele não foi escrito como resultado.

#### A conta que obriga a desconfiar

130,81 USDC/h são ~3.139 USDC/dia. Invertendo `estimar_retorno`:

```python
rewards = params.daily_rate * (horas / 24.0) * fracao * fator_de_captura
```

o `pro_rata` usa `fator = 1`, então `Σ(daily_rate × fracao) ≈ 3.137 USDC/dia`
nas 48 janelas. Com `fracao ≈ 1` isso diz que cada pool paga ~65 USDC/dia **e
levamos tudo**. O perfil do ensaio cota **1.000 shares** — ×200 o default —
contra livros de pool finos, que é a receita para a fatia colar em 1.

E o `cotacao.py` declara o limite no cabeçalho: *"`fracao_do_pool` é uma
ESTIMATIVA pro-rata (…) o quanto dela vira execução de verdade depende de
onde a nossa ordem está na fila, **que ninguém aqui pode afirmar**"*.

#### ❌ E a premissa não era observável

Conferido no artefato, não suposto. O diário grava **só a ordem**:

```json
{"evento":"cotacao_colocada","janela":"…","shares":1000.0,"preco_limite":0.32}
```

`fracao_do_pool`, `score_proprio` e `score_total_do_livro` não estavam no
diário nem no relato. **Catorze dias produziriam um número e nenhuma forma de
saber de onde ele veio** — "número sintético não fecha item" aplicado ao
próprio instrumento do item.

**Isto travava o relógio dos 14 dias**, e por isso foi consertado antes.

#### O conserto: a caixa publica a fatia

`CaixaDoMaker` passa a acumular a fatia **no mesmo lugar e com o mesmo peso**
que acumula o reward, e o relato de 60 s traz:

| campo | o que responde |
|---|---|
| `media_ponderada` | a fatia que de fato produziu o número (ponderada pelo intervalo, não por passada) |
| `maxima` | o pior caso para a credibilidade do total |
| `passadas_quase_inteiras` | quantas vezes o modelo nos deu ≥ 90% do pool — contagem, porque um punhado some numa média |
| `media_ponderada: None` | **não medi** — e `None` não é zero, que seria afirmar fatia nula |

Três testes em `TestAFatiaSaiNoRelato`, todos verificados por mutação:

| mutação | o que reprova |
|---|---|
| média por passada em vez de ponderada pelo tempo | `0.504 != 0.1733` |
| contador desligado | `0 != 1` |
| `None` virando zero | `None != 0.1733` |

#### O que fazer com os 130,81 USDC/h

**Nada, ainda.** Ele fica registrado como MEDIDO e NÃO INTERPRETADO até o
relato publicar a fatia e alguém olhar. São três razões, e qualquer uma
basta: 19 minutos são 1/1300 do que o 4.2 pede; o `pro_rata` é estimativa
com hipótese de fila; e 6 medidas de markout respondem por 11 dos 47 USDC do
líquido, o que é ruído com aparência de resultado.

**A conferência, depois do próximo restart:**

```bash
journalctl -u pulsearb-shadow-maker@base --since '-30min' --no-pager \
  | grep '"msg":"shadow"' | tail -1 \
  | python3 -c '
import json, sys
d = json.loads(sys.stdin.read().split("python[", 1)[1].split(": ", 1)[1])
f = d["maker"]["caixa"]["fracao_do_pool"]
print("fatia media (ponderada):", f["media_ponderada"])
print("fatia maxima           :", f["maxima"])
print("passadas com >= 90%%    :", f["passadas_quase_inteiras"])
'
```

| leitura | o que significa |
|---|---|
| média perto de 1 | o número mede o TAMANHO da cotação, não a estratégia |
| média baixa com `passadas_quase_inteiras` alto | poucos mercados desertos dominam o total |
| média baixa e contador baixo | a fatia é plausível, e aí os 130 USDC/h merecem investigação de verdade |

#### ✅ A fatia foi medida — e a minha suspeita NÃO se confirmou

2026-09-22, 5 min de rodada com o código novo:

| campo | valor |
|---|---|
| fatia média (ponderada) | **0,3409** |
| fatia máxima | **1,0** |
| passadas com ≥ 90% | **84 de 716** (11,7%) |
| rewards pro-rata | 8,81 USDC |
| rewards com captura (fator 0,3) | 2,64 USDC |
| markout | **3 medidas, 0,0 c/share** |

**Eu esperava fatia perto de 1** — cotar 1.000 shares em livros finos parecia
receita para isso — **e ela deu um terço**. A desconfiança estava certa em
existir e errada no palpite; fica registrado assim, porque prever mal e
acertar por sorte é pior que prever mal e saber.

Refeita a conta com o denominador certo: **~98 USDC/h de parede**, ainda ~10×
a barra do 1.12, agora com uma premissa que sobreviveu à conferência.

#### ❌ E a contagem não respondia a pergunta que decide

`passadas_quase_inteiras: 84 de 716` diz quantas vezes o modelo deu o pool
inteiro. **Não diz quanto dos 8,81 USDC veio delas** — e uma passada com
fatia 1,0 num pool de `daily_rate` alto pesa muito mais que a sua fração na
contagem. Distribuição da premissa e contribuição dela para o número são
perguntas diferentes, e eu tinha publicado só a primeira.

Corrigido no mesmo dia: `rewards_dessas_passadas` e
`fracao_do_total_que_vem_delas` entram no relato. O teste que os guarda
produz o caso que justifica o campo — **uma passada de duas, 50% da
contagem, responde por 95% do reward**.

| leitura de `fracao_do_total_que_vem_delas` | o que significa |
|---|---|
| baixa | os mercados desertos são ruído; o resultado é da rota |
| alta | o total vem de mercados vazios — **e mercado vazio deixa de ser vazio quando alguém cota nele** |

#### ⚠️ O que o líquido é hoje: estimativa pura

`liquido_pro_rata == rewards_pro_rata` porque o markout tem **3 medidas e
0,0 c/share**. A única parcela MEDIDA do 4.2 não está contribuindo com nada,
e as outras duas — fatia e `fator_de_captura = 0,3` — são hipóteses.

Enquanto o markout não tiver amostra, **o número do 4.2 é 100% estimativa**,
por mais bem instrumentada que ela esteja. É o que o cabeçalho da caixa já
dizia: *"Nada aqui é lucro: é o número que pode REPROVAR a rota."*

### 10.1o. ❌ Um merge de UI produziu Python que não compila — 2026-09-22

O squash do #188 pôs no `main` um arquivo com `IndentationError`. Quem
puxasse e reiniciasse ficava com o serviço em laço de restart.

```python
        self.rewards_com_captura_usdc += estimado.rewards_usdc
        pro_rata_da_passada = 0.0
        if self.fator_de_captura > 0.0:
        self.fracao_ponderada_x_segundos += ...      # ← `if` sem corpo
```

A linha `self.rewards_pro_rata_usdc += ...` sumiu, e com ela o corpo do
`if`.

#### A causa: squash + "Update branch"

`220b3c4` é um **merge do `main` na branch feito pela UI do GitHub**. O
`main` trazia `1215d77`, o SQUASH do #187, com as mesmas mudanças que a
branch já tinha em commits separados. O merge de três vias juntou os dois
lados **sem conflito** e semanticamente errado.

É a mesma armadilha do #175, com um resultado pior: lá o bloco sumiu, aqui
o arquivo deixou de compilar. **Merge limpo não é merge correto** quando um
dos lados é um squash do outro.

#### O que torna esta falha difícil de ver

| sinal | por que não pegou |
|---|---|
| `git show --stat` do merge | mostrou **3 arquivos**; o quarto era invisível |
| o quarto arquivo | `tests/test_4_2_caixa_maker.py`, revertido para a versão do `main` — e como o `stat` compara CONTRA o `main`, um arquivo revertido a ele **não aparece** |
| CI | o PR foi mergeado **4 s depois** de o `testes` começar |
| Sonar | passou — ele analisa, não executa |

**Dois testes sumiram sem deixar rastro no diff.** A contagem da suíte caiu
de 1886 para 1884, e esse foi o único indício.

#### As travas que isto pede

1. **Nunca usar "Update branch" da UI numa branch cujo `main` levou squash.**
   O caminho é `git merge origin/main` local, com `make check` depois.
2. **Conferir o conteúdo ANTES do merge, não depois.** Já era a lição do
   #175 e eu a apliquei tarde: conferi o `main` depois de mergeado.
3. **Não mergear com o CI em andamento.** `testes` teria pegado em 90 s.
4. **Contar os testes.** A suíte caindo de 1886 para 1884 foi o que denunciou
   a perda invisível. O `test_quadro_nao_mente` já faz isso por arquivo
   citado no quadro; `test_4_2_caixa_maker.py` não está entre eles.
5. **Todo `git pull` que toca `src/` confere se o módulo COMPILA antes de
   `systemctl start`.** Custa dois segundos e teria evitado o laço de
   restart de hoje:

   ```bash
   cd /opt/pulsearb && sudo git pull --ff-only
   python3 -c "import ast; ast.parse(open('src/pulsearb/live/caixa_maker.py').read())" \
     && echo COMPILA || echo "NAO SOBE — nao inicie"
   sudo systemctl start pulsearb-shadow-maker@base
   ```

#### ❌ E o conserto falhou na primeira tentativa, pelo mesmo mecanismo

O #189 foi mergeado e **o `main` continuou quebrado**: o squash levou 68 das
113 linhas e deixou o `if` sem corpo outra vez.

A razão é a base de merge. Em `1215d77` aquelas três linhas existiam; a
branch as MODIFICOU; o `main` (via #188) as APAGOU. Todo merge de três vias
vê "modificado de um lado, apagado do outro" e resolve pela deleção — e
repetiria isso indefinidamente enquanto a branch carregasse histórico
anterior à deleção.

**A saída é tirar a ambiguidade:** partir do `main` como ele está, resolver o
conflito de verdade (aqui ele finalmente apareceu, em vez de ser
auto-resolvido), e deixar o diff como **adição pura**. Com zero linhas
removidas, não há deleção para o squash honrar.

Confira antes de mergear qualquer conserto deste tipo:

```bash
git diff origin/main..HEAD | grep -c "^-[^-]"    # tem de ser 0
```

#### E o estrago era maior do que o arquivo que não compilava

Ao conferir o resto, em 2026-09-23: o `ESTADO_PARA_LIVE.md` do `main` estava
com **226 KB contra 294 KB** na branch. **Os squashes vinham derrubando
conteúdo do QUADRO em silêncio** — dois dias de linhas, incluindo a medida
dos 2%, o plano, a fase 0, o disjuntor e a fatia.

E a minha branch carregava **dois marcadores de conflito** no quadro, que eu
empurrei. Eles sobreviveram a tudo: `make check` passou, o Sonar passou (não
analisa markdown), e a revisão de olho não os viu. O que os pegou foi um
`grep` manual — e o que quase os deixou passar foi eu conferir marcadores
**só no arquivo `.py`** do mesmo merge.

**Como a linha 4.0 foi resolvida:** nenhum dos dois lados era superconjunto.
Eles compartilhavam 27.215 caracteres de prefixo e divergiam em caudas
COMPLEMENTARES — a do HEAD com a medida dos 2%, a do `main` com tudo do plano
em diante. Escolher um lado perderia o outro. A resolução foi a **união**:
prefixo + as duas caudas, conferida pela presença dos marcos de cada uma
(`1,99`, `40–46`, `0,3409`, `130,81`, `fracao_do_total_que_vem_delas`).

**A trava que entra:**
`test_quadro_nao_mente.py::test_nenhum_arquivo_versionado_tem_marcador_de_conflito`
varre `git ls-files` inteiro. `=======` fica de fora de propósito — em
markdown é sublinhado de título. Verificado por mutação.

### 10.2. O que ainda NÃO está medido, e o que este passo mede

- **Disco do diário:** ✅ **medido em 2026-09-20** — 3,04 MiB/h nas quatro
  instâncias, ≈ 1,0 GiB em 336 h, contra 18 GB livres. A conta e o método
  estão no §10.1d, junto com as duas perguntas que a mesma medida deixou em
  aberto (memória e CPU).
- **O custo de saída** (quadro, 1.12): o SHADOW anota as intenções, não as
  execuções. O número que decide o 1.12 sai do `markout_dos_pools.py` com os
  horizontes longos, no Mac — este passo não substitui aquele.
- **O que este passo mede:** que a rota roda 24 h × 14 sem derrubar o
  processo, quantas cotações repousam e por quanto tempo, e qual fração das
  janelas com pool o portão deixa cotar. É o dado que o 4.2 exige, e é o que
  não existe hoje.
