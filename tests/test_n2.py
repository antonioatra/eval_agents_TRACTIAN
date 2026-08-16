"""T11 — `pontuar_n2`, a camada de trajetória (`METRICAS §3`).

O que estes testes protegem, em ordem de importância:

1. **N2.1, aderência causal.** É a única métrica de custo zero com chance de pegar falha de
   CONTEÚDO ("concluiu desvio sem ter lido o baseline"), e por isso `METRICAS §N2.1` a marca
   com ★. Os testes fixam o que é verificável no trace e o que ficou declaradamente de fora.
2. **Cobertura evidencial.** A diferença entre "chamou a tool" e "o campo apareceu no payload"
   é exatamente o que `METRICAS §N1.3` cobra: quem chama `get_baseline` com o ativo errado
   marca ponto em N1.1 e zero aqui.
3. **Redundância.** `cache_hit` é alegação do servidor MCP. O scorer recalcula do trace, e há
   teste de que um `cache_hit=False` mentindo continua sendo pego.
4. **Pureza.** Mesmo trace, mesmo score, sempre — `ARQUITETURA §5`, decisão 1.

Os traces são montados evento a evento, como em `test_estado.py`.
"""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime

import pytest

from tapieval.schema.trace import (
    BudgetEvent,
    FinalAnswer,
    Hydration,
    LLMCall,
    RunEnd,
    RunStart,
    ToolCall,
    ToolResult,
)
from tapieval.scoring import n2 as modulo_n2
from tapieval.scoring.gabarito import (
    DIRETORIO_DE_CENARIOS,
    Cenario,
    carregar_cenarios,
    carregar_regras,
)
from tapieval.scoring.n2 import pontuar_n2
from tapieval.scoring.trajetoria import (
    Precedencia,
    TrajetoriaDeReferencia,
    carregar_trajetoria,
    carregar_trajetorias,
)

RUN_ID = "run_teste_n2"
ATIVO = "asset_M205"
TS = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Construtores de evento
# ---------------------------------------------------------------------------


def _run_start(seq: int = 0) -> RunStart:
    return RunStart(
        run_id=RUN_ID,
        seq=seq,
        ts=TS,
        iteration=0,
        experiment_id="exp_teste",
        scenario_id="cen_06_diagnosticos_divergentes",
        split="dev",
        variant_id="base",
        model_key="qwen",
        seed=7,
        env_mode="replay",
        solicitacao="em quem eu acredito?",
        user_id="usr_carla",
        asset_id=ATIVO,
    )


def _hydration(seq: int, resumo: dict) -> Hydration:
    return Hydration(
        run_id=RUN_ID,
        seq=seq,
        ts=TS,
        iteration=0,
        endpoints=["/users/me"],
        ok=True,
        latencia_ms=12,
        resumo=resumo,
    )


def _llm(seq: int, *, parse_ok: bool = True, tentativa: int = 1, iteration: int = 1) -> LLMCall:
    return LLMCall(
        run_id=RUN_ID,
        seq=seq,
        ts=TS,
        iteration=iteration,
        model_key="qwen",
        prompt_sha="a" * 64,
        prompt_tokens=100,
        completion_tokens=50,
        completion_sha="b" * 64,
        latencia_ms=800,
        finish_reason="stop",
        parse_ok=parse_ok,
        parse_erro=None if parse_ok else "json inválido: falta `decisao`",
        tentativa=tentativa,
    )


def _call(
    seq: int,
    tool_name: str,
    tool_call_id: str,
    args: dict | None = None,
    iteration: int = 1,
) -> ToolCall:
    return ToolCall(
        run_id=RUN_ID,
        seq=seq,
        ts=TS,
        iteration=iteration,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        args=args if args is not None else {"asset_id": ATIVO},
        args_validos=True,
    )


def _result(
    seq: int,
    tool_call_id: str,
    *,
    status: str = "COMPLETO",
    body: dict | None = None,
    cache_hit: bool = False,
    iteration: int = 1,
) -> ToolResult:
    return ToolResult(
        run_id=RUN_ID,
        seq=seq,
        ts=TS,
        iteration=iteration,
        tool_call_id=tool_call_id,
        status=status,
        http_status=200,
        latencia_ms=30,
        cache_hit=cache_hit,
        body=body,
    )


