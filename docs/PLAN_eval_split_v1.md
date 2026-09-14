# Plan de evaluación — Dividir cuenta (cajas de color + velocidad)

**Autor:** Fable (planificador) · **Ejecutor:** Sonnet, solo, sin volver a invocar a Fable
**Fecha:** 2026-09-14 · **Pedido original:** ver `docs/SESSION_STATE.md` / conversación del día — carpeta de prueba: `/Users/kako2/Downloads/Boletas/` (26 fotos)

## 0. No re-derivar esto (ya verificado hoy, ver contexto completo en la sesión que generó este plan)

- Bandas de color: causa real = Tesseract infla la altura de líneas puntuales borrosas; fix = `_clamp_adjacent_overlap()` en `services/ocr_position/app/main.py` (ya en prod), acota geométricamente contra el punto medio con el vecino. Validado contra CORD-v2 (0% traslape) y el eval propio (69/70). **NO validado todavía contra las boletas chilenas reales del usuario.**
- Velocidad: causa real = `reasoning_effort` sin fijar en modelos `gpt-5*` → miles de tokens de pensamiento invisible. Fix = `reasoning_effort="low"` en `vision_text()` (`backend/app/ai/provider.py:185-186`, commit `1fbfe0e`). 74.1s → 18.0s en la boleta de 22+ ítems. Validado: 87.6% en el eval de 9 boletas (vs 85.9% baseline).
- Infra reusable, NO crear de cero:
  - `backend/tests/eval/run_eval.py --pipeline bill [--only X] [--dump]` → pipeline real, gasta LLM, puntúa por línea (`items_lines_bill`) + needs_review.
  - `services/ocr_position/tests/eval_position.py [--receipts DIR --expected DIR]` → SOLO matching de posición (Tesseract, gratis). Hoy solo reporta `matched/total` por ítem — **no chequea geometría de traslape/altura**, eso hay que agregarlo (ver §2).
  - `expected/*.json` solo necesita el campo `"name"` por ítem para `eval_position.py` (`_load_items` solo lee `row["name"]`) — **no hace falta precio/qty de verdad para el check geométrico**, así que las "fixtures" nuevas para geometría son mucho más baratas de armar que las de `run_eval.py`.

## 1. Principios que este plan respeta (no repetirlos, aplicarlos)

- Medir antes de tocar código. Ningún umbral se fija por intuición — todo lo que dependa de un número (K de altura absurda, piso de match%, tolerancia de orden, umbral de retry) se **mide en la Iteración 1** contra datos reales, nunca se adivina acá.
- Nunca aceptar un cambio que empeore el eval propio (9 fixtures, 87.6%) ni el % de traslape en CORD (0%).
- Reusar `eval_position.py` y `run_eval.py` extendiéndolos, no crear scripts nuevos paralelos salvo que de verdad no se pueda reusar.
- Criterio de "caja de color OK" tiene que ser código ejecutable, no un humano mirando 26 fotos (eso ya se hizo hoy y no escala).

---

## 2. Cajas de color — protocolo de validación

### 2.1 Clasificación de las 26 fotos y qué hacer con cada una

| Categoría | Fotos | Acción |
|---|---|---|
| **A — Duplicados de fixture existente** | `images-10`≈danes_vitacura, `images-11`≈dondewilly_vinadelmar, `images-12`/`images-21`≈cuenta_valeria, `images-17`≈barlaprovidencia | Reusar el `expected/*.json` YA existente (mismos nombres de ítem, mismo local). Costo: **$0 LLM**. |
| **B — Nuevas, ya sanity-checkeadas hoy** | `images-15` (Sushi Plop, 3), `images-13` (Fabimfood, 1), `images-9` (Sand.Cheese+Pollo, 2) | Los nombres de ítem ya se generaron hoy end-to-end. Si no quedaron loggeados/guardados, 1 llamada barata (`_read`+`_reformat`, sin retry) para recuperarlos. Costo: ~0-3 llamadas. |
| **C — Nuevas, difíciles, corridas hoy sin ground-truth exacto** | `images-23` (28 ítems, dedos tapando), `images-16` (combo con ítems a $0, legítimo), `images-24` (Lolita Jones, 12) | Ídem: recuperar/generar solo los `name` (no hace falta precio exacto). Costo: ~3 llamadas. |
| **D — Sin revisar, priorizadas** | `images-14`/`images-20` (Mistura, dup entre sí, 12 ítems — restorán, on-target), `images-19` (Wok, formato de cantidad raro "1,0000 x 4190,0000" — edge case de *parsing*, no solo de geometría), `1.webp`/`1-2.webp`/`1-3.webp` + `boleta-líder-...webp` (supermercado, 20-33 ítems muy juntos — el escenario MÁS parecido al que rompió el fix de hoy, aunque fuera del caso de uso típico "dividir con amigos") | Correr estas primero dentro de "sin revisar". Costo: ~4-5 llamadas (Mistura solo 1 de las 2 fotos dup). |
| **D — Sin revisar, baja prioridad** | `images-18` (screenshot de tweet, no foto de cámara — dominio distinto), `DeOCnv4XcAAMtX4.jpg`, `ExMB89kVoAktfCD.jpg`, `FLZ4-dhXwAYx8Gf.jpg`(+`-large`), `file_20190820172730.jpg`, `nota_venta.png` | Solo si sobra tiempo/presupuesto después de §2.4. Documentar como "no cubierto, alcance dudoso" si se saltan. |

