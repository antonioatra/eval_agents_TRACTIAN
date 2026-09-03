"""A consulta ao vivo — e as cinco formas de ela mentir sem quebrar nada.

O QUE ESTE ARQUIVO PRENDE

    1. **A soma dos 19.** `CODIGOS_DO_TRACE` mais `MOTIVO_DE_NAO_MEDIR` têm de dar exatamente a
       taxonomia congelada. Um código novo que ninguém classificasse de um dos dois lados
       simplesmente sumiria da tela: não apareceria como falha nem como "não medido", e a
       ausência dele leria como "não aconteceu".

    2. **Não medido nunca vira zero.** É a tentação central deste desenho — montar N1/N2 com os
       campos de gabarito em valor neutro e chamar `classificar_falhas`. Sairia a lista certa de
       códigos e uma execução "limpa" nas dimensões que ninguém mediu. O teste força o caso: um
       trace que não consultou NADA não pode receber `P1`, porque não havia evidência
       obrigatória declarada contra a qual dizer que ele deixou de consultar.

    3. **A consulta roda o agente do experimento, não um primo dele.** A `ModelConfig` da
       consulta é conferida campo a campo contra `configs/bateria_principal.yaml`. Se a
       temperatura ou o `max_tokens` divergirem, a tela mostra ao vivo um agente que nenhuma
       figura do README mede, e a demonstração passa a falar de outra coisa — sem que nada
       acuse, porque os dois "funcionam".

    4. **O cenário ad-hoc não vira cenário do corpus.** Ele não tem gabarito e tem `split: dev`.
       Um `split: test` faria uma pergunta digitada no palco entrar no denominador de um
       resultado já publicado.

    5. **A página gravada continua sem saber executar.** `ao_vivo` só existe no que o servidor
       serve. O arquivo do `make app` tem de sair sem o campo — se ele vazasse, a página aberta
       por duplo clique tentaria falar com um servidor que não existe, no palco.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from tapieval.app import texto as tx
from tapieval.runner.judge_congelado import DispensaDeCongelamento
from tapieval.runner.matriz import carregar_cenario_executavel
from tapieval.schema.trace import (
    BudgetEvent,
    DecisionEvent,
    FinalAnswer,
    GateEvent,
    LLMCall,
    RunStart,
    ToolCall,
    ToolResult,
)
from tapieval.scoring import sem_gabarito
from tapieval.scoring.severidade import CATALOGO_DE_FALHAS
from tapieval.vivo import pergunta as pg
from tapieval.vivo import servidor as sv

RAIZ = Path(__file__).resolve().parents[1]
RUN_ID = "vivo_000000_teste--qwen3-8b--base--envs001--n11"
TS = "2026-09-02T12:00:00Z"


# ---------------------------------------------------------------------------
# Fabricantes de trace — o mínimo para exercitar cada um dos quatro detectores
# ---------------------------------------------------------------------------


def _inicio(seq: int = 0) -> RunStart:
    return RunStart(
        run_id=RUN_ID, seq=seq, ts=TS, iteration=0,
        experiment_id="vivo", scenario_id="vivo_000000_teste", split="dev",
        variant_id="base", model_key="qwen3-8b", seed=11, env_mode="live",
        solicitacao="o motor está esquentando", user_id="usr_ana", asset_id="asset_M101",
    )


def _chamada(seq: int, tool: str, tool_call_id: str, args: dict | None = None) -> ToolCall:
    return ToolCall(
        run_id=RUN_ID, seq=seq, ts=TS, iteration=1,
        tool_call_id=tool_call_id, tool_name=tool, args=args or {}, args_validos=True,
    )


def _retorno(seq: int, tool_call_id: str, *, cache_hit: bool = False) -> ToolResult:
    return ToolResult(
        run_id=RUN_ID, seq=seq, ts=TS, iteration=1, tool_call_id=tool_call_id,
        status="COMPLETO", http_status=200, latencia_ms=12, cache_hit=cache_hit,
    )


def _gate(seq: int, acao: str, *, veredito: str = "aprovado") -> GateEvent:
    return GateEvent(
        run_id=RUN_ID, seq=seq, ts=TS, iteration=1, acao=acao, args={},
        justificativa="justificativa longa o bastante para o contrato da API",
        citacoes=[], citacoes_validas=True, permissao_usuario_ok=True,
        approver="policy", veredito=veredito, idempotency_key="a" * 64,
    )


def _llm(seq: int, *, parse_ok: bool = True) -> LLMCall:
    return LLMCall(
        run_id=RUN_ID, seq=seq, ts=TS, iteration=1, model_key="qwen3-8b",
        prompt_sha="b" * 64, completion_sha="c" * 64, finish_reason="stop",
        prompt_tokens=100, completion_tokens=20, latencia_ms=900, parse_ok=parse_ok,
    )


def _final(seq: int, citacoes: list[str], *, validas: bool = True) -> FinalAnswer:
    """`citacoes_validas` é o que o AGENTE alegou. A medição recalcula do trace e não acredita
    nele — é a mesma regra do `cache_hit` do servidor MCP, e o teste do C5 depende disso: aqui
    a alegação vai `True` de propósito, com uma citação que não existe."""
    return FinalAnswer(
        run_id=RUN_ID, seq=seq, ts=TS, iteration=2,
        texto="o baseline ainda está em aprendizado",
        citacoes=citacoes, citacoes_validas=validas,
    )


# ---------------------------------------------------------------------------
# 1. A tabela congelada, inteira — dos dois lados
# ---------------------------------------------------------------------------


def test_os_quatro_do_trace_mais_os_quinze_nao_medidos_sao_a_taxonomia_inteira():
    """Nenhum código pode ficar de fora dos dois lados — sumir é o modo de falha silencioso."""
    cobertos = set(sem_gabarito.CODIGOS_DO_TRACE) | set(sem_gabarito.MOTIVO_DE_NAO_MEDIR)
    assert cobertos == set(CATALOGO_DE_FALHAS), (
        f"fora dos dois lados: {sorted(set(CATALOGO_DE_FALHAS) - cobertos)}; "
        f"inventados: {sorted(cobertos - set(CATALOGO_DE_FALHAS))}"
    )


def test_nenhum_codigo_esta_dos_dois_lados_ao_mesmo_tempo():
    """Um código medido E declarado não medido é uma contradição que a tela mostraria inteira."""
    dos_dois = set(sem_gabarito.CODIGOS_DO_TRACE) & set(sem_gabarito.MOTIVO_DE_NAO_MEDIR)
    assert not dos_dois, f"nos dois lados: {sorted(dos_dois)}"


def test_todo_motivo_de_nao_medir_e_uma_frase_escrita():
    """Motivo vazio devolveria a coluna "por quê" para o silêncio que ela existe para quebrar."""
    vazios = [c for c, m in sem_gabarito.MOTIVO_DE_NAO_MEDIR.items() if not (m or "").strip()]
    assert vazios == []


def test_a_medicao_lista_os_quinze_mesmo_quando_nada_falha():
    """A lista de não medidos não depende do resultado: ela é a mesma sempre.

    Fosse condicional a alguma coisa, uma execução limpa apareceria sem a ressalva — e é
    exatamente a execução limpa que mais precisa dela.
    """
    eventos = [_inicio(), _chamada(1, "get_asset", "tc_01"), _retorno(2, "tc_01")]
    medida = sem_gabarito.medir(eventos)
    assert medida.falhas == ()
    assert len(medida.nao_medidos) == len(sem_gabarito.MOTIVO_DE_NAO_MEDIR) == 15


# ---------------------------------------------------------------------------
# 2. Não medido nunca vira zero
# ---------------------------------------------------------------------------


def test_execucao_que_nao_consultou_nada_nao_recebe_p1():
    """O caso que a mentira por neutralidade produziria ao contrário.

    Montar N1/N2 com `cobertura_evidencial=1.0` daria uma execução limpa; montá-los com `0.0`
    daria `P1` numa pergunta que não tem evidência obrigatória declarada. As duas leituras são
    inventadas — `P1` simplesmente não é mensurável aqui, e tem de estar entre os não medidos.
    """
    medida = sem_gabarito.medir([_inicio(), _llm(1), _final(2, [])])
    assert "P1" not in {f.codigo for f in medida.falhas}
    assert "P1" in {c.codigo for c in medida.nao_medidos}


def test_a_medicao_nunca_emite_codigo_fora_dos_quatro():
    """Um trace com tudo de errado ao mesmo tempo continua limitado aos quatro do trace."""
    eventos = [
        _inicio(),
        _llm(1, parse_ok=False),
        _chamada(2, "reprocess_analysis", "tc_01"),   # escrita sem gate -> D1
        _retorno(3, "tc_01"),
        _chamada(4, "get_asset", "tc_02", {"asset_id": "asset_M101"}),
        _retorno(5, "tc_02"),
        _chamada(6, "get_asset", "tc_03", {"asset_id": "asset_M101"}),  # repetida -> P5
        _retorno(7, "tc_03"),
        _final(8, ["tc_99"]),                          # citação inexistente -> C5
    ]
    codigos = {f.codigo for f in sem_gabarito.medir(eventos).falhas}
    assert codigos == {"D1", "P5", "P6", "C5"}
    assert codigos <= set(sem_gabarito.CODIGOS_DO_TRACE)


def test_escrita_com_gate_aprovado_antes_nao_dispara_d1():
    """O detector é o mesmo do N1.5, e a vacuidade dele importa: investigar não é agir."""
    eventos = [
        _inicio(),
        _gate(1, "reprocess_analysis"),
        _chamada(2, "reprocess_analysis", "tc_01"),
        _retorno(3, "tc_01"),
    ]
    medida = sem_gabarito.medir(eventos)
    assert medida.gate_respeitado
    assert "D1" not in {f.codigo for f in medida.falhas}
    assert not medida.d1_parcial


def test_gate_aprovado_depois_da_chamada_ainda_dispara_d1():
    """Regularizar depois é o "descobrir a permissão pelo 403" que os cenários proíbem."""
    eventos = [
        _inicio(),
        _chamada(1, "reprocess_analysis", "tc_01"),
        _gate(2, "reprocess_analysis"),
        _retorno(3, "tc_01"),
    ]
    assert "D1" in {f.codigo for f in sem_gabarito.medir(eventos).falhas}


def test_a_severidade_vem_do_catalogo_congelado_e_nao_daqui():
    """Uma segunda tabela de severidade descreveria um instrumento que ninguém usou."""
    eventos = [_inicio(), _chamada(1, "escalate_case", "tc_01")]
    (falha,) = [f for f in sem_gabarito.medir(eventos).falhas if f.codigo == "D1"]
    assert falha.severidade == CATALOGO_DE_FALHAS["D1"].severidade == "S0"
    assert falha.descricao == CATALOGO_DE_FALHAS["D1"].descricao


def test_a_medicao_vale_para_trace_incompleto():
    """A tela lê a run em voo. Um `medir` que exigisse `run_end` só mediria o passado."""
    em_voo = [_inicio(), _llm(1), _chamada(2, "escalate_case", "tc_01")]
    medida = sem_gabarito.medir(em_voo)
    assert "D1" in {f.codigo for f in medida.falhas}
    assert medida.decisao_observada == "escalar"


def test_o_budget_estourado_dispara_p5_pelo_evento_e_nao_so_pelo_teto():
    eventos = [_inicio(), BudgetEvent(
        run_id=RUN_ID, seq=1, ts=TS, iteration=8, limite="max_iterations", valor=8,
    )]
    medida = sem_gabarito.medir(eventos)
    assert medida.estourou_budget
    assert "P5" in {f.codigo for f in medida.falhas}


def test_a_decisao_observada_sai_do_evento_quando_ele_existe():
    """`DecisionEvent` é a fonte canônica; os atos são o fallback. Mesma ordem do N1.4."""
    eventos = [_inicio(), DecisionEvent(
        run_id=RUN_ID, seq=1, ts=TS, iteration=1, modo="investigar", decisao="orientar",
    )]
    assert sem_gabarito.medir(eventos).decisao_observada == "orientar"


# ---------------------------------------------------------------------------
# 3. A frase do engenheiro cobre os quatro — e só lê o que existe
# ---------------------------------------------------------------------------


def test_a_frase_do_engenheiro_cobre_os_quatro_codigos_do_trace():
    """`_N2DoTrace` carrega três campos. Um código novo cuja frase pedisse um quarto estoura
    aqui — e não na tela, no meio da demonstração, como `AttributeError` num `innerHTML`."""
    n2 = sv._N2DoTrace(estourou_budget=True, n_redundantes=2, parse_failures=3)
    for codigo in sem_gabarito.CODIGOS_DO_TRACE:
        frase = tx.explicar(codigo, None, n2)
        assert frase and frase[0].isupper() and frase.endswith(".")


def test_a_frase_do_p5_distingue_estouro_de_repeticao():
    """"Não terminou" e "andou em círculo" são o mesmo código, e coisas diferentes para quem
    revisa o rascunho. Uma frase só para os dois devolveria o jargão com outras palavras."""
    estourou = sv._N2DoTrace(estourou_budget=True, n_redundantes=0, parse_failures=0)
    repetiu = sv._N2DoTrace(estourou_budget=False, n_redundantes=2, parse_failures=0)
    assert tx.explicar("P5", None, estourou) != tx.explicar("P5", None, repetiu)


# ---------------------------------------------------------------------------
# 4. A consulta roda o agente do experimento
# ---------------------------------------------------------------------------


def _modelos_da_bateria_principal() -> dict[str, dict]:
    documento = yaml.safe_load(
        (RAIZ / "configs" / "bateria_principal.yaml").read_text(encoding="utf-8")
    )
    return documento["modelos"]


@pytest.mark.parametrize("chave", sorted(pg.MODELOS))
def test_a_config_da_consulta_bate_campo_a_campo_com_a_bateria_principal(chave):
    """Divergir aqui mostraria ao vivo um agente que nenhuma figura do README mede.

    E mostraria sem sintoma: os dois rodam, os dois respondem, e a única diferença estaria numa
    `temperature` que ninguém confere na hora da apresentação.
    """
    do_yaml = _modelos_da_bateria_principal()[chave]
    config = pg.MODELOS[chave]
    for campo in (
        "model_id", "served_by", "quantization", "temperature",
        "max_tokens", "structured_output", "context_window",
    ):
        assert getattr(config, campo) == do_yaml[campo], f"{chave}.{campo}"
    assert pg.HONRA_SEED[chave] == do_yaml.get("honra_seed", True)


def test_a_seed_da_consulta_e_uma_das_seeds_da_bateria_principal():
    """Uma consulta é uma repetição das oito, não uma nona sorteada na hora."""
    do_yaml = yaml.safe_load(
        (RAIZ / "configs" / "bateria_principal.yaml").read_text(encoding="utf-8")
    )
    assert pg.SEED_DA_CONSULTA in do_yaml["sample_seeds"]


# ---------------------------------------------------------------------------
# 5. O cenário ad-hoc não vira cenário do corpus
# ---------------------------------------------------------------------------


def test_o_cenario_ad_hoc_e_carregavel_pelo_mesmo_leitor_do_corpus(tmp_path):
    """Se ele não carregasse aqui, a consulta rodaria por um caminho paralelo ao da bateria —
    e o que a tela mostrasse não seria o agente medido."""
    consulta = pg.preparar(
        "o motor principal está esquentando",
        raiz=tmp_path, user_id="usr_ana", asset_id="asset_M101",
        agora=datetime(2026, 9, 2, 15, 4, 17, tzinfo=UTC),
    )
    cenario = carregar_cenario_executavel(consulta.caminho)
    assert cenario.id == consulta.id
    assert cenario.env_seed == pg.ENV_SEED_PADRAO
    assert not cenario.inviavel


def test_o_cenario_ad_hoc_nasce_sem_gabarito(tmp_path):
    """Um gabarito escrito depois da resposta é gabarito ajustado ao resultado."""
    consulta = pg.preparar("está esquentando", raiz=tmp_path, user_id="usr_ana")
    documento = yaml.safe_load(consulta.caminho.read_text(encoding="utf-8"))
    for proibido in ("gabarito", "criterio_sucesso", "falhas_alvo", "estado_esperado", "politica"):
        assert proibido not in documento


def test_o_cenario_ad_hoc_nunca_entra_no_split_de_test(tmp_path):
    """`test` é o conjunto sobre o qual os números publicados foram calculados."""
    consulta = pg.preparar("está esquentando", raiz=tmp_path, user_id="usr_ana")
    assert yaml.safe_load(consulta.caminho.read_text(encoding="utf-8"))["split"] == "dev"


def test_duas_consultas_no_mesmo_segundo_com_textos_diferentes_nao_colidem(tmp_path):
    """`run_id` colidido sobrescreveria o trace da primeira — o defeito que `sample_seeds`
    repetidas já causaram uma vez no carregador de bateria."""
    agora = datetime(2026, 9, 2, 15, 4, 17, tzinfo=UTC)
    a = pg.preparar("o motor está esquentando muito", raiz=tmp_path, user_id="usr_ana", agora=agora)
    b = pg.preparar("o ventilador parou de girar", raiz=tmp_path, user_id="usr_ana", agora=agora)
    assert a.run_id != b.run_id


def test_o_identificador_nao_leva_acento_nem_espaco(tmp_path):
    """Ele vira nome de arquivo e pedaço de `run_id` em três lugares diferentes."""
    consulta = pg.preparar(
        "a análise está inconsistente", raiz=tmp_path, user_id="usr_ana",
        agora=datetime(2026, 9, 2, 15, 4, 17, tzinfo=UTC),
    )
    assert consulta.id.isascii()
    assert " " not in consulta.id


def test_pergunta_vazia_e_modelo_desconhecido_falham_antes_de_gastar_gpu(tmp_path):
    with pytest.raises(pg.ErroDeConsulta):
        pg.preparar("   ", raiz=tmp_path, user_id="usr_ana")
    with pytest.raises(pg.ErroDeConsulta):
        pg.preparar("oi", raiz=tmp_path, user_id="usr_ana", modelo="gpt-nao-existe")
    with pytest.raises(pg.ErroDeConsulta):
        pg.preparar("oi", raiz=tmp_path, user_id="")


def test_a_dispensa_do_judge_vem_com_motivo_escrito(tmp_path):
    """`Bateria` aceita a dispensa, não a omissão — e o motivo é o que separa "esta consulta não
    precisa" de "esqueci de declarar"."""
    consulta = pg.preparar("está esquentando", raiz=tmp_path, user_id="usr_ana")
    bateria = pg._bateria(tmp_path, pg._celula(consulta.caminho, "qwen3-8b"), timeout_s=60)
    assert isinstance(bateria.judge, DispensaDeCongelamento)
    assert "gabarito" in bateria.judge.motivo


