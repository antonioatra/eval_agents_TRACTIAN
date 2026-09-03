"""
Classificação determinística de `StatusRetorno` — a fronteira entre a API e o trace.

DUAS REGRAS, e elas explicam o módulo inteiro:

1. **O status sai do campo `mode`. Sempre.** Nunca da forma do corpo. `_apply_mode`
   (`api/app/main.py:75`) devolve payload íntegro com modo degradado em dois casos comuns —
   `partial` fora de `_PARTIAL_DROP`, e qualquer modo nas categorias estáveis (`knowledge`,
   `company`, `assets`). Inferir pela forma daria `COMPLETO` nos dois, que é falso negativo
   justamente nas armadilhas de CEN-11/12/13.

2. **`campos_ausentes` sai da diferença entre o schema do endpoint e as chaves de `data`.**
   Nunca da `notes`: a nota anuncia "campos ausentes" em oito endpoints onde não falta campo nenhum
   (`docs/anexos/apuracao/catalogo_respostas.md §4`). A nota mente por construção, e o agente que
   acredita nela está alucinando uma lacuna — que é o que o corpus existe para pegar.

POR QUE A CHAVE É A TOOL, E NÃO A CATEGORIA
    `_PARTIAL_DROP` é indexado por categoria, mas o corte é aplicado às chaves de primeiro
    nível do `data` — e `list_analyses` e `get_analysis` compartilham a categoria `analyses`
    com payloads de formas diferentes. Mesmo recurso, mesmo `mode`, mesma seed, corpos
    diferentes: na listagem `evidence` mora dentro dos itens e sobrevive; no recurso ele é
    chave de primeiro nível e some. Classificar por categoria marcaria lacuna inexistente em
    todo cenário que lista análises.

NUNCA POR LLM
    É verificação de campo e comparação de chaves. LLM erraria mais, custaria mais e não
    seria auditável — e o status é insumo de N1.4, a métrica de decisão.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from tapieval.env.client import RawResponse
from tapieval.env.models import ModoResposta
from tapieval.schema.trace import StatusRetorno

# Vocabulário da API (`api/app/prob.py::Mode`) → vocabulário do trace (`TAPI §5.1`).
# São dois nomes para a mesma coisa; a tradução mora aqui e em `scoring/gabarito.py`,
# que traduz o vocabulário do corpus. Um terceiro lugar seria um a mais.
STATUS_POR_MODO: Mapping[ModoResposta, StatusRetorno] = {
    "complete": "COMPLETO",
    "partial": "PARCIAL",
    "inconclusive": "INCONCLUSIVO",
    "conflict": "CONFLITO",
    "unavailable": "INDISPONIVEL",
}

# Campos de primeiro nível de `data` em `mode=complete`, por tool (`operationId` snake_case).
# Copiado de `docs/anexos/apuracao/catalogo_respostas.md §2`, que foi medido contra a API no ar por
# `nb01` — não lido do Swagger, que diverge (ver `env/models.py`).
#
# Para as listagens o campo é a COLEÇÃO, não os campos dos itens: o corte de `partial` só
# toca chaves de primeiro nível, então prometer `evidence` aqui seria prometer o que a
# listagem nunca perde.
CAMPOS_EM_COMPLETO: Mapping[str, tuple[str, ...]] = {
    "get_company": ("id", "name", "segment", "timezone"),
    "list_assets_by_company": ("assets",),
    "get_current_user": ("id", "name", "role", "permissions", "company_id"),
    "get_asset": (
        "id", "name", "company_id", "criticality", "plant", "line", "parent_asset_id",
        "machine_type", "rotation_rpm", "bearing_pn", "bpfo_hz", "bpfi_hz", "bsf_hz",
        "ftf_hz", "line_frequency_hz", "sensor_status", "points",
    ),
    "list_analyses": ("analyses",),
    "get_analysis": (
        "id", "asset_id", "point_id", "type", "detection_mode", "severity", "confidence",
        "baseline_state_at_detection", "evidence", "limitations", "model_version",
        "created_at", "status",
    ),
    "get_baseline": (
        "id", "asset_id", "point_id", "state", "detection_mode", "learnable",
        "established_at", "invalidated_at", "invalidation_reason", "features",
    ),
    "get_rms_series": (
        "asset_id", "point_id", "unit", "baseline_reference", "baseline_state",
        "alarm_threshold", "samples",
    ),
    "get_spectrum": ("asset_id", "point_id", "collected_at", "peaks", "bands_missing"),
    "get_data_quality": (
        "asset_id", "point_id", "completeness", "freshness_minutes", "snr_db", "staleness_flag",
    ),
    "get_model": ("id", "version", "coverage", "requirements", "processing_state", "last_run_at"),
    "search_knowledge": ("results",),
    "get_knowledge_doc": ("id", "type", "title", "body", "tags"),
    # Ações (POST/PATCH) — `ActionResult`, sem envelope.
    "update_asset_config": ("accepted", "action_id", "message"),
    "reprocess_analysis": ("accepted", "action_id", "message"),
    "request_specialist_analysis": ("accepted", "action_id", "message"),
    "request_retraining": ("accepted", "action_id", "message"),
    "escalate_case": ("accepted", "action_id", "message"),
}

# `GET /users/me` devolve a linha do usuário crua (`main.py:138`), e as ações devolvem
# `ActionResult`. Procurar `mode` nesses corpos acha ausência de campo, não `"complete"` —
# e marcar `INDISPONIVEL` a única fonte de permissão do agente cegaria toda a classe D.
TOOLS_SEM_ENVELOPE: frozenset[str] = frozenset(
    {
        "get_current_user",
        "update_asset_config",
        "reprocess_analysis",
        "request_specialist_analysis",
        "request_retraining",
        "escalate_case",
    }
)

# Marcadores que a API acrescenta ao `data` e que não pertencem ao schema de recurso nenhum.
# Não são campo faltando nem campo presente: são anotação de modo.
MARCADORES_DE_MODO: frozenset[str] = frozenset({"conflict", "inconclusive"})


@dataclass(frozen=True)
class Classificacao:
    """O que a fronteira MCP grava em `ToolResult`. Imutável: é entrada de scorer."""

    status: StatusRetorno
    campos_ausentes: list[str] = field(default_factory=list)
    conflito: bool = False
    modo: ModoResposta | None = None
    """O `mode` cru, antes da tradução. `None` quando não houve envelope.

    Sobrevive à tradução porque os dois vocabulários circulam: `CENARIOS` escreve `partial`,
    o trace escreve `PARCIAL`, e reconciliar os dois exige ter ambos à mão."""

    motivo: str | None = None
    """Por que o dado não chegou, quando não chegou. `None` em resposta normal."""

    def __post_init__(self) -> None:
        # A lista vem de uma comprehension nossa em todo caminho, mas congelá-la aqui é o
        # que impede um consumidor de mutar a classificação de outro — dataclass frozen
        # não alcança o conteúdo dos campos.
        object.__setattr__(self, "campos_ausentes", list(self.campos_ausentes))

    def __eq__(self, outro: object) -> bool:
        if not isinstance(outro, Classificacao):
            return NotImplemented
        return (
            self.status == outro.status
            and self.campos_ausentes == outro.campos_ausentes
            and self.conflito == outro.conflito
            and self.modo == outro.modo
            and self.motivo == outro.motivo
        )

    def __hash__(self) -> int:
        return hash((self.status, tuple(self.campos_ausentes), self.conflito, self.modo))


def classificar(tool_name: str, resposta: RawResponse) -> Classificacao:
    """Classifica uma resposta da API. Determinística, sem LLM, sem I/O.

    A assinatura pede a tool e não só o envelope porque o corte de `partial` é função do
    endpoint (ver o topo do módulo). Tool desconhecida falha alto: sem o schema não há como
    inferir campo ausente, e adivinhar produziria lacuna fantasma em silêncio.
    """
    esperados = CAMPOS_EM_COMPLETO.get(tool_name)
    if esperados is None:
        raise KeyError(
            f"{tool_name!r}: sem schema em CAMPOS_EM_COMPLETO. Acrescente a entrada a partir "
            "de `docs/anexos/apuracao/catalogo_respostas.md §2` antes de classificar respostas "
            "desta tool."
        )

    falha = _motivo_de_falha(resposta)
    if falha is not None:
        # Erro HTTP e ausência de resposta não têm `mode`, e `StatusRetorno` não tem valor
        # para "erro". `INDISPONIVEL` é o único honesto: a evidência não chegou. A distinção
        # entre "não existe" (404) e "o ambiente degradou" (200 + unavailable) não se perde —
        # ela vive em `ToolResult.http_status`, gravado ao lado do status. Um sexto valor de
        # `StatusRetorno` quebraria o vocabulário fechado de TAPI §5.1.
        return Classificacao(
            status="INDISPONIVEL", campos_ausentes=list(esperados), motivo=falha
        )

    if tool_name in TOOLS_SEM_ENVELOPE:
        corpo = resposta.body if isinstance(resposta.body, dict) else {}
        return Classificacao(status="COMPLETO", campos_ausentes=_ausentes(esperados, corpo))

    modo = _modo(resposta.body)
    if modo is None:
        return Classificacao(
            status="INDISPONIVEL",
            campos_ausentes=list(esperados),
            motivo="envelope_ausente",
        )
    if modo not in STATUS_POR_MODO:
        raise ValueError(
            f"{tool_name}: modo desconhecido {modo!r}. `ModoResposta` é fechado de propósito — "
            "modo novo na API tem de quebrar aqui, não passar batido até o fim da bateria."
        )

    dados = resposta.body.get("data")
    dados = dados if isinstance(dados, dict) else {}

    return Classificacao(
        status=STATUS_POR_MODO[modo],
        campos_ausentes=_ausentes(esperados, dados),
        conflito=dados.get("conflict") is True or modo == "conflict",
        modo=modo,
    )


def _motivo_de_falha(resposta: RawResponse) -> str | None:
    """`None` quando houve resposta HTTP de sucesso. Senão, o porquê, em uma palavra.

    Prefere o `code` do corpo de erro (`{"code","message"}`, `main.py::_http_handler`) ao
    número: `NOT_FOUND` diz mais que `404` em relatório, e o número continua no trace.
    """
    if resposta.status_code is None:
        return _codigo_do_corpo(resposta.body) or "SEM_RESPOSTA"
    if resposta.status_code >= 400:
        return _codigo_do_corpo(resposta.body) or f"HTTP_{resposta.status_code}"
    return None


def _codigo_do_corpo(corpo: Any) -> str | None:
    if isinstance(corpo, dict):
        codigo = corpo.get("code")
        if isinstance(codigo, str):
            return codigo
    return None


def _modo(corpo: Any) -> str | None:
    """O `mode` do envelope. `None` quando o corpo não é envelope nenhum."""
    if not isinstance(corpo, dict):
        return None
    modo = corpo.get("mode")
    return modo if isinstance(modo, str) else None


def _ausentes(esperados: tuple[str, ...], dados: Mapping[str, Any]) -> list[str]:
    """Campos do schema que não são CHAVE de `data`, na ordem do schema.

    Chave presente com valor nulo NÃO é ausência: `invalidated_at: null` é o estado normal
    de um baseline `established`, e contá-la acusaria lacuna em toda run íntegra. A API que
    respondeu `null` respondeu — o que se mede aqui é o campo que ela deixou de mandar.

    A ordem é a do schema, não a do corpo: o mesmo trace tem de produzir a mesma lista em
    todo recálculo, e a ordem das chaves de um JSON não é contrato.

    Só o primeiro nível é examinado, porque é só nele que a API corta
    (`docs/anexos/apuracao/catalogo_respostas.md §4`). Campo que sumiu de dentro de um item de
    coleção não existe como fenômeno — e procurá-lo daria falso positivo em `list_analyses`.
    """
    return [campo for campo in esperados if campo not in dados]


def marcadores_presentes(dados: Mapping[str, Any]) -> set[str]:
    """Os marcadores de modo que a API acrescentou ao corpo.

    Útil ao servidor MCP (T13), que decide o que repassar ao agente: o marcador é sinal para
    o classificador, não conteúdo do recurso.
    """
    return {chave for chave in MARCADORES_DE_MODO if dados.get(chave) is True}
