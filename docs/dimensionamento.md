# Dimensionamento das cinco baterias e a aritmética do corte

**Decisão em aberto: A16.** Este documento não a fecha — prepara a conta e faz **uma**
recomendação (§4). A decisão é do operador.

**Fonte dos números:** `docs/piloto.json` (4ª passada, `runs/piloto_2026-08-24c`) e
`docs/piloto.md §§1–7`. **Nada aqui foi executado**: os manifestos foram carregados com
`tapieval.runner.matriz.carregar_bateria` e conferidos célula a célula com `--dry-run`; as horas
são extrapolação de mediana medida.

**Recurso escasso:** ~16 h de GPU, em duas madrugadas de ~8 h. As baterias locais competem por
uma GPU única (`--paralelismo 1`, medido: com 2 as mesmas duas células levaram 446 s contra
222 s **e** uma run se perdeu). A bateria de referência roda na nuvem e **não** disputa esse
recurso — ela disputa RPD, que não é o gargalo desta noite.

---

## 1 · A conta, por bateria

### Entrada medida (4ª passada, n=24)

| modelo | runs | duração/run **mediana** | média | faixa | `llm_call` mediana |
|---|---|---|---|---|---|
| `qwen3-14b` | 12 | **142,0 s** | 153,8 s | 58,6–245,2 s | 19,2 s |
| `qwen3-8b` | 12 | **78,3 s** | 86,6 s | 24,4–152,9 s | 10,5 s |

Mediana e não média: a distribuição é assimétrica por construção — `budget_exceeded` trunca em
cima, terminação precoce trunca embaixo. E **por modelo**, não geral, porque a matriz não é
metade de cada um: a de mutantes roda em 1 modelo só.

### As cinco baterias

| # | bateria | matriz declarada | células | mistura | horas |
|---|---|---|---|---|---|
| 1 | **principal** | 18 **test** × 2 mod × `base` × 8 `sample_seed` | **288** | 144 + 144 | **8,81** |
| 2 | **mutantes** | 6 **dev** × 1 mod (8B) × 4 MUT × 5 seeds | **120** | 0 + 120 | **2,61** |
| 3 | **metamórfica** | perturbações × 6 dev × 2 mod | **96** nominal | 48 + 48 | **2,94** |
| | | *realizável (§2.2)* | *50–60* | | *1,47–1,84* |
| 4 | **ambiente** | 6 dev × 2 mod × `env_seed` válidas | **90** (não 96, §2.1) | 45 + 45 | **2,75** |
| 5 | **referência** | 6 dev × 1 fronteira × 4 seeds | **24** | nuvem | **~0,3 fora da GPU** |
| | **total na GPU** | | **594** | | **17,11** |

`horas = (n_14b × 142,0 + n_8b × 78,3) / 3600`. A linha 3 usa o nominal de `METRICAS §9.2` para
que a conta continue comparável com a de `docs/piloto.json`; a linha em itálico é a contagem
real, e as duas aparecem porque **cortar uma bateria com base num número inflado é cortar pelo
motivo errado**.

**17,11 h contra 16 h → faltam cortar 1,11 h**, se a noite correr na velocidade da 4ª passada.
Ela pode não correr — ver §5.

### O que a conta **não** inclui

* **Troca de modelo:** zero, e é achado da piloto. Os dois SUTs ficam carregados juntos
  (5 GB + 9 GB nos ~16 GB efetivos), como nas quatro passadas. Se tivessem de alternar, a ordem
  de `Bateria.expandir` (cenário → modelo → variante → seed) forçaria 36 recargas só na
  principal.
* **Judge (N3):** roda **depois**, sobre traces gravados, na free tier. Não é madrugada de GPU.
* **35 rotulagens humanas (N4.1):** tempo humano, não de máquina. Cortá-las não compraria um
  minuto de madrugada — e é por isso que a proibição de cortá-las não custa nada aqui.
* **Overhead do MCP:** 0,47 ms por chamada. ~4.600 chamadas na bateria inteira ≈ 2 s. Ignorável.
* **`parse_erro` e retentativa:** na taxa da 4ª passada (14B a 7,6 %) as retentativas já estão
  dentro das medianas medidas, porque as medianas vêm de runs que as sofreram.

---

## 2 · Duas matrizes de `METRICAS §9.2` não fecham, e é preciso dizer qual é a real

