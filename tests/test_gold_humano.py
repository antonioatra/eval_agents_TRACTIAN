"""A27 — o gold de INS.1 sai da rotulagem humana, e este arquivo é o caminho escrito.

O QUE O A27 ACUSAVA
    `METRICAS §5` define N4.1 como "houve falha? qual código da taxonomia? qual severidade
    S0–S3?", e nem `N4Humano` nem `RotuloHumano` têm campo para código ou severidade. A
    conclusão foi que não existia caminho que produzisse "falhas reais (gold)" a partir de uma
    rotulagem, e que INS.1 — logo INS.2, o número que testa H0 — ficava sem denominador.

O QUE A INVESTIGAÇÃO DE 29/08 ENCONTROU
    O caminho existe e é o desenho, não uma coincidência. `classificar_falhas` nunca LÊ um
    código: ela o DERIVA dos campos fechados da rubrica mais N1/N2. É por isso que a taxonomia
    pode ser lista fechada e congelada por hash — e é por isso que pedir o código ao rotulador
    seria pior que redundante, porque abriria a porta para um código fora da lista ou
    incoerente com os campos que ele mesmo marcou.

    O que faltava era o TIPO. A anotação do slot dizia `N3Judge`, então o caminho pelo qual o
    gold existe era um erro de tipo que nenhum teste exercitava. `VereditoDaRubrica` (Protocol)
    é a correção, e os testes abaixo são o caminho deixando de ser tácito.

O QUE ESTE ARQUIVO TAMBÉM FIXA, E É A PARTE DESCONFORTÁVEL
    O gold assim construído reusa o mesmo `n1`/`n2` do detector. `test_a_metade_pd_do_gold_e_a
    _saida_do_proprio_detector` existe para que essa limitação seja uma propriedade verificada
    e não uma frase no `METRICAS` que ninguém confere — e o teste seguinte mostra por que a
    INS.2 sobrevive a ela.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tapieval.labeling.cli import RotuloHumano
from tapieval.schema.trace import N3Judge, N4Humano
from tapieval.scoring.severidade import VereditoDaRubrica, classificar_falhas
from tests.test_severidade import n1_limpo, n2_limpo

VEREDITO = {
    "causa_raiz_correta": False,
    "mencionou_limitacao_relevante": False,
    "responde_a_pergunta": "parcial",
    "afirmacoes_sem_suporte": ["a bomba está em falha iminente"],
    "contradiz_evidencia": True,
    "recomendou_acao_sem_base": True,
}


def humano(**trocas) -> N4Humano:
    return N4Humano(rotulador="antonio", amostra="estimativa", **{**VEREDITO, **trocas})


def judge(**trocas) -> N3Judge:
    return N3Judge(
        configuracao="com_trace",
        justificativa="cita tc_01 e tc_03",
        judge_latencia_ms=120,
        **{**VEREDITO, **trocas},
    )


def codigos(falhas) -> list[str]:
    return [falha.codigo for falha in falhas]


# ---------------------------------------------------------------------------
# O caminho do gold, agora tipado
# ---------------------------------------------------------------------------


def test_o_rotulo_humano_satisfaz_o_slot_do_veredito():
    """Se ele deixar de satisfazer, o gold para de existir — e sem este teste isso
    aconteceria em silêncio, porque `classificar_falhas` aceita qualquer coisa que tenha os
    atributos certos."""
    assert isinstance(humano(), VereditoDaRubrica)
    assert isinstance(judge(), VereditoDaRubrica)


def test_o_mesmo_veredito_produz_o_mesmo_codigo_venha_do_humano_ou_do_judge():
    """É esta identidade que faz INS.1 comparar duas medidas da MESMA coisa.

    Se as duas fontes produzissem taxonomias diferentes para o mesmo veredito, o recall
    mediria a diferença entre dois classificadores e não entre dois observadores.
    """
    n1, n2 = n1_limpo(), n2_limpo()

    gold = classificar_falhas(n1, n2, humano())
    detectado = classificar_falhas(n1, n2, judge())

    assert codigos(gold) == codigos(detectado) == ["C1", "C2", "C3", "C7", "C4"]
    assert [f.severidade for f in gold] == [f.severidade for f in detectado]


def test_o_rotulo_da_cli_chega_ao_gold_pela_conversao_que_ja_existe():
    """`RotuloHumano` (saída da CLI) e `N4Humano` (schema) são classes diferentes, e a
    conversão mora num lugar só — `para_n4humano`. O gold entra por ali ou não entra."""
    rotulo = RotuloHumano(
        run_id="r1",
        experiment_id="calibracao_2026-08-24",
        scenario_id="aut_01",
        model_key="qwen3-14b",
        variant_id="base",
        env_seed="envs002",
        sample_seed=23,
        amostra="estimativa",
        configuracao="com_trace",
        rotulador="antonio",
        seed_da_amostragem=42,
        rotulado_em=datetime(2026, 8, 29, tzinfo=UTC),
        justificativa="a resposta afirma falha iminente sem nada em tc_01..tc_04",
        **VEREDITO,
    )

    gold = classificar_falhas(n1_limpo(), n2_limpo(), rotulo.para_n4humano())

    assert codigos(gold) == ["C1", "C2", "C3", "C7", "C4"]


def test_o_humano_nao_digita_codigo_nem_severidade():
    """O A27 propunha acrescentar os campos. A taxonomia é lista FECHADA congelada por hash
    (`METRICAS §6`), e um código digitado poderia cair fora dela ou contradizer os campos que
    o próprio rotulador marcou. O rótulo não tem onde guardar um, e é de propósito."""
    campos = set(N4Humano.model_fields)

    assert not campos & {"codigo", "codigos", "severidade", "falhas"}
    assert {"causa_raiz_correta", "contradiz_evidencia", "recomendou_acao_sem_base"} <= campos


# ---------------------------------------------------------------------------
# O que o caminho custa — a limitação que `METRICAS §11` declara
# ---------------------------------------------------------------------------


def test_a_metade_pd_do_gold_e_a_saida_do_proprio_detector():
    """⚠️ O gold reusa o MESMO `n1`/`n2` do detector, então P/D e C5 entram nos dois lados
    iguais por construção: `Recall(N1+N2)` ali é identidade, não medição, e INS.3 é zero.

    Este teste existe para que a limitação seja verificada e não só escrita. Se um dia o gold
    passar a ter P/D independentes, ele quebra — e quebrar é a resposta certa, porque aí a
    frase do `METRICAS §11` terá deixado de valer.
    """
    n1 = n1_limpo(tools_faltantes=["get_baseline"], citacoes_validas=False)
    n2 = n2_limpo(cobertura_evidencial=0.5, estourou_budget=True)

    gold = set(codigos(classificar_falhas(n1, n2, humano())))
    so_deterministico = set(codigos(classificar_falhas(n1, n2, None)))

    assert so_deterministico, "o cenário tem de ter falha determinística, senão não testa nada"
    assert so_deterministico <= gold
    assert so_deterministico == {c for c in gold if not c.startswith("C") or c == "C5"}


def test_ins2_sobrevive_a_essa_limitacao_porque_e_uma_diferenca():
    """A parte idêntica cancela: `ΔRecall(N3 | N1+N2)` fica sendo a fração do gold que SÓ o
    judge alcança, e nenhum código determinístico entra na conta.

    Que o número que testa H0 seja justamente o robusto a este defeito não é sorte — é o
    motivo de `METRICAS §7` marcar INS.2, e não INS.1, como "o número que testa H0".
    """
    n1 = n1_limpo(tools_faltantes=["get_baseline"])
    n2 = n2_limpo(cobertura_evidencial=0.5)

    gold = set(codigos(classificar_falhas(n1, n2, humano())))
    sem_judge = set(codigos(classificar_falhas(n1, n2, None)))
    # o judge discorda do humano em dois campos de conteúdo
    com_judge = set(
        codigos(
            classificar_falhas(
                n1, n2, judge(causa_raiz_correta=True, recomendou_acao_sem_base=False)
            )
        )
    )

    recall_sem = len(sem_judge & gold) / len(gold)
    recall_com = len(com_judge & gold) / len(gold)
    ganho = recall_com - recall_sem

    assert ganho > 0
    # e o ganho é exatamente o conteúdo em que os dois concordaram
    conteudo_concordante = (com_judge & gold) - sem_judge
    assert conteudo_concordante == {"C2", "C3", "C4"}
    assert ganho == pytest.approx(len(conteudo_concordante) / len(gold))


def test_a_amostra_de_melhoria_nao_pode_entrar_no_denominador_do_recall():
    """`METRICAS §7.1` dizia "35 execuções" como fonte do recall e `§11` diz "n=20 para κ e
    recall". As 15 de melhoria são escolhidas por desacordo, e recall sobre elas é enviesado
    pelo mesmo motivo que κ — o A25 mediu pior: saem 15/15 `sem_resposta_final`, um modo de
    falha só.

    O rótulo carrega `amostra` justamente para que a separação seja verificável, e não
    disciplina de quem escreve o notebook.
    """
    assert humano(**{}).amostra == "estimativa"
    de_melhoria = N4Humano(rotulador="antonio", amostra="melhoria", **VEREDITO)

    assert de_melhoria.amostra == "melhoria"
    # o campo existe e é obrigatório: não dá para produzir um rótulo sem dizer de qual amostra
    with pytest.raises(ValueError, match="amostra"):
        N4Humano(rotulador="antonio", **VEREDITO)
