"""T18 — a execução da matriz, célula a célula, com retomada.

UMA RUN, UM SERVIDOR, UM MUNDO
    Cada célula ganha `RunContext` novo — e com ele cache novo, contador de `seq` novo,
    contador de `tool_call_id` novo —, `TractianClient` novo e gate novo (logo, chaves de
    idempotência novas). O servidor MCP em memória nasce dentro de `abrir_sessao` e morre com
    ela (`ARQUITETURA §4.4`).

    É o vazamento mais silencioso do plano. Cache compartilhado entre células faria a segunda
    parecer mais eficiente que a primeira — mesmo cenário, metade das chamadas HTTP — e o
    artefato do instrumento viraria resultado sobre o agente. Chave de idempotência
    compartilhada seria pior: a segunda run não conseguiria executar a ação irreversível que
    a primeira executou, e a bateria mediria "o agente não agiu".

DIVERGÊNCIA DO ENUNCIADO: THREADS, NÃO `asyncio.Semaphore(2)`
    O plano manda `asyncio.Semaphore(2)`. Um semáforo de asyncio limitaria duas *tasks* no
    mesmo laço de eventos — e `Inferencia.completar` é **síncrona** (`sut/llm.py`, e é
    síncrona de propósito: uma run é sequencial e a GPU é única). Chamada de dentro de uma
    task, ela bloqueia o laço inteiro, as duas runs se serializam e o `2` vira decoração:
    o manifesto declararia um paralelismo que não existe.

    Então o limite é um pool de threads de tamanho `paralelismo`, uma run por thread, cada
    uma com o seu próprio laço `anyio.run`. O contrato é o mesmo — no máximo N runs em voo —
    e ele é verdadeiro.

    Vale registrar o que a T0b mediu depois de o plano ser escrito: **o paralelismo não é o
    que resolve o cronograma**. 27,5 s/passo no 14B contra ~16 h de "duas madrugadas"; o
    ganho previsto vinha do prefix cache do servidor de inferência, e a GPU continua sendo
    uma. `paralelismo` fica configurável, com `1` disponível para diagnóstico — nesse caso a
    run acontece na própria thread do chamador, para que o traceback de um erro não passe
    por um `Future`.

A7 · A VALIDAÇÃO ACONTECE SOBRE O DISCO, NÃO SOBRE A MEMÓRIA
    O `ObservadorDeTrace` guarda os eventos em memória enquanto os escreve, e seria mais
    barato validar essa lista. Seria também a validação errada: o que os scorers leem é o
    arquivo, e o defeito que interessa é exatamente o que separa os dois — evento que se
    perdeu entre a emissão e o disco. Validar a memória atestaria a integridade de uma coisa
    que ninguém pontua.

RE-EXECUÇÃO APAGA O TRACE ANTIGO ANTES DE COMEÇAR
    `TraceWriter` abre em modo append (e tem de abrir: uma run que morre no meio deixa trace
    completo até o último evento). Reaproveitar o arquivo de uma tentativa anterior somaria
    duas runs no mesmo `.jsonl`, com dois `seq=1` — e o `validar_trace` acusaria
    `seq_duplicado` numa run que rodou bem, culpando o transporte por um erro do runner.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import anyio
import httpx

from tapieval.env.client import TractianClient
from tapieval.mcp.gate import Approver, AutoApprove, AutoDeny, PolicyApprover
from tapieval.mcp.instrumentacao import ObservadorDeTrace, ligar_gate
from tapieval.mcp.server import RunContext
from tapieval.runner.manifesto import (
    CenarioExcluido,
    CoordenadaDaCelula,
    JudgeDoManifesto,
    Manifesto,
    RegistroDeRun,
    StatusDaRun,
    escrever_manifesto,
    judge_do_manifesto,
    ler_manifesto,
    motivo_nao_pontuavel_de,
)
from tapieval.runner.matriz import RAIZ_DO_REPO, Bateria, Celula
from tapieval.schema.reader import read_trace, validar_trace
from tapieval.schema.trace import (
    LLMCall,
    RunEnd,
    RunError,
    RunStart,
    ToolCall,
    TraceEvent,
    VariantConfig,
)
from tapieval.schema.writer import TraceWriter
from tapieval.scoring.judge_llm import SERVIDO_POR_POR_PROVEDOR
from tapieval.sut.agent import (
    Agent,
    ResultadoDaRun,
    Solicitacao,
    TrilhaDoHarness,
    carregar_solicitacao,
    sha_do_prompt,
)
from tapieval.sut.llm import ClienteDeInferencia, Inferencia
from tapieval.sut.referencia import ClienteDeReferencia
from tapieval.sut.sessao import abrir_sessao

DIRETORIO_DE_PROMPTS = RAIZ_DO_REPO / "prompts"

SERVIDO_POR_NA_NUVEM: frozenset[str] = frozenset(SERVIDO_POR_POR_PROVEDOR.values())
"""Os `ModelConfig.served_by` que exigem o cliente de referência em vez do local (R5).

