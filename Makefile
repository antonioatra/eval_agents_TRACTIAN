.PHONY: venv install test lint corpus api api-stop t0b piloto clean

VENV    := .venv
PY      := $(VENV)/bin/python
PIP     := $(VENV)/bin/pip
API_DIR := inteli-tractian-project
API_PY  := $(API_DIR)/api/.venv/bin/python

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

# T19 — bateria piloto e dimensionamento. Exige a API do parceiro no ar (`make api`) e os
# dois modelos carregados com `--parallel 1`: com os 4 slots padrão do LM Studio o KV cache
# de 8192 não comporta o prompt de ~2,6k tokens e a chamada pendura em vez de errar.
#
#   lms load qwen3-14b-mlx --context-length 16384 --parallel 1 --gpu max -y
#   lms load qwen3-8b-mlx  --context-length 16384 --parallel 1 --gpu max -y
piloto: install
	$(PY) -m tapieval.runner --manifest configs/bateria_piloto.yaml --paralelismo 1
	$(PY) scripts/analisar_piloto.py runs/piloto_2026-08-23 --json docs/piloto.json
	$(PY) scripts/medir_overhead_mcp.py --repeticoes 20 --json docs/overhead_mcp.json

# Sobe a API industrial do parceiro em localhost:8000 (necessária para `make corpus`).
api:
	cd $(API_DIR) && ../$(API_PY) -m uvicorn app.main:app --app-dir api --port 8000

clean:
	rm -rf $(VENV) .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
