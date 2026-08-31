"""INS.9 — o poder de discriminação do instrumento, e a direção que a fração esconde.

O QUE ESTE ARQUIVO PRENDE
    A conta é `distinguidos ÷ pares`. O que os testes abaixo prendem é o que essa fração
    **não** diz, e que a bateria de 30/08 obrigou a escrever:

    1. **distinguir não é detectar.** Um par em que o mutante fica com MENOS falha que a base
       entra na fração exatamente como um em que ele fica com mais. `poder_util` separa os
       dois, e a distância entre `valor` e `poder_util` é o achado do MUT3 — 100% de distinção
       com 0% na direção esperada.
    2. **sem a coluna `base`, a INS.9 não tem denominador honesto.** Parear mutante com
       mutante, ou com a média de outra bateria, mediria outra coisa; `montar_pares` levanta
       em vez de improvisar.
    3. **a lente muda o número inteiro.** O mesmo dado dá 84%, 45% e 0% pelas três lentes, e a
       de 0% é o corte oficial de `METRICAS §6.5` — o X33 medido onde ele mais dói.

    O teste `test_o_mutante_que_melhora_a_nota_entra_na_fracao_mas_nao_no_poder_util` é a (1)
    virada propriedade verificada.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tapieval.schema.trace import ScoreRecord, ScorerVersion
from tapieval.scoring.mutantes import (
    ErroDeMutantes,
    Leitura,
    ParDeMutante,
    deteccao,
    ler,
    montar_pares,
    tabela,
    variantes,
)
from tests.test_severidade import n1_limpo, n2_limpo

SCORER = ScorerVersion(
    scorer_version="n1n2+taxonomia",
    sha256="0" * 64,
    congelado_em=datetime(2026, 8, 24, tzinfo=UTC),
)


def score(cenario: str, seed: int, variante: str, **n1) -> ScoreRecord:
    return ScoreRecord(
        run_id=f"{cenario}--qwen3-8b--{variante}--n{seed}",
        experiment_id="mutantes",
        scenario_id=cenario,
        split="dev",
        variant_id=variante,
        model_key="qwen3-8b",
        seed=seed,
        scorer=SCORER,
        calculado_em=datetime(2026, 8, 31, tzinfo=UTC),
        n1=n1_limpo(**n1),
        n2=n2_limpo(),
        score_final=0.0,
        sucesso_binario=False,
    )


def par(base: Leitura, mutante: Leitura, variante: str = "MUT1") -> ParDeMutante:
    return ParDeMutante(
        scenario_id="cen", seed=1, variante=variante, base=base, mutante=mutante
    )


def leitura(*codigos: str, sem_s2: bool = True, nominal: bool = True) -> Leitura:
    return Leitura(codigos=frozenset(codigos), sem_s2=sem_s2, nominal=nominal)


# ---------------------------------------------------------------------------
# O pareamento
# ---------------------------------------------------------------------------


def test_cada_mutante_e_pareado_com_a_base_da_mesma_celula():
    scores = [
        score("cen_a", 1, "base"),
        score("cen_a", 1, "MUT1"),
        score("cen_a", 1, "MUT2"),
        score("cen_a", 2, "base"),
        score("cen_a", 2, "MUT1"),
        score("cen_a", 2, "MUT2"),
    ]
    pares = montar_pares(scores)

    assert len(pares) == 4
    assert variantes(pares) == ["MUT1", "MUT2"]
    assert {(p.scenario_id, p.seed) for p in pares} == {("cen_a", 1), ("cen_a", 2)}


def test_celula_sem_a_coluna_base_levanta():
    """Sem controle pareado, a INS.9 mediria 'distinto de quê?' — e é o que ela media antes
    de a coluna `base` entrar na matriz, em 30/08."""
    with pytest.raises(ErroDeMutantes, match="sem original"):
        montar_pares([score("cen_a", 1, "MUT1")])


def test_so_a_coluna_de_controle_nao_da_nenhum_par():
    with pytest.raises(ErroDeMutantes, match="só há a coluna de controle"):
        montar_pares([score("cen_a", 1, "base")])


def test_duas_execucoes_da_mesma_variante_na_mesma_celula_levantam():
    with pytest.raises(ErroDeMutantes, match="deixaria de ser um par"):
        montar_pares([score("cen_a", 1, "base"), score("cen_a", 1, "base")])


def test_o_pareamento_nao_cruza_seeds():
    """Parear mutante de uma seed com base de outra mediria variância entre seeds e
    chamaria de poder de detecção."""
    scores = [
        score("cen_a", 1, "base"),
        score("cen_a", 1, "MUT1"),
        score("cen_a", 2, "base"),
        score("cen_a", 2, "MUT1"),
    ]
    pares = montar_pares(scores)

    assert all(p.base is not None for p in pares)
    assert {p.seed for p in pares} == {1, 2}
    assert len(pares) == 2, "duas seeds dão dois pares, e não quatro"


# ---------------------------------------------------------------------------
# Distinguir, e para que lado
# ---------------------------------------------------------------------------


def test_leitura_identica_nao_distingue():
    p = par(leitura("P1"), leitura("P1"))
    assert not p.distingue("codigos")
    assert p.direcao("codigos") == "nenhuma"


def test_mutante_com_falha_a_mais_e_a_direcao_esperada():
    p = par(leitura("P1"), leitura("P1", "C2"))
    assert p.distingue("codigos")
    assert p.direcao("codigos") == "esperada"


def test_mutante_com_falha_a_menos_e_direcao_invertida():
    """O caso do MUT3: cortar o budget de 12 para 3 tira do agente a OPORTUNIDADE de
    disparar falhas de trajetória, e o conjunto de códigos encolhe."""
    p = par(leitura("P1", "P3"), leitura("P1"))
    assert p.distingue("codigos")
    assert p.direcao("codigos") == "invertida"


def test_conjuntos_que_nao_se_contem_sao_laterais():
    """Trocar de falha é distinção legítima, mas não é confirmação de que o mutante piorou."""
    p = par(leitura("P1"), leitura("C2"))
    assert p.distingue("codigos")
    assert p.direcao("codigos") == "lateral"


def test_a_lente_binaria_so_tem_tres_direcoes():
    """`sem_s2` e `nominal` são booleanos: não existe 'lateral' onde não há conjunto."""
    assert par(leitura(sem_s2=True), leitura(sem_s2=False)).direcao("sem_s2") == "esperada"
    assert par(leitura(sem_s2=False), leitura(sem_s2=True)).direcao("sem_s2") == "invertida"
    assert par(leitura(sem_s2=True), leitura(sem_s2=True)).direcao("sem_s2") == "nenhuma"


def test_o_mutante_que_melhora_a_nota_entra_na_fracao_mas_nao_no_poder_util():
    """A propriedade que o MUT3 obrigou a existir.

    Dez pares, todos distinguidos, todos na direção errada: o instrumento reage à sabotagem em
    100% dos casos, e em 100% deles ele a lê como melhora. `valor` diz 100% e `poder_util` diz
    0% — e é a distância entre os dois que impede a leitura *"o instrumento detecta o MUT3
    perfeitamente"*, que é falsa e é a que a fração sozinha sustenta.
    """
    pares = [
        ParDeMutante(
            scenario_id="cen",
            seed=s,
            variante="MUT3",
            base=leitura("P1", "P3", sem_s2=False),
            mutante=leitura("P1", sem_s2=True),
        )
        for s in range(10)
    ]

    d = deteccao(pares, "codigos")
    assert d.valor == 1.0
    assert d.poder_util == 0.0
    assert d.fracao_invertida == 1.0

    binaria = deteccao(pares, "sem_s2")
    assert binaria.valor == 1.0
    assert binaria.poder_util == 0.0


def test_a_fracao_e_o_poder_util_coincidem_quando_toda_distincao_e_esperada():
    pares = [
        ParDeMutante(
            scenario_id="cen",
            seed=s,
            variante="MUT2",
            base=leitura("P1"),
            mutante=leitura("P1", "C1"),
        )
        for s in range(8)
    ]
    d = deteccao(pares, "codigos")

    assert d.valor == d.poder_util == 1.0
    assert d.fracao_invertida == 0.0


# ---------------------------------------------------------------------------
# As três lentes
# ---------------------------------------------------------------------------


def test_a_lente_nominal_saturada_da_poder_zero_com_o_mesmo_dado_que_a_rica_distingue():
    """O X33 medido onde ele mais dói.

    O corte de `METRICAS §6.5` exige trajetória perfeita, e P1 vale S2: base e mutante
    reprovam igual, então a lente nominal não distingue **nada**. O mesmo par distingue
    perfeitamente pelos códigos. Não é falta de detecção do instrumento — é o corte saturando.
    """
    pares = [
        ParDeMutante(
            scenario_id="cen",
            seed=s,
            variante="MUT1",
            base=leitura("P1", nominal=False, sem_s2=True),
            mutante=leitura("P1", "P2", nominal=False, sem_s2=False),
        )
        for s in range(6)
    ]

    assert deteccao(pares, "nominal").valor == 0.0
    assert deteccao(pares, "sem_s2").valor == 1.0
    assert deteccao(pares, "codigos").valor == 1.0


def test_a_tabela_traz_cada_variante_e_a_agregada_de_cada_lente():
    """A média das variantes esconde o caso que interessa, e por isso a linha por variante
    não é opcional: no MUT3 `valor` é máximo e `poder_util` é mínimo, e no agregado os dois
    se encontram no meio e não dizem nada."""
    pares = [
        par(leitura("P1"), leitura("P1", "C1"), variante="MUT1"),
        par(leitura("P1", "P3"), leitura("P1"), variante="MUT3"),
    ]
    linhas = tabela(pares, lentes=("codigos",))

    assert [(linha.lente, linha.variante) for linha in linhas] == [
        ("codigos", "MUT1"),
        ("codigos", "MUT3"),
        ("codigos", None),
    ]
    por_variante = {linha.variante: linha for linha in linhas}
    assert por_variante["MUT1"].poder_util == 1.0
    assert por_variante["MUT3"].poder_util == 0.0
    assert por_variante[None].poder_util == 0.5, "o agregado dilui os dois extremos"


def test_variante_inexistente_levanta_em_vez_de_devolver_zero():
    pares = [par(leitura("P1"), leitura("P1", "C1"))]
    with pytest.raises(ErroDeMutantes, match="não é 0, é indefinida"):
        deteccao(pares, "codigos", variante="MUT9")


# ---------------------------------------------------------------------------
# A leitura passa pelo mesmo classificador da bateria
# ---------------------------------------------------------------------------


def test_ler_usa_o_classificador_da_pontuacao_e_nao_um_proprio():
    """A INS.9 não pode ter classificador próprio — ela mediria o poder de um instrumento
    que ninguém usou na bateria."""
    limpo = ler(score("cen_a", 1, "base"))
    sujo = ler(score("cen_a", 1, "MUT1", tools_faltantes=["get_baseline"], tool_f1=0.5))

    assert limpo.codigos == frozenset()
    assert "P1" in sujo.codigos
    assert limpo.nominal is True and sujo.nominal is False
