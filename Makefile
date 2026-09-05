.PHONY: venv install test lint corpus api api-stop t0b piloto judge pontuar repro app copiloto clean

VENV    := .venv
PY      := $(VENV)/bin/python
PIP     := $(VENV)/bin/pip
API_DIR := inteli-tractian-project
API_PY  := $(API_DIR)/api/.venv/bin/python

# As figuras que o repositório sabe regenerar hoje, e o notebook versionado que produz cada
# uma. Uma linha por par; `make repro` executa os notebooks e depois confere que TODAS as
# figuras da lista foram regravadas. É a lista que se estende quando a bateria oficial rodar
# (curva custo × recall, matriz de concordância, pass^k) — ver o comentário de `repro`.
FIG_NOTEBOOKS := notebooks/nb01_exploracao_api.ipynb notebooks/nb02_cobertura_corpus.ipynb \
                 notebooks/nb03_calibracao_judge.ipynb notebooks/nb04_resultados_principais.ipynb \
                 notebooks/nb05_passk_estabilidade.ipynb \
                 notebooks/nb06_severidade_erros.ipynb
FIG_NOMES     := fig01_distribuicao_status fig02_matriz_cobertura fig03_flip_rate \
                 fig04_curva_rubrica fig05_custo_recall_h0 fig06_recall_por_classe_h0 \
                 fig07_h2_funcao_vs_args fig08_ins9_mutantes fig09_passk_curvas \
                 fig10_decomposicao_variancia fig11_severidade_por_modelo fig12_taxonomia_falhas
# Desde a T31 toda figura sai em PNG **e** SVG na mesma chamada de `figuras.exportar`. A
# conferência cobra os dois: um sem o outro é a versão vetorial atrasada em relação à do
# documento, e nada no repositório denunciaria isso.
FIG_ARQUIVOS  := $(foreach f,$(FIG_NOMES),figures/$(f).png figures/$(f).svg)

# `uv` não está disponível neste ambiente; o venv é criado com o venv da stdlib.
$(VENV):
	python3 -m venv $(VENV)
	$(PIP) install -q --upgrade pip

venv: $(VENV)

install: venv
	$(PIP) install -q -e ".[dev]"

test: install
	$(PY) -m pytest -q

lint: install
	$(VENV)/bin/ruff check src tests scripts

# Valida o corpus por dois caminhos: estático (réplica de resolve_mode) e contra a API no ar.
# Usa o venv da API do parceiro, que já tem pyyaml e é quem serve os endpoints.
corpus:
	$(API_PY) scripts/validar_cenarios.py
	$(API_PY) scripts/checar_seeds_na_api.py

# T0b — portão de viabilidade: o modelo escolhe função e argumento com as 18 tools na janela?
# Checa o servidor de inferência primeiro; sem ele o número mediria a ferramenta de servir.
t0b: install
	$(PY) scripts/checar_servidor_de_inferencia.py
	$(PY) scripts/medir_tool_calling.py

# X25 — a passada corrente da piloto vive em UMA variável, e o teste de estrutura reprova se
# ela divergir do diretório que o `docs/anexos/resultados/piloto.json` versionado declara. O defeito
# que isto conserta: o alvo rodava `configs/bateria_piloto.yaml` (1ª passada) e analisava
# `runs/piloto_2026-08-24` enquanto o `docs/anexos/resultados/piloto.json` descrevia a 4ª — então
# `make piloto` sobrescrevia a aritmética do A16 com números de um SUT anterior ao A17 e ao A18.
PILOTO_CONFIG := configs/bateria_piloto_a18b.yaml
PILOTO_DIR    := runs/piloto_2026-08-24c

# T19 — bateria piloto e dimensionamento. Exige a API do parceiro no ar (`make api`) e os
# dois modelos carregados com `--parallel 1`: com os 4 slots padrão do LM Studio o KV cache
# de 8192 não comporta o prompt de ~2,6k tokens e a chamada pendura em vez de errar.
#
#   lms load qwen3-14b-mlx --context-length 16384 --parallel 1 --gpu max -y
#   lms load qwen3-8b-mlx  --context-length 16384 --parallel 1 --gpu max -y
piloto: install
	$(PY) -m tapieval.runner --manifest $(PILOTO_CONFIG) --paralelismo 1
	$(PY) scripts/analisar_piloto.py $(PILOTO_DIR) --json docs/anexos/resultados/piloto.json
	$(PY) scripts/medir_overhead_mcp.py --repeticoes 20 --json docs/anexos/resultados/overhead_mcp.json

