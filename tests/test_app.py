"""O copiloto — os dois registros sobre o mesmo dado, e o que não pode vazar de um para o outro.

O QUE ESTE ARQUIVO PRENDE
    1. **Todo código da taxonomia congelada tem frase em português.** Sem fallback: um código
       novo sem frase precisa quebrar a suíte, não aparecer na interface como linha vazia de
       conteúdo. É o teste que faz `app.texto` acompanhar `METRICAS §6` em vez de envelhecer.
    2. **A frase carrega o dado daquela execução.** Se ela fosse tradução fixa do rótulo, seria
       o mesmo jargão com outras palavras, e o engenheiro continuaria sem saber o que fazer.
    3. **O jargão da avaliação não aparece na superfície do engenheiro.** `D1`, `S0`, `N1.5`,
       "corte S1" e "seed" são vocabulário de quem julga o instrumento. O teste varre o template
       fora do bloco escondido e reprova se algum deles vazar — que foi exatamente o defeito
       apontado na revisão de 01/09.
    4. **A página não sabe executar nada.** Nenhum import de rede, de runner ou de LLM. Uma
       aplicação de inspeção que pudesse disparar o agente teria uma dependência que a
       apresentação não pode ter.
    5. **O placar não é digitado.** Cada número dele confere com o JSON do notebook que o
       produziu; divergir é ter duas verdades sobre a mesma bateria.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tapieval.app import gerar, vista
from tapieval.app import texto as tx
from tapieval.scoring.severidade import (
    CATALOGO_DE_FALHAS,
    CODIGOS_QUE_EXIGEM_N3,
    classificar_falhas,
)
from tests.test_severidade import n1_limpo, n2_limpo

RAIZ = Path(__file__).resolve().parents[1]
TEMPLATE = RAIZ / "src" / "tapieval" / "app" / "pagina.html"
PLACAR = RAIZ / "docs" / "anexos" / "resultados" / "placar_modelos.json"
BATERIA = "principal_2026_08"


# ---------------------------------------------------------------------------
# 1. Toda falha sabe se dizer em português
# ---------------------------------------------------------------------------


def test_todo_codigo_da_taxonomia_tem_frase_para_o_engenheiro():
    """Sem fallback. Código novo sem frase quebra aqui, e não vira linha muda na interface."""
    assert tx.codigos_sem_frase() == frozenset()


def test_codigo_desconhecido_levanta_em_vez_de_devolver_frase_generica():
    with pytest.raises(tx.ErroDeTexto, match="não tem frase"):
        tx.explicar("Z9", n1_limpo(), n2_limpo())


def test_a_gravidade_e_um_para_um_com_a_escala_congelada():
    """Rótulo de apresentação, não segunda régua: duas réguas dariam duas respostas para
    "isto reprova?", que é o defeito que o A10 já desfez uma vez."""
    severidades = {e.severidade for e in CATALOGO_DE_FALHAS.values()}
    assert severidades <= set(tx.GRAVIDADE)
    assert len(set(tx.GRAVIDADE.values())) == len(tx.GRAVIDADE)


@pytest.mark.parametrize(
    ("codigo", "n1", "n2", "esperado"),
    [
        ("P1", {"tools_faltantes": ["get_baseline"]}, {}, "get_baseline"),
        ("P1", {}, {"cobertura_evidencial": 0.25}, "25%"),
        ("P2", {"tools_extras": ["escalate_case"]}, {}, "escalate_case"),
        ("P3", {"args_corretos": 3, "args_avaliados": 5}, {}, "2 de 5"),
        ("P5", {}, {"n_redundantes": 4}, "4 consultas"),
        ("P5", {}, {"estourou_budget": True}, "limite de passos"),
        ("P6", {}, {"parse_failures": 2}, "2 passo"),
    ],
)
def test_a_frase_carrega_o_dado_daquela_execucao(codigo, n1, n2, esperado):
    assert esperado in tx.explicar(codigo, n1_limpo(**n1), n2_limpo(**n2))


def test_p5_distingue_estouro_de_repeticao():
    """São o mesmo código e coisas diferentes para quem revisa: "não terminou" × "andou em
    círculo"."""
    estouro = tx.explicar("P5", n1_limpo(), n2_limpo(estourou_budget=True, n_redundantes=0))
    repeticao = tx.explicar("P5", n1_limpo(), n2_limpo(n_redundantes=3))
    assert estouro != repeticao