def _par(
    seq: int,
    tool_name: str,
    tool_call_id: str,
    *,
    args: dict | None = None,
    body: dict | None = None,
    cache_hit: bool = False,
    iteration: int = 1,
) -> list:
    """Chamada + retorno, o par mínimo que produz trajetória."""
    return [
        _call(seq, tool_name, tool_call_id, args, iteration=iteration),
        _result(seq + 1, tool_call_id, body=body, cache_hit=cache_hit, iteration=iteration),
    ]


def _final(seq: int) -> FinalAnswer:
    return FinalAnswer(
        run_id=RUN_ID,
        seq=seq,
        ts=TS,
        iteration=3,
        texto="prevalece looseness",
        citacoes=["tc_01"],
        citacoes_validas=True,
    )


def _run_end(seq: int, *, status: str = "ok", tool_calls: int = 4) -> RunEnd:
    return RunEnd(
        run_id=RUN_ID,
        seq=seq,
        ts=TS,
        iteration=3,
        status=status,
        duracao_ms=9000,
        total_tool_calls=tool_calls,
        total_llm_calls=4,
        total_prompt_tokens=1000,
        total_completion_tokens=400,
    )


def _budget(seq: int, limite: str = "max_tool_calls", valor: int = 12) -> BudgetEvent:
    return BudgetEvent(run_id=RUN_ID, seq=seq, ts=TS, iteration=3, limite=limite, valor=valor)


def _corpo(dados: dict, mode: str = "complete") -> dict:
    """Envelope real da API: `{mode, notes, data}` (`CENARIOS §5.4`)."""
    return {"mode": mode, "notes": None, "data": dados}


def _cenario(evidencias: tuple[str, ...] = ()) -> Cenario:
    """Cenário mínimo: só o que a N2 consome (`evidencias_obrigatorias`)."""
    return Cenario(
        id="cen_teste",
        regra=_QUALQUER_REGRA,
        split="dev",
        criticidade_declarada="high",
        evidencias_obrigatorias=evidencias,
        fontes_obrigatorias={},
    )


_QUALQUER_REGRA = carregar_regras()["evidencia_indisponivel"]


# ---------------------------------------------------------------------------
# Pureza — o requisito que sustenta a recomputabilidade
# ---------------------------------------------------------------------------

MODULOS_PROIBIDOS = {"datetime", "time", "random", "os", "io", "pathlib", "yaml", "httpx"}


def test_o_modulo_de_scoring_nao_importa_nada_que_traga_io_ou_relogio():
    """Pureza estrutural: o scorer não lê disco nem relógio.

    O carregamento do YAML mora em `scoring/trajetoria.py` justamente para que este módulo
    possa ser verificado assim — igual ao que `test_estado.py` faz com `derivar_estado`.
    """
    arvore = ast.parse(inspect.getsource(modulo_n2))
    importados = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            importados.update(alias.name.split(".")[0] for alias in no.names)
        elif isinstance(no, ast.ImportFrom) and no.module:
            importados.add(no.module.split(".")[0])
    assert importados.isdisjoint(MODULOS_PROIBIDOS)


def test_o_scorer_nunca_carrega_a_trajetoria_do_disco():
    """A trajetória de referência é INJETADA. Carregá-la aqui reintroduziria I/O no scorer."""
    assert "carregar_trajetoria" not in inspect.getsource(modulo_n2)


def test_a_lista_de_eventos_do_chamador_nao_e_mutada_nem_a_ordem_importa():
    eventos = [*_par(1, "get_baseline", "tc_01"), *_par(3, "get_spectrum", "tc_02"), _final(5)]
    embaralhados = [eventos[3], eventos[0], eventos[4], eventos[1], eventos[2]]
    copia = list(embaralhados)
    cenario = _cenario()

    assert pontuar_n2(embaralhados, cenario) == pontuar_n2(eventos, cenario)
    assert embaralhados == copia


def test_trace_vazio_e_erro_e_nao_um_zero_silencioso():
    with pytest.raises(ValueError, match="vazio"):
        pontuar_n2([], _cenario())


# ---------------------------------------------------------------------------
# N2.4 — volume · N2.5 — budget
# ---------------------------------------------------------------------------


def test_volume_conta_tool_calls_e_a_maior_iteracao():
    eventos = [
        _run_start(),
        *_par(1, "get_asset", "tc_01", iteration=1),
        *_par(3, "get_baseline", "tc_02", iteration=2),
        _final(5),
    ]
    n2 = pontuar_n2(eventos, _cenario())
    assert n2.n_tool_calls == 2
    assert n2.n_iteracoes == 3  # `_final` está na iteração 3


