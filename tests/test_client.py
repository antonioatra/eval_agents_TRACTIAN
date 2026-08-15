"""T2 — cliente da API do parceiro e modelos do Swagger.

Duas propriedades sustentam tudo que vem depois e são o critério de pronto:

1. `TractianClient.get` devolve um `RawResponse` **cru** — status, corpo e latência
   medida — e não classifica nada. Classificar é da T7, e classificar cedo demais
   destruiria justamente o dado que o framework existe para medir.
2. Os modelos aceitam as respostas **degradadas**. Um modelo que exige campo
   obrigatório explode em `partial`/`inconclusive`/`unavailable`, ou seja, em 40% das
   respostas da API (CENARIOS §8.1) — que são os casos interessantes.

Os payloads dos testes são cópias fiéis do que a API devolve de verdade (conferidos
contra `api/app/{main,store}.py` e os parquet de `data/`), não do que o Swagger promete:
onde os dois divergem, quem manda é a API.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from tapieval.env.client import RawResponse, TractianClient
from tapieval.env.models import (
    Analise,
    Ativo,
    Baseline,
    DocumentoDeConhecimento,
    Envelope,
    ErroDaApi,
    ListaDeAtivos,
    QualidadeDeDados,
    ResultadoDeAcao,
    SerieRms,
    Usuario,
)

BASE = "http://localhost:8000"

# Resposta real de GET /assets/asset_M101 em modo complete (repare: plana).
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
    "points": [{"id": "pt_M101_de", "asset_id": "asset_M101", "location": "DE",
                "sensor_status": "online"}],
}


def _envelope_vazio(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"mode": "complete", "notes": None, "data": {}})


def _transporte(handler: Any = None) -> httpx.MockTransport:
    """Transporte falso. O handler recebe o `httpx.Request`, então dá para inspecioná-lo."""
    return httpx.MockTransport(handler or _envelope_vazio)


def _cliente(handler: Any = None, **kwargs: Any) -> TractianClient:
    return TractianClient(BASE, transport=_transporte(handler), **kwargs)


# ---------------------------------------------------------------------------
# 1. `get` devolve RawResponse tipado, com a latência medida
# ---------------------------------------------------------------------------


def test_get_devolve_raw_response_tipado():
    corpo = {"mode": "complete", "notes": None, "data": ATIVO_COMPLETO}
    with _cliente(lambda _r: httpx.Response(200, json=corpo)) as cliente:
        resposta = cliente.get("/assets/asset_M101")

    assert type(resposta) is RawResponse
    assert resposta.status_code == 200
    assert resposta.body == corpo
    assert isinstance(resposta.latencia_ms, int)


def test_latencia_e_medida_de_verdade():
    """Uma resposta que demora 40ms tem que aparecer como ~40ms, não como 0."""

    def lento(_request: httpx.Request) -> httpx.Response:
        time.sleep(0.04)
        return httpx.Response(200, json={"mode": "complete", "notes": None, "data": {}})

    with _cliente(lento) as cliente:
        resposta = cliente.get("/assets/asset_M101")

    assert resposta.latencia_ms >= 35


def test_latencia_e_inteiro_nao_negativo_em_resposta_instantanea():
    with _cliente() as cliente:
        resposta = cliente.get("/assets/asset_M101")

    assert isinstance(resposta.latencia_ms, int)
    assert resposta.latencia_ms >= 0


# ---------------------------------------------------------------------------
# 2. O cliente é cru: não classifica, não levanta em erro HTTP, não repete
# ---------------------------------------------------------------------------


def test_get_nao_classifica_resposta_degradada():
    """`mode=unavailable` volta como veio. Traduzir para INDISPONIVEL é da T7."""
    corpo = {"mode": "unavailable", "notes": "Indisponibilidade temporária.", "data": {}}
    with _cliente(lambda _r: httpx.Response(200, json=corpo)) as cliente:
        resposta = cliente.get("/assets/asset_G501/rms")

    assert resposta.status_code == 200
    assert resposta.body == corpo


def test_erro_http_vira_raw_response_e_nao_excecao():
    """404 é um fato do ambiente (AUT-05), não um acidente: precisa chegar ao trace."""
    corpo = {"code": "NOT_FOUND", "message": "Ativo não encontrado."}
    with _cliente(lambda _r: httpx.Response(404, json=corpo)) as cliente:
        resposta = cliente.get("/assets/asset_inexistente")

    assert resposta.status_code == 404
    assert resposta.body == corpo


def test_falha_de_transporte_vira_status_code_none():
    """Sem resposta HTTP não há status. `None` diz isso; `0` inventaria um status."""

    def cai(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with _cliente(cai) as cliente:
        resposta = cliente.get("/assets/asset_M101")

    assert resposta.status_code is None
    assert resposta.body["code"] == "TRANSPORT_ERROR"
    assert isinstance(resposta.latencia_ms, int)


def test_nao_repete_a_chamada_em_erro():
    """Retry é do servidor MCP, que emite um evento por tentativa (ARQUITETURA §4.3)."""
    chamadas = []

    def conta(request: httpx.Request) -> httpx.Response:
        chamadas.append(request.url)
        return httpx.Response(503, json={"code": "ERROR", "message": "indisponível"})

    with _cliente(conta) as cliente:
        cliente.get("/assets/asset_M101")

    assert len(chamadas) == 1


def test_nao_repete_a_chamada_em_falha_de_transporte():
    chamadas = []

    def cai(request: httpx.Request) -> httpx.Response:
        chamadas.append(request.url)
        raise httpx.ConnectError("connection refused")

    with _cliente(cai) as cliente:
        cliente.get("/assets/asset_M101")

    assert len(chamadas) == 1


def test_corpo_nao_json_volta_cru():
    """Um proxy no caminho devolve HTML. Perder isso cega o diagnóstico."""
    with _cliente(lambda _r: httpx.Response(502, text="<html>bad gateway</html>")) as cliente:
        resposta = cliente.get("/assets/asset_M101")

    assert resposta.status_code == 502
    assert resposta.body == "<html>bad gateway</html>"


# ---------------------------------------------------------------------------
# 3. Contexto da requisição: x-user-id, seed e resolução do endpoint
# ---------------------------------------------------------------------------


def test_envia_o_header_x_user_id():
    visto = {}

    def espia(request: httpx.Request) -> httpx.Response:
        visto["user"] = request.headers.get("x-user-id")
        return httpx.Response(200, json={})

    with _cliente(espia, user_id="usr_ana") as cliente:
        cliente.get("/users/me")

    assert visto["user"] == "usr_ana"


def test_seed_do_construtor_entra_na_query():
    """Esquecer o seed não dá ambiente aleatório: dá OUTRO ambiente fixo (CENARIOS §8.1)."""
    visto = {}

    def espia(request: httpx.Request) -> httpx.Response:
        visto["url"] = request.url
        return httpx.Response(200, json={})

    with _cliente(espia, seed="s001") as cliente:
        cliente.get("/assets/asset_M101")

    assert visto["url"].params["seed"] == "s001"


def test_seed_da_chamada_vence_o_do_construtor():
    visto = {}

    def espia(request: httpx.Request) -> httpx.Response:
        visto["url"] = request.url
        return httpx.Response(200, json={})

    with _cliente(espia, seed="s001") as cliente:
        cliente.get("/assets/asset_M101", {"seed": "complete"})

    assert visto["url"].params["seed"] == "complete"


def test_sem_seed_configurado_nao_manda_seed():
    """`seed=None` na query seria um seed literal 'None' — outro ambiente ainda."""
    visto = {}

    def espia(request: httpx.Request) -> httpx.Response:
        visto["url"] = request.url
        return httpx.Response(200, json={})

    with _cliente(espia) as cliente:
        cliente.get("/assets/asset_M101")

    assert "seed" not in visto["url"].params


def test_params_da_chamada_convivem_com_o_seed():
    visto = {}

    def espia(request: httpx.Request) -> httpx.Response:
        visto["url"] = request.url
        return httpx.Response(200, json={})

    with _cliente(espia, seed="s001") as cliente:
        cliente.get("/assets/asset_M101/rms", {"point_id": "pt_M101_de"})

    assert visto["url"].params["point_id"] == "pt_M101_de"
    assert visto["url"].params["seed"] == "s001"


@pytest.mark.parametrize("base", ["http://localhost:8000", "http://localhost:8000/"])
def test_endpoint_relativo_resolve_contra_a_base(base):
    visto = {}

    def espia(request: httpx.Request) -> httpx.Response:
        visto["url"] = request.url
        return httpx.Response(200, json={})

    with TractianClient(base, transport=_transporte(espia)) as cliente:
        cliente.get("/assets/asset_M101")

    assert str(visto["url"]) == "http://localhost:8000/assets/asset_M101"


# ---------------------------------------------------------------------------
# 4. Modelos: o envelope e o caminho feliz
# ---------------------------------------------------------------------------


def test_envelope_de_ativo_completo():
    envelope = Envelope[Ativo].model_validate(
        {"mode": "complete", "notes": None, "data": ATIVO_COMPLETO}
    )

    assert envelope.mode == "complete"
    assert envelope.notes is None
    assert envelope.data.id == "asset_M101"
    assert envelope.data.criticality == "critical"
    assert envelope.data.points[0].location == "DE"


def test_ativo_e_plano_como_a_api_devolve():
    """O Swagger promete `hierarchy` e `config.bearing_specs`; a API devolve tudo plano."""
    ativo = Ativo.model_validate(ATIVO_COMPLETO)

    assert ativo.plant == "Planta 1"
    assert ativo.bearing_pn == "NU 310"
    assert ativo.line_frequency_hz == 60.0
    assert not hasattr(ativo, "hierarchy")
    assert not hasattr(ativo, "config")


def test_lista_de_ativos_vem_embrulhada_em_assets():
    envelope = Envelope[ListaDeAtivos].model_validate(
        {"mode": "complete", "notes": None, "data": {"assets": [ATIVO_COMPLETO]}}
    )

    assert [ativo.id for ativo in envelope.data.assets] == ["asset_M101"]


def test_analise_com_evidencias_e_limitacoes():
    dados = {
        "id": "an_9901",
        "asset_id": "asset_G501",
        "point_id": "pt_G501_de",
        "type": "bearing_fault",
        "detection_mode": "baseline",
        "severity": "none",
        "confidence": 0.2,
        "baseline_state_at_detection": "learning",
        "evidence": [{"metric": "bpfo_amplitude", "value": 0.9, "reference": 0.4,
                      "note": "acima da referência"}],
        "limitations": ["baseline_learning", "data_gap"],
        "model_version": "3.2.1",
        "created_at": "2026-07-14T00:00:00+00:00",
        "status": "inconclusive",
    }

    analise = Analise.model_validate(dados)

    assert analise.model_version == "3.2.1"
    assert analise.created_at == datetime(2026, 7, 14, tzinfo=UTC)
    assert analise.evidence[0].metric == "bpfo_amplitude"
    assert analise.limitations == ["baseline_learning", "data_gap"]


def test_serie_rms_com_limiar_derivado_do_baseline():
    dados = {
        "asset_id": "asset_M101",
        "point_id": "pt_M101_de",
        "unit": "mm/s",
        "baseline_reference": 2.1,
        "baseline_state": "established",
        "alarm_threshold": 3.0,
        "samples": [{"ts": "2026-06-16T00:00:00+00:00", "value": 2.078}],
    }

    serie = SerieRms.model_validate(dados)

    assert serie.alarm_threshold == 3.0
    assert serie.samples[0].value == 2.078


def test_qualidade_de_dados_e_documento_de_conhecimento():
    qualidade = QualidadeDeDados.model_validate(
        {"asset_id": "asset_M101", "point_id": "pt_M101_de", "completeness": 0.98,
         "freshness_minutes": 5, "snr_db": 18.2, "staleness_flag": False}
    )
    doc = DocumentoDeConhecimento.model_validate(
        {"id": "kb_glos_001", "type": "glossary", "title": "BPFO", "body": "...",
         "tags": ["rolamento"]}
    )

    assert qualidade.staleness_flag is False
    assert doc.type == "glossary"


def test_usuario_nao_vem_em_envelope():
    """`GET /users/me` é a única leitura sem envelope — devolve o usuário cru."""
    usuario = Usuario.model_validate(
        {"id": "usr_ana", "name": "Ana Mantovani", "role": "maintenance_manager",
         "permissions": ["read", "action_high", "escalate"], "company_id": "comp_forja_br"}
    )

    assert usuario.permissions == ["read", "action_high", "escalate"]


def test_resultado_de_acao_e_erro():
    acao = ResultadoDeAcao.model_validate(
        {"accepted": True, "action_id": "act_1a2b3c4d", "message": "Caso escalado."}
    )
    erro = ErroDaApi.model_validate({"code": "FORBIDDEN", "message": "Permissão necessária."})

    assert acao.action_id == "act_1a2b3c4d"
    assert erro.code == "FORBIDDEN"


# ---------------------------------------------------------------------------
# 5. Modelos sob degradação — o caso que mais importa
# ---------------------------------------------------------------------------


def test_modo_partial_com_campos_faltando_nao_quebra():
    """`partial` remove campos do payload; campo obrigatório aqui viraria falso erro."""
    dados = {k: v for k, v in ATIVO_COMPLETO.items() if k not in ("criticality", "points")}

    envelope = Envelope[Ativo].model_validate(
        {"mode": "partial", "notes": "Informação parcial: campos ausentes ['criticality']",
         "data": dados}
    )

    assert envelope.data.id == "asset_M101"
    assert envelope.data.criticality is None
    assert envelope.data.points == []


def test_modo_inconclusive_preserva_o_marcador_da_api():
    """`{'inconclusive': True, ...}` não cabe no schema do recurso e não pode ser jogado fora."""
    envelope = Envelope[Ativo].model_validate(
        {"mode": "inconclusive", "notes": "dados insuficientes",
         "data": {"inconclusive": True, "asset_id": "asset_M101"}}
    )

    assert envelope.data.inconclusive is True
    assert envelope.data.asset_id == "asset_M101"


def test_modo_conflict_preserva_a_flag_de_conflito():
    envelope = Envelope[Analise].model_validate(
        {"mode": "conflict", "notes": "Conflito entre fontes",
         "data": {"id": "an_9901", "asset_id": "asset_M101", "conflict": True}}
    )

    assert envelope.data.conflict is True


def test_modo_unavailable_com_data_vazio():
    envelope = Envelope[SerieRms].model_validate(
        {"mode": "unavailable", "notes": "Indisponibilidade temporária.", "data": {}}
    )

    assert envelope.data.samples == []
    assert envelope.notes.startswith("Indisponibilidade")


def test_sentinela_de_baseline_ausente():
    """Sem baseline a API devolve `{'baseline': None}`, não `data: null`."""
    envelope = Envelope[Baseline].model_validate(
        {"mode": "inconclusive", "notes": "Nenhum baseline para este ativo/ponto.",
         "data": {"baseline": None}}
    )

    assert envelope.data.state is None


def test_modo_desconhecido_e_rejeitado():
    """O vocabulário de `mode` é fechado: modo novo tem que falhar alto, não passar batido."""
    with pytest.raises(ValueError):
        Envelope[Ativo].model_validate({"mode": "inventado", "notes": None, "data": {}})
