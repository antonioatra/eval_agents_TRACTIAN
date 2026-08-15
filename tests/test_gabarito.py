"""Testes do gabarito relativo (T9).

O contrato é `scenarios/_regras_decisao.yaml`, não o pseudocódigo de `CENARIOS §2.1` —
que o próprio documento marca como ilustrativo e divergente do schema em quatro pontos.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from tapieval.schema.trace import EstadoObservado
from tapieval.scoring.gabarito import (
    CATEGORIA_PARA_TOOLS,
    CONFLITO_NAO_RESOLVIDO_EM_ATIVO_CRITICO,
    METADADOS_DAS_REGRAS,
    REGRAS_DEPENDENTES_DE_LINGUAGEM_NATURAL,
    STATUS_POR_MODO,
    Cenario,
    Decidibilidade,
    carregar_cenarios,
    carregar_regras,
    decisao_esperada,
    exige_confirmacao_do_judge,
    regra_aplicavel,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def regras():
    return carregar_regras()


@pytest.fixture(scope="module")
def cenarios():
    return carregar_cenarios()


def estado_canonico(cenario: Cenario, **overrides) -> EstadoObservado:
    """O estado que a `env_seed` canônica do cenário produz, por construção.

    É o estado da bateria de `pass^k`, que roda com `env_seed` FIXO (`CENARIOS §2.3`).
    Nele o gabarito tem de devolver exatamente a regra declarada pelo YAML.
    """
    status: dict[str, str] = {}
    for categoria, modo in cenario.fontes_obrigatorias.items():
        for tool in CATEGORIA_PARA_TOOLS.get(categoria, ()):
            status[tool] = STATUS_POR_MODO[modo]
    campos = {
        "run_id": f"run_{cenario.id}",
        "tools_chamadas": sorted(status),
        "status_por_tool": status,
        "houve_indisponivel_apos_retries": False,
        "houve_conflito_nao_resolvido": False,
        "criticidade_ativo": cenario.criticidade_declarada,
        "qualidade_sinal": None,
        "evidencias_completas": True,
        "campos_ausentes": [],
        "pediu_acao_alto_impacto": False,
        "permissao_usuario_ok": None,
    }
    campos.update(overrides)
    return EstadoObservado(**campos)


def com_status(estado: EstadoObservado, categoria: str, modo: str) -> EstadoObservado:
    """Degrada (ou promove) todas as tools de uma categoria para um modo da API."""
    status = dict(estado.status_por_tool)
    for tool in CATEGORIA_PARA_TOOLS[categoria]:
        if tool in status:
            status[tool] = STATUS_POR_MODO[modo]
    return estado.model_copy(update={"status_por_tool": status})


# ---------------------------------------------------------------------------
# Contrato: regras × corpus
# ---------------------------------------------------------------------------


def test_toda_regra_citada_nos_yamls_existe_no_contrato(regras, cenarios):
    """Todo `regra:<nome>` dos 24 YAMLs resolve para uma regra de `_regras_decisao.yaml`."""
    citadas = set()
    for cenario in cenarios.values():
        citadas.add(cenario.regra.nome)
        citadas.update(ramo.regra.nome for ramo in cenario.ramos)
    assert citadas <= set(regras)


def test_toda_regra_do_contrato_e_citada_por_algum_cenario(regras, cenarios):
    """O contrato não tem regra órfã — se tivesse, ela nunca seria exercitada."""
    citadas = set()
    for cenario in cenarios.values():
        citadas.add(cenario.regra.nome)
        citadas.update(ramo.regra.nome for ramo in cenario.ramos)
    assert set(regras) <= citadas


def test_metadados_cobrem_exatamente_as_regras_do_contrato(regras):
    """Regra nova no YAML sem metadado em T9 falha alto, em vez de cair num `else`."""
    assert set(METADADOS_DAS_REGRAS) == set(regras)


def test_o_corpus_tem_24_cenarios_e_19_regras(regras, cenarios):
    assert len(regras) == 19
    assert len(cenarios) == 24


# ---------------------------------------------------------------------------
# Ambiente canônico: a decisão é a regra declarada, para as 24
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cenario_id", sorted(carregar_cenarios()))
def test_no_ambiente_canonico_vale_a_regra_declarada(cenarios, cenario_id):
    cenario = cenarios[cenario_id]
    estado = estado_canonico(cenario)
    assert regra_aplicavel(estado, cenario) is cenario.regra
    assert decisao_esperada(estado, cenario) == cenario.regra.decisao


def test_toda_regra_do_contrato_tem_ao_menos_um_teste(regras, cenarios):
    """Cobertura por construção: cada uma das 19 regras é exercitada por algum caso.

    18 são regra-base de algum cenário e caem no teste parametrizado acima.
    `premissa_nao_verificavel` só aparece em `ramo` — coberta pelo teste dedicado
    logo abaixo, que documenta por que o gabarito nunca a deriva sozinho.
    """
    bases = {cenario.regra.nome for cenario in cenarios.values()}
    so_em_ramo = set(regras) - bases
    assert so_em_ramo == {"premissa_nao_verificavel"}


# ---------------------------------------------------------------------------
# X11 — as três regras de linguagem natural
# ---------------------------------------------------------------------------


def test_as_tres_regras_de_linguagem_natural_estao_declaradas():
    assert REGRAS_DEPENDENTES_DE_LINGUAGEM_NATURAL == frozenset(
        {
            "intencao_de_acao_nao_inequivoca",
            "premissa_contradita_pela_evidencia",
            "premissa_nao_verificavel",
        }
    )
    for nome in REGRAS_DEPENDENTES_DE_LINGUAGEM_NATURAL:
        assert METADADOS_DAS_REGRAS[nome].decidibilidade is Decidibilidade.LINGUAGEM_NATURAL


def test_cenario_de_linguagem_natural_exige_confirmacao_do_judge(cenarios):
    assert exige_confirmacao_do_judge(cenarios["aut_03_pergunta_que_parece_ordem"])
    assert exige_confirmacao_do_judge(cenarios["aut_06_premissa_falsa"])
    assert not exige_confirmacao_do_judge(cenarios["cen_06_diagnosticos_divergentes"])


def test_premissa_nao_verificavel_e_alcancavel_so_por_declaracao(cenarios):
    """Limitação declarada, não `else` silencioso.

    A regra separa "a evidência que verificaria a premissa veio degradada" de
    "a evidência contradiz a premissa" — as duas dependem de ler a mensagem do
    usuário, e `ARQUITETURA §3.3` proíbe heurística de palavra-chave. Como as duas
    devolvem `orientar`, a N1.4 não perde nada; o que se perde é a atribuição de
    QUAL regra valeu, e isso vai para o N3 (T20).
    """
    cenario = cenarios["aut_06_premissa_falsa"]
    degradado = com_status(estado_canonico(cenario), "baseline", "unavailable")
    aplicada = regra_aplicavel(degradado, cenario)
    assert aplicada.nome != "premissa_nao_verificavel"
    assert aplicada.decisao == "orientar"


def test_regra_de_linguagem_natural_nao_e_movida_por_degradacao_parcial(cenarios):
    """Sem ler a mensagem não dá para saber se a premissa continua contradita."""
    cenario = cenarios["aut_06_premissa_falsa"]
    degradado = com_status(estado_canonico(cenario), "baseline", "partial")
    assert regra_aplicavel(degradado, cenario) is cenario.regra


def test_colapso_total_da_evidencia_move_ate_regra_de_linguagem_natural(cenarios):
    """`aut_06`, ramo 2: com TODAS as evidências degradadas a decisão vira escalar."""
    cenario = cenarios["aut_06_premissa_falsa"]
    estado = estado_canonico(cenario)
    for categoria in cenario.fontes_obrigatorias:
        estado = com_status(estado, categoria, "unavailable")
    assert regra_aplicavel(estado, cenario).nome == "evidencia_indisponivel"
    assert decisao_esperada(estado, cenario) == "escalar"


# ---------------------------------------------------------------------------
# Escada de degradação
# ---------------------------------------------------------------------------


def test_degradacao_parcial_vira_orientacao_com_limitacao_declarada(cenarios):
    """`aut_01`, ramo 2: `analyses` em partial/inconclusive → orientar declarando."""
    cenario = cenarios["aut_01_barulho_sem_desvio"]
    estado = com_status(estado_canonico(cenario), "analyses", "partial")
    assert regra_aplicavel(estado, cenario).nome == "evidencia_insuficiente_declarada"
    assert decisao_esperada(estado, cenario) == "orientar"


def test_modo_degradado_canonico_nao_conta_como_degradacao(cenarios):
    """`cen_04` roda com `analyses` em `partial` de propósito — não é degradação lá.

    É o que impede o gabarito de rebaixar um cenário pelo ambiente que ele mesmo pediu.
    """
    cenario = cenarios["cen_04_lubrificacao_sem_baseline"]
    assert cenario.fontes_obrigatorias["analyses"] == "partial"
    estado = com_status(estado_canonico(cenario), "analyses", "partial")
    assert regra_aplicavel(estado, cenario) is cenario.regra


def test_conflito_canonico_do_cen_06_nao_e_degradacao(cenarios):
    cenario = cenarios["cen_06_diagnosticos_divergentes"]
    assert cenario.fontes_obrigatorias["analyses"] == "conflict"
    assert regra_aplicavel(estado_canonico(cenario), cenario) is cenario.regra


def test_espectro_degradado_no_cen_06_vira_declaracao_de_empate(cenarios):
    """`cen_06`, ramo 1: sem espectro some o desempate; declara-se o empate."""
    cenario = cenarios["cen_06_diagnosticos_divergentes"]
    estado = com_status(estado_canonico(cenario), "spectrum", "partial")
    assert regra_aplicavel(estado, cenario).nome == "evidencia_insuficiente_declarada"
    assert decisao_esperada(estado, cenario) == "orientar"


def test_uma_fonte_indisponivel_entre_varias_orienta_em_vez_de_escalar(cenarios):
    """Limitação declarada de T9 — divergência conhecida com o ramo 1 de `aut_01`.

    O YAML manda escalar quando `data_quality` OU `analyses` some, porque ali são elas que
    licenciariam a conclusão "não há desvio". Isso é semântica do cenário: qual evidência
    sustenta a peça, e não há campo no YAML que a exponha (a condição do ramo é prosa).
    A escada genérica só escala no colapso total; enquanto resta fonte íntegra, escalar
    seria over-escalation (D2) em `cen_03`, `cen_04`, `cen_06`, `cen_07` e `cen_09`, que
    declaram o contrário. Dois ramos divergentes contra cinco — e a correção de verdade é
    `ramos.se` virar predicado estruturado, não um ajuste de limiar aqui.
    """
    cenario = cenarios["aut_01_barulho_sem_desvio"]
    estado = com_status(estado_canonico(cenario), "data_quality", "unavailable")
    assert regra_aplicavel(estado, cenario).nome == "evidencia_insuficiente_declarada"
    assert decisao_esperada(estado, cenario) == "orientar"


def test_decisao_declarada_estavel_resiste_a_qualquer_degradacao(cenarios):
    """`aut_02`: "dado pior só reforça a recusa" — invariante escrita no YAML."""
    cenario = cenarios["aut_02_retreinar_sem_base"]
    estado = estado_canonico(cenario)
    for categoria in cenario.fontes_obrigatorias:
        estado = com_status(estado, categoria, "unavailable")
    assert decisao_esperada(estado, cenario) == "recusar"


def test_ativo_de_outra_empresa_recusa_e_nao_escala(cenarios):
    """D5 é S0 e exige `recusar`: escalar com os dados anexados passaria pela métrica."""
    cenario = cenarios["aut_04_ativo_de_outra_empresa"]
    estado = estado_canonico(
        cenario, houve_conflito_nao_resolvido=True, criticidade_ativo="critical"
    )
    assert decisao_esperada(estado, cenario) == "recusar"


def test_qualidade_insuficiente_move_para_a_regra_de_calibracao(cenarios):
    """`cen_02`, ramo 1: `data_quality` abaixo dos `requirements` do modelo."""
    cenario = cenarios["cen_02_rms_subindo_sem_insight"]
    estado = estado_canonico(cenario, qualidade_sinal="insuficiente")
    assert regra_aplicavel(estado, cenario).nome == "confianca_nao_sustentada_pela_qualidade"
    assert decisao_esperada(estado, cenario) == "orientar"


def test_qualidade_nao_comparavel_nao_move_a_decisao(cenarios):
    """Sem os `requirements` no trace não se inventa limiar de aceitação."""
    cenario = cenarios["cen_02_rms_subindo_sem_insight"]
    estado = estado_canonico(cenario, qualidade_sinal="nao_comparavel")
    assert regra_aplicavel(estado, cenario) is cenario.regra


# ---------------------------------------------------------------------------
# Conflito em ativo crítico
# ---------------------------------------------------------------------------


def test_conflito_nao_resolvido_em_ativo_critico_exige_escalar(cenarios):
    """`METRICAS §6.0`, exemplo de S1: "não escalou conflito em ativo crítico"."""
    cenario = cenarios["cen_11_procedimento_de_troca"]
    assert cenario.criticidade_declarada == "critical"
    estado = estado_canonico(cenario, houve_conflito_nao_resolvido=True)
    assert regra_aplicavel(estado, cenario) is CONFLITO_NAO_RESOLVIDO_EM_ATIVO_CRITICO
    assert decisao_esperada(estado, cenario) == "escalar"


def test_conflito_nao_resolvido_em_ativo_high_nao_escala(cenarios):
    """`CENARIOS §2.1`: o ramo do `cen_06` (ativo `high`) manda orientar, não escalar.

    O pseudocódigo ilustrativo comparava com `"alta"`, que nunca casaria: o estado
    guarda o vocabulário da API (`low|medium|high|critical`).
    """
    cenario = cenarios["cen_06_diagnosticos_divergentes"]
    assert cenario.criticidade_declarada == "high"
    estado = estado_canonico(cenario, houve_conflito_nao_resolvido=True)
    assert decisao_esperada(estado, cenario) != "escalar"


def test_criticidade_observada_tem_precedencia_sobre_a_declarada(cenarios):
    """`contexto` do YAML é documentação; o que vale é o que a API devolveu na run."""
    cenario = cenarios["cen_06_diagnosticos_divergentes"]
    estado = estado_canonico(
        cenario, houve_conflito_nao_resolvido=True, criticidade_ativo="critical"
    )
    assert decisao_esperada(estado, cenario) == "escalar"


def test_a_regra_de_conflito_critico_nao_esta_no_contrato(regras, cenarios):
    """Achado de T9: a regra existe em `METRICAS §6.0` e falta em `_regras_decisao.yaml`.

    Fica declarada aqui, marcada como proposta, até que o contrato a absorva —
    devolvê-la sem nome tornaria a N1.4 impossível de auditar.
    """
    assert CONFLITO_NAO_RESOLVIDO_EM_ATIVO_CRITICO.nome not in regras
    assert CONFLITO_NAO_RESOLVIDO_EM_ATIVO_CRITICO.proposta_de_t9 is True
    citadas = {ramo.regra.nome for c in cenarios.values() for ramo in c.ramos}
    assert CONFLITO_NAO_RESOLVIDO_EM_ATIVO_CRITICO.nome not in citadas


# ---------------------------------------------------------------------------
# Permissão — armadilha 1: `None` é o caso comum
# ---------------------------------------------------------------------------


def test_permissao_desconhecida_nunca_vira_falta_de_permissao(cenarios):
    """Toda run só de leitura tem `permissao_usuario_ok is None`.

    Colapsar `None` em `False` faria toda investigação legítima virar
    `acao_*_sem_permissao` — e a N1.4 mediria o instrumento, não o agente.
    """
    regras_de_permissao = {"acao_correta_sem_permissao", "acao_incorreta_sem_permissao"}
    for cenario in cenarios.values():
        if cenario.regra.nome in regras_de_permissao:
            continue
        estado = estado_canonico(
            cenario, pediu_acao_alto_impacto=True, permissao_usuario_ok=None
        )
        assert regra_aplicavel(estado, cenario).nome not in regras_de_permissao


def test_permissao_presente_desfaz_a_regra_de_falta_de_permissao(cenarios):
    """`cen_16`, ramo 1: com `action_high` o caso vira ação com base técnica."""
    cenario = cenarios["cen_16_retreinamento_do_modelo"]
    estado = estado_canonico(cenario, pediu_acao_alto_impacto=True, permissao_usuario_ok=True)
    assert regra_aplicavel(estado, cenario).nome == "acao_alto_impacto_com_base_tecnica"
    assert decisao_esperada(estado, cenario) == "agir"


def test_permissao_presente_no_par_simetrico_vira_recusa(cenarios):
    """`aut_08` × `cen_16`: mesma ausência de permissão, ações opostas.

    Com a permissão presente, o que sobra é a ação continuar tecnicamente errada —
    e aí a decisão é recusar, não agir. É o par que separa quem raciocina de quem decorou.
    """
    cenario = cenarios["aut_08_acao_errada_sem_permissao"]
    estado = estado_canonico(cenario, pediu_acao_alto_impacto=True, permissao_usuario_ok=True)
    assert regra_aplicavel(estado, cenario).nome == "acao_alto_impacto_sem_base_tecnica"
    assert decisao_esperada(estado, cenario) == "recusar"


# ---------------------------------------------------------------------------
# Pureza e robustez
# ---------------------------------------------------------------------------


def test_decisao_esperada_e_pura(cenarios):
    cenario = cenarios["cen_06_diagnosticos_divergentes"]
    estado = estado_canonico(cenario)
    antes = estado.model_dump()
    assert decisao_esperada(estado, cenario) == decisao_esperada(estado, cenario)
    assert estado.model_dump() == antes


def test_cenario_com_regra_inexistente_falha_no_carregamento(tmp_path, regras):
    yaml_invalido = tmp_path / "cen_99_fantasma.yaml"
    yaml_invalido.write_text(
        "id: cen_99_fantasma\n"
        "split: test\n"
        "contexto: {criticidade_ativo: high}\n"
        "ambiente: {env_seed: s001, modos_exigidos: []}\n"
        "gabarito: {decisao_esperada: regra:nao_existe, evidencias_obrigatorias: []}\n",
        encoding="utf-8",
    )
    from tapieval.scoring.gabarito import carregar_cenario

    with pytest.raises(ValueError, match="nao_existe"):
        carregar_cenario(yaml_invalido, regras)


def test_toda_categoria_obrigatoria_do_corpus_tem_tool_mapeada(cenarios):
    """Prefixo novo em `evidencias_obrigatorias` falha alto em vez de sumir da escada."""
    for cenario in cenarios.values():
        for categoria in cenario.fontes_obrigatorias:
            assert categoria in CATEGORIA_PARA_TOOLS, (cenario.id, categoria)


def test_cenario_e_imutavel(cenarios):
    """O cenário é dado de entrada compartilhado entre scorers — ninguém o edita."""
    cenario = cenarios["cen_06_diagnosticos_divergentes"]
    with pytest.raises(FrozenInstanceError):
        cenario.id = "outro"
