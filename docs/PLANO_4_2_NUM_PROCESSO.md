# Plano — as quatro variantes do 4.2 num processo só

**Estado: PROPOSTA.** Nada aqui está implementado, e nada aqui fecha item do
quadro. O que o autoriza é a decisão do operador; o que o fecharia é a
medida sobre o processo escrito.

## Por que, em dois números

| | medido |
|---|---|
| a rota maker inteira (conexão de pools, descoberta, cotação) | **1,99 pp de um núcleo** |
| tudo o mais que sobe junto com ela | **~37,9 pp** |

As quatro rodadas do 4.2 diferem em **três escalares** e no caminho do
registro de risco. Hoje cada uma paga os ~38 pp por conta própria — quatro
vezes o mesmo trabalho sobre os mesmos mercados, os mesmos livros e as
mesmas assinaturas — e é essa multiplicação, não a estratégia, que pediu
uma máquina de 4 vCPU (runbook §10.1f, §10.1j).

**O veredito do §10.1f continua correto para a topologia de quatro
processos.** Este plano troca a topologia, não a medida.

## O que fica compartilhado e o que fica por variante

| peça | escopo | por quê |
|---|---|---|
| feeds RTDS, `clob[pools]`, `CicloAoVivo` | **um** | é o trabalho caro, e é idêntico |
| descoberta de pools | **um** | as variantes cotam os mesmos pools |
| `LacoMaker` | **por variante** | é o objeto da comparação |
| `ClienteSombraDeOrdens` + diário | **por variante** | `data/diarios/shadow-maker-<nome>.jsonl` |
| `PortaoDeRisco` + registro do dia | **por variante** | `data/risco/registro_maker_<nome>.json` |
| arquivo de kill | **um** | é uma pessoa puxando a chave; vale para todas |

**Ganho colateral da comparação:** hoje as quatro conexões recebem o mesmo
mercado com microdiferenças de chegada. Num processo, as variantes decidem
sobre **o mesmo objeto de livro, no mesmo instante** — a comparação fica
mais limpa do que a de hoje, não menos.

## O ponto perigoso: o portão de risco

Hoje `_portao_do_ciclo` devolve o portão do executor, e
`PULSEARB_RISK__CAMINHO_DO_REGISTRO` aponta o registro **daquela rodada** —
ou seja, taker e maker de um processo dividem um orçamento.

Num processo com N variantes isso não pode ficar como está: com um portão
só, a exposição da variante A consumiria os tetos de B, `POSICOES_MAX_ABERTAS`
e `EXPOSICAO_MAX_USDC` estourariam quatro vezes mais rápido, e a partir daí
quem cotasse primeiro ganharia o orçamento. **As variantes pareceriam
diferentes por ordem de chegada, não por mérito** — e é exatamente a
comparação entre regras que o 4.2 existe para fazer.

Verificado na fonte (`risk/gates.py`):

- `PortaoDeRisco(caminho_do_registro=...)` já aceita o caminho por instância;
- `_kill_acionado()` lê um **arquivo** — N portões continuam obedecendo à
  mesma chave de emergência, que é o comportamento que se quer;
- o disjuntor mora no `RegistroDoDia`, que é por arquivo — cada variante
  arma o seu com as suas próprias perdas, que é o comportamento correto.

**A mudança de semântica que isto traz, dita antes de acontecer:** hoje a
exposição do taker conta contra o maker da mesma rodada. Num processo com o
taker separado (ou desligado), deixa de contar. Isso afeta **todas as
variantes igualmente**, então a comparação entre elas segue válida — mas os
números **não são comparáveis com as rodadas r4–r8**, e isso tem de sair no
relatório, não só aqui.

## A decisão que simplifica: desligar a rota taker neste processo

O taker está **medido e reprovado** (quadro 1.1/1.4/1.5, −195,25 USDC em
2.069 trades). Mantê-lo dentro do processo do 4.2 custa três coisas:

