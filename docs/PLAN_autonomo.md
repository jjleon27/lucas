# Plan de trabajo autónomo (sesión 2026-09-09)

Objetivo: trabajar hasta agotar presupuesto, dejando todo commiteado+pusheado y
la doc + graphify al día. Cambios visibles en el celu → push frecuente.

## Fase 1 — Bugs seguros (bajo riesgo, algunos visibles en la app)
1. `backend/app/ocr.py` ~1350: var `compact` no definida en el fallback dos-etapas
   (NameError silencioso). Arreglar para usar `send_bytes`.
2. `backend/app/ocr.py` `_RECEIPT_TEXT_PROMPT`: portar reglas MONEDA + propina +
   "fecha null si no estás seguro" (igual que se hizo en `_RECEIPT_PROMPT`).
3. `frontend/src/components/TransactionList.tsx` ~276: no se puede cambiar de
   categoría estándar a "Otra" en edición inline. (bug viejo pendiente)
4. Tests (`pytest`) + `npm run build` frontend. Commit + push. Deploy.

## Fase 2 — Sync de documentación (pedido explícito del usuario)
5. `docs/MASTER_PLAN.md`: §3 (arquitectura), §5/§6 (estructura), §19 (constraint
   "Deployment target: Docker Compose" → Vercel), §20 (tabla de estabilidad).
6. `docs/ROADMAP.md`: marcar migración Vercel; T4-6 (open finance) contexto Fintoc/scraping.
7. `docs/CURRENT_STATE.md`, `docs/ARCHITECTURE.md`, `README.md`, `docs/AI_RULES.md`:
   sección deployment → Vercel + Neon + Blob. Quitar/marcar lo de Railway/Docker.
8. `graphify update .` (AST-only, sin costo API).

## Fase 3 — Feature Proyectos (Opción B) si queda presupuesto
9. Backend modelo `Project(id, user_id, name, budget, start_date, end_date, archived)`
   + `transactions.project_id` FK nullable. ALTER en `_migrate_schema()`.
10. `backend/app/routers/projects.py`: CRUD + resumen (gasto total, barra presupuesto,
    gasto por categoría dentro del proyecto).
11. Schemas + wire en `main.py`.
12. Frontend: página `/projects` (lista + detalle con barra de presupuesto),
    selector de proyecto al crear/editar transacción.
13. Tests backend del router.

## Fase 4 — Cierre (antes de agotar tokens)
14. `graphify update .` final.
15. `MASTER_PLAN.md` + `ROADMAP.md` + `CURRENT_STATE.md` al día con lo hecho.
16. `docs/SESSION_STATE.md` sección ESTADO MANUAL con estado final.
17. commit + push de todo. Deploy a prod (o instrucción al usuario si el deploy
    me lo bloquea el classifier).

## Permisos necesarios
- Bash: `git` (add/commit/push/checkout/merge/log/diff), `npm` (build/test),
  `python3 -m pytest`, `graphify`, `vercel` (deploy/env/logs), `curl` (verificar).
- Edit / Write en el repo.
- WebSearch / WebFetch para verificar APIs si hace falta.

## PROGRESO (2026-09-09)
- [x] Fase 1: `compact` NameError → `send_bytes`; `_RECEIPT_TEXT_PROMPT` reglas
  moneda/propina/fecha; **bug del split arreglado** (`_normalize_boleta_items`
  ya no escala precios correctos al TOTAL NETO). Nuevo caso eval `lider_quilicura`.
  Tests 457 pass (11 fail preexistentes). Eval OCR **95.5%**. Deployado + verificado
  en prod ("CHOCO 160" = 2150, no 1765).
  - Nota TransactionList.tsx ~276 (cambiar a "Otra" inline): revisado, el código
    actual YA lo maneja bien — parece arreglado en el churn de junio. No se tocó.
- [x] Fase 2: MASTER_PLAN/ROADMAP/CURRENT_STATE/README sincronizados a Vercel.
  `graphify update .` hecho (2452 nodos). Prep Fable 5.1 (anthropic 0.125.0,
  provider default `claude-fable-5-1`, max_tokens 16k). Deploy OK con el bump.
- [ ] Fase 3: **Proyectos (Opción B)** — NO empezada. Handoff abajo.
- [ ] Fase 4: cierre (este bloque).

## Handoff Fase 3 — feature Proyectos (para próxima sesión)
Spec (memoria `project-next-feature`): entidad `Project(id, user_id, name, budget
opcional, start_date, end_date, archived)` + `transactions.project_id` FK nullable.
Pasos:
1. `backend/app/models.py`: modelo `Project`; `Transaction.project_id`.
2. `backend/app/database.py` `_migrate_schema()`: ALTER TABLE transactions ADD
   COLUMN project_id INTEGER; CREATE TABLE projects (create_all lo hace).
3. `backend/app/schemas.py`: `ProjectCreate/Out`, `project_id` en `TransactionOut/Update`.
4. `backend/app/routers/projects.py`: CRUD + `GET /projects/{id}/summary`
   (gasto total, % presupuesto, por categoría). Wire en `main.py`.
5. `backend/tests/`: test del router.
6. Frontend: `frontend/src/app/projects/page.tsx` (lista + detalle con barra de
   presupuesto), selector de proyecto en el form de transacción
   (`transactions/page.tsx` y/o `review/page.tsx`), item en `Sidebar.tsx`.
7. `graphify update .`, actualizar MASTER_PLAN §16/§20, commit+push, `vercel --prod`.

## "Que siga funcionando si cierro el laptop"
- **La APP**: ya cumple — está 100% en Vercel (serverless), no depende del laptop.
- **Esta sesión de Claude Code**: SÍ se pausa al cerrar el laptop (corre local).
  Para trabajo autónomo continuo se necesitaría un agente cloud (`/schedule` o
  agente remoto). Todo está commiteado+pusheado y este archivo + SESSION_STATE
  permiten a cualquier sesión futura retomar desde Fase 3.

## Reglas de la corrida
- Commits chicos y atómicos, push después de cada fase (o antes si hay riesgo de compactación).
- No gastar API de pago sin necesidad (el eval con LLM NO se corre en loop).
- Si algo necesita decisión del usuario → dejarlo anotado en este archivo y seguir con lo demás.
- Verificar cada deploy con `curl /api/health` + rutas.
