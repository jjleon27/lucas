# SESSION STATE
*Auto-guardado por hook Stop · 2026-09-09 04:51:48*

## Presión de contexto
- Tool uses esta sesión: **227** — 🔴 MUY ALTA — posible compactación pronto

## Última petición del usuario
listo

## Último trabajo del asistente
Todas las rutas dan 200 ahora (`/split`, `/dashboard`, etc.) y la API sigue OK. El 404 al "dividir cuenta" está resuelto.

La app quedó completa en Ve

## Archivos modificados esta sesión
- `/Users/kako2/.claude/projects/-Users-kako2-Documents-lucas/memory/MEMORY.md`
- `/Users/kako2/.claude/projects/-Users-kako2-Documents-lucas/memory/ocr_eval_harness.md`
- `api/index.py`
- `api/requirements.txt`
- `backend/.env.example`
- `backend/app/ai/provider.py`
- `backend/app/config.py`
- `backend/app/database.py`
- `backend/app/ocr.py`
- `backend/app/storage.py`
- `backend/requirements.txt`
- `backend/tests/eval/README.md`
- `backend/tests/eval/expected/baobar_providencia.json`
- `backend/tests/eval/expected/barlaprovidencia.json`
- `backend/tests/eval/expected/cuenta_valeria.json`
- `backend/tests/eval/expected/danes_vitacura.json`
- `backend/tests/eval/expected/dondewilly_vinadelmar.json`
- `backend/tests/eval/expected/montana_bellavista.json`
- `backend/tests/eval/expected/ponzano_madrid.json`
- `backend/tests/eval/results/.gitignore`
- `backend/tests/eval/run_eval.py`
- `docs/PLAN_migracion_vercel.md`
- `docs/SESSION_STATE.md`
- `frontend/next.config.js`
- `package.json`
- `vercel.json`

## Cómo continuar en la próxima sesión
1. Lee `docs/MASTER_PLAN.md` §20 y §16
2. Lee `docs/ROADMAP.md` tabla "Work Order Summary"
3. Revisa los archivos modificados arriba
4. Pregunta al usuario si quiere continuar desde la última tarea

## ESTADO MANUAL (fin de sesión autónoma 2026-09-09)

TODO HECHO, commiteado y pusheado a `main`. Prod verificado en
https://frontend-mu-dusky-88.vercel.app

### Hecho esta sesión
1. **Bug del split** (usuario reportó "$2.150 → $1.765"): `_normalize_boleta_items`
   escalaba precios correctos al TOTAL NETO. Fix en `vision_parse` + prompt.
   Eval `lider_quilicura` guard de regresión. Verificado en prod: CHOCO 160 = 2150.
2. **Migración a Vercel** completa (de sesión previa): static front + Python fn +
   Neon + Blob. Ver `docs/PLAN_migracion_vercel.md`.
3. **OCR gpt-5-mini** default, eval 95.5%. Fable 5.1 listo (falta ANTHROPIC_API_KEY:
   `AI_PROVIDER=anthropic`, `ANTHROPIC_MODEL=claude-fable-5-1`).
4. **Feature Proyectos** (Opción B) — backend + frontend + `/projects` + selector
   en TransactionList. Verificado en prod. Tests `test_projects.py`.
5. Docs sincronizadas (MASTER_PLAN, ROADMAP, CURRENT_STATE, README), graphify
   actualizado (2528 nodos), memoria actualizada.
6. `compact` NameError arreglado; `_RECEIPT_TEXT_PROMPT` con reglas moneda/propina/fecha.

### Pendiente (nada bloquea, todo anotado)
- Rotar `OPENAI_API_KEY` (se pegó en el chat).
- Borrar proyecto Railway `reasonable-laughter`.
- Copiar env vars de Production → Preview en Vercel (para que los previews anden).
- `_RECEIPT_TEXT_PROMPT`: portar fix "no escalar precios" (solo `_RECEIPT_PROMPT` lo tiene).
- `_RECEIPT_PROMPT` a veces devuelve `total_neto`/`iva_amount` null (metadata; el split anda igual).
- Probar cartola PDF escaneada real (`pypdfium2` no testeado en prod).
- Proyectos: asignar desde el form de "nueva transacción" (hoy solo al editar);
  filtro por proyecto en la lista de movimientos.
- (viejo) test_ocr_normalize/_integration: 11 tests fallan hace meses (dead code del churn de junio).

### "Que siga funcionando si cierro el laptop"
La APP: sí, está 100% en Vercel serverless. Esta sesión de Claude Code: se pausa
al cerrar el laptop. Para retomar: leer `docs/PLAN_autonomo.md` (todo commiteado).
