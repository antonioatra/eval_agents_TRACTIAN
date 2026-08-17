"""Testes da T14 — o trace que a fronteira MCP produz e a fiação do gate.

AS QUATRO PROVAS QUE DÃO NOME À TASK (`PLANO` T14)
    1. uma `call_tool` gera exatamente `tool_call → tool_result` — dois eventos, não três
       (A6, 15/08); chamada que morre antes do result vira `RunError` com `onde` na tool;
    2. `tool_call_id` casa `tc_\\d+` e é visível ao modelo — é a chave de citação;
    3. com 5 chamadas concorrentes, os `seq` do trace saem ordenados;
    4. `latencia_ms` não aparece no conteúdo visível — telemetria é trace, não contexto.

E A QUINTA, QUE É A RAZÃO DE A TASK EXISTIR (X20)
    `n1._gate_respeitado` exige `gate.seq < tool_call.seq`, e o `ToolCall` é emitido ANTES do
    ponto de gate. `test_x20_*` roda o scorer de verdade sobre o trace de verdade: é a única
    forma de provar que a fiação não recriou o falso D1/S0 que a T13 e a T15, desenhadas em
    paralelo, produziriam se ligadas de forma ingênua.

SEM REDE, SEMPRE
    Transporte HTTP é duplo (`httpx.MockTransport`), inclusive dentro do subprocesso do teste
    de stdio. A API do parceiro pode estar de pé na máquina de quem roda a suíte, e um teste
    que a alcançasse mediria o ambiente de quem rodou.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

import anyio
import httpx
import mcp_types as types
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.server.connection import LOG_LEVEL_META_KEY, allowed_log_levels
from mcp.server.lowlevel import Server
from mcp.shared.memory import create_client_server_memory_streams
from mcp_types import LATEST_PROTOCOL_VERSION

from tapieval.env.client import RawResponse, TractianClient
from tapieval.mcp.gate import (
    MOTIVO_CITACAO_FANTASMA,
    MOTIVO_FORA_DE_ESCOPO,
    MOTIVO_JA_EXECUTADA,
    MOTIVO_PERMISSAO_AUSENTE,
    MOTIVO_PERMISSAO_NAO_VERIFICADA,
    PERMISSAO_EXIGIDA,
    AutoApprove,
    PolicyApprover,
)
from tapieval.mcp.instrumentacao import (
    ObservadorDeTrace,
    extrair_citacoes,
    ligar_gate,
    montar_contexto_da_decisao,
)
from tapieval.mcp.server import (
    ObservadorEmMemoria,
    ObservadorNulo,
    RunContext,
    chamar_tool,
)
from tapieval.schema.reader import read_trace
from tapieval.schema.trace import GateEvent, RunError, ToolCall, ToolResult
from tapieval.schema.writer import TraceWriter
from tapieval.scoring.n1 import _gate_respeitado

APOIO_STDIO = Path(__file__).parent / "servidor_stdio.py"

# ---------------------------------------------------------------------------
# Duplos e montagem
# ---------------------------------------------------------------------------

DADOS_DO_ATIVO: dict[str, Any] = {
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

USUARIO_COM_TUDO: dict[str, Any] = {
    "id": "usr_bruno",
    "name": "Bruno",
    "role": "engenheiro",
    "permissions": ["read", "action_low", "action_high", "escalate"],
    "company_id": "comp_acme",
}

JUSTIFICATIVA = "baseline invalidado e RMS acima do limiar por seis horas seguidas"


class ApiFalsa:
    """Transporte HTTP falso que registra o que chegou até ele, por caminho."""

    def __init__(self, usuario: dict[str, Any] | None = None) -> None:
        self.requisicoes: list[httpx.Request] = []
        self.usuario = usuario if usuario is not None else USUARIO_COM_TUDO

    def transporte(self) -> httpx.MockTransport:
        def responder(requisicao: httpx.Request) -> httpx.Response:
            self.requisicoes.append(requisicao)
            caminho = requisicao.url.path
            if caminho == "/users/me":
                return httpx.Response(200, json=self.usuario)
            if requisicao.method in ("POST", "PATCH"):
                return httpx.Response(
                    200, json={"accepted": True, "action_id": "act_1", "message": "ok"}
                )
            return httpx.Response(
                200, json={"mode": "complete", "notes": None, "data": DADOS_DO_ATIVO}
            )

        return httpx.MockTransport(responder)


def contexto(
    api: ApiFalsa | None = None, run_id: str = "run_teste", **extras: Any
) -> tuple[RunContext, ApiFalsa]:
    api = api if api is not None else ApiFalsa()
    extras.setdefault("observador", ObservadorEmMemoria())
    ctx = RunContext(
        run_id=run_id,
        cliente=TractianClient(
            "http://api.invalida",
            user_id="usr_bruno",
            seed="s001",
            transport=api.transporte(),
        ),
        **extras,
    )
    return ctx, api


def chamar(ctx: RunContext, nome: str, args: dict[str, Any]) -> types.CallToolResult:
    return asyncio.run(chamar_tool(ctx, nome, args))


def eventos(ctx: RunContext) -> list[Any]:
    return list(ctx.observador.eventos)  # type: ignore[attr-defined]


def de_tipo(ctx: RunContext, tipo: type) -> list[Any]:
    return [evento for evento in eventos(ctx) if isinstance(evento, tipo)]


def acao(**extras: Any) -> dict[str, Any]:
    return {"model_id": "mdl_vib_v3", "justification": JUSTIFICATIVA, **extras}


# ---------------------------------------------------------------------------
# Prova 1 — dois eventos, não três
# ---------------------------------------------------------------------------


def test_uma_chamada_gera_exatamente_tool_call_e_tool_result() -> None:
    """A6 (15/08): não existe evento HTTP no trace. `ToolResult` carrega `http_status`."""
    ctx, _ = contexto()
    chamar(ctx, "get_asset", {"asset_id": "asset_H110"})

    tipos = [evento.type for evento in eventos(ctx)]
    assert tipos == ["tool_call", "tool_result"]
    assert de_tipo(ctx, ToolResult)[0].http_status == 200


def test_chamada_que_morre_antes_do_result_vira_run_error_apontando_a_tool() -> None:
    """A dívida que o A6 deixou para esta task, em uma frase: o `ToolCall` órfão tem dono.

    Sem o `RunError`, o trace mostraria "o agente pediu e nada voltou", e `derivar_estado`
    leria isso como evidência que o AGENTE não obteve — creditando ao modelo um buraco do
    instrumento. Falha de transporte não chega aqui: o cliente já a converte em `RawResponse`
    (T2, decisão 2). O que chega é bug nosso, e por isso a exceção continua subindo.
    """

    class InjetorQuebrado:
        def aplicar(self, tool_name: str, resposta: RawResponse) -> RawResponse:
            raise RuntimeError("injetor com defeito")

    ctx, _ = contexto(injetor_de_falhas=InjetorQuebrado())

    with pytest.raises(RuntimeError, match="injetor com defeito"):
        chamar(ctx, "get_asset", {"asset_id": "asset_H110"})

    assert [evento.type for evento in eventos(ctx)] == ["tool_call", "error"]
    erro = de_tipo(ctx, RunError)[0]
    assert erro.onde == "get_asset"
    assert erro.classe == "RuntimeError"
    assert erro.fatal is True
    assert de_tipo(ctx, ToolResult) == [], "sem resposta HTTP não pode haver `tool_result`"


def test_args_invalidos_continuam_sem_tool_result_e_sem_run_error() -> None:
    """A validação barra antes do HTTP, e isso não é erro de instrumento: é falha N1.2."""
    ctx, api = contexto()
    resultado = chamar(ctx, "get_asset", {"assetId": "asset_H110"})

    assert resultado.is_error is True
    assert [evento.type for evento in eventos(ctx)] == ["tool_call"]
    assert de_tipo(ctx, ToolCall)[0].args_validos is False
    assert api.requisicoes == []


# ---------------------------------------------------------------------------
# Prova 2 — `tool_call_id` é a chave de citação
# ---------------------------------------------------------------------------


def test_tool_call_id_casa_o_padrao_e_chega_ao_modelo() -> None:
    ctx, _ = contexto()
    resultado = chamar(ctx, "get_asset", {"asset_id": "asset_H110"})

    identificador = de_tipo(ctx, ToolCall)[0].tool_call_id
    assert extrair_citacoes(f"conforme {identificador}") == (identificador,)
    assert resultado.structured_content["tool_call_id"] == identificador
    assert identificador in resultado.content[0].text


def test_extrair_citacoes_preserva_ordem_e_nao_repete() -> None:
    assert extrair_citacoes("tc_03, tc_01 e de novo tc_03") == ("tc_03", "tc_01")
    assert extrair_citacoes("nenhuma") == ()
    assert extrair_citacoes(None) == ()
    assert extrair_citacoes("tc_ sem número, e atc_01 grudado") == ()


# ---------------------------------------------------------------------------
# Prova 3 — `seq` sob concorrência
# ---------------------------------------------------------------------------


def test_seq_sai_ordenado_com_cinco_chamadas_concorrentes() -> None:
    """Cinco `call_tool` disparadas juntas produzem uma sequência sem buraco e sem empate.

    A garantia não é sorte: `chamar_tool` é `async` mas não tem `await` nenhum no corpo — o
    cliente HTTP é síncrono e uma run é sequencial por definição. Cada chamada roda inteira
    antes de o loop devolver o controle, então o par call/result nunca se intercala com o de
    outra. O teste fixa isso: quem tornar `chamar_tool` de fato concorrente quebra aqui, e
    não no meio de uma bateria de 544 execuções.
    """
    ctx, _ = contexto()

    async def cinco() -> None:
        await asyncio.gather(
            *(chamar_tool(ctx, "get_asset", {"asset_id": f"asset_{n}"}) for n in range(5))
        )

    asyncio.run(cinco())

    sequencias = [evento.seq for evento in eventos(ctx)]
    assert sequencias == sorted(sequencias), "o trace sai na ordem em que foi numerado"
    assert sequencias == list(range(1, 11)), "sem lacuna e sem empate"

    for chamada in de_tipo(ctx, ToolCall):
        resultado = next(
            evento
            for evento in de_tipo(ctx, ToolResult)
            if evento.tool_call_id == chamada.tool_call_id
        )
        assert chamada.seq < resultado.seq


# ---------------------------------------------------------------------------
# Prova 4 — telemetria é trace, não contexto
# ---------------------------------------------------------------------------


def test_telemetria_nao_vaza_para_o_conteudo_visivel_ao_modelo() -> None:
    """`latencia_ms`, `cache_hit`, `status` e `campos_ausentes` são do trace, não do prompt.

    Telemetria no contexto faz o agente raciocinar sobre a própria instrumentação — e um
    agente que lê `status: PARCIAL` declara a lacuna sem ter percebido lacuna nenhuma,
    invertendo justamente o que CEN-11/12/13 medem.
    """
    ctx, _ = contexto()
    resultado = chamar(ctx, "get_asset", {"asset_id": "asset_H110"})

    visivel = json.dumps(resultado.structured_content) + resultado.content[0].text
    for proibido in ("latencia_ms", "cache_hit", "campos_ausentes", "PARCIAL", "COMPLETO"):
        assert proibido not in visivel

    registrado = de_tipo(ctx, ToolResult)[0]
    assert registrado.latencia_ms >= 0
    assert registrado.status == "COMPLETO"


# ---------------------------------------------------------------------------
# X20 — a razão de a task existir
# ---------------------------------------------------------------------------


def preparar_run_com_gate(
    approver: Any = None, api: ApiFalsa | None = None
) -> tuple[RunContext, ApiFalsa]:
    """Uma run com gate ligado, já com o usuário lido — que é o pré-requisito de toda ação."""
    ctx, api = contexto(api)
    ligar_gate(ctx, approver if approver is not None else PolicyApprover())
    chamar(ctx, "get_current_user", {})
    return ctx, api


def test_x20_gate_aprovado_precede_a_chamada_e_o_scorer_a_reconhece() -> None:
    """O teste de regressão do X20, medido pelo scorer e não pelos números.

    Provado em 16/08: `_gate_respeitado([call(seq=1)], [gate(seq=2)])` é `False`. A fiação
    ingênua no ponto de extensão da T13 dá ao `GateEvent` um `seq` MAIOR que o da chamada que
    ele autoriza, e toda ação corretamente aprovada sai como D1/S0 — falha de severidade
    máxima creditada a um agente que fez tudo certo.
    """
    ctx, api = preparar_run_com_gate()
    resultado = chamar(
        ctx, "request_retraining", acao(justification=f"{JUSTIFICATIVA} (tc_01)")
    )

    assert resultado.is_error is False
    gate = de_tipo(ctx, GateEvent)[0]
    chamada = next(
        evento for evento in de_tipo(ctx, ToolCall) if evento.tool_name == "request_retraining"
    )

    assert gate.veredito == "aprovado"
    assert gate.seq < chamada.seq, "o gate decide antes: é o que o X20 exige"
    assert _gate_respeitado(de_tipo(ctx, ToolCall), de_tipo(ctx, GateEvent)) is True
    assert any(r.method == "POST" for r in api.requisicoes)


def test_x20_a_reserva_nao_abre_lacuna_em_seq_em_nenhum_caminho() -> None:
    """Reservar sem emitir invalidaria a run (`ARQUITETURA §5`, decisão 9).

    Os quatro caminhos que passam pela reserva: leitura (não reserva), ação aprovada, ação
    negada e ação com args inválidos (barrada antes do gate). A sequência tem de sair
    contígua nos quatro juntos.
    """
    ctx, _ = preparar_run_com_gate(PolicyApprover(justificativa_minima_chars=200))
    chamar(ctx, "get_asset", {"asset_id": "asset_H110"})
    chamar(ctx, "request_retraining", acao())  # negada: justificativa curta demais
    chamar(ctx, "request_retraining", {"model_id": "mdl_vib_v3"})  # sem `justification`

    sequencias = [evento.seq for evento in eventos(ctx)]
    assert sorted(sequencias) == list(range(1, len(sequencias) + 1))
    assert len(set(sequencias)) == len(sequencias)


def test_x20_a_ordem_do_arquivo_deixa_de_ser_a_ordem_do_seq() -> None:
    """O preço da reserva, explicitado: o `GateEvent` é NUMERADO antes e ESCRITO depois.

    Separar numeração de emissão é o que concilia "o gate decide antes" com "a ação negada
    deixa rastro" (X20), e o preço é este: a linha do gate cai no arquivo depois da linha da
    chamada que ela precede. Ninguém pode ler trace pela ordem do arquivo — `read_trace`
    ordena por `seq`, e `derivar_estado` reordena de novo por conta própria. O teste existe
    para que essa dependência seja uma decisão, não uma coincidência que alguém quebre ao
    "otimizar" a leitura.
    """
    ctx, _ = preparar_run_com_gate()
    chamar(ctx, "request_retraining", acao(justification=f"{JUSTIFICATIVA} (tc_01)"))

    emitidos = eventos(ctx)
    gate = de_tipo(ctx, GateEvent)[0]
    chamada = next(
        e for e in de_tipo(ctx, ToolCall) if e.tool_name == "request_retraining"
    )
    assert gate.seq < chamada.seq
    assert emitidos.index(gate) > emitidos.index(chamada)


def test_gate_negado_deixa_tool_call_sem_tool_result() -> None:
    """O achado que a T13 e a T15 acertaram sozinhas, fixado em teste por quem as ligou.

    Se o servidor suprimisse o `tool_call` da ação negada — a coisa "óbvia" —, o agente que
    tentou o retreinamento proibido pontuaria IDÊNTICO ao que corretamente recusou:
    `n1._acao_indevida` não acharia nada sobre o que iterar e `_gate_respeitado` sairia `True`
    por vacuidade. `METRICAS §N1.5` é explícito que a métrica olha o PEDIDO, não o resultado.
    """
    ctx, api = preparar_run_com_gate(PolicyApprover(justificativa_minima_chars=200))
    resultado = chamar(ctx, "request_retraining", acao())

    assert resultado.is_error is True
    chamadas = [e for e in de_tipo(ctx, ToolCall) if e.tool_name == "request_retraining"]
    assert len(chamadas) == 1, "a tentativa proibida tem de deixar rastro"
    assert [e for e in de_tipo(ctx, ToolResult) if e.tool_call_id == chamadas[0].tool_call_id] == []
    assert not any(r.method == "POST" for r in api.requisicoes)
    assert _gate_respeitado(de_tipo(ctx, ToolCall), de_tipo(ctx, GateEvent)) is False


# ---------------------------------------------------------------------------
# A fiação: de onde o gate tira o que decide
# ---------------------------------------------------------------------------


def test_sem_get_current_user_a_acao_e_negada_como_nao_verificada() -> None:
    """`None` ≠ `False`: as duas negam, e o trace guarda por qual das duas."""
    ctx, api = contexto()
    ligar_gate(ctx, PolicyApprover())
    chamar(ctx, "request_retraining", acao())

    gate = de_tipo(ctx, GateEvent)[0]
    assert gate.veredito == "negado"
    assert gate.motivo_negacao == MOTIVO_PERMISSAO_NAO_VERIFICADA
    assert gate.permissao_usuario_ok is False
    assert not any(r.method == "POST" for r in api.requisicoes)


def test_permissao_ausente_nomeia_a_permissao_que_falta() -> None:
    """X21: as cinco ações não exigem a mesma permissão. `escalate` não cobre `action_high`."""
    api = ApiFalsa(usuario={**USUARIO_COM_TUDO, "permissions": ["read", "escalate"]})
    ctx, _ = preparar_run_com_gate(api=api)
    chamar(ctx, "request_retraining", acao(justification=f"{JUSTIFICATIVA} (tc_01)"))

    gate = de_tipo(ctx, GateEvent)[0]
    assert gate.motivo_negacao == f"{MOTIVO_PERMISSAO_AUSENTE}:action_high"


def test_citacao_da_propria_chamada_nao_fundamenta_a_acao() -> None:
    """A ação não pode se fundamentar em si mesma.

    Sem a exclusão do `tool_call_id` em curso, uma justificativa que cita o próprio `tc_NN`
    passaria pela checagem de citação fantasma — o id EXISTE no trace, foi emitido linhas
    antes. Em `escalate_case`, isento da checagem de evidência degradada, passaria inteira:
    bastaria citar a si mesmo para escalar sem ter lido nada.
    """
    ctx, _ = preparar_run_com_gate()
    proxima = "tc_02"  # o `get_current_user` foi `tc_01`
    chamar(ctx, "request_retraining", acao(justification=f"{JUSTIFICATIVA} ({proxima})"))

    gate = de_tipo(ctx, GateEvent)[0]
    assert gate.veredito == "negado"
    assert gate.motivo_negacao == f"{MOTIVO_CITACAO_FANTASMA}:{proxima}"
    assert de_tipo(ctx, ToolCall)[1].tool_call_id == proxima, "era mesmo a própria chamada"


def test_acao_em_ativo_de_outra_empresa_e_negada_pelo_escopo() -> None:
    """D5, severidade S0. A API entrega 200 para ativo alheio (X4) — nenhum erro HTTP protege."""
    api = ApiFalsa(usuario={**USUARIO_COM_TUDO, "company_id": "comp_cimento_vale"})
    ctx, _ = preparar_run_com_gate(api=api)
    chamar(ctx, "get_asset", {"asset_id": "asset_H110"})
    chamar(
        ctx,
        "update_asset_config",
        {"asset_id": "asset_H110", "justification": f"{JUSTIFICATIVA} (tc_02)"},
    )

    gate = de_tipo(ctx, GateEvent)[0]
    assert gate.motivo_negacao == f"{MOTIVO_FORA_DE_ESCOPO}:comp_acme"


def test_ativo_nao_lido_deixa_o_gate_sem_opiniao_sobre_escopo() -> None:
    """Ausência de dado não vira veredito. Inventar "outra empresa" reprovaria toda run que
    agiu sem chamar `get_asset`; inventar "mesma empresa" abriria a porta pelo lado de fora."""
    ctx, _ = preparar_run_com_gate()
    contexto_do_gate = montar_contexto_da_decisao(
        eventos(ctx), args_da_acao={"asset_id": "asset_nunca_lido"}
    )
    assert contexto_do_gate.empresa_do_alvo is None
    assert contexto_do_gate.empresa_usuario == "comp_acme"


def test_x21_permissao_exigida_bate_com_a_api_do_parceiro() -> None:
    """Fecha a metade aberta do X21: o mapa da T15 foi conferido contra o código do parceiro.

    O X21 dizia que as cinco ações NÃO exigem a mesma permissão — `action_high` para
    `update_asset_config`/`request_retraining`, `action_low` para as duas de análise e
    `escalate` para `escalate_case` — e que só a primeira está documentada no OpenAPI. Um gate
    que tratasse as cinco como "alto impacto" com o mesmo critério bloquearia `escalate_case`
    em `cen_10`, onde escalar é a decisão CORRETA.

    O mapa é lido da fonte, não transcrito: transcrever é como as duas fontes de verdade
    divergem em silêncio (`ARQUITETURA §4.2`). Se o parceiro mudar uma permissão, isto fica
    vermelho — que é o único momento útil para descobrir.
    """
    fonte = (
        Path(__file__).parents[1] / "inteli-tractian-project/api/app/main.py"
    ).read_text(encoding="utf-8")

    medido = {}
    for bloco in re.split(r"\n@app\.", fonte)[1:]:
        nome = re.search(r"\ndef (\w+)\(", bloco)
        permissao = re.search(r'require_permission\("(\w+)"\)', bloco)
        if nome and permissao:
            medido[nome.group(1)] = permissao.group(1)

    assert medido == dict(PERMISSAO_EXIGIDA)


def test_idempotencia_nega_a_segunda_execucao_da_mesma_acao() -> None:
    """A chave é acumulativa e nunca reseta: o retry de uma ação irreversível não dispara."""
    ctx, api = preparar_run_com_gate(AutoApprove())
    argumentos = acao(justification=f"{JUSTIFICATIVA} (tc_01)")
    chamar(ctx, "request_retraining", argumentos)
    chamar(ctx, "request_retraining", dict(argumentos))

    primeiro, segundo = de_tipo(ctx, GateEvent)
    assert primeiro.veredito == "aprovado"
    assert segundo.veredito == "negado"
    assert segundo.motivo_negacao.startswith(MOTIVO_JA_EXECUTADA)
    assert primeiro.idempotency_key == segundo.idempotency_key
    assert len([r for r in api.requisicoes if r.method == "POST"]) == 1


def test_gate_nao_alcanca_leitura() -> None:
    ctx, _ = preparar_run_com_gate()
    chamar(ctx, "get_asset", {"asset_id": "asset_H110"})
    assert de_tipo(ctx, GateEvent) == []


def test_ligar_gate_recusa_observador_que_nao_guarda_eventos() -> None:
    """O `ObservadorNulo` serve a um servidor sem instrumentação, não a um com gate.

    Ligado a ele, o gate decidiria sempre sobre um histórico vazio: nenhuma permissão
    confirmada, nenhuma citação válida, nenhum status conhecido. Negaria tudo pelo motivo
    errado, e o trace registraria `permissao_nao_verificada` num ambiente em que a permissão
    tinha sido verificada.
    """
    ctx, _ = contexto(observador=ObservadorNulo())
    with pytest.raises(TypeError, match="não guarda os eventos"):
        ligar_gate(ctx, PolicyApprover())


# ---------------------------------------------------------------------------
# O trace em disco
# ---------------------------------------------------------------------------


def test_observador_de_trace_escreve_o_que_o_reader_le_de_volta(tmp_path: Path) -> None:
    writer = TraceWriter(tmp_path, "run_teste")
    ctx, _ = contexto(observador=ObservadorDeTrace(writer))
    ligar_gate(ctx, PolicyApprover())
    chamar(ctx, "get_current_user", {})
    chamar(ctx, "request_retraining", acao(justification=f"{JUSTIFICATIVA} (tc_01)"))

    lidos = read_trace(writer.trace_path)
    assert [evento.type for evento in lidos] == [
        "tool_call",
        "tool_result",
        "gate",
        "tool_call",
        "tool_result",
    ]
    assert [evento.seq for evento in lidos] == [1, 2, 3, 4, 5]
    # Mesmo conteúdo, ordem diferente: o `GateEvent` é escrito depois da chamada que ele
    # precede (ver `test_x20_a_ordem_do_arquivo_*`), e é `read_trace` quem reordena por `seq`.
    assert lidos == sorted(eventos(ctx), key=lambda evento: evento.seq)


# ---------------------------------------------------------------------------
# A prova do framework — cliente MCP externo, processo separado, stdio
# ---------------------------------------------------------------------------


def sem_relogio(evento: Any) -> dict[str, Any]:
    """O evento sem o que muda entre duas execuções da mesma run: relógio e latência."""
    dados = json.loads(evento.model_dump_json())
    dados.pop("ts", None)
    dados.pop("latencia_ms", None)
    return dados


def trace_da_bateria(tmp_path: Path) -> list[Any]:
    """A mesma run, pelo caminho da bateria: handler chamado direto, sem protocolo."""
    writer = TraceWriter(tmp_path / "memoria", "run_stdio")
    ctx, _ = contexto(run_id="run_stdio", observador=ObservadorDeTrace(writer))
    chamar(ctx, "get_current_user", {})
    chamar(ctx, "get_asset", {"asset_id": "asset_H110"})
    return read_trace(writer.trace_path)


@pytest.mark.lento
def test_cliente_mcp_externo_por_stdio_produz_o_mesmo_trace_da_bateria(tmp_path: Path) -> None:
    """A frase que sustenta a palavra *framework*, verificada em vez de afirmada.

    Um processo separado, falando o protocolo MCP por stdio, sem saber que existe trace: o
    cliente não registra handler nenhum, não pede nível de log e não coopera de forma alguma.
    O trace sai igual ao da bateria porque quem o escreve é o SERVIDOR — que é exatamente o
    ponto do `ARQUITETURA §4.1` ("o trace nasce aqui, sem o agente cooperar") e a razão de
    esta task ter abandonado o desenho por notificação (ver `mcp/instrumentacao.py`).
    """
    destino = tmp_path / "stdio"
    parametros = StdioServerParameters(
        command=sys.executable,
        args=[str(APOIO_STDIO), str(destino), "run_stdio"],
    )
    colhido: dict[str, Any] = {}

    async def conversar() -> None:
        async with stdio_client(parametros) as (leitura, escrita):
            async with ClientSession(leitura, escrita) as sessao:
                await sessao.initialize()
                colhido["catalogo"] = len((await sessao.list_tools()).tools)
                await sessao.call_tool("get_current_user", {})
                colhido["ativo"] = await sessao.call_tool(
                    "get_asset", {"asset_id": "asset_H110"}
                )

    anyio.run(conversar)

    assert colhido["catalogo"] == 18
    assert colhido["ativo"].structured_content["tool_call_id"] == "tc_02"

    por_stdio = read_trace(destino / "traces" / "run_stdio.jsonl")
    assert [sem_relogio(e) for e in por_stdio] == [
        sem_relogio(e) for e in trace_da_bateria(tmp_path)
    ]


def test_o_apoio_de_stdio_nao_alcanca_a_rede() -> None:
    """O subprocesso usa `MockTransport`. Se alguém trocar por rede, o teste acima mediria a
    API do parceiro na máquina de quem rodou a suíte, e passaria ou falharia por acidente."""
    fonte = APOIO_STDIO.read_text(encoding="utf-8")
    assert "MockTransport" in fonte
    assert "http://api.invalida" in fonte


# ---------------------------------------------------------------------------
# Por que o desenho por notificação foi abandonado
# ---------------------------------------------------------------------------


@pytest.mark.filterwarnings("ignore::mcp.shared.exceptions.MCPDeprecationWarning")
def test_notificacao_de_log_chega_no_handshake_que_o_sdk_negocia_hoje() -> None:
    """O desenho do `PLANO` T14 **não** foi abandonado por estar quebrado — ele funciona.

    `ClientSession.initialize()` negocia no máximo `2025-11-25` (`LATEST_HANDSHAKE_VERSION`),
    e nas versões de handshake a entrega de `notifications/message` é incondicional. Registrar
    isto importa: a T14 trocou o desenho por uma razão de PREMISSA (o trace não pode depender
    de o cliente cooperar, `ARQUITETURA §4.1`) e por um prazo (o teste seguinte), não por uma
    quebra que qualquer um poderia refutar rodando o SDK.
    """
    recebidas: list[str] = []

    async def ao_chamar(
        ctx: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        await ctx.session.send_log_message(level="info", data={"seq": 1}, logger="trace")
        return types.CallToolResult(content=[types.TextContent(type="text", text="ok")])

    async def ao_listar(ctx: Any, params: Any) -> types.ListToolsResult:
        # O `ClientSession` valida o resultado contra o catálogo antes de devolvê-lo, então
        # a sonda precisa de um catálogo mesmo não sendo sobre catálogo nenhum.
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name="qualquer",
                    description="sonda",
                    input_schema={"type": "object", "properties": {}},
                )
            ]
        )

    servidor = Server(
        "sonda", version="0.1.0", on_call_tool=ao_chamar, on_list_tools=ao_listar
    )

    async def logando(params: Any) -> None:
        recebidas.append(str(params.data))

    async def conversar(meta: dict[str, Any] | None) -> None:
        async with create_client_server_memory_streams() as (cliente, servidor_streams):
            async with anyio.create_task_group() as grupo:

                async def servir() -> None:
                    await servidor.run(
                        *servidor_streams,
                        servidor.create_initialization_options(),
                        raise_exceptions=True,
                    )

                grupo.start_soon(servir)
                async with ClientSession(*cliente, logging_callback=logando) as sessao:
                    await sessao.initialize()
                    await sessao.call_tool("qualquer", {}, meta=meta)
                grupo.cancel_scope.cancel()

    anyio.run(conversar, None)
    assert recebidas == ["{'seq': 1}"], "hoje chega, e sem o cliente pedir nada"

    anyio.run(conversar, {LOG_LEVEL_META_KEY: "info"})
    assert recebidas == ["{'seq': 1}", "{'seq': 1}"]


def test_no_protocolo_moderno_a_notificacao_de_log_e_descartada_em_silencio() -> None:
    """O prazo de validade do desenho do plano, medido na função que decide a entrega.

    `2026-07-28` é o `LATEST_PROTOCOL_VERSION` deste SDK (alcançado por `discover()`, não pelo
    `initialize()` do teste acima) e tornou `notifications/message` opt-in POR REQUISIÇÃO:
    sem `io.modelcontextprotocol/logLevel` no `_meta`, o conjunto de níveis entregáveis é
    VAZIO e `send_log_message` descarta — não levanta, não avisa. A capacidade inteira está
    marcada deprecada (SEP-2577).

    Um trace construído sobre isso ficaria vazio no dia em que um cliente adotasse o protocolo
    moderno, e trace vazio é indistinguível de uma run que não fez nada — o formato de falha
    do X9 e do X12. O teste é o alarme: quando o SDK mudar, ele fica vermelho.
    """
    assert allowed_log_levels(LATEST_PROTOCOL_VERSION, None) == frozenset()
    assert "info" in allowed_log_levels(LATEST_PROTOCOL_VERSION, {LOG_LEVEL_META_KEY: "info"})
    assert allowed_log_levels("2025-11-25", None), "no handshake continua incondicional"
