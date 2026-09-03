"""
Custo por camada de julgamento — o eixo x de H0.

H0 é uma curva custo × recall de detecção de falha (ARQUITETURA §12). O recall vem
do gold humano (N4.1); o custo vem daqui, e é INS.4 em METRICAS §7: segundos e
tokens para N1–N3, minutos de humano para N4. Sem estes números a figura principal
do trabalho não tem eixo x.

CUSTO É SCORE, NÃO TRACE
    O trace guarda fato bruto do que o agente fez; custo é resultado de medir uma
    AVALIAÇÃO daquele trace, e muda quando a rubrica muda sem que o agente rode de
    novo. Por isso mora ao lado dos scores, versionado pelo scorer:

        runs/<experiment_id>/scores/<scorer_version>/<run_id>.custo.jsonl

    Um arquivo por execução avaliada, uma linha por camada. JSONL append-only e não
    um JSON único porque as camadas são medidas em momentos e processos diferentes —
    N1/N2 no scorer, N3 no judge, N4 por um humano horas depois. Um JSON único
    exigiria ler-modificar-escrever, que é a corrida que este projeto já evita no
    trace (ARQUITETURA §5, decisão 1 e §4.3).

OS TOKENS VÊM DA ORIGEM, NÃO DO TRACE
    Os tokens do AGENTE estão no trace (`LLMCall.prompt_tokens`), mas os do JUDGE
    não estão em trace nenhum — e são justamente eles que separam os dois pontos da
    curva. Agregar do trace mediria o SUT, não o instrumento. Então quem chama o LLM
    reporta o que o servidor devolveu, via `MedidorDeCusto.registrar_llm`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from types import TracebackType
from typing import Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError, model_validator

from tapieval.schema.trace import SCHEMA_VERSION

# As camadas de julgamento de METRICAS §2–§5. N3 aparece duas vezes de propósito:
# cego e com trace são a MESMA rubrica com insumo diferente (METRICAS §4), e a
# diferença de custo entre elas é um resultado do trabalho (H1), não um detalhe de
# implementação — separá-las aqui é o que permite plotar os dois pontos.
#
# `SUT_referencia` (R5) é a exceção, e ela NÃO é uma camada de julgamento: é o custo do SUJEITO, não
# do instrumento. Existe porque o SUT de referência é o único SUT que fala com a nuvem
# (`sut/referencia.py`) e o único cujo custo de saída NÃO cabe no trace — `LLMCall` tem
# `prompt_tokens` e `completion_tokens` e mais nada, e um modelo de fronteira com raciocínio interno
# gasta ~35× mais tokens de raciocínio que de resposta (`docs/anexos/apuracao/migracao_vertex.md
# §4`). Sem esta camada esse custo não teria onde ser medido.
#
# **Quem agregar INS.4 tem de excluí-la.** Somar o custo do sujeito dentro do custo das
# camadas de julgamento moveria o eixo x de H0 para o lado que favorece a conclusão que o
# trabalho quer defender — o formato do X9.
CamadaJulgamento = Literal[
    "N1", "N2", "N3_cego", "N3_com_trace", "N4", "SUT_referencia"
]

# Camadas que não chamam LLM: N1 e N2 são determinísticas, N4 é humana.
CAMADAS_SEM_LLM = frozenset({"N1", "N2", "N4"})

# O que não é camada de julgamento e não pode entrar na curva custo × recall de H0.
CAMADAS_FORA_DO_JULGAMENTO = frozenset({"SUT_referencia"})

# Só o N4 tem humano no loop.
CAMADAS_COM_HUMANO = frozenset({"N4"})


class CustoRecord(BaseModel):
    """O custo de avaliar UMA execução com UMA camada.

    `segundos` é relógio de parede da camada inteira, não latência de chamada — a
    unidade de INS.4 é a passagem completa do scorer, que soma dezenas de segundos.
    Por isso não reaproveita o `latencia_ms` inteiro do trace, que mede outra coisa.

    `minutos_humano` existe separado porque tempo humano não é cronometrável pelo
    processo: ele é anotado à mão pelo rotulador (METRICAS §7, INS.4).
    """

    schema_version: str = SCHEMA_VERSION
    run_id: str                                  # chave de junção com trace e score
    camada: CamadaJulgamento

    segundos: float = Field(default=0.0, ge=0)
    tokens_in: int = Field(default=0, ge=0)
    tokens_out: int = Field(default=0, ge=0)
    chamadas_llm: int = Field(default=0, ge=0)
    minutos_humano: float | None = Field(default=None, ge=0)

    medido_em: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _checar_coerencia_da_camada(self) -> CustoRecord:
        """Token em camada determinística é erro de contabilidade, não dado.

        O ponto da curva onde N1+N2 custam quase nada é metade do achado de H0:
        deixar um token vazar para lá desloca o eixo x exatamente onde ele precisa
        valer zero. Mesma lógica para minuto humano fora do N4.
        """
        if self.camada in CAMADAS_SEM_LLM and (
            self.tokens_in or self.tokens_out or self.chamadas_llm
        ):
            raise ValueError(f"camada {self.camada} não chama LLM: tokens têm de ser zero")
        if self.camada not in CAMADAS_COM_HUMANO and self.minutos_humano is not None:
            raise ValueError(f"camada {self.camada} não tem humano no loop")
        return self


class MedidorDeCusto:
    """Cronômetro e acumulador de tokens de uma camada, usado como context manager.

    Vive fora do `TraceWriter` de propósito (ver X8 no relatório de T35): custo é
    score, e o writer do trace não pode ganhar estado mutável enquanto dois
    emissores compartilharem o mesmo arquivo.

        with MedidorDeCusto(run_id, "N3_com_trace") as medidor:
            resposta = judge(...)
            medidor.registrar_llm(resposta.prompt_tokens, resposta.completion_tokens)
        writer.registrar(medidor.fechar())
    """

    def __init__(self, run_id: str, camada: CamadaJulgamento) -> None:
        self.run_id = run_id
        self.camada = camada
        self.tokens_in = 0
        self.tokens_out = 0
        self.chamadas_llm = 0
        self.minutos_humano: float | None = None
        self._inicio = perf_counter()
        self._fim: float | None = None

    def __enter__(self) -> MedidorDeCusto:
        # Não reinicia o cronômetro: ele parte da construção, para que a medição
        # não dependa de o chamador ter usado ou não a forma `with`.
        return self

    def __exit__(
        self,
        classe: type[BaseException] | None,
        erro: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        # Congela o tempo mesmo quando a camada estourou: uma avaliação que falhou
        # depois de gastar 40 s gastou 40 s, e esconder isso barateia a curva.
        self._fim = perf_counter()

    def registrar_llm(
        self, prompt_tokens: int, completion_tokens: int, tokens_raciocinio: int = 0
    ) -> None:
        """Acumula o que o servidor de inferência devolveu. Nada é estimado aqui.

        `tokens_raciocinio` soma em `tokens_out`, e o parâmetro existe separado para que o
        call site diga o que está fazendo. Modelos com raciocínio interno cobram e gastam
        esses tokens sem os devolver em `completion_tokens` — medido em 24/08 no endpoint
        OpenAI-compatible do Gemini, onde 21 + 228 tokens reportados correspondiam a 711
        totais. Deixá-los de fora subestimaria o custo do judge em ~65% no eixo x de H0,
        na direção que favorece a conclusão que o trabalho quer defender (o formato de X9).

        Somam em `tokens_out` em vez de virar quarto campo porque, para H0, um token de
        raciocínio custa o mesmo que um token de saída — e o `CustoRecord` fica com os oito
        campos escalares que garantem a atomicidade do append (ver `CustoWriter.registrar`).
        Quem quiser separá-los depois precisa reabrir aquela análise.
        """
        self.tokens_in += prompt_tokens
        self.tokens_out += completion_tokens + tokens_raciocinio
        self.chamadas_llm += 1

    def registrar_minutos_humano(self, minutos: float) -> None:
        """Tempo cronometrado à mão pelo rotulador do N4."""
        self.minutos_humano = minutos

    def segundos(self) -> float:
        fim = self._fim if self._fim is not None else perf_counter()
        return fim - self._inicio

    def fechar(self) -> CustoRecord:
        """Materializa o registro. Pode ser chamado depois do `with`."""
        return CustoRecord(
            run_id=self.run_id,
            camada=self.camada,
            segundos=self.segundos(),
            tokens_in=self.tokens_in,
            tokens_out=self.tokens_out,
            chamadas_llm=self.chamadas_llm,
            minutos_humano=self.minutos_humano,
        )


class CustoWriter:
    """Acrescenta registros de custo em `scores/<scorer_version>/<run_id>.custo.jsonl`.

    O custo é versionado pelo scorer porque É do scorer: trocar a rubrica do judge
    muda quantos tokens ele consome sobre o mesmo trace, e `scores/v1` e `scores/v2`
    precisam poder coexistir (ARQUITETURA §5, decisão 1).
    """

    def __init__(self, run_dir: str | Path, run_id: str, scorer_version: str) -> None:
        self.run_dir = Path(run_dir)
        self.run_id = run_id
        self.scorer_version = scorer_version
        self.scores_dir = self.run_dir / "scores" / scorer_version
        self.custo_path = self.scores_dir / f"{run_id}.custo.jsonl"

        self.scores_dir.mkdir(parents=True, exist_ok=True)

    def registrar(self, registro: CustoRecord) -> None:
        """Grava uma linha. Reabrir o arquivo a cada linha é o que permite que o
        scorer, o judge e a planilha do humano escrevam em momentos distintos sem
        combinar nada entre si.

        Repetir uma camada é remedição legítima (o judge rodou de novo); quem agrega
        INS.4 decide qual vale, porque o arquivo guarda fato, não conclusão.

        Sem trava, ao contrário do `TraceWriter` — e aqui é seguro por construção, não
        por sorte: o `CustoRecord` tem oito campos escalares e nenhum campo de texto
        livre, então a linha fica na casa das centenas de bytes, muito abaixo do
        PIPE_BUF de 4KB que torna o append POSIX atômico. É o mesmo invariante do
        trace (ver X8), com a diferença de que ali ele depende de payload grande ir
        para blob e aqui não depende de nada: **quem acrescentar campo de texto livre
        a `CustoRecord` quebra essa garantia** e precisa reabrir a análise.
        """
        if registro.run_id != self.run_id:
            raise ValueError(
                f"run_id do registro ({registro.run_id}) não é o deste arquivo ({self.run_id})"
            )
        with self.custo_path.open("a", encoding="utf-8") as arquivo:
            arquivo.write(registro.model_dump_json() + "\n")


_ADAPTADOR_CUSTO: TypeAdapter[CustoRecord] = TypeAdapter(CustoRecord)


def ler_custos(path: str | Path) -> list[CustoRecord]:
    """Lê os custos de uma execução, na ordem em que foram medidos.

    Arquivo ausente devolve lista vazia: camada não medida é o caso normal — só 35
    das execuções recebem N4 (METRICAS §5). Linha malformada, ao contrário, é erro:
    custo ilegível somado em silêncio deslocaria o eixo x de H0 sem aviso.
    """
    caminho = Path(path)
    if not caminho.exists():
        return []

    registros: list[CustoRecord] = []
    with caminho.open(encoding="utf-8") as arquivo:
        for numero, linha in enumerate(arquivo, start=1):
            if not linha.strip():
                continue
            try:
                registros.append(_ADAPTADOR_CUSTO.validate_json(linha))
            except ValidationError as erro:
                raise ValueError(f"custo inválido em {caminho}:{numero}") from erro
    return registros
