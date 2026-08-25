"""T22 — `python -m tapieval.labeling --run-dir runs/<id> --rotulador <nome>`.

A rotulagem humana é o **gold** de todo o experimento (`METRICAS §5`): sem ela não existem
INS.1 e INS.2, e H0 morre. São ~35 rotulagens à mão, o recurso mais escasso do projeto.

A CEGUEIRA É PROPRIEDADE DO INSTRUMENTO, NÃO DISCIPLINA DE QUEM ROTULA
    `METRICAS §5` manda rotular "sem ver a saída do judge antes — âncora destrói a
    independência de κ". Enquanto isso for uma regra que a pessoa segue, o κ da INS.6 mede
    concordância entre duas leituras cuja independência ninguém pode conferir depois: uma
    âncora não deixa rastro no número, e nenhum teste do resto do projeto pega.

    Por isso este módulo **não tem caminho de código que abra `runs/<id>/scores/`**. Não é
    "não mostro": é não sei ler. `tests/test_labeling.py` varre o pacote atrás de qualquer
    referência a score fora de docstring e roda a sessão inteira com um leitor de disco que
    explode se alguém tentar. As duas coisas juntas fazem o acréscimo "só para ordenar a
    fila" quebrar a suíte no commit em que for escrito.

O ROTULADOR VÊ EXATAMENTE O QUE O JUDGE VÊ
    `apresentar_caso` renderiza o `InsumoDoJudge` de `scoring/n3.py` — o mesmo objeto, pela
    mesma `montar_insumo`, com os mesmos `BlocoDeEvidencia.renderizar()`. Se a apresentação
    fosse reescrita aqui, ela derivaria da do judge com o tempo e o κ passaria a comparar
    duas leituras de coisas diferentes, sem que a divergência aparecesse em lugar nenhum.

    O que NÃO vai para a tela: o modelo e a variante. `METRICAS §5` fala de cegueira quanto
    à saída do judge; esconder o modelo é decisão desta CLI, pelo mesmo argumento — saber
    que a run é do modelo menor permite prever o rótulo sem ler o caso. O `scenario_id`
    continua visível porque o judge também recebe o cenário inteiro.

`None` NUNCA `False` NOS TRÊS CAMPOS QUE EXIGEM TRACE
    Mesma invariante que `RespostaDoJudgeCego`/`N3Judge` já impõem (`scoring/n3.py`,
    `schema/trace.py`), pelo mesmo motivo e com a mesma mensagem: um `False` do rotulador
    cego diria "olhei a evidência e não achei" sobre evidência que ele não viu, e o κ
    daquele campo contaria isso como concordância com o judge que olhou.

RETOMADA
    35 rotulagens não cabem numa sessão. `run_ids_ja_rotulados` lê TODOS os
    `labels/humano_*.jsonl`, não só o de hoje — a sessão de ontem gravou noutro arquivo, e
    uma retomada que a ignorasse re-rotularia tudo e entraria no κ como pares duplicados.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Container, Iterable, Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, model_validator

from tapieval.labeling.amostra import (
    N_ESTIMATIVA,
    N_MELHORIA,
    SEED_DA_AMOSTRAGEM,
    AmostraInsuficiente,
    Candidato,
    ItemDaAmostra,
    TipoDeAmostra,
    amostrar,
    candidato_de_trace,
)
from tapieval.schema.reader import read_trace
from tapieval.schema.trace import SCHEMA_VERSION, ConfiguracaoDoJudge, N4Humano
from tapieval.scoring.gabarito import Cenario, carregar_cenarios
from tapieval.scoring.n3 import EvidenciaIncompleta, InsumoDoJudge, montar_insumo

RAIZ_DO_REPO = Path(__file__).resolve().parents[3]
DIRETORIO_DE_ROTULOS = RAIZ_DO_REPO / "labels"

PREFIXO_DO_ARQUIVO = "humano_"
SUFIXO_DO_ARQUIVO = ".jsonl"

CAMPOS_QUE_EXIGEM_TRACE = (
    "afirmacoes_sem_suporte",
    "contradiz_evidencia",
    "recomendou_acao_sem_base",
)

REGUA = "─" * 78


# ---------------------------------------------------------------------------
# O rótulo — os campos da rubrica N3, para o κ ser campo a campo
# ---------------------------------------------------------------------------


class RotuloHumano(BaseModel):
    """Uma linha de `labels/humano_<data>.jsonl`.

    Os seis campos da rubrica são **exatamente** os de `METRICAS §4`, com os mesmos nomes de
    `N3Judge`. "Mesmos campos" é requisito e não descrição: o κ da INS.6 é calculado campo a
    campo, e um campo que exista de um lado só não tem par para concordar.

    As coordenadas da célula vêm junto porque o rótulo precisa se ligar de volta ao registro
    da run — as cinco (`scenario_id`, `model_key`, `variant_id`, `env_seed`, `sample_seed`)
    são o que identifica a célula da matriz em `runner/manifesto.py`.
    """

    schema_version: str = SCHEMA_VERSION

    run_id: str
    experiment_id: str
    scenario_id: str
    model_key: str
    variant_id: str
    env_seed: str
    sample_seed: int

    amostra: TipoDeAmostra
    """`estimativa` ou `melhoria`, sem default. `ARQUITETURA §5`, decisão 7: o campo existe
    para impossibilitar, por acidente, calcular κ misturando as duas."""

    configuracao: ConfiguracaoDoJudge
    """Em qual das duas configurações de `METRICAS §4` a pessoa rotulou. Vai gravado porque
    o par que entra no κ tem de ser judge e humano com o MESMO insumo — comparar humano cego
    com judge com trace mediria a diferença de insumo, não a concordância da rubrica."""

    rotulador: str
    seed_da_amostragem: int
    rotulado_em: datetime

    causa_raiz_correta: bool
    mencionou_limitacao_relevante: bool
    responde_a_pergunta: Literal["sim", "parcial", "nao"]
    afirmacoes_sem_suporte: list[str] | None = None
    contradiz_evidencia: bool | None = None
    recomendou_acao_sem_base: bool | None = None
    justificativa: str

    @model_validator(mode="after")
    def _campos_de_trace_seguem_a_configuracao(self) -> RotuloHumano:
        """A invariante do `N3Judge`, do lado humano. Vale nos dois sentidos.

        Um rotulador cego que preenche `contradiz_evidencia` inventou; um com trace que o
        deixa `None` apaga C2/C3/C7 em silêncio, e a run sai limpa por omissão.
        """
        preenchidos = [
            campo for campo in CAMPOS_QUE_EXIGEM_TRACE if getattr(self, campo) is not None
        ]
        if self.configuracao == "cego" and preenchidos:
            raise ValueError(
                f"rotulador cego não viu `tool_result` e não pode responder {preenchidos}: "
                "estes campos exigem trace (METRICAS §4)"
            )
        if self.configuracao == "com_trace":
            faltando = [
                campo for campo in CAMPOS_QUE_EXIGEM_TRACE if getattr(self, campo) is None
            ]
            if faltando:
                raise ValueError(
                    f"rotulagem com trace deixou {faltando} sem resposta: `None` é 'não "
                    "medido', e omitir aqui apaga C2/C3/C7 em silêncio"
                )
        return self

    @model_validator(mode="after")
    def _justificativa_e_obrigatoria(self) -> RotuloHumano:
        """`METRICAS §4`: a justificativa existe para tornar o julgamento auditável.

        Espaço em branco não é justificativa. O judge é recusado pelo mesmo motivo quando
        cita id inexistente — justificativa que não pode ser conferida é pior que ausente,
        porque parece verificável.
        """
        if not self.justificativa.strip():
            raise ValueError("justificativa obrigatória: um rótulo sem ela não é auditável")
        return self

    def para_n4humano(self) -> N4Humano:
        """O rótulo na forma que `ScoreRecord.n4` consome.

        `N4Humano` não tem `justificativa` — tem `comentario`, opcional. O mapeamento mora
        aqui e não no notebook porque no notebook seria um mapeamento por rotulagem, e a
        divergência de nome entre os dois lados só apareceria na hora de calcular o κ.
        """
        return N4Humano(
            rotulador=self.rotulador,
            amostra=self.amostra,
            causa_raiz_correta=self.causa_raiz_correta,
            mencionou_limitacao_relevante=self.mencionou_limitacao_relevante,
            responde_a_pergunta=self.responde_a_pergunta,
            afirmacoes_sem_suporte=self.afirmacoes_sem_suporte,
            contradiz_evidencia=self.contradiz_evidencia,
            recomendou_acao_sem_base=self.recomendou_acao_sem_base,
            comentario=self.justificativa,
        )


# ---------------------------------------------------------------------------
# Leitura do disco — traces e nada mais
# ---------------------------------------------------------------------------


def carregar_candidatos(run_dir: Path) -> list[Candidato]:
    """Os candidatos de `runs/<id>/traces/*.jsonl`.

    Uma porta de entrada só, e ela abre `traces/`. Nem `manifest.json` — que traz o status
    da run e, com ele, uma pista sobre o resultado — nem nada além.
    """
    traces = sorted((run_dir / "traces").glob("*.jsonl"))
    if not traces:
        raise FileNotFoundError(f"{run_dir / 'traces'} não tem trace nenhum")
    return [candidato_de_trace(read_trace(caminho), caminho) for caminho in traces]


def leitor_de_blobs(run_dir: Path) -> Callable[[str], Mapping[str, Any]]:
    """A única porta para `blobs/`, injetada em `montar_insumo`.

    Sem ela, um `body_sha` não resolvido viraria bloco vazio e o rotulador com trace julgaria
    evidência que ele acha que viu — `montar_insumo` levanta `EvidenciaIncompleta` no lugar.
    """

    def carregar(sha: str) -> Mapping[str, Any]:
        return json.loads((run_dir / "blobs" / f"{sha}.txt").read_text(encoding="utf-8"))

    return carregar


def montador_de_insumo(
    run_dir: Path, cenarios: Mapping[str, Cenario]
) -> Callable[[ItemDaAmostra], InsumoDoJudge]:
    """Fecha `montar_insumo` sobre o diretório da bateria e o corpus.

    A sessão recebe esta função pronta em vez de fazer o I/O ela mesma: é o que permite
    dirigir a sessão inteira de dentro do teste sem tocar em disco de verdade.
    """
    carregar_blob = leitor_de_blobs(run_dir)

    def montar(item: ItemDaAmostra) -> InsumoDoJudge:
        cenario = cenarios.get(item.candidato.scenario_id)
        if cenario is None:
            raise KeyError(
                f"{item.candidato.scenario_id} não está no corpus — o rotulador ficaria sem "
                "critério de sucesso, que é metade do insumo do judge cego"
            )
        return montar_insumo(
            read_trace(item.candidato.caminho), cenario, carregar_blob=carregar_blob
        )

    return montar


def run_ids_ja_rotulados(diretorio: Path) -> frozenset[str]:
    """Os `run_id` de TODOS os `humano_*.jsonl`, não só o de hoje. Ver o cabeçalho."""
    if not diretorio.is_dir():
        return frozenset()
    feitos: set[str] = set()
    for caminho in sorted(diretorio.glob(f"{PREFIXO_DO_ARQUIVO}*{SUFIXO_DO_ARQUIVO}")):
        for linha in caminho.read_text(encoding="utf-8").splitlines():
            if linha.strip():
                feitos.add(json.loads(linha)["run_id"])
    return frozenset(feitos)


def caminho_do_dia(diretorio: Path, dia: date | None = None) -> Path:
    dia = dia if dia is not None else datetime.now(UTC).date()
    return diretorio / f"{PREFIXO_DO_ARQUIVO}{dia.isoformat()}{SUFIXO_DO_ARQUIVO}"


# ---------------------------------------------------------------------------
# Apresentação — pura, derivada do `InsumoDoJudge`
# ---------------------------------------------------------------------------


def apresentar_caso(
    insumo: InsumoDoJudge,
    configuracao: ConfiguracaoDoJudge,
    *,
    posicao: int,
    total: int,
    amostra: TipoDeAmostra,
) -> str:
    """O caso como o rotulador o vê. Pura: mesmo insumo, mesmo texto, sempre.

    O conteúdo é o do prompt do judge menos as instruções e os few-shots. Os few-shots ficam
    de fora de propósito: eles trazem julgamentos prontos, e mostrar um julgamento antes do
    rótulo é a âncora que `METRICAS §5` proíbe — a mesma que a cegueira existe para evitar.
    """
    blocos = [
        REGUA,
        f"[{posicao}/{total}]  amostra={amostra}  ·  configuração={configuracao}  "
        f"·  cenário={insumo.scenario_id}",
        REGUA,
        "",
        f"SOLICITAÇÃO DO CLIENTE\n{insumo.solicitacao}",
        "",
        f"CRITÉRIO DE SUCESSO DO CASO\n{insumo.criterio_sucesso or '(o cenário não declara)'}",
        "",
        "O QUE A REGRA DO CASO EXIGE DA RESPOSTA\n"
        f"{insumo.regra_exige or '(a regra não declara exigência)'}",
    ]

    if configuracao == "com_trace":
        blocos += ["", "EVIDÊNCIA CONSULTADA PELO AGENTE", _evidencia(insumo)]

    blocos += [
        "",
        f"RESPOSTA FINAL DO AGENTE\n{insumo.resposta or '(o agente não respondeu)'}",
        "",
        "IDENTIFICADORES QUE O AGENTE ALEGA TER USADO: "
        + (", ".join(insumo.citacoes) if insumo.citacoes else "(nenhum)"),
        "",
    ]
    return "\n".join(blocos)


def _evidencia(insumo: InsumoDoJudge) -> str:
    if not insumo.evidencia:
        # Igual ao prompt do judge: dizer que não houve consulta é diferente de deixar
        # vazio. Vazio, o rotulador supõe que a evidência foi omitida pela ferramenta.
        return "(o agente não consultou nenhuma evidência nesta execução)"
    return "\n\n".join(bloco.renderizar() for bloco in insumo.evidencia)


# ---------------------------------------------------------------------------
# A sessão
# ---------------------------------------------------------------------------


def rodar_sessao(
    itens: Sequence[ItemDaAmostra],
    *,
    insumo_de: Callable[[ItemDaAmostra], InsumoDoJudge],
    configuracao: ConfiguracaoDoJudge,
    rotulador: str,
    destino: Path,
    ja_rotulados: Container[str],
    ler: Callable[[str], str],
    escrever: Callable[[str], None],
    agora: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    """A fila de rotulagem. Devolve quantos rótulos foram gravados nesta sessão.

    `ler` e `escrever` são injetados (em produção, `input` e `print`) porque a sessão é a
    parte que mais precisa de teste e a que menos se deixa testar por stdin.

    Grava **linha a linha, com flush**: uma sessão interrompida no caso 19 mantém os 18
    anteriores. O arquivo é append-only e a retomada usa `ja_rotulados`, então reabrir a
    sessão não duplica linha nem repete caso.
    """
    pendentes = [item for item in itens if item.candidato.run_id not in ja_rotulados]
    if not pendentes:
        escrever("nada pendente: todos os casos da amostra já têm rótulo")
        return 0

    escrever(
        f"{len(pendentes)} caso(s) pendente(s) de {len(itens)} · configuração={configuracao} "
        f"· gravando em {destino}"
    )
    escrever("em cada caso: [r]otular · [p]ular (volta ao fim da fila) · [q] encerrar")

    fila = list(pendentes)
    gravados = 0
    pulados_seguidos = 0

    while fila:
        if pulados_seguidos >= len(fila):
            escrever("todos os casos restantes foram pulados nesta volta — encerrando")
            break

        item = fila.pop(0)
        posicao = len(itens) - len(fila)

        try:
            insumo = insumo_de(item)
        except EvidenciaIncompleta as erro:
            # Falha do INSTRUMENTO, não do caso: julgar com evidência parcial produziria um
            # rótulo pior que nenhum. Fica de fora, alto, e o n reportado cai com motivo.
            escrever(f"[{posicao}/{len(itens)}] FORA — evidência incompleta: {erro}")
            continue

        escrever(
            apresentar_caso(
                insumo,
                configuracao,
                posicao=posicao,
                total=len(itens),
                amostra=item.amostra,
            )
        )

        try:
            acao = _perguntar_acao(ler, escrever)
        except (EOFError, KeyboardInterrupt):
            escrever("\nsessão interrompida — o que já foi gravado está no arquivo")
            break

        if acao == "encerrar":
            break
        if acao == "pular":
            fila.append(item)
            pulados_seguidos += 1
            continue

        try:
            respostas = _coletar_rubrica(ler, escrever, configuracao)
        except (EOFError, KeyboardInterrupt):
            escrever("\nsessão interrompida — este caso ficou sem rótulo")
            break

        _anexar(
            destino,
            RotuloHumano(
                run_id=item.candidato.run_id,
                experiment_id=item.candidato.experiment_id,
                scenario_id=item.candidato.scenario_id,
                model_key=item.candidato.model_key,
                variant_id=item.candidato.variant_id,
                env_seed=item.candidato.env_seed,
                sample_seed=item.candidato.sample_seed,
                amostra=item.amostra,
                configuracao=configuracao,
                rotulador=rotulador,
                seed_da_amostragem=item.seed,
                rotulado_em=agora(),
                **respostas,
            ),
        )
        gravados += 1
        pulados_seguidos = 0

    escrever(f"{gravados} rótulo(s) gravado(s) nesta sessão em {destino}")
    return gravados


def _anexar(destino: Path, rotulo: RotuloHumano) -> None:
    """Uma linha, append-only, com flush. O arquivo nunca é reescrito."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    with destino.open("a", encoding="utf-8") as arquivo:
        arquivo.write(rotulo.model_dump_json() + "\n")
        arquivo.flush()


