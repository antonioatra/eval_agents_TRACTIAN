"""H2 — a comparação pareada entre dois modelos, e as duas exclusões sem as quais ela mente.

O QUE ESTE MÓDULO PRODUZ
    `ARQUITETURA §12 / H2`: *"comparados dois modelos locais de portes diferentes, a diferença
    será maior na acurácia dos ARGUMENTOS do que na escolha da FUNÇÃO"*. A hipótese vive
    inteira dentro da N1: `tool_f1` (N1.1) é escolher a função, `args_acc` (N1.2) é preencher o
    schema, e `METRICAS §N1.2` diz por que a segunda é **condicional** — separar
    *"não soube o que fazer"* de *"soube e preencheu mal"* é a hipótese inteira; somadas, ela
    não teria como ser testada.

    Sai de graça da bateria principal: 18 cenários de test × 2 modelos × 8 `sample_seed`.

POR QUE PAREADO, E NÃO DUAS MÉDIAS
    Os cenários têm dificuldades muito diferentes, e a variância entre cenários é maior que a
    diferença entre modelos. Comparar `média(14B)` com `média(8B)` põe essa variância inteira
    dentro do erro. O par `(mesmo cenário, mesma seed)` cancela a dificuldade: o que sobra na
    diferença é o modelo, que é a única coisa que variou.

    O bootstrap reamostra **os pares**, pela mesma razão que a INS.2 reamostra as runs: a
    unidade de observação independente é o par, não a execução solta.

⚠️ AS DUAS EXCLUSÕES, E POR QUE ELAS MUDAM A RESPOSTA
    Dois campos da N1 têm um valor de preenchimento que **não** significa "errou":

    - `args_acc = 0.0` quando `args_avaliados == 0`. É acurácia condicional sobre conjunto
      vazio: indefinida, não zero. Ler como zero é afirmar que o agente preencheu tudo errado
      quando ele não chegou a preencher nada.
    - `decisao_correta = False` quando `decisao_prevista is None`. O trace não tem
      `DecisionEvent` nem ato observável — é *"não houve decisão a comparar"*, e não
      *"decidiu errado"*.

    A segunda não é simétrica entre os modelos, e é aí que ela deixa de ser detalhe: das 37
    execuções sem decisão da bateria principal, **30 são do 14B** — o X31, a taxa de
    `parse_erro` 15× maior do modelo maior. Contá-las como decisão errada importa uma falha de
    FORMATO para dentro de uma afirmação sobre CAPACIDADE, e o efeito é grande: o Δ de
    `decisao_correta` vai de −0,102 (pares com decisão dos dois lados) a −0,139 (todos os
    pares). O segundo número é, em boa parte, o X31 disfarçado de H2.

    Por isso `montar_pares` descarta o par quando **qualquer um dos dois lados** não tem a
    métrica definida, e devolve os descartes contados por motivo — descarte silencioso muda o
    denominador sem aparecer em lugar nenhum. O X31 continua sendo resultado, e é reportado
    onde ele é o assunto; aqui ele sai do caminho para não ser contado duas vezes.

⚠️ O VEREDITO DE "CRUZA ZERO" PODE SER SORTEIO, E O MÓDULO DIZ QUANDO
    `args_acc` na bateria principal dá Δ = −0,061 com t ≈ −1,96 — em cima do limiar. O mesmo
    bootstrap com `seed=99` devolve IC que **não** cruza zero; com `seed=42`, IC que cruza.
    Nenhuma das duas é mais verdadeira: o dado está na fronteira e a semente decide a frase.

    Conferir isso rodando algumas sementes não serve — foi a primeira tentativa aqui, e ela
    passou: cinco sementes concordaram e a instabilidade só apareceu na sexta. Um teste que
    depende de sortear a semente certa não é teste.

    O que o módulo faz é medir a distância ao limiar diretamente. `p_bootstrap` é a fração de
    reamostras do lado errado do zero (bilateral), e "IC95 cruza zero" é exatamente
    `p_bootstrap > 0,05`. Perto de 0,05 esse booleano é ruído de Monte Carlo: com B
    reamostras, o erro padrão de `p` ali é `sqrt(0,05 × 0,95 / B)` ≈ 0,0022 para B = 10.000.
    `veredito_estavel` é `False` quando `p` está a menos de **três** desses erros padrão do
    limiar — para `args_acc`, `p` = 0,0514, a 0,0014 do corte.

    Quando ele é `False`, a frase honesta não é "significativo" nem "não significativo" — é
    *"o efeito está no limiar e este n não decide"*.
"""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from tapieval.schema.trace import ScoreRecord

Metrica = Literal["tool_f1", "tool_f1_liquido", "args_acc", "decisao_correta"]

METRICAS_DE_H2: tuple[Metrica, ...] = (
    "tool_f1",
    "tool_f1_liquido",
    "args_acc",
    "decisao_correta",
)

ERROS_PADRAO_DE_MARGEM = 3.0
"""Quantos erros padrão de Monte Carlo separam um veredito confiável de um sorteio.

Três é a mesma folga que se usa para dizer que uma diferença saiu do ruído. Aqui o ruído não é
o do fenômeno — é o do PRÓPRIO bootstrap, que reamostra um número finito de vezes.
"""


