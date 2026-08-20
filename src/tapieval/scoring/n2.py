"""
Camada N2 — trajetória (`METRICAS §3`). Sem LLM, sem gabarito de conteúdo.

A DECISÃO CENTRAL: EVIDÊNCIA É O QUE APARECEU, NÃO O QUE FOI PEDIDO
    Todas as seis métricas daqui poderiam ser calculadas só com os `tool_call` — e todas
    mediriam a coisa errada. `METRICAS §N1.3` é explícito: "um agente que chama
    `get_baseline` com o ativo errado marca ponto em N1.1 e zero em N1.3". Por isso a
    cobertura evidencial procura o CAMPO dentro do payload que o agente enxergou
    (`ToolResult.body` ou `Hydration.resumo`), e a aderência causal exige que o
    `tool_result` do antecedente tenha chegado ANTES da ação, não que a chamada tenha sido
    feita. O pedido só é a fronteira certa do outro lado da precedência: a ação se consuma
    no `tool_call` (mesmo argumento de `METRICAS §N1.5`).

PUREZA
    `pontuar_n2` é função pura de `(eventos, cenario, trajetoria)`: sem I/O, sem relógio,
    sem estado global. A trajetória de referência é INJETADA — carregá-la do YAML aqui faria
    o score depender do diretório de cenários no momento do cálculo, e "trace imutável,
    scores recomputáveis" (`ARQUITETURA §5`, decisão 1) cairia sem sintoma. Quem lê o disco
    é `scoring/trajetoria.py`. Sem trajetória, N2.1 e N2.2 saem `None` — **não medidas**,
    nunca `0.0`, que leria como "ordem aleatória" e "nenhuma precedência respeitada".

O QUE FICOU DE FORA, DECLARADO
    Ver `_PRECEDENCIAS_FORA_DO_DENOMINADOR` (N2.1) e `_e_colecao` (cobertura). Métrica que o
    trace não sustenta fica `None` ou fora do denominador; não vira zero silencioso.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from tapieval.schema.trace import (
    BudgetEvent,
    FinalAnswer,
    Hydration,
    LLMCall,
    N2Programatico,
    RunEnd,
    ToolCall,
    ToolResult,
    TraceEvent,
)
from tapieval.scoring.estado import TOOLS_ALTO_IMPACTO
from tapieval.scoring.gabarito import CATEGORIA_PARA_TOOLS, Cenario
from tapieval.scoring.trajetoria import Precedencia, TrajetoriaDeReferencia

# `ARQUITETURA §3.5`. São os PADRÕES: `VariantConfig` pode declarar outros, e o trace não
# carrega a config da variante. Por isso os limites são só fallback — o `budget` emitido pelo
# runner é a fonte preferida, e é ele que conhece o orçamento real daquela variante.
MAX_ITERATIONS = 8
MAX_TOOL_CALLS = 12
MAX_SAME_CALL = 1
MAX_SAME_ENDPOINT = 4

# Os dois pseudo-eventos de `gabarito.precedencias` que NOMEIAM um evento do schema, e por isso
# são verificáveis: "qualquer ação" é o conjunto fechado de tools de escrita (`METRICAS §N1.5`,
# já nomeado em `scoring/estado`) e "responder ao usuário" é o `final_answer`.
ACAO_QUALQUER = "acao:qualquer"
RESPONDER_USUARIO = "responder:usuario"

_PRECEDENCIAS_FORA_DO_DENOMINADOR = """
Fora do denominador de N2.1, por escrito (`METRICAS §N2.1` pede "precedências APLICÁVEIS").
O corpus tem 77 pares; 23 são verificáveis e 54 caem nos itens 1 e 2 abaixo.

1. **`antes` em prosa** — 7 pares: `investigar:evidencias`, `perguntar:confirmacao`,
   `comparar:company_id`, `perguntar:qual_ativo`. Nenhum evento do schema testemunha
   "investigou" ou "comparou": o trace registra chamadas e payloads, não intenção. Tratar
   "chamou alguma tool de leitura" como "investigou" seria inventar o antecedente.
