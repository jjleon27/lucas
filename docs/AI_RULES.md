# LUCAS — AI AGENT ENGINEERING RULES
**Version:** 1.0 | **Last updated:** 2026-05-11
**Authority:** Supersedes any AI default behavior. Applies to ALL Claude Code sessions.

> These rules are derived exclusively from the verified LUCAS repository source.
> They are not aspirational guidelines — they are mandatory constraints.
> Violating any rule marked INVARIANT risks silent data corruption or broken features.
> Violating any rule marked SECURITY risks user data exposure.
> Violating any rule marked NEVER is grounds to stop and ask the user before proceeding.

---

## RULE ZERO — THE PRIME DIRECTIVE

**Before writing a single character of code, read every file you intend to modify.**

If you cannot read it, do not touch it.
If you cannot verify a fact, do not assume it.
If you cannot find a feature in the source, do not implement as if it exists.

---

## 1. Repository Reading Rules

### 1.1 — Read-Before-Edit is Mandatory
NEVER edit a file you have not read in the current session. Not even a one-line
change. Code you have not read has invariants you do not know.

### 1.2 — Start Every Session with a Structural Survey
On any session touching more than one file, run:
```bash
find . -type f | grep -v "/.next/" | grep -v "node_modules" | grep -v "__pycache__" | grep -v ".pyc" | sort
```
Understand what exists before deciding what to add.

### 1.3 — Anti-Hallucination: Verify Feature Existence
NEVER assume a function, endpoint, field, or module exists because it sounds
logical given the project name. Verify with grep or a file read first.

```bash
# Before assuming a function exists:
grep -r "function_name" backend/app/
# Before assuming an endpoint exists:
grep -r "router\." backend/app/routers/
```

### 1.4 — Anti-Hallucination: Do Not Reconstruct from README
The README and the code can diverge. README is documentation; source is truth.
When they conflict, **the source wins**. Verify every claim in the README
against actual `models.py`, `schemas.py`, and router files before acting on it.

### 1.5 — Unread Files Are Unknown Files
`routers/ai.py` and `services/ai_usage.py` were not fully verified at the time
of MASTER_PLAN creation. Read them before modifying or depending on them.
Do not assume their content matches any description.

### 1.6 — Cross-Reference the Three Sources
For any feature, verify it in all three places before describing it as implemented:
1. `models.py` — does the data model support it?
2. The relevant router — is the endpoint wired?
3. The frontend `api.ts` — is the client calling it?

If any layer is missing, the feature is at best PARTIAL.

---

## 2. File Editing Rules

### 2.1 — Anti-Full-Rewrite Rule
NEVER rewrite a file from scratch. Edit only the lines that need to change.
A rewrite destroys comments, invariant relationships, and historical context
that you cannot reconstruct from a single read.

**NEVER DO THIS:**
```python
# "I'll clean this up and rewrite it properly"
# [deletes 400 lines and writes 400 new lines]
```

**DO THIS:**
```python
# Read the file. Identify the exact lines. Use Edit tool to change only them.
```

### 2.2 — Edit Scope Must Match Task Scope
A bug fix edits the bug. A feature adds the feature. Neither task justifies
reformatting unrelated code, renaming unrelated variables, or reorganizing
unrelated imports. Scope creep silently breaks things.

### 2.3 — Preserve All Docstrings and Module Comments
Module-level docstrings in the LUCAS backend explain architectural decisions
(e.g., why `anchor_balance` exists, why `is_transfer` is set). Do not delete
them when editing.

### 2.4 — No Dead-Code Cleanup During Feature Work
Never remove code that "looks unused" during a feature session. Mark it with
a comment if suspicious and raise it to the user. Silent removals break
backward compatibility.

### 2.5 — One Change Per Commit Scope
Do not bundle unrelated changes. If fixing the `resp.content` bug in `voice.py`,
do not simultaneously refactor `provider.py`. Separate concerns, separate edits.

### 2.6 — Smoke Test After Every Edit
After any backend edit:
```bash
cd backend && python -c "from app.main import app; print('import ok')"
```
After any frontend edit:
```bash
cd frontend && npx tsc --noEmit
```
Do not report a task complete if these fail.

---

## 3. Backend Architecture Rules

### 3.1 — Module Boundaries Are Hard
The LUCAS backend has a strict layering:

```
routers/     <- HTTP concerns only (request parsing, response shaping, auth)
services/    <- Business logic (balance computation, transfer detection)
ai/          <- All LLM interaction (provider, categorizer, chat, voice, etc.)
models.py    <- ORM definitions only (no business logic)
schemas.py   <- Pydantic DTOs only (no business logic)
```

**NEVER** put business logic in `models.py` or `schemas.py`.
**NEVER** put database queries in `ai/` modules directly (go through `services/`).
**NEVER** put HTTP-specific code (request, response, status codes) in `services/` or `ai/`.

