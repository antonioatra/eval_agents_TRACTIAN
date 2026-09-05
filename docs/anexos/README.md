# Anexos

O `docs/` guarda o artefato que se lê primeiro — [`ARQUITETURA.md`](../ARQUITETURA.md).
Tudo o que o sustenta está aqui.

## Documentos de fundo

| Arquivo | Fonte de verdade de |
|---|---|
| [`METRICAS.md`](METRICAS.md) | métricas, camadas de julgamento, severidade, protocolo de execução |
| [`CENARIOS.md`](CENARIOS.md) | os 24 cenários, gabaritos, split, seeds canônicas |
| [`GLOSSARIO.md`](GLOSSARIO.md) | os termos do case; é por onde começa quem chega agora |
| [`REPRODUZIR.md`](REPRODUZIR.md) | reprodutibilidade ponta a ponta, máquina limpa |

O enunciado do parceiro é o PDF `[UPDATED] Tapi Inteli  Tractian.pdf`.

## `apuracao/`

Os registros de medição — o que foi medido, quando e contra o quê. É onde a `ARQUITETURA.md`
vai buscar número em vez de repeti-lo: `piloto.md`, `dimensionamento.md`, `judge.md`,
`taxonomia_erros.md` (**gerado** pelo `nb06`, não escrito à mão), `catalogo_respostas.md`,
`reconciliacao.md`, `tool_calling_baseline.md`, `limites_free_tier.md`, `migracao_vertex.md`,
`lacunas_justificadas.md`.

## `resultados/`

Os JSON que os notebooks produzem e que o README e a página do copiloto citam.
São **derivados**: `make repro` os regrava a partir de `runs/`, e número digitado à mão aqui
envelhece na primeira vez que a bateria muda.

## `../capturas/`

As capturas de tela da aplicação (`docs/capturas/`). Ficam **fora de `figures/`** de propósito:
figura sai de notebook e `make repro` a regrava conferindo o disco; captura de tela sai de
`make copiloto` com a API e a GPU no ar, e nenhum alvo sabe refazê-la. Misturar as duas
afrouxaria o teste que exige que toda figura citada seja regravável.

Elas são **prova de execução, não ilustração**: mostram a aplicação rodando com os dois serviços
no ar. Quem clona o repositório não precisa refazê-las para reproduzir número nenhum — nenhum
resultado depende delas.
