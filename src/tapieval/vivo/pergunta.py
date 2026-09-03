"""
Uma pergunta digitada agora virando uma execução real — cenário, célula e trace no disco.

O CENÁRIO AD-HOC É UM ARQUIVO, E ISSO É O PONTO
    A pergunta não roda por um caminho paralelo ao da bateria. Ela é gravada como um YAML de
    cenário em `runs/vivo/perguntas/`, carregada pelo MESMO `carregar_cenario_executavel`, e
    executada pelo MESMO `executar_celula` que rodou as 462 execuções do experimento. O trace
    que sai é do mesmo schema, no mesmo layout de diretório, e pode ser repontuado depois por
    quem escrever um gabarito para ele.

    Um caminho de execução só para a demonstração seria a pior escolha possível: a tela
    mostraria um agente que não é o medido, e nenhuma das figuras do README diria nada sobre
    ele. Aqui, o que se vê ao vivo é literalmente o corpo de prova do experimento.

O YAML SAI SEM `gabarito`, E O CARREGADOR ACEITA
    `carregar_cenario_executavel` lê `id`, `split`, `status` e `ambiente.env_seed` — nada do
    gabarito, porque o runner monta o processo em que o SUT roda e não pode ver o lado da
    pontuação (é a "uma porta por consumidor" de `sut.agent.carregar_solicitacao`). Então um
    cenário sem gabarito é executável por construção: quem morre sem gabarito é a pontuação, e
    é ela que `scoring/sem_gabarito.py` substitui pela fatia que o trace sustenta.

A DISPENSA DO JUDGE É ESCRITA, NÃO OMITIDA
    `Bateria` exige declarar o judge congelado ou a dispensa com o motivo por escrito (R4).
    Uma pergunta ad-hoc não tem N3 a pontuar — não há `criterio_sucesso` nem `regra.exige` para
    o judge ler —, então ela declara a dispensa e diz isso. Omitir seria fazer "esta consulta
    não precisa" e "esqueci" caírem no mesmo silêncio.

`split: dev` E NÃO `test`
    O split separa calibração de resultado (`METRICAS §9.3`). Uma pergunta feita no palco não
    é nem uma coisa nem outra, mas ela não pode ser `test`: `test` é o conjunto sobre o qual os
    números reportados foram calculados, e deixar entrar ali qualquer execução avulsa
    contaminaria o denominador de um resultado já publicado.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

from tapieval.runner.judge_congelado import DispensaDeCongelamento
from tapieval.runner.matriz import (
    Bateria,
    Celula,
    ModeloDaBateria,
    carregar_cenario_executavel,
)
from tapieval.runner.runner import (
    _fabrica_padrao,
    executar_celula,
    indexar_prompts,
    resolver_prompt,
)
from tapieval.schema.trace import ModelConfig
from tapieval.sut.variants import carregar_variantes

NOME_DA_BATERIA = "vivo"
"""`runs/vivo/` — o diretório onde toda consulta ao vivo aterrissa.

Um diretório só, e não um por consulta: o layout de `ARQUITETURA §5` é `runs/<experiment_id>/`
com `traces/` e `blobs/` dentro, e blob endereçado por conteúdo se deduplica entre execuções.
Um diretório por pergunta reescreveria o prompt de sistema em disco a cada consulta."""

ENV_SEED_PADRAO = "s001"
"""A seed do mundo. Fixa, e declarada no YAML de cada consulta.

A API do parceiro devolve envelope probabilístico em função de `(seed, recurso, categoria)`:
sem seed, duas perguntas iguais veem mundos diferentes e nada do que se vê na tela é
repetível. `s001` é a seed canônica mais usada pelo corpus — não é especial, é conhecida."""

VARIANTE_PADRAO = "base"
"""O prompt original. Os mutantes existem para medir o instrumento (INS.9), não para atender."""

MOTIVO_DA_DISPENSA = (
    "consulta ao vivo: a pergunta não tem gabarito, então não há critério de sucesso nem "
    "regra que o judge possa ler. A medição possível está em `scoring/sem_gabarito.py`"
)

MODELOS: dict[str, ModelConfig] = {
    "qwen3-8b": ModelConfig(
        model_id="qwen3-8b-mlx",
        served_by="lmstudio",
        quantization="4bit",
        temperature=0.7,
        max_tokens=1200,
        structured_output="json_schema",
        context_window=16384,
    ),
    "qwen3-14b": ModelConfig(
        model_id="qwen3-14b-mlx",
        served_by="lmstudio",
        quantization="4bit",
        temperature=0.7,
        max_tokens=1200,
        structured_output="json_schema",
        context_window=16384,
    ),
}
"""Os dois modelos do experimento, com a configuração de `configs/bateria_principal.yaml`.

