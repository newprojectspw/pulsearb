# PULSEARB recorder — imagem slim para a VPS.
#
# Só o recorder: o backtest roda offline, na máquina de análise, sobre as
# gravações baixadas. A VPS existe para gravar 72h sem cair.
FROM python:3.12-slim

# Usuário não-root (seção 9 do plano do projeto).
RUN useradd --create-home --shell /usr/sbin/nologin pulsearb

WORKDIR /app

# Dependências primeiro: camada estável, cache aproveitado entre builds.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && rm -rf /root/.cache

COPY config.yaml ./

# As gravações saem por volume, nunca ficam na imagem.
VOLUME ["/data"]
ENV PULSEARB_RECORDER__OUTPUT_DIR=/data

USER pulsearb

# --duration é sobrescrevível: `docker run ... --duration 24h`
ENTRYPOINT ["python", "-m", "pulsearb.recorder"]
CMD ["--duration", "72h"]
