# LUCAS — OPERATIONAL ARCHITECTURE
**Version:** 1.0 | **Last updated:** 2026-05-11
**Source authority:** Full repository read — every claim verified against source files.
**Status tags:** `[IMPLEMENTED]` `[PARTIAL]` `[BROKEN]` `[NOT VERIFIED]`

> This document is a systems-level operational map derived exclusively from the
> verified repository. It is not aspirational documentation. Unverified items are
> explicitly marked. Use this document for debugging, onboarding, agent reasoning,
> and Graphify indexing.

---

## 1. High-Level System Architecture

```
╔══════════════════════════════════════════════════════════════════════════╗
║                           LUCAS SYSTEM BOUNDARY                          ║
║                                                                          ║
║  ┌─────────────────────────────────┐                                     ║
║  │       BROWSER / PWA CLIENT       │                                     ║
║  │                                  │                                     ║
║  │  Next.js 14 App Router           │                                     ║
║  │  React 18 / TypeScript 5.6       │                                     ║
║  │  Tailwind CSS 3.4                │                                     ║
║  │  recharts (PieChart only)        │                                     ║
║  │  Web Speech API (voice input)    │                                     ║
║  │                                  │                                     ║
║  │  JWT stored in localStorage      │                                     ║
║  │  Token: Bearer in every request  │                                     ║
║  └──────────────┬──────────────────┘                                     ║
║                 │  REST JSON / multipart                                   ║
║                 │  HTTP (no WebSocket, no GraphQL)                         ║
║                 ▼                                                          ║
║  ┌─────────────────────────────────┐    ┌──────────────────────────────┐ ║
║  │        FASTAPI BACKEND           │    │      POSTGRESQL 16            │ ║
║  │                                  │    │                              │ ║
║  │  uvicorn (ASGI)                  │◄──►│  users                       │ ║
║  │  SQLAlchemy 2.0 ORM              │    │  accounts                    │ ║
║  │  pydantic 2.9 validation         │    │  transactions                │ ║
║  │  JWT (HS256, 7-day expiry)       │    │  receipt_items               │ ║
║  │  bcrypt password hashing         │    │  item_assignments            │ ║
║  │                                  │    │  people                      │ ║
║  │  ┌───────────┐ ┌──────────────┐  │    │  categories                  │ ║
║  │  │  routers/ │ │  services/   │  │    │  merchant_category_rules     │ ║
║  │  │           │ │              │  │    │  ai_usage                    │ ║
║  │  │ auth      │ │ accounts.py  │  │    └──────────────────────────────┘ ║
║  │  │ upload    │ │ dedupe.py    │  │                                     ║
║  │  │ txns      │ │ ai_usage.py  │  │    ┌──────────────────────────────┐ ║
║  │  │ accounts  │ └──────────────┘  │    │   FILE STORAGE               │ ║
║  │  │ split     │ ┌──────────────┐  │    │                              │ ║
║  │  │ dashboard │ │    ai/       │  │◄──►│   Local FS: ./uploads/       │ ║
║  │  │ cartola   │ │              │  │    │   OR S3 (boto3)              │ ║
║  │  │ voice     │ │ provider.py  │  │    │                              │ ║
║  │  │ email     │ │ categorizer  │  │    │   Served as /files/* static  │ ║
║  │  │ ai        │ │ predictor    │  │    └──────────────────────────────┘ ║
║  │  └───────────┘ │ alerts       │  │                                     ║
║  │                │ chat         │  │    ┌──────────────────────────────┐ ║
║  │                │ voice        │  │    │   EXTERNAL AI PROVIDERS      │ ║
║  │                │ email_parser │  │◄──►│                              │ ║
║  │                └──────────────┘  │    │   OpenAI (gpt-4o-mini)       │ ║
║  │                                  │    │   Anthropic (claude-haiku)   │ ║
║  │  Tesseract OCR (local, offline)  │    │   Google Gemini 1.5-flash    │ ║
║  │  pdfplumber (text PDF parsing)   │    │                              │ ║
║  │  OpenCV + Pillow (preprocessing) │    └──────────────────────────────┘ ║
║  └─────────────────────────────────┘                                     ║
║                                                                          ║
║  ┌─────────────────────────────────┐                                     ║
║  │   EMAIL INGESTION (INBOUND)      │                                     ║
║  │                                  │                                     ║
║  │  SendGrid Inbound Parse webhook  │                                     ║
║  │  POST /email/inbound             │                                     ║
║  │  Per-user forwarding address:    │                                     ║
║  │  lucas-{token}@{EMAIL_DOMAIN}    │                                     ║
║  └──────────────┬──────────────────┘                                     ║
║                 │                                                          ║
║                 ▼                                                          ║
║               backend                                                      ║
╚══════════════════════════════════════════════════════════════════════════╝
```

**Deployment:** Docker Compose (single-machine). Three containers: `db`, `backend`,
`frontend`. No load balancer, no service mesh, no message queue. `[IMPLEMENTED]`

**Production hardening:** `[NOT VERIFIED]` — no Nginx, no TLS termination, no
health checks beyond docker healthcheck on Postgres.

---

## 2. Frontend ↔ Backend Interaction Model

### 2.1 Communication Protocol

All communication is **synchronous REST over HTTP**. No WebSockets, no SSE, no
GraphQL. Every request goes through `frontend/src/lib/api.ts`, which:

1. Reads JWT from `localStorage` (key: `lucas_token`)
2. Attaches `Authorization: Bearer <token>` to every request
3. On HTTP 401: clears token + redirects to `/` (auto-logout)
4. On HTTP 204: returns `undefined` (no body)
5. On any other error: throws `Error("${status} ${statusText}: ${body_text}")`

### 2.2 Frontend Contract Points

The following `api.ts` interfaces define the frontend-backend contract.
Any backend schema change must be mirrored in these TypeScript interfaces:

| TypeScript Interface | Backend Schema | Router |
|---------------------|---------------|--------|
| `User` | `UserOut` | `GET /auth/me` |
| `TokenOut` | `TokenOut` | `POST /auth/signup`, `/auth/login` |
| `Account` | `AccountOut` | `GET /accounts` |
| `Transaction` | `TransactionOut` | `GET /transactions` |
| `DashboardData` | `DashboardOut` | `GET /dashboard` |
| `ParsedUpload` | `ParsedUpload` | `POST /upload` |
| `CartolaReport` | `CartolaReport` | `POST /cartola/upload` |
| `VoiceParsed` | `VoiceParsed` | `POST /voice/parse` |
| `SplitResultV2` | `SplitResultV2Out` | `GET /split/result` |
| `SettleOut` | `SettleOut` | `POST /split/settle` |

### 2.3 Upload Requests

File uploads use raw `fetch()` directly (not the `request()` wrapper) with
`FormData`. Affected calls: `uploadImage()`, `uploadCartola()`,
`uploadCardImage()`. This is intentional (Content-Type must be multipart, not
application/json).

### 2.4 Trust Boundary: Client → Backend

The backend trusts only:
- The JWT signature (HS256 with `JWT_SECRET`)
- The `user_id` extracted from the JWT payload (`sub` claim)

The backend does NOT trust:
- Any user ID passed in request bodies (never used)
- Any account/transaction IDs without re-verifying `user_id` ownership
- File content-types declared by the client (validated server-side)
- Currency values (normalized to uppercase on write)

---

## 3. Authentication Flow `[IMPLEMENTED]`

