# Engenharia e avaliação de agentes industriais — arquitetura

**Case Inteli × TRACTIAN** · onboarding 13/08/2026 · entrega 08/09/2026

**A ideia central e as decisões de desenho.** Cobre os dois entregáveis do TAPI §2 — o agente e o
framework que o mede — como uma pilha só, e aponta para os documentos que detalham cada parte.

| Documento | Fonte de verdade de | Entra aqui como |
|---|---|---|
| **`METRICAS.md`** | métricas, camadas de julgamento, severidade, protocolo de execução | §7–§11 |
| **`CENARIOS.md`** | os 24 cenários, gabaritos, split, seeds canônicas | §6 |
| `scenarios/*.yaml` | os cenários executáveis, mais `_regras_decisao.yaml` | — |

**Nada de métrica ou de cenário é redefinido aqui.** Quando este documento precisa de um número
de protocolo, ele referencia — a versão anterior duplicava, e as duas cópias divergiram.

A hipótese que organiza o trabalho inteiro é **H0** (§12): *onde compensa pagar por LLM para
medir*. Tudo o mais existe para torná-la testável.

---

## 0. Escopo

O TAPI §2 pede uma solução contendo **construção do agente** e **framework de avaliação de
agentes**. Não são dois projetos concorrendo pelo mesmo mês: é uma pilha, e a fronteira entre
os dois é a camada MCP.

> **O agente é o entregável 1; o framework que o mede é o entregável 2. A camada MCP separa
> os dois — por isso ela não é detalhe de implementação, é decisão de arquitetura.**

| | Como o agente entra | Como o framework entra |
|---|---|---|
| Camada MCP | é como o agente acessa a API | é onde o framework intercepta |
| Trace | rastreabilidade da execução (TAPI §6) | substrato de todas as métricas |
| Cenários | casos de uso da demo | corpus versionado com gabarito |
| Hipótese | — | sobre o poder de medição do instrumento |
| Prova de qualidade | atende os 3 modos do TAPI §4 | detecta degradações plantadas |

O agente é **deliberadamente simples** (~4 dias de engenharia): ReAct, tools 1:1 com endpoint,
sem invenção. Não porque seja descartável — ele é entregável e é avaliado por si — mas porque
cada hora gasta em sofisticação do agente é uma hora que não vai para o instrumento que o
mede, e o instrumento é a parte que quase ninguém entrega bem.

### Princípio reitor

> **Tudo que pode ser determinístico, seja. A LLM só entra onde não há alternativa — e onde
> entra, é validada contra humano e congelada antes de medir.**

---

## 1. A pilha em cinco camadas

```
┌─ 5. EXPERIMENTO ──────────────────────────────────────────┐
│    runner (cenário × variante × seed) · agregação         │
│    notebooks de análise · figuras · relatório             │
└────────────────────┬──────────────────────────────────────┘
                     │ lê scores
┌─ 4. JULGAMENTO ────┴──────────────────────────────────────┐
│  N1 determinístico   tool certa? args? gate? ação indevida│  sem LLM
│  N2 programático     trajetória, redundância, roteamento  │  sem LLM
│  N3 LLM-as-judge     resposta sustentada pelas evidências?│  com LLM
│  N4 humano           ~35 execuções priorizadas            │  você
└────────────────────┬──────────────────────────────────────┘
                     │ consome
┌─ 3. TRACE ─────────┴──────────────────────────────────────┐
│    schema canônico JSONL · imutável · append-only         │
│    escrito pelo FRAMEWORK (interceptando), não pelo agente│
└────────────────────┬──────────────────────────────────────┘
                     │ instrumenta
┌─ 2. AGENTE + MCP ──┴──────────────────────────────────────┐
│  2b  agente ReAct · cliente MCP · budget · citações       │  ENTREGÁVEL 1
│      eixos de variação por config · mutantes              │
│  ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄ list_tools / call_tool ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄  │
│  2a  servidor MCP · tools 1:1 · cache · gate de ação      │  FRONTEIRA
│      classificação de status · emissão de eventos         │
└────────────────────┬──────────────────────────────────────┘
                     │ chama
┌─ 1. AMBIENTE ──────┴──────────────────────────────────────┐
│    cliente HTTP + modelos Pydantic do Swagger             │
│    proxy record/replay · fault injection                  │
└───────────────────────────────────────────────────────────┘
```

**A camada 2a é a fronteira do produto.** Acima dela mora o agente; abaixo, tudo é
substituível. Como o servidor MCP é o único caminho até a API, é ali que o trace nasce — o
agente não coopera com a instrumentação, ele é observado por ela. Troque o 2b por qualquer
outro cliente MCP e o framework continua medindo, sem uma linha de adaptador. É isso que torna
a entrega um *framework* e não *o meu eval do meu agente*.

Mapeamento com a arquitetura de referência do TAPI §8: solicitação → agente (2b) → camada MCP
(2a) → API industrial (1) → resposta/ação/escalonamento → trace e resultados (3, 4, 5).

---

## 2. Quadro de decisões

