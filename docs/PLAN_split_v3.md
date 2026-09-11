# Plan: mejoras a Dividir Cuenta (feedback usuario 2026-09-09)

Sistema actual: `backend/app/routers/bills.py` + modelos `Bill/BillParticipant/
BillItem/BillItemShare/BillDebt`. Frontend `frontend/src/app/split/page.tsx`
(flujo 5 pasos: Capturar → Revisar → Asignar → ¿Quién pagó? → Resumen).
`set_shares` ya acepta `weight` (0-1) o `units` (float). `set_payers` ya acepta
lista `{participant_id, paid_amount}` (multi-pagador). "Yo" se agrega solo como
participante en `create_bill`.

## Pedidos del usuario
1. Ítems impares / dividir un ítem en partes iguales entre los seleccionados.
2. Reparto flexible por ítem: unidades enteras + porcentaje + montos + "dividir
   en N". Ej: 1 persona 2 uds, otras 2 personas se reparten 3.
3. "¿Quién pagó?" → pagar por porcentajes o estilo Splitwise.
4. BUG: al dividir no se guardó el monto.
5. Opción explícita de guardar o no como gasto.
6. Aunque no se guarde como gasto, la división debe persistir para revisarla luego.

## Diagnóstico del BUG (4)
`finalize_bill` crea la Transaction solo si `my_share > 0` (`me_participant.owes_amount`).
Si el usuario no se asignó ítems, o si el loop `postShares` de `goToWhoPaid` falló
parcialmente (deja ítems sin shares), `owes_amount` de "Yo" queda 0 → no se crea tx
y no hay feedback. Además `finalize` no valida que cada ítem tenga shares que sumen 1.

## Cambios

### Backend (`bills.py`)
- [ ] `FinalizePayload += save_to_expense: bool = True`.
- [ ] `finalize_bill`:
  - validar que cada ítem tenga shares (sum weights ≈ 1); si no → 400 claro.
  - crear Transaction solo si `save_to_expense and my_share > 0`.
  - siempre `bill.status = "finalized"` + calcular debts (aunque no haya gasto).
  - devolver `saved_transaction_id` y `my_share` en `_bill_out`.
- [ ] `GET /bills`: incluir por bill `total_amount`, `my_share`, `status`,
  `n_participants`, `saved_transaction_id` para la lista de historial.
- [ ] `set_shares`: aceptar `percent` en `ShareEntry` (0-100 → weight = percent/100).
  (units > percent > weight en precedencia.)

### Frontend (`split/page.tsx`)
- [ ] Paso 5: switch "Guardar mi parte como gasto" (default ON). Pasar
  `save_to_expense`. Copy: OFF → "División guardada ✓ (sin gasto)".
- [ ] Paso 1: sección "Divisiones guardadas" (bills finalizadas) → abrir resumen
  read-only (reusar UI del paso 5).
- [ ] Paso 3: en el popover de cada ítem, además de "÷ N" agregar modos:
  - "Partes iguales" entre los seleccionados (weight = 1/N, N cualquiera).
  - "Porcentaje" — input % por persona seleccionada.
  - "Montos" — input $ por persona (último absorbe resto).
  Generar `shares` con weights y postear.
- [ ] Paso 4: modo "Por porcentaje" — % por persona → `paid_amount = total*pct/100`.

## Orden
1. Backend (todo) + tests → commit + deploy + verificar con curl.
2. Frontend P0: toggle guardar + historial → commit + deploy.
3. Frontend P1: modos de reparto por ítem (%, montos, partes iguales) → commit + deploy.
4. Frontend P2: pagador por % → commit + deploy.
5. Doc + graphify + MASTER_PLAN.

---

## ESTADO 2026-09-09

### HECHO (commiteado, deployado, verificado en prod)
- **BUG "no se guardó el monto" ARREGLADO.** Causa: `finalize_bill` hacía
  `from ..services import account_svc` (import roto) → 500 al crear la tx. Además
  `reconcile_new_transaction` con firma vieja. commit 7358046.