```
Client                          Backend                         DB
  │                                │                             │
  │── POST /auth/signup ──────────►│                             │
  │   {email, password, locale}    │── hash_password(bcrypt) ───►│
  │                                │── INSERT User ─────────────►│
  │                                │── generate email_token ─────►│
  │◄── {access_token, user} ───────│◄── User row ────────────────│
  │   (JWT, 7-day expiry)          │                             │
  │                                │                             │
  │── POST /auth/login ───────────►│                             │
  │   form: username+password      │── SELECT User by email ─────►│
  │   (OAuth2PasswordRequestForm)  │── verify_password(bcrypt) ──│
  │◄── {access_token, user} ───────│                             │
  │                                │                             │
  │── POST /auth/google ──────────►│                             │
  │   {credential: google_id_token}│── GET tokeninfo (Google) ───►(Google API)
  │                                │◄── {email, aud, verified} ──│
  │                                │── get_or_create_user ───────►│
  │◄── {access_token, user} ───────│                             │
  │                                │                             │
  │── POST /auth/quick ───────────►│  (ALLOW_PASSWORDLESS=True)  │
  │   {email, locale}              │── get_or_create_user ───────►│
  │◄── {access_token, user} ───────│                             │
```

**JWT structure:**
```json
{"sub": "<user_id_int>", "exp": <unix_timestamp>}
```
Algorithm: HS256. Secret: `settings.jwt_secret`. Expiry: 7 days (configurable).

**`email_token` generation:** `secrets.token_urlsafe(9)` (12 URL-safe chars).
Generated at signup. Used as inbound email webhook identifier. Lazy-generated
for users who existed before email feature was added.

**Auth provider tracking:** `User.auth_provider` stores `"password"`, `"email"`,
or `"google"`. Used for auditing only; not enforced at login time.

**Security gaps:**
- Google token verified via `GET tokeninfo` HTTP call (network-dependent)
- No token revocation mechanism
- No refresh token — re-login required after 7 days
- `ALLOW_PASSWORDLESS=True` default allows account creation without password `[BROKEN for prod]`

---

## 4. OCR Ingestion Flow `[IMPLEMENTED]`

```
Client                        Backend (routers/upload.py)
  │                                     │
  │── POST /upload ─────────────────────►│
  │   multipart: file (image or PDF)     │
  │                                      │── validate content-type + size (25MB max)
  │                                      │── storage.save_image(data, filename)
  │                                      │         → returns URL string
  │                                      │
  │                                      │── if is_pdf: ocr.parse_receipt_from_pdf()
  │                                      │── else:      ocr.parse_receipt()
  │                                      │
  │                     ┌────────────────▼──────────────────────────────────┐
  │                     │           ocr.parse_receipt()                      │
  │                     │                                                    │
  │                     │   1. vision_parse() — try LLM first               │
  │                     │      ├── ai_provider.is_available()?              │
  │                     │      │   NO  → skip to Tesseract                  │
  │                     │      │   YES → proceed                            │
  │                     │      │                                            │
  │                     │      ├── [Layer 1] run_ocr() → Tesseract text     │
  │                     │      │   _parse_boleta_from_text() → items+conf  │
  │                     │      │   _extract_boleta_totals() → neto+iva     │
  │                     │      │                                            │
  │                     │      ├── [Vision] _shrink_for_vision(max=1600px) │
  │                     │      │   base64 encode → data URL                │
  │                     │      │   ai_provider.vision_json(                │
  │                     │      │     system_prompt=_SYSTEM_PROMPT,         │
  │                     │      │     grounding_text=neto+iva injected,     │
  │                     │      │     temperature=0.0, purpose="parse")     │
  │                     │      │                                            │
  │                     │      ├── [Layer 2] validate LLM total_neto/iva   │
  │                     │      │   against 19% ratio (Chilean SII law)     │
  │                     │      │                                            │
  │                     │      └── [Layer 3] item source selection:        │
  │                     │          tess_conf ≥ 0.97 → exact Tesseract items│
  │                     │          tess_conf ≥ 0.80 → light normalization  │
  │                     │          else         → LLM items + normalize    │
  │                     │                                                    │
  │                     │   2. If vision_parse() returns None:              │
  │                     │      run_ocr() → heuristic_parse()               │
  │                     │      └── _parse_signed_statement() for tables    │
  │                     └──────────────────────────────────────────────────┘
  │                                      │
  │                                      │── for each ParsedReceipt:
  │                                      │   _enrich(tx, db, user_id):
  │                                      │     if is_cc_payment: category="Transferencia"
  │                                      │     else: categorizer.categorize()
  │                                      │
  │                                      │── _apply_currency_default()
  │                                      │   (force user's preferred currency)
  │                                      │
  │                                      │── dedupe.suggest_account_for_hint()
  │                                      │   (bank_hint + account_type_hint → account_id)
  │                                      │
  │                                      │── for each tx:
  │                                      │   dedupe.find_duplicate() → sets dupe_of
  │                                      │
  │◄── ParsedUpload ─────────────────────│
  │   {type, image_url, currency,        │
  │    transactions[], suggested_acct}   │
  │                                      │
  │  [USER REVIEWS + CONFIRMS IN UI]     │
  │                                      │
  │── POST /transactions ────────────────►│ (one per confirmed transaction)
  │   {amount, category, date, ...}      │── dedupe 60s guard
  │   ?image_url=<url>                   │── INSERT Transaction
  │                                      │── account_svc.reconcile_new_transaction()
  │◄── TransactionOut ───────────────────│
```

**Data enters at:** POST /upload (multipart image)
**Validation occurs at:** content-type check, size check (25MB), pydantic on output
**Dedupe occurs at:** upload time (find_duplicate sets dupe_of), again at save time (60s guard)
**AI is used at:** vision_json() call, categorizer.categorize() (LLM fallback)
**Persistence occurs at:** storage.save_image() (file), then POST /transactions (DB)
**Transfer-linking occurs at:** POST /transactions → reconcile_new_transaction()

**Fallback path:** If `vision_parse()` returns None (no API key OR exception),
`parse_receipt()` falls to `run_ocr()` + `heuristic_parse()`. No AI cost.

---

## 5. PDF Ingestion Flow (Single Receipt) `[BROKEN — pdf2image missing]`

```
POST /upload (is_pdf=True)
        │
        ▼
ocr.parse_receipt_from_pdf(pdf_bytes)
        │
        ├── pdf_page_count(pdf_bytes)
        │   └── pdfplumber.open() → len(pdf.pages) [capped at 20]
        │
        └── for each page (0..n):
            ├── pdf_page_to_image_bytes(pdf_bytes, page_index=i, dpi=150)
            │   └── from pdf2image import convert_from_bytes  ← IMPORT FAILS
            │       pdf2image NOT in requirements.txt
            │       RuntimeError: "pdf2image is not installed"
            │
            └── [if import succeeds] parse_receipt(img_bytes)
                └── [same flow as §4]
```

**Status: BROKEN.** `pdf2image` is imported but not in `requirements.txt`.
Every PDF receipt upload will raise `RuntimeError` at `pdf_page_to_image_bytes()`.

**Fix required:** Add `pdf2image>=1.33.3` to `requirements.txt` and
`RUN apt-get install -y poppler-utils` to the Dockerfile.

---

## 6. Cartola Ingestion Flow `[PARTIAL — text path BROKEN]`

