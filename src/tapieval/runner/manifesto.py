"""T18 — o manifesto da bateria: o que ela declarou rodar e o que rodou de fato.

`runs/<experiment_id>/manifest.json` (`ARQUITETURA §5`). Três papéis, e só eles:

1. **Declara a matriz.** As células, os modelos e as variantes inteiros — não os ids. É o
   que permite conferir, depois, que o `prompt_sha` da coluna é o do prompt que rodou
   (`sut/variants.py`) e que a `ModelConfig` do eixo x do custo é a que o servidor recebeu.
2. **Registra cada run.** Uma linha por célula executada, com o resultado e a validade.
3. **Sustenta a retomada.** Célula com registro não roda de novo; célula sem registro é
   célula faltante **explícita**, não ausência.

A7 · POR QUE A VALIDADE DA RUN MORA AQUI
    `schema/reader.validar_trace` existe desde 19/08 e ninguém a chamava. Quem a chama é o
    runner, ao fechar cada run, e o motivo que ela devolve vem parar neste arquivo.

    Run com defeito estrutural **não é apagada**: entra com `valida: false` e o porquê. É a
    diferença entre "essa célula não foi medida, por este defeito" e um buraco silencioso nas
    contagens. Descartar em silêncio suporia que runs quebram de forma aleatória, e elas não
    quebram: quebram pelo mesmo motivo, na mesma célula da matriz — e é justamente aí que a
    ausência seria lida como "nada de errado aconteceu".

    `motivo_nao_pontuavel` sai daqui já na forma que o `ScoreRecord` consome, e não como
    booleano: quem pontuar a run monta `ScoreRecord(pontuavel=False,
    motivo_nao_pontuavel=registro.motivo_nao_pontuavel)`, e o validador do `ScoreRecord`
    recusa a combinação perigosa (fora do denominador **e** aprovada). O outro caminho para
    não pontuável — `decisao_prevista is None`, de `scoring.severidade.motivo_nao_pontuavel`
    — só é descoberto ao pontuar, e por isso **não** está aqui: o runner registra o que o
    runner sabe.

AS COORDENADAS DA CÉLULA NÃO SE REPETEM NO REGISTRO
    `celulas` diz de quem é cada `run_id`; `runs` diz o que aconteceu. Escrever cenário,
    modelo, variante e seed nos dois lugares seria criar duas fontes para a mesma coisa, e a
    divergência aqui renomeia célula de experimento — o mesmo motivo pelo qual o `variant_id`
    vem da chave do YAML e não de um campo (T17).
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from tapieval.schema.reader import Defeito
from tapieval.schema.trace import SCHEMA_VERSION, ModelConfig, VariantConfig

NOME_DO_MANIFESTO = "manifest.json"

StatusDaRun = Literal[
    "ok", "budget_exceeded", "error", "timeout", "falha_do_instrumento"
]
"""Os quatro do `RunEnd` mais um quinto que só o manifesto conhece.

