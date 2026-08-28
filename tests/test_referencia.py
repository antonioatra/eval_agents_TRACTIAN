"""R5 — o cliente do SUT de referência, o teto de leitura de `ARQUITETURA §13`.

Quatro propriedades são o critério de pronto, e três delas são sobre coisas que falham em
SILÊNCIO se ninguém as prender aqui:

1. **A barreira contra o modelo do judge é erro, não convenção.** Configurar o SUT de
   referência com o `MODELO_PADRAO` do judge faz o judge julgar a si mesmo exatamente na
   única linha que serve de teto — e a bateria rodaria inteira, gravaria 24 traces válidos e
   não acusaria nada. Nenhum campo do manifesto compara os dois ids.
2. **Toda chamada registra custo, com os tokens de raciocínio dentro.** O projeto já catalogou
   o erro de esquecer `registrar_llm` (`scoring/n3.py`, `tests/test_n3.py`): o eixo x de H0 vai
   a zero e nada mais no projeto nota, porque `CustoRecord` não distingue grátis de não
   medido. Aqui é pior, porque o `LLMCall` do trace **não tem campo** para token de raciocínio
   e este cliente é o único lugar onde ele sobrevive.
3. **A promessa de `sut/llm.py` continua de pé.** Este módulo existe para que o cliente local
   NÃO precise falar com a nuvem; um teste guarda que ele não passou a falar.
4. **O contrato é o `Inferencia` que o agente e o runner já consomem**, não um paralelo.

Nada aqui fala com a rede: `httpx.MockTransport` responde por ela e `time.sleep` é
substituído — o mesmo padrão de `tests/test_judge_llm.py`.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from tapieval.schema.custo import CAMADAS_FORA_DO_JULGAMENTO, MedidorDeCusto
from tapieval.schema.trace import ModelConfig
from tapieval.scoring.judge_llm import MODELO_PADRAO as MODELO_DO_JUDGE
from tapieval.sut.llm import ClienteDeInferencia, Inferencia
from tapieval.sut.referencia import (
    CAMADA_DE_CUSTO,
    ESPERAS_S,
    MODELO_PADRAO,
    TETO_DE_ESPERA_S,
    ClienteDeReferencia,
    ModeloDoJudgeComoSUT,
    RespostaDeReferencia,
    config_de_referencia,
    recusar_modelo_do_judge,
)

ESQUEMA = {
    "type": "object",
    "properties": {"acao": {"type": "string"}},
    "required": ["acao"],
    "additionalProperties": False,
}

MENSAGENS = [{"role": "user", "content": "qual é o ativo?"}]

RESPOSTA_VERTEX = {
    "choices": [{"message": {"content": '{"acao": "buscar_ativo"}'}, "finish_reason": "stop"}],
    "usage": {
        "prompt_tokens": 1200,
        "completion_tokens": 5,
        "total_tokens": 1381,
        # Medido em 25/08: o compat do Vertex DECLARA o número, e 5 contra 176 é a razão real
        # de uma chamada de controle — 35× mais raciocínio que resposta.
        "completion_tokens_details": {"reasoning_tokens": 176},
    },
}

CORPO_429_COM_ESPERA = '{"error": {"code": 429, "message": "Please retry in 90.0s"}}'


@pytest.fixture
def dorme(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Registra as esperas em vez de cumpri-las — o teste mede a DECISÃO, não a paciência."""
    esperas: list[float] = []
    monkeypatch.setattr(
        "tapieval.sut.referencia.time.sleep", lambda segundos: esperas.append(segundos)
    )
    return esperas


def cliente_com(
    roteiro: Callable[[httpx.Request], httpx.Response],
    *,
    run_id: str = "run_ref_01",
    modelo: ModelConfig | None = None,
) -> ClienteDeReferencia:
    """Um cliente que só sabe falar com o roteiro. `chave` explícita = AI Studio, sem ADC."""
    return ClienteDeReferencia(
        run_id,
        modelo,
        chave="fake",
        transport=httpx.MockTransport(roteiro),
    )


def responde_sempre(payload: dict[str, Any]) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _: httpx.Response(200, json=payload)


# ---------------------------------------------------------------------------
# 1. A barreira contra o modelo do judge
# ---------------------------------------------------------------------------