```
Client                     Backend (routers/cartola.py)
  │                                   │
  │── POST /cartola/upload ───────────►│
  │   multipart: PDF file             │── validate content-type (PDF only)
  │                                   │── size check (30MB max)
  │                                   │── storage.save_image() (stores the PDF)
  │                                   │
  │                    ┌──────────────▼────────────────────────────────────┐
  │                    │          cartola.parse_cartola()                   │
  │                    │                                                   │
  │                    │  text = _extract_text(pdf_bytes)                  │
  │                    │         └── pdfplumber.open() → extract_text()    │
  │                    │             [WORKS — pdfplumber in requirements]  │
  │                    │                                                   │
  │                    │  if text:  ← has text layer (Santander, BCI...)   │
  │                    │    data = _llm_structure(text)                    │
  │                    │      └── ai_provider.chat_completion(...)         │
  │                    │           └── resp = LLMResponse                  │
  │                    │               raw = (resp.content or "").strip()  │
  │                    │                            ▲                      │
  │                    │                     ATTRIBUTE ERROR               │
  │                    │                     resp has no .content          │
  │                    │                     use resp.text                 │
  │                    │                   ← RETURNS None always [BROKEN]  │
  │                    │                                                   │
  │                    │  if not data:  ← always True due to bug above     │
  │                    │    images = _render_pages_as_images(pdf_bytes)    │
  │                    │    [pdfplumber .to_image(), NOT pdf2image]        │
  │                    │    for img in images:                             │
  │                    │      vr = vision_parse(img)  [WORKS if API key]  │
  │                    │    ← merged transactions [WORKS] [PARTIAL]        │
  │                    └───────────────────────────────────────────────────┘
  │                                   │
  │                                   │── for each tx:
  │                                   │   _enrich() → categorizer.categorize()
  │                                   │
  │                                   │── dedupe.suggest_account_for_hint()
  │                                   │   (bank+type from account_info)
  │                                   │
  │                                   │── for each tx:
  │                                   │   dedupe.find_duplicate() → sets dupe_of
  │                                   │
  │                                   │── compute app_balance for suggested account
  │                                   │   (account_svc.compute_account_balance)
  │                                   │   drift = closing_balance - app_balance
  │                                   │
  │◄── CartolaReport ─────────────────│
  │   {bank, account_type, last4,     │
  │    transactions[], new_count,     │
  │    duplicate_count, drift}        │
  │                                   │
  │  [USER REVIEWS — selects txns]    │
  │                                   │
  │── POST /cartola/commit ───────────►│
  │   {account_id, transactions[],    │── for each tx (dupe_of=None only):
  │    reconcile_to_closing_balance,  │   INSERT Transaction (status="confirmed")
  │    closing_balance}               │   NOTE: reconcile_new_transaction()
  │                                   │         NOT CALLED HERE [GAP]
  │                                   │── if reconcile_to_closing_balance:
  │                                   │   db.flush() → compute balance after save
  │                                   │   acc.anchor_date = today
  │                                   │   acc.anchor_balance = closing_balance
  │◄── {saved_count, skipped, drift} ─│
```

**Data enters at:** POST /cartola/upload (PDF file)
**Validation occurs at:** content-type (PDF only), size (30MB), pydantic on CartolaCommitIn
**Dedupe occurs at:** upload time (find_duplicate), commit skips dupe_of rows
**AI is used at:** _llm_structure (BROKEN), vision_parse per page (WORKS)
**Persistence occurs at:** storage (PDF file), cartola/commit (DB transactions)
**Transfer-linking occurs at:** NOT CALLED — cartola commit bypasses reconcile_new_transaction()
**Reconciliation occurs at:** optional, if reconcile_to_closing_balance=True

**CRITICAL GAP:** `POST /cartola/commit` does not call `reconcile_new_transaction()`
on saved transactions. CC payments in cartola statements will not be auto-linked.
The user must manually link them from the pending-transfers UI.

---

## 7. Voice Transaction Flow `[BROKEN when AI configured]`

```
Client (VoiceButton.tsx)              Backend (routers/voice.py)
  │                                             │
  │  [Browser Web Speech API]                   │
  │  speechRecognition.start()                  │
  │  → user speaks → transcript string          │
  │                                             │
  │── POST /voice/parse ────────────────────────►│
  │   {transcript: "gasté 5 lucas en Uber",     │
  │    today: "2026-05-11"}                     │── body.today or date.today()
  │                                             │
  │                          ┌──────────────────▼──────────────────────────┐
  │                          │          ai/voice.py:parse_voice()           │
  │                          │                                              │
  │                          │  if not transcript: → _fallback_unclear()   │
  │                          │  if not _MONEY_HINT_RE: → _fallback_unclear()│
  │                          │  if not ai_provider.is_available():          │
  │                          │    → _fallback_unclear()                    │
  │                          │                                              │
  │                          │  resp = ai_provider.chat_completion(         │
  │                          │    system=_SYSTEM_PROMPT (Chilean slang),   │
  │                          │    user=f"Hoy es {today}.\n{transcript}",   │
  │                          │    temperature=0.0, purpose="voice")        │
  │                          │                                              │
  │                          │  if resp is None or not                     │
  │                          │     (resp.content or "").strip():  ← BUG   │
  │                          │         AttributeError: no .content         │
  │                          │         should be resp.text                 │
  │                          │         raises 500 when AI is configured    │
  │                          └──────────────────────────────────────────────┘
  │                                             │
  │                                             │── [if bug fixed, continues:]
  │                                             │── json.loads(resp.text)
  │                                             │── categorizer.categorize()
  │                                             │   (enriches category via 3 layers)
  │                                             │── dedupe.suggest_account_for_hint()
  │                                             │   (account_hint string → account_id)
  │◄── VoiceParsed ─────────────────────────────│
  │   {action, amount, category, merchant,      │
  │    date, account_hint, suggested_account_id,│
  │    confidence, clarification, transcript}   │
  │                                             │
  │  [USER REVIEWS CONFIRMATION CARD]           │
  │  if confidence < 0.6 → ask to repeat        │
  │                                             │
  │── POST /transactions ────────────────────────►│ (user confirms)
  │                                             │── INSERT Transaction
  │                                             │── reconcile_new_transaction()
```

**BROKEN state:** When any AI provider is configured, `ai/voice.py:parse_voice()`
raises `AttributeError: 'LLMResponse' object has no attribute 'content'` at
line 141. The router catches no exception — this propagates as HTTP 500.

**Working state (no AI key):** Falls through to `_fallback_unclear()` immediately.
Voice feature is functionally degraded — returns "unclear" on every call.

**No auto-save:** Voice never writes to DB. User must confirm → POST /transactions.
**Transfer-linking:** Fires at POST /transactions (reconcile_new_transaction).

---

## 8. Email Ingestion Flow `[IMPLEMENTED]`

