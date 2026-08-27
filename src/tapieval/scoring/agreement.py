"""κ de Cohen entre o judge (N3) e o rotulador humano (N4), campo a campo (METRICAS §7, INS.6).

INS.6 é a linha que autoriza (ou não) o resto do trabalho a chamar N3 de medida. O recall de
N3 e o ganho incremental da INS.2 só significam alguma coisa se o julgamento do judge
concordar com o gold humano acima do acaso — e "acima do acaso" é precisamente o que κ mede,
e o que uma taxa de concordância bruta não mede: com um campo booleano enviesado, dois
rotuladores que chutam sempre `False` concordam em 90% dos itens sem terem olhado nada.

CAMPO A CAMPO, NUNCA AGREGADO
    `METRICAS §7` define a INS.6 como "judge × humano, **campo a campo**". Um κ médio sobre
    os seis campos da rubrica é o número que esconde exatamente o campo que reprova: a
    própria `METRICAS §4` já prevê que `responde_a_pergunta` é "o campo com maior flip rate
    esperado" e candidato a corte, e uma média de 0,72 com cinco campos em 0,85 e um em 0,15
    passaria como "aceitável, declarar como limitação" enquanto um sexto da rubrica não mede
    nada. Não existe função aqui que devolva um κ único — é omissão de propósito.

SÓ A AMOSTRA DE `estimativa`, E MISTURAR É ERRO NOMEADO
    As duas amostras de `METRICAS §5` têm propósitos opostos. A de **estimativa** (n≈20) é
    aleatória estratificada e é a única que estima concordância na POPULAÇÃO. A de
    **melhoria** (n≈15) é escolhida por desacordo entre camadas, flip e fronteira do score —
    ou seja, pelos casos onde a rubrica é ambígua. `METRICAS §5` é literal: aplicar aquela
    priorização à amostra de estimativa "destruiria o κ".

    Um rótulo de melhoria que chega aqui levanta `RotuloForaDaAmostraDeEstimativa` em vez de
    ser filtrado em silêncio. Filtro silencioso muda o denominador sem aviso — quem chamou
    continuaria dizendo "κ sobre 20 itens" com 14 —, que é o mesmo formato de erro que
    `labeling/amostra.py::AmostraInsuficiente` já recusa a cometer do lado da amostragem.

`None` DE QUALQUER LADO SAI DO DENOMINADOR DAQUELE CAMPO
    `None` nos três campos que exigem trace é **não medido**, nunca "limpo" — está escrito
    assim no `N3Judge`, no `N4Humano` e no `RotuloHumano`. Contar "não perguntei" como
    discordância empurraria o κ para baixo por defeito do instrumento, e o efeito não seria
    marginal: o judge **cego** tem `None` nos três campos por construção (a invariante de
    `N3Judge._campos_de_trace_seguem_a_configuracao`), então o κ do cego nesses campos seria
    zero por definição — e a comparação cego × com-trace, que é metade do achado da T20,
    viraria artefato da contagem. O par sai do denominador; quantos saíram vai na tabela,
    porque um campo com 3 pares de 20 é um número que ninguém deve reportar sem ver o n.

    Ao contrário de `None`, a lista **vazia** é resposta: "olhei e não achei".

`afirmacoes_sem_suporte` É BINARIZADO EM "houve ao menos uma"
    Não existe κ sobre lista — κ exige categorias nominais mutuamente exclusivas, e uma
    lista de strings não é uma. Das três saídas possíveis, esta implementação binariza em
    lista não-vazia = `True`, e as duas recusadas ficam registradas:

    - **κ sobre a contagem** (0, 1, 2, 3…) — é o que o flip rate faz em
      `scripts/calibrar_judge.py::_valor_comparavel`, e lá está certo: INS.7 compara o
      instrumento com ele mesmo, cinco repetições sobre o mesmo item, e pode se dar ao luxo
      da granularidade. Aqui não: com n≈20 pares, cada contagem distinta vira uma categoria
      com dois ou três itens, `p_e` fica dominado por células quase vazias e o κ resultante
      oscila violentamente com um par a mais ou a menos. Pior, ele passaria a punir
      *granularidade* — judge que lista duas afirmações onde o humano listou uma discordaria
      inteiro, embora os dois concordem no que a rubrica pergunta.
    - **Jaccard entre os conjuntos de ids** — mede sobreposição, não concordância corrigida
      pelo acaso; não é κ, não tem a faixa de interpretação de `METRICAS §7` e não é
      comparável com os outros cinco campos da tabela. Além disso os itens da lista são texto
      livre (`N3.3` é `list[str]`), não ids de um vocabulário fechado: dois rotuladores
      apontando a MESMA afirmação com redações diferentes teriam interseção vazia, e o número
      mediria escolha de palavra.

    O desempate é que a binarização é a leitura que o resto do pipeline **já** faz deste
    campo: `scoring/severidade.py` emite C3 com `if n3.afirmacoes_sem_suporte:` — verdade da
    lista, não o seu tamanho. Medir a concordância numa granularidade que nenhuma decisão a
    jusante usa seria medir outra coisa.

κ INDEFINIDO TEM NOME E MOTIVO
    Quando os dois lados põem todos os pares numa única categoria (e na mesma), `p_e = 1` e o
    denominador de κ zera. Isso não é `ZeroDivisionError` — é um resultado esperado com n≈20
    em campos de base desbalanceada — e muito menos `0.0`, que na escala de κ significa
    "concordância no nível do acaso" quando o que houve foi concordância **perfeita sem
    variância para medir**. Reportar 0.0 aí inverteria o sinal da conclusão. `Kappa.kappa` vem
    `KAPPA_INDEFINIDO` (NaN, como o `NAO_ESTIMAVEL` do `passk`), `Kappa.indefinido` é `True` e
    `Kappa.motivo_indefinido` carrega a frase que o notebook imprime na célula.

POR QUE ESCRITO À MÃO
    Não é falta de biblioteca: é que nenhuma implementação pronta faz as três coisas acima —
    descartar par por `None` sem tratá-lo como categoria, distinguir κ indefinido de κ zero, e
    devolver o n descartado junto do número. Uma chamada de terceiro com `labels=[…]` e um
    `nan_policy` faria as três em silêncio, e o silêncio é o que este projeto está tentando
    não comprar.

Como o `passk`, este módulo **não importa o schema**. Judge e humano chegam por tipagem
estrutural — `N3Judge`, `N4Humano`, `RotuloHumano` e o `dict` cru de uma linha de
`labels/humano_*.jsonl` servem igualmente, que é o que o notebook tem em mãos. Nada aqui lê
disco nem chama modelo: dado o mesmo par de entrada, o mesmo número.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

# ---------------------------------------------------------------------------
# A rubrica — os mesmos seis campos de METRICAS §4, na mesma ordem
# ---------------------------------------------------------------------------

CAMPOS_QUE_EXIGEM_TRACE = (
    "afirmacoes_sem_suporte",
    "contradiz_evidencia",
    "recomendou_acao_sem_base",
)
"""Os três de `METRICAS §4` que o judge cego não responde. Repetidos aqui, e não importados
de `labeling/cli.py`, porque aquele módulo lê disco e monta CLI — importá-lo arrastaria o
pacote de rotulagem para dentro de um estimador puro. `tests/test_agreement.py` amarra as
duas tuplas, então a duplicação quebra a suíte se divergir."""

CAMPOS_DA_RUBRICA = (
    "causa_raiz_correta",
    "mencionou_limitacao_relevante",
    "responde_a_pergunta",
    *CAMPOS_QUE_EXIGEM_TRACE,
)
"""Os seis campos de propósito, na ordem da tabela de `METRICAS §4` (N3.1, N3.2, N3.5, N3.3,
N3.4, N3.6). `justificativa` não entra: é texto livre para auditoria, não categoria."""

CATEGORIAS_DE_RESPONDE_A_PERGUNTA = ("sim", "parcial", "nao")
"""`responde_a_pergunta` é NOMINAL de três categorias, não booleano.

