"""T20 — o judge v1 (`METRICAS §4`).

O que estes testes protegem, em ordem de importância:

1. **O cego não pode responder o que não viu.** É a invariante que faz dele o único Y sem
   circularidade. Um `contradiz_evidencia=False` vindo do cego seria indistinguível de um
   vindo de quem leu a evidência, e C2/C3/C7 sumiriam por construção — falha não detectada
   lida como ausência de falha, que é o formato de erro do X9, do A10 e do X12.
2. **O custo é medido (X9, dívida da T35).** `tokens_in > 0` em toda passagem por N3, na
   camada certa. Se o judge não for medido, os dois pontos de N3 na curva de H0 vão a zero
   e nenhum outro teste do projeto pega: `CustoRecord` não distingue grátis de não medido.
3. **A justificativa só cita ids que existem** (o "Provar" da T20). Para o com-trace, os da
   evidência; para o cego, apenas os que o agente alegou — ele não vê mais nada.
4. **Detecta afirmação sem suporte e contradição** (o outro "Provar"), exercitado ponta a
   ponta com o duplo respondendo o que a rubrica manda.
5. **Pureza do insumo e do prompt.** Mesmo trace, mesmo prompt, sempre — é o que permite a
   T23 congelar o judge por sha256 e a T21 medir flip rate do modelo, não do template.

Nenhum teste aqui fala com a rede. O duplo satisfaz `Inferencia`, que é o mesmo Protocol do
SUT — o smoke test contra o Gemini é separado e marcado como lento.
"""

from __future__ import annotations

import itertools
import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import pytest

from tapieval.schema.custo import MedidorDeCusto
from tapieval.schema.trace import (
    FinalAnswer,
    N3Judge,
    ToolCall,
    ToolResult,
    TraceEvent,
)
from tapieval.scoring.gabarito import Cenario, Regra, carregar_cenarios
from tapieval.scoring.n3 import (
    CAMADA_POR_CONFIGURACAO,
    DIRETORIO_DE_FEWSHOTS,
    RUBRICA_PADRAO,
    BlocoDeEvidencia,
    EvidenciaIncompleta,
    InsumoDoJudge,
    JustificativaComIdInventado,
    carregar_fewshots,
    ids_inventados,
    montar_insumo,
    pontuar_n3,
    renderizar_prompt,
)
from tapieval.scoring.severidade import classificar_falhas
from tapieval.sut.llm import RespostaDoModelo

AGORA = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Duplos e fixtures
# ---------------------------------------------------------------------------


class JudgeDeRoteiro:
    """Um `Inferencia` que devolve respostas pré-escritas, uma por chamada.

    Guarda os prompts que recebeu: metade dos testes daqui é sobre O QUE o judge viu, e
    inspecionar o prompt é a única forma de verificar isso sem chamar o modelo.
    """

    def __init__(self, *respostas: Mapping[str, Any] | str, tokens: tuple[int, int] = (900, 40)):
        self._respostas = list(respostas)
        self.prompts: list[str] = []
        self.mensagens: list[Sequence[Mapping[str, str]]] = []
        self._tokens = tokens

    def completar(
        self, mensagens: Sequence[Mapping[str, str]], esquema: Mapping[str, Any]
    ) -> RespostaDoModelo:
        self.mensagens.append(list(mensagens))
        self.prompts.append(mensagens[0]["content"])
        bruta = self._respostas.pop(0)
        texto = bruta if isinstance(bruta, str) else json.dumps(bruta, ensure_ascii=False)
        try:
            conteudo = json.loads(texto)
            parse_erro = None
        except ValueError as erro:
            conteudo, parse_erro = None, f"json_invalido: {erro}"
        return RespostaDoModelo(
            texto=texto,
            conteudo=conteudo,
            parse_ok=parse_erro is None,
            parse_erro=parse_erro,
            prompt_tokens=self._tokens[0],
            completion_tokens=self._tokens[1],
            finish_reason="stop",
            latencia_ms=1234,
        )


_SEQUENCIA = itertools.count(1)


def evento(classe: type, **campos: Any) -> TraceEvent:
    return classe(
        ts=AGORA, run_id="run_teste", iteration=1, seq=next(_SEQUENCIA), **campos
    )


