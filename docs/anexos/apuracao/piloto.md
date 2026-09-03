# T19 · Bateria piloto e dimensionamento

**Bateria:** `configs/bateria_piloto.yaml` · **Reproduzir:** `make piloto`
**Duas passadas, e a comparação entre elas é o §6:**

| | data | SUT | saída |
|---|---|---|---|
| 1ª | 23/08/2026 | como a T17 o deixou | `runs/piloto_2026-08-23/` |
| 2ª | 24/08/2026 | com o A17 — o agente sabe quanto orçamento resta | `runs/piloto_2026-08-24/` |

**Números crus:** `docs/anexos/resultados/piloto.json` (análise da 2ª) e `docs/anexos/resultados/overhead_mcp.json` (fronteira MCP).
**As seções 1–5 descrevem a 1ª passada**, que é onde os achados apareceram; o §6 mede o efeito da
correção e refaz o dimensionamento.

6 cenários de dev × 2 modelos × variante `base` × 2 `sample_seed` = **24 execuções**.
**24/24 concluíram**, zero falha de instrumento, zero run inválida — 49,9 min de relógio,
182 chamadas ao modelo, 183 chamadas de tool, 718k tokens de prompt e 32k de completação.

> **Divergência declarada do enunciado: 6 cenários, não 4.** A T19 pede "4 cenários × 2
> modelos × 2 seeds = 16"; o A2 diz que a taxonomia congela "logo após a T19 (bateria piloto
> nos 6 de dev)". Escolher 4 dos 6 deixaria dois cenários de dev sem execução real antes de um
> congelamento irreversível. Custo do desvio: ~18 min dentro de um tempo-caixa de 3 h.

---

## As três perguntas do enunciado

| # | Pergunta | Resposta | Veredito |
|---|---|---|---|
| 1 | `tempo_medio × 600` cabe em duas madrugadas? | **19,8 h** contra 16 h — e **13,5 h** depois do A17 (§6) | não cabia; **passa a caber** |
| 2 | `parse_erro` por modelo passa de 20%? | 14B **1,2%** · 8B **0,0%** | passa longe — nenhuma troca de modelo |
| 3 | Overhead do MCP por chamada | **0,47 ms** de mediana | desprezível — número registrado |

---

## 1 · Dimensionamento: não cabe, e o paralelismo não salva

| modelo | runs | duração/run mediana | média | faixa | `llm_call` mediana |
|---|---|---|---|---|---|
| `qwen3-14b` | 12 | **157,2 s** | 158,4 s | 36,3–243,4 s | 17,7 s |
| `qwen3-8b` | 12 | **93,3 s** | 91,3 s | 49,2–125,1 s | 8,8 s |

A extrapolação usa **mediana por modelo**, não média geral. A distribuição de duração é
assimétrica por construção — `budget_exceeded` trunca em cima, terminação precoce trunca
embaixo — e a matriz de `METRICAS §9.2` não é metade de cada modelo: a principal é 18×2×8, a
de mutantes é 6×**1**×4×5.

| bateria | matriz | exec | horas |
|---|---|---|---|
| principal | 18 test × 2 modelos × 1 variante × 8 `sample_seed` | 288 | 10,0 |
| mutantes | 6 × 1 modelo × 4 MUT × 5 seeds | 120 | 3,1 |
| metamórfica | perturbações sobre 6 cenários × 2 modelos | 96 | 3,3 |
| ambiente | 6 × 2 modelos × 8 `env_seed` | 96 | 3,3 |
| **total** | | **600** | **19,8** |

> **Premissa da linha "mutantes":** `METRICAS §9.2` diz "1 modelo" sem nomear qual. A conta usa
> o **8B**, que é o barato. Com o 14B a linha vai de 3,1 h para 5,2 h e o total para 21,9 h.

**O enunciado da T19 fala em 544 execuções; são 600.** Ele foi escrito antes de a tabela de
baterias existir. A conta acima usa a tabela.

### O paralelismo entre os dois modelos foi medido, e é pior

Era o único caminho que não custava nada cientificamente: dois modelos carregados são duas
instâncias separadas — será que rodam de fato ao mesmo tempo? **Não.** As mesmas duas células,
no mesmo servidor, medidas de relógio:

| | tempo de parede | resultado |
|---|---|---|
| `--paralelismo 1` | **222 s** | 2 runs concluídas |
| `--paralelismo 2` | **446 s** | 1 run perdida em `falha_do_instrumento` |

Duas vezes mais lento, e com perda de run: sob contenção de GPU cada chamada individual passa
dos 300 s de `TIMEOUT_PADRAO_S` do cliente de inferência, e a contenção vira run perdida em vez
de run lenta. **A bateria roda com `--paralelismo 1`.** Isto confirma por medição direta o que a
T0b e a T18 já diziam por argumento: GPU única, o ganho previsto vinha de prefix cache e ele não
aparece aqui.

### As opções de corte, com a aritmética

Para sair de 19,8 h e chegar às 16 h de duas madrugadas, faltam **3,8 h**:

| opção | efeito | total | custo científico |
|---|---|---|---|
| test 18 → **11 cenários** | principal cai a 176 exec | **15,9 h** | corta 7 cenários do que o corpus mede; mexe no denominador do recall |
| `sample_seed` 8 → **5** | principal cai a 180 exec | **16,0 h** | encosta no limite, e é o eixo que sustenta o `pass^k` |
| cortar metamórfica **ou** ambiente | −3,3 h | 16,5 h | **não basta sozinho** |
| cortar as **duas** | −6,7 h | 13,2 h | mata robustez e variância atribuível à API |
| **três madrugadas** | 6,6 h por noite | 19,8 h | nada do corpus muda |

**A decisão é sua (A16).** O que a piloto acrescenta ao que a T0b já dizia é que a conta agora
é de ponta a ponta e não por passo: a T0b estimou 17,9–25,0 h a partir de 27,5 s/passo, e a
medida real caiu dentro da faixa, em 19,8 h.

---

## 2 · `parse_erro`: 0,5% no total — mas só depois de uma correção

**1 erro em 182 chamadas.** Por modelo: 14B **1/86 (1,2%)**, 8B **0/96 (0,0%)**. A linha de corte
do enunciado é 20%; nenhum dos dois chega perto, e **nenhuma troca de modelo é necessária**.

O único erro foi `json_invalido: Unterminated string`, com `finish_reason=stop` a 171 tokens de
completação — o decodificador emitiu fim-de-texto no meio de uma string apesar da gramática. A
retentativa recuperou e a run terminou `ok`. É quirk do servidor, não do modelo; fica registrado
porque não é raro na escala da bateria: 600 runs fazem ~4.600 chamadas, e a mesma taxa daria
~25 ocorrências — cada uma custando uma retentativa, e uma segunda falha matando a run.

### O que esse número esconde, e por que ele não é comparável ao de antes

**Antes de uma mudança no prompt, o `parse_erro` era ~100%.** As três primeiras runs — nos dois
modelos — morreram na iteração 1 pelo mesmo motivo, e o motivo é de desenho:

* `esquema_estrito` põe **todo** campo em `required` (é o que o modo `strict` exige), então a
  gramática obriga `acao` **e** `resposta` a estarem presentes e permite os dois preenchidos;
* a regra "exatamente um dos dois" vive só no `model_validator` do Pydantic, que roda **depois**
  da geração;
* o modelo, obrigado a emitir as duas chaves, preenchia as duas. Run morta na primeira iteração.

**A saída natural não existe neste stack.** O certo seria um `anyOf` de duas formas fechadas.
`anyOf` entre objetos — no topo do esquema ou aninhado — **pendura o compilador de gramática do
LM Studio indefinidamente**: medido em 23/08 nos dois modelos, com um esquema mínimo de dois
campos. Não é tamanho do nosso esquema; é o motor.

Então a regra desceu para o prompt (`prompts/agente_v1.md` e `prompts/agente_mut4.md`, que o
teste de espelhamento mantém alinhados), e
`test_a_exclusao_entre_acao_e_resposta_nao_cabe_no_esquema_e_por_isso_mora_no_prompt` impede que
ela suma de lá numa reescrita.

> **Limitação declarada, e ela contradiz uma docstring do projeto.** `sut/llm.py` diz que, com a
> gramática ligada, "o `parse_erro` que sobra é falha de schema real, não de formatação". Enquanto
> o motor não fizer alternação de objetos, **parte do `parse_erro` mede obediência a instrução** —
> e a comparação entre modelos herda esse confundimento. Os 0,5% de hoje são o resíduo depois de a
> instrução funcionar, não a taxa de violação de schema.