def test_evento_de_budget_marca_estouro():
    eventos = [_run_start(), *_par(1, "get_asset", "tc_01"), _budget(3), _final(4)]
    assert pontuar_n2(eventos, _cenario()).estourou_budget


def test_run_end_budget_exceeded_marca_estouro_sem_evento_de_budget():
    eventos = [_run_start(), *_par(1, "get_asset", "tc_01"), _run_end(3, status="budget_exceeded")]
    assert pontuar_n2(eventos, _cenario()).estourou_budget


def test_limite_de_tool_calls_marca_estouro_mesmo_sem_evento():
    """Fallback para trace que não emitiu `budget`: 12 chamadas é bater em MAX_TOOL_CALLS.

    Quatro endpoints × três chamadas isola o limite de volume do de mesmo endpoint (4).
    """
    tools = ["get_asset", "get_baseline", "get_data_quality", "get_model"] * 3
    eventos = [_run_start()]
    for i, tool in enumerate(tools):
        eventos.extend(_par(1 + 2 * i, tool, f"tc_{i:02d}", args={"janela": i}))
    assert pontuar_n2(eventos, _cenario()).estourou_budget


def test_limite_de_chamadas_ao_mesmo_endpoint_marca_estouro():
    """MAX_SAME_ENDPOINT = 4 chamadas ao mesmo endpoint com args diferentes."""
    eventos = [_run_start()]
    for i in range(5):
        eventos.extend(_par(1 + 2 * i, "get_rms_series", f"tc_{i:02d}", args={"janela": i}))
    assert pontuar_n2(eventos, _cenario()).estourou_budget


def test_trajetoria_dentro_do_orcamento_nao_estoura():
    eventos = [_run_start(), *_par(1, "get_asset", "tc_01"), _run_end(3)]
    assert not pontuar_n2(eventos, _cenario()).estourou_budget


# ---------------------------------------------------------------------------
# N2.3 — redundância
# ---------------------------------------------------------------------------


def test_chamada_identica_repetida_conta_como_redundante():
    eventos = [
        _run_start(),
        *_par(1, "get_rms_series", "tc_01", args={"asset_id": ATIVO}),
        *_par(3, "get_rms_series", "tc_02", args={"asset_id": ATIVO}),
    ]
    assert pontuar_n2(eventos, _cenario()).n_redundantes == 1


def test_cache_hit_false_mentindo_nao_esconde_a_redundancia():
    """`cache_hit` é alegação do servidor MCP (T13). O scorer recalcula do trace e vence."""
    eventos = [
        _run_start(),
        *_par(1, "get_rms_series", "tc_01", cache_hit=False),
        *_par(3, "get_rms_series", "tc_02", cache_hit=False),
        *_par(5, "get_rms_series", "tc_03", cache_hit=False),
    ]
    assert pontuar_n2(eventos, _cenario()).n_redundantes == 2


def test_cache_hit_do_servidor_conta_mesmo_sem_repeticao_visivel():
    """O servidor pode normalizar args que o trace mostra como diferentes: união de evidências."""
    eventos = [
        _run_start(),
        *_par(1, "get_rms_series", "tc_01", args={"asset_id": ATIVO, "janela": "24h"}),
        *_par(3, "get_rms_series", "tc_02", args={"asset_id": ATIVO}, cache_hit=True),
    ]
    assert pontuar_n2(eventos, _cenario()).n_redundantes == 1


def test_mesma_tool_com_args_diferentes_nao_e_redundante():
    eventos = [
        _run_start(),
        *_par(1, "get_rms_series", "tc_01", args={"asset_id": ATIVO, "janela": "24h"}),
        *_par(3, "get_rms_series", "tc_02", args={"asset_id": ATIVO, "janela": "7d"}),
    ]
    assert pontuar_n2(eventos, _cenario()).n_redundantes == 0


def test_ordem_das_chaves_dos_args_nao_inventa_diferenca():
    eventos = [
        _run_start(),
        *_par(1, "get_rms_series", "tc_01", args={"asset_id": ATIVO, "janela": "24h"}),
        *_par(3, "get_rms_series", "tc_02", args={"janela": "24h", "asset_id": ATIVO}),
    ]
    assert pontuar_n2(eventos, _cenario()).n_redundantes == 1


