"""
A porta por onde o SUT DE REFERÊNCIA fala com a nuvem — a promessa INVERSA à de `sut/llm.py`.

POR QUE UM TERCEIRO CLIENTE, E NÃO UM CAMPO A MAIS NO DO SUT (R5)
    `sut/llm.py` declara em docstring que "não conhece provedor pago, não lê chave de API de
    ambiente nenhum e não tem fallback para nuvem", e `ClienteDeInferencia._corpo` não monta
    `Authorization`. Essa promessa é estrutural: é ela que garante que o SUT medido é o modelo
    local que o manifesto declara. Alargá-la para caber o SUT de referência abriria caminho
    para QUALQUER SUT sair para a rede sem que nada quebrasse — e aí a bateria principal
    poderia medir a nuvem achando que mede a GPU do notebook.

    Este módulo é para o SUT de referência o que `scoring/judge_llm.py` é para o judge: a
    exceção nomeada, isolada num arquivo só, com a promessa oposta escrita no topo.
    **Este módulo SAI para a rede, lê credencial do ambiente e fala com provedor pago.**

    O que ele reaproveita dos dois vizinhos é o que é protocolo, nunca o que é política:

    * de `sut/llm.py` — `RespostaDoModelo`, `_interpretar`, `NOME_DO_ESQUEMA` e o contrato
      `Inferencia`, que é o que o `Agent` e o runner consomem (`sut/agent.py`, `runner/
      runner.py:FabricaDeInferencia`). Implementar o mesmo Protocol, e não um paralelo, é o
      que permite apontar uma célula para cá sem tocar no agente;
    * de `scoring/judge_llm.py` — o encanamento do endpoint OpenAI-compatible do Google
      (URL do Vertex, portador renovável, leitura de chave, `espera_pedida`). Isso não é
      política do judge, é detalhe de fio do provedor; **e a duplicação seria pior que a
      dependência**, porque foi um canário que descobriu que o laço de retentativa precisava
      cobrir `httpx.TransportError` (`docs/migracao_vertex.md §7`).

    Que a `sut/` passe a depender de `scoring/` por isso é uma inversão de camada real, e
    está anotada como achado: o lugar certo desse encanamento é um `tapieval/provedores/
    google.py` que os dois clientes importem. A extração toca `scoring/`, que esta task não
    pode tocar.

O SUT DE REFERÊNCIA NÃO PODE SER O MODELO DO JUDGE — E ISSO É ERRO, NÃO CONVENÇÃO
    `ARQUITETURA §13` põe o judge com o critério "o maior disponível, **≠ dos SUTs**", pelo
    motivo escrito lá: juiz igual ao réu prefere as próprias respostas. O `MODELO_PADRAO` do
    judge é `gemini-3.6-flash`. Se o SUT de referência fosse o mesmo id, o judge julgaria a si
    mesmo **exatamente na única linha que serve de teto de leitura** — e o teto deixaria de
    medir o que existe para medir a preferência do juiz por si.

    O argumento é bom demais para depender de disciplina de quem escreve o YAML:
    `ModeloDoJudgeComoSUT` levanta no construtor, comparando com o prefixo `google/` removido
    e sem diferenciar maiúsculas, para que `google/Gemini-3.6-Flash` não escape pela borda.

    **O que a barreira NÃO cobre, e é limitação declarada:** ela compara IDs, não famílias.
    `gemini-3.7-flash` julgado por `gemini-3.6-flash` continua sendo dois modelos da mesma
    família, e a auto-preferência não cai a zero — só deixa de ser identidade. Cobrir isso
    exigiria um judge de outro fornecedor, que é decisão de projeto e de orçamento.

TOKENS DE RACIOCÍNIO SÃO CUSTO, E AQUI ELES SOMEM DO TRACE SE NINGUÉM OS MEDIR
    O `usage` do compat do Vertex declara `completion_tokens_details.reasoning_tokens`; o do
    AI Studio só entrega pela subtração `total - prompt - completion` (A20, `docs/
    migracao_vertex.md §4`). `RespostaDeReferencia` usa a MESMA regra do `RespostaDoJudge`,
    chamada e não recopiada: prefere o declarado, cai na subtração, nunca devolve negativo.

    Numa chamada real medida em 25/08, `completion=5` contra `reasoning=176` — **35× mais
    tokens de raciocínio que de resposta**. Ignorá-los subestimaria o custo de saída em quase
    uma ordem de grandeza.

    **E o trace não os salva.** `LLMCall` (`schema/trace.py`) tem `prompt_tokens` e
    `completion_tokens` e mais nada, e `Agent._registrar_llm` grava o que o servidor chamou
    de `completion_tokens`. Para os SUTs locais isso é a conta inteira; para um modelo de
    fronteira com raciocínio interno é ~1/35 dela. Por isso o medidor deste módulo **não é
    redundante com o trace: ele é o único lugar onde os tokens de raciocínio sobrevivem.**

INSTRUMENTAÇÃO DE CUSTO POR CONSTRUÇÃO, NÃO POR DISCIPLINA
    `run_id` é obrigatório no construtor e o `MedidorDeCusto` é montado aqui dentro, com a
    camada certa. Não há caminho em que alguém instancie o cliente e esqueça de medir.

    Isso não é zelo: o projeto já catalogou o erro (`scoring/n3.py`, e `tests/test_n3.py`).
    Esquecer `registrar_llm` põe o eixo x de H0 em zero e **nenhum teste pega**, porque
    `CustoRecord` não distingue "grátis" de "não medido".

    O cliente NÃO escreve o `CustoRecord` em disco. `CustoWriter` grava em
    `scores/<scorer_version>/`, e o custo do SUT não tem versão de scorer — ele não é custo
    de julgar, é custo do sujeito julgado. Quem quiser persistir chama `medidor.fechar()` e
    decide onde. O acumulador fica exposto em `self.medidor`.

O QUE AINDA IMPEDE A BATERIA DE RODAR (achados da R5, fora do território desta task)
    1. `runner/runner.py:_fabrica_padrao` monta SEMPRE um `ClienteDeInferencia` apontado para
       `bateria.inferencia_base_url` (default `http://127.0.0.1:1234/v1`), e a CLI
       (`runner/cli.py`) não expõe `fabrica_de_inferencia`. A costura existe e está documentada
       ("é a mesma costura que a T26c usa para apontar uma célula ao SUT de referência sem
       tocar no runner") — **mas nenhum caminho de linha de comando a alcança**, e
       `docs/dimensionamento.md §7` manda rodar esta bateria exatamente pela CLI. O conserto
       mínimo é `_fabrica_padrao` despachar por `celula.modelo.config.served_by`; ele toca
       `runner/`, que esta task não pode tocar.
    2. `configs/judge_frozen.json` não existe. Teto pontuado por outra rubrica não é teto.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import TracebackType
from typing import Any

import httpx

from tapieval.schema.custo import CamadaJulgamento, MedidorDeCusto
from tapieval.schema.trace import ModelConfig
from tapieval.scoring.judge_llm import (
    AI_STUDIO,
    BASE_URL_AI_STUDIO,
    LOCAL_VERTEX_PADRAO,
    PREFIXO_DO_PUBLISHER,
    SERVIDO_POR_POR_PROVEDOR,
    STATUS_TRANSITORIOS,
    VERTEX,
    ChaveAusente,
    RespostaDoJudge,
    base_url_do_vertex,
    credencial_do_vertex,
    credencial_estatica,
    espera_pedida,
    ler_chave,
    ler_projeto,
)
from tapieval.scoring.judge_llm import MODELO_PADRAO as MODELO_DO_JUDGE
from tapieval.sut.llm import (
    CAMINHO_DE_COMPLETACAO,
    NOME_DO_ESQUEMA,
    RespostaDoModelo,
    _interpretar,
)

CAMADA_DE_CUSTO: CamadaJulgamento = "SUT_referencia"
"""A camada sob a qual o custo deste cliente é acumulado.

