"""T31 — a identidade das figuras, e a divergência que ninguém denuncia sozinha.

O QUE ESTE ARQUIVO PRENDE
    A T31 consertou quatro inconsistências que conviveram meses no repositório sem que nada
    quebrasse, porque **nenhuma delas muda um número**. Cada figura, olhada sozinha, estava
    certa; o conjunto é que não era um conjunto:

    1. metade dos notebooks declarava `family="Inter, …"` e a outra metade caía no default do
       plotly — duas tipografias no mesmo conjunto de slides;
    2. `scale=2` em uns e `scale=3` em outros, sobre larguras lógicas de 940, 980 e 1180 —
       quatro densidades de pixel, que projetadas viram uma figura nítida ao lado de uma borrada;
    3. `nb05` tinha anotação em `size=8`, que projetada não é pequena: é ilegível;
    4. nenhum SVG, então o slide esticava um raster.

    Erro que não muda número precisa de teste, ou volta na próxima figura. Os testes abaixo
    varrem os **notebooks versionados** e o diretório `figures/`, que é onde a divergência
    reaparece — não o módulo, que é fácil de manter certo.
"""

from __future__ import annotations

import json
import re
import struct
from pathlib import Path

import pytest

from tapieval import figuras as fg

RAIZ = Path(__file__).resolve().parents[1]
NOTEBOOKS = sorted((RAIZ / "notebooks").glob("nb0*.ipynb"))
FIGURAS = RAIZ / "figures"
INDEX = FIGURAS / "INDEX.md"


def fonte_do_notebook(caminho: Path) -> str:
    nb = json.loads(caminho.read_text(encoding="utf-8"))
    return "\n".join(
        "".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"
    )


@pytest.fixture(scope="module")
def fontes() -> dict[str, str]:
    if not NOTEBOOKS:
        pytest.skip("nenhum notebook no disco")
    return {c.name: fonte_do_notebook(c) for c in NOTEBOOKS}


# ---------------------------------------------------------------------------
# O piso de legibilidade
# ---------------------------------------------------------------------------


def test_o_piso_de_corpo_sobrevive_a_condicao_de_projecao():
    """`CORPO_MINIMO` não é gosto: é o menor tamanho que passa em `legivel_a_50`."""
    assert fg.legivel_a_50(fg.CORPO_MINIMO)
    assert not fg.legivel_a_50(fg.CORPO_MINIMO - 1)


def test_toda_a_escala_tipografica_esta_acima_do_piso():
    for papel, tamanho in fg.TAMANHO.items():
        assert tamanho >= fg.CORPO_MINIMO, papel
        assert fg.legivel_a_50(tamanho), papel


def test_figura_mais_larga_precisa_de_corpo_maior():
    """A conta depende da largura, e na direção que engana: a figura MAIS LARGA é a que aperta,
    porque ela espreme mais conteúdo lógico na mesma largura de tela. Numa de 1600 px lógicos o
    piso não passa. É por isso que `LARGURA` é canônica em vez de escolhida por figura."""
    assert not fg.legivel_a_50(fg.CORPO_MINIMO, largura_logica=1600)
    assert fg.legivel_a_50(fg.CORPO_MINIMO, largura_logica=800)


def test_nenhum_notebook_usa_corpo_abaixo_do_piso(fontes):
    """Varre `size=` em TODA a fonte, inclusive anotação — que é onde ele sempre escapa."""
    fora = {
        nome: sorted({int(n) for n in re.findall(r"size=(\d+)", src) if int(n) < fg.CORPO_MINIMO})
        for nome, src in fontes.items()
    }
    fora = {k: v for k, v in fora.items() if v}
    assert not fora, f"corpo abaixo de {fg.CORPO_MINIMO}: {fora}"


# ---------------------------------------------------------------------------
# Uma identidade só
# ---------------------------------------------------------------------------


def test_todo_notebook_de_figura_usa_o_modulo_de_estilo(fontes):
    for nome, src in fontes.items():
        assert "from tapieval import figuras as fg" in src, nome


def test_nenhum_notebook_redeclara_a_paleta_central(fontes):
    """As cores que o módulo possui não podem ter uma segunda declaração: é assim que o azul
    de uma figura vira outro azul na figura ao lado sem ninguém ver."""
    centrais = {fg.TINTA, fg.TINTA2, fg.SUPERFICIE, fg.AZUL, fg.AMBAR, fg.VERDE,
                fg.CINZA, fg.VERMELHO}
    for nome, src in fontes.items():
        literais = {h.lower() for h in re.findall(r'"(#[0-9a-fA-F]{6})"', src)}
        repetidas = literais & {c.lower() for c in centrais}
        assert not repetidas, f"{nome} redeclara {sorted(repetidas)} — use `fg.`"