### 2.1 Ambiente: 6 × 2 × 8 = 96 **não é realizável**. A realizável é **90**.

`CENARIOS §8.3` é a restrição: só cenário **política-dependente** aceita `env_seed` livre. Para
o **dado-dependente**, seed fora da lista de válidas não testa robustez — troca o cenário por
outro. Em AUT-01, `analyses` em `inconclusive` faz o cenário deixar de ser um negativo
verdadeiro; a lacuna que ele preenche desaparece.

Contado nos YAMLs de `scenarios/` (canônica **incluída**, porque ela é o ponto de comparação):

| cenário de dev | natureza | `env_seed` | equivalentes | usáveis |
|---|---|---|---|---|
| `aut_01_barulho_sem_desvio` | dado | s001 | 7 | **8** |
| `aut_03_pergunta_que_parece_ordem` | **política** | s002 | 7 (livre: 330/1000) | **8** |
| `aut_06_premissa_falsa` | dado | s004 | 7 | **8** |
| `cen_04_lubrificacao_sem_baseline` | dado | s004 | 6 | **7** |
| `cen_06_diagnosticos_divergentes` | dado | s003 | 6 | **7** |
| `cen_09_cobertura_do_modelo` | dado | s017 | 6 | **7** |
| | | | **por modelo** | **45** |

**45 × 2 modelos = 90 execuções, 2,75 h.** Três dos seis trazem 6 equivalentes e não 7; forçar a
oitava significaria sortear fora da lista, e a lista é o resultado da varredura de 1000 seeds do
`CENARIOS §8.4`, que exige `complete` em **todos** os recursos da trajetória esperada.

**O limite mais fundo é sobre H4, não sobre aritmética.** Dos 5 política-dependentes do corpus
(AUT-03, AUT-04, AUT-05, AUT-07, CEN-15), **só AUT-03 está em dev**. Nos outros cinco cenários
desta bateria a `env_seed` varia dentro de mundos escolhidos justamente para **preservar** o
gabarito — o que se mede ali é variação de `notes` e de forma, não robustez a mundo degradado.
**A H4 sai desta bateria apoiada em 1 cenário de 6.** Trocar para os 5 política-dependentes
daria H4 forte e violaria o split: três deles são de test.

### 2.2 Metamórfica: o "~96" não sai de uma contagem. A contagem real é **50–60**.

96 = 6 × 2 × 8, mas as perturbações são **cinco** e nem as cinco se aplicam aos seis cenários.
P3 (`env_seed` degradante) está bloqueada pelo mesmo eixo ausente da §2.1. P2 (permissão menor)
só é interpretável onde a decisão esperada envolve ação com permissão em jogo — `aut_03`,
`cen_06`, `cen_09`; em `aut_01` a decisão já é "sem desvio". P5 (ativo sadio) exige um ativo
equivalente que não existe para os seis. P1 e P4 se aplicam aos seis, com `aut_06` já sendo o
cenário de premissa falsa.

Teto 4 × 6 × 2 = 48 células perturbadas, ~38 com o filtro de aplicabilidade, mais 12 de
referência → **50 a 60 execuções, 1,47–1,84 h**.

### 2.3 A coluna `base` falta na bateria de mutantes

INS.9 é "fração de MUT1–4 distinguida **do original**". A matriz de `METRICAS §9.2` lista só os
quatro mutantes: não há coluna de controle. `configs/bateria_mutantes.yaml` reproduz a matriz da
spec como está — 120 — e deixa a lacuna escrita. Fechá-la custa **+30 runs, +0,65 h**
(6 × 1 × 5 variantes × 5 seeds = 150). Ver §6.

---

## 3 · A tabela de cortes

Ordem de preferência já decidida no projeto: (1) ambiente · (2) metamórfica · (3) MUT3 ·
(4) `sample_seed` 8 → 5. **Nunca cortar as 35 rotulagens humanas** — sem elas o judge não tem
lastro, e a INS.6 (κ judge × humano) deixa de existir; além disso elas não compram madrugada.

A coluna que decide é a última, não a primeira.

