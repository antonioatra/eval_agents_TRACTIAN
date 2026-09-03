"""
O servidor da consulta ao vivo — `make copiloto`.

BIBLIOTECA PADRÃO, E NÃO FASTAPI
    `http.server` basta para um servidor local, de um usuário, com quatro rotas. FastAPI
    entraria como dependência nova de um projeto cujo argumento inteiro é orçamento zero, e
    entraria para resolver um problema que não existe aqui: não há autenticação, não há
    concorrência real (a GPU aceita uma run por vez) e o schema das quatro respostas cabe nesta
    docstring. `starlette` e `uvicorn` estão instalados no ambiente — mas como dependência
    TRANSITIVA do `mcp`, e construir sobre isso é depender de um detalhe de outro pacote.

UMA CONSULTA POR VEZ, POR MEDIÇÃO E NÃO POR CAUTELA
    A piloto mediu: com `--paralelismo 2` as mesmas duas células levaram 446 s contra 222 s, e
    uma run se perdeu em `falha_do_instrumento` — sob contenção de GPU cada chamada passa dos
    300 s do cliente de inferência e a contenção vira run perdida em vez de run lenta. O
    servidor recusa a segunda consulta enquanto a primeira corre, e diz por quê.

A PÁGINA É A MESMA, O MODO É QUE MUDA
    `GET /` devolve o html de `tapieval.app` com um campo a mais no payload: `ao_vivo`. É a
    única diferença entre o que este servidor serve e o que `make app` grava em disco. A página
    lê esse campo e, só quando ele existe, o botão "fazer uma nova pergunta" passa a chamar o
    agente em vez de casar a pergunta com a execução gravada mais próxima.

    Duas páginas — uma "de demonstração" e outra "de verdade" — divergiriam na semana da
    entrega, e a que fosse projetada seria a errada.

O TRACE É LIDO DO DISCO, NÃO GUARDADO EM MEMÓRIA
    `TraceWriter` abre, escreve e fecha o arquivo a cada evento, justamente para que uma run
    que morre deixe o trace completo até o último evento. Então acompanhar a execução é reler o
    `.jsonl` — e o que a página desenha ao vivo é exatamente o mesmo arquivo que ela desenharia
    amanhã. Um espelho em memória seria uma segunda verdade, e a divergência entre as duas só
    apareceria na run que interessa: a que quebrou.
"""

from __future__ import annotations

import json
import threading
import traceback
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from tapieval.app import gerar as gerador
from tapieval.app import texto as tx
from tapieval.app import vista
from tapieval.runner.matriz import API_BASE_URL_PADRAO, INFERENCIA_BASE_URL_PADRAO
from tapieval.schema.reader import read_trace
from tapieval.scoring import sem_gabarito
from tapieval.scoring.severidade import CATALOGO_DE_FALHAS
from tapieval.vivo import pergunta as pg

PORTA_PADRAO = 7000

LIMITE_DA_PERGUNTA = 2000
"""Caracteres. A janela do modelo é de 16k tokens e o prompt de sistema já ocupa parte dela:
uma pergunta de dez mil caracteres não seria respondida, seria truncada em silêncio."""


class ErroDoServidor(RuntimeError):
    """Pré-condição da consulta ao vivo que não está de pé."""


@dataclass
class Execucao:
    """Uma consulta em voo ou terminada. O que o servidor guarda entre duas requisições."""

    consulta: pg.Consulta
    status: str = "rodando"
    """`rodando` até a thread fechar; depois, o status que o runner devolveu — `ok`,
    `budget_exceeded`, `timeout`, `error` ou `falha_do_instrumento`."""

    erro: str | None = None


@dataclass
class Estado:
    """O servidor inteiro. Uma instância por processo."""

    raiz: Path
    api_base_url: str
    inferencia_base_url: str
    pagina: str
    execucoes: dict[str, Execucao] = field(default_factory=dict)
    em_voo: str | None = None
    trava: threading.Lock = field(default_factory=threading.Lock)


# ---------------------------------------------------------------------------
# As pré-condições, conferidas ANTES de a página abrir
# ---------------------------------------------------------------------------


