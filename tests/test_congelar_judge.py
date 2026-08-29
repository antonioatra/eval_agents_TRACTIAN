"""T23 — o script que produz o `configs/judge_frozen.json`.

O QUE ESTES TESTES SUSTENTAM
    `tests/test_judge_congelado.py` (R4) testa o CONTRATO do arquivo com um congelamento de
    fixture. Aqui o assunto é outro: o arquivo de verdade, montado a partir dos prompts e dos
    few-shots que estão no repositório. As duas metades da T23 se encontram no round-trip —
    o que este script grava tem de carregar pelo caminho que a bateria usa.

    O teste que mais importa é o do `prompt` com as DUAS configurações. Congelar só o cego
    seria um arquivo perfeitamente válido para o carregador da R4, com sha conferindo, e ainda
    assim deixaria `judge_trace_v2.md` livre para mudar sem que nada ficasse vermelho — a
    falha que o congelamento existe para impedir, entrando pela porta que ficou aberta.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

import congelar_judge  # noqa: E402
from tapieval.runner.judge_congelado import (  # noqa: E402
    carregar_judge_congelado,
    sha_da_rubrica,
    sha_do_judge,
)
from tapieval.scoring.n3 import RUBRICA_PADRAO, TEMPLATE_POR_CONFIGURACAO  # noqa: E402

QUANDO = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)


@pytest.fixture
def documento() -> dict:
    return congelar_judge.montar(RUBRICA_PADRAO, quando=QUANDO)


def gravar(documento: dict, destino: Path) -> Path:
    destino.write_text(
        json.dumps(documento, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return destino


# ---------------------------------------------------------------------------
# O round-trip: as duas metades da T23 se encontram aqui
# ---------------------------------------------------------------------------


def test_o_que_o_script_grava_carrega_pelo_caminho_da_bateria(documento, tmp_path: Path):
    """`sha_do_judge` e `carregar_judge_congelado` são funções diferentes, e é a segunda que
    a bateria chama. Um congelamento que só passa pela primeira morre às 4 da manhã."""
    lido = carregar_judge_congelado(gravar(documento, tmp_path / "judge_frozen.json"))

    assert lido.scorer_version == RUBRICA_PADRAO
    assert lido.sha256 == documento["sha256"]
    assert lido.congelado_em == QUANDO
    assert lido.fewshot_origem == "escritos_a_mao"
    assert lido.judge_model.temperature == 0.0


def test_congela_os_quatro_fewshots_do_repositorio_com_id(documento):
    """Os ids vão para `ScorerVersion.fewshot_ids`, que é o que permite dizer qual exemplo
    mudou entre duas versões do judge."""
    ids = [exemplo["id"] for exemplo in documento["fewshots"]]

    assert ids == sorted(ids), "a ordem tem de ser a de `carregar_fewshots`, que é por nome"
    assert len(ids) == 4
    assert all(exemplo["origem"] == "escrito_a_mao" for exemplo in documento["fewshots"])


# ---------------------------------------------------------------------------
# As duas configurações — o buraco que este script existe para não deixar
# ---------------------------------------------------------------------------


def test_o_prompt_congelado_carrega_as_duas_configuracoes(documento):
    """`METRICAS §4` define duas configurações, e as duas produzem N3 na bateria."""
    for configuracao in ("cego", "com_trace"):
        assert f"<<<{configuracao}>>>" in documento["prompt"]

    trace = (RAIZ / "prompts" / TEMPLATE_POR_CONFIGURACAO[RUBRICA_PADRAO]["com_trace"]).read_text(
        encoding="utf-8"
    )
    assert trace in documento["prompt"]


def test_mexer_no_template_com_trace_muda_o_sha(documento):
    """A prova de que a segunda configuração está de fato assinada.

    Se o `prompt` carregasse só o cego, este sha não se moveria — e o arquivo continuaria
    válido para o carregador da R4, com o judge com trace livre para mudar em silêncio.
    """
    adulterado = dict(documento)
    adulterado["prompt"] = documento["prompt"].replace(
        "<<<com_trace>>>", "<<<com_trace>>>\nUma frase que ninguém escreveu."
    )

    assert adulterado["prompt"] != documento["prompt"]
    assert sha_do_judge(adulterado) != sha_do_judge(documento)


def test_trocar_a_ordem_das_configuracoes_muda_o_sha(monkeypatch):
    """A ordem é parte da serialização assinada, e por isso ela é constante e não um `set`."""
    partes = {"cego": "A", "com_trace": "B"}
    normal = congelar_judge.serializar(partes)

    monkeypatch.setattr(congelar_judge, "CONFIGURACOES", ("com_trace", "cego"))
    assert congelar_judge.serializar(partes) != normal


# ---------------------------------------------------------------------------
# O recorte da rubrica
# ---------------------------------------------------------------------------


def test_a_rubrica_e_parte_do_prompt_e_nao_o_prompt_inteiro(documento):
    """Se os dois campos carregassem o mesmo texto, os dois shas mudariam sempre juntos e o
    `rubrica_sha` não rastrearia nada — que é a única coisa que ele existe para fazer."""
    assert documento["rubrica"] != documento["prompt"]
    assert len(documento["rubrica"]) < len(documento["prompt"])
    assert documento["rubrica_sha"] == sha_da_rubrica(documento["rubrica"])
    assert documento["rubrica_sha"] != sha_da_rubrica(documento["prompt"])


def test_a_rubrica_recortada_traz_os_dois_campos_que_a_v2_reescreveu(documento):
    """O recorte tem de conter o que a T21 de fato mexeu, senão ele está no lugar errado."""
    for campo in ("causa_raiz_correta", "mencionou_limitacao_relevante", "responde_a_pergunta"):
        assert f"`{campo}`" in documento["rubrica"]

    # E não pode trazer o enquadramento nem os marcadores do caso, que são prompt e não rubrica.
    assert "{resposta}" not in documento["rubrica"]
    assert "{fewshots}" not in documento["rubrica"]


def test_template_sem_o_cabecalho_da_rubrica_e_erro_e_nao_recorte_vazio():
    """Um sha do texto vazio confere sempre, contra qualquer rubrica."""
    with pytest.raises(SystemExit, match="não tem a seção"):
        congelar_judge.recortar_rubrica("um prompt sem seções", "cego")


def test_template_sem_o_fim_da_rubrica_e_erro():
    with pytest.raises(SystemExit, match="não dá para saber onde a rubrica termina"):
        congelar_judge.recortar_rubrica("## As perguntas\n\n`campo` — pergunta fechada", "cego")


# ---------------------------------------------------------------------------
# Recongelar é ato deliberado
# ---------------------------------------------------------------------------


def test_nao_sobrescreve_um_congelamento_existente_sem_forcar(tmp_path: Path, capsys):
    destino = tmp_path / "judge_frozen.json"
    destino.write_text("{}", encoding="utf-8")

    sys.argv = ["congelar_judge.py", "--caminho", str(destino)]
    assert congelar_judge.main() == 2
    assert destino.read_text(encoding="utf-8") == "{}"
    assert "já existe" in capsys.readouterr().out


def test_forcar_regrava(tmp_path: Path):
    destino = tmp_path / "judge_frozen.json"
    destino.write_text("{}", encoding="utf-8")

    sys.argv = ["congelar_judge.py", "--caminho", str(destino), "--forcar"]
    assert congelar_judge.main() == 0
    assert carregar_judge_congelado(destino).scorer_version == RUBRICA_PADRAO
