"""T23 — κ de Cohen campo a campo (METRICAS §7, INS.6).

O que estes testes protegem, em ordem de importância:

1. **os dois do enunciado do plano** — κ de um rotulador contra si mesmo é 1.0, e o cálculo
   usa só a amostra de estimativa (n=20, não 35);
2. **a semântica de `None`** — par não medido sai do denominador daquele campo em vez de
   virar discordância. É o caso que acontece de fato com o judge cego, que tem `None` nos
   três campos que exigem trace por construção. Um teste mostra explicitamente que trocar
   `None` por `False` muda o número, para a diferença não poder ser reintroduzida por
   descuido;
3. **κ indefinido não é κ zero** — `p_e = 1` devolve NaN nomeado com motivo, e não `0.0`
   nem `ZeroDivisionError`. O teste-irmão mostra o oposto: 95% de concordância bruta com
   κ = 0.0 exato, que é a razão de κ existir;
4. **as três categorias de `responde_a_pergunta`** — o caso montado dá κ = 0.5 com três
   categorias e κ = 1.0 se `parcial` for colapsado em `nao`, então o teste quebra se alguém
   binarizar o campo;
5. **um valor conhecido calculado à mão** (a=8, b=2, c=1, d=9 → κ = 0.7), para o estimador
   não poder ser reescrito para outra fórmula em silêncio;
6. **a tabela do notebook** — os cinco números que a INS.6 reporta juntos.

Os testes de aritmética usam `kappa_de_cohen` direto, sobre pares de categorias: ele é o
estimador e precisa ser auditável sozinho. Os de contrato usam os modelos reais
(`N3Judge`, `N4Humano`, `RotuloHumano`), porque é lá que a duplicação de nomes de campo entre
schema e rubrica pode divergir sem que a aritmética perceba.
"""

from __future__ import annotations

import math
from dataclasses import asdict
from datetime import UTC, datetime

import pytest

from tapieval.labeling.cli import CAMPOS_QUE_EXIGEM_TRACE as CAMPOS_DE_TRACE_DA_ROTULAGEM
from tapieval.labeling.cli import RotuloHumano
from tapieval.schema.trace import N3Judge, N4Humano
from tapieval.scoring.agreement import (
    CAMPOS_DA_RUBRICA,
    CAMPOS_QUE_EXIGEM_TRACE,
    CATEGORIAS_DE_RESPONDE_A_PERGUNTA,
    ConfiguracoesDiferentes,
    RotuloDuplicado,
    RotuloForaDaAmostraDeEstimativa,
    RotuloSemAmostra,
    ValorForaDaRubrica,
    categoria_do_campo,
    faixa_de_kappa,
    kappa_de_cohen,
    kappa_por_campo,
    tabela_por_campo,
)

AGORA = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Construtores — dicts, porque o notebook lê `labels/humano_*.jsonl` como JSONL
# ---------------------------------------------------------------------------


def julgamento(**campos) -> dict:
    """Um julgamento do judge, com trace (responde os seis campos)."""
    base = {
        "configuracao": "com_trace",
        "causa_raiz_correta": True,
        "mencionou_limitacao_relevante": True,
        "responde_a_pergunta": "sim",
        "afirmacoes_sem_suporte": [],
        "contradiz_evidencia": False,
        "recomendou_acao_sem_base": False,
        "justificativa": "tc_01",
    }
    return base | campos


def rotulo(**campos) -> dict:
    """Um rótulo humano da amostra de estimativa."""
    base = julgamento() | {"amostra": "estimativa", "rotulador": "antonio"}
    return base | campos


def pares_de(judges: list[dict], humanos: list[dict]) -> list[tuple[dict, dict]]:
    return list(zip(judges, humanos, strict=True))


# ---------------------------------------------------------------------------
# 1. Contrato: a rubrica daqui é a de METRICAS §4, e é a do schema
# ---------------------------------------------------------------------------