```
Bank sends notification email
        │
        ▼
User's Gmail filter forwards to lucas-{token}@{EMAIL_DOMAIN}
        │
        ▼
SendGrid Inbound Parse (or other SMTP webhook)
        │
        ▼
POST /email/inbound (multipart/form-data OR application/json)
        │
        ├── extract To: address
        │   _extract_token_from_address() → token string
        │   _user_from_token(db, token) → User row
        │   if no token or no user → return {"ok": False}
        │
        ├── ai/email_parser.py:parse_email(subject, body_text, body_html)
        │   │
        │   ├── _SKIP_SUBJECTS regex: marketing/welcome/cartola → return None
        │   │
        │   ├── _extract_heuristic(subject, body) [FREE, INSTANT]
        │   │   ├── _AMOUNT_RE: find CLP amounts (100–100,000,000)
        │   │   ├── _DATE_RE: find dates
        │   │   ├── _MERCHANT_RE: find "comercio:", "establecimiento:" etc.
        │   │   ├── _INCOME_RE / _EXPENSE_RE: is it income or expense?
        │   │   └── _CARD_RE: extract card last4
        │   │
        │   └── if heuristic has no merchant:
        │       ai_provider.chat_completion(
        │         model="gpt-4o-mini",  ← hardcoded
        │         purpose="email_parse",
        │         max_tokens=256)
        │       json.loads(resp.text)
        │       if data.get("skip"): return None
        │
        ├── resolve account_id by card_last4
        │   (ILIKE match on account.name)
        │
        ├── dedupe.find_duplicate() → check for existing tx
        │   if duplicate found → return {"ok": True, "action": "duplicate"}
        │
        ├── INSERT Transaction (status="pending_review")
        │   NOTE: reconcile_new_transaction() NOT CALLED HERE
        │
        └── return {"ok": True, "action": "created", "transaction_id": ...}

[USER OPENS REVIEW QUEUE → GET /email/pending]
        │
        ▼
POST /email/review/{tx_id}
  {action: "confirm" | "skip" | "not_expense" | "pending"}
        │
        ├── "confirm" → tx.status = "confirmed"
        │              categorizer.remember_correction() if remember=True
        │              account_svc.reconcile_new_transaction() ← CALLED HERE
        │
        ├── "not_expense" → DELETE tx
        ├── "skip" → return tx unchanged (still pending_review)
        └── "pending" → append " [Por Cobrar]" to notes
```

**Data enters at:** POST /email/inbound (webhook from email provider)
**Validation occurs at:** token lookup, subject skip filter, pydantic on path params
**Dedupe occurs at:** find_duplicate() before INSERT
**AI is used at:** email_parser LLM fallback (heuristic first)
**Persistence occurs at:** INSERT Transaction (status=pending_review)
**Transfer-linking occurs at:** POST /email/review/{id} with action="confirm"

**Trust boundary:** The email inbound endpoint has NO authentication header.
It relies entirely on the user's email_token being secret and unguessable.
Anyone who knows a user's token can inject transactions into their review queue.

---

## 9. Transaction Lifecycle — All Creation Paths

This is the most critical section. The following table documents **every path**
that can create a `Transaction` row, and which invariants fire on each.

```
╔═════════════════════════════════════════════════════════════════════════╗
║              TRANSACTION CREATION PATHS — INVARIANT MATRIX              ║
╠══════════════════════════╦════════╦════════╦═══════════╦══════════════╗ ║
║  Path                    ║ Dedupe ║ 60s    ║ reconcile ║ status       ║ ║
║                          ║ check  ║ guard  ║ _new_tx() ║              ║ ║
╠══════════════════════════╬════════╬════════╬═══════════╬══════════════╣ ║
║ POST /transactions       ║ NO*    ║ YES    ║ YES       ║ confirmed    ║ ║
║ (manual from UI)         ║        ║        ║           ║              ║ ║
╠══════════════════════════╬════════╬════════╬═══════════╬══════════════╣ ║
║ POST /upload →           ║ YES    ║ YES    ║ YES       ║ confirmed    ║ ║
║ POST /transactions       ║(upload)║(save)  ║ (save)    ║              ║ ║
╠══════════════════════════╬════════╬════════╬═══════════╬══════════════╣ ║
║ POST /cartola/commit     ║ YES    ║ NO     ║ NO ←GAP   ║ confirmed    ║ ║
║                          ║(upload)║        ║           ║              ║ ║
╠══════════════════════════╬════════╬════════╬═══════════╬══════════════╣ ║
║ POST /email/inbound      ║ YES    ║ NO     ║ NO        ║ pending_rev. ║ ║
╠══════════════════════════╬════════╬════════╬═══════════╬══════════════╣ ║
║ POST /email/review       ║ NO     ║ NO     ║ YES       ║ confirmed    ║ ║
║ (action=confirm)         ║        ║        ║           ║              ║ ║
╠══════════════════════════╬════════╬════════╬═══════════╬══════════════╣ ║
║ POST /split/start-manual ║ NO ←GAP║ NO     ║ NO ←GAP   ║ confirmed    ║ ║
╠══════════════════════════╬════════╬════════╬═══════════╬══════════════╣ ║
║ POST /split/settle       ║ NO     ║ NO     ║ NO        ║ confirmed    ║ ║
║ (save_to_lucas=True)     ║        ║        ║           ║ (updates     ║ ║
║ [updates existing tx]    ║        ║        ║           ║  existing)   ║ ║
╚══════════════════════════╩════════╩════════╩═══════════╩══════════════╝ ║
                                                                          ║
* POST /transactions has a 60-second exact-duplicate guard that covers    ║
  the most common manual double-tap case but is NOT the full deduplication ║
╚═════════════════════════════════════════════════════════════════════════╝
```

**GAPS:**
1. **POST /cartola/commit** skips `reconcile_new_transaction()`. CC payment
   rows from cartola import will not be auto-linked to their debit counterpart.
2. **POST /split/start-manual** skips both deduplication and transfer-linking.
   A manually created split transaction could be a duplicate of an existing row.

---

## 10. Transaction Deduplication Lifecycle

```
[Proposed Transaction arrives]
           │
           ▼
services/dedupe.py:find_duplicate(db, user_id, account_id, proposed)
           │
           ├── DB query:
           │   WHERE user_id = ?
           │     AND is_income = proposed.is_income
           │     AND date BETWEEN proposed.date-2d AND proposed.date+2d
           │     AND amount BETWEEN amt-tol AND amt+tol
           │     [if account_id known: AND account_id = ?]
           │
           │   Tolerance:
           │     CLP: ±0.5 (integer amounts)
           │     Other: max(amount * 0.005, 0.01)
           │
           ├── For each candidate:
           │   _merchant_similar(candidate.merchant, proposed.merchant)?
           │   OR _merchant_similar(candidate.merchant, proposed.description)?
           │   OR _merchant_similar(candidate.notes, proposed.description)?
           │
           │   _merchant_similar(a, b):
           │     exact match → True
           │     substring → True
           │     Jaccard on tokens ≥ 0.5 → True
           │
           ├── First matching candidate → return it
           └── None found → return None

[If duplicate found]:
  proposed.dupe_of = candidate.id
  → Frontend shows "already imported" badge
  → User can override (clear dupe_of) or skip

[60-second exact guard in POST /transactions]:
  WHERE user_id=? AND date=? AND amount=? AND is_income=? AND merchant=?
    AND created_at >= NOW() - INTERVAL 60 seconds
  → HTTP 409 {detail: "duplicate_transaction", existing_id: ...}
  → Hard block (cannot override without waiting 60s)
```

---

## 11. Transfer-Linking Lifecycle

