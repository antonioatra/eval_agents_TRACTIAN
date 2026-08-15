"""
Leitura de trace — desserializa o JSONL de volta para os eventos concretos.

Duas responsabilidades, e só elas:

1. Reconstruir o TIPO do evento. `TraceEvent` é união discriminada por `type`, então
   um `TypeAdapter` do pydantic devolve `ToolCall`, `GateEvent` etc. — nunca dict nem
   `BaseEvent`. Sem isso todo consumidor a jusante (`derivar_estado`, scorers) teria
   de reimplementar o despacho por `type`.
2. Ordenar por `seq`, não pela ordem das linhas. São dois emissores escrevendo no
   mesmo arquivo e a notificação MCP é assíncrona: ordem de chegada não é ordem de
   evento. Ordenar por `ts` também seria errado — são dois relógios
   (ARQUITETURA §4.3 e §5, decisão 8).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from tapieval.schema.trace import TraceEvent

_ADAPTADOR_EVENTO: TypeAdapter[TraceEvent] = TypeAdapter(TraceEvent)


def read_trace(path: str | Path) -> list[TraceEvent]:
    """Lê o JSONL de uma run e devolve os eventos ordenados por `seq`.

    Linhas em branco são ignoradas; linha malformada é erro, porque um trace
    parcialmente ilegível pontuado em silêncio é pior que uma run que falha.

    A ordenação é estável: eventos com o mesmo `seq` mantêm a ordem do arquivo.
    Lacuna em `seq` invalida a run (ARQUITETURA §5, decisão 9), mas essa checagem
    é do validador da run, não do reader — que precisa conseguir carregar
    justamente o trace quebrado para diagnosticá-lo.
    """
    caminho = Path(path)
    eventos: list[TraceEvent] = []

    with caminho.open(encoding="utf-8") as arquivo:
        for numero, linha in enumerate(arquivo, start=1):
            if not linha.strip():
                continue
            try:
                eventos.append(_ADAPTADOR_EVENTO.validate_json(linha))
            except ValidationError as erro:
                raise ValueError(f"evento inválido em {caminho}:{numero}") from erro

    eventos.sort(key=lambda evento: evento.seq)
    return eventos