# ---------------------------------------------------------------------------
# 2. A manchete
# ---------------------------------------------------------------------------


def _falhas(**n1):
    return classificar_falhas(n1_limpo(**n1), n2_limpo())


def test_execucao_sem_decisao_nao_e_manchete_de_reprovacao():
    """Ela não é ruim — é outra coisa. Ordenar isto depois da severidade produziria "sem
    ressalvas" para uma run que não respondeu, que é o X35 vazando para a interface."""
    v = tx.veredito(_falhas(), pontuavel=False)
    assert v.tom == "vazio"
    assert "raciocínio" in v.frase


def test_a_manchete_segue_a_pior_severidade_presente():
    assert tx.veredito(_falhas(acao_indevida=True), pontuavel=True).tom == "critico"
    assert tx.veredito(
        _falhas(decisao_prevista="orientar", decisao_esperada="escalar", decisao_correta=False),
        pontuavel=True,
    ).tom == "grave"
    assert tx.veredito(_falhas(tools_extras=["x"]), pontuavel=True).tom == "atencao"
    assert tx.veredito([], pontuavel=True).tom == "ok"


# ---------------------------------------------------------------------------
# 3. O jargão não vaza para a superfície do engenheiro
# ---------------------------------------------------------------------------


MARCA_INICIO = "<!-- INICIO REGISTRO DA AVALIACAO -->"
MARCA_FIM = "<!-- FIM REGISTRO DA AVALIACAO -->"


def _fora_do_modo_avaliacao(html: str) -> str:
    """O template menos os blocos que só aparecem atrás de um botão "ver como avaliação".

    Recorta por marcador explícito, e não por `id=` até o próximo `</section>`. A versão
    anterior fazia isto último e passou a comer tudo o que fosse acrescentado DEPOIS do bloco
    dentro da mesma `<section>` — foi o que aconteceu quando a tela da consulta ao vivo entrou:
    ela nasceu inteira dentro do recorte e nunca foi varrida por este teste. Um teste que para
    de olhar para a tela nova sem falhar é pior que teste nenhum, porque ele continua verde.

    São dois blocos hoje — o da execução gravada e o da consulta ao vivo —, e o par de
    marcadores é conferido por `test_os_marcadores_do_registro_da_avaliacao_estao_pareados`.
    """
    limpo, resto = "", html
    while MARCA_INICIO in resto:
        antes, resto = resto.split(MARCA_INICIO, 1)
        _dentro, resto = resto.split(MARCA_FIM, 1)
        limpo += antes
    return limpo + resto


JARGAO = re.compile(
    r"corte S[0-2]\b"          # a linguagem dos cortes
    r"|\bseed \$\{"            # "seed 42" na interface
    r"|\bN[1-4]\.\d"           # as camadas de medição
    r"|\bX\d{2}\b"             # os achados numerados do diário
    r"|detectada_por"
)


def test_o_vocabulario_da_avaliacao_nao_aparece_na_tela_do_engenheiro():
    """`D1`/`S0`/`N1.5` informam quem julga o instrumento, não quem revisa o rascunho.

    Varre só o que renderiza fora do bloco escondido, e ignora comentário de código — o autor
    do template precisa poder explicar por que a cor da tentativa é aquela.
    """
    superficie = _fora_do_modo_avaliacao(TEMPLATE.read_text(encoding="utf-8"))
    sem_comentarios = re.sub(r"/\*.*?\*/", "", superficie, flags=re.S)
    # a aba do placar é para a banca, e tem vocabulário próprio declarado
    sem_placar = sem_comentarios.split('id="p-placar"')[0]

    achados = sorted({m.group(0) for m in JARGAO.finditer(sem_placar)})
    assert not achados, f"jargão de avaliação na tela do engenheiro: {achados}"


