"""A porta do judge sob rate limit — o que a T21 descobriu batendo na free tier de verdade.

`tests/test_n3.py` cobre a mecânica do julgamento com um duplo de roteiro fixo, e nunca vê
transporte. O que falta é o comportamento do cliente quando o serviço do outro lado diz
"devagar": foi exatamente aí que a primeira calibração morreu, e foi um erro de raciocínio
sobre a janela, não um bug de digitação.

Nada aqui fala com a rede: `httpx.MockTransport` responde por ela, e `time.sleep` é
substituído para que a suíte não durma os 35 s que o teste descreve.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from tapieval.scoring.judge_llm import (
    AI_STUDIO,
    BASE_URL_AI_STUDIO,
    ESPERAS_S,
    MODELO_PADRAO,
    TETO_DE_ESPERA_S,
    VERTEX,
    ClienteDoJudge,
    base_url_do_vertex,
    espera_pedida,
)

CORPO_429 = json.dumps(
    [
        {
            "error": {
                "code": 429,
                "message": (
                    "You exceeded your current quota, please check your plan and billing "
                    "details. \n* Quota exceeded for metric: generativelanguage.googleapis."
                    "com/generate_content_free_tier_requests, limit: 20, model: "
                    "gemini-3.6-flash\nPlease retry in 35.52310516s."
                ),
                "status": "RESOURCE_EXHAUSTED",
            }
        }
    ]
)
"""O corpo literal que a API devolveu em 24/08, com a quota nomeada e a espera pedida.

É copiado do real e não inventado de propósito: o parser tem de sobreviver ao formato que o
Google manda mesmo, inclusive à lista externa e às quebras de linha no meio da mensagem."""

RESPOSTA_OK = {
    "choices": [{"message": {"content": '{"ok": true}'}}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 40},
}


@pytest.fixture
def dorme(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Registra as esperas em vez de cumpri-las — o teste mede a DECISÃO, não a paciência."""
    esperas: list[float] = []
    monkeypatch.setattr(
        "tapieval.scoring.judge_llm.time.sleep", lambda segundos: esperas.append(segundos)
    )
    return esperas


def cliente_com(roteiro: Callable[[httpx.Request], httpx.Response]) -> ClienteDoJudge:
    return ClienteDoJudge(chave="fake", transport=httpx.MockTransport(roteiro))


# ---------------------------------------------------------------------------
# `espera_pedida` — o número que o serviço manda
# ---------------------------------------------------------------------------


def test_espera_pedida_le_o_numero_que_a_api_manda() -> None:
    """35,52 s viram 36,52: o segundo a mais é margem contra a janela deslizante.

    Voltar no instante exato que o serviço citou é apostar que os dois relógios concordam.
    Concordam quase sempre — e "quase" é o que derruba bateria de madrugada."""
    assert espera_pedida(CORPO_429) == pytest.approx(36.52310516)


def test_espera_pedida_devolve_none_quando_a_resposta_nao_pede_nada() -> None:
    """Sem o número, quem decide é o backoff fixo. `None` é o que sinaliza isso."""
    assert espera_pedida('{"error": {"code": 503, "message": "backend unavailable"}}') is None


def test_espera_pedida_nao_passa_do_teto() -> None:
    """Espera pedida acima da janela de um minuto não é rate limit de minuto.

    É quota diária ou projeto suspenso — casos em que dormir o que foi pedido queima a noite
    inteira sem chance de sucesso. O teto transforma isso em falha rápida."""
    assert espera_pedida("Please retry in 86400.0s") == TETO_DE_ESPERA_S


# ---------------------------------------------------------------------------
# O cliente sob 429
# ---------------------------------------------------------------------------


def test_cliente_espera_o_que_a_api_pediu_e_nao_o_backoff_fixo(dorme: list[float]) -> None:
    """O bug que matou a primeira calibração, preso por teste.

    `ESPERAS_S` começa em 2 s. A resposta pede 35,5. Esperar 2 s aqui devolve a chamada para
    dentro da mesma janela que acabou de recusá-la, e as três esperas fixas somam 40 s — todas
    dentro de uma janela que o serviço disse durar 35 s a partir de um instante POSTERIOR ao
    início da nossa contagem. A quarta tentativa levanta, e a rodada morre."""
    tentativas: list[int] = []

    def roteiro(request: httpx.Request) -> httpx.Response:
        tentativas.append(1)
        if len(tentativas) == 1:
            return httpx.Response(429, text=CORPO_429)
        return httpx.Response(200, json=RESPOSTA_OK)

    with cliente_com(roteiro) as cliente:
        cliente.completar([{"role": "user", "content": "oi"}], {"type": "object"})

    assert dorme == [pytest.approx(36.52310516)], "esperou o backoff fixo, não o pedido"
    assert dorme[0] != ESPERAS_S[0]


