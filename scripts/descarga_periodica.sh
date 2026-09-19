#!/usr/bin/env bash
# Descarga periódica: baixa, confere e só então apaga da VPS.
#
#   ./scripts/descarga_periodica.sh root@1.2.3.4 ~/pulsearb-dados
#
# Roda NA MÁQUINA DE ANÁLISE (o Mac), a cada ~12 h, enquanto uma gravação
# longa estiver em curso. É o que permite 72 h de gravação num disco que
# só comporta ~22 h (RUNBOOK §6).
#
# Compõe os dois scripts que já existem, e a ORDEM é a segurança:
# `fetch_recordings.sh` baixa e sai com 1 se algum gzip reprovar; com
# `set -e` isso impede o purge de correr. `purge_recordings.sh` refaz as
# conferências por conta própria — existe, tamanho bate, gzip abre — e só
# apaga o que passou nas três.
#
# DOIS DIAS, e não um. `fetch_recordings.sh` baixa o padrão de UM dia. Uma
# rodada que caia depois da meia-noite UTC deixaria para trás as horas do dia
# anterior, e elas só seriam apagadas... nunca, porque o purge não apaga o que
# não está aqui. Baixar ontem e hoje cobre a virada com folga.
set -euo pipefail

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="${1:?uso: $0 usuario@host [destino]}"
DESTINO="${2:-$HOME/pulsearb-dados}"
DESTINO="${DESTINO/#\~/$HOME}"

# `date -u -v-1d` no BSD (macOS), `date -u -d yesterday` no GNU.
ONTEM=$(date -u -v-1d +%Y-%m-%d 2>/dev/null || date -u -d yesterday +%Y-%m-%d)
HOJE=$(date -u +%Y-%m-%d)

echo "=== descarga periódica  $(date -u '+%Y-%m-%d %H:%M:%S UTC') ==="

# A SONDA VEM PRIMEIRO, e ela é a razão de este bloco existir. Sem ela, um
# host que não resolve fazia o `rsync` falhar, o `||` abaixo dizia "sem
# arquivos deste dia", o purge não achava nada e a rotina terminava
# imprimindo `=== fim ===`. Ou seja: a corrida inteira falhava e o log dizia
# que tinha corrido — e `=== fim ===` é exatamente o marcador que o RUNBOOK
# §6 manda conferir. Medido em 2026-09-18, com o host errado na linha de
# comando.
#
# Não saber se a VPS respondeu é motivo de RECUSA, nunca de seguir em frente.
if ! ssh -o BatchMode=yes -o ConnectTimeout=15 "$HOST" true 2>/tmp/pulsearb-sonda.$$; then
  echo "ERRO: não consegui falar com $HOST." >&2
  sed 's/^/      /' /tmp/pulsearb-sonda.$$ >&2 || true
  rm -f /tmp/pulsearb-sonda.$$
  echo "      NADA foi baixado nem apagado." >&2
  echo "      Se o host tem um apelido no ~/.ssh/config, use o apelido." >&2
  exit 2
fi
rm -f /tmp/pulsearb-sonda.$$

for dia in "$ONTEM" "$HOJE"; do
  echo "--- baixando $dia"
  # Um dia sem nenhum arquivo faz o fetch sair com 1, e isso NÃO é erro aqui:
  # na primeira rodada não há ontem. O purge adiante é quem decide o que
  # apagar, e ele só apaga o que conferiu.
  "$AQUI/fetch_recordings.sh" "$HOST" "$dia" "$DESTINO" || {
    echo "    (sem arquivos de $dia, ou algum reprovou — nada será apagado deste dia)"
  }
done

echo "--- apagando da VPS o que tem cópia íntegra aqui"
"$AQUI/purge_recordings.sh" "$HOST" "$DESTINO" --apagar

echo "=== fim  $(date -u '+%Y-%m-%d %H:%M:%S UTC') ==="
