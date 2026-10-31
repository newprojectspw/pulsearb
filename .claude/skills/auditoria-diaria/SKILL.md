---
name: auditoria-diaria
description: Audita o que o bot PULSEARB decidiu nas últimas 24 h contra a política de risco e diz ALERTA quando algo saiu dela. Use quando o usuário pedir auditoria, auditor diário, conferir a rodada, "o bot respeitou os tetos?", "o que ele fez hoje?", ou ao fim de uma rodada SHADOW. Também cobre o teste mensal do botão de parada.
---

# Auditoria diária do agente

Um bot que gasta dinheiro precisa de três coisas: teto que ele não ultrapassa,
objetivo que inclua não perder, e parada que não seja zerar. Esta skill confere
as três contra o que o bot **de fato fez**, não contra o que o código promete.

## Rode isto

```bash
.venv/bin/python scripts/auditor_diario.py <diário.jsonl> --registro <registro.json>
```

O diário é `data/shadow/*.jsonl` ou `data/diarios/*.jsonl`. O registro sai do
`config.yaml` (`risk.caminho_do_registro`) quando você omite `--registro`.
Opções: `--horas` (janela, default 24), `--fim-epoch` (default: carimbo mais
novo do diário), `--repouso-minimo-s` (alimenta o alerta de churn).

**Os dois caminhos são sanitizados** (`caminho_de_diario_lido`,
`caminho_de_relatorio_lido`): precisam ser relativos à raiz do repositório.
Caminho em `/tmp` é recusado — é a proteção contra path traversal do #206, não
um defeito. Para ler de outra raiz, `PULSEARB_RELATORIOS_INPUT_ROOT`.

## Como ler a saída

Primeira palavra `ALERTA` ou `LIMPO`. Saída `0` limpo, `1` alerta, `2` recusa.
Dez linhas no máximo, nesta ordem: cabeçalho · movido vs tetos · registro ·
três maiores decisões · fora da política · recusas do portão · estratégia ·
perseguição do objetivo · IDs sem prefixo de sombra.

**`ALERTA` com `RECUSA` não é o mesmo que `ALERTA` com números.** Recusa quer
dizer *não consegui apurar* — diário ausente, registro ilegível, dia do diário
diferente do dia do registro. Estado desconhecido recusa; não vira "limpo".
Se vier `recusa_dia_indeterminado`, você pareou diário e registro de dias
diferentes: pegue o par certo, não force.

Sete recusas: `recusa_diario_ausente`, `recusa_diario_ilegivel`,
`recusa_linha_corrompida`, `recusa_registro_ausente`,
`recusa_registro_ilegivel`, `recusa_dia_indeterminado`,
`recusa_politica_ilegivel`.

Quinze alertas, todos nomeados em `MOTIVOS_DO_AUDITOR`: tetos
(`stake_acima_do_teto`, `janela_acima_do_teto`, `exposicao_acima_do_teto`,
`posicoes_acima_do_teto`, `perda_acima_do_teto`), qualidade
(`preco_fora_da_faixa`, `spread_acima_do_teto`, `latencia_acima_do_teto`),
estado (`disjuntor_armado`, `pausa_em_curso`), e os que olham intenção
(`ordem_real_no_diario`, `mudanca_de_estrategia_nao_permitida`,
`raspando_o_teto`, `concentracao_numa_janela`, `churn_de_cotacoes`).

Alerta sem nome em `MOTIVOS_DO_AUDITOR` é defeito, não escolha: recusa anônima
não vira métrica nem alarme.

## O que já se sabe ao interpretar

**A rota maker em sombra não passa pelo teto por trade do portão.** Medido em
`data/shadow/diario-20260924-044201-802197-41092.jsonl`: 1.544 cotações a até
94,70 USDC contra teto de 5,00, 149 janelas acima de 15,00, movido 76.030 USDC
contra exposição máxima de 50. O maker cota 100 shares por perna por desenho do
ensaio; o portão gravou `shares=5,0` em todas as decisões da rota taker. Então
`alerta_stake_acima_do_teto` em volume, vindo da rota `maker`, é esse desenho
aparecendo — **não** é o taker furando o teto. Diga de qual rota veio o alerta
antes de chamar de defeito. Que o maker deva ou não obedecer ao mesmo teto é
decisão aberta do Paulo.

**`latencia_da_decisao_ms` é sempre 0,0** nos 31 diários existentes. O alerta de
latência está testado contra o teto do `config.yaml`, mas nunca viu latência
real alta. Não conclua "latência boa" dele.

**Repouso mínimo real observado: 15,004 s.** O default de 10 s nunca dispara
churn em dado real; use `--repouso-minimo-s` para sondar.

## O teste mensal da parada

Auditar todo dia não basta: a parada precisa ser exercitada, senão você não sabe
se ela funciona. **Ela já funcionou em setembro**, com o teto de 25: o
disjuntor armou em 05/09 a −27,84 USDC
(`data/risco/arquivo/...20260905-disjuntor-armado.json`) e recusou 156.404
decisões só naquele dia; os registros em `data/invalidado-2026092*` da VPS
mostram mais cinco armadas, de −25,35 a −32,18. **Mas a rodada de 15 dias
(05–19/10) não a exercitou**: correu com
`PULSEARB_RISK__PERDA_MAX_DIARIA_USDC=5000` (`deploy/rodadas/comum.env:47`,
PR #186), e o PnL chegou a −179,24 USDC sem armar. A prova de setembro é sobre
o código daquela época; o teste mensal existe para ela não envelhecer.

Atenção ao que o −179,24 é: o registro **não vira o dia num processo longo**
(`_hoje` fixado no construtor, `risk/gates.py:258`; a virada só acontece em
`_carregar`). Numa rodada contínua de 14 dias, `pnl_realizado_usdc` acumula os
14 dias e o "teto diário" vira teto da rodada inteira. Para perda o erro é
conservador — o acumulado bate o teto antes —, mas não leia o número como
perda de um dia.

Para testar sem arriscar nada: rode em SHADOW com `perda_max_diaria_usdc`
baixíssimo e confirme que `armar_disjuntor` grava `disjuntor_armado: true` com
motivo, que ele **não** desarma na virada do dia, e que o auditor reporta
`alerta_disjuntor_armado`. `desarmar_disjuntor` e `retomar` não têm chamador no
código: são para pessoa, de caso pensado — é o lado certo para errar.

A chave de emergência é um arquivo: `risk.caminho_do_kill`
(`data/risco/KILL`). Criar o arquivo recusa tudo com `MOTIVOS.KILL_ACIONADO`.

## Limites desta skill

Ela audita **decisão**, não resultado. O diário não tem desfecho, então ela não
diz se o bot ganhou ou perdeu — só se ele obedeceu. Lucro e calibração saem de
outro lugar (`scripts/resumo_da_rodada_maker.py`, `relatorios/*.json`).

Nunca ligue LIVE para auditar, nunca afrouxe um teto para o alerta parar de
aparecer, e nunca trate recusa como limpo. O auditor só lê: não abre socket,
não assina nada, não escreve arquivo.
