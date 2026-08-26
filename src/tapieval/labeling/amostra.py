"""As duas amostras de `METRICAS §5`, e o que separa uma da outra. Tudo puro.

DUAS AMOSTRAS COM PROPÓSITOS OPOSTOS, E POR ISSO DISJUNTAS
    A de **estimativa** (n=20) é aleatória estratificada: ela é a única que pode entrar no κ
    da INS.6, porque κ estima concordância na POPULAÇÃO e uma amostra escolhida por
    dificuldade não estima nada disso. A de **melhoria** (n=15) é o oposto de propósito —
    vai onde a rubrica está ambígua, porque uma rotulagem ali ensina mais sobre o que
    reescrever (`METRICAS §5`, N4.2). Misturar as duas destrói a de estimativa e não melhora
    a de melhoria.

    A disjunção não é higiene: é a condição de validade do único número que o trabalho tem
    para dizer que N3 mede o que supõe medir. `amostrar` sorteia a estimativa PRIMEIRO,
    sobre o corpus inteiro, e a melhoria sai do complemento — a ordem inversa enviesaria a
    estimativa por remoção justamente dos casos difíceis.

POR QUE A PRIORIDADE AQUI NÃO É A DE `METRICAS §5`, E O QUE ELA É
    O pseudocódigo de N4.2 pontua desacordo entre camadas (`n1_ok != n2_ok`), flip do judge
    e fronteira do score. Todos esses sinais moram em `runs/<id>/scores/` — e este pacote
    **não pode ler score**: se pudesse, a cegueira do rotulador voltaria a depender de
    disciplina, e a independência do κ é justamente o que a T22 existe para tornar
    estrutural. Ver o teste de varredura em `tests/test_labeling.py`.

    O que sobra é derivar a prioridade do TRACE, que o rotulador vai ver de qualquer jeito.
    `prioridade_revisao_humana` usa cinco sinais que dizem "aqui as duas configurações do
    judge têm insumo diferente o bastante para discordarem" — que é o mesmo fenômeno que
    §5 caça, medido um degrau antes. É proxy, não equivalente, e está registrado como tal.

A `env_seed` NÃO ESTÁ NO `RunStart`
    `RunStart.seed` é a `sample_seed`. A `env_seed` só existe no `run_id`
    (`runner/matriz.py`) e em `manifest.json::celulas`. Sai do `run_id` aqui, e não do
    manifesto, porque o manifesto é reescrito por re-execução parcial e o nome do trace não.
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from tapieval.schema.trace import FinalAnswer, RunStart, ToolResult, TraceEvent

TipoDeAmostra = Literal["estimativa", "melhoria"]

SEED_DA_AMOSTRAGEM = 42
"""A seed do sorteio da estimativa, fixada e gravada em cada rótulo.

