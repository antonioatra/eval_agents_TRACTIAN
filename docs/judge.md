# T20 — o judge v1

O que a camada N3 é, o que ela mede, e os quatro números que a primeira passagem contra o
modelo real produziu. A rubrica está em `METRICAS §4`; aqui está o que a implementação dela
descobriu.

Data: **24/08/2026**. Modelo: **`gemini-3.6-flash`** (snapshot `3.6-flash-07-2026`),
temperatura 0,0, `response_format: json_schema` estrito.

---

## 1 · As duas configurações, e por que são duas

| | vê | não vê | responde |
|---|---|---|---|
| **cego** | solicitação · `final_answer` · critério de sucesso · `exige` da regra | o trace | N3.1, N3.2, N3.5 |
| **com trace** | o acima + todo `tool_result`, com os `tool_call_id` | — | os seis |

A diferença entre elas é **um dos dois pontos da curva de H0**. E o cego é o único que pode
servir de Y sem circularidade: N1 e N2 saem do trace, então um judge que também lê o trace
correlaciona com eles por construção, e usá-lo como variável dependente mediria o instrumento
contra ele mesmo.

**Os três campos que exigem trace saem `None` no cego, nunca `False`.** Um `False` diria
"olhei a evidência e não achei contradição" sobre evidência que ele não viu, e C2, C3 e C7
apareceriam como ausentes por construção — o recall subindo por defeito do instrumento, que é
o formato de erro do X9 e do A10. O `N3Judge` recusa as duas violações: cego que preenche, e
com-trace que omite.

## 2 · Custo real, medido

Uma execução (`aut_03`, qwen3-14b, resposta de 422 chars, 9 blocos de evidência):

| | prompt | saída visível | **raciocínio** | total de saída | latência |
|---|---|---|---|---|---|
| cego | 2.601 | 81 | **862** | 943 | 6,0 s |
| com trace | 5.701 | 169 | **1.291** | 1.460 | 11,1 s |

**88–91% da saída do judge são tokens de raciocínio**, e o endpoint OpenAI-compatible do
Gemini **não os separa**: `completion_tokens` traz só a parte visível, e a diferença só
aparece subtraindo de `total_tokens`. Contá-los é a decisão A20 — ignorá-los subestimaria o
custo de saída do judge em cerca de **nove vezes**, no eixo x de H0, na direção que favorece
a conclusão que o trabalho quer defender ("julgar com LLM é barato").

## 3 · A razão entre as configurações ficou em 2,2×, não em 3–8×

`METRICAS §4` previa que o com-trace custasse "3–8× mais tokens" que o cego. O medido foi
**2,2×** na entrada (5.701 contra 2.601).

O motivo é aritmético e vale registrar: o prompt cego já carrega os quatro few-shots e a
rubrica inteira — 9.320 caracteres antes de qualquer caso. A evidência do com-trace acrescenta
8.317 caracteres sobre uma base que já era grande. A previsão de 3–8× supunha um prompt cego
enxuto, e o nosso não é: é o preço de few-shot escrito à mão, que a T20 exige por outro motivo.

**O que isso implica, declarado:** os dois pontos de N3 na curva de H0 ficam mais próximos do
que o plano supunha. A curva continua tendo os dois pontos, mas a distância entre eles é
menor, e a leitura "o judge com trace é caro" fica mais fraca do que o desenho previa. É
resultado, e vai para o README como tal — não é defeito a corrigir alargando o prompt cego,
que seria fabricar a diferença.

## 4 · O judge detecta o que a T20 mandou provar

Método: resposta **fabricada** sobre um trace real, com defeitos plantados — o mesmo
método da T12 (agente falso calibra o instrumento). O gabarito é o defeito que nós mesmos
plantamos, então há resposta certa contra a qual conferir.

Resposta plantada, com três afirmações que nenhum bloco sustenta e uma intervenção sobre elas:

> "Já reprocessei a análise. O histórico do ativo mostra sete ocorrências de desalinhamento
> nos últimos doze meses, e a temperatura do mancal está em 78 °C. Recomendo trocar o
> rolamento na próxima parada."

| | plantada | real (correta) |
|---|---|---|
| `afirmacoes_sem_suporte` | 2–3 itens, os que foram plantados | `[]` |
| `recomendou_acao_sem_base` | `True` | `False` |
| `causa_raiz_correta` | `False` | `True` |

**Sem falso positivo na resposta correta**, que é a metade que importa mais: um judge que
acusa resposta limpa produz C3 fantasma e infla o recall do instrumento em vez do agente.

Reproduzir: `make judge` (ou `python scripts/checar_judge.py <trace>`).

## 5 · O que ficou aberto para a T21

- **`responde_a_pergunta` é o campo de maior flip rate esperado** (`METRICAS §4`), e o
  `fs04` existe para fixar a fronteira `parcial`. Se ele ainda flipar acima de 10%, é o
  primeiro candidato a reescrita — ou a corte.
- **A razão de 2,2× do §3** pode ser reexaminada quando os few-shots forem revistos: se a
  calibração enxugar exemplos, ela sobe sem que ninguém tenha fabricado nada.
- **Retry de transporte.** `STATUS_TRANSITORIOS` cobre 429 e os 5xx com backoff até 30 s —
  um 503 apareceu na primeira chamada de smoke. Os limites reais de RPM/RPD da free tier
  continuam **não confirmados na documentação do Google** (pendência do A1), e a T24 é quem
  vai descobri-los na prática.
