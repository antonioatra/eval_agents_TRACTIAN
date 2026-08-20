# `scenarios/` — os cenários executáveis

Um YAML por cenário, 24 no total. **A documentação do corpus está em `CENARIOS.md`** (raiz):
por que o corpus é assim, a tabela dos 24 com split e seeds, os oito autorais em detalhe, os
achados da conversão e a varredura de seeds. Este README documenta só o **schema dos arquivos**.

```
_regras_decisao.yaml    as 19 regras nomeadas — contrato entre o corpus e a implementação (T9)
aut_01…aut_08.yaml      os 8 autorais
cen_01…cen_16.yaml      os 16 oficiais, convertidos de docs/test-scenarios.md
```

Validação (exige a API no ar):

```
make api        # sobe a API do parceiro em localhost:8000
make corpus     # roda os dois validadores
```

---

## Campos

| Campo | Papel |
|---|---|
| `id` | igual ao nome do arquivo — o validador falha se divergirem |
| `procedencia` | `autoral` \| `oficial` — variável controlada: as duas existem nos dois splits |
| `origem` | (oficiais) cenário, chamados e `case_id` de origem no material do parceiro |
| `split` | `dev` \| `test` — decidido na criação, nunca depois |
| `status` | `valido` (default, pode ser omitido) \| `inviavel` — ver *Cenário inviável* abaixo |
| `justificativa_inviabilidade` | obrigatória quando `status: inviavel`, proibida caso contrário |
| `natureza` | `dado_dependente` \| `politica_dependente` — só os segundos entram na bateria de robustez com seed variável |
| `adversarial` | a lacuna que o cenário preenche; `null` para cenário comum |
| `solicitacao` | a mensagem do usuário, literal |
| `user_id` / `asset_id` | quem pede e sobre o quê |
| `contexto` | empresa, permissões e criticidade — informativo, espelha o que a API devolve |
| `ambiente.env_seed` | a seed canônica do cenário |
| `ambiente.modos_exigidos` | pares `(recurso, categoria) → modos aceitáveis`; os dois validadores conferem |
| `ambiente.seeds_equivalentes` | outras seeds que satisfazem as mesmas exigências (bateria de ambiente) |
| `ambiente.seeds_de_ramo` | seeds que produzem o ambiente de um `gabarito.ramos[]` específico — ver abaixo |
| `estado_esperado` | o que a API devolve sob a seed canônica. **Não é gabarito** — é documentação da montagem |
| `nota_de_conversao` | (quando houve) divergência entre o spec do parceiro e o dado real, e como foi resolvida |
| `politica` | as regras de domínio que o cenário cobra |
| `falhas_alvo` | códigos de `METRICAS §6` que o cenário existe para provocar |
| `criterio_sucesso` | resumo em prosa do que conta como acerto |

### `gabarito`

| Campo | Métrica que alimenta |
|---|---|
| `evidencias_obrigatorias` | N1.3 — checklist de suficiência |
| `tools_esperadas` | N1.1 — conjunto de referência do F1 |
| `tools_aceitaveis` | não exigidas e **não penalizadas** — evita punir caminho alternativo válido |
| `args_esperados` | N1.2, condicional à tool certa |
| `precedencias` | N2.1 — pares ordenados `(antes, depois)` |
| `decisao_esperada` | N1.4 — **sempre `regra:<nome>`, nunca um valor** |
| `deve_mencionar` | N3.1/N3.2 — o que a resposta precisa dizer |
| `proibido` | N1.5 — tools que não podem ser chamadas (chamada indevida é S0) |
| `proibido_no_texto` | verificação determinística sobre o `final_answer` (D5, alucinação) |
| `ramos` | gabarito relativo — em que a decisão se transforma se o ambiente degradar |

### `ramos[].quando` — a fatia avaliável do ramo (A9)

O `se:` de um ramo é **prosa** e não é avaliável: *"`spectrum` degradar para partial/unavailable
(perdendo o pico de choque)"*. Sem mais nada, o scorer só podia aproximá-lo por uma escada
genérica que desce um degrau sempre que qualquer fonte obrigatória degrada — mais severa do que
o cenário pede em uns casos, mais frouxa em outros, e em nenhum deles o que o autor escreveu.

```yaml
    - se: "`model` degradar para partial (perde requirements)"
      quando: {categoria: model, modo_pior_que: complete}
      decisao: regra:acao_alto_impacto_com_base_tecnica
```

`quando` traduz a parte mecânica do `se:` — **"esta categoria veio pior que este modo"** — e o
`se:` continua existindo, porque carrega o que o predicado não cabe e é o que o judge (T20) lê.
Onde há `quando`, o gabarito é o que o cenário declarou; onde não há, continua a aproximação.

**Só ramos de DEGRADAÇÃO ganham `quando`, e isso é deliberado.** *"Usuário sem `action_high`"*,
*"PATCH chamado sem justificativa (400)"* e *"`baseline` aparecer `established`"* não são sobre
modo de retorno; inventar predicado para condição que ninguém sabe avaliar seria transformar
prosa em código errado, que é pior do que prosa.

Duas coisas que o validador confere, porque as duas apodrecem em silêncio: `categoria` tem de ser
evidência obrigatória do próprio cenário (senão o ramo nunca dispara, e ramo que nunca dispara é
gabarito nunca conferido que ainda assim conta como cobertura na leitura), e `modo_pior_que` tem
de ser um modo da API.

### `ambiente.seeds_de_ramo` — a seed que torna o ramo alcançável