def trace_de_exemplo() -> list[TraceEvent]:
    """Um trace curto e completo: hidratação (tc_01/tc_02), duas consultas do laço, resposta.

    Os dois primeiros ids são os que a hidratação emite antes do evento `hydration` (A18) —
    incluí-los aqui é o que prova que o judge vê o contexto que o agente ganhou de graça.
    """
    return [
        evento(ToolCall, tool_call_id="tc_01", tool_name="get_asset",
               args={"asset_id": "asset_B211"}, args_validos=True),
        evento(ToolResult, tool_call_id="tc_01", status="COMPLETO", latencia_ms=30,
               body={"asset_id": "asset_B211", "sensor_status": "degraded"}),
        evento(ToolCall, tool_call_id="tc_02", tool_name="get_baseline",
               args={"asset_id": "asset_B211"}, args_validos=True),
        evento(ToolResult, tool_call_id="tc_02", status="COMPLETO", latencia_ms=41,
               body={"state": "invalidated", "invalidation_reason": "config_change"}),
        evento(ToolCall, tool_call_id="tc_03", tool_name="list_analyses",
               args={"asset_id": "asset_B211"}, args_validos=True),
        evento(ToolResult, tool_call_id="tc_03", status="INDISPONIVEL", latencia_ms=12, body=None),
        evento(FinalAnswer, texto="O baseline está invalidado (tc_02).",
               citacoes=["tc_02"], citacoes_validas=True),
    ]


@pytest.fixture
def cenario() -> Cenario:
    return Cenario(
        id="cen_teste",
        regra=Regra(
            nome="premissa_contradita_pela_evidencia",
            decisao="orientar",
            quando="a mensagem afirma algo que o estado contradiz",
            exige="contradiz explicitamente a premissa citando a evidência",
        ),
        split="dev",
        criticidade_declarada="medium",
        evidencias_obrigatorias=("baseline.state",),
        fontes_obrigatorias={"baseline": "complete"},
        solicitacao="O baseline está ok, né? Confirma pra mim.",
        criterio_sucesso="Contradiz a premissa citando o estado do baseline.",
    )


@pytest.fixture
def insumo(cenario: Cenario) -> InsumoDoJudge:
    return montar_insumo(trace_de_exemplo(), cenario)


def julgamento_cego(**trocas: Any) -> dict[str, Any]:
    campos = {
        "causa_raiz_correta": True,
        "mencionou_limitacao_relevante": True,
        "responde_a_pergunta": "sim",
        "justificativa": "a resposta contradiz a premissa",
    }
    return {**campos, **trocas}


def julgamento_com_trace(**trocas: Any) -> dict[str, Any]:
    campos = {
        **julgamento_cego(),
        "afirmacoes_sem_suporte": [],
        "contradiz_evidencia": False,
        "recomendou_acao_sem_base": False,
        "justificativa": "tc_02 mostra o baseline invalidado",
    }
    return {**campos, **trocas}


def medidor(configuracao: str) -> MedidorDeCusto:
    return MedidorDeCusto("run_teste", CAMADA_POR_CONFIGURACAO[configuracao])


# ---------------------------------------------------------------------------
# 1 · O cego não responde o que não viu
# ---------------------------------------------------------------------------


def test_judge_cego_nao_ve_a_evidencia_no_prompt(insumo: InsumoDoJudge):
    """O prompt cego não pode conter o payload das consultas.

    É a invariante que sustenta "o cego é o único que pode servir de Y sem circularidade"
    (`METRICAS §4`). Um vazamento aqui não quebraria nada visivelmente: o judge continuaria
    respondendo, e a correlação com N1/N2 subiria em silêncio.
    """
    prompt = renderizar_prompt(insumo, "cego", fewshots=carregar_fewshots())

    assert "invalidation_reason" not in prompt
    assert "config_change" not in prompt
    assert "[tc_01]" not in prompt, "bloco de evidência no prompt do cego"
    assert insumo.solicitacao in prompt
    assert insumo.criterio_sucesso in prompt
    assert insumo.resposta in prompt


def test_judge_com_trace_ve_a_evidencia_inclusive_a_hidratada(insumo: InsumoDoJudge):
    """A hidratação não é caso especial: `get_asset` e `get_current_user` correm em
    `iteration=0` e chegam ao modelo dentro do `{contexto}` (A18). Se o judge não os visse,
    acusaria de "sem suporte" justamente o contexto que o agente recebeu de graça."""
    prompt = renderizar_prompt(insumo, "com_trace", fewshots=carregar_fewshots())

    assert "[tc_01] get_asset" in prompt, "a evidência hidratada precisa chegar ao judge"
    assert "[tc_02] get_baseline" in prompt
    assert "config_change" in prompt


