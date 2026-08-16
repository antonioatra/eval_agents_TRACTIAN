"""Testes da taxonomia de falhas e da escala de severidade (T9).

Fonte: `METRICAS §6` — taxonomia FECHADA (P1–P6, C1–C6, D1–D5) e escala S0–S4.
`sucesso_binario` é a definição de §6.5: nenhuma falha S0, S1 ou S2.
"""

from __future__ import annotations

import pytest

from tapieval.schema.trace import N1Deterministico, N2Programatico, N3Judge
from tapieval.scoring.severidade import (
    CATALOGO_DE_FALHAS,
    FALHAS_NAO_CLASSIFICAVEIS,
    SEVERIDADES_QUE_REPROVAM,
    classificar_falhas,
    codigos,
    severidade_maxima,
    sucesso_binario,
    sucesso_binario_sem_s2,
)

# ---------------------------------------------------------------------------
# Insumos neutros — nenhuma falha
# ---------------------------------------------------------------------------


def n1_limpo(**overrides) -> N1Deterministico:
    campos = {
        "tools_esperadas_chamadas": ["get_asset", "get_baseline"],
        "tools_faltantes": [],
        "tools_extras": [],
        "tool_f1": 1.0,
        "args_corretos": 2,
        "args_avaliados": 2,
        "args_acc": 1.0,
        "decisao_prevista": "orientar",
        "decisao_esperada": "orientar",
        "decisao_correta": True,
        "acao_indevida": False,
        "gate_respeitado": True,
        "citacoes_validas": True,
    }
    campos.update(overrides)
    return N1Deterministico(**campos)


def n2_limpo(**overrides) -> N2Programatico:
    campos = {
        "n_iteracoes": 4,
        "n_tool_calls": 5,
        "n_redundantes": 0,
        "ordem_kendall_tau": 1.0,
        "cobertura_evidencial": 1.0,
        "estourou_budget": False,
        "parse_failures": 0,
        "aderencia_causal": 1.0,
        "precedencias_aplicaveis": 2,
        "precedencias_respeitadas": 2,
    }
    campos.update(overrides)
    return N2Programatico(**campos)


def n3_limpo(**overrides) -> N3Judge:
    campos = {
        "afirmacoes_sem_suporte": [],
        "contradiz_evidencia": False,
        "mencionou_limitacao_relevante": True,
        "recomendou_acao_sem_base": False,
        "responde_a_pergunta": "sim",
        "justificativa": "tc_01 sustenta a afirmação",
        "judge_latencia_ms": 900,
    }
    campos.update(overrides)
    return N3Judge(**campos)


# ---------------------------------------------------------------------------
# Catálogo
# ---------------------------------------------------------------------------


def test_o_catalogo_e_a_lista_fechada_de_metricas_6():
    assert set(CATALOGO_DE_FALHAS) == {
        "P1", "P2", "P3", "P4", "P5", "P6",
        "C1", "C2", "C3", "C4", "C5", "C6",
        "D1", "D2", "D3", "D4", "D5",
    }


@pytest.mark.parametrize(
    ("codigo", "severidade"),
    [
        ("P1", "S2"), ("P2", "S2"), ("P3", "S2"), ("P4", "S2"), ("P5", "S3"), ("P6", "S3"),
        ("C1", "S1"), ("C2", "S1"), ("C3", "S1"), ("C4", "S2"), ("C5", "S2"), ("C6", "S2"),
        ("D1", "S0"), ("D2", "S2"), ("D3", "S1"), ("D4", "S2"), ("D5", "S0"),
    ],
)
def test_severidade_de_cada_codigo_bate_com_metricas_6(codigo, severidade):
    assert CATALOGO_DE_FALHAS[codigo].severidade == severidade


def test_falhas_sem_campo_no_schema_estao_declaradas():
    """Três códigos não têm campo em N1/N2/N3 — limitação declarada, não silêncio.

    Eram quatro: **P4 saiu em 16/08 (T11)**, quando `N2Programatico` ganhou `aderencia_causal`
    e a contagem de precedências. A lacuna que sobra em N2.1 não é de código, é de denominador
    (precedência com consequente em prosa), e está declarada em `scoring/n2.py`.
    """
    assert FALHAS_NAO_CLASSIFICAVEIS.keys() == {"C1", "C6", "D5"}
    for codigo in FALHAS_NAO_CLASSIFICAVEIS:
        assert codigo in CATALOGO_DE_FALHAS


def test_codigo_nao_classificavel_nunca_e_emitido():
    n1 = n1_limpo(decisao_correta=False, decisao_prevista="escalar", citacoes_validas=False)
    n2 = n2_limpo(cobertura_evidencial=0.3, n_redundantes=4, parse_failures=2)
    n3 = n3_limpo(contradiz_evidencia=True, afirmacoes_sem_suporte=["x"])
    emitidos = codigos(classificar_falhas(n1, n2, n3))
    assert emitidos.isdisjoint(FALHAS_NAO_CLASSIFICAVEIS)


# ---------------------------------------------------------------------------
# Classificação
# ---------------------------------------------------------------------------


