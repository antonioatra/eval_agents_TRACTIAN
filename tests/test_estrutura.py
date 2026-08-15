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
