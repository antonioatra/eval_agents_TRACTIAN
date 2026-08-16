"""T7 — classificação determinística de `StatusRetorno` e de campos ausentes.

DUAS PROPRIEDADES, e elas são o critério de pronto:

1. **`StatusRetorno` sai do campo `mode`, sempre.** Nunca da forma do corpo. Isso importa
   porque `partial` fora de `_PARTIAL_DROP` devolve o payload inteiro e `unavailable` em
   categoria estável também — inferir pela forma daria `COMPLETO` nos dois, que é falso
   negativo justamente nas armadilhas de CEN-11/12/13 (`docs/catalogo_respostas.md §4`).
2. **`campos_ausentes` sai da diferença entre o schema do endpoint e as chaves de `data`.**
   Nunca da `notes`: a nota anuncia "campos ausentes" em oito endpoints onde não falta campo
   nenhum. Ler a nota é acreditar na API contra a evidência dela mesma.

Os payloads abaixo são cópias do que a API devolve — as tabelas de
`docs/catalogo_respostas.md §2–§5`, geradas por `nb01` contra a API no ar.
"""

from __future__ import annotations

from typing import Any

import pytest

from tapieval.env.client import RawResponse
from tapieval.env.status import (
    CAMPOS_EM_COMPLETO,
    TOOLS_SEM_ENVELOPE,
    Classificacao,
    classificar,
)

# ---------------------------------------------------------------------------
# Fixtures de payload — o que a API devolve de verdade
# ---------------------------------------------------------------------------

BASELINE_COMPLETO: dict[str, Any] = {
    "id": "bl_0007",
    "asset_id": "asset_M101",
    "point_id": "pt_M101_DE",
    "state": "established",
    "detection_mode": "baseline",
    "learnable": True,
    "established_at": "2025-06-01T00:00:00Z",
    "invalidated_at": None,
    "invalidation_reason": None,
    "features": [{"feature": "rms_velocity", "reference": 2.1, "tolerance": 0.6}],
}

ATIVO_COMPLETO: dict[str, Any] = {
    "id": "asset_M101",
    "name": "Motor principal da forja",
    "company_id": "comp_forja_br",
    "criticality": "critical",
    "plant": "Planta 1",
    "line": "Forjamento",
    "parent_asset_id": None,
    "machine_type": "motor_induction",
    "rotation_rpm": 1780,
    "bearing_pn": "NU 310",
    "bpfo_hz": 142.3,
    "bpfi_hz": 218.1,
    "bsf_hz": 58.7,
    "ftf_hz": 11.9,
    "line_frequency_hz": 60.0,
    "sensor_status": "online",
    "points": [{"id": "pt_M101_DE", "asset_id": "asset_M101", "location": "DE"}],
}

ANALISE_COMPLETA: dict[str, Any] = {
    "id": "an_9905",
    "asset_id": "asset_M208",
    "point_id": "pt_M208_DE",
    "type": "bearing_fault",
    "detection_mode": "baseline",
    "severity": "high",
    "confidence": 0.87,
    "baseline_state_at_detection": "established",
    "evidence": [{"metric": "rms_velocity", "value": 4.2, "reference": 2.1}],
    "limitations": ["janela de 24h"],
    "model_version": "v3",
    "created_at": "2025-08-01T10:00:00Z",
    "status": "open",
}


def envelope(modo: str, data: Any, notes: str | None = None) -> RawResponse:
    return RawResponse(
        status_code=200, body={"mode": modo, "notes": notes, "data": data}, latencia_ms=12
    )


# ---------------------------------------------------------------------------
# Um teste por modo do §5.1
# ---------------------------------------------------------------------------


def test_complete_da_completo_sem_campo_ausente():
    resultado = classificar("get_baseline", envelope("complete", BASELINE_COMPLETO))
    assert resultado.status == "COMPLETO"
    assert resultado.campos_ausentes == []
    assert resultado.conflito is False


