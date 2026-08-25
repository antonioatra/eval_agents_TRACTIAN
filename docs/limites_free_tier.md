# O A1, medido: os limites da free tier do judge

Pendência aberta desde 15/08 e reafirmada no `docs/judge.md §5`: *"os limites reais de RPM/RPD
da free tier continuam não confirmados na documentação do Google"*. Este documento fecha a
parte que a carga real conseguiu fechar, em **24/08/2026**.

---

## 1 · A documentação não publica o número, e isso foi verificado

A página oficial de rate limits (`ai.google.dev/gemini-api/docs/rate-limits`) **não traz tabela
de free tier por modelo**. Ela diz que os limites "dependem de vários fatores (como o seu tier
de uso) e podem ser vistos no Google AI Studio" e manda o leitor ao painel.

Isso não é detalhe de documentação: significa que **o limite não é citável num relatório**, e a
única forma de conhecê-lo é bater nele. O trabalho precisa do número porque o judge é a única
peça que sai para a rede, e as três baterias dependem dele.

## 2 · O número, dito pela própria API

A calibração da T21 foi a primeira carga real do projeto contra a API. O 429 que ela produziu
**nomeia a quota**:

```
Quota exceeded for metric:
  generativelanguage.googleapis.com/generate_content_free_tier_requests
  limit: 20, model: gemini-3.6-flash
Please retry in 35.52310516s.
```

Dois achados, e o segundo vale mais que o primeiro:

| | |
|---|---|
| **a quota** | `generate_content_free_tier_requests`, **limit: 20**, para `gemini-3.6-flash` |
| **a espera** | a resposta diz **quanto** esperar, com precisão de nanossegundo |

## 3 · O erro que isto revelou no nosso cliente

`ESPERAS_S = (2.0, 8.0, 30.0)` somava 40 s de backoff, escolhido em 24/08 com o raciocínio
*"o limite é por minuto, então esperar 2 s três vezes não sai da janela"*. O raciocínio estava
certo e o número estava errado: as três esperas caíram **todas** dentro de uma janela que o
próprio serviço tinha dito que duraria 35 s **a partir de um instante posterior** ao início da
contagem. A quarta tentativa levantou `HTTPStatusError`, e a rodada morreu.

O conserto não foi alargar o backoff no chute. `ClienteDoJudge` passou a **ler a espera que a
resposta pede** (`espera_pedida`) e a honrá-la, com teto de 75 s — acima da janela de um minuto,
uma espera pedida maior é sinal de outra coisa (quota diária, projeto suspenso), e insistir ali
queima madrugada sem chance de sucesso.

**Por que isto importa mais para a T24 que para a T21.** A bateria principal roda de madrugada,
sem ninguém olhando. Uma bateria que desiste porque esperou 30 s onde o serviço pediu 38 perde
a noite inteira, e as runs afetadas ficam **sem N3** — que é o mesmo silêncio do X9: some parte
da amostra, e o recall sai medido sobre o que sobrou.

## 4 · O registro é uma lista no tempo, não um contador

`ClienteDoJudge.eventos_de_limite` guarda cada status transitório com **instante, status,
tentativa, espera pedida e corpo**. É lista e não contador porque o que decide o dimensionamento
é a **distribuição no tempo**: dez 429 no mesmo minuto são um limite de RPM, dez espalhados pelo
dia são um limite de RPD, e as duas leituras pedem manifestos diferentes. `calibrar_judge.py`
despeja tudo em `limites.json` num `finally` — ele é escrito **também quando a rodada morre**,
que é justamente quando tem mais a dizer.

## 5 · O `limit: 20` é POR DIA, e isso muda o plano

A mensagem nomeia a métrica (`..._free_tier_requests`) **sem** o sufixo `PerMinute`/`PerDay`
que o Google usa em outras quotas, então o número sozinho não distinguia as duas leituras. A
carga distinguiu.

O que decide é o padrão no tempo. Três tentativas espaçadas de **exatamente 60 s** (21:46:01,
21:47:01, 21:48:01), todas recusadas, e todas pedindo **mais ~59,6 s**:

