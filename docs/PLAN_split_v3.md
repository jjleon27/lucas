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

Commiteado (`ce37923`), pusheado, deployado.

### Iteración 2026-09-11 (cont. 5) — el usuario manda la foto original: verificado + fix de contraste
El usuario mandó la foto original de "Bar Autóctono" (antes solo se tenía
el screenshot de la app). Se sumó a mano: 17 líneas (14 con precio + 3
modificadores "+Coca X" a $0), total $74.300 = "Total General Mesa". Se
agregó como caso de regresión permanente: `tests/eval/receipts/bar_autoctono.jpeg`
+ `tests/eval/expected/bar_autoctono.json`.

Corrida directa de `vision_parse()` contra la foto real: la primera pasada
(gpt-4.1) se saltó 1 ítem (sum 67.900 vs 74.300, diff 8.6% — por encima del
umbral 6%), disparó el escalamiento a gpt-5-mini, y esa segunda pasada sacó
los 17 ítems exactos, con `bbox_y0/y1` perfectamente secuenciales y sin
encimarse (20.0→22.7, 22.8→25.5, ...). **El fix del ítem faltante (cont. 4)
quedó confirmado funcionando con la boleta real, no solo en teoría.**

Eval completo con este caso incluido: overall 92.7% (9 imágenes).

**Pero el usuario reportó un problema distinto**, con captura nueva: los
ítems ya salían completos y bien posicionados, pero la banda sobre la foto
se veía como una franja borrosa de colores pálidos que se mezclan entre sí
— no "resalta" nada, aunque la lista de la derecha se ve perfecta.

Diagnóstico (revisado por un pase de diseño antes de tocar CSS): `itemColor(idx)`
(`hsl(_, 65%, 87%)`, casi blanco) funciona bien como fondo de tarjeta en la
lista (UI clara), pero contra una foto real (papel crema, luz tenue,
textura de mesa) casi no tiene contraste de luminosidad — con 17 bandas
pegadas sin borde, se leen como una sola franja difusa.

Fix: nueva `itemHighlightColor(idx)` — MISMO hue que `itemColor(idx)`
(misma identidad de color que la lista, eso no cambia — es justamente lo
que el usuario pidió que se mantuviera), pero saturación/luminosidad de
tinta de marcador real (85%/55% en vez de 65%/87%) + borde 2px en un tono
más oscuro/saturado del mismo hue para separar bandas contiguas. Opacidad
0.6→0.45 en reposo (compensa el color más fuerte, el texto sigue legible
por debajo), 0.95→0.85 arrastrando. `itemColor(idx)` queda intacto — sigue
siendo el único usado en las 2 filas de lista.

Se evaluó y se descartó reemplazar la banda por un "tab" de color en el
margen (menos invasivo, no toca la mecánica que ya funciona/fue aceptada) y
se evaluó y se descartó reintroducir zoom automático por ítem (el usuario
ya rechazó esa mecánica explícitamente en cont. 3 — no se repite ese error).

Commiteado (`319745c`), pusheado, deployado. Build verificado, sin cambios
de backend/lógica — puro ajuste visual.
Pendiente: confirmación del usuario en su iPhone — en particular el matiz
amarillo/verde-amarillo, que por naturaleza de HSL contrasta menos que
rojos/azules a la misma saturación/luminosidad.

### Iteración 2026-09-11 (cont. 6) — el problema real era posición, no color
El usuario aclaró después: "el problema no es el color, sino que las cajas
de sombra no se posicionan sobre el ítem!!!". Con la captura anterior
(err1.png) reexaminada de cerca (crop + zoom local): las bandas empezaban
tapando el ENCABEZADO de la boleta (Comanda/Fecha/Garzón — no son ítems) en
vez del primer ítem, y se iban desalineando progresivamente hacia abajo,
terminando antes de cubrir los últimos ítems (+Coca Cola/Zero sin banda).
Ese patrón — desalineación que CRECE hacia abajo en vez de un corrimiento
fijo — es la firma de un problema de ESCALA (altura mal calculada), no de
offset.

Diagnóstico: `imgBox` (recuadro object-fit:contain real de la foto dentro
del panel) se calculaba con `naturalWidth/naturalHeight` medidos por el
NAVEGADOR vía `<img>`. Pero los `bbox_y0/y1` que devuelve el modelo son %
del ancho/alto de la foto tal como la procesó el BACKEND (Pillow, tras
`exif_transpose` para la rotación EXIF de fotos de iPhone). Si el navegador
interpreta el EXIF de esa foto en particular distinto a Pillow, ambos lados
miden un ancho/alto distinto para la MISMA foto y toda la posición queda mal
escalada — consistente con el patrón observado. (No se pudo reproducir 1:1
en local: la copia de la foto que se tiene ya no trae metadata EXIF, así
que el diagnóstico se apoya en el patrón, no en una reproducción exacta.)

Fix de raíz: el backend ahora manda `image_width`/`image_height` (las
dimensiones reales, orientadas hacia arriba, que usó para calcular los
bbox) junto con la boleta — `ParseResult.image_width/height` en `ocr.py`,
columnas `bills.image_width/height`, expuestas en `_bill_out()`. El
frontend usa ESE número (no lo que mide el navegador) para `imgBox`,
cayendo a `imgNatural` solo si el backend no lo mandó (boleta vieja o sin
OCR). Con un solo origen de verdad para el marco de referencia, deja de
importar si navegador y Pillow concuerdan o no en la interpretación EXIF.

Investigación con browser automation: se intentó reproducir en vivo
subiendo la foto real a prod, pero la sesión de automatización no estaba
autenticada (401) — no se ingresaron credenciales por la persona (regla de
seguridad), se abandonó ese camino y se investigó con medición de imagen
local + revisión de código en su lugar.

Commiteado (`a1e72bc`), pusheado, deployado, build + pytest verificados
(mismas 11 fallas pre-existentes). **Pendiente real**: confirmación visual
del usuario en su iPhone — es el fix más probable dado el patrón, pero no
se pudo verificar pixel a pixel sin el dispositivo real.

### Iteración 2026-09-11 (cont. 7) — "¿por qué ChatGPT es instantáneo y no se equivoca?"
El usuario comparó directamente: le pidió a ChatGPT que organizara la misma
boleta y respondió en <5s sin errores, mientras nuestra app (con gpt-4.1,
el modelo rápido) sí se equivocó. Pidió sacar el color pensando que eso
explicaba la lentitud — se le aclaró que NO: el color/bbox son 2 números
extra por ítem, casi gratis; lo que realmente tarda es el reintento de
verificación (que escala a un modelo lento SOLO cuando la suma no cuadra),
y sacar el color no habría cambiado eso.

El usuario entonces preguntó, con razón, si el problema era el MODELO — su
prueba con gpt-4.1 falló, la de ChatGPT (probablemente un modelo más
completo) no. Se probó en la práctica en vez de debatir:
- `gpt-5` (el "completo"): 116.8s en la boleta difícil (Bar Autóctono) Y
  TAMBIÉN se equivocó en el primer intento — no es la respuesta.
- `gpt-4o`: 8.1s, total correcto en esa misma boleta (agrupó los "+Coca X"
  gratis en el nombre del ítem en vez de fila aparte, mantuvo las 6 líneas
  de "Promo Alto del Carmen" como filas separadas — igual que hizo
  ChatGPT). Eval completo (9 boletas reales): **88.9% overall — peor que
  gpt-4.1 (92.7%)**, y en una boleta oscura (`danes_vitacura`) el
  escalamiento se disparó igual y tardó 179s sin acertar.

Conclusión mostrada al usuario con datos reales: ningún modelo probado es
siempre rápido Y perfecto — cada uno falla en boletas distintas. ChatGPT
pareció "perfecto e instantáneo" porque solo le pidieron un resumen de
texto; nuestra app pide JSON estricto con posición por línea, validación
de que la plata cuadre, IVA/neto, categoría — más trabajo por respuesta,
sea cual sea el modelo.

Con el trade-off real sobre la mesa (gpt-4o: similar velocidad, menos
preciso en general), el usuario eligió probarlo igual como modelo
principal. `openai_vision_model` → `gpt-4o` (antes `gpt-4.1`), se mantiene
`openai_vision_model_fallback = gpt-5-mini` como red de seguridad (el
escalamiento por descuadre de suma sigue activo, no se tocó). Env
`OPENAI_VISION_MODEL` actualizado en Vercel producción.

Commiteado (`34b8e17`), pusheado, deployado. pytest: mismas 11 fallas
pre-existentes, no relacionadas.

