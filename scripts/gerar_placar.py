"""Gera `docs/anexos/resultados/placar_modelos.json` a partir dos três resultados dos notebooks.

POR QUE ISTO É GERADO E NÃO DIGITADO
    O placar responde "qual dos dois modelos é melhor?" com um vencedor por critério. Cada
    número dele já tem dono — `resultados_h0.json` (nb04), `resultados_passk.json` (nb05) e
    `resultados_taxonomia.json` (nb06). Digitá-los num quarto arquivo criaria uma quarta
    verdade, que diverge silenciosamente na primeira vez que uma bateria for repontuada.

    O que é editorial aqui é **a escolha dos critérios e o texto**; os valores vêm dos JSONs, e
    `tests/test_app.py` reprova se este arquivo ficar mais velho que as fontes.

⚠️ O SUT DE REFERÊNCIA NÃO ENTRA
    Ele é modelo de fronteira e faz 100% no corte S1 — mas rodou nos **6 cenários de dev**, e a
    bateria principal nos **18 de test**, com interseção **zero**. Pôr os dois na mesma tabela
    leria como "o de fronteira ganha no mesmo teste", que não é o que foi medido. O contraste
    dele mora no README §7.3 e no explicador da própria página, com a ressalva junto.
"""

from __future__ import annotations

import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
DOCS = RAIZ / "docs" / "anexos" / "resultados"

passk = json.loads((DOCS / "resultados_passk.json").read_text(encoding="utf-8"))
h0 = json.loads((DOCS / "resultados_h0.json").read_text(encoding="utf-8"))
taxo = json.loads((DOCS / "resultados_taxonomia.json").read_text(encoding="utf-8"))

curvas, h2 = passk["curvas"], h0["h2"]
sens = {(x["corte"], x["modelo"]): x for x in taxo["sensibilidade"]}
ordem = taxo["ordem_dos_modelos"]
p6 = next(f for f in taxo["frequencias"] if f["codigo"] == "P6")
n_por_modelo = {m: 144 for m in ("qwen3-8b", "qwen3-14b")}

pct = lambda x: f"{x:.1%}".replace(".", ",")  # noqa: E731


def vencedor_de(a: float, b: float, rotulo_a: str, rotulo_b: str, empate: str) -> str:
    if a == b:
        return empate
    return rotulo_a if a > b else rotulo_b


