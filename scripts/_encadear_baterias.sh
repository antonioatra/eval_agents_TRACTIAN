#!/bin/zsh
# Encadeia principal → mutantes, com o portão de `_portao_da_bateria.py` entre as duas.
# Operador da noite de 30/08, não peça do framework. O portão é o critério do PLANO T24-26 e
# conta `falha_do_instrumento` + trace inválido — NÃO o `error` do trace, que é resultado do
# experimento (ParseErro do modelo entra na taxonomia como medida).
set -u
cd "$(dirname "$0")/.."
PY=.venv/bin/python
PID_PRINCIPAL="$1"

while kill -0 "$PID_PRINCIPAL" 2>/dev/null; do sleep 60; done
echo "[$(date '+%m-%d %H:%M')] principal terminou"

if ! $PY scripts/_portao_da_bateria.py runs/principal_2026_08; then
  echo "[$(date '+%m-%d %H:%M')] portao reprovou; mutantes NAO roda."
  exit 1
fi

echo "[$(date '+%m-%d %H:%M')] iniciando mutantes (150 celulas)"
$PY -m tapieval.runner --manifest configs/bateria_mutantes.yaml --paralelismo 1
echo "[$(date '+%m-%d %H:%M')] mutantes terminou"
$PY scripts/_portao_da_bateria.py runs/mutantes_2026_08
