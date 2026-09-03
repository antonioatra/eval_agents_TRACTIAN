"""A vista do copiloto — trace + score + cenário virando o que a página desenha.

O QUE ESTA APLICAÇÃO É, E O QUE ELA NÃO É
    É a *"aplicação para inspeção de traces"* que o TAPI §6 lista como formato aceito de
    entregável da trilha de avaliação. Ela navega **execuções já medidas**: lê
    `runs/<bateria>/traces/*.jsonl`, `runs/<bateria>/scores.jsonl` e os YAMLs do corpus.

    **Ela não executa o agente, não fala com a API e não sobe modelo.** Isso não é limitação
    contornável mais tarde — é o desenho. Uma demonstração que depende de GPU e de endpoint no
    ar falha na hora em que ela precisa funcionar, e o dado gravado é o mesmo que sustenta as
    figuras do README. Quem quiser rodar o agente de verdade usa `tapieval` (o runner).

DUAS AUDIÊNCIAS, UM DADO
    A página tem dois registros sobre a mesma execução: o do **engenheiro de suporte**, em
    português (`app.texto`), e o da **avaliação**, com os códigos da taxonomia congelada. O
    segundo fica atrás de um botão. Os dois saem do mesmo `ScoreRecord` — não há resumo, não há
    arredondamento, não há uma versão "para mostrar".

A ARITMÉTICA NÃO MORA AQUI
    Mesma regra dos notebooks. As falhas vêm de `bateria.falhas_do_score`, os cortes de
    `taxonomia.severidades_que_reprovam`, o texto de `app.texto`. Este módulo agrupa e serializa.
    Um classificador próprio aqui descreveria um instrumento que ninguém usou.

POR QUE UM LEITOR DE YAML PRÓPRIO
    `gabarito.carregar_cenario` devolve a fatia que o **gabarito** consome, e ela de propósito
    não carrega título, permissões do usuário nem categoria — nada disso entra na pontuação.
    A página precisa exatamente desses campos e de nenhum dos outros. Reusar aquele carregador
    obrigaria a passar `regras` e ainda assim não traria o que falta, então aqui há um leitor
    pequeno e declarado, que lê os campos de **apresentação** e mais nada.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from tapieval.app import texto as tx
from tapieval.schema.trace import ScoreRecord
from tapieval.scoring.bateria import falhas_do_score, ler_scores
from tapieval.scoring.severidade import sucesso_binario, sucesso_binario_sem_s2

CODIGOS_DE_CONTEUDO_QUE_EXIGEM_JUDGE = ("C1", "C2", "C3", "C4", "C7")
"""Espelha `severidade.CODIGOS_QUE_EXIGEM_N3`, para a página poder dizer *"esta falha o caso
previa e esta bateria não teve como medir"*. `test_app.py` confere que os dois não divergem."""


class ErroDeVista(ValueError):
    """A bateria pedida não dá para montar sem produzir uma página que engana."""


@dataclass(frozen=True)
class Pergunta:
    """Os campos de **apresentação** de um cenário — o que a lista do histórico mostra."""

    id: str
    titulo: str
    solicitacao: str
    usuario: str
    ativo: str
    permissoes: tuple[str, ...]
    criticidade: str
    procedencia: str
    categoria: str
    criterio_sucesso: str
    falhas_alvo: tuple[str, ...]


def ler_pergunta(caminho: Path) -> Pergunta:
    """Lê os campos de apresentação de um YAML do corpus. Ver a docstring do módulo."""
    y = yaml.safe_load(caminho.read_text(encoding="utf-8"))
    contexto = y.get("contexto") or {}
    return Pergunta(
        id=y["id"],
        titulo=y.get("titulo") or y["id"],
        solicitacao=y.get("solicitacao") or "",
        usuario=y.get("user_id") or "",
        ativo=y.get("asset_id") or "",
        permissoes=tuple(contexto.get("permissoes_usuario") or ()),
        criticidade=contexto.get("criticidade_ativo") or "",
        procedencia=y.get("procedencia") or "",
        categoria=y.get("categoria") or "",
        criterio_sucesso=y.get("criterio_sucesso") or "",
        falhas_alvo=tuple(y.get("falhas_alvo") or ()),
    )


