"""
A porta por onde o JUDGE fala com um modelo. Fica aqui, e não em `sut/llm.py`, de propósito.

POR QUE UM SEGUNDO CLIENTE (A1, 15/08)
    `sut/llm.py` declara que não conhece provedor pago, não lê chave de API de ambiente
    nenhum e não tem fallback para nuvem — e essa promessa vale, porque é ela que garante
    que o SUT medido é o modelo local que o manifesto declara. O judge é o outro lado do
    arranjo híbrido do A1: SUT local (alto volume, modelo pequeno), judge na free tier
    (baixo volume, modelo grande). Ele PRECISA sair para a rede, e enfiar essa capacidade no
    cliente do SUT abriria caminho para um SUT sair para a rede sem que nada quebrasse.

    O que este módulo reaproveita é o que é protocolo, não política: `esquema_estrito` e o
    `RespostaDoModelo` de `sut/llm.py`. O endpoint do Gemini é OpenAI-compatible e aceita
    `response_format={"type":"json_schema","strict":true}`, então a gramática do decodificador
    — que é o que impede `parse_erro` de virar variável do experimento (`ARQUITETURA §5`,
    decisão 4) — vale igual dos dois lados.

TOKENS DE RACIOCÍNIO SÃO CUSTO, E O ENDPOINT OPENAI NÃO OS SEPARA (medido em 24/08)
    O `usage` do endpoint compatível devolve `prompt_tokens` e `completion_tokens` cuja soma
    é MENOR que `total_tokens`: numa medição de controle, 21 + 228 contra 711. A diferença
    são os tokens de raciocínio, que o endpoint nativo expõe como `thoughtsTokenCount` e o
    compatível só entrega pela subtração.

    Ignorá-los subestimaria o custo do judge em ~65% no eixo x de H0 — exatamente na direção
    que favorece a conclusão que o trabalho quer defender ("julgar com LLM é barato"). Por
    isso `RespostaDoJudge.tokens_raciocinio` existe e entra em `tokens_out` no medidor. Ver
    `A20` no `DECISOES.md`.

O SNAPSHOT É FIXO, NUNCA `-latest`
    `MODELO_PADRAO` é um id datado. Os aliases `gemini-flash-latest` e afins mudam de modelo
    sob o pé, e a T23 congela o judge por sha256 justamente para que ele não mude: um alias
    tornaria o congelamento decorativo.
"""

from __future__ import annotations

import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any

import httpx

from tapieval.schema.trace import ModelConfig
from tapieval.sut.llm import RespostaDoModelo, _interpretar

BASE_URL_PADRAO = "https://generativelanguage.googleapis.com/v1beta/openai"

MODELO_PADRAO = "gemini-3.6-flash"
"""Snapshot `3.6-flash-07-2026`, escolhido em 24/08. Flash e não Pro porque o judge são
~1.400 chamadas (A1) e o Pro não cabe no RPD da free tier; datado e não preview porque a T23
o congela. Folgadamente mais forte que os SUTs Qwen3 8B/14B locais, que é o requisito de
validade da N3 que o A1 nomeia."""

TIMEOUT_PADRAO_S = 120.0
"""O judge faz uma chamada por execução, não um laço. Mais curto que os 300 s do SUT local
porque aqui o custo de esperar é RPD queimado, não GPU ocupada."""

SERVIDO_POR = "gemini_api"
"""O que vai para `ModelConfig.served_by` e daí para o manifesto. O TAPI §9 exige declarar
que o judge roda em serviço externo, e este é o campo que o declara (A1)."""

JANELA_DO_MODELO_PADRAO = 1_048_576
"""`inputTokenLimit` do `gemini-3.6-flash`, lido do catálogo da API em 24/08. Registrado
porque o TAPI §9 exige, e porque o judge com trace é a chamada longa do trabalho (3–8× mais
tokens que o cego, `METRICAS §4`) — a folga de janela é o que garante que ele não trunca."""

CAMINHO_DE_COMPLETACAO = "/chat/completions"