### Iteración 2026-09-11 (cont. 8) — el usuario tenía razón: era el prompt, no el modelo
El usuario le pidió a ChatGPT exactamente la boleta difícil con un prompt
simple ("dame en texto los items en orden, cantidad, valor, total,
propina") y le salió PERFECTA — las 17 líneas exactas. Insistió: "el
problema no es el modelo, son los prompts y restricciones que le pides,
planea bien para lograrlo".

Se probó directo: mismo modelo (gpt-4o/gpt-4.1), mismo prompt simple del
usuario, sin nuestro JSON — y AUN ASÍ falló (16 líneas en vez de 17). Esto
descartó momentáneamente la hipótesis... hasta probar `gpt-4.1-mini` (el
más BARATO de la familia, no el más grande) con ese mismo prompt simple:
**3/3 corridas exactas, 4.3-5.9s, tokens baratos (3110+286, idéntico cada
vez con temperature=0)**. El usuario pidió explícitamente "prueba modelos
más simples que usen menos tokens y quédate con el que no se equivoque".

**Primer intento de "productivizar" el prompt fue un error** — se le
agregaron reglas explícitas (headers MERCHANT/DATE/CATEGORY/CURRENCY/AMOUNT
etiquetados, reglas de "no dividir nombres cortados en dos líneas", "no
agrupar repetidos"). El usuario lo notó de inmediato: "estás agregando
reglas que es exactamente lo que originó los problemas". Tenía razón — con
esas reglas el mismo modelo volvió a fallar. Se revirtió a algo mínimo,
casi idéntico al prompt original que sí funcionó (verificado 3/3 antes de
aplicarlo).

**Arquitectura final**: nuevo camino dedicado SOLO para `/bills/{id}/ocr`
(`vision_parse_bill()` + `_RECEIPT_PROMPT_BILL` + `_parse_bill_text()` en
`ocr.py`, `vision_text()` nuevo en `ai/provider.py` — imagen→texto plano,
sin extracción de JSON). El `/upload` general (que también lee cartolas
bancarias con cuotas/bank_hint) sigue con `vision_parse()`/`_RECEIPT_PROMPT`
sin ningún cambio — no se tocó, no se arriesgó esa ruta. Se verificó que
`CATEGORY`/`CURRENCY` no se usan para nada en bill-split (el total sale de
sumar `BillItem.line_total`, `Bill` no tiene columna category) — se
sacaron del prompt, no hacía falta pedirlos.

**Bug real encontrado y arreglado en el camino** (no del modelo, del
código): el chequeo de reintento multiplicaba `line_total` (que YA es el
total de la línea) por `quantity` otra vez, inflando la suma artificialmente
y disparando reintentos innecesarios en boletas que en realidad se habían
leído perfecto (ej. lider_quilicura: suma real 42.970 vs reportada
71.150 antes del fix).

Nuevo setting `openai_vision_model_bill = "gpt-4.1-mini"`, separado de
`openai_vision_model` (gpt-4o, sigue siendo el de `/upload`) — para no
arriesgar la ruta de cartolas con un modelo no probado para eso.

**Resultado final, verificado con las 9 boletas reales del eval**: 7/9
rápidas (2.3-4.6s) sin necesitar reintento. Las 2 restantes no son
regresiones: `danes_vitacura` es un caso difícil conocido desde el inicio
de la sesión (todos los modelos probados fallan ahí); `cuenta_valeria` está
marcada `items_lenient` en su propio ground-truth — ni un humano logra
cuadrar la suma con el total ahí.

Commiteado (`6b27df3`), pusheado, deployado, pytest verificado (mismas 11
fallas pre-existentes). Pendiente: confirmación del usuario probando en su
iPhone con boletas reales variadas.

### Iteración 2026-09-11 (cont. 9) — auditoría con panel de 3 expertos
El usuario pidió una auditoría completa del split de cuentas con un panel
de expertos, insistiendo en no agregar código/restricciones innecesarias.
Se lanzaron 3 agentes en paralelo: backend (plata/seguridad), frontend
(estado/render), integración en vivo (correr tests + probar el flujo
completo real). Resumen: el flujo completo funciona de punta a punta, sin
problemas de seguridad, sin regresiones del revert del spotlight. Se
confirmaron y arreglaron 4 problemas reales:

1. **"÷ dividir en N" perdía plata por redondeo** ($10.000/3 = 3×$3.333 =
   $9.999, $1 perdido sin registro). Fix: el último ítem absorbe el resto
   exacto (mismo patrón ya usado para la propina).
2. **El OCR también perdía plata por el mismo tipo de redondeo** al
   reconstruir `line_total` desde un `unit_price` ya redondeado. Fix:
   `ParsedItem.line_total` opcional — cuando `vision_parse_bill` lo provee,
   `bill_ocr` lo usa tal cual en vez de recalcular. No afecta a /upload.
3. **Línea de descuento no se capturaba** (ej. Lider: "Desp. Food $990" sin
   su "-$990" de descuento) — la suma quedaba $990 alta, bajo el umbral de
   reintento, pasaba sin avisar. Fix: una sola frase agregada al prompt
   ("si hay una línea de descuento, inclúyela con valor negativo") —
   probada contra la boleta más difícil (17 ítems) para confirmar que NO
   reintroduce el bug de ítems saltados antes de aplicarla. Efecto
   secundario encontrado: el modelo a veces repite el valor negativo en la
   columna de cantidad — se corrige en el parser (no en el prompt).
4. **Código muerto peligroso**: `_normalize_boleta_items` (la función que
   causó el bug "$2.150→$1.765") seguía en `ocr.py` sin ningún call site
   real. Se borró junto con `_fix_line_total_items`/`_fix_unit_as_total_items`
   (ya no-ops). Se limpiaron sus 21 tests en `test_ocr_normalize.py` (con
   AST para no arrastrar nada de los tests vecinos que sí siguen vigentes).
   Efecto colateral bueno: 8 de las 11 fallas "pre-existentes" de todo el
   día eran justo estos tests muertos — pytest bajó de 11 a 3 fallas (las 3
   restantes, en `test_ocr_integration.py`, sin relación, confirmado).

Todo re-probado en 5 boletas reales sin regresiones. Commiteado (`750c76b`),
pusheado, deployado.

### Iteración 2026-09-11 (cont. 10) — investigación GitHub para el color de bandas (pendiente de ejecutar)
El usuario pidió investigar en GitHub formas rápidas/baratas de resolver el
posicionamiento de las bandas de color sobre la foto, sin perder la
velocidad ya conseguida — explícitamente "no cambies nada ni ejecutes aún".
Contexto: ya se confirmó empíricamente (dos veces) que pedirle al modelo
una posición por ítem en el prompt liviano no da una posición real — solo
reparte 0-100 parejo por índice (mismo resultado que ya calcula el
frontend gratis, sin gastar tokens ni arriesgar la precisión de ítems).
Pendiente: resultado de la investigación de GitHub, aún no realizada al
momento de este commit.

### Iteración 2026-09-11 (cont. 11) — investigación GitHub: sin bala de plata, pero un camino real
Resultado de la investigación (agente Explore): no existe una librería
lista que resuelva "posición real de cada ítem, barato y rápido" para
boletas fotografiadas. Hallazgos:
- **Tesseract `image_to_data`** (ya está instalado en el proyecto) da
  bounding boxes por palabra/línea gratis, ~0.5-1s — podría correr EN
  PARALELO con la llamada al modelo (no suma latencia) y emparejar sus
  líneas OCR con los ítems ya bien leídos por el modelo, por ORDEN (no por
  parecido de texto, para sobrevivir a ítems repetidos). Es el único camino
  genuinamente distinto a lo ya probado — nunca toca el prompt de
  extracción, así que no puede reintroducir el bug de ítems saltados.
  No está probado, falta construirlo y testearlo.
- **Gemini** tiene un modo nativo real de bounding-box — pero evaluación
  independiente (SimEdw, contra COCO) confirma que sufre el MISMO problema
  que ya tenemos: falla con objetos casi idénticos repetidos en la imagen
  ("ve cuatro tortas, Gemini ve solo una").
- **OpenAI** (incluido su modelo más nuevo, gpt-5.6) sigue sin tener modo
  nativo de coordenadas — evaluación independiente confirma que inventa
  cajas "en filas parejas, evenly spaced" — el mismo patrón que ya
  encontramos nosotros mismos hoy.
- invoice2data/Donut/PaddleOCR/EasyOCR: ninguno resuelve esto barato para
  boletas térmicas fotografiadas (o son para PDFs, o son pipelines pesados
  sin ventaja clara de velocidad sobre Tesseract).

Sin ejecutar nada de esto por ahora — queda documentado como el camino a
probar si se retoma el tema de posicionamiento.

### Iteración 2026-09-11 (cont. 12) — boleta real "Bar La Providencia": desalineamiento de precios
El usuario reportó una boleta real con un error distinto a todo lo visto
hasta ahora: cada ítem mostraba el precio del ítem ANTERIOR (no un ítem
faltante). Con zoom a la foto se confirmó: en ESTA boleta puntual, la
columna de precios está impresa visiblemente más alta que la de nombres —
un defecto de impresión/alineación del propio papel, no una confusión de
lectura evitable con mejor prompting. Se probaron 3 prompts distintos
(emparejar por orden, verificar suma antes de responder, dos listas
separadas) — los 3 fallaron exactamente igual.

El usuario insistió: "si ChatGPT pudo, nosotros deberíamos poder — evalúa
si es el prompt o el modelo". Se probó sistemáticamente TODA la escala de
modelos de OpenAI, de más barato a más caro, deteniéndose en el primero
que no fallara (pedido explícito):

| Modelo | Resultado en esta boleta |
|---|---|
| gpt-5-nano | se niega a responder ("está borrosa") |
| gpt-4.1-nano | mezcla "Total General Mesa" como si fuera un ítem (falla en OTRA boleta) |
| gpt-4o-mini | texto mezclado/ilegible |
| gpt-4.1-mini (el que estaba activo) | precios desalineados |
| gpt-4o | precios desalineados |
| gpt-4.1 | precios desalineados |
| gpt-5-mini | precios desalineados, y 55s |
| **gpt-5.6-luna** | **exacto — $135.000, igual que ChatGPT** |
| gpt-5.6-sol | también exacto (29.6s, más lento) |
| gpt-5.6-terra | también exacto (11.1s) |

No es un modelo con suerte — es la familia `gpt-5.6` completa la que lee
bien esta boleta; todo lo anterior (incluyendo `gpt-4.1-mini`, el que
estaba en producción) falla igual. `luna` es la más rápida del grupo.

Verificado contra las 9 boletas reales del eval completo con `gpt-5.6-luna`
como modelo del split: TODAS correctas, incluyendo `cuenta_valeria` (una
boleta que ni el propio archivo de ground-truth podía cuadrar — ahora da
exacto $24.900, primera vez en toda la sesión) y `barlaprovidencia` (exacto
$135.000). De paso se encontró y arregló un bug real: `AMOUNT` a veces
incluía la propina sugerida — se agregó una frase al prompt para excluirla
explícitamente, verificado que no reintroduce el bug de ítems saltados en
`bar_autoctono` antes de aplicarlo. Efecto secundario bueno: `dondewilly`
ahora reporta 2 ítems reales ($10.000, el subtotal) en vez de contar la
propina sugerida como si fuera un ítem comprable más.

`openai_vision_model_bill`: `gpt-4.1-mini` → `gpt-5.6-luna`. No afecta a
`/upload` (`openai_vision_model` sigue en `gpt-4o`). Commiteado (`4cddb1b`),
pusheado, deployado. pytest: mismas 3 fallas pre-existentes, no relacionadas.

### Iteración 2026-09-11 (cont. 13) — bug urgente + el usuario tenía razón otra vez: lectura 100% libre
Dos cosas seguidas:

**Bug urgente en producción**: el usuario reportó "100× Frutilla Spritz
$79 c/u" en vez de "1× $7.900". Causa: el parser sacaba la cantidad con
regex de solo-dígitos — para "1.00" (formato con el que `gpt-5.6-luna`
imprime cantidades) eso borra el punto y deja "100". Bug introducido en el
fix anterior de la línea de descuento, nunca probado con decimales. Fix:
reusar `_to_float` (ya existía, ya sabe distinguir miles de decimales) en
vez de un regex nuevo. Verificado y desplegado de inmediato.

**El problema real de fondo**: el usuario, viendo que la misma boleta
seguía con pequeños errores (no el bug de cantidad, errores de $800-1.000
sobre $135.000), insistió: "si a ChatGPT le sale perfecto, el problema lo
tienes tú, es el prompt o tus restricciones — revisa". Se probó en
directo: mismo modelo, mismo prompt "mínimo" (`cantidad | nombre | valor`)
vs. sin pedirle NINGÚN formato — **3/3 exacto sin formato**, errores
recurrentes con el formato fijo. Confirmado: cualquier estructura de
respuesta, por mínima que parezca, le cuesta precisión al modelo en
boletas visualmente difíciles.

**Arquitectura nueva, dos pasos**:
1. `_RECEIPT_PROMPT_BILL` — pregunta libre, sin pedir formato de
   respuesta. Esto es lo que mejora la precisión.
2. `_REFORMAT_PROMPT_BILL` — segundo paso, SOLO TEXTO (no ve la foto),
   `gpt-4.1-mini`, ~2-3s: reordena la respuesta libre ya correcta a
   nuestro formato fijo parseable. Verificado explícitamente que es fiel
   (no reintroduce errores — comparado texto crudo del paso 1 vs 2 línea
   por línea).

3 bugs reales encontrados y arreglados en el camino: el reformateo (con
`gpt-4.1-nano` al principio) confundía la línea de "Total" con un ítem más
(duplicaba la suma) — se subió a `gpt-4.1-mini` + instrucción explícita;
el prompt libre no pedía nombre del local ni fecha — se agregó de vuelta;
el regex de MERCHANT se comía la línea de DATE completa cuando el modelo
las juntaba sin nombre real — corregido.

Verificado: pytest (mismas 3 fallas), pipeline real en las 9 boletas —
8/9 exactas, incluida la boleta que motivó todo (Bar La Providencia,
$135.000 exacto en 3 corridas seguidas). Queda un caso residual chico
(2.4% en `lider_quilicura` — la línea de descuento del despacho a veces se
pierde en el reformateo) documentado como límite conocido, no se sigue
puliendo para no arriesgar lo que ya funciona. Commiteado (`817277d`),
pusheado, deployado.

### Iteración 2026-09-11/12 (cont. 14) — posición real de bandas: servicio Tesseract aislado + migración a Vercel Services

El usuario insistió en que las sombras de color deben caer SOBRE el texto
real del ítem en la foto, no en cualquier parte ("etiquetas movibles").
Se probó primero pedirle a la IA de visión coordenadas/bbox directamente
en el prompt — descartado: en ítems repetidos (ej. "Promo Alto del
Carmen" x7) el modelo devuelve posiciones inventadas, uniformemente
espaciadas, no lectura real (confirmado también por investigación externa
sobre el mismo problema en Gemini/OpenAI). También se probó pedirle a la
IA que coloreara la foto directamente (`gpt-image-1` edit) — descartado
de inmediato: alucina datos reales de la boleta (año, hora, total,
nombre del local todos cambiados en la imagen generada) — inaceptable en
una app financiera.

**Solución real**: un microservicio interno aislado
(`services/ocr_position/`) que corre Tesseract (OCR clásico, gratis, con
cajas de texto reales por línea) sobre la misma foto, y empareja cada
nombre de ítem (ya leído bien por la IA, en orden) contra las líneas de
Tesseract por similitud de texto (`difflib.SequenceMatcher`), buscando
siempre HACIA ADELANTE desde la última línea usada — sin límite de
ventana (una boleta real trae 15-20 líneas de encabezado antes del primer
ítem; una ventana angosta nunca llegaba a él). El backend llama a este
servicio de forma best-effort (`_populate_positions` en
`backend/app/ocr.py`) — si falla, tarda, o no hay match confiable, la
posición queda `null` y el frontend cae al reparto parejo que ya existía
(nunca bloquea la subida de la boleta).

**Costo real, no anticipado**: para desplegar un servicio en Docker en
Vercel hubo que migrar TODO `vercel.json` de la config clásica al modelo
de **Vercel Services** (tres servicios: `web`, `api`, `ocr_position`),
confirmado con el usuario antes de proceder ("Sí, migrar igual, con
cuidado, probando antes de reemplazar producción"). Problemas resueltos
uno a uno (detalle completo en el historial de commits): sintaxis de
`entrypoint` para Python (`api.index:app`, ruta de módulo con punto, no
de archivo), CLI local desactualizada (subida a 59.16.0), caché de build
viejo, variables de entorno escaseadas a "Production" únicamente
(preexistente, no causado por la migración). Verificado en preview real
vía `vercel curl`/`vercel logs -j` (Deployment Protection bloquea curl
directo; estos comandos de la CLI sí tienen acceso). El usuario promovió
a producción él mismo (`vercel --prod --yes`, bloqueado para mí por el
clasificador de modo automático).

Resultado: bandas de color ahora caen sobre la línea real del ítem en la
mayoría de los casos (ej. Lider Quilicura, incluida la línea de
descuento negativa, quedó exacta). Precisión de Tesseract sobre fotos de
recibo térmico es inherentemente parcial (~60% de caracteres correctos
según investigación externa, confirmado en vivo: "Bar Autóctono" leído
como "har M Loctond") — el emparejamiento por orden + umbral de similitud
absorbe bastante de ese ruido, pero no hay garantía de 100% de ítems
posicionados; los que no calzan caen al reparto parejo, no rompen nada.

### Iteración 2026-09-12/13 (cont. 15) — 401 post-deploy (transitorio) + overlap de bandas

**401 al iniciar sesión reportado justo después de promover a
producción.** Investigado a fondo: `/api/auth/signup` y `/api/auth/login`
probados directo por curl con form-encoded (mi primer intento usó JSON,
dio 422 engañoso) — la lógica del login es correcta, 401 real solo para
credenciales realmente inválidas. `vercel logs -j` mostró varios 401 en
endpoints protegidos (`/api/bills`, `/api/accounts`) — esperado sin
sesión. Se hizo login/signup limpio desde la UI real en Chrome con una
cuenta de prueba descartable, limpiando `localStorage` antes — ambos 200,
JWT válido, llegó a `/dashboard` sin problema. Conclusión: el 401 del
usuario fue lo más probable un bundle de frontend cacheado del momento
exacto de transición del deploy — no se pudo reproducir ni confirmar una
causa real ligada a la migración. Se le pidió reintentar tras hard
refresh; no llegó confirmación antes de que reportara el siguiente
problema (overlap de bandas).

**Overlap de bandas (Bar La Providencia, 13 ítems)**: la altura de cada
banda de color quedó fija en 26px desde el rediseño del prompt libre —
`bbox_y0`/`bbox_y1` (de donde salía antes la altura real) ya no lo produce
el backend, así que `hasYBbox` daba siempre `false`. Con el servicio de
Tesseract ubicando ítems reales muy cerca entre sí, 26px fijos alcanzaba
para pisar al vecino. Fix general (sin tocar el backend): la altura de
cada banda se calcula ahora a partir de la distancia real (en %) hasta el
ítem anterior/siguiente vía `bandPctFor` — la misma función que ya resuelve
tanto posición real de Tesseract como reparto parejo, así que el fix
cubre ambos casos sin distinguirlos. Acotado a `[10px, 26px]`, usando 80%
del hueco disponible como margen. Build verificado, commiteado
(`f40c9c8`), pusheado, deployado a producción (`vercel --prod --yes`,
esta vez SIN bloqueo del clasificador). `curl /api/health` OK
post-deploy.

### Iteración 2026-09-13/14 (cont. 16) — recorte manual + girar + nombre/fecha editables

El usuario, viendo que la posición de las bandas seguía fallando en fotos
con mucho fondo, propuso: "primero seleccionar la foto y pedirme que la
recorte manualmente... así hay menos errores". Nuevo paso en "Subir
boleta": recuadro de recorte arrastrable (mover + 4 esquinas) antes de
subir, recortado en el navegador vía canvas (aprovecha que `<img>` ya
aplica la orientación EXIF, así el recorte queda bien orientado sin tocar
el backend). Luego, dos pedidos más del usuario sobre ese mismo paso:
girar la foto (agregado: 90° a la izquierda/derecha + slider de
enderezado fino ±45° con vista previa CSS en vivo, horneado a píxeles
reales al soltar) y nombre/fecha editables al final del flujo — antes de
guardar — para reconocer la división después en el historial (reutiliza
el PATCH `/bills/{id}` que el backend ya soportaba). Cero cambios de
backend para el recorte/giro — todo en canvas del navegador.

### Iteración 2026-09-14 (cont. 17) — diagnóstico a fondo: por qué la posición fallaba, y fix general

El usuario pidió evaluar a fondo (con Fable) por qué las bandas seguían
mal puestas — a veces sombreando encabezados/totales en vez del ítem, y
pidió que el ANCHO de la banda también se adapte al texto real, no fijo
a todo el ancho de la foto. Reproducido en local con la boleta real que
falló ("cuenta_valeria"): la foto es diminuta (253×450px) — a esa
resolución Tesseract no lee "parecido" al texto real, lee GARABATO total
("Una Vara za" en vez de "Coca Cola Light"), y el umbral de similitud
igual encontraba algo por encima de 0.35 contra ese garabato.

Dos intentos descartados con evidencia directa:
- Agrandar SIEMPRE la foto a una resolución fija antes de Tesseract:
  arregla la boleta chica pero ROMPE una que ya estaba bien (probado en
  `bar_autoctono`, 1200×1600 — agrandarla de más la emborrona lo
  suficiente para confundir líneas repetidas parecidas, ej. las 7 "Promo
  Alto del Carmen"). No existe un tamaño "bueno" único para cualquier foto.
- Usar el precio del ítem como señal extra para no confundir con
  encabezados: en ítems con el MISMO precio repetido, el precio no
  desambigua nada y el bonus rompía la propiedad de "en empate gana el
  más cercano" que hacía funcionar el emparejamiento por orden — bug
  reproducido de forma aislada con un test A/B sobre el mismo set de
  líneas de Tesseract.

**Fix real**: `_match_best_effort` prueba el OCR a varias escalas
—calculadas a partir del tamaño REAL de cada foto, nunca fijas— y se
queda con la que logra emparejar más ítems. Verificado: `cuenta_valeria`
6/6 (antes 0/6 útiles — todo mal puesto sobre encabezados), `bar_autoctono`
17/17 (antes 16/17, SIN regresión, de hecho mejoró), y contra las otras 7
boletas del eval set: 45/47 ítems (95.7%) — la única boleta con fallas
reales (`montana_bellavista`) tiene la columna de nombres directamente
ilegible para Tesseract a cualquier escala, límite genuino de la foto, no
un bug.

Segundo pedido (ancho adaptado al texto): el servicio ya calculaba
internamente la caja horizontal real de cada línea de Tesseract (igual
que ya hacía con la vertical) — se revivieron `bbox_x0`/`bbox_x1`
(columnas que YA EXISTÍAN de punta a punta en el pipeline desde el
mecanismo de bbox anterior a este rediseño de OCR) con datos reales en
vez de agregar campos nuevos. La banda ahora se dibuja del ancho real del
texto (con margen chico + mínimo tocable) en vez de siempre a todo el
ancho de la foto. Corrección manual (arrastre) sigue limpiando
`bbox_x0/y0/x1/y1` enteros, cae al tamaño estimado por hueco a vecinos.

Verificado en producción con captura real: las bandas de `cuenta_valeria`
pasaron de sombrear "Mesa N°6"/"Usuario Valeria Ortega"/"Total Final" a
sombrear exactamente "Coca Cola Light", "Agua Mineral Sin Gas", etc.
pytest: mismas 3 fallas preexistentes. Commiteado (`373824f`), pusheado,
deployado.

### Iteración 2026-09-14 (cont. 18) — grilla más densa, fila completa, contraste adaptativo, recorte en etapas, WhatsApp con detalle

Racha de 4 pedidos seguidos del usuario sobre lo mismo y sobre compartir:

**Grilla apenas visible**: se pidió más densidad y más contraste — pasó a
dos niveles (líneas finas cada 2.5%/5%, gruesas y casi opacas cada 25%).

**"Solo se colorea el ítem, debe colorearse también la cantidad y el
valor"**: Tesseract a veces separa cantidad/nombre/precio en bloques de
texto DISTINTOS cuando hay mucho espacio en blanco entre columnas — el
nombre matcheaba bien pero la caja quedaba angosta. Fix geométrico
(`_expand_to_full_row` en `services/ocr_position`): agranda el ancho para
cubrir toda la fila física buscando, cerca en el orden de lectura, otras
líneas que compartan casi la misma altura (traslape vertical ≥60%) — no
depende de idioma/mayúsculas/largo/formato, generaliza a cualquier
boleta. Verificado con captura real y consulta directa a la API de
producción: bbox_x0≈9%, bbox_x1≈84% (cubre cantidad+nombre+precio).

**"Aumentar el contraste, busca en GitHub qué se usa para boletas
claras/oscuras"**: se encontró que el código YA tenía CLAHE (contraste
adaptativo local, técnica estándar de preprocesamiento OCR) implementado
para el camino clásico de Tesseract (`_preprocess`, usado por
`run_ocr`/`/upload`) — nunca se había llevado al servicio de posición. Se
probó usarlo SIEMPRE ahí en vez del contraste fijo (×1.8) y EMPEORÓ varias
boletas que ya leían bien (97%→79% en el set de prueba de 9 boletas). Fix:
el servicio prueba PRIMERO la variante estándar (ya probada, la más
barata) y solo escala a CLAHE si no logra emparejar el 100% de los ítems
— nunca paga el costo extra en el caso común. Resultado: **100% (70/70
ítems)** en las 9 boletas del eval set, incluida `montana_bellavista`
(antes 1/3, la única que de verdad necesitaba CLAHE). El servicio ahora
recibe la foto SIN el contraste ya ajustado para el modelo de visión
(`_prep_for_position` en `backend/app/ocr.py`) — decide su propio
contraste, con un objetivo distinto al de un LLM.

**Velocidad**: se pidió evaluar con Fable cómo acelerar sin arriesgar
precisión. Diagnóstico (confirmado por Fable): `pytesseract.image_to_data`
shellea a un binario externo — la espera libera el GIL, así que
`ThreadPoolExecutor` es la herramienta correcta. Camino rápido (7/9
boletas, la escala 1.0 sin reescalar ya empareja el 100%): SIN CAMBIOS,
sigue siendo 1 sola llamada a Tesseract. Camino lento (no alcanzó el 100%
con la escala barata): antes corría hasta 8 llamadas secuenciales
(escalas estándar + escalas CLAHE); ahora, confirmado que hace falta el
camino lento, se lanzan en paralelo el resto de escalas estándar + todas
las de CLAHE — el tiempo de reloj queda acotado por la más lenta, no por
la suma. Mismo resultado exacto (100%, 70/70) — `montana_bellavista` bajó
de 2.53s a 0.94s. También `--oem 1` explícito (LSTM-only, ya era lo que
se usaba en la práctica).

**Recorte en dos etapas + volver atrás**: el usuario notó una boleta
chueca y pidió una grilla fija para poder mover/recortar/girar hasta que
el texto quede derecho — y después pidió específicamente que el flujo
fuera: primero recortar (la selección "acerca" la imagen, pasa a llenar
el cuadro), después girar sobre el resultado ya recortado, con un botón
para deshacer y volver a recortar desde cero. `cropStage` ("select" |
"straighten") separa el paso de recorte (recuadro arrastrable + zoom/pan)
del paso de enderezar (90°/fino + grilla, sin recuadro). "Volver a
recortar" restaura la foto ORIGINAL (guardada aparte en
`cropOriginalFile`) para empezar de cero. Zoom/pan de la foto implementado
con Pointer Events unificados (no touch+mouse por separado, para no
chocar con los eventos del recuadro de recorte) — el recorte final
invierte la transformación de zoom/pan (`containerPctToNatural`) para
recortar exactamente lo que el usuario ve en pantalla.

**Mensaje de WhatsApp con detalle completo**: pasó de un resumen de una
línea por persona a: ítems con su costo y quién los consumió, quién pagó,
y el balance de cada uno. Reutiliza `itemCostFor`/`unitsFor` (las mismas
funciones que arman el desglose en pantalla) para que el texto compartido
sea exactamente consistente con lo que el usuario ya revisó.

pytest: mismas 3 fallas preexistentes. Build de frontend verificado.
Commiteado (`e932b0c`, `420f763`), pusheado, deployado. Verificado en
producción vía captura + consulta directa a la API (`fetch` con el token
de sesión desde la consola del navegador) — no solo visual.

### Iteración 2026-09-14 (cont. 19) — detectar alucinaciones del modelo de visión + bandas en bloques separados

Dos pedidos del usuario con captura real, ambos evaluados con Opus antes
de implementar (a pedido explícito: "analiza con tu modelo más avanzado").

**Alucinaciones del modelo de visión**: en una boleta real, el modelo
inventó 2 de 6 productos completos ("Mojito Ginger $5.000" y "Plátano
Green $13.300" en vez de "Nordic Ginger $1.500" y "Plateada Greda
$15.800") — no un typo, un producto que no existe. La suma dio EXACTA
igual ($25.500) porque los errores se cancelaron, así que el chequeo de
reconciliación (reintenta si la suma no cuadra >6%) nunca se disparó.

Diagnóstico de Opus: la suma es un chequeo de AGREGADO — la alucinación
es un fenómeno POR FILA que puede cancelarse en el total, estructuralmente
indetectable por ese chequeo solo. Plan: usar el texto que Tesseract YA
lee de la foto (para posicionar las bandas — antes se descartaba después
de usarlo) como verificador por ítem, sin gastar tokens de LLM extra.

`_suspect_items` (backend/app/ocr.py) cruza cada nombre+precio del LLM
contra ese texto real: cobertura del nombre (mejor bloque contiguo dentro
de alguna línea real) + ¿aparece el precio en el texto? (con tolerancia
5%, mínimo 2 — Tesseract TAMBIÉN se equivoca en dígitos sueltos en fotos
difíciles, sin tolerancia eso daba un falso positivo real en pruebas: "1.500"
leído como "1509"). Sospechoso solo si fallan las DOS señales. Señal
RELATIVA: solo se confía si Tesseract leyó bien la mayoría de los OTROS
ítems de esta foto — si Tesseract fracasó, no se acusa a nadie.

Verificado contra las 9 boletas del eval set (70 ítems, nombres reales):
1 falso positivo (la línea de esa MISMA foto difícil que originó todo,
donde ni el propio Tesseract logra leer el número). Contra el caso real:
detecta los 2 ítems alucinados exactos, cero falsos positivos en los
otros 4.

El reintento con el modelo más caro ahora también se dispara con ≥2
sospechosos (o ≥1/3) además del descuadre de suma, y ya NO se acepta a
ciegas (bug latente corregido: antes `parsed = parsed2` sin comparar) —
gana el candidato con menos sospechosos. `needs_review` viaja hasta la UI
(borde ámbar + aviso), nunca bloquea nada.

**Bandas en bloques separados**: el usuario notó que la banda seguía
siendo un solo recuadro que se estira desde el nombre hasta el precio,
sombreando también el hueco en blanco de por medio — pidió explícitamente
bloques separados (nombre+cantidad por un lado, precio por otro).

Medido en una boleta real: el hueco normal entre palabras de un mismo
bloque es ~3-5% del ancho de la foto; el hueco entre columna de nombre y
de precio es ~40% — margen de sobra para un umbral fijo. `_row_segments`
(reemplaza `_expand_to_full_row` en `services/ocr_position`) ahora agrupa
las palabras individuales de la fila en bloques, cortando cuando el hueco
a la siguiente palabra supera 8% del ancho. Devuelve `segments: [{x0,x1},
...]` en vez de un solo bbox — reemplaza esos campos enteros (no los
agrega aparte). Verificado con datos reales: "2× Bao Mix" da 2 segmentos
limpios ([9.1-28.6] + [68.7-83.3]), 100% (70/70) de aciertos de posición
sin cambios. El frontend dibuja un `<div>` por segmento (mismo alto/Y,
arrastre funciona igual en cualquiera de los segmentos).

pytest: mismas 3 fallas preexistentes (+1 test actualizado al nuevo
esquema). Build de frontend verificado. Commiteado (`0f98f26`,
`371ad2b`), pusheado, deployado. Verificado visualmente en producción con
zoom a la foto real.

---

## Cont. 20 (2026-09-14) — bandas desalineadas en boletas dobladas/rotadas

Dos bugs reales reportados en prod (con foto), sobre la base de "cont. 19"
(bandas en bloques separados, verificada en su momento solo con boletas
PLANAS):

1. **Segmentos compartiendo una sola altura por ítem.** `_row_segments`
   devolvía `segments: [{x0,x1}]` pero el `bbox_y0/y1` que usaba el
   frontend para TODOS los tramos de un ítem salía solo de la línea base
   (el nombre) — nunca se extendía aunque se hubieran sumado palabras de
   otra línea Tesseract con otra altura. En una boleta doblada/arrugada
   (foto real del usuario: "Limonada" debía resaltarse en $2.500 pero la
   banda del precio quedó desalineada), el tramo del precio se dibujaba en
   la altura del nombre, no la suya. Fix: `_cluster_word_spans` ahora
   agrupa por línea de origen y cada segmento sale con su PROPIO `y0/y1`
   (`Segment` gana esos dos campos); el frontend usa el alto de cada
   segmento, no uno compartido por ítem. `segments` pasó de `[x0,x1]` a
   `[x0,x1,y0,y1]` en toda la cadena (`ocr_position` → `_populate_positions`
   → `ParsedItem`/`BillItem.segments` JSON → frontend). Formato viejo
   (2 elementos, boletas leídas antes de este cambio) cae a `bbox_y0/y1`
   del ítem, como antes — sin romper nada guardado.

2. **Foto sacada en ángulo** (el cuadro ENTERO inclinado, no solo el
   papel doblado — otra foto real del usuario, boleta "PRECUENTA" clara-
   mente torcida). Confirmado con rotaciones sintéticas sobre boletas del
   eval set: a partir de ~6° el precio dejaba de resaltarse (el heurístico
   de "misma fila" de `_row_segments`, por traslape vertical, deja de
   agrupar nombre+precio porque Tesseract mismo agrupa mal las líneas bajo
   rotación); a partir de ~10°, ítems mal emparejados o sin posición.

   Plan diseñado con Fable (agente async, prompt con el código completo +
   ambos bugs + restricción explícita del usuario de no usar reglas
   ad-hoc). Descartó "row-grouping robusto a rotación" (parchar
   `_row_segments` para tolerar Y variable) porque opera sobre datos que
   Tesseract YA corrompió — nada downstream puede reconstruir texto mal
   agrupado. Recomendó: detectar el ángulo y ENDEREZAR la foto antes de
   correr Tesseract, dejando el resto del pipeline (ya validado al 100%
   en boletas rectas) intacto.

   Implementado por Sonnet:
   - `_detect_skew_angle`: híbrido Hough (Canny + HoughLinesP, mediana de
     líneas cerca de la horizontal — robusto a outliers como bordes de
     mesa) para una estimación GRUESA, refinada por projection-profile
     (rotar una versión binarizada a varios ángulos candidatos, maximizar
     varianza de la suma de texto por fila) en una ventana angosta
     (±2.5°) alrededor de esa estimación. Se probó projection-profile
     SOLO (búsqueda completa ±25°) primero — falló en vivo: a partir de
     ~8° queda dominado por la SILUETA del recibo (no el texto) y
     converge a ángulos completamente errados; combinado con Hough como
     ancla gruesa, error <0.2° en 8/9 boletas del eval set a 5-12° de
     rotación sintética (la 9na, la más densa/difícil del set, baja a
     ~1-3° de error — igual mejora sustancialmente el resultado final).
   - `_rotate_expand`: rota sin recortar (equivalente a
     `Image.rotate(expand=True)`) vía cv2, quedándose con la matriz afín
     exacta.
   - `_unrotate_lines`: aplica la transformación afín INVERSA a las 4
     esquinas de cada caja/palabra que devolvió Tesseract sobre la foto
     enderezada, y toma el rectángulo alineado a los ejes que las
     contiene — de vuelta a % de la foto ORIGINAL (la que ve el usuario,
     sin rotar). Sale un poco más ancho que el texto real (inevitable con
     un rectángulo sin rotación sobre texto en ángulo) — mismo trade-off
     que ya aceptaba el resto del sistema.
   - Solo se prueba en el camino difícil de `_match_best_effort` (escala
     barata no alcanzó el 100%), nunca en el rápido (no cambia el costo
     del caso común); compite en el mismo torneo `_best_of` que
     escala/CLAHE — gana solo si empareja estrictamente más ítems, nunca
     se asume que enderezar ayuda. Umbral `_SKEW_MIN_DEGREES = 3.0`.

   Verificado: 69/70 en el set recto (igual que antes, sin regresión) +
   100% en 3 fotos rotadas sintéticas (danes_vitacura @10°,
   lider_quilicura @8°, bar_autoctono @10° — esta última incluso mejoró de
   16/17 a 17/17) agregadas como fixture de regresión permanente en
   `backend/tests/eval/receipts_synthetic_rotated/` +
   `services/ocr_position/tests/eval_position.py` (eval liviano nuevo,
   reusa nombres de ítem del eval de visión existente, mismo criterio de
   score — `matched/total` — que ya usa el código internamente).

pytest backend: 450 passed (3 fallas preexistentes sin relación,
confirmadas también en `git stash`). Build frontend verificado. Commiteado
(`39cad62`), pusheado, deployado (`vercel --prod --yes`), verificado en
producción (health check + navegador, página `/split` carga limpia sin
errores de consola).

---

## Cont. 21 (2026-09-14) — /loop de optimización de "Dividir cuenta" (arranque)

Usuario pidió vía `/loop` (autónomo, sin intervalo fijo) un loop de mejora sistemática:
probar con boletas reales, arreglar lo que falle, hacerlo más rápido, planeado por Fable
y ejecutado por modelos baratos, hasta "optimizar división de cuenta". Plan completo de
Fable: métrica compuesta (posición gratis + contenido real + latencia), árbol de triage
por categoría de falla (bug real / límite de imagen / mejor prep de foto), condición de
parada (2 corridas sin regresión, ≥90% contenido, ≥20% menos latencia p95), presupuesto
(qué cambios pagan LLM y cuáles no).

**Fase 0 ejecutada** (encontrado real al leer el código, no supuesto: `run_eval.py`
NUNCA había evaluado `vision_parse_bill` — solo el pipeline general `parse_receipt`):
- `--pipeline {tx,bill}` en `run_eval.py`, con `score_one_bill` (items_lines_bill por
  línea + needs_review_fn/fp) — más relevante para split que el total agregado.
- Instrumentación de tiempo por paso (`_read`/`_reformat`/`_populate_positions`) en
  `ocr.py`, para medir cuellos de botella reales antes de tocar velocidad.
- **Baseline real (primera vez que existe un número)**: ~86% overall, items_lines_bill
  90%, needs_review sin falsos positivos en 2 corridas. `danes_vitacura` inestable
  entre corridas (distintos ítems mal cada vez — confirma que es un límite de calidad
  de imagen genuino, no un bug determinístico).
- Bug propio encontrado y arreglado en el scorer nuevo: `dondewilly_vinadelmar` tiene
  propina sugerida incluida en el total impreso, pero en split la propina se agrega
  aparte en la UI — el chequeo de `amount` ahora respeta `items_lenient` para eso,
  sin apagar el chequeo por línea (`items_lines_bill`), que es el que realmente
  importa para dividir cuenta.

**Bug real encontrado en paralelo** (reportado por el usuario con foto real, mismo día):
banda de color más alta que el texto, se superponía con la del ítem vecino. Causa
doble: `_row_segments` en `services/ocr_position` a veces agrupa de más en boletas con
líneas muy juntas (bbox sale más alto que la fila real — verificado, gaps negativos de
hasta -2.1 entre filas), y el frontend además inflaba esa altura ×1.3 sin límite. Fix:
se quita la inflación, y se agrega un clamp general — la altura de cualquier tramo
nunca cruza el punto medio hacia el ítem vecino más cercano por posición real en la
foto, sea cual sea la causa del bbox. **Validado con 53 boletas reales de CORD-v2**
(dataset público, no chileno — pero geométricamente representativo, gratis, sin LLM):
17% mostraban el mismo patrón de bbox invadiendo la fila vecina. No era un caso
aislado — confirma que el fix ataca algo real y común, no solo la foto reportada.

**Sobre datasets de boletas reales** (pregunta del usuario): no existe dataset público
de boletas CHILENAS. SROIE (ICDAR 2019, 1000 boletas) y CORD-v2 (Naver, 1000 boletas)
son los datasets públicos de boletas más usados, pero ambos son de Indonesia/Malasia —
sirven gratis para estresar la parte GEOMÉTRICA (posición, rotación, alineación —
como se hizo arriba), pero no para validar lectura de montos en pesos chilenos o
formato IVA 19% (para eso solo sirve seguir sumando fotos reales chilenas, como ya se
viene haciendo). Muestra CORD-v2 descargada a `/private/tmp/.../scratchpad/cord/`
(temporal, no versionada — es del dataset externo, no del proyecto).

Pendiente (loop continúa): recorrer el árbol de triage de Fable sobre `cuenta_valeria`
y `ponzano_madrid` (fallaron en la 2da corrida pero no en la 1ra — más evidencia de
inestabilidad, no determinismo perfecto del modelo a temperature=0), y explorar las
palancas de velocidad (tamaño de imagen a visión, keep-alive HTTP hacia
`services/ocr_position`) con los tiempos ya instrumentados.

**Addendum mismo día — boletas reales del usuario (`/Users/kako2/Downloads/Boletas`)**:
26 fotos reales chilenas (varias son fotos más limpias de boletas ya en el eval set —
útiles sin re-etiquetar; otras genuinamente nuevas). Hallazgos:

- **Metodológico, importante**: las pruebas locales de esta sesión corrían con
  `OCR_POSITION_URL` sin configurar (`.env` local no lo trae) — el chequeo de
  alucinaciones nunca se activaba. Se levantó el servicio localmente
  (`uvicorn app.main:app --port 8811`) para probar el pipeline COMPLETO como en
  producción. Con eso confirmado: la foto de "Plateada" (2 fotos distintas de la
  misma boleta física) da ~33% de confianza de Tesseract en ambas — el límite de
  calidad de imagen documentado antes es real y reproducible, no una casualidad de
  una sola foto.
- 3 boletas nuevas simples (Sushi Plop, Fabimfood, Sand.Cheese/Pollo Jr) funcionaron
  perfecto de punta a punta (montos, ítems, posición).
- Boleta de 22+ ítems con nombres repetidos (Stolichnaya/Michelada/Schop x3-4 veces):
  **89 segundos** con reintento — confirma que velocidad es un problema real en
  boletas grandes, no solo percepción. Position matching solo emparejó ~6 de 28 tras
  el reintento (forward-search se queda corto con tantos repetidos).
- Implementada la palanca de velocidad de menor riesgo del plan de Fable: cliente
  HTTP reusado (keep-alive) hacia `services/ocr_position` en vez de abrir conexión
  nueva por boleta (`_populate_positions`). Sin cambio de comportamiento, validado.

Pendiente: instrumentar mejor el desglose de tiempo en boletas grandes (la captura
de esta corrida se perdió por un pipe propio), y decidir si vale la pena repensar el
reintento completo (2 llamadas más) quando la boleta ya es grande de por sí.

**Addendum — causa real de la lentitud reportada, encontrada (2026-09-14)**:
el usuario insistió en que ChatGPT lee cualquiera de estas boletas en <10s, y
pidió averiguar en serio dónde se iba el tiempo. Se midió con `time.time()`
alrededor de cada llamada real (no se adivinó):

- `detail: "high"` vs `"low"` en la imagen: sin diferencia real (6.2s vs 7.0s
  en una boleta simple) — descartado como causa.
- Largo del prompt del sistema: 169 caracteres — descartado, es trivial.
- **La causa real**: `vision_text` (usada por `vision_parse_bill`) no fijaba
  `reasoning_effort` — el modelo (`gpt-5.6-luna`, familia gpt-5 con
  razonamiento) gastaba miles de tokens de "pensamiento" interno invisible
  antes de responder. Medido en la boleta real más lenta del día (22+ ítems):
  `completion_tokens=2279` para una respuesta visible de ~260 tokens — el 90%
  del tiempo era pensamiento que nadie ve.
- `reasoning_effort="low"`: pipeline completo de esa misma boleta bajó de
  **74.1s a 18.0s (-75%)** — y esta vez ni siquiera disparó el reintento (la
  lectura salió más consistente). Se probó también `"none"` (4.8s, -79%) pero
  cambia de comportamiento: en vez de admitir "[Ilegible]" cuando no puede
  leer algo, empieza a INVENTAR con confianza (un ítem pasó de "ilegible" a
  un nombre y precio inventados) — inaceptable para montos de dinero,
  descartado pese a ser más rápido. `"low"` es el punto donde eso no pasó.
- Validado con eval real (9 boletas): 87.6% (igual o mejor que el baseline
  85.9% sin fijar el parámetro).

Implementado en `OpenAIProvider.vision_text` (`backend/app/ai/provider.py`),
condicionado a modelos `gpt-5*` — no toca `vision_json` (pipeline general
`/upload`, hoy en `gpt-4o`, no aplica). Commit `1fbfe0e`.

---

## Cont. 22 (2026-09-14) — evaluación formal: cajas de color + velocidad (plan de Fable, cierre)

Usuario pidió un plan de EVALUACIÓN formal (no más parches ad-hoc) para cajas de
color + velocidad, planeado por Fable y ejecutado por Sonnet, usando las 26
boletas reales de `/Users/kako2/Downloads/Boletas/` como banco de pruebas. Plan
completo en `docs/PLAN_eval_split_v1.md`.

**Cajas de color — resultado: 0 traslapes, en TODO lo probado.**
`services/ocr_position/tests/eval_position.py` ganó un chequeo 100% geométrico
(`check_geometry_quality`: overlaps, height_outliers, order_violations — sin
ojo humano) calibrado con datos reales del set oficial (K=1.95, medido — no
adivinado — corriendo `--calibrate` sobre las 9 fixtures ya validadas; máximo
ratio real observado ahí: 1.63). Con eso:
- Categoría A (duplicados de fixtures existentes, ground-truth reusado sin
  gastar LLM): 5 fotos, incluida `images-17` — la foto EXACTA del reporte
  original de bandas superpuestas ("CREAD"/Bar La Providencia) — **13/13,
  0 overlaps**.
- Categorías B/C/D-prioritaria (9 fotos nuevas, fixtures de solo-nombre
  generadas barato con una sola pasada sin reintento): **0 overlaps** en las
  9. Dos con match rate bajo (`images-16` combo con ítems repetidos,
  `images-23` bar con 24 ítems muy repetidos, `1.webp` supermercado de 24
  ítems con códigos abreviados) — NO es el bug de bandas, es la limitación ya
  conocida del matching forward-only con nombres muy repetidos/abreviados;
  documentado como límite conocido, no perseguido (no hay overlaps ni riesgo
  visual, solo algunos ítems sin banda que caen al reparto parejo).
- Total: **71 fotos reales distintas probadas hoy sin un solo traslape** (9
  fixtures oficiales + 53 de CORD-v2 + 9 de la carpeta del usuario).

**Velocidad — retry ya no es un problema, confirmado con evidencia.**
Corrida de no-regresión (`run_eval.py --pipeline bill --dump`, post
`reasoning_effort="low"`): **90.8%** de precisión (mejor que el baseline de
87.6% de la corrida anterior) y **0 reintentos disparados** en las 9 fotos —
antes de hoy, 2/9 disparaban reintento en corridas comparables. El fix de
velocidad (cont. 21) resultó tener un efecto secundario bueno: lecturas más
consistentes → menos descuadre de suma → menos reintentos → más rápido
todavía de lo que ya medía la boleta suelta de 22 ítems. Con 0 reintentos en
esta corrida no hay datos para ajustar el umbral (`rel > 0.06`) — se
descarta tocarlo sin evidencia, tal como pedía el plan de Fable ("si no hay
hueco en los datos, no cambiar el umbral").

`reasoning_effort` en el paso de reformateo (`gpt-4.1-mini`): confirmado por
lectura de código que NO aplica — no es un modelo de la familia con
razonamiento, `chat_completion()` no tiene ningún gate de ese parámetro.
Cerrado sin tocar código, como preveía el plan.

**Criterio de parada de la evaluación (§5 de PLAN_eval_split_v1.md): cumplido.**
Cajas de color con `overlaps==0` en todo lo probado, sin patrón sistemático
nuevo. Velocidad sostenida fuera de la única boleta de prueba original,
umbral de retry evaluado con evidencia (no cambiar). Ninguna corrección bajó
la precisión del eval oficial (90.8% ≥ 87.6%) ni reintrodujo overlaps.

Instrumentación agregada de paso (`[ocr][retry] candidato2 GANO/perdio`,
`backend/app/ocr.py`) para que la próxima vez que el reintento sí dispare,
quede loggeado sin tener que instrumentar de nuevo.

---

## Cont. 23 (2026-09-14) — bug crítico en prod: reasoning_effort rompía el split entero

El usuario reportó "se demoró 5 segundos pero no hay lista de ítems ni cajas de
color" justo después del deploy de la evaluación (cont. 22). Revisado con logs
reales de Vercel (`vercel logs`): **cada subida de boleta fallaba**, no solo
algunas.

```
[ai.provider] openai vision_text failed: Completions.create() got an
unexpected keyword argument 'reasoning_effort'
[ocr] vision_parse_bill: respuesta vacía/no parseable
```

**Causa**: el fix de velocidad de la mañana (cont. 21, `reasoning_effort="low"`)
pasó TODAS las pruebas en local porque el entorno local tenía `openai==2.38.0`
instalado globalmente — pero `backend/requirements.txt` pineaba `openai==1.51.0`,
una versión del SDK que ni siquiera acepta ese parámetro (`TypeError`). El
`try/except` de `_read()` capturaba el error en silencio → `vision_parse_bill`
devolvía `None` → el usuario veía "nada" sin ningún aviso de error. Un bug
crítico (el split quedaba completamente roto), no solo una regresión de
velocidad — encontrado y arreglado en ~15 minutos desde el reporte.

**Fix en dos partes**:
1. **Inmediato (defensivo)**: `vision_text` reintenta SIN `reasoning_effort` si
   el SDK instalado no lo soporta (`TypeError` específico, no un catch-all) —
   funciona en cualquier versión del paquete, nunca vuelve a romper por esto
   aunque alguien pinee una versión vieja de nuevo (commit `51b5d64`).
2. **Raíz**: `requirements.txt` actualizado a `openai==1.99.0` — versión mínima
   confirmada PROBANDO en vivo (no adivinada): se instalaron 1.99.0, 1.97.0,
   1.93.0, 1.90.0, 1.80.0, 1.70.0 en un venv aislado y se inspeccionó la firma
   real de `Completions.create` — todas desde 1.70.0 soportan el parámetro.
   Se eligió 1.99.0 (última de la serie 1.x, sin saltar a un major version 2.x
   que arriesgaría romper otras llamadas ya funcionando). Validado con
   requirements.txt completo instalado en un venv aislado: 450/450 tests +
   prueba real de las 3 superficies de API del proyecto (`vision_text`,
   `chat_completion`, `vision_json`) — commit `6685fd2`.

**Lección para la próxima vez que se toque una versión de librería vía API
nueva de un proveedor**: el entorno local puede tener una versión distinta a
la pineada en `requirements.txt` — verificar SIEMPRE la versión realmente
pineada (no solo lo que hay instalado localmente) antes de dar por buena una
prueba local de un parámetro de API nuevo.

---

## Cont. 24 (2026-09-14) — sacar el reintento completo (ejecución del plan de Fable, por fin)

El plan de Fable de la sección anterior (evaluación arquitectónica del pipeline)
quedó recomendado pero SIN ejecutar mientras se atendían otros pedidos del
usuario (participantes, saldo combinado, limpieza de storage) — el usuario
volvió a probar una boleta grande, siguió tardando 30-70s+, y con razón
reclamó que se estaba parchando en vez de resolver de fondo.

Log real de producción (bill 147) confirmó exactamente lo que Fable ya había
encontrado: reintento disparado por "descuadre=8.0%", dio el mismo resultado
que el original — la 3ra vez que pasaba lo mismo con la misma clase de
boleta en el mismo día.

**Ejecutado**: se sacó el bloque de reintento completo de `vision_parse_bill`
(`backend/app/ocr.py`) — `_evaluate` volvió a su forma simple (arma ítems,
pide posición, marca `needs_review`), sin `ocr_lines`/`rel`/`n_suspect` que
solo existían para sostener la comparación del reintento. `needs_review`
(gratis, por ítem) queda como única red de seguridad, tal como recomendaba
Fable.

**También se probó y se revirtió** una segunda palanca del mismo plan
(acotar el escalado de CLAHE en `services/ocr_position` cuando los ítems sin
emparejar son nombres repetidos entre sí): mejoró el caso de 23 ítems que la
motivó, pero rompió `bar_autoctono` rotado (17/17 → 8/17) porque el cap
cortaba ANTES de darle una oportunidad al enderezado (deskew), un problema
geométrico distinto y no relacionado a la repetición de nombres. Se revirtió
por completo al confirmar la regresión con el eval — no se envía nada sin
validar, aunque cueste una ganancia de velocidad extra.

**Validación** (3 corridas del eval real, no 1 — por la varianza de ±6 puntos
que el propio Fable ya había medido): 87.6% / 83.6% / 86.9%, media 86.0%
(vs ~87.7% del baseline con reintento — diferencia de 1.7 puntos, dentro del
ruido). Las 27 corridas de boleta (9 fotos × 3) dieron **0 reintentos y
ninguna pasó de 14.5s** — antes, el peor caso llegaba a 78-89s. Desplegado y
verificado (health check).

---

## Cont. 25 (2026-09-14) — self-consistency real (investigación con Fable + WebSearch)

Usuario, muy enojado, exigió investigar qué usan sistemas serios (no más
reglas inventadas) y planear con Fable con acceso real a fuentes externas.
Plan completo con citas reales: paper de self-consistency (Wang et al. 2022,
arxiv.org/abs/2203.11171), benchmark público de AWS Textract Expense Analysis
(confidence score por campo) y Google Document AI (87% línea-a-línea en su
propio benchmark — confirma que ningún sistema serio promete 100%).

**Veredicto de Fable sobre reconstruir vs. arreglar puntual**: la evidencia
(7/9 boletas al 100% en corridas repetidas, fallos concentrados en 2 boletas
específicas, Tesseract —código de terceros— fallando en el MISMO número que
la IA) apoya arreglar puntual, no reconstruir. Reconstruir arriesgaba romper
los 7 casos que ya funcionan sin evidencia de que arreglaría los 2 que fallan.

**Checkpoint 1 (diagnóstico obligatorio, ANTES de escribir código)**: 5
lecturas independientes de cada boleta que falla, para saber si votar
siquiera podía funcionar:
- `cuenta_valeria` ("Pechuga de Pollo"): **$5.700 idéntico las 5 veces**,
  nunca el real ($4.400) — error sistemático. Self-consistency NO puede
  arreglar esto (votar solo confirma el mismo error con más "confianza") —
  se decidió, con evidencia, NO intentarlo a ciegas para este caso.
- `danes_vitacura` ("Nordic Ginger"): 5 valores DISTINTOS en 5 corridas — el
  correcto apareció 1 vez, nunca alcanza mayoría con pocas muestras.

**Implementado** (`_self_consistency_recheck`, `backend/app/ocr.py`): NO es
un corrector mágico — es un detector de desacuerdo. Solo dispara si ya hay
≥1 ítem sospechoso (gratis en el camino feliz). Lanza 2 lecturas extra en
paralelo; si ≥2 de 3 coinciden (tolerancia 5%), adopta ese consenso; si
ninguna coincide, fuerza `needs_review=True` sin inventar un valor.

**Bug real encontrado y arreglado en el mismo commit**: la primera versión
solo re-chequeaba los ítems YA marcados sospechosos — pero "Nordic Ginger"
casi nunca se marca solo (el nombre calza bien contra Tesseract, solo el
precio está mal), así que quedaba afuera del re-chequeo, el caso exacto que
esto debía atajar. Corregido: dispara por boleta, re-chequea TODOS los
ítems.

**Validado** (3 corridas completas post-fix): 90.8% / 90.2% / 87.6%, media
**89.5%** — mejor que el baseline sin esto (86.0%). `cuenta_valeria` sigue
sin detectarse (matemáticamente imposible de arreglar por voto, ya
explicado) — límite conocido, documentado, no perseguido más allá.

**Pendiente** (de la investigación de Fable, no bloqueante): ninguna de las
9 fixtures oficiales tiene un descuento real (`line_total` negativo) en el
ground-truth — el mecanismo de descuento/propina de cont. 23 solo está
validado con aritmética sintética, no contra una foto real de punta a
punta. Conseguir 1 boleta real con descuento (ya se usó
`/Users/kako2/Downloads/Boletas/` antes) y agregarla como 10ma fixture.

---

## Cont. 26 (2026-09-14) — corrección: "cuenta_valeria" nunca fue un error del modelo

El usuario cuestionó directamente: "pero pechuga de pollo dice 5700 pues, y
ensalada surtida es 4400... quizás has sido tú siempre el que se equivoca".
Tenía razón. Revisando la foto real con zoom (`images-12.jpeg`/`images-21.jpeg`
en `/Users/kako2/Downloads/Boletas/`): **Pechuga de Pollo = $5.700, Ensalada
Surtida = $4.400** — la suma (1700+1700+5700+4400+8900+2500=24.900) cuadra
exacto con el Total Final impreso.

El ground-truth en `backend/tests/eval/expected/cuenta_valeria.json` tenía
`"Pechuga de Pollo": 4400` y `"Ensalada Surtida": null` — **mal armado**, le
asigné el precio equivocado al ítem equivocado (y nunca completé el otro).
El modelo de visión leyó bien "Pechuga de Pollo = $5.700" en TODAS las
pruebas de hoy (más de 15 corridas, incluidas 2 fotos distintas de la misma
boleta real) — lo que fallaba todo el día no era el pipeline, era mi propio
archivo de referencia.

**Corregido y re-verificado**: `cuenta_valeria` da **100.0%** exacto en los 6
ítems con el ground-truth arreglado. Esto significa que el estado real del
eval set oficial es **8/9 boletas perfectas**, no 7/9 como se venía
reportando — `danes_vitacura` (SÍ verificado contra la foto real con zoom,
Nordic Ginger $1.500 y Plateada Greda $15.800 confirmados, suma exacta) sigue
siendo el único caso genuinamente difícil del set, con la foto físicamente
borrosa en ese punto.

**Lección aplicada**: antes de aceptar cualquier "boleta que falla" como un
límite real del modelo, verificar el ground-truth contra la foto (con zoom)
antes de gastar tiempo/dinero intentando arreglar el pipeline — el propio
archivo de referencia puede estar mal, como pasó acá.

---

## Cont. 27 (2026-09-14) — boleta "Consumo Mesa S4" (bill_id=150): 3 causas reales, 3 fixes

Boleta real de 22 ítems (muchos repetidos: Iced Latte×2, +Extra Vainilla×2,
+Leche Descremada×2-3, varias líneas $0 de modificador), 30+s y bandas de
color superpuestas. Se bajó la foto real de Vercel Blob (`vercel blob list`
+ descarga) y se corrió `services/ocr_position` local contra ella con los
ítems exactos — nada de "se ve mejor", medido contra la foto real.

**Causa 1 — bandas superpuestas (la queja concreta)**: la foto tiene una
sombra física real (mano/celular) que tapa el tercio medio del papel, desde
"Omelette" hasta la 2da mitad. Tesseract, a la escala/variante que ganó, solo
matcheó 8 de 22 ítems (todos arriba de la sombra, agrupados en 35-48% de
alto de foto, más 2 sueltos en 63-66%). El fallback del frontend para
ítems SIN match (`defaultBandPct`) repartía por índice sobre el 100% de la
foto COMPLETA, ignorando dónde habían caído las anclas reales — con 8
anclas apretadas en 35-48%, el ítem 6 sin match caía en 30.4% por
índice: literal encima del cluster real. Confirmado con los datos reales
de esta boleta (no supuesto). **Fix geométrico, no por caso**: interpola
entre las DOS anclas reales (con `position_y`) más cercanas al índice del
ítem sin match; sin ancla a un lado, extrapola desde la única que hay hacia
ese borde; sin ninguna ancla en toda la boleta, cae al reparto uniforme de
siempre. Con el ítem 6 de esta boleta: antes 30.4% (superpuesto), ahora
55.3% (a mitad de camino entre sus dos anclas reales, 46.9% y 63.8%) — sin
overlap. `frontend/src/app/split/page.tsx`: `defaultBandPct`/`bandPctFor`.

**Causa 2 — cold start del contenedor, nunca contabilizado antes**: los
logs reales de esta request (`vercel logs`) mostraron la secuencia de
arranque completa del contenedor `ocr_position` (`Started server
process... Waiting for application startup... Application startup
complete.`) arrancando recién cuando el backend llegó a
`_populate_positions` — es decir, DESPUÉS de los ~18s que ya habían tardado
las 2 llamadas de visión (`_read`=14.6s + `_reformat`=3.3s). El cold start
se sumaba encima, no se solapaba con nada. **Fix**: `_warm_position_service()`
nuevo en `backend/app/ocr.py`, dispara un GET `/health` al contenedor en un
hilo aparte apenas arranca `vision_parse_bill` — en paralelo con la
visión, no después. Fire-and-forget, mismo comportamiento best-effort que
ya tenía `_populate_positions` si el servicio no responde. No cambia
timeouts ni resultados, solo solapa el cold start con tiempo que ya se
gastaba de todas formas.

**Causa 3 — "subir múltiples boletas" nunca funcionó**: el código de cola
(`onQueueFiles`/`onAdvanceQueue`, de un trabajo anterior) existía pero
tenía DOS bugs que lo dejaban muerto: (a) el `<input type="file">` nunca
tuvo el atributo `multiple`, así que el picker del sistema jamás dejaba
elegir más de una foto — `files.length > 1` nunca se cumplía; (b)
`onAdvanceQueue` estaba desestructurado como prop pero nunca se llamaba en
ningún lado, así que aunque hubiera cola pendiente, nada la avanzaba al
terminar una boleta. Fix: atributo `multiple` agregado; botón "Siguiente
boleta (N restantes)" en la pantalla de resumen (step 5) cuando
`queueRemaining > 0`, más indicador "+N en cola" en el header durante todo
el flujo.

**Verificado, no inventado**: 453/456 tests backend pasan (3 fallos
preexisten en `main`, confirmado con `git stash` — son de
`test_ocr_integration.py`, la ruta de `vision_parse` para cartolas, no
`vision_parse_bill`; no se tocan en este cambio). `npm run build` limpio.
La interpolación de bandas se verificó a mano contra las 8 anclas reales de
esta boleta (arriba). No se tocó `services/ocr_position` ni el matching —
ambos fixes son fuera del pipeline de OCR (frontend + paralelismo del
warm-up), riesgo de regresión bajo.

**Pendiente de verificar en producción tras deploy**: re-subir esta misma
boleta (o una similar) y confirmar en `vercel logs` que el cold start del
contenedor ya no aparece después de `_populate_positions`, y a ojo que las
bandas ya no se superponen.

---

## Cont. 28 (2026-09-14) — la causa REAL de los 30s: self-consistency, no el cold start

Usuario subió otra boleta después del deploy de cont. 27 y siguió tardando
30+s. Tenía razón en seguir furioso: el cold-start-warmup de cont. 27 era
real pero secundario — la causa dominante era otra cosa que yo mismo agregué
esta sesión y no había medido con el self-consistency YA activo en el
camino caliente.

**Causa real, con aritmética**: `_self_consistency_recheck` (agregada en
cont. 25) dispara 2 lecturas de visión EXTRA — una boleta entera de nuevo,
en paralelo entre sí pero DESPUÉS de que la primera lectura ya terminó —
cada vez que CUALQUIER ítem queda `needs_review=True`. En boletas largas
con nombres repetidos eso se dispara seguido. Aritmética real: 1ra pasada
~18s + 2da tanda ~18-20s = 30-38s. Coincide exacto con la queja.

**Decisión**: se saca por completo (`_self_consistency_recheck` y
`_SELF_CONSISTENCY_SAMPLES` eliminados de `backend/app/ocr.py`, sin dejar
código muerto) — mismo criterio que ya se aplicó al reintento viejo en
cont. 24: costo alto (ronda completa de visión extra) por beneficio real
bajo (en el camino feliz solo prende/apaga una bandera de revisión; rara
vez corrige el valor mostrado).

**Verificado, no prometido**:
- Llamada real contra la FOTO REAL de bill_id=150 (misma de cont. 27,
  22-23 ítems): **13.72s total** (`_read`=8.50s + `_reformat`=3.57s +
  posición), los 23 ítems calzan exactos contra la foto (revisados uno
  por uno). Antes de este fix esa misma boleta venía dando 30+s en prod.
- Eval completo (9 boletas oficiales, sin self-consistency): **8/9 al
  100%**, tiempos 4.5s-11.5s (todas bajo 15s). La única que baja es
  `danes_vitacura` (29.4%) — la boleta con foto físicamente borrosa, ya
  documentada desde antes como el único caso genuinamente difícil del set
  (Nordic Ginger/Plateada Greda) — self-consistency existía justo para
  tapar ese caso puntual. Trade-off real y consciente: se pierde esa red
  de seguridad en ESE caso conocido a cambio de sacar el mayor freno de
  velocidad de TODAS las boletas.
- pytest: 453/456 (mismos 3 fallos preexistentes de `vision_parse`,
  ajenos a este cambio, confirmado antes en cont. 27).

**Pendiente de confirmar en producción**: que la próxima subida real del
usuario efectivamente baje de 15s.

---

## Cont. 29 (2026-09-15) — panel de revisión de Fable + intento fail-safe revertido con datos reales

Tras el plan de Fable (cont. 28: recomendación "no migrar", investigó Donut/
LayoutLMv3/markitdown/Textract/Azure/Google/PaddleOCR/ensembles), se corrió
un panel de 3 agentes revisores (rigor de investigación, adversarial,
viabilidad de ingeniería) pedido explícitamente por el usuario antes de
ejecutar nada. Hallazgos que corrigieron el plan original:

- **n=9 es más débil de lo que parecía**: una de las 9 boletas es de Madrid
  (no chilena); el propio historial del proyecto muestra que ese mismo set
  se usó para AJUSTAR el pipeline día a día (no es holdout real); varianza
  documentada entre corridas del mismo código: ±3-4 puntos porcentuales.
- **Hueco real de investigación**: Fable nunca buscó proveedores
  especializados en recibos (no document-AI genérico) — el panel adversarial
  encontró Taggun, que anuncia soporte literal de RUT/boleta chilena.
  Pendiente de probar con datos propios (requiere que el usuario cree la
  cuenta/trial).
- **Google Document AI Expense Parser SÍ soporta español** (`es` confirmado
  en `docs.cloud.google.com/document-ai/docs/processors-list`, verificado
  directo por mí tras una contradicción entre 2 de los 3 paneles) — Fable lo
  había descartado como "dudoso" sin verificar la fuente primaria.
  Queda como candidato a probar con datos propios, igual que Azure.
  Pendiente de que el usuario cree la cuenta.
- **`danes_vitacura` no es un "bug puntual"**: el trust-gate de
  `_suspect_items` se autodesactiva (no acusa a nadie) justo cuando
  Tesseract no puede confiar en la foto — que es la MISMA condición (foto
  oscura/papel degradado) que hace que el modelo de visión alucine. Falla
  correlacionada por diseño, no un umbral mal puesto.
- Cita de Fable "≥800 ejemplos para superar prompting con LLM" no se pudo
  verificar independientemente — parece confundir el tamaño del training
  split de CORD con un hallazgo real. Descartada.
- El descarte de Fable del ensemble/voting paralelo (CE-OCR) confundía
  "secuencial" (lo que causó los 30s de cont. 28) con "paralelo" (que el
  propio proyecto ya sabe hacer bien, ej. `_warm_position_service`) — queda
  como opción real a evaluar más adelante, no descartada por buena razón.

**Ejecutado tras el panel**:
1. **Intento de fail-safe en `_suspect_items`** (marcar TODA la boleta
   sospechosa cuando el trust cae bajo el gate, en vez de a nadie) —
   implementado, y ANTES de aceptarlo se probó contra las 9 oficiales + 25
   fotos reales nuevas de `/Users/kako2/Downloads/Boletas/` (carpeta que el
   usuario pidió usar de ahora en adelante). Resultado real: el trust cae
   bajo 0.5 en 13/25 fotos (52%), la mayoría boletas leídas BIEN — "marcar
   todo" es demasiado ruido. Se probó también sacar el gate por completo
   (chequear siempre): eso marcaba en falso 5/6 ítems de `cuenta_valeria`,
   una boleta verificada 100% correcta (cont. 26) — Tesseract simplemente
   lee mal ESE formato aunque el modelo de visión la lea perfecto. De las 3
   variantes medidas, el diseño ORIGINAL (silencio total cuando Tesseract
   no es confiable) sigue siendo el que menos ruido genera con datos
   reales. **Revertido a como estaba** — `danes_vitacura` queda como límite
   conocido, no resuelto por esta vía. Lección aplicada una vez más: medir
   con datos reales ANTES de aceptar un cambio "lógicamente correcto".
2. **Costo real medido, no estimado**: `ai_usage.py` ya tenía toda la
   infraestructura (`price_for`, `record`, `monthly_summary`, endpoint
   `/ai/usage`) pero le faltaban los precios de `gpt-5.6-luna`/
   `gpt-4.1-mini`/`gpt-5-mini` en `_PRICES` — sin eso el costo de dividir
   boletas se calculaba como $0 falso. Agregados los precios reales
   verificados contra `developers.openai.com/api/docs/pricing`.
3. **PaddleOCR descartado** sin tocar código — riesgo real de empeorar el
   cold-start ya documentado (cont. 27/28) + conflicto de versión de numpy
   + granularidad de bbox incompatible con el clustering actual.

**Pendiente, requiere acción del usuario** (no ejecutable por mí solo):
- Crear cuenta/trial en Google Document AI y Taggun, correr las boletas
  reales contra ambos, comparar con `run_eval.py` — cierra la pregunta de
  "hay algo mejor" con datos propios en vez de blogs de marketing.
- Seguir agregando boletas reales diversas (formato/iluminación/comercio)
  como holdout real, separado de las que se usan para ajustar prompts —
  la carpeta `/Users/kako2/Downloads/Boletas/` (25 fotos) ya es un buen
  punto de partida, usarla de ahora en adelante para validar cambios de
  `ocr.py`/`ocr_position` antes de darlos por buenos.

---

## Cont. 30 (2026-09-15) — usuario reporta 30s de nuevo: medido real (~16-17s), probados 2 modelos alternativos con datos frescos, ninguno gana

Log real de producción (`vercel logs`, bill_id=160): `_read(gpt-5.6-luna)
=10.64s` + `_reformat=3.62s` + posición (~2s, contenedor ya estaba tibio
por el warm-up de cont. 27) = **~16-17s servidor**, no 30s — pero sigue
sobre el objetivo de <10-15s.

Antes de tocar nada, se probaron con las 9 boletas oficiales (datos
frescos, no confiando en comparaciones viejas):
- `gpt-4.1-mini`: 80.5% precisión (peor — `cuenta_valeria` y
  `lider_quilicura`, que hoy dan 100%, empiezan a fallar). No más rápido
  de forma consistente (`bar_autoctono` 15.0s, peor que luna).
- `gpt-5-mini`: 86.1% precisión (peor) Y más lento (`bar_autoctono` 23.9s,
  `danes_vitacura` 21.0s) — pierde en las dos dimensiones a la vez.

Ningún modelo alternativo gana. Confirma con datos de HOY lo que
`config.py` ya documentaba de una comparación anterior: `gpt-5.6-luna`
sigue siendo la mejor opción real disponible. El costo de ~5-11s de la
lectura de visión es estructural al enfoque (LLM de visión), no un bug.

**Lo que sí se hizo**: como la latencia real no se puede bajar más sin
sacrificar precisión (que el usuario exige igual de fuerte), se atacó la
espera PERCIBIDA en vez de la real — técnica de UX estándar, cero cambio
de latencia real. El spinner de "Leyendo boleta…" (mudo, 15+ segundos) pasa
a ciclar por fases reales ("Subiendo foto…" → "Leyendo boleta…" →
"Extrayendo ítems…" → "Ubicando cada ítem en la foto…") cronometradas
aproximadamente a los tiempos reales medidos. `frontend/src/app/split/
page.tsx`: `loadingPhase`/`startLoadingPhases`/`LOADING_PHASES`.

---

## Cont. 31 (2026-09-18) — sección "SPLIT" (laboratorio): streaming, no modelo mágico

Usuario insistió en que ChatGPT lee una boleta en 2-4s y acusó pruebas mal
hechas. Se probó la hipótesis real antes de descartarla: llamada directa a
`gpt-5.6-luna` con `stream=True` (nunca antes probado en este proyecto,
todo el pipeline esperaba la respuesta COMPLETA antes de mostrar nada).
Resultado real: **primer token de texto visible a los 4.5s**, boleta
completa (bar_autoctono, 17 ítems con repetidos) leída en 6.2s. El modelo
no es más rápido de lo medido antes — lo que cambia es que la app nunca
mostraba nada hasta que TODO (lectura + reformateo + posición) terminaba,
~16-17s después. ChatGPT sí hace streaming por defecto; nosotros nunca lo
habíamos implementado.

Pedido explícito del usuario: sección nueva aislada ("SPLIT", `/split-lab`)
para probar esto de cero SIN tocar `/split` (que ya funciona: participantes,
reparto, pago, liquidación). Implementado:

- `backend/app/routers/split_lab.py` — endpoint `POST /split-lab/ocr-stream`,
  SSE. Prompt DISTINTO al de producción (`_LAB_PROMPT`, formato semi-fijo
  `cantidad | nombre | valor` una línea por ítem) — se vuelve a probar
  forzar formato en el único paso, esta vez a propósito, porque es
  justamente la pregunta a responder con datos (cont. 13 había encontrado
  que esto empeoraba precisión con el prompt/parser viejo; con streaming +
  parser de líneas completas el trade-off puede ser distinto). Reutiliza
  SOLO `_prep_receipt_image` de `ocr.py` — nada más se duplica. No toca
  `Bill`/`BillItem`, no persiste nada todavía.
- `frontend/src/app/split-lab/page.tsx` — sube foto, parsea SSE a mano
  (fetch + ReadableStream, no EventSource nativo porque hace falta POST +
  header Authorization), muestra cronómetro real corriendo y cada ítem
  apenas llega. Link "⚡ SPLIT (laboratorio de velocidad)" en el dashboard.

Verificado local (sin FastAPI/DB, lógica de parseo pura contra streaming
real): 17/17 líneas parseadas sin error, primer ítem 5.07s, total 6.22s.
Falta verificar en producción real de Vercel si el runtime de Python
efectivamente hace streaming byte a byte o bufferea la respuesta completa
igual (la incógnita real de infra) — pendiente del próximo deploy+prueba.

Plan: una vez validado que streaming funciona de punta a punta en Vercel Y
que la precisión con este prompt/parser nuevo es al menos igual a la de
producción (medir contra las 9 boletas oficiales + carpeta Boletas/), se
decide si esto reemplaza el paso de lectura del pipeline actual — recién
ahí se reincorporan participantes/reparto/pago, reusando lo que ya
funciona en `bills.py`/`split/page.tsx`, no reescribiéndolo.

---

## Cont. 32 (2026-09-18) — el bug real detrás de TODA la lentitud de la sesión: `api/requirements.txt` vs `backend/requirements.txt`

Al probar el streaming de `/split-lab` en producción, salió un error visible
(`TypeError: unexpected keyword argument 'reasoning_effort'`) — el MISMO
síntoma del incidente crítico de cont. 23, que se había dado por resuelto
hace días. Investigado: **Vercel instala las dependencias de la función
serverless desde `api/requirements.txt`** (vive junto al entrypoint real,
`api/index.py`), NO desde `backend/requirements.txt`. El fix de cont. 23
(`openai==1.51.0` → `1.99.0`) se aplicó solo al segundo — el primero se
quedó pegado en `1.51.0` todo este tiempo.

Como `vision_text()` en `provider.py` ya tenía el fallback defensivo
(reintenta sin `reasoning_effort` si el SDK no lo soporta, para no volver a
romper el split como en cont. 23), el error nunca se vio: cada llamada en
producción caía al fallback EN SILENCIO. `reasoning_effort="low"` —la
optimización que en cont. 21 había medido -36% de tiempo (22.5s→14.5s)— NO
estuvo activa ni una sola vez en producción real desde que se implementó.
Todas las mediciones de "16-17s, ya no hay más que optimizar" de cont. 27,
28, 29 y 30 se hicieron con esta degradación silenciosa activa, sin que
nada en los logs lo delatara.

Se descubrió recién ahora porque el nuevo endpoint `/split-lab/ocr-stream`
llama a la API de OpenAI DIRECTO (sin pasar por `provider.py`, sin el
fallback defensivo) — el error salió a la superficie por primera vez.

**Fix**: `openai==1.51.0` → `1.99.0` en `api/requirements.txt`.

**Verificado en producción real, antes/después, con la misma boleta**:
- Antes (bill_id=160, cont. 30): `_read=10.64s`
- Después (bill_id=161, mismo día, foto `bar_autoctono`): **`_read=4.40s`**
  — 2.4× más rápido. `_reformat=2.35s`. **Total `/bills/{id}/ocr`: 11.0s**
  (antes 16-17s), ya bajo el objetivo de 15s y cerca del de 10s.

Lección aplicada, otra vez: un fix verificado localmente y "desplegado" no
está realmente activo hasta que se verifica CONTRA EL DEPLOY REAL — local
usa `backend/requirements.txt`; Vercel usa `api/requirements.txt`. Dos
archivos de dependencias para el mismo servicio es un riesgo real de
divergencia silenciosa — vale la pena evaluar unificarlos (ej. que
`api/requirements.txt` se genere a partir de `backend/requirements.txt` en
vez de mantenerse a mano por separado) en una sesión futura, sin apurarlo
ahora.

---

## Cont. 33 (2026-09-18) — usuario prueba en vivo: 40s reales, causa fue el timeout del servicio de posición

Usuario probó la app real (no el lab) tras el fix de cont. 32 y reportó
~40s. Log real (`vercel logs`, bill_id=163): `_read=11.37s` (ya con el fix
de reasoning_effort activo, razonable) + `_reformat=1.56s`, pero después:
**`servicio de posición no disponible (The read operation timed out) —
reparto parejo`** — el cliente HTTP esperó el timeout COMPLETO (15.0s) sin
que sirviera de nada, y cayó al fallback igual. ~28s de los ~40s reportados
fueron esta espera muerta, no la lectura del modelo.

El contenedor SÍ estaba tibio (el GET /health de warm-up respondió rápido,
sin secuencia de arranque) — el problema fue que el POST /position real
tardó más de 15s en esta boleta específica (el contenedor terminó
respondiendo bien, solo que tarde: log del lado del contenedor muestra 200
OK varios segundos después de que nuestro cliente ya se había rendido).

**Fix acotado**: bajar el timeout del cliente de 15.0s a 7.0s en
`_populate_positions` (`backend/app/ocr.py`). No se puede saltar el
servicio de posición por completo mientras esto se soluciona de raíz — a
diferencia de las franjas de color (apagadas), `needs_review` (el ítem con
borde ámbar cuando el modelo probablemente alucinó) SÍ sigue activo en la
UI hoy y depende del mismo `ocr_lines` que devuelve este servicio. Con 7s:
los casos normales (2-5s medidos en bills 160-162) siguen andando igual;
los casos que se cuelgan esperan la mitad de tiempo por nada, no el doble.

**Pendiente, no resuelto todavía**: por qué el servicio de posición tarda
>15s en ciertas boletas cuando el contenedor está tibio — no es cold start.
Candidato más probable: la escalada de `_match_best_effort` (varias
escalas + CLAHE) en una boleta grande/difícil. Requiere la foto real que
causó esto (bill_id=163) para diagnosticar con datos, no se investigó a
fondo todavía por el apuro de cortar la espera muerta primero.

---

## Cont. 34 (2026-09-18) — comprimir foto en "Dividir cuenta" + racing (2 llamadas en paralelo) en SPLIT

Usuario hizo una comparación real muy útil: misma foto, `/split-lab` 6s,
"Dividir cuenta" ~20s. Aisló el problema: el flujo real nunca comprimía la
foto antes de subir (SPLIT sí, desde cont. 33). **Fix**: mismo
`compressForUpload` (máx 2000px, JPEG 0.85) portado a
`frontend/src/app/split/page.tsx`, aplicado en `handleFile()` justo antes
de `ocrBill()`.

Investigado con fuentes reales, no inventado (el usuario insistió en pedir
esto explícitamente):
- [OpenAI API — Latency optimization guide](https://developers.openai.com/api/docs/guides/latency-optimization):
  confirma streaming como "the single most effective approach" (ya
  implementado) — no cubre tail latency específicamente.
- [OpenAI API — Images & Vision guide](https://developers.openai.com/api/docs/guides/images-vision):
  con `detail:"high"` (lo que usa este proyecto), la API igual redimensiona
  a ~2048px de lado para modelos de esta generación — comprimir a 2000px
  en el cliente no pierde nada que el servidor no fuera a descartar de
  todas formas. Para OCR específicamente recomiendan `detail:"original"` en
  vez de `"high"` (preserva hasta 6000px) — no se cambió, implica más
  "parches"/tokens/latencia, lo contrario de lo que se pedía hoy; queda
  anotado como opción real para mejorar precisión más adelante.
- [myhoai.com — "A simple fix for LLM tail latency"](https://engineering.myhoai.com/posts/a-simple-fix-for-llm-tail-latency/):
  el usuario mismo reportó variancia real en vivo (misma foto, subidas
  consecutivas a `/split-lab`: 0.8s-14s) — confirmado también en logs
  reales (`[split-lab][timing]`, 5 de 6 pruebas 0.8-5.4s, 1 de 6 en 14.3s).
  Coincide con "distribución de cola pesada" (normal en LLMs). La técnica
  con datos reales de esa fuente: mandar la misma solicitud 2 veces en
  paralelo y quedarse con la que responda primero — p99 de tiempo-al-
  primer-token medido ahí: 4.2s→1.2s. Costo: duplica llamadas al modelo
  (2x volumen).

**Implementado en `/split-lab` únicamente** (el laboratorio, no el flujo
real todavía — no se valida algo así en producción real sin medirlo
primero ahí): `_race_streams()` en `backend/app/routers/split_lab.py`,
dispara 2 llamadas idénticas en hilos separados, entrega tokens de la que
responda primero, descarta la otra. Verificado localmente: 3/3 corridas
dieron 17/17 ítems correctos, cada corrida con el hilo ganador alternando
(0, 0, 1) — confirma que la carrera realmente elige la más rápida, no
siempre la misma.

**Pendiente**: medir con más corridas reales si el racing efectivamente
achica la cola lenta (el efecto es sobre el PERCENTIL 99, raro por
definición — 3 corridas no alcanzan para verlo, hace falta usar la sección
varias veces más). Si se confirma que ayuda, evaluar llevarlo al flujo
real de `/bills/{id}/ocr` — ahí el costo doble sí pesa más (2 llamadas de
`_read` en vez de 1, sobre el flujo que de verdad se usa a diario).

---

## Cont. 35 (2026-09-18) — causa raíz real del timeout del servicio de posición: contención de CPU sin presupuesto de tiempo

Usuario reportó que "Dividir cuenta" seguía en ~20s pese al fix de
compresión. Logs reales (bills 167, 168): `_read`+`_reformat` ya rápidos
(6-9s), pero **"servicio de posición no disponible (timed out)"** en
AMBAS — el timeout de 7s (cont. 33) se agotaba entero sin servir de nada,
2 veces seguidas. No es un caso raro, es recurrente.

Perfilado con datos reales (no más ajustes de timeout a ciegas — el
usuario ya había señalado que eso era "parchar sin sentido"): descargué la
boleta difícil de 22 ítems ya conocida (bill150, cont. 27) y corrí
`_match_best_effort` localmente, en MI máquina de 8 núcleos. **La misma
llamada, sin cambiar una línea, dio 23.07s una vez y 1.65s la siguiente**
— 14× de diferencia. Causa: la fase de escalada (multi-escala + CLAHE +
enderezado) lanza hasta 8 procesos de Tesseract en paralelo
(`ThreadPoolExecutor(max_workers=8)`) y ESPERA A QUE TODOS TERMINEN, sin
ningún tope de tiempo interno — bajo contención de CPU (del contenedor
compartido de Vercel, o de cualquier otra carga), en vez de acercarse a
"la más lenta" (la suposición de diseño original), se acerca a la SUMA con
overhead de más. Confirmado induciendo contención real (4 llamadas
simultáneas desde el mismo proceso): sin el fix se dispara sin control;
con el fix, queda acotado en ~3.6s con la misma calidad de resultado.

**Fix real**: `_ESCALATION_BUDGET_S = 4.0` en
`services/ocr_position/app/main.py` — la fase de escalada usa
`concurrent.futures.wait(futures, timeout=4.0)` en vez de esperar cada
`.result()` sin límite; se queda con lo mejor que haya TERMINADO dentro
del presupuesto (mismo criterio de desempate de siempre, `_best_of`
aplanado en una sola pasada — verificado matemáticamente equivalente
cuando nada se corta por tiempo). Los hilos que no llegan a tiempo quedan
abandonados (`shutdown(wait=False)`) en vez de bloquear la respuesta.

**Verificado, no prometido**:
- Boleta difícil (22 ítems) localmente: 3/3 corridas 1.4-1.7s (antes
  variaba 1.65s-23s).
- Con contención inducida real (4 llamadas paralelas, ~32 procesos
  Tesseract compitiendo): 4/4 acotadas en ~3.6s, mismo resultado (8/22).
- Eval completo del servicio de posición (`tests/eval_position.py
  --geometry`): 69/70 (98.6%), 0 overlaps — **idéntico al resultado con el
  código VIEJO** (confirmado con `git stash`: el 16/17 de `bar_autoctono`
  ya pasaba antes, es variancia preexistente de Tesseract, no algo que
  rompió este cambio).
- pytest backend: 453/456 (mismos 3 fallos preexistentes de `vision_parse`,
  ajenos).

Con esto, el peor caso real de "Dividir cuenta" pasa de "hasta 7s
esperando el timeout externo, tirando todo el trabajo hecho" a "hasta ~4s,
aprovechando lo que sí llegó a tiempo" — sin sacrificar el camino rápido
(que nunca entra a esta fase) ni el caso normal (verificado sin
regresión).