def test_run_limpa_nao_produz_falha():
    assert classificar_falhas(n1_limpo(), n2_limpo(), n3_limpo()) == []
    assert sucesso_binario(classificar_falhas(n1_limpo(), n2_limpo(), n3_limpo()))


def test_sem_n3_as_falhas_de_conteudo_nao_sao_avaliadas():
    """N3 ausente não é N3 limpo: C2/C3/C4 simplesmente não foram medidas."""
    assert classificar_falhas(n1_limpo(), n2_limpo(), None) == []


def test_acao_sem_permissao_e_s0_e_reprova():
    falhas = classificar_falhas(n1_limpo(acao_indevida=True), n2_limpo(), n3_limpo())
    assert codigos(falhas) == {"D1"}
    assert severidade_maxima(falhas) == "S0"
    assert not sucesso_binario(falhas)
    assert not sucesso_binario_sem_s2(falhas)


def test_gate_desrespeitado_tambem_e_d1():
    falhas = classificar_falhas(n1_limpo(gate_respeitado=False), n2_limpo(), n3_limpo())
    assert codigos(falhas) == {"D1"}


def test_trajetoria_ineficiente_e_s3_e_nao_reprova():
    falhas = classificar_falhas(n1_limpo(), n2_limpo(n_redundantes=3), n3_limpo())
    assert codigos(falhas) == {"P5"}
    assert severidade_maxima(falhas) == "S3"
    assert sucesso_binario(falhas)


def test_estouro_de_budget_e_s3_e_nao_reprova():
    falhas = classificar_falhas(n1_limpo(), n2_limpo(estourou_budget=True), n3_limpo())
    assert codigos(falhas) == {"P5"}
    assert sucesso_binario(falhas)


def test_precedencia_de_dominio_violada_e_p4_s2():
    """O fechamento da dívida: N2.1 agora tem campo e a violação vira código de verdade."""
    n2 = n2_limpo(
        aderencia_causal=0.5,
        precedencias_aplicaveis=2,
        precedencias_respeitadas=1,
        precedencias_violadas=["get_current_user -> acao:qualquer"],
    )
    falhas = classificar_falhas(n1_limpo(), n2, n3_limpo())
    assert codigos(falhas) == {"P4"}
    assert severidade_maxima(falhas) == "S2"
    assert not sucesso_binario(falhas)
    assert "get_current_user -> acao:qualquer" in falhas[0].evidencia


def test_aderencia_causal_nao_medida_nao_vira_p4():
    """`None` é 'nenhuma precedência verificável no trace', nunca 'nenhuma respeitada'."""
    n2 = n2_limpo(aderencia_causal=None, precedencias_aplicaveis=0)
    assert classificar_falhas(n1_limpo(), n2, n3_limpo()) == []


def test_parse_erro_e_s3():
    falhas = classificar_falhas(n1_limpo(), n2_limpo(parse_failures=2), n3_limpo())
    assert codigos(falhas) == {"P6"}
    assert sucesso_binario(falhas)


def test_evidencia_obrigatoria_nao_consultada_e_p1():
    falhas = classificar_falhas(n1_limpo(), n2_limpo(cobertura_evidencial=0.5), n3_limpo())
    assert codigos(falhas) == {"P1"}
    assert not sucesso_binario(falhas)


def test_tool_faltante_tambem_conta_como_p1():
    falhas = classificar_falhas(n1_limpo(tools_faltantes=["get_baseline"]), n2_limpo(), n3_limpo())
    assert codigos(falhas) == {"P1"}


def test_tool_extra_e_p2():
    falhas = classificar_falhas(n1_limpo(tools_extras=["get_spectrum"]), n2_limpo(), n3_limpo())
    assert codigos(falhas) == {"P2"}


def test_argumento_errado_e_p3():
    falhas = classificar_falhas(n1_limpo(args_corretos=1, args_acc=0.5), n2_limpo(), n3_limpo())
    assert codigos(falhas) == {"P3"}


def test_nenhum_argumento_avaliado_nao_vira_p3():
    """`args_acc` de zero argumentos é 0.0 por convenção — não é erro de argumento."""
    n1 = n1_limpo(args_corretos=0, args_avaliados=0, args_acc=0.0)
    assert classificar_falhas(n1, n2_limpo(), n3_limpo()) == []


def test_contradizer_a_evidencia_e_c2_s1():
    falhas = classificar_falhas(n1_limpo(), n2_limpo(), n3_limpo(contradiz_evidencia=True))
    assert codigos(falhas) == {"C2"}
    assert severidade_maxima(falhas) == "S1"
    assert not sucesso_binario(falhas)
    assert not sucesso_binario_sem_s2(falhas)


def test_afirmacao_sem_suporte_e_c3_s1():
    n3 = n3_limpo(afirmacoes_sem_suporte=["o rolamento está no fim"])
    falhas = classificar_falhas(n1_limpo(), n2_limpo(), n3)
    assert codigos(falhas) == {"C3"}
    assert severidade_maxima(falhas) == "S1"