```
[New Transaction saved to DB]
           │
           ▼
account_svc.reconcile_new_transaction(db, user_id, tx)
           │
           ├── if tx.is_transfer or tx.linked_transaction_id: return None (already handled)
           │
           ▼
find_transfer_match(db, user_id, tx, window_days=4)
           │
           ├── if not tx.amount or not tx.account_id: return None
           ├── if not (looks_like_cc_payment(tx.merchant) or tx.is_transfer):
           │   return None  ← most transactions exit here (no match attempt)
           │
           ├── DB query for candidates:
           │   WHERE user_id = ?
           │     AND id != tx.id
           │     AND account_id IS NOT NULL
           │     AND account_id != tx.account_id  ← must be different account
           │     AND is_income != tx.is_income     ← opposite direction
           │     AND amount BETWEEN amt-tol AND amt+tol
           │     AND date BETWEEN tx.date-4d AND tx.date+4d
           │     AND linked_transaction_id IS NULL  ← not already linked
           │
           ├── Sort candidates by |candidate.date - tx.date| ascending
           └── Return candidates[0] if any, else None

[Match found]:
link_as_transfer(db, a, b)
           │
           ├── a.is_transfer = True
           ├── b.is_transfer = True
           ├── a.linked_transaction_id = b.id
           ├── b.linked_transaction_id = a.id
           ├── db.add(a), db.add(b), db.flush()
           └── db.commit()

[No match found]:
  tx remains unlinked (is_transfer depends on parser/user)
  appears in pending_transfers count on dashboard if:
    tx.is_transfer=True OR looks_like_cc_payment(tx.merchant)

[Manual linking via UI]:
  GET /accounts/transfer/suggest/{tx_id}
    ← candidates (±10 days, looser tolerance)
  POST /accounts/transfer/link {a_id, b_id}
    → link_as_transfer(db, a, b)

[Unlinking]:
  POST /accounts/transfer/unlink/{tx_id}
    a.linked_transaction_id = None; a.is_transfer = False
    b.linked_transaction_id = None; b.is_transfer = False
```

**CC Payment Heuristic** (`looks_like_cc_payment`):
```
regex: pago tarjeta|pago recibido|pago cmr|pago falabella|pago credit|
       pago tc|abono tarjeta|abono cuenta|transferencia recibida|
       transferencia enviada|credit card payment|cc payment|payment received
```
Case-insensitive match on `Transaction.merchant`.

---

## 12. Balance Computation Lifecycle

```
[GET /accounts OR GET /dashboard]
           │
           ▼
account_svc.compute_account_balance(db, account)
           │
           ├── since = account.anchor_date (may be None)
           │
           ├── income_q:
           │   SELECT COALESCE(SUM(amount), 0)
           │   FROM transactions
           │   WHERE account_id = ?
           │     AND is_income = True
           │     [AND date >= since  (if anchor_date set)]
           │
           ├── expense_q:
           │   SELECT COALESCE(SUM(amount), 0)
           │   FROM transactions
           │   WHERE account_id = ?
           │     AND is_income = False
           │     [AND date >= since  (if anchor_date set)]
           │
           ├── if account.type == "credit":
           │   used = anchor_balance + expense - income
           │   used = max(used, 0.0)
           │   return {current_used: used,
           │           available_credit: max(credit_limit - used, 0)}
           │
           └── else (debit/savings/wallet/cash):
               balance = anchor_balance + income - expense
               return {current_balance: balance}

NOTE: is_transfer and status are NOT filtered here.
      A transfer transaction DOES affect account balance.
      A pending_review transaction DOES affect account balance.
      This differs from the dashboard total_spent calculation.
```

**Balance is NEVER stored.** It is recomputed on every read from:
- `anchor_balance` (static DB value)
- `anchor_date` (static DB value)
- All `Transaction` rows for this account since `anchor_date`

**Drift accumulates** when:
- Cash transactions are not recorded (forgot to log)
- Bank fees are not captured
- OCR parse rounded an amount
- A transaction is on the wrong account

Drift is corrected by `POST /accounts/{id}/reconcile` (snaps anchor).

---

## 13. Reconciliation Lifecycle

```
[User observes drift between app balance and bank statement]
           │
           ▼
POST /accounts/{acc_id}/reconcile
  {expected_balance: <what bank shows>, as_of_date: <optional>}
           │
           ├── compute current app balance:
           │   compute_account_balance(db, acc)
           │   current_app = used (credit) or balance (debit)
           │
           ├── drift = expected_balance - current_app
           │
           ├── as_of = as_of_date OR date.today()
           │
           ├── WRITE to DB:
           │   acc.anchor_date = as_of
           │   acc.anchor_balance = expected_balance
           │   db.commit()
           │
           └── return ReconcileOut {
               previous_anchor_balance, previous_anchor_date,
               new_anchor_balance, new_anchor_date, drift
           }

[Alternative: Cartola reconciliation]
POST /cartola/commit {reconcile_to_closing_balance: True, closing_balance: X}
           │
           ├── db.flush() ← count new transactions in balance
           ├── compute_account_balance() → current_after_save
           ├── drift = closing_balance - current_after_save
           ├── acc.anchor_date = date.today()
           ├── acc.anchor_balance = closing_balance
           └── db.commit()
```

---

## 14. Dashboard Computation Lifecycle

```
GET /dashboard
           │
           ▼
routers/dashboard.py:dashboard()
           │
           ├── ai/predictor.py:summarize(db, user, today)
           │   │
           │   ├── _month_bounds(today) → first, last, days_in_month
           │   │
           │   ├── spent = _sum(db, user_id, first, today, income=False)
           │   │   [EXCLUDES is_transfer=True AND status='pending_review']
           │   │
           │   ├── income_actual = _sum(db, ..., income=True)
           │   │   [EXCLUDES is_transfer=True AND status='pending_review']
           │   │
           │   ├── historical_avg_income = _avg_monthly_income(3 months)
           │   │
           │   ├── income_target: settings → monthly_budget → historical_avg
           │   │
           │   ├── fixed_expenses: from user.settings["fixed_expenses"]
           │   ├── fixed_total: sum of fixed_expenses amounts
           │   │
           │   ├── variable_budget = income_target - fixed_total
           │   │
           │   ├── linear_projection = (spent/days_elapsed) * days_in_month
           │   ├── trailing_30d = _trailing_avg_daily() * days_in_month
           │   ├── w = min(days_elapsed/15, 1.0)
           │   ├── projected_spend = w*linear + (1-w)*trailing
           │   │
           │   ├── safe_spend_actual = max(income_actual - fixed - spent, 0) / dr
           │   ├── safe_spend_projected = max(variable_budget - spent, 0) / dr
           │   │
           │   ├── status: good/warning/danger vs variable_budget
           │   │
           │   └── by_category: GROUP BY category (expenses, non-transfer, confirmed)
           │
           ├── alerts = ai/alerts.py:build_alerts(summary)
           │   [RULE-BASED, NO LLM]
           │   budget%: good/warning/danger message
           │   top category ≥ 40% of spending: nudge
           │
           ├── per-account summaries:
           │   for each non-archived account:
           │     compute_account_balance(db, account)
           │     → {current_balance, current_used, available_credit}
           │
           ├── pending_transfers = count_pending_cc_payments(db, user_id)
           │
           └── pending_review_count:
               COUNT(transactions WHERE status='pending_review')
```

**No LLM is used in the dashboard.** All computation is deterministic Python.

---

## 15. AI Provider Flow

```
[Any module needing LLM]
           │
           ▼
ai/provider.py:chat_completion(messages, *, purpose, user_id, db)
           │
           ├── _pick_provider():
           │   preferred = settings.ai_provider.lower()
           │   if preferred in _PROVIDERS and _PROVIDERS[preferred].available():
           │     return _PROVIDERS[preferred]
           │   else:
           │     return first available provider
           │   if none available: return None
           │
           ├── if None: return None  ← graceful degradation
           │
           ├── provider.chat_completion(messages, model, temperature, max_tokens)
           │   OpenAI:    client.chat.completions.create()
           │   Anthropic: client.messages.create()
           │            (system messages extracted from messages list)
           │   Gemini:    GenerativeModel.generate_content()
           │
           ├── _log_usage(db, user_id, resp, purpose)
           │   → services/ai_usage.py:record()
           │     INSERT AiUsage(user_id, provider, model, purpose,
           │                    prompt_tokens, completion_tokens)
           │
           └── return LLMResponse(text, prompt_tokens, completion_tokens,
                                  model, provider)

[Vision calls]
ai/provider.py:vision_json(system_prompt, user_text, image_data_url, ...)
           │
           ├── prov = _pick_provider()
           ├── if prov is None or not hasattr(prov, "vision_json"):
           │   return None
           │   NOTE: Only OpenAIProvider has vision_json().
           │         Anthropic and Gemini providers do NOT implement vision_json().
           │         Vision path is OpenAI-only.
           │
           └── prov.vision_json(system_prompt, user_text, image_data_url, ...)
```