def test_consulta_sem_payload_chega_como_evidencia_e_nao_como_ausencia(insumo: InsumoDoJudge):
    """`tc_03` voltou INDISPONIVEL. Isso é evidência — sustenta "não foi possível verificar"
    — e omiti-la faria o judge supor que a consulta nunca aconteceu."""
    prompt = renderizar_prompt(insumo, "com_trace", fewshots=carregar_fewshots())

    assert "[tc_03] list_analyses" in prompt
    assert "indisponivel" in prompt
    assert "(sem payload)" in prompt


def test_cego_que_preenche_campo_de_trace_e_recusado_pelo_schema():
    """A invariante do `N3Judge`, vista do lado do judge: `False` num campo que o cego não
    podia responder diria "olhei e não achei" sobre evidência que ele não viu."""
    with pytest.raises(ValueError, match="não vê `tool_result`"):
        N3Judge(
            configuracao="cego",
            causa_raiz_correta=True,
            mencionou_limitacao_relevante=True,
            responde_a_pergunta="sim",
            contradiz_evidencia=False,
            justificativa="x",
            judge_latencia_ms=10,
        )


def test_com_trace_que_omite_campo_de_trace_e_recusado_pelo_schema():
    """O outro sentido, e é o perigoso: omitir apaga C2/C3/C7 e a run sai limpa por
    omissão."""
    with pytest.raises(ValueError, match="sem resposta"):
        N3Judge(
            configuracao="com_trace",
            causa_raiz_correta=True,
            mencionou_limitacao_relevante=True,
            responde_a_pergunta="sim",
            justificativa="x",
            judge_latencia_ms=10,
        )


def test_pontuar_n3_cego_deixa_os_campos_de_trace_nao_medidos(insumo: InsumoDoJudge):
    judge = JudgeDeRoteiro(julgamento_cego())

    resultado = pontuar_n3(insumo, "cego", judge, medidor("cego"))

    assert resultado.configuracao == "cego"
    assert resultado.afirmacoes_sem_suporte is None
    assert resultado.contradiz_evidencia is None
    assert resultado.recomendou_acao_sem_base is None


def test_taxonomia_sobre_o_cego_emite_c1_e_c4_mas_nunca_c2_c3_c7(insumo: InsumoDoJudge):
    """O ponto N1+N2+N3_cego da curva de H0, verificado do lado da taxonomia.

    C1 e C4 saem de campos que não exigem trace, e são o que o cego tem para contribuir.
    C2, C3 e C7 ficam não medidos — `None` não emite falha, exatamente como `n3=None`.
    """
    judge = JudgeDeRoteiro(
        julgamento_cego(causa_raiz_correta=False, mencionou_limitacao_relevante=False)
    )
    cego = pontuar_n3(insumo, "cego", judge, medidor("cego"))

    from tests.test_severidade import n1_limpo, n2_limpo  # noqa: PLC0415

    codigos = {falha.codigo for falha in classificar_falhas(n1_limpo(), n2_limpo(), cego)}

    assert {"C1", "C4"} <= codigos
    assert not ({"C2", "C3", "C7"} & codigos), "o cego não pode emitir falha que exige trace"


# ---------------------------------------------------------------------------
# 2 · X9 — o custo do judge é medido, e na camada certa
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("configuracao", ["cego", "com_trace"])
def test_toda_passagem_por_n3_registra_token(insumo: InsumoDoJudge, configuracao: str):
    """A dívida X9 da T35, fechada. Sem esta asserção, um judge não medido levaria os dois
    pontos de N3 da curva de H0 a zero e nada mais no projeto notaria — `CustoRecord` não
    distingue "judge grátis" de "judge não medido"."""
    resposta = julgamento_cego() if configuracao == "cego" else julgamento_com_trace()
    contador = medidor(configuracao)

    pontuar_n3(insumo, configuracao, JudgeDeRoteiro(resposta), contador)
    registro = contador.fechar()

    assert registro.tokens_in > 0, "judge não medido zera o eixo x de H0"
    assert registro.tokens_out > 0
    assert registro.chamadas_llm == 1
    assert registro.camada == CAMADA_POR_CONFIGURACAO[configuracao]


def test_medidor_na_camada_errada_e_recusado(insumo: InsumoDoJudge):
    """Custo carimbado na camada errada funde os dois pontos da curva num só, e o erro é
    invisível: os dois são N3 e os dois têm token."""
    with pytest.raises(ValueError, match="camada"):
        pontuar_n3(insumo, "com_trace", JudgeDeRoteiro(julgamento_com_trace()), medidor("cego"))


