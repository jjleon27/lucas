# LUCAS AI — Roadmap
> Last updated: 2026-05-11
> Priorities derived from confirmed bugs, architectural gaps, and MASTER_PLAN intent.
> Items marked [NOT VERIFIED] depend on code sections not yet fully read.

---

## Tier 0 — Fix Before Any New Feature
> These are confirmed bugs that corrupt data or make the system unusable. Fix first, always.

### T0-1: Fix `resp.content` → `resp.text` in voice.py [BUG-001]
- **File:** `backend/app/ai/voice.py` (lines ~141, ~158)
- **Impact:** Every voice transaction that hits the LLM path raises HTTP 500
- **Effort:** 5 minutes — 2-line fix

### T0-2: Fix `resp.content` → `resp.text` in cartola.py [BUG-002]
- **File:** `backend/app/cartola.py` (line ~158 in `_llm_structure()`)
- **Impact:** Cartola text extraction always silently fails; all text PDFs fall to slower vision path
- **Effort:** 5 minutes — 1-line fix

### T0-3: Add `pdf2image` to requirements.txt [BUG-003]
- **File:** `backend/requirements.txt`
- **Impact:** PDF receipt parsing fails on any fresh install
- **Effort:** 1 minute

### T0-4: Fix Docker frontend networking [BUG-004]
- **File:** `docker-compose.yml`
- **Change:** `NEXT_PUBLIC_API_URL: http://localhost:8000` → `http://backend:8000`
- **Impact:** All API calls from frontend fail inside Docker
- **Effort:** 1 minute

---

## Tier 1 — Architectural Invariant Gaps
> These allow data corruption to occur silently. Fix before going to production.

### T1-1: Add `reconcile_new_transaction()` to cartola commit [BUG-005]
- **File:** `backend/app/routers/cartola.py`, `commit_cartola()`
- **Change:** Call `account_svc.reconcile_new_transaction(db, user_id, tx)` for each saved transaction
- **Impact:** Cartola-imported transactions are never transfer-linked; internal transfers double-count in balance

### T1-2: Add dedupe + `reconcile_new_transaction()` to split/start-manual [BUG-006]
- **File:** `backend/app/routers/split.py`, `start_manual()`
- **Change:** Run dedupe check before save; call `reconcile_new_transaction()` after
- **Impact:** Duplicate split transactions possible; no transfer-linking on manual splits

### T1-3: Harden security defaults
- **File:** `backend/app/config.py`
- **Changes:**
  - Remove `"dev-secret-change-me"` default for `jwt_secret` — fail-fast if unset
  - Change `allow_passwordless` default to `False`
- **Impact:** Production deployment with default config is insecure

---

## Tier 2 — Core Completeness
> Features in the architecture or UI that are partially broken or unverified.

### T2-1: Verify and fix voice category alignment
- **Symptom:** LLM voice parser may return English category names; DEFAULT_CATEGORIES are Spanish
- **Action:** Read `routers/voice.py` and `ai/voice.py` fully; ensure LLM prompt enforces Spanish category names from DEFAULT_CATEGORIES list
- **Risk:** Mis-categorized voice transactions

### T2-2: Verify AI chat endpoint
- **File:** `backend/app/routers/ai.py` [NOT VERIFIED]
- **Action:** Read the file; confirm endpoint exists, LLMResponse.text is used (not .content), usage is logged to AiUsage
- **Risk:** If .content bug present here too, chat is broken

### T2-3: Verify AI usage logging coverage
- **File:** `backend/app/services/ai_usage.py` [NOT VERIFIED]
- **Action:** Confirm all LLM call sites (voice, categorize, parse, chat, cartola, email) log to AiUsage
- **Risk:** Cost visibility missing for some paths

### T2-4: Production Docker frontend
- **File:** `docker-compose.yml`, `frontend/Dockerfile` (if exists)
- **Change:** Replace `npm run dev` with `npm run build && npm start` for production
- **Impact:** Dev server in production has no caching, is slow, and exposes source maps

### T2-5: Verify FX / multi-currency handling
- **Scope:** `currency` field exists on Account and Transaction; conversion logic [NOT VERIFIED]
- **Action:** Confirm whether balance computation handles mixed-currency accounts; document or implement

---

## Tier 3 — Reliability & Operations

