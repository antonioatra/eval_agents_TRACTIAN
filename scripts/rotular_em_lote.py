"""T22 — rotular fora do terminal: a fila sai em texto, as respostas voltam em arquivo.

POR QUE ISTO EXISTE, E O QUE ELE NÃO É
    A CLI de `tapieval.labeling` é interativa e pressupõe uma pessoa no terminal. Quando a
    sessão acontece noutro lugar — um chat, um editor, um par de olhos que não é o dono do
    shell —, as 35 rotulagens precisam de um caminho que não seja digitar `jsonl` à mão.

    Este script é esse caminho, e ele NÃO reimplementa nada: `mostrar` chama a mesma
    `apresentar_caso`, e `gravar` conduz a mesma `rodar_sessao` pelo `ler` que ela já recebe
    injetado — o motivo pelo qual ela recebe. Validação do `RotuloHumano`, `None`-nunca-`False`
    nos três campos de trace, append-only com flush, retomada por `run_id` e a amostra
    congelada do X27 continuam sendo os do instrumento, não cópias.

A CEGUEIRA MUDA DE NATUREZA, E ISSO PRECISA ESTAR ESCRITO
    Em `tapieval.labeling` a cegueira é ESTRUTURAL: o pacote não tem caminho de código que abra
    `runs/<id>/scores/`, e há teste que varre o pacote atrás de um. Este script herda a
    propriedade — `tests/test_rotular_em_lote.py` faz a mesma varredura sobre ele —, mas ela
    para na saída do processo. Quem intermedeia o texto pode ter visto a saída do judge por
    outro caminho, e uma âncora não deixa rastro no κ. Rotular assim é decisão de método: vale
    um parágrafo no DECISOES e uma limitação declarada na INS.6, não uma flag.

O ÍNDICE É OPACO DE PROPÓSITO
    A chave da resposta é a posição na fila, nunca o `run_id`: `run_id` carrega o `model_key`
    (`runner/matriz.py`), e `apresentar_caso` esconde o modelo justamente para que saber "é o
    menor" não anteceda a leitura do caso. Uma chave que vazasse o modelo desfaria isso.

A FILA É CONFERIDA POR IMPRESSÃO
    `mostrar` imprime um sha dos `run_id` pendentes, e `gravar` exige o mesmo valor em
    `--fila`. Sem essa conferência, responder contra uma fila velha — alguém rotulou no
    terminal no meio, um trace foi refeito — gravaria o rótulo no `run_id` errado em silêncio,
    e o κ compararia pares que nunca foram pares. É o mesmo formato de falha que o X27 fechou
    para a amostra, um nível acima.

SÓ MODO CEGO
    `com_trace` acrescenta três campos, um deles lista de tamanho livre, e é a configuração que
    NÃO sustenta o κ (`METRICAS §5`). Fica recusado por nome, em vez de meio-suportado.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ / "src") not in sys.path:  # execução direta, sem `pip install -e`
    sys.path.insert(0, str(RAIZ / "src"))

from tapieval.labeling.amostra import (  # noqa: E402
    N_ESTIMATIVA,
    N_MELHORIA,
    SEED_DA_AMOSTRAGEM,
    ItemDaAmostra,
)
from tapieval.labeling.cli import (  # noqa: E402
    DIRETORIO_DE_ROTULOS,
    amostra_da_sessao,
    apresentar_caso,
    caminho_da_amostra,
    caminho_do_dia,
    carregar_candidatos,
    montador_de_insumo,
    rodar_sessao,
    run_ids_ja_rotulados,
)
from tapieval.scoring.gabarito import carregar_cenarios  # noqa: E402

CONFIGURACAO = "cego"

CHAVES = ("n3.1", "n3.2", "n3.5", "just")

BOOL = {"s": "s", "sim": "s", "true": "s", "n": "n", "nao": "n", "não": "n", "false": "n"}
ESCOLHA = {"s": "s", "sim": "s", "p": "p", "parcial": "p", "n": "n", "nao": "n", "não": "n"}


class ErroDeLote(Exception):
    """Tudo que faz o lote parar antes de gravar. Nunca meio-gravado."""


class RoteiroEsgotado(RuntimeError):
    """A sessão pediu mais do que o bloco respondeu.

    NÃO herda de `EOFError` de propósito: `rodar_sessao` trata `EOFError` como interrupção
    normal e encerra em silêncio, o que aqui esconderia uma resposta recusada por validação —
    a pergunta seria repetida, consumiria a resposta seguinte, e o rótulo sairia deslocado.
    """


# ---------------------------------------------------------------------------
# A fila
# ---------------------------------------------------------------------------


def fila_pendente(
    run_dir: Path,
    labels_dir: Path,
    *,
    seed: int = SEED_DA_AMOSTRAGEM,
    n_estimativa: int = N_ESTIMATIVA,
    n_melhoria: int = N_MELHORIA,
    escrever: Callable[[str], None] = print,
) -> tuple[ItemDaAmostra, ...]:
    """A amostra congelada menos o que já tem rótulo, na ordem em que ela foi sorteada."""
    candidatos = carregar_candidatos(run_dir)
    itens = amostra_da_sessao(
        caminho_da_amostra(labels_dir, run_dir),
        candidatos,
        n_estimativa=n_estimativa,
        n_melhoria=n_melhoria,
        seed=seed,
        reamostrar=False,
        escrever=escrever,
    )
    feitos = run_ids_ja_rotulados(labels_dir)
    return tuple(item for item in itens if item.candidato.run_id not in feitos)


def impressao_da_fila(itens: Sequence[ItemDaAmostra]) -> str:
    """Os `run_id` pendentes, em ordem, num sha curto. Muda a fila, muda a impressão."""
    corpo = "\n".join(item.candidato.run_id for item in itens)
    return hashlib.sha256(corpo.encode("utf-8")).hexdigest()[:12]


def renderizar(
    itens: Sequence[ItemDaAmostra],
    *,
    insumo_de: Callable[[ItemDaAmostra], object],
    quantos: int,
) -> str:
    """Os próximos `quantos` casos, como o rotulador os vê. Nada além disso vai para a tela."""
    blocos = []
    for indice, item in enumerate(itens[:quantos], start=1):
        blocos.append(f"caso {indice}")
        blocos.append(
            apresentar_caso(
                insumo_de(item),  # type: ignore[arg-type]
                CONFIGURACAO,
                posicao=indice,
                total=len(itens),
                amostra=item.amostra,
            )
        )
    return "\n".join(blocos)


# ---------------------------------------------------------------------------
# As respostas
# ---------------------------------------------------------------------------


def analisar(texto: str) -> dict[int, list[str]]:
    """Blocos `caso N` → a sequência de teclas que `rodar_sessao` espera no modo cego.

    Valida VALOR aqui, e não deixa a CLI validar, por um motivo mecânico: pergunta recusada é
    pergunta repetida, e repetida ela consumiria a resposta do campo seguinte. O rótulo sairia
    deslocado por um, com todos os campos preenchidos e nenhum deles no lugar.
    """
    blocos: dict[int, dict[str, str]] = {}
    atual: dict[str, str] | None = None
    ultima_chave: str | None = None

    for numero, linha in enumerate(texto.splitlines(), start=1):
        crua = linha.strip()
        if not crua:
            continue
        cabeca = crua.lower()
        if cabeca.startswith("caso "):
            indice = cabeca.removeprefix("caso ").strip().rstrip(":")
            if not indice.isdigit():
                raise ErroDeLote(f"linha {numero}: `{crua}` não nomeia um caso da fila")
            if int(indice) in blocos:
                raise ErroDeLote(f"linha {numero}: o caso {indice} aparece duas vezes")
            atual = {}
            blocos[int(indice)] = atual
            ultima_chave = None
            continue
        if atual is None:
            raise ErroDeLote(f"linha {numero}: resposta antes de qualquer `caso N`")

        chave, separador, valor = crua.partition(":")
        chave = chave.strip().lower()
        if separador and chave in CHAVES:
            if chave in atual:
                raise ErroDeLote(f"linha {numero}: `{chave}` repetida no mesmo caso")
            atual[chave] = valor.strip()
            ultima_chave = chave
            continue
        if ultima_chave == "just":
            # Justificativa de várias linhas: colada numa só, porque `_perguntar_texto` lê uma.
            atual["just"] = f"{atual['just']} {crua}".strip()
            continue
        raise ErroDeLote(
            f"linha {numero}: `{crua}` não é `{'` / `'.join(CHAVES)}` nem continuação de `just`"
        )

    if not blocos:
        raise ErroDeLote("nenhum bloco `caso N` no arquivo de respostas")
    return {indice: _teclas(indice, campos) for indice, campos in sorted(blocos.items())}


def _teclas(indice: int, campos: dict[str, str]) -> list[str]:
    faltando = [chave for chave in CHAVES if chave not in campos]
    if faltando:
        raise ErroDeLote(f"caso {indice}: falta {', '.join(f'`{c}`' for c in faltando)}")
    return [
        "r",
        _valor(indice, "n3.1", campos["n3.1"], BOOL, "s / n"),
        _valor(indice, "n3.2", campos["n3.2"], BOOL, "s / n"),
        _valor(indice, "n3.5", campos["n3.5"], ESCOLHA, "s / p / n"),
        _justificativa(indice, campos["just"]),
    ]


def _valor(indice: int, chave: str, cru: str, aceitos: dict[str, str], dica: str) -> str:
    valor = aceitos.get(cru.strip().lower())
    if valor is None:
        raise ErroDeLote(f"caso {indice}: `{chave}: {cru}` não é {dica}")
    return valor


def _justificativa(indice: int, cru: str) -> str:
    if not cru.strip():
        raise ErroDeLote(
            f"caso {indice}: a justificativa é obrigatória (METRICAS §4) — sem ela o rótulo "
            "não audita"
        )
    return cru.strip()


class Roteiro:
    """Um `ler` de bloco: uma resposta por pergunta, e explode se sobrar pergunta."""

    def __init__(self, teclas: Sequence[str], *, caso: int):
        self.teclas = list(teclas)
        self.caso = caso

    def __call__(self, pergunta: str) -> str:
        if not self.teclas:
            raise RoteiroEsgotado(
                f"caso {self.caso}: a sessão perguntou `{pergunta.strip()}` e o bloco não "
                "tem mais resposta — nada foi gravado para este caso"
            )
        return self.teclas.pop(0)


# ---------------------------------------------------------------------------
# A gravação
# ---------------------------------------------------------------------------


def gravar_lote(
    itens: Sequence[ItemDaAmostra],
    respostas: dict[int, list[str]],
    *,
    insumo_de: Callable[[ItemDaAmostra], object],
    rotulador: str,
    labels_dir: Path,
    escrever: Callable[[str], None] = print,
) -> int:
    """Um caso por chamada de `rodar_sessao`, com a fila resolvida ANTES de gravar a primeira.

    Um caso por chamada porque é o que torna o índice auditável: a sessão recebe o item que o
    bloco nomeia, e não o próximo da fila. Passar a fila inteira e confiar na ordem faria um
    bloco faltante deslocar todos os seguintes, em silêncio.
    """
    fora = [indice for indice in respostas if not 1 <= indice <= len(itens)]
    if fora:
        raise ErroDeLote(
            f"caso(s) {', '.join(map(str, sorted(fora)))} fora da fila, que tem "
            f"{len(itens)} pendente(s)"
        )

    alvos = [(indice, itens[indice - 1]) for indice in sorted(respostas)]
    destino = caminho_do_dia(labels_dir)
    gravados = 0
    for indice, item in alvos:
        gravados += rodar_sessao(
            [item],
            insumo_de=insumo_de,  # type: ignore[arg-type]
            configuracao=CONFIGURACAO,
            rotulador=rotulador,
            destino=destino,
            ja_rotulados=run_ids_ja_rotulados(labels_dir),
            ler=Roteiro(respostas[indice], caso=indice),
            escrever=lambda _: None,  # o caso já foi apresentado no `mostrar`
        )
        escrever(f"caso {indice}: rotulado")
    escrever(f"{gravados} rótulo(s) gravado(s) em {destino}")
    return gravados


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Rotulagem cega conduzida por arquivo (T22). `mostrar` imprime os próximos casos "
            "e a impressão da fila; `gravar` lê as respostas e conduz a sessão real."
        )
    )
    parser.add_argument("acao", choices=("mostrar", "gravar"))
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--labels-dir", type=Path, default=DIRETORIO_DE_ROTULOS)
    parser.add_argument("--quantos", type=int, default=5, help="casos por lote no `mostrar`")
    parser.add_argument("--respostas", type=Path, help="arquivo de blocos `caso N`")
    parser.add_argument("--fila", help="a impressão da fila que o `mostrar` imprimiu")
    parser.add_argument("--rotulador")
    parser.add_argument("--seed", type=int, default=SEED_DA_AMOSTRAGEM)
    parser.add_argument("--n-estimativa", type=int, default=N_ESTIMATIVA)
    parser.add_argument("--n-melhoria", type=int, default=N_MELHORIA)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    try:
        itens = fila_pendente(
            args.run_dir,
            args.labels_dir,
            seed=args.seed,
            n_estimativa=args.n_estimativa,
            n_melhoria=args.n_melhoria,
            escrever=lambda linha: print(linha, file=sys.stderr),
        )
    except (OSError, ValueError) as erro:
        print(f"erro ao montar a fila: {erro}", file=sys.stderr)
        return 2

    if not itens:
        print("nada pendente: todos os casos da amostra já têm rótulo", file=sys.stderr)
        return 0

    insumo_de = montador_de_insumo(args.run_dir, carregar_cenarios())

    if args.acao == "mostrar":
        print(f"fila: {impressao_da_fila(itens)}  ·  {len(itens)} caso(s) pendente(s)")
        print(renderizar(itens, insumo_de=insumo_de, quantos=args.quantos))
        return 0

    if not args.respostas or not args.rotulador:
        print("gravar exige --respostas e --rotulador", file=sys.stderr)
        return 2
    if args.fila != impressao_da_fila(itens):
        print(
            f"erro: a fila mudou desde o `mostrar` (--fila={args.fila}, agora "
            f"{impressao_da_fila(itens)}). Rode `mostrar` de novo: gravar contra a fila "
            "velha poria o rótulo no run errado.",
            file=sys.stderr,
        )
        return 2

    try:
        respostas = analisar(args.respostas.read_text(encoding="utf-8"))
        gravar_lote(
            itens,
            respostas,
            insumo_de=insumo_de,
            rotulador=args.rotulador,
            labels_dir=args.labels_dir,
        )
    except (ErroDeLote, RoteiroEsgotado) as erro:
        print(f"erro no lote: {erro}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
