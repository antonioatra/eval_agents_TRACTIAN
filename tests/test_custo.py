"""T35 — instrumentação de custo por camada de julgamento.

Sem estes números a figura principal do trabalho não tem eixo x: H0 é uma curva
custo × recall por camada (ARQUITETURA §12, INS.4 em METRICAS §7). O recall vem do
gold humano; o custo vem daqui.

Quatro propriedades são o critério de pronto:

1. N1 e N2 registram `tokens=0` — são determinísticos, e isso é invariante do
   schema, não disciplina de quem chama;
2. o judge cego e o judge com trace registram contagens DIFERENTES — é essa
   diferença que sustenta os dois pontos da curva (METRICAS §4, H1);
3. a rotulagem humana aceita tempo cronometrado à mão (`minutos_humano`), porque
   não existe relógio de parede para medir N4;
4. custo é score, não trace: mora em `scores/`, nunca no `.jsonl` do trace
   (ARQUITETURA §5, decisão 1).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from tapieval.schema import custo as modulo_custo
from tapieval.schema.custo import (
    CustoRecord,
    CustoWriter,
    MedidorDeCusto,
    ler_custos,
)
from tapieval.schema.trace import ToolCall
from tapieval.schema.writer import TraceWriter

RUN_ID = "run_teste_01"
SCORER = "v1"


def _relogio_falso(monkeypatch, marcas: list[float]) -> None:
    """Substitui o cronômetro do medidor por uma sequência determinística."""
    restantes = iter(marcas)
    monkeypatch.setattr(modulo_custo, "perf_counter", lambda: next(restantes))


# ---------------------------------------------------------------------------
# 1. N1 e N2 não gastam token — invariante, não convenção
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("camada", ["N1", "N2"])
def test_camada_deterministica_registra_zero_token(camada, monkeypatch):
    """N1 e N2 rodam sem LLM: o custo delas é só tempo de CPU."""
    _relogio_falso(monkeypatch, [10.0, 10.25])

    with MedidorDeCusto(run_id=RUN_ID, camada=camada) as medidor:
        pass
    registro = medidor.fechar()

    assert registro.tokens_in == 0
    assert registro.tokens_out == 0
    assert registro.chamadas_llm == 0
    assert registro.segundos == pytest.approx(0.25)


@pytest.mark.parametrize("camada", ["N1", "N2", "N4"])
def test_camada_sem_llm_recusa_token(camada):
    """Token numa camada determinística é bug de contabilidade, não dado.

    Deixar passar contamina o eixo x de H0 exatamente onde ele precisa valer zero.
    """
    with pytest.raises(ValidationError):
        CustoRecord(run_id=RUN_ID, camada=camada, tokens_in=120)


def test_camada_fora_do_vocabulario_e_recusada():
    """`camada` é Literal fechado: cada valor é um ponto nomeado da curva."""
    with pytest.raises(ValidationError):
        CustoRecord(run_id=RUN_ID, camada="N5")


# ---------------------------------------------------------------------------
# 2. Judge cego × judge com trace — os dois pontos da curva de H0
# ---------------------------------------------------------------------------


def test_medidor_soma_as_chamadas_de_llm():
    """Os tokens vêm de quem chama o LLM; o medidor só acumula o que recebe."""
    medidor = MedidorDeCusto(run_id=RUN_ID, camada="N3_cego")

    medidor.registrar_llm(prompt_tokens=800, completion_tokens=120)
    medidor.registrar_llm(prompt_tokens=200, completion_tokens=40)
    registro = medidor.fechar()

    assert registro.tokens_in == 1000
    assert registro.tokens_out == 160
    assert registro.chamadas_llm == 2


def test_judge_cego_e_judge_com_trace_registram_contagens_diferentes():
    """A diferença ENTRE as duas configurações é o resultado, não um detalhe.

    Mesma rubrica, insumo diferente: o judge com trace lê todos os `tool_result`
    e custa 3–8× mais tokens (METRICAS §4). São dois pontos da mesma curva.
    """
    cego = MedidorDeCusto(run_id=RUN_ID, camada="N3_cego")
    cego.registrar_llm(prompt_tokens=900, completion_tokens=150)

    com_trace = MedidorDeCusto(run_id=RUN_ID, camada="N3_com_trace")
    com_trace.registrar_llm(prompt_tokens=5400, completion_tokens=210)

    registro_cego = cego.fechar()
    registro_com_trace = com_trace.fechar()

    assert registro_cego.camada != registro_com_trace.camada
    assert registro_com_trace.tokens_in > registro_cego.tokens_in
    assert registro_com_trace.tokens_out != registro_cego.tokens_out


def test_os_dois_pontos_da_curva_convivem_na_mesma_execucao(tmp_path):
    """Uma execução avaliada tem um custo POR camada, todos no mesmo arquivo."""
    writer = CustoWriter(tmp_path, RUN_ID, SCORER)

    for camada, tokens_in in [("N3_cego", 900), ("N3_com_trace", 5400)]:
        medidor = MedidorDeCusto(run_id=RUN_ID, camada=camada)
        medidor.registrar_llm(prompt_tokens=tokens_in, completion_tokens=100)
        writer.registrar(medidor.fechar())

    registros = ler_custos(writer.custo_path)

    assert [registro.camada for registro in registros] == ["N3_cego", "N3_com_trace"]
    assert [registro.tokens_in for registro in registros] == [900, 5400]


# ---------------------------------------------------------------------------
# 3. N4 — tempo humano cronometrado à mão
# ---------------------------------------------------------------------------


def test_rotulagem_humana_aceita_minutos_cronometrados():
    """Não há relógio de parede para o humano: o tempo é digitado por ele.

    INS.4 mede N1–N3 em segundos e N4 em minutos (METRICAS §7).
    """
    registro = CustoRecord(run_id=RUN_ID, camada="N4", minutos_humano=12.5)

    assert registro.minutos_humano == 12.5
    assert registro.tokens_in == 0
    assert registro.chamadas_llm == 0


def test_medidor_aceita_minutos_humano_no_n4():
    """O mesmo medidor serve ao N4 — só que o tempo entra à mão."""
    medidor = MedidorDeCusto(run_id=RUN_ID, camada="N4")

    medidor.registrar_minutos_humano(8.0)
    registro = medidor.fechar()

    assert registro.minutos_humano == 8.0


def test_minutos_humano_fora_do_n4_e_recusado():
    """Só o N4 tem humano no loop; minuto humano no N3 seria custo inventado."""
    with pytest.raises(ValidationError):
        CustoRecord(run_id=RUN_ID, camada="N3_cego", minutos_humano=3.0)


def test_valores_negativos_sao_recusados():
    with pytest.raises(ValidationError):
        CustoRecord(run_id=RUN_ID, camada="N4", minutos_humano=-1.0)


# ---------------------------------------------------------------------------
# 4. Custo é score: mora em scores/, nunca no trace
# ---------------------------------------------------------------------------


def test_custo_nao_encosta_no_trace(tmp_path):
    """O `.jsonl` do trace guarda fato bruto; custo é resultado de avaliação."""
    trace_writer = TraceWriter(tmp_path, RUN_ID)
    trace_writer.emit(
        ToolCall(
            run_id=RUN_ID,
            seq=1,
            ts=datetime(2026, 8, 15, 12, 0, tzinfo=UTC),
            iteration=1,
            tool_call_id="tc_01",
            tool_name="get_vibration_analysis",
            args={"asset_id": "AT-001"},
            args_validos=True,
        )
    )

    custo_writer = CustoWriter(tmp_path, RUN_ID, SCORER)
    custo_writer.registrar(CustoRecord(run_id=RUN_ID, camada="N1", segundos=0.4))

    linhas_do_trace = trace_writer.trace_path.read_text(encoding="utf-8").splitlines()

    assert len(linhas_do_trace) == 1
    assert json.loads(linhas_do_trace[0])["type"] == "tool_call"
    assert custo_writer.custo_path != trace_writer.trace_path
    assert custo_writer.custo_path.is_relative_to(tmp_path / "scores" / SCORER)


def test_writer_cria_o_diretorio_de_scores(tmp_path):
    """`runs/<experiment_id>/scores/<scorer_version>/` nasce com o writer."""
    run_dir = tmp_path / "runs" / "exp_inexistente"

    writer = CustoWriter(run_dir, RUN_ID, SCORER)

    assert writer.scores_dir.is_dir()
    assert writer.custo_path == run_dir / "scores" / SCORER / f"{RUN_ID}.custo.jsonl"


def test_versoes_de_scorer_nao_se_misturam(tmp_path):
    """`scores/v1` e `scores/v2` coexistem: o custo do judge muda com a rubrica."""
    v1 = CustoWriter(tmp_path, RUN_ID, "v1")
    v2 = CustoWriter(tmp_path, RUN_ID, "v2")

    v1.registrar(CustoRecord(run_id=RUN_ID, camada="N3_cego", tokens_in=900))
    v2.registrar(CustoRecord(run_id=RUN_ID, camada="N3_cego", tokens_in=1500))

    assert [registro.tokens_in for registro in ler_custos(v1.custo_path)] == [900]
    assert [registro.tokens_in for registro in ler_custos(v2.custo_path)] == [1500]


def test_writer_novo_continua_o_arquivo_existente(tmp_path):
    """N1/N2 e N3 são medidos em momentos diferentes; o segundo não trunca."""
    CustoWriter(tmp_path, RUN_ID, SCORER).registrar(CustoRecord(run_id=RUN_ID, camada="N1"))
    CustoWriter(tmp_path, RUN_ID, SCORER).registrar(CustoRecord(run_id=RUN_ID, camada="N2"))

    registros = ler_custos(tmp_path / "scores" / SCORER / f"{RUN_ID}.custo.jsonl")

    assert [registro.camada for registro in registros] == ["N1", "N2"]


def test_writer_recusa_registro_de_outra_run(tmp_path):
    """Custo de outra run neste arquivo corromperia INS.4 em silêncio."""
    writer = CustoWriter(tmp_path, RUN_ID, SCORER)

    with pytest.raises(ValueError, match="run_id"):
        writer.registrar(CustoRecord(run_id="run_outra", camada="N1"))


def test_roundtrip_preserva_os_campos(tmp_path):
    writer = CustoWriter(tmp_path, RUN_ID, SCORER)
    writer.registrar(
        CustoRecord(
            run_id=RUN_ID,
            camada="N3_com_trace",
            segundos=31.5,
            tokens_in=5400,
            tokens_out=210,
            chamadas_llm=1,
        )
    )

    (registro,) = ler_custos(writer.custo_path)

    assert registro.camada == "N3_com_trace"
    assert registro.segundos == pytest.approx(31.5)
    assert registro.tokens_in == 5400
    assert registro.tokens_out == 210
    assert registro.chamadas_llm == 1
    assert registro.minutos_humano is None


def test_ler_custos_de_arquivo_inexistente(tmp_path):
    """Camada ainda não medida não é erro: N4 só existe para 35 execuções."""
    assert ler_custos(tmp_path / "scores" / SCORER / "run_sem_custo.custo.jsonl") == []


def test_ler_custos_erra_apontando_a_linha_invalida(tmp_path):
    """Custo ilegível somado em silêncio deslocaria o eixo x sem aviso."""
    writer = CustoWriter(tmp_path, RUN_ID, SCORER)
    writer.registrar(CustoRecord(run_id=RUN_ID, camada="N1"))
    with writer.custo_path.open("a", encoding="utf-8") as arquivo:
        arquivo.write('{"camada": "N9"}\n')

    with pytest.raises(ValueError, match=r":2$"):
        ler_custos(writer.custo_path)