- **Backend**: `save_to_expense` en `FinalizePayload`; `percent` en `ShareEntry`
  (units>percent>weight); guard "cada ítem repartido al 100%"; `my_share` +
  `transaction_id` + `created_at` en `_bill_out` y `GET /bills`. tests/test_bills.py (4).
- **Frontend P0**: paso 5 toggle "Guardar mi parte como gasto" (default ON;
  OFF → división guardada sin gasto). Paso 1 "Divisiones guardadas" (lista de
  bills finalizadas, tap → resumen read-only). commit b… (split P0).
- **Frontend P2**: paso 4 "Pagamos varios" → toggle Montos / Porcentaje.
- Verificado prod: item share % 40/60 → my_share correcto; finalize sin gasto
  deja `transaction_id:null` y `status:finalized`; historial lista bien.

### P1 — HECHO (2026-09-09, commit siguiente a efcd92e)
Botón **⚙** por ítem en el paso 3 (`split/page.tsx`): panel con modo
**Porcentaje / Unidades / Montos** + input por participante con preview del monto.
- Estado: `advItems: Set<itemId>` (ítems con reparto manual), `advOpen`, `advMode`, `advVals`.
- `applyAdv`: valida (% suman 100 / uds suman qty / montos suman line_total),
  convierte a `weight` (+`units` en modo unidades, para que el backend lo guarde),
  postea vía `postShares`, marca el ítem en `advItems`.
- `goToWhoPaid` salta los ítems de `advItems` (ya tienen shares). `itemFullyAssigned`
  los acepta si sus shares suman ~1.
- Ítem con reparto manual: resumen "%/monto por persona" + Editar / Quitar.
- `advItems` se resetea al empezar una división nueva.
- **Verificado en prod**: ítem qty=5, "Yo 2 uds / Ana 1.5 / Carl 1.5" → Yo debe
  $4.000 (2/5), Ana y Carl $3.000 c/u. Fracciones OK.

TODA la sección "Dividir cuenta" del feedback quedó implementada.

---

## Ronda 2026-09-11 — Compartir WhatsApp + marcador de color arrastrable

**Checkpoint antes de empezar**: tag git `checkpoint-2026-09-11-pre-markers` en
`8868ce3` (para `git reset --hard` / comparar si algo se rompe).

### Bug: Compartir por WhatsApp abría la app vacía — ARREGLADO
Causa: `<a href="https://wa.me/?text=...">` dentro de una PWA instalada
(standalone) no completa la redirección wa.me→api.whatsapp.com→whatsapp://
(necesita una pestaña normal de Safari) → WhatsApp abre sin el texto.
Fix: `navigator.share({text})` (Web Share API, hoja nativa de iOS) con fallback
al link wa.me si el navegador no soporta share.

### Feature: marcador de color arrastrable por ítem sobre la foto — HECHO
- Backend: `ParsedItem.position_y` (0-100, estimación del modelo: altura de la
  línea en la imagen) pedida en `_RECEIPT_PROMPT`; `BillItem.position_y`
  (migración); `PATCH /bills/{id}/items/{id}` acepta `position_y` para
  persistir la corrección manual.
- Frontend (paso 2, Revisar): marcador circular numerado por ítem sobre la foto,
  mismo color (`ITEM_COLORS[idx]`) y número que su fila a la derecha (que ahora
  también muestra el número). Se arrastra verticalmente (Pointer Events +
  setPointerCapture) y persiste al soltar. Sigue el pan/zoom de la imagen.
  Sin `position_y` del modelo → se reparte parejo por índice hasta que se arrastre.
- **Simplificación consciente**: el marcador se posiciona como % de la altura
  del contenedor (no replica el letterboxing exacto de `object-contain`) — la
  alineación inicial puede quedar levemente desfasada si la foto tiene mucho
  espacio vacío arriba/abajo; el arrastre lo corrige en un toque y de ahí queda
  fijo. No se intentó bounding-box 2D exacto — solo altura vertical, suficiente
  para una boleta (lista de una columna).
- Verificado en prod: `position_y` llega desde el OCR (valores monótonos
  20→37.5% en orden de lectura en la boleta Lider), el PATCH persiste, rechaza
  fuera de rango (422).