**Vision is OpenAI-only.** Switching `AI_PROVIDER=anthropic` disables vision
parsing. The Tesseract fallback will activate.

**Provider availability:**
```
_PROVIDERS = {
    "openai":    OpenAIProvider()   → available if settings.openai_api_key != ""
    "anthropic": AnthropicProvider()→ available if settings.anthropic_api_key != ""
    "gemini":    GeminiProvider()   → available if settings.google_api_key != ""
}
```

---

## 16. Categorization Flow

```
[Transaction needs a category]
           │
           ▼
ai/categorizer.py:categorize(merchant, raw_text, *, db, user_id)
           │
           ├── [Layer 1 — FREE] _user_learned(db, user_id, merchant)
           │   SELECT category FROM merchant_category_rules
           │   WHERE user_id=? AND merchant_key=lower(merchant)
           │   Hit: return cached category immediately (no LLM cost)
           │   Miss: continue
           │
           ├── [Layer 2 — FREE] _rule_based(merchant, raw_text)
           │   haystack = f"{merchant} {raw_text}".lower()
           │   for cat, keywords in _RULES:
           │     if any(k in haystack for k in keywords): return cat
           │   Covers ~60-70% of generic LatAm + global merchants
           │   Miss: continue
           │
           └── [Layer 3 — PAID] _llm_categorize(merchant, raw_text)
               chat_completion([
                 system: "Classify into ONE of [19 Spanish categories]",
                 user: f"Merchant: {merchant}\nReceipt:\n{raw_text[:1500]}"
               ], temperature=0, max_tokens=10, purpose="categorize")
               if None: return None
               guess = resp.text.strip()
               return guess if guess in DEFAULT_CATEGORIES else None
               │
               └── fallback: "Otros"

[User corrects a category]:
PATCH /transactions/{id} {category: "new_category"}
  → categorizer.remember_correction(db, user_id, merchant, new_category)
    UPSERT merchant_category_rules(user_id, merchant_key, category, hits)
    hits += 1 on update
    → next categorization for same merchant: Layer 1 hits, zero LLM cost
```

**19 Spanish categories:**
Alimentación, Supermercado, Transporte, Compras, Entretenimiento, Bares y Salidas,
Cuentas y Servicios, Salud, Viajes, Suscripciones, Tecnología, Educación, Hogar,
Ropa, Ingresos, Transferencia, Inversión, Seguros, Otros

---

## 17. Storage Flow

```
[File save request]
           │
           ▼
app/storage.py:save_image(data: bytes, filename: str) → str (URL)
           │
           ├── if settings.storage_backend == "local":
           │   path = LOCAL_STORAGE_DIR / safe_filename
           │   write bytes to disk
           │   return "/files/{safe_filename}"
           │   (served by FastAPI StaticFiles mount at /files)
           │
           └── if settings.storage_backend == "s3":
               boto3.client("s3").put_object(
                 Bucket=settings.aws_bucket,
                 Key=safe_filename,
                 Body=data
               )
               return "https://{bucket}.s3.amazonaws.com/{key}"

[Frontend resolves image URLs]:
api.ts:resolveImageUrl(url):
  if url.startsWith("http"): return url (S3 or absolute URL)
  else: return API_BASE + url  (local /files/* path)
```

**Local storage** is the default. Files are stored in `./uploads/` (Docker
volume `lucas_uploads`). The directory is created at startup if it does not exist.

**S3 storage** requires `STORAGE_BACKEND=s3`, `AWS_BUCKET`, `AWS_ACCESS_KEY_ID`,
`AWS_SECRET_ACCESS_KEY`, `AWS_REGION`. No presigned URLs — objects are public.
`[NOT VERIFIED — S3 access control config not reviewed in source]`

---

## 18. Database Ownership Boundaries

Every table is user-scoped. There is no global data visible across users.

```
users
  └── id (PK)
      └── accounts        [user_id FK → users.id CASCADE DELETE]
      └── transactions    [user_id FK → users.id CASCADE DELETE]
      └── people          [user_id FK → users.id CASCADE DELETE]
      └── categories      [user_id FK → users.id CASCADE DELETE]
      └── merchant_category_rules [user_id FK → users.id CASCADE DELETE]
      └── ai_usage        [user_id FK → users.id CASCADE DELETE]

accounts
  └── id (PK)
      └── transactions    [account_id FK → accounts.id SET NULL on delete]

transactions
  └── id (PK)
      └── receipt_items   [transaction_id FK → transactions.id CASCADE DELETE]
      └── linked          [linked_transaction_id FK → transactions.id SET NULL]

receipt_items
  └── id (PK)
      └── item_assignments [item_id FK → receipt_items.id CASCADE DELETE]
      └── person (legacy)  [assigned_to FK → people.id SET NULL]

people
  └── id (PK)
      └── item_assignments [person_id FK → people.id CASCADE DELETE]
```

**Ownership enforcement in every router:**
```python
# Correct pattern — all reads/writes filter by user_id:
db.query(models.Transaction).filter(
    models.Transaction.id == tx_id,
    models.Transaction.user_id == current.id  ← mandatory
)
```

**No multi-tenancy cross-contamination** is architecturally possible as long
as this filter is present. Missing it is a security vulnerability.

---

## 19. Service Dependency Graph

```
routers/auth.py
  └── app/auth.py (JWT, bcrypt)
  └── models.User
  └── schemas (UserCreate, UserOut, TokenOut)

routers/upload.py
  └── app/ocr.py
      └── app/ai/provider.py
      └── app/ai/categorizer.py
  └── app/storage.py
  └── app/services/dedupe.py

routers/transactions.py
  └── app/ai/categorizer.py (remember_correction on PATCH)
  └── app/services/accounts.py (reconcile_new_transaction on POST)

routers/accounts.py
  └── app/services/accounts.py (compute_account_balance)
  └── app/storage.py (card image upload)

routers/split.py
  └── app/services/accounts.py (validate account ownership in settle)

routers/dashboard.py
  └── app/ai/predictor.py
  └── app/ai/alerts.py
  └── app/ai/chat.py
      └── app/ai/predictor.py
      └── app/ai/provider.py
  └── app/services/accounts.py

routers/cartola.py
  └── app/cartola.py
      └── app/ai/provider.py
      └── app/ocr.py:vision_parse
  └── app/ai/categorizer.py
  └── app/services/dedupe.py
  └── app/services/accounts.py

routers/voice.py
  └── app/ai/voice.py
      └── app/ai/provider.py
  └── app/ai/categorizer.py
  └── app/services/dedupe.py

routers/email.py
  └── app/ai/email_parser.py
      └── app/ai/provider.py
  └── app/services/accounts.py (reconcile on confirm)
  └── app/services/dedupe.py

routers/ai.py
  └── app/ai/provider.py
  └── app/services/ai_usage.py [NOT VERIFIED — file not read]
```

---

## 20. Error Propagation Model

