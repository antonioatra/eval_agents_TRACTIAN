"""T22 — a CLI de rotulagem humana cega (`METRICAS §4`, §5, §7).

O que estes testes protegem, em ordem de importância:

1. **A cegueira é do INSTRUMENTO, não da disciplina de quem rotula.** Dois testes: um
   estrutural, que varre o pacote atrás de qualquer referência a score fora de docstring, e
   um comportamental, que roda a sessão inteira com um leitor de disco que explode se
   alguém abrir `runs/*/scores/`. Sem os dois, "não mostrei o judge" é promessa; o κ da
   INS.6 é uma medida de independência, e âncora não deixa rastro nenhum no número.
2. **As duas amostras são disjuntas, e a de estimativa é sorteada primeiro.** A fila de
   melhoria prioriza o caso difícil de propósito (`METRICAS §5`, N4.2); se um item dela
   vazasse para a estimativa, o κ passaria a ser medido sobre casos escolhidos por serem
   ambíguos — e concordância em caso difícil não estima concordância na população.
3. **`None`, nunca `False`, nos três campos que exigem trace.** Mesma invariante do
   `N3Judge` (`schema/trace.py`), pelo mesmo motivo: um `False` do rotulador cego diria
   "olhei a evidência e não achei" sobre evidência que ele não viu, e o κ daquele campo
   contaria isso como concordância com o judge com trace.
4. **A retomada não re-rotula e não duplica linha.** São 35 rotulagens à mão; uma sessão
   interrompida que recomeça do zero custa o recurso mais escasso do projeto.
5. **A amostragem é pura e determinística.** Mesma seed, mesma amostra, independente da
   ordem em que os traces foram lidos do disco.

Nenhum teste aqui lê stdin: `rodar_sessao` recebe `ler`/`escrever` injetados, que é o que
permite dirigir a sessão inteira de dentro do teste.
"""

from __future__ import annotations

import ast
import itertools
import json
from collections.abc import Iterator, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tapieval.labeling import cli as mod_cli
from tapieval.labeling.amostra import (
    N_ESTIMATIVA,
    N_MELHORIA,
    SEED_DA_AMOSTRAGEM,
    AmostraCongeladaIncompativel,
    AmostraInsuficiente,
    Candidato,
    SinaisDeIncerteza,
    amostrar,
    candidato_de_trace,
    congelar,
    descongelar,
    env_seed_do_run_id,
    impressao_do_universo,
    prioridade_revisao_humana,
    sinais_de_incerteza,
)
from tapieval.labeling.cli import (
    RotuloHumano,
    apresentar_caso,
    carregar_candidatos,
    main,
    rodar_sessao,
    run_ids_ja_rotulados,
)
from tapieval.schema.trace import (
    FinalAnswer,
    LLMCall,
    RunStart,
    ToolCall,
    ToolResult,
    TraceEvent,
)
from tapieval.scoring.gabarito import Cenario, Regra
from tapieval.scoring.n3 import InsumoDoJudge, montar_insumo

AGORA = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fixtures — traces sintéticos, um `Cenario` de teste, e um lote de candidatos
# ---------------------------------------------------------------------------


_SEQUENCIA = itertools.count(1)


def evento(classe: type, run_id: str = "run_teste", **campos: Any) -> TraceEvent:
    return classe(ts=AGORA, run_id=run_id, iteration=1, seq=next(_SEQUENCIA), **campos)


def trace_completo(run_id: str, *, scenario_id: str, model_key: str) -> list[TraceEvent]:
    """Uma run sã: hidratação, uma consulta, resposta citando o que consultou."""
    return [
        evento(
            RunStart,
            run_id,
            experiment_id="piloto_teste",
            scenario_id=scenario_id,
            split="dev",
            variant_id="base",
            model_key=model_key,
            seed=11,
            env_mode="live",
            solicitacao="O baseline está ok, né?",
            user_id="usr_ana",
            asset_id="asset_B211",
        ),
        evento(
            ToolCall,
            run_id,
            tool_call_id="tc_01",
            tool_name="get_baseline",
            args={"asset_id": "asset_B211"},
            args_validos=True,
        ),
        evento(
            ToolResult,
            run_id,
            tool_call_id="tc_01",
            status="COMPLETO",
            latencia_ms=30,
            body={"state": "invalidated", "invalidation_reason": "config_change"},
        ),
        evento(
            FinalAnswer,
            run_id,
            texto="O baseline está invalidado (tc_01).",
            citacoes=["tc_01"],
            citacoes_validas=True,
        ),
    ]


def trace_sem_resposta(run_id: str, *, scenario_id: str, model_key: str) -> list[TraceEvent]:
    """A run que estourou o budget sem responder — o pior caso para o judge cego."""
    return [
        evento
        for evento in trace_completo(run_id, scenario_id=scenario_id, model_key=model_key)
        if not isinstance(evento, FinalAnswer)
    ]


def run_id_de(scenario_id: str, model_key: str, sample_seed: int = 11) -> str:
    return f"{scenario_id}--{model_key}--base--envs001--n{sample_seed}"


def lote_de_candidatos(
    *, cenarios: int = 6, modelos: int = 2, seeds: int = 4
) -> list[Candidato]:
    """Uma matriz cheia — o suficiente para 20 + 15 sem esbarrar no piso."""
    candidatos: list[Candidato] = []
    for indice_cenario in range(cenarios):
        for indice_modelo in range(modelos):
            for sample_seed in range(seeds):
                scenario_id = f"cen_{indice_cenario:02d}"
                model_key = f"modelo-{indice_modelo}"
                candidatos.append(
                    Candidato(
                        run_id=run_id_de(scenario_id, model_key, sample_seed),
                        experiment_id="piloto_teste",
                        scenario_id=scenario_id,
                        model_key=model_key,
                        variant_id="base",
                        env_seed="s001",
                        sample_seed=sample_seed,
                        caminho=Path(f"/traces/{scenario_id}.jsonl"),
                        sinais=SinaisDeIncerteza(),
                    )
                )
    return candidatos


@pytest.fixture
def cenario() -> Cenario:
    return Cenario(
        id="cen_00",
        regra=Regra(
            nome="premissa_contradita_pela_evidencia",
            decisao="orientar",
            quando="a mensagem afirma algo que o estado contradiz",
            exige="contradiz explicitamente a premissa citando a evidência",
        ),
        split="dev",
        criticidade_declarada="medium",
        evidencias_obrigatorias=("baseline.state",),
        fontes_obrigatorias={"baseline": "complete"},
        solicitacao="O baseline está ok, né? Confirma pra mim.",
        criterio_sucesso="Contradiz a premissa citando o estado do baseline.",
    )


@pytest.fixture
def insumo(cenario: Cenario) -> InsumoDoJudge:
    eventos = trace_completo(
        run_id_de("cen_00", "modelo-0"), scenario_id="cen_00", model_key="modelo-0"
    )
    return montar_insumo(eventos, cenario)