# T20 — portão de viabilidade do judge. Fala com o Gemini (A1), então exige GEMINI_API_KEY
# no ambiente ou no .env, e gasta ~4 chamadas da free tier. A mecânica do N3 é provada
# offline pela suíte; isto prova que o modelo do outro lado responde a rubrica.
judge: install
	$(PY) scripts/checar_judge.py

# Pontua N1 e N2 de uma bateria já executada e grava `runs/<dir>/scores.jsonl`.
#
#   make pontuar BATERIA=runs/principal_2026_08
#
# NÃO FALA COM A REDE e não sobe modelo nenhum: N1 e N2 são função pura de (trace, gabarito),
# e `tests/test_repro.py` bloqueia `socket` para provar isso. **N3 é outra passagem** — o
# judge roda noutro dia, sobre os traces já gravados, e é uma questão de RPD
# (`configs/bateria_referencia.yaml`). Os registros saem daqui com `n3=None`, que a taxonomia
# lê como NÃO MEDIDO e nunca como "limpo".
#
# Sai 1 quando a bateria está incompleta — e grava assim mesmo: bateria incompleta é
# REPORTADA como incompleta (`PLANO` T24-26), não descartada.
pontuar: install
	@test -n "$(BATERIA)" || { echo "uso: make pontuar BATERIA=runs/<experiment_id>"; exit 2; }
	$(PY) -m tapieval.scoring --bateria $(BATERIA)

# Reprodutibilidade ponta a ponta. É a demonstração da banca: de um clone limpo até
# uma figura que saiu de trace real, sem passo manual no meio. Instruções em `docs/anexos/REPRODUZIR.md`.
#
#   1. install   venv + dependências;
#   2. replay    pontua N1 e N2 dos 24 traces versionados de `runs/piloto_2026-08-24c/` DUAS
#                vezes — a segunda num processo novo, com outra `PYTHONHASHSEED` — e exige
#                score idêntico campo a campo (`tests/test_repro.py`);
#   3. figuras   reexecuta os notebooks versionados que gravam as figuras.
#
# O PASSO 2 NÃO FALA COM A REDE e não sobe modelo nenhum: N1 e N2 são função pura de
# (trace, gabarito), e o teste bloqueia `socket` para provar isso em vez de supor. É o que
# permite rodá-lo em qualquer máquina. **N3 fica de fora dele de propósito** — é julgamento
# por LLM e varia mesmo a temperatura 0; quem mede essa variação é o *flip rate* (INS.7), e
# exigi-la estável aqui seria asseverar a sorte do endpoint. Não "conserte" isso.
#
# O PASSO 3 EXIGE A API DO PARCEIRO NO AR (`make api`, em outro terminal). Os dois notebooks
# medem a API em vez de confiar na réplica de `resolve_mode`, e não existe fixture gravada,
# de propósito — é a razão de eles existirem. São ~1 min 10 s, quase tudo na varredura de
# seeds do nb01 (67 000 chamadas HTTP locais).
#
# A execução é para FORA da árvore (`--output-dir` num temporário): `--inplace` reescreveria
# os notebooks versionados a cada `make repro` e sujaria o diff da banca com saída de célula.
# As figuras não sofrem com isso — os notebooks as gravam por caminho absoluto em `figures/`.
#
# O NB03 ENTROU EM 26/08 E NÃO PRECISA DA API. Ele lê `runs/*/julgamentos.jsonl` versionados
# e regrava fig03/fig04 sem chamada de rede nenhuma — o judge não roda de novo aqui. O gate da
# API acima continua valendo pelos outros dois, que a medem.
#
# O NB04 ENTROU EM 30/08 E TAMBÉM NÃO PRECISA DA API. Ele lê o gold humano da T22, os
# julgamentos do judge congelado e os traces da calibração — tudo versionado — e regrava a
# curva custo × recall (H0) e o recall por classe. Zero chamada de rede: `scoring/ins.py` é
# função pura de (trace, gabarito, rótulo).
#
# O NB05 ENTROU EM 31/08 E TAMBÉM NÃO PRECISA DA API. Ele lê `runs/principal_2026_08/
# scores.jsonl` versionado e regrava as curvas de `pass^k` e a decomposição de variância.
# `scoring/estabilidade.py` é função pura de `ScoreRecord`.
#
# O NB06 ENTROU EM 01/09 E TAMBÉM NÃO PRECISA DA API. Ele lê o mesmo `scores.jsonl` da
# principal mais o gold humano da T22 e os traces da calibração — tudo versionado — e regrava a
# distribuição de severidade e a taxonomia observada. Ele também reescreve o
# `docs/anexos/apuracao/taxonomia_erros.md`, que é GERADO e não escrito à mão: número digitado num
# markdown envelhece na primeira vez que a bateria muda e ninguém percebe.
#
# ESTENDER é acrescentar o par (notebook, figura) a FIG_NOTEBOOKS/FIG_ARQUIVOS lá em cima: a
# conferência final já reprova, com o nome do arquivo, figura declarada que não apareceu no
# disco.
repro: install
	$(PY) -m pytest -q tests/test_repro.py
	@$(PY) -c "import httpx; httpx.get('http://localhost:8000/openapi.json', timeout=5)" \
	  || { echo "make repro: a API do parceiro não respondeu em localhost:8000." \
	            "Rode \`make api\` em outro terminal e repita."; exit 1; }
	@marca=$$(mktemp) && saida=$$(mktemp -d) && estado=0 && \
	for nb in $(FIG_NOTEBOOKS); do \
	  if [ ! -f "$$nb" ]; then echo "make repro: FALTOU o notebook $$nb"; estado=1; continue; fi; \
	  echo "make repro: executando $$nb"; \
	  $(PY) -m jupyter nbconvert --to notebook --execute --output-dir "$$saida" "$$nb" \
	    || estado=1; \
	done; \
	for fig in $(FIG_ARQUIVOS); do \
	  if [ ! -f "$$fig" ]; then echo "make repro: FALTOU $$fig — nenhum notebook a gravou"; estado=1; \
	  elif [ ! "$$fig" -nt "$$marca" ]; then echo "make repro: FALTOU regravar $$fig — o notebook rodou mas não a escreveu"; estado=1; \
	  else echo "make repro: regravada $$fig"; fi; \
	done; \
	rm -rf "$$saida" "$$marca"; \
	exit $$estado

