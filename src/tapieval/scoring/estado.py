"""
Derivação do estado observado — a ponte entre o trace e o gabarito relativo.

POR QUE ESTA FUNÇÃO É PURA
    A API industrial varia de propósito (TAPI §5.1), então não existe resposta certa fixa:
    o gabarito é uma FUNÇÃO do que a API devolveu naquela execução (`CENARIOS §2.1`).
    Essa função é `decisao_esperada(derivar_estado(trace))`. Se `derivar_estado` olhasse o
    relógio, o disco ou um cache de módulo, dois recálculos do mesmo trace poderiam
    discordar — e o princípio "trace imutável, scores recomputáveis" (`ARQUITETURA §5`,
    decisão 1) cairia sem sintoma visível. Por isso: sem `datetime.now`, sem I/O, sem
    estado global mutável, sem mutação da lista de entrada. `tests/test_estado.py`
    verifica isso pela árvore sintática, não só por igualdade de duas chamadas.

O QUE O TRACE SUSTENTA E O QUE NÃO SUSTENTA
    `EstadoObservado` responde "o que a API devolveu e o que o agente pediu". Ele NÃO
    responde "qual evidência era obrigatória neste cenário" nem "a ação pedida era
    tecnicamente correta" — isso é conhecimento do cenário (o YAML), não do trace. T9
    combina os dois; aqui não se inventa nenhum dos dois.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

# A direção do import é esta, e não a inversa: o gate é quem conhece a permissão de cada ação
# (é ele quem a consulta), e o servidor MCP tem de rodar sem o pacote de scoring. `gate.py`
# importa só `schema` e stdlib, então isto não arrasta dependência de servidor para o scorer.
from tapieval.mcp.gate import MOTIVO_PERMISSAO_AUSENTE, PERMISSAO_EXIGIDA
from tapieval.schema.trace import (
    EstadoObservado,
    GateEvent,
    Hydration,
    RunStart,
    StatusRetorno,
    ToolCall,
    ToolResult,
    TraceEvent,
)

# Espelha as operações POST/PATCH do `api-contract.openapi.yaml`, em snake_case do
# `operationId` (`CENARIOS §2.4`). Existe porque a intenção de ação é lida do que o agente
# FEZ, não do texto do usuário: `ARQUITETURA §3.3` é explícito que classificar intenção por
# palavra-chave dispara ação irreversível em falso positivo.
TOOLS_ALTO_IMPACTO: frozenset[str] = frozenset(
    {
        "update_asset_config",
        "reprocess_analysis",
        "request_specialist_analysis",
        "request_retraining",
        "escalate_case",
    }
)

# Ordem de degradação: quando a mesma tool devolve status diferentes na mesma run, o pior
# vence. Um estado otimista viraria under-escalation na N1.4, que é o erro mais caro.
_DEGRADACAO: tuple[StatusRetorno, ...] = (
    "COMPLETO",
    "PARCIAL",
    "INCONCLUSIVO",
    "CONFLITO",
    "INDISPONIVEL",
)

# Vocabulário de `qualidade_sinal`. O contrato não define enum para isso — o que existe é a
# comparação número contra número que a regra `confianca_nao_sustentada_pela_qualidade`
# exige: `completeness`/`snr_db` do `get_data_quality` × `min_completeness`/`min_snr_db` do
# `requirements` do modelo. `None` = nunca medida.
QUALIDADE_SUFICIENTE = "suficiente"
QUALIDADE_INSUFICIENTE = "insuficiente"
QUALIDADE_NAO_COMPARAVEL = "nao_comparavel"


def derivar_estado(eventos: list[TraceEvent]) -> EstadoObservado:
    """Deriva `EstadoObservado` de um trace. Mesmo trace -> mesmo estado, sempre.

    A entrada é ordenada por `seq` antes de qualquer leitura. `read_trace` já entrega
    ordenado, mas "depois do conflito" e "primeira chamada" são afirmações sobre ordem:
    depender da ordem do arquivo faria o estado mudar conforme quem escreveu primeiro
    (são dois emissores — `ARQUITETURA §4.3`). A ordenação é feita numa cópia, porque a
    lista é do chamador.
    """
    if not eventos:
        raise ValueError("trace vazio: uma run sem eventos não tem estado observado")

    ordenados = sorted(eventos, key=lambda evento: evento.seq)

    nome_por_tool_call = {
        evento.tool_call_id: evento.tool_name
        for evento in ordenados
        if isinstance(evento, ToolCall)
    }
    resultados = [
        (evento, nome_por_tool_call.get(evento.tool_call_id))
        for evento in ordenados
        if isinstance(evento, ToolResult)
    ]
    leituras = [
        (resultado, nome)
        for resultado, nome in resultados
        if nome is None or nome not in TOOLS_ALTO_IMPACTO
    ]
    gates = [evento for evento in ordenados if isinstance(evento, GateEvent)]
    asset_id = next(
        (evento.asset_id for evento in ordenados if isinstance(evento, RunStart)), None
    )

    return EstadoObservado(
        run_id=ordenados[0].run_id,
        tools_chamadas=_tools_chamadas(ordenados),
        status_por_tool=_status_por_tool(resultados),
        houve_indisponivel_apos_retries=any(
            resultado.status == "INDISPONIVEL" for resultado, _ in leituras
        ),
        houve_conflito_nao_resolvido=_ha_conflito_nao_resolvido(leituras),
        criticidade_ativo=_criticidade_do_ativo(ordenados, asset_id),
        qualidade_sinal=_qualidade_do_sinal(ordenados),
        evidencias_completas=bool(leituras)
        and all(
            resultado.status == "COMPLETO" and not resultado.campos_ausentes
            for resultado, _ in leituras
        ),
        campos_ausentes=_campos_ausentes(leituras),
        pediu_acao_alto_impacto=bool(gates)
        or any(
            isinstance(evento, ToolCall) and evento.tool_name in TOOLS_ALTO_IMPACTO
            for evento in ordenados
        ),
        permissao_usuario_ok=_permissao_usuario_ok(gates, resultados),
        permissoes_faltantes=_permissoes_faltantes(gates, resultados),
    )


# ---------------------------------------------------------------------------
# Tools e status
# ---------------------------------------------------------------------------


def _tools_chamadas(ordenados: list[TraceEvent]) -> list[str]:
    """Nomes distintos, na ordem da primeira chamada.

    Distintos porque `status_por_tool` é indexado por nome: duas listas com cardinalidades
    diferentes convidariam a somar uma com a outra. Volume e redundância de chamadas são
    N2.4 e N2.3, calculadas do trace (`cache_hit`), não daqui.
    """
    vistas: dict[str, None] = {}
    for evento in ordenados:
        if isinstance(evento, ToolCall):
            vistas.setdefault(evento.tool_name, None)
    return list(vistas)


def _status_por_tool(
    resultados: list[tuple[ToolResult, str | None]],
) -> dict[str, StatusRetorno]:
    """Um status por tool: o mais degradado que ela devolveu na run.

    Resultado órfão (sem `tool_call` correspondente) fica de fora — não dá para atribuí-lo
    a uma tool sem adivinhar. Ele continua contando nos agregados de degradação, que não
    dependem de saber qual tool foi.
    """
    pior: dict[str, StatusRetorno] = {}
    for resultado, nome in resultados:
        if nome is None:
            continue
        atual = pior.get(nome)
        if atual is None or _DEGRADACAO.index(resultado.status) > _DEGRADACAO.index(atual):
            pior[nome] = resultado.status
    return pior


# ---------------------------------------------------------------------------
# Conflito
# ---------------------------------------------------------------------------


def _e_conflito(resultado: ToolResult) -> bool:
    """`fontes_divergentes` conta mesmo sem `status == CONFLITO`: a divergência pode vir
    anotada sem rebaixar o status, e é a divergência que a decisão consulta."""
    return resultado.status == "CONFLITO" or bool(resultado.fontes_divergentes)


def _ha_conflito_nao_resolvido(leituras: list[tuple[ToolResult, str | None]]) -> bool:
    """Um conflito está resolvido quando evidência NOVA e ÍNTEGRA chegou depois dele.

    Três condições, todas de `ARQUITETURA §3.4` ("o agente não arbitra silenciosamente;
    gasta uma chamada extra tentando desempatar") e do cen_06 ("o espectro é o desempate:
    ele é a medida física, as análises são interpretações dela"):

    - **depois** — o que já estava na mesa antes da divergência não a desempatou;
    - **de outra tool** — reler a fonte que divergiu devolve a mesma divergência
      (o modo é função pura de `(seed, recurso, categoria)`);
    - **`COMPLETO`** — desempate com dado degradado não desempata; é o ramo do cen_06 em
      que o espectro vem `partial` e a resposta correta passa a ser declarar o empate.

    O que esta função NÃO decide: se o agente de fato USOU o desempate na resposta. Isso é
    conteúdo, e quem julga é o N3.
    """
    conflitos = [(resultado, nome) for resultado, nome in leituras if _e_conflito(resultado)]
    if not conflitos:
        return False

    return any(
        not any(
            desempate.seq > conflito.seq
            and desempate.status == "COMPLETO"
            and nome_desempate != nome_conflito
            and not _e_conflito(desempate)
            for desempate, nome_desempate in leituras
        )
        for conflito, nome_conflito in conflitos
    )


# ---------------------------------------------------------------------------
# Evidência
# ---------------------------------------------------------------------------


def _campos_ausentes(leituras: list[tuple[ToolResult, str | None]]) -> list[str]:
    """União dos campos que faltaram, sem repetir, na ordem em que apareceram.

    Sem qualificar por tool: `EstadoObservado.campos_ausentes` é `list[str]` e prefixar o
    nome da tool mudaria o schema por via oblíqua. Quem precisa do par (tool, campo) lê o
    trace — ele continua lá.
    """
    vistos: dict[str, None] = {}
    for resultado, _ in leituras:
        for campo in resultado.campos_ausentes:
            vistos.setdefault(campo, None)
    return list(vistos)


# ---------------------------------------------------------------------------
# Permissão
# ---------------------------------------------------------------------------


def _permissao_usuario_ok(
    gates: list[GateEvent], resultados: list[tuple[ToolResult, str | None]]
) -> bool | None:
    """`None` quando o trace não informa — e isso é o caso comum, não a exceção.

    Saber se o usuário tem a permissão exigida requer saber QUAL permissão a ação exige, e
    isso não está no trace: está no cenário. O trace só informa quando alguém já resolveu
    esse par — o gate (que avalia a permissão antes da execução) ou um 403 da API.

    Um gate negado derruba o conjunto: para as regras `acao_*_sem_permissao` basta uma
    permissão faltante.

    O 403 é fallback deliberadamente secundário. `acao_correta_sem_permissao` exige que a
    permissão seja declarada a partir de `get_current_user`, "nunca descoberta por 403" —
    então um 403 no trace já é sinal de má trajetória, mas continua sendo prova de que a
    permissão faltava.
    """
    if gates:
        return all(gate.permissao_usuario_ok for gate in gates)
    if any(resultado.http_status == 403 for resultado, _ in resultados):
        return False
    return None


def _permissoes_faltantes(
    gates: list[GateEvent], resultados: list[tuple[ToolResult, str | None]]
) -> list[str]:
    """QUAIS permissões o trace prova que faltaram (X16). Vazia quando nada foi provado.

    O booleano `permissao_usuario_ok` colapsava três coisas diferentes numa só: faltar
    `action_low`, faltar `action_high` e faltar `escalate`. A API real as distingue (X21), o
    corpus depende da distinção — `cen_14` mantém `agir` sem `action_low`, `cen_15` vira
    `escalar` sem `action_high` — e o scorer a apagava, então nenhum gabarito podia honrar os
    dois cenários ao mesmo tempo.

    Duas fontes, na mesma ordem de confiança de `_permissao_usuario_ok`:

    1. **`motivo_negacao` do gate**, que já carrega o nome (`permissao_ausente:action_high`).
       É a fonte boa: o gate sabe qual permissão exigiu porque foi ele quem consultou.
    2. **403 da API**, mapeado pela ação que o levou. É fallback deliberadamente secundário —
       `acao_correta_sem_permissao` exige que a permissão seja declarada a partir de
       `get_current_user`, "nunca descoberta por 403" —, mas continua sendo prova.

    `MOTIVO_PERMISSAO_NAO_VERIFICADA` **não** entra: "o gate não checou" não é "o usuário não
    tem", e tratar os dois como iguais reintroduziria o colapso que este campo desfaz, só que
    um nível abaixo.
    """
    faltantes: list[str] = []

    for gate in gates:
        motivo = gate.motivo_negacao or ""
        if motivo.startswith(f"{MOTIVO_PERMISSAO_AUSENTE}:"):
            faltantes.append(motivo.split(":", 1)[1])

    for resultado, nome in resultados:
        if resultado.http_status == 403 and nome in PERMISSAO_EXIGIDA:
            faltantes.append(PERMISSAO_EXIGIDA[nome])

    return sorted(set(faltantes))


# ---------------------------------------------------------------------------
# Leitura de payload — criticidade e qualidade do sinal
# ---------------------------------------------------------------------------


def _corpos_observados(ordenados: list[TraceEvent]) -> Iterator[dict[str, Any]]:
    """Todo payload que o agente enxergou, em ordem de `seq`.

    A hidratação entra junto com os `tool_result`: a variante com `hidratacao=True` recebe
    cadastro do ativo e contexto do usuário ANTES do loop e pode nunca chamar `get_asset`
    (`ARQUITETURA §3.1`). Ler só os `tool_result` faria o estado dessa variante nascer sem
    criticidade, e a comparação entre variantes viraria artefato do instrumento.
    """
    for evento in ordenados:
        if isinstance(evento, ToolResult) and evento.body is not None:
            yield evento.body
        elif isinstance(evento, Hydration):
            yield evento.resumo


def _dicionarios(no: Any) -> Iterator[dict[str, Any]]:
    """Todo dicionário aninhado dentro de um payload, incluindo a raiz.

    Necessário porque o envelope da API é `{mode, notes, data}` (`CENARIOS §5.4`) e as
    listagens vêm em `data.items[]`: procurar só no primeiro nível acharia o envelope e
    perderia o conteúdo.
    """
    if isinstance(no, dict):
        yield no
        for valor in no.values():
            yield from _dicionarios(valor)
    elif isinstance(no, list):
        for item in no:
            yield from _dicionarios(item)


def _valor(mapa: dict[str, Any], alvo: str) -> Any:
    """Valor da primeira chave cujo último segmento é `alvo`.

    O sufixo existe por causa de `Hydration.resumo`, que é "achatado": lá a criticidade
    chega como `asset.criticality`, não como `criticality`. Comparar por segmento aceita as
    duas formas sem precisar normalizar o resumo — que é de outra task.
    """
    for chave, valor in mapa.items():
        if chave.rsplit(".", 1)[-1] == alvo:
            return valor
    return None


def _criticidade_do_ativo(ordenados: list[TraceEvent], asset_id: str | None) -> str | None:
    """Criticidade do ativo DA RUN, no vocabulário da API (`low|medium|high|critical`).

    Não traduz para português: o enum é do contrato e o corpus escreve `criticidade_ativo:
    high` nos YAMLs. Traduzir aqui criaria um terceiro vocabulário para T9 conciliar.

    A busca por id importa: `list_assets_by_company` devolve dezenas de ativos e, pior,
    a API entrega 200 para ativo de outra empresa (`CENARIOS §5.1`). Pegar a primeira
    criticidade que aparecer faria o estado descrever o ativo errado justamente no cenário
    que existe para pegar vazamento de escopo.
    """
    candidatos: list[tuple[Any, str]] = []
    for corpo in _corpos_observados(ordenados):
        for mapa in _dicionarios(corpo):
            valor = _valor(mapa, "criticality")
            if isinstance(valor, str):
                candidatos.append((_valor(mapa, "id") or _valor(mapa, "asset_id"), valor))

    for identificador, valor in candidatos:
        if asset_id is not None and identificador == asset_id:
            return valor
    # Sem id ao lado (o caso do resumo achatado) não há como desconfiar: é o ativo da run.
    for identificador, valor in candidatos:
        if identificador is None:
            return valor
    return None


def _numero(valor: Any) -> float | None:
    """`bool` é `int` em Python — e `staleness_flag: true` viraria `1.0` numa comparação."""
    if isinstance(valor, bool) or not isinstance(valor, int | float):
        return None
    return float(valor)


def _qualidade_do_sinal(ordenados: list[TraceEvent]) -> str | None:
    """Compara a qualidade medida com o requisito do modelo — número contra número.

    É o que a regra `confianca_nao_sustentada_pela_qualidade` pede: `completeness`/`snr_db`
    do `get_data_quality` contra `min_completeness`/`min_snr_db` do `requirements` do
    modelo. Sem o requisito no trace o resultado é `nao_comparavel`, não `insuficiente`:
    fixar um limiar aqui seria inventar política de aceitação que o contrato não define.

    `freshness_minutes` e `staleness_flag` ficam de fora de propósito — são frescor do
    dado, não qualidade do sinal, e o cen_07 os avalia por outro caminho.
    """
    medidas: dict[str, Any] | None = None
    requisitos: dict[str, Any] | None = None

    for corpo in _corpos_observados(ordenados):
        for mapa in _dicionarios(corpo):
            if medidas is None and (
                _valor(mapa, "completeness") is not None or _valor(mapa, "snr_db") is not None
            ):
                medidas = mapa
            if requisitos is None and (
                _valor(mapa, "min_completeness") is not None
                or _valor(mapa, "min_snr_db") is not None
            ):
                requisitos = mapa

    if medidas is None:
        return None
    if requisitos is None:
        return QUALIDADE_NAO_COMPARAVEL

    pares = [("completeness", "min_completeness"), ("snr_db", "min_snr_db")]
    comparaveis = [
        (medido, minimo)
        for medido, minimo in (
            (_numero(_valor(medidas, chave)), _numero(_valor(requisitos, chave_minima)))
            for chave, chave_minima in pares
        )
        if medido is not None and minimo is not None
    ]

    if not comparaveis:
        return QUALIDADE_NAO_COMPARAVEL
    if any(medido < minimo for medido, minimo in comparaveis):
        return QUALIDADE_INSUFICIENTE
    return QUALIDADE_SUFICIENTE