def test_configurar_o_sut_de_referencia_com_o_modelo_do_judge_e_erro_nomeado() -> None:
    """Juiz igual ao réu prefere as próprias respostas (`ARQUITETURA §13`).

    E o dano é invisível: a bateria roda inteira e grava 24 traces válidos. `model_id` do SUT
    e `judge_model` do `ScorerVersion` são campos diferentes do manifesto, e ninguém os
    compara — então ou a barreira levanta, ou o teto mede o judge concordando consigo mesmo.
    """
    with pytest.raises(ModeloDoJudgeComoSUT) as erro:
        config_de_referencia(model_id=MODELO_DO_JUDGE)

    assert MODELO_DO_JUDGE in str(erro.value)


def test_a_barreira_nao_escapa_pelo_prefixo_do_publisher_nem_pela_caixa() -> None:
    """`google/gemini-3.6-flash` é o MESMO modelo — o prefixo é detalhe de fio do Vertex.

    Uma barreira que comparasse strings cruas seria decorativa: o compat do Vertex exige o
    prefixo, então escrevê-lo no YAML é um erro plausível, não exótico.
    """
    for escrita in (
        f"google/{MODELO_DO_JUDGE}",
        MODELO_DO_JUDGE.upper(),
        f"  google/{MODELO_DO_JUDGE.title()}  ",
    ):
        with pytest.raises(ModeloDoJudgeComoSUT):
            recusar_modelo_do_judge(escrita)


def test_a_barreira_levanta_antes_de_qualquer_soquete() -> None:
    """Descobrir isto depois de 24 runs gravadas não serviria de nada.

    O transporte deste teste explode se alguém o tocar: a barreira tem de estar no construtor,
    antes de o cliente HTTP existir.
    """

    def nunca(_: httpx.Request) -> httpx.Response:
        raise AssertionError("o cliente saiu para a rede antes de conferir o modelo")

    modelo = config_de_referencia().model_copy(update={"model_id": MODELO_DO_JUDGE})

    with pytest.raises(ModeloDoJudgeComoSUT):
        ClienteDeReferencia(
            "run_ref_01", modelo, chave="fake", transport=httpx.MockTransport(nunca)
        )


def test_o_modelo_padrao_deste_modulo_nao_e_o_do_judge() -> None:
    """O default tem de passar pela própria barreira — senão ela é teatro."""
    assert MODELO_PADRAO != MODELO_DO_JUDGE
    recusar_modelo_do_judge(MODELO_PADRAO)


def test_a_barreira_aceita_outro_modelo_do_judge_declarado() -> None:
    """Uma bateria julgada por outro id precisa poder dizer qual é o dela.

    `config_do_judge(model_id=...)` existe; uma barreira presa ao `MODELO_PADRAO` deixaria
    passar exatamente o caso em que o judge foi trocado.
    """
    with pytest.raises(ModeloDoJudgeComoSUT):
        recusar_modelo_do_judge("gemini-3.7-flash", modelo_do_judge="gemini-3.7-flash")


# ---------------------------------------------------------------------------
# 2. Instrumentação de custo — nenhum ramo devolve resposta sem medir
# ---------------------------------------------------------------------------


def test_toda_chamada_registra_tokens_e_tokens_in_e_maior_que_zero() -> None:
    """O erro que o projeto já catalogou: esquecer `registrar_llm`.

    Sem esta asserção o eixo x de H0 vai a zero e **nada mais no projeto nota** — `CustoRecord`
    não distingue "grátis" de "não medido" (`scoring/n3.py`, `tests/test_n3.py`).
    """
    with cliente_com(responde_sempre(RESPOSTA_VERTEX)) as cliente:
        cliente.completar(MENSAGENS, ESQUEMA)

    assert cliente.medidor.tokens_in > 0
    assert cliente.medidor.tokens_in == 1200
    assert cliente.medidor.chamadas_llm == 1


def test_os_tokens_de_raciocinio_entram_no_custo_de_saida() -> None:
    """5 tokens de resposta e 176 de raciocínio: contar só os 5 subestima em ~36×.

    `MedidorDeCusto.registrar_llm` soma raciocínio em `tokens_out` porque, para H0, um token
    de raciocínio custa o mesmo que um de saída.
    """
    with cliente_com(responde_sempre(RESPOSTA_VERTEX)) as cliente:
        resposta = cliente.completar(MENSAGENS, ESQUEMA)

    assert resposta.tokens_raciocinio == 176
    assert cliente.medidor.tokens_out == 5 + 176