class ErroDeComparacao(ValueError):
    """Comparação impossível de fazer sem produzir número que engana."""


@dataclass(frozen=True)
class Par:
    """Uma célula do fatorial vista pelos dois modelos ao mesmo tempo."""

    scenario_id: str
    seed: int
    valor_a: float
    valor_b: float

    @property
    def delta(self) -> float:
        """`b − a`. O sinal é do modelo B, que por convenção é o MAIOR."""
        return self.valor_b - self.valor_a


@dataclass(frozen=True)
class ParesDaMetrica:
    """Os pares que sobreviveram, e a contabilidade de quem não sobreviveu."""

    metrica: Metrica
    modelo_a: str
    modelo_b: str
    pares: tuple[Par, ...]
    descartes: tuple[tuple[str, int], ...]
    """`(motivo, quantidade)` ordenado por motivo. Vazio quando nada foi descartado."""

    @property
    def n_descartados(self) -> int:
        return sum(n for _, n in self.descartes)


@dataclass(frozen=True)
class DiferencaPareada:
    """O Δ pareado de uma métrica, com IC bootstrap e o veredito sobre o veredito."""

    metrica: Metrica
    modelo_a: str
    modelo_b: str
    media_a: float
    media_b: float
    delta: float
    ic95: tuple[float, float]
    n_pares: int
    n_descartados: int
    descartes: tuple[tuple[str, int], ...]
    repeticoes: int
    seed: int
    p_bootstrap: float
    """Fração bilateral das reamostras do lado errado do zero.

    É a mesma informação do IC, em escala contínua — e é a escala contínua que permite dizer
    *quão perto* do limiar o resultado está, coisa que o booleano "cruza zero" apaga.
    """

    @property
    def erro_de_monte_carlo(self) -> float:
        """Erro padrão de `p_bootstrap` na vizinhança de 0,05, para este número de reamostras."""
        return (0.05 * 0.95 / self.repeticoes) ** 0.5

    @property
    def veredito_estavel(self) -> bool:
        """`False` quando o veredito "cruza zero?" está dentro do ruído do próprio bootstrap.

        É o caso do `args_acc` da bateria principal (`p` = 0,0514, a 0,0014 do corte). Reportar
        só o IC de uma semente ali seria escolher a frase pelo sorteio, e esta propriedade
        existe para que a escolha não possa ser feita sem que ela apareça.
        """
        margem = ERROS_PADRAO_DE_MARGEM * self.erro_de_monte_carlo
        return abs(self.p_bootstrap - 0.05) > margem

    @property
    def cruza_zero(self) -> bool:
        return self.ic95[0] <= 0.0 <= self.ic95[1]

    @property
    def leitura(self) -> str:
        """A frase que os dados sustentam — inclusive quando ela é 'não dá para dizer'."""
        if not self.veredito_estavel:
            return "no limiar — este n não decide"
        if self.cruza_zero:
            return "cruza zero"
        return "não cruza zero"


def _valor(score: ScoreRecord, metrica: Metrica) -> float | None:
    """O valor da métrica, ou `None` quando ela é **indefinida** naquela execução.

    O `None` daqui não existe no `ScoreRecord`: lá `args_acc` é `0.0` e `decisao_correta` é
    `False` nesses casos, porque o schema não tem como carregar "não avaliado" num float. A
    tradução mora aqui, num lugar só, e é o que impede que um zero de preenchimento entre numa
    média como se fosse desempenho.
    """
    n1 = score.n1
    if metrica == "args_acc":
        return None if n1.args_avaliados == 0 else float(n1.args_acc)
    if metrica == "decisao_correta":
        return None if n1.decisao_prevista is None else float(n1.decisao_correta)
    return float(getattr(n1, metrica))


def _motivo_da_indefinicao(metrica: Metrica, modelo: str) -> str:
    if metrica == "args_acc":
        return f"{modelo}: nenhuma chamada com tool correta — args_acc indefinida"
    return f"{modelo}: sem decisão observável no trace"