VARIAVEL_DA_CHAVE = "GEMINI_API_KEY"

STATUS_TRANSITORIOS = frozenset({429, 500, 502, 503, 504})
"""Falha do serviço, não do julgamento. 429 é o limite de RPM da free tier, e os 5xx são o
que o Google devolve sob carga — um 503 apareceu na primeira chamada de smoke em 24/08.

Existe porque o judge são ~1.400 chamadas (A1) numa free tier com limite por minuto: sem
retry, a bateria da T24 morre no meio da noite e as runs afetadas ficariam sem N3. E run sem
N3 não é run limpa — sumiriam C1..C7 da amostra, que é o mesmo silêncio do X9."""

ESPERAS_S = (2.0, 8.0, 30.0)
"""Backoff das retentativas de transporte. Cresce até meio minuto porque o limite da free
tier é por MINUTO: esperar 2 s três vezes não sai da janela que causou o 429."""


class ChaveAusente(RuntimeError):
    """Nenhuma credencial para o judge. Erro alto e cedo, nunca degrade silencioso.

    Um fallback para modelo local aqui poria o judge ABAIXO dos agentes que ele julga, que é
    o problema de validade da N3 que o A1 recusou ao decidir não usar o Gemini como SUT."""


def ler_chave(env_path: Path | None = None) -> str:
    """A chave, do ambiente ou do `.env` da raiz — nesta ordem.

    O `.env` do projeto é escrito como `GEMINI_API_KEY = valor`, com espaços em volta do
    `=`: isso NÃO é atribuição válida de shell, e um `source .env` deixa a variável vazia
    sem erro. Ler o arquivo aqui evita que o judge saia com credencial vazia por um detalhe
    de formatação.
    """
    do_ambiente = os.environ.get(VARIAVEL_DA_CHAVE, "").strip()
    if do_ambiente:
        return do_ambiente

    caminho = env_path if env_path is not None else Path(__file__).resolve().parents[3] / ".env"
    if caminho.exists():
        for linha in caminho.read_text(encoding="utf-8").splitlines():
            nome, separador, valor = linha.partition("=")
            if separador and nome.strip() == VARIAVEL_DA_CHAVE and valor.strip():
                return valor.strip()

    raise ChaveAusente(
        f"{VARIAVEL_DA_CHAVE} não está no ambiente nem em {caminho}. O judge não tem "
        "fallback local por decisão (A1): um judge mais fraco que os SUTs invalidaria a N3."
    )


def config_do_judge(
    model_id: str = MODELO_PADRAO,
    *,
    temperature: float = 0.0,
    max_tokens: int = 2048,
    seed: int | None = None,
) -> ModelConfig:
    """A configuração que vai para o manifesto e para o `ScorerVersion.judge_model`.

    `temperature=0.0` é o default porque o flip rate da T21 mede instabilidade da RUBRICA;
    temperatura alta mediria amostragem, e a INS.7 leria variação que o prompt não causou.
    A calibração pode variar isto de propósito — mas então é condição declarada, não default.
    """
    return ModelConfig(
        model_id=model_id,
        served_by=SERVIDO_POR,
        quantization=None,
        temperature=temperature,
        max_tokens=max_tokens,
        seed=seed,
        structured_output="json_schema",
        context_window=JANELA_DO_MODELO_PADRAO,
    )


