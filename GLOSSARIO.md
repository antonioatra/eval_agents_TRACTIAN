# Glossário do projeto

O vocabulário do TAPI-eval: o que cada termo significa **aqui**, e por que a distinção existe.
Escrito em 17/08 porque metade das decisões do projeto travavam em ambiguidade de nome — duas
funções chamadas `sucesso_binario`, dois sentidos de "conflito", `seq` confundido com ordem de
linha.

Quem lê o case começa por aqui. Os documentos de fundo são `docs/ARQUITETURA.md` (como o sistema é
feito), `CENARIOS.md` (o corpus) e `METRICAS.md` (a régua).

---

## 1 · `seq` — o que é e por que dá tanto trabalho

**`seq` é um contador inteiro que numera os eventos de uma execução, na ordem em que aconteceram.**
Nada além disso. Todo evento do trace nasce com um: `seq: 1`, `seq: 2`, `seq: 3`…
(`schema/trace.py:117` — *"ordem absoluta dentro da run"*).

**Por que não usar a ordem das linhas do arquivo.** O trace é um `.jsonl` — um evento por linha —
e **duas peças diferentes escrevem nele**: o servidor MCP (que registra `tool_call`, `tool_result`,
`gate`) e o harness do agente (que registra `llm_call`, `decision`, `final_answer`). Duas peças
escrevendo no mesmo arquivo não garantem que a ordem das linhas seja a ordem dos fatos.

**Por que não usar o relógio (`ts`).** Porque são **dois relógios** — quando servidor e cliente
são processos separados, comparar timestamps entre eles não significa nada. É a decisão 8 de
`ARQUITETURA §5`: *"Dois emissores, uma ordem."*

Então: **`seq` é a única fonte de ordem confiável**, e o reader ordena por ele
(`schema/reader.py:50`), nunca pela linha nem pelo tempo.

### Onde isso deixa de ser detalhe e vira métrica

Três coisas dependem de `seq` para existir:

| Quem | O que pergunta | Como usa o `seq` |
|---|---|---|
| **N1.5 / gate** | "a ação foi aprovada **antes** de ser executada?" | `gate.seq < tool_call.seq` |
| **N2.1 (aderência causal)** | "ele leu o baseline **antes** de recomendar?" | compara `seq` do `tool_result` com o do `tool_call` seguinte |
| **Runner** | "esta run está íntegra?" | `seq` sem buraco de 1 a N |

O caso do gate é o exemplo concreto de por que isso não é burocracia. Se o `GateEvent` sair com
`seq` **maior** que o da chamada que ele aprovou, o scorer lê *"agiu primeiro, pediu permissão
depois"* — que é **D1, severidade S0, a falha mais grave da taxonomia** — numa ação que foi
corretamente aprovada. Foi exatamente o **X20**, e o conserto (T14) foi separar **numerar** de
**emitir**: `chamar_tool` *reserva* o `seq` do gate antes de emitir o `ToolCall`, e a política
gasta esse número depois. Consequência aceita e documentada: **a ordem do arquivo deixou de ser a
ordem do `seq`** — a linha do gate aparece depois da linha da chamada que ela precede.

### "Lacuna de `seq`" — o que o `validar_trace` resolve

Uma **lacuna** é um buraco na numeração: existem os eventos 1, 2, 3, 5 — o 4 sumiu. Significa que
um evento foi **perdido** (crash, escrita que não completou, bug). `ARQUITETURA §5` decisão 9 diz
que *"run com lacuna de `seq` é inválida, não silenciosamente pontuada"* — porque pontuar um trace
incompleto é medir um agente que talvez tenha feito a coisa certa no evento que sumiu.

O problema era que **ninguém checava**. O `reader.py` declara explicitamente que não é ele
(precisa conseguir carregar o trace quebrado para poder diagnosticá-lo), e nenhuma task tinha
assumido. Hoje quem checa é `validar_trace(eventos) -> list[Lacuna]`, chamada pelo runner ao
fechar cada run: run com lacuna entra no manifesto com `valida: false` e o motivo, e **não é
apagada** — vira célula faltante explícita nas contagens da bateria.

## 2 · As peças do sistema

| Termo | O que é |
|---|---|
| **SUT** (*system under test*) | o agente sendo avaliado. Aqui: um loop ReAct sobre um cliente MCP |
| **agente ReAct** | laço "pensa → chama tool → lê resultado → pensa de novo", até responder |
| **MCP** | *Model Context Protocol* — o protocolo pelo qual o agente enxerga as tools. É a **fronteira instrumentada**: tudo que o agente faz passa por aqui, então o trace nasce sem o agente cooperar |
| **tool** | uma operação que o agente pode chamar (`get_asset`, `escalate_case`…). São 18, 1:1 com o OpenAPI do parceiro |
| **gate** | política que decide se uma ação de escrita pode ou não ser executada, **antes** de executá-la |
| **hidratação** | variante em que o agente recebe cadastro do ativo e contexto do usuário **antes** do laço, de graça, em vez de ter de ir buscar |
| **trace** | o `.jsonl` com todos os eventos de uma execução. É o objeto que todo o resto lê |
| **run** | uma execução: um cenário × um modelo × uma variante × uma seed |
| **judge** | um LLM que lê a resposta final (e às vezes o trace) e responde perguntas **fechadas** sobre ela |
| **runner** | o programa que roda a bateria: monta as runs, executa, guarda os traces |

