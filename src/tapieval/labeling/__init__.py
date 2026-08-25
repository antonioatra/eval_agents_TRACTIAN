"""Rotulagem humana cega — a camada N4 de `METRICAS §5`, o gold de todo o experimento.

Duas propriedades que este pacote existe para tornar **estruturais**, e não disciplinares:

1. **O rotulador não tem como ver a saída do judge.** Não há caminho de código que abra
   `runs/<id>/scores/`. A independência do κ (INS.6) deixa de depender de a pessoa se
   lembrar da regra — e uma âncora, se acontecesse, não deixaria rastro nenhum no número.
2. **A amostra de estimativa e a de melhoria são disjuntas por construção.** A de
   estimativa é aleatória estratificada e é a única que pode entrar no κ; a de melhoria é
   escolhida por dificuldade de propósito e fica de fora dele.

`amostra.py` é puro (o sorteio e a prioridade); `cli.py` faz o I/O e a sessão.
"""

from __future__ import annotations

from tapieval.labeling.amostra import (
    N_ESTIMATIVA,
    N_MELHORIA,
    SEED_DA_AMOSTRAGEM,
    AmostraInsuficiente,
    Candidato,
    ItemDaAmostra,
    SinaisDeIncerteza,
    TipoDeAmostra,
    amostrar,
    candidato_de_trace,
    prioridade_revisao_humana,
    sinais_de_incerteza,
)
from tapieval.labeling.cli import (
    RotuloHumano,
    apresentar_caso,
    carregar_candidatos,
    rodar_sessao,
    run_ids_ja_rotulados,
)

__all__ = [
    "N_ESTIMATIVA",
    "N_MELHORIA",
    "SEED_DA_AMOSTRAGEM",
    "AmostraInsuficiente",
    "Candidato",
    "ItemDaAmostra",
    "RotuloHumano",
    "SinaisDeIncerteza",
    "TipoDeAmostra",
    "amostrar",
    "apresentar_caso",
    "candidato_de_trace",
    "carregar_candidatos",
    "prioridade_revisao_humana",
    "rodar_sessao",
    "run_ids_ja_rotulados",
    "sinais_de_incerteza",
]
