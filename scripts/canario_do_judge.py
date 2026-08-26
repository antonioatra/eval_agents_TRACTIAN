#!/usr/bin/env python3
"""O canário da T23 — detectar que o modelo do judge mudou sob o pé.

POR QUE ELE EXISTE (medido em 25/08, `docs/migracao_vertex.md §3`)
    A T23 congela o judge por sha256 para que ele não mude no meio do trabalho. Só que o que
    o sha256 congela é o PROMPT e o ID — e o id é um alias. `gemini-3.6-flash-07-2026` responde
    404 nos dois provedores: o AI Studio deixa LER para onde o alias aponta (`version`), o
    Vertex nem isso (`versionId: default`), e nenhum dos dois deixa fixá-lo.

    Então o congelamento não alcança o peso do outro lado. O que resta é o método que o projeto
    já usa para calibrar instrumento contra gabarito conhecido (T12): rodar uma entrada FIXA e
    comparar com o que ela produziu antes. Se o modelo virar, a comparação denuncia.

    Roda antes e depois de cada bateria. Duas chamadas por vez, não 1.400.

O QUE ELE COMPARA, E POR QUE NÃO É O TEXTO
    Dois sinais, de naturezas diferentes:

    1. **`tokens_in` sobre um prompt byte a byte idêntico.** É impressão digital do tokenizador
       e do tratamento de prompt. Mudou o número para a mesma entrada? Alguma coisa mudou do
       outro lado. Este sinal é mais barato e mais afiado que qualquer comparação semântica —
       e ele não é hipotético: a migração mediu 2.601 → 2.803 tokens para o MESMO prompt ao
       trocar de provedor (§5 do `migracao_vertex.md`). O sinal funciona porque já funcionou.

    2. **Os campos do veredito.** O que a rubrica de fato decidiu sobre a resposta plantada.

O QUE IMPEDE O CANÁRIO DE MENTIR
    Um judge com temperatura 0 ainda não é uma função pura, e um canário ingênuo confundiria a
    instabilidade da própria rubrica — que é o que a INS.7 mede — com troca de modelo. Cada
    rodada faz N repetições e classifica cada campo em ESTÁVEL (mesmo valor nas N) ou INSTÁVEL.

    **A comparação só olha para os campos que a linha de base provou estáveis.** Um campo que já
    variava sozinho não pode testemunhar contra o modelo — ele testemunha contra a rubrica, e
    esse é outro instrumento (a INS.7), com outro número.

O QUE ELE NÃO ESTABELECE
    Ausência de divergência não é prova de que o modelo não mudou: dois snapshots podem
    concordar nesta entrada. O canário é detecção, não garantia — e é por isso que o veredito
    negativo se chama "sem evidência de troca", e não "não mudou".

Uso:
    python scripts/canario_do_judge.py --gravar     # estabelece a linha de base
    python scripts/canario_do_judge.py              # compara com ela
    python scripts/canario_do_judge.py --repeticoes 5
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from checar_judge import (  # noqa: E402
    RESPOSTA_COM_DEFEITO_PLANTADO,
    TRACE_PADRAO,
)
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

CAMINHO_PADRAO = Path("docs/canario_do_judge.json")

REPETICOES_PADRAO = 3
"""Três porque duas não distinguem 'estável' de 'coincidiu': com N=2 qualquer campo que caia no
mesmo valor por acaso entra na lista de comparáveis e vira falso alarme na próxima rodada. E
mais que três custa chamada sem comprar separação — o que se quer aqui é o piso de ruído, não
a distribuição dele, que é trabalho da INS.7."""

CONFIGURACAO = "com_trace"
"""O canário julga com trace porque é a configuração que usa MAIS do modelo: ela vê a evidência
e tem de confrontá-la com a resposta. Um modelo trocado tem mais superfície para divergir aqui
do que no cego, que só lê a resposta."""

CAMPOS_DO_VEREDITO = (
    "causa_raiz_correta",
    "mencionou_limitacao_relevante",
    "responde_a_pergunta",
    "contradiz_evidencia",
    "recomendou_acao_sem_base",
    "n_afirmacoes_sem_suporte",
    "afirmacoes_sem_suporte",
)
"""`afirmacoes_sem_suporte` (texto livre) e a contagem dela entram as duas de propósito: se o
texto se provar estável nas repetições, ele vira um sinal fino; se não, a contagem sobrevive
como sinal grosso. Qual dos dois vale é a rodada que decide, não este arquivo."""


def _uma_passada(cliente: ClienteDoJudge, insumo: InsumoDoJudge) -> dict[str, Any]:
    """Uma passada do judge sobre a entrada fixa, reduzida aos campos comparáveis.

    `chamadas_llm` vem junto porque `pontuar_n3` **retenta** quando a saída não valida ou
    cita id inventado, e a retentativa reenvia o prompt com a resposta anterior e a correção
    coladas atrás (`n3.py:441`). O `tokens_in` da passada é a SOMA das chamadas, então uma
    retentativa dobra o número sobre uma entrada byte a byte idêntica. Sem este campo o
    canário não tem como distinguir as duas coisas — ver `classificar`.
    """
    medidor = MedidorDeCusto("canario", CAMADA_POR_CONFIGURACAO[CONFIGURACAO])
    julgamento = pontuar_n3(insumo, CONFIGURACAO, cliente, medidor)
    custo = medidor.fechar()

    afirmacoes = tuple(getattr(julgamento, "afirmacoes_sem_suporte", ()) or ())
    return {
        "tokens_in": custo.tokens_in,
        "chamadas_llm": custo.chamadas_llm,
        "causa_raiz_correta": julgamento.causa_raiz_correta,
        "mencionou_limitacao_relevante": julgamento.mencionou_limitacao_relevante,
        "responde_a_pergunta": julgamento.responde_a_pergunta,
        "contradiz_evidencia": getattr(julgamento, "contradiz_evidencia", None),
        "recomendou_acao_sem_base": getattr(julgamento, "recomendou_acao_sem_base", None),
        "n_afirmacoes_sem_suporte": len(afirmacoes),
        "afirmacoes_sem_suporte": list(afirmacoes),
    }


def classificar(passadas: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Separa o que ficou ESTÁVEL, o que variou sozinho e o que não tem TESTEMUNHO.

    A terceira gaveta nasceu de um falso alarme real (26/08). O canário rodado depois da
    calibração da T21 acusou `DIVERGIU` com `tokens_in` variando entre 5993, 5993 e 12140 — e
    12140 não é outro modelo, é a MESMA entrada enviada duas vezes: `pontuar_n3` retenta
    reapresentando o prompt com a resposta anterior colada atrás, e o medidor soma as duas
    chamadas. Na calibração isso aconteceu em **35 das 220 células** (25,5% do `com_trace`),
    então não é raridade que dê para ignorar.

    **Uma passada que retentou não pode testemunhar sobre `tokens_in`**, porque o número que
    ela produz não é sobre a entrada fixa — é sobre a entrada fixa mais a correção. Ela não é
    prova de instabilidade nem de estabilidade: é ausência de medição, e é isso que
    `sem_testemunho` diz. Contá-la como instável faria o canário gritar por causa da rubrica,
    que é exatamente o erro que o `estaveis`/`instaveis` já existia para não cometer — e um
    canário que grita antes de toda bateria não trava nada, porque ninguém acredita nele.

    Os campos do VEREDITO não passam por esse filtro: a retentativa devolve um julgamento
    válido, e o que a rubrica decidiu é comparável tenha havido correção ou não.
    """
    estaveis: dict[str, Any] = {}
    instaveis: dict[str, list[Any]] = {}
    sem_testemunho: dict[str, str] = {}

    limpas = [p for p in passadas if p.get("chamadas_llm", 1) == 1]
    if len(limpas) < 2:
        sem_testemunho["tokens_in"] = (
            f"{len(passadas) - len(limpas)} de {len(passadas)} passadas retentaram — "
            "sobrou menos de uma dupla sem retentativa para comparar"
        )

    for campo in ("tokens_in", *CAMPOS_DO_VEREDITO):
        if campo in sem_testemunho:
            continue
        fonte = limpas if campo == "tokens_in" else passadas
        valores = [passada[campo] for passada in fonte]
        primeiro = valores[0]
        if all(valor == primeiro for valor in valores):
            estaveis[campo] = primeiro
        else:
            instaveis[campo] = valores

    return {
        "estaveis": estaveis,
        "instaveis": instaveis,
        "sem_testemunho": sem_testemunho,
        "chamadas_llm": [p.get("chamadas_llm") for p in passadas],
    }


