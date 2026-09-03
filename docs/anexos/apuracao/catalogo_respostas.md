# Catálogo de respostas da API

O que cada endpoint devolve, em cada modo, **medido contra a API no ar** — não lido do contrato.
Gerado por `notebooks/nb01_exploracao_api.ipynb` (15/08). As tabelas de campo e a varredura de
seeds são cópias das saídas daquele notebook; ele não escreve este arquivo.

Público: T7 (classificador de status), T13 (servidor MCP) e T9 (implementação das métricas).

---

## 1. O envelope

Toda **leitura** devolve `{"mode": …, "notes": …, "data": …}`, HTTP 200, com uma exceção
(`GET /users/me`, §5). As **ações** (POST/PATCH) devolvem `ActionResult`, sem envelope.

```json
{"mode": "partial", "notes": "Informação parcial: campos ausentes ['features']", "data": {...}}
```

> **O `mode` chega explícito. T7 lê o campo — não infere.** O que precisa ser inferido é
> apenas *quais campos sumiram*, e nem sempre sumiu algum. Ver §3 e §4.

`mode` é função pura de `(seed, resource, category)`; não há sorteio por chamada. Duas chamadas
idênticas devolvem sempre o mesmo corpo. Seeds especiais: `seed=complete` força `complete`,
`seed=degraded` força `partial`, e omitir a seed dá *outro* ambiente fixo (`hash("noseed"|…)`),
não um ambiente aleatório. **Overrides de `data/seed.json` vencem tudo, inclusive `seed=complete`.**

---

## 2. Campos sempre presentes, por endpoint

`complete` é a referência: todo campo abaixo aparece em `data` (para as listagens, `data` é
`{"<coleção>": [...]}` e os campos listados são os de cada item).

| Endpoint | Tool (`operationId` snake_case) | Categoria | Campos em `complete` |
|---|---|---|---|
| `GET /companies/{companyId}` | `get_company` | `company` | `id`, `name`, `segment`, `timezone` |
| `GET /companies/{companyId}/assets` | `list_assets_by_company` | `assets` | `assets[]` (itens = payload de ativo, sem `points`) |
| `GET /users/me` | `get_current_user` | — | `id`, `name`, `role`, `permissions`, `company_id` — **sem envelope** |
| `GET /assets/{assetId}` | `get_asset` | `asset` | `id`, `name`, `company_id`, `criticality`, `plant`, `line`, `parent_asset_id`, `machine_type`, `rotation_rpm`, `bearing_pn`, `bpfo_hz`, `bpfi_hz`, `bsf_hz`, `ftf_hz`, `line_frequency_hz`, `sensor_status`, `points` |
| `GET /assets/{assetId}/analyses` | `list_analyses` | `analyses` | `analyses[]` (itens = os 13 campos de `get_analysis`) |
| `GET /analyses/{analysisId}` | `get_analysis` | `analyses` | `id`, `asset_id`, `point_id`, `type`, `detection_mode`, `severity`, `confidence`, `baseline_state_at_detection`, `evidence`, `limitations`, `model_version`, `created_at`, `status` |
| `GET /assets/{assetId}/baseline` | `get_baseline` | `baseline` | `id`, `asset_id`, `point_id`, `state`, `detection_mode`, `learnable`, `established_at`, `invalidated_at`, `invalidation_reason`, `features` |
| `GET /assets/{assetId}/rms` | `get_rms_series` | `rms` | `asset_id`, `point_id`, `unit`, `baseline_reference`, `baseline_state`, `alarm_threshold`, `samples` |
| `GET /assets/{assetId}/spectrum` | `get_spectrum` | `spectrum` | `asset_id`, `point_id`, `collected_at`, `peaks`, `bands_missing` |
| `GET /assets/{assetId}/data-quality` | `get_data_quality` | `data_quality` | `asset_id`, `point_id`, `completeness`, `freshness_minutes`, `snr_db`, `staleness_flag` |
| `GET /models/{modelId}` | `get_model` | `model` | `id`, `version`, `coverage`, `requirements`, `processing_state`, `last_run_at` |
| `GET /knowledge/search` | `search_knowledge` | `knowledge` | `results[]` (itens: `id`, `type`, `title`, `body`, `tags`) |
| `GET /knowledge/{docId}` | `get_knowledge_doc` | `knowledge` | `id`, `type`, `title`, `body`, `tags` |

Ações — todas devolvem `{"accepted": true, "action_id": "act_<hex8>", "message": "…"}`:

