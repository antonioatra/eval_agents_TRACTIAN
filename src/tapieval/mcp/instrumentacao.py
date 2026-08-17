"""
Instrumentação da fronteira MCP — onde o trace de fato nasce e onde o gate é fiado.

O QUE MUDOU EM RELAÇÃO AO PLANO, E POR QUÊ (17/08)
    O `PLANO` T14 e o `ARQUITETURA §4.3` mandavam o servidor emitir os eventos como *logging
    notification* MCP, para o cliente escrever no `TraceWriter` do outro lado. **Aqui o
    `ObservadorDeTrace` escreve direto no `TraceWriter`, do lado do servidor**; a notificação
    não foi implementada.

    A razão principal não é técnica, é de premissa: o desenho por notificação contradiz a
    razão de existir da fronteira MCP (`ARQUITETURA §4.1`) — *"o trace nasce aqui, sem o
    agente cooperar"*. Trace que viaja por notificação **exige** que o cliente coopere: que
    ele registre um `message_handler` e escreva o que chegar. Um agente de terceiro apontado
    para este servidor não faz nem uma coisa nem outra, e produziria trace VAZIO — que é
    indistinguível de uma run que não fez nada. Mesmo formato de falha do X9/X12/X14, na
    direção que favorece a conclusão que o trabalho quer defender. Escrevendo do lado do
    servidor, a prova de *"o framework avalia qualquer agente MCP"* fica mais forte, não mais
    fraca: o cliente externo não precisa nem saber que existe trace
    (`tests/test_mcp_trace.py::test_cliente_mcp_externo_por_stdio_*`).

    É também o que o próprio `schema/writer.py` já assumia ao documentar dois emissores
    escrevendo no mesmo `.jsonl` (X8). O texto "por notificação" da docstring dele era o único
    lugar em que os dois desenhos coexistiam — e eles não fecham: por notificação existiria um
    escritor só, o do cliente.

    A SEGUNDA RAZÃO É UM PRAZO, E FOI MEDIDA ANTES DE SER ESCRITA
        Hoje a notificação **funciona**: `ClientSession.initialize()` negocia no máximo
        `2025-11-25` (`LATEST_HANDSHAKE_VERSION`), e nas versões de handshake a entrega de
        `notifications/message` é incondicional. Mas o protocolo moderno — `2026-07-28`, o
        `LATEST_PROTOCOL_VERSION` deste SDK, alcançado por `ClientSession.discover()` — tornou
        a entrega **opt-in por requisição**: sem a chave `io.modelcontextprotocol/logLevel` no
        `_meta` da chamada, `send_log_message` DESCARTA, sem levantar e sem avisar
        (`mcp/server/connection.py::allowed_log_levels`), e a capacidade inteira está marcada
        deprecada (SEP-2577). Construir o trace sobre ela seria construir sobre uma peça com
        data de validade cuja falha é silenciosa. Os dois comportamentos — o de hoje e o do
        protocolo moderno — estão fixados em teste, para que o dia em que isso mudar seja um
        teste vermelho e não um trace vazio.

    O QUE SE PERDE
        O `seq` compartilhado entre os dois emissores quando eles são PROCESSOS diferentes: em
        stdio o servidor numera o que ele emite e o harness numera o que ele emite, e as duas
        séries colidem. Na bateria não acontece — transporte em memória, um `RunContext` só,
        um contador só (`ARQUITETURA §4.4`). Registrado como dívida da T16/T18.

A ORDEM DO `seq` DO GATE (X20)
    `n1._coberta_por_gate` exige `gate.seq < tool_call.seq`, e o `ToolCall` é emitido **antes**
    do ponto de gate — de propósito, senão a ação negada não deixaria rastro. A conciliação
    está em `server.chamar_tool`, que RESERVA o número do gate antes de emitir o `ToolCall` e
    o entrega à política; `PoliticaComGate` só o gasta quando o `GateEvent` sai de fato.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

from tapieval.env.status import TOOLS_SEM_ENVELOPE
from tapieval.mcp.gate import (
    Approver,
    ContextoDaDecisao,
    GateDeAcao,
    Justificativa,
    RegistroDoGate,
    construir_gate_event,
)
from tapieval.mcp.server import RunContext
from tapieval.schema.trace import (
    RunStart,
    StatusRetorno,
    ToolCall,
    ToolResult,
    TraceEvent,
)
from tapieval.schema.writer import TraceWriter
from tapieval.scoring.estado import derivar_estado

PADRAO_DE_CITACAO = re.compile(r"\btc_\d+\b")
"""O `tool_call_id` como o modelo o vê. É a chave de citação (`PLANO` T14, prova 2).