# A página de inspeção de traces (TAPI §6). Lê a bateria versionada e escreve UM html
# autocontido — sem servidor, sem rede, sem modelo. O placar sai dos JSONs dos notebooks, e é
# regerado antes para que a página nunca cite número mais velho que a bateria.
app: install
	$(PY) scripts/gerar_placar.py
	$(PY) -m tapieval.app --raiz . --saida app/copiloto.html
	@echo "make app: abra app/copiloto.html no navegador (duplo clique serve)"

# A MESMA página, servida — e com a consulta ao vivo ligada: uma pergunta que não está no
# corpus vira cenário executável, roda no runner de verdade contra a API do parceiro, e o trace
# aparece na tela enquanto acontece.
#
# EXIGE OS DOIS SERVIÇOS NO AR, e confere antes de subir:
#   1. a API do parceiro em :8000  ->  `make api` noutro terminal;
#   2. o LM Studio em :1234 com o modelo carregado ->
#        lms load qwen3-8b-mlx --context-length 16384 --parallel 1 --gpu max -y
#
# `make app` continua sendo o plano B do palco: ele grava um html que abre por duplo clique, sem
# servidor, sem rede e sem GPU. Este alvo é o contrário — ele mostra o agente rodando, e por isso
# tem as dependências que o outro existe para não ter. Os dois saem do mesmo template.
copiloto: install
	$(PY) scripts/gerar_placar.py
	$(PY) -m tapieval.vivo --raiz . --porta 7000

# Sobe a API industrial do parceiro em localhost:8000 (necessária para `make corpus`).
api:
	cd $(API_DIR) && ../$(API_PY) -m uvicorn app.main:app --app-dir api --port 8000

clean:
	rm -rf $(VENV) .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
