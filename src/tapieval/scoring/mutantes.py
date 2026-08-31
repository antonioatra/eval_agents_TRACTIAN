"""INS.9 — a fração dos pares `(base, mutante)` que o instrumento distingue, e por três lentes.

O QUE ESTE MÓDULO PRODUZ
    `METRICAS §7.1`: quatro degradações deliberadas do agente (MUT1 tira uma tool do
    `list_tools` do servidor; MUT2 remove a exigência de citar evidência; MUT3 corta o budget
    de 12 para 3 chamadas; MUT4 autoriza concluir sem checar o baseline), e a pergunta é se o
    instrumento **percebe** cada uma. É poder de discriminação do avaliador, e não desempenho
    do agente: um instrumento que dá a mesma leitura para um agente são e um agente sabotado
    não mede nada, por mais bonita que seja a métrica que ele imprime.

O QUE A COLUNA `base` COMPRA, E POR QUE ELA NÃO EXISTIA ANTES
    A matriz de `METRICAS §7.1` era `6 cenários × 4 mutantes × 5 seeds`. Sem a coluna de
    controle, a INS.9 seria *"fração distinguida do original"* **sem original** — só daria para
    comparar mutante contra mutante, ou contra a média de outra bateria, com outro dia e outro
    servidor no meio. A `base` entrou em 30/08 com a margem que o corte do A16 liberou, e é ela
    que torna o par `(mesmo cenário, mesma seed, com e sem sabotagem)` possível.

AS TRÊS LENTES, E POR QUE A DIFERENÇA ENTRE ELAS É O ACHADO
    | lente | o que compara | o que ela é |
    |---|---|---|
    | `codigos` | os códigos da taxonomia que dispararam | a leitura rica |
    | `sem_s2` | `sucesso_binario_sem_s2` | a mitigação proposta para o X33 |
    | `nominal` | `sucesso_binario` de `METRICAS §6.5` | o corte oficial |

    Pela lente `nominal`, o poder é **zero** em todos os quatro mutantes: o corte de §6.5 exige
    trajetória perfeita, P1 dispara em quase toda run e vale S2, então base e mutante reprovam
    igual. Não é falta de detecção — é o X33 medido onde ele mais dói, e é o argumento mais
    forte de que aquele corte não pode ser a métrica reportada sozinha.

⚠️ DISTINGUIR NÃO É DETECTAR — A DIREÇÃO É METADE DO NÚMERO
    "Fração distinguida" é cega ao sinal: um par em que o mutante fica com **menos** falha que
    a base conta como distinção, e conta como se o instrumento tivesse acertado. Na bateria de
    30/08 isso não é hipótese: **o MUT3 é distinguido em 30 de 30 pares pela lente dos códigos,
    e em 22 deles o conjunto de códigos do mutante é subconjunto ESTRITO do da base** — o
    agente com o budget cortado dispara menos falhas que o agente são. Pela lente `sem_s2` ele
    "passa" em 30 de 30 contra 27% da base.

    O mecanismo é direto: vários códigos da taxonomia são proporcionais à OPORTUNIDADE. Um
    agente que só pode fazer três chamadas não tem como acumular redundância, violar
    precedência ou perder cobertura em oito passos — ele para antes. Cortar o budget não
    melhora o agente, melhora a nota.

    Por isso todo resultado daqui vem com `por_direcao`, e `poder_util` — a fração distinguida
    **na direção esperada** — é reportada ao lado da fração distinguida. Quando as duas se
    afastam, quem se afasta é a métrica, não o agente.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from tapieval.schema.trace import ScoreRecord
from tapieval.scoring.bateria import falhas_do_score
from tapieval.scoring.severidade import (
    codigos,
    sucesso_binario,
    sucesso_binario_sem_s2,
)

Lente = Literal["codigos", "sem_s2", "nominal"]
LENTES: tuple[Lente, ...] = ("codigos", "sem_s2", "nominal")

Direcao = Literal["esperada", "invertida", "lateral", "nenhuma"]

VARIANTE_DE_CONTROLE = "base"


class ErroDeMutantes(ValueError):
    """Leitura de INS.9 que não pode ser feita sem produzir número que engana."""


@dataclass(frozen=True)
class Leitura:
    """O que o instrumento diz sobre uma execução, pelas três lentes de uma vez."""

    codigos: frozenset[str]
    sem_s2: bool
    nominal: bool


def ler(score: ScoreRecord) -> Leitura:
    """Traduz um `ScoreRecord` para as três lentes.

    Passa por `falhas_do_score`, que é o mesmo caminho da pontuação da bateria — a INS.9 não
    pode ter classificador próprio, senão ela mediria o poder de um instrumento que ninguém
    usou.
    """
    falhas = falhas_do_score(score)
    return Leitura(
        codigos=frozenset(codigos(falhas)),
        sem_s2=sucesso_binario_sem_s2(falhas),
        nominal=sucesso_binario(falhas),
    )


@dataclass(frozen=True)
class ParDeMutante:
    """A mesma célula com e sem a sabotagem."""

    scenario_id: str
    seed: int
    variante: str
    base: Leitura
    mutante: Leitura

    def distingue(self, lente: Lente) -> bool:
        """A leitura do instrumento muda entre base e mutante — em qualquer direção."""
        return self.direcao(lente) != "nenhuma"

    def direcao(self, lente: Lente) -> Direcao:
        """Para que lado a leitura mudou.

        `esperada`: o mutante está pior aos olhos do instrumento, que é o que uma sabotagem
        deveria produzir. `invertida`: o mutante está MELHOR — a métrica premiou a degradação.
        `lateral` (só na lente dos códigos): mudou de falha sem que um conjunto contenha o
        outro, o que é distinção legítima mas não é confirmação de que o mutante piorou.
        """
        if lente == "codigos":
            base, mutante = self.base.codigos, self.mutante.codigos
            if base == mutante:
                return "nenhuma"
            if mutante > base:
                return "esperada"
            if mutante < base:
                return "invertida"
            return "lateral"

        base_ok = getattr(self.base, lente)
        mutante_ok = getattr(self.mutante, lente)
        if base_ok == mutante_ok:
            return "nenhuma"
        return "esperada" if base_ok and not mutante_ok else "invertida"


@dataclass(frozen=True)
class DeteccaoDeMutantes:
    """INS.9 de uma lente, para uma variante ou para o agregado."""

    lente: Lente
    variante: str | None
    """`None` = agregado sobre todas as variantes mutantes."""

    n_pares: int
    n_distinguidos: int
    por_direcao: tuple[tuple[Direcao, int], ...]

    @property
    def valor(self) -> float:
        """INS.9 como `METRICAS §7` a define: fração distinguida, em qualquer direção."""
        return self.n_distinguidos / self.n_pares

    @property
    def poder_util(self) -> float:
        """A fração distinguida **na direção esperada** — o número que não se engana sozinho.

        Quando `poder_util` é muito menor que `valor`, o instrumento está reagindo à sabotagem
        pelo lado errado: ele nota a diferença e a lê como melhora. É o caso do MUT3.
        """
        direcoes = dict(self.por_direcao)
        return direcoes.get("esperada", 0) / self.n_pares

    @property
    def fracao_invertida(self) -> float:
        """Fração dos pares em que a sabotagem MELHOROU a nota."""
        direcoes = dict(self.por_direcao)
        return direcoes.get("invertida", 0) / self.n_pares


def montar_pares(
    scores: Iterable[ScoreRecord],
    *,
    controle: str = VARIANTE_DE_CONTROLE,
) -> list[ParDeMutante]:
    """Casa cada execução mutante com a execução de controle da mesma célula.

    A chave é `(scenario_id, seed)`, e é exigida idêntica: parear um mutante com a base de
    outra seed mediria a variância entre seeds e a chamaria de poder de detecção.
    """
    por_celula: dict[tuple[str, int], dict[str, ScoreRecord]] = {}
    for score in scores:
        celula = (score.scenario_id, score.seed)
        alojado = por_celula.setdefault(celula, {})
        if score.variant_id in alojado:
            raise ErroDeMutantes(
                f"duas execuções para {celula} e a variante {score.variant_id!r}: "
                f"o par deixaria de ser um par"
            )
        alojado[score.variant_id] = score

    sem_controle = sorted(c for c, lado in por_celula.items() if controle not in lado)
    if sem_controle:
        raise ErroDeMutantes(
            f"{len(sem_controle)} célula(s) sem a variante de controle {controle!r} — sem "
            f"original a INS.9 mede 'distinto de quê?': {sem_controle[:3]}"
        )

    pares: list[ParDeMutante] = []
    for (scenario_id, seed), lado in sorted(por_celula.items()):
        base = ler(lado[controle])
        for variante, score in sorted(lado.items()):
            if variante == controle:
                continue
            pares.append(
                ParDeMutante(
                    scenario_id=scenario_id,
                    seed=seed,
                    variante=variante,
                    base=base,
                    mutante=ler(score),
                )
            )
    if not pares:
        raise ErroDeMutantes("nenhum par (base, mutante): só há a coluna de controle")
    return pares


def deteccao(
    pares: Sequence[ParDeMutante],
    lente: Lente,
    *,
    variante: str | None = None,
) -> DeteccaoDeMutantes:
    """INS.9 de uma lente, opcionalmente restrita a uma variante."""
    recortados = [p for p in pares if variante is None or p.variante == variante]
    if not recortados:
        raise ErroDeMutantes(
            f"nenhum par para variante={variante!r}: a fração sobre conjunto vazio não é 0, "
            f"é indefinida"
        )
    direcoes: Counter[Direcao] = Counter(p.direcao(lente) for p in recortados)
    return DeteccaoDeMutantes(
        lente=lente,
        variante=variante,
        n_pares=len(recortados),
        n_distinguidos=sum(n for d, n in direcoes.items() if d != "nenhuma"),
        por_direcao=tuple(sorted(direcoes.items())),
    )


def variantes(pares: Sequence[ParDeMutante]) -> list[str]:
    """As variantes mutantes presentes, em ordem — o eixo x da figura."""
    return sorted({p.variante for p in pares})


def tabela(
    pares: Sequence[ParDeMutante],
    *,
    lentes: Sequence[Lente] = LENTES,
) -> list[DeteccaoDeMutantes]:
    """Cada lente × cada variante, mais a linha agregada de cada lente.

    A tabela inteira, e não só a agregada, porque a média das quatro variantes esconde
    exatamente o caso que interessa: o MUT3 tem o maior `valor` e o menor `poder_util` do
    conjunto, e no agregado os dois se encontram no meio e não dizem nada.
    """
    saida: list[DeteccaoDeMutantes] = []
    for lente in lentes:
        for variante in variantes(pares):
            saida.append(deteccao(pares, lente, variante=variante))
        saida.append(deteccao(pares, lente))
    return saida
