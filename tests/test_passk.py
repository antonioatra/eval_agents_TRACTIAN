"""T27 — estimador pass^k (METRICAS §7.2, INS.8).

O que estes testes protegem:

1. os quatro casos obrigatórios da task — 1.0, 0.0, um valor conhecido e `NaN`;
2. a **equivalência com a forma fechada** `comb(sucessos,k)/comb(trials,k)` do documento.
   A implementação usa produto de razões por custo; o teste amarra o resultado à
   fórmula publicada, senão a otimização poderia mudar a métrica em silêncio;
3. os degenerados (`n == 0`, `k == 0`, `k == n`, zero sucessos), que são exatamente
   os casos que o notebook nb05 encontra quando uma célula da grade está incompleta.

`pass^k` mede CONFIABILIDADE, não capacidade: uma única falha em 8 trials zera
`pass^8`. Vários testes existem só para garantir que essa dureza não se perca.
"""

from __future__ import annotations

import math
from math import comb, isnan

import pytest

from tapieval.scoring.passk import (
    curva_pass_hat_k,
    pass_hat_k,
    pass_hat_k_medio,
    pass_hat_k_por_cenario,
)

# ---------------------------------------------------------------------------
# 1. Os quatro casos obrigatórios
# ---------------------------------------------------------------------------


def test_todos_os_trials_passam_da_um():
    """8 de 8 é o único jeito de tirar 1.0 em pass^8."""
    assert pass_hat_k(sucessos=8, trials=8, k=8) == 1.0


def test_uma_unica_falha_zera_o_pass_k_completo():
    """7 de 8 com k=8: não existem 8 sucessos para sortear, logo 0.0 — não 0.875."""
    assert pass_hat_k(sucessos=7, trials=8, k=8) == 0.0


def test_valor_conhecido_bate_com_a_forma_fechada():
    """4 de 8 com k=2 = comb(4,2)/comb(8,2) = 3/14 (METRICAS §7.2)."""
    assert pass_hat_k(sucessos=4, trials=8, k=2) == pytest.approx(comb(4, 2) / comb(8, 2))


def test_k_maior_que_trials_e_nan():
    """Sem k trials não há o que estimar. `NaN` é o valor especificado, não exceção:
    o notebook precisa plotar a curva com a célula faltando visível, não com a run morta."""
    assert isnan(pass_hat_k(sucessos=8, trials=8, k=9))


# ---------------------------------------------------------------------------
# 2. Equivalência com a forma fechada do documento
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("trials", range(1, 13))
def test_equivale_a_comb_sobre_comb_em_toda_a_grade_pequena(trials):
    """Varre todo (sucessos, k) válido e compara com a fórmula publicada."""
    for sucessos in range(trials + 1):
        for k in range(1, trials + 1):
            esperado = comb(sucessos, k) / comb(trials, k)
            assert pass_hat_k(sucessos, trials, k) == pytest.approx(esperado, abs=1e-12)


def test_pass_1_e_a_taxa_media_de_sucesso():
    """pass^1 tem que reproduzir a média simples — é a linha de base contra a qual
    o decaimento da curva é lido (METRICAS §10, resultado 5)."""
    assert pass_hat_k(sucessos=5, trials=8, k=1) == pytest.approx(5 / 8)


def test_curva_decai_monotonicamente_em_k():
    """Exigir mais acertos seguidos nunca pode aumentar a probabilidade."""
    valores = [pass_hat_k(sucessos=6, trials=8, k=k) for k in range(1, 9)]

    assert valores == sorted(valores, reverse=True)
    assert valores[0] > valores[-1] == 0.0


# ---------------------------------------------------------------------------
# 3. Estabilidade numérica
# ---------------------------------------------------------------------------


def test_nao_estoura_com_trials_grande():
    """A forma fechada exigiria inteiros de centenas de milhares de dígitos.
    O produto de razões devolve um float finito e plausível."""
    valor = pass_hat_k(sucessos=500_000, trials=1_000_000, k=8)

    assert math.isfinite(valor)
    assert valor == pytest.approx(0.5**8, rel=1e-4)