| # | corte | economia | escopo restante | **o que deixa de ser sustentável** |
|---|---|---|---|---|
| 1 | **cortar ambiente** | **−2,75 h** | 14,36 h *(12,95–13,26 com a metamórfica real)* | **H4 inteira.** Some a decomposição de variância de `METRICAS §7.2`: `pass^8` com `env_seed` fixo continua, `pass^8` com `env_seed` livre não existe, e a área entre as curvas — que É a inconsistência atribuível à plataforma — deixa de ser mensurável. Some também INS.8 lido "por mundo". Custo real menor que o nominal: a H4 que se perde já era de 1 cenário em 6 (§2.1). |
| 2 | **cortar metamórfica** | **−2,94 h** (nominal) · −1,5 a −1,8 h (real) | 11,42 h | **Robustez e invariâncias.** Sem P1 não há evidência sobre sensibilidade a superfície linguística; sem P2 o respeito a permissão (D1) é medido só onde o cenário já o testa de propósito, nunca sob perturbação; sem P4 a sicofancia (C2) fica sem teste dirigido; sem P5 o viés de confirmação (C1) idem. Nenhuma delas some da **taxonomia** — somem da **evidência**. |
| 3 | **cortar MUT3** (mantendo MUT1, MUT2, MUT4) | **−0,65 h** nominal, e **menos na prática** | 10,77 h | **Metade do apoio da classe P na curva de INS.9.** Sobra MUT1 como único ponto de P contra dois de C. Duas ressalvas empurram em direções opostas: (a) a economia é superestimada — MUT3 roda com `max_tool_calls: 3` e é o mutante de runs mais curtas, então 0,65 h é teto, não valor; (b) MUT3 é o único mutante **já confundido** pelo A17/A18 — o SUT informa o orçamento restante, então sob MUT3 o agente *enxerga* o corte de 12 → 3 em vez de bater nele às cegas. É o mutante mais barato de perder cientificamente e o que menos economiza. |
| 4 | **`sample_seed` 8 → 5** | **−3,31 h** | 13,81 h (sozinho) | **O `pass^8` vira `pass^5`, e é o resultado central.** `pass^k` decai rápido e o valor de k **é** a afirmação: com 80 % de acerto, `pass^8 ≈ 0,17` e `pass^5 ≈ 0,33` — o relatório passa a apresentar um número duas vezes mais otimista sobre a mesma confiabilidade, e a comparação com o τ-bench, que é de onde a métrica vem, muda de eixo. O IC bootstrap de INS.2 (o número que testa H0) também perde precisão. É o eixo que `matriz.py` marca em mensagem de erro como "o único que não se corta para caber no tempo". |
| — | **cortar as 35 rotulagens humanas** | **0,00 h** | 17,11 h | Não é corte: é tempo humano, não de GPU. Sem elas não há INS.6, não há a segunda fonte de verdade-terreno de `METRICAS §7.1` e a divergência entre a curva dos mutantes e a das falhas espontâneas — que é um achado por si — deixa de ser calculável. **Nunca.** |

### Combinações, e como cada uma se comporta numa noite ruim

O escopo é fixo; o que varia é a velocidade da noite. As colunas abaixo aplicam ao mesmo escopo
o fator de cada uma das quatro passadas (19,8 / 13,5 / 21,3 / 17,3 h para as ~600 execuções).

⚠️ **Refeita em 30/08: uma linha desta tabela estava inflada.** A versão anterior media TODOS os
escopos com a metamórfica **nominal** (96 células, 2,94 h) — o número que a própria §2.2 diz não
ser realizável. Contada pelas 50–60 células que de fato se constroem (1,53–1,84 h), cortar **só o
ambiente** não estoura por 1,7 h: fica **na linha** dos 16 h. A recomendação do §4 não muda; o
motivo dela muda, e isso importa.

| escopo | 4ª (17,3) | 3ª (21,3) — **a pior** | 1ª (19,8) | 2ª (13,5) | cabe em 16 h? |
|---|---|---|---|---|---|
| tudo (metamórfica nominal) | 17,11 | **21,07** | 19,59 | 13,35 | **não** |
| tudo (metamórfica real) | 15,71–16,01 | **19,34–19,71** | 17,98–18,33 | 12,26–12,49 | **não** |
| − ambiente (metamórfica nominal) | 14,36 | 17,68 | 16,43 | 11,21 | não |
| **− ambiente (metamórfica real)** | 12,95–13,26 | **15,95–16,32** | 14,82–15,17 | 10,11–10,35 | **na linha** |
| − ambiente (metam. real) **+ coluna `base`** | 13,60–13,91 | **16,75–17,13** | 15,57–15,92 | 10,62–10,85 | **não** |
| **− ambiente − metamórfica** | 11,42 | **14,06** | 13,07 | 8,91 | **sim, sempre** |
| − ambiente − metamórfica **+ coluna `base`** | 12,07 | 14,87 | 13,82 | 9,42 | **sim** |
| − ambiente − metamórfica − MUT3 | 10,77 | 13,26 | 12,33 | 8,40 | sim |
| só `sample_seed` 8 → 5 | 13,81 | **17,00** | 15,80 | 10,78 | **não numa noite ruim** |