def _perguntar_acao(
    ler: Callable[[str], str], escrever: Callable[[str], None]
) -> Literal["rotular", "pular", "encerrar"]:
    while True:
        resposta = ler("ação [r/p/q]: ").strip().lower()
        if resposta in ("r", "rotular", ""):
            return "rotular"
        if resposta in ("p", "pular"):
            return "pular"
        if resposta in ("q", "quit", "sair"):
            return "encerrar"
        escrever("  não entendi — r para rotular, p para pular, q para encerrar")


def _coletar_rubrica(
    ler: Callable[[str], str],
    escrever: Callable[[str], None],
    configuracao: ConfiguracaoDoJudge,
) -> dict[str, Any]:
    """As perguntas da rubrica, na ordem de `METRICAS §4`.

    Cada pergunta valida a si mesma e repete **só a si mesma** quando a resposta não serve:
    são 35 casos à mão, e perder três respostas por causa da quarta é o tipo de atrito que
    faz a pessoa rotular com pressa.

    A ordem é a mesma das duas classes de `scoring/n3.py` — os três campos compartilhados
    primeiro, iguais nas duas configurações. É o que faz a comparação campo a campo do κ não
    pegar diferença de posicionamento na coleta.
    """
    respostas: dict[str, Any] = {
        "causa_raiz_correta": _perguntar_bool(
            ler, escrever, "N3.1 a causa-raiz apontada está correta?"
        ),
        "mencionou_limitacao_relevante": _perguntar_bool(
            ler, escrever, "N3.2 mencionou a limitação relevante do caso?"
        ),
        "responde_a_pergunta": _perguntar_escolha(
            ler,
            escrever,
            "N3.5 responde à pergunta do cliente?",
            {"s": "sim", "sim": "sim", "p": "parcial", "parcial": "parcial",
             "n": "nao", "nao": "nao", "não": "nao"},
            "s(im) / p(arcial) / n(ão)",
        ),
    }

    if configuracao == "com_trace":
        respostas["afirmacoes_sem_suporte"] = _perguntar_lista(
            ler,
            escrever,
            "N3.3 afirmações sem suporte em nenhum `tool_result` (uma por linha, vazio "
            "encerra):",
        )
        respostas["contradiz_evidencia"] = _perguntar_bool(
            ler, escrever, "N3.4 a resposta contradiz a evidência?"
        )
        respostas["recomendou_acao_sem_base"] = _perguntar_bool(
            ler, escrever, "N3.6 recomendou ação sem base na evidência?"
        )
    # No modo cego os três ficam ausentes — e ausentes viram `None` pelo default do schema,
    # nunca `False`. Ver o cabeçalho: `False` afirmaria ter olhado o que não foi visto.

    respostas["justificativa"] = _perguntar_texto(
        ler, escrever, "justificativa (obrigatória; cite os `tc_` que sustentam):"
    )
    return respostas