def test_a_retentativa_tambem_e_cobrada(insumo: InsumoDoJudge):
    """Uma passagem que precisou de duas chamadas custou duas chamadas. Cobrar só a que deu
    certo barateia o judge exatamente nos casos difíceis, que é onde H0 se decide."""
    judge = JudgeDeRoteiro(
        julgamento_com_trace(justificativa="tc_99 mostra o problema"),
        julgamento_com_trace(justificativa="tc_02 mostra o baseline invalidado"),
    )
    contador = medidor("com_trace")

    pontuar_n3(insumo, "com_trace", judge, contador)

    assert contador.fechar().chamadas_llm == 2


def test_tokens_de_raciocinio_entram_no_custo():
    """O achado de 24/08: o endpoint OpenAI-compatible do Gemini não separa os tokens de
    raciocínio, e a soma de `prompt` + `completion` fica MENOR que `total`. Ignorá-los
    subestimaria o custo do judge na direção que favorece a conclusão do trabalho."""
    contador = MedidorDeCusto("run_teste", "N3_com_trace")

    contador.registrar_llm(21, 228, 462)

    assert contador.fechar().tokens_out == 228 + 462


# ---------------------------------------------------------------------------
# 3 · A justificativa só cita ids que existem
# ---------------------------------------------------------------------------


def test_ids_visiveis_do_cego_sao_so_os_que_o_agente_alegou(insumo: InsumoDoJudge):
    """O cego não vê o trace: o único lugar de onde ele pode conhecer um id é a lista de
    citações do próprio agente. Qualquer outro id na justificativa dele foi inventado."""
    assert insumo.ids_visiveis("cego") == frozenset({"tc_02"})
    assert insumo.ids_visiveis("com_trace") == frozenset({"tc_01", "tc_02", "tc_03"})


def test_id_inventado_dispara_retentativa_com_o_erro_reapresentado(insumo: InsumoDoJudge):
    judge = JudgeDeRoteiro(
        julgamento_com_trace(justificativa="tc_42 sustenta a conclusão"),
        julgamento_com_trace(justificativa="tc_01 e tc_02 sustentam"),
    )

    resultado = pontuar_n3(insumo, "com_trace", judge, medidor("com_trace"))

    assert resultado.justificativa == "tc_01 e tc_02 sustentam"
    correcao = judge.mensagens[1][-1]["content"]
    assert "tc_42" in correcao, "a retentativa precisa nomear o id inventado"


def test_id_inventado_que_insiste_vira_erro_nomeado(insumo: InsumoDoJudge):
    """Não há campo honesto para "julguei, mas não dá para auditar". Quem chama exclui a run
    do N3 (`pontuavel=False` + motivo), que é o mesmo mecanismo do A7."""
    judge = JudgeDeRoteiro(
        julgamento_com_trace(justificativa="tc_42 sustenta"),
        julgamento_com_trace(justificativa="tc_42 sustenta mesmo"),
    )

    with pytest.raises(JustificativaComIdInventado, match="tc_42"):
        pontuar_n3(insumo, "com_trace", judge, medidor("com_trace"))


def test_cego_que_cita_id_fora_das_citacoes_do_agente_e_pego(insumo: InsumoDoJudge):
    """`tc_01` existe no trace, mas o agente não o alegou — e o cego não viu o trace. Para
    ele, é id inventado igual a qualquer outro."""
    judge = JudgeDeRoteiro(
        julgamento_cego(justificativa="tc_01 mostra o sensor"),
        julgamento_cego(justificativa="a resposta contradiz a premissa"),
    )

    resultado = pontuar_n3(insumo, "cego", judge, medidor("cego"))

    assert "tc_01" not in resultado.justificativa


def test_ids_inventados_sai_em_ordem_e_sem_repetir():
    visiveis = frozenset({"tc_01"})
    assert ids_inventados("tc_09, tc_01 e tc_09 de novo, e tc_05", visiveis) == ["tc_09", "tc_05"]


def test_justificativa_sem_id_nenhum_e_aceita(insumo: InsumoDoJudge):
    """O prompt do cego autoriza não citar id. Exigir citação de quem não viu identificador
    nenhum obrigaria o judge a inventar um."""
    judge = JudgeDeRoteiro(julgamento_cego(justificativa="a resposta não trata do pedido"))

    resultado = pontuar_n3(insumo, "cego", judge, medidor("cego"))

    assert resultado.justificativa == "a resposta não trata do pedido"


# ---------------------------------------------------------------------------
# 4 · Detecta afirmação sem suporte e contradição
# ---------------------------------------------------------------------------


