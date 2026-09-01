"""T31 — a identidade visual das figuras, num lugar só, e a exportação que os slides usam.

O QUE ESTE MÓDULO EXISTE PARA CONSERTAR
    Até 01/09 cada notebook redeclarava a própria paleta e a própria escala de exportação. O
    resultado passou despercebido porque cada figura, olhada sozinha, estava certa:

    - `nb01`–`nb03` declaravam `family="Inter, …"`; `nb04`–`nb06` não declaravam nada e caíam
      no default do plotly. **Duas tipografias no mesmo conjunto de slides.**
    - `nb01`–`nb03` exportavam com `scale=2` e `nb04`–`nb06` com `scale=3`, sobre larguras
      lógicas diferentes (940, 980, 1180). **Quatro densidades de pixel diferentes**, que numa
      apresentação aparecem como uma figura nítida ao lado de uma borrada.
    - `nb05` tinha anotação em `size=8`. Projetada, ela não é pequena — é ilegível.

    Nada disso é erro de análise, e é justamente por isso que precisava de um lugar só: erro
    que não muda número nenhum não tem quem o denuncie.

⚠️ O PISO DE CORPO É REQUISITO DE PROJEÇÃO, NÃO GOSTO
    `CORPO_MINIMO` é o menor tamanho lógico que sobrevive à condição em que estas figuras vão
    ser lidas: projetadas, e olhadas de longe. A conta está em `legivel_a_50`, e
    `test_figuras.py` varre os notebooks e reprova qualquer `size=` abaixo dele — inclusive em
    anotação, que é onde ele sempre escapa.

POR QUE PNG **E** SVG
    O PNG é o que entra no README e no `make repro` (md5 estável, comparável entre execuções).
    O SVG é o que sobrevive ao projetor: vetor não borra quando o slide é esticado para 3 m de
    largura, e é o formato que a T31 pede. Os dois saem da MESMA figura na mesma chamada, para
    não existir a versão que foi regravada e a que não foi.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paleta
# ---------------------------------------------------------------------------

TINTA = "#0b0b0b"
"""Título e texto de destaque."""
TINTA2 = "#52514e"
"""Corpo, rótulo de eixo, subtítulo."""
SUPERFICIE = "#fcfcfb"
"""Fundo da figura E da área de plotagem — os dois, sempre. Área de plotagem branca sobre
papel creme produz um retângulo que o leitor lê como caixa, e não há caixa nenhuma."""

AZUL = "#2a78d6"
AMBAR = "#eda100"
VERDE = "#1baf7a"
CINZA = "#c9c8c4"
VERMELHO = "#d64545"

COR_DO_MODELO = {"qwen3-8b": AZUL, "qwen3-14b": AMBAR}
"""Fixo em todo o trabalho. Trocar a cor de um modelo entre figuras é o tipo de inconsistência
que faz o leitor comparar a coisa errada sem perceber que comparou."""

ROTULO_DO_MODELO = {"qwen3-8b": "8B", "qwen3-14b": "14B"}

RAMPA_DE_SEVERIDADE = {"S0": "#7d1a12", "S1": "#b3403a", "S2": "#d99b8f", "S3": CINZA}
"""Rampa própria para severidade, de propósito fora do azul/âmbar dos modelos: numa figura que
mostra os dois eixos ao mesmo tempo, cor repetida vira leitura cruzada."""

# ---------------------------------------------------------------------------
# Tipografia e escala
# ---------------------------------------------------------------------------

FAMILIA = "Inter, Helvetica, Arial, sans-serif"
"""Declarada em toda figura. Sem isto, metade do conjunto cai no default do plotly."""

CORPO_MINIMO = 10
"""O menor `size` lógico permitido em qualquer texto de figura — ver `legivel_a_50`."""

TAMANHO = {
    "titulo": 19,
    "subtitulo_do_painel": 13,
    "eixo": 12,
    "rotulo": 11,
    "nota": CORPO_MINIMO,
}
"""A escala tipográfica. Cinco degraus é o que estas figuras precisam; mais degraus viram
decisão caso a caso, que é como as tipografias divergiram em primeiro lugar."""

ESCALA = 3
"""Multiplicador do `write_image`. Com as larguras canônicas abaixo, dá ~300 dpi."""

LARGURA = 1180
"""Largura lógica canônica. Uma só, para que a mesma `ESCALA` produza a mesma densidade em
todas as figuras — que é metade do defeito que a T31 conserta."""

ALTURAS = {"faixa": 560, "alta": 620, "baixa": 460}
"""As três alturas que o conjunto usa. Nomeadas para não virarem número solto na célula."""


def legivel_a_50(tamanho: int, *, largura_logica: int = LARGURA) -> bool:
    """O texto sobrevive à condição de leitura destas figuras?

    A condição não é "50% de zoom" em abstrato: é a figura ocupando meia largura de um
    projetor de 1920 px, que é como ela aparece num slide de duas colunas — o pior caso do
    conjunto. A figura de `largura_logica` px vira 960 px na tela, então o fator é
    `960 / largura_logica`, e o piso de conforto para leitura projetada é 8 px na tela.

    Com `LARGURA = 1180` o fator é 0,81, e `CORPO_MINIMO = 10` dá 8,1 px. É apertado de
    propósito: subir o piso encolheria o que cabe na figura, e o que a T31 quer garantir é que
    nada fique ABAIXO da linha, não que tudo fique confortável.
    """
    return tamanho * (960 / largura_logica) >= 8.0


def layout(
    *,
    titulo: str,
    subtitulo: str = "",
    altura: str = "faixa",
    **extra: Any,
) -> dict:
    """O layout comum a toda figura do trabalho.

    O título é sempre alinhado à esquerda (`x=0`): título centralizado sobre eixo alinhado à
    esquerda desalinha os dois eixos de leitura da figura, e todas estas têm eixo y à esquerda.
    """
    cabecalho = f"<b>{titulo}</b>"
    if subtitulo:
        cabecalho += f"<br><sub>{subtitulo}</sub>"
    base = {
        "title": {
            "text": cabecalho,
            "font": {"color": TINTA, "size": TAMANHO["titulo"], "family": FAMILIA},
            "x": 0,
            "xanchor": "left",
        },
        "font": {"color": TINTA2, "size": TAMANHO["rotulo"], "family": FAMILIA},
        "plot_bgcolor": SUPERFICIE,
        "paper_bgcolor": SUPERFICIE,
        "width": LARGURA,
        "height": ALTURAS[altura],
    }
    return base | extra


class ErroDeFigura(ValueError):
    """A figura não pode ser exportada sem produzir um arquivo que engana."""


def exportar(fig: Any, nome: str, diretorio: Path) -> tuple[Path, Path]:
    """Grava PNG (≈300 dpi) e SVG da MESMA figura, e devolve os dois caminhos.

    Os dois na mesma chamada de propósito: enquanto o notebook gravava só o PNG e alguém
    exportava o SVG à mão quando lembrava, existia a possibilidade de o vetor do slide estar
    uma versão atrás do raster do README — e nada no repositório denunciaria isso.

    `nome` vem sem extensão. Com extensão, levanta: a alternativa é gravar
    `fig09_curvas.png.svg` e ninguém perceber até a apresentação.
    """
    if nome.endswith((".png", ".svg")):
        raise ErroDeFigura(f"`nome` vem sem extensão: {nome!r}")
    diretorio.mkdir(parents=True, exist_ok=True)
    png, svg = diretorio / f"{nome}.png", diretorio / f"{nome}.svg"
    fig.write_image(png, scale=ESCALA)
    fig.write_image(svg)
    return png, svg
