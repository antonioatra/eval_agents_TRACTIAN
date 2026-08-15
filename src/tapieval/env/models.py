"""
Modelos das respostas da API industrial do parceiro (contrato `api-contract.openapi.yaml`).

DUAS REGRAS DE MODELAGEM, e elas explicam quase todo campo daqui:

1. **Nenhum campo é obrigatório.** A API degrada o payload de propósito: `partial` remove
   campos, `inconclusive` troca o corpo por um marcador, `unavailable` devolve `{}`
   (`api/app/main.py::_apply_mode`). Como isso acontece em ~40% das respostas
   (CENARIOS §8.1), campo obrigatório aqui transformaria o fenômeno que o framework
   existe para medir em erro de validação.
2. **Campo desconhecido é preservado** (`extra="allow"`). Os marcadores de degradação
   — `inconclusive: true`, `conflict: true` — não pertencem ao schema de nenhum recurso
   e são exatamente o que a T7 precisa ler. Descartá-los em silêncio seria pior que
   falhar.

Os nomes dos campos são os do JSON (inglês); só os nomes de classe são traduzidos.
Onde o Swagger e a implementação divergem, **manda a implementação** — em especial
`Ativo`, que o Swagger descreve aninhado (`hierarchy`, `config.bearing_specs`) e a API
devolve plano.

Validar é opcional: `TractianClient` devolve o corpo cru. Estes modelos são para quem
precisa navegar a resposta com tipo — classificador de status (T7), hidratação e scorers.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

# Espelha `api/app/prob.py::Mode`. Fechado de propósito: modo novo na API tem que quebrar
# aqui, e não passar despercebido até o fim da bateria.
ModoResposta = Literal["complete", "partial", "inconclusive", "conflict", "unavailable"]


class ModeloDaApi(BaseModel):
    """Base dos modelos da API. Ver as duas regras no topo do módulo.

    `protected_namespaces=()` libera `Analise.model_version`, que colide com o prefixo
    `model_` reservado pelo pydantic — renomear o campo quebraria o contrato do JSON.
    """

    model_config = ConfigDict(extra="allow", protected_namespaces=())


T = TypeVar("T", bound=ModeloDaApi)


class Envelope(ModeloDaApi, Generic[T]):
    """Envelope `{mode, notes, data}` de toda leitura, exceto `GET /users/me`.

    `mode` é explícito — não se infere da forma do corpo (CENARIOS §5.4). Um payload
    completo com `mode="partial"` acontece (categorias sem regra em `_PARTIAL_DROP`
    degradam só na nota), e nesse caso quem tem razão é o `mode`.
    """

    mode: ModoResposta
    notes: str | None = None
    data: T | None = None


# ---------------------------------------------------------------------------
# Contexto
# ---------------------------------------------------------------------------


class Empresa(ModeloDaApi):
    id: str | None = None
    name: str | None = None
    segment: str | None = None
    timezone: str | None = None


class Usuario(ModeloDaApi):
    """`GET /users/me`. Vem sem envelope: a API devolve a linha do usuário direto.

    `permissions` governa apenas AÇÕES. As leituras não são isoladas por empresa
    (CENARIOS §5.1), então comparar `company_id` é responsabilidade do agente.
    """

    id: str | None = None
    name: str | None = None
    role: str | None = None
    permissions: list[str] = Field(default_factory=list)
    company_id: str | None = None


# ---------------------------------------------------------------------------
# Ativos
# ---------------------------------------------------------------------------


class Ponto(ModeloDaApi):
    id: str | None = None
    asset_id: str | None = None
    location: str | None = None
    sensor_status: str | None = None


class Ativo(ModeloDaApi):
    """`GET /assets/{assetId}`.

    Plano, ao contrário do Swagger: os campos de hierarquia (`plant`, `line`,
    `parent_asset_id`) e de configuração técnica (`machine_type`, `bearing_pn`, as
    frequências de rolamento) vêm no primeiro nível, porque a API serializa a linha do
    parquet como está e só acrescenta `points`.
    """

    id: str | None = None
    name: str | None = None
    company_id: str | None = None
    criticality: str | None = None

    plant: str | None = None
    line: str | None = None
    parent_asset_id: str | None = None

    machine_type: str | None = None
    rotation_rpm: float | None = None
    bearing_pn: str | None = None
    bpfo_hz: float | None = None
    bpfi_hz: float | None = None
    bsf_hz: float | None = None
    ftf_hz: float | None = None
    line_frequency_hz: float | None = None
    sensor_status: str | None = None

    points: list[Ponto] = Field(default_factory=list)


class ListaDeAtivos(ModeloDaApi):
    """`GET /companies/{companyId}/assets` — a lista vem embrulhada em `data.assets`."""

    assets: list[Ativo] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Análises
# ---------------------------------------------------------------------------


class Evidencia(ModeloDaApi):
    metric: str | None = None
    value: float | None = None
    reference: float | None = None      # null em detecção sintomática
    note: str | None = None


class Analise(ModeloDaApi):
    """`GET /analyses/{analysisId}`.

    `evidence` e `limitations` são os dois campos que o modo `partial` remove — e são
    justamente a base de qualquer resposta fundamentada. Ausência aqui é sinal, não ruído.
    """

    id: str | None = None
    asset_id: str | None = None
    point_id: str | None = None
    type: str | None = None
    detection_mode: str | None = None
    severity: str | None = None
    confidence: float | None = None
    baseline_state_at_detection: str | None = None
    evidence: list[Evidencia] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    model_version: str | None = None
    created_at: datetime | None = None
    status: str | None = None


class ListaDeAnalises(ModeloDaApi):
    """`GET /assets/{assetId}/analyses` — lista embrulhada em `data.analyses`."""

    analyses: list[Analise] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Dados técnicos
# ---------------------------------------------------------------------------


class FeatureDeBaseline(ModeloDaApi):
    feature: str | None = None
    reference: float | None = None
    tolerance: float | None = None


class Baseline(ModeloDaApi):
    """`GET /assets/{assetId}/baseline` — o estado normal aprendido do próprio ativo.

    Sem baseline, a API não devolve `data: null`: devolve `{"baseline": null}` com
    `mode="inconclusive"`. O sentinela cai em `extra` e o modelo fica todo vazio.
    """

    id: str | None = None
    asset_id: str | None = None
    point_id: str | None = None
    state: str | None = None
    detection_mode: str | None = None
    learnable: bool | None = None
    established_at: datetime | None = None
    invalidated_at: datetime | None = None
    invalidation_reason: str | None = None
    features: list[FeatureDeBaseline] = Field(default_factory=list)


class AmostraRms(ModeloDaApi):
    ts: datetime | None = None
    value: float | None = None


class SerieRms(ModeloDaApi):
    """`GET /assets/{assetId}/rms`.

    `alarm_threshold` é derivado do baseline (`reference + tolerance`) pela própria API,
    não de norma ISO — é o limiar que o agente deve citar (CEN-13).
    """

    asset_id: str | None = None
    point_id: str | None = None
    unit: str | None = None
    baseline_reference: float | None = None
    baseline_state: str | None = None
    alarm_threshold: float | None = None
    samples: list[AmostraRms] = Field(default_factory=list)


class PicoEspectral(ModeloDaApi):
    freq_hz: float | None = None
    amplitude_mm_s: float | None = None
    note: str | None = None             # "1x", "BPFO", "2x line"


class Espectro(ModeloDaApi):
    """`GET /assets/{assetId}/spectrum`.

    Sem `frequency_resolution_hz`: o Swagger declara, a API não devolve. `bands_missing`
    é o campo que diz quais bandas faltaram na coleta.
    """

    asset_id: str | None = None
    point_id: str | None = None
    collected_at: datetime | None = None
    peaks: list[PicoEspectral] = Field(default_factory=list)
    bands_missing: list[str] = Field(default_factory=list)


class QualidadeDeDados(ModeloDaApi):
    """`GET /assets/{assetId}/data-quality` — insumo da regra de qualidade, não da decisão."""

    asset_id: str | None = None
    point_id: str | None = None
    completeness: float | None = None
    freshness_minutes: int | None = None
    snr_db: float | None = None
    staleness_flag: bool | None = None


# ---------------------------------------------------------------------------
# Modelo de detecção
# ---------------------------------------------------------------------------


class CoberturaDoModelo(ModeloDaApi):
    machine_type: str | None = None
    supported: bool | None = None
    can_learn_baseline: bool | None = None    # false -> só detecção sintomática
    note: str | None = None


class RequisitosDoModelo(ModeloDaApi):
    min_completeness: float | None = None
    min_snr_db: float | None = None
    min_rotation_rpm: float | None = None


class ModeloDeDeteccao(ModeloDaApi):
    """`GET /models/{modelId}` — o modelo de vibração da TRACTIAN, não o LLM avaliado."""

    id: str | None = None
    version: str | None = None
    coverage: list[CoberturaDoModelo] = Field(default_factory=list)
    requirements: RequisitosDoModelo | None = None
    processing_state: str | None = None
    last_run_at: datetime | None = None


# ---------------------------------------------------------------------------
# Conhecimento
# ---------------------------------------------------------------------------


class DocumentoDeConhecimento(ModeloDaApi):
    id: str | None = None
    type: str | None = None
    title: str | None = None
    body: str | None = None
    tags: list[str] = Field(default_factory=list)


class ResultadoDeBusca(ModeloDaApi):
    """`GET /knowledge/search` — lista embrulhada em `data.results`.

    Busca vazia devolve `results: []` com `mode="complete"`: a base tem 4 documentos e
    nenhum sobre reprocessamento (CENARIOS §5.3). Vazio não é falha do ambiente.
    """

    results: list[DocumentoDeConhecimento] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Ações e erros
# ---------------------------------------------------------------------------


class PedidoDeAcao(ModeloDaApi):
    """Corpo de toda ação de impacto (POST/PATCH).

    `justification` com menos de 20 caracteres é 400 na API — validar aqui evita gastar
    uma tentativa de ação para descobrir isso. `params` e `changes` são aceitos pela API
    mas não alteram estado nenhum: ela só confere a justificativa.
    """

    justification: str = Field(min_length=20)
    params: dict[str, Any] | None = None
    changes: dict[str, Any] | None = None


class ResultadoDeAcao(ModeloDaApi):
    """Resposta de ação aceita. Sem envelope e sem ciclo de status: aceitou, executou."""

    accepted: bool | None = None
    action_id: str | None = None
    message: str | None = None


class ErroDaApi(ModeloDaApi):
    """Corpo de erro `{code, message}` para 400/401/403/404 (`main.py::_http_handler`)."""

    code: str | None = None
    message: str | None = None