### 3.2 — Settings Access
NEVER use `os.getenv()` anywhere in the codebase. All env vars are read through
`config.py:settings`. If a new env var is needed, add it to the `Settings` class first.

**NEVER DO THIS:**
```python
import os
api_key = os.getenv("OPENAI_API_KEY")
```

**DO THIS:**
```python
from .config import settings
api_key = settings.openai_api_key
```

### 3.3 — Router Auth Dependency
Every router endpoint that touches user data must use:
```python
current: models.User = Depends(auth.get_current_user)
```
Never add an endpoint that bypasses this dependency for data belonging to a user.

### 3.4 — No Global State in Routers
Routers are stateless. Do not add module-level variables that hold user data,
cached queries, or mutable configuration.

### 3.5 — Postgres Compatibility Only
This project runs on PostgreSQL 16. All SQL written in SQLAlchemy or raw SQL
must be Postgres-compatible.

**NEVER use SQLite-only SQL constructs:**
- No `AUTOINCREMENT` (use `SERIAL` or SQLAlchemy default)
- No `PRAGMA` statements
- No `REPLACE INTO`
- No SQLite-specific `UPSERT` syntax
- When writing `func.*` aggregations, test they work in Postgres
- Do not sort by Python-side logic when a Postgres `ORDER BY` is available

### 3.6 — Database Session Lifecycle
Sessions are request-scoped via `Depends(get_db)`. Never store a `db` session
in a module-level variable. Never pass a session between requests.

---

## 4. Frontend Architecture Rules

### 4.1 — All Backend Calls Go Through `src/lib/api.ts`
NEVER use raw `fetch()` in page or component files. Every backend call goes
through the typed wrapper in `api.ts`, which handles JWT attachment, 401
auto-logout, and error normalization.

