"""tapieval — framework de avaliação de agentes industriais (Case Inteli × TRACTIAN).

Desenho geral em `docs/ARQUITETURA.md`, métricas e protocolo em `METRICAS.md`, corpus em
`CENARIOS.md`. Os subpacotes seguem a pilha de cinco camadas de `ARQUITETURA §1`:

    schema/   trace imutável, writer/reader, instrumentação de custo
    env/      cliente da API industrial, modelos do Swagger, injeção de falha
    mcp/      servidor MCP (a fronteira), gate de ação, instrumentação por notificação
    sut/      o sistema sob teste: sessão, agente ReAct, variantes e mutantes
    scoring/  N1 determinístico, N2 trajetória, N3 judge, gabarito relativo, concordância
    runner/   orquestração das baterias e CLI

O trace é imutável e os scores são derivados dele — trocar um scorer nunca exige
re-executar o agente.
"""

__version__ = "0.1.0"
