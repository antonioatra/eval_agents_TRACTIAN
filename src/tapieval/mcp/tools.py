"""
Catálogo de tools MCP — uma tool por endpoint, gerado do contrato OpenAPI.

UMA TOOL POR ENDPOINT, 1:1, SEM TOOL DE CONVENIÊNCIA
    A granularidade é o que torna "escolha da função" mensurável (`ARQUITETURA §4.2`). Uma
    tool que agregasse duas chamadas — `diagnosticar_ativo` que buscasse cadastro e análises —
    destruiria a contagem de eficiência da N2 e tornaria `tools_esperadas` do corpus
    incomparável com o que o agente fez. O catálogo é derivado do contrato justamente para que
    ninguém possa acrescentar uma tool sem acrescentar um endpoint.

X10 — POR QUE ESTE MÓDULO NÃO USA `yaml.safe_load`
    O contrato declara o path `/assets/{assetId}` DUAS VEZES: uma com `get` (linha ~331) e
    outra com `patch` (linha ~348). Em YAML a chave repetida sobrescreve a anterior, então
    `yaml.safe_load` devolve 17 paths e o do ativo fica **só com `patch`** — some `get_asset`,
    o endpoint mais usado do corpus inteiro, e some em silêncio: o catálogo continua parecendo
    válido, com 17 tools em vez de 18.

    A saída é `_CarregadorQueFundeChaves`, um `SafeLoader` que FUNDE mapeamentos duplicados em
    vez de sobrescrevê-los, e falha alto quando a fusão seria ambígua (duas definições da mesma
    subchave). Assim o documento inteiro continua parseado com estrutura — parâmetros, schemas,
    `requestBody` — sem depender de regex, e a duplicidade vira dado em vez de perda.
    `scripts/validar_cenarios.py` escapa por acidente: usa regex sobre o texto cru e acha os 18
    `operationId`. A API real expõe os dois endpoints normalmente.

CAMELCASE NO CONTRATO, SNAKE_CASE NA TOOL
    O contrato escreve parâmetro de path em camelCase (`assetId`) e de query em snake_case
    (`point_id`). O corpus escreve tudo em snake_case (`args_esperados: {asset_id: ...}`), e é o
    corpus que a N1.2 compara com `ToolCall.args`. Expor `assetId` daria 0% de acurácia de
    argumento na bateria inteira por diferença de convenção. O catálogo normaliza para
    snake_case e guarda o nome original só para montar a URL.

O `seed` NÃO É ARGUMENTO DE TOOL
    Ele é parâmetro declarado do contrato, mas pertence ao ambiente da run, não ao agente:
    `TractianClient` o injeta em toda query. Expô-lo deixaria o agente escolher o próprio
    ambiente, e um agente que descobrisse `seed=complete` (`api/app/prob.py::resolve_mode`)
    passaria a bateria sem degradação nenhuma.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import yaml

RAIZ_DO_REPO = Path(__file__).resolve().parents[3]
CAMINHO_DO_CONTRATO = (
    RAIZ_DO_REPO / "inteli-tractian-project" / "docs" / "api-contract.openapi.yaml"
)

METODOS_HTTP: tuple[str, ...] = ("get", "post", "patch", "put", "delete")

METODOS_DE_LEITURA: frozenset[str] = frozenset({"get"})

# Parâmetros do contrato que a fronteira preenche e o agente nunca vê. Ver o topo do módulo.
PARAMETROS_DO_AMBIENTE: frozenset[str] = frozenset({"seed"})


# ---------------------------------------------------------------------------
# Leitura do contrato
# ---------------------------------------------------------------------------


class ErroDeContrato(ValueError):
    """O contrato OpenAPI não pôde ser lido de forma inequívoca."""


class _CarregadorQueFundeChaves(yaml.SafeLoader):
    """`SafeLoader` que funde mapeamentos duplicados em vez de sobrescrevê-los.

    Existe por causa do X10 (ver o topo do módulo). A fusão é rasa e só vale entre dicionários
    com subchaves disjuntas: `{get: ...}` + `{patch: ...}` vira `{get: ..., patch: ...}`.
    Qualquer outra duplicidade levanta `ErroDeContrato` — silenciar a segunda seria repetir
    exatamente o bug que este carregador existe para corrigir, só que num lugar diferente.
    """

    def construct_mapping(self, node: Any, deep: bool = False) -> dict[Any, Any]:
        self.flatten_mapping(node)
        mapeamento: dict[Any, Any] = {}
        for no_da_chave, no_do_valor in node.value:
            chave = self.construct_object(no_da_chave, deep=True)
            valor = self.construct_object(no_do_valor, deep=True)
            if chave in mapeamento:
                mapeamento[chave] = _fundir(chave, mapeamento[chave], valor)
            else:
                mapeamento[chave] = valor
        return mapeamento


def _fundir(chave: Any, anterior: Any, novo: Any) -> dict[Any, Any]:
    if not isinstance(anterior, dict) or not isinstance(novo, dict):
        raise ErroDeContrato(
            f"chave {chave!r} declarada duas vezes com valor não-mapeável — "
            "não há fusão possível, corrija o contrato"
        )
    colisoes = sorted(set(anterior) & set(novo))
    if colisoes:
        raise ErroDeContrato(
            f"chave {chave!r} declarada duas vezes e as duas definem {colisoes} — "
            "qual delas vale é ambíguo, corrija o contrato"
        )
    return {**anterior, **novo}


def carregar_documento_do_contrato(caminho: Path | None = None) -> dict[str, Any]:
    """O contrato inteiro, com os paths duplicados FUNDIDOS. Ver X10 no topo do módulo."""
    caminho = caminho or CAMINHO_DO_CONTRATO
    documento = yaml.load(caminho.read_text(encoding="utf-8"), Loader=_CarregadorQueFundeChaves)
    if not isinstance(documento, dict) or "paths" not in documento:
        raise ErroDeContrato(f"{caminho}: não parece um contrato OpenAPI (sem `paths`)")
    return documento


# ---------------------------------------------------------------------------
# Operações
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Parametro:
    """Um argumento de tool derivado de um parâmetro do contrato."""

    nome: str
    """Nome exposto ao agente, em snake_case. É o que o corpus escreve."""

    nome_na_api: str
    """Nome original do contrato. Só serve para montar a URL (`{assetId}`)."""

    local: str
    """`"path"` ou `"query"`."""

    obrigatorio: bool
    schema: Mapping[str, Any] = field(default_factory=dict)
    descricao: str | None = None


@dataclass(frozen=True)
class Operacao:
    """Uma operação do contrato = uma tool MCP. A correspondência é 1:1 por construção."""

    nome: str
    """`operationId` em snake_case — o nome da tool e a chave usada pelo corpus e pelos scorers."""

    operation_id: str
    metodo: str
    caminho: str
    """Template do path, com os nomes originais: `/assets/{assetId}/baseline`."""

    descricao: str
    parametros: tuple[Parametro, ...] = ()
    corpo: tuple[Parametro, ...] = ()
    """Propriedades do `requestBody`. Vazio nas leituras."""

    @property
    def alto_impacto(self) -> bool:
        """Escrita é alto impacto. Derivado do método, nunca de uma segunda lista.

        Duas listas divergem em silêncio, e a divergência aqui significaria gate cobrindo uma
        tool que a N1.3 não conta como ação — ou o contrário.
        """
        return self.metodo not in METODOS_DE_LEITURA

    @property
    def argumentos(self) -> tuple[Parametro, ...]:
        return self.parametros + self.corpo

    def input_schema(self) -> dict[str, Any]:
        """JSON Schema dos argumentos da tool.

        `additionalProperties: False` é deliberado: argumento inventado tem de virar
        `args_validos=False` no trace, não ser descartado em silêncio. Um agente que manda
        `assetId` em vez de `asset_id` está errando a chamada, e a N1.2 precisa enxergar isso.
        """
        propriedades: dict[str, Any] = {}
        obrigatorios: list[str] = []
        for parametro in self.argumentos:
            propriedade = dict(parametro.schema)
            if parametro.descricao:
                propriedade.setdefault("description", parametro.descricao)
            propriedades[parametro.nome] = propriedade
            if parametro.obrigatorio:
                obrigatorios.append(parametro.nome)
        return {
            "type": "object",
            "properties": propriedades,
            "required": obrigatorios,
            "additionalProperties": False,
        }

    def montar_caminho(self, args: Mapping[str, Any]) -> str:
        caminho = self.caminho
        for parametro in self.parametros:
            if parametro.local == "path":
                caminho = caminho.replace(f"{{{parametro.nome_na_api}}}", str(args[parametro.nome]))
        return caminho

    def montar_query(self, args: Mapping[str, Any]) -> dict[str, Any]:
        return {
            parametro.nome_na_api: args[parametro.nome]
            for parametro in self.parametros
            if parametro.local == "query" and args.get(parametro.nome) is not None
        }

    def montar_corpo(self, args: Mapping[str, Any]) -> dict[str, Any]:
        return {
            parametro.nome: args[parametro.nome]
            for parametro in self.corpo
            if args.get(parametro.nome) is not None
        }


@cache
def carregar_operacoes(caminho: Path | None = None) -> Mapping[str, Operacao]:
    """As operações do contrato, indexadas pelo nome da tool.

    Cacheado por processo: o contrato é um arquivo versionado que não muda durante uma bateria,
    e reler 22 KB de YAML a cada `list_tools` de 544 runs seria desperdício puro.
    """
    documento = carregar_documento_do_contrato(caminho)
    componentes = documento.get("components") or {}

    operacoes: dict[str, Operacao] = {}
    for caminho_do_path, item in (documento.get("paths") or {}).items():
        for metodo in METODOS_HTTP:
            definicao = item.get(metodo)
            if definicao is None:
                continue
            operacao = _operacao(caminho_do_path, metodo, definicao, componentes)
            if operacao.nome in operacoes:
                raise ErroDeContrato(
                    f"operationId duplicado: {operacao.operation_id!r} em "
                    f"{metodo.upper()} {caminho_do_path}"
                )
            operacoes[operacao.nome] = operacao
    return operacoes


def _operacao(
    caminho: str, metodo: str, definicao: Mapping[str, Any], componentes: Mapping[str, Any]
) -> Operacao:
    operation_id = definicao.get("operationId")
    if not operation_id:
        raise ErroDeContrato(f"{metodo.upper()} {caminho}: operação sem `operationId`")

    parametros = tuple(
        parametro
        for crua in definicao.get("parameters") or ()
        if (parametro := _parametro(_resolver(crua, componentes))) is not None
    )
    return Operacao(
        nome=para_snake_case(operation_id),
        operation_id=operation_id,
        metodo=metodo,
        caminho=caminho,
        descricao=_descricao(definicao, metodo, caminho),
        parametros=parametros,
        corpo=_corpo(definicao, componentes),
    )


def _parametro(crua: Mapping[str, Any]) -> Parametro | None:
    nome_na_api = crua["name"]
    if nome_na_api in PARAMETROS_DO_AMBIENTE:
        return None
    local = crua.get("in", "query")
    return Parametro(
        nome=para_snake_case(nome_na_api),
        nome_na_api=nome_na_api,
        local=local,
        # Parâmetro de path é sempre obrigatório: sem ele não existe URL.
        obrigatorio=bool(crua.get("required")) or local == "path",
        schema=dict(crua.get("schema") or {"type": "string"}),
        descricao=crua.get("description"),
    )


def _corpo(definicao: Mapping[str, Any], componentes: Mapping[str, Any]) -> tuple[Parametro, ...]:
    corpo = definicao.get("requestBody")
    if not corpo:
        return ()
    schema = (((corpo.get("content") or {}).get("application/json")) or {}).get("schema") or {}
    achatado = _achatar(schema, componentes)
    obrigatorios = set(achatado.get("required") or ())
    return tuple(
        Parametro(
            nome=nome,
            nome_na_api=nome,
            local="body",
            obrigatorio=nome in obrigatorios,
            schema={chave: valor for chave, valor in propriedade.items() if chave != "description"},
            descricao=propriedade.get("description"),
        )
        for nome, propriedade in (
            _resolver_em_profundidade(achatado.get("properties") or {}, componentes)
        ).items()
    )


def _achatar(schema: Mapping[str, Any], componentes: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve `$ref` e funde `allOf` num único objeto.

    O `requestBody` de `updateAssetConfig` é `allOf: [ActionRequest, {changes: ...}]`. Sem
    achatar, `justification` — que é o campo que a API valida com `minLength: 20` — não
    apareceria no schema da tool, e o agente descobriria a regra levando 400.
    """
    schema = _resolver(schema, componentes)
    if "allOf" not in schema:
        return dict(schema)

    fundido: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
    for parcela in schema["allOf"]:
        achatada = _achatar(parcela, componentes)
        fundido["properties"].update(achatada.get("properties") or {})
        fundido["required"] = list(
            dict.fromkeys([*fundido["required"], *(achatada.get("required") or ())])
        )
    return fundido