**NEVER DO THIS (in a page component):**
```typescript
const res = await fetch(`http://localhost:8000/transactions`, { ... });
```

**DO THIS:**
```typescript
import { listTransactions } from "@/lib/api";
const txs = await listTransactions();
```

### 4.2 — No External State Management Libraries
LUCAS frontend uses React `useState` / `useEffect` only. Do not add Redux,
Zustand, Jotai, React Query, or any other state library without explicit
user instruction. The existing pattern must be preserved.

### 4.3 — TypeScript Types Must Mirror Backend Schemas
When adding a new backend field, add the corresponding TypeScript field in
`api.ts` before using it in a component. Never use `any` to paper over a
type mismatch. If `TransactionOut` gains a new field, `Transaction` in `api.ts`
must gain it too.

### 4.4 — Auth Guard Pattern
Every authenticated page checks the token before loading:
```typescript
useEffect(() => {
  if (!getToken()) { router.replace("/"); return; }
  loadData();
}, [router, loadData]);
```
Never remove this guard. Never add authenticated pages without it.

### 4.5 — No New Dependencies Without User Approval
The frontend has a minimal dependency set (Next.js, React, Tailwind, recharts,
lucide-react). Do not add new npm packages without explicit user instruction.

### 4.6 — PWA-Safe Client Code
All code that touches `window`, `localStorage`, or browser APIs must be
guarded with `typeof window !== "undefined"`. This is already done in `api.ts`;
maintain it in any new code.

---

## 5. Database Safety Rules

### 5.1 — INVARIANT: Never Write Computed Balances to the DB
`Account.current_balance`, `Account.current_used`, and `Account.available_credit`
are **computed fields** returned by `services/accounts.py:compute_account_balance()`.
They exist only in `schemas.AccountOut` as runtime values.

**NEVER** store a computed balance to the database. The only balance-related
columns on the `accounts` table are:
- `anchor_balance` — the known-correct starting point
- `anchor_date` — when that anchor was established
- `credit_limit` — static limit

**NEVER DO THIS:**
```python
account.current_balance = some_computed_value
db.commit()
```

### 5.2 — Anchor Updates Are Privileged Operations
The only places that legitimately write `anchor_balance` and `anchor_date` are:
1. `routers/accounts.py:reconcile_account()` — manual reconciliation
2. `routers/cartola.py:commit_cartola()` — cartola reconciliation
3. Initial account creation in `routers/accounts.py:create_account()`

Any other code writing to these columns is a bug.

### 5.3 — Transaction `status` Field Rules
Only two valid values: `"confirmed"` and `"pending_review"`.
- Manual user-entered transactions: always `"confirmed"` (default).
- Email-imported transactions: always `"pending_review"`.
- Transactions become `"confirmed"` only via explicit user action in review queue.
- **NEVER** auto-confirm email-imported transactions.

### 5.4 — Cascade Delete Awareness
`Transaction` has `CASCADE` delete from `User`. `ReceiptItem` has `CASCADE`
delete from `Transaction`. `ItemAssignment` has `CASCADE` delete from
`ReceiptItem` and `Person`. Never delete a parent record without understanding
what children will also be deleted.

### 5.5 — Nullable FKs Are Intentional
`Transaction.account_id` is nullable (backward compatibility with pre-accounts
data). `Transaction.linked_transaction_id` is nullable (unlinked transfers).
`ReceiptItem.assigned_to` is nullable (legacy single-assign, unassigned).
Do not add NOT NULL constraints to these columns.

### 5.6 — No Raw SQL Without Review
Prefer SQLAlchemy ORM. If raw SQL is absolutely necessary, review it for:
- SQL injection (use parameterized queries only)
- Postgres compatibility (no SQLite-isms)
- Correct transaction handling (session.execute, not cursor.execute)

### 5.7 — No `db.commit()` in AI Modules
`ai/` modules receive a `db` session for logging purposes only
(`services/ai_usage.py`). They must not commit to any user-data tables.
All data persistence happens in routers and services.

---

## 6. Financial Invariant Rules

### 6.1 — INVARIANT: Transfers Are Excluded from Dashboard Totals
`ai/predictor.py:_sum()` explicitly filters `is_transfer=False` and
`status != "pending_review"`. Any new aggregate query on transactions for
budget/spending purposes must apply the same two filters.

**NEVER** include transfer transactions in:
- `total_spent`
- `total_income`
- `projected_end_of_month`
- Category breakdown
- Safe-spend calculations

### 6.2 — INVARIANT: Amount Is Always Positive
`Transaction.amount` stores the absolute value of the transaction.
Direction is encoded in `is_income` (True = income, False = expense).
Never store a negative amount.

### 6.3 — Income Target Fallback Chain
`income_target` resolution (from `ai/predictor.py:summarize()`):
1. `user.settings["income_target"]` (if > 0)
2. `user.monthly_budget` (if > 0)
3. `historical_avg_income` (3-month average, if > 0)
4. 0.0 (no budget set)

Do not break this chain. Do not skip levels. Do not substitute different sources.

### 6.4 — Variable Budget Definition
`variable_budget = income_target - fixed_total`
This is the ceiling against which projected spending is measured.
`fixed_total` comes from `user.settings["fixed_expenses"]` list. Modifying
this definition will break status computation and safe-spend calculations.

### 6.5 — Status Thresholds Are Fixed
```python
if projected_spend > variable_budget * 1.2:  # → "danger"
elif projected_spend > variable_budget:       # → "warning"
else:                                         # → "good"
```
Do not change these thresholds without explicit user instruction. They are
product decisions, not implementation details.

### 6.6 — CLP Number Format Is Non-Negotiable
In CLP (Chilean Peso): dots are thousands separators, commas are decimal
separators (if present), and amounts have no decimal places.
- `"$17.517"` = 17517 (NOT 17.517)
- `"$1.489.991"` = 1489991

Use `ocr.py:_to_float()` or `ocr.py:_parse_clp()` for any CLP string parsing.
Never roll your own CLP parser.

### 6.7 — Chilean IVA Is 19% of TOTAL NETO (Legal Requirement)
`IVA = round(TOTAL_NETO * 0.19)` is a Chilean SII legal constraint.
If IVA extracted from a receipt deviates more than 5% from this formula,
it is an OCR error — fall back to computing it from TOTAL NETO.
This is enforced in `ocr.py:_extract_boleta_totals()`. Do not remove this sanity check.

---

## 7. Transfer-Linking Invariant Rules

### 7.1 — INVARIANT: Auto-Link After Every Transaction Save
Any code path that creates a new `Transaction` and commits it to the database
must call:
```python
account_svc.reconcile_new_transaction(db, user_id, tx)
```
This applies to: `routers/transactions.py`, `routers/email.py` (on confirm),
and any future import path. The only exceptions are transactions that are
explicitly CC payments being saved as the second leg of an already-linked pair.

### 7.2 — INVARIANT: Links Are Always Bidirectional
```python
a.linked_transaction_id = b.id
b.linked_transaction_id = a.id
a.is_transfer = True
b.is_transfer = True
```
**NEVER** set only one side. A half-linked transfer is a data corruption bug.
Always use `services/accounts.py:link_as_transfer(db, a, b)`.

### 7.3 — Unlink Is Also Bidirectional
When unlinking a transfer, both `a.linked_transaction_id = None` and
`b.linked_transaction_id = None` must be set, and both `is_transfer` flags
must be cleared. See `routers/transactions.py:unlink_transfer()` as the reference.

### 7.4 — Transfer Match Tolerance Is Fixed
Auto-link tolerance (from `services/accounts.py:find_transfer_match()`):
- CLP: ±0.5 (integer amounts)
- Other currencies: ±0.5% (minimum ±0.01)
- Date window: ±4 days

These are calibrated for Chilean bank statement timing. Do not widen them
without understanding the false-positive rate.

### 7.5 — CC Payment Heuristic Is a Regex
`services/accounts.py:looks_like_cc_payment()` uses a regex against the merchant
field. It is a heuristic — it can have false positives. Do not treat its output
as definitive. The user can always manually unlink.

### 7.6 — pending_transfers Badge Consistency
`count_pending_cc_payments()` and the filter in `list_transactions(pending_transfers=True)`
must use the same logic. If you change one, change the other. A count that
does not match the list is a UX bug.

---

## 8. Deduplication Invariant Rules

### 8.1 — INVARIANT: All External Transaction Creation Must Check Duplicates
Every path that proposes saving a transaction from an external source (upload,
cartola, email, voice) must call:
```python
dedupe.find_duplicate(db, user_id=..., account_id=..., proposed=tx)
```
before committing. If a duplicate is found, `proposed.dupe_of` must be set.
**Never silently skip this step.**

### 8.2 — Duplicate Match Criteria Are Fixed
From `services/dedupe.py:find_duplicate()`:
- Same `is_income` flag
- Date within ±2 days
- Amount within ±0.5 CLP (or ±0.5% for other currencies)
- Merchant similarity: Jaccard ≥ 0.5 on tokens, OR substring match

Do not tighten (will miss real duplicates) or loosen (will create false positives
that block legitimate distinct transactions) these criteria.

### 8.3 — Duplicate Is a Suggestion, Not a Block
`dupe_of` on `ParsedReceipt` is a **hint** to the frontend. The user decides
whether to skip or save anyway. Backend code never silently drops a transaction
because it looks like a duplicate.

### 8.4 — Exact-Duplicate Guard on Manual Save
`routers/transactions.py:create_transaction()` has a 60-second exact-duplicate
guard (same user + date + amount + is_income + merchant). This prevents
double-tap submissions. Do not remove this guard.

### 8.5 — Account Scope for Deduplication
When `account_id` is known, `find_duplicate()` scopes its search to that account.
When account is unknown (null), it searches across all user transactions.
Do not pass `account_id=None` when you actually know the account — this
increases false-positive risk.

---

## 9. OCR System Rules

### 9.1 — INVARIANT: Never Trust OCR Totals Blindly
The three-layer protection in `ocr.py:vision_parse()` exists because LLMs
hallucinate amounts. The hierarchy is:
1. Tesseract ground-truth (`_extract_boleta_totals`) — highest trust
2. Validated LLM values (IVA ratio within 5% of 19%) — medium trust
3. Raw LLM amount — lowest trust, only used when ground truth unavailable

**NEVER** bypass this hierarchy. If adding new OCR logic, preserve the
Tesseract ground-truth → validated LLM → raw LLM priority.

### 9.2 — Chilean Boleta Structure Is Deterministic
Chilean SII boletas always have:
- `TOTAL NETO` = pre-tax subtotal
- `IVA (19%)` = exactly 19% of TOTAL NETO
- `TOTAL` = TOTAL NETO + IVA

Product line prices are NETO (pre-tax). The IVA row is the only place IVA
appears. Never add IVA to individual product prices.

### 9.3 — Barcode vs. Price Distinction
Chilean supermarket receipts include 12-13 digit EAN barcodes as line prefixes.
These are NEVER prices. Any number with 12+ consecutive digits is a barcode.
The `_BARCODE_ONLY_RE` regex and the 12-14 digit stripping in `_parse_boleta_from_text`
enforce this. Do not remove these guards.

### 9.4 — Vision Is Optional, Tesseract Is Always Available
The vision path requires an `OPENAI_API_KEY`. The Tesseract path runs offline.
Any OCR-related code must degrade gracefully when no API key is set.
`ocr.py:parse_receipt()` implements this correctly — preserve the fallback.

### 9.5 — Image Shrink Before Vision Calls
All images are shrunk to max 1600px before sending to the vision API
(`_shrink_for_vision()`). This reduces cost and latency. Do not send
full-resolution images to the LLM API.

### 9.6 — pdf2image Must Be Added to requirements.txt
`pdf2image` is currently imported in `ocr.py` but missing from `requirements.txt`.
Before any PDF receipt work, add `pdf2image` and the system dependency
`poppler-utils` to the requirements and Dockerfile. This is a P0 bug.

### 9.7 — Tesseract Confidence Thresholds Are Calibrated
```
tess_conf >= 0.97 → use Tesseract items directly (exact prices, no scaling)
tess_conf >= 0.80 → light normalization
tess_conf <  0.80 → fall back to LLM items with proportional normalization
```
Do not lower the 0.97 threshold (introduces price errors).
Do not raise the 0.80 threshold (loses valid item data).

---

## 10. AI Provider Abstraction Rules

### 10.1 — INVARIANT: No Direct Provider SDK Calls
NEVER import `openai`, `anthropic`, or `google.generativeai` outside of
`backend/app/ai/provider.py`. All LLM calls go through:
- `ai_provider.chat_completion(messages, *, purpose=, user_id=, db=)`
- `ai_provider.vision_json(system_prompt, user_text, image_data_url, *, purpose=, user_id=, db=)`

**NEVER DO THIS:**
```python
from openai import OpenAI
client = OpenAI(api_key="...")
resp = client.chat.completions.create(...)
```

**DO THIS:**
```python
from .ai import provider as ai_provider
resp = ai_provider.chat_completion(messages, purpose="parse", user_id=user_id, db=db)
```

### 10.2 — LLMResponse Has `.text`, Not `.content`
`LLMResponse` is defined in `ai/provider.py` as:
```python
@dataclass
class LLMResponse:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""
    provider: str = ""