- Pendiente de verificación real: cómo se ve/siente el arrastre en un iPhone de
  verdad (no se puede probar el gesto táctil desde acá) — pedirle al usuario
  que lo pruebe y reporte si el marcador queda muy desalineado al abrir la
  imagen (ahí se evaluaría implementar el object-contain exacto).

### Iteración 2026-09-11 — de "franja de altura fija" a bbox real
Historial de intentos sobre esta misma feature (documentado para no repetir errores):
1. Círculo numerado, % del panel completo → visible pero "funciona pésimo" (desconectado, números confusos).
2. Franja + mix-blend-mode + aspect-ratio calculado con CSS → **invisible** (stacking context roto por los `transform` de los padres, y/o `imgNatural` nunca se resolvía).
3. Vuelta a % del panel completo, opacidad plana, altura fija 26px → visible pero desalineado (la foto tiene letterboxing por `object-fit:contain` en un panel angosto).
4. (Con Claude Fable 5.1) Cálculo manual en JS del recuadro real que ocupa la foto dentro del panel (mismo algoritmo que `object-fit:contain`, con `ResizeObserver` + `naturalWidth/Height`) → alineación correcta, pero la altura seguía fija en 26px → con 15-20 ítems, las franjas se ENCIMABAN y tapaban toda la foto ("se sombrea cualquier cosa").
5. **(Definitivo)** El usuario preguntó "¿pero esto no lo puede hacer GPT, que es el modelo que ya usamos?" — tenía razón: solo le habíamos pedido un punto vago (`position_y`), nunca un recuadro real con formato estricto. Se cambió el prompt para pedir `bbox_x0/y0/x1/y1` (0-100) por ítem, con reglas explícitas ("ajustado a esa línea, no invadas la vecina") + instrucción de auto-revisar antes de responder. Resultado probado con 3 boletas reales: bboxes ajustados, secuenciales, sin encimarse. **No hizo falta Google Cloud Vision ni ningún servicio nuevo** — el modelo sí podía, solo había que pedírselo bien.

Backend: `ParsedItem`/`BillItem` += `bbox_x0/y0/x1/y1`; `position_y` ahora se
deriva del centro vertical del bbox (compatibilidad con el arrastre existente).
Frontend: la franja usa el bbox real (ancho/alto exactos) vía el mismo `imgBox`
(recuadro real de la foto) que calculó Fable en el paso anterior — ambos fixes
se complementan: uno da el marco de referencia correcto (dónde está la foto
dentro del panel), el otro da el tamaño correcto de cada ítem dentro de ese marco.

Eval tras el cambio de prompt: 92.4% (baseline previo 95.5% — la diferencia es
ruido normal de `danes_vitacura`, una foto oscura con varianza alta entre
corridas incluso antes de este cambio; el resto de las boletas ≥85.7%).
Verificado en prod: bboxes secuenciales y sin encimarse en Lider, Danes, Bao Bar.

### Iteración 2026-09-11 (cont.) — 504 al subir + colores repetidos en boletas largas
El usuario reportó dos problemas reales al probar en su iPhone con una boleta
de 21 ítems (Bar Autóctono):
1. **"OCR 504 al subir boleta"** — el bbox por ítem alargó las respuestas del
   modelo (2/8 boletas del eval tardaron 60.3s/63.7s) justo contra el límite
   de 60s configurado en `vercel.json`. Fix: `maxDuration` 60→180. Verificado
   en prod subiendo la boleta más lenta conocida (~59-64s reales): antes
   hubiera dado 504, ahora HTTP 200 completo.
2. **"Aún no calza"** — con 21 ítems y una paleta fija de 8 colores, el ítem 1
   y el ítem 9 quedaban EXACTAMENTE del mismo color — imposible de distinguir
   entre la foto y la lista en boletas largas. Fix: `itemColor(idx)` genera un
   color pastel por índice con ángulo dorado (137.508°/índice) en vez de una
   paleta fija — separación máxima de tono, no se repite en la práctica sea
   cual sea el largo de la boleta.