class Roteiro:
    """Um `ler` de roteiro: devolve respostas pré-escritas, uma por pergunta.

    Guarda as perguntas que recebeu — metade dos testes de ergonomia é sobre O QUE foi
    perguntado e quantas vezes, e não há outra forma de ver isso sem stdin de verdade.
    """

    def __init__(self, *respostas: str):
        self.respostas = list(respostas)
        self.perguntas: list[str] = []

    def __call__(self, pergunta: str) -> str:
        self.perguntas.append(pergunta)
        if not self.respostas:
            raise EOFError("roteiro esgotado")
        return self.respostas.pop(0)


def rotulo_cego(*respostas_extras: str) -> list[str]:
    """A sequência mínima de teclas que rotula um caso no modo cego."""
    return ["r", "s", "s", "sim", "porque a evidência contradiz a premissa", *respostas_extras]


def rotulo_com_trace() -> list[str]:
    """A mesma coisa no modo com trace: mais a lista, mais dois booleanos."""
    return [
        "r",
        "s",
        "s",
        "sim",
        "",  # `afirmacoes_sem_suporte` vazia
        "n",
        "n",
        "tc_01 sustenta a afirmação",
    ]


def escrita() -> tuple[list[str], Any]:
    linhas: list[str] = []
    return linhas, linhas.append


def ler_jsonl(caminho: Path) -> list[dict[str, Any]]:
    return [
        json.loads(linha)
        for linha in caminho.read_text(encoding="utf-8").splitlines()
        if linha.strip()
    ]


# ---------------------------------------------------------------------------
# 1 · A cegueira é do instrumento
# ---------------------------------------------------------------------------


MODULOS_DO_PACOTE = sorted(Path(mod_cli.__file__).parent.glob("*.py"))


def _simbolos_e_literais(caminho: Path) -> Iterator[str]:
    """Todo identificador e todo literal de string do módulo, MENOS as docstrings.

    As docstrings ficam de fora porque este arquivo e o pacote inteiro precisam poder
    EXPLICAR por que não leem score. Um teste que proíbe a palavra também na prosa
    obrigaria a apagar a justificativa da regra junto com a regra.
    """
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    docstrings = {
        id(no.value)
        for no in ast.walk(arvore)
        if isinstance(no, ast.Expr)
        and isinstance(no.value, ast.Constant)
        and isinstance(no.value.value, str)
    }
    for no in ast.walk(arvore):
        if isinstance(no, ast.Constant) and isinstance(no.value, str):
            if id(no) not in docstrings:
                yield no.value
        elif isinstance(no, ast.Name):
            yield no.id
        elif isinstance(no, ast.Attribute):
            yield no.attr
        elif isinstance(no, ast.ImportFrom) and no.module:
            yield no.module


def test_o_pacote_nao_tem_caminho_de_codigo_que_leia_score():
    """Varredura estrutural: nenhum símbolo nem literal do pacote fala de score.

    Não basta "não mostrar" a saída do judge ao rotulador. Se o módulo tiver como carregar
    `runs/<id>/scores/`, a independência do κ (INS.6) passa a depender de ninguém
    acrescentar duas linhas depois — e é exatamente o tipo de acréscimo que parece
    inofensivo em revisão ("só para ordenar a fila"). Este teste é o que faz esse acréscimo
    quebrar a suíte no mesmo commit em que for escrito.
    """
    ofensores: list[tuple[str, str]] = []
    for caminho in MODULOS_DO_PACOTE:
        for simbolo in _simbolos_e_literais(caminho):
            if "score" in simbolo.lower():
                ofensores.append((caminho.name, simbolo))

    assert not ofensores, (
        f"o pacote de rotulagem referencia score fora de docstring: {ofensores}. "
        "A cegueira do rotulador é propriedade do instrumento (METRICAS §5)."
    )


def test_o_pacote_so_importa_de_scoring_o_que_monta_o_insumo():
    """A única dependência legítima de `scoring` é a montagem do insumo do judge.

    `n3.montar_insumo` (o insumo, idêntico ao do judge) e `gabarito` (o corpus). Qualquer
    outro módulo de `scoring` traz junto o vocabulário de pontuação, e com ele a tentação.
    """
    permitidos = {"tapieval.scoring.n3", "tapieval.scoring.gabarito"}
    importados = {
        no.module
        for caminho in MODULOS_DO_PACOTE
        for no in ast.walk(ast.parse(caminho.read_text(encoding="utf-8")))
        if isinstance(no, ast.ImportFrom) and no.module and "scoring" in no.module
    }
    assert importados <= permitidos, f"importa de scoring além do permitido: {importados}"


def test_sessao_completa_com_leitor_que_explode_em_scores(tmp_path: Path, cenario: Cenario):
    """O teste comportamental: qualquer leitura de `scores/` derruba a sessão.

    O estrutural pega a referência escrita à mão; este pega o caminho indireto — uma função
    de outro módulo que resolva `run_dir / <qualquer coisa> / scores`. Os dois juntos são a
    diferença entre uma convenção e uma propriedade.
    """
    run_dir = _montar_run_dir(tmp_path, cenarios=("cen_00",), modelos=("modelo-0",), seeds=(11,))
    (run_dir / "scores" / "v1").mkdir(parents=True)
    (run_dir / "scores" / "v1" / "envenenado.json").write_text(
        json.dumps({"n3": {"causa_raiz_correta": True}}), encoding="utf-8"
    )

    leitura_original = Path.read_text

    def read_text_que_explode(self: Path, *args: Any, **kwargs: Any) -> str:
        if "scores" in self.parts:
            raise AssertionError(f"a rotulagem tentou ler {self} — âncora do judge (METRICAS §5)")
        return leitura_original(self, *args, **kwargs)

    abertura_original = Path.open

    def open_que_explode(self: Path, *args: Any, **kwargs: Any) -> Any:
        if "scores" in self.parts:
            raise AssertionError(f"a rotulagem tentou abrir {self} — âncora do judge")
        return abertura_original(self, *args, **kwargs)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(Path, "read_text", read_text_que_explode)
    monkeypatch.setattr(Path, "open", open_que_explode)
    try:
        destino = tmp_path / "labels" / "humano_2026-08-24.jsonl"
        candidatos = carregar_candidatos(run_dir)
        itens = amostrar(candidatos, n_estimativa=1, n_melhoria=0)
        linhas, escrever = escrita()
        gravados = rodar_sessao(
            itens,
            insumo_de=_insumo_fixo(run_dir, {"cen_00": cenario}),
            configuracao="cego",
            rotulador="antonio",
            destino=destino,
            ja_rotulados=frozenset(),
            ler=Roteiro(*rotulo_cego()),
            escrever=escrever,
            agora=lambda: AGORA,
        )
    finally:
        monkeypatch.undo()

    assert gravados == 1
    assert "envenenado" not in "\n".join(linhas)


