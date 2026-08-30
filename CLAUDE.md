# PULSEARB — como trabalhar neste repositório

## O quadro é a fonte da verdade, e ele mora aqui

`docs/ESTADO_PARA_LIVE.md` é o estado do projeto. Não é relatório: é o
documento que decide se o bot pode operar com dinheiro real.

**Regra 1 — o quadro anda no MESMO commit que o conserto.** Quem fecha um
item edita a linha dele antes de commitar. Quadro atualizado num commit
separado, "depois", é quadro que fica para trás — e um quadro que mente sobre
o que está pronto é pior que não ter quadro, porque alguém decide LIVE a
partir dele.

**Regra 2 — nada vira ✅ sem evidência que se possa reproduzir.** Marque:

| símbolo | quando |
|---|---|
| ✅ | existe, roda, e há teste ou medida que prove |
| 🟡 | parte existe — **escreva o que falta**, não só "parcial" |
| ❌ | medido e reprovado (guarde o número que reprovou) |
| ⬜ | não começou |

Número sintético não fecha item. "Passou no meu teste" não fecha item que
exige medida sobre gravação real. O M2 inteiro existe para fazer valer isso.

**Regra 3 — o que falta vai escrito.** `🟡 falta rodar as 24 h` é útil.
`🟡 parcial` não é: seis meses depois ninguém sabe o que faltava.

## Duas sessões trabalham neste repositório ao mesmo tempo

Uma na nuvem (sem `data/`), outra no Mac (com as gravações reais). As duas
empurram para o mesmo remoto.

- **Sempre `git pull --rebase` antes de qualquer push.** Já houve push
  recusado por não fazer isso.
- Trabalho grande vai em branch própria com PR, não direto na branch
  compartilhada.
- Antes de editar um arquivo que a outra sessão está mexendo, diga no commit
  ou abra PR. Conflito em `docs/ESTADO_PARA_LIVE.md` é o mais provável de
  todos — é o arquivo que as duas tocam.

## O que este projeto trata como erro grave

São as lições que já custaram caro aqui. Elas não são estilo:

**Falha fechada.** Estado desconhecido é motivo de RECUSA, não de seguir em
frente. Feed sem carimbo, registro ilegível, tick desconhecido, relógio não
monitorado — todos recusam. Um portão que "não sabe" e deixa passar não é
portão.

**Toda recusa tem nome.** Constante em `MOTIVOS`, nunca frase livre. Recusa
anônima não vira métrica nem alarme, e não distingue "o bot está travado" de
"o bot não achou trade".

**Mesmo caminho.** O motor ao vivo e o backtest usam as MESMAS funções. Se
cada um tiver a sua cópia, uma divergência entre SHADOW e backtest parece
diferença de mercado quando é diferença de código — e é justamente essa
comparação que justifica o SHADOW existir.

**Timeout não é recusa.** Recusada = o servidor respondeu e disse não.
Incerta = a resposta não chegou e a ordem PODE estar no livro. Tratar incerta
como recusada e reenviar é posição dupla. Ver `execution/cliente.py`.

**Fato de API vai VERIFICADO na fonte.** `docs/API_NOTES.md` cita arquivo e
símbolo. Campo assumido a partir do que parecia razoável já produziu dois
defeitos silenciosos aqui (`price_change` §6.1b e `market_resolved` §12.13):
o parser lia zero eventos e o relatório saía normal.

**Teste que encoda a suposição não é teste.** Os dois defeitos acima
passaram porque as fixtures eram sintéticas, escritas a partir do que
imaginávamos que o servidor mandava.

## Nada aqui liga o modo LIVE por conveniência

A trava tripla do item 3.4 (`risk/autorizacao.py`) exige `MODE=LIVE` **mais**
a variável `PULSEARB_CONFIRM_LIVE` **mais** a frase exata `EU ACEITO O RISCO`.
`escolher_executor` recusa LIVE sem as três. Não afrouxe isso para testar
nada — use SHADOW.

Chave privada vem de `PULSEARB_CHAVE_PRIVADA`, nunca de `config.yaml`, que é
versionado. Ela não aparece em `repr`, log ou mensagem de erro.

## Comandos

```
make venv     # cria .venv (uv + Python 3.12)
make check    # ruff + pytest — rode ANTES de empurrar
make test
make lint
```

O CI roda `ruff check src/ tests/ scripts/` e `pytest`. `ruff format` **não**
roda: o repositório não está formatado por ele, e rodá-lo produziria um diff
gigante sem relação com o que você mudou.