def test_cliente_cai_no_backoff_fixo_quando_a_resposta_nao_pede_nada(
    dorme: list[float],
) -> None:
    """Um 503 sob carga não traz `retry in`. O backoff antigo continua sendo o plano B."""
    tentativas: list[int] = []

    def roteiro(request: httpx.Request) -> httpx.Response:
        tentativas.append(1)
        if len(tentativas) == 1:
            return httpx.Response(503, text="backend unavailable")
        return httpx.Response(200, json=RESPOSTA_OK)

    with cliente_com(roteiro) as cliente:
        cliente.completar([{"role": "user", "content": "oi"}], {"type": "object"})

    assert dorme == [ESPERAS_S[0]]


def test_eventos_de_limite_guardam_o_corpo_que_nomeia_a_quota(dorme: list[float]) -> None:
    """Sem este registro, o 429 some dentro do backoff — que o engole por desenho.

    E o corpo é a parte que importa: é ele que diz `generate_content_free_tier_requests,
    limit: 20`, que é a diferença entre saber QUE bateu no limite e saber em QUAL limite."""

    def roteiro(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text=CORPO_429)

    with cliente_com(roteiro) as cliente:
        with pytest.raises(httpx.HTTPStatusError):
            cliente.completar([{"role": "user", "content": "oi"}], {"type": "object"})
        eventos = cliente.eventos_de_limite

    assert len(eventos) == len(ESPERAS_S) + 1, "todo 429 vira evento, inclusive o último"
    assert all(evento["status"] == 429 for evento in eventos)
    assert "generate_content_free_tier_requests" in eventos[0]["corpo"]
    assert eventos[0]["espera_pedida_s"] == pytest.approx(36.52310516)
    assert eventos[-1]["espera_s"] is None, "a última tentativa não espera: ela levanta"


def test_sucesso_na_primeira_nao_gera_evento_de_limite(dorme: list[float]) -> None:
    """A lista vazia é informação: "rodou e nunca bateu no limite" precisa ser distinguível
    de "esqueci de medir", que é o formato de erro que o X9 nomeia."""

    def roteiro(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=RESPOSTA_OK)

    with cliente_com(roteiro) as cliente:
        cliente.completar([{"role": "user", "content": "oi"}], {"type": "object"})
        assert cliente.eventos_de_limite == []
    assert dorme == []


# ---------------------------------------------------------------------------
# A migração para o Vertex (25/08) — o que muda no fio e o que NÃO muda no manifesto
# ---------------------------------------------------------------------------


def test_base_url_do_vertex_nao_prefixa_a_regiao_global() -> None:
    """O falso negativo que fez a primeira sonda do catálogo dar 404 em tudo.

    `global` é servido por `aiplatform.googleapis.com` puro; toda outra região leva o prefixo.
    Montar `global-aiplatform...` produz um host que não existe, e o 404 resultante parece
    "o modelo não está lá" quando o problema é o endereço."""
    assert base_url_do_vertex("proj") == (
        "https://aiplatform.googleapis.com/v1/projects/proj/locations/global/endpoints/openapi"
    )
    assert base_url_do_vertex("proj", "us-central1").startswith(
        "https://us-central1-aiplatform.googleapis.com/"
    )


def test_o_prefixo_do_publisher_vai_no_fio_e_nao_no_manifesto(dorme: list[float]) -> None:
    """`google/` é detalhe de protocolo do compat do Vertex, não identidade do modelo.

    Se ele vazasse para `ModelConfig.model_id`, os manifestos da piloto (AI Studio) e da
    bateria (Vertex) deixariam de ser comparáveis campo a campo por uma diferença que não é
    do modelo. Quem declara o provedor é `served_by`, e é lá que a diferença deve aparecer."""
    enviados: list[str] = []

    def roteiro(request: httpx.Request) -> httpx.Response:
        enviados.append(json.loads(request.content)["model"])
        return httpx.Response(200, json=RESPOSTA_OK)

    with ClienteDoJudge(
        provedor=VERTEX,
        projeto="proj",
        credencial=lambda: "tok",
        transport=httpx.MockTransport(roteiro),
    ) as cliente:
        cliente.completar([{"role": "user", "content": "oi"}], {"type": "object"})
        assert cliente.modelo.model_id == MODELO_PADRAO, "o prefixo vazou para o manifesto"
        assert cliente.modelo.served_by == "vertex_ai"

    assert enviados == [f"google/{MODELO_PADRAO}"]


def test_o_portador_e_relido_a_cada_tentativa(dorme: list[float]) -> None:
    """O token do Vertex vale 1 h e a bateria da T24 roda a madrugada inteira.

    Fixar o cabeçalho no `httpx.Client` faria a renovação do `google-auth` acontecer sem que
    ninguém a usasse: as chamadas continuariam mandando o token velho até o fim da noite. O
    que se perde não é a chamada — são as runs, que ficam sem N3."""
    portadores = iter(["token-velho", "token-novo"])
    vistos: list[str] = []

    def roteiro(request: httpx.Request) -> httpx.Response:
        vistos.append(request.headers["Authorization"])
        if len(vistos) == 1:
            return httpx.Response(503, text="backend unavailable")
        return httpx.Response(200, json=RESPOSTA_OK)

    with ClienteDoJudge(
        provedor=VERTEX,
        projeto="proj",
        credencial=lambda: next(portadores),
        transport=httpx.MockTransport(roteiro),
    ) as cliente:
        cliente.completar([{"role": "user", "content": "oi"}], {"type": "object"})

    assert vistos == ["Bearer token-velho", "Bearer token-novo"]