def test_o_rotulador_cego_nao_ve_a_evidencia(insumo: InsumoDoJudge):
    """O caso apresentado no modo cego não pode conter o payload das consultas.

    Mesma invariante de `test_judge_cego_nao_ve_a_evidencia_no_prompt`: o rotulador cego e o
    judge cego têm de ver o MESMO insumo, senão o κ compara duas leituras de coisas
    diferentes e o número não significa nada.
    """
    texto = apresentar_caso(insumo, "cego", posicao=1, total=35, amostra="estimativa")

    assert "invalidation_reason" not in texto
    assert "config_change" not in texto
    assert insumo.solicitacao in texto
    assert insumo.resposta in texto
    assert insumo.criterio_sucesso in texto
    assert insumo.regra_exige in texto


def test_o_rotulador_com_trace_ve_a_evidencia(insumo: InsumoDoJudge):
    """E o com trace vê, no mesmo formato que o judge — `BlocoDeEvidencia.renderizar`."""
    texto = apresentar_caso(insumo, "com_trace", posicao=1, total=35, amostra="estimativa")

    assert "invalidation_reason" in texto
    assert "tc_01" in texto


def test_o_caso_apresentado_nao_identifica_o_modelo(insumo: InsumoDoJudge, cenario: Cenario):
    """Saber que a run é do modelo menor é âncora tão boa quanto ver a nota do judge.

    Não está escrito em `METRICAS §5` — lá "cego" é sobre a saída do judge. É decisão desta
    CLI, pelo mesmo argumento: qualquer coisa que permita prever o rótulo sem ler o caso
    contamina o κ. O `scenario_id` continua visível porque o judge também o recebe.
    """
    texto = apresentar_caso(insumo, "com_trace", posicao=1, total=35, amostra="estimativa")

    assert "modelo-0" not in texto
    assert "qwen" not in texto.lower()


# ---------------------------------------------------------------------------
# 2 · As duas amostras
# ---------------------------------------------------------------------------


def test_as_duas_amostras_sao_disjuntas():
    """A garantia central de `METRICAS §5`: nunca misturadas.

    A de melhoria é escolhida por dificuldade; um item dela dentro da estimativa
    transformaria o κ numa concordância medida sobre os casos mais ambíguos do corpus, que
    é sistematicamente menor que a da população — e o viés cai na direção que faz o
    instrumento parecer pior do que é, mas continua sendo viés.
    """
    itens = amostrar(lote_de_candidatos())

    estimativa = {item.candidato.run_id for item in itens if item.amostra == "estimativa"}
    melhoria = {item.candidato.run_id for item in itens if item.amostra == "melhoria"}

    assert len(estimativa) == N_ESTIMATIVA
    assert len(melhoria) == N_MELHORIA
    assert not (estimativa & melhoria)


def test_todo_item_declara_a_amostra_a_que_pertence():
    """Sem default silencioso: `amostra` é campo obrigatório do item e do rótulo gravado."""
    itens = amostrar(lote_de_candidatos())
    assert all(item.amostra in ("estimativa", "melhoria") for item in itens)


def test_a_estimativa_nao_carrega_prioridade():
    """`prioridade` é `None` na estimativa, e não zero.

    Um número ali convidaria a ordenar a fila de estimativa por ele — que é exatamente o
    que `METRICAS §5` proíbe ("aplicá-la à amostra de estimativa destruiria o κ").
    """
    itens = amostrar(lote_de_candidatos())
    assert all(item.prioridade is None for item in itens if item.amostra == "estimativa")
    assert all(item.prioridade is not None for item in itens if item.amostra == "melhoria")


def test_a_estimativa_e_estratificada_por_cenario_e_por_modelo():
    """Nenhum estrato `(cenário, modelo)` fica de fora, e nenhum leva o dobro do outro.

    A garantia exata do rodízio é **por estrato**: com 12 estratos e n=20, oito ficam com 2
    e quatro com 1. É ela que importa — um cenário ausente da amostra de estimativa
    significa que o κ não diz nada sobre aquele cenário.

    Nas margens a garantia é mais frouxa por consequência aritmética: os 8 itens extras são
    distribuídos entre os 12 estratos pela seed, e um cenário pode receber os dois extras
    enquanto outro não recebe nenhum — diferença de 2 na margem, nunca mais que isso.
    """
    itens = amostrar(lote_de_candidatos())
    estimativa = [item.candidato for item in itens if item.amostra == "estimativa"]

    por_estrato: dict[tuple[str, str], int] = {}
    por_cenario: dict[str, int] = {}
    por_modelo: dict[str, int] = {}
    for candidato in estimativa:
        por_estrato[candidato.estrato] = por_estrato.get(candidato.estrato, 0) + 1
        por_cenario[candidato.scenario_id] = por_cenario.get(candidato.scenario_id, 0) + 1
        por_modelo[candidato.model_key] = por_modelo.get(candidato.model_key, 0) + 1

    assert len(por_estrato) == 12
    assert max(por_estrato.values()) - min(por_estrato.values()) <= 1

    assert len(por_cenario) == 6
    assert max(por_cenario.values()) - min(por_cenario.values()) <= 2
    assert len(por_modelo) == 2
    assert max(por_modelo.values()) - min(por_modelo.values()) <= 2


def test_a_amostragem_nao_depende_da_ordem_de_leitura_do_disco():
    """`glob` não promete ordem, e a amostra não pode depender dela.

    Se dependesse, reexecutar a amostragem noutra máquina daria outros 20 itens com a mesma
    seed — e a `seed=42` gravada no arquivo deixaria de reproduzir coisa nenhuma.
    """
    candidatos = lote_de_candidatos()
    direto = amostrar(candidatos)
    invertido = amostrar(list(reversed(candidatos)))

    assert [item.candidato.run_id for item in direto] == [
        item.candidato.run_id for item in invertido
    ]


def test_a_mesma_seed_da_a_mesma_amostra_e_outra_seed_da_outra():
    candidatos = lote_de_candidatos()
    a = [item.candidato.run_id for item in amostrar(candidatos, seed=SEED_DA_AMOSTRAGEM)]
    b = [item.candidato.run_id for item in amostrar(candidatos, seed=SEED_DA_AMOSTRAGEM)]
    c = [item.candidato.run_id for item in amostrar(candidatos, seed=7)]

    assert a == b
    assert a != c


def test_a_seed_vai_gravada_em_cada_item():
    itens = amostrar(lote_de_candidatos(), seed=SEED_DA_AMOSTRAGEM)
    assert {item.seed for item in itens} == {SEED_DA_AMOSTRAGEM}


def test_corpus_pequeno_demais_falha_em_vez_de_encolher_a_amostra():
    """Amostra menor em silêncio é mudança de denominador sem aviso — o formato do X12.

    Quem quiser rotular 10 em vez de 35 passa `--n-estimativa`; o que não pode é o
    instrumento decidir isso sozinho e o README continuar dizendo "20 itens".
    """
    with pytest.raises(AmostraInsuficiente, match="35"):
        amostrar(lote_de_candidatos(cenarios=2, modelos=2, seeds=2))


