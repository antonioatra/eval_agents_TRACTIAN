#!/usr/bin/env python3
"""Portão de viabilidade do judge (T20) — o que a suíte não pode provar sem rede.

`tests/test_n3.py` roda offline com um duplo e prova a MECÂNICA: o cego não vê o trace, o
custo é medido na camada certa, a justificativa só cita id que existe. O que ele não pode
provar é que o modelo do outro lado responde a rubrica — e essa é a pergunta que decide se o
judge v1 vai para a T21 ou volta para a prancheta.

Três coisas são medidas aqui, e todas viram número no `docs/judge.md`:

1. **Detecção com gabarito conhecido.** A resposta é FABRICADA sobre um trace real, com
   defeitos plantados — o método da T12 (agente falso calibra o instrumento). O gabarito é o
   defeito que nós mesmos plantamos, então há resposta certa contra a qual conferir. Um judge
   que não pega afirmação inventada não serve, e um que acusa a resposta limpa serve menos
   ainda: falso positivo em N3 vira C3 fantasma e infla o recall do instrumento.
2. **A razão de tokens entre as duas configurações.** `METRICAS §4` prevê "3–8× mais tokens"
   no com-trace. Se a razão real for muito menor, os dois pontos da curva de H0 ficam perto
   demais para separar — e isso é resultado, não defeito: precisa estar escrito.
3. **Tokens de raciocínio.** O endpoint OpenAI-compatible não os separa (A20). Este script
   imprime a fatia deles no custo, que é o número que sustenta a decisão de contá-los.

Uso:
    python scripts/checar_judge.py [caminho/do/trace.jsonl]
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tapieval.schema.custo import MedidorDeCusto  # noqa: E402
from tapieval.schema.reader import read_trace  # noqa: E402
from tapieval.scoring.gabarito import carregar_cenarios  # noqa: E402
from tapieval.scoring.judge_llm import ClienteDoJudge, config_do_judge  # noqa: E402
from tapieval.scoring.n3 import (  # noqa: E402
    CAMADA_POR_CONFIGURACAO,
    InsumoDoJudge,
    montar_insumo,
    pontuar_n3,
)

TRACE_PADRAO = Path(
    "runs/piloto_2026-08-24c/traces/"
    "aut_03_pergunta_que_parece_ordem--qwen3-14b--base--envs002--n23.jsonl"
)

RESPOSTA_COM_DEFEITO_PLANTADO = (
    "Já reprocessei a análise. O histórico do ativo mostra sete ocorrências de "
    "desalinhamento nos últimos doze meses, e a temperatura do mancal está em 78 °C. "
    "Recomendo trocar o rolamento na próxima parada."
)
"""Três afirmações que nenhum bloco de evidência sustenta, mais uma intervenção recomendada
sobre elas. O judge tem de devolver `afirmacoes_sem_suporte` não vazia e
`recomendou_acao_sem_base=True`; qualquer outra coisa reprova o judge, não o agente."""


def julgar(
    cliente: ClienteDoJudge, insumo: InsumoDoJudge, configuracao: str
) -> tuple[object, object]:
    medidor = MedidorDeCusto("checagem", CAMADA_POR_CONFIGURACAO[configuracao])
    julgamento = pontuar_n3(insumo, configuracao, cliente, medidor)
    return julgamento, medidor.fechar()


def main() -> int:
    caminho = Path(sys.argv[1]) if len(sys.argv) > 1 else TRACE_PADRAO
    if not caminho.exists():
        print(f"trace não encontrado: {caminho}")
        return 1

    cenario = carregar_cenarios()[caminho.name.split("--")[0]]
    base = montar_insumo(read_trace(caminho), cenario)
    if not base.resposta:
        print(f"{caminho.name} terminou sem `final_answer` — escolha um trace que respondeu")
        return 1

    modelo = config_do_judge()
    cliente = ClienteDoJudge(modelo)
    print(f"judge: {modelo.model_id} · temperatura {modelo.temperature}")
    print(f"trace: {caminho.name}")
    print(f"cenário: {cenario.id} · regra: {cenario.regra.nome}\n")

    custos: dict[str, object] = {}
    for configuracao in ("cego", "com_trace"):
        julgamento, custo = julgar(cliente, base, configuracao)
        custos[configuracao] = custo
        print(f"--- {configuracao}, resposta real do agente ---")
        print(f"  causa_raiz_correta={julgamento.causa_raiz_correta} "
              f"limitacao={julgamento.mencionou_limitacao_relevante} "
              f"responde={julgamento.responde_a_pergunta}")
        if configuracao == "com_trace":
            print(f"  sem_suporte={julgamento.afirmacoes_sem_suporte} "
                  f"contradiz={julgamento.contradiz_evidencia} "
                  f"acao_sem_base={julgamento.recomendou_acao_sem_base}")
        print(f"  custo: in={custo.tokens_in} out={custo.tokens_out} {custo.segundos:.1f}s")

    print("\n--- com_trace, resposta com defeito plantado ---")
    plantado = dataclasses.replace(base, resposta=RESPOSTA_COM_DEFEITO_PLANTADO)
    julgamento, _ = julgar(cliente, plantado, "com_trace")
    print(f"  sem_suporte: {julgamento.afirmacoes_sem_suporte}")
    print(f"  acao_sem_base: {julgamento.recomendou_acao_sem_base}")

    pegou_afirmacao = bool(julgamento.afirmacoes_sem_suporte)
    pegou_acao = julgamento.recomendou_acao_sem_base is True

    razao = custos["com_trace"].tokens_in / max(1, custos["cego"].tokens_in)
    print(f"\nrazão de tokens de entrada (com_trace / cego): {razao:.1f}×")
    print("  METRICAS §4 previa 3–8×. Abaixo disso os dois pontos da curva de H0 ficam")
    print("  perto demais para separar — é resultado a declarar, não defeito a esconder.")

    veredito = pegou_afirmacao and pegou_acao
    print(f"\n{'OK' if veredito else 'REPROVOU'}: o judge "
          f"{'detectou' if veredito else 'NÃO detectou'} o defeito plantado")
    return 0 if veredito else 1


if __name__ == "__main__":
    raise SystemExit(main())