```
`resp.content` does NOT exist. Using it raises `AttributeError`.
**ALWAYS use `resp.text`.**

This is a confirmed bug in `ai/voice.py` lines 141 and 158, and in
`cartola.py` line 158. Fix these before extending those features.

### 10.3 — Handle None Return
`chat_completion()` and `vision_json()` return `None` when no provider is
configured. Every call site must check for `None` before accessing `.text`.

**NEVER DO THIS:**
```python
resp = ai_provider.chat_completion(messages)
data = json.loads(resp.text)  # crashes if resp is None
```

**DO THIS:**
```python
resp = ai_provider.chat_completion(messages)
if resp is None:
    return fallback_value
data = json.loads(resp.text)
```

### 10.4 — Log Every LLM Call
Every call to `chat_completion()` or `vision_json()` must pass:
- `purpose=` — one of: `"chat"`, `"categorize"`, `"parse"`, `"voice"`,
  `"email_parse"` — or a new descriptive string.
- `user_id=` — the authenticated user's ID.
- `db=` — the active SQLAlchemy session.

Omitting these silently skips usage logging and corrupts cost-per-user metrics.

### 10.5 — Provider Selection Is Automatic
The provider is selected by `_pick_provider()` based on `AI_PROVIDER` env var,
falling back to the first provider with a key set. Do not hardcode a provider
in call sites. The `gpt-4o-mini` hardcode in `email_parser.py` is a documented
exception for cost reasons — do not copy this pattern without justification.

### 10.6 — Adding a New Provider
To add a new LLM provider:
1. Implement a subclass of `LLMProvider` in `ai/provider.py`.
2. Register it in `_PROVIDERS`.
3. Add its API key and model name to `config.py:Settings`.
4. Update `.env.example` with the new variables.
5. Do NOT change any other file in the codebase.

---

## 11. Voice System Rules

### 11.1 — Fix the Bug Before Extending
The voice system has a confirmed `AttributeError` at `ai/voice.py:141`:
```python
if resp is None or not (resp.content or "").strip():  # BUG: .content → .text
```
**NEVER add new voice features without fixing this first.**

### 11.2 — Voice Never Auto-Saves
The voice pipeline returns a `VoiceParsed` confirmation object. The user must
explicitly confirm before the transaction is saved. Do not add auto-save
behavior to the voice path.

### 11.3 — Voice Uses User's Local Date
`VoiceParseIn.today` is passed from the frontend (user's local date). The
backend uses `body.today or date.today()`. This must be preserved — the server
may run in a different timezone than the user.

### 11.4 — Chilean Slang Is Required in the Voice Prompt
The voice LLM system prompt encodes Chilean colloquial amounts:
- "luca/lucas" = 1,000 CLP each
- "palo" = 1,000,000 CLP
- "gamba/quina" = 100 CLP

Do not remove or simplify these rules. This vocabulary is critical for the
target user base.

### 11.5 — Category Language Must Be Spanish
The voice system prompt currently uses English category names ("Food & Dining").
This is a confirmed bug (MASTER_PLAN §12 item 13). When fixing it, align
to the 19 Spanish categories in `ai/categorizer.py:DEFAULT_CATEGORIES`.

### 11.6 — Confidence Threshold Is a Contract
`VoiceParsed.confidence < 0.6` must trigger a frontend confirmation request,
not auto-save. The confidence field exists specifically for this. Do not
remove it from the response schema.

---

## 12. Cartola/PDF Parsing Rules

### 12.1 — Fix the Bug Before Extending
`cartola.py:_llm_structure()` has a confirmed `AttributeError` at line 158:
```python
raw = (resp.content or "").strip()  # BUG: .content → .text
```
The text-based cartola path (Santander, BCI, Falabella PDFs with text layer)
is non-functional. **NEVER add new cartola features without fixing this first.**

### 12.2 — Two Distinct Cartola Paths
- Path A (text PDF): `pdfplumber` extracts text → `_llm_structure()` → `_build_result()`
- Path B (scanned PDF): `_render_pages_as_images()` → `vision_parse()` per page

Path A is broken (see 12.1). Path B works. Do not confuse them.
`pdfplumber` is in `requirements.txt`. Path B uses `pdfplumber`'s `.to_image()`
method — it does NOT require `pdf2image`.

### 12.3 — Closing Balance Reconciliation Is Always Optional
`CartolaCommitIn.reconcile_to_closing_balance` defaults to `False`.
Do not change this default. Reconciliation is a user-initiated action.

### 12.4 — Cartola Deduplication Before Commit
`routers/cartola.py:upload_cartola()` populates `dupe_of` on every transaction
before returning the `CartolaReport`. Never save a transaction with
`dupe_of != None` without explicit user override.

### 12.5 — Drift Is Informational Only
`CartolaReport.drift = closing_balance - app_balance` is displayed to the user.
It does not automatically trigger reconciliation and should not.

---

## 13. Testing Rules

### 13.1 — The Only Existing Tests Are Boleta Parser Tests
`backend/tests/test_boleta_parser.py` is the only test file. Do not delete it.
Do not modify it without understanding its purpose.

### 13.2 — New Features Require Tests
Any new service function must have a corresponding test before being considered
complete. Any bug fix must have a regression test.

### 13.3 — Test Against PostgreSQL, Not SQLite
Tests must run against a real PostgreSQL instance. SQLite has different SQL
semantics, type coercion, NULL behavior, and ordering guarantees. Using SQLite
in tests while running Postgres in production hides real bugs.

### 13.4 — Do Not Mock the Database for Financial Tests
Balance computation, transfer-linking, and deduplication tests must use a real
database session with real transaction data. Mocking the DB for these tests
masks the SQL queries most likely to break in production.

### 13.5 — Test Isolation
Each test must create its own user and data. Never depend on test ordering
or shared state. Use transactions that roll back after each test.

---

## 14. Refactor Rules

### 14.1 — Refactor Is a Separate Task
Never refactor during a bug fix. Never refactor during a feature addition.
Refactoring changes behavior invisibly and makes bug attribution impossible.
If a refactor is needed, propose it to the user as a standalone separate task.

### 14.2 — Preserve Backward Compatibility Fields
Several fields and endpoints are explicitly marked "kept for backward compat":
- `ReceiptItem.assigned_to` (legacy single-assign)
- `POST /split/assign` (legacy endpoint)
- `SplitResultOut` (legacy schema)
- `Transaction.linked` relationship (self-referential)

Do not remove them. They may be used by older clients or test fixtures.

### 14.3 — No Rename Without Full Audit
Before renaming any column, field, or route, grep for every usage:
```bash
grep -r "old_name" backend/ frontend/
```
Renames silently break API clients and database queries.

### 14.4 — Category Names Are a Stable External Contract
The 19 Spanish category names in `ai/categorizer.py:DEFAULT_CATEGORIES` appear
in the database, in the frontend UI, in the LLM prompts, and in user data.
Renaming a category without a data migration corrupts existing transactions.
Treat them as a versioned external API.

---

## 15. Migration Rules

### 15.1 — Alembic Before Any Schema Change
`alembic` is in `requirements.txt`. A migrations directory must exist before
modifying `models.py`. Every model change must have a migration script.

### 15.2 — No Destructive Migrations Without Backup
`DROP COLUMN`, `DROP TABLE`, `NOT NULL` additions on existing data columns —
all require a production backup and a rollback plan before execution.

### 15.3 — Column Additions Must Be Nullable or Have a Default
Adding a column to a table with existing rows requires either `nullable=True`
or a server-side `default`. Adding `NOT NULL` without a default will fail on
any non-empty database.

### 15.4 — `init_db()` Is Not a Migration Tool
`database.py:init_db()` calls `Base.metadata.create_all()`. This creates
missing tables but does NOT alter existing ones. Do not rely on it to apply
schema changes to an existing database.

---

## 16. Security Rules

### 16.1 — SECURITY: Disable Passwordless in Production
`ALLOW_PASSWORDLESS` must be `False` in any production deployment.
The current default of `True` allows anyone to create an account with any
email address without a password. Verify this before any public deployment.

### 16.2 — SECURITY: Replace JWT Secret
`JWT_SECRET` defaults to `"dev-secret-change-me"`. A JWT signed with this
known default can be forged by any attacker who knows the default. Add a
runtime production check in `config.py`.

### 16.3 — SECURITY: Never Commit .env
`.env` is in `.gitignore`. Never add API keys, JWT secrets, or database
passwords to any tracked file. Verify `.gitignore` before any commit.

### 16.4 — SECURITY: Validate Account Ownership on Every Access
Every endpoint that touches an account or transaction must filter by user:
```python
.filter(Model.user_id == current.id)
```
Never trust a client-supplied ID without this check.

### 16.5 — SECURITY: No SQL Injection
Never use Python string formatting to build SQL queries. Use SQLAlchemy ORM
or parameterized `.filter()` calls only.

**NEVER DO THIS:**
```python
db.execute(f"SELECT * FROM transactions WHERE merchant = '{merchant}'")
```

### 16.6 — SECURITY: File Upload Validation
Uploaded files are validated by content-type and size:
- Receipts: 25 MB max
- Cartola PDFs: 30 MB max

Do not remove these limits.

### 16.7 — SECURITY: Email Token Is Secret
Each user's `email_token` is their inbound webhook identifier. It must not be
exposed in list endpoints or logs. It appears only in `UserOut.email_token`
and the `/email/forwarding-address` endpoint.

### 16.8 — SECURITY: CORS Must Be Explicit in Production
`CORS_ORIGINS` defaults to `http://localhost:3000`. In production, set it to
the exact frontend domain. Never use `*` as a CORS origin in production.