def test_args_aninhados_comparam_por_valor():
    eventos = [
        _run_start(),
        *_par(1, "get_spectrum", "tc_01", args={"filtros": {"bandas": [1, 2]}}),
        *_par(3, "get_spectrum", "tc_02", args={"filtros": {"bandas": [1, 2]}}),
    ]
    assert pontuar_n2(eventos, _cenario()).n_redundantes == 1


# ---------------------------------------------------------------------------
# N2.2 — Kendall τ
# ---------------------------------------------------------------------------


def _trajetoria(*tools: str, precedencias: tuple[Precedencia, ...] = ()) -> TrajetoriaDeReferencia:
    return TrajetoriaDeReferencia(
        cenario_id="cen_teste", tools_esperadas=tools, precedencias=precedencias
    )


def _so_chamadas(*tools: str) -> list:
    eventos: list = [_run_start()]
    for i, tool in enumerate(tools):
        eventos.extend(_par(1 + 2 * i, tool, f"tc_{i:02d}", args={"asset_id": f"a{i}"}))
    return eventos


def test_ordem_identica_a_referencia_da_tau_um():
    eventos = _so_chamadas("list_analyses", "get_baseline", "get_spectrum")
    trajetoria = _trajetoria("list_analyses", "get_baseline", "get_spectrum")
    assert pontuar_n2(eventos, _cenario(), trajetoria).ordem_kendall_tau == 1.0


def test_ordem_invertida_da_tau_menos_um():
    eventos = _so_chamadas("get_spectrum", "get_baseline", "list_analyses")
    trajetoria = _trajetoria("list_analyses", "get_baseline", "get_spectrum")
    assert pontuar_n2(eventos, _cenario(), trajetoria).ordem_kendall_tau == -1.0


def test_uma_inversao_adjacente_em_tres_tools_da_um_terco():
    """3 pares; uma troca adjacente deixa 2 concordantes e 1 discordante → (2-1)/3."""
    eventos = _so_chamadas("get_baseline", "list_analyses", "get_spectrum")
    trajetoria = _trajetoria("list_analyses", "get_baseline", "get_spectrum")
    assert pontuar_n2(eventos, _cenario(), trajetoria).ordem_kendall_tau == pytest.approx(1 / 3)


def test_menos_de_duas_tools_em_comum_da_none_e_nao_zero():
    """τ é INDEFINIDO com um par só — `0.0` seria 'ordem aleatória', que é outra afirmação."""
    eventos = _so_chamadas("get_baseline", "get_asset")
    trajetoria = _trajetoria("get_baseline", "get_spectrum")
    assert pontuar_n2(eventos, _cenario(), trajetoria).ordem_kendall_tau is None


def test_sem_trajetoria_de_referencia_tau_e_none():
    eventos = _so_chamadas("get_baseline", "get_spectrum")
    assert pontuar_n2(eventos, _cenario()).ordem_kendall_tau is None


def test_tools_fora_da_referencia_nao_entram_no_tau():
    """`get_current_user` no meio não deve rebaixar uma ordem que respeita a referência."""
    eventos = _so_chamadas("list_analyses", "get_current_user", "get_baseline", "get_spectrum")
    trajetoria = _trajetoria("list_analyses", "get_baseline", "get_spectrum")
    assert pontuar_n2(eventos, _cenario(), trajetoria).ordem_kendall_tau == 1.0


def test_tool_repetida_conta_pela_primeira_ocorrencia():
    """A referência é conjunto ordenado, não sequência com repetição."""
    eventos = _so_chamadas("get_baseline", "get_spectrum", "get_baseline")
    trajetoria = _trajetoria("get_baseline", "get_spectrum")
    assert pontuar_n2(eventos, _cenario(), trajetoria).ordem_kendall_tau == 1.0


# ---------------------------------------------------------------------------
# Cobertura evidencial
# ---------------------------------------------------------------------------


def test_chamar_a_tool_sem_o_campo_no_payload_nao_cobre():
    """`METRICAS §N1.3`: quem chama `get_baseline` do ativo errado marca N1.1 e zero aqui."""
    eventos = [
        _run_start(),
        *_par(1, "get_baseline", "tc_01", body=_corpo({"asset_id": "outro"})),
    ]
    assert pontuar_n2(eventos, _cenario(("baseline.state",))).cobertura_evidencial == 0.0