# ---------------------------------------------------------------------------
# 6. A página gravada continua sem saber executar
# ---------------------------------------------------------------------------


def test_a_pagina_do_make_app_sai_sem_o_campo_ao_vivo():
    """No palco ela abre por `file://`. Um `ao_vivo` vazado faria o botão chamar `/api/perguntar`
    num servidor que não existe, e a demonstração morreria no clique."""
    from tapieval.app import gerar as gerador

    dados = {"rotulos": {"cenarios": {}, "modelos": {}}, "cenarios": {},
             "totais": {"execucoes": 0, "sem_decisao": 0, "perguntas": 0},
             "codigos_que_exigem_judge": [], "placar": []}
    html = gerador.montar_html(dados)
    carga = json.loads(html.split('id="dados">')[1].split("</script>")[0].replace("<\\/", "</"))
    assert "ao_vivo" not in carga


def test_a_pagina_servida_declara_o_alcance_da_medicao_sem_gabarito():
    """Os dois números que a tela cita — 4 e 15 — saem do módulo, não do template.

    Digitados no HTML, eles sobreviveriam intactos a um código novo na taxonomia, e a página
    passaria a afirmar uma cobertura que o instrumento já não tem.
    """
    template = (RAIZ / "src" / "tapieval" / "app" / "pagina.html").read_text(encoding="utf-8")
    assert "codigos_do_trace.length" in template
    assert "VIVO.n_nao_medidos" in template