`falha_do_instrumento` é a run que o harness **não conseguiu completar** — endpoint de
inferência fora do ar, bug nosso. No trace ela sai como `RunEnd(status="error")`, porque é o
vocabulário que o schema tem, com um `RunError(onde="harness")` dizendo o que houve; aqui ela
é separada de `error` porque as duas exigem coisas opostas do operador. `error` é resultado do
experimento (o modelo não produziu passo válido) e não se repete; `falha_do_instrumento` é
defeito nosso e a retomada a refaz depois de consertado.
"""

STATUS_QUE_SAO_RESULTADO: frozenset[str] = frozenset(
    {"ok", "budget_exceeded", "error", "timeout"}
)


class CoordenadaDaCelula(BaseModel):
    """A célula da matriz, declarada antes de rodar. É o denominador da bateria."""

    run_id: str
    scenario_id: str
    split: str
    model_key: str
    variant_id: str
    sample_seed: int
    env_seed: str


class CenarioExcluido(BaseModel):
    """Cenário que estava no split e não entrou na matriz (X12). Mudança de denominador."""

    cenario_id: str
    motivo: str


class RegistroDeRun(BaseModel):
    """O que aconteceu numa célula. Um por `run_id`, reescrito só numa re-execução."""

    run_id: str
    status: StatusDaRun
    duracao_ms: int
    concluida_em: datetime

    n_tool_calls: int = 0
    n_llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    parse_failures: int = 0
    iteracoes: int = 0

    valida: bool = True
    defeitos: tuple[str, ...] = ()
    motivo_nao_pontuavel: str | None = None
    """Já na forma que `ScoreRecord.motivo_nao_pontuavel` consome. `None` quando a run é
    estruturalmente sã — o que **não** garante que ela seja pontuável: a decisão ausente
    (`scoring.severidade.motivo_nao_pontuavel`) só aparece ao pontuar."""

    erro: str | None = None
    """A exceção que interrompeu a run. Só em `falha_do_instrumento`."""

    trace: str
    """Caminho do `.jsonl` relativo a `runs/<experiment_id>/`."""

    @model_validator(mode="after")
    def _validade_e_motivo_andam_juntos(self) -> RegistroDeRun:
        """A mesma invariante do `ScoreRecord`, um degrau antes.

        Sem ela, "o trace quebrou" e "a run correu bem" ficam indistinguíveis para quem lê o
        manifesto sem abrir o trace — e quem lê o manifesto é justamente quem monta o
        denominador do `pass^k`.
        """
        if not self.valida:
            if not self.defeitos:
                raise ValueError(
                    "run inválida exige `defeitos` — a decisão 9 de `ARQUITETURA §5` manda "
                    "guardar o motivo, não só o veredito"
                )
            if not self.motivo_nao_pontuavel:
                raise ValueError("run inválida exige `motivo_nao_pontuavel` preenchido")
        else:
            if self.defeitos:
                raise ValueError("`defeitos` preenchidos numa run marcada como válida")
            if self.motivo_nao_pontuavel:
                raise ValueError(
                    "`motivo_nao_pontuavel` preenchido numa run estruturalmente sã"
                )
        if self.status == "falha_do_instrumento" and not self.erro:
            raise ValueError(
                "`falha_do_instrumento` exige `erro`: sem a exceção, a run vira um buraco "
                "que ninguém consegue reproduzir nem consertar"
            )
        return self

    @property
    def e_resultado(self) -> bool:
        """A run terminou — bem ou mal, mas terminou. A retomada não a repete."""
        return self.status in STATUS_QUE_SAO_RESULTADO


class Manifesto(BaseModel):
    """O manifesto completo. Serializado como JSON, relido pela retomada."""

    schema_version: str = SCHEMA_VERSION
    experiment_id: str
    criado_em: datetime
    atualizado_em: datetime

    api_base_url: str
    inferencia_base_url: str
    approver: str
    paralelismo: int
    timeout_s: float | None = None
    env_mode: Literal["live", "replay"] = "live"
    cassette_id: str | None = None
    """Sempre `None`: a T6 (proxy record/replay) foi cortada em 14/08 porque o modo de retorno
    da API é função pura de `(seed, recurso, categoria)` — reprodutibilidade do ambiente é um
    query param, não uma camada de software. O campo fica porque o `RunStart` o tem."""

    modelos: dict[str, ModelConfig]
    """A `ModelConfig` **sem** a `sample_seed` da célula: aqui é o modelo, lá é a repetição."""
    variantes: dict[str, VariantConfig]

    celulas: tuple[CoordenadaDaCelula, ...]
    cenarios_excluidos: tuple[CenarioExcluido, ...] = ()
    runs: dict[str, RegistroDeRun] = Field(default_factory=dict)

    def registrar(self, registro: RegistroDeRun) -> None:
        self.runs[registro.run_id] = registro
        self.atualizado_em = datetime.now(UTC)

    def pendentes(self, *, retomar: bool = True) -> tuple[CoordenadaDaCelula, ...]:
        """As células que ainda precisam rodar.

        Com `retomar=False` são todas — é o que se pede depois de mexer no agente, quando
        reaproveitar trace antigo compararia duas versões do SUT na mesma tabela.
        """
        if not retomar:
            return self.celulas
        return tuple(
            celula
            for celula in self.celulas
            if not (
                (registro := self.runs.get(celula.run_id)) is not None
                and registro.e_resultado
            )
        )

    def faltantes(self) -> tuple[CoordenadaDaCelula, ...]:
        """Células declaradas sem registro nenhum. Bateria incompleta é reportada, não escondida."""
        return tuple(celula for celula in self.celulas if celula.run_id not in self.runs)

    def invalidas(self) -> tuple[RegistroDeRun, ...]:
        return tuple(registro for registro in self.runs.values() if not registro.valida)


def motivo_nao_pontuavel_de(defeitos: Sequence[Defeito]) -> str | None:
    """A frase que vai para `ScoreRecord.motivo_nao_pontuavel`, ou `None` se não há defeito.

    Nomeia **todos** os defeitos, não o primeiro: `seq` duplicado e lacuna costumam vir do
    mesmo acidente (dois emissores numerando sem coordenação), e ler só um deles mandaria o
    diagnóstico para o lado errado.
    """
    if not defeitos:
        return None
    return "trace inválido (A7): " + "; ".join(str(defeito) for defeito in defeitos)


def caminho_do_manifesto(diretorio: Path) -> Path:
    return diretorio / NOME_DO_MANIFESTO


def ler_manifesto(diretorio: Path) -> Manifesto | None:
    """O manifesto da bateria, ou `None` se ela nunca rodou."""
    caminho = caminho_do_manifesto(diretorio)
    if not caminho.exists():
        return None
    return Manifesto.model_validate_json(caminho.read_text(encoding="utf-8"))


def escrever_manifesto(manifesto: Manifesto, diretorio: Path) -> Path:
    """Grava o manifesto de forma atômica: arquivo temporário e `os.replace`.

    Reescrever no lugar deixaria, se a bateria morresse no meio da escrita, um JSON truncado
    — e um manifesto ilegível transforma uma bateria retomável de 288 runs numa bateria
    perdida. `os.replace` é atômico no mesmo sistema de arquivos, e o temporário é criado no
    mesmo diretório justamente para garantir isso.
    """
    diretorio.mkdir(parents=True, exist_ok=True)
    destino = caminho_do_manifesto(diretorio)
    temporario = destino.with_suffix(".json.tmp")
    temporario.write_text(
        manifesto.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporario, destino)
    return destino


__all__ = [
    "NOME_DO_MANIFESTO",
    "CenarioExcluido",
    "CoordenadaDaCelula",
    "Manifesto",
    "RegistroDeRun",
    "StatusDaRun",
    "caminho_do_manifesto",
    "escrever_manifesto",
    "ler_manifesto",
    "motivo_nao_pontuavel_de",
]