def rodar(cliente: ClienteDoJudge, insumo: InsumoDoJudge, repeticoes: int) -> dict[str, Any]:
    """N passadas, separando o que ficou estável do que variou sozinho."""
    passadas = [_uma_passada(cliente, insumo) for _ in range(repeticoes)]

    modelo = cliente.modelo
    return {
        "quando": datetime.now(UTC).isoformat(timespec="seconds"),
        "provedor": cliente.provedor,
        "model_id": modelo.model_id,
        "served_by": modelo.served_by,
        "temperature": modelo.temperature,
        "trace": TRACE_PADRAO.name,
        "repeticoes": repeticoes,
        **classificar(passadas),
    }


def comparar(base: dict[str, Any], agora: dict[str, Any]) -> list[str]:
    """As divergências que testemunham contra o MODELO, e só elas."""
    divergencias: list[str] = []

    if base["served_by"] != agora["served_by"]:
        divergencias.append(
            f"provedor mudou: {base['served_by']} → {agora['served_by']}. "
            "A linha de base não vale entre provedores — os absolutos de token diferem em "
            "6–8% para o mesmo prompt (migracao_vertex §5). Regrave antes de comparar."
        )
        return divergencias

    if base["model_id"] != agora["model_id"]:
        divergencias.append(f"model_id mudou: {base['model_id']} → {agora['model_id']}")

    for campo, esperado in base["estaveis"].items():
        if campo in agora.get("sem_testemunho", {}):
            # Não é concordância nem divergência: a rodada de agora não mediu este campo.
            # Silenciar aqui e reportar em `main` — divergência é o que testemunha CONTRA o
            # modelo, e ausência de medida não testemunha nada.
            continue
        if campo in agora["instaveis"]:
            divergencias.append(
                f"{campo}: era estável em {esperado!r}, agora varia entre "
                f"{agora['instaveis'][campo]!r}"
            )
        elif agora["estaveis"].get(campo) != esperado:
            divergencias.append(
                f"{campo}: {esperado!r} → {agora['estaveis'].get(campo)!r}"
            )

    return divergencias