| # | Tema | Decisão | Status |
|---|---|---|---|
| **Escopo** ||||
| 1 | Entregáveis | os dois do TAPI §2, uma pilha só | ✅ |
| 2 | Integração | **servidor MCP** entre agente e API | ✅ |
| 3 | Instrumentação | servidor MCP (chamadas) + harness (modelo), unidos por `seq` | ✅ |
| **Agente** ||||
| 4 | Topologia | nós = fases de decisão; tools no loop | ✅ |
| 5 | Triagem | hidratação determinística + intenção por LLM | ✅ |
| 6 | Mini-agentes | 3 subgrafos, um por modo do TAPI §4 | ✅ |
| 7 | Estado | `StateGraph` tipado com Pydantic | ✅ |
| 8 | Granularidade das tools | 1:1 com endpoint, sem tool de conveniência | ✅ |
| 9 | Retorno degradado | classificação determinística no servidor MCP | ✅ |
| 10 | Parada | 8 iterações · 12 tool calls · cache, orçamento nunca reseta | ✅ |
| 11 | Suficiência | checklist por tipo de solicitação | ✅ |
| 12 | Gate de ação | `Approver` injetável no servidor; humano é uma das políticas | ✅ revisto |
| 13 | Permissões | checa antes de tentar | ✅ |
| 14 | Idempotência | chave acumulativa por ação+args, nunca reseta | ✅ |
| 15 | Fundamentação | schema + validador determinístico de citações | ✅ |
| 16 | Memória | SQLite de casos resolvidos, com guarda de recência | ✅ |
| 17 | RAG | não. Conhecimento é tool da API | ✅ |
| 18 | Multi-turno | **turno único** na bateria; multi-turno só na demo, se sobrar | ⚠️ revisto |
| 19 | Concorrência | `asyncio` entre solicitações, sem broker | ✅ |
| **Framework** ||||
| 20 | Núcleo | runner de cenários + métricas de trajetória | ✅ |
| 21 | Ordem do corpus | cenários primeiro, catálogo da API depois | ✅ |
| 22 | Gabarito | relativo ao estado observado (função, não valor fixo) | ✅ |
| 23 | Ambiente | proxy record/replay + fault injection | ⚠️ depende do Swagger |
| 24 | Trace | JSONL canônico, imutável, escrito pelo framework | ✅ |
| 25 | Scores | arquivos separados do trace, versionados, recomputáveis | ✅ |
| 26 | Judge | prompt + rubrica + few-shot. Sem fine-tuning | ✅ |
| 27 | Saída do judge | perguntas fechadas; agregação aritmética própria | ✅ |
| 28 | Validação do judge | flip rate + κ contra humano, split dev/test | ✅ |
| 29 | Congelamento | judge versionado com hash antes da bateria final | ✅ |
| 30 | Severidade | escala S0–S4, define sucesso binário | ✅ |
| 31 | Estabilidade | pass^k (τ-bench), n=8 seeds | ✅ |
| 32 | Trade-off | cortar eixos de variação para pagar repetições | ✅ |
| **Entrega** ||||
| 33 | Modelos | 2 SUTs locais + 1 judge distinto, tudo local | ⚠️ par definitivo na S1 |
| 34 | Hipóteses | **H0 principal** (curva custo × recall); H1 virou dois pontos dela; H2 apoio, H4 secundária | ⚠️ revisto 14/08 |
| 35 | Interface | CLI de trace (dia 1) + notebooks; **React cortado** | ⚠️ revisto |
| 36 | Apresentação | notebooks versionados → figuras → README | ✅ |

As três linhas marcadas **revisto** mudaram na fusão dos dois documentos de arquitetura —
o porquê de cada uma está em §17.

---

## 3. O agente (entregável 1)

### 3.1 Fluxo

```
              Solicitação
        msg + user_id + asset_id? + thread_id
                    │
                    ▼
        Hidratação de contexto          DETERMINÍSTICO, SEM LLM
        empresa · perfil · permissões · cadastro e criticidade do ativo
                    │
                    ▼
        Triagem de intenção             LLM
                    │
      ┌─────────────┼─────────────┐
      ▼             ▼             ▼
  Contextualizar  Investigar    Executar
  tools de        tools de      tools de
  conhecimento    dados         ação
      │             │             │
      │             ▼             ▼
      │        Suficiência ──► Gate de ação
      │        checklist        Approver
      │        por tipo         injetável
      │             │             │
      ▼             ▼             ▼
  Resposta     Pergunta ao    Escalonamento
  fundamentada   usuário       (3 níveis)
                    │
              (falta evidência → volta para Investigar)
```

**A hidratação vem antes de qualquer decisão** e não usa LLM: os ids já vieram na entrada,
buscar contexto/permissões/ativo é execução, não julgamento. Economiza ~3 iterações e deixa o
trace limpo. O que **não** entra na hidratação: análises, sinais e cobertura de modelo "por
via das dúvidas" — isso é investigação, é decisão do agente, e é exatamente o que a rubrica
avalia.

A justificativa de continuação entre nós é **estado interno**, não entrada. Toda run começa
por uma solicitação:

```python
class Solicitacao(BaseModel):
    message: str          # "O motor da bomba 3 tá com vibração alta desde ontem, é grave?"
    user_id: str          # → empresa, perfil, permissões (hidratação)
    asset_id: str | None  # opcional; se ausente, o agente descobre pelo texto
    thread_id: str        # continuidade entre interações
```

### 3.2 Segregação por modo

A fronteira entre os subgrafos é o vocabulário do próprio TAPI §4 — Contextualizar, Investigar,
Executar — e não o domínio técnico (vibração, temperatura, cadastro).