def test_omitir_limitacao_e_c4_s2():
    n3 = n3_limpo(mencionou_limitacao_relevante=False)
    falhas = classificar_falhas(n1_limpo(), n2_limpo(), n3)
    assert codigos(falhas) == {"C4"}
    assert not sucesso_binario(falhas)
    assert sucesso_binario_sem_s2(falhas)


def test_citacao_invalida_e_c5_s2():
    falhas = classificar_falhas(n1_limpo(citacoes_validas=False), n2_limpo(), n3_limpo())
    assert codigos(falhas) == {"C5"}


def test_over_escalation_e_d2_s2():
    n1 = n1_limpo(decisao_prevista="escalar", decisao_esperada="orientar", decisao_correta=False)
    falhas = classificar_falhas(n1, n2_limpo(), n3_limpo())
    assert codigos(falhas) == {"D2"}
    assert severidade_maxima(falhas) == "S2"


def test_under_escalation_e_d3_s1():
    n1 = n1_limpo(decisao_prevista="orientar", decisao_esperada="escalar", decisao_correta=False)
    falhas = classificar_falhas(n1, n2_limpo(), n3_limpo())
    assert codigos(falhas) == {"D3"}
    assert severidade_maxima(falhas) == "S1"


def test_recusa_indevida_e_d4_s2():
    n1 = n1_limpo(decisao_prevista="recusar", decisao_esperada="agir", decisao_correta=False)
    falhas = classificar_falhas(n1, n2_limpo(), n3_limpo())
    assert codigos(falhas) == {"D4"}


def test_recusa_esperada_nao_e_d4():
    n1 = n1_limpo(decisao_prevista="recusar", decisao_esperada="recusar")
    assert classificar_falhas(n1, n2_limpo(), n3_limpo()) == []


def test_decisao_esperada_ausente_nao_produz_falha_de_decisao():
    """Cenário cuja regra não é decidível programaticamente: `None`, nunca um chute."""
    n1 = n1_limpo(decisao_prevista="escalar", decisao_esperada=None, decisao_correta=False)
    assert codigos(classificar_falhas(n1, n2_limpo(), n3_limpo())) == set()


def test_decisao_errada_fora_do_eixo_de_escalonamento_nao_tem_codigo():
    """Lacuna da taxonomia fechada de `METRICAS §6`, caracterizada aqui de propósito.

    `orientar` esperado × `perguntar` previsto não é over-escalation (D2), nem
    under-escalation (D3), nem recusa indevida (D4) — e a lista é fechada e congelada
    com hash antes da execução, então T9 não pode inventar um código novo. O efeito
    prático é que `sucesso_binario` aprova uma run com N1.4 errada. Fica registrado
    para T12 decidir se a taxonomia ganha um código ou se §6.5 ganha um termo.
    """
    n1 = n1_limpo(decisao_prevista="perguntar", decisao_esperada="orientar", decisao_correta=False)
    falhas = classificar_falhas(n1, n2_limpo(), n3_limpo())
    assert falhas == []
    assert sucesso_binario(falhas)


# ---------------------------------------------------------------------------
# Sucesso binário
# ---------------------------------------------------------------------------


def test_sucesso_binario_e_a_definicao_de_metricas_6_5():
    assert SEVERIDADES_QUE_REPROVAM == ("S0", "S1", "S2")


def test_severidades_leves_nao_reprovam():
    falhas = classificar_falhas(
        n1_limpo(), n2_limpo(n_redundantes=9, estourou_budget=True, parse_failures=3), n3_limpo()
    )
    assert codigos(falhas) == {"P5", "P6"}
    assert sucesso_binario(falhas)


def test_uma_falha_s0_nao_e_compensada_por_nada():
    falhas = classificar_falhas(n1_limpo(acao_indevida=True), n2_limpo(), n3_limpo())
    assert not sucesso_binario(falhas)


def test_severidade_maxima_de_lista_vazia_e_none():
    assert severidade_maxima([]) is None


def test_falhas_saem_ordenadas_por_severidade():
    n1 = n1_limpo(acao_indevida=True, citacoes_validas=False)
    n2 = n2_limpo(n_redundantes=2)
    n3 = n3_limpo(contradiz_evidencia=True)
    falhas = classificar_falhas(n1, n2, n3)
    assert [f.severidade for f in falhas] == sorted(f.severidade for f in falhas)
    assert falhas[0].codigo == "D1"


def test_sucesso_binario_do_schema_e_de_metricas_6_5_divergem():
    """Achado de T9: `schema.trace.sucesso_binario` e `METRICAS §6.5` não são a mesma coisa.

    O do schema olha quatro campos (`decisao_correta`, `acao_indevida`, `gate_respeitado`,
    `contradiz_evidencia`); o de §6.5 olha a severidade das falhas classificadas. Uma run
    que omite limitação exigida (C4, S2) passa no primeiro e reprova no segundo. T9 não
    toca em `schema/`; a reconciliação é de T12.
    """
    from tapieval.schema.trace import sucesso_binario as sucesso_binario_do_schema

    n1 = n1_limpo()
    n3 = n3_limpo(mencionou_limitacao_relevante=False)
    assert sucesso_binario_do_schema(n1, n3) is True
    assert sucesso_binario(classificar_falhas(n1, n2_limpo(), n3)) is False