---

## 3 · Overhead do MCP: 0,47 ms por chamada

`scripts/medir_overhead_mcp.py`, 100 chamadas (20 repetições × 5 tools de leitura), servidor em
memória e `RunContext` novo a cada chamada — cache desligado na marra, porque repetir a mesma
chamada mediria o caminho do cache.

| | mediana | média | p95 | máx |
|---|---|---|---|---|
| HTTP puro (`ToolResult.latencia_ms`) | 3,00 ms | — | — | — |
| fronteira inteira (`call_tool` cronometrado no cliente) | 3,55 ms | — | — | — |
| **diferença = overhead do MCP** | **0,47 ms** | 0,46 ms | 0,88 ms | 1,21 ms |

A diferença cobre a fronteira inteira, não só o transporte: serialização do pedido, streams em
memória, despacho do `call_tool`, validação de argumentos, gate, classificação da resposta,
emissão dos dois eventos de trace e serialização da volta.

**Contra o tempo de run:** uma run mediana faz ~7,6 chamadas de tool e gasta 93–157 s. O MCP
responde por **~3,6 ms**, ou **0,003%** do tempo de run. O enunciado manda investigar acima de 5%;
estamos quatro ordens de grandeza abaixo. **O que domina é a latência do modelo** — 8,8 s por
chamada no 8B, 17,7 s no 14B.

> **Ressalva de medição:** `ToolResult.latencia_ms` é inteiro em milissegundos, então o
> subtraendo tem quantização de ±0,5 ms — da ordem do próprio resultado. O número sustenta
> "sub-milissegundo, desprezível"; **não** sustenta "0,47 e não 0,60".

Esta é a resposta pronta para *"MCP não deixa tudo mais lento?"*: **não, e a medida existe.**

---

## 4 · O achado que não estava no enunciado: 18 de 24 runs não terminam

| modelo | `ok` | `budget_exceeded` |
|---|---|---|
| `qwen3-14b` | 6 | 6 |
| `qwen3-8b` | **0** | **12** |

**O 8B nunca produziu `final_answer` em 12 execuções.** O limite que morde é
`max_iterations = 8` — `max_tool_calls = 12` nunca foi atingido (máximo observado: 10 chamadas).

Três coisas se somam:

1. **O agente não sabe quanto lhe resta.** O laço de `sut/agent.py` não informa a iteração atual
   nem o teto ao modelo. Nada no histórico diz "conclua".
2. **Repetição idêntica não tem freio.** `_excedeu_o_endpoint` libera explicitamente a chamada
   igual (`_chave_dos_args(args) in vistos` → segue), porque ela é cache hit e sai de graça. Mas
   ela **queima uma iteração**. Em 16 das 24 runs houve ao menos uma repetição idêntica; num caso
   o 8B chamou `get_current_user` cinco vezes com `{}`, aprendendo nada a cada volta.
3. **O gabarito pede menos do que o orçamento dá.** Os 6 cenários de dev esperam de 2 a 6 tools;
   o orçamento de 8 iterações é folgado para investigar e apertado para investigar *e* concluir.

**Isto é comportamento medível, e o instrumento foi desenhado para medi-lo** — P5 (redundância /
loop / estouro de budget) é classe de falha declarada em `METRICAS §N2.5`, severidade S3. O
problema não é o `budget_exceeded` existir; é a **proporção**. `METRICAS` trata S3 como "desconto
pequeno · não afeta `pass^k`", o que pressupõe que exista um resultado a descontar. Com 75% das
runs sem `final_answer`, o judge da T20 pontuaria o quarto que sobrou — e no 8B, nada.

**Não mexi nisto.** Alterar `max_iterations`, ou passar o orçamento restante ao modelo, muda a
coluna `base` do experimento (T17) e o que a bateria principal mede. É decisão de escopo, não de
implementação, e está registrada como ponto aberto no `DECISOES.md`.

