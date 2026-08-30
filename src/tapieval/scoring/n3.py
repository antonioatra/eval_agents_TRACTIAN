"""
Camada N3 — LLM-as-judge (`METRICAS §4`). Duas configurações, MESMA rubrica, insumo
diferente: `cego` vê mensagem + `final_answer` + critério de sucesso; `com_trace` vê também
a evidência que o agente consultou. A diferença entre as duas é um dos pontos da curva de H0.

PERGUNTAS FECHADAS, NUNCA NOTA
    `METRICAS §4` é explícito, e o motivo tem quatro partes: concordância medível campo a
    campo (o κ da T23), pesos reajustáveis sem re-rodar o judge, justificativa auditável, e
    falha grave que zera em vez de descontar. Por isso este módulo não produz float nenhum —
    produz um `N3Judge` de booleanos e listas, e a aritmética é de quem lê.

O CEGO É O ÚNICO QUE PODE SERVIR DE Y
    N1 e N2 saem do trace. Um judge que também lê o trace correlaciona com eles por
    construção, e usá-lo como variável dependente mediria o instrumento contra ele mesmo. O
    cego não vê o trace: é a única leitura independente barata que o trabalho tem. Por isso
    os campos que exigem trace saem `None` no cego — não `False`. Ver `N3Judge`.

DUAS COISAS QUE ESTE MÓDULO SE RECUSA A FAZER, E POR QUÊ
    1. Julgar com evidência incompleta (`EvidenciaIncompleta`). Um `tool_result` cujo payload
       foi para blob e não pôde ser lido chegaria ao judge como bloco vazio, e todo número da
       resposta viraria `afirmacoes_sem_suporte`. O recall de C3 subiria por defeito do
       instrumento — o formato de erro do X9.
    2. Aceitar justificativa que cita identificador inexistente
       (`JustificativaComIdInventado`). A justificativa existe para tornar o julgamento
       auditável; um `tc_` inventado a torna pior que ausente, porque parece verificável.

CUSTO É OBRIGATÓRIO AQUI (X9, dívida da T35)
    `pontuar_n3` chama `medidor.registrar_llm(...)` sempre que fala com o modelo. Se
    esquecesse, os dois pontos de N3 na curva de H0 iriam a zero e NENHUM teste do resto do
    projeto pegaria: o schema de custo não distingue "judge grátis" de "judge não medido". O
    `medidor` é parâmetro obrigatório justamente para que esquecê-lo seja erro de assinatura,
    e não silêncio.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from tapieval.schema.custo import MedidorDeCusto
from tapieval.schema.trace import (
    ConfiguracaoDoJudge,
    FinalAnswer,
    N3Judge,
    ToolCall,
    ToolResult,
    TraceEvent,
)
from tapieval.scoring.gabarito import Cenario
from tapieval.sut.llm import Inferencia, esquema_estrito

DIRETORIO_DE_PROMPTS = Path(__file__).resolve().parents[3] / "prompts"
DIRETORIO_DE_FEWSHOTS = DIRETORIO_DE_PROMPTS / "fewshot"

RUBRICA_PADRAO = "v2"
"""A rubrica que o projeto usa quando ninguém pede outra.

Era `v1` até 29/08. A T21 fechou a comparação (`8173c97`) e o **A26 adotou a v2**: pareado por
item e agrupado pela hipótese que a v2 declarou ANTES de medir, ela conserta o alvo — nos
cenários cuja conclusão correta é uma ausência, os dois campos reescritos caem de 15/28 para
3/28 (p=0,004). Fora do alvo cobra 6/60 → 11/60, direção ruim e magnitude dentro do ruído
(p=0,227).

**O preço que o A26 supunha não existia.** O argumento contra a adoção era que
`recomendou_acao_sem_base` — único campo de veredito com flip zero na v1 — mede 3/22 na v2, e
o canário da T23 ficaria só com o `tokens_in`. Medido em 29/08 com a elegibilidade do próprio
canário: sob a v2 quem tem flip zero é `responde_a_pergunta`, **0/44** contra os 0/22 que
sustentavam a testemunha da v1. A testemunha troca de nome e melhora — o dobro de itens, e três
categorias em vez de duas, então uma troca de modelo pode movê-la nos dois sentidos.

