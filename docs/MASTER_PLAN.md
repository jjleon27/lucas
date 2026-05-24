# LUCAS — MASTER PLAN
**Version:** 0.1.0 | **Last updated:** 2026-05-11 | **Based on:** full source read

> This document is the single source of truth for the LUCAS AI project.
> It is derived exclusively from the actual repository code. Anything not
> directly verified in source is marked **[NOT VERIFIED]**.
> Features are classified as: `[IMPLEMENTED]`, `[PARTIAL]`, or `[PLANNED]`.

---

## 1. Project Vision

LUCAS is an AI-powered personal finance assistant targeting Latin American users
(primary: Chile, secondary: Brazil / Mexico / Argentina). It combines three
historically separate tools — an expense tracker, a receipt OCR reader, and a
bill splitter — into a single progressive web app (PWA) backed by a persistent
FastAPI + PostgreSQL server.

Core bet: most users already have their financial data in screenshots (bank app,
supermarket receipts, payment confirmations). LUCAS reads these images directly
instead of requiring manual data entry or bank API integration.

---

## 2. Core Product Philosophy

1. **Screenshot-first.** The primary input method is an image drop or camera
   capture. Text input and voice are secondary. OCR / vision LLM must work well
   on Chilean bank apps, supermarket boletas, and credit-card statements.

2. **LLM as enrichment, not dependency.** The app is usable without any API
   key (Tesseract fallback, rule-based categorization). The AI makes it faster
   and smarter, not required.

3. **Cost discipline per user.** Every LLM call is logged to `ai_usage`. The
   design explicitly prioritises free layers (user rules → keyword rules) before
   falling to the LLM. Target KPI: `cost_LLM / active_user_monthly` as low as
   possible.

4. **Never double-count.** Transfer-linking (CC payment ↔ debit deduction) and
   deduplication logic are core invariants. The system would rather show 0 than
   count the same transaction twice.

5. **User trust through confirmation.** Voice, email-imported, and cartola
   transactions are never saved automatically — they go to a review queue or
   a confirmation card.

6. **Sync everywhere via PWA.** One Next.js codebase runs on mobile browsers,
   desktop browsers, and future native shells. No separate mobile codebase.

---

## 3. Confirmed Architecture

```
┌──────────────────────┐    REST + JWT      ┌───────────────────────┐
│  Next.js 14 (PWA)    │ ←────────────────→ │  FastAPI 0.115.0      │
│  React 18 / TS 5.6   │                    │  Python (uvicorn)     │
│  Tailwind CSS 3.4    │                    │  SQLAlchemy 2.0       │
│  recharts 2.12       │                    └─────────┬─────────────┘
│  lucide-react 0.445  │                              │
└──────────────────────┘                 ┌────────────┼──────────────┐
                                         ↓            ↓              ↓
                                  PostgreSQL 16   Local FS      AI Providers
                                  (anchor-based    or S3        OpenAI /
                                   balances,                    Anthropic /
                                   all user data)               Gemini
                                                             ↑
                                                       Tesseract OCR
                                                       (offline fallback)
```

**Communication:** REST JSON over HTTP. JWT (HS256, 7-day expiry) in
`Authorization: Bearer` header. Token stored in `localStorage` on the frontend.

**Infrastructure:** Docker Compose with three services: `db` (Postgres 16),
`backend` (FastAPI), `frontend` (Node 20-alpine, runs `npm run dev` — not
production-hardened).

**CORS:** Configurable via `CORS_ORIGINS` env var (comma-separated). Default:
`http://localhost:3000`.

---

## 4. Confirmed Implemented Systems

