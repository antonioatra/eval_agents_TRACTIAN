"""T17 — as variantes do agente, e os quatro mutantes, carregados de configuração.

O QUE É UMA VARIANTE
    Uma coluna do experimento. `VariantConfig` já existia no schema porque o trace precisa
    registrar sob qual configuração a run rodou; o que faltava era **de onde ela vem**. Vem
    daqui: um YAML versionado, para que mudar a matriz do experimento não exija tocar em
    código e para que o diff de uma bateria para outra seja legível.

POR QUE O `prompt_sha` É DERIVADO, E NUNCA ESCRITO À MÃO
    O prompt **é** a variante nos mutantes de conteúdo — MUT4 se distingue da base apenas
    por uma frase a mais. Um hash escrito à mão no YAML envelhece em silêncio: alguém edita
    `prompts/agente_v1.md`, o YAML segue com o hash antigo e a coluna do experimento passa a
    ser rotulada com o hash de um prompt que não rodou. O carregador lê o arquivo e calcula.

    A proteção contra "prompt editado depois de congelar" não se perde: ela mora no
    manifesto, que grava o `VariantConfig` inteiro no início da bateria, e em
    `Agent._conferir_prompt_declarado`, que compara o hash gravado com o prompt que recebeu.
    Aqui é a fonte; lá é a conferência.

OS DOIS ERROS DE CONFIGURAÇÃO QUE ESTE MÓDULO SE RECUSA A COMETER EM SILÊNCIO
    Os dois têm a mesma forma: o mutante vira uma cópia da base, a INS.9 mede 0% de detecção,
    e o número parece dizer *"o instrumento não detecta degradação"* quando o que houve foi
    *"não houve degradação para detectar"*. É o padrão do X18 — falha da medição lida como
    falha do medido.

    1. **Campo desconhecido.** `tools_oculta:` em vez de `tools_ocultas:` seria ignorado pelo
       Pydantic, que por padrão descarta chave extra. MUT1 sairia idêntico à base.
    2. **Tool inexistente em `tools_ocultas`.** `get_dataquality` não esconde nada: o filtro
       do servidor remove por nome, e nome errado remove zero tools.

O `variant_id` VEM DA CHAVE DO YAML, NUNCA DE UM CAMPO
    Escrever o id duas vezes é criar duas fontes que divergem, e a divergência aqui renomeia
    coluna de experimento.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from tapieval.mcp.tools import carregar_operacoes
from tapieval.schema.trace import VariantConfig
from tapieval.sut.agent import sha_do_prompt

RAIZ_DO_REPO = Path(__file__).resolve().parents[3]

CAMINHO_PADRAO = RAIZ_DO_REPO / "configs" / "variants.yaml"

CAMPOS_DA_VARIANTE: frozenset[str] = frozenset(VariantConfig.model_fields) - {
    "variant_id",
    "prompt_sha",
}
"""O que o YAML pode declarar. `variant_id` vem da chave e `prompt_sha` é derivado."""

CHAVE_DO_PROMPT = "prompt"
"""Caminho do template, relativo à raiz do repositório. Vira `prompt_sha`."""


class ErroDeVariante(ValueError):
    """A configuração de variantes não pôde ser lida sem ambiguidade."""


def carregar_variantes(caminho: Path | None = None) -> dict[str, VariantConfig]:
    """As variantes do experimento, indexadas por `variant_id`.

    Levanta `ErroDeVariante` em vez de devolver algo parcialmente válido: uma bateria que
    roda com a matriz errada produz números que ninguém consegue reinterpretar depois.
    """
    caminho = caminho or CAMINHO_PADRAO
    if not caminho.exists():
        raise ErroDeVariante(f"configuração de variantes não encontrada: {caminho}")

    documento = yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}
    cruas = documento.get("variantes")
    if not isinstance(cruas, dict) or not cruas:
        raise ErroDeVariante(
            f"{caminho.name}: esperava um mapeamento `variantes:` não vazio no topo"
        )

    catalogo = frozenset(carregar_operacoes())
    variantes: dict[str, VariantConfig] = {}
    for variant_id, corpo in cruas.items():
        variantes[variant_id] = _uma_variante(variant_id, corpo or {}, catalogo, caminho)

    if "base" not in variantes:
        raise ErroDeVariante(
            f"{caminho.name}: falta a variante `base`. Todo mutante se lê contra ela — sem "
            "base não existe o 'original' de que a INS.9 quer distinguir os mutantes"
        )
    return variantes


def _uma_variante(
    variant_id: str, corpo: Any, catalogo: frozenset[str], caminho: Path
) -> VariantConfig:
    if not isinstance(corpo, dict):
        raise ErroDeVariante(f"variante {variant_id!r}: esperava um mapeamento de campos")

    # Antes do ramo genérico: `variant_id` também é "desconhecido" aqui, mas quem o escreveu
    # não errou a grafia — errou o modelo mental. A mensagem específica é a útil.
    if "variant_id" in corpo:
        raise ErroDeVariante(
            f"variante {variant_id!r}: `variant_id` vem da chave do YAML e não pode ser "
            "declarado de novo — duas fontes para o mesmo id divergem em silêncio"
        )

    desconhecidos = set(corpo) - CAMPOS_DA_VARIANTE - {CHAVE_DO_PROMPT}
    if desconhecidos:
        raise ErroDeVariante(
            f"variante {variant_id!r}: campo(s) desconhecido(s) {sorted(desconhecidos)}. "
            f"Campos aceitos: {sorted(CAMPOS_DA_VARIANTE | {CHAVE_DO_PROMPT})}. "
            "Chave com erro de grafia seria descartada em silêncio e a variante sairia "
            "igual à base"
        )

    campos = dict(corpo)
    relativo = campos.pop(CHAVE_DO_PROMPT, None)
    if not relativo:
        raise ErroDeVariante(
            f"variante {variant_id!r}: falta `{CHAVE_DO_PROMPT}`. O prompt é o que distingue "
            "um mutante de conteúdo da base; sem ele não há o que hashear"
        )
    arquivo = RAIZ_DO_REPO / relativo
    if not arquivo.exists():
        raise ErroDeVariante(f"variante {variant_id!r}: prompt inexistente em {relativo}")

    ocultas = frozenset(campos.get("tools_ocultas") or ())
    fora_do_catalogo = sorted(ocultas - catalogo)
    if fora_do_catalogo:
        raise ErroDeVariante(
            f"variante {variant_id!r}: `tools_ocultas` cita {fora_do_catalogo}, que não "
            "existe(m) no catálogo. Nome errado não esconde tool nenhuma, e o mutante sairia "
            "idêntico à base"
        )

    try:
        return VariantConfig(
            variant_id=variant_id,
            prompt_sha=sha_do_prompt(arquivo.read_text(encoding="utf-8")),
            **campos,
        )
    except ValueError as erro:
        raise ErroDeVariante(f"{caminho.name}, variante {variant_id!r}: {erro}") from erro


def mutantes(variantes: dict[str, VariantConfig]) -> dict[str, VariantConfig]:
    """Só as degradações deliberadas — o que a bateria de mutantes roda (`METRICAS §7.1`)."""
    return {nome: v for nome, v in variantes.items() if v.mutante}
