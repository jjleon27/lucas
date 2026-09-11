# SESSION STATE
*Auto-guardado por hook Stop · 2026-09-11 02:26:30*

## Presión de contexto
- Tool uses esta sesión: **606** — 🔴 MUY ALTA — posible compactación pronto

## Última petición del usuario
[Image: source: /Users/kako2/Downloads/IMG_4615.PNG]

## Último trabajo del asistente
¿Qué te gustaría aclarar antes de que decidas?

## Archivos modificados esta sesión
- `/Users/kako2/.claude/projects/-Users-kako2-Documents-lucas/memory/MEMORY.md`
- `/Users/kako2/.claude/projects/-Users-kako2-Documents-lucas/memory/deploy_vercel_2026_09.md`
- `/Users/kako2/.claude/projects/-Users-kako2-Documents-lucas/memory/feedback_ocr_no_escalar.md`
- `/Users/kako2/.claude/projects/-Users-kako2-Documents-lucas/memory/ocr_eval_harness.md`
- `/Users/kako2/.claude/projects/-Users-kako2-Documents-lucas/memory/project_next_feature.md`
- `README.md`
- `api/index.py`
- `api/requirements.txt`
- `backend/.env.example`
- `backend/app/ai/provider.py`
- `backend/app/config.py`
- `backend/app/database.py`
- `backend/app/main.py`
- `backend/app/models.py`
- `backend/app/ocr.py`
- `backend/app/routers/bills.py`
- `backend/app/routers/projects.py`
- `backend/app/routers/transactions.py`
- `backend/app/schemas.py`
- `backend/app/storage.py`
- `backend/requirements.txt`
- `backend/tests/eval/README.md`
- `backend/tests/eval/expected/baobar_providencia.json`
- `backend/tests/eval/expected/barlaprovidencia.json`
- `backend/tests/eval/expected/cuenta_valeria.json`
- `backend/tests/eval/expected/danes_vitacura.json`
- `backend/tests/eval/expected/dondewilly_vinadelmar.json`
- `backend/tests/eval/expected/lider_quilicura.json`
- `backend/tests/eval/expected/montana_bellavista.json`
- `backend/tests/eval/expected/ponzano_madrid.json`
- `backend/tests/eval/results/.gitignore`
- `backend/tests/eval/run_eval.py`
- `backend/tests/test_bills.py`
- `backend/tests/test_projects.py`
- `docs/CURRENT_STATE.md`
- `docs/MASTER_PLAN.md`
- `docs/PLAN_autonomo.md`
- `docs/PLAN_migracion_vercel.md`
- `docs/PLAN_split_v3.md`
- `docs/ROADMAP.md`
- `docs/SESSION_STATE.md`
- `frontend/next.config.js`
- `frontend/src/app/projects/page.tsx`
- `frontend/src/app/split/page.tsx`
- `frontend/src/components/Sidebar.tsx`
- `frontend/src/components/TransactionList.tsx`
- `frontend/src/lib/api.ts`
- `package.json`
- `vercel.json`

## Cómo continuar en la próxima sesión
1. Lee `docs/MASTER_PLAN.md` §20 y §16
2. Lee `docs/ROADMAP.md` tabla "Work Order Summary"
3. Revisa los archivos modificados arriba
4. Pregunta al usuario si quiere continuar desde la última tarea

## ADDENDUM 8 (2026-09-11) — bbox real en vez de punto: solucionado con GPT, sin Google Vision
El usuario preguntó "¿pero esto no lo puede hacer GPT?" tras ver que las franjas
seguían mal (screenshot: pared de colores tapando toda la foto, causa: altura
fija 26px encimándose con 15-20 ítems). Investigué en GitHub/web: el patrón
correcto es pedir un bbox real con formato estricto (no un punto vago) — nunca
lo habíamos intentado así. Cambié el prompt para pedir bbox_x0/y0/x1/y1 por
ítem + auto-revisión. Probado con 3 boletas reales: bboxes ajustados,
secuenciales, sin encimarse. NO hizo falta Google Cloud Vision.

Backend: ParsedItem/BillItem += bbox_x0/y0/x1/y1 (migración). Frontend: la
franja usa el bbox exacto (ancho/alto reales) sobre el imgBox que ya calculaba
bien Fable. Eval 92.4% (vs 95.5% baseline — ruido normal de danes_vitacura, no
regresión sistemática). Deployado y verificado end-to-end en prod (bboxes
secuenciales confirmados vía curl).

CRÍTICO — quinta iteración sobre esta feature: pedir captura de pantalla real
al usuario antes de seguir asumiendo que algo "debería" verse bien.
