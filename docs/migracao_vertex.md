# O judge sai da free tier: o que a migração para o Vertex mudou, e o que ela corrigiu

**Data: 25/08/2026.** Fecha a consequência de cronograma aberta em `docs/limites_free_tier.md §5`
("o judge na free tier não sustenta o trabalho") e corrige uma afirmação que o projeto vinha
repetindo desde 15/08 sobre o congelamento da T23.

---

## 1 · O motivo é cronograma, e o crédito não era a solução óbvia

A free tier dá **20 chamadas por dia** para o `gemini-3.6-flash`, o que põe as ~1.400 chamadas
do judge a **70 dias**. O crédito de trial do Google Cloud (US$ 300) parecia resolver, e não
resolvia: a documentação de billing da Gemini API é explícita —

> *"No, the Google Cloud Welcome credit or free trial credit can't be used towards the Gemini
> API or AI Studio."*

O crédito cobre o **Vertex**, que serve o mesmo modelo por outro endpoint. E o Vertex serve os
flash por **dynamic shared quota**: não há RPM/RPD pré-definido, os pedidos são atendidos
enquanto houver capacidade. O `limit: 20` diário deixa de existir — não fica maior, deixa de
ser o regime.

## 2 · O catálogo, medido

`scripts/checar_catalogo_vertex.py`, saída bruta em `docs/catalogo_vertex.json`:

| modelo | meta | chamada | versionId | launchStage |
|---|---|---|---|---|
| `gemini-3.6-flash` | 200 | **200** | `default` | GA |
| `gemini-3.7-flash` | 200 | 200 | `default` | GA |
| `gemini-3-6-flash` | 404 | 404 | — | — |
| `gemini-3.6-flash-07-2026` | 404 | 404 | — | — |

Dois detalhes que custaram uma passada inteira de falso negativo:

* **A região é `global`.** Os flash 3.x dão 404 em `us-central1`, inclusive o 3.7 usado como
  controle. Foi o controle que separou "o modelo não está lá" de "o endereço está errado" —
  que era a função dele.
* **`global` não leva prefixo de host.** É `aiplatform.googleapis.com` puro. A primeira versão
  da sonda montou `global-aiplatform.googleapis.com` e recebeu 404 em tudo, inclusive no
  controle, o que teria matado a migração por um erro de string.

## 3 · A correção que vale mais que a migração: o id sempre foi um alias

`judge_llm.py` afirmava, desde 15/08, que `MODELO_PADRAO` era "um id datado" e que "um alias
tornaria o congelamento decorativo". A segunda metade continua verdadeira. A primeira nunca foi.

| | AI Studio | Vertex |
|---|---|---|
| catálogo **nomeia** o snapshot | ✅ `version: 3.6-flash-07-2026` | ❌ `versionId: default` |
| o id datado é **chamável** | ❌ **404** | ❌ 404 |
| a resposta reporta o snapshot | ❌ `modelVersion: gemini-3.6-flash` | ❌ idem |

`gemini-3.6-flash-07-2026` responde 404 **nos dois lados**. O AI Studio deixa *ler* para onde o
alias aponta; não deixa fixá-lo. Então a T23 nunca congelou o peso do outro lado — ela congela
o **prompt** e o **id**, que é bem menos do que o texto dela dava a entender.

**A migração não troca snapshot fixo por alias.** Troca **alias legível** por **alias mudo**.

A defesa que resta é medir: o canário do `scripts/checar_judge.py` — o trace com defeito
plantado — rodado antes e depois da bateria. Se o modelo virar sob o pé, a resposta ao canário
denuncia. É o método da T12 aplicado ao próprio instrumento.

## 4 · O ganho que não estava no plano: o A20 deixa de ser subtração

O `usage` dos dois compat, na mesma chamada de controle:

| provedor | `usage` |
|---|---|
| AI Studio | `{"prompt_tokens": 2, "completion_tokens": 0, "total_tokens": 6}` |
| Vertex | `… "completion_tokens_details": {"reasoning_tokens": 4}` |

