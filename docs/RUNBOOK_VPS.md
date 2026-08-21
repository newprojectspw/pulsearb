# Runbook — recorder na VPS de Londres

Do zero até uma gravação de 72h rodando. Testável por quem nunca viu o
projeto.

**Região: Londres.** Escolhida entre as candidatas, mas com uma ressalva
honesta: a cadência medida do feed (p50 ~0,9s, API_NOTES 13.1) torna a
latência de rede praticamente irrelevante para esta estratégia. A escolha é
**revisável** e de baixo impacto — se o backtest mostrar sensibilidade real a
latência, revisita-se; enquanto não mostrar, é ruído.

---

## 1. Droplet

Qualquer VPS pequena serve. O recorder é I/O de rede e escrita sequencial:

| Recurso | Mínimo | Por quê |
|---|---|---|
| vCPU | 1 | o processo passa a vida esperando socket |
| RAM | 1 GB | fila assíncrona + buffers de WS |
| Disco | **80 GB** (mín. 50 GB com descarga periódica) | ~470 MB/h comprimido (ver §6); 72h ≈ 34 GB |
| Região | Londres | ver ressalva acima |

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
docker run -d --restart=always --name pulsearb-recorder \
  -v /opt/pulsearb/data:/data pulsearb-recorder --duration 72h
```

## 6. Uso de disco — MEDIDO em produção

| | Estimativa original | **Real (medido 2026-08-18)** |
|---|---|---|
| Comprimido | ~5 MB/h | **~470 MB/h** |
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

Se a primeira hora fechada não estiver na casa das centenas de MB, algo está
errado — provavelmente um feed calado (§5.1).

### Descarga periódica (disco menor) ou volume extra

Duas saídas quando o disco é o limite. Escolha uma **antes** de começar as
72h, não no meio.

**a) Descarga periódica.** Baixe e apague as horas já transferidas conforme
avança, em vez de esperar o fim. Rode isto na máquina de análise a cada ~12h:

```bash
# baixa tudo que já fechou, verifica a integridade e só então apaga da VPS
rsync -avz --partial --progress \
  'root@SEU_IP:/opt/pulsearb/data/recordings/pulsearb-*.jsonl.gz' ~/pulsearb-dados/

for f in ~/pulsearb-dados/pulsearb-*.jsonl.gz; do gzip -t "$f" || echo "RUIM: $f"; done

# apague só o que baixou íntegro, e NUNCA o arquivo da hora corrente
ssh root@SEU_IP 'ls -t /opt/pulsearb/data/recordings/*.jsonl.gz | tail -n +2 | xargs rm -f'
```

O `tail -n +2` preserva o arquivo mais recente, que é aquele em que o recorder
está escrevendo neste instante.

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
| `divergencia_topo_book.taxa` | acima de 1% invalida a conta do maker (ver `VEREDITO_M2.md`) |
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

## 8. Parar

```bash
sudo systemctl stop pulsearb-recorder     # para agora
sudo systemctl disable pulsearb-recorder  # não sobe no boot
```

O recorder fecha o arquivo corrente e grava o relatório final de cobertura
antes de sair. Matar com `kill -9` perde, no máximo, a última linha — o replay
tolera isso e conta quantas foram.

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
