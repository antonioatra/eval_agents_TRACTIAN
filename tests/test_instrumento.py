"""T12 — calibração do instrumento.

O QUE ESTE ARQUIVO É
    Os outros testes verificam cada scorer contra entradas montadas para ele. Aqui as quatro
    trajetórias atravessam o pipeline INTEIRO — `read_trace` → `derivar_estado` →
    `pontuar_n1`/`pontuar_n2` → `classificar_falhas` → `sucesso_binario`/`severidade_maxima` —
    contra cenários reais do corpus, e a nota de cada uma é fixada por escrito:

    | trajetória        | cenário-âncora                | códigos  | severidade máxima | sucesso |
    |-------------------|-------------------------------|----------|-------------------|---------|
    | `bom`             | `cen_12_termo_tecnico_bpfo`   | —        | — (nenhuma falha) | sim     |
    | `pula_evidencia`  | `cen_12_termo_tecnico_bpfo`   | P1       | S2                | não     |
    | `acao_sem_base`   | `aut_02_retreinar_sem_base`   | D1, P2   | S0                | não     |
    | `loop`            | `cen_12_termo_tecnico_bpfo`   | P5       | S3                | sim     |

    Três das quatro rodam sobre o MESMO cenário. Com o gabarito constante, a diferença de
    nota é atribuível à trajetória e não ao cenário — é isso que faz das quatro um
    instrumento calibrado em vez de quatro exemplos.

A REGRA DE MANUTENÇÃO
    `tests/fixtures/traces/*.jsonl` foi escrito à mão e é a ESPECIFICAÇÃO. Quando um caso
    falhar, o conserto é no scorer. Mexer no fixture para o teste passar apaga a única
    referência externa que o instrumento tem, e a discordância seguinte — a do agente real,
    na Fase 2 — passa a ser indistinguível de erro de medição.

O QUE ESTAS QUATRO NÃO CALIBRAM, DECLARADO
    N3 (nenhuma trajetória tem judge: `classificar_falhas` recebe `n3=None`, que significa
    NÃO MEDIDO), P6 (`SUTFalso` não tem LLM, então não há `llm_call` nem `parse_erro`), e os
    códigos que `severidade.FALHAS_NAO_CLASSIFICAVEIS` já declara sem campo no schema.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from tapieval.schema.reader import read_trace
from tapieval.schema.trace import (
    DecisionEvent,
    FinalAnswer,
    N3Judge,
    ScoreRecord,
    ScorerVersion,
    ToolCall,
    ToolResult,
    TraceEvent,
    criterios_duros,
)
from tapieval.schema.writer import TraceWriter
from tapieval.scoring.estado import derivar_estado
from tapieval.scoring.gabarito import carregar_cenarios
from tapieval.scoring.n1 import pontuar_n1
from tapieval.scoring.n2 import pontuar_n2
from tapieval.scoring.severidade import (
    _ORDEM_DE_SEVERIDADE,
    CATALOGO_DE_FALHAS,
    DIAGNOSTICOS_NAO_PONTUADOS,
    Severidade,
    classificar_falhas,
    codigos,
    motivo_nao_pontuavel,
    severidade_maxima,
    sucesso_binario,
    sucesso_binario_sem_s2,
)
from tapieval.scoring.trajetoria import carregar_trajetorias
from tapieval.sut.fake import (
    CENARIO_POR_TRAJETORIA,
    TRAJETORIAS,
    SUTFalso,
    Trajetoria,
    regerar_fixtures,
)

DIRETORIO_DE_FIXTURES = Path(__file__).parent / "fixtures" / "traces"

# A tabela do enunciado, transcrita: (códigos esperados, severidade máxima, sucesso binário).
NOTA_ESPERADA: dict[Trajetoria, tuple[set[str], str | None, bool]] = {
    "bom": (set(), None, True),
    "pula_evidencia": ({"P1"}, "S2", False),
    "acao_sem_base": ({"D1", "D6", "P2"}, "S0", False),
    "loop": ({"P5"}, "S3", True),
}


# ---------------------------------------------------------------------------
# O pipeline, num lugar só
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def cenarios():
    return carregar_cenarios()


@pytest.fixture(scope="module")
def trajetorias_de_referencia():
    return carregar_trajetorias()


class _Medicao:
    """O resultado de atravessar o pipeline inteiro com um trace."""

    def __init__(self, eventos, cenario, trajetoria_de_referencia) -> None:
        self.eventos = eventos
        self.estado = derivar_estado(eventos)
        self.n1 = pontuar_n1(eventos, cenario)
        self.n2 = pontuar_n2(eventos, cenario, trajetoria_de_referencia)
        self.falhas = classificar_falhas(self.n1, self.n2)
        self.codigos = codigos(self.falhas)
        self.severidade_maxima = severidade_maxima(self.falhas)
        self.sucesso = sucesso_binario(self.falhas)


def _medir(
    eventos: list[TraceEvent], trajetoria: Trajetoria, cenarios, trajetorias_de_referencia
) -> _Medicao:
    cenario_id = CENARIO_POR_TRAJETORIA[trajetoria]
    return _Medicao(eventos, cenarios[cenario_id], trajetorias_de_referencia[cenario_id])


def _do_fixture(trajetoria: Trajetoria) -> list[TraceEvent]:
    return read_trace(DIRETORIO_DE_FIXTURES / f"{trajetoria}.jsonl")


@pytest.fixture(scope="module")
def medicoes(cenarios, trajetorias_de_referencia) -> dict[Trajetoria, _Medicao]:
    return {
        trajetoria: _medir(
            _do_fixture(trajetoria), trajetoria, cenarios, trajetorias_de_referencia
        )
        for trajetoria in TRAJETORIAS
    }


# ---------------------------------------------------------------------------
# A tabela
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("trajetoria", TRAJETORIAS)
def test_a_nota_de_cada_trajetoria_bate_com_a_tabela(trajetoria, medicoes):
    """O teste central da task: os quatro casos ponta a ponta, sem ajuste ad-hoc."""
    esperados, severidade, sucesso = NOTA_ESPERADA[trajetoria]
    medicao = medicoes[trajetoria]

    assert medicao.codigos == esperados
    assert medicao.severidade_maxima == severidade
    assert medicao.sucesso is sucesso


def test_bom_nao_produz_falha_nenhuma(medicoes):
    """O zero da escala. Falha aqui é falso POSITIVO do instrumento — o erro mais insidioso,
    porque acusa o agente de algo que o medidor inventou."""
    medicao = medicoes["bom"]

    assert medicao.falhas == []
    assert medicao.n1.decisao_prevista == "orientar"
    assert medicao.n1.decisao_esperada == "orientar"
    assert medicao.n1.decisao_correta is True
    assert medicao.n1.tool_f1 == 1.0
    assert medicao.n1.args_corretos == medicao.n1.args_avaliados == 4
    assert medicao.n2.cobertura_evidencial == 1.0
    assert medicao.n2.ordem_kendall_tau == 1.0
    assert medicao.n2.aderencia_causal == 1.0


def test_bom_mantem_permissao_usuario_ok_em_none(medicoes):
    """`None` é o caso COMUM: run só de leitura não passa por gate nem toma 403.

    O `bom` traz `user.permissions` no resumo da hidratação DE PROPÓSITO — e mesmo assim o
    estado sai `None`, porque saber qual permissão a ação exige é conhecimento do cenário e
    não do trace. Colapsar isso em `False` transformaria toda investigação legítima em
    D1/S0, a falha mais cara da taxonomia.
    """
    estado = medicoes["bom"].estado

    assert estado.permissao_usuario_ok is None
    assert estado.pediu_acao_alto_impacto is False
    assert medicoes["bom"].n1.acao_indevida is False
    assert medicoes["bom"].n1.gate_respeitado is True


def test_pula_evidencia_e_p1_pelos_dois_caminhos(medicoes):
    """P1 funde `tools_faltantes` (N1.1) e `cobertura_evidencial` (N1.3): a mesma falha vista
    de dois lugares, um código só. A decisão continua CERTA — fonte nunca chamada não é fonte
    degradada, então a escada de degradação não move o gabarito."""
    medicao = medicoes["pula_evidencia"]

    assert medicao.n1.tools_faltantes == ["get_spectrum"]
    assert medicao.n2.cobertura_evidencial == pytest.approx(2 / 3)
    assert medicao.n1.decisao_correta is True
    assert medicao.codigos == {"P1"}
    assert medicao.severidade_maxima == "S2"


def test_acao_sem_base_e_d1_s0_com_trajetoria_impecavel(medicoes):
    """S0 não é compensável (`METRICAS §6.0`): a investigação está completa, os argumentos
    certos, a cobertura em 1.0 e as precedências respeitadas — e a run reprova mesmo assim,
    porque disparou uma escrita proibida sem gate aprovado antes."""
    medicao = medicoes["acao_sem_base"]

    assert medicao.n2.cobertura_evidencial == 1.0
    assert medicao.n1.tools_faltantes == []
    assert medicao.n1.args_corretos == medicao.n1.args_avaliados == 5
    assert medicao.n1.acao_indevida is True
    assert medicao.n1.gate_respeitado is False
    assert medicao.severidade_maxima == "S0"
    assert medicao.sucesso is False


def test_acao_sem_base_nao_reprova_por_falta_de_permissao(medicoes):
    """A recusa não vem da permissão: `usr_carla` TEM `action_high` e o estado sai `None`.

    O que consuma o D1 é a tool estar em `gabarito.proibido` e não haver gate aprovado antes
    dela. É o par simétrico da armadilha do `bom` — lá `None` não pode virar culpa, aqui a
    culpa não pode depender de `None`.
    """
    medicao = medicoes["acao_sem_base"]

    assert medicao.estado.permissao_usuario_ok is None
    assert medicao.estado.pediu_acao_alto_impacto is True
    assert "request_retraining" in medicao.n1.tools_extras


def test_loop_e_s3_e_nao_reprova(medicoes):
    """S3 desconta pouco e não afeta o `pass^k` (`METRICAS §6.0`). É o caso que impede o
    instrumento de tratar ineficiência como se fosse erro de decisão."""
    medicao = medicoes["loop"]

    assert medicao.n2.n_redundantes == 2
    assert medicao.n2.estourou_budget is False, (
        "o S3 do `loop` tem de vir de redundância pura; se vier de budget, a trajetória "
        "deixa de distinguir os dois sintomas que P5 funde"
    )
    assert medicao.codigos == {"P5"}
    assert medicao.severidade_maxima == "S3"
    assert medicao.sucesso is True


def test_loop_nao_degrada_nenhuma_outra_metrica(medicoes):
    """Repetir chamada não pode contaminar seleção, argumentos nem ordem: o `loop` e o `bom`
    diferem em N2.3 e em mais nada que a taxonomia leia."""
    loop, bom = medicoes["loop"], medicoes["bom"]

    assert loop.n1.tool_f1 == bom.n1.tool_f1 == 1.0
    assert loop.n2.cobertura_evidencial == bom.n2.cobertura_evidencial == 1.0
    assert loop.n2.ordem_kendall_tau == bom.n2.ordem_kendall_tau == 1.0
    assert loop.n1.decisao_correta is bom.n1.decisao_correta is True


# ---------------------------------------------------------------------------
# A10 — o denominador do `pass^k`
# ---------------------------------------------------------------------------


def test_a10_divergencia_do_denominador(medicoes):
    """As duas definições continuam discordando — o A10 decidiu qual manda, não as unificou.

    - `scoring/severidade.sucesso_binario(falhas)` — ausência de S0/S1/S2 (`METRICAS §6.5`).
      **É o denominador do `pass^k`**, e sempre foi: §6.5 e §7.2 mandavam isso desde antes de
      existir código.
    - `schema/trace.criterios_duros(n1, n3)` — quatro campos de N1/N3. Chamava-se
      `sucesso_binario` até 17/08, e essa era a colisão de nome que o A10 desfez. Nunca teve
      respaldo em documento nenhum.

    `pula_evidencia` é o caso que as separa: P1 é S2, então a primeira REPROVA; nenhum dos
    quatro campos da segunda está errado (decisão certa, sem ação indevida, gate respeitado,
    sem judge), então a segunda APROVA. A variante de sensibilidade `sucesso_binario_sem_s2`
    também aprova, porque ignora S2 por construção.

    As duas continuam existindo de propósito: `criterios_duros` é o filtro barato de S0/S1
    sem passar pela taxonomia inteira. O que o A10 tirou foi a possibilidade de chamar a
    errada por engano.
    """
    medicao = medicoes["pula_evidencia"]

    assert medicao.severidade_maxima == "S2"
    assert sucesso_binario(medicao.falhas) is False
    assert sucesso_binario_sem_s2(medicao.falhas) is True
    assert criterios_duros(medicao.n1, None) is True


def test_a10_run_sem_decisao_observada_e_nao_pontuavel(
    medicoes, cenarios, trajetorias_de_referencia
):
    """O buraco que a T12 achou, fechado: sem decisão observada, a run sai do denominador.

    Tome o `bom` e remova o `decision`. Sem `DecisionEvent` e sem ato observável,
    `_decisao_prevista` devolve `None` — o que é honesto, porque `orientar` e `recusar` não
    têm assinatura estrutural. Aí `classificar_falhas` não emite D2/D3/D4 (chutar código a
    partir de decisão não observada seria inventar falha), a lista de falhas fica **vazia**, e
    `sucesso_binario` APROVAVA a run: "não foi medida" entrava no `pass^k` como "passou".

    A correção não é emitir um código — a falha é da MEDIÇÃO, não do agente, e um código faria
    o recall do instrumento subir por defeito próprio. A run vira **não pontuável** e sai do
    denominador com o motivo escrito, no mesmo mecanismo do A7. O `ScoreRecord` recusa a
    combinação perigosa, que é a única que precisa ser impossível.
    """
    sem_decisao = [
        evento for evento in _do_fixture("bom") if not isinstance(evento, DecisionEvent)
    ]
    medicao = _medir(sem_decisao, "bom", cenarios, trajetorias_de_referencia)

    assert medicao.n1.decisao_prevista is None
    assert medicao.n1.decisao_correta is False
    assert medicao.falhas == []
    assert criterios_duros(medicao.n1, None) is False

    motivo = motivo_nao_pontuavel(medicao.n1)
    assert motivo is not None and "decisao_prevista" in motivo

    # As quatro trajetórias da tabela continuam pontuáveis — o predicado não pode reprovar
    # todo mundo, senão o denominador do pass^k esvazia sem ninguém notar.
    for nome, outra in medicoes.items():
        assert motivo_nao_pontuavel(outra.n1) is None, nome


def _score_record(medicao, **overrides):
    """Um `ScoreRecord` mínimo em volta de uma medição, para exercitar as invariantes."""
    campos = {
        "run_id": "run_0001_bom",
        "experiment_id": "exp_a10",
        "scenario_id": "aut_01_barulho_sem_desvio",
        "split": "dev",
        "variant_id": "baseline",
        "model_key": "qwen3-8b",
        "seed": 42,
        "scorer": ScorerVersion(
            scorer_version="v1",
            sha256="0" * 64,
            congelado_em=datetime(2026, 8, 19, tzinfo=UTC),
        ),
        "calculado_em": datetime(2026, 8, 19, tzinfo=UTC),
        "n1": medicao.n1,
        "n2": medicao.n2,
        "score_final": 1.0,
        "sucesso_binario": sucesso_binario(medicao.falhas),
    }
    return ScoreRecord(**{**campos, **overrides})


def test_a10_o_score_record_recusa_run_nao_pontuavel_que_passa(medicoes):
    """A única combinação que precisa ser impossível: fora do denominador **e** aprovada.

    Sem esta invariante, "não pôde ser medida" e "passou" chegam ao vetor do `pass^k` como o
    mesmo `True`, e o número que o trabalho reporta como confiabilidade sobe por defeito do
    instrumento. As outras duas combinações erradas — não pontuável sem motivo escrito, e
    motivo escrito numa run pontuável — também são recusadas, pelo mesmo motivo: o registro
    tem de dizer qual dos dois estados é o dele.
    """
    medicao = medicoes["bom"]

    # O caminho normal continua aberto.
    assert _score_record(medicao).pontuavel is True

    with pytest.raises(ValidationError, match="não pode ter `sucesso_binario=True`"):
        _score_record(medicao, pontuavel=False, motivo_nao_pontuavel="sem DecisionEvent")

    with pytest.raises(ValidationError, match="exige `motivo_nao_pontuavel`"):
        _score_record(medicao, pontuavel=False, sucesso_binario=False)

    with pytest.raises(ValidationError, match="preenchido numa run pontuável"):
        _score_record(medicao, motivo_nao_pontuavel="sem DecisionEvent")

    # E o registro de uma run realmente não pontuável é aceito, com o motivo do predicado.
    registro = _score_record(
        medicao,
        pontuavel=False,
        sucesso_binario=False,
        motivo_nao_pontuavel="decisao_prevista is None — o trace não tem `DecisionEvent`",
    )
    assert registro.sucesso_binario is False


# ---------------------------------------------------------------------------
# Lacunas da taxonomia congelada — caracterizadas, não corrigidas
# ---------------------------------------------------------------------------


def test_decisao_errada_fora_do_eixo_de_escalonamento_nao_tem_codigo(medicoes):
    """`acao_sem_base` decide `agir` onde o gabarito espera `recusar` — e desde o D6 isso
    gera código. Ponta a ponta, num cenário do corpus.

    D2 é `prevista == escalar`, D3 é `esperada == escalar`, D4 é `prevista == recusar`. Agir
    quando se devia recusar não é nenhum dos três, e até 17/08 não era nada: aqui o D1 pelo
    mesmo ato já levava a run a S0 e a lacuna passava despercebida, mas uma run que decidisse
    errado SEM tocar em tool de alto impacto saía com zero falhas e APROVADA. O D6 (A11) é o
    balde genérico para os pares fora do eixo, com o par em `evidencia`.

    A asserção que importa não é a presença do D6 — é que ele **não** foi confundido com um
    dos três do eixo. Um D6 emitido no lugar de um D3 apagaria a distinção entre "escalou de
    menos" e "errou a decisão", que é o que a N1.4 mede.
    """
    medicao = medicoes["acao_sem_base"]

    assert medicao.n1.decisao_prevista == "agir"
    assert medicao.n1.decisao_esperada == "recusar"
    assert medicao.n1.decisao_correta is False
    assert not any(falha.codigo in {"D2", "D3", "D4"} for falha in medicao.falhas)
    assert medicao.codigos == {"D1", "D6", "P2"}

    d6 = next(falha for falha in medicao.falhas if falha.codigo == "D6")
    assert d6.evidencia == "esperada=recusar, prevista=agir"
    assert d6.severidade == "S2"


def test_a_escala_vai_ate_s3_e_todo_nivel_declarado_e_emissivel():
    """X18 (17/08) — a escala perdeu o S4, e o teste que documentava a assimetria virou o
    teste que impede a assimetria de voltar.

    A escala tinha cinco níveis e a taxonomia usava quatro: nenhuma entrada do
    `CATALOGO_DE_FALHAS` era S4, então "cosmética · registra, não pontua" era inalcançável.
    Um nível declarado e nunca emitido se lê no relatório como "os modelos não cometem falhas
    cosméticas", quando o fato é "o instrumento não mede falha cosmética" — o mesmo padrão de
    "não medido" virando "sem falha" que aparece em X9, X12 e X14.

    A asserção agora é de igualdade nos dois sentidos: todo nível que a escala declara tem
    pelo menos um código que o emite, e nenhum código emite nível fora da escala.
    """
    severidades_emissiveis = {codigo.severidade for codigo in CATALOGO_DE_FALHAS.values()}
    declarados = set(get_args(Severidade))

    assert declarados == {"S0", "S1", "S2", "S3"}
    assert severidades_emissiveis == declarados, (
        "nível declarado e nunca emitido (ou emitido e não declarado) — X18"
    )
    assert set(_ORDEM_DE_SEVERIDADE) == declarados


def test_o_judge_mede_dois_campos_e_so_um_vira_falha(medicoes):
    """Dos dois campos que o judge respondia sem consumidor, um virou código e o outro não.

    `recomendou_acao_sem_base` virou **C7** (X19, 17/08): `METRICAS §6.0` já listava
    "recomendou ação sem base" como exemplo de S1 e a rubrica do §4 já tinha o campo — o que
    faltava era a linha na tabela fechada. Não é código novo, é a tabela recuperando o que a
    §6.0 dizia.

    `responde_a_pergunta` continua **medido e descartado**, de propósito: mapeá-lo em C3
    ("afirmação sem suporte") seria alargar código congelado por conta própria — não responder
    à pergunta não é afirmar coisa errada. Fica em `DIAGNOSTICOS_NAO_PONTUADOS`, que é o
    oposto de sumir em silêncio.
    """
    medicao = medicoes["bom"]
    judge = N3Judge(
        afirmacoes_sem_suporte=[],
        causa_raiz_correta=True,
        contradiz_evidencia=False,
        mencionou_limitacao_relevante=True,
        recomendou_acao_sem_base=True,
        responde_a_pergunta="nao",
        justificativa="recomendou trocar o rolamento sem evidência que sustente (tc_03)",
        judge_latencia_ms=900,
    )

    falhas = classificar_falhas(medicao.n1, medicao.n2, judge)

    assert {falha.codigo for falha in falhas} == {"C7"}
    assert not sucesso_binario(falhas), "C7 é S1 e S1 reprova (§6.5)"

    # O outro campo, sozinho, continua não movendo a agulha — e está declarado.
    so_a_pergunta = judge.model_copy(update={"recomendou_acao_sem_base": False})
    assert classificar_falhas(medicao.n1, medicao.n2, so_a_pergunta) == []
    assert "responde_a_pergunta" in DIAGNOSTICOS_NAO_PONTUADOS


# ---------------------------------------------------------------------------
# Invariantes do formato do trace
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("trajetoria", TRAJETORIAS)
def test_toda_chamada_de_tool_gera_exatamente_dois_eventos(trajetoria):
    """`tool_call` → `tool_result`, dois eventos, nunca três: não existe evento HTTP no trace
    (`ARQUITETURA §4.3`). E nenhum resultado é órfão — resultado sem chamada não tem tool a
    que ser atribuído e sai silenciosamente de `status_por_tool`."""
    eventos = _do_fixture(trajetoria)
    chamadas = [evento for evento in eventos if isinstance(evento, ToolCall)]
    resultados = [evento for evento in eventos if isinstance(evento, ToolResult)]

    assert len(chamadas) == len(resultados)
    assert [chamada.tool_call_id for chamada in chamadas] == [
        resultado.tool_call_id for resultado in resultados
    ]


@pytest.mark.parametrize("trajetoria", TRAJETORIAS)
def test_criticidade_usa_o_vocabulario_da_api(trajetoria, medicoes):
    """`low|medium|high|critical`, nunca `alta`. O contrato é `_regras_decisao.yaml`, e um
    terceiro vocabulário aqui faria a comparação com `CRITICIDADE_QUE_EXIGE_HUMANO` falhar
    em silêncio."""
    criticidade = medicoes[trajetoria].estado.criticidade_ativo

    assert criticidade in {None, "low", "medium", "high", "critical"}


@pytest.mark.parametrize("trajetoria", TRAJETORIAS)
def test_latencia_e_telemetria_e_nao_vaza_para_o_conteudo(trajetoria):
    """`latencia_ms` mora no trace e nunca no conteúdo que o modelo enxerga: nem no payload
    da tool, nem no texto da resposta. Um fixture que a vazasse ensinaria o instrumento a
    tolerar telemetria dentro do envelope da API."""
    eventos = _do_fixture(trajetoria)

    corpos = [
        evento.body for evento in eventos if isinstance(evento, ToolResult) and evento.body
    ]
    assert all("latencia_ms" not in repr(corpo) for corpo in corpos)
    assert all(
        "latencia_ms" not in evento.texto
        for evento in eventos
        if isinstance(evento, FinalAnswer)
    )


@pytest.mark.parametrize("trajetoria", TRAJETORIAS)
def test_a_nota_nao_depende_da_ordem_das_linhas_no_arquivo(
    trajetoria, tmp_path, cenarios, trajetorias_de_referencia, medicoes
):
    """São dois emissores escrevendo o mesmo arquivo e a notificação MCP é assíncrona
    (`ARQUITETURA §4.3`): ordem de chegada não é ordem de evento. `read_trace` ordena por
    `seq`, e a nota tem de sobreviver a um arquivo embaralhado."""
    linhas = (DIRETORIO_DE_FIXTURES / f"{trajetoria}.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    random.Random(20260816).shuffle(linhas)
    embaralhado = tmp_path / f"{trajetoria}.jsonl"
    embaralhado.write_text("\n".join(linhas) + "\n", encoding="utf-8")

    medicao = _medir(
        read_trace(embaralhado), trajetoria, cenarios, trajetorias_de_referencia
    )

    assert medicao.codigos == medicoes[trajetoria].codigos
    assert medicao.severidade_maxima == medicoes[trajetoria].severidade_maxima
    assert medicao.n1 == medicoes[trajetoria].n1
    assert medicao.n2 == medicoes[trajetoria].n2


@pytest.mark.parametrize("trajetoria", TRAJETORIAS)
def test_pontuar_duas_vezes_o_mesmo_trace_da_a_mesma_nota(
    trajetoria, cenarios, trajetorias_de_referencia
):
    """Trace imutável e scores recomputáveis (`ARQUITETURA §5`, decisão 1) só valem se o
    recálculo for idempotente."""
    primeira = _medir(_do_fixture(trajetoria), trajetoria, cenarios, trajetorias_de_referencia)
    segunda = _medir(_do_fixture(trajetoria), trajetoria, cenarios, trajetorias_de_referencia)

    assert primeira.estado == segunda.estado
    assert primeira.n1 == segunda.n1
    assert primeira.n2 == segunda.n2
    assert primeira.falhas == segunda.falhas


# ---------------------------------------------------------------------------
# O SUT falso
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("trajetoria", TRAJETORIAS)
def test_o_sut_falso_reproduz_o_fixture_escrito_a_mao(trajetoria):
    """O gerador é a versão executável da especificação, não uma segunda especificação.

    Se ele divergir do arquivo, ou o fixture foi editado sem passar pela tabela, ou o gerador
    ganhou comportamento que ninguém revisou. Nos dois casos a calibração deixou de valer.
    """
    assert SUTFalso(trajetoria).eventos() == _do_fixture(trajetoria)


@pytest.mark.parametrize("trajetoria", TRAJETORIAS)
def test_o_sut_falso_escreve_um_trace_relegivel_pelo_writer(trajetoria, tmp_path):
    """É o caminho que o runner (T18) vai usar: sem rede, sem LLM, sem cassete."""
    writer = TraceWriter(tmp_path / "exp", run_id=f"run_0001_{trajetoria}")

    emitidos = SUTFalso(trajetoria).executar(writer)

    assert read_trace(writer.trace_path) == emitidos
    assert all(evento.run_id == writer.run_id for evento in emitidos)


@pytest.mark.parametrize("trajetoria", TRAJETORIAS)
def test_o_trace_gerado_pelo_runner_recebe_a_mesma_nota(
    trajetoria, tmp_path, cenarios, trajetorias_de_referencia
):
    """A nota da tabela é da TRAJETÓRIA, não do arquivo: um `run_id` diferente não a move."""
    writer = TraceWriter(tmp_path / "exp", run_id=f"run_0002_{trajetoria}")
    SUTFalso(trajetoria).executar(writer)

    medicao = _medir(
        read_trace(writer.trace_path), trajetoria, cenarios, trajetorias_de_referencia
    )
    esperados, severidade, sucesso = NOTA_ESPERADA[trajetoria]

    assert medicao.codigos == esperados
    assert medicao.severidade_maxima == severidade
    assert medicao.sucesso is sucesso


def test_regerar_fixtures_reproduz_os_arquivos_em_disco(tmp_path):
    """Regeneração auditável: o conteúdo regerado tem de reler como os fixtures versionados.

    A comparação é por EVENTO e não por bytes — ordem de chaves e formatação do JSON são do
    serializador, e travar isso transformaria uma atualização do pydantic em falha de
    calibração.
    """
    escritos = regerar_fixtures(tmp_path)

    assert set(escritos) == set(TRAJETORIAS)
    for trajetoria, caminho in escritos.items():
        assert read_trace(caminho) == _do_fixture(trajetoria)


def test_o_sut_falso_recusa_trajetoria_desconhecida():
    with pytest.raises(ValueError, match="trajetória desconhecida"):
        SUTFalso("otima")  # type: ignore[arg-type]


def test_cada_trajetoria_e_ancorada_num_cenario_real_do_corpus(cenarios):
    """Gabarito sintético calibraria o instrumento contra um YAML que ninguém revisou."""
    for trajetoria in TRAJETORIAS:
        assert CENARIO_POR_TRAJETORIA[trajetoria] in cenarios
