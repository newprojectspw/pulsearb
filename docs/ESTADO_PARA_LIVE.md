# ESTADO — o que falta para operar com dinheiro real

> ## ✅ BLOCO 1 DESSUSPENSO EM 2026-08-30 — a remediação rodou, e mudou o veredito
>
> O defeito de variância do `prob_up_twap` foi corrigido e, mais que isso,
> **substituído por medição**: a §13.8 já tinha VERIFICADO que a janela resolve
> por um ponto do stream `twap_sixty`, não por uma média que nós calculemos, e
> o modelo derivado subestimava a variância em **39 a 48 vezes** (6,3× no
> desvio) por três erros compostos. A `V(t)` agora é medida em dado real, num
> dia ANTERIOR ao avaliado (curva de 23/08 → avaliação de 24/08), com guarda de
> in-sample em código.
>
> **O que a rodada produziu:** o **1.3 passa** — e a borda some junto. Os itens
> 1.1 a 1.4 desta página estão atualizados com os números do modelo medido; o
> "+2,7125 na banda" foi para o histórico. A atribuição causal dele à
> superconfiança é inferência, não medição — a delimitação está na §2d-ter.

**Semáforo de hoje: 🔴 VERMELHO** — o veredito não mudou, a causa mudou de
novo, e desta vez para pior. Com o preditor consertado
(`relatorios/M2_24AGO_MEDIDO.json`, curva de 23/08 sobre o dia 24/08), o
**1.3 passa nos cinco baldes** — `erro_de_confiabilidade` de **0,0126** a
**0,0493**, todos com 20 faixas ocupadas e `calibracao_avaliavel: true`,
contra 0,207 na banda com o modelo derivado. **E a borda desapareceu no mesmo
movimento:** 688 trades, PnL **−67,27**, `bandas_com_edge: []` — nenhuma das
cinco bandas de horizonte é positiva. Placar do taker: **1.2 e 1.3 ✅ · 1.1,
1.4 e 1.5 ❌**. Como o critério exige as CINCO, **o taker segue reprovado** —
por ausência de borda e por capacidade, não mais por calibração. **O MAKER
também não passa nos cinco:** 1.7, 1.8, 1.9 e 1.10 passam, mas o **1.6 é NÃO
AVALIÁVEL** — esta página chegou a marcá-lo ✅ com +35,6 USDC/8h e isso foi
**revertido em 2026-08-31**, porque contradizia o `resumo_m2.py` e o
`VEREDITO_M2.md`. O número segue registrado como estimativa de ordem de
grandeza, com o método à vista.
**O M2.2 rodou em 2026-08-31** — 24 h com a fórmula confirmada, 126,7 M
registros — e 1.7/1.8 saíram idênticos aos de antes (−0,1974 e 65,922 h),
porque nenhum dos dois passa pela fórmula de reward. A pendência fecha.

> ### 🔬 2026-08-31 — o taker não reprova por falta de sinal. Reprova pela REGRA.
>
> O teste de direção rodou sobre o dia inteiro
> (`relatorios/M2_DIRECAO_20260824.json`, **688 janelas independentes**) e a
> resposta veio do lado que eu não tinha antecipado:
>
> | | |
> |---|---|
> | direção escolhida acerta | **0,4157** · p = **1,16e−05** |
> | se sorteasse, acertaria | 0,5043 (taxa-base de 247.306 previsões) |
> | o preditor está calibrado? | **sim** — ECE 0,0126 a 0,0493, 20 faixas nos 5 baldes |
>
> **O preditor prevê bem e a regra escolhe mal.** `edge = prob − preço >
> threshold` seleciona 688 momentos de 247 mil e acerta **menos que sortear**.
> Não é ausência de borda: é uma regra de entrada anti-informativa, medida
> sobre uma coorte que não muda com o fill.
>
> **Isso move o conserto de lugar:** trocar o preditor por outro igualmente
> calibrado daria o mesmo resultado. O defeito está em QUANDO se entra.
>
> **E não autoriza inverter a regra** — comprar o que o modelo acha caro não
> tem razão a priori de funcionar, e não foi testado.
>
> **A seleção adversa deixou de ser hipótese (§2d-quinquies).** Quebrando as
> 688 apostas pela confiança do lado apostado: **543 delas (79 %) saem entre
> 0,45 e 0,55** — a regra opera onde o modelo não sabe —, e ali ele realiza
> **8,75 pontos abaixo do que promete**. No agregado de 247 mil previsões o
> mesmo modelo é calibrado. A comparação é pareada por construção, e a
> diferença é a seleção.
>
> **O número aguenta peso:** reproduziu ao dígito em **quatro** rodadas
> independentes, e o único viés conhecido do instrumento — quando os dois
> lados passam do threshold, o registrado é Up por ordem da lista — foi
> medido em **0 de 149.448 sinais**.
>
> **O mecanismo:** `edge = prob − ask > threshold` dispara em qualquer lado.
> Com o modelo em 0,48 e o ask em 0,44 a margem existe *se o modelo estiver
> certo*, e a regra compra Up mesmo achando Down mais provável. Ela otimiza a
> discordância com o preço, não a convicção — e quando a convicção é nula, o
> que resta é o caso em que discordar do mercado sai caro.

**A sensibilidade à latência inverteu.** Com o modelo defeituoso o PnL da
banda decaía monotonicamente com a latência (+3,3119 a 150 ms → +0,4736 a
1000 ms), e eu citava esse decaimento como evidência de sinal direcional real.
Com o modelo medido o PnL **melhora** com latência (−67,94 a 150 ms → −55,78 a
1000 ms). O que isso estabelece é que **a evidência que eu invocava antes não
sobrevive** — mesmo instrumento, sinal oposto. O que NÃO estabelece é ausência
de direção: a varredura mantém o sinal fixo e troca só o book do fill, então
ela mexe no preço de entrada e na coorte de trades ao mesmo tempo (ver §2d-ter
do `VEREDITO_M2.md`). Quem carrega a conclusão é o resto — PnL negativo nas
**cinco** bandas e `hit_rate` 0,4172, abaixo de 0,5 no lado escolhido pelo
modelo. **Sobre o +2,7125:** a leitura de que ele vinha do desvio-padrão 6,3×
pequeno demais — `P(Up)` saturado em 0 e 1, amostra pequena fazendo o resto — é
a explicação mais econômica, e não está medida. O preditor corrigido opera
outra coorte de trades, então esta rodada não testa aqueles 640. O que está
medido é que a regra corrigida perde. A delimitação completa está na §2d-ter.

**O 1.5 reprova por motivo independente e continua igual:** p50 de 128,05 USDC
a 3 ticks contra os 200 exigidos. É teto de **capacidade** do book — nenhum
conserto de preditor o resolve, e restringir horizonte não cria liquidez.

Isto NÃO invalida o M4 (portões de risco, SHADOW, ciclo ao vivo): é
exatamente a máquina que permite medir sem arriscar. Invalida a decisão de
ligar o LIVE com a estratégia taker atual.

**Decisão pré-registrada em aberto, de Paulo:** encerrar o taker no registro,
ou virar para a rota maker. **O que mudou em 2026-08-31 torna a escolha mais
dura, não mais fácil:**

- o **taker** tem causa medida — a regra de entrada seleciona pior que o
  acaso (0,4157, p=1,16e−05, quatro rodadas) — e **as duas correções óbvias
  foram testadas e falharam**: exigir convicção mínima não salva, e inverter é
  2,4× pior. O motivo de inverter falhar é o que encerra a rota: a soma dos
  asks é **1,0210**, e o spread de 2,1 % é maior que qualquer vantagem
  direcional. **As duas pontas perdem** — o taker paga o spread, e é isso;
- o **maker** perdeu o ✅ do 1.6, que era o critério que fazia a rota parecer
  aprovada. Passa em 1.7, 1.8, 1.9 e 1.10; o 1.6 é **não avaliável por
  construção** enquanto a fila não for observável;
- o **motor maker rodou, e o resultado é pior que não rodar** (item 4.0):
  o laço fechou em 2026-09-07 e a rodada de 24 h de 08/09 fez **44.430
  avaliações sem uma única cotação repousar**, com motivo ÚNICO
  `sem_pool_de_reward`. A peça que faltava deixou de ser o motor.

O que o M2.2 fechou: 1.7 e 1.8 remedidos em 24 h com a fórmula certa, iguais
aos de antes. O maker está inteiro em código — cotar (`live/cotacao.py`, 20
testes), mexer ou deixar (`live/repouso.py`, 17), o I/O que cancela e
reconcilia (`live/execucao_maker.py`, 15) e o laço que os chama
(`live/laco_maker.py`, 7). **O que falta não é peça: é pool.**

> ⚠️ **DUAS MEDIDAS DESTE DOCUMENTO SE CONTRADIZEM, e a contradição está
> aberta.** A seção *"O pool de reward não é esporádico"* mede, sobre
> `M2_20260824.json`, **8 de 8 janelas de 4 h COM pool (100 %)**. A rodada de
> SHADOW de 2026-09-08 mede **44.430 avaliações sem uma única janela com
> pool**, motivo único `sem_taxa_diaria`. As duas não podem estar certas ao
> mesmo tempo sobre o mesmo programa.
>
> São três as leituras possíveis, e elas levam a decisões opostas: **(a)** o
> programa mudou entre 24/08 e 08/09 e os updown de 4 h saíram dele — achado
> sobre a Polymarket, não sobre nós; **(b)** a extração de agosto contava pool
> que não existia, e aí a conclusão daquela seção (*"a rota é restrita à
> janela de 4 h, onde o pool está sempre lá"*) **cai inteira**; **(c)** as duas
> medem conjuntos de mercados diferentes.
>
> **O teste que decide é barato e ainda não foi feito:** a gravação de agosto
> guardou `raw_gamma`, e desde 2026-09-07 existe UM leitor compartilhado
> (`markets/rewards_da_gamma.py`). Reler o `raw_gamma` de 24/08 com o leitor de
> hoje separa (a) de (b) em uma passada — e a passada foi feita.
>
> **RESOLVIDO em 2026-09-09: é (a).** O leitor de HOJE
> (`markets/rewards_da_gamma.py`) rodado sobre o relatório de agosto
> (`relatorios/M2_20260824.json`) devolve `duracoes_com_pool:
> {"14400": 8, "300": 1, "900": 1}` e, em `duracoes_sem_pool`,
> `{"300": 518, "900": 167, "3600": 44}` — **sem nenhuma entrada `14400`**.
> Isto é: em 24/08 as **8 de 8 janelas de 4 h tinham pool**, e nenhuma ficou
> sem. A medida de agosto estava CERTA, e a afirmação do quadro também estava,
> naquela data.
>
> Não é leitor diferente contando diferente: a extração de agosto registrou
> `forma_do_rewards_bruto: {"sem_lista_de_rewards": 729}`, que é o MESMO
> diagnóstico que hoje devolve `chave_da_lista: None`. Mesma pergunta, mesmo
> campo, respostas opostas em datas diferentes.
>
> **Conclusão: o pool saiu das janelas de 4 h entre 24/08 e 08/09.** Mudou do
> lado da Polymarket, não do nosso. O programa segue vivo — só que agora em
> mercados de horizonte longo, e **nenhum** de janela curta.
>
> **CORRIGIDO EM 2026-09-13 — o número anterior estava errado por 27×.** A
> varredura de 08/09 amostrou 2.500 mercados da *Gamma* e reportou "940 com
> pool, 6.766 USDC/dia". Medindo na fonte autoritativa — `GET
> /rewards/markets/current` do CLOB, que é a lista do próprio programa,
> paginada até o fim — são **18.384 mercados com pool, somando 185.520
> USDC/dia**. A amostra não errou a direção (o programa vive fora das janelas
> curtas); errou a escala, e por muito. Medição em
> `scripts/varredura_de_pools.py`; ver §2f do `VEREDITO_M2.md`.
>
> **O que isso muda para a decisão:** a rota maker não era inviável por
> desenho, e o trabalho feito nela não foi desperdício — ela tinha onde ganhar
> há duas semanas. O que a inviabiliza é uma mudança de programa da
> contraparte, fora do nosso controle e que pode se reverter. Isso é diferente
> de "a estratégia estava errada", e a decisão de encerrar ou esperar deve ser
> tomada sabendo disso.
> rodar, **nenhuma das duas seções deve ser citada como fato isolado**.

Atualizado: 2026-08-31 · fonte dos números correntes:
**`relatorios/M2_24AGO_MEDIDO.json`** — 24 horas de gravação real de
2026-08-24 (126,7 M registros, `pior_fracao_coberta 1,0`, 0 silêncios),
avaliadas com o preditor de variância **medida** sobre a curva de 23/08
(`relatorios/VARIANCIA_23AGO.json`), 688 trades. As rodadas do preditor
derivado — `M2_24AGO.json` (695 trades) e `HORIZONTE_240_120_v2.json`
(640 trades) — passaram a ser **histórico**, e é delas que vêm os números do
**restante do Bloco 1**, da tabela de critérios até o fim daquele bloco. Os
demais blocos são estado corrente. O veredito de 20 h, com 837 s de silêncio e
42% dos snapshots descartados, segue em `docs/VEREDITO_M2.md`.

> Como ler: **✅** passou com dado real · **✅ *na banda*** (só no histórico)
> passou apenas restrito à faixa 240-120 s de tempo restante · **❌** medido e reprovado ·
> **🟡** parcial · **⚠️** não avaliável por construção · **⏳** sem amostra
> suficiente · **⬜** não existe / não começou.
> Um item só vira ✅ com número de gravação real. Número de gravação
> sintética não conta — é a regra que o M2 existe para fazer valer.

**As contagens de teste desta página são conferidas pela suíte**
(`tests/test_quadro_nao_mente.py`, 2026-08-31). Quando um item diz "47
testes" como evidência, esse número é comparado com o que o pytest coleta de
verdade, e o teste quebra se divergirem — com a mensagem dizendo qual linha
daqui ficou para trás. É a Regra 1 do `CLAUDE.md` executável: sem isso, o
número envelhece em silêncio, porque quem adiciona um teste raramente abre o
quadro, e a linha segue afirmando o valor antigo com cara de evidência.

*(A primeira versão desse teste passava verde sem conferir nada: procurava
`N tests collected` na saída do pytest, que no modo `-q` sai como
`tests/arquivo.py: N`, não achava, e caía num `skip`. Agora ausência de
medição é falha, não skip — teste que não mede tem de falhar alto.)*

---

## Bloco 0 — Captação (bloqueia tudo o que vem depois)

Gravar mais horas antes disso só produz mais horas meio-cegas.

| # | Item | Estado | Evidência |
|---|---|---|---|
| 0.1 | Flush do silêncio de `conexao_inteira` até o fim da gravação | ✅ | M2.10, teste trava; falhava antes |
| 0.2 | `suspeita_de_assinatura_caducada` não acusa conexão morta | ✅ | M2.10, teste trava; acusava 7 falsos |
| 0.3 | `total_s` como união, não soma | ✅ | M2.10; dava 14.476 s numa hora de 3.600 s |
| 0.4 | Reassinatura decide por (tópico, ativo), não por tópico | ✅ | M2.10, teste trava |
| 0.5 | **O recorder rodava com o M2.7?** | ✅ **RESPONDIDO: sim, e a defesa NÃO funcionou** | log da VPS: 2.482 reassinaturas, uma a cada 5 s, sem recuperação |
| 0.6 | **Escalada: derrubar o socket após N reassinaturas sem efeito** | ✅ **deployada** em 2026-08-23 01:31 | M2.11; ainda sem oportunidade de agir — 0 alarmes desde então. **2026-09-17, achado da fixture real (uma hora da M2_72H):** o servidor RESPONDE à reassinatura com `statusCode 400 … connection_id_fk` e `500 … __subscriptions does not exist` (API_NOTES §6.2b) e ninguém lia — 4 recusas na hora. O `400` diz que o servidor já não tem a nossa conexão: reassinar sobre ela é o que o 0.5 mediu 2.482 vezes sem efeito. 🟡 **sub-item novo: ler a recusa e derrubar na primeira** — código pronto (`feeds/rtds.erro_do_servidor`, `reconexoes_por_recusa`, close 1012; 7 testes com as duas mensagens reais verbatim), **falta a medida**: a próxima gravação de 72 h diz se os 83 silêncios com assinatura caducada do 0.8 caem |
| 0.7 | **Cobertura > 95 % em todos os ativos** | ✅ **0,9994** | hora 23:00, 99,9 % nos 8 ativos, `silencios: 0` |
| 0.8 | 72 h contínuas e limpas | ❌ **MEDIDO E REPROVADO em 2026-09-18 — o número que reprovou é 83,04 s** | **A BARRA, decidida por Paulo em 2026-09-18 entre três opções postas antes de ele ver o resultado de cada uma.** Uma gravação de 72 h é *limpa* quando passa nas DUAS metades, porque elas medem coisas diferentes: **(saúde do feed)** soma dos silêncios ≤ 1 % da janela **E** nenhum silêncio > 60 s **E** ≤ 2 quedas de conexão inteira; **(dano às medidas)** janelas resolvidas que fecharam dentro de uma lacuna do stream ≤ 0,5 %. Só o dano não basta: uma gravação pode ter dano baixo por sorte, se os silêncios calharem em horas sem janela a fechar. Só a saúde não basta: mede o feed e não o estrago. **Esta gravação (09/09 02:19 → 12/09 02:19 UTC) passa em três dos quatro termos e reprova num:** soma 614,25 s = 0,24 % ✅; quedas de conexão 2 ✅; fechamentos em lacuna 8 de 2.318 = 0,35 % ✅; **maior silêncio 83,04 s ❌** — mais longo que a janela de 60 s do TWAP, ou seja, uma janela de 5m que fechasse ali teria o preço de liquidação em branco. **A causa tem nome desde 2026-09-17** (API_NOTES §6.2b): 99 dos 101 silêncios são de tópico com a conexão viva, 83 deles com `suspeita_de_assinatura_caducada`, e o RTDS respondia `statusCode 400 … connection_id_fk` às reassinaturas sem ninguém ler. **O que fecha o item:** UMA gravação nova de 72 h com o conserto do §6.2b a correr (o feed derruba o socket na primeira recusa) que passe nos quatro termos. Não é análise: é gravar de novo. **E em 2026-09-18 descobriu-se que gravar de novo está BLOQUEADO por operação, não por código:** o recorder estava parado havia uma semana (`inactive (dead) since 2026-09-11 05:50:01 UTC`) e a causa é um `/usr/local/bin/disk-guard.sh` no cron do root, fora deste repositório, que a cada 10 min pára o recorder quando o disco passa de 85 % — o log do próprio guarda confirma: `Fri Sep 11 05:50:01 UTC 2026 disco em 85% — recorder parado`, o mesmo segundo do `Stopping` no journal, e uma linha a cada 10 min até `Fri Sep 18 15:30:01` — a semana inteira, porque o disco nunca desceu. Ele acerta ao PARAR em vez de apagar (nenhuma gravação se perdeu), e erra ao ser mudo: `systemctl stop` não é falha, logo `Restart=always` não reergue, e a única marca fica num log que ninguém lê. O disco está em 85 % com 15 GB de gravação já recolhida. **Antes da gravação nova:** libertar disco, conferir que a unit instalada é a do repositório (a de lá tinha `User=root`) e correr a hora de teste do §7.2. **FEITO EM 2026-09-18, e a hora de teste PASSOU nas duas fontes.** Disco de 85 % para 21 %, unit do repositório instalada, e a hora de teste do M2.7 mediu: (a) teto calculado do recorder `silencio_admitido_s_por_hora = 14,9` contra meta de 60 → `meta_atingida: true`; (b) a AUTORIDADE, o backtest sobre os carimbos da gravação, `silencio_do_rtds.total_s = 0.0`, `silencios = 0`, `suspeita_de_assinatura_caducada = 0`. **Zero segundos de silêncio**, contra os ~20.400 s/h que motivaram o marco. **E o conserto do #151 aparece agindo:** `reconexoes_por_recusa_do_servidor = 11` e `reconexoes_por_watchdog = 0` — onze vezes o servidor recusou a reassinatura (`500 … relation "__subscriptions" does not exist`), o feed LEU a recusa e derrubou o socket antes de o watchdog precisar agir. Junto veio: `linhas_corrompidas 0`, `arquivos_ilegiveis []`, cobertura do stream da âncora 1,0 nos oito ativos com `buracos_s 0.0`, e a âncora CONFIRMADA (τ=0 explica 100 % das 26 janelas elegíveis). **O que ISSO NÃO fecha:** o 0.8 exige 72 h, e uma hora não é 72 — a barra é o maior silêncio ao longo da janela inteira, não numa hora boa. **O que falta agora é só disco:** medido 699 MiB/h, a folga até o `disk-guard` dá 22,8 h, e 72 h pedem 33 a 49 GiB. Paulo escolheu descarga periódica a cada 12 h (`scripts/descarga_periodica.sh`, RUNBOOK §6); com ela cada ciclo acumula 8,2 GiB dentro dos 15,6 de folga e a gravação de 72 h cabe. Documentado no RUNBOOK §6. Histórico: a linha dizia *'contador reiniciou 01:31 UTC'* de 23/08 a 12/09; de 12/09 a 17/09 dizia que faltava só o número; de 17/09 a 18/09 esteve 🟡 com a barra por escrever |

### 0.7 FECHOU — a hora das 23:00

```
pior_fracao_coberta  0.9994      silencios  0      conexao_inteira  0
  bnb btc doge eth hype sol xrp zec: 99,9% cada, silencio_final 3,0s
```

E a âncora ganhou veredito pela primeira vez nesta campanha:

> **CONFIRMADA: tau=0 explica 100% das 24 janelas elegiveis.**

`distribuicao_das_elegiveis` em quartis 4/6/8/6, `concentrada: False` — amostra
espalhada pela hora inteira, não amontoada numa rajada.

**Ressalva sobre o mérito:** a hora das 23:00 foi gravada com o código
ANTERIOR ao M2.11. O feed se recuperou sozinho por volta das 20:00, antes de a
escalada entrar em serviço. Ela segue instalada e nunca foi acionada — é
seguro que ainda não foi reclamado.

### A medição que motivou tudo — o feed é INTERMITENTE, não morto

Cadência medida: **1,061 s por tick** por ativo (mediana 1,0 s, com intervalos
de 2 s e 8 s puxando a média), 8 ativos ⇒ **27.152 por hora cheia** é o
esperado. A tabela abaixo usava 8 ticks/s e subestimava a cobertura das horas
boas em ~5 pontos; os números estão corrigidos.

| Hora (UTC) | twap gravados | span | esperado | cobertura |
|---|---|---|---|---|
| 16:00 | 13.500 | 3.600 s | 27.152 | 49,7 % |
| 17:00 | 2.344 | 3.600 s | 27.152 | **8,6 %** |
| 18:00 | 6.037 | 3.600 s | 27.152 | 22,2 % |
| 19:00 | 2.320 | 3.600 s | 27.152 | **8,5 %** |
| 20:00 | 27.214 | 3.600 s | 27.152 | **100,2 %** |
| 23:00 | 27.304 | 3.600 s | 27.152 | **100,6 %** |
| 00:00 | 27.286 | 3.600 s | 27.152 | **100,5 %** |

Ele funciona em rajadas, e o episódio ruim foi das ~16:29 às ~19:xx. Desde as
20:00 as horas cheias vêm a **~100 %** — três seguidas.

**A defesa do M2.7 falhou, e agora está provado.** O recorder rodava com ela:
2.482 reassinaturas, uma a cada 5 s. As horas 17 e 19 ficaram em 8 % mesmo
assim. O `sem_dados_timeout_s` de 30 s nunca derrubou a conexão — não há
linha de reconexão no log —, ou seja, **o socket estava vivo recebendo outro
tráfego** enquanto o tópico não vinha. Reassinar nesse estado não produziu
efeito nenhum, 2.482 vezes.

Daí o item 0.6: reassinar cobre assinatura caducada; não cobre o servidor que
parou de publicar aquele tópico para aquela conexão. A resposta que sobra é
derrubar e reconectar, refazendo a assinatura do zero.

### As quedas do CLOB WS — dois códigos, duas causas (medido em 2026-09-14)

O SHADOW via a conexão do CLOB cair e voltar o tempo todo. O log com
`close_code` (M2.11) separou dois fenômenos, e `scripts/sonda_clob_ws.py`
(sondas `1008`, `custo` e `rajada`) mede cada um:

**`1008 invalid subscription payload`, sempre 10,2 s depois do PRIMEIRO
connect.** O CLOB lê qualquer texto anterior à primeira assinatura como
payload de assinatura, e o `PING` de aplicação não é um. O SHADOW conecta
com o conjunto vazio e só assina depois da descoberta; o PING chegava antes.
Sonda: conexão vazia + PING = 1008 aos 10,2 s; conexão vazia + `subscribe`
dinâmico antes do PING = viva; com o conserto (`_heartbeat` não manda PING
enquanto `token_ids` está vazio), a conexão vazia vive os 15 s e fecha com
1000. Teste `test_sem_assinatura_NAO_manda_ping`. ✅

**`1013 slow consumer: send buffer full` — e NÃO é o nosso consumidor.**
Três medidas fecham isso: (a) consumidor VAZIO (callback que só conta) com
os 152 tokens Up/Down cai igual — 3 quedas em 240 s, 6 em 420 s; (b) três
processos paralelos com custo artificial de 0, 150 e 400 µs por mensagem
caíram **no mesmo instante** (142,4/142,5 s; 162–164 s; 216–217 s); (c)
`max_queue=None` e um `sleep(0)` por mensagem não mudaram nada (os dois foram
testados e REVERTIDOS). O que derruba é a rajada do lado do servidor: dois
mercados de 5 min são **54 % dos bytes** (409 msg/s de `price_change` em UM
mercado), pior segundo medido 4,09 MB. Repartir os 152 tokens em 4 conexões
de 38 (round-robin) não resolve — a fatia com os dois mercados pesados
(94 % do tráfego) caiu 3 vezes enquanto as outras três não caíram nenhuma.
**Os tokens dos pools (50–120 msg/s) nunca caíram.** O que o código faz com
isso: a rota de pools ganhou **conexão própria** (`ProcessoShadow.
poly_pools`, rótulo `clob[pools]`), para a queda dos Up/Down — cerca de uma
por minuto nos períodos ruins, 1–2 s sem livro cada — não apagar o livro que
o maker cota. A conexão dos Up/Down continua caindo; para o taker isso já
era assim em toda rodada anterior, e a rota dele está medida e reprovada.
Rodada 4 (20 min): 24 quedas na `clob[updown]`, **1** na `clob[pools]`. 🟡
falta: medir em rodada longa (horas) quantas quedas a `clob[pools]` tem
sozinha.

---

## Bloco 1 — Veredito M2: existe edge líquido?

Critérios escritos **antes** dos números, em `VEREDITO_M2.md`. Não são
negociáveis depois do resultado.

Amostra: **24 horas de 2026-08-24**, `pior_fracao_coberta` 1,0 nos oito
ativos, **0 silêncios**, 896 janelas conhecidas, **688 trades** com o preditor
de variância medida. A amostra anterior — 20 h de 23/08, com 837 s de silêncio
e 42% dos snapshots descartados — segue em `VEREDITO_M2.md` para comparação.

### TAKER — 2 passa, 3 reprova (o critério exige as 5)

Sem restrição de banda: com o preditor corrigido não há banda de horizonte com
edge para restringir a (`bandas_com_edge: []`).

| # | Critério | Exigido | Medido (24 h, modelo MEDIDO) | |
|---|---|---|---|---|
| 1.1 | PnL líquido a 300 ms, threshold ≥ 0,02 | positivo | **−195,2525 USDC sobre 2.069 trades** (**M2_72H_20260912**, 72 h, 287.745.263 registros, 0 linhas corrompidas, 0 arquivos ilegiveis) · antes −67,2744. **E desta vez `bandas_com_edge` NAO veio vazia:** `240-120s` deu **+12,6942 USDC em 1.943 trades, acerto 0,6897**, e `<30s` deu +23,7373 em 321. **Três ressalvas, e elas decidem se isso vale algo.** (a) A margem é fina ao ponto de sumir: +12,83 sobre **6.562 USDC de capital movimentado** é **0,196%**, e as taxas comeram **129,80** de um bruto de ~143 — qualquer atrito a mais zera. (b) A banda foi escolhida DEPOIS de ver o resultado, o que é in-sample; só conta repetindo em dia independente. (c) O `<30s` NÃO é edge: acerto **0,5016** — cara ou coroa exato — com PnL positivo é lucro sem direção, e com n=321 são poucos trades grandes. Publicado, não adotado. | ❌ **e agora com pista nomeada** |
| 1.2 | Número de trades | ≥ 200 | **688** | ✅ |
| 1.3 | Calibração: `erro_de_confiabilidade` < 0,05 em ≥ 1 balde avaliável | sim | **os cinco baldes passam**: 0,0126 (`<30s`) · 0,0285 · 0,0319 · 0,0452 · 0,0493, todos com 20 faixas ocupadas | ✅ **resolvido pela §2d-ter** |
| 1.4 | Positivo também a 600 ms | sim | negativo em toda a grade de latência, e **melhorando** com ela (−67,94 a 150 ms → −55,78 a 1000 ms) | ❌ |
| 1.5 | Profundidade p50 a 3 ticks | ≥ 200 USDC | **139,0 (5m) · 63,0 (15m) · 30,9 (1h) · 24,0 (4h)** (M2_25AGO 5h, 0000–0500 UTC) · antes 128,0/50,0/28,7/27,0 (M2_24AGO 24h) — conclusão idêntica. **Remedido em 72 h (**M2_72H_20260912**, 72 h, 287.745.263 registros, 0 linhas corrompidas, 0 arquivos ilegiveis): 192,7 (5m) · 106,1 (15m) · 52,8 (1h) · 28,0 (4h) — NENHUMA duração alcança os 200 USDC**, com a amostra maior que este projeto já teve e cobrindo as 24 horas do dia, o que derruba a hipótese de 'era madrugada'. **Este é o critério que não tem conserto por estratégia:** é teto de CAPACIDADE. Edge perfeito não o move. | ❌ |

**⬇️ Daqui até o fim do Bloco 1 é histórico. A tabela acima é o estado
corrente.** Os números de 1.1 a 1.4 vinham do preditor com a variância
derivada, que subestimava o desvio-padrão em 6,3×. O que vem a seguir — o
"+2,7125 na banda", o decaimento monótono com a latência, o ECE de 0,207 —
descreve aquele preditor, e fica no documento porque é como se chegou aqui.
**O escopo é este bloco só:** os Blocos 2 em diante já trazem os números
correntes, e a tabela de pendências no fim da página também.

**1.1 inverteu duas vezes, e a última leitura é a que tem lastro.** 5 h deram
−41,57; 20 h deram +102,92; 24 h limpas dão **−53,28**. A gravação de 20 h
carregava 837 s de silêncio e descartou 42% dos snapshots — e buraco de livro
não erra para os dois lados, porque o preenchimento simulado usa o último
snapshot conhecido, que numa lacuna é sistematicamente melhor que o real.

**E o −53,28 é entrada múltipla, não borda negativa.** `max_1_entradas` dá
+2,7125, `max_3` dá −98,39 e `max_10` dá −221,64. E +2,71 em 640 trades é
0,4 centavo por trade com drawdown de −108 — o que a §2d-bis depois mostrou é
que esses 640 trades são **exatamente** a banda 240-120 s, e que o número
sobrevive à grade de latência inteira. É edge de direção real; o que ele não é
é taker viável, pelas razões da seção seguinte.

### A banda 240-120 s — o que a §2d-bis achou, e o que ela não salva

A `curva_de_horizonte` forçou o preditor bruto em cada faixa de tempo restante.
Deu edge em **exatamente uma**:

| banda | trades | PnL USDC | hit |
|---|---|---|---|
| > 240 s | 695 | −53,28 | — |
| **240-120 s** | **640** | **+2,7125** | **0,7063** |
| 120-60 s | — | −0,17 | — |
| 60-30 s | — | −49,16 | — |
| < 30 s | — | −5,41 | — |

Dois caminhos independentes batem ao centavo: a célula 240-120 s da curva e o
`faixa_de_tempo.comparacao.restrito` da rodada com `--tempo-restante-min 120
--tempo-restante-max 240`.

Sensibilidade a latência **dentro da banda** — decaimento monótono, positivo em
toda a grade:

| latência | trades | PnL USDC |
|---|---|---|
| 150 ms | 640 | +3,3119 |
| 300 ms | 640 | +2,7125 |
| 600 ms | 640 | **+1,3488** ← o 1.4 |
| 1000 ms | 640 | +0,4736 |

**A lacuna de medição que invalidava o 1.4, achada e fechada (PR #41).** O 1.4
lê `sensibilidade_latencia.600ms`, e esse bloco — junto com `curva_de_edge` e
`curva_de_capacidade` — rodava a própria configuração só com threshold e
latência, **ignorando `--tempo-restante-*`**. Numa rodada restrita o 1.1 saía da
banda e o 1.4 de `>240s`: dois critérios do mesmo relatório sobre populações
diferentes, sem aviso. O 1.4 publicava −54,3953 — os 695 trades de `>240s`, não
os 640 da banda —, e por isso **nunca era de fato remedido**, como a §2d-bis
mandava. Corrigido em `FaixaDeOperacao` (runner): os três diagnósticos herdam a
banda operada, e a rodada irrestrita fica idêntica à de antes (travado em
`test_m2_e2e`). Com a correção, **o 1.4 passa: +1,3488**.

**Ressalva de sobreajuste.** O `curva_de_edge` restrito é positivo de 0,01 a
0,05, melhor em 0,03 (+3,0489), e negativo a partir de 0,08. Esse 0,03 foi
escolhido OLHANDO esta amostra; adotá-lo exige repetir em dia independente,
senão é sobreajuste. O threshold registrado segue **0,02**.

**E um dia não é veredito.** Tudo acima é 2026-08-24. Uma banda que passa aqui
autoriza o próximo experimento — remedir 1.1-1.5 restrito a ela em **dias
independentes** —, não dinheiro real.

O texto abaixo é da leitura de 20 h e fica como registro do que se acreditava
então:

- faixa calibrada (240–120 s): **+91,58** com drawdown **menor** (−43,07
  contra −60,84)
- sensibilidade a latência plana: 103,39 (150 ms) → 100,41 (1000 ms)
- `threshold_mordeu: **true**`, 5 resultados distintos

**A degenerescência do threshold acabou.** Nas horas isoladas de 22/08 a grade
inteira dava um resultado só, e isso invalidava a leitura de 1.1. Agora ela
separa (97,71 a 102,97, melhor em 0,03). O que a grade mostra, porém, é que o
limiar quase não discrimina: 568 trades passam em todos os patamares. **A
borda não vem de escolher situações boas — vem de um viés sistemático.** O
modelo prevê 0,6445 no balde operado e realiza 0,6225; a lucratividade nasce
dessa diferença contra o preço, não de seleção.

**1.3 está marcado ⚠️ de propósito, e não ✅.** O `erro` publicado é
`|prob_média_prevista − freq_realizada|`, e `freq_realizada` é a **taxa-base do
balde**, não a acurácia da previsão. No balde `<30s`: previu 0,514, realizou
0,5073 — cara-ou-coroa dos dois lados. Um preditor que cospe 0,51 constante
tira nota máxima nesse critério. Enquanto a medição for essa, 1.3 não carrega
informação, e tratá-lo como aprovado seria contar como evidência o que é
artefato de construção.

**Conserto feito (M2.13):** o relatório agora publica `curva_de_confiabilidade`
por faixa de probabilidade prevista, mais `erro_de_confiabilidade` (ECE) e
`faixas_ocupadas`. Medido contra três preditores sintéticos de 20 mil
observações:

| Preditor | `erro` (antigo) | ECE | `faixas_ocupadas` |
|---|---|---|---|
| constante 0,51 num mundo 50/50 | +0,0051 | **0,0051** | **1** |
| bem calibrado | −0,0007 | 0,0070 | 18 |
| otimista em 15 pontos | +0,1487 | **0,1487** | 15 |

**O ECE sozinho não resolve** — o constante passa nele também, porque cai todo
numa faixa só. Quem o denuncia é `faixas_ocupadas`. Por isso 1.3 virou
CONJUNÇÃO: `calibracao_avaliavel` (≥ 3 faixas com amostra) **e** ECE abaixo do
limiar. Com `calibracao_avaliavel` false o critério fica **não avaliado**, que
não é o mesmo que reprovado.

**Rodou, e o veredito é reprovação, não "não avaliado".** `calibracao_avaliavel`
veio **true** — 20 faixas ocupadas no melhor balde, 20 na banda operada —, então
a conjunção foi de fato exercida, e o ECE ficou em 0,0694 no melhor balde e
**0,207 na banda 240-120 s**. O preditor não é constante disfarçado; ele é
**confiante e errado**, com ~75 mil das ~79 mil previsões nos extremos. O 1.3
reprova com informação, que é o que o M2.13 existia para garantir.

**1.5 é o único critério de borda que reprova, e é o mais duro.** É teto de
CAPACIDADE. O backtest move 1.651,59 USDC em 568 trades — **2,91 USDC por
trade**, e o lucro é de **+0,18 USDC por trade**. A duração mais líquida (5 m)
tem p50 de 87,77 USDC a 3 ticks, **44 % do mínimo de 200** que o critério
fixou antes de existir dado. Nenhuma duração passa.

### MAKER — 1.7 a 1.10 passam; **o 1.6 NÃO é avaliável** (revertido em 31/08)

| # | Critério | Exigido | Medido | |
|---|---|---|---|---|
| 1.6 | Conta fechada com fator 0,3 | positiva | **NÃO AVALIÁVEL pela conta exata** — `o_que_falta_para_fechar` continua com os 3 termos, e o `resumo_m2.py` reporta assim em toda rodada. A estimativa de +35,6 USDC/8h **não fecha o critério**: ver abaixo. **Saída aberta em 2026-09-06:** `conta_pessimista_do_maker` (`analysis/measurements.py`, 9 testes) troca *estimar a fila* por **limitar por baixo**. A assimetria que torna isso possível: rewards **não dependem de fila** (§15.3 pontua por spread e tamanho, amostrado 1×/min — ganha-se por ESTAR no livro), então só o custo é governado por ela, e ele entra no máximo (estatística ADVERSA do markout, não a média; rebate omitido porque omitir termo positivo preserva o limite). Se `fecha_no_pior_caso` for true, o 1.6 fecha **sem hipótese de fila nenhuma** — e o fator 0,3 deixa de ser necessário. **Falta um único dado:** `shares_executadas_por_recorte`, contando varreduras de nível na gravação (não precisa da fila, precisa do livro no tempo) — e isso exige a gravação de ≥72 h do 4.1. Sem ele a conta sai `avaliavel: false`, nunca um número. **E em 2026-09-09 a conta ficou DECIDÍVEL sem esse dado no caso que importa:** o limite é `líquido = rewards − custo` e o custo nunca é negativo (`|markout| × shares`, ambos ≥ 0), então **rewards = 0 implica líquido ≤ 0 qualquer que seja o número de shares varridas** — nenhum valor dele muda o SINAL. Com as 24 h de 08/09 medindo rewards zero em TODAS as janelas, o veredito deixa de depender da fila por completo. Isso NÃO afrouxa a falha fechada: ela existe para não inventar número ausente, e aqui nada é inventado — apenas se nota que a resposta não depende dele. `sem_recortes` separa *medi zero rewards em N janelas* de *não medi nada*, porque sem recorte algum o total também é 0,0 e aí o zero seria ausência de medida. 16 testes, os ramos verificados por mutação. **Corrigido em 2026-09-09, e o defeito foi achado por ENSAIO sobre gravação real:** a guarda original usava *não há recorte* como sinal de *não medi nada* — mas a simulação de rewards só gera recorte para janela COM pool, então zero janelas com pool também chega como recorte vazio. Os dois casos são opostos (*ausência de medida* × **medida de ausência**) e a guarda bloqueava o segundo, que é justamente o que tem veredito. O discriminador passou a ser `janelas_sem_pool_de_reward.total > 0`: prova que a medição OLHOU e não achou pool. Sobre a gravação de 09/09 o relatório passou de `avaliavel: false` sem conclusão para `fecha_no_pior_caso: false` com veredito **E em 2026-09-11 descobri que o veredito existia no relatório e NÃO CHEGAVA a quem lê.** O `#96` pôs `fecha_no_pior_caso` no JSON; o `resumo_m2.py` — que é o que alguém de fato lê — continuava consultando só `o_que_falta_para_fechar` e imprimindo NAO AVALIAVEL com o texto antigo. Nenhuma referência a `limite_pessimista` no arquivo inteiro. Um item que podia ser ❌ ficava ⬜ **em toda rodada**, e a diferença entre os dois manda instrumentar × manda parar de instrumentar. Achado rodando o resumo sobre relatório REAL (ensaio de 3 h da gravação do 4.1) e comparando com o JSON — leitura de código não teria achado, porque o campo ausente não dá erro. **E a minha primeira correção saiu SEM EFEITO:** adivinhei o caminho como `rota_maker.conta_fechada.limite_pessimista`, e o bloco é **IRMÃO** de `conta_fechada`, não filho. `ruff` passou, a saída ficou byte a byte idêntica à de antes, e só a execução mostrou. Por isso o teste que trava isso não compara strings: `test_o_campo_que_o_resumo_le_EXISTE_no_relatorio` **percorre** `CAMPO_DO_LIMITE` dentro de um relatório que o próprio backtest acabou de gerar, então rename de qualquer lado quebra o teste em vez de virar veredito ausente — verificado por mutação (com o caminho errado, falha). **Medido no ensaio de 3 h:** 1.6 = **REPROVA**, decidido por reward zero, **69 de 69 janelas sem pool**, todas por `sem_taxa_diaria`. **A ressalva vai impressa junto, e ela importa:** o limite omite o rebate de propósito, e nesta gravação o rebate (0,35 c/share, que é TETO — só existe se alguém nos executa, e em p=0,50) **supera** o markout (−0,3011 c/share, MEDIDO sobre 13.332 execuções), saldo **+0,0489 c/share**. Então este REPROVA quer dizer **não demonstrado positivo**, e não demonstrado negativo: o que sobra é um teto de receita contra um custo observado, ainda dependente da fila. 11 testes novos (1.296 na suíte). | ⚠️ |
| 1.7 | Markout 5 s | ≥ −0,5 ¢/share | **−0,1974** (246.504 execuções) — **remedido no M2.2** (`M2_20260824.json`, 2026-08-31, fórmula confirmada) | ✅ |
| 1.8 | Horas de amostra na célula | ≥ 20 h | **65,922 h** — **remedido no M2.2** (2026-08-31) — mas isso era sobre a gravação de 24/08, **quando ainda havia pool**. **Remedido em 2026-09-12 sobre 72 h: 2,325 h — ❌.** E a causa não é amostra curta: de **2.233 janelas, UMA tinha pool de reward**; as outras **2.232 saíram por `sem_taxa_diaria`**. As 2,3 h são dessa única janela. Isto confirma em escala o achado do #93 — os mercados updown deixaram de participar do programa de rewards — e é ACHADO SOBRE O PROGRAMA, não defeito nosso. O 1.8 volta a ✅ se e quando o pool voltar; não há código a consertar. | ❌ (era ✅ em 24/08) |
| 1.9 | Divergência com topo deslocado (emenda no VEREDITO_M2) | < 1 % | **0,20 %** (agregada: 2,82 %) | ✅ |
| 1.10 | Fórmula de reward confirmada na doc | sim | **CONFIRMADA** — `docs.polymarket.com/programs/liquidity-rewards` (2026-08-30): `S(v,s)=((v-s)/v)²×b`, quadrática, v=`rewardsMaxSpread` em centavos, amostrada a cada 1 min (10.080/epoch). **`analysis/rewards.py` corrigida no mesmo commit** — remove fórmula exponencial, fator_desconto e varredura. O M2.2 maker precisa re-rodar com a fórmula certa. Ver API_NOTES §15.3. ⚠️ **A fórmula tinha DUAS partes e o código implementava UMA (achado em 2026-09-14):** `S(v,s)` por ordem entrou em 30/08; a combinação dos lados — `Q_min = max(min(Q_ne,Q_no), max(Q_ne,Q_no)/3)` dentro de [0,10, 0,90], `min(Q_ne,Q_no)` fora — está no §15.3 desde o mesmo dia e o código **somava** os lados. `exige_dois_lados` existia como campo e nunca era ligado. **Corrigido:** `combinar_lados` em `analysis/rewards.py`, usado por `score_da_ordem` e por `live/cotacao.py` (mesmo caminho), e `denominador_pessimista` com prova de que o piso da fatia é o publicado. Dois testes que encodavam a soma (`dois == 2 × um`) reescritos; 7 novos. **O 1.12 não muda** — ver a seção abaixo | ✅ |

### A fórmula do §15.3 tinha uma metade que o código não implementava (2026-09-14)

O `API_NOTES` §15.3 está `[VERIFICADO]` desde 30/08 e diz, em duas linhas, como
os dois lados de um maker se combinam:

```
dentro de [0,10, 0,90]:  Q_min = max(min(Q_ne, Q_no), max(Q_ne, Q_no) / 3)
fora:                    Q_min = min(Q_ne, Q_no)
```

`score_da_ordem` e `live/cotacao.py` **somavam** `Q_ne + Q_no`. O campo
`exige_dois_lados` de `ParametrosDeReward` existia para isso e `grep` em
`src/` e `scripts/` só acha a declaração e um `if` que ninguém liga. É a
terceira vez que este projeto acha o mesmo defeito: fato conferido na fonte,
guardado na doc, ausente do código (`price_change` §6.1b, `market_resolved`
§12.13, e agora este). Dois testes encodavam a soma — `dois == 2 × um` — e
passavam porque o código somava também.

**O sinal do erro, provado e não estimado.** Para um maker com `a + b = S`
nos dois lados, `Q_min ≤ S/2` sempre, com igualdade só em `a = b` (prova de
três linhas na docstring de `denominador_pessimista`; teste parametrizado
cobre 35 divisões dentro e fora da faixa). Logo:

- **cotação simétrica:** numerador certo = `S/2`; o antigo era `S` (2×).
  Denominador certo ≤ `Σ/2`; o antigo era `Σ` (≥ 2×). Os dois erros se
  cancelavam e a fatia saía **exatamente o piso**. Por isso **o 1.12 não
  muda**: os +148,02 USDC/h foram medidos com as quatro cotações simétricas
  da varredura e continuam sendo limite inferior. Há um teste que reproduz a
  conta antiga à mão e confere que a nova dá a mesma fatia.
- **cotação de UM lado:** o antigo pagava inteira; a doc paga **um terço**
  dentro da faixa e **ZERO** fora dela. `ORDENS_PADRAO` do M2.2 tem uma de um
  lado, e `live/cotacao.py` aceita `dois_lados=False` — ali o motor ao vivo
  superestimava a própria receita por 3×, ou por infinito nos mercados de
  horizonte longo, que vivem fora de [0,10, 0,90] e são **exatamente onde o
  pool foi parar**.

**O que isto muda para a decisão:** nada no 1.12, que é a única conta
positiva; e tira da mesa qualquer variante de cotar um lado só nos mercados
longos — ela vale zero por regra do programa, não por falta de fila.

**Dois termos do 1.12 que ainda não são medida, e o que mede cada um
(registrado ANTES de rodar, 2026-09-14):**

1. **Capital — deixou de ser estimativa.** `capital_da_ordem` em
   `analysis/rewards.py` lê do livro: cotar os dois lados de um mercado
   binário é pôr DUAS compras, uma em cada token, e cada uma imobiliza
   `tamanho × preço` — `tamanho × (1 − spread_nosso)` no total, sempre ≤
   `tamanho`. Sem cunhar nada. A estimativa de "~1.000 por mercado" era boa;
   agora é conta, e o *conta_do_maker_nos_pools* da branch `soma-dos-lados-e-pools` pode publicar
   `capital_usdc` por mercado em vez de um `~`.
2. **Custo de saída — o buraco.** O markout do 1.12 é de **5 s**
   (o *markout_dos_pools* da mesma branch chama `medir_markout` com os
   horizontes padrão 1/5/30 s). Cinco segundos medem seleção adversa num livro que se move;
   não medem o que acontece quando UMA das duas compras executa num mercado
   que resolve por oráculo em dias e cujo livro tem 11 centavos de spread
   (o `LAC (-9.5)` da seção acima). Sair desse inventário custa **metade do
   spread**, à vista — 5,5 c/share naquele livro, contra os 0,06 c/share do
   markout de 5 s. É ~90× o custo modelado, e o 1.12 fecha por 3% de custo
   sobre receita: **não sobrevive a um custo de saída assim se as execuções
   de um lado só forem frequentes.** O que decide é `taxa de execução
   unilateral × (spread/2)` por mercado, e nada disso foi medido.
   **Medição (em código desde 2026-09-14):** o *markout_dos_pools* de novo
   (`--top 60 --duracao 4h`, horizontes padrão agora `1,5,30,300,1800`) —
   o markout a 30 min é a proxy do custo até conseguir sair — e, no mesmo
   relatório e sobre as mesmas execuções, `custo_de_saida_centavos_por_share`
   = spread/2 no fill, por mercado. **Critério:** o 1.12 passa
   de novo se `receita − (execuções/h × max(markout_1800s, spread/2))`
   continuar positivo nos mesmos 95 mercados. Se não, a rota maker nos pools
   longos cai como caiu nos updown, e por motivo com nome:
   `custo_de_saida_maior_que_reward`. Roda no Mac — este ambiente não
   alcança a Polymarket (`403 CONNECT` em `clob`, `gamma-api`, `data-api`).

### Duas rotas que não dependem do preditor — 1.11 e 1.12 (2026-09-13)

Registradas em `VEREDITO_M2.md` §2e e §2f **antes** de rodar. As duas escapam
inteiras dos critérios 1.1, 1.3 e 1.4, que reprovaram medindo qualidade de
previsão — nenhuma delas prevê nada. Nenhuma escapa do **1.5**: capacidade
continua sendo o que o livro comporta.

**As duas fecharam em 2026-09-13.** 1.11 **REPROVA** — a arbitragem que dá
nome ao projeto não existe de forma tomável. 1.12 **PASSA** — e é a primeira
conta deste projeto que fecha positiva com os dois lados medidos no mesmo
regime.

**O que 1.12 NÃO autoriza.** Ele diz que a conta fecha sobre dado medido; não
diz que o dinheiro está no bolso. Quatro coisas ficam de pé, e a primeira já
matou esta rota uma vez:

1. **O programa de rewards pode mudar.** Foi exatamente o que aconteceu com as
   janelas Up/Down entre 24/08 e 08/09 — o pool sumiu do lado da Polymarket,
   sem aviso. Nada aqui está sob nosso controle.
2. **O capital é estimativa** (~1.000 USDC por mercado para 1.000 shares nos
   dois lados), não medição.
3. **`shares_executadas` é teto pelo volume do mercado**, não simulação de
   fila — de propósito, mas é aproximação.
4. **A trava tripla do LIVE segue intacta.** Operar exige `MODE=LIVE` +
   `PULSEARB_CONFIRM_LIVE` + a frase exata, e isso é decisão humana.

**A rota está LIGADA no processo (2026-09-13), e é opt-in.** `Settings.
descobrir_pools_de_reward` (padrão `False`; env
`PULSEARB_DESCOBRIR_POOLS_DE_REWARD=true`) faz o `ProcessoShadow` rodar
`laco_de_descoberta_de_pools` a cada 300 s: `DescobertaDePools.descobrir()` →
`Rastreador.absorver()` → assina os tokens no WS → o `LacoMaker` cota pelo
mesmo caminho das janelas Up/Down. O taker continua recusando essas janelas
(`jogo="reward"` ∉ `jogos_operados` → `PULOU_JOGO_NAO_OPERADO`, coberto por
teste com executor hostil). 17 testes em `tests/test_pools_de_reward.py`; o
relato de 60 s ganha `pools_descobertos`.

**Rodou em SHADOW ao vivo em 2026-09-14 (duas rodadas curtas), e as duas
ensinaram algo.** Rodada 1: 57 janelas descobertas, e o maker recusou 100 %
com `sem_candidata_que_pontue`. Causa: o `LacoMaker` cotava com
`risk.stake_max_por_trade_usdc` (5,0 USDC do taker) lido como **5 shares** —
e 5 shares não pontuam em pool nenhum (`rewards_min_size` vai de 50 a
1.000). Conserto: `Settings.tamanho_da_cotacao_maker_shares` (padrão 5,0
para a rodada do taker não mudar; a rota de pools liga com 1.000, o tamanho
que o 1.12 mediu). No mesmo commit a descoberta ganhou o filtro pelo livro:
`DescobertaDePools(tamanho_da_cotacao=…)` consulta `GET /book` e descarta,
COM MOTIVO (`nao_pontua_com_este_tamanho`, `sem_livro`), a janela onde
`score_da_ordem` — a mesma função do 1.12 — dá zero para esse tamanho. Sem
o filtro o processo assinava 272 tokens e o WS derrubava com `1013 slow
consumer`; com ele, 2–3 janelas saem por rodada. 21 testes. Rodada 2 (1.000
shares): `livro/60 s = 136.473` em 266 tokens, e o maker chegou a **querer
cotar 145 vezes** (`ganho_justifica_perder_a_fila: 145`) — e foi barrado
145 vezes por `portao:disjuntor_armado`: o registro SHADOW
(`data/risco/registro_do_dia.shadow.json`) está com o disjuntor armado
desde 2026-09-08 pela perda sintética do taker (−26,59 USDC, teto 25), e o
disjuntor gruda por desenho. **Não foi desarmado por esta sessão**: desarmar
é ato humano. O ensaio da rota de pools roda com registro próprio
(`PULSEARB_RISK__CAMINHO_DO_REGISTRO`), e o quadro só marca cotação-sombra
repousando quando houver relato com `cotacoes_repousando > 0`.

**Rodada 4 (2026-09-14, 20 min, registro próprio, `TOP_DE_POOLS=60`,
1.000 shares, tetos do taker elevados por env só para o ensaio:
`PULSEARB_RISK__STAKE_MAX_POR_TRADE_USDC=1000`,
`STAKE_MAX_POR_JANELA_USDC=2000`, `EXPOSICAO_MAX_USDC=120000`,
`POSICOES_MAX_ABERTAS=120`, `SPREAD_MAXIMO=0.06`): houve cotação-sombra
repousando.** Relato final: `cotacoes_repousando: 59`, `estavel: 2850`,
`ganho_justifica_perder_a_fila: 196`, `portao:spread_anomalo: 142`,
`livro_indisponivel: 1148` (diário `data/shadow/diario-20260914-045201-*`).
Das 25 quedas de WS, 24 foram na `clob[updown]` e **1** na `clob[pools]`
(sem close frame, origem cliente) — o livro dos pools ficou de pé enquanto
os Up/Down caíam. **E o diário mostrou o defeito que estas rodadas
existem para achar:** as 69 cotações saíram todas entre 0,46 e 0,499 em
mercados cujo meio ia de 0,03 a 0,97 — o `LacoMaker._ordem_da_cotacao`
fechava sobre `meio=0.5` fixo, e não sobre o `livro.mid`. Num mercado a
0,90 isso é bid 40 ¢ abaixo do meio (não pontua); num a 0,10, bid 39 ¢
ACIMA do ask, que em LIVE executaria na hora como taker. Segundo defeito
no mesmo lugar: a estimativa contava dois lados (`dois_lados=True`) e o
`montar` colocava um (bid do Up) — três vezes o score dentro de
[0,10, 0,90] e score positivo fora, onde o §15.3 paga zero. Conserto no
mesmo commit deste parágrafo (#116): o preço sai do `livro.mid` da passada
(sem meio, `livro_sem_meio` e não cota) e `Cotacao.preco` arredonda para a
grade do tick (bid para baixo, ask para cima — o estimado e o colocado
olham o mesmo preço). **E no commit seguinte a cotação ganhou as DUAS
pernas que a conta descreve:** bid no Up a `meio − d` e bid no Down a
`(1 − meio) − d` — que no livro do Up é o ask a `meio + d`, o preço que
`estimar_retorno` já pontuava do lado ask. `CotacaoAberta` guarda os dois
ids (`order_id`, `order_id_down`); as pernas passam as duas pelo portão,
entram juntas e saem juntas (`execucao_maker`): Down RECUSADO cancela o Up
(o Up sozinho não é a cotação avaliada), qualquer INCERTA — envio ou
cancelamento, de qualquer perna — para e devolve RECONCILIAR, e uma
`Cotacao` de dois lados sem como montar o Down é recusada com nome
(`dois_lados_sem_ordem_do_lado_down`) em vez de virar o defeito da r4 de
novo. Fora de [0,10, 0,90] só a cotação de dois lados pontua, e lá o
portão de preço (`preco_minimo` 0,05 / `preco_maximo` 0,95) barra a perna
que cair fora da faixa, com nome. 19 testes em
`tests/test_4_0c_laco_maker.py` (`TestOPrecoSegueOMeioDoLivro`; 39 no arquivo desde a regra de recolher), 22 em
`tests/test_m4_execucao_maker.py` (`TestDuasPernas`) e
`tests/test_m4_cotacao_maker.py` (`TestOPrecoCaiNaGradeDoTick`). ✅ (b)
**conferido em duas rodadas no mesmo dia.** **Rodada 5** (20 min, código
de #116, uma perna, mesmo env da r4; diário
`data/shadow/diario-20260914-052154-*`): os preços passaram a ir de 0,10 a
0,88 acompanhando o meio de CADA mercado. `scripts/confere_diario_maker.py`
(busca `GET /book` do token cotado e mede `preco_limite` contra o meio de
agora, em ticks) sobre as 53 colocações com livro: **33 a 1,5 ticks abaixo
do meio**, as demais entre 1,0 e 7,5, as de tick 0,001 a 0,1–0,2, e UMA a
−8 (mercado de temperatura em Xangai cujo meio andou de 0,27 para 0,18
entre a colocação e a conferência — as colocações seguintes já saíram a
0,14). Relato final: `cotacoes_repousando: 38`; motivos `estavel 2296,
ganho_justifica_perder_a_fila 164, livro_indisponivel 1213,
portao:spread_anomalo 146, sem_candidata_que_pontue 774, sem_pool_de_reward
604`; quedas 23 `clob[updown]` / 1 `clob[pools]` / 3 `rtds`. O
`sem_candidata_que_pontue 774` é a perna única fora de [0,10, 0,90], onde o
§15.3 paga zero. **Rodada 6** (20 min, código de #117, duas pernas, mesmo
env; diário `data/shadow/diario-20260914-054431-*`): 116 `cotacao_colocada`
= **58 Up + 58 Down**, 55 janelas e **todas com as duas pernas**; nos 59
pares Up/Down, `preco_up + preco_down` = 0,97 (39), 0,98 (7), 0,997 (6),
0,998 (7) — o Down espelha o Up a `d` de cada lado do meio, como a conta
descreve. `sem_candidata_que_pontue` foi de 774 a **0**; apareceu
`portao:preco_fora_da_faixa: 198`, a perna que cai fora de
[0,05, 0,95] barrada com nome, como previsto. Repousando subiu a 53–55 e
aos ~17 min caiu a 4 → 1 → 0. **Achado da r6, que não é da rota maker:**
`portao:pausa_por_sequencia: 506`. O registro de risco é sufixado pelo modo
(`registro_do_dia.shadow.json`, não `registro_do_dia.json`), e ele
carregava as perdas SINTÉTICAS do taker em SHADOW das rodadas anteriores
(pnl −8,47, 4 perdas seguidas): o portão compartilhado pausou por 1 h
(`pausado_ate_epoch 1789369314`) e o maker, que por desenho entra pelo
MESMO portão, tirou todas as cotações (`_sair`). Uma rota medida morta
(taker Up/Down) pausando a rota que sobrou é o comportamento do portão,
não defeito dele — a pausa é o sinal de que o *modelo do taker* parou de
acertar, e o maker não tem modelo. **Decisão, sem afrouxar código:** o
ensaio do 4.2 sobe com o registro `.shadow.json` apagado (r7 em diante), e
o quadro diz isso aqui; se a pausa voltar a disparar dentro de uma rodada,
o operador sobe `PULSEARB_RISK__PERDAS_SEGUIDAS_PARA_PAUSA` por env, de
propósito e por escrito — o bot não sobe sozinho. Se a pausa deve ou não
valer para a rota maker é decisão de política que fica escrita como
pergunta, não respondida por conveniência.
Motivos finais da r6: `estavel 2581, ganho_abaixo_do_piso 5,
ganho_justifica_perder_a_fila 668, livro_indisponivel 1121, livro_sem_meio
16, portao:pausa_por_sequencia 506, portao:preco_fora_da_faixa 198,
portao:spread_anomalo 32, sem_pool_de_reward 592`; quedas 14
`clob[updown]` / 1 `clob[pools]` / 3 `rtds`. **Rodada 7** (20 min, #118,
a caixa do 4.2 ligada, registro `.shadow.json` apagado antes; diário
`data/shadow/diario-20260914-061217-*`): **os primeiros números de
dinheiro da rota** — ver a linha 4.2. E a rodada achou o defeito de
medida que o relógio existe para achar: `livro_indisponivel: 2151` em
~5.600 avaliações (38%), com o resumo dos livros mostrando **55–60% dos
tokens de pool "mudos" a qualquer instante** (`mudos 112–174 de 152–288`).
Não eram livros velhos: eram mercados de horizonte longo parados, e o WS
só manda delta quando algo muda — os 10 s de `SILENCIO_DO_TOKEN_S`, certos
para o Up/Down de 5 min, chamavam de mudo um livro que estava certo, e o
relógio parava nele (rewards subcontados, cotação sem reposicionar).
Conserto no mesmo commit deste parágrafo: `LivrosAoVivo.livro` aceita
`silencio_s` por consulta, e o maker lê por `_livro_para_o_maker`, que
vigia a **CONEXÃO** dos pools (conectada e com mensagem — PONG conta — há
menos que `pong_stale_seconds`, 30 s) em vez do token, com teto de 900 s
por token só para uma assinatura que morresse em silêncio não virar livro
eterno; sem a rota de pools ligada vale a regra de sempre. 4 testes em
`tests/test_m4_shadow_processo.py` (`TestOLivroDoMakerEhVigiadoPelaConexao`)
e 2 em `tests/test_m4_livros.py`. A r7 também pegou uma **queda de rede
local de ~5 min** (1789367066–1789367372: rtds ×2, `clob[pools]` e
`clob[updown]` com `timed out during opening handshake` ao mesmo tempo):
`portao:feed_parado 195` e `portao:relogio_nao_monitorado 133`, o maker
tirou 42 das 60 cotações e manteve 18 (as de livro indisponível — sair por
falta de dado nosso perde a fila, por desenho), o relógio ficou parado em
24,44 USDC os 5 min inteiros (nada acumulou sem passada), e ao voltar
repôs 50 → 56. 🟡 falta: (c) os tetos do
taker (`stake_max_por_trade_usdc` 5, `spread_maximo` 0,04) barram qualquer
cotação de 1.000 shares — o operador que quiser a rota maker tem de subir
os tetos de propósito (`PULSEARB_RISK__*`), com o capital que os pools
exigem (`rewards_min_size` 50–1.000 shares × preço); o bot não os sobe
sozinho.

| # | Critério | Exigido | Medido | |
|---|---|---|---|---|
| 1.11 | Arbitragem de soma-dos-lados tomável | ≥ 1 episódio que sobrevive a 300 ms de latência | ❌ **MEDIDO E REPROVADO sobre 116 h (2026-09-13).** 385.339.627 registros, 3.878 janelas, 7.756 tokens pareados, **3,37 milhões de instantes avaliados** por direção. Bruto: 2.267 + 2.344 instantes com soma fora de 1,00. Líquido de taxa: 531 + 573. **Episódios que sobrevivem a 300 ms: ZERO, nas duas direções.** `duracao_dos_episodios_s` = `p50 = p90 = max = 0,0 s` — cada um dos 1.104 aparece em UM update de livro e some no seguinte. E não é só dessincronia: **201 deles (82 + 119) tinham o livro do par fresco a ≤ 1 ms**, genuinamente simultâneos, e ainda assim duraram zero. A capacidade nos melhores é de 14 a 28 USDC. O nome do projeto (*bot de arbitragem de latência*) está medido e morto: a soma sai de 1,00 em 0,016% dos instantes, e nunca por tempo suficiente para uma ordem chegar. `scripts/soma_dos_lados.py`, passada 2 em memória constante. | ❌ |
| 1.12 | Existe recorte onde a rota maker se paga | persistência ≥ 50% das amostras **E** ≥ 10 USDC/h **E** markout DESTE regime | ✅ **PASSA — os três itens, medidos em 2026-09-13.** **(a) Persistência:** 12 amostras ao longo de 2 h sobre os 300 maiores pools. **185 de 300 pontuam em TODAS as 12**, e o conjunto que pontua em ≥50% é EXATAMENTE o que pontua em 100% — ou qualifica sempre, ou nunca. Os 185 têm **concessão ZERO** (`p50 = p90 = max = 0,00 c`): basta entrar na fila, sem apertar o spread. **(b) Receita, pelo MÍNIMO das 12 amostras: 174,08 USDC/h.** **(c) Markout DESTE regime: −0,0606 c/share em 5 s, sobre 4.272 execuções** (`scripts/markout_dos_pools.py`, 4 h, 60 mercados, 104.780 USDC/dia de pool) — **4,7× menor** que os −0,2838 das janelas de 5 min de cripto, e é por isso que transportar aquele número teria decidido errado. **A CONTA FECHA:** ótimo em **95 mercados, +148,02 USDC/h = 3.552 USDC/dia**, capital estimado ~95.000 USDC → **3,74%/dia**; em top_10, +44,08 USDC/h sobre ~10.000 = **10,6%/dia**. O custo no ótimo é 3% da receita. **É LIMITE INFERIOR:** assume que TODO o fluxo taker nos atropela, o que superestima o custo — e como o reward não depende de fila (§15.3), o erro entra só de um lado. `scripts/conta_do_maker_nos_pools.py`. **REBAIXADO PARA 🟡 em 2026-09-14 — passa nos três itens registrados; o critério não media um custo.** (1) Os #111 e #113 cruzaram: a varredura dividia o `Q_min` de `score_da_ordem` pela soma crua dos dois lados do livro — fatia 2× a 4× menor que qualquer denominador real. Corrigido para `denominador_pessimista` no mesmo commit desta linha; para cotação simétrica a fatia publicada NÃO muda (teste em `test_m22_integridade_maker.py`). (2) **O que falta, e está escrito:** o markout do item (c) é de **5 s**. Uma execução de UM lado em mercado que resolve em dias, com 11 c de spread, custa ~spread/2 para sair — ~90× o custo modelado, numa conta que fecha por 3% de custo. Volta a ✅ se `receita − execuções/h × max(markout_1800s, spread/2)` seguir positivo nos mesmos 95 mercados. **A medição existe em código desde 2026-09-14:** `medir_markout` publica `custo_de_saida_centavos_por_share` (spread/2 no instante do fill) ao lado dos horizontes, sobre as MESMAS execuções, com recorte por mercado opt-in; o *markout_dos_pools* ganhou `--horizontes 1,5,30,300,1800` e liga o recorte; o *conta_do_maker_nos_pools* publica `liquido_com_custo_de_saida_usdc_por_hora` por mercado (¢/share × shares/h do fluxo taker, a mesma hipótese pessimista da conta de 5 s) e por recorte — **só com cobertura completa**, senão `None` e a contagem dos que faltam —, e sai `CUSTO DE SAÍDA AUSENTE` (nunca zero) se o markout vier sem o recorte ou sem 30 min; o horizonte de 30 min só conta quando a coleta cobre o instante-alvo (revisão do Codex no PR #114 pegou os três). 6 testes. E o `scripts/resumo_das_rotas.py` passou a decidir o 1.12 pelos **QUATRO** itens: 1.12d = líquido com custo de saída > 0 no recorte que era o ótimo da conta pessimista ("os mesmos mercados"); sem `--conta`, ou com `custo_de_saida.ausente: true`, o veredito é NÃO AVALIÁVEL — nunca PASSA por omissão (5 testes; de brinde, `_conta_fechada` dividia por zero com lista vazia). **Falta rodar** — no Mac: `markout_dos_pools.py --top 60 --duracao 4h --json relatorios/MARKOUT_POOLS_SAIDA.json` e depois a conta com esse `--markout`. | 🟡 **ENSAIO na VPS (2026-09-17, 10 min, 60 mercados): o encanamento está de pé e a trava funcionou — e a IMPRESSÃO não.** O ensaio saiu com `CUSTO DE SAÍDA AUSENTE`, pelo motivo certo e aritmético: `1800s` veio `n=0`, porque **não se mede markout de 30 min dentro de uma janela de 10 min**. `_custo_de_saida` devolveu `None` e a conta não fechou, como projetado. Mas o **custo de saída foi medido pela primeira vez** no agregado: `custo_de_saida_centavos_por_share` **média 1,9985 ¢/share, p50 1,0, n=33** — contra os −0,0606 ¢ de 5 s que o critério usava, ou seja **≈33× maior**, na direção e na ordem de grandeza que o rebaixamento de 2026-09-14 temia (ele estimava ~90×). Guardanapo sobre a mesma amostra, usando a coluna `USDC/1k sh` (receita por 1.000 shares do MESMO fluxo taker): a saída custa 19,99 USDC por 1.000 shares na média (10,00 na mediana), e quase toda a lista passa disso — temperaturas de cidades entre 20 e 99 USDC/1k sh, Holtec a 439 — enquanto `Saudi Oil Pipeline` (1,96) perde 10×. **Não é veredito** (n=33, horizonte de 30 min vazio), é razão para gastar as 4 h. **O DEFEITO estava na saída, não na conta:** o aviso ia para o **stderr** e a tabela para o stdout, terminando em `LÍQUIDO +293,46 USDC/h` com `custo 0,000` em toda a coluna. Num `> arquivo.txt` o aviso some e sobra gravada uma conta que parece fechada e não é — medida de ausência virando ausência de medida **pelo canal de saída**. Consertado: o aviso vai para o stdout e diz explicitamente que as somas NÃO são o veredito; a tabela ganhou a coluna `liq/h saída` que imprime `—` (nunca `0.000`) quando não há medida; e o cabeçalho `somas (pior caso de custo)` — ambíguo, porque o pior caso é o do FLUXO — virou `somas (pior caso de FLUXO; markout 5 s)` com a marca `(custo de saída: —)` em cada linha sem medida. 8 testes novos, 4 mutações verificadas. **A rodada de 4 h ESTÁ EM CURSO na VPS** (2026-09-17, `relatorios/mk-20260917.json`), com ~14 execuções/min medidas no início — projeta ~3.000 contra as 33 do ensaio. **RODADA DE 4 h na VPS (2026-09-17, 60 mercados, `relatorios/mk-20260917.json`): 1.032 execuções com markout utilizável** (33 no ensaio), markout de 5 s **−0,0851 ¢/share**; a conta de 5 s fecha folgada (top_50 **+161,74 USDC/h**, top_214 +92,65 — a cauda custa 192 dos 285 de receita). **O custo de saída foi medido em 45 dos 214 mercados** (top_5: 4 de 5; top_10: 6 de 10; top_50: 8 de 50) e o veredito saiu `None` em TODO recorte — a regra do #114 exige cobertura completa. Não é defeito de instrumento: o custo de saída precisa de uma execução com 30 min de acompanhamento, e pool de reward paga para cotar e NÃO ser executado — 169 mercados não tiveram fill em 4 h. **Veredito: NÃO AVALIÁVEL pela regra de cobertura completa, e a regra exige uma cobertura que esta rota não produz.** A conta passou a publicar, AO LADO do `None` e nunca no lugar dele, o mesmo líquido **nos mercados medidos** (`liquido_com_custo_de_saida_nos_medidos_usdc_por_hora`, com contagem e receita) — subconjunto definido por disponibilidade de dado, não por resultado, que é o que um LIVE faria. **Decisão pendente, do quadro:** manter 1.12d como 'cobertura completa ou nada' (e aí a rota fica 🟡 por construção) ou reescrevê-lo como 'líquido > 0 nos mercados em que o custo de saída foi medido, e só se opera nesses'. ⬜ falta: rodar a conta de novo sobre os JSONs já colhidos (instantâneo) para ter o número nos 45 — e a decisão. **A CONTA NOS MEDIDOS (2026-09-17, mesma rodada de 4 h, `#145`): o custo de saída come 66 % da receita no topo e mais de 100 % logo abaixo.** top_5 (4 medidos): receita 30,57 → líquido **+10,44 USDC/h**; top_10 (6 medidos): 38,96 → **+12,66**; top_25 e top_50 (os mesmos 8 medidos): 44,96 → **−0,92**; top_214 (45 medidos): 59,24 → **−729,94**. Por mercado, o que sobra da receita depois da saída: Holtec **94 %** (306 USDC/1k sh — quase não há fluxo taker), Atlanta 35 %, MrBeast 27 %, Los Angeles 19 %, Madrid 19 %, Nova York **3 %**. O custo de 5 s do MrBeast era 0,26 USDC/h; o de saída, 7,77 — **30×**, exatamente a ordem que o rebaixamento de 2026-09-14 temia. **Leitura:** a rota sobrevive num bolso de ~6 mercados a +10–13 USDC/h (≈ 250–300 USDC/dia), não nos 95 a +148 USDC/h da conta de 5 s; e **selecionar pool por TAMANHO — o default `top_de_pools_de_reward=120` — escolhe justamente os perdedores**: o que separa Holtec de Nova York é `receita_por_mil_shares` (reward por unidade de fluxo que nos atropela), não o pool. **Não fecha ✅ e não vira ❌.** Continua 🟡, e o que falta está escrito: (i) coleta de **24 h** — o horizonte de 30 min tem poucas amostras por mercado em 4 h, e o bolso dos 6 foi escolhido DEPOIS de ver o resultado (in-sample, a mesma ressalva do 1.1); (ii) a verificação do `side` (auditoria §2.1) — **medida em 2026-09-17 na M2_72H, alinhando por ORDEM DE CHEGADA: 1.801.752 prints, 1.708.080 classificáveis, 0,8274 concordam com `side` = taker → INDETERMINADO** pelo limiar pré-registado de 0,90 (MAKER seria ≤ 0,10: o campo NÃO está invertido, mas 17 % discordam). Os discordantes têm padrão de ATRASO, não de semântica: prints BUY dois e três ticks abaixo do bid em mercado a subir, e a fixture real mostra o `price_change` a chegar ~800 ms depois do seu carimbo contra ~45 ms do print — comparava-se o print com um livro de outro instante. **v2 corrida em 2026-09-18: `side` é o lado do TAKER — FECHADO.** Alinhamento estrito pelo carimbo 0,8368; **janela de toque de 1 s 0,9932** (1.770.661 concordam, 12.180 discordam, 18.800 sem toque). A diferença tem mecanismo, verificado nos exemplos com carimbo: o servidor emite o `price_change` do livro PÓS-negócio 1 ms antes do `last_trade_price` do negócio que o causou, e aí o preço do fill aparece do lado oposto — viés da medida estrita, não do campo (API_NOTES §6.1c). A convenção de `livros.py:60` está certa e o sinal de todo o markout do quadro, incluindo o custo de saída, fica confirmado. Nota lateral do mesmo relatório: 2,3 % dos `price_change` e 2,4 % dos prints chegam ao recorder mais de 5 s depois do carimbo — atraso de chegada na máquina de gravação, o mesmo fenómeno do 4.1; **(iii) o critério 1.12d — DECIDIDO por Paulo em 2026-09-18**, entre três opções postas antes de ele ver o resultado de cada uma: o número que fecha o item é `liquido_do_1_12d_usdc_por_hora`, a soma sobre os mercados com custo de saída MEDIDO, válida só se eles cobrirem **≥ 90 % das SHARES** do recorte. Por que não a cobertura completa (regra do Codex, #114): nas rodadas de 4 h e 24 h ela devolveu `None` em TODO recorte — pool de reward paga para cotar e NÃO ser executado, e mercado sem fill não gera markout de 30 min; um critério que nunca produz número não recusa nada, só cala. Por que não 'só os medidos': aí o recorte passaria com o subconjunto que por acaso teve dado, que é exactamente o achado do #114. Por que SHARES e não contagem de mercados: o que atropela a cotação é fluxo, e dez mercados minúsculos sem medida não pesam como um grande. Os outros dois líquidos continuam publicados ao lado, para a escolha do critério seguir auditável. **(iv) o selector de pools — DECIDIDO na mesma data: trocado para `receita_por_mil_shares`.** Os recortes deixam de ser ordenados por líquido no pior caso sobre um universo escolhido por `daily_rate`. A rodada de 4 h de 2026-09-17 mostrou o que a ordem antiga escolhe: nos mercados do topo por tamanho de pool o custo de saída comia 66 % da receita, e abaixo deles passava dos 100 % — pool grande atrai fluxo grande, e é o fluxo que atropela a cotação. Mercado sem fluxo medido vai para o FIM da ordem, não para o início: ausência de trade na amostra é ausência de dado, não ausência de fluxo. Decidido ANTES das 4 rodadas SHADOW e das 2 semanas do 4.2, que é o que impedia de as começar. 3 mutações verificadas (limiar a zero, cobertura por contagem, selector de volta ao líquido). |
| 1.13 | **Arbitragem por IDENTIDADE** (cesta neg-risk e escada de limiares) | ⬜ **rota NOVA, aberta em 2026-09-16, e nunca medida** — `neg_risk` existia só como flag de qual contrato assinar (§2/§6.1a), e escada/monotonicidade não existia em lugar nenhum do repositório. **Por que esta e não outra:** os seis ❌ do quadro reprovaram por LATÊNCIA (o 1.11 mediu ZERO episódios sobrevivendo a 300 ms; medimos p50 218 ms, §16) ou por PREVISÃO (1.1/1.4/1.5). As duas identidades não dependem de nenhum dos dois: numa cesta de resultados mutuamente exclusivos e **exaustivos** 1 share de cada YES paga exatamente 1,00 sempre, e numa escada `P(X ≥ k₁) ≥ P(X ≥ k₂)` porque quem passa de k₂ passou de k₁. E elas vivem em mercados de horizonte longo, que o `OUTROS_BOTS.md` §6 item 3 já tinha medido baterem a janela curta por duas ordens de grandeza (**+143,96 ¢/h contra +0,63 ¢ por 5 min**) — enquanto a descoberta inteira do projeto continua apontada para `{ativo}-updown-{dur}-{epoch}`, que é a família reprovada. `analysis/arbitragem.py` (40 testes, 13 mutações verificadas) faz a conta com as MESMAS funções do backtest (`simulate_taker_buy` atravessa o livro nível a nível, `engine/fees.py` cobra a taxa por share) e devolve a **maior** cesta que ainda dá lucro, não a primeira — atravessar o livro encarece a cesta, então dizer só *existe* diria isso sobre algo que talvez renda centavos. **A trava que importa não é o lucro, é `conjunto_nao_exaustivo`:** conjunto a que falte um resultado não paga 1,00, a conta fecha bonita do mesmo jeito e a posição vira DIRECIONAL — erro silencioso, e o chamador tem de provar a exaustividade em vez de presumi-la. Mesma coisa na escada: os limiares entram para serem conferidos, porque livro não carrega o limiar dele e trocar os dois inverte a desigualdade sem nada acusar (`escada_fora_de_ordem` — achado pelo próprio guarda de *motivo declarado e nunca devolvido*, que disparou na primeira execução). `scripts/varredura_de_arbitragem.py` dá o veredito, e **o custo dele é o ponto: uma varredura, não 14 dias** — se não houver oportunidade, o ❌ sai no mesmo dia e a rota morre barato. Ele **não adivinha campo de API**: a estrutura do evento neg-risk nunca foi verificada na fonte, então ele procura, RECUSA o que não reconhece e imprime o que viu — a primeira rodada é a verificação para o `API_NOTES`, que é como fato de API entra aqui (§6.1b e §12.13 custaram isso). E mede **persistência**, não existência: o 1.11 não morreu por não achar episódio, morreu porque nenhum sobrevivia à latência. **Consertado ao RODAR, não ao ler:** a primeira execução (rede bloqueada nesta sessão) saiu com **código 0** e um traceback — varredura que não produziu veredito sendo tratada como *olhei e não achei*; virou `rede_indisponivel` com saída 1, que é a mesma leitura que o `live/shadow.py` já faz da rodada sem saída. ⬜ **falta rodar** — a rede para `polymarket.com` é negada por política no contêiner da nuvem; é uma varredura de minutos no Mac ou na VPS. E falta a ESCADA: ela precisa do agrupamento por ativo e data, e a forma do slug dessas escadas não está verificada. **RODOU NA VPS em 2026-09-16, e o resultado foi um defeito MEU antes de ser um resultado de mercado.** 599 passadas em 10 min sobre 22 eventos neg-risk; o script imprimiu **❌ para a cesta** — e as recusas diziam `conjunto_incompleto` **11.980**, que dividido por 599 passadas são **20 eventos, de 22**. Só DOIS chegaram à aritmética (os 1.197 `sem_folga` = 2 × 599), e os outros 20 foram recusados antes da conta rodar. **O veredito reprovou o que não foi olhado**, três linhas depois de o próprio script imprimir que *recusa não é 'não há arbitragem'* — a distinção do `sem_recortes` no 1.6 (medida de ausência × ausência de medida) violada dentro do arquivo que a cita. Consertado: o veredito conta quantos EVENTOS chegaram à conta e sai **NÃO AVALIÁVEL** quando a maioria não chegou. E a contagem passou a ser por evento — 11.980 escondia que eram 20, e número grande fazia a recusa parecer medida. **`conjunto_incompleto` virou três motivos**, porque *incompleto* não dizia o que destrava: `conjunto_com_fechado` (há resultado FECHADO — a cesta sobre os abertos ainda paga 1,00 **se** todos os fechados resolveram NÃO, e isso é fato de API por verificar), `conjunto_sem_livro` e `conjunto_pequeno_demais`. **E o `--cru` que a mensagem de erro mandava rodar não existia no argparse** — flag fantasma, achada pelo mesmo guarda de *motivo declarado e nunca devolvido*; agora existe e imprime a forma dos eventos recusados, que é como o `neg_risk` vira `[VERIFICADO]` no API_NOTES. 26 testes, 7 mutações verificadas. ⬜ **o veredito da cesta continua EM ABERTO** — o que a primeira rodada mediu foi o leitor, não o mercado. Falta rodar de novo com o diagnóstico separado, e o `--cru` diz se `conjunto_com_fechado` é recusa legítima ou estreiteza minha. **E a recusa dos 20 eventos era ESTREITEZA MINHA, não fato de mercado.** Eu jogava o evento inteiro fora quando havia um resultado FECHADO — mas candidato eliminado é resultado fechado, e isso é o caso comum num evento multi-resultado de horizonte longo. **A identidade sobrevive:** se todos os fechados resolveram NÃO, exatamente um dos ABERTOS vence e a cesta sobre eles paga 1,00 do mesmo jeito. A cesta passa a comprar só os abertos — um resultado que já resolveu NÃO custa zero, e comprá-lo seria pagar por bilhete que já perdeu. As duas saídas ruins ficaram separadas porque pedem coisas diferentes: `evento_ja_decidido` (um fechado resolveu SIM — acabou, os abertos valem zero) e `fechado_sem_resolucao_legivel` (estado DESCONHECIDO, que recusa como em todo o resto do projeto). A resolução é lida do par `outcomes`/`outcomePrices` **casado pelo NOME**, nunca pela posição: presumir que o índice 0 é o *Yes* é exatamente o campo assumido a partir do que parecia razoável que produziu os defeitos do §6.1b e do §12.13 — e há mutação que troca a ordem para provar. **Segunda rodada na VPS (2026-09-16, 599 passadas) com o diagnóstico separado, e ela apontou UM alvo:** 2 avaliados de 22, **18 fora por `fechado_sem_resolucao_legivel`** e 2 por `evento_ja_decidido`. Os 2 que resolveram limpo são a pista — a listagem `/events` traz `outcomes`/`outcomePrices` do mercado aninhado **às vezes**, e quando não traz o dado está em `/markets/{id}` (§2). `enriquecer_fechados` busca por ele antes de julgar, **cacheado para sempre porque resultado fechado não reabre** — sem o cache seriam 18 buscas × 599 passadas. Busca que falha **mantém a recusa**: não achar a resolução continua sendo estado desconhecido, nunca *provavelmente resolveu não* (mutação verificada). Aberto nunca é buscado. 36 testes, 13 mutações. ⬜ **falta a terceira rodada** — e é ela que finalmente mede o mercado: se os 18 entrarem, a cesta neg-risk ganha veredito de verdade; se continuarem fora, o `--cru` (que agora também enriquece antes de imprimir) mostra a forma que sobrou. **TERCEIRA rodada na VPS (2026-09-16, `c38c01b`): o conserto NAO destravou** — os mesmos **18 fora por `fechado_sem_resolucao_legivel`**, com o codigo que busca `/markets/{id}` ja rodando. Quatro explicacoes possiveis, e a saida da varredura nao distingue nenhuma: a busca falhou, o mercado cheio tambem nao traz o par, nao ha identificador, ou o nome do resultado NAO e 'Yes' (num evento multi-resultado ele pode ser o nome do candidato, e ai o casamento por nome do `resolveu_nao` nao acha). Escolher uma delas e pedir outra rodada de 10 min seria adivinhar — o `--cru` passou a percorrer as quatro em ordem e dizer em qual parou, imprimindo a URL tentada, o desfecho da busca, as chaves do que voltou e o que o `resolveu_nao` respondeu em cada etapa. E passou a separar `closed` de `active: false`, que o `_falta_resolucao` trata igual e NAO sao a mesma coisa: mercado inativo pode nunca ter aberto, e ai nao existe preco final para ler nem vai existir. O `--cru` e instantaneo — nao espera os 10 min. 40 testes. **QUARTA rodada na VPS (`--cru`, 2026-09-17): as quatro hipóteses foram respondidas, e a resposta começou por um defeito MEU.** O diagnóstico imprimiu *"O PAR EXISTE mas o nome do resultado não bate com 'yes'/'sim'"* **duas linhas abaixo** de `outcomes='["Yes", "No"]' outcomePrices=None`. O nome estava lá; o que faltava era o PREÇO. A condição olhava só `outcomes` e concluía sobre o *nome* — medida de ausência × ausência de medida do 1.6, agora dentro do instrumento escrito para fazer valer essa distinção. E o teste que deveria pegar isso PASSAVA, porque a fixture encodava a minha suposição (nomes trocados) em vez da forma que o servidor manda — a lição do §6.1b outra vez. `porque_ilegivel` agora diz QUAL metade do par faltou, uma frase por caso. **A hipótese verdadeira era a 2**, não a 4: o mercado cheio também não traz o par — `/markets/{id}` dessas pernas **não carrega sequer a chave** `outcomePrices`. **E embaixo dela estava o que de fato explica os 18:** nada ali estava fechado. `closed=False active=False`, slugs `will-person-v-win-...`, `will-person-af-win-...` — são **vagas reservadas** para candidatos ainda sem nome, **77 de 128** mercados em `democratic-presidential-nominee-2028` e **76 de 128** em `presidential-election-winner-2028`. O motivo `fechado_sem_resolucao_legivel` **nomeava um fato que não aconteceu**, e recusa com nome falso não vira métrica — é a regra do projeto, violada por mim durante três rodadas, e ela mandava consertar a busca, que nunca foi o problema. Agora são coisas separadas: `nunca_abriu` (`active=false` **sem** `closed`) devolve `perna_nem_abriu_nem_resolveu`, e `_falta_resolucao` para de buscar `/markets/{id}` para elas — requisição medidamente jogada fora. A ordem das checagens é a da força do que se aprende: `evento_ja_decidido` (definitivo) → vaga reservada (ESTRUTURAL, não se conserta com um GET) → fechado ilegível (lacuna de dado); deixar o ilegível na frente foi o que manteve a causa de 77 em 128 escondida atrás de uma que se resolvia buscando. **A vaga RECUSA, não sai da cesta:** não sei se ela pode ser ativada e vencer depois, e se puder o conjunto dos abertos não é exaustivo e a "cesta" é aposta direcional com cara de arbitragem — falha fechada. 47 testes, 4 mutações verificadas. **O que isto significa para a rota:** a aritmética da cesta rodou em **2 eventos dos 22** e não achou folga (`sem_folga`); os outros 20 saíram por estado do evento, não por preço. Eventos-eleição de 128 vagas **não são candidatos a cesta enquanto houver reserva aberta** — isso é achado, não falha. ⬜ **falta**: descoberta que ache eventos neg-risk em que TODA perna esteja ou aberta-com-livro ou fechada-com-resolução-legível; entre os 22 desta amostra não havia nenhum além dos 2 medidos, e 2 eventos não dão veredito de rota. **Filtro de campo (2026-09-17, auditoria §3.4):** a replicação independente de Breguez (36 eventos negRisk, 964 snapshots de 2026-08-20 a 30, andando o livro L2 inteiro) mediu **56 % dos campos com ≤ 20 resultados completos e traváveis, e 0 % dos com > 20** — o achado das vagas reservadas medido por outra pessoa, com outro método, dez dias antes. `MAX_RESULTADOS_DA_CESTA = 20` e o motivo `campo_grande_demais` recusam o evento grande ANTES de qualquer GET por perna: campo grande é iliquidez, não mispricing, e olhar para ele custava rede para chegar a uma recusa que já se sabia. 49 testes. ⬜ falta a mesma coisa de antes — descoberta que traga eventos neg-risk de campo pequeno; os 22 desta amostra continuam sendo os 22. |

**A correção de escala do programa de rewards.** O quadro dizia *"940 mercados
com pool, 6.766 USDC/dia"*, de uma **amostra** de 2.500 mercados da Gamma. A
lista autoritativa é do próprio CLOB (`GET /rewards/markets/current`, paginada
até `LTE=`): **18.384 mercados, 185.520 USDC/dia**. Errado por **27×**. A
conclusão qualitativa sobrevive — o programa vive fora das janelas curtas — a
escala não.

**Dois defeitos meus, achados medindo, e os dois escondiam dinheiro:**

1. As cotações hipotéticas iam só até 500 shares, e `rewards_min_size` chega a
   **1.000** em 98 mercados que carregam **39.287 USDC/dia — 21% do programa**.
   A varredura publicava zero e parecia que os maiores pools não pagavam.
2. `score_da_ordem` modela **entrar na fila** (N ticks para fora do topo). Em
   mercado largo isso nunca pontua. **Não foi alterada** — é compartilhada com
   o motor ao vivo, e mudá-la mudaria o comportamento do bot. A concessão é
   medida ao lado.

**O achado estrutural que isso destravou: pool sem dono não é pool de graça.**
No `Spread: LAC (-9.5)` — 11.657 USDC/dia, `score_do_mercado` **zero** — o
livro está 0,23 / 0,34 (spread de **11 centavos**) contra `max_spread` de
**2,5**. Para pontuar é preciso cotar **dentro** do spread, num preço que
ninguém oferece. O reward ali é pagamento por fornecer liquidez que o mercado
recusa àquele preço. Daí `concessao_para_pontuar_c`: zero = basta entrar na
fila; alto = paga-se markout imediato, à vista, por cada share.


### O maker de PARES — o que os outros bots fazem, medido aqui (2026-09-14)

`docs/OUTROS_BOTS.md` guarda o estudo do código de três bots públicos de
Polymarket (`warproxxx/poly-maker` `4f32103`, `terrytrl100/polymarket-automated-mm`
`849cda4`, `RuneDn/polymarket-liquidity-bot` `0cfcd36`) e o que se vê dos
lucrativos, que não publicam código. **Nenhum bot público é lucrativo:** o
único com resultado real publicado fechou a sessão em **−$15,51**
(`poly-maker/TIPS.md:74-77`), com a maior perda num único fill adverso em
livro fino. O que se repete em quem aparece nos leaderboards dos binários de
cripto é ESTRUTURA, não previsão: compra dos DOIS lados em lote pequeno,
nunca vende, e deixa o par liquidar em 1,00 — ganhando `1 − (pUp + pDown)`
mais o rebate.

Essa é a conta que o 4.2 nunca fez: ele mede cada perna pelo markout de 5 s,
que é negativo por construção para um maker. `scripts/maker_de_pares.py`
(22 testes em `tests/test_maker_de_pares.py`) faz a conta do PAR sobre a
gravação real, com o MESMO modelo de execução do `CaixaDoMaker` (prints
`last_trade_price` do lado SELL a preço ≤ o nosso bid; atravessada = a perna
inteira, no nível = pro-rata com a fila que o livro mostra à frente) e a
resolução da própria gravação para a perna que ficou sozinha.

**O DIA INTEIRO, medido (2026-09-13 UTC, 27 arquivos, 1.000 janelas Up/Down
— 692 de 5 min, 230 de 15 min, 58 de 1 h, 20 de 4 h —, lote de 20 shares por
perna, `relatorios/PARES_20260913.json`):**

**(1) Ficar parado com o livro andando contra é 98% da perda.** A cotação que
descansa e não recolhe perde **−4.227,56 USDC no dia**. A MESMA cotação, que
recolhe a ordem 100 ms depois de o melhor bid cair abaixo dela, fecha o termo
determinístico em **−79,76**. É a maior diferença que este projeto já mediu
entre duas regras de cotação, e a regra é a que o `poly-maker` já usa (o
regime EVENT do `poly-maker`, documentado em `docs/OUTROS_BOTS.md` §2).

**(2) O que sobra é pequeno e ainda negativo.** Com o recolher ligado e o
gatilho de salto do spot a 3 bps (cooloff de 5 s), o termo que NÃO depende de
sorte — par travado + rebate teto — fecha em **−80,21 + 45,53 = −34,68 USDC
no dia**, sobre 16.969 shares executadas e 8.474 USDC de capital. A soma
`pUp + pDown` paga nos pares caiu de 1,23 (sem recolher) para **1,015**: o
recolher tira quase toda a seleção adversa, mas não a inverte.

**(3) O +165 que aparece na coluna do total é CARA-OU-COROA, e está marcado
como tal no relatório.** Ele vem de `residual_das_pernas_soltas` = +199,95,
que é o resultado de 346 pernas que ficaram sozinhas e foram à resolução:
52,3% ganharam, a 0,491 de preço médio. O desvio-padrão desse termo é
**186 USDC** (`incerteza.sigma_da_perna_solta_usdc`), então `z = 0,89` — não
se distingue de zero. Quem ler essa coluna como lucro está lendo ruído; por
isso o relatório publica `sem_a_aposta_travado_mais_rebate` ao lado, e a
tabela imprime o `z`.

**(4) Quanto maior a janela, melhor** — e isto sim tem sinal: +143,96 ¢ por
janela de 1 h (40 janelas), +56,29 ¢ por janela de 15 min (175), **+0,63 ¢**
por janela de 5 min (381). As janelas de 5 min de cripto, que são as do
taker, são o pior lugar para esta estrutura.

**A hipótese que mais infla o fill está isolada e medível.** Um print que
passa ABAIXO do nosso preço conta a perna INTEIRA — é o que o `CaixaDoMaker`
faz (§4.2), e são 451 das 1.205 execuções do dia. Um print de 3 shares não
pode ter comprado 20. O eixo `atravessada` liga o teto de verdade
(`tamanho_do_print`). Como a hipótese gera MAIS fill, ela puxa o resultado
para BAIXO: o negativo acima é, nessa direção, pessimista.

**As peças dos bots que lucram, medidas no mesmo dia (`--grade focada`,
`relatorios/PARES_FOCADA_20260913.json`) — e uma delas VIRA O SINAL.**

A coluna que importa é o termo determinístico (par travado + rebate; a perna
solta é aposta e vai à parte):

| cotação | lote 5 | lote 20 | lote 100 |
|---|---|---|---|
| juntar ao topo (o que o projeto fazia) | −5,57 | **−34,67** | −170,95 |
| **1 tick abaixo do MICROPRICE** | **+6,60** | **+30,68** | **+137,84** |
| 1 tick abaixo do microprice + viés de inventário (2 ticks) | +7,37 | **+36,09** | +125,21 |
| 3 ticks abaixo do microprice | −3,25 | −3,32 | −53,88 |

**Cotar a partir do microprice, e não do melhor bid, é o que faz o par valer
a pena.** A soma `pUp + pDown` que pagamos cai de **1,015 para 0,989–0,995**:
o par passa a ser comprado por MENOS de 1,00, que é exatamente a economia dos
makers de leaderboard. O preço disso é fill: 108 pares em vez de 334 (e
8.278 shares em vez de 16.969 no lote 20) — **menos execuções, e melhores**.

O viés de inventário ajuda no lote pequeno (+30,68 → +36,09) e atrapalha no
grande (+137,84 → +125,21): descer o bid do lado comprado evita o par, e com
lote grande o par é onde está o dinheiro. Ficar 3 ticks abaixo do microprice
mata o fill (33 pares) e junto o resultado.

**O que este número NÃO é:** não é lucro do dia. (a) A perna solta continua
sendo a maior linha do P&L e é aposta — no lote 100 ela vale −98,11 com
sigma **587**; (b) a hipótese de execução atravessada (perna inteira por
print abaixo do nosso preço) infla o fill, e portanto é PESSIMISTA aqui;
(c) o recolher foi medido a **100 ms**, e esta máquina tem p50 de 245 ms.
O capital empregado SOMADO no dia foi 3.905 USDC no lote 20 e 17.565 no lote
100 — não é exposição simultânea, e por isso não vira "% ao dia" nenhum.

**A mesma conta no regime que PAGA tem instrumento, coleta e as duas
parcelas.** Os pools do 1.12 são outro mercado — markout 4,7× menor, reward
por estar no livro, resolução em dias. `scripts/markout_dos_pools.py
--gravar` passou a guardar os eventos crus no formato do recorder (com um
registro `pools_snapshot` que diz quais dois tokens formam cada par, mais
`rewards_max_spread` e `rewards_min_size` do instante da coleta — eles mudam
ao vivo), e `scripts/maker_de_pares_nos_pools.py` (14 testes) roda o MESMO
motor sobre essa gravação, com três diferenças que não podiam ser
escondidas:

- **a grade traz as peças que a grade dos bots mediu no Up/Down** sobre a
  célula do laço ao vivo (1 tick do meio, recolher a 245 ms): microprice,
  pausa de 30 s depois do fill atravessado, reprice de 4 ticks, histerese
  de 2 ticks, e pausa + reprice — porque aqui cada recolhida também CUSTA
  reward (a receita só conta com as duas pernas descansando), e o número
  do Up/Down não vale para os pools sem medir;
- **a perna solta é marcada a preço de saída** (melhor bid do fim, menos o
  fee de taker): marcar a resultado num mercado que não resolveu seria
  inventar o resultado. Sem bid no fim, a janela sai do P&L;
- **a cotação é a do bot ao vivo** — a N ticks do MEIO, que é onde o §15.3
  pontua, e não juntando ao melhor bid: num livro largo (o `LAC` tem 11
  centavos de spread) juntar ao topo é ficar fora da banda e não pontuar. O
  motor ganhou guarda para nunca cruzar o ask, que seria virar taker;
- **o reward entra, pela MESMA função que o `laco_maker` chama**
  (`estimar_retorno`), integrada no tempo em que as DUAS pernas repousam,
  truncando intervalo acima de 60 s para que queda de feed não vire receita.
  Reward e custo ficam separados no JSON: um é estimativa com hipótese de
  fila, o outro é medido nos prints.

Coleta de 6 h em curso desde 2026-09-14 15:37 UTC (60 mercados), com a
varredura de persistência de 2 h ao lado (300 pools) para refazer o 1.12 com
dado do mesmo dia.

**A latência que este Mac tem NÃO é a que a medida usou, e isso importa.**
`scripts/benchmark_latency.py --label mac-casa`
(`relatorios/LATENCIA_MAC_20260914.json`, 2026-09-14 18:05 UTC, 100
requisições em conexão quente): REST do CLOB **p50 = 244,9 ms, p90 = 260,0,
p99 = 349,7, máx 609,6**; conexão fria 161,7 ms até o TLS e 256,9 até o
primeiro byte; WS do CLOB 471,3 ms para conectar; WS do RTDS 694,1 ms, com a
primeira mensagem 208,4 ms depois de assinar. O PING/PONG do WS não
respondeu sem assinatura ativa — fica anotado que a melhor aproximação de
decisão→ack ainda é o REST quente.

Ou seja: **cancelar leva ~245 ms no p50 e ~350 ms no p99 daqui**, não os
100 ms que a rodada do dia usou. **O dia inteiro na latência real
(`relatorios/PARES_LATENCIA_20260913.json`, `--grade latencia`, 1.000
janelas, lote 20, juntando ao topo, salto 3 bps, termo determinístico
`travado + rebate`):**

| recolher | determinístico | travado | rebate | soma paga do par | pares |
|---|---|---|---|---|---|
| parado | −2.573,00 | −2.665,60 | 92,59 | 1,198 | 683 |
| 100 ms | −34,67 | −80,21 | 45,53 | 1,015 | 334 |
| **245 ms (p50 daqui)** | **−144,70** | −196,84 | 52,14 | 1,028 | 362 |
| 350 ms (p99 daqui) | −183,05 | −237,85 | 54,80 | 1,033 | 370 |
| 600 ms | −229,17 | −286,04 | 56,87 | 1,041 | 383 |
| 1000 ms | −315,48 | −376,79 | 61,31 | 1,048 | 409 |

Cada 100 ms a mais de latência custa **30–40 USDC por dia** neste lote: o
que a ordem parada perde (−2.573) o recolher recupera quase inteiro a 100 ms
e só 94 % a 245 ms — e os 6 % que sobram são o dobro do rebate do dia. A
soma paga do par sobe com a latência (1,015 → 1,028 → 1,048) porque os
pares a mais que entram são justamente os que o taker fecha contra nós
enquanto o cancelamento viaja. A perna solta fica ruído em todas as linhas
(sigma 137–149; residual de +199,95 a −111,93 sem ordem). Uma VPS perto do
CLOB vale, medido, da ordem de 110 USDC/dia neste lote em relação a esta
casa — a decisão de onde hospedar tem efeito em USDC, não só em
milissegundos. O que a latência faz à configuração do MICROPRICE (a que
vira o termo positivo) é a linha-base da grade `bots`, abaixo.

**O que faltava do `poly-maker` agora tem eixo, teste e grade — e falta o
número.** Sobre a melhor configuração medida (microprice −1 tick, lote 20,
salto 3 bps) e na latência REAL desta máquina (recolher a 245 ms),
`scripts/maker_de_pares.py --grade bots` isola cada peça que o estudo
apontou e ainda não tinha sido separada: (1) **pausa por fill tóxico** —
depois de um fill atravessado a janela inteira fica 30 ou 90 s sem cotação
(o regime EVENT deles disparado pelo NOSSO fill, não pelo salto); (2)
**`c_vol · σ`** — o alvo desce `vol_x ×` a amplitude do meio dos últimos
10 s, em ticks; (3) **`flow_z`** — o alvo anda com o fluxo assinado dos
prints dos últimos 10 s, nunca acima do topo; (4) **`reprice_ticks` por
estratégia** (1 e 4, "descansar > reagir"); mais a sensibilidade de
`atravessada = tamanho_do_print`, (5) a **histerese do reconciler** deles
(só recolocar mais fundo se o alvo caiu mais que 2 ticks) e as combinações
que o `poly-maker` roda juntas. Os mecanismos estão presos em 7 testes novos
(`tests/test_maker_de_pares.py`, `TestOQueFaltavaDoPolyMaker`): a pausa
recolhe as DUAS pernas e volta depois; fill no nível não pausa; a vol
esvazia passado o horizonte; fluxo comprador não melhora o topo; o reprice
da estratégia sobrescreve o global; a histerese segura a ordem numa descida
de 2 ticks e solta numa de 3.

**Uma hora de fumaça já diz a ordem** (`relatorios/PARES_BOTS_SMOKE.json`,
2026-09-13 12:00–13:00 UTC, 160 janelas, termo determinístico; não é o dia):
base micro-1 a 245 ms **+14,24** (20 pares, soma paga 0,980); **pausa de 30 s
+18,42** (soma 0,966) e de 90 s −0,52 (mata o fill); **reprice 4 +18,29**
(menos recolocações, mesma soma); **atravessada por tamanho do print
+24,54** — a hipótese da perna inteira é pessimista aqui, como escrito;
**fluxo PIORA** (1 tick −7,30; 2 ticks −34,23, soma 1,049: inclinar o alvo
para o lado do fluxo comprador devolve a ordem ao topo e desfaz o desconto
do microprice); **vol como estava não cota** (0,34 com 120 shares e 7,4
MILHÕES de recolocações na hora: a amplitude muda a cada evento, o alvo
com ela, e a ordem é recolocada a cada evento — o reconciler deles tem
histerese justamente para isso, e ela virou o eixo (5), com a vol agora
medida com histerese). ⬜ falta o número do dia
(`relatorios/PARES_BOTS_20260913.json`, em curso): a rodada anterior ficou
presa porque o Mac está NA BATERIA (26 %) e dormindo entre uma leitura e
outra — 500 mil registros a cada 15–20 min de relógio de parede, contra 25
mil por segundo acordado. `caffeinate -dimsu` não segura o sono na bateria.
O que reproduz o número é o Mac na tomada.

**ONDE cotar é um eixo, e a focada já diz para onde ele aponta.** A quebra
por duração e por ativo do `PARES_FOCADA_20260913.json` (total com rebate e
com a perna solta, USDC no dia), na melhor configuração (microprice 1 tick,
lote 20, recolher 100 ms): 5 min **−37,14** (178 janelas), 15 min +24,07
(109), 1 h **+58,42** (39), 4 h +6,76 (11); btc **−134,86** (198 janelas),
eth **+128,54** (100), bitcoin +32,35 (24), ethereum +26,07 (15). O sinal é
o mesmo nas SEIS configurações com microprice (lote 5/20/100 × skew 0/2):
5 min e btc negativos em todas, 1 h e eth positivos em todas — e os
leaderboards dizem que quem ganha nas janelas curtas de BTC tem latência
<100 ms, que esta máquina não tem (p50 = 245 ms). Como o total carrega o
cara-ou-coroa da perna solta, o número que fecha o filtro é o termo
determinístico com ele LIGADO: `Estrategia.duracao_min_s` e
`Estrategia.sem_ativos` (3 testes em `tests/test_maker_de_pares.py`,
classe `TestOndeCotar`; a quebra por grupo agora traz
`sem_a_aposta_travado_mais_rebate`), grade `--grade onde` (base dos bots ×
{sem filtro, ≥15 min, sem btc, ≥15 min sem btc, ≥1 h} e os quatro filtros
com pausa 30 s + reprice 4). ⬜ falta o número do dia
(`relatorios/PARES_ONDE_20260913.json`, em curso em paralelo com a grade
dos bots).

⬜ **falta**: a rodada `--grade focada` (lote, viés, microprice), a
sensibilidade do eixo `atravessada`, e o resultado da conta nos POOLS do
1.12 — que são outro regime (markout 4,7× menor) e onde o reward entra na
conta. Enquanto isso não existir, isto NÃO reprova a rota maker: reprova a
ideia de copiar o formato "compra os dois lados e espera" para as janelas de
cripto de 5 min.


### O lado da RECEITA do 1.12 se reproduziu num segundo dia (2026-09-14)

A varredura de persistência rodou de novo, mesma forma (300 maiores pools,
12 amostras em 2 h): `relatorios/POOLS_PERSIST_20260914.json`.

| | 2026-09-13 | 2026-09-14 |
|---|---|---|
| mercados com pool (universo do CLOB) | 18.384 | **17.008** |
| pool diário somado | 185.520 USDC | **130.239 USDC** |
| medidos | 300 | 300 |
| pontuam em ≥ 50% das amostras | 185 | **261** |
| pontuam em 12/12 | 185 | **226** |
| receita somada **pelo mínimo** | 174,08 USDC/h | **207,14 USDC/h** |

Dois dias, dois números da mesma ordem, com a mesma propriedade: quem
pontua, pontua sempre, e com **concessão zero** (250 dos que pontuam) — basta
entrar na fila, sem apertar o spread. O critério (a) e o (b) do 1.12 não
foram sorte de um dia.

O que isto NÃO diz: que a rota lucra. É **receita**, e o custo continua sendo
o markout mais o custo de SAÍDA da perna unilateral, que é o termo aberto do
1.12 (ver a nota do custo de saída acima). A coleta de 6 h de 2026-09-14
existe para fechá-lo, e `scripts/conta_do_maker_nos_pools.py` já aplica o
critério: o 1.12 passa de novo se `receita − execuções/h × max(markout 30
min, spread/2)` continuar positivo.

### O pool de reward não é esporádico — ele é da JANELA DE 4 H

Esta página dizia "≈ 1 % das janelas participam", e a frase estava certa na
aritmética e **errada na leitura**. Quebrando por duração no dia inteiro de
2026-08-24 (`M2_20260824.json`, 739 janelas):

| duração | com pool | sem pool | % com pool |
|---|---|---|---|
| 5 min | 1 | 518 | 0,2 % |
| 15 min | 1 | 167 | 0,6 % |
| 1 h | 0 | 44 | **0,0 %** |
| **4 h** | **8** | **0** | **100 %** |

**Todas as janelas de 4 h têm pool. Nenhuma outra duração tem.** O "1 %" saía
de dividir 10 por 739 — e 739 é dominado pelas 518 janelas de 5 min, que
existem em muito maior número justamente por serem curtas. Contar janela como
unidade mistura coisas de tamanhos diferentes.

O motivo das 729 sem pool é **um só**: `sem_taxa_diaria` (`rewards_daily_rate`
ausente na Gamma, com `rewards_max_spread` e `rewards_min_size` presentes). A
cadeia do dado foi conferida ponta a ponta — não é campo que ninguém lê.

**A consequência prática inverte o veredito da rota:** ela não é inviável por
falta de pool; ela é **restrita à janela de 4 h**, onde o pool está sempre lá.
É nessas janelas que o 1.6 foi avaliado, e é por isso que o **+35,6 USDC/8h**
(rewards 25,8 + exec. líquida 9,7, fator=0,3) é o número da rota viável, e não
uma média sobre um universo que inclui mercados que não pagam.

### ⚠️ O 1.6 estava marcado ✅ e foi REVERTIDO em 2026-08-31

Esta página dizia *"o 1.6 PASSA com a fórmula confirmada"*, com **+35,6
USDC/8h**. O `resumo_m2.py` — o instrumento que lê o relatório — reporta **NÃO
AVALIÁVEL** em toda rodada, e o `VEREDITO_M2.md` §"MAKER" diz, desde o
primeiro veredito, que o critério é *"não avaliável **por construção**, não
por falta de amostra"*. O quadro contradizia os dois.

**Como o número foi construído, e onde ele escorrega.** O relatório publica
`rewards_usdc = 39,37` para 200 shares @ 1 tick, 2 lados, em 7,995 h — e esse
número **já é a nossa fatia pro-rata**. A avaliação anterior dividiu-o pela
fatia média (0,457) para estimar o pool total (≈ 86 USDC) e aplicou o fator
0,3 **sobre o pool**, chegando a 25,8. Aplicar o mesmo 0,3 sobre a fatia que a
fórmula já dá levaria a 39,37 × 0,3 = **11,8**. As duas leituras do mesmo
"fator 0,3" diferem por **2,2×**, e nenhuma das duas está escrita no critério
pré-registrado — que é o que o torna não avaliável.

**Os três termos que faltam continuam faltando**, e nenhum deles é amostra:
`volume_taker_usdc` e `custo_de_markout` em USDC dependem de **quais** das
nossas cotações teriam sido executadas — isto é, da posição na fila, que o WS
agregado não entrega —, e `capital_imobilizado` é decisão do M3.

**O que o número vale:** é uma estimativa de ordem de grandeza, útil para
decidir se vale construir o motor maker (item 4.0). Não é medição, e não fecha
critério. Fica registrado como estimativa, com o método à vista, em vez de
apagado — apagar esconderia que a rota chegou a parecer aprovada.

**Consequência no placar:** o MAKER **não passa nos cinco**. Passa em 1.7,
1.8, 1.9 e 1.10; o 1.6 fica não avaliável até existir medição de fila ou uma
definição pré-registrada do que o fator 0,3 desconta.

### O terceiro termo do 1.6 é decidível — e ele reprova o tamanho avaliado

`o_que_falta_para_fechar` lista três termos. Os dois primeiros dependem da
fila. **O terceiro, `capital_imobilizado`, não** — ele sai dos tetos que já
estão no `RiskSettings`, e ninguém tinha feito a conta:

| cotação | rewards 8 h | capital preso | retorno/capital | cabe no teto de 50 USDC? |
|---|---|---|---|---|
| 200 shares, 2 lados | 39,37 | **200 USDC** | 19,7 % | **ESTOURA 4×** |
| 50 shares, 2 lados | 22,75 | 50 USDC | **45,5 %** | sim |
| 50 shares, 1 lado | 15,65 | 25 USDC | **62,6 %** | sim |

**O 1.6 foi avaliado com 200 shares nos dois lados — que prende 200 USDC
contra o teto de exposição de 50** (item 3.9). A cotação que fundamentou o
"+35,6 USDC/8h" não passaria pelo próprio portão de risco do projeto.

**E o tamanho menor rende MAIS por USDC imobilizado**, não menos: 45,5 %
contra 19,7 % em 8 h. O motivo está na fórmula — o score é linear no tamanho,
mas a nossa entrada também engorda o denominador, então dobrar a cotação menos
que dobra a fatia. Com o fator 0,3, 50 shares em dois lados dão **6,83 USDC em
8 h sobre 50 de capital (13,7 %)**.

**O que isso fecha e o que não fecha.** Fecha o terceiro termo: o capital é
decidível, e a decisão é ≤ 50 shares por lado. Não fecha o 1.6 — os dois
primeiros termos continuam dependendo da fila. O que muda é que o número que
circulava (+35,6) vem de um dimensionamento que o portão recusa, e o número do
dimensionamento admissível é **cinco vezes menor**.

**1.10 confirmada em 2026-08-30** — ver linha 1.10 acima e API_NOTES §15.3.

### O M2.2 rodou (2026-08-31), e os números de 1.7 e 1.8 NÃO mudaram

24 h de 2026-08-24, 126.724.222 registros, 7 h 37 min de processamento —
`relatorios/M2_20260824.json`, com `analysis/rewards.py` já usando
`S(v,s)=((v-s)/v)²×b`.

| | antes (M2_24AGO_MEDIDO) | **M2.2, fórmula confirmada** |
|---|---|---|
| 1.7 markout 5 s | −0,1974 (246.504 exec.) | **−0,1974 (246.504 exec.)** |
| 1.8 horas na célula | 65,915 h | **65,922 h** |

**Iguais, e isso é o resultado — não um erro de rodada.** Markout é o que o
preço faz depois da execução, e horas de amostra é quanto tempo a célula
existiu: nenhum dos dois passa pela fórmula de reward. O que a fórmula move é
o **1.6**, que é onde ela entra na conta. A pendência dizia "confirmar 1.7 e
1.8 com a fórmula certa", e a confirmação é literalmente esta: eles não
dependiam dela. Sem rodar, isso era suposição.

**A captação desta rodada é a melhor do projeto:** `pior_fracao_coberta` 1,0,
**100 % nos oito ativos**, 0 silêncios, 896 janelas conhecidas e 820 com
resolução. A âncora saiu **CONFIRMADA** com τ=0 explicando 100 % das 768
janelas elegíveis, quartis 190/192/196/190 e `concentrada: false`.

**E o taker afundou onde já estava reprovado.** A curva de horizonte com o
preditor cru dá `hit_rate` **abaixo de 0,5 nas cinco bandas**, e piorando
conforme o horizonte encurta: 0,4172 (>240 s) · 0,3657 · 0,2881 · 0,2010 ·
**0,1222** (<30 s). Nenhuma banda tem edge. Um `hit_rate` de 0,12 não é
ausência de sinal — é sinal com o **lado invertido**, e é exatamente a
pergunta que o `direcao_sem_fill` existe para responder sobre uma coorte que
não muda com o fill. Ainda **não rodado** sobre esta amostra.

### A âncora está fechada

| | |
|---|---|
| Consistência em τ=0 | **0,9984** |
| Janelas elegíveis | **640** (de 647 recebidas) |
| Discordantes | 1 |
| Quartis | 134 / 168 / 170 / 168 · `concentrada: false` |
| Família de controle (`media_60s`) | 0,9528 |

Amostra grande, bem espalhada no tempo, e a família perdedora continuou
perdendo por 4,5 pontos. **Não é mais pergunta em aberto.**

### Um defeito de instrumento achado nesta rodada

`cobertura_da_gravacao` reporta **1,0** em todos os oito ativos, e o mesmo
relatório registra um silêncio de `conexao_inteira` de **3.601 s**. Os dois não
podem estar certos.

Neste caso o silêncio é benigno — é o buraco da hora 01:00, que foi excluída de
propósito (dois dos três fragmentos vieram corrompidos da origem). Por isso
`suspeita_de_assinatura_caducada` deu 0 e os oito ativos "emudeceram" com
0,152 s de dispersão: é a borda entre arquivos, não o feed. Mas **a métrica de
cobertura não descontou o buraco**, e é justamente ela que o veredito da
âncora consome desde o M2.9.

**Conserto feito (M2.13):** `coberto_s` passou a ser a SOMA dos intervalos,
cada um limitado a `idade_maxima_da_amostra_ms` — a mesma régua que o resto do
relatório usa para dizer que uma janela abriu "em lacuna". No caso desta
rodada a conta nova dá **0,9526** onde a antiga dava 1,0000, e o novo
`maior_buraco_s` sai em **3.601,0 s**, que bate com o `intervalo_s.max` e com o
silêncio de conexão inteira de 3.600,67 s. Os números agora concordam entre si.
O relatório também ganhou `buracos_s` e `silencio_inicial_s` — a borda da
frente, que só o `silencio_final_s` não via.

**⬆️ Fim do histórico do Bloco 1.** Daqui para baixo, estado corrente.

---

## Bloco 2 — M3: modelo e calibração

| # | Item | Estado |
|---|---|---|
| 2.1 | Modelo TWAP endgame | ✅ `engine/twap.py` — variância agora **medida** (`engine/variancia.py`), não derivada; a derivada errava por 39–48× |
| 2.2 | Modelo horário | ✅ `engine/hourly.py` |
| 2.3 | Curva de calibração sobre gravação real | ✅ **MEDIDA e CALIBRADA** — ECE de **0,0126 a 0,0493** nos cinco baldes, 20 faixas ocupadas em cada. *(Com o preditor derivado eram 0,0694 no melhor balde e 0,207 na banda operada, viés MISTO e SEM ORDEM — ver abaixo.)* |

### O 2.3 mudou de natureza, não só de estado

Até 2026-08-25 este item estava marcado "⏳ existe por bucket; falta amostra".
As duas metades da frase envelheceram, e por motivos opostos.

**O que existia não media calibração.** O `erro` publicado era
`|prob_média_prevista − freq_realizada|`, e `freq_realizada` é a **taxa-base do
balde**. Um preditor que cospe uma constante igual à taxa-base tirava nota
máxima sem saber nada — foi o que a rodada de 20 h expôs no balde `<30s`:
previsto 0,514 contra realizado 0,5073, cara-ou-coroa dos dois lados, e o
critério "passou" com 0,0067.

**O M2.13 trocou o instrumento.** Agora o relatório publica
`curva_de_confiabilidade` por faixa de probabilidade prevista,
`erro_de_confiabilidade` (ECE) e `faixas_ocupadas`. O critério virou
CONJUNÇÃO — `calibracao_avaliavel` (≥ 3 faixas com amostra) **e** ECE abaixo do
limiar —, porque o ECE sozinho também não pega o preditor constante: ele cai
todo numa faixa só.

**E a amostra deixou de faltar.** São 24 horas contínuas e limpas do dia 24,
mais 23 do dia 23 e 5 do dia 25.

Ou seja: o que faltava no 2.3 **não era código nem dado** — era rodar o
backtest com o instrumento novo sobre a gravação que já existia. **Rodou.** A
mesma rodada fechou o 2.3 do M3 e tirou o critério 1.3 do M2 do limbo do "não
avaliado": ele ficou **avaliado e reprovado**, com `calibracao_avaliavel` true
e ECE 0,207 na banda operada.

**E em 30/08 o 1.3 deixou de reprovar.** O ECE de 0,207 não era defeito do
sinal: era a variância derivada subestimando o desvio-padrão em 6,3×, o que
saturava `P(Up)` nos extremos — exatamente as ~75 mil previsões de ~79 mil
citadas acima. Com a `V(t)` medida em dia anterior ao avaliado (§2d-ter do
`VEREDITO_M2.md`), o ECE cai para 0,0126–0,0493 nos cinco baldes. Os números
dos três parágrafos anteriores descrevem o instrumento antigo e ficam como
registro de como se chegou aqui.

---

### O analisador ficava mudo por três horas — M2.15

Descoberto em 2026-08-26, do pior jeito possível: a rodada de 24 h foi
lançada, o operador rodou `tail -f` no log e viu um arquivo **vazio**. A
leitura de fora foi "travou".

Não estava travado. O backtest tem 6 chamadas de `print` no arquivo inteiro, e
todas são de erro ou do JSON final — ele não imprimia **nada** enquanto
processava. Três horas e meia de silêncio absoluto, por construção.

Agora ele diz onde está, a cada 500 mil registros:

```
[04:12:07] passada 1: comecando sobre 24 arquivo(s)
[04:13:41] passada 1: 500,000 registros | 5,319/s | 1.6 min nesta passada | rss 1.84 GiB
```

Duas decisões que não são detalhe:

**Vai para STDERR.** O relatório sai por stdout; progresso ali corromperia o
JSON, e quem redirecionasse `> relatorio.json` receberia um arquivo que não
parseia. Há teste travando isso.

**`rss` está junto porque o modo real de falhar numa máquina de análise não é
erro — é swap.** E swap não parece travamento: parece lentidão sem fim. Sem o
número, não há como distinguir "está devagar" de "não vai terminar".

Uma armadilha apareceu na implementação: `ru_maxrss` vem em **bytes no macOS**
e em **kilobytes no Linux**. A conta errada dá 1024× de diferença — e como a
máquina de análise é um Mac e os testes rodam em Linux, o erro passaria
despercebido nos dois lugares por motivos opostos. Há teste parametrizado nas
duas plataformas.

---

### `scripts/analisa_dia.sh` — a colagem saiu do caminho

Três caracteres do zsh interativo morderam esta campanha, cada um custando
uma rodada:

| | O que acontece |
|---|---|
| `#` | sem `interactive_comments`, vira `command not found: #` e a linha seguinte roda solta |
| `!` | expansão de histórico, **ativa mesmo dentro de aspas duplas** — `echo "PID $!"` prende o terminal em `dquote>` |
| `&` | numa colagem de várias linhas, muda o que roda em primeiro e segundo plano |

O custo do `!` não foi o incômodo: foi uma rodada de 24 h que **nunca
começou** e ninguém percebeu, porque o log que provaria isso também nunca foi
criado. O operador esperou horas por um processo inexistente.

Agora é um comando curto, sem caractere especial nenhum:

```
./scripts/analisa_dia.sh 20260824
./scripts/analisa_dia.sh 20260824 ~/pulsearb-dados ~/pulsearb-m2 relatorios/VARIANCIA_23AGO.json
```

O quarto argumento é opcional: se passado, o `--curva-de-variancia` é repassado ao backtest. É o que o M2.2 exige para re-rodar com a variância medida em vez da derivada.

Ele monta os links **só das horas cujo gzip abre inteiro** — a hora corrente e
a que morreu no meio de uma escrita reprovam, e incluí-las envenenaria a
rodada por causa de um arquivo, depois de três horas de processamento.

**E ele prova que subiu antes de dizer que subiu.** Espera a linha
`passada 1: comecando` aparecer no log; se o processo morrer antes, imprime o
log e sai com erro.

Isso não é paranoia de projeto — os dois testes do próprio script pegaram dois
defeitos nele:

1. A primeira versão checava só "arquivo não vazio", e **anunciou `rodando`
   para um processo que tinha acabado de morrer** com `ModuleNotFoundError`.
   Mensagem de erro também enche o arquivo.
2. A segunda passava caminho **absoluto** no `--json`, que a contenção de
   saída do M2.5 recusa. O próprio guard do script mostrou o erro.

---

## Bloco 3 — M4: execução e travas (**nada existe**)

`main.py:153` — *"Trava do M1: só SIM existe. SHADOW/LIVE chegam no M4."*

| # | Item | Estado |
|---|---|---|
| 3.1 | `risk/gates.py` | ✅ **M4.1** — 8 portões, 47 testes |
| 3.2 | Cliente de ordens, assinatura EIP-712, auth do CLOB | ✅ **2026-08-30** — `execution/auth.py` (42 testes: L2 HMAC-SHA256 + typed data do L1 + `CredenciaisL2.do_ambiente`) e `execution/ordem.py` (41 testes: struct EIP-712, valores, `AssinadorLocal`). Travado por **conferência diferencial** contra o `polymarket-client==0.6.0`: sete casos de valores, o typed data e a assinatura, byte a byte. Fatos em API_NOTES §12.14. Falta só falar com o servidor de verdade |
| 3.3 | Modo SHADOW | ✅ **M4.3** — decide tudo, envia nada, 15 testes |
| 3.4 | Modo LIVE + trava tripla (`MODE=LIVE` + `CONFIRM_LIVE` + `EU ACEITO O RISCO`) | ✅ **2026-08-30** — `risk/autorizacao.py`, 22 testes. A frase é comparada EXATAMENTE; `escolher_executor` só chega em LIVE por ela, e a recusa lista TODOS os bloqueios |
| 3.5 | Ordens FOK, conexão quente, nonce/idempotência, rejeição e timeout **Reconciliação no ARRANQUE ligada em 2026-09-17 (auditoria §2.3):** `reconciliar`/`cancelar_orfas` existiam desde 2026-09-06, testadas, e tinham **zero chamadas** fora dos testes — o contrato *INCERTA obriga reconciliação* estava escrito em três lugares e cumprido em nenhum. `LacoMaker.reconciliar_no_arranque` casa o que achávamos repousar com o que o servidor lista, ANTES de qualquer cotação: órfã é cancelada por default (exposição que nenhum portão autorizou), fantasma com todas as pernas sumidas é largado, perna só sumida fica para o `passo`, e `ErroDeLeitura` SOBE — o `ProcessoShadow.run` a chama antes de subir as tarefas e marca `falhou = reconciliacao_no_arranque: …` em vez de cotar por cima de órfã que ninguém viu. O resultado sai no relato de 60 s (`maker.reconciliacao_no_arranque`). Em SHADOW o cliente sombra lê o diário, então o caminho roda a cada arranque; em LIVE é o `GET /data/orders`. 5 testes no laço + 3 no processo (um deles prende a FIAÇÃO, não só a função). 🟡 continua: o que falta é o de sempre — uma ordem assinada recebendo resposta. **MEDIDO EM 2026-09-18, E A RECUSA NÃO É DO CÓDIGO: bloqueio REGIONAL.** O `smoke_ordem_assinada.py` correu na VPS de Londres com a carteira dedicada vazia e o CLOB respondeu **403 `Trading restricted in your region`** (API_NOTES §17). **O que isso PROVA que funciona:** as credenciais L2 e a assinatura de GET estão certas — a leitura de saldo é autenticada e devolveu 0,0 —, a descoberta achou mercado operável ao vivo (`btc-updown-5m-1789742700`, tick 0,01), a ordem FOK foi construída e ENVIADA, e as travas do smoke funcionaram (leu o saldo antes de tudo, seguiu só por ser zero). **O que isso NÃO fecha:** o critério do 3.5 exige uma recusa de NEGÓCIO (saldo, allowance), que prova que o servidor avaliou o corpo da ordem. O 403 de região é anterior: o servidor não olhou para a ordem. **O que isso abre, e é maior que o 3.5:** o modo LIVE não opera desta máquina, com código nenhum. Não é defeito a consertar, é onde a máquina está — decisão de conformidade, não de engenharia. **O que continua POR VERIFICAR, e a primeira versão desta linha afirmou demais (achado do Codex no #160):** a assinatura L2 do POST da ordem. `assinar_l2` assina `timestamp + método + caminho + corpo`; o saldo é um GET de corpo VAZIO, a ordem é um POST com corpo, e a serialização canónica do corpo nunca foi exercitada contra o servidor. O 403 de região chega antes dessa validação. Falta: correr o mesmo smoke de uma região em que a Polymarket permita operar. Um 4xx de NEGÓCIO fecha o item; um 401 no `/order` seria a primeira medida da assinatura do POST, não uma regressão. | 🟡 **código completo; a encanação já falou com o CLOB, a ordem assinada não** — `execution/cliente.py` (**71 testes**, com `tipo_de_ordem` configurável desde 31/08 — ver 4.0) contra `MockTransport`: FOK, id determinístico, e **timeout ≠ recusa** (três estados; `INCERTA` é terminal e obriga reconciliação). **Cancelamento em 2026-09-06** (`cancelar`, 11 testes): `DELETE /order` verificado no SDK 0.6.0 (API_NOTES §4.4), com `EstadoDoCancelamento` de três estados em que o PERIGO inverte — `INCERTA` no envio manda PARAR, no cancelamento manda RECONCILIAR e recancelar é seguro; um 200 cujo `canceled[]` não traz o id é `NAO_CANCELADA`, não sucesso. **Listagem em 2026-09-06** (`listar_ordens_abertas`): `GET /data/orders` paginado (§4.5), assinando o path PELADO e mandando a query fora da assinatura; leitura é **fail-closed** — timeout/5xx levantam `ErroDeLeitura`, NUNCA devolvem lista vazia, porque "nenhuma ordem aberta" é afirmação que declara o livro limpo. **Medido ao vivo em 2026-08-31** (`scripts/smoke_clob_rest.py`, só GET público): DNS, TLS e os endpoints respondem 200, com **p50 de 218 ms e p99 de ~970 ms** — ver API_NOTES §16. **Passo 6 do RUNBOOK §8.1 implementado em 2026-09-10:** `derivar_credenciais` (`execution/auth.py`) faz `POST /auth/api-key` com queda para `GET /auth/derive-api-key` no 400 — o 400 ali não é erro, é o servidor dizendo que a credencial daquele endereço já existe, e só criar falharia da segunda vez em diante (`[VERIFICADO]` SDK 0.6.0, `create_or_derive_api_key`). A resposta traz **`apiKey` em camelCase**, não `key` nem `api_key`: ler o nome errado devolveria credencial VAZIA em silêncio, falhando depois no envio como `auth_recusada`, longe da causa — o mesmo modo de falha do §6.1b, e por isso cada campo é conferido e nomeado. `scripts/derivar_credenciais.py` é o que o operador roda: grava arquivo `0600` e **nunca imprime o segredo**, nem em erro. Verificado por mutação: ler `key`, vazar o payload no erro, ou não cair para derivar no 400 — cada um derruba testes. **Antes disto o passo 6 não tinha código**: `cabecalhos_l1` existia e dizia servir para derivar, mas ninguém chamava o endpoint. **O CONSTRUTOR CONCRETO chegou em 2026-09-11:** `ConstrutorDeOrdemLocal` (`execution/ordem.py`). Até aqui o `execution/cliente.py` declarava `ConstrutorDeOrdem` como **Protocol** e as únicas implementações eram dublês de teste — o caminho estava todo coberto e **não havia jeito de produzir uma ordem real**. Ele costura peças que já existiam e já eram verificadas (`valores_da_ordem`, `OrdemNaoAssinada`, `typed_data_da_ordem`, `AssinadorLocal`), e a costura trouxe duas decisões próprias: **`owner` é a API KEY, não o endereço** (`[VERIFICADO]` `_build_send_order_payload` — pôr o endereço dá ordem bem formada que o servidor recusa por dono desconhecido, sem apontar o campo), e **o `id_do_cliente` NÃO vai no fio** (o corpo do CLOB não tem campo para ele, e enfiá-lo em `metadata` mudaria o hash assinado, porque `metadata` está em `_ORDER_FIELDS`). Três mutações verificadas: owner com endereço, troca de maker/taker (preço invertido) e ignorar `neg_risk` (exchange errado no domínio assinado) — cada uma derruba o seu teste. **O SMOKE existe desde 2026-09-11** (`scripts/smoke_ordem_assinada.py`, 9 testes nas travas). **A primeira execução real, em 2026-09-11, achou um defeito NO PRÓPRIO SCRIPT:** ele chamava `http.get(..., content=...)`, e o `get` do httpx não aceita `content` — o `TypeError` caía num `except Exception` largo e virava *'não consegui ler o saldo'*. A leitura **nunca tocou a rede**, e o operador leu falha de REDE onde havia código quebrado: duas causas opostas com a mesma mensagem, e a segunda invisível até alguém depurar na mão. Corrigido para `request` e, mais importante, o `except` foi **estreitado para `httpx.HTTPError`** — só falha de rede pode virar `None`; erro de programação sobe. **A trava funcionou:** ela abortou em vez de enviar com estado desconhecido, que é exatamente o que ela existe para fazer. **Segunda execução, mesma data: a ordem SAIU e o CLOB respondeu `403 auth_recusada`.** O que isso prova e o que não prova: a leitura de `/balance-allowance` devolveu **200** com a MESMA credencial e o MESMO mecanismo de assinatura L2 — então **a assinatura está certa**, e o 403 no `POST /order` aponta para permissão da CONTA para a ação (sem colateral, sem allowance, ou conta não habilitada), não para o que assinamos. **Mas não fecha o 3.5:** recusa por auth significa que o servidor parou ANTES de avaliar o corpo, então o corpo EIP-712 segue sem prova de estar bem formado. Dois defeitos meus vieram junto e foram corrigidos: o cliente **descartava o corpo da resposta** em 401/403 — justamente onde a causa mora, e onde *assinatura recusada* e *conta sem permissão* são indistinguíveis sem ele —, e a mensagem do smoke imprimia *'é o desfecho esperado'* incondicionalmente, inclusive quando o motivo era `auth_recusada`, que é o único que NÃO fecha o item: ele envia uma ordem ASSINADA e trata **recusa como sucesso** — recusa é resposta, e uma recusa que NÃO seja `auth_recusada` prova que a assinatura L2 foi aceita e o corpo estava bem formado, que é o que o item pede. **Três travas, porque ele fala com exchange real:** (1) confirmação com frase exata, na forma do 3.4 — aproximada não serve; (2) **recusa se a carteira tiver saldo**, e saldo ILEGÍVEL aborta em vez de virar zero (não saber não autoriza enviar); (3) **FOK a preço que não cruza** — FOK nunca repousa, e o preço não tem o que cruzar, então mesmo que a trava 2 falhasse a ordem morreria no instante. Duas mutações verificadas: aceitar frase aproximada e transformar saldo ilegível em zero — cada uma derruba a sua trava. Falta o que sempre faltou: **uma ordem assinada recebendo resposta** — agora a um comando de distância, e sem mover dinheiro, que exige credencial e move dinheiro. Nenhum GET substitui isso. O `content=` (e não `json=`) é o que preserva os bytes assinados; falha de rede vira `INCERTA` e nunca recusa, porque recusa autorizaria reenviar uma ordem que talvez esteja no livro |
| 3.6 | Trava: stake máximo por trade e por janela (US$ 5) | ✅ **M4.1** — mais exposição total, posições e disjuntor |

### 3.1 e 3.6 fecharam — os portões vêm ANTES do cliente de ordens

Ordem deliberada: um cliente de ordens sem portão é uma máquina de perder
dinheiro que já funciona; um portão sem cliente é um teste que não custa nada.

| Portão | Recusa quando |
|---|---|
| `modo_nao_opera` | modo não é LIVE — SIM e SHADOW nunca enviam |
| `disjuntor_armado` | a perda do dia estourou, ou o registro estava ilegível |
| `feed_parado` | algum feed está velho — preço velho é preço que já não existe |
| `ordem_mal_formada` | shares ≤ 0, ou preço fora de (0, 1) |
| `preco_fora_da_faixa` | fora de [0,05 · 0,95] — a 0,97 arrisca-se 0,97 para ganhar 0,03 |
| `stake_acima_do_teto` | a ordem passa de 5 USDC |
| `janela_no_teto` | o acumulado no MESMO mercado passa de 15 USDC |
| `exposicao_no_teto` | o capital simultâneo em risco passa de 50 USDC |
| `posicoes_no_teto` | mais de 5 janelas com posição aberta |

Três decisões de projeto, cada uma cobrindo uma forma concreta de sangrar:

**Falha fechada.** `avaliar()` começa negando. Registro do dia ilegível **arma
o disjuntor** em vez de assumir que estava tudo bem — não dá para distinguir
"arquivo corrompido" de "arquivo com o disjuntor armado que não consigo ler".

**O disjuntor gruda.** Não desarma porque o número melhorou depois (perdeu 11,
armou, ganhou 5 → continua armado), não desarma na virada de data, e
**sobrevive a reinício** porque é gravado em disco com rename atômico. Sem
persistência ele viraria um limite por vida de processo — que não é limite
nenhum: bot perde, processo cai, systemd reinicia, contador zera, bot perde de
novo.

**Toda recusa se nomeia.** `Decisao.motivo` é constante de `MOTIVOS`;
construir uma recusa com frase livre levanta `ValueError`. Recusa anônima não
vira métrica nem alarme, e não distingue "o bot está travado" de "o bot não
achou trade".

Os tetos não são chute de conforto — saem do que o M2 mediu: 2,91 USDC
movimentados por trade, 0,18 de lucro, e profundidade mediana de 87,8 USDC na
duração mais líquida. Subir qualquer um deles deve esperar a curva de
capacidade (M2.14) dizer onde o teto está.
| 3.7 | Trava: perda diária máxima → disjuntor | ✅ **M4.1** — código usa **US$ 25**, este doc dizia 20; decisão sua |
| 3.8 | Trava: 4 perdas consecutivas → pausa de 1 h | ✅ **M4.4** — persiste, atravessa a meia-noite, 9 testes |
| 3.9 | Trava: exposição simultânea máxima | ✅ **M4.1** — código usa **5 janelas / US$ 50**, este doc dizia 2; decisão sua |
| 3.10 | Trava: feed velho / relógio > 250 ms / spread anômalo | ✅ **3 de 3** — feed ✅ (M4.1), spread ✅ (M4.4, teto 0,04), relógio ✅ `live/relogio.py` detecta **anomalia** (pior ativo, 250 ms) e **salto**, e recusa em LIVE sem fonte. **O sensor não certifica sincronia** (equação de via única — ver §3.10 abaixo): isso é limitação física coberta pelo daemon NTP (5.4 ✅). Wiring: `live/shadow.py` passa `relogio_do_servidor=precos.relogio` ao `PortaoDeRisco` |
| 3.11 | Kill switch: arquivo `KILL` + botão no dashboard | ✅ **2026-08-30** — arquivo ✅ **M4.4** (lido a cada ordem, ilegível = acionado); botão ✅ `ui/server.py`, 8 testes. **Só arma, não desarma**: o que para o bot fica parado até uma pessoa apagar o arquivo na máquina, e o dashboard não tem autenticação — uma rota que desarmasse seria uma rota para religar um bot parado de propósito. Chave puxada por `touch` fora da página aparece nela |
| 3.12 | Suíte de testes das travas (uma por trava) | ✅ — **108**: 47 no portão, 27 nas travas novas, 20 no relógio do servidor, 14 na sincronia NTP |
| 3.13 | SHADOW rodando 24 h sem crash | ✅ **CUMPRIDO em 2026-08-31** — PID 26423, de 00:46 a 01:20 do dia seguinte: **24,58 h de diário contínuo, sem crash**. 45.652 linhas, **259 intenções aprovadas**, **cinco portões exercitados** (`pausa_por_sequencia` 44.701 · `spread_anomalo` 525 · `preco_fora_da_faixa` 95 · `livro_desconhecido` 66 · `feed_parado` 6), disjuntor desarmado. **O PnL de +113,85 NÃO conta** — este ensaio rodou com o motor que gravava `preco_pago = best_ask`, corrigido só depois; ver o aviso abaixo e o item 4.2. **E um defeito apareceu no fim:** o processo **não encerrou no prazo** — ver 3.14 |
| **3.14** | **`--duration` não encerrava: o relógio congela quando a máquina dorme** | ✅ **causa medida e corrigida em 2026-08-31.** O ensaio do 3.13 seguiu vivo em **24,6 h** com `--duration 24h`. Não era cancelamento preso — escrevi o teste de `run()` com descoberta lenta e ele **passou**. A causa é outra: `time.monotonic()` no macOS sai de `mach_absolute_time()`, que **congela em suspensão**. Medido nesta máquina: **190,8 h de monotonic contra 370,8 h de parede desde o boot — 180 h de sono**. O deadline era só monotônico, então `24h` virava 24 h + o que a máquina dormisse. `caffeinate -i` não cobre: impede o sono por inatividade, não o de tampa fechada. **Corrigido com prazo DUPLO** (`prazo_vencido`), encerrando no primeiro a vencer — só parede também não serviria, porque um ajuste de NTP encurtaria a rodada, e é o relógio que o 3.10 diz não poder assumir estável. 3 testes novos, um por modo de falha |
| **3.16** | **Rodada suspensa era indistinguível de rodada saudável: sensor de vigília** | ✅ **medido e coberto em 2026-09-05.** Segunda consequência do 3.14, que ficou sem sensor: os **dois** laços do `ProcessoShadow` correm em tempo monotônico — decisão a cada 1 s, relato a cada 60 s —, então **congelam juntos** quando a máquina dorme. O relato sai sempre com `+60 passos` e uma rodada suspensa tem a mesma cara de uma saudável. **Medido:** rodada de 24 h na bateria (PID 9131, 01:22–11:08 UTC) ficou **1,16 h acordada em 9,77 h de parede — ciclo de trabalho 11,9%**, projetando **2,9 h de observação em 24 h**. `feeds_saudaveis` esteve `true` o tempo todo, e corretamente: os feeds não têm defeito quando o processo inteiro está suspenso. Nove horas se passaram antes de alguém comparar os dois relógios. **Corrigido** com `vigilia` no resumo de 60 s (`_vigilia`), comparando monotônico com parede em **duas** faixas: `da_rodada` responde se a rodada vale como medida de 24 h, e `desde_o_relato` denuncia a parada nova que o acumulado dilui. Ciclo preso em 1,0 para que NTP para trás não invente 'acordado mais que o tempo decorrido'. 6 testes, verificados por mutação. **Operacional:** subir com `caffeinate -dimsu` **e na tomada** (o `-s` é ignorado na bateria); tampa fechada dorme de qualquer forma. **Confirmado sobre rodada real:** rodada de 24 h (PID 17024, 2026-09-05 20:27 → 2026-09-06 20:27 UTC) encerrou no prazo de parede com **86.400,6 s = 24,0 h exatas**, `falhou: None`, 86.209 passos, 1.434 relatos. O sensor reportou **ciclo de trabalho 1,0 · acordado 24,0 h · dormiu 0,0 h · zero janelas de 60 s com sono** — contraste direto com a rodada de 05/09 (ciclo 0,12) que motivou o item. `caffeinate -dimsu` + tomada segurou a máquina acordada as 24 h. Ressalva: essa rodada correu com o **disjuntor armado** (−27,84 USDC herdados), então validou o sensor, não o caminho de trade — toda intenção recusada a montante |

*(A linha 3.13 sumiu do quadro num merge entre as duas sessões e foi
restaurada em 2026-08-31. Fica o registro: conflito neste arquivo é o mais
provável de todos, e uma linha perdida em merge é indistinguível de um item
que nunca existiu.)*

### 3.13 — a primeira tentativa não mediu nada, e o motivo tem nome

A rodada anterior (PID 24507) gravou **3.050 entradas e recusou todas** com
`relogio_derivado`. Não era relógio: era latência de rede até a Polymarket,
~1.278 ms no diário, contra um teto de 250 ms.

**E a latência foi medida por fora depois, o que fecha o argumento**
(API_NOTES §16, 2026-08-31): `GET /ok` e `GET /time` dão **p50 de ~218 ms e p99
de ~970 ms** de ida e volta, do mesmo Mac. O teto do item 3.10 é 250 ms — ou
seja, a recusa não vinha de relógio ruim, vinha de a régua ser menor que a
distância. O offset do relógio também foi estimado, e **não decide nada**: o
espalhamento das amostras ficou maior que a mediana nas duas rodadas, então a
medição só diz "sub-segundo". É por isso que o 5.4 exige daemon de NTP, e não
uma conta nossa.

`_portao_do_relogio` aplicava o teto **em todos os modos**. A ausência de
fonte já tinha a exceção certa — fora do LIVE não recusa, senão o SHADOW não
ensaia —, mas o atraso acima do teto não tinha, e apagava o diário inteiro.
Corrigido: **só o LIVE recusa por `relogio_derivado`**; a fonte muda
(`atraso_ms = None`) continua recusando em qualquer modo, porque não saber
custa o mesmo que saber que está ruim.

**O ensaio corrigido, medido em duas fotos** (a segunda é a corrente):

| | 2,8 h | **7,2 h** |
|---|---|---|
| linhas no diário | 7.684 | **21.398** |
| intenções aprovadas | 32 | **72** |
| `pausa_por_sequencia` | 7.604 | 21.267 |
| `preco_fora_da_faixa` | 32 | 32 |
| `livro_desconhecido` | 16 | 16 |
| `spread_anomalo` | — | **11** |
| PnL realizado | +18,48 | **−11,82 USDC** |
| perdas seguidas / disjuntor | 0 / desarmado | 1 / desarmado |

**O PnL trocou de sinal, e isso não é surpresa — é convergência.** De +18,48
para −11,82 com o dobro de entradas. O taker reprova em 1.1 e 1.4 por ausência
de borda medida no backtest; um SHADOW que ficasse lucrativo ao vivo seria a
contradição a explicar, não este número.

**Cinco portões já foram exercitados**, contra um só na primeira hora. É o que
dá sentido ao ensaio: um diário com um motivo único não distingue "as travas
funcionam" de "uma trava está engolindo tudo".

**Os três avisos que este número exige.** (a) O SHADOW **não prova
preenchimento** — ninguém do outro lado sabe que a ordem existe, então o PnL é
uma conta sobre fill assumido, não uma observação. (b) 72 entradas não
sustentam veredito nenhum, nem no sinal negativo. (c) `pausa_por_sequencia` em
99 % das linhas **não é 99 % do tempo**: o bot continua tentando durante a
pausa, e cada tentativa vira uma linha.

> **⚠️ O PnL DESTE ensaio é otimista por construção, e o defeito era de código.**
> Até 2026-08-31 o motor gravava `preco_pago = livro.best_ask` — o topo, como
> se a ordem inteira coubesse no primeiro nível e não custasse slippage. O
> backtest sempre atravessou o livro com `simulate_taker_buy`. **Duas contas
> para a mesma coisa**, que é exatamente a violação que a regra do *mesmo
> caminho* existe para impedir: a diferença apareceria como "o mercado ao vivo
> é melhor", quando é aritmética.
>
> Achado ao investigar por que o shadow marcou **+126,77 às 10,6 h** enquanto o
> backtest do mesmo motor dá negativo. Corrigido no motor, com teste de topo
> raso travando `preco_pago > best_ask`.
>
> **O ensaio NÃO foi reiniciado** — o que ele mede sobre estabilidade e
> portões vale, e reiniciar custaria as horas já corridas. Ele completou
> **24,58 h e fechou o 3.13**. Mas o **PnL dele (+113,85) sai enviesado para
> cima** e não entra em conta nenhuma. O item **4.2** exige um ensaio com o
> motor corrigido, e esse ainda **não começou**.

**O que melhorou em relação à primeira hora** (25 aprovadas, PnL +19,27, e
`pausa_por_sequencia` como ÚNICO motivo): agora há **quatro** portões
aparecendo no diário, não um. `preco_fora_da_faixa` e `livro_desconhecido`
sendo exercitados é o que dá sentido ao ensaio — um diário com um motivo só
não distingue "as travas funcionam" de "uma trava está engolindo tudo".

**A trava 3.8 disparou ao vivo pela primeira vez, e está certa.** Quatro perdas
seguidas pausam por 1 h. A consequência operacional é que 24 h de ensaio
produzem ordens em poucas dezenas, não milhares — o que **limita o que o 3.13
pode concluir**, e é melhor saber disso antes de ler o resultado do que depois.
Não é motivo para afrouxar a trava.

### A terceira trava do 3.10: o que ela pega, e o que ela NÃO pega

Feed velho e spread anômalo eram medidos desde o M4.1/M4.4. "Relógio > 250 ms"
estava no item desde a especificação e **nunca teve fonte**. Agora tem uma — e
a parte mais importante deste registro é o limite dela, porque a primeira
versão prometeu mais do que entrega e a revisão do PR #47 pegou.

**A medição.** Cada tick do feed-verdade traz o carimbo do servidor; na
chegada, olhamos o nosso relógio. Chamando `offset` a diferença entre o nosso
relógio e o verdadeiro (negativo = estamos atrasados):

    atraso = chegada_local − carimbo_servidor = latencia + offset

**Duas incógnitas numa equação só**, medidas de UMA via.

**O erro que eu cometi.** Escrevi que isso é um *limite superior* da deriva —
"pequeno prova relógio bom". É falso, e falso na direção perigosa, porque as
parcelas se cancelam:

| offset | latência | medido | portão 250 ms | erro real no `seconds_left` |
|---|---|---|---|---|
| **−400 ms** | **400 ms** | **0 ms** | **passa** | **400 ms** |
| −100 ms | 120 ms | +20 ms | passa | 100 ms |
| +300 ms | 80 ms | +380 ms | recusa | 300 ms |

Relógio local atrasado é justamente o caso que infla o `seconds_left` — o bot
opera achando que sobra mais tempo do que sobra — e é justamente o caso que a
latência positiva mascara. Um `abs()` depois não recupera nada: o valor já saiu
zerado da subtração.

**O que a trava vale, então.** É **detector de anomalia**, não certificado de
relógio. `|atraso|` grande prova que algo está grande; `|atraso|` pequeno é
ausência de alarme deste sensor, e nada mais.

**As duas metades que fecham o buraco:**

1. **NTP/chrony verificado é PRÉ-CONDIÇÃO de deploy**, não algo que software
   nosso possa provar com um feed de uma via. Entra na lista do Bloco 5, ao
   lado da carteira dedicada e da imagem Docker.
2. **O salto do relógio esse dá para pegar**, e é o modo de falhar mais comum:
   o NTP corrige de vez no meio da operação. `_Saltos` compara o avanço do
   relógio de parede com o do monótono — lendo os dois por conta própria, não
   o `chegada_ms` do chamador, que carrega o espaçamento entre ticks e faria
   um feed reproduzido parecer relógio pulando. Salto detectado recusa por
   30 s: as medianas anteriores foram calculadas com o relógio antigo.

**Uma janela por ativo, e o portão lê a pior** — também da revisão. Com janela
única, um ativo continuamente atrasado entre oito saudáveis fica abaixo da
mediana global; os ticks dele chegam sem parar, então a checagem de feed velho
também não acusa, e uma ordem naquele ativo sairia com preço velho e o portão
dizendo que está tudo bem.

**Três recusas, todas deliberadas:**

- **fonte muda** (sem tick, amostra velha, ou salto recente) → recusa. Não
  saber custa o mesmo que saber que está ruim; tratar não-sei como zero é o
  defeito do `cobertura_da_gravacao`, que o M2 já pagou uma vez.
- **em LIVE sem fonte instalada** → recusa tudo. Uma trava que se auto-desativa
  quando ninguém a ligou não é trava. Fora do LIVE a ausência não recusa —
  o SHADOW existe para ensaiar.
- **carimbo no futuro** → recusa igual, em módulo. É o que resta de sinal de
  relógio local atrasado depois que a cancelação come o resto.

**Como a trava se provou real:** ao ligá-la, **todos** os testes de caminho
LIVE passaram a recusar por `relogio_nao_monitorado`. Os testes passaram a
instalar a fonte, como o ciclo ao vivo terá de fazer.

~~**O que ainda faltava:**~~ **Fechado.** (a) NTP verificado → **5.4 ✅** (`risk/sincronia.py`, daemon NTP, 14 testes); (b) ciclo ao vivo construir o `PortaoDeRisco` passando a fonte → **`live/shadow.py` ✅** (`relogio_do_servidor=precos.relogio`). O 3.10 virou ✅.

### 3.4 e 5.4 fecharam — a autorização para LIVE existe antes do cliente

Mesma ordem que fechou o 3.1 e o 3.6 antes do cliente de ordens: **a licença
vem antes da máquina**. Uma trava tripla escrita às pressas no dia em que o
cliente chegar é uma trava que ninguém testou.

`risk/autorizacao.py` responde uma pergunta só — *pode entrar em LIVE?* — e a
responde com **todos** os bloqueios de uma vez:

| Bloqueio | O que é | O que impede |
|---|---|---|
| `modo_nao_e_live` | configuração | o default nunca opera |
| `sem_confirmacao_explicita` | `PULSEARB_CONFIRM_LIVE=1` | um `.env` copiado de outra máquina |
| `sem_aceite_do_risco` | a frase exata, digitada | automação e engano de dedo |
| `relogio_nao_sincronizado` | o daemon de NTP (5.4) | operar com `seconds_left` errado |
| `sem_cliente_de_ordens` | 3.2 e 3.5 | acreditar que trava substitui código |

**Três travas de intenção porque uma não basta e duas se copiam juntas.** A
frase é comparada **exatamente**: `true`, `sim` e `eu aceito o risco` são
recusados, e há sete testes parametrizados travando isso. Um booleano se digita
sem pensar — é justamente o que a terceira trava existe para impedir. Espaço
nas pontas passa, porque é artefato de terminal e não descuido.

**Todos os bloqueios, e não o primeiro.** Reportar um por vez faria o operador
consertar, rodar, descobrir o próximo — cada volta achando que era a última.
Pior: enquanto o cliente de ordens não existisse, ele apareceria primeiro e a
trava tripla nunca seria exercitada.

**O 5.4 fecha a metade que o sensor de tempo não fecha.** `live/relogio.py`
mede `latencia + offset` numa subtração só e as duas se cancelam — ver 3.10.
Sincronia verificada vem de quem faz medição de duas vias: o daemon de NTP da
máquina. `risk/sincronia.py` pergunta ao systemd, ao chrony e ao macOS, nessa
ordem, e distingue três respostas em vez de duas:

- **sincronizado** → passa
- **daemon respondeu que não** → recusa, e o conserto é investigar rede/fonte
- **não determinado** (sem daemon, formato desconhecido, timeout) → recusa, e o
  conserto é instalar e habilitar NTP

Colapsar os dois últimos em "não" mandaria o operador pelo caminho errado. E
não determinado recusa igual: um relógio não verificado tem o mesmo efeito no
`seconds_left` que um relógio errado — a diferença é só a nossa ignorância.

Três defesas no subprocesso, porque rodar comando externo num processo que
decide é coisa que dá errado: timeout obrigatório de 3 s, nunca levanta
exceção, e **não roda no caminho quente** — é verificação de subida, e há um
teste que quebra se alguém a chamar com a sincronia já em mãos.

**O que isto NÃO faz:** autorizar. Com `cliente_de_ordens_existe=False`, que é
o estado real, a autorização nunca sai positiva. O caminho positivo existe e é
testado para que os testes de recusa provem alguma coisa — se tudo recusasse
de qualquer jeito, eles não provariam nada.

### O ciclo de decisão ao vivo existe — `live/ciclo.py`

Esta seção dizia, até 2026-08-30: *"o executor existe e está testado, mas não
há ciclo de decisão ao vivo para alimentá-lo"*. As peças listadas abaixo
estavam todas prontas; faltava **a orquestração**, e é ela que entrou.

`CicloAoVivo` recebe `FeedEvent`s, roteia para o estado e chama o motor:

| Evento | Vai para | Guarda |
|---|---|---|
| `rtds` + `crypto_prices_twap_sixty` | `precos.anotar` | só este tópico; e18 exato e carimbo do servidor obrigatórios |
| `rtds` + outro tópico | contado, ignorado | `crypto_prices` (spot) não é a âncora |
| `poly_ws` | `livros.aplicar` | mesmo `OrderBook` do critério 1.5 |
| outra fonte | **contada** | "não chegou nada" e "não sei ler" têm consertos opostos |

**Nenhum parser novo.** Cada evento é lido pela MESMA função do backtest —
`parse_rtds_event`, `e18_do_evento`, `eventos_do_payload`, `LivrosAoVivo`. As
duas últimas eram privadas dentro do `backtest/__main__.py` e mudaram de casa
para `feeds/`; duas cópias fariam uma divergência de parsing aparecer como
diferença de mercado, que é o que a comparação SHADOW×backtest existe para
detectar.

**Sem rede aqui dentro.** O ciclo não abre socket, não faz HTTP e não dorme.
Isso não é elegância: é o que permite alimentá-lo com uma **reprodução de
gravação** e rodar SHADOW e backtest sobre o MESMO dado. Um ciclo que só
soubesse falar com a rede não poderia ser confrontado com nada — e a
confrontação é a razão de o SHADOW existir.

#### Um buraco que apareceu ao montar o ciclo, e não tinha dono

`PrecosAoVivo` devolve o último preço de um ativo **sem olhar a idade dele**.
Um ativo mudo entre sete saudáveis decidiria com preço velho, e nada mais no
caminho pegaria: `livros` mede silêncio por token, mas preço não; o portão
`feed_parado` olha o feed, não o ativo.

O ciclo fecha isso calculando `feeds_saudaveis` **pelo pior ativo** — a mesma
escolha do sensor de relógio, e pela mesma razão. Fechar tudo por causa de um é
conservador e é o lado certo para errar: com entrada única por janela, o custo
de parar é uma janela perdida; o de operar com preço velho é uma posição tomada
contra um mercado que já se moveu. `precos_velhos_s` **nomeia** quais ativos
estão velhos — "feed parado" sem dizer qual não é alarme acionável.

Sem nenhum preço ainda, a saúde é `false`: bot recém-subido não sabe nada, e
não saber não autoriza.

#### E o processo que lhe dá rede — `live/shadow.py`

O ciclo não abre socket por design. `ProcessoShadow` é quem abre, e reusa a
fiação do recorder — `RtdsFeed`, `PolyMarketWsFeed`, `MarketDiscovery`, as
mesmas classes que gravaram as 24 h do M2. Se o SHADOW abrisse os sockets por
outro caminho, uma diferença de assinatura ou de reconexão faria a população
que ele vê divergir da que o backtest leu, e a comparação entre os dois
perderia o sentido.

```
python -m pulsearb.live.shadow --duration 24h \
    --curva-de-variancia relatorios/VARIANCIA_23AGO.json
```

**A ligação que a fábrica faz, e que é a mais fácil de esquecer:** o
`PortaoDeRisco` recebe `relogio_do_servidor=precos.relogio` — a MESMA
instância que o ciclo alimenta tick a tick. Sem ela a trava de relógio diria
"não sei" a cada ordem, o diário sairia com `relogio_nao_monitorado` em toda
linha, e nenhum dos portões que interessam seria exercitado. Há teste travando
a identidade da instância, porque uma cópia não recebe os ticks.

**Redundância no RTDS, como no recorder.** `rtds_conexoes` (default 2)
conexões ao mesmo endpoint. Conexão individual já produziu lacunas de 30 a
306 s, e uma lacuna aqui que a gravação não tem faria o SHADOW perder ticks de
âncora que o backtest enxerga — furando exatamente a comparação que justifica o
SHADOW. O tick repetido que a redundância produz é descartado **no ciclo**, não
no processo, para valer também na reprodução de gravação; e é **contado**
(`preco_repetido`), porque perto de zero com duas conexões significa que a
redundância não está funcionando.

**Só o jogo TWAP é operado.** A janela horária resolve pelo candle 1h da
Binance, e a âncora dela é o campo `o` do `kline_1h` (`engine/hourly.py`) — não
o `twap_sixty`. Um processo que só assina RTDS não tem essa série, e
`estimar_prob_up` cairia em `prob_up_hourly` com a âncora do observável errado:
toda probabilidade horária sairia de uma série que não resolve aquela janela.
`jogos_operados` recusa, conta (`jogo_sem_feed_proprio`) e só se amplia quando
o feed da Binance estiver ligado e roteado.

**Só os ativos OPERADOS entram no feed.** `all_price_assets` inclui os
`extra_price_assets`, que existem para gravação e backtest futuro. Como
`feeds_saudaveis` fecha pelo pior ativo, um SOL mudo bloquearia intenções de
BTC/ETH saudáveis — o gate de saúde passaria a depender de ativos que o bot nem
opera.

**O tamanho da ordem não sai do teto em USDC.** São unidades diferentes:
`stake_max_por_trade_usdc` é USDC e quem o aplica é o portão, sobre
`shares × preço`; `shares_por_trade` é em SHARES, e o default (5) é o mínimo
que o mercado aceita (§12.5). E o motor passou a recusar ordem abaixo desse
mínimo, como o backtest já fazia — sem isso o SHADOW registraria `pode=true`
para ordem que a corretora rejeitaria.

**Assinaturas rodam, não acumulam.** Token de janela encerrada é desassinado
depois da carência de resolução — a MESMA de `pulsearb.tempo` que o recorder
usa. Sem isso, 24 h de descoberta acumulariam milhares de assinaturas, e cada
reconexão reenviaria o conjunto histórico inteiro no frame inicial.

**Três cadências, e cada uma tem um número por trás:**

| Laço | Cadência | Por quê |
|---|---|---|
| decisão | 1 s | o feed entrega 1,061 tick/s por ativo; decidir mais rápido que o dado chega não muda nada |
| descoberta | 30 s | janela de 5 min descoberta com 30 s de atraso ainda sobra 4,5 min, e a faixa operada são os últimos 240 s |
| relato | 60 s | estado no log sem poluir |

O custo da primeira é até ~1 s de atraso a mais que o backtest — dentro da
grade de latência que o M2 já mediu (150 a 1000 ms).

**Um passo que levanta NÃO derruba o processo.** O SHADOW existe para rodar
24 h e mostrar o que aconteceu; cair no primeiro evento estranho entregaria
zero informação sobre as outras 23 horas. O erro sai nomeado no log e o laço
segue — e há um teste que trava isso, para que ninguém "limpe" o `try/except`
achando que ele esconde bug.

**Janela não-operável também é assinada.** O motor decide se opera e o diário
quer o motivo; não ver o livro de uma janela recusada trocaria "recusei por X"
por "não sei nada sobre ela", que é justamente o que o M2 quer medir.

**O que ainda falta para o 3.13:** rodar. As 24 h contínuas exigem máquina e
tempo, não código.

#### O elo que faltava para o "mesmo caminho" — `replay/ao_vivo.py`

O ciclo não abre socket por design. `live/shadow.py` lhe dá rede; `replay/ao_vivo.py` lhe dá gravações.

`ReplayCiclo` itera um arquivo de gravação e alimenta o `CicloAoVivo`, mas com um detalhe que não é cosmético: `agora_ns` e `agora_epoch` passados a `ciclo.passo()` e `ciclo.feeds_saudaveis()` vêm de `record.ts_wall_ns`, não de `time.time_ns()`. Sem isso, preços gravados ontem pareceriam ter chegado 24 h atrás — `feeds_saudaveis()` retornaria `False` para todo tick no replay acelerado, e o ciclo nunca decidiria.

**O teste de "mesmo caminho" prova a paridade.** `test_replay_ao_vivo.py` roda os primeiros 5 minutos de `pulsearb-20260824-2000.jsonl.gz` pelos dois caminhos e exige três invariantes:

1. **Séries E18 idênticas** — os parsers do ciclo e do backtest direto produzem `(ts_wall_ns, valor_e18)` iguais para cada ativo. Uma divergência aqui apareceria como diferença de mercado na comparação SHADOW × backtest quando na verdade seria diferença de código.
2. **Relógio da gravação** — `ts_inicio_ns` cai em 2026-08-24, não no dia de hoje. Prova que o ciclo não está usando `time.time_ns()`.
3. **Discovery alimenta o rastreador** — `on_descoberta()` recebe e conta os mercados do snapshot gravado.

O teste roda em ~2 min localmente e é ignorado em CI (sem gravação real). PR #55.

### As peças que o ciclo casa

*Escrito enquanto elas eram construídas, e mantido: cada uma carrega uma
decisão de projeto que continua valendo. O que mudou em 2026-08-30 é que o
componente que as casa passou a existir — a seção acima.*

O que ainda descreve o estado: o `main.py` é dashboard mais feeds, quem tem a
fiação de rede é o recorder (957 linhas: descoberta, rotação de janelas,
assinatura, livro), e quem tem a lógica de decisão sobre gravação é o
`BacktestRunner`. O `CicloAoVivo` é o consumidor da primeira e o par ao vivo da
segunda.

Primeira peça pronta: **`live/rastreador.py`** — quais janelas estão abertas
agora e quanto falta em cada uma.

`seconds_left` é o número que ele existe para produzir, e ele decide mais do
que parece: escolhe o balde de calibração, e o M2 mediu erro de **0,008** na
faixa 240–120 s contra **0,240** acima de 240 s. Trinta vezes. Um
`seconds_left` deslocado não degrada a decisão — toma a decisão na faixa
errada.

Daí `duracao_do_slug` ter saído do backtest para `markets/discovery.py`: as
duas pontas passaram a usar **a mesma função**, e há um teste que compara as
identidades. Com duas cópias, uma divergência entre SHADOW e backtest
pareceria diferença de mercado quando seria diferença de aritmética — e é
justamente essa comparação que justifica o SHADOW existir.

Falha fechada em quatro casos, cada um com nome próprio em `descartes`:
`nao_operavel`, `sem_fechamento_legivel`, `sem_par_de_tokens` e `ja_fechada`.
O contador responde a pergunta que importa quando o bot não opera: *ele não
achou janela, ou achou e jogou fora?*

Segunda peça pronta: **`live/livros.py`** — o livro de cada token, ao vivo.

Fino de propósito: reusa `OrderBook`, a MESMA classe com que o critério 1.5
mediu os 87,8 USDC. Se o shadow medisse profundidade de outro jeito, a
comparação entre os dois não diria nada sobre capacidade.

Carrega duas defesas que o M2 pagou caro para aprender:

**Silêncio é por TOKEN, não por feed.** É a lição do M2.7/M2.10 no RTDS —
tópico mudo com a conexão viva. O feed do CLOB pode estar impecável enquanto o
livro de um token não recebe nada há minutos, e o portão `feed_parado` olha o
feed, não o token. `resumo()["mudos"]` é o número que nenhum outro alarme daria.

**Delta sem snapshot é contado, não engolido.** A gravação de 20 h mediu
187.452 dessas observações. Aplicá-las a um livro vazio inventaria
profundidade; ignorá-las em silêncio esconderia que o livro está incompleto.

Uma escolha registrada: `livro()` devolve o **objeto vivo**, não uma cópia — o
próximo delta o reescreve embaixo de quem o guardar. Clonar a cada consulta
custaria uma cópia por tick por token no caminho quente, para proteger um uso
que a decisão não faz. Está no docstring e há dois testes travando o
comportamento, incluindo o de `.clone()` para quem precisar congelar.

Terceira peça pronta: **`live/precos.py`** — TWAP corrente, volatilidade e a
âncora de cada janela.

**A âncora ganha ao vivo um jeito de faltar que o backtest não tinha.** O M2 a
fixou em τ=0 — valor do stream no instante da abertura, 0,9984 sobre 640
janelas. Mas se o processo subiu às 12:03 e a janela abriu às 12:00, o valor de
12:00 não existe em lugar nenhum: a série começa quando o bot começa. Usar a
amostra mais antiga disponível seria inventar a âncora e errar a janela inteira
em silêncio — exatamente o que `ancora_verificada` recusa fazer.

**Consequência operacional que precisa ser esperada:** o bot recém-iniciado
**não opera nada por até uma janela inteira** — a de 4 h inclusive. Não é
defeito, é a âncora sendo honesta. `sem_ancora` separa os dois diagnósticos:
`serie_nao_alcanca_a_abertura` é normal ao subir; `lacuna_no_instante_da_abertura`
persistente aponta para o feed.

A busca é a **mesma** do M2: `SerieE18AoVivo` compõe `StreamE18` em vez de
reimplementar `em()`, e há um teste comparando as duas respostas. Uma segunda
cópia dessa busca seria a forma mais silenciosa possível de o SHADOW e o
backtest discordarem sobre a âncora.

Âncora resolvida fica **fixada**: a abertura é um instante, e reler a série
depois daria outro valor conforme os pontos velhos são podados.

Quarta e última peça: **`live/motor.py`** — o laço.

O que sobra de novo nele é só a **orquestração**. Cada etapa reusa o que o
backtest usa, e essa é a regra que faz o SHADOW valer alguma coisa:

| Etapa | Compartilhado |
|---|---|
| duração da janela | `markets.discovery.duracao_do_slug` |
| âncora | `analysis.anchor_sweep.StreamE18.em` |
| probabilidade | `engine.decisao.estimar_prob_up` (extraído do `BacktestRunner`) |
| edge | `backtest.runner.edge_liquido` |
| livro e profundidade | `backtest.book.OrderBook` |
| portões | `risk.PortaoDeRisco.avaliar_risco` |

Quando falta uma peça a resposta é sempre a mesma — não opera, e conta o
motivo. `pulos` responde à pergunta operacional que mais vai ser feita: *o bot
está vivo e não opera, por quê?*

```
sem_ancora                  esperado logo após subir
volatilidade_nao_calibrada  some depois de 20 retornos
sem_livro_confiavel         token mudo, não mercado parado
fora_da_faixa_de_tempo      o gatilho chegando cedo — foi o BUG 2 do M2.6
edge_abaixo_do_threshold    só este fala sobre a BORDA
```

O motor é **síncrono e sem I/O**: dá para simular seis horas de mercado num
teste sem esperar seis horas e sem fingir rede.

Três defeitos apareceram ao escrevê-lo, e valem registro. Eu tinha fixado
`JOGO_TWAP` no motor — janela horária seria estimada com o modelo errado, e os
dois jogos são fisicamente diferentes (API_NOTES §13.4). `JanelaAoVivo` passou
a carregar o jogo. `feeds_saudaveis` estava chumbado em `True` e virou
parâmetro do `tick()` — o motor não decide sobre saúde de feed, ele repassa e o
portão recusa. E o `Executor` não declarava `portao`, que o laço precisa para
dar baixa na exposição.

**O ciclo está fechado.** O que falta agora é o cliente de ordens (3.2), que
exige credencial, e a trava tripla do LIVE (3.4).

### 3.3 fechou — o SHADOW ensaia o caminho inteiro

O backtest diz o que teria acontecido sobre gravação. O SHADOW diz o que
teria acontecido **ao vivo**: feed real no tempo real, decisão com a latência
real, livro no estado em que estava. A diferença entre os dois é a única
medida honesta de quanto do resultado do backtest é artefato de olhar o
passado com calma.

**A regra que o faz valer: mesmo caminho.** Executor é uma interface com duas
implementações que divergem só no último passo — uma escreveria na rede, a
outra escreve num arquivo. Os portões de risco rodam iguais, e a exposição é
contabilizada, senão os tetos por janela nunca seriam exercitados.

O portão de **modo** é a única exceção, e de propósito: ele existe para
impedir envio, e no shadow não há envio para impedir. Rodá-lo faria toda
intenção sair como `modo_nao_opera` e o diário perderia justamente o que
justifica o ensaio — *qual portão estaria segurando se o modo fosse LIVE*. Daí
`avaliar_risco()` existir separado de `avaliar()`; quem envia chama a segunda.

**Pedir LIVE falha alto**, com `NotImplementedError`. Cair para SHADOW em
silêncio seria a falha mais cara possível: o operador acredita que está
operando, o dinheiro não se move, e a descoberta vem quando alguém for
conferir o saldo.

**O que o SHADOW não prova:** que a ordem seria preenchida. Ninguém do outro
lado sabe que ela existe — não há fila, não há concorrência pelo nível, o
mercado não reage. O diário guarda `melhor_bid`, `melhor_ask` e
`profundidade_no_topo` do instante para que essa conta possa ser feita depois.
Ela é uma **conta**, não uma observação.

`resumo()["por_motivo"]` é a parte acionável: um shadow que roda a noite
inteira com zero aprovadas não é falta de oportunidade, é um portão fechado —
e ali está o nome dele.

---

## Bloco 4 — As três condições para ligar o LIVE

Definidas na seção 8 do prompt do projeto. Sem atalho.

| # | Condição | Estado |
|---|---|---|
| 4.1 | Recorder ≥ 72 h + backtest líquido positivo com latência realista | ❌ **MEDIDO E REPROVADO em 2026-09-12** — o marcador ficou ⬜ por uma hora depois de o item fechar, porque eu escrevi o veredito no corpo da linha e não troquei o símbolo; quem batesse o olho no quadro leria 'em aberto' sobre um item decidido. É a Regra 1 aplicada ao próprio quadro. Histórico: **era ⬜ — e a estratégia que o backtest teria de aprovar não existe em código.** O taker está medido e reprovado (1.1/1.4/1.5); a rota que resta é a maker, e o motor dela **não começou** — ver 4.0 abaixo. **O critério ganhou forma avaliável em 2026-09-06:** o relatório passou a trazer `rota_maker.limite_pessimista` ao lado da conta aberta, com `fecha_no_pior_caso` — positivo ali fecha o líquido **sem depender de posição na fila**, que é o que travava o 1.6. **Bloqueio de dado LEVANTADO em 2026-09-08:** `colima` + `docker` instalados, imagem construída (5.2 ✅) e a **gravação de 72 h em curso** — container `pulsearb-rec-72h`, `--restart unless-stopped`, gravando em `~/pulsearb-gravacao`, prevista para 12/09. ⚠️ **O que ela vai medir já é sabido, e é preciso dizer:** ela grava os mercados updown, e as 24 h de SHADOW de 08/09 mostraram que eles não têm pool de reward. O critério do 4.1 pede backtest *líquido positivo*, e com rewards zero o líquido é negativo por construção. A gravação fecha o critério com a evidência que ele exige — mas o veredito provável é **reprovação da rota maker nestes mercados**, não aprovação. A decisão que isso abre (aceitar o fim da rota, ou adaptar a estratégia a mercados de horizonte longo, onde o pool está) é de direção do projeto, e fica em aberto até a gravação fechar. **FECHADO EM 2026-09-12 — ❌, MEDIDO E REPROVADO.** A gravação rodou de 09/09 02:19 a 12/09 02:19 UTC e o backtest completo saiu em `relatorios/M2_72H_20260912_ok.json`. **A METADE DO CRITÉRIO QUE ERA NOSSA PASSOU:** 72 arquivos, **287.745.263 registros, 0 linhas corrompidas, `arquivos_ilegiveis: []`**, cobertura do stream **99,8% nos oito ativos**, 2.536 janelas conhecidas e 2.460 com resolução. O recorder de ≥72 h existe e é íntegro — isso é fato, não promessa. **A OUTRA METADE REPROVOU:** líquido **−195,2525 USDC a 300 ms** e **−199,1752 a 600 ms**, com latência realista. Positivo era a exigência; saiu negativo nos dois cenários. **GANHO COLATERAL QUE VALE MAIS QUE O VEREDITO: a âncora ficou provada em escala.** τ=0 explica **0,9996 de 2.310 janelas elegíveis**, com **1 discordante**, e `concentrada: False` — as janelas se espalham pelos quatro quartis da gravação. Antes eram 88 janelas de uma fatia; agora são 2.310 cobrindo três dias. Isso deixou de ser indício e virou fato estabelecido, e é o alicerce de todo o resto. **O QUE NÃO SE FECHA COM MAIS GRAVAÇÃO:** o **1.5**. Nenhuma das quatro durações chega aos 200 USDC de profundidade (192,7 / 106,1 / 52,8 / 28,0), agora medido sobre as 24 horas do dia. É teto de CAPACIDADE — estratégia nenhuma o levanta. **O QUE SOBRA COMO PISTA, e só como pista:** a banda **240-120s** deu **+12,6942 USDC em 1.943 trades com acerto 0,6897**. Ver 1.1 para as três ressalvas que impedem de tratar isso como resultado — a margem de 0,196%, a escolha in-sample da banda, e o `<30s` que é ruído com cara de lucro. **DEFEITO NOVO, ACHADO DE GRAÇA:** `deriva.veredito` acusa **p50 do offset variando 7.195,5 ms entre as horas**, com p99 de 1.862 ms e três horas acima de 3,5 s — *MEDIANA MOVENDO: suspeita de DERIVA de relógio (NTP ausente ou quebrado)* na máquina de gravação. Não contamina o que está acima, porque a âncora usa carimbo do SERVIDOR. **CORRIGIDO NA MESMA SESSÃO, e a correção é minha:** li o veredito automático e ignorei a ressalva ao lado dele, que diz por escrito que o offset *inclui latência de rede, então é TETO do erro de relógio, não o erro em si*. **NÃO é deriva de relógio.** A mediana das 72 horas é **110,4 ms** e apenas **6 horas de 72** passam de 1 s; `sntp time.apple.com` mede o relógio local a **143 ms**, coerente com a mediana. NTP quebrado produz deriva MONOTÔNICA em todas as horas — aqui são 66 horas limpas e 6 picos. **E os picos têm dono:** 11/09 22:00 UTC (=18:00 local) é o meu ensaio de 3 h; 12/09 01:00 UTC (=21:00 local) é o backtest de 72 h morto às 21:14 e relançado às 21:15; 11/09 20:00 e 21:00 UTC são a suíte completa, a mutação e o ruff. `chegada_local` é carimbado quando o evento é PROCESSADO, então máquina sob carga atrasa o carimbo. **O conserto não é NTP — é não rodar análise pesada na máquina que grava**, e isso já está no RUNBOOK como motivo de a VPS existir. Item ENCERRADO, não aberto **A PISTA FOI TESTADA EM 7 DIAS INDEPENDENTES E REPROVOU (2026-09-13).** A banda `240-120s` tinha dado **+12,6942 USDC em 1.943 trades** nas 72 h, e eu a publiquei com a ressalva de que fora escolhida DEPOIS de ver o resultado. Critério registrado ANTES do teste: *5 ou mais dias com edge → sobrevive; 3 ou 4 → moeda; 2 ou menos → era ruído*. **Resultado: 2 de 7, e a soma dá −2,65 USDC.** O +12,69 era ruído in-sample. **NÃO adotei a `120-60s`**, que deu 4/7 e +72,01 — pelo critério pré-registrado 4/7 é moeda, ela não era a banda sob teste (nas 72 h deu −18,54, marcada `nao`), e adotá-la agora seria repetir o erro in-sample um nível mais fundo. **O ACHADO QUE VALE MAIS QUE O VEREDITO: a variância é do DIA, não da banda.** Em 23/08 o preditor ganha em quase toda faixa (+143,92 / +91,02 / +40,67); em 21/08 perde em quase toda (−207,31 / −68,90 / +4,38). Os +72 da `120-60s` são dois dias bons carregando cinco medianos. As bandas não separam sinal — os dias separam, e isso é o oposto de um edge explorável: não há como saber de antemão em que dia se está. **Desenho da medição:** um dia por vez, sete relatórios (`relatorios/DIA_*.json`). A corrida única sobre os 73 GB **não cabia** — a passada 2 crescia 0,026 GiB por milhão de registros e projetava ~20 GiB numa máquina de 8 GB; por dia o pico ficou em **2,35 GiB**. E sete tabelas respondem *em quantos dias a banda foi positiva*, que é replicação; um agregado só responderia *a soma foi positiva*, que não distingue sinal de um dia bom. **DEFEITO DE GRAVAÇÃO ACHADO DE GRAÇA:** o dia 18/08 saiu com **732 janelas conhecidas e ZERO com resolução** — `pulsearb-20260818-0000.jsonl.gz` e `-0700` estão corrompidos (*invalid literal/length/distance code*, *invalid block type*). Não muda o veredito, que se apoia em 6 dias, mas parte daquela gravação está perdida e ninguém sabia **E EM 2026-09-18, A VARREDURA COMPLETA MOSTROU QUE NÃO ERAM DOIS ARQUIVOS, ERAM ONZE — E QUE OS RELATÓRIOS JÁ DIZIAM ISSO.** `gzip -t` sobre a pasta inteira acusou 11 arquivos corrompidos, dos quais **9 nunca tinham sido notados**: 18/08 `-0000`, `-0700` e **`-1200`**; 19/08 `-1100`; 20/08 `-2300`; 22/08 `-2100`, `-2100-002` e `-2200`; 23/08 `-0100` e `-0100-002`; 25/08 `-0500`. **E o mecanismo NÃO falhou:** `reader.arquivos_ilegiveis` contou cada um, o backtest escreveu a contagem em `gravacao.arquivos_ilegiveis` de cada `DIA_*.json`, e os números batem exatamente com a varredura — 3, 1, 1, 0, 3, 2, 0 para 18 a 24/08. **O defeito é de LEITURA, e é o mesmo do item 1.6:** o dado correto estava no JSON e a conclusão foi tirada sem o consultar. **O que isso custou, medido:** a taxa de resolução (`com_resolucao/conhecidas`) fica entre **0,888 e 0,916** nos dias sãos — e **19/08 deu 164 de 670, ou 0,2448**. Três em cada quatro janelas daquele dia não têm resolução, e ele entrou no teste de sete dias como se fosse um dia normal. **O que NÃO muda:** a reprovação da banda `240-120s` (2 de 7). Hora corrompida produz MENOS trades, nunca trades inventados, então dado degradado enviesa para o lado da reprovação — não se pode fabricar um positivo por falta de dado. **O que fica ressalvado:** a afirmação *'a variância é do DIA, não da banda'* cita números por dia, e os de 19/08 vieram de um quarto do dia. A ressalva não a derruba; apenas diz que ela foi feita sem saber disso. |
<<<<<<< HEAD
| **4.0** | **Motor MAKER — a rota que sobrou** | 🟡 **duas peças de quatro, em 2026-08-31.** ✅ **(a) onde cotar:** `live/cotacao.py` escolhe `distancia_ticks` e tamanho pelo líquido `rewards − markout`, usando a **mesma** `score_de_nivel` do backtest; publica as parcelas separadas porque uma é estimativa com hipótese de fila e a outra é medida (1.7); 43 testes. ✅ **(b) `orderType` configurável:** `ClienteDeOrdens(tipo_de_ordem="GTC")`, com tipo desconhecido falhando na **construção** e não no envio (`[VERIFICADO]` §4.1); default segue FOK. 🟡 **(c) repousar:** `live/repouso.py` decide *mexer ou deixar* com **histerese dupla** — piso de ganho (0,50 USDC) e tempo mínimo repousada (30 s) —, porque reposicionar custa a fila e o livro pisca; cotação que **deixa de pontuar** vence as duas travas, já que ficar seria pagar risco de execução por zero reward. Toda decisão sai com **motivo nomeado**, como no `risk/gates.py`. 17 testes. **O I/O chegou em 2026-09-06** (#81): `execution/cliente.py` ganhou `cancelar` (`DELETE /order`, §4.4) e `listar_ordens_abertas` (`GET /data/orders`, §4.5, leitura **fail-closed** — erro ao ler levanta, nunca vira livro limpo), e `live/execucao_maker.py` (15 testes; 22 desde 2026-09-14, com as duas pernas) traduz a `Decisao` em chamadas do cliente. A trava que dá nome ao módulo: reposicionar é cancelar E DEPOIS enviar, e **cancelamento incerto PARA a sequência** — mandar a nova por cima da antiga que talvez repouse seria posição dupla entre dois makers (verificado por mutação). `reconciliar` separa **órfãs** (no servidor, não esperadas — o envio que ficou INCERTA e afinal entrou) de **fantasmas**. **Dois pré-requisitos do laço fechados:** (1) `execution/cliente_sombra.py` (`ClienteSombraDeOrdens`, 10 testes, 2026-09-07) — sem ele o laço mandaria ordem DE VERDADE em SHADOW, violando a invariante do topo do `live/shadow.py`; não abre socket, não assina, não recebe credencial, e todo id sai com prefixo `sombra-`. `aplicar_decisao` e `reconciliar` rodam contra ele SEM alteração — a prova do mesmo caminho. Não simula preenchimento de propósito: fingir exigiria a fila do 1.6. (2) Os **parâmetros de reward chegam ao caminho ao vivo** (10 testes, 2026-09-07): a leitura saiu de dentro do `recorder` para `markets/rewards_da_gamma.py` e os DOIS lados usam as MESMAS funções — a leitura é ambígua de propósito (três nomes para a lista, seis para a taxa), e duas cópias divergiriam na primeira grafia nova, aparecendo como *'o SHADOW achou pool onde o backtest não achou'*. `JanelaAoVivo` ganhou `reward_daily_rate`, `reward_min_size` e `reward_max_spread` (em FRAÇÃO, convertido no leitor compartilhado). Os três andam juntos ou nenhum; sem pool saem `None`, não zero. ✅ **O LAÇO fechou em 2026-09-07** — `live/laco_maker.py` (7 testes) chama cotação → repouso → execução por janela aberta, e o `ProcessoShadow` roda `laco_de_cotacao` como tarefa própria. **Cadência de 15 s, não 1 s**: a pergunta do maker é *vale trocar?*, e a histerese do repouso já tem piso de 30 s repousada — consultar a cada segundo só produziria `repousada_ha_pouco_tempo` em série. Três travas que só aparecem na composição, verificadas por mutação: **janela que fecha leva a cotação com ela** (senão 24 h viram dezenas de órfãs num mercado que ninguém acompanha); **livro indisponível NÃO cancela** (sair por falta de dado NOSSO perderia a fila de graça, e o livro volta no passo seguinte); e **MANTER não custa ida à rede** (senão a histerese não economizaria nada). Janela sem pool não recebe cotação. O cliente é o **sombra, sempre** — trocá-lo pelo real é o que faria a rota cotar de verdade, e é decisão de LIVE que a autorização recusa. O laço **não derruba a rodada**: defeito nele sai no log e o taker, que é o caminho medido, segue. `maker` sai no relato de 60 s com `motivos` nomeados. **4.0(c) COMPLETO.** ✅ **O portão entrou em 2026-09-07**, no mesmo dia em que o buraco foi achado: toda cotação passa por `avaliar_risco` — os MESMOS portões do taker, sem o de modo (que faria tudo sair como `modo_nao_opera` e o diário perderia qual trava seguraria em LIVE, como no `ExecutorSombra`). Três propriedades, verificadas por mutação: **sem portão NÃO cota** (`sem_portao`, falha fechada — cotar 'porque ninguém passou trava' é o oposto do que a trava serve); **o portão barra ENTRAR, nunca SAIR** (um kill switch que impedisse cancelar prenderia a cotação no livro exatamente quando alguém puxou a chave para tirá-la); e vale para a **exposição que já existe**, não só para a nova — checar só no REPOSICIONAR deixava uma cotação repousando nunca ser reavaliada, e um disjuntor que armasse no meio da rodada não a tirava do livro. Esse último buraco foi achado por um teste que eu escrevi esperando que passasse. 12 testes — **64 desde 2026-09-14**, quando a rodada r4 mostrou o `montar` cotando de `meio=0.5` fixo em vez do `livro.mid` e contando dois lados enquanto colocava um; a cotação passou a ter as duas pernas, bid no Up e bid no Down (ver a nota da rodada 4 no 1.12; `execucao_maker` foi a 22 testes). ⬜ **(d) posição na fila:** o WS agregado não mostra, e é dela que dependiam os 3 termos que travavam o 1.6. **Deixou de ser o bloqueio em 2026-09-06** (#82): `conta_pessimista_do_maker` troca *estimar a fila* por **limitar por baixo** — rewards não dependem dela (§15.3), então só o custo depende, e ele entra no máximo. **E em 2026-09-08 a fila deixou de importar por outra razão, mais dura:** a rodada de 24 h (ciclo 1,0, zero sono) fez **44.430 avaliações maker** e nenhuma cotação repousou — motivo ÚNICO `sem_pool_de_reward`, em todas as janelas, o dia inteiro. Sem pool, `líquido = rewards − markout` fica negativo por construção, e nenhuma hipótese de fila muda isso. **A varredura do mesmo dia mostra que o programa NÃO acabou** — mas todos os mercados com pool são de horizonte longo (eleições 2026/2028, "antes de 2027", campeonatos), e **nenhum** de janela curta. ⚠️ **O número publicado aqui em 08/09 — "940 (37,6%), 6.766 USDC/dia" — estava errado por 27× e foi corrigido em 2026-09-13:** aquilo era amostra de 2.500 mercados da Gamma; a lista autoritativa do CLOB (`GET /rewards/markets/current`, paginada) traz **18.384 mercados com pool, 185.520 USDC/dia**. A conclusão qualitativa sobrevive, a escala não. O reward existe; ele não está onde este bot opera. Descartadas as duas alternativas antes de concluir: o leitor acha pool em 6 de 20 mercados quaisquer (não é chave errada), e `/markets/slug/` e `keyset` concordam que `clobRewards` não vem nesses mercados (não é rota de busca). ✅ **(e) recolher quando o livro anda contra (2026-09-14, noite):** a regra dos makers dos leaderboards (regime EVENT do `poly-maker`, `docs/OUTROS_BOTS.md` §6 item 1), que o *maker_de_pares* mediu na gravação como quase toda a perda — **−583,54 USDC em 4 h ficando, −31,87 recolhendo**. `LacoMaker.recolher_se_o_livro_andou` roda no sono de 1 s entre passadas: melhor bid abaixo da cotação → as duas pernas saem com motivo `livro_andou_contra`; livro indisponível NÃO recolhe (perder a fila por falta de dado nosso). Em LIVE a nossa ordem é o topo, então o gatilho vira *sozinho no nível* (tamanho no melhor bid ≤ o nosso). Knob `maker_recolhe_quando_o_livro_anda` (padrão desligado; a unit da VPS liga). A revisão do Codex no PR #126 pegou três coisas: cotação que já melhora o topo ao nascer (livro largo — os pools) seria recolhida no primeiro segundo sem o mercado ter andado, então a referência é `min(preço, melhor bid na primeira observação)` e recolhe só se o mercado cair dela; lado de bids vazio recolhe (é o caso mais forte, e é o que a simulação medida faz); e os prints do intervalo são conferidos ANTES de sair, senão a saída apagava o cursor da caixa e subcontava a própria métrica. E na segunda rodada: a referência nasce na COLOCAÇÃO, do livro que colocou a cotação (mercado que anda no primeiro segundo é recolhido no primeiro poll), e `OSError` do diário no recolher segue o mesmo caminho fatal da passada (`io_do_diario_maker`); e o último intervalo de reward é acertado pela caixa ANTES de sair, senão recolher muito empurraria o reward para baixo por construção. Na quarta rodada: quando só o livro do Down dispara a saída e o do Up não está à mão, a caixa acerta no ESPELHO do livro do Down (bid = 1 − ask, o mesmo espelho da colocação) em vez de perder o intervalo; e a referência do recolher sai com a cotação por qualquer porta (`janela_fechou`, `pool_sumiu`, recotação), não só pelo recolher — antes cada uma deixava uma chave morta para sempre. Na quinta: em LIVE, cotação que melhora o topo (os pools) É o melhor bid do livro desde que nasce, e comparar o topo com a referência nunca dispararia — o que se compara é o melhor bid EXTERNO (o livro sem o nosso nível), e a referência da recotação desconta a nossa ordem anterior. Na sexta: tokens e parâmetros de janela que nunca cotou saem quando a janela some (a limpeza da saída da cotação não os alcançava). Na sétima: a referência do Down vem do livro do DOWN, não do espelho do Up — os dois livros não são complementares, e a referência sintética contra o livro real recolhia par parado no primeiro poll; perna sem livro na colocação fica sem referência e é marcada na primeira observação. Na oitava: Down sem bid nenhum não recebe o par com a regra ligada (`perna_down_sem_bids`) — o recolher o tiraria no segundo seguinte e a passada o poria de volta, sem fim; e a caixa acerta o reward NO INSTANTE do print, com as pernas de antes dele, no mesmo `conferir_prints` da passada e do recolher — senão um print no meio de 10 s fazia os 10 s parecerem de um lado só (o relógio da caixa nunca anda para trás). Na nona: os prints das DUAS pernas entram em ordem de tempo, não perna a perna — um print do Down anterior ao do Up era visto depois dele e descartado, e o Down parecia aberto até o print do Up. 21 testes. **🟡 falta medir em SHADOW se o sono de 1 s basta** — a medição foi com 100 ms; o relato tem de mostrar `livro_andou_contra` e a caixa tem de mostrar menos execuções atravessadas por hora que a r9 sem a regra. 🟡 **(f) âncora do MICROPRICE (2026-09-14, noite):** o `maker_de_pares` mediu sobre a gravação de 13/09 que cotar a partir do microprice é **o que vira o sinal** do termo determinístico — de −34,67 (juntando ao topo, lote 20) para **+30,68** a 1 tick abaixo dele, com a soma paga do par caindo de 1,015 para **0,995**; com lote 100, +137,84; a 3 ticks o fill morre (`docs/OUTROS_BOTS.md` §6, item 6). É a peça central do `poly-maker` (`quoting.py:37-39`) e era a única do estudo com efeito medido que ainda não tinha porta para o caminho ao vivo. **O microprice era função PRIVADA de um script** (`_microprice` em `scripts/maker_de_pares.py`): quem mediu e quem opera não podiam usar a mesma — virou `OrderBook.microprice`, e o script medido passou a chamá-la (mesmo caminho). No laço ele entra como **âncora**, não como novo meio: `AncoraDoMicroprice` é um TETO do bid e um PISO do ask, e **só aperta** — nunca puxa a cotação para mais perto do meio do que a distância já escolhida. Ela mora dentro de `Cotacao.preco`, e não em quem envia, porque o preço AVALIADO e o ENVIADO têm de ser o mesmo número: a conta vê a perna Down como o ask do livro do Up, o laço a envia como bid no livro do Down com a âncora espelhada, e um teste parametrizado trava que os dois arredondamentos caem no mesmo tick — é o modo de falha do §6.1b, que não levanta erro nenhum. Cada perna é ancorada pelo microprice do LIVRO DELA (a lição da rodada 7 do #126: os dois livros não são complementares). Falha fechada: com a âncora ligada e o microprice de alguma perna indisponível, NÃO se cota (`sem_microprice`) — e não se cancela o que já repousa, porque é falta de dado nosso. `ticks` negativo levanta na CONSTRUÇÃO. Knob `maker_ticks_abaixo_do_microprice` (padrão `None` = desligado). 13 testes, cinco mutações verificadas (ignorar a âncora no envio, não espelhar na perna Down, ignorar no score, afrouxar em vez de apertar, e cotar sem microprice). **A âncora empurrou o `_passo_da_janela` para complexidade cognitiva 31** (o SonarCloud reprova acima de 15, e foi o que reprovou o portão de qualidade do #127 — os achados do Sonar são ilegíveis da sessão da nuvem, então a métrica foi medida localmente, com o pacote `cognitive_complexity`, antes e depois). Os portões viraram métodos nomeados — `_dados_da_passada` junta os quatro que têm o MESMO tratamento (livro indisponível, janela sem tempo, livro sem meio, sem microprice: nenhum cancela o que repousa), e `_ancora_do_microprice` e `_perna_down_sem_bids` ficaram cada um no seu —, e a função caiu para **abaixo de 15**, menos do que os 25 que já tinha ANTES deste PR. **E a reestruturação achou um defeito de métrica:** `portao:*` e `perna_down_sem_bids` eram contados DUAS vezes quando havia cotação repousando, porque `_sair` já conta o motivo com que sai — o relato de 60 s inflava a contagem de cada trava justamente quando ela fazia o que mais importa: tirar do livro o que já estava lá. `_recusar` conta uma vez só; verificado por mutação. **A revisão do Codex no #127 pegou mais duas, e a primeira era grave:** a âncora valia só na COLOCAÇÃO. O microprice anda quando o TAMANHO no topo muda — sem o meio se mexer e sem a distância escolhida mudar —, e aí a ordem que repousa fica acima do teto novo, CONTINUA pontuando (logo `atual_nao_pontua_mais` não a pega) e a candidata ancorada pontua MENOS que ela, então o piso de ganho jamais aprovaria a troca: ela ficaria ali até morrer. `acima_do_teto_do_microprice` é trava própria e vence a histerese, pela mesma razão de ordem que o `atual_nao_pontua_mais` vence — deixá-la para o piso de ganho seria deixá-la nunca. A segunda: `ticks` negativo levantava só na primeira passada com livro bom, e o laço maker roda como tarefa própria (defeito nele sai no log sem derrubar a rodada), então um valor errado no ambiente mataria em silêncio a rota inteira por 14 dias com o processo vivo — a validação subiu para o `Settings` (`ge=0`), que recusa no carregamento. Três mutações a mais verificadas. **Na segunda rodada, o inverso da mesma moeda:** quando o teto AFROUXA (o topo do livro engorda de novo), a ordem não está acima dele — não há risco a corrigir — mas a MESMA `Cotacao` volta a caber mais perto do meio, e o atalho do `estavel` a dava como igual: uma catraca que só andava para longe do meio, pontuando menos a cada aperto. Eu tinha argumentado que o caso não era alcançável com a grade (1..5) e estava ERRADO — uma busca sobre o próprio código achou 186 combinações de topo que o produzem. `_a_ancora_mudou_o_preco` só ABRE o caminho: aqui não há risco, só oportunidade, então quem decide são a histerese e o piso de ganho (com pool de 100 USDC/dia o piso segura, e é a política certa; com 1.000 a ordem volta). E a receita da unit para a rodada da âncora passou a trocar UMA regra pela outra — ligar só a linha nova deixaria as duas ligadas, que é exatamente o que a nota ao lado proíbe. **Na terceira, uma incoerência minha:** eu tinha escrito o ramo que CANCELA por teto sem candidata e o tornado inalcançável, porque montava o teto só quando havia candidata que pontuasse. Livro que ALARGA com o meio parado tira a grade ancorada inteira da faixa de reward — e é justamente aí que o teto mais importa, porque a que repousa continua pontuando. O teto passa a existir sempre que a regra está ligada; só o preço da candidata é opcional. **Na quarta, a falha fechada estava fechada demais:** `sem_microprice` abortava a passada INTEIRA, e com isso a caixa deixava de contar o repouso (truncando em silêncio a medida que a rodada existe para fazer) e o PORTÃO deixava de rodar — um disjuntor que armasse durante a falta não tirava a ordem do livro, que é exatamente a propriedade que o 4.0 registra ter custado caro para achar. Agora faltar o microprice impede COTAR, e só. **E uma revisão adversarial minha, depois que o Codex esgotou o limite de uso, achou o pior defeito de todos — e ele era MEU, da rodada 1:** eu tinha feito a trava do teto vencer a histerese, mas o teto é `microprice − folga`, e o microprice anda com o MEIO. Com `distancia_ticks=1` e `ticks=1` o preço colocado COINCIDE com o teto, então qualquer passo de 1 tick do meio punha uma das pernas acima dele. Medido na cadência real de 15 s, com os tamanhos do topo congelados: **39 trocas em 40 passadas, todas antes do repouso mínimo de 30 s**, contra 2,05 com a regra desligada. Reposicionar custa a fila, e nem a simulação do `maker_de_pares` nem o SHADOW enxergam esse custo — seria pagá-lo às cegas, e a trava chegou a reposicionar com ganho estimado NEGATIVO. O conserto separa o que eu tinha juntado: o **limite duro** é o microprice (repousar acima dele é ser a opção grátis, e isso vence a histerese), a **folga de `ticks`** é preferência de colocação e não é emergência. Remedido: **9,42 trocas, zero antes dos 30 s**. Um teste trava a regressão (40 passadas com o meio passeando, zero trocas antes do repouso mínimo). A mesma revisão pegou mais três: `acima_do_microprice` não estava em `MOTIVOS` (regra do projeto — toda recusa tem nome constante); `sem_microprice` inflava `sem_candidata_que_pontue`, que é o contador que distingue *o bot está travado* de *o bot não achou trade*; e a exclusividade entre os dois knobs só existia em comentário — agora o relato de 60 s publica `maker.regras`, então uma rodada confundida se denuncia na primeira hora em vez de no fim dos 14 dias. **Fica dito, para a comparação entre rodadas:** com o knob DESLIGADO as ações e os preços são idênticos aos de antes, mas `portao:*` e `perna_down_sem_bids` passaram a contar UMA vez em vez de duas quando havia cotação repousando — rodada de antes e rodada de depois não são comparáveis nesses dois contadores. **🟡 falta medir, e a medida tem de ser SEPARADA da do (e):** ligar as duas na mesma rodada confunde os efeitos e nenhuma das duas fica medida. A unit da VPS **não** liga esta — a rodada do recolher está em curso. E o que a medida do §6 autoriza dizer é limitado: ela comparou com *juntar ao topo* nas janelas Up/Down de cripto, não com a nossa grade de distância do meio nos pools, e o termo medido **não incluía rewards** — afastar do meio baixa o score do §15.3. O que se espera ver numa rodada com ela ligada: menos execuções atravessadas por hora e soma paga do par abaixo de 1,00, sem o reward por hora cair mais do que isso compensa. 🟡 **(g) pausa por fill tóxico (2026-09-15):** o regime EVENT do `poly-maker` disparado pelo NOSSO fill, e não pelo salto do livro — quem atravessa a cotação paga acima do nosso preço para entrar AGORA, e normalmente sabe de algo que o livro ainda não mostrou; cotar no minuto seguinte é oferecer a mesma opção de graça outra vez. **Duas medidas independentes apontam para cá.** A simulação (`--grade bots`, hora de fumaça de 13/09, microprice −1 tick a 245 ms): base **+14,24**, com 30 s **+18,42**, com 90 s **−0,52** — noventa segundos matam o fill, e a janela certa é curta. E a r7 do SHADOW, do outro lado: de 12 execuções possíveis, **2 atravessadas dominaram o markout** (−22,85 USDC, −0,7376 ¢/share). `LacoMaker.pausa_apos_fill_toxico_s` tira a cotação com motivo `pausa_por_fill_toxico` e NÃO recoloca enquanto dura; a caixa guarda o instante do último fill atravessado por slug, e esse relógio SOBREVIVE ao `esquecer` justamente porque a pausa existe para valer depois de a cotação sair. Fill **no nível** não pausa: é a fila andando, e pausar nele tiraria a cotação do livro toda vez que ela funcionasse. Knob desligado por padrão, validado no carregamento. 5 testes, 4 mutações verificadas. **A revisão do Codex pegou uma:** a pausa saía ANTES de a caixa fechar o intervalo. Um fill atravessado consome UMA perna; a outra segue no livro ganhando reward de um lado só até a passada seguinte, e `_sair` apaga o relógio — sair antes de fechar jogava fora até uma cadência inteira de reward legítimo, e isso enviesava o experimento CONTRA a própria regra que se quer medir. Agora a pausa vem depois do acerto, na mesma ordem que o `livro_andou_contra` já seguia — as duas saídas passaram a ser o MESMO `_recolher`, com o motivo por parâmetro, porque duas cópias dessa ordem é como as duas divergem. **E a segunda rodada pegou mais três, todos sobre a mesma pergunta — a regra faz o que foi medido?** (1) A pausa esperava o livro para decidir, mas **cancelar não precisa de livro**: uma falta mais longa que a pausa deixaria a cotação exposta o intervalo tóxico inteiro. (2) Os prints só eram vistos na passada de 15 s, então um fill logo depois dela deixava a outra perna exposta quase uma cadência e **a pausa de 30 s virava 15–30 s efetivos** — seria medir outra regra; `recolher_por_fill_toxico` entrou no sono de 1 s, ao lado do recolher, e a decisão de COTAR segue nos 15 s. (3) `ts_ns` é a CHEGADA, e um `last_trade_price` reenviado depois de reassinatura chega agora carregando negócio de minutos atrás: a pausa passou a armar pelo **carimbo do servidor** quando ele existe, o que não inventa limiar nenhum — a execução continua contando, só não arma a pausa. E na terceira rodada o mesmo achado com uma volta a mais: um reenvio RECENTE o bastante para a pausa valer, mas de negócio ANTERIOR à cotação, tiraria do livro uma ordem que aquele negócio não pôde ter executado — ela nem existia. A pausa só arma com negócio dentro da vida da cotação. **E na quarta rodada o achado deixou de ser sobre a pausa e virou sobre a MEDIDA:** o reenvio anterior à cotação não armava mais a pausa, mas seguia CONSUMINDO a perna — era contado como execução nossa de uma ordem que não estava no livro quando aquele negócio aconteceu, e o fill real seguinte era então descartado como perna consumida, justamente no caso em que a pausa deve armar. Print com carimbo do SERVIDOR anterior à cotação passa a ser descartado da conta inteira e contado como `prints_reenviados` no relato — um número que nunca tinha sido publicado. **Isto muda a linha de base da caixa, inclusive com os knobs desligados:** `execucoes_atravessadas` e o markout caem pelo tanto de reenvio que o feed mandar, e o sentido da mudança FAVORECE a rota, o que obriga a dizê-lo alto. A conta velha estava errada — um negócio anterior à cotação não pode tê-la executado —, mas os números de 4.2 medidos antes disto não são comparáveis com os de depois, e `prints_reenviados` é o que diz de quanto foi. **🟡 falta medir**, e agora são TRÊS regras esperando rodada — cada uma precisa da sua, e o relato de 60 s publica `maker.regras` para uma rodada confundida se denunciar na primeira hora. **E o custo das reconexões saiu do código novo em 2026-09-21 (runbook §10.1i), com um número que a rota maker precisa carregar:** 27 reconexões/h, 116,8 s sem livro (3,24% da hora), pior buraco 37,1 s — contra a linha de base de 23,6 s (0,65%) do código antigo. A minha previsão, dita antes da medida, era ~70 s/h: errou 67% para baixo e errou o LUGAR. 86% do custo veio de TRÊS quedas de `clob[updown]`, todas `1013 slow consumer: send buffer full` — o servidor dizendo que o NOSSO cliente não drena o socket, que é a starvation de CPU de 1 vCPU chegando na ponta do WebSocket. As 24 quedas do RTDS custaram 16 s (0,67 s de média, ao lado dos 0,79 s da base), confirmando em produção que o nosso próprio 1012 tem piso zero. **O número que decide a rota não é a média:** durante a rajada de 3,3 min foram 100,7 s cegos em 195,6 s — **51% do tempo sem livro do `updown`**. E a janela de 4 h achou um defeito meu do #177/#178: as nove quedas dormiram todas no teto, inclusive uma que veio depois de 77,1 min de conexão saudável — `pedidos_de_paciencia_seguidos` só zerava numa queda sem piso, e nada media o "seguidos". Consertado com reset por saúde, testes por mutação. **🟡 falta repetir na máquina de 4 vCPU**, porque calibrar o backoff com um número produzido pela CPU que vamos trocar seria medir a máquina, não a política. **E em 2026-09-21 o operador RECUSOU a máquina maior, o que fecha o último caminho que este item tinha (runbook §10.1j).** Sem eufemismo: o 4.2, do jeito que está especificado, **não tem caminho em 1 vCPU** — quatro em paralelo dá `id=0`/`r=4`, o plano B de três janelas reprovou com `%wait` de 0,38% → 16,70% → 35,97%, e uma rodada por vez é recusada pelo próprio §10.1c porque compararia a regra com o mercado em vez de com um controle do mesmo intervalo. **A saída que restou não estava na tabela do §10.1f:** a rodada não é um bloco de tamanho fixo — `PULSEARB_TOP_DE_POOLS_DE_REWARD=60` é o que compra os ~40% de núcleo. Baixá-lo NAS DUAS rodadas da janela mantém a comparação contemporânea que o 4.2 exige, ao preço de o veredito passar a valer para os N melhores pools e não para 60 — e de 14 dias talvez não carregarem o mesmo peso estatístico, conta que sai dos diários r4–r8 no Mac e **não está feita**. **Nem está medido que 20 pools cabem:** a relação entre pools e CPU pode não ser linear. A medida de `%wait` que decide custa 20 minutos e está escrita no §10.1j, com a régua do §10.1f. **Enquanto ela não rodar, este item não anda**, e o que dá para fechar sem ela é só uma `base` sozinha por 14 dias — que mede sobrevivência, repouso e fração cotável, e não decide regra nenhuma. **A medida rodou no mesmo dia e REPROVOU: `%wait` 28,59%** (mín 3,80, máx 47,00) com 20 pools, contra 35,97% com 60 — e `%CPU` 38,35% contra os ~40% de antes. A variável pegou, conferido no log (`janelas: 20`), não suposto. **Três vezes menos pools, o mesmo custo: a saída que eu tinha proposto está morta, e teria custado um veredito mais estreito por nada.** O que ela ensinou, porém, muda o alvo: o custo não escala com pools, e `%usr` 36,07% contra `%system` 2,27% diz que é cálculo em Python por EVENTO DE LIVRO, não por mercado, não I/O. E `base` e `pausa` são dois processos que assinam os mesmos tokens e processam o mesmo livro cada um por si — o trabalho caro é feito duas vezes, que é também por que o `id` do `vmstat` nunca via a contenção que o `%wait` via. **A hipótese que sobra** — um processo, uma assinatura, as regras em paralelo dentro dele — sairia de graça em hardware e em escopo, mas é hipótese, e a anterior reprovou; o perfil de 60 s com `py-spy` no §10.1j é que decide, e se ele vier sem topo claro **o 4.2 fica parado nesta máquina**. **E ao ler o código para interpretar o perfil apareceu o que nenhuma medida tinha olhado:** cada rodada carrega a rota taker INTEIRA, e ela não é opt-in — `clob[updown]`, `laco_de_descoberta` e `laco_de_decisao` sobem incondicionalmente; só a rota de pools tem trava. E o `clob[updown]` é, pela medida de 2026-09-14 gravada no próprio código, **54% do tráfego em rajadas de 4 MB/s**, contra 50–120 msg/s dos pools. Isso explica a reprovação dos 20 pools: `TOP_DE_POOLS_DE_REWARD` mexe só em `clob[pools]`, então a medida cortou a metade PEQUENA e deixou a grande intacta — **e derruba a minha leitura de que 'o custo não está no processamento de livro', que não foi testada.** Significa também que as duas rodadas do 4.2 pagam a conexão mais pesada da casa para medir a rota maker, sendo que o taker já está medido e reprovado (1.1/1.4/1.5). A medida que decide não toca em código — a mesma rodada sozinha, com e sem `DESCOBRIR_POOLS_DE_REWARD` — e está no §10.1j com as três leituras escritas antes de rodar. **Ela rodou no mesmo dia, e o número muda o item:** `base` sozinha deu `%CPU` 39,87 com a rota de pools e 37,88 sem ela — **a rota maker inteira custa 1,99 ponto percentual**, 2% de um núcleo. É ela que o 4.2 existe para medir. Os outros ~37,9% são infraestrutura que sobe sem ninguém pedir. **A leitura que eu tinha escrito para esta linha estava errada** — eu previa 'o taker é quase todo o custo', e isso não segue: os 37,9% são taker + RTDS + maquinaria fixa, e a medida não os separa; o que ela prova é que o MAKER é barato. **A aritmética que sai disso:** as quatro rodadas diferem em três escalares e no caminho do registro de risco, e hoje cada uma paga os ~38% por conta própria (4 × ~40% = ~160%, que foi o dimensionamento que pediu 4 vCPU) — mas esses 38% são trabalho IDÊNTICO feito quatro vezes. Num processo só, paga-se uma vez: **estimativa de ~40–46% de um núcleo para as quatro**, que cabe em 1 vCPU com folga. ⚠️ **Os 2% são medidos; os 40–46% são estimativa** e não fecham nada — quem fecha é a medida sobre o processo escrito. **O que isso significa:** o veredito do §10.1f (não cabe em 1 vCPU) continua correto PARA A TOPOLOGIA ATUAL de quatro processos; o que mudou é que a topologia deixou de ser dado do problema e virou escolha. O preço está escrito no §10.1j: é mudança de código, perde-se isolamento entre variantes, e há risco de contaminação cruzada — em troca de uma comparação mais limpa que a de hoje, com as variantes vendo byte a byte a mesma entrada |
=======
| **4.0** | **Motor MAKER — a rota que sobrou** | 🟡 **duas peças de quatro, em 2026-08-31.** ✅ **(a) onde cotar:** `live/cotacao.py` escolhe `distancia_ticks` e tamanho pelo líquido `rewards − markout`, usando a **mesma** `score_de_nivel` do backtest; publica as parcelas separadas porque uma é estimativa com hipótese de fila e a outra é medida (1.7); 43 testes. ✅ **(b) `orderType` configurável:** `ClienteDeOrdens(tipo_de_ordem="GTC")`, com tipo desconhecido falhando na **construção** e não no envio (`[VERIFICADO]` §4.1); default segue FOK. 🟡 **(c) repousar:** `live/repouso.py` decide *mexer ou deixar* com **histerese dupla** — piso de ganho (0,50 USDC) e tempo mínimo repousada (30 s) —, porque reposicionar custa a fila e o livro pisca; cotação que **deixa de pontuar** vence as duas travas, já que ficar seria pagar risco de execução por zero reward. Toda decisão sai com **motivo nomeado**, como no `risk/gates.py`. 17 testes. **O I/O chegou em 2026-09-06** (#81): `execution/cliente.py` ganhou `cancelar` (`DELETE /order`, §4.4) e `listar_ordens_abertas` (`GET /data/orders`, §4.5, leitura **fail-closed** — erro ao ler levanta, nunca vira livro limpo), e `live/execucao_maker.py` (15 testes; 22 desde 2026-09-14, com as duas pernas) traduz a `Decisao` em chamadas do cliente. A trava que dá nome ao módulo: reposicionar é cancelar E DEPOIS enviar, e **cancelamento incerto PARA a sequência** — mandar a nova por cima da antiga que talvez repouse seria posição dupla entre dois makers (verificado por mutação). `reconciliar` separa **órfãs** (no servidor, não esperadas — o envio que ficou INCERTA e afinal entrou) de **fantasmas**. **Dois pré-requisitos do laço fechados:** (1) `execution/cliente_sombra.py` (`ClienteSombraDeOrdens`, 10 testes, 2026-09-07) — sem ele o laço mandaria ordem DE VERDADE em SHADOW, violando a invariante do topo do `live/shadow.py`; não abre socket, não assina, não recebe credencial, e todo id sai com prefixo `sombra-`. `aplicar_decisao` e `reconciliar` rodam contra ele SEM alteração — a prova do mesmo caminho. Não simula preenchimento de propósito: fingir exigiria a fila do 1.6. (2) Os **parâmetros de reward chegam ao caminho ao vivo** (10 testes, 2026-09-07): a leitura saiu de dentro do `recorder` para `markets/rewards_da_gamma.py` e os DOIS lados usam as MESMAS funções — a leitura é ambígua de propósito (três nomes para a lista, seis para a taxa), e duas cópias divergiriam na primeira grafia nova, aparecendo como *'o SHADOW achou pool onde o backtest não achou'*. `JanelaAoVivo` ganhou `reward_daily_rate`, `reward_min_size` e `reward_max_spread` (em FRAÇÃO, convertido no leitor compartilhado). Os três andam juntos ou nenhum; sem pool saem `None`, não zero. ✅ **O LAÇO fechou em 2026-09-07** — `live/laco_maker.py` (7 testes) chama cotação → repouso → execução por janela aberta, e o `ProcessoShadow` roda `laco_de_cotacao` como tarefa própria. **Cadência de 15 s, não 1 s**: a pergunta do maker é *vale trocar?*, e a histerese do repouso já tem piso de 30 s repousada — consultar a cada segundo só produziria `repousada_ha_pouco_tempo` em série. Três travas que só aparecem na composição, verificadas por mutação: **janela que fecha leva a cotação com ela** (senão 24 h viram dezenas de órfãs num mercado que ninguém acompanha); **livro indisponível NÃO cancela** (sair por falta de dado NOSSO perderia a fila de graça, e o livro volta no passo seguinte); e **MANTER não custa ida à rede** (senão a histerese não economizaria nada). Janela sem pool não recebe cotação. O cliente é o **sombra, sempre** — trocá-lo pelo real é o que faria a rota cotar de verdade, e é decisão de LIVE que a autorização recusa. O laço **não derruba a rodada**: defeito nele sai no log e o taker, que é o caminho medido, segue. `maker` sai no relato de 60 s com `motivos` nomeados. **4.0(c) COMPLETO.** ✅ **O portão entrou em 2026-09-07**, no mesmo dia em que o buraco foi achado: toda cotação passa por `avaliar_risco` — os MESMOS portões do taker, sem o de modo (que faria tudo sair como `modo_nao_opera` e o diário perderia qual trava seguraria em LIVE, como no `ExecutorSombra`). Três propriedades, verificadas por mutação: **sem portão NÃO cota** (`sem_portao`, falha fechada — cotar 'porque ninguém passou trava' é o oposto do que a trava serve); **o portão barra ENTRAR, nunca SAIR** (um kill switch que impedisse cancelar prenderia a cotação no livro exatamente quando alguém puxou a chave para tirá-la); e vale para a **exposição que já existe**, não só para a nova — checar só no REPOSICIONAR deixava uma cotação repousando nunca ser reavaliada, e um disjuntor que armasse no meio da rodada não a tirava do livro. Esse último buraco foi achado por um teste que eu escrevi esperando que passasse. 12 testes — **64 desde 2026-09-14**, quando a rodada r4 mostrou o `montar` cotando de `meio=0.5` fixo em vez do `livro.mid` e contando dois lados enquanto colocava um; a cotação passou a ter as duas pernas, bid no Up e bid no Down (ver a nota da rodada 4 no 1.12; `execucao_maker` foi a 22 testes). ⬜ **(d) posição na fila:** o WS agregado não mostra, e é dela que dependiam os 3 termos que travavam o 1.6. **Deixou de ser o bloqueio em 2026-09-06** (#82): `conta_pessimista_do_maker` troca *estimar a fila* por **limitar por baixo** — rewards não dependem dela (§15.3), então só o custo depende, e ele entra no máximo. **E em 2026-09-08 a fila deixou de importar por outra razão, mais dura:** a rodada de 24 h (ciclo 1,0, zero sono) fez **44.430 avaliações maker** e nenhuma cotação repousou — motivo ÚNICO `sem_pool_de_reward`, em todas as janelas, o dia inteiro. Sem pool, `líquido = rewards − markout` fica negativo por construção, e nenhuma hipótese de fila muda isso. **A varredura do mesmo dia mostra que o programa NÃO acabou** — mas todos os mercados com pool são de horizonte longo (eleições 2026/2028, "antes de 2027", campeonatos), e **nenhum** de janela curta. ⚠️ **O número publicado aqui em 08/09 — "940 (37,6%), 6.766 USDC/dia" — estava errado por 27× e foi corrigido em 2026-09-13:** aquilo era amostra de 2.500 mercados da Gamma; a lista autoritativa do CLOB (`GET /rewards/markets/current`, paginada) traz **18.384 mercados com pool, 185.520 USDC/dia**. A conclusão qualitativa sobrevive, a escala não. O reward existe; ele não está onde este bot opera. Descartadas as duas alternativas antes de concluir: o leitor acha pool em 6 de 20 mercados quaisquer (não é chave errada), e `/markets/slug/` e `keyset` concordam que `clobRewards` não vem nesses mercados (não é rota de busca). ✅ **(e) recolher quando o livro anda contra (2026-09-14, noite):** a regra dos makers dos leaderboards (regime EVENT do `poly-maker`, `docs/OUTROS_BOTS.md` §6 item 1), que o *maker_de_pares* mediu na gravação como quase toda a perda — **−583,54 USDC em 4 h ficando, −31,87 recolhendo**. `LacoMaker.recolher_se_o_livro_andou` roda no sono de 1 s entre passadas: melhor bid abaixo da cotação → as duas pernas saem com motivo `livro_andou_contra`; livro indisponível NÃO recolhe (perder a fila por falta de dado nosso). Em LIVE a nossa ordem é o topo, então o gatilho vira *sozinho no nível* (tamanho no melhor bid ≤ o nosso). Knob `maker_recolhe_quando_o_livro_anda` (padrão desligado; a unit da VPS liga). A revisão do Codex no PR #126 pegou três coisas: cotação que já melhora o topo ao nascer (livro largo — os pools) seria recolhida no primeiro segundo sem o mercado ter andado, então a referência é `min(preço, melhor bid na primeira observação)` e recolhe só se o mercado cair dela; lado de bids vazio recolhe (é o caso mais forte, e é o que a simulação medida faz); e os prints do intervalo são conferidos ANTES de sair, senão a saída apagava o cursor da caixa e subcontava a própria métrica. E na segunda rodada: a referência nasce na COLOCAÇÃO, do livro que colocou a cotação (mercado que anda no primeiro segundo é recolhido no primeiro poll), e `OSError` do diário no recolher segue o mesmo caminho fatal da passada (`io_do_diario_maker`); e o último intervalo de reward é acertado pela caixa ANTES de sair, senão recolher muito empurraria o reward para baixo por construção. Na quarta rodada: quando só o livro do Down dispara a saída e o do Up não está à mão, a caixa acerta no ESPELHO do livro do Down (bid = 1 − ask, o mesmo espelho da colocação) em vez de perder o intervalo; e a referência do recolher sai com a cotação por qualquer porta (`janela_fechou`, `pool_sumiu`, recotação), não só pelo recolher — antes cada uma deixava uma chave morta para sempre. Na quinta: em LIVE, cotação que melhora o topo (os pools) É o melhor bid do livro desde que nasce, e comparar o topo com a referência nunca dispararia — o que se compara é o melhor bid EXTERNO (o livro sem o nosso nível), e a referência da recotação desconta a nossa ordem anterior. Na sexta: tokens e parâmetros de janela que nunca cotou saem quando a janela some (a limpeza da saída da cotação não os alcançava). Na sétima: a referência do Down vem do livro do DOWN, não do espelho do Up — os dois livros não são complementares, e a referência sintética contra o livro real recolhia par parado no primeiro poll; perna sem livro na colocação fica sem referência e é marcada na primeira observação. Na oitava: Down sem bid nenhum não recebe o par com a regra ligada (`perna_down_sem_bids`) — o recolher o tiraria no segundo seguinte e a passada o poria de volta, sem fim; e a caixa acerta o reward NO INSTANTE do print, com as pernas de antes dele, no mesmo `conferir_prints` da passada e do recolher — senão um print no meio de 10 s fazia os 10 s parecerem de um lado só (o relógio da caixa nunca anda para trás). Na nona: os prints das DUAS pernas entram em ordem de tempo, não perna a perna — um print do Down anterior ao do Up era visto depois dele e descartado, e o Down parecia aberto até o print do Up. 21 testes. **🟡 falta medir em SHADOW se o sono de 1 s basta** — a medição foi com 100 ms; o relato tem de mostrar `livro_andou_contra` e a caixa tem de mostrar menos execuções atravessadas por hora que a r9 sem a regra. 🟡 **(f) âncora do MICROPRICE (2026-09-14, noite):** o `maker_de_pares` mediu sobre a gravação de 13/09 que cotar a partir do microprice é **o que vira o sinal** do termo determinístico — de −34,67 (juntando ao topo, lote 20) para **+30,68** a 1 tick abaixo dele, com a soma paga do par caindo de 1,015 para **0,995**; com lote 100, +137,84; a 3 ticks o fill morre (`docs/OUTROS_BOTS.md` §6, item 6). É a peça central do `poly-maker` (`quoting.py:37-39`) e era a única do estudo com efeito medido que ainda não tinha porta para o caminho ao vivo. **O microprice era função PRIVADA de um script** (`_microprice` em `scripts/maker_de_pares.py`): quem mediu e quem opera não podiam usar a mesma — virou `OrderBook.microprice`, e o script medido passou a chamá-la (mesmo caminho). No laço ele entra como **âncora**, não como novo meio: `AncoraDoMicroprice` é um TETO do bid e um PISO do ask, e **só aperta** — nunca puxa a cotação para mais perto do meio do que a distância já escolhida. Ela mora dentro de `Cotacao.preco`, e não em quem envia, porque o preço AVALIADO e o ENVIADO têm de ser o mesmo número: a conta vê a perna Down como o ask do livro do Up, o laço a envia como bid no livro do Down com a âncora espelhada, e um teste parametrizado trava que os dois arredondamentos caem no mesmo tick — é o modo de falha do §6.1b, que não levanta erro nenhum. Cada perna é ancorada pelo microprice do LIVRO DELA (a lição da rodada 7 do #126: os dois livros não são complementares). Falha fechada: com a âncora ligada e o microprice de alguma perna indisponível, NÃO se cota (`sem_microprice`) — e não se cancela o que já repousa, porque é falta de dado nosso. `ticks` negativo levanta na CONSTRUÇÃO. Knob `maker_ticks_abaixo_do_microprice` (padrão `None` = desligado). 13 testes, cinco mutações verificadas (ignorar a âncora no envio, não espelhar na perna Down, ignorar no score, afrouxar em vez de apertar, e cotar sem microprice). **A âncora empurrou o `_passo_da_janela` para complexidade cognitiva 31** (o SonarCloud reprova acima de 15, e foi o que reprovou o portão de qualidade do #127 — os achados do Sonar são ilegíveis da sessão da nuvem, então a métrica foi medida localmente, com o pacote `cognitive_complexity`, antes e depois). Os portões viraram métodos nomeados — `_dados_da_passada` junta os quatro que têm o MESMO tratamento (livro indisponível, janela sem tempo, livro sem meio, sem microprice: nenhum cancela o que repousa), e `_ancora_do_microprice` e `_perna_down_sem_bids` ficaram cada um no seu —, e a função caiu para **abaixo de 15**, menos do que os 25 que já tinha ANTES deste PR. **E a reestruturação achou um defeito de métrica:** `portao:*` e `perna_down_sem_bids` eram contados DUAS vezes quando havia cotação repousando, porque `_sair` já conta o motivo com que sai — o relato de 60 s inflava a contagem de cada trava justamente quando ela fazia o que mais importa: tirar do livro o que já estava lá. `_recusar` conta uma vez só; verificado por mutação. **A revisão do Codex no #127 pegou mais duas, e a primeira era grave:** a âncora valia só na COLOCAÇÃO. O microprice anda quando o TAMANHO no topo muda — sem o meio se mexer e sem a distância escolhida mudar —, e aí a ordem que repousa fica acima do teto novo, CONTINUA pontuando (logo `atual_nao_pontua_mais` não a pega) e a candidata ancorada pontua MENOS que ela, então o piso de ganho jamais aprovaria a troca: ela ficaria ali até morrer. `acima_do_teto_do_microprice` é trava própria e vence a histerese, pela mesma razão de ordem que o `atual_nao_pontua_mais` vence — deixá-la para o piso de ganho seria deixá-la nunca. A segunda: `ticks` negativo levantava só na primeira passada com livro bom, e o laço maker roda como tarefa própria (defeito nele sai no log sem derrubar a rodada), então um valor errado no ambiente mataria em silêncio a rota inteira por 14 dias com o processo vivo — a validação subiu para o `Settings` (`ge=0`), que recusa no carregamento. Três mutações a mais verificadas. **Na segunda rodada, o inverso da mesma moeda:** quando o teto AFROUXA (o topo do livro engorda de novo), a ordem não está acima dele — não há risco a corrigir — mas a MESMA `Cotacao` volta a caber mais perto do meio, e o atalho do `estavel` a dava como igual: uma catraca que só andava para longe do meio, pontuando menos a cada aperto. Eu tinha argumentado que o caso não era alcançável com a grade (1..5) e estava ERRADO — uma busca sobre o próprio código achou 186 combinações de topo que o produzem. `_a_ancora_mudou_o_preco` só ABRE o caminho: aqui não há risco, só oportunidade, então quem decide são a histerese e o piso de ganho (com pool de 100 USDC/dia o piso segura, e é a política certa; com 1.000 a ordem volta). E a receita da unit para a rodada da âncora passou a trocar UMA regra pela outra — ligar só a linha nova deixaria as duas ligadas, que é exatamente o que a nota ao lado proíbe. **Na terceira, uma incoerência minha:** eu tinha escrito o ramo que CANCELA por teto sem candidata e o tornado inalcançável, porque montava o teto só quando havia candidata que pontuasse. Livro que ALARGA com o meio parado tira a grade ancorada inteira da faixa de reward — e é justamente aí que o teto mais importa, porque a que repousa continua pontuando. O teto passa a existir sempre que a regra está ligada; só o preço da candidata é opcional. **Na quarta, a falha fechada estava fechada demais:** `sem_microprice` abortava a passada INTEIRA, e com isso a caixa deixava de contar o repouso (truncando em silêncio a medida que a rodada existe para fazer) e o PORTÃO deixava de rodar — um disjuntor que armasse durante a falta não tirava a ordem do livro, que é exatamente a propriedade que o 4.0 registra ter custado caro para achar. Agora faltar o microprice impede COTAR, e só. **E uma revisão adversarial minha, depois que o Codex esgotou o limite de uso, achou o pior defeito de todos — e ele era MEU, da rodada 1:** eu tinha feito a trava do teto vencer a histerese, mas o teto é `microprice − folga`, e o microprice anda com o MEIO. Com `distancia_ticks=1` e `ticks=1` o preço colocado COINCIDE com o teto, então qualquer passo de 1 tick do meio punha uma das pernas acima dele. Medido na cadência real de 15 s, com os tamanhos do topo congelados: **39 trocas em 40 passadas, todas antes do repouso mínimo de 30 s**, contra 2,05 com a regra desligada. Reposicionar custa a fila, e nem a simulação do `maker_de_pares` nem o SHADOW enxergam esse custo — seria pagá-lo às cegas, e a trava chegou a reposicionar com ganho estimado NEGATIVO. O conserto separa o que eu tinha juntado: o **limite duro** é o microprice (repousar acima dele é ser a opção grátis, e isso vence a histerese), a **folga de `ticks`** é preferência de colocação e não é emergência. Remedido: **9,42 trocas, zero antes dos 30 s**. Um teste trava a regressão (40 passadas com o meio passeando, zero trocas antes do repouso mínimo). A mesma revisão pegou mais três: `acima_do_microprice` não estava em `MOTIVOS` (regra do projeto — toda recusa tem nome constante); `sem_microprice` inflava `sem_candidata_que_pontue`, que é o contador que distingue *o bot está travado* de *o bot não achou trade*; e a exclusividade entre os dois knobs só existia em comentário — agora o relato de 60 s publica `maker.regras`, então uma rodada confundida se denuncia na primeira hora em vez de no fim dos 14 dias. **Fica dito, para a comparação entre rodadas:** com o knob DESLIGADO as ações e os preços são idênticos aos de antes, mas `portao:*` e `perna_down_sem_bids` passaram a contar UMA vez em vez de duas quando havia cotação repousando — rodada de antes e rodada de depois não são comparáveis nesses dois contadores. **🟡 falta medir, e a medida tem de ser SEPARADA da do (e):** ligar as duas na mesma rodada confunde os efeitos e nenhuma das duas fica medida. A unit da VPS **não** liga esta — a rodada do recolher está em curso. E o que a medida do §6 autoriza dizer é limitado: ela comparou com *juntar ao topo* nas janelas Up/Down de cripto, não com a nossa grade de distância do meio nos pools, e o termo medido **não incluía rewards** — afastar do meio baixa o score do §15.3. O que se espera ver numa rodada com ela ligada: menos execuções atravessadas por hora e soma paga do par abaixo de 1,00, sem o reward por hora cair mais do que isso compensa. 🟡 **(g) pausa por fill tóxico (2026-09-15):** o regime EVENT do `poly-maker` disparado pelo NOSSO fill, e não pelo salto do livro — quem atravessa a cotação paga acima do nosso preço para entrar AGORA, e normalmente sabe de algo que o livro ainda não mostrou; cotar no minuto seguinte é oferecer a mesma opção de graça outra vez. **Duas medidas independentes apontam para cá.** A simulação (`--grade bots`, hora de fumaça de 13/09, microprice −1 tick a 245 ms): base **+14,24**, com 30 s **+18,42**, com 90 s **−0,52** — noventa segundos matam o fill, e a janela certa é curta. E a r7 do SHADOW, do outro lado: de 12 execuções possíveis, **2 atravessadas dominaram o markout** (−22,85 USDC, −0,7376 ¢/share). `LacoMaker.pausa_apos_fill_toxico_s` tira a cotação com motivo `pausa_por_fill_toxico` e NÃO recoloca enquanto dura; a caixa guarda o instante do último fill atravessado por slug, e esse relógio SOBREVIVE ao `esquecer` justamente porque a pausa existe para valer depois de a cotação sair. Fill **no nível** não pausa: é a fila andando, e pausar nele tiraria a cotação do livro toda vez que ela funcionasse. Knob desligado por padrão, validado no carregamento. 5 testes, 4 mutações verificadas. **A revisão do Codex pegou uma:** a pausa saía ANTES de a caixa fechar o intervalo. Um fill atravessado consome UMA perna; a outra segue no livro ganhando reward de um lado só até a passada seguinte, e `_sair` apaga o relógio — sair antes de fechar jogava fora até uma cadência inteira de reward legítimo, e isso enviesava o experimento CONTRA a própria regra que se quer medir. Agora a pausa vem depois do acerto, na mesma ordem que o `livro_andou_contra` já seguia — as duas saídas passaram a ser o MESMO `_recolher`, com o motivo por parâmetro, porque duas cópias dessa ordem é como as duas divergem. **E a segunda rodada pegou mais três, todos sobre a mesma pergunta — a regra faz o que foi medido?** (1) A pausa esperava o livro para decidir, mas **cancelar não precisa de livro**: uma falta mais longa que a pausa deixaria a cotação exposta o intervalo tóxico inteiro. (2) Os prints só eram vistos na passada de 15 s, então um fill logo depois dela deixava a outra perna exposta quase uma cadência e **a pausa de 30 s virava 15–30 s efetivos** — seria medir outra regra; `recolher_por_fill_toxico` entrou no sono de 1 s, ao lado do recolher, e a decisão de COTAR segue nos 15 s. (3) `ts_ns` é a CHEGADA, e um `last_trade_price` reenviado depois de reassinatura chega agora carregando negócio de minutos atrás: a pausa passou a armar pelo **carimbo do servidor** quando ele existe, o que não inventa limiar nenhum — a execução continua contando, só não arma a pausa. E na terceira rodada o mesmo achado com uma volta a mais: um reenvio RECENTE o bastante para a pausa valer, mas de negócio ANTERIOR à cotação, tiraria do livro uma ordem que aquele negócio não pôde ter executado — ela nem existia. A pausa só arma com negócio dentro da vida da cotação. **E na quarta rodada o achado deixou de ser sobre a pausa e virou sobre a MEDIDA:** o reenvio anterior à cotação não armava mais a pausa, mas seguia CONSUMINDO a perna — era contado como execução nossa de uma ordem que não estava no livro quando aquele negócio aconteceu, e o fill real seguinte era então descartado como perna consumida, justamente no caso em que a pausa deve armar. Print com carimbo do SERVIDOR anterior à cotação passa a ser descartado da conta inteira e contado como `prints_reenviados` no relato — um número que nunca tinha sido publicado. **Isto muda a linha de base da caixa, inclusive com os knobs desligados:** `execucoes_atravessadas` e o markout caem pelo tanto de reenvio que o feed mandar, e o sentido da mudança FAVORECE a rota, o que obriga a dizê-lo alto. A conta velha estava errada — um negócio anterior à cotação não pode tê-la executado —, mas os números de 4.2 medidos antes disto não são comparáveis com os de depois, e `prints_reenviados` é o que diz de quanto foi. **🟡 falta medir**, e agora são TRÊS regras esperando rodada — cada uma precisa da sua, e o relato de 60 s publica `maker.regras` para uma rodada confundida se denunciar na primeira hora. **E o custo das reconexões saiu do código novo em 2026-09-21 (runbook §10.1i), com um número que a rota maker precisa carregar:** 27 reconexões/h, 116,8 s sem livro (3,24% da hora), pior buraco 37,1 s — contra a linha de base de 23,6 s (0,65%) do código antigo. A minha previsão, dita antes da medida, era ~70 s/h: errou 67% para baixo e errou o LUGAR. 86% do custo veio de TRÊS quedas de `clob[updown]`, todas `1013 slow consumer: send buffer full` — o servidor dizendo que o NOSSO cliente não drena o socket, que é a starvation de CPU de 1 vCPU chegando na ponta do WebSocket. As 24 quedas do RTDS custaram 16 s (0,67 s de média, ao lado dos 0,79 s da base), confirmando em produção que o nosso próprio 1012 tem piso zero. **O número que decide a rota não é a média:** durante a rajada de 3,3 min foram 100,7 s cegos em 195,6 s — **51% do tempo sem livro do `updown`**. E a janela de 4 h achou um defeito meu do #177/#178: as nove quedas dormiram todas no teto, inclusive uma que veio depois de 77,1 min de conexão saudável — `pedidos_de_paciencia_seguidos` só zerava numa queda sem piso, e nada media o "seguidos". Consertado com reset por saúde, testes por mutação. **🟡 falta repetir na máquina de 4 vCPU**, porque calibrar o backoff com um número produzido pela CPU que vamos trocar seria medir a máquina, não a política. **E em 2026-09-21 o operador RECUSOU a máquina maior, o que fecha o último caminho que este item tinha (runbook §10.1j).** Sem eufemismo: o 4.2, do jeito que está especificado, **não tem caminho em 1 vCPU** — quatro em paralelo dá `id=0`/`r=4`, o plano B de três janelas reprovou com `%wait` de 0,38% → 16,70% → 35,97%, e uma rodada por vez é recusada pelo próprio §10.1c porque compararia a regra com o mercado em vez de com um controle do mesmo intervalo. **A saída que restou não estava na tabela do §10.1f:** a rodada não é um bloco de tamanho fixo — `PULSEARB_TOP_DE_POOLS_DE_REWARD=60` é o que compra os ~40% de núcleo. Baixá-lo NAS DUAS rodadas da janela mantém a comparação contemporânea que o 4.2 exige, ao preço de o veredito passar a valer para os N melhores pools e não para 60 — e de 14 dias talvez não carregarem o mesmo peso estatístico, conta que sai dos diários r4–r8 no Mac e **não está feita**. **Nem está medido que 20 pools cabem:** a relação entre pools e CPU pode não ser linear. A medida de `%wait` que decide custa 20 minutos e está escrita no §10.1j, com a régua do §10.1f. **Enquanto ela não rodar, este item não anda**, e o que dá para fechar sem ela é só uma `base` sozinha por 14 dias — que mede sobrevivência, repouso e fração cotável, e não decide regra nenhuma. **A medida rodou no mesmo dia e REPROVOU: `%wait` 28,59%** (mín 3,80, máx 47,00) com 20 pools, contra 35,97% com 60 — e `%CPU` 38,35% contra os ~40% de antes. A variável pegou, conferido no log (`janelas: 20`), não suposto. **Três vezes menos pools, o mesmo custo: a saída que eu tinha proposto está morta, e teria custado um veredito mais estreito por nada.** O que ela ensinou, porém, muda o alvo: o custo não escala com pools, e `%usr` 36,07% contra `%system` 2,27% diz que é cálculo em Python por EVENTO DE LIVRO, não por mercado, não I/O. E `base` e `pausa` são dois processos que assinam os mesmos tokens e processam o mesmo livro cada um por si — o trabalho caro é feito duas vezes, que é também por que o `id` do `vmstat` nunca via a contenção que o `%wait` via. **A hipótese que sobra** — um processo, uma assinatura, as regras em paralelo dentro dele — sairia de graça em hardware e em escopo, mas é hipótese, e a anterior reprovou; o perfil de 60 s com `py-spy` no §10.1j é que decide, e se ele vier sem topo claro **o 4.2 fica parado nesta máquina**. **E ao ler o código para interpretar o perfil apareceu o que nenhuma medida tinha olhado:** cada rodada carrega a rota taker INTEIRA, e ela não é opt-in — `clob[updown]`, `laco_de_descoberta` e `laco_de_decisao` sobem incondicionalmente; só a rota de pools tem trava. E o `clob[updown]` é, pela medida de 2026-09-14 gravada no próprio código, **54% do tráfego em rajadas de 4 MB/s**, contra 50–120 msg/s dos pools. Isso explica a reprovação dos 20 pools: `TOP_DE_POOLS_DE_REWARD` mexe só em `clob[pools]`, então a medida cortou a metade PEQUENA e deixou a grande intacta — **e derruba a minha leitura de que 'o custo não está no processamento de livro', que não foi testada.** Significa também que as duas rodadas do 4.2 pagam a conexão mais pesada da casa para medir a rota maker, sendo que o taker já está medido e reprovado (1.1/1.4/1.5). A medida que decide não toca em código — a mesma rodada sozinha, com e sem `DESCOBRIR_POOLS_DE_REWARD` — e está no §10.1j com as três leituras escritas antes de rodar. **O plano do caminho está escrito em `docs/PLANO_4_2_NUM_PROCESSO.md` (PROPOSTA, nada implementado):** as quatro variantes num processo, com feeds e `CicloAoVivo` compartilhados e `LacoMaker`, diário e `PortaoDeRisco` por variante. O ponto perigoso está nomeado — hoje taker e maker de uma rodada dividem um portão, e com N variantes num portão só a exposição de uma consumiria os tetos da outra, fazendo as variantes parecerem diferentes por ordem de chegada e não por mérito. Verificado na fonte que `PortaoDeRisco` aceita registro por instância, que o kill é arquivo (uma chave derruba todas) e que o disjuntor é por registro. Seis travas, cada uma com teste por mutação, e uma pergunta aberta que precede qualquer linha: a rota maker depende de algo que o laço do taker produz? **E o #181 foi ao ar em 2026-09-21 (§10.1k): a regra funciona — saúde acima de 60 s zera e o sono volta para 5–7,5 s — mas o resultado PIOROU**, de 2,25 quedas/h e 78,7 s cegos para 27,2 quedas/h e 390,6 s (10,8% da hora) no `clob[updown]`. A leitura que desfavorece o meu conserto é a mais provável: os sonos de 30–45 s estavam suprimindo as quedas, e voltar em 5 s reassina no meio da rajada de 4 MB/s. Os 27/h estão dentro da faixa nativa medida em 14/09 (até 6 quedas em 7 min); os 2,25/h é que eram a anomalia do contador travado no teto. **O #181 fica** — o defeito que ele consertou é real e independente. **Falta o agregado por conexão:** o maker cota no `clob[pools]`, não no `updown`, e se o pools estiver limpo esse custo é todo do taker, que o plano prevê desligar. **O agregado chegou e é isso mesmo:** `clob[updown]` 23 quedas e 322,6 s, RTDS 24 quedas e 15,5 s, e **`clob[pools]` ZERO quedas na hora inteira** — e o zero é medido, não linha que faltou: a conexão aparece uma única vez no log (o `conectado`) e a descoberta segue publicando `janelas: 53`. **O efeito colateral do #181 não toca o 4.2**; ele encarece a rota taker, que está medida, reprovada e que o plano prevê desligar — o que reforça a fase 1, porque desligá-la tira de uma vez o acoplamento de orçamento no portão, os 54% de tráfego e esses 322,6 s cegos por hora. ❓ **O que o agregado NÃO prova:** que o livro de pool está chegando — conexão viva e muda dá o mesmo zero; fecha isso o relato de 60 s do maker, com cotações em vez de recusas por `sem_livro`. **O relato foi lido e achou coisa pior (runbook §10.1l):** o livro CHEGA — a estratégia achou onde cotar **6489 vezes** —, mas `cotacoes_repousando` é **0** porque `portao:disjuntor_armado` recusou as 6489. O registro lido antes da limpeza diz `"perda do dia em -25.35 USDC, teto 25.00"`, e o `pausado_ate_epoch` converte para **2026-09-20 ~00:32 UTC**: **o disjuntor estava armado havia quase dois dias, e toda rodada maker desde então recusou cotar.** A causa é de configuração e está no `comum.env`: ele sobe `stake_max_por_trade` ×200, `stake_max_por_janela` ×133, `exposicao_max` ×2400 e `posicoes_max_abertas` ×24, e **deixa `perda_max_diaria_usdc` no default de 25 USDC** — com cotação de 1000 shares, uma execução ruim estoura, e o disjuntor GRUDA por projeto. **A limpeza do §10.1g não conserta isso:** ela zera o registro e o teto continua onde estava, então a rodada seguinte arma de novo. Faltam duas travas: o teto tem de ser ESCOLHIDO pelo operador (4.0 (c)) e escrito no `comum.env`, e o arranque tem de conferir que nenhuma instância sobe com o disjuntor armado — a conferência do §10.1g olha se os diários nasceram vazios e não olha o portão. **Enquanto as duas não existirem, nenhuma janela de 14 dias do 4.2 vale**, porque produz zeros que parecem mercado quieto. **As duas travas fecharam no mesmo dia.** O operador escolheu o teto: **5000 USDC**, escrito em `comum.env` com a razão junto — não é número novo, é a MESMA razão que o default carrega (25 = 5× o stake de 5, "cinco trades ruins e para") aplicada ao stake de 1000 deste perfil. E a conferência do disjuntor entrou no §10.1g ao lado da dos diários, porque a conferência antiga olhava só se os diários nasceram vazios e deixou passar dois dias de rodada que não cotava. O defeito ganhou teste em `tests/test_perfil_do_ensaio.py`, três, todos verificados por mutação: tirar a linha reprova, pôr o default de 25 reprova com `assert 25.0 >= (5.0 * 1000.0)`, e pôr 999999 reprova também — porque subir não pode virar desligar, e um teto que nunca arma não exercita o portão, que é metade do motivo de o SHADOW existir. **Falta aplicar na VPS:** `git pull` e reiniciar, senão a rodada no ar rearma com o teto velho. **Fase 0 do plano respondida em 2026-09-22, e ela desmente o "aparenta que não" que eu mesmo tinha escrito:** a rota maker DEPENDE de estado do taker, em dois pontos. (1) O rastreador é um dict só — `on_descoberta` chama `atualizar` e a descoberta de pools chama `absorver` —, então o maker recebe as janelas Up/Down junto; mas já as recusa com `sem_pool_de_reward` (os 772 do relato), e desligar o taker **não muda o que ele cota**. (2) **`feeds_saudaveis` mata o maker**: `ultimo_preco_ns` só recebe tick `twap_sixty` filtrado por `ativos_operados = frozenset(settings.assets)`, e sem nenhum preço o portão recusa com `FEED_PARADO` — esvaziar `assets` para desligar o taker reproduziria exatamente o quadro do §10.1l, rodada saudável cotando zero. **Isso restringe a fase 1:** desligar `laco_de_decisao`, `laco_de_descoberta` e a conexão `clob[updown]` é seguro; tocar em `assets` NÃO é, e o teste da fase 1 tem de exercer que o maker continua cotando com o taker desligado. Fica registrado, sem virar trabalho agora, que o portão aplica ao maker um critério que é do taker — cotar um mercado de reward não deveria exigir preço de BTC fresco; a frescura que importa ali é a do livro do pool, já vigiada em `_livro_para_o_maker`. **E em 2026-09-22 a rodada finalmente cotou:** com o teto de 5000 e o registro limpo, `cotacoes_repousando` saiu de 0 para **45**, e `portao:disjuntor_armado` sumiu do relato — primeira vez que a rota produz o dado que este item existe para medir. **O mesmo relato entregou um defeito de NOME (runbook §10.1m):** 1.899 das 451.116 passadas da janela anterior recusaram com `portao:ordem_mal_formada`, que o `risk/gates.py` define como "defeito de quem chamou" — e não era. A perna Down é um bid no livro dela, cujo meio é `1 − meio_up`; num mercado a 0,99 esse meio é 0,01 e recuar um tick o põe em ZERO, reproduzido na aritmética. Os pools de reward incluem exatamente esses mercados (as várias janelas de *bankruptcy by 2029*). Como `ORDEM_MAL_FORMADA` é checado antes de `PRECO_FORA_DA_FAIXA`, essas recusas nunca chegavam ao motivo que as descreve — e quem lesse 14 dias de relato iria caçar um bug que não existe. **Consertado no nome, não no preço:** o maker reconhece a perna que saiu de (0,1) antes de perguntar ao portão e conta `sem_espaco_para_recuar`; nenhum mercado passa a ser cotado ou deixa de ser, e `ordem_mal_formada` volta a significar defeito nosso. Pôr piso em `Cotacao.preco` moveria a cotação, que é mudar a estratégia. **72 testes** no laço maker (`test_4_0c_laco_maker.py`), três deles novos e dois verificados por mutação. **E o primeiro dado do 4.2 saiu no mesmo dia (runbook §10.1n):** 19 min, 48 cotações, **100% do tempo repousando também pontuando**, e **130,81 USDC/h de parede** — 13× a barra do 1.12. **Registrado como MEDIDO e NÃO INTERPRETADO**, por três razões: 19 minutos são 1/1300 do que o item pede; o `pro_rata` é estimativa com hipótese de fila; e 6 medidas de markout respondem por 11 dos 47 USDC do líquido. **E a premissa que domina o número não era observável em lugar nenhum** — `rewards = daily_rate × horas/24 × fracao` com `fator = 1` no pro-rata, e `fracao_do_pool` não estava no relato nem no diário, que grava só a ordem. Catorze dias dariam um número sem forma de conferir se ele veio de uma fatia plausível ou de o modelo nos atribuir o pool inteiro de 48 mercados — cotamos 1.000 shares, ×200 o default, contra livros finos. **Isso travava o relógio dos 14 dias e foi consertado antes de ele começar:** a `CaixaDoMaker` acumula a fatia no mesmo lugar e com o mesmo peso que acumula o reward, e o relato publica média ponderada pelo TEMPO, máxima, e a CONTAGEM de passadas com ≥ 90% do pool — com `None` em vez de zero quando não houve medida, porque zero seria afirmar fatia nula. Três testes por mutação. **A fatia foi medida no mesmo dia: 0,3409 de média ponderada** — e a minha suspeita de que ela estaria perto de 1 NÃO se confirmou. Com o denominador certo o número é ~98 USDC/h de parede, ainda ~10× a barra. **Mas a máxima é 1,0 e 84 de 716 passadas têm fatia ≥ 90%**, e a contagem não dizia quanto do reward vinha delas — distribuição da premissa e contribuição dela para o número são perguntas diferentes, e eu tinha publicado só a primeira. Corrigido: `rewards_dessas_passadas` e `fracao_do_total_que_vem_delas` entram no relato, e o teste que os guarda produz o caso que justifica o campo — uma passada de duas responde por 95% do reward. **⚠️ E o líquido hoje é estimativa pura:** `liquido == rewards` porque o markout tem 3 medidas e 0,0 c/share. A única parcela MEDIDA do item não contribui com nada, e as outras duas (fatia e `fator_de_captura = 0,3`) são hipóteses |
>>>>>>> origin/main
| 4.2 | **SHADOW ≥ 2 semanas** com edge líquido *medido* | 🟡 **o relógio EXISTE desde 2026-09-14, e ainda não rodou.** Até então o SHADOW do maker contava `cotacoes_repousando` e nada mais — nenhum número de dinheiro, então as 2 semanas não tinham o que medir. `live/caixa_maker.py` (`CaixaDoMaker`, 33 testes em `tests/test_4_2_caixa_maker.py`) fecha a conta a cada passada do laço com as MESMAS funções da análise: **(1) rewards integrados no tempo** — a cada passada, `estimar_retorno` da cotação aberta sobre o livro de agora, por `horas = dt/3600`, somando `rewards_pro_rata` (sem hipótese de fila) e `rewards_com_captura` (× 0,3); intervalo sem passada maior que 60 s é TRUNCADO e contado (`intervalos_truncados`), para uma queda de feed não virar reward; **(2) execuções possíveis** — os prints `last_trade_price` do WS (§6.1a; `LivrosAoVivo.negocios_desde`, 256 por token) contra o preço da NOSSA perna: `SELL` a preço ≤ o nosso é execução-sombra, **atravessada** (o tamanho inteiro) se abaixo ou **no nível** (o tamanho do print, pro-rata) se igual; **(3) markout medido, não transportado** — cada execução-sombra vira uma medida a 5 s (`meio_5s − preco_nosso`, em ¢/share), sem meio depois de 120 s vai a `sem_referencia`; e o `liquido_pro_rata_usdc = rewards − custo_de_markout` sai no relato de 60 s em `maker.caixa`. **A conta é PESSIMISTA** no que não se observa (fila) e HONESTA no que se observa (prints, meio): print que bate o nosso nível executa contra nós na conta, mesmo que na fila real fosse de outro maker. **Primeiros números, r7 (2026-09-14, 20 min, 60 pools, 1.000 shares por perna, diário `data/shadow/diario-20260914-061217-*`):** 56–60 cotações repousando, **40.594 cotação-segundos** (11,3 cotação-horas, já descontados os 5 min de queda de rede); **rewards pro-rata 29,94 USDC** = **2,66 USDC/h por cotação** → ~150–160 USDC/h com 60 no livro, do tamanho do mínimo de 174 USDC/h do 1.12(a); com captura 0,3: 8,98. **Execuções possíveis: 12** — 10 `no_nivel` (1.097,6 shares, fila decide) e **2 `atravessadas` (2 × 1.000 shares, a perna inteira)**, nos 146 prints vistos; **markout −0,7376 ¢/share sobre 3.097,6 shares = −22,85 USDC** — as duas atravessadas (mrbeast 0,92 → print 0,89; NYC temperatura 0,18 → 0,17) dominam. **Líquido pro-rata +7,09; líquido com captura 0,3 = −13,87 USDC em 20 min.** Duas ressalvas escritas: (1) o horizonte medido foi **23,4 s, não 5 s** — o markout fecha na passada seguinte do laço, a cada 15 s; os −0,0606 ¢ do 1.12(c) são a 5 s, então os dois números NÃO são o mesmo instrumento ainda; (2) as duas atravessadas caíram na MESMA passada (1789366908, 1,4 ms entre elas, mercados diferentes), e a caixa carimbava o print pela chegada — reenvio de reassinatura pareceria negócio de agora. Desde este commit `Negocio` guarda o `timestamp` do servidor (§6.1a) e o log da execução-sombra sai com `atraso_s`; a r8 dirá. A r7 subcontou o relógio por `livro_indisponivel` em 38% das passadas (defeito de medida consertado no mesmo commit, ver a nota da r7 no 1.12) — os números acima são PISO do reward e não são o markout do regime. **O horizonte de 5 s ficou de verdade em 2026-09-14** (`ProcessoShadow._dormir_medindo_markout`, 3 testes em `tests/test_m4_shadow_processo.py`): entre as passadas de 15 s o laço dorme em passos de 1 s e, enquanto houver execução-sombra pendente, fecha o markout dela contra o livro de agora — a decisão de cotar segue a 15 s, só a medida ficou a 5 s. **r8 (2026-09-14, 20 min, mesma configuração da r7, livro vigiado pela conexão, registro limpo; rodou ANTES deste conserto):** o defeito de medida da r7 sumiu — `livro_indisponivel` não aparece nos motivos (r7: 38%), `intervalos_truncados 0`, 55–56 cotações repousando por 15 min seguidos, **50.131 cotação-segundos**, rewards pro-rata **37,84 USDC = 2,72 USDC/h por cotação** (r7: 2,66 — repete). Execuções possíveis **7** em 112 prints: 4 `no_nivel` (480 shares) e **3 atravessadas** (Seoul 26 °C 0,07 → 0,05; NYC 76–77 °F 0,23 → 0,22; mrbeast 0,88 → 0,87); **markout +0,2638 ¢/share sobre 3.480 shares = +9,18 USDC** (r7: −0,74 — sinal oposto em 20 min cada: amostra pequena, não markout que mudou), ainda a **20,4 s**. O carimbo do servidor respondeu à ressalva (2): os 7 prints chegaram com `atraso_s` de 0,6 a 14,1 s, em passadas diferentes — negócios de agora, não reenvio. **E a pausa da r6 voltou com o registro limpo:** aos ~16 min `portao:pausa_por_sequencia 970` (4 perdas sintéticas seguidas do taker em SHADOW) tirou as 55 cotações — 55 → 0 nos 4 min finais. Isso torna a pergunta de política da r6 um BLOQUEIO do 4.2: com a pausa compartilhada, 2 semanas perdem ~44 min por hora e a fila inteira a cada disparo. **A resposta é humana, não minha:** as duas saídas que tentei (subir a variável de pausa por env na rodada; dar ao maker uma avaliação de risco que pule SÓ a pausa do modelo, mantendo chave, disjuntor, feed, relógio e spread, contada no relato) foram barradas pelo classificador de permissão da sessão como afrouxamento de portão de risco, e eu não contorno isso. Argumento a favor da exceção: a sequência é alimentada só por resultado de janela do TAKER (o maker não escreve no registro), é sinal do modelo dele, que o maker não usa. Contra: um portão a menos é um portão a menos, e o maker em LIVE ainda não alimenta o disjuntor com as execuções dele. 🟡 falta: a decisão sobre a pausa (operador sobe a variável por env para o ensaio, ou aprova a exceção por escrito); a r9 com o markout a 5 s; e então as 2 semanas, com o registro `.shadow.json` limpo. **A CAIXA TINHA TRÊS DEFEITOS, OS TRÊS A FAVOR DA ROTA (revisão de 2026-09-14, reproduzidos executando o código; consertados no mesmo commit desta frase):** (1) a perna-sombra **nunca era consumida** — cada print que batia o nível contava a perna inteira de novo (3 prints de 5 shares numa perna de 50 → 150 "executadas"), e a cotação seguia ganhando reward como se ainda estivesse no livro; agora cada perna é consumida pelo que executa, print em perna consumida é contado e ignorado (`prints_em_perna_consumida`), e cotação com as duas pernas consumidas não repousa nem rende (`acertos_apos_execucao`); (2) o reward era pontuado **a `distancia_ticks` do meio de AGORA**, não no preço enviado — ordem que o meio deixou fora da faixa seguia ganhando, e o laço nunca via `atual_nao_pontua_mais` por deriva; agora `estimar_retorno_repousando` (em `live/cotacao.py`, mesma fórmula, mesmo caminho) avalia `preco_up`/`preco_down` contra o meio de agora, na caixa E no laço; (3) o markout era `meio_depois − preco_nosso`, que **creditava a distância inicial ao meio como ganho** — mercado parado dava +d ticks por share; agora é `meio_depois − meio_no_fill`, como o `medir_markout` da análise, e execução sem meio no fill vai a `sem_referencia`, não a número. **Consequência: os números da r7/r8 estão contaminados nas duas direções** (reward de ordem fora da faixa e custo multiplicado por prints) e têm de ser remedidos com esta versão antes de valer como relógio. Achados menores da mesma revisão, em aberto: passada com `livro_indisponivel` não avança o relógio (mitigado pelo #119); o deque de 256 prints por token descarta sem contar; `confere_diario_maker` usa um `--tick` global; o significado de `side` em `last_trade_price` está assumido, não `[VERIFICADO]` no §6.1a. **Para as 2 semanas correrem sem o Mac dormir:** `deploy/pulsearb-shadow-maker.service` + RUNBOOK §10 ligam a mesma rota na VPS (`PULSEARB_MODE=SHADOW`, `PULSEARB_DESCOBRIR_POOLS_DE_REWARD=true`, `--duration 14d`, diário anexado, cliente sombra, trava tripla fechada, e o perfil do ensaio por escrito — 1.000 shares, `TOP_DE_POOLS=60`, os tetos das rodadas r4–r8 e registro de risco próprio, porque com os defaults do taker a rota não cota nada; cabe nos 18,4 GB porque o SHADOW não grava o stream) — o §10.1 diz o que o relato da primeira hora tem de mostrar. O PnL do TAKER em shadow anterior a 2026-08-31 segue **enviesado para cima** (`preco_pago = best_ask`) e não conta **E O VEREDITO DO 4.2 NÃO CHEGAVA A NINGUÉM (2026-09-15).** `liquido_pro_rata_usdc` — rewards menos markout, que é exatamente o "edge líquido medido" que este item exige — já sai no relato de 60 s desde 2026-09-14, e **nenhum leitor do repositório olhava para ele**: fechar o item obrigaria a abrir o `journalctl` e ler JSON à mão por 14 dias. É o defeito que o projeto já pagou duas vezes (o `#96` pôs `fecha_no_pior_caso` no JSON e o `resumo_m2.py` seguiu imprimindo o texto antigo; um item que podia ser ❌ ficou ⬜ em toda rodada), e o `resumo_das_rotas.py` nasceu dessa lição para o 1.11/1.12 — o 4.2, que é o item que decide se o bot vê dinheiro, seguia sem. **`scripts/resumo_da_rodada_maker.py`** (78 testes em `tests/test_resumo_da_rodada_maker.py`, 6 mutações verificadas nas DUAS direções — renomear o campo no leitor E renomear no motor derrubam o teste) lê os relatos e diz o veredito. Ele **não recalcula nada**: uma segunda conta da §15.3 divergiria da primeira e a divergência pareceria mercado. E recusa com nome as quatro maneiras de perder 14 dias, cada uma hoje invisível: `rodada_confundida` (duas regras experimentais juntas não se distinguem — o §10.1 mandava conferir a olho), `regras_mudaram_no_meio` (**a unit anexa ao mesmo diário de propósito, então editar a unit e reiniciar mistura duas regras nos mesmos 14 dias sem deixar rastro**), `rodada_dormiu` (3.16: 24 h com ciclo 0,12 observaram 2,9 h) e `relato_sem_maker` (o laço volta com "sem caminho de diario" e a rodada segue viva, relatando, medindo nada). Um detalhe que teria custado uma rodada inteira: `ticks_abaixo_do_microprice=0` e `pausa_apos_fill_toxico_s=0.0` são valores LIGADOS válidos (ambos `ge=0`) e falsos em Python — ler o knob por verdade booleana faria uma rodada da âncora passar por rodada BASE, e o quadro registraria a medida no item errado. Três testes travam isso. O leitor também confere a cada leitura o que o §10.1 pedia a olho: **todo `order_id` do diário começa com `sombra-`**, e um sem prefixo significaria ordem REAL. **Defeito achado junto e consertado:** o `confere_diario_maker.py` só aceitava `data/shadow` e a rodada do 4.2 grava em `data/diarios` (a unit passa `--diario`) — o verificador do achado r4 recusava, com uma mensagem sobre pasta errada, justamente o diário que importa; a contenção virou `caminhos.caminho_de_diario_lido`, que conhece as duas. 🟡 falta rodar: o leitor não tem rodada para ler. **E AS QUATRO RODADAS PASSARAM A CORRER JUNTAS (2026-09-15).** Eram três regras experimentais mais a base, cada uma precisando da sua rodada — **56 dias em sequência**. O tempo era o menor dos dois problemas: quatro rodadas em semanas diferentes comparam **regra com MERCADO**, porque a liquidez, a volatilidade e o próprio conjunto de pools mudam de semana para semana, e essa diferença entraria no resultado com o nome da regra. É a mesma confusão que a unit já recusava DENTRO de uma rodada, espalhada no tempo, onde cada rodada sozinha parece limpa. `deploy/pulsearb-shadow-maker@.service` (template) + `deploy/rodadas/{base,recolher,ancora,pausa}.env` sobem as quatro sobre o MESMO mercado, nos mesmos 14 dias. Próprios de cada instância, e cada um por um motivo medido: o diário (somar duas rodadas no mesmo arquivo é o que o `O_EXCL` já fecha), o registro de risco (**o `_gravar` monta o `.tmp` a partir do caminho do registro — duas rodadas no mesmo registro escrevem o mesmo temporário e o rename atômico pode publicar uma mistura**) e o arquivo de regra, este **sem o `-` do systemd**: com `-` um arquivo ausente seria ignorado em silêncio e as quatro subiriam como BASE, verdes, por 14 dias. O `KILL` segue compartilhado de propósito. **Defeito achado ao escrever os `.env` e consertado:** `PULSEARB_MAKER_TICKS_ABAIXO_DO_MICROPRICE=` (vazio, que é como uma unit escreve "desligada") **derrubava o processo na subida** com `int_parsing` do pydantic — sem grafia para desligado, a única saída seria omitir a linha, e aí regra ausente por decisão fica idêntica a regra ausente por esquecimento; `_vazio_e_desligado` no `Settings` lê vazio como `None`, e **`0` continua LIGADO** nos dois (âncora no microprice exato, pausa de duração zero), que é justamente por que desligado precisava de grafia própria. 39 testes em `tests/test_4_2_rodadas_do_shadow.py`, 4 mutações verificadas — a que mais importa: **um erro de digitação no nome da variável não levanta nada** (o pydantic não a encontra e usa o default), então a instância `@ancora` rodaria 14 dias medindo a BASE com o rótulo da âncora; o teste carrega cada `.env` no `Settings` de verdade e confere o que saiu. 🟡 falta rodar, e falta medir se a VPS carrega quatro processos: disco do diário (já em aberto no §10.2 com UM processo), memória e o limite de conexões WS do CLOB. Se não couber, a saída NÃO é voltar aos 56 dias — é base + uma por vez, que preserva a comparação contra o mesmo mercado. **E O LEITOR NASCEU QUEBRADO PARA O CASO QUE IMPORTA (revisão do Codex, consertado no mesmo dia).** Quatro defeitos, e o primeiro o tornava inútil: o `laco_de_relato` **retorna** quando o prazo vence, então o último relato de 60 s sai sempre ANTES do fim, e o estado final saía só por `print` em stdout — sem `msg` e com `indent=2`, fora do fluxo de relatos por definição e fora do `grep` documentado. **Toda rodada de 14 dias BEM SUCEDIDA sairia `curta_demais`**: o leitor jamais diria PASSA. Consertado nas duas pontas — `shadow.main` emite o estado final também pelo log, marcado com `fim_da_rodada`, e a marca separa três coisas que o resto do relato não separa (rodada que terminou, journal capturado no meio, processo morto pelo systemd); o leitor exige a marca (`rodada_nao_terminou`). **Os outros dois achados eram recusas que eu declarei em `MOTIVOS` e nunca liguei** — a recusa existia no papel e o veredito aprovava assim mesmo, que é o `limite_pessimista` outra vez: (a) zero medidas de markout deixava o líquido ser rewards puros e ainda assim PASSA, quando zero medidas é *não observei o custo* e não *o custo é zero* (`custo_nao_medido`, a mesma distinção do `sem_recortes` no 1.6); (b) `order_id` sem `sombra-` era impresso como alarme e o veredito saía PASSA — um ensaio que PODE ter mandado ordem real relatado como SHADOW válido (`ordem_sem_prefixo_de_sombra`, agora a primeira recusa de todas, valendo até sem relato nenhum). `test_todo_motivo_declarado_e_ALCANCAVEL` percorre o corpo do `_julgar` e não deixa mais nenhum motivo ficar decorativo. **E um quarto achado, meu, que nenhum dos dois tinha nomeado:** `Restart=always` + `CaixaDoMaker` que **não persiste nada** + `run` que refaz `inicio_parede` = um restart joga fora rewards e markout acumulados e reinicia o relógio dos 14 dias, com o diário continuando (é anexado) e o calendário na parede dizendo 14 dias. Nada denunciava. `parede_s` caindo entre dois relatos é a assinatura, e o leitor diz quantas vezes e que a conta cobre só o último trecho — o `curta_demais` já dava o veredito certo, faltava o motivo chegar a quem lê, para a saída ser *recomece limpa* e não *o leitor está quebrado*. 5 mutações verificadas nos quatro consertos. 🟡 falta: **a caixa não sobreviver ao restart continua sendo um risco da rodada de 14 dias, não só do leitor** — nenhuma das quatro rodadas paralelas tolera um restart sem perder a medida, e isso ainda não tem conserto, só sensor. **E um quinto achado na revisão, sobre a unit e não sobre o código (consertado no mesmo dia):** o `systemd.exec(5)` diz que, entre `EnvironmentFile=`, o **último vence** — e que qualquer `EnvironmentFile=` vence as linhas `Environment=`. Eu tinha posto o `.env` da máquina DEPOIS do arquivo da rodada: um `.env` de máquina que definisse qualquer variável do ensaio (uma regra do maker, o caminho do registro, um teto de stake) silenciaria o arquivo versionado nas quatro instâncias, por 14 dias, sem nada denunciar. A ordem foi invertida e há teste que trava qual dos dois é o último. **Na unit de uma rodada só o risco é maior e ficou escrito lá:** todo o perfil dela é `Environment=`, que perde para QUALQUER `EnvironmentFile`. E a nota do `Restart=always` nas duas units foi corrigida — ela dizia que um restart *continua a mesma rodada*, o que vale para o diário e **não** para a medida. **E o RESTART deixou de ser risco em aberto (sexto achado da revisão, consertado no mesmo dia).** O Codex viu o que eu não tinha visto: **com `Restart=always`, até a saída BEM SUCEDIDA reinicia.** As duas semanas terminavam, o processo encerrava limpo, e 10 s depois uma rodada NOVA começava sozinha, anexando ao mesmo diário, para sempre. Duas coisas mudaram: (1) as duas units passaram a `Restart=on-failure` — o `main` já devolve 0 no sucesso e 1 quando a rodada não produziu saída, então o código de saída já dizia a verdade e faltava a unit escutá-la; há teste que percorre o fonte, porque um `return 0` incondicional desfaria o conserto sem tocar na unit. (2) O leitor passou a **somar os trechos** em vez de só denunciar o restart: os campos da caixa são cumulativos desde a subida, então o último relato de cada trecho é o total daquele trecho, e a soma deles é a rodada. `parede_s` caindo corta o trecho; markout em ¢/share e ciclo de trabalho não somam (são médias) e entram ponderados pelos pesos que o próprio motor publica ao lado deles — shares e tempo de parede. **Nada é recalculado**, e o que se perde é real: o tempo fora do ar não foi observado e não conta, então a rodada leva MAIS de 14 dias de calendário para fechar 14 dias de medida, que é o número certo. Sem a soma, uma queda de rede no dia 7 jogava fora 14 dias de medida legítima — é o teste `test_os_dois_trechos_SOMAM_e_fecham_as_duas_semanas`. 4 mutações verificadas. **O que continua 🟡:** a caixa segue sem persistir, e a soma reconstrói a rodada do journal, não do disco — se o journal for rotacionado, o trecho rotacionado some com ele. **Sétimo e oitavo achados da revisão (2026-09-15), e o sétimo é outro defeito de MEDIDA na caixa:** (7) **o reenvio de um negócio de DEPOIS da cotação contava DUAS VEZES.** O filtro do carimbo do servidor pega o reenvio de um negócio ANTERIOR à cotação; não pega o de depois, que aconteceu mesmo, com a ordem no livro, e volta numa reconexão com carimbo de chegada novo e o mesmo carimbo de servidor. Ele consumia a perna outra vez e registrava markout em dobro, e **ao longo de 14 dias cada reconexão inflava execuções e custo**. O conserto usa o `transaction_hash`, que está no `last_trade_price` e é `[VERIFICADO]` no §6.1a — e **não sozinho**: uma transação pode varrer vários níveis e render mais de um evento com o mesmo hash e preços diferentes, então a identidade é (token, hash, preço, tamanho, lado); desduplicar só pelo hash descartaria a segunda perna de uma varredura, que é execução legítima nossa. Print **sem** hash PASSA: quem não reconhece deixa passar, porque os dois erros não são simétricos — contar de novo infla o custo (erra contra a rota) e descartar um print legítimo tira custo (erra a favor dela). **O sentido desta correção é oposto ao do achado 5:** aquele tirava execuções falsas que eram creditadas a nós, este tira custo em dobro — as duas mexem na linha de base da 4.2 e a medida continua a refazer. 4 mutações verificadas, e uma delas ensinou algo: a mutação *o wire para de trazer o `transaction_hash`* **não foi pega** pelos testes da caixa, porque eles usam um dublê de print — o `live/livros.py` podia parar de ler o campo e a desduplicação viraria um `None` silencioso, que deixa passar, sem nada falhar. Foi preciso um teste SOBRE O WIRE (`test_o_transaction_hash_do_WIRE_chega_ao_negocio`), que é exatamente a lição do `price_change` §6.1b. (8) **O fim da rodada não fecha a conta:** o `run` devolve o estado em memória, então markouts cujo horizonte de 5 s não passou ficam pendentes e os prints da última cadência de 15 s não chegam a ser olhados — os dois favorecem a rota. O leitor passou a IMPRIMIR quantas execuções ficaram sem markout e a dizer que o líquido é um **limite superior** por elas. 🟡 **falta: o motor fechar a conta antes de sair** — o conserto de verdade do (8) é no `run`, e ele não está feito; o que existe é a ressalva impressa. **Nona, décima e décima-primeira rodadas de revisão (2026-09-15), e duas delas são consequência dos meus próprios consertos:** (9) **o `.env` da máquina ainda vencia TODO o perfil** — inverter a ordem dos `EnvironmentFile` resolveu só as três regras, porque `EnvironmentFile` vence `Environment=` **sempre, não por ordem**, e o resto do perfil (`TOP_DE_POOLS`, os tetos de risco, o caminho do registro, o próprio `PULSEARB_MODE`) seguia em linhas `Environment=` da unit. Um `.env` com o caminho do registro daria às quatro instâncias o MESMO registro, que é exatamente a escrita rasgada que a separação existia para impedir. **A unit não tem mais nenhuma linha `Environment=`:** o perfil virou `deploy/rodadas/comum.env` (carregado depois do `.env`) e o registro de cada rodada foi para o `%i.env`, que é o último de todos. (10) **`Restart=on-failure` abriu um buraco que o `always` tapava por acidente:** uma exceção não-`OSError` no `passo` do maker fazia `laco_de_cotacao` retornar, e como o `run` espera com `FIRST_COMPLETED` isso encerra a rodada INTEIRA — o comentário que dizia *'a rodada segue sem cotar'* era falso. Com `falhou` vazio o processo saía com 0 e o systemd entendia *terminou bem*: as duas semanas paravam por uma exceção transitória e ninguém voltava. Marcar `falhou` conserta as três leituras de uma vez — código de saída 1, `on-failure` devolve a rodada, e o leitor recusa com `processo_falhou`. (11) **A soma dos trechos tinha um buraco que dava PASSA de graça:** trecho sem um campo era pulado e o valor do ÚLTIMO trecho ficava, enquanto `parede_s` somava os dois — **13 dias sem o maker no ar mais 1 dia medido fechavam as duas semanas com a conta de um dia só**. Parcela que falta agora zera o campo e cai no `campo_ausente`. **Duas das cinco mutações desta rodada não foram pegas na primeira tentativa** (o campo zerado e o `falhou` do laço), e isso quer dizer que eu tinha consertado os dois sem teste — os testes vieram depois, e só depois deles os consertos valem. **Décima-segunda rodada (2026-09-15), e ela desfaz a premissa das quatro paralelas:** com `Restart=on-failure`, `--duration 14d` é **reexecutado** — uma instância que caísse no dia 13 ganharia outros 14 e observaria 27 dias, terminando 13 dias depois das irmãs. Os rewards e o markout dela deixariam de cobrir o MESMO intervalo de mercado, que é a única razão de as quatro correrem juntas. O prazo virou **absoluto** (`--ate`, RFC3339 em UTC), escrito UMA vez no `comum.env` e portanto igual nas quatro por construção: sobrevive ao restart sem persistir nada e faz as quatro terminarem juntas. Instante sem fuso é RECUSADO (seria lido como hora local da VPS e a rodada acabaria na hora errada em silêncio), prazo já vencido encerra com saída 0 (restart depois do fim tem de encerrar de novo, não reerguer), e a variável nasce **vazia** — sem prazo combinado a unit não sobe, saindo com 2, e `RestartPreventExitStatus=2` impede o laço de restart eterno que `StartLimitIntervalSec=0` deixaria correr. **Mais dois da mesma rodada:** trecho sem `maker.regras` era FILTRADO FORA da conferência e tinha os rewards somados assim mesmo — medida de regra desconhecida entrando num PASSA; agora recusa. E o RUNBOOK §10 subia a unit de uma rodada só antes do §10.1c subir as quatro: **cinco processos, com o `@recolher` em duplicata**, um assinante de feed a mais que a medida de capacidade não contou — o §10.1c passou a mandar parar e desabilitar a antiga primeiro. **Décima-terceira e última rodada de revisão (o Codex esgotou o limite de uso), e o achado dela é o mais grave de todos — e era MEU:** o sono de 1 s só chamava `recolher_por_fill_toxico` **quando a pausa estava ligada**, e esse método é quem ingere os prints. Resultado: na rodada da pausa os prints eram vistos a cada segundo; nas outras três, só a cada 15 s. A `CaixaDoMaker` carimba `meio_no_fill` com o livro **do instante em que VÊ o print** e mede 5 s depois do negócio, então um fill logo após uma passada saía medido do segundo 1 ao 5 na rodada da pausa e do livro do segundo 15 ao 16 nas outras. **O INSTRUMENTO DE MARKOUT FICAVA DIFERENTE ENTRE CONTROLE E TRATAMENTO**, e a comparação de 14 dias mediria a diferença entre os instrumentos junto com a da regra — invalidando exatamente o desenho de quatro rodadas paralelas que este commit construiu. Ver print não muda o que o bot faz, muda quando a conta fecha: o método virou `ver_prints_entre_passadas`, roda **sempre**, e só o CANCELAMENTO olha o knob. Teste que compara as duas rodadas sobre o mesmo print e exige `meio_no_fill` e horizonte idênticos. **Mais dois da mesma rodada:** `pendentes_de_markout` passou a SOMAR entre trechos — eu tinha argumentado que não somava e estava errado, porque o que se lê é o último relato de CADA trecho e, num trecho que morreu, aqueles são fills cujo markout nunca fecha; sem somar, o fill perdido num trecho antigo sumia até da ressalva. E `--diario` pedido mas ilegível virava só um aviso, com o veredito seguindo para PASSA: **a conferência do prefixo `sombra-` era pulada em silêncio** e o ensaio saía declarado válido sem ninguém ter olhado o artefato de segurança dele (`diario_ilegivel`). **O RELÓGIO COMEÇOU A ANDAR em 2026-09-19 22:15:50 UTC**, prazo absoluto `PULSEARB_RODADA_TERMINA_EM=2026-10-03T22:15:48Z`, quatro instâncias `active running` com `NRestarts=0`. **A medida das primeiras 3 h 34 min (§10.1d do runbook) respondeu UMA das três perguntas de capacidade e deixou DUAS em aberto — é por isso que esta linha segue 🟡 e não vira ✅ no dia 3 de outubro por decurso de prazo.** **(a) Disco: ✅** 11.368.842 B somados em 3 h 34 = 3,04 MiB/h nas quatro, ≈ 1,0 GiB em 336 h contra 18 GB livres. **(b) Memória: ❓** 174 MiB disponíveis de 961, **swap zero**. **(c) CPU: ❓** carga **4,00 / 4,00 / 4,01 em 1 vCPU**. E a medida trouxe um número que ninguém tinha pedido: **601 linhas/h de `conexão caiu` nas quatro** (`ConnectionClosedError: no close frame received or sent`, `"close_origem":"cliente"`, nível WARNING — o nível ERRO deu **0**), contra 11/h medidas no §7.2 com um processo só. **Salto de 13×, não explicado.** Se a causa for CPU saturada, o processo sem fatia para responder o ping derruba a própria conexão, e a fila do escalonador entra no `cotacoes_repousando` e no `meio_no_fill` **com o nome do mercado** — a mesma confusão que a última rodada do Codex achou entre as instâncias, vinda agora da máquina em vez do calendário, e igualmente capaz de invalidar os 14 dias. **O QUE FALTA, ESCRITO:** rodar `ps -o stat,pcpu,pmem -C python` e `vmstat 5 4` na VPS e separar CPU (R, `wa` baixo) de disco (D, `wa` alto). Se for CPU, o ensaio não cabe em 1 vCPU e a saída já está no §10.1c — **base + uma regra**, 28 dias, ou uma máquina maior. **Enquanto isso estiver aberto, a gravação de 72 h do M2 NÃO sobe junto:** 174 MiB sem swap não acomodam um quinto processo, e um OOM que mate uma das rodadas abre nela um buraco que as irmãs não têm. **O DESEMPATE FOI FEITO EM 2026-09-20, E REPROVOU: ❌ a VPS de 1 vCPU não carrega quatro rodadas.** `ps` dá os quatro processos em estado **R** com **24,5 / 24,5 / 24,5 / 24,4 %CPU — soma 97,9% de um núcleo**; `vmstat` dá **`r=4` constante, `wa=0`, `id=0`**; OOM ainda em 0. O `wa=0` derruba a hipótese de disco. E **`r=4` com `id=0` prova que os 24,5% são TETO, não custo**: um processo que precisasse só disso dormiria depois de trabalhar e a fila média cairia para perto de 1 — ela não cai, então cada rodada quer mais CPU do que recebe. Isso explica as 601 reconexões/h sem mais nada: o laço de eventos não volta a tempo de responder o ping, e quem fecha é o nosso lado (`close_origem: cliente`). **CONSEQUÊNCIA: o ensaio iniciado em 2026-09-19 22:15 UTC não produz dado válido e será reiniciado.** O `cotacoes_repousando` e o `meio_no_fill` das quatro carregariam fila de escalonador junto com mercado, e isso não se corrige na análise — é o instrumento medindo a si mesmo, a mesma família de defeito que a décima-terceira rodada de revisão achou no código. **O QUE FALTA PARA ESCOLHER A SAÍDA, ESCRITO:** quanto UMA rodada consome sozinha. Com `id=0` o teto esconde a demanda, então não se sabe se **base + uma** (a saída do §10.1c) cabe nesta máquina nem que tamanho de VPS comprar. O ensaio que responde está no §10.1e do runbook — parar três, deixar a base sozinha 10 min, medir `%CPU` e a contagem de `conexão caiu`; essa contagem responde de quebra se há TAMBÉM limite do CLOB por IP, caso em que máquina maior não resolve sozinha. **MEDIDO NO MESMO DIA — uma rodada sozinha custa ~40% de um núcleo, e isso muda a saída.** Com três paradas e a `base` só, `vmstat` dá `us+sy` = 38, 38, 48 e `id` entre 52 e 62%; `STAT` volta de **R** para **S** e `r` cai de 4 constante para 0–1; `free` sobe de ~85 para ~505 MiB, ou **~140 MiB por rodada**. **ATENÇÃO À ARMADILHA que quase inverteu a leitura:** o `%CPU` do `ps` seguiu marcando 24,6% — porque ele é a média de TODA a vida do processo, e este tinha 5 h 55 min sob disputa contra 2 min sozinho. Quem lesse o `ps` concluiria que a rodada custa 24,5% com ou sem concorrência e compraria máquina errada; a demanda real só aparece no `vmstat`, que mede o intervalo. **A CONTA DE CAPACIDADE:** 1 rodada ~40% **medido** (cabe, `id` 52–62%); **base + uma regra ~80% — ❓ NÃO MEDIDO**; quatro ~160% — ❌ **medido e reprovado** (`id=0`, `r=4`). **CORREÇÃO DE MÉTODO, achada pelo Codex (P1) na revisão do #170 e aplicada no mesmo commit:** a primeira versão marcava 'base + uma regra' como ❌ dizendo que 80% sustentado com picos de 96% é 'a mesma beira' que derrubou as quatro. **Não se sustenta.** O regime que falhou pedia ~160% e mostrava `id=0`, sem folga nenhuma; 80–96% ainda tem CPU sobrando, e a 96% o laço de eventos continua sendo escalonado. O 96% saiu de dobrar a MAIOR de três amostras de 5 s. **Vale a regra deste quadro nos dois sentidos: ❌ é 'medido e reprovado', não 'estimado e reprovado'** — e o custo do erro era concreto, porque reprovar o plano B na conta empurra para o sequencial de 56 dias (que o §10.1c recusa) ou para comprar máquina, duas saídas caras escolhidas sem medida. O ensaio que decide está no §10.1f: subir a segunda rodada, 10 min, e ler `id`, `r` e a contagem de reconexão. **MAIS DOIS ACHADOS DO CODEX NA MESMA REVISÃO, os dois procedentes e conferidos na fonte antes de aceitar. (P2 — atribuição)** os 38–48% saíram do `us+sy` do `vmstat`, que é da MÁQUINA INTEIRA: parar as outras três não prova que o que sobrou é da `base`, e multiplicar isso por quatro dimensiona VPS errado. Estender a amostra não corrige — é erro de atribuição, não de ruído. O custo de uma rodada precisa sair por PID e por intervalo (`pidstat`, ou `utime+stime` do `/proc`), com uma linha de base do host medida com TUDO parado; o §10.1f passou a trazer os dois comandos. O veredito de saturação das quatro continua de pé, porque `id=0` e `r=4` são da máquina e é da máquina que se quer saber. **(P1 — artefatos, e no código é pior do que o relatado)** a unit passa `--diario` com caminho FIXO, então a unicidade por `O_EXCL` de `caminho_do_diario_da_rodada` não vale (ela é do caminho default) e o `_anotar` abre em **append**; e o `resumo_da_rodada_maker.py` corta o diário em trechos onde `parede_s` cai e **SOMA os trechos**. Logo, religar a `base` no mesmo arquivo somaria as ~6 h de quatro processos que o §10.1e declarou inválidas ao resultado dos 14 dias, **sem nenhum campo dizendo isso** — e a `pausa`, parada e religada, teria um buraco que a `base` não tem, que é a cobertura desigual que o §10.1c existe para impedir. **Medir capacidade NÃO inicia o ensaio:** o novo §10.1g manda afastar (não apagar) diários e registros de risco, escrever prazo NOVO e comum, e conferir com `ls -l data/diarios/` que os diários nasceram agora e vazios. **O que JÁ está decidido porque é aritmética: quatro rodadas pedem mais de um núcleo e nenhuma máquina de 1 vCPU entrega isso — o regime de quatro foi medido direto, com `id=0` e `r=4`, sem depender de atribuição.** **QUARTA RODADA DO CODEX NO #170, dois achados, os dois procedentes — e eles chegaram DEPOIS do merge, então viraram trabalho à parte. (P2 — a tabela contradizia o próprio texto)** a linha dizia "~40% **medido**" enquanto a seção acima já explicava que o `us+sy` é da máquina inteira, e o dimensionamento de VPS saía desse número. Como a carga do resto do host é ≥ 0, o que vale é `custo_da_rodada ≤ 38–48%` **na janela amostrada**: é TETO, não custo. As duas incertezas apontam para lados opostos — a atribuição faz o número ser alto demais, a amostra curta faz ser baixo demais. Logo **4 vCPU é folgado com certeza e pode ser folgado demais**: se o `pidstat` der 25% por rodada, 2 vCPU resolve. **Não se compra máquina por essa tabela.** **(P1 — o plano B são TRÊS janelas, não uma)** o procedimento subia `base` e `pausa` e parava; `recolher` e `ancora`, paradas na medida de capacidade, **nunca voltavam a ser agendadas** — o 4.2 sairia com resultado para UMA das três regras e nada dizia que faltava. São três regras, cada uma precisa de uma base no mesmo intervalo, logo **três janelas de 14 dias = 42 dias**, cada uma repetindo o §10.1g inteiro com uma base NOVA. **E o plano B dá regra contra base (limpo, é o que o 4.2 exige) mas NÃO dá regra contra regra** — `pausa` e `recolher` correriam com 28 dias de distância, e ranqueá-las mediria o mercado. **MEDIDO POR PID EM 2026-09-20 14:57 — e o pico é o que decide.** `pidstat 5 12` sobre a `base` sozinha: 48,0 · 42,8 · 32,8 · 37,2 · 32,2 · 39,6 · 50,0 · 46,8 · 40,8 · **66,2** · 40,8 · 33,8, **média 42,6%, pico 66,2%**; o laço do `/proc` numa janela seguinte confirma por outro caminho (média ~36%, pico 61%). **Isso fecha a pergunta de atribuição do P2** — o número é por processo, e os 38–48% do `vmstat` eram mesmo quase todos da rodada. **Mas ele abre outra: a rodada varia de 19% a 66%, e dimensionar pela média subdimensiona em quase 60%.** Como as quatro assinam os MESMOS mercados e reagem aos MESMOS eventos de livro, os picos tendem a coincidir — logo quatro pedem **264% no pico**, não 168%. **2 vCPU (200%) corta nos picos; são 4 vCPU (66% de utilização no pico), e a dúvida de 'folgado demais' morreu aqui.** E duas rodadas pedem 2 × 66% = **132%**, que não cabe em 1 vCPU — o que NÃO reprova o plano B (extrapolação não reprova), mas diz o que olhar no ensaio: não a média do `id`, e sim se ele encosta em 0 nos picos, que é justamente quando a cotação decide. **E UMA AFIRMAÇÃO MINHA FOI DESMENTIDA PELA MEDIDA:** eu escrevera que a CPU saturada explicava as reconexões. Contando `conexão caiu` na mesma unidade, mesmo `grep`, janelas de 20 min: **8 com as quatro disputando** contra **9 e 10** depois de parar três. **A reconexão não cai quando sobra CPU** — tem causa própria, ainda não achada, e máquina maior não a resolve. O salto de 13× que eu inferi nunca existiu: as 601/h vinham de um `grep` largo sobre as quatro unidades, e eu comparei duas contas diferentes e chamei a diferença de fenômeno. **O ENSAIO DE DUAS RODADAS FOI RODADO EM 2026-09-20 15:17 E 15:30, E REPROVOU — ❌, com o número que reprovou.** E reprovou pela coluna que eu NÃO tinha eleito como critério: os dois sinais que eu mandara olhar **passaram** (`id` nunca zerou, mínimo 21%; `r` ficou entre 0 e 2). O que reprova é o **`%wait` do `pidstat`** — tempo em que o processo está PRONTO e não recebe CPU, fila de escalonador pura: **0,38% sozinha → 16,70% e 35,97% com a `pausa`**, quase cem vezes, com intervalos de até 50,20%. **Por que o `id` não pegou:** ele é média de 5 s da máquina inteira, o `%wait` é do processo e conta o instante — duas rodadas reagindo ao MESMO evento de livro acordam JUNTAS, uma espera a outra nesses milissegundos, e o ocioso entre as rajadas enche o `id` de volta. **Média boa com fila nas rajadas é o pior caso, porque a rajada é exatamente quando a cotação decide** — e é a latência dela que o `cotacoes_repousando` e o `meio_no_fill` medem. **Logo o plano B do §10.1h morre, e com ele a última saída gratuita. Sobra: VPS de 4 vCPU (as quatro em paralelo, 14 dias, comparações limpas), uma rodada por vez (56 dias, que o §10.1c recusa por comparar regra com mercado), ou baratear a rodada (40% de um núcleo para cotar 60 mercados em SHADOW é muito, e `in` ~2.000–3.000/s com `cs` ~1.000/s sugerem que há o que cortar — mas é investigação inteira, sem garantia de caber em 1 vCPU no fim). A recomendação é a VPS, por risco: as outras duas gastam semanas para talvez chegar ao mesmo lugar, e o 4.2 é pré-requisito de LIVE.** **E A RECONEXÃO ESTÁ DESCARTADA COMO CPU, agora com janelas limpas:** 03:30–03:50 sob disputa das quatro deu **8**; solo deu **15** (05:00), **14** (09:00) e **8** (13:00). Solo é igual ou MAIOR, e a variação entre janelas solo é maior que a diferença para o regime de disputa — o que move a taxa é outra coisa, hora do dia ou agitação do mercado, nenhuma medida. Vira item próprio: ~30–45 reconexões/h por processo, `no close frame received or sent`; **e o `close_origem: cliente` que aparecia nessas linhas era DEFEITO DO REGISTRO, não dado** — `feeds/base.py` fazia `"servidor" if rcvd is not None else "cliente"`, carimbando cliente sempre que faltava frame recebido, inclusive quando não havia frame NENHUM (o caso de `no close frame received or sent` e o de um `OSError` de rede). Esse carimbo sustentou parte da hipótese de CPU que as janelas limpas derrubaram: **ausência de frame é ausência de medida**, e campo que finge saber vira gráfico e vira conclusão. Corrigido para três estados (`servidor`, `cliente`, `desconhecida`) com teste que falha se voltar, verificado por mutação. Para a investigação o ponto de partida muda: o suspeito passa a ser a camada de transporte — TCP/TLS cortado por intermediário, rede da VPS, ou o servidor fechando sem frame — e não o nosso laço de eventos; **máquina maior não resolve isso**, e enquanto não tiver causa entra como ressalva no relato de cobertura de qualquer rodada de 14 dias. **A CAUSA FOI ACHADA EM 2026-09-20 ~21:30, e ela vira o item do avesso: é o SERVIDOR que fecha, a cada ~300 s.** Com o código do #174 rodando, 60 min de `base` sozinha deram **24 de 24 `close_origem: servidor`** — nenhuma `cliente`, nenhuma `desconhecida`; o CLOB encerra e avisa. E os intervalos entre quedas: **nove dos treze não-nulos entre 299 e 306 s**, mais um de 592 (≈ 2 × 296), mais dez de 0 s que são rajadas de várias conexões no mesmo instante. Isso é período, e o período são **os cinco minutos exatos do ciclo `updown`**: o CLOB encerra a conexão quando o mercado de 5 min acaba. **Não é rede, não é intermediário, não é defeito nosso — é o ciclo do produto.** **E isso corrige uma frase que eu havia escrito horas antes** ('o giro de mercado do `updown` a cada 5 min não derruba nada'): a leitura do nosso código estava certa — `subscribe`/`unsubscribe` não reconectam — mas a conclusão sobre o fenômeno estava errada, porque eu olhei só um dos dois lados da conexão. Mesmo defeito de método que já apareceu duas vezes hoje: concluir sobre o que não se mediu. **CONSEQUÊNCIA: deixa de ser defeito e vira característica** — ~30–45 quedas/h é o que o ciclo produz, e nenhuma máquina, rede ou keepalive muda isso. Continua entrando na cobertura, porque reconexão é tempo sem livro, **mas a pergunta útil deixou de ser 'por que cai' e passou a ser 'quanto custa'**: o que fecha o item é o tempo entre a queda e a primeira mensagem depois dela, somado na hora. **O QUE FALTA, ESCRITO:** (1) o `close_code`, que separa encerramento limpo de mercado (1000/1001) de outra coisa que só parece periódica; (2) o resto dos carimbos módulo 300 — se concentrar num valor, as quedas estão presas ao relógio do mercado; espalhado significaria conexão que envelhece, outra causa com o mesmo período aparente. Os dois comandos estão no §10.1f-quater. **RODADOS — E OS DOIS DERRUBARAM A MINHA CONCLUSÃO, que era o objetivo do teste.** Os códigos: **20 × `1012 Service Restart` e 4 × `1013 Try Again Later`** — nenhum 1000 nem 1001, que seriam fim normal de mercado. E os carimbos módulo 300 saíram **espalhados de 1 a 295**, não concentrados: **as quedas NÃO estão presas ao relógio do mercado.** O período de ~300 s é o **tempo de vida da conexão** — a borda do CLOB recicla conexões por idade e anuncia como `1012`; a fase é a de cada conexão, por isso não alinha. Era exatamente a alternativa que eu tinha nomeado e mandado distinguir pelo módulo. **E a medida achou algo que É nosso:** o laço de `_run` zera o backoff a cada conexão boa e **não olhava o código do close**, então as quatro quedas com `1013` — o servidor dizendo que está sobrecarregado e pedindo para voltar mais tarde — foram respondidas em **~0,5 s**. Corrigido com piso de espera por código: **5 s para 1013, nenhum para 1012** (que diz que o serviço está voltando, e voltar rápido é o certo), aplicado como `max(backoff, piso)` para não encurtar um backoff já grande. Dois testes, o primeiro verificado por mutação. **E A REVISÃO DO #177 PÔS O PRÓPRIO DIAGNÓSTICO SOB SUSPEITA — terceira vez que este campo diz saber o que não sabe.** `_registrar_queda` atribuía a origem por `"servidor" if rcvd is not None`; num close que **NÓS** iniciamos o servidor responde com o frame dele, `rcvd` existe, e a queda saía carimbada **servidor**. E nós fechamos com **1012 de propósito** em `_derrubar_por_recusa` e `_escalar_se_sem_efeito`. Logo parte das '20 de 24 com 1012' pode ter sido nossa. Corrigido com `rcvd_then_sent`, com teste verificado por mutação. **O teste que decide não precisa de código novo:** as duas quedas nossas carregam razão própria no frame (`topico mudo apos reassinaturas`, `assinatura recusada pelo servidor`) e logam `derrubando a conexão` — um `grep` em `close_reason` resolve. **Outros dois achados da mesma revisão, os dois procedentes:** (1) o piso de 5 s era multiplicado pelo jitter de `[0.5, 1.5)`, então **metade das voltas dormia menos que o mínimo anunciado** — o piso passou a valer DEPOIS do jitter, num método público que o teste exerce de verdade; (2) eu tinha deixado o **1012 sem piso** dizendo que 'ele avisa que está voltando' — inverte o padrão, que diz que o serviço **está reiniciando** e recomenda voltar com atraso aleatório de 5 a 30 s, e deixava de fora o código de 20 das 24 quedas. Agora 1012 e 1013 têm piso, **mas só quando foi o SERVIDOR que fechou** — o nosso próprio 1012 existe para reconectar rápido. **E O `grep` EM `close_reason` RODOU: ERA NOSSO.** 22 das 30 quedas de uma hora trazem `close_reason: assinatura recusada pelo servidor` — o nosso `_derrubar_por_recusa`, com as 22 linhas de `derrubando a conexão` confirmando uma a uma. As outras 8 são `slow consumer: send buffer full`, do servidor e **já documentadas** em `live/shadow.py:340` desde 2026-09-14 (acontecem com consumidor vazio e no mesmo instante em três processos paralelos). **CAI:** o '24 de 24 do servidor' (era o carimbo quebrado), o 'CLOB recicla conexões por idade' (os 1012 eram os NOSSOS) e o período de 300 s como tempo de vida da conexão. **FICA DE PÉ:** que não é CPU, pelas janelas limpas. **O FENÔMENO DE VERDADE:** o servidor recusa a nossa assinatura 22×/h e nós derrubamos a conexão para refazê-la — **não é defeito de reconexão, é defeito de assinatura com a reconexão como sintoma**, e o contador `reconexoes_por_recusa` existe justamente para medi-lo. **E ISSO MUDA ONDE PROCURAR:** `_recusa_de_assinatura` é implementado em `feeds/rtds.py`, não no `poly_ws` — logo as 22 são provavelmente do **RTDS**, e todas as seções anteriores trataram as quedas como se fossem de um feed só. **MEDIDO: são o RTDS, e a causa tem nome.** Por conexão, 60 min: **8 `clob[updown]`** (os `slow consumer` conhecidos), **13 `rtds[shadow:0]`** e **12 `rtds[shadow:1]`** — ~25/h nas duas do RTDS. E a recusa crua é `statusCode 500 … ERROR #42P01 relation "__subscriptions" does not exist`: `undefined_table` do PostgreSQL, **o banco do RTDS sem a tabela de assinaturas**. O `API_NOTES` §6.2b já registrava essa mensagem desde a gravação M2_72H de **2026-09-09**, e ela está em `tests/fixtures/rtds_recusas_reais.json` como `recusa_500`. **A cadeia inteira:** RTDS responde 500 → `e_de_assinatura` classifica → `_derrubar_por_recusa` fecha com 1012 → o carimbo quebrado dizia "servidor" → eu li como "o CLOB recicla conexões". Nenhum elo era o que eu disse, e o primeiro é defeito do servidor deles, de onze dias atrás. **O QUE NÃO DECIDI, E POR QUÊ:** derrubar a conexão a cada 500 pode ser dano puro (o `42P01` é determinístico, reconectar não cria tabela, e o feed entrega dado entre as recusas — sugerindo que só a REASSINATURA bate no caminho quebrado) ou pode ser certo (se a assinatura não se registra, o tópico vai calar). **O log não separa os dois**, e mudar isso seria trocar uma decisão deliberada, testada e ancorada em fixture real por preferência minha — cheguei a escrever a mudança e a desfiz. O §10.1f-septies traz o ensaio que decide: uma instância de TESTE com a reassinatura periódica desligada, uma hora, contando quedas e tópicos mudos. **O CUSTO TEM LINHA DE BASE: 23,6 s sem livro por hora, 0,65% da hora** (2026-09-21, 60 min, 30 reconexões, média 0,79 s, pior 5,2 s; 9,2 s no `clob[updown]`, 7,3 e 7,1 nas duas do RTDS). **É limite inferior** — conta da queda até o `conectado`, sem a ida e volta da assinatura — **e é do código ANTERIOR ao #177**, porque a janela cobriu 00:37–01:37 e o restart foi às 01:36. Previsão para o código novo, ainda NÃO medida: ~70 s/h (2% da hora), porque ~8 das 30 quedas são `1013` do servidor e ganharam piso de 5 s. O §10.1f-octies traz o comando para fechar com a hora inteira no código novo. **O que segue em aberto é o CUSTO:** ~24 quedas/h × tempo até a primeira mensagem depois de cada uma = tempo sem livro por hora, e é esse número que entra no relato de cobertura das 14 dias. **O que NÃO muda:** a invalidade do ensaio de 19/09 se apoia só no `id=0` com `r=4`, e segue de pé. **O que falta:** a janela solo inteiramente limpa (as de 9 e 10 pegaram a transição) e o ensaio de duas rodadas. A escolha entre 42 dias de graça e 14 dias pagando VPS maior está no §10.1h, com a tabela dos dois lados; as duas dependem de o ensaio de duas rodadas passar. **O QUE AINDA FALTA, ESCRITO:** (1) os ~40% saem de TRÊS amostras de 5 s que já variam de 38 a 48 — antes de comprar hardware, deixar uma rodada sozinha 30–60 min e refazer o `vmstat`, porque quem satura é o pico, não a média; (2) a comparação de reconexões NÃO está feita — o `grep` de 8 min misturou ~5,7 min de disputa com ~2,3 min de solo, e as 601/h do §10.1d vieram de outro `grep`, mais largo, sobre as quatro unidades; os dois números não se comparam, e o §10.1f traz o par de janelas que compara. |
| 4.3 | LIVE começa no stake mínimo; aumentar só após 100 trades com expectativa positiva **PRÉ-REQUISITO NOVO, medido em 2026-09-18:** a máquina que opera tem de estar numa região em que a Polymarket permita operar. A VPS actual (Londres) recebe **403 `Trading restricted in your region`** a qualquer ordem (API_NOTES §17, item 3.5). Enquanto isso não mudar, o LIVE não começa — e não há linha de código que o resolva. | ⬜ |

**Piso de tempo:** mesmo que a captação seja consertada hoje e o veredito venha
positivo, são ~3 dias de gravação + M4 construído + 2 semanas de SHADOW antes
do primeiro dólar real.

---

## Segurança e infraestrutura (antes de qualquer credencial existir)

| # | Item | Estado |
|---|---|---|
| 5.1 | Carteira **dedicada**, só com o capital de operação em USDC na Polygon | 🟡 **metade executada em 2026-09-11 — a metade que não custa nada.** ✅ **carteira criada** (10/09) e ✅ **credenciais L2 derivadas** (11/09, `scripts/derivar_credenciais.py`, arquivo `0600`, segredo nunca impresso). A medida que destravou isso: o passo 6 **NÃO precisa de carteira fundeada** — o CLOB aceitou a assinatura L1 de uma carteira sem USDC, sem MATIC e sem allowance. Por isso o RUNBOOK foi reordenado: **6 e 7 antes de 3, 4 e 5**, porque descobrem de graça se a assinatura L1 é aceita, e travar no passo 6 *depois* de fundear seria dinheiro parado numa carteira quente esperando conserto. ⛔ **E a metade que falta está BLOQUEADA por onde a máquina está, não por falta de procedimento:** o CLOB recusa ordens desta região (403, API_NOTES §17). Fundear e aprovar allowances num host que não pode enviar ordem é pôr dinheiro e aprovações numa carteira quente sem contrapartida. Por isso o RUNBOOK §8.1 ganhou um **passo 0** em 2026-09-18: rodar o `smoke_ordem_assinada.py` do host que vai operar, com a carteira vazia, e só seguir para os passos 3, 4 e 5 se a recusa for de NEGÓCIO. Achado do Codex na revisão do PR #160: o pré-requisito estava registado no quadro e no API_NOTES, e ausente do caminho operacional — o runbook mandava fundear sem verificar. ⬜ **Falta o que move dinheiro:** fundear e aprovar as allowances de USDC e CTF — e é aí que está a armadilha que não avisa: sem elas a ordem é aceita pelo CLOB e falha na liquidação, com mensagem que não fala em allowance. Os endereços dos contratos vão ao API_NOTES `[VERIFICADO]` antes de qualquer transação. Procedimento completo em `RUNBOOK_VPS.md` §8.1: EOA (`signature_type=0`, API_NOTES §3), os 7 passos em ordem, e a armadilha que não avisa — **allowances de USDC e CTF setadas à mão antes da primeira ordem**, senão a ordem é aceita pelo CLOB e falha na liquidação, com mensagem que não fala em allowance. `CredenciaisL2.do_ambiente()` fecha o caminho das 4 variáveis L2 (antes só a chave privada tinha).  Os endereços dos contratos vão ao API_NOTES `[VERIFICADO]` antes de qualquer transação |
| 5.2 | Imagem Docker efetivamente construída | ✅ **O BUILD RODOU em 2026-09-08** — `colima` + `docker` instalados nesta máquina e a imagem construída pela primeira vez: **13 passos, 27,5 s, 336 MB**. Quatro das cinco verificações passaram, e três delas eram *acreditadas* até hoje: **(a)** o `pip install .` resolve de fato no `python:3.12-slim` (a leitura só tinha conferido wheels no PyPI); **(b)** o ENTRYPOINT **arranca** — `--help` responde, que é o modo de falha que o `test_5_2_imagem_do_recorder.py` até então apenas SIMULAVA; **(c)** roda como não-root com **UID 10001**, batendo com o Dockerfile; **(d)** escreve no **volume anônimo**, o que prova o conserto do `mkdir`+`chown`. ⚠️ **A quinta NÃO é verificável aqui, e isso é limitação de plataforma, não resultado:** o cenário de **bind mount** do RUNBOOK §5. No macOS com Colima o `/data` montado aparece como `root:root` dentro do container e ainda assim um processo UID 10001 escreve — a camada de mount traduz a escrita para o usuário do host e **não reproduz a semântica de UID do Linux**. Num VPS Linux de verdade quem manda é o dono no host, e o `chown 10001:10001` do RUNBOOK **continua necessário e continua não verificado**. Quem o verifica é o job de `deploy/ci-job-docker.yml`, que roda em `ubuntu-latest` — Linux real. **O JOB FOI APLICADO em 2026-09-12** — o escopo `workflow` entrou no token via `gh auth refresh -s workflow` (não foi preciso criar PAT: o login é OAuth do `gh`, e o refresh adiciona o escopo ao token existente), e o bloco `docker:` saiu de `deploy/ci-job-docker.yml` para dentro de `.github/workflows/ci.yml`. A cópia de espera foi **apagada no mesmo commit** — duas cópias do mesmo YAML divergem, e a que ninguém roda apodrece. **A primeira tentativa de aplicar quebrou o YAML e é um defeito que vale registrar:** recortei o bloco com `index("  docker:")`, e essa string aparece ANTES dentro do próprio comentário de instruções (*“da linha `  docker:` até o fim do arquivo”*) — cortei na prosa, não no job, e o `ci.yml` saiu inválido, o que teria derrubado também o job `testes` que já funcionava. Consertado buscando a **linha exata**, e o `test_o_ci_e_yaml_valido` agora guarda isso. Os 8 testes de `test_5_2_job_docker.py` passaram a ler o `ci.yml` em vez do arquivo de espera; o teste que existia só para falhar no dia da aplicação saiu, cumprido. ⬜ **O que AINDA não foi medido:** o resultado do build em Linux real. O job existe e é válido, mas só roda no próximo PR — e é ele quem responde se o `chown 10001:10001` do RUNBOOK é mesmo necessário, pergunta que o macOS com Colima não consegue responder. **Falta:** ver o job verde uma vez, e o deploy na VPS |
| 5.3 | **CI que roda `pytest` e `ruff` a cada push** | ✅ **RODANDO** — `.github/workflows/ci.yml`, check `testes` verde nos PRs #41/#42/#43 |
| 5.4 | **NTP/chrony verificado na máquina que opera** | ✅ **2026-08-30** — `risk/sincronia.py` pergunta ao daemon (systemd, chrony, macOS), 14 testes. **Não determinado conta como não sincronizado**, e o LIVE recusa por isso. Falta só habilitar NTP na VPS |

Sobre 5.3: até o PR #40 o único check que aparecia era o SonarCloud, que vem
do GitHub App e faz análise estática — **não executa a suíte**. Os testes só
rodavam na máquina de quem estava editando, e um commit que quebrasse o
backtest chegava ao `main` com o quality gate verde. Para um projeto que vai
mexer com dinheiro real, "passou no meu ambiente" não é verificação.

**Agora roda.** `.github/workflows/ci.yml` executa os mesmos alvos do
`make check` (`ruff check src tests scripts` + `pytest`) a cada push, e o check
`testes` saiu verde nos PRs #41, #42 e #43. Ele instala o extra `analise` junto
com `dev` de propósito — sem pyarrow o teste de `replay/columnar.py` cai num
`importorskip` e some do relatório, e a CI passaria rodando menos testes que a
máquina do desenvolvedor.

O achado que segurou o workflow antes — `C Security Rating on New Code` no
SonarCloud, com `permissions: contents: read` não resolvendo — não voltou a
aparecer. Fica o registro da hipótese nunca testada: supply-chain,
`actions/checkout@v5` e `actions/setup-python@v6` não fixadas por commit SHA.

**A contenção de LEITURA ganhou raiz própria em 2026-09-14.** A mensagem de
erro de `caminho_de_relatorio_lido` mandava definir
`PULSEARB_BACKTEST_OUTPUT_ROOT` para ler de outra raiz — variável de SAÍDA
liberando leitura, e liberando junto toda `caminho_de_escrita` do mesmo
processo, porque as duas partilhavam `raiz_de_saida()`. Agora
`PULSEARB_RELATORIOS_INPUT_ROOT` abre a leitura sem abrir a escrita (teste
confere os dois lados), e ausente cai na raiz de saída, que é o caso comum.
No mesmo commit `scripts/resumo_m2.py` deixou de ter a sua cópia da regra
(com `is_relative_to`, que o motor de taint do SonarCloud não reconhece) e
passou a chamar a função compartilhada — uma contenção, não duas. Achado
revisando o patch S2083 dos scripts novos da branch `soma-dos-lados-e-pools`,
que herdam a mesma mensagem e passam a herdar a variável certa ao rebasear.

---

## Quando é OK avançar

- **Para gravar 72 h:** ~~basta o Bloco 0 fechar~~ — **já foi gravado.** A
  rodada de 09/09 a 12/09 produziu 287,7 M registros íntegros, e o backtest
  sobre ela fechou o 4.1 (reprovando). O 0.8 segue ⏳ só por falta de publicar
  a contagem de silêncios, não por falta de gravação.
- **Para escrever o M4:** o Bloco 1 precisa dar veredito **positivo** para
  taker ou maker. Se der negativo, **o projeto para — e isso é sucesso**:
  custou 72 h de VPS em vez de meses de capital. (O M4 foi escrito assim mesmo,
  e isso está certo: ele é a máquina de medir sem arriscar. O que ele não
  autoriza é ligar o LIVE.)
- **Para ligar o LIVE:** Blocos 0 a 4 inteiros, sem exceção.

Hoje nenhum dos três está liberado.

## As pendências que realmente travam, e o que destrava cada uma

Todo o resto do quadro é ou trabalho já feito, ou trabalho que só depende de
tempo de máquina. Estas não — a primeira linha fica na tabela riscada, porque
saber o que deixou de travar é parte do estado:

| Pendência | Natureza | O que destrava |
|---|---|---|
| ~~**1.3 calibração**~~ **RESOLVIDO em 30/08** (ECE 0,0126–0,0493 nos cinco baldes) | era defeito de **variância**, não do sinal: 39–48× na variância, 6,3× no desvio | feito — a `V(t)` passou a ser MEDIDA em dia anterior ao avaliado (§2d-ter). **E com o conserto a borda sumiu:** `bandas_com_edge: []`, o que move a reprovação para 1.1 e 1.4 |
| **1.1 / 1.4 — a REGRA DE ENTRADA seleciona pior que o acaso** (bandas de −22,54 a −113,64; irrestrito −67,27) | **medido em 2026-08-31**, e a causa mudou de nome | **RODOU: `M2_DIRECAO_20260824.json`, 24 h, 688 janelas independentes.** `direcao_sem_fill` deu **acurácia 0,4157 com p = 1,16e−05** — a direção escolhida erra sistematicamente, sobre coorte que **não muda com o fill**. E o preditor **não é o culpado**: em 247.306 previsões ele sai calibrado (ECE 0,0126–0,0493, 20 faixas em todos os cinco baldes) e a taxa-base é 0,5043. Ou seja: prever, ele prevê; quem escolhe mal é a regra `edge = prob − preço > threshold`, que seleciona 688 momentos e acerta menos que sortear. **O conserto muda de lugar** — não adianta trocar o preditor, é a regra de entrada. **As DUAS correções óbvias foram testadas, e nenhuma serve.** (§2d-sexies) exigir convicção mínima não salva: nenhum limiar de 0,50 a 0,80 dá acurácia acima de 0,5 *com* amostra que sustente — o melhor (0,5833) sai de 24 apostas com p=0,54, e subir o limiar corta de 395 para 24 antes de corrigir a direção. (§2d-septies) **inverter é 2,4× PIOR**: −0,01477 contra −0,00618 por share. A razão é o preço, não a direção — a soma dos asks é 1,0210, e acertar 58 % pagando 0,5991 perde mais que acertar 42 % pagando 0,4219. **As duas pontas perdem: o spread de 2,1 % é maior que qualquer vantagem direcional.** **SEGUNDO DIA (§2d-octies, 25/08, 97 janelas, curva de 23/08):** a direção **reproduz** — 0,3711 com p=0,0148, errando como no dia 24. Mas **a inversão NÃO**: positiva (+0,058) no dia 25 e negativa (−0,015) no dia 24. Rodando só o dia 25 eu concluiria "inverter funciona", e estaria errado — no dia com 7× mais amostra o mesmo movimento perde. **O erro de direção é robusto; a lucratividade da inversão não é.** **O que NÃO está estabelecido:** que nenhuma regra funcione — foram testadas três |
| **1.5 profundidade** (p50 115 USDC a 3 ticks em 300 s, dia 24; 28 USDC em 4 h) | teto de **capacidade** do book, e **critério da rota TAKER** | nada sob nosso controle — é liquidez do mercado, e 115 contra 200 não sugere contestar o limiar. **MAS a leitura muda com a rota (medido 2026-08-31):** o mesmo book raso que impede o taker de mover 200 USDC é o que dá ao maker uma **fatia alta do pool**. Nossa cotação de 200 shares a ~0,50 vale ~100 USDC por lado; num livro cujo p50 a 3 ticks é 115 USDC, isso é ~46 % do total — que é **exatamente** a `fatia_media` de 0,457 que o relatório publica. O 1.5 reprova o taker e **não é critério do maker** (os do maker são 1.6 a 1.10). Book raso não é obstáculo para quem cota; é obstáculo para quem atravessa |
| ~~**1.10 fórmula de reward**~~ **CONFIRMADA em 30/08** — `S(v,s)=((v-s)/v)²×b` (quadrática em centavos, 1/min), com $1M em rewards para TWAP em agosto | fato externo — acessível na máquina Mac (docs.polymarket.com) | feito — API_NOTES §15.3. `analysis/rewards.py` corrigida. `resumo_m2.py` atualizado: critério 1.10 agora reporta **PASSA**. Falta re-rodar o M2.2 com dados reais: `./scripts/analisa_dia.sh 20260824 ~/pulsearb-dados ~/pulsearb-m2 relatorios/VARIANCIA_23AGO.json` (quarto arg adicionado em 2026-08-30) |

E uma última, que é metodológica e vale para qualquer resultado acima: **um dia
não é veredito**. A rodada de 30/08 é o primeiro número desta página com
separação de dias — a curva de variância vem de 23/08 e a avaliação é de 24/08,
com recusa em código se as datas se cruzarem. Isso cobre o preditor; não cobre
o resto. Qualquer edge que apareça daqui em diante continua precisando de dias
independentes para separar borda de sobreajuste.