| Endpoint | Tool | Permissão exigida |
|---|---|---|
| `PATCH /assets/{assetId}` | `update_asset_config` | `action_high` |
| `POST /analyses/{analysisId}/reprocess` | `reprocess_analysis` | `action_low` |
| `POST /analyses/{analysisId}/request-specialist` | `request_specialist_analysis` | `action_low` |
| `POST /models/{modelId}/request-retraining` | `request_retraining` | `action_high` |
| `POST /cases/{caseId}/escalate` | `escalate_case` | `escalate` |

Todas exigem `justification` com ≥ 20 caracteres no corpo. **A permissão é checada antes da
justificativa**: sem a permissão, uma justificativa curta produz 403, não 400.

---

## 3. O que muda em cada modo

| Modo | Forma do `data` | `notes` (texto fixo) |
|---|---|---|
| `complete` | payload íntegro | `null` |
| `partial` | payload menos `_PARTIAL_DROP[categoria]` — **e nada, se a categoria não tem entrada** | `Informação parcial: campos ausentes [...]` ou `… (detalhes)` |
| `inconclusive` (categoria instável) | `{"inconclusive": true}` **+ `asset_id`** se o payload original o tinha | `Resultado inconclusivo: dados insuficientes para concluir.` |
| `inconclusive` (categoria estável) | payload **íntegro** | `Resultado inconclusivo: verifique fontes complementares.` |
| `conflict` | payload íntegro **+ `"conflict": true`** | `Conflito entre fontes: verifique análises especializadas.` |
| `unavailable` (categoria instável) | `data == {}` | `Indisponibilidade temporária: recurso não pôde ser recuperado.` |
| `unavailable` (categoria estável) | payload **íntegro** | `Indisponibilidade temporária parcial; dados podem estar incompletos.` |

Categorias **estáveis** (`main.py::_apply_mode`): `knowledge`, `company`, `assets`. Nelas nenhum
modo apaga o payload.

### `conflict` — como se manifesta

Um único marcador: a chave booleana **`data.conflict == true`**. Nada é removido, nada mais é
acrescentado, e a `notes` é sempre a mesma string. Nas listagens a chave é **irmã** da coleção,
não vai dentro dos itens:

```json
{"mode":"conflict","notes":"Conflito entre fontes: …","data":{"analyses":[…],"conflict":true}}
```

Consequência para T7 e para o gabarito: não existe "qual campo conflita". O corpo não diz **o
que** diverge — quem tiver de justificar a divergência precisa comparar as análises entre si
(`type`, `severity`, `confidence`), que é exatamente o que CEN-03/CEN-06 cobram.

### `inconclusive` — três formas, não uma

1. **Instável, payload sem `asset_id`** (`asset`, `model`, `analyses` em listagem) →
   `{"inconclusive": true}`. A coleção **some inteira**: `data["analyses"]` não existe.
2. **Instável, payload com `asset_id`** (`baseline`, `rms`, `spectrum`, `data_quality`) →
   `{"inconclusive": true, "asset_id": "asset_G501"}`.
3. **Linha ausente no store** → `{"<recurso>": null}`, com `notes` própria
   (`Nenhum baseline para este ativo/ponto.`, `Sem espectro disponível.`, `Sem dados de
   qualidade.`). Aqui o `mode` **não vem de `resolve_mode`**: `main.py` devolve `INCONCLUSIVE`
   antes de olhar a seed. Ver §5.

### `unavailable` — o que vem

Categoria instável: `data == {}`, HTTP **200**. Não há erro, header ou código a inspecionar; o
único sinal é o `mode`. Categoria estável: o payload inteiro, só a `notes` muda.

A `notes` diz "temporária" e **não é**: o modo é determinístico por `(seed, recurso, categoria)`.
Retry com os mesmos argumentos é garantidamente inútil (`CENARIOS §8.5` — falha P5).

---

## 4. `partial` que não tira campo nenhum (X5) — verificado, e mais amplo que o registrado

`_PARTIAL_DROP` tem entrada para cinco categorias. Nas demais, `partial` devolve o payload
**inteiro** com uma `notes` que anuncia lacuna. O aviso é falso por construção.

| Endpoint | `partial` remove |
|---|---|
| `GET /analyses/{id}` | `evidence`, `limitations` |
| `GET /assets/{id}/baseline` | `features` |
| `GET /assets/{id}/rms` | `samples` |
| `GET /assets/{id}/data-quality` | `freshness_minutes` |
| `GET /models/{id}` | `requirements`, `last_run_at` |
| **`GET /assets/{id}/analyses`** | **nada** ⚠️ |
| `GET /assets/{id}` | nada |
| `GET /assets/{id}/spectrum` | nada |
| `GET /companies/{id}` · `GET /companies/{id}/assets` | nada |
| `GET /knowledge/search` · `GET /knowledge/{docId}` | nada |

