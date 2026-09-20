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
| `divergencias` | 0, ou poucas e estáveis | o topo que reconstruímos discorda do que o servidor manda. Crescendo sem parar = parser errado (ver API_NOTES 6.1b), não perda. |
| `resyncs` | poucos | cada um é um buraco que foi consertado. Muitos = a fonte do problema não foi resolvida. |
| `offset_relogio_p50_ms` | estável, dezenas de ms | crescendo ao longo da gravação = NTP quebrado (§4.1). |

**`divergencias` alto logo no primeiro minuto é o sinal mais importante desta
lista**, e vale parar por ele: significa que o livro reconstruído não
corresponde ao real, e uma gravação de 72h nessas condições não sustenta
veredito nenhum. Confira `integridade.divergencia_topo_book.formas_de_price_change`
no relatório final para saber qual formato o servidor está usando.

Só depois desses quatro checks a gravação está de fato iniciada.

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

E isso explica as 601 reconexões/h do §10.1d sem precisar de mais nada: o
laço de eventos não volta a tempo de responder o ping, e quem fecha a
conexão é o nosso lado — exatamente o `"close_origem":"cliente"` com
`no close frame received or sent` que o diário registra.

**Consequência, dita sem rodeio: o ensaio de 14 dias iniciado em
2026-09-19 22:15 UTC não produz dado válido.** O `cotacoes_repousando` e o
`meio_no_fill` das quatro carregam fila de escalonador junto com mercado, e
o feed cai 150×/h em cada uma. Não é um ensaio ruim que se corrige na
análise: é um instrumento medindo a si mesmo.

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

### 10.1f. Uma rodada sozinha cabe, e o custo dela tem teto de ~40%

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

vmstat 5 12                    # id e r sob DOIS processos
ps -o pid,stat,pcpu,args -C python --sort=-pcpu | head -4
journalctl -u pulsearb-shadow-maker@base --since '-10min' --no-pager \
  | grep -c 'conexão caiu'
```

Reprova (e aí sim ❌) se: `id` encostar em 0, `r` ficar em 2 constante, ou a
contagem de reconexão subir contra a janela solo do §10.1f. **Passa** se
sobrar `id` com folga e a reconexão não mudar — e então o plano B vale, sem
gastar nada.

**Os 38–48% da primeira linha são um TETO, não o custo da rodada** (achado
P2 do Codex, quarta rodada do #170 — a versão anterior desta tabela dizia
"~40% medido" enquanto a seção acima já explicava que o `us+sy` é da máquina
inteira; a tabela contradizia o próprio texto). Como a carga do resto do
host é ≥ 0, vale `custo_da_rodada ≤ 38–48%` **na janela amostrada**, e nada
mais forte do que isso até a medida por PID rodar. As duas incertezas
apontam para lados opostos: a **atribuição** faz o número ser alto demais
(sobra host dentro dele), a **amostra curta** faz ser baixo demais (o pico
de uma janela maior pode passar de 48%).

**O que JÁ está decidido, porque é aritmética e não extrapolação:** quatro
rodadas em paralelo pedem mais de um núcleo, e nenhuma máquina de 1 vCPU
entrega isso — isso não depende de atribuição nenhuma, já que o regime de
quatro foi medido direto com `id = 0` e `r = 4`.

**O tamanho da máquina, esse ainda não está decidido.** Os `4 × 40% = 160%`
saem do teto, então **4 vCPU é folgado com certeza, e pode ser folgado
demais** — se a medida por PID der 25% por rodada, 2 vCPU resolve. ~560 MiB
de RSS somados; 2 GiB de memória bastam pela conta e tiram o swap do zero.
**Não compre por esta tabela: rode o `pidstat` primeiro**, e só então
multiplique. E a compra só entra em cena se o ensaio de duas rodadas acima
reprovar.

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
```

O registro de risco vai junto porque o ensaio do 4.2 sobe com registro
limpo — é o procedimento das rodadas r7 em diante, e um registro com
exposição herdada da medida de capacidade faria o portão recusar por um
motivo que não é do ensaio.

**A conferência que fecha isto:** `ls -l data/diarios/` logo depois tem de
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

**A escolha, então, é esta — e é sua, não minha:**

| | plano B nesta VPS | máquina maior |
|---|---|---|
| tempo | **42 dias** | **14 dias** |
| custo | zero | o preço da VPS por ~1 mês |
| regra vs. base | ✅ limpo nas três | ✅ limpo nas três |
| regra vs. regra | ❌ 28 dias de distância | ✅ mesmo intervalo |

E as duas dependem do ensaio de duas rodadas do §10.1f passar: se ele
reprovar, o plano B cai junto e sobra só a máquina maior.

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
