"""INS.1, INS.2 e INS.3 — o recall por camada e o número que testa H0.

O QUE ESTE ARQUIVO PRENDE
    A conta em si é simples: interseção de conjuntos dividida por tamanho. O que é difícil, e
    o que os testes abaixo prendem, é **o que a conta pode significar** — porque três
    propriedades do gold deste projeto tornam a leitura ingênua falsa:

    1. a metade determinística do gold é a saída do próprio detector (A27), então
       `Recall(n1n2)` é identidade e não medição;
    2. o gold foi rotulado às cegas, então C2, C3 e C7 não existem nele e o judge `com_trace`
       não pode ganhar recall por eles;
    3. `ΔRecall` sobrevive a (1) porque é uma diferença — e é por isso que `METRICAS §7`
       marca a INS.2, e não a INS.1, como o número da hipótese.

    O teste `test_o_delta_cancela_a_parte_deterministica` é a (3) escrita como propriedade
    verificada, e não como frase num documento que ninguém confere.
"""

from __future__ import annotations

import math

import pytest

from tapieval.scoring.ins import (
    ErroDeINS,
    ItemAvaliado,
    curva,
    falso_alarme,
    ganho_incremental,
    montar_item,
    recall_por_camada,
    recall_por_classe,
)
from tests.test_gold_humano import humano, judge
from tests.test_severidade import n1_limpo, n2_limpo


def item(run_id: str, gold: set[str], **camadas) -> ItemAvaliado:
    """Um item montado à mão, para testar a aritmética sem passar pelo classificador."""
    base = frozenset(camadas.get("n1n2", set()))
    return ItemAvaliado(
        run_id=run_id,
        gold=frozenset(gold),
        detectado={
            "n1n2": base,
            "n1n2n3_cego": frozenset(camadas.get("n1n2n3_cego", base)),
            "n1n2n3_com_trace": frozenset(camadas.get("n1n2n3_com_trace", base)),
        },
    )


# ---------------------------------------------------------------------------
# INS.1 — e a bandeira que impede a leitura errada
# ---------------------------------------------------------------------------


def test_recall_e_micro_e_nao_media_de_razoes():
    """Com n=20, a média por run é dominada pelas runs com um código só no gold.

    Aqui a run A tem 4 códigos e a camada pega 1; a run B tem 1 e a camada pega 1. Micro dá
    2/5 = 0,4; macro daria (0,25 + 1)/2 = 0,625. O denominador de `METRICAS §7` é "falhas
    reais", no plural e sem "por execução".
    """
    itens = [
        item("A", {"P1", "P2", "C1", "D6"}, n1n2={"P1"}),
        item("B", {"P1"}, n1n2={"P1"}),
    ]

    assert recall_por_camada(itens, "n1n2").valor == pytest.approx(2 / 5)


def test_a_camada_deterministica_vem_marcada_como_identidade():
    """`Recall(n1n2) == 1.0` sem a bandeira ao lado afirmaria que a camada barata pega tudo.

    O que se mediu foi `x == x`: a metade P/D/C5 do gold sai do mesmo `n1`/`n2` que a detecção
    (A27). A bandeira é o que impede a figura de dizer o contrário do `METRICAS §11`.
    """
    assert recall_por_camada([item("A", {"P1"}, n1n2={"P1"})], "n1n2").identidade is True
    assert recall_por_camada([item("A", {"P1"}, n1n2={"P1"})], "n1n2n3_cego").identidade is False


def test_run_com_gold_vazio_fica_fora_do_denominador():
    """O humano não viu falha: não há o que recuperar, e 0/0 não é recall 0.

    Ela não sai do trabalho — é exatamente onde uma acusação vira falso alarme na INS.3.
    """
    itens = [item("A", {"P1"}, n1n2={"P1"}), item("B", set(), n1n2=set())]

    recall = recall_por_camada(itens, "n1n2")
    assert recall.n_gold == 1
    assert recall.valor == pytest.approx(1.0)


