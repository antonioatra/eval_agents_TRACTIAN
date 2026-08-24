#!/usr/bin/env python3
"""T19 — lê uma bateria já executada e devolve os números do dimensionamento.

O QUE ELE RESPONDE, E DE ONDE TIRA CADA COISA
    O manifesto tem o que é da run (status, duração, contagens); o trace tem o que é do
    passo (latência de cada `llm_call`, `parse_ok`, tool chamada). Os dois são lidos, e
    nenhum número aqui é estimado a partir do outro.

A EXTRAPOLAÇÃO USA MEDIANA, E NÃO MÉDIA
    A distribuição de duração de run é assimétrica à direita por construção: `budget_exceeded`
    gasta o orçamento inteiro e trunca em cima, `error` morre cedo e trunca embaixo. A média
    de uma mistura dessas duas trunca-agens não descreve nenhuma das duas populações. A
    mediana por modelo é o que se multiplica; a média entra no relatório ao lado, para que a
    assimetria fique visível em vez de escondida.

    O total extrapolado sai **por modelo**, porque a matriz de `METRICAS §9.2` não é metade
    de cada: a principal é 18×2×8, a de mutantes é 6×**1**×4×5. Usar uma mediana só, dos dois
    modelos misturados, erraria a conta na direção de quem tem mais células.

USO
    python scripts/analisar_piloto.py runs/piloto_2026-08-23
    python scripts/analisar_piloto.py runs/piloto_2026-08-23 --json docs/piloto.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tapieval.runner.manifesto import ler_manifesto  # noqa: E402
from tapieval.schema.reader import read_trace  # noqa: E402

EXECUCOES_POR_BATERIA: dict[str, tuple[str, int, int]] = {
    # nome: (matriz, execuções, quantos modelos participam)
    "principal": ("18 test × 2 modelos × 1 variante × 8 sample_seed", 288, 2),
    "mutantes": ("6 × 1 modelo × 4 MUT × 5 seeds", 120, 1),
    "metamórfica": ("perturbações sobre 6 cenários × 2 modelos", 96, 2),
    "ambiente": ("6 × 2 modelos × 8 env_seed", 96, 2),
}
"""`METRICAS §9.2`. Somam 600 — o número do A16, e não os 544 do enunciado da T19, que foi
escrito antes de a tabela de baterias existir."""

DURACAO_ALVO_H = 16.0
"""As "duas madrugadas" do plano, em horas."""


@dataclass(frozen=True)
class Run:
    run_id: str
    cenario: str
    modelo: str
    status: str
    duracao_ms: int
    n_llm_calls: int
    n_tool_calls: int
    iteracoes: int
    parse_failures: int
    latencias_llm_ms: tuple[int, ...]
    llm_calls_com_parse_ok: int
    prompt_tokens: int
    completion_tokens: int

    @property
    def parse_erros(self) -> int:
        return len(self.latencias_llm_ms) - self.llm_calls_com_parse_ok


def carregar(diretorio: Path) -> list[Run]:
    manifesto = ler_manifesto(diretorio)
    if manifesto is None:
        raise SystemExit(f"{diretorio}: sem manifesto — a bateria rodou?")

    por_run_id = {celula.run_id: celula for celula in manifesto.celulas}

    runs: list[Run] = []
    for registro in manifesto.runs.values():
        coordenada = por_run_id[registro.run_id]
        eventos = read_trace(diretorio / registro.trace)
        chamadas = [e for e in eventos if e.type == "llm_call"]
        runs.append(
            Run(
                run_id=registro.run_id,
                cenario=coordenada.scenario_id,
                modelo=coordenada.model_key,
                status=registro.status,
                duracao_ms=registro.duracao_ms,
                n_llm_calls=registro.n_llm_calls,
                n_tool_calls=registro.n_tool_calls,
                iteracoes=registro.iteracoes,
                parse_failures=registro.parse_failures,
                latencias_llm_ms=tuple(c.latencia_ms for c in chamadas),
                llm_calls_com_parse_ok=sum(1 for c in chamadas if c.parse_ok),
                prompt_tokens=registro.prompt_tokens,
                completion_tokens=registro.completion_tokens,
            )
        )
    return sorted(runs, key=lambda r: r.run_id)


def _por_modelo(runs: list[Run]) -> dict[str, Any]:
    resumo: dict[str, Any] = {}
    for modelo in sorted({r.modelo for r in runs}):
        do_modelo = [r for r in runs if r.modelo == modelo]
        latencias = [ms for r in do_modelo for ms in r.latencias_llm_ms]
        chamadas = sum(len(r.latencias_llm_ms) for r in do_modelo)
        erros = sum(r.parse_erros for r in do_modelo)
        duracoes_s = [r.duracao_ms / 1000 for r in do_modelo]
        resumo[modelo] = {
            "n_runs": len(do_modelo),
            "status": dict(Counter(r.status for r in do_modelo).most_common()),
            "duracao_s_mediana": round(statistics.median(duracoes_s), 1),
            "duracao_s_media": round(statistics.fmean(duracoes_s), 1),
            "duracao_s_min": round(min(duracoes_s), 1),
            "duracao_s_max": round(max(duracoes_s), 1),
            "llm_calls_total": chamadas,
            "llm_calls_por_run_mediana": statistics.median(
                len(r.latencias_llm_ms) for r in do_modelo
            ),
            "latencia_llm_ms_mediana": round(statistics.median(latencias), 1)
            if latencias
            else None,
            "parse_erros": erros,
            "parse_erro_taxa": round(erros / chamadas, 4) if chamadas else None,
            "runs_com_parse_erro": sum(1 for r in do_modelo if r.parse_erros),
            "tool_calls_por_run_mediana": statistics.median(
                r.n_tool_calls for r in do_modelo
            ),
            "prompt_tokens_por_run_mediana": statistics.median(
                r.prompt_tokens for r in do_modelo
            ),
        }
    return resumo


def _extrapolar(por_modelo: dict[str, Any]) -> dict[str, Any]:
    """Horas por bateria de `METRICAS §9.2`, com a mediana de cada modelo.

    Numa bateria de dois modelos as células se dividem ao meio; numa de um modelo só, o
    plano nomeia qual (a de mutantes roda no 8B, que é o barato). Sem essa distinção a conta
    fica pendurada na mediana errada.
    """
    modelos = sorted(por_modelo)
    medianas = {m: por_modelo[m]["duracao_s_mediana"] for m in modelos}
    mais_barato = min(medianas, key=lambda m: medianas[m])

    detalhe: dict[str, Any] = {}
    total_s = 0.0
    for nome, (matriz, execucoes, n_modelos) in EXECUCOES_POR_BATERIA.items():
        if n_modelos == 1:
            segundos = execucoes * medianas[mais_barato]
        else:
            por_modelo_exec = execucoes / len(modelos)
            segundos = sum(por_modelo_exec * medianas[m] for m in modelos)
        total_s += segundos
        detalhe[nome] = {
            "matriz": matriz,
            "execucoes": execucoes,
            "horas": round(segundos / 3600, 1),
        }

    return {
        "por_bateria": detalhe,
        "execucoes_totais": sum(v[1] for v in EXECUCOES_POR_BATERIA.values()),
        "horas_totais": round(total_s / 3600, 1),
        "alvo_horas": DURACAO_ALVO_H,
        "cabe_no_alvo": total_s / 3600 <= DURACAO_ALVO_H,
        "modelo_da_bateria_de_um_modelo_so": mais_barato,
    }


def _por_cenario(runs: list[Run]) -> dict[str, Any]:
    return {
        cenario: {
            "n_runs": len([r for r in runs if r.cenario == cenario]),
            "status": dict(
                Counter(r.status for r in runs if r.cenario == cenario).most_common()
            ),
            "duracao_s_mediana": round(
                statistics.median(
                    r.duracao_ms / 1000 for r in runs if r.cenario == cenario
                ),
                1,
            ),
            "iteracoes_mediana": statistics.median(
                r.iteracoes for r in runs if r.cenario == cenario
            ),
        }
        for cenario in sorted({r.cenario for r in runs})
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("diretorio", type=Path)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)

    runs = carregar(args.diretorio)
    por_modelo = _por_modelo(runs)
    relatorio = {
        "diretorio": str(args.diretorio),
        "n_runs": len(runs),
        "status_geral": dict(Counter(r.status for r in runs).most_common()),
        "por_modelo": por_modelo,
        "por_cenario": _por_cenario(runs),
        "extrapolacao": _extrapolar(por_modelo),
    }

    print(f"{len(runs)} runs · {relatorio['status_geral']}\n")
    for modelo, dados in por_modelo.items():
        print(f"{modelo}")
        print(
            f"  duração/run  mediana {dados['duracao_s_mediana']:7.1f}s  "
            f"(média {dados['duracao_s_media']:.1f} · "
            f"{dados['duracao_s_min']:.1f}–{dados['duracao_s_max']:.1f})"
        )
        print(
            f"  llm_call     {dados['llm_calls_total']} chamadas · "
            f"mediana {dados['latencia_llm_ms_mediana']} ms · "
            f"{dados['llm_calls_por_run_mediana']}/run"
        )
        print(
            f"  parse_erro   {dados['parse_erros']}/{dados['llm_calls_total']} = "
            f"{(dados['parse_erro_taxa'] or 0) * 100:.1f}%  "
            f"({dados['runs_com_parse_erro']} runs afetadas)"
        )
        print(f"  status       {dados['status']}")

    extra = relatorio["extrapolacao"]
    print(f"\nextrapolação para {extra['execucoes_totais']} execuções (METRICAS §9.2):")
    for nome, dados in extra["por_bateria"].items():
        print(f"  {nome:<14} {dados['execucoes']:>4} exec  {dados['horas']:>6.1f} h")
    veredito = "CABE" if extra["cabe_no_alvo"] else "NÃO CABE"
    print(f"  {'TOTAL':<14} {extra['execucoes_totais']:>4} exec  {extra['horas_totais']:>6.1f} h"
          f"   → {veredito} nas {extra['alvo_horas']:.0f} h de duas madrugadas")

    if args.json:
        args.json.write_text(json.dumps(relatorio, indent=2, ensure_ascii=False), "utf-8")
        print(f"\njson → {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
