"""
Trajetória de referência do cenário — a parte do YAML que a N2 consome e o `Cenario` não leva.

POR QUE UM MÓDULO SÓ PARA ISSO
    `scoring/gabarito.Cenario` foi desenhado para a N1.4 e guarda `tools_esperadas` como
    `frozenset`: para saber SE a tool foi chamada, ordem é ruído. A N2.2 mede exatamente a
    ordem (Kendall τ contra a trajetória de referência) e a N2.1 precisa de `precedencias`,
    que `Cenario` não carrega. As duas informações estão no YAML e nenhuma sobrevive à
    conversão — daí este carregador, que lê o mesmo arquivo e devolve o que falta.

POR QUE SEPARADO DE `n2.py`
    Este módulo faz I/O; `pontuar_n2` é função pura e a trajetória lhe é INJETADA. Manter a
    leitura de disco fora do scorer é o que permite testar a pureza dele estruturalmente
    (`tests/test_n2.py`), como `test_estado.py` faz com `derivar_estado`. Sem essa separação,
    "mesmo trace, mesmo score" passaria a depender do que está no diretório de cenários.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from tapieval.scoring.gabarito import DIRETORIO_DE_CENARIOS


@dataclass(frozen=True)
class Precedencia:
    """Um par ordenado `(antes, depois)` de `gabarito.precedencias` (`METRICAS §N2.1`).

    `antes` é sempre nome de tool no corpus atual; `depois` é, na maioria das vezes, um
    pseudo-evento em prosa (`atribuir:causa_da_ausencia_de_insight`). O carregador não filtra
    nada: quem decide o que é verificável é o scorer, e o filtro precisa ser auditável lá.
    """

    antes: str
    depois: str
    regra: str = ""


@dataclass(frozen=True)
class TrajetoriaDeReferencia:
    """Imutável: é entrada compartilhada entre runs do mesmo cenário."""

    cenario_id: str
    tools_esperadas: tuple[str, ...]
    """Na ORDEM do YAML. `test-scenarios.md` chama isso de 'referência, não script rígido' —
    por isso a N2.2 usa τ, que tolera caminho alternativo, e não distância de edição."""

    precedencias: tuple[Precedencia, ...] = ()
    caminho: Path | None = None


def carregar_trajetoria(caminho: Path) -> TrajetoriaDeReferencia:
    """Lê `gabarito.tools_esperadas` e `gabarito.precedencias` de um YAML de cenário."""
    documento: Mapping[str, Any] = yaml.safe_load(caminho.read_text(encoding="utf-8"))
    gabarito = documento.get("gabarito") or {}
    return TrajetoriaDeReferencia(
        cenario_id=documento["id"],
        tools_esperadas=tuple(gabarito.get("tools_esperadas") or ()),
        precedencias=tuple(
            Precedencia(
                antes=precedencia["antes"],
                depois=precedencia["depois"],
                regra=precedencia.get("regra", ""),
            )
            for precedencia in (gabarito.get("precedencias") or ())
        ),
        caminho=caminho,
    )


def carregar_trajetorias(
    diretorio: Path = DIRETORIO_DE_CENARIOS,
) -> dict[str, TrajetoriaDeReferencia]:
    """Todo o corpus, indexado por `id`. Arquivos `_*.yaml` são contrato, não cenário."""
    trajetorias = [
        carregar_trajetoria(caminho)
        for caminho in sorted(diretorio.glob("*.yaml"))
        if not caminho.name.startswith("_")
    ]
    return {trajetoria.cenario_id: trajetoria for trajetoria in trajetorias}
