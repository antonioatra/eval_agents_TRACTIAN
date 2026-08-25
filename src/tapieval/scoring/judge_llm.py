"""
A porta por onde o JUDGE fala com um modelo. Fica aqui, e não em `sut/llm.py`, de propósito.

POR QUE UM SEGUNDO CLIENTE (A1, 15/08)
    `sut/llm.py` declara que não conhece provedor pago, não lê chave de API de ambiente
    nenhum e não tem fallback para nuvem — e essa promessa vale, porque é ela que garante
    que o SUT medido é o modelo local que o manifesto declara. O judge é o outro lado do
    arranjo híbrido do A1: SUT local (alto volume, modelo pequeno), judge na nuvem (baixo
    volume, modelo grande). Ele PRECISA sair para a rede, e enfiar essa capacidade no
    cliente do SUT abriria caminho para um SUT sair para a rede sem que nada quebrasse.

    Desde 25/08 o judge fala com o **Vertex** e não com a free tier do AI Studio. O motivo
    é de cronograma, não de qualidade: a free tier dava 20 chamadas por DIA (§5 do
    `docs/limites_free_tier.md`), o que punha as ~1.400 do judge a 70 dias. O Vertex serve
    os flash por dynamic shared quota, sem RPD, e o crédito de trial o paga. Os dois
    provedores continuam implementados: o AI Studio é para onde se volta.

    O que este módulo reaproveita é o que é protocolo, não política: `esquema_estrito` e o
    `RespostaDoModelo` de `sut/llm.py`. O endpoint do Gemini é OpenAI-compatible e aceita
    `response_format={"type":"json_schema","strict":true}`, então a gramática do decodificador
    — que é o que impede `parse_erro` de virar variável do experimento (`ARQUITETURA §5`,
    decisão 4) — vale igual dos dois lados.

TOKENS DE RACIOCÍNIO SÃO CUSTO, E SÓ UM DOS DOIS PROVEDORES OS DECLARA
    O `usage` do compat do **AI Studio** devolve `prompt_tokens` e `completion_tokens` cuja
    soma é MENOR que `total_tokens`: numa medição de controle, 21 + 228 contra 711. A
    diferença são os tokens de raciocínio, que o endpoint nativo expõe como
    `thoughtsTokenCount` e o compatível só entrega pela subtração (A20, 24/08).

    O compat do **Vertex** declara o número: `completion_tokens_details.reasoning_tokens`
    (medido em 25/08). `RespostaDoJudge` prefere o declarado e cai na subtração quando ele
    não vem — então a migração troca um custo reconstruído por um custo medido.

    Ignorá-los subestimaria o custo do judge em ~65% no eixo x de H0 — exatamente na direção
    que favorece a conclusão que o trabalho quer defender ("julgar com LLM é barato").

O ID DO MODELO É UM ALIAS NOS DOIS PROVEDORES (corrigido em 25/08)
    Esta seção dizia que `MODELO_PADRAO` era um id datado e que "um alias tornaria o
    congelamento decorativo". A segunda metade continua verdadeira; a primeira não era.

    O catálogo do AI Studio NOMEIA o snapshot (`version: 3.6-flash-07-2026`), mas chamar
    esse id datado devolve **404**: ele é legível, não chamável. No Vertex o `versionId` é
    `default`, e nem legível é. Os dois lados servem o alias.

    Consequência para a T23: o sha256 congela o PROMPT e o id, não o peso do outro lado.
    Contra troca de modelo sob o pé o que resta é medir — o canário do `checar_judge.py`,
    rodado antes e depois da bateria.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any

import httpx

from tapieval.schema.trace import ModelConfig
from tapieval.sut.llm import RespostaDoModelo, _interpretar

AI_STUDIO = "ai_studio"
VERTEX = "vertex"

PROVEDOR_PADRAO = VERTEX
"""Migrado em 25/08. O AI Studio continua inteiro e chamável de propósito: é o único dos
dois cujo catálogo ainda NOMEIA o snapshot por trás do alias, e é para lá que se volta se
o canário da T23 acusar troca de modelo sob o pé."""

BASE_URL_AI_STUDIO = "https://generativelanguage.googleapis.com/v1beta/openai"

LOCAL_VERTEX_PADRAO = "global"
"""Medido em 25/08: os flash 3.x respondem em `global` e dão 404 em `us-central1`. E
`global` é a única região cujo host não leva prefixo — `global-aiplatform...` não existe."""

VARIAVEL_DO_PROJETO = "GOOGLE_CLOUD_PROJECT"

ESCOPO_DO_VERTEX = "https://www.googleapis.com/auth/cloud-platform"


def base_url_do_vertex(projeto: str, local: str = LOCAL_VERTEX_PADRAO) -> str:
    """O endpoint OpenAI-compatible do Vertex, que carrega projeto e região na própria URL.

    É por isso que o Vertex não precisa de `x-goog-user-project` aqui e o catálogo precisa:
    lá o projeto vai no cabeçalho, aqui ele já está no caminho.
    """
    if local == "global":
        host = "aiplatform.googleapis.com"
    else:
        host = f"{local}-aiplatform.googleapis.com"
    return f"https://{host}/v1/projects/{projeto}/locations/{local}/endpoints/openapi"


MODELO_PADRAO = "gemini-3.6-flash"
"""Flash e não Pro porque o judge são ~1.400 chamadas (A1). Folgadamente mais forte que os
SUTs Qwen3 8B/14B locais, que é o requisito de validade da N3 que o A1 nomeia.

