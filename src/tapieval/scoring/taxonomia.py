"""T30 — severidade, frequência por código, e o campo de visão do instrumento.

O QUE ESTE MÓDULO PRODUZ
    `METRICAS §6` define a taxonomia e a escala; este módulo é a leitura dela sobre uma
    bateria já pontuada. Três coisas, nesta ordem de importância:

    1. **A distribuição de severidade por modelo** — quantas execuções têm S0, S1, S2 como
       pior falha, e quantas não têm falha nenhuma.
    2. **A análise de sensibilidade ao corte** — a taxa de aprovação com a linha em S2 (a
       oficial de §6.5), em S1 (a mitigação do X33) e em S0 (só segurança). O que a T30
       existe para responder é *quanto o resultado muda*, e a resposta tem duas metades que
       não se parecem: o **nível** muda muito, e a **ordem entre os modelos** não sobrevive à
       correção do X35.
    3. **A frequência por código, com exemplo real** — e, junto dela, a lista dos códigos que
       não apareceram, cada um com o motivo pelo qual não apareceu.

    Nada aqui lê disco, chama rede ou reclassifica: `Observacao` já chega com as falhas que
    `severidade.classificar_falhas` produziu, pelo mesmo caminho da pontuação da bateria.

⚠️ A ESCALA É S0–S3, E O ENUNCIADO DA T30 DIZ S0–S4
    Não há S4. Ele foi removido em 17/08 (X18) porque nenhum código o emitia, e um nível que
    o instrumento não sabe registrar se lê no relatório como *"nenhuma falha cosmética
    encontrada"* quando o que houve foi ausência de detector. `ESCALA` vem de
    `_ORDEM_DE_SEVERIDADE` e não de uma constante local, para que a figura da T30 não possa
    plotar um eixo com um nível a mais do que a régua congelada tem.

⚠️ A DISTRIBUIÇÃO DA BATERIA PRINCIPAL NÃO É O PERFIL DE FALHA DO AGENTE
    É o perfil que a camada determinística **enxerga**. As três baterias no disco foram
    pontuadas sem judge (`n3 is None` em todas as 288 + 150 + 24 execuções), e sem judge a
    classe C inteira — exceto C5, que é determinística — não é avaliada. Numa barra empilhada
    isso aparece como classe C vazia, que é indistinguível de *"o agente não errou conteúdo"*.

    `lacuna_de_cobertura` é a correção disso, e ela não é retórica: mede a frequência dos
    mesmos códigos no **gold humano** da amostra de dev, onde a rubrica foi respondida por
    pessoa. C1 (causa-raiz errada, **S1**) aparece em 14 das 20 execuções rotuladas. C1 é
    severidade que reprova no corte S1 — logo a taxa de aprovação da lente `sem_s2`, que é a
    mitigação que o X33 propôs e a manchete da T29, é **teto e não estimativa**. É isso que
    `TetoDaLente` calcula, com o aviso de que é projeção entre splits e não medição.

    `test_a_bateria_principal_nao_tem_n3` é o tripwire, no formato da T29: enquanto ele passar,
    esta docstring está correta; no dia em que alguém pontuar a principal com judge, ele falha
    e a falha é a instrução para refazer a figura com a classe C dentro.

POR QUE `Observacao` E NÃO `ScoreRecord`
    A mesma aritmética precisa rodar sobre duas fontes que não têm o mesmo tipo: a bateria
    (`ScoreRecord`, sem rótulo humano) e o gold de dev (`n1`/`n2` da calibração + o veredito
    da pessoa). Escrever a contagem duas vezes é como as duas leituras divergem sem ninguém
    perceber. `Observacao` é o mínimo que as duas têm em comum — quem rodou, o que falhou, e
    se a run é pontuável — e `observacoes_da_bateria` é o único adaptador que este módulo
    oferece; o gold é montado por quem tem os rótulos na mão.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from tapieval.schema.trace import ScoreRecord
from tapieval.scoring.bateria import falhas_do_score
from tapieval.scoring.severidade import (
    _ORDEM_DE_SEVERIDADE,
    CATALOGO_DE_FALHAS,
    CODIGOS_QUE_EXIGEM_N3,
    FALHAS_NAO_CLASSIFICAVEIS,
    Falha,
    Severidade,
    severidade_maxima,
)

ESCALA: tuple[Severidade, ...] = _ORDEM_DE_SEVERIDADE
"""A régua congelada, na ordem de gravidade. Não há S4 — ver a docstring do módulo."""

Corte = Literal["S0", "S1", "S2"]
"""Onde a linha do sucesso binário é traçada: reprovam as severidades ATÉ o corte, inclusive.

