#!/usr/bin/env bash
# Roda o backtest de um dia inteiro, do zero, em um comando.
#
#   ./scripts/analisa_dia.sh 20260824
#   ./scripts/analisa_dia.sh 20260824 ~/pulsearb-dados ~/pulsearb-m2
#   ./scripts/analisa_dia.sh 20260824 ~/pulsearb-dados ~/pulsearb-m2 relatorios/VARIANCIA_23AGO.json
#
# Roda NA MÁQUINA DE ANÁLISE (o Mac).
#
# POR QUE ESTE SCRIPT EXISTE, e não é preguiça de digitar. A sequência
# manual — montar links, lançar em segundo plano, conferir se subiu — tem
# três caracteres que o zsh interativo trata de forma especial:
#
#   #   comentário: zsh sem `interactive_comments` responde
#       "command not found: #" e a linha seguinte vira comando solto
#   !   expansão de histórico, ATIVA MESMO DENTRO DE ASPAS DUPLAS.
#       `echo "PID $!"` deixa o terminal preso em `dquote>`
#   &   junto de `&&` numa colagem de várias linhas, muda o que roda em
#       primeiro e segundo plano
#
# Os três já morderam de verdade nesta campanha, e o custo não foi o
# incômodo: foi uma rodada de 24 h que ninguém percebeu que nunca começou,
# porque o log que deveria provar isso também nunca foi criado.
#
# Aqui dentro, num script, nada disso se aplica: `!` não expande, `#` é
# comentário de verdade, e o `&` está sob controle. O operador digita um
# comando curto sem caractere especial nenhum.
set -euo pipefail

DIA="${1:?uso: $0 YYYYMMDD [dados] [saida] [curva-de-variancia.json]}"
DADOS="${2:-$HOME/pulsearb-dados}"
STAGE="${3:-$HOME/pulsearb-m2}"
CURVA="${4:-}"

LIMITE_POR_TOKEN="${PULSEARB_LIMITE_POR_TOKEN:-20000}"
NIVEIS="${PULSEARB_NIVEIS:-103}"

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# RELATIVO, não absoluto: o backtest contém a saída sob a raiz de trabalho
# (M2.5) e recusa caminho absoluto. O script faz `cd "$RAIZ"` antes de
# lançar, então o relativo resolve para o lugar certo — e `RELATORIO_ABS`
# existe só para o que se imprime na tela.
RELATORIO="relatorios/M2_${DIA}.json"
RELATORIO_ABS="$RAIZ/$RELATORIO"
LOG="$HOME/m2_${DIA}.log"

echo "==> dia $DIA"
echo "    dados     $DADOS"
echo "    relatorio $RELATORIO_ABS"
echo "    log       $LOG"
[ -n "$CURVA" ] && echo "    curva     $CURVA"
echo

# ── 1. as horas que existem, com o gzip conferido ────────────────────────
#
# Só entra hora ÍNTEGRA. A hora corrente da gravação e a que morreu no meio
# de uma escrita reprovam no `gzip -t`, e incluí-las envenenaria a rodada
# inteira por causa de um arquivo — depois de três horas de processamento.
mkdir -p "$STAGE"
rm -f "${STAGE:?}"/*.jsonl.gz

horas_ok=0
horas_ruins=0
for arquivo in "$DADOS/pulsearb-${DIA}-"[0-9][0-9][0-9][0-9].jsonl.gz; do
  [ -e "$arquivo" ] || continue
  if gzip -t "$arquivo" 2>/dev/null; then
    ln -sf "$arquivo" "$STAGE/"
    horas_ok=$((horas_ok + 1))
  else
    printf '  PULADA  %s (gzip nao abre inteiro)\n' "$(basename "$arquivo")"
    horas_ruins=$((horas_ruins + 1))
  fi
done

if [ "$horas_ok" -eq 0 ]; then
  echo "nenhuma hora integra de $DIA em $DADOS" >&2
  exit 1
fi
echo "==> $horas_ok hora(s) integra(s), $horas_ruins pulada(s)"

# ── 2. lançar ────────────────────────────────────────────────────────────
mkdir -p "$RAIZ/relatorios"
cd "$RAIZ"
CURVA_ARGS=()
[ -n "$CURVA" ] && CURVA_ARGS=(--curva-de-variancia "$CURVA")
nohup python -m pulsearb.backtest "$STAGE" \
  --limite-por-token "$LIMITE_POR_TOKEN" \
  --niveis-por-lado "$NIVEIS" \
  --json "$RELATORIO" \
  "${CURVA_ARGS[@]}" \
  > "$LOG" 2>&1 &
pid=$!
echo "==> lancado, PID $pid"

# ── 3. PROVAR que subiu ──────────────────────────────────────────────────
#
# O defeito que motivou este script foi uma rodada que nunca começou sem
# ninguém notar. Anunciar "lancado" e sair repetiria isso: um PID existe por
# um instante mesmo quando o processo morre logo em seguida. Então o script
# espera a primeira linha de progresso REAL antes de dizer que deu certo.
#
# E "esperar a primeira linha" NAO e "esperar o arquivo ficar nao-vazio":
# mensagem de erro tambem enche o arquivo. Este script anunciou "rodando"
# para um processo que tinha acabado de morrer com ModuleNotFoundError, no
# primeiro teste que rodei nele. A prova precisa ser a linha de progresso
# ESPECIFICA, e nada menos.
echo "==> esperando a primeira linha de progresso..."
for _ in $(seq 1 60); do
  sleep 1
  if grep -q "passada 1: comecando" "$LOG" 2>/dev/null; then
    echo
    head -3 "$LOG"
    echo
    echo "==> rodando. Para acompanhar:"
    echo "    tail -f $LOG"
    echo "==> quando terminar:"
    if [ -n "$CURVA" ]; then
      echo "    python scripts/resumo_m2.py $RELATORIO  # curva: $CURVA"
    else
      echo "    python scripts/resumo_m2.py $RELATORIO"
    fi
    echo "    (a partir de $RAIZ)"
    exit 0
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "==> O PROCESSO MORREU antes de comecar a processar." >&2
    echo "--- $LOG ---" >&2
    cat "$LOG" >&2 || true
    exit 1
  fi
done

echo "==> subiu, mas nao imprimiu nada em 60 s. Confira: tail -f $LOG" >&2
exit 1