| instante | espera pedida |
|---|---|
| 21:45:13 | 47,4 s |
| 21:46:01 | 59,6 s |
| 21:47:01 | 59,7 s |
| 21:48:01 | 59,6 s |

Uma chamada por minuto não esgota uma quota de 20 por minuto. Se a janela fosse de um minuto,
a segunda tentativa teria passado. O serviço empurrando sempre para o minuto seguinte, sem
nunca liberar, é quota **esgotada** — e o total de chamadas do dia bate com 20.

**A ressalva, porque ela existe:** a confirmação final foi feita ~1 min depois da última
tentativa da rodada anterior, e não com a janela perfeitamente limpa. O padrão de 60 em 60 s
já é conclusivo sozinho, mas a leitura mais limpa custa só esperar o dia virar — a quota RPD
reseta à meia-noite do Pacífico.

**A consequência é de cronograma, não de código:**

| | chamadas | dias a 20/dia |
|---|---|---|
| calibração da T21 (21 itens × 2 configs × 5) | 210 | **11** |
| judge das três baterias (estimativa do A1) | ~1.400 | **70** |

O judge na free tier **não sustenta o trabalho** com este modelo. As saídas são decisão de
projeto, não ajuste técnico — e uma delas é barata: **a quota é por modelo** (a própria
mensagem diz `model: gemini-3.6-flash`), e o catálogo da conta lista `gemini-3.7-flash`,
`gemini-3.5-flash`, `gemini-3.5-flash-lite`, `gemini-3.1-flash-lite` e outros. Trocar de
modelo compra quota nova — ao preço de mexer em `MODELO_PADRAO`, que a T23 congela, e de
julgar com um modelo cuja qualidade não foi verificada contra a rubrica.

## 6 · A quota é por modelo — confirmado, não deduzido

Com `gemini-3.6-flash` esgotado, uma chamada mínima a cada alternativa foi aceita:

| modelo | resposta com o 3.6 esgotado |
|---|---|
| `gemini-3.7-flash` | **200** |
| `gemini-3.5-flash` | **200** |

Então trocar de modelo compra quota nova, e a leitura `model: gemini-3.6-flash` dentro da
mensagem de erro é literal.

**Mas isso resolve menos do que parece.** Se cada modelo trouxer os mesmos 20/dia, o catálogo
inteiro de flash soma algo como 120 chamadas por dia — contra as ~1.400 que o judge das
baterias precisa. Distribuir o judge entre modelos também **destruiria a comparação**: o N3 é
um instrumento só, e julgar metade da amostra com um modelo e metade com outro faria a variação
entre modelos entrar no número como se fosse variação do agente. A T23 congela o judge
justamente contra isso.

O que **não** foi medido é se os outros modelos têm quota maior que 20 — descobrir custa
esgotá-los, um a um.

## 7 · O que isto ainda não estabelece

- **O TPM**, que nunca apareceu: nenhum 429 citou quota de tokens. Não é prova de que não
  exista — é prova de que a nossa carga não chegou lá.
- **Se o limite é por projeto ou por chave.** A documentação diz que rate limits são por
  projeto, não por chave, então trocar de chave dentro do mesmo projeto não compra quota. Não
  testado.

---

## 8 · Fechado em 25/08: o judge saiu da free tier

A consequência de cronograma do §5 — 70 dias para as ~1.400 chamadas do judge — foi resolvida
migrando o judge para o **Vertex**, que serve o mesmo `gemini-3.6-flash` por dynamic shared
quota, sem RPD. O crédito de trial paga (a Gemini API do AI Studio é explicitamente excluída
dele; o Vertex não).

Isso torna obsoleta a saída barata que o §6 registrou e desaconselhou — distribuir o judge entre
modelos para comprar quota nova. Ela deixa de ser necessária, e continua sendo uma má ideia
pelo motivo que o §6 já dava.

Ver `docs/migracao_vertex.md`. Ele também corrige, com medição, a afirmação de que o
`MODELO_PADRAO` era um snapshot datado: o id datado responde 404 nos **dois** provedores, e o
que a T23 congela é o prompt e o id, não o peso do outro lado.