def test_partial_lista_exatamente_o_que_sumiu():
    """`_PARTIAL_DROP['baseline'] == ('features',)`: some `features` e mais nada."""
    corpo = {chave: valor for chave, valor in BASELINE_COMPLETO.items() if chave != "features"}
    nota = "Informação parcial: campos ausentes ['features']"
    resultado = classificar("get_baseline", envelope("partial", corpo, nota))
    assert resultado.status == "PARCIAL"
    assert resultado.campos_ausentes == ["features"]


def test_inconclusive_instavel_perde_o_payload_inteiro():
    """Forma 1 do §3: a coleção some e sobra o marcador."""
    resultado = classificar("get_baseline", envelope("inconclusive", {"inconclusive": True}))
    assert resultado.status == "INCONCLUSIVO"
    assert set(resultado.campos_ausentes) == set(CAMPOS_EM_COMPLETO["get_baseline"])


def test_conflict_marca_o_conflito_e_nao_remove_campo():
    """`conflict` acrescenta a chave booleana e não tira nada (§3)."""
    resultado = classificar(
        "get_analysis", envelope("conflict", {**ANALISE_COMPLETA, "conflict": True})
    )
    assert resultado.status == "CONFLITO"
    assert resultado.campos_ausentes == []
    assert resultado.conflito is True


def test_unavailable_instavel_devolve_corpo_vazio_com_http_200():
    """Não há erro, header nem código: o único sinal é o `mode`."""
    resultado = classificar("get_rms_series", envelope("unavailable", {}))
    assert resultado.status == "INDISPONIVEL"
    assert set(resultado.campos_ausentes) == set(CAMPOS_EM_COMPLETO["get_rms_series"])


# ---------------------------------------------------------------------------
# A armadilha: modo degradado com corpo íntegro
# ---------------------------------------------------------------------------


def test_partial_em_endpoint_sem_entrada_no_drop_nao_inventa_campo_ausente():
    """X5 — `GET /assets/{id}` em `partial` devolve o payload inteiro.

    A `notes` anuncia lacuna; não há lacuna. O agente que "declara a lacuna" aqui está
    alucinando a partir da nota, e é o que CEN-11/12/13 cobram. O classificador não pode
    dar munição para isso.
    """
    resultado = classificar(
        "get_asset",
        envelope("partial", ATIVO_COMPLETO, "Informação parcial: campos ausentes (detalhes)"),
    )
    assert resultado.status == "PARCIAL"
    assert resultado.campos_ausentes == []


def test_unavailable_em_categoria_estavel_mantem_o_status_e_o_payload():
    """`knowledge` é estável: nenhum modo apaga o corpo, só troca a nota.

    O status continua `INDISPONIVEL` — quem manda é o `mode` — mas nada faltou.
    """
    corpo = {"results": [{"id": "doc_01", "type": "sop", "title": "t", "body": "b", "tags": []}]}
    resultado = classificar("search_knowledge", envelope("unavailable", corpo))
    assert resultado.status == "INDISPONIVEL"
    assert resultado.campos_ausentes == []


def test_list_analyses_em_partial_nao_perde_campo():
    """O corte é função do ENDPOINT, não da categoria (§4).

    `analyses` está em `_PARTIAL_DROP`, mas `GET /assets/{id}/analyses` tem payload
    `{"analyses": [...]}` e o corte só toca chaves de primeiro nível. Cobrar `evidence` aqui
    marcaria lacuna inexistente em todo cenário que lista análises.
    """
    itens = {"analyses": [ANALISE_COMPLETA]}
    resultado = classificar("list_analyses", envelope("partial", itens))
    assert resultado.status == "PARCIAL"
    assert resultado.campos_ausentes == []


def test_get_analysis_em_partial_perde_evidence_e_limitations():
    """O par simétrico do teste acima, na mesma seed: aqui o corte é real."""
    corpo = {
        chave: valor
        for chave, valor in ANALISE_COMPLETA.items()
        if chave not in {"evidence", "limitations"}
    }
    resultado = classificar("get_analysis", envelope("partial", corpo))
    assert resultado.campos_ausentes == ["evidence", "limitations"]