def _resolver_em_profundidade(no: Any, componentes: Mapping[str, Any]) -> Any:
    """Resolve todo `$ref` aninhado, inclusive dentro de propriedades e de listas.

    Sem isto, `updateAssetConfig.changes.config` sairia do catálogo como
    `{"$ref": "#/components/schemas/AssetConfig"}` — uma referência que aponta para um
    documento que o cliente MCP não tem. O modelo receberia um schema quebrado exatamente na
    tool mais perigosa do catálogo.
    """
    if isinstance(no, dict):
        resolvido = _resolver(no, componentes) if "$ref" in no else no
        return {
            chave: _resolver_em_profundidade(valor, componentes)
            for chave, valor in resolvido.items()
        }
    if isinstance(no, list):
        return [_resolver_em_profundidade(item, componentes) for item in no]
    return no


def _resolver(no: Mapping[str, Any], componentes: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve `$ref` local (`#/components/...`). Referência externa não existe no contrato."""
    referencia = no.get("$ref")
    if not referencia:
        return dict(no)
    if not referencia.startswith("#/components/"):
        raise ErroDeContrato(f"referência não local: {referencia!r}")

    alvo: Any = componentes
    for parte in referencia.removeprefix("#/components/").split("/"):
        alvo = alvo[parte]
    return _resolver(alvo, componentes)


def _descricao(definicao: Mapping[str, Any], metodo: str, caminho: str) -> str:
    """A melhor descrição disponível no contrato, com o recurso anexado.

    O modelo escolhe função pelo texto: uma tool mal descrita é uma tool que ele não usa, e a
    N1.1 registraria isso como má escolha do agente quando o defeito é do catálogo. Sete das 18
    operações do contrato só têm a descrição da resposta 200 — "Empresa.", "Espectro FFT." —, o
    que é pouco para discriminar entre 18 opções.

    O sufixo `(GET /assets/{assetId}/rms)` é acrescentado a TODAS, uniformemente, e é derivado
    do contrato, não escrito à mão: dá ao modelo o recurso e o verbo sem que ninguém injete
    conhecimento de domínio no catálogo — o que tornaria o catálogo uma variável do experimento
    em vez de uma constante.
    """
    descricao = (definicao.get("description") or "").strip()
    if not descricao:
        resposta_ok = (definicao.get("responses") or {}).get("200") or {}
        descricao = (resposta_ok.get("description") or definicao.get("summary") or "").strip()
    return f"{' '.join(descricao.split())} ({metodo.upper()} {caminho})".strip()


def para_snake_case(nome: str) -> str:
    """`getRmsSeries` → `get_rms_series`, `assetId` → `asset_id`. Idempotente para snake_case."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", nome).lower()