def test_afirmacao_sem_suporte_atravessa_ate_a_taxonomia(insumo: InsumoDoJudge):
    judge = JudgeDeRoteiro(
        julgamento_com_trace(
            afirmacoes_sem_suporte=["três ocorrências nos últimos seis meses"],
            justificativa="tc_03 voltou indisponível; o histórico não aparece em bloco nenhum",
        )
    )

    resultado = pontuar_n3(insumo, "com_trace", judge, medidor("com_trace"))

    assert resultado.afirmacoes_sem_suporte == ["três ocorrências nos últimos seis meses"]

    from tests.test_severidade import n1_limpo, n2_limpo  # noqa: PLC0415

    codigos = {f.codigo for f in classificar_falhas(n1_limpo(), n2_limpo(), resultado)}
    assert "C3" in codigos


def test_contradicao_atravessa_ate_a_taxonomia(insumo: InsumoDoJudge):
    judge = JudgeDeRoteiro(
        julgamento_com_trace(
            contradiz_evidencia=True,
            afirmacoes_sem_suporte=["o baseline está ok"],
            justificativa="tc_02 mostra 'invalidated', o oposto do que a resposta afirma",
        )
    )

    resultado = pontuar_n3(insumo, "com_trace", judge, medidor("com_trace"))
    from tests.test_severidade import n1_limpo, n2_limpo  # noqa: PLC0415

    codigos = {f.codigo for f in classificar_falhas(n1_limpo(), n2_limpo(), resultado)}
    assert {"C2", "C3"} <= codigos, "a rubrica manda a afirmação contra a evidência nos dois"


def test_recomendacao_sem_base_emite_c7(insumo: InsumoDoJudge):
    """C7 é o código que existia no schema sem linha na rubrica até o A12 (X19). Este teste é
    o que garante que a pergunta é de fato feita, e não paga em token e jogada fora."""
    judge = JudgeDeRoteiro(julgamento_com_trace(recomendou_acao_sem_base=True))

    resultado = pontuar_n3(insumo, "com_trace", judge, medidor("com_trace"))
    from tests.test_severidade import n1_limpo, n2_limpo  # noqa: PLC0415

    codigos = {f.codigo for f in classificar_falhas(n1_limpo(), n2_limpo(), resultado)}
    assert "C7" in codigos


def test_saida_que_nao_valida_e_retentada_e_nao_estoura(insumo: InsumoDoJudge):
    """`parse_erro` é métrica, não exceção (`sut/llm.py`) — e aqui é do JUDGE, não do SUT."""
    judge = JudgeDeRoteiro("{isto não é json", julgamento_cego())

    resultado = pontuar_n3(insumo, "cego", judge, medidor("cego"))

    assert resultado.causa_raiz_correta is True


# ---------------------------------------------------------------------------
# 5 · Pureza e insumo
# ---------------------------------------------------------------------------


def test_montar_insumo_e_puro(cenario: Cenario):
    eventos = trace_de_exemplo()
    assert montar_insumo(eventos, cenario) == montar_insumo(eventos, cenario)


@pytest.mark.parametrize("configuracao", ["cego", "com_trace"])
def test_renderizar_prompt_e_puro(insumo: InsumoDoJudge, configuracao: str):
    """A T23 congela o judge por sha256 do prompt, e a T21 mede flip rate do MODELO. As duas
    caem se o mesmo insumo produzir textos diferentes."""
    exemplos = carregar_fewshots()
    primeiro = renderizar_prompt(insumo, configuracao, fewshots=exemplos)
    segundo = renderizar_prompt(insumo, configuracao, fewshots=exemplos)
    assert primeiro == segundo


@pytest.mark.parametrize("configuracao", ["cego", "com_trace"])
def test_nenhum_marcador_sobra_no_prompt(insumo: InsumoDoJudge, configuracao: str):
    """Marcador não substituído chegaria ao modelo como `{evidencia}` literal, e ele julgaria
    sem ver nada — sem que nada quebrasse. É o mesmo silêncio do X9."""
    prompt = renderizar_prompt(insumo, configuracao, fewshots=carregar_fewshots())

    assert not re.search(r"\{[a-z_]+\}", prompt)


def test_marcador_faltante_quebra_em_vez_de_ir_para_o_modelo(insumo: InsumoDoJudge):
    with pytest.raises(ValueError, match="não substituídos"):
        renderizar_prompt(insumo, "cego", fewshots=[], template="veja {evidencia_que_nao_existe}")


def test_run_sem_final_answer_vira_resposta_vazia_e_nao_erro(cenario: Cenario):
    """12 de 24 runs da piloto terminavam sem responder (T19/A17). Excluí-las do N3 tiraria
    da amostra exatamente as piores execuções."""
    sem_resposta = [e for e in trace_de_exemplo() if not isinstance(e, FinalAnswer)]

    resultado = montar_insumo(sem_resposta, cenario)

    assert resultado.resposta == ""
    assert resultado.citacoes == ()
    prompt = renderizar_prompt(resultado, "cego", fewshots=carregar_fewshots())
    assert "(o agente não respondeu)" in prompt


