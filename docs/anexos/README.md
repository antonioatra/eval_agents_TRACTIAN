# Anexos

O `docs/` guarda os dois artefatos que se leem primeiro — [`ARQUITETURA.md`](../ARQUITETURA.md)
e a apresentação. Tudo o que os sustenta está aqui.

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

Os JSON que os notebooks produzem e que o README, a apresentação e a página do copiloto citam.
São **derivados**: `make repro` os regrava a partir de `runs/`, e número digitado à mão aqui
envelhece na primeira vez que a bateria muda.

## `../capturas/`

As capturas de tela da aplicação que o deck usa (`docs/capturas/`). Ficam **fora de `figures/`**
de propósito: figura sai de notebook e `make repro` a regrava conferindo o disco; captura de tela
sai de `make copiloto` com a API e a GPU no ar, e nenhum alvo sabe refazê-la. Misturar as duas
afrouxaria, para o deck inteiro, o teste que exige que toda figura citada seja regravável.

O slide que as usa **degrada para uma moldura vazia** quando o arquivo não está no disco — quem
clona o repositório para reger o deck não tem por que ter a captura, e um `make deck` que morresse
por um PNG de tela deixaria a banca sem o arquivo inteiro por causa de um slide.