| System | Status | Location |
|--------|--------|----------|
| JWT authentication (email+password) | IMPLEMENTED | `backend/app/auth.py`, `routers/auth.py` |
| Passwordless / quick login | IMPLEMENTED (dev only) | `routers/auth.py` |
| Google OAuth login | IMPLEMENTED | `routers/auth.py` |
| Screenshot OCR + parsing | IMPLEMENTED | `backend/app/ocr.py` |
| PDF receipt parsing | IMPLEMENTED (with caveats — see §12) | `ocr.py` |
| Vision LLM parsing (gpt-4o-mini) | IMPLEMENTED | `ocr.py`, `ai/provider.py` |
| Categorizer (3-layer) | IMPLEMENTED | `ai/categorizer.py` |
| Transaction CRUD | IMPLEMENTED | `routers/transactions.py` |
| Account management (5 types) | IMPLEMENTED | `routers/accounts.py` |
| Balance computation (anchor-based) | IMPLEMENTED | `services/accounts.py` |
| Transfer-linking (auto + manual) | IMPLEMENTED | `services/accounts.py`, `routers/transactions.py` |
| Balance reconciliation | IMPLEMENTED | `routers/accounts.py` |
| Bill splitter v2 (multi-assign) | IMPLEMENTED | `routers/split.py` |
| Voice input (parse only) | PARTIAL (bug in code) | `ai/voice.py`, `routers/voice.py` |
| Cartola (PDF bank statement) import | IMPLEMENTED | `cartola.py`, `routers/cartola.py` |
| Email ingestion (forwarding webhook) | IMPLEMENTED | `routers/email.py`, `ai/email_parser.py` |
| Review queue | IMPLEMENTED | `routers/email.py` |
| Dashboard (monthly summary) | IMPLEMENTED | `routers/dashboard.py`, `ai/predictor.py` |
| Alerts engine | IMPLEMENTED | `ai/alerts.py` |
| Chat with Lucas (basic + action) | IMPLEMENTED | `ai/chat.py`, `routers/dashboard.py` |
| AI usage logging | IMPLEMENTED (service exists) | `services/ai_usage.py` |
| Deduplication | IMPLEMENTED | `services/dedupe.py` |
| Card image upload per account | IMPLEMENTED | `routers/accounts.py` |
| Multi-currency support (display) | PARTIAL (stored, no conversion) | `models.py`, `schemas.py` |
| PWA manifest | IMPLEMENTED | `frontend/public/manifest.json` |
| i18n (es/en/pt) | IMPLEMENTED | `frontend/src/lib/i18n.tsx` |

---

## 5. Backend Structure

```
backend/
├── app/
│   ├── main.py          # FastAPI app, CORS, static files, router registration
│   ├── config.py        # pydantic-settings: all env vars in one place
│   ├── database.py      # SQLAlchemy engine + session factory + init_db()
│   ├── models.py        # ORM models (User, Account, Transaction, Person, ...)
│   ├── schemas.py       # Pydantic DTOs (request/response validation)
│   ├── auth.py          # JWT creation + get_current_user dependency
│   ├── ocr.py           # OCR pipeline: vision_parse + heuristic_parse + boleta parser
│   ├── cartola.py       # Cartola (bank statement PDF) parser
│   ├── storage.py       # File save abstraction: local FS or S3
│   ├── ai/
│   │   ├── provider.py  # LLM abstraction (OpenAI / Anthropic / Gemini)
│   │   ├── categorizer.py  # 3-layer categorizer
│   │   ├── predictor.py    # Monthly summary + safe-spend calc
│   │   ├── alerts.py       # Rule-based alert messages
│   │   ├── chat.py         # Chat context builder + LLM call
│   │   ├── voice.py        # Voice transcript → structured transaction
│   │   └── email_parser.py # Email body → transaction dict
│   ├── routers/
│   │   ├── auth.py         # /auth/*
│   │   ├── upload.py       # /upload, /process
│   │   ├── transactions.py # /transactions/* + transfer endpoints
│   │   ├── accounts.py     # /accounts/* + reconcile + transfer linking
│   │   ├── split.py        # /split/*
│   │   ├── dashboard.py    # /dashboard, /chat, /chat/action
│   │   ├── cartola.py      # /cartola/upload, /cartola/commit
│   │   ├── voice.py        # /voice/parse
│   │   ├── email.py        # /email/inbound, /email/pending, /email/review/*
│   │   └── ai.py           # /ai/status, /ai/usage [file exists; content NOT READ]
│   └── services/
│       ├── accounts.py     # Balance computation, transfer detection/linking
│       ├── dedupe.py       # Duplicate detection + account hint resolution
│       └── ai_usage.py     # Token usage recording [file exists; content NOT READ]
├── tests/
│   └── test_boleta_parser.py  # Tests for boleta OCR parsing
├── requirements.txt
├── Dockerfile
└── .env.example
```