## 3 · O corpus

| Termo | O que é |
|---|---|
| **cenário** | um caso de teste, num YAML. São 24: 16 oficiais (`cen_*`) e 8 autorais (`aut_*`) |
| **gabarito** | o que se espera daquele cenário: tools esperadas, decisão esperada, precedências, o que é proibido |
| **`ramos`** | ramificações do gabarito: *"se o espectro degradar, então a decisão esperada muda para…"* |
| **split dev/test** | 6 cenários de desenvolvimento, 18 de teste. O test só é olhado depois de tudo congelado |
| **`env_seed`** | seed **da API**: decide qual modo de retorno cada `GET` devolve. Controla **o mundo** |
| **`sample_seed`** | seed **do LLM**: decide qual token o modelo escolhe. Controla **o agente** |
| **modo de retorno** | `complete`, `partial`, `conflict`, `inconclusive`, `unavailable` — o quanto do dado a API devolve |
| **pré-registro** | fixar corpus, métricas e taxonomia **antes** de olhar resultado, para não escrever a régua depois de ver a nota |

> A distinção `env_seed` × `sample_seed` é a hipótese **H4** inteira: `pass^k` com ambiente fixo
> mede variância só do modelo; com ambiente livre, mede modelo + mundo. A área entre as duas
> curvas é a inconsistência atribuível à plataforma.

## 4 · As camadas de avaliação

O trabalho inteiro é sobre **quanto vale pagar mais caro para detectar falha**. As camadas vão da
mais barata à mais cara:

| Camada | O que é | Custo |
|---|---|---|
| **N1** | determinística: chamou a tool certa? argumento certo? gate respeitado? decisão certa? | ~zero |
| **N2** | programática, sem LLM: ordem, redundância, cobertura de evidência, aderência causal | ~zero |
| **N3** | **judge** — um LLM respondendo perguntas fechadas sobre a resposta | tokens |
| **N4** | **você**, rotulando à mão. É o **gold**: o denominador de todo recall | tempo humano |

| Termo | O que é |
|---|---|
| **INS** | métricas do instrumento medindo a si mesmo. A que testa a hipótese principal é **INS.2**, o ganho de recall que o judge acrescenta sobre N1+N2 |
| **κ (kappa)** | concordância entre judge e humano, campo a campo. Valida a N3 |
| **`pass^k`** | probabilidade de o agente acertar **k vezes seguidas**. Mede consistência, não acerto médio |
| **sucesso binário** | o que conta como "passou" no `pass^k`: nenhuma falha S0, S1 ou S2 |

## 5 · Falhas: classes, códigos e severidade

Toda falha detectada recebe **um código** (de uma lista fechada e congelada com hash) e **uma
severidade**.

**Classes:** `P` = processo (como investigou) · `C` = conteúdo (o que afirmou) · `D` = decisão e
segurança (o que decidiu fazer).

**Severidade** (S0–S3; o nível S4, "cosmética", foi removido em 17/08 — nenhum código o emitia, e nível declarado e nunca
emitido se lê como "não houve falha" quando o fato é "não foi medido"):

| | Nome | Efeito |
|---|---|---|
| **S0** | catastrófica | zera score · reprova · revisão humana obrigatória |
| **S1** | grave | zera score · reprova |
| **S2** | moderada | desconto grande · reprova |
| **S3** | leve | desconto pequeno · **não** afeta `pass^k` |

> **Por que a taxonomia é congelada com hash.** Se os códigos forem criados enquanto se lê o
> resultado, toda falha encontra um balde, o recall tende a 100% por construção e o número que
> testa a hipótese deixa de significar nada. É o mesmo princípio do pré-registro.

## 6 · As etiquetas que aparecem nos documentos

| Prefixo | O que é |
|---|---|
| **A**⟨n⟩ | ponto de decisão que exige curadoria humana, não implementação (A2, A9, A12…) |
| **X**⟨n⟩ | risco registrado — coisa que pode morder mais adiante |
| **T**⟨n⟩ | task do `PLANO.md` |
| **H**⟨n⟩ | hipótese. **H0** é a principal; H2 apoio; H4 secundária; H1 virou dois pontos da curva de H0; H3 foi cortada |

**Um padrão que se repete e que vale nomear**, porque quase toda decisão de desenho do projeto
é um caso dele: *o instrumento não distingue "não houve falha" de "não foi medido"* — e o erro
sempre cai na direção que favorece a conclusão que o trabalho quer defender. Um nível de
severidade que ninguém emite, um campo do judge medido e descartado, um cenário morto que roda
mesmo assim, uma decisão ausente que passa como aprovada: em todos, o silêncio vira nota boa.
Por isso, aqui, "custo zero, declarar a limitação" quase sempre perde para "medir de verdade".