criterios = [
    {
        "criterio": "Média simples das execuções",
        "oito": pct(curvas["8B|incluir"]["media_simples"]),
        "quatorze": pct(curvas["14B|incluir"]["media_simples"]),
        "vencedor": vencedor_de(
            curvas["8B|incluir"]["media_simples"],
            curvas["14B|incluir"]["media_simples"], "8B", "14B", "empate",
        ),
        "fonte": "docs/anexos/resultados/resultados_passk.json · curvas · media_simples",
        "nota": "a média que o pass^k existe para contradizer",
    },
    {
        "criterio": f"Entregar {passk['cruzamento_8b_ultrapassa_14b']['incluir']} vezes seguidas",
        "oito": pct(curvas["8B|incluir"]["passk"]["3"]),
        "quatorze": pct(curvas["14B|incluir"]["passk"]["3"]),
        "vencedor": vencedor_de(
            curvas["8B|incluir"]["passk"]["3"],
            curvas["14B|incluir"]["passk"]["3"], "8B", "14B", "empate",
        ),
        "fonte": "docs/anexos/resultados/resultados_passk.json · pass^3 · a inversão sobrevive\n"
        "às três leituras",
        "nota": "exigir consistência inverte a ordem que a média dá",
    },
    {
        "criterio": "Entregar nas 8 tentativas seguidas",
        "oito": f"{curvas['8B|incluir']['passk']['8']:.3f}".replace(".", ","),
        "quatorze": f"{curvas['14B|incluir']['passk']['8']:.3f}".replace(".", ","),
        "vencedor": "empate em zero",
        "fonte": "docs/anexos/resultados/resultados_passk.json · pass^8",
        "nota": "nenhum cenário é entregue nas 8 tentativas por nenhum dos dois",
    },
    {
        "criterio": "Sem nenhuma ressalva, nem de atenção (o corte oficial)",
        "oito": f"{sens[('S2', 'qwen3-8b')]['n_aprovadas']}/{n_por_modelo['qwen3-8b']}",
        "quatorze": f"{sens[('S2', 'qwen3-14b')]['n_aprovadas']}/{n_por_modelo['qwen3-14b']}",
        "vencedor": "empate em zero",
        "fonte": "docs/anexos/resultados/resultados_taxonomia.json · sensibilidade · corte S2",
        "nota": "a régua oficial não separa ninguém — o SUT de fronteira também dá zero nela",
    },
    {
        "criterio": "Sem problema crítico nem grave, descontadas as que não concluíram",
        "oito": pct(sens[("S1", "qwen3-8b")]["taxa_entre_pontuaveis"]),
        "quatorze": pct(sens[("S1", "qwen3-14b")]["taxa_entre_pontuaveis"]),
        "vencedor": "8B" if ordem["S1"]["lider_entre_pontuaveis"] == "qwen3-8b" else "14B",
        "fonte": "docs/anexos/resultados/resultados_taxonomia.json · taxa_entre_pontuaveis "
        "· corte S1",
        "nota": (
            f"como reportado seria {pct(sens[('S1', 'qwen3-8b')]['taxa'])} × "
            f"{pct(sens[('S1', 'qwen3-14b')]['taxa'])} — a ordem inverte quando as execuções "
            "sem decisão saem"
        ),
    },
    {
        "criterio": "Sem problema crítico, descontadas as que não concluíram",
        "oito": pct(sens[("S0", "qwen3-8b")]["taxa_entre_pontuaveis"]),
        "quatorze": pct(sens[("S0", "qwen3-14b")]["taxa_entre_pontuaveis"]),
        "vencedor": "8B" if ordem["S0"]["lider_entre_pontuaveis"] == "qwen3-8b" else "14B",
        "fonte": "docs/anexos/resultados/resultados_taxonomia.json · taxa_entre_pontuaveis "
        "· corte S0",
        "nota": (
            f"{abs(ordem['S0']['delta_entre_pontuaveis']) * 100:.1f} ponto de diferença em "
            "n = 251 — dentro do ruído"
        ).replace(".", ","),
    },
    {
        "criterio": "Escolher a ferramenta certa",
        "oito": "—",
        "quatorze": f"+{h2['tool_f1_liquido']['delta']:.3f}".replace(".", ","),
        "vencedor": "14B",
        "fonte": "docs/anexos/resultados/resultados_h0.json · h2 · tool_f1_liquido",
        "nota": f"só no líquido; o bruto {h2['tool_f1']['leitura']}",
    },
    {
        "criterio": "Preencher os parâmetros da consulta",
        "oito": f"+{abs(h2['args_acc']['delta']):.3f}".replace(".", ","),
        "quatorze": "—",
        "vencedor": "8B (no limiar)",
        "fonte": "docs/anexos/resultados/resultados_h0.json · h2 · args_acc · "
        f"p = {h2['args_acc']['p_bootstrap']}",
        "nota": h2["args_acc"]["leitura"],
    },
    {
        "criterio": "Devolver uma saída bem formada",
        "oito": pct(p6["por_modelo"]["qwen3-8b"] / n_por_modelo["qwen3-8b"]),
        "quatorze": pct(p6["por_modelo"]["qwen3-14b"] / n_por_modelo["qwen3-14b"]),
        "vencedor": vencedor_de(
            -p6["por_modelo"]["qwen3-8b"], -p6["por_modelo"]["qwen3-14b"], "8B", "14B", "empate"
        ),
        "fonte": "docs/anexos/resultados/resultados_taxonomia.json · frequencias · P6",
        "nota": "quanto menor melhor — é o que produz as execuções sem decisão do 14B",
    },
]

saida = {
    "gerado_por": "scripts/gerar_placar.py",
    "fontes": [
        "docs/anexos/resultados/resultados_h0.json",
        "docs/anexos/resultados/resultados_passk.json",
        "docs/anexos/resultados/resultados_taxonomia.json",
    ],
    "aviso_sut_de_referencia": (
        "O SUT de fronteira faz 100% no corte S1, mas rodou nos 6 cenários de dev contra os 18 "
        "de test desta bateria — interseção zero. Por isso ele não entra nesta tabela."
    ),
    "criterios": criterios,
}
(DOCS / "placar_modelos.json").write_text(
    json.dumps(saida, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
)

contagem: dict[str, int] = {}
for c in criterios:
    v = c["vencedor"]
    chave = "8B" if v.startswith("8B") else "14B" if v.startswith("14B") else "empate"
    contagem[chave] = contagem.get(chave, 0) + 1
print(f"docs/anexos/resultados/placar_modelos.json · {len(criterios)} critérios · {contagem}")