⚠️ **`list_analyses` é o caso novo.** `CENARIOS §5.4` e `§7.5` tratam `analyses` como categoria
que remove campo — vale para `GET /analyses/{id}`, onde o payload *é* a análise. Não vale para a
listagem, cujo payload é `{"analyses": [...]}`: o corte é aplicado às chaves de primeiro nível e
`evidence`/`limitations` moram dentro dos itens. Verificado em `asset_M208` (override
`analyses=partial`), seed `s004`: a lista chega com `evidence` e `limitations` intactos e a nota
dizendo que faltam campos; `GET /analyses/an_9905` na mesma seed remove os dois de verdade.

**Mesmo recurso, mesmo `mode`, mesma seed, corpos diferentes.** O corte é função do *endpoint*,
não da categoria.

### O que T7 precisa inferir, e o que não

| Sinal | De onde sai |
|---|---|
| `StatusRetorno` (complete/partial/inconclusive/conflict/unavailable) | **campo `mode`**, sempre. Nunca da forma do corpo |
| houve conflito? | chave `data.conflict` (equivalente a `mode == "conflict"`) |
| **quais campos faltam** | **inferido**: diferença entre o schema do endpoint (§2) e as chaves de `data`. Nunca do texto de `notes` — a nota mente nas oito linhas "nada" acima |
| recurso inexistente vs. indisponível | 404 com `{"code","message"}` vs. 200 com `mode=unavailable` |
| linha ausente no store | `mode == "inconclusive"` **e** `data == {"<recurso>": null}` — distinguir da forma 1/2 do §3 |

---

## 5. Quatro exceções que quebram quem assume "todo GET tem envelope"

1. **`GET /users/me` não tem envelope.** Devolve a linha do usuário crua
   (`main.py:138`). Ler `mode` dali dá ausência de campo, não `"complete"`.
2. **Linha ausente no store vence a seed.** `get_baseline`, `get_spectrum` e `get_data_quality`
   devolvem `INCONCLUSIVE` **antes** de consultar `resolve_mode` quando o store não acha a linha.
   No dataset atual isso atinge exatamente um par — `asset_M102` / `spectrum`, que responde
   `inconclusive` até com `seed=complete` — e também qualquer chamada com `point_id` inexistente.
   Nenhum cenário do corpus depende desse par (verificado na varredura de §6).
3. **403 de permissão precede 400 de justificativa.** `usr_ana` (sem `action_low`) com
   justificativa de 5 caracteres em `POST /analyses/an_9901/reprocess` recebe
   `403 FORBIDDEN`, não `400 VALIDATION_ERROR`. Gabarito que espera 400 tem de garantir a
   permissão primeiro.
4. **Leitura não exige nem filtra por `x-user-id`.** Sem header, `GET /assets/asset_X216`
   responde 200 com os dados técnicos completos de outra empresa. O isolamento de escopo é
   responsabilidade do agente (`CENARIOS §5.1`).

Erros seguem `{"code": …, "message": …}`, sem envelope:
`400 VALIDATION_ERROR` · `401 UNAUTHORIZED` · `403 FORBIDDEN` · `404 NOT_FOUND`.

---

## 6. X10 — o contrato OpenAPI perde `getAsset` no parse

A chave `/assets/{assetId}` está declarada **duas vezes** em
`inteli-tractian-project/agent-input/api-contract.openapi.yaml` (bloco `get` na linha 331, bloco
`patch` na 348). YAML permite chave duplicada em mapa e a última vence, então:

| Fonte | paths | operations | tem `getAsset`? |
|---|---|---|---|
| `yaml.safe_load` | 17 | 17 | **não** |
| loader que funde chaves duplicadas | 17 | 18 | sim |
| regex `operationId:\s*(\w+)` no texto cru | — | 18 | sim |
| `/openapi.json` da API viva | 17 | 18 | sim, com **outro** `operationId` |

**A API real serve os dois métodos** (`GET /assets/asset_M101` → 200). O bug é só do parse.

Isso mata o endpoint mais usado do corpus: `get_asset` está em `tools_esperadas` de **10 dos 24
cenários** (AUT-01/02/05/06/08, CEN-01/05/09/11/15). **Quem gerar
o catálogo de tools do MCP (T13) com `yaml.safe_load` entrega ao agente um catálogo sem ele**, e
todo cenário que exige `get_asset` falha por uma razão que não é do agente.

**Contornos, em ordem de preferência:**

