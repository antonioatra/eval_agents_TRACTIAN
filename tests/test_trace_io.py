"""T5 — escrita e leitura de trace.

Três propriedades sustentam tudo que vem depois e são o critério de pronto:

1. o roundtrip preserva o TIPO do evento (união discriminada por `type`);
2. `blob` deduplica por sha;
3. o reader ordena por `seq`, não pela ordem das linhas do arquivo.

A terceira é a que mais importa: são dois emissores escrevendo no mesmo arquivo e a
notificação MCP é assíncrona, então um arquivo fora de ordem é o caso normal, não o
patológico (ARQUITETURA §4.3 e §5, decisão 8).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from tapieval.schema.reader import read_trace, validar_trace
from tapieval.schema.trace import GateEvent, RunStart, ToolCall, ToolResult
from tapieval.schema.writer import TraceWriter

RUN_ID = "run_teste_01"
TS = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


def _run_start(seq: int = 0) -> RunStart:
    return RunStart(
        run_id=RUN_ID,
        seq=seq,
        ts=TS,
        iteration=0,
        experiment_id="exp_teste",
        scenario_id="CEN-04",
        split="dev",
        variant_id="base",
        model_key="qwen",
        seed=7,
        env_mode="replay",
        solicitacao="a bomba 3 está vibrando",
        user_id="user_01",
        asset_id="AT-001",
    )


def _tool_call(seq: int, tool_call_id: str = "tc_01") -> ToolCall:
    return ToolCall(
        run_id=RUN_ID,
        seq=seq,
        ts=TS,
        iteration=1,
        tool_call_id=tool_call_id,
        tool_name="get_vibration_analysis",
        args={"asset_id": "AT-001", "dias": 30},
        args_validos=True,
    )


def _tool_result(seq: int, tool_call_id: str = "tc_01") -> ToolResult:
    return ToolResult(
        run_id=RUN_ID,
        seq=seq,
        ts=TS,
        iteration=1,
        tool_call_id=tool_call_id,
        status="PARCIAL",
        http_status=200,
        latencia_ms=42,
        campos_ausentes=["rms"],
    )


def _gate(seq: int) -> GateEvent:
    return GateEvent(
        run_id=RUN_ID,
        seq=seq,
        ts=TS,
        iteration=2,
        acao="criar_ordem_servico",
        args={"asset_id": "AT-001"},
        justificativa="vibração acima do limiar",
        citacoes=["tc_01"],
        citacoes_validas=True,
        permissao_usuario_ok=True,
        approver="policy",
        veredito="aprovado",
        idempotency_key="a" * 64,
    )


# ---------------------------------------------------------------------------
# 1. Roundtrip preserva o tipo
# ---------------------------------------------------------------------------


def test_roundtrip_devolve_o_tipo_concreto_do_evento(tmp_path):
    """Um `ToolCall` volta como `ToolCall` — não como dict nem como `BaseEvent`."""
    writer = TraceWriter(tmp_path, RUN_ID)
    writer.emit(_tool_call(seq=1))

    eventos = read_trace(writer.trace_path)

    assert len(eventos) == 1
    assert type(eventos[0]) is ToolCall
    assert eventos[0].tool_call_id == "tc_01"
    assert eventos[0].args == {"asset_id": "AT-001", "dias": 30}


def test_roundtrip_discrimina_tipos_diferentes_na_mesma_run(tmp_path):
    """A união é discriminada por `type`: cada linha vira a sua própria classe."""
    writer = TraceWriter(tmp_path, RUN_ID)
    for evento in (_run_start(0), _tool_call(1), _tool_result(2), _gate(3)):
        writer.emit(evento)

    eventos = read_trace(writer.trace_path)

    assert [type(evento) for evento in eventos] == [RunStart, ToolCall, ToolResult, GateEvent]


def test_roundtrip_preserva_campos_tipados(tmp_path):
    """Datas e listas voltam tipadas, não como string e dict crus."""
    writer = TraceWriter(tmp_path, RUN_ID)
    writer.emit(_tool_result(seq=1))

    (evento,) = read_trace(writer.trace_path)

    assert isinstance(evento, ToolResult)
    assert evento.ts == TS
    assert evento.status == "PARCIAL"
    assert evento.campos_ausentes == ["rms"]
    assert evento.tentativas == 1  # default preservado


# ---------------------------------------------------------------------------
# 2. blob deduplica por sha
# ---------------------------------------------------------------------------


def test_blob_deduplica_o_mesmo_texto(tmp_path):
    """Mesmo conteúdo: um arquivo só, mesmo hash devolvido."""
    writer = TraceWriter(tmp_path, RUN_ID)
    texto = "system prompt longo que se repete em toda run da variante"

    primeiro = writer.blob(texto)
    segundo = writer.blob(texto)

    assert primeiro == segundo
    assert list(writer.blobs_dir.iterdir()) == [writer.blobs_dir / f"{primeiro}.txt"]


def test_blob_separa_textos_diferentes(tmp_path):
    writer = TraceWriter(tmp_path, RUN_ID)

    um = writer.blob("prompt A")
    outro = writer.blob("prompt B")

    assert um != outro
    assert len(list(writer.blobs_dir.iterdir())) == 2


def test_blob_preserva_o_conteudo_com_acentos(tmp_path):
    """O blob guarda o prompt de verdade; utf-8 tem que sobreviver ao roundtrip."""
    writer = TraceWriter(tmp_path, RUN_ID)
    texto = "análise de vibração — rolamento com folga"

    sha = writer.blob(texto)

    assert (writer.blobs_dir / f"{sha}.txt").read_text(encoding="utf-8") == texto


# ---------------------------------------------------------------------------
# 3. O reader ordena por seq, não pela ordem do arquivo
# ---------------------------------------------------------------------------


def test_reader_ordena_por_seq_e_nao_pela_ordem_das_linhas(tmp_path):
    """Notificação MCP é assíncrona: ordem de chegada não é ordem de evento."""
    writer = TraceWriter(tmp_path, RUN_ID)
    for evento in (_gate(3), _run_start(0), _tool_result(2), _tool_call(1)):
        writer.emit(evento)

    # O arquivo está mesmo fora de ordem — sem isto o teste passaria por acidente.
    linhas = writer.trace_path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(linha)["seq"] for linha in linhas] == [3, 0, 2, 1]

    eventos = read_trace(writer.trace_path)

    assert [evento.seq for evento in eventos] == [0, 1, 2, 3]
    assert [type(evento) for evento in eventos] == [RunStart, ToolCall, ToolResult, GateEvent]


def test_reader_e_estavel_para_seq_repetido(tmp_path):
    """Empate em `seq` não embaralha: a ordem do arquivo decide o desempate."""
    writer = TraceWriter(tmp_path, RUN_ID)
    writer.emit(_tool_call(seq=1, tool_call_id="tc_primeiro"))
    writer.emit(_tool_call(seq=1, tool_call_id="tc_segundo"))

    eventos = read_trace(writer.trace_path)

    assert [evento.tool_call_id for evento in eventos] == ["tc_primeiro", "tc_segundo"]


# ---------------------------------------------------------------------------
# Escrita: append-only e criação de diretório
# ---------------------------------------------------------------------------


def test_emit_faz_append_e_nao_sobrescreve(tmp_path):
    writer = TraceWriter(tmp_path, RUN_ID)
    for seq in range(3):
        writer.emit(_tool_call(seq=seq, tool_call_id=f"tc_{seq}"))

    linhas = writer.trace_path.read_text(encoding="utf-8").splitlines()

    assert len(linhas) == 3


def test_writer_novo_continua_o_trace_existente(tmp_path):
    """Dois emissores escrevem na mesma run; o segundo não pode truncar o arquivo."""
    TraceWriter(tmp_path, RUN_ID).emit(_tool_call(seq=1, tool_call_id="tc_cliente"))
    TraceWriter(tmp_path, RUN_ID).emit(_tool_call(seq=2, tool_call_id="tc_servidor"))

    eventos = read_trace(tmp_path / "traces" / f"{RUN_ID}.jsonl")

    assert [evento.tool_call_id for evento in eventos] == ["tc_cliente", "tc_servidor"]


def test_writer_cria_o_layout_em_disco(tmp_path):
    """`runs/<experiment_id>/{traces,blobs}` nascem com o writer."""
    run_dir = tmp_path / "runs" / "exp_inexistente"

    writer = TraceWriter(run_dir, RUN_ID)

    assert writer.traces_dir.is_dir()
    assert writer.blobs_dir.is_dir()
    assert writer.trace_path == run_dir / "traces" / f"{RUN_ID}.jsonl"


def test_runs_diferentes_escrevem_em_arquivos_diferentes(tmp_path):
    um = TraceWriter(tmp_path, "run_a")
    outro = TraceWriter(tmp_path, "run_b")

    um.emit(_tool_call(seq=1, tool_call_id="tc_a"))
    outro.emit(_tool_call(seq=1, tool_call_id="tc_b"))

    assert [evento.tool_call_id for evento in read_trace(um.trace_path)] == ["tc_a"]
    assert [evento.tool_call_id for evento in read_trace(outro.trace_path)] == ["tc_b"]


# ---------------------------------------------------------------------------
# Leitura: casos de borda
# ---------------------------------------------------------------------------


def test_read_trace_de_arquivo_vazio(tmp_path):
    caminho = tmp_path / "vazio.jsonl"
    caminho.write_text("", encoding="utf-8")

    assert read_trace(caminho) == []


def test_read_trace_ignora_linhas_em_branco(tmp_path):
    writer = TraceWriter(tmp_path, RUN_ID)
    writer.emit(_tool_call(seq=1))
    with writer.trace_path.open("a", encoding="utf-8") as arquivo:
        arquivo.write("\n")

    assert len(read_trace(writer.trace_path)) == 1


def test_read_trace_erra_apontando_a_linha_invalida(tmp_path):
    """Trace parcialmente ilegível pontuado em silêncio é pior que run que falha."""
    writer = TraceWriter(tmp_path, RUN_ID)
    writer.emit(_tool_call(seq=1))
    with writer.trace_path.open("a", encoding="utf-8") as arquivo:
        arquivo.write('{"type": "tool_call", "seq": 2}\n')

    with pytest.raises(ValueError, match=r":2$"):
        read_trace(writer.trace_path)


def test_read_trace_erra_com_type_desconhecido(tmp_path):
    """O discriminador é fechado: `type` fora do vocabulário não vira `BaseEvent`."""
    caminho = tmp_path / "estranho.jsonl"
    caminho.write_text(
        '{"type": "inventado", "run_id": "r", "seq": 1, "ts": "2026-08-15T12:00:00Z",'
        ' "iteration": 0}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        read_trace(caminho)


# ---------------------------------------------------------------------------
# 6. `validar_trace` (A7) — o que torna uma run não pontuável
# ---------------------------------------------------------------------------


def test_trace_integro_nao_tem_defeito():
    """O zero da função. Falha aqui invalidaria run boa, que é o pior erro deste validador:
    ele não reprova o agente, ele **apaga a célula** da matriz da bateria."""
    assert validar_trace([_run_start(0), _tool_call(1), _tool_result(2)]) == []


def test_lacuna_de_seq_invalida_a_run():
    """`ARQUITETURA §5`, decisão 9. O evento que sumiu é evidência que sumiu, e evidência que
    sumiu vira 'o agente não consultou' na N1.1 — falha do transporte imputada ao modelo."""
    defeitos = validar_trace([_run_start(0), _tool_call(1), _tool_result(3)])

    assert [defeito.tipo for defeito in defeitos] == ["lacuna_de_seq"]
    assert "[2]" in defeitos[0].detalhe


def test_seq_duplicado_invalida_a_run():
    """X23 pelo lado do leitor: dois emissores numerando no mesmo espaço sem coordenação.

    A escolha da T16 foi o harness não numerar em stdio. Se um `seq` repetir, essa garantia
    caiu e a ordem total deixou de existir — e é dela que a aderência causal da N2 depende.
    """
    defeitos = validar_trace([_run_start(0), _tool_call(1), _tool_result(1)])

    assert [defeito.tipo for defeito in defeitos] == ["seq_duplicado"]
    assert "[1]" in defeitos[0].detalhe


def test_trace_sem_run_start_invalida_a_run():
    """Sem `RunStart`, `derivar_estado` não acha o `asset_id` e a criticidade do ativo nasce
    nula: a run seria pontuada contra um mundo vazio, em vez de não ser pontuada."""
    assert [d.tipo for d in validar_trace([_tool_call(0), _tool_result(1)])] == [
        "sem_run_start"
    ]


def test_trace_vazio_e_sem_run_start_e_nao_uma_quarta_categoria():
    """O efeito é o mesmo, e uma categoria a mais só daria ao runner um caso a mais para
    esquecer de tratar."""
    assert [d.tipo for d in validar_trace([])] == ["sem_run_start"]


def test_os_defeitos_se_acumulam_em_vez_de_curto_circuitar():
    """O manifesto guarda o motivo, e um trace que quebrou por dois motivos quebrou por dois.

    Curto-circuitar no primeiro faria o diagnóstico da bateria depender da ordem das
    checagens — e a run seria reprocessada, corrigida pela metade, e falharia de novo.
    """
    defeitos = validar_trace([_tool_call(1), _tool_result(1), _gate(4)])

    assert {defeito.tipo for defeito in defeitos} == {
        "sem_run_start",
        "seq_duplicado",
        "lacuna_de_seq",
    }


def test_o_defeito_se_le_como_texto_no_manifesto():
    """`valida: false` sem motivo legível é o mesmo que descartar em silêncio."""
    defeito = validar_trace([_run_start(0), _tool_call(2)])[0]
    assert str(defeito).startswith("lacuna_de_seq: ")
