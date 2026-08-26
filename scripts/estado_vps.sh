#!/usr/bin/env bash
# Diz, numa ida só, se a VPS está em dia e se o disco aguenta a próxima
# gravação.
#
#   ./scripts/estado_vps.sh                      # host do ~/.pulsearb-vps
#   ./scripts/estado_vps.sh root@1.2.3.4         # host explícito
#
# Roda NA MÁQUINA DE ANÁLISE (o Mac), que é onde estão a chave e o `ssh`.
#
# POR QUE NÃO É SÓ UM ALIAS. A versão manual desta conferência nasceu
# encadeada com `&&`, e a primeira coisa que ela fez foi esconder o que
# interessava: o `git rev-parse origin/main` falhou naquele clone — a ref
# remota não existe lá —, o `&&` cortou a cadeia, e o estado do serviço e do
# disco nunca chegaram a ser consultados. Um `fatal:` de git respondeu por
# uma pergunta que era sobre systemd e espaço livre.
#
# Aqui cada item é consultado por conta própria e responde por si. Item que
# não deu para apurar aparece como DESCONHECIDO, nunca como "ok" — estado
# desconhecido é motivo de olhar, não de seguir em frente, a mesma regra que
# o portão de risco aplica ao dinheiro.
#
# O comando remoto vai em aspas SIMPLES e o diretório entra por stdin. Não é
# preciosismo: interpolar `$DIR` na string do ssh faz a expansão acontecer
# aqui e o resultado ser reinterpretado pelo shell de lá, com as duas
# passadas de aspas que isso implica.
set -euo pipefail

ARQUIVO_DE_HOST="${PULSEARB_VPS_HOST_FILE:-$HOME/.pulsearb-vps}"
DIR="${PULSEARB_VPS_DIR:-/opt/pulsearb}"
SERVICO="${PULSEARB_VPS_SERVICE:-pulsearb-recorder}"

HOST="${1:-${PULSEARB_VPS_HOST:-}}"
if [ -z "$HOST" ] && [ -r "$ARQUIVO_DE_HOST" ]; then
  HOST="$(tr -d '[:space:]' < "$ARQUIVO_DE_HOST")"
fi
if [ -z "$HOST" ]; then
  echo "uso: $0 usuario@host" >&2
  echo "ou guarde o host uma vez:  echo usuario@host > $ARQUIVO_DE_HOST" >&2
  exit 2
fi

echo "==> consultando $HOST:$DIR"

# ShellCheck não enxerga dentro das aspas simples; as variáveis abaixo são
# do shell REMOTO, montadas lá.
if ! saida=$(printf '%s\n%s\n' "$DIR" "$SERVICO" | ssh -o ConnectTimeout=15 "$HOST" '
  read -r dir
  read -r servico
  cd "$dir" 2>/dev/null || { echo "DIRETORIO=INACESSIVEL"; exit 3; }
  echo "COMMIT_LOCAL=$(git rev-parse --short HEAD 2>/dev/null || echo DESCONHECIDO)"
  if git fetch -q origin main 2>/dev/null; then
    echo "COMMIT_ORIGEM=$(git rev-parse --short FETCH_HEAD 2>/dev/null || echo DESCONHECIDO)"
  else
    echo "COMMIT_ORIGEM=DESCONHECIDO"
  fi
  echo "SUJO=$(git status --porcelain 2>/dev/null | wc -l | tr -d " ")"
  echo "SERVICO=$(systemctl is-active "$servico" 2>/dev/null || echo desconhecido)"
  df -h data 2>/dev/null | tail -1 | while read -r _fs tam _usado livre pct _resto; do
    echo "DISCO=$livre livres de $tam ($pct usado)"
  done
  echo "GRAVACOES=$(find data/recordings -name "*.jsonl.gz" 2>/dev/null | wc -l | tr -d " ")"
  echo "ULTIMA=$(ls -t data/recordings/*.jsonl.gz 2>/dev/null | head -1 | xargs -r basename)"
'); then
  echo "==> nao consegui falar com $HOST (ssh falhou)" >&2
  exit 1
fi

commit_local="DESCONHECIDO"; commit_origem="DESCONHECIDO"
sujo=""; servico=""; disco=""; gravacoes=""; ultima=""
while IFS='=' read -r chave valor; do
  case "$chave" in
    COMMIT_LOCAL)  commit_local="$valor" ;;
    COMMIT_ORIGEM) commit_origem="$valor" ;;
    SUJO)          sujo="$valor" ;;
    SERVICO)       servico="$valor" ;;
    DISCO)         disco="$valor" ;;
    GRAVACOES)     gravacoes="$valor" ;;
    ULTIMA)        ultima="$valor" ;;
    DIRETORIO)     echo "==> $DIR nao existe ou nao da para entrar" >&2; exit 3 ;;
    *)             : ;;
  esac
done <<< "$saida"

echo
echo "  codigo    ${commit_local} (VPS)  ${commit_origem} (origin/main)"
if [ "$commit_local" = "DESCONHECIDO" ] || [ "$commit_origem" = "DESCONHECIDO" ]; then
  echo "            DESCONHECIDO: nao da para afirmar que esta em dia"
elif [ "$commit_local" = "$commit_origem" ]; then
  echo "            em dia"
else
  echo "            ATRASADA — atualize com:"
  echo "            ssh $HOST 'cd $DIR && git pull origin main'"
fi
# `A && echo` como comando solto sairia do script com `set -e` sempre que
# A fosse falso — que aqui e o caso NORMAL (arvore limpa).
if [ "${sujo:-0}" != "0" ]; then
  echo "            ${sujo} arquivo(s) modificado(s) na VPS, fora do git"
fi

echo "  servico   ${servico:-DESCONHECIDO} (${SERVICO})"
echo "  disco     ${disco:-DESCONHECIDO}"
echo "  gravacoes ${gravacoes:-DESCONHECIDO} arquivo(s), ultima: ${ultima:-nenhuma}"
echo
# A nota muda com o estado porque a MESMA palavra significa coisas opostas:
# com a coleta encerrada, `inactive` e o certo; com ela em curso, e o
# defeito. Uma nota fixa treinaria o leitor a ignorar as duas.
case "$servico" in
  active)
    echo "Servico no ar. Olhe o disco acima: gravacao que enche o disco para"
    echo "com 'No space left', e a hora corrente se perde."
    ;;
  inactive)
    echo "Servico parado. Com a coleta encerrada, e o esperado; se deveria"
    echo "estar gravando, o conserto esta no runbook secao 5."
    ;;
  *)
    echo "Estado do servico DESCONHECIDO — nao conclua que esta parado."
    ;;
esac

# Sai diferente de zero quando o estado do codigo nao pode ser apurado: quem
# chamar isto de um script precisa distinguir "em dia" de "nao consegui ver".
if [ "$commit_local" = "DESCONHECIDO" ] || [ "$commit_origem" = "DESCONHECIDO" ]; then
  exit 4
fi
