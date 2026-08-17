"""
Servidor MCP — a fronteira por onde toda chamada do agente passa.

POR QUE UM SERVIDOR MCP E NÃO TOOLS NO PROCESSO DO AGENTE (`ARQUITETURA §4.1`)
    Ponto único de instrumentação (o trace nasce aqui, sem o agente cooperar), ponto único de
    controle (cache, classificação de status, injeção de falha, catálogo e — via T15 — gate),
    e a propriedade que sustenta a palavra *framework*: qualquer cliente MCP vira SUT
    instrumentado, inclusive um agente de terceiro apontado para este servidor por stdio.

UM SERVIDOR POR RUN
    `criar_servidor(ctx)` recebe o `RunContext` da run e devolve um servidor amarrado a ele.
    Cache, contador de `tool_call_id` e `seq` vivem no contexto, então dois servidores nunca
    se enxergam. Isso não é higiene: cache compartilhado entre runs faria a segunda célula da
    matriz parecer mais eficiente que a primeira, e a comparação viraria artefato da ordem em
    que a bateria rodou.

O QUE ESTE MÓDULO NÃO FAZ — e onde as outras tasks entram
    * **Persistência de trace (T14).** O servidor emite `ToolCall`/`ToolResult` para um
      `Observador` injetável, cujo padrão é nulo. Quem escreve no `TraceWriter` é o
      `ObservadorDeTrace` de `mcp/instrumentacao.py`. O servidor não conhece `TraceWriter` —
      se conhecesse, testá-lo exigiria disco.
    * **Gate de ação (T15).** O gancho é `RunContext.antes_da_acao`, consultado em
      `chamar_tool` imediatamente antes da requisição HTTP e só para tool de escrita. Nenhuma
      política mora aqui: o padrão é `None` = sem gate. Quem traduz um `Approver` para este
      contrato é a T14 (`mcp/instrumentacao.py::PoliticaComGate`).
    * **Variantes de catálogo (T17).** `RunContext.tools_ocultas` já filtra `list_tools` e
      `call_tool`; quem decide o que ocultar é o `VariantConfig`, que não é desta task.

O QUE O MODELO ENXERGA (`ARQUITETURA §4.3`)
    O corpo da API **verbatim** — `mode`, `notes` e `data` como vieram — mais o
    `tool_call_id`, que ele precisa para citar evidência. Nada de latência, status
    classificado ou `cache_hit`: telemetria no contexto faz o agente raciocinar sobre a
    própria instrumentação. A `notes` enganosa e os marcadores `conflict`/`inconclusive`
    passam intactos porque são o ambiente que o corpus mede — editá-los mudaria o gabarito
    de CEN-06, CEN-08 e da armadilha de "indisponibilidade temporária".
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import mcp_types as types
from mcp.server.lowlevel import Server

from tapieval.env.client import RawResponse, TractianClient
from tapieval.env.faults import FaultInjector
from tapieval.env.status import Classificacao, classificar
from tapieval.mcp.tools import (
    Operacao,
    carregar_operacoes,
    tools_visiveis,
    validar_argumentos,
)
from tapieval.schema.trace import RunError, ToolCall, ToolResult, TraceEvent

NOME_DO_SERVIDOR = "tapieval-tractian"

TAMANHO_MAXIMO_DO_BODY_INLINE = 8_192
"""Acima disto o `ToolResult` guarda só o `body_sha` (`ARQUITETURA §5`, decisão 6).

