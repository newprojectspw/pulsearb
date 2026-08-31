# PULSEARB recorder — imagem slim para a VPS.
#
# Só o recorder: o backtest roda offline, na máquina de análise, sobre as
# gravações baixadas. A VPS existe para gravar 72h sem cair.
FROM python:3.12-slim

# Usuário não-root (seção 9 do plano do projeto).
#
# UID FIXO, e alto de propósito. Sem `--uid` o `useradd` daria o primeiro
# livre (1000 no slim), que é justamente o UID do primeiro usuário humano de
# quase toda VPS — então o dono do bind mount passaria a depender de quem foi
# criado primeiro na máquina. Com 10001 o número é conhecido, e o `chown` do
# host (RUNBOOK §5) pode citá-lo em vez de "o UID que tiver saído".
RUN useradd --create-home --shell /usr/sbin/nologin --uid 10001 pulsearb

WORKDIR /app

# Dependências primeiro: camada estável, cache aproveitado entre builds.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && rm -rf /root/.cache

COPY config.yaml ./

# As gravações saem por volume, nunca ficam na imagem.
#
# O `mkdir` + `chown` ANTES do `VOLUME` não é redundante — é o que faz o
# recorder conseguir escrever. `VOLUME` num caminho que não existe na imagem
# cria o ponto como root:root; o processo roda como `pulsearb`, e a primeira
# gravação morre com `PermissionError`. Com `--restart=always` isso vira um
# container reiniciando em laço, e o sintoma no `docker logs` é uma exceção de
# escrita que não menciona permissão de volume.
#
# Isto cobre o volume ANÔNIMO. Em bind mount (`-v /opt/pulsearb/data:/data`,
# que é o que o RUNBOOK usa) quem manda é o dono no HOST, e nenhuma linha
# deste arquivo alcança isso — por isso o runbook manda `chown 10001:10001`.
RUN mkdir -p /data && chown pulsearb:pulsearb /data
VOLUME ["/data"]
ENV PULSEARB_RECORDER__OUTPUT_DIR=/data

USER pulsearb

# --duration é sobrescrevível: `docker run ... --duration 24h`
ENTRYPOINT ["python", "-m", "pulsearb.recorder"]
CMD ["--duration", "72h"]