def test_a_rubrica_tem_os_seis_campos_de_metricas_4():
    """Seis campos de propósito. `justificativa` é texto livre e não é categoria."""
    assert CAMPOS_DA_RUBRICA == (
        "causa_raiz_correta",
        "mencionou_limitacao_relevante",
        "responde_a_pergunta",
        "afirmacoes_sem_suporte",
        "contradiz_evidencia",
        "recomendou_acao_sem_base",
    )


def test_os_campos_daqui_existem_nos_dois_lados_do_par():
    """Mesmos campos dos dois lados é requisito, não descrição (docstring do `N4Humano`):
    um campo que exista de um lado só não tem par para concordar, e o κ daquele campo
    ficaria vazio em silêncio."""
    for campo in CAMPOS_DA_RUBRICA:
        assert campo in N3Judge.model_fields, campo
        assert campo in N4Humano.model_fields, campo
        assert campo in RotuloHumano.model_fields, campo


def test_os_campos_que_exigem_trace_batem_com_os_da_rotulagem():
    """A tupla está duplicada em `labeling/cli.py` e aqui — este teste é o que impede as
    duas de divergirem. Se um quarto campo passar a exigir trace, a suíte quebra."""
    assert CAMPOS_QUE_EXIGEM_TRACE == CAMPOS_DE_TRACE_DA_ROTULAGEM


def test_nao_existe_kappa_agregado_no_modulo():
    """`METRICAS §7` define a INS.6 campo a campo. A ausência de um κ único é de propósito:
    a média esconderia o campo que reprova. `kappa_por_campo` devolve seis, sempre."""
    resultados = kappa_por_campo(pares_de([julgamento()], [rotulo()]))
    assert tuple(resultados) == CAMPOS_DA_RUBRICA


# ---------------------------------------------------------------------------
# 2. Os dois casos do enunciado do plano
# ---------------------------------------------------------------------------


def test_rotulador_contra_si_mesmo_da_um_em_todos_os_campos():
    """O caso obrigatório. Com p_o = 1, κ = (1 - p_e)/(1 - p_e) = 1.0 exato.

    Os rótulos variam de propósito em todos os seis campos: contra si mesmo com um valor só
    o κ seria indefinido (p_e = 1), e o teste passaria pelo motivo errado.
    """
    rotulos = [
        rotulo(
            causa_raiz_correta=indice % 2 == 0,
            mencionou_limitacao_relevante=indice % 3 == 0,
            responde_a_pergunta=CATEGORIAS_DE_RESPONDE_A_PERGUNTA[indice % 3],
            afirmacoes_sem_suporte=[f"a{indice}"] if indice % 2 else [],
            contradiz_evidencia=indice % 4 == 0,
            recomendou_acao_sem_base=indice % 5 == 0,
        )
        for indice in range(20)
    ]
    for campo, resultado in kappa_por_campo(pares_de(rotulos, rotulos)).items():
        assert resultado.kappa == 1.0, campo
        assert resultado.n_pares == 20
        assert resultado.n_descartados == 0
        assert not resultado.indefinido
        assert resultado.faixa == "excelente"


def test_so_a_amostra_de_estimativa_entra_e_a_de_melhoria_e_erro_nomeado():
    """n = 20, não 35. As 15 de melhoria são recusadas por nome, não filtradas em silêncio.

    `METRICAS §5`: a fila de melhoria prioriza exatamente os casos difíceis, e concordância
    medida sobre casos difíceis não estima concordância na população.
    """
    estimativa = [rotulo(causa_raiz_correta=indice % 2 == 0) for indice in range(20)]
    melhoria = [rotulo(amostra="melhoria") for _ in range(15)]
    judges = [julgamento(causa_raiz_correta=indice % 2 == 0) for indice in range(35)]

    with pytest.raises(RotuloForaDaAmostraDeEstimativa, match="melhoria"):
        kappa_por_campo(pares_de(judges, estimativa + melhoria))

    so_estimativa = kappa_por_campo(pares_de(judges[:20], estimativa))
    assert so_estimativa["causa_raiz_correta"].n_pares == 20


