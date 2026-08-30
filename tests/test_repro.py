"""Reprodutibilidade ponta a ponta — pontuar o mesmo trace duas vezes dá o mesmo score.

O QUE ESTE ARQUIVO PROVA
    `ARQUITETURA §5`, decisão 1, é a promessa central do desenho: **trace imutável, scores
    derivados**. Ela só vale se a derivação for uma função — mesmo trace, mesmo score, hoje e
    na banca. Nenhum outro teste da suíte a exercita ponta a ponta: `test_n1.py` e
    `test_n2.py` pontuam traces sintéticos montados na memória, e um trace sintético não tem
    as formas que o SUT real produz (`unavailable` repetido, `budget_exceeded` no meio da
    iteração 8, `tool_result` fora de ordem por causa dos dois emissores).

    O insumo aqui são os **24 traces reais** de `runs/piloto_2026-08-24c/` — 6 cenários de dev
    × 2 modelos × 2 `sample_seed`, a segunda passada da piloto (A17). Eles são o estado atual
    do SUT versionado no repositório.

POR QUE O N3 FICA DE FORA, DELIBERADAMENTE
    N3 é julgamento por LLM. Mesmo com temperatura 0 o endpoint pode variar — amostragem,
    versão do modelo servido, empate numérico. Um teste que exigisse N3 estável estaria
    medindo a sorte do endpoint e reprovaria a suíte por algo que não é defeito do
    instrumento. **A instabilidade do N3 não é ignorada: ela é o objeto de medição do *flip
    rate*** (`METRICAS §7`, INS.7 — o judge 5× sobre os mesmos itens), que roda como
    experimento e não como asserção de CI.

    Quem for "consertar" este arquivo acrescentando N3: não é conserto, é trocar uma
    propriedade que vale por uma que não vale. O mesmo aviso está em `docs/REPRODUZIR.md`.

POR QUE NÃO PODE FALAR COM A REDE
    N1 e N2 são funções puras de `(trace, gabarito)` — o gabarito vem de YAML no disco e o
    trace de JSONL no disco. Se algum caminho de pontuação abrisse socket, "recomputável" já
    não seria verdade: o score passaria a depender de um servidor no ar naquele instante.
    `test_pontuar_nao_abre_socket` transforma isso em falha barulhenta em vez de suposição.

DUAS PASSADAS, E POR QUE UMA DELAS É EM OUTRO PROCESSO
    Repetir a chamada no mesmo processo não pega a classe de não-determinismo mais comum em
    Python: ordem de iteração de `set`, que muda com a semente de hash e é fixada só na
    partida do interpretador. `test_replay_independe_do_hash_seed` roda as duas passadas em
    subprocessos com `PYTHONHASHSEED` diferente — é o que faz `tools_extras` ou
    `precedencias_violadas` numa ordem instável virar falha aqui, e não número diferente na
    figura da banca.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tapieval.scoring.bateria import pontuar_bateria
from tapieval.scoring.gabarito import _regras_do_contrato

RAIZ = Path(__file__).resolve().parents[1]

DIRETORIO_DA_PILOTO = RAIZ / "runs" / "piloto_2026-08-24c"
"""A segunda passada da piloto (A17) — o único conjunto de traces reais do SUT atual.