Extrair o id do texto **não** é a heurística de palavra-chave que `ARQUITETURA §3.3` proíbe:
`tc_07` é um identificador que o próprio servidor entregou ao modelo, não uma inferência
sobre o que ele quis dizer. O contrato da API não tem campo de citação em nenhuma das cinco
ações (só `justification`, `params` e, no `update_asset_config`, `changes`) — conferido em
`mcp/tools.py` —, então o texto da justificativa é o único canal que existe."""


# ---------------------------------------------------------------------------
# Observador
# ---------------------------------------------------------------------------


class HistoricoDeEventos(Protocol):
    """O que o gate precisa de um observador: os eventos já emitidos nesta run.

    Estreito de propósito — `ObservadorEmMemoria` (o duplo de teste do `server.py`) satisfaz
    este protocolo sem tocar em disco, então a fiação do gate é testável sem `TraceWriter`.
    """

    eventos: Sequence[TraceEvent]


class ObservadorDeTrace:
    """O `Observador` da bateria: persiste no `TraceWriter` e guarda o histórico da run.

    As duas responsabilidades andam juntas porque são a mesma lista vista de dois lugares —
    o arquivo é o registro, e o histórico em memória é o que o gate consulta para decidir.
    Mantê-las separadas obrigaria a reler o `.jsonl` a cada ação de alto impacto.

    FALHA DE ESCRITA SOBE, E ISSO É DELIBERADO
        A docstring de `server.Observador` diz que o servidor não pode depender do observador
        funcionar — e não depende: o servidor não trata exceção nenhuma daqui, quem trata é o
        runner. Mas engolir erro de escrita produziria uma run que o agente completou e que o
        instrumento não registrou, que é exatamente o que este projeto passa o tempo todo
        tentando não fazer. Trace incompleto tem de derrubar a run, não a pontuar pela metade.
    """

    def __init__(self, writer: TraceWriter) -> None:
        self.writer = writer
        self.eventos: list[TraceEvent] = []

    def emitir(self, evento: TraceEvent) -> None:
        self.eventos.append(evento)
        self.writer.emit(evento)


# ---------------------------------------------------------------------------
# O contexto que o gate consome
# ---------------------------------------------------------------------------


def extrair_citacoes(texto: str | None) -> tuple[str, ...]:
    """Os `tool_call_id` citados num texto, na ordem em que aparecem e sem repetir.

    Sem repetir porque citar duas vezes a mesma evidência não é citar duas evidências, e
    `citacoes_invalidas` devolveria o mesmo id duas vezes no motivo da negação.
    """
    if not texto:
        return ()
    vistos: dict[str, None] = {}
    for citacao in PADRAO_DE_CITACAO.findall(texto):
        vistos.setdefault(citacao, None)
    return tuple(vistos)


def montar_contexto_da_decisao(
    eventos: Sequence[TraceEvent],
    *,
    args_da_acao: Mapping[str, Any] | None = None,
    ignorar_tool_call_id: str | None = None,
) -> ContextoDaDecisao:
    """O `ContextoDaDecisao` do instante, montado do que o servidor já observou.

    `ContextoDaDecisao` existe (T15) porque `EstadoObservado` sozinho não sustenta a decisão
    do gate; esta função é o "quem monta é a T14" que a docstring dele promete.

    `ignorar_tool_call_id` é o id da chamada EM CURSO. Ele é excluído das citações
    disponíveis: a ação não pode se fundamentar em si mesma. Sem a exclusão, uma justificativa
    que cita o próprio `tc_NN` passaria pela checagem de citação fantasma — e em
    `escalate_case`, que é isento da checagem de evidência degradada, passaria inteira.
    """
    estado = derivar_estado(list(eventos))

    nome_por_id = {
        evento.tool_call_id: evento.tool_name
        for evento in eventos
        if isinstance(evento, ToolCall)
    }
    ids = frozenset(nome_por_id) - {ignorar_tool_call_id}
    status_por_id: dict[str, StatusRetorno] = {
        evento.tool_call_id: evento.status
        for evento in eventos
        if isinstance(evento, ToolResult) and evento.tool_call_id in ids
    }

    permissoes, empresa_usuario = _do_usuario(eventos, nome_por_id)
    return ContextoDaDecisao(
        estado=estado,
        tool_call_ids=ids,
        permissoes_usuario=permissoes,
        status_por_tool_call=status_por_id,
        empresa_usuario=empresa_usuario,
        empresa_do_alvo=_empresa_do_alvo(eventos, nome_por_id, args_da_acao or {}),
    )


def _do_usuario(
    eventos: Sequence[TraceEvent], nome_por_id: Mapping[str, str]
) -> tuple[frozenset[str] | None, str | None]:
    """Permissões e empresa do usuário, do último `get_current_user` que trouxe corpo.

    `None` nos dois quando a tool nunca foi chamada, quando ela falhou, ou quando o corpo foi
    para blob por tamanho. `None` **não** é "sem permissão": é "não verificada", e o gate é
    fail-closed, então as duas negam — mas por motivos diferentes no trace
    (`permissao_nao_verificada` × `permissao_ausente:<perm>`), que é a distinção que o revisor
    humano precisa ver. Ler o último e não o primeiro é irrelevante hoje (a resposta é
    determinística e cacheada por run) e correto se um dia deixar de ser.

    `GET /users/me` vem SEM envelope — a API devolve a linha do usuário crua
    (`env/status.py::TOOLS_SEM_ENVELOPE`) —, então os campos estão no primeiro nível do corpo.
    """
    for evento in reversed(list(eventos)):
        if not isinstance(evento, ToolResult):
            continue
        if nome_por_id.get(evento.tool_call_id) != "get_current_user":
            continue
        if evento.status == "INDISPONIVEL" or not isinstance(evento.body, dict):
            continue
        permissoes = evento.body.get("permissions")
        if not isinstance(permissoes, list):
            continue
        return frozenset(str(item) for item in permissoes), _texto(
            evento.body.get("company_id")
        )
    return None, None


def _empresa_do_alvo(
    eventos: Sequence[TraceEvent],
    nome_por_id: Mapping[str, str],
    args_da_acao: Mapping[str, Any],
) -> str | None:
    """A empresa dona do ativo em que a ação mexe — quando o servidor já a observou.

    O alvo é o `asset_id` que a própria ação nomeia; quando ela não nomeia nenhum
    (`escalate_case`, `reprocess_analysis`), cai para o ativo da run, declarado no `RunStart`.
    A queda é o que faz o D5 alcançar as ações que não falam de ativo diretamente: `aut_04` é
    sobre operar em ativo alheio, não sobre qual verbo foi usado.

    `None` quando o ativo nunca foi lido — e aí o gate não tem opinião, por desenho
    (`gate._fora_de_escopo`). Inventar "mesma empresa" por ausência de dado seria abrir a
    porta pelo lado de fora; inventar "outra empresa" reprovaria toda ação de run que não
    chamou `get_asset`.
    """
    alvo = _texto(args_da_acao.get("asset_id")) or _asset_da_run(eventos)
    if alvo is None:
        return None

    for evento in reversed(list(eventos)):
        if not isinstance(evento, ToolResult) or not isinstance(evento.body, dict):
            continue
        if nome_por_id.get(evento.tool_call_id) in TOOLS_SEM_ENVELOPE:
            continue
        dados = evento.body.get("data")
        if isinstance(dados, dict) and _texto(dados.get("id")) == alvo:
            return _texto(dados.get("company_id"))
    return None


def _asset_da_run(eventos: Sequence[TraceEvent]) -> str | None:
    for evento in eventos:
        if isinstance(evento, RunStart):
            return evento.asset_id
    return None


def _texto(valor: Any) -> str | None:
    return str(valor) if isinstance(valor, str) and valor else None


# ---------------------------------------------------------------------------
# A fiação do gate
# ---------------------------------------------------------------------------


class ExecucaoNoServidor:
    """`Executor` nulo: quem faz a requisição é o servidor, na linha seguinte ao gate liberar.

    O `GateDeAcao` (T15) foi desenhado para decidir **e** executar, porque nasceu sem o
    servidor. Ligar os dois dando a ele um executor de verdade poria duas chamadas HTTP na
    mesma tool — e "exatamente uma requisição HTTP por chamada de tool" é o invariante que
    sustenta a contagem de eficiência da N2 (`server._executar`).

    O que o gate precisa garantir sozinho ele continua garantindo: a chave de idempotência é
    QUEIMADA antes de `executar` retornar, então o retry de uma ação já aplicada é negado
    mesmo que o HTTP que veio depois tenha estourado. Não há desfazer (`§3.7`).
    """

    def executar(self, acao: str, args: dict[str, Any]) -> None:
        return None


class PoliticaComGate:
    """Adaptador `Approver` → `server.PoliticaDeAcao`. É aqui que o `GateEvent` sai.

    Faz três coisas que nenhuma das duas pontas podia fazer sozinha: monta o
    `ContextoDaDecisao` a partir do que o servidor observou, gasta o `seq` que `chamar_tool`
    reservou (X20) e traduz o veredito para o `str | None` que o servidor entende.
    """

    def __init__(
        self,
        gate: GateDeAcao,
        observador: HistoricoDeEventos,
        ctx: RunContext,
    ) -> None:
        self.gate = gate
        self.observador = observador
        self.ctx = ctx
        self._seq_reservado: int | None = None

    def __call__(
        self,
        tool_name: str,
        args: Mapping[str, Any],
        *,
        seq: int,
        tool_call_id: str,
    ) -> str | None:
        justificativa = str(args.get("justification") or "")
        contexto = montar_contexto_da_decisao(
            self.observador.eventos,
            args_da_acao=args,
            ignorar_tool_call_id=tool_call_id,
        )

        self._seq_reservado = seq
        try:
            resultado = self.gate.submeter(
                tool_name,
                args,
                Justificativa(texto=justificativa, citacoes=extrair_citacoes(justificativa)),
                contexto,
            )
        finally:
            # Reserva não gasta é lacuna em `seq`, que invalida a run (`ARQUITETURA §5`,
            # decisão 9). Se `submeter` levantar antes de emitir, a lacuna já existe e o
            # `seq` pendurado aqui faria a PRÓXIMA ação reusar um número — dois eventos com
            # o mesmo `seq` é pior que um buraco, porque o reader ordena e não acusa.
            self._seq_reservado = None

        return None if resultado.veredito.aprovado else resultado.veredito.motivo

    def emitir_gate(self, registro: RegistroDoGate) -> None:
        """`EmissorDeGate` (T15): registro + envelope = `GateEvent` no trace.

        O envelope é montado à mão em vez de sair de `ctx._cabecalho()`, e a diferença não é
        estilo: `_cabecalho` TIRA um `seq` novo do contador. Chamá-lo aqui gastaria um número
        que nunca viraria evento — lacuna em `seq`, que `ARQUITETURA §5` decisão 9 diz
        invalidar a run — e ainda por cima daria ao gate a ordem errada que o X20 descreve.
        `ts` e `iteration` são leitura direta e não têm esse efeito colateral.
        """
        if self._seq_reservado is None:
            raise RuntimeError(
                "emitir_gate sem `seq` reservado: o número do GateEvent é tirado em "
                "`server.chamar_tool`, antes do ToolCall (X20). Emitir fora do fluxo do "
                "servidor produziria `gate.seq > tool_call.seq` e reprovaria ação aprovada."
            )
        self.ctx.observador.emitir(
            construir_gate_event(
                registro,
                run_id=self.ctx.run_id,
                seq=self._seq_reservado,
                ts=datetime.now(UTC),
                iteration=self.ctx.iteracao_atual,
            )
        )


def ligar_gate(
    ctx: RunContext,
    approver: Approver,
    *,
    observador: HistoricoDeEventos | None = None,
) -> GateDeAcao:
    """Pluga um `Approver` no `RunContext`. Depois disto nenhuma escrita passa sem decisão.

    Devolve o `GateDeAcao` porque ele é quem guarda as chaves de idempotência da run — o
    runner precisa da referência para provar, no fim, que a segunda tentativa de uma ação
    irreversível não aconteceu.
    """
    historico = observador if observador is not None else ctx.observador
    if not hasattr(historico, "eventos"):
        raise TypeError(
            f"{type(historico).__name__} não guarda os eventos da run, e o gate decide a "
            "partir deles (permissões, citações, status). Use `ObservadorDeTrace` — o "
            "`ObservadorNulo` serve a um servidor sem instrumentação, não a um com gate."
        )

    politica = PoliticaComGate(
        GateDeAcao(approver, ExecucaoNoServidor()),
        historico,  # type: ignore[arg-type]
        ctx,
    )
    politica.gate.emissor = politica
    ctx.antes_da_acao = politica
    return politica.gate
