# LUCAS AI — Current State
> Last updated: 2026-05-11
> Based on verified source code only. Unknown items marked [NOT VERIFIED].

---

## 1. Overall Status

LUCAS is a functional MVP with a working core loop: OCR receipt scan → transaction save → balance update.
Several subsystems are partially broken due to known bugs. The system is NOT production-ready due to security defaults and missing infrastructure hardening.

---

## 2. Feature Status Matrix

### 2.1 Authentication & Users

| Feature | Status | Notes |
|---------|--------|-------|
| Email + password registration | WORKING | Argon2 hashing |
| JWT login (7-day expiry) | WORKING | HS256, stored in localStorage |
| Passwordless email login | WORKING | `ALLOW_PASSWORDLESS=True` default |
| Google OAuth | [NOT VERIFIED] | `auth_provider` field exists, flow unverified |
| Facebook OAuth | [NOT VERIFIED] | `auth_provider` field exists, flow unverified |
| User settings (JSON blob) | WORKING | currency, locale, notification prefs |
| Per-user forwarding email address | WORKING | `lucas-{email_token}@{EMAIL_DOMAIN}` |

**Security issues:**
- `JWT_SECRET` defaults to `"dev-secret-change-me"` — UNSAFE for production
- `ALLOW_PASSWORDLESS=True` default — UNSAFE for production

---

### 2.2 Accounts

| Feature | Status | Notes |
|---------|--------|-------|
| Create / edit / archive account | WORKING | types: debit, credit, savings, wallet, cash |
| Anchor-based balance computation | WORKING | Never stores live balance; computes from anchor + txns |
| Debit balance formula | WORKING | `anchor_balance + income - expense` since `anchor_date` |
| Credit used formula | WORKING | `anchor_balance + expense - income` since `anchor_date` |
| Credit limit tracking | WORKING | `credit_limit` column on Account |
| Card image / color / icon | WORKING | Visual customization, `card_image_url` |
| Multi-currency | PARTIAL | `currency` field exists; FX conversion [NOT VERIFIED] |

---

### 2.3 Transaction Entry — Manual

| Feature | Status | Notes |
|---------|--------|-------|
| Manual transaction form | WORKING | POST /transactions, 60s idempotency guard |
| Deduplication on manual save | NOT IMPLEMENTED | No dedupe check on POST /transactions |
| `reconcile_new_transaction()` on save | WORKING | Called in transactions router |
| Transfer-linking on save | WORKING | `link_as_transfer()` called in reconcile |
| Category auto-suggestion | WORKING | 3-layer: learned rules → keywords → LLM |
| Income flag | WORKING | `is_income` field |

---

### 2.4 Transaction Entry — OCR / Receipt Scan

| Feature | Status | Notes |
|---------|--------|-------|
| Camera/image upload | WORKING | Multipart POST /upload |
| Tesseract OCR extraction | WORKING | Primary path |
| LLM receipt parsing | WORKING | Vision API fallback |
| Boleta IVA validation (19% check) | WORKING | Accepts ±5% tolerance |
| Boleta proportional normalization | WORKING | Fallback when IVA ratio fails |
| PDF receipt parsing | BROKEN | `pdf2image` missing from requirements.txt |
| Deduplication on upload | WORKING | Jaccard + amount + ±2-day window |
| `reconcile_new_transaction()` on confirm | WORKING | Called in save-to-transactions path |

---

### 2.5 Transaction Entry — Cartola (Bank Statement PDF)

| Feature | Status | Notes |
|---------|--------|-------|
| PDF upload | WORKING | POST /cartola/upload |
| Scanned PDF → vision parse | WORKING | pdf2image → per-page vision_parse |
| Text-based PDF → LLM structure | BROKEN | `resp.content` bug in `_llm_structure()`, silently fails |
| Deduplication in upload | WORKING | Compared against existing txns |
| `reconcile_new_transaction()` on commit | MISSING | GAP — POST /cartola/commit does not call it |
| Transfer-linking on commit | MISSING | Not called via reconcile (see above) |
| Status of committed cartola txns | WORKING | Set to `"confirmed"` |

---

### 2.6 Transaction Entry — Email Ingestion

| Feature | Status | Notes |
|---------|--------|-------|
| SendGrid inbound webhook | WORKING | POST /email/inbound |
| Per-user forwarding address | WORKING | `lucas-{email_token}@{EMAIL_DOMAIN}` |
| Email → transaction parse | WORKING | LLM-based extraction |
| Deduplication on ingest | WORKING | Standard dedupe applied |
| Transactions created as `pending_review` | WORKING | Review queue status |
| Review queue: confirm transaction | WORKING | POST /email/review calls `reconcile_new_transaction()` |
| `reconcile_new_transaction()` on confirm | WORKING | Called in review confirm path |

---

### 2.7 Transaction Entry — Voice

| Feature | Status | Notes |
|---------|--------|-------|
| Browser Web Speech API transcription | WORKING | Frontend-only |
| Chilean slang parsing (lucas, palo) | WORKING | Backend logic |
| LLM voice parsing | BROKEN | `resp.content` bug raises HTTP 500 on LLM path |
| Fallback to keyword parsing | WORKING | Fallback triggers before LLM if no transcript |
| Spanish → DEFAULT_CATEGORIES mapping | PARTIAL | [NOT VERIFIED] alignment |

---

### 2.8 Bill Splitting