def test_run_id_repetido_e_erro():
    """Dois traces com o mesmo `run_id` rotulariam a mesma run duas vezes sem que apareça."""
    candidatos = lote_de_candidatos()
    with pytest.raises(ValueError, match="repetid"):
        amostrar([*candidatos, candidatos[0]])


# ---------------------------------------------------------------------------
# 3 · A prioridade da fila de melhoria
# ---------------------------------------------------------------------------


def test_prioridade_cresce_com_o_que_torna_o_judge_menos_confiavel():
    """A ordem entre os sinais é a que o docstring de `prioridade_revisao_humana` declara."""
    limpa = prioridade_revisao_humana(SinaisDeIncerteza())
    sem_resposta = prioridade_revisao_humana(SinaisDeIncerteza(sem_resposta_final=True))
    citacao_falsa = prioridade_revisao_humana(SinaisDeIncerteza(citacao_fora_do_trace=True))
    degradada = prioridade_revisao_humana(SinaisDeIncerteza(evidencia_degradada=2))

    assert limpa == 0.0
    assert sem_resposta > citacao_falsa > degradada > limpa


def test_a_melhoria_pega_os_casos_de_maior_prioridade():
    """A fila de melhoria não é aleatória (`METRICAS §5`, N4.2)."""
    candidatos = lote_de_candidatos()
    marcados = {candidatos[indice].run_id for indice in (0, 5, 13, 21, 30, 40, 44, 47)}
    candidatos = [
        candidato
        if candidato.run_id not in marcados
        else replace(candidato, sinais=SinaisDeIncerteza(sem_resposta_final=True))
        for candidato in candidatos
    ]

    itens = amostrar(candidatos, n_estimativa=4, n_melhoria=4)
    melhoria = {item.candidato.run_id for item in itens if item.amostra == "melhoria"}
    estimativa = {item.candidato.run_id for item in itens if item.amostra == "estimativa"}

    # Todo marcado que sobrou do sorteio da estimativa está na melhoria.
    assert melhoria <= marcados
    assert len(melhoria & estimativa) == 0


def test_empate_de_prioridade_espalha_a_melhoria_pelos_estratos():
    """O A25: quando quase todo mundo empata, o desempate não pode concentrar.

    Sobre as 84 runs da bateria de calibração, três dos cinco sinais dão zero e 42 runs
    empatam no topo — o desempate escolhe a fila inteira. O `shuffle` puro que estava aqui
    devolvia 6 dos 15 num cenário só e 10 num modelo só. Com 48 candidatos empatados em 12
    estratos e uma fila de 12, o rodízio obriga **um de cada estrato**: é a asserção mais
    forte possível sobre o espalhamento, e ela falha com qualquer sorteio sem rodízio.
    """
    candidatos = [
        replace(candidato, sinais=SinaisDeIncerteza(sem_resposta_final=True))
        for candidato in lote_de_candidatos()
    ]

    itens = amostrar(candidatos, n_estimativa=0, n_melhoria=12)
    melhoria = [item for item in itens if item.amostra == "melhoria"]

    assert len(melhoria) == 12
    estratos = {item.candidato.estrato for item in melhoria}
    assert len(estratos) == 12, "o desempate concentrou — algum estrato saiu duas vezes"


def test_o_rodizio_nao_passa_a_frente_da_prioridade():
    """O rodízio age DENTRO do empate. Faixa menor nunca ultrapassa faixa maior.

    O risco que este teste fecha é o do conserto do A25 ter trocado uma degeneração por
    outra: espalhar por estrato à custa da ordem de dificuldade transformaria a fila de
    melhoria numa segunda amostra aleatória, e a fila existe justamente por não ser isso.
    """
    dificil = ("cen_00", "modelo-0")
    candidatos = [
        replace(candidato, sinais=SinaisDeIncerteza(sem_resposta_final=True))
        if candidato.estrato == dificil
        else candidato
        for candidato in lote_de_candidatos()
    ]

    itens = amostrar(candidatos, n_estimativa=0, n_melhoria=4)
    melhoria = [item for item in itens if item.amostra == "melhoria"]

    assert len(melhoria) == 4
    assert all(item.candidato.estrato == dificil for item in melhoria)
    assert all(item.prioridade == prioridade_revisao_humana(
        SinaisDeIncerteza(sem_resposta_final=True)
    ) for item in melhoria)


def test_sinais_saem_do_trace_e_de_mais_nada():
    """`sinais_de_incerteza` é pura sobre eventos — sem disco, sem judge, sem score."""
    completo = sinais_de_incerteza(
        trace_completo(run_id_de("cen_00", "m"), scenario_id="cen_00", model_key="m")
    )
    assert completo == SinaisDeIncerteza()

    sem_resposta = sinais_de_incerteza(
        trace_sem_resposta(run_id_de("cen_00", "m"), scenario_id="cen_00", model_key="m")
    )
    assert sem_resposta.sem_resposta_final


def test_citacao_fora_do_trace_e_sinal():
    """O cego só conhece os ids que o agente ALEGOU (`InsumoDoJudge.ids_visiveis`).

    Uma citação que não existe no trace é invisível para ele e evidente para o com trace:
    divergência garantida entre as duas configurações, que é o que a fila de melhoria caça.
    """
    eventos = trace_completo(run_id_de("cen_00", "m"), scenario_id="cen_00", model_key="m")
    eventos = [
        evento
        if not isinstance(evento, FinalAnswer)
        else FinalAnswer(
            ts=AGORA,
            run_id=evento.run_id,
            iteration=1,
            seq=evento.seq,
            texto="baseado em tc_09",
            citacoes=["tc_09"],
            citacoes_validas=False,
        )
        for evento in eventos
    ]

    assert sinais_de_incerteza(eventos).citacao_fora_do_trace


def test_evidencia_degradada_e_sinal():
    eventos = trace_completo(run_id_de("cen_00", "m"), scenario_id="cen_00", model_key="m")
    eventos = [
        evento
        if not isinstance(evento, ToolResult)
        else ToolResult(
            ts=AGORA,
            run_id=evento.run_id,
            iteration=1,
            seq=evento.seq,
            tool_call_id="tc_01",
            status="INCONCLUSIVO",
            latencia_ms=10,
            body=None,
        )
        for evento in eventos
    ]

    assert sinais_de_incerteza(eventos).evidencia_degradada == 1


# ---------------------------------------------------------------------------
# 4 · Os campos do rótulo, e o `None` que não é `False`
# ---------------------------------------------------------------------------


