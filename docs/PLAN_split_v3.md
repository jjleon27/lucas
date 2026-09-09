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