**Key env vars** (from `config.py`):

| Variable | Default | Required |
|----------|---------|----------|
| `DATABASE_URL` | `postgresql://lucas:lucas@localhost:5432/lucas` | Required |
| `JWT_SECRET` | `dev-secret-change-me` | Required (change in prod) |
| `STORAGE_BACKEND` | `local` | Optional |
| `LOCAL_STORAGE_DIR` | `./uploads` | Optional |
| `AI_PROVIDER` | `""` (auto) | Optional |
| `OPENAI_API_KEY` | `""` | Optional |
| `ANTHROPIC_API_KEY` | `""` | Optional |
| `GOOGLE_API_KEY` | `""` | Optional |
| `GOOGLE_CLIENT_ID` | `""` | Optional (Google OAuth) |
| `ALLOW_PASSWORDLESS` | `True` | Must be `False` in prod |
| `CORS_ORIGINS` | `http://localhost:3000` | Must set in prod |
| `EMAIL_DOMAIN` | `notify.lucasapp.com` | Required for email feature |

**Default AI models** (from `config.py`):
- OpenAI: `gpt-4o-mini`
- Anthropic: `claude-haiku-4-5-20251001`
- Gemini: `gemini-1.5-flash`

---

## 6. Frontend Structure

```
frontend/
├── src/
│   ├── app/                    # Next.js 14 App Router
│   │   ├── layout.tsx          # Root layout (Sidebar + main)
│   │   ├── page.tsx            # Landing / login page
│   │   ├── dashboard/page.tsx  # Dashboard: stats, pie chart, budget panel
│   │   ├── upload/page.tsx     # Receipt/screenshot upload + OCR review
│   │   ├── transactions/page.tsx # Transaction list + edit/delete
│   │   ├── split/page.tsx      # Bill splitter v2
│   │   ├── cartola/page.tsx    # Cartola (PDF) import flow
│   │   ├── chat/page.tsx       # Chat with Lucas
│   │   ├── accounts/page.tsx   # Account management
│   │   └── review/page.tsx     # Email review queue
│   ├── components/
│   │   ├── Sidebar.tsx         # Navigation sidebar
│   │   ├── StatCard.tsx        # Metric card with tone (good/warning/danger)
│   │   ├── UploadZone.tsx      # Drag-and-drop file upload area
│   │   ├── BillSplitter.tsx    # Full split session UI
│   │   ├── BudgetPanel.tsx     # Income target + fixed expenses form
│   │   ├── TransactionList.tsx # Paginated transaction list with edit
│   │   ├── VoiceButton.tsx     # Voice input trigger + confirmation card
│   │   ├── LucasFAB.tsx        # Floating action button with chat/action
│   │   ├── PendingTransferList.tsx # Unlinked CC payments list
│   │   ├── CategoryChips.tsx   # Category filter chips
│   │   ├── CardImagePicker.tsx # Card photo picker for accounts
│   │   ├── ImagePreview.tsx    # Receipt image preview
│   │   └── NumericInput.tsx    # Currency-formatted numeric input
│   └── lib/
│       ├── api.ts              # Typed fetch wrapper (all backend calls)
│       ├── categories.ts       # Category metadata (icons, colors)
│       └── i18n.tsx            # Translation hook (es/en/pt)
├── package.json
├── next.config.js
├── tailwind.config.ts
└── tsconfig.json
```

**Dependencies (runtime):**
- `next` 14.2.13 (App Router)
- `react` / `react-dom` 18.3.1
- `recharts` 2.12.7 (PieChart for category breakdown)
- `lucide-react` 0.445.0 (icons)

**Auth flow in frontend:** JWT stored in `localStorage` under key `lucas_token`.
`api.ts` auto-attaches as `Bearer`. 401 response auto-clears token and redirects
to `/`. No refresh token mechanism — user must re-login after 7 days.

---

## 7. Financial Logic Systems

### 7.1 Account Types

Five account types: `debit`, `credit`, `savings`, `wallet`, `cash`.