def test_o_rotulo_cego_deixa_none_nos_tres_campos_que_exigem_trace(
    tmp_path: Path, cenario: Cenario
):
    """A invariante 3 da abertura, gravada no arquivo.

    Um `False` diria "olhei a evidência e não achei" sobre evidência não vista, e o κ do
    campo contaria isso como concordância com o judge com trace — que olhou.
    """
    destino = _rotular_um(tmp_path, cenario, configuracao="cego", teclas=rotulo_cego())
    linha = ler_jsonl(destino)[0]

    assert linha["configuracao"] == "cego"
    assert linha["afirmacoes_sem_suporte"] is None
    assert linha["contradiz_evidencia"] is None
    assert linha["recomendou_acao_sem_base"] is None
    assert linha["causa_raiz_correta"] is True
    assert linha["responde_a_pergunta"] == "sim"
    assert linha["justificativa"]


def test_o_rotulo_com_trace_preenche_os_seis(tmp_path: Path, cenario: Cenario):
    destino = _rotular_um(
        tmp_path, cenario, configuracao="com_trace", teclas=rotulo_com_trace()
    )
    linha = ler_jsonl(destino)[0]

    assert linha["configuracao"] == "com_trace"
    assert linha["afirmacoes_sem_suporte"] == []
    assert linha["contradiz_evidencia"] is False
    assert linha["recomendou_acao_sem_base"] is False


def test_rotulo_cego_que_preenche_campo_de_trace_e_recusado():
    """A mesma invariante do `N3Judge`, no schema do rótulo — não só na coleta."""
    with pytest.raises(ValueError, match="exigem trace"):
        RotuloHumano(
            run_id="r",
            experiment_id="e",
            scenario_id="c",
            model_key="m",
            variant_id="base",
            env_seed="s001",
            sample_seed=11,
            amostra="estimativa",
            configuracao="cego",
            rotulador="antonio",
            seed_da_amostragem=42,
            rotulado_em=AGORA,
            causa_raiz_correta=True,
            mencionou_limitacao_relevante=True,
            responde_a_pergunta="sim",
            contradiz_evidencia=False,
            justificativa="x",
        )


def test_rotulo_com_trace_que_deixa_campo_vazio_e_recusado():
    with pytest.raises(ValueError, match="sem resposta"):
        RotuloHumano(
            run_id="r",
            experiment_id="e",
            scenario_id="c",
            model_key="m",
            variant_id="base",
            env_seed="s001",
            sample_seed=11,
            amostra="melhoria",
            configuracao="com_trace",
            rotulador="antonio",
            seed_da_amostragem=42,
            rotulado_em=AGORA,
            causa_raiz_correta=True,
            mencionou_limitacao_relevante=True,
            responde_a_pergunta="sim",
            justificativa="x",
        )


def test_justificativa_vazia_e_recusada():
    """`METRICAS §4`: justificativa é obrigatória, e existe para tornar auditável."""
    with pytest.raises(ValueError, match="justificativa"):
        RotuloHumano(
            run_id="r",
            experiment_id="e",
            scenario_id="c",
            model_key="m",
            variant_id="base",
            env_seed="s001",
            sample_seed=11,
            amostra="estimativa",
            configuracao="cego",
            rotulador="antonio",
            seed_da_amostragem=42,
            rotulado_em=AGORA,
            causa_raiz_correta=True,
            mencionou_limitacao_relevante=True,
            responde_a_pergunta="sim",
            justificativa="   ",
        )


def test_o_rotulo_vira_n4humano_do_schema(tmp_path: Path, cenario: Cenario):
    """O rótulo que a CLI grava tem de caber em `ScoreRecord.n4` sem tradução à mão.

    `N4Humano` (`schema/trace.py`) é o que o κ da INS.6 consome. Ele não tem
    `justificativa` — tem `comentario`, opcional; o mapeamento é aqui, e não no notebook,
    porque um mapeamento no notebook é um mapeamento por rotulagem.
    """
    destino = _rotular_um(tmp_path, cenario, configuracao="cego", teclas=rotulo_cego())
    rotulo = RotuloHumano(**ler_jsonl(destino)[0])
    n4 = rotulo.para_n4humano()

    assert n4.rotulador == "antonio"
    assert n4.amostra == "estimativa"
    assert n4.contradiz_evidencia is None
    assert n4.comentario == rotulo.justificativa


def test_a_linha_gravada_identifica_a_run_inteira(tmp_path: Path, cenario: Cenario):
    """Sem as cinco coordenadas, o rótulo não se liga ao `ScoreRecord` correspondente."""
    destino = _rotular_um(tmp_path, cenario, configuracao="cego", teclas=rotulo_cego())
    linha = ler_jsonl(destino)[0]

    for campo in (
        "run_id",
        "experiment_id",
        "scenario_id",
        "model_key",
        "variant_id",
        "env_seed",
        "sample_seed",
        "amostra",
        "configuracao",
        "seed_da_amostragem",
        "rotulado_em",
    ):
        assert linha[campo] is not None, campo
    assert linha["env_seed"] == "s001"
    assert linha["seed_da_amostragem"] == SEED_DA_AMOSTRAGEM


# ---------------------------------------------------------------------------
# 5 · Retomada e ergonomia
# ---------------------------------------------------------------------------


def test_a_retomada_nao_re_rotula_o_que_ja_foi_feito(tmp_path: Path, cenario: Cenario):
    """São 35 rotulagens à mão: recomeçar do zero custa o recurso mais escasso do projeto."""
    run_dir = _montar_run_dir(
        tmp_path, cenarios=("cen_00",), modelos=("modelo-0",), seeds=(11, 23)
    )
    itens = amostrar(carregar_candidatos(run_dir), n_estimativa=2, n_melhoria=0)
    destino = tmp_path / "labels" / "humano_2026-08-24.jsonl"
    insumo_de = _insumo_fixo(run_dir, {"cen_00": cenario})

    _, escrever = escrita()
    primeiro = rodar_sessao(
        itens,
        insumo_de=insumo_de,
        configuracao="cego",
        rotulador="antonio",
        destino=destino,
        ja_rotulados=frozenset(),
        ler=Roteiro(*rotulo_cego()),  # o roteiro acaba depois de um caso: EOF interrompe
        escrever=escrever,
        agora=lambda: AGORA,
    )
    assert primeiro == 1

    feitos = run_ids_ja_rotulados(destino.parent)
    _, escrever = escrita()
    segundo = rodar_sessao(
        itens,
        insumo_de=insumo_de,
        configuracao="cego",
        rotulador="antonio",
        destino=destino,
        ja_rotulados=feitos,
        ler=Roteiro(*rotulo_cego()),
        escrever=escrever,
        agora=lambda: AGORA,
    )
    assert segundo == 1

    linhas = ler_jsonl(destino)
    assert len(linhas) == 2
    assert len({linha["run_id"] for linha in linhas}) == 2


