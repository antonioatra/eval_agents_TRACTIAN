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


# ---------------------------------------------------------------------------
# 3 · Quem pode testemunhar é a INS.7 que decide, não a sorte de três repetições
# ---------------------------------------------------------------------------


FLIP_RATE_V1 = {
    "causa_raiz_correta": {"itens": 44, "flipados": 8, "flip_rate": 0.1818},
    "mencionou_limitacao_relevante": {"itens": 44, "flipados": 13, "flip_rate": 0.2954},
    "responde_a_pergunta": {"itens": 44, "flipados": 1, "flip_rate": 0.0227},
    "afirmacoes_sem_suporte": {"itens": 22, "flipados": 2, "flip_rate": 0.0909},
    "contradiz_evidencia": {"itens": 22, "flipados": 1, "flip_rate": 0.0454},
    "recomendou_acao_sem_base": {"itens": 22, "flipados": 0, "flip_rate": 0.0},
}
"""O que a T21 mediu de fato em 26/08, com os números arredondados."""


def test_campo_que_flipa_sozinho_nao_testemunha_mesmo_caindo_igual_nas_repeticoes(canario):
    """O falso alarme de 26/08 na sua forma final, e a razão desta leva.

    A linha de base de 25/08 viu `n_afirmacoes_sem_suporte = 3` três vezes seguidas e o
    promoveu a testemunha. A INS.7 mediu 9,1% de flip naquele campo — três repetições não
    separam ESTÁVEL de SORTUDO. Aqui as três repetições concordam de novo, e ele continua
    fora: quem decide é o número da INS.7, não a rodada.
    """
    resultado = canario.classificar([passada(), passada(), passada()], FLIP_RATE_V1)

    assert "n_afirmacoes_sem_suporte" not in resultado["estaveis"]
    assert "9.1%" in resultado["sem_testemunho"]["n_afirmacoes_sem_suporte"]


def test_so_o_campo_com_flip_zero_sobrevive_como_testemunha(canario):
    """O preço da decisão, explícito: o canário fica FRACO em vez de forte e sortudo.

    Da rubrica v1 sobra um campo de veredito — `recomendou_acao_sem_base`, 0/22 na INS.7 —
    mais o `tokens_in`. É pouco, e é o que se pode afirmar. Se este teste começar a passar com
    mais campos, é porque a rubrica ficou mais estável, e aí a mudança é bem-vinda e visível.
    """
    resultado = canario.classificar([passada(), passada(), passada()], FLIP_RATE_V1)

    assert set(resultado["estaveis"]) == {"tokens_in", "recomendou_acao_sem_base"}


def test_o_corte_da_testemunha_e_flip_zero_e_nao_os_10_por_cento_da_t21(canario):
    """São perguntas diferentes sobre o mesmo número.

    Os 10% da T21 decidem o que vale REESCREVER na rubrica — um campo com 4,5% de flip é bom
    o bastante para pontuar. Testemunhar contra o modelo é outra exigência: qualquer flip
    próprio é um alarme que dispara sozinho. `contradiz_evidencia` (4,5%) fica abaixo do corte
    da T21 e mesmo assim não testemunha.
    """
    reprovados = canario.campos_que_podem_testemunhar(FLIP_RATE_V1)

    assert "contradiz_evidencia" in reprovados
    assert "recomendou_acao_sem_base" not in reprovados


def test_texto_livre_das_afirmacoes_nunca_testemunha(canario):
    """A INS.7 mede aquele campo pela CONTAGEM, então não existe número para o texto — e o
    texto é, por construção, mais ruidoso que a contagem que o resume. Sem medida, ele fica
    fora: promovê-lo por falta de número seria escolher o sinal mais frouxo exatamente onde
    não há como conferir."""
    reprovados = canario.campos_que_podem_testemunhar(FLIP_RATE_V1)

    assert "afirmacoes_sem_suporte" in reprovados
    assert "sem medida" in reprovados["afirmacoes_sem_suporte"]


def test_sem_flip_rate_o_canario_volta_ao_criterio_local(canario):
    """A primeira linha de base é gravada antes de existir calibração nenhuma. Nesse mundo o
    critério local é o que há — pior, e é por isso que a CLI avisa em vez de seguir calada."""
    assert canario.campos_que_podem_testemunhar(None) == {}
    assert canario.campos_que_podem_testemunhar({}) == {}

    resultado = canario.classificar([passada(), passada(), passada()])
    assert "n_afirmacoes_sem_suporte" in resultado["estaveis"]


