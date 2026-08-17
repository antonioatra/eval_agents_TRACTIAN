"""
Abertura da sessão MCP — o lado cliente da fronteira, nos dois transportes.

MESMO CÓDIGO DE SERVIDOR, DOIS TRANSPORTES (`ARQUITETURA §4.4`)
    Streams em memória na bateria: um servidor por run, isolamento perfeito de cache, cassete
    e chaves de idempotência sem custo de processo. Stdio na demo e para cliente de terceiro,
    porque é assim que um cliente MCP externo se conecta. A escolha é de quem abre a sessão;
    o `mcp/server.py` não conhece transporte.

O `seq` É DE UM PROCESSO, E ISSO DECIDE O X23
    Em memória, servidor e harness compartilham o mesmo `RunContext` — logo o mesmo contador —
    e os dois emissores caem numa ordem total única, que é o que `ARQUITETURA §5` (decisões 8
    e 9) exige do trace: `seq` monotônico, sem lacuna e sem empate.

    **Em stdio isso é impossível, e a decisão desta task é não fingir que é.** O servidor está
    em outro processo, com outro contador; qualquer numeração do lado do cliente colide com a
    dele. As saídas que consideramos e por que não servem:

    * *offset alto no harness* (`seq` do cliente começando em 100_000) — produz lacuna
      gigante, e lacuna invalida a run pela decisão 9. Pior: jogaria todo `final_answer` para
      depois de todo `tool_call`, e a N2 mede aderência causal comparando exatamente esses
      `seq` (`n2._aderencia_causal`). As precedências do tipo `depois: "decidir:*"` passariam
      a ser satisfeitas por construção — viés silencioso na direção que favorece o agente.
    * *`seq` negativo ou prefixado no harness* — mesma coisa com outra aritmética.
    * *devolver a numeração ao cliente por notificação* — exige o cliente cooperar, que é a
      premissa que o A13 derrubou.

    Então: **em stdio o trace é o da fronteira** — só o que o servidor vê (`tool_call`,
    `tool_result`, `gate`), que é precisamente o trace que um agente de terceiro produz e o
    que a T14 provou ser idêntico ao da bateria. O harness não numera nada e não escreve nada
    (`agent.TrilhaDeFronteira`). O que se perde é o que depende de evento de cliente: N1.4 lê
    a decisão dos atos observáveis em vez do `DecisionEvent`, e `parse_failures` e
    `n_iteracoes` da N2 não existem. **Nenhuma medição do trabalho depende disso**, porque a
    bateria inteira roda em memória (§4.4); stdio serve à demo e à prova de generalidade.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Literal

import anyio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.shared.memory import create_client_server_memory_streams

from tapieval.mcp.server import RunContext, criar_servidor

Transporte = Literal["memoria", "stdio"]


@asynccontextmanager
async def abrir_sessao(
    ctx: RunContext | None = None,
    transporte: Transporte = "memoria",
    *,
    comando: Sequence[str] | None = None,
) -> AsyncIterator[ClientSession]:
    """Uma sessão MCP inicializada, pronta para `list_tools` e `call_tool`.

    `ctx` é obrigatório em memória e **proibido** em stdio: lá o `RunContext` vive no outro
    processo, e aceitar um aqui daria a impressão de que o cache, o gate e o contador de `seq`
    deste lado valem alguma coisa. Não valem — a fronteira está do outro lado do pipe.

    Em stdio, `comando` é o processo servidor (`[sys.executable, "-m", ...]`). Sem default:
    qual servidor subir é escolha de quem monta a demo, e adivinhar um caminho de módulo aqui
    esconderia o erro de configuração até o meio da execução.
    """
    if transporte == "memoria":
        if ctx is None:
            raise ValueError(
                "transporte em memória exige o `RunContext` da run: é ele que carrega o "
                "cliente HTTP, o cache, o observador e o contador de `seq`."
            )
        async with _sessao_em_memoria(ctx) as sessao:
            yield sessao
        return

    if comando is None:
        raise ValueError(
            "transporte stdio exige `comando`: o servidor roda em outro processo e é ele "
            "que precisa ser lançado."
        )
    if ctx is not None:
        raise ValueError(
            "em stdio o `RunContext` é do processo do servidor (X23). Passar um aqui daria "
            "ao harness um contador de `seq` paralelo, e as duas séries colidiriam no mesmo "
            "trace — ver a docstring do módulo."
        )
    async with _sessao_por_stdio(comando) as sessao:
        yield sessao


@asynccontextmanager
async def _sessao_em_memoria(ctx: RunContext) -> AsyncIterator[ClientSession]:
    """Servidor e cliente no mesmo processo, ligados por streams de memória.

    O servidor roda numa task própria e é cancelado na saída. Cancelar em vez de esperar é o
    correto aqui: `Server.run` só termina quando o stream fecha, e uma run que acabou não tem
    mais nada a atender — esperar por educação penduraria a bateria em cada célula.
    """
    servidor = criar_servidor(ctx)
    async with create_client_server_memory_streams() as (do_cliente, do_servidor):
        async with anyio.create_task_group() as grupo:

            async def servir() -> None:
                await servidor.run(
                    *do_servidor,
                    servidor.create_initialization_options(),
                    # Exceção de handler sobe em vez de virar erro de protocolo silencioso:
                    # bug nosso no servidor tem de derrubar a run, não devolver `is_error`
                    # ao agente e ser contado como indisponibilidade do ambiente.
                    raise_exceptions=True,
                )

            grupo.start_soon(servir)
            async with ClientSession(*do_cliente) as sessao:
                await sessao.initialize()
                yield sessao
            grupo.cancel_scope.cancel()


@asynccontextmanager
async def _sessao_por_stdio(comando: Sequence[str]) -> AsyncIterator[ClientSession]:
    """Cliente falando o protocolo de verdade com um servidor em outro processo.

    `errlog=sys.stderr` é o default do SDK e fica explícito: o stderr do servidor é a única
    janela de diagnóstico quando o handshake falha, e redirecioná-lo para lugar nenhum
    transformaria "o servidor morreu ao subir" em "o cliente ficou esperando".
    """
    parametros = StdioServerParameters(command=comando[0], args=list(comando[1:]))
    async with stdio_client(parametros, errlog=sys.stderr) as (leitura, escrita):
        async with ClientSession(leitura, escrita) as sessao:
            await sessao.initialize()
            yield sessao