def test_a_retomada_le_todos_os_arquivos_de_rotulo_e_nao_so_o_de_hoje(tmp_path: Path):
    """A sessão de ontem gravou em `humano_2026-08-23.jsonl`. Ela conta.

    O nome do arquivo carrega a data; a retomada que olhasse só o de hoje re-rotularia tudo
    o que foi feito ontem — e a duplicata entraria no κ como dois pares independentes.
    """
    labels = tmp_path / "labels"
    labels.mkdir()
    (labels / "humano_2026-08-23.jsonl").write_text(
        json.dumps({"run_id": "a--m--base--envs001--n11"}) + "\n", encoding="utf-8"
    )
    (labels / "humano_2026-08-24.jsonl").write_text(
        json.dumps({"run_id": "b--m--base--envs001--n11"}) + "\n", encoding="utf-8"
    )

    assert run_ids_ja_rotulados(labels) == {
        "a--m--base--envs001--n11",
        "b--m--base--envs001--n11",
    }


def test_pular_devolve_o_caso_ao_fim_da_fila(tmp_path: Path, cenario: Cenario):
    """Pular não descarta: o item volta, senão a amostra encolhe por conveniência."""
    run_dir = _montar_run_dir(
        tmp_path, cenarios=("cen_00",), modelos=("modelo-0",), seeds=(11, 23)
    )
    itens = amostrar(carregar_candidatos(run_dir), n_estimativa=2, n_melhoria=0)
    destino = tmp_path / "labels" / "humano_2026-08-24.jsonl"

    linhas, escrever = escrita()
    gravados = rodar_sessao(
        itens,
        insumo_de=_insumo_fixo(run_dir, {"cen_00": cenario}),
        configuracao="cego",
        rotulador="antonio",
        destino=destino,
        ja_rotulados=frozenset(),
        ler=Roteiro("p", *rotulo_cego(), *rotulo_cego()),
        escrever=escrever,
        agora=lambda: AGORA,
    )

    assert gravados == 2
    rotulados = [linha["run_id"] for linha in ler_jsonl(destino)]
    # O pulado foi para o fim e foi rotulado por último.
    assert rotulados == [itens[1].candidato.run_id, itens[0].candidato.run_id]


def test_entrada_invalida_repergunta_sem_perder_o_que_ja_foi_digitado(
    tmp_path: Path, cenario: Cenario
):
    """A validação não pode custar o trabalho já feito.

    Quem digitou três campos e errou o quarto responde o quarto de novo, não os quatro.
    """
    roteiro = Roteiro("r", "s", "s", "talvez", "sim", "a justificativa")
    destino = _rotular_um(
        tmp_path, cenario, configuracao="cego", teclas=None, roteiro=roteiro
    )
    linha = ler_jsonl(destino)[0]

    assert linha["causa_raiz_correta"] is True
    assert linha["mencionou_limitacao_relevante"] is True
    assert linha["responde_a_pergunta"] == "sim"
    # As duas primeiras perguntas foram feitas UMA vez; só a terceira se repetiu.
    perguntas = [pergunta for pergunta in roteiro.perguntas if "causa" in pergunta.lower()]
    assert len(perguntas) == 1


def test_o_progresso_aparece(tmp_path: Path, cenario: Cenario):
    """`12/35` na tela. É uma pessoa rotulando à mão; ela precisa saber onde está."""
    run_dir = _montar_run_dir(
        tmp_path, cenarios=("cen_00",), modelos=("modelo-0",), seeds=(11, 23)
    )
    itens = amostrar(carregar_candidatos(run_dir), n_estimativa=2, n_melhoria=0)

    linhas, escrever = escrita()
    rodar_sessao(
        itens,
        insumo_de=_insumo_fixo(run_dir, {"cen_00": cenario}),
        configuracao="cego",
        rotulador="antonio",
        destino=tmp_path / "labels" / "humano_2026-08-24.jsonl",
        ja_rotulados=frozenset(),
        ler=Roteiro(*rotulo_cego()),
        escrever=escrever,
        agora=lambda: AGORA,
    )

    assert any("1/2" in linha for linha in linhas)


def test_o_arquivo_e_append_only(tmp_path: Path, cenario: Cenario):
    """Duas sessões, dois rótulos, um arquivo — a segunda não reescreve a primeira."""
    destino = _rotular_um(tmp_path, cenario, configuracao="cego", teclas=rotulo_cego())
    tamanho = destino.stat().st_size

    run_dir = tmp_path / "runs" / "piloto_teste"
    itens = amostrar(carregar_candidatos(run_dir), n_estimativa=1, n_melhoria=0)
    _, escrever = escrita()
    rodar_sessao(
        itens,
        insumo_de=_insumo_fixo(run_dir, {"cen_00": cenario}),
        configuracao="com_trace",
        rotulador="antonio",
        destino=destino,
        ja_rotulados=frozenset(),
        ler=Roteiro(*rotulo_com_trace()),
        escrever=escrever,
        agora=lambda: AGORA,
    )

    assert destino.stat().st_size > tamanho
    assert len(ler_jsonl(destino)) == 2


# ---------------------------------------------------------------------------
# 6 · Leitura do disco e a CLI de ponta a ponta
# ---------------------------------------------------------------------------


def test_env_seed_sai_do_run_id_porque_o_run_start_nao_a_carrega():
    """Divergência real do schema: `RunStart` tem `seed` (a de amostra) e não `env_seed`.

    A `env_seed` só existe no `run_id` (`matriz.py::CelulaDaMatriz.run_id`) e em
    `manifest.json::celulas`. Derivá-la do nome evita depender do manifesto — que pode ter
    sido reescrito por uma re-execução parcial — e falha alto se o formato mudar.
    """
    assert env_seed_do_run_id("cen_04--qwen3-8b--base--envs004--n23") == "s004"
    with pytest.raises(ValueError, match="run_id"):
        env_seed_do_run_id("formato_estranho")


def test_candidato_sai_do_trace(tmp_path: Path):
    eventos = trace_completo(
        run_id_de("cen_00", "modelo-0", 23), scenario_id="cen_00", model_key="modelo-0"
    )
    candidato = candidato_de_trace(eventos, tmp_path / "t.jsonl")

    assert candidato.scenario_id == "cen_00"
    assert candidato.model_key == "modelo-0"
    assert candidato.variant_id == "base"
    assert candidato.env_seed == "s001"
    assert candidato.sample_seed == 11  # `RunStart.seed`, a de amostra


def test_carregar_candidatos_le_so_traces(tmp_path: Path):
    run_dir = _montar_run_dir(
        tmp_path, cenarios=("cen_00", "cen_01"), modelos=("modelo-0",), seeds=(11,)
    )
    (run_dir / "scores").mkdir()
    candidatos = carregar_candidatos(run_dir)

    assert len(candidatos) == 2
    assert {candidato.scenario_id for candidato in candidatos} == {"cen_00", "cen_01"}