**O que fica declarado como limitação:** `mencionou_limitacao_relevante` mede 11,4% na v2,
ainda acima do corte de 10% da T21 — mas contra 29,5% na v1."""

TEMPLATE_POR_CONFIGURACAO: Mapping[str, Mapping[ConfiguracaoDoJudge, str]] = {
    "v1": {
        "cego": "judge_cego_v1.md",
        "com_trace": "judge_trace_v1.md",
    },
    "v2": {
        "cego": "judge_cego_v2.md",
        "com_trace": "judge_trace_v2.md",
    },
}
"""A v2 muda SOMENTE `causa_raiz_correta` e `mencionou_limitacao_relevante` — os dois campos
que a INS.7 mediu acima do corte de 10% em 26/08. O resto é byte a byte igual à v1, para que a
comparação isole a reescrita em vez de medir duas rubricas inteiras uma contra a outra."""

CAMADA_POR_CONFIGURACAO: Mapping[ConfiguracaoDoJudge, str] = {
    "cego": "N3_cego",
    "com_trace": "N3_com_trace",
}

PADRAO_DE_ID = re.compile(r"\btc_\d+\b")
"""Como um `tool_call_id` aparece na justificativa. O formato é `tc_01`, fixado pelo
`ToolCall.tool_call_id` e citado literalmente pelo agente em `final_answer.citacoes`."""

TENTATIVAS_PADRAO = 2
"""Uma retentativa quando o judge inventa identificador, com o erro reapresentado — o mesmo
tratamento que o `Agent` dá a `parse_erro` (`sut/agent.py`). Duas porque a segunda passagem
carrega a correção; uma terceira só repetiria o mesmo prompt."""


class EvidenciaIncompleta(RuntimeError):
    """Um `tool_result` do trace não pôde ser materializado para o judge.

    Erro do INSTRUMENTO, não do agente: quem chama exclui a run do N3 em vez de pontuá-la
    com evidência parcial (`ScoreRecord.pontuavel=False` + `motivo_nao_pontuavel`)."""


class JustificativaComIdInventado(RuntimeError):
    """O judge citou `tool_call_id` que não existe no que ele viu, e insistiu na retentativa.

    Também é falha do instrumento. O `N3Judge` sai pela exceção e não por um campo porque
    não há campo honesto para "julguei, mas não dá para auditar"."""


# ---------------------------------------------------------------------------
# O que o modelo preenche — um esquema por configuração
# ---------------------------------------------------------------------------


class RespostaDoJudgeCego(BaseModel):
    """Os três campos de `METRICAS §4` que não exigem trace, mais a justificativa."""

    causa_raiz_correta: bool
    mencionou_limitacao_relevante: bool
    responde_a_pergunta: Literal["sim", "parcial", "nao"]
    justificativa: str


class RespostaDoJudgeComTrace(BaseModel):
    """Os seis campos da rubrica. A ordem põe os três compartilhados primeiro, igual ao
    cego: o modelo responde as mesmas perguntas na mesma ordem nas duas configurações, e
    a comparação campo a campo da T21 não pega diferença de posicionamento no prompt."""

    causa_raiz_correta: bool
    mencionou_limitacao_relevante: bool
    responde_a_pergunta: Literal["sim", "parcial", "nao"]
    afirmacoes_sem_suporte: list[str] = Field(default_factory=list)
    contradiz_evidencia: bool
    recomendou_acao_sem_base: bool
    justificativa: str


ESQUEMA_POR_CONFIGURACAO: Mapping[ConfiguracaoDoJudge, type[BaseModel]] = {
    "cego": RespostaDoJudgeCego,
    "com_trace": RespostaDoJudgeComTrace,
}


# ---------------------------------------------------------------------------
# Insumo — puro, derivado do trace
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BlocoDeEvidencia:
    """Uma consulta do agente, como o judge a vê.

    A hidratação não é caso especial: ela emite `tool_call`/`tool_result` normais antes do
    evento `hydration` (A18), então `get_asset` e `get_current_user` chegam aqui com os ids
    deles. Tratá-la à parte faria o judge acusar de "sem suporte" justamente o contexto que
    o agente recebeu de graça."""

    tool_call_id: str
    tool: str
    args: Mapping[str, Any]
    status: str
    corpo: Mapping[str, Any] | None

    def renderizar(self) -> str:
        argumentos = ", ".join(f"{chave}={valor!r}" for chave, valor in sorted(self.args.items()))
        cabecalho = f"[{self.tool_call_id}] {self.tool}({argumentos}) → {self.status.lower()}"
        if self.corpo is None:
            # Status sem payload é evidência de verdade: sustenta "não foi possível
            # verificar" e não sustenta conclusão técnica. O prompt diz isso ao judge.
            return f"{cabecalho}\n  (sem payload)"
        corpo = json.dumps(self.corpo, ensure_ascii=False, sort_keys=True, indent=2)
        return f"{cabecalho}\n{_indentar(corpo)}"


@dataclass(frozen=True)
class InsumoDoJudge:
    """Tudo que uma configuração do judge pode ver, separado do que ela vai ver.

    Guarda os dois conjuntos de uma vez de propósito: montar é puro e barato, e o mesmo
    insumo alimenta as duas configurações da mesma run — que é o que faz delas dois pontos
    da MESMA curva, e não duas medições de coisas diferentes."""

    scenario_id: str
    criterio_sucesso: str
    regra_exige: str
    solicitacao: str
    resposta: str
    citacoes: tuple[str, ...]
    evidencia: tuple[BlocoDeEvidencia, ...]

    def ids_visiveis(self, configuracao: ConfiguracaoDoJudge) -> frozenset[str]:
        """Os `tool_call_id` que aquela configuração tem como conhecer.

        Para o `com_trace` são os da evidência. Para o `cego` são só os que o agente
        alegou em `final_answer.citacoes` — ele não vê o trace, então qualquer outro id
        na justificativa dele só pode ter sido inventado.
        """
        if configuracao == "cego":
            return frozenset(self.citacoes)
        return frozenset(bloco.tool_call_id for bloco in self.evidencia)


def montar_insumo(
    eventos: Sequence[TraceEvent],
    cenario: Cenario,
    *,
    carregar_blob: Callable[[str], Mapping[str, Any]] | None = None,
) -> InsumoDoJudge:
    """O trace virando insumo do judge. Função pura de `(eventos, cenario)`.

    Sem I/O, sem relógio — a mesma regra de `pontuar_n1` e `pontuar_n2`, pelo mesmo motivo
    (`ARQUITETURA §5`, decisão 1: trace imutável, scores recomputáveis). `carregar_blob` é a
    única porta para disco, e é INJETADA: sem ela, um `body_sha` não resolvido vira
    `EvidenciaIncompleta` em vez de bloco vazio.
    """
    chamadas = {evento.tool_call_id: evento for evento in eventos if isinstance(evento, ToolCall)}
    resultados = [evento for evento in eventos if isinstance(evento, ToolResult)]

    blocos: list[BlocoDeEvidencia] = []
    for resultado in resultados:
        chamada = chamadas.get(resultado.tool_call_id)
        blocos.append(
            BlocoDeEvidencia(
                tool_call_id=resultado.tool_call_id,
                tool=chamada.tool_name if chamada else "(desconhecida)",
                args=dict(chamada.args) if chamada else {},
                status=str(resultado.status),
                corpo=_corpo(resultado, carregar_blob),
            )
        )

    finais = [evento for evento in eventos if isinstance(evento, FinalAnswer)]
    final = finais[-1] if finais else None

    return InsumoDoJudge(
        scenario_id=cenario.id,
        criterio_sucesso=cenario.criterio_sucesso,
        regra_exige=cenario.regra.exige,
        solicitacao=cenario.solicitacao,
        # Run que estourou o orçamento sem responder chega aqui sem `final_answer`. O texto
        # vazio é o julgamento correto disso — `responde_a_pergunta="nao"` — e não um erro:
        # a T19 mediu que 12 de 24 runs da piloto terminavam sem responder, e excluí-las do
        # N3 tiraria da amostra exatamente as piores.
        resposta=final.texto if final else "",
        citacoes=tuple(final.citacoes) if final else (),
        evidencia=tuple(blocos),
    )


def _corpo(
    resultado: ToolResult,
    carregar_blob: Callable[[str], Mapping[str, Any]] | None,
) -> Mapping[str, Any] | None:
    if resultado.body is not None:
        return resultado.body
    if resultado.body_sha is None:
        return None
    if carregar_blob is None:
        raise EvidenciaIncompleta(
            f"{resultado.tool_call_id} tem payload em blob ({resultado.body_sha[:12]}) e "
            "nenhum `carregar_blob` foi injetado: julgar sem ele transformaria todo número "
            "da resposta em `afirmacoes_sem_suporte`"
        )
    try:
        return carregar_blob(resultado.body_sha)
    except Exception as erro:  # noqa: BLE001 — a origem do erro é do chamador, não nossa
        raise EvidenciaIncompleta(
            f"{resultado.tool_call_id}: blob {resultado.body_sha[:12]} não pôde ser lido"
        ) from erro


# ---------------------------------------------------------------------------
# Prompt — puro
# ---------------------------------------------------------------------------


def carregar_fewshots(diretorio: Path = DIRETORIO_DE_FEWSHOTS) -> list[Mapping[str, Any]]:
    """Os exemplos, em ordem de nome. Escritos à mão (`PLANO` T20): viés estruturalmente
    zero, porque nenhum deles saiu de execução do corpus. O `ensina` de cada arquivo diz o
    que ele fixa, e é o que a T21 lê para decidir qual reescrever."""
    return [
        json.loads(caminho.read_text(encoding="utf-8"))
        for caminho in sorted(diretorio.glob("*.json"))
    ]


def renderizar_prompt(
    insumo: InsumoDoJudge,
    configuracao: ConfiguracaoDoJudge,
    *,
    fewshots: Sequence[Mapping[str, Any]],
    template: str | None = None,
    rubrica: str = RUBRICA_PADRAO,
) -> str:
    """O prompt completo de uma configuração. Puro: mesmo insumo, mesmo texto, sempre.

    A pureza é o que permite que a T23 congele o judge por sha256 do prompt renderizado —
    e é o que faz o flip rate da T21 medir variação do MODELO, não do template.
    """
    texto = template if template is not None else _ler_template(configuracao, rubrica)
    substituicoes = {
        "{criterio_sucesso}": insumo.criterio_sucesso or "(o cenário não declara critério)",
        "{regra_exige}": insumo.regra_exige or "(a regra não declara exigência)",
        "{solicitacao}": insumo.solicitacao,
        "{resposta}": insumo.resposta or "(o agente não respondeu)",
        "{citacoes}": ", ".join(insumo.citacoes) if insumo.citacoes else "(nenhum)",
        "{fewshots}": _renderizar_fewshots(fewshots, configuracao),
    }
    if configuracao == "com_trace":
        substituicoes["{evidencia}"] = _renderizar_evidencia(insumo.evidencia)

    for marcador, valor in substituicoes.items():
        texto = texto.replace(marcador, valor)

    sobraram = re.findall(r"\{[a-z_]+\}", texto)
    if sobraram:
        # Marcador não substituído chegaria ao modelo como literal `{evidencia}` e ele
        # julgaria sem ver nada, sem que nada quebrasse. É o mesmo silêncio do X9.
        raise ValueError(f"marcadores não substituídos no prompt {configuracao}: {sobraram}")
    return texto


ABERTURA_DA_RUBRICA = "## As perguntas"
FIM_DA_RUBRICA = "## Exemplos"


class RubricaSemSecao(ValueError):
    """O template não tem a seção `## As perguntas`, ou ela saiu vazia."""


def recortar_rubrica(texto: str, configuracao: str) -> str:
    """A seção `## As perguntas` de um template, sem o enquadramento em volta.

    ESTE RECORTE TEM DOIS CONSUMIDORES, E É DE PROPÓSITO
        O `rubrica_sha` do congelamento (T23) assina exatamente estes bytes, e a CLI de
        rotulagem (T22) os IMPRIME para o rotulador humano. Os dois lendo a mesma função é o
        que garante que o humano e o judge respondam à mesma pergunta — enquanto a CLI
        resumia a rubrica com as próprias palavras, ela perguntava *"mencionou a limitação
        relevante?"* onde a rubrica manda um procedimento de dois passos cujo caso (iii) é
        `true`. O κ da INS.6 mediria essa diferença de enunciado e a reportaria como
        discordância entre humano e máquina.

    O recorte é conferido dos dois lados. Um template sem a seção, ou com ela vazia, faria o
    `rubrica_sha` assinar o nada — e um sha do nada confere sempre, contra qualquer rubrica.
    """
    inicio = texto.find(ABERTURA_DA_RUBRICA)
    if inicio < 0:
        raise RubricaSemSecao(
            f"o template {configuracao!r} não tem a seção {ABERTURA_DA_RUBRICA!r}. O recorte "
            "da rubrica é por cabeçalho; sem ele o `rubrica_sha` assinaria texto vazio"
        )
    fim = texto.find(FIM_DA_RUBRICA, inicio)
    if fim < 0:
        raise RubricaSemSecao(
            f"o template {configuracao!r} tem {ABERTURA_DA_RUBRICA!r} mas não "
            f"{FIM_DA_RUBRICA!r} depois dela — não dá para saber onde a rubrica termina"
        )
    recorte = texto[inicio:fim].strip()
    if not recorte:
        raise RubricaSemSecao(f"a rubrica do template {configuracao!r} saiu vazia")
    return recorte


def perguntas_da_rubrica(configuracao: ConfiguracaoDoJudge, rubrica: str) -> str:
    """O recorte da rubrica adotada, lido do template que o judge de fato usa."""
    return recortar_rubrica(_ler_template(configuracao, rubrica), configuracao)


def _ler_template(configuracao: ConfiguracaoDoJudge, rubrica: str) -> str:
    try:
        arquivos = TEMPLATE_POR_CONFIGURACAO[rubrica]
    except KeyError:
        raise ValueError(
            f"rubrica {rubrica!r} não existe (há {sorted(TEMPLATE_POR_CONFIGURACAO)}). "
            "Rubrica errada em silêncio produziria uma tabela de scores que diz v2 e foi "
            "julgada por outra coisa"
        ) from None
    return (DIRETORIO_DE_PROMPTS / arquivos[configuracao]).read_text(encoding="utf-8")


def _renderizar_fewshots(
    fewshots: Sequence[Mapping[str, Any]], configuracao: ConfiguracaoDoJudge
) -> str:
    chave = "julgamento_cego" if configuracao == "cego" else "julgamento_com_trace"
    partes: list[str] = []
    for indice, exemplo in enumerate(fewshots, start=1):
        blocos = [
            f"### Exemplo {indice}",
            "",
            f"**Critério de sucesso do caso:**\n{exemplo['criterio_sucesso']}",
            "",
            f"**O que a regra do caso exige da resposta:**\n{exemplo['regra_exige']}",
            "",
            f"**Solicitação do cliente:**\n{exemplo['solicitacao']}",
        ]
        if configuracao == "com_trace":
            evidencia = "\n".join(
                BlocoDeEvidencia(
                    tool_call_id=bloco["tool_call_id"],
                    tool=bloco["tool"],
                    args={},
                    status=bloco["status"],
                    corpo=bloco.get("corpo"),
                ).renderizar()
                for bloco in exemplo["evidencia"]
            )
            blocos += ["", f"**Evidência consultada pelo agente:**\n{evidencia}"]
        blocos += [
            "",
            f"**Resposta final do agente:**\n{exemplo['resposta']}",
            "",
            "**Identificadores que o agente alega ter usado:** "
            + (", ".join(exemplo["citacoes"]) or "(nenhum)"),
            "",
            "**Julgamento:**",
            "```json",
            json.dumps(exemplo[chave], ensure_ascii=False, indent=2),
            "```",
        ]
        partes.append("\n".join(blocos))
    return "\n\n".join(partes)


def _renderizar_evidencia(evidencia: Sequence[BlocoDeEvidencia]) -> str:
    if not evidencia:
        # Run sem consulta nenhuma. Dizer isso é diferente de deixar vazio: o judge precisa
        # saber que a ausência é fato do trace, senão ele supõe que a evidência foi omitida.
        return "(o agente não consultou nenhuma evidência nesta execução)"
    return "\n\n".join(bloco.renderizar() for bloco in evidencia)


def _indentar(texto: str) -> str:
    return "\n".join(f"  {linha}" for linha in texto.splitlines())


# ---------------------------------------------------------------------------
# Julgamento — a única parte com I/O
# ---------------------------------------------------------------------------


def ids_inventados(justificativa: str, visiveis: frozenset[str]) -> list[str]:
    """Identificadores citados na justificativa que não existem no que o judge viu.

    Em ordem de aparição e sem repetir, para que a mensagem de retentativa nomeie o
    problema na ordem em que o modelo o cometeu."""
    vistos: list[str] = []
    for identificador in PADRAO_DE_ID.findall(justificativa):
        if identificador not in visiveis and identificador not in vistos:
            vistos.append(identificador)
    return vistos


def pontuar_n3(
    insumo: InsumoDoJudge,
    configuracao: ConfiguracaoDoJudge,
    inferencia: Inferencia,
    medidor: MedidorDeCusto,
    *,
    fewshots: Sequence[Mapping[str, Any]] | None = None,
    tentativas: int = TENTATIVAS_PADRAO,
    rubrica: str = RUBRICA_PADRAO,
) -> N3Judge:
    """Uma execução, uma configuração, um `N3Judge`.

    `medidor` é obrigatório e posicional: é a dívida X9 da T35 fechada por assinatura. Um
    judge não medido levaria os dois pontos de N3 da curva de H0 a zero sem que nenhum teste
    do resto do projeto notasse, porque `CustoRecord` não distingue grátis de não medido.

    O judge vê UMA execução por vez, nunca a tabela agregada (`METRICAS §4`): agregar é papel
    do notebook, e jogar a tabela aqui destruiria a rastreabilidade e estouraria a janela.
    """
    if medidor.camada != CAMADA_POR_CONFIGURACAO[configuracao]:
        # Custo carimbado na camada errada mistura os dois pontos da curva de H0 num só, e
        # o erro é invisível: os dois são `N3` e os dois têm token.
        raise ValueError(
            f"medidor está na camada {medidor.camada!r}, mas a configuração é "
            f"{configuracao!r} (esperava {CAMADA_POR_CONFIGURACAO[configuracao]!r})"
        )

    exemplos = fewshots if fewshots is not None else carregar_fewshots()
    prompt = renderizar_prompt(insumo, configuracao, fewshots=exemplos, rubrica=rubrica)
    esquema = esquema_estrito(ESQUEMA_POR_CONFIGURACAO[configuracao])
    visiveis = insumo.ids_visiveis(configuracao)

    mensagens: list[dict[str, str]] = [{"role": "user", "content": prompt}]
    ultimo_erro = ""

    for tentativa in range(1, tentativas + 1):
        resposta = inferencia.completar(mensagens, esquema)
        # `tokens_raciocinio` só existe em `RespostaDoJudge` (o cliente do Gemini). O
        # `getattr` é o que deixa o duplo de teste e um judge local satisfazerem `Inferencia`
        # sem carregar um campo que eles não têm como medir.
        medidor.registrar_llm(
            resposta.prompt_tokens,
            resposta.completion_tokens,
            getattr(resposta, "tokens_raciocinio", 0),
        )

        if not resposta.parse_ok or resposta.conteudo is None:
            ultimo_erro = f"a saída não validou contra o esquema: {resposta.parse_erro}"
        else:
            inventados = ids_inventados(resposta.conteudo["justificativa"], visiveis)
            if not inventados:
                return _montar_julgamento(resposta.conteudo, configuracao, resposta.latencia_ms)
            ultimo_erro = (
                f"a justificativa cita {inventados}, que não existem. Cite apenas "
                f"identificadores presentes no caso, ou nenhum."
            )

        if tentativa < tentativas:
            mensagens = [
                *mensagens,
                {"role": "assistant", "content": resposta.texto},
                {"role": "user", "content": f"Corrija e responda de novo: {ultimo_erro}"},
            ]

    raise JustificativaComIdInventado(
        f"{insumo.scenario_id} · judge {configuracao}: {tentativas} tentativas e "
        f"{ultimo_erro}"
    )


def _montar_julgamento(
    conteudo: Mapping[str, Any],
    configuracao: ConfiguracaoDoJudge,
    latencia_ms: int,
) -> N3Judge:
    """O dicionário validado virando `N3Judge`.

    Os três campos que exigem trace não são mencionados na configuração cega — nem como
    `None` explícito, nem como `False`. O default do schema já é `None`, e o validador do
    `N3Judge` recusa o cego que os preencha."""
    campos: dict[str, Any] = {
        "configuracao": configuracao,
        "causa_raiz_correta": conteudo["causa_raiz_correta"],
        "mencionou_limitacao_relevante": conteudo["mencionou_limitacao_relevante"],
        "responde_a_pergunta": conteudo["responde_a_pergunta"],
        "justificativa": conteudo["justificativa"],
        "judge_latencia_ms": latencia_ms,
    }
    if configuracao == "com_trace":
        campos["afirmacoes_sem_suporte"] = list(conteudo["afirmacoes_sem_suporte"])
        campos["contradiz_evidencia"] = conteudo["contradiz_evidencia"]
        campos["recomendou_acao_sem_base"] = conteudo["recomendou_acao_sem_base"]
    return N3Judge(**campos)