def montar_pares(
    scores: Iterable[ScoreRecord],
    metrica: Metrica,
    *,
    modelo_a: str,
    modelo_b: str,
) -> ParesDaMetrica:
    """Casa as execuções dos dois modelos por `(scenario_id, seed)`.

    Exige que os dois modelos tenham exatamente as mesmas células. Uma célula presente num
    modelo e ausente no outro não vira par — e se isso acontecer em qualquer quantidade a
    função **levanta**, em vez de comparar um fatorial furado com um inteiro: bateria
    incompleta é para ser reportada como tal, não emparelhada por cima.
    """
    por_celula: dict[tuple[str, int], dict[str, ScoreRecord]] = {}
    for score in scores:
        if score.model_key not in (modelo_a, modelo_b):
            continue
        celula = (score.scenario_id, score.seed)
        alojado = por_celula.setdefault(celula, {})
        if score.model_key in alojado:
            raise ErroDeComparacao(
                f"duas execuções para a mesma célula {celula} e o mesmo modelo "
                f"{score.model_key!r}: o par deixaria de ser um par"
            )
        alojado[score.model_key] = score

    if not por_celula:
        raise ErroDeComparacao(
            f"nenhuma execução de {modelo_a!r} ou {modelo_b!r} nestes scores"
        )

    incompletas = sorted(c for c, lado in por_celula.items() if len(lado) != 2)
    if incompletas:
        raise ErroDeComparacao(
            f"{len(incompletas)} célula(s) com só um dos modelos — o pareamento exige o "
            f"fatorial completo: {incompletas[:3]}"
        )

    pares: list[Par] = []
    descartes: Counter[str] = Counter()
    for (scenario_id, seed), lado in sorted(por_celula.items()):
        valor_a = _valor(lado[modelo_a], metrica)
        valor_b = _valor(lado[modelo_b], metrica)
        if valor_a is None or valor_b is None:
            # Descarta o PAR, e não só o lado indefinido: manter o outro lado compararia o
            # modelo com nada e desequilibraria o fatorial na direção de quem falhou menos.
            if valor_a is None:
                descartes[_motivo_da_indefinicao(metrica, modelo_a)] += 1
            if valor_b is None:
                descartes[_motivo_da_indefinicao(metrica, modelo_b)] += 1
            continue
        pares.append(Par(scenario_id=scenario_id, seed=seed, valor_a=valor_a, valor_b=valor_b))

    return ParesDaMetrica(
        metrica=metrica,
        modelo_a=modelo_a,
        modelo_b=modelo_b,
        pares=tuple(pares),
        descartes=tuple(sorted(descartes.items())),
    )


def _reamostrar(
    deltas: Sequence[float], *, repeticoes: int, seed: int
) -> list[float]:
    """As médias das reamostras, ordenadas. É o insumo do IC e do `p` de uma vez só."""
    sorteio = random.Random(seed)
    n = len(deltas)
    return sorted(sum(sorteio.choices(deltas, k=n)) / n for _ in range(repeticoes))


def diferenca_pareada(
    pares_da_metrica: ParesDaMetrica,
    *,
    repeticoes: int = 10_000,
    seed: int = 42,
) -> DiferencaPareada:
    """Δ pareado `(B − A)` com IC 95% percentil sobre reamostragem dos pares.

    `seed` é fixa porque o IC entra numa figura da banca — a mesma exigência que
    `tests/test_repro.py` faz do resto da pontuação. E é justamente porque ela é fixa que
    `p_bootstrap` precisa vir junto: uma semente fixa numa fronteira congela um veredito de
    sorteio e o apresenta como resultado.
    """
    pares = pares_da_metrica.pares
    if not pares:
        raise ErroDeComparacao(
            f"nenhum par sobreviveu para {pares_da_metrica.metrica!r}: a diferença sobre "
            f"conjunto vazio não é 0, é indefinida"
        )

    deltas = [p.delta for p in pares]
    n = len(pares)
    medias = _reamostrar(deltas, repeticoes=repeticoes, seed=seed)
    inferior = medias[int(0.025 * repeticoes)]
    superior = medias[min(int(0.975 * repeticoes), repeticoes - 1)]
    abaixo = sum(1 for m in medias if m <= 0.0) / repeticoes
    acima = sum(1 for m in medias if m >= 0.0) / repeticoes

    return DiferencaPareada(
        metrica=pares_da_metrica.metrica,
        modelo_a=pares_da_metrica.modelo_a,
        modelo_b=pares_da_metrica.modelo_b,
        media_a=sum(p.valor_a for p in pares) / n,
        media_b=sum(p.valor_b for p in pares) / n,
        delta=sum(deltas) / n,
        ic95=(inferior, superior),
        n_pares=n,
        n_descartados=pares_da_metrica.n_descartados,
        descartes=pares_da_metrica.descartes,
        repeticoes=repeticoes,
        seed=seed,
        p_bootstrap=2 * min(abaixo, acima),
    )


def comparar_h2(
    scores: Iterable[ScoreRecord],
    *,
    modelo_a: str,
    modelo_b: str,
    metricas: Sequence[Metrica] = METRICAS_DE_H2,
    repeticoes: int = 10_000,
    seed: int = 42,
) -> list[DiferencaPareada]:
    """As quatro métricas de H2 na ordem em que a hipótese as opõe.

    A hipótese é sobre a **razão** entre as duas primeiras e a terceira, não sobre nenhuma
    delas isolada: função (`tool_f1`, e a líquida do X24, sem o crédito da hidratação) contra
    argumentos (`args_acc`). `decisao_correta` entra porque é a métrica que fecha a leitura —
    escolher a função e preencher o schema não servem de nada se a decisão sai errada.
    """
    materializados = list(scores)
    return [
        diferenca_pareada(
            montar_pares(materializados, metrica, modelo_a=modelo_a, modelo_b=modelo_b),
            repeticoes=repeticoes,
            seed=seed,
        )
        for metrica in metricas
    ]