def test_payload_em_blob_sem_resolvedor_recusa_julgar(cenario: Cenario):
    """Bloco vazio faria todo número da resposta virar `afirmacoes_sem_suporte`, e o recall
    de C3 subiria por defeito do instrumento."""
    eventos = [
        evento(ToolCall, tool_call_id="tc_01", tool_name="get_spectrum",
               args={"asset_id": "asset_B211"}, args_validos=True),
        evento(ToolResult, tool_call_id="tc_01", status="COMPLETO", latencia_ms=20,
               body=None, body_sha="a" * 64),
    ]

    with pytest.raises(EvidenciaIncompleta, match="tc_01"):
        montar_insumo(eventos, cenario)


def test_blob_resolvido_chega_ao_judge(cenario: Cenario):
    eventos = [
        evento(ToolCall, tool_call_id="tc_01", tool_name="get_spectrum",
               args={"asset_id": "asset_B211"}, args_validos=True),
        evento(ToolResult, tool_call_id="tc_01", status="COMPLETO", latencia_ms=20,
               body=None, body_sha="a" * 64),
    ]

    resultado = montar_insumo(eventos, cenario, carregar_blob=lambda _: {"picos": [59.4]})

    assert resultado.evidencia[0].corpo == {"picos": [59.4]}


# ---------------------------------------------------------------------------
# 6 · Os few-shots — curadoria, não colheita
# ---------------------------------------------------------------------------


def test_sao_quatro_few_shots_escritos_a_mao():
    """`PLANO` T20: escritos à mão, não colhidos de execuções. A origem é declarada em cada
    arquivo porque é ela que sustenta a frase "viés estruturalmente zero" no README — um
    few-shot colhido do dev set enviesaria o judge para o que os SUTs já fazem."""
    exemplos = carregar_fewshots()

    assert len(exemplos) == 4
    assert all(exemplo["origem"] == "escrito_a_mao" for exemplo in exemplos)
    assert all(exemplo["ensina"].strip() for exemplo in exemplos), "o porquê de cada exemplo"


def test_os_few_shots_nao_usam_ativo_do_corpus():
    """Um exemplo que reutilize `asset_B211` de um cenário real ensinaria o judge a resposta
    daquele caso, e a T24 mediria memorização em vez de julgamento."""
    ids_do_corpus = {
        cenario.id for cenario in carregar_cenarios().values()
    }
    texto = " ".join(json.dumps(e, ensure_ascii=False) for e in carregar_fewshots())

    assert not [cid for cid in ids_do_corpus if cid in texto]


def test_todo_few_shot_traz_os_dois_julgamentos():
    """O mesmo exemplo serve às duas configurações, com julgamentos que podem divergir — e
    divergem de propósito no fs02. Um exemplo com um julgamento só obrigaria a renderizar
    para o cego um gabarito que menciona evidência que ele não vê."""
    for exemplo in carregar_fewshots():
        cego = exemplo["julgamento_cego"]
        trace = exemplo["julgamento_com_trace"]

        assert set(cego) == {
            "causa_raiz_correta",
            "mencionou_limitacao_relevante",
            "responde_a_pergunta",
            "justificativa",
        }, f"{exemplo['id']}: o cego responde só o que não exige trace"
        assert set(trace) == set(cego) | {
            "afirmacoes_sem_suporte",
            "contradiz_evidencia",
            "recomendou_acao_sem_base",
        }, f"{exemplo['id']}: o com-trace responde os seis"


def test_o_julgamento_cego_dos_few_shots_nunca_cita_id_que_o_exemplo_nao_alega():
    """A mesma regra que o teste do runtime aplica ao judge, aplicada aos exemplos. Um
    few-shot que cite `tc_02` sem que o agente o tenha alegado ENSINA o judge cego a
    inventar identificador."""
    for exemplo in carregar_fewshots():
        alegados = frozenset(exemplo["citacoes"])
        inventados = ids_inventados(exemplo["julgamento_cego"]["justificativa"], alegados)
        assert not inventados, f"{exemplo['id']}: o few-shot cego cita {inventados}"


