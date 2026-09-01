"""T29 — `pass^k` por modelo, e as três escolhas de método que mudam o número.

O QUE ESTE ARQUIVO PRENDE
    A conta é `passk.pass_hat_k_medio` e ela já tem teste próprio. O que os testes abaixo
    prendem é a **agregação de propósito** — o que entra no vetor de tentativas — porque é ali
    que a bateria de 30/08 mostrou que a resposta muda:

    1. **A lente oficial é a reta zero.** `sucesso_binario` de `METRICAS §6.5` reprova as 288
       execuções da bateria principal. `pass^k` sobre ela não ordena nada, e um teste que só
       exercitasse a lente `sem_s2` deixaria isso invisível.
    2. **As 37 execuções sem decisão passam na lente `sem_s2`.** Elas recebem só códigos de
       processo, nenhum S0/S1 — então a mitigação do X33 credita como sucesso a execução em
       que o agente não decidiu nada. 30 das 37 são do 14B (o X31), e é isso que produz a
       vantagem dele em k=1.
    3. **O cruzamento sobrevive às três leituras; a ordem em k=1 não.** É a separação entre o
       achado e o artefato de método, e é o que o README tem direito de citar.

    `test_a_bateria_no_disco_nao_tem_eixo_de_ambiente` não é teste de regressão: é **tripwire**
    da H4. Enquanto ele passar, a área entre as curvas de ambiente fixo e livre não é
    calculável e a docstring de `estabilidade.py` está correta. No dia em que alguém
    implementar o eixo de `env_seed` em `runner/matriz.py` e rodar a bateria, ele falha — e a
    falha é a instrução para reabrir H4.
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tapieval.schema.trace import ScoreRecord, ScorerVersion
from tapieval.scoring.estabilidade import (
    ErroDeEstabilidade,
    cruzamento,
    curva,
    decomposicao_de_variancia,
    sucesso_da_run,
    tabela,
    vetores_por_cenario,
)
from tests.test_severidade import n1_limpo, n2_limpo

RAIZ = Path(__file__).resolve().parents[1]

DIRETORIO_DA_PRINCIPAL = RAIZ / "runs" / "principal_2026_08"
"""A bateria que sustenta a T29: 18 cenários de test × 2 modelos × 8 `sample_seed`."""

EXECUCOES_DA_PRINCIPAL = 288
INDECISAS_DA_PRINCIPAL = 37
"""`decisao_prevista is None`. Declaradas como número para que a bateria encolher reprove:
uma análise de estabilidade sobre meia bateria passaria calada."""

SCORER = ScorerVersion(
    scorer_version="n1n2+taxonomia",
    sha256="0" * 64,
    congelado_em=datetime(2026, 8, 24, tzinfo=UTC),
)


MOTIVO_INDECISA = (
    "decisao_prevista is None — o trace não tem `DecisionEvent` nem ato observável, "
    "então não há decisão a comparar com o gabarito"
)


def score(
    cenario: str,
    seed: int,
    *,
    modelo: str = "qwen3-8b",
    pontuavel: bool = True,
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
        n2=n2_limpo(),
        score_final=0.0,
        sucesso_binario=False,
        pontuavel=pontuavel,
        motivo_nao_pontuavel=None if pontuavel else MOTIVO_INDECISA,
    )


@pytest.fixture(scope="module")
def scores_da_principal() -> list[ScoreRecord]:
    caminho = DIRETORIO_DA_PRINCIPAL / "scores.jsonl"
    if not caminho.exists():
        pytest.skip(f"bateria principal ausente: {caminho}")
    linhas = caminho.read_text(encoding="utf-8").splitlines()
    return [ScoreRecord.model_validate_json(linha) for linha in linhas if linha.strip()]


# ---------------------------------------------------------------------------
# O tripwire de H4
# ---------------------------------------------------------------------------


def test_a_bateria_no_disco_nao_tem_eixo_de_ambiente():
    """Enquanto `env_seed` for constante por célula, H4 não é calculável — e é por isso que
    `estabilidade.py` se recusa a chamar a decomposição dele de "modelo × ambiente".

    Quando este teste falhar, a bateria de ambiente existe: reabrir `METRICAS §7.2`.
    """
    celulas: dict[tuple[str, str, str, str], set[str]] = defaultdict(set)
    encontrou = False

    for scores in sorted(RAIZ.glob("runs/*/scores.jsonl")):
        encontrou = True
        for linha in scores.read_text(encoding="utf-8").splitlines():
            if not linha.strip():
                continue
            registro = json.loads(linha)
            achado = re.search(r"--envs(\w+)--", registro["run_id"])
            if not achado:
                continue
            chave = (
                scores.parent.name,
                registro["scenario_id"],
                registro["model_key"],
                registro["variant_id"],
            )
            celulas[chave].add(achado.group(1))

    if not encontrou:
        pytest.skip("nenhuma bateria pontuada no disco")

    com_varios = {c: e for c, e in celulas.items() if len(e) > 1}
    assert not com_varios, (
        "alguma célula tem mais de uma `env_seed` — o eixo de ambiente existe e a H4 "
        f"voltou a ser calculável: {sorted(com_varios)[:3]}"
    )


# ---------------------------------------------------------------------------
# A lente
# ---------------------------------------------------------------------------


def test_a_lente_nominal_reprova_toda_a_bateria_principal(scores_da_principal):
    """O X33 medido onde ele dói: `pass^k` sobre o corte oficial é a reta zero.

    Não é defeito do estimador nem da agregação — é o corte de §6.5 saturado no piso, e
    reportar só a lente `sem_s2` esconderia que a métrica oficial não separa os modelos.
    """
    assert len(scores_da_principal) == EXECUCOES_DA_PRINCIPAL

    for modelo in ("qwen3-8b", "qwen3-14b"):
        vetores = vetores_por_cenario(
            scores_da_principal, model_key=modelo, lente="nominal", trato="incluir"
        )
        assert vetores.media_simples == 0.0, modelo
        assert all(v == 0.0 for v in curva(vetores).passk.values()), modelo


def test_a_lente_sem_s2_credita_como_sucesso_a_run_que_nao_decidiu(scores_da_principal):
    """As 37 indecisas passam em `sem_s2` — elas só têm código de processo, nenhum S0/S1.

    É o defeito da mitigação do X33, e ele não é simétrico: 30 das 37 são do 14B.
    """
    indecisas = [s for s in scores_da_principal if not s.pontuavel]
    assert len(indecisas) == INDECISAS_DA_PRINCIPAL
    assert all(sucesso_da_run(s, "sem_s2") for s in indecisas)
    assert not any(sucesso_da_run(s, "nominal") for s in indecisas)

    do_14b = sum(1 for s in indecisas if s.model_key == "qwen3-14b")
    assert do_14b == 30, "a assimetria do X31 mudou — a leitura da T29 precisa ser refeita"


# ---------------------------------------------------------------------------
# O trato das indecisas
# ---------------------------------------------------------------------------


def test_excluir_tira_a_run_do_vetor_e_conta_o_motivo():
    scores = [
        score("cen_a", 1),
        score("cen_a", 2, pontuavel=False),
        score("cen_a", 3),
    ]
    vetores = vetores_por_cenario(scores, model_key="qwen3-8b", lente="sem_s2")

    assert vetores.por_cenario == {"cen_a": (True, True)}
    assert vetores.n_descartados == 1
    assert len(vetores.descartes) == 1
    assert "decisao_prevista is None" in vetores.descartes[0][0]


def test_falha_mantem_a_celula_cheia_e_conta_como_nao_sucesso():
    """O trato que preserva o eixo x inteiro sem creditar o X31 como acerto."""
    scores = [
        score("cen_a", 1),
        score("cen_a", 2, pontuavel=False),
    ]
    vetores = vetores_por_cenario(
        scores, model_key="qwen3-8b", lente="sem_s2", trato="falha"
    )

    assert vetores.por_cenario == {"cen_a": (True, False)}
    assert vetores.n_descartados == 0


def test_incluir_credita_a_indecisa_pela_lente_e_em_sem_s2_isso_e_sucesso():
    scores = [score("cen_a", 1, pontuavel=False)]
    vetores = vetores_por_cenario(
        scores, model_key="qwen3-8b", lente="sem_s2", trato="incluir"
    )

    assert vetores.por_cenario == {"cen_a": (True,)}


def test_o_trato_muda_a_ordem_em_k1_e_nao_muda_o_cruzamento(scores_da_principal):
    """A separação entre o artefato de método e o achado.

    Com `incluir`, o 14B lidera em k=1 — vantagem construída pelas 30 indecisas dele. Com
    `falha`, ele já começa atrás. **Em qualquer um dos três, o 8B está à frente a partir de
    k=3**, e é essa a frase que o README pode citar.
    """
    ordens_em_k1 = set()

    for trato in ("incluir", "falha", "excluir"):
        curvas = {
            modelo: curva(
                vetores_por_cenario(
                    scores_da_principal, model_key=modelo, lente="sem_s2", trato=trato
                )
            )
            for modelo in ("qwen3-8b", "qwen3-14b")
        }
        ordens_em_k1.add(curvas["qwen3-8b"].passk[1] > curvas["qwen3-14b"].passk[1])

        k = cruzamento(curvas["qwen3-8b"], curvas["qwen3-14b"])
        assert k is not None and k <= 3, f"{trato}: cruzamento em {k}"

    assert ordens_em_k1 == {True, False}, (
        "a ordem em k=1 deixou de depender do trato — a ressalva de método mudou"
    )


# ---------------------------------------------------------------------------
# A curva
# ---------------------------------------------------------------------------


def test_a_media_simples_pondera_por_execucao_e_nao_por_cenario():
    """Duas médias diferentes, e a do contraste é a ingênua — a que o relatório escreveria."""
    scores = [
        score("cen_a", 1),
        score("cen_a", 2),
        score("cen_a", 3),
        score("cen_b", 1, acao_indevida=True),
    ]
    vetores = vetores_por_cenario(scores, model_key="qwen3-8b", lente="sem_s2")

    por_execucao = sum(sum(v) for v in vetores.por_cenario.values()) / vetores.n_trials
    assert vetores.media_simples == pytest.approx(por_execucao)


def test_celula_desigual_trunca_a_curva_e_a_curva_diz_onde():
    """`NaN` a partir do menor cenário — e `k_maximo_estimavel` avisa antes do gráfico.

    Preservar o `NaN` é o ponto: uma curva completa sobre denominador que mudou no meio
    compararia modelos em bases diferentes sem que nada aparecesse.
    """
    scores = [score("cen_a", s) for s in (1, 2, 3)] + [score("cen_b", 1)]
    vetores = vetores_por_cenario(scores, model_key="qwen3-8b", lente="sem_s2")
    c = curva(vetores, k_max=3)

    assert vetores.k_maximo_estimavel == 1
    assert c.truncada
    assert not math.isnan(c.passk[1])
    assert math.isnan(c.passk[2]) and math.isnan(c.passk[3])
    assert math.isnan(c.queda_da_media_ao_k_maximo)


def test_pass_8_e_zero_para_os_dois_modelos_em_toda_a_grade(scores_da_principal):
    """Nenhum cenário é entregue nas 8 seeds por nenhum dos dois modelos.

    Vale nas duas lentes e nos tratos que chegam a k=8. É o resultado mais desconfortável da
    T29, e o que impede de ler o cruzamento como "então use o 8B".
    """
    for c in tabela(scores_da_principal, ["qwen3-8b", "qwen3-14b"], tratos=("incluir", "falha")):
        assert c.passk[8] == 0.0, f"{c.model_key}/{c.lente}/{c.trato}"


def test_a_grade_tem_uma_curva_por_modelo_lente_e_trato(scores_da_principal):
    curvas = tabela(scores_da_principal, ["qwen3-8b", "qwen3-14b"])
    assert len(curvas) == 2 * 2 * 3
    assert len({(c.model_key, c.lente, c.trato) for c in curvas}) == 12


# ---------------------------------------------------------------------------
# A decomposição
# ---------------------------------------------------------------------------


def test_modelo_que_so_varia_entre_cenarios_tem_variancia_toda_entre():
    """Passa sempre em `cen_a`, falha sempre em `cen_b` — zero inconsistência interna."""
    scores = [score("cen_a", s) for s in (1, 2, 3, 4)] + [
        score("cen_b", s, acao_indevida=True)
        for s in (1, 2, 3, 4)
    ]
    d = decomposicao_de_variancia(
        vetores_por_cenario(scores, model_key="qwen3-8b", lente="sem_s2")
    )

    assert d.dentro == pytest.approx(0.0)
    assert d.entre > 0.0
    assert d.fracao_entre == pytest.approx(1.0)


def test_modelo_que_varia_dentro_do_cenario_tem_variancia_toda_dentro():
    """Metade em cada cenário: a dificuldade não explica nada, a inconsistência explica tudo."""
    scores = []
    for cenario in ("cen_a", "cen_b"):
        scores += [score(cenario, 1), score(cenario, 2)]
        scores += [
            score(cenario, s, acao_indevida=True)
            for s in (3, 4)
        ]
    d = decomposicao_de_variancia(
        vetores_por_cenario(scores, model_key="qwen3-8b", lente="sem_s2")
    )

    assert d.entre == pytest.approx(0.0)
    assert d.dentro == pytest.approx(0.25)
    assert d.fracao_entre == pytest.approx(0.0)


def test_o_14b_tem_menos_variancia_entre_cenarios_que_o_8b(scores_da_principal):
    """O mecanismo por trás do cruzamento, e ele sobrevive aos três tratos.

    O 8B tem cenários que domina e cenários que não; o 14B varia de tentativa para tentativa
    no mesmo cenário. `pass^k` cobra a segunda, a média simples não vê nenhuma das duas.
    """
    for trato in ("incluir", "falha", "excluir"):
        d = {
            modelo: decomposicao_de_variancia(
                vetores_por_cenario(
                    scores_da_principal, model_key=modelo, lente="sem_s2", trato=trato
                )
            )
            for modelo in ("qwen3-8b", "qwen3-14b")
        }
        assert d["qwen3-14b"].fracao_entre < d["qwen3-8b"].fracao_entre, trato


# ---------------------------------------------------------------------------
# As recusas
# ---------------------------------------------------------------------------


def test_modelo_ausente_levanta_em_vez_de_devolver_vetor_vazio():
    with pytest.raises(ErroDeEstabilidade, match="nenhuma execução"):
        vetores_por_cenario([score("cen_a", 1)], model_key="qwen3-70b", lente="sem_s2")


def test_trato_desconhecido_levanta():
    with pytest.raises(ErroDeEstabilidade, match="trato desconhecido"):
        vetores_por_cenario(
            [score("cen_a", 1)], model_key="qwen3-8b", lente="sem_s2", trato="chutar"
        )


def test_cruzamento_de_um_modelo_consigo_mesmo_levanta():
    vetores = vetores_por_cenario([score("cen_a", 1)], model_key="qwen3-8b", lente="sem_s2")
    c = curva(vetores, k_max=1)
    with pytest.raises(ErroDeEstabilidade, match="consigo mesmo"):
        cruzamento(c, c)


def test_cruzamento_pula_o_k_sem_dado_em_vez_de_inventar_inversao():
    """`NaN` não é empate nem inversão — é ausência, e ausência não cruza curva."""
    a = curva(
        vetores_por_cenario(
            [score("cen_a", 1), score("cen_a", 2)], model_key="qwen3-8b", lente="sem_s2"
        ),
        k_max=3,
    )
    b = curva(
        vetores_por_cenario(
            [score("cen_a", 1, modelo="qwen3-14b")], model_key="qwen3-14b", lente="sem_s2"
        ),
        k_max=3,
    )

    assert math.isnan(b.passk[2])
    assert cruzamento(a, b) is None