3. Optimización de paso: el ancho del bloque de ítems (`bbox_x0/x1`) se pide
   UNA vez por boleta (`items_x0/items_x1`), no por ítem — confirmado con 3
   boletas reales que el ancho apenas varía línea a línea. Menos tokens
   redundantes (la latencia en sí no bajó mucho — el cuello de botella real es
   la lectura línea por línea de boletas largas, no los campos de posición).

Todo commiteado, deployado, verificado con tests + eval + curl a prod.

### Iteración 2026-09-11 (cont. 2) — tap-to-spotlight: fin de "adivinar por color"
El usuario confirmó que el TOTAL de la boleta de 21 ítems estaba bien (la
sospecha de ítems duplicados era un falso positivo mío), pero insistió en
algo más de fondo: incluso con bboxes exactos y colores sin repetir, en una
boleta larga (bar, poca luz) es difícil emparejar a simple vista "qué color
pastel de la lista es cuál banda en la foto". Pidió explícitamente que la
banda sombreada en la foto sea inconfundiblemente el mismo ítem que se toca
en la lista — no solo del mismo color, sino literalmente señalado.

**Fix (con Claude Fable 5.1, delegado)**: en vez de seguir afinando el
parecido de color, se agregó una interacción nueva — tocar el nombre/precio
de un ítem en la lista de la derecha:
- hace zoom + pan automático de la foto centrando el bbox exacto de ese
  ítem (12-18% de alto del panel objetivo, clamp 1x-4x igual que el
  pinch-zoom manual), reusando `imgBox` (mismo cálculo de `object-fit:contain`
  de la iteración anterior) y `applyTransform`
- esa banda queda a opacidad 100% + borde blanco + pulso; TODAS las demás
  caen a opacidad 0.08 — cero ambigüedad, ya no depende de distinguir tonos
- tocar el mismo ítem de nuevo, tocar la foto directamente, o cambiar de
  paso, vuelve la foto a la vista normal (1x)

Matemática del transform verificada a mano (offset del centro del bbox
respecto al centro del contenedor × scale + translate = 0,0 → queda centrado).
Bug de borde encontrado y corregido en el camino: en touchscreens el
`touchstart` de una banda igual burbujea al contenedor pese al
`stopPropagation` del pointerdown — sin una bandera (`bandInteractionRef`),
arrastrar la banda del ítem spotlighted habría cerrado el spotlight a mitad
de arrastre (regresión del gesto de ajuste manual ya existente).

No se tocó backend, `itemColor`, `imgBox`, ni el flujo de arrastre de
`position_y`. Build verificado, revisado línea por línea, commiteado,
pusheado y deployado a prod (`98b7c5b`).

### Iteración 2026-09-11 (cont. 3) — el usuario rechaza el spotlight, pide simple + rápido
El usuario fue tajante: no quería tap-to-spotlight. Cita textual: "yo no
quiero eso! quiero simplemente que se cree la lista que se crea a la
derecha con items en colores pero que esos mismos items se resalten con
los mismos colores en la foto de la izquierda! y que sea mas rapido, se
esta demorando mucho." Pidió explícitamente un proceso de plan → equipo
crítico que lo juzgue y afine → ejecutar, antes de tocar código.

**Revert**: `git revert 98b7c5b` — vuelve la banda a simple, siempre
visible, mismo color que la lista (`itemColor(idx)`, sin cambios), sin
zoom ni dimming. Además se cambió `left`/`width` a **todo el ancho de la
foto** (antes usaba `bbox_x0/x1` para un ancho ajustado — ya no hace falta
si no hay zoom-to-item, y el backend dejó de pedir ese dato, ver abajo).

**Diagnóstico de la lentitud real** (verificado por un pase de revisión
independiente contra el código real antes de tocar nada — encontró 2
supuestos equivocados en el borrador inicial):
1. `ai/provider.py` `vision_json()` (el llamado principal de OCR) manda la
   foto con `detail:"high"` fijo, a resolución original del iPhone — nunca
   se redimensiona. Existía una función `_shrink_for_vision()` ya escrita
   para esto pero **sin usar en ningún lado**. Se conectó al flujo real
   (refactorizada en `_resize_for_vision` + wrapper), resize a 2000px
   ANTES del contraste/nitidez (no después — importa el orden para no
   perder el fix de legibilidad de cantidades, b31d9f2).
