"""T0 — a estrutura do pacote existe e importa.

Teste mínimo por desenho: prova que o layout de `src/tapieval` é importável e versionado,
que é o critério de pronto de T0. Cada subpacote ganha teste próprio na sua task.
"""
from __future__ import annotations

import importlib

import pytest

SUBPACOTES = ["schema", "env", "mcp", "sut", "scoring", "runner"]


def test_versao():
    import tapieval

    assert tapieval.__version__ == "0.1.0"


@pytest.mark.parametrize("nome", SUBPACOTES)
def test_subpacote_importa(nome):
    assert importlib.import_module(f"tapieval.{nome}") is not None


def test_schema_de_trace_foi_movido():
    """`schema_trace.py` da raiz virou `tapieval.schema.trace` (T0)."""
    trace = importlib.import_module("tapieval.schema.trace")
    assert trace.__doc__ and "trace" in trace.__doc__.lower()


def test_x25_o_alvo_piloto_do_makefile_e_o_piloto_json_falam_da_mesma_passada():
    """X25 — `make piloto` não pode analisar uma passada e sobrescrever o JSON de outra.

    O alvo rodava a 1ª passada e escrevia por cima do `docs/piloto.json` da 4ª, que é a base
    da aritmética do A16. As três fontes (manifesto do alvo, diretório do alvo, diretório
    declarado no JSON versionado) têm de nomear o mesmo experimento.
    """
    import json
    import re
    from pathlib import Path

    raiz = Path(__file__).resolve().parent.parent
    makefile = (raiz / "Makefile").read_text()

    def variavel(nome: str) -> str:
        achado = re.search(rf"^{nome}\s*:=\s*(\S+)", makefile, re.MULTILINE)
        assert achado, f"Makefile perdeu a variável {nome} — o X25 volta sem ela"
        return achado.group(1)

    config = variavel("PILOTO_CONFIG")
    diretorio = variavel("PILOTO_DIR")

    # O alvo tem de usar as variáveis, não caminhos soltos.
    assert "--manifest $(PILOTO_CONFIG)" in makefile
    assert "analisar_piloto.py $(PILOTO_DIR)" in makefile

    experiment_id = re.search(
        r"^experiment_id:\s*(\S+)", (raiz / config).read_text(), re.MULTILINE
    ).group(1)
    assert diretorio == f"runs/{experiment_id}", (
        f"{config} roda `{experiment_id}` e o alvo analisa `{diretorio}`"
    )

    declarado = json.loads((raiz / "docs" / "piloto.json").read_text())["diretorio"]
    assert declarado == diretorio, (
        f"`make piloto` analisaria `{diretorio}` e o docs/piloto.json versionado descreve "
        f"`{declarado}` — rodar o alvo sobrescreveria os números de outra passada"
    )