1. a conexão `clob[updown]` — **54% do tráfego, rajadas de 4 MB/s**
   (medido em 2026-09-14, gravado em `live/shadow.py`);
2. o acoplamento de orçamento descrito acima;
3. `laco_de_descoberta` e `laco_de_decisao` rodando por nada.

Hoje as três subidas são **incondicionais**; só a rota de pools tem trava.

### ✅ Fase 0 respondida — 2026-09-22

A pergunta era: *a rota maker depende de algo que o laço do taker produz?*
Eu tinha escrito que os caminhos "aparentam ser distintos". **Aparentavam, e
não são.** Existem duas dependências, e só uma é inofensiva.

`laco_de_cotacao` passa cinco coisas ao `LacoMaker.passo` (`shadow.py:620`).
Duas vêm de estado compartilhado com o taker:

#### 1. O rastreador é UM só — e isso não atrapalha

`ciclo.on_descoberta` chama `rastreador.atualizar(mercados)` e
`_um_ciclo_de_pools` chama `rastreador.absorver(janelas)`: **o mesmo dict
`self.janelas`**, e `abertas()` devolve tudo misturado. O maker recebe as
janelas Up/Down do taker junto com os pools.

Mas ele já as recusa, com motivo nomeado: `_passo_da_janela` chama
`self._parametros(janela)` e, sem pool de reward, conta
`sem_pool_de_reward` — **os 772 do relato de 2026-09-21 são exatamente
isso**. Desligar o taker tira essas janelas do rastreador e portanto **não
muda nada do que o maker cota**; só faz o contador parar de subir.

Nenhum dos dois métodos aposenta o que o outro pôs (ambos documentam isso),
então não há risco de a descoberta do taker apagar os pools.

#### 2. `feeds_saudaveis` — e esta MATA o maker

A cadeia, verificada de ponta a ponta:

| passo | arquivo |
|---|---|
| `feeds_saudaveis=self.ciclo.feeds_saudaveis(...)` | `shadow.py:624` |
| `if not self.ultimo_preco_ns: return False` | `ciclo.py:252` |
| `ultimo_preco_ns[tick.asset] = ...`, só de tick `twap_sixty` filtrado por `ativos_operados` | `ciclo.py:153` |
| `ativos_operados=frozenset(settings.assets)` | `shadow.py:168` |
| `if not feeds_saudaveis: return Decisao(False, MOTIVOS.FEED_PARADO)` | `gates.py:559` |
| `return await self._recusar(janela.slug, f"portao:{recusa}")` | `laco_maker.py:369` |

**Esvaziar `settings.assets` para desligar o taker faria o maker recusar
tudo com `portao:feed_parado`** — e produziria exatamente o quadro do
§10.1l: rodada saudável, `falhou: null`, cotando zero, parecendo mercado
quieto.

#### O que isto obriga a fase 1 a fazer

"Desligar o taker" não é uma coisa, são três — e só duas podem cair:

| desligar | pode? | efeito no maker |
|---|---|---|
| `laco_de_decisao` (o `passo()` do taker) | ✅ | nenhum: o maker não lê nada que ele produza |
| `laco_de_descoberta` + conexão `clob[updown]` | ✅ | para de receber janelas que já eram recusadas por `sem_pool_de_reward` |
| feeds RTDS / `settings.assets` | ❌ **NUNCA** | `feeds_saudaveis` vira False e o maker recusa tudo |

A trava da fase 1, portanto, **não pode ser `assets=[]`** — tem de ser um
botão próprio que não toque no feed de preço. E o teste que a acompanha tem
de exercer justamente isto: com o taker desligado, o maker continua cotando.

#### A pergunta que este achado levanta, e que não é da fase 1

Por que cotar *"vai chover em Wellington"* exige um preço de BTC fresco? O
`feeds_saudaveis` é um conceito do **taker** — a frescura do feed-verdade do
jogo TWAP. Para a rota maker sobre pools, a frescura que importa é a do
livro do próprio pool, e essa já é vigiada em `_livro_para_o_maker` pela
saúde da conexão `clob[pools]`.