def test_recall_sem_gold_nenhum_e_nan_e_nao_zero():
    """Zero diria "a camada não pegou nada"; NaN diz "não havia o que pegar"."""
    assert math.isnan(recall_por_camada([item("A", set(), n1n2=set())], "n1n2").valor)


# ---------------------------------------------------------------------------
# INS.2 — a propriedade que sustenta H0
# ---------------------------------------------------------------------------


def test_o_delta_cancela_a_parte_deterministica():
    """A limitação do A27 não contamina a INS.2, e é por isso que ela é o número da hipótese.

    Os dois itens têm P1 e D6 iguais nos dois lados por construção. Se o delta dependesse
    deles, mudar quantos códigos determinísticos existem mudaria o resultado — aqui o item B
    tem o dobro deles, e o delta continua sendo só a fração de conteúdo que o judge alcança.
    """
    so_p_e_d = [
        item("A", {"P1", "C1"}, n1n2={"P1"}, n1n2n3_com_trace={"P1", "C1"}),
        item("B", {"P1", "D6", "C1"}, n1n2={"P1", "D6"}, n1n2n3_com_trace={"P1", "D6", "C1"}),
    ]
    ganho = ganho_incremental(so_p_e_d, repeticoes=200)

    # 2 códigos de conteúdo ganhos em 5 do gold
    assert ganho.delta == pytest.approx(2 / 5)
    assert ganho.codigos_ganhos == {"C1"}


def test_o_delta_negativo_nao_e_truncado():
    """Camada mais cara que detecta MENOS refuta H0 — e a refutação é resultado.

    `ARQUITETURA §12`: se o judge não acrescentar detecção, a conclusão vira "avaliação
    determinística basta", que o documento chama de mais forte que a confirmação. Truncar em
    zero apagaria exatamente esse achado.
    """
    itens = [item("A", {"P1", "C1"}, n1n2={"P1", "C1"}, n1n2n3_com_trace={"P1"})]

    assert ganho_incremental(itens, repeticoes=200).delta == pytest.approx(-0.5)


def test_o_ic_e_reproduzivel_com_a_mesma_seed():
    """Dois `make` do mesmo notebook não podem imprimir intervalos diferentes."""
    itens = [
        item(f"r{i}", {"P1", "C1"}, n1n2={"P1"}, n1n2n3_com_trace={"P1", "C1"})
        for i in range(6)
    ] + [item("r6", {"P1", "C1"}, n1n2={"P1"}, n1n2n3_com_trace={"P1"})]

    primeiro = ganho_incremental(itens, repeticoes=500)
    segundo = ganho_incremental(itens, repeticoes=500)

    assert primeiro.ic95 == segundo.ic95
    # E o IC é mesmo um intervalo, não um ponto: com 7 itens e um deles discordando, a
    # reamostragem tem de produzir deltas diferentes entre si. Um IC degenerado passaria na
    # asserção de reprodutibilidade acima sem ter feito bootstrap nenhum.
    assert primeiro.ic95[0] < primeiro.ic95[1]


def test_o_ic_contem_a_estimativa_pontual():
    itens = [
        item(f"r{i}", {"P1", "C1"}, n1n2={"P1"}, n1n2n3_com_trace={"P1", "C1"})
        for i in range(5)
    ] + [item("r5", {"P1", "C1"}, n1n2={"P1"}, n1n2n3_com_trace={"P1"})]

    ganho = ganho_incremental(itens, repeticoes=2000)
    inferior, superior = ganho.ic95

    assert inferior <= ganho.delta <= superior


def test_delta_sobre_conjunto_vazio_e_erro_e_nao_zero():
    """Zero se lê como "o judge não acrescentou nada" sobre um experimento que não aconteceu."""
    with pytest.raises(ErroDeINS, match="indefinido"):
        ganho_incremental([])


