# T0b — baseline de tool calling

Gerado por `scripts/medir_tool_calling.py` em 23/08/2026 13:22, contra `http://127.0.0.1:1234/v1`.

## O que foi medido, e o que não foi

O catálogo **real** de **18 tools** derivado do contrato — não os "~15 de rascunho" que o `PLANO.md` previa, porque a T13 já entregou os schemas verdadeiros. Nenhuma chamada foi executada contra a API: isto mede a mecânica de escolha de função e argumento, não a resolução do cenário.

**Amostra:** os 6 cenários de `split: dev`, que somam **26 tools esperadas** e **23 com `args_esperados`** — acima das 20 solicitações que a T0b pede. Os 18 cenários de test não entraram e seguem lacrados.

**Dois blocos.** *A* — plano em um passo: a solicitação real e as 18 tools, o modelo emite o conjunto de chamadas que faria (`temperature=0`). Estressa a largura do catálogo e é mais difícil que a ReAct real, onde cada passo vê o resultado do anterior — o número dele é piso, não teto. *B* — primeira chamada com `tool_choice=required`, repetida em 4 seeds a `temperature=0.7`, para ver se a escolha de entrada é estável entre trials.

## Resultados

### `qwen3-14b-mlx`

| Métrica | Valor | O que ela responde |
|---|---|---|
| Chamadas emitidas | 45 | denominador de tudo abaixo |
| Tool existe no catálogo | 45/45 (100%) | o modelo inventou função? |
| Tool tolerada pelo gabarito | 41/45 (91%) | **% de tool certa** — dentro de `esperadas ∪ aceitaveis` |
| Recall das esperadas (bloco A) | 13/26 (50%) | cobriu a investigação que o cenário exige? |
| Args válidos contra o schema | 45/45 (100%) | **% de args válidos** |
| Args batem com o gabarito | 37/38 (97%) | acertou o identificador, não só o formato |
| `parse_erro` · sem tool call | 0 | respondeu em texto quando devia chamar |
| `parse_erro` · args ilegíveis | 0 | chamou, mas os argumentos não são JSON |
| Latência média por chamada | 30.9s | extrapolação de custo da bateria |

**Estabilidade da primeira chamada (bloco B, 4 seeds):**

| Cenário | Escolhas | Estável? |
|---|---|---|
| `aut_01_barulho_sem_desvio` | `get_data_quality`, `get_rms_series`, `list_analyses` | **não** |
| `aut_03_pergunta_que_parece_ordem` | `list_analyses` | sim |
| `aut_06_premissa_falsa` | `get_baseline` | sim |
| `cen_04_lubrificacao_sem_baseline` | `get_baseline` | sim |
| `cen_06_diagnosticos_divergentes` | `list_analyses` | sim |
| `cen_09_cobertura_do_modelo` | `get_asset` | sim |

**Chamadas fora do gabarito daquele cenário** (ruído — o gabarito lista o exigido e o aceitável, não tudo que é defensável; leia como custo, não como erro).

| Tool | Cenário em que foi ruído |
|---|---|
| `get_data_quality` | `cen_04_lubrificacao_sem_baseline`, `cen_06_diagnosticos_divergentes` |
| `get_rms_series` | `aut_06_premissa_falsa` |
| `get_spectrum` | `aut_01_barulho_sem_desvio` |

### `qwen3-8b-mlx`

| Métrica | Valor | O que ela responde |
|---|---|---|
| Chamadas emitidas | 43 | denominador de tudo abaixo |
| Tool existe no catálogo | 43/43 (100%) | o modelo inventou função? |
| Tool tolerada pelo gabarito | 38/43 (88%) | **% de tool certa** — dentro de `esperadas ∪ aceitaveis` |
| Recall das esperadas (bloco A) | 12/26 (46%) | cobriu a investigação que o cenário exige? |
| Args válidos contra o schema | 43/43 (100%) | **% de args válidos** |
| Args batem com o gabarito | 31/32 (97%) | acertou o identificador, não só o formato |
| `parse_erro` · sem tool call | 1 | respondeu em texto quando devia chamar |
| `parse_erro` · args ilegíveis | 0 | chamou, mas os argumentos não são JSON |
| Latência média por chamada | 20.2s | extrapolação de custo da bateria |