---

## 17. Docker/Infrastructure Rules

### 17.1 — Frontend Docker Runs Dev Server
The `docker-compose.yml` frontend service runs `npm run dev`. This is not
production-hardened. For production, replace with:
```yaml
command: sh -c "npm install && npm run build && npm start"
```

### 17.2 — Container Networking for Frontend
`NEXT_PUBLIC_API_URL=http://localhost:8000` is wrong inside Docker containers —
`localhost` resolves to the frontend container itself, not the backend.
The correct value inside Docker Compose is:
```yaml
NEXT_PUBLIC_API_URL: http://backend:8000
```
Or use a reverse proxy on a shared network port.

### 17.3 — Never Volume-Mount Backend in Production
The development compose file mounts `./backend:/app`, exposing `.env` to the
container layer. In production, use build-time `COPY` and environment variables.

### 17.4 — Uploads Volume Must Persist
`lucas_uploads` stores all user-uploaded receipt images. Never run
`docker compose down -v` without understanding that this destroys all uploads.

### 17.5 — Postgres 16 Is the Target Version
The `db` service specifies `postgres:16`. Do not downgrade. SQLAlchemy
expressions and query behavior are tested against this version.

---

## 18. Type Consistency Rules

### 18.1 — `amount` Is Always Positive Float
`Transaction.amount` is always `> 0`. Direction is `is_income: bool`.
Never store a negative amount.

