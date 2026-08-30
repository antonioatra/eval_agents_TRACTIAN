"""T21 — a retomada da calibração do judge, depois que o judge trocou de provedor.

Em 25/08 o A23 foi fechado migrando o judge do AI Studio para o Vertex. Os dois servem o mesmo
`gemini-3.6-flash`, mas **não são a mesma medição**: para o prompt byte a byte idêntico o Vertex
conta 6–8% mais tokens de entrada (`docs/migracao_vertex.md §5`). A chave de retomada era
`(trace, configuração, repetição)`, e com ela uma rodada nova daria por feitas as células que o
AI Studio já tinha julgado — o flip rate (INS.7) compararia então um julgamento de um provedor
contra quatro do outro, e atribuiria à ambiguidade da RUBRICA uma variação que é de
infraestrutura. É o mesmo formato de erro do X9: o número sai na direção que favorece a
hipótese, e nada quebra.

Os testes são sobre a chave e sobre o que a retomada considera feito. Nenhuma chamada de rede.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def calibrar():
    spec = importlib.util.spec_from_file_location(
        "calibrar_judge", RAIZ / "scripts" / "calibrar_judge.py"
    )
    modulo = importlib.util.module_from_spec(spec)
    sys.modules["calibrar_judge"] = modulo
    spec.loader.exec_module(modulo)
    return modulo


def linha(**campos):
    base = {
        "trace": "calibracao_2026-08-24/aut_01--qwen3-8b--base--envs001--n11.jsonl",
        "configuracao": "cego",
        "repeticao": 1,
        "served_by": "vertex_ai",
    }
    return base | campos


def test_a_chave_carrega_o_provedor(calibrar):
    assert calibrar.chave(linha())[3] == "vertex_ai"


def test_o_mesmo_item_julgado_pelos_dois_provedores_sao_duas_celulas(calibrar):
    do_vertex = calibrar.chave(linha(served_by="vertex_ai"))
    do_ai_studio = calibrar.chave(linha(served_by="gemini_api"))
    assert do_vertex != do_ai_studio


def test_linha_antiga_sem_o_campo_e_do_ai_studio(calibrar):
    """As 3 células gravadas em 24/08 são anteriores ao campo, e não podem virar `None`.

    Tratá-las como provedor desconhecido faria a retomada regravá-las contra o AI Studio
    também — gastando quota para produzir a linha que já existe.
    """
    antiga = linha()
    del antiga["served_by"]
    assert calibrar.chave(antiga)[3] == "gemini_api"


def test_a_retomada_nao_da_por_feita_a_celula_julgada_pelo_outro_provedor(
    calibrar, tmp_path
):
    arquivo = tmp_path / "julgamentos.jsonl"
    arquivo.write_text(
        json.dumps(linha(served_by="gemini_api"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    feitos = calibrar.ja_gravados(arquivo)

    item = linha()["trace"]
    assert (item, "cego", 1, "gemini_api", "v1") in feitos
    assert (item, "cego", 1, "vertex_ai", "v1") not in feitos


def test_a_retomada_nao_da_por_feita_a_celula_julgada_pela_outra_rubrica(calibrar, tmp_path):
    """A comparação v1 × v2 da T21 é o MESMO item julgado por dois prompts.

    Sem a rubrica na chave, a rodada da v2 daria por feitas as células que a v1 já julgou: a
    curva compararia a v1 contra ela mesma e sairia plana — que é exatamente o formato de
    resultado que faria a reescrita da rubrica parecer inócua, sem nada quebrar. É o mesmo
    erro do X9 e a mesma forma do que a migração de provedor quase causou.
    """
    arquivo = tmp_path / "julgamentos.jsonl"
    arquivo.write_text(
        json.dumps(linha(rubrica="v1"), ensure_ascii=False) + "\n", encoding="utf-8"
    )

    feitos = calibrar.ja_gravados(arquivo)

    item = linha()["trace"]
    assert (item, "cego", 1, "vertex_ai", "v1") in feitos
    assert (item, "cego", 1, "vertex_ai", "v2") not in feitos


def test_linha_antiga_sem_rubrica_e_da_v1(calibrar):
    """As 220 células de 26/08 foram gravadas antes do campo existir, e são todas v1.

    Lê-las como rubrica desconhecida faria a próxima rodada da v1 regravá-las — gastando as
    220 chamadas para produzir a linha que já está no disco.
    """
    antiga = linha()
    antiga.pop("rubrica", None)
    assert calibrar.chave(antiga)[4] == "v1"


def test_linha_corrompida_continua_sendo_ignorada(calibrar, tmp_path):
    """A tolerância que a retomada já tinha não pode ter sido perdida no caminho."""
    arquivo = tmp_path / "julgamentos.jsonl"
    arquivo.write_text(
        "{isto não é json\n" + json.dumps(linha(), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    assert len(calibrar.ja_gravados(arquivo)) == 1


# ---------------------------------------------------------------------------
# `--somente` — julgar exatamente as runs que têm rótulo humano
# ---------------------------------------------------------------------------

CALIBRACAO = RAIZ / "runs" / "calibracao_2026-08-24"


def test_somente_julga_as_runs_nomeadas_e_ignora_a_cota_por_cenario(calibrar):
    """Por id, quem nomeou sabe qual quer — a cota do instrumento sai do caminho.

    É o que faz o judge cair sobre o MESMO conjunto que o gold humano. Uma amostra
    estratificada por cenário cobriria outros traces, e o denominador de INS.1 ficaria vazio:
    `ΔRecall(N3 | N1+N2)` precisa de julgamento e rótulo sobre a mesma run.
    """
    todos, _ = calibrar.compor_amostra([CALIBRACAO], por_cenario=99)
    assert len(todos) >= 3, "a calibração precisa ter traces julgáveis para este teste valer"

    # Três do MESMO cenário — com a cota em 1, a amostra normal traria só um deles.
    por_cenario = {}
    for caminho in todos:
        por_cenario.setdefault(caminho.name.split("--")[0], []).append(caminho)
    cenario, caminhos = max(por_cenario.items(), key=lambda kv: len(kv[1]))
    assert len(caminhos) >= 2, f"{cenario} precisa de 2+ traces para o teste ter alvo"

    alvo = frozenset(caminho.stem for caminho in caminhos[:2])
    escolhidos, _ = calibrar.compor_amostra([CALIBRACAO], por_cenario=1, somente=alvo)

    assert {caminho.stem for caminho in escolhidos} == alvo


def test_somente_com_run_inexistente_e_erro_e_nao_amostra_menor(calibrar):
    """Julgar 18 de 20 itens de estimativa produziria recall sobre outro denominador."""
    with pytest.raises(SystemExit, match="não tem trace julgável"):
        calibrar.compor_amostra(
            [CALIBRACAO], por_cenario=99, somente=frozenset({"run_que_nao_existe"})
        )


def test_arquivo_de_somente_vazio_e_erro(calibrar, tmp_path):
    """`frozenset()` julgaria zero traces e imprimiria 'nada a fazer' — que se lê como sucesso."""
    vazio = tmp_path / "somente.txt"
    vazio.write_text("# só comentário\n\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="não tem nenhum run_id"):
        calibrar._ler_somente(vazio)


def test_arquivo_de_somente_ignora_comentario_e_linha_vazia(calibrar, tmp_path):
    arquivo = tmp_path / "somente.txt"
    arquivo.write_text("# gold\naut_01--x--base--envs001--n11\n\n  \n", encoding="utf-8")

    assert calibrar._ler_somente(arquivo) == frozenset({"aut_01--x--base--envs001--n11"})
