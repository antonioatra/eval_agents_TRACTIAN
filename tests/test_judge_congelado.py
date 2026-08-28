"""R4 — o manifesto de bateria VALIDA o judge congelado, não só o menciona em comentário.

O QUE ESTES TESTES SUSTENTAM
    `METRICAS §9.3` manda congelar prompt + rubrica + few-shots + snapshot do modelo com sha256
    antes da bateria final. Até a R4 essa exigência morava num comentário de cabeçalho em caixa
    alta nos cinco manifestos, porque `CAMPOS_DA_BATERIA` recusava a chave `judge`. Cabeçalho
    não roda. Os testes abaixo são a exigência virando código:

    * bateria sem `judge` não carrega — em NENHUMA bateria, e a mensagem diz as duas saídas;
    * `judge` apontando para arquivo que não existe não carrega, e o erro nomeia o arquivo;
    * `judge_frozen.json` cujo sha não confere não carrega — este é o caso perigoso, porque um
      sha que não bate parece congelado para todo leitor humano do arquivo;
    * a dispensa exige o motivo por escrito, e é assim que piloto e calibração continuam
      rodando sem que "esqueci de declarar" passe por "esta bateria não precisa".

O `configs/judge_frozen.json` DE VERDADE NÃO EXISTE, E ESTES TESTES NÃO O INVENTAM
    Ele é produzido pela outra metade da T23, que depende de uma decisão de curadoria (rubrica
    v1 ou v2). Aqui o congelamento é fixture: o que se testa é o CONTRATO do arquivo, e o
    contrato tem de estar de pé antes de o arquivo existir — senão a T23 congela num formato e
    o carregador espera outro.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from tapieval.runner.judge_congelado import (
    CAMPOS_ASSINADOS,
    DispensaDeCongelamento,
    ErroDeJudgeCongelado,
    JudgeCongelado,
    carregar_judge_congelado,
    material_assinado,
    sha_da_rubrica,
    sha_do_judge,
)
from tapieval.runner.manifesto import judge_do_manifesto
from tapieval.runner.matriz import (
    RAIZ_DO_REPO,
    ErroDeBateria,
    carregar_bateria,
    carregar_corpus_executavel,
)
from tapieval.sut.variants import carregar_variantes

MODELO_DO_JUDGE: dict[str, Any] = {
    "model_id": "gemini-3.6-flash",
    "served_by": "vertex_ai",
    "quantization": None,
    "temperature": 0.0,
    "max_tokens": 2048,
    "seed": None,
    "structured_output": "json_schema",
    "context_window": 1048576,
}


def congelamento(**trocas: Any) -> dict[str, Any]:
    """Um `judge_frozen.json` no formato do contrato, com o sha já coerente."""
    documento: dict[str, Any] = {
        "scorer_version": "v2",
        "prompt": "Você é o juiz N3. Responda só o JSON do schema.",
        "rubrica": "N3.1 responde_a_pergunta: sim | parcial | nao ...",
        "fewshots": [
            {"id": "fs01", "entrada": "...", "saida": {"responde_a_pergunta": "sim"}},
            {"id": "fs04", "entrada": "...", "saida": {"responde_a_pergunta": "parcial"}},
        ],
        "judge_model": dict(MODELO_DO_JUDGE),
        "congelado_em": "2026-08-27T12:00:00+00:00",
        "fewshot_origem": "escritos_a_mao",
    }
    documento.update(trocas)
    documento["sha256"] = sha_do_judge(documento)
    return documento


def escrever_congelamento(diretorio: Path, **trocas: Any) -> Path:
    caminho = diretorio / "judge_frozen.json"
    caminho.write_text(
        json.dumps(congelamento(**trocas), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return caminho


# ---------------------------------------------------------------------------
# O sha
# ---------------------------------------------------------------------------


def test_o_sha_assina_prompt_rubrica_fewshots_modelo_e_versao() -> None:
    """São os cinco campos que mudam o que o judge responde — `METRICAS §9.3`."""
    base = congelamento()
    for campo, outro_valor in (
        ("prompt", "outro prompt"),
        ("rubrica", "outra rubrica"),
        ("scorer_version", "v3"),
        ("fewshots", [{"id": "fs01"}]),
        ("judge_model", {**MODELO_DO_JUDGE, "temperature": 0.7}),
    ):
        assert campo in CAMPOS_ASSINADOS
        assert sha_do_judge({**base, campo: outro_valor}) != base["sha256"], campo


def test_o_sha_nao_muda_com_a_data_do_congelamento_nem_com_notas() -> None:
    """Recongelar conteúdo idêntico noutro dia é o MESMO judge.

    Assinar `congelado_em` faria dois congelamentos iguais parecerem instrumentos diferentes —
    e é a mesma escolha de `scoring/severidade.py`: arrumação não mexe no sha, curadoria mexe.
    """
    base = congelamento()
    assert sha_do_judge({**base, "congelado_em": "2027-01-01T00:00:00+00:00"}) == base["sha256"]
    assert sha_do_judge({**base, "notas": "recongelado após revisão de formatação"}) == (
        base["sha256"]
    )


def test_o_snapshot_do_modelo_e_normalizado_antes_de_assinar() -> None:
    """Omitir um campo que tem default descreve o MESMO modelo, e tem de dar o mesmo sha.

    Assinar o dicionário cru transformaria estilo de escrita do arquivo em mudança de
    instrumento — e a T23 e o carregador brigariam por causa de uma chave omitida.
    """
    enxuto = {c: v for c, v in MODELO_DO_JUDGE.items() if c not in {"seed", "quantization"}}
    assert sha_do_judge(congelamento(judge_model=enxuto)) == congelamento()["sha256"]


def test_o_material_assinado_nao_depende_da_ordem_das_chaves() -> None:
    documento = congelamento()
    invertido = dict(reversed(list(documento.items())))
    assert material_assinado(invertido) == material_assinado(documento)


# ---------------------------------------------------------------------------
# Carregamento do arquivo congelado
# ---------------------------------------------------------------------------


def test_o_congelamento_valido_carrega_com_o_sha_recalculado(tmp_path: Path) -> None:
    caminho = escrever_congelamento(tmp_path)
    judge = carregar_judge_congelado(caminho)

    assert judge.scorer_version == "v2"
    assert judge.sha256 == congelamento()["sha256"]
    assert judge.fewshot_ids == ("fs01", "fs04")
    assert judge.fewshot_origem == "escritos_a_mao"
    assert judge.judge_model.model_id == "gemini-3.6-flash"
    assert judge.rubrica_sha == sha_da_rubrica(congelamento()["rubrica"])
    assert judge.congelado_em == datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def test_arquivo_inexistente_diz_qual_arquivo_e_por_que_ele_importa(tmp_path: Path) -> None:
    """Erro nomeado, não `FileNotFoundError` cru: quem lê tem de saber o que declarar."""
    with pytest.raises(ErroDeJudgeCongelado) as erro:
        carregar_judge_congelado(tmp_path / "judge_frozen.json")

    mensagem = str(erro.value)
    assert "judge_frozen.json" in mensagem
    assert "não existe" in mensagem
    assert "pré-requisito" in mensagem


def test_sha_que_nao_confere_e_recusado_com_os_dois_valores(tmp_path: Path) -> None:
    """O caso perigoso: um judge cujo sha não bate PARECE congelado.

    Ele é a única forma de o pré-registro de `METRICAS §9.3` ser violado sem deixar rastro —
    a rubrica se ajusta ao resultado e o `ScoreRecord` continua carregando um sha de aparência
    respeitável. Por isso o sha é recalculado a cada carregamento, e não só lido.
    """
    documento = congelamento()
    declarado = documento["sha256"]
    documento["rubrica"] = documento["rubrica"] + " (e mais esta linha, depois de congelar)"
    caminho = tmp_path / "judge_frozen.json"
    caminho.write_text(json.dumps(documento), encoding="utf-8")

    with pytest.raises(ErroDeJudgeCongelado) as erro:
        carregar_judge_congelado(caminho)

    mensagem = str(erro.value)
    assert declarado in mensagem
    assert sha_do_judge(documento) in mensagem
    assert "PIOR que nenhum" in mensagem


def test_sha_com_tamanho_errado_e_recusado_antes_de_comparar(tmp_path: Path) -> None:
    caminho = tmp_path / "judge_frozen.json"
    caminho.write_text(json.dumps({**congelamento(), "sha256": "abc"}), encoding="utf-8")
    with pytest.raises(ErroDeJudgeCongelado, match="64 caracteres"):
        carregar_judge_congelado(caminho)


@pytest.mark.parametrize("campo", sorted({*CAMPOS_ASSINADOS, "sha256", "congelado_em"}))
def test_campo_obrigatorio_ausente_e_recusado(tmp_path: Path, campo: str) -> None:
    documento = congelamento()
    del documento[campo]
    caminho = tmp_path / "judge_frozen.json"
    caminho.write_text(json.dumps(documento), encoding="utf-8")

    with pytest.raises(ErroDeJudgeCongelado, match=campo):
        carregar_judge_congelado(caminho)


def test_campo_desconhecido_e_recusado_porque_nao_entraria_no_sha(tmp_path: Path) -> None:
    """Conteúdo não assinado dentro de um arquivo chamado 'frozen' é a falha que o sha impede."""
    documento = congelamento()
    documento["rubrica_extra"] = "a parte da rubrica que ficou de fora"
    caminho = tmp_path / "judge_frozen.json"
    caminho.write_text(json.dumps(documento), encoding="utf-8")

    with pytest.raises(ErroDeJudgeCongelado, match="rubrica_extra"):
        carregar_judge_congelado(caminho)


def test_fewshots_vazio_e_valido_e_fewshots_ausente_nao_e(tmp_path: Path) -> None:
    """Um judge sem few-shot é um desenho; um arquivo que não fala deles é incompleto."""
    caminho = escrever_congelamento(tmp_path, fewshots=[])
    assert carregar_judge_congelado(caminho).fewshot_ids == ()

    documento = congelamento()
    del documento["fewshots"]
    caminho.write_text(json.dumps(documento), encoding="utf-8")
    with pytest.raises(ErroDeJudgeCongelado, match="fewshots"):
        carregar_judge_congelado(caminho)


def test_fewshot_sem_id_e_recusado(tmp_path: Path) -> None:
    caminho = tmp_path / "judge_frozen.json"
    caminho.write_text(json.dumps(congelamento(fewshots=[{"entrada": "x"}])), encoding="utf-8")
    with pytest.raises(ErroDeJudgeCongelado, match="fewshot_ids"):
        carregar_judge_congelado(caminho)


def test_rubrica_sha_declarado_tem_de_conferir_com_a_rubrica(tmp_path: Path) -> None:
    caminho = tmp_path / "judge_frozen.json"
    caminho.write_text(
        json.dumps(congelamento(rubrica_sha="0" * 64)), encoding="utf-8"
    )
    with pytest.raises(ErroDeJudgeCongelado, match="rubrica_sha"):
        carregar_judge_congelado(caminho)


def test_judge_model_invalido_e_recusado_nomeando_o_campo(tmp_path: Path) -> None:
    # O sha não é calculável sobre um snapshot inválido, então o documento leva um `sha256` de
    # enchimento: o `judge_model` é validado ANTES da conferência do sha, e é ele que reprova.
    documento = congelamento()
    documento["judge_model"] = {"model_id": "só isso"}
    documento["sha256"] = "0" * 64
    caminho = tmp_path / "judge_frozen.json"
    caminho.write_text(json.dumps(documento), encoding="utf-8")
    with pytest.raises(ErroDeJudgeCongelado, match="judge_model"):
        carregar_judge_congelado(caminho)


def test_assinar_snapshot_invalido_nao_vaza_excecao_do_pydantic(tmp_path: Path) -> None:
    """`sha_do_judge` é o que o script de congelamento da T23 chama para gravar o `sha256`.

    Um snapshot incompleto ali tem de produzir uma frase sobre `judge_model`, não um stack
    trace do Pydantic sobre `served_by` — congelar o judge é o ato que menos pode falhar de
    forma obscura.
    """
    documento = congelamento()
    documento["judge_model"] = {"model_id": "só isso"}
    with pytest.raises(ErroDeJudgeCongelado, match="judge_model"):
        sha_do_judge(documento)


def test_assinar_documento_incompleto_diz_o_que_falta() -> None:
    documento = congelamento()
    del documento["rubrica"]
    with pytest.raises(ErroDeJudgeCongelado, match="rubrica"):
        sha_do_judge(documento)


def test_arquivo_que_nao_e_json_nao_vira_stack_trace(tmp_path: Path) -> None:
    caminho = tmp_path / "judge_frozen.json"
    caminho.write_text("isto não é json", encoding="utf-8")
    with pytest.raises(ErroDeJudgeCongelado, match="não é JSON válido"):
        carregar_judge_congelado(caminho)


# ---------------------------------------------------------------------------
# O campo `judge:` no YAML da bateria
# ---------------------------------------------------------------------------


@pytest.fixture
def corpus(tmp_path: Path) -> dict[str, Any]:
    diretorio = tmp_path / "cenarios"
    diretorio.mkdir(parents=True, exist_ok=True)
    (diretorio / "cen_a.yaml").write_text(
        yaml.safe_dump(
            {"id": "cen_a", "split": "test", "ambiente": {"env_seed": "s001"}},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return carregar_corpus_executavel(diretorio)


@pytest.fixture
def variantes() -> dict[str, Any]:
    return carregar_variantes()


AUSENTE = object()


def escrever_bateria(tmp_path: Path, judge: Any = AUSENTE) -> Path:
    corpo: dict[str, Any] = {
        "experiment_id": "exp_judge",
        "saida": str(tmp_path / "runs"),
        "cenarios": {"ids": ["cen_a"]},
        "modelos": {
            "m1": {
                "model_id": "modelo-de-teste",
                "served_by": "lmstudio",
                "quantization": "q4",
                "temperature": 0.0,
                "max_tokens": 512,
                "structured_output": "json_schema",
                "context_window": 8192,
            }
        },
        "variantes": ["base"],
        "sample_seeds": [1],
        "paralelismo": 1,
    }
    if judge is not AUSENTE:
        corpo["judge"] = judge
    caminho = tmp_path / "bateria.yaml"
    caminho.write_text(yaml.safe_dump(corpo, allow_unicode=True), encoding="utf-8")
    return caminho


def carregar(tmp_path: Path, corpus, variantes, judge: Any = AUSENTE):
    return carregar_bateria(
        escrever_bateria(tmp_path, judge),
        corpus=corpus,
        variantes_disponiveis=variantes,
    )


def test_bateria_sem_campo_judge_nao_carrega(tmp_path, corpus, variantes) -> None:
    """A ausência é erro em TODA bateria — é a decisão de desenho da R4.

    Se a exigência valesse só para as cinco finais (reconhecidas por nome de arquivo ou por
    `experiment_id`), o YAML da principal continuaria sem dizer nada e "esqueci de declarar"
    voltaria a ser indistinguível de "esta bateria não precisa". A mensagem oferece as duas
    saídas justamente para que ninguém precise adivinhar qual delas é a sua.
    """
    with pytest.raises(ErroDeBateria) as erro:
        carregar(tmp_path, corpus, variantes)

    mensagem = str(erro.value)
    assert "falta `judge:`" in mensagem
    assert "configs/judge_frozen.json" in mensagem
    assert "sem_congelamento" in mensagem
    assert "Omitir não é uma terceira opção" in mensagem


def test_judge_apontando_para_arquivo_inexistente_nao_carrega(
    tmp_path, corpus, variantes
) -> None:
    with pytest.raises(ErroDeBateria) as erro:
        carregar(tmp_path, corpus, variantes, judge=str(tmp_path / "nao_existe.json"))

    mensagem = str(erro.value)
    assert "bateria.yaml" in mensagem
    assert "nao_existe.json" in mensagem
    assert "não existe" in mensagem


def test_judge_com_sha_que_nao_confere_nao_carrega(tmp_path, corpus, variantes) -> None:
    documento = congelamento()
    documento["prompt"] = documento["prompt"] + " (editado depois de congelar)"
    caminho = tmp_path / "judge_frozen.json"
    caminho.write_text(json.dumps(documento), encoding="utf-8")

    with pytest.raises(ErroDeBateria, match="não confere com o conteúdo"):
        carregar(tmp_path, corpus, variantes, judge=str(caminho))


def test_judge_congelado_valido_chega_a_bateria(tmp_path, corpus, variantes) -> None:
    caminho = escrever_congelamento(tmp_path)
    bateria = carregar(tmp_path, corpus, variantes, judge=str(caminho))

    assert isinstance(bateria.judge, JudgeCongelado)
    assert bateria.judge.sha256 == congelamento()["sha256"]


def test_caminho_relativo_do_judge_se_resolve_contra_a_raiz_do_repo(
    tmp_path, corpus, variantes, monkeypatch
) -> None:
    """`judge: configs/judge_frozen.json` é relativo ao repo, como `arquivo_de_variantes`.

    O caminho é fixo e não se negocia por bateria (cabeçalho dos cinco manifestos), então ele
    tem de significar a mesma coisa venha o YAML de onde vier.
    """
    raiz = tmp_path / "repo"
    (raiz / "configs").mkdir(parents=True)
    escrever_congelamento(raiz / "configs")
    monkeypatch.setattr("tapieval.runner.matriz.RAIZ_DO_REPO", raiz)

    bateria = carregar(tmp_path, corpus, variantes, judge="configs/judge_frozen.json")
    assert isinstance(bateria.judge, JudgeCongelado)


def test_a_dispensa_exige_o_motivo_por_escrito(tmp_path, corpus, variantes) -> None:
    """É o motivo que separa a decisão do esquecimento: ninguém escreve uma frase sem querer."""
    for vazio in ({"sem_congelamento": ""}, {"sem_congelamento": "   "}):
        with pytest.raises(ErroDeBateria, match="esqueci de declarar"):
            carregar(tmp_path, corpus, variantes, judge=vazio)


def test_a_dispensa_com_motivo_carrega_e_guarda_o_motivo(tmp_path, corpus, variantes) -> None:
    bateria = carregar(
        tmp_path,
        corpus,
        variantes,
        judge={"sem_congelamento": "piloto, anterior ao congelamento da T23"},
    )
    assert bateria.judge == DispensaDeCongelamento("piloto, anterior ao congelamento da T23")


def test_forma_desconhecida_de_judge_e_recusada(tmp_path, corpus, variantes) -> None:
    with pytest.raises(ErroDeBateria, match="sem_congelamento"):
        carregar(tmp_path, corpus, variantes, judge={"congelado": False})
    with pytest.raises(ErroDeBateria):
        carregar(tmp_path, corpus, variantes, judge=17)


# ---------------------------------------------------------------------------
# O que vai para o `manifest.json`
# ---------------------------------------------------------------------------


def test_o_congelamento_lido_vira_o_registro_do_manifesto(tmp_path: Path) -> None:
    judge = carregar_judge_congelado(escrever_congelamento(tmp_path))
    registro = judge_do_manifesto(judge)

    assert registro.congelado is True
    assert registro.sha256 == judge.sha256
    assert registro.scorer_version == "v2"
    assert registro.fewshot_ids == ("fs01", "fs04")
    assert registro.judge_model is not None
    assert registro.motivo_da_dispensa is None


def test_a_dispensa_vira_registro_de_manifesto_com_o_motivo() -> None:
    registro = judge_do_manifesto(DispensaDeCongelamento("bateria de dimensionamento"))
    assert registro.congelado is False
    assert registro.motivo_da_dispensa == "bateria de dimensionamento"
    assert registro.sha256 is None


def test_o_registro_do_manifesto_nao_mistura_os_dois_casos() -> None:
    from tapieval.runner.manifesto import JudgeDoManifesto

    with pytest.raises(ValueError, match="motivo_da_dispensa"):
        JudgeDoManifesto(congelado=False)
    with pytest.raises(ValueError, match="sha256"):
        JudgeDoManifesto(congelado=True)


# ---------------------------------------------------------------------------
# Os manifestos de verdade, em `configs/`
# ---------------------------------------------------------------------------

BATERIAS_FINAIS = ("principal", "mutantes", "metamorfica", "ambiente", "referencia")
BATERIAS_SEM_CONGELAMENTO = (
    "piloto",
    "piloto_a18",
    "piloto_a18b",
    "calibracao",
)


@pytest.mark.parametrize("nome", BATERIAS_FINAIS)
def test_as_cinco_baterias_finais_declaram_o_judge_congelado_em_campo(nome: str) -> None:
    """O cabeçalho em caixa alta deixou de ser a única defesa: agora é campo, e ele é lido.

    O teste lê o YAML em vez de chamar `carregar_bateria` porque `configs/judge_frozen.json`
    ainda não existe — ele depende da decisão de curadoria (rubrica v1 ou v2) que trava a outra
    metade da T23. O que se prova aqui é a declaração; quando o arquivo existir, o carregador
    prova o resto, e falha ruidosamente se o caminho tiver mudado.
    """
    documento = yaml.safe_load(
        (RAIZ_DO_REPO / "configs" / f"bateria_{nome}.yaml").read_text(encoding="utf-8")
    )
    assert documento["judge"] == "configs/judge_frozen.json"


@pytest.mark.parametrize("nome", BATERIAS_FINAIS)
def test_as_cinco_baterias_finais_morrem_no_carregamento_sem_o_congelamento(nome: str) -> None:
    """Hoje o arquivo não existe — e é exatamente por isso que a bateria não carrega.

    Este teste é a prova de que a exigência é executável e não decorativa: enquanto a T23 não
    congelar a rubrica, `--dry-run` sai em 2 s dizendo o porquê, em vez de a madrugada começar.
    Quando `configs/judge_frozen.json` existir, ele passa a exercitar a conferência do sha.
    """
    caminho = RAIZ_DO_REPO / "configs" / f"bateria_{nome}.yaml"
    congelado = RAIZ_DO_REPO / "configs" / "judge_frozen.json"

    if congelado.exists():
        bateria = carregar_bateria(caminho)
        assert isinstance(bateria.judge, JudgeCongelado)
        return

    with pytest.raises(ErroDeBateria) as erro:
        carregar_bateria(caminho)
    assert "judge_frozen.json" in str(erro.value)


@pytest.mark.parametrize("nome", BATERIAS_SEM_CONGELAMENTO)
def test_os_manifestos_de_piloto_e_calibracao_continuam_carregando(nome: str) -> None:
    """Elas rodam sem judge congelado DE PROPÓSITO — e agora dizem isso, em vez de omitir."""
    bateria = carregar_bateria(RAIZ_DO_REPO / "configs" / f"bateria_{nome}.yaml")
    assert isinstance(bateria.judge, DispensaDeCongelamento)
    assert "congelamento" in bateria.judge.motivo
