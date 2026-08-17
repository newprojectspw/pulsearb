#!/usr/bin/env bash
# Baixa as gravações do dia da VPS para a máquina de análise.
#
#   ./scripts/fetch_recordings.sh pulsearb@1.2.3.4
#   ./scripts/fetch_recordings.sh pulsearb@1.2.3.4 2026-08-17 ./gravacoes
#
# Os arquivos já são .jsonl.gz — não recomprime, só transfere e verifica.
# rsync com --partial retoma transferência interrompida, que importa quando o
# link cai no meio de centenas de MB.
set -euo pipefail

HOST="${1:?uso: $0 usuario@host [YYYY-MM-DD] [destino]}"
DIA="${2:-$(date -u +%Y%m%d)}"
DESTINO="${3:-./data/recordings}"
ORIGEM="${PULSEARB_REMOTE_DIR:-/opt/pulsearb/data/recordings}"

# O recorder nomeia como pulsearb-YYYYmmdd-HHMM.jsonl.gz
PADRAO="pulsearb-${DIA//-/}-*.jsonl.gz"

mkdir -p "$DESTINO"
echo "==> baixando $PADRAO de $HOST:$ORIGEM"

rsync -avz --partial --progress \
  --include="$PADRAO" --exclude='*' \
  "$HOST:$ORIGEM/" "$DESTINO/"

echo
echo "==> verificando integridade do gzip"
falhas=0
for arquivo in "$DESTINO"/${PADRAO}; do
  [ -e "$arquivo" ] || { echo "nenhum arquivo para $DIA"; exit 1; }
  if gzip -t "$arquivo" 2>/dev/null; then
    linhas=$(gzip -dc "$arquivo" | wc -l)
    printf '  OK   %-42s %6s linhas  %s\n' \
      "$(basename "$arquivo")" "$linhas" "$(du -h "$arquivo" | cut -f1)"
  else
    printf '  RUIM %-42s (gzip corrompido)\n' "$(basename "$arquivo")"
    falhas=$((falhas + 1))
  fi
done

total=$(du -sh "$DESTINO" | cut -f1)
echo
echo "==> $total em $DESTINO"
if [ "$falhas" -gt 0 ]; then
  echo "==> $falhas arquivo(s) corrompido(s) — o replay tolera a última linha"
  echo "    quebrada, mas arquivo inteiro ruim precisa ser rebaixado."
  exit 1
fi

echo "==> rodar o backtest:"
echo "    python -m pulsearb.backtest $DESTINO --json relatorio-$DIA.json"