def _tools_de_alto_impacto() -> frozenset[str]:
    return frozenset(nome for nome, op in carregar_operacoes().items() if op.alto_impacto)


TOOLS_DE_ALTO_IMPACTO: frozenset[str] = _tools_de_alto_impacto()
"""As tools de escrita, derivadas do método HTTP do contrato.

É onde a política injetável do gate (T15) entra, e tem de bater com
`scoring.estado.TOOLS_ALTO_IMPACTO` — há teste de contrato para isso."""


# ---------------------------------------------------------------------------
# Validação de argumentos — antes de qualquer HTTP
# ---------------------------------------------------------------------------

_TIPOS_JSON: Mapping[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "object": dict,
    "array": list,
}


def validar_argumentos(operacao: Operacao, args: Mapping[str, Any]) -> str | None:
    """`None` quando os argumentos servem; senão, o motivo em uma linha.

    Validador explícito e pequeno, não um motor de JSON Schema: os schemas deste catálogo usam
    seis palavras-chave (`type`, `enum`, `minLength`, `required`, `properties`,
    `additionalProperties`) e uma dependência a mais para cobrir o que não usamos seria custo
    sem contrapartida. Palavra-chave nova no contrato tem de aparecer aqui — por isso
    `_TIPOS_JSON` é fechado.

    A mensagem vai para `ToolCall.args_erro` e volta ao agente: ela é a única chance dele de
    corrigir a chamada, então diz o que faltou, não que "houve erro de validação".
    """
    schema = operacao.input_schema()
    propriedades: Mapping[str, Any] = schema["properties"]

    desconhecidos = sorted(set(args) - set(propriedades))
    if desconhecidos:
        return (
            f"argumento(s) inesperado(s) {desconhecidos}; "
            f"esperados: {sorted(propriedades)}"
        )

    faltando = sorted(nome for nome in schema["required"] if args.get(nome) is None)
    if faltando:
        return f"argumento(s) obrigatório(s) ausente(s): {faltando}"

    for nome, valor in args.items():
        if valor is None:
            continue
        erro = _validar_valor(nome, valor, propriedades[nome])
        if erro is not None:
            return erro
    return None