2. El lever real y dominante era el **modelo**: `gpt-5-mini` tardaba
   22-46s/imagen. Se le preguntó al usuario explícitamente (no es una
   decisión que se deba tomar en silencio en una app de plata real) y
   eligió cambiar a `gpt-4.1`.
3. Un intento de "simplificar" el bbox a un solo `position_y` (en vez de
   `bbox_y0/y1`) fue RECHAZADO por el equipo revisor: ya se probó antes
   (ver Iteración 2026-09-11 arriba, paso 4) y causa que las bandas se
   encimen en boletas largas. Se mantuvo `bbox_y0/y1` por ítem; solo se
   dejó de pedir `items_x0/items_x1` (ancho del bloque), que ya no se usa.

**Resultado medido con el eval harness (8 boletas reales, antes/después)**:

| | gpt-5-mini (antes) | gpt-4.1 + resize (después) |
|---|---|---|
| Velocidad | 21.9-45.8s/img (avg ~36s) | 2.4-6.9s/img (avg ~4s) — **~10x más rápido** |
| Precisión overall | 91.5% | 90.7% — prácticamente igual |

El 81.4% histórico de gpt-4.1 (ver `MASTER_PLAN.md` §20, comentario viejo
en `config.py`) estaba desactualizado — el prompt mejoró mucho desde esa
medición (bbox, reglas de IVA/neto, etc.), así que el trade-off real hoy
es mucho mejor de lo que sugería el historial.

Env `OPENAI_VISION_MODEL` actualizado en Vercel producción. Tests pytest:
11 fallas en `test_ocr_normalize`/`test_ocr_integration` confirmadas
**pre-existentes** (fallan igual en `main` sin este cambio, vía
`git stash`) — no relacionadas, quedan pendientes fuera de este trabajo.

Todo commiteado (`3f94b46`), pusheado, deployado, verificado en prod.

### Iteración 2026-09-11 (cont. 4) — gpt-4.1 se saltó un ítem, escalamiento
Probando en prod con la boleta real "Bar Autóctono" (la misma de antes, 21
ítems con varias "Promo X" repetidas), el usuario reportó dos cosas con
captura: (1) faltaba un ítem en la lista comparado con lo que dice la foto,
(2) las bandas de color seguían mal alineadas ("los colores siguen
pésimo"). Esperable: gpt-4.1 es más débil que gpt-5-mini justo en el tipo
de boleta compleja para la que se había elegido gpt-5-mini originalmente
(ver commit `2990316`) — el eval de 8 boletas (mayormente simples, un solo
local) no lo capturó porque no incluye ninguna boleta de bar con líneas
repetidas.

Ya existía un retry cuando la suma de ítems no cuadraba con el total
(`_diff/_amount_preview > 0.10`), pero un solo ítem faltante de ~$6.400 en
un total >$100.000 es solo ~5-6% de diferencia — nunca disparaba. Fix:
- Umbral bajado 10%→6%.
- El retry ya NO reintenta con el mismo modelo que se equivocó — escala a
  `openai_vision_model_fallback = "gpt-5-mini"` (nuevo setting), el mismo
  modelo que ya se verificó bueno leyendo bboxes/montos en boletas de bar
  complejas. Esto también arregla las bandas mal posicionadas en los casos
  que escalan, sin tocar el prompt de bbox en sí.

Verificado con eval: overall se mantiene 90.7%, una boleta (dondewilly,
diff≈9%, antes no disparaba con el umbral de 10%) escaló y llegó a 100%.
La mayoría (7/8) se mantuvo rápida (2.2-5.2s); solo la que escaló pagó el
costo de la segunda pasada (~40s) — exactamente el trade-off buscado: rápido
por defecto, cuidadoso solo cuando hace falta.

Commiteado (`ce37923`), pusheado, deployado. **Pendiente real**: no se pudo
reproducir la boleta exacta "Bar Autóctono" localmente (solo se tuvo el
screenshot de la app, no la foto original) — falta que el usuario la
vuelva a subir en prod para confirmar que ahora sale completa y las
bandas calzan.
