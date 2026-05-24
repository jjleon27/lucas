# LUCAS — AI-Powered Personal Finance Assistant

> Screenshot-first finance tracking, predictive budgeting, and interactive bill-splitting.
> Think: Mint + Splitwise + ChatGPT, in one PWA that syncs everywhere.

---

## ✨ Features (MVP)

- 📸 **Screenshot upload + OCR** — drop a receipt or bank confirmation, LUCAS extracts amount, date, merchant, and line items.
- 🧠 **AI financial brain** — LLM-powered categorization, end-of-month balance prediction, daily safe-spend calculation.
- 📊 **Clean dashboard** — budget vs. actual, category breakdown, traffic-light status (good / warning / danger).
- 🧾 **Interactive bill splitter** — tap or drag items, assign them to people, totals update in real time.
- 🔄 **Learning loop** — every user correction improves categorization and split suggestions.
- 🌐 **Sync everywhere** — PWA works identically on phone, tablet, and desktop browsers.

---

## 🏗️ Architecture

```
┌──────────────┐     REST/JWT      ┌───────────────┐
│  Next.js 14  │ ←───────────────→ │  FastAPI      │
│  (PWA)       │                   │  (Python 3.11)│
│  Tailwind    │                   │               │
└──────────────┘                   └───────┬───────┘
                                           │
                             ┌─────────────┼───────────────┐
                             ↓             ↓               ↓
                       ┌──────────┐  ┌─────────┐   ┌─────────────┐
                       │PostgreSQL│  │  Local  │   │  OpenAI /   │
                       │          │  │ Storage │   │  Claude API │
                       └──────────┘  │  / S3   │   │  (OCR+cat.) │
                                     └─────────┘   └─────────────┘
```

---

## 📁 Project structure

```
lucas/
├── backend/              # FastAPI + PostgreSQL + OCR/AI
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── database.py
│   │   ├── models.py
│   │   ├── schemas.py
│   │   ├── auth.py
│   │   ├── ocr.py
│   │   ├── storage.py
│   │   ├── ai/
│   │   │   ├── categorizer.py
│   │   │   ├── predictor.py
│   │   │   └── alerts.py
│   │   └── routers/
│   │       ├── auth.py
│   │       ├── upload.py
│   │       ├── transactions.py
│   │       ├── split.py
│   │       └── dashboard.py
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env.example
├── frontend/             # Next.js 14 App Router, Tailwind, TypeScript
│   ├── src/
│   │   ├── app/
│   │   │   ├── layout.tsx
│   │   │   ├── page.tsx          # landing / login
│   │   │   ├── dashboard/page.tsx
│   │   │   ├── upload/page.tsx
│   │   │   ├── split/page.tsx
│   │   │   └── transactions/page.tsx
│   │   ├── components/
│   │   │   ├── Sidebar.tsx
│   │   │   ├── StatCard.tsx
│   │   │   ├── UploadZone.tsx
│   │   │   ├── BillSplitter.tsx
│   │   │   └── TransactionList.tsx
│   │   └── lib/
│   │       └── api.ts
│   ├── package.json
│   ├── tailwind.config.ts
│   └── next.config.js
├── docker-compose.yml    # one-command local dev
└── README.md
```

---

## 🚀 Quick start (Docker — recommended)

```bash
# 1. Clone / unzip the project
cd lucas

# 2. Configure env vars
cp backend/.env.example backend/.env
# Edit backend/.env and add your OPENAI_API_KEY (or ANTHROPIC_API_KEY)

# 3. Start everything
docker compose up --build
```

That's it. Open:
- **Frontend:** http://localhost:3000
- **Backend docs:** http://localhost:8000/docs
- **Postgres:** localhost:5432 (user: `lucas`, pass: `lucas`, db: `lucas`)

---

## 🤖 Cómo dejar andando la IA (para hablarle de verdad)

Sin una API key la app igual corre, pero el chat y la lectura inteligente de boletas caen al modo "fallback" (texto crudo y reglas). Para activarlo en serio:

### 1. Conseguir una API key (una de estas tres)

| Proveedor | Dónde sacarla | Costo aprox. MVP |
|-----------|---------------|------------------|
| **OpenAI** (recomendado para empezar) | https://platform.openai.com/api-keys → "Create new secret key" | Carga mínima USD 5. ~$0.0001 por chat con `gpt-4o-mini` |
| **Anthropic Claude** | https://console.anthropic.com → Settings → API Keys | Similar a OpenAI |
| **Google Gemini** (más barato) | https://aistudio.google.com/app/apikey → "Create API key" | Tiene free tier generoso |

Para OpenAI necesitas cargar al menos USD 5 en *Billing → Add payment method* antes de que la key funcione.

### 2. Pegarla en el archivo `.env`

