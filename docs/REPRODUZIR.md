# Reproduzir

Como sair de um clone limpo e chegar a uma figura que veio de trace real, sem passo manual no
meio. É o que `make repro` faz.

Este documento é para quem não acompanhou o projeto. Ele diz o que é preciso ter instalado, o
que cada passo faz, **o que reproduz byte a byte e o que não reproduz — com o motivo** — e
quanto tempo leva.

---

## 1. Pré-requisitos

Nem todo passo precisa de tudo. A tabela é o mapa; os detalhes vêm depois.

| Precisa de | `make test` | `make repro` | reexecutar a piloto (`make piloto`) |
|---|---|---|---|
| Python + venv do projeto | ✅ | ✅ | ✅ |
| API industrial do parceiro no ar (`localhost:8000`) | — | ✅ | ✅ |
| LM Studio com os dois modelos carregados | — | — | ✅ |
| `GEMINI_API_KEY` (judge N3) | — | — | só para `make judge` |

### Python

`pyproject.toml` declara `requires-python = ">=3.11"`. Os números deste documento foram
medidos em **CPython 3.14.6** (macOS 15, Apple Silicon), que é o interpretador dos venvs deste
repositório — inclusive o da API do parceiro.

```bash
make install     # cria .venv com o venv da stdlib e instala -e ".[dev]"
```

`uv` **não** é usado aqui: o `Makefile` da raiz cria o venv com `python3 -m venv` de propósito,
porque `uv` não estava disponível na máquina em que o projeto foi feito.

### A API industrial do parceiro

Ela mora em `inteli-tractian-project/` e é o ambiente que os cenários exercitam. Os dados
(`inteli-tractian-project/data/*.parquet`) **estão versionados**, então não é preciso rodar
`make data`. Falta só o venv dela, que é ignorado pelo git por causa do tamanho (289 MB).

O caminho oficial do parceiro usa `uv`:

```bash
cd inteli-tractian-project && make setup
```

Sem `uv`, o equivalente com a stdlib — é o que existe nesta máquina:

```bash
cd inteli-tractian-project/api
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Depois, em **outro terminal**, deixe a API no ar enquanto roda o `repro`:

```bash
make api     # uvicorn em http://localhost:8000  ·  Swagger em /docs
```

`make repro` confere isso antes de executar os notebooks e para com a mensagem
`a API do parceiro não respondeu em localhost:8000` se ela estiver fora. Não há fixture gravada
da API, **de propósito**: o valor dos dois notebooks é justamente medir o servidor de verdade em
vez de confiar na réplica de `resolve_mode` que vive em `scripts/validar_cenarios.py`.

### LM Studio — só para reexecutar a piloto

`make repro` **não** precisa disto. Só `make piloto` (que roda o agente de novo) precisa, e a
configuração não é opcional:

```bash
lms load qwen3-14b-mlx --context-length 16384 --parallel 1 --gpu max -y
lms load qwen3-8b-mlx  --context-length 16384 --parallel 1 --gpu max -y
```

**Por que `--parallel 1`, e por que isso é um achado da piloto** (cabeçalho de
`configs/bateria_piloto.yaml`): com os quatro slots de concorrência que o LM Studio carrega por
padrão, o KV cache de 8192 é dividido entre eles e um prompt de ~2,6k tokens — 18 schemas de
tool na janela — não cabe. A chamada **não erra: ela pendura**, e a run morre em 300 s de
timeout com `n_llm_calls=0`. A mesma chamada num modelo carregado com
`--parallel 1 --context-length 16384` volta em 17 s.

---

## 2. O que `make repro` faz

```bash
make repro
```

Três passos, nesta ordem, e o alvo para no primeiro que falhar.

### Passo 1 — `install`

Cria `.venv` se não existir e instala o pacote em modo editável com as dependências de
desenvolvimento.

### Passo 2 — replay determinístico (`tests/test_repro.py`)

Pontua **N1 e N2 dos 24 traces reais** de `runs/piloto_2026-08-24c/` — 6 cenários de dev × 2
modelos (`qwen3-8b`, `qwen3-14b`) × 2 `sample_seed` (11, 23) — **duas vezes**, e exige score
idêntico campo a campo.

As duas passadas não são iguais entre si de propósito:

- a primeira dupla roda no mesmo processo, com o cache do contrato de regras **limpo** entre
  elas: prova que a segunda pontuação não depende de estado deixado pela primeira;
- a segunda dupla roda em **dois subprocessos com `PYTHONHASHSEED` diferente** (1 e 99991).
  Essa é a que pega a classe de não-determinismo mais comum em Python — um campo de lista
  montado a partir de um `set`, cuja ordem de iteração é fixada na partida do interpretador.
  Repetir a chamada no mesmo processo **não** pega isso.

Quando um campo diverge, a mensagem nomeia o trace, a camada, o campo e os dois valores:

```
replay depende da semente de hash (PYTHONHASHSEED=1 × 99991):
  aut_01_barulho_sem_desvio--qwen3-14b--base--envs001--n11 · n1.tools_extras:
    1ª=['escalate_case', 'search_knowledge', 'get_spectrum']
    2ª=['escalate_case', 'get_spectrum', 'search_knowledge']