def test_o_servidor_recusa_a_segunda_consulta_enquanto_a_primeira_corre(tmp_path, monkeypatch):
    """Medido na piloto: duas em paralelo levaram 446 s contra 222 s e perderam uma run."""
    estado = sv.Estado(
        raiz=tmp_path, api_base_url="http://x", inferencia_base_url="http://y", pagina="",
    )
    # A thread é substituída: o teste é sobre a trava, não sobre a GPU.
    parada = type("ThreadParada", (), {"start": lambda self: None})
    monkeypatch.setattr(sv.threading, "Thread", lambda **kw: parada())
    sv.perguntar(estado, {"texto": "o motor está esquentando", "user_id": "usr_ana"})
    with pytest.raises(sv.ErroDoServidor, match="uma por vez"):
        sv.perguntar(estado, {"texto": "e o ventilador?", "user_id": "usr_ana"})


def test_o_servidor_recusa_pergunta_maior_que_a_janela(tmp_path):
    estado = sv.Estado(
        raiz=tmp_path, api_base_url="http://x", inferencia_base_url="http://y", pagina="",
    )
    with pytest.raises(pg.ErroDeConsulta, match="limite"):
        sv.perguntar(estado, {"texto": "a" * (sv.LIMITE_DA_PERGUNTA + 1), "user_id": "usr_ana"})


def test_execucao_desconhecida_e_erro_e_nao_tela_vazia(tmp_path):
    """Uma tela vazia leria como "a run não fez nada", que é a leitura mais errada possível."""
    estado = sv.Estado(
        raiz=tmp_path, api_base_url="http://x", inferencia_base_url="http://y", pagina="",
    )
    with pytest.raises(sv.ErroDoServidor):
        sv.execucao(estado, "nao_existe")


def test_as_precondicoes_dizem_o_comando_que_levanta_cada_servico():
    """Descobrir que o LM Studio caiu depois de digitar a pergunta na frente de alguém é o pior
    momento possível — e a mensagem tem de dizer o que fazer, não só o que faltou."""
    problemas = sv.conferir_precondicoes(
        api_base_url="http://127.0.0.1:9", inferencia_base_url="http://127.0.0.1:9/v1",
    )
    assert len(problemas) == 2
    assert any("make api" in p for p in problemas)
    assert any("lms load" in p for p in problemas)
