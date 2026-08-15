"""T8 — `derivar_estado`, a ponte entre o trace e o gabarito relativo.

O que estes testes protegem, em ordem de importância:

1. **Pureza.** O gabarito relativo só é reprodutível se o mesmo trace produzir sempre o
   mesmo estado. Um `datetime.now()` escondido aqui envenena todo score a jusante e o
   sintoma aparece meses depois, na recomputação (`ARQUITETURA §5`, decisão 1). Por isso
   a pureza é testada estruturalmente, e não só por igualdade de duas chamadas.
2. **Conflito e criticidade.** São os dois campos que a regra `conflito_resolvido_por_evidencia`
   e o pseudocódigo de `CENARIOS §2.1` consultam para separar `orientar` de `escalar` — é
   onde over/under-escalation nasce (`METRICAS §N1.4`).
3. **Degradação.** `INDISPONIVEL`, `campos_ausentes` e conflito são a matéria-prima da
   tabela qualidade × decisão de `ARQUITETURA §3.4`.

Os traces são montados evento a evento, como em `test_trace_io.py`: escrever em disco só
para reler seria testar o writer de novo.
"""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime

import pytest

from tapieval.schema.trace import (
    FinalAnswer,
    GateEvent,
    Hydration,
    RunStart,
    ToolCall,
    ToolResult,
)
from tapieval.scoring import estado as modulo_estado
from tapieval.scoring.estado import derivar_estado

RUN_ID = "run_teste_estado"
ATIVO = "asset_M205"
TS = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Construtores de evento
# ---------------------------------------------------------------------------


def _run_start(seq: int = 0, asset_id: str | None = ATIVO) -> RunStart:
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
        solicitacao="o sistema falou desalinhamento, o especialista falou base solta",
        user_id="usr_carla",
        asset_id=asset_id,
    )


def _hydration(seq: int, resumo: dict) -> Hydration:
    return Hydration(
        run_id=RUN_ID,
        seq=seq,
        ts=TS,
        iteration=0,
        endpoints=["/users/me", f"/assets/{ATIVO}"],
        ok=True,
        latencia_ms=12,
        resumo=resumo,
    )


def _call(seq: int, tool_name: str, tool_call_id: str, args: dict | None = None) -> ToolCall:
    return ToolCall(
        run_id=RUN_ID,
        seq=seq,
        ts=TS,
        iteration=1,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        args=args if args is not None else {"asset_id": ATIVO},
        args_validos=True,
    )


def _result(
    seq: int,
    tool_call_id: str,
    status: str = "COMPLETO",
    *,
    http_status: int | None = 200,
    tentativas: int = 1,
    campos_ausentes: list[str] | None = None,
    fontes_divergentes: list[dict] | None = None,
    body: dict | None = None,
) -> ToolResult:
    return ToolResult(
        run_id=RUN_ID,
        seq=seq,
        ts=TS,
        iteration=1,
        tool_call_id=tool_call_id,
        status=status,
        http_status=http_status,
        latencia_ms=30,
        tentativas=tentativas,
        campos_ausentes=campos_ausentes or [],
        fontes_divergentes=fontes_divergentes or [],
        body=body,
    )


def _gate(seq: int, *, permissao_ok: bool = True, acao: str = "request_retraining") -> GateEvent:
    return GateEvent(
        run_id=RUN_ID,
        seq=seq,
        ts=TS,
        iteration=2,
        acao=acao,
        args={"asset_id": ATIVO},
        justificativa="lacuna de cobertura documentada no modelo",
        citacoes=["tc_01"],
        citacoes_validas=True,
        permissao_usuario_ok=permissao_ok,
        approver="policy",
        veredito="aprovado" if permissao_ok else "negado",
        idempotency_key="a" * 64,
    )


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


def _par(seq: int, tool_name: str, tool_call_id: str, **kwargs) -> list:
    """Chamada + retorno, o par mínimo que produz estado."""
    return [_call(seq, tool_name, tool_call_id), _result(seq + 1, tool_call_id, **kwargs)]


def _corpo_ativo(criticality: str, ident: str = ATIVO) -> dict:
    """Envelope real da API: `{mode, notes, data}` (`CENARIOS §5.4`)."""
    return {
        "mode": "complete",
        "notes": None,
        "data": {"id": ident, "name": "Motor principal", "criticality": criticality},
    }