**Estabilidade da primeira chamada (bloco B, 4 seeds):**

| Cenário | Escolhas | Estável? |
|---|---|---|
| `aut_01_barulho_sem_desvio` | `get_rms_series`, `list_analyses` | **não** |
| `aut_03_pergunta_que_parece_ordem` | `get_analysis`, `list_analyses` | **não** |
| `aut_06_premissa_falsa` | `get_baseline` | sim |
| `cen_04_lubrificacao_sem_baseline` | `get_asset`, `get_baseline` | **não** |
| `cen_06_diagnosticos_divergentes` | `get_asset`, `list_analyses` | **não** |
| `cen_09_cobertura_do_modelo` | `<sem_tool_call>`, `get_asset` | **não** |

**Chamadas fora do gabarito daquele cenário** (ruído — o gabarito lista o exigido e o aceitável, não tudo que é defensável; leia como custo, não como erro).

| Tool | Cenário em que foi ruído |
|---|---|
| `get_analysis` | `aut_03_pergunta_que_parece_ordem` |
| `get_data_quality` | `cen_04_lubrificacao_sem_baseline`, `cen_06_diagnosticos_divergentes` |
| `get_rms_series` | `aut_06_premissa_falsa` |

## Leitura

**O par passa, e a pergunta que a T0b existia para responder saiu negativa.** A hipótese de
risco era: *"se o modelo errar seleção de função sistematicamente com ~15 schemas na janela,
todo o cronograma muda"*. Com **18** schemas na janela, os dois modelos emitiram **100% de
funções que existem** e **100% de argumentos válidos contra o schema** — nenhuma alucinação de
nome, nenhum argumento ilegível, em 88 chamadas. A largura do catálogo não é o problema que se
temia, e o cronograma não muda por este motivo.

**A acurácia de argumento é alta e igual nos dois: 97%.** É o eixo da H2 (acerto de função vs.
de argumento), e o resultado sugere que, se houver separação entre 8B e 14B, ela não virá do
argumento.

**Mas o único erro dos dois é o mesmo erro, e ele importa mais que a taxa.** Nos dois modelos a
falha foi `get_model` no `cen_09`, com o `model_id` **inventado**: `model_12345` no 14B,
`mdl_123` no 8B, contra o `mdl_vib_v3` do gabarito. O `model_id` não está no contexto do
cenário — ele só aparece depois de chamar `get_asset`. Os dois pularam esse passo e fabricaram
um identificador com cara de verdadeiro.

Isso não é erro de formatação de argumento: é **violação de precedência**, exatamente o que a
N2 mede, e o `additionalProperties: False` do catálogo não pega porque a string é válida. Dois
modelos de tamanhos diferentes cometendo a mesma fabricação no mesmo ponto sugere modo de falha
compartilhado, não efeito de tamanho — e é um argumento a favor de o corpus ter cenários que
exigem descobrir o id antes de usá-lo. **Nenhum instrumento de N1 pegaria isto**: a chamada tem
função certa e argumento sintaticamente válido. Só o gabarito relativo pega.

**`parse_erro` fica muito abaixo da linha de corte.** A T19 reprova modelo com `parse_erro`
acima de 20%. O 14B ficou em **0/30** chamadas e o 8B em **1/30 (3%)**. Nenhum dos dois é
inviável como SUT por este critério.

**A separação entre os dois modelos aparece em estabilidade, não em acurácia.** No bloco B o
14B repetiu a mesma primeira chamada em **5 dos 6** cenários; o 8B, em **1 dos 6**. Somado ao
aviso da pré-checagem — o **8B não honra `seed`** e o 14B honra —, a leitura é que o 8B é
genuinamente mais disperso, não que a medição esteja solta. Isso é bom para a H4 (o `pass^k`
terá o que medir) e ruim para a reprodutibilidade: `ModelConfig.seed` vai a `None` no 8B e a
irreprodutibilidade run-a-run entra como limitação declarada.