class ClienteDoJudge:
    """Cliente síncrono do endpoint OpenAI-compatible do Gemini. Satisfaz `Inferencia`.

    Satisfazer o mesmo Protocol do SUT é o que permite que `pontuar_n3` seja testado com um
    duplo de roteiro fixo, sem rede — a suíte inteira do N3 roda offline, e só o smoke test
    fala com o Google.
    """

    def __init__(
        self,
        modelo: ModelConfig | None = None,
        *,
        chave: str | None = None,
        base_url: str = BASE_URL_PADRAO,
        timeout_s: float = TIMEOUT_PADRAO_S,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.modelo = modelo if modelo is not None else config_do_judge()
        self.base_url = base_url
        self._chave = chave if chave is not None else ler_chave()
        self._http = httpx.Client(
            base_url=base_url,
            timeout=timeout_s,
            transport=transport,
            headers={"Authorization": f"Bearer {self._chave}"},
        )

    def completar(
        self,
        mensagens: Sequence[Mapping[str, str]],
        esquema: Mapping[str, Any],
    ) -> RespostaDoJudge:
        """Uma passada pelo judge. Mesma assimetria do SUT: erro de formato volta como dado,
        erro de transporte sobe."""
        corpo = self._corpo(mensagens, esquema)

        inicio = time.perf_counter()
        resposta = self._postar_com_retentativa(corpo)
        latencia_ms = round((time.perf_counter() - inicio) * 1000)

        payload = resposta.json()
        base = _interpretar(payload, esquema, latencia_ms, enviado=corpo["messages"])
        return RespostaDoJudge.a_partir_de(base, payload)

    def _postar_com_retentativa(self, corpo: Mapping[str, Any]) -> httpx.Response:
        """POST com backoff nos status transitórios. Erro permanente sobe na hora.

        A latência medida INCLUI a espera de propósito: `judge_latencia_ms` alimenta INS.4, e
        uma noite de bateria que passou metade do tempo esperando o rate limit gastou esse
        tempo. Esconder a espera faria o eixo x de H0 medir o judge num mundo sem limite.
        """
        ultima: httpx.Response | None = None
        for tentativa in range(len(ESPERAS_S) + 1):
            resposta = self._http.post(CAMINHO_DE_COMPLETACAO, json=corpo)
            if resposta.status_code not in STATUS_TRANSITORIOS:
                resposta.raise_for_status()
                return resposta
            ultima = resposta
            if tentativa < len(ESPERAS_S):
                time.sleep(ESPERAS_S[tentativa])

        assert ultima is not None
        ultima.raise_for_status()
        raise AssertionError("inalcançável: raise_for_status já levantou")

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
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "julgamento", "strict": True, "schema": dict(esquema)},
            },
        }
        if modelo.seed is not None:
            corpo["seed"] = modelo.seed
        return corpo

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> ClienteDoJudge:
        return self

    def __exit__(
        self,
        classe: type[BaseException] | None,
        erro: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


@dataclass(frozen=True)
class RespostaDoJudge(RespostaDoModelo):
    """`RespostaDoModelo` mais os tokens de raciocínio, que o SUT local não reporta.

    Herda em vez de duplicar para continuar satisfazendo `Inferencia` — quem só quer
    `prompt_tokens` e `completion_tokens` não precisa saber que este campo existe.
    """

    tokens_raciocinio: int = 0

    @classmethod
    def a_partir_de(
        cls, base: RespostaDoModelo, payload: Mapping[str, Any]
    ) -> RespostaDoJudge:
        """Deriva o raciocínio por subtração: `total - prompt - completion`.

        Nunca negativo: um servidor que não devolva `total_tokens`, ou que o devolva já
        somado de outro jeito, produz zero em vez de um número inventado — subestimar com
        zero é ruim, mas inventar um custo seria pior, e o zero é auditável contra o
        `usage_ausente` que o `RespostaDoModelo` já carrega."""
        uso = payload.get("usage")
        raciocinio = 0
        if isinstance(uso, dict) and "total_tokens" in uso:
            raciocinio = max(
                0,
                int(uso["total_tokens"]) - base.prompt_tokens - base.completion_tokens,
            )
        return cls(
            texto=base.texto,
            conteudo=base.conteudo,
            parse_ok=base.parse_ok,
            parse_erro=base.parse_erro,
            prompt_tokens=base.prompt_tokens,
            completion_tokens=base.completion_tokens,
            finish_reason=base.finish_reason,
            latencia_ms=base.latencia_ms,
            usage_ausente=base.usage_ausente,
            tokens_raciocinio=raciocinio,
        )