*(A §2.2 arredondou o piso da metamórfica real para 1,47 h; a mistura 25 + 25 dá **1,53 h**.
Nenhuma conclusão depende dessa diferença.)*

**O que a tabela corrigida diz, e é diferente do que a anterior dizia:**

1. **Pelo tempo puro, manter a metamórfica é empate técnico, não estouro.** Na pior noite
   observada ela erra os 16 h por 0 a 20 minutos, e cabe nas outras três passadas. Quem decidir
   por tempo, e só por tempo, pode legitimamente mantê-la — a tabela antiga não permitia essa
   leitura porque cobrava dela 1,4 h que não existem.
2. **A coluna `base` dos mutantes desempata** (§2.3, +0,65 h). É a única pendência que muda uma
   métrica que **vai** ser reportada — a INS.9 —, e com ela dentro manter a metamórfica volta a
   estourar (16,75–17,13 h na pior noite) enquanto cortar as duas fica em 14,87 h.
3. **E nenhuma das duas roda hoje**, que é o argumento que não é de tempo e por isso não cabe
   nesta tabela: `Bateria.expandir` (`runner/matriz.py`) tem quatro eixos — cenários × modelos ×
   variantes × `sample_seeds`. Não há eixo de `env_seed` nem de perturbação, e `scenarios/` não
   tem um único YAML perturbado. O ambiente precisa do eixo (ou de 45 YAMLs clonados só para
   variar a seed); a metamórfica precisa de ~24 YAMLs derivados, da regra de derivação de
   gabarito em código, e do mesmo eixo ausente para a P3.

**A recomendação do §4 vale, com o motivo 1 corrigido:** o que exclui as duas baterias não é a
aritmética da madrugada — é que elas não existem como código. A aritmética apenas deixa de
oferecer um caminho barato para mantê-las.

---

## 4 · Recomendação

✅ **RATIFICADA em 30/08 — é a decisão do A16, e a execução das T24–26 saiu do bloqueio.**
O item (a) da margem (a coluna `base` dos mutantes, +0,65 h) **não** foi incluído junto: ele muda
um manifesto já validado célula a célula, e como o runner retoma por célula, acrescentá-lo depois
custa as 30 runs novas e nada de retrabalho. Fica na mesa, não fechado.

> **Cortar a bateria de ambiente e a bateria metamórfica. Manter `pass^8`, os quatro mutantes,
> a bateria de referência e as 35 rotulagens humanas.**
>
> **Escopo: 408 execuções na GPU + 24 na nuvem · 11,42 h · margem de 4,58 h (29 %).**

Três motivos, na ordem em que pesam:

**1. É o único escopo que sobrevive à pior noite observada COM a coluna `base` dentro.** 14,87 h
contra 16 h no fator da 3ª passada. ⚠️ **Corrigido em 30/08:** cortar só o ambiente **não** dá as
17,68 h que este parágrafo afirmava — aquele número media a metamórfica pelas 96 células nominais
da §2.2, e pelas 50–60 realizáveis o mesmo escopo dá 15,95–16,32 h, que é empate na linha. O que o
desempata pelo tempo é a coluna `base` (+0,65 h, §2.3), que leva o escopo com metamórfica a
16,75–17,13 h. Com uma dispersão de passada para passada de ±25 %, uma margem de 10 % não é
margem, é sorte — e aqui nem 10 % havia.

**2. Nenhuma das duas é construível em duas noites**, e este é o argumento que fecha a questão.
Não é só tempo de GPU: a bateria de ambiente exige um eixo `env_seeds` em `runner/matriz.py` que
não existe (§5.1); a metamórfica exige ~24 YAMLs derivados, a regra de derivação de gabarito
escrita em código, **e** o mesmo eixo para P3. Cortá-las tira da rota crítica um trabalho de
engenharia que hoje não tem dono nem prazo — e mantê-las na tabela seria dimensionar uma bateria
que não pode rodar.