def conferir_precondicoes(*, api_base_url: str, inferencia_base_url: str) -> list[str]:
    """Os dois serviços que a consulta ao vivo exige, cada um com o comando que o levanta.

    Conferido no boot e não na primeira pergunta: descobrir que o LM Studio está fora do ar
    depois de digitar a pergunta na frente de alguém é o pior momento possível, e é o momento
    em que se descobriria se ninguém conferisse antes.
    """
    problemas: list[str] = []
    try:
        httpx.get(f"{api_base_url}/openapi.json", timeout=3.0).raise_for_status()
    except Exception:  # noqa: BLE001 — qualquer falha aqui é a mesma falha para quem lê
        problemas.append(
            f"a API do parceiro não responde em {api_base_url} — suba com `make api`"
        )
    try:
        httpx.get(f"{inferencia_base_url}/models", timeout=3.0).raise_for_status()
    except Exception:  # noqa: BLE001
        problemas.append(
            f"o servidor de inferência não responde em {inferencia_base_url} — abra o LM "
            "Studio e carregue `qwen3-8b-mlx` (`lms load qwen3-8b-mlx --context-length "
            "16384 --parallel 1 --gpu max -y`)"
        )
    return problemas


# ---------------------------------------------------------------------------
# As rotas
# ---------------------------------------------------------------------------


def mundo(estado: Estado, user_id: str) -> dict:
    """Quem é o usuário e que ativos ele enxerga — lido da API **agora**, não do corpus.

    É o que faz a consulta ser uma consulta: a lista de ativos que aparece no formulário é a
    que a plataforma devolve neste instante para este usuário, com a criticidade e o status de
    sensor que ela está reportando. Uma lista fixa no código seria cenário, não mundo.
    """
    with httpx.Client(base_url=estado.api_base_url, timeout=10.0) as cliente:
        cabecalho = {"x-user-id": user_id}
        eu = cliente.get("/users/me", headers=cabecalho)
        eu.raise_for_status()
        perfil = eu.json()

        ativos = cliente.get(
            f"/companies/{perfil['company_id']}/assets",
            headers=cabecalho,
            params={"seed": pg.ENV_SEED_PADRAO},
        )
        ativos.raise_for_status()
        corpo = ativos.json()

    return {
        "usuario": perfil,
        # O envelope probabilístico (`mode`, `notes`) sobe junto de propósito: a lista de
        # ativos pode vir `partial`, e a tela que a mostra tem de poder dizer isso. É a mesma
        # informação que o agente recebe, e escondê-la aqui daria ao operador uma visão do
        # mundo melhor do que a do agente que ele está avaliando.
        "modo": corpo.get("mode"),
        "notas": corpo.get("notes"),
        "ativos": [
            {
                "id": ativo["id"],
                "nome": ativo.get("name"),
                "criticidade": ativo.get("criticality"),
                "linha": ativo.get("line"),
                "sensor": ativo.get("sensor_status"),
            }
            for ativo in (corpo.get("data") or {}).get("assets") or []
        ],
    }


def perguntar(estado: Estado, corpo: dict) -> dict:
    """Prepara a consulta, dispara a execução numa thread e devolve o `run_id` na hora."""
    texto = str(corpo.get("texto") or "")
    if len(texto) > LIMITE_DA_PERGUNTA:
        raise pg.ErroDeConsulta(
            f"a pergunta tem {len(texto)} caracteres e o limite é {LIMITE_DA_PERGUNTA} — "
            "acima disso ela não cabe na janela junto com o prompt de sistema"
        )

    with estado.trava:
        if estado.em_voo is not None:
            raise ErroDoServidor(
                "já existe uma consulta rodando. O servidor executa uma por vez porque a "
                "piloto mediu que duas em paralelo dobram o tempo e perdem uma run por "
                "contenção de GPU — espere esta terminar"
            )
        consulta = pg.preparar(
            texto,
            raiz=estado.raiz,
            user_id=str(corpo.get("user_id") or ""),
            asset_id=(corpo.get("asset_id") or None),
            modelo=str(corpo.get("modelo") or "qwen3-8b"),
            case_id=(corpo.get("case_id") or None),
        )
        estado.execucoes[consulta.run_id] = Execucao(consulta=consulta)
        estado.em_voo = consulta.run_id

    threading.Thread(
        target=_rodar, args=(estado, consulta), name=f"consulta-{consulta.id}", daemon=True
    ).start()

    return {
        "run_id": consulta.run_id,
        "cenario_id": consulta.id,
        "cenario_yaml": str(consulta.caminho.relative_to(estado.raiz)),
        "modelo": consulta.modelo,
        "texto": consulta.texto,
        "user_id": consulta.user_id,
        "asset_id": consulta.asset_id,
    }


