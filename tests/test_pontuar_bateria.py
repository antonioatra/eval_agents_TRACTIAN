"""A passagem de pontuação de uma bateria inteira — `scoring/bateria.py`.

O QUE ESTE ARQUIVO PRENDE
    `tests/test_n1.py` e `test_n2.py` provam a derivação de UMA run sobre traces sintéticos;
    `tests/test_repro.py` prova que ela é determinística sobre os 24 traces reais da piloto.
    Nenhum dos dois olha o que este módulo faz de novo: **a contabilidade da bateria**.

    E a contabilidade é onde o erro é caro e silencioso. Uma célula que some da tabela muda o
    denominador do `pass^k`; uma `falha_do_instrumento` pontuada põe defeito nosso na
    taxonomia como se fosse falha do agente; um trace ilegível que derrube o processo custa a
    tabela inteira às 22h. Cada teste abaixo prende UM desses modos de falha — todos
    silenciosos, todos na direção que favorece o resultado.

POR QUE A PILOTO E NÃO FIXTURE SINTÉTICA
    O insumo é `runs/piloto_2026-08-24c/`, os mesmos 24 traces reais do `test_repro`. Um
    manifesto sintético não tem `budget_exceeded` no meio da iteração 8 nem run sem
    `DecisionEvent` — e a segunda é exatamente o caso que separa "não pôde ser medida" de
    "passou" (A10). Os casos que a piloto NÃO tem (falha do instrumento, trace corrompido,
    célula faltante) são produzidos copiando a bateria para `tmp_path` e mexendo no
    manifesto: continua sendo dado real, com um defeito conhecido injetado.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tapieval.schema.trace import N3Judge, ScoreRecord
from tapieval.scoring import bateria
from tapieval.scoring.bateria import (
    NOME_DOS_SCORES,
    SCORER_DETERMINISTICO,
    caminho_dos_scores,
    escrever_scores,
    falhas_do_score,
    ler_scores,
    pontuar_bateria,
    scorer_deterministico,
)
from tapieval.scoring.cli import main
from tapieval.scoring.severidade import SHA_DA_TAXONOMIA

RAIZ = Path(__file__).resolve().parents[1]
PILOTO = RAIZ / "runs" / "piloto_2026-08-24c"
CELULAS_DA_PILOTO = 24

CALCULADO_EM = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
"""Fixo nos testes: `calculado_em` é a hora do relógio e mudaria a serialização a cada
execução, escondendo num diff de timestamp a diferença que se quer observar."""


# ---------------------------------------------------------------------------
# Uma cópia da piloto que se pode estragar
# ---------------------------------------------------------------------------


def _copia_da_piloto(tmp_path: Path) -> Path:
    destino = tmp_path / "bateria"
    shutil.copytree(PILOTO, destino)
    return destino


def _mexer_no_manifesto(diretorio: Path, mudanca) -> None:
    caminho = diretorio / "manifest.json"
    documento: dict[str, Any] = json.loads(caminho.read_text(encoding="utf-8"))
    mudanca(documento)
    caminho.write_text(json.dumps(documento), encoding="utf-8")


def _primeiro_run_id(diretorio: Path) -> str:
    documento = json.loads((diretorio / "manifest.json").read_text(encoding="utf-8"))
    return sorted(documento["runs"])[0]


def _um_run_id_sem_decisao() -> str:
    """Uma run da piloto que já sai não pontuável por `decisao_prevista is None`."""
    sem_decisao = [
        score.run_id
        for score in pontuar_bateria(PILOTO, calculado_em=CALCULADO_EM).scores
        if not score.pontuavel
    ]
    assert sem_decisao, "a piloto não tem mais run sem decisão; o teste perdeu o alvo"
    return sorted(sem_decisao)[0]


# ---------------------------------------------------------------------------
# O caminho feliz: a bateria inteira vira registro
# ---------------------------------------------------------------------------


def test_pontua_todas_as_celulas_declaradas():
    """Célula declarada e não pontuada é bateria pela metade lida como bateria inteira."""
    pontuacao = pontuar_bateria(PILOTO, calculado_em=CALCULADO_EM)

    assert len(pontuacao.scores) == CELULAS_DA_PILOTO
    assert pontuacao.faltantes == ()
    assert pontuacao.nao_pontuadas == ()
    assert pontuacao.completa


def test_a_coordenada_sai_do_manifesto_e_nao_do_nome_do_arquivo():
    """O `run_id` é uma string com `--` no meio, e o `scenario_id` também pode ter.

    Partir o nome do arquivo funcionaria em todo o corpus de hoje e quebraria calado no dia
    em que um cenário fosse renomeado — com a tabela já impressa. O manifesto é a fonte de
    verdade da matriz (`ARQUITETURA §5`), e este teste é o que amarra isso.
    """
    documento = json.loads((PILOTO / "manifest.json").read_text(encoding="utf-8"))
    declaradas = {celula["run_id"]: celula for celula in documento["celulas"]}

    for score in pontuar_bateria(PILOTO, calculado_em=CALCULADO_EM).scores:
        celula = declaradas[score.run_id]
        assert score.scenario_id == celula["scenario_id"]
        assert score.model_key == celula["model_key"]
        assert score.variant_id == celula["variant_id"]
        assert score.seed == celula["sample_seed"]
        assert score.split == celula["split"]


def test_o_scorer_carimbado_e_o_deterministico_e_nao_o_do_judge():
    """Esta passagem não roda judge; carimbar `v2` diria que rodou.

    `ScorerVersion` dentro do score é o que prova de qual instrumento o registro veio (R4).
    Um registro com `n3=None` e o sha do `judge_frozen.json` no `scorer` seria uma afirmação
    falsa sobre a procedência do número — e não haveria como detectá-la depois.
    """
    scorer = scorer_deterministico()
    assert scorer.scorer_version == SCORER_DETERMINISTICO != "v2"
    assert scorer.sha256 == SHA_DA_TAXONOMIA
    assert scorer.judge_model is None

    for score in pontuar_bateria(PILOTO, calculado_em=CALCULADO_EM).scores:
        assert score.n3 is None
        assert score.scorer.sha256 == SHA_DA_TAXONOMIA


def test_n3_ausente_nao_vira_falha_de_conteudo_ausente():
    """`n3=None` é NÃO MEDIDO, e não "limpo" — a leitura que sustenta a curva de H0.

    Se a ausência do judge fosse lida como ausência de falha, o ponto N1+N2 da curva subiria
    por construção e o ganho incremental do N3 (INS.2) mediria o próprio buraco.
    """
    pontuacao = pontuar_bateria(PILOTO, calculado_em=CALCULADO_EM)
    codigos = {falha.codigo for score in pontuacao.scores for falha in falhas_do_score(score)}

    # C2, C3 e C7 são os três que exigem o trace lido pelo judge (`METRICAS §4`).
    assert not codigos & {"C2", "C3", "C7"}
    # E a passagem não é vazia: as classes que N1/N2 medem aparecem.
    assert codigos


# ---------------------------------------------------------------------------
# Run não pontuável — o formato de erro do A10
# ---------------------------------------------------------------------------


def test_run_sem_decisao_fica_fora_do_denominador_com_motivo():
    """A piloto tem runs sem `DecisionEvent`; nenhuma delas pode sair aprovada.

    Sem o predicado de `severidade.motivo_nao_pontuavel`, `sucesso_binario([])` é `True` e a
    run **não medida** entra no `pass^k` como **passou** (A10).
    """
    pontuacao = pontuar_bateria(PILOTO, calculado_em=CALCULADO_EM)
    sem_decisao = [score for score in pontuacao.scores if not score.pontuavel]

    assert sem_decisao, "a piloto tinha runs sem decisão; se não tem mais, o teste perdeu o alvo"
    for score in sem_decisao:
        assert score.sucesso_binario is False
        assert score.motivo_nao_pontuavel
        assert len(pontuacao.pontuaveis) == len(pontuacao.scores) - len(sem_decisao)


def test_run_sem_decisao_nao_passa_nem_quando_nao_ha_falha_detectada(monkeypatch):
    """O caso que a piloto não tem no disco, e que é exatamente o A10.

    Uma run sem `DecisionEvent` **e** sem nenhuma falha que N1/N2 saibam detectar:
    `sucesso_binario([])` é `True`, e sem o corte por `pontuavel` ela entraria no `pass^k`
    como aprovada. As runs sem decisão da piloto todas têm alguma falha de processo junto, o
    que faz o corte parecer supérfluo — por isso a lista vazia é injetada aqui em vez de
    procurada nos traces. Sem o corte, o validador do `ScoreRecord` recusa o registro e a run
    some da tabela: o mesmo dado perdido, por outra porta.
    """
    monkeypatch.setattr(bateria, "classificar_falhas", lambda *args, **kwargs: [])
    pontuacao = pontuar_bateria(PILOTO, calculado_em=CALCULADO_EM)

    assert len(pontuacao.scores) == CELULAS_DA_PILOTO, (
        f"runs engolidas pelo validador: {[r.motivo for r in pontuacao.nao_pontuadas]}"
    )
    sem_decisao = [score for score in pontuacao.scores if not score.pontuavel]
    assert sem_decisao, "a piloto tinha runs sem decisão; se não tem mais, o teste perdeu o alvo"
    assert all(score.sucesso_binario is False for score in sem_decisao)


def test_run_invalida_do_manifesto_e_pontuada_mas_fora_do_denominador(tmp_path):
    """A7: trace estruturalmente quebrado não some da contagem — entra com o motivo.

    Descartar em silêncio suporia que runs quebram de forma aleatória; elas quebram pelo
    mesmo motivo, na mesma célula da matriz, e é justamente aí que a ausência seria lida
    como "nada de errado aconteceu".
    """
    diretorio = _copia_da_piloto(tmp_path)
    # De propósito uma run que JÁ é não pontuável por decisão ausente: é onde os dois motivos
    # valem ao mesmo tempo, e é o único lugar onde a precedência entre eles é observável. O
    # defeito estrutural é a causa; a decisão ausente é sintoma dele.
    alvo = _um_run_id_sem_decisao()

    def quebrar(documento):
        documento["runs"][alvo]["valida"] = False
        documento["runs"][alvo]["defeitos"] = ["seq duplicado: 4"]
        documento["runs"][alvo]["motivo_nao_pontuavel"] = "trace inválido (A7): seq duplicado: 4"

    _mexer_no_manifesto(diretorio, quebrar)
    pontuacao = pontuar_bateria(diretorio, calculado_em=CALCULADO_EM)

    (score,) = [s for s in pontuacao.scores if s.run_id == alvo]
    assert score.pontuavel is False
    assert score.sucesso_binario is False
    assert score.motivo_nao_pontuavel == "trace inválido (A7): seq duplicado: 4"
    assert len(pontuacao.scores) == CELULAS_DA_PILOTO, "a run inválida foi pontuada, não descartada"


# ---------------------------------------------------------------------------
# O que não pode virar ScoreRecord
# ---------------------------------------------------------------------------


def test_falha_do_instrumento_nao_vira_score(tmp_path):
    """Defeito nosso não entra na taxonomia como falha do agente.

    É a mesma separação que `scripts/_portao_da_bateria.py` faz, e que custou uma reescrita
    lá: `falha_do_instrumento` é o harness quebrado, `error` é o modelo não produzindo passo
    válido. Pontuar a primeira mediria o medidor.
    """
    diretorio = _copia_da_piloto(tmp_path)
    alvo = _primeiro_run_id(diretorio)

    def falhar(documento):
        documento["runs"][alvo]["status"] = "falha_do_instrumento"
        documento["runs"][alvo]["erro"] = "ConnectError: LM Studio fora do ar"

    _mexer_no_manifesto(diretorio, falhar)
    pontuacao = pontuar_bateria(diretorio, calculado_em=CALCULADO_EM)

    assert alvo not in {score.run_id for score in pontuacao.scores}
    (nao_pontuada,) = [r for r in pontuacao.nao_pontuadas if r.run_id == alvo]
    assert "LM Studio fora do ar" in nao_pontuada.motivo
    assert not pontuacao.completa


def test_celula_faltante_e_reportada_e_nao_apenas_ausente(tmp_path):
    """Bateria incompleta é reportada como incompleta (`PLANO` T24-26).

    Sem isto, uma bateria de 288 células que gravou 240 produz uma tabela de 240 linhas que
    se lê como completa — e o denominador do `pass^k` passa a ser outro sem que ninguém
    tenha decidido isso.
    """
    diretorio = _copia_da_piloto(tmp_path)
    alvo = _primeiro_run_id(diretorio)

    _mexer_no_manifesto(diretorio, lambda documento: documento["runs"].pop(alvo))
    pontuacao = pontuar_bateria(diretorio, calculado_em=CALCULADO_EM)

    assert pontuacao.faltantes == (alvo,)
    assert len(pontuacao.scores) == CELULAS_DA_PILOTO - 1
    assert not pontuacao.completa


def test_trace_ilegivel_nao_derruba_as_outras_runs(tmp_path):
    """Uma run ruim custa uma linha da tabela, não a tabela.

    Uma passagem que morre na primeira run corrompida obrigaria a escolher, às 22h, entre
    consertar o trace e não ter resultado nenhum — com as outras 287 runs já no disco.
    """
    diretorio = _copia_da_piloto(tmp_path)
    alvo = _primeiro_run_id(diretorio)
    (diretorio / "traces" / f"{alvo}.jsonl").write_text("{ isto não é json\n", encoding="utf-8")

    pontuacao = pontuar_bateria(diretorio, calculado_em=CALCULADO_EM)

    assert len(pontuacao.scores) == CELULAS_DA_PILOTO - 1
    (nao_pontuada,) = pontuacao.nao_pontuadas
    assert nao_pontuada.run_id == alvo


def test_cenario_fora_do_corpus_e_nomeado(tmp_path):
    """Gabarito que não existe mais não pode virar pontuação contra outro gabarito.

    Pontuar `aut_01` com o gabarito de `aut_02` renomeia o experimento em silêncio: os
    números saem, a tabela imprime, e não há campo no registro que denuncie a troca.
    """
    diretorio = _copia_da_piloto(tmp_path)
    alvo = _primeiro_run_id(diretorio)

    def renomear(documento):
        for celula in documento["celulas"]:
            if celula["run_id"] == alvo:
                celula["scenario_id"] = "cenario_que_nao_existe"

    _mexer_no_manifesto(diretorio, renomear)
    pontuacao = pontuar_bateria(diretorio, calculado_em=CALCULADO_EM)

    (nao_pontuada,) = [r for r in pontuacao.nao_pontuadas if r.run_id == alvo]
    assert "cenario_que_nao_existe" in nao_pontuada.motivo


def test_diretorio_sem_manifesto_levanta(tmp_path):
    """Pontuar diretório vazio devolveria uma tabela de zero linha em vez de um erro."""
    with pytest.raises(FileNotFoundError, match="nunca rodou"):
        pontuar_bateria(tmp_path)


# ---------------------------------------------------------------------------
# Disco
# ---------------------------------------------------------------------------


def test_escrever_e_reler_devolve_os_mesmos_registros(tmp_path):
    """Ida e volta pelo JSONL sem perda: é o formato que os notebooks da T28-T30 leem."""
    pontuacao = pontuar_bateria(PILOTO, calculado_em=CALCULADO_EM)
    destino = escrever_scores(pontuacao, tmp_path)

    relidos = ler_scores(destino)
    assert len(relidos) == CELULAS_DA_PILOTO
    assert [s.run_id for s in relidos] == sorted(s.run_id for s in pontuacao.scores)
    assert relidos == sorted(pontuacao.scores, key=lambda s: s.run_id)


def test_o_arquivo_e_uma_linha_por_run_ordenada(tmp_path):
    """JSONL de verdade — uma linha por registro, ordenada, legível por `jq` e por `grep`.

    Ordenar por `run_id` é o que torna o diff entre duas pontuações da mesma bateria
    legível: sem ordem estável, reordenar o dicionário de runs mudaria o arquivo inteiro.
    """
    destino = escrever_scores(pontuar_bateria(PILOTO, calculado_em=CALCULADO_EM), tmp_path)
    linhas = destino.read_text(encoding="utf-8").splitlines()

    assert len(linhas) == CELULAS_DA_PILOTO
    ids = [json.loads(linha)["run_id"] for linha in linhas]
    assert ids == sorted(ids)


def test_escrever_recusa_apagar_o_julgamento_do_judge(tmp_path):
    """A passagem determinística rodada por engano depois da do judge apagaria N3.

    O judge custa chamada de rede e RPD (`configs/bateria_referencia.yaml`), e o arquivo
    resultante não teria nenhum campo dizendo que o julgamento existiu. A recusa é o que
    transforma isso em erro barulhento em vez de perda silenciosa.
    """
    pontuacao = pontuar_bateria(PILOTO, calculado_em=CALCULADO_EM)
    destino = escrever_scores(pontuacao, tmp_path)

    _gravar_com_n3(destino, ler_scores(destino))

    with pytest.raises(ValueError, match="apagaria julgamento"):
        escrever_scores(pontuacao, tmp_path)

    escrever_scores(pontuacao, tmp_path, sobrescrever_n3=True)
    assert all(score.n3 is None for score in ler_scores(destino))


def _gravar_com_n3(destino: Path, scores: list[ScoreRecord]) -> None:
    """Põe um julgamento cego no primeiro registro e regrava o arquivo."""
    julgado = scores[0].model_copy(
        update={
            "n3": N3Judge(
                configuracao="cego",
                causa_raiz_correta=True,
                mencionou_limitacao_relevante=True,
                responde_a_pergunta="sim",
                justificativa="julgamento de teste",
                judge_latencia_ms=1,
            )
        }
    )
    linhas = [julgado, *scores[1:]]
    destino.write_text(
        "".join(score.model_dump_json() + "\n" for score in linhas), encoding="utf-8"
    )


def test_score_ilegivel_nomeia_a_linha(tmp_path):
    """Score parcialmente ilegível pontuado em silêncio é pior que a leitura que falha."""
    caminho = tmp_path / "scores.jsonl"
    caminho.write_text('{"run_id": "x"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match=r"scores\.jsonl:1"):
        ler_scores(caminho)


def test_caminho_padrao_e_o_diretorio_da_bateria(tmp_path):
    """Sem `--saida`, o score mora ao lado do trace de onde saiu."""
    diretorio = _copia_da_piloto(tmp_path)
    destino = escrever_scores(pontuar_bateria(diretorio, calculado_em=CALCULADO_EM))

    assert destino == caminho_dos_scores(diretorio)
    assert destino.is_file()


# ---------------------------------------------------------------------------
# A propriedade que o `test_repro` prova por outro caminho
# ---------------------------------------------------------------------------


def test_duas_passagens_dao_o_mesmo_arquivo(tmp_path):
    """Mesmo trace, mesmo gabarito, mesmo byte — com `calculado_em` fixo.

    `test_repro.py` prova o mesmo campo a campo e em outro processo, com outra semente de
    hash. Aqui a asserção é sobre a SERIALIZAÇÃO: um campo de lista montado a partir de
    `set` sai numa ordem instável, e é o arquivo que a banca vai ler.
    """
    primeira = escrever_scores(
        pontuar_bateria(PILOTO, calculado_em=CALCULADO_EM), tmp_path / "a"
    )
    segunda = escrever_scores(
        pontuar_bateria(PILOTO, calculado_em=CALCULADO_EM), tmp_path / "b"
    )

    assert primeira.read_text(encoding="utf-8") == segunda.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# A CLI — `python -m tapieval.scoring`
# ---------------------------------------------------------------------------


def test_cli_grava_ao_lado_dos_traces_e_sai_zero(tmp_path):
    """Bateria inteira pontuada: código 0 e `scores.jsonl` dentro do diretório da bateria."""
    diretorio = _copia_da_piloto(tmp_path)

    assert main(["--bateria", str(diretorio)]) == 0
    assert len(ler_scores(diretorio / NOME_DOS_SCORES)) == CELULAS_DA_PILOTO


def test_cli_sai_um_quando_a_bateria_esta_incompleta(tmp_path):
    """Sair 0 sobre bateria pela metade deixaria um `make` verde sobre denominador trocado."""
    diretorio = _copia_da_piloto(tmp_path)
    _mexer_no_manifesto(diretorio, lambda doc: doc["runs"].pop(_primeiro_run_id(diretorio)))

    assert main(["--bateria", str(diretorio)]) == 1
    assert (diretorio / NOME_DOS_SCORES).is_file(), (
        "o arquivo ainda é gravado: bateria incompleta é REPORTADA, não descartada"
    )


def test_cli_nao_derruba_o_codigo_de_saida_por_run_nao_pontuavel(tmp_path):
    """A piloto tem 6 runs fora do denominador e mesmo assim está inteira.

    Confundir "não pontuável" com "faltante" faria toda bateria honesta sair 1, e o código de
    saída deixaria de significar coisa alguma.
    """
    diretorio = _copia_da_piloto(tmp_path)
    assert any(not score.pontuavel for score in pontuar_bateria(diretorio).scores)
    assert main(["--bateria", str(diretorio)]) == 0


def test_cli_sem_gravar_nao_escreve_nada(tmp_path):
    diretorio = _copia_da_piloto(tmp_path)

    assert main(["--bateria", str(diretorio), "--sem-gravar"]) == 0
    assert not (diretorio / NOME_DOS_SCORES).exists()


def test_cli_respeita_a_saida_escolhida(tmp_path):
    diretorio = _copia_da_piloto(tmp_path)
    saida = tmp_path / "outro"

    assert main(["--bateria", str(diretorio), "--saida", str(saida)]) == 0
    assert (saida / NOME_DOS_SCORES).is_file()
    assert not (diretorio / NOME_DOS_SCORES).exists()


def test_cli_recusa_apagar_n3_e_sai_dois(tmp_path):
    """Erro de operação sai 2, e não 1: 1 é bateria incompleta, 2 é comando que não rodou."""
    diretorio = _copia_da_piloto(tmp_path)
    main(["--bateria", str(diretorio)])
    _gravar_com_n3(diretorio / NOME_DOS_SCORES, ler_scores(diretorio / NOME_DOS_SCORES))

    assert main(["--bateria", str(diretorio)]) == 2
    assert any(score.n3 is not None for score in ler_scores(diretorio / NOME_DOS_SCORES))

    assert main(["--bateria", str(diretorio), "--sobrescrever-n3"]) == 0
    assert all(score.n3 is None for score in ler_scores(diretorio / NOME_DOS_SCORES))


def test_cli_sai_dois_sem_manifesto(tmp_path):
    assert main(["--bateria", str(tmp_path)]) == 2


# ---------------------------------------------------------------------------
# Os `scores.jsonl` versionados no repositório
# ---------------------------------------------------------------------------


def _scores_versionados() -> list[Path]:
    return sorted((RAIZ / "runs").glob("*/scores.jsonl"))


@pytest.mark.parametrize("caminho", _scores_versionados(), ids=lambda c: c.parent.name)
def test_score_versionado_ainda_e_o_que_o_scorer_de_hoje_produz(caminho):
    """N1 e N2 do arquivo no disco têm de bater com a recomputação — ou a promessa é falsa.

    `ARQUITETURA §5`, decisão 1, promete score DERIVADO do trace. Um `scores.jsonl` commitado
    é uma cópia dessa derivação, e cópia envelhece: mexer num scorer e não regravar deixa no
    repositório um número que nenhum código produz mais, com a figura da banca saindo dele.
    Este teste torna o envelhecimento uma falha vermelha em vez de uma descoberta.

    **A comparação é só de N1, N2 e das coordenadas**, e é aí que ela é honesta. `calculado_em`
    é hora do relógio, não derivação. E N3 vem da OUTRA passagem, que roda depois: exigir que
    o arquivo tivesse `n3=None` como esta passagem produz faria o teste passar hoje e reprovar
    a bateria no dia em que o judge julgasse — punindo exatamente o trabalho que ele deveria
    proteger. Os campos que dependem de N3 (`sucesso_binario`, `score_final`, `pontuavel`) só
    entram na comparação quando o arquivo ainda não tem julgamento.
    """
    gravados = {score.run_id: score for score in ler_scores(caminho)}
    recomputados = {
        score.run_id: score
        for score in pontuar_bateria(caminho.parent, calculado_em=CALCULADO_EM).scores
    }

    assert gravados.keys() == recomputados.keys(), (
        f"{caminho} e a bateria discordam sobre quais runs existem — rode "
        f"`make pontuar BATERIA={caminho.parent}`"
    )
    for run_id, gravado in gravados.items():
        recomputado = recomputados[run_id]
        assert gravado.n1 == recomputado.n1, f"{caminho}: N1 de {run_id} mudou desde a gravação"
        assert gravado.n2 == recomputado.n2, f"{caminho}: N2 de {run_id} mudou desde a gravação"
        assert (gravado.scenario_id, gravado.model_key, gravado.variant_id, gravado.seed) == (
            recomputado.scenario_id,
            recomputado.model_key,
            recomputado.variant_id,
            recomputado.seed,
        ), f"{caminho}: a coordenada de {run_id} mudou"
        if gravado.n3 is None:
            assert gravado.sucesso_binario == recomputado.sucesso_binario
            assert gravado.pontuavel == recomputado.pontuavel
            assert gravado.score_final == recomputado.score_final
