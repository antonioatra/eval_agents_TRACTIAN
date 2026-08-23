"""T17 — as variantes do experimento e os quatro mutantes.

O QUE ESTES TESTES PROTEGEM
    Um mutante mal configurado não quebra nada: ele roda, produz trace, é pontuado, e sai
    **idêntico à base**. A INS.9 então mede 0% de detecção e o número é lido como *"o
    instrumento não distingue degradação"*, quando o que houve foi *"não havia degradação"*.
    É o formato do X18 — falha da medição que se disfarça de falha do medido — e a única
    defesa é recusar a configuração no carregamento, alto e cedo.

    Por isso a maioria dos testes aqui é sobre o que o carregador RECUSA, não sobre o que ele
    aceita.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from tapieval.mcp.server import RunContext, listar_tools
from tapieval.schema.trace import VariantConfig
from tapieval.sut import variants
from tapieval.sut.variants import (
    CAMINHO_PADRAO,
    ErroDeVariante,
    carregar_variantes,
    mutantes,
)

RAIZ = Path(__file__).resolve().parent.parent

TOOL_DE_QUALIDADE_DE_SINAL = "get_data_quality"


@pytest.fixture(scope="module")
def variantes() -> dict[str, VariantConfig]:
    return carregar_variantes()


@pytest.fixture
def escrever(tmp_path):
    """Escreve um `variants.yaml` mínimo e devolve o caminho."""
    def montar(**corpo_das_variantes) -> Path:
        caminho = tmp_path / "variants.yaml"
        caminho.write_text(yaml.safe_dump({"variantes": corpo_das_variantes}))
        return caminho
    return montar


BASE_MINIMA = {"prompt": "prompts/agente_v1.md"}


# ---------------------------------------------------------------------------
# O que a T17 pede que se prove
# ---------------------------------------------------------------------------


def test_os_quatro_mutantes_existem(variantes):
    assert sorted(mutantes(variantes)) == ["MUT1", "MUT2", "MUT3", "MUT4"]


def test_todo_mutante_declara_descricao_e_classe(variantes):
    for nome, variante in mutantes(variantes).items():
        assert variante.mutacao_descricao, f"{nome} sem `mutacao_descricao`"
        assert variante.classe in {"P", "C"}, f"{nome} com classe {variante.classe!r}"


def test_as_duas_classes_de_falha_tem_ponto_de_apoio(variantes):
    """Requisito de desenho do `METRICAS §7.1`, e a razão de o MUT4 existir.

    Se os quatro mutantes fossem de processo, a curva de detecção da classe C não teria de
    onde partir e a INS.9 responderia por metade do espaço de falhas achando que responde
    pelo todo.
    """
    classes = [v.classe for v in mutantes(variantes).values()]
    assert classes.count("P") >= 1
    assert classes.count("C") >= 1, "sem mutante de conteúdo a curva da classe C fica vazia"


def test_sob_mut1_a_tool_de_qualidade_de_sinal_some_do_catalogo(variantes):
    """A prova nomeada da T17. Sob MCP a tool não é escondida — ela não existe.

    Vale checar contra a base no mesmo teste: se a tool sumisse dos dois, o que estaria
    quebrado seria o catálogo, não o mutante.
    """
    da_base = {t.name for t in listar_tools(_ctx(variantes["base"]))}
    do_mut1 = {t.name for t in listar_tools(_ctx(variantes["MUT1"]))}

    assert TOOL_DE_QUALIDADE_DE_SINAL in da_base
    assert TOOL_DE_QUALIDADE_DE_SINAL not in do_mut1
    assert do_mut1 == da_base - {TOOL_DE_QUALIDADE_DE_SINAL}, "MUT1 só pode tirar essa tool"


def _ctx(variante: VariantConfig) -> RunContext:
    """Um contexto só para `listar_tools`, que lê `tools_ocultas` e mais nada.

    `cliente` fica com um objeto vazio em vez de `None`: o campo é obrigatório e o catálogo
    não o toca, então mentir o tipo aqui não compraria nada.
    """
    return RunContext(
        run_id="run_teste",
        cliente=SimpleNamespace(seed=None),
        tools_ocultas=variante.tools_ocultas,
    )


# ---------------------------------------------------------------------------
# Cada mutante degrada o que diz degradar
# ---------------------------------------------------------------------------


def test_cada_mutante_difere_da_base_em_algo(variantes):
    """Um mutante que não difere da base é uma segunda base com outro nome."""
    base = variantes["base"]
    comparaveis = ("tools_ocultas", "exige_citacao", "max_tool_calls", "prompt_sha",
                   "max_iterations", "hidratacao", "tools_visiveis")
    for nome, variante in mutantes(variantes).items():
        diferencas = [
            campo for campo in comparaveis
            if getattr(variante, campo) != getattr(base, campo)
        ]
        assert diferencas, f"{nome} é idêntico à base: a INS.9 mediria 0% por construção"


def test_mut3_corta_o_budget_abaixo_do_que_o_corpus_exige(variantes):
    """3 chamadas não cobrem nenhum cenário: os gabaritos pedem de 2 a 6 tools."""
    assert variantes["MUT3"].max_tool_calls == 3
    assert variantes["MUT3"].max_tool_calls < variantes["base"].max_tool_calls


def test_mut2_muda_o_prompt_renderizado_e_nao_o_template(variantes):
    """`exige_citacao` governa um bloco injetado, então o `prompt_sha` é igual ao da base.

    Está certo que seja igual: o hash é do TEMPLATE. Este teste existe para que ninguém
    "conserte" essa igualdade achando que é bug — o que distingue MUT2 é o campo.
    """
    assert variantes["MUT2"].prompt_sha == variantes["base"].prompt_sha
    assert variantes["MUT2"].exige_citacao is False
    assert variantes["base"].exige_citacao is True


def test_mut4_tem_template_proprio_e_ele_e_a_base_mais_a_mutacao(variantes):
    """MUT4 é de conteúdo: nenhuma capacidade sai, só a instrução muda.

    O template tem de conter a base inteira — se divergir em outra coisa, MUT4 deixa de ser
    um mutante de uma variável só e vira duas mudanças que ninguém consegue separar.
    """
    assert variantes["MUT4"].prompt_sha != variantes["base"].prompt_sha
    assert variantes["MUT4"].tools_ocultas == frozenset()
    assert variantes["MUT4"].max_tool_calls == variantes["base"].max_tool_calls

    base_texto = (RAIZ / "prompts" / "agente_v1.md").read_text(encoding="utf-8")
    mut4_texto = (RAIZ / "prompts" / "agente_mut4.md").read_text(encoding="utf-8")
    faltando = [linha for linha in base_texto.splitlines() if linha.strip()
                and linha not in mut4_texto]
    assert not faltando, f"MUT4 perdeu linhas da base: {faltando[:3]}"
    assert "baseline" in mut4_texto


# ---------------------------------------------------------------------------
# O que o carregador recusa — o grosso da defesa
# ---------------------------------------------------------------------------


def test_campo_com_grafia_errada_e_recusado(escrever):
    """`tools_oculta` seria descartado pelo Pydantic e MUT1 sairia igual à base."""
    caminho = escrever(base=BASE_MINIMA, MUT1={
        **BASE_MINIMA, "mutante": True, "classe": "P", "mutacao_descricao": "x",
        "tools_oculta": ["get_data_quality"],
    })
    with pytest.raises(ErroDeVariante, match="desconhecido"):
        carregar_variantes(caminho)


def test_tool_inexistente_em_tools_ocultas_e_recusada(escrever):
    """Nome errado remove zero tools, e o mutante de capacidade vira cópia da base."""
    caminho = escrever(base=BASE_MINIMA, MUT1={
        **BASE_MINIMA, "mutante": True, "classe": "P", "mutacao_descricao": "x",
        "tools_ocultas": ["get_dataquality"],
    })
    with pytest.raises(ErroDeVariante, match="não existe"):
        carregar_variantes(caminho)


def test_variant_id_declarado_no_corpo_e_recusado(escrever):
    caminho = escrever(base={**BASE_MINIMA, "variant_id": "outra"})
    with pytest.raises(ErroDeVariante, match="vem da chave"):
        carregar_variantes(caminho)


def test_mutante_sem_classe_e_recusado(escrever):
    caminho = escrever(base=BASE_MINIMA, MUT9={
        **BASE_MINIMA, "mutante": True, "mutacao_descricao": "x"
    })
    with pytest.raises(ErroDeVariante, match="classe"):
        carregar_variantes(caminho)


def test_mutante_sem_descricao_e_recusado(escrever):
    caminho = escrever(base=BASE_MINIMA, MUT9={
        **BASE_MINIMA, "mutante": True, "classe": "P"
    })
    with pytest.raises(ErroDeVariante, match="mutacao_descricao"):
        carregar_variantes(caminho)


def test_variante_nao_mutante_nao_pode_alegar_degradacao(escrever):
    """Senão a base entraria na conta da INS.9 como se fosse defeito."""
    caminho = escrever(base={**BASE_MINIMA, "classe": "P"})
    with pytest.raises(ErroDeVariante, match="não é mutante"):
        carregar_variantes(caminho)


def test_prompt_ausente_e_recusado(escrever):
    caminho = escrever(base={})
    with pytest.raises(ErroDeVariante, match="prompt"):
        carregar_variantes(caminho)


def test_prompt_que_nao_existe_no_disco_e_recusado(escrever):
    caminho = escrever(base={"prompt": "prompts/nao_existe.md"})
    with pytest.raises(ErroDeVariante, match="inexistente"):
        carregar_variantes(caminho)


def test_configuracao_sem_base_e_recusada(escrever):
    caminho = escrever(MUT1={
        **BASE_MINIMA, "mutante": True, "classe": "P", "mutacao_descricao": "x"
    })
    with pytest.raises(ErroDeVariante, match="base"):
        carregar_variantes(caminho)


def test_arquivo_inexistente_e_recusado(tmp_path):
    with pytest.raises(ErroDeVariante, match="não encontrada"):
        carregar_variantes(tmp_path / "nao_existe.yaml")


# ---------------------------------------------------------------------------
# O hash é derivado, nunca escrito
# ---------------------------------------------------------------------------


def test_prompt_sha_vem_do_arquivo_e_acompanha_a_edicao(tmp_path, monkeypatch):
    """Hash escrito à mão envelhece em silêncio e rotula a coluna com o prompt errado."""
    monkeypatch.setattr(variants, "RAIZ_DO_REPO", tmp_path)
    prompt = tmp_path / "p.md"
    caminho = tmp_path / "variants.yaml"
    caminho.write_text(yaml.safe_dump({"variantes": {"base": {"prompt": "p.md"}}}))

    prompt.write_text("primeira versão")
    antes = carregar_variantes(caminho)["base"].prompt_sha

    prompt.write_text("segunda versão")
    assert carregar_variantes(caminho)["base"].prompt_sha != antes


def test_o_yaml_versionado_carrega(variantes):
    """O arquivo real, não um fixture: é ele que a bateria vai usar."""
    assert CAMINHO_PADRAO.exists()
    assert "base" in variantes
    assert len(variantes) == 5