Fica numa constante e não espalhada pelos call sites porque a camada é identidade do custo:
`MedidorDeCusto` aceita qualquer valor do Literal, e um cliente de SUT que acumulasse em
`N3_com_trace` somaria o custo do SUJEITO dentro do custo do INSTRUMENTO — que é o eixo x de
H0 andando para o lado que favorece a conclusão que o trabalho quer defender."""

PROVEDOR_PADRAO = VERTEX
"""Vertex, e não a free tier que `ARQUITETURA §13` e o cabeçalho do YAML ainda nomeiam.

`docs/limites_free_tier.md §5` mediu: a free tier dá **20 chamadas por DIA**. Esta bateria são
24 runs × ~8 chamadas ≈ 200, ou seja **10 dias** — o mesmo bloqueio de cronograma que tirou o
judge de lá em 25/08 (`docs/migracao_vertex.md`). O Vertex serve por dynamic shared quota, sem
RPD, e o crédito de trial o paga.

Consequência que o cabeçalho de `configs/bateria_referencia.yaml` registra: o "bloqueio 3"
daquele arquivo — 200 + 1.400 não cabem no mesmo dia de RPD — **deixa de existir** no Vertex,
porque não há RPD. O que sobra é o teto do DSQ, que `docs/migracao_vertex.md §8` declara
desconhecido."""

MODELO_PADRAO = "gemini-3.7-flash"
"""O único id que o projeto MEDIU como chamável e que **não** é o modelo do judge.

