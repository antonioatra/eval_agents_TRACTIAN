"""T29 — `pass^k` por modelo, o que a média esconde, e a metade de H4 que não tem dado.

O QUE ESTE MÓDULO PRODUZ
    A T29 pedia duas coisas. Uma existe e é o resultado abaixo; a outra foi cortada em 30/08 e
    não pode ser reaberta por análise. Vale escrever qual é qual antes de qualquer número.

    **O que existe:** `pass^k` para k = 1..8 por modelo, sobre as 8 `sample_seed` da bateria
    principal, contrastado com a média simples das mesmas execuções. `passk.py` tem o
    estimador; aqui mora a agregação de propósito — que vetor entra nele, e como se lê a
    distância entre a curva e a média.

    **O que não existe: a área entre as curvas de ambiente fixo e livre (H4).** `env_seed` é
    constante por cenário em todas as três baterias no disco — conferido célula a célula pelo
    `test_a_bateria_no_disco_nao_tem_eixo_de_ambiente`, que é tripwire e não decoração: no dia
    em que alguém implementar o eixo, ele falha e manda reescrever esta docstring. O bloqueio é
    duplo e está declarado em `configs/bateria_ambiente.yaml`: `runner/matriz.py` não tem eixo
    de `env_seed`, e a bateria foi cortada por tempo de GPU (A16, confirmado em 30/08). Sem os
    dois braços não há área entre curvas, e **`pass^k` com ambiente livre não é estimável por
    reamostragem do que existe** — reamostrar 8 seeds do MESMO mundo mede a variância do
    modelo, que é justamente o braço que já se tem. Quem quiser H4 roda a bateria; não há
    conta que a substitua.

⚠️ A MÉTRICA OFICIAL É ZERO EM TODAS AS 288 EXECUÇÕES
    `sucesso_binario` de `METRICAS §6.5` — nenhuma falha S0, S1 ou S2 — dá **0/144 nos dois
    modelos**. `pass^k` sobre ela é a reta zero para todo k: o corte oficial não ordena os
    modelos, não decai, não tem o que esconder. É o X33 medido onde ele mais dói, e é por isso
    que `Lente` existe aqui em vez de o módulo assumir §6.5 em silêncio.

    A lente `sem_s2` — sem falha S0 ou S1, a mitigação proposta pelo X33 — é a única que produz
    curva. **Ela também tem um defeito, e ele não é pequeno** (ver abaixo). As duas são
    reportadas juntas porque nenhuma das duas sozinha é uma resposta honesta.

⚠️ AS 37 EXECUÇÕES SEM DECISÃO, E POR QUE ELAS MUDAM A MANCHETE
    37 execuções da bateria principal têm `pontuavel = False`, todas pelo mesmo motivo:
    `decisao_prevista is None`, o trace não tem `DecisionEvent` nem ato observável. **30 delas
    são do 14B** — o X31, a taxa de `parse_erro` 15× maior do modelo maior.

    O classificador dá a essas 37 execuções **só códigos de processo** (P1, P2, P3, P5, P6), e
    nenhum deles é S0 ou S1. Consequência: **as 37 passam na lente `sem_s2`**. Uma execução em
    que o agente não produziu decisão nenhuma é contada como sucesso pela lente que o X33
    propôs como conserto — e 30 desses sucessos são de um modelo só.

    O tamanho disso: das 55 aprovações do 14B em `sem_s2`, **30 vêm de execuções sem decisão**
    (55%); das 38 do 8B, 7 (18%). A vantagem do 14B em k=1 é, em maior parte, o X31 vestido de
    confiabilidade.

    `TratoDeIndecisa` põe as três leituras possíveis na mesa em vez de escolher uma calada:

    | trato | o que faz | o que custa |
    |---|---|---|
    | `excluir` | tira a run do vetor | células ficam desiguais e a curva **trunca** |
    | `falha` | conta como não-sucesso | mantém k = 1..8; assume que não entregar é falhar |
    | `incluir` | conta como sucesso | credita ao modelo a própria falha de formato |

    O padrão é `excluir`, que é a regra já congelada na docstring de `passk.py`. **Mas a
    premissa dela não vale para este motivo**, e isso precisa estar escrito: lá o argumento é
    que converter para `False` "transforma defeito do instrumento em erro do modelo", e o X31
    concluiu o contrário sobre o `parse_erro` — *"não é ruído, e não é a máquina: é o modelo"*.
    Para `decisao_prevista is None`, `falha` é a leitura defensável, e é a única das três que
    entrega a curva até k=8 sem creditar o X31 como acerto.

    Não resolvi isso escolhendo. Resolvi medindo as três, porque **a ordem em k=1 depende do
    trato e o resto não** — ver `cruzamento`.

O QUE SOBREVIVE ÀS TRÊS LEITURAS, E O QUE NÃO
    **Não sobrevive:** o nível das curvas, e quem lidera em k=1. Com `incluir` o 14B abre
    0,382 contra 0,264; com `falha` ele fica atrás já em k=1.

    **Sobrevive:** a partir de k=3 o 8B está à frente nas três leituras, e a média simples
    ordena os modelos ao contrário do `pass^k`. Sobrevive também o motivo, que é o que
    `decomposicao_de_variancia` mede: a variância do 8B é quase metade **entre** cenários — ele
    tem cenários que passa quase sempre e cenários que falha sempre —, enquanto a do 14B é
    predominantemente **dentro** do cenário, que é exatamente o que `pass^k` pune e a média
    apaga.

    E sobrevive o resultado mais desconfortável: **`pass^8` é 0,000 para os dois modelos em
    todas as lentes e tratos.** Nenhum cenário é entregue nas 8 seeds por nenhum dos dois. O
    piso não é artefato do corte — é o dado.

POR QUE A DECOMPOSIÇÃO AQUI NÃO É A DE H4, E O NOME DIZ ISSO
    `METRICAS §7.2` chama de "decomposição de variância" a separação **modelo × ambiente**, que
    exige os dois braços e não existe. A que este módulo faz é **entre cenários × dentro do
    cenário**, com o ambiente constante — outra pergunta, respondida com o dado que há. Ela
    explica o cruzamento das curvas; ela **não** quantifica inconsistência atribuível à
    plataforma, e `Decomposicao` não tem campo que sugira que quantifica.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from tapieval.schema.trace import ScoreRecord
from tapieval.scoring.bateria import falhas_do_score
from tapieval.scoring.passk import NAO_ESTIMAVEL, pass_hat_k_medio
from tapieval.scoring.severidade import sucesso_binario, sucesso_binario_sem_s2

Lente = Literal["sem_s2", "nominal"]
"""As duas leituras binárias de §6.5. `codigos`, a terceira lente da INS.9, não entra: ela é
conjunto de falhas, não sucesso, e `pass^k` precisa de um booleano por tentativa."""

LENTES: tuple[Lente, ...] = ("sem_s2", "nominal")

TratoDeIndecisa = Literal["excluir", "falha", "incluir"]
TRATOS: tuple[TratoDeIndecisa, ...] = ("excluir", "falha", "incluir")

K_MAXIMO_DA_BATERIA = 8
"""As 8 `sample_seed` de `METRICAS §8.2`. É o teto do eixo x, não um default de conveniência."""


class ErroDeEstabilidade(ValueError):
    """Leitura de `pass^k` que não pode ser feita sem produzir número que engana."""


def sucesso_da_run(score: ScoreRecord, lente: Lente) -> bool:
    """O booleano de uma tentativa, pela lente pedida.

    Passa por `falhas_do_score` — o mesmo caminho da pontuação da bateria e da INS.9. Um
    classificador próprio aqui mediria a estabilidade de um instrumento que ninguém usou.
    """
    falhas = falhas_do_score(score)
    if lente == "nominal":
        return sucesso_binario(falhas)
    return sucesso_binario_sem_s2(falhas)


@dataclass(frozen=True)
class VetoresDoModelo:
    """Os vetores de tentativas de um modelo, um por cenário, e quem ficou de fora."""

    model_key: str
    lente: Lente
    trato: TratoDeIndecisa
    por_cenario: Mapping[str, tuple[bool, ...]]
    descartes: tuple[tuple[str, int], ...]
    """`(motivo, quantidade)` ordenado por motivo. Vazio quando nada foi descartado."""

    @property
    def n_trials(self) -> int:
        return sum(len(v) for v in self.por_cenario.values())

    @property
    def n_descartados(self) -> int:
        return sum(n for _, n in self.descartes)

    @property
    def media_simples(self) -> float:
        """A média que o `pass^k` existe para contradizer — ponderada por execução.

        É de propósito que ela NÃO seja a média das taxas por cenário: a média simples do
        relatório ingênuo soma todas as runs e divide, e é essa que se quer contrastar.
        """
        if not self.n_trials:
            return NAO_ESTIMAVEL
        return sum(sum(v) for v in self.por_cenario.values()) / self.n_trials

    @property
    def k_maximo_estimavel(self) -> int:
        """O menor cenário manda no eixo x inteiro.

        `pass_hat_k_medio` devolve `NaN` assim que **um** cenário tem menos de k tentativas,
        porque média sobre subconjuntos diferentes de cenários só é comparável na aparência.
        Esta propriedade diz onde a curva vai truncar antes de o gráfico mostrar o buraco — é o
        preço do trato `excluir`, e ele fica legível em vez de virar linha que some.
        """
        if not self.por_cenario:
            return 0
        return min(len(v) for v in self.por_cenario.values())


def vetores_por_cenario(
    scores: Iterable[ScoreRecord],
    *,
    model_key: str,
    lente: Lente,
    trato: TratoDeIndecisa = "excluir",
) -> VetoresDoModelo:
    """Agrupa as execuções de um modelo em vetores de `bool` por cenário.

    O agrupamento é por cenário e não num vetor único porque `pass^k` sobre a bateria inteira
    misturaria dificuldade de cenário com inconsistência do modelo — a mesma razão que
    `pass_hat_k_por_cenario` documenta.

    Levanta se nenhuma execução do modelo aparecer: vetor vazio produziria `NaN` silencioso lá
    na frente, e a causa (nome de modelo errado) some do rastro.
    """
    if trato not in TRATOS:
        raise ErroDeEstabilidade(f"trato desconhecido: {trato!r}")

    por_cenario: dict[str, list[bool]] = {}
    descartes: Counter[str] = Counter()
    vistos = 0

    for score in scores:
        if score.model_key != model_key:
            continue
        vistos += 1

        if not score.pontuavel:
            if trato == "excluir":
                descartes[score.motivo_nao_pontuavel or "não pontuável, sem motivo"] += 1
                continue
            if trato == "falha":
                por_cenario.setdefault(score.scenario_id, []).append(False)
                continue

        por_cenario.setdefault(score.scenario_id, []).append(sucesso_da_run(score, lente))

    if not vistos:
        raise ErroDeEstabilidade(f"nenhuma execução de {model_key!r} nos scores recebidos")

    return VetoresDoModelo(
        model_key=model_key,
        lente=lente,
        trato=trato,
        por_cenario={c: tuple(v) for c, v in sorted(por_cenario.items())},
        descartes=tuple(sorted(descartes.items())),
    )


@dataclass(frozen=True)
class CurvaDeEstabilidade:
    """A curva `k -> pass^k` de um modelo, ao lado da média que ela contradiz."""

    model_key: str
    lente: Lente
    trato: TratoDeIndecisa
    passk: Mapping[int, float]
    media_simples: float
    n_trials: int
    n_descartados: int
    n_cenarios: int
    k_maximo_estimavel: int

    @property
    def truncada(self) -> bool:
        """`True` quando a curva não chega ao k pedido por falta de tentativas em algum cenário."""
        return any(math.isnan(v) for v in self.passk.values())

    @property
    def queda_da_media_ao_k_maximo(self) -> float:
        """Quanto a média simples some quando se exige consistência — o número da T29.

        `NaN` se a curva truncou antes do fim: reportar a queda até um k menor e chamá-la de
        "a queda" compararia modelos em k diferentes, que é a comparação que não vale.
        """
        ultimo = self.passk.get(max(self.passk)) if self.passk else None
        if ultimo is None or math.isnan(ultimo) or math.isnan(self.media_simples):
            return NAO_ESTIMAVEL
        return self.media_simples - ultimo


def curva(
    vetores: VetoresDoModelo, k_max: int = K_MAXIMO_DA_BATERIA
) -> CurvaDeEstabilidade:
    """`pass^k` médio entre cenários, para k = 1..k_max.

    O `NaN` de `pass_hat_k_medio` é preservado, não filtrado: ele é o buraco do trato
    `excluir`, e apagá-lo daria uma curva que parece completa sobre um denominador que mudou.
    """
    if k_max < 1:
        raise ErroDeEstabilidade(f"k_max precisa ser >= 1: {k_max}")

    return CurvaDeEstabilidade(
        model_key=vetores.model_key,
        lente=vetores.lente,
        trato=vetores.trato,
        passk={k: pass_hat_k_medio(vetores.por_cenario, k) for k in range(1, k_max + 1)},
        media_simples=vetores.media_simples,
        n_trials=vetores.n_trials,
        n_descartados=vetores.n_descartados,
        n_cenarios=len(vetores.por_cenario),
        k_maximo_estimavel=vetores.k_maximo_estimavel,
    )


@dataclass(frozen=True)
class Decomposicao:
    """Variância **entre cenários** × **dentro do cenário**, com o ambiente constante.

    Não é a decomposição de H4 (`METRICAS §7.2`), que separa modelo de ambiente e exige um
    braço que não foi rodado. Ver a docstring do módulo.
    """

    model_key: str
    lente: Lente
    trato: TratoDeIndecisa
    dentro: float
    """`E[Var(Y | cenário)]` — inconsistência do modelo repetindo o MESMO cenário."""
    entre: float
    """`Var(E[Y | cenário])` — o quanto o resultado depende de QUAL cenário caiu."""

    @property
    def total(self) -> float:
        return self.dentro + self.entre

    @property
    def fracao_entre(self) -> float:
        """A fração da variância que é dificuldade de cenário, não inconsistência.

        Alta (8B, ~0,48) descreve um modelo previsível: ele tem cenários que domina e cenários
        que não. Baixa (14B, ~0,19 a 0,35) descreve um modelo que varia de tentativa para
        tentativa no mesmo cenário — e é essa fração que `pass^k` cobra e a média não vê.
        """
        if not self.total:
            return NAO_ESTIMAVEL
        return self.entre / self.total


def decomposicao_de_variancia(vetores: VetoresDoModelo) -> Decomposicao:
    """Decompõe a variância dos Bernoulli em dentro-do-cenário e entre-cenários.

        Var(Y) = E[Var(Y | cenário)] + Var(E[Y | cenário])

    Os cenários pesam **igual**, não por número de execuções: o cenário é a unidade de
    observação aqui, mesma convenção de `pass_hat_k_medio`. Com células desiguais (o trato
    `excluir`) ponderar por execução deixaria os cenários mais completos mandarem na conta, e
    justamente os incompletos são os que o X31 esvaziou.
    """
    taxas = [sum(v) / len(v) for v in vetores.por_cenario.values() if v]
    if not taxas:
        raise ErroDeEstabilidade(
            f"sem cenário com execução para decompor ({vetores.model_key})"
        )

    dentro = sum(p * (1.0 - p) for p in taxas) / len(taxas)
    media = sum(taxas) / len(taxas)
    entre = sum((p - media) ** 2 for p in taxas) / len(taxas)

    return Decomposicao(
        model_key=vetores.model_key,
        lente=vetores.lente,
        trato=vetores.trato,
        dentro=dentro,
        entre=entre,
    )


def cruzamento(curva_a: CurvaDeEstabilidade, curva_b: CurvaDeEstabilidade) -> int | None:
    """O menor k em que `a` passa à frente de `b`, ou `None` se nunca passar.

    Existe para que a frase *"a média ordena os modelos ao contrário do `pass^k`"* seja um
    número conferível e não uma leitura de gráfico. Chamado com `a` = o modelo que a média põe
    ATRÁS: o cruzamento é o k a partir do qual a ordem se inverte.

    k em que qualquer uma das duas curvas é `NaN` é pulado — não é empate nem inversão, é
    ausência de dado, e tratá-lo como qualquer das duas inventaria um cruzamento.
    """
    if curva_a.model_key == curva_b.model_key:
        raise ErroDeEstabilidade(f"cruzamento de um modelo consigo mesmo: {curva_a.model_key}")

    for k in sorted(set(curva_a.passk) & set(curva_b.passk)):
        va, vb = curva_a.passk[k], curva_b.passk[k]
        if math.isnan(va) or math.isnan(vb):
            continue
        if va > vb:
            return k
    return None


def tabela(
    scores: Sequence[ScoreRecord],
    modelos: Sequence[str],
    *,
    lentes: Sequence[Lente] = LENTES,
    tratos: Sequence[TratoDeIndecisa] = TRATOS,
    k_max: int = K_MAXIMO_DA_BATERIA,
) -> list[CurvaDeEstabilidade]:
    """Cada modelo × cada lente × cada trato — a grade que o nb05 plota e o README cita.

    A grade inteira é o entregável, não uma célula dela: é vendo as 12 curvas juntas que se
    separa o que depende da escolha de método (o nível, a ordem em k=1) do que não depende (o
    cruzamento, o piso em k=8).
    """
    return [
        curva(
            vetores_por_cenario(scores, model_key=modelo, lente=lente, trato=trato),
            k_max=k_max,
        )
        for modelo in modelos
        for lente in lentes
        for trato in tratos
    ]