### 18.2 — `currency` Is Always Uppercase 3-Letter ISO
`"CLP"`, `"USD"`, `"BRL"`, etc. Enforce with `.upper()` at write time.
See `routers/cartola.py:commit_cartola()` for the reference pattern.

### 18.3 — Dates Are Python `date`, Not `datetime`
`Transaction.date` and `Account.anchor_date` are `date` objects (no time).
`Transaction.created_at` and `Account.created_at` are `datetime`. Never mix
them in comparisons or arithmetic.

### 18.4 — `split_type` Values Are Constrained
Valid values: `"equal"`, `"percent"`, `"amount"` (from `schemas.SPLIT_TYPES`).
Validate before writing to `ItemAssignment.split_type`.

### 18.5 — `account.type` Values Are Constrained
Valid values: `"debit"`, `"credit"`, `"savings"`, `"wallet"`, `"cash"`
(from `schemas.ACCOUNT_TYPES`). Validate before writing.

### 18.6 — Category Must Be One of DEFAULT_CATEGORIES
When writing a category to the database, it should be one of the 19 values
defined in `ai/categorizer.py:DEFAULT_CATEGORIES`. Off-list values will display
but will not match keyword rules or LLM prompts.

---

## 19. Graphify Integration Rules

### 19.1 — MASTER_PLAN Is the Knowledge Root
`docs/MASTER_PLAN.md` is the primary document for Graphify indexing.
When running `/graphify` on this repository, MASTER_PLAN anchors the knowledge
graph. AI_RULES defines behavioral constraints layered on top of it.

