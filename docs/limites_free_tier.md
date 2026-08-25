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

## 5 · O que isto ainda não estabelece

- **Se o `limit: 20` é por minuto ou por dia.** A mensagem nomeia a métrica
  (`..._free_tier_requests`) sem o sufixo `PerMinute`/`PerDay` que o Google usa em outras
  quotas, e os cinco eventos registrados caem todos numa janela de um minuto — compatível com
  as duas leituras. A distinção **muda o cronograma por um fator de dias**: a 20/minuto a
  calibração inteira leva minutos; a 20/dia, ela leva mais de uma semana e as três baterias
  ficam inviáveis na free tier.
- **O TPM**, que nunca apareceu: nenhum 429 citou quota de tokens. Não é prova de que não
  exista — é prova de que a nossa carga não chegou lá.
- **Se o limite é por projeto ou por chave.** A documentação diz que rate limits são por
  projeto, não por chave, então trocar de chave dentro do mesmo projeto não compra quota. Não
  testado.
