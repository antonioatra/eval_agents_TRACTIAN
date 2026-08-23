"""T18 — `python -m tapieval.runner --manifest <yaml>`.

A CLI não decide nada sobre o experimento: tudo que muda a matriz está no YAML da bateria,
versionado junto com o resultado. As únicas flags são sobre **como** rodar, não sobre **o
que** rodar — `--paralelismo` e `--timeout` porque dependem da máquina daquela noite,
`--do-zero` porque é uma decisão do operador, e `--dry-run` porque conferir a matriz antes de
gastar duas madrugadas é barato.

`--paralelismo` e `--timeout` sobrescrevem o arquivo, e o **manifesto grava o valor que
valeu**, não o do YAML: quem ler o resultado depois precisa saber sob que condição ele saiu.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from tapieval.runner.manifesto import (
    CoordenadaDaCelula,
    Manifesto,
    RegistroDeRun,
    caminho_do_manifesto,
)
from tapieval.runner.matriz import Bateria, ErroDeBateria, carregar_bateria
from tapieval.runner.runner import ErroDeExecucao, rodar_bateria


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tapieval.runner",
        description="Executa uma bateria de avaliação e grava traces e manifesto.",
    )
    parser.add_argument(
        "--manifest",
        required=True,
        type=Path,
        help="YAML da bateria (ex.: configs/bateria_piloto.yaml)",
    )
    parser.add_argument(
        "--paralelismo",
        type=int,
        default=None,
        help="runs simultâneas; sobrescreve o YAML. 1 roda na própria thread (diagnóstico)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        dest="timeout_s",
        help="teto de segundos por run; sobrescreve o YAML",
    )
    parser.add_argument(
        "--do-zero",
        action="store_true",
        help=(
            "reexecuta TODAS as células, inclusive as já registradas. É o que se pede depois "
            "de mexer no agente — retomar compararia duas versões do SUT na mesma tabela"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="imprime a matriz (e os cenários excluídos) sem executar nada",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)

    try:
        bateria = carregar_bateria(args.manifest)
    except (ErroDeBateria, OSError) as erro:
        print(f"erro na bateria: {erro}", file=sys.stderr)
        return 2

    bateria = _com_sobrescritas(bateria, args)
    _descrever(bateria)

    if args.dry_run:
        return 0

    try:
        manifesto = rodar_bateria(
            bateria, retomar=not args.do_zero, ao_concluir=_imprimir_progresso
        )
    except ErroDeExecucao as erro:
        print(f"erro na execução: {erro}", file=sys.stderr)
        return 2

    return _resumir(manifesto, bateria)


def _com_sobrescritas(bateria: Bateria, args: argparse.Namespace) -> Bateria:
    trocas: dict[str, object] = {}
    if args.paralelismo is not None:
        if args.paralelismo < 1:
            raise SystemExit("--paralelismo precisa ser >= 1")
        trocas["paralelismo"] = args.paralelismo
    if args.timeout_s is not None:
        trocas["timeout_s"] = args.timeout_s
    if not trocas:
        return bateria
    return replace(bateria, **trocas)  # type: ignore[arg-type]


def _descrever(bateria: Bateria) -> None:
    celulas = bateria.expandir()
    print(f"bateria {bateria.experiment_id} → {bateria.diretorio}")
    print(
        f"  {len(bateria.cenarios)} cenários × {len(bateria.modelos)} modelos × "
        f"{len(bateria.variantes)} variantes × {len(bateria.sample_seeds)} seeds "
        f"= {len(celulas)} células"
    )
    print(f"  approver={bateria.approver} paralelismo={bateria.paralelismo}")
    for excluido in bateria.excluidos:
        # X12: mudança de denominador é fato do experimento, e aparece antes de a bateria
        # começar — não num rodapé que ninguém lê depois.
        print(f"  EXCLUÍDO {excluido.cenario_id}: {excluido.motivo}")


def _imprimir_progresso(coordenada: CoordenadaDaCelula, registro: RegistroDeRun) -> None:
    marca = "ok " if registro.valida else "INV"
    print(
        f"[{marca}] {coordenada.run_id} status={registro.status} "
        f"{registro.duracao_ms}ms tools={registro.n_tool_calls} "
        f"llm={registro.n_llm_calls}"
        + (f" defeitos={list(registro.defeitos)}" if registro.defeitos else "")
    )


def _resumir(manifesto: Manifesto, bateria: Bateria) -> int:
    """Fecha com as contagens que a T24 confere: células, traces, inválidas.

    Devolve 1 quando a bateria ficou incompleta ou produziu run inválida. Sair 0 com célula
    faltante deixaria um `make` verde sobre uma bateria pela metade — que é o formato de
    falha que este projeto passa o tempo tentando não repetir.
    """
    faltantes = manifesto.faltantes()
    invalidas = manifesto.invalidas()
    instrumento = [
        r for r in manifesto.runs.values() if r.status == "falha_do_instrumento"
    ]

    print(f"\nmanifesto: {caminho_do_manifesto(bateria.diretorio)}")
    print(f"  células declaradas: {len(manifesto.celulas)}")
    print(f"  runs registradas:   {len(manifesto.runs)}")
    print(f"  células faltantes:  {len(faltantes)}")
    print(f"  runs inválidas:     {len(invalidas)}  (fora do denominador do pass^k, com motivo)")
    print(f"  falhas do instrumento: {len(instrumento)}")

    for registro in invalidas:
        print(f"    INVÁLIDA {registro.run_id}: {registro.motivo_nao_pontuavel}")
    for coordenada in faltantes:
        print(f"    FALTANTE {coordenada.run_id}")

    return 0 if not faltantes and not invalidas else 1


if __name__ == "__main__":
    raise SystemExit(main())