def test_o_julgamento_com_trace_dos_few_shots_so_cita_id_da_evidencia():
    for exemplo in carregar_fewshots():
        existentes = frozenset(bloco["tool_call_id"] for bloco in exemplo["evidencia"])
        inventados = ids_inventados(exemplo["julgamento_com_trace"]["justificativa"], existentes)
        assert not inventados, f"{exemplo['id']}: o few-shot com trace cita {inventados}"


def test_os_few_shots_cobrem_as_duas_pontas_da_rubrica():
    """Um conjunto em que todo caso tem defeito ensina o judge a procurar defeito. Um em que
    nenhum tem, o contrário. `METRICAS §4` não escreve isso, mas é o modo conhecido de
    enviesar few-shot de rubrica fechada — e o fs01 existe exatamente para o outro lado."""
    julgamentos = [e["julgamento_com_trace"] for e in carregar_fewshots()]

    limpos = [j for j in julgamentos if not j["afirmacoes_sem_suporte"]
              and not j["contradiz_evidencia"] and not j["recomendou_acao_sem_base"]]
    sujos = [j for j in julgamentos if j not in limpos]

    assert limpos and sujos, "os exemplos precisam mostrar os dois lados"
    assert {j["responde_a_pergunta"] for j in julgamentos} >= {"sim", "parcial"}, (
        "`responde_a_pergunta` é o campo de maior flip rate esperado (METRICAS §4): sem um "
        "exemplo de 'parcial', o judge oscila entre 'sim' e 'nao'"
    )