| Subgrafo | Tools que enxerga | Pode causar dano? |
|---|---|---|
| Contextualizar | `get_procedimento`, `get_glossario`, `get_orientacao_suporte`, `get_user_context` | não — só leitura |
| Investigar | `get_asset`, `get_hierarquia`, `get_analises`, `get_qualidade_sinal`, `get_espectro`, `get_cobertura_modelo` | não — só leitura |
| Executar | `solicitar_analise`, `reprocessar_analise`, `solicitar_retreinamento`, `alterar_config`, `escalar_humano` | **sim — irreversível** |

Três razões, em ordem de peso:

1. **O risco fica confinado.** Só um subgrafo alcança tool destrutiva. O gate mora num lugar
   só, e é *estruturalmente impossível* o agente de investigação disparar um retreinamento.
   Argumento de segurança, não de organização.
2. **A partição é real.** Cada agente vê 4–6 tools em vez de 15+. Por domínio, `get_asset` e
   `get_qualidade_sinal` se repetiriam nos três — os schemas duplicariam sem reduzir a
   competição na janela de contexto, e o ganho evaporaria.
3. **Casa com a rubrica.** O TAPI avalia "decisão entre orientar, agir ou escalar". Componentes
   com esses nomes fazem o README se escrever sozinho.

Domínio técnico não some: vira **parâmetro** dentro de Investigar (qual checklist de
suficiência se aplica), não fronteira de agente.

### 3.3 Determinístico ou LLM

| Tarefa | Como | Por quê |
|---|---|---|
| Buscar contexto, permissões, ativo | determinístico | os ids já vieram na entrada |
| Validar existência e acesso | determinístico | é checagem, não julgamento |
| Classificar status do retorno | determinístico | é forma da resposta, não semântica |
| Classificar intenção | **LLM** | é linguagem natural |
| Escolher qual tool chamar | **LLM** | é o que a rubrica avalia |
| Redigir a resposta fundamentada | **LLM** | idem |

Intenção não aceita heurística de palavra-chave: *"reprocessa a análise da bomba 3"* (Executar)
e *"por que a análise da bomba 3 deu isso?"* (Investigar) são quase idênticas lexicalmente e
opostas em consequência. Palavra-chave erra e dispara ação irreversível.

### 3.4 Qualidade do dado não é decisão

São eixos independentes. Completo/parcial/inconclusivo/conflito/indisponível é **qualidade do
dado** (TAPI §5.1); orientar/agir/escalar é **decisão**.

| | Orientar | Agir | Escalar |
|---|---|---|---|
| Completo | caso simples, o agente responde | ação com justificativa forte | achado grave em ativo crítico |
| Parcial | responde com ressalva explícita | **nunca** | escala dizendo o que falta |
| Inconclusivo | explica por que não conclui | **nunca** | escala, prioridade baixa |
| Conflito | **nunca** | **nunca** | escala expondo as duas versões |
| Indisponível | **nunca** | **nunca** | após retries, alerta de falha |

O que puxa para o humano é **criticidade do ativo + risco da ação**, não completude do dado.
Se dado completo sempre virasse humano, o agente seria um roteador — e a rubrica avalia
justamente se ele decide.

**Conflito entre fontes**, concretamente: `/analises` afirma "falha em rolamento, confiança
0.87" mas `/dados-tecnicos` diz que a qualidade do sinal no período é ruim; `/ativos` marca
criticidade alta mas `/modelos` diz que o ativo está fora de cobertura; duas análises
consecutivas divergem sem evento entre elas. **Política: o agente não arbitra silenciosamente.**
Registra em `conflicts`, gasta *uma* chamada extra tentando desempatar, e se persistir escala
expondo as duas versões.

**Indisponibilidade — revisto em 14/08 contra a API real.** Não é sorteio por chamada: o modo é
função pura de `(seed, recurso, categoria)` (`api/app/prob.py`). Três chamadas idênticas com a
mesma seed devolvem `unavailable` idêntico, apesar de o campo `notes` dizer *"Indisponibilidade
temporária"*.

> **Retry é zero tentativas, não três.** Marca `INDISPONIVEL` na primeira e devolve ao agente
> decidir. Repetir a chamada não pode dar outro resultado — só queima budget, e é registrado
> como falha P5 (`METRICAS.md §6.1`).

O texto enganoso do `notes` é uma armadilha legítima do ambiente e vira item de avaliação: o
agente que confia na palavra "temporária" em vez do comportamento observado paga em orçamento.

### 3.5 Política de parada e cache

```python
MAX_ITERATIONS    = 8    # voltas no loop do agente
MAX_TOOL_CALLS    = 12   # total por solicitação
MAX_SAME_CALL     = 1    # mesmo endpoint + mesmos args → cache
MAX_SAME_ENDPOINT = 4    # mesmo endpoint, args diferentes
```

Um caso de vibração consome 5 chamadas antes de qualquer raciocínio: contexto do usuário →
cadastro do ativo → qualidade do sinal → última análise → cobertura do modelo. Três chamadas
seria orçamento de brinquedo.

**O orçamento nunca reseta ao trocar de tool.** É assim que nasce loop infinito, porque *toda*
chamada traz alguma informação nova.

Dentro de um atendimento (segundos) o dado não muda: repetir chamada idêntica é sintoma de
loop, não busca de dado novo. Por isso **cache por run**, não intervalo entre requisições. O
cache-hit é registrado no trace e vira a métrica "chamadas redundantes tentadas" — bom
indicador de qualidade de planejamento. Frescor importa *entre* sessões, e aí quem resolve é o
`retrieved_at` do banco de casos (§3.9).

