"""
Cliente de inferência local — a única porta por onde o SUT fala com um modelo.

ORÇAMENTO ZERO E MODELO LOCAL (`ARQUITETURA §13`)
    O endpoint é OpenAI-compatible (LM Studio, Ollama ou `mlx_lm.server`), e é sempre local.
    Este módulo não conhece provedor pago, não lê chave de API de ambiente nenhum e não tem
    fallback para nuvem: o custo do experimento é tempo de GPU, e a `T35` mede tokens porque
    tokens são o eixo x do H0, não porque alguém vá pagar por eles.

SAÍDA ESTRUTURADA É DO SERVIDOR DE INFERÊNCIA, NUNCA DO PROMPT (`PLANO` T16)
    `response_format={"type": "json_schema", ...}` obriga a gramática no decodificador. Pedir
    JSON por instrução no prompt transformaria "o modelo obedece formato" numa variável do
    experimento, e ela contaminaria justamente a métrica que separa os modelos — `parse_erro`
    é o principal confound entre eles (`ARQUITETURA §5`, decisão 4). Com a gramática ligada, o
    `parse_erro` que sobra é falha de schema real, não de formatação.

    `ModelConfig.structured_output` decide o mecanismo e é registrado no manifesto (TAPI §9).
    `none` existe para medir o custo de NÃO ter gramática — é uma condição experimental
    legítima, não um degrade silencioso.

PARSE_ERRO É MÉTRICA, NÃO EXCEÇÃO
    Resposta que não valida volta como `RespostaDoModelo(parse_ok=False)` com o texto cru
    preservado. Quem chama decide o que fazer (o `Agent` reapresenta o erro ao modelo, uma
    vez); o que nunca acontece é a tentativa desaparecer do trace.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ValidationError

from tapieval.schema.trace import ModelConfig

TIMEOUT_PADRAO_S = 300.0
"""Cinco minutos. Um modelo de 14B em quantização 4 bits com 15 schemas na janela leva
dezenas de segundos por passo em GPU de notebook; o timeout de 10s do cliente da API
(`env/client.py`) mataria toda run e a bateria mediria o timeout, não o modelo."""

NOME_DO_ESQUEMA = "passo_do_agente"

CAMINHO_DE_COMPLETACAO = "/chat/completions"


@dataclass(frozen=True)
class RespostaDoModelo:
    """Uma passada pelo modelo, com tudo que o `LLMCall` do trace precisa.

    `conteudo` é o dicionário já validado contra o esquema pedido, ou `None` quando não
    validou. `texto` é sempre o que o modelo devolveu, inclusive quando não validou — é o
    único material de diagnóstico de um `parse_erro`, e descartá-lo tornaria a falha
    incontável e inexplicável ao mesmo tempo.
    """

    texto: str
    conteudo: Mapping[str, Any] | None
    parse_ok: bool
    parse_erro: str | None
    prompt_tokens: int
    completion_tokens: int
    finish_reason: str
    latencia_ms: int
    usage_ausente: bool = False
    """O servidor de inferência não devolveu `usage`, e os tokens acima são ESTIMATIVA.

    Não é detalhe: os tokens são o eixo de custo do H0 (`T35`). O campo existe para que a
    estimativa nunca passe por medição — o `Agent` transforma isto em `RunError` não fatal no
    trace, e a análise pode excluir a run em vez de somar um número inventado."""


class Inferencia(Protocol):
    """O que o agente precisa de um modelo. Existe para o teste injetar roteiro fixo.

    Estreito de propósito: uma função de mensagens + esquema para `RespostaDoModelo`. Sem
    streaming, sem estado entre chamadas — o histórico é do agente, porque é ele que decide
    o que o modelo vê (`sut/agent.py`).
    """

    modelo: ModelConfig

    def completar(
        self,
        mensagens: Sequence[Mapping[str, str]],
        esquema: Mapping[str, Any],
    ) -> RespostaDoModelo: ...


class ClienteDeInferencia:
    """Cliente síncrono de um endpoint OpenAI-compatible local, um por bateria.

    Síncrono porque uma run é sequencial e a GPU é única: o paralelismo da bateria é de duas
    runs (`asyncio.Semaphore(2)` na T18), e vem do prefix cache do servidor de inferência, não
    de concorrência dentro da run.
    """

    def __init__(
        self,
        base_url: str,
        modelo: ModelConfig,
        *,
        timeout_s: float = TIMEOUT_PADRAO_S,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url
        self.modelo = modelo
        self._http = httpx.Client(base_url=base_url, timeout=timeout_s, transport=transport)

    def completar(
        self,
        mensagens: Sequence[Mapping[str, str]],
        esquema: Mapping[str, Any],
    ) -> RespostaDoModelo:
        """Um passo do modelo. Erro de formato volta como dado; erro de transporte sobe.

        A assimetria é deliberada. `parse_erro` é comportamento do modelo sob avaliação e
        pertence ao trace; endpoint fora do ar é falha do instrumento, e mascará-la de
        `parse_erro` inflaria a métrica que separa os modelos com defeito nosso.
        """
        corpo = self._corpo(mensagens, esquema)

        inicio = time.perf_counter()
        resposta = self._http.post(CAMINHO_DE_COMPLETACAO, json=corpo)
        latencia_ms = round((time.perf_counter() - inicio) * 1000)
        resposta.raise_for_status()

        return _interpretar(resposta.json(), esquema, latencia_ms, enviado=corpo["messages"])

    def _corpo(
        self, mensagens: Sequence[Mapping[str, str]], esquema: Mapping[str, Any]
    ) -> dict[str, Any]:
        modelo = self.modelo
        corpo: dict[str, Any] = {
            "model": modelo.model_id,
            "messages": [dict(mensagem) for mensagem in mensagens],
            "temperature": modelo.temperature,
            "max_tokens": modelo.max_tokens,
            "stream": False,
        }
        if modelo.top_p is not None:
            corpo["top_p"] = modelo.top_p
        if modelo.seed is not None:
            corpo["seed"] = modelo.seed

        if modelo.structured_output == "json_schema":
            corpo["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": NOME_DO_ESQUEMA,
                    "strict": True,
                    "schema": dict(esquema),
                },
            }
        elif modelo.structured_output == "grammar":
            raise NotImplementedError(
                "structured_output='grammar' não está implementado: a sintaxe de GBNF é do "
                "llama.cpp e não viaja no protocolo OpenAI. Quebrar aqui é melhor que cair "
                "em `prompt` em silêncio e medir outro mecanismo do que o manifesto declara."
            )
        # `prompt` e `none` não mandam `response_format`: a diferença entre os dois é o
        # conteúdo do system prompt, que é do `Agent`, não deste cliente.

        return corpo

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> ClienteDeInferencia:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def _interpretar(
    payload: Mapping[str, Any],
    esquema: Mapping[str, Any],
    latencia_ms: int,
    *,
    enviado: Sequence[Mapping[str, str]],
) -> RespostaDoModelo:
    """A resposta do endpoint virando `RespostaDoModelo`, com os tokens medidos ou estimados."""
    escolha = (payload.get("choices") or [{}])[0]
    mensagem = escolha.get("message") or {}
    texto = mensagem.get("content") or ""
    finish_reason = escolha.get("finish_reason") or "desconhecido"

    uso = payload.get("usage")
    if isinstance(uso, dict) and "prompt_tokens" in uso and "completion_tokens" in uso:
        prompt_tokens = int(uso["prompt_tokens"])
        completion_tokens = int(uso["completion_tokens"])
        usage_ausente = False
    else:
        prompt_tokens = _tokens_estimados(
            "".join(mensagem.get("content") or "" for mensagem in enviado)
        )
        completion_tokens = _tokens_estimados(texto)
        usage_ausente = True

    conteudo, parse_erro = _validar(texto, esquema)
    return RespostaDoModelo(
        texto=texto,
        conteudo=conteudo,
        parse_ok=parse_erro is None,
        parse_erro=parse_erro,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        finish_reason=finish_reason,
        latencia_ms=latencia_ms,
        usage_ausente=usage_ausente,
    )


def _validar(
    texto: str, esquema: Mapping[str, Any]
) -> tuple[Mapping[str, Any] | None, str | None]:
    """JSON + schema. A mensagem de erro é curta de propósito: ela volta para o modelo.

    Devolver o `ValidationError` inteiro do Pydantic ocuparia centenas de tokens da janela
    com URLs de documentação, e a segunda tentativa perderia contexto útil para caber.
    """
    try:
        carregado = json.loads(texto)
    except ValueError as erro:
        return None, f"json_invalido: {erro}"

    if not isinstance(carregado, dict):
        return None, f"json_invalido: esperava objeto, veio {type(carregado).__name__}"

    modelo = _MODELO_POR_ESQUEMA.get(id(esquema))
    if modelo is None:
        return carregado, None
    try:
        modelo.model_validate(carregado)
    except ValidationError as erro:
        return None, "schema_invalido: " + "; ".join(
            f"{'.'.join(str(parte) for parte in problema['loc'])}: {problema['msg']}"
            for problema in erro.errors()[:3]
        )
    return carregado, None


_MODELO_POR_ESQUEMA: dict[int, type[BaseModel]] = {}
"""Esquema (por identidade) → modelo Pydantic que o valida.

