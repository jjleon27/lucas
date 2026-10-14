#!/usr/bin/env bash
# A/B pagado del lector de boletas de "Dividir cuenta" (2026-10-14).
# Baseline = prompt/schema de 0e5d21e; candidate = actual. Misma imagen
# preprocesada y mismo scorer para ambos. 1 llamada por boleta (sin racing).
# Costo estimado: 60 boletas × 2 variantes ≈ 120 llamadas ≈ US$1-3.
# Uso (desde backend/, con OPENAI_API_KEY en el entorno):
#   bash tests/eval/run_ab.sh            # main (28) + holdout (32), 1 corrida c/u
set -euo pipefail
cd "$(dirname "$0")/../.."
python3 tests/eval/fetch_holdout.py
for set in main holdout; do
  for variant in baseline candidate; do
    python3 tests/eval/run_eval.py --pipeline bill --set "$set" --variant "$variant"
  done
done
ls -t tests/eval/results/*_bill_*.json | head -4
echo "Comparar: python3 tests/eval/run_eval.py --compare <baseline.json> <candidate.json> (por set)"