### 3.6 Fundamentação

Schema não basta: o modelo preenche `citations: ["tc_3","tc_7"]` com ids inventados sem piscar.
Validador determinístico, em runtime:

```python
def validar_justificativa(just, state) -> Resultado:
    # 1. schema Pydantic: campos obrigatórios, mínimo de citações
    # 2. os tool_call_ids citados existem em state.tool_calls?
    # 3. o valor afirmado bate com o que a tool retornou?
    # 4. alguma citação aponta para PARCIAL/INCONCLUSIVO? → bloqueia
    # falhou → devolve o erro ao agente para refazer
```

LLM-juiz só na avaliação offline (N3), **nunca em runtime**.

### 3.7 Gate de ação, permissões e idempotência

O TAPI §5.1 é explícito: *"uma chamada aceita representará a execução da ação e retornará
sucesso, sem ciclo adicional de status"*. **Não há desfazer** — daí o gate.

O gate mora no **servidor MCP**, não no agente: é onde a ação executa, e um agente com bug (ou
um cliente de terceiro) não contorna o que não está no seu processo. É um protocolo injetável:

```python
class Approver(Protocol):
    def decidir(self, acao, justificativa, estado) -> Veredito: ...

# AutoApprove · AutoDeny · PolicyApprover (bateria) · HumanApprover (demo)
```

`interrupt()` humano **não funciona** num runner de 544 execuções em lote — por isso a política
é injetável, e o humano é *uma* das implementações, usada na demo.

```python
key = sha256(f"{acao}:{json.dumps(args, sort_keys=True)}").hexdigest()
if key in ctx.acoes_executadas:
    return JaExecutada(key)
ctx.acoes_executadas.add(key)
```

A chave de idempotência é **acumulativa e nunca reseta por nó** — se resetasse, um retry
dispararia retreinamento duas vezes. Mecanismo independente do contador de retries de leitura.

Permissões são checadas **antes** de tentar a ação, não depois do erro.

### 3.8 Escalonamento em três níveis

| Prioridade | Quando |
|---|---|
| Alta | ativo crítico com evidência de falha, ou conflito não resolvido em ativo crítico |
| Média | dado parcial em ativo relevante, ou ação de alto impacto sem permissão do usuário |
| Baixa | inconclusivo, fora de escopo, dúvida sem urgência |

O critério não é volume de informação — entra **criticidade do ativo**, que a API fornece.

### 3.9 Memória de casos resolvidos

```python
class CasoResolvido(BaseModel):
    case_id: str
    asset_id: str
    problema: str          # categoria normalizada
    conclusao: str
    acao_tomada: str | None
    evidencias: list[str]
    resolvido_em: datetime
```

Busca **estruturada** por `asset_id` + categoria, não semântica. Caso com mais de 30 dias entra
com aviso de idade no prompt, e **nunca** pula a checagem de qualidade de sinal atual: em
manutenção preditiva a máquina degrada, e um rolamento em estágio 1 há duas semanas pode estar
em estágio 3 hoje. Caso antigo é **evidência de contexto**, nunca substituto de investigação.

---

## 4. A camada MCP (fronteira)

### 4.1 O que ela compra

O TAPI §6 aceita "tools, servidor MCP ou abordagem equivalente". MCP não é obrigatório — a
razão de escolhê-lo é que ele resolve os dois entregáveis de uma vez:

- **Ponto único de instrumentação.** Toda chamada atravessa o servidor. O trace nasce ali, sem
  o agente cooperar.
- **Ponto único de controle.** Gate, cache, classificação de status, injeção de falha e
  exposição de catálogo ficam num lugar só, fora do alcance do agente.
- **O framework deixa de ser específico do meu agente.** Qualquer cliente MCP vira SUT
  instrumentado — inclusive um agente de terceiro, apontado para o mesmo servidor por stdio.
  Essa é a frase que sustenta a palavra *framework* na apresentação; sem ela é alegação.

Custos, declarados: latência extra por chamada (desprezível em memória, milissegundos por
stdio — medida no piloto e reportada); mais superfície de erro, porque o schema existe no
servidor e o gabarito no corpus podem divergir; e um teste de contrato obrigatório para
impedir essa divergência (§4.2).

### 4.2 Catálogo

Uma tool por endpoint, **1:1**, sem tool de conveniência que agregue duas chamadas — a
granularidade é o que torna "escolha da função" mensurável. Os schemas são declarados no
servidor e servidos por `list_tools`; o agente **descobre** as tools, nunca declara uma lista
paralela. Duas fontes de verdade divergem em silêncio e matam o experimento.

**Teste de contrato:** toda tool citada em `gabarito.tools_esperadas` de qualquer cenário
precisa existir em `list_tools`. É barato e pega na hora um gabarito que diz
`get_signal_quality` quando o servidor expõe `get_sinal_qualidade` — erro que, sem esse teste,
só aparece no meio da bateria disfarçado de "o agente não chamou a tool esperada".

### 4.3 Quem emite o quê

| Evento | Emitido por | Por quê |
|---|---|---|
| `tool_call`, `tool_result`, `gate` | **servidor MCP** | é o único ponto por onde passa toda chamada |
| `llm_call`, `budget`, `final_answer`, `decision` | **harness do cliente** | o servidor não enxerga o modelo |

