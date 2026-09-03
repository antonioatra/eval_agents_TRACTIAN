#!/usr/bin/env python3
"""Portão da migração do judge para o Vertex (A1/T23) — o snapshot congelado existe lá?

POR QUE ESTE SCRIPT EXISTE ANTES DE QUALQUER LINHA DE MIGRAÇÃO
    O crédito de trial do Google Cloud não paga a Gemini API do AI Studio, mas paga o Vertex.
    Migrar o judge para lá tira o `limit: 20` diário do caminho (o Vertex serve os flash por
    dynamic shared quota, sem RPD pré-definido). Só que a T23 congela o judge por sha256, e o
    congelamento só vale se o id apontar para um modelo FIXO.

    Se o Vertex só oferecer alias móvel, o congelamento vira decorativo e a migração cai —
    não por dificuldade técnica, mas porque destruiria a propriedade que o judge existe para
    ter. Esta é a pergunta bloqueante, e ela se responde antes de mexer no `judge_llm.py`.

DUAS SONDAS, PORQUE ELAS RESPONDEM COISAS DIFERENTES
    1. **Metadados do publisher** (`publishers/google/models/{id}`): diz o que o catálogo
       DECLARA — `versionId`, `launchStage`, se é preview. É o que responde a pergunta da T23.
    2. **Chamada mínima** pelo endpoint OpenAI-compatible: diz o que o serviço ACEITA. O §6 do
       `docs/anexos/apuracao/limites_free_tier.md` fechou a questão da quota por modelo exatamente
       assim, e pelo mesmo motivo: o catálogo pode listar um modelo que a conta não pode chamar.

    As duas juntas separam "não existe" de "existe e você não tem acesso" — que pedem ações
    opostas do operador.

O ID É INCÓGNITA, E POR ISSO A LISTA DE CANDIDATOS
    No AI Studio o judge chama `gemini-3.6-flash`. A documentação do Vertex escreve a mesma
    família como `gemini-3-6-flash`, com hífen no lugar do ponto, e o endpoint compatível
    ainda prefixa o publisher (`google/...`). Nenhuma das três formas é óbvia o bastante para
    ser chutada, então todas são testadas — e a que responder é a que vale.

Uso:
    python scripts/checar_catalogo_vertex.py [--projeto ID] [--local us-central1]

Requer credencial de aplicação (ADC). Sem ela o script explica como obter e sai com 2.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass

import httpx

ESCOPO = "https://www.googleapis.com/auth/cloud-platform"


def _host(local: str) -> str:
    """`global` é servido pelo host sem prefixo; toda outra região leva o seu."""
    if local == "global":
        return "aiplatform.googleapis.com"
    return f"{local}-aiplatform.googleapis.com"

LOCAL_PADRAO = "global"
"""Medido em 25/08: os flash 3.x respondem em `global` e dão 404 em `us-central1` — inclusive
o 3.7 usado como controle. E `global` é a única região cujo host NÃO leva prefixo: é
`aiplatform.googleapis.com` puro. Montar `global-aiplatform...` produz 404 em tudo, que foi
exatamente o falso negativo da primeira passada desta sonda."""

CANDIDATOS = (
    "gemini-3.6-flash",
    "gemini-3-6-flash",
    "gemini-3.6-flash-07-2026",
    "gemini-3-6-flash-07-2026",
)
"""O primeiro é o id que a T23 congela hoje (`judge_llm.MODELO_PADRAO`). Os outros três são as
grafias que a documentação do Vertex e a convenção de snapshot datado tornam plausíveis. Testar
os quatro custa quatro chamadas; deduzir custa uma migração escrita contra o id errado."""

CANDIDATOS_DE_CONTROLE = ("gemini-3.7-flash", "gemini-3-7-flash")
"""Controle, não alternativa. Se NENHUM candidato responder, estes separam 'o 3.6 não está no
Vertex' de 'a conta/projeto não fala com o Vertex' — que é a diferença entre trocar de modelo e
consertar a configuração."""

PROMPT_MINIMO = "responda apenas: ok"


@dataclass(frozen=True)
class Sonda:
    """O que uma tentativa contra um id produziu. Guarda o corpo porque é nele que o Google
    escreve o motivo — foi um corpo de 429 que nomeou a quota em 24/08."""

    modelo: str
    metadados_status: int | None
    version_id: str | None
    launch_stage: str | None
    completacao_status: int | None
    detalhe: str


def _credencial() -> tuple[str, str | None]:
    """Devolve (token, projeto_da_credencial). Falha com instrução, não com stack trace."""
    try:
        import google.auth
        import google.auth.transport.requests
    except ModuleNotFoundError:
        print(
            "google-auth não está instalado.\n"
            "  .venv/bin/pip install google-auth\n",
            file=sys.stderr,
        )
        raise SystemExit(2) from None

    try:
        credencial, projeto = google.auth.default(scopes=[ESCOPO])
    except Exception as erro:  # noqa: BLE001 — a mensagem do google-auth É a instrução
        print(
            f"sem credencial de aplicação (ADC): {erro}\n\n"
            "Para obter:\n"
            "  brew install --cask google-cloud-sdk\n"
            "  gcloud auth application-default login\n"
            "  gcloud config set project SEU_PROJETO\n"
            "  gcloud services enable aiplatform.googleapis.com\n",
            file=sys.stderr,
        )
        raise SystemExit(2) from None

    credencial.refresh(google.auth.transport.requests.Request())
    return credencial.token, projeto


def _metadados(cliente: httpx.Client, local: str, projeto: str, modelo: str) -> Sonda:
    """Pergunta ao catálogo o que ele declara sobre o id."""
    caminhos = (
        f"/v1/publishers/google/models/{modelo}",
        f"/v1/projects/{projeto}/locations/{local}/publishers/google/models/{modelo}",
    )
    ultimo_status: int | None = None
    ultimo_corpo = ""
    for caminho in caminhos:
        resposta = cliente.get(caminho)
        ultimo_status, ultimo_corpo = resposta.status_code, resposta.text[:300]
        if resposta.status_code == 200:
            corpo = resposta.json()
            return Sonda(
                modelo=modelo,
                metadados_status=200,
                version_id=corpo.get("versionId"),
                launch_stage=corpo.get("launchStage"),
                completacao_status=None,
                detalhe=corpo.get("name", ""),
            )
    return Sonda(modelo, ultimo_status, None, None, None, ultimo_corpo)


def _completacao(cliente: httpx.Client, local: str, projeto: str, modelo: str) -> tuple[int, str]:
    """Uma chamada mínima pelo mesmo endpoint que o judge usaria. `max_tokens` baixo de
    propósito: a pergunta é 'aceita?', não 'responde bem?'."""
    base = f"/v1/projects/{projeto}/locations/{local}/endpoints/openapi/chat/completions"
    resposta = cliente.post(
        base,
        json={
            "model": f"google/{modelo}",
            "messages": [{"role": "user", "content": PROMPT_MINIMO}],
            "max_tokens": 8,
        },
    )
    return resposta.status_code, resposta.text[:300]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--projeto", default=os.environ.get("GOOGLE_CLOUD_PROJECT"))
    parser.add_argument("--local", default=LOCAL_PADRAO)
    parser.add_argument("--controle", action="store_true", help="testa também o 3.7-flash")
    args = parser.parse_args()

    token, projeto_da_credencial = _credencial()
    projeto = args.projeto or projeto_da_credencial
    if not projeto:
        print(
            "projeto não determinado. Passe --projeto ou:\n"
            "  gcloud config set project SEU_PROJETO\n",
            file=sys.stderr,
        )
        return 2

    print(f"projeto={projeto}  local={args.local}\n")

    alvos = list(CANDIDATOS) + (list(CANDIDATOS_DE_CONTROLE) if args.controle else [])
    sondas: list[Sonda] = []

    with httpx.Client(
        base_url=f"https://{_host(args.local)}",
        headers={
            "Authorization": f"Bearer {token}",
            # Sem este cabeçalho a ADC local leva 403 no catálogo: a API exige projeto de
            # quota, e a ADC não o carrega sozinha.
            "x-goog-user-project": projeto,
        },
        timeout=60.0,
    ) as cliente:
        for modelo in alvos:
            sonda = _metadados(cliente, args.local, projeto, modelo)
            status, detalhe = _completacao(cliente, args.local, projeto, modelo)
            sondas.append(
                Sonda(
                    modelo=sonda.modelo,
                    metadados_status=sonda.metadados_status,
                    version_id=sonda.version_id,
                    launch_stage=sonda.launch_stage,
                    completacao_status=status,
                    detalhe=sonda.detalhe if sonda.metadados_status == 200 else detalhe,
                )
            )

    largura = max(len(s.modelo) for s in sondas)
    print(f"{'modelo'.ljust(largura)}  meta  chamada  versionId       launchStage")
    print("-" * (largura + 46))
    for s in sondas:
        print(
            f"{s.modelo.ljust(largura)}  "
            f"{str(s.metadados_status or '-').ljust(4)}  "
            f"{str(s.completacao_status or '-').ljust(7)}  "
            f"{(s.version_id or '-').ljust(14)}  "
            f"{s.launch_stage or '-'}"
        )

    aceitos = [s for s in sondas if s.completacao_status == 200]
    print()
    if not aceitos:
        print("NENHUM candidato aceito. Detalhe da última tentativa:")
        print(f"  {sondas[-1].detalhe}")
        return 1

    print("Aceitos:", ", ".join(s.modelo for s in aceitos))
    print(
        "\nDecisão da T23: o id só serve se `versionId` apontar para snapshot fixo.\n"
        "Se o catálogo devolver alias móvel, o congelamento por sha256 vira decorativo."
    )
    caminho = "docs/anexos/resultados/catalogo_vertex.json"
    with open(caminho, "w", encoding="utf-8") as arquivo:
        json.dump([vars(s) for s in sondas], arquivo, ensure_ascii=False, indent=2)
    print(f"\nsondas gravadas em {caminho}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