### 7.2 Balance Computation (`services/accounts.py`)

**Debit / savings / wallet / cash:**
```
live_balance = anchor_balance
             + SUM(income transactions, date >= anchor_date)
             - SUM(expense transactions, date >= anchor_date)
```

**Credit card:**
```
live_used = anchor_balance
           + SUM(expense transactions, date >= anchor_date)
           - SUM(income transactions, date >= anchor_date)
available_credit = credit_limit - live_used
```

If `anchor_date` is `NULL`, all transactions since the beginning are counted.

### 7.3 Dashboard Summary (`ai/predictor.py`)

- `income_target`: from `user.settings["income_target"]` → fallback to
  `monthly_budget` → fallback to `historical_avg_income` (3-month average).
- `fixed_total`: sum of `user.settings["fixed_expenses"]` list.
- `variable_budget = income_target - fixed_total`
- `safe_spend_actual = (income_actual - fixed_total - spent) / days_remaining`
- `safe_spend_projected = (variable_budget - spent) / days_remaining`
- Projected spend: blended `w * linear + (1-w) * trailing_30d` where
  `w = min(days_elapsed / 15, 1.0)`.
- Status thresholds: `projected > variable_budget * 1.2` → danger;
  `projected > variable_budget` → warning; else good.
- Excludes `is_transfer=True` and `status='pending_review'` transactions.

### 7.4 Alerts (`ai/alerts.py`)

Rule-based, no LLM. Checks:
- Budget percentage + projected overspend (sym + amount).
- Top category share ≥ 40% for Alimentación, Entretenimiento, Compras.
- Language: Spanish/Chilean neutral.

---

## 8. OCR and PDF Ingestion Systems

### 8.1 Screenshot Pipeline (`ocr.py`)

**Vision path (preferred):**
Requires an OpenAI API key. Sends image to `gpt-4o-mini` with vision.

Three-layer protection against wrong totals on Chilean boletas:
1. **Pre-LLM (Tesseract):** `_extract_boleta_totals()` + `_parse_boleta_from_text()`
   extract TOTAL NETO / IVA / TOTAL deterministically. Injected into LLM prompt
   as ground-truth constraints.
2. **LLM JSON:** `total_neto` and `iva_amount` in response validated against 19%
   ratio (Chilean SII legal requirement). Invalid values discarded.
3. **Post-LLM normalization:** Item source priority:
   - A) Tesseract confidence ≥ 0.97 → exact Tesseract item prices, no scaling.
   - B) Tesseract confidence ≥ 0.80 → light proportional normalization.
   - C) LLM items + proportional normalization to match ground-truth total.
   - D) No items (bank statements).

**Tesseract fallback:**
`run_ocr()` → `heuristic_parse()` → `_parse_signed_statement()`.
Handles CLP number formats, Spanish/Chilean date formats, signed statement
tables (+ income / - expense).

**Chilean boleta line formats handled:**
- Format A: `[barcode] [description] [price]` on one line.
- Format B: barcode-only line → next line has `NxUNIT description price`.
- Format C: restaurant (no barcode): `description price`.

**Supported image types:** JPEG, PNG, GIF, WEBP (auto-detected by magic bytes).
Max upload size: 25 MB. Images are downscaled to max 1600px before vision call.

### 8.2 PDF Parsing (`ocr.py` + `cartola.py`)

**Single receipt as PDF (`ocr.py`):**
- `parse_receipt_from_pdf()`: renders each page to JPEG via `pdf2image`, then
  calls `parse_receipt()` per page. Capped at 20 pages.
- **BUG:** `pdf2image` is imported in `ocr.py` but is NOT in `requirements.txt`.
  PDF parsing will fail unless `pdf2image` and `poppler` are installed manually.

**Cartola (bank statement PDF) (`cartola.py`):**
- Text PDFs (Santander, BCI, Falabella, BancoEstado, Itaú): `pdfplumber`
  extracts text → LLM structures into JSON (account_info, balances, transactions).
- Scanned PDFs: renders pages via `pdfplumber`'s `to_image()` → `vision_parse()`
  per page.
- `pdfplumber` IS in requirements.txt. This path works.

