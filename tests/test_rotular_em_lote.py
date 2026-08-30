"""T22 — o driver que conduz a rotulagem cega por arquivo (`scripts/rotular_em_lote.py`).

O QUE ESTES TESTES SUSTENTAM
    `tests/test_labeling.py` já prova as propriedades do INSTRUMENTO: a cegueira estrutural do
    pacote, o `None`-nunca-`False`, a amostra congelada, a retomada. Aqui o assunto é a porta
    nova que o driver abre para essas propriedades — e uma porta nova é exatamente onde uma
    garantia estrutural costuma escorrer.

    Três invariantes concentram o valor:

    1. **A varredura de score vale para o script também.** Se ela parasse no pacote, bastaria
       o driver abrir `runs/<id>/scores/` "só para ordenar a fila" e a independência do κ
       morreria sem nada ficar vermelho.
    2. **O índice não vaza o modelo.** `run_id` carrega o `model_key`; usá-lo como chave da
       resposta desfaria a decisão de `apresentar_caso` de esconder o modelo.
    3. **Nada é gravado contra uma fila que mudou, nem contra um índice que não existe.** O
       modo de falha que importa aqui não é o erro barulhento: é o rótulo correto gravado no
       `run_id` errado, que o κ conta como discordância sem ter como saber.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

import rotular_em_lote as mod  # noqa: E402
from tapieval.scoring.gabarito import Cenario  # noqa: E402
from tests.test_labeling import (  # noqa: E402
    _montar_run_dir,
    cenario,  # a fixture, reusada: o driver tem de ver o mesmo caso que a CLI vê
)

__all__ = ["cenario"]

BLOCO = """
caso 1
n3.1: s
n3.2: n
n3.5: p
just: a resposta não contrasta com falhas por desvio
"""


# ---------------------------------------------------------------------------
# Montagem
# ---------------------------------------------------------------------------


def montar(tmp_path: Path, cenario: Cenario, *, seeds: tuple[int, ...] = (11, 12)) -> Any:
    """Um `runs/` de mentira, um `labels/` vazio, e a fila já resolvida."""
    run_dir = _montar_run_dir(
        tmp_path, cenarios=("cen_00",), modelos=("modelo-0", "modelo-1"), seeds=seeds
    )
    labels = tmp_path / "labels"
    itens = mod.fila_pendente(
        run_dir,
        labels,
        n_estimativa=2,
        n_melhoria=0,
        escrever=lambda _: None,
    )
    insumo_de = mod.montador_de_insumo(run_dir, {"cen_00": cenario})
    return run_dir, labels, itens, insumo_de


def gravar(labels: Path, itens, insumo_de, respostas: dict[int, list[str]]) -> int:
    return mod.gravar_lote(
        itens,
        respostas,
        insumo_de=insumo_de,
        rotulador="antonio",
        labels_dir=labels,
        escrever=lambda _: None,
    )


def rotulos(labels: Path) -> list[dict[str, Any]]:
    linhas: list[dict[str, Any]] = []
    for caminho in sorted(labels.glob("humano_*.jsonl")):
        linhas += [
            json.loads(linha)
            for linha in caminho.read_text(encoding="utf-8").splitlines()
            if linha.strip()
        ]
    return linhas


# ---------------------------------------------------------------------------
# 1 · A cegueira atravessa a porta nova
# ---------------------------------------------------------------------------


def _simbolos_e_literais(caminho: Path):
    """Idêntica à de `test_labeling`, e pelo mesmo motivo: a prosa pode falar de score."""
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


def test_o_driver_nao_tem_caminho_de_codigo_que_leia_score():
    """A mesma varredura do pacote, sobre o script. Ver o cabeçalho, invariante 1."""
    ofensores = [
        simbolo
        for simbolo in _simbolos_e_literais(Path(mod.__file__))
        if "score" in simbolo.lower()
    ]
    assert ofensores == [], (
        "o driver ganhou caminho para a saída do judge: "
        f"{ofensores}. A cegueira da INS.6 é estrutural — ver METRICAS §5."
    )


def test_o_texto_apresentado_nao_carrega_modelo_nem_run_id(tmp_path: Path, cenario: Cenario):
    """O índice é opaco de propósito: `run_id` embute o `model_key`."""
    _, _, itens, insumo_de = montar(tmp_path, cenario)

    texto = mod.renderizar(itens, insumo_de=insumo_de, quantos=len(itens))

    for item in itens:
        assert item.candidato.run_id not in texto
        assert item.candidato.model_key not in texto
    assert "caso 1" in texto


# ---------------------------------------------------------------------------
# 2 · O rótulo sai pelo caminho real
# ---------------------------------------------------------------------------


def test_o_rotulo_gravado_passa_pelo_instrumento(tmp_path: Path, cenario: Cenario):
    """Sem reimplementar gravação: os três campos de trace saem `None`, nunca `False`."""
    _, labels, itens, insumo_de = montar(tmp_path, cenario)

    assert gravar(labels, itens, insumo_de, mod.analisar(BLOCO)) == 1

    (linha,) = rotulos(labels)
    assert linha["run_id"] == itens[0].candidato.run_id
    assert linha["rotulador"] == "antonio"
    assert linha["configuracao"] == "cego"
    assert linha["causa_raiz_correta"] is True
    assert linha["mencionou_limitacao_relevante"] is False
    assert linha["responde_a_pergunta"] == "parcial"
    for campo in ("afirmacoes_sem_suporte", "contradiz_evidencia", "recomendou_acao_sem_base"):
        assert linha[campo] is None, f"{campo} veio preenchido no modo cego"


def test_a_justificativa_de_varias_linhas_vira_uma_so():
    """`_perguntar_texto` lê UMA linha. Duas viariam resposta da pergunta seguinte."""
    teclas = mod.analisar("caso 1\nn3.1: n\nn3.2: n\nn3.5: n\njust: primeira\nsegunda\n")

    assert teclas[1][-1] == "primeira segunda"


def test_retomada_nao_reapresenta_o_caso_ja_rotulado(tmp_path: Path, cenario: Cenario):
    run_dir, labels, itens, insumo_de = montar(tmp_path, cenario)
    gravar(labels, itens, insumo_de, mod.analisar(BLOCO))
    ja_rotulado = itens[0].candidato.run_id

    depois = mod.fila_pendente(
        run_dir, labels, n_estimativa=2, n_melhoria=0, escrever=lambda _: None
    )

    assert ja_rotulado not in [item.candidato.run_id for item in depois]
    assert len(depois) == len(itens) - 1


# ---------------------------------------------------------------------------
# 3 · O que precisa parar ANTES de gravar
# ---------------------------------------------------------------------------


def test_indice_fora_da_fila_para_o_lote_inteiro(tmp_path: Path, cenario: Cenario):
    """Um índice inválido não pode levar junto os blocos válidos do mesmo arquivo."""
    _, labels, itens, insumo_de = montar(tmp_path, cenario)
    respostas = mod.analisar(BLOCO + "\ncaso 99\nn3.1: s\nn3.2: s\nn3.5: s\njust: x\n")

    with pytest.raises(mod.ErroDeLote, match="fora da fila"):
        gravar(labels, itens, insumo_de, respostas)

    assert rotulos(labels) == [], "gravou parte do lote antes de recusar o resto"


def test_a_fila_que_mudou_recusa_a_gravacao(tmp_path: Path, cenario: Cenario):
    """A conferência que impede o rótulo certo de cair no `run_id` errado."""
    run_dir, labels, itens, _ = montar(tmp_path, cenario)
    arquivo = tmp_path / "respostas.txt"
    arquivo.write_text(BLOCO, encoding="utf-8")

    codigo = mod.main(
        [
            "gravar",
            "--run-dir", str(run_dir),
            "--labels-dir", str(labels),
            "--respostas", str(arquivo),
            "--rotulador", "antonio",
            "--fila", "impressaovelha",
            "--n-estimativa", "2",
            "--n-melhoria", "0",
        ]
    )

    assert codigo == 2
    assert rotulos(labels) == []


def test_a_impressao_da_fila_muda_quando_a_fila_muda(tmp_path: Path, cenario: Cenario):
    _, labels, itens, insumo_de = montar(tmp_path, cenario)
    antes = mod.impressao_da_fila(itens)

    gravar(labels, itens, insumo_de, mod.analisar(BLOCO))

    assert mod.impressao_da_fila(itens[1:]) != antes


@pytest.mark.parametrize(
    ("texto", "erro"),
    [
        ("caso 1\nn3.1: talvez\nn3.2: n\nn3.5: p\njust: x\n", "não é s / n"),
        ("caso 1\nn3.1: s\nn3.2: n\nn3.5: p\n", "falta `just`"),
        ("caso 1\nn3.1: s\nn3.2: n\nn3.5: p\njust:   \n", "justificativa é obrigatória"),
        ("caso 1\nn3.1: s\nn3.1: n\nn3.2: n\nn3.5: p\njust: x\n", "repetida"),
        ("caso 1\nn3.1: s\nn3.2: n\nn3.5: p\njust: x\ncaso 1\nn3.1: n\n", "duas vezes"),
        ("n3.1: s\n", "antes de qualquer"),
        ("caso 1\nn9.9: s\n", "não é `n3.1`"),
        ("", "nenhum bloco"),
    ],
)
def test_resposta_fora_de_formato_e_erro_nomeado(texto: str, erro: str):
    """Valor recusado pela CLI seria pergunta repetida, e pergunta repetida desloca o rótulo
    inteiro por um campo — com todos preenchidos e nenhum no lugar. Por isso valida aqui."""
    with pytest.raises(mod.ErroDeLote, match=erro):
        mod.analisar(texto)


def test_bloco_curto_explode_em_vez_de_gravar_pela_metade(tmp_path: Path, cenario: Cenario):
    """`RoteiroEsgotado` não herda de `EOFError` — herdasse, `rodar_sessao` encerraria calada."""
    _, labels, itens, insumo_de = montar(tmp_path, cenario)

    with pytest.raises(mod.RoteiroEsgotado, match="não tem mais resposta"):
        gravar(labels, itens, insumo_de, {1: ["r", "s"]})

    assert rotulos(labels) == []


def test_com_trace_nao_e_oferecido():
    """A configuração que não sustenta o κ fica recusada por nome, não meio-suportada."""
    assert mod.CONFIGURACAO == "cego"
    assert "--configuracao" not in mod.construir_parser().format_help()


def test_o_run_id_da_fila_nao_aparece_no_que_o_mostrar_imprime(
    tmp_path: Path,
    cenario: Cenario,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
):
    """A impressão da fila é um sha justamente para não ser a lista de `run_id`."""
    run_dir, labels, itens, _ = montar(tmp_path, cenario)
    monkeypatch.setattr(mod, "carregar_cenarios", lambda: {"cen_00": cenario})

    codigo = mod.main(
        [
            "mostrar",
            "--run-dir", str(run_dir),
            "--labels-dir", str(labels),
            "--n-estimativa", "2",
            "--n-melhoria", "0",
        ]
    )

    saida = capsys.readouterr().out
    assert codigo == 0
    assert mod.impressao_da_fila(itens) in saida
    for item in itens:
        assert item.candidato.run_id not in saida
        assert item.candidato.model_key not in saida


def test_o_mostrar_imprime_a_rubrica_antes_dos_casos(
    tmp_path: Path,
    cenario: Cenario,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
):
    """O conserto de 30/08 vale para quem rotula fora do terminal, que é o ponto do driver."""
    run_dir, labels, _, _ = montar(tmp_path, cenario)
    monkeypatch.setattr(mod, "carregar_cenarios", lambda: {"cen_00": cenario})

    mod.main(
        [
            "mostrar",
            "--run-dir", str(run_dir),
            "--labels-dir", str(labels),
            "--n-estimativa", "2",
            "--n-melhoria", "0",
        ]
    )

    saida = capsys.readouterr().out
    protocolo = mod.protocolo_de_rotulagem(mod.CONFIGURACAO, mod.RUBRICA_PADRAO)
    assert protocolo in saida
    assert saida.index(protocolo) < saida.index("caso 1"), (
        "a rubrica saiu depois do primeiro caso — quem lê de cima para baixo já julgou"
    )