def _insumo_plantado() -> InsumoDoJudge:
    """A entrada fixa: trace real, resposta com defeito plantado.

    A resposta plantada e não a real porque ela força o judge a EXERCER a rubrica — três
    afirmações sem suporte e uma ação recomendada sobre elas. Um judge trocado tem de decidir
    algo aqui, e é a decisão que o canário compara. Sobre a resposta limpa, dois modelos
    diferentes concordariam com facilidade demais para o sinal valer."""
    cenario = carregar_cenarios()[TRACE_PADRAO.name.split("--")[0]]
    base = montar_insumo(read_trace(TRACE_PADRAO), cenario)
    return dataclasses.replace(base, resposta=RESPOSTA_COM_DEFEITO_PLANTADO)


def main() -> int:
    parser = argparse.ArgumentParser(description="Canário da T23")
    parser.add_argument("--gravar", action="store_true", help="estabelece a linha de base")
    parser.add_argument("--repeticoes", type=int, default=REPETICOES_PADRAO)
    parser.add_argument("--caminho", type=Path, default=CAMINHO_PADRAO)
    args = parser.parse_args()

    if not TRACE_PADRAO.exists():
        print(f"trace não encontrado: {TRACE_PADRAO}")
        return 2

    insumo = _insumo_plantado()
    modelo = config_do_judge()
    with ClienteDoJudge(modelo) as cliente:
        print(f"canário: {modelo.model_id} · {cliente.provedor} · {args.repeticoes} repetições")
        agora = rodar(cliente, insumo, args.repeticoes)

    print(f"\nestáveis ({len(agora['estaveis'])}):")
    for campo, valor in agora["estaveis"].items():
        print(f"  {campo} = {valor!r}")
    if agora["instaveis"]:
        print(f"\ninstáveis ({len(agora['instaveis'])}) — não entram na comparação:")
        for campo, valores in agora["instaveis"].items():
            print(f"  {campo}: {valores!r}")
    if agora.get("sem_testemunho"):
        print(f"\nsem testemunho ({len(agora['sem_testemunho'])}) — não foram medidos:")
        for campo, motivo in agora["sem_testemunho"].items():
            print(f"  {campo}: {motivo}")
    if any(chamadas and chamadas > 1 for chamadas in agora.get("chamadas_llm", [])):
        print(f"  chamadas por passada: {agora['chamadas_llm']} (>1 é retentativa do judge)")

    if args.gravar:
        args.caminho.write_text(
            json.dumps(agora, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"\nlinha de base gravada em {args.caminho}")
        return 0

    if not args.caminho.exists():
        print(f"\nsem linha de base em {args.caminho}. Rode com --gravar antes da bateria.")
        return 2

    base = json.loads(args.caminho.read_text(encoding="utf-8"))
    divergencias = comparar(base, agora)

    print(f"\nlinha de base de {base['quando']} ({base['repeticoes']} repetições)")
    if divergencias:
        print(f"\nDIVERGIU ({len(divergencias)}):")
        for divergencia in divergencias:
            print(f"  · {divergencia}")
        print(
            "\nDivergência não prova troca de modelo, mas é o único sinal que temos de que ela\n"
            "aconteceu. Antes de usar os N3 desta bateria, decida se eles são comparáveis aos\n"
            "anteriores — e registre a decisão."
        )
        return 1

    print(
        "\nSEM EVIDÊNCIA DE TROCA nos campos estáveis.\n"
        "  Não é prova de que o modelo não mudou: dois snapshots podem concordar nesta\n"
        "  entrada. É o que dá para afirmar sem um snapshot fixável, que não existe."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