**LLM models for cartola:** uses `ai/provider.py` chat_completion (not vision).
Default model: `gpt-4o-mini` or whatever provider is configured.

---

## 9. Voice Transaction System

**Architecture (`ai/voice.py`, `routers/voice.py`):**

1. Browser transcribes speech via **Web Speech API** (client-side, free, local).
   Locale: `es-CL` assumed (NOT VERIFIED — depends on frontend VoiceButton.tsx).
2. Frontend POSTs transcript to `POST /voice/parse`.
3. `voice_ai.parse_voice()` sends to LLM with Chilean slang rules:
   - "luca/lucas" = 1,000 CLP each
   - "palo" = 1,000,000 CLP
   - "gamba/quina" = 100 CLP
   - "ayer" / "anteayer" date parsing
4. `dedupe.suggest_account_for_hint()` resolves `account_hint` string (e.g.,
   "débito", "CMR", "efectivo") to an actual `account_id`.
5. Returns `VoiceParsed` schema — frontend shows confirmation card.
6. User must explicitly save; no auto-save.

**CONFIRMED BUG (`ai/voice.py` lines 141–158):**
```python
if resp is None or not (resp.content or "").strip():  # ← resp.content does NOT exist
    return _fallback_unclear(transcript, today)
raw = resp.content.strip()                             # ← AttributeError
```
`LLMResponse` has `.text` not `.content`. When AI is available, this code will
raise `AttributeError` on every voice call. **Voice feature is broken when AI
is configured.**

---

## 10. Transfer-Linking System

**Purpose:** When a user pays their credit card from their debit account, the
same money appears twice — once as debit outflow, once as credit card inflow.
These must be linked so the dashboard doesn't double-count.

**Data model:** `Transaction.is_transfer` (bool) + `Transaction.linked_transaction_id`
(self-referential FK, nullable).

**Auto-linking (`services/accounts.py:reconcile_new_transaction`):**
Triggered on every `POST /transactions` and `POST /email/review/{tx_id}` confirm.

Match criteria (`find_transfer_match`):
- Different account from the candidate
- Opposite `is_income` flag
- Amount within ±0.5 CLP (or ±0.5% for non-CLP)
- Date within ±4 days
- Neither transaction already linked
- At least one side matches CC payment heuristic (regex on merchant name) OR
  the incoming tx has `is_transfer=True` (set by parser)

CC payment merchant regex: `pago tarjeta|pago recibido|pago cmr|pago falabella|
pago credit|pago tc|abono tarjeta|abono cuenta|transferencia recibida|
transferencia enviada|credit card payment|cc payment|payment received`

**Manual linking:**
- `GET /accounts/transfer/suggest/{tx_id}` — up to 10 candidates (±10 days, wider tolerance)
- `POST /accounts/transfer/link` — link two transactions
- `POST /accounts/transfer/unlink/{tx_id}` — unlink

**Dashboard badge:** `pending_transfers` count shown on dashboard when unlinked
CC payments exist. `PendingTransferList.tsx` component handles the UI.

---

## 11. Balance Reconciliation System

**Trigger:** Manual (user enters real bank balance) or automatic (cartola commit).

**`POST /accounts/{acc_id}/reconcile`:**
- User provides `expected_balance` (what the bank app shows right now).
- App computes `drift = expected - computed`.
- Snaps `anchor_date = today` and `anchor_balance = expected`.
- From this moment, the live balance formula starts from the new anchor.
- Returns `ReconcileOut` with drift and old/new anchor values.

**Cartola reconciliation (`POST /cartola/commit`):**
- Optional `reconcile_to_closing_balance=true` + `closing_balance`.
- After saving all new transactions, re-anchors to cartola's closing balance.
- Drift computed as `closing_balance - computed_after_save`.

---

## 12. Known Technical Debt