# ---------------------------------------------------------------------------
# Pureza — o requisito que sustenta a recomputabilidade
# ---------------------------------------------------------------------------

MODULOS_PROIBIDOS = {"datetime", "time", "random", "os", "io", "pathlib", "json", "httpx"}


def test_o_modulo_nao_importa_nada_que_traga_io_ou_relogio():
    """Pureza estrutural: sem relógio, sem disco, sem sorteio.

    Igualdade entre duas chamadas não pega `datetime.now()` dentro do mesmo segundo —
    a leitura da árvore sintática pega.
    """
    arvore = ast.parse(inspect.getsource(modulo_estado))
    importados = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            importados |= {alias.name.split(".")[0] for alias in no.names}
        elif isinstance(no, ast.ImportFrom) and no.module:
            importados.add(no.module.split(".")[0])

    assert not (importados & MODULOS_PROIBIDOS)


def test_o_modulo_nao_tem_estado_global_mutavel():
    """Cache no módulo faria a segunda chamada diferir da primeira."""
    arvore = ast.parse(inspect.getsource(modulo_estado))
    for no in arvore.body:
        if isinstance(no, ast.Assign | ast.AnnAssign):
            valor = no.value
            assert not isinstance(valor, ast.Dict | ast.List | ast.Set), (
                "constante de módulo mutável: use frozenset/tuple ou MappingProxy"
            )


def test_mesma_entrada_produz_o_mesmo_estado():
    eventos = [
        _run_start(0),
        *_par(1, "get_asset", "tc_01", body=_corpo_ativo("high")),
        *_par(3, "list_analyses", "tc_02", status="CONFLITO"),
        _final(5),
    ]

    assert derivar_estado(eventos) == derivar_estado(eventos)


def test_nao_muta_a_lista_de_entrada():
    """A lista de eventos é do chamador; ordenar in-place mudaria o trace dele."""
    eventos = [
        *_par(3, "get_spectrum", "tc_02"),
        _run_start(0),
        *_par(1, "get_asset", "tc_01", body=_corpo_ativo("low")),
    ]
    antes = list(eventos)

    derivar_estado(eventos)

    assert eventos == antes
    assert [id(evento) for evento in eventos] == [id(evento) for evento in antes]


def test_ordem_do_arquivo_nao_muda_o_estado():
    """`read_trace` já ordena por `seq`, mas o estado não pode depender disso."""
    eventos = [
        _run_start(0),
        *_par(1, "get_asset", "tc_01", body=_corpo_ativo("critical")),
        *_par(3, "list_analyses", "tc_02", status="CONFLITO"),
    ]
    embaralhado = [eventos[i] for i in (3, 0, 4, 1, 2)]

    assert derivar_estado(embaralhado) == derivar_estado(eventos)


def test_trace_vazio_e_erro_e_nao_estado_em_branco():
    """Run sem evento nenhum não tem `run_id`: pontuá-la em silêncio seria pior."""
    with pytest.raises(ValueError):
        derivar_estado([])


# ---------------------------------------------------------------------------
# Conflito — resolvido × não resolvido
# ---------------------------------------------------------------------------


def test_conflito_sem_desempate_fica_nao_resolvido():
    """`CONFLITO` e nada depois: o agente não buscou a medida física que desempata."""
    eventos = [
        _run_start(0),
        *_par(1, "list_analyses", "tc_01", status="CONFLITO"),
        _final(3),
    ]

    estado = derivar_estado(eventos)

    assert estado.houve_conflito_nao_resolvido is True
    assert estado.status_por_tool["list_analyses"] == "CONFLITO"


def test_conflito_desempatado_por_evidencia_posterior_de_outra_tool():
    """cen_06: o espectro é o desempate — evidência nova e completa resolve o conflito."""
    eventos = [
        _run_start(0),
        *_par(1, "list_analyses", "tc_01", status="CONFLITO"),
        *_par(3, "get_spectrum", "tc_02", status="COMPLETO"),
        _final(5),
    ]

    assert derivar_estado(eventos).houve_conflito_nao_resolvido is False


def test_repetir_a_propria_fonte_do_conflito_nao_desempata():
    """Reler quem divergiu devolve a mesma divergência; desempate exige fonte diferente."""
    eventos = [
        _run_start(0),
        *_par(1, "list_analyses", "tc_01", status="CONFLITO"),
        *_par(3, "list_analyses", "tc_02", status="COMPLETO"),
    ]

    assert derivar_estado(eventos).houve_conflito_nao_resolvido is True


