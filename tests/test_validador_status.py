"""A4/T3 — `status: inviavel` tira o cenário do corpus executável, e o validador cobra o porquê.

Antes desta task o campo não existia em lugar nenhum: um `status:` escrito num YAML passava em
silêncio, o cenário continuava contando no split e o runner rodaria ele assim mesmo. Estes testes
existem para que esse silêncio não volte.

O corpus real não é tocado: cada teste monta um corpus mínimo em `tmp_path` e aponta o módulo
para lá.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

RAIZ = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def validador():
    spec = importlib.util.spec_from_file_location(
        "validar_cenarios", RAIZ / "scripts" / "validar_cenarios.py"
    )
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture
def corpus(tmp_path, validador, monkeypatch):
    """Monta um corpus mínimo válido — 2 dev + 2 test — e devolve um escritor de cenários."""
    regra = sorted(
        yaml.safe_load((RAIZ / "scenarios" / "_regras_decisao.yaml").read_text())["regras"]
    )[0]
    tool = sorted(validador.catalogo_de_tools())[0]
    (tmp_path / "_regras_decisao.yaml").write_text(yaml.safe_dump({"regras": [regra]}))

    def escrever(cid, **over):
        cenario = {
            "id": cid,
            "procedencia": "autoral",
            "split": "dev",
            "natureza": "dado_dependente",
            "solicitacao": "texto",
            "user_id": "usr_ana",
            "asset_id": f"asset_{cid}",
            "ambiente": {"env_seed": "s001", "modos_exigidos": []},
            "gabarito": {
                "evidencias_obrigatorias": ["asset.criticality"],
                "tools_esperadas": [tool],
                "decisao_esperada": f"regra:{regra}",
                "proibido": [],
            },
        }
        cenario.update(over)
        (tmp_path / f"{cid}.yaml").write_text(yaml.safe_dump(cenario, allow_unicode=True))

    monkeypatch.setattr(validador, "CENARIOS", tmp_path)
    monkeypatch.setattr(validador, "SPLIT_ESPERADO", {"dev": 2, "test": 2})
    for cid, split, proc in (
        ("a", "dev", "autoral"), ("b", "dev", "oficial"),
        ("c", "test", "autoral"), ("d", "test", "oficial"),
    ):
        escrever(cid, split=split, procedencia=proc)
    return escrever


def test_corpus_base_e_valido(validador, corpus, capsys):
    assert validador.main() == 0
    assert "4 cenários (4 executáveis)" in capsys.readouterr().out


def test_status_ausente_vale_valido(validador, corpus, capsys):
    """Os 24 YAMLs vivos não declaram `status` — ausência tem de significar `valido`."""
    corpus("a", split="dev", procedencia="autoral")  # reescreve sem `status`
    assert validador.main() == 0
    assert "inviáveis" not in capsys.readouterr().out


def test_status_desconhecido_falha(validador, corpus, capsys):
    corpus("a", split="dev", procedencia="autoral", status="talvez")
    assert validador.main() == 1
    assert "status inválido 'talvez'" in capsys.readouterr().out


def test_inviavel_sem_justificativa_falha(validador, corpus, capsys):
    """Declarar inviável sem dizer por quê é indistinguível de esconder o cenário."""
    corpus("a", split="dev", procedencia="autoral", status="inviavel")
    assert validador.main() == 1
    assert "exige justificativa_inviabilidade" in capsys.readouterr().out


def test_justificativa_sem_status_falha(validador, corpus, capsys):
    corpus("a", split="dev", procedencia="autoral", justificativa_inviabilidade="porque sim")
    assert validador.main() == 1
    assert "justificativa_inviabilidade sem status inviavel" in capsys.readouterr().out


def test_inviavel_sai_da_contagem_e_avisa(validador, corpus, capsys):
    """O bug original: inviável continuava contando. Agora sai do split e o erro explica."""
    corpus(
        "a", split="dev", procedencia="autoral",
        status="inviavel", justificativa_inviabilidade="nenhuma seed satisfaz os modos exigidos",
    )
    assert validador.main() == 1
    saida = capsys.readouterr().out
    assert "4 cenários (3 executáveis)" in saida
    assert "inviáveis (fora das baterias): a" in saida
    assert "1 cenários executáveis, esperado 2" in saida
    assert "denominador das baterias" in saida


def test_inviavel_nao_e_cobrado_por_ambiente_insatisfazivel(validador, corpus, capsys):
    """Seed que não satisfaz os modos é a razão típica da inviabilidade — não pode virar erro."""
    exigencia = [{"recurso": "asset_zzz", "categoria": "asset", "modos": ["conflict"]}]
    corpus(
        "a", split="dev", procedencia="autoral",
        ambiente={"env_seed": "s001", "modos_exigidos": exigencia},
        status="inviavel", justificativa_inviabilidade="modos exigidos insatisfazíveis",
    )
    validador.main()
    assert "dá asset_zzz/asset" not in capsys.readouterr().out


def test_inviavel_ainda_e_validado_como_documento(validador, corpus, capsys):
    """Sair da bateria não é sair da curadoria: gabarito quebrado continua sendo erro."""
    corpus(
        "a", split="dev", procedencia="autoral",
        status="inviavel", justificativa_inviabilidade="declarado",
        gabarito={
            "evidencias_obrigatorias": ["asset.criticality"],
            "tools_esperadas": ["tool_que_nao_existe"],
            "decisao_esperada": "regra:inexistente",
            "proibido": [],
        },
    )
    assert validador.main() == 1
    saida = capsys.readouterr().out
    assert "tools fora do catálogo" in saida
    assert "regra desconhecida" in saida


def test_inviavel_nao_conta_como_vazamento_de_ativo(validador, corpus):
    """Cenário que não roda não pode vazar dev/test — ele nunca toca o ativo."""
    corpus("a", split="dev", procedencia="autoral", asset_id="asset_compartilhado")
    corpus(
        "e", split="test", procedencia="oficial", asset_id="asset_compartilhado",
        status="inviavel", justificativa_inviabilidade="declarado",
    )
    assert validador.main() == 0