def _rodar(estado: Estado, consulta: pg.Consulta) -> None:
    """O corpo da thread. Fecha `em_voo` no `finally` — uma exceção aqui travaria o servidor."""
    try:
        status = pg.executar(consulta, raiz=estado.raiz)
        estado.execucoes[consulta.run_id].status = status
    except Exception:  # noqa: BLE001 — `executar_celula` promete não levantar; se levantar, aparece
        estado.execucoes[consulta.run_id].status = "falha_do_instrumento"
        estado.execucoes[consulta.run_id].erro = traceback.format_exc(limit=6)
    finally:
        with estado.trava:
            estado.em_voo = None


def execucao(estado: Estado, run_id: str) -> dict:
    """O estado atual da consulta: os passos que já chegaram, a resposta e a medição possível.

    Chamado em laço pela página enquanto a run corre. Lê o trace do disco a cada chamada — ver
    a docstring do módulo para o porquê de não haver espelho em memória.
    """
    registro = estado.execucoes.get(run_id)
    if registro is None:
        raise ErroDoServidor(f"não conheço a execução {run_id!r}")

    consulta = registro.consulta
    passos: list[dict] = []
    resposta: str | None = None
    citacoes: list[str] = []
    medicao: dict | None = None
    duracao_ms: int | None = None

    if consulta.trace.exists():
        cru = [
            json.loads(linha)
            for linha in consulta.trace.read_text(encoding="utf-8").splitlines()
            if linha.strip()
        ]
        cru.sort(key=lambda e: e.get("seq", 0))
        passos = vista.passos_do_trace(cru, consulta.blobs)

        final = next((e for e in cru if e["type"] == "final_answer"), None)
        if final is not None:
            resposta = final.get("texto")
            citacoes = list(final.get("citacoes") or ())
        fim = next((e for e in reversed(cru) if e["type"] == "run_end"), None)
        if fim is not None:
            duracao_ms = fim.get("duracao_ms")

        # A medição roda mesmo com a run em voo: `sem_gabarito.medir` aceita trace incompleto
        # de propósito, e ver o `D1` acender no instante da escrita indevida é metade do que
        # esta tela tem para mostrar. `read_trace` pode falhar numa linha sendo escrita agora —
        # aí a medição desta volta simplesmente não vem, e a próxima traz.
        try:
            medicao = _medicao(read_trace(consulta.trace))
        except (ValueError, OSError):
            medicao = None

    return {
        "run_id": run_id,
        "status": registro.status,
        "erro": registro.erro,
        "texto": consulta.texto,
        "user_id": consulta.user_id,
        "asset_id": consulta.asset_id,
        "modelo": consulta.modelo,
        "passos": passos,
        "resposta": resposta,
        "citacoes": citacoes,
        "duracao_ms": duracao_ms,
        "medicao": medicao,
    }


@dataclass(frozen=True)
class _N2DoTrace:
    """Os três campos de N2 que as frases dos quatro códigos do trace leem — e nada além.

    `tx.explicar` recebe `(n1, n2)` para montar a frase com o número daquela execução: "estourou
    o limite de passos", "repetiu 2 consultas", "em 3 passos a saída veio malformada". Os quatro
    códigos que esta camada emite leem exatamente estes três campos, e `D1` e `C5` não leem
    nenhum.

    Passar um `N2Programatico` inteiro com o resto em valor neutro seria a mentira por
    neutralidade que `sem_gabarito` existe para evitar, e reescrever as frases aqui criaria uma
    segunda versão delas para divergir da primeira. Um objeto com só o que é lido resolve os
    dois: o que não foi medido não está presente para ser lido por engano.

    `test_a_frase_do_engenheiro_cobre_os_quatro_codigos_do_trace` prende a correspondência — um
    código novo em `CODIGOS_DO_TRACE` cuja frase pedisse outro campo estoura ali, e não na tela.
    """

    estourou_budget: bool
    n_redundantes: int
    parse_failures: int