### 19.2 — Mark Unverified Facts in Documentation
Any section or statement added to `docs/` that is not directly derived from
source code must be marked `[NOT VERIFIED]`. This prevents Graphify from
treating speculative content as verified architecture.

### 19.3 — Keep Docs in Sync with Code
After any significant code change, update the relevant section of
`docs/MASTER_PLAN.md`. The Stability Status table (§20) must be updated
when bugs are fixed or features land.

### 19.4 — This File Is a Constraint Document, Not a Feature Document
`AI_RULES.md` defines what agents must NOT do. `MASTER_PLAN.md` describes what
the system does. Do not add feature descriptions here.

---

## 20. Rules for Future AI Agents

### 20.1 — Read MASTER_PLAN First
Before writing any code in any session on this repository, read
`docs/MASTER_PLAN.md` in its entirety. It contains confirmed architecture,
known bugs, and non-goals. Ignoring it leads to duplicate work and regressions.

### 20.2 — Read AI_RULES Second
This file is a mandatory constraint set. It is not optional reading.

### 20.3 — Do Not Hallucinate System State
If you have not verified that a bug is fixed, do not say it is fixed.
If you have not read a file, do not describe its contents.
If you have not run a command, do not describe its output.

### 20.4 — Confirmed Bugs Must Be Fixed Before Feature Extension
Before adding functionality to any of these modules, fix the known bug first:
- `ai/voice.py` → fix `resp.content` → `resp.text` (lines 141, 158)
- `cartola.py` → fix `resp.content` → `resp.text` (line 158)
- `requirements.txt` → add `pdf2image`