def test_campo_presente_com_valor_nulo_nao_e_campo_ausente():
    """`invalidated_at: null` é o estado normal de um baseline `established`.

    A diferença é entre CHAVES, não entre valores: a API que responde `null` respondeu.
    Contar nulo como ausência acusaria lacuna em toda run íntegra.
    """
    resultado = classificar("get_baseline", envelope("complete", BASELINE_COMPLETO))
    assert "invalidated_at" not in resultado.campos_ausentes
    assert "invalidation_reason" not in resultado.campos_ausentes


def test_campos_ausentes_sai_na_ordem_do_schema_e_nao_do_corpo():
    """Ordem estável: o mesmo trace tem de produzir a mesma lista em todo recálculo."""
    resultado = classificar("get_model", envelope("inconclusive", {"inconclusive": True}))
    assert resultado.campos_ausentes == list(CAMPOS_EM_COMPLETO["get_model"])


def test_linha_ausente_no_store_e_inconclusivo_com_tudo_faltando():
    """Forma 3 do §3: `{"<recurso>": null}` — o `mode` vem antes de `resolve_mode`."""
    resultado = classificar(
        "get_baseline",
        envelope("inconclusive", {"baseline": None}, "Nenhum baseline para este ativo/ponto."),
    )
    assert resultado.status == "INCONCLUSIVO"
    assert set(resultado.campos_ausentes) == set(CAMPOS_EM_COMPLETO["get_baseline"])


# ---------------------------------------------------------------------------
# Fora do envelope
# ---------------------------------------------------------------------------


def test_get_current_user_nao_tem_envelope_e_e_completo():
    """Exceção 1 do §5: a API devolve a linha do usuário crua.

    Ler `mode` dali dá ausência de campo, não `"complete"` — então o classificador não pode
    nem tentar, sob pena de marcar `INDISPONIVEL` a única fonte de permissão do agente.
    """
    corpo = {
        "id": "usr_pedro",
        "name": "Pedro",
        "role": "coordenador",
        "permissions": ["read", "escalate"],
        "company_id": "comp_mineracao_andes",
    }
    resposta = RawResponse(status_code=200, body=corpo, latencia_ms=8)
    resultado = classificar("get_current_user", resposta)
    assert resultado.status == "COMPLETO"
    assert resultado.campos_ausentes == []
    assert "get_current_user" in TOOLS_SEM_ENVELOPE


def test_acao_aceita_e_completo():
    """POST/PATCH devolvem `ActionResult` sem envelope."""
    corpo = {"accepted": True, "action_id": "act_1a2b3c4d", "message": "ok"}
    resposta = RawResponse(status_code=201, body=corpo, latencia_ms=30)
    resultado = classificar("escalate_case", resposta)
    assert resultado.status == "COMPLETO"


def test_envelope_ausente_em_endpoint_que_deveria_ter_e_indisponivel():
    """Corpo que não é envelope só chega por falha (proxy, injeção de falha, versão nova).

    Vira `INDISPONIVEL` com motivo declarado em vez de `COMPLETO` por descuido: o dado não
    chegou, e assumir que chegou é o erro que apaga a degradação que se quer medir.
    """
    resposta = RawResponse(status_code=200, body="<html>502</html>", latencia_ms=9)
    resultado = classificar("get_baseline", resposta)
    assert resultado.status == "INDISPONIVEL"
    assert resultado.motivo == "envelope_ausente"


def test_modo_desconhecido_falha_alto():
    """`ModoResposta` é fechado: modo novo na API tem de quebrar aqui, não passar batido."""
    with pytest.raises(ValueError, match="modo desconhecido"):
        classificar("get_baseline", envelope("degraded_v2", BASELINE_COMPLETO))


def test_tool_desconhecida_falha_alto():
    """Sem o schema do endpoint não há como inferir campo ausente — e adivinhar é pior."""
    with pytest.raises(KeyError, match="get_inexistente"):
        classificar("get_inexistente", envelope("complete", {}))