Colapsar `parcial` em qualquer um dos lados seria mudar a rubrica dentro da métrica que
deveria validá-la — e justamente no campo que `METRICAS §4` marca como o de maior flip rate
esperado, isto é, aquele onde `parcial` é a categoria disputada. κ nominal de três categorias
não pondera a distância entre elas (não há `sim` "mais perto" de `parcial` do que de `nao`),
o que é o tratamento correto: a rubrica não declara ordem, declara três respostas."""

AMOSTRA_QUE_ESTIMA = "estimativa"
"""A única amostra de `METRICAS §5` que entra no κ. Ver o cabeçalho e `labeling/amostra.py`."""


# ---------------------------------------------------------------------------
# Erros — nomeados, porque cada um deles seria um número plausível se fosse silêncio
# ---------------------------------------------------------------------------


class ErroDeConcordancia(ValueError):
    """Base dos erros da INS.6. Sempre entrada inválida de quem chama, nunca dado ausente."""


class RotuloForaDaAmostraDeEstimativa(ErroDeConcordancia):
    """Um rótulo da amostra de **melhoria** entrou no cálculo (`METRICAS §5`).

    Erro e não filtro: a amostra de melhoria é escolhida por dificuldade, e concordância
    medida sobre casos difíceis não estima concordância na população. Descartá-la em silêncio
    encolheria o n sem que o README soubesse.
    """


class RotuloSemAmostra(ErroDeConcordancia):
    """O lado humano não declara de qual amostra veio.

    `ARQUITETURA §5`, decisão 7: o campo `amostra` existe para tornar impossível, por
    acidente, calcular κ misturando as duas. Assumir `estimativa` quando o campo falta
    devolveria exatamente o acidente que a decisão 7 proíbe.
    """


class ConfiguracoesDiferentes(ErroDeConcordancia):
    """Judge e humano julgaram o mesmo item com insumos diferentes (cego × com trace).

    O `RotuloHumano` grava `configuracao` justamente porque "o par que entra no κ tem de ser
    judge e humano com o MESMO insumo — comparar humano cego com judge com trace mediria a
    diferença de insumo, não a concordância da rubrica" (`labeling/cli.py`).
    """


class RotuloDuplicado(ErroDeConcordancia):
    """A mesma run aparece em mais de um par.

    Uma sessão de rotulagem retomada que reescreva rótulos já gravados entra no κ como pares
    duplicados (`labeling/cli.py`, RETOMADA): o item duplicado pesa dobrado em `p_o` e em
    `p_e`, e o n reportado deixa de ser o número de execuções julgadas.
    """


class ValorForaDaRubrica(ErroDeConcordancia):
    """Um campo veio num tipo que a rubrica de `METRICAS §4` não prevê.

    O caso que motiva a checagem é `afirmacoes_sem_suporte` chegando já como contagem (`int`)
    ou como string: `bool(2)` e `bool("nao")` são `True` e o κ sairia plausível.
    """


# ---------------------------------------------------------------------------
# O resultado
# ---------------------------------------------------------------------------

KAPPA_INDEFINIDO = float("nan")
"""κ sem denominador. NaN pelo mesmo motivo do `passk.NAO_ESTIMAVEL`: some do gráfico em vez
de virar um ponto, e nenhuma aritmética a jusante o confunde com um valor medido."""

Faixa = Literal["excelente", "aceitavel", "insuficiente", "indefinido"]

LEITURA_DA_FAIXA: dict[Faixa, str] = {
    "excelente": "excelente",
    "aceitavel": "aceitável — declarar como limitação",
    "insuficiente": "o judge não mede o que se supõe",
    "indefinido": "κ indefinido — reportar a concordância observada e o n",
}
"""As três leituras de `METRICAS §7` mais a quarta que o documento não previu (κ indefinido).

