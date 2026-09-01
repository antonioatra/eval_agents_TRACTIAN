"""Monta a página autocontida — `make app`.

POR QUE UM ARQUIVO SÓ, E NÃO UM SERVIDOR
    A página precisa abrir no dia da apresentação, numa máquina que pode não ser esta, sem
    `pip install`, sem porta livre e sem rede. Um HTML com os dados embutidos abre com duplo
    clique e continua abrindo daqui a um ano. É o mesmo argumento que faz as figuras serem PNG
    versionado em vez de dashboard: o entregável não pode depender de um processo no ar.

    Streamlit e Gradio, que o TAPI §9 sugere, resolvem o problema oposto — desenvolvimento
    rápido de algo que roda enquanto alguém segura o terminal. Aqui o custo cai na hora errada.

POR QUE OS DADOS VÃO EMBUTIDOS E NÃO EM UM `.json` AO LADO
    `file://` bloqueia `fetch` de arquivo irmão na maioria dos navegadores. Um JSON ao lado
    obrigaria a subir servidor para ler o próprio arquivo — que é exatamente a dependência que
    este desenho existe para não ter.
"""

from __future__ import annotations

import json
from pathlib import Path

from tapieval.app import vista

MODELOS_PADRAO = {"qwen3-8b": "Qwen3 8B", "qwen3-14b": "Qwen3 14B"}
BATERIA_PADRAO = "principal_2026_08"

CAMINHO_DO_TEMPLATE = Path(__file__).with_name("pagina.html")
MARCA = "__DADOS__"


def montar_html(dados: dict, *, template: str | None = None) -> str:
    """Injeta o payload no template.

    `</` é escapado dentro do bloco `<script type="application/json">`: uma string do corpus que
    contivesse `</script>` fecharia o bloco antes da hora e quebraria a página inteira de um
    jeito que só aparece com aquele dado específico. É defesa contra o dado, não contra ataque.
    """
    bruto = template if template is not None else CAMINHO_DO_TEMPLATE.read_text(encoding="utf-8")
    if MARCA not in bruto:
        raise vista.ErroDeVista(f"o template não tem a marca {MARCA}")
    carga = json.dumps(dados, ensure_ascii=False).replace("</", "<\\/")
    return bruto.replace(MARCA, carga)


def gerar(
    raiz: Path,
    saida: Path,
    *,
    bateria: str = BATERIA_PADRAO,
    modelos: dict[str, str] | None = None,
) -> Path:
    """Lê a bateria e o placar, escreve a página. Devolve o caminho escrito."""
    dados = vista.montar(raiz, bateria=bateria, modelos=modelos or MODELOS_PADRAO)
    dados["placar"] = vista.carregar_placar(raiz)
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(montar_html(dados), encoding="utf-8")
    return saida


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Gera a página do copiloto de suporte.")
    p.add_argument("--raiz", type=Path, default=Path.cwd())
    p.add_argument("--bateria", default=BATERIA_PADRAO)
    p.add_argument("--saida", type=Path, default=None)
    a = p.parse_args(argv)

    saida = a.saida or (a.raiz / "app" / "copiloto.html")
    caminho = gerar(a.raiz, saida, bateria=a.bateria)
    print(f"{caminho} · {caminho.stat().st_size / 1024 / 1024:.1f} MB")
    return 0
