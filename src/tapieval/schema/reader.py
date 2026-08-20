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

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import TypeAdapter, ValidationError

from tapieval.schema.trace import RunStart, TraceEvent

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


# ---------------------------------------------------------------------------
# Validação da run (A7) — separada do reader de propósito
#
# O reader precisa conseguir carregar justamente o trace quebrado, senão não há como
# diagnosticá-lo. Quem julga se a run é pontuável é esta função, e quem a chama é o runner
# (T18) ao fechar cada run.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Defeito:
    """Um motivo para a run não ser pontuável. Nunca é falha do agente."""

    tipo: Literal["lacuna_de_seq", "seq_duplicado", "sem_run_start"]
    detalhe: str

    def __str__(self) -> str:
        return f"{self.tipo}: {self.detalhe}"


def validar_trace(eventos: Sequence[TraceEvent]) -> list[Defeito]:
    """Os defeitos estruturais de uma run. Lista vazia = run pontuável.

    `ARQUITETURA §5`, decisão 9: **run com lacuna de `seq` é inválida, não silenciosamente
    pontuada.** Um evento perdido é evidência perdida, e evidência perdida vira, na N1.1 e na
    `cobertura_evidencial`, "o agente não consultou" — a falha do transporte é imputada ao
    modelo, na direção que favorece a conclusão que o trabalho quer defender.

    O que a função devolve é **motivo**, não booleano: o manifesto da bateria guarda a run com
    `valida: false` e o porquê (A7). Run defeituosa **não é apagada** — vira célula faltante
    explícita nas contagens. Descartar em silêncio suporia que runs quebram de forma
    aleatória, e elas não quebram: quebram pelo mesmo motivo, na mesma célula da matriz.

    Três defeitos, e por que cada um:

    - **`lacuna_de_seq`** — evento que sumiu entre a emissão e o disco.
    - **`seq_duplicado`** — dois emissores numerando no mesmo espaço sem coordenação. É o X23
      visto do lado do leitor: em stdio, harness e servidor são processos diferentes, e a
      escolha foi o harness não numerar. Se um `seq` repetir, essa garantia caiu, e a ordem
      total — de que a N2 depende para aderência causal — deixou de existir.
    - **`sem_run_start`** — sem ele `derivar_estado` não acha o `asset_id` da run, e a
      criticidade do ativo nasce nula: a run seria pontuada contra um mundo vazio.

    Trace vazio é `sem_run_start`, não uma quarta categoria: o efeito é o mesmo.
    """
    defeitos: list[Defeito] = []

    if not any(isinstance(evento, RunStart) for evento in eventos):
        defeitos.append(
            Defeito("sem_run_start", "nenhum `RunStart` no trace — a run não tem contexto")
        )

    numeros = sorted(evento.seq for evento in eventos)
    duplicados = sorted({numero for numero in numeros if numeros.count(numero) > 1})
    if duplicados:
        defeitos.append(
            Defeito("seq_duplicado", f"`seq` repetido em {duplicados} — não há ordem total")
        )

    distintos = sorted(set(numeros))
    if distintos:
        faltando = sorted(set(range(distintos[0], distintos[-1] + 1)) - set(distintos))
        if faltando:
            defeitos.append(
                Defeito(
                    "lacuna_de_seq",
                    f"faltam os `seq` {faltando} entre {distintos[0]} e {distintos[-1]}",
                )
            )

    return defeitos