Um ramo pode declarar uma decisão para um ambiente que a `env_seed` canônica **nunca produz** —
foi o caso de `cen_08` e `cen_09` com `model: partial` até 19/08. O ramo existia, ninguém o
executava, e a matriz de cobertura o contava como coberto.

```yaml
  seeds_de_ramo:
    - ramo: "`model` degradar para partial (perde requirements)"
      seed: s065
      exige:
        - {recurso: mdl_vib_v3, categoria: model, modos: [partial]}
```

A seed entra aqui e **não** como `env_seed`: trocar a canônica mudaria o que o cenário mede na
bateria principal, além de mexer no split e nas contagens. Assim o ramo é exercido pela bateria
de ambiente (T26b) a custo zero. O validador confere que a seed produz mesmo o ambiente de
`exige` e que o texto de `ramo` ainda existe em `gabarito.ramos[].se` — renomear o `se:` deixaria
a seed órfã.

---

## Três regras que o validador impõe

**1. Nome de tool = `operationId` do OpenAPI em snake_case.** `getBaseline` → `get_baseline`. O
catálogo é derivado do próprio `api-contract.openapi.yaml`, então divergência de nomenclatura é
impossível por construção.

**2. `decisao_esperada` nunca é um valor, sempre `regra:<nome>`.** As regras vivem em
`_regras_decisao.yaml`. Um gabarito com valor fixo penalizaria o agente por variação da API que
não está sob o controle dele — ver `CENARIOS §2.1`.

**3. Nenhum ativo aparece nos dois splits.** Dois cenários sobre o mesmo ativo compartilham dados;
calibrar num ativo ensina como ele se comporta.

## Cenário inviável

> **Último recurso, não rotina.** Cenário que não bate com a seed declarada **não é inviável** —
> é seed errada, e trocar a seed é o trabalho normal da reconciliação (T3, item 3). Cenário que
> não fecha com nenhuma seed também não é: o T3 pode mudar `fixtures`, `asset_id` e `user_id` até
> a montagem fazer sentido. **Recriar é o default.** Hoje o corpus tem zero inviáveis.

O que **não** pode mudar é o `gabarito`. Você ajusta o mundo até o cenário fazer sentido; não
ajusta o que conta como acerto depois de ver o resultado.

Sobra um caso em que recriar não resolve: **`overrides`**. O `resolve_mode` da API consulta
`data/seed.json` *antes* da seed e retorna direto —

```python
ov = OVERRIDES.get(recurso, {})
if categoria in ov:
    return ov[categoria]      # a seed nunca é consultada
```

— então `asset_G501` tem `baseline` sempre `partial`, `asset_S420` tem `analyses` sempre
`conflict`, e assim por diante. Um cenário que precise do contrário nesse ativo é impossível sob
qualquer seed. Trocar de ativo resolveria, mas nos 16 oficiais o ativo vem amarrado à `origem`
(chamado e `case_id` do material do parceiro), e procedência é variável controlada do desenho.

**Aí sim, declarar.** Se a regra fosse "recriar até passar", o corpus deixaria de testar o que
importa e passaria a testar o que esta API deixa fácil — a matriz de cobertura do `CENARIOS §6.2`
viraria descrição da API, não do problema, com todo cenário difícil silenciosamente convertido
num fácil. Declarado, o cenário vira limitação registrada no README (TAPI §6). Apagado, some.

```yaml
status: inviavel
justificativa_inviabilidade: >-
  nenhuma das 1000 seeds produz baseline=complete e data_quality=partial ao mesmo tempo
  neste ativo — a combinação que o cenário existe para testar não é alcançável.
```

O campo é **opcional e default `valido`**, para que os cenários vivos não precisem declarar o
caso comum. O que o validador impõe:

| Regra | Por quê |
|---|---|
| `justificativa_inviabilidade` obrigatória quando `inviavel` | sem o porquê escrito, declarar inviável é indistinguível de esconder cenário que não passou |
| justificativa sem `status: inviavel` é erro | evita justificativa órfã sobrevivendo a um cenário reabilitado |
| inviável **sai** do split, da contagem e do isolamento de ativo | ele não roda; cobrar isolamento de quem nunca toca o ativo mede o que não existe |
| inviável **não** é cobrado por `modos_exigidos` × `env_seed` | ser insatisfazível por qualquer seed é a razão típica da inviabilidade — cobrar deixaria o corpus vermelho para sempre |
| inviável **continua** validado como documento (schema, tools, regras) | sair da bateria não é sair da curadoria |
| o split quebra e o erro diz o porquê | declarar inviável muda o denominador das baterias (`PLANO §baterias`, `METRICAS`). Falhar alto força a re-decisão; o silêncio é que era o bug |

> **Quem executa cenários tem de filtrar `status`.** O validador protege o corpus, não o runner.
> T18 precisa pular os inviáveis explicitamente — um `glob("*.yaml")` sem filtro roda cenário
> declarado morto e envenena as contagens sem nenhum teste acusar.

### Marcos citados em `precedencias`

Além dos nomes de tool, os pares usam marcos derivados do trace pela `derivar_estado`:
`afirmar:<x>`, `decidir:<x>`, `atribuir:<x>`, `explicar:<x>`, `propor:candidato`,
`perguntar:<x>`, `investigar:<x>`, `responder:<x>`, `acao:qualquer`, `comparar:<x>`.
A lista fechada é congelada em T8/T9 junto com `derivar_estado`.

**Precedência global**, válida para todos e não repetida em cada arquivo: *não repetir chamada
cujo retorno foi `unavailable` com a mesma seed e os mesmos argumentos* (`CENARIOS §2.5`).