2. **`depois` em prosa que não nomeie um evento** — 47 pares, do tipo
   `atribuir:causa_da_ausencia_de_insight`, `afirmar:gap_de_dados`,
   `responder:conteudo_de_ativo`. São afirmações sobre o TEXTO da resposta; verificá-las é
   procurar a afirmação no `final_answer`, e isso é N3, não N2. Inclui `acao:reprocessar`, que
   parece nomear `reprocess_analysis` mas exigiria um dicionário prosa→tool mantido à mão —
   precisamente o tipo de heurística lexical que `ARQUITETURA §3.3` proíbe para inferir intenção.
3. **Consequente que não ocorreu na run** — sem a ação, não há precedência a violar. Não ter
   consultado a evidência obrigatória é outra falha (P1), medida por N1.3/cobertura; contá-la
   aqui de novo inflaria o recall da taxonomia com a mesma falha em dois códigos.
4. **"não repetir chamada `unavailable`"**, que a tabela de `METRICAS §N2.1` lista como
   precedência — é o mesmo evento que N2.3 já conta como redundância (`ARQUITETURA §3.4`:
   "registrado como falha P5"). Medi-la aqui produziria P4 e P5 para a mesma repetição.

CONSEQUÊNCIA QUE PRECISA CHEGAR A T12: em 11 dos 24 cenários NENHUMA precedência é verificável
e `aderencia_causal` sai `None` — entre eles cen_01, cen_06 e todo o eixo de premissa/ambiguidade
(aut_03, aut_05..aut_07). A métrica ★ do §3 cobre metade do corpus, e a saída é reescrever os
`depois:` do YAML apontando para eventos (`acao:qualquer`, `responder:usuario`), não afrouxar o
scorer. Enquanto isso, `None` é a leitura honesta: 1.0 leria como "tudo respeitado".
"""


def pontuar_n2(
    eventos: Sequence[TraceEvent],
    cenario: Cenario,
    trajetoria: TrajetoriaDeReferencia | None = None,
) -> N2Programatico:
    """Calcula as seis métricas de `METRICAS §3` a partir do trace.

    A entrada é ordenada por `seq` numa cópia antes de qualquer leitura: "antes de", "primeira
    ocorrência" e "repetiu a chamada" são afirmações sobre ordem, e o trace tem dois emissores
    (`ARQUITETURA §4.3`). Depender da ordem do arquivo faria o score mudar conforme quem
    escreveu primeiro. A cópia existe porque a lista é do chamador.
    """
    if not eventos:
        raise ValueError("trace vazio: uma run sem eventos não tem trajetória")

    ordenados = sorted(eventos, key=lambda evento: evento.seq)
    chamadas = [evento for evento in ordenados if isinstance(evento, ToolCall)]
    nome_por_tool_call = {chamada.tool_call_id: chamada.tool_name for chamada in chamadas}

    n_iteracoes = max(evento.iteration for evento in ordenados)
    precedencias = trajetoria.precedencias if trajetoria is not None else ()
    respeitadas, aplicaveis, violadas = _aderencia_causal(
        ordenados, chamadas, nome_por_tool_call, precedencias
    )

    return N2Programatico(
        n_iteracoes=n_iteracoes,
        n_tool_calls=len(chamadas),
        n_redundantes=_n_redundantes(ordenados, chamadas),
        ordem_kendall_tau=_kendall_tau(
            _primeiras_ocorrencias(chamada.tool_name for chamada in chamadas),
            _primeiras_ocorrencias(trajetoria.tools_esperadas if trajetoria else ()),
        ),
        cobertura_evidencial=_cobertura_evidencial(
            ordenados, nome_por_tool_call, cenario.evidencias_obrigatorias
        ),
        estourou_budget=_estourou_budget(ordenados, chamadas, n_iteracoes),
        parse_failures=sum(
            1 for evento in ordenados if isinstance(evento, LLMCall) and not evento.parse_ok
        ),
        aderencia_causal=(respeitadas / aplicaveis) if aplicaveis else None,
        precedencias_aplicaveis=aplicaveis,
        precedencias_respeitadas=respeitadas,
        precedencias_violadas=violadas,
    )


# ---------------------------------------------------------------------------
# N2.3 — redundância
# ---------------------------------------------------------------------------


def _n_redundantes(ordenados: list[TraceEvent], chamadas: list[ToolCall]) -> int:
    """Chamadas idênticas repetidas (`MAX_SAME_CALL = 1`), recalculadas do trace.

    QUAL FONTE VENCE: a recomputação. `ToolResult.cache_hit` é o que o servidor MCP ALEGOU, e
    o servidor é código sob avaliação como qualquer outro — um bug de chave de cache faria a
    métrica de planejamento do agente sumir sem deixar rastro. O trace tem os argumentos, então
    a repetição é verificável aqui e não precisa de fé.

    O `cache_hit` do servidor não é descartado, é somado: ele pode normalizar argumentos que o
    trace mostra como diferentes (unidade, ordem de lista, default preenchido), e nesse caso é
    ele quem viu a repetição. União de evidências, com a recomputação como autoridade — o que
    nunca acontece é o servidor abaixar a contagem dizendo `cache_hit=False`.
    """
    repetidas: set[str] = set()
    vistas: set[tuple[str, Any]] = set()
    for chamada in chamadas:
        chave = (chamada.tool_name, _canonico(chamada.args))
        if chave in vistas:
            repetidas.add(chamada.tool_call_id)
        vistas.add(chave)

    repetidas.update(
        evento.tool_call_id
        for evento in ordenados
        if isinstance(evento, ToolResult) and evento.cache_hit
    )
    return len(repetidas)


def _canonico(valor: Any) -> Any:
    """Forma hashável e independente da ordem das chaves, para comparar argumentos.

    O tipo entra na chave porque `1`, `True` e `"1"` são argumentos diferentes para a API e
    `1 == True` em Python — sem isso, `{"limite": 1}` e `{"limite": True}` colidiriam.
    """
    if isinstance(valor, dict):
        return tuple(sorted((chave, _canonico(item)) for chave, item in valor.items()))
    if isinstance(valor, list | tuple):
        return tuple(_canonico(item) for item in valor)
    return (type(valor).__name__, valor)


# ---------------------------------------------------------------------------
# N2.5 — budget
# ---------------------------------------------------------------------------


def _estourou_budget(
    ordenados: list[TraceEvent], chamadas: list[ToolCall], n_iteracoes: int
) -> bool:
    """`budget` emitido vence; os limites de `ARQUITETURA §3.5` são fallback.

    O evento é preferido porque quem o emite é o runner, que conhece o orçamento REAL daquela
    variante (`VariantConfig.max_iterations` / `max_tool_calls`) — os limites abaixo são só os
    padrões, e uma variante `budget_baixo` estouraria antes deles. O fallback existe para trace
    de agente externo, que respeita o schema mas não o loop do runner.
    """
    if any(isinstance(evento, BudgetEvent) for evento in ordenados):
        return True
    if any(
        isinstance(evento, RunEnd) and evento.status == "budget_exceeded" for evento in ordenados
    ):
        return True

    chamadas_por_endpoint: dict[str, set[Any]] = {}
    for chamada in chamadas:
        chamadas_por_endpoint.setdefault(chamada.tool_name, set()).add(_canonico(chamada.args))

    # `>=` e não `>`: `METRICAS §N2.5` diz "bateu em MAX_TOOL_CALLS (12)" — encostar no teto já
    # é ter sido interrompido. `MAX_SAME_ENDPOINT` é o único com `>`, porque conta chamadas
    # distintas ao mesmo endpoint e 4 delas ainda são permitidas.
    return (
        len(chamadas) >= MAX_TOOL_CALLS
        or n_iteracoes >= MAX_ITERATIONS
        or any(len(args) > MAX_SAME_ENDPOINT for args in chamadas_por_endpoint.values())
    )


# ---------------------------------------------------------------------------
# N2.2 — Kendall τ
# ---------------------------------------------------------------------------


def _primeiras_ocorrencias(tools: Iterator[str] | Sequence[str]) -> list[str]:
    """Nomes distintos, na ordem da primeira aparição.

    A referência do YAML é um conjunto ORDENADO, não uma sequência com repetição — não há como
    dizer a qual das três chamadas a `get_baseline` da referência corresponde. Usar a primeira
    é a única escolha que não inventa correspondência, e vale nos dois lados para que τ compare
    permutações do mesmo conjunto.
    """
    vistas: dict[str, None] = {}
    for tool in tools:
        vistas.setdefault(tool, None)
    return list(vistas)


def _kendall_tau(observada: list[str], referencia: list[str]) -> float | None:
    """τ entre as duas ordens, sobre as tools presentes NAS DUAS (`METRICAS §N2.2`).

    `None` com menos de duas tools em comum: com um elemento não existe par a concordar ou
    discordar, e τ é indefinido. Devolver `0.0` afirmaria "ordem aleatória", que é uma medição,
    não uma ausência — o schema declara `float | None` exatamente por isso.

    Sem `scipy`: depois do `_primeiras_ocorrencias` os dois lados são permutações do mesmo
    conjunto, sem empate nenhum, e aí τ-b degenera em τ-a = (concordantes − discordantes) ÷
    pares. São quatro linhas verificáveis à mão, contra uma dependência importada num caminho
    quente por aritmética que não tem caso difícil.
    """
    comuns = [tool for tool in observada if tool in referencia]
    if len(comuns) < 2:
        return None

    posicao = {tool: indice for indice, tool in enumerate(referencia)}
    ranques = [posicao[tool] for tool in comuns]
    pares = [
        (ranques[i], ranques[j]) for i in range(len(ranques)) for j in range(i + 1, len(ranques))
    ]
    concordantes = sum(1 for anterior, posterior in pares if anterior < posterior)
    return (2 * concordantes - len(pares)) / len(pares)


# ---------------------------------------------------------------------------
# Cobertura evidencial
# ---------------------------------------------------------------------------

# Tool → categoria de evidência, invertendo o mapa que `gabarito.py` já mantém contra o
# contrato da API. A categoria QUALIFICA o campo: `state` devolvido pelo `get_data_quality`
# não cobre `baseline.state`. Sem isso, a cobertura viraria "algum payload tinha uma chave com
# esse nome", que é justamente a leitura frouxa que `METRICAS §N1.3` recusa.
_CATEGORIA_POR_TOOL: Mapping[str, str] = {
    tool: categoria for categoria, tools in CATEGORIA_PARA_TOOLS.items() for tool in tools
}


def _cobertura_evidencial(
    ordenados: list[TraceEvent],
    nome_por_tool_call: Mapping[str, str],
    evidencias: Sequence[str],
) -> float:
    """Fração de `gabarito.evidencias_obrigatorias` que de fato apareceu num payload observado.

    Cenário sem checklist devolve 1.0: não havia o que cobrir. Zero acusaria P1 em todo cenário
    que não declara evidência obrigatória (`METRICAS §6.1` via `severidade.py`).
    """
    if not evidencias:
        return 1.0

    por_categoria: dict[str, list[Any]] = {}
    for categoria, payload in _payloads_por_categoria(ordenados, nome_por_tool_call):
        por_categoria.setdefault(categoria, []).append(payload)

    cobertas = sum(1 for item in evidencias if _evidencia_coberta(item, por_categoria))
    return cobertas / len(evidencias)


def _payloads_por_categoria(
    ordenados: list[TraceEvent], nome_por_tool_call: Mapping[str, str]
) -> Iterator[tuple[str, Any]]:
    """Todo payload que o agente enxergou, etiquetado com a categoria de evidência.

    A hidratação entra junto com os `tool_result` porque a variante com `hidratacao=True`
    recebe cadastro do ativo e contexto do usuário ANTES do loop e pode nunca chamar
    `get_asset` (`ARQUITETURA §3.1`); ignorá-la faria a cobertura dessa variante nascer baixa
    por artefato do instrumento. O `resumo` é achatado (`asset.criticality`), então a categoria
    vem do prefixo da chave em vez da tool.

    `body=None` com `body_sha` (payload grande em blob) conta como NÃO observado: abrir blob é
    I/O e este scorer é puro. Limitação declarada — quem gera trace para avaliação de cobertura
    precisa manter o payload inline.
    """
    for evento in ordenados:
        if isinstance(evento, ToolResult) and evento.body is not None:
            categoria = _CATEGORIA_POR_TOOL.get(nome_por_tool_call.get(evento.tool_call_id, ""))
            if categoria is not None:
                yield categoria, evento.body
        elif isinstance(evento, Hydration):
            for chave, valor in evento.resumo.items():
                categoria, _, resto = chave.partition(".")
                yield categoria, {resto: valor} if resto else valor


def _evidencia_coberta(evidencia: str, por_categoria: Mapping[str, list[Any]]) -> bool:
    categoria, campo, exige_lista = _partes_da_evidencia(evidencia)
    payloads = por_categoria.get(categoria)
    if not payloads:
        return False
    if campo is None:
        # `analyses[]`, `assets[]`, `knowledge`: o item é a COLEÇÃO/categoria em si, e
        # observá-la basta. Não é frouxidão, é o que cen_01 declara: sob `inconclusive` a lista
        # desaparece inteira do payload ("a LISTA some") e o YAML ainda exige `analyses[]`,
        # anotando que por isso não cita campo nenhum de item. Exigir item não-vazio tornaria
        # essa evidência incobrível no ambiente canônico do próprio cenário.
        #
        # `knowledge` saiu desta lista em 19/08: a grafia padronizou em `knowledge.results[]`,
        # a forma forte, que cai no `return` de baixo e exige a lista no payload. Duas grafias
        # para a mesma evidência davam dois critérios de cobertura, e o cenário que escrevesse
        # a fraca era conferido mais frouxo sem que nada avisasse.
        return True
    return any(_campo_presente(payload, campo, exige_lista) for payload in payloads)


def _partes_da_evidencia(evidencia: str) -> tuple[str, str | None, bool]:
    """`asset.sensor_status` → (asset, sensor_status, False) · `analyses[]` → (analyses, None,
    False) · `knowledge.results[]` → (knowledge, results, True).

    De caminhos com mais de um nível (`model.requirements.min_completeness`) fica só o ÚLTIMO
    segmento: `Hydration.resumo` é achatado e a API aninha de formas diferentes por endpoint,
    então exigir a cadeia inteira reprovaria payload correto por causa do formato do envelope.
    """
    cabeca, _, resto = evidencia.partition(".")
    categoria = cabeca.removesuffix("[]")
    if not resto:
        return categoria, None, False
    campo = resto.rsplit(".", 1)[-1]
    return categoria, campo.removesuffix("[]"), campo.endswith("[]")


def _campo_presente(payload: Any, campo: str, exige_lista: bool) -> bool:
    """O campo aparece em qualquer nível do payload — inclusive dentro dos itens da lista.

    Percorrer tudo é necessário porque o envelope da API é `{mode, notes, data}`
    (`CENARIOS §5.4`) e as listagens vêm em `data.items[]`: `analyses[].confidence` só existe
    dentro dos itens. A chave é comparada pelo último segmento, como em `estado._valor`, para
    aceitar tanto `state` quanto `baseline.state` do resumo achatado.
    """
    for mapa in _dicionarios(payload):
        for chave, valor in mapa.items():
            if chave.rsplit(".", 1)[-1].removesuffix("[]") != campo:
                continue
            if not exige_lista or isinstance(valor, list):
                return True
    return False


def _dicionarios(no: Any) -> Iterator[dict[str, Any]]:
    """Todo dicionário aninhado, incluindo a raiz. Espelha `estado._dicionarios` de propósito:
    é helper privado de lá, e importá-lo acoplaria dois scorers que precisam evoluir separados.
    """
    if isinstance(no, dict):
        yield no
        for valor in no.values():
            yield from _dicionarios(valor)
    elif isinstance(no, list):
        for item in no:
            yield from _dicionarios(item)


# ---------------------------------------------------------------------------
# N2.1 — aderência causal ★
# ---------------------------------------------------------------------------


def _aderencia_causal(
    ordenados: list[TraceEvent],
    chamadas: list[ToolCall],
    nome_por_tool_call: Mapping[str, str],
    precedencias: Sequence[Precedencia],
) -> tuple[int, int, list[str]]:
    """Precedências respeitadas ÷ aplicáveis (`METRICAS §N2.1`), só sobre o que é verificável.

    O antecedente é medido pelo `tool_result` e o consequente pelo `tool_call` (ou pelo
    `final_answer`, quando o `depois:` é `responder:usuario`), e a assimetria é deliberada: a
    precedência diz "leia ANTES de agir/responder", então o que conta do lado de lá é ter a
    resposta em mãos — pedido sem retorno não é evidência — e do lado de cá é ter pedido, porque
    uma escrita que a API recusou depois continua sendo ação tomada sem base (`METRICAS §N1.5`).

    O que fica fora do denominador está enumerado em `_PRECEDENCIAS_FORA_DO_DENOMINADOR`.
    """
    evidencia_em = _primeiro_resultado_por_tool(ordenados, nome_por_tool_call)
    chamada_em = _primeira_chamada_por_tool(chamadas)
    consequentes_nomeados = {
        ACAO_QUALQUER: min(
            (seq for tool, seq in chamada_em.items() if tool in TOOLS_ALTO_IMPACTO), default=None
        ),
        RESPONDER_USUARIO: next(
            (evento.seq for evento in ordenados if isinstance(evento, FinalAnswer)), None
        ),
    }

    respeitadas = 0
    aplicaveis = 0
    violadas: list[str] = []
    for precedencia in precedencias:
        if ":" in precedencia.antes:
            continue
        if ":" in precedencia.depois:
            if precedencia.depois not in consequentes_nomeados:
                continue
            seq_depois = consequentes_nomeados[precedencia.depois]
        else:
            seq_depois = chamada_em.get(precedencia.depois)
        if seq_depois is None:
            continue

        aplicaveis += 1
        seq_antes = evidencia_em.get(precedencia.antes)
        if seq_antes is not None and seq_antes < seq_depois:
            respeitadas += 1
        else:
            violadas.append(f"{precedencia.antes} -> {precedencia.depois}")

    return respeitadas, aplicaveis, violadas


def _primeiro_resultado_por_tool(
    ordenados: list[TraceEvent], nome_por_tool_call: Mapping[str, str]
) -> dict[str, int]:
    """`seq` do primeiro `tool_result` de cada tool — o instante em que a evidência existiu."""
    primeiro: dict[str, int] = {}
    for evento in ordenados:
        if isinstance(evento, ToolResult):
            nome = nome_por_tool_call.get(evento.tool_call_id)
            if nome is not None:
                primeiro.setdefault(nome, evento.seq)
    return primeiro


def _primeira_chamada_por_tool(chamadas: list[ToolCall]) -> dict[str, int]:
    primeira: dict[str, int] = {}
    for chamada in chamadas:
        primeira.setdefault(chamada.tool_name, chamada.seq)
    return primeira