def test_resultado_fica_sempre_em_zero_um():
    """Produto de razões acumula erro; nenhum caso pode vazar do intervalo."""
    for trials in (3, 17, 101):
        for sucessos in (0, 1, trials // 2, trials):
            for k in range(1, trials + 1):
                valor = pass_hat_k(sucessos, trials, k)
                assert 0.0 <= valor <= 1.0


# ---------------------------------------------------------------------------
# 4. Degenerados
# ---------------------------------------------------------------------------


def test_zero_sucessos_da_zero():
    assert pass_hat_k(sucessos=0, trials=8, k=1) == 0.0


def test_k_zero_da_um_por_vacuidade():
    """Nenhuma exigência é sempre satisfeita. Mantém a curva definida na origem."""
    assert pass_hat_k(sucessos=0, trials=8, k=0) == 1.0


def test_k_igual_a_trials_com_todos_sucessos():
    assert pass_hat_k(sucessos=3, trials=3, k=3) == 1.0


def test_sem_trials_e_sem_exigencia_da_um():
    """n == 0 e k == 0: vacuidade também, e não divisão por zero."""
    assert pass_hat_k(sucessos=0, trials=0, k=0) == 1.0


def test_sem_trials_com_k_positivo_e_nan():
    """Cenário sem nenhuma execução é célula faltando, não fracasso."""
    assert isnan(pass_hat_k(sucessos=0, trials=0, k=1))


@pytest.mark.parametrize(
    ("sucessos", "trials", "k"),
    [(-1, 8, 1), (2, -8, 1), (2, 8, -1), (9, 8, 1)],
)
def test_entrada_incoerente_levanta(sucessos, trials, k):
    """Negativo ou mais sucessos que trials é bug de quem chama — e aí sim é exceção,
    porque `NaN` silencioso viraria um ponto plausível no gráfico."""
    with pytest.raises(ValueError):
        pass_hat_k(sucessos, trials, k)


# ---------------------------------------------------------------------------
# 5. Curva e agregação (consumo do nb05)
# ---------------------------------------------------------------------------


def test_curva_cobre_k_de_um_ate_trials():
    curva = curva_pass_hat_k(sucessos=8, trials=8)

    assert list(curva) == [1, 2, 3, 4, 5, 6, 7, 8]
    assert all(valor == 1.0 for valor in curva.values())


def test_curva_com_k_max_maior_que_trials_traz_nan_no_excedente():
    """A grade do nb05 é k=1..8 mesmo quando um modelo tem menos runs."""
    curva = curva_pass_hat_k(sucessos=4, trials=4, k_max=6)

    assert curva[4] == 1.0
    assert isnan(curva[5]) and isnan(curva[6])


def test_agregacao_por_cenario_usa_a_lista_de_sucessos_binarios():
    """Entrada é `ScoreRecord.sucesso_binario` por run, agrupada por cenário."""
    resultados = {
        "CEN-01": [True, True, True, True],
        "CEN-02": [True, False, True, True],
    }

    por_cenario = pass_hat_k_por_cenario(resultados, k=2)

    assert por_cenario["CEN-01"] == pytest.approx(1.0)
    assert por_cenario["CEN-02"] == pytest.approx(comb(3, 2) / comb(4, 2))


def test_media_entre_cenarios_e_a_media_dos_pass_k():
    resultados = {
        "CEN-01": [True, True],
        "CEN-02": [True, False],
    }

    assert pass_hat_k_medio(resultados, k=2) == pytest.approx(0.5)


def test_media_e_nan_se_algum_cenario_nao_tem_trials_suficientes():
    """Comparar modelos exige a mesma base de cenários; média sobre subconjunto
    diferente seria um número comparável só na aparência."""
    resultados = {
        "CEN-01": [True, True, True, True],
        "CEN-02": [True, True],
    }

    assert isnan(pass_hat_k_medio(resultados, k=4))


def test_agregacao_sem_cenario_nenhum_e_nan():
    assert isnan(pass_hat_k_medio({}, k=1))
