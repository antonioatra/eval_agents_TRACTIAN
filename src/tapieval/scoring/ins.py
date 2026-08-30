"""INS.1, INS.2 e INS.3 — o recall por camada contra o gold humano, e o número que testa H0.

O QUE ESTE MÓDULO PRODUZ
    `METRICAS §7`: **INS.1** é recall por camada (falhas detectadas ÷ falhas reais);
    **INS.2** é `ΔRecall(N3 | N1+N2)` com IC bootstrap — *"o número que testa H0"*; **INS.3** é
    o falso alarme, porque detector barulhento não é barato. A figura principal do trabalho é
    a curva custo × recall que sai destes três.

    Nada aqui fala com rede nem re-executa run: as três leituras são função pura de
    `(n1, n2, n3, rótulo humano)` — todos já no disco.

AS CAMADAS QUE ESTE INSTRUMENTO DE FATO SEPARA, E POR QUE NÃO SÃO QUATRO
    `ARQUITETURA §12` desenha a curva como N1 → N2 → N3 → N4. **N1 e N2 não são separáveis na
    saída**: `severidade._falhas_de_processo` recebe os dois e o P1 dispara com
    `n2.cobertura_evidencial < 1.0` **ou** `n1.tools_faltantes` — é a fusão que o X28 registra,
    e ela está dentro do sha da taxonomia. Separá-las exigiria reescrever o classificador
    depois de congelado, que é exatamente o que o congelamento proíbe.

    Então os pontos que existem são três, e são estes:

    | camada | insumo | o que acrescenta |
    |---|---|---|
    | `n1n2` | trace + gabarito | P, D e C5 — deterministico, custo ~0 |
    | `n1n2n3_cego` | + judge sobre a RESPOSTA | C1, C4 — conteúdo sem evidência |
    | `n1n2n3_com_trace` | + judge sobre resposta E trace | C2, C3, C7 — exigem evidência |

⚠️ A METADE DETERMINÍSTICA DO GOLD É A SAÍDA DO PRÓPRIO DETECTOR (A27)
    O rotulador humano responde os campos fechados da rubrica; o código e a severidade são
    **derivados** por `classificar_falhas` a partir desses campos MAIS o `n1`/`n2` da run. Logo
    P1–P6, D1–D6 e C5 entram no gold e na detecção **iguais por construção**: ali
    `Recall(n1n2)` é 100% e INS.3 é 0 por identidade, **não por medição**.

    `recall_por_camada` devolve isso em `identidade=True` para que a leitura errada — "a camada
    barata já pega 100%" — não possa ser feita sem passar por cima de um campo que diz o
    contrário. **A INS.2 é robusta a isso**, e é o motivo de `METRICAS §7` marcar ela, e não a
    INS.1, como o número da hipótese: `ΔRecall` é uma diferença, a parte idêntica cancela, e
    sobra a fração do gold que só o judge alcança.

⚠️ O QUE O GOLD CEGO NÃO PODE ADJUDICAR
    O humano rotulou **sem ver a evidência**, então `afirmacoes_sem_suporte`,
    `contradiz_evidencia` e `recomendou_acao_sem_base` vêm `None` — não medidos. C2, C3 e C7
    **não existem no gold**, e portanto o judge `com_trace` não pode ganhar recall por eles:
    o que ele detectar ali conta como falso alarme na INS.3, não como acerto na INS.1.

    Isso não é defeito deste módulo nem do judge: é o alcance do gold que existe. Está aqui
    por escrito porque a leitura ingênua — "o judge com trace não acrescentou nada" — é falsa,
    e a certa é *"o gold não tem como dizer se acrescentou"*.
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from tapieval.schema.trace import N1Deterministico, N2Programatico
from tapieval.scoring.severidade import (
    VereditoDaRubrica,
    classificar_falhas,
    codigos,
)

Camada = Literal["n1n2", "n1n2n3_cego", "n1n2n3_com_trace"]

CAMADAS: tuple[Camada, ...] = ("n1n2", "n1n2n3_cego", "n1n2n3_com_trace")
"""Na ordem da curva: cada uma acrescenta insumo e custo sobre a anterior."""

AMOSTRA_DO_DENOMINADOR = "estimativa"
"""`METRICAS §5`: só a amostra de estimativa entra em κ e em recall.

