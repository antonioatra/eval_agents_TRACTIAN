#!/usr/bin/env python3
"""Validador do corpus de cenários (T1).

Checa, sem subir a API:
  1. schema  — campos obrigatórios de cada YAML;
  2. catálogo — toda tool citada existe no contrato OpenAPI (nome = operationId em snake_case);
  3. regras   — todo `decisao_esperada: regra:<x>` existe em scenarios/_regras_decisao.yaml;
  4. ambiente — a `env_seed` declarada produz os modos exigidos, replicando resolve_mode da API;
  5. split    — nenhum ativo aparece dos dois lados de dev/test.

Cenário com `status: inviavel` continua sendo validado como documento (schema, tools, regras)
mas sai do corpus executável: não roda, não conta no split e não entra na checagem de ambiente
— ser insatisfazível por qualquer seed é justamente a razão mais provável de ter sido declarado
inviável, e exigir o contrário deixaria o corpus vermelho para sempre.

Uso: inteli-tractian-project/api/.venv/bin/python scripts/validar_cenarios.py
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent
CENARIOS = RAIZ / "scenarios"
API = RAIZ / "inteli-tractian-project"
SEED_CFG = json.loads((API / "data" / "seed.json").read_text())
OVERRIDES = SEED_CFG["overrides"]
DISTRIBUICAO = SEED_CFG["distribution"]

OBRIGATORIOS = [
    "id", "procedencia", "split", "natureza", "solicitacao", "user_id", "ambiente", "gabarito",
]
GABARITO_OBRIGATORIOS = [
    "evidencias_obrigatorias", "tools_esperadas", "decisao_esperada", "proibido",
]

# `status` é opcional: ausente significa `valido`, para que os 24 cenários vivos não precisem
# declarar o caso comum. Só o desvio é escrito (A4/T3).
STATUS_VALIDOS = ("valido", "inviavel")
STATUS_PADRAO = "valido"

# Campos observados nos payloads reais da API (verificado em 14/08 com a API no ar).
# Toda evidência obrigatória precisa apontar para um campo que existe — senão a N1.3 não computa.
CAMPOS_POR_RECURSO = {
    "user": {"id", "name", "role", "permissions", "company_id"},
    "asset": {"id", "name", "company_id", "criticality", "plant", "line", "parent_asset_id",
              "machine_type", "rotation_rpm", "bearing_pn", "bpfo_hz", "bpfi_hz", "bsf_hz",
              "ftf_hz", "line_frequency_hz", "sensor_status", "points"},
    "baseline": {"id", "asset_id", "point_id", "state", "detection_mode", "learnable",
                 "established_at", "invalidated_at", "invalidation_reason", "features"},
    "rms": {"asset_id", "point_id", "unit", "baseline_reference", "baseline_state",
            "alarm_threshold", "samples"},
    "data_quality": {"asset_id", "point_id", "completeness", "freshness_minutes", "snr_db",
                     "staleness_flag"},
    "spectrum": {"asset_id", "point_id", "collected_at", "peaks", "bands_missing"},
    "analyses[]": {"id", "asset_id", "point_id", "type", "detection_mode", "severity",
                   "confidence", "baseline_state_at_detection", "evidence", "limitations",
                   "model_version", "created_at", "status"},
    "model": {"id", "version", "coverage", "processing_state", "last_run_at",
              "requirements.min_completeness", "requirements.min_snr_db",
              "requirements.min_rotation_rpm"},
    "knowledge": {"results[]"},
}
# A própria listagem/documento é a evidência — não há campo a apontar.
EVIDENCIAS_SEM_CAMPO = {"analyses[]", "assets[]", "knowledge"}

SPLIT_ESPERADO = {"dev": 6, "test": 18}


def catalogo_de_tools() -> set[str]:
    """Nomes de tool MCP derivados dos operationId do contrato — fonte única de verdade."""
    texto = (API / "docs" / "api-contract.openapi.yaml").read_text()
    ops = re.findall(r"operationId:\s*(\w+)", texto)
    return {re.sub(r"(?<!^)(?=[A-Z])", "_", op).lower() for op in ops}


def resolve_mode(recurso: str, categoria: str, seed: str | None) -> str:
    """Réplica exata de api/app/prob.py::resolve_mode."""
    ov = OVERRIDES.get(recurso, {})
    if categoria in ov:
        return ov[categoria]
    if seed == "complete":
        return "complete"
    if seed == "degraded":
        return "partial"
    h = hashlib.sha256(f"{seed or 'noseed'}|{recurso}|{categoria}".encode()).hexdigest()
    r = int(h[:12], 16) / float(0xFFFFFFFFFFFF)
    acumulado = 0.0
    for nome, peso in DISTRIBUICAO.items():
        acumulado += peso
        if r < acumulado:
            return nome
    return "complete"


def main() -> int:
    tools = catalogo_de_tools()
    regras = set(yaml.safe_load((CENARIOS / "_regras_decisao.yaml").read_text())["regras"])
    erros: list[str] = []
    ativos_por_split: dict[str, set[str]] = {"dev": set(), "test": set()}
    composicao: dict[tuple[str, str], int] = {}
    inviaveis: list[str] = []
    cenarios = sorted(p for p in CENARIOS.glob("*.yaml") if not p.name.startswith("_"))

    for caminho in cenarios:
        c = yaml.safe_load(caminho.read_text())
        cid = c.get("id", caminho.name)
        falta = [k for k in OBRIGATORIOS if k not in c]
        if falta:
            erros.append(f"{cid}: campos ausentes {falta}")
            continue
        if caminho.stem != cid:
            erros.append(f"{cid}: nome de arquivo ({caminho.stem}) diverge do id")
        if c["split"] not in ("dev", "test"):
            erros.append(f"{cid}: split inválido {c['split']!r}")
        if c["natureza"] not in ("dado_dependente", "politica_dependente"):
            erros.append(f"{cid}: natureza inválida {c['natureza']!r}")

        status = c.get("status", STATUS_PADRAO)
        if status not in STATUS_VALIDOS:
            erros.append(
                f"{cid}: status inválido {status!r}, esperado um de {list(STATUS_VALIDOS)}"
            )
        inviavel = status == "inviavel"
        # A justificativa é o que separa "declarar inviável" de "esconder cenário que não passou".
        justificativa = str(c.get("justificativa_inviabilidade") or "").strip()
        if inviavel and not justificativa:
            erros.append(f"{cid}: status inviavel exige justificativa_inviabilidade não vazia")
        if not inviavel and justificativa:
            erros.append(f"{cid}: justificativa_inviabilidade sem status inviavel")
        if inviavel:
            inviaveis.append(cid)

        g = c["gabarito"]
        for k in GABARITO_OBRIGATORIOS:
            if k not in g:
                erros.append(f"{cid}: gabarito sem {k}")

        for ev in g.get("evidencias_obrigatorias", []):
            if ev in EVIDENCIAS_SEM_CAMPO:
                continue
            recurso, _, campo = ev.partition(".")
            if recurso not in CAMPOS_POR_RECURSO:
                erros.append(f"{cid}: evidência com recurso desconhecido {ev!r}")
            elif campo not in CAMPOS_POR_RECURSO[recurso]:
                erros.append(f"{cid}: evidência {ev!r} não existe no payload de {recurso}")

        citadas = set(g.get("tools_esperadas", [])) | set(g.get("tools_aceitaveis", []))
        citadas |= set(g.get("proibido", [])) | set(g.get("args_esperados", {}))
        desconhecidas = sorted(citadas - tools)
        if desconhecidas:
            erros.append(f"{cid}: tools fora do catálogo {desconhecidas}")

        decisao = g.get("decisao_esperada", "")
        if not str(decisao).startswith("regra:"):
            erros.append(f"{cid}: decisao_esperada deve ser nome de regra, veio {decisao!r}")
        elif decisao.removeprefix("regra:") not in regras:
            erros.append(f"{cid}: regra desconhecida {decisao!r}")
        for ramo in g.get("ramos", []):
            r = str(ramo.get("decisao", ""))
            if not r.startswith("regra:") or r.removeprefix("regra:") not in regras:
                erros.append(f"{cid}: ramo com regra desconhecida {r!r}")

        if c["procedencia"] not in ("autoral", "oficial"):
            erros.append(f"{cid}: procedência inválida {c['procedencia']!r}")

        # Daqui para baixo é o corpus executável. Um cenário inviável continua no repositório
        # como registro — ele foi declarado, não apagado —, mas não é rodado, então cobrar dele
        # ambiente satisfazível, split e isolamento de ativo mediria algo que nunca executa.
        if inviavel:
            continue

        seed = c["ambiente"]["env_seed"]
        for exigido in c["ambiente"].get("modos_exigidos", []):
            obtido = resolve_mode(exigido["recurso"], exigido["categoria"], seed)
            if obtido not in exigido["modos"]:
                erros.append(
                    f"{cid}: seed {seed} dá {exigido['recurso']}/{exigido['categoria']}="
                    f"{obtido}, esperado {exigido['modos']}"
                )

        ativos = {c["asset_id"]} if c.get("asset_id") else set()
        ativos |= {a["id"] for a in (c.get("contexto", {}).get("candidatos") or [])}
        ativos_por_split[c["split"]] |= ativos
        chave = (c["split"], c["procedencia"])
        composicao[chave] = composicao.get(chave, 0) + 1

    sobreposicao = ativos_por_split["dev"] & ativos_por_split["test"]
    if sobreposicao:
        erros.append(f"vazamento dev/test: ativos em comum {sorted(sobreposicao)}")

    # Tamanho do split e presença das duas procedências dos dois lados: procedência é
    # variável controlada, não confundidora. Se `oficial` ficasse só num lado, qualquer
    # diferença dev × test seria inseparável da diferença de quem escreveu o gabarito.
    for split, esperado in SPLIT_ESPERADO.items():
        obtido = sum(n for (s, _), n in composicao.items() if s == split)
        if obtido != esperado:
            erros.append(
                f"split {split}: {obtido} cenários executáveis, esperado {esperado}"
                + (
                    f" — {len(inviaveis)} declarado(s) inviável(is) ({', '.join(inviaveis)}). "
                    "Declarar inviável muda o denominador das baterias: atualize SPLIT_ESPERADO "
                    "aqui e as contagens de PLANO §baterias e METRICAS antes de rodar."
                    if inviaveis
                    else ""
                )
            )
        for proc in ("autoral", "oficial"):
            if composicao.get((split, proc), 0) == 0:
                erros.append(f"split {split}: nenhum cenário de procedência {proc}")

    executaveis = len(cenarios) - len(inviaveis)
    print(
        f"{len(cenarios)} cenários ({executaveis} executáveis) · "
        f"{len(tools)} tools no catálogo · {len(regras)} regras"
    )
    if inviaveis:
        print(f"inviáveis (fora das baterias): {', '.join(inviaveis)}")
    for split in ("dev", "test"):
        partes = " + ".join(
            f"{composicao.get((split, p), 0)} {p}" for p in ("autoral", "oficial")
        )
        print(f"{split}: {partes} · ativos {sorted(ativos_por_split[split])}")
    if erros:
        print("\nFALHAS:")
        for e in erros:
            print(f"  - {e}")
        return 1
    print("\nOK — corpus válido.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