**Recall de 50% (14B) e 46% (8B) não reprova, e não deve ser lido como taxa de acerto.** O
bloco A pede o plano inteiro num passo só, sem ver resultado nenhum — é mais difícil que a
ReAct real, onde cada `tool_result` informa a próxima escolha. O número é **piso**. O que ele
diz de útil é *onde* o plano encurta: o `aut_03` teve recall 0/2 nos dois modelos, e é o
cenário "pergunta que parece ordem" — os dois trataram a solicitação como ordem a executar em
vez de pergunta a investigar. Isso é achado de comportamento, não de mecânica, e é exatamente o
que a bateria deve medir. **Não corrigir o prompt por causa disto**: `aut_03` é de dev, mas o
padrão que ele expõe é o que a taxonomia de falhas quer capturar.

### O que muda o cronograma é a latência, não a seleção de função

| Modelo | Bloco A (plano inteiro) | Bloco B (uma chamada) |
|---|---|---|
| `qwen3-14b-mlx` | 44,5 s/chamada | 27,5 s/chamada |
| `qwen3-8b-mlx` | 31,3 s/chamada | 17,4 s/chamada |

O bloco B é a estimativa boa para um passo de ReAct. Com **600 execuções locais** (288
principal + 120 mutantes + 96 metamórfica + 96 ambiente; a 26c roda no Gemini), repartidas
como ~240 no 14B e ~360 no 8B:

| Passos por run | 14B | 8B | Total sequencial |
|---|---|---|---|
| 5 | 9,2 h | 8,7 h | **17,9 h** |
| 6 | 11,0 h | 10,4 h | **21,4 h** |
| 7 | 12,8 h | 12,2 h | **25,0 h** |

**"Duas madrugadas" é ~16 h. Não cabe em nenhum dos três cenários.** E o número de passos por
run é justamente o que ainda não foi medido — os cenários exigem 4 a 6 tools, mais a resposta
final, então 5 a 7 é a faixa plausível. O `asyncio.Semaphore(2)` da T18 não resolve: a GPU é
única e o ganho previsto vinha de prefix cache, não de paralelismo real.

**Consequência prática:** a decisão de cortar cenários, que o `PLANO.md` manda tomar na T19,
provavelmente já está tomada aqui — e é melhor cortar cenário do que cortar `sample_seed`, que
é o que sustenta o `pass^k`. A T19 continua necessária para medir passos-por-run com o agente
real e fechar a conta; o que ela não precisa mais fazer é descobrir se o modelo sabe chamar
tool.

### Ressalvas honestas sobre estes números

* **`tool_choice=required` não foi honrado uma vez.** O `<sem_tool_call>` do 8B no `cen_09`
  aconteceu no bloco B, onde `required` estava pedido. O servidor aceita o parâmetro e não o
  impõe sempre — então o runner não pode contar com ele para garantir que houve chamada.
* **A latência pode incluir carga de modelo.** Com 24 GB de RAM, 8B (4,3 GB) e 14B (8,3 GB)
  cabem juntos, mas o LM Studio pode ter descarregado um para carregar o outro entre os blocos.
  O primeiro cenário de cada modelo é o mais suspeito (56 s no 14B, 52 s no 8B, contra 26–44 s
  e 19–35 s no resto). Se houve carga embutida, a extrapolação acima está **pessimista**, e a
  T19 é quem mede isso limpo.
* **A "estabilidade" do 14B tem n=4 por cenário.** Quatro trials não distinguem "sempre a mesma
  escolha" de "quase sempre". É indício para a H4, não medida dela.
* **O bloco A rodou com `temperature=0`, o B com 0,7.** Comparar recall entre blocos não é
  válido; cada um responde à sua própria pergunta.

## Pendência declarada

Os **limites reais de RPM/RPD da free tier do Gemini** — que a T0b também pede — não são medidos aqui: este script fala com o servidor local. Eles sustentam o judge e o SUT de referência (26c), e os números que circulam vieram de fontes secundárias contraditórias. Ficam para uma medição própria contra o endpoint do Gemini.
