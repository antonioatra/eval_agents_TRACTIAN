# Índice das figuras

Cada figura tem **uma frase que ela sustenta** e **uma que ela não sustenta**. A segunda não é
modéstia: é o que impede a figura de ser citada por algo que ela não mostra.

Toda figura aqui é regenerável rodando o notebook da coluna *origem* — `make repro` executa os
notebooks e **reprova** se alguma figura declarada não for regravada no disco.

> ⚠️ **A numeração não segue a do `PLANO`.** A T28 foi escrita antes do nb03 existir e pedia
> `fig04_custo_recall_h0`; o `fig04` já era do nb03 desde 26/08. A numeração segue a ordem em que
> as figuras passaram a existir, e este arquivo é o mapa entre as duas.

| # | arquivo | origem | sustenta | **não** sustenta |
|---|---|---|---|---|
| 01 | `fig01_distribuicao_status.png` | nb01 | os cinco modos de retorno da API aparecem no corpus na proporção que o `CENARIOS §5` declara | nada sobre o agente — é a API medida, não a avaliação |
| 02 | `fig02_matriz_cobertura.png` | nb02 | os 24 cenários cobrem a matriz TAPI §5 × `StatusRetorno` sem célula vazia por acidente | que a cobertura seja suficiente; cobre o que foi desenhado para cobrir |
| 03 | `fig03_flip_rate.png` | nb03 | a rubrica v2 reduziu a instabilidade do judge campo a campo (INS.7) | que a v2 esteja "certa" — mede estabilidade, não acerto |
| 04 | `fig04_curva_rubrica.png` | nb03 | onde os flips estavam por cenário e para onde foram | que os cenários sem flip sejam fáceis; podem só não ter sido amostrados |
| 05 | `fig05_custo_recall_h0.png` | nb04 | **H0**: o recall sobe com retorno decrescente, e `ΔRecall(N3 \| N1+N2)` = **+19,4%**, IC95 [+13,5%, +24,8%] | o valor **absoluto** do ponto N1+N2 (0,759): ali o recall é **identidade e não medição** (A27) |
| 06 | `fig06_recall_por_classe_h0.png` | nb04 | **a predição de H0**: o ganho está inteiro em conteúdo (C: 0% → 81%); processo e decisão o gabarito estrutural já dava | os 100% de P e D — mesma identidade do A27. E C2/C3/C7 não estão no gold, que é cego |
| 07 | `fig07_h2_funcao_vs_args.png` | nb04 | **H2**: a diferença entre os modelos é maior nos argumentos (−0,061) que na função (+0,021), e o **sinal se inverte** — o 14B escolhe melhor a função e preenche pior o schema | que `args_acc` seja conclusivo: p = 0,0514 contra corte de 0,05 — **está no limiar**, e a figura o marca em âmbar em vez de escolher um lado |
| 08 | `fig08_ins9_mutantes.png` | nb04 | **INS.9**: o corte nominal de §6.5 tem poder **zero** nos quatro mutantes (o X33), e mesmo a lente rica acerta a direção em só 8% dos 120 pares | que 84% seja "taxa de detecção": 31% das distinções vão na direção errada — no MUT3, o agente sabotado é o mais bem avaliado |

## O que ainda não tem figura

| o quê | por quê | quando |
|---|---|---|
| pass^k por modelo (INS.8) | a bateria principal fechou em 31/08; o notebook é o nb05 (T29) | próxima leva |
| severidade S0–S4 e taxonomia (T30) | mesma bateria, notebook nb06 | próxima leva |
| decomposição de variância (H4) | **não vai existir**: o A16 cortou a bateria de ambiente, e sem eixo de `env_seed` não há variância ambiental para decompor | trabalho futuro declarado |
| κ do judge por configuração | a `fig05_kappa_por_config` do enunciado da T28 virou a `fig03`/`fig04` do nb03, que já mostram estabilidade e curva da rubrica | não vai existir com esse nome |

## Regenerar

```bash
make api      # noutro terminal — o nb01 e o nb02 medem a API do parceiro
make repro    # executa os notebooks e confere que TODA figura da lista foi regravada
```

O nb03 e o nb04 **não** precisam da API: leem `runs/` e `labels/` versionados, e a pontuação
N1/N2 é função pura de `(trace, gabarito)` — `tests/test_repro.py` bloqueia `socket` para provar.
