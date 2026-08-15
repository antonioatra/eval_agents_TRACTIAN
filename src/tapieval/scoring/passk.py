"""
pass^k — confiabilidade, não capacidade (METRICAS §7.2, INS.8).

    pass@k  — "pelo menos 1 das k tentativas passou"   otimista, mede CAPACIDADE
    pass^k  — "TODAS as k tentativas passaram"          pessimista, mede CONFIABILIDADE

`pass@k` seria mentiroso aqui: o técnico manda a pergunta uma vez e recebe uma resposta,
não existe "melhor de 5". `pass^k` decai rápido — 80% de acerto dá pass^5 ≈ 33% — e é
justamente esse decaimento, contrastado com a média simples, que mostra o quanto a média
esconde de variância.

A entrada de cada trial é o **sucesso binário** de `ScoreRecord.sucesso_binario`
(`schema/trace.py`): decisão correta, sem ação indevida, gate respeitado e sem
contradição com a evidência. Nenhuma função daqui importa o schema — elas recebem
contagens ou listas de `bool`, para servirem igualmente ao notebook (nb05), que trabalha
sobre DataFrame, e ao runner, que trabalha sobre `ScoreRecord`.

O estimador puro está separado da agregação de propósito: `pass_hat_k` é a métrica e
precisa ser auditável sozinha; como se soma cenário com cenário é decisão de análise, que
muda mais vezes do que a métrica.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

NAO_ESTIMAVEL = float("nan")


def pass_hat_k(sucessos: int, trials: int, k: int) -> float:
    """Probabilidade de k trials sorteados sem reposição terem TODOS passado.

    Estimador não enviesado do τ-bench, definido em METRICAS §7.2 como
    `comb(sucessos, k) / comb(trials, k)`.

    O cálculo aqui é a mesma razão reescrita em produto de razões:

        pass^k = ∏ (sucessos - i) / (trials - i),  i = 0..k-1

    A escolha é por **custo**, não por gosto: a forma fechada materializa dois inteiros
    que crescem como fatoriais — com 10^6 trials, `comb` produz números de centenas de
    milhares de dígitos e o cálculo passa a ser quadrático no tamanho deles, para depois
    devolver um float. O produto faz k multiplicações e pronto. Cada fator está em [0, 1]
    (as guardas abaixo garantem numerador e denominador não-negativos), então o produto
    decai monotonicamente: não há como estourar. O preço é o arredondamento acumulado —
    k erros relativos de ~1e-16, irrelevante para o k ≤ 8 da bateria (METRICAS §8.2).

    Devolve `NaN` quando `k > trials`. É comportamento especificado, não erro: a bateria
    tem células incompletas (uma run que morreu, um modelo com menos seeds) e a curva do
    nb05 precisa mostrar o buraco como buraco. Exceção mataria o notebook inteiro por
    causa de um ponto, e 0.0 mentiria — dado ausente viraria falha do agente.

    Entrada incoerente (negativos, ou mais sucessos que trials) é bug de quem chama e
    levanta `ValueError`: aí o silêncio produziria um ponto plausível no gráfico.
    """
    if sucessos < 0 or trials < 0 or k < 0:
        raise ValueError(f"argumentos não podem ser negativos: {sucessos=}, {trials=}, {k=}")
    if sucessos > trials:
        raise ValueError(f"sucessos ({sucessos}) não pode exceder trials ({trials})")

    if k > trials:
        return NAO_ESTIMAVEL
    if sucessos < k:
        # Curto-circuito necessário, não otimização: sem ele o produto passaria por um
        # fator zero e continuaria com fatores negativos, devolvendo -0.0.
        return 0.0

    probabilidade = 1.0
    for i in range(k):
        probabilidade *= (sucessos - i) / (trials - i)
    return probabilidade


def curva_pass_hat_k(sucessos: int, trials: int, k_max: int | None = None) -> dict[int, float]:
    """Curva `k -> pass^k` para k = 1..k_max, com `k_max = trials` por padrão.

    É o formato que o nb05 plota (uma curva por modelo). `k_max` explícito existe para
    manter a mesma grade de x entre modelos com número diferente de runs: o excedente vem
    `NaN` e some do gráfico, em vez de encurtar a linha e sugerir comparação indevida.
    """
    if k_max is None:
        k_max = trials
    return {k: pass_hat_k(sucessos, trials, k) for k in range(1, k_max + 1)}


def pass_hat_k_por_cenario(
    sucessos_por_cenario: Mapping[str, Sequence[bool]], k: int
) -> dict[str, float]:
    """`pass^k` de cada cenário, a partir dos `sucesso_binario` das runs daquele cenário.

    Agregar antes de estimar seria outra métrica: somar as runs de todos os cenários num
    par (sucessos, trials) único mistura dificuldade de cenário com inconsistência do
    modelo, que é exatamente o que `pass^k` deveria isolar.
    """
    return {
        cenario: pass_hat_k(sum(sucessos), len(sucessos), k)
        for cenario, sucessos in sucessos_por_cenario.items()
    }


def pass_hat_k_medio(sucessos_por_cenario: Mapping[str, Sequence[bool]], k: int) -> float:
    """Média dos `pass^k` dos cenários — o número reportado por modelo (METRICAS §10.5).

    Devolve `NaN` se qualquer cenário tiver menos de k trials, em vez de descartá-lo:
    comparar modelos exige a mesma base de cenários, e uma média sobre subconjuntos
    diferentes é comparável só na aparência. Sem cenário nenhum, `NaN` pela mesma razão.
    """
    valores = list(pass_hat_k_por_cenario(sucessos_por_cenario, k).values())
    if not valores or any(valor != valor for valor in valores):  # valor != valor: é NaN
        return NAO_ESTIMAVEL
    return sum(valores) / len(valores)