**Não existe evento HTTP separado** (decidido em 15/08). O `ToolResult` já carrega `http_status`
e `latencia_ms`, e "é chamada de escrita" se deriva do `tool_name`, que é um conjunto fechado e
conhecido. Um `http_request` próprio duplicaria a informação e daria a T14 um quarto evento para
reconciliar por `seq` em troca de nada. O que se perde é o registro da chamada que morre antes de
virar `tool_result` — esse caso vira `RunError` com `onde` apontando a tool. `parse_erro` também
não é evento: é campo do `llm_call` (`parse_ok`, `parse_erro`), porque a tentativa de parsing
pertence à chamada que a produziu.

O servidor emite seus eventos como **logging notification** MCP; o cliente registra um
`message_handler` que escreve no `TraceWriter`. Notificação é assíncrona, então ordem de
chegada não é ordem de evento: cada evento carrega `seq` monotônico atribuído no servidor, e o
`TraceReader` ordena por ele (§5, decisão 8).

**A única telemetria visível ao modelo é o `tool_call_id`**, porque ele precisa dela para citar
evidência. Latência, status classificado e cache-hit vão para o trace, nunca para o contexto —
senão o agente começa a raciocinar sobre a própria instrumentação.

### 4.4 Transportes

| Transporte | Onde | Por quê |
|---|---|---|
| streams em memória | bateria e testes | um servidor por run: isolamento perfeito de cache, cassete e chaves de idempotência, sem custo de processo |
| stdio | demo e clientes de terceiro | é como um cliente MCP externo se conecta |

Mesmo código de servidor nos dois. Uma flag, zero duplicação.

---

## 5. Trace

Schema completo em **`schema_trace.py`**. Decisões de desenho:

1. **Trace imutável, scores derivados.** Mudar um scorer não pode exigir re-executar o agente.
   `scores/v1/` e `scores/v2/` coexistem sobre os mesmos traces.
2. **`ToolCall` e `ToolResult` são eventos distintos.** O que foi pedido e o que voltou são
   fatos independentes — juntá-los impede medir latência, retries, cache e replay.
3. **`tool_call_id`** (`tc_03`) é a chave de citação. Torna verificável a alegação "me baseei
   na análise X".
4. **`parse_erro` é métrica, não exceção.** É o principal confound entre modelos.
5. **`racional_declarado` é debug, nunca scorer.** Racionalização post-hoc não é fiel ao
   processo interno; explicação convincente de escolha errada é comum.
6. **Payload grande vira blob**, trace guarda `sha`. Mantém o JSONL leve para pandas.
7. **`N4Humano.amostra`** separa `estimativa` de `melhoria` no dado — impossibilita, por
   acidente, calcular κ misturando as duas.
8. **Dois emissores, uma ordem.** `seq` monotônico atribuído no servidor; o reader ordena por
   ele. Ordenar por `ts` seria errado — são dois relógios.
9. **Run com lacuna de `seq` é inválida**, não silenciosamente pontuada.

### Layout em disco

```
runs/<experiment_id>/
    manifest.json
    traces/<run_id>.jsonl
    blobs/<sha256>.txt
    scores/<scorer_version>/<run_id>.json
    cassettes/<cassette_id>.json
```

---

## 6. Corpus de cenários → `CENARIOS.md`

**24 cenários = 16 oficiais + 8 autorais**, split 6 dev / 18 test, cada um com `env_seed`
canônica validada contra a API. Os arquivos executáveis são os YAMLs em `scenarios/`.

O que vive lá e não se repete aqui: ordem de autoria, gabarito relativo, pares simétricos de
regra, `env_seed` × `sample_seed`, a tabela dos 24 com split e seeds, os oito autorais em
detalhe, o que o ambiente **não** protege, cobertura, e os achados da conversão.

Três fatos deste corpus que o resto da arquitetura assume:

- **O gabarito é função do estado observado, não um valor** (`CENARIOS §2.1`). Nenhum YAML guarda
  uma decisão fixa: guarda o nome de uma regra, implementada em T9 sobre `derivar_estado(trace)`.
  É o que permite avaliar contra uma API que varia de propósito.
- **A `env_seed` é por cenário, não por bateria** (`CENARIOS §2.3`). Não existe seed global: um
  cenário que exige 5 recursos `complete` sobrevive a ~7,8% das seeds.
- **A API não isola leitura por empresa** (`CENARIOS §5.1`): `GET` de ativo de outra empresa
  devolve 200 com payload completo. O isolamento de escopo é responsabilidade do agente — é o que
  a camada MCP (§4) e o gate (§3) existem para impor.

---

## 7–11. Severidade, camadas de julgamento, validação, pass^k e matriz → `METRICAS.md`

Estas cinco seções foram absorvidas pelo catálogo de métricas, que é a fonte de verdade delas:

| Era | Agora |
|---|---|
| §7 Severidade das falhas | `METRICAS §6.0` (escala S0–S4) e `§6.5` (sucesso binário) |
| §8 Camadas de julgamento | `METRICAS §2–§5` (N1.1–N1.6, N2.1–N2.6, rubrica N3, gold N4) |
| §9 Validação do instrumento | `METRICAS §7` (camada INS: recall, ganho incremental, κ, flip rate, mutantes) |
| §10 Estabilidade pass^k | `METRICAS §7.2`, com a decomposição de variância que sustenta H4 |
| §11 Matriz de execução | `METRICAS §9.2` (baterias) e `§9.3` (higiene experimental) |

