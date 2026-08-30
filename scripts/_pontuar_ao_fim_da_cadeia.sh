#!/bin/zsh
# Espera a cadeia de baterias terminar e pontua N1/N2 do que ela gravou.
#
# Operador da noite de 30/08, não peça do framework — o irmão de `_encadear_baterias.sh`.
# Existe como processo SEPARADO de propósito: a cadeia já estava rodando quando a passagem de
# pontuação passou a existir, e o zsh lê o script incrementalmente enquanto o executa —
# acrescentar as linhas de pontuação ao arquivo em execução corromperia a execução em voo.
#
# Pontua o que EXISTE. Se o portão reprovar a principal, a de mutantes não roda e o diretório
# dela não aparece; pontuar só a principal é a leitura certa dessa noite, e a ausência do
# outro diretório é o registro de que o portão fez o que devia.
#
# `--sem-gravar` NÃO é usado: bateria incompleta é reportada como incompleta (PLANO T24-26),
# e o `scores.jsonl` de uma bateria pela metade continua sendo o dado que ela produziu. O
# código de saída de cada passagem vai para o log, e é ele que diz se a bateria fechou.
set -u
cd "$(dirname "$0")/.."
PY=.venv/bin/python
PID_CADEIA="$1"

while kill -0 "$PID_CADEIA" 2>/dev/null; do sleep 60; done
echo "[$(date '+%m-%d %H:%M')] cadeia (pid $PID_CADEIA) terminou"

for diretorio in runs/principal_2026_08 runs/mutantes_2026_08; do
  if [ ! -f "$diretorio/manifest.json" ]; then
    echo "[$(date '+%m-%d %H:%M')] $diretorio não tem manifesto — não rodou; nada a pontuar"
    continue
  fi
  echo "[$(date '+%m-%d %H:%M')] pontuando $diretorio"
  $PY -m tapieval.scoring --bateria "$diretorio"
  # `codigo=$?` na linha SEGUINTE, e não dentro do `echo`: o `$(date ...)` do echo roda antes
  # da expansão de `$?` e o zera. O ensaio deste script reportou "código de saída 0" para uma
  # bateria com 198 células faltantes — o log mentia na direção confortável.
  codigo=$?
  echo "[$(date '+%m-%d %H:%M')] $diretorio → código de saída $codigo"
done