A de melhoria é escolhida por desacordo entre camadas, e recall sobre casos difíceis não
estima recall na população — o mesmo motivo que exclui a melhoria do κ. O A25 mediu pior: ela
saiu 15/15 `sem_resposta_final`, um modo de falha só.
"""


class ErroDeINS(ValueError):
    """Entrada inválida de quem chama. Nunca dado ausente — esse tem campo próprio."""


@dataclass(frozen=True)
class ItemAvaliado:
    """Uma run com rótulo humano: o gold dela e o que cada camada detectou.

    Guardar os conjuntos, e não só as contagens, é o que permite responder *quais* códigos o
    judge acrescentou — que é a pergunta que a figura de H0 levanta e a tabela responde.
    """

    run_id: str
    gold: frozenset[str]
    detectado: Mapping[Camada, frozenset[str]]

    def acertos(self, camada: Camada) -> frozenset[str]:
        return self.gold & self.detectado[camada]

    def alarmes(self, camada: Camada) -> frozenset[str]:
        return self.detectado[camada] - self.gold


@dataclass(frozen=True)
class Recall:
    """INS.1 de uma camada, com o que é preciso para lê-la sem enganar."""

    camada: Camada
    valor: float
    n_gold: int
    n_acertos: int
    n_itens: int
    identidade: bool
    """`True` quando o recall é 100% **por construção** e não por medição (A27).

    Vale para a camada determinística: a metade P/D/C5 do gold sai do mesmo `n1`/`n2` que a
    detecção. Reportar 1.0 sem esta bandeira ao lado seria afirmar que a camada barata pega
    tudo, quando o que se mediu foi `x == x`.
    """


@dataclass(frozen=True)
class GanhoIncremental:
    """INS.2 — a diferença de recall entre duas camadas, com IC bootstrap.

    O IC é percentil sobre reamostragem **das runs**, e não dos códigos: a unidade de
    observação independente é a execução avaliada. Reamostrar códigos trataria dois códigos da
    mesma run como duas medidas independentes e devolveria um IC estreito demais.
    """

    de: Camada
    para: Camada
    delta: float
    recall_de: float
    recall_para: float
    ic95: tuple[float, float]
    n_itens: int
    repeticoes: int
    codigos_ganhos: frozenset[str]
    """Os códigos que a camada mais cara acertou e a mais barata não. É o que dá conteúdo à
    frase "o ganho se concentra numa classe de falha" — ou a desmente."""


# ---------------------------------------------------------------------------
# Montagem dos itens
# ---------------------------------------------------------------------------


def montar_item(
    run_id: str,
    n1: N1Deterministico,
    n2: N2Programatico,
    humano: VereditoDaRubrica,
    julgamentos: Mapping[str, VereditoDaRubrica | None],
) -> ItemAvaliado:
    """O gold e as três detecções de UMA run. Sem I/O.

    `julgamentos` mapeia `"cego"`/`"com_trace"` para o veredito daquela configuração, ou
    `None` quando ela não foi julgada. `None` propaga como camada **não medida**: o conjunto
    detectado é o da camada determinística, e não um conjunto vazio — vazio diria "o judge
    olhou e não achou nada", que é a leitura que o A10 e o X9 existem para impedir.
    """
    gold = frozenset(codigos(classificar_falhas(n1, n2, humano)))
    deterministico = frozenset(codigos(classificar_falhas(n1, n2, None)))

    detectado: dict[Camada, frozenset[str]] = {"n1n2": deterministico}
    for camada, chave in (("n1n2n3_cego", "cego"), ("n1n2n3_com_trace", "com_trace")):
        veredito = julgamentos.get(chave)
        detectado[camada] = (  # type: ignore[index]
            deterministico
            if veredito is None
            else frozenset(codigos(classificar_falhas(n1, n2, veredito)))
        )
    return ItemAvaliado(run_id=run_id, gold=gold, detectado=detectado)


# ---------------------------------------------------------------------------
# INS.1 — recall por camada
# ---------------------------------------------------------------------------


def recall_por_camada(itens: Sequence[ItemAvaliado], camada: Camada) -> Recall:
    """Recall agregado (micro): `Σ|acertos| / Σ|gold|` sobre as runs.

    Micro e não macro porque com n=20 a média de razões por run é dominada pelas runs com um
    código só no gold — uma delas vale tanto quanto uma run com seis. O denominador do
    documento é *"falhas reais"*, no plural e sem "por execução".

    Run com gold vazio (o humano não viu falha nenhuma) **não** entra no denominador nem no
    numerador. Ela não é descartada do trabalho: é o insumo da INS.3, onde uma acusação sobre
    ela é exatamente o falso alarme que se quer contar.
    """
    _validar(itens)
    n_gold = sum(len(item.gold) for item in itens)
    n_acertos = sum(len(item.acertos(camada)) for item in itens)
    return Recall(
        camada=camada,
        valor=(n_acertos / n_gold) if n_gold else float("nan"),
        n_gold=n_gold,
        n_acertos=n_acertos,
        n_itens=len(itens),
        identidade=camada == "n1n2",
    )


def falso_alarme(itens: Sequence[ItemAvaliado], camada: Camada) -> float:
    """INS.3: fração das acusações da camada que o gold não sustenta.

    `Σ|detectado \\ gold| / Σ|detectado|`. Sobre a metade determinística ele é **0 por
    identidade** (A27); é sobre os códigos de conteúdo que ele mede alguma coisa — e é lá que
    ele importa, porque um judge que acusa muito compra recall com ruído.
    """
    _validar(itens)
    n_detectado = sum(len(item.detectado[camada]) for item in itens)
    if not n_detectado:
        return float("nan")
    return sum(len(item.alarmes(camada)) for item in itens) / n_detectado


# ---------------------------------------------------------------------------
# INS.2 — o número que testa H0
# ---------------------------------------------------------------------------


def ganho_incremental(
    itens: Sequence[ItemAvaliado],
    *,
    de: Camada = "n1n2",
    para: Camada = "n1n2n3_com_trace",
    repeticoes: int = 10_000,
    seed: int = 42,
) -> GanhoIncremental:
    """`ΔRecall(para | de)` com IC 95% por bootstrap percentil sobre as runs.

    `seed` fixa porque o IC entra numa figura da banca: dois `make` do mesmo notebook não
    podem imprimir intervalos diferentes. É a mesma exigência que `tests/test_repro.py` faz do
    resto da pontuação.

    **O delta pode ser negativo, e isso é resultado, não erro.** Uma camada mais cara que
    detecta MENOS do gold refuta H0 na direção interessante — `ARQUITETURA §12` diz que a
    refutação é *"resultado mais forte e mais acionável que a confirmação"*. Nada aqui trunca
    em zero.
    """
    _validar(itens)
    if not itens:
        raise ErroDeINS("nenhum item: ΔRecall sobre conjunto vazio não é 0, é indefinido")

    recall_de = recall_por_camada(itens, de).valor
    recall_para = recall_por_camada(itens, para).valor
    delta = recall_para - recall_de

    sorteio = random.Random(seed)
    deltas: list[float] = []
    for _ in range(repeticoes):
        reamostra = [sorteio.choice(itens) for _ in itens]
        n_gold = sum(len(item.gold) for item in reamostra)
        if not n_gold:
            continue  # reamostra só com gold vazio: não informa sobre o delta
        acertos_de = sum(len(item.acertos(de)) for item in reamostra)
        acertos_para = sum(len(item.acertos(para)) for item in reamostra)
        deltas.append((acertos_para - acertos_de) / n_gold)

    deltas.sort()
    if deltas:
        inferior = deltas[int(0.025 * len(deltas))]
        superior = deltas[min(int(0.975 * len(deltas)), len(deltas) - 1)]
    else:  # pragma: no cover - só com todo gold vazio, que `_validar` já torna improvável
        inferior = superior = float("nan")

    ganhos: set[str] = set()
    for item in itens:
        ganhos |= item.acertos(para) - item.acertos(de)

    return GanhoIncremental(
        de=de,
        para=para,
        delta=delta,
        recall_de=recall_de,
        recall_para=recall_para,
        ic95=(inferior, superior),
        n_itens=len(itens),
        repeticoes=repeticoes,
        codigos_ganhos=frozenset(ganhos),
    )


def curva(itens: Sequence[ItemAvaliado]) -> list[Recall]:
    """As três camadas na ordem da curva — o eixo y da figura principal."""
    return [recall_por_camada(itens, camada) for camada in CAMADAS]


def recall_por_classe(
    itens: Sequence[ItemAvaliado], camada: Camada
) -> dict[str, Recall]:
    """O mesmo recall, quebrado por classe de falha (P, C, D).

    É a estratificação que H0 prediz: *"o ganho se concentra numa única classe"*. Sem ela a
    curva agregada confirmaria a hipótese por qualquer motivo — inclusive por um ganho
    espalhado por igual, que `ARQUITETURA §12` diz refutar a estratificação.
    """
    saida: dict[str, Recall] = {}
    for classe in ("P", "C", "D"):
        recortados = [
            ItemAvaliado(
                run_id=item.run_id,
                gold=frozenset(c for c in item.gold if c.startswith(classe)),
                detectado={
                    k: frozenset(c for c in v if c.startswith(classe))
                    for k, v in item.detectado.items()
                },
            )
            for item in itens
        ]
        saida[classe] = recall_por_camada(recortados, camada)
    return saida


# ---------------------------------------------------------------------------
# Recusas
# ---------------------------------------------------------------------------


def _validar(itens: Iterable[ItemAvaliado]) -> None:
    vistos: set[str] = set()
    for item in itens:
        if item.run_id in vistos:
            raise ErroDeINS(
                f"{item.run_id} aparece mais de uma vez: uma sessão de rotulagem retomada que "
                "reescreva rótulos já gravados faz a run pesar dobrado no recall, e o n "
                "reportado deixa de ser o número de execuções avaliadas"
            )
        vistos.add(item.run_id)
        faltando = [camada for camada in CAMADAS if camada not in item.detectado]
        if faltando:
            raise ErroDeINS(f"{item.run_id} não tem detecção para {faltando}")


__all__ = [
    "AMOSTRA_DO_DENOMINADOR",
    "CAMADAS",
    "Camada",
    "ErroDeINS",
    "GanhoIncremental",
    "ItemAvaliado",
    "Recall",
    "curva",
    "falso_alarme",
    "ganho_incremental",
    "montar_item",
    "recall_por_camada",
    "recall_por_classe",
]