Registrado por `esquema_estrito`, que é quem produz os dois ao mesmo tempo. Existe porque
`Inferencia.completar` recebe o esquema como dicionário — é isso que viaja no protocolo — e
validar de volta com o Pydantic dá mensagem de erro melhor do que qualquer validador de
JSON Schema genérico daria. Chaveado por `id()` e não pelo conteúdo porque o dicionário não
é hashável e serializá-lo a cada passo custaria mais que a validação."""


def esquema_estrito(modelo: type[BaseModel]) -> dict[str, Any]:
    """O JSON Schema de um modelo Pydantic no formato que decodificador com gramática aceita.

    Três ajustes sobre o que o Pydantic gera, todos exigidos pelo modo `strict` da API
    OpenAI e igualmente aceitos por LM Studio e Ollama:

    1. `additionalProperties: false` em todo objeto — sem isso o modelo pode inventar campo,
       e campo inventado passaria pela validação sem virar `parse_erro`;
    2. todo campo em `required`, inclusive o opcional — opcionalidade se expressa por
       `anyOf: [..., {"type": "null"}]`, que é o que o Pydantic já emite para `X | None`;
    3. `default` removido — a gramática não preenche default, e deixá-lo declarado sugere
       que o campo pode faltar.
    """
    esquema = modelo.model_json_schema()
    _endurecer(esquema)
    _MODELO_POR_ESQUEMA[id(esquema)] = modelo
    return esquema


def _endurecer(no: Any) -> None:
    if isinstance(no, dict):
        no.pop("default", None)
        if no.get("type") == "object" and "properties" in no:
            no["additionalProperties"] = False
            no["required"] = list(no["properties"])
        for valor in no.values():
            _endurecer(valor)
    elif isinstance(no, list):
        for item in no:
            _endurecer(item)


def _tokens_estimados(texto: str) -> int:
    """Quatro caracteres por token. Só entra em cena com `usage_ausente=True`."""
    return max(1, len(texto) // 4) if texto else 0