**3. As duas são as que menos entregam por hora gasta.** A H4 que a bateria de ambiente
sustentaria já nasce apoiada em 1 cenário de 6 (§2.1) — o limite não é de tempo, é do corpus, e
mais uma noite não o resolve. A metamórfica entrega hipóteses (n=1 por condição), não achados.

**Elas viram trabalho futuro declarado, não silêncio.** Ambas as matrizes estão escritas por
extenso nos cabeçalhos de `configs/bateria_ambiente.yaml` e `configs/bateria_metamorfica.yaml`,
com o bloqueio de código nomeado. Quem retomar não recomeça.

**O que fazer com as 4,58 h de margem, em ordem:** (a) fechar a lacuna da coluna `base` dos
mutantes (+0,65 h, §2.3) — é a única das pendências que muda uma métrica que **vai** ser
reportada, a INS.9; (b) nada. Margem gasta é margem que não existe.

---

## 5 · A margem de erro, e ela é grande

**Estas extrapolações não separam diferenças de poucas horas de ruído.** As quatro passadas da
piloto deram **19,8 · 13,5 · 21,3 · 17,3 h** para o mesmo escopo nominal de ~600 execuções.
Com n=24, `temperature=0,7` e o 8B com `honra_seed: false`, a dispersão basal engole a
diferença — e `docs/piloto.md §7` é explícito em que **nenhuma dessas variações deve ser
atribuída a uma mudança específica do SUT**.

Portanto:

* **Não leia "17,11 h" como 17,11 h.** Leia como "por volta de 17, entre ~13 e ~21".
* **O que é robusto** é o lado grosso: **as duas passadas mais recentes ficaram acima das 16 h
  disponíveis**, e o déficit é da ordem de **1,5 a 5 h**, não conhecido com mais precisão.
* **Não decida entre dois cortes por uma diferença de meia hora.** Decida por (a) sobrevivência
  à pior noite observada e (b) o que a segunda coluna da tabela §3 diz que se perde.
* O que **não** é ruído: a diferença entre 288 e 180 execuções na principal, ou entre incluir e
  não incluir uma bateria inteira. Diferenças de escopo são exatas; a taxa é que é ruidosa.

Três fontes de erro conhecidas e não modeladas, todas empurrando **para cima**:

1. `budget_exceeded` foi 13 de 24 runs na 4ª passada. Cada mudança futura no SUT que faça mais
   runs concluírem também as faz durarem mais.
2. O `parse_erro` do 14B foi a **7,6 %** na 4ª passada, contra ~1 % nas anteriores. Longe do
   limiar de 20 %, mas cada erro custa uma retentativa e a direção merece vigilância.
3. A piloto roda 6 cenários de dev; a principal roda 18 de test, que não têm por que ter a mesma
   distribuição de comprimento de trajetória.

---

## 6 · Divergências entre os documentos e o código, e decisões a ratificar

**Nenhuma foi resolvida mexendo em `src/`.** Todas estão declaradas nos cabeçalhos dos
manifestos correspondentes.

### 6.1 O carregador da bateria é `runner/matriz.py`, não `runner/manifesto.py`

`manifesto.py` é o **schema do `runs/<id>/manifest.json`** — o que a bateria declarou e o que
rodou. Quem lê e valida o YAML de configuração é `runner/matriz.py::carregar_bateria`. Os cinco
manifestos foram validados contra ele.

### 6.2 Não há campo para declarar o judge congelado

`CAMPOS_DA_BATERIA` recusa campo desconhecido no topo do YAML — de propósito, porque chave com
erro de grafia descartada em silêncio faria a bateria rodar outra matriz. Consequência:
**`judge: configs/judge_frozen.json` não pode ser escrito em campo.** A declaração está no
cabeçalho dos cinco manifestos, com "rodar sem ele é erro" em caixa alta.

*Extensão mínima, quando houver quem a faça:* acrescentar `judge` a `CAMPOS_DA_BATERIA`, um
`judge: Path` em `Bateria`, e recusar o carregamento se o arquivo não existir — para que a
bateria morra no `--dry-run` e não às 4 da manhã.

**Ratificar:** a instrução original oferecia "estender o carregador ou ajustar a spec"; a
instrução de escopo proibia tocar em `src/`. Segui a proibição de escopo e documentei a extensão.

### 6.3 Não há eixo de `env_seed` — a bateria de ambiente está bloqueada por código