| Feature | Status | Notes |
|---------|--------|-------|
| Start split from receipt | WORKING | POST /split/start |
| Start split manually (no receipt) | WORKING | POST /split/start-manual |
| Per-item assignment to people | WORKING | ItemAssignment model |
| Split types: equal / percent / amount | WORKING | `split_type` + `value` fields |
| `is_me` person for the user themselves | WORKING | Created lazily on first split |
| Deduplication on start-manual | MISSING | GAP — not called |
| `reconcile_new_transaction()` on start-manual | MISSING | GAP — not called |
| Settlement: save to LUCAS | WORKING | POST /split/settle → saves my share as confirmed txn |

---

### 2.9 AI / LLM

| Feature | Status | Notes |
|---------|--------|-------|
| Provider abstraction layer | WORKING | `ai/provider.py`, supports OpenAI / Anthropic / Gemini |
| Vision parsing (image→JSON) | WORKING | OpenAI-only (gpt-4o vision) |
| Text completion | WORKING | All three providers |
| LLM usage tracking | WORKING | `AiUsage` model; [NOT VERIFIED] logging coverage |
| Chat / AI assistant endpoint | [NOT VERIFIED] | `routers/ai.py` exists, contents unverified |
| Per-user learned category rules | WORKING | Written on category correction, read before LLM |
| Keyword categorizer rules | WORKING | Static rules, Spanish labels |

---

### 2.10 Categories & Budgets

| Feature | Status | Notes |
|---------|--------|-------|
| 19 default Spanish categories | WORKING | Seeded on signup |
| Per-user custom categories | WORKING | CRUD on /categories |
| Per-category monthly budget | WORKING | `monthly_budget` field on Category |
| Global monthly budget per user | WORKING | `monthly_budget` field on User |
| Budget vs. actual tracking | [NOT VERIFIED] | Computation not verified in source |

---

### 2.11 Infrastructure

| Feature | Status | Notes |
|---------|--------|-------|
| Docker Compose (3 containers) | WORKING | db, backend, frontend |
| PostgreSQL 16 | WORKING | Only supported DB |
| Alembic migrations | WORKING | `alembic/versions/` exists |
| Frontend `NEXT_PUBLIC_API_URL` | BROKEN | `http://localhost:8000` fails inside Docker networking |
| Frontend server | DEV ONLY | Runs `npm run dev`, not production build |
| S3 / file storage | [NOT VERIFIED] | boto3 in requirements.txt |
| Environment variable validation | WORKING | Pydantic Settings via `config.py` |

---

## 3. Confirmed Bugs (P0 / P1)

### BUG-001 — Voice LLM path raises HTTP 500 [P0]
- **File:** `backend/app/ai/voice.py`, lines ~141, ~158
- **Cause:** `resp.content` — LLMResponse has `.text`, not `.content`
- **Impact:** Every voice transaction that reaches the LLM path fails with AttributeError
- **Fix:** Replace `resp.content` with `resp.text` (2 occurrences)

### BUG-002 — Cartola text parsing silently fails [P1]
- **File:** `backend/app/cartola.py`, line ~158 in `_llm_structure()`
- **Cause:** `resp.content` — same root cause as BUG-001
- **Impact:** All text-based PDF cartola parsing fails; falls back to image (vision) path silently
- **Fix:** Replace `resp.content` with `resp.text`

### BUG-003 — `pdf2image` missing from requirements.txt [P0]
- **File:** `backend/requirements.txt`
- **Cause:** Package imported in `ocr.py` but not listed as a dependency
- **Impact:** PDF receipt parsing fails on any fresh install / container rebuild
- **Fix:** Add `pdf2image` to requirements.txt

### BUG-004 — Docker frontend networking broken [P1]
- **File:** `docker-compose.yml`
- **Cause:** `NEXT_PUBLIC_API_URL: http://localhost:8000` — inside Docker, localhost = frontend container
- **Impact:** All API calls from frontend fail when running in Docker
- **Fix:** Change to `http://backend:8000` or use Docker service name

### BUG-005 — `reconcile_new_transaction()` not called on cartola commit [P1]
- **File:** `backend/app/routers/cartola.py`, commit_cartola()
- **Cause:** Transfer-linking and reconciliation skipped after batch import
- **Impact:** Cartola-imported transactions never get transfer-linked; balance anomalies possible
- **Fix:** Add `account_svc.reconcile_new_transaction(db, user_id, tx)` loop after save

### BUG-006 — `reconcile_new_transaction()` and dedupe not called on split/start-manual [P1]
- **File:** `backend/app/routers/split.py`, start_manual()
- **Cause:** Transaction created directly without going through reconcile pipeline
- **Impact:** Duplicate split transactions possible; no transfer-linking
- **Fix:** Add dedupe check and `reconcile_new_transaction()` call

---

## 4. Security Issues

| Issue | Severity | Default | Recommended |
|-------|----------|---------|-------------|
| `JWT_SECRET` default | CRITICAL | `"dev-secret-change-me"` | Random 256-bit secret from env |
| `ALLOW_PASSWORDLESS` default | HIGH | `True` | `False` for production |
| JWT stored in localStorage | MEDIUM | — | HttpOnly cookie preferred |
| No rate limiting confirmed | [NOT VERIFIED] | — | Required before public launch |

---

## 5. What Is NOT Implemented (vs. MASTER_PLAN intent)

- Production frontend build (currently `npm run dev` in Docker)
- FX currency conversion (field exists, logic unverified)
- Push notifications (architecture mentions it, flow unverified)
- End-to-end encryption for stored receipts
- Rate limiting on auth endpoints
- Automated test suite (no test files observed in file listing)
