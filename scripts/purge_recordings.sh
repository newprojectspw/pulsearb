#!/usr/bin/env bash
# Apaga da VPS as gravações que já estão ÍNTEGRAS na máquina de análise.
#
#   ./scripts/purge_recordings.sh root@1.2.3.4 ~/pulsearb-dados            # so LISTA
#   ./scripts/purge_recordings.sh root@1.2.3.4 ~/pulsearb-dados --apagar   # apaga
#
# Roda NA MÁQUINA DE ANÁLISE (o Mac), não na VPS: é aqui que estão as cópias
# a conferir.
#
# A ORDEM IMPORTA, e é a razão deste script existir. Apagar por data —
# `rm pulsearb-20260822-*.jsonl.gz` na VPS — assume que tudo foi baixado e
# está inteiro. Um arquivo que baixou pela metade tem o nome certo, passa no
# `ls`, e reprova no `gzip -t`: apagar por existência de nome perderia a
# gravação sem ninguém notar. Gravação perdida não volta — a hora de mercado
# já passou.
#
# Três conferências por arquivo, todas LOCAIS, antes de qualquer remoção:
#
#   1. existe aqui
#   2. o gzip abre inteiro (`gzip -t`)
#   3. o tamanho bate com o da VPS
#
# A (3) cobre o caso que a (2) não pega: o arquivo da hora CORRENTE está
# sendo escrito na VPS e cresce. A cópia local fica menor e o gzip reprova —
# as duas travas o excluem, que é o comportamento certo.
#
# Sem `--apagar` o script só lista. É o default de propósito: quem apaga
# gravação faz isso de caso pensado.
set -euo pipefail

HOST="${1:?uso: $0 usuario@host [destino] [--apagar]}"
DESTINO="${2:-./data/recordings}"
MODO="${3:-}"
ORIGEM="${PULSEARB_REMOTE_DIR:-/opt/pulsearb/data/recordings}"

DESTINO="${DESTINO/#\~/$HOME}"

echo "==> comparando $HOST:$ORIGEM com $DESTINO"
echo

# `stat -c` no GNU (a VPS é Ubuntu). Nome e tamanho, um par por linha.
#
# `printf %q` porque o caminho é interpolado AQUI e executado LÁ: sem
# aspas-por-construção, um diretório com espaço vira dois argumentos do outro
# lado do ssh, e o glob casa nada. É o SC2029 do shellcheck, e a expansão do
# lado do cliente é intencional — `$ORIGEM` é configuração desta máquina.
origem_remota=$(printf %q "$ORIGEM")
# shellcheck disable=SC2029  # a expansao do lado do cliente e o objetivo
remotos=$(ssh "$HOST" "stat -c '%n %s' $origem_remota/*.jsonl.gz 2>/dev/null" || true)
if [ -z "$remotos" ]; then
  echo "nenhuma gravação em $HOST:$ORIGEM"
  exit 0
fi

seguros=()
bytes_a_liberar=0
while read -r caminho tamanho_remoto; do
  [ -n "$caminho" ] || continue
  nome=$(basename "$caminho")
  local_="$DESTINO/$nome"

  if [ ! -f "$local_" ]; then
    printf '  MANTER   %-42s nao esta em %s\n' "$nome" "$DESTINO"
    continue
  fi
  # `stat` difere entre macOS (-f%z) e GNU (-c%s); a máquina de análise pode
  # ser qualquer uma das duas.
  tamanho_local=$(stat -f%z "$local_" 2>/dev/null || stat -c%s "$local_")
  if [ "$tamanho_local" != "$tamanho_remoto" ]; then
    printf '  MANTER   %-42s tamanho difere (%s local, %s remoto)\n' \
      "$nome" "$tamanho_local" "$tamanho_remoto"
    continue
  fi
  if ! gzip -t "$local_" 2>/dev/null; then
    printf '  MANTER   %-42s gzip local corrompido\n' "$nome"
    continue
  fi

  printf '  APAGAR   %-42s copia integra aqui\n' "$nome"
  seguros+=("$caminho")
  bytes_a_liberar=$((bytes_a_liberar + tamanho_remoto))
done <<< "$remotos"

echo
gib=$(awk "BEGIN{printf \"%.1f\", $bytes_a_liberar/1073741824}")
echo "==> ${#seguros[@]} arquivo(s) com cópia íntegra aqui — ${gib} GiB a liberar"

if [ "${#seguros[@]}" -eq 0 ]; then
  exit 0
fi

if [ "$MODO" != "--apagar" ]; then
  echo "==> nada foi apagado. Para apagar de verdade:"
  echo "    $0 $HOST $DESTINO --apagar"
  exit 0
fi

printf '%s\n' "${seguros[@]}" | ssh "$HOST" "xargs -r rm -f --"
echo "==> apagados de $HOST"
echo
# shellcheck disable=SC2029  # idem: $origem_remota e desta maquina, ja com printf %q
ssh "$HOST" "df -h $origem_remota | tail -1"
