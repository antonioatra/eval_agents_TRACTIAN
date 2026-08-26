#!/usr/bin/env python3
"""Preenche o `erro` das células `error` de um manifesto JÁ FECHADO, a partir do trace.

POR QUE ISTO EXISTE, E POR QUE É UM SCRIPT E NÃO UM `sed`
    `20ffef8` fez o runner repetir no manifesto o último `RunError` fatal do trace, porque
    `error` é **resultado do experimento** e medida sem causa não se agrega. A correção vale
    para execução nova — e a bateria de calibração de 26/08 já tinha fechado com duas células
    `error` e `erro: null`, com o motivo legível só abrindo o trace.

    Reexecutar as duas trocaria resultado por resultado: elas são medida, não falha do
    instrumento. Preencher o campo a partir do trace é **derivação sem perda** — a informação
    já estava no disco, só não estava agregada. Mas continua sendo edição no registro de um
    experimento encerrado, e edição assim precisa ser auditável: por isso um script versionado,
    que usa a MESMA função do runner (`_erro_fatal_do_agente`) em vez de uma segunda redação,
    e não um comando de terminal que ninguém consegue reproduzir depois.

O QUE ELE NÃO FAZ
    Não toca em célula cujo `erro` já está preenchido (a exceção do harness é mais específica
    que qualquer evento do trace), não toca em célula que não seja `status="error"`, e não
    inventa motivo: célula `error` sem `RunError` fatal no trace fica como está e é reportada.
    Sem `--gravar` ele só mostra o que faria.

Uso:
    python scripts/derivar_erro_do_trace.py runs/calibracao_2026-08-24
    python scripts/derivar_erro_do_trace.py runs/calibracao_2026-08-24 --gravar
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from tapieval.runner.runner import _erro_fatal_do_agente  # noqa: E402
from tapieval.schema.reader import read_trace  # noqa: E402


def derivar(run_dir: Path) -> list[tuple[str, str]]:
    """`(run_id, motivo)` de cada célula `error` que está sem motivo e tem um no trace."""
    manifesto = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    achados: list[tuple[str, str]] = []
    for run_id, registro in manifesto.get("runs", {}).items():
        if registro.get("status") != "error" or registro.get("erro"):
            continue
        caminho = run_dir / registro["trace"]
        if not caminho.exists():
            print(f"⚠️  {run_id}: trace ausente em {caminho} — deixado como está")
            continue
        motivo = _erro_fatal_do_agente(read_trace(caminho))
        if motivo is None:
            print(f"⚠️  {run_id}: `error` sem `RunError` fatal no trace — deixado como está")
            continue
        achados.append((run_id, motivo))
    return achados


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument(
        "--gravar", action="store_true",
        help="sem isto, só mostra o que faria — o default é não editar registro fechado",
    )
    args = parser.parse_args(argv)

    achados = derivar(args.run_dir)
    if not achados:
        print("nenhuma célula `error` sem motivo derivável.")
        return 0

    for run_id, motivo in achados:
        print(f"{run_id}\n    erro: {motivo}")

    if not args.gravar:
        print(f"\n{len(achados)} célula(s) — nada gravado. Use --gravar.")
        return 0

    caminho = args.run_dir / "manifest.json"
    manifesto = json.loads(caminho.read_text(encoding="utf-8"))
    for run_id, motivo in achados:
        manifesto["runs"][run_id]["erro"] = motivo
    caminho.write_text(
        json.dumps(manifesto, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\n{len(achados)} célula(s) preenchida(s) em {caminho}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