**Por que saíram daqui.** As duas cópias divergiram: esta seção ainda dizia 16 cenários de test e
256 execuções depois que o corpus passou a 18 e 288, e listava mutantes `M1–M4` enquanto o
catálogo já tinha `MUT1–MUT4` com um deles trocado. Uma arquitetura que repete números de
protocolo envelhece mal — aqui ficam as decisões de desenho, lá ficam as definições operacionais.

---

## 12. Hipóteses

Formato TAPI: *se [mudança] → [métrica] [direção], porque [mecanismo]*.

**Nenhuma hipótese é *"o meu agente é bom"*.** Todas são sobre o poder de medição do framework —
o agente é o corpo de prova, não o objeto de estudo.

### H0 — principal

> **Se acrescentarmos camadas de avaliação mais caras (N1 → N2 → N3 → N4) sobre um agente
> industrial que opera contra uma API estruturada, o recall de detecção de falhas sobe com
> retorno decrescente e custo crescente, e o ganho se concentra numa única classe de falha —
> porque falhas de *processo* e de *decisão* são verificáveis contra um gabarito estrutural
> (que tool, com que argumento, em que ordem, sob que permissão), enquanto falhas de *conteúdo*
> exigem julgar texto livre contra evidência, que é o que só um LLM (ou um humano) faz.**

**A métrica que a testa** é o ganho incremental **`ΔRecall(N3 | N1+N2)`** com IC bootstrap
(INS.2 em `METRICAS §7`), lido contra o eixo de custo por execução avaliada (INS.4). A figura
principal do trabalho é a **curva custo × recall, uma linha por classe de falha (P, C, D)**.

**A predição concreta**, em termos da taxonomia de `METRICAS §6`: P e D são detectáveis sem LLM,
com custo perto de zero; C exige N3, com exceção parcial de C5 (citação inválida, verificável
determinicamente) e de C1 quando a causa-raiz errada decorre de uma precedência violada, que a
N2.1 já pega.

**Por que esta hipótese e não uma sobre o agente.** A pergunta que ela responde não é *"o agente
acerta?"* — é ***onde compensa pagar por LLM para medir***. É uma hipótese sobre o instrumento, e
é falseável nos dois sentidos:

- se o judge **não** acrescentar detecção sobre N1+N2, H0 está refutada e a conclusão vira
  *"para agente industrial com API estruturada, avaliação determinística basta"* — resultado mais
  forte e mais acionável que a confirmação;
- se acrescentar **em todas as classes por igual**, a estratificação por classe estava errada e o
  achado é sobre a taxonomia, não sobre as camadas.

**Requisito de desenho que ela impõe.** Sem instrumentação de custo (T35) a curva não tem eixo x
e H0 não é testável — é a peça mais fácil de esquecer do plano inteiro. E sem gold humano (N4.1)
não existe recall nenhum, porque não há denominador.

### H1 — dois pontos da curva de H0

> Se o LLM-as-judge receber o trace de execução além da resposta final, sua concordância com o
> julgamento humano aumenta, porque avaliar fundamentação exige saber o que a API efetivamente
> devolveu — informação ausente da resposta.

**Era a hipótese principal até 14/08.** Foi rebaixada quando H0 foi formulada, porque *judge
cego* e *judge com trace* não são duas hipóteses: são **duas configurações da camada N3**, ou
seja, dois pontos da mesma curva custo × recall — o segundo mais caro (o trace ocupa contexto) e,
presumivelmente, com mais recall. Medir os dois continua valendo e não custa nada a mais: usa as
mesmas 35 rotulagens de N4.1 que já são necessárias. Mede-se κ (INS.6) nas duas configurações.

### H2 — apoio

> Comparados dois modelos locais de portes diferentes, a diferença será maior na acurácia dos
> argumentos do que na escolha da função, porque escolher a tool é reconhecimento de intenção e
> preencher argumentos exige leitura precisa do schema.

Sai de graça dos scorers N1 (N1.1 × N1.2). Garante resultado mesmo se o judge der trabalho.

### H4 — secundária

> Se medirmos pass^k com ambiente controlado por replay e com ambiente livre, a diferença
> quantifica a fração da inconsistência atribuível à variabilidade da API, porque o replay
> elimina a variância ambiental mantendo a estocasticidade do modelo.

Depende da distinção `env_seed` × `sample_seed` (`CENARIOS.md §2`): pass^k exige `env_seed` fixo
e `sample_seed` variando.

### H3 — cortada, e por quê

> *Se as tools forem segregadas por modo, o erro de seleção de função cai, e o ganho é maior
> quanto menor o modelo.*

Hipótese sobre o desenho do **agente**, não do instrumento. Seria um segundo eixo de variação
(segregado vs. agente único de 15 tools) e dobraria a bateria principal — comprando um resultado
sobre o agente ao preço do pass^k, que é o resultado sobre o instrumento. A flag de arquitetura
fica implementada no código (custa pouco) e a hipótese fica registrada como **trabalho futuro**,
com o cálculo de custo que a matou. Declarar isso é mais forte que omitir.

---

## 13. Modelos e hardware

**MacBook Pro M5, 24 GB de memória unificada** (`hw.memsize` = 25.769.803.776 B, medido 15/08 —
uma versão anterior desta seção dizia 32 GB e ~22 GB efetivos; era erro, e o orçamento real é
~6 GB menor). Em Mac a memória é unificada, então o teto do modelo é a RAM total menos o que o
sistema usa: **~16 GB efetivos para pesos + KV cache**. **Não cabem os três juntos** — e não
precisam: o judge só roda depois, sobre traces já gravados (§14). **Carregamento sequencial,
nunca simultâneo.**

