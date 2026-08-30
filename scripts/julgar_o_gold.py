#!/usr/bin/env python3
"""T23 — o judge congelado sobre os runs que TÊM rótulo, uma vez cada, para o κ da INS.6.

POR QUE NÃO DÁ PARA REUSAR A CALIBRAÇÃO
    `calibrar_judge.py` (T21) julga uma amostra ESTRATIFICADA POR CENÁRIO, 5 vezes cada, para
    medir o flip rate. São dois desenhos diferentes: lá o que interessa é a variação do judge
    sobre o mesmo item; aqui, o veredito UMA vez sobre exatamente os itens que o humano
    rotulou. Das 20 runs de estimativa do gold, só 5 caíram na amostra da T21 — julgar as
    outras 15 com o script de lá exigiria forçar a amostragem dele a coincidir com a nossa,
    que é mais frágil do que pedir a lista.

O JUDGE É O CONGELADO, E ISSO NÃO É CERIMÔNIA
    `METRICAS §7` define INS.6 como a concordância com **o judge que vai rodar**. Medir κ com
    um judge diferente do congelado produziria um número que descreve um instrumento que não
    é o do experimento — e o congelamento existe justamente para essa frase ser verificável.
    O sha vai no cabeçalho da saída e é conferido na leitura, pelo mesmo caminho da R4.

SÓ A AMOSTRA DE ESTIMATIVA
    `METRICAS §5`: a de melhoria é escolhida por dificuldade, e concordância em caso difícil
    não estima concordância na população. Julgar os 15 dela gastaria crédito para produzir um
    número que o §5 proíbe de reportar.

RETOMADA, PELO MESMO MOTIVO DE SEMPRE
    Uma chamada pendurada no Vertex (X30) não pode custar as anteriores. O arquivo é
    append-only e a retomada relê o que já está lá — a chave é `<run>/<trace>`, a mesma de
    `calibrar_judge.identificar`, porque o mesmo nome de arquivo existe em passadas diferentes.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ / "src") not in sys.path:
    sys.path.insert(0, str(RAIZ / "src"))

from tapieval.runner.judge_congelado import carregar_judge_congelado  # noqa: E402
from tapieval.schema.custo import MedidorDeCusto  # noqa: E402
from tapieval.schema.reader import read_trace  # noqa: E402
from tapieval.scoring.gabarito import carregar_cenarios  # noqa: E402
from tapieval.scoring.judge_llm import ClienteDoJudge, config_do_judge  # noqa: E402
from tapieval.scoring.n3 import (  # noqa: E402
    CAMADA_POR_CONFIGURACAO,
    montar_insumo,
    pontuar_n3,
)

CONFIGURACAO = "cego"
CAMINHO_DO_CONGELAMENTO = RAIZ / "configs" / "judge_frozen.json"


def alvos(labels_dir: Path, run_dir: Path) -> list[tuple[str, Path]]:
    """Os `run_id` de estimativa que têm rótulo, com o caminho do trace de cada um."""
    vistos: dict[str, None] = {}
    for caminho in sorted(labels_dir.glob("humano_*.jsonl")):
        if caminho.suffix != ".jsonl":
            continue
        for linha in caminho.read_text(encoding="utf-8").splitlines():
            if not linha.strip():
                continue
            rotulo = json.loads(linha)
            if rotulo.get("amostra") == "estimativa":
                vistos.setdefault(rotulo["run_id"], None)
    faltando = [
        run_id for run_id in vistos if not (run_dir / "traces" / f"{run_id}.jsonl").exists()
    ]
    if faltando:
        raise SystemExit(
            f"{len(faltando)} rótulo(s) apontam para trace que não existe em {run_dir}: "
            f"{faltando[:3]}. Rotular contra uma bateria e julgar contra outra produziria "
            "pares que nunca foram pares."
        )
    return [(run_id, run_dir / "traces" / f"{run_id}.jsonl") for run_id in vistos]


def identificar(caminho: Path) -> str:
    """`<run>/<trace>` — a mesma chave de `calibrar_judge`, pelo mesmo motivo."""
    return f"{caminho.parent.parent.name}/{caminho.name}"


def ja_julgados(arquivo: Path) -> set[str]:
    if not arquivo.exists():
        return set()
    return {
        json.loads(linha)["trace"]
        for linha in arquivo.read_text(encoding="utf-8").splitlines()
        if linha.strip()
    }


def _custo(medidor: MedidorDeCusto) -> dict[str, Any]:
    registro = medidor.fechar()
    bruto = asdict(registro) if is_dataclass(registro) else dict(registro)
    return {
        "tokens_in": bruto.get("tokens_in", 0),
        "tokens_out": bruto.get("tokens_out", 0),
        "segundos": bruto.get("segundos", 0.0),
    }


def gravar(arquivo: Path, registro: dict[str, Any]) -> None:
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    with arquivo.open("a", encoding="utf-8") as saida:
        saida.write(json.dumps(registro, ensure_ascii=False, default=str) + "\n")


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="T23 — o judge congelado sobre o gold.")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--labels-dir", type=Path, default=RAIZ / "labels")
    parser.add_argument("--saida", type=Path, default=None)
    parser.add_argument("--congelamento", type=Path, default=CAMINHO_DO_CONGELAMENTO)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    congelado = carregar_judge_congelado(args.congelamento)
    itens = alvos(args.labels_dir, args.run_dir)
    saida = args.saida or RAIZ / "runs" / f"judge_gold_{args.run_dir.name}"
    arquivo = saida / "julgamentos.jsonl"
    feitos = ja_julgados(arquivo)
    pendentes = [(rid, c) for rid, c in itens if identificar(c) not in feitos]

    print(
        f"judge congelado {congelado.scorer_version} sha={congelado.sha256[:12]}… · "
        f"{congelado.judge_model.model_id} · {CONFIGURACAO}"
    )
    print(f"{len(itens)} de estimativa · já julgados: {len(itens) - len(pendentes)} · "
          f"a fazer: {len(pendentes)}")
    if args.dry_run or not pendentes:
        return 0

    cenarios = carregar_cenarios()
    cliente = ClienteDoJudge(config_do_judge())
    falhas = 0
    try:
        for indice, (run_id, caminho) in enumerate(pendentes, start=1):
            cenario = cenarios[run_id.split("--")[0]]
            insumo = montar_insumo(read_trace(caminho), cenario)
            medidor = MedidorDeCusto("judge_gold", CAMADA_POR_CONFIGURACAO[CONFIGURACAO])
            try:
                julgamento = pontuar_n3(
                    insumo, CONFIGURACAO, cliente, medidor,
                    rubrica=congelado.scorer_version,
                )
            except Exception as erro:  # noqa: BLE001 — a célula morre, a rodada segue
                falhas += 1
                print(f"[{indice}/{len(pendentes)}] {run_id[:46]} · FALHOU: {erro}")
                continue
            gravar(arquivo, {
                "trace": identificar(caminho),
                "run_id": run_id,
                "cenario": cenario.id,
                "configuracao": CONFIGURACAO,
                "rubrica": congelado.scorer_version,
                "judge_sha256": congelado.sha256,
                "judge_model_id": congelado.judge_model.model_id,
                "julgamento": julgamento.model_dump(mode="json"),
                "custo": _custo(medidor),
                "instante": time.time(),
            })
            print(f"[{indice}/{len(pendentes)}] {run_id[:46]} · ok")
    finally:
        eventos = [asdict(e) if is_dataclass(e) else e for e in cliente.eventos_de_limite]
        (saida / "limites.json").write_text(
            json.dumps({"eventos": eventos, "total": len(eventos)},
                       ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
    print(f"\n{len(pendentes) - falhas} julgamento(s) · {falhas} falha(s) · {arquivo}")
    return 1 if falhas else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
