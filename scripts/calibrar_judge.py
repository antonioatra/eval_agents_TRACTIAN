#!/usr/bin/env python3
"""T21 — o judge 5× sobre os mesmos itens, para medir o flip rate por campo (INS.7).

O QUE ESTE SCRIPT MEDE, E CONTRA O QUÊ
    `METRICAS §7` define INS.7 como "judge 5× sobre os mesmos itens, % de mudança por
    campo", e a causa que ela isola é **ambiguidade da rubrica** — não erro do agente, não
    amostragem do modelo. Por isso a temperatura fica em 0,0 (o default de `config_do_judge`):
    com temperatura alta o número mediria variação que o prompt não causou, e a reescrita da
    rubrica que ele motiva seria feita para consertar ruído.

SÓ O DEV SET, E ISSO NÃO É DETALHE
    `METRICAS §9.3` separa dev de test para que a rubrica possa ser reescrita olhando os
    dados sem contaminar o resultado. Calibrar contra test é escolher o prompt que faz o
    número final ficar bonito. Todas as passadas da piloto rodaram exatamente os 6 cenários
    de dev, então qualquer combinação delas continua dentro do dev — ver
    `RUNS_EM_ORDEM_DE_PREFERENCIA` para por que mais de uma passada entra.

O CAMPO ONDE O JULGAMENTO NÃO EXISTE É `None`, E `None` NÃO FLIPA
    Os três campos que exigem trace saem `None` na configuração cega por construção
    (`scoring/n3.py`). O resumo os conta separado em vez de tratar `None` como um valor a
    mais: se `None` entrasse na conta, o cego apareceria com flip rate 0% em três campos que
    ele nem responde, e a média por configuração ficaria falsamente estável.

ESTE SCRIPT TAMBÉM É A SONDA DO A1
    Os limites de RPM/RPD da free tier não estão publicados — a página de rate limits do
    Google manda consultar o AI Studio em vez de imprimir a tabela (verificado em 24/08).
    A calibração é a primeira carga real do projeto contra a API, então ela é a chance mais
    barata de descobrir onde os limites ficam: `ClienteDoJudge.eventos_de_limite` guarda todo
    status transitório com o instante, e o resumo os despeja em `limites.json`. Uma sonda
    sintética separada queimaria a mesma quota sem produzir julgamento nenhum.

RETOMADA É REQUISITO, NÃO CONFORTO
    Se o RPD estourar no meio, o processo morre — e o que já foi julgado tem de sobreviver.
    A gravação é append-only, uma linha por (trace, configuração, repetição), e a chave
    dessas três coisas é o que o script relê na entrada para pular o que já existe. Rodar de
    novo depois do estouro continua de onde parou, sem regravar nem duplicar.

Uso:
    python scripts/calibrar_judge.py                      # dev estratificado, 5×
    python scripts/calibrar_judge.py --repeticoes 3 --configuracoes com_trace
    python scripts/calibrar_judge.py --por-cenario 3      # amostra menor, quota apertada
    python scripts/calibrar_judge.py --somente-resumo     # sem rede, relê o que já existe
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from tapieval.schema.custo import MedidorDeCusto  # noqa: E402
from tapieval.schema.reader import read_trace  # noqa: E402
from tapieval.scoring.gabarito import carregar_cenarios  # noqa: E402
from tapieval.scoring.judge_llm import ClienteDoJudge, config_do_judge  # noqa: E402
from tapieval.scoring.n3 import (  # noqa: E402
    CAMADA_POR_CONFIGURACAO,
    JustificativaComIdInventado,
    montar_insumo,
    pontuar_n3,
)

CONFIGURACOES_PADRAO = ("cego", "com_trace")

CAMPOS_COMPARTILHADOS = (
    "causa_raiz_correta",
    "mencionou_limitacao_relevante",
    "responde_a_pergunta",
)
CAMPOS_SO_COM_TRACE = (
    "afirmacoes_sem_suporte",
    "contradiz_evidencia",
    "recomendou_acao_sem_base",
)
CAMPOS = CAMPOS_COMPARTILHADOS + CAMPOS_SO_COM_TRACE

CORTE_DE_FLIP = 0.10
"""A linha do enunciado da T21: campo acima de 10% de flip é reescrito na v2 da rubrica.