El archivo va en **`backend/.env`** (mismo folder que `backend/Dockerfile`). Si no existe todavía:

```bash
cd backend
cp .env.example .env
```

Abre `backend/.env` con cualquier editor y busca la línea de tu proveedor:

```env
OPENAI_API_KEY=sk-proj-aquiVaTuKeyCompleta...
```

Guarda el archivo. **Nunca subas el `.env` a GitHub** — `.gitignore` ya lo excluye.

### 3. Reiniciar el backend

```bash
# Si usas Docker:
docker compose down && docker compose up --build

# Si corres sin Docker:
# Ctrl+C en la terminal de uvicorn y arráncalo de nuevo.
uvicorn app.main:app --reload --port 8000
```

### 4. Verificar que quedó activa

```bash
curl http://localhost:8000/ai/status
# Respuesta esperada:
# {"available": true, "provider": "openai"}
```

Si dice `"available": false`, la key no se cargó — revisa que el archivo se llame exactamente `.env` (no `.env.txt`) y que no haya espacios ni comillas alrededor del valor.

### 5. Probarla en la app

1. Abre http://localhost:3000
2. Crea cuenta / login
3. Ve a **Chat** (sidebar) y pregunta "¿cuánto gasté este mes?"
4. Sube una boleta o screenshot del banco en **Upload** — debería leerla con IA de verdad.

### 6. Ver cuánto estás gastando en tokens

```bash
curl -H "Authorization: Bearer TU_JWT" http://localhost:8000/ai/usage
```

Devuelve tokens + costo estimado en USD del mes, desglosado por `purpose`
(chat / categorize / parse). Ese es tu KPI: **`cost_LLM / usuario_activo`**.

### Cambiar de proveedor

Para saltar de OpenAI a Claude (o al revés) sin tocar código:

```env
AI_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-api03-...
```

Toda la app lo usa automáticamente — `chat.py`, `categorizer.py` y `ocr.py` llaman al `ai/provider.py` abstracto, no al SDK directo.

---

## 🛠️ Manual local setup (no Docker)

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # fill in DATABASE_URL + OPENAI_API_KEY

# Install Tesseract OCR system dependency:
#   macOS:   brew install tesseract
#   Ubuntu:  sudo apt install tesseract-ocr
#   Windows: https://github.com/UB-Mannheim/tesseract/wiki

uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local   # point NEXT_PUBLIC_API_URL at your backend
npm run dev
```

---

## 🔑 Environment variables

### `backend/.env`

| Variable            | Default                                        | Purpose                                         |
|---------------------|------------------------------------------------|-------------------------------------------------|
| `DATABASE_URL`      | `postgresql://lucas:lucas@localhost:5432/lucas`| SQLAlchemy Postgres connection string           |
| `JWT_SECRET`        | *(required)*                                   | 32+ random chars for token signing              |
| `OPENAI_API_KEY`    | *(optional)*                                   | Enables LLM categorization & predictions        |
| `STORAGE_BACKEND`   | `local`                                        | `local` or `s3`                                 |
| `LOCAL_STORAGE_DIR` | `./uploads`                                    | Where to write images if `STORAGE_BACKEND=local`|
| `AWS_BUCKET`        | —                                              | Required if `STORAGE_BACKEND=s3`                |

### `frontend/.env.local`

| Variable              | Default                  |
|-----------------------|--------------------------|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000`  |

---

## 📱 Sync: how it works

LUCAS is a **single Next.js PWA** that runs identically in:
- Any mobile browser (iOS Safari, Android Chrome) — "Add to Home Screen" installs it like a native app.
- Any desktop browser.
- Future: wrapped as a React Native / Capacitor shell for app-store distribution.

Because everything flows through the FastAPI backend + Postgres, **every device sees the same data instantly** — just log in. No separate codebases to maintain.

---

## 🧭 Next improvements (post-MVP roadmap)

1. **Conversational chat** — "how much did I spend on Uber in March?" answered by a tool-using LLM.
2. **Shareable split links** — non-users assign their own items via a public link → organic growth.
3. **Recurring-subscription detection** — auto-surface Netflix/Spotify/gym.
4. **Email-inbox ingestion** — Gmail OAuth → scan for receipts & bank alerts (per-provider parsers).
5. **On-device OCR** — run Tesseract in-browser (WASM) for privacy-conscious users.
6. **Open banking** (Plaid/TrueLayer/Belvo) — optional direct connection for users who want it.
7. **Multi-currency** — essential for LatAm/EU users.
8. **WebSocket live-split** — multiple people assigning items in the same room.
9. **Fine-tuned categorizer** — once you have 10k+ user-corrected labels, replace LLM calls with a small local model to cut cost.
10. **Native apps** — Capacitor wrap once PMF is clear.

---

## 📜 License

MIT — do whatever you want, but attribution appreciated.