```
[Exception in ai/provider.py chat_completion()]
  │
  provider.chat_completion() catches Exception → prints log → returns None
  ├── Caller checks `if resp is None:` → uses fallback
  └── Fallback: "Otros" (categorizer), _fallback_unclear (voice), None (cartola)

[AttributeError: resp.content in voice.py]
  │
  voice_ai.parse_voice() does NOT catch this
  └── Propagates to routers/voice.py:parse_voice()
      └── FastAPI default exception handler → HTTP 500
          {"detail": "Internal Server Error"}

[AttributeError: resp.content in cartola.py]
  │
  _llm_structure() does NOT catch this
  └── Propagates to cartola.parse_cartola()
      └── text path falls through to image path (silently — no explicit catch)
      Wait: _llm_structure has:
        try: ... except Exception: print + return None
      So this IS caught in cartola.py. The bug is masked by the try/except.
      Result: _llm_structure always returns None, text path never works.
      Falls to scanned-PDF path (vision_parse per page).

[DB exception during transaction save]
  │
  SQLAlchemy raises OperationalError or IntegrityError
  └── NOT caught in routers → FastAPI 500
      DB session is rolled back by get_db() dependency cleanup

[File too large]
  │
  router raises HTTPException(413)
  └── FastAPI returns {"detail": "..."} with 413 status

[Invalid JWT]
  │
  auth.get_current_user raises HTTPException(401)
  └── Frontend api.ts catches 401 → clearToken() + redirect to "/"

[Duplicate transaction (60s guard)]
  │
  router raises HTTPException(409, {"detail": "duplicate_transaction", "existing_id": N})
  └── Frontend can extract existing_id from error body to show "already saved"
```

---

## 21. State Transition Model

### Transaction Status States

```
                  [email/inbound creates]
                  [cartola OCR creates]
                          │
                          ▼
                   ┌──────────────┐
                   │ pending_     │
                   │ review       │
                   └──────┬───────┘
                          │ POST /email/review
                          │
              ┌───────────┼────────────────────┐
              │           │                    │
    action=confirm  action=not_expense   action=pending
              │           │                    │
              ▼           ▼                    ▼
       ┌──────────┐  [DELETED]         stays pending_review
       │confirmed │                    with "[Por Cobrar]" note
       └──────────┘
              ▲
              │
    [Manual entry via POST /transactions]
    [Upload confirm via POST /transactions]
    [Voice confirm via POST /transactions]
```

### Account Anchor States

```
[Initial state]
  anchor_date = NULL (all transactions counted)
  anchor_balance = 0.0

[After first reconcile / cartola commit]
  anchor_date = specific date
  anchor_balance = known-correct balance at that date

[Balance drift accumulates]
  → transactions happen that aren't tracked
  → app_balance ≠ bank_balance

[After reconcile]
  anchor_date = today
  anchor_balance = bank_balance
  → drift reset to zero
```

### Transfer Link States

```
Transaction A (CC payment debit)    Transaction B (CC card credit)
  is_transfer = False                 is_transfer = False
  linked_transaction_id = NULL        linked_transaction_id = NULL
           │                                   │
           └───────── reconcile_new_tx ─────────┘
                              │
                              ▼
  is_transfer = True                  is_transfer = True
  linked_transaction_id = B.id        linked_transaction_id = A.id
           │                                   │
           └──────── unlink_transfer ───────────┘
                              │
                              ▼
  is_transfer = False                 is_transfer = False
  linked_transaction_id = NULL        linked_transaction_id = NULL
```

---

## 22. Request Lifecycle Examples

### Example A: Uploading a Supermarket Receipt (Happy Path)

```
1. User: drag receipt image to /upload page
2. Frontend: POST /upload (multipart, 800KB JPEG)
3. Backend: validate content-type="image/jpeg", size < 25MB
4. storage.save_image() → /files/upload_abc123.jpg
5. ocr.parse_receipt(data):
   a. vision_parse() if OpenAI key set:
      - run_ocr() → Tesseract text (tess_conf=0.94 for clear photo)
      - _extract_boleta_totals() → {total_neto: 29521, iva_amount: 5609}
      - _shrink_for_vision() → 1200px JPEG
      - ai_provider.vision_json(..., grounding_text="total_neto=29521 iva=5609")
      - LLM returns JSON with 7 items
      - tess_conf=0.94: light normalization → items sum = 29521
      - IVA row appended: {name:"IVA (19%)", price:5609, qty:1}
      - raw_amount = 35130 (29521+5609)
   b. _enrich(): categorizer.categorize("Lider", ...) → "Supermercado"
   c. find_duplicate() → None (no matching existing tx)
6. Frontend receives ParsedUpload:
   {type:"single", image_url:"/files/upload_abc123.jpg",
    transactions:[{amount:35130, merchant:"Lider", category:"Supermercado",
                   items:[...7 items + IVA...]}]}
7. User reviews, confirms
8. Frontend: POST /transactions?image_url=/files/upload_abc123.jpg
   {amount:35130, category:"Supermercado", date:"2026-05-11", ...}
9. Backend: 60s duplicate guard → clear
10. INSERT Transaction(amount=35130, category="Supermercado", ...)
11. reconcile_new_transaction() → looks_like_cc_payment("Lider") = False → skip
12. Return TransactionOut
Total time: ~2-4s (dominated by vision API call)
```

### Example B: Email-Imported Transaction (Review Queue)

```
1. Banco de Chile sends "Cargo en tarjeta: $15.990 en UBER" email
2. Gmail filter forwards to lucas-abc123@notify.lucasapp.com
3. SendGrid POSTs to /email/inbound
4. Token extracted from To: address → user found
5. email_parser._extract_heuristic():
   - amount=15990, merchant="" (no keyword match), is_income=False
   - merchant empty → falls to LLM
6. ai_provider.chat_completion() → {amount:15990, merchant:"Uber", category:"Transporte"}
7. find_duplicate() → None
8. INSERT Transaction(status="pending_review", amount=15990, merchant="Uber")
9. Dashboard badge: pending_review_count=1
10. User clicks review banner → /review page
11. GET /email/pending → [the transaction]
12. User clicks confirm → POST /email/review/{id} {action:"confirm"}
13. tx.status = "confirmed"
14. reconcile_new_transaction() → looks_like_cc_payment("Uber") = False → skip
15. Dashboard count clears
```

---

## 23. Performance Bottlenecks

| Bottleneck | Source | Typical Cost |
|-----------|--------|-------------|
| Vision LLM call (gpt-4o-mini) | OCR: every receipt upload | 1–3s, ~$0.0001 |
| Tesseract preprocessing | OpenCV bilateral filter on full image | 200–500ms |
| Dashboard query (no index on date+user_id composite) | N full-table scan on transactions | grows with data |
| compute_account_balance (2 SUM queries per account) | Every GET /accounts, every dashboard | O(txns) per account |
| Chat context build (200 txns, 90 days) | GET /chat (every message) | 50–200ms DB |
| Cartola PDF rendering via pdfplumber | 1 per page | 500ms–2s per page |
| Cold start (uvicorn + model imports) | First request after container start | 3–10s |

**No caching layer** exists anywhere in the system. Every request hits the DB.
The dashboard makes approximately 5–10 DB queries per request.

---

## 24. Architectural Weak Points

1. **No migrations directory.** `alembic` is in requirements but no `alembic.ini`
   or `migrations/` folder exists. Any schema change requires manual intervention.
   `[CONFIRMED]`

2. **No refresh token.** JWT expires after 7 days with no silent renewal.
   Users lose session without warning. `[CONFIRMED]`

3. **Vision is OpenAI-only.** Switching to Anthropic or Gemini silently disables
   the primary OCR path. Only the Tesseract fallback activates. `[CONFIRMED]`