def _medicao(eventos) -> dict:
    """A medição sem gabarito, nos dois registros — a frase do engenheiro e o código."""
    medida = sem_gabarito.medir(eventos)
    n2 = _N2DoTrace(
        estourou_budget=medida.estourou_budget,
        n_redundantes=medida.n_redundantes,
        parse_failures=medida.parse_failures,
    )
    return {
        "decisao_observada": medida.decisao_observada,
        "gate_respeitado": medida.gate_respeitado,
        "citacoes_validas": medida.citacoes_validas,
        "n_redundantes": medida.n_redundantes,
        "estourou_budget": medida.estourou_budget,
        "parse_failures": medida.parse_failures,
        "d1_parcial": medida.d1_parcial,
        "ressalva_do_d1": sem_gabarito.RESSALVA_DO_D1,
        "falhas": [
            {
                "codigo": f.codigo,
                "severidade": f.severidade,
                "gravidade": tx.gravidade(f.severidade),
                "humano": tx.explicar(f.codigo, None, n2),
                "descricao": f.descricao,
                "camada": f.detectada_por,
                "evidencia": f.evidencia,
            }
            for f in medida.falhas
        ],
        "codigos_do_trace": [
            {
                "codigo": codigo,
                "severidade": CATALOGO_DE_FALHAS[codigo].severidade,
                "descricao": CATALOGO_DE_FALHAS[codigo].descricao,
                "camada": CATALOGO_DE_FALHAS[codigo].detectada_por,
                "disparou": any(f.codigo == codigo for f in medida.falhas),
            }
            for codigo in sem_gabarito.CODIGOS_DO_TRACE
        ],
        "nao_medidos": [
            {
                "codigo": c.codigo,
                "severidade": c.severidade,
                "descricao": c.descricao,
                "motivo": c.motivo,
            }
            for c in medida.nao_medidos
        ],
    }


# ---------------------------------------------------------------------------
# O HTTP
# ---------------------------------------------------------------------------


def montar_pagina(
    raiz: Path, *, bateria: str = gerador.BATERIA_PADRAO, usuarios: list[str] | None = None
) -> str:
    """A mesma página do `make app`, com `ao_vivo` no payload.

    Levanta se a bateria gravada não existir: a consulta ao vivo é UM caso: sem o histórico
    medido ao lado dele, a tela perderia a comparação que dá sentido ao que ela mostra.
    """
    dados = vista.montar(raiz, bateria=bateria, modelos=gerador.MODELOS_PADRAO)
    dados["placar"] = vista.carregar_placar(raiz)
    dados["ao_vivo"] = {
        "modelos": sorted(pg.MODELOS),
        "usuarios": usuarios if usuarios is not None else usuarios_do_corpus(raiz),
        "env_seed": pg.ENV_SEED_PADRAO,
        "limite_da_pergunta": LIMITE_DA_PERGUNTA,
        "codigos_do_trace": list(sem_gabarito.CODIGOS_DO_TRACE),
        "n_nao_medidos": len(sem_gabarito.MOTIVO_DE_NAO_MEDIR),
    }
    return gerador.montar_html(dados)


def usuarios_do_corpus(raiz: Path) -> list[str]:
    """Os `user_id` que o corpus usa, lidos dos YAMLs. Não há `GET /users` no contrato.

    Do corpus e não de uma lista no código: são usuários reais da API do parceiro, com empresa
    e permissões diferentes entre si, e é essa variedade que faz a consulta ao vivo poder
    mostrar um `403` de verdade em vez de um caminho feliz ensaiado.
    """
    import yaml

    achados: set[str] = set()
    for caminho in sorted((raiz / "scenarios").glob("*.yaml")):
        if caminho.name.startswith("_"):
            continue
        documento = yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}
        if isinstance(documento.get("user_id"), str):
            achados.add(documento["user_id"])
    return sorted(achados)


