"""
Testes do runner (T18) — a matriz, a retomada, o isolamento entre células e o A7.

A REDE NÃO É ALCANÇADA. A API do parceiro é `httpx.MockTransport` e o modelo é um roteiro
(`ModeloDeRoteiro`), como na T16. O que muda aqui é o objeto sob teste: não é o agente, é o
processo que o executa 288 vezes sem deixar uma run contaminar a seguinte.

O CORPUS DESTES TESTES É DE MENTIRA, E ISSO É DE PROPÓSITO. Cenário de verdade tem gabarito,
seeds canônicas validadas contra a API e ativos do holdout; carregar `scenarios/` aqui
amarraria os testes do runner a mudanças de curadoria que não têm nada a ver com ele. Os dois
testes que precisam do corpus real dizem isso no nome.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from tapieval.mcp.gate import AutoApprove, AutoDeny, PolicyApprover
from tapieval.runner.cli import main
from tapieval.runner.manifesto import (
    Manifesto,
    RegistroDeRun,
    caminho_do_manifesto,
    ler_manifesto,
    motivo_nao_pontuavel_de,
)
from tapieval.runner.matriz import (
    Bateria,
    ErroDeBateria,
    carregar_bateria,
    carregar_corpus_executavel,
)
from tapieval.runner.runner import (
    ErroDeExecucao,
    construir_approver,
    indexar_prompts,
    resolver_prompt,
    rodar_bateria,
)
from tapieval.schema.reader import Defeito, read_trace, validar_trace
from tapieval.schema.trace import (
    LLMCall,
    ModelConfig,
    N1Deterministico,
    N2Programatico,
    RunEnd,
    RunError,
    RunStart,
    ScoreRecord,
    ScorerVersion,
    ToolCall,
)
from tapieval.schema.writer import TraceWriter
from tapieval.sut.llm import RespostaDoModelo
from tapieval.sut.variants import carregar_variantes

RAIZ = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# A API de mentira, que conta quantas vezes foi chamada
# ---------------------------------------------------------------------------

USUARIO = {
    "id": "usr_teste",
    "name": "Teste",
    "role": "tecnico",
    "permissions": ["read", "action_low", "action_high", "escalate"],
    "company_id": "comp_acme",
}

ATIVO = {
    "id": "asset_T1",
    "name": "Ativo T1",
    "company_id": "comp_acme",
    "criticality": "high",
    "sensor_status": "online",
    "machine_type": "pump",
    "points": [],
}


class ApiContada:
    """`httpx.MockTransport` que guarda a URL de cada requisição.

    O contador é o instrumento da prova 2 do enunciado: se o cache atravessasse células, a
    segunda run faria menos chamadas que a primeira — e o número mediria o vazamento, não o
    agente.
    """

    def __init__(self) -> None:
        self.requisicoes: list[str] = []
        self._trava = threading.Lock()

    @property
    def transporte(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._responder)

    def caminhos(self) -> list[str]:
        with self._trava:
            return list(self.requisicoes)

    def _responder(self, requisicao: httpx.Request) -> httpx.Response:
        with self._trava:
            self.requisicoes.append(f"{requisicao.method} {requisicao.url.path}")

        caminho = requisicao.url.path
        if caminho == "/users/me":
            return httpx.Response(200, json=USUARIO)
        if requisicao.method in ("POST", "PATCH"):
            return httpx.Response(200, json={"accepted": True, "action_id": "act_1"})
        if caminho.startswith("/assets/") and caminho.count("/") == 2:
            return httpx.Response(
                200, json={"mode": "complete", "notes": None, "data": ATIVO}
            )
        return httpx.Response(
            200,
            json={"mode": "complete", "notes": None, "data": {"id": "obj_1", "items": []}},
        )


MODELO = ModelConfig(
    model_id="modelo-de-teste",
    served_by="lmstudio",
    quantization="q4",
    temperature=0.0,
    max_tokens=512,
    structured_output="json_schema",
    context_window=8192,
)


class ModeloDeRoteiro:
    """`Inferencia` de roteiro fixo. Uma instância por célula, como na bateria de verdade."""

    def __init__(
        self,
        passos: Sequence[Any],
        modelo: ModelConfig = MODELO,
        *,
        antes: Any = None,
    ) -> None:
        self.modelo = modelo
        self.passos = list(passos)
        self.chamadas = 0
        self._antes = antes

    def completar(
        self, mensagens: Sequence[Mapping[str, str]], esquema: Mapping[str, Any]
    ) -> RespostaDoModelo:
        self.chamadas += 1
        if self._antes is not None:
            self._antes()
        passo = self.passos.pop(0) if self.passos else _RESPOSTA_FINAL
        texto = json.dumps(passo, ensure_ascii=False)

        from tapieval.sut.llm import _validar  # noqa: PLC0415 — é o parser real

        conteudo, erro = _validar(texto, esquema)
        return RespostaDoModelo(
            texto=texto,
            conteudo=conteudo,
            parse_ok=erro is None,
            parse_erro=erro,
            prompt_tokens=100,
            completion_tokens=20,
            finish_reason="stop",
            latencia_ms=1,
        )


def passo_de_acao(tool: str, args: dict[str, Any], modo: str = "investigar") -> dict[str, Any]:
    return {
        "modo": modo,
        "pensamento": f"preciso de {tool}",
        "acao": {"tool": tool, "args": args},
        "resposta": None,
        "decisao": None,
        "prioridade_escalonamento": None,
    }


def passo_de_resposta(texto: str, *, decisao: str = "orientar") -> dict[str, Any]:
    return {
        "modo": "investigar",
        "pensamento": "tenho o suficiente",
        "acao": None,
        "resposta": {"texto": texto, "citacoes": [], "perguntar_de_volta": False},
        "decisao": decisao,
        "prioridade_escalonamento": None,
    }


_RESPOSTA_FINAL = passo_de_resposta("terminei")

ROTEIRO_PADRAO = [
    passo_de_acao("list_analyses", {"asset_id": "asset_T1"}),
    passo_de_resposta("as análises não mostram desvio"),
]


# ---------------------------------------------------------------------------
# Corpus e bateria de mentira
# ---------------------------------------------------------------------------


def escrever_cenario(
    diretorio: Path,
    identificador: str,
    *,
    split: str = "test",
    status: str | None = None,
    env_seed: str = "s001",
) -> Path:
    corpo: dict[str, Any] = {
        "id": identificador,
        "split": split,
        "natureza": "dado_dependente",
        "solicitacao": f"o que houve com o ativo em {identificador}?",
        "user_id": "usr_teste",
        "asset_id": "asset_T1",
        "ambiente": {"env_seed": env_seed},
    }
    if status is not None:
        corpo["status"] = status
        corpo["justificativa_inviabilidade"] = "declarado morto na curadoria"

    diretorio.mkdir(parents=True, exist_ok=True)
    caminho = diretorio / f"{identificador}.yaml"
    caminho.write_text(yaml.safe_dump(corpo, allow_unicode=True), encoding="utf-8")
    return caminho


AUSENTE = object()


def escrever_bateria(
    tmp_path: Path,
    *,
    cenarios: Any = AUSENTE,
    modelos: Any = AUSENTE,
    variantes: Any = AUSENTE,
    sample_seeds: Any = AUSENTE,
    **extras: Any,
) -> Path:
    corpo: dict[str, Any] = {
        "experiment_id": "exp_teste",
        "saida": str(tmp_path / "runs"),
        "cenarios": {"split": "test"} if cenarios is AUSENTE else cenarios,
        "modelos": {"m1": _campos_do_modelo()} if modelos is AUSENTE else modelos,
        "variantes": ["base"] if variantes is AUSENTE else variantes,
        "sample_seeds": [1] if sample_seeds is AUSENTE else sample_seeds,
        "paralelismo": 1,
    }
    corpo.update(extras)
    tmp_path.mkdir(parents=True, exist_ok=True)
    caminho = tmp_path / "bateria.yaml"
    caminho.write_text(yaml.safe_dump(corpo, allow_unicode=True), encoding="utf-8")
    return caminho


def _campos_do_modelo(**trocas: Any) -> dict[str, Any]:
    campos = {
        "model_id": "modelo-de-teste",
        "served_by": "lmstudio",
        "quantization": "q4",
        "temperature": 0.0,
        "max_tokens": 512,
        "structured_output": "json_schema",
        "context_window": 8192,
    }
    campos.update(trocas)
    return campos


@pytest.fixture
def corpus(tmp_path: Path) -> dict[str, Any]:
    diretorio = tmp_path / "cenarios"
    escrever_cenario(diretorio, "cen_a")
    escrever_cenario(diretorio, "cen_b", env_seed="s002")
    escrever_cenario(diretorio, "cen_morto", status="inviavel")
    escrever_cenario(diretorio, "cen_dev", split="dev")
    return carregar_corpus_executavel(diretorio)


@pytest.fixture
def variantes() -> dict[str, Any]:
    return carregar_variantes()


def montar(
    tmp_path: Path, corpus: dict[str, Any], catalogo: dict[str, Any], **kwargs: Any
) -> Bateria:
    caminho = escrever_bateria(tmp_path, **kwargs)
    return carregar_bateria(caminho, corpus=corpus, variantes_disponiveis=catalogo)


def rodar(
    bateria: Bateria,
    api: ApiContada,
    *,
    roteiro: Sequence[Any] = ROTEIRO_PADRAO,
    modelos: dict[str, ModeloDeRoteiro] | None = None,
    fabrica: Any = None,
    **kwargs: Any,
) -> Manifesto:
    """Roda a bateria com um `ModeloDeRoteiro` novo por célula (como a bateria de verdade)."""
    registro: dict[str, ModeloDeRoteiro] = modelos if modelos is not None else {}

    def padrao(celula: Any) -> ModeloDeRoteiro:
        modelo = ModeloDeRoteiro(list(roteiro))
        registro[celula.run_id] = modelo
        return modelo

    return rodar_bateria(
        bateria,
        fabrica_de_inferencia=fabrica or padrao,
        transporte_http=api.transporte,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 1 · Um trace por célula da matriz
# ---------------------------------------------------------------------------


def test_gera_um_trace_por_celula_da_matriz(tmp_path, corpus, variantes) -> None:
    bateria = montar(
        tmp_path,
        corpus,
        variantes,
        cenarios={"ids": ["cen_a", "cen_b"]},
        variantes=["base", "MUT3"],
        sample_seeds=[1, 2],
    )
    manifesto = rodar(bateria, ApiContada())

    assert len(manifesto.celulas) == 2 * 1 * 2 * 2
    assert len(manifesto.runs) == 8
    assert manifesto.faltantes() == ()

    traces = sorted(p.stem for p in (bateria.diretorio / "traces").glob("*.jsonl"))
    assert traces == sorted(c.run_id for c in manifesto.celulas)


def test_o_run_id_nomeia_as_cinco_coordenadas_da_celula(tmp_path, corpus, variantes) -> None:
    """Derivado, não sorteado: o nome do arquivo É a chave da célula, e por isso disco e
    manifesto podem ser conferidos um contra o outro depois de uma bateria interrompida."""
    bateria = montar(
        tmp_path, corpus, variantes, cenarios={"ids": ["cen_b"]}, sample_seeds=[7]
    )
    (celula,) = bateria.expandir()
    assert celula.run_id == "cen_b--m1--base--envs002--n7"


def test_cada_run_declara_no_run_start_a_celula_que_o_manifesto_diz(
    tmp_path, corpus, variantes
) -> None:
    bateria = montar(
        tmp_path, corpus, variantes, cenarios={"ids": ["cen_a"]}, sample_seeds=[3]
    )
    manifesto = rodar(bateria, ApiContada())
    (coordenada,) = manifesto.celulas

    eventos = read_trace(bateria.diretorio / "traces" / f"{coordenada.run_id}.jsonl")
    inicio = eventos[0]
    assert isinstance(inicio, RunStart)
    assert inicio.seq == 1, "o RunStart tem de ser o começo da run (derivar_estado depende)"
    assert (inicio.scenario_id, inicio.model_key, inicio.variant_id, inicio.seed) == (
        coordenada.scenario_id,
        coordenada.model_key,
        coordenada.variant_id,
        coordenada.sample_seed,
    )
    assert inicio.env_mode == "live"
    assert inicio.cassette_id is None, "a T6 foi cortada; não existe cassete para citar"


# ---------------------------------------------------------------------------
# 2 · Cada run tem servidor próprio
# ---------------------------------------------------------------------------


def test_o_cache_de_uma_run_nao_serve_a_outra(tmp_path, corpus, variantes) -> None:
    """As duas células fazem o MESMO número de chamadas HTTP.

    É o vazamento mais silencioso do plano: cache compartilhado faria a segunda parecer mais
    eficiente com o mesmo cenário, e o artefato do instrumento entraria no resultado como
    ganho de eficiência do agente.
    """
    uma = montar(
        tmp_path, corpus, variantes, cenarios={"ids": ["cen_a"]}, sample_seeds=[1]
    )
    api_uma = ApiContada()
    rodar(uma, api_uma)
    chamadas_de_uma_run = len(api_uma.caminhos())
    assert chamadas_de_uma_run > 0

    duas = montar(
        tmp_path / "b", corpus, variantes, cenarios={"ids": ["cen_a"]}, sample_seeds=[1, 2]
    )
    api_duas = ApiContada()
    rodar(duas, api_duas)

    assert len(api_duas.caminhos()) == 2 * chamadas_de_uma_run


def test_as_chaves_de_idempotencia_nao_atravessam_celulas(tmp_path, corpus, variantes) -> None:
    """A mesma ação irreversível, na mesma célula repetida, executa nas duas runs.

    Chave compartilhada faria a segunda run ser barrada por "já fiz isso" — e a bateria
    mediria "o agente não agiu" onde o instrumento é que lembrava demais.
    """
    roteiro = [
        passo_de_acao(
            "escalate_case",
            {"case_id": "case_1", "justification": "ativo crítico e evidência inconclusiva"},
            modo="executar",
        ),
        passo_de_resposta("escalei", decisao="escalar"),
    ]
    bateria = montar(
        tmp_path,
        corpus,
        variantes,
        cenarios={"ids": ["cen_a"]},
        sample_seeds=[1, 2],
        approver="auto_approve",
    )
    api = ApiContada()
    rodar(bateria, api, roteiro=roteiro)

    escalonamentos = [c for c in api.caminhos() if c.startswith("POST") and "escalate" in c]
    assert len(escalonamentos) == 2


# ---------------------------------------------------------------------------
# 3 · Retomada
# ---------------------------------------------------------------------------


def test_retoma_sem_repetir_o_que_ja_rodou(tmp_path, corpus, variantes) -> None:
    bateria = montar(
        tmp_path, corpus, variantes, cenarios={"ids": ["cen_a", "cen_b"]}, sample_seeds=[1]
    )
    api = ApiContada()
    primeira = rodar(bateria, api)
    concluidas = {run_id: r.concluida_em for run_id, r in primeira.runs.items()}
    chamadas_da_primeira = len(api.caminhos())

    segunda_api = ApiContada()
    segunda = rodar(bateria, segunda_api)

    assert segunda_api.caminhos() == [], "retomada não pode reexecutar célula já registrada"
    assert {r.run_id: r.concluida_em for r in segunda.runs.values()} == concluidas
    assert chamadas_da_primeira > 0


def test_retomada_roda_so_o_que_faltou(tmp_path, corpus, variantes) -> None:
    """Meia bateria feita, meia por fazer: a segunda invocação fecha só o buraco."""
    metade = montar(
        tmp_path, corpus, variantes, cenarios={"ids": ["cen_a"]}, sample_seeds=[1]
    )
    rodar(metade, ApiContada())

    inteira = montar(
        tmp_path, corpus, variantes, cenarios={"ids": ["cen_a"]}, sample_seeds=[1, 2]
    )
    api = ApiContada()
    executados: dict[str, ModeloDeRoteiro] = {}
    manifesto = rodar(inteira, api, modelos=executados)

    assert set(executados) == {"cen_a--m1--base--envs001--n2"}
    assert len(manifesto.runs) == 2
    assert manifesto.faltantes() == ()


def test_do_zero_reexecuta_tudo(tmp_path, corpus, variantes) -> None:
    bateria = montar(
        tmp_path, corpus, variantes, cenarios={"ids": ["cen_a"]}, sample_seeds=[1]
    )
    rodar(bateria, ApiContada())

    executados: dict[str, ModeloDeRoteiro] = {}
    rodar(bateria, ApiContada(), modelos=executados, retomar=False)
    assert set(executados) == {"cen_a--m1--base--envs001--n1"}


def test_reexecucao_apaga_o_trace_antigo_em_vez_de_appendar(
    tmp_path, corpus, variantes
) -> None:
    """`TraceWriter` abre em modo append. Reaproveitar o arquivo somaria duas runs no mesmo
    `.jsonl`, com dois `seq=1` — e o A7 acusaria `seq_duplicado` numa run que rodou bem."""
    bateria = montar(
        tmp_path, corpus, variantes, cenarios={"ids": ["cen_a"]}, sample_seeds=[1]
    )
    rodar(bateria, ApiContada())
    (celula,) = bateria.expandir()
    trace = bateria.diretorio / "traces" / f"{celula.run_id}.jsonl"
    linhas_da_primeira = len(trace.read_text(encoding="utf-8").splitlines())

    manifesto = rodar(bateria, ApiContada(), retomar=False)

    assert len(trace.read_text(encoding="utf-8").splitlines()) == linhas_da_primeira
    assert validar_trace(read_trace(trace)) == []
    assert manifesto.runs[celula.run_id].valida is True


# ---------------------------------------------------------------------------
# 4 · Erro numa run não derruba a bateria
# ---------------------------------------------------------------------------


def test_erro_numa_run_nao_derruba_a_bateria(tmp_path, corpus, variantes) -> None:
    """287 células boas perdidas por uma péssima é o custo errado — e a péssima continua
    visível como `falha_do_instrumento`, com a exceção escrita."""
    bateria = montar(
        tmp_path,
        corpus,
        variantes,
        cenarios={"ids": ["cen_a", "cen_b"]},
        sample_seeds=[1, 2],
    )

    def fabrica(celula: Any) -> ModeloDeRoteiro:
        if celula.cenario.id == "cen_b" and celula.sample_seed == 1:
            def explodir() -> None:
                raise httpx.ConnectError("conexão recusada")

            return ModeloDeRoteiro(list(ROTEIRO_PADRAO), antes=explodir)
        return ModeloDeRoteiro(list(ROTEIRO_PADRAO))

    manifesto = rodar(bateria, ApiContada(), fabrica=fabrica)

    assert len(manifesto.runs) == 4, "as outras três células rodaram"
    quebrada = manifesto.runs["cen_b--m1--base--envs002--n1"]
    assert quebrada.status == "falha_do_instrumento"
    assert "ConnectError" in (quebrada.erro or "")
    assert [r.status for r in manifesto.runs.values()].count("ok") == 3


def test_falha_do_instrumento_deixa_o_motivo_no_trace_e_no_manifesto(
    tmp_path, corpus, variantes
) -> None:
    """`falha_do_instrumento` é do manifesto; o trace só tem os quatro status do `RunEnd`.

    A distinção existe porque as duas exigem coisas opostas do operador: `error` é resultado
    do experimento e não se repete, defeito nosso é para consertar e refazer.
    """
    bateria = montar(
        tmp_path, corpus, variantes, cenarios={"ids": ["cen_a"]}, sample_seeds=[1]
    )

    def fabrica(_celula: Any) -> ModeloDeRoteiro:
        def explodir() -> None:
            raise RuntimeError("o servidor de inferência caiu")

        return ModeloDeRoteiro(list(ROTEIRO_PADRAO), antes=explodir)

    manifesto = rodar(bateria, ApiContada(), fabrica=fabrica)
    (registro,) = manifesto.runs.values()

    eventos = read_trace(bateria.diretorio / registro.trace)
    erros = [e for e in eventos if isinstance(e, RunError)]
    fins = [e for e in eventos if isinstance(e, RunEnd)]
    assert [e.onde for e in erros] == ["harness"]
    assert fins[0].status == "error"
    assert registro.status == "falha_do_instrumento"
    assert validar_trace(eventos) == [], "trace de run quebrada continua estruturalmente são"


def test_a_run_que_falhou_e_refeita_na_retomada(tmp_path, corpus, variantes) -> None:
    """`falha_do_instrumento` não é resultado: consertado o instrumento, a retomada a refaz."""
    bateria = montar(
        tmp_path, corpus, variantes, cenarios={"ids": ["cen_a"]}, sample_seeds=[1]
    )

    def quebrado(_celula: Any) -> ModeloDeRoteiro:
        def explodir() -> None:
            raise RuntimeError("caiu")

        return ModeloDeRoteiro(list(ROTEIRO_PADRAO), antes=explodir)

    rodar(bateria, ApiContada(), fabrica=quebrado)

    executados: dict[str, ModeloDeRoteiro] = {}
    manifesto = rodar(bateria, ApiContada(), modelos=executados)
    assert set(executados) == {"cen_a--m1--base--envs001--n1"}
    assert next(iter(manifesto.runs.values())).status == "ok"


def test_run_que_encerra_por_orcamento_e_resultado_e_nao_se_repete(
    tmp_path, corpus, variantes
) -> None:
    """`budget_exceeded` é comportamento do modelo, não defeito nosso — a retomada o mantém."""
    bateria = montar(
        tmp_path,
        corpus,
        variantes,
        cenarios={"ids": ["cen_a"]},
        variantes=["MUT3"],
        sample_seeds=[1],
    )
    roteiro = [passo_de_acao("list_analyses", {"asset_id": "asset_T1"})] * 8
    manifesto = rodar(bateria, ApiContada(), roteiro=roteiro)
    (registro,) = manifesto.runs.values()
    assert registro.status == "budget_exceeded"
    assert registro.e_resultado is True

    executados: dict[str, ModeloDeRoteiro] = {}
    rodar(bateria, ApiContada(), modelos=executados)
    assert executados == {}


# ---------------------------------------------------------------------------
# A dívida do bloco 8 — `validar_trace` chamada, e o motivo no manifesto
# ---------------------------------------------------------------------------


def test_lacuna_de_seq_vira_run_invalida_com_o_motivo_no_manifesto(
    tmp_path, corpus, variantes, monkeypatch
) -> None:
    """Um evento que some entre a emissão e o disco invalida a run — e diz por quê.

    O `monkeypatch` simula exatamente o defeito que a decisão 9 de `ARQUITETURA §5` descreve:
    o evento foi emitido e não chegou ao arquivo. Se o runner validasse a lista em memória do
    `ObservadorDeTrace` (que é mais barato), ele atestaria a integridade de uma coisa que
    nenhum scorer lê.
    """
    emitir_original = TraceWriter.emit

    def emitir_com_buraco(self: TraceWriter, evento: Any) -> None:
        if evento.seq == 3:
            return
        emitir_original(self, evento)

    monkeypatch.setattr(TraceWriter, "emit", emitir_com_buraco)

    bateria = montar(
        tmp_path, corpus, variantes, cenarios={"ids": ["cen_a"]}, sample_seeds=[1]
    )
    manifesto = rodar(bateria, ApiContada())
    (registro,) = manifesto.runs.values()

    assert registro.valida is False
    assert registro.status == "ok", "o agente foi bem; quem falhou foi o transporte do trace"
    assert any("lacuna_de_seq" in defeito for defeito in registro.defeitos)
    assert "[3]" in registro.defeitos[0]
    assert registro.motivo_nao_pontuavel and "A7" in registro.motivo_nao_pontuavel
    assert manifesto.invalidas() == (registro,)


def test_run_invalida_nao_e_apagada_e_continua_no_disco(
    tmp_path, corpus, variantes, monkeypatch
) -> None:
    """Descartar em silêncio suporia que runs quebram de forma aleatória. Elas não quebram:
    quebram pelo mesmo motivo, na mesma célula da matriz."""
    emitir_original = TraceWriter.emit
    monkeypatch.setattr(
        TraceWriter,
        "emit",
        lambda self, evento: None if evento.seq == 2 else emitir_original(self, evento),
    )
    bateria = montar(
        tmp_path, corpus, variantes, cenarios={"ids": ["cen_a"]}, sample_seeds=[1]
    )
    manifesto = rodar(bateria, ApiContada())
    (registro,) = manifesto.runs.values()

    assert (bateria.diretorio / registro.trace).exists()
    assert registro.run_id in {c.run_id for c in manifesto.celulas}
    assert manifesto.faltantes() == (), "célula inválida é célula medida-e-defeituosa"


def test_o_motivo_do_manifesto_e_o_que_o_score_record_consome(
    tmp_path, corpus, variantes, monkeypatch
) -> None:
    """Fecha o laço com o A10: o motivo sai daqui na forma que o `ScoreRecord` aceita, e o
    validador dele recusa a única combinação perigosa — fora do denominador **e** aprovada."""
    emitir_original = TraceWriter.emit
    monkeypatch.setattr(
        TraceWriter,
        "emit",
        lambda self, evento: None if evento.seq == 3 else emitir_original(self, evento),
    )
    bateria = montar(
        tmp_path, corpus, variantes, cenarios={"ids": ["cen_a"]}, sample_seeds=[1]
    )
    manifesto = rodar(bateria, ApiContada())
    (registro,) = manifesto.runs.values()
    (coordenada,) = manifesto.celulas

    def score(**trocas: Any) -> ScoreRecord:
        campos: dict[str, Any] = {
            "run_id": registro.run_id,
            "experiment_id": manifesto.experiment_id,
            "scenario_id": coordenada.scenario_id,
            "split": coordenada.split,
            "variant_id": coordenada.variant_id,
            "model_key": coordenada.model_key,
            "seed": coordenada.sample_seed,
            "scorer": ScorerVersion(
                scorer_version="v1", sha256="0" * 64, congelado_em=datetime.now(UTC)
            ),
            "calculado_em": datetime.now(UTC),
            "n1": _n1_vazia(),
            "n2": _n2_vazia(),
            "score_final": 0.0,
            "sucesso_binario": False,
            "pontuavel": False,
            "motivo_nao_pontuavel": registro.motivo_nao_pontuavel,
        }
        campos.update(trocas)
        return ScoreRecord(**campos)

    assert score().pontuavel is False
    with pytest.raises(ValueError, match="não pontuável"):
        score(sucesso_binario=True)


def _n1_vazia() -> N1Deterministico:
    """A N1 de uma run que não pôde ser medida. `decisao_prevista=None` de propósito: é o
    outro caminho para não pontuável, e ele não vem do runner."""
    return N1Deterministico(
        tools_esperadas_chamadas=[],
        tools_faltantes=[],
        tools_extras=[],
        tool_f1=0.0,
        tool_f1_liquido=0.0,
        args_corretos=0,
        args_avaliados=0,
        args_acc=0.0,
        decisao_prevista=None,
        decisao_esperada=None,
        decisao_correta=False,
        acao_indevida=False,
        gate_respeitado=True,
        citacoes_validas=True,
    )


def _n2_vazia() -> N2Programatico:
    return N2Programatico(
        n_iteracoes=0,
        n_tool_calls=0,
        n_redundantes=0,
        ordem_kendall_tau=None,
        cobertura_evidencial=0.0,
        estourou_budget=False,
        parse_failures=0,
    )


def test_trace_ausente_cai_em_sem_run_start_e_nao_numa_quarta_categoria() -> None:
    assert [d.tipo for d in validar_trace([])] == ["sem_run_start"]
    assert motivo_nao_pontuavel_de([]) is None
    assert motivo_nao_pontuavel_de(
        [Defeito("lacuna_de_seq", "faltam os `seq` [3]"), Defeito("seq_duplicado", "[7]")]
    ) == ("trace inválido (A7): lacuna_de_seq: faltam os `seq` [3]; seq_duplicado: [7]")


def test_o_registro_recusa_invalida_sem_motivo() -> None:
    """A mesma invariante do `ScoreRecord`, um degrau antes: veredito sem motivo não passa."""
    comuns = {
        "run_id": "r",
        "status": "ok",
        "duracao_ms": 1,
        "concluida_em": datetime.now(UTC),
        "trace": "traces/r.jsonl",
    }
    with pytest.raises(ValueError, match="defeitos"):
        RegistroDeRun(**comuns, valida=False)
    with pytest.raises(ValueError, match="motivo_nao_pontuavel"):
        RegistroDeRun(**comuns, valida=False, defeitos=("lacuna_de_seq: x",))
    with pytest.raises(ValueError, match="válida"):
        RegistroDeRun(**comuns, valida=True, defeitos=("lacuna_de_seq: x",))
    with pytest.raises(ValueError, match="erro"):
        RegistroDeRun(**{**comuns, "status": "falha_do_instrumento"})


# ---------------------------------------------------------------------------
# X12 — o cenário inviável não roda
# ---------------------------------------------------------------------------


def test_cenario_inviavel_sai_do_split_e_o_motivo_fica_no_manifesto(
    tmp_path, corpus, variantes
) -> None:
    """Um `glob("scenarios/*.yaml")` sem filtro roda cenário declarado morto e envenena as
    contagens sem nenhum teste acusar. O filtro está no runner, e a exclusão é explícita."""
    bateria = montar(tmp_path, corpus, variantes, cenarios={"split": "test"})

    assert [c.id for c in bateria.cenarios] == ["cen_a", "cen_b"]
    assert [e.cenario_id for e in bateria.excluidos] == ["cen_morto"]
    assert "declarado morto" in bateria.excluidos[0].motivo

    manifesto = rodar(bateria, ApiContada())
    assert all(c.scenario_id != "cen_morto" for c in manifesto.celulas)
    assert [e.cenario_id for e in manifesto.cenarios_excluidos] == ["cen_morto"]
    assert not list((bateria.diretorio / "traces").glob("cen_morto*"))


def test_nomear_cenario_inviavel_em_ids_e_erro(tmp_path, corpus, variantes) -> None:
    """Assimetria deliberada com o `split`: quem nomeia o cenário sabe qual quer, e devolver
    uma bateria menor do que a pedida esconderia um erro de configuração."""
    with pytest.raises(ErroDeBateria, match="inviável"):
        montar(tmp_path, corpus, variantes, cenarios={"ids": ["cen_a", "cen_morto"]})


def test_split_seleciona_so_o_split_pedido(tmp_path, corpus, variantes) -> None:
    bateria = montar(tmp_path, corpus, variantes, cenarios={"split": "dev"})
    assert [c.id for c in bateria.cenarios] == ["cen_dev"]


def test_o_corpus_real_carrega_e_nenhum_inviavel_escapa_para_a_matriz() -> None:
    """Teste de contrato contra `scenarios/` de verdade: 24 vivos, 6 dev / 18 test.

    Se um cenário for declarado inviável (ou um novo entrar), este teste fica vermelho junto
    com `SPLIT_ESPERADO` de `validar_cenarios.py` — que é o ponto: o denominador das baterias
    mudou, e nada pode mudá-lo em silêncio.
    """
    real = carregar_corpus_executavel()
    vivos = [c for c in real.values() if not c.inviavel]

    assert len(real) == 24
    assert len(vivos) == 24
    assert sorted(c.split for c in vivos).count("dev") == 6
    assert sorted(c.split for c in vivos).count("test") == 18
    assert all(c.env_seed for c in vivos), "toda célula precisa declarar o mundo em que roda"


# ---------------------------------------------------------------------------
# O manifesto declara a matriz inteira
# ---------------------------------------------------------------------------


def test_o_manifesto_guarda_modelconfig_e_variantconfig_inteiras(
    tmp_path, corpus, variantes
) -> None:
    """Ids não bastam: é o manifesto que permite conferir, depois, que o `prompt_sha` da
    coluna é o do prompt que rodou e que a `ModelConfig` do eixo x do custo é a que o
    servidor recebeu."""
    bateria = montar(
        tmp_path,
        corpus,
        variantes,
        cenarios={"ids": ["cen_a"]},
        variantes=["base", "MUT4"],
        sample_seeds=[1],
    )
    manifesto = rodar(bateria, ApiContada())

    assert manifesto.modelos["m1"].model_id == "modelo-de-teste"
    assert manifesto.variantes["MUT4"].prompt_sha == variantes["MUT4"].prompt_sha
    assert manifesto.variantes["MUT4"].mutante is True

    relido = ler_manifesto(bateria.diretorio)
    assert relido is not None
    assert relido.model_dump() == manifesto.model_dump()


def test_a_modelconfig_do_manifesto_nao_carrega_a_sample_seed(
    tmp_path, corpus, variantes
) -> None:
    """Aqui é o modelo, lá é a repetição. Fixar a seed no manifesto colapsaria o eixo."""
    bateria = montar(
        tmp_path, corpus, variantes, cenarios={"ids": ["cen_a"]}, sample_seeds=[1, 2]
    )
    manifesto = rodar(bateria, ApiContada())
    assert manifesto.modelos["m1"].seed is None
    assert sorted(c.sample_seed for c in manifesto.celulas) == [1, 2]


def test_honra_seed_falso_zera_a_seed_da_modelconfig_e_preserva_a_do_run_start(
    tmp_path, corpus, variantes
) -> None:
    """Achado da T0b: o 8B não honra `seed`. `ModelConfig.seed=None` declara a limitação;
    `RunStart.seed` continua registrando a repetição, porque ela aconteceu."""
    bateria = montar(
        tmp_path,
        corpus,
        variantes,
        cenarios={"ids": ["cen_a"]},
        modelos={"m8b": _campos_do_modelo(honra_seed=False)},
        sample_seeds=[5],
    )
    (celula,) = bateria.expandir()
    assert celula.modelo.para(5).seed is None

    manifesto = rodar(bateria, ApiContada())
    (registro,) = manifesto.runs.values()
    eventos = read_trace(bateria.diretorio / registro.trace)
    assert isinstance(eventos[0], RunStart)
    assert eventos[0].seed == 5


def test_o_manifesto_e_gravado_a_cada_run_e_nao_no_fim(tmp_path, corpus, variantes) -> None:
    """Uma bateria morta às 4h da manhã perde a run em voo, não a noite inteira."""
    bateria = montar(
        tmp_path, corpus, variantes, cenarios={"ids": ["cen_a", "cen_b"]}, sample_seeds=[1]
    )
    vistos: list[int] = []

    def espiar(_coordenada: Any, _registro: Any) -> None:
        do_disco = ler_manifesto(bateria.diretorio)
        assert do_disco is not None
        vistos.append(len(do_disco.runs))

    rodar(bateria, ApiContada(), ao_concluir=espiar)
    assert vistos == [1, 2]


def test_manifesto_de_outro_experimento_no_mesmo_diretorio_e_erro(
    tmp_path, corpus, variantes
) -> None:
    """Copiar um `runs/<exp>/` por cima de outro misturaria traces de matrizes diferentes na
    mesma tabela — e ninguém descobriria pelas contagens, que continuariam batendo."""
    primeira = montar(tmp_path, corpus, variantes, cenarios={"ids": ["cen_a"]})
    rodar(primeira, ApiContada())

    outra = montar(
        tmp_path / "outro",
        corpus,
        variantes,
        cenarios={"ids": ["cen_a"]},
        experiment_id="exp_outro",
    )
    outra.diretorio.mkdir(parents=True, exist_ok=True)
    caminho_do_manifesto(outra.diretorio).write_text(
        caminho_do_manifesto(primeira.diretorio).read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    with pytest.raises(ErroDeExecucao, match="misturariam"):
        rodar(outra, ApiContada())


# ---------------------------------------------------------------------------
# O prompt da variante, e o approver que a espelha
# ---------------------------------------------------------------------------


def test_o_prompt_da_variante_e_achado_pelo_hash_que_ela_declara(variantes) -> None:
    indice = indexar_prompts()
    assert resolver_prompt(variantes["MUT4"], indice).find("baseline") >= 0
    assert resolver_prompt(variantes["base"], indice) != resolver_prompt(
        variantes["MUT4"], indice
    )


def test_prompt_editado_depois_do_carregamento_derruba_a_run_antes_de_comecar(
    variantes,
) -> None:
    """Rodar assim rotularia a coluna do experimento com o hash de um prompt que não rodou."""
    congelada = variantes["base"].model_copy(update={"prompt_sha": "f" * 64})
    with pytest.raises(ErroDeExecucao, match="prompt_sha"):
        resolver_prompt(congelada, indexar_prompts())


def test_o_approver_espelha_o_exige_citacao_da_variante(variantes) -> None:
    """O MUT2 degrada `exige_citacao`. Um gate que continuasse exigindo fundamentação
    anularia o mutante, e a INS.9 mediria 0% de detecção por defeito do runner."""
    base = construir_approver("policy", variantes["base"])
    mut2 = construir_approver("policy", variantes["MUT2"])
    assert isinstance(base, PolicyApprover) and base.exige_citacao is True
    assert isinstance(mut2, PolicyApprover) and mut2.exige_citacao is False
    assert isinstance(construir_approver("auto_approve", variantes["base"]), AutoApprove)
    assert isinstance(construir_approver("auto_deny", variantes["base"]), AutoDeny)


def test_mut1_esconde_a_tool_do_catalogo_da_run(tmp_path, corpus, variantes) -> None:
    """`VariantConfig.tools_ocultas` só vale se o runner a levar ao `RunContext`."""
    bateria = montar(
        tmp_path,
        corpus,
        variantes,
        cenarios={"ids": ["cen_a"]},
        variantes=["MUT1"],
        sample_seeds=[1],
    )
    roteiro = [
        passo_de_acao("get_data_quality", {"asset_id": "asset_T1", "point_id": "pt_1"}),
        passo_de_resposta("concluí sem qualidade de sinal"),
    ]
    manifesto = rodar(bateria, ApiContada(), roteiro=roteiro)
    (registro,) = manifesto.runs.values()
    eventos = read_trace(bateria.diretorio / registro.trace)
    chamadas = [e.tool_name for e in eventos if isinstance(e, ToolCall)]
    assert "get_data_quality" not in chamadas


# ---------------------------------------------------------------------------
# Paralelismo
# ---------------------------------------------------------------------------


def test_paralelismo_dois_roda_duas_celulas_ao_mesmo_tempo(tmp_path, corpus, variantes) -> None:
    """A prova de que o limite não é decoração.

    `Inferencia.completar` é síncrona; num `asyncio.Semaphore(2)` ela bloquearia o laço e as
    duas runs se serializariam, com o manifesto declarando um paralelismo inexistente. A
    barreira de duas partes só destrava se as duas runs estiverem de fato em voo.
    """
    bateria = montar(
        tmp_path,
        corpus,
        variantes,
        cenarios={"ids": ["cen_a", "cen_b"]},
        sample_seeds=[1],
        paralelismo=2,
    )
    barreira = threading.Barrier(2, timeout=10)

    def fabrica(_celula: Any) -> ModeloDeRoteiro:
        encontrou = {"ja": False}

        def esperar_a_outra() -> None:
            if not encontrou["ja"]:
                encontrou["ja"] = True
                barreira.wait()

        return ModeloDeRoteiro(list(ROTEIRO_PADRAO), antes=esperar_a_outra)

    manifesto = rodar(bateria, ApiContada(), fabrica=fabrica)
    assert [r.status for r in manifesto.runs.values()] == ["ok", "ok"]


def test_paralelismo_um_roda_na_thread_do_chamador(tmp_path, corpus, variantes) -> None:
    """`1` existe para diagnóstico: o traceback de um erro não passa por um `Future`."""
    bateria = montar(
        tmp_path,
        corpus,
        variantes,
        cenarios={"ids": ["cen_a", "cen_b"]},
        sample_seeds=[1],
        paralelismo=1,
    )
    threads: set[int] = set()

    def fabrica(_celula: Any) -> ModeloDeRoteiro:
        threads.add(threading.get_ident())
        return ModeloDeRoteiro(list(ROTEIRO_PADRAO))

    rodar(bateria, ApiContada(), fabrica=fabrica)
    assert threads == {threading.get_ident()}


# ---------------------------------------------------------------------------
# Timeout — o único status do `RunEnd` que só o runner produz
# ---------------------------------------------------------------------------


def test_run_que_estoura_o_relogio_de_parede_fecha_como_timeout(
    tmp_path, corpus, variantes
) -> None:
    """`ResultadoDaRun` não produz `timeout`: quem tem relógio de parede da run é o runner.

    O cancelamento chega no próximo ponto de espera — `completar` é síncrona e não é
    interrompida —, então o teto real é `timeout_s` mais uma chamada ao modelo. É proteção
    contra servidor pendurado, não cronômetro.
    """
    bateria = montar(
        tmp_path,
        corpus,
        variantes,
        cenarios={"ids": ["cen_a"]},
        sample_seeds=[1],
        timeout_s=0.05,
    )
    roteiro = [
        passo_de_acao("list_analyses", {"asset_id": "asset_T1"}),
        passo_de_acao("get_baseline", {"asset_id": "asset_T1", "point_id": "pt_1"}),
        passo_de_resposta("terminei"),
    ]

    def fabrica(_celula: Any) -> ModeloDeRoteiro:
        return ModeloDeRoteiro(list(roteiro), antes=lambda: time.sleep(0.2))

    manifesto = rodar(bateria, ApiContada(), fabrica=fabrica)
    (registro,) = manifesto.runs.values()

    assert registro.status == "timeout"
    eventos = read_trace(bateria.diretorio / registro.trace)
    fins = [e for e in eventos if isinstance(e, RunEnd)]
    assert fins[0].status == "timeout"
    assert fins[0].total_llm_calls == len([e for e in eventos if isinstance(e, LLMCall)])
    assert validar_trace(eventos) == []


# ---------------------------------------------------------------------------
# A configuração da bateria
# ---------------------------------------------------------------------------


def test_campo_com_grafia_errada_e_recusado(tmp_path, corpus, variantes) -> None:
    """Mesmo formato de falha da T17: chave descartada em silêncio roda outra matriz."""
    with pytest.raises(ErroDeBateria, match="desconhecido"):
        montar(tmp_path, corpus, variantes, sample_seed=[1])


def test_seed_no_bloco_do_modelo_e_recusada_com_o_motivo(tmp_path, corpus, variantes) -> None:
    with pytest.raises(ErroDeBateria, match="colapsaria o eixo"):
        montar(tmp_path, corpus, variantes, modelos={"m1": _campos_do_modelo(seed=7)})


def test_sample_seeds_repetidas_sao_recusadas(tmp_path, corpus, variantes) -> None:
    """Duas células com a mesma seed colidem no mesmo `run_id`, e a segunda sobrescreveria
    o trace da primeira — uma repetição do `pass^k` que some sem nada acusar."""
    with pytest.raises(ErroDeBateria, match="repete"):
        montar(tmp_path, corpus, variantes, sample_seeds=[1, 2, 1])


def test_sem_selecao_de_cenario_a_bateria_nao_carrega(tmp_path, corpus, variantes) -> None:
    """Sem seleção explícita o denominador passaria a depender de quantos arquivos existem."""
    with pytest.raises(ErroDeBateria, match="cenarios"):
        montar(tmp_path, corpus, variantes, cenarios={})


def test_split_e_ids_juntos_sao_recusados(tmp_path, corpus, variantes) -> None:
    with pytest.raises(ErroDeBateria, match="OU"):
        montar(tmp_path, corpus, variantes, cenarios={"split": "test", "ids": ["cen_a"]})


def test_variante_inexistente_e_recusada(tmp_path, corpus, variantes) -> None:
    with pytest.raises(ErroDeBateria, match="inexistente"):
        montar(tmp_path, corpus, variantes, variantes=["MUT9"])


def test_approver_desconhecido_e_recusado(tmp_path, corpus, variantes) -> None:
    with pytest.raises(ErroDeBateria, match="approver"):
        montar(tmp_path, corpus, variantes, approver="humano")


def test_cenario_sem_env_seed_e_recusado(tmp_path) -> None:
    """A seed do ambiente é por cenário; sem ela a run roda contra um mundo indeclarável."""
    diretorio = tmp_path / "cenarios"
    diretorio.mkdir()
    (diretorio / "cen_x.yaml").write_text(
        yaml.safe_dump({"id": "cen_x", "split": "test", "ambiente": {}}), encoding="utf-8"
    )
    with pytest.raises(ErroDeBateria, match="env_seed"):
        carregar_corpus_executavel(diretorio)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_dry_run_imprime_a_matriz_e_os_excluidos_sem_executar(
    tmp_path, corpus, variantes, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(
        "tapieval.runner.cli.carregar_bateria",
        lambda caminho: montar(tmp_path, corpus, variantes, cenarios={"split": "test"}),
    )
    codigo = main(["--manifest", str(tmp_path / "bateria.yaml"), "--dry-run"])
    saida = capsys.readouterr().out

    assert codigo == 0
    assert "2 células" in saida
    assert "EXCLUÍDO cen_morto" in saida
    assert not (tmp_path / "runs" / "exp_teste" / "traces").exists()


def test_a_cli_sai_1_quando_a_bateria_tem_run_invalida(
    tmp_path, corpus, variantes, monkeypatch, capsys
) -> None:
    """Sair 0 com célula faltante ou run inválida deixaria um `make` verde sobre uma bateria
    pela metade."""
    emitir_original = TraceWriter.emit
    monkeypatch.setattr(
        TraceWriter,
        "emit",
        lambda self, evento: None if evento.seq == 3 else emitir_original(self, evento),
    )
    bateria = montar(tmp_path, corpus, variantes, cenarios={"ids": ["cen_a"]})
    monkeypatch.setattr("tapieval.runner.cli.carregar_bateria", lambda _c: bateria)
    api = ApiContada()
    monkeypatch.setattr(
        "tapieval.runner.cli.rodar_bateria",
        lambda b, **kw: rodar_bateria(
            b,
            fabrica_de_inferencia=lambda _c: ModeloDeRoteiro(list(ROTEIRO_PADRAO)),
            transporte_http=api.transporte,
            **kw,
        ),
    )

    codigo = main(["--manifest", str(tmp_path / "bateria.yaml")])
    saida = capsys.readouterr().out
    assert codigo == 1
    assert "INVÁLIDA" in saida
    assert "runs inválidas:     1" in saida


def test_a_cli_recusa_bateria_ilegivel_sem_stack_trace(tmp_path, capsys) -> None:
    ruim = tmp_path / "ruim.yaml"
    ruim.write_text("experiment_id: x\n", encoding="utf-8")
    assert main(["--manifest", str(ruim)]) == 2
    assert "erro na bateria" in capsys.readouterr().err


def test_o_manifesto_do_disco_e_o_caminho_que_a_cli_anuncia(
    tmp_path, corpus, variantes
) -> None:
    bateria = montar(tmp_path, corpus, variantes, cenarios={"ids": ["cen_a"]})
    rodar(bateria, ApiContada())
    assert caminho_do_manifesto(bateria.diretorio).exists()
    assert caminho_do_manifesto(bateria.diretorio).name == "manifest.json"