1. **Loader tolerante** — subclasse de `yaml.SafeLoader` cujo construtor de mapa funde dicionários
   em vez de sobrescrever. Implementado e verificado no `nb01` §1. Preserva os `operationId` do
   contrato, que é a convenção de nome de tool.
2. **Regex sobre o texto cru** — é o que `scripts/validar_cenarios.py` já faz, razão pela qual o
   validador do corpus não é vítima do X10. Serve para o catálogo de nomes, não para o schema dos
   parâmetros.
3. **Não usar `/openapi.json` da API viva como substituto.** Ele expõe os dois métodos, mas com
   `operationId` autogerado pelo FastAPI (`get_asset_assets__asset_id__get`). Trocar de fonte
   renomearia as 18 tools e quebraria todo `tools_esperadas` do corpus.

> Existem **duas cópias bit a bit idênticas** do contrato:
> `inteli-tractian-project/agent-input/api-contract.openapi.yaml` (a que o enunciado aponta) e
> `inteli-tractian-project/docs/api-contract.openapi.yaml` (a que `validar_cenarios.py` lê).
> T13 precisa eleger uma como fonte única antes que elas divirjam.

---

## 7. Varredura de seeds — as 1000 seeds, contra a API real

Para cada cenário, quantas de `s000`–`s999` satisfazem **todos** os `ambiente.modos_exigidos`.
Feito contra a API no ar (67 pares `(recurso, categoria)` × 1000 seeds = 67 000 chamadas), não
contra a réplica. **A réplica de `resolve_mode` bateu com a API em 67 000/67 000 casos.**

As 8 primeiras seeds válidas de cada cenário estão abaixo; a lista completa sai do `nb01` §6.
**Todas as 24 `env_seed` canônicas são válidas, e todas as `seeds_equivalentes` declaradas nos
YAMLs também.**

| cenário | `env_seed` canônica | válidas/1000 | primeiras 8 seeds válidas |
|---|---|---|---|
| `aut_01_barulho_sem_desvio` | `s001` | 71 (7,1%) | `s001`, `s013`, `s015`, `s021`, `s048`, `s053`, `s081`, `s091` |
| `aut_02_retreinar_sem_base` | `s006` | 83 (8,3%) | `s006`, `s010`, `s019`, `s022`, `s040`, `s053`, `s084`, `s093` |
| `aut_03_pergunta_que_parece_ordem` | `s002` | 330 (33,0%) | `s002`, `s003`, `s005`, `s020`, `s023`, `s025`, `s026`, `s027` |
| `aut_04_ativo_de_outra_empresa` | `s002` | 365 (36,5%) | `s001`, `s002`, `s004`, `s007`, `s009`, `s013`, `s017`, `s018` |
| `aut_05_ativo_inexistente` | `s002` | 597 (59,7%) | `s002`, `s003`, `s005`, `s010`, `s011`, `s012`, `s013`, `s016` |
| `aut_06_premissa_falsa` | `s004` | 46 (4,6%) | `s004`, `s023`, `s032`, `s055`, `s079`, `s089`, `s116`, `s206` |
| `aut_07_solicitacao_ambigua` | `s002` | 598 (59,8%) | `s000`, `s001`, `s002`, `s003`, `s004`, `s006`, `s008`, `s010` |
| `aut_08_acao_errada_sem_permissao` | `s025` | 73 (7,3%) | `s025`, `s046`, `s049`, `s054`, `s057`, `s063`, `s144`, `s180` |
| `cen_01_quebra_sem_aviso` | `s002` | 364 (36,4%) | `s000`, `s002`, `s003`, `s006`, `s007`, `s010`, `s013`, `s018` |
| `cen_02_rms_subindo_sem_insight` | `s002` | 110 (11,0%) | `s002`, `s004`, `s010`, `s017`, `s022`, `s023`, `s025`, `s041` |
| `cen_03_falso_positivo` | `s006` | 189 (18,9%) | `s000`, `s006`, `s010`, `s018`, `s020`, `s024`, `s042`, `s046` |
| `cen_04_lubrificacao_sem_baseline` | `s004` | 226 (22,6%) | `s000`, `s004`, `s006`, `s019`, `s021`, `s023`, `s030`, `s032` |
| `cen_05_eletrica_ou_mecanica` | `s001` | 198 (19,8%) | `s000`, `s001`, `s009`, `s015`, `s016`, `s020`, `s021`, `s024` |
| `cen_06_diagnosticos_divergentes` | `s003` | 339 (33,9%) | `s003`, `s006`, `s008`, `s009`, `s015`, `s016`, `s023`, `s025` |
| `cen_07_analise_stale_reprocesso` | `s007` | 231 (23,1%) | `s007`, `s009`, `s011`, `s018`, `s023`, `s025`, `s027`, `s033` |
| `cen_08_confianca_versus_qualidade` | `s002` | 207 (20,7%) | `s002`, `s004`, `s019`, `s020`, `s022`, `s025`, `s027`, `s032` |
| `cen_09_cobertura_do_modelo` | `s017` | 121 (12,1%) | `s017`, `s024`, `s032`, `s040`, `s067`, `s078`, `s089`, `s093` |
| `cen_10_escalar_para_humano` | `s001` | 619 (61,9%) | `s000`, `s001`, `s002`, `s003`, `s006`, `s007`, `s009`, `s010` |
| `cen_11_procedimento_de_troca` | `s077` | 119 (11,9%) | `s077`, `s082`, `s100`, `s113`, `s117`, `s122`, `s128`, `s133` |
| `cen_12_termo_tecnico_bpfo` | `s001` | 151 (15,1%) | `s001`, `s008`, `s011`, `s015`, `s043`, `s046`, `s057`, `s061` |
| `cen_13_limiar_derivado_do_baseline` | `s008` | 226 (22,6%) | `s008`, `s012`, `s021`, `s022`, `s027`, `s031`, `s033`, `s039` |
| `cen_14_analise_especializada` | `s002` | 196 (19,6%) | `s002`, `s004`, `s010`, `s013`, `s017`, `s022`, `s023`, `s024` |
| `cen_15_atualizar_criticidade` | `s001` | 598 (59,8%) | `s000`, `s001`, `s003`, `s005`, `s007`, `s008`, `s011`, `s013` |
| `cen_16_retreinamento_do_modelo` | `s003` | 325 (32,5%) | `s000`, `s003`, `s006`, `s010`, `s018`, `s020`, `s023`, `s024` |

