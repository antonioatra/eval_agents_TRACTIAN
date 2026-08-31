"""H2 — a comparação pareada entre modelos, e as leituras que o pareamento impede.

O QUE ESTE ARQUIVO PRENDE
    A aritmética é uma subtração e um bootstrap. O que é difícil, e o que os testes abaixo
    prendem, são três decisões de método que mudam a resposta sem mudar o dado:

    1. **pareado, não duas médias** — a variância entre cenários é maior que a diferença entre
       modelos, e comparar médias soltas põe essa variância dentro do erro;
    2. **`args_acc = 0` com `args_avaliados = 0` é indefinido, não zero**, e
       `decisao_correta = False` com `decisao_prevista = None` é *"não houve decisão"*, não
       *"decidiu errado"* — a segunda não é simétrica entre os modelos e por isso ela desloca
       o Δ na direção de quem falhou de FORMATO, importando o X31 para dentro de H2;
    3. **um veredito de "cruza zero" pode ser sorteio da semente** — `args_acc` está a 0,0014
       do corte, e `veredito_estavel` é a propriedade que impede a frase de ser escolhida pela
       semente que sair.

    O teste `test_o_veredito_no_limiar_nao_e_estavel` é a (3) virada propriedade verificada, e
    não frase num documento que ninguém confere.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tapieval.schema.trace import ScoreRecord, ScorerVersion
from tapieval.scoring.comparacao import (
    DiferencaPareada,
    ErroDeComparacao,
    comparar_h2,
    diferenca_pareada,
    montar_pares,
)
from tests.test_severidade import n1_limpo, n2_limpo

SCORER = ScorerVersion(
    scorer_version="n1n2+taxonomia",
    sha256="0" * 64,
    congelado_em=datetime(2026, 8, 24, tzinfo=UTC),
)


def score(cenario: str, seed: int, modelo: str, **n1) -> ScoreRecord:
    """Um `ScoreRecord` mínimo em volta de uma N1 — só o que a comparação lê."""
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
        n2=n2_limpo(),
        score_final=0.0,
        sucesso_binario=False,
    )


def fatorial(valores: dict[tuple[str, int], tuple[float, float]], campo: str = "tool_f1"):
    """`{(cenario, seed): (valor_8b, valor_14b)}` virado bateria."""
    saida = []
    for (cenario, seed), (a, b) in valores.items():
        saida.append(score(cenario, seed, "qwen3-8b", **{campo: a}))
        saida.append(score(cenario, seed, "qwen3-14b", **{campo: b}))
    return saida


# ---------------------------------------------------------------------------
# O pareamento
# ---------------------------------------------------------------------------


def test_o_par_casa_por_cenario_e_seed():
    scores = fatorial({("cen_a", 1): (0.2, 0.5), ("cen_b", 2): (0.9, 0.4)})
    pares = montar_pares(scores, "tool_f1", modelo_a="qwen3-8b", modelo_b="qwen3-14b")

    assert [(p.scenario_id, p.seed) for p in pares.pares] == [("cen_a", 1), ("cen_b", 2)]
    assert [round(p.delta, 3) for p in pares.pares] == [0.3, -0.5]


def test_o_pareamento_cancela_a_dificuldade_do_cenario_que_a_media_solta_nao_cancela():
    """A razão de existir do pareamento, escrita como número.

    Dois cenários: um fácil (os dois modelos vão bem) e um difícil (os dois vão mal). O modelo
    B é melhor que o A por 0,10 em CADA cenário — o efeito é constante e limpo. Mas o fatorial
    é desbalanceado de propósito nas seeds observadas, do jeito que uma bateria real fica
    quando uma run morre: aí a média solta de cada modelo passa a misturar a dificuldade do
    cenário com o modelo, e o Δ das médias deixa de ser 0,10.
    """
    facil = {("cen_facil", s): (0.90, 1.00) for s in (1, 2, 3)}
    dificil = {("cen_dificil", 1): (0.10, 0.20)}
    scores = fatorial({**facil, **dificil})

    pares = montar_pares(scores, "tool_f1", modelo_a="qwen3-8b", modelo_b="qwen3-14b")
    delta_pareado = diferenca_pareada(pares, repeticoes=200).delta
    assert delta_pareado == pytest.approx(0.10)

    # A mesma conta sem parear, com um cenário difícil a mais no lado do 8B — que é o que
    # acontece quando o fatorial fura e ninguém repara.
    scores_furados = [s for s in scores if not (s.model_key == "qwen3-14b" and s.seed == 3)]
    por_modelo: dict[str, list[float]] = {}
    for s in scores_furados:
        por_modelo.setdefault(s.model_key, []).append(s.n1.tool_f1)
    delta_solto = sum(por_modelo["qwen3-14b"]) / len(por_modelo["qwen3-14b"]) - sum(
        por_modelo["qwen3-8b"]
    ) / len(por_modelo["qwen3-8b"])

    assert delta_solto != pytest.approx(0.10, abs=0.01)


def test_celula_com_so_um_dos_modelos_levanta_em_vez_de_comparar():
    """Bateria incompleta é reportada como tal, não emparelhada por cima (regra do PLANO)."""
    scores = fatorial({("cen_a", 1): (0.2, 0.5)})
    scores = [s for s in scores if s.model_key != "qwen3-14b"]

    with pytest.raises(ErroDeComparacao, match="fatorial completo"):
        montar_pares(scores, "tool_f1", modelo_a="qwen3-8b", modelo_b="qwen3-14b")


def test_duas_execucoes_para_a_mesma_celula_e_modelo_levantam():
    scores = fatorial({("cen_a", 1): (0.2, 0.5)})
    scores.append(score("cen_a", 1, "qwen3-8b", tool_f1=0.9))

    with pytest.raises(ErroDeComparacao, match="deixaria de ser um par"):
        montar_pares(scores, "tool_f1", modelo_a="qwen3-8b", modelo_b="qwen3-14b")


# ---------------------------------------------------------------------------
# As duas exclusões
# ---------------------------------------------------------------------------


def test_args_acc_sem_chamada_avaliada_e_indefinida_e_nao_zero():
    """`args_acc = 0.0` com denominador vazio não é desempenho, é ausência de medida."""
    scores = [
        score("cen_a", 1, "qwen3-8b", args_acc=0.8, args_avaliados=5, args_corretos=4),
        score("cen_a", 1, "qwen3-14b", args_acc=0.0, args_avaliados=0, args_corretos=0),
        score("cen_b", 1, "qwen3-8b", args_acc=0.6, args_avaliados=5, args_corretos=3),
        score("cen_b", 1, "qwen3-14b", args_acc=0.6, args_avaliados=5, args_corretos=3),
    ]
    pares = montar_pares(scores, "args_acc", modelo_a="qwen3-8b", modelo_b="qwen3-14b")

    assert pares.n_descartados == 1
    assert [(p.scenario_id) for p in pares.pares] == ["cen_b"]
    assert diferenca_pareada(pares, repeticoes=200).delta == pytest.approx(0.0)

    # Sem a exclusão, o zero de preenchimento viraria −0,8 num dos pares e o Δ seria −0,4.
    ingenuo = [
        s.n1.args_acc for s in scores if s.model_key == "qwen3-14b"
    ], [s.n1.args_acc for s in scores if s.model_key == "qwen3-8b"]
    assert sum(ingenuo[0]) / 2 - sum(ingenuo[1]) / 2 == pytest.approx(-0.4)


def test_run_sem_decisao_sai_do_par_em_vez_de_contar_como_decisao_errada():
    """A exclusão que mais muda o número, e a razão é o X31.

    `decisao_correta = False` com `decisao_prevista = None` significa que o trace não tem
    decisão a comparar. Contá-la como erro importa uma falha de FORMATO para dentro de uma
    afirmação sobre CAPACIDADE — e como o parse_erro é 15× mais frequente no modelo maior, o
    erro sai todo na mesma direção.
    """
    scores = []
    for seed in range(1, 5):
        scores.append(score("cen_a", seed, "qwen3-8b", decisao_correta=True))
        scores.append(
            score(
                "cen_a",
                seed,
                "qwen3-14b",
                decisao_prevista=None if seed <= 2 else "orientar",
                decisao_correta=False if seed <= 2 else True,
            )
        )

    pares = montar_pares(scores, "decisao_correta", modelo_a="qwen3-8b", modelo_b="qwen3-14b")
    assert pares.n_descartados == 2
    assert diferenca_pareada(pares, repeticoes=200).delta == pytest.approx(0.0)

    motivos = dict(pares.descartes)
    assert any("sem decisão observável" in m for m in motivos)
    assert all("qwen3-14b" in m for m in motivos), "o descarte tem de dizer de que lado veio"


def test_o_par_inteiro_cai_e_nao_so_o_lado_indefinido():
    """Manter o lado definido compararia o modelo com nada e desequilibraria o fatorial."""
    scores = [
        score("cen_a", 1, "qwen3-8b", args_acc=1.0, args_avaliados=3, args_corretos=3),
        score("cen_a", 1, "qwen3-14b", args_acc=0.0, args_avaliados=0, args_corretos=0),
    ]
    pares = montar_pares(scores, "args_acc", modelo_a="qwen3-8b", modelo_b="qwen3-14b")

    assert pares.pares == ()
    with pytest.raises(ErroDeComparacao, match="não é 0, é indefinida"):
        diferenca_pareada(pares, repeticoes=200)


def test_tool_f1_nao_tem_valor_indefinido_e_nenhum_par_e_descartado():
    """As duas exclusões valem para dois campos, e só para eles — o resto entra inteiro."""
    scores = fatorial({("cen_a", 1): (0.0, 0.0), ("cen_b", 1): (0.0, 1.0)})
    pares = montar_pares(scores, "tool_f1", modelo_a="qwen3-8b", modelo_b="qwen3-14b")

    assert pares.n_descartados == 0
    assert len(pares.pares) == 2


# ---------------------------------------------------------------------------
# O IC, e o veredito sobre o veredito
# ---------------------------------------------------------------------------


def test_efeito_grande_e_consistente_nao_cruza_zero():
    scores = fatorial({("cen", s): (0.10, 0.70) for s in range(1, 21)})
    d = diferenca_pareada(
        montar_pares(scores, "tool_f1", modelo_a="qwen3-8b", modelo_b="qwen3-14b")
    )

    assert d.delta == pytest.approx(0.60)
    assert not d.cruza_zero
    assert d.veredito_estavel
    assert d.leitura == "não cruza zero"


def test_efeito_nulo_cruza_zero():
    valores = {("cen", s): (0.5, 0.5 + (0.2 if s % 2 else -0.2)) for s in range(1, 21)}
    d = diferenca_pareada(
        montar_pares(fatorial(valores), "tool_f1", modelo_a="qwen3-8b", modelo_b="qwen3-14b")
    )

    assert d.cruza_zero
    assert d.veredito_estavel
    assert d.leitura == "cruza zero"


def test_o_ic_e_reprodutivel_com_a_mesma_semente_e_muda_com_outra():
    """A semente é fixa porque o IC entra numa figura — e é por isso que ela precisa de aviso."""
    pares = montar_pares(
        fatorial({("cen", s): (0.4, 0.4 + (s % 5) / 10) for s in range(1, 31)}),
        "tool_f1",
        modelo_a="qwen3-8b",
        modelo_b="qwen3-14b",
    )
    a = diferenca_pareada(pares, repeticoes=2_000, seed=42)
    b = diferenca_pareada(pares, repeticoes=2_000, seed=42)
    c = diferenca_pareada(pares, repeticoes=2_000, seed=7)

    assert a.ic95 == b.ic95
    assert a.ic95 != c.ic95


def limiar(p_bootstrap: float, repeticoes: int = 10_000) -> DiferencaPareada:
    """Um resultado com `p` escolhido, para testar o veredito sem depender do sorteio.

    Construir o objeto direto, em vez de procurar um conjunto que caia no limiar, é
    deliberado: um teste que precisa da semente certa para o dado cair na fronteira prende a
    semente, e não a propriedade. Os `p` usados abaixo são os REAIS da bateria principal.
    """
    return DiferencaPareada(
        metrica="args_acc",
        modelo_a="qwen3-8b",
        modelo_b="qwen3-14b",
        media_a=0.5,
        media_b=0.44,
        delta=-0.061,
        ic95=(-0.123, 0.000),
        n_pares=124,
        n_descartados=21,
        descartes=(),
        repeticoes=repeticoes,
        seed=42,
        p_bootstrap=p_bootstrap,
    )


def test_o_veredito_no_limiar_nao_e_estavel():
    """A propriedade que o `args_acc` da bateria principal obrigou a existir.

    `p` = 0,0514 está a 0,0014 do corte de 0,05, e o erro de Monte Carlo de `p` ali é 0,0022 —
    ou seja, o booleano "IC95 cruza zero" é lido de dentro do ruído do próprio bootstrap. É
    exatamente o caso em que trocar a semente troca a frase, e `veredito_estavel` recusa a
    frase nos dois sentidos em vez de escolher a que sair.
    """
    d = limiar(0.0514)

    assert not d.veredito_estavel
    assert d.leitura == "no limiar — este n não decide"
    assert abs(d.p_bootstrap - 0.05) < 3 * d.erro_de_monte_carlo


def test_longe_do_limiar_o_veredito_vale_dos_dois_lados():
    """A margem não pode engolir todo resultado — senão nada seria nunca conclusivo.

    Os dois `p` são os do `tool_f1_liquido` (0,0242, não cruza) e do `decisao_correta`
    (0,0696, cruza) da mesma bateria: os dois a mais de três erros padrão do corte, os dois
    com veredito que se sustenta.
    """
    assert limiar(0.0242).veredito_estavel
    assert limiar(0.0696).veredito_estavel


def test_mais_reamostras_estreitam_a_faixa_de_indecisao():
    """O mesmo `p` que é indeciso com 10.000 reamostras decide com um milhão.

    É a consequência prática de a margem ser erro de Monte Carlo e não folga arbitrária: a
    indecisão do `args_acc` é comprável com mais bootstrap — e **só** com mais bootstrap. O
    que ela não compra é a outra indecisão, a do n: `p` continuaria em 0,0514, o Δ continuaria
    em −0,061, e o que teria melhorado é a precisão com que se sabe que o efeito está no
    limiar, não o efeito.
    """
    assert not limiar(0.0514, repeticoes=10_000).veredito_estavel
    assert limiar(0.0514, repeticoes=1_000_000).veredito_estavel


def test_o_erro_de_monte_carlo_encolhe_com_mais_reamostras():
    """A margem não é constante: mais bootstrap, veredito mais confiável perto do corte."""
    pares = montar_pares(
        fatorial({("cen", s): (0.4, 0.5) for s in range(1, 11)}),
        "tool_f1",
        modelo_a="qwen3-8b",
        modelo_b="qwen3-14b",
    )
    poucas = diferenca_pareada(pares, repeticoes=1_000)
    muitas = diferenca_pareada(pares, repeticoes=10_000)

    assert muitas.erro_de_monte_carlo < poucas.erro_de_monte_carlo


# ---------------------------------------------------------------------------
# A leitura de H2
# ---------------------------------------------------------------------------


def test_comparar_h2_devolve_as_quatro_metricas_na_ordem_da_hipotese():
    scores = fatorial({("cen", s): (0.4, 0.5) for s in range(1, 6)})
    saida = comparar_h2(
        scores, modelo_a="qwen3-8b", modelo_b="qwen3-14b", repeticoes=200
    )

    assert [d.metrica for d in saida] == [
        "tool_f1",
        "tool_f1_liquido",
        "args_acc",
        "decisao_correta",
    ]


def test_o_sinal_do_delta_e_do_modelo_maior():
    """`Δ` é `B − A`, e B é o maior por convenção — trocar isso inverteria a leitura de H2."""
    scores = fatorial({("cen", 1): (0.2, 0.9)})
    d = diferenca_pareada(
        montar_pares(scores, "tool_f1", modelo_a="qwen3-8b", modelo_b="qwen3-14b"),
        repeticoes=200,
    )

    assert d.modelo_b == "qwen3-14b"
    assert d.delta > 0
    assert d.media_b > d.media_a