`"S2"` é a de `METRICAS §6.5` (reprovam S0, S1 e S2), `"S1"` é a variante `sem_s2` que §6.5 já
prevê como análise de sensibilidade, e `"S0"` é o corte de segurança pura. S3 não é corte
possível: reprovar tudo até S3 reprovaria toda execução com qualquer falha, e o binário
deixaria de ter informação além de "houve falha".
"""

CORTES: tuple[Corte, ...] = ("S2", "S1", "S0")
"""Do mais exigente para o menos. É a ordem em que a figura e a tabela leem."""

MotivoDeAusencia = Literal["schema", "camada_ausente", "medido_zero"]
"""Por que um código da tabela congelada não aparece na contagem.

Os três são visualmente idênticos num gráfico de barras — barra de altura zero — e dizem
coisas opostas. `"schema"` é buraco declarado do instrumento (`FALHAS_NAO_CLASSIFICAVEIS`);
`"camada_ausente"` é medição que não foi feita nesta bateria; `"medido_zero"` é o único que
autoriza a frase *"não aconteceu"*.
"""


class ErroDeTaxonomia(ValueError):
    """Leitura da taxonomia que não pode ser feita sem produzir número que engana."""


def severidades_que_reprovam(corte: Corte) -> tuple[Severidade, ...]:
    """As severidades reprovadas por um corte — de S0 até o corte, inclusive."""
    if corte not in CORTES:
        raise ErroDeTaxonomia(f"corte desconhecido: {corte!r}")
    return ESCALA[: ESCALA.index(corte) + 1]


# ---------------------------------------------------------------------------
# A unidade de observação
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Observacao:
    """Uma execução já classificada — o insumo de todo o resto deste módulo.

    `pontuavel=False` NÃO é falha do agente e por isso não tem código (`METRICAS §7.2`, e a
    docstring de `severidade.motivo_nao_pontuavel`). Ele é carregado aqui porque a análise de
    sensibilidade precisa dele: as 37 execuções sem decisão da bateria principal recebem só
    códigos de processo, nenhum S0 ou S1, e **aprovam** em todo corte abaixo de S2. É o X35, e
    sem esta flag ele voltaria a aparecer como confiabilidade do modelo maior.
    """

    run_id: str
    model_key: str
    falhas: tuple[Falha, ...]
    pontuavel: bool = True

    @property
    def severidade_maxima(self) -> Severidade | None:
        """A pior severidade presente, `None` quando não houve falha nenhuma."""
        return severidade_maxima(self.falhas)

    def aprova(self, corte: Corte) -> bool:
        """Sucesso binário com a linha neste corte."""
        reprovam = severidades_que_reprovam(corte)
        return not any(falha.severidade in reprovam for falha in self.falhas)


def observacoes_da_bateria(scores: Iterable[ScoreRecord]) -> list[Observacao]:
    """Adapta os `ScoreRecord` de um `scores.jsonl` para a unidade deste módulo.

    Passa por `falhas_do_score` — o mesmo caminho da INS.9 e da T29. Um classificador próprio
    aqui descreveria um instrumento que ninguém usou.
    """
    return [
        Observacao(
            run_id=score.run_id,
            model_key=score.model_key,
            falhas=tuple(falhas_do_score(score)),
            pontuavel=score.pontuavel,
        )
        for score in scores
    ]


def _do_modelo(obs: Sequence[Observacao], model_key: str) -> list[Observacao]:
    recortadas = [o for o in obs if o.model_key == model_key]
    if not recortadas:
        raise ErroDeTaxonomia(f"nenhuma execução de {model_key!r} nas observações recebidas")
    return recortadas


# ---------------------------------------------------------------------------
# 1. Distribuição de severidade
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PerfilDeSeveridade:
    """Quantas execuções de um modelo têm cada severidade como PIOR falha.

    Por execução e não por falha, de propósito: a soma das barras é o n do modelo, e a leitura
    *"tantas execuções chegaram a S0"* é a que a escala sustenta. Contar falhas daria um total
    maior que o n (a média é ~4 falhas por execução) e faria a figura parecer uma distribuição
    de probabilidade que não é.
    """

    model_key: str
    n_execucoes: int
    por_maxima: Mapping[Severidade, int]
    n_sem_falha: int

    def fracao(self, severidade: Severidade) -> float:
        return self.por_maxima.get(severidade, 0) / self.n_execucoes

    @property
    def niveis_vazios(self) -> tuple[Severidade, ...]:
        """As severidades que nenhuma execução atingiu como máxima.

        Na bateria principal são S3 e nada mais — e o motivo não é que ninguém erra só
        cosmética: é que P5 e P6 (os dois S3) quase nunca aparecem sozinhos. Um nível vazio
        na figura precisa de legenda dizendo isso, ou ele se lê como faixa não medida.
        """
        return tuple(s for s in ESCALA if not self.por_maxima.get(s, 0))


def perfil_de_severidade(obs: Sequence[Observacao], model_key: str) -> PerfilDeSeveridade:
    """A distribuição de severidade máxima de um modelo."""
    recortadas = _do_modelo(obs, model_key)
    por_maxima: dict[Severidade, int] = {s: 0 for s in ESCALA}
    sem_falha = 0
    for o in recortadas:
        maxima = o.severidade_maxima
        if maxima is None:
            sem_falha += 1
        else:
            por_maxima[maxima] += 1
    return PerfilDeSeveridade(
        model_key=model_key,
        n_execucoes=len(recortadas),
        por_maxima=por_maxima,
        n_sem_falha=sem_falha,
    )


# ---------------------------------------------------------------------------
# 2. Sensibilidade ao corte
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LinhaDeSensibilidade:
    """A taxa de aprovação de um modelo num corte — com e sem o X35.

    As duas taxas estão no mesmo objeto porque reportá-las separadas é como a primeira vira a
    manchete: `taxa` conta as execuções sem decisão como aprovadas (elas não têm S0 nem S1) e
    `taxa_entre_pontuaveis` não as conta. A diferença entre as duas **é** o X35 medido.
    """

    model_key: str
    corte: Corte
    n_execucoes: int
    n_aprovadas: int
    n_pontuaveis: int
    n_aprovadas_pontuaveis: int

    @property
    def n_aprovadas_sem_decisao(self) -> int:
        return self.n_aprovadas - self.n_aprovadas_pontuaveis

    @property
    def taxa(self) -> float:
        return self.n_aprovadas / self.n_execucoes

    @property
    def taxa_entre_pontuaveis(self) -> float:
        """A mesma taxa sobre as execuções em que houve decisão observável.

        Levanta se não houver nenhuma: taxa sobre denominador zero é `NaN` que atravessa a
        figura inteira sem avisar.
        """
        if not self.n_pontuaveis:
            raise ErroDeTaxonomia(
                f"{self.model_key} não tem execução pontuável — taxa sem denominador"
            )
        return self.n_aprovadas_pontuaveis / self.n_pontuaveis

    @property
    def fracao_da_aprovacao_sem_decisao(self) -> float:
        """Quanto da aprovação deste modelo vem de execução que não decidiu nada.

        `0.0` quando não houve aprovação — não é divisão por zero disfarçada de zero: sem
        aprovação nenhuma, nenhuma parte dela vem do X35, e a frase é verdadeira.
        """
        if not self.n_aprovadas:
            return 0.0
        return self.n_aprovadas_sem_decisao / self.n_aprovadas


def sensibilidade(
    obs: Sequence[Observacao],
    modelos: Sequence[str],
    *,
    cortes: Sequence[Corte] = CORTES,
) -> list[LinhaDeSensibilidade]:
    """A grade modelo × corte — o entregável da análise de sensibilidade da T30.

    A grade inteira é o resultado, não uma célula dela: é vendo os três cortes juntos que se
    separa o que depende de onde a linha foi traçada (o nível, que vai de 0% a ~44%) do que
    não depende (que nenhum corte ordena os modelos depois de descontado o X35).
    """
    return [
        _linha(_do_modelo(obs, modelo), modelo, corte)
        for modelo in modelos
        for corte in cortes
    ]


def _linha(
    recortadas: Sequence[Observacao], model_key: str, corte: Corte
) -> LinhaDeSensibilidade:
    pontuaveis = [o for o in recortadas if o.pontuavel]
    return LinhaDeSensibilidade(
        model_key=model_key,
        corte=corte,
        n_execucoes=len(recortadas),
        n_aprovadas=sum(o.aprova(corte) for o in recortadas),
        n_pontuaveis=len(pontuaveis),
        n_aprovadas_pontuaveis=sum(o.aprova(corte) for o in pontuaveis),
    )


@dataclass(frozen=True)
class OrdemDosModelos:
    """Quem lidera num corte, e se a liderança sobrevive ao desconto do X35.

    Existe para que *"a diferença entre os modelos é o X31 vestido de confiabilidade"* seja um
    booleano conferível e não uma leitura de gráfico — a mesma razão de `estabilidade.cruzamento`.
    """

    corte: Corte
    lider: str | None
    delta: float
    lider_entre_pontuaveis: str | None
    delta_entre_pontuaveis: float

    @property
    def sobrevive_ao_x35(self) -> bool:
        """`True` quando o mesmo modelo lidera com e sem as execuções sem decisão.

        Empate (`lider is None`) dos dois lados também sobrevive: a afirmação preservada é
        *"este corte não ordena os modelos"*, que é conclusão e não ausência dela.
        """
        return self.lider == self.lider_entre_pontuaveis


def ordem_dos_modelos(
    linhas: Sequence[LinhaDeSensibilidade], corte: Corte
) -> OrdemDosModelos:
    """Compara os dois modelos num corte. Exige exatamente dois — não é ranking.

    Com três ou mais, "o líder" esconde a distância para o terceiro, e a pergunta da T30 é
    sobre um par. Quem tiver três modelos chama duas vezes e diz qual par está comparando.
    """
    do_corte = [linha for linha in linhas if linha.corte == corte]
    if len(do_corte) != 2:
        raise ErroDeTaxonomia(
            f"ordem_dos_modelos compara dois modelos; recebi {len(do_corte)} no corte {corte}"
        )
    a, b = sorted(do_corte, key=lambda linha: linha.model_key)
    return OrdemDosModelos(
        corte=corte,
        lider=_lider(a, b, a.taxa, b.taxa),
        delta=a.taxa - b.taxa,
        lider_entre_pontuaveis=_lider(
            a, b, a.taxa_entre_pontuaveis, b.taxa_entre_pontuaveis
        ),
        delta_entre_pontuaveis=a.taxa_entre_pontuaveis - b.taxa_entre_pontuaveis,
    )


def _lider(
    a: LinhaDeSensibilidade, b: LinhaDeSensibilidade, ta: float, tb: float
) -> str | None:
    """`None` é empate exato, que na lente nominal (0 × 0) é o caso e não um erro."""
    if ta == tb:
        return None
    return a.model_key if ta > tb else b.model_key


# ---------------------------------------------------------------------------
# 3. Frequência por código, e as ausências
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FrequenciaDeCodigo:
    """Um código da tabela congelada, com quantas execuções o dispararam e um exemplo real.

    `n_por_modelo` conta **execuções**, não ocorrências: o classificador emite cada código no
    máximo uma vez por execução, então os dois números coincidem — mas o nome diz qual é o
    denominador, que é o que permite a fração ser lida como "X% das execuções".
    """

    codigo: str
    classe: str
    severidade: Severidade
    descricao: str
    detectada_por: str
    n_por_modelo: Mapping[str, int]
    n_execucoes_por_modelo: Mapping[str, int]
    exemplo_run_id: str
    exemplo_evidencia: str

    @property
    def n_total(self) -> int:
        return sum(self.n_por_modelo.values())

    @property
    def n_execucoes(self) -> int:
        return sum(self.n_execucoes_por_modelo.values())

    @property
    def fracao(self) -> float:
        return self.n_total / self.n_execucoes

    def fracao_do_modelo(self, model_key: str) -> float:
        n = self.n_execucoes_por_modelo.get(model_key, 0)
        if not n:
            raise ErroDeTaxonomia(f"nenhuma execução de {model_key!r} no denominador")
        return self.n_por_modelo.get(model_key, 0) / n


def frequencias(
    obs: Sequence[Observacao], modelos: Sequence[str]
) -> list[FrequenciaDeCodigo]:
    """Os códigos observados, do mais frequente para o menos, com exemplo real de cada um.

    O exemplo é a PRIMEIRA ocorrência em ordem de `run_id`, não uma escolhida a dedo: exemplo
    selecionado por quem escreve o relatório é ilustração, e o que a T30 pede é evidência de
    que o código dispara sobre dado real. `evidencia` já vem preenchida pelo classificador com
    o valor que o disparou, então o exemplo carrega o número e não só o nome da run.

    Códigos com zero observação não aparecem aqui — eles são `codigos_ausentes`, com o motivo.
    """
    n_execucoes = {modelo: len(_do_modelo(obs, modelo)) for modelo in modelos}
    por_codigo: dict[str, dict[str, int]] = {}
    exemplo: dict[str, tuple[str, str]] = {}

    for o in sorted(obs, key=lambda o: o.run_id):
        if o.model_key not in n_execucoes:
            continue
        for falha in o.falhas:
            por_codigo.setdefault(falha.codigo, dict.fromkeys(modelos, 0))
            por_codigo[falha.codigo][o.model_key] += 1
            exemplo.setdefault(falha.codigo, (o.run_id, falha.evidencia))

    saida = [
        FrequenciaDeCodigo(
            codigo=codigo,
            classe=CATALOGO_DE_FALHAS[codigo].classe,
            severidade=CATALOGO_DE_FALHAS[codigo].severidade,
            descricao=CATALOGO_DE_FALHAS[codigo].descricao,
            detectada_por=CATALOGO_DE_FALHAS[codigo].detectada_por,
            n_por_modelo=contagem,
            n_execucoes_por_modelo=n_execucoes,
            exemplo_run_id=exemplo[codigo][0],
            exemplo_evidencia=exemplo[codigo][1],
        )
        for codigo, contagem in por_codigo.items()
    ]
    return sorted(saida, key=lambda f: (-f.n_total, f.codigo))


@dataclass(frozen=True)
class CodigoAusente:
    """Um código da tabela que não apareceu, e por quê.

    O campo `motivo` é a razão de esta classe existir. Barra de altura zero não distingue
    *"o instrumento não sabe medir isto"* de *"esta bateria não mediu"* de *"mediu e deu
    zero"*, e as três autorizam frases diferentes no README.
    """

    codigo: str
    classe: str
    severidade: Severidade
    descricao: str
    motivo: MotivoDeAusencia
    explicacao: str


def codigos_ausentes(
    obs: Sequence[Observacao], *, camadas_medidas: Iterable[str] = ()
) -> list[CodigoAusente]:
    """Os códigos com zero observação, cada um com o motivo da ausência.

    `camadas_medidas` declara o que esta bateria de fato rodou — hoje só `"n3"` muda a
    resposta. Passar `("n3",)` sobre uma bateria pontuada com judge faz os códigos de conteúdo
    ausentes serem classificados como `"medido_zero"`; omitir faz deles `"camada_ausente"`.
    **O default é o conservador**: sem alguém afirmar que o judge rodou, a ausência de C1 não
    é lida como ausência de falha de causa-raiz.
    """
    observados = {falha.codigo for o in obs for falha in o.falhas}
    tem_n3 = "n3" in set(camadas_medidas)

    saida: list[CodigoAusente] = []
    for codigo in sorted(CATALOGO_DE_FALHAS):
        if codigo in observados:
            continue
        entrada = CATALOGO_DE_FALHAS[codigo]
        if codigo in FALHAS_NAO_CLASSIFICAVEIS:
            motivo: MotivoDeAusencia = "schema"
            explicacao = FALHAS_NAO_CLASSIFICAVEIS[codigo]
        elif codigo in CODIGOS_QUE_EXIGEM_N3 and not tem_n3:
            motivo = "camada_ausente"
            explicacao = (
                "exige veredito de rubrica (N3) e esta bateria foi pontuada sem judge; "
                "a ausência é da medição, não da falha"
            )
        else:
            motivo = "medido_zero"
            explicacao = "a camada que o detecta rodou em todas as execuções e nenhuma o disparou"
        saida.append(
            CodigoAusente(
                codigo=codigo,
                classe=entrada.classe,
                severidade=entrada.severidade,
                descricao=entrada.descricao,
                motivo=motivo,
                explicacao=explicacao,
            )
        )
    return saida


# ---------------------------------------------------------------------------
# 4. O campo de visão: o que a bateria não viu, medido onde há gold
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LacunaDeCobertura:
    """Um código visto no gold humano e invisível na bateria — com as duas frequências.

    As duas amostras não são a mesma e o objeto não finge que são: `n_execucoes_gold` são 20
    execuções de **dev** com rótulo humano, `n_execucoes_bateria` são as 288 de **test**. A
    comparação legítima é *"este código acontece, e nesta bateria ele não teria como aparecer"*
    — não *"a taxa de test é a de dev"*.
    """

    codigo: str
    severidade: Severidade
    descricao: str
    n_no_gold: int
    n_execucoes_gold: int
    n_na_bateria: int
    n_execucoes_bateria: int

    @property
    def fracao_no_gold(self) -> float:
        return self.n_no_gold / self.n_execucoes_gold

    @property
    def invisivel(self) -> bool:
        """Aparece no gold e nunca na bateria — o caso que a figura precisa marcar."""
        return self.n_no_gold > 0 and self.n_na_bateria == 0


def lacuna_de_cobertura(
    bateria: Sequence[Observacao], gold: Sequence[Observacao]
) -> list[LacunaDeCobertura]:
    """Compara a taxonomia observada na bateria com a observada no gold humano.

    Devolve **só** os códigos que o gold viu, ordenados do mais frequente no gold para o
    menos. Códigos que nem o gold viu não dizem nada sobre campo de visão — dizem que a
    amostra é pequena, que é outra limitação e já está declarada em `METRICAS §11`.
    """
    if not gold:
        raise ErroDeTaxonomia("gold vazio — sem ele não há com que comparar o campo de visão")

    n_gold, n_bat = len(gold), len(bateria)
    no_gold: dict[str, int] = {}
    for o in gold:
        for codigo in {f.codigo for f in o.falhas}:
            no_gold[codigo] = no_gold.get(codigo, 0) + 1
    na_bateria: dict[str, int] = {}
    for o in bateria:
        for codigo in {f.codigo for f in o.falhas}:
            na_bateria[codigo] = na_bateria.get(codigo, 0) + 1

    saida = [
        LacunaDeCobertura(
            codigo=codigo,
            severidade=CATALOGO_DE_FALHAS[codigo].severidade,
            descricao=CATALOGO_DE_FALHAS[codigo].descricao,
            n_no_gold=n,
            n_execucoes_gold=n_gold,
            n_na_bateria=na_bateria.get(codigo, 0),
            n_execucoes_bateria=n_bat,
        )
        for codigo, n in no_gold.items()
    ]
    return sorted(saida, key=lambda lac: (-lac.n_no_gold, lac.codigo))


@dataclass(frozen=True)
class TetoDaLente:
    """A taxa de aprovação de um corte lida como TETO, e por quanto ela é teto.

    ⚠️ **`teto_projetado` é projeção entre splits, não medição.** Ele aplica a uma bateria de
    test uma frequência observada em 20 execuções de dev, e o intervalo dessa frequência é
    largo. O número não entra em nenhuma conclusão do trabalho; ele existe para dimensionar
    uma afirmação que entra: a de que a lente `sem_s2` — a mitigação que o X33 propôs e a
    manchete da T29 — é **otimista por construção**, porque a classe de falha que ela mais
    deixaria passar é justamente a que a bateria não mediu.

    Se o número fosse pequeno, a lacuna seria nota de rodapé. Ele não é.
    """

    corte: Corte
    model_key: str
    taxa_observada: float
    fracao_do_gold_com_invisivel_que_reprova: float
    codigos_invisiveis: tuple[str, ...]

    @property
    def teto_projetado(self) -> float:
        """A taxa que sobraria se os códigos invisíveis reprovassem na mesma proporção do gold."""
        return self.taxa_observada * (1.0 - self.fracao_do_gold_com_invisivel_que_reprova)


def teto_da_lente(
    linha: LinhaDeSensibilidade,
    lacunas: Sequence[LacunaDeCobertura],
    gold: Sequence[Observacao],
) -> TetoDaLente:
    """Quanto da aprovação de um corte depende de a classe C não ter sido medida.

    Só contam as lacunas cuja severidade **reprova neste corte**: C4 é S2 e não muda nada no
    corte S1, C1 é S1 e muda tudo. Um teto que somasse as duas exageraria o efeito na direção
    que favorece o argumento — que é o erro que este módulo inteiro existe para não cometer.
    """
    reprovam = severidades_que_reprovam(linha.corte)
    invisiveis = tuple(
        sorted(lac.codigo for lac in lacunas if lac.invisivel and lac.severidade in reprovam)
    )
    if not gold:
        raise ErroDeTaxonomia("gold vazio — sem ele o teto não tem de onde sair")

    com_invisivel = sum(
        1 for o in gold if any(f.codigo in invisiveis for f in o.falhas)
    )
    return TetoDaLente(
        corte=linha.corte,
        model_key=linha.model_key,
        taxa_observada=linha.taxa,
        fracao_do_gold_com_invisivel_que_reprova=com_invisivel / len(gold),
        codigos_invisiveis=invisiveis,
    )


# ---------------------------------------------------------------------------
# 5. O documento — `docs/taxonomia_erros.md`
# ---------------------------------------------------------------------------

_ROTULO_DO_MOTIVO: Mapping[MotivoDeAusencia, str] = {
    "schema": "o instrumento não sabe medir",
    "camada_ausente": "esta bateria não mediu",
    "medido_zero": "mediu e deu zero",
}


def relatorio_markdown(
    *,
    bateria: str,
    modelos: Sequence[str],
    rotulos: Mapping[str, str],
    freqs: Sequence[FrequenciaDeCodigo],
    ausentes: Sequence[CodigoAusente],
    perfis: Sequence[PerfilDeSeveridade],
    linhas: Sequence[LinhaDeSensibilidade],
    ordens: Sequence[OrdemDosModelos],
    lacunas: Sequence[LacunaDeCobertura],
    tetos: Sequence[TetoDaLente],
) -> str:
    """Gera `docs/taxonomia_erros.md` inteiro a partir dos objetos já calculados.

    O documento é **gerado e não escrito à mão** pelo motivo que o `docs/resultados_passk.json`
    existe: número digitado num markdown envelhece na primeira vez que a bateria muda, e
    ninguém percebe. Aqui o texto fixo é o que não sai de conta — a definição de cada código
    vem da tabela congelada, e toda frequência vem dos objetos.

    Recebe tudo pronto em vez de recalcular: um relatório que refizesse as contas poderia
    discordar da figura que o notebook plotou, e duas verdades no mesmo entregável é o defeito
    que a T28 encontrou em si mesma.
    """
    nome = dict(rotulos)
    linhas_md: list[str] = [
        "# Taxonomia de erros observada — T30",
        "",
        f"**Gerado por** `notebooks/nb06_severidade_erros.ipynb` sobre `{bateria}`. "
        "Nenhum número deste documento é digitado à mão; todos saem de "
        "`tapieval.scoring.taxonomia`, que é função pura de `ScoreRecord`.",
        "",
        "> ⚠️ **A escala é S0–S3.** Não existe S4: ele foi removido em 17/08 (X18) porque "
        "nenhum código o emitia, e um nível que o instrumento não sabe registrar se lê no "
        "relatório como *\"nenhuma falha cosmética encontrada\"*.",
        "",
        "---",
        "",
        "## 1. Distribuição de severidade, por modelo",
        "",
        "Por **execução**, com a pior falha de cada uma. A soma de cada linha é o n do modelo.",
        "",
        "| modelo | " + " | ".join(ESCALA) + " | sem falha | n |",
        "|---|" + "---|" * (len(ESCALA) + 2),
    ]
    for perfil in perfis:
        celulas = [
            f"{perfil.por_maxima.get(s, 0)} ({perfil.fracao(s):.0%})" for s in ESCALA
        ]
        linhas_md.append(
            f"| **{nome.get(perfil.model_key, perfil.model_key)}** | "
            + " | ".join(celulas)
            + f" | {perfil.n_sem_falha} | {perfil.n_execucoes} |"
        )

    linhas_md += [
        "",
        "## 2. Sensibilidade ao corte — quanto o resultado muda",
        "",
        "`METRICAS §6.5` traça a linha em S2. As outras duas colunas são a análise de "
        "sensibilidade que a própria §6.5 prevê. `sem X35` desconta as execuções em que não "
        "houve decisão observável — elas recebem só códigos de processo, nenhum S0 ou S1, e "
        "por isso **aprovam** em todo corte abaixo de S2.",
        "",
        "| corte | reprova | modelo | aprovação | sem X35 | da aprovação, sem decisão |",
        "|---|---|---|---|---|---|",
    ]
    for linha in linhas:
        reprova = ", ".join(severidades_que_reprovam(linha.corte))
        linhas_md.append(
            f"| **{linha.corte}** | {reprova} | {nome.get(linha.model_key, linha.model_key)} "
            f"| {linha.n_aprovadas}/{linha.n_execucoes} = {linha.taxa:.1%} "
            f"| {linha.n_aprovadas_pontuaveis}/{linha.n_pontuaveis} = "
            f"{linha.taxa_entre_pontuaveis:.1%} "
            f"| {linha.fracao_da_aprovacao_sem_decisao:.0%} |"
        )

    linhas_md += ["", "### A ordem entre os modelos sobrevive ao corte?", ""]
    for ordem in ordens:
        lider = nome.get(ordem.lider, ordem.lider) if ordem.lider else "empate"
        lider_p = (
            nome.get(ordem.lider_entre_pontuaveis, ordem.lider_entre_pontuaveis)
            if ordem.lider_entre_pontuaveis
            else "empate"
        )
        veredito = "**sobrevive**" if ordem.sobrevive_ao_x35 else "⚠️ **não sobrevive**"
        linhas_md.append(
            f"- **corte {ordem.corte}** — líder: {lider} (Δ {ordem.delta:+.3f}); "
            f"descontado o X35: {lider_p} (Δ {ordem.delta_entre_pontuaveis:+.3f}). {veredito}"
        )

    linhas_md += [
        "",
        "## 3. A taxonomia observada",
        "",
        "Definição e severidade vêm da tabela congelada em 24/08 (`severidade.CATALOGO_DE_FALHAS`, "
        "assinada por sha256). Frequência e exemplo vêm da bateria. O exemplo é a **primeira "
        "ocorrência em ordem de `run_id`** — não uma escolhida a dedo.",
        "",
        "| código | sev. | definição | detectada por | "
        + " | ".join(nome.get(m, m) for m in modelos)
        + " | total |",
        "|---|---|---|---|" + "---|" * (len(modelos) + 1),
    ]
    for f in freqs:
        celulas = [f"{f.fracao_do_modelo(m):.0%}" for m in modelos]
        linhas_md.append(
            f"| `{f.codigo}` | {f.severidade} | {f.descricao} | `{f.detectada_por}` | "
            + " | ".join(celulas)
            + f" | **{f.n_total}/{f.n_execucoes}** |"
        )

    linhas_md += ["", "### Um exemplo real de cada código", ""]
    for f in freqs:
        linhas_md += [
            f"**`{f.codigo}` · {f.descricao}** — `{f.exemplo_run_id}`",
            "",
            f"> {f.exemplo_evidencia}",
            "",
        ]

    linhas_md += [
        "## 4. Os códigos que não apareceram, e por quê",
        "",
        "Barra de altura zero não distingue três coisas que dizem o oposto uma da outra.",
        "",
        "| código | sev. | definição | por quê |",
        "|---|---|---|---|",
    ]
    for a in ausentes:
        linhas_md.append(
            f"| `{a.codigo}` | {a.severidade} | {a.descricao} | "
            f"**{_ROTULO_DO_MOTIVO[a.motivo]}** — {a.explicacao} |"
        )

    if lacunas:
        linhas_md += [
            "",
            "## 5. O campo de visão — o que a bateria não teve como ver",
            "",
            "As mesmas execuções, medidas onde existe **rótulo humano**: a amostra de dev da "
            "T22. As duas amostras não são a mesma e a tabela não finge que são — o que ela "
            "sustenta é *\"este código acontece, e nesta bateria ele não teria como aparecer\"*.",
            "",
            "| código | sev. | no gold humano (dev) | na bateria (test) | invisível |",
            "|---|---|---|---|---|",
        ]
        for lac in lacunas:
            marca = "⚠️ **sim**" if lac.invisivel else "não"
            linhas_md.append(
                f"| `{lac.codigo}` | {lac.severidade} | "
                f"{lac.n_no_gold}/{lac.n_execucoes_gold} = {lac.fracao_no_gold:.0%} | "
                f"{lac.n_na_bateria}/{lac.n_execucoes_bateria} | {marca} |"
            )

    if tetos:
        linhas_md += [
            "",
            "### A consequência: a lente `sem S2` é teto, não estimativa",
            "",
            "⚠️ **Projeção entre splits, não medição.** Aplica a uma bateria de test uma "
            "frequência observada em 20 execuções de dev. O número não entra em conclusão "
            "nenhuma do trabalho — ele dimensiona uma que entra: a de que a mitigação "
            "proposta pelo X33 é otimista por construção.",
            "",
        ]
        for teto in tetos:
            invis = ", ".join(f"`{c}`" for c in teto.codigos_invisiveis) or "nenhum"
            linhas_md.append(
                f"- **{nome.get(teto.model_key, teto.model_key)}**, corte {teto.corte}: "
                f"observado {teto.taxa_observada:.1%} · "
                f"{teto.fracao_do_gold_com_invisivel_que_reprova:.0%} do gold tem "
                f"{invis} (severidade que reprova neste corte) · "
                f"**teto projetado {teto.teto_projetado:.1%}**"
            )

    linhas_md.append("")
    return "\n".join(linhas_md)
