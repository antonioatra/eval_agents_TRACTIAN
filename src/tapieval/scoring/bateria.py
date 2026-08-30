"""Pontuar uma bateria já gravada — do `manifest.json` aos `ScoreRecord` no disco.

POR QUE ISTO EXISTE, E POR QUE SÓ AGORA
    `ARQUITETURA §5`, decisão 1: **trace imutável, scores derivados**. Até 30/08 a derivação
    existia como função por run (`pontuar_n1`, `pontuar_n2`) e como laço dentro de
    `tests/test_repro.py::pontuar_replay` — um laço de teste, que ninguém pode chamar para
    pontuar as 288 células da bateria principal. O `--scorer v2` que o `PLANO` T24-26 manda
    rodar depois da madrugada não tinha implementação; pontuar seria improviso às 21h, com o
    resultado da bateria na mão. Este módulo é esse laço promovido a código de produção, e
    `pontuar_replay` passou a consumi-lo em vez de duplicá-lo.

O QUE ELE NÃO FAZ: N3
    A passagem aqui é **offline e determinística** — N1 e N2 são função pura de
    `(trace, gabarito)`, e `tests/test_repro.py` bloqueia socket para provar que continuam
    sendo. O judge é outro dia, outro custo e outra RPD (`configs/bateria_referencia.yaml`),
    e por isso é outra passagem: os registros saem daqui com `n3=None`, que a taxonomia lê
    como **não medido** e nunca como "limpo" (`severidade.classificar_falhas`). É o que dá o
    ponto N1+N2 da curva de H0 sem que a ausência do judge vire ausência de falha.

    `escrever_scores` **recusa** sobrescrever um `scores.jsonl` que já tem N3 dentro. Rodar
    esta passagem por engano depois da do judge apagaria julgamento que custou chamada de
    rede, e o arquivo voltaria ao estado anterior sem nenhum aviso.

O QUE ELE FAZ COM A RUN QUE NÃO DÁ PARA PONTUAR
    Três casos, e nenhum deles é omissão silenciosa — o mesmo princípio do A7:

    | caso | de onde vem | no resultado |
    |---|---|---|
    | célula declarada sem registro | `Manifesto.faltantes()` | `faltantes`, e o processo sai 1 |
    | `falha_do_instrumento` | defeito nosso, não resultado | `nao_pontuadas`, com o erro |
    | trace ilegível / vazio | exceção ao ler ou pontuar | `nao_pontuadas`, com a exceção |

    A run com `valida=false` (A7) **é pontuada**: N1 e N2 são funções sobre os eventos que
    existem, e o motivo do manifesto vai para `ScoreRecord.motivo_nao_pontuavel` com
    `pontuavel=False` — fora do denominador do `pass^k`, com o porquê escrito, em vez de
    fora da contagem sem deixar rastro.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time
from pathlib import Path

from tapieval.runner.manifesto import (
    CoordenadaDaCelula,
    RegistroDeRun,
    ler_manifesto,
)
from tapieval.schema.reader import read_trace
from tapieval.schema.trace import ScoreRecord, ScorerVersion, TraceEvent
from tapieval.scoring.gabarito import Cenario, carregar_cenarios
from tapieval.scoring.n1 import pontuar_n1
from tapieval.scoring.n2 import pontuar_n2
from tapieval.scoring.severidade import (
    CONGELADA_EM,
    Falha,
    classificar_falhas,
    motivo_nao_pontuavel,
    sha_da_taxonomia,
    sucesso_binario,
)
from tapieval.scoring.trajetoria import TrajetoriaDeReferencia, carregar_trajetorias

NOME_DOS_SCORES = "scores.jsonl"

SCORER_DETERMINISTICO = "n1n2+taxonomia"
"""O nome do instrumento desta passagem, e não `"v2"`.