Endpoint OpenAI-compatible (LM Studio ou Ollama) para que trocar de modelo seja uma linha de
config; MLX no Apple Silicon; **prefix cache** ligado — num loop ReAct o prefixo se repete, e sem
cache reprocessa tudo a cada iteração. Paralelismo ajuda pouco (GPU única): o ganho vem de prefix
cache e de MoE.

**Arranjo híbrido (A1, decidido 15/08): SUTs locais, judge na free tier.** O trabalho tem dois
tetos complementares — local não tem limite de chamadas mas limita o tamanho do modelo; free tier
dá modelo grande mas ~1.500 chamadas/dia. As ~4.200 chamadas de SUT ficam onde não há RPD; as
~1.400 do judge ficam onde há modelo grande de graça. Ver `DECISOES A1` para a conta.

| Papel | Onde | Perfil | Critério |
|---|---|---|---|
| SUT A | local | Qwen3-8B q4, ~5 GB | o "pequeno" — onde os erros interessantes aparecem |
| SUT B | local | Qwen3-14B q4, ~9 GB | mesma família do A; carregado depois dele, nunca junto |
| Judge | Gemini free tier | o maior disponível, **≠ dos SUTs** | juiz igual ao réu prefere as próprias respostas |
| SUT de referência | Gemini free tier | modelo de fronteira | **fora da bateria fatorial** — ver abaixo |

**O SUT de referência não é uma terceira célula do desenho.** Ele roda só nos 6 cenários de dev
(~100–300 chamadas, folgadas na free tier) e serve de teto contra o qual ler os dois SUTs locais:
"quanto do erro medido é do porte do modelo e quanto é do problema ser difícil?". Entrar na
bateria 18 × 2 × 8 quebraria a H2, que exige mesma família para isolar o tamanho — e estouraria o
RPD, porque SUT é o papel de alto volume. A comparação controlada (Qwen3-8B × Qwen3-14B) e o
ponto de fronteira respondem perguntas diferentes; o desenho comporta as duas desde que não se
misturem na mesma tabela.

Dimensionamento local em q4, para referência: Qwen3 8B ~5 GB · Qwen3 14B ~9 GB · gpt-oss 20B
~12 GB · Mistral Small 24B ~14 GB. **Qwen3 30B-A3B (~18 GB) não cabe** nos 16 GB efetivos — era
candidato a SUT B sob o orçamento errado de 32 GB. Nada de 70B.

**Mesma família nos dois SUTs.** Cruzar famílias introduz diferenças de treino, de formato de
tool calling e de tokenizer — e aí não se sabe se a diferença veio do tamanho ou da marca.
Mesma família isola o tamanho como *a* variável.

Critério de escolha: **qualidade de tool calling nativo**, não benchmark geral. O ranking muda
rápido; verificar os pesos disponíveis no momento da escolha.

**Tarefa inegociável da S1:** dar ao modelo maior as ~15 tools reais e rodar 20 chamadas. Se
ele errar seleção de função sistematicamente, todo o cronograma muda — melhor descobrir no dia
3 que no dia 20.

Registrar por modelo, conforme TAPI §9: id, quantização, temperatura, seed, janela, mecanismo
de saída estruturada, limitações observadas.

---

## 14. Apresentação de dados

Notebooks são artefato de primeira classe, não rascunho:

- **Notebook nunca executa o agente.** Lê de `runs/*/scores/` e `traces/`. Análise reprodutível
  em segundos, independente de GPU.
- Cada notebook exporta figuras nomeadas para `figures/`, em PNG (300 dpi) e SVG.
- Toda figura do README e da apresentação vem de um notebook versionado. Nenhum print de tela.
- Seeds fixas e `%watermark` de versões no topo.

| Notebook | Produz |
|---|---|
| `nb01_exploracao_api` | catálogo de respostas da API, reconciliação dos cenários |
| `nb02_cobertura_corpus` | matriz de cobertura, lacunas justificadas |
| `nb03_calibracao_judge` | flip rate por campo, κ por campo, curva de iteração da rubrica |
| `nb04_resultados_principais` | H1 e H2, comparação entre modelos |
| `nb05_passk_estabilidade` | curvas de pass^k, decomposição de variância (H4) |
| `nb06_severidade_erros` | taxonomia de falhas por severidade e por modelo |
| `nb07_figuras_finais` | exporta as figuras da apresentação |

---

## 15. Riscos e limitações

| Risco | Mitigação |
|---|---|
| Autor do agente é autor do gabarito | gabarito de 16 dos 24 escrito por terceiro; procedência é variável controlada (`CENARIOS §1.3`) |
| Judge enviesado por auto-preferência | judge ≠ SUTs; validação por κ |
| Contaminação do judge pelos erros observados | split dev/test por cenário; congelamento com hash |
| n pequeno para inferência | reportar IC; não afirmar significância sem teste |
| Saída estruturada favorece um modelo | mesmo mecanismo nos dois; `parse_erro` reportado |
| Swagger diferente do previsto | camada 1 isolada; `StatusRetorno` é o único enum acoplado |
| Bateria não cabe no prazo | corpus núcleo de 8 cenários com matriz completa; resto reduzido |
| Camada MCP atrasa a bateria | transporte em memória; overhead medido no piloto e reportado |
| Schema do servidor diverge do gabarito | teste de contrato `list_tools` × cenários (§4.2) |
| Perda de evento por notificação assíncrona | `seq` monotônico; run com lacuna é marcada inválida |