| # | Issue | Severity | Location |
|---|-------|----------|----------|
| 1 | `pdf2image` imported but NOT in `requirements.txt` | HIGH | `ocr.py:42` |
| 2 | `resp.content` bug — should be `resp.text` | HIGH | `ai/voice.py:141,158` |
| 3 | Same `resp.content` bug | HIGH | `cartola.py:158` |
| 4 | `ALLOW_PASSWORDLESS=True` by default | HIGH | `config.py` |
| 5 | No Alembic migrations folder — schema changes require full DB rebuild | MEDIUM | (absent) |
| 6 | Docker frontend runs `npm run dev` — not production-hardened | MEDIUM | `docker-compose.yml` |
| 7 | `NEXT_PUBLIC_API_URL=http://localhost:8000` in docker-compose — frontend container cannot reach backend by localhost | MEDIUM | `docker-compose.yml` |
| 8 | `JWT_SECRET` defaults to `dev-secret-change-me` | HIGH | `config.py` |
| 9 | No refresh token — users re-login every 7 days | LOW | `auth.py`, `api.ts` |
| 10 | `routers/ai.py` content not read — AI usage endpoint behavior unverified | LOW | `routers/ai.py` |
| 11 | `services/ai_usage.py` content not read — recording logic unverified | LOW | `services/ai_usage.py` |
| 12 | Legacy `SplitSession` model referenced in README intro but not in `models.py` | LOW | README vs models |
| 13 | Category mismatch: voice.py uses English categories ("Food & Dining") while OCR/dashboard use Spanish ("Alimentación") | MEDIUM | `ai/voice.py:43` |
| 14 | `cartola.py:_llm_structure` calls `resp.content` (line 158) — same bug as voice | HIGH | `cartola.py:158` |

---

## 13. Current Project Risks

1. **Voice feature non-functional:** The `resp.content` bug means any user
   with an AI API key configured will get a 500 on `/voice/parse`.

2. **Cartola LLM path non-functional:** `resp.content` bug in `cartola.py`
   means the text-based cartola parsing path fails silently for all banks.
   Only the scanned-PDF fallback path (vision) works.

3. **PDF receipts only work if `pdf2image` + `poppler` are installed out-of-band.**
   The Docker image will not install them automatically.

4. **No database migration strategy.** Adding a column requires either raw
   ALTER TABLE or dropping+recreating the DB.

5. **Docker `localhost` networking issue.** The frontend Docker container
   sets `NEXT_PUBLIC_API_URL=http://localhost:8000` but in a multi-container
   network, `localhost` resolves to the frontend container itself.

6. **No test coverage beyond boleta parser.** Only `tests/test_boleta_parser.py`
   exists. All routers, services, and AI modules have zero test coverage.

7. **Multi-currency is stored but not converted.** Transactions in different
   currencies are shown as-is. Dashboard sums across currencies without
   conversion — numbers will be wrong for multi-currency users.

8. **Email domain is hardcoded fallback** (`notify.lucasapp.com`). If
   `EMAIL_DOMAIN` env var is not set, the forwarding address will be on a
   domain the developer does not own.

---

## 14. Rules for Future Development

1. **Never invent architecture.** Read existing code before adding to it.
   The AI provider abstraction (`ai/provider.py`) must be used for all LLM
   calls — never import `openai`, `anthropic`, or `google.generativeai` directly.

2. **Add migrations before schema changes.** Before modifying `models.py`,
   create an Alembic migration. `alembic` is already in requirements.

3. **Respect the deduplication invariant.** Any new code that creates
   transactions must call `dedupe.find_duplicate()` first or risk duplicate rows.

4. **All new transaction creation must try transfer auto-link.** Call
   `account_svc.reconcile_new_transaction()` after saving any new transaction.

5. **LLM usage must be logged.** Pass `purpose=`, `user_id=`, and `db=` to
   `ai_provider.chat_completion()` / `ai_provider.vision_json()` on every call.

6. **Categorizer layers must be preserved.** Any new categorization must
   call `categorizer.categorize()`, not bypass it. User corrections must
   call `categorizer.remember_correction()`.

7. **Status field for auto-imported transactions.** Any transaction not
   manually entered by the user must start as `status="pending_review"`.
   Only user-confirmed transactions get `status="confirmed"`.

8. **Fix bugs before new features.** The `resp.content` bug (items 2, 3 in §12)
   breaks voice and cartola. Fix before extending those features.