`docs/catalogo_vertex.json` (25/08) testou seis ids contra o Vertex em `global`. Responderam
200 exatamente dois: `gemini-3.6-flash` (o judge — proibido aqui pela barreira) e
`gemini-3.7-flash`. Todos os ids datados responderam 404 nos dois provedores.

O QUE ISTO CUSTA, E PRECISA DE RATIFICAÇÃO
    `configs/bateria_referencia.yaml` declara `gemini-3.6-pro`, escolhido em
    `docs/dimensionamento.md §6.6` sob a premissa de free tier (Pro cabia no RPD das ~200
    chamadas; Flash era do judge por causa das ~1.400). Essa premissa morreu com a migração,
    e **o Pro nunca foi conferido contra o catálogo** — não está em `catalogo_vertex.json`.

    Um *flash* como teto é um teto MAIS BAIXO que um *pro*. Isso encolhe a distância entre o
    modelo de fronteira e os Qwen3 8B/14B locais, ou seja, faz os SUTs locais parecerem
    relativamente melhores — viés na direção confortável. **Tem de ser declarado ao ler a
    figura**, e a saída limpa é conferir o Pro com `scripts/checar_catalogo_vertex.py` antes
    da noite da execução.

Este default só vale para quem constrói o cliente sem `ModelConfig`; na bateria quem manda é
o `model_id` da célula, que vem do YAML."""

JANELA_DO_MODELO_PADRAO = 1_048_576
"""`inputTokenLimit` da linha 3.x, o mesmo registrado para o judge. TAPI §9 exige declarar."""

TIMEOUT_PADRAO_S = 60.0
"""Sessenta segundos, não os 120 do judge nem os 300 do SUT local.

Os três números medem coisas diferentes. O SUT local espera GPU de notebook (dezenas de
segundos por passo, `sut/llm.py`). O judge faz UMA chamada por execução e pode esperar. Aqui é
um laço ReAct de ~8 chamadas dentro do `timeout_s: 300` da run: são ~37 s de orçamento por
chamada, contra ~6 s de latência normal medida no canário. Uma chamada que não respondeu em
60 s não está lenta — está perdida (`docs/migracao_vertex.md §7`), e deixar 120 s pendurados
consumiria sozinha 40% da run."""

ESPERAS_S = (2.0, 8.0)
"""Duas retentativas, 10 s de espera no total — e não as três de 40 s do judge.

Mesmo motivo do timeout: o backoff do judge cabe porque lá a unidade é a chamada; aqui a
unidade é a RUN inteira, e 40 s de espera numa chamada de um laço de oito come o orçamento das
outras sete. Duas tentativas cobrem o modo de falha medido (uma requisição pendurada em ~seis,
`docs/migracao_vertex.md §8`); a terceira compraria pouco e custaria a run."""

TETO_DE_ESPERA_S = 30.0
"""Teto para a espera que a própria API pede, também mais curto que os 75 s do judge.

