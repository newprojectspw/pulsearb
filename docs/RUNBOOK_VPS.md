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
| Disco | 10 GB | ~5 MB/h comprimido (ver §6); 72h ≈ 0,35 GB |
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

Só depois desses três checks a gravação está de fato iniciada.

Alternativa com Docker:

```bash
docker build -f deploy/Dockerfile -t pulsearb-recorder .
docker run -d --restart=always --name pulsearb-recorder \
  -v /opt/pulsearb/data:/data pulsearb-recorder --duration 72h
```

## 6. Uso de disco

Estimativa a partir das cadências **medidas** (API_NOTES 13.1) e de 76 janelas
ativas (13.6), com 7 ativos de preço:

| | Valor |
|---|---|
| Eventos/hora | ~72.000 |
| Cru | ~27 MB/h |
| **Comprimido (gzip -1)** | **~5 MB/h** |
| Por dia | ~0,12 GB |
| **72h** | **~0,35 GB** |
| Semana | ~0,82 GB |

Ou seja: **10 GB de disco cobrem meses.** A razão de compressão medida é ~5,7x.

Se quiser conferir na prática depois de uma hora rodando:

```bash
du -sh /opt/pulsearb/data/recordings
```

## 7. Coletar as gravações

Da máquina de análise:

```bash
./scripts/fetch_recordings.sh pulsearb@SEU_IP                 # hoje
./scripts/fetch_recordings.sh pulsearb@SEU_IP 2026-08-17      # um dia
```

O script verifica a integridade de cada gzip e imprime a contagem de linhas.
Depois:

```bash
python -m pulsearb.backtest data/recordings --json relatorio.json
```

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
- [ ] **§5.1 passou**: 0-1 "conexão caiu" em 60s, e os três `msgs_*` > 0
- [ ] o log tem `descoberta` a cada 30s, com `assinadas` estável
- [ ] `data/recordings/` tem um `.jsonl.gz` crescendo
- [ ] `du -sh` bate com a ordem de grandeza da §6
- [ ] `descartadas` está em 0