ATENÇÃO, CORRIGIDO EM 25/08: este id é um ALIAS, e sempre foi. O catálogo do AI Studio diz
`version: 3.6-flash-07-2026`, mas esse id datado responde **404** — ele é legível, não
chamável. No Vertex nem legível: o `versionId` é `default`. Então a T23 nunca teve um
snapshot fixo para congelar, e o sha256 congela o PROMPT e o id, não o peso do outro lado.
A defesa real contra troca de modelo sob o pé é o canário: rodar o fixture de defeito
plantado do `checar_judge.py` antes e depois da bateria e comparar."""

TIMEOUT_PADRAO_S = 120.0
"""O judge faz uma chamada por execução, não um laço. Mais curto que os 300 s do SUT local
porque aqui o custo de esperar é tempo de bateria, não GPU ocupada."""

SERVIDO_POR_POR_PROVEDOR = {AI_STUDIO: "gemini_api", VERTEX: "vertex_ai"}
"""O que vai para `ModelConfig.served_by` e daí para o manifesto. O TAPI §9 exige declarar
que o judge roda em serviço externo, e este é o campo que o declara (A1). Os dois provedores
servem o MESMO modelo, então distinguir aqui é o que impede uma bateria julgada metade em
cada um de passar despercebida na leitura do manifesto."""

PREFIXO_DO_PUBLISHER = "google/"
"""O compat do Vertex exige `google/gemini-3.6-flash`; o do AI Studio recusa o prefixo.

Ele é detalhe de fio, não identidade: `ModelConfig.model_id` guarda `gemini-3.6-flash` nos
dois casos, para que os manifestos da piloto e da bateria continuem comparáveis campo a
campo. Quem diz o provedor é `served_by`."""

JANELA_DO_MODELO_PADRAO = 1_048_576
"""`inputTokenLimit` do `gemini-3.6-flash`, lido do catálogo da API em 24/08. Registrado
porque o TAPI §9 exige, e porque o judge com trace é a chamada longa do trabalho (3–8× mais
tokens que o cego, `METRICAS §4`) — a folga de janela é o que garante que ele não trunca."""

CAMINHO_DE_COMPLETACAO = "/chat/completions"

VARIAVEL_DA_CHAVE = "GEMINI_API_KEY"

STATUS_TRANSITORIOS = frozenset({429, 500, 502, 503, 504})
"""Falha do serviço, não do julgamento. Os 5xx são o que o Google devolve sob carga — um 503
apareceu na primeira chamada de smoke em 24/08 — e o 429 muda de sentido com o provedor:
na free tier do AI Studio era quota estourada, no Vertex é capacidade compartilhada
("Vertex AI is overloaded"), que passa sozinha.

Existe porque o judge são ~1.400 chamadas (A1) numa free tier com limite por minuto: sem
retry, a bateria da T24 morre no meio da noite e as runs afetadas ficariam sem N3. E run sem
N3 não é run limpa — sumiriam C1..C7 da amostra, que é o mesmo silêncio do X9."""

ESPERAS_S = (2.0, 8.0, 30.0)
"""Backoff das retentativas de transporte, usado quando a resposta NÃO diz quanto esperar.