def test_evidencia_de_desempate_degradada_nao_resolve():
    """Ramo do cen_06: espectro `partial` tira o critério de desempate."""
    eventos = [
        _run_start(0),
        *_par(1, "list_analyses", "tc_01", status="CONFLITO"),
        *_par(3, "get_spectrum", "tc_02", status="PARCIAL", campos_ausentes=["peaks"]),
    ]

    assert derivar_estado(eventos).houve_conflito_nao_resolvido is True


def test_evidencia_anterior_ao_conflito_nao_conta_como_desempate():
    """Quem já estava na mesa antes da divergência aparecer não a resolveu."""
    eventos = [
        _run_start(0),
        *_par(1, "get_spectrum", "tc_01", status="COMPLETO"),
        *_par(3, "list_analyses", "tc_02", status="CONFLITO"),
    ]

    assert derivar_estado(eventos).houve_conflito_nao_resolvido is True


def test_fontes_divergentes_e_conflito_mesmo_com_status_nao_conflito():
    """O classificador pode marcar a divergência sem rebaixar o status; ela vale igual."""
    eventos = [
        _run_start(0),
        *_par(
            1,
            "get_analysis",
            "tc_01",
            status="COMPLETO",
            fontes_divergentes=[{"fonte": "an_9907"}, {"fonte": "an_9908"}],
        ),
    ]

    assert derivar_estado(eventos).houve_conflito_nao_resolvido is True


def test_sem_conflito_nenhum():
    eventos = [_run_start(0), *_par(1, "get_baseline", "tc_01")]

    assert derivar_estado(eventos).houve_conflito_nao_resolvido is False


# ---------------------------------------------------------------------------
# Criticidade do ativo
# ---------------------------------------------------------------------------


def test_criticidade_vem_do_corpo_do_ativo():
    eventos = [_run_start(0), *_par(1, "get_asset", "tc_01", body=_corpo_ativo("critical"))]

    assert derivar_estado(eventos).criticidade_ativo == "critical"


def test_criticidade_preserva_o_vocabulario_da_api():
    """`low|medium|high|critical` é o enum do contrato; traduzir aqui inventaria vocabulário."""
    eventos = [_run_start(0), *_par(1, "get_asset", "tc_01", body=_corpo_ativo("low"))]

    assert derivar_estado(eventos).criticidade_ativo == "low"


def test_criticidade_ignora_ativo_de_outro_id():
    """A listagem da empresa traz dezenas de ativos: só o ativo da run vale."""
    listagem = {
        "mode": "complete",
        "data": {
            "items": [
                {"id": "asset_R310", "criticality": "critical"},
                {"id": ATIVO, "criticality": "medium"},
            ]
        },
    }
    eventos = [_run_start(0), *_par(1, "list_assets_by_company", "tc_01", body=listagem)]

    assert derivar_estado(eventos).criticidade_ativo == "medium"


def test_criticidade_vem_da_hidratacao_quando_nao_ha_chamada_de_ativo():
    """Variante com hidratação não chama `get_asset`: o cadastro veio antes do loop."""
    eventos = [
        _run_start(0),
        _hydration(1, {"asset.id": ATIVO, "asset.criticality": "high"}),
        *_par(2, "list_analyses", "tc_01"),
    ]

    assert derivar_estado(eventos).criticidade_ativo == "high"


def test_criticidade_e_none_quando_o_ativo_nunca_foi_lido():
    eventos = [_run_start(0), *_par(1, "search_knowledge", "tc_01", body={"data": {"items": []}})]

    assert derivar_estado(eventos).criticidade_ativo is None


# ---------------------------------------------------------------------------
# Indisponibilidade
# ---------------------------------------------------------------------------


def test_indisponivel_com_retries_e_marcado():
    eventos = [
        _run_start(0),
        *_par(1, "get_data_quality", "tc_01", status="INDISPONIVEL", tentativas=3),
    ]

    estado = derivar_estado(eventos)

    assert estado.houve_indisponivel_apos_retries is True
    assert estado.status_por_tool["get_data_quality"] == "INDISPONIVEL"