def test_cobertura_total_quando_todos_os_campos_aparecem():
    eventos = [
        _run_start(),
        *_par(1, "get_asset", "tc_01", body=_corpo({"sensor_status": "offline"})),
        *_par(3, "get_baseline", "tc_02", body=_corpo({"state": "learning"})),
    ]
    cenario = _cenario(("asset.sensor_status", "baseline.state"))
    assert pontuar_n2(eventos, cenario).cobertura_evidencial == 1.0


def test_cobertura_parcial_da_a_fracao_correta():
    """Três de cinco itens do checklist — o critério de pronto de T11."""
    eventos = [
        _run_start(),
        *_par(1, "get_asset", "tc_01", body=_corpo({"sensor_status": "offline"})),
        *_par(3, "get_baseline", "tc_02", body=_corpo({"state": "learning"})),
        *_par(5, "list_analyses", "tc_03", body=_corpo({"inconclusive": True}, "inconclusive")),
    ]
    cenario = _cenario(
        (
            "asset.sensor_status",
            "baseline.state",
            "analyses[]",
            "data_quality.completeness",
            "model.coverage",
        )
    )
    assert pontuar_n2(eventos, cenario).cobertura_evidencial == pytest.approx(0.6)


def test_colecao_e_coberta_pela_listagem_observada_mesmo_vazia():
    """cen_01: sob `inconclusive` a lista SOME e o cenário ainda exige `analyses[]`.

    O YAML é explícito ('só a listagem em si'), então `categoria[]` cobre-se por ter
    observado o payload da listagem — é a única forma de caminho em que isso vale.
    """
    eventos = [
        _run_start(),
        *_par(1, "list_analyses", "tc_01", body=_corpo({"inconclusive": True}, "inconclusive")),
    ]
    assert pontuar_n2(eventos, _cenario(("analyses[]",))).cobertura_evidencial == 1.0


def test_colecao_nao_observada_nao_cobre():
    eventos = [_run_start(), *_par(1, "get_baseline", "tc_01", body=_corpo({"state": "ok"}))]
    assert pontuar_n2(eventos, _cenario(("analyses[]",))).cobertura_evidencial == 0.0


def test_campo_de_item_de_colecao_e_procurado_dentro_dos_itens():
    eventos = [
        _run_start(),
        *_par(
            1,
            "list_analyses",
            "tc_01",
            body=_corpo({"items": [{"id": "an_9907", "confidence": 0.69}]}),
        ),
    ]
    cenario = _cenario(("analyses[].confidence",))
    assert pontuar_n2(eventos, cenario).cobertura_evidencial == 1.0


def test_campo_ausente_nos_itens_da_colecao_nao_cobre():
    eventos = [
        _run_start(),
        *_par(1, "list_analyses", "tc_01", body=_corpo({"items": [{"id": "an_9907"}]})),
    ]
    cenario = _cenario(("analyses[].confidence",))
    assert pontuar_n2(eventos, cenario).cobertura_evidencial == 0.0


def test_campo_da_categoria_errada_nao_cobre():
    """`state` vindo do `data_quality` não é `baseline.state` — a categoria qualifica o campo."""
    eventos = [
        _run_start(),
        *_par(1, "get_data_quality", "tc_01", body=_corpo({"state": "degraded"})),
    ]
    assert pontuar_n2(eventos, _cenario(("baseline.state",))).cobertura_evidencial == 0.0


def test_hidratacao_achatada_cobre_o_campo_do_ativo():
    """A variante com `hidratacao=True` pode nunca chamar `get_asset` (`ARQUITETURA §3.1`)."""
    eventos = [
        _run_start(),
        _hydration(1, {"asset.sensor_status": "offline", "user.permissions": ["read"]}),
    ]
    cenario = _cenario(("asset.sensor_status", "user.permissions"))
    assert pontuar_n2(eventos, cenario).cobertura_evidencial == 1.0


def test_caminho_aninhado_usa_o_ultimo_segmento():
    eventos = [
        _run_start(),
        *_par(
            1,
            "get_model",
            "tc_01",
            body=_corpo({"requirements": {"min_completeness": 0.7}}),
        ),
    ]
    cenario = _cenario(("model.requirements.min_completeness",))
    assert pontuar_n2(eventos, cenario).cobertura_evidencial == 1.0