Ela não é decorativa: sem a seed no arquivo de saída, "aleatória estratificada" é uma
afirmação que ninguém pode conferir depois, e a amostra do README deixa de ser reproduzível.
"""

N_ESTIMATIVA = 20
N_MELHORIA = 15

STATUS_INTEGRO = "COMPLETO"
"""O único `StatusRetorno` em que a evidência chega inteira ao judge. Os outros quatro
(`PARCIAL`, `INCONCLUSIVO`, `CONFLITO`, `INDISPONIVEL`) são degradação declarada."""


class AmostraInsuficiente(RuntimeError):
    """Não há runs suficientes para as duas amostras no tamanho pedido.

    Erro, e não amostra menor em silêncio: encolher o n sozinho é mudança de denominador
    sem aviso — o formato do X12 —, e o README continuaria dizendo "κ sobre 20 itens".
    """


# ---------------------------------------------------------------------------
# Sinais de incerteza — puros, derivados só do trace
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SinaisDeIncerteza:
    """O que, no trace, faz as duas configurações do judge terem insumo diferente.

    Todos os campos são fatos do trace, observáveis sem pontuar nada. O default é a run
    limpa: prioridade zero.
    """

    sem_resposta_final: bool = False
    """A run terminou sem `final_answer`. O cego recebe texto vazio e o com trace recebe a
    evidência inteira — o maior desacordo estrutural possível entre as duas configurações."""

    citacao_fora_do_trace: bool = False
    """O agente citou um `tool_call_id` que não existe no trace. `InsumoDoJudge.ids_visiveis`
    dá ao cego apenas os ids ALEGADOS, então a citação inventada é invisível para ele e
    evidente para o com trace: discordância garantida, e é um C5 que a rubrica não pergunta."""

    respondeu_sem_consultar_evidencia: bool = False
    """Respondeu sem nenhum `tool_result`. O cego julga o texto; o com trace julga um bloco
    vazio. `afirmacoes_sem_suporte` e `causa_raiz_correta` divergem quase por construção."""

    evidencia_degradada: int = 0
    """Quantos `tool_result` voltaram fora de `COMPLETO`. É onde vive a pergunta mais
    ambígua da rubrica — `mencionou_limitacao_relevante` (N3.2, o campo do C4): decidir se a
    limitação era "relevante" depende de saber o que degradou, e o cego não sabe."""

    resposta_sem_citacao: bool = False
    """Consultou evidência, respondeu e não citou nada. A justificativa do judge cego fica
    sem id nenhum para ancorar, e a auditabilidade que `METRICAS §4` exige some do par."""


PESOS_DE_PRIORIDADE: Mapping[str, float] = {
    "sem_resposta_final": 3.0,
    "citacao_fora_do_trace": 2.5,
    "respondeu_sem_consultar_evidencia": 2.0,
    "evidencia_degradada": 1.5,
    "resposta_sem_citacao": 1.0,
}
"""A escala é a de `METRICAS §5` (3.0 / 2.5 / 1.5 / 1.0) de propósito: a fila de melhoria é
lida por quem conhece aquele pseudocódigo, e mudar a régua junto com os sinais tornaria as
duas versões incomparáveis. O que mudou foi de ONDE cada sinal vem, não quanto ele pesa."""


def sinais_de_incerteza(eventos: Sequence[TraceEvent]) -> SinaisDeIncerteza:
    """Os cinco sinais de uma run. Função pura de `eventos` — sem disco, sem relógio.

    Só olha `ToolResult` e `FinalAnswer`, os mesmos eventos que `montar_insumo` consome:
    o que o rotulador vai ver é o que decide a prioridade, e nada além.
    """
    resultados = [evento for evento in eventos if isinstance(evento, ToolResult)]
    finais = [evento for evento in eventos if isinstance(evento, FinalAnswer)]
    final = finais[-1] if finais else None

    respondeu = final is not None and bool(final.texto.strip())
    ids_no_trace = {resultado.tool_call_id for resultado in resultados}
    citacoes = tuple(final.citacoes) if final else ()

    return SinaisDeIncerteza(
        sem_resposta_final=not respondeu,
        citacao_fora_do_trace=any(citacao not in ids_no_trace for citacao in citacoes),
        respondeu_sem_consultar_evidencia=respondeu and not resultados,
        evidencia_degradada=sum(
            1 for resultado in resultados if str(resultado.status) != STATUS_INTEGRO
        ),
        resposta_sem_citacao=respondeu and bool(resultados) and not citacoes,
    )


def prioridade_revisao_humana(sinais: SinaisDeIncerteza) -> float:
    """Quanto uma rotulagem desta run ensina sobre a rubrica. Só para a fila de melhoria.

    **Aplicar isto à amostra de estimativa destruiria o κ** (`METRICAS §5`, literal): a fila
    prioriza o caso difícil, e concordância medida sobre casos difíceis não estima
    concordância na população. Por isso `ItemDaAmostra.prioridade` é `None` na estimativa —
    o número nem existe do lado que não pode usá-lo.

    O critério: **onde o judge é menos confiável é onde as duas configurações dele recebem
    insumos que não sustentam a mesma resposta.** Os cinco sinais, em ordem de peso, e o que
    cada um prevê:

    | Sinal | Peso | Divergência que ele prevê |
    |---|---|---|
    | `sem_resposta_final` | 3.0 | cego julga vazio, com trace julga a evidência |
    | `citacao_fora_do_trace` | 2.5 | o id inventado é invisível para o cego |
    | `respondeu_sem_consultar_evidencia` | 2.0 | `afirmacoes_sem_suporte` sem base para existir |
    | `evidencia_degradada` | 1.5 | N3.2 ("limitação relevante") exige saber o que degradou |
    | `resposta_sem_citacao` | 1.0 | justificativa sem id para ancorar dos dois lados |

    A soma é linear e os pesos são de curadoria — não saíram de dado nenhum, porque não há
    dado antes das 35 rotulagens. Eles ordenam a fila; não entram em número reportado.
    """
    total = 0.0
    if sinais.sem_resposta_final:
        total += PESOS_DE_PRIORIDADE["sem_resposta_final"]
    if sinais.citacao_fora_do_trace:
        total += PESOS_DE_PRIORIDADE["citacao_fora_do_trace"]
    if sinais.respondeu_sem_consultar_evidencia:
        total += PESOS_DE_PRIORIDADE["respondeu_sem_consultar_evidencia"]
    if sinais.evidencia_degradada > 0:
        # O peso não cresce com a contagem: duas evidências degradadas não tornam a run duas
        # vezes mais ambígua, e uma run com dez consultas degradadas monopolizaria a fila.
        total += PESOS_DE_PRIORIDADE["evidencia_degradada"]
    if sinais.resposta_sem_citacao:
        total += PESOS_DE_PRIORIDADE["resposta_sem_citacao"]
    return total


# ---------------------------------------------------------------------------
# Candidato — uma run elegível, com o que a amostragem precisa saber e só isso
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidato:
    """Uma run elegível para rotulagem.

    Carrega as cinco coordenadas da célula porque são elas que ligam o rótulo ao registro
    correspondente depois; carrega `caminho` porque é a CLI que faz I/O, não este módulo.
    """

    run_id: str
    experiment_id: str
    scenario_id: str
    model_key: str
    variant_id: str
    env_seed: str
    sample_seed: int
    caminho: Path
    sinais: SinaisDeIncerteza

    @property
    def estrato(self) -> tuple[str, str]:
        """`(cenário, modelo)`. O produto cartesiano equilibra as DUAS margens de uma vez —
        estratificar só por cenário deixaria a amostra livre para concentrar num modelo."""
        return (self.scenario_id, self.model_key)


def env_seed_do_run_id(run_id: str) -> str:
    """A `env_seed` do nome da run. `...--env<seed>--n<sample_seed>` (`runner/matriz.py`).

    Falha alto se o formato mudar. Uma `env_seed` errada em silêncio ligaria o rótulo à
    célula errada da matriz, e o κ compararia julgamentos de execuções diferentes.
    """
    partes = run_id.split("--")
    if len(partes) < 5 or not partes[-2].startswith("env") or not partes[-1].startswith("n"):
        raise ValueError(
            f"run_id fora do formato de `runner/matriz.py`: {run_id!r} "
            "(esperava <cenario>--<modelo>--<variante>--env<seed>--n<sample_seed>)"
        )
    return partes[-2][len("env") :]


def candidato_de_trace(eventos: Sequence[TraceEvent], caminho: Path) -> Candidato:
    """O `RunStart` mais os sinais viram um candidato. Puro sobre os eventos já lidos."""
    inicios = [evento for evento in eventos if isinstance(evento, RunStart)]
    if not inicios:
        raise ValueError(f"{caminho.name}: trace sem `run_start` — não dá para identificar a run")
    inicio = inicios[0]

    return Candidato(
        run_id=inicio.run_id,
        experiment_id=inicio.experiment_id,
        scenario_id=inicio.scenario_id,
        model_key=inicio.model_key,
        variant_id=inicio.variant_id,
        env_seed=env_seed_do_run_id(inicio.run_id),
        sample_seed=inicio.seed,
        caminho=caminho,
        sinais=sinais_de_incerteza(eventos),
    )


# ---------------------------------------------------------------------------
# A amostragem
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ItemDaAmostra:
    """Um caso da fila de rotulagem, já com o rótulo da amostra a que pertence."""

    candidato: Candidato
    amostra: TipoDeAmostra
    """Obrigatório e sem default: `ARQUITETURA §5`, decisão 7 — o campo existe para
    impossibilitar, por acidente, calcular κ misturando as duas."""

    prioridade: float | None
    """`None` na estimativa, sempre. Ver `prioridade_revisao_humana`."""

    seed: int
    """A seed que produziu esta amostra, para viajar até o arquivo de rótulos."""


def amostrar(
    candidatos: Iterable[Candidato],
    *,
    n_estimativa: int = N_ESTIMATIVA,
    n_melhoria: int = N_MELHORIA,
    seed: int = SEED_DA_AMOSTRAGEM,
) -> tuple[ItemDaAmostra, ...]:
    """As duas amostras de `METRICAS §5`, disjuntas por construção.

    A estimativa vem primeiro, sorteada sobre o corpus inteiro; a melhoria sai do que
    sobrou, ordenada por `prioridade_revisao_humana`. A ordem importa: se a melhoria
    escolhesse antes, ela removeria os casos difíceis do universo da estimativa, e a
    "aleatória" passaria a ser aleatória sobre um corpus podado — enviesada para fácil.

    Determinística: a mesma `seed` sobre o mesmo conjunto dá a mesma amostra,
    independentemente da ordem em que os traces foram lidos do disco (`glob` não promete
    ordem, e a seed gravada no arquivo precisa reproduzir alguma coisa).
    """
    unicos = _ordenados_e_unicos(candidatos)
    if len(unicos) < n_estimativa + n_melhoria:
        raise AmostraInsuficiente(
            f"{len(unicos)} runs elegíveis para uma amostra de {n_estimativa + n_melhoria} "
            f"({n_estimativa} de estimativa + {n_melhoria} de melhoria). Reduzir o n é "
            "decisão do operador (--n-estimativa/--n-melhoria), não do instrumento: "
            "encolher sozinho mudaria o denominador sem aviso"
        )

    sorteio = random.Random(seed)
    estimativa = _sortear_estratificado(unicos, n_estimativa, sorteio)

    escolhidos = {candidato.run_id for candidato in estimativa}
    sobra = [candidato for candidato in unicos if candidato.run_id not in escolhidos]
    melhoria = _fila_de_melhoria(sobra, n_melhoria, sorteio)

    return (
        *(
            ItemDaAmostra(candidato, "estimativa", None, seed)
            for candidato in estimativa
        ),
        *(
            ItemDaAmostra(
                candidato, "melhoria", prioridade_revisao_humana(candidato.sinais), seed
            )
            for candidato in melhoria
        ),
    )


def _ordenados_e_unicos(candidatos: Iterable[Candidato]) -> list[Candidato]:
    """Ordem canônica por `run_id`, e `run_id` repetido é erro.

    Dois traces com o mesmo `run_id` rotulariam a mesma run duas vezes; no κ isso entra
    como dois pares independentes, o que estreita o intervalo de confiança sem nenhuma
    informação nova por trás.
    """
    por_id: dict[str, Candidato] = {}
    for candidato in candidatos:
        if candidato.run_id in por_id:
            raise ValueError(
                f"run_id repetido entre os candidatos: {candidato.run_id!r} — "
                "rotular a mesma run duas vezes infla o n do κ sem informação nova"
            )
        por_id[candidato.run_id] = candidato
    return [por_id[chave] for chave in sorted(por_id)]


def _sortear_estratificado(
    candidatos: Sequence[Candidato], n: int, sorteio: random.Random
) -> list[Candidato]:
    """Sorteio estratificado por `(cenário, modelo)`, alocado em rodízio.

    Rodízio em vez de alocação proporcional porque n=20 sobre 12 estratos não divide: a
    proporcional arredondaria alguns estratos para zero, e um cenário ausente da amostra de
    estimativa significa que o κ não diz nada sobre aquele cenário. No rodízio a diferença
    entre o estrato mais e o menos representado nunca passa de um item, e QUAIS estratos
    ganham o item extra é decidido pela seed.
    """
    return _rodizio_por_estrato(candidatos, n, sorteio)


def _fila_de_melhoria(
    sobra: Sequence[Candidato], n: int, sorteio: random.Random
) -> list[Candidato]:
    """Por prioridade decrescente e, DENTRO de cada empate, rodízio por `(cenário, modelo)`.

    O empate não é exceção aqui, é o caso comum (A25). Sobre as 84 runs da bateria de
    calibração de 26/08, três dos cinco sinais de `prioridade_revisao_humana` dão **zero**: a
    prioridade colapsa em quatro valores e **42 runs empatam no topo**, então o desempate
    escolhe quinze dos quinze. Um `shuffle` puro num empate desse tamanho concentra — ele
    devolvia 6 dos 15 num cenário só e 10 num modelo só, cobrindo 9 dos 12 estratos. A fila
    existe para achar **ambiguidade da rubrica**, e rubrica ambígua se manifesta cenário a
    cenário: quinze casos espalhados por doze estratos ensinam mais que quinze casos de dois.

    O rodízio é o mesmo mecanismo de `_sortear_estratificado`, e pela mesma razão: a diferença
    entre o estrato mais e o menos representado nunca passa de um item, e quais estratos ganham
    o item extra continua decidido pela seed — o desempate segue semeado e reproduzível, só
    deixou de ser cego para as margens.

    A ORDEM DE PRIORIDADE CONTINUA MANDANDO. O rodízio só age dentro de uma faixa de prioridade;
    nenhuma run de faixa menor passa à frente de uma de faixa maior.

    O QUE ISTO NÃO CONSERTA, DECLARADO: os 42 do topo empatam porque **todos** têm
    `sem_resposta_final`, então a fila continua saindo 15/15 sem `final_answer`. Isso é da
    ORDENAÇÃO, não do desempate — alcançar uma run com resposta exigiria pular a faixa 3,0
    inteira, que é abandonar a ordem de prioridade que a fila existe para seguir. Fica como
    limitação declarada da T22.
    """
    por_prioridade: dict[float, list[Candidato]] = {}
    for candidato in sobra:
        por_prioridade.setdefault(
            prioridade_revisao_humana(candidato.sinais), []
        ).append(candidato)

    escolhidos: list[Candidato] = []
    for prioridade in sorted(por_prioridade, reverse=True):
        if len(escolhidos) >= n:
            break
        escolhidos.extend(
            _rodizio_por_estrato(
                por_prioridade[prioridade], n - len(escolhidos), sorteio
            )
        )
    return escolhidos


def _rodizio_por_estrato(
    candidatos: Sequence[Candidato], n: int, sorteio: random.Random
) -> list[Candidato]:
    """Até `n` candidatos, um estrato `(cenário, modelo)` de cada vez, em ordem semeada.

    Devolve menos que `n` quando os candidatos acabam — quem chama decide se isso é erro
    (a estimativa, que tem `AmostraInsuficiente` acima) ou continuação (a fila de melhoria,
    que passa para a faixa de prioridade seguinte).
    """
    estratos: dict[tuple[str, str], list[Candidato]] = {}
    for candidato in candidatos:
        estratos.setdefault(candidato.estrato, []).append(candidato)

    chaves = sorted(estratos)
    sorteio.shuffle(chaves)
    filas = {chave: list(estratos[chave]) for chave in chaves}
    for fila in filas.values():
        sorteio.shuffle(fila)

    escolhidos: list[Candidato] = []
    while len(escolhidos) < n:
        avancou = False
        for chave in chaves:
            if len(escolhidos) >= n:
                break
            if filas[chave]:
                escolhidos.append(filas[chave].pop())
                avancou = True
        if not avancou:
            break
    return escolhidos
