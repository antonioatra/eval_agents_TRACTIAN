"""T23/R4 — o judge que a bateria declara, e o que faz de um arquivo um congelamento.

POR QUE ISTO EXISTE
    `METRICAS §9.3` manda congelar o judge (prompt + rubrica + few-shots + snapshot do modelo)
    com sha256 **antes** da bateria final, e o cabeçalho dos cinco manifestos repete a exigência
    em caixa alta. Cabeçalho não é validação: até aqui, rodar a bateria principal contra um
    judge não congelado — ou contra um `judge_frozen.json` editado depois de congelado — não
    encontrava nada no caminho. A curva de H0 ficaria incomparável com ela mesma entre duas
    noites e nada ficaria vermelho.

    É o formato de erro que este projeto já catalogou duas vezes (X12 no denominador, A7 na
    validade da run): o instrumento não distingue "medido" de "não medido", e a ausência é lida
    como "nada de errado aconteceu". A resposta é a mesma das outras duas — a ausência vira
    erro, e o desvio vira uma linha escrita que o manifesto grava.

UM SHA QUE NÃO CONFERE É PIOR QUE NENHUM SHA
    Um `judge_frozen.json` cujo `sha256` não bate com o conteúdo **parece** congelado para todo
    leitor humano do arquivo e para todo `ScoreRecord` que copiar o campo. Ele é a única forma
    de o pré-registro ser violado sem deixar rastro. Por isso o sha é **recalculado** aqui a
    cada carregamento, e não só lido.

O QUE O SHA ASSINA, E O QUE ELE DEIXA DE FORA
    Assina `CAMPOS_ASSINADOS`: a versão, o prompt, a rubrica, os few-shots e o snapshot do
    modelo — tudo que muda o que o judge responde. Fica de fora `congelado_em` (o carimbo do
    ato, não o instrumento: recongelar conteúdo idêntico noutro dia é o mesmo judge, e assinar
    a data faria dois congelamentos iguais parecerem diferentes), `rubrica_sha` (derivado da
    rubrica, e conferido contra ela), `fewshot_origem` e `notas`.

    É a mesma escolha de `scoring/severidade.py::_material_do_congelamento`: arrumação não mexe
    no sha, curadoria mexe.

O `judge_model` É NORMALIZADO ANTES DE ASSINAR
    O snapshot entra no material como `ModelConfig(...).model_dump(mode="json")`, não como o
    dicionário cru. Dois arquivos que descrevem o mesmo modelo — um omitindo um campo que tem
    default, o outro escrevendo-o — descrevem o mesmo judge e têm de ter o mesmo sha. Assinar o
    cru transformaria estilo de escrita em mudança de instrumento.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from tapieval.schema.trace import ModelConfig

CAMPOS_ASSINADOS: tuple[str, ...] = (
    "fewshots",
    "judge_model",
    "prompt",
    "rubrica",
    "scorer_version",
)
"""Em ordem alfabética porque é assim que entram no material — ver `material_assinado`."""

CAMPOS_NAO_ASSINADOS: frozenset[str] = frozenset(
    {"sha256", "congelado_em", "rubrica_sha", "fewshot_origem", "notas"}
)

CAMPOS_DO_JUDGE: frozenset[str] = frozenset(CAMPOS_ASSINADOS) | CAMPOS_NAO_ASSINADOS

CAMPOS_OBRIGATORIOS: frozenset[str] = frozenset(CAMPOS_ASSINADOS) | {
    "sha256",
    "congelado_em",
}

CHAVE_DA_DISPENSA = "sem_congelamento"
"""A única forma de uma bateria rodar sem judge congelado: dizendo-o, com o motivo escrito."""


class ErroDeJudgeCongelado(ValueError):
    """O judge declarado não pôde ser lido como um congelamento.

    Fica fora de `ErroDeBateria` de propósito: quem congela o judge (a outra metade da T23) usa
    este módulo sem passar pelo carregador de bateria. `matriz.carregar_bateria` reembrulha em
    `ErroDeBateria`, para que a promessa "erro é `ErroDeBateria`, nunca parcial" continue de pé.
    """


# ---------------------------------------------------------------------------
# As duas declarações possíveis
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JudgeCongelado:
    """O congelamento lido de `configs/judge_frozen.json`, com o sha já conferido."""

    caminho: Path
    scorer_version: str
    sha256: str
    congelado_em: datetime
    judge_model: ModelConfig
    rubrica_sha: str
    fewshot_ids: tuple[str, ...] = ()
    fewshot_origem: str | None = None


@dataclass(frozen=True)
class DispensaDeCongelamento:
    """A bateria declarou, por escrito, que roda sem judge congelado — e por quê.

    O motivo é obrigatório e não vazio porque é ele que separa esta declaração de um esquecimento:
    ninguém escreve uma frase sem querer. Ele vai para o `manifest.json`, onde quem for ler o
    resultado da bateria vê, no mesmo lugar em que vê o sha das outras, que ali não havia sha.
    """

    motivo: str


DeclaracaoDoJudge = JudgeCongelado | DispensaDeCongelamento


# ---------------------------------------------------------------------------
# O sha
# ---------------------------------------------------------------------------


def material_assinado(documento: dict[str, Any]) -> str:
    """A serialização canônica que o sha256 assina.

    JSON com chaves ordenadas e sem espaço: reformatar o arquivo, reordenar as chaves ou trocar
    a indentação não muda o sha; mudar uma palavra da rubrica, acrescentar um few-shot ou trocar
    o snapshot do modelo muda.
    """
    faltando = sorted(set(CAMPOS_ASSINADOS) - set(documento))
    if faltando:
        raise ErroDeJudgeCongelado(
            f"não dá para assinar um congelamento sem {faltando}: o sha assina "
            f"{list(CAMPOS_ASSINADOS)}, e assinar um subconjunto produziria um hash que confere "
            "com um arquivo incompleto"
        )
    assinado = {campo: documento[campo] for campo in CAMPOS_ASSINADOS}
    assinado["judge_model"] = _snapshot_do_modelo(documento["judge_model"]).model_dump(
        mode="json"
    )
    return json.dumps(assinado, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha_do_judge(documento: dict[str, Any]) -> str:
    """O sha256 do conteúdo congelado. É o que a outra metade da T23 grava em `sha256`."""
    return hashlib.sha256(material_assinado(documento).encode("utf-8")).hexdigest()


def sha_da_rubrica(rubrica: str) -> str:
    """O `ScorerVersion.rubrica_sha`: a rubrica sozinha, para que ela possa ser rastreada
    entre versões do judge que só mexeram no prompt ou nos few-shots."""
    return hashlib.sha256(rubrica.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------


def carregar_judge_congelado(caminho: Path) -> JudgeCongelado:
    """Lê o arquivo do judge congelado e confere o sha. Erro é `ErroDeJudgeCongelado`."""
    if not caminho.exists():
        raise ErroDeJudgeCongelado(
            f"o judge congelado declarado não existe: {caminho}. Ele é pré-requisito da "
            "execução, não subproduto dela (`METRICAS §9.3`) — a bateria morre aqui, no "
            "carregamento, e não às 4 da manhã com 200 traces já gravados"
        )

    try:
        bruto = json.loads(caminho.read_text(encoding="utf-8"))
    except json.JSONDecodeError as erro:
        raise ErroDeJudgeCongelado(f"{caminho}: não é JSON válido — {erro}") from erro
    if not isinstance(bruto, dict):
        raise ErroDeJudgeCongelado(f"{caminho}: esperava um objeto JSON no topo")

    documento: dict[str, Any] = bruto

    desconhecidos = sorted(set(documento) - CAMPOS_DO_JUDGE)
    if desconhecidos:
        # Mesmo argumento do `CAMPOS_DA_BATERIA`, e um a mais: campo não previsto é campo NÃO
        # assinado, e conteúdo que anda junto do judge sem entrar no sha é exatamente o que o
        # congelamento existe para impedir. Acrescentar campo é ato deliberado, aqui e lá.
        raise ErroDeJudgeCongelado(
            f"{caminho}: campo(s) desconhecido(s) {desconhecidos}. Aceitos: "
            f"{sorted(CAMPOS_DO_JUDGE)}. Campo fora da lista não entra no material assinado, "
            "e conteúdo não assinado viajando dentro de um arquivo chamado 'frozen' é a "
            "própria falha que o sha existe para impedir"
        )

    faltando = sorted(CAMPOS_OBRIGATORIOS - set(documento))
    if faltando:
        raise ErroDeJudgeCongelado(
            f"{caminho}: falta(m) {faltando}. Um congelamento é prompt + rubrica + few-shots + "
            "snapshot do modelo + o sha256 que os assina; sem qualquer uma das partes não há o "
            "que conferir depois"
        )

    _exigir_texto(documento, "scorer_version", caminho)
    _exigir_texto(documento, "prompt", caminho)
    _exigir_texto(documento, "rubrica", caminho)

    fewshot_ids = _ids_dos_fewshots(documento["fewshots"], caminho)
    modelo = _snapshot_do_modelo_validado(documento["judge_model"], caminho)
    congelado_em = _congelado_em(documento["congelado_em"], caminho)

    declarado = documento["sha256"]
    if not isinstance(declarado, str) or len(declarado) != 64:
        raise ErroDeJudgeCongelado(
            f"{caminho}: `sha256` precisa ser o hexadecimal de 64 caracteres do sha256, veio "
            f"{declarado!r}"
        )

    calculado = sha_do_judge(documento)
    if declarado.lower() != calculado:
        raise ErroDeJudgeCongelado(
            f"{caminho}: o `sha256` declarado ({declarado}) não confere com o conteúdo "
            f"({calculado}). Um judge cujo sha não bate é PIOR que nenhum judge congelado: ele "
            "parece congelado para quem lê o arquivo e para todo `ScoreRecord` que copiar o "
            "campo, e é a única forma de o pré-registro de `METRICAS §9.3` ser violado sem "
            f"deixar rastro. Assinados: {list(CAMPOS_ASSINADOS)}"
        )

    rubrica_sha = _rubrica_sha(documento, caminho)
    origem = documento.get("fewshot_origem")
    if origem is not None and not isinstance(origem, str):
        raise ErroDeJudgeCongelado(f"{caminho}: `fewshot_origem` precisa ser texto")

    return JudgeCongelado(
        caminho=caminho,
        scorer_version=documento["scorer_version"],
        sha256=calculado,
        congelado_em=congelado_em,
        judge_model=modelo,
        rubrica_sha=rubrica_sha,
        fewshot_ids=fewshot_ids,
        fewshot_origem=origem,
    )


def interpretar_declaracao(cru: Any, *, raiz: Path, contexto: str) -> DeclaracaoDoJudge:
    """Traduz o campo `judge:` do YAML da bateria numa das duas declarações.

    Duas formas, e nenhuma terceira:

        judge: configs/judge_frozen.json          # o congelamento, conferido
        judge:
          sem_congelamento: "por que esta bateria roda sem ele"

    A ausência do campo **não** é uma delas — quem trata disso é `matriz.carregar_bateria`,
    porque a mensagem certa depende de qual bateria é.
    """
    if isinstance(cru, str):
        if not cru.strip():
            raise ErroDeJudgeCongelado(f"{contexto}: `judge` veio vazio")
        caminho = Path(cru)
        return carregar_judge_congelado(caminho if caminho.is_absolute() else raiz / caminho)

    if isinstance(cru, dict):
        chaves = sorted(cru)
        if chaves != [CHAVE_DA_DISPENSA]:
            raise ErroDeJudgeCongelado(
                f"{contexto}: `judge` em forma de mapeamento aceita exatamente "
                f"`{CHAVE_DA_DISPENSA}: <motivo>`, veio {chaves}"
            )
        motivo = cru[CHAVE_DA_DISPENSA]
        if not isinstance(motivo, str) or not motivo.strip():
            raise ErroDeJudgeCongelado(
                f"{contexto}: `{CHAVE_DA_DISPENSA}` exige o motivo por escrito. O motivo é o "
                "que separa 'esta bateria não precisa de judge congelado' de 'esqueci de "
                "declarar' — e só o primeiro é uma decisão"
            )
        return DispensaDeCongelamento(motivo=motivo.strip())

    raise ErroDeJudgeCongelado(
        f"{contexto}: `judge` precisa ser o caminho do congelamento ou "
        f"`{{{CHAVE_DA_DISPENSA}: <motivo>}}`, veio {type(cru).__name__}"
    )


# ---------------------------------------------------------------------------
# Detalhes de validação
# ---------------------------------------------------------------------------


def _exigir_texto(documento: dict[str, Any], campo: str, caminho: Path) -> None:
    valor = documento[campo]
    if not isinstance(valor, str) or not valor.strip():
        raise ErroDeJudgeCongelado(f"{caminho}: `{campo}` precisa ser texto não vazio")


def _ids_dos_fewshots(crus: Any, caminho: Path) -> tuple[str, ...]:
    """Os few-shots, com id. Lista **vazia é aceita e não é o mesmo que campo ausente**.

    Um judge sem few-shot é um desenho possível (e a T20 registra que o prompt cego já é grande
    por causa deles); um judge cujo arquivo não diz nada sobre few-shots é um arquivo incompleto.
    """
    if not isinstance(crus, list):
        raise ErroDeJudgeCongelado(
            f"{caminho}: `fewshots` precisa ser uma lista (vazia se o judge não usa nenhum — "
            "que é diferente de o campo não existir)"
        )
    ids: list[str] = []
    for posicao, exemplo in enumerate(crus):
        if not isinstance(exemplo, dict) or not isinstance(exemplo.get("id"), str):
            raise ErroDeJudgeCongelado(
                f"{caminho}: `fewshots[{posicao}]` precisa ser um objeto com `id` — é o `id` "
                "que vai para `ScorerVersion.fewshot_ids` e permite dizer qual exemplo mudou "
                "entre duas versões do judge"
            )
        ids.append(exemplo["id"])
    repetidos = sorted({i for i in ids if ids.count(i) > 1})
    if repetidos:
        raise ErroDeJudgeCongelado(f"{caminho}: `fewshots` repete o(s) id(s) {repetidos}")
    return tuple(ids)


def _snapshot_do_modelo(cru: Any) -> ModelConfig:
    """O snapshot normalizado, ou `ErroDeJudgeCongelado` — nunca uma `ValidationError` crua.

    `sha_do_judge` é API pública: é ela que o script de congelamento da T23 chama para gravar
    o campo `sha256`. Deixar a exceção do Pydantic vazar por ali daria, a quem congela o judge
    com um snapshot incompleto, um stack trace sobre `served_by` em vez de uma frase dizendo
    que o `judge_model` está inválido — e o congelamento é justamente o ato que não pode
    falhar de forma obscura.
    """
    if isinstance(cru, ModelConfig):
        return cru
    if not isinstance(cru, dict):
        raise ErroDeJudgeCongelado(
            f"`judge_model` precisa ser um objeto com os campos de `ModelConfig`, veio "
            f"{type(cru).__name__}"
        )
    try:
        return ModelConfig(**cru)
    except (TypeError, ValueError) as erro:
        raise ErroDeJudgeCongelado(f"`judge_model` inválido — {erro}") from erro


def _snapshot_do_modelo_validado(cru: Any, caminho: Path) -> ModelConfig:
    """O mesmo de `_snapshot_do_modelo`, com o arquivo na mensagem.

    Quem carrega tem um caminho para citar; quem só calcula o sha não tem.
    """
    try:
        return _snapshot_do_modelo(cru)
    except ErroDeJudgeCongelado as erro:
        raise ErroDeJudgeCongelado(f"{caminho}: {erro}") from erro


def _congelado_em(cru: Any, caminho: Path) -> datetime:
    if isinstance(cru, datetime):
        return cru
    if not isinstance(cru, str):
        raise ErroDeJudgeCongelado(
            f"{caminho}: `congelado_em` precisa ser um instante ISO-8601"
        )
    try:
        return datetime.fromisoformat(cru)
    except ValueError as erro:
        raise ErroDeJudgeCongelado(
            f"{caminho}: `congelado_em` {cru!r} não é ISO-8601 — {erro}"
        ) from erro


def _rubrica_sha(documento: dict[str, Any], caminho: Path) -> str:
    calculado = sha_da_rubrica(documento["rubrica"])
    declarado = documento.get("rubrica_sha")
    if declarado is None:
        return calculado
    if not isinstance(declarado, str) or declarado.lower() != calculado:
        raise ErroDeJudgeCongelado(
            f"{caminho}: `rubrica_sha` declarado ({declarado!r}) não confere com a rubrica do "
            f"arquivo ({calculado}). Ele é derivado; declarado errado, aponta para uma rubrica "
            "que não é a que está aqui"
        )
    return calculado


__all__ = [
    "CAMPOS_ASSINADOS",
    "CAMPOS_DO_JUDGE",
    "CAMPOS_OBRIGATORIOS",
    "CHAVE_DA_DISPENSA",
    "DeclaracaoDoJudge",
    "DispensaDeCongelamento",
    "ErroDeJudgeCongelado",
    "JudgeCongelado",
    "carregar_judge_congelado",
    "interpretar_declaracao",
    "material_assinado",
    "sha_da_rubrica",
    "sha_do_judge",
]
