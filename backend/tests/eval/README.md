# Eval del pipeline de OCR de boletas

Puntúa el pipeline **real** de visión (`app.ocr.parse_receipt`) contra fotos
reales de boletas/comandas, con ground-truth escrito a mano.

Sirve para responder "¿el cambio X mejoró o empeoró la lectura?" con un número,
en vez de a ojo. Los `tests/test_boleta_*.py` normales NO tocan este camino —
solo prueban el parser regex de respaldo con texto inventado.

## Correr

Hace llamadas reales al LLM → cuesta plata (~1-3 ¢/imagen) y tarda ~30-90 s.
No es un test de pytest, no corre en CI.

```bash
cd backend
PY=/Library/Frameworks/Python.framework/Versions/3.13/bin/python3   # el que tiene cv2/PIL

# baseline con lo que hay en .env (hoy: openai / gpt-4.1)
$PY tests/eval/run_eval.py

# probar otro modelo de visión de OpenAI
$PY tests/eval/run_eval.py --vision-model gpt-4o
$PY tests/eval/run_eval.py --vision-model gpt-5-mini

# una sola imagen, viendo el JSON que devolvió
$PY tests/eval/run_eval.py --only ponzano_madrid --dump

# comparar dos corridas
$PY tests/eval/run_eval.py --compare tests/eval/results/A.json tests/eval/results/B.json
```

Cada corrida imprime un scorecard y guarda el detalle en
`tests/eval/results/<ts>_<provider>_<model>.json` (git-ignored).

## Probar Gemini / Claude

Hoy **solo `OpenAIProvider` implementa `vision_json`** en `app/ai/provider.py`.
Con `--provider gemini` el pipeline se cae al fallback Tesseract y el eval da
basura. Para A/B real con Gemini 2.5 Flash o Claude Sonnet hay que agregarles
un método `vision_json(...)` a `GeminiProvider` / `AnthropicProvider` primero.

## El set (7 imágenes)

| archivo | qué prueba |
|---|---|
| `danes_vitacura` | comanda CLP, foto oscura, item con qty=2 (Plateada Greda) |
| `baobar_providencia` | comanda CLP, "propina sugerida" que NO se cobra, item $0, qty=2 |
| `barlaprovidencia` | precuenta CLP difícil: 13 líneas, precios desalineados, no footea (item-level lenient) |
| `montana_bellavista` | boleta SII CLP con precios **sin** separador de miles ("3100") |
| `ponzano_madrid` | **EUR con decimales** (Madrid) — no debe convertir a CLP |
| `cuenta_valeria` | comanda CLP sin nombre de local, precios sin puntos (item-level lenient) |
| `dondewilly_vinadelmar` | boleta chica CLP donde el TOTAL **sí** incluye la propina |

## Ground truth

`expected/<nombre>.json` por imagen. Campos:

- `currency`, `amount` (total final cobrado), `date` (o `null`)
- `merchant_contains` — substring esperado, o `null` si la boleta no tiene nombre
- `total_neto` / `iva_amount` — `null` si la boleta no desglosa IVA
- `item_count` (+ `item_count_max` opcional para un rango aceptable)
- `items_subtotal` + `items` (`line_total` puede ser `null` cuando no es legible)
- `items_lenient: true` → no se puntúa la suma de ítems (boleta que no footea)

## Scoring

Checks ponderados, solo cuenta lo "aplicable":

| check | peso | criterio |
|---|---|---|
| `amount` | 3 | ±2% (mín ±200 CLP / ±0.5 en otras monedas) |
| `item_count` | 2 | exacto = 1.0, ±1 = 0.5 |
| `items_subtotal` | 2 | Σ(precio×qty) dentro de ±5% (salvo `items_lenient`) |
| `currency` | 1 | exacto |
| `merchant` | 1 | substring, normalizado sin tildes |
| `neto_iva` | 1 | ambos `null` esperados → no debe inventar; si hay → ±2% |
| `date` | 1 | exacto |

`OVERALL` = promedio simple del % por imagen.