def test_rotulo_sem_o_campo_amostra_e_recusado():
    """`ARQUITETURA §5`, decisão 7. Assumir `estimativa` na ausência devolveria o acidente
    que o campo existe para impossibilitar."""
    sem_amostra = rotulo()
    del sem_amostra["amostra"]
    with pytest.raises(RotuloSemAmostra, match="amostra"):
        kappa_por_campo(pares_de([julgamento()], [sem_amostra]))


# ---------------------------------------------------------------------------
# 3. A aritmética — valor conhecido, calculado à mão
# ---------------------------------------------------------------------------


def test_valor_conhecido_calculado_a_mao():
    """Tabela 2×2 com a=8 (T,T), b=2 (T,F), c=1 (F,T), d=9 (F,F), n=20.

        p_o = (8 + 9)/20 = 0.85
        p_e = (10/20)(9/20) + (10/20)(11/20) = 0.225 + 0.275 = 0.50
        κ   = (0.85 - 0.50)/(1 - 0.50) = 0.70

    0.70 cai na faixa "aceitável, declarar como limitação" de `METRICAS §7`.
    """
    pares = [(True, True)] * 8 + [(True, False)] * 2 + [(False, True)] + [(False, False)] * 9
    resultado = kappa_de_cohen(pares)

    assert resultado.n_pares == 20
    assert resultado.concordancia_observada == pytest.approx(0.85)
    assert resultado.concordancia_esperada == pytest.approx(0.50)
    assert resultado.kappa == pytest.approx(0.70)
    assert resultado.faixa == "aceitavel"


def test_concordancia_bruta_alta_com_kappa_zero():
    """95% de concordância e κ = 0.0 exato — a razão de a INS.6 pedir κ e não taxa de acerto.

    Judge diz `True` nos 20; humano diz `True` em 19. Os dois quase não têm variância, e o
    acaso já explicaria tudo o que eles concordaram.
    """
    pares = [(True, True)] * 19 + [(True, False)]
    resultado = kappa_de_cohen(pares)

    assert resultado.concordancia_observada == pytest.approx(0.95)
    assert resultado.kappa == pytest.approx(0.0)
    assert not resultado.indefinido
    assert resultado.faixa == "insuficiente"


def test_discordancia_total_da_kappa_negativo():
    """Pior que o acaso é um resultado legítimo, não um erro a truncar em zero."""
    resultado = kappa_de_cohen([(True, False)] * 10 + [(False, True)] * 10)
    assert resultado.kappa < 0
    assert resultado.faixa == "insuficiente"


# ---------------------------------------------------------------------------
# 4. `None` sai do denominador — nunca vira discordância
# ---------------------------------------------------------------------------


def test_par_com_none_de_qualquer_lado_e_descartado():
    """`None` é "não medido". Ele conta em `n_descartados`, não em `n_pares`."""
    pares = [(True, True), (None, True), (True, None), (None, None), (False, False)]
    resultado = kappa_de_cohen(pares)

    assert resultado.n_pares == 2
    assert resultado.n_descartados == 3


def test_none_nao_e_a_mesma_coisa_que_false():
    """O teste que impede a regressão de `None` virar categoria.

    Mesmos dados, com `None` de um lado em 4 pares. Tratado como descarte, sobram 6 pares
    concordantes; tratado como `False`, os 4 viram discordância e o κ despenca.
    """
    com_none = [(True, True)] * 3 + [(False, False)] * 3 + [(None, True)] * 4
    como_false = [(True, True)] * 3 + [(False, False)] * 3 + [(False, True)] * 4

    assert kappa_de_cohen(com_none).kappa == 1.0
    assert kappa_de_cohen(com_none).n_pares == 6
    assert kappa_de_cohen(como_false).kappa < 1.0
    assert kappa_de_cohen(como_false).n_pares == 10