O corte é decisão de projeto, não estatística — e por isso fica nomeado aqui em vez de
espalhado por comparações soltas. Quem quiser discutir onde ele deveria estar discute esta
constante."""


# ---------------------------------------------------------------------------
# Seleção dos itens
# ---------------------------------------------------------------------------


RUNS_EM_ORDEM_DE_PREFERENCIA = (
    "piloto_2026-08-24c",
    "piloto_2026-08-24",
    "piloto_2026-08-24b",
    "piloto_2026-08-23",
)
"""As passadas da piloto, da mais representativa do SUT atual para a menos.

A 24c é o SUT de hoje (depois da reversão do rótulo seco) e é a primeira escolha. As outras
entram porque a 24c **não cobre os seis cenários de dev**: `aut_01` e `aut_06` não terminaram
nenhuma run nela, e `aut_06_premissa_falsa` é onde `afirmacoes_sem_suporte` e
`contradiz_evidencia` têm mais o que fazer — calibrar a rubrica sem ele deixaria cegos os dois
campos que mais dependem dela.

A 24b vem DEPOIS da 24 apesar de ser mais nova: ela roda o rótulo seco, que foi revertido, e
é a passada menos parecida com o SUT que vai para as baterias.

O QUE ISSO CUSTA, DECLARADO: os itens de passadas antigas são respostas de um SUT que já
mudou. Para o flip rate isso não contamina nada — INS.7 mede a rubrica julgando o MESMO item
cinco vezes, e a variação medida é do judge, não do agente. Contaminaria se estes julgamentos
virassem gold para κ, e não viram: o κ da T23 sai da rotulagem humana sobre as baterias.
"""


def compor_amostra(
    runs: Sequence[Path], *, por_cenario: int
) -> tuple[list[Path], dict[str, list[str]]]:
    """Até `por_cenario` itens de cada cenário, varrendo as runs na ordem de preferência.

    A cota por cenário existe para o flip rate não virar a média de um cenário só: sem ela,
    `cen_09` entraria com 12 itens e `aut_01` com 1, e "a rubrica é estável" significaria
    "a rubrica é estável em cen_09". Estratificar aqui é mais barato que ponderar depois.
    """
    por_cenario_escolhidos: dict[str, list[Path]] = defaultdict(list)
    for run in runs:
        if not (run / "traces").is_dir():
            continue
        for caminho in traces_julgaveis(run):
            cenario = caminho.name.split("--")[0]
            if len(por_cenario_escolhidos[cenario]) < por_cenario:
                por_cenario_escolhidos[cenario].append(caminho)

    escolhidos = [
        caminho
        for cenario in sorted(por_cenario_escolhidos)
        for caminho in por_cenario_escolhidos[cenario]
    ]
    procedencia = {
        cenario: [caminho.parent.parent.name for caminho in caminhos]
        for cenario, caminhos in sorted(por_cenario_escolhidos.items())
    }
    return escolhidos, procedencia


def traces_julgaveis(run: Path) -> list[Path]:
    """Os traces do diretório que têm o que julgar, em ordem estável.

    Run sem `final_answer` não entra: o judge julga a resposta ao usuário, e uma run que
    esgotou o orçamento sem responder não tem resposta para julgar. Ela não é descartada do
    projeto — vira falha de processo na N2 —, mas colocá-la aqui produziria cinco julgamentos
    de string vazia e um flip rate que mede o vazio.
    """
    diretorio = run / "traces"
    if not diretorio.is_dir():
        raise SystemExit(f"não achei traces em {diretorio}")

    cenarios = carregar_cenarios()
    escolhidos: list[Path] = []
    for caminho in sorted(diretorio.glob("*.jsonl")):
        cenario = cenarios.get(caminho.name.split("--")[0])
        if cenario is None:
            continue
        if montar_insumo(read_trace(caminho), cenario).resposta:
            escolhidos.append(caminho)
    return escolhidos


# ---------------------------------------------------------------------------
# Gravação append-only, com retomada
# ---------------------------------------------------------------------------


def chave(linha: Mapping[str, Any]) -> tuple[str, str, int]:
    return (linha["trace"], linha["configuracao"], int(linha["repeticao"]))


def ja_gravados(arquivo: Path) -> set[tuple[str, str, int]]:
    """As células que já existem. Linha corrompida é ignorada e será refeita.

    Ignorar em vez de explodir é deliberado: a única forma de uma linha sair pela metade é o
    processo ter morrido no meio da escrita, que é exatamente o cenário que a retomada existe
    para atender. Explodir aqui transformaria a interrupção em trabalho perdido.
    """
    if not arquivo.exists():
        return set()
    vistos: set[tuple[str, str, int]] = set()
    for linha in arquivo.read_text(encoding="utf-8").splitlines():
        if not linha.strip():
            continue
        try:
            vistos.add(chave(json.loads(linha)))
        except (json.JSONDecodeError, KeyError):
            continue
    return vistos


def ler_julgamentos(arquivo: Path) -> list[dict[str, Any]]:
    if not arquivo.exists():
        return []
    saida: list[dict[str, Any]] = []
    for linha in arquivo.read_text(encoding="utf-8").splitlines():
        if linha.strip():
            try:
                saida.append(json.loads(linha))
            except json.JSONDecodeError:
                continue
    return saida


def gravar(arquivo: Path, linha: Mapping[str, Any]) -> None:
    with arquivo.open("a", encoding="utf-8") as saida:
        saida.write(json.dumps(linha, ensure_ascii=False, sort_keys=True) + "\n")
        saida.flush()


# ---------------------------------------------------------------------------
# Flip rate
# ---------------------------------------------------------------------------


def _valor_comparavel(valor: Any) -> Any:
    """`afirmacoes_sem_suporte` é lista de texto livre, e comparar texto seria medir redação.

    O que a rubrica decide nesse campo é **se há afirmação sem suporte e quantas** — a frase
    exata que o judge escolhe para descrevê-la é estilo, e duas redações da mesma acusação
    contariam como flip. Comparar pela CONTAGEM mantém o campo no resumo sem inflá-lo com
    variação que não é da rubrica.

    O preço está declarado: duas listas de tamanho 2 apontando afirmações diferentes contam
    como estáveis aqui. Esse caso é lido à mão no notebook, sobre as justificativas.
    """
    if isinstance(valor, (list, tuple)):
        return len(valor)
    return valor


def flip_por_campo(
    julgamentos: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """% de itens em que as repetições não foram unânimes, campo a campo (INS.7).

    Um item é o par (trace, configuração) — a mesma execução julgada pela mesma configuração.
    Ele conta como *flipado* no campo se as repetições devolveram mais de um valor distinto.
    É a leitura literal de "% de mudança por campo" de `METRICAS §7`, e é mais severa que a
    alternativa (distância à moda): um item que flipou uma vez em cinco já conta inteiro.
    Severa é o que se quer de um alarme de ambiguidade.

    Item com uma repetição só é excluído da conta — com n=1 não há o que flipar, e mantê-lo
    no denominador diluiria o número na direção de "a rubrica é estável".
    """
    por_item: dict[tuple[str, str], dict[str, list[Any]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for linha in julgamentos:
        item = (linha["trace"], linha["configuracao"])
        for campo in CAMPOS:
            valor = linha["julgamento"].get(campo)
            if valor is not None:
                por_item[item][campo].append(_valor_comparavel(valor))

    resumo: dict[str, dict[str, Any]] = {}
    for campo in CAMPOS:
        itens = [valores[campo] for valores in por_item.values() if len(valores[campo]) > 1]
        flipados = [valores for valores in itens if len(set(map(repr, valores))) > 1]
        total = len(itens)
        resumo[campo] = {
            "itens": total,
            "flipados": len(flipados),
            "flip_rate": (len(flipados) / total) if total else None,
            "acima_do_corte": (len(flipados) / total > CORTE_DE_FLIP) if total else None,
        }
    return resumo


def resumo_de_custo(julgamentos: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    por_config: dict[str, Counter[str]] = defaultdict(Counter)
    for linha in julgamentos:
        custo = linha.get("custo") or {}
        alvo = por_config[linha["configuracao"]]
        alvo["chamadas"] += 1
        alvo["tokens_in"] += int(custo.get("tokens_in", 0))
        alvo["tokens_out"] += int(custo.get("tokens_out", 0))
        alvo["segundos"] += int(round(float(custo.get("segundos", 0.0))))
    return {config: dict(contagem) for config, contagem in por_config.items()}


# ---------------------------------------------------------------------------
# Execução
# ---------------------------------------------------------------------------


def _serializar(julgamento: Any) -> dict[str, Any]:
    bruto = asdict(julgamento) if is_dataclass(julgamento) else dict(julgamento)
    return {chave: valor for chave, valor in bruto.items()}


def identificar(caminho: Path) -> str:
    """`<run>/<trace>`, e não só o nome do arquivo.

    O mesmo cenário aparece com nome de arquivo IDÊNTICO em passadas diferentes da piloto —
    `aut_03...n23.jsonl` existe em quatro delas. Chavear só pelo nome faria a retomada
    considerar já-feito um item de outra passada, e o flip rate juntaria num item só
    julgamentos de duas respostas diferentes."""
    return f"{caminho.parent.parent.name}/{caminho.name}"


def rodar(
    runs: Sequence[Path],
    saida: Path,
    *,
    repeticoes: int,
    configuracoes: Sequence[str],
    limite: int | None,
    por_cenario: int,
) -> int:
    saida.mkdir(parents=True, exist_ok=True)
    arquivo = saida / "julgamentos.jsonl"

    caminhos, procedencia = compor_amostra(runs, por_cenario=por_cenario)
    if limite is not None:
        caminhos = caminhos[:limite]
    if not caminhos:
        raise SystemExit(f"nenhum trace com `final_answer` em {[str(r) for r in runs]}")

    cenarios = carregar_cenarios()
    feitos = ja_gravados(arquivo)

    celulas = [
        (caminho, configuracao, repeticao)
        for caminho in caminhos
        for configuracao in configuracoes
        for repeticao in range(1, repeticoes + 1)
        if (identificar(caminho), configuracao, repeticao) not in feitos
    ]

    total = len(caminhos) * len(configuracoes) * repeticoes
    print(f"itens: {len(caminhos)} traces × {len(configuracoes)} configurações × {repeticoes}×")
    for cenario, origens in procedencia.items():
        print(f"  {cenario:<36} {len(origens):>2} · {', '.join(origens)}")
    (saida / "amostra.json").write_text(
        json.dumps(
            {"por_cenario": por_cenario, "procedencia": procedencia,
             "itens": [identificar(c) for c in caminhos]},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    print(f"células: {total} · já feitas: {total - len(celulas)} · a fazer: {len(celulas)}\n")
    if not celulas:
        print("nada a fazer — tudo já está gravado.")
        return 0

    modelo = config_do_judge()
    falhas = 0
    cliente = ClienteDoJudge(modelo)
    # `try/finally` e não `with` porque `limites.json` precisa ser escrito TAMBÉM quando a
    # rodada morre no meio — que é justamente quando ele tem mais a dizer. Um `with` que só
    # gravasse no caminho feliz perderia o registro exatamente na noite em que a free tier
    # derrubou a bateria, que é a informação que o A1 está esperando.
    try:
        for indice, (caminho, configuracao, repeticao) in enumerate(celulas, start=1):
            cenario = cenarios[caminho.name.split("--")[0]]
            insumo = montar_insumo(read_trace(caminho), cenario)
            medidor = MedidorDeCusto("calibracao", CAMADA_POR_CONFIGURACAO[configuracao])
            rotulo = f"[{indice}/{len(celulas)}] {caminho.name[:44]} {configuracao} #{repeticao}"

            try:
                julgamento = pontuar_n3(insumo, configuracao, cliente, medidor)
            except JustificativaComIdInventado as erro:
                # Não é falha de transporte: o judge respondeu, e respondeu citando id que
                # não existe, três vezes seguidas. É achado sobre a rubrica — o campo
                # `justificativa` está pedindo mais do que o modelo consegue entregar — e
                # some se a célula for simplesmente pulada em silêncio.
                falhas += 1
                print(f"{rotulo} · ID INVENTADO: {erro}")
                gravar(
                    arquivo,
                    {
                        "trace": identificar(caminho),
                        "cenario": cenario.id,
                        "configuracao": configuracao,
                        "repeticao": repeticao,
                        "erro": "id_inventado",
                        "detalhe": str(erro),
                        "julgamento": {},
                        "custo": _custo_como_dict(medidor),
                        "instante": time.time(),
                    },
                )
                continue

            custo = _custo_como_dict(medidor)
            gravar(
                arquivo,
                {
                    "trace": identificar(caminho),
                    "cenario": cenario.id,
                    "configuracao": configuracao,
                    "repeticao": repeticao,
                    "erro": None,
                    "julgamento": _serializar(julgamento),
                    "custo": custo,
                    "instante": time.time(),
                },
            )
            print(f"{rotulo} · {custo['tokens_in']}in/{custo['tokens_out']}out")
    finally:
        _gravar_limites(saida, cliente)
        cliente.close()

    print(f"\ngravado em {arquivo}")
    if falhas:
        print(f"{falhas} célula(s) com id inventado — estão no arquivo, com `erro` preenchido.")
    return 0


def _custo_como_dict(medidor: MedidorDeCusto) -> dict[str, Any]:
    registro = medidor.fechar()
    bruto = asdict(registro) if is_dataclass(registro) else dict(registro)
    return {
        "tokens_in": bruto.get("tokens_in", 0),
        "tokens_out": bruto.get("tokens_out", 0),
        "segundos": bruto.get("segundos", 0.0),
    }


def _gravar_limites(saida: Path, cliente: ClienteDoJudge) -> None:
    """O material do A1: onde a free tier reclamou, e o que ela disse.

    Grava mesmo quando a lista está vazia — "rodou N chamadas e nunca bateu no limite" é
    informação sobre o limite, e um arquivo ausente seria indistinguível de "esqueci de
    medir", que é o formato de erro que o X9 nomeia.
    """
    eventos = list(cliente.eventos_de_limite)
    (saida / "limites.json").write_text(
        json.dumps(
            {
                "eventos": eventos,
                "total": len(eventos),
                "por_status": dict(Counter(evento["status"] for evento in eventos)),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    if eventos:
        print(f"\n⚠️  {len(eventos)} resposta(s) transitória(s) da API — ver limites.json")


def imprimir_resumo(saida: Path) -> int:
    julgamentos = [
        linha for linha in ler_julgamentos(saida / "julgamentos.jsonl") if not linha.get("erro")
    ]
    if not julgamentos:
        raise SystemExit(f"nenhum julgamento válido em {saida}")

    print(f"julgamentos válidos: {len(julgamentos)}\n")
    print(f"{'campo':<34} {'itens':>6} {'flip':>6} {'taxa':>8}")
    print("-" * 58)
    for campo, dados in flip_por_campo(julgamentos).items():
        taxa = dados["flip_rate"]
        marca = " ← acima do corte" if dados["acima_do_corte"] else ""
        texto = f"{taxa:.1%}" if taxa is not None else "—"
        print(f"{campo:<34} {dados['itens']:>6} {dados['flipados']:>6} {texto:>8}{marca}")
    print(f"\ncorte da T21: {CORTE_DE_FLIP:.0%} — campo acima disso é reescrito na v2\n")

    for config, custo in resumo_de_custo(julgamentos).items():
        print(
            f"{config:<10} {custo['chamadas']:>4} chamadas · "
            f"{custo['tokens_in']:>7} in · {custo['tokens_out']:>7} out · "
            f"{custo['segundos']:>5}s"
        )

    (saida / "flip_rate.json").write_text(
        json.dumps(flip_por_campo(julgamentos), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runs", type=Path, nargs="+", default=None,
        help="diretórios de run, na ordem de preferência (default: as quatro passadas da piloto)",
    )
    parser.add_argument(
        "--por-cenario", type=int, default=4,
        help="teto de itens por cenário — estratifica o flip rate (default: 4)",
    )
    parser.add_argument("--saida", type=Path, default=None)
    parser.add_argument("--repeticoes", type=int, default=5)
    parser.add_argument(
        "--configuracoes", nargs="+", default=list(CONFIGURACOES_PADRAO),
        choices=list(CONFIGURACOES_PADRAO),
    )
    parser.add_argument(
        "--limite", type=int, default=None,
        help="usar só os N primeiros traces — para uma passada curta antes de gastar a quota",
    )
    parser.add_argument("--somente-resumo", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    saida = args.saida if args.saida is not None else RAIZ / "runs" / "calibracao_judge"

    if args.somente_resumo:
        return imprimir_resumo(saida)

    runs = args.runs if args.runs else [
        RAIZ / "runs" / nome for nome in RUNS_EM_ORDEM_DE_PREFERENCIA
    ]
    codigo = rodar(
        runs,
        saida,
        repeticoes=args.repeticoes,
        configuracoes=args.configuracoes,
        limite=args.limite,
        por_cenario=args.por_cenario,
    )
    if codigo == 0:
        print()
        imprimir_resumo(saida)
    return codigo


if __name__ == "__main__":
    raise SystemExit(main())
