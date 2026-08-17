"""
O agente sob avaliação (entregável 1) — laço ReAct sobre um cliente MCP.

O QUE ESTE MÓDULO É, E O QUE ELE DELIBERADAMENTE NÃO SABE
    Ele recebe uma `Solicitacao`, descobre as tools por `list_tools`, hidrata contexto sem
    LLM, roda o laço {modelo → `call_tool` → observação} e responde. **Não conhece gabarito,
    cenário, split nem manifesto.** O corpus só chega aqui pela fatia de ENTRADA
    (`Solicitacao`), nunca pelo `scoring.gabarito.Cenario` — que carrega `tools_esperadas`,
    `decisao_esperada` e `proibido`. Ver "divergência 1" abaixo.

TRÊS DIVERGÊNCIAS EM RELAÇÃO AO `PLANO` T16, TODAS FILHAS DE DECISÕES JÁ TOMADAS

    1. **`run(solicitacao)`, não `run(cenario, writer)`.** Passar o `Cenario` ao agente poria
       o gabarito dentro do alcance do SUT: bastaria um `getattr` acidental — ou um prompt que
       serialize o objeto — para o agente ler as `tools_esperadas` do próprio cenário que está
       resolvendo. A fatia de entrada é `Solicitacao` (`ARQUITETURA §3.1`), e é ela que entra.

    2. **Nenhum `writer` é passado.** Depois do A13 quem escreve o trace é o lado do servidor
       (`mcp/instrumentacao.ObservadorDeTrace`). Um segundo writer no cliente criaria dois
       escritores no mesmo `.jsonl` dentro do MESMO processo — que é justamente a situação em
       que o append atômico do POSIX deixa de bastar (aviso em `schema/writer.py`). O harness
       emite pelo `RunContext` compartilhado, no mesmo observador e no mesmo contador de `seq`.

    3. **`RunStart` e `RunEnd` não saem daqui.** Eles carregam `experiment_id`, `split`,
       `seed`, `env_mode` e `cassette_id` — conhecimento do manifesto, que é do runner (T18).
       O agente devolve os totais em `ResultadoDaRun` para o runner fechar o `RunEnd`. Isso
       impõe uma ordem: o runner emite `RunStart` ANTES de chamar `Agent.run`, senão o
       `seq=1` do trace não é o começo da run e `montar_contexto_da_decisao` não acha o
       `asset_id` da run (o gate depende dele para o D5).

O QUE O MODELO VÊ, E DE ONDE VEM
    O catálogo do prompt é montado a partir de `list_tools` — nunca de uma lista escrita à
    mão (`ARQUITETURA §4.2`). Duas fontes de verdade divergiriam em silêncio: bastaria o
    servidor renomear uma tool para o prompt continuar anunciando a antiga, e a bateria
    mediria "o agente errou a função" quando o instrumento é que estava desalinhado.

    A saída estruturada é imposta pelo servidor de inferência (`sut/llm.py`), não por
    instrução no prompt. O prompt descreve as tools; a gramática garante o formato.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

import mcp_types as types
import yaml
from mcp import ClientSession
from pydantic import BaseModel, Field, model_validator

from tapieval.mcp.instrumentacao import extrair_citacoes
from tapieval.mcp.server import RunContext
from tapieval.schema.trace import (
    BudgetEvent,
    Decisao,
    DecisionEvent,
    FinalAnswer,
    Hydration,
    LLMCall,
    Modo,
    RunError,
    TraceEvent,
    VariantConfig,
)
from tapieval.scoring.n2 import MAX_SAME_ENDPOINT
from tapieval.sut.llm import Inferencia, RespostaDoModelo, esquema_estrito

RAIZ_DO_REPO = Path(__file__).resolve().parents[3]
CAMINHO_DO_PROMPT = RAIZ_DO_REPO / "prompts" / "agente_v1.md"

TENTATIVAS_DE_PARSE = 2
"""Duas passadas pelo modelo por iteração quando a saída não valida.

Uma seria injusta com modelo pequeno (um retry com o erro na janela resolve a maioria dos
casos de schema), e três esconderia o problema: `parse_erro` é o principal confound entre
modelos (`ARQUITETURA §5`, decisão 4) e a métrica só significa algo se o número de chances
for igual e pequeno para todos. As duas tentativas viram dois `LLMCall` no trace, com
`tentativa` 1 e 2 — nenhuma desaparece."""

TOOLS_POR_MODO: Mapping[Modo, frozenset[str]] = {
    "contextualizar": frozenset(
        {"search_knowledge", "get_knowledge_doc", "get_current_user", "get_company"}
    ),
    "investigar": frozenset(
        {
            "get_asset",
            "list_assets_by_company",
            "list_analyses",
            "get_analysis",
            "get_baseline",
            "get_rms_series",
            "get_spectrum",
            "get_data_quality",
            "get_model",
        }
    ),
    "executar": frozenset(
        {
            "update_asset_config",
            "reprocess_analysis",
            "request_specialist_analysis",
            "request_retraining",
            "escalate_case",
        }
    ),
}
"""A partição de `ARQUITETURA §3.2`, com os nomes REAIS do catálogo.

