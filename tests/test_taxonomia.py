"""T30 — severidade, frequência por código, e as três ausências que se parecem.

O QUE ESTE ARQUIVO PRENDE
    A classificação é `severidade.classificar_falhas` e ela já tem teste próprio. O que os
    testes abaixo prendem é a **leitura** dela sobre uma bateria, porque é aí que a bateria de
    30/08 mostrou que o relatório mente sozinho se ninguém segurar:

    1. **`aprova` não pode divergir de `sucesso_binario`.** O corte S2 deste módulo e o de
       `METRICAS §6.5` são a mesma coisa dita duas vezes, e duas verdades sobre o mesmo corte
       é como a figura da T30 e a curva da T29 passariam a discordar sem ninguém notar.
    2. **As três ausências.** Código que o schema não sustenta, código que a camada não mediu,
       e código medido que deu zero são barras de altura zero idênticas e frases opostas. O
       default de `codigos_ausentes` é o conservador, e é isso que o teste exige.
    3. **O X35 dentro da sensibilidade.** Execução sem decisão recebe só código de processo e
       **aprova** em todo corte abaixo de S2. Se `LinhaDeSensibilidade` não carregasse as duas
       taxas no mesmo objeto, a manchete voltaria a ser a primeira.
    4. **O teto só conta o que reprova naquele corte.** C1 é S1 e muda o corte S1; C4 é S2 e
       não muda. Somar as duas exageraria o efeito na direção que favorece o argumento.

    `test_a_bateria_principal_nao_tem_n3` é **tripwire**, no formato do da T29: enquanto ele
    passar, a classe C não é medida na principal e a figura da T30 tem obrigação de dizer
    isso. No dia em que alguém pontuar a principal com judge, ele falha — e a falha é a
    instrução para refazer a figura com a classe C dentro.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from tapieval.schema.trace import ScoreRecord, ScorerVersion
from tapieval.scoring.bateria import falhas_do_score
from tapieval.scoring.severidade import (
    CATALOGO_DE_FALHAS,
    CODIGOS_QUE_EXIGEM_N3,
    FALHAS_NAO_CLASSIFICAVEIS,
    Falha,
    classificar_falhas,
    sucesso_binario,
    sucesso_binario_sem_s2,
)
from tapieval.scoring.taxonomia import (
    CORTES,
    ESCALA,
    ErroDeTaxonomia,
    Observacao,
    codigos_ausentes,
    frequencias,
    lacuna_de_cobertura,
    observacoes_da_bateria,
    ordem_dos_modelos,
    perfil_de_severidade,
    relatorio_markdown,
    sensibilidade,
    severidades_que_reprovam,
    teto_da_lente,
)
from tests.test_severidade import n1_limpo, n2_limpo

RAIZ = Path(__file__).resolve().parents[1]

CAMINHO_DA_PRINCIPAL = RAIZ / "runs" / "principal_2026_08" / "scores.jsonl"

EXECUCOES_DA_PRINCIPAL = 288
INDECISAS_DA_PRINCIPAL = 37
"""Declarados como número pelo motivo do `test_estabilidade`: uma análise de severidade sobre
meia bateria passaria calada."""

SCORER = ScorerVersion(
    scorer_version="n1n2+taxonomia",
    sha256="0" * 64,
    congelado_em=datetime(2026, 8, 24, tzinfo=UTC),
)

MOTIVO_INDECISA = (
    "decisao_prevista is None — o trace não tem `DecisionEvent` nem ato observável, "
    "então não há decisão a comparar com o gabarito"
)


class VereditoFalso:
    """Um veredito de rubrica que reprova em tudo — o insumo da sonda de `CODIGOS_QUE_EXIGEM_N3`."""

    causa_raiz_correta = False
    mencionou_limitacao_relevante = False
    afirmacoes_sem_suporte = ["inventou o número"]
    contradiz_evidencia = True
    recomendou_acao_sem_base = True


def score(
    cenario: str = "cen_01",
    seed: int = 42,
    *,
    modelo: str = "qwen3-8b",
    pontuavel: bool = True,
    n2_overrides: dict | None = None,
    **n1,
) -> ScoreRecord:
    if not pontuavel:
        n1.setdefault("decisao_prevista", None)
        n1.setdefault("decisao_correta", False)
    return ScoreRecord(
        run_id=f"{cenario}--{modelo}--base--n{seed}",
        experiment_id="teste",
        scenario_id=cenario,
        split="test",
        variant_id="base",
        model_key=modelo,
        seed=seed,
        scorer=SCORER,
        calculado_em=datetime(2026, 8, 31, tzinfo=UTC),
        n1=n1_limpo(**n1),
        n2=n2_limpo(**(n2_overrides or {})),
        score_final=0.0,
        sucesso_binario=False,
        pontuavel=pontuavel,
        motivo_nao_pontuavel=None if pontuavel else MOTIVO_INDECISA,
    )


def obs(
    run_id: str, codigos: list[str], *, modelo: str = "qwen3-8b", pontuavel: bool = True
) -> Observacao:
    """Observação montada direto dos códigos — para os testes que são sobre a leitura, não
    sobre o classificador. Quem testa o classificador é `test_severidade.py`."""
    falhas = tuple(
        Falha(
            codigo=c,
            classe=CATALOGO_DE_FALHAS[c].classe,
            severidade=CATALOGO_DE_FALHAS[c].severidade,
            descricao=CATALOGO_DE_FALHAS[c].descricao,
            detectada_por=CATALOGO_DE_FALHAS[c].detectada_por,
            evidencia=f"evidência de {c}",
        )
        for c in codigos
    )
    return Observacao(run_id=run_id, model_key=modelo, falhas=falhas, pontuavel=pontuavel)


@pytest.fixture(scope="module")
def scores_da_principal() -> list[ScoreRecord]:
    if not CAMINHO_DA_PRINCIPAL.exists():
        pytest.skip(f"bateria principal ausente: {CAMINHO_DA_PRINCIPAL}")
    linhas = CAMINHO_DA_PRINCIPAL.read_text(encoding="utf-8").splitlines()
    return [ScoreRecord.model_validate_json(linha) for linha in linhas if linha.strip()]


# ---------------------------------------------------------------------------
# O tripwire da classe C
# ---------------------------------------------------------------------------


def test_a_bateria_principal_nao_tem_n3(scores_da_principal):
    """Enquanto nenhum `ScoreRecord` tiver `n3`, a classe C não é medida na principal — e a
    figura da T30 tem obrigação de marcar a ausência como ausência de MEDIÇÃO.

    Quando este teste falhar, o judge rodou sobre a principal: refazer a figura com a classe C
    dentro e trocar `camadas_medidas=()` por `("n3",)` no notebook.
    """
    com_judge = [s.run_id for s in scores_da_principal if s.n3 is not None]
    assert not com_judge, (
        f"{len(com_judge)} execução(ões) da principal têm N3 — a classe C passou a ser "
        f"medida e a T30 precisa ser refeita: {com_judge[:3]}"
    )


def test_a_lista_de_codigos_que_exigem_n3_e_a_que_o_classificador_de_fato_emite():
    """A sonda, não a lista escrita à mão.

    Classifica a MESMA run com e sem veredito de rubrica; a diferença é, por construção,
    exatamente o conjunto de códigos que só existem quando há judge. Se alguém acrescentar um
    campo à rubrica e emitir código novo, este teste falha aqui — em vez de a T30 classificar
    a ausência dele como "medido e deu zero", que é a única das três frases que autoriza
    dizer que a falha não aconteceu.
    """
    n1, n2 = n1_limpo(), n2_limpo()
    sem_judge = {f.codigo for f in classificar_falhas(n1, n2, None)}
    com_judge = {f.codigo for f in classificar_falhas(n1, n2, VereditoFalso())}

    assert com_judge - sem_judge == set(CODIGOS_QUE_EXIGEM_N3)


def test_nenhum_codigo_esta_nos_dois_mapas_de_ausencia():
    """Um código não pode ser ao mesmo tempo "o schema não sustenta" e "a camada não rodou".

    Se estivesse, `codigos_ausentes` daria a resposta pela ordem dos `if` — que é a forma de
    a explicação no relatório depender de refatoração, não do instrumento.
    """
    assert not (CODIGOS_QUE_EXIGEM_N3 & set(FALHAS_NAO_CLASSIFICAVEIS))


# ---------------------------------------------------------------------------
# Os cortes
# ---------------------------------------------------------------------------


def test_a_escala_nao_tem_s4():
    """O enunciado da T30 pede "S0–S4". A régua congelada tem quatro níveis e o último é S3
    (X18). A figura lê `ESCALA`, então ela não tem como plotar um nível a mais."""
    assert ESCALA == ("S0", "S1", "S2", "S3")


@pytest.mark.parametrize(
    ("corte", "esperado"),
    [("S0", ("S0",)), ("S1", ("S0", "S1")), ("S2", ("S0", "S1", "S2"))],
)
def test_o_corte_reprova_de_s0_ate_ele_inclusive(corte, esperado):
    assert severidades_que_reprovam(corte) == esperado


def test_corte_desconhecido_levanta():
    """`S3` inclusive: reprovar até S3 reprovaria toda execução com qualquer falha, e o
    binário deixaria de dizer mais do que "houve falha"."""
    with pytest.raises(ErroDeTaxonomia, match="corte desconhecido"):
        severidades_que_reprovam("S3")  # type: ignore[arg-type]


def test_aprova_no_corte_s2_e_a_mesma_coisa_que_sucesso_binario():
    """A definição de §6.5 dita duas vezes tem que dar a mesma resposta — inclusive nas runs
    em que ela é falsa por um código só."""
    for codigos in ([], ["P5"], ["P1"], ["D1"], ["C1"], ["P5", "P6"], ["P1", "D1"]):
        o = obs("r", codigos)
        assert o.aprova("S2") == sucesso_binario(o.falhas), codigos


def test_aprova_no_corte_s1_e_a_mesma_coisa_que_sucesso_binario_sem_s2():
    for codigos in ([], ["P1"], ["D1"], ["C1"], ["P1", "P5"], ["C4"]):
        o = obs("r", codigos)
        assert o.aprova("S1") == sucesso_binario_sem_s2(o.falhas), codigos


def test_os_cortes_sao_aninhados_e_a_taxa_nao_pode_cair_ao_afrouxar():
    """Afrouxar o corte não pode reprovar quem já passava — se cair, `severidades_que_reprovam`
    parou de devolver prefixo da escala e a análise de sensibilidade virou ruído."""
    populacao = [
        obs("a", []),
        obs("b", ["P5"]),
        obs("c", ["P1"]),
        obs("d", ["C1"]),
        obs("e", ["D1"]),
        obs("f", ["P1", "D1"]),
    ]
    linhas = {
        linha.corte: linha for linha in sensibilidade(populacao, ["qwen3-8b"], cortes=CORTES)
    }
    assert linhas["S2"].taxa <= linhas["S1"].taxa <= linhas["S0"].taxa


# ---------------------------------------------------------------------------
# Perfil de severidade
# ---------------------------------------------------------------------------


def test_o_perfil_conta_execucoes_e_a_soma_fecha_o_n():
    """Por execução e não por falha: a soma das barras é o n do modelo. Contar falhas daria
    total maior que o n e faria a figura parecer distribuição de probabilidade."""
    populacao = [
        obs("a", []),
        obs("b", ["P5"]),
        obs("c", ["P1", "P5"]),
        obs("d", ["D1", "P1", "C1"]),
    ]
    p = perfil_de_severidade(populacao, "qwen3-8b")

    assert p.n_execucoes == 4
    assert p.n_sem_falha == 1
    assert p.por_maxima == {"S0": 1, "S1": 0, "S2": 1, "S3": 1}
    assert sum(p.por_maxima.values()) + p.n_sem_falha == p.n_execucoes
    assert p.fracao("S0") == 0.25


def test_o_perfil_nomeia_os_niveis_vazios():
    """Nível vazio na figura precisa de legenda, ou se lê como faixa não medida."""
    p = perfil_de_severidade([obs("a", ["P1"]), obs("b", ["P1"])], "qwen3-8b")
    assert p.niveis_vazios == ("S0", "S1", "S3")


def test_modelo_ausente_levanta_em_vez_de_devolver_perfil_vazio():
    """Perfil vazio produziria uma barra de zero indistinguível de "o modelo não falhou", e a
    causa (nome de modelo errado) some do rastro."""
    with pytest.raises(ErroDeTaxonomia, match="nenhuma execução"):
        perfil_de_severidade([obs("a", ["P1"])], "qwen3-70b")


# ---------------------------------------------------------------------------
# Sensibilidade e o X35
# ---------------------------------------------------------------------------


def test_a_execucao_sem_decisao_aprova_no_corte_s1_e_a_linha_conta_isso():
    """O X35 medido: execução sem decisão recebe só código de processo (nenhum S0/S1) e
    **aprova** na lente que o X33 propôs como conserto. As duas taxas moram no mesmo objeto
    porque separá-las é como a primeira vira a manchete."""
    populacao = [
        obs("sem_decisao_1", ["P1", "P5"], pontuavel=False),
        obs("sem_decisao_2", ["P1", "P5"], pontuavel=False),
        obs("com_decisao_ok", ["P1"]),
        obs("com_decisao_ruim", ["D1"]),
    ]
    (linha,) = sensibilidade(populacao, ["qwen3-8b"], cortes=["S1"])

    assert linha.n_aprovadas == 3
    assert linha.n_aprovadas_sem_decisao == 2
    assert linha.taxa == 0.75
    assert linha.taxa_entre_pontuaveis == 0.5
    assert linha.fracao_da_aprovacao_sem_decisao == pytest.approx(2 / 3)


def test_sem_aprovacao_a_fracao_do_x35_e_zero_e_nao_divisao_por_zero():
    """Sem aprovação nenhuma, nenhuma parte dela vem do X35 — a frase é verdadeira, e `NaN`
    aqui atravessaria a figura inteira sem avisar."""
    (linha,) = sensibilidade([obs("a", ["D1"])], ["qwen3-8b"], cortes=["S1"])
    assert linha.n_aprovadas == 0
    assert linha.fracao_da_aprovacao_sem_decisao == 0.0


def test_taxa_sem_denominador_levanta():
    """Modelo em que TODA execução é sem decisão: a taxa entre pontuáveis não existe."""
    (linha,) = sensibilidade(
        [obs("a", ["P1"], pontuavel=False)], ["qwen3-8b"], cortes=["S1"]
    )
    with pytest.raises(ErroDeTaxonomia, match="sem denominador"):
        _ = linha.taxa_entre_pontuaveis


def test_a_ordem_entre_modelos_pode_nao_sobreviver_ao_desconto_do_x35():
    """O caso que a bateria real produziu, reduzido: o modelo B lidera só porque as execuções
    em que ele não decidiu nada entram como aprovação."""
    populacao = [
        obs("a1", ["P1"], modelo="A"),
        obs("a2", ["D1"], modelo="A"),
        obs("a3", ["D1"], modelo="A"),
        obs("b1", ["D1"], modelo="B"),
        obs("b2", ["P1"], modelo="B", pontuavel=False),
        obs("b3", ["P1"], modelo="B", pontuavel=False),
    ]
    linhas = sensibilidade(populacao, ["A", "B"], cortes=["S1"])
    ordem = ordem_dos_modelos(linhas, "S1")

    assert ordem.lider == "B"
    assert ordem.lider_entre_pontuaveis == "A"
    assert not ordem.sobrevive_ao_x35


def test_empate_dos_dois_lados_sobrevive():
    """Empate é conclusão — "este corte não ordena os modelos" —, não ausência dela."""
    populacao = [obs("a", ["D1"], modelo="A"), obs("b", ["D1"], modelo="B")]
    ordem = ordem_dos_modelos(sensibilidade(populacao, ["A", "B"], cortes=["S1"]), "S1")

    assert ordem.lider is None
    assert ordem.sobrevive_ao_x35


def test_ordem_com_tres_modelos_levanta():
    """"O líder" entre três esconde a distância para o terceiro, e a pergunta da T30 é sobre
    um par."""
    populacao = [obs(m, ["P1"], modelo=m) for m in ("A", "B", "C")]
    linhas = sensibilidade(populacao, ["A", "B", "C"], cortes=["S1"])
    with pytest.raises(ErroDeTaxonomia, match="compara dois modelos"):
        ordem_dos_modelos(linhas, "S1")


# ---------------------------------------------------------------------------
# Frequência e exemplo
# ---------------------------------------------------------------------------


def test_as_frequencias_saem_ordenadas_do_mais_frequente_e_carregam_a_definicao_congelada():
    populacao = [
        obs("r1", ["P1", "D1"]),
        obs("r2", ["P1"]),
        obs("r3", ["P1", "P5"]),
    ]
    freqs = frequencias(populacao, ["qwen3-8b"])

    assert [f.codigo for f in freqs] == ["P1", "D1", "P5"]
    assert freqs[0].n_total == 3
    assert freqs[0].fracao == 1.0
    assert freqs[0].severidade == CATALOGO_DE_FALHAS["P1"].severidade
    assert freqs[0].descricao == CATALOGO_DE_FALHAS["P1"].descricao


def test_o_exemplo_e_a_primeira_ocorrencia_por_run_id_e_nao_uma_escolhida_a_dedo():
    """Exemplo selecionado por quem escreve o relatório é ilustração; o que a T30 pede é
    evidência de que o código dispara sobre dado real."""
    populacao = [obs("r9", ["P1"]), obs("r1", ["P1"]), obs("r5", ["P1"])]
    (freq,) = frequencias(populacao, ["qwen3-8b"])

    assert freq.exemplo_run_id == "r1"
    assert freq.exemplo_evidencia == "evidência de P1"


def test_a_fracao_por_modelo_usa_o_n_daquele_modelo():
    """Denominador errado aqui é como "51% das execuções do 14B" vira "51% da bateria"."""
    populacao = [
        obs("a1", ["P1"], modelo="A"),
        obs("a2", ["P1"], modelo="A"),
        obs("b1", ["P1"], modelo="B"),
        obs("b2", [], modelo="B"),
        obs("b3", [], modelo="B"),
    ]
    (freq,) = frequencias(populacao, ["A", "B"])

    assert freq.n_total == 3
    assert freq.fracao_do_modelo("A") == 1.0
    assert freq.fracao_do_modelo("B") == pytest.approx(1 / 3)


def test_codigo_com_zero_observacao_nao_entra_nas_frequencias():
    """Ele é `codigos_ausentes`, com o motivo — que é a informação que a barra de zero perde."""
    freqs = frequencias([obs("r1", ["P1"])], ["qwen3-8b"])
    assert [f.codigo for f in freqs] == ["P1"]


# ---------------------------------------------------------------------------
# As três ausências
# ---------------------------------------------------------------------------


def test_o_default_de_codigos_ausentes_e_o_conservador():
    """Sem alguém afirmar que o judge rodou, a ausência de C1 NÃO é lida como ausência de
    falha de causa-raiz."""
    ausentes = {a.codigo: a for a in codigos_ausentes([obs("r1", ["P1"])])}

    assert ausentes["C1"].motivo == "camada_ausente"
    assert ausentes["C6"].motivo == "schema"
    assert ausentes["C5"].motivo == "medido_zero"
    assert ausentes["D5"].motivo == "schema"
    assert "P1" not in ausentes


def test_declarar_n3_medido_transforma_camada_ausente_em_medido_zero():
    """A afirmação é de quem chama, e é ela que autoriza a frase "não aconteceu"."""
    ausentes = {
        a.codigo: a for a in codigos_ausentes([obs("r1", ["P1"])], camadas_medidas=("n3",))
    }
    assert ausentes["C1"].motivo == "medido_zero"
    assert ausentes["C6"].motivo == "schema", (
        "o schema não passa a sustentar C6 porque o judge rodou"
    )


def test_todo_codigo_da_tabela_ou_e_observado_ou_tem_motivo():
    """Nenhum código pode sumir do relatório: a tabela congelada é a lista fechada, e código
    que não aparece em lugar nenhum é a ausência que ninguém vê."""
    populacao = [obs("r1", ["P1", "D1"])]
    observados = {f.codigo for f in frequencias(populacao, ["qwen3-8b"])}
    ausentes = {a.codigo for a in codigos_ausentes(populacao)}

    assert observados | ausentes == set(CATALOGO_DE_FALHAS)
    assert not (observados & ausentes)


# ---------------------------------------------------------------------------
# Campo de visão e teto
# ---------------------------------------------------------------------------


def test_a_lacuna_marca_como_invisivel_o_codigo_que_o_gold_ve_e_a_bateria_nao():
    bateria = [obs("b1", ["P1"]), obs("b2", ["P1", "D1"])]
    gold = [obs("g1", ["P1", "C1"]), obs("g2", ["C1", "C4"])]
    lacunas = {lac.codigo: lac for lac in lacuna_de_cobertura(bateria, gold)}

    assert lacunas["C1"].invisivel
    assert lacunas["C1"].fracao_no_gold == 1.0
    assert not lacunas["P1"].invisivel
    assert "D1" not in lacunas, "código que o gold não viu não diz nada sobre campo de visão"


def test_lacuna_sem_gold_levanta():
    with pytest.raises(ErroDeTaxonomia, match="gold vazio"):
        lacuna_de_cobertura([obs("b1", ["P1"])], [])


def test_o_teto_so_desconta_o_codigo_cuja_severidade_reprova_naquele_corte():
    """C1 é S1 e derruba o corte S1; C4 é S2 e não o toca. Somar as duas exageraria o efeito
    na direção que favorece o argumento."""
    bateria = [obs("b1", []), obs("b2", ["P1"]), obs("b3", ["D1"]), obs("b4", [])]
    gold = [obs("g1", ["C1"]), obs("g2", ["C4"]), obs("g3", ["C4"]), obs("g4", [])]
    lacunas = lacuna_de_cobertura(bateria, gold)
    (linha,) = sensibilidade(bateria, ["qwen3-8b"], cortes=["S1"])

    teto = teto_da_lente(linha, lacunas, gold)

    assert teto.codigos_invisiveis == ("C1",), "C4 é S2 e não reprova no corte S1"
    assert teto.taxa_observada == 0.75
    assert teto.fracao_do_gold_com_invisivel_que_reprova == 0.25
    assert teto.teto_projetado == pytest.approx(0.75 * 0.75)


def test_sem_codigo_invisivel_o_teto_e_a_propria_taxa():
    """Quando a bateria vê tudo o que o gold vê, não há o que descontar — e o teto não pode
    inventar desconto para parecer prudente."""
    bateria = [obs("b1", ["C1"]), obs("b2", [])]
    gold = [obs("g1", ["C1"]), obs("g2", [])]
    lacunas = lacuna_de_cobertura(bateria, gold)
    (linha,) = sensibilidade(bateria, ["qwen3-8b"], cortes=["S1"])

    teto = teto_da_lente(linha, lacunas, gold)
    assert teto.codigos_invisiveis == ()
    assert teto.teto_projetado == teto.taxa_observada


# ---------------------------------------------------------------------------
# O adaptador e o relatório
# ---------------------------------------------------------------------------


def test_observacoes_da_bateria_usa_o_mesmo_classificador_da_pontuacao():
    """Um classificador próprio aqui descreveria um instrumento que ninguém usou."""
    registros = [
        score("cen_01", 42, tools_faltantes=["get_baseline"]),
        score("cen_02", 42, pontuavel=False),
    ]
    observacoes = observacoes_da_bateria(registros)

    assert [o.run_id for o in observacoes] == [r.run_id for r in registros]
    assert observacoes[0].falhas == tuple(falhas_do_score(registros[0]))
    assert observacoes[1].pontuavel is False


def test_o_relatorio_declara_a_escala_e_nao_deixa_ausencia_sem_motivo():
    populacao = [obs("r1", ["P1", "D1"], modelo="A"), obs("r2", ["P1"], modelo="A")]
    gold = [obs("g1", ["C1"])]
    linhas = sensibilidade(populacao, ["A"])
    texto = relatorio_markdown(
        bateria="teste",
        modelos=["A"],
        rotulos={"A": "A"},
        freqs=frequencias(populacao, ["A"]),
        ausentes=codigos_ausentes(populacao),
        perfis=[perfil_de_severidade(populacao, "A")],
        linhas=linhas,
        ordens=[],
        lacunas=lacuna_de_cobertura(populacao, gold),
        tetos=[teto_da_lente(linhas[1], lacuna_de_cobertura(populacao, gold), gold)],
    )

    assert "Não existe S4" in texto
    assert "S4 |" not in texto, (
        "a tabela de severidade não pode ganhar uma coluna que a régua não tem"
    )
    assert "`C1`" in texto and "esta bateria não mediu" in texto
    assert "nan" not in texto.lower()
    for codigo in CATALOGO_DE_FALHAS:
        assert f"`{codigo}`" in texto, f"{codigo} sumiu do relatório"


# ---------------------------------------------------------------------------
# Regressão sobre a bateria real
# ---------------------------------------------------------------------------


def test_a_bateria_principal_ainda_tem_as_288_execucoes_e_as_37_indecisas(scores_da_principal):
    assert len(scores_da_principal) == EXECUCOES_DA_PRINCIPAL
    assert sum(1 for s in scores_da_principal if not s.pontuavel) == INDECISAS_DA_PRINCIPAL


def test_a_lente_oficial_reprova_a_bateria_inteira(scores_da_principal):
    """0/288 no corte S2. É o X33 medido onde ele mais dói, e é o motivo de a T30 reportar os
    três cortes em vez de assumir §6.5 em silêncio."""
    populacao = observacoes_da_bateria(scores_da_principal)
    for linha in sensibilidade(populacao, ["qwen3-8b", "qwen3-14b"], cortes=["S2"]):
        assert linha.n_aprovadas == 0, linha.model_key


def test_nenhum_corte_da_bateria_real_ordena_os_modelos_depois_do_x35(scores_da_principal):
    """O resultado da T30. No corte S2 é empate em zero; nos outros dois o líder muda quando
    as execuções sem decisão saem do numerador — e é isso que impede a leitura "o 14B é mais
    confiável"."""
    populacao = observacoes_da_bateria(scores_da_principal)
    linhas = sensibilidade(populacao, ["qwen3-14b", "qwen3-8b"])

    ordens = {o.corte: o for o in (ordem_dos_modelos(linhas, c) for c in CORTES)}

    assert ordens["S2"].lider is None, "empate em zero"
    assert ordens["S1"].lider == "qwen3-14b"
    assert ordens["S1"].lider_entre_pontuaveis == "qwen3-8b"
    assert not ordens["S1"].sobrevive_ao_x35
    assert ordens["S0"].lider == "qwen3-14b"
    assert abs(ordens["S0"].delta_entre_pontuaveis) < abs(ordens["S0"].delta), (
        "descontado o X35, a distância entre os modelos tem que encolher"
    )