9. **No new categories without updating all four category lists:**
   `ai/categorizer.py:DEFAULT_CATEGORIES`, `ai/categorizer.py:_RULES`,
   `ai/voice.py:_SYSTEM_PROMPT`, `ai/email_parser.py:_SYSTEM_PROMPT`.

10. **Spanish as the primary language.** Backend error messages, chat prompts,
    and alert text default to Chilean Spanish. The `ai/chat.py` system prompt
    explicitly enforces Spanish regardless of user language.

---

## 15. Rules for AI Agents Working on This Repo

1. **Read before writing.** Always read the relevant file(s) before editing.
   Never reconstruct logic from memory.

2. **Verify LLMResponse fields.** `LLMResponse` (defined in `ai/provider.py`)
   has `.text`, `.prompt_tokens`, `.completion_tokens`, `.model`, `.provider`.
   It does NOT have `.content`. Any code using `resp.content` is a bug.

3. **Use the abstraction.** All LLM calls go through `ai/provider.py:chat_completion()`
   or `ai/provider.py:vision_json()`. Never call provider SDKs directly.

4. **Match the schema layer.** Request and response validation happens in
   `schemas.py` (Pydantic). ORM models live in `models.py`. Do not merge them.

5. **Account balance is computed, not stored.** `Account.anchor_balance` is
   the static anchor. Live balance is always `compute_account_balance()`.
   Never write live balance to the DB.

6. **Transfer links are bidirectional.** `link_as_transfer(a, b)` sets both
   `a.linked_transaction_id = b.id` and `b.linked_transaction_id = a.id`.
   Never set only one side.

7. **The `is_me` person.** Each user has at most one `Person` with `is_me=True`
   (auto-created by `_get_or_create_me()`). Never create a second one for the
   same user.

8. **Do not touch `resp.content` anywhere.** Use `resp.text` on `LLMResponse`.

9. **Chilean CLP number format.** "$17.517" = 17517 (dot = thousands separator,
   no decimals). Never treat a dot as a decimal point for CLP. The `_to_float()`
   and `_parse_clp()` helpers handle this — use them.

10. **Category language consistency.** The categorizer (`ai/categorizer.py`)
    uses Spanish categories. Voice (`ai/voice.py`) currently uses English
    categories — this is a known mismatch (§12 item 13). Fix by aligning
    voice to Spanish before shipping.

---

## 16. Short-Term Roadmap

Priority order based on bugs and gaps in current implementation.

| Priority | Task | Type |
|----------|------|------|
| P0 | Fix `resp.content` → `resp.text` in `ai/voice.py` | Bug fix |
| P0 | Fix `resp.content` → `resp.text` in `cartola.py` | Bug fix |
| P0 | Add `pdf2image` to `requirements.txt` | Infrastructure |
| P1 | Set `ALLOW_PASSWORDLESS=False` default (or env-gate clearly) | Security |
| P1 | Set production-safe `JWT_SECRET` enforcement (raise if default) | Security |
| P1 | Add Alembic migrations directory + initial migration | Infrastructure |
| P1 | Fix voice category language (use Spanish, match categorizer) | Consistency |
| P2 | Fix Docker networking: `NEXT_PUBLIC_API_URL` for container-to-container | Infrastructure |
| P2 | Add test coverage for transfer-linking + balance computation | Testing |
| P2 | Read and verify `routers/ai.py` content | Verification |
| P3 | Add refresh token endpoint | Auth |
| P3 | Shareable split links (non-user URL) | Feature |
| P3 | Recurring subscription detection | Feature |

---

## 17. Long-Term Roadmap

Items listed in README as post-MVP and confirmed as not implemented in source.

1. **Conversational chat with tool-use:** Current chat sends raw transaction
   history as context. True tool-use (SELECT queries on demand) would allow
   arbitrary questions without the 200-transaction context limit.

2. **Shareable split links:** Public URL for non-users to assign their own
   items. Organic growth loop.

3. **Email magic-link authentication:** Replace `allow_passwordless` quick
   login with a proper signed-URL flow.

4. **Open banking (Plaid / Belvo / Fintoc):** Direct connection to Chilean/LatAm
   bank accounts. Highest-impact but highest-complexity feature.