def test_judge_cego_deixa_indefinidos_os_tres_campos_que_exigem_trace():
    """O caso real, e o motivo de a regra do `None` existir.

    O judge cego tem `None` nos três campos por invariante de schema. Se `None` contasse como
    `False`, o κ desses três campos seria negativo por defeito do instrumento — e a comparação
    cego × com-trace, que é metade do achado da T20, mediria a contagem em vez da rubrica.
    """
    cegos = [
        julgamento(
            configuracao="cego",
            causa_raiz_correta=indice % 2 == 0,
            afirmacoes_sem_suporte=None,
            contradiz_evidencia=None,
            recomendou_acao_sem_base=None,
        )
        for indice in range(20)
    ]
    humanos = [
        rotulo(configuracao="cego", causa_raiz_correta=indice % 2 == 0) for indice in range(20)
    ]
    resultados = kappa_por_campo(pares_de(cegos, humanos))

    assert resultados["causa_raiz_correta"].kappa == 1.0
    for campo in CAMPOS_QUE_EXIGEM_TRACE:
        assert resultados[campo].n_pares == 0
        assert resultados[campo].n_descartados == 20
        assert math.isnan(resultados[campo].kappa)
        assert resultados[campo].indefinido
        assert "nenhum par" in resultados[campo].motivo_indefinido


# ---------------------------------------------------------------------------
# 5. κ indefinido tem nome — e não é κ zero
# ---------------------------------------------------------------------------


def test_pe_igual_a_um_devolve_indefinido_e_nao_zero_nem_excecao():
    """Os dois lados numa categoria só: `p_e = 1`, denominador zero.

    `0.0` leria como "concordância ao acaso" quando o que houve foi concordância PERFEITA sem
    variância para medir — o sinal invertido da conclusão. NaN nomeado, com motivo.
    """
    resultado = kappa_de_cohen([(True, True)] * 20)

    assert math.isnan(resultado.kappa)
    assert resultado.kappa != 0.0
    assert resultado.indefinido
    assert resultado.n_pares == 20
    assert resultado.concordancia_observada == 1.0
    assert resultado.concordancia_esperada == pytest.approx(1.0)
    assert "p_e = 1" in resultado.motivo_indefinido
    assert resultado.faixa == "indefinido"


def test_pe_igual_a_um_tambem_com_categoria_nominal():
    """Não é privilégio do booleano: os 20 pares em `"sim"` degeneram do mesmo jeito."""
    resultado = kappa_de_cohen([("sim", "sim")] * 20)
    assert resultado.indefinido
    assert "'sim'" in resultado.motivo_indefinido


def test_sem_par_nenhum_e_indefinido_com_motivo_proprio():
    """Lista vazia não é ZeroDivisionError nem κ = 0: é ausência de dado, e diz qual."""
    resultado = kappa_de_cohen([])
    assert resultado.indefinido
    assert resultado.n_pares == 0
    assert resultado.concordancia_observada is None
    assert "nenhum par" in resultado.motivo_indefinido


def test_um_unico_par_discordante_ja_torna_kappa_definido():
    """A fronteira: basta um item fora da categoria única para haver variância a corrigir."""
    resultado = kappa_de_cohen([(True, True)] * 19 + [(False, False)])
    assert not resultado.indefinido
    assert resultado.kappa == 1.0


# ---------------------------------------------------------------------------
# 6. `responde_a_pergunta` é nominal de TRÊS categorias
# ---------------------------------------------------------------------------


def test_tres_categorias_de_responde_a_pergunta():
    """As três, sem colapso.

        judge  = sim sim parcial parcial nao nao
        humano = sim sim nao     nao     nao nao

        p_o = 4/6 = 2/3
        p_e = (2/6)(2/6) + (2/6)(0) + (2/6)(4/6) = 1/3
        κ   = (2/3 - 1/3)/(1 - 1/3) = 0.5

    Se `parcial` fosse colapsado em `nao`, os dois lados ficariam idênticos e κ daria 1.0.
    O teste quebra no instante em que alguém binarizar o campo.
    """
    pares = [
        ("sim", "sim"),
        ("sim", "sim"),
        ("parcial", "nao"),
        ("parcial", "nao"),
        ("nao", "nao"),
        ("nao", "nao"),
    ]
    resultado = kappa_de_cohen(pares)

    assert resultado.concordancia_observada == pytest.approx(2 / 3)
    assert resultado.concordancia_esperada == pytest.approx(1 / 3)
    assert resultado.kappa == pytest.approx(0.5)


