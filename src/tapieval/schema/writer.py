"""
Escrita de trace — append-only, um evento JSON por linha.

O writer é o único ponto de escrita do trace e não sabe pontuar nada: ele registra
fatos. Score nenhum entra aqui (ARQUITETURA §5, decisão 1).

Dois emissores escrevem no mesmo arquivo — o servidor MCP, por notificação, e o
harness do cliente (ARQUITETURA §4.3). Por isso `emit` não atribui `seq`: a ordem
absoluta vem no evento, atribuída por quem o produziu, e a ordenação é
responsabilidade do reader.
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

from tapieval.schema.trace import TraceEvent


class TraceWriter:
    """
    Escreve os eventos de uma run em `<run_dir>/traces/<run_id>.jsonl` e os payloads
    grandes em `<run_dir>/blobs/<sha256>.txt`.

    `run_dir` é o diretório do experimento (`runs/<experiment_id>/`); os subdiretórios
    são criados se não existirem.
    """

    def __init__(self, run_dir: str | Path, run_id: str) -> None:
        self.run_dir = Path(run_dir)
        self.run_id = run_id
        self.traces_dir = self.run_dir / "traces"
        self.blobs_dir = self.run_dir / "blobs"
        self.trace_path = self.traces_dir / f"{run_id}.jsonl"

        self.traces_dir.mkdir(parents=True, exist_ok=True)
        self.blobs_dir.mkdir(parents=True, exist_ok=True)

        # Serializa emissões concorrentes DESTA instância. Não protege contra duas
        # instâncias escrevendo no mesmo arquivo — o que é o caso quando o servidor MCP
        # e o harness do cliente criam cada um o seu writer. Aí a garantia vem do modo
        # append do POSIX, que é atômico para linhas menores que PIPE_BUF (4KB). Payload
        # grande vai para blob e a linha carrega só o sha, então o limite é respeitado
        # por construção — mas quem instrumentar o writer (T14, T35) precisa saber disso
        # antes de acrescentar estado mutável compartilhado aqui.
        self._trava = threading.Lock()

    def emit(self, event: TraceEvent) -> None:
        """Acrescenta um evento ao trace da run.

        Abre o arquivo a cada evento em vez de manter um descritor aberto: uma run
        que morre no meio deixa um trace completo até o último evento emitido, que é
        justamente o que se quer investigar quando uma run morre.
        """
        linha = event.model_dump_json()
        with self._trava, self.trace_path.open("a", encoding="utf-8") as arquivo:
            arquivo.write(linha + "\n")

    def blob(self, texto: str) -> str:
        """Grava um texto grande em `blobs/<sha256>.txt` e devolve o sha256.

        Endereçado por conteúdo: o mesmo texto sempre produz o mesmo hash e um único
        arquivo. Prompts de sistema se repetem em toda run da mesma variante, então a
        deduplicação é a diferença entre um blob e centenas de cópias idênticas.
        """
        conteudo = texto.encode("utf-8")
        sha = hashlib.sha256(conteudo).hexdigest()
        destino = self.blobs_dir / f"{sha}.txt"
        if not destino.exists():
            destino.write_bytes(conteudo)
        return sha