A seed do ambiente é lida do YAML do cenário e entra na célula como constante
(`Celula.run_id` usa `cenario.env_seed`). A própria docstring de `matriz.py` prevê a bateria de
ambiente ("não precisará renomear célula nenhuma para caber"), mas o eixo nunca foi implementado.

`configs/bateria_ambiente.yaml` declara, por isso, **o braço canônico** (12 células) e carrega a
matriz completa de 90 no cabeçalho. A extensão mínima está escrita lá, em 4 itens — e o item 4
importa: a restrição do `CENARIOS §8.3` tem de morar no carregador, senão vive só num comentário.

### 6.4 Não há eixo de perturbação — a bateria metamórfica está bloqueada por conteúdo

E não deveria haver eixo: perturbação muda `solicitacao`, `permissoes_usuario`, `asset_id` e
`estado_esperado`. São cenários derivados, e o lugar deles é `scenarios/`. O que falta é a
**regra de derivação executável**; sem ela, "gabarito derivado por regra" vira curadoria manual
disfarçada — e o barato da bateria era exatamente não haver curadoria nova.

### 6.5 O SUT de referência não pode falar com a nuvem pelo cliente atual

`sut/llm.py` declara em docstring que "não conhece provedor pago, não lê chave de API de ambiente
nenhum e não tem fallback para nuvem", e `ClienteDeInferencia._corpo` não monta `Authorization`.
A promessa é o que garante que o SUT medido é o modelo local que o manifesto declara.

**Saída recomendada:** um `ClienteDeReferencia` ao lado, como `scoring/judge_llm.py` já é para o
judge, reaproveitando o que é protocolo (`esquema_estrito`, `RespostaDoModelo`) e não o que é
política. **Ratificar antes de rodar a bateria 5.**

### 6.6 O SUT de referência não pode ser o mesmo modelo do judge

`ARQUITETURA §13` põe o judge na free tier com o critério "o maior disponível, **≠ dos SUTs**",
pelo motivo escrito: juiz igual ao réu prefere as próprias respostas. O `MODELO_PADRAO` do judge
é `gemini-3.6-flash`. Se o SUT de referência for o mesmo, **o judge julga a si mesmo — na única
linha que serve de teto.**

**Decisão que tomei sozinho e que precisa de ratificação:** `configs/bateria_referencia.yaml`
declara `gemini-3.6-pro`. O raciocínio é o do próprio `judge_llm.py` invertido — o judge é Flash
porque são ~1.400 chamadas e o Pro não cabe no RPD; esta bateria são ~200 chamadas e cabe.
**O id tem de ser conferido contra o catálogo da free tier na noite, e tem de ser um snapshot
datado, nunca um alias `-latest`.**

### 6.7 RPD: ~200 (referência) + ~1.400 (judge) não cabem no mesmo dia

A conta do A1 dá ~1.400 chamadas de judge na free tier. A bateria de referência são 24 runs ×
~8 chamadas ≈ 200. Somadas passam do teto diário de ~1.500. **Têm de cair em dias diferentes** —
o que é fácil, porque o judge roda depois, sobre traces gravados.

### 6.8 A bateria de mutantes não tem coluna de controle

Ver §2.3. Declarada como a spec pede (120), com a lacuna e as três saídas escritas no cabeçalho
de `configs/bateria_mutantes.yaml`. **Recomendação: acrescentar `base` (+30 runs, +0,65 h).**

### 6.9 O braço canônico do ambiente e o de referência da metamórfica são as mesmas 12 células

Ambos são `dev × 2 modelos × base × seed 11`, cada cenário na sua `env_seed` canônica. Se as
duas baterias rodarem, roda-se **um** dos dois arquivos — 12 runs (~0,37 h), não 24.

### 6.10 Decisões menores tomadas sozinho

* **As oito `sample_seed` da principal:** `[11, 23, 42, 77, 101, 137, 199, 251]`. As quatro
  primeiras são as da T0b (`SEEDS_DO_BLOCO_B`), o que mantém a bateria comparável com a linha de
  base de tool calling e com as quatro passadas da piloto. As quatro seguintes foram fixadas
  **antes** de qualquer resultado da principal existir e não se re-sorteiam — a propriedade que
  importa não é a aritmética delas, é terem sido escolhidas a priori.