# ---------------------------------------------------------------------------
# Erro HTTP e ausência de resposta
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("http_status", "codigo"),
    [(400, "VALIDATION_ERROR"), (401, "UNAUTHORIZED"), (403, "FORBIDDEN"), (404, "NOT_FOUND")],
)
def test_erro_http_e_indisponivel_com_o_codigo_preservado(http_status: int, codigo: str):
    """Erro não tem `mode`, e `StatusRetorno` não tem valor para "erro".

    `INDISPONIVEL` é o único honesto: a evidência não chegou. A distinção entre "não existe"
    e "o ambiente degradou" não se perde — ela vive em `ToolResult.http_status`, que o T13
    grava ao lado do status. Por isso o motivo volta aqui em vez de virar um sexto valor de
    `StatusRetorno`, que quebraria o vocabulário de TAPI §5.1.
    """
    resposta = RawResponse(
        status_code=http_status, body={"code": codigo, "message": "..."}, latencia_ms=7
    )
    resultado = classificar("get_asset", resposta)
    assert resultado.status == "INDISPONIVEL"
    assert resultado.motivo == codigo
    assert set(resultado.campos_ausentes) == set(CAMPOS_EM_COMPLETO["get_asset"])


def test_sem_resposta_http_e_indisponivel():
    """`status_code is None` é o que o cliente devolve quando não houve resposta nenhuma."""
    resposta = RawResponse(
        status_code=None,
        body={"code": "TRANSPORT_ERROR", "message": "timed out"},
        latencia_ms=10_000,
    )
    resultado = classificar("get_rms_series", resposta)
    assert resultado.status == "INDISPONIVEL"
    assert resultado.motivo == "TRANSPORT_ERROR"


def test_erro_500_sem_corpo_json_ainda_classifica():
    resposta = RawResponse(status_code=500, body="Internal Server Error", latencia_ms=15)
    resultado = classificar("get_asset", resposta)
    assert resultado.status == "INDISPONIVEL"
    assert resultado.motivo == "HTTP_500"


# ---------------------------------------------------------------------------
# Propriedades gerais
# ---------------------------------------------------------------------------


def test_classificacao_e_deterministica_e_imutavel():
    resposta = envelope("partial", {"asset_id": "asset_M101", "completeness": 0.4, "snr_db": 9.0,
                                    "staleness_flag": False, "point_id": "pt"})
    primeira = classificar("get_data_quality", resposta)
    segunda = classificar("get_data_quality", resposta)
    assert primeira == segunda
    assert primeira.campos_ausentes == ["freshness_minutes"]
    with pytest.raises(AttributeError):
        primeira.status = "COMPLETO"  # type: ignore[misc]


def test_nenhum_endpoint_de_leitura_ficou_sem_schema():
    """Toda tool de leitura do catálogo precisa de entrada — senão o classificador falha alto
    no meio da bateria em vez de na importação."""
    leituras = {
        "get_company", "list_assets_by_company", "get_current_user", "get_asset",
        "list_analyses", "get_analysis", "get_baseline", "get_rms_series", "get_spectrum",
        "get_data_quality", "get_model", "search_knowledge", "get_knowledge_doc",
    }
    assert leituras <= set(CAMPOS_EM_COMPLETO)


def test_a_nota_nunca_entra_na_classificacao():
    """Mesma resposta, notas opostas: a classificação não pode mudar.

    É o teste que trava a regra 2 do topo do módulo — a nota mente por construção em oito
    endpoints, e um dia alguém vai querer "aproveitar" o texto dela.
    """
    corpo = {chave: valor for chave, valor in ATIVO_COMPLETO.items()}
    mentindo = classificar("get_asset", envelope("partial", corpo, "campos ausentes ['points']"))
    calada = classificar("get_asset", envelope("partial", corpo, None))
    assert mentindo == calada


def test_classificacao_carrega_o_modo_cru_para_auditoria():
    """O `mode` original sobrevive à tradução: `CENARIOS` fala em `partial`, o trace em
    `PARCIAL`, e reconciliar os dois vocabulários exige ter os dois à mão."""
    resultado = classificar("get_asset", envelope("partial", ATIVO_COMPLETO))
    assert resultado.modo == "partial"
    assert isinstance(resultado, Classificacao)