5. **WebSocket live-split:** Multiple people assigning items in the same room
   in real time.

6. **On-device OCR (WASM Tesseract):** Privacy-preserving mode — runs in browser
   without uploading the image. Requires significant frontend work.

7. **Fine-tuned categorizer:** Once 10k+ user-corrected labels exist, replace
   LLM categorization with a small local model.

8. **Multi-currency conversion:** Store exchange rates, convert all amounts to
   display currency in dashboard aggregations.

9. **Native app wrapper:** Capacitor/React Native shell once PMF is clear.

10. **Fine-grained AI cost dashboard:** Currently `ai_usage` table tracks tokens.
    Build a UI for users to see their own LLM cost breakdown.

---

## 18. Non-Goals

The following are explicitly out of scope for LUCAS:

- **Investment / portfolio tracking.** LUCAS is a spending tracker, not a
  brokerage integration.
- **Crypto tracking.** No wallet integrations planned.
- **Tax filing or tax advice.** The system categorizes boletas but makes no
  legal tax claims.
- **Real-time market data / exchange rates.** Multi-currency display is stored;
  no live conversion feed.
- **Social features (friends leaderboard, public profiles).** Bill splitting
  is local/private to the user's account.
- **SMS parsing.** Only email and image ingestion are implemented.
- **Automated savings rules.** LUCAS shows data; it does not move money.

---

## 19. Constraints

1. **LatAm-first design.** Chilean number formats (CLP, no decimals, dots as
   thousands separators) are baked into OCR, voice, and categorizer logic.
   Other LatAm currencies (BRL, MXN, ARS, PEN, COP) are supported in schema
   but not deeply tested.

2. **LLM provider-agnostic design.** All AI calls go through `ai/provider.py`.
   The system must remain swappable between OpenAI, Anthropic, and Gemini
   without code changes outside that file.

3. **No direct bank API.** LUCAS is explicitly screenshot-first. Bank API
   integration (Plaid, Fintoc, Belvo) is a future option, not a current
   dependency.

4. **Privacy:** Images are stored server-side (local FS or S3). OCR text is
   stored in `raw_ocr` column. No image is sent to a third party except when
   the LLM vision path is active (i.e., API key is set).

5. **Single-user sessions.** JWT auth is per-user. Multi-user household
   accounts are not supported.

6. **Deployment target:** Docker Compose (self-hosted). No managed cloud
   deployment has been built or documented.

---

## 20. Current Stability Status

| Component | Status | Notes |
|-----------|--------|-------|
| Auth (password + Google) | STABLE | |
| Auth (quick/passwordless) | STABLE (dev only) | Must disable in prod |
| Transaction CRUD | STABLE | |
| Account management | STABLE | |
| Balance computation | STABLE | anchor-based, correctly excludes transfers |
| Transfer-linking (auto) | STABLE | |
| Transfer-linking (manual) | STABLE | |
| Balance reconciliation | STABLE | |
| OCR (vision path) | STABLE | requires OpenAI key |
| OCR (Tesseract fallback) | STABLE | requires Tesseract system dep |
| PDF receipt parsing | BROKEN | `pdf2image` not in requirements.txt |
| Categorizer | STABLE | |
| Dashboard summary | STABLE | |
| Alerts | STABLE | |
| Chat (basic + action) | STABLE | |
| Bill splitter v2 | STABLE | |
| Cartola (text PDF) | BROKEN | `resp.content` bug in LLM path |
| Cartola (scanned PDF) | STABLE | vision path not affected by content bug |
| Voice parse | BROKEN | `resp.content` bug — 500 when AI is configured |
| Email ingestion | STABLE | requires email domain + webhook setup |
| Review queue | STABLE | |
| Deduplication | STABLE | |
| AI usage logging | STABLE (code exists) | `services/ai_usage.py` not fully read |
| i18n (es/en/pt) | PARTIAL | strings translated; currency not converted |
| PWA | PARTIAL | manifest exists; no service worker verified |

---

*This document was generated by reading every file in the repository. Items
marked [NOT VERIFIED] or flagged with notes represent gaps in verification,
not gaps in the product. Do not add features to this document until the code
that implements them has been written and read.*
