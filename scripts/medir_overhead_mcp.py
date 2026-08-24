#!/usr/bin/env python3
"""T19, item 3 — quanto a fronteira MCP cobra por chamada.

POR QUE ISTO É UM SCRIPT E NÃO SAI DO TRACE DA BATERIA
    O enunciado da T19 pede `latencia_tool − latencia_http` por chamada. O trace registra o
    **subtraendo** e não o minuendo: `ToolResult.latencia_ms` vem do `RawResponse` e cobre só
    a ida e volta HTTP (`env/client.py`), e ninguém cronometra o `session.call_tool` do lado
    do cliente. Dava para instrumentar o agente, mas isso mudaria o SUT no meio do piloto —
    medir a ferramenta com a ferramenta modificada é o que a T0b evitou fazer.

    Então a medição acontece fora: mesmo servidor, mesmo `RunContext`, mesmo transporte em
    memória que a bateria usa, e o relógio nos dois lados da fronteira.

O QUE ENTRA NA DIFERENÇA
    Serialização do pedido, os streams em memória, o despacho do `call_tool`, a validação de
    argumentos, o gate, a classificação da resposta (`env/status.py`), a emissão dos dois
    eventos de trace e a serialização da volta. É a fronteira inteira, não só o transporte —
    e é assim que a pergunta da banca ("MCP não deixa tudo mais lento?") merece ser
    respondida: o número é do desenho, não de uma camada dele.

CACHE DESLIGADO NA MARRA
    O cache de leitura é por run e por `(tool, args)`. Repetir a mesma chamada mediria o
    caminho do cache, que é rápido e não é o que se pergunta. Cada repetição usa um
    `RunContext` novo — e é o que a bateria faz também: um servidor por run.

USO
    python scripts/medir_overhead_mcp.py
    python scripts/medir_overhead_mcp.py --repeticoes 10 --json docs/overhead_mcp.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tapieval.env.client import TractianClient  # noqa: E402
from tapieval.mcp.server import RunContext  # noqa: E402
from tapieval.schema.trace import ToolResult  # noqa: E402
from tapieval.sut.sessao import abrir_sessao  # noqa: E402

BASE_URL_PADRAO = "http://127.0.0.1:8000"
USUARIO = "usr_ana"
SEED = "s001"
ATIVO = "asset_H110"

CHAMADAS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("get_asset", {"asset_id": ATIVO}),
    ("get_current_user", {}),
    ("list_analyses", {"asset_id": ATIVO}),
    ("get_data_quality", {"asset_id": ATIVO}),
    ("get_baseline", {"asset_id": ATIVO}),
)
"""Cinco leituras do caminho quente do corpus. Só leitura: uma ação de impacto passaria pelo
gate e pela idempotência, que são caros de propósito e não são overhead de transporte."""


@dataclass
class Coletor:
    """Observador mínimo: guarda a latência HTTP que o servidor registrou."""

    latencias_http: list[int] = field(default_factory=list)

    def emitir(self, evento: Any) -> None:
        if isinstance(evento, ToolResult):
            self.latencias_http.append(evento.latencia_ms)


@dataclass(frozen=True)
class Medida:
    tool: str
    total_ms: float
    http_ms: float

    @property
    def overhead_ms(self) -> float:
        return self.total_ms - self.http_ms


async def _uma_chamada(base_url: str, tool: str, args: dict[str, Any]) -> Medida:
    coletor = Coletor()
    cliente = TractianClient(base_url, user_id=USUARIO, seed=SEED)
    ctx = RunContext(run_id="overhead", cliente=cliente, observador=coletor)
    try:
        async with abrir_sessao(ctx) as sessao:
            # A primeira chamada da sessão paga o handshake do protocolo; ela não entra na
            # conta, senão o overhead por chamada carregaria um custo que é por run.
            await sessao.list_tools()
            inicio = time.perf_counter()
            await sessao.call_tool(tool, args)
            total_ms = (time.perf_counter() - inicio) * 1000
    finally:
        cliente.close()

    if not coletor.latencias_http:
        raise RuntimeError(
            f"{tool}: nenhum `tool_result` emitido — a chamada não atravessou a fronteira, "
            "e uma diferença calculada sobre zero HTTP seria overhead inventado"
        )
    return Medida(tool, total_ms, float(coletor.latencias_http[-1]))


async def _coletar(base_url: str, repeticoes: int) -> list[Medida]:
    medidas: list[Medida] = []
    for _ in range(repeticoes):
        for tool, args in CHAMADAS:
            medidas.append(await _uma_chamada(base_url, tool, args))
    return medidas


def _resumo(medidas: list[Medida]) -> dict[str, Any]:
    overheads = [m.overhead_ms for m in medidas]
    por_tool: dict[str, Any] = {}
    for tool, _ in CHAMADAS:
        do_tool = [m for m in medidas if m.tool == tool]
        por_tool[tool] = {
            "n": len(do_tool),
            "total_ms_mediana": round(statistics.median(m.total_ms for m in do_tool), 2),
            "http_ms_mediana": round(statistics.median(m.http_ms for m in do_tool), 2),
            "overhead_ms_mediana": round(
                statistics.median(m.overhead_ms for m in do_tool), 2
            ),
        }
    return {
        "n_chamadas": len(medidas),
        "overhead_ms_mediana": round(statistics.median(overheads), 2),
        "overhead_ms_media": round(statistics.fmean(overheads), 2),
        "overhead_ms_p95": round(sorted(overheads)[int(0.95 * (len(overheads) - 1))], 2),
        "overhead_ms_max": round(max(overheads), 2),
        "http_ms_mediana": round(statistics.median(m.http_ms for m in medidas), 2),
        "total_ms_mediana": round(statistics.median(m.total_ms for m in medidas), 2),
        "por_tool": por_tool,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=BASE_URL_PADRAO)
    parser.add_argument("--repeticoes", type=int, default=10)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)

    medidas = anyio.run(_coletar, args.base_url, args.repeticoes)
    resumo = _resumo(medidas)

    print(f"{len(medidas)} chamadas · {args.repeticoes} repetições × {len(CHAMADAS)} tools")
    print(f"  HTTP     mediana: {resumo['http_ms_mediana']:8.2f} ms")
    print(f"  fronteira mediana: {resumo['total_ms_mediana']:8.2f} ms")
    print(
        f"  overhead mediana: {resumo['overhead_ms_mediana']:8.2f} ms  "
        f"(média {resumo['overhead_ms_media']:.2f} · p95 {resumo['overhead_ms_p95']:.2f} · "
        f"máx {resumo['overhead_ms_max']:.2f})"
    )
    for tool, dados in resumo["por_tool"].items():
        print(
            f"    {tool:<20} http {dados['http_ms_mediana']:7.2f}  "
            f"total {dados['total_ms_mediana']:7.2f}  "
            f"overhead {dados['overhead_ms_mediana']:6.2f} ms"
        )

    if args.json:
        args.json.write_text(json.dumps(resumo, indent=2, ensure_ascii=False), "utf-8")
        print(f"\njson → {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