Nota: `boletaFloriCumpke_...JPG` aparece en la carpeta pero no estaba en la lista de 26 revisada hoy — tratarla como Categoría D sin revisar, baja prioridad (no re-analizar, solo clasificar igual que el resto de "sin revisar").

**Cómo generar los `name`-only fixtures para B/C/D** (barato, sin retry — la precisión de precio no importa para este check):
1. Llamar una sola vez a `_read()` + `_reformat()` + `_parse_bill_text()` (las mismas funciones de `backend/app/ocr.py`, función `vision_parse_bill`) por foto — **sin** disparar el retry/fallback (no hace falta para extraer nombres, ahorra plata).
2. Guardar `{"items": [{"name": ...}, ...]}` en una carpeta NUEVA, separada de `backend/tests/eval/expected/` para no mezclarla con las fixtures de precisión ya validadas — ej. `backend/tests/eval/expected_geo_only/<stem>.json`.
3. Las fotos en sí NO hace falta copiarlas al repo: `eval_position.py --receipts /Users/kako2/Downloads/Boletas --expected backend/tests/eval/expected_geo_only` ya apunta directo a la carpeta del usuario (son datos personales del usuario, no hace falta versionarlas — a diferencia de las 9 del eval oficial que ya estaban commiteadas de antes).
4. Para Categoría A, como los nombres de archivo no coinciden con el stem del fixture (`images-10.jpeg` vs `danes_vitacura.json`), agregar un mapeo chico (dict o manifest json, ej. `{"images-10": "danes_vitacura", ...}`) — extensión mínima a `eval_position.py`, no un script nuevo.

### 2.2 Criterio objetivo de "caja de color OK" (en código, sin ojo humano)

Extender `eval_position.py` (no crear otro script) para que, además de `matched/total`, calcule por foto:

```
def check_geometry_quality(items_out):
    matched = [it for it in items_out if it.position_y is not None]
    heights = [it.bbox_y1 - it.bbox_y0 for it in matched if it.bbox_y0/y1 no son None]
    median_h = mediana(heights)

    overlaps = cuántos pares consecutivos (a, b) en orden de lectura,
               AMBOS matcheados, tienen a.bbox_y1 > b.bbox_y0
               # debería ser 0 SIEMPRE post-clamp — esto es casi un assert
               # de regresión del fix de hoy, no un umbral a ajustar

    height_outliers = cuántos heights son <= 0 o > K * median_h
               # K: FIJAR EN ITERACIÓN 1, ver §2.3 — no adivinar un número acá

    order_violations = cuántos ítems (en orden de lectura) tienen
               position_y < max(position_y anteriores) - TOLERANCIA
               # TOLERANCIA: FIJAR EN ITERACIÓN 1

    return {match_rate, overlaps, height_outliers, order_violations, median_h}
```

Esto es 100% geométrico — no depende de mirar la foto, solo de los números que ya devuelve `/position`.

### 2.3 Qué medir en la Iteración 1 (antes de fijar K y TOLERANCIA)

1. Correr `eval_position.py` (sin cambios) sobre las 9 fixtures oficiales → anotar match% actual (línea base real, no inventada).
2. Con la extensión de §2.2, correr sobre esas mismas 9 fixtures (que ya sabemos que están bien, el "banco de boletas rectas ya validado" que menciona el propio docstring de `eval_position.py`) → **la distribución de `heights` y `overlaps` en este set define K y TOLERANCIA**: K = algo por encima del outlier más alto que aparece en boletas ya buenas (ej. p95 o max de ese set × margen), TOLERANCIA = igual, a partir de si aparecen `order_violations` falsos-positivos en fotos que sabemos que están OK. Escribir los números medidos en este mismo doc o en el commit, no dejarlos implícitos en el código.

### 2.4 Árbol de decisión por foto/categoría