**Quando termina, termina bem.** Nas 6 runs `ok`, as 6 tiveram `citacoes_validas=True` — nenhuma
citação inventada — e as decisões são plausíveis: `perguntar` no `aut_03` (a "pergunta que parece
ordem"), `escalar` no `cen_09`, `orientar` nos demais.

---

## 5 · O que quase matou a piloto, e não estava no enunciado

### `PARALLEL 4` do LM Studio pendura a chamada em vez de errar

Com os quatro slots de concorrência que o LM Studio carrega por padrão, o KV cache de 8192 é
dividido entre eles e um prompt de ~2,6k tokens — 18 schemas de tool na janela — não cabe. A
chamada **não erra: ela pendura.** 300 s de timeout do cliente, `n_llm_calls=0`,
`falha_do_instrumento` no manifesto. A mesma chamada, no mesmo modelo carregado com
`--parallel 1 --context-length 16384`, volta em **17 s** — contra 79 s antes.

```
lms load qwen3-14b-mlx --context-length 16384 --parallel 1 --gpu max -y
lms load qwen3-8b-mlx  --context-length 16384 --parallel 1 --gpu max -y
```

### O TTL do carregamento sob demanda desfaz essa configuração sozinho

Modelo carregado **sob demanda** pelo LM Studio nasce com o default — `PARALLEL 4`, contexto
8192, TTL de 1 h — e o TTL descarrega o modelo depois de uma hora ociosa. Numa bateria noturna
isso é: as primeiras horas rodam na configuração certa, um intervalo ocioso descarrega o modelo,
e a recarga automática volta **exatamente com a configuração que pendura**. Observado nesta
sessão, entre a piloto e a medida de paralelismo.

**Conferir `lms ps` antes de cada bateria**, e carregar explicitamente com `--parallel 1`.
Nenhum campo do manifesto captura isso: ele grava a `ModelConfig` que o cliente pediu, não como o
servidor carregou o modelo. Um `context_window` de 16384 no manifesto continua verdadeiro
enquanto o servidor serve 8192/4 por slot.

---

## Ressalvas

* **`sample_seed` no 8B não é reprodutível.** A pré-checagem reconfirmou o achado da T0b: o 14B
  devolve texto idêntico para a mesma seed, o 8B não. `ModelConfig.seed` vai a `None` no 8B e a
  irreprodutibilidade é limitação declarada. A repetição aconteceu, mas não se refaz.
* **n = 2 por célula.** Tudo aqui é dimensionamento, não resultado. "O 8B nunca terminou" é
  0 de 12 execuções em 6 cenários — forte para uma piloto, insuficiente para `pass^k`.
* **A extrapolação é condicional ao orçamento atual.** 18 das 24 runs gastaram as 8 iterações;
  são o teto de tempo, não o caso médio. Se `max_iterations` mudar, a conta muda junto — para
  cima se subir, e **pouco** para baixo se as runs passarem a terminar: nas 6 runs `ok` do 14B a
  mediana foi 138,6 s contra 187,4 s das que estouraram, apenas 26% mais rápido.
* **Um cenário, um mundo.** `env_seed` é canônica por cenário (`CENARIOS §2.3`); a piloto não
  varia esse eixo. A variância aqui é do modelo, não do ambiente.

---

## 6 · A segunda passada (A17): o que mudou e o que o número faz

A 1ª passada fechou com 18 de 24 runs em `budget_exceeded` e o 8B sem uma única `final_answer`
(§4). A correção do **A17** não alargou teto nenhum — `max_iterations` continua 8 e
`max_tool_calls` continua 12. Ela acrescentou **duas informações** ao que o modelo lê de volta:

* `[orçamento] iteração 3 de 8 · 5 de 12 chamadas de tool usadas` em toda observação, com o
  aviso de que esgotar encerra a run **sem resposta ao usuário**;
* `[repetida] esta chamada a get_asset é idêntica a uma que você já fez` quando a chamada repete.

A repetição **não é bloqueada**, e isso é desenho: `n2._n_redundantes` recalcula a redundância do
trace contando `ToolCall` idênticos, então um harness que recusasse a chamada tiraria o evento do
trace e o instrumento relataria ausência de laço porque foi ele quem impediu o laço.

Mesmo recorte, mesmo servidor, mesma configuração de carga. **Só o SUT mudou.**

| | 1ª passada | 2ª passada |
|---|---|---|
| runs com `final_answer` | **6/24** | **12/24** |
| — no `qwen3-8b` | **0/12** | **6/12** |
| — no `qwen3-14b` | 6/12 | 6/12 |
| duração mediana `qwen3-14b` | 157,2 s | **110,1 s** |
| duração mediana `qwen3-8b` | 93,3 s | **61,2 s** |
| `parse_erro` | 1/182 (0,5%) | 2/159 (1,3%) |
| **extrapolação para 600 execuções** | **19,8 h** | **13,5 h** |

**O 8B saiu de zero.** É o número que mais importa: sem `final_answer` não há o que o judge da
T20 pontue, e um dos dois modelos do eixo de H2 contribuía com nada.

### A queda de tempo é do agente, não da máquina — verificado

A latência mediana por chamada caiu 17,7 s → 10,6 s no 14B, o que o SUT não deveria causar. A
verificação separa as duas explicações:

| | ms por token gerado | tokens gerados por chamada |
|---|---|---|
| `qwen3-14b` | 100,2 → 91,4 (**−9%**) | 177 → 116 (**−34%**) |
| `qwen3-8b` | 58,7 → 55,8 (**−5%**) | 150 → 141 (−6%) |

O servidor está praticamente igual; **o que encolheu foi o que o modelo escreve** — e, no 8B, o
número de chamadas por run (8,0 → 6,1), porque as runs acabam antes. O 14B escrever 34% menos por
passo é efeito colateral não previsto: empurrado a concluir, ele racionaliza menos.

**Extrapolação conservadora:** atribuindo **toda** a deriva de ms/token à máquina e não à
mudança, o total vai a **14,5 h** — ainda dentro das 16 h, com ~1,5 h de margem em vez de 2,5 h.

### Duas coisas pioraram, e as duas ficam declaradas

**Citação inventada apareceu.** `citacoes_validas` era 6/6 na 1ª passada e é **10/12** na 2ª: duas
runs citaram `tool_call_id` que não existe no trace. Mais runs concluindo significa mais
oportunidade de fabricar, e é exatamente a falha que a rubrica do judge existe para pegar — mas o
número desce de 100% e isso não se esconde.

**A repetição de chamada da hidratação dobrou, e o aviso não a conteve** — 20 → 44 ocorrências,
23 de 24 runs. A repetição **do que o próprio modelo já pediu** ficou praticamente estável (9 →
12): o aviso não piorou nada, e também não resolveu nada.

O que dobrou tem causa estrutural, não comportamental: **a hidratação chama `get_asset` e
`get_current_user` em `iteration=0` e o modelo nunca vê os `tool_call_id` delas** — o resultado
chega renderizado dentro de `{contexto}`, não como observação de tool. Quando o prompt exige citar
`tool_call_id` e o orçamento empurra para concluir, a única forma de o modelo obter um id citável
sobre o ativo ou sobre o usuário é **chamar a tool de novo**. Ele não está repetindo por
desatenção; está comprando uma citação.

Isto conecta com o risco já registrado sobre a hidratação (a N1.1 credita as duas chamadas ao
agente, e `iteration=0` é o único marcador que as separa). **Consequência a decidir antes da
bateria principal:** a redundância P5 que a N2.3 vai contar tem uma fração que é artefato do
desenho da hidratação, não comportamento do agente. Ou a hidratação expõe os ids ao modelo, ou a
N2.3 desconta repetição de chamada hidratada, ou o número sai inflado com a ressalva escrita.

### O que isto faz com o A16

Com 13,5 h medidas (14,5 h na leitura conservadora) contra as 16 h de duas madrugadas, **nenhum
corte é necessário**: nem cenário de test, nem `sample_seed`, nem bateria inteira. A tabela de
opções de corte do §1 continua no documento porque ela é o registro de como a conta era feita — e
porque a margem é de ~1,5 h, o que **não** cobre uma noite em que o servidor esteja mais lento.

## 7 · A terceira e a quarta passadas: a hidratação expõe os ids

A consequência que o §6 deixou para decidir foi decidida assim: **a hidratação expõe os
`tool_call_id` ao modelo.** As outras duas saídas foram recusadas com motivo — descontar a
repetição hidratada na N2.3 esconderia uma chamada que o agente de fato gasta (o instrumento
relatando uma ausência que ele próprio produziu), e sair com o número inflado deixaria a P5, que é
o que a INS mede, dependendo de nota de rodapé.

A mudança está em `_contexto_renderizado`: cada bloco do contexto pré-carregado passa a sair
agrupado sob o id que o sustenta. Ela **não** mexe no `prompts/agente_v1.md`, de propósito —
escrever a instrução no template mudaria o `prompt_sha`, que rotula a coluna do experimento, e
obrigaria a reetiquetar uma variante que não mudou de prompt.

### As quatro passadas, lado a lado

Mesmo recorte nas quatro: 6 cenários de dev × 2 modelos × 2 seeds, só a variante `base`, mesmo
servidor.

| | 1ª (23/08) | 2ª (A17) | 3ª (A18) | 4ª (A18, rótulo seco) |
|---|---|---|---|---|
| repetição de chamada **hidratada** | 20 | 44 | **9** | 16 |
| runs afetadas | 14/24 | 22/24 | **8/24** | 10/24 |
| repetição **do que o modelo pediu** | 9 | 12 | 8 | 10 |
| `citacoes_validas` | 6/6 | 10/12 | **9/9** | **10/10** |
| `final_answer` | 6/24 | 12/24 | 9/24 | 10/24 |
| `pensamento` (mediana) | 301 ch | 231 ch | 385 ch | 406 ch |
| tokens por chamada | 161 | 126 | 202 | 183 |
| ms por token gerado | 84,0 | 86,0 | 82,8 | 83,4 |
| extrapolação das 600 | 19,8 h | 13,5 h | 21,3 h | 17,3 h |

### O que estas duas passadas estabelecem

**A repetição era compra de citação, e o diagnóstico se confirma.** Com o id disponível ela cai de
44 para 9–16, e a repetição do que o próprio modelo pediu — o controle — não se move. A citação
inventada, que era o outro lado da mesma moeda, zera: 9/9 e 10/10 contra 10/12.

**A quarta passada existe porque a terceira produziu uma hipótese, e ela foi refutada.** Na 3ª o
`pensamento` cresceu 67% e passou a mencionar `tc_` em 23 de 157 passos; a hipótese era que a
oração *"cite este id em vez de chamar a tool de novo"* convidava o modelo a deliberar. A 4ª rodou
o rótulo seco (`tool → id:`) e separou as duas coisas: a menção a `tc_` caiu (23/157 → 4/150), mas
o `pensamento` ficou **mais longo** (385 → 406 chars) e a repetição **subiu** (9 → 16). A
instrução causava a narração e não causava o tamanho — e estava segurando repetição de verdade.
Ela foi mantida.

### O que estas duas passadas NÃO estabelecem

**A extrapolação de tempo não é separável de ruído neste tamanho de amostra.** As quatro passadas
deram 19,8 · 13,5 · 21,3 · 17,3 h. Com n=24, temperatura 0,7 e o 8B com `honra_seed: false`, a
dispersão basal engole diferenças de poucas horas, e nenhuma delas deve ser atribuída a uma
mudança específica do SUT. O que é robusto é o lado grosso: **as duas últimas passadas ficaram
acima das 16 h disponíveis.**

O mesmo vale para o `final_answer` (12 → 9 → 10): três runs de diferença cabem dentro de um
desvio-padrão e não caracterizam regressão nem melhora.

### O que isto faz com o A16, de novo

**O A16 volta a estar aberto.** Com 17,3 h na passada mais recente contra as 16 h de duas
madrugadas, a tabela de opções de corte do §1 volta à mesa — e desta vez sem a folga que o §6
tinha declarado. A quantidade a cortar não é conhecida com precisão pelo motivo acima; o que se
sabe é que ela é da ordem de 1,5 a 5 h.

### Ressalva declarada da escolha

A instrução no rótulo é parte do harness, e a variante `sem_hidratacao` não recebe equivalente. O
eixo `hidratacao × sem_hidratacao` passa a carregar meia diferença a mais que a hidratação em si —
mesma natureza da ressalva do §6 sobre o MUT3 enxergar o corte de orçamento.

### Um achado lateral que fica em observação

Na 4ª passada o **`parse_erro` do 14B foi a 7,6 %** (6 de 79 chamadas, 5 runs afetadas), contra
0 % do 8B na mesma passada e ~1 % nas passadas anteriores. O limiar de inviabilidade do enunciado
da T19 é 20 %, então nada muda agora — mas o 14B é metade do eixo de H2 e a direção merece
vigilância na bateria principal.