def test_o_custo_acumula_entre_as_chamadas_do_laco_react() -> None:
    """Uma run é um laço de ~8 passos; o custo do teto é o da run, não o do último passo."""
    with cliente_com(responde_sempre(RESPOSTA_VERTEX)) as cliente:
        cliente.completar(MENSAGENS, ESQUEMA)
        cliente.completar(MENSAGENS, ESQUEMA)
        cliente.completar(MENSAGENS, ESQUEMA)

    assert cliente.medidor.chamadas_llm == 3
    assert cliente.medidor.tokens_in == 3 * 1200
    assert cliente.medidor.tokens_out == 3 * (5 + 176)


def test_resposta_que_nao_valida_tambem_custa() -> None:
    """`parse_erro` é comportamento do modelo, e o servidor cobrou por ele igual.

    Um ramo de erro que devolvesse sem medir barataria o teto exatamente nas runs em que o
    modelo mais gastou — que são as que interessam.
    """
    payload = {
        "choices": [{"message": {"content": "não é JSON"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 900, "completion_tokens": 12, "total_tokens": 912},
    }

    with cliente_com(responde_sempre(payload)) as cliente:
        resposta = cliente.completar(MENSAGENS, ESQUEMA)

    assert resposta.parse_ok is False
    assert resposta.parse_erro is not None
    assert cliente.medidor.tokens_in == 900


def test_o_medidor_fecha_num_registro_valido_da_camada_do_sut() -> None:
    """A camada é identidade do custo, e vem por construção — não do call site.

    Acumular em `N3_cego`/`N3_com_trace` somaria o custo do SUJEITO dentro do custo do
    INSTRUMENTO, que é o eixo x de H0 andando para o lado confortável.
    """
    with cliente_com(responde_sempre(RESPOSTA_VERTEX), run_id="run_ref_07") as cliente:
        cliente.completar(MENSAGENS, ESQUEMA)

    registro = cliente.medidor.fechar()

    assert registro.run_id == "run_ref_07"
    assert registro.camada == CAMADA_DE_CUSTO == "SUT_referencia"
    assert registro.tokens_in == 1200
    assert registro.tokens_out == 181
    assert registro.minutos_humano is None


def test_a_camada_do_sut_esta_declarada_fora_do_julgamento() -> None:
    """Quem agregar INS.4 tem de conseguir excluí-la sem adivinhar o nome.

    O custo do sujeito não é ponto da curva custo × recall; somá-lo lá é o formato do X9.
    """
    assert CAMADA_DE_CUSTO in CAMADAS_FORA_DO_JULGAMENTO


def test_medidor_injetado_e_respeitado() -> None:
    """A costura existe para o teste e para quem quiser um acumulador por bateria.

    Ela é `keyword-only` e sem semântica de "não medir": não há valor que desligue a medição.
    """
    medidor = MedidorDeCusto("run_ref_09", CAMADA_DE_CUSTO)

    cliente = ClienteDeReferencia(
        "run_ref_09",
        chave="fake",
        transport=httpx.MockTransport(responde_sempre(RESPOSTA_VERTEX)),
        medidor=medidor,
    )
    with cliente:
        cliente.completar(MENSAGENS, ESQUEMA)

    assert medidor.tokens_in == 1200


# ---------------------------------------------------------------------------
# 3. Tokens de raciocínio — a regra do judge, sem desencontro
# ---------------------------------------------------------------------------


def test_o_declarado_vence_a_subtracao() -> None:
    """O Vertex declara; a subtração é reconstrução e supõe que `total` só tem três parcelas."""
    payload = {
        "choices": [{"message": {"content": "{}"}}],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 999,
            "completion_tokens_details": {"reasoning_tokens": 40},
        },
    }
    base = RespostaDeReferencia(
        texto="{}",
        conteudo={},
        parse_ok=True,
        parse_erro=None,
        prompt_tokens=10,
        completion_tokens=5,
        finish_reason="stop",
        latencia_ms=1,
    )

    assert RespostaDeReferencia.a_partir_de(base, payload).tokens_raciocinio == 40


def test_sem_o_campo_declarado_cai_na_subtracao() -> None:
    """O compat do AI Studio não declara: 21 + 228 contra 711 é a medição do A20 (24/08)."""
    payload = {"usage": {"prompt_tokens": 21, "completion_tokens": 228, "total_tokens": 711}}
    base = RespostaDeReferencia(
        texto="",
        conteudo=None,
        parse_ok=False,
        parse_erro="x",
        prompt_tokens=21,
        completion_tokens=228,
        finish_reason="stop",
        latencia_ms=1,
    )

    assert RespostaDeReferencia.a_partir_de(base, payload).tokens_raciocinio == 462


def test_raciocinio_nunca_e_negativo() -> None:
    """Zero é auditável contra `usage_ausente`; um número inventado não seria."""
    payload = {"usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 5}}
    base = RespostaDeReferencia(
        texto="",
        conteudo=None,
        parse_ok=False,
        parse_erro="x",
        prompt_tokens=10,
        completion_tokens=10,
        finish_reason="stop",
        latencia_ms=1,
    )

    assert RespostaDeReferencia.a_partir_de(base, payload).tokens_raciocinio == 0


# ---------------------------------------------------------------------------
# 4. O contrato — o mesmo `Inferencia` que o agente e o runner consomem
# ---------------------------------------------------------------------------


def test_completar_tem_a_mesma_assinatura_do_cliente_local() -> None:
    """Implementar o contrato, e não um paralelo, é o que deixa o `Agent` intocado.

    `Inferencia` é um Protocol (não `runtime_checkable`), então o que se compara é o que o
    Protocol exige: o nome e a ordem dos parâmetros de `completar`.
    """
    local = inspect.signature(ClienteDeInferencia.completar)
    referencia = inspect.signature(ClienteDeReferencia.completar)

    assert list(local.parameters) == list(referencia.parameters)


def test_o_cliente_serve_onde_o_protocolo_e_pedido() -> None:
    """Um `Inferencia` é `modelo: ModelConfig` + `completar(...)`. As duas pontas."""

    def usar(inferencia: Inferencia) -> ModelConfig:
        return inferencia.modelo

    with cliente_com(responde_sempre(RESPOSTA_VERTEX)) as cliente:
        assert isinstance(usar(cliente), ModelConfig)


def test_a_resposta_e_um_resposta_do_modelo() -> None:
    """O `Agent` lê `prompt_tokens`, `completion_tokens`, `texto` e `parse_ok` sem saber daqui."""
    from tapieval.sut.llm import RespostaDoModelo

    with cliente_com(responde_sempre(RESPOSTA_VERTEX)) as cliente:
        resposta = cliente.completar(MENSAGENS, ESQUEMA)

    assert isinstance(resposta, RespostaDoModelo)
    assert resposta.conteudo == {"acao": "buscar_ativo"}
    assert resposta.finish_reason == "stop"


# ---------------------------------------------------------------------------
# 5. A promessa oposta — este módulo sai para a rede, o cliente local não
# ---------------------------------------------------------------------------


def test_o_cliente_de_referencia_manda_authorization() -> None:
    """É a diferença inteira em relação a `sut/llm.py`, e ela é a razão do arquivo existir."""
    vistos: list[str | None] = []

    def roteiro(request: httpx.Request) -> httpx.Response:
        vistos.append(request.headers.get("Authorization"))
        return httpx.Response(200, json=RESPOSTA_VERTEX)

    with cliente_com(roteiro) as cliente:
        cliente.completar(MENSAGENS, ESQUEMA)

    assert vistos == ["Bearer fake"]


def test_o_cliente_local_continua_sem_falar_com_a_nuvem() -> None:
    """A promessa de `sut/llm.py` é estrutural, e esta task existe para NÃO a quebrar.

    Se um dia `ClienteDeInferencia` passar a montar `Authorization`, o SUT local ganha um
    caminho para a rede sem que nada mais quebre — e a bateria principal poderia medir a nuvem
    achando que mede a GPU do notebook.
    """
    vistos: list[str | None] = []

    def roteiro(request: httpx.Request) -> httpx.Response:
        vistos.append(request.headers.get("Authorization"))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    modelo = config_de_referencia().model_copy(update={"served_by": "lmstudio"})
    with ClienteDeInferencia(
        "http://local", modelo, transport=httpx.MockTransport(roteiro)
    ) as local:
        local.completar(MENSAGENS, ESQUEMA)

    assert vistos == [None]

    fonte = inspect.getsource(ClienteDeInferencia)
    assert "Authorization" not in fonte


# ---------------------------------------------------------------------------
# 6. O payload — `structured_output` é condição experimental, não política do cliente
# ---------------------------------------------------------------------------


def _corpo_enviado(modelo: ModelConfig | None = None) -> dict[str, Any]:
    import json

    capturado: dict[str, Any] = {}

    def roteiro(request: httpx.Request) -> httpx.Response:
        capturado.update(json.loads(request.content))
        return httpx.Response(200, json=RESPOSTA_VERTEX)

    with cliente_com(roteiro, modelo=modelo) as cliente:
        cliente.completar(MENSAGENS, ESQUEMA)
    return capturado


def test_json_schema_manda_a_gramatica_para_o_decodificador() -> None:
    """É o que impede `parse_erro` de virar variável do experimento (`ARQUITETURA §5`, d. 4)."""
    corpo = _corpo_enviado()

    assert corpo["response_format"]["type"] == "json_schema"
    assert corpo["response_format"]["json_schema"]["strict"] is True
    assert corpo["response_format"]["json_schema"]["schema"] == ESQUEMA


def test_structured_output_none_nao_manda_response_format() -> None:
    """`none` é condição experimental legítima — medir o custo de NÃO ter gramática.

    Forçar `json_schema` aqui, como o judge faz, apagaria a condição sem nada quebrar: o
    manifesto continuaria declarando o que o YAML disse.
    """
    modelo = config_de_referencia().model_copy(update={"structured_output": "none"})

    assert "response_format" not in _corpo_enviado(modelo)


def test_structured_output_grammar_levanta() -> None:
    """GBNF é do llama.cpp e não viaja no protocolo OpenAI. Quebrar > degradar em silêncio."""
    modelo = config_de_referencia().model_copy(update={"structured_output": "grammar"})

    with cliente_com(responde_sempre(RESPOSTA_VERTEX), modelo=modelo) as cliente:
        with pytest.raises(NotImplementedError):
            cliente.completar(MENSAGENS, ESQUEMA)


def test_a_temperatura_e_o_teto_de_saida_vao_no_fio() -> None:
    """O teto tem de ser lido sob a mesma condição de amostragem dos SUTs locais."""
    corpo = _corpo_enviado()

    assert corpo["temperature"] == 0.7
    assert corpo["max_tokens"] == 1200
    assert corpo["stream"] is False


def test_seed_so_vai_quando_o_servidor_a_honra() -> None:
    """O compat do Google não expõe `seed`; `None` é o que o schema reserva para isso."""
    assert "seed" not in _corpo_enviado()
    assert _corpo_enviado(config_de_referencia(seed=11))["seed"] == 11


def test_no_vertex_o_id_no_fio_leva_publisher_e_o_manifesto_nao() -> None:
    """`google/` é detalhe de fio. Deixá-lo vazar para `model_id` tornaria os manifestos da
    piloto e da bateria incomparáveis campo a campo por algo que não é do modelo."""
    import json

    capturado: dict[str, Any] = {}

    def roteiro(request: httpx.Request) -> httpx.Response:
        capturado.update(json.loads(request.content))
        return httpx.Response(200, json=RESPOSTA_VERTEX)

    cliente = ClienteDeReferencia(
        "run_ref_01",
        provedor="vertex",
        projeto="projeto-falso",
        credencial=lambda: "tok",
        transport=httpx.MockTransport(roteiro),
    )
    with cliente:
        cliente.completar(MENSAGENS, ESQUEMA)

    assert capturado["model"] == f"google/{MODELO_PADRAO}"
    assert cliente.modelo.model_id == MODELO_PADRAO
    assert cliente.modelo.served_by == "vertex_ai"


# ---------------------------------------------------------------------------
# 7. O laço de retentativa — dimensionado para a RUN, não para a chamada
# ---------------------------------------------------------------------------


def test_status_transitorio_e_retentado_e_a_espera_fica_no_teto(dorme: list[float]) -> None:
    """A espera pedida vale, mas com teto mais curto que o do judge.

    Lá a unidade é a chamada e 75 s cabem. Aqui a unidade é a RUN: obedecer 90 s dentro de um
    `timeout_s: 300` com ~8 chamadas troca uma chamada perdida por uma run perdida — e run
    perdida é célula faltante no manifesto.
    """
    tentativas: list[int] = []

    def roteiro(_: httpx.Request) -> httpx.Response:
        tentativas.append(1)
        if len(tentativas) == 1:
            return httpx.Response(429, text=CORPO_429_COM_ESPERA)
        return httpx.Response(200, json=RESPOSTA_VERTEX)

    with cliente_com(roteiro) as cliente:
        cliente.completar(MENSAGENS, ESQUEMA)

    assert dorme == [TETO_DE_ESPERA_S]
    assert len(tentativas) == 2
    assert cliente.eventos_de_limite[0]["status"] == 429
    assert cliente.eventos_de_limite[0]["run_id"] == "run_ref_01"


def test_sem_numero_na_resposta_vale_o_backoff_curto(dorme: list[float]) -> None:
    """No Vertex o 429 quase nunca traz `retry in` — o backoff fixo passa a ser o plano inteiro.

    Duas esperas e não as três do judge: 40 s de espera numa chamada de um laço de oito come o
    orçamento das outras sete.
    """

    def roteiro(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="backend unavailable")

    with cliente_com(roteiro) as cliente:
        with pytest.raises(httpx.HTTPStatusError):
            cliente.completar(MENSAGENS, ESQUEMA)

    assert dorme == list(ESPERAS_S)
    assert sum(dorme) <= 10.0


def test_requisicao_pendurada_e_retentada(dorme: list[float]) -> None:
    """O defeito que o canário achou em 25/08 com duas chamadas, antes da bateria existir.

    Uma chamada normal responde em ~6 s; 60 s pendurados não são lentidão, são requisição
    perdida. Sem isto no laço, uma run inteira morre — e vira célula faltante.
    """
    tentativas: list[int] = []

    def roteiro(request: httpx.Request) -> httpx.Response:
        tentativas.append(1)
        if len(tentativas) == 1:
            raise httpx.ReadTimeout("pendurou", request=request)
        return httpx.Response(200, json=RESPOSTA_VERTEX)

    with cliente_com(roteiro) as cliente:
        cliente.completar(MENSAGENS, ESQUEMA)

    assert dorme == [ESPERAS_S[0]]
    assert cliente.eventos_de_limite[0]["erro"] == "ReadTimeout"
    assert cliente.eventos_de_limite[0]["status"] is None


def test_erro_permanente_sobe_na_hora(dorme: list[float]) -> None:
    """400 não passa esperando. Retentar erro nosso só queima orçamento de run."""

    def roteiro(_: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text='{"error": {"message": "modelo inexistente"}}')

    with cliente_com(roteiro) as cliente:
        with pytest.raises(httpx.HTTPStatusError):
            cliente.completar(MENSAGENS, ESQUEMA)

    assert dorme == []


def test_o_portador_e_relido_a_cada_tentativa(dorme: list[float]) -> None:
    """O token do Vertex vale 1 h e a bateria roda a madrugada.

    Fixar o cabeçalho no `httpx.Client` faria o `google-auth` renovar sem que ninguém usasse a
    renovação — e a retentativa depois de uma espera pode ser a primeira chamada do outro lado
    do vencimento.
    """
    portadores = iter(["velho", "novo"])
    vistos: list[str | None] = []

    def roteiro(request: httpx.Request) -> httpx.Response:
        vistos.append(request.headers.get("Authorization"))
        if len(vistos) == 1:
            return httpx.Response(503, text="devagar")
        return httpx.Response(200, json=RESPOSTA_VERTEX)

    cliente = ClienteDeReferencia(
        "run_ref_01",
        provedor="vertex",
        projeto="projeto-falso",
        credencial=lambda: next(portadores),
        transport=httpx.MockTransport(roteiro),
    )
    with cliente:
        cliente.completar(MENSAGENS, ESQUEMA)

    assert vistos == ["Bearer velho", "Bearer novo"]
