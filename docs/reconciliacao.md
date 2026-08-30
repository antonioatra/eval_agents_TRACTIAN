# Reconciliação corpus × API real

Levantamento da T3 (item 3), medido em **15/08** contra a API no ar por
`notebooks/nb01_exploracao_api.ipynb`. **Só levanta e propõe; não altera cenário** — toda decisão
aqui é de curadoria humana.

> ✅ **A reconciliação fechou em 24/08** (tag `corpus-v2-reconciliado`). Este documento é o
> **levantamento**, não uma lista de pendências: ele fica porque é a evidência de onde cada
> correção do corpus saiu. O nome antigo do arquivo (`reconciliacao_pendente.md`) sobreviveu ao
> próprio estado e dizia o contrário.
>
> **Onde foi parar o que está aqui:**
>
> | daqui | virou |
> |---|---|
> | §2 — `analyses[]` satisfeita por **olhar**, não por **achar** | a saída 1, adotada: `n2._evidencia_coberta` trata a coleção como coberta ao ser observada, com o raciocínio deste §2 no comentário. Exigir item não-vazio tornaria a evidência incobrível no ambiente canônico do próprio cenário |
> | D1 — o corte de `partial` é do **endpoint**, não da categoria | risco **X17**, e `campos_ausentes` passou a ser calculado por endpoint |
> | D2 — a base tem **5** documentos, não 4 | corrigido no corpus v2 (o `kb_guid_003` entrou, e `aut_03` passou a citar os 5) |
> | D6 — duas grafias para a mesma evidência | padronizado em `knowledge.results[]` em 19/08; duas grafias davam dois critérios de cobertura, e o cenário que escrevesse a fraca era conferido mais frouxo sem aviso |
> | D8 — `yaml.safe_load` perde o `getAsset` | risco **X10**; o catálogo de tools do MCP não sai de `safe_load` |
>
> **§1 continua sendo um resultado, e não um arquivo vazio:** 24/24 cenários com a `env_seed`
> canônica satisfazendo os `modos_exigidos`, 152 seeds equivalentes conferidas, e 67 000
> comparações entre a réplica de `resolve_mode` e a API real com **zero divergências**.

---

## 1. Ambiente: nada a reconciliar

**Nenhum cenário diverge no eixo `env_seed` × `modos_exigidos`.** Isto é um resultado, não um
arquivo vazio:

| Verificação | Escopo | Resultado |
|---|---|---|
| `env_seed` canônica satisfaz todos os `modos_exigidos` | 24/24 cenários | **passa** |
| `seeds_equivalentes` declaradas nos YAMLs satisfazem os mesmos `modos_exigidos` | 152 seeds em 24 cenários | **passam todas** |
| réplica de `resolve_mode` (`scripts/validar_cenarios.py`) × API real | 67 pares `(recurso, categoria)` × 1000 seeds = 67 000 chamadas | **0 divergências** |
| tool de ação em `tools_esperadas` × permissão do `user_id` declarado | 24 cenários | **casam todas** |
| evidência obrigatória existe e chega preenchida na `env_seed` canônica | 113 evidências | 109 passam, 4 em §2 |

Não há **nenhuma** proposta de "trocar a seed" nesta leva. A varredura completa (contagem e as 8
primeiras seeds válidas por cenário) está em `docs/catalogo_respostas.md §7`; ela **confirma** o
que os YAMLs já declaravam em vez de corrigi-lo.

---

## 2. Cenários que não fecham — e nenhum deles por causa da seed

Quatro cenários prometem a evidência `analyses[]` e recebem uma listagem que não sustenta a
leitura ingênua de "a lista veio preenchida". Nos quatro a seed está **certa** e a montagem está
**certa**: o que não fecha é a métrica.