Na free tier o 429 vinha com `Please retry in ...s` e honrá-lo era a coisa certa. No Vertex ele
quase nunca traz número (`docs/migracao_vertex.md §8`). Quando trouxer, obedecer 75 s dentro de
uma run de 300 s troca uma chamada perdida por uma run perdida — e run perdida é célula
faltante no manifesto, que é pior."""


class ModeloDoJudgeComoSUT(ValueError):
    """O SUT de referência foi configurado com o mesmo modelo do judge.

    Erro nomeado e levantado no construtor porque o dano é silencioso: a bateria roda inteira,
    grava 24 traces perfeitamente válidos, e o número que sai é o judge concordando consigo
    mesmo. Nada no manifesto denuncia isso — `model_id` do SUT e `judge_model` do
    `ScorerVersion` são campos diferentes, e ninguém os compara.

    É o mesmo formato de falha do X9 e do A10: o denominador mente para o lado que favorece o
    instrumento, e a única linha que serve de teto de leitura é justamente a afetada.
    """


def _sem_publisher(model_id: str) -> str:
    """`google/gemini-3.6-flash` e `gemini-3.6-flash` são o mesmo modelo.

    O prefixo é detalhe de fio do compat do Vertex (`judge_llm.PREFIXO_DO_PUBLISHER`) e não
    entra no manifesto — mas nada impede alguém de escrevê-lo no YAML, e uma barreira que
    escapasse por essa borda seria decorativa.
    """
    bruto = model_id.strip().lower()
    prefixo = PREFIXO_DO_PUBLISHER.lower()
    return bruto[len(prefixo) :] if bruto.startswith(prefixo) else bruto


def recusar_modelo_do_judge(model_id: str, modelo_do_judge: str = MODELO_DO_JUDGE) -> None:
    """Levanta `ModeloDoJudgeComoSUT` quando os dois ids nomeiam o mesmo modelo."""
    if _sem_publisher(model_id) == _sem_publisher(modelo_do_judge):
        raise ModeloDoJudgeComoSUT(
            f"o SUT de referência foi configurado com {model_id!r}, que é o modelo do judge "
            f"({modelo_do_judge!r}). O judge julgaria a si mesmo na única linha que serve de "
            "teto de leitura, e `ARQUITETURA §13` exige judge ≠ dos SUTs justamente porque "
            "juiz igual ao réu prefere as próprias respostas. Escolha outro modelo de "
            "fronteira (ver `docs/catalogo_vertex.json`) ou troque o modelo do judge."
        )


def config_de_referencia(
    model_id: str = MODELO_PADRAO,
    *,
    provedor: str = PROVEDOR_PADRAO,
    temperature: float = 0.7,
    max_tokens: int = 1200,
    seed: int | None = None,
    context_window: int = JANELA_DO_MODELO_PADRAO,
) -> ModelConfig:
    """A `ModelConfig` do SUT de referência, para o manifesto (TAPI §9).

    `temperature=0.7` e `max_tokens=1200` são os dos SUTs locais em
    `configs/bateria_principal.yaml`, de propósito: o teto tem de ser lido sob a mesma
    condição de amostragem e o mesmo teto de saída, senão a diferença medida inclui a
    diferença de configuração. `temperature=0.0` aqui mediria outra coisa que não o teto.

    `seed=None` é o default porque o compat do Google não expõe `seed` — o mesmo tratamento
    que o `qwen3-8b` recebe pelo mesmo motivo (`honra_seed: false` no YAML).
    """
    recusar_modelo_do_judge(model_id)
    return ModelConfig(
        model_id=model_id,
        served_by=SERVIDO_POR_POR_PROVEDOR[provedor],
        quantization=None,
        temperature=temperature,
        max_tokens=max_tokens,
        seed=seed,
        structured_output="json_schema",
        context_window=context_window,
    )


@dataclass(frozen=True)
class RespostaDeReferencia(RespostaDoModelo):
    """`RespostaDoModelo` mais os tokens de raciocínio, que o SUT local não reporta.

    Herda de `RespostaDoModelo` — e não de `RespostaDoJudge`, que tem a mesma forma — porque
    o papel é outro: isto é resposta de um SUJEITO sob avaliação, não do instrumento. Quem
    consome só `prompt_tokens` e `completion_tokens` (o `Agent`, via `Inferencia`) não precisa
    saber que este campo existe.
    """

    tokens_raciocinio: int = 0

    @classmethod
    def a_partir_de(
        cls, base: RespostaDoModelo, payload: Mapping[str, Any]
    ) -> RespostaDeReferencia:
        """A regra do judge, CHAMADA e não recopiada.

        Preferir o `reasoning_tokens` declarado, cair na subtração `total - prompt -
        completion` quando ele não vem e nunca devolver negativo é uma regra que custou duas
        medições para ficar de pé (A20 em 24/08, migração em 25/08). Recopiá-la aqui deixaria
        as duas livres para se desencontrarem na próxima correção — e o custo do SUT de
        referência e o do judge entram no MESMO eixo x de H0.
        """
        raciocinio = RespostaDoJudge.a_partir_de(base, payload).tokens_raciocinio
        return cls(
            texto=base.texto,
            conteudo=base.conteudo,
            parse_ok=base.parse_ok,
            parse_erro=base.parse_erro,
            prompt_tokens=base.prompt_tokens,
            completion_tokens=base.completion_tokens,
            finish_reason=base.finish_reason,
            latencia_ms=base.latencia_ms,
            usage_ausente=base.usage_ausente,
            tokens_raciocinio=raciocinio,
        )


class ClienteDeReferencia:
    """O SUT de referência falando com a nuvem. Satisfaz `Inferencia`, como o cliente local.

    Satisfazer o MESMO Protocol de `sut/llm.py` — e não um paralelo — é o que faz o `Agent`
    aceitar este cliente sem uma linha de mudança, e o que permite que
    `runner.FabricaDeInferencia` aponte uma célula para cá.

    A diferença de comportamento em relação ao `ClienteDoJudge` não é acidente: aqui
    `structured_output` é **condição experimental** e é honrado como o cliente local o honra
    (`none` e `prompt` não mandam `response_format`; `grammar` levanta), enquanto o judge
    força `json_schema` sempre. Lá o mecanismo de saída é do instrumento; aqui é do sujeito.
    """

    def __init__(
        self,
        run_id: str,
        modelo: ModelConfig | None = None,
        *,
        provedor: str | None = None,
        chave: str | None = None,
        credencial: Callable[[], str] | None = None,
        projeto: str | None = None,
        local: str = LOCAL_VERTEX_PADRAO,
        base_url: str | None = None,
        timeout_s: float = TIMEOUT_PADRAO_S,
        transport: httpx.BaseTransport | None = None,
        medidor: MedidorDeCusto | None = None,
        modelo_do_judge: str = MODELO_DO_JUDGE,
    ) -> None:
        """`run_id` é posicional e obrigatório: custo sem execução a que pertencer não é custo.

        `medidor` existe só para o teste inspecionar o acumulador; o caminho normal é o
        cliente montar o seu, com `CAMADA_DE_CUSTO`. Um default `None` que significasse "não
        medir" recriaria exatamente o erro que `scoring/n3.py` catalogou.

        `modelo_do_judge` é parametrizável porque a barreira compara com o `MODELO_PADRAO` do
        judge, e uma bateria julgada por outro id (`config_do_judge(model_id=...)`) precisa
        conseguir dizer qual é o dela.
        """
        # `chave` explícita significa AI Studio mesmo quando o padrão é Vertex — a mesma
        # convenção do `ClienteDoJudge`, e é assim que a suíte injeta portador falso.
        if provedor is None:
            provedor = AI_STUDIO if chave is not None else PROVEDOR_PADRAO
        if provedor not in SERVIDO_POR_POR_PROVEDOR:
            raise ValueError(f"provedor desconhecido: {provedor!r}")

        self.provedor = provedor
        self.modelo = modelo if modelo is not None else config_de_referencia(provedor=provedor)

        # Antes de qualquer soquete: a barreira é do desenho do experimento, e descobri-la
        # depois de 24 runs gravadas não serviria de nada.
        recusar_modelo_do_judge(self.modelo.model_id, modelo_do_judge)

        self.run_id = run_id
        self.medidor = (
            medidor if medidor is not None else MedidorDeCusto(run_id, CAMADA_DE_CUSTO)
        )

        if provedor == VERTEX:
            self.projeto: str | None = projeto if projeto is not None else ler_projeto()
            self.base_url = base_url or base_url_do_vertex(str(self.projeto), local)
            self._credencial = credencial or credencial_do_vertex()
        else:
            self.projeto = None
            self.base_url = base_url or BASE_URL_AI_STUDIO
            self._credencial = credencial or credencial_estatica(
                chave if chave is not None else ler_chave()
            )

        self._http = httpx.Client(base_url=self.base_url, timeout=timeout_s, transport=transport)

        self.eventos_de_limite: list[dict[str, Any]] = []
        """Todo status transitório recebido, com o instante e a espera que se seguiu.

        Mesma estrutura de `ClienteDoJudge`, e pelo mesmo motivo com um alvo diferente: o teto
        do dynamic shared quota do Vertex é declarado DESCONHECIDO em
        `docs/migracao_vertex.md §8`, e esta bateria é a primeira carga do projeto que roda
        um LAÇO na nuvem (~8 chamadas por run, 2 runs em paralelo) em vez de uma chamada
        isolada. Se houver parede, é aqui que ela aparece primeiro."""

    # -- o contrato `Inferencia` -------------------------------------------

    def completar(
        self,
        mensagens: Sequence[Mapping[str, str]],
        esquema: Mapping[str, Any],
    ) -> RespostaDeReferencia:
        """Um passo do modelo. Mesma assimetria do SUT local e do judge.

        `parse_erro` volta como dado (é comportamento do modelo sob avaliação e pertence ao
        trace); falha de transporte sobe, depois das retentativas (é falha do instrumento, e
        mascará-la de `parse_erro` inflaria com defeito nosso a métrica que separa os
        modelos — `ARQUITETURA §5`, decisão 4).

        Toda passada por aqui registra custo. Não há ramo que devolva resposta sem medir.
        """
        corpo = self._corpo(mensagens, esquema)

        inicio = time.perf_counter()
        resposta = self._postar_com_retentativa(corpo)
        latencia_ms = round((time.perf_counter() - inicio) * 1000)

        payload = resposta.json()
        base = _interpretar(payload, esquema, latencia_ms, enviado=corpo["messages"])
        final = RespostaDeReferencia.a_partir_de(base, payload)

        # Os tokens de raciocínio somam em `tokens_out` (`MedidorDeCusto.registrar_llm`), e
        # este é o ÚNICO lugar onde eles sobrevivem: o `LLMCall` do trace não tem campo para
        # eles, e `Agent._registrar_llm` grava só o `completion_tokens` do servidor.
        self.medidor.registrar_llm(
            final.prompt_tokens, final.completion_tokens, final.tokens_raciocinio
        )
        return final

    # -- o fio --------------------------------------------------------------

    def _corpo(
        self, mensagens: Sequence[Mapping[str, str]], esquema: Mapping[str, Any]
    ) -> dict[str, Any]:
        """O payload, com `structured_output` honrado como condição experimental.

        Copia deliberadamente a política de `ClienteDeInferencia._corpo` e não a do judge:
        `none` existe para medir o custo de NÃO ter gramática (`sut/llm.py`), e forçar
        `json_schema` aqui apagaria essa condição do modelo de referência sem que nada
        quebrasse — o manifesto continuaria declarando o que o YAML disse.
        """
        modelo = self.modelo
        id_no_fio = (
            f"{PREFIXO_DO_PUBLISHER}{modelo.model_id}"
            if self.provedor == VERTEX
            else modelo.model_id
        )
        corpo: dict[str, Any] = {
            "model": id_no_fio,
            "messages": [dict(mensagem) for mensagem in mensagens],
            "temperature": modelo.temperature,
            "max_tokens": modelo.max_tokens,
            "stream": False,
        }
        if modelo.top_p is not None:
            corpo["top_p"] = modelo.top_p
        if modelo.seed is not None:
            corpo["seed"] = modelo.seed

        if modelo.structured_output == "json_schema":
            corpo["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": NOME_DO_ESQUEMA,
                    "strict": True,
                    "schema": dict(esquema),
                },
            }
        elif modelo.structured_output == "grammar":
            raise NotImplementedError(
                "structured_output='grammar' não existe no compat do Google: GBNF é do "
                "llama.cpp e não viaja no protocolo OpenAI. Quebrar aqui é melhor que cair "
                "em `prompt` em silêncio e medir outro mecanismo do que o manifesto declara."
            )

        return corpo

    def _postar_com_retentativa(self, corpo: Mapping[str, Any]) -> httpx.Response:
        """POST com backoff curto. Erro permanente sobe na hora.

        O cabeçalho é montado A CADA tentativa e não no cliente: no Vertex o portador vence em
        1 h e `credencial_do_vertex` só renova se for chamado. Fixá-lo no `httpx.Client` faria
        o `google-auth` renovar sem que ninguém usasse a renovação.

        `httpx.TransportError` entra no laço junto com os status: foi assim que o canário de
        25/08 descobriu que uma requisição pendurada matava a rodada inteira
        (`docs/migracao_vertex.md §7`). Aqui o custo seria a RUN, que vira célula faltante.
        """
        ultima: httpx.Response | None = None
        for tentativa in range(len(ESPERAS_S) + 1):
            padrao = ESPERAS_S[tentativa] if tentativa < len(ESPERAS_S) else None
            try:
                resposta = self._http.post(
                    CAMINHO_DE_COMPLETACAO,
                    json=corpo,
                    headers={"Authorization": f"Bearer {self._credencial()}"},
                )
            except httpx.TransportError as erro:
                self._registrar_evento(
                    status=None,
                    erro=type(erro).__name__,
                    tentativa=tentativa,
                    espera=padrao,
                    pedida=None,
                    corpo_da_resposta=str(erro)[:600],
                )
                if padrao is None:
                    raise
                time.sleep(padrao)
                continue

            if resposta.status_code not in STATUS_TRANSITORIOS:
                resposta.raise_for_status()
                return resposta

            ultima = resposta
            pedida = espera_pedida(resposta.text)
            if pedida is not None:
                pedida = min(pedida, TETO_DE_ESPERA_S)
            espera = padrao if pedida is None else (pedida if padrao is not None else None)
            self._registrar_evento(
                status=resposta.status_code,
                erro=None,
                tentativa=tentativa,
                espera=espera,
                pedida=pedida,
                corpo_da_resposta=resposta.text[:600],
            )
            if espera is not None:
                time.sleep(espera)

        assert ultima is not None
        ultima.raise_for_status()
        raise AssertionError("inalcançável: raise_for_status já levantou")

    def _registrar_evento(
        self,
        *,
        status: int | None,
        erro: str | None,
        tentativa: int,
        espera: float | None,
        pedida: float | None,
        corpo_da_resposta: str,
    ) -> None:
        self.eventos_de_limite.append(
            {
                "instante": time.time(),
                "run_id": self.run_id,
                "status": status,
                "erro": erro,
                "tentativa": tentativa,
                "espera_s": espera,
                "espera_pedida_s": pedida,
                "corpo": corpo_da_resposta,
            }
        )

    # -- ciclo de vida ------------------------------------------------------

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> ClienteDeReferencia:
        return self

    def __exit__(
        self,
        classe: type[BaseException] | None,
        erro: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


__all__ = [
    "CAMADA_DE_CUSTO",
    "ESPERAS_S",
    "MODELO_PADRAO",
    "PROVEDOR_PADRAO",
    "TETO_DE_ESPERA_S",
    "TIMEOUT_PADRAO_S",
    "ChaveAusente",
    "ClienteDeReferencia",
    "ModeloDoJudgeComoSUT",
    "RespostaDeReferencia",
    "config_de_referencia",
    "recusar_modelo_do_judge",
]