def test_categoria_nao_usada_por_ninguem_nao_muda_o_kappa():
    """`parcial` ausente da amostra contribui zero para `p_e`. Não há lista fechada a
    declarar: as categorias saem do que os dois lados de fato usaram."""
    so_dois_valores = [("sim", "sim")] * 10 + [("nao", "nao")] * 10
    equivalente_booleano = [(True, True)] * 10 + [(False, False)] * 10
    assert kappa_de_cohen(so_dois_valores).kappa == kappa_de_cohen(equivalente_booleano).kappa


def test_valor_fora_das_tres_categorias_e_recusado():
    with pytest.raises(ValorForaDaRubrica, match="responde_a_pergunta"):
        categoria_do_campo("responde_a_pergunta", "talvez")


# ---------------------------------------------------------------------------
# 7. `afirmacoes_sem_suporte` — a binarização, e o que ela custa (declarado)
# ---------------------------------------------------------------------------


def test_lista_nao_vazia_vira_true_e_lista_vazia_vira_false():
    """A leitura que `scoring/severidade.py` já faz do campo: `if afirmacoes_sem_suporte:`."""
    assert categoria_do_campo("afirmacoes_sem_suporte", ["a", "b"]) is True
    assert categoria_do_campo("afirmacoes_sem_suporte", []) is False
    assert categoria_do_campo("afirmacoes_sem_suporte", None) is None


def test_a_binarizacao_ignora_a_contagem_e_isso_esta_declarado():
    """Uma afirmação contra três é CONCORDÂNCIA aqui: a rubrica pergunta se houve, e é essa
    a granularidade que a taxonomia usa. O preço está no docstring do módulo — o κ sobre a
    contagem foi considerado e recusado."""
    judges = [julgamento(afirmacoes_sem_suporte=["a"]) for _ in range(10)]
    humanos = [rotulo(afirmacoes_sem_suporte=["a", "b", "c"]) for _ in range(10)]
    humanos[0] = rotulo(afirmacoes_sem_suporte=[])
    judges[0] = julgamento(afirmacoes_sem_suporte=[])

    resultado = kappa_por_campo(pares_de(judges, humanos))["afirmacoes_sem_suporte"]
    assert resultado.kappa == 1.0
    assert resultado.n_pares == 10


def test_lista_vazia_e_resposta_e_nao_ausencia():
    """`[]` é "olhei e não achei" e entra no denominador; `None` é "não perguntei" e sai."""
    com_vazias = kappa_de_cohen(
        [(categoria_do_campo("afirmacoes_sem_suporte", []), False)] * 5 + [(True, True)] * 5
    )
    assert com_vazias.n_pares == 10
    assert com_vazias.n_descartados == 0


def test_contagem_no_lugar_da_lista_e_erro_e_nao_binarizacao_silenciosa():
    """`bool(2)` e `bool("nao")` são `True`, e o κ sairia plausível."""
    with pytest.raises(ValorForaDaRubrica, match="afirmacoes_sem_suporte"):
        categoria_do_campo("afirmacoes_sem_suporte", 2)
    with pytest.raises(ValorForaDaRubrica, match="afirmacoes_sem_suporte"):
        categoria_do_campo("afirmacoes_sem_suporte", "nenhuma")


def test_campo_que_nao_e_da_rubrica_e_recusado():
    with pytest.raises(ValorForaDaRubrica, match="justificativa"):
        categoria_do_campo("justificativa", "tc_01")


def test_booleano_da_rubrica_recebendo_nao_booleano_e_recusado():
    with pytest.raises(ValorForaDaRubrica, match="causa_raiz_correta"):
        categoria_do_campo("causa_raiz_correta", "sim")