def _validar_valor(nome: str, valor: Any, schema: Mapping[str, Any]) -> str | None:
    tipo = schema.get("type")
    esperado = _TIPOS_JSON.get(tipo) if tipo else None
    if esperado is not None:
        # `bool` é subclasse de `int` em Python: sem esta guarda, `True` passaria por integer.
        if isinstance(valor, bool) is not (tipo == "boolean") or not isinstance(valor, esperado):
            return f"{nome!r} deveria ser {tipo}, veio {type(valor).__name__}"

    opcoes = schema.get("enum")
    if opcoes is not None and valor not in opcoes:
        return f"{nome!r} deveria ser um de {list(opcoes)}, veio {valor!r}"

    minimo = schema.get("minLength")
    if minimo is not None and isinstance(valor, str) and len(valor.strip()) < minimo:
        return f"{nome!r} precisa de ao menos {minimo} caracteres, veio {len(valor.strip())}"
    return None


def tools_visiveis(ocultas: Sequence[str] | frozenset[str] = frozenset()) -> list[Operacao]:
    """As operações do catálogo menos as ocultas, em ordem estável.

    O filtro existe para a T17 (`VariantConfig.tools_ocultas`): uma variante do experimento faz
    uma tool sumir de `list_tools`. As variantes NÃO são implementadas aqui — só o ponto onde
    elas entram. A ordem é a do contrato, e é estável de propósito: catálogo em ordem variável
    mudaria o prompt entre runs e viraria um confundidor silencioso na comparação de modelos.
    """
    ocultas = frozenset(ocultas)
    return [op for nome, op in carregar_operacoes().items() if nome not in ocultas]