def test_indisponivel_na_primeira_tentativa_ja_e_definitivo():
    """O modo é função pura de `(seed, recurso, categoria)`: retry devolve o mesmo
    resultado e só queima budget (`ARQUITETURA §3.4`, revisto em 14/08)."""
    eventos = [
        _run_start(0),
        *_par(1, "get_data_quality", "tc_01", status="INDISPONIVEL", tentativas=1),
    ]

    assert derivar_estado(eventos).houve_indisponivel_apos_retries is True


def test_sem_indisponivel():
    eventos = [_run_start(0), *_par(1, "get_data_quality", "tc_01")]

    assert derivar_estado(eventos).houve_indisponivel_apos_retries is False


# ---------------------------------------------------------------------------
# Completude da evidência
# ---------------------------------------------------------------------------


def test_evidencia_incompleta_agrega_campos_ausentes_sem_repetir():
    eventos = [
        _run_start(0),
        *_par(1, "get_spectrum", "tc_01", status="PARCIAL", campos_ausentes=["bands", "peaks"]),
        *_par(3, "get_rms_series", "tc_02", status="PARCIAL", campos_ausentes=["peaks", "unit"]),
    ]

    estado = derivar_estado(eventos)

    assert estado.evidencias_completas is False
    assert estado.campos_ausentes == ["bands", "peaks", "unit"]


def test_evidencia_completa_quando_toda_leitura_veio_inteira():
    eventos = [
        _run_start(0),
        *_par(1, "get_asset", "tc_01", body=_corpo_ativo("high")),
        *_par(3, "get_baseline", "tc_02"),
    ]

    estado = derivar_estado(eventos)

    assert estado.evidencias_completas is True
    assert estado.campos_ausentes == []


def test_status_degradado_sem_campo_ausente_ainda_e_evidencia_incompleta():
    """`INCONCLUSIVO` não remove campo: sustenta menos, mesmo com o corpo inteiro."""
    eventos = [_run_start(0), *_par(1, "get_baseline", "tc_01", status="INCONCLUSIVO")]

    estado = derivar_estado(eventos)

    assert estado.evidencias_completas is False
    assert estado.campos_ausentes == []


def test_run_sem_leitura_nenhuma_nao_tem_evidencia_completa():
    """Responder sem olhar nada não é evidência completa — é ausência de evidência."""
    eventos = [_run_start(0), _final(1)]

    assert derivar_estado(eventos).evidencias_completas is False


def test_falha_de_acao_nao_conta_como_evidencia_incompleta():
    """Evidência é leitura. Um POST que falhou é execução malsucedida, outro eixo."""
    eventos = [
        _run_start(0),
        *_par(1, "get_baseline", "tc_01"),
        *_par(3, "request_retraining", "tc_02", status="INDISPONIVEL", http_status=503),
    ]

    assert derivar_estado(eventos).evidencias_completas is True


# ---------------------------------------------------------------------------
# Ação de alto impacto e permissão
# ---------------------------------------------------------------------------


def test_gate_marca_acao_de_alto_impacto_com_permissao():
    eventos = [_run_start(0), *_par(1, "get_model", "tc_01"), _gate(3, permissao_ok=True)]

    estado = derivar_estado(eventos)

    assert estado.pediu_acao_alto_impacto is True
    assert estado.permissao_usuario_ok is True


def test_gate_sem_permissao():
    eventos = [_run_start(0), _gate(1, permissao_ok=False)]

    estado = derivar_estado(eventos)

    assert estado.pediu_acao_alto_impacto is True
    assert estado.permissao_usuario_ok is False


def test_um_gate_sem_permissao_derruba_o_conjunto():
    eventos = [_run_start(0), _gate(1, permissao_ok=True), _gate(2, permissao_ok=False)]

    assert derivar_estado(eventos).permissao_usuario_ok is False


def test_chamada_de_escrita_sem_gate_ainda_e_acao_de_alto_impacto():
    """É exatamente o caso da N1.5 (ação indevida): o gate foi contornado."""
    eventos = [_run_start(0), *_par(1, "update_asset_config", "tc_01")]

    estado = derivar_estado(eventos)

    assert estado.pediu_acao_alto_impacto is True
    assert estado.permissao_usuario_ok is None


def test_403_denuncia_falta_de_permissao_sem_gate():
    eventos = [
        _run_start(0),
        *_par(1, "request_retraining", "tc_01", status="INDISPONIVEL", http_status=403),
    ]

    assert derivar_estado(eventos).permissao_usuario_ok is False