O texto vem do documento e mora aqui, e não no notebook, para a figura e o README não
poderem discordar da faixa que o número recebeu.
"""

CORTE_EXCELENTE = 0.8
CORTE_ACEITAVEL = 0.6


@dataclass(frozen=True)
class Kappa:
    """κ de um único campo, com o que é preciso para lê-lo.

    `n_pares` e `n_descartados` não são diagnóstico opcional: um κ de 0,9 sobre 3 pares e um
    κ de 0,9 sobre 20 são a mesma casa decimal e conclusões diferentes, e nos campos que
    exigem trace o segundo número é grande sempre que o judge cego entra na conta.

    `p_o` e `p_e` ficam expostos porque são o que se reporta quando κ é indefinido: dizer
    "concordância observada 100%, sem variância para corrigir pelo acaso" é honesto; dizer
    "κ = 0" seria o contrário do que aconteceu.
    """

    kappa: float
    n_pares: int
    n_descartados: int
    concordancia_observada: float | None
    concordancia_esperada: float | None
    motivo_indefinido: str | None = None

    @property
    def indefinido(self) -> bool:
        return self.motivo_indefinido is not None

    @property
    def faixa(self) -> Faixa:
        return faixa_de_kappa(self.kappa)

    @property
    def leitura(self) -> str:
        return LEITURA_DA_FAIXA[self.faixa]


@dataclass(frozen=True)
class LinhaDaTabela:
    """Uma linha da tabela por campo da INS.6 — o formato que o notebook consome.

    `pandas.DataFrame([dataclasses.asdict(linha) for linha in tabela_por_campo(pares)])`
    monta a tabela do relatório sem nenhuma tradução intermediária.
    """

    campo: str
    n_pares: int
    n_descartados: int
    kappa: float
    faixa: Faixa
    leitura: str
    concordancia_observada: float | None
    motivo_indefinido: str | None


def faixa_de_kappa(kappa: float) -> Faixa:
    """A faixa de `METRICAS §7`: > 0.8 excelente · 0.6–0.8 aceitável · < 0.6 insuficiente.

    O documento sobrepõe as faixas em 0.8 ("> 0.8" e "0.6–0.8" reivindicam o mesmo ponto).
    Desempate desta implementação: **0.8 exato é `aceitavel`** — na dúvida, a leitura que
    obriga a declarar a limitação, nunca a que promove o número. 0.6 exato é `aceitavel`
    porque o documento escreve o intervalo fechado dos dois lados.
    """
    if math.isnan(kappa):
        return "indefinido"
    if kappa > CORTE_EXCELENTE:
        return "excelente"
    if kappa >= CORTE_ACEITAVEL:
        return "aceitavel"
    return "insuficiente"


# ---------------------------------------------------------------------------
# Leitura dos dois lados — tipagem estrutural, sem importar o schema
# ---------------------------------------------------------------------------


def _ler(objeto: Any, campo: str) -> Any:
    """Campo de um modelo Pydantic ou de um `dict` de JSONL, sem distinguir os dois.

    O judge chega como `N3Judge`; o humano chega como `N4Humano` (dentro do `ScoreRecord`) ou
    como `RotuloHumano` — ou como o `dict` cru de `labels/humano_*.jsonl`, que é o que o
    notebook tem antes de instanciar qualquer coisa. Ausência é `None`: os três campos que
    exigem trace são opcionais nos três modelos.
    """
    if isinstance(objeto, Mapping):
        return objeto.get(campo)
    return getattr(objeto, campo, None)


def categoria_do_campo(campo: str, valor: Any) -> bool | str | None:
    """O valor bruto de um campo virado categoria nominal — ou `None` se não foi medido.

    `afirmacoes_sem_suporte` é a única conversão: lista não-vazia vira `True`, lista vazia
    vira `False`, `None` continua `None`. O porquê está no cabeçalho do módulo.
    """
    if campo not in CAMPOS_DA_RUBRICA:
        raise ValorForaDaRubrica(
            f"{campo!r} não é campo da rubrica de METRICAS §4 (há {list(CAMPOS_DA_RUBRICA)})"
        )
    if valor is None:
        return None

    if campo == "afirmacoes_sem_suporte":
        if not isinstance(valor, (list, tuple, set, frozenset)):
            raise ValorForaDaRubrica(
                f"`afirmacoes_sem_suporte` é `list[str] | None` (METRICAS §4, N3.3) e veio "
                f"{type(valor).__name__} = {valor!r}: uma contagem ou uma string seriam "
                "binarizadas para `True` sem que nada quebrasse"
            )
        return len(valor) > 0

    if campo == "responde_a_pergunta":
        if valor not in CATEGORIAS_DE_RESPONDE_A_PERGUNTA:
            raise ValorForaDaRubrica(
                f"`responde_a_pergunta` só admite {list(CATEGORIAS_DE_RESPONDE_A_PERGUNTA)} "
                f"e veio {valor!r}"
            )
        return valor

    if not isinstance(valor, bool):
        raise ValorForaDaRubrica(f"{campo!r} é booleano na rubrica e veio {valor!r}")
    return valor


# ---------------------------------------------------------------------------
# O estimador — puro, sobre pares de categorias já extraídas
# ---------------------------------------------------------------------------


def kappa_de_cohen(pares: Iterable[tuple[Any, Any]]) -> Kappa:
    """κ de Cohen sobre pares `(categoria_do_judge, categoria_do_humano)`.

        κ = (p_o - p_e) / (1 - p_e)

    `p_o` é a concordância observada; `p_e`, a esperada se os dois rotulassem
    independentemente com as suas frequências marginais — `Σ_c p_judge(c)·p_humano(c)`. É a
    correção pelo acaso, e é ela que impede um campo desbalanceado de parecer validado: dois
    rotuladores que dizem `False` em 19 de 20 itens concordam em 95% e podem ter κ ≈ 0.

    Par com `None` de qualquer lado é **descartado**, não convertido em categoria: entra em
    `n_descartados` e sai do denominador. O conjunto de categorias sai do que os dois lados
    de fato usaram — uma categoria que ninguém usou contribui zero para `p_e` e não muda o
    resultado, então não há lista fechada a declarar aqui.

    Devolve `Kappa` indefinido (NaN + motivo) em dois casos, os dois esperados com n≈20 e
    nenhum deles exceção: nenhum par sobrando, e `p_e = 1` — que acontece exatamente quando
    os dois lados usam uma única categoria, e a mesma.
    """
    usados: list[tuple[Any, Any]] = []
    n_descartados = 0
    for do_judge, do_humano in pares:
        if do_judge is None or do_humano is None:
            n_descartados += 1
            continue
        usados.append((do_judge, do_humano))

    n = len(usados)
    if n == 0:
        return Kappa(
            kappa=KAPPA_INDEFINIDO,
            n_pares=0,
            n_descartados=n_descartados,
            concordancia_observada=None,
            concordancia_esperada=None,
            motivo_indefinido=(
                "nenhum par com os dois lados respondidos"
                + (f" ({n_descartados} descartado(s) por `None`)" if n_descartados else "")
            ),
        )

    marginal_do_judge = Counter(do_judge for do_judge, _ in usados)
    marginal_do_humano = Counter(do_humano for _, do_humano in usados)

    p_o = sum(1 for do_judge, do_humano in usados if do_judge == do_humano) / n
    p_e = sum(
        (marginal_do_judge[categoria] / n) * (marginal_do_humano[categoria] / n)
        for categoria in set(marginal_do_judge) | set(marginal_do_humano)
    )

    if p_e >= 1.0:
        # Só acontece com os dois lados concentrados na MESMA categoria única — a álgebra não
        # admite outro jeito de `Σ p_j(c)·p_h(c)` chegar a 1. `p_o` aí vale 1 e κ seria 0/0.
        categoria_unica = ", ".join(
            repr(categoria) for categoria in sorted(marginal_do_judge, key=repr)
        )
        return Kappa(
            kappa=KAPPA_INDEFINIDO,
            n_pares=n,
            n_descartados=n_descartados,
            concordancia_observada=p_o,
            concordancia_esperada=p_e,
            motivo_indefinido=(
                f"os dois lados responderam {categoria_unica} nos {n} pares: concordância "
                "perfeita, sem variância para corrigir pelo acaso (p_e = 1). Não é κ = 0"
            ),
        )

    return Kappa(
        kappa=(p_o - p_e) / (1.0 - p_e),
        n_pares=n,
        n_descartados=n_descartados,
        concordancia_observada=p_o,
        concordancia_esperada=p_e,
    )


# ---------------------------------------------------------------------------
# INS.6 — κ campo a campo sobre os pares judge × humano
# ---------------------------------------------------------------------------


def _validar(pares: Sequence[tuple[Any, Any]]) -> None:
    """As três recusas que precedem qualquer conta.

    Todas são condições em que o κ ainda sairia — plausível e errado. Por isso são exceção
    aqui, e não linha de log em algum lugar.
    """
    vistos: dict[Any, int] = {}
    for indice, (do_judge, do_humano) in enumerate(pares):
        amostra = _ler(do_humano, "amostra")
        if amostra is None:
            raise RotuloSemAmostra(
                f"o rótulo humano do par {indice} não declara `amostra`: sem ele não há como "
                "garantir que só a de estimativa entrou (METRICAS §5, ARQUITETURA §5 dec. 7)"
            )
        if amostra != AMOSTRA_QUE_ESTIMA:
            raise RotuloForaDaAmostraDeEstimativa(
                f"o rótulo humano do par {indice} é da amostra {amostra!r}: só a "
                f"{AMOSTRA_QUE_ESTIMA!r} entra no κ da INS.6 — a de melhoria é escolhida por "
                "dificuldade e não estima concordância na população (METRICAS §5)"
            )

        config_do_judge = _ler(do_judge, "configuracao")
        config_do_humano = _ler(do_humano, "configuracao")
        if (
            config_do_judge is not None
            and config_do_humano is not None
            and config_do_judge != config_do_humano
        ):
            raise ConfiguracoesDiferentes(
                f"par {indice}: judge {config_do_judge!r} contra humano {config_do_humano!r}. "
                "Insumo diferente mede diferença de insumo, não concordância da rubrica"
            )

        run_id = _ler(do_humano, "run_id")
        if run_id is not None:
            if run_id in vistos:
                raise RotuloDuplicado(
                    f"a run {run_id!r} aparece nos pares {vistos[run_id]} e {indice}: o item "
                    "pesaria dobrado em p_o e em p_e, e o n deixaria de ser o número de "
                    "execuções julgadas. As duas configurações do judge são DUAS medições de "
                    "κ (METRICAS §4), uma chamada cada"
                )
            vistos[run_id] = indice


def kappa_por_campo(pares: Sequence[tuple[Any, Any]]) -> dict[str, Kappa]:
    """A INS.6: um κ para cada um dos seis campos de `METRICAS §4`.

    `pares` são `(julgamento_do_judge, rótulo_humano)` da MESMA execução, já pareados por
    quem chama — o pareamento mora em `runs/<id>/scores/` e em `labels/`, que este módulo
    não lê. Judge pode ser `N3Judge`; humano pode ser `N4Humano`, `RotuloHumano` ou o `dict`
    de uma linha de `labels/humano_*.jsonl`.

    Não há e não haverá aqui uma função que devolva "o κ" — ver o cabeçalho.
    """
    pares = list(pares)
    _validar(pares)
    return {
        campo: kappa_de_cohen(
            (
                categoria_do_campo(campo, _ler(do_judge, campo)),
                categoria_do_campo(campo, _ler(do_humano, campo)),
            )
            for do_judge, do_humano in pares
        )
        for campo in CAMPOS_DA_RUBRICA
    }


def tabela_por_campo(pares: Sequence[tuple[Any, Any]]) -> list[LinhaDaTabela]:
    """A tabela da INS.6 pronta para o notebook, na ordem de `CAMPOS_DA_RUBRICA`.

    Campo, n de pares usados, n descartados por `None`, κ, e a leitura da faixa de
    `METRICAS §7` — os cinco números que a figura e o README precisam citar juntos. Vão
    juntos de propósito: κ sem o n ao lado é o formato de erro que este projeto passa o
    tempo todo recusando.
    """
    return [
        LinhaDaTabela(
            campo=campo,
            n_pares=resultado.n_pares,
            n_descartados=resultado.n_descartados,
            kappa=resultado.kappa,
            faixa=resultado.faixa,
            leitura=resultado.leitura,
            concordancia_observada=resultado.concordancia_observada,
            motivo_indefinido=resultado.motivo_indefinido,
        )
        for campo, resultado in kappa_por_campo(pares).items()
    ]