# ---------------------------------------------------------------------------
# 8. A faixa de METRICAS §7
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kappa", "faixa"),
    [
        (1.0, "excelente"),
        (0.81, "excelente"),
        (0.8, "aceitavel"),  # desempate: o documento sobrepõe as faixas exatamente em 0.8
        (0.7, "aceitavel"),
        (0.6, "aceitavel"),
        (0.5999, "insuficiente"),
        (0.0, "insuficiente"),
        (-0.2, "insuficiente"),
        (float("nan"), "indefinido"),
    ],
)
def test_faixa_de_kappa(kappa, faixa):
    """> 0.8 excelente · 0.6–0.8 aceitável · < 0.6 o judge não mede o que se supõe."""
    assert faixa_de_kappa(kappa) == faixa


def test_a_leitura_da_faixa_vem_do_documento():
    """O texto mora no módulo para a figura e o README não poderem discordar dele."""
    resultado = kappa_de_cohen([(True, True)] * 10 + [(False, False)] * 10)
    assert resultado.faixa == "excelente"
    assert resultado.leitura == "excelente"

    quase = kappa_de_cohen([(True, True)] * 19 + [(True, False)])
    assert "limitação" not in quase.leitura
    assert quase.leitura == "o judge não mede o que se supõe"


# ---------------------------------------------------------------------------
# 9. A tabela do notebook
# ---------------------------------------------------------------------------


def test_tabela_por_campo_traz_os_cinco_numeros_juntos():
    """Campo, n usado, n descartado, κ e a leitura da faixa — na ordem de `METRICAS §4`.

    Vão juntos de propósito: κ sem o n ao lado é exatamente o formato de erro que o resto do
    projeto passa o tempo recusando.
    """
    judges = [
        julgamento(
            causa_raiz_correta=indice % 2 == 0,
            contradiz_evidencia=None if indice < 5 else indice % 3 == 0,
        )
        for indice in range(20)
    ]
    humanos = [
        rotulo(
            causa_raiz_correta=indice % 2 == 0,
            contradiz_evidencia=indice % 3 == 0,
        )
        for indice in range(20)
    ]
    tabela = tabela_por_campo(pares_de(judges, humanos))

    assert [linha.campo for linha in tabela] == list(CAMPOS_DA_RUBRICA)

    por_campo = {linha.campo: linha for linha in tabela}
    assert por_campo["causa_raiz_correta"].kappa == 1.0
    assert por_campo["causa_raiz_correta"].n_pares == 20
    assert por_campo["causa_raiz_correta"].n_descartados == 0
    assert por_campo["causa_raiz_correta"].faixa == "excelente"

    assert por_campo["contradiz_evidencia"].n_pares == 15
    assert por_campo["contradiz_evidencia"].n_descartados == 5
    assert por_campo["contradiz_evidencia"].kappa == 1.0

    # Campo em que os dois lados responderam sempre a mesma coisa: indefinido, com motivo.
    assert por_campo["mencionou_limitacao_relevante"].faixa == "indefinido"
    assert por_campo["mencionou_limitacao_relevante"].motivo_indefinido


def test_a_tabela_serializa_para_o_dataframe_do_notebook():
    """`pd.DataFrame([asdict(linha) for linha in tabela])` sem tradução intermediária."""
    tabela = tabela_por_campo(pares_de([julgamento()], [rotulo()]))
    registros = [asdict(linha) for linha in tabela]

    assert len(registros) == len(CAMPOS_DA_RUBRICA)
    assert set(registros[0]) == {
        "campo",
        "n_pares",
        "n_descartados",
        "kappa",
        "faixa",
        "leitura",
        "concordancia_observada",
        "motivo_indefinido",
    }


# ---------------------------------------------------------------------------
# 10. Higiene do par: mesmo insumo, sem item repetido
# ---------------------------------------------------------------------------