1. **`overlaps > 0`** → **INVESTIGAR** ya (máxima prioridad — es literalmente el bug reportado hoy; si el clamp geométrico de `_clamp_adjacent_overlap` no lo evita en una boleta chilena real, hay un caso que ni CORD ni el eval propio cubrieron).
2. **`match_rate` muy por debajo del piso medido en §2.3** → mirar si es un límite ya conocido (dedos tapando en `images-23`, boleta doblada, formato rarísimo) → si es un caso ya documentado como límite conocido → **DOCUMENTAR COMO LÍMITE, no perseguir**. Si aparece un patrón nuevo no visto hoy → **INVESTIGAR**.
3. **`height_outliers > 0` pero `overlaps == 0`** → registrar, **NO bloquea** (el clamp ya evita el daño visual); solo pasa a INVESTIGAR si el outlier es sistemático (aparece en varias boletas distintas, no una sola foto rara).
4. **`order_violations > 0`** → señal de mismatch de nombre por Tesseract, no es el bug de bandas en sí pero es salud general del matching — registrar, no bloquea salvo que sea sistemático.
5. Todo dentro de rango → **ACEPTAR**.

### 2.5 Criterio de parada de esta parte

Parar cuando: Categorías A + B + C + la prioridad alta de D (Mistura, Wok, un supermercado largo) pasaron por el check de §2.2 con `overlaps == 0` en todas y sin patrón nuevo de `match_rate`/`height_outliers` sistemático. Las fotos de baja prioridad de D quedan documentadas como "no cubiertas, alcance dudoso" — no hace falta procesarlas para cerrar esta evaluación.

---

## 3. Velocidad — qué queda por investigar

Orden: primero lo que no cuesta LLM nuevo (reusa datos de hoy o de una sola corrida que Sonnet igual necesita para no-regresión), después lo que sí.

### 3.1 Por qué el retry se dispara tan seguido (`backend/app/ocr.py:2087`, `rel > 0.06 or n_suspect >= 2 or n_suspect/n >= 1/3`)

1. Correr `run_eval.py --pipeline bill --dump` sobre las 9 fixtures AHORA (post `reasoning_effort="low"`, que no estaba en prod cuando se midió "2/9 con retry") — esta corrida hay que hacerla igual para confirmar no-regresión, así que el costo es "gratis" en el sentido de que ya estaba planeada.
2. De esa corrida, tabular por boleta: `rel`, `n_suspect`, si disparó retry, y si el candidato 2 ganó (`(cand2.n_suspect, cand2.rel) < (cand.n_suspect, cand.rel)`).
3. Regla para decidir si subir el umbral (barato — usa datos que ya están, no llamadas nuevas):
   - Si hay boletas con retry disparado por `rel` apenas arriba de 0.06 donde el candidato 2 **no ganó** → evidencia de que el umbral puede subirse sin perder precisión.
   - Si el caso de 19% de descuadre efectivamente necesitó y se benefició del retry → el umbral nuevo (si sube) debe quedar por debajo de ese valor.
   - Buscar un "hueco" en los datos entre los `rel` de retries que sí ayudaron y los que no. Si hay un hueco limpio → mover el umbral ahí. Si no hay hueco (se superponen) → **no cambiar el umbral por `rel`**, y evaluar si `n_suspect` es una señal más limpia; si tampoco → **descartar el cambio, documentar como ya cerca del óptimo**.
4. Bajar el umbral (reintentar MÁS seguido) es la rama cara: necesita forzar retry en boletas que hoy no lo disparan, para ver si mejora precisión — **solo hacerlo si después de 3.1.3 la precisión del set sigue por debajo del baseline ya medido (87.6%)**. Priorizar siempre la rama barata (subir) antes que la cara (bajar).

### 3.2 `reasoning_effort` en el paso de reformateo (`gpt-4.1-mini`, `backend/app/ocr.py:2015-2025`, usa `chat_completion()` no `vision_text()`)

1. Leer `chat_completion()` en `backend/app/ai/provider.py` y confirmar si aplica algún filtro de `reasoning_effort` (los que sí existen están en `vision_text`/`vision_parse`, gateados por `vision_model.startswith("gpt-5")` — `gpt-4.1-mini` no cae ahí).
2. `gpt-4.1-mini` no es un modelo de razonamiento (familia o1/o3/o4/gpt-5) — `reasoning_effort` no es un parámetro válido para él en la API de OpenAI. Confirmar esto (lectura de código + doc de la API, sin llamada nueva) y **cerrar el punto sin tocar código**: el paso ya mide 2-4s de 18-74s totales, el techo de ganancia acá es marginal aunque se pudiera aplicar.

### 3.3 Otras palancas de velocidad, priorizadas por impacto/riesgo (medir antes de tocar código)