`"v2"` é a versão da **rubrica do judge** (`METRICAS §7`, e é o que `judge_frozen.json`
congela). Carimbar `v2` num registro sem N3 diria que ele saiu daquele judge — e o
`ScorerVersion` dentro do score é justamente o que prova de qual instrumento o registro veio
(`runner/manifesto.py`, R4). O que produz estes números é N1 + N2 + a taxonomia congelada, e
é esse o nome que fica.
"""


@dataclass(frozen=True)
class RunNaoPontuada:
    """Uma run que existia e não virou `ScoreRecord`, com o motivo por escrito."""

    run_id: str
    motivo: str


@dataclass(frozen=True)
class PontuacaoDaBateria:
    """O resultado da passagem inteira: o que pontuou, o que não pontuou, o que faltou."""

    experiment_id: str
    diretorio: Path
    scores: tuple[ScoreRecord, ...]
    nao_pontuadas: tuple[RunNaoPontuada, ...] = ()
    faltantes: tuple[str, ...] = ()

    @property
    def completa(self) -> bool:
        """Toda célula declarada virou `ScoreRecord`.

        Bateria incompleta é reportada como incompleta (`PLANO` T24-26): quem lê a tabela
        precisa saber que o denominador não é o que o YAML declarou.
        """
        return not self.faltantes and not self.nao_pontuadas

    @property
    def pontuaveis(self) -> tuple[ScoreRecord, ...]:
        """Os registros que entram no denominador do `pass^k` (`METRICAS §6.5`)."""
        return tuple(score for score in self.scores if score.pontuavel)


def scorer_deterministico() -> ScorerVersion:
    """A identidade do instrumento desta passagem.

    O `sha256` é o da **taxonomia congelada** (`METRICAS §6`, congelada em 24/08) e não o de
    prompt+rubrica+few-shots, que é o que o campo guarda quando há judge: sem LLM não há
    prompt para assinar, e o que de fato decide o veredito aqui — quais códigos existem e que
    severidade cada um tem — é exatamente o que aquele sha assina. `judge_model=None` é o que
    `ScorerVersion` já documenta para scorer sem LLM.
    """
    return ScorerVersion(
        scorer_version=SCORER_DETERMINISTICO,
        sha256=sha_da_taxonomia(),
        judge_model=None,
        rubrica_sha=None,
        fewshot_ids=[],
        fewshot_origem=None,
        congelado_em=datetime.combine(CONGELADA_EM, time.min, tzinfo=UTC),
    )


def pontuar_run(
    coordenada: CoordenadaDaCelula,
    registro: RegistroDeRun,
    cenario: Cenario,
    trajetoria: TrajetoriaDeReferencia | None,
    *,
    experiment_id: str,
    eventos_do_trace: Sequence[TraceEvent],
    scorer: ScorerVersion,
    calculado_em: datetime,
) -> ScoreRecord:
    """Uma célula → um `ScoreRecord`. Sem I/O: o trace já vem lido.

    Manter a leitura de disco fora daqui é o que `trajetoria.py` já faz por `pontuar_n2` e
    pela mesma razão — o que se quer testar é a derivação, não o diretório de cenários.
    """
    n1 = pontuar_n1(eventos_do_trace, cenario)
    n2 = pontuar_n2(eventos_do_trace, cenario, trajetoria)

    # Dois caminhos para "não pontuável", e eles não se substituem. O do manifesto é o A7
    # (trace estruturalmente quebrado), que o runner descobriu ao fechar a run; o de
    # `severidade` é a decisão ausente, que só aparece ao pontuar. Quando os dois valem, o do
    # manifesto vem primeiro: o defeito estrutural é a causa, e a decisão ausente é sintoma.
    motivo = registro.motivo_nao_pontuavel or motivo_nao_pontuavel(n1)
    pontuavel = motivo is None

    falhas = classificar_falhas(n1, n2, None)
    passou = sucesso_binario(falhas) if pontuavel else False

    # DUAS DECISÕES DE CAMPO QUE VALE LER ANTES DE "CORRIGIR":
    #
    # `score_final` é o sucesso binário de `METRICAS §6.5` em float, e NÃO um composto
    # ponderado. Nenhum documento do projeto define ponderação — `severidade.py` diz que "a
    # ponderação é do agregador (T12)" e a T12 entregou fixtures e severidades, não um
    # agregador. Inventar pesos aqui poria na figura da banca um número que ninguém consegue
    # defender, e que discordaria do `sucesso_binario` ao lado dele no mesmo registro. O campo
    # fica redundante de propósito, e a redundância está declarada.
    #
    # `prioridade_revisao_humana` fica em 0.0 porque a fórmula de `METRICAS §5` (N4.2) precisa
    # de `judge_flipou` e `variancia_seeds` — as duas são propriedades de CONJUNTOS de runs, e
    # esta função vê uma. Calcular só as parcelas disponíveis daria um número com o nome da
    # fórmula e o valor de outra coisa. Além disso, aquela priorização vale só para a amostra
    # de melhoria, que a T22 já fechou.

    return ScoreRecord(
        run_id=coordenada.run_id,
        experiment_id=experiment_id,
        scenario_id=coordenada.scenario_id,
        split=coordenada.split,
        variant_id=coordenada.variant_id,
        model_key=coordenada.model_key,
        seed=coordenada.sample_seed,
        scorer=scorer,
        calculado_em=calculado_em,
        n1=n1,
        n2=n2,
        n3=None,
        score_final=float(passou),
        sucesso_binario=passou,
        pontuavel=pontuavel,
        motivo_nao_pontuavel=motivo,
        prioridade_revisao_humana=0.0,
    )


def pontuar_bateria(
    diretorio: Path,
    *,
    cenarios: Mapping[str, Cenario] | None = None,
    trajetorias: Mapping[str, TrajetoriaDeReferencia] | None = None,
    calculado_em: datetime | None = None,
) -> PontuacaoDaBateria:
    """Pontua N1 e N2 de todas as runs registradas de uma bateria já executada.

    A coordenada de cada run sai do **manifesto**, não do nome do arquivo: o `run_id` é
    `<cenario>--<modelo>--<variante>--envs<seed>--n<seed>` e partir a string por `--`
    funcionaria hoje e quebraria calado no dia em que um `scenario_id` tiver `--` no nome.
    O manifesto é a fonte de verdade da matriz (`ARQUITETURA §5`).

    Uma exceção ao ler ou pontuar UMA run não derruba a bateria: ela vira `RunNaoPontuada`
    com a mensagem, e as outras 287 seguem. Uma passagem que morre na primeira run ruim
    obrigaria a escolher entre consertar às 22h e não ter tabela nenhuma.
    """
    manifesto = ler_manifesto(diretorio)
    if manifesto is None:
        raise FileNotFoundError(
            f"{diretorio} não tem `manifest.json` — esta bateria nunca rodou, e pontuar o "
            "diretório vazio devolveria uma tabela de zero linha em vez de um erro"
        )

    cenarios = carregar_cenarios() if cenarios is None else cenarios
    trajetorias = carregar_trajetorias() if trajetorias is None else trajetorias
    scorer = scorer_deterministico()
    calculado_em = calculado_em or datetime.now(UTC)

    coordenadas = {celula.run_id: celula for celula in manifesto.celulas}
    scores: list[ScoreRecord] = []
    nao_pontuadas: list[RunNaoPontuada] = []

    for run_id in sorted(coordenadas):
        registro = manifesto.runs.get(run_id)
        if registro is None:
            continue  # célula faltante; contada abaixo, pelo manifesto
        if registro.status == "falha_do_instrumento":
            # Defeito nosso, não resultado do experimento — a mesma separação que o portão
            # entre baterias faz. Pontuar o que o harness não conseguiu completar poria
            # falha do instrumento na taxonomia como se fosse falha do agente.
            nao_pontuadas.append(
                RunNaoPontuada(
                    run_id, f"falha_do_instrumento: {registro.erro or 'sem exceção gravada'}"
                )
            )
            continue

        try:
            eventos = read_trace(diretorio / registro.trace)
            scores.append(
                pontuar_run(
                    coordenadas[run_id],
                    registro,
                    _cenario_de(cenarios, coordenadas[run_id]),
                    trajetorias.get(coordenadas[run_id].scenario_id),
                    experiment_id=manifesto.experiment_id,
                    eventos_do_trace=eventos,
                    scorer=scorer,
                    calculado_em=calculado_em,
                )
            )
        except Exception as erro:  # noqa: BLE001 - uma run ruim não derruba a bateria
            nao_pontuadas.append(
                RunNaoPontuada(run_id, f"{type(erro).__name__}: {erro}")
            )

    return PontuacaoDaBateria(
        experiment_id=manifesto.experiment_id,
        diretorio=diretorio,
        scores=tuple(scores),
        nao_pontuadas=tuple(nao_pontuadas),
        faltantes=tuple(celula.run_id for celula in manifesto.faltantes()),
    )


def _cenario_de(
    cenarios: Mapping[str, Cenario], coordenada: CoordenadaDaCelula
) -> Cenario:
    cenario = cenarios.get(coordenada.scenario_id)
    if cenario is None:
        raise KeyError(
            f"cenário {coordenada.scenario_id!r} não está no corpus carregado — o manifesto "
            "declara uma célula cujo gabarito não existe mais em `scenarios/`, e pontuar "
            "contra outro gabarito renomearia o experimento"
        )
    return cenario


def falhas_do_score(score: ScoreRecord) -> list[Falha]:
    """A taxonomia deste registro, recomputada dos campos que ele guarda.

    `ScoreRecord` não tem lista de falhas de propósito — falha é **derivada** de N1/N2/N3, e
    guardar a derivação ao lado da fonte cria duas verdades que divergem no dia em que a
    tabela de códigos mudar. Quem precisa das falhas (a taxonomia da T30) chama isto.
    """
    return classificar_falhas(score.n1, score.n2, score.n3)


# ---------------------------------------------------------------------------
# Disco
# ---------------------------------------------------------------------------


def caminho_dos_scores(diretorio: Path) -> Path:
    return diretorio / NOME_DOS_SCORES


def escrever_scores(
    pontuacao: PontuacaoDaBateria,
    diretorio: Path | None = None,
    *,
    sobrescrever_n3: bool = False,
) -> Path:
    """Grava um `ScoreRecord` por linha em `scores.jsonl`, ordenado por `run_id`.

    JSONL e não JSON porque a bateria principal tem 288 registros e o formato tem de aguentar
    ser lido linha a linha por notebook, `grep` e `jq` — e porque um arquivo truncado no meio
    perde a última linha em vez de ficar ilegível inteiro.

    **A recusa que este writer tem** é a que protege o judge: se o arquivo de destino já tem
    registro com `n3` preenchido e `sobrescrever_n3` é falso, ele levanta em vez de gravar.
    Esta passagem produz `n3=None`; rodá-la depois da passagem do judge apagaria julgamento
    que custou chamada de rede, e nada no arquivo resultante diria que isso aconteceu.
    """
    destino = caminho_dos_scores(diretorio or pontuacao.diretorio)
    if not sobrescrever_n3:
        _recusar_se_apagaria_n3(destino, pontuacao)

    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_suffix(".jsonl.tmp")
    with temporario.open("w", encoding="utf-8") as arquivo:
        for score in sorted(pontuacao.scores, key=lambda s: s.run_id):
            arquivo.write(score.model_dump_json() + "\n")
    temporario.replace(destino)
    return destino


def _recusar_se_apagaria_n3(destino: Path, pontuacao: PontuacaoDaBateria) -> None:
    if not destino.exists():
        return
    if any(score.n3 is not None for score in pontuacao.scores):
        return  # a passagem nova também traz judge; não há o que perder
    com_n3 = sum(1 for score in ler_scores(destino) if score.n3 is not None)
    if com_n3:
        raise ValueError(
            f"{destino} já tem {com_n3} registro(s) com N3, e esta passagem produz n3=None: "
            "gravar apagaria julgamento do judge sem deixar rastro. Rode com "
            "`--sobrescrever-n3` se é isso mesmo que se quer."
        )


def ler_scores(caminho: Path) -> list[ScoreRecord]:
    """Relê o `scores.jsonl`. Linha malformada é erro, pelo motivo do `read_trace`."""
    registros: list[ScoreRecord] = []
    for numero, linha in enumerate(
        caminho.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not linha.strip():
            continue
        try:
            registros.append(ScoreRecord.model_validate_json(linha))
        except Exception as erro:
            raise ValueError(f"{caminho}:{numero}: score ilegível — {erro}") from erro
    return registros


__all__ = [
    "NOME_DOS_SCORES",
    "SCORER_DETERMINISTICO",
    "PontuacaoDaBateria",
    "RunNaoPontuada",
    "caminho_dos_scores",
    "escrever_scores",
    "falhas_do_score",
    "ler_scores",
    "pontuar_bateria",
    "pontuar_run",
    "scorer_deterministico",
]