Mantém o JSONL leve para pandas. O corte é por JSON serializado, não por número de campos:
uma série de RMS com mil amostras tem poucos campos e é o payload mais pesado do corpus."""


# ---------------------------------------------------------------------------
# Costuras injetáveis
# ---------------------------------------------------------------------------


class Observador(Protocol):
    """Quem recebe os eventos que o servidor emite. A T14 pluga o adaptador de trace aqui.

    Deliberadamente estreito: recebe eventos já montados e não devolve nada. O servidor não
    pode depender de o observador funcionar — uma falha de escrita de trace não pode alterar
    o que o agente recebeu, senão a instrumentação vira parte do experimento.
    """

    def emitir(self, evento: TraceEvent) -> None: ...


class ObservadorNulo:
    """O padrão. Servidor sem instrumentação continua sendo um servidor correto."""

    def emitir(self, evento: TraceEvent) -> None:
        return None


class ObservadorEmMemoria:
    """Duplo de teste — e o que a T14 vai substituir. Guarda os eventos em ordem de emissão."""

    def __init__(self) -> None:
        self.eventos: list[TraceEvent] = []

    def emitir(self, evento: TraceEvent) -> None:
        self.eventos.append(evento)


class PoliticaDeAcao(Protocol):
    """O gancho do gate (T15). `None` libera; uma string é o motivo da negação.

    O tipo é uma função e não o `Approver` de `mcp/gate.py` de propósito: o servidor não
    importa o gate. A T14 escreve o adaptador que traduz o veredito do `Approver` para este
    contrato, e é lá que o `GateEvent` é emitido — o servidor só garante que nada de escrita
    atravessa sem passar por aqui primeiro.

    OS DOIS ARGUMENTOS NOMEADOS EXISTEM PARA O EVENTO DO GATE, NÃO PARA A DECISÃO
        `seq` é o número **reservado** para o `GateEvent` antes de o `ToolCall` ser emitido
        (ver `chamar_tool`); sem ele o evento nasceria depois da chamada que ele autoriza e
        `n1._coberta_por_gate` leria toda ação aprovada como não coberta. `tool_call_id` é
        o id da chamada em curso, e serve para a política **excluí-lo** das citações
        disponíveis: citar a própria chamada que ainda não voltou não é fundamentar nada.
    """

    def __call__(
        self, tool_name: str, args: Mapping[str, Any], *, seq: int, tool_call_id: str
    ) -> str | None: ...


# ---------------------------------------------------------------------------
# Contexto da run
# ---------------------------------------------------------------------------


class _Contador:
    """Contador monotônico simples. Existe para que `seq` e `tool_call_id` sejam do CONTEXTO.

    Se fossem do servidor, o harness do cliente — que emite `llm_call`, `budget`, `decision` e
    `final_answer` — não teria como participar da mesma sequência, e reconciliar os dois
    emissores por `seq` (`ARQUITETURA §4.3`) deixaria de ser possível.
    """

    def __init__(self) -> None:
        self._n = 0

    def proximo(self) -> int:
        self._n += 1
        return self._n


@dataclass
class RunContext:
    """Tudo que uma run precisa para atravessar a fronteira MCP. Um por run, sem exceção.

    Pequeno e explícito de propósito: cada campo aqui é uma coisa que muda entre células da
    matriz de experimentos, e o que não muda não entra.
    """

    run_id: str
    cliente: TractianClient
    """O cliente HTTP da run. Carrega `seed` e `x-user-id`, que são da run inteira."""

    injetor_de_falhas: FaultInjector | None = None
    """Só as falhas que a API não produz (`env/faults.py`). `None` = ambiente limpo."""

    observador: Observador = field(default_factory=ObservadorNulo)
    antes_da_acao: PoliticaDeAcao | None = None
    tools_ocultas: frozenset[str] = frozenset()

    iteracao_atual: int = 0
    """A volta do loop ReAct, escrita pelo harness antes de cada iteração.

    O servidor não enxerga o loop do agente (`§4.3`) e não tem como derivá-la; deixá-la sempre
    em zero tornaria `n_iteracoes` da N2 incalculável a partir dos eventos do servidor."""

    cache: dict[str, tuple[RawResponse, Classificacao]] = field(default_factory=dict)
    """Cache de LEITURA da run, chaveado por `sha256(tool + args ordenados)`.

    `default_factory` e não um default compartilhado: um dicionário no nível da classe seria
    global, e um cache global entre runs falsificaria a métrica de chamadas redundantes."""

    sequencia: _Contador = field(default_factory=_Contador)
    chamadas: _Contador = field(default_factory=_Contador)

    @property
    def env_seed(self) -> str | None:
        """A semente do ambiente. Vive no cliente, que é quem a injeta em toda query.

        Exposta aqui por conveniência e como propriedade, nunca como campo próprio: duas
        cópias da semente divergiriam em silêncio, e divergir aqui significa a run inteira
        rodar num ambiente diferente do que o manifesto declara.
        """
        return self.cliente.seed

    def proximo_tool_call_id(self) -> str:
        return f"tc_{self.chamadas.proximo():02d}"

    def _cabecalho(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "seq": self.sequencia.proximo(),
            "ts": datetime.now(UTC),
            "iteration": self.iteracao_atual,
        }


# ---------------------------------------------------------------------------
# Catálogo
# ---------------------------------------------------------------------------


def listar_tools(ctx: RunContext) -> list[types.Tool]:
    """O catálogo servido por `list_tools`. Fonte única: o contrato OpenAPI.

    O agente **descobre** as tools aqui e nunca declara uma lista paralela — duas fontes de
    verdade divergem em silêncio e matam o experimento (`ARQUITETURA §4.2`).
    """
    return [
        types.Tool(
            name=operacao.nome,
            description=operacao.descricao,
            input_schema=operacao.input_schema(),
        )
        for operacao in tools_visiveis(ctx.tools_ocultas)
    ]


def _operacao_visivel(ctx: RunContext, nome: str) -> Operacao | None:
    if nome in ctx.tools_ocultas:
        return None
    return carregar_operacoes().get(nome)


# ---------------------------------------------------------------------------
# Execução de uma tool
# ---------------------------------------------------------------------------


async def chamar_tool(
    ctx: RunContext, nome: str, args: Mapping[str, Any] | None
) -> types.CallToolResult:
    """Uma chamada de tool, da validação ao `ToolResult`.

    `async` porque é o que o SDK exige do handler, mas o corpo é síncrono: o `TractianClient` é
    síncrono e uma run é sequencial por definição — o agente decide a próxima chamada depois de
    ver a anterior. Fingir concorrência aqui só acrescentaria uma fonte de não-determinismo a
    um framework cujo valor inteiro está em "mesmo trace, mesmo score".
    """
    args = dict(args or {})

    operacao = _operacao_visivel(ctx, nome)
    if operacao is None:
        # Não emite `tool_call`: nenhuma tool foi chamada. Emitir inventaria uma `tools_extras`
        # na N1.1 e uma chamada gasta na N2 para um nome que o catálogo nunca ofereceu.
        return _erro(f"tool desconhecida: {nome!r}. Use `list_tools` para ver o catálogo.")

    erro_de_args = validar_argumentos(operacao, args)

    # ---- RESERVA DO `seq` DO GATE (X20) ---------------------------------------------
    # `n1._coberta_por_gate` exige `gate.seq < tool_call.seq`, e o gate só pode DECIDIR
    # depois de o `ToolCall` existir (ele é emitido mesmo quando a ação é negada — sem isso o
    # agente que tenta o retreinamento proibido pontuaria igual ao que corretamente recusou).
    # As duas exigências só coexistem separando a NUMERAÇÃO da EMISSÃO: o número do gate é
    # tirado aqui, o evento é emitido lá embaixo. Reservar sem emitir abriria lacuna em `seq`
    # — que `ARQUITETURA §5` decisão 9 diz invalidar a run —, então a reserva acontece
    # exatamente quando o gate vai ser consultado, e não quando ele *poderia* ser.
    passa_pelo_gate = (
        erro_de_args is None and operacao.alto_impacto and ctx.antes_da_acao is not None
    )
    seq_do_gate = ctx.sequencia.proximo() if passa_pelo_gate else None

    tool_call_id = ctx.proximo_tool_call_id()
    ctx.observador.emitir(
        ToolCall(
            **ctx._cabecalho(),
            tool_call_id=tool_call_id,
            tool_name=nome,
            args=args if erro_de_args is None else {},
            args_raw=args,
            args_validos=erro_de_args is None,
            args_erro=erro_de_args,
        )
    )
    if erro_de_args is not None:
        # Sem HTTP não há resposta a classificar, então NÃO sai `ToolResult`: um par
        # call/result completo diria que a API respondeu algo, e ela não foi consultada.
        # A N1.2 já trata `args_validos=False` como reprovação (`scoring/n1.py`).
        return _erro(f"argumentos inválidos para {nome}: {erro_de_args}")

    if passa_pelo_gate:
        # ---- PONTO DE EXTENSÃO DO GATE (T15, fiado pela T14) ------------------------
        # Última linha antes da requisição, e só para escrita. Permissão e justificativa são
        # checadas ANTES de tentar a ação, nunca depois do 403 (`ARQUITETURA §3.7`): não há
        # desfazer, e descobrir a falta de permissão por erro da API já é a falha P4.
        assert ctx.antes_da_acao is not None and seq_do_gate is not None  # noqa: S101
        motivo = ctx.antes_da_acao(nome, args, seq=seq_do_gate, tool_call_id=tool_call_id)
        if motivo is not None:
            # Sem `ToolResult`, pelo mesmo motivo dos args inválidos: a API não foi consultada.
            # O par que o scorer lê é `gate(negado)` + `tool_call` sem resultado.
            return _erro(f"ação bloqueada: {motivo}")

    try:
        resposta, classificacao, veio_do_cache = _obter(ctx, operacao, args)
    except Exception as erro:
        # A chamada que morre antes de virar `tool_result` (A6, 15/08). Sem este evento o
        # trace ficaria com um `ToolCall` órfão e o scorer leria "o agente pediu e nada
        # voltou" como evidência ausente do agente, quando é falha do instrumento.
        #
        # Emite e RELEVANTA. Falha de transporte já vira `RawResponse` no cliente (T2,
        # decisão 2), então o que chega aqui é bug de programação — engoli-lo devolveria
        # `INDISPONIVEL` ao agente e contaminaria a métrica de robustez com defeito nosso.
        # Quem isola a run é o runner (T18): "erro numa run não derruba a bateria".
        ctx.observador.emitir(
            RunError(
                **ctx._cabecalho(),
                onde=nome,
                classe=type(erro).__name__,
                mensagem=str(erro),
                fatal=True,
            )
        )
        raise

    ctx.observador.emitir(
        ToolResult(
            **ctx._cabecalho(),
            tool_call_id=tool_call_id,
            status=classificacao.status,
            http_status=resposta.status_code,
            latencia_ms=0 if veio_do_cache else resposta.latencia_ms,
            cache_hit=veio_do_cache,
            campos_ausentes=classificacao.campos_ausentes,
            **_corpo_do_evento(resposta.body),
        )
    )
    return _resposta_ao_agente(tool_call_id, resposta.body)


def _obter(
    ctx: RunContext, operacao: Operacao, args: Mapping[str, Any]
) -> tuple[RawResponse, Classificacao, bool]:
    """A resposta e sua classificação, do cache da run ou da API.

    **Só leitura é cacheada.** Cachear uma escrita esconderia a segunda execução de uma ação
    irreversível; idempotência de ação é do gate, com chave própria e acumulativa
    (`ARQUITETURA §3.7`), e confundir os dois mecanismos faria o cache decidir se uma ação
    roda — o que ele não tem informação nenhuma para decidir.

    O cache é consultado ANTES do injetor de falhas: o injetor conta ocorrências, e um
    cache-hit que avançasse o contador faria um agente que repete chamadas mudar o ambiente
    que ele mesmo enfrenta depois.
    """
    cacheavel = not operacao.alto_impacto
    chave = _chave_de_cache(operacao.nome, args)

    if cacheavel and chave in ctx.cache:
        resposta, classificacao = ctx.cache[chave]
        return resposta, classificacao, True

    resposta = _executar(ctx, operacao, args)
    if ctx.injetor_de_falhas is not None:
        resposta = ctx.injetor_de_falhas.aplicar(operacao.nome, resposta)

    classificacao = classificar(operacao.nome, resposta)
    if cacheavel:
        ctx.cache[chave] = (resposta, classificacao)
    return resposta, classificacao, False


def _executar(ctx: RunContext, operacao: Operacao, args: Mapping[str, Any]) -> RawResponse:
    """Exatamente uma requisição HTTP por chamada de tool. Sem exceção, sem agregação.

    É medição: uma tool que fizesse duas chamadas destruiria a contagem de eficiência da N2 e
    tornaria `tools_esperadas` do corpus incomparável com o que o agente fez.
    """
    caminho = operacao.montar_caminho(args)
    if operacao.metodo == "get":
        return ctx.cliente.get(caminho, operacao.montar_query(args))
    if operacao.metodo == "post":
        return ctx.cliente.post(caminho, operacao.montar_corpo(args))
    if operacao.metodo == "patch":
        return ctx.cliente.patch(caminho, operacao.montar_corpo(args))
    raise NotImplementedError(
        f"{operacao.nome}: método {operacao.metodo.upper()} não existe no contrato hoje. "
        "Verbo novo tem de quebrar aqui, não cair num ramo silencioso."
    )


def _chave_de_cache(nome: str, args: Mapping[str, Any]) -> str:
    """`sha256(tool + args ordenados)`. A ordem dos argumentos não é parte da identidade."""
    material = f"{nome}:{json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Formatação
# ---------------------------------------------------------------------------


def _corpo_do_evento(corpo: Any) -> dict[str, Any]:
    """`body` inline quando pequeno; `body_sha` quando grande ou quando não é objeto.

    Corpo que não é dicionário acontece — `corpo_malformado` devolve HTML, e um proxy no
    caminho faz o mesmo em produção. `ToolResult.body` é `dict | None` por schema congelado,
    então o texto cru vira `body_sha`: perder o rastro dele cegaria o diagnóstico justamente
    no caso em que a resposta não fez sentido.
    """
    if isinstance(corpo, dict):
        serializado = json.dumps(corpo, sort_keys=True, ensure_ascii=False, default=str)
        if len(serializado) <= TAMANHO_MAXIMO_DO_BODY_INLINE:
            return {"body": corpo}
    else:
        serializado = str(corpo)
    return {"body": None, "body_sha": hashlib.sha256(serializado.encode("utf-8")).hexdigest()}


def _resposta_ao_agente(tool_call_id: str, corpo: Any) -> types.CallToolResult:
    """O que volta ao modelo: o corpo da API verbatim + o `tool_call_id`. Nada mais.

    Erro HTTP NÃO vira `is_error`: 404 de ativo inexistente e 403 sem permissão são fatos do
    ambiente que os cenários exercitam de propósito (aut_05, aut_08), e o agente precisa
    raciocinar sobre eles. `is_error` fica para o que impediu a chamada de acontecer.
    """
    conteudo = corpo if isinstance(corpo, dict) else {"data": corpo}
    estruturado = {"tool_call_id": tool_call_id, **conteudo}
    texto = json.dumps(estruturado, ensure_ascii=False, default=str)
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=texto)],
        structured_content=estruturado,
        is_error=False,
    )


def _erro(mensagem: str) -> types.CallToolResult:
    """Erro reportado DENTRO do resultado, não como erro de protocolo.

    É o que o MCP recomenda e o que este framework precisa: o agente tem de ver o erro para
    se corrigir, e a tentativa malsucedida tem de continuar contando no orçamento.
    """
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=mensagem)], is_error=True
    )


# ---------------------------------------------------------------------------
# O servidor
# ---------------------------------------------------------------------------


def criar_servidor(ctx: RunContext) -> Server[Any]:
    """O servidor MCP desta run. Mesmo código nos dois transportes (`ARQUITETURA §4.4`).

    Streams em memória na bateria — um servidor por run, isolamento perfeito de cache e chaves
    de idempotência sem custo de processo — e stdio na demo, que é como um cliente MCP externo
    (ou um agente de terceiro) se conecta. A escolha é de quem chama `Server.run`: este módulo
    não conhece transporte.
    """

    async def ao_listar(
        _ctx: Any, _params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        return types.ListToolsResult(tools=listar_tools(ctx))

    async def ao_chamar(
        _ctx: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        return await chamar_tool(ctx, params.name, params.arguments)

    return Server(
        NOME_DO_SERVIDOR,
        version="0.1.0",
        instructions=(
            "API industrial de manutenção preditiva. Uma tool por endpoint: nenhuma delas "
            "agrega chamadas, então investigar exige compor várias. Toda ação de impacto "
            "exige `justification` com pelo menos 20 caracteres."
        ),
        on_list_tools=ao_listar,
        on_call_tool=ao_chamar,
    )
