"""A retomada de `scripts/julgar_o_gold.py`, depois que o gold passou a ter duas configurações.

O QUE ESTE ARQUIVO PRENDE
    Até 31/08 o script julgava só `cego` e a chave de retomada era o trace sozinho. As duas
    coisas casavam enquanto houvesse uma configuração só. Ao acrescentar `com_trace` — o
    segundo ponto da curva de H0 (`ARQUITETURA §12`) — a chave antiga passa a dar por feita a
    célula `com_trace` porque a `cego` do mesmo trace já está gravada: o script imprime "nada
    a fazer", **não faz nenhuma chamada**, e o segundo ponto da curva simplesmente não existe.

    Nada quebraria. O arquivo continuaria válido, o κ continuaria certo, e a única evidência
    do buraco seria uma figura com um ponto a menos que ninguém contou. **É o mesmo defeito
    que a T21 já consertou uma vez** em `calibrar_judge`, quando a chave não carregava o
    provedor e uma rodada nova dava por feito o que o AI Studio tinha julgado.

    Nenhuma chamada de rede: o que se testa é a chave, não o judge.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def julgar():
    spec = importlib.util.spec_from_file_location(
        "julgar_o_gold", RAIZ / "scripts" / "julgar_o_gold.py"
    )
    modulo = importlib.util.module_from_spec(spec)
    sys.modules["julgar_o_gold"] = modulo
    spec.loader.exec_module(modulo)
    return modulo


def _gravar(arquivo: Path, *registros: dict) -> None:
    arquivo.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in registros), encoding="utf-8"
    )


TRACE = "calibracao_2026-08-24/aut_01--qwen3-8b--base--envs001--n11.jsonl"


def test_a_configuracao_entra_na_chave_da_retomada(julgar, tmp_path):
    """Julgado às cegas não é julgado com trace — são dois pontos da curva, não um."""
    arquivo = tmp_path / "julgamentos.jsonl"
    _gravar(arquivo, {"trace": TRACE, "configuracao": "cego"})

    feitos = julgar.ja_julgados(arquivo)

    assert (TRACE, "cego") in feitos
    assert (TRACE, "com_trace") not in feitos, (
        "a retomada deu por feita a célula `com_trace` porque a `cego` existe — o segundo "
        "ponto da curva de H0 nunca seria julgado, e o script diria 'nada a fazer'"
    )


def test_linha_antiga_sem_configuracao_e_cego(julgar, tmp_path):
    """Até 31/08 este script gravava só `cego`; supor o contrário rejulgaria o gold inteiro."""
    arquivo = tmp_path / "julgamentos.jsonl"
    _gravar(arquivo, {"trace": TRACE})

    assert julgar.ja_julgados(arquivo) == {(TRACE, "cego")}


def test_as_duas_configuracoes_do_mesmo_trace_convivem(julgar, tmp_path):
    arquivo = tmp_path / "julgamentos.jsonl"
    _gravar(
        arquivo,
        {"trace": TRACE, "configuracao": "cego"},
        {"trace": TRACE, "configuracao": "com_trace"},
    )

    assert julgar.ja_julgados(arquivo) == {(TRACE, "cego"), (TRACE, "com_trace")}


def test_o_default_continua_sendo_o_cego(julgar):
    """O κ da INS.6 é cego × cego — o humano rotulou sem ver a evidência.

    Trocar o default mediria a diferença de INSUMO como se fosse discordância de rubrica, e
    o número sairia com o nome de κ.
    """
    args = julgar.construir_parser().parse_args(["--run-dir", "runs/calibracao_2026-08-24"])

    assert args.configuracao == "cego"
    assert julgar.CONFIGURACAO_PADRAO == "cego"