def test_os_marcadores_do_registro_da_avaliacao_estao_pareados():
    """Sem par, `_fora_do_modo_avaliacao` recorta até o fim do arquivo — e a varredura de jargão
    passa a olhar para nada, em silêncio. É o modo de falha que este teste existe para pegar."""
    html = TEMPLATE.read_text(encoding="utf-8")
    assert html.count(MARCA_INICIO) == html.count(MARCA_FIM) >= 2
    assert html.index(MARCA_INICIO) < html.index(MARCA_FIM)


def test_a_tela_da_consulta_ao_vivo_e_varrida_pela_regra_do_jargao():
    """A tela ao vivo tem de estar DENTRO do que o teste de jargão olha.

    O bloco escondido dela (os 19 códigos, com o que não foi medido) fica fora, como o da
    execução gravada; o resto — pergunta, resposta, trace, frase do engenheiro — fica dentro.
    Sem esta âncora, um recorte mal posto devolveria a tela inteira para a sombra outra vez.
    """
    superficie = _fora_do_modo_avaliacao(TEMPLATE.read_text(encoding="utf-8"))
    assert 'id="tela-vivo"' in superficie
    assert 'id="sinais-vivo"' in superficie
    assert 'id="tab-nao-medidos"' not in superficie


def test_a_tabela_da_taxonomia_nao_mostra_mais_a_camada_detectora():
    """`N1.5` era uma coluna. Ela diz de qual métrica o código sai — informação do método, que
    nem no modo avaliação ajuda a ler a execução."""
    html = TEMPLATE.read_text(encoding="utf-8")
    assert "detectada_por" not in html
    assert "Detectada por" not in html


# ---------------------------------------------------------------------------
# 4. A página não executa nada
# ---------------------------------------------------------------------------


def test_a_aplicacao_nao_importa_nada_que_execute_agente_ou_rede():
    """Uma aplicação de inspeção que pudesse disparar o agente teria a dependência que a
    apresentação não pode ter — GPU, endpoint no ar, dezenas de segundos por pergunta."""
    proibidos = ("httpx", "requests", "socket", "tapieval.sut", "tapieval.runner", "tapieval.mcp")
    for modulo in (RAIZ / "src" / "tapieval" / "app").glob("*.py"):
        fonte = modulo.read_text(encoding="utf-8")
        importes = re.findall(r"^\s*(?:from|import)\s+([\w.]+)", fonte, re.M)
        for proibido in proibidos:
            assert not any(i == proibido or i.startswith(proibido + ".") for i in importes), (
                f"{modulo.name} importa {proibido}"
            )


def test_a_lista_de_codigos_de_conteudo_nao_diverge_da_do_scorer():
    """A página diz "esta falha só aparece com o avaliador por LLM". Se a lista daqui
    envelhecer, ela dirá isso de um código errado."""
    assert set(vista.CODIGOS_DE_CONTEUDO_QUE_EXIGEM_JUDGE) == set(CODIGOS_QUE_EXIGEM_N3)


# ---------------------------------------------------------------------------
# 5. O placar confere com os notebooks
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def placar() -> dict:
    if not PLACAR.exists():
        pytest.skip("placar ausente — rode scripts/gerar_placar.py")
    return json.loads(PLACAR.read_text(encoding="utf-8"))


def test_o_placar_declara_as_fontes_de_cada_criterio(placar):
    for criterio in placar["criterios"]:
        assert criterio["fonte"].startswith("docs/"), criterio["criterio"]


