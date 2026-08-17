"""Servidor MCP por stdio, para o teste que prova *"o framework avalia qualquer agente MCP"*.

Roda num processo separado, falando o protocolo de verdade, e escreve o trace ele mesmo — o
cliente do outro lado não sabe que existe trace, não registra handler nenhum e não coopera de
forma alguma. É essa a prova (`PLANO` T14, "Pronto").

Não é módulo de teste: o nome não casa `test_*.py`, então o pytest não o coleta. É invocado
como script — `python tests/servidor_stdio.py <run_dir> <run_id>`.

A REDE NÃO É ALCANÇADA AQUI. O transporte é `httpx.MockTransport`, igual ao do resto da
suíte: a API do parceiro pode estar de pé na máquina de quem roda os testes, e alcançá-la
faria o teste medir o ambiente de quem rodou. `test_mcp_trace.py` verifica esta propriedade
lendo este arquivo.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import anyio
import httpx
from mcp.server.stdio import stdio_server

from tapieval.env.client import TractianClient
from tapieval.mcp.instrumentacao import ObservadorDeTrace
from tapieval.mcp.server import RunContext, criar_servidor
from tapieval.schema.writer import TraceWriter

# Duplicado de `test_mcp_trace.py` de propósito: importar do módulo de teste faria o
# subprocesso depender da coleta do pytest, e o valor deste apoio está em ser um processo
# comum. Se os dois divergirem, a comparação de traces do teste quebra e diz por quê.
DADOS_DO_ATIVO: dict[str, Any] = {
    "id": "asset_H110",
    "name": "Bomba H-110",
    "company_id": "comp_acme",
    "criticality": "high",
    "plant": "P1",
    "line": "L1",
    "parent_asset_id": None,
    "machine_type": "pump",
    "rotation_rpm": 1780,
    "bearing_pn": "6205",
    "bpfo_hz": 89.1,
    "bpfi_hz": 130.9,
    "bsf_hz": 58.2,
    "ftf_hz": 11.8,
    "line_frequency_hz": 60,
    "sensor_status": "online",
    "points": [],
}

USUARIO: dict[str, Any] = {
    "id": "usr_bruno",
    "name": "Bruno",
    "role": "engenheiro",
    "permissions": ["read", "action_low", "action_high", "escalate"],
    "company_id": "comp_acme",
}


def responder(requisicao: httpx.Request) -> httpx.Response:
    if requisicao.url.path == "/users/me":
        return httpx.Response(200, json=USUARIO)
    if requisicao.method in ("POST", "PATCH"):
        return httpx.Response(
            200, json={"accepted": True, "action_id": "act_1", "message": "ok"}
        )
    return httpx.Response(200, json={"mode": "complete", "notes": None, "data": DADOS_DO_ATIVO})


async def servir(run_dir: Path, run_id: str) -> None:
    ctx = RunContext(
        run_id=run_id,
        cliente=TractianClient(
            "http://api.invalida",
            user_id="usr_bruno",
            seed="s001",
            transport=httpx.MockTransport(responder),
        ),
        observador=ObservadorDeTrace(TraceWriter(run_dir, run_id)),
    )
    servidor = criar_servidor(ctx)
    async with stdio_server() as (leitura, escrita):
        await servidor.run(leitura, escrita, servidor.create_initialization_options())


if __name__ == "__main__":
    anyio.run(servir, Path(sys.argv[1]), sys.argv[2])
