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
Pendiente: confirmación visual del usuario en su iPhone.