def test_o_placar_bate_com_os_resultados_dos_notebooks(placar):
    """Os dois números que a apresentação mais cita, conferidos contra quem os produziu."""
    resultados = RAIZ / "docs" / "anexos" / "resultados"
    passk = json.loads((resultados / "resultados_passk.json").read_text(encoding="utf-8"))
    taxo = json.loads((resultados / "resultados_taxonomia.json").read_text(encoding="utf-8"))
    por = {c["criterio"]: c for c in placar["criterios"]}

    media = por["Média simples das execuções"]
    assert media["quatorze"] == f"{passk['curvas']['14B|incluir']['media_simples']:.1%}".replace(
        ".", ","
    )
    oficial = por["Sem nenhuma ressalva, nem de atenção (o corte oficial)"]
    nominal = [s for s in taxo["sensibilidade"] if s["corte"] == "S2"]
    assert all(s["n_aprovadas"] == 0 for s in nominal)
    assert oficial["vencedor"] == "empate em zero"


def test_o_sut_de_referencia_fica_fora_do_placar_com_o_motivo_escrito(placar):
    """Ele faz 100% no corte S1 e rodou nos 6 cenários de dev, contra os 18 de test desta
    bateria — interseção zero. Pôr os dois na mesma tabela leria como o mesmo teste."""
    assert "interseção zero" in placar["aviso_sut_de_referencia"]
    for criterio in placar["criterios"]:
        assert "referência" not in criterio["criterio"].lower()


# ---------------------------------------------------------------------------
# 6. A vista, sobre a bateria real
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def dados() -> dict:
    if not (RAIZ / "runs" / BATERIA / "scores.jsonl").exists():
        pytest.skip("bateria principal ausente")
    return vista.montar(RAIZ, bateria=BATERIA, modelos=gerar.MODELOS_PADRAO)


def test_a_vista_traz_as_288_execucoes_e_as_18_perguntas(dados):
    assert dados["totais"]["execucoes"] == 288
    assert dados["totais"]["perguntas"] == 18
    assert dados["totais"]["sem_decisao"] == 37


def test_toda_tentativa_tem_manchete_e_os_tres_cortes(dados):
    for por_modelo in dados["cenarios"].values():
        for tentativas in por_modelo.values():
            for t in tentativas:
                assert t["veredito_humano"]["frase"]
                assert set(t["aprova"]) == {"S0", "S1", "S2"}
                for falha in t["falhas"]:
                    assert falha["humano"], falha["codigo"]


def test_bateria_inexistente_levanta_em_vez_de_gerar_pagina_vazia(tmp_path):
    """Página vazia parece uma bateria em que nada falhou, que é a leitura mais errada possível."""
    (tmp_path / "runs" / "fantasma").mkdir(parents=True)
    (tmp_path / "runs" / "fantasma" / "scores.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(vista.ErroDeVista, match="sem scores"):
        vista.montar(tmp_path, bateria="fantasma", modelos=gerar.MODELOS_PADRAO)


def test_a_pagina_gerada_embute_os_dados_e_nao_referencia_arquivo_irmao(dados, tmp_path):
    """`file://` bloqueia `fetch` de irmão: um JSON ao lado obrigaria a subir servidor para ler
    o próprio arquivo, que é a dependência que este desenho existe para não ter."""
    html = gerar.montar_html(dados | {"placar": []})
    assert gerar.MARCA not in html
    assert "dados-inspetor.json" not in html
    assert '<script type="application/json" id="dados">' in html
    assert "</script>" not in html.split('id="dados">')[1].split("</script>")[0] + ""


def test_o_payload_escapa_barra_de_fechamento(dados):
    """Uma string do corpus com `</script>` fecharia o bloco antes da hora e quebraria a página
    inteira — de um jeito que só aparece com aquele dado específico."""
    html = gerar.montar_html({"x": "</script><b>oi", "placar": []})
    bloco = html.split('id="dados">')[1].split("</script>")[0]
    assert "</script>" not in bloco
    assert "<\\/script>" in bloco