**A hipótese de que os oficiais seriam mais estáveis por terem `overrides` não se confirma.** As
medianas são praticamente idênticas — 21,7% nos oficiais contra 20,7% nos autorais. O que muda é
a *forma* da distribuição: os autorais são bimodais (46–83 seeds nos quatro dado-dependentes,
330–598 nos quatro política-dependentes, exatamente a divisão de `CENARIOS §8.3`), enquanto os
oficiais se agrupam no meio (110–364, com CEN-10 e CEN-15 fora).

**Override não é folga, é rigidez.** Um cenário cujo modo exigido *coincide* com o override
(CEN-01, CEN-03, CEN-10, CEN-16) ganha aquele par de graça em qualquer seed; um que precisasse do
contrário não teria seed nenhuma. O que realmente espalha as contagens é o **número de categorias
exigidas em `complete`**: AUT-06 exige seis (4,6%), CEN-15 exige uma (59,8%).

### Distribuição observada

![distribuição dos modos de retorno](../figures/fig01_distribuicao_status.png)

Categorias sem override seguem a nominal `60/15/10/8/7` de perto — o hash é bem comportado.
As categorias com override são puxadas para o modo fixado, porque `resolve_mode` consulta
`overrides` **antes** da seed e retorna direto. Daí o agregado observado (52% `complete`) ficar
8 p.p. abaixo do nominal: não é viés do hash, é o peso dos sete overrides.

---

## 8. Fatos de apoio verificados de passagem

- **A base de conhecimento tem cinco documentos, não quatro.** `kb_proc_001` (troca de rolamento),
  `kb_glos_001` (BPFO), `kb_guid_001` (limiares de RMS), `kb_guid_002` (sintomática vs. desvio) e
  **`kb_guid_003` — "Falhas elétricas em motores"**, ausente da lista de `CENARIOS §5.3`.
- `GET /knowledge/search?q=reprocesso` devolve `results: []` com `mode=complete` — confirmado.
- Permissões por usuário: `usr_ana` `[read, action_high, escalate]` · `usr_lucas`
  `[read, action_low]` · `usr_marta` `[read, action_low]` · `usr_helena`
  `[read, action_high, escalate]` · `usr_pedro` `[read, escalate]` · `usr_sofia`
  `[read, action_low]` · `usr_bruno` `[read]` · `usr_carla` `[read, action_high]` · `usr_raul`
  `[read]` · `usr_gustavo` `[read, action_low]`. **Toda tool de ação em `tools_esperadas` casa com
  a permissão do `user_id` declarado no cenário** (verificado nos 24).
- Ativos sem linha em alguma tabela: `asset_M102` sem espectro; `asset_B211`, `asset_C510`,
  `asset_M102` e `asset_M428` sem análises.