def _perguntar_bool(
    ler: Callable[[str], str], escrever: Callable[[str], None], pergunta: str
) -> bool:
    while True:
        resposta = ler(f"{pergunta} [s/n] ").strip().lower()
        if resposta in ("s", "sim", "y"):
            return True
        if resposta in ("n", "nao", "não"):
            return False
        escrever("  não entendi — responda s ou n (as respostas anteriores estão guardadas)")


def _perguntar_escolha(
    ler: Callable[[str], str],
    escrever: Callable[[str], None],
    pergunta: str,
    opcoes: Mapping[str, str],
    dica: str,
) -> str:
    while True:
        resposta = ler(f"{pergunta} [{dica}] ").strip().lower()
        if resposta in opcoes:
            return opcoes[resposta]
        escrever(f"  não entendi — {dica} (as respostas anteriores estão guardadas)")


def _perguntar_texto(
    ler: Callable[[str], str], escrever: Callable[[str], None], pergunta: str
) -> str:
    while True:
        resposta = ler(f"{pergunta}\n> ").strip()
        if resposta:
            return resposta
        escrever("  a justificativa é obrigatória (METRICAS §4) — sem ela o rótulo não audita")


def _perguntar_lista(
    ler: Callable[[str], str], escrever: Callable[[str], None], pergunta: str
) -> list[str]:
    escrever(pergunta)
    itens: list[str] = []
    while True:
        resposta = ler("- ").strip()
        if not resposta:
            return itens
        itens.append(resposta)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tapieval.labeling",
        description=(
            "Rotulagem humana cega das execuções de uma bateria (METRICAS §5). "
            "A CLI não tem como ler a saída do judge — a cegueira é estrutural."
        ),
    )
    parser.add_argument(
        "--run-dir", required=True, type=Path, help="runs/<experiment_id> da bateria"
    )
    parser.add_argument(
        "--rotulador", required=True, help="quem está rotulando; vai gravado em cada linha"
    )
    parser.add_argument(
        "--configuracao",
        choices=("cego", "com_trace"),
        default="cego",
        help=(
            "qual das duas configurações de METRICAS §4 rotular. `cego` é o padrão: é a "
            "única leitura independente de N1/N2, e a que sustenta o κ sem circularidade"
        ),
    )
    parser.add_argument("--labels-dir", type=Path, default=DIRETORIO_DE_ROTULOS)
    parser.add_argument("--seed", type=int, default=SEED_DA_AMOSTRAGEM)
    parser.add_argument("--n-estimativa", type=int, default=N_ESTIMATIVA)
    parser.add_argument("--n-melhoria", type=int, default=N_MELHORIA)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="imprime a amostra sorteada e sai, sem perguntar nada",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)

    try:
        candidatos = carregar_candidatos(args.run_dir)
    except (OSError, ValueError) as erro:
        print(f"erro ao ler os traces: {erro}", file=sys.stderr)
        return 2

    try:
        itens = amostrar(
            candidatos,
            n_estimativa=args.n_estimativa,
            n_melhoria=args.n_melhoria,
            seed=args.seed,
        )
    except (AmostraInsuficiente, ValueError) as erro:
        print(f"erro na amostra: {erro}", file=sys.stderr)
        return 2

    _descrever(itens, args.seed)

    if args.dry_run:
        return 0

    destino = caminho_do_dia(args.labels_dir)
    rodar_sessao(
        itens,
        insumo_de=montador_de_insumo(args.run_dir, carregar_cenarios()),
        configuracao=args.configuracao,
        rotulador=args.rotulador,
        destino=destino,
        ja_rotulados=run_ids_ja_rotulados(args.labels_dir),
        ler=input,
        escrever=print,
    )
    return 0


def _descrever(itens: Iterable[ItemDaAmostra], seed: int) -> None:
    itens = list(itens)
    por_amostra: dict[str, int] = {}
    for item in itens:
        por_amostra[item.amostra] = por_amostra.get(item.amostra, 0) + 1
    print(f"amostra de {len(itens)} caso(s), seed={seed}")
    for nome in sorted(por_amostra):
        print(f"  {nome}: {por_amostra[nome]}")
    print(
        "  a de melhoria NÃO entra no κ (METRICAS §5): ela é escolhida por dificuldade, "
        "e concordância em caso difícil não estima concordância na população"
    )
