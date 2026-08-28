"""T18 — a configuração da bateria e a matriz de células que ela expande.

O QUE ESTE MÓDULO DECIDE, E O QUE ELE SE RECUSA A DECIDIR SOZINHO
    Ele lê um YAML de bateria e devolve as células a executar. Nenhum eixo do experimento
    tem valor padrão: cenários, modelos, variantes e `sample_seed` são obrigatórios no
    arquivo. O que ganha default é infraestrutura — URL, paralelismo, diretório de saída —,
    porque errar isso quebra alto (connection refused) em vez de medir outra coisa em
    silêncio.

X12 · O FILTRO DE `status: inviavel` MORA AQUI, E NÃO SÓ NO VALIDADOR DE CORPUS
    `scripts/validar_cenarios.py` tira o cenário inviável do split e das contagens, mas quem
    *executa* cenário nunca passou por ele: um `glob("scenarios/*.yaml")` sem filtro roda
    cenário declarado morto e envenena o denominador sem nada ficar vermelho. O filtro está
    em `carregar_corpus_executavel`, e o cenário excluído **não some** — vira uma linha em
    `Bateria.excluidos` que o manifesto grava com o motivo. Mudança de denominador é fato do
    experimento, não detalhe de carregamento.

    E há uma assimetria deliberada entre as duas formas de selecionar cenário:

    * por `split:` — o inviável é **filtrado** e registrado como excluído. Quem pediu "o
      split test" pediu o corpus executável daquele split, e o inviável não faz parte dele.
    * por `ids:` — o inviável é **erro**. Quem nomeou o cenário sabe qual quer; devolver
      silenciosamente uma bateria menor do que a pedida esconderia um erro de configuração
      atrás de um número plausível.

A `env_seed` É DO CENÁRIO, E ENTRA NO `run_id`
    Não existe seed de ambiente global (`CENARIOS §2.3`): um cenário que exige 5 categorias
    `complete` sobrevive a ~7,8% das seeds, e nenhuma seed serve aos 8 autorais ao mesmo
    tempo. A célula carrega a canônica do YAML do cenário.

    Ela entra no `run_id` mesmo sendo constante por cenário nesta bateria: o nome do trace
    passa a dizer em que mundo a run rodou, e a bateria de ambiente (T26b), que varia
    justamente este eixo, não precisará renomear célula nenhuma para caber.

R4 · `judge:` É OBRIGATÓRIO EM TODA BATERIA, E A DISPENSA SE ESCREVE
    `METRICAS §9.3` manda congelar o judge antes da bateria final, e até aqui essa exigência
    morava num comentário de cabeçalho em caixa alta nos cinco manifestos — porque
    `CAMPOS_DA_BATERIA` recusava a chave. Cabeçalho não é validação.

    O campo existe agora, e a decisão de desenho é **de quem é o ônus**. A tentação era deixar
    `judge` opcional e exigi-lo só nas cinco baterias finais, reconhecidas por nome de arquivo
    ou por `experiment_id`. Isso teria posto a regra num lugar onde ela não se lê: o YAML da
    principal continuaria sem dizer nada, e a diferença entre "esta bateria não precisa" e
    "esqueci de declarar" voltaria a ser a ausência de uma linha — que é exatamente o defeito
    que esta task veio consertar, e o mesmo formato do X12 e do A7.

    Então o campo é **obrigatório em toda bateria**, e são duas as formas de satisfazê-lo:

        judge: configs/judge_frozen.json     # o congelamento, com o sha CONFERIDO ao carregar
        judge:
          sem_congelamento: "por que esta bateria roda sem ele"

    A piloto e a calibração usam a segunda: são anteriores ao congelamento da T23 e rodam sem
    ele **de propósito** — e agora dizem isso, em vez de omitir. O motivo é obrigatório e não
    vazio porque ninguém escreve uma frase sem querer, e ele vai para o `manifest.json` no
    mesmo campo em que o sha das outras vai: quem ler o resultado vê que ali não havia sha.

    O que este desenho **não** promete: nada impede alguém de escrever `sem_congelamento` na
    bateria principal. Código não detecta mentira escrita; detecta silêncio. O que se ganha é
    que a mentira precisa ser digitada, aparece no diff e fica gravada no manifesto da run.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from tapieval.runner.judge_congelado import (
    CHAVE_DA_DISPENSA,
    DeclaracaoDoJudge,
    ErroDeJudgeCongelado,
    interpretar_declaracao,
)
from tapieval.schema.trace import ModelConfig, VariantConfig
from tapieval.sut.variants import CAMINHO_PADRAO as CAMINHO_DAS_VARIANTES
from tapieval.sut.variants import carregar_variantes

RAIZ_DO_REPO = Path(__file__).resolve().parents[3]
DIRETORIO_DE_CENARIOS = RAIZ_DO_REPO / "scenarios"

STATUS_PADRAO = "valido"
"""Ausente significa `valido` — a mesma convenção de `scripts/validar_cenarios.py`, para que
os 24 cenários vivos não precisem declarar o caso comum. Só o desvio é escrito."""

APROVADORES = ("policy", "auto_approve", "auto_deny")
"""`HumanApprover` fica de fora de propósito: `ARQUITETURA §3.7` é explícito que ele não
escala para a bateria, e uma bateria de 288 runs parada num `input()` é o formato de falha
que ninguém descobre até a madrugada acabar."""

CAMPOS_DA_BATERIA: frozenset[str] = frozenset(
    {
        "experiment_id",
        "cenarios",
        "modelos",
        "variantes",
        "sample_seeds",
        "judge",
        "saida",
        "api_base_url",
        "inferencia_base_url",
        "paralelismo",
        "timeout_s",
        "approver",
        "arquivo_de_variantes",
    }
)

CAMPOS_DO_MODELO: frozenset[str] = frozenset(ModelConfig.model_fields) - {"seed"} | {
    "honra_seed"
}
"""`seed` não se escreve: ela É a `sample_seed` da célula (ver `ModeloDaBateria.para`)."""

API_BASE_URL_PADRAO = "http://127.0.0.1:8000"
INFERENCIA_BASE_URL_PADRAO = "http://127.0.0.1:1234/v1"
PARALELISMO_PADRAO = 2


class ErroDeBateria(ValueError):
    """A configuração da bateria não pôde ser lida sem ambiguidade."""


# ---------------------------------------------------------------------------
# A fatia EXECUTÁVEL do cenário
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CenarioExecutavel:
    """O que o runner precisa saber de um cenário — e nada do gabarito.

    Terceira porta de entrada do mesmo YAML, ao lado de `scoring.gabarito.carregar_cenario`
    (que lê o gabarito para pontuar) e `sut.agent.carregar_solicitacao` (que lê o pedido para
    executar). Uma porta por consumidor: o runner precisa de `status`, `split` e `env_seed`,
    que nenhuma das outras duas carrega, e não pode ver `tools_esperadas` — ele monta o
    processo em que o SUT roda.
    """

    id: str
    caminho: Path
    split: str
    status: str
    env_seed: str
    natureza: str | None = None
    justificativa_inviabilidade: str | None = None

    @property
    def inviavel(self) -> bool:
        return self.status != STATUS_PADRAO


def carregar_cenario_executavel(caminho: Path) -> CenarioExecutavel:
    documento = yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}
    ambiente = documento.get("ambiente") or {}
    env_seed = ambiente.get("env_seed")
    identificador = documento.get("id") or caminho.stem

    if not env_seed:
        raise ErroDeBateria(
            f"{caminho.name}: falta `ambiente.env_seed`. A seed do ambiente é por cenário "
            "(`CENARIOS §2.3`) e sem ela a run roda contra um mundo que o manifesto não "
            "consegue declarar"
        )

    return CenarioExecutavel(
        id=identificador,
        caminho=caminho,
        split=documento.get("split", ""),
        status=documento.get("status", STATUS_PADRAO),
        env_seed=str(env_seed),
        natureza=documento.get("natureza"),
        justificativa_inviabilidade=documento.get("justificativa_inviabilidade"),
    )


def carregar_corpus_executavel(
    diretorio: Path = DIRETORIO_DE_CENARIOS,
) -> dict[str, CenarioExecutavel]:
    """Todos os cenários do diretório, **inclusive os inviáveis**, indexados por `id`.

    Carregar o inviável e filtrá-lo depois, em vez de nunca o ler: é assim que
    `selecionar_cenarios` consegue dizer *qual* cenário saiu e *por quê*. Um filtro no
    `glob` deixaria a exclusão indistinguível de "o arquivo não existe".

    Arquivos `_*.yaml` são contrato (`_regras_decisao.yaml`), não cenário.
    """
    cenarios = [
        carregar_cenario_executavel(caminho)
        for caminho in sorted(diretorio.glob("*.yaml"))
        if not caminho.name.startswith("_")
    ]
    return {cenario.id: cenario for cenario in cenarios}


# ---------------------------------------------------------------------------
# Os eixos
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModeloDaBateria:
    """Um modelo do experimento: a `ModelConfig` sem a seed, mais o que fazer com ela.

    `honra_seed=False` é achado da T0b, não conveniência: o 8B devolveu textos diferentes
    para a mesma seed e o 14B não. Com ele em `False`, `ModelConfig.seed` vai a `None` — que
    é o que `schema/trace.py` reserva para "servidor não suporta seed" — enquanto
    `RunStart.seed` continua registrando a `sample_seed` da célula, porque a repetição
    aconteceu mesmo sendo irreprodutível. Escrever a seed numa `ModelConfig` que o servidor
    ignora seria declarar um determinismo que a bateria não tem.
    """

    model_key: str
    config: ModelConfig
    honra_seed: bool = True

    def para(self, sample_seed: int) -> ModelConfig:
        """A `ModelConfig` desta célula."""
        return self.config.model_copy(
            update={"seed": sample_seed if self.honra_seed else None}
        )


@dataclass(frozen=True)
class Celula:
    """Uma coordenada da matriz. O `run_id` é derivado dela e nunca sorteado.

    Sorteio (uuid) tornaria a retomada dependente do manifesto ser a única verdade: um
    manifesto perdido no meio da bateria órfã os traces já escritos e a retomada os
    repetiria. Derivado, o nome do arquivo **é** a chave da célula, e disco e manifesto
    podem ser conferidos um contra o outro.
    """

    cenario: CenarioExecutavel
    modelo: ModeloDaBateria
    variante: VariantConfig
    sample_seed: int

    @property
    def run_id(self) -> str:
        return (
            f"{self.cenario.id}--{self.modelo.model_key}--{self.variante.variant_id}"
            f"--env{self.cenario.env_seed}--n{self.sample_seed}"
        )


@dataclass(frozen=True)
class CenarioExcluido:
    cenario_id: str
    motivo: str


@dataclass(frozen=True)
class Bateria:
    """A configuração de uma bateria, resolvida contra o corpus e o catálogo de variantes."""

    experiment_id: str
    cenarios: tuple[CenarioExecutavel, ...]
    modelos: tuple[ModeloDaBateria, ...]
    variantes: tuple[VariantConfig, ...]
    sample_seeds: tuple[int, ...]
    judge: DeclaracaoDoJudge
    """O judge contra o qual esta bateria será pontuada, ou a dispensa por escrito (R4).

    Sem default, e o lugar na lista de campos obrigatórios é o ponto: não existe `Bateria`
    construída sem responder a esta pergunta, nem no carregador nem em teste."""

    saida: Path = RAIZ_DO_REPO / "runs"
    api_base_url: str = API_BASE_URL_PADRAO
    inferencia_base_url: str = INFERENCIA_BASE_URL_PADRAO
    paralelismo: int = PARALELISMO_PADRAO
    timeout_s: float | None = None
    approver: str = "policy"
    excluidos: tuple[CenarioExcluido, ...] = ()
    caminho: Path | None = field(default=None, compare=False)

    @property
    def diretorio(self) -> Path:
        """`runs/<experiment_id>/` — o layout de `ARQUITETURA §5`."""
        return self.saida / self.experiment_id

    def expandir(self) -> tuple[Celula, ...]:
        """As células, na ordem em que a bateria as executa.

        Cenário no laço mais externo e `sample_seed` no mais interno: as repetições de uma
        mesma célula ficam adjacentes, então uma bateria interrompida no meio deixa o
        `pass^k` de alguns cenários completo em vez de todos pela metade.
        """
        return tuple(
            Celula(cenario, modelo, variante, seed)
            for cenario in self.cenarios
            for modelo in self.modelos
            for variante in self.variantes
            for seed in self.sample_seeds
        )


# ---------------------------------------------------------------------------
# Carregamento
# ---------------------------------------------------------------------------


def carregar_bateria(
    caminho: Path,
    *,
    corpus: Mapping[str, CenarioExecutavel] | None = None,
    variantes_disponiveis: Mapping[str, VariantConfig] | None = None,
) -> Bateria:
    """Lê o YAML da bateria e resolve todos os eixos. Erro é `ErroDeBateria`, nunca parcial."""
    documento = yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}
    if not isinstance(documento, dict):
        raise ErroDeBateria(f"{caminho.name}: esperava um mapeamento no topo")

    desconhecidos = sorted(set(documento) - CAMPOS_DA_BATERIA)
    if desconhecidos:
        raise ErroDeBateria(
            f"{caminho.name}: campo(s) desconhecido(s) {desconhecidos}. Aceitos: "
            f"{sorted(CAMPOS_DA_BATERIA)}. Chave com erro de grafia seria descartada em "
            "silêncio e a bateria rodaria uma matriz diferente da que o arquivo descreve"
        )

    experiment_id = documento.get("experiment_id")
    if not experiment_id or not isinstance(experiment_id, str):
        raise ErroDeBateria(f"{caminho.name}: falta `experiment_id` — é o nome do diretório")

    corpus = corpus if corpus is not None else carregar_corpus_executavel()
    arquivo_de_variantes = documento.get("arquivo_de_variantes")
    if variantes_disponiveis is None:
        variantes_disponiveis = carregar_variantes(
            RAIZ_DO_REPO / arquivo_de_variantes
            if arquivo_de_variantes
            else CAMINHO_DAS_VARIANTES
        )

    cenarios, excluidos = _selecionar_cenarios(documento.get("cenarios"), corpus, caminho)
    modelos = _modelos(documento.get("modelos"), caminho)
    variantes = _variantes(documento.get("variantes"), variantes_disponiveis, caminho)
    sample_seeds = _sample_seeds(documento.get("sample_seeds"), caminho)
    judge = _judge(documento, caminho)

    approver = documento.get("approver", "policy")
    if approver not in APROVADORES:
        raise ErroDeBateria(
            f"{caminho.name}: `approver` {approver!r} desconhecido. Aceitos: "
            f"{list(APROVADORES)}"
        )

    paralelismo = int(documento.get("paralelismo", PARALELISMO_PADRAO))
    if paralelismo < 1:
        raise ErroDeBateria(f"{caminho.name}: `paralelismo` precisa ser >= 1")

    saida = documento.get("saida")
    timeout_s = documento.get("timeout_s")

    return Bateria(
        experiment_id=experiment_id,
        cenarios=cenarios,
        modelos=modelos,
        variantes=variantes,
        sample_seeds=sample_seeds,
        judge=judge,
        saida=Path(saida) if saida else RAIZ_DO_REPO / "runs",
        api_base_url=documento.get("api_base_url", API_BASE_URL_PADRAO),
        inferencia_base_url=documento.get(
            "inferencia_base_url", INFERENCIA_BASE_URL_PADRAO
        ),
        paralelismo=paralelismo,
        timeout_s=float(timeout_s) if timeout_s is not None else None,
        approver=approver,
        excluidos=excluidos,
        caminho=caminho,
    )


def _selecionar_cenarios(
    crus: Any, corpus: Mapping[str, CenarioExecutavel], caminho: Path
) -> tuple[tuple[CenarioExecutavel, ...], tuple[CenarioExcluido, ...]]:
    """Os cenários da bateria, e os que o X12 tirou dela com o motivo escrito."""
    if not isinstance(crus, dict) or not crus:
        raise ErroDeBateria(
            f"{caminho.name}: falta `cenarios:` com `split:` ou `ids:`. Sem seleção "
            "explícita a bateria rodaria o corpus inteiro, e o denominador de toda métrica "
            "passaria a depender de quantos arquivos existem no diretório"
        )
    desconhecidos = sorted(set(crus) - {"split", "ids"})
    if desconhecidos:
        raise ErroDeBateria(
            f"{caminho.name}: `cenarios` aceita `split` ou `ids`, veio {desconhecidos}"
        )
    if "split" in crus and "ids" in crus:
        raise ErroDeBateria(
            f"{caminho.name}: `cenarios` aceita `split` OU `ids`, nunca os dois — a "
            "interseção dos dois é uma terceira seleção que ninguém escreveu"
        )

    if "ids" in crus:
        return _por_ids(crus["ids"], corpus, caminho), ()
    return _por_split(crus["split"], corpus, caminho)


def _por_ids(
    ids: Any, corpus: Mapping[str, CenarioExecutavel], caminho: Path
) -> tuple[CenarioExecutavel, ...]:
    if not isinstance(ids, list) or not ids:
        raise ErroDeBateria(f"{caminho.name}: `cenarios.ids` precisa ser uma lista não vazia")

    ausentes = sorted(str(i) for i in ids if str(i) not in corpus)
    if ausentes:
        raise ErroDeBateria(f"{caminho.name}: cenário(s) inexistente(s) no corpus: {ausentes}")

    selecionados = tuple(corpus[str(i)] for i in ids)
    inviaveis = [c for c in selecionados if c.inviavel]
    if inviaveis:
        # Assimetria deliberada com `_por_split` — ver a docstring do módulo.
        detalhe = ", ".join(
            f"{c.id} ({c.justificativa_inviabilidade or 'sem justificativa'})"
            for c in inviaveis
        )
        raise ErroDeBateria(
            f"{caminho.name}: `cenarios.ids` nomeia cenário declarado inviável: {detalhe}. "
            "Nomear um cenário é dizer que se quer aquele; rodar a bateria sem ele em "
            "silêncio devolveria menos células do que o arquivo pediu (X12)"
        )
    return selecionados


def _por_split(
    split: Any, corpus: Mapping[str, CenarioExecutavel], caminho: Path
) -> tuple[tuple[CenarioExecutavel, ...], tuple[CenarioExcluido, ...]]:
    if split not in ("dev", "test"):
        raise ErroDeBateria(
            f"{caminho.name}: `cenarios.split` precisa ser 'dev' ou 'test', veio {split!r}"
        )

    do_split = [c for c in corpus.values() if c.split == split]
    if not do_split:
        raise ErroDeBateria(f"{caminho.name}: nenhum cenário no split {split!r}")

    selecionados = tuple(c for c in do_split if not c.inviavel)
    excluidos = tuple(
        CenarioExcluido(
            c.id,
            "status: inviavel — "
            + (c.justificativa_inviabilidade or "sem justificativa declarada"),
        )
        for c in do_split
        if c.inviavel
    )
    if not selecionados:
        raise ErroDeBateria(
            f"{caminho.name}: todos os cenários do split {split!r} estão declarados inviáveis"
        )
    return selecionados, excluidos


def _modelos(crus: Any, caminho: Path) -> tuple[ModeloDaBateria, ...]:
    if not isinstance(crus, dict) or not crus:
        raise ErroDeBateria(
            f"{caminho.name}: falta `modelos:` — um mapeamento `model_key: {{campos da "
            "ModelConfig}}`. O `model_key` vem da chave, como o `variant_id` da T17"
        )

    modelos: list[ModeloDaBateria] = []
    for model_key, corpo in crus.items():
        if not isinstance(corpo, dict):
            raise ErroDeBateria(f"modelo {model_key!r}: esperava um mapeamento de campos")

        desconhecidos = sorted(set(corpo) - CAMPOS_DO_MODELO)
        if desconhecidos:
            extra = ""
            if "seed" in desconhecidos:
                extra = (
                    " `seed` em particular não se declara: ela É a `sample_seed` da célula, e "
                    "fixá-la aqui colapsaria o eixo que sustenta o pass^k num valor só."
                )
            raise ErroDeBateria(
                f"modelo {model_key!r}: campo(s) desconhecido(s) {desconhecidos}. Aceitos: "
                f"{sorted(CAMPOS_DO_MODELO)}.{extra}"
            )

        campos = dict(corpo)
        honra_seed = bool(campos.pop("honra_seed", True))
        try:
            config = ModelConfig(**campos)
        except ValueError as erro:
            raise ErroDeBateria(f"modelo {model_key!r}: {erro}") from erro
        modelos.append(ModeloDaBateria(str(model_key), config, honra_seed))

    return tuple(modelos)


def _variantes(
    crus: Any, disponiveis: Mapping[str, VariantConfig], caminho: Path
) -> tuple[VariantConfig, ...]:
    if not isinstance(crus, list) or not crus:
        raise ErroDeBateria(
            f"{caminho.name}: falta `variantes:` — uma lista de ids de `configs/variants.yaml`"
        )
    ausentes = sorted(str(v) for v in crus if str(v) not in disponiveis)
    if ausentes:
        raise ErroDeBateria(
            f"{caminho.name}: variante(s) inexistente(s) {ausentes}. Disponíveis: "
            f"{sorted(disponiveis)}"
        )
    return tuple(disponiveis[str(v)] for v in crus)


def _judge(documento: Mapping[str, Any], caminho: Path) -> DeclaracaoDoJudge:
    """O judge declarado, com o congelamento já conferido — ou a dispensa por escrito (R4).

    A ausência é erro **em toda bateria**, e a mensagem diz as duas saídas. Deixar a ausência
    passar em nome das baterias de dimensionamento faria "esqueci de declarar" e "esta bateria
    não precisa" caírem no mesmo silêncio, e o cabeçalho em caixa alta dos cinco manifestos
    continuaria sendo a única defesa contra rodar a bateria final com a rubrica solta.
    """
    if "judge" not in documento:
        raise ErroDeBateria(
            f"{caminho.name}: falta `judge:`. `METRICAS §9.3` congela prompt + rubrica + "
            "few-shots + snapshot do modelo com sha256 ANTES da bateria final, e pontuar duas "
            "noites contra rubricas diferentes torna a curva de H0 incomparável com ela mesma. "
            "Declare o congelamento — `judge: configs/judge_frozen.json` — ou, se esta bateria "
            f"roda sem ele de propósito, escreva o porquê: `judge: {{{CHAVE_DA_DISPENSA}: "
            '"<motivo>"}}`. Omitir não é uma terceira opção'
        )
    try:
        return interpretar_declaracao(
            documento["judge"], raiz=RAIZ_DO_REPO, contexto=caminho.name
        )
    except ErroDeJudgeCongelado as erro:
        # Reembrulhado para manter a promessa do carregador: erro é `ErroDeBateria`, e a CLI
        # (que só captura essa) morre com mensagem em vez de stack trace.
        raise ErroDeBateria(f"{caminho.name}: {erro}") from erro


def _sample_seeds(crus: Any, caminho: Path) -> tuple[int, ...]:
    if not isinstance(crus, list) or not crus:
        raise ErroDeBateria(
            f"{caminho.name}: falta `sample_seeds:` — a lista de repetições. É o eixo do "
            "`pass^k` (`METRICAS §9.1`), e é o único que não se corta para caber no tempo"
        )
    seeds = [int(s) for s in crus]
    repetidas = sorted({s for s in seeds if seeds.count(s) > 1})
    if repetidas:
        raise ErroDeBateria(
            f"{caminho.name}: `sample_seeds` repete {repetidas}. Duas células com a mesma "
            "seed colidem no mesmo `run_id`, e a segunda sobrescreveria o trace da primeira"
        )
    return tuple(seeds)


__all__ = [
    "APROVADORES",
    "Bateria",
    "Celula",
    "CenarioExcluido",
    "CenarioExecutavel",
    "DeclaracaoDoJudge",
    "ErroDeBateria",
    "ModeloDaBateria",
    "carregar_bateria",
    "carregar_cenario_executavel",
    "carregar_corpus_executavel",
]
