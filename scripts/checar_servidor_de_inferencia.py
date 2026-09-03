#!/usr/bin/env python3
"""Checa se o servidor de inferência local serve o que a bateria exige — antes da T0b.

POR QUE ISTO EXISTE
    A T0b mede o modelo. Este script mede o SERVIDOR, e roda antes: se o endpoint não honra
    `response_format: json_schema` ou ignora `seed`, a T0b mediria a ferramenta de servir em vez do
    modelo, e o número iria para `docs/anexos/apuracao/tool_calling_baseline.md` como se fosse do
    Qwen.

AS TRÊS EXIGÊNCIAS, E O QUE CADA UMA QUEBRA SE FALTAR
    1. `/v1/models` responde e lista os dois modelos do par (A1: Qwen3-8B + Qwen3-14B).
       Faltando um, não existe o eixo de H2 — que separa acerto de função de acerto de
       argumento COMPARANDO os dois tamanhos.
    2. `response_format={"type":"json_schema"}` é honrado. Sem gramática no decodificador, o
       `parse_erro` deixa de ser falha de schema e vira falha de formatação — e `parse_erro` é
       o principal confound entre modelos (`ARQUITETURA §5`, decisão 4). O teste não pergunta
       ao servidor se ele suporta: manda um schema com campo obrigatório e confere a resposta.
    3. `seed` é honrado: duas chamadas com a mesma seed e temperatura > 0 devolvem o MESMO
       texto. Sem isso, `sample_seed` não controla nada, `ModelConfig.seed` tem de ir a `None`
       no manifesto, e o `pass^k` deixa de ser reproduzível run a run (H4).

    A 3 é a única que pode falhar sem ser bloqueante: dá para rodar a bateria registrando
    `seed=None` e declarando a limitação. As duas primeiras são bloqueantes.

USO
    python scripts/checar_servidor_de_inferencia.py
    python scripts/checar_servidor_de_inferencia.py --base-url http://127.0.0.1:11434/v1

Sai com código 1 se alguma exigência bloqueante falhar.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass

import httpx

BASE_URL_PADRAO = "http://127.0.0.1:1234/v1"
"""LM Studio. Ollama serve em `:11434/v1`, `mlx_lm.server` em `:8080/v1`."""

TIMEOUT_S = 300.0
"""O mesmo do cliente de inferência: 14B em q4 com schema na janela leva dezenas de segundos."""

ESQUEMA_DE_PROVA = {
    "type": "object",
    "properties": {
        "cidade": {"type": "string"},
        "habitantes": {"type": "integer"},
    },
    "required": ["cidade", "habitantes"],
    "additionalProperties": False,
}

PERGUNTA = "Responda com a capital do Brasil e uma estimativa de habitantes."


@dataclass
class Resultado:
    nome: str
    ok: bool
    bloqueante: bool
    detalhe: str

    def linha(self) -> str:
        marca = "OK  " if self.ok else ("FALHA" if self.bloqueante else "AVISO")
        return f"[{marca}] {self.nome}: {self.detalhe}"


def _chamar(
    cliente: httpx.Client,
    modelo: str,
    *,
    com_esquema: bool,
    seed: int | None,
    temperatura: float,
) -> tuple[str, float]:
    corpo: dict = {
        "model": modelo,
        "messages": [{"role": "user", "content": PERGUNTA}],
        "temperature": temperatura,
        "max_tokens": 200,
    }
    if com_esquema:
        corpo["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "prova", "strict": True, "schema": ESQUEMA_DE_PROVA},
        }
    if seed is not None:
        corpo["seed"] = seed

    inicio = time.perf_counter()
    resposta = cliente.post("/chat/completions", json=corpo)
    decorrido = time.perf_counter() - inicio
    resposta.raise_for_status()
    return resposta.json()["choices"][0]["message"]["content"], decorrido


def checar_modelos(cliente: httpx.Client) -> tuple[Resultado, list[str]]:
    try:
        resposta = cliente.get("/models")
        resposta.raise_for_status()
    except httpx.HTTPError as erro:
        return (
            Resultado(
                "servidor de pé",
                False,
                True,
                f"{type(erro).__name__}: {erro}. Suba o LM Studio (ou Ollama) e ligue "
                "o servidor local.",
            ),
            [],
        )

    ids = sorted(item["id"] for item in resposta.json().get("data", []))
    if not ids:
        return Resultado("servidor de pé", False, True, "responde, mas não serve modelo nenhum"), []
    return Resultado("servidor de pé", True, True, f"{len(ids)} modelo(s): {', '.join(ids)}"), ids


def checar_par_do_a1(ids: list[str]) -> Resultado:
    achados = {tamanho: [i for i in ids if tamanho in i.lower()] for tamanho in ("8b", "14b")}
    faltando = [tamanho for tamanho, encontrados in achados.items() if not encontrados]
    if faltando:
        return Resultado(
            "par 8B + 14B (A1)",
            False,
            True,
            f"não achei modelo com {' nem '.join(faltando)} no id. H2 compara os dois tamanhos.",
        )
    return Resultado(
        "par 8B + 14B (A1)",
        True,
        True,
        f"{achados['8b'][0]} + {achados['14b'][0]}",
    )


def checar_json_schema(cliente: httpx.Client, modelo: str) -> Resultado:
    nome = f"json_schema honrado ({modelo})"
    try:
        texto, decorrido = _chamar(
            cliente, modelo, com_esquema=True, seed=None, temperatura=0.0
        )
    except httpx.HTTPStatusError as erro:
        return Resultado(
            nome, False, True, f"HTTP {erro.response.status_code}: {erro.response.text[:200]}"
        )
    except httpx.HTTPError as erro:
        return Resultado(nome, False, True, f"{type(erro).__name__}: {erro}")

    try:
        dados = json.loads(texto)
    except json.JSONDecodeError:
        return Resultado(
            nome,
            False,
            True,
            f"a resposta não é JSON — o servidor ignorou o schema: {texto[:120]!r}",
        )

    faltando = [campo for campo in ESQUEMA_DE_PROVA["required"] if campo not in dados]
    if faltando:
        return Resultado(nome, False, True, f"JSON sem os campos obrigatórios {faltando}")
    if not isinstance(dados.get("habitantes"), int):
        return Resultado(
            nome, False, True, "`habitantes` não veio inteiro: o tipo do schema não foi imposto"
        )
    return Resultado(nome, True, True, f"schema imposto, {decorrido:.1f}s na primeira chamada")


def checar_seed(cliente: httpx.Client, modelo: str) -> Resultado:
    nome = f"seed honrada ({modelo})"
    try:
        primeira, _ = _chamar(cliente, modelo, com_esquema=False, seed=42, temperatura=0.8)
        segunda, _ = _chamar(cliente, modelo, com_esquema=False, seed=42, temperatura=0.8)
    except httpx.HTTPError as erro:
        return Resultado(nome, False, False, f"{type(erro).__name__}: {erro}")

    if primeira != segunda:
        return Resultado(
            nome,
            False,
            False,
            "mesma seed devolveu textos diferentes — `ModelConfig.seed` vai a None e a "
            "irreprodutibilidade entra como limitação declarada",
        )
    return Resultado(nome, True, False, "duas chamadas com seed=42 e T=0.8 saíram idênticas")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=BASE_URL_PADRAO)
    parser.add_argument(
        "--modelo",
        action="append",
        default=None,
        help="id do modelo a testar (repetível). Padrão: os dois primeiros que o servidor lista.",
    )
    args = parser.parse_args()

    resultados: list[Resultado] = []
    with httpx.Client(base_url=args.base_url, timeout=TIMEOUT_S) as cliente:
        de_pe, ids = checar_modelos(cliente)
        resultados.append(de_pe)
        if not de_pe.ok:
            print("\n".join(r.linha() for r in resultados))
            return 1

        resultados.append(checar_par_do_a1(ids))
        alvos = args.modelo or ids[:2]
        for modelo in alvos:
            resultados.append(checar_json_schema(cliente, modelo))
            resultados.append(checar_seed(cliente, modelo))

    print("\n".join(r.linha() for r in resultados))
    bloqueios = [r for r in resultados if not r.ok and r.bloqueante]
    avisos = [r for r in resultados if not r.ok and not r.bloqueante]
    print(
        f"\n{len(resultados) - len(bloqueios) - len(avisos)}/{len(resultados)} ok · "
        f"{len(bloqueios)} bloqueante(s) · {len(avisos)} aviso(s)"
    )
    return 1 if bloqueios else 0


if __name__ == "__main__":
    sys.exit(main())
