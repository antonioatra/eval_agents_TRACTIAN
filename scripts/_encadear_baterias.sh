#!/bin/zsh
# Encadeia principal → mutantes com portão de integridade entre as duas.
# Não é peça do framework: é o operador da noite de 30/08. O portão é o critério
# do PLANO T24-26 — nº de traces == nº de células, e `run_end` com erro < 5%.
set -u
cd "$(dirname "$0")/.."
PY=.venv/bin/python
PID_PRINCIPAL="$1"

while kill -0 "$PID_PRINCIPAL" 2>/dev/null; do sleep 60; done
echo "[$(date +%H:%M)] principal terminou"

$PY - <<'GATE'
import json, pathlib, sys
d = pathlib.Path("runs/principal_2026_08/traces")
traces = sorted(d.glob("*.jsonl"))
fins = []
for t in traces:
    ev = [json.loads(l) for l in t.open()]
    fim = [e for e in ev if e.get("type") == "run_end"]
    fins.append(fim[-1]["status"] if fim else "SEM_run_end")
from collections import Counter
c = Counter(fins)
falhas = sum(v for k, v in c.items() if k in ("falha_do_instrumento", "SEM_run_end"))
print(f"traces={len(traces)}/288  status={dict(c)}  falhas_de_instrumento={falhas}")
if len(traces) < 288:
    print("PORTAO: bateria INCOMPLETA — mutantes NAO roda. Reportada como incompleta, nao descartada.")
    sys.exit(1)
if falhas / len(traces) >= 0.05:
    print(f"PORTAO: falha de instrumento em {falhas/len(traces):.1%} (>= 5%) — mutantes NAO roda.")
    sys.exit(1)
print("PORTAO: ok")
GATE

if [ $? -ne 0 ]; then echo "[$(date +%H:%M)] portao reprovou; parando aqui."; exit 1; fi

echo "[$(date +%H:%M)] iniciando mutantes (150 celulas)"
$PY -m tapieval.runner --manifest configs/bateria_mutantes.yaml --paralelismo 1
echo "[$(date +%H:%M)] mutantes terminou"