def _pensamento(blobs: Path, sha: str | None) -> str:
    """O campo `pensamento` do JSON que o agente devolve — o raciocínio declarado do passo.

    Blob ausente ou JSON malformado devolvem string vazia, e a página desenha *"o modelo não
    declarou raciocínio neste passo"*. É o caso do `parse_erro` (o X31), que é frequente: tratar
    isso como erro de leitura esconderia justamente a falha mais característica do 14B.
    """
    if not sha:
        return ""
    caminho = blobs / f"{sha}.txt"
    if not caminho.exists():
        return ""
    try:
        return (json.loads(caminho.read_text(encoding="utf-8")).get("pensamento") or "").strip()
    except (json.JSONDecodeError, OSError):
        return ""


def passos_do_trace(eventos: Sequence[Mapping], blobs: Path) -> list[dict]:
    """Os eventos do trace na ordem em que aconteceram, no vocabulário da página.

    A hidratação vira o "passo 0": é a camada MCP buscando contexto **antes** de o modelo ver
    qualquer coisa, e mostrá-la é metade do argumento de que a fronteira instrumentada existe.
    """
    passos: list[dict] = []
    for e in eventos:
        tipo, it = e["type"], e.get("iteration", 0)
        if tipo == "hydration":
            passos.append(
                {"it": 0, "tipo": "hydration", "endpoints": list(e.get("endpoints") or ()),
                 "ms": e.get("latencia_ms")}
            )
        elif tipo == "llm_call":
            passos.append(
                {"it": it, "tipo": "pensamento",
                 "texto": _pensamento(blobs, e.get("completion_sha")),
                 "tokens_in": e.get("prompt_tokens"), "tokens_out": e.get("completion_tokens"),
                 "ms": e.get("latencia_ms"), "parse_ok": e.get("parse_ok")}
            )
        elif tipo == "decision":
            passos.append(
                {"it": it, "tipo": "decisao", "modo": e.get("modo"), "decisao": e.get("decisao"),
                 "racional": (e.get("racional_declarado") or "").strip()}
            )
        elif tipo == "tool_call":
            passos.append(
                {"it": it, "tipo": "chamada", "tool": e["tool_name"], "args": e.get("args") or {}}
            )
        elif tipo == "tool_result":
            passos.append(
                {"it": it, "tipo": "resultado", "status": e.get("status"),
                 "http": e.get("http_status"), "ms": e.get("latencia_ms")}
            )
        elif tipo == "gate":
            passos.append(
                {"it": it, "tipo": "gate", "acao": e.get("acao"),
                 "veredito": e.get("veredito"), "motivo": e.get("motivo")}
            )
        elif tipo == "budget":
            passos.append(
                {"it": it, "tipo": "budget", "limite": e.get("limite"), "valor": e.get("valor")}
            )
        elif tipo == "error":
            passos.append({"it": it, "tipo": "erro", "texto": str(e.get("mensagem") or "")[:300]})
    return passos


