"""O canário da T23 — o que ele pode e o que ele NÃO pode testemunhar (X29).

O canário existe porque o congelamento por sha256 alcança o prompt e o id do modelo, e o id
é um alias: o peso do outro lado pode trocar sem que nada no nosso registro mude. Ele roda uma
entrada fixa antes e depois de cada bateria e compara.

O que estes testes protegem é a única propriedade que faz um canário valer alguma coisa: **ele
só pode gritar por causa do modelo.** Um canário que também grita por causa da rubrica, da
retentativa ou do provedor é um alarme que ninguém lê na terceira vez — e aí ele não protege
bateria nenhuma. Cada teste aqui fecha uma fonte de grito que não é troca de modelo.

Nenhum deles fala com a rede: `classificar` e `comparar` são puras sobre as passadas já
colhidas, que é o motivo de elas terem sido separadas de `rodar`.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

RAIZ = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def canario():
    # `scripts/` no path porque o canário importa `checar_judge` (a entrada plantada é a
    # mesma do portão de viabilidade da T20 — duas cópias dela divergiriam em silêncio).
    sys.path.insert(0, str(RAIZ / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "canario_do_judge", RAIZ / "scripts" / "canario_do_judge.py"
    )
    modulo = importlib.util.module_from_spec(spec)
    sys.modules["canario_do_judge"] = modulo
    spec.loader.exec_module(modulo)
    return modulo


def passada(**campos: Any) -> dict[str, Any]:
    """Uma passada completa, com o veredito no valor de sempre e só o pedido variando."""
    base: dict[str, Any] = {
        "tokens_in": 5993,
        "chamadas_llm": 1,
        "causa_raiz_correta": False,
        "mencionou_limitacao_relevante": True,
        "responde_a_pergunta": "parcial",
        "contradiz_evidencia": False,
        "recomendou_acao_sem_base": True,
        "n_afirmacoes_sem_suporte": 3,
        "afirmacoes_sem_suporte": ["a", "b", "c"],
    }
    base.update(campos)
    return base


# ---------------------------------------------------------------------------
# 1 · A retentativa não pode virar alarme
# ---------------------------------------------------------------------------


def test_retentativa_tira_tokens_in_da_comparacao_em_vez_de_marcar_instavel(canario):
    """O falso alarme de 26/08, na forma mínima.

    `pontuar_n3` retenta reapresentando o prompt com a resposta anterior colada atrás, e o
    medidor SOMA as chamadas — então a passada que retentou reporta ~2× `tokens_in` sobre uma
    entrada byte a byte idêntica. Chamar isso de instável faz o canário testemunhar contra o
    modelo por causa da rubrica.
    """
    resultado = canario.classificar(
        [passada(), passada(), passada(tokens_in=12140, chamadas_llm=2)]
    )

    assert "tokens_in" in resultado["estaveis"]
    assert resultado["estaveis"]["tokens_in"] == 5993
    assert "tokens_in" not in resultado["instaveis"]
    assert resultado["chamadas_llm"] == [1, 1, 2]


def test_sem_dupla_limpa_tokens_in_fica_sem_testemunho(canario):
    """Menos de duas passadas sem retentativa não é estável NEM instável: é não medido.

    Com uma passada só não há o que comparar, e chamar de "estável" um valor visto uma vez
    daria à próxima rodada uma linha de base que ela não tem como honrar.
    """
    resultado = canario.classificar(
        [
            passada(),
            passada(tokens_in=12140, chamadas_llm=2),
            passada(tokens_in=12141, chamadas_llm=2),
        ]
    )

    assert "tokens_in" not in resultado["estaveis"]
    assert "tokens_in" not in resultado["instaveis"]
    assert "tokens_in" in resultado["sem_testemunho"]


def test_campo_sem_testemunho_nao_conta_como_divergencia(canario):
    """A ponte entre `classificar` e `comparar`, que é onde o falso alarme de fato saía.

    A linha de base tem `tokens_in` estável. A rodada de agora não conseguiu medi-lo. Isso não
    é divergência — e antes deste conserto virava uma, porque a comparação lia o campo ausente
    como `None` e reportava `5993 → None`.
    """
    base = {
        "served_by": "vertex",
        "model_id": "gemini-3.6-flash",
        "estaveis": {"tokens_in": 5993, "causa_raiz_correta": False},
        "instaveis": {},
    }
    agora = {
        "served_by": "vertex",
        "model_id": "gemini-3.6-flash",
        "estaveis": {"causa_raiz_correta": False},
        "instaveis": {},
        "sem_testemunho": {"tokens_in": "2 de 3 passadas retentaram"},
    }

    assert canario.comparar(base, agora) == []


# ---------------------------------------------------------------------------
# 2 · O que ele AINDA tem de pegar
# ---------------------------------------------------------------------------


def test_tokens_in_diferente_entre_passadas_limpas_continua_denunciando(canario):
    """O conserto não pode ter comprado o silêncio: duas passadas SEM retentativa que
    discordam do número de tokens sobre a mesma entrada continuam sendo instabilidade."""
    resultado = canario.classificar([passada(), passada(tokens_in=6100), passada()])

    assert "tokens_in" in resultado["instaveis"]
    assert "tokens_in" not in resultado["estaveis"]


def test_veredito_e_comparado_mesmo_na_passada_que_retentou(canario):
    """A retentativa devolve um julgamento VÁLIDO. O que a rubrica decidiu é comparável
    tenha havido correção ou não — só o `tokens_in` é que fala da entrada."""
    resultado = canario.classificar(
        [passada(), passada(), passada(causa_raiz_correta=True, tokens_in=12140, chamadas_llm=2)]
    )

    assert "causa_raiz_correta" in resultado["instaveis"]
    assert resultado["instaveis"]["causa_raiz_correta"] == [False, False, True]


def test_campo_do_veredito_que_muda_de_valor_e_divergencia(canario):
    """O caso que o canário existe para pegar."""
    base = {
        "served_by": "vertex",
        "model_id": "gemini-3.6-flash",
        "estaveis": {"causa_raiz_correta": False},
        "instaveis": {},
    }
    agora = {
        "served_by": "vertex",
        "model_id": "gemini-3.6-flash",
        "estaveis": {"causa_raiz_correta": True},
        "instaveis": {},
        "sem_testemunho": {},
    }

    divergencias = canario.comparar(base, agora)
    assert len(divergencias) == 1
    assert "causa_raiz_correta" in divergencias[0]


def test_troca_de_provedor_invalida_a_linha_de_base_inteira(canario):
    """Os absolutos de token diferem 6–8% entre provedores para o mesmo prompt
    (`migracao_vertex §5`). Comparar através da troca produziria divergência garantida e sem
    significado, então a comparação para e pede regravação."""
    base = {
        "served_by": "gemini_api",
        "model_id": "gemini-3.6-flash",
        "estaveis": {"tokens_in": 2601},
        "instaveis": {},
    }
    agora = {
        "served_by": "vertex",
        "model_id": "gemini-3.6-flash",
        "estaveis": {"tokens_in": 2803},
        "instaveis": {},
        "sem_testemunho": {},
    }

    divergencias = canario.comparar(base, agora)
    assert len(divergencias) == 1
    assert "provedor mudou" in divergencias[0]


def test_linha_de_base_antiga_sem_sem_testemunho_continua_comparavel(canario):
    """A linha de base gravada em 25/08 não tem a chave nova. Ler um canário antigo não pode
    explodir — senão o conserto obrigaria a regravar a referência, que é justamente a coisa
    que não se quer regravar sem motivo."""
    base = {
        "served_by": "vertex",
        "model_id": "gemini-3.6-flash",
        "estaveis": {"causa_raiz_correta": False},
        "instaveis": {},
    }
    agora = {
        "served_by": "vertex",
        "model_id": "gemini-3.6-flash",
        "estaveis": {"causa_raiz_correta": False},
        "instaveis": {},
    }

    assert canario.comparar(base, agora) == []
