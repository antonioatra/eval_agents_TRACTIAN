.PHONY: venv install test lint corpus api api-stop clean

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

# Sobe a API industrial do parceiro em localhost:8000 (necessária para `make corpus`).
api:
	cd $(API_DIR) && ../$(API_PY) -m uvicorn app.main:app --app-dir api --port 8000

clean:
	rm -rf $(VENV) .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