def test_chave_explicita_continua_significando_ai_studio(dorme: list[float]) -> None:
    """A suíte inteira injeta `chave=` e não pode passar a exigir ADC por causa do default.

    Também é a garantia de que voltar para o AI Studio é uma linha, e não uma reversão."""

    def roteiro(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=RESPOSTA_OK)

    with cliente_com(roteiro) as cliente:
        assert cliente.provedor == AI_STUDIO
        assert cliente.modelo.served_by == "gemini_api"
        assert cliente.base_url == BASE_URL_AI_STUDIO


# ---------------------------------------------------------------------------
# A20 — o custo do raciocínio, declarado contra reconstruído
# ---------------------------------------------------------------------------


def test_tokens_de_raciocinio_preferem_o_numero_declarado(dorme: list[float]) -> None:
    """O compat do Vertex declara `reasoning_tokens`; o do AI Studio só entrega por subtração.

    Preferir o declarado importa porque este número entra em `tokens_out` e daí no eixo x de
    H0. A subtração assume que `total` não contém nada além das três parcelas — suposição que
    não é verificável do lado de cá. Aqui os dois discordam de propósito: 40-10-5 daria 25, e
    o teste exige o 7 que o serviço afirmou."""

    def roteiro(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"ok": true}'}}],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 40,
                    "completion_tokens_details": {"reasoning_tokens": 7},
                },
            },
        )

    with cliente_com(roteiro) as cliente:
        resposta = cliente.completar([{"role": "user", "content": "oi"}], {"type": "object"})

    assert resposta.tokens_raciocinio == 7


def test_tokens_de_raciocinio_caem_na_subtracao_quando_nao_ha_declaracao(
    dorme: list[float],
) -> None:
    """O caminho do AI Studio, que continua sendo o plano B e não pode regredir (A20)."""

    def roteiro(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=RESPOSTA_OK)

    with cliente_com(roteiro) as cliente:
        resposta = cliente.completar([{"role": "user", "content": "oi"}], {"type": "object"})

    assert resposta.tokens_raciocinio == 40 - 10 - 5


# ---------------------------------------------------------------------------
# Falha de transporte — o defeito que o canário encontrou em 25/08
# ---------------------------------------------------------------------------


def test_read_timeout_e_retentado_e_nao_mata_a_rodada(dorme: list[float]) -> None:
    """O laço só sabia retentar STATUS, e um `ReadTimeout` subia direto.

    Na free tier isso quase não aparecia: o modo de falha de lá era 429, que está tratado. No
    Vertex a chamada pendura, e foi assim que a primeira comparação do canário morreu — depois
    de a gravação da linha de base ter passado com as mesmas três chamadas.

    Alargar o timeout não seria conserto: a chamada normal responde em ~6 s, então 120 s
    pendurados não são lentidão, são uma requisição perdida. O que se perde sem a retentativa
    não é a chamada — são as runs, que ficam sem N3."""
    tentativas: list[int] = []

    def roteiro(request: httpx.Request) -> httpx.Response:
        tentativas.append(1)
        if len(tentativas) == 1:
            raise httpx.ReadTimeout("The read operation timed out", request=request)
        return httpx.Response(200, json=RESPOSTA_OK)

    with cliente_com(roteiro) as cliente:
        resposta = cliente.completar([{"role": "user", "content": "oi"}], {"type": "object"})

    assert resposta.parse_ok
    assert dorme == [ESPERAS_S[0]]
    assert len(cliente.eventos_de_limite) == 1
    assert cliente.eventos_de_limite[0]["erro"] == "ReadTimeout"
    assert cliente.eventos_de_limite[0]["status"] is None


def test_timeout_em_todas_as_tentativas_sobe(dorme: list[float]) -> None:
    """Retentar não é insistir para sempre: esgotadas as esperas, o erro sobe.

    E sobe COM o registro completo — os eventos são o que diz, depois, se a noite morreu por
    uma requisição pendurada ou por outra coisa."""

    def roteiro(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("The read operation timed out", request=request)

    with cliente_com(roteiro) as cliente:
        with pytest.raises(httpx.ReadTimeout):
            cliente.completar([{"role": "user", "content": "oi"}], {"type": "object"})

    assert len(cliente.eventos_de_limite) == len(ESPERAS_S) + 1
    assert all(evento["erro"] == "ReadTimeout" for evento in cliente.eventos_de_limite)
    assert cliente.eventos_de_limite[-1]["espera_s"] is None, "a última não espera: ela levanta"
