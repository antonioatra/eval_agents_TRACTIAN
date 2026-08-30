"""`python -m tapieval.scoring --bateria runs/<experiment_id>` — a passagem determinística.

A simetria com `python -m tapieval.runner` é proposital: quem rodou a bateria à meia-noite
pontua com o mesmo formato de comando, e o resultado fica ao lado dos traces de onde saiu.

A CLI não decide nada sobre o experimento — não escolhe cenário, não filtra célula, não
pondera nada. Ela lê o manifesto, deriva, grava e **conta**. As duas flags são sobre o
arquivo de saída, não sobre a medição.

O CÓDIGO DE SAÍDA É 1 QUANDO A BATERIA NÃO ESTÁ INTEIRA
    Célula faltante e run não pontuada mudam o denominador. Sair 0 sobre uma bateria pela
    metade deixaria um `make` verde por cima de uma tabela que se lê como completa — o
    formato de falha que este projeto passa o tempo tentando não repetir. Run **não
    pontuável** (decisão ausente, trace A7) NÃO derruba o código de saída: ela é resultado
    medido, está no arquivo com o motivo escrito, e sai do denominador do `pass^k` por
    decisão e não por acidente.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from tapieval.scoring.bateria import (
    PontuacaoDaBateria,
    escrever_scores,
    pontuar_bateria,
    scorer_deterministico,
)


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tapieval.scoring",
        description=(
            "Pontua N1 e N2 de uma bateria já executada e grava `scores.jsonl`. "
            "Não fala com a rede: N3 é outra passagem."
        ),
    )
    parser.add_argument(
        "--bateria",
        required=True,
        type=Path,
        help="diretório da bateria (ex.: runs/principal_2026_08)",
    )
    parser.add_argument(
        "--saida",
        type=Path,
        default=None,
        help="onde gravar o `scores.jsonl`; por padrão, dentro do diretório da bateria",
    )
    parser.add_argument(
        "--sobrescrever-n3",
        action="store_true",
        dest="sobrescrever_n3",
        help=(
            "grava mesmo que o arquivo de destino já tenha julgamento do judge dentro. "
            "Sem isto, a passagem se recusa a apagar N3 que custou chamada de rede"
        ),
    )
    parser.add_argument(
        "--sem-gravar",
        action="store_true",
        dest="sem_gravar",
        help="só imprime as contagens; não escreve arquivo nenhum",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)

    try:
        pontuacao = pontuar_bateria(args.bateria)
    except FileNotFoundError as erro:
        print(f"erro ao pontuar: {erro}", file=sys.stderr)
        return 2

    _descrever(pontuacao)

    if not args.sem_gravar:
        try:
            destino = escrever_scores(
                pontuacao, args.saida, sobrescrever_n3=args.sobrescrever_n3
            )
        except ValueError as erro:
            print(f"erro ao gravar: {erro}", file=sys.stderr)
            return 2
        print(f"\nscores: {destino}")

    return 0 if pontuacao.completa else 1


def _descrever(pontuacao: PontuacaoDaBateria) -> None:
    scorer = scorer_deterministico()
    pontuaveis = pontuacao.pontuaveis
    sucessos = [score for score in pontuaveis if score.sucesso_binario]

    print(f"bateria {pontuacao.experiment_id} → {pontuacao.diretorio}")
    print(f"  scorer: {scorer.scorer_version} sha={scorer.sha256[:12]}… (N3 não medido)")
    print(f"  runs pontuadas:      {len(pontuacao.scores)}")
    print(f"  no denominador:      {len(pontuaveis)}  (pass^k, METRICAS §6.5)")
    print(f"  fora do denominador: {len(pontuacao.scores) - len(pontuaveis)}  (com motivo escrito)")
    print(f"  sucesso binário:     {len(sucessos)}/{len(pontuaveis)}")
    print(f"  runs não pontuadas:  {len(pontuacao.nao_pontuadas)}")
    print(f"  células faltantes:   {len(pontuacao.faltantes)}")

    # Por modelo, porque é a primeira leitura que o operador quer e a única que não depende
    # de notebook nenhum. Não é análise: é a contagem que diz se a bateria vale a pena abrir.
    por_modelo = Counter(score.model_key for score in pontuaveis)
    acertos = Counter(score.model_key for score in sucessos)
    for modelo in sorted(por_modelo):
        print(f"    {modelo}: {acertos[modelo]}/{por_modelo[modelo]}")

    for nao_pontuada in pontuacao.nao_pontuadas:
        print(f"    NÃO PONTUADA {nao_pontuada.run_id}: {nao_pontuada.motivo}")
    for run_id in pontuacao.faltantes:
        print(f"    FALTANTE {run_id}")


if __name__ == "__main__":
    raise SystemExit(main())