```

Este passo **não fala com a rede e não sobe modelo nenhum**. O teste
`test_pontuar_nao_abre_socket` bloqueia o construtor de `socket.socket` durante a pontuação, de
modo que qualquer cliente HTTP que aparecesse ali embaixo reprovaria — não é suposição, é
verificação. Isso é o que torna o passo 2 rodável em qualquer máquina, inclusive em CI.

### Passo 3 — figuras

Reexecuta os notebooks versionados que gravam as figuras:

| Notebook | Figura |
|---|---|
| `notebooks/nb01_exploracao_api.ipynb` | `figures/fig01_distribuicao_status.png` |
| `notebooks/nb02_cobertura_corpus.ipynb` | `figures/fig02_matriz_cobertura.png` |

A execução é **para fora da árvore** (`nbconvert --output-dir` num diretório temporário):
`--inplace` reescreveria os notebooks versionados a cada `make repro` e sujaria o diff com saída
de célula. As figuras não sofrem com isso porque os notebooks as gravam por caminho absoluto em
`figures/`.

No fim o alvo confere, arquivo por arquivo, se cada figura **existe** e se foi **regravada nesta
execução** (mais nova que a marca criada no início). Se alguma faltar, ele diz qual e sai com
erro — não há como o `make repro` ficar verde sobre uma figura velha.

Estender é acrescentar o par `(notebook, figura)` a `FIG_NOTEBOOKS` / `FIG_ARQUIVOS`, no topo do
`Makefile`.

---

## 3. O que é determinístico e o que não é

Esta seção é a que importa numa banca. **Cada linha tem um motivo, e nenhum deles é "não deu
tempo".**

### Reproduz byte a byte

| O quê | Por quê |
|---|---|
| **N1 (`METRICAS §2`)** — F1 de seleção, acurácia de argumentos, decisão, ação indevida, gate, citações | `pontuar_n1` é função pura de `(eventos, cenário)`: sem relógio, sem I/O, sem estado global. O gabarito é YAML versionado; o trace é JSONL imutável. |
| **N2 (`METRICAS §3`)** — aderência causal, Kendall τ, redundância, volume, budget, `parse_erro` | Mesma pureza, com a trajetória de referência **injetada** em vez de lida de dentro do scorer. Todo campo de lista sai `sorted()` ou na ordem do YAML, nunca na ordem de iteração de um `set`. |
| **As duas figuras atuais** | Verificado: duas execuções independentes de `make repro` produziram PNGs com o **mesmo md5** (`d17e7fdf…` e `e150c951…`). Vale para a mesma máquina, mesmas versões de `plotly`/`kaleido` e mesmas fontes instaladas — não é garantia entre máquinas. |
| **A taxonomia de falhas** | Congelada por sha256 (`scoring/severidade.sha_da_taxonomia`), que entra em todo `ScoreRecord` (`METRICAS §9.3`). |

Por trás disso está a decisão 1 de `ARQUITETURA §5`: **trace imutável, scores derivados.** Mudar
um scorer não pode exigir reexecutar o agente, e `scores/v1/` e `scores/v2/` coexistem sobre os
mesmos traces. Essa promessa só vale se a derivação for uma função — e é exatamente isso que o
passo 2 verifica.

### NÃO reproduz, e está certo assim

| O quê | Por quê |
|---|---|
| **N3 — LLM-as-judge (`METRICAS §4`)** | É julgamento por LLM. Mesmo a temperatura 0, o endpoint pode variar: amostragem, versão do modelo servido, empate numérico. **Por isso N3 fica deliberadamente fora do `make repro`.** Um teste que exigisse N3 estável estaria asseverando a sorte do endpoint e reprovaria a suíte por algo que não é defeito do instrumento. A instabilidade não é ignorada — ela **é** o objeto de medição do *flip rate* (INS.7, `METRICAS §7`: o judge 5× sobre os mesmos itens, % de mudança por campo), que roda como experimento e alimenta a reescrita da rubrica. Quem for "consertar" isto acrescentando N3 ao teste não está consertando: está trocando uma propriedade que vale por uma que não vale. |
| **Reexecutar o agente (`make piloto`)** | O SUT é um LLM local a `temperature 0.7` e sem `seed` fixada no servidor (ver `runs/piloto_2026-08-24c/manifest.json`). Duas execuções da mesma célula produzem traces diferentes — é o que o `sample_seed` e o `pass^k` existem para medir, não um defeito a eliminar. **É por isso que o `repro` pontua traces GRAVADOS em vez de rodar o agente:** o que se prova aqui é que o instrumento é uma função, não que o modelo é. |
| **Duração, latência e contagem de tokens** | Dependem de máquina, carga e servidor. Entram no trace como fato daquela execução; nenhuma métrica de `METRICAS §2`–`§3` depende delas. |
| **O modo de retorno da API** | É função pura de `(seed, recurso, categoria)` — reproduzir o ambiente é passar a `env_seed` canônica do cenário, não gravar cassete. Foi por isso que a camada de record/replay (T6) foi cortada em 14/08, e é por isso que `manifest.json` traz `cassette_id: null`. |

---

## 4. Quanto tempo leva

Medido nesta máquina, com a API já no ar:

| Passo | Tempo |
|---|---|
| `make install` num clone limpo (baixa pydantic, pandas, plotly, kaleido, scipy, jupyter) | ~1–3 min |
| `make install` com o venv já pronto | ~2 s |
| Passo 2 — replay dos 24 traces, 5 testes | **~1 s** |
| Passo 3 — os dois notebooks (nb01 varre 67 000 chamadas HTTP locais) | ~1 min 10 s |
| **`make repro` inteiro, venv pronto** | **~1 min 15 s** |
| `make test` — a suíte completa (834 testes) | ~5 s |

Para comparação, o que **não** está no `repro`: a passada gravada em `runs/piloto_2026-08-24c/`
levou **1 h 16 min de relógio** (48 min somando só a duração das 24 runs) com
`--paralelismo 1`, e as três baterias de `METRICAS §9.2` somam 600 execuções. É a diferença
entre demonstrar o instrumento e reexecutar o experimento.

---

## 5. O que o `make repro` ainda não regenera

Declarado para não parecer omissão: **as figuras de resultado não existem porque as baterias não
rodaram.** Não há dado de onde tirá-las, e o alvo não inventa figura.

Ficam de fora, e voltam para a lista do `Makefile` quando a bateria oficial gravar seus scores:

- a **curva custo × recall** (`METRICAS §10`, a figura principal do projeto);
- a **matriz de concordância** N1+N2 × N3 sobre falhas;
- o **ΔRecall sob mutantes** e o **pass^k por modelo**.

Também fica de fora a escrita dos scores em disco: o layout
`runs/<experiment_id>/scores/<scorer_version>/<run_id>.json` de `ARQUITETURA §5` está
especificado mas ainda não é produzido por nenhum código — o `ScoreRecord` completo depende de
N3 e da agregação de `score_final`. Hoje o passo 2 pontua N1 e N2 em memória e compara; quando o
escritor existir, ele entra entre os passos 2 e 3.

---

## 6. Quando algo falha

| Sintoma | O que é |
|---|---|
| `a API do parceiro não respondeu em localhost:8000` | Falta `make api` em outro terminal. |
| `FALTOU <figura> — nenhum notebook a gravou` | Figura declarada em `FIG_ARQUIVOS` sem notebook que a produza. |
| `FALTOU regravar <figura> — o notebook rodou mas não a escreveu` | O notebook executou mas a célula da figura não gravou. Abra o `.ipynb` executado (o `nbconvert` escreve num temporário) e veja o erro da célula. |
| `replay não determinístico:` / `replay depende da semente de hash` | **É o achado, não o teste.** Algum scorer deixou de ser função pura do trace. A mensagem nomeia o campo, o trace e os dois valores. |
| `a pontuação N1/N2 tentou abrir socket` | Um caminho de pontuação passou a depender de um servidor no ar. `ARQUITETURA §5`, decisão 1, cai junto. |
| `esperados 24 traces … achados N` | O conjunto de replay encolheu. Um teste de determinismo sobre zero trace passa sempre — por isso o número é verificado. |