def test_payload_em_blob_nao_conta_como_observado():
    """`body=None` com `body_sha`: o scorer é puro e não abre blob — limitação declarada."""
    eventos = [_run_start(), _call(1, "get_baseline", "tc_01"), _result(2, "tc_01", body=None)]
    assert pontuar_n2(eventos, _cenario(("baseline.state",))).cobertura_evidencial == 0.0


def test_listagem_em_blob_tambem_nao_cobre_a_colecao():
    """A regra do blob vale inclusive para `categoria[]`, que só exige ter visto a listagem.

    Seria defensável abrir exceção aqui (a coleção se cobre pela observação, e essa dá para
    afirmar sem ler o payload), mas duas semânticas de "observado" no mesmo cálculo tornariam
    a fração ininterpretável. Fixado por teste para ser decisão, não efeito colateral.
    """
    eventos = [_run_start(), _call(1, "list_analyses", "tc_01"), _result(2, "tc_01", body=None)]
    assert pontuar_n2(eventos, _cenario(("analyses[]",))).cobertura_evidencial == 0.0


def test_cenario_sem_checklist_tem_cobertura_um():
    eventos = [_run_start(), *_par(1, "get_asset", "tc_01", body=_corpo({"id": ATIVO}))]
    assert pontuar_n2(eventos, _cenario()).cobertura_evidencial == 1.0


# ---------------------------------------------------------------------------
# N2.6 — `parse_erro`
# ---------------------------------------------------------------------------


def test_conta_uma_falha_por_tentativa_de_parse_que_falhou():
    """Retries de parsing são `llm_call` distintas — cada uma que falhou conta."""
    eventos = [
        _run_start(),
        _llm(1, parse_ok=False, tentativa=1),
        _llm(2, parse_ok=False, tentativa=2),
        _llm(3, parse_ok=True, tentativa=3),
        _final(4),
    ]
    assert pontuar_n2(eventos, _cenario()).parse_failures == 2


def test_saida_estruturada_valida_nao_conta():
    eventos = [_run_start(), _llm(1), _llm(2), _final(3)]
    assert pontuar_n2(eventos, _cenario()).parse_failures == 0


# ---------------------------------------------------------------------------
# N2.1 — aderência causal ★
# ---------------------------------------------------------------------------


def test_precedencia_entre_duas_tools_respeitada():
    eventos = [
        _run_start(),
        *_par(1, "get_current_user", "tc_01"),
        *_par(3, "escalate_case", "tc_02"),
    ]
    trajetoria = _trajetoria(precedencias=(Precedencia("get_current_user", "escalate_case", ""),))
    n2 = pontuar_n2(eventos, _cenario(), trajetoria)
    assert n2.precedencias_aplicaveis == 1
    assert n2.precedencias_respeitadas == 1
    assert n2.aderencia_causal == 1.0
    assert n2.precedencias_violadas == []


def test_precedencia_entre_duas_tools_violada():
    eventos = [
        _run_start(),
        *_par(1, "escalate_case", "tc_01"),
        *_par(3, "get_current_user", "tc_02"),
    ]
    trajetoria = _trajetoria(precedencias=(Precedencia("get_current_user", "escalate_case", ""),))
    n2 = pontuar_n2(eventos, _cenario(), trajetoria)
    assert n2.aderencia_causal == 0.0
    assert n2.precedencias_violadas == ["get_current_user -> escalate_case"]


def test_antes_exige_o_resultado_e_nao_so_a_chamada():
    """Ter PEDIDO a evidência não é tê-la: sem `tool_result` a precedência é violada."""
    eventos = [
        _run_start(),
        _call(1, "get_current_user", "tc_01"),
        *_par(2, "escalate_case", "tc_02"),
        _result(4, "tc_01"),
    ]
    trajetoria = _trajetoria(precedencias=(Precedencia("get_current_user", "escalate_case", ""),))
    assert pontuar_n2(eventos, _cenario(), trajetoria).aderencia_causal == 0.0


def test_acao_qualquer_vale_para_qualquer_tool_de_escrita():
    eventos = [
        _run_start(),
        *_par(1, "get_current_user", "tc_01"),
        *_par(3, "update_asset_config", "tc_02"),
    ]
    trajetoria = _trajetoria(precedencias=(Precedencia("get_current_user", "acao:qualquer", ""),))
    assert pontuar_n2(eventos, _cenario(), trajetoria).aderencia_causal == 1.0