1. **[Alto impacto, bajo riesgo]** Re-medir p50/p95 de tiempo por paso (`[ocr][timing]` ya existe en `ocr.py`) sobre las 9 fixtures + las boletas prioritarias de §2.1-D, no solo la boleta de 22+ ítems de hoy. Confirma si 18s es típico o un caso con suerte, y si algún paso que no era cuello de botella hoy (ej. `_populate_positions`) lo es en fotos más difíciles (28 ítems, poca luz).
2. **[Medio impacto, bajo riesgo]** Ajuste del umbral de retry (§3.1) — barato porque reusa la corrida de no-regresión.
3. **[Bajo impacto, bajo riesgo]** Confirmar que `reasoning_effort` no aplica al reformateo (§3.2) — cierre de punto abierto, sin costo.
4. **[Medio impacto, riesgo medio]** `_populate_positions` prueba varias escalas (`_CANDIDATE_TARGET_LONG_SIDES = [0,1300,2000,2800]`) × CLAHE × deskew en paralelo (`ThreadPoolExecutor`, `services/ocr_position/app/main.py`) — hoy solo se midió 3.6s en UNA boleta. Medir en 3-5 boletas difíciles antes de asumir que sigue siendo despreciable; solo tocar código si el tiempo real lo justifica.
5. **[Bajo impacto, riesgo alto — último recurso]** Cambiar `openai_vision_model_bill` (paso 1) por un modelo más barato/rápido. Solo considerar si, después de 1-4, el p50/p95 sigue siendo inaceptable para el usuario — cualquier cambio de modelo obliga a re-correr el eval completo de precisión (87.6% es el piso a no romper) y es la palanca con más riesgo de las cinco.

---

## 4. Orden concreto de ejecución (para Sonnet, sin volver a invocar a Fable)

1. **Fase 0 (gratis, sin LLM):** correr `eval_position.py` tal cual sobre las 9 fixtures oficiales → línea base de match%. Con eso, extender el script con `check_geometry_quality()` (§2.2) y correrlo de nuevo sobre esas mismas 9 → fijar K y TOLERANCIA (§2.3) con los números reales obtenidos, dejarlos anotados en el código o en un comentario/commit.
2. **Fase 1 (gratis, sin LLM):** clasificar las 26 fotos según §2.1. Armar el mapeo de Categoría A (dict/manifest chico). Correr `eval_position.py` extendido sobre Categoría A directo (ya tiene `expected/*.json` reusable) → aplicar árbol de decisión §2.4.
3. **Fase 2 (barato, ~6-8 llamadas LLM sin retry):** generar los `name`-only fixtures de Categorías B, C y D-prioritaria (§2.1) con una sola pasada `_read`+`_reformat` (sin fallback/retry). Correr `eval_position.py` extendido sobre cada una → aplicar árbol de decisión §2.4.
4. **Fase 3 (reusa una corrida que ya hace falta, ~9-13 llamadas LLM con retry incluido):** correr `run_eval.py --pipeline bill --dump` sobre las 9 fixtures oficiales → (a) confirmar no-regresión de precisión (piso: 87.6%), (b) usar esos mismos datos para el análisis de umbral de retry (§3.1) y timing (§3.3.1).
5. **Fase 4 (gratis, solo lectura de código):** cerrar §3.2 (reasoning_effort en reformateo).
6. **Fase 5 (condicional):** solo si Fase 3 muestra que el umbral de retry debería bajar (más agresivo) Y la precisión sigue por debajo del baseline después de intentar subirlo — ahí sí, experimentos nuevos forzando retry en casos borde (§3.1.4). Ídem para §3.3 punto 5 (cambio de modelo) — solo si 1-4 no alcanzan.
7. Documentar resultado final: qué boletas quedaron ACEPTADAS, cuáles como LÍMITE CONOCIDO (con la razón), y si el umbral de retry/otros parámetros cambiaron o se dejaron igual (con la evidencia numérica que lo respalda) — en `docs/SESSION_STATE.md` o un nuevo `docs/PLAN_split_v4.md` de cierre, siguiendo la convención ya usada en el proyecto.

## 5. Criterio de parada global

Esta evaluación se da por terminada cuando:
- Cajas de color: §2.5 cumplido (Categorías A+B+C+D-prioritaria con `overlaps==0`, sin patrón sistemático nuevo).
- Velocidad: p50/p95 medido en el set ampliado confirma que la mejora de hoy (reasoning_effort) se sostiene fuera de la única boleta de prueba, el umbral de retry quedó ajustado con evidencia (o explícitamente descartado con evidencia de que ya está cerca del óptimo), y el punto de reformateo quedó cerrado.
- Ninguna corrección de este plan bajó la precisión del eval oficial de 9 boletas por debajo de 87.6%, ni reintrodujo `overlaps > 0` en el set validado.

No seguir iterando más allá de esto sin un nuevo reporte concreto del usuario — las palancas de mayor riesgo (§3.3.5, §3.1.4) quedan explícitamente pospuestas hasta que las baratas se agoten y sigan sin alcanzar.