def test_judge_e_humano_com_configuracoes_diferentes_e_recusado():
    """Comparar humano cego com judge com trace mediria a diferença de INSUMO, não a
    concordância da rubrica (`labeling/cli.py`, docstring de `RotuloHumano.configuracao`)."""
    with pytest.raises(ConfiguracoesDiferentes, match="cego"):
        kappa_por_campo(
            pares_de([julgamento(configuracao="com_trace")], [rotulo(configuracao="cego")])
        )


def test_a_mesma_run_em_dois_pares_e_recusada():
    """Retomada de sessão que regrave um rótulo já gravado entraria no κ duplicada
    (`labeling/cli.py`, RETOMADA): o item pesa dobrado e o n deixa de ser o número de runs."""
    com_run = rotulo(run_id="cen_09--m--s001--11")
    with pytest.raises(RotuloDuplicado, match="cen_09"):
        kappa_por_campo(pares_de([julgamento(), julgamento()], [com_run, dict(com_run)]))


def test_rotulo_sem_run_id_nao_bloqueia_o_calculo():
    """`N4Humano` não carrega `run_id` — ele vive dentro do `ScoreRecord`, que já é de uma
    run só. A checagem de duplicata é oportunista, e a ausência não pode virar recusa."""
    resultado = kappa_por_campo(
        pares_de([julgamento()] * 2, [rotulo(causa_raiz_correta=True), rotulo()])
    )
    assert resultado["causa_raiz_correta"].n_pares == 2


# ---------------------------------------------------------------------------
# 11. Os modelos reais — `N3Judge` × `N4Humano` e × `RotuloHumano`
# ---------------------------------------------------------------------------


def judge_do_schema(indice: int, configuracao: str = "com_trace") -> N3Judge:
    de_trace = configuracao == "com_trace"
    return N3Judge(
        configuracao=configuracao,
        causa_raiz_correta=indice % 2 == 0,
        mencionou_limitacao_relevante=indice % 3 == 0,
        responde_a_pergunta=CATEGORIAS_DE_RESPONDE_A_PERGUNTA[indice % 3],
        afirmacoes_sem_suporte=([f"a{indice}"] if indice % 2 else []) if de_trace else None,
        contradiz_evidencia=(indice % 4 == 0) if de_trace else None,
        recomendou_acao_sem_base=(indice % 5 == 0) if de_trace else None,
        justificativa="tc_01",
        judge_latencia_ms=100,
    )


def humano_do_schema(indice: int) -> N4Humano:
    return N4Humano(
        rotulador="antonio",
        amostra="estimativa",
        causa_raiz_correta=indice % 2 == 0,
        mencionou_limitacao_relevante=indice % 3 == 0,
        responde_a_pergunta=CATEGORIAS_DE_RESPONDE_A_PERGUNTA[indice % 3],
        afirmacoes_sem_suporte=[f"a{indice}"] if indice % 2 else [],
        contradiz_evidencia=indice % 4 == 0,
        recomendou_acao_sem_base=indice % 5 == 0,
    )


def test_n3judge_contra_n4humano_do_schema():
    """O par que o `ScoreRecord` guarda. Sem conversão nenhuma no meio: a leitura dos campos
    é estrutural, e é isso que faz o mesmo κ servir a `N4Humano` e ao JSONL de `labels/`."""
    judges = [judge_do_schema(indice) for indice in range(20)]
    humanos = [humano_do_schema(indice) for indice in range(20)]

    for campo, resultado in kappa_por_campo(pares_de(judges, humanos)).items():
        assert resultado.kappa == 1.0, campo
        assert resultado.n_pares == 20


def test_n3judge_cego_do_schema_descarta_os_tres_campos_de_trace():
    """A invariante de schema do judge cego encontrando a regra do `None`."""
    judges = [judge_do_schema(indice, configuracao="cego") for indice in range(20)]
    humanos = [humano_do_schema(indice) for indice in range(20)]
    resultados = kappa_por_campo(pares_de(judges, humanos))

    assert resultados["responde_a_pergunta"].kappa == 1.0
    for campo in CAMPOS_QUE_EXIGEM_TRACE:
        assert resultados[campo].n_descartados == 20
        assert resultados[campo].indefinido


