"""
Schema canônico de trace — framework de avaliação de agentes industriais.
Case Inteli x TRACTIAN, trilha 2.

PRINCÍPIO CENTRAL
    O trace é IMUTÁVEL e descreve o que aconteceu.
    Os scores são DERIVADOS, versionados e recomputáveis a partir do trace.
    Nunca escreva resultado de avaliação dentro do trace: mudar um scorer
    passaria a exigir re-executar o agente.

LAYOUT EM DISCO
    runs/<experiment_id>/
        manifest.json                    # config do experimento inteiro
        traces/<run_id>.jsonl            # 1 evento por linha, append-only
        blobs/<sha256>.txt               # prompts e payloads grandes
        scores/<scorer_version>/<run_id>.json
        cassettes/<cassette_id>.json     # respostas gravadas da API (replay)
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator

SCHEMA_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Vocabulário
# ---------------------------------------------------------------------------

# TAPI §5.1 — classificado deterministicamente no wrapper da tool, nunca pelo LLM
StatusRetorno = Literal[
    "COMPLETO", "PARCIAL", "INCONCLUSIVO", "CONFLITO", "INDISPONIVEL"
]

# TAPI §4 — os três modos de atendimento
Modo = Literal["contextualizar", "investigar", "executar"]

# Cinco valores, não quatro. `recusar` foi acrescentado em 15/08 (A8): o vocabulário de
# `scenarios/_regras_decisao.yaml` sempre teve cinco, duas regras o devolvem
# (`acao_alto_impacto_sem_base_tecnica` e `ativo_fora_do_escopo_da_empresa`, esta última
# é D5/S0), e a taxonomia de METRICAS §6 tem D4 — "recusa indevida de tarefa legítima" —
# que só existe se recusar for decisão legítima. Colapsar em `escalar` apagaria a
# diferença entre encerrar recusando e passar para um humano, que é exatamente o que
# D5 exige: nenhum campo técnico do ativo alheio na resposta.
Decisao = Literal["orientar", "agir", "escalar", "perguntar", "recusar"]

Split = Literal["dev", "test"]

# live: chama a API de verdade e grava | replay: reproduz cassete gravado
EnvMode = Literal["live", "replay"]


# ---------------------------------------------------------------------------
# Manifesto — o cabeçalho imutável do experimento
# ---------------------------------------------------------------------------


class ModelConfig(BaseModel):
    """Tudo que o TAPI §9 exige registrar sobre o modelo."""

    model_id: str                      # "qwen3-14b-instruct-4bit"
    served_by: str                     # "lmstudio" | "ollama" | "mlx"
    quantization: str | None = None
    temperature: float
    top_p: float | None = None
    max_tokens: int
    seed: int | None = None            # None = servidor não suporta seed
    structured_output: Literal["grammar", "json_schema", "prompt", "none"]
    context_window: int


class VariantConfig(BaseModel):
    """Um ponto no espaço de variação do agente. É a 'coluna' do experimento."""

    variant_id: str                    # "base" | "sem_hidratacao" | "budget_baixo"
    hidratacao: bool = True            # busca determinística de contexto+ativo
    tools_visiveis: Literal["todas", "por_modo"] = "todas"
    exige_citacao: bool = True         # prompt pede fundamentação explícita
    max_iterations: int = 8
    max_tool_calls: int = 12
    prompt_sha: str                    # hash do system prompt (blob)

    tools_ocultas: frozenset[str] = frozenset()
    """Tools que somem de `list_tools` para esta variante (`RunContext.tools_ocultas`).

    Sob MCP a degradação é real: a tool não é escondida do prompt, ela **não existe** para
    aquele cliente. Fica aqui e não só no `RunContext` porque é a variante quem define a
    coluna do experimento — o contexto da run é só quem a executa."""

    mutante: bool = False              # degradação deliberada (validação do framework)
    mutacao_descricao: str | None = None
    classe: Literal["P", "C"] | None = None
    """A classe de falha que o mutante degrada: **P**rocesso ou **C**onteúdo.

    Exigida em todo mutante porque a INS.9 lê a taxa de detecção **por classe**
    (`METRICAS §7.1`): sem ela os quatro mutantes viram um número só, e a curva de detecção
    da classe C — que MUT4 existe para sustentar — não tem como ser separada da de P."""

    @model_validator(mode="after")
    def _mutante_declara_o_que_degradou(self) -> VariantConfig:
        """Mutante sem descrição e classe é degradação anônima, e mede-se sozinha errado.

        A INS.9 pergunta "que fração dos mutantes o instrumento distinguiu do original, por
        classe". Um mutante sem `classe` não entra em nenhuma das duas curvas e some da
        conta; um sem `mutacao_descricao` entra, mas ninguém sabe dizer do que ele é o
        ponto de apoio. Nos dois casos o denominador mente para o lado que favorece o
        instrumento — o mesmo formato do X18 e do A10.

        O inverso também falha: variante que não é mutante não pode alegar degradação, senão
        a base entraria na conta de detecção como se fosse defeito.
        """
        if self.mutante:
            if not self.mutacao_descricao:
                raise ValueError(
                    f"variante {self.variant_id!r} é mutante e não diz o que degradou: "
                    "`mutacao_descricao` é obrigatória"
                )
            if self.classe is None:
                raise ValueError(
                    f"variante {self.variant_id!r} é mutante sem `classe`: a INS.9 lê "
                    "detecção por classe (P/C) e um mutante sem classe some da conta"
                )
        else:
            if self.mutacao_descricao or self.classe is not None:
                raise ValueError(
                    f"variante {self.variant_id!r} não é mutante mas declara degradação "
                    "(`mutacao_descricao`/`classe`); só mutante entra na conta da INS.9"
                )
        return self


class ExperimentManifest(BaseModel):
    schema_version: str = SCHEMA_VERSION
    experiment_id: str
    criado_em: datetime
    descricao: str

    corpus_version: str                # hash ou tag do corpus de cenários
    scenario_ids: list[str]
    split_por_cenario: dict[str, Split]

    models: dict[str, ModelConfig]     # model_key -> config
    variants: dict[str, VariantConfig]
    seeds: list[int]
    env_mode: EnvMode
    cassette_id: str | None = None     # obrigatório se env_mode == "replay"

    git_sha: str | None = None
    notas: str | None = None


# ---------------------------------------------------------------------------
# Eventos do trace
# ---------------------------------------------------------------------------


class BaseEvent(BaseModel):
    run_id: str
    seq: int                           # ordem absoluta dentro da run
    ts: datetime
    iteration: int                     # volta do loop ReAct (0 = pré-loop)


class RunStart(BaseEvent):
    type: Literal["run_start"] = "run_start"
    experiment_id: str
    scenario_id: str
    split: Split
    variant_id: str
    model_key: str
    seed: int
    env_mode: EnvMode
    cassette_id: str | None = None
    solicitacao: str                   # o pedido em linguagem natural
    user_id: str
    asset_id: str | None = None


class Hydration(BaseEvent):
    """Busca determinística de contexto antes do loop. Sem LLM."""

    type: Literal["hydration"] = "hydration"
    endpoints: list[str]
    ok: bool
    latencia_ms: int
    resumo: dict[str, Any]             # o que foi carregado, achatado


class LLMCall(BaseEvent):
    """Uma chamada ao modelo. Prompt e resposta vão para blob."""

    type: Literal["llm_call"] = "llm_call"
    model_key: str
    prompt_sha: str                    # blobs/<sha>.txt
    prompt_tokens: int
    completion_tokens: int
    completion_sha: str
    latencia_ms: int
    finish_reason: str                 # "stop" | "length" | "tool_calls" | ...
    parse_ok: bool                     # saída estruturada validou?
    parse_erro: str | None = None      # métrica própria — não descartar
    tentativa: int = 1                 # nº de retries de parsing


class ToolCall(BaseEvent):
    """O que o agente PEDIU. Separado do que recebeu, de propósito."""

    type: Literal["tool_call"] = "tool_call"
    tool_call_id: str                  # "tc_03" — é o que a resposta cita
    tool_name: str
    args: dict[str, Any]               # já validado pelo schema
    args_raw: dict[str, Any] | None = None   # antes da coerção, se houve
    args_validos: bool
    args_erro: str | None = None


class ToolResult(BaseEvent):
    """O que a API DEVOLVEU, com a degradação classificada deterministicamente."""

    type: Literal["tool_result"] = "tool_result"
    tool_call_id: str
    status: StatusRetorno
    http_status: int | None = None
    latencia_ms: int
    tentativas: int = 1                # retries de INDISPONIVEL
    cache_hit: bool = False            # chamada idêntica já feita nesta run
    campos_ausentes: list[str] = Field(default_factory=list)
    fontes_divergentes: list[dict[str, Any]] = Field(default_factory=list)
    body: dict[str, Any] | None = None       # payload pequeno inline
    body_sha: str | None = None              # payload grande -> blob
    from_cassette: bool = False              # veio do replay


class DecisionEvent(BaseEvent):
    """Roteamento e decisão. Precisa ser estruturado para virar métrica."""

    type: Literal["decision"] = "decision"
    modo: Modo | None = None
    decisao: Decisao | None = None
    prioridade_escalonamento: Literal["alta", "media", "baixa"] | None = None
    # introspecção: útil para DEBUG, proibida como evidência em scorer
    # (racionalização post-hoc não é fiel ao processo interno)
    racional_declarado: str | None = None


class GateEvent(BaseEvent):
    """Ação de alto impacto. Sem humano no loop — approver injetável."""

    type: Literal["gate"] = "gate"
    acao: str
    args: dict[str, Any]
    justificativa: str
    citacoes: list[str]                # tool_call_ids alegados
    citacoes_validas: bool             # determinístico: os ids existem?
    citacoes_invalidas: list[str] = Field(default_factory=list)
    permissao_usuario_ok: bool
    """"O gate CONFIRMOU a permissão exigida?", não "o usuário a tem". A pergunta tem três
    respostas (tem / não tem / não verificada ainda) e o campo é `bool` — as duas últimas
    colapsam em `False`, e a distinção sobrevive em `motivo_negacao` (`mcp/gate.py`). O
    colapso é seguro porque negar impede a execução: sem `tool_call` de escrita no trace,
    `n1._acao_indevida` não itera sobre nada e leitura legítima não vira D1/S0."""

    # Quatro valores, não três. `human` foi acrescentado em 16/08 (T15): `ARQUITETURA §3.7`
    # sempre listou as quatro políticas — "AutoApprove · AutoDeny · PolicyApprover (bateria) ·
    # HumanApprover (demo)" — e sem o valor o trace da demo teria de registrar `policy` para
    # uma decisão humana, apagando a única diferença que o campo existe para guardar. É
    # acréscimo, não alteração: nenhum trace existente carrega `human` e nenhum scorer lê
    # `approver` (N1 lê `acao`, `veredito`, `seq`, `citacoes`; `derivar_estado` lê
    # `permissao_usuario_ok`), então nada recomputa diferente.
    approver: Literal["auto_approve", "auto_deny", "policy", "human"]
    veredito: Literal["aprovado", "negado"]
    motivo_negacao: str | None = None
    idempotency_key: str               # sha256(acao + args) — nunca reseta


class BudgetEvent(BaseEvent):
    type: Literal["budget"] = "budget"
    limite: Literal["max_iterations", "max_tool_calls", "max_same_endpoint"]
    valor: int


class FinalAnswer(BaseEvent):
    type: Literal["final_answer"] = "final_answer"
    texto: str
    citacoes: list[str]                # tool_call_ids que o agente alega usar
    citacoes_validas: bool
    perguntou_de_volta: bool = False


class RunError(BaseEvent):
    type: Literal["error"] = "error"
    onde: str
    classe: str
    mensagem: str
    fatal: bool


class RunEnd(BaseEvent):
    type: Literal["run_end"] = "run_end"
    status: Literal["ok", "budget_exceeded", "error", "timeout"]
    duracao_ms: int
    total_tool_calls: int
    total_llm_calls: int
    total_prompt_tokens: int
    total_completion_tokens: int


TraceEvent = Annotated[
    RunStart
    | Hydration
    | LLMCall
    | ToolCall
    | ToolResult
    | DecisionEvent
    | GateEvent
    | BudgetEvent
    | FinalAnswer
    | RunError
    | RunEnd,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Estado observado — a ponte para o gabarito relativo
# ---------------------------------------------------------------------------


class EstadoObservado(BaseModel):
    """
    Derivado do trace por função pura (`tapieval.scoring.estado.derivar_estado`).
    É o que o gabarito relativo consome, permitindo avaliar sem congelar o ambiente:

        decisao_esperada(estado) -> Decisao

    A derivação mora em `scoring/` e não aqui: este módulo é schema, e schema não
    pode depender de scorer — é o que mantém o trace recomputável por outro scorer.
    """

    run_id: str
    tools_chamadas: list[str]
    status_por_tool: dict[str, StatusRetorno]
    houve_indisponivel_apos_retries: bool
    houve_conflito_nao_resolvido: bool
    criticidade_ativo: str | None
    qualidade_sinal: str | None
    evidencias_completas: bool
    campos_ausentes: list[str]
    pediu_acao_alto_impacto: bool
    permissao_usuario_ok: bool | None
    """Visão colapsada: "alguma permissão exigida faltou?". `None` = o trace não informa.

    Continua existindo porque as regras que são só sobre *ter ou não ter* permissão leem daqui.
    Quem precisa saber **qual** permissão faltou lê `permissoes_faltantes` (X16)."""

    permissoes_faltantes: list[str] = Field(default_factory=list)
    """Quais permissões o trace PROVA que faltaram — `action_high`, `action_low`, `escalate`.

    Existe porque a API real exige permissões **diferentes** por ação (X21), e colapsar tudo
    num `permissao_usuario_ok=False` apagava essa distinção dentro do scorer: `cen_14` diz que
    faltar `action_low` mantém `agir`, `cen_15` diz que faltar `action_high` vira `escalar`, e
    com um booleano só nenhum gabarito podia honrar os dois (X16).

    Lista e não valor único: uma run pode pedir mais de uma ação e esbarrar em mais de uma
    permissão. Vazia quando nada foi provado faltando — o que inclui o caso comum de
    `permissao_usuario_ok is None` (run só de leitura, sem gate e sem 403)."""


# ---------------------------------------------------------------------------
# Scores — derivados, versionados, recomputáveis
# ---------------------------------------------------------------------------


class ScorerVersion(BaseModel):
    """Congelado antes da bateria final. O hash entra em todo ScoreRecord."""

    scorer_version: str                # "v2"
    sha256: str                        # hash de prompt + rubrica + few-shots
    judge_model: ModelConfig | None = None    # None nos scorers sem LLM
    rubrica_sha: str | None = None
    fewshot_ids: list[str] = Field(default_factory=list)
    fewshot_origem: Literal["escritos_a_mao", "dev_set", "misto"] | None = None
    congelado_em: datetime


class N1Deterministico(BaseModel):
    """Camada 1: sem LLM. Máxima confiabilidade."""

    tools_esperadas_chamadas: list[str]
    tools_faltantes: list[str]
    tools_extras: list[str]
    tool_f1: float

    # N1.1 líquida (X24, 19/08) — a mesma conta sobre as chamadas do LAÇO, sem a hidratação.
    tool_f1_liquido: float
    tools_creditadas_pela_hidratacao: list[str] = Field(default_factory=list)
    """Tools esperadas que SÓ a hidratação cobriu (`iteration == 0`).

    A hidratação atravessa a fronteira MCP — não há caminho honesto que não atravesse —, então
    `get_asset` e `get_current_user` chegam ao trace como `tool_call` normais e a N1.1 bruta os
    credita ao agente. A variante `hidratacao=True` ganharia duas tools esperadas de graça e a
    comparação com `sem_hidratacao` nasceria confundida por construção.

    As duas convivem de propósito: a bruta é o que o trace mostra e é o que a taxonomia
    consome (P1 não muda); a líquida é o que o agente escolheu chamar. **A diferença entre as
    duas não é ruído a descontar — é o resultado sobre o efeito da hidratação**, e por isso
    esta lista carrega os nomes em vez de só um delta numérico."""

    args_corretos: int
    args_avaliados: int
    args_acc: float
    decisao_prevista: Decisao | None
    decisao_esperada: Decisao | None       # via gabarito relativo
    decisao_correta: bool
    acao_indevida: bool                    # métrica de segurança
    gate_respeitado: bool
    citacoes_validas: bool


class N2Programatico(BaseModel):
    """Camada 2: sem LLM. Trajetória e eficiência."""

    n_iteracoes: int
    n_tool_calls: int
    n_redundantes: int                     # cache hits — sintoma de mau plano
    ordem_kendall_tau: float | None        # vs. trajetória de referência
    cobertura_evidencial: float            # % do checklist do cenário coberto
    estourou_budget: bool
    parse_failures: int

    # N2.1 — aderência causal. Acrescentada em 16/08 (T11): `METRICAS §N2.1` a marca com ★
    # ("a mais importante do conjunto") e os YAMLs já traziam `gabarito.precedencias`, mas não
    # havia campo aqui — o que deixava P4 em `severidade.FALHAS_NAO_CLASSIFICAVEIS`.
    # `None` = nenhuma precedência do cenário é verificável no trace, nunca "nada respeitado".
    aderencia_causal: float | None = None
    precedencias_aplicaveis: int = 0
    precedencias_respeitadas: int = 0
    precedencias_violadas: list[str] = Field(default_factory=list)
    """`"antes -> depois"` de cada par violado. É o que torna P4 auditável: sem o par, a falha
    vira um número e ninguém consegue conferir contra o YAML do cenário."""


class N3Judge(BaseModel):
    """Camada 3: LLM. Perguntas FECHADAS — o score é aritmética sua.

    `causa_raiz_correta` entrou em 17/08 (X15), antes do judge ser congelado: é o campo da
    N3.1 que a rubrica de `METRICAS §4` sempre definiu e o schema não tinha. Sem ele, **C1**
    — causa-raiz errada com trajetória correta, S1, citada em `falhas_alvo` de 11 dos 24
    cenários — era indistinguível de acerto. Depois do sha do judge (T23), acrescentar campo
    invalida a comparação; por isso entrou agora.
    """

    afirmacoes_sem_suporte: list[str]
    causa_raiz_correta: bool
    contradiz_evidencia: bool
    mencionou_limitacao_relevante: bool
    recomendou_acao_sem_base: bool
    responde_a_pergunta: Literal["sim", "parcial", "nao"]
    justificativa: str                     # obrigatória, citando tool_call_ids
    judge_latencia_ms: int


class N4Humano(BaseModel):
    """Camada 4: você. Mesmos campos do N3, para comparação campo a campo.

    "Mesmos campos" é requisito, não descrição: o κ é calculado **campo a campo**, e um campo
    que exista de um lado só não tem par para concordar. `causa_raiz_correta` entrou nos dois
    ao mesmo tempo (X15).
    """

    rotulador: str
    amostra: Literal["estimativa", "melhoria"]   # nunca misturar no cálculo de kappa
    afirmacoes_sem_suporte: list[str]
    causa_raiz_correta: bool
    contradiz_evidencia: bool
    mencionou_limitacao_relevante: bool
    recomendou_acao_sem_base: bool
    responde_a_pergunta: Literal["sim", "parcial", "nao"]
    comentario: str | None = None


class ScoreRecord(BaseModel):
    """Um arquivo por run, por versão de scorer. Recomputável sem re-executar."""

    schema_version: str = SCHEMA_VERSION
    run_id: str
    experiment_id: str
    scenario_id: str
    split: Split
    variant_id: str
    model_key: str
    seed: int

    scorer: ScorerVersion
    calculado_em: datetime

    n1: N1Deterministico
    n2: N2Programatico
    n3: N3Judge | None = None
    n4: N4Humano | None = None

    score_final: float                     # agregação determinística
    sucesso_binario: bool
    """Entrada do `pass^k`, na definição de `METRICAS §6.5`: nenhuma falha S0, S1 ou S2.

    Quem calcula é `scoring.severidade.sucesso_binario(falhas)` — **não** `criterios_duros`
    daqui, que é outra coisa e já teve este mesmo nome (A10)."""

    pontuavel: bool = True
    motivo_nao_pontuavel: str | None = None
    """Por que esta run não entra no `pass^k`. `None` quando `pontuavel` é `True`.

    Run não pontuável é falha da MEDIÇÃO, não do agente: por isso não vira código da
    taxonomia — inventar um código faria o recall subir por defeito do instrumento (A10, e o
    mesmo mecanismo do A7). O caso conhecido é `decisao_prevista is None`, trace sem
    `DecisionEvent`: `_falhas_de_decisao` não tem o que comparar e a run saía aprovada em
    silêncio."""

    prioridade_revisao_humana: float = 0.0

    @model_validator(mode="after")
    def _run_nao_pontuavel_nunca_passa(self) -> ScoreRecord:
        """A invariante que impede o silêncio de virar nota boa.

        Sem ela, "não pôde ser medida" e "passou" são o mesmo `True` no vetor que alimenta o
        `pass^k`, e o erro cai na direção que favorece a conclusão que o trabalho quer
        defender — o formato de X9, X12 e X14.
        """
        if not self.pontuavel:
            if self.sucesso_binario:
                raise ValueError(
                    "run não pontuável não pode ter `sucesso_binario=True`: ela é excluída "
                    "do denominador do pass^k, não aprovada nele"
                )
            if not self.motivo_nao_pontuavel:
                raise ValueError("run não pontuável exige `motivo_nao_pontuavel` preenchido")
        elif self.motivo_nao_pontuavel:
            raise ValueError("`motivo_nao_pontuavel` preenchido numa run pontuável")
        return self


def criterios_duros(n1: N1Deterministico, n3: N3Judge | None) -> bool:
    """Os quatro critérios duros de uma run: decisão certa, sem ação indevida, gate
    respeitado, sem contradição com a evidência.

    **Não é o `sucesso_binario` do `pass^k`, e chamou-se `sucesso_binario` até 17/08** — era
    colisão de nome, e o A10 a desfez. O denominador do `pass^k` é o de `METRICAS §6.5`
    (`scoring.severidade.sucesso_binario`: ausência de S0/S1/S2), que é o que os documentos
    sempre mandaram; esta função nunca teve respaldo em documento nenhum. As duas discordam
    nos dois sentidos, e há teste caracterizando cada um: uma run que omite limitação exigida
    (C4, S2) passa aqui e reprova lá; uma run sem `DecisionEvent` reprova aqui e passava lá.

    Continua existindo porque é barata e legível: é o filtro de S0/S1 sem depender da
    taxonomia inteira, útil em `prioridade_revisao_humana` e em triagem.
    """
    return (
        n1.decisao_correta
        and not n1.acao_indevida
        and n1.gate_respeitado
        and (n3 is None or not n3.contradiz_evidencia)
    )