| Cenário | O que o YAML promete | O que a API devolve na `env_seed` declarada | Existe outra seed? | Proposta |
|---|---|---|---|---|
| `aut_06_premissa_falsa` | evidência `analyses[]`; `asset_B211/analyses → complete` | `s004` → `mode=complete`, `data.analyses == []` | **não** — `asset_B211` não tem nenhuma análise no dataset, em nenhuma seed | **nem seed nem fixture.** A lista vazia é o dado certo e o YAML já a documenta (`estado_esperado.analises: []`, `CENARIOS §7.7`). Corrigir é decidir como N1.3 lê `analyses[]` — ver §3 |
| `aut_08_acao_errada_sem_permissao` | idem; `asset_M428/analyses → complete` | `s025` → `mode=complete`, `data.analyses == []` | **não** — `asset_M428` também não tem análises | idem |
| `cen_01_quebra_sem_aviso` | evidência `analyses[]`; `asset_G501/analyses → inconclusive` | `s002` → `mode=inconclusive`, `data == {"inconclusive": true}`; a chave `analyses` **não existe** | **não, e por construção**: `analyses=inconclusive` é override fixo de `asset_G501` em `data/seed.json` — `resolve_mode` retorna antes de olhar a seed | **irreparável por seed, e correto assim.** O cenário existe justamente para testar isto (`CENARIOS §7.9`: o gabarito proíbe citar id de análise, porque citar um seria alucinação C3). Nada a mudar no cenário |
| `cen_10_escalar_para_humano` | idem; mesmo ativo e mesmo override | `s001` → `mode=inconclusive`, `data == {"inconclusive": true}` | **não**, mesmo motivo | idem |

**O que decidir (não é meu):** `analyses[]` é uma evidência satisfeita por **olhar**, não por
**achar**. Se N1.3 for implementada como "a listagem veio não-vazia", ela reprova o agente correto
nos quatro cenários acima — 4 dos 24, sendo 3 no split de teste. A leitura que os quatro gabaritos
pressupõem é: *o agente chamou `list_analyses` no ativo certo e usou o retorno (inclusive vazio ou
inconclusivo) na resposta.*

Duas saídas possíveis, ambas de curadoria:

1. **Definir na semântica de N1.3** que evidência terminada em `[]` é satisfeita pela *chamada
   bem-sucedida*, não pelo conteúdo. Custo: uma linha em `METRICAS`, zero mudança no corpus.
   Herda: **T9**.
2. **Marcar no YAML** que a evidência é de listagem vazia (um campo novo em `gabarito`). Custo:
   mudança de schema em 4 arquivos + validador. Mais explícito, mais caro.

Sou a favor da 1: o schema já separa `EVIDENCIAS_SEM_CAMPO` justamente por isso
(`scripts/validar_cenarios.py:70`), só falta a métrica honrar a separação.

> **O que eu deliberadamente não propus:** trocar `asset_B211` / `asset_M428` por ativos que
> tenham análises. Resolveria o vermelho e destruiria os dois cenários — em AUT-06 a ausência de
> análise *é* parte da evidência contra a premissa falsa, e em AUT-08 o par simétrico com CEN-16
> depende do ativo. Recriar cenário até tudo passar transforma o corpus numa descrição do que esta
> API deixa fácil.

---

## 3. Divergências documento × dado real

Nenhuma exige mudar cenário. Todas exigem alguém decidir se corrige o documento ou o código que
o lê.

