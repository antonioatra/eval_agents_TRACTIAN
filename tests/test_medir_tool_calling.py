"""T0b — o julgamento de uma chamada emitida, e o contrato do script com o split do corpus.

A T0b é um portão de viabilidade: o número que ela produz decide se o cronograma inteiro segue
com este par de modelos. Um julgador que confunda "chamou tool inexistente" com "chamou tool
certa com argumento errado" faria esse portão abrir ou fechar pelo motivo errado, e não há
segunda medição para pegar isso — o documento é escrito uma vez e citado depois.

O teste do split é o que mais importa aqui. `CENARIOS_DE_DEV` é uma lista escrita à mão no
script; se alguém mover um cenário de dev para test no corpus e a lista não acompanhar, a T0b
passa a mandar mensagem de test para o modelo em silêncio, e o pré-registro se perde sem que
nada fique vermelho.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

RAIZ = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def medidor():
    spec = importlib.util.spec_from_file_location(
        "medir_tool_calling", RAIZ / "scripts" / "medir_tool_calling.py"
    )
    modulo = importlib.util.module_from_spec(spec)
    sys.modules["medir_tool_calling"] = modulo
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture(scope="module")
def operacoes(medidor):
    from tapieval.mcp.tools import carregar_operacoes

    return dict(carregar_operacoes())


@pytest.fixture
def cenario(medidor):
    return medidor.CenarioDeDev(
        id="cen_falso",
        solicitacao="o ativo está estranho",
        contexto="asset_id: asset_H110",
        esperadas=frozenset({"get_asset", "get_baseline"}),
        aceitaveis=frozenset({"get_current_user"}),
        args_esperados={"get_asset": {"asset_id": "asset_H110"}},
    )


def _chamada(medidor, nome, args, crus="", erro=None):
    return medidor.ChamadaEmitida(nome=nome, args=args, args_crus=crus, erro_de_parse=erro)


def test_tool_inventada_nao_conta_como_argumento_invalido(medidor, cenario, operacoes):
    """Função que não existe é uma falha de seleção, não de argumento.

    Se `args_validos` viesse `False` aqui, a tool inventada entraria no denominador de acurácia
    de argumento e afundaria uma métrica que ela não mede.
    """
    j = medidor.julgar(_chamada(medidor, "diagnosticar_ativo", {"asset_id": "asset_H110"}),
                       cenario, operacoes)
    assert j.existe_no_catalogo is False
    assert j.args_validos is None
    assert j.tolerada is False


def test_argumentos_ilegiveis_ficam_separados_de_argumentos_errados(medidor, cenario, operacoes):
    """`parse_erro` e "chamou errado" são confounds diferentes e não podem colapsar num só."""
    j = medidor.julgar(
        _chamada(medidor, "get_asset", None, crus="{asset_id:", erro="JSON inválido: ..."),
        cenario, operacoes,
    )
    assert j.existe_no_catalogo is True
    assert j.esperada is True
    assert j.args_validos is None, "ilegível não é o mesmo que inválido"
    assert j.args_batem is None
    assert "JSON inválido" in j.detalhe


def test_chamada_certa_com_argumento_certo(medidor, cenario, operacoes):
    j = medidor.julgar(_chamada(medidor, "get_asset", {"asset_id": "asset_H110"}),
                       cenario, operacoes)
    assert (j.esperada, j.tolerada, j.args_validos, j.args_batem) == (True, True, True, True)


def test_argumento_valido_no_schema_mas_com_o_ativo_errado(medidor, cenario, operacoes):
    """O eixo da H2: acertar a função não é acertar a chamada.

    `asset_G999` passa no schema — é string — e mesmo assim é a pergunta errada.
    """
    j = medidor.julgar(_chamada(medidor, "get_asset", {"asset_id": "asset_G999"}),
                       cenario, operacoes)
    assert j.args_validos is True
    assert j.args_batem is False
    assert "asset_G999" in j.detalhe


def test_tool_aceitavel_nao_e_esperada_mas_tambem_nao_e_ruido(medidor, cenario, operacoes):
    j = medidor.julgar(_chamada(medidor, "get_current_user", {}), cenario, operacoes)
    assert j.tolerada is True
    assert j.esperada is False


def test_tool_sem_gabarito_de_args_nao_inventa_veredito(medidor, cenario, operacoes):
    """`get_baseline` é esperada mas não tem `args_esperados` neste cenário.

    `args_batem` tem de ficar `None` — não `False`. Contar como erro puniria o modelo por uma
    lacuna do gabarito, que é exatamente o padrão que a leva de 19/08 passou a corrigir: não
    confundir "não houve falha" com "não foi medido".
    """
    j = medidor.julgar(_chamada(medidor, "get_baseline", {"asset_id": "asset_H110"}),
                       cenario, operacoes)
    assert j.esperada is True
    assert j.args_batem is None


def test_argumento_obrigatorio_faltando_reprova_no_schema(medidor, cenario, operacoes):
    j = medidor.julgar(_chamada(medidor, "get_asset", {}), cenario, operacoes)
    assert j.args_validos is False
    assert j.detalhe


def test_a_lista_de_dev_do_script_bate_com_o_corpus(medidor):
    """Contrato: o script só pode olhar o que o corpus marca como `split: dev`.

    Test fica lacrado até o judge congelar. Se este teste ficar vermelho, é o corpus que mudou
    de split — e a T0b tem de ser reexecutada, não remendada.
    """
    no_corpus = {
        caminho.stem
        for caminho in (RAIZ / "scenarios").glob("*.yaml")
        if (yaml.safe_load(caminho.read_text()) or {}).get("split") == "dev"
    }
    assert set(medidor.CENARIOS_DE_DEV) == no_corpus


def test_carregar_cenarios_traz_gabarito_suficiente_para_as_20_sondas(medidor):
    """A T0b pede 20 solicitações; os 6 de dev têm de sustentar isso sozinhos."""
    cenarios = medidor.carregar_cenarios_de_dev()
    assert len(cenarios) == 6
    assert sum(len(c.esperadas) for c in cenarios) >= 20
    assert all(c.solicitacao and c.contexto for c in cenarios)


def test_o_catalogo_exposto_ao_modelo_e_o_real(medidor):
    """18 tools, com schema e descrição — não o rascunho de ~15 que o PLANO previa."""
    schemas = medidor.schemas_openai()
    assert len(schemas) == 18
    for schema in schemas:
        funcao = schema["function"]
        assert funcao["description"], f"{funcao['name']} sem descrição"
        assert funcao["parameters"]["additionalProperties"] is False


def test_ruido_e_reportado_por_cenario_e_nao_agregado(medidor, cenario, operacoes):
    """`get_asset` é esperada em cinco dos seis cenários de dev e ruído no `aut_03`.

    Agregar os nomes numa lista só faria o relatório acusar a tool errada: quem lesse veria
    `get_asset` sob "fora do gabarito" e concluiria que o modelo erra a chamada mais básica do
    corpus. O ruído só se lê junto do cenário em que ocorreu.
    """
    placar = medidor.Placar(modelo="falso")
    placar.julgamentos = [
        medidor.julgar(_chamada(medidor, "get_data_quality", {"asset_id": "asset_H110"}),
                       cenario, operacoes),
    ]
    texto = "\n".join(medidor.linhas_do_placar(placar, []))
    assert "Cenário em que foi ruído" in texto
    assert "`get_data_quality` | `cen_falso`" in texto


def test_funcao_inventada_e_nomeada_no_relatorio(medidor, cenario, operacoes):
    """Contar quantas foram inventadas sem dizer quais desperdiça o achado mais útil da T0b."""
    placar = medidor.Placar(modelo="falso")
    placar.julgamentos = [
        medidor.julgar(_chamada(medidor, "diagnosticar_ativo", {}), cenario, operacoes)
    ]
    texto = "\n".join(medidor.linhas_do_placar(placar, []))
    assert "não existem no catálogo" in texto
    assert "`diagnosticar_ativo`" in texto