def test_acao_qualquer_olha_a_primeira_escrita_da_run():
    eventos = [
        _run_start(),
        *_par(1, "reprocess_analysis", "tc_01"),
        *_par(3, "get_current_user", "tc_02"),
        *_par(5, "escalate_case", "tc_03"),
    ]
    trajetoria = _trajetoria(precedencias=(Precedencia("get_current_user", "acao:qualquer", ""),))
    assert pontuar_n2(eventos, _cenario(), trajetoria).aderencia_causal == 0.0


def test_consequente_ausente_do_trace_fica_fora_do_denominador():
    """Sem a ação, não há precedência a violar. Não consultar evidência é P1, medido em N1.3."""
    eventos = [_run_start(), *_par(1, "get_current_user", "tc_01")]
    trajetoria = _trajetoria(precedencias=(Precedencia("get_current_user", "acao:qualquer", ""),))
    n2 = pontuar_n2(eventos, _cenario(), trajetoria)
    assert n2.precedencias_aplicaveis == 0
    assert n2.aderencia_causal is None


def test_antecedente_em_prosa_nao_e_verificavel():
    """`investigar:evidencias` é pseudo-evento: nada no trace o testemunha deterministicamente."""
    eventos = [_run_start(), *_par(1, "escalate_case", "tc_01")]
    trajetoria = _trajetoria(
        precedencias=(Precedencia("investigar:evidencias", "acao:qualquer", ""),)
    )
    n2 = pontuar_n2(eventos, _cenario(), trajetoria)
    assert n2.precedencias_aplicaveis == 0
    assert n2.aderencia_causal is None


def test_responder_usuario_se_ancora_no_final_answer():
    """O pseudo-evento NOMEIA um evento do schema — logo é verificável sem ler o texto."""
    eventos = [_run_start(), *_par(1, "get_knowledge_doc", "tc_01"), _final(3)]
    trajetoria = _trajetoria(
        precedencias=(Precedencia("get_knowledge_doc", "responder:usuario", ""),)
    )
    assert pontuar_n2(eventos, _cenario(), trajetoria).aderencia_causal == 1.0


def test_responder_usuario_violado_quando_a_evidencia_chega_depois_da_resposta():
    eventos = [_run_start(), _final(1), *_par(2, "get_knowledge_doc", "tc_01")]
    trajetoria = _trajetoria(
        precedencias=(Precedencia("get_knowledge_doc", "responder:usuario", ""),)
    )
    assert pontuar_n2(eventos, _cenario(), trajetoria).aderencia_causal == 0.0


def test_responder_qualificado_pelo_conteudo_nao_e_verificavel():
    """`responder:conteudo_de_ativo` afirma algo sobre o TEXTO da resposta — isso é N3."""
    eventos = [_run_start(), *_par(1, "get_current_user", "tc_01"), _final(3)]
    trajetoria = _trajetoria(
        precedencias=(Precedencia("get_current_user", "responder:conteudo_de_ativo", ""),)
    )
    assert pontuar_n2(eventos, _cenario(), trajetoria).aderencia_causal is None


def test_consequente_em_prosa_que_nao_e_acao_qualquer_nao_e_verificavel():
    eventos = [_run_start(), *_par(1, "get_baseline", "tc_01")]
    trajetoria = _trajetoria(
        precedencias=(Precedencia("get_baseline", "atribuir:causa_da_ausencia_de_insight", ""),)
    )
    assert pontuar_n2(eventos, _cenario(), trajetoria).aderencia_causal is None


def test_aderencia_e_a_fracao_das_precedencias_verificaveis():
    eventos = [
        _run_start(),
        *_par(1, "get_current_user", "tc_01"),
        *_par(3, "escalate_case", "tc_02"),
        *_par(5, "get_baseline", "tc_03"),
    ]
    trajetoria = _trajetoria(
        precedencias=(
            Precedencia("get_current_user", "escalate_case", ""),  # respeitada
            Precedencia("get_baseline", "escalate_case", ""),  # violada: baseline veio depois
            Precedencia("investigar:evidencias", "acao:qualquer", ""),  # fora do denominador
        )
    )
    n2 = pontuar_n2(eventos, _cenario(), trajetoria)
    assert (n2.precedencias_respeitadas, n2.precedencias_aplicaveis) == (1, 2)
    assert n2.aderencia_causal == 0.5


def test_sem_trajetoria_a_aderencia_e_none():
    eventos = [_run_start(), *_par(1, "get_baseline", "tc_01")]
    assert pontuar_n2(eventos, _cenario()).aderencia_causal is None