`piloto_2026-08-23` fica no repositório como o "antes" (18 de 24 runs não terminavam) e
`piloto_2026-08-24`/`-24b` são passadas intermediárias. Fixar o diretório aqui, em vez de
varrer `runs/*`, é o que impede este teste de mudar de significado quando a bateria oficial
gravar uma pasta nova ao lado.
"""

CALCULADO_EM_FIXO = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
"""Fixo porque `ScoreRecord.calculado_em` é relógio, não derivação do trace."""

TRACES_ESPERADOS = 24
"""6 cenários de dev × 2 modelos × 2 `sample_seed`. Declarado como número para que um
conjunto que encolheu reprove aqui: um teste de determinismo sobre zero trace passa sempre."""


# ---------------------------------------------------------------------------
# O replay
# ---------------------------------------------------------------------------


def pontuar_replay(diretorio: Path = DIRETORIO_DA_PILOTO) -> dict[str, dict[str, Any]]:
    """Pontua N1 e N2 de todas as runs de uma bateria já gravada, do zero.

    O laço mora em `scoring/bateria.py` desde 30/08 — este arquivo o consumiu por dez dias e
    ele era, até então, o ÚNICO caminho para pontuar uma bateria inteira. Um laço de teste
    não pode ser a implementação de produção: pontuar as 288 células da bateria principal
    exigiria copiá-lo para um script na noite da execução, e a cópia divergiria da que este
    teste prova ser determinística.

    "Do zero" é literal: o contrato de regras é relido do disco a cada chamada
    (`_regras_do_contrato` é `@cache`), porque um cache quente esconderia justamente o que
    este módulo quer medir — se a segunda passada depende de estado deixado pela primeira.

    `calculado_em` entra fixo. É a hora do relógio, muda entre as duas passadas por
    construção, e não é derivada do trace: deixá-la variar faria toda comparação campo a
    campo acusar diferença onde não há uma.
    """
    _regras_do_contrato.cache_clear()

    pontuacao = pontuar_bateria(diretorio, calculado_em=CALCULADO_EM_FIXO)
    assert not pontuacao.nao_pontuadas, (
        # `pontuar_bateria` engole a exceção de UMA run de propósito, para que a bateria da
        # madrugada não morra na primeira run ruim. Aqui isso viraria um replay silenciosamente
        # menor, e um teste de determinismo sobre 23 traces em vez de 24 passa sem dizer nada.
        "runs não pontuadas no replay: "
        + "; ".join(f"{r.run_id}: {r.motivo}" for r in pontuacao.nao_pontuadas)
    )

    return {
        score.run_id: {
            "n1": score.n1.model_dump(mode="json"),
            "n2": score.n2.model_dump(mode="json"),
        }
        for score in pontuacao.scores
    }


def divergencias(
    primeira: dict[str, dict[str, Any]], segunda: dict[str, dict[str, Any]]
) -> list[str]:
    """As diferenças entre duas passadas, uma linha por campo, com os dois valores.

    Devolve frases e não um booleano porque `assert a == b` sobre 24 dicionários aninhados
    imprime as duas árvores inteiras e não diz qual campo mudou — que é a única informação
    útil quando isto falhar às duas da manhã antes da banca.
    """
    achados: list[str] = []

    faltando_na_segunda = sorted(set(primeira) - set(segunda))
    faltando_na_primeira = sorted(set(segunda) - set(primeira))
    achados += [f"{run_id}: pontuada só na 1ª passada" for run_id in faltando_na_segunda]
    achados += [f"{run_id}: pontuada só na 2ª passada" for run_id in faltando_na_primeira]

    for run_id in sorted(set(primeira) & set(segunda)):
        for camada in ("n1", "n2"):
            antes, depois = primeira[run_id][camada], segunda[run_id][camada]
            for campo in sorted(set(antes) | set(depois)):
                if antes.get(campo) != depois.get(campo):
                    achados.append(
                        f"{run_id} · {camada}.{campo}: "
                        f"1ª={antes.get(campo)!r} 2ª={depois.get(campo)!r}"
                    )
    return achados


# ---------------------------------------------------------------------------
# O conjunto de replay existe e é o que se pensa que é
# ---------------------------------------------------------------------------


def test_o_conjunto_de_replay_esta_versionado():
    """Sem estes arquivos os testes abaixo passariam sobre o vazio, sem dizer nada."""
    assert DIRETORIO_DA_PILOTO.is_dir(), f"{DIRETORIO_DA_PILOTO} não existe"
    assert (DIRETORIO_DA_PILOTO / "manifest.json").is_file()

    traces = sorted((DIRETORIO_DA_PILOTO / "traces").glob("*.jsonl"))
    assert len(traces) == TRACES_ESPERADOS, (
        f"esperados {TRACES_ESPERADOS} traces em {DIRETORIO_DA_PILOTO / 'traces'}, "
        f"achados {len(traces)}"
    )


def test_replay_pontua_todas_as_runs_do_manifesto():
    """Célula declarada e não pontuada é bateria pela metade lida como bateria inteira."""
    pontuadas = pontuar_replay()
    assert len(pontuadas) == TRACES_ESPERADOS, (
        f"{TRACES_ESPERADOS} células no manifesto, {len(pontuadas)} pontuadas: "
        f"{sorted(pontuadas)}"
    )


# ---------------------------------------------------------------------------
# A propriedade
# ---------------------------------------------------------------------------


def test_replay_e_deterministico_no_mesmo_processo():
    """Mesmo trace, mesmo gabarito, mesmo score — as duas passadas em sequência.

    N3 fora, de propósito: ver o cabeçalho deste módulo.
    """
    achados = divergencias(pontuar_replay(), pontuar_replay())
    assert not achados, "replay não determinístico:\n  " + "\n  ".join(achados)


@pytest.mark.lento
def test_replay_independe_do_hash_seed():
    """A mesma propriedade com o interpretador reiniciado e outra semente de hash.

    Pega o não-determinismo que a repetição no mesmo processo não pega: um campo de lista
    montado a partir de `set` sai numa ordem que depende da semente, e ela é sorteada na
    partida do interpretador. Dois processos com sementes fixas e diferentes tornam essa
    ordem observável — e reprodutível quando falhar.
    """
    primeira = _pontuar_em_subprocesso(hash_seed="1")
    segunda = _pontuar_em_subprocesso(hash_seed="99991")

    achados = divergencias(primeira, segunda)
    assert not achados, (
        "replay depende da semente de hash (PYTHONHASHSEED=1 × 99991):\n  "
        + "\n  ".join(achados)
    )


def _pontuar_em_subprocesso(hash_seed: str) -> dict[str, dict[str, Any]]:
    """Roda `pontuar_replay` num interpretador novo e devolve o resultado como JSON.

    `sys.executable` e não `python`: o subprocesso tem de ser o MESMO interpretador da suíte,
    senão um `python` de fora do venv acharia outra versão do pydantic e a diferença medida
    seria a das dependências, não a da semente.
    """
    programa = (
        "import json, sys;"
        f" sys.path.insert(0, {str(RAIZ / 'src')!r});"
        f" sys.path.insert(0, {str(RAIZ / 'tests')!r});"
        " from test_repro import pontuar_replay;"
        " print(json.dumps(pontuar_replay(), sort_keys=True))"
    )
    concluido = subprocess.run(
        [sys.executable, "-c", programa],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONHASHSEED": hash_seed},
        cwd=RAIZ,
        check=False,
    )
    assert concluido.returncode == 0, (
        f"replay falhou com PYTHONHASHSEED={hash_seed}:\n{concluido.stderr}"
    )
    return json.loads(concluido.stdout)


# ---------------------------------------------------------------------------
# A pontuação não fala com a rede
# ---------------------------------------------------------------------------


def test_pontuar_nao_abre_socket(monkeypatch):
    """Nenhum caminho de N1/N2 pode depender de servidor no ar.

    O bloqueio é no construtor de `socket.socket`, e não num mock de `httpx`: qualquer
    cliente HTTP acaba ali embaixo, e bloquear só a biblioteca que se conhece hoje deixaria
    passar a que alguém importar amanhã. Se este teste falhar, o achado não é o teste — é um
    scorer que deixou de ser recomputável do trace (`ARQUITETURA §5`, decisão 1).

    Ressalva declarada: um caminho que importe `ssl` (ou `httpx`) pela PRIMEIRA vez já dentro
    do bloqueio quebra no `import`, com `TypeError`, e não com a mensagem abaixo — `ssl` herda
    de `socket.socket`. O veredito é o mesmo (a pontuação tocou a rede), só a mensagem é pior.
    """

    def recusar(*args, **kwargs):
        raise AssertionError(
            "a pontuação N1/N2 tentou abrir socket: o score deixou de ser função pura do "
            "trace e passou a depender de um servidor no ar"
        )

    monkeypatch.setattr(socket, "socket", recusar)
    monkeypatch.setattr(socket, "create_connection", recusar)

    assert len(pontuar_replay()) == TRACES_ESPERADOS