O §3.2 escreve a tabela em nomes de rascunho (`get_procedimento`, `get_glossario`,
`get_orientacao_suporte`, `get_user_context`) que não existem no contrato do parceiro: a base
de conhecimento é `search_knowledge` + `get_knowledge_doc`, com o tipo de documento em
`type`, e o contexto do usuário é `get_current_user`. Duas tools não aparecem na tabela e
foram alocadas aqui: `get_company` em contextualizar (é cadastro da empresa, é leitura de
contexto) e `list_assets_by_company` em investigar (é a tool que desambigua "a bomba 3" em
aut_07, que é investigação). Divergência registrada — a fonte de verdade dos NOMES é
`mcp/tools.py`, que os deriva do OpenAPI, e o teste de contrato garante que este mapa não
inventa nome nenhum.

Só vale quando `VariantConfig.tools_visiveis == "por_modo"`. Na variante `todas` o modelo vê
o catálogo inteiro, e a comparação entre as duas é uma coluna do experimento."""

MODOS_DE_LEITURA: tuple[Modo, ...] = ("contextualizar", "investigar")
"""O catálogo do primeiro passo na variante `por_modo`.

Antes de declarar um modo o agente não pode ver tool de ação: a segregação de `§3.2` existe
para tornar *estruturalmente impossível* o subgrafo de investigação disparar um
retreinamento, e um catálogo inicial com as cinco ações dentro devolveria essa possibilidade
por outra porta."""


# ---------------------------------------------------------------------------
# A fatia de ENTRADA do cenário
# ---------------------------------------------------------------------------


class Solicitacao(BaseModel):
    """O pedido que abre uma run (`ARQUITETURA §3.1`), e nada além dele.

    `case_id` não está na §3.1 e é o fechamento do **X22**: `escalate_case` exige `case_id` no
    caminho e **não existe tool que descubra um** — não há `GET /cases` nem `listCases` no
    contrato. Sem injetá-lo, o agente nunca conseguiria escalar, falhar em escalar é S1 em
    vários cenários, e a bateria mediria uma impossibilidade creditando-a ao modelo.

    A injeção é legítima porque é assim que o dado chega no produto real: quem atende um
    chamado já está DENTRO do chamado. Os 16 cenários oficiais trazem `origem.case_id`; os 8
    autorais não têm caso nenhum (`CENARIOS-AUTORAIS §2.2`), e lá o `None` é o certo — em
    `aut_08` a decisão de escalar é lida do `final_answer` pela N1.4, não da tool.
    """

    message: str
    user_id: str
    asset_id: str | None = None
    thread_id: str = "thr_1"
    case_id: str | None = None


def carregar_solicitacao(caminho: Path) -> Solicitacao:
    """A fatia de entrada de um YAML de cenário. Não lê `gabarito` nem `estado_esperado`.

    Uma porta de entrada por consumidor: `scoring.gabarito.carregar_cenario` lê o gabarito
    para pontuar, esta lê o pedido para executar, e nenhuma das duas vê o lado da outra. É a
    mesma razão da divergência 1 do módulo — o que o agente não pode ler, ele não recebe.
    """
    documento = yaml.safe_load(caminho.read_text(encoding="utf-8"))
    origem = documento.get("origem") or {}
    return Solicitacao(
        message=documento["solicitacao"],
        user_id=documento["user_id"],
        asset_id=documento.get("asset_id"),
        thread_id=f"thr_{documento['id']}",
        case_id=documento.get("case_id") or origem.get("case_id"),
    )


# ---------------------------------------------------------------------------
# O passo do agente — o esquema que a gramática do decodificador impõe
# ---------------------------------------------------------------------------


class AcaoDoPasso(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class RespostaDoPasso(BaseModel):
    texto: str
    citacoes: list[str] = Field(default_factory=list)
    perguntar_de_volta: bool = False


class PassoDoAgente(BaseModel):
    """Um passo: ou uma chamada de tool, ou a resposta final. Nunca as duas, nunca nenhuma.

    O `modo` vem em todo passo porque a triagem de intenção é LLM (`ARQUITETURA §3.3`) e pode
    mudar no meio do atendimento — "por que a análise deu isso?" que termina em
    "reprocessa então" atravessa investigar → executar. Registrar só o primeiro apagaria a
    transição, que é exatamente o que a rubrica avalia.

    `pensamento` é `racional_declarado` no trace: DEBUG, proibido como evidência em scorer
    (`ARQUITETURA §5`, decisão 5).
    """

    modo: Modo
    pensamento: str
    acao: AcaoDoPasso | None = None
    resposta: RespostaDoPasso | None = None
    decisao: Decisao | None = None
    prioridade_escalonamento: Literal["alta", "media", "baixa"] | None = None

    @model_validator(mode="after")
    def _exatamente_um(self) -> PassoDoAgente:
        if (self.acao is None) == (self.resposta is None):
            raise ValueError(
                "preencha `acao` (para chamar uma tool) OU `resposta` (para encerrar), "
                "nunca as duas e nunca nenhuma"
            )
        if self.resposta is not None and self.decisao is None:
            raise ValueError("`resposta` exige `decisao`")
        return self


ESQUEMA_DO_PASSO = esquema_estrito(PassoDoAgente)


# ---------------------------------------------------------------------------
# Trilha — como o harness entra no trace (ou não entra)
# ---------------------------------------------------------------------------


class Trilha(Protocol):
    """Por onde os eventos do CLIENTE saem: `llm_call`, `budget`, `decision`, `final_answer`.

    Existe como protocolo por causa do X23: em memória o harness compartilha o contador de
    `seq` do servidor e escreve no mesmo trace; em stdio ele não tem como participar da mesma
    ordem e não escreve nada (ver `sut/sessao.py`).
    """

    def entrar_na_iteracao(self, iteracao: int) -> None: ...

    def emitir(self, classe: type[Any], /, **campos: Any) -> TraceEvent | None: ...

    def blob(self, texto: str) -> str: ...


class TrilhaDoHarness:
    """A trilha da bateria: mesmo `RunContext`, mesmo contador, mesmo observador.

    `ctx._cabecalho()` é usado de propósito, em vez de montar `seq`/`ts`/`iteration` aqui: uma
    segunda implementação da numeração é uma segunda chance de ela divergir, e `seq` duplicado
    passa pelo reader sem acusar (ele ordena, não valida unicidade). O `PoliticaComGate` da
    T14 evita esse mesmo método por um motivo oposto e específico — ele precisa de um `seq`
    JÁ reservado, e `_cabecalho` tira um novo.
    """

    def __init__(self, ctx: RunContext) -> None:
        self.ctx = ctx

    def entrar_na_iteracao(self, iteracao: int) -> None:
        # O servidor não enxerga o laço do agente (`§4.3`) e não tem como derivar a iteração;
        # sem isto todo evento de tool nasceria com `iteration=0` e `n_iteracoes` da N2 seria
        # incalculável a partir do trace.
        self.ctx.iteracao_atual = iteracao

    def emitir(self, classe: type[Any], /, **campos: Any) -> TraceEvent | None:
        # `classe` é posicional-only: `RunError` tem um campo chamado `classe`, e sem a barra
        # `emitir(RunError, classe="ParseErro")` colidiria com o próprio parâmetro.
        evento = classe(**self.ctx._cabecalho(), **campos)
        self.ctx.observador.emitir(evento)
        return evento

    def blob(self, texto: str) -> str:
        """Persiste o texto se o observador tiver writer; sempre devolve o endereço dele.

        O sha é o do conteúdo, então um trace escrito sem writer (teste com
        `ObservadorEmMemoria`) continua apontando para o mesmo endereço que a bateria
        apontaria. O que falta nesse caso é o arquivo, não a consistência do trace.
        """
        writer = getattr(self.ctx.observador, "writer", None)
        if writer is not None:
            sha: str = writer.blob(texto)
            return sha
        return hashlib.sha256(texto.encode("utf-8")).hexdigest()


class TrilhaDeFronteira:
    """A trilha do stdio: não numera, não emite, não escreve. É o X23 assumido.

    Um agente de terceiro produz exatamente este trace — só o que a fronteira viu — e a T14
    provou que ele sai idêntico ao da bateria para os eventos do servidor. O harness nosso
    rodando por stdio não é mais privilegiado que ele.
    """

    def entrar_na_iteracao(self, iteracao: int) -> None:
        return None

    def emitir(self, classe: type[Any], /, **campos: Any) -> TraceEvent | None:
        return None

    def blob(self, texto: str) -> str:
        return hashlib.sha256(texto.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Resultado
# ---------------------------------------------------------------------------


@dataclass
class ResultadoDaRun:
    """O que o runner precisa para fechar o `RunEnd` — e nada que o trace já diga.

    `status` usa o vocabulário do `RunEnd` porque é para lá que ele vai. `timeout` não é
    produzido aqui: quem tem relógio de parede da run é o runner.
    """

    status: Literal["ok", "budget_exceeded", "error"]
    final_answer: FinalAnswer | None
    n_tool_calls: int = 0
    n_llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    parse_failures: int = 0
    iteracoes: int = 0
    ids_das_chamadas: tuple[str, ...] = ()
    duracao_ms: int = 0


# ---------------------------------------------------------------------------
# O agente
# ---------------------------------------------------------------------------


def carregar_prompt(caminho: Path = CAMINHO_DO_PROMPT) -> str:
    return caminho.read_text(encoding="utf-8")


def sha_do_prompt(texto: str) -> str:
    """`VariantConfig.prompt_sha` — o hash do TEMPLATE, não do prompt renderizado.

    O prompt renderizado muda com o catálogo e com a solicitação, então seu hash não
    identificaria variante nenhuma. O do template identifica: é ele que muda quando a
    variante muda de instrução, que é o que a coluna do experimento significa. O hash do
    prompt renderizado existe também, e vai em cada `LLMCall.prompt_sha`.
    """
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


class Agent:
    """O agente ReAct. Um por run — ele guarda o histórico da conversa e os ids já vistos.

    `sessao` é qualquer `ClientSession` MCP: em memória na bateria, stdio na demo. O agente
    não sabe qual é, e é isso que faz o framework valer para outro cliente também.
    """

    def __init__(
        self,
        variant: VariantConfig,
        model_key: str,
        sessao: ClientSession,
        inferencia: Inferencia,
        *,
        trilha: Trilha | None = None,
        prompt_sistema: str | None = None,
    ) -> None:
        self.variant = variant
        self.model_key = model_key
        self.sessao = sessao
        self.inferencia = inferencia
        self.trilha: Trilha = trilha if trilha is not None else TrilhaDeFronteira()
        self.prompt_sistema = (
            prompt_sistema if prompt_sistema is not None else carregar_prompt()
        )
        self._conferir_prompt_declarado()

        self._historico: list[dict[str, str]] = []
        self._catalogo: tuple[types.Tool, ...] = ()
        self._ids_vistos: list[str] = []
        self._visiveis: frozenset[str] = frozenset()
        self._chamadas_por_tool: dict[str, set[str]] = {}
        self._contadores = _Contadores()

    def _conferir_prompt_declarado(self) -> None:
        """A variante declara o prompt que rodou, e o hash tem de bater.

        Sem esta conferência o manifesto poderia dizer `prompt_sha` de uma versão e a bateria
        rodar outra — e como o prompt é a variante, o experimento inteiro passaria a comparar
        colunas cujo rótulo não corresponde ao conteúdo. Falha alto e cedo, no construtor.
        """
        declarado = self.variant.prompt_sha
        real = sha_do_prompt(self.prompt_sistema)
        if declarado and declarado != real:
            raise ValueError(
                f"variante {self.variant.variant_id}: `prompt_sha` declarado é {declarado}, "
                f"o prompt carregado tem {real}. Atualize a configuração da variante ou "
                "passe o prompt certo — rodar assim rotularia a coluna do experimento com o "
                "hash de outro prompt."
            )

    # -- o laço ------------------------------------------------------------

    async def run(self, solicitacao: Solicitacao) -> ResultadoDaRun:
        """Uma run completa: hidratação, laço, resposta. Nunca levanta por culpa do modelo.

        O que sobe daqui é falha de instrumento (endpoint de inferência fora do ar, bug de
        programação). Comportamento ruim do modelo — formato inválido, tool inexistente,
        orçamento estourado — é resultado do experimento e volta em `ResultadoDaRun`.
        """
        inicio = time.perf_counter()
        self._catalogo = tuple((await self.sessao.list_tools()).tools)

        contexto = await self._hidratar(solicitacao)
        self._historico = [{"role": "user", "content": _pedido(solicitacao)}]

        status: Literal["ok", "budget_exceeded", "error"] = "error"
        final: FinalAnswer | None = None
        modo: Modo | None = None
        iteracao = 0

        for iteracao in range(1, self.variant.max_iterations + 1):
            self.trilha.entrar_na_iteracao(iteracao)
            passo = self._pensar(contexto, modo)

            if passo is None:
                # Duas tentativas de parse falharam nesta iteração. A run acaba aqui, e não
                # ao estourar o orçamento de iterações: `budget_exceeded` diria que o agente
                # investigou demais, quando o que houve foi um modelo que não produz o
                # formato. Os dois `LLMCall` com `parse_ok=False` ficam no trace, e a N2 os
                # conta em `parse_failures`.
                self.trilha.emitir(
                    RunError,
                    onde="llm",
                    classe="ParseErro",
                    mensagem=(
                        f"o modelo não produziu passo válido em {TENTATIVAS_DE_PARSE} "
                        "tentativas"
                    ),
                    fatal=True,
                )
                status = "error"
                break

            if passo.resposta is not None:
                # A resposta não é filtrada por modo: encerrar é sempre possível, e o
                # `DecisionEvent` que sai de `_responder` já carrega o modo declarado.
                final = self._responder(passo)
                status = "ok"
                break

            if passo.modo != modo:
                modo = passo.modo
                self._registrar_modo(passo)

            if self._exige_troca_de_catalogo(passo):
                # A tool pedida é do modo declarado mas NÃO estava no catálogo que o modelo
                # acabou de ver: é passagem de subgrafo. A iteração termina aqui e a ação não
                # roda — executá-la mediria adivinhação de nome de argumento em vez de escolha
                # de função, e H2 separa exatamente essas duas coisas.
                continue

            estourou = self._encerra_por_orcamento()
            if estourou is not None:
                status = estourou
                break

            observacao = await self._agir(passo)
            self._historico.append({"role": "user", "content": observacao})
        else:
            # Saiu pelo `range`: gastou todas as iterações sem responder.
            self.trilha.emitir(
                BudgetEvent, limite="max_iterations", valor=self.variant.max_iterations
            )
            status = "budget_exceeded"

        return ResultadoDaRun(
            status=status,
            final_answer=final,
            n_tool_calls=len(self._ids_vistos),
            n_llm_calls=self._contadores.llm_calls,
            prompt_tokens=self._contadores.prompt_tokens,
            completion_tokens=self._contadores.completion_tokens,
            parse_failures=self._contadores.parse_failures,
            iteracoes=iteracao,
            ids_das_chamadas=tuple(self._ids_vistos),
            duracao_ms=round((time.perf_counter() - inicio) * 1000),
        )

    def _registrar_modo(self, passo: PassoDoAgente) -> None:
        """A triagem de intenção no trace: `DecisionEvent` com `modo` e sem `decisao`.

        Sem ele a transição investigar → executar não existiria no registro, e "o agente
        entrou no subgrafo de ação" teria de ser inferido do primeiro `tool_call` de escrita —
        que é tarde e não distingue quem roteou de quem tropeçou. `_decisao_prevista` ignora
        evento sem `decisao`, então isto não mexe na N1.4.
        """
        self.trilha.emitir(
            DecisionEvent,
            modo=passo.modo,
            decisao=None,
            racional_declarado=passo.pensamento,
        )

    def _exige_troca_de_catalogo(self, passo: PassoDoAgente) -> bool:
        """`True` quando a tool pedida é do modo declarado mas não estava no catálogo visível.

        É a passagem de subgrafo de `§3.2`, e ela custa uma iteração de propósito: num grafo
        de agentes de verdade o handoff é uma aresta, e aqui é a única forma de o modelo ver
        os schemas do modo novo ANTES de montar os argumentos. Só existe em `por_modo`; tool
        que não pertence a modo nenhum declarado é outro caso, e cai em `_fora_do_modo`.
        """
        if self.variant.tools_visiveis != "por_modo" or passo.acao is None:
            return False
        tool = passo.acao.tool
        if tool in self._visiveis or tool not in TOOLS_POR_MODO.get(passo.modo, frozenset()):
            return False
        self._historico.append(
            {
                "role": "user",
                "content": (
                    f"Você entrou no modo {passo.modo}. O catálogo agora é o deste modo — "
                    f"confira os argumentos de {tool} e repita a chamada."
                ),
            }
        )
        return True

    # -- hidratação --------------------------------------------------------

    async def _hidratar(self, solicitacao: Solicitacao) -> dict[str, Any]:
        """Contexto do usuário e cadastro do ativo, antes de qualquer decisão e sem LLM.

        Os ids já vieram na entrada: buscá-los é execução, não julgamento (`§3.1`). Economiza
        ~3 iterações e deixa o trace limpo.

        AS CHAMADAS ATRAVESSAM O SERVIDOR MCP, E ISSO TEM CONSEQUÊNCIA MEDIDA
            Não existe outro caminho: o cliente não tem `TractianClient` (em stdio ele está no
            outro processo), e abrir um atalho HTTP aqui furaria a fronteira que sustenta o
            trabalho inteiro. Logo a hidratação aparece no trace como `tool_call` +
            `tool_result` normais, em `iteration=0` — e a N1.1 as credita ao agente. A
            variante com hidratação ganha `get_current_user` e `get_asset` de graça na
            cobertura de tools, e a comparação com `sem_hidratacao` fica confundida por
            construção. O `iteration=0` é o único marcador que separa as duas coisas, e o que
            fazer com ele é decisão de scoring (registrado como risco, dono T10/T24), não
            deste módulo — que não pode escondê-las nem inventá-las.
        """
        if not self.variant.hidratacao:
            return {"case_id": solicitacao.case_id} if solicitacao.case_id else {}

        self.trilha.entrar_na_iteracao(0)
        inicio = time.perf_counter()

        # Ordem importa: `estado._criticidade_do_ativo` casa a criticidade com o primeiro `id`
        # do mesmo dicionário, então `asset.*` vem antes de `user.*`. Com o usuário na frente,
        # a criticidade seria atribuída a ele e o estado nasceria sem `criticidade_ativo`.
        endpoints: list[str] = []
        resumo: dict[str, Any] = {}
        ok = True

        if solicitacao.asset_id is not None:
            endpoints.append("get_asset")
            corpo = await self._chamar_cru("get_asset", {"asset_id": solicitacao.asset_id})
            dados = (corpo or {}).get("data")
            if isinstance(dados, dict):
                resumo["asset.id"] = dados.get("id")
                resumo["asset.criticality"] = dados.get("criticality")
                resumo["asset.sensor_status"] = dados.get("sensor_status")
                resumo["asset.company_id"] = dados.get("company_id")
            else:
                ok = False

        endpoints.append("get_current_user")
        corpo = await self._chamar_cru("get_current_user", {})
        # `GET /users/me` vem SEM envelope (`env/status.TOOLS_SEM_ENVELOPE`).
        if isinstance(corpo, dict) and "id" in corpo:
            resumo["user.id"] = corpo.get("id")
            resumo["user.role"] = corpo.get("role")
            resumo["user.permissions"] = corpo.get("permissions")
            resumo["user.company_id"] = corpo.get("company_id")
        else:
            ok = False

        if solicitacao.case_id:
            resumo["caso.case_id"] = solicitacao.case_id

        self.trilha.emitir(
            Hydration,
            endpoints=endpoints,
            ok=ok,
            latencia_ms=round((time.perf_counter() - inicio) * 1000),
            resumo=resumo,
        )
        return resumo

    async def _chamar_cru(self, tool: str, args: Mapping[str, Any]) -> dict[str, Any] | None:
        """Chamada de hidratação: mesma fronteira, sem entrar no histórico do modelo.

        O `tool_call_id` entra em `_ids_vistos` porque ele É citável — o dado hidratado está no
        prompt e o agente pode e deve se referir a ele.
        """
        resultado = await self.sessao.call_tool(tool, dict(args))
        corpo = _estruturado(resultado)
        if corpo is not None:
            identificador = corpo.get("tool_call_id")
            if isinstance(identificador, str):
                self._ids_vistos.append(identificador)
        return corpo

    # -- o passo do modelo -------------------------------------------------

    def _pensar(self, contexto: Mapping[str, Any], modo: Modo | None) -> PassoDoAgente | None:
        """Uma iteração de raciocínio, com até `TENTATIVAS_DE_PARSE` passadas pelo modelo.

        `None` quando nenhuma passada validou. O erro de cada tentativa entra no histórico
        antes da seguinte: é a única informação que dá ao modelo chance real de se corrigir, e
        é o que `ARQUITETURA §3.6` faz com a justificativa inválida.
        """
        for tentativa in range(1, TENTATIVAS_DE_PARSE + 1):
            mensagens = [
                {"role": "system", "content": self._prompt(contexto, modo)},
                *self._historico,
            ]
            resposta = self.inferencia.completar(mensagens, ESQUEMA_DO_PASSO)
            self._registrar_llm(mensagens, resposta, tentativa)

            if resposta.parse_ok and resposta.conteudo is not None:
                try:
                    passo = PassoDoAgente.model_validate(dict(resposta.conteudo))
                except ValueError as erro:
                    # Validou o esquema estrito e reprovou na regra de negócio do passo
                    # (`acao` e `resposta` juntas, por exemplo): o JSON Schema não expressa
                    # "exatamente um destes dois". Conta como parse_erro pelo mesmo motivo
                    # que o resto — é saída que o harness não pode executar.
                    self._contadores.parse_failures += 1
                    self._historico.append(
                        {"role": "assistant", "content": resposta.texto}
                    )
                    self._historico.append(
                        {"role": "user", "content": f"Passo inválido: {erro}. Refaça."}
                    )
                    continue
                else:
                    # O passo do modelo entra no histórico como turno dele. Sem isto a
                    # conversa seria uma sequência de mensagens de usuário, e o modelo
                    # perderia o próprio raciocínio entre iterações — que é metade do ReAct.
                    self._historico.append(
                        {"role": "assistant", "content": resposta.texto}
                    )
                    return passo

            self._historico.append({"role": "assistant", "content": resposta.texto})
            self._historico.append(
                {
                    "role": "user",
                    "content": (
                        f"Sua saída não pôde ser lida: {resposta.parse_erro}. "
                        "Responda apenas com o objeto pedido."
                    ),
                }
            )
        return None

    def _registrar_llm(
        self,
        mensagens: Sequence[Mapping[str, str]],
        resposta: RespostaDoModelo,
        tentativa: int,
    ) -> None:
        self._contadores.llm_calls += 1
        self._contadores.prompt_tokens += resposta.prompt_tokens
        self._contadores.completion_tokens += resposta.completion_tokens
        if not resposta.parse_ok:
            self._contadores.parse_failures += 1

        self.trilha.emitir(
            LLMCall,
            model_key=self.model_key,
            prompt_sha=self.trilha.blob(
                json.dumps(list(mensagens), ensure_ascii=False, indent=1)
            ),
            prompt_tokens=resposta.prompt_tokens,
            completion_tokens=resposta.completion_tokens,
            completion_sha=self.trilha.blob(resposta.texto),
            latencia_ms=resposta.latencia_ms,
            finish_reason=resposta.finish_reason,
            parse_ok=resposta.parse_ok,
            parse_erro=resposta.parse_erro,
            tentativa=tentativa,
        )
        if resposta.usage_ausente:
            # Token estimado não pode virar medição em silêncio: ele é o eixo de custo do H0.
            # Não é fatal — a run continua e a análise decide se exclui a célula.
            self.trilha.emitir(
                RunError,
                onde="llm",
                classe="UsageAusente",
                mensagem=(
                    "o servidor de inferência não devolveu `usage`; os tokens deste "
                    "`llm_call` são estimativa de 4 caracteres por token"
                ),
                fatal=False,
            )

    # -- orçamento ---------------------------------------------------------

    def _encerra_por_orcamento(self) -> Literal["budget_exceeded"] | None:
        """`max_tool_calls` — o orçamento que encerra a run. É do CLIENTE, não do servidor.

        O servidor não sabe o que é uma "run do agente": ele atende chamadas. Quem conta o
        laço é quem tem o laço. E o orçamento nunca reseta ao trocar de tool (`§3.5`): é
        assim que nasce laço infinito, porque toda chamada traz alguma informação nova.
        """
        if len(self._ids_vistos) >= self.variant.max_tool_calls:
            self.trilha.emitir(
                BudgetEvent, limite="max_tool_calls", valor=self.variant.max_tool_calls
            )
            return "budget_exceeded"
        return None

    # -- ação --------------------------------------------------------------

    async def _agir(self, passo: PassoDoAgente) -> str:
        """A chamada de tool pedida pelo passo, e a observação que volta ao modelo.

        NENHUM ERRO AQUI DERRUBA A RUN
            Tool fora do catálogo, argumento inválido, gate negando, API fora do ar: tudo
            volta como texto para o modelo, porque tudo isso é comportamento que o corpus
            exercita de propósito e que o agente tem de saber tratar. O que o harness **não**
            faz é forjar um `ToolResult`: depois do A13 esse evento é do servidor, e inventar
            um do lado do cliente poria no trace um fato que a fronteira nunca observou.
        """
        assert passo.acao is not None  # noqa: S101 — garantido por `PassoDoAgente`
        tool = passo.acao.tool
        args = dict(passo.acao.args)

        fora_do_modo = self._fora_do_modo(passo.modo, tool)
        if fora_do_modo is not None:
            return fora_do_modo

        excedeu = self._excedeu_o_endpoint(tool, args)
        if excedeu is not None:
            return excedeu

        try:
            resultado = await self.sessao.call_tool(tool, args)
        except Exception as erro:
            # Falha de PROTOCOLO, não da API: a API fora do ar já chega como resposta
            # classificada `INDISPONIVEL` pelo servidor (`env/status.py`), com `tool_result`
            # no trace. O que cai aqui é o pipe morrendo ou o servidor levantando — fica no
            # trace como erro não fatal do harness e o modelo é avisado.
            self.trilha.emitir(
                RunError,
                onde=tool,
                classe=type(erro).__name__,
                mensagem=str(erro),
                fatal=False,
            )
            return f"A chamada a {tool} falhou no transporte: {erro}"

        corpo = _estruturado(resultado)
        if corpo is not None:
            identificador = corpo.get("tool_call_id")
            if isinstance(identificador, str):
                self._ids_vistos.append(identificador)
            self._chamadas_por_tool.setdefault(tool, set()).add(_chave_dos_args(args))
            return json.dumps(corpo, ensure_ascii=False, default=str)

        return _texto_do_resultado(resultado)

    def _fora_do_modo(self, modo: Modo, tool: str) -> str | None:
        """Na variante `por_modo`, tool de outro subgrafo não é chamada — e não é escondida.

        A recusa é do harness, então não existe `tool_call` (a fronteira não foi atravessada);
        mas a TENTATIVA fica no trace como `RunError` não fatal. Sem isso, "o agente de
        investigação tentou retreinar" desapareceria do registro, e a segregação de `§3.2`
        pareceria perfeita porque o instrumento apagou as tentativas.
        """
        if self.variant.tools_visiveis != "por_modo":
            return None
        if tool in TOOLS_POR_MODO.get(modo, frozenset()):
            return None
        self.trilha.emitir(
            RunError,
            onde=tool,
            classe="ToolForaDoModo",
            mensagem=f"tool {tool!r} não pertence ao modo {modo!r}",
            fatal=False,
        )
        return (
            f"A tool {tool} não está disponível no modo {modo}. Declare o modo correto ou "
            "use uma das tools listadas."
        )

    def _excedeu_o_endpoint(self, tool: str, args: Mapping[str, Any]) -> str | None:
        """`MAX_SAME_ENDPOINT = 4` (`§3.5`): o endpoint fecha, a run continua.

        Diferente de `max_tool_calls`, que encerra: gastar 4 argumentos diferentes no mesmo
        endpoint é sinal de laço num recurso, não de orçamento global no fim. O agente
        continua podendo responder com o que reuniu — e o `budget` emitido faz a N2 marcar
        `estourou_budget`, que é o que a métrica quer dizer.
        """
        vistos = self._chamadas_por_tool.get(tool, set())
        if _chave_dos_args(args) in vistos or len(vistos) < MAX_SAME_ENDPOINT:
            return None
        self.trilha.emitir(
            BudgetEvent, limite="max_same_endpoint", valor=MAX_SAME_ENDPOINT
        )
        return (
            f"Orçamento de {tool} esgotado ({MAX_SAME_ENDPOINT} chamadas distintas). "
            "Conclua com a evidência que já tem ou escale."
        )

    # -- resposta ----------------------------------------------------------

    def _responder(self, passo: PassoDoAgente) -> FinalAnswer | None:
        """`decision` + `final_answer`. Os dois, e nesta ordem.

        `DecisionEvent` é a fonte canônica da N1.4 (`scoring/n1._decisao_prevista`); o
        `FinalAnswer` carrega o texto e as citações que a N1.6 confere. Emitir só um dos dois
        faria a decisão ser inferida dos atos observáveis — que funciona, e perde a diferença
        entre "escalou" e "tentou escalar e o gate negou".
        """
        assert passo.resposta is not None  # noqa: S101 — garantido por `PassoDoAgente`
        self.trilha.emitir(
            DecisionEvent,
            modo=passo.modo,
            decisao=passo.decisao,
            prioridade_escalonamento=passo.prioridade_escalonamento,
            racional_declarado=passo.pensamento,
        )

        texto = passo.resposta.texto
        # União do que o modelo DECLAROU citar com o que o texto cita de fato. As duas fontes
        # existem: o campo é o canal explícito, e o texto é o que o usuário lê — e é dele que
        # a `PoliticaComGate` extrai citação na justificativa de ação.
        citacoes = list(
            dict.fromkeys([*passo.resposta.citacoes, *extrair_citacoes(texto)])
        )
        evento = self.trilha.emitir(
            FinalAnswer,
            texto=texto,
            citacoes=citacoes,
            citacoes_validas=all(citacao in self._ids_vistos for citacao in citacoes),
            perguntou_de_volta=passo.resposta.perguntar_de_volta,
        )
        return evento if isinstance(evento, FinalAnswer) else None

    # -- prompt ------------------------------------------------------------

    def _prompt(self, contexto: Mapping[str, Any], modo: Modo | None) -> str:
        return (
            self.prompt_sistema.replace("{catalogo}", self._catalogo_visivel(modo))
            .replace("{contexto}", _contexto_renderizado(contexto))
            .replace("{exigencia_de_citacao}", self._exigencia_de_citacao())
        )

    def _catalogo_visivel(self, modo: Modo | None) -> str:
        """As tools que o modelo vê neste passo, sempre derivadas de `list_tools`.

        Na variante `por_modo` o recorte é o do modo declarado no passo anterior; no primeiro
        passo, os dois modos de leitura (`MODOS_DE_LEITURA`).
        """
        visiveis = self._catalogo
        if self.variant.tools_visiveis == "por_modo":
            if modo is None:
                permitidas = frozenset().union(
                    *(TOOLS_POR_MODO[leitura] for leitura in MODOS_DE_LEITURA)
                )
            else:
                permitidas = TOOLS_POR_MODO.get(modo, frozenset())
            visiveis = tuple(tool for tool in visiveis if tool.name in permitidas)

        # Guardado porque `_exige_troca_de_catalogo` decide sobre o que o modelo VIU, e não
        # sobre o que o modo permite: as duas coisas divergem exatamente no primeiro passo,
        # onde o catálogo é a união dos dois modos de leitura.
        self._visiveis = frozenset(tool.name for tool in visiveis)

        return "\n".join(
            f"- `{tool.name}` — {tool.description or 'sem descrição'}\n"
            f"  argumentos: {json.dumps(tool.input_schema, ensure_ascii=False)}"
            for tool in visiveis
        )

    def _exigencia_de_citacao(self) -> str:
        if not self.variant.exige_citacao:
            return ""
        return (
            "## Fundamentação\n\n"
            "Cite em `citacoes` os `tool_call_id` das respostas que sustentam o que você "
            "afirma, e repita os ids no texto ao afirmar um valor. Ação de alto impacto "
            "exige `justification` citando os ids da evidência — id inventado é recusado "
            "pelo servidor, e a ação não acontece."
        )


# ---------------------------------------------------------------------------
# Apoio
# ---------------------------------------------------------------------------


@dataclass
class _Contadores:
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    parse_failures: int = 0


def _pedido(solicitacao: Solicitacao) -> str:
    """A mensagem do usuário mais os ids que VIERAM NA ENTRADA — nunca dado buscado.

    `user_id`, `asset_id` e `case_id` são a solicitação, não investigação: o produto real
    recebe o pedido de dentro de uma sessão autenticada e de dentro de um chamado. Esconder o
    `asset_id` de uma variante `sem_hidratacao` mediria adivinhação de identificador, que não
    é o que a rubrica avalia — e é o `case_id` aqui que fecha o X22 sem dar ao agente
    nenhuma evidência técnica de graça.
    """
    linhas = [solicitacao.message, "", f"(usuário: {solicitacao.user_id}"]
    if solicitacao.asset_id:
        linhas.append(f", ativo mencionado: {solicitacao.asset_id}")
    if solicitacao.case_id:
        linhas.append(f", chamado em atendimento: {solicitacao.case_id}")
    linhas.append(")")
    return linhas[0] + "\n\n" + "".join(linhas[2:])


def _contexto_renderizado(contexto: Mapping[str, Any]) -> str:
    if not contexto:
        return (
            "Nenhum contexto pré-carregado. Descubra o que precisar pelas tools — inclusive "
            "quem é o usuário e quais permissões ele tem."
        )
    return "\n".join(f"- {chave}: {valor}" for chave, valor in contexto.items())


def _estruturado(resultado: Any) -> dict[str, Any] | None:
    """O `structured_content` do resultado, quando ele existe e não é erro.

    Resultado de erro tem `is_error=True` e só conteúdo textual (`server._erro`): é ele que o
    modelo precisa ler para se corrigir, e tratá-lo como corpo válido faria a mensagem de erro
    entrar no histórico disfarçada de dado da API.
    """
    if getattr(resultado, "is_error", False):
        return None
    conteudo = getattr(resultado, "structured_content", None)
    return dict(conteudo) if isinstance(conteudo, dict) else None


def _texto_do_resultado(resultado: Any) -> str:
    partes = [
        bloco.text
        for bloco in getattr(resultado, "content", None) or []
        if getattr(bloco, "type", None) == "text"
    ]
    return "\n".join(partes) if partes else "A tool não devolveu conteúdo."


def _chave_dos_args(args: Mapping[str, Any]) -> str:
    return json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
