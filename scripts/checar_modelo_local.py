#!/usr/bin/env python3
"""Verifica se um modelo em safetensors está inteiro — antes de mandar o servidor carregá-lo.

POR QUE ISTO EXISTE
    Um download interrompido não parece interrompido: os arquivos estão lá, o diretório tem
    tokenizer, config e index, e só a carga falha — com uma mensagem que fala de tensor e
    offset, não de download. Em 23/08 o 14B parou em 15% do segundo shard e o erro que voltou
    foi `Tensor 'lm_head.weight' invalid data offsets (48619520, 437575680) exceeding the size
    of the file`. Diagnosticar aquilo custou minutos que a T0b não tem de sobra: ela é
    tempo-caixa de 3h.

    O `lm_head` é o pior sintoma possível porque é o último tensor do último shard: ele estoura
    primeiro e sozinho, o que faz um download truncado parecer arquivo corrompido.

O QUE O SCRIPT CONFERE, E NESSA ORDEM
    1. `model.safetensors.index.json` existe e lista os shards.
    2. Todo shard citado existe no disco.
    3. O header de cada shard é legível: os 8 primeiros bytes são o tamanho do header, o resto
       é JSON com `data_offsets` por tensor.
    4. Nenhum `data_offsets` ultrapassa o tamanho real do arquivo — é exatamente a checagem que
       o carregador faz, feita antes e com mensagem que diz "faltam N bytes" em vez de citar um
       tensor.
    5. Todo tensor do `weight_map` aparece em algum header.

USO
    python scripts/checar_modelo_local.py ~/.lmstudio/models/lmstudio-community/Qwen3-14B-MLX-4bit
    python scripts/checar_modelo_local.py --todos    # varre o diretório de modelos do LM Studio

Sai com 1 se algum modelo estiver incompleto.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

MODELOS_DO_LM_STUDIO = Path.home() / ".lmstudio" / "models"


def _humano(n: float) -> str:
    for unidade in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unidade == "GB":
            return f"{n:.1f} {unidade}" if unidade != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} GB"


def ler_header(shard: Path) -> dict:
    """O header de um safetensors: 8 bytes little-endian com o tamanho, depois JSON."""
    with shard.open("rb") as arquivo:
        cru = arquivo.read(8)
        if len(cru) < 8:
            raise ValueError(f"arquivo com {len(cru)} bytes: nem o cabeçalho chegou")
        tamanho = struct.unpack("<Q", cru)[0]
        bruto = arquivo.read(tamanho)
        if len(bruto) < tamanho:
            raise ValueError(
                f"cabeçalho declara {tamanho} bytes e só há {len(bruto)}: download truncado"
            )
        return json.loads(bruto)


def checar_modelo(pasta: Path) -> list[str]:
    """Devolve a lista de problemas. Vazia significa modelo inteiro."""
    problemas: list[str] = []
    indice = pasta / "model.safetensors.index.json"

    if indice.exists():
        mapa = json.loads(indice.read_text())["weight_map"]
        shards = sorted({pasta / nome for nome in mapa.values()})
    else:
        solto = pasta / "model.safetensors"
        if not solto.exists():
            return [f"nem `{indice.name}` nem `model.safetensors` existem em {pasta}"]
        mapa, shards = {}, [solto]

    tensores_vistos: set[str] = set()
    for shard in shards:
        if not shard.exists():
            problemas.append(f"`{shard.name}` não existe")
            continue
        tamanho = shard.stat().st_size
        try:
            header = ler_header(shard)
        except (ValueError, json.JSONDecodeError) as erro:
            problemas.append(f"`{shard.name}` ({_humano(tamanho)}): {erro}")
            continue

        with shard.open("rb") as arquivo:
            inicio_dos_dados = 8 + struct.unpack("<Q", arquivo.read(8))[0]

        # Dois tensores diferentes, e confundi-los aponta para o lugar errado: o que vai mais
        # longe é o ÚLTIMO do arquivo (quase sempre `lm_head`, que é o que o carregador
        # reclama); o primeiro a estourar é onde o download parou de fato.
        fim_exigido = 0
        primeiro_a_estourar = ""
        inicio_do_primeiro = float("inf")
        for nome, meta in header.items():
            if nome == "__metadata__":
                continue
            tensores_vistos.add(nome)
            comeco, fim = (inicio_dos_dados + deslocamento
                           for deslocamento in meta["data_offsets"])
            fim_exigido = max(fim_exigido, fim)
            if fim > tamanho and comeco < inicio_do_primeiro:
                inicio_do_primeiro, primeiro_a_estourar = comeco, nome
        if fim_exigido > tamanho:
            problemas.append(
                f"`{shard.name}` truncado: precisa de {_humano(fim_exigido)} e tem "
                f"{_humano(tamanho)} — faltam {_humano(fim_exigido - tamanho)} "
                f"(a truncagem começa em `{primeiro_a_estourar}`)"
            )

    faltando = sorted(set(mapa) - tensores_vistos) if mapa else []
    if faltando:
        problemas.append(
            f"{len(faltando)} tensor(es) do índice não aparecem em shard nenhum, "
            f"ex.: {', '.join(faltando[:3])}"
        )
    return problemas


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pasta", nargs="?", type=Path)
    parser.add_argument("--todos", action="store_true",
                        help=f"varre {MODELOS_DO_LM_STUDIO}")
    args = parser.parse_args()

    if args.todos:
        alvos = sorted(p.parent for p in MODELOS_DO_LM_STUDIO.rglob("config.json"))
    elif args.pasta:
        alvos = [args.pasta.expanduser()]
    else:
        parser.error("informe uma pasta ou use --todos")

    if not alvos:
        print(f"nenhum modelo encontrado em {MODELOS_DO_LM_STUDIO}")
        return 1

    ruins = 0
    for pasta in alvos:
        problemas = checar_modelo(pasta)
        nome = pasta.relative_to(MODELOS_DO_LM_STUDIO) if MODELOS_DO_LM_STUDIO in pasta.parents \
            else pasta
        if problemas:
            ruins += 1
            print(f"[INCOMPLETO] {nome}")
            for problema in problemas:
                print(f"             {problema}")
        else:
            print(f"[OK]         {nome}")
    return 1 if ruins else 0


if __name__ == "__main__":
    sys.exit(main())