def test_toda_figura_declara_a_familia_tipografica(fontes):
    """Sem declaração explícita o plotly usa o default dele, e o conjunto fica com duas
    tipografias — o defeito original da T31."""
    for nome, src in fontes.items():
        assert "fg.FAMILIA" in src, nome


def test_toda_exportacao_passa_pelo_modulo(fontes):
    """`write_image` direto grava um formato só e com a escala que a célula escolher."""
    for nome, src in fontes.items():
        assert "fg.exportar(" in src, nome
        assert ".write_image(" not in src, f"{nome} ainda exporta por fora do módulo"


# ---------------------------------------------------------------------------
# O disco
# ---------------------------------------------------------------------------


def _figuras_do_index() -> list[str]:
    if not INDEX.exists():
        pytest.skip("INDEX.md ausente")
    return sorted(set(re.findall(r"`(fig\d+_[a-z0-9_]+)\.png`", INDEX.read_text(encoding="utf-8"))))


def test_toda_figura_do_index_existe_em_png_e_svg():
    """O PNG é o que entra no README; o SVG é o que sobrevive ao projetor. Um sem o outro é a
    versão do slide atrasada em relação à do documento, sem nada que denuncie."""
    faltando = []
    for nome in _figuras_do_index():
        for ext in ("png", "svg"):
            if not (FIGURAS / f"{nome}.{ext}").exists():
                faltando.append(f"{nome}.{ext}")
    assert not faltando, faltando


def test_toda_figura_no_disco_esta_declarada_no_index():
    """Figura órfã é figura que alguém pode citar sem saber o que ela não sustenta."""
    no_disco = {p.stem for p in FIGURAS.glob("fig*.png")}
    assert no_disco == set(_figuras_do_index())


def test_todas_as_figuras_tem_a_mesma_densidade_de_pixel():
    """Larguras lógicas diferentes com a mesma `ESCALA` dão densidades diferentes, e num slide
    isso aparece como uma figura nítida ao lado de uma borrada."""
    larguras = {}
    for p in sorted(FIGURAS.glob("fig*.png")):
        larguras[p.name] = struct.unpack(">II", p.read_bytes()[16:24])[0]
    assert len(set(larguras.values())) == 1, larguras
    assert next(iter(larguras.values())) == fg.LARGURA * fg.ESCALA


# ---------------------------------------------------------------------------
# `exportar`
# ---------------------------------------------------------------------------


class FiguraFalsa:
    """O mínimo que `exportar` usa. Um plotly de verdade aqui testaria o plotly."""

    def __init__(self) -> None:
        self.gravou: list[tuple[str, int | None]] = []

    def write_image(self, caminho, scale=None):  # noqa: ANN001
        Path(caminho).write_bytes(b"x")
        self.gravou.append((Path(caminho).suffix, scale))


def test_exportar_grava_os_dois_formatos_da_mesma_figura(tmp_path):
    fig = FiguraFalsa()
    png, svg = fg.exportar(fig, "fig99_teste", tmp_path)

    assert png.exists() and svg.exists()
    assert dict(fig.gravou) == {".png": fg.ESCALA, ".svg": None}


def test_exportar_recusa_nome_com_extensao(tmp_path):
    """Com extensão, gravaria `fig09_curvas.png.svg` e ninguém veria até a apresentação."""
    with pytest.raises(fg.ErroDeFigura, match="sem extensão"):
        fg.exportar(FiguraFalsa(), "fig99_teste.png", tmp_path)


def test_o_layout_comum_alinha_o_titulo_a_esquerda_e_pinta_os_dois_fundos():
    """Área de plotagem branca sobre papel creme produz um retângulo que o leitor lê como
    caixa, e não há caixa nenhuma."""
    lay = fg.layout(titulo="t", subtitulo="s")
    assert lay["title"]["x"] == 0
    assert lay["plot_bgcolor"] == lay["paper_bgcolor"] == fg.SUPERFICIE
    assert lay["title"]["font"]["family"] == fg.FAMILIA
    assert lay["width"] == fg.LARGURA


def test_a_cor_do_modelo_e_estavel_entre_figuras():
    """Trocar a cor de um modelo entre figuras faz o leitor comparar a coisa errada sem
    perceber que comparou."""
    assert fg.COR_DO_MODELO == {"qwen3-8b": fg.AZUL, "qwen3-14b": fg.AMBAR}
    assert set(fg.RAMPA_DE_SEVERIDADE) == {"S0", "S1", "S2", "S3"}
    assert not set(fg.RAMPA_DE_SEVERIDADE.values()) & {fg.AZUL, fg.AMBAR}
