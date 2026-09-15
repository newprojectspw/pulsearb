# As quatro rodadas do 4.2, e por que elas correm JUNTAS

Cada arquivo aqui é **uma rodada** do item 4.2: liga no máximo uma regra
experimental e desliga as outras duas explicitamente.

    sudo cp deploy/pulsearb-shadow-maker@.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now pulsearb-shadow-maker@base
    sudo systemctl enable --now pulsearb-shadow-maker@recolher
    sudo systemctl enable --now pulsearb-shadow-maker@ancora
    sudo systemctl enable --now pulsearb-shadow-maker@pausa

## Juntas, e não uma depois da outra

Rodar as quatro em sequência custa **56 dias**. Mas o tempo é o menor dos
dois problemas.

O grande é que quatro rodadas em semanas diferentes comparam **regra com
mercado**. O líquido da semana 3 contra o da semana 1 não isola a regra: a
liquidez, a volatilidade e o próprio conjunto de pools de reward mudam de
semana para semana, e a diferença entre as duas semanas entra no resultado
com o nome da regra. É a mesma confusão que a unit já recusa dentro de uma
rodada (duas regras ligadas juntas não se distinguem) — só que espalhada no
tempo, onde é mais difícil de ver.

Quatro processos sobre o **mesmo mercado, nos mesmos 14 dias**, comparam a
regra com a base e só isso.

## O que cada rodada precisa ter de próprio, e o que compartilha

| coisa | por quê |
|---|---|
| diário (`data/diarios/shadow-maker-%i.jsonl`) | somar duas rodadas no mesmo arquivo é o defeito que o `caminho_do_diario_da_rodada` já fecha com `O_EXCL` |
| registro de risco (`data/risco/registro_maker_%i.json`) | o `_gravar` monta o `.tmp` a partir do caminho do registro: dois processos no MESMO registro escrevem o mesmo temporário e o rename atômico pode publicar uma mistura dos dois |
| `KILL` | **compartilhado de propósito.** A chave existe para parar tudo, não uma rodada |

Nada mais é compartilhado: cada processo abre os próprios feeds, tem a
própria `CaixaDoMaker` e não envia ordem nenhuma — o cliente é o sombra.

## E no fim (ou na primeira hora)

    .venv/bin/python scripts/resumo_da_rodada_maker.py \
        --relatos relatorios/RELATOS_base.jsonl \
        --diario data/diarios/shadow-maker-base.jsonl

O leitor recusa com nome a rodada que não vale — inclusive a que ligou duas
regras por engano. Rode nas quatro e compare o `líquido pro-rata` de cada uma
contra o da `base`.