| # | Onde | O documento diz | O dado diz | Proposta | Herda |
|---|---|---|---|---|---|
| D1 | `CENARIOS §5.4`, `§7.5` | `partial` na categoria `analyses` remove `evidence` e `limitations` | verdade só em `GET /analyses/{id}`. Em `GET /assets/{id}/analyses` o corte é aplicado às chaves de primeiro nível (`{"analyses": [...]}`) e **nada é removido** — os itens chegam com `evidence` e `limitations` | corrigir as duas tabelas: o corte é função do **endpoint**, não da categoria. `list_analyses` entra na lista de aviso falso ao lado de `asset`, `spectrum`, `company`, `assets`, `knowledge` | **T7** (`campos_ausentes`), docs |
| D2 | `CENARIOS §5.3` | "a base de conhecimento tem **4** documentos", listando `kb_proc_001`, `kb_glos_001`, `kb_guid_001`, `kb_guid_002` | são **5**: existe `kb_guid_003`, *"Falhas elétricas em motores"* (`tags: eletrica, fft, motor`) | corrigir a contagem. Vale reexaminar CEN-05 (elétrica × mecânica): esse documento é diretamente pertinente e é o **único dos cinco que nenhum cenário cita** (os outros quatro aparecem em AUT-03, CEN-04, CEN-11, CEN-12, CEN-13) — pode ser cobertura perdida, não erro | **T4** (matriz de cobertura) |
| D3 | `ARQUITETURA §3.4`, matriz Orientar/Agir/Escalar | linha **Conflito**: orientar "nunca", agir "nunca", escalar sim | CEN-03 e CEN-06 rodam com `analyses=conflict` (override permanente de `asset_S420` e `asset_M205`) e os gabaritos pedem **orientar** (`insight_invalidado_por_baseline`, `conflito_resolvido_por_evidencia`); CEN-16 pede **agir** condicionado à permissão | distinguir no texto **`StatusRetorno.CONFLITO`** (o campo `mode`, uma flag num único retorno) de **"conflito entre fontes irresolvido"** (contradição entre endpoints que o agente não desempatou). A matriz fala do segundo; lida literalmente contra o primeiro, ela reprova a resposta certa em 3 cenários — e como o override é permanente, proibiria orientar sobre esses ativos para sempre | **T9** / rubrica |
| D4 | `ARQUITETURA §3.4`, mesma matriz | linha **Indisponível**, coluna Escalar: "após **retries**, alerta de falha" | o próprio §3.4, revisto em 14/08 logo abaixo da matriz: *"Retry é zero tentativas, não três"* | a célula não foi atualizada junto com o parágrafo. Contradição interna ao mesmo parágrafo — trocar por "na primeira, alerta de falha" | docs |
| D5 | `ARQUITETURA §3.4`, "Conflito entre fontes" | "gasta *uma* chamada extra tentando desempatar" | se a chamada extra for ao **mesmo** endpoint com os mesmos argumentos, o retorno é bit a bit idêntico (modo é função pura de `(seed, recurso, categoria)`) — é a mesma armadilha do retry, e conta como P5 | qualificar: a chamada de desempate só vale se for a **outra fonte** (é o que CEN-03/CEN-06 cobram, com o espectro desempatando) | **T9** |
| D6 | schema de cenário | `evidencias_obrigatorias` usa duas grafias para a mesma coisa: `knowledge` (CEN-11/12/13) e `knowledge.results[]` (AUT-03) | `validar_cenarios.py` aceita as duas por caminhos diferentes — a primeira por `EVIDENCIAS_SEM_CAMPO`, a segunda porque `results[]` está em `CAMPOS_POR_RECURSO["knowledge"]` | escolher uma grafia. Enquanto houver duas, N1.3 precisa tratar as duas ou vai medir AUT-03 de um jeito e CEN-11/12/13 de outro | **T9**, `scenarios/README` |
| D7 | duplicação de artefato | — | o contrato OpenAPI existe em **duas cópias bit a bit idênticas**: `inteli-tractian-project/agent-input/api-contract.openapi.yaml` (a que o enunciado aponta) e `inteli-tractian-project/docs/api-contract.openapi.yaml` (a que `validar_cenarios.py` lê) | eleger uma fonte única antes que divirjam. Se divergirem, o corpus valida contra um contrato e o agente recebe outro, sem nenhum teste acusando | **T13** |
| D8 | X10, ver `catalogo_respostas.md §6` | — | `yaml.safe_load` sobre o contrato devolve 17 operações e **perde `getAsset`** (chave `/assets/{assetId}` declarada duas vezes) | o catálogo de tools do MCP não pode sair de `safe_load`. Contorno verificado no `nb01` §1 (loader que funde chaves duplicadas). `validar_cenarios.py` já é imune porque usa regex | **T13** |
| D9 | `CENARIOS §5.4` (implícito) | — | `get_baseline`, `get_spectrum` e `get_data_quality` devolvem `mode=inconclusive` **antes** de consultar a seed quando o store não acha a linha, com uma terceira forma de corpo (`{"spectrum": null}`). Atinge `asset_M102`/`spectrum` e qualquer `point_id` inexistente | registrar a terceira forma de `inconclusive` no classificador. Nenhum cenário depende do par afetado (verificado) | **T7** |

---

## 4. O que ficou fora deste levantamento

- **Item 4 da T3** (fora do meu escopo por definição).
- **Coerência semântica entre `deve_mencionar` e o dado** — conferi que os campos citados existem
  e chegam preenchidos, não que os *valores* citados em prosa (ex.: "subharmônico em 300 Hz,
  0.7 mm/s") batem com o que a API devolve. É trabalho de curadoria por cenário, não de varredura.
- **Cenários inviáveis:** continuam **zero**. Nada no levantamento justificou declarar um.
