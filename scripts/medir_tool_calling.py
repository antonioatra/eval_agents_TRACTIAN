#!/usr/bin/env python3
"""T0b — mede se o modelo candidato escolhe função e argumento com o catálogo inteiro na janela.

POR QUE ISTO EXISTE
    A T0b é o portão de viabilidade do cronograma (`PLANO.md`): se o modelo erra seleção de
    função sistematicamente com ~18 schemas na janela, não adianta construir runner, judge e
    baterias em cima dele. Melhor descobrir no dia 1 que no dia 20.

    Este script NÃO é a bateria. Não usa o runner, não escreve trace, não pontua com N1/N2 e
    não executa nenhuma chamada contra a API — ele mede só a mecânica de tool calling. O que
    ele produz é `docs/tool_calling_baseline.md`.

O CATÁLOGO É O REAL, NÃO UM RASCUNHO
    O `PLANO.md` previa "~15 tools de rascunho" porque a T0b foi escrita antes da T13. As 18
    tools reais já existem, derivadas do contrato, com snake_case normalizado e
    `validar_argumentos` pronto. Medir contra o rascunho daria um número que não se aplica ao
    que a bateria vai rodar.

SÓ CENÁRIOS DE DEV — E POR QUE ISSO NÃO CUSTOU n
    O split é 6 dev / 18 test (`CENARIOS.md §`). Test fica lacrado até o judge congelar, e uma
    sonda de seleção de função que visse as 18 mensagens de test as gastaria antes da bateria.
    Os 6 de dev somam 26 tools esperadas, 23 com `args_esperados` — acima das 20 solicitações
    que a T0b pede. O split saiu intacto sem perder tamanho de amostra.

OS DOIS BLOCOS, E O QUE CADA UM RESPONDE
    Bloco A — PLANO EM UM PASSO. A solicitação real do cenário, as 18 tools, e o modelo emite
    o conjunto de chamadas que faria. Julga contra `tools_esperadas` (recall), o que chamou
    fora de `esperadas ∪ aceitaveis` (ruído) e `args_esperados` (acurácia de argumento). É o
    bloco que estressa a largura do catálogo: escolher 4-6 de 18 é mais difícil do que a ReAct
    real, onde cada passo vê o resultado do anterior. O número dele é um piso, não um teto.

    Bloco B — PRIMEIRA CHAMADA, REPETIDA. A mesma solicitação, `tool_choice=required`, quatro
    `sample_seed` diferentes. Mede se a escolha de entrada é estável entre trials. Instabilidade
    aqui é o que o `pass^k` vai medir depois; se já for alta em n=4, o pass^k da bateria vem
    baixo por variância do modelo, e é melhor saber agora.

`parse_erro` É A MÉTRICA QUE MAIS IMPORTA
    É o principal confound entre modelos (`ARQUITETURA §5`, decisão 4). Aqui ele conta duas
    coisas distintas e as mantém distintas: `sem_tool_call` (o modelo respondeu em texto quando
    devia chamar) e `args_ilegiveis` (chamou, mas os argumentos não são JSON). Somá-las
    esconderia qual dos dois problemas o modelo tem.

USO
    python scripts/medir_tool_calling.py
    python scripts/medir_tool_calling.py --modelo qwen3-8b --modelo qwen3-14b
    python scripts/medir_tool_calling.py --base-url http://127.0.0.1:11434/v1

Exige o servidor de inferência de pé. Rode antes `scripts/checar_servidor_de_inferencia.py`.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from tapieval.mcp.tools import (  # noqa: E402
    Operacao,
    carregar_operacoes,
    tools_visiveis,
    validar_argumentos,
)

BASE_URL_PADRAO = "http://127.0.0.1:1234/v1"
TIMEOUT_S = 600.0
"""14B em q4 com 18 schemas na janela e várias chamadas na saída leva minutos, não segundos."""

CENARIOS_DE_DEV: tuple[str, ...] = (
    "aut_01_barulho_sem_desvio",
    "aut_03_pergunta_que_parece_ordem",
    "aut_06_premissa_falsa",
    "cen_04_lubrificacao_sem_baseline",
    "cen_06_diagnosticos_divergentes",
    "cen_09_cobertura_do_modelo",
)
"""Os 6 de `split: dev`. Test não entra: ver o topo do módulo."""

SEEDS_DO_BLOCO_B: tuple[int, ...] = (11, 23, 42, 77)

SISTEMA = (
    "Você é um agente de manutenção preditiva industrial. Você observa o ambiente chamando "
    "tools; cada tool corresponde a um endpoint da API industrial, sem atalho e sem agregação. "
    "Não invente identificadores: use apenas os que aparecem no contexto da solicitação."
)

INSTRUCAO_BLOCO_A = (
    "Emita as chamadas de tool que você faria para investigar esta solicitação, todas de uma "
    "vez. Não responda ao usuário ainda — apenas chame as tools."
)

INSTRUCAO_BLOCO_B = (
    "Emita exatamente UMA chamada de tool: a primeira que você faria para investigar esta "
    "solicitação."
)


# ---------------------------------------------------------------------------
# Cenários e catálogo
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CenarioDeDev:
    id: str
    solicitacao: str
    contexto: str
    esperadas: frozenset[str]
    aceitaveis: frozenset[str]
    args_esperados: dict[str, dict[str, Any]]

    @property
    def toleradas(self) -> frozenset[str]:
        """O que não conta como ruído: o gabarito já declara as duas listas."""
        return self.esperadas | self.aceitaveis


def carregar_cenarios_de_dev() -> list[CenarioDeDev]:
    cenarios: list[CenarioDeDev] = []
    for nome in CENARIOS_DE_DEV:
        dados = yaml.safe_load((RAIZ / "scenarios" / f"{nome}.yaml").read_text())
        if dados.get("split") != "dev":
            raise SystemExit(f"{nome}: split é {dados.get('split')!r}, não 'dev'. Abortando.")
        gabarito = dados.get("gabarito") or {}
        contexto = dados.get("contexto") or {}
        linhas = [f"user_id: {dados.get('user_id')}"]
        if dados.get("asset_id"):
            linhas.append(f"asset_id: {dados['asset_id']}")
        for chave in ("empresa_usuario", "permissoes_usuario"):
            if chave in contexto:
                linhas.append(f"{chave}: {contexto[chave]}")
        cenarios.append(
            CenarioDeDev(
                id=dados["id"],
                solicitacao=" ".join(str(dados["solicitacao"]).split()),
                contexto="\n".join(linhas),
                esperadas=frozenset(gabarito.get("tools_esperadas") or ()),
                aceitaveis=frozenset(gabarito.get("tools_aceitaveis") or ()),
                args_esperados=dict(gabarito.get("args_esperados") or {}),
            )
        )
    return cenarios


def schemas_openai() -> list[dict[str, Any]]:
    """As 18 tools no formato de function calling da API OpenAI-compatible."""
    return [
        {
            "type": "function",
            "function": {
                "name": operacao.nome,
                "description": operacao.descricao,
                "parameters": operacao.input_schema(),
            },
        }
        for operacao in tools_visiveis()
    ]


# ---------------------------------------------------------------------------
# Chamada ao servidor
# ---------------------------------------------------------------------------


@dataclass
class ChamadaEmitida:
    nome: str
    args: dict[str, Any] | None
    args_crus: str
    erro_de_parse: str | None = None


@dataclass
class Resposta:
    chamadas: list[ChamadaEmitida]
    texto: str
    segundos: float
    erro_http: str | None = None

    @property
    def sem_tool_call(self) -> bool:
        return self.erro_http is None and not self.chamadas


def pedir(
    cliente: httpx.Client,
    modelo: str,
    *,
    mensagens: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    seed: int | None,
    temperatura: float,
    forcar_uma: bool,
) -> Resposta:
    corpo: dict[str, Any] = {
        "model": modelo,
        "messages": mensagens,
        "tools": tools,
        "tool_choice": "required" if forcar_uma else "auto",
        "temperature": temperatura,
        "max_tokens": 1200,
    }
    if seed is not None:
        corpo["seed"] = seed

    inicio = time.perf_counter()
    try:
        resposta = cliente.post("/chat/completions", json=corpo)
        resposta.raise_for_status()
    except httpx.HTTPStatusError as erro:
        return Resposta([], "", time.perf_counter() - inicio,
                        f"HTTP {erro.response.status_code}: {erro.response.text[:300]}")
    except httpx.HTTPError as erro:
        return Resposta([], "", time.perf_counter() - inicio, f"{type(erro).__name__}: {erro}")

    decorrido = time.perf_counter() - inicio
    mensagem = resposta.json()["choices"][0]["message"]
    emitidas: list[ChamadaEmitida] = []
    for bruta in mensagem.get("tool_calls") or []:
        funcao = bruta.get("function") or {}
        crus = funcao.get("arguments") or ""
        try:
            args = json.loads(crus) if crus.strip() else {}
            erro = None
            if not isinstance(args, dict):
                args, erro = None, f"argumentos não são objeto JSON: {crus[:80]!r}"
        except json.JSONDecodeError as falha:
            args, erro = None, f"JSON inválido: {falha.msg}"
        emitidas.append(ChamadaEmitida(funcao.get("name") or "", args, crus, erro))
    return Resposta(emitidas, mensagem.get("content") or "", decorrido)


# ---------------------------------------------------------------------------
# Julgamento
# ---------------------------------------------------------------------------


@dataclass
class Julgamento:
    """O veredito de UMA chamada emitida, contra o gabarito do cenário."""

    cenario: str
    tool: str
    existe_no_catalogo: bool
    tolerada: bool
    esperada: bool
    args_validos: bool | None
    args_batem: bool | None
    detalhe: str = ""


def julgar(
    chamada: ChamadaEmitida, cenario: CenarioDeDev, operacoes: dict[str, Operacao]
) -> Julgamento:
    operacao = operacoes.get(chamada.nome)
    if operacao is None:
        return Julgamento(cenario.id, chamada.nome, False, False, False, None, None,
                          "tool que não existe no catálogo")
    if chamada.args is None:
        return Julgamento(cenario.id, chamada.nome, True, chamada.nome in cenario.toleradas,
                          chamada.nome in cenario.esperadas, None, None,
                          chamada.erro_de_parse or "argumentos ilegíveis")

    erro = validar_argumentos(operacao, chamada.args)
    esperados = cenario.args_esperados.get(chamada.nome)
    batem: bool | None = None
    detalhe = erro or ""
    if esperados is not None:
        batem = all(str(chamada.args.get(k)) == str(v) for k, v in esperados.items())
        if not batem:
            detalhe = detalhe or f"esperado {esperados}, veio {chamada.args}"
    return Julgamento(
        cenario.id, chamada.nome, True, chamada.nome in cenario.toleradas,
        chamada.nome in cenario.esperadas, erro is None, batem, detalhe,
    )


@dataclass
class Placar:
    modelo: str
    julgamentos: list[Julgamento] = field(default_factory=list)
    sem_tool_call: int = 0
    erros_http: list[str] = field(default_factory=list)
    recall_por_cenario: dict[str, tuple[int, int]] = field(default_factory=dict)
    segundos: list[float] = field(default_factory=list)
    primeira_por_cenario: dict[str, list[str]] = field(default_factory=dict)

    def taxa(self, quantos: int, de: int) -> str:
        return f"{quantos}/{de} ({100 * quantos / de:.0f}%)" if de else "—"


# ---------------------------------------------------------------------------
# Os dois blocos
# ---------------------------------------------------------------------------


def rodar_bloco_a(
    cliente: httpx.Client, modelo: str, cenarios: list[CenarioDeDev],
    tools: list[dict[str, Any]], operacoes: dict[str, Operacao], placar: Placar,
) -> None:
    for cenario in cenarios:
        mensagens = [
            {"role": "system", "content": SISTEMA},
            {"role": "user", "content":
             f"{cenario.solicitacao}\n\n[contexto]\n{cenario.contexto}\n\n{INSTRUCAO_BLOCO_A}"},
        ]
        resposta = pedir(cliente, modelo, mensagens=mensagens, tools=tools, seed=42,
                         temperatura=0.0, forcar_uma=False)
        placar.segundos.append(resposta.segundos)
        if resposta.erro_http:
            placar.erros_http.append(f"[A/{cenario.id}] {resposta.erro_http}")
            continue
        if resposta.sem_tool_call:
            placar.sem_tool_call += 1
            print(f"  [A] {cenario.id}: SEM TOOL CALL — respondeu em texto")
            placar.recall_por_cenario[cenario.id] = (0, len(cenario.esperadas))
            continue

        chamadas = [julgar(c, cenario, operacoes) for c in resposta.chamadas]
        placar.julgamentos.extend(chamadas)
        acertou = {j.tool for j in chamadas if j.esperada}
        placar.recall_por_cenario[cenario.id] = (len(acertou), len(cenario.esperadas))
        ruido = [j.tool for j in chamadas if not j.tolerada]
        print(f"  [A] {cenario.id}: {len(resposta.chamadas)} chamada(s), "
              f"recall {len(acertou)}/{len(cenario.esperadas)}"
              + (f", ruído: {', '.join(sorted(set(ruido)))}" if ruido else "")
              + f", {resposta.segundos:.0f}s")


def rodar_bloco_b(
    cliente: httpx.Client, modelo: str, cenarios: list[CenarioDeDev],
    tools: list[dict[str, Any]], operacoes: dict[str, Operacao], placar: Placar,
) -> None:
    for cenario in cenarios:
        escolhas: list[str] = []
        for seed in SEEDS_DO_BLOCO_B:
            mensagens = [
                {"role": "system", "content": SISTEMA},
                {"role": "user", "content":
                 f"{cenario.solicitacao}\n\n[contexto]\n{cenario.contexto}\n\n"
                 f"{INSTRUCAO_BLOCO_B}"},
            ]
            resposta = pedir(cliente, modelo, mensagens=mensagens, tools=tools, seed=seed,
                             temperatura=0.7, forcar_uma=True)
            placar.segundos.append(resposta.segundos)
            if resposta.erro_http:
                placar.erros_http.append(f"[B/{cenario.id}/s{seed}] {resposta.erro_http}")
                continue
            if resposta.sem_tool_call:
                placar.sem_tool_call += 1
                escolhas.append("<sem_tool_call>")
                continue
            primeira = resposta.chamadas[0]
            placar.julgamentos.append(julgar(primeira, cenario, operacoes))
            escolhas.append(primeira.nome)
        placar.primeira_por_cenario[cenario.id] = escolhas
        distintas = sorted(set(escolhas))
        marca = "estável" if len(distintas) == 1 else f"{len(distintas)} escolhas distintas"
        print(f"  [B] {cenario.id}: {marca} — {', '.join(distintas)}")


# ---------------------------------------------------------------------------
# Relatório
# ---------------------------------------------------------------------------


def linhas_do_placar(placar: Placar, cenarios: list[CenarioDeDev]) -> list[str]:
    js = placar.julgamentos
    total = len(js)
    com_args = [j for j in js if j.args_validos is not None]
    com_gold = [j for j in js if j.args_batem is not None]
    acertou_recall = sum(a for a, _ in placar.recall_por_cenario.values())
    devia = sum(b for _, b in placar.recall_por_cenario.values())
    ilegiveis = [j for j in js if j.args_validos is None and j.existe_no_catalogo]

    validos = placar.taxa(sum(bool(j.args_validos) for j in com_args), len(com_args))
    batem = placar.taxa(sum(bool(j.args_batem) for j in com_gold), len(com_gold))
    linhas = [
        f"### `{placar.modelo}`",
        "",
        "| Métrica | Valor | O que ela responde |",
        "|---|---|---|",
        f"| Chamadas emitidas | {total} | denominador de tudo abaixo |",
        f"| Tool existe no catálogo | {placar.taxa(sum(j.existe_no_catalogo for j in js), total)}"
        " | o modelo inventou função? |",
        f"| Tool tolerada pelo gabarito | {placar.taxa(sum(j.tolerada for j in js), total)}"
        " | **% de tool certa** — dentro de `esperadas ∪ aceitaveis` |",
        f"| Recall das esperadas (bloco A) | {placar.taxa(acertou_recall, devia)}"
        " | cobriu a investigação que o cenário exige? |",
        f"| Args válidos contra o schema | {validos}"
        " | **% de args válidos** |",
        f"| Args batem com o gabarito | {batem}"
        " | acertou o identificador, não só o formato |",
        f"| `parse_erro` · sem tool call | {placar.sem_tool_call}"
        " | respondeu em texto quando devia chamar |",
        f"| `parse_erro` · args ilegíveis | {len(ilegiveis)}"
        " | chamou, mas os argumentos não são JSON |",
    ]
    if placar.segundos:
        media = sum(placar.segundos) / len(placar.segundos)
        linhas.append(f"| Latência média por chamada | {media:.1f}s "
                      f"| extrapolação de custo da bateria |")
    linhas += ["", "**Estabilidade da primeira chamada (bloco B, 4 seeds):**", "",
               "| Cenário | Escolhas | Estável? |", "|---|---|---|"]
    for cid, escolhas in placar.primeira_por_cenario.items():
        distintas = sorted(set(escolhas))
        linhas.append(f"| `{cid}` | {', '.join(f'`{e}`' for e in distintas)} | "
                      f"{'sim' if len(distintas) == 1 else '**não**'} |")

    ruido = sorted({j.tool for j in js if j.existe_no_catalogo and not j.tolerada})
    if ruido:
        linhas += ["", "**Tools chamadas fora do gabarito** (ruído, não necessariamente erro — "
                   "o gabarito lista o exigido, não o permitido): "
                   + ", ".join(f"`{t}`" for t in ruido)]
    if placar.erros_http:
        linhas += ["", "**Erros de transporte:**", ""] + [f"- `{e}`" for e in placar.erros_http]
    return linhas


def escrever_relatorio(placares: list[Placar], cenarios: list[CenarioDeDev],
                       base_url: str, destino: Path) -> None:
    n_tools = len(tools_visiveis())
    gold_tools = sum(len(c.esperadas) for c in cenarios)
    gold_args = sum(len(c.args_esperados) for c in cenarios)
    linhas = [
        "# T0b — baseline de tool calling",
        "",
        f"Gerado por `scripts/medir_tool_calling.py` em {time.strftime('%d/%m/%Y %H:%M')}, "
        f"contra `{base_url}`.",
        "",
        "## O que foi medido, e o que não foi",
        "",
        f"O catálogo **real** de **{n_tools} tools** derivado do contrato — não os \"~15 de "
        "rascunho\" que o `PLANO.md` previa, porque a T13 já entregou os schemas verdadeiros. "
        "Nenhuma chamada foi executada contra a API: isto mede a mecânica de escolha de função "
        "e argumento, não a resolução do cenário.",
        "",
        f"**Amostra:** os {len(cenarios)} cenários de `split: dev`, que somam **{gold_tools} "
        f"tools esperadas** e **{gold_args} com `args_esperados`** — acima das 20 solicitações "
        "que a T0b pede. Os 18 cenários de test não entraram e seguem lacrados.",
        "",
        "**Dois blocos.** *A* — plano em um passo: a solicitação real e as 18 tools, o modelo "
        "emite o conjunto de chamadas que faria (`temperature=0`). Estressa a largura do "
        "catálogo e é mais difícil que a ReAct real, onde cada passo vê o resultado do "
        "anterior — o número dele é piso, não teto. *B* — primeira chamada com "
        f"`tool_choice=required`, repetida em {len(SEEDS_DO_BLOCO_B)} seeds a `temperature=0.7`, "
        "para ver se a escolha de entrada é estável entre trials.",
        "",
        "## Resultados",
        "",
    ]
    for placar in placares:
        linhas += linhas_do_placar(placar, cenarios) + [""]
    linhas += [
        "## Leitura",
        "",
        "> _A preencher à mão depois de ler os números: o par passa, reprova, ou reprova só o "
        "8B? Reprovando, a troca é **dentro da mesma família** — cruzar famílias mata a H2 "
        "(`PLANO.md`, A1)._",
        "",
        "## Pendência declarada",
        "",
        "Os **limites reais de RPM/RPD da free tier do Gemini** — que a T0b também pede — não "
        "são medidos aqui: este script fala com o servidor local. Eles sustentam o judge e o "
        "SUT de referência (26c), e os números que circulam vieram de fontes secundárias "
        "contraditórias. Ficam para uma medição própria contra o endpoint do Gemini.",
        "",
    ]
    destino.write_text("\n".join(linhas))


# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=BASE_URL_PADRAO)
    parser.add_argument("--modelo", action="append", default=None,
                        help="id do modelo (repetível). Padrão: os dois que o servidor listar.")
    parser.add_argument("--saida", default="docs/tool_calling_baseline.md")
    parser.add_argument("--bruto", default="docs/tool_calling_baseline.json",
                        help="julgamentos crus, para auditoria")
    args = parser.parse_args()

    cenarios = carregar_cenarios_de_dev()
    tools = schemas_openai()
    operacoes = dict(carregar_operacoes())
    print(f"{len(tools)} tools no catálogo · {len(cenarios)} cenários de dev · "
          f"{sum(len(c.esperadas) for c in cenarios)} tools esperadas no gabarito")

    placares: list[Placar] = []
    with httpx.Client(base_url=args.base_url, timeout=TIMEOUT_S) as cliente:
        try:
            disponiveis = sorted(m["id"] for m in cliente.get("/models").json().get("data", []))
        except httpx.HTTPError as erro:
            print(f"servidor de inferência inacessível em {args.base_url}: {erro}\n"
                  "Suba o LM Studio e rode antes scripts/checar_servidor_de_inferencia.py")
            return 1
        alvos = args.modelo or disponiveis[:2]
        if not alvos:
            print("o servidor não lista modelo nenhum")
            return 1

        for modelo in alvos:
            print(f"\n=== {modelo} ===")
            placar = Placar(modelo=modelo)
            rodar_bloco_a(cliente, modelo, cenarios, tools, operacoes, placar)
            rodar_bloco_b(cliente, modelo, cenarios, tools, operacoes, placar)
            placares.append(placar)

    escrever_relatorio(placares, cenarios, args.base_url, RAIZ / args.saida)
    (RAIZ / args.bruto).write_text(json.dumps(
        {p.modelo: {"julgamentos": [vars(j) for j in p.julgamentos],
                    "sem_tool_call": p.sem_tool_call,
                    "primeira_por_cenario": p.primeira_por_cenario,
                    "recall_por_cenario": p.recall_por_cenario,
                    "erros_http": p.erros_http} for p in placares},
        ensure_ascii=False, indent=2))
    print(f"\nescrito: {args.saida} e {args.bruto}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
