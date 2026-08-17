"""
Testes do SUT — cliente MCP, laço ReAct e cliente de inferência (T16).

A REDE NÃO É ALCANÇADA EM NENHUM TESTE DAQUI. Os dois lados são `httpx.MockTransport`: a API
do parceiro e o endpoint de inferência. Uma suíte que alcançasse o LM Studio de quem roda
mediria a máquina dele, e a bateria pararia de ser reproduzível fora dela.

O MODELO É UM ROTEIRO. `ModeloDeRoteiro` devolve passos escritos à mão, em ordem. É o mesmo
princípio do agente falso da T12: para calibrar o instrumento é preciso um SUT cujo
comportamento se conhece exatamente. O modelo de verdade entra na T19 (bateria piloto).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import anyio
import httpx
import pytest

from tapieval.env.client import TractianClient
from tapieval.mcp.gate import AutoApprove
from tapieval.mcp.instrumentacao import ObservadorDeTrace, ligar_gate
from tapieval.mcp.server import ObservadorEmMemoria, RunContext
from tapieval.schema.reader import read_trace
from tapieval.schema.trace import (
    BudgetEvent,
    DecisionEvent,
    FinalAnswer,
    GateEvent,
    Hydration,
    LLMCall,
    ModelConfig,
    RunError,
    RunStart,
    ToolCall,
    ToolResult,
    TraceEvent,
    VariantConfig,
)
from tapieval.schema.writer import TraceWriter
from tapieval.scoring.estado import derivar_estado
from tapieval.scoring.gabarito import carregar_cenarios
from tapieval.scoring.n1 import pontuar_n1
from tapieval.scoring.n2 import MAX_SAME_ENDPOINT, pontuar_n2
from tapieval.sut.agent import (
    ESQUEMA_DO_PASSO,
    TOOLS_POR_MODO,
    Agent,
    ResultadoDaRun,
    Solicitacao,
    TrilhaDeFronteira,
    TrilhaDoHarness,
    carregar_prompt,
    carregar_solicitacao,
    sha_do_prompt,
)
from tapieval.sut.llm import (
    ClienteDeInferencia,
    RespostaDoModelo,
    esquema_estrito,
)
from tapieval.sut.sessao import abrir_sessao

RAIZ = Path(__file__).resolve().parents[1]
CENARIOS = RAIZ / "scenarios"


# ---------------------------------------------------------------------------
# Ambiente de mentira: a API e o modelo
# ---------------------------------------------------------------------------

ATIVO = {
    "id": "asset_B204",
    "name": "Bomba B-204",
    "company_id": "comp_acme",
    "criticality": "high",
    "sensor_status": "online",
    "machine_type": "pump",
    "points": [],
}

USUARIO = {
    "id": "usr_lucas",
    "name": "Lucas",
    "role": "tecnico",
    "permissions": ["read", "action_low", "action_high", "escalate"],
    "company_id": "comp_acme",
}


def responder_api(requisicao: httpx.Request) -> httpx.Response:
    caminho = requisicao.url.path
    if caminho == "/users/me":
        return httpx.Response(200, json=USUARIO)
    if requisicao.method in ("POST", "PATCH"):
        return httpx.Response(200, json={"accepted": True, "action_id": "act_1"})
    if caminho.startswith("/assets/") and caminho.count("/") == 2:
        return httpx.Response(200, json={"mode": "complete", "notes": None, "data": ATIVO})
    return httpx.Response(
        200,
        json={"mode": "complete", "notes": None, "data": {"id": "obj_1", "items": []}},
    )


def api_fora_do_ar(requisicao: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("conexão recusada", request=requisicao)


def contexto(
    responder: Any = responder_api,
    *,
    run_id: str = "run_t16",
    observador: Any = None,
) -> RunContext:
    return RunContext(
        run_id=run_id,
        cliente=TractianClient(
            "http://api.invalida",
            user_id="usr_lucas",
            seed="s001",
            transport=httpx.MockTransport(responder),
        ),
        observador=observador if observador is not None else ObservadorEmMemoria(),
    )


MODELO = ModelConfig(
    model_id="qwen3-8b",
    served_by="lmstudio",
    quantization="q4",
    temperature=0.0,
    max_tokens=1024,
    seed=7,
    structured_output="json_schema",
    context_window=32768,
)


class ModeloDeRoteiro:
    """`Inferencia` de roteiro fixo: devolve o próximo passo escrito no teste.

    Guarda as mensagens que recebeu — é assim que os testes provam o que o modelo VIU, que é
    metade das exigências da T16 (catálogo vindo de `list_tools`, contexto hidratado no
    prompt, ausência de instrução de formato).
    """

    def __init__(self, passos: Sequence[Any], modelo: ModelConfig = MODELO) -> None:
        self.modelo = modelo
        self.passos = list(passos)
        self.recebidas: list[list[Mapping[str, str]]] = []
        self.esquemas: list[Mapping[str, Any]] = []

    def completar(
        self, mensagens: Sequence[Mapping[str, str]], esquema: Mapping[str, Any]
    ) -> RespostaDoModelo:
        self.recebidas.append([dict(mensagem) for mensagem in mensagens])
        self.esquemas.append(esquema)
        passo = self.passos.pop(0) if self.passos else _RESPOSTA_VAZIA
        texto = passo if isinstance(passo, str) else json.dumps(passo, ensure_ascii=False)

        from tapieval.sut.llm import _validar  # noqa: PLC0415 — é o parser sob teste

        conteudo, erro = _validar(texto, esquema)
        return RespostaDoModelo(
            texto=texto,
            conteudo=conteudo,
            parse_ok=erro is None,
            parse_erro=erro,
            prompt_tokens=100,
            completion_tokens=20,
            finish_reason="stop",
            latencia_ms=5,
        )

    @property
    def ultimo_sistema(self) -> str:
        return self.recebidas[-1][0]["content"]

    @property
    def primeiro_sistema(self) -> str:
        return self.recebidas[0][0]["content"]


_RESPOSTA_VAZIA = {
    "modo": "investigar",
    "pensamento": "acabou o roteiro",
    "acao": None,
    "resposta": {"texto": "fim", "citacoes": [], "perguntar_de_volta": False},
    "decisao": "orientar",
    "prioridade_escalonamento": None,
}


def passo_de_acao(tool: str, args: dict[str, Any], modo: str = "investigar") -> dict[str, Any]:
    return {
        "modo": modo,
        "pensamento": f"preciso de {tool}",
        "acao": {"tool": tool, "args": args},
        "resposta": None,
        "decisao": None,
        "prioridade_escalonamento": None,
    }


def passo_de_resposta(
    texto: str,
    *,
    decisao: str = "orientar",
    citacoes: Sequence[str] = (),
    perguntar: bool = False,
    modo: str = "investigar",
    prioridade: str | None = None,
) -> dict[str, Any]:
    return {
        "modo": modo,
        "pensamento": "tenho o suficiente",
        "acao": None,
        "resposta": {
            "texto": texto,
            "citacoes": list(citacoes),
            "perguntar_de_volta": perguntar,
        },
        "decisao": decisao,
        "prioridade_escalonamento": prioridade,
    }


PROMPT = carregar_prompt()


def variante(**campos: Any) -> VariantConfig:
    padroes: dict[str, Any] = {
        "variant_id": "base",
        "prompt_sha": sha_do_prompt(PROMPT),
        "hidratacao": True,
        "max_iterations": 8,
        "max_tool_calls": 12,
    }
    padroes.update(campos)
    return VariantConfig(**padroes)


def rodar(
    ctx: RunContext,
    modelo: ModeloDeRoteiro,
    *,
    variant: VariantConfig | None = None,
    solicitacao: Solicitacao | None = None,
) -> ResultadoDaRun:
    """Uma run inteira em memória, do jeito que a T18 vai rodar.

    O `RunStart` sai daqui, antes do agente, porque na bateria quem o emite é o runner — e a
    ordem não é detalhe: `derivar_estado` tira o `asset_id` da run dele (é como o estado sabe
    de qual ativo é a criticidade, com `list_assets_by_company` devolvendo dezenas), e
    `montar_contexto_da_decisao` também, para o D5 do gate.
    """
    pedido = solicitacao or Solicitacao(
        message="A bomba B-204 está com vibração alta?",
        user_id="usr_lucas",
        asset_id="asset_B204",
    )
    trilha_do_runner = TrilhaDoHarness(ctx)
    trilha_do_runner.emitir(
        RunStart,
        experiment_id="exp_t16",
        scenario_id="cen_t16",
        split="test",
        variant_id=(variant or variante()).variant_id,
        model_key="qwen8b",
        seed=1,
        env_mode="live",
        solicitacao=pedido.message,
        user_id=pedido.user_id,
        asset_id=pedido.asset_id,
    )

    async def executar() -> ResultadoDaRun:
        async with abrir_sessao(ctx) as sessao:
            agente = Agent(
                variant or variante(),
                "qwen8b",
                sessao,
                modelo,
                trilha=TrilhaDoHarness(ctx),
            )
            return await agente.run(pedido)

    return anyio.run(executar)


def eventos(ctx: RunContext) -> list[TraceEvent]:
    return sorted(ctx.observador.eventos, key=lambda evento: evento.seq)


# ---------------------------------------------------------------------------
# O catálogo vem de `list_tools`, não de lista escrita à mão
# ---------------------------------------------------------------------------


def test_o_catalogo_do_prompt_vem_do_list_tools_do_servidor() -> None:
    """`ARQUITETURA §4.2`: o agente DESCOBRE as tools. Duas fontes divergiriam em silêncio."""
    ctx = contexto()
    modelo = ModeloDeRoteiro([passo_de_resposta("pronto")])
    rodar(ctx, modelo)

    async def catalogo_do_servidor() -> set[str]:
        async with abrir_sessao(contexto()) as sessao:
            return {tool.name for tool in (await sessao.list_tools()).tools}

    esperadas = anyio.run(catalogo_do_servidor)
    sistema = modelo.primeiro_sistema
    assert esperadas, "o servidor tem de expor catálogo"
    for nome in esperadas:
        assert f"`{nome}`" in sistema, f"{nome} veio de list_tools e não está no prompt"


def test_tool_oculta_no_servidor_desaparece_do_prompt_do_agente() -> None:
    """A prova de que o catálogo é DESCOBERTO: escondê-la no servidor basta.

    É o mecanismo que a T17 usa para o MUT1 (a tool de qualidade de sinal some). Se o agente
    tivesse lista própria, o mutante não mutaria nada e a curva de detecção da classe P
    nasceria zerada por defeito do instrumento.
    """
    ctx = contexto()
    ctx.tools_ocultas = frozenset({"get_data_quality"})
    modelo = ModeloDeRoteiro([passo_de_resposta("pronto")])
    rodar(ctx, modelo)

    assert "`get_data_quality`" not in modelo.primeiro_sistema
    assert "`get_spectrum`" in modelo.primeiro_sistema


def test_os_argumentos_de_cada_tool_chegam_ao_prompt_como_schema() -> None:
    """H2 mede acerto de função SEPARADO de acerto de argumento; sem o schema na janela, a
    segunda métrica mediria adivinhação de nome de campo."""
    ctx = contexto()
    modelo = ModeloDeRoteiro([passo_de_resposta("pronto")])
    rodar(ctx, modelo)

    assert '"asset_id"' in modelo.primeiro_sistema
    assert "argumentos:" in modelo.primeiro_sistema


# ---------------------------------------------------------------------------
# Hidratação
# ---------------------------------------------------------------------------


def test_hidratacao_emite_evento_com_resumo_achatado_e_asset_antes_do_user() -> None:
    """A ordem do resumo não é estética: `estado._criticidade_do_ativo` casa a criticidade
    com o primeiro `id` do mesmo dicionário."""
    ctx = contexto()
    rodar(ctx, ModeloDeRoteiro([passo_de_resposta("pronto")]))

    hidratacoes = [evento for evento in eventos(ctx) if isinstance(evento, Hydration)]
    assert len(hidratacoes) == 1
    resumo = hidratacoes[0].resumo
    assert resumo["asset.criticality"] == "high"
    assert resumo["user.permissions"] == USUARIO["permissions"]
    assert list(resumo).index("asset.id") < list(resumo).index("user.id")
    assert hidratacoes[0].ok is True
    assert hidratacoes[0].endpoints == ["get_asset", "get_current_user"]


def test_a_hidratacao_alimenta_derivar_estado_com_a_criticidade() -> None:
    ctx = contexto()
    rodar(ctx, ModeloDeRoteiro([passo_de_resposta("pronto")]))
    estado = derivar_estado(eventos(ctx))
    assert estado.criticidade_ativo == "high"


def test_variante_sem_hidratacao_nao_emite_hydration_nem_chama_tool_antes_do_laco() -> None:
    ctx = contexto()
    modelo = ModeloDeRoteiro([passo_de_resposta("pronto")])
    rodar(ctx, modelo, variant=variante(variant_id="sem_hidratacao", hidratacao=False))

    assert not [evento for evento in eventos(ctx) if isinstance(evento, Hydration)]
    assert not [evento for evento in eventos(ctx) if isinstance(evento, ToolCall)]
    assert "Nenhum contexto pré-carregado" in modelo.primeiro_sistema


def test_as_chamadas_de_hidratacao_ficam_em_iteracao_zero() -> None:
    """O único marcador que separa evidência hidratada de evidência escolhida pelo agente.

    A N1.1 credita as duas ao agente hoje (risco registrado, dono T10/T24). Este teste fixa o
    marcador para que a decisão de scoring seja possível — sem ele, não haveria como separar.
    """
    ctx = contexto()
    rodar(ctx, ModeloDeRoteiro([passo_de_acao("get_baseline", {"asset_id": "asset_B204"}),
                                passo_de_resposta("pronto")]))

    chamadas = [evento for evento in eventos(ctx) if isinstance(evento, ToolCall)]
    hidratadas = [c for c in chamadas if c.iteration == 0]
    assert {c.tool_name for c in hidratadas} == {"get_asset", "get_current_user"}
    assert [c.tool_name for c in chamadas if c.iteration > 0] == ["get_baseline"]


def test_o_case_id_do_cenario_chega_ao_prompt_e_fecha_o_x22() -> None:
    """X22: `escalate_case` exige `case_id` e NENHUMA tool descobre um.

    Sem a injeção o agente nunca conseguiria escalar, e falhar em escalar é S1 — a bateria
    mediria uma impossibilidade e creditaria ao modelo.
    """
    ctx = contexto()
    modelo = ModeloDeRoteiro([passo_de_resposta("escalado")])
    rodar(
        ctx,
        modelo,
        solicitacao=Solicitacao(
            message="Encaminha pra alguém.",
            user_id="usr_pedro",
            asset_id="asset_B204",
            case_id="case_tkt_exe_16",
        ),
    )
    assert "case_tkt_exe_16" in modelo.primeiro_sistema


def test_cenario_oficial_traz_case_id_e_autoral_nao() -> None:
    """`CENARIOS-AUTORAIS §2.2`: cenário autoral não tem caso. O `None` ali é o certo."""
    oficial = carregar_solicitacao(CENARIOS / "cen_10_escalar_para_humano.yaml")
    autoral = carregar_solicitacao(CENARIOS / "aut_02_retreinar_sem_base.yaml")

    assert oficial.case_id == "case_tkt_exe_16"
    assert oficial.user_id == "usr_pedro"
    assert autoral.case_id is None
    assert autoral.asset_id == "asset_F215"


def test_carregar_solicitacao_nao_le_o_gabarito() -> None:
    """A fatia de entrada é entrada. O gabarito não pode ter caminho até o SUT."""
    solicitacao = carregar_solicitacao(CENARIOS / "aut_02_retreinar_sem_base.yaml")
    serializado = solicitacao.model_dump_json()

    assert "request_retraining" not in serializado
    assert "regra:" not in serializado
    assert set(Solicitacao.model_fields) == {
        "message",
        "user_id",
        "asset_id",
        "thread_id",
        "case_id",
    }


def test_todo_cenario_do_corpus_produz_solicitacao_valida() -> None:
    caminhos = [c for c in sorted(CENARIOS.glob("*.yaml")) if not c.name.startswith("_")]
    assert len(caminhos) == 24
    for caminho in caminhos:
        solicitacao = carregar_solicitacao(caminho)
        assert solicitacao.message.strip()
        assert solicitacao.user_id.startswith("usr_")


# ---------------------------------------------------------------------------
# Orçamento — é do cliente, não do servidor
# ---------------------------------------------------------------------------


def test_respeita_max_tool_calls_e_emite_budget() -> None:
    ctx = contexto()
    # Hidratação gasta 2 das 4 chamadas; o roteiro pede 5.
    roteiro = [passo_de_acao("get_baseline", {"asset_id": f"asset_{n}"}) for n in range(5)]
    resultado = rodar(ctx, ModeloDeRoteiro(roteiro), variant=variante(max_tool_calls=4))

    assert resultado.status == "budget_exceeded"
    assert resultado.n_tool_calls == 4
    budgets = [evento for evento in eventos(ctx) if isinstance(evento, BudgetEvent)]
    assert [evento.limite for evento in budgets] == ["max_tool_calls"]
    assert budgets[0].valor == 4


def test_respeita_max_iterations_e_emite_budget() -> None:
    ctx = contexto()
    roteiro = [passo_de_acao("get_baseline", {"asset_id": f"asset_{n}"}) for n in range(10)]
    resultado = rodar(ctx, ModeloDeRoteiro(roteiro), variant=variante(max_iterations=3))

    assert resultado.status == "budget_exceeded"
    assert resultado.iteracoes == 3
    budgets = [evento for evento in eventos(ctx) if isinstance(evento, BudgetEvent)]
    assert [evento.limite for evento in budgets] == ["max_iterations"]


def test_o_orcamento_nao_reseta_ao_trocar_de_tool() -> None:
    """`§3.5`: é assim que nasce laço infinito, porque toda chamada traz informação nova."""
    ctx = contexto()
    roteiro = [
        passo_de_acao("get_baseline", {"asset_id": "asset_B204"}),
        passo_de_acao("get_spectrum", {"asset_id": "asset_B204"}),
        passo_de_acao("get_rms_series", {"asset_id": "asset_B204"}),
    ]
    resultado = rodar(ctx, ModeloDeRoteiro(roteiro), variant=variante(max_tool_calls=3))

    assert resultado.status == "budget_exceeded"
    assert resultado.n_tool_calls == 3


def test_max_same_endpoint_fecha_o_endpoint_mas_nao_encerra_a_run() -> None:
    """Gastar 4 argumentos no mesmo endpoint é laço num recurso, não fim de orçamento."""
    ctx = contexto()
    roteiro = [
        *(
            passo_de_acao("get_baseline", {"asset_id": "asset_B204", "point_id": f"pt_{n}"})
            for n in range(MAX_SAME_ENDPOINT + 2)
        ),
        passo_de_resposta("concluí com o que tenho"),
    ]
    resultado = rodar(ctx, ModeloDeRoteiro(roteiro), variant=variante(max_iterations=10))

    assert resultado.status == "ok"
    budgets = [evento for evento in eventos(ctx) if isinstance(evento, BudgetEvent)]
    # Duas recusas, dois eventos: cada tentativa depois do teto é uma tentativa de fato, e
    # quantas vezes o agente insistiu num endpoint fechado é sintoma de laço, não ruído.
    assert [evento.limite for evento in budgets] == ["max_same_endpoint"] * 2
    chamadas = [
        evento
        for evento in eventos(ctx)
        if isinstance(evento, ToolCall) and evento.tool_name == "get_baseline"
    ]
    assert len(chamadas) == MAX_SAME_ENDPOINT


def test_chamada_repetida_identica_nao_conta_para_max_same_endpoint() -> None:
    """Repetição idêntica é cache-hit e vira `n_redundantes` na N2 — outra métrica."""
    ctx = contexto()
    args = {"asset_id": "asset_B204"}
    roteiro = [*(passo_de_acao("get_baseline", dict(args)) for _ in range(6)),
               passo_de_resposta("ok")]
    resultado = rodar(ctx, ModeloDeRoteiro(roteiro), variant=variante(max_iterations=10))

    assert resultado.status == "ok"
    assert not [evento for evento in eventos(ctx) if isinstance(evento, BudgetEvent)]
    resultados = [
        evento
        for evento in eventos(ctx)
        if isinstance(evento, ToolResult) and evento.cache_hit
    ]
    assert len(resultados) == 5


# ---------------------------------------------------------------------------
# Erro de tool não derruba a run
# ---------------------------------------------------------------------------


def test_api_fora_do_ar_vira_tool_result_indisponivel_e_a_run_continua() -> None:
    """`PLANO` T16: "erro de tool não derruba a run".

    Quem classifica `INDISPONIVEL` é o servidor (`env/status.py`), e o `tool_result` é dele —
    o harness não forja evento de fronteira (A13). O que o agente vê é o corpo do erro, e ele
    segue decidindo.
    """
    ctx = contexto(api_fora_do_ar)
    roteiro = [
        passo_de_acao("get_baseline", {"asset_id": "asset_B204"}),
        passo_de_resposta("não consegui a evidência, escalando", decisao="escalar"),
    ]
    resultado = rodar(ctx, ModeloDeRoteiro(roteiro), variant=variante(hidratacao=False))

    assert resultado.status == "ok"
    resultados = [evento for evento in eventos(ctx) if isinstance(evento, ToolResult)]
    assert [evento.status for evento in resultados] == ["INDISPONIVEL"]


def test_tool_inexistente_volta_como_texto_de_erro_sem_tool_call() -> None:
    """Nome fora do catálogo não gera `tool_call`: contaria `tools_extras` na N1.1 e uma
    chamada gasta na N2 para uma tool que o servidor nunca ofereceu."""
    ctx = contexto()
    modelo = ModeloDeRoteiro(
        [
            passo_de_acao("get_vibration_magic", {"asset_id": "asset_B204"}),
            passo_de_resposta("errei a tool, seguindo"),
        ]
    )
    resultado = rodar(ctx, modelo, variant=variante(hidratacao=False))

    assert resultado.status == "ok"
    assert not [evento for evento in eventos(ctx) if isinstance(evento, ToolCall)]
    # A observação volta ao modelo como mensagem de usuário: é assim que ele se corrige.
    assert "desconhecida" in modelo.recebidas[-1][-1]["content"]


def test_argumento_invalido_chega_ao_trace_e_o_agente_e_avisado() -> None:
    ctx = contexto()
    roteiro = [
        passo_de_acao("get_asset", {"asset_id": 123}),
        passo_de_resposta("corrigindo"),
    ]
    rodar(ctx, ModeloDeRoteiro(roteiro), variant=variante(hidratacao=False))

    chamadas = [evento for evento in eventos(ctx) if isinstance(evento, ToolCall)]
    assert len(chamadas) == 1
    assert chamadas[0].args_validos is False
    assert not [evento for evento in eventos(ctx) if isinstance(evento, ToolResult)]


# ---------------------------------------------------------------------------
# parse_erro é métrica, não exceção
# ---------------------------------------------------------------------------


def test_parse_erro_e_registrado_com_tentativa_e_o_modelo_ganha_uma_segunda_chance() -> None:
    ctx = contexto()
    modelo = ModeloDeRoteiro(["isto não é json", passo_de_resposta("na segunda deu")])
    resultado = rodar(ctx, modelo, variant=variante(hidratacao=False))

    chamadas = [evento for evento in eventos(ctx) if isinstance(evento, LLMCall)]
    assert [(evento.tentativa, evento.parse_ok) for evento in chamadas] == [
        (1, False),
        (2, True),
    ]
    assert chamadas[0].parse_erro is not None
    assert chamadas[0].parse_erro.startswith("json_invalido")
    assert resultado.status == "ok"
    assert resultado.parse_failures == 1
    assert "não pôde ser lida" in modelo.recebidas[-1][-1]["content"]


def test_duas_tentativas_falhas_encerram_a_run_como_error_e_nao_como_budget() -> None:
    """`budget_exceeded` diria que o agente investigou demais; o que houve foi um modelo que
    não produz o formato. A distinção é o que separa P (processo) de instrumento."""
    ctx = contexto()
    resultado = rodar(
        ctx, ModeloDeRoteiro(["{", "{"]), variant=variante(hidratacao=False)
    )

    assert resultado.status == "error"
    assert resultado.parse_failures == 2
    erros = [evento for evento in eventos(ctx) if isinstance(evento, RunError)]
    assert [(evento.classe, evento.fatal) for evento in erros] == [("ParseErro", True)]


def test_passo_com_acao_e_resposta_juntas_e_parse_erro_de_regra() -> None:
    """O JSON Schema não expressa "exatamente um destes dois", então a regra vive no Pydantic
    e a violação conta como saída inexecutável — do mesmo jeito que JSON quebrado."""
    ctx = contexto()
    invalido = passo_de_acao("get_asset", {"asset_id": "asset_B204"})
    invalido["resposta"] = {
        "texto": "e também respondo",
        "citacoes": [],
        "perguntar_de_volta": False,
    }
    resultado = rodar(
        ctx,
        ModeloDeRoteiro([invalido, passo_de_resposta("agora só a resposta")]),
        variant=variante(hidratacao=False),
    )

    assert resultado.status == "ok"
    assert resultado.parse_failures == 1


def test_usage_ausente_vira_run_error_nao_fatal_em_vez_de_token_inventado() -> None:
    """Os tokens são o eixo de custo do H0 (T35): estimativa não pode passar por medição."""
    ctx = contexto()

    class SemUsage(ModeloDeRoteiro):
        def completar(
            self, mensagens: Sequence[Mapping[str, str]], esquema: Mapping[str, Any]
        ) -> RespostaDoModelo:
            resposta = super().completar(mensagens, esquema)
            return RespostaDoModelo(
                **{**resposta.__dict__, "usage_ausente": True},
            )

    resultado = rodar(
        ctx, SemUsage([passo_de_resposta("ok")]), variant=variante(hidratacao=False)
    )
    erros = [evento for evento in eventos(ctx) if isinstance(evento, RunError)]
    assert [(evento.classe, evento.fatal) for evento in erros] == [("UsageAusente", False)]
    assert resultado.status == "ok"


# ---------------------------------------------------------------------------
# Decisão e resposta
# ---------------------------------------------------------------------------


def test_a_resposta_emite_decision_antes_do_final_answer() -> None:
    ctx = contexto()
    rodar(
        ctx,
        ModeloDeRoteiro(
            [passo_de_resposta("escalando", decisao="escalar", prioridade="alta")]
        ),
        variant=variante(hidratacao=False),
    )

    ordenados = eventos(ctx)
    decisoes = [evento for evento in ordenados if isinstance(evento, DecisionEvent)]
    finais = [evento for evento in ordenados if isinstance(evento, FinalAnswer)]
    assert len(decisoes) == 1 and len(finais) == 1
    assert decisoes[0].seq < finais[0].seq
    assert decisoes[0].decisao == "escalar"
    assert decisoes[0].prioridade_escalonamento == "alta"
    assert decisoes[0].racional_declarado == "tenho o suficiente"


def test_citacao_do_texto_e_do_campo_se_unem_e_a_validade_e_recalculada() -> None:
    ctx = contexto()
    roteiro = [
        passo_de_acao("get_baseline", {"asset_id": "asset_B204"}),
        passo_de_resposta(
            "o baseline está established (tc_01)",
            citacoes=["tc_99"],
        ),
    ]
    rodar(ctx, ModeloDeRoteiro(roteiro), variant=variante(hidratacao=False))

    final = next(evento for evento in eventos(ctx) if isinstance(evento, FinalAnswer))
    assert final.citacoes == ["tc_99", "tc_01"]
    assert final.citacoes_validas is False


def test_perguntar_de_volta_viaja_para_o_trace() -> None:
    ctx = contexto()
    rodar(
        ctx,
        ModeloDeRoteiro([passo_de_resposta("qual bomba?", decisao="perguntar", perguntar=True)]),
        variant=variante(hidratacao=False),
    )
    final = next(evento for evento in eventos(ctx) if isinstance(evento, FinalAnswer))
    assert final.perguntou_de_volta is True


# ---------------------------------------------------------------------------
# Segregação por modo (`§3.2`)
# ---------------------------------------------------------------------------


def test_o_mapa_de_modos_so_usa_nomes_que_existem_no_catalogo() -> None:
    """Teste de contrato: nome inventado aqui quebra em vez de virar tool invisível."""

    async def nomes() -> set[str]:
        async with abrir_sessao(contexto()) as sessao:
            return {tool.name for tool in (await sessao.list_tools()).tools}

    catalogo = anyio.run(nomes)
    do_mapa = frozenset().union(*TOOLS_POR_MODO.values())
    assert do_mapa <= catalogo, do_mapa - catalogo
    assert do_mapa == catalogo, f"tool sem modo: {catalogo - do_mapa}"


def test_por_modo_o_primeiro_passo_nao_ve_tool_de_acao() -> None:
    """`§3.2`: antes de declarar `executar` é estruturalmente impossível agir."""
    ctx = contexto()
    modelo = ModeloDeRoteiro([passo_de_resposta("ok")])
    rodar(ctx, modelo, variant=variante(tools_visiveis="por_modo", hidratacao=False))

    sistema = modelo.primeiro_sistema
    assert "`get_asset`" in sistema
    assert "`search_knowledge`" in sistema
    for acao in TOOLS_POR_MODO["executar"]:
        assert f"`{acao}`" not in sistema


def test_por_modo_recusa_tool_de_outro_subgrafo_e_registra_a_tentativa() -> None:
    """A tentativa não pode desaparecer: sem registro a segregação pareceria perfeita porque
    o instrumento apagou as tentativas."""
    ctx = contexto()
    roteiro = [
        passo_de_acao("request_retraining", {"model_id": "mdl_1"}, modo="investigar"),
        passo_de_resposta("desisti da ação"),
    ]
    resultado = rodar(
        ctx, ModeloDeRoteiro(roteiro), variant=variante(tools_visiveis="por_modo", hidratacao=False)
    )

    assert resultado.status == "ok"
    assert not [evento for evento in eventos(ctx) if isinstance(evento, ToolCall)]
    erros = [evento for evento in eventos(ctx) if isinstance(evento, RunError)]
    assert [(evento.classe, evento.onde, evento.fatal) for evento in erros] == [
        ("ToolForaDoModo", "request_retraining", False)
    ]


def test_por_modo_a_entrada_no_modo_executar_custa_uma_iteracao_e_mostra_os_schemas() -> None:
    """A passagem de subgrafo de `§3.2` é uma aresta, e aqui ela é uma iteração.

    O modelo pede a ação sem nunca ter visto o schema dela; executar assim mediria
    adivinhação de nome de argumento, que é justamente o que H2 separa de escolha de função.
    A troca não roda a ação: mostra o catálogo do modo novo e devolve a vez ao modelo.
    """
    ctx = contexto()
    acao = passo_de_acao(
        "escalate_case",
        {"case_id": "case_1", "justification": "evidência em tc_01 sustenta a escalada"},
        modo="executar",
    )
    roteiro = [
        passo_de_acao("get_asset", {"asset_id": "asset_B204"}, modo="investigar"),
        acao,  # pedida sem ver o schema: vira troca de catálogo, não chamada
        acao,  # agora com o catálogo do modo na janela
        passo_de_resposta("escalei", decisao="escalar", modo="executar"),
    ]
    modelo = ModeloDeRoteiro(roteiro)
    resultado = rodar(
        ctx, modelo, variant=variante(tools_visiveis="por_modo", hidratacao=False)
    )

    assert resultado.status == "ok"
    chamadas = [evento.tool_name for evento in eventos(ctx) if isinstance(evento, ToolCall)]
    assert chamadas == ["get_asset", "escalate_case"]
    # No passo da troca o catálogo ainda era o de leitura; no seguinte, o de execução.
    assert "`escalate_case`" not in modelo.recebidas[1][0]["content"]
    assert "`escalate_case`" in modelo.recebidas[2][0]["content"]
    assert "`get_asset`" not in modelo.recebidas[2][0]["content"]

    modos = [
        evento.modo
        for evento in eventos(ctx)
        if isinstance(evento, DecisionEvent)
    ]
    assert modos == ["investigar", "executar", "executar"]


# ---------------------------------------------------------------------------
# `exige_citacao`
# ---------------------------------------------------------------------------


def test_exige_citacao_muda_o_prompt_e_e_a_unica_diferenca() -> None:
    ctx = contexto()
    com = ModeloDeRoteiro([passo_de_resposta("ok")])
    sem = ModeloDeRoteiro([passo_de_resposta("ok")])
    rodar(ctx, com, variant=variante(hidratacao=False))
    rodar(contexto(), sem, variant=variante(exige_citacao=False, hidratacao=False))

    assert "Fundamentação" in com.primeiro_sistema
    assert "Fundamentação" not in sem.primeiro_sistema


def test_prompt_sha_declarado_diferente_do_carregado_quebra_no_construtor() -> None:
    """A variante É o prompt. Rodar com hash de outro rotularia a coluna do experimento
    errado, e nada no resultado acusaria."""

    with pytest.raises(ValueError, match="prompt_sha"):
        Agent(
            variante(prompt_sha="0" * 64),
            "qwen8b",
            cast("Any", None),  # a conferência é no construtor, antes de qualquer sessão
            ModeloDeRoteiro([]),
        )


# ---------------------------------------------------------------------------
# `seq` — a ordem total que o trace exige
# ---------------------------------------------------------------------------


def test_os_dois_emissores_compartilham_uma_ordem_sem_lacuna_e_sem_empate() -> None:
    """`ARQUITETURA §5`, decisões 8 e 9. Em memória o contador é um só — é o que faz o X23
    não contaminar a bateria."""
    ctx = contexto()
    roteiro = [
        passo_de_acao("get_baseline", {"asset_id": "asset_B204"}),
        passo_de_acao("get_spectrum", {"asset_id": "asset_B204"}),
        passo_de_resposta("pronto"),
    ]
    rodar(ctx, ModeloDeRoteiro(roteiro))

    sequencias = [evento.seq for evento in eventos(ctx)]
    assert sequencias == list(range(1, len(sequencias) + 1))
    assert len(set(sequencias)) == len(sequencias)


def test_a_trilha_de_fronteira_nao_emite_nada_e_e_a_decisao_do_x23() -> None:
    """Em stdio o harness não numera: as duas séries de `seq` colidiriam. O trace é o da
    fronteira — exatamente o que um agente de terceiro produz."""
    trilha = TrilhaDeFronteira()
    assert trilha.emitir(BudgetEvent, limite="max_tool_calls", valor=1) is None
    assert trilha.blob("x") == sha_do_prompt("x")


def test_abrir_sessao_recusa_contexto_em_stdio_e_exige_contexto_em_memoria() -> None:
    async def sem_contexto() -> None:
        async with abrir_sessao(None, "memoria"):
            pass

    async def contexto_em_stdio() -> None:
        async with abrir_sessao(contexto(), "stdio", comando=["python", "-c", ""]):
            pass

    async def stdio_sem_comando() -> None:
        async with abrir_sessao(None, "stdio"):
            pass

    with pytest.raises(ValueError, match="RunContext"):
        anyio.run(sem_contexto)
    with pytest.raises(ValueError, match="X23"):
        anyio.run(contexto_em_stdio)
    with pytest.raises(ValueError, match="comando"):
        anyio.run(stdio_sem_comando)


# ---------------------------------------------------------------------------
# O cliente de inferência
# ---------------------------------------------------------------------------


def test_o_esquema_vai_no_response_format_e_nunca_por_instrucao_no_prompt() -> None:
    """`PLANO` T16: saída estruturada é do servidor de inferência. Pedir JSON por instrução
    faria "o modelo obedece formato" virar variável do experimento."""
    enviados: list[dict[str, Any]] = []

    def responder(requisicao: httpx.Request) -> httpx.Response:
        enviados.append(json.loads(requisicao.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": json.dumps(passo_de_resposta("ok"))},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 11, "completion_tokens": 3},
            },
        )

    cliente = ClienteDeInferencia(
        "http://inferencia.invalida/v1", MODELO, transport=httpx.MockTransport(responder)
    )
    resposta = cliente.completar([{"role": "user", "content": "oi"}], ESQUEMA_DO_PASSO)

    assert enviados[0]["response_format"]["type"] == "json_schema"
    assert enviados[0]["response_format"]["json_schema"]["strict"] is True
    assert enviados[0]["response_format"]["json_schema"]["schema"] == ESQUEMA_DO_PASSO
    assert enviados[0]["model"] == "qwen3-8b"
    assert enviados[0]["seed"] == 7
    assert enviados[0]["temperature"] == 0.0
    assert resposta.parse_ok is True
    assert (resposta.prompt_tokens, resposta.completion_tokens) == (11, 3)
    assert resposta.usage_ausente is False


def test_structured_output_none_nao_manda_response_format() -> None:
    """Condição experimental legítima: mede o custo de NÃO ter gramática."""
    enviados: list[dict[str, Any]] = []

    def responder(requisicao: httpx.Request) -> httpx.Response:
        enviados.append(json.loads(requisicao.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    modelo = MODELO.model_copy(update={"structured_output": "none"})
    cliente = ClienteDeInferencia(
        "http://inferencia.invalida/v1", modelo, transport=httpx.MockTransport(responder)
    )
    cliente.completar([{"role": "user", "content": "oi"}], ESQUEMA_DO_PASSO)
    assert "response_format" not in enviados[0]


def test_structured_output_grammar_quebra_alto() -> None:
    """GBNF é do llama.cpp e não viaja no protocolo OpenAI. Cair em `prompt` em silêncio
    mediria outro mecanismo do que o manifesto declara."""
    modelo = MODELO.model_copy(update={"structured_output": "grammar"})
    cliente = ClienteDeInferencia(
        "http://inferencia.invalida/v1",
        modelo,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={})),
    )
    with pytest.raises(NotImplementedError, match="grammar"):
        cliente.completar([{"role": "user", "content": "oi"}], ESQUEMA_DO_PASSO)


def test_usage_ausente_e_declarado_e_os_tokens_viram_estimativa() -> None:
    def responder(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": json.dumps(passo_de_resposta("ok"))},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    cliente = ClienteDeInferencia(
        "http://inferencia.invalida/v1", MODELO, transport=httpx.MockTransport(responder)
    )
    resposta = cliente.completar([{"role": "user", "content": "oi"}], ESQUEMA_DO_PASSO)
    assert resposta.usage_ausente is True
    assert resposta.prompt_tokens > 0


def test_endpoint_de_inferencia_fora_do_ar_sobe_em_vez_de_virar_parse_erro() -> None:
    """A assimetria é o ponto: `parse_erro` é comportamento do modelo, endpoint morto é
    falha do instrumento. Confundi-los inflaria a métrica que separa os modelos."""
    cliente = ClienteDeInferencia(
        "http://inferencia.invalida/v1",
        MODELO,
        transport=httpx.MockTransport(lambda _: httpx.Response(500, text="boom")),
    )
    with pytest.raises(httpx.HTTPStatusError):
        cliente.completar([{"role": "user", "content": "oi"}], ESQUEMA_DO_PASSO)


def test_esquema_estrito_fecha_objetos_e_exige_todo_campo() -> None:
    esquema = esquema_estrito_do_passo()
    assert esquema["additionalProperties"] is False
    assert set(esquema["required"]) == set(esquema["properties"])
    for definicao in (esquema.get("$defs") or {}).values():
        if definicao.get("type") == "object":
            assert definicao["additionalProperties"] is False
            assert set(definicao["required"]) == set(definicao["properties"])
    assert "default" not in json.dumps(esquema)


def esquema_estrito_do_passo() -> dict[str, Any]:
    from tapieval.sut.agent import PassoDoAgente  # noqa: PLC0415

    return esquema_estrito(PassoDoAgente)


def test_o_esquema_carrega_o_vocabulario_fechado_de_modo_e_decisao() -> None:
    serializado = json.dumps(ESQUEMA_DO_PASSO)
    for modo in ("contextualizar", "investigar", "executar"):
        assert modo in serializado
    for decisao in ("orientar", "agir", "escalar", "perguntar", "recusar"):
        assert decisao in serializado


# ---------------------------------------------------------------------------
# Ponta a ponta: a run vira score
# ---------------------------------------------------------------------------


def test_uma_run_completa_com_gate_produz_trace_pontuavel_por_n1_e_n2(tmp_path: Path) -> None:
    """O teste que junta tudo: agente real, servidor real, gate ligado, trace em disco, e os
    dois scorers determinísticos lendo o arquivo de volta.

    É a prova de que o instrumento fecha ponta a ponta antes de qualquer modelo de verdade
    entrar (T19).
    """
    observador = ObservadorDeTrace(TraceWriter(tmp_path, "run_e2e"))
    ctx = contexto(run_id="run_e2e", observador=observador)
    gate = ligar_gate(ctx, AutoApprove())

    roteiro = [
        passo_de_acao("list_analyses", {"asset_id": "asset_B204"}),
        passo_de_acao(
            "escalate_case",
            {
                "case_id": "case_tkt_exe_16",
                "justification": "análises inconclusivas em tc_03 e ativo crítico em tc_01",
            },
            modo="executar",
        ),
        passo_de_resposta(
            "escalei o caso: evidência em tc_03 é inconclusiva e o ativo é crítico (tc_01)",
            decisao="escalar",
            prioridade="alta",
            modo="executar",
        ),
    ]
    resultado = rodar(
        ctx,
        ModeloDeRoteiro(roteiro),
        solicitacao=Solicitacao(
            message="Encaminha pra alguém.",
            user_id="usr_lucas",
            asset_id="asset_B204",
            case_id="case_tkt_exe_16",
        ),
    )

    assert resultado.status == "ok"
    assert resultado.n_llm_calls == 3
    assert resultado.prompt_tokens == 300

    do_disco = read_trace(tmp_path / "traces" / "run_e2e.jsonl")
    assert [evento.seq for evento in do_disco] == list(range(1, len(do_disco) + 1))

    gates = [evento for evento in do_disco if isinstance(evento, GateEvent)]
    assert len(gates) == 1
    assert gates[0].veredito == "aprovado"
    chamada_da_acao = next(
        evento
        for evento in do_disco
        if isinstance(evento, ToolCall) and evento.tool_name == "escalate_case"
    )
    # X20: o gate precede por `seq` a chamada que ele autoriza.
    assert gates[0].seq < chamada_da_acao.seq
    assert gate is not None, "o runner precisa da referência para provar idempotência (T18)"

    cenario = carregar_cenarios()["cen_10_escalar_para_humano"]
    n1 = pontuar_n1(do_disco, cenario)
    n2 = pontuar_n2(do_disco, cenario)

    assert n1.decisao_prevista == "escalar"
    assert n1.gate_respeitado is True
    assert n1.acao_indevida is False
    assert n2.n_tool_calls == resultado.n_tool_calls
    assert n2.parse_failures == 0
    assert n2.n_iteracoes == 3


def test_o_trace_da_run_e_o_mesmo_lido_do_disco_e_da_memoria(tmp_path: Path) -> None:
    """O observador guarda em memória o que escreve — o gate decide sobre a lista e o scorer
    sobre o arquivo, e as duas visões têm de ser a mesma coisa."""
    observador = ObservadorDeTrace(TraceWriter(tmp_path, "run_par"))
    ctx = contexto(run_id="run_par", observador=observador)
    rodar(
        ctx,
        ModeloDeRoteiro(
            [passo_de_acao("get_asset", {"asset_id": "asset_B204"}), passo_de_resposta("ok")]
        ),
        variant=variante(hidratacao=False),
    )

    da_memoria = [evento.model_dump_json() for evento in eventos(ctx)]
    do_disco = [
        evento.model_dump_json()
        for evento in read_trace(tmp_path / "traces" / "run_par.jsonl")
    ]
    assert da_memoria == do_disco


def test_o_gate_esta_no_caminho_do_agente_de_verdade_e_a_recusa_nao_derruba_a_run(
    tmp_path: Path,
) -> None:
    """Citação fantasma na justificativa: o gate nega, a API não é chamada, o agente segue.

    A T15 provou isso chamando o handler direto. Aqui é o agente inteiro, com laço e prompt, e
    o que se prova a mais é que a negação volta ao modelo como observação — um agente que não
    vê a recusa não tem como corrigir a rota, e a run mediria o instrumento.
    """
    from tapieval.mcp.gate import PolicyApprover  # noqa: PLC0415 — só este teste usa

    observador = ObservadorDeTrace(TraceWriter(tmp_path, "run_gate"))
    ctx = contexto(run_id="run_gate", observador=observador)
    ligar_gate(ctx, PolicyApprover())

    modelo = ModeloDeRoteiro(
        [
            passo_de_acao(
                "escalate_case",
                {
                    "case_id": "case_1",
                    "justification": "o espectro em tc_77 mostra falha grave no rolamento",
                },
                modo="executar",
            ),
            passo_de_resposta("não escalei; explico o que encontrei", decisao="orientar"),
        ]
    )
    # Hidratação LIGADA de propósito: sem ela o gate nega antes de olhar a citação, porque
    # permissão não verificada é fail-closed (`permissao_nao_verificada`) e o teste passaria
    # pelo motivo errado.
    resultado = rodar(ctx, modelo)

    assert resultado.status == "ok"
    gates = [evento for evento in eventos(ctx) if isinstance(evento, GateEvent)]
    assert [(evento.veredito, evento.motivo_negacao) for evento in gates] == [
        ("negado", "citacao_fantasma:tc_77")
    ]
    assert gates[0].citacoes_invalidas == ["tc_77"]
    # A ação NÃO chegou à API, mas a tentativa está no trace: sem o `tool_call`, o agente que
    # tenta a ação proibida pontuaria igual ao que corretamente recusou.
    chamadas = [evento.tool_name for evento in eventos(ctx) if isinstance(evento, ToolCall)]
    assert chamadas == ["get_asset", "get_current_user", "escalate_case"]
    respondidas = {
        evento.tool_call_id for evento in eventos(ctx) if isinstance(evento, ToolResult)
    }
    assert len(respondidas) == 2, "a escrita negada não tem resultado: a API não foi chamada"
    assert "bloqueada" in modelo.recebidas[-1][-1]["content"]


@pytest.mark.lento
def test_o_agente_roda_por_stdio_contra_servidor_em_outro_processo(tmp_path: Path) -> None:
    """A prova de generalidade aplicada ao NOSSO agente: mesmo código, transporte de terceiro.

    E é aqui que o X23 aparece em vez de ser argumentado: com `TrilhaDeFronteira`, o trace tem
    **só** eventos do servidor — nenhum `llm_call`, nenhum `final_answer` —, `seq` contíguo, e
    a run continua pontuável em N1 pelos atos observáveis. Se o harness numerasse deste lado,
    as duas séries colidiriam no mesmo arquivo.
    """
    destino = tmp_path / "stdio"
    apoio = RAIZ / "tests" / "servidor_stdio.py"
    modelo = ModeloDeRoteiro(
        [
            passo_de_acao("get_asset", {"asset_id": "asset_H110"}),
            passo_de_resposta("a bomba está com sensor online", citacoes=["tc_01"]),
        ]
    )

    async def executar() -> ResultadoDaRun:
        async with abrir_sessao(
            None,
            "stdio",
            comando=[sys.executable, str(apoio), str(destino), "run_stdio_agente"],
        ) as sessao:
            agente = Agent(variante(hidratacao=False), "qwen8b", sessao, modelo)
            return await agente.run(
                Solicitacao(
                    message="A bomba H-110 está bem?",
                    user_id="usr_bruno",
                    asset_id="asset_H110",
                )
            )

    resultado = anyio.run(executar)
    assert resultado.status == "ok"
    assert resultado.final_answer is None, "sem trilha, o harness não emite evento nenhum"

    do_disco = read_trace(destino / "traces" / "run_stdio_agente.jsonl")
    tipos = [evento.type for evento in do_disco]
    assert tipos == ["tool_call", "tool_result"]
    assert [evento.seq for evento in do_disco] == [1, 2]