def test_main_dry_run_descreve_a_amostra_sem_perguntar_nada(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    """`--dry-run` confere a amostra antes de gastar as horas — igual ao runner (T18)."""
    run_dir = _montar_run_dir(
        tmp_path, cenarios=("cen_00",), modelos=("modelo-0",), seeds=(11, 23)
    )
    codigo = main(
        [
            "--run-dir",
            str(run_dir),
            "--rotulador",
            "antonio",
            "--n-estimativa",
            "2",
            "--n-melhoria",
            "0",
            "--dry-run",
        ]
    )
    saida = capsys.readouterr().out

    assert codigo == 0
    assert "estimativa" in saida
    assert f"seed={SEED_DA_AMOSTRAGEM}" in saida


def test_main_falha_com_mensagem_quando_o_corpus_e_pequeno(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    run_dir = _montar_run_dir(
        tmp_path, cenarios=("cen_00",), modelos=("modelo-0",), seeds=(11,)
    )
    # `--labels-dir` explícito: sem ele o default é o `labels/` do repositório, e um teste
    # que escreve na árvore de verdade suja o diff de quem rodar a suíte.
    codigo = main([
        "--run-dir", str(run_dir), "--rotulador", "antonio", "--dry-run",
        "--labels-dir", str(tmp_path / "labels"),
    ])

    assert codigo == 2
    assert "amostra" in capsys.readouterr().err.lower()


# ---------------------------------------------------------------------------
# Auxiliares de montagem em disco
# ---------------------------------------------------------------------------


def _montar_run_dir(
    tmp_path: Path,
    *,
    cenarios: Sequence[str],
    modelos: Sequence[str],
    seeds: Sequence[int],
) -> Path:
    """Um `runs/<id>/` de mentira, com traces reais escritos em JSONL."""
    run_dir = tmp_path / "runs" / "piloto_teste"
    (run_dir / "traces").mkdir(parents=True, exist_ok=True)
    (run_dir / "blobs").mkdir(parents=True, exist_ok=True)
    for scenario_id in cenarios:
        for model_key in modelos:
            for sample_seed in seeds:
                run_id = run_id_de(scenario_id, model_key, sample_seed)
                eventos = trace_completo(
                    run_id, scenario_id=scenario_id, model_key=model_key
                )
                (run_dir / "traces" / f"{run_id}.jsonl").write_text(
                    "\n".join(
                        evento.model_dump_json(exclude_none=False) for evento in eventos
                    )
                    + "\n",
                    encoding="utf-8",
                )
    return run_dir


def _insumo_fixo(run_dir: Path, cenarios: dict[str, Cenario]) -> Any:
    return mod_cli.montador_de_insumo(run_dir, cenarios)


def _rotular_um(
    tmp_path: Path,
    cenario: Cenario,
    *,
    configuracao: str,
    teclas: list[str] | None,
    roteiro: Roteiro | None = None,
) -> Path:
    run_dir = _montar_run_dir(
        tmp_path, cenarios=("cen_00",), modelos=("modelo-0",), seeds=(11,)
    )
    itens = amostrar(carregar_candidatos(run_dir), n_estimativa=1, n_melhoria=0)
    destino = tmp_path / "labels" / "humano_2026-08-24.jsonl"
    _, escrever = escrita()
    rodar_sessao(
        itens,
        insumo_de=_insumo_fixo(run_dir, {"cen_00": cenario}),
        configuracao=configuracao,
        rotulador="antonio",
        destino=destino,
        ja_rotulados=frozenset(),
        ler=roteiro if roteiro is not None else Roteiro(*(teclas or [])),
        escrever=escrever,
        agora=lambda: AGORA,
    )
    return destino


def test_llm_call_no_trace_nao_atrapalha(tmp_path: Path):
    """Trace real tem `llm_call`; a amostragem ignora o que não é evidência nem resposta."""
    eventos = [
        *trace_completo(run_id_de("cen_00", "m"), scenario_id="cen_00", model_key="m"),
        evento(
            LLMCall,
            run_id_de("cen_00", "m"),
            model_key="m",
            prompt_sha="a" * 64,
            prompt_tokens=10,
            completion_tokens=2,
            completion_sha="b" * 64,
            latencia_ms=5,
            finish_reason="stop",
            parse_ok=True,
        ),
    ]
    assert sinais_de_incerteza(eventos) == SinaisDeIncerteza()


# ---------------------------------------------------------------------------
# 6 · A amostra congelada (X27)
# ---------------------------------------------------------------------------


def test_a_amostra_congelada_sobrevive_a_mudanca_no_universo():
    """O X27 na sua forma exata, e a razão de o congelamento existir.

    `amostrar` é determinística sobre o MESMO conjunto — e o conjunto é `runs/<id>/traces/`,
    que não é imutável: uma bateria retomada, um trace órfão refeito, uma execução
    acrescentada em leva. Sem congelar, a mesma seed produz outra amostra e a sessão seguinte
    continua num conjunto diferente do que começou — o κ sairia de 20 itens que nunca
    estiveram juntos.
    """
    candidatos = lote_de_candidatos()
    itens = amostrar(candidatos, n_estimativa=4, n_melhoria=4)
    registro = congelar(itens, candidatos, n_estimativa=4, n_melhoria=4)

    # Chega uma execução nova (o cenário de leva do X27) e o sorteio muda.
    acrescentado = [*candidatos, replace(candidatos[0], run_id=run_id_de("cen_09", "modelo-9", 7))]
    reamostrado = amostrar(acrescentado, n_estimativa=4, n_melhoria=4)
    assert [i.candidato.run_id for i in reamostrado] != [i.candidato.run_id for i in itens], (
        "o lote de teste não exercita o X27 — o universo mudou e a amostra não"
    )

    descongelada = descongelar(registro, acrescentado)

    assert [i.candidato.run_id for i in descongelada] == [i.candidato.run_id for i in itens]
    assert [i.amostra for i in descongelada] == [i.amostra for i in itens]
    assert [i.prioridade for i in descongelada] == [i.prioridade for i in itens]


def test_a_impressao_do_universo_denuncia_a_mudanca_sem_invalidar_a_amostra():
    """A impressão não serve para recusar a amostra — serve para a mudança ser DITA.

    Absorver em silêncio é o defeito; recusar seria trocá-lo por outro, porque a amostra
    congelada é justamente o que tem de sobreviver.
    """
    candidatos = lote_de_candidatos()
    registro = congelar(
        amostrar(candidatos, n_estimativa=4, n_melhoria=4),
        candidatos,
        n_estimativa=4,
        n_melhoria=4,
    )
    acrescentado = [*candidatos, replace(candidatos[0], run_id=run_id_de("cen_09", "modelo-9", 7))]

    assert impressao_do_universo(candidatos) == registro["universo_sha256"]
    assert impressao_do_universo(acrescentado) != registro["universo_sha256"]
    assert descongelar(registro, acrescentado), "a amostra tem de sobreviver à mudança"


def test_a_impressao_nao_depende_da_ordem_de_leitura_do_disco():
    """`glob` não promete ordem. Uma impressão sensível a ela acusaria mudança toda vez."""
    candidatos = lote_de_candidatos()
    assert impressao_do_universo(candidatos) == impressao_do_universo(candidatos[::-1])


def test_run_que_sumiu_do_disco_e_erro_e_nao_amostra_menor():
    """Pular em silêncio encolheria o n do κ sem aviso — o formato do X12 — e o README
    continuaria dizendo "κ sobre 20 itens"."""
    candidatos = lote_de_candidatos()
    registro = congelar(
        amostrar(candidatos, n_estimativa=4, n_melhoria=4),
        candidatos,
        n_estimativa=4,
        n_melhoria=4,
    )
    sorteados = {item["run_id"] for item in registro["itens"]}
    sobreviventes = [c for c in candidatos if c.run_id not in sorteados]

    with pytest.raises(AmostraCongeladaIncompativel, match="não existem mais"):
        descongelar(registro, sobreviventes)


def test_amostra_congelada_de_outra_versao_e_erro():
    """Formato futuro lido como se fosse o de hoje produziria uma amostra plausível e errada."""
    candidatos = lote_de_candidatos()
    registro = congelar(
        amostrar(candidatos, n_estimativa=4, n_melhoria=4),
        candidatos,
        n_estimativa=4,
        n_melhoria=4,
    )
    registro["versao"] = 99

    with pytest.raises(AmostraCongeladaIncompativel, match="versão"):
        descongelar(registro, candidatos)


def test_a_sessao_congela_na_primeira_vez_e_rele_nas_seguintes(tmp_path):
    """O comportamento que a CLI de fato exerce, ponta a ponta sobre o disco."""
    candidatos = lote_de_candidatos()
    caminho = tmp_path / "amostra_bateria.json"
    ditas: list[str] = []

    primeira = mod_cli.amostra_da_sessao(
        caminho, candidatos, n_estimativa=4, n_melhoria=4,
        seed=SEED_DA_AMOSTRAGEM, reamostrar=False, escrever=ditas.append,
    )
    assert caminho.exists()
    assert "congelada em" in " ".join(ditas)

    acrescentado = [*candidatos, replace(candidatos[0], run_id=run_id_de("cen_09", "modelo-9", 7))]
    ditas.clear()
    segunda = mod_cli.amostra_da_sessao(
        caminho, acrescentado, n_estimativa=4, n_melhoria=4,
        seed=SEED_DA_AMOSTRAGEM, reamostrar=False, escrever=ditas.append,
    )

    assert [i.candidato.run_id for i in segunda] == [i.candidato.run_id for i in primeira]
    assert "o universo mudou" in " ".join(ditas), "a mudança tem de ser dita em voz alta"


def test_reamostrar_de_proposito_sobrescreve(tmp_path):
    """A saída de emergência existe, e é explícita. A CLI ainda recusa usá-la depois que já
    há rótulo — trocar o conjunto embaixo deles produziria um arquivo com metade de cada
    amostra e nada nele diria isso."""
    candidatos = lote_de_candidatos()
    caminho = tmp_path / "amostra_bateria.json"

    mod_cli.amostra_da_sessao(
        caminho, candidatos, n_estimativa=4, n_melhoria=4,
        seed=SEED_DA_AMOSTRAGEM, reamostrar=False, escrever=lambda _: None,
    )
    acrescentado = [*candidatos, replace(candidatos[0], run_id=run_id_de("cen_09", "modelo-9", 7))]
    depois = mod_cli.amostra_da_sessao(
        caminho, acrescentado, n_estimativa=4, n_melhoria=4,
        seed=SEED_DA_AMOSTRAGEM, reamostrar=True, escrever=lambda _: None,
    )

    esperada = amostrar(acrescentado, n_estimativa=4, n_melhoria=4)
    assert [i.candidato.run_id for i in depois] == [i.candidato.run_id for i in esperada]


def test_dry_run_nao_congela(tmp_path):
    """Quem explora `--n-melhoria` na mão fixaria sem querer a amostra da primeira tentativa,
    e o congelamento viraria armadilha em vez da proteção que ele é."""
    caminho = tmp_path / "amostra_bateria.json"

    mod_cli.amostra_da_sessao(
        caminho, lote_de_candidatos(), n_estimativa=4, n_melhoria=4,
        seed=SEED_DA_AMOSTRAGEM, reamostrar=False, gravar=False, escrever=lambda _: None,
    )

    assert not caminho.exists()


# ---------------------------------------------------------------------------
# 9 · O protocolo — o conserto de 30/08
# ---------------------------------------------------------------------------


def test_o_protocolo_e_a_rubrica_que_o_judge_recebe_byte_a_byte():
    """A CLI não pode resumir a rubrica com as próprias palavras.

    Foi o que ela fez até 30/08: perguntava *"mencionou a limitação relevante?"* onde a
    rubrica manda um procedimento de dois passos cujo caso (iii) — o critério não exige
    declaração nenhuma — responde `true`. O humano lia a pergunta curta e respondia `false`;
    o judge seguia o procedimento e respondia `true`. O κ da INS.6 contava a diferença de
    ENUNCIADO como discordância entre duas leituras, e reportava como desacordo humano ×
    máquina o que era desacordo entre a CLI e a rubrica.

    Este teste fixa a única forma de isso não voltar: o texto é derivado, não copiado.
    """
    from tapieval.scoring.n3 import RUBRICA_PADRAO, perguntas_da_rubrica

    for configuracao in ("cego", "com_trace"):
        recorte = perguntas_da_rubrica(configuracao, RUBRICA_PADRAO)
        assert recorte in mod_cli.protocolo_de_rotulagem(configuracao, RUBRICA_PADRAO)


def test_o_protocolo_e_impresso_antes_da_primeira_pergunta(tmp_path: Path, cenario: Cenario):
    """Rubrica impressa depois do primeiro caso é rubrica que ninguém leu a tempo."""
    run_dir = _montar_run_dir(tmp_path, cenarios=("cen_00",), modelos=("modelo-0",), seeds=(11,))
    itens = amostrar(carregar_candidatos(run_dir), n_estimativa=1, n_melhoria=0)
    linhas, escrever = escrita()

    class RoteiroQueMarca(Roteiro):
        def __init__(self, *respostas: str):
            super().__init__(*respostas)
            self.escrito_antes: list[int] = []

        def __call__(self, pergunta: str) -> str:
            self.escrito_antes.append(len(linhas))
            return super().__call__(pergunta)

    roteiro = RoteiroQueMarca(*rotulo_cego())
    rodar_sessao(
        itens,
        insumo_de=_insumo_fixo(run_dir, {"cen_00": cenario}),
        configuracao="cego",
        rotulador="antonio",
        destino=tmp_path / "labels" / "humano_2026-08-30.jsonl",
        ja_rotulados=frozenset(),
        ler=roteiro,
        escrever=escrever,
        agora=lambda: AGORA,
    )

    antes_da_primeira = "\n".join(linhas[: roteiro.escrito_antes[0]])
    assert "As perguntas" in antes_da_primeira
    assert "não se aplica" in antes_da_primeira, (
        "o protocolo saiu sem a regra que causou o defeito: 'não se aplica' é `true`"
    )