class _Handler(BaseHTTPRequestHandler):
    estado: Estado

    protocol_version = "HTTP/1.1"
    server_version = "tapieval-vivo"

    def do_GET(self) -> None:  # noqa: N802 — assinatura de `BaseHTTPRequestHandler`
        rota = urlparse(self.path)
        consulta = parse_qs(rota.query)
        try:
            if rota.path in ("/", "/index.html"):
                return self._html(self.estado.pagina)
            if rota.path == "/api/mundo":
                return self._json(mundo(self.estado, consulta["user_id"][0]))
            if rota.path == "/api/execucao":
                return self._json(execucao(self.estado, consulta["run_id"][0]))
            return self._erro(404, f"rota desconhecida: {rota.path}")
        except (KeyError, IndexError) as erro:
            self._erro(400, f"falta o parâmetro {erro}")
        except (ErroDoServidor, pg.ErroDeConsulta) as erro:
            self._erro(400, str(erro))
        except httpx.HTTPError as erro:
            self._erro(502, f"a API do parceiro respondeu mal: {erro}")

    def do_POST(self) -> None:  # noqa: N802
        rota = urlparse(self.path)
        try:
            tamanho = int(self.headers.get("content-length") or 0)
            corpo = json.loads(self.rfile.read(tamanho) or b"{}")
            if rota.path == "/api/perguntar":
                return self._json(perguntar(self.estado, corpo))
            return self._erro(404, f"rota desconhecida: {rota.path}")
        except json.JSONDecodeError as erro:
            self._erro(400, f"corpo não é JSON: {erro}")
        except (ErroDoServidor, pg.ErroDeConsulta) as erro:
            self._erro(409, str(erro))

    def log_message(self, formato: str, *args: object) -> None:
        """Silencia o log de acesso: a página faz uma chamada por segundo enquanto a run corre,
        e o terminal do apresentador não pode virar uma cachoeira de `GET /api/execucao`."""

    def _html(self, corpo: str) -> None:
        dados = corpo.encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(dados)))
        self.send_header("cache-control", "no-store")
        self.end_headers()
        self.wfile.write(dados)

    def _json(self, corpo: dict) -> None:
        dados = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(dados)))
        self.send_header("cache-control", "no-store")
        self.end_headers()
        self.wfile.write(dados)

    def _erro(self, codigo: int, mensagem: str) -> None:
        dados = json.dumps({"erro": mensagem}, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)


def construir(
    raiz: Path,
    *,
    porta: int = PORTA_PADRAO,
    bateria: str = gerador.BATERIA_PADRAO,
    api_base_url: str = API_BASE_URL_PADRAO,
    inferencia_base_url: str = INFERENCIA_BASE_URL_PADRAO,
) -> tuple[ThreadingHTTPServer, Estado]:
    """O servidor montado e a página já gerada, sem ainda estar servindo.

    A página é montada no boot e não a cada `GET /`: são 2,2 MB lidos de `runs/` e de
    `docs/anexos/resultados/`, e refazê-los a cada refresh transformaria um F5 em três segundos
    de espera no meio da apresentação.
    """
    estado = Estado(
        raiz=raiz,
        api_base_url=api_base_url,
        inferencia_base_url=inferencia_base_url,
        pagina=montar_pagina(raiz, bateria=bateria),
    )
    handler = type("Handler", (_Handler,), {"estado": estado})
    return ThreadingHTTPServer(("127.0.0.1", porta), handler), estado


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        prog="python -m tapieval.vivo",
        description="Sobe o copiloto com consulta ao vivo — uma pergunta nova, executada.",
    )
    p.add_argument("--raiz", type=Path, default=Path.cwd())
    p.add_argument("--porta", type=int, default=PORTA_PADRAO)
    p.add_argument("--bateria", default=gerador.BATERIA_PADRAO)
    p.add_argument("--api", default=API_BASE_URL_PADRAO)
    p.add_argument("--inferencia", default=INFERENCIA_BASE_URL_PADRAO)
    p.add_argument(
        "--sem-conferir",
        action="store_true",
        help="sobe mesmo com API ou LM Studio fora do ar (só para inspecionar a página)",
    )
    a = p.parse_args(argv)

    problemas = (
        []
        if a.sem_conferir
        else conferir_precondicoes(
            api_base_url=a.api, inferencia_base_url=a.inferencia
        )
    )
    if problemas:
        print("a consulta ao vivo não pode subir:")
        for problema in problemas:
            print(f"  · {problema}")
        return 1

    servidor, _ = construir(
        a.raiz,
        porta=a.porta,
        bateria=a.bateria,
        api_base_url=a.api,
        inferencia_base_url=a.inferencia,
    )
    print(f"copiloto ao vivo em http://127.0.0.1:{a.porta}  (ctrl-c encerra)")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nencerrado")
    finally:
        servidor.server_close()
    return 0


__all__ = ["Estado", "conferir_precondicoes", "construir", "main", "montar_pagina"]