* **Mutantes e referência usam prefixos dessa lista** (5 e 4 seeds), para que as células fiquem
  pareáveis com as da principal e da piloto em vez de comparáveis só em distribuição.
* **`paralelismo: 1` nos quatro manifestos locais; `2` na referência**, onde o gargalo é RPD e
  latência de rede, não a GPU única.
* **`timeout_s: 900` nos locais, `300` na referência**: modelo servido não pendura como o LM
  Studio pendurava; o que acontece lá é 429 ou corte de rede, e esperar 15 min por isso só queima
  relógio.
* **`experiment_id` das baterias 3 e 4 diz `braco_canonico` / `braco_referencia`**, para que
  ninguém leia `runs/ambiente_.../` como se fosse a bateria de ambiente inteira.
* **A tabela de cortes do `docs/piloto.md §1` não foi reescrita.** O próprio `§6` daquele
  documento declara que ela "continua no documento porque ela é o registro de como a conta era
  feita". A conta atualizada é a deste arquivo; a de lá é história, e reescrevê-la apagaria o
  registro de como a estimativa se moveu entre as quatro passadas.

---

## 7 · Como rodar, depois de decidido

```bash
# 1. `configs/judge_frozen.json` TEM de existir antes de qualquer linha abaixo.

# 2. Os dois SUTs carregados juntos, com --parallel 1 (achado da piloto: com os 4 slots
#    padrão do LM Studio o KV cache não comporta o prompt e a chamada PENDURA em vez de errar).
lms load qwen3-14b-mlx --context-length 16384 --parallel 1 --gpu max -y
lms load qwen3-8b-mlx  --context-length 16384 --parallel 1 --gpu max -y

# 3. A API do parceiro no ar.
make api

# 4. Conferir a matriz antes de gastar a madrugada — é barato.
.venv/bin/python -m tapieval.runner --manifest configs/bateria_principal.yaml --dry-run
.venv/bin/python -m tapieval.runner --manifest configs/bateria_mutantes.yaml  --dry-run

# 5. Noite 1 — a principal. Retomável: célula com registro não roda de novo.
.venv/bin/python -m tapieval.runner --manifest configs/bateria_principal.yaml --paralelismo 1

# 6. Noite 2 — mutantes; depois a referência (nuvem, não disputa GPU, outro dia de RPD).
.venv/bin/python -m tapieval.runner --manifest configs/bateria_mutantes.yaml   --paralelismo 1
.venv/bin/python -m tapieval.runner --manifest configs/bateria_referencia.yaml
```

Saída esperada do `--dry-run` — **atualizada em 30/08, depois do corte do A16 e da coluna
`base`**, e conferida contra a tabela do §1:

```
principal    18 cenários × 2 modelos × 1 variantes × 8 seeds = 288 células
mutantes      6 cenários × 1 modelos × 5 variantes × 5 seeds = 150 células   ← `base` + 4 MUT
referencia    6 cenários × 1 modelos × 1 variantes × 4 seeds =  24 células
```

**São três, e não cinco.** `metamorfica` e `ambiente` saíram no corte do A16 (30/08) e não rodam:
os manifestos ficam no repositório com a matriz por extenso e o bloqueio de código nomeado, como
trabalho futuro declarado. Rodá-los hoje produziria o braço reduzido de 12 células cada, que não é
a bateria — é o que sobra dela sem os eixos que `runner/matriz.py` não tem.

**A linha `mutantes` mudou de 120 para 150** quando a coluna `base` foi ratificada (§6.8, item (a)
da margem). Quem conferir contra uma cópia antiga desta página vai achar que a matriz cresceu
sozinha; cresceu por decisão, e está registrada no cabeçalho do YAML.

⚠️ **E o `model_id` da referência mudou.** O `gemini-3.6-pro` que o §6.6 declarava **não existe**
no catálogo — conferido em 30/08 com `scripts/checar_catalogo_vertex.py`, 404 nas quatro grafias,
com os dois flash respondendo 200 na mesma passada (controle passou: é negativo real). O manifesto
passou a `gemini-3.7-flash`, que é o único id aceito **≠ do judge** e já era o `MODELO_PADRAO` de
`sut/referencia.py`. **A ressalva não sumiu, foi confirmada:** um flash como teto é teto mais baixo
que um pro, o que encolhe a distância até os Qwen3 locais e faz os SUTs locais parecerem
relativamente melhores — viés na direção confortável, e ele vai para as limitações da T32.