Passa a valer MAIS depois da migração, não menos: o 429 de quota do AI Studio vinha com
`Please retry in ...s`, e o 429 de sobrecarga do Vertex não traz número nenhum. Lá o
`espera_pedida` devolve `None` quase sempre, e este backoff é o plano inteiro."""

TETO_DE_ESPERA_S = 75.0
"""Teto para a espera pedida pela própria API. A janela do limite de RPM é de um minuto, e
uma espera pedida acima disso é sinal de outra coisa (quota diária, projeto suspenso) — casos
em que insistir queima tempo de madrugada sem chance de sucesso."""

_ESPERA_PEDIDA = re.compile(r"retry in ([0-9]+(?:\.[0-9]+)?)s", re.IGNORECASE)


def espera_pedida(corpo: str) -> float | None:
    """Os segundos que o corpo do 429 pede, quando ele pede.

    O Google devolve `"Please retry in 35.52310516s"` junto com a quota estourada, e esse
    número é MELHOR que qualquer backoff que a gente escolha: ele é a janela deslizante real,
    medida do lado do servidor. Ignorá-lo foi o que fez a primeira calibração morrer — as
    esperas fixas (2 s, 8 s, 30 s) somam 40 s e caíram todas dentro de uma janela que o
    próprio serviço tinha dito que duraria 35 s a partir de um instante posterior.

    Vale mais na T24 que aqui: uma bateria de madrugada que desiste porque esperou 30 s onde
    o serviço pediu 38 perde a noite inteira, e as runs afetadas ficam sem N3.
    """
    achado = _ESPERA_PEDIDA.search(corpo)
    if achado is None:
        return None
    return min(float(achado.group(1)) + 1.0, TETO_DE_ESPERA_S)


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


def credencial_estatica(chave: str) -> Callable[[], str]:
    """A chave do AI Studio não expira, então o portador é sempre o mesmo."""
    return lambda: chave


def credencial_do_vertex() -> Callable[[], str]:
    """O portador do Vertex, renovado quando vence.

    POR QUE UM CALLABLE E NÃO UMA STRING
        O token OAuth do Google vale **1 h**. A bateria da T24 roda a madrugada inteira e o
        judge são ~1.400 chamadas: um token lido uma vez no `__init__` expira no meio, e o
        que se perde não é a chamada — é a noite, com as runs afetadas ficando sem N3. Esse
        é o mesmo silêncio do X9 que o §3 do `docs/limites_free_tier.md` já pagou para
        aprender uma vez, e não vale a pena aprender de novo por outro motivo.

        `google-auth` guarda o vencimento e renova sozinho; o que este módulo faz é chamar
        o portador A CADA request em vez de fixá-lo no cabeçalho do cliente.
    """
    try:
        import google.auth
        import google.auth.transport.requests
    except ModuleNotFoundError as erro:  # pragma: no cover — depende do ambiente
        raise ChaveAusente(
            "google-auth não está instalado; o judge no Vertex precisa dele para renovar "
            "o token de 1 h. `pip install google-auth`."
        ) from erro

    try:
        credencial, _ = google.auth.default(scopes=[ESCOPO_DO_VERTEX])
    except Exception as erro:  # pragma: no cover — depende do ambiente
        raise ChaveAusente(
            f"sem credencial de aplicação (ADC) para o Vertex: {erro}. "
            "Rode `gcloud auth application-default login`."
        ) from erro

    pedido = google.auth.transport.requests.Request()

    def portador() -> str:
        if not credencial.valid:
            credencial.refresh(pedido)
        return str(credencial.token)

    return portador


def ler_projeto() -> str:
    """O projeto do Vertex, do ambiente ou da configuração do gcloud."""
    do_ambiente = os.environ.get(VARIAVEL_DO_PROJETO, "").strip()
    if do_ambiente:
        return do_ambiente

    try:
        import google.auth

        _, projeto = google.auth.default(scopes=[ESCOPO_DO_VERTEX])
    except Exception:  # pragma: no cover — depende do ambiente
        projeto = None

    if not projeto:
        raise ChaveAusente(
            f"projeto do Vertex não determinado. Defina {VARIAVEL_DO_PROJETO} ou rode "
            "`gcloud config set project SEU_PROJETO`."
        )
    return str(projeto)


def config_do_judge(
    model_id: str = MODELO_PADRAO,
    *,
    provedor: str = PROVEDOR_PADRAO,
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
        served_by=SERVIDO_POR_POR_PROVEDOR[provedor],
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
        provedor: str | None = None,
        chave: str | None = None,
        credencial: Callable[[], str] | None = None,
        projeto: str | None = None,
        local: str = LOCAL_VERTEX_PADRAO,
        base_url: str | None = None,
        timeout_s: float = TIMEOUT_PADRAO_S,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        # `chave` explícita significa AI Studio mesmo quando o padrão é Vertex: é assim que
        # a suíte injeta um portador falso sem precisar de ADC nem de rede.
        if provedor is None:
            provedor = AI_STUDIO if chave is not None else PROVEDOR_PADRAO
        if provedor not in SERVIDO_POR_POR_PROVEDOR:
            raise ValueError(f"provedor desconhecido: {provedor!r}")

        self.provedor = provedor
        self.modelo = modelo if modelo is not None else config_do_judge(provedor=provedor)

        if provedor == VERTEX:
            self.projeto = projeto if projeto is not None else ler_projeto()
            self.base_url = base_url or base_url_do_vertex(self.projeto, local)
            # Injetável para que a suíte exercite o portador RENOVÁVEL sem ADC e sem rede:
            # é a única forma de provar por teste que o token é relido a cada tentativa.
            self._credencial = credencial or credencial_do_vertex()
        else:
            self.projeto = None
            self.base_url = base_url or BASE_URL_AI_STUDIO
            self._credencial = credencial or credencial_estatica(
                chave if chave is not None else ler_chave()
            )

        self._http = httpx.Client(
            base_url=self.base_url,
            timeout=timeout_s,
            transport=transport,
        )
        self.eventos_de_limite: list[dict[str, Any]] = []
        """Todo status transitório recebido, com o instante e a espera que se seguiu.

        Existe porque os limites de RPM/RPD da free tier NÃO estão publicados: a página de
        rate limits do Google manda consultar o AI Studio em vez de imprimir a tabela. A
        única forma de saber onde eles ficam é bater neles com carga real, e a calibração da
        T21 é a primeira carga real do projeto. Sem este registro a informação passa e some
        dentro do backoff, que engole o 429 por desenho.

        É lista e não contador porque o que decide o dimensionamento da T24 é a DISTRIBUIÇÃO
        no tempo: dez 429 no mesmo minuto são um limite de RPM, dez espalhados pelo dia são
        um limite de RPD, e as duas leituras pedem manifestos diferentes."""

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
            # O cabeçalho é montado A CADA tentativa, e não no cliente: no Vertex o token
            # vence em 1 h, e uma retentativa depois de uma espera longa pode ser a primeira
            # chamada do outro lado do vencimento.
            resposta = self._http.post(
                CAMINHO_DE_COMPLETACAO,
                json=corpo,
                headers={"Authorization": f"Bearer {self._credencial()}"},
            )
            if resposta.status_code not in STATUS_TRANSITORIOS:
                resposta.raise_for_status()
                return resposta
            ultima = resposta
            padrao = ESPERAS_S[tentativa] if tentativa < len(ESPERAS_S) else None
            # A espera que o serviço pede vence a nossa mesmo quando é MENOR: ela é a janela
            # real, e dormir mais que o necessário na free tier é tempo de bateria jogado
            # fora. Só o último passo não espera — ali a chamada já vai levantar.
            pedida = espera_pedida(resposta.text)
            espera = padrao if pedida is None else (pedida if padrao is not None else None)
            self.eventos_de_limite.append(
                {
                    "instante": time.time(),
                    "status": resposta.status_code,
                    "tentativa": tentativa,
                    "espera_s": espera,
                    "espera_pedida_s": pedida,
                    # O corpo do erro do Google costuma nomear a quota estourada
                    # (`GenerateRequestsPerMinutePerProject` e afins). É a diferença entre
                    # saber QUE bateu no limite e saber em QUAL limite bateu.
                    "corpo": resposta.text[:600],
                }
            )
            if espera is not None:
                time.sleep(espera)

        assert ultima is not None
        ultima.raise_for_status()
        raise AssertionError("inalcançável: raise_for_status já levantou")

    def _corpo(
        self, mensagens: Sequence[Mapping[str, str]], esquema: Mapping[str, Any]
    ) -> dict[str, Any]:
        modelo = self.modelo
        id_no_fio = (
            f"{PREFIXO_DO_PUBLISHER}{modelo.model_id}"
            if self.provedor == VERTEX
            else modelo.model_id
        )
        corpo: dict[str, Any] = {
            "model": id_no_fio,
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
        """O número declarado quando existe; a subtração quando não.

        MEDIDO EM 25/08, E É UM GANHO DA MIGRAÇÃO
            O compat do **Vertex** devolve `completion_tokens_details.reasoning_tokens`
            explícito. O do **AI Studio** não: numa chamada de controle ele devolveu
            `{prompt: 2, completion: 0, total: 6}`, e os 4 tokens de raciocínio só existiam
            pela subtração — que é exatamente o que o A20 registrou.

            Preferir o declarado importa porque este número entra em `tokens_out` e daí no
            eixo x de H0. Subtrair é reconstrução: ela assume que `total` não contém mais
            nada além das três parcelas, e essa suposição não é verificável do lado de cá.
            Com o campo declarado, o custo do judge passa a ser medido em vez de inferido.

        Nunca negativo: um servidor que não devolva `total_tokens`, ou que o devolva já
        somado de outro jeito, produz zero em vez de um número inventado — subestimar com
        zero é ruim, mas inventar um custo seria pior, e o zero é auditável contra o
        `usage_ausente` que o `RespostaDoModelo` já carrega."""
        uso = payload.get("usage")
        raciocinio = 0
        if isinstance(uso, dict):
            detalhes = uso.get("completion_tokens_details")
            declarado = (
                detalhes.get("reasoning_tokens") if isinstance(detalhes, dict) else None
            )
            if declarado is not None:
                raciocinio = max(0, int(declarado))
            elif "total_tokens" in uso:
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
