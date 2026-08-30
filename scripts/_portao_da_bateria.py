"""O portão entre uma bateria e a seguinte. Operador da noite, não peça do framework.

O critério é o do `PLANO` T24-26: nº de traces == nº de células, e `run_end` com erro abaixo
de 5%. A parte que engana é a segunda, e ela custou uma reescrita: **no TRACE, falha do agente
e falha do harness saem as duas como `status="error"`** (`runner.py` converte
`falha_do_instrumento` em `error` ao emitir o `RunEnd`). Contar `error` no trace reprovaria a
bateria por ParseErro do modelo — que é RESULTADO do experimento e entra na taxonomia como
medida, não defeito nosso.

Quem separa os dois é o manifesto: lá `falha_do_instrumento` continua com o próprio nome, e
`valida=false` marca trace estruturalmente quebrado (A7). São esses dois que o portão conta.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tapieval.runner.manifesto import ler_manifesto  # noqa: E402

TETO_DE_FALHA = 0.05


def main() -> int:
    diretorio = Path(sys.argv[1])
    manifesto = ler_manifesto(diretorio)
    if manifesto is None:
        print(f"PORTAO: {diretorio} não tem manifesto — a bateria nunca rodou.")
        return 1

    declaradas = len(manifesto.celulas)
    faltantes = manifesto.faltantes()
    invalidas = manifesto.invalidas()
    instrumento = [r for r in manifesto.runs.values() if r.status == "falha_do_instrumento"]

    from collections import Counter

    print(f"células declaradas : {declaradas}")
    print(f"com registro       : {len(manifesto.runs)}")
    print(f"status             : {dict(Counter(r.status for r in manifesto.runs.values()))}")
    print(f"falha_do_instrumento: {len(instrumento)}")
    print(f"traces inválidos (A7): {len(invalidas)}")

    if faltantes:
        print(
            f"PORTAO REPROVA: {len(faltantes)} células sem registro. Bateria INCOMPLETA — "
            "reportada como tal, não descartada, e a próxima não roda em cima disso."
        )
        return 1

    defeituosas = len(instrumento) + len(invalidas)
    fracao = defeituosas / declaradas
    if fracao >= TETO_DE_FALHA:
        print(
            f"PORTAO REPROVA: defeito nosso em {fracao:.1%} das células "
            f"({defeituosas}/{declaradas}), teto é {TETO_DE_FALHA:.0%}."
        )
        return 1

    print(f"PORTAO OK: defeito nosso em {fracao:.1%} ({defeituosas}/{declaradas}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