def test_a_retentativa_continua_governando_o_tokens_in(canario):
    """As duas gavetas são independentes: o flip rate não mede `tokens_in`, e a retentativa
    não fala dos campos do veredito. Uma não pode desligar a outra."""
    resultado = canario.classificar(
        [
            passada(),
            passada(tokens_in=12140, chamadas_llm=2),
            passada(tokens_in=12141, chamadas_llm=2),
        ],
        FLIP_RATE_V1,
    )

    assert "tokens_in" in resultado["sem_testemunho"]
    assert "retentaram" in resultado["sem_testemunho"]["tokens_in"]


# ---------------------------------------------------------------------------
# 4 · A rubrica é parte da identidade da rodada, não pano de fundo
# ---------------------------------------------------------------------------


def test_a_linha_de_base_nao_vale_entre_rubricas(canario):
    """A terceira fonte de grito que não é troca de modelo.

    Todo número da rodada é da rubrica que a produziu: o veredito é o que ela decidiu, e o
    `tokens_in` é o tamanho do prompt dela. Comparar a linha de base da v1 contra uma rodada
    da v2 produziria divergência garantida e sem significado — a mesma forma do falso alarme
    da retentativa, e a mesma do provedor.
    """
    base = {
        "served_by": "vertex",
        "model_id": "gemini-3.6-flash",
        "rubrica": "v1",
        "estaveis": {"tokens_in": 5993, "recomendou_acao_sem_base": True},
        "instaveis": {},
    }
    agora = {
        "served_by": "vertex",
        "model_id": "gemini-3.6-flash",
        "rubrica": "v2",
        "estaveis": {"tokens_in": 6402, "recomendou_acao_sem_base": True},
        "instaveis": {},
        "sem_testemunho": {},
    }

    divergencias = canario.comparar(base, agora)
    assert len(divergencias) == 1, "a rubrica trocada tem de parar a comparação, não somar a ela"
    assert "rubrica mudou" in divergencias[0]
    assert "flip_rate_judge_v2.json" in divergencias[0]


def test_linha_de_base_sem_rubrica_e_lida_como_a_do_projeto(canario):
    """As linhas de base de 25/08 e 26/08 não têm a chave — foram gravadas quando a v1 era a
    única rubrica que existia. Explodir aqui obrigaria a regravar a referência por causa de um
    campo novo, que é a coisa que não se regrava sem motivo."""
    base = {
        "served_by": "vertex",
        "model_id": "gemini-3.6-flash",
        "estaveis": {"recomendou_acao_sem_base": True},
        "instaveis": {},
    }
    agora = {
        "served_by": "vertex",
        "model_id": "gemini-3.6-flash",
        "rubrica": canario.RUBRICA_PADRAO,
        "estaveis": {"recomendou_acao_sem_base": True},
        "instaveis": {},
        "sem_testemunho": {},
    }

    assert canario.comparar(base, agora) == []


def test_o_flip_rate_default_segue_a_rubrica_da_rodada(canario):
    """Um default fixo apontando para a v1 faria a rodada da v2 herdar por OMISSÃO a lista de
    testemunhas de outra rubrica — e na direção perigosa, porque a v2 existe justamente para
    mexer no flip dos dois campos que a v1 reprovou."""
    assert canario.caminho_do_flip_rate("v1").name == "flip_rate_judge_v1.json"
    assert canario.caminho_do_flip_rate("v2").name == "flip_rate_judge_v2.json"
    assert canario.CAMINHO_FLIP_RATE_PADRAO == canario.caminho_do_flip_rate(
        canario.RUBRICA_PADRAO
    )


def test_a_passada_julga_com_a_rubrica_pedida(canario, monkeypatch):
    """O furo em si: `_uma_passada` chamava `pontuar_n3` sem `rubrica=`, então o canário
    julgava com a v1 mesmo depois de o projeto adotar outra. A referência que decide quem
    testemunha descreveria uma rubrica que o canário não exerce."""
    pedidas: list[str] = []

    class JulgamentoFalso:
        causa_raiz_correta = False
        mencionou_limitacao_relevante = True
        responde_a_pergunta = "parcial"
        contradiz_evidencia = False
        recomendou_acao_sem_base = True
        afirmacoes_sem_suporte = ()

    def espiao(insumo, configuracao, inferencia, medidor, **kwargs):
        pedidas.append(kwargs.get("rubrica"))
        medidor.registrar_llm(1, 1)
        return JulgamentoFalso()

    monkeypatch.setattr(canario, "pontuar_n3", espiao)
    canario._uma_passada(object(), object(), "v2")

    assert pedidas == ["v2"]