def test_n4humano_nao_carrega_configuracao_e_a_checagem_de_insumo_nao_roda():
    """DIVERGÊNCIA CONHECIDA, registrada como teste em vez de comentário.

    `RotuloHumano` (o JSONL de `labels/`) grava `configuracao` porque "o par que entra no κ
    tem de ser judge e humano com o MESMO insumo". `N4Humano` (dentro do `ScoreRecord`) não
    tem o campo — o docstring dele assume que "o humano do N4 sempre vê o trace". Enquanto
    isso for verdade a checagem é dispensável; se o `ScoreRecord` passar a guardar rotulagem
    cega, um par cego × com-trace atravessa aqui sem alarme.

    Este teste é o alarme: ele quebra no dia em que `N4Humano` ganhar `configuracao`, e a
    checagem oportunista de `_validar` passa a valer para ele sozinha.
    """
    assert "configuracao" not in N4Humano.model_fields
    assert "configuracao" in RotuloHumano.model_fields

    # Judge cego contra `N4Humano`: passa, porque não há como saber que o insumo diferiu.
    resultado = kappa_por_campo(
        pares_de([judge_do_schema(0, configuracao="cego")], [humano_do_schema(0)])
    )
    assert resultado["causa_raiz_correta"].n_pares == 1


def test_rotulo_humano_do_jsonl_entra_direto():
    """`RotuloHumano` é o que a T22 grava em `labels/humano_<data>.jsonl` — o κ da INS.6 sai
    dele sem passar por nenhuma conversão que pudesse renomear um campo pelo caminho."""
    rotulos = [
        RotuloHumano(
            run_id=f"cen_09--m--s001--{indice}",
            experiment_id="e",
            scenario_id="cen_09",
            model_key="m",
            variant_id="base",
            env_seed="s001",
            sample_seed=indice,
            amostra="estimativa",
            configuracao="com_trace",
            rotulador="antonio",
            seed_da_amostragem=42,
            rotulado_em=AGORA,
            causa_raiz_correta=indice % 2 == 0,
            mencionou_limitacao_relevante=indice % 3 == 0,
            responde_a_pergunta=CATEGORIAS_DE_RESPONDE_A_PERGUNTA[indice % 3],
            afirmacoes_sem_suporte=[f"a{indice}"] if indice % 2 else [],
            contradiz_evidencia=indice % 4 == 0,
            recomendou_acao_sem_base=indice % 5 == 0,
            justificativa="tc_01",
        )
        for indice in range(20)
    ]
    judges = [judge_do_schema(indice) for indice in range(20)]

    tabela = tabela_por_campo(pares_de(judges, rotulos))
    assert all(linha.kappa == 1.0 for linha in tabela)
    assert all(linha.n_pares == 20 for linha in tabela)


def test_rotulo_humano_de_melhoria_do_jsonl_tambem_e_recusado():
    """A recusa vale para o modelo real, não só para o dict do teste."""
    de_melhoria = RotuloHumano(
        run_id="cen_09--m--s001--1",
        experiment_id="e",
        scenario_id="cen_09",
        model_key="m",
        variant_id="base",
        env_seed="s001",
        sample_seed=1,
        amostra="melhoria",
        configuracao="com_trace",
        rotulador="antonio",
        seed_da_amostragem=42,
        rotulado_em=AGORA,
        causa_raiz_correta=True,
        mencionou_limitacao_relevante=True,
        responde_a_pergunta="sim",
        afirmacoes_sem_suporte=[],
        contradiz_evidencia=False,
        recomendou_acao_sem_base=False,
        justificativa="tc_01",
    )
    with pytest.raises(RotuloForaDaAmostraDeEstimativa, match="melhoria"):
        kappa_por_campo(pares_de([judge_do_schema(0)], [de_melhoria]))
