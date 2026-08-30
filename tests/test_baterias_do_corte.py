"""As duas decisões de 30/08 que mudaram matriz, presas por teste.

O corte do A16 fechou o escopo da execução (principal + mutantes + referência) e, ao fazê-lo,
mexeu em dois manifestos que já tinham sido validados célula a célula em 24/08. Cada mudança
aqui tem um modo de falha silencioso, e é dele que estes testes tratam:

* **A coluna `base` dos mutantes** é a coluna de CONTROLE da INS.9 — "fração distinguida **do
  original**". Ela não estava na matriz da spec (`METRICAS §9.2` lista só os quatro mutantes) e
  entrou por decisão do operador. Uma reescrita futura que a lesse como "um quinto mutante" e a
  removesse por economia deixaria a INS.9 sem denominador, e a bateria ainda rodaria — 120
  células verdes medindo degradação contra nada.

* **O `model_id` da referência** foi `gemini-3.6-pro` de 24/08 a 30/08, um id que o catálogo do
  Vertex **nunca aceitou** e que ninguém tinha conferido. O defeito não era de sintaxe: o YAML
  carregava, o `--dry-run` imprimia 24 células, e a descoberta só viria na primeira chamada de
  rede. É esse formato de erro que o segundo teste torna impossível de repetir.
"""

from __future__ import annotations

import json

import yaml

from tapieval.runner.matriz import RAIZ_DO_REPO
from tapieval.scoring.judge_llm import MODELO_PADRAO as MODELO_DO_JUDGE


def _manifesto(nome: str) -> dict:
    caminho = RAIZ_DO_REPO / "configs" / f"bateria_{nome}.yaml"
    return yaml.safe_load(caminho.read_text(encoding="utf-8"))


def test_a_bateria_de_mutantes_tem_a_coluna_base_de_controle() -> None:
    """Sem `base` a INS.9 não tem contra o que ler a degradação.

    O teste é sobre a PRESENÇA da coluna, não sobre o tamanho da matriz: se um mutante for
    cortado por tempo (o §3 do dimensionamento nomeia o MUT3 como candidato), a bateria
    continua válida — o que não pode acontecer é o controle sair junto.
    """
    variantes = _manifesto("mutantes")["variantes"]
    assert "base" in variantes, (
        "a coluna de controle da INS.9 sumiu da matriz de mutantes; sem ela a métrica "
        "mede degradação contra nada e a bateria roda verde mesmo assim"
    )
    assert variantes[0] == "base", "`base` é controle, não o quinto mutante — vem primeiro"


def test_o_controle_dos_mutantes_roda_nas_mesmas_seeds_que_os_mutantes() -> None:
    """O pareamento é o que faz o n=5 sustentar a leitura.

    A detecção da INS.9 é lida entre PARES (mutante e `base` da mesma seed). Se as seeds
    divergissem, ela teria de ser lida entre distribuições, e cinco repetições não sustentam
    isso. Como `base` e os MUT compartilham a lista de `sample_seeds` do manifesto, o que este
    teste guarda é que a lista continua sendo o prefixo da principal.
    """
    mutantes = _manifesto("mutantes")["sample_seeds"]
    principal = _manifesto("principal")["sample_seeds"]
    assert mutantes == principal[: len(mutantes)], (
        "as seeds dos mutantes deixaram de ser prefixo das da principal; as células param de "
        "ser pareáveis com as de lá e com as da piloto"
    )


def test_o_sut_de_referencia_declara_um_id_que_o_catalogo_aceitou() -> None:
    """O `gemini-3.6-pro` carregava, imprimia 24 células e dava 404 na primeira chamada.

    `docs/catalogo_vertex.json` é o registro das sondas: `completacao_status == 200` quer dizer
    que o serviço ACEITOU o id, não só que o catálogo o declara. Um id fora dessa lista é um id
    que este projeto nunca mediu — e a bateria não pode ser a primeira a descobrir.
    """
    declarado = _manifesto("referencia")["modelos"]["gemini-referencia"]["model_id"]
    sondas = json.loads(
        (RAIZ_DO_REPO / "docs" / "catalogo_vertex.json").read_text(encoding="utf-8")
    )
    aceitos = {s["modelo"] for s in sondas if s.get("completacao_status") == 200}
    assert declarado in aceitos, (
        f"`{declarado}` não está entre os ids que o catálogo aceitou ({sorted(aceitos)}); "
        "rodar assim gasta a janela de RPD para descobrir um 404"
    )


def test_o_sut_de_referencia_nao_e_o_modelo_do_judge() -> None:
    """Juiz igual ao réu prefere as próprias respostas — e cairia sobre a linha do teto.

    `sut/referencia.py` já recusa isso em construtor; aqui a mesma regra é conferida no
    MANIFESTO, que é onde ela seria violada por edição de texto sem ninguém executar nada.
    """
    declarado = _manifesto("referencia")["modelos"]["gemini-referencia"]["model_id"]
    assert declarado != MODELO_DO_JUDGE, (
        f"o SUT de referência e o judge são ambos `{declarado}`: o judge julgaria a si mesmo "
        "na única linha que serve de teto (ARQUITETURA §13)"
    )