O portão está aplicando ao maker um critério que não é dele. Erra para o
lado fechado, então **não é urgente** — mas é a razão pela qual um corte
inocente em `assets` derrubaria o ensaio em silêncio. Fica registrado aqui e
não vira trabalho agora.

## O que se perde: isolamento entre variantes

Hoje uma variante que morre não leva as outras. Num processo só, leva.

**A recomendação é deixar que leve** — e a razão é a regra da falha fechada.
Uma variante que morre em silêncio enquanto as outras seguem produz 14 dias
de dado onde uma das regras tem cobertura menor que as demais, que é
precisamente o que o §10.1c existe para impedir. Cobertura desigual entre
rodadas é pior que uma queda visível.

E o mecanismo de recuperação já existe: o `systemd` reinicia, e
`scripts/resumo_da_rodada_maker.py` já corta o diário em trechos onde
`parede_s` cai e os soma — ele foi escrito para exatamente este caso.

**O que NÃO se aceita:** uma variante que levanta exceção e é engolida por
um `except` largo, com as outras seguindo. Se isso for implementado, o
ensaio mente.

## As travas, e cada uma com o seu teste

Nenhuma destas é opcional, e todas vão verificadas por mutação — desligar a
trava tem de reprovar o teste:

1. **Diários não se cruzam.** Uma cotação da variante A nunca aparece no
   diário de B.
2. **Registros de risco não se cruzam.** Exposição registrada por A não
   consome teto de B. (Este é o teste que protege a comparação inteira.)
3. **O kill derruba TODAS.** Arquivo de kill presente → nenhuma variante
   cota, e cada uma recusa com `MOTIVOS.KILL_ACIONADO`.
4. **Os três botões chegam na variante certa.** Uma tabela de variantes com
   valores distintos produz `LacoMaker`s com esses valores, e trocar a
   ordem da tabela não troca os botões.
5. **Variante que levanta derruba o processo.** O oposto do `except` largo,
   e é o teste que impede alguém de "consertar" isso depois.
6. **Sem portão, não cota.** A trava que já existe (`sem_portao`) continua
   valendo por variante.

## De onde vêm os botões de cada variante

Os quatro `.env` de hoje não servem: `EnvironmentFile` entrega **um**
conjunto de variáveis ao processo, e aqui são N.

Proposta: um arquivo versionado, `deploy/rodadas/variantes.yaml`, com um
bloco por variante (nome, os três escalares, diário, registro). Os `.env`
atuais viram a fonte desse arquivo e continuam existindo enquanto a
topologia velha existir — **não se apaga o caminho antigo antes de o novo
estar medido**.

`comum.env` não muda: o perfil do ensaio continua igual para todas, que é o
que faz a comparação ser sobre a REGRA.

## Fases, e o que cada uma entrega

| fase | entrega | fecha o quê |
|---|---|---|
| 0 | ✅ **feito em 2026-09-22** — ver acima: o acoplamento existe e é no `feeds_saudaveis` | habilitou a 1, com uma restrição que ela não tinha |
| 1 | trava que desliga `laco_de_decisao`, `laco_de_descoberta` e `clob[updown]` — **sem tocar em `assets`** — + medida de `%CPU` | substitui a estimativa de 40–46 pp por medida |
| 2 | N variantes num processo, com as 6 travas e seus testes | o caminho do 4.2 nesta máquina |
| 3 | uma hora de `%wait` com as quatro | ✅ ou ❌ para "cabe em 1 vCPU" |
| 4 | §10.1g inteiro e o relógio dos 14 dias começa | o ensaio do 4.2 |

**A fase 3 é a que pode reprovar tudo**, e a leitura fica escrita antes de
rodar: `%wait` perto de 0,4% aprova; na casa dos 15–35% reprova, e aí o 4.2
não tem caminho em 1 vCPU por nenhum dos desenhos que conhecemos.
