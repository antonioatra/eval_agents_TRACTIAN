"""T7 — injeção de falhas que a API do parceiro não produz.

POR QUE O INJETOR É PEQUENO
    Fault injection quase toda virou **escolha de seed**: o modo de retorno é função pura de
    `(seed, recurso, categoria)` (`ARQUITETURA §3.4`), então `partial`, `inconclusive`,
    `conflict` e `unavailable` se pedem à API, não se fabricam. O que sobra é o que a API
    nunca faz: não responder (timeout), responder com corpo que não é JSON, e responder 5xx.
    Três falhas — e é de propósito que sejam três.

POR QUE ELE É DETERMINÍSTICO E CONTÁVEL
    `quando` é o número da ocorrência, não uma probabilidade. Uma falha sorteada tornaria a
    run irreprodutível, e o framework inteiro é construído sobre "mesmo trace, mesmo score".
"""

from __future__ import annotations

import pytest

from tapieval.env.client import RawResponse
from tapieval.env.faults import FaultInjector, FaultSpec
from tapieval.env.status import classificar

RESPOSTA_OK = RawResponse(
    status_code=200,
    body={"mode": "complete", "notes": None, "data": {"asset_id": "asset_M101"}},
    latencia_ms=11,
)


def test_sem_spec_a_resposta_passa_intacta():
    injetor = FaultInjector([])
    assert injetor.aplicar("get_asset", RESPOSTA_OK) is RESPOSTA_OK


def test_timeout_vira_ausencia_de_resposta_com_a_latencia_do_estouro():
    """O cliente devolve `status_code=None` quando não houve resposta — o injetor imita isso.

    A latência é preservada e alta: o tempo até o timeout é o dado relevante quando a API
    não responde, e zerá-lo faria a run parecer rápida justamente onde ela travou.
    """
    injetor = FaultInjector([FaultSpec(tool="get_asset", falha="timeout", timeout_ms=10_000)])
    resultado = injetor.aplicar("get_asset", RESPOSTA_OK)

    assert resultado.status_code is None
    assert resultado.body["code"] == "TRANSPORT_ERROR"
    assert resultado.latencia_ms == 10_000


def test_corpo_malformado_nao_e_json_e_o_classificador_o_pega():
    """Um proxy no caminho devolve HTML. O par com T7 é o ponto do teste."""
    injetor = FaultInjector([FaultSpec(tool="get_baseline", falha="corpo_malformado")])
    resultado = injetor.aplicar("get_baseline", RESPOSTA_OK)

    assert resultado.status_code == 200
    assert isinstance(resultado.body, str)
    assert classificar("get_baseline", resultado).status == "INDISPONIVEL"


def test_http_500_preserva_o_status_e_o_corpo_de_erro():
    injetor = FaultInjector([FaultSpec(tool="get_asset", falha="http_500")])
    resultado = injetor.aplicar("get_asset", RESPOSTA_OK)

    assert resultado.status_code == 500
    assert classificar("get_asset", resultado).motivo == "HTTP_500"


def test_a_falha_atinge_so_a_tool_declarada():
    injetor = FaultInjector([FaultSpec(tool="get_asset", falha="http_500")])
    assert injetor.aplicar("get_baseline", RESPOSTA_OK) is RESPOSTA_OK


def test_curinga_atinge_todas_as_tools():
    injetor = FaultInjector([FaultSpec(tool="*", falha="http_500")])
    assert injetor.aplicar("get_baseline", RESPOSTA_OK).status_code == 500
    assert injetor.aplicar("get_model", RESPOSTA_OK).status_code == 500


def test_quando_dispara_na_ocorrencia_indicada_e_so_nela():
    """Falha intermitente sem sorteio: é a n-ésima chamada daquela tool que quebra.

    Serve ao cenário "tentou de novo e funcionou", que é o único caso em que retry faz
    sentido — o modo degradado da API, esse sim, é o mesmo em toda repetição.
    """
    injetor = FaultInjector([FaultSpec(tool="get_asset", falha="http_500", quando=2)])

    assert injetor.aplicar("get_asset", RESPOSTA_OK) is RESPOSTA_OK
    assert injetor.aplicar("get_asset", RESPOSTA_OK).status_code == 500
    assert injetor.aplicar("get_asset", RESPOSTA_OK) is RESPOSTA_OK


def test_o_contador_de_quando_e_por_tool():
    """Duas tools não compartilham contador: senão a ordem das chamadas de OUTRA tool
    decidiria onde a falha cai, e a run deixaria de ser reprodutível a partir da spec."""
    injetor = FaultInjector([FaultSpec(tool="get_asset", falha="http_500", quando=1)])

    assert injetor.aplicar("get_baseline", RESPOSTA_OK) is RESPOSTA_OK
    assert injetor.aplicar("get_asset", RESPOSTA_OK).status_code == 500


def test_a_primeira_spec_que_casa_vence():
    """Ordem explícita em vez de acúmulo: duas falhas na mesma resposta não compõem."""
    injetor = FaultInjector(
        [
            FaultSpec(tool="get_asset", falha="http_500"),
            FaultSpec(tool="*", falha="timeout"),
        ]
    )
    assert injetor.aplicar("get_asset", RESPOSTA_OK).status_code == 500


def test_falha_desconhecida_e_recusada_na_construcao():
    """Erro de digitação numa spec de falha não pode virar "nenhuma falha" em silêncio:
    a bateria rodaria inteira medindo o ambiente errado."""
    with pytest.raises(ValueError, match="falha desconhecida"):
        FaultInjector([FaultSpec(tool="get_asset", falha="desconecta_o_cabo")])


def test_specs_sao_imutaveis_e_o_injetor_nao_muta_a_lista_recebida():
    specs = [FaultSpec(tool="get_asset", falha="http_500")]
    injetor = FaultInjector(specs)
    specs.clear()

    assert injetor.aplicar("get_asset", RESPOSTA_OK).status_code == 500
    with pytest.raises(AttributeError):
        injetor.specs[0].falha = "timeout"  # type: ignore[misc]


def test_reset_zera_os_contadores_entre_runs():
    """Um injetor por bateria, não por run, é o uso provável — e sem `reset` a segunda run
    herdaria o contador da primeira e receberia a falha em outra chamada."""
    injetor = FaultInjector([FaultSpec(tool="get_asset", falha="http_500", quando=1)])

    assert injetor.aplicar("get_asset", RESPOSTA_OK).status_code == 500
    injetor.reset()
    assert injetor.aplicar("get_asset", RESPOSTA_OK).status_code == 500
