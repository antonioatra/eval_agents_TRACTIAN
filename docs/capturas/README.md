# Capturas de tela

O que o deck mostra da aplicação. **Não são regeneráveis por `make repro`** — cada uma é uma tela
capturada com os dois serviços no ar, e é por isso que elas não moram em `figures/`.

| arquivo | o que mostra | como refazer |
|---|---|---|
| `copiloto_ao_vivo.png` | uma pergunta que não está no corpus, executada de verdade: o cabeçalho com o YAML de cenário gerado, a faixa do resultado da run, o rascunho e o trace com os retornos `PARCIAL`/`COMPLETO` da plataforma | ver abaixo |

## Como refazer uma captura

```bash
make api                      # noutro terminal — a API do parceiro em :8000
lms load qwen3-8b-mlx --context-length 16384 --parallel 1 --gpu max -y
make copiloto                 # a página em :7000, com a consulta ao vivo ligada
```

Fazer a pergunta pela interface, esperar a run fechar (~2 min 20 s no 8B), e capturar a janela em
**1920 px de largura**. A captura que está aqui usou `zoom: 0.78` na página para caber o cabeçalho,
a faixa e as duas colunas no mesmo quadro — o slide reserva uma área de 11,39 × 4,12 in, então o
quadro tem de ser largo e baixo.

**A run pode terminar sem responder**, e a captura atual é uma dessas. Isso não é um problema a
contornar tirando outra: acontece em 37 das 288 execuções da bateria gravada, a tela diz isso na
cara, e é o resultado medido aparecendo na demonstração. Trocar por uma execução bem-sucedida
escolhida a dedo seria escolher o quadro que favorece a conclusão.
