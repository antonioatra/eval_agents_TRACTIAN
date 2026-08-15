#!/usr/bin/env python3
"""Confere `ambiente.modos_exigidos` de cada cenário contra a API REAL no ar.

Complementa `validar_cenarios.py`, que checa a mesma coisa contra uma réplica de
`api/app/prob.py::resolve_mode`. A réplica pode divergir do original sem ninguém notar — este
script fecha esse buraco batendo no servidor de verdade.

Uso: com a API no ar em localhost:8000,
    inteli-tractian-project/api/.venv/bin/python scripts/checar_seeds_na_api.py
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent
CENARIOS = RAIZ / "scenarios"
BASE = "http://localhost:8000"

ROTA_POR_CATEGORIA = {
    "asset": "/assets/{r}",
    "baseline": "/assets/{r}/baseline",
    "rms": "/assets/{r}/rms",
    "spectrum": "/assets/{r}/spectrum",
    "data_quality": "/assets/{r}/data-quality",
    "analyses": "/assets/{r}/analyses",
    "model": "/models/{r}",
    "company": "/companies/{r}",
    "assets": "/companies/{r}/assets",
}


def rota(recurso: str, categoria: str) -> str | None:
    if categoria == "knowledge":
        termo = recurso.split(":", 1)[1]
        if termo.startswith("kb_"):
            return f"/knowledge/{termo}"
        return "/knowledge/search?q=" + urllib.parse.quote(termo)
    modelo = ROTA_POR_CATEGORIA.get(categoria)
    return modelo.format(r=recurso) if modelo else None


def modo_na_api(caminho: str, seed: str) -> str | None:
    url = f"{BASE}{caminho}" + ("&" if "?" in caminho else "?") + f"seed={seed}"
    req = urllib.request.Request(url)
    req.add_header("x-user-id", "usr_ana")   # qualquer usuário: GETs de consulta não filtram
    with urllib.request.urlopen(req) as resposta:
        return json.load(resposta).get("mode")


def main() -> int:
    confirmadas = 0
    problemas: list[str] = []

    for caminho in sorted(CENARIOS.glob("*.yaml")):
        if caminho.name.startswith("_"):
            continue
        cenario = yaml.safe_load(caminho.read_text())
        seed = cenario["ambiente"]["env_seed"]
        for exigido in cenario["ambiente"].get("modos_exigidos", []):
            recurso, categoria = exigido["recurso"], exigido["categoria"]
            endpoint = rota(recurso, categoria)
            if endpoint is None:
                problemas.append(f"{cenario['id']}: categoria sem rota conhecida ({categoria})")
                continue
            try:
                obtido = modo_na_api(endpoint, seed)
            except urllib.error.URLError as erro:
                problemas.append(f"{cenario['id']}: {endpoint} falhou ({erro})")
                continue
            if obtido in exigido["modos"]:
                confirmadas += 1
            else:
                problemas.append(
                    f"{cenario['id']} seed={seed}: {recurso}/{categoria} -> API diz {obtido}, "
                    f"YAML espera {exigido['modos']}"
                )

    print(f"{confirmadas} exigências confirmadas contra a API real")
    if problemas:
        print("\nPROBLEMAS:")
        for p in problemas:
            print(f"  - {p}")
        return 1
    print("OK — toda env_seed produz na API o modo que o cenário declara.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
