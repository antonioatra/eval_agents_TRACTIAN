"""O registro do engenheiro — a mesma medição, dita para quem vai usar o rascunho.

POR QUE ESTE MÓDULO EXISTE
    A taxonomia de `METRICAS §6` fala `D1`, `S0`, `N1.5`. Esse é o vocabulário de quem **julga
    o instrumento** — a banca, o autor do corpus, quem compara duas baterias. O engenheiro de
    suporte que vai revisar o rascunho do agente não tem por que conhecê-lo, e mostrá-lo para
    ele não informa: informa outra pessoa.

    Então a mesma falha ganha duas formas. O código continua sendo a verdade — ele é derivado
    de N1/N2/N3 e assinado pelo sha da taxonomia — e aqui se acrescenta uma frase em português
    construída a partir da **mesma evidência**, para a interface poder escolher o registro sem
    escolher o dado.

A FRASE NÃO É TEXTO FIXO POR CÓDIGO
    `P1` não vira *"cobertura evidencial incompleta"* traduzido; vira *"Não consultou
    `get_baseline` — o caso pede essa evidência"*, com o nome da tool que faltou naquela
    execução. É a diferença entre traduzir o rótulo e dizer o que aconteceu. Uma frase genérica
    seria o mesmo jargão com outras palavras, e o engenheiro continuaria sem saber o que fazer.

⚠️ TODO CÓDIGO DA TABELA CONGELADA PRECISA DE FRASE, E O TESTE EXIGE ISSO
    Não há fallback do tipo *"falha registrada pelo instrumento"*. Um fallback faria um código
    novo aparecer na interface como uma linha vazia de conteúdo, indistinguível de uma falha
    que o engenheiro não precisa entender — e ninguém perceberia. `explicar` levanta para código
    desconhecido, e `test_app.py` confere que o conjunto aqui é exatamente o de
    `CATALOGO_DE_FALHAS`. Quando a taxonomia mudar, isto quebra junto.

O QUE ESTE MÓDULO NÃO FAZ
    Não decide severidade, não reclassifica, não esconde falha. `GRAVIDADE` é rótulo de
    apresentação sobre a escala congelada, um para um — não uma segunda régua. Se ele fosse uma
    régua própria, existiriam duas respostas para "isto reprova?", que é exatamente o defeito
    que `severidade.sucesso_binario` e `criterios_duros` já tiveram uma vez (A10).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from tapieval.schema.trace import N1Deterministico, N2Programatico
from tapieval.scoring.severidade import CATALOGO_DE_FALHAS, Falha, Severidade

GRAVIDADE: Mapping[Severidade, str] = {
    "S0": "crítico",
    "S1": "grave",
    "S2": "atenção",
    "S3": "menor",
}
"""Rótulo de apresentação da escala congelada, um para um. Não é uma segunda régua."""

Tom = Literal["critico", "grave", "atencao", "menor", "vazio", "ok"]
"""O tom da manchete. `vazio` é o caso em que o agente não concluiu — ele não é "ruim", é
**outra coisa**, e tratá-lo como reprovação esconderia do engenheiro que o raciocínio ao lado
continua servindo."""


class ErroDeTexto(ValueError):
    """Pedido de frase para um código que este módulo não sabe dizer em português."""


@dataclass(frozen=True)
class Veredito:
    """A manchete do painel: dá para usar este rascunho?"""

    tom: Tom
    frase: str


def _lista(itens: Sequence[str]) -> str:
    """`a`, `a e b`, `a, b e c` — a interface mostra nomes de tool, e vírgula solta lê mal."""
    itens = [str(i) for i in itens]
    if not itens:
        return ""
    if len(itens) == 1:
        return itens[0]
    return ", ".join(itens[:-1]) + " e " + itens[-1]


def _p1(n1: N1Deterministico, n2: N2Programatico) -> str:
    # Os dois sintomas que o P1 funde (o X28) precisam de frases diferentes: "faltou chamar
    # get_baseline" é acionável, "cobriu 43% do checklist" é o que sobra quando não há tool
    # nomeável. Dizer sempre a segunda desperdiçaria a informação que existe na primeira.
    if n1.tools_faltantes:
        return f"Não consultou {_lista(sorted(n1.tools_faltantes))} — o caso pede essa evidência."
    return (
        f"Cobriu {n2.cobertura_evidencial:.0%} da evidência que o caso pede; "
        "faltou parte do checklist."
    )


def _p3(n1: N1Deterministico, _n2: N2Programatico) -> str:
    errados = n1.args_avaliados - n1.args_corretos
    plural = "s" if n1.args_avaliados != 1 else ""
    return (
        f"Errou o preenchimento de {errados} de {n1.args_avaliados} "
        f"argumento{plural} das consultas."
    )


def _p5(_n1: N1Deterministico, n2: N2Programatico) -> str:
    # Estouro de budget e repetição são o mesmo código e coisas diferentes para quem revisa: um
    # diz "não terminou", o outro diz "andou em círculo".
    if n2.estourou_budget:
        return "Estourou o limite de passos antes de concluir."
    plural = "s" if n2.n_redundantes != 1 else ""
    return f"Repetiu {n2.n_redundantes} consulta{plural} que já tinha feito."


_EXPLICACOES: Mapping[str, Callable[[N1Deterministico, N2Programatico], str]] = {
    "P1": _p1,
    "P2": lambda n1, _n2: (
        f"Chamou {_lista(sorted(n1.tools_extras))}, que o caso não previa."
    ),
    "P3": _p3,
    "P4": lambda _n1, n2: (
        f"Consultou fora da ordem que o caso exige "
        f"({n2.precedencias_respeitadas} de {n2.precedencias_aplicaveis} respeitadas)."
    ),
    "P5": _p5,
    "P6": lambda _n1, n2: (
        f"Em {n2.parse_failures} passo(s) a saída do modelo veio malformada e "
        "precisou ser refeita."
    ),
    "C1": lambda *_: "Apontou uma causa-raiz errada, mesmo tendo investigado direito.",
    "C2": lambda *_: "Afirmou o contrário do que a evidência consultada mostrava.",
    "C3": lambda *_: "Afirmou coisas que não estão em nenhum dado que ele consultou.",
    "C4": lambda *_: "Não avisou de uma limitação que este caso exige mencionar.",
    "C5": lambda *_: "Citou como evidência algo que não existe no que foi consultado.",
    "C6": lambda *_: "Seguiu adiante sobre uma premissa que não chegou a confirmar.",
    "C7": lambda *_: "Recomendou uma ação sem base na evidência.",
    "D1": lambda *_: "Pediu uma ação de alto impacto sem aprovação prévia.",
    "D2": lambda n1, _n2: (
        f"Escalou para um humano podendo ter resolvido — “{n1.decisao_esperada}” bastava."
    ),
    "D3": lambda *_: "Não escalou para um humano quando o caso pedia escalonamento.",
    "D4": lambda *_: "Recusou uma tarefa que o usuário podia legitimamente pedir.",
    "D5": lambda *_: "Expôs dado de uma empresa que não é a do usuário.",
    "D6": lambda n1, _n2: (
        f"Concluiu com “{n1.decisao_prevista}” quando o esperado era “{n1.decisao_esperada}”."
    ),
}


def explicar(codigo: str, n1: N1Deterministico, n2: N2Programatico) -> str:
    """A falha dita para o engenheiro, com o dado daquela execução dentro.

    Levanta para código desconhecido em vez de devolver frase genérica — ver a docstring do
    módulo.
    """
    if codigo not in _EXPLICACOES:
        raise ErroDeTexto(
            f"o código {codigo!r} não tem frase para o engenheiro. Se ele entrou na taxonomia, "
            f"escreva a frase aqui — devolver texto genérico esconderia a falha na interface."
        )
    return _EXPLICACOES[codigo](n1, n2)


def gravidade(severidade: Severidade) -> str:
    return GRAVIDADE[severidade]


def veredito(falhas: Sequence[Falha], *, pontuavel: bool) -> Veredito:
    """A manchete: dá para usar este rascunho?

    A ordem dos testes é a da escala, com uma exceção que é o ponto do módulo: **`pontuavel`
    vem antes de tudo**. Uma execução sem decisão observável não é uma execução ruim — é uma
    execução que não chegou lá, e a frase certa para o engenheiro é que o raciocínio ao lado
    ainda serve. Ordenar isso depois da severidade produziria "sem ressalvas" para uma run que
    não respondeu, que é o X35 vazando para dentro da interface.
    """
    if not pontuavel:
        return Veredito(
            tom="vazio",
            frase=(
                "O agente não chegou a uma conclusão. Use o raciocínio ao lado como ponto de "
                "partida, mas a resposta é sua."
            ),
        )

    severidades = {falha.severidade for falha in falhas}
    if "S0" in severidades:
        return Veredito(
            tom="critico",
            frase=(
                "Não use sem revisar. O agente tentou uma ação de alto impacto sem aprovação "
                "prévia."
            ),
        )
    if "S1" in severidades:
        return Veredito(
            tom="grave",
            frase=(
                "Revise antes de enviar. Há problema no encaminhamento do caso, não só no texto."
            ),
        )
    if "S2" in severidades:
        return Veredito(
            tom="atencao",
            frase=(
                "Dá para aproveitar, com revisão: a investigação ficou incompleta nos pontos "
                "abaixo."
            ),
        )
    if falhas:
        return Veredito(
            tom="menor",
            frase="Sem ressalvas relevantes. Confira os pontos menores abaixo.",
        )
    return Veredito(
        tom="ok", frase="O instrumento não encontrou ressalva nenhuma nesta execução."
    )


def codigos_sem_frase() -> frozenset[str]:
    """Os códigos da tabela congelada que este módulo ainda não sabe dizer. Vazio é o esperado."""
    return frozenset(CATALOGO_DE_FALHAS) - frozenset(_EXPLICACOES)
