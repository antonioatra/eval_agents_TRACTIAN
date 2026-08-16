"""
Injeção de falhas — só o que a API do parceiro não produz.

POR QUE ESTE MÓDULO É PEQUENO, E POR QUE ISSO É O DESENHO
    T6 (proxy record/replay) foi cortada em 14/08 pelo mesmo motivo que encolhe este módulo:
    o modo de retorno é função pura de `(seed, recurso, categoria)` (`ARQUITETURA §3.4`).
    `partial`, `inconclusive`, `conflict` e `unavailable` não precisam ser fabricados — se
    pedem à API escolhendo a seed, e é o que `ambiente.env_seed` faz em cada cenário. Um
    injetor que os simulasse estaria reimplementando uma função hash que já existe, com o
    risco extra de simular errado.

    O que sobra é o que a API nunca faz: não responder, responder com corpo que não é JSON,
    e responder 5xx. Três falhas.

POR QUE DETERMINÍSTICO, NUNCA SORTEADO
    `quando` é o número da ocorrência, não uma probabilidade. Falha sorteada tornaria a run
    irreprodutível, e o framework inteiro se apoia em "mesmo trace, mesmo score"
    (`ARQUITETURA §5`, decisão 1). Uma bateria com injeção probabilística não é comparável
    consigo mesma.

ONDE ELE ENTRA
    Entre `TractianClient` e o classificador de status: recebe a `RawResponse` que voltou e
    devolve a que o resto da pilha vai ver. Ele não sabe nada de trace nem de `seq` — quem
    grava o evento é o servidor MCP (T13), que já grava a resposta real.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from tapieval.env.client import RawResponse

Falha = Literal["timeout", "corpo_malformado", "http_500"]

FALHAS_CONHECIDAS: frozenset[str] = frozenset({"timeout", "corpo_malformado", "http_500"})

CURINGA = "*"

# O que um proxy no caminho devolve quando a API não respondeu: HTML, não JSON. É a forma
# concreta que `corpo_malformado` assume, e é o que o classificador tem de sobreviver.
CORPO_MALFORMADO = "<html><head><title>502 Bad Gateway</title></head><body>502</body></html>"


@dataclass(frozen=True)
class FaultSpec:
    """Uma falha declarada. Imutável: é configuração de experimento, entra no manifesto."""

    tool: str
    """Nome da tool (`operationId` snake_case) ou `"*"` para todas."""

    falha: Falha
    quando: int | None = None
    """Ocorrência em que a falha dispara, 1-based. `None` = todas as ocorrências."""

    timeout_ms: int = 10_000
    """Latência gravada quando `falha == "timeout"`. Só faz sentido nesse caso.

    O valor importa: o tempo até o timeout é o dado relevante quando a API não responde, e
    zerá-lo faria a run parecer rápida justamente onde ela travou."""


class FaultInjector:
    """Aplica as falhas declaradas às respostas, contando ocorrências por tool.

    Stateful de propósito — `quando` exige contador. O estado é reprodutível a partir da
    sequência de chamadas, então duas execuções da mesma run continuam idênticas; é
    `reset()` que garante isso entre runs de uma mesma bateria.
    """

    def __init__(self, specs: Iterable[FaultSpec]) -> None:
        self.specs: Sequence[FaultSpec] = tuple(specs)
        # Cópia, e não referência: a lista é do chamador e pode mudar depois. Uma spec que
        # sumisse no meio da bateria trocaria o ambiente em silêncio.

        desconhecidas = sorted({spec.falha for spec in self.specs} - FALHAS_CONHECIDAS)
        if desconhecidas:
            raise ValueError(
                f"falha desconhecida: {desconhecidas}. Conhecidas: {sorted(FALHAS_CONHECIDAS)}. "
                "Degradação de payload não se injeta — se pede à API por `env_seed`."
            )

        self._ocorrencias: Counter[str] = Counter()

    def aplicar(self, tool_name: str, resposta: RawResponse) -> RawResponse:
        """A resposta que o resto da pilha vai ver. Sem spec aplicável, a original.

        Devolver o mesmo objeto (e não uma cópia) quando nada se aplica é intencional: torna
        "nenhuma falha foi injetada" verificável por identidade nos testes e no debug.
        """
        self._ocorrencias[tool_name] += 1
        ocorrencia = self._ocorrencias[tool_name]

        spec = self._spec_aplicavel(tool_name, ocorrencia)
        if spec is None:
            return resposta
        return _injetar(spec, resposta)

    def reset(self) -> None:
        """Zera os contadores. Um injetor por bateria, um `reset` por run.

        Sem isso a segunda run herdaria o contador da primeira e receberia a falha em outra
        chamada — o mesmo cenário mediria coisas diferentes conforme a ordem da bateria.
        """
        self._ocorrencias.clear()

    def _spec_aplicavel(self, tool_name: str, ocorrencia: int) -> FaultSpec | None:
        """A primeira spec que casa. Ordem explícita em vez de acúmulo: duas falhas na mesma
        resposta não compõem (o que seria "timeout com corpo malformado"?)."""
        for spec in self.specs:
            if spec.tool not in (tool_name, CURINGA):
                continue
            if spec.quando is not None and spec.quando != ocorrencia:
                continue
            return spec
        return None


def _injetar(spec: FaultSpec, resposta: RawResponse) -> RawResponse:
    if spec.falha == "timeout":
        # Espelha exatamente o que `TractianClient.get` devolve num `httpx.TransportError`:
        # a falha injetada tem de ser indistinguível da real, senão o classificador acerta
        # por um caminho que não existe em produção.
        return RawResponse(
            status_code=None,
            body={"code": "TRANSPORT_ERROR", "message": "timeout injetado"},
            latencia_ms=spec.timeout_ms,
        )

    if spec.falha == "corpo_malformado":
        return RawResponse(
            status_code=resposta.status_code,
            body=CORPO_MALFORMADO,
            latencia_ms=resposta.latencia_ms,
        )

    return RawResponse(
        status_code=500,
        body={"code": "HTTP_500", "message": "erro interno injetado"},
        latencia_ms=resposta.latencia_ms,
    )
