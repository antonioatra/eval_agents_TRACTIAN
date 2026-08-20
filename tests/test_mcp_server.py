"""Testes do servidor MCP — catálogo 1:1, cache por run e validação antes do HTTP.

OS TRÊS TESTES QUE DÃO NOME À TASK
    1. `test_contrato_corpus_x_catalogo` — toda tool citada em qualquer gabarito dos 24
       cenários existe em `list_tools`. É o teste que pega, em milissegundos, o gabarito que
       escreve `get_signal_quality` quando o servidor expõe `get_data_quality`. Sem ele o erro
       aparece no meio da bateria disfarçado de "o agente não chamou a tool esperada".
    2. `test_chamada_identica_sai_do_cache` — o `ToolResult` sai com `cache_hit=True` e a API
       não é tocada de novo.
    3. `test_args_invalidos_nao_chegam_ao_http` — a validação de schema barra antes de
       qualquer requisição.

SEM REDE, SEMPRE
    O transporte HTTP é sempre um duplo (`httpx.MockTransport`). A API do parceiro pode estar
    de pé na máquina de quem roda a suíte, e um teste que a alcançasse mediria o ambiente de
    quem rodou, não o servidor.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import anyio
import httpx
import mcp_types as types
import pytest
import yaml
from mcp import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from tapieval.env.client import TractianClient
from tapieval.env.faults import FaultInjector, FaultSpec
from tapieval.env.status import CAMPOS_EM_COMPLETO
from tapieval.mcp import tools as catalogo
from tapieval.mcp.server import (
    ObservadorEmMemoria,
    RunContext,
    chamar_tool,
    criar_servidor,
    listar_tools,
)
from tapieval.schema.trace import ToolCall, ToolResult
from tapieval.scoring.estado import TOOLS_ALTO_IMPACTO
from tapieval.scoring.gabarito import carregar_cenarios

# ---------------------------------------------------------------------------
# Duplos de teste
# ---------------------------------------------------------------------------


class ApiFalsa:
    """Transporte HTTP falso que registra toda requisição que chegou até ele.

    `requisicoes` é a evidência dos testes de cache e de validação: "não chegou ao HTTP" só
    é verificável contando o que o transporte viu.
    """

    def __init__(self, resposta: Callable[[httpx.Request], httpx.Response] | None = None) -> None:
        self.requisicoes: list[httpx.Request] = []
        self._resposta = resposta or _envelope_completo

    def transporte(self) -> httpx.MockTransport:
        def responder(requisicao: httpx.Request) -> httpx.Response:
            self.requisicoes.append(requisicao)
            return self._resposta(requisicao)

        return httpx.MockTransport(responder)


def _envelope_completo(requisicao: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"mode": "complete", "notes": None, "data": _DADOS_DE_ATIVO})


_DADOS_DE_ATIVO: dict[str, Any] = {
    "id": "asset_H110",
    "name": "Bomba H-110",
    "company_id": "comp_acme",
    "criticality": "high",
    "plant": "P1",
    "line": "L1",
    "parent_asset_id": None,
    "machine_type": "pump",
    "rotation_rpm": 1780,
    "bearing_pn": "6205",
    "bpfo_hz": 89.1,
    "bpfi_hz": 130.9,
    "bsf_hz": 58.2,
    "ftf_hz": 11.8,
    "line_frequency_hz": 60,
    "sensor_status": "online",
    "points": [],
}


def contexto(
    api: ApiFalsa | None = None,
    **extras: Any,
) -> tuple[RunContext, ApiFalsa]:
    """Um `RunContext` de teste com transporte falso e observador em memória."""
    api = api if api is not None else ApiFalsa()
    cliente = TractianClient(
        "http://api.invalida",
        user_id="usr_bruno",
        seed="s001",
        transport=api.transporte(),
    )
    ctx = RunContext(
        run_id="run_teste",
        cliente=cliente,
        observador=ObservadorEmMemoria(),
        **extras,
    )
    return ctx, api


def chamar(ctx: RunContext, nome: str, args: dict[str, Any]) -> types.CallToolResult:
    return asyncio.run(chamar_tool(ctx, nome, args))


def eventos(ctx: RunContext) -> list[Any]:
    assert isinstance(ctx.observador, ObservadorEmMemoria)
    return ctx.observador.eventos


def chamadas(ctx: RunContext) -> list[ToolCall]:
    return [evento for evento in eventos(ctx) if isinstance(evento, ToolCall)]


def resultados(ctx: RunContext) -> list[ToolResult]:
    return [evento for evento in eventos(ctx) if isinstance(evento, ToolResult)]


# ---------------------------------------------------------------------------
# 1. Contrato corpus × catálogo — o teste mais valioso da task
# ---------------------------------------------------------------------------


def test_contrato_corpus_x_catalogo() -> None:
    """Toda tool citada em qualquer gabarito dos 24 cenários existe em `list_tools`.

    Cobre `tools_esperadas`, `tools_aceitaveis`, `proibido` e as chaves de `args_esperados`:
    um nome errado em `proibido` faria a N1.5 medir uma proibição que nunca poderia ser
    violada — falso verde, que é pior que vermelho.
    """
    ctx, _ = contexto()
    expostas = {tool.name for tool in listar_tools(ctx)}

    divergentes: dict[str, list[str]] = {}
    for cenario_id, cenario in sorted(carregar_cenarios().items()):
        citadas = (
            set(cenario.tools_esperadas)
            | set(cenario.tools_aceitaveis)
            | set(cenario.tools_proibidas)
            | set(cenario.args_esperados)
        )
        fora = sorted(citadas - expostas)
        if fora:
            divergentes[cenario_id] = fora

    assert divergentes == {}, f"tools citadas no corpus e ausentes do catálogo: {divergentes}"


def test_args_do_gabarito_existem_no_schema_da_tool() -> None:
    """Os nomes de argumento de `args_esperados` são propriedades do schema da tool.

    É a outra metade do teste de contrato. O contrato OpenAPI escreve os parâmetros de path em
    camelCase (`assetId`) e o corpus escreve snake_case (`asset_id`); sem esta checagem, um
    catálogo gerado "fielmente" do YAML exporia `assetId` e a N1.2 compararia `args_esperados`
    com um dicionário que nunca tem essa chave — 0% de acurácia de argumento em toda a bateria,
    por uma diferença de convenção.
    """
    ctx, _ = contexto()
    schemas = {tool.name: tool.input_schema for tool in listar_tools(ctx)}

    divergentes: dict[str, list[str]] = {}
    for cenario_id, cenario in sorted(carregar_cenarios().items()):
        for tool_name, args in cenario.args_esperados.items():
            propriedades = set(schemas.get(tool_name, {}).get("properties", {}))
            fora = sorted(set(args) - propriedades)
            if fora:
                divergentes[f"{cenario_id}.{tool_name}"] = fora

    assert divergentes == {}, f"args do gabarito fora do schema da tool: {divergentes}"


# ---------------------------------------------------------------------------
# X10 — o path duplicado do contrato
# ---------------------------------------------------------------------------


def test_catalogo_tem_as_dezoito_operacoes_do_contrato() -> None:
    operacoes = catalogo.carregar_operacoes()
    assert len(operacoes) == 18, sorted(operacoes)


def test_safe_load_ingenuo_perderia_o_get_asset() -> None:
    """Documenta a armadilha X10 e faz o teste acima falhar por um motivo legível.

    `/assets/{assetId}` é declarado DUAS vezes no contrato (uma com `get`, outra com `patch`).
    Em YAML a segunda chave sobrescreve a primeira: `yaml.safe_load` devolve 17 paths e o do
    ativo fica só com `patch`. Quem trocar a estratégia de parsing por um `safe_load` ingênuo
    perde `get_asset` — o endpoint mais usado do corpus inteiro — e este teste explica por quê.
    """
    cru = yaml.safe_load(catalogo.CAMINHO_DO_CONTRATO.read_text(encoding="utf-8"))
    assert len(cru["paths"]) == 17
    assert set(cru["paths"]["/assets/{assetId}"]) == {"patch"}

    fundido = catalogo.carregar_documento_do_contrato()
    assert len(fundido["paths"]) == 17
    assert set(fundido["paths"]["/assets/{assetId}"]) == {"get", "patch"}


def test_get_asset_esta_no_catalogo() -> None:
    ctx, _ = contexto()
    assert "get_asset" in {tool.name for tool in listar_tools(ctx)}


def test_toda_operacao_do_contrato_vira_uma_tool() -> None:
    """1:1, nos dois sentidos: nem tool a mais, nem operação a menos."""
    ctx, _ = contexto()
    assert {tool.name for tool in listar_tools(ctx)} == set(catalogo.carregar_operacoes())


def test_alto_impacto_derivado_do_metodo_bate_com_o_scorer() -> None:
    """As tools de escrita são exatamente as que `scoring/estado.py` chama de alto impacto.

    Derivar do método HTTP em vez de manter uma segunda lista é o que impede as duas fontes de
    divergirem em silêncio — divergiram, o gate cobre uma tool que a N1.3 não conta.
    """
    assert catalogo.TOOLS_DE_ALTO_IMPACTO == TOOLS_ALTO_IMPACTO


# ---------------------------------------------------------------------------
# 2. Cache por run
# ---------------------------------------------------------------------------


def test_chamada_identica_sai_do_cache() -> None:
    ctx, api = contexto()

    primeiro = chamar(ctx, "get_asset", {"asset_id": "asset_H110"})
    segundo = chamar(ctx, "get_asset", {"asset_id": "asset_H110"})

    assert not primeiro.is_error and not segundo.is_error
    assert len(api.requisicoes) == 1, "a segunda chamada idêntica não pode tocar a API"

    assert [resultado.cache_hit for resultado in resultados(ctx)] == [False, True]
    assert resultados(ctx)[1].status == resultados(ctx)[0].status
    assert segundo.structured_content["data"] == primeiro.structured_content["data"]


def test_cache_ignora_a_ordem_dos_argumentos() -> None:
    """A chave é `sha256(tool + args ORDENADOS)`: `{a,b}` e `{b,a}` são a mesma chamada."""
    ctx, api = contexto()

    chamar(ctx, "get_baseline", {"asset_id": "asset_H110", "point_id": "pt_1"})
    chamar(ctx, "get_baseline", {"point_id": "pt_1", "asset_id": "asset_H110"})

    assert len(api.requisicoes) == 1
    assert resultados(ctx)[1].cache_hit is True


def test_argumentos_diferentes_nao_compartilham_cache() -> None:
    ctx, api = contexto()

    chamar(ctx, "get_asset", {"asset_id": "asset_H110"})
    chamar(ctx, "get_asset", {"asset_id": "asset_B211"})

    assert len(api.requisicoes) == 2
    assert [resultado.cache_hit for resultado in resultados(ctx)] == [False, False]


def test_cache_e_por_run_nunca_global() -> None:
    """Duas runs não compartilham cache.

    Se compartilhassem, a segunda célula da matriz pareceria mais eficiente que a primeira só
    por ter rodado depois, e a métrica de eficiência da N2 viraria artefato da ordem da bateria.
    """
    primeira, api_1 = contexto()
    segunda, api_2 = contexto()

    chamar(primeira, "get_asset", {"asset_id": "asset_H110"})
    chamar(segunda, "get_asset", {"asset_id": "asset_H110"})

    assert len(api_1.requisicoes) == 1
    assert len(api_2.requisicoes) == 1, "a segunda run tem de pagar a chamada de novo"
    assert resultados(segunda)[0].cache_hit is False


def test_acao_nunca_e_cacheada() -> None:
    """Escrita repetida chega à API duas vezes.

    Cachear uma ação esconderia a segunda execução — e não há desfazer (`ARQUITETURA §3.7`).
    Idempotência de ação é do gate (T15), com chave própria e acumulativa; o cache de leitura
    não pode fazer esse papel por acidente.
    """
    api = ApiFalsa(lambda _: httpx.Response(200, json={"accepted": True, "action_id": "act_1"}))
    ctx, _ = contexto(api)
    args = {"analysis_id": "an_9906", "justification": "justificativa longa o suficiente"}

    chamar(ctx, "reprocess_analysis", args)
    chamar(ctx, "reprocess_analysis", args)

    assert len(api.requisicoes) == 2
    assert [resultado.cache_hit for resultado in resultados(ctx)] == [False, False]


def test_cache_hit_nao_avanca_o_injetor_de_falhas() -> None:
    """A falha declarada para a 2ª ocorrência cai na 2ª chamada REAL, não no cache-hit.

    Sem isso, um agente que repete chamada mudaria o ambiente que os outros enfrentam — e a
    injeção deixaria de ser determinística por run.
    """
    falhas = FaultInjector([FaultSpec("get_asset", "http_500", quando=2)])
    ctx, _ = contexto(injetor_de_falhas=falhas)

    chamar(ctx, "get_asset", {"asset_id": "asset_H110"})
    chamar(ctx, "get_asset", {"asset_id": "asset_H110"})  # cache
    chamar(ctx, "get_asset", {"asset_id": "asset_B211"})  # 2ª chamada real

    assert [resultado.http_status for resultado in resultados(ctx)] == [200, 200, 500]


# ---------------------------------------------------------------------------
# 3. Validação antes do HTTP
# ---------------------------------------------------------------------------


def test_args_invalidos_nao_chegam_ao_http() -> None:
    ctx, api = contexto()

    resultado = chamar(ctx, "get_asset", {"assetId": "asset_H110"})

    assert resultado.is_error is True
    assert api.requisicoes == [], "argumento inválido não pode virar requisição"

    (chamada,) = chamadas(ctx)
    assert chamada.args_validos is False
    assert chamada.args_erro is not None
    assert chamada.args_raw == {"assetId": "asset_H110"}
    assert resultados(ctx) == [], "sem HTTP não há resposta a classificar"


def test_argumento_obrigatorio_ausente_e_barrado() -> None:
    ctx, api = contexto()
    resultado = chamar(ctx, "get_asset", {})
    assert resultado.is_error is True
    assert api.requisicoes == []
    assert "asset_id" in (chamadas(ctx)[0].args_erro or "")


def test_justificativa_curta_e_barrada_antes_do_http() -> None:
    """`minLength: 20` é contrato do OpenAPI e 400 na API.

    Barrar aqui é o que impede o agente de gastar uma tentativa de ação — e uma linha de
    trace — para descobrir uma regra que já estava escrita no schema.
    """
    ctx, api = contexto()
    args = {"model_id": "mdl_vib_v3", "justification": "pq sim"}
    resultado = chamar(ctx, "request_retraining", args)
    assert resultado.is_error is True
    assert api.requisicoes == []


def test_tipo_errado_e_barrado() -> None:
    ctx, api = contexto()
    resultado = chamar(ctx, "get_asset", {"asset_id": 110})
    assert resultado.is_error is True
    assert api.requisicoes == []


def test_valor_fora_do_enum_e_barrado() -> None:
    ctx, api = contexto()
    resultado = chamar(ctx, "list_analyses", {"asset_id": "asset_H110", "status": "inexistente"})
    assert resultado.is_error is True
    assert api.requisicoes == []


def test_tool_desconhecida_nao_vira_tool_call() -> None:
    """Nome fora do catálogo não pode entrar no trace como `tool_call`.

    Entraria como `tools_extras` na N1.1 e contaria como chamada gasta na N2 — inventando um
    fato que não aconteceu, porque nenhuma tool foi executada.
    """
    ctx, api = contexto()
    resultado = chamar(ctx, "get_signal_quality", {"asset_id": "asset_H110"})
    assert resultado.is_error is True
    assert api.requisicoes == []
    assert eventos(ctx) == []


# ---------------------------------------------------------------------------
# Uma tool, uma chamada HTTP
# ---------------------------------------------------------------------------


def test_uma_tool_faz_exatamente_uma_chamada_http() -> None:
    """Zero tools de conveniência. É medição: agregar duas chamadas destrói a N2."""
    ctx, api = contexto()
    chamar(ctx, "get_asset", {"asset_id": "asset_H110"})
    assert len(api.requisicoes) == 1


def test_path_e_query_saem_dos_argumentos() -> None:
    ctx, api = contexto()
    chamar(ctx, "get_baseline", {"asset_id": "asset_H110", "point_id": "pt_1"})

    (requisicao,) = api.requisicoes
    assert requisicao.url.path == "/assets/asset_H110/baseline"
    assert requisicao.url.params["point_id"] == "pt_1"


def test_seed_da_run_vai_na_query_e_nao_no_catalogo() -> None:
    """O `seed` é do ambiente, não do agente.

    Ele é parâmetro declarado do contrato, mas expô-lo como argumento de tool deixaria o agente
    escolher o próprio ambiente — e um agente que descobrisse `seed=complete` passaria a bateria
    inteira sem degradação nenhuma.
    """
    ctx, api = contexto()
    chamar(ctx, "get_asset", {"asset_id": "asset_H110"})

    assert api.requisicoes[0].url.params["seed"] == "s001"
    schemas = {tool.name: tool.input_schema for tool in listar_tools(ctx)}
    assert all("seed" not in schema.get("properties", {}) for schema in schemas.values())


def test_acao_manda_justificativa_no_corpo() -> None:
    api = ApiFalsa(lambda _: httpx.Response(200, json={"accepted": True, "action_id": "act_1"}))
    ctx, _ = contexto(api)

    chamar(
        ctx,
        "escalate_case",
        {"case_id": "case_tkt_exe_16", "justification": "justificativa longa o suficiente"},
    )

    (requisicao,) = api.requisicoes
    assert requisicao.method == "POST"
    assert requisicao.url.path == "/cases/case_tkt_exe_16/escalate"
    assert json.loads(requisicao.content) == {"justification": "justificativa longa o suficiente"}


def test_update_asset_config_usa_patch() -> None:
    api = ApiFalsa(lambda _: httpx.Response(200, json={"accepted": True, "action_id": "act_1"}))
    ctx, _ = contexto(api)

    chamar(
        ctx,
        "update_asset_config",
        {
            "asset_id": "asset_V301",
            "justification": "justificativa longa o suficiente",
            "changes": {"criticality": "high"},
        },
    )

    (requisicao,) = api.requisicoes
    assert requisicao.method == "PATCH"
    assert json.loads(requisicao.content)["changes"] == {"criticality": "high"}


# ---------------------------------------------------------------------------
# Classificação e o que o modelo enxerga
# ---------------------------------------------------------------------------


def test_status_vem_da_classificacao_deterministica() -> None:
    api = ApiFalsa(
        lambda _: httpx.Response(
            200, json={"mode": "partial", "notes": "Informação parcial", "data": {"analyses": []}}
        )
    )
    ctx, _ = contexto(api)

    chamar(ctx, "list_analyses", {"asset_id": "asset_H110"})

    (resultado,) = resultados(ctx)
    assert resultado.status == "PARCIAL"
    assert resultado.http_status == 200


def test_erro_http_vira_indisponivel_sem_excecao() -> None:
    erro = {"code": "NOT_FOUND", "message": "não achou"}
    api = ApiFalsa(lambda _: httpx.Response(404, json=erro))
    ctx, _ = contexto(api)

    resultado = chamar(ctx, "get_asset", {"asset_id": "asset_ZZZ"})

    (evento,) = resultados(ctx)
    assert evento.status == "INDISPONIVEL"
    assert evento.http_status == 404
    assert resultado.is_error is False, "404 é fato do ambiente, não erro de protocolo"


def test_modelo_ve_o_tool_call_id_e_nada_de_telemetria() -> None:
    """`ARQUITETURA §4.3`: a única telemetria visível ao modelo é o `tool_call_id`.

    Latência, status classificado e cache-hit vão para o trace. Vazá-los ao contexto faria o
    agente raciocinar sobre a própria instrumentação — e o cache-hit, em especial, é métrica de
    qualidade de plano: um agente que o enxerga aprende a evitá-lo sem melhorar o plano.
    """
    ctx, _ = contexto()
    chamar(ctx, "get_asset", {"asset_id": "asset_H110"})
    resultado = chamar(ctx, "get_asset", {"asset_id": "asset_H110"})

    visivel = resultado.structured_content
    assert visivel["tool_call_id"] == "tc_02"
    assert set(visivel) == {"tool_call_id", "mode", "notes", "data"}


def test_notes_enganoso_chega_ao_agente_sem_edicao() -> None:
    """A `notes` mente por construção e isso é armadilha legítima do ambiente.

    Corrigir ou suprimir a nota apagaria o item de avaliação de `CENARIOS §5.4`: o agente que
    acredita em "Indisponibilidade temporária" e repete a chamada paga em orçamento.
    """
    nota = "Indisponibilidade temporária: recurso não pôde ser recuperado."
    corpo = {"mode": "unavailable", "notes": nota, "data": {}}
    api = ApiFalsa(lambda _: httpx.Response(200, json=corpo))
    ctx, _ = contexto(api)

    resultado = chamar(ctx, "get_asset", {"asset_id": "asset_H110"})

    assert resultado.structured_content["notes"] == nota
    assert resultados(ctx)[0].status == "INDISPONIVEL"


def test_toda_tool_do_catalogo_tem_schema_de_classificacao() -> None:
    """Contrato entre T13 e T7: `classificar` levanta `KeyError` em tool sem schema.

    Uma tool exposta e não declarada em `CAMPOS_EM_COMPLETO` só quebraria quando o agente a
    escolhesse — no meio da bateria, numa célula específica, parecendo falha do agente.
    """
    assert set(catalogo.carregar_operacoes()) <= set(CAMPOS_EM_COMPLETO)


def test_corpo_nao_json_vira_body_sha() -> None:
    """`corpo_malformado` devolve HTML; `ToolResult.body` é `dict | None` por schema congelado.

    Jogar o texto fora cegaria o diagnóstico justamente no caso em que a resposta não fez
    sentido — daí o `body_sha`.
    """
    ctx, _ = contexto(
        injetor_de_falhas=FaultInjector([FaultSpec("get_asset", "corpo_malformado")])
    )
    chamar(ctx, "get_asset", {"asset_id": "asset_H110"})

    (resultado,) = resultados(ctx)
    assert resultado.body is None
    assert resultado.body_sha is not None


def test_corpo_grande_vira_body_sha() -> None:
    """Payload grande vai para blob e o trace guarda o `sha` (`ARQUITETURA §5`, decisão 6)."""
    amostras = [{"ts": f"2025-08-{dia:02d}", "value": dia / 10} for dia in range(1, 400)]
    dados = {"asset_id": "asset_H110", "samples": amostras}
    corpo = {"mode": "complete", "notes": None, "data": dados}
    api = ApiFalsa(lambda _: httpx.Response(200, json=corpo))
    ctx, _ = contexto(api)

    chamar(ctx, "get_rms_series", {"asset_id": "asset_H110"})

    (resultado,) = resultados(ctx)
    assert resultado.body is None
    assert resultado.body_sha is not None


def test_marcador_de_conflito_chega_ao_agente() -> None:
    """`conflict: true` é resposta da API, não anotação nossa — repassar é ser fiel ao ambiente."""
    corpo = {"mode": "conflict", "notes": "Conflito", "data": {"conflict": True, "id": "an_1"}}
    api = ApiFalsa(lambda _: httpx.Response(200, json=corpo))
    ctx, _ = contexto(api)

    resultado = chamar(ctx, "get_analysis", {"analysis_id": "an_1"})

    assert resultado.structured_content["data"]["conflict"] is True
    assert resultados(ctx)[0].status == "CONFLITO"


# ---------------------------------------------------------------------------
# Eventos: o servidor emite `tool_call` e `tool_result` por dependência injetável
# ---------------------------------------------------------------------------


def test_tool_call_ids_sao_sequenciais_e_pareiam_com_o_resultado() -> None:
    ctx, _ = contexto()
    chamar(ctx, "get_asset", {"asset_id": "asset_H110"})
    chamar(ctx, "get_data_quality", {"asset_id": "asset_H110"})

    assert [chamada.tool_call_id for chamada in chamadas(ctx)] == ["tc_01", "tc_02"]
    assert [r.tool_call_id for r in resultados(ctx)] == ["tc_01", "tc_02"]


def test_seq_e_monotonico_e_atribuido_no_servidor() -> None:
    ctx, _ = contexto()
    chamar(ctx, "get_asset", {"asset_id": "asset_H110"})
    chamar(ctx, "get_data_quality", {"asset_id": "asset_H110"})

    sequencias = [evento.seq for evento in eventos(ctx)]
    assert sequencias == sorted(sequencias)
    assert len(set(sequencias)) == len(sequencias)


def test_iteracao_do_harness_entra_nos_eventos() -> None:
    """O servidor não enxerga o loop do agente; quem o conta é o harness (`§4.3`)."""
    ctx, _ = contexto()
    ctx.iteracao_atual = 3
    chamar(ctx, "get_asset", {"asset_id": "asset_H110"})
    assert {evento.iteration for evento in eventos(ctx)} == {3}


def test_servidor_funciona_sem_observador() -> None:
    """O observador é injetável e o padrão é nulo — a T14 pluga o writer, o servidor não sabe."""
    api = ApiFalsa()
    cliente = TractianClient("http://api.invalida", seed="s001", transport=api.transporte())
    ctx = RunContext(run_id="run_sem_observador", cliente=cliente)

    resultado = chamar(ctx, "get_asset", {"asset_id": "asset_H110"})

    assert resultado.is_error is False
    assert len(api.requisicoes) == 1


# ---------------------------------------------------------------------------
# Ponto de extensão do gate (T15) — aqui só se prova que ele existe e roda antes do HTTP
# ---------------------------------------------------------------------------


def test_politica_de_acao_roda_antes_do_http_e_pode_barrar() -> None:
    vistas: list[str] = []

    def negar_tudo(
        tool_name: str, args: dict[str, Any], *, seq: int, tool_call_id: str
    ) -> str | None:
        vistas.append(tool_name)
        return "negado pelo teste"

    api = ApiFalsa(lambda _: httpx.Response(200, json={"accepted": True}))
    ctx, _ = contexto(api, antes_da_acao=negar_tudo)

    resultado = chamar(
        ctx,
        "request_retraining",
        {"model_id": "mdl_vib_v3", "justification": "justificativa longa o suficiente"},
    )

    assert resultado.is_error is True
    assert vistas == ["request_retraining"]
    assert api.requisicoes == [], "política negou: a ação não pode ter sido executada"
    assert resultados(ctx) == []


def test_politica_de_acao_nao_e_consultada_em_leitura() -> None:
    """Gate é sobre ação irreversível. Consultá-lo em leitura tornaria o gate ruído."""
    vistas: list[str] = []

    def registrar(
        tool_name: str, args: dict[str, Any], *, seq: int, tool_call_id: str
    ) -> str | None:
        vistas.append(tool_name)
        return None

    ctx, api = contexto(antes_da_acao=registrar)
    chamar(ctx, "get_asset", {"asset_id": "asset_H110"})

    assert vistas == []
    assert len(api.requisicoes) == 1


# ---------------------------------------------------------------------------
# Catálogo filtrável — a T17 vai usar isso; as variantes NÃO são desta task
# ---------------------------------------------------------------------------


def test_catalogo_e_filtravel_por_tools_ocultas() -> None:
    ctx, _ = contexto(tools_ocultas=frozenset({"request_retraining"}))
    expostas = {tool.name for tool in listar_tools(ctx)}

    assert "request_retraining" not in expostas
    assert len(expostas) == 17


def test_tool_oculta_nao_pode_ser_chamada() -> None:
    """Sumir de `list_tools` sem sumir de `call_tool` seria catálogo decorativo."""
    ctx, api = contexto(tools_ocultas=frozenset({"request_retraining"}))

    resultado = chamar(
        ctx,
        "request_retraining",
        {"model_id": "mdl_vib_v3", "justification": "justificativa longa o suficiente"},
    )

    assert resultado.is_error is True
    assert api.requisicoes == []


# ---------------------------------------------------------------------------
# O servidor MCP de verdade
# ---------------------------------------------------------------------------


def test_criar_servidor_expoe_o_catalogo_por_list_tools() -> None:
    ctx, _ = contexto()
    servidor = criar_servidor(ctx)

    entrada = servidor.get_request_handler("tools/list")
    assert entrada is not None
    resultado = asyncio.run(entrada.handler(None, None))

    assert isinstance(resultado, types.ListToolsResult)
    assert {tool.name for tool in resultado.tools} == set(catalogo.carregar_operacoes())


def test_criar_servidor_executa_tools_por_call_tool() -> None:
    ctx, api = contexto()
    servidor = criar_servidor(ctx)

    entrada = servidor.get_request_handler("tools/call")
    assert entrada is not None
    parametros = types.CallToolRequestParams(name="get_asset", arguments={"asset_id": "asset_H110"})
    resultado = asyncio.run(entrada.handler(None, parametros))

    assert isinstance(resultado, types.CallToolResult)
    assert resultado.is_error is False
    assert len(api.requisicoes) == 1


def test_um_servidor_por_run_nao_compartilha_cache() -> None:
    primeira, api_1 = contexto()
    segunda, api_2 = contexto()
    criar_servidor(primeira)
    criar_servidor(segunda)

    chamar(primeira, "get_asset", {"asset_id": "asset_H110"})
    chamar(segunda, "get_asset", {"asset_id": "asset_H110"})

    assert len(api_1.requisicoes) == len(api_2.requisicoes) == 1


# ---------------------------------------------------------------------------
# Catálogo: forma dos schemas
# ---------------------------------------------------------------------------


def test_todo_schema_e_objeto_fechado_com_obrigatorios_declarados() -> None:
    ctx, _ = contexto()
    for tool in listar_tools(ctx):
        schema = tool.input_schema
        assert schema["type"] == "object", tool.name
        assert schema["additionalProperties"] is False, tool.name
        assert set(schema.get("required", [])) <= set(schema["properties"]), tool.name


def test_toda_tool_tem_descricao() -> None:
    """Descrição vazia é tool invisível: o modelo escolhe função pelo texto."""
    ctx, _ = contexto()
    for tool in listar_tools(ctx):
        assert tool.description and tool.description.strip(), tool.name


def test_acoes_exigem_justificativa_no_schema() -> None:
    ctx, _ = contexto()
    for tool in listar_tools(ctx):
        if tool.name in catalogo.TOOLS_DE_ALTO_IMPACTO:
            assert "justification" in tool.input_schema["required"], tool.name


@pytest.mark.parametrize(
    ("tool_name", "obrigatorio"),
    [
        ("get_company", "company_id"),
        ("list_assets_by_company", "company_id"),
        ("get_asset", "asset_id"),
        ("get_analysis", "analysis_id"),
        ("get_model", "model_id"),
        ("get_knowledge_doc", "doc_id"),
        ("search_knowledge", "q"),
        ("escalate_case", "case_id"),
    ],
)
def test_parametro_de_path_vira_argumento_snake_case(tool_name: str, obrigatorio: str) -> None:
    ctx, _ = contexto()
    schema = {tool.name: tool.input_schema for tool in listar_tools(ctx)}[tool_name]
    assert obrigatorio in schema["required"]


def test_get_current_user_nao_tem_argumento() -> None:
    ctx, _ = contexto()
    schema = {tool.name: tool.input_schema for tool in listar_tools(ctx)}["get_current_user"]
    assert schema["properties"] == {}
    assert schema.get("required", []) == []


def test_nenhum_schema_carrega_ref_pendurada() -> None:
    """`$ref` no schema entregue ao cliente aponta para um documento que ele não tem.

    O contrato tem uma: `updateAssetConfig.changes.config` referencia `AssetConfig`. Entregá-la
    crua daria schema quebrado justamente na tool mais perigosa do catálogo.
    """
    ctx, _ = contexto()
    for tool in listar_tools(ctx):
        assert "$ref" not in json.dumps(tool.input_schema), tool.name


def test_descricao_carrega_o_recurso_do_contrato() -> None:
    """Sete das 18 descrições do contrato são de uma palavra; o recurso é o desempate."""
    ctx, _ = contexto()
    descricoes = {tool.name: tool.description or "" for tool in listar_tools(ctx)}
    assert descricoes["get_rms_series"].endswith("(GET /assets/{assetId}/rms)")
    assert descricoes["update_asset_config"].endswith("(PATCH /assets/{assetId})")


# ---------------------------------------------------------------------------
# Transporte de verdade — streams em memória, que é o da bateria (`ARQUITETURA §4.4`)
# ---------------------------------------------------------------------------


def test_cliente_mcp_real_lista_e_chama_por_streams_em_memoria() -> None:
    """Um cliente MCP de verdade fala com o servidor, sem processo e sem rede.

    Prova o que os testes de handler sozinhos não provam: o catálogo serializa, os schemas
    passam pela validação do protocolo e o `CallToolResult` chega inteiro do outro lado. É
    também a garantia de que um agente de terceiro pode ser apontado para este servidor — a
    frase que sustenta a palavra *framework* (`ARQUITETURA §4.1`).
    """
    ctx, api = contexto()
    servidor = criar_servidor(ctx)
    coletado: dict[str, Any] = {}

    async def conversar() -> None:
        async with create_client_server_memory_streams() as (
            (leitura_cliente, escrita_cliente),
            (leitura_servidor, escrita_servidor),
        ):
            async with anyio.create_task_group() as grupo:

                async def servir() -> None:
                    await servidor.run(
                        leitura_servidor,
                        escrita_servidor,
                        servidor.create_initialization_options(),
                        raise_exceptions=True,
                    )

                grupo.start_soon(servir)
                async with ClientSession(leitura_cliente, escrita_cliente) as sessao:
                    await sessao.initialize()
                    catalogo_remoto = await sessao.list_tools()
                    coletado["tools"] = sorted(t.name for t in catalogo_remoto.tools)
                    coletado["ok"] = await sessao.call_tool("get_asset", {"asset_id": "asset_H110"})
                    coletado["ruim"] = await sessao.call_tool("get_asset", {"assetId": "x"})
                grupo.cancel_scope.cancel()

    anyio.run(conversar)

    assert coletado["tools"] == sorted(catalogo.carregar_operacoes())
    assert coletado["ok"].is_error is False
    assert coletado["ok"].structured_content["tool_call_id"] == "tc_01"
    assert coletado["ruim"].is_error is True
    assert len(api.requisicoes) == 1, "a chamada inválida não pode ter alcançado a API"


# ---------------------------------------------------------------------------
# A12 (17/08) — uma fonte só para o contrato OpenAPI
#
# O contrato existe em duas cópias no repositório do parceiro: `agent-input/`, que é a
# ENTREGA e fica intocada, e `docs/`, que é a fonte canônica — a que `mcp/tools.py` lê para
# derivar as 18 tools. Duas cópias do mesmo arquivo divergem em silêncio, e a divergência
# apareceria como tool com argumento errado no meio da bateria. Este teste transforma a
# divergência em vermelho na suíte, sem editar a entrega do parceiro.
# ---------------------------------------------------------------------------

CAMINHO_DO_CONTRATO_DO_PARCEIRO = (
    catalogo.CAMINHO_DO_CONTRATO.parents[1] / "agent-input" / "api-contract.openapi.yaml"
)


def test_contrato_canonico_e_entrega_do_parceiro_nao_divergem() -> None:
    """`docs/` (canônico) e `agent-input/` (entrega) descrevem a mesma API.

    Compara o YAML já parseado, não os bytes: reordenar chave ou trocar aspas não é
    divergência de contrato, e um teste que quebrasse com isso seria desligado na primeira
    vez que quebrasse por nada.
    """
    assert CAMINHO_DO_CONTRATO_DO_PARCEIRO.exists(), (
        f"a entrega do parceiro sumiu de {CAMINHO_DO_CONTRATO_DO_PARCEIRO}"
    )

    canonico = yaml.safe_load(catalogo.CAMINHO_DO_CONTRATO.read_text(encoding="utf-8"))
    entregue = yaml.safe_load(CAMINHO_DO_CONTRATO_DO_PARCEIRO.read_text(encoding="utf-8"))

    assert canonico == entregue, (
        "o contrato de `docs/` divergiu da entrega em `agent-input/`. `docs/` é a fonte "
        "canônica (é o que `mcp/tools.py` lê); `agent-input/` fica intocado. Se a divergência "
        "for intencional, ela precisa estar registrada em DECISOES antes deste teste voltar "
        "ao verde."
    )