Derivado de `SERVIDO_POR_POR_PROVEDOR` e não redigitado: acrescentar um provedor lá tem de
bastar. Uma lista literal aqui deixaria o provedor novo cair no cliente local, que tentaria
falar `http://127.0.0.1:1234/v1` com um modelo de fronteira e falharia longe da causa."""

FabricaDeInferencia = Callable[[Celula], Inferencia]
"""Como a célula vira um cliente de modelo. Injetável: é a única costura que o teste da
bateria precisa para rodar sem GPU, e é a mesma costura que a T26c usa para apontar uma
célula ao SUT de referência sem tocar no runner."""

AoConcluir = Callable[[CoordenadaDaCelula, RegistroDeRun], None]
"""Chamado na thread do chamador a cada run fechada. É o que a CLI usa para imprimir
progresso — o runner não imprime nada."""


class ErroDeExecucao(RuntimeError):
    """Falha que impede a bateria de começar. Falha de uma run individual não é isto."""


# ---------------------------------------------------------------------------
# O prompt da variante, encontrado pelo hash que ela declara
# ---------------------------------------------------------------------------


def indexar_prompts(diretorio: Path = DIRETORIO_DE_PROMPTS) -> dict[str, Path]:
    """`sha256(conteúdo) -> arquivo`, para todo `prompts/*.md`."""
    return {
        sha_do_prompt(caminho.read_text(encoding="utf-8")): caminho
        for caminho in sorted(diretorio.glob("*.md"))
    }


def resolver_prompt(
    variante: VariantConfig, indice: dict[str, Path] | None = None
) -> str:
    """O texto do prompt desta variante, achado **pelo hash** que ela declara.

    A `VariantConfig` guarda `prompt_sha` e não o caminho, de propósito (T17: o id vem da
    chave, o hash vem do arquivo, e nada disso se escreve duas vezes). Procurar pelo hash é a
    conferência de `Agent._conferir_prompt_declarado` feita **antes** de a run começar, e não
    exige devolver à variante um campo que ela decidiu não ter.

    Falhar aqui é o certo: um prompt editado depois de a variante ser carregada rotularia a
    coluna do experimento com o hash de um prompt que não rodou.
    """
    indice = indice if indice is not None else indexar_prompts()
    caminho = indice.get(variante.prompt_sha)
    if caminho is None:
        raise ErroDeExecucao(
            f"variante {variante.variant_id}: nenhum arquivo em {DIRETORIO_DE_PROMPTS.name}/ "
            f"tem o `prompt_sha` declarado ({variante.prompt_sha[:12]}…). O prompt foi "
            "editado depois de a variante ser carregada, e rodar assim rotularia a coluna do "
            "experimento com o hash de um prompt que não rodou"
        )
    return caminho.read_text(encoding="utf-8")


def construir_approver(nome: str, variante: VariantConfig) -> Approver:
    """O aprovador da run. `exige_citacao` vem da VARIANTE, não da bateria.

    `PolicyApprover.exige_citacao` espelha `VariantConfig.exige_citacao` (está escrito lá):
    o MUT2 degrada exatamente isso, e um gate que continuasse exigindo fundamentação
    anularia o mutante — a degradação de conteúdo não chegaria à fronteira e a INS.9 mediria
    0% de detecção por defeito do runner.
    """
    if nome == "auto_approve":
        return AutoApprove()
    if nome == "auto_deny":
        return AutoDeny()
    if nome == "policy":
        return PolicyApprover(exige_citacao=variante.exige_citacao)
    raise ErroDeExecucao(f"approver desconhecido: {nome!r}")


# ---------------------------------------------------------------------------
# A bateria
# ---------------------------------------------------------------------------


def rodar_bateria(
    bateria: Bateria,
    *,
    fabrica_de_inferencia: FabricaDeInferencia | None = None,
    transporte_http: httpx.BaseTransport | None = None,
    retomar: bool = True,
    ao_concluir: AoConcluir | None = None,
) -> Manifesto:
    """Executa a matriz e devolve o manifesto. O manifesto é gravado a cada run fechada.

    Gravar a cada run e não no fim é o que torna a retomada real: uma bateria morta às 4h da
    manhã perde a run em voo, não a noite inteira.
    """
    diretorio = bateria.diretorio
    diretorio.mkdir(parents=True, exist_ok=True)

    manifesto = _manifesto_da_bateria(bateria, diretorio)
    escrever_manifesto(manifesto, diretorio)

    pendentes = manifesto.pendentes(retomar=retomar)
    if not pendentes:
        return manifesto

    fabrica = fabrica_de_inferencia or _fabrica_padrao(bateria)
    indice_de_prompts = indexar_prompts()
    por_id = {celula.run_id: celula for celula in bateria.expandir()}

    def executar(coordenada: CoordenadaDaCelula) -> RegistroDeRun:
        return executar_celula(
            bateria,
            por_id[coordenada.run_id],
            fabrica=fabrica,
            transporte_http=transporte_http,
            prompt=resolver_prompt(
                por_id[coordenada.run_id].variante, indice_de_prompts
            ),
        )

    def fechar(coordenada: CoordenadaDaCelula, registro: RegistroDeRun) -> None:
        manifesto.registrar(registro)
        escrever_manifesto(manifesto, diretorio)
        if ao_concluir is not None:
            ao_concluir(coordenada, registro)

    if bateria.paralelismo == 1:
        for coordenada in pendentes:
            fechar(coordenada, executar(coordenada))
        return manifesto

    with ThreadPoolExecutor(max_workers=bateria.paralelismo) as pool:
        # `map` e não `submit` + `as_completed`: o resultado chega na ordem das células, o
        # manifesto é escrito só nesta thread (sem trava) e o pool nunca fica com mais de
        # `paralelismo` runs em voo, que é o contrato que substitui o `Semaphore(2)`.
        for coordenada, registro in zip(pendentes, pool.map(executar, pendentes), strict=True):
            fechar(coordenada, registro)
    return manifesto


def _manifesto_da_bateria(bateria: Bateria, diretorio: Path) -> Manifesto:
    """O manifesto existente, conferido contra a configuração, ou um novo."""
    celulas = tuple(
        CoordenadaDaCelula(
            run_id=celula.run_id,
            scenario_id=celula.cenario.id,
            split=celula.cenario.split,
            model_key=celula.modelo.model_key,
            variant_id=celula.variante.variant_id,
            sample_seed=celula.sample_seed,
            env_seed=celula.cenario.env_seed,
        )
        for celula in bateria.expandir()
    )
    agora = datetime.now(UTC)
    judge = judge_do_manifesto(bateria.judge)
    existente = ler_manifesto(diretorio)

    if existente is not None:
        if existente.experiment_id != bateria.experiment_id:
            raise ErroDeExecucao(
                f"{diretorio} tem manifesto de {existente.experiment_id!r} e a bateria diz "
                f"{bateria.experiment_id!r}. Dois experimentos no mesmo diretório misturariam "
                "traces de matrizes diferentes na mesma tabela"
            )
        _conferir_judge_da_retomada(existente, judge, diretorio)
        existente.judge = judge
        existente.celulas = celulas
        existente.cenarios_excluidos = tuple(
            CenarioExcluido(cenario_id=e.cenario_id, motivo=e.motivo)
            for e in bateria.excluidos
        )
        existente.atualizado_em = agora
        return existente

    return Manifesto(
        experiment_id=bateria.experiment_id,
        criado_em=agora,
        atualizado_em=agora,
        api_base_url=bateria.api_base_url,
        inferencia_base_url=bateria.inferencia_base_url,
        approver=bateria.approver,
        paralelismo=bateria.paralelismo,
        timeout_s=bateria.timeout_s,
        judge=judge,
        modelos={m.model_key: m.config for m in bateria.modelos},
        variantes={v.variant_id: v for v in bateria.variantes},
        celulas=celulas,
        cenarios_excluidos=tuple(
            CenarioExcluido(cenario_id=e.cenario_id, motivo=e.motivo)
            for e in bateria.excluidos
        ),
    )


def _conferir_judge_da_retomada(
    existente: Manifesto, judge: JudgeDoManifesto, diretorio: Path
) -> None:
    """Retomar sob outro judge congelado é erro, pelo mesmo motivo do `experiment_id`.

    Metade das células declarando um sha e metade declarando outro deixa o manifesto sem
    resposta para "contra qual judge esta bateria foi pontuada" — que é a única pergunta que o
    campo existe para responder. Manifesto anterior ao campo (`judge is None`) é preenchido sem
    reclamar: ali não há divergência, há um campo que ainda não existia.
    """
    anterior = existente.judge
    if anterior is None or anterior == judge:
        return
    raise ErroDeExecucao(
        f"{diretorio} tem manifesto declarando judge {_descricao_do_judge(anterior)} e a "
        f"bateria diz {_descricao_do_judge(judge)}. Retomar trocaria o instrumento no meio da "
        "matriz, e o resultado não seria comparável com ele mesmo. Rode `--do-zero` noutro "
        "diretório, ou volte o judge declarado"
    )


def _descricao_do_judge(judge: JudgeDoManifesto) -> str:
    if judge.congelado:
        return f"congelado {judge.scorer_version} ({judge.sha256})"
    return f"sem congelamento ({judge.motivo_da_dispensa})"


def _fabrica_padrao(bateria: Bateria) -> FabricaDeInferencia:
    """Um cliente por célula, com a `ModelConfig` da célula, escolhido pelo `served_by`.

    Por célula e não por bateria: a `sample_seed` mora na `ModelConfig` (`seed`), então um
    cliente compartilhado mandaria a seed da primeira célula em todas — e o eixo de
    repetição do `pass^k` viraria uma coluna constante.

    R5 · POR QUE O DESPACHO MORA AQUI, E NÃO NUMA FLAG DA CLI
        A bateria de referência (T26c, `ARQUITETURA §13`) roda contra um modelo de fronteira na
        nuvem, e `sut/llm.py` promete não sair para a rede. `sut/referencia.py` é a porta que falta,
        e satisfaz o mesmo Protocol — mas até aqui **nenhum caminho de linha de comando a
        alcançava**: esta função montava sempre um `ClienteDeInferencia` apontado para
        `inferencia_base_url`, e a costura injetável (`fabrica_de_inferencia`) só existe para quem
        chama `executar_bateria` em Python. `docs/anexos/apuracao/dimensionamento.md §7` manda rodar
        esta bateria pela CLI.

        Uma flag `--referencia` resolveria por fora e estaria errada: o que decide o cliente
        é o **modelo da célula**, não a invocação. Uma bateria com um modelo local e um de
        fronteira na mesma matriz é legal (não é a 26c, mas o carregador aceita), e uma flag
        de bateria não teria como dizer "este sim, aquele não". `served_by` já viaja na
        `ModelConfig`, já vai para o manifesto e já é o campo que o TAPI §9 exige para
        declarar que o modelo roda em serviço externo — despachar por ele faz o manifesto e
        o cliente concordarem por construção, em vez de por disciplina de quem digita.
    """

    def fabricar(celula: Celula) -> Inferencia:
        config = celula.modelo.para(celula.sample_seed)
        if config.served_by in SERVIDO_POR_NA_NUVEM:
            # O `run_id` é obrigatório no cliente de referência: custo sem execução a que
            # pertencer não é custo. Aqui ele existe, e é o mesmo do trace.
            return ClienteDeReferencia(celula.run_id, config)
        return ClienteDeInferencia(bateria.inferencia_base_url, config)

    return fabricar


# ---------------------------------------------------------------------------
# Uma célula
# ---------------------------------------------------------------------------


def executar_celula(
    bateria: Bateria,
    celula: Celula,
    *,
    fabrica: FabricaDeInferencia,
    transporte_http: httpx.BaseTransport | None = None,
    prompt: str | None = None,
) -> RegistroDeRun:
    """Uma run inteira, do `RunStart` ao `validar_trace`. **Nunca levanta.**

    O que sobe de dentro — endpoint fora do ar, bug nosso — vira `falha_do_instrumento` no
    manifesto e `RunError(onde="harness")` no trace. Uma run que explode não pode derrubar a
    bateria: 287 células boas perdidas por uma péssima é o custo errado, e a célula ruim
    continua visível como célula ruim.
    """
    diretorio = bateria.diretorio
    run_id = celula.run_id
    caminho_do_trace = diretorio / "traces" / f"{run_id}.jsonl"
    caminho_do_trace.unlink(missing_ok=True)

    solicitacao = carregar_solicitacao(celula.cenario.caminho)

    observador = ObservadorDeTrace(TraceWriter(diretorio, run_id))
    ctx = RunContext(
        run_id=run_id,
        cliente=TractianClient(
            bateria.api_base_url,
            user_id=solicitacao.user_id,
            seed=celula.cenario.env_seed,
            transport=transporte_http,
        ),
        observador=observador,
        tools_ocultas=celula.variante.tools_ocultas,
    )
    ligar_gate(ctx, construir_approver(bateria.approver, celula.variante))
    trilha = TrilhaDoHarness(ctx)

    inicio = time.perf_counter()
    erro: str | None = None
    resultado: ResultadoDaRun | None = None
    status: StatusDaRun = "falha_do_instrumento"

    try:
        _emitir_run_start(trilha, bateria, celula, solicitacao)
        resultado, expirou = anyio.run(
            _rodar_agente, ctx, celula, solicitacao, fabrica, prompt, bateria.timeout_s
        )
        status = "timeout" if expirou else (resultado.status if resultado else "error")
    except Exception as excecao:  # noqa: BLE001 — é o ponto de contenção da bateria
        classe, erro = _descrever(excecao)
        _tentar_emitir(
            trilha,
            RunError,
            onde="harness",
            classe=classe,
            mensagem=erro[:2000],
            fatal=True,
        )
        status = "falha_do_instrumento"

    duracao_ms = round((time.perf_counter() - inicio) * 1000)
    _tentar_emitir(
        trilha,
        RunEnd,
        status="error" if status == "falha_do_instrumento" else status,
        duracao_ms=duracao_ms,
        **_totais(resultado, observador.eventos),
    )

    return _fechar_run(
        run_id=run_id,
        status=status,
        duracao_ms=duracao_ms,
        resultado=resultado,
        eventos=observador.eventos,
        erro=erro,
        caminho_do_trace=caminho_do_trace,
        diretorio=diretorio,
    )


async def _rodar_agente(
    ctx: RunContext,
    celula: Celula,
    solicitacao: Solicitacao,
    fabrica: FabricaDeInferencia,
    prompt: str | None,
    timeout_s: float | None,
) -> tuple[ResultadoDaRun | None, bool]:
    """O laço do agente contra o servidor desta run. Devolve `(resultado, expirou)`.

    O cancelamento por tempo chega no próximo ponto de espera, e `Inferencia.completar` é
    síncrona: uma chamada de inferência em curso **não** é interrompida, então o teto real é
    `timeout_s` mais uma chamada ao modelo. Isso é aceitável e é o motivo de o timeout existir
    — proteger a bateria de um servidor pendurado, não cronometrar a run com precisão.
    """
    inferencia = fabrica(celula)
    limite = timeout_s if timeout_s is not None else math.inf
    try:
        async with abrir_sessao(ctx) as sessao:
            agente = Agent(
                celula.variante,
                celula.modelo.model_key,
                sessao,
                inferencia,
                trilha=TrilhaDoHarness(ctx),
                prompt_sistema=prompt,
            )
            with anyio.move_on_after(limite) as escopo:
                resultado = await agente.run(solicitacao)
                return resultado, False
        return None, escopo.cancelled_caught
    finally:
        fechar = getattr(inferencia, "close", None)
        if callable(fechar):
            fechar()


def _emitir_run_start(
    trilha: TrilhaDoHarness,
    bateria: Bateria,
    celula: Celula,
    solicitacao: Solicitacao,
) -> None:
    """O `RunStart` sai ANTES de `Agent.run`, e a ordem não é detalhe.

    `derivar_estado` tira o `asset_id` da run dele — é como o estado sabe de qual ativo é a
    criticidade, com `list_assets_by_company` devolvendo dezenas — e
    `montar_contexto_da_decisao` também, para o D5 do gate. Emitido depois, o `seq=1` do
    trace não seria o começo da run.

    `seed` é a `sample_seed` da célula mesmo quando o modelo não a honra (`honra_seed=False`,
    achado da T0b): a repetição aconteceu, e é ela que o `pass^k` conta. Quem declara que o
    determinismo não existe é `ModelConfig.seed=None`, no manifesto — o `RunStart` não carrega
    `ModelConfig` nenhuma (`schema/trace.py`), e é por isso que a conferência do modelo se faz
    contra o manifesto e não contra o trace.
    """
    trilha.emitir(
        RunStart,
        experiment_id=bateria.experiment_id,
        scenario_id=celula.cenario.id,
        split=celula.cenario.split,
        variant_id=celula.variante.variant_id,
        model_key=celula.modelo.model_key,
        seed=celula.sample_seed,
        env_mode="live",
        cassette_id=None,
        solicitacao=solicitacao.message,
        user_id=solicitacao.user_id,
        asset_id=solicitacao.asset_id,
    )


def _tentar_emitir(trilha: TrilhaDoHarness, classe: type, /, **campos: object) -> None:
    """Emite e engole a falha de escrita — **só aqui**, e só no fechamento da run.

    `ObservadorDeTrace` deixa a exceção subir de propósito: trace incompleto tem de derrubar
    a run em vez de ser pontuado pela metade. Mas este é o caminho em que a run já está
    caindo, e uma segunda exceção aqui só trocaria o diagnóstico verdadeiro (`erro`, no
    registro) por "não consegui escrever o RunEnd". O defeito não se perde: sem `RunEnd` o
    trace continua sendo lido, e a run é julgada pelo `validar_trace` como qualquer outra.
    """
    try:
        trilha.emitir(classe, **campos)
    except Exception:  # noqa: BLE001 — ver docstring
        return


def _descrever(excecao: BaseException) -> tuple[str, str]:
    """`(classe, mensagem)` da falha — atravessando o `ExceptionGroup` do anyio.

    A sessão em memória roda o servidor num task group, então qualquer exceção do agente
    chega aqui embrulhada num `ExceptionGroup` cujo `str` é *"unhandled errors in a TaskGroup
    (1 sub-exception)"*. Guardar isso no manifesto seria guardar nada: `falha_do_instrumento`
    existe para que a run possa ser reproduzida e consertada, e o wrapper apaga justamente o
    que aponta o defeito. Todas as folhas são nomeadas, não a primeira — um grupo com duas
    causas tem duas coisas a consertar.
    """
    folhas = _folhas(excecao)
    classe = "+".join(dict.fromkeys(type(folha).__name__ for folha in folhas))
    mensagem = " | ".join(f"{type(f).__name__}: {f}" for f in folhas)
    return classe or type(excecao).__name__, mensagem or str(excecao)


def _folhas(excecao: BaseException) -> list[BaseException]:
    if isinstance(excecao, BaseExceptionGroup):
        return [folha for sub in excecao.exceptions for folha in _folhas(sub)]
    return [excecao]


def _totais(
    resultado: ResultadoDaRun | None, eventos: Sequence[TraceEvent]
) -> dict[str, int]:
    """Os totais do `RunEnd`. Do `ResultadoDaRun` quando ele existe, do trace quando não.

    O agente é a fonte designada (`ResultadoDaRun` existe para o runner fechar o `RunEnd`),
    mas em timeout e em falha do instrumento não há resultado — e aí o trace é o que sobrou,
    que é o registro dos fatos de qualquer forma.
    """
    if resultado is not None:
        return {
            "total_tool_calls": resultado.n_tool_calls,
            "total_llm_calls": resultado.n_llm_calls,
            "total_prompt_tokens": resultado.prompt_tokens,
            "total_completion_tokens": resultado.completion_tokens,
        }
    chamadas = [e for e in eventos if isinstance(e, ToolCall)]
    llm = [e for e in eventos if isinstance(e, LLMCall)]
    return {
        "total_tool_calls": len(chamadas),
        "total_llm_calls": len(llm),
        "total_prompt_tokens": sum(e.prompt_tokens for e in llm),
        "total_completion_tokens": sum(e.completion_tokens for e in llm),
    }


def _fechar_run(
    *,
    run_id: str,
    status: StatusDaRun,
    duracao_ms: int,
    resultado: ResultadoDaRun | None,
    eventos: Sequence[TraceEvent],
    erro: str | None,
    caminho_do_trace: Path,
    diretorio: Path,
) -> RegistroDeRun:
    """O registro da run, com o veredito do A7 sobre o trace **em disco**."""
    valida, defeitos, motivo = _validar_o_que_ficou_no_disco(caminho_do_trace)
    totais = _totais(resultado, eventos)
    erro = erro or _erro_fatal_do_agente(eventos)

    return RegistroDeRun(
        run_id=run_id,
        status=status,
        duracao_ms=duracao_ms,
        concluida_em=datetime.now(UTC),
        n_tool_calls=totais["total_tool_calls"],
        n_llm_calls=totais["total_llm_calls"],
        prompt_tokens=totais["total_prompt_tokens"],
        completion_tokens=totais["total_completion_tokens"],
        parse_failures=resultado.parse_failures if resultado else 0,
        iteracoes=resultado.iteracoes if resultado else 0,
        valida=valida,
        defeitos=defeitos,
        motivo_nao_pontuavel=motivo,
        erro=erro,
        trace=str(caminho_do_trace.relative_to(diretorio)),
    )


def _validar_o_que_ficou_no_disco(
    caminho: Path,
) -> tuple[bool, tuple[str, ...], str | None]:
    """`validar_trace` sobre o arquivo — o A7 finalmente chamado por alguém.

    Trace ausente cai em `sem_run_start` por `validar_trace([])`, e não numa quarta
    categoria: o efeito é o mesmo, a run não tem contexto. Linha ilegível é caso à parte
    porque nem chega ao validador — o reader recusa antes, e recusar é o certo (um trace
    parcialmente ilegível pontuado em silêncio é pior que uma run que falha).
    """
    try:
        eventos = read_trace(caminho) if caminho.exists() else []
    except ValueError as erro:
        defeito = f"trace_ilegivel: {erro}"
        return False, (defeito,), f"trace inválido (A7): {defeito}"

    defeitos = validar_trace(eventos)
    if not defeitos:
        return True, (), None
    return (
        False,
        tuple(str(defeito) for defeito in defeitos),
        motivo_nao_pontuavel_de(defeitos),
    )


def _erro_fatal_do_agente(eventos: Sequence[TraceEvent]) -> str | None:
    """O motivo de um `status="error"` que veio do agente, e não de exceção no harness.

    `erro` só era preenchido no `except` desta função — o caminho do
    `falha_do_instrumento`. Quando quem falha é o **agente** (um `ParseErro` que esgota as
    tentativas, por exemplo), a run volta pelo caminho normal com `resultado.status ==
    "error"`, e o manifesto ficava com a célula marcada `error` e `erro: null`: a linha
    dizia que houve falha e não dizia qual, com o motivo legível só abrindo o trace.

    Isso importa porque `error` é **resultado do experimento**, não defeito nosso — ele entra
    na taxonomia como medida, e uma medida sem causa não se agrega. Duas células assim
    saíram da bateria de calibração; nas três baterias seriam linhas opacas em escala.

    Só o último fatal, e só quando `erro` ainda está vazio: a exceção do harness é mais
    específica que qualquer evento do trace, e evento não-fatal não é a causa da run ter
    terminado.
    """
    fatais = [
        evento
        for evento in eventos
        if isinstance(evento, RunError) and evento.fatal
    ]
    if not fatais:
        return None
    ultimo = fatais[-1]
    return f"{ultimo.classe}: {ultimo.mensagem}"


__all__ = [
    "AoConcluir",
    "ErroDeExecucao",
    "FabricaDeInferencia",
    "construir_approver",
    "executar_celula",
    "indexar_prompts",
    "resolver_prompt",
    "rodar_bateria",
]