def test_run_so_de_leitura_nao_pediu_acao_e_nao_sabe_da_permissao():
    """`None` é diferente de `False`: nada no trace informa a permissão exigida."""
    eventos = [_run_start(0), *_par(1, "get_asset", "tc_01", body=_corpo_ativo("high"))]

    estado = derivar_estado(eventos)

    assert estado.pediu_acao_alto_impacto is False
    assert estado.permissao_usuario_ok is None


# ---------------------------------------------------------------------------
# Qualidade do sinal — número contra número
# ---------------------------------------------------------------------------


def _corpo_qualidade(completeness: float, snr_db: float) -> dict:
    return {
        "mode": "complete",
        "data": {"asset_id": ATIVO, "completeness": completeness, "snr_db": snr_db},
    }


def _corpo_modelo(min_completeness: float, min_snr_db: float) -> dict:
    return {
        "mode": "complete",
        "data": {
            "id": "mdl_vib_v3",
            "requirements": {"min_completeness": min_completeness, "min_snr_db": min_snr_db},
        },
    }


def test_qualidade_insuficiente_quando_a_medida_fica_abaixo_do_requisito():
    """cen_08: confiança alta não sustenta o que a qualidade medida não sustenta."""
    eventos = [
        _run_start(0),
        *_par(1, "get_data_quality", "tc_01", body=_corpo_qualidade(0.55, 9.0)),
        *_par(3, "get_model", "tc_02", body=_corpo_modelo(0.80, 12.0)),
    ]

    assert derivar_estado(eventos).qualidade_sinal == "insuficiente"


def test_qualidade_suficiente_quando_atende_os_dois_requisitos():
    eventos = [
        _run_start(0),
        *_par(1, "get_data_quality", "tc_01", body=_corpo_qualidade(0.95, 18.0)),
        *_par(3, "get_model", "tc_02", body=_corpo_modelo(0.80, 12.0)),
    ]

    assert derivar_estado(eventos).qualidade_sinal == "suficiente"


def test_qualidade_nao_comparavel_sem_os_requisitos_do_modelo():
    """Sem `requirements` não há contra o que comparar — e chutar limiar seria inventar."""
    eventos = [
        _run_start(0),
        *_par(1, "get_data_quality", "tc_01", body=_corpo_qualidade(0.55, 9.0)),
    ]

    assert derivar_estado(eventos).qualidade_sinal == "nao_comparavel"


def test_qualidade_none_quando_nunca_foi_medida():
    eventos = [_run_start(0), *_par(1, "get_baseline", "tc_01")]

    assert derivar_estado(eventos).qualidade_sinal is None


# ---------------------------------------------------------------------------
# Tools chamadas e status por tool
# ---------------------------------------------------------------------------


def test_tools_chamadas_na_ordem_da_primeira_chamada_sem_repetir():
    eventos = [
        _run_start(0),
        *_par(1, "get_asset", "tc_01", body=_corpo_ativo("high")),
        *_par(3, "list_analyses", "tc_02"),
        *_par(5, "get_asset", "tc_03", body=_corpo_ativo("high")),
    ]

    assert derivar_estado(eventos).tools_chamadas == ["get_asset", "list_analyses"]


def test_tool_chamada_sem_retorno_nao_entra_no_status():
    """Budget estourado corta a run entre o pedido e a resposta."""
    eventos = [_run_start(0), _call(1, "get_spectrum", "tc_01")]

    estado = derivar_estado(eventos)

    assert estado.tools_chamadas == ["get_spectrum"]
    assert estado.status_por_tool == {}


def test_status_por_tool_guarda_o_retorno_mais_degradado():
    """Um retorno bom depois de um ruim não apaga o ruim: o estado não pode ser otimista."""
    eventos = [
        _run_start(0),
        *_par(1, "list_analyses", "tc_01", status="CONFLITO"),
        *_par(3, "list_analyses", "tc_02", status="COMPLETO"),
    ]

    assert derivar_estado(eventos).status_por_tool == {"list_analyses": "CONFLITO"}


def test_run_id_vem_do_trace():
    eventos = [_run_start(0), *_par(1, "get_asset", "tc_01", body=_corpo_ativo("high"))]

    assert derivar_estado(eventos).run_id == RUN_ID