### T3-1: Write automated test suite
- **Scope:** No test files observed in repository
- **Priority areas:**
  1. `reconcile_new_transaction()` pipeline (transfer-linking, balance update)
  2. OCR boleta IVA validation (19% check + normalization)
  3. Deduplication (Jaccard + amount + date window)
  4. JWT auth middleware
  5. All transaction creation paths (assert reconcile is called)

### T3-2: Rate limiting on auth endpoints
- **Scope:** `/auth/login`, `/auth/register`, `/auth/passwordless-*`
- **Action:** Add rate limiting middleware (e.g., slowapi) to prevent brute-force and abuse

### T3-3: Database connection pooling review
- **Scope:** SQLAlchemy engine config in `database.py` [NOT VERIFIED]
- **Action:** Confirm pool size, max overflow, and connection timeout are appropriate for production load

### T3-4: Alembic migration hygiene
- **Scope:** `alembic/versions/`
- **Action:** Confirm all model changes have corresponding migration files; no unmigrated schema drift

### T3-5: Error handling audit
- **Focus:** Cartola `_llm_structure()` wraps `resp.content` bug in bare `except Exception: pass` — this pattern hides bugs
- **Action:** Audit all `try/except Exception` blocks; replace with specific exceptions and proper logging

---

## Tier 4 — New Features (Post-Stabilization)

> Do not implement any of these until Tier 0, 1, and 2 are resolved.

### T4-1: Push notifications
- Architecture mentions it; implementation unverified. Mobile PWA notification support.

### T4-2: Google OAuth / Facebook OAuth
- `auth_provider` field exists on User model; flows not verified in routers.

### T4-3: Recurring transaction detection
- Identify monthly subscriptions, rent, utilities from transaction history.

### T4-4: Export (CSV / PDF statements)
- Allow users to export transaction history filtered by date, category, account.

### T4-5: Shared expenses / group splits
- Extend Person/ItemAssignment model to support cross-user bill sharing (not just personal tracking).

### T4-6: Bank API integration (Open Finance)
- Direct bank feed instead of manual cartola uploads. Chile: CMF Open Finance framework.

### T4-7: Budget alerts
- Real-time push/email notification when category spend approaches or exceeds monthly budget.

### T4-8: Savings goals
- Dedicated savings tracking distinct from transaction categories.

---

## Work Order Summary

```
Priority  | ID    | Item                                          | Effort
----------|-------|-----------------------------------------------|--------
CRITICAL  | T0-1  | Fix resp.content → resp.text (voice.py)       | 5 min
CRITICAL  | T0-2  | Fix resp.content → resp.text (cartola.py)     | 5 min
CRITICAL  | T0-3  | Add pdf2image to requirements.txt             | 1 min
CRITICAL  | T0-4  | Fix Docker NEXT_PUBLIC_API_URL                | 1 min
HIGH      | T1-1  | reconcile on cartola commit                   | 30 min
HIGH      | T1-2  | dedupe + reconcile on split/start-manual      | 30 min
HIGH      | T1-3  | Harden security defaults (JWT, passwordless)  | 15 min
MEDIUM    | T2-1  | Voice category → Spanish alignment            | 1 hr
MEDIUM    | T2-2  | Verify/fix AI chat endpoint                   | 1 hr
MEDIUM    | T2-3  | Verify AI usage logging coverage              | 1 hr
MEDIUM    | T2-4  | Production Docker frontend build              | 2 hr
MEDIUM    | T2-5  | FX / multi-currency verification              | 1 hr
LOW       | T3-1  | Automated test suite                          | 2-3 days
LOW       | T3-2  | Rate limiting on auth endpoints               | 2 hr
LOW       | T3-3  | DB connection pool review                     | 1 hr
LOW       | T3-4  | Alembic migration hygiene                     | 1 hr
LOW       | T3-5  | Error handling audit (bare except blocks)     | 2 hr
LATER     | T4-*  | New features — only after above resolved      | varies
```

---

## What Will NOT Be Done

- SQLite support — PostgreSQL 16 is the only supported database
- Direct provider SDK calls bypassing `ai/provider.py` abstraction
- Storing computed live balances in the database
- New authentication providers before hardening current auth defaults
- Full rewrites of working subsystems (OCR pipeline, deduplication, categorizer)