**Limitações que vão no README de qualquer forma:** um único domínio (manutenção industrial
sintética); dois modelos locais apenas; corpus de dezenas, não milhares; κ estimado sobre 20
itens; ausência de validação externa por outro anotador; avaliador humano único e não cego ao
projeto.

---

## 16. Cronograma

| Semana | Foco | LLM? |
|---|---|---|
| **S1** 13–19/08 | Teste de tool calling do modelo maior (dias 1–2, **antes de tudo**). Cenários v1 sem ver a API → reconciliação → catálogo → cobertura. Cliente + modelos do Swagger. Proxy record/replay. Schema de trace. Scorers N1/N2. **Agente falso** para calibrar o instrumento. `nb01`, `nb02` | ❌ |
| **S2** 20–26/08 | Servidor MCP: tools 1:1, cache, gate, emissão de eventos. Cliente MCP + agente ReAct. Fault injection. Mutantes M1–M4. Bateria piloto | ✅ |
| **S3** 27/08–02/09 | Judge v1 → flip rate → v2 → 35 rotulagens → κ → **congela**. Baterias principal, mutantes e ambiente livre. `nb03`–`nb06` | ✅ |
| **S4** 03–08/09 | `nb07`, README completo, reprodutibilidade ponta a ponta, ensaio | — |

**Regra do agente falso (S1).** Um stub que emite trajetórias escritas à mão — uma boa, uma que
pula evidência, uma que dispara ação sem justificativa, uma que entra em loop. Rodar o framework
contra ele verifica que os scorers dão as notas certas. Quando o agente real entrar na S2, o
instrumento já está calibrado e qualquer discordância é do agente, não do medidor.

**Regra do mock (S1).** O mock da API com os cinco modos de retorno é o que impede travar se o
contrato real vier diferente do esperado — e vira infra de teste na S3. Não pular.

---

## 17. Decisões revistas na fusão dos documentos

Este documento uniu o material de arquitetura do agente (v3, 11/08) com o do framework
(13/08). Quatro decisões mudaram, e o registro do porquê importa mais que a decisão:

| Decisão | Era | Virou | Por quê |
|---|---|---|---|
| Integração | tools diretas, "MCP depois" | **servidor MCP desde o início** | com os dois entregáveis, o servidor paga por si: é o ponto de instrumentação que torna o framework agnóstico ao agente |
| Gate de ação | `interrupt()` humano sempre | **`Approver` injetável**, humano é uma das políticas | `interrupt()` não funciona num runner de 544 execuções em lote |
| Multi-turno | chat multi-turno | **turno único** na bateria | multi-turno multiplica o espaço de trajetórias e inviabiliza gabarito e pass^k no prazo; fica para a demo |
| Interface | React + FastAPI + SSE (1–1,5 dia) | **CLI de trace + notebooks**; React cortado | a rubrica pede "qualidade da demonstração", não "front bonito" — CLI + figuras de trace real demonstram melhor, e o dia e meio vai para a bateria |

E uma que se manteve, contra a intuição inicial: **MCP é camada, não nó.** Ele fica embaixo de
todos os nós do agente, não é o primeiro deles. O primeiro passo continua sendo a hidratação
determinística (§3.1), e a escolha de tool pela LLM só acontece depois da triagem de intenção.

---

## 18. Pendências

**Resolvidas em 14/08, medindo contra a API real** (registro em `CENARIOS.md §5 e §8`):

| Era | Resposta medida |
|---|---|
| 1. Confirmar os cinco modos do §5.1 | confirmados, e vêm explícitos no envelope `{mode, notes, data}` — o classificador de status não precisa inferir da forma do corpo |
| 2. A variação é por chamada ou por recurso? | **por `(seed, recurso, categoria)`, determinística**. Nunca por chamada. Corta o proxy record/replay (T6) e simplifica H4: não há variância ambiental estocástica, há sensibilidade a mundo |
| 3. Há limite de rate? | não observado nas ~80 requests da validação. **Não testado em volume** — a varredura de 1000 seeds foi cálculo local (a função de `prob.py` replicada), não chamadas HTTP |

Descobertas que **não** estavam na lista e mudaram o desenho:

- **A API não isola leitura entre empresas** — `usr_bruno` (comp_acme) lê ativo da comp_cimento_vale
  com HTTP 200. Escopo é responsabilidade do agente; gerou o código de falha D5 (S0).
- **`env_seed` é por cenário, não por bateria** — nenhuma seed em 1000 mantém 8 cenários válidos
  simultaneamente.
- **`unavailable` não é transitório** — ver §3.4 revisto.

Em aberto:

4. ✅ **Par de modelos** — **resolvido 15/08** (`DECISOES A1`): Qwen3-8B + Qwen3-14B locais como
   SUTs, judge e SUT de referência na free tier do Gemini. A formulação anterior desta pendência
   assumia 22 GB efetivos e cogitava MoE 30B-A3B como SUT B; com os ~16 GB reais isso não cabe.
   Falta só confirmar o par contra o T0b — se o 14B errar seleção de função sistematicamente, a
   troca é dentro da mesma família.
5. ⚪ **Checklists de suficiência** só fecham depois do Swagger real; preencher contra o mock na
   S1 e revisar quando o contrato chegar.