Copiados campo a campo de propósito e conferidos por teste contra o YAML: uma consulta ao vivo
que rodasse com `temperature` ou `max_tokens` diferentes mostraria na tela um agente que não é
o que as figuras do README medem, e a demonstração passaria a falar de outra coisa."""

HONRA_SEED = {"qwen3-8b": False, "qwen3-14b": True}
"""Achado da T0b: o 8B devolve texto diferente para a mesma seed, o 14B não. Com `False` a
`ModelConfig.seed` vai a `None` — declarar seed que o servidor ignora seria declarar um
determinismo que não existe."""

SEED_DA_CONSULTA = 11
"""A primeira `sample_seed` da bateria principal. Uma consulta é uma repetição, não oito."""


class ErroDeConsulta(ValueError):
    """A consulta não pode rodar sem produzir uma tela que engana."""


@dataclass(frozen=True)
class Consulta:
    """Uma pergunta ad-hoc, já materializada em cenário no disco e pronta para executar."""

    id: str
    caminho: Path
    texto: str
    user_id: str
    asset_id: str | None
    modelo: str
    run_id: str
    diretorio: Path

    @property
    def trace(self) -> Path:
        return self.diretorio / "traces" / f"{self.run_id}.jsonl"

    @property
    def blobs(self) -> Path:
        return self.diretorio / "blobs"


def preparar(
    texto: str,
    *,
    raiz: Path,
    user_id: str,
    asset_id: str | None = None,
    modelo: str = "qwen3-8b",
    case_id: str | None = None,
    env_seed: str = ENV_SEED_PADRAO,
    agora: datetime | None = None,
) -> Consulta:
    """Grava o cenário ad-hoc e devolve a consulta pronta. Não executa nada.

    Separado de `executar` porque o servidor precisa responder o `run_id` ANTES de a execução
    terminar — a página começa a acompanhar o trace no segundo seguinte, e uma run leva
    minutos. Sem essa separação a única tela possível seria uma ampulheta.
    """
    limpo = texto.strip()
    if not limpo:
        raise ErroDeConsulta("a pergunta veio vazia")
    if modelo not in MODELOS:
        raise ErroDeConsulta(f"modelo desconhecido: {modelo!r} — conheço {sorted(MODELOS)}")
    if not user_id.strip():
        raise ErroDeConsulta("toda consulta é feita POR alguém: `user_id` é obrigatório")

    identificador = _identificador(limpo, agora or datetime.now(UTC))
    diretorio = raiz / "runs" / NOME_DA_BATERIA
    perguntas = diretorio / "perguntas"
    perguntas.mkdir(parents=True, exist_ok=True)

    caminho = perguntas / f"{identificador}.yaml"
    caminho.write_text(
        yaml.safe_dump(
            {
                "id": identificador,
                "procedencia": "autoral",
                "split": "dev",
                "solicitacao": limpo,
                "user_id": user_id.strip(),
                "asset_id": asset_id or None,
                "case_id": case_id or None,
                "ambiente": {"env_seed": env_seed},
                "nota": (
                    "Consulta ao vivo — cenário gerado a partir de pergunta digitada na "
                    "aplicação. NÃO tem gabarito e NÃO entra em bateria nenhuma: não há "
                    "evidência obrigatória, tool esperada nem decisão esperada declaradas "
                    "antes da execução, que é o que faria dele um cenário do corpus."
                ),
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    celula = _celula(caminho, modelo)
    return Consulta(
        id=identificador,
        caminho=caminho,
        texto=limpo,
        user_id=user_id.strip(),
        asset_id=asset_id or None,
        modelo=modelo,
        run_id=celula.run_id,
        diretorio=diretorio,
    )


def executar(consulta: Consulta, *, raiz: Path, timeout_s: float = 900.0) -> str:
    """Roda o agente e devolve o `status` da run. **Nunca levanta** — igual à bateria.

    `executar_celula` já é o ponto de contenção: endpoint fora do ar e bug nosso viram
    `falha_do_instrumento` com `RunError(onde="harness")` no trace, e não exceção. A tela lê o
    status do disco, então uma consulta que morre aparece como consulta que morreu — que é o
    contrário de uma tela que trava sem dizer nada.
    """
    celula = _celula(consulta.caminho, consulta.modelo)
    bateria = _bateria(raiz, celula, timeout_s=timeout_s)
    bateria.diretorio.mkdir(parents=True, exist_ok=True)

    registro = executar_celula(
        bateria,
        celula,
        fabrica=_fabrica_padrao(bateria),
        prompt=resolver_prompt(celula.variante, indexar_prompts()),
    )
    return registro.status


def _celula(caminho: Path, modelo: str) -> Celula:
    return Celula(
        cenario=carregar_cenario_executavel(caminho),
        modelo=ModeloDaBateria(
            model_key=modelo,
            config=MODELOS[modelo],
            honra_seed=HONRA_SEED[modelo],
        ),
        variante=carregar_variantes()[VARIANTE_PADRAO],
        sample_seed=SEED_DA_CONSULTA,
    )


def _bateria(raiz: Path, celula: Celula, *, timeout_s: float) -> Bateria:
    return Bateria(
        experiment_id=NOME_DA_BATERIA,
        cenarios=(celula.cenario,),
        modelos=(celula.modelo,),
        variantes=(celula.variante,),
        sample_seeds=(celula.sample_seed,),
        judge=DispensaDeCongelamento(motivo=MOTIVO_DA_DISPENSA),
        saida=raiz / "runs",
        paralelismo=1,
        timeout_s=timeout_s,
    )


def _identificador(texto: str, agora: datetime) -> str:
    """`vivo_<hhmmss>_<três palavras da pergunta>` — legível no diretório e no `run_id`.

    O carimbo de tempo vem primeiro porque é o que garante unicidade (duas perguntas iguais em
    momentos diferentes são duas consultas), e as palavras vêm depois porque é o que faz um
    diretório com trinta traces continuar navegável no dia seguinte.
    """
    palavras = [
        _sem_acento(palavra)
        for palavra in re.findall(r"[0-9A-Za-zÀ-ÿ_]{4,}", texto.lower())[:3]
    ]
    sufixo = "_".join(p for p in palavras if p) or "pergunta"
    return f"vivo_{agora:%H%M%S}_{sufixo}"[:64]


def _sem_acento(palavra: str) -> str:
    normalizada = unicodedata.normalize("NFKD", palavra)
    return "".join(c for c in normalizada if not unicodedata.combining(c) and c.isalnum())


__all__ = [
    "ENV_SEED_PADRAO",
    "MODELOS",
    "NOME_DA_BATERIA",
    "Consulta",
    "ErroDeConsulta",
    "executar",
    "preparar",
]