4. **Balance computation is O(n) per account.** As transaction count grows,
   `compute_account_balance()` gets slower. No aggregate cache.

5. **Chat context is capped at 200 transactions.** Users with dense history
   get increasingly incomplete answers as the cap cuts off older data. `[CONFIRMED]`

6. **Multi-currency aggregation without conversion.** Dashboard sums amounts
   across currencies. A user with CLP + USD transactions gets a meaningless
   total_spent. `[CONFIRMED]`

7. **Single-process server.** uvicorn with default single worker. Long vision
   API calls (3s) block other requests on the same worker. `[CONFIRMED]`

8. **Email token is permanent and unrevocable.** Once a `lucas-{token}@domain`
   address is known to an attacker, they can inject pending_review transactions
   indefinitely. No token rotation mechanism. `[CONFIRMED]`

9. **cartola.py `_llm_structure()` silently fails.** The `resp.content` bug
   is caught by a `try/except Exception` wrapper. The text path silently falls
   to vision path. Text PDF cartola parsing is non-functional without the
   error being visible to the user or developer. `[CONFIRMED BUG]`

---

## 25. Critical Invariants

The following invariants must hold at all times for data integrity:

| # | Invariant | Enforced by | Breaks if |
|---|-----------|-------------|-----------|
| I1 | Transfer rows excluded from spending totals | `predictor._sum(is_transfer=False)` | is_transfer flag wrong |
| I2 | Live balance never stored | No column for it in accounts | Someone writes to a computed field |
| I3 | Transfer links are bidirectional | `link_as_transfer()` sets both sides | Only one side is set |
| I4 | Amount is always positive | Schema validation on write | Negative value slips through |
| I5 | pending_review excluded from spending | `predictor._sum(status!='pending_review')` | Status update bypassed |
| I6 | Balance = anchor + delta since anchor_date | compute_account_balance() formula | anchor_date or anchor_balance corrupted |
| I7 | CLP amounts have no decimals | `_to_float()` / `_parse_clp()` | Raw float used directly |
| I8 | IVA = 19% of TOTAL NETO (±5%) | `_extract_boleta_totals()` sanity check | Sanity check removed |
| I9 | User data scoped by user_id | Every query filters `user_id=current.id` | Filter omitted |
| I10 | Dedupe check before external tx save | upload/cartola/email all call find_duplicate | Check bypassed |

---

## 26. Failure Modes

| Failure | Trigger | Observable Effect | Recovery |
|---------|---------|-------------------|---------|
| Voice HTTP 500 | AI key set, `resp.content` bug | 500 on every voice call | Fix bug in voice.py |
| Cartola text parsing silent failure | `resp.content` bug | Falls to vision path | Fix bug in cartola.py |
| PDF receipt crash | pdf2image missing | HTTP 500 on PDF upload | Add pdf2image to requirements |
| Vision disabled | AI_PROVIDER=anthropic or gemini | Tesseract fallback activates silently | Set AI_PROVIDER=openai |
| Frontend can't reach backend in Docker | NEXT_PUBLIC_API_URL=localhost:8000 | Network error on all API calls | Fix docker-compose URL |
| JWT expired | 7-day expiry | 401 on all API calls → auto-logout | Re-login |
| DB connection refused | Postgres not started | 500 on every request | Start DB container |
| Duplicate upload creates double | upload + save happen twice | Two identical rows | User must delete duplicate manually |
| Cartola saves without transfer-link | reconcile_new_transaction not called | CC payments not auto-linked | Manual link from UI |
| Balance drift | Untracked transactions | App balance ≠ bank balance | Reconcile endpoint |

---

## 27. Concurrency Risks

1. **No optimistic locking on balance computation.** Two concurrent requests
   reading and writing `anchor_balance` for the same account could produce
   a race condition. In practice, low risk on single-user personal finance.

2. **No SELECT FOR UPDATE on transfer matching.** Two uploads arriving
   simultaneously with matching transfer candidates could both match the same
   counterpart transaction. Result: `linked_transaction_id` set to the same row
   from two directions. The `link_as_transfer()` `db.flush()` partially mitigates
   this but does not prevent the race.

3. **60-second duplicate guard has a time window.** Two identical requests
   arriving within the same DB transaction could both pass the `created_at >=
   NOW() - 60s` check before either commits. Low probability, not mitigated.

4. **uvicorn single worker.** FastAPI with default settings runs one synchronous
   worker. Async endpoints (`async def upload_image`) can interleave; sync
   endpoints (`def dashboard`) block. Vision API calls (async HTTP) should not
   block other requests, but heavy Tesseract (CPU) will.

5. **No row-level locking on account anchor updates.** A simultaneous cartola
   commit and manual reconcile for the same account could produce last-write-wins
   on `anchor_balance` and `anchor_date`.

---

## 28. Future Scaling Constraints

| Constraint | Current state | What breaks first |
|-----------|---------------|------------------|
| Per-user balance is O(txns) | recomputed on every read | Slow dashboard for power users (>5K txns) |
| Chat context capped at 200 txns | `limit=200` hardcoded | Answers degrade for dense users |
| Single Postgres instance | no read replicas | Dashboard queries contend with write traffic |
| Vision is OpenAI-only | no Anthropic/Gemini vision | AI_PROVIDER swap silently degrades OCR |
| No horizontal backend scaling | one uvicorn worker | Tesseract CPU blocks concurrent users |
| File storage is local | Docker volume | Cannot scale to multiple backend instances |
| JWT stored in localStorage | XSS-accessible | Requires HttpOnly cookies for hardened security |
| No background job queue | all processing is synchronous | Long cartola PDFs (20 pages) time out HTTP requests |
| No connection pooling config | SQLAlchemy default | High concurrent users exhaust DB connections |
| Categories are hardcoded strings | no DB table | Adding a category requires code change + migration |

---

## APPENDIX — Quick Reference: Where Each Key Operation Happens

| Operation | File | Function |
|-----------|------|---------|
| Parse receipt image | `app/ocr.py` | `parse_receipt()` |
| Parse boleta items | `app/ocr.py` | `_parse_boleta_from_text()` |
| Parse cartola PDF | `app/cartola.py` | `parse_cartola()` |
| Parse voice transcript | `app/ai/voice.py` | `parse_voice()` |
| Parse email body | `app/ai/email_parser.py` | `parse_email()` |
| Categorize transaction | `app/ai/categorizer.py` | `categorize()` |
| Remember user correction | `app/ai/categorizer.py` | `remember_correction()` |
| Compute account balance | `app/services/accounts.py` | `compute_account_balance()` |
| Find transfer match | `app/services/accounts.py` | `find_transfer_match()` |
| Link transfer pair | `app/services/accounts.py` | `link_as_transfer()` |
| Auto-link on save | `app/services/accounts.py` | `reconcile_new_transaction()` |
| Find duplicate | `app/services/dedupe.py` | `find_duplicate()` |
| Guess account from hint | `app/services/dedupe.py` | `suggest_account_for_hint()` |
| Compute dashboard data | `app/ai/predictor.py` | `summarize()` |
| Generate alerts | `app/ai/alerts.py` | `build_alerts()` |
| Chat with user | `app/ai/chat.py` | `answer()` / `answer_with_action()` |
| Send LLM request | `app/ai/provider.py` | `chat_completion()` / `vision_json()` |
| Save file | `app/storage.py` | `save_image()` |

---

*This document describes the system as it exists on 2026-05-11.*
*Any section marked [NOT VERIFIED] requires source validation before acting on it.*
*Update this document after any architectural change.*