def montar_tentativa(score: ScoreRecord, traces: Path, blobs: Path) -> dict:
    """Uma execução inteira, nos dois registros.

    `aprova` traz os três cortes e não só o oficial: a página do engenheiro não os usa, mas o
    modo avaliação os mostra lado a lado, e é vendo os três que se enxerga que o veredito depende
    de onde a linha foi traçada.
    """
    caminho = traces / f"{score.run_id}.jsonl"
    if not caminho.exists():
        raise ErroDeVista(f"score sem trace no disco: {caminho}")
    linhas = caminho.read_text(encoding="utf-8").splitlines()
    eventos = [json.loads(linha) for linha in linhas if linha.strip()]

    falhas = falhas_do_score(score)
    fim = next(e for e in reversed(eventos) if e["type"] == "run_end")
    resposta = next((e for e in eventos if e["type"] == "final_answer"), None)
    v = tx.veredito(falhas, pontuavel=score.pontuavel)

    return {
        "run_id": score.run_id,
        "seed": score.seed,
        "solicitacao": eventos[0].get("solicitacao"),
        "usuario": eventos[0].get("user_id"),
        "ativo": eventos[0].get("asset_id"),
        "resposta": resposta.get("texto") if resposta else None,
        "citacoes": list(resposta.get("citacoes") or ()) if resposta else [],
        "status_final": fim.get("status"),
        "duracao_ms": fim.get("duracao_ms"),
        "total_tool_calls": fim.get("total_tool_calls"),
        "total_llm_calls": fim.get("total_llm_calls"),
        "passos": passos_do_trace(eventos, blobs),
        "pontuavel": score.pontuavel,
        "motivo_nao_pontuavel": score.motivo_nao_pontuavel,
        "decisao_prevista": score.n1.decisao_prevista,
        "decisao_esperada": score.n1.decisao_esperada,
        "veredito_humano": {"tom": v.tom, "frase": v.frase},
        "falhas": [
            {
                "codigo": f.codigo,
                "severidade": f.severidade,
                "gravidade": tx.gravidade(f.severidade),
                "humano": tx.explicar(f.codigo, score.n1, score.n2),
                "descricao": f.descricao,
                "detectada_por": f.detectada_por,
                "evidencia": f.evidencia,
            }
            for f in falhas
        ],
        "aprova": {
            "S2": sucesso_binario(falhas),
            "S1": sucesso_binario_sem_s2(falhas),
            "S0": not any(f.severidade == "S0" for f in falhas),
        },
    }


def montar(
    raiz: Path,
    *,
    bateria: str,
    modelos: Mapping[str, str],
    cenarios: Path | None = None,
) -> dict:
    """A vista inteira: todas as perguntas do corpus que a bateria rodou, por modelo.

    Levanta se a bateria não tiver score nenhum — uma página vazia parece uma bateria em que
    nada falhou, que é a leitura mais errada possível.
    """
    diretorio = raiz / "runs" / bateria
    scores = ler_scores(diretorio / "scores.jsonl")
    if not scores:
        raise ErroDeVista(f"bateria {bateria!r} sem scores — a página sairia vazia")

    corpus = cenarios or (raiz / "scenarios")
    ids = sorted({s.scenario_id for s in scores})
    perguntas = {i: ler_pergunta(corpus / f"{i}.yaml") for i in ids}

    traces, blobs = diretorio / "traces", diretorio / "blobs"
    por_cenario: dict[str, dict[str, list[dict]]] = {}
    for cid in ids:
        por_cenario[cid] = {}
        for modelo in modelos:
            do_modelo = sorted(
                (s for s in scores if s.scenario_id == cid and s.model_key == modelo),
                key=lambda s: s.seed,
            )
            por_cenario[cid][modelo] = [montar_tentativa(s, traces, blobs) for s in do_modelo]

    return {
        "bateria": bateria,
        "cenarios": por_cenario,
        "rotulos": {
            "cenarios": {i: vars(p) | {"permissoes": list(p.permissoes),
                                       "falhas_alvo": list(p.falhas_alvo)}
                         for i, p in perguntas.items()},
            "modelos": dict(modelos),
        },
        "totais": {
            "execucoes": len(scores),
            "sem_decisao": sum(1 for s in scores if not s.pontuavel),
            "perguntas": len(ids),
        },
        "codigos_que_exigem_judge": list(CODIGOS_DE_CONTEUDO_QUE_EXIGEM_JUDGE),
    }


def carregar_placar(raiz: Path) -> list[dict]:
    """O placar dos modelos, lido de `docs/anexos/resultados/placar_modelos.json`.

    Fica em `docs/` e não aqui porque cada número dele tem dono: eles saem dos notebooks, via
    `resultados_h0.json`, `resultados_passk.json` e `resultados_taxonomia.json`. Recalcular aqui
    criaria uma segunda verdade que diverge no dia em que uma bateria for repontuada.
    """
    caminho = raiz / "docs" / "anexos" / "resultados" / "placar_modelos.json"
    if not caminho.exists():
        raise ErroDeVista(
            f"{caminho} não existe — o placar sai dos notebooks, não desta aplicação"
        )
    return json.loads(caminho.read_text(encoding="utf-8"))["criterios"]


def perguntas_do_corpus(vista: Mapping) -> Iterable[str]:
    """Os ids na ordem em que a página os lista. Existe para o teste não repetir a chave."""
    return vista["rotulos"]["cenarios"].keys()