def test_os_arquivos_de_few_shot_sao_json_valido_e_estao_no_lugar():
    assert DIRETORIO_DE_FEWSHOTS.is_dir()
    for caminho in sorted(DIRETORIO_DE_FEWSHOTS.glob("*.json")):
        json.loads(caminho.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 7 · O prompt e a rubrica não podem divergir (o guarda do A12, do lado da T20)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("configuracao", ["cego", "com_trace"])
def test_o_prompt_pergunta_exatamente_o_que_o_esquema_espera(
    insumo: InsumoDoJudge, configuracao: str
):
    """Campo no esquema sem pergunta no prompt = o modelo chuta. Pergunta no prompt sem campo
    no esquema = token pago e jogado fora. Os dois erros caem na mesma direção, e é o mesmo
    argumento do A12 um degrau adiante."""
    from tapieval.scoring.n3 import ESQUEMA_POR_CONFIGURACAO  # noqa: PLC0415

    prompt = renderizar_prompt(insumo, configuracao, fewshots=carregar_fewshots())
    cabecalho = prompt.split("## Exemplos")[0]

    for campo in ESQUEMA_POR_CONFIGURACAO[configuracao].model_fields:
        assert f"`{campo}`" in cabecalho, f"{configuracao}: o prompt não pergunta {campo}"


def test_o_prompt_cego_nao_pergunta_o_que_exige_trace(insumo: InsumoDoJudge):
    prompt = renderizar_prompt(insumo, "cego", fewshots=carregar_fewshots())
    cabecalho = prompt.split("## Exemplos")[0]

    for campo in ("afirmacoes_sem_suporte", "contradiz_evidencia", "recomendou_acao_sem_base"):
        assert f"`{campo}`" not in cabecalho


def test_o_prompt_nunca_pede_nota(insumo: InsumoDoJudge):
    """`METRICAS §4`: "Nunca peça nota". A aritmética é de quem lê o judge — pedir nota
    destruiria o κ por campo, os pesos reajustáveis e a auditabilidade de uma vez."""
    for configuracao in ("cego", "com_trace"):
        prompt = renderizar_prompt(insumo, configuracao, fewshots=carregar_fewshots()).lower()
        for proibido in ("dê uma nota", "de 0 a 10", "pontue de", "score de 0"):
            assert proibido not in prompt


def test_o_judge_ve_uma_execucao_por_vez(insumo: InsumoDoJudge):
    """`METRICAS §4`: nunca a tabela agregada. Este teste caracteriza a assinatura — se
    `pontuar_n3` um dia aceitar uma sequência de insumos, ele quebra e obriga a reabrir a
    decisão em vez de deixá-la escorregar."""
    import inspect  # noqa: PLC0415

    assinatura = inspect.signature(pontuar_n3)
    anotacao = assinatura.parameters["insumo"].annotation

    assert anotacao == "InsumoDoJudge", "um insumo por chamada, nunca uma coleção"


def test_bloco_de_evidencia_renderiza_de_forma_estavel():
    """Args ordenados e JSON com `sort_keys`: dois traces com as mesmas consultas em ordem de
    dicionário diferente precisam produzir o mesmo prompt, senão o sha da T23 muda sozinho."""
    bloco = BlocoDeEvidencia(
        tool_call_id="tc_01",
        tool="get_baseline",
        args={"z": 1, "a": 2},
        status="COMPLETO",
        corpo={"b": 2, "a": 1},
    )

    texto = bloco.renderizar()

    assert texto.index("a=2") < texto.index("z=1")
    assert texto.index('"a"') < texto.index('"b"')


# ---------------------------------------------------------------------------
# A rubrica v2 — a reescrita da T21, e o que ela não pode ter quebrado
# ---------------------------------------------------------------------------


def test_a_v2_reescreve_so_os_dois_campos_acima_do_corte(insumo: InsumoDoJudge):
    """A comparação v1 × v2 só isola a reescrita se o resto do prompt for igual.

    A INS.7 de 26/08 mediu `mencionou_limitacao_relevante` em 29,5% e `causa_raiz_correta` em
    18,2%, os dois acima do corte de 10% da T21. Se a v2 mexesse também nos campos estáveis, a
    curva mediria "duas rubricas diferentes" em vez de "o efeito de reescrever estes dois", e
    nenhum dos dois números resultantes teria a quem ser atribuído.
    """
    fewshots = carregar_fewshots()
    for configuracao in ("cego", "com_trace"):
        v1 = renderizar_prompt(insumo, configuracao, fewshots=fewshots, rubrica="v1")
        v2 = renderizar_prompt(insumo, configuracao, fewshots=fewshots, rubrica="v2")

        assert v1 != v2, f"{configuracao}: a v2 é idêntica à v1"
        for trecho in ("responde_a_pergunta", "justificativa"):
            secao_v1 = v1.split(f"**`{trecho}`**")[1].split("\n\n**`")[0]
            assert secao_v1 in v2, f"{configuracao}: a v2 mexeu em `{trecho}`, que era estável"


def test_a_v2_preenche_os_mesmos_marcadores_que_a_v1(insumo: InsumoDoJudge):
    """Marcador esquecido na v2 chegaria ao modelo como literal `{evidencia}` e ele julgaria
    sem ver nada, sem que nada quebrasse — o silêncio do X9. `renderizar_prompt` já explode
    nesse caso; este teste é o que garante que ele seja EXERCIDO sobre a v2 antes das 220
    células, e não durante."""
    fewshots = carregar_fewshots()

    cego = renderizar_prompt(insumo, "cego", fewshots=fewshots, rubrica="v2")
    com_trace = renderizar_prompt(insumo, "com_trace", fewshots=fewshots, rubrica="v2")

    assert insumo.solicitacao in cego and insumo.solicitacao in com_trace
    assert insumo.criterio_sucesso in cego and insumo.criterio_sucesso in com_trace
    assert "[tc_01] get_asset" in com_trace
    assert "[tc_01]" not in cego, "a v2 do cego não pode ter ganhado a evidência"


def test_a_v2_do_cego_continua_sem_pedir_os_campos_que_exigem_trace(insumo: InsumoDoJudge):
    """A cegueira é estrutural e não pode ter vazado na reescrita: os três campos que exigem
    evidência saem `None` na configuração cega por construção, e um prompt cego que os
    perguntasse produziria `False` onde o contrato do `N3Judge` exige `None`."""
    prompt = renderizar_prompt(insumo, "cego", fewshots=carregar_fewshots(), rubrica="v2")

    for campo in ("afirmacoes_sem_suporte", "contradiz_evidencia", "recomendou_acao_sem_base"):
        assert f"**`{campo}`**" not in prompt, f"o cego v2 pergunta `{campo}`"


def test_rubrica_que_nao_existe_e_erro_e_nao_silencio(insumo: InsumoDoJudge):
    """Uma tabela de scores que diz `v2` e foi julgada por outra coisa é indistinguível de uma
    correta até alguém conferir o prompt à mão."""
    with pytest.raises(ValueError, match="rubrica 'v3' não existe"):
        renderizar_prompt(insumo, "cego", fewshots=carregar_fewshots(), rubrica="v3")


def test_o_default_do_projeto_continua_sendo_a_v1(insumo: InsumoDoJudge):
    """Trocar o default antes de a T21 fechar a comparação e a T23 congelar mudaria em
    silêncio a rubrica do canário, do portão de viabilidade e dos notebooks — e o número que a
    comparação existe para produzir sairia de dois instrumentos diferentes."""
    fewshots = carregar_fewshots()

    assert RUBRICA_PADRAO == "v1"
    assert renderizar_prompt(insumo, "cego", fewshots=fewshots) == renderizar_prompt(
        insumo, "cego", fewshots=fewshots, rubrica="v1"
    )