### 20.5 — The Three Core Invariants Are Non-Negotiable
These three behaviors must be preserved in all code, forever:
1. **Never double-count.** Transfers excluded from dashboard totals. Dedupe before every external save.
2. **Never write computed balances.** Live balance is always computed on read from anchor + transactions.
3. **Never auto-save unconfirmed data.** Voice, email, cartola imports → review queue first.

### 20.6 — Ask Before Destructive Actions
Any operation that could cause data loss, schema change, or endpoint removal
must be confirmed with the user before execution:
- Dropping database columns or tables
- Removing API endpoints (even deprecated ones)
- Changing JWT secret or token structure
- Modifying the deduplication algorithm
- Changing balance computation logic
- Changing transfer-link tolerance values

### 20.7 — Do Not Invent Endpoints
Do not add a new API endpoint unless the user explicitly requests it.
Absent endpoints may be intentionally absent.

### 20.8 — Do Not Break the Category Contract
The 19 Spanish category names are stored in user data. Any renaming requires
a data migration. Treat them as a versioned API.

### 20.9 — Preserve the Provider Abstraction
The ability to swap AI providers (OpenAI / Anthropic / Gemini) by changing a
single env var is a core architectural property. No change may break this.

### 20.10 — Leave the Repository Better Than You Found It
Each session must either fix a known bug, add a tested feature, or improve
documentation. It must not add new bugs, new technical debt, or new
unverified assumptions to the codebase.

---

## APPENDIX A — Files That Must Not Be Modified Lightly

| File | Why careful editing is required |
|------|--------------------------------|
| `backend/app/models.py` | Schema change = migration required |
| `backend/app/schemas.py` | API contract — frontend TypeScript depends on it |
| `backend/app/ai/provider.py` | Provider abstraction — core architectural property |
| `backend/app/services/accounts.py` | Balance + transfer logic — financial invariants |
| `backend/app/services/dedupe.py` | Deduplication — anti-double-count invariant |
| `backend/app/ai/categorizer.py` | Category names are stored in user data |
| `frontend/src/lib/api.ts` | All frontend-to-backend contract |
| `docker-compose.yml` | Infrastructure — affects running system |
| `backend/.env` | Secrets — never commit |

---

## APPENDIX B — Confirmed Bugs (as of 2026-05-11)

| # | Bug | File | Line(s) | Fix |
|---|-----|------|---------|-----|
| 1 | `resp.content` → AttributeError | `ai/voice.py` | 141, 158 | Change to `resp.text` |
| 2 | `resp.content` → AttributeError | `cartola.py` | 158 | Change to `resp.text` |
| 3 | `pdf2image` missing from requirements | `requirements.txt` | — | Add `pdf2image>=1.33.3` |
| 4 | Voice uses English categories | `ai/voice.py` | ~43 | Align to Spanish DEFAULT_CATEGORIES |
| 5 | Docker frontend localhost URL broken | `docker-compose.yml` | ~42 | Change to `http://backend:8000` |
| 6 | JWT secret defaults to known value | `config.py` | 14 | Enforce non-default in production |
| 7 | Passwordless enabled by default | `config.py` | ~42 | Default to `False` |

Fixing bugs 1 and 2 will immediately restore voice and cartola text-parsing
functionality. These are the highest-priority fixes in the entire codebase.

---

## APPENDIX C — The Minimum Checklist for Any Code Change

Before marking any task complete, verify all of the following:

- [ ] All modified files were read before editing
- [ ] `python -c "from app.main import app"` passes (backend)
- [ ] `npx tsc --noEmit` passes (frontend, if changed)
- [ ] No new `os.getenv()` calls introduced
- [ ] No direct `openai`/`anthropic`/`google.generativeai` imports introduced
- [ ] No `resp.content` anywhere in modified files (use `resp.text`)
- [ ] Any new transaction creation calls `dedupe.find_duplicate()` + `reconcile_new_transaction()`
- [ ] No computed balance written to `accounts` table
- [ ] No transfer set as one-sided only
- [ ] No auto-save of unconfirmed (email/cartola/voice) transactions
- [ ] No new `NOT NULL` column without a default or migration
- [ ] `ALLOW_PASSWORDLESS` and `JWT_SECRET` defaults not weakened

---

*This document is law for all AI agents working on LUCAS.*
*When in doubt: read the source, preserve the invariants, ask the user.*