def test_mais_da_metade_da_aprovacao_do_14b_vem_de_execucao_sem_decisao(scores_da_principal):
    """O tamanho do X35, preso como número: se ele encolher, a manchete da T29 muda junto."""
    populacao = observacoes_da_bateria(scores_da_principal)
    por_modelo = {
        linha.model_key: linha
        for linha in sensibilidade(populacao, ["qwen3-8b", "qwen3-14b"], cortes=["S1"])
    }

    assert por_modelo["qwen3-14b"].fracao_da_aprovacao_sem_decisao > 0.5
    assert por_modelo["qwen3-8b"].fracao_da_aprovacao_sem_decisao < 0.25


def test_a_classe_c_inteira_esta_ausente_da_bateria_principal(scores_da_principal):
    """Menos C5, que é determinística — e cuja ausência é medição, não buraco de camada."""
    populacao = observacoes_da_bateria(scores_da_principal)
    observados = {f.codigo for f in frequencias(populacao, ["qwen3-8b", "qwen3-14b"])}
    ausentes = {a.codigo: a.motivo for a in codigos_ausentes(populacao)}

    assert not any(c.startswith("C") for c in observados)
    for codigo in CODIGOS_QUE_EXIGEM_N3:
        assert ausentes[codigo] == "camada_ausente"
    assert ausentes["C5"] == "medido_zero", (
        "citação inválida foi medida em 288 execuções e deu zero"
    )
