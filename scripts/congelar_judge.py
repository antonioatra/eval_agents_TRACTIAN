#!/usr/bin/env python3
"""T23 — produz o `configs/judge_frozen.json`. A outra metade do que a R4 validou.

O QUE ESTE SCRIPT É, E O QUE ELE NÃO É
    A R4 (`2c401ba`) fixou o CONTRATO do arquivo e ensinou o carregador de bateria a conferir
    o sha256 a cada leitura. O arquivo em si não existia, porque produzi-lo dependia de uma
    decisão de curadoria — qual rubrica vale — e essa decisão era do A26. O A26 fechou em
    29/08 pela **v2**, e este script é o ato mecânico que sobra.

    Ele não decide nada. Lê o que a rubrica adotada tem, assina, e grava.

POR QUE OS DOIS TEMPLATES ENTRAM, E NÃO SÓ O CEGO
    `METRICAS §4` define DUAS configurações do judge — cego e com trace —, e as duas produzem
    N3 na bateria. Congelar só uma deixaria a outra livre: editar `judge_trace_v2.md` depois
    do congelamento não mudaria o sha, e todo `ScoreRecord` continuaria carregando um hash que
    afirma um judge que já não é o que rodou. É a mesma falha que o congelamento existe para
    impedir, entrando pela porta que ficou aberta.

    Os dois vão para `prompt` numa serialização canônica que nomeia cada configuração — ordem
    fixa, delimitador fixo —, para que o sha dependa do conteúdo e não de como o script foi
    escrito.

POR QUE `rubrica` NÃO É O PROMPT INTEIRO
    `rubrica_sha` existe, diz o `judge_congelado`, "para que a rubrica possa ser rastreada
    entre versões do judge que só mexeram no prompt ou nos few-shots". Isso só tem uso se a
    rubrica for uma PARTE do prompt: se os dois campos carregassem o mesmo texto, os dois shas
    mudariam sempre juntos e o rastreio não distinguiria nada.

    A parte é a seção `## As perguntas` de cada template — onde moram os campos fechados e o
    critério de cada um. É o recorte que a T21 de fato reescreveu: a v2 mexeu em
    `causa_raiz_correta` e `mencionou_limitacao_relevante`, e em nada do enquadramento em
    volta. O recorte é por cabeçalho e é conferido: se algum template deixar de ter a seção, o
    script morre em vez de assinar um recorte vazio.

RECONGELAR É ATO DELIBERADO
    Existindo o arquivo, o script recusa sobrescrever sem `--forcar`. Um congelamento
    silenciosamente regravado é indistinguível do original para quem lê o repositório, e a
    `git tag judge-v2-frozen` passaria a apontar para um estado que já não vale.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from tapieval.runner.judge_congelado import (  # noqa: E402
    carregar_judge_congelado,
    sha_da_rubrica,
    sha_do_judge,
)
from tapieval.scoring.judge_llm import config_do_judge  # noqa: E402
from tapieval.scoring.n3 import (  # noqa: E402
    DIRETORIO_DE_PROMPTS,
    RUBRICA_PADRAO,
    TEMPLATE_POR_CONFIGURACAO,
    carregar_fewshots,
)

CAMINHO_PADRAO = RAIZ / "configs" / "judge_frozen.json"

CONFIGURACOES: tuple[str, ...] = ("cego", "com_trace")
"""Ordem fixa. É parte da serialização assinada — trocar a ordem mudaria o sha sem que uma
palavra do judge tivesse mudado."""

ABERTURA_DA_RUBRICA = "## As perguntas"
FIM_DA_RUBRICA = "## Exemplos"


def _texto_dos_templates(rubrica: str) -> dict[str, str]:
    """Os dois templates da rubrica adotada, lidos do disco, byte a byte."""
    arquivos = TEMPLATE_POR_CONFIGURACAO[rubrica]
    return {
        configuracao: (DIRETORIO_DE_PROMPTS / arquivos[configuracao]).read_text(
            encoding="utf-8"
        )
        for configuracao in CONFIGURACOES
    }


def serializar(partes: dict[str, str]) -> str:
    """A serialização canônica de um texto por configuração.

    Delimitador explícito com o nome da configuração: dois templates concatenados sem marca
    teriam o mesmo sha de um único template que contivesse os dois, e a fronteira entre eles
    é justamente o que distingue as duas metades do instrumento.
    """
    return "\n".join(
        f"<<<{configuracao}>>>\n{partes[configuracao]}" for configuracao in CONFIGURACOES
    )


def recortar_rubrica(texto: str, configuracao: str) -> str:
    """A seção `## As perguntas` de um template, sem o enquadramento em volta.

    O recorte é conferido dos dois lados. Um template sem a seção, ou com ela vazia, faria o
    `rubrica_sha` assinar o nada — e um sha do nada confere sempre, contra qualquer rubrica.
    """
    inicio = texto.find(ABERTURA_DA_RUBRICA)
    if inicio < 0:
        raise SystemExit(
            f"o template {configuracao!r} não tem a seção {ABERTURA_DA_RUBRICA!r}. O recorte "
            "da rubrica é por cabeçalho; sem ele o `rubrica_sha` assinaria texto vazio"
        )
    fim = texto.find(FIM_DA_RUBRICA, inicio)
    if fim < 0:
        raise SystemExit(
            f"o template {configuracao!r} tem {ABERTURA_DA_RUBRICA!r} mas não "
            f"{FIM_DA_RUBRICA!r} depois dela — não dá para saber onde a rubrica termina"
        )
    recorte = texto[inicio:fim].strip()
    if not recorte:
        raise SystemExit(f"a rubrica do template {configuracao!r} saiu vazia")
    return recorte


def montar(rubrica: str, *, quando: datetime) -> dict[str, Any]:
    """O documento do congelamento, com o sha já coerente com o conteúdo."""
    templates = _texto_dos_templates(rubrica)
    documento: dict[str, Any] = {
        "scorer_version": rubrica,
        "prompt": serializar(templates),
        "rubrica": serializar(
            {c: recortar_rubrica(templates[c], c) for c in CONFIGURACOES}
        ),
        "fewshots": carregar_fewshots(),
        "judge_model": config_do_judge().model_dump(mode="json"),
        "congelado_em": quando.isoformat(),
        "fewshot_origem": "escritos_a_mao",
        "notas": (
            "Congelado pela T23 depois do A26 (29/08/2026), que adotou a rubrica v2. "
            "`prompt` e `rubrica` carregam as DUAS configurações de METRICAS §4 (cego e "
            "com_trace) na ordem fixa acima — congelar só uma deixaria a outra livre para "
            "mudar sem alterar o sha. `rubrica` é a seção `## As perguntas` de cada template; "
            "`prompt` é o template inteiro."
        ),
    }
    documento["rubrica_sha"] = sha_da_rubrica(documento["rubrica"])
    documento["sha256"] = sha_do_judge(documento)
    return documento


def main() -> int:
    parser = argparse.ArgumentParser(description="Congela o judge da T23")
    parser.add_argument(
        "--rubrica", default=RUBRICA_PADRAO, choices=sorted(TEMPLATE_POR_CONFIGURACAO)
    )
    parser.add_argument("--caminho", type=Path, default=CAMINHO_PADRAO)
    parser.add_argument(
        "--forcar",
        action="store_true",
        help="regrava um congelamento que já existe (a tag do git passa a mentir — ver docstring)",
    )
    args = parser.parse_args()

    if args.caminho.exists() and not args.forcar:
        print(
            f"{args.caminho} já existe. Recongelar é ato deliberado: um congelamento regravado\n"
            "é indistinguível do original para quem lê o repositório, e a tag `judge-v2-frozen`\n"
            "passaria a apontar para um estado que já não vale. Use --forcar se é isso mesmo."
        )
        return 2

    documento = montar(args.rubrica, quando=datetime.now(UTC))
    args.caminho.parent.mkdir(parents=True, exist_ok=True)
    args.caminho.write_text(
        json.dumps(documento, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # Reler pelo caminho de produção é o que prova que o arquivo serve: `sha_do_judge` e
    # `carregar_judge_congelado` são funções diferentes, e é a segunda que a bateria chama.
    lido = carregar_judge_congelado(args.caminho)
    print(f"congelado em {args.caminho}")
    print(f"  scorer_version : {lido.scorer_version}")
    print(f"  sha256         : {lido.sha256}")
    print(f"  rubrica_sha    : {lido.rubrica_sha}")
    print(f"  judge_model    : {lido.judge_model.model_id} · {lido.judge_model.served_by}")
    print(f"  few-shots      : {', '.join(lido.fewshot_ids)}")
    print(f"  congelado_em   : {lido.congelado_em.isoformat()}")
    print("\nA partir daqui, NENHUM commit pode alterar os templates da rubrica nem")
    print("prompts/fewshot/ — o sha deixaria de conferir e toda bateria morreria no carregamento.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