O A20 registrou que o endpoint compatível "só entrega [os tokens de raciocínio] pela subtração".
Isso vale para o AI Studio. O Vertex **declara** o número.

Importa porque este valor entra em `tokens_out` e daí no eixo x de H0. A subtração é
reconstrução: supõe que `total` não contém nada além das três parcelas, e essa suposição não é
verificável do lado de cá. Numa chamada real medida em 25/08, `completion=5` contra
`reasoning=176` — **35× mais tokens de raciocínio que de resposta**. O custo do judge passa de
inferido a medido.

`RespostaDoJudge` prefere o declarado e cai na subtração quando ele não vem, então o caminho do
AI Studio não regride.

## 5 · O gate passou, e o instrumento não se moveu

`scripts/checar_judge.py` contra o Vertex:

* detectou as **três** afirmações plantadas e `acao_sem_base=True`;
* **não** acusou a resposta limpa — falso positivo aqui viraria C3 fantasma e inflaria o recall;
* razão de tokens de entrada com_trace/cego: **2,2×**, o mesmo valor medido no AI Studio em
  24/08 (`docs/judge.md §3`).

A razão ter se reproduzido é o resultado que mais importa: o instrumento mede a mesma coisa dos
dois lados.

**Mas os valores absolutos se moveram**, e isso precisa estar escrito:

| | AI Studio (24/08) | Vertex (25/08) | Δ |
|---|---|---|---|
| entrada, cego | 2.601 | 2.803 | +7,8% |
| entrada, com_trace | 5.701 | 6.050 | +6,1% |

Mesmo prompt, contagem ~6–8% maior. Não foi investigado de onde vem. **A consequência prática é
a regra que já valia por outro motivo:** o judge de uma bateria roda inteiro num provedor só.
Metade em cada um poria uma diferença de 7% de contagem dentro do eixo x de H0 como se fosse
variação do agente — a mesma contaminação que o §6 do `limites_free_tier.md` recusou quando a
proposta era distribuir o judge entre modelos.

## 6 · O que a migração custou no código

Os dois provedores continuam implementados. `PROVEDOR_PADRAO = VERTEX`, e voltar é uma linha.

| | |
|---|---|
| `base_url` | montada por `base_url_do_vertex(projeto, local)`, com projeto e região no caminho |
| credencial | deixa de ser chave estática e vira **callable**, relido a cada tentativa |
| `served_by` | `gemini_api` → `vertex_ai` no `ModelConfig`, e daí no manifesto (TAPI §9) |
| id no fio | `google/gemini-3.6-flash`; `ModelConfig.model_id` segue `gemini-3.6-flash` |

**Por que o callable.** O token OAuth vale 1 h e a bateria da T24 roda a madrugada inteira. Um
token lido no `__init__` expira no meio, e o que se perde não é a chamada — são as runs, que
ficam sem N3. Fixar o cabeçalho no `httpx.Client` faria o `google-auth` renovar sem que ninguém
usasse a renovação. Por isso o cabeçalho é montado **a cada tentativa**, e há teste para isso.

**O que o `google/` NÃO faz:** não entra no manifesto. Ele é detalhe de fio do compat do Vertex;
deixá-lo vazar para `model_id` tornaria os manifestos da piloto e da bateria incomparáveis campo
a campo por uma diferença que não é do modelo.

## 7 · O que isto ainda não estabelece

* **Onde fica o teto do DSQ.** "Sem RPD" não é "sem limite": sob pressão o Vertex devolve 429
  de sobrecarga. Não foi alcançado com a carga de smoke.
* **O 429 do Vertex não traz `retry in`.** `espera_pedida` devolve `None` lá quase sempre, e o
  backoff fixo (`ESPERAS_S`) passa a ser o plano inteiro — o oposto do que valia na free tier.
  Não foi exercitado contra um 429 real do Vertex.
* **De onde vêm os 6–8% de diferença na contagem de tokens** do §5.
* **Se o alias muda**, e com que frequência. O canário do §3 é detecção, não previsão.