# ---------------------------------------------------------------------------
# INS.3 e a estratificação por classe
# ---------------------------------------------------------------------------


def test_falso_alarme_conta_acusacao_que_o_gold_nao_sustenta():
    """Detector barulhento não é barato: recall comprado com ruído tem de aparecer."""
    itens = [item("A", {"P1"}, n1n2={"P1"}, n1n2n3_com_trace={"P1", "C2", "C3"})]

    assert falso_alarme(itens, "n1n2") == pytest.approx(0.0)
    assert falso_alarme(itens, "n1n2n3_com_trace") == pytest.approx(2 / 3)


def test_a_estratificacao_por_classe_separa_o_ganho():
    """H0 prediz que o ganho se concentra em C. Sem quebrar por classe, a curva agregada
    confirmaria a hipótese até quando o ganho estivesse espalhado por igual — que
    `ARQUITETURA §12` diz refutar a estratificação."""
    itens = [item("A", {"P1", "C1", "D6"}, n1n2={"P1", "D6"}, n1n2n3_cego={"P1", "C1", "D6"})]

    por_classe = recall_por_classe(itens, "n1n2n3_cego")

    assert por_classe["C"].valor == pytest.approx(1.0)
    assert por_classe["P"].valor == pytest.approx(1.0)
    assert recall_por_classe(itens, "n1n2")["C"].valor == pytest.approx(0.0)


def test_a_curva_tem_as_tres_camadas_na_ordem():
    itens = [item("A", {"P1"}, n1n2={"P1"})]

    assert [r.camada for r in curva(itens)] == [
        "n1n2",
        "n1n2n3_cego",
        "n1n2n3_com_trace",
    ]


# ---------------------------------------------------------------------------
# Recusas
# ---------------------------------------------------------------------------


def test_run_duplicada_e_erro():
    """Uma sessão de rotulagem retomada que reescreva rótulos faz a run pesar dobrado."""
    with pytest.raises(ErroDeINS, match="mais de uma vez"):
        recall_por_camada([item("A", {"P1"}), item("A", {"P1"})], "n1n2")


def test_camada_faltando_e_erro():
    incompleto = ItemAvaliado(run_id="A", gold=frozenset({"P1"}), detectado={"n1n2": frozenset()})

    with pytest.raises(ErroDeINS, match="não tem detecção"):
        recall_por_camada([incompleto], "n1n2")


# ---------------------------------------------------------------------------
# A montagem, contra o classificador de verdade
# ---------------------------------------------------------------------------


def test_judge_nao_julgado_nao_vira_camada_vazia():
    """`None` é NÃO MEDIDO. Um conjunto vazio diria "o judge olhou e não achou nada".

    É a mesma leitura que `classificar_falhas` já dá a `n3=None`, e o formato de erro do X9 e
    do A10: a ausência de medição virando ausência de falha, sempre na direção que favorece a
    hipótese.
    """
    montado = montar_item(
        "A", n1_limpo(tools_faltantes=["get_baseline"]), n2_limpo(), humano(), {}
    )

    assert montado.detectado["n1n2n3_cego"] == montado.detectado["n1n2"]
    assert montado.detectado["n1n2"], "o item precisa ter falha determinística para o teste valer"


def test_a_montagem_produz_o_gold_pelo_caminho_do_a27():
    """O gold sai de `classificar_falhas(n1, n2, humano)` — não de código digitado."""
    montado = montar_item(
        "A",
        n1_limpo(tools_faltantes=["get_baseline"]),
        n2_limpo(cobertura_evidencial=0.5),
        humano(),
        {"com_trace": judge()},
    )

    assert "P1" in montado.gold, "a falha de processo tem de estar nos dois lados (A27)"
    assert {c for c in montado.gold if c.startswith("C")}, "o veredito humano gera conteúdo"
    assert montado.acertos("n1n2") == montado.gold & montado.detectado["n1n2"]