# ---------------------------------------------------------------------------
# Trajetória de referência lida do corpus
# ---------------------------------------------------------------------------


def test_a_ordem_de_tools_esperadas_e_a_do_yaml_e_nao_a_alfabetica():
    """`Cenario.tools_esperadas` é `frozenset` — a ORDEM, que é o insumo de τ, só existe aqui."""
    trajetoria = carregar_trajetoria(DIRETORIO_DE_CENARIOS / "cen_01_quebra_sem_aviso.yaml")
    assert trajetoria.tools_esperadas == (
        "get_asset",
        "get_baseline",
        "get_data_quality",
        "list_analyses",
        "get_rms_series",
        "get_model",
    )


def test_as_precedencias_do_yaml_chegam_inteiras():
    trajetoria = carregar_trajetoria(
        DIRETORIO_DE_CENARIOS / "cen_06_diagnosticos_divergentes.yaml"
    )
    assert [(p.antes, p.depois) for p in trajetoria.precedencias] == [
        ("get_spectrum", "decidir:diagnostico_prevalente"),
        ("list_analyses", "comparar:analises"),
        ("get_baseline", "afirmar:validade_da_deteccao"),
    ]
    assert trajetoria.precedencias[0].regra.startswith("o desempate vem do espectro")


def test_o_corpus_inteiro_carrega_e_bate_com_o_conjunto_do_gabarito():
    trajetorias = carregar_trajetorias()
    cenarios = carregar_cenarios()
    assert set(trajetorias) == set(cenarios)
    for cenario_id, trajetoria in trajetorias.items():
        assert frozenset(trajetoria.tools_esperadas) == cenarios[cenario_id].tools_esperadas


def test_o_corpus_inteiro_e_pontuavel_sem_explodir():
    """Fumaça sobre o corpus real: nenhum caminho de evidência ou precedência quebra o scorer."""
    trajetorias = carregar_trajetorias()
    eventos = [
        _run_start(),
        _hydration(1, {"asset.criticality": "high", "user.permissions": ["read"]}),
        *_par(2, "get_baseline", "tc_01", body=_corpo({"state": "established"})),
    ]
    for cenario_id, cenario in carregar_cenarios().items():
        n2 = pontuar_n2(eventos, cenario, trajetorias[cenario_id])
        assert 0.0 <= n2.cobertura_evidencial <= 1.0
        assert n2.aderencia_causal is None or 0.0 <= n2.aderencia_causal <= 1.0


def test_metade_do_corpus_nao_sustenta_a_metrica_estrela():
    """Caracteriza a cobertura real de N2.1 — 23 de 77 pares, 11 de 24 cenários sem nenhum.

    Não é asserção de qualidade, é o número que precisa aparecer no relatório: a métrica que
    `METRICAS §N2.1` marca com ★ é medível em pouco mais da metade do corpus, porque a maioria
    dos `depois:` é afirmação sobre o texto da resposta. Se alguém reescrever os YAMLs
    apontando para eventos (`acao:qualquer`, `responder:usuario`), este teste quebra — e é
    exatamente aí que se quer ser avisado.
    """
    trajetorias = carregar_trajetorias()
    ancorados = {"acao:qualquer", "responder:usuario"}
    verificaveis = {
        cenario_id: [
            p
            for p in trajetoria.precedencias
            if ":" not in p.antes and (":" not in p.depois or p.depois in ancorados)
        ]
        for cenario_id, trajetoria in trajetorias.items()
    }
    total = sum(len(t.precedencias) for t in trajetorias.values())
    assert (total, sum(len(v) for v in verificaveis.values())) == (77, 23)
    assert sum(1 for pares in verificaveis.values() if not pares) == 11


def test_cen_01_nao_tem_nenhuma_precedencia_verificavel():
    """Achado declarado: as quatro precedências de cen_01 têm consequente em prosa.

    A métrica ★ do `METRICAS §3` simplesmente não é medível nesse cenário, e o honesto é
    devolver `None` — não 1.0, que leria como 'tudo respeitado'.
    """
    trajetoria = carregar_trajetoria(DIRETORIO_DE_CENARIOS / "cen_01_quebra_sem_aviso.yaml")
    eventos = [_run_start(), *_par(1, "get_baseline", "tc_01"), *_par(3, "escalate_case", "tc_02")]
    assert pontuar_n2(eventos, _cenario(), trajetoria).aderencia_causal is None
