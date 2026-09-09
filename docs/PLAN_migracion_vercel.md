# Plan: migrar Lucas de Railway → 100% Vercel (Plan B)

> Objetivo: eliminar Railway. Un solo proyecto Vercel = frontend Next.js + backend
> FastAPI como Vercel Python Functions. Postgres en Neon (free). Imágenes en Vercel Blob.
> Costo de hosting: US$0 (Vercel Hobby + Neon free + Blob free tier).

Fecha: 2026-09-09. Rama `migracion-vercel`.

## Progreso
- [x] Paso 1 — `api/index.py` (Starlette Mount /api) + `api/requirements.txt`. commit 60992a7
- [x] Paso 2 — cv2/pytesseract lazy; `run_ocr()` degrada a "".
- [x] Paso 3 — `pdf2image` → `pypdfium2` en ocr.py + requirements.
- [x] Paso 4 — `storage.py` backend `"blob"` (pkg `vercel_blob`); `next.config.js` host blob.
- [x] Extra — `database.py` NullPool en Vercel; `vercel.json` monorepo.
- [x] Verificado local: `/api/health` `/api/docs` `/api/openapi.json` → 200. Tests 457 pass, 11 fail preexistentes (sin regresión).
- [ ] Paso 5 — Neon (provisionar + DATABASE_URL pooled)
- [ ] Paso 6 — Vercel project Root Directory → repo root
- [ ] Paso 7 — Blob store + env vars en Vercel
- [ ] Paso 8 — deploy preview → verificar → prod ; datos: FRESH (usuario eligió b) ; apagar Railway

## Decisión tomada: datos viejos → EMPEZAR DE CERO (opción b). No se migra el Postgres de Railway.

---

## RUNBOOK pasos 5-8 (los ejecuta el usuario — el classifier bloquea deploy/infra a Claude)

```bash
cd /Users/kako2/Documents/lucas
git push -u origin migracion-vercel        # subir la rama

# --- Vercel: apuntar el proyecto al repo root (no a frontend/) ---
# Dashboard → proyecto lucas → Settings → General → Root Directory → dejar VACÍO (repo root).
# (vercel.json ya hace: build de Next en frontend/, y la función api/index.py.)

# --- Neon (Postgres serverless, free) ---
vercel link                                # linkear el dir al proyecto lucas
vercel integration add neon                # o Dashboard → Storage → Create → Neon
#   → crea la DB y setea POSTGRES_URL / DATABASE_URL en el proyecto.
#   Usar el connection string POOLED (…-pooler.…neon.tech).

# --- Vercel Blob (imágenes) ---
vercel blob store add lucas-uploads        # o Dashboard → Storage → Create → Blob
#   → setea BLOB_READ_WRITE_TOKEN en el proyecto.

# --- Env vars del proyecto (Production + Preview) ---
vercel env add OPENAI_API_KEY              # la real (estaba en Railway)
vercel env add OPENAI_VISION_MODEL         # gpt-5-mini
vercel env add JWT_SECRET                  # generar: openssl rand -hex 32
vercel env add ALLOW_PASSWORDLESS          # false
vercel env add STORAGE_BACKEND            # blob
vercel env add CORS_ORIGINS               # https://<tu-dominio>.vercel.app
# DATABASE_URL y BLOB_READ_WRITE_TOKEN los setea la integración sola.
# Si Neon setea POSTGRES_URL y no DATABASE_URL:  vercel env add DATABASE_URL  (mismo valor pooled)

# --- Frontend env ---
vercel env add NEXT_PUBLIC_API_URL        # /api    (same-origin)
#   quitar el viejo NEXT_PUBLIC_API_URL que apuntaba a Railway.

# --- Deploy preview y verificación ---
vercel deploy                             # preview
#   probar:  curl https://<preview>.vercel.app/api/health   → {"ok":true}
#            abrir /api/docs, hacer signup/login, subir 1 boleta (foto), ver dashboard
#            probar 1 cartola PDF (verifica pypdfium2)
vercel deploy --prod                      # promover a producción

# --- Apagar Railway ---
# Dashboard Railway → proyecto reasonable-laughter → Settings → Delete
```

## Riesgos vivos a vigilar en el deploy
- `vercel.json` monorepo: si Vercel no buildea Next, mover `buildCommand`/`outputDirectory`
  o poner Root Directory = `frontend` y las funciones en `frontend/api/`.
- `opencv`/`tesseract` ausentes → `run_ocr()` devuelve "" (ya manejado) → todo va por visión.
- `pdfplumber.to_image()` (cartola escaneada) usa pypdfium2 como backend → verificar con PDF real.
- Neon autosuspend: primera query tras idle +~1s.
- Cold start función Python ~2-5s.

---

## Arquitectura objetivo (enfoque de MÍNIMA CHURN)

```
repo/
├── api/
│   ├── index.py            # sys.path → ../backend; from app.main import app
│   └── requirements.txt    # trimmed para serverless (sin tesseract/opencv; pdf2image→pypdfium2)
├── backend/                # SIN CAMBIOS de ubicación. Sigue corriendo local + queda como fallback.
│   └── app/ ...            # solo cambios internos: lazy imports (paso 2) + storage blob (paso 4)
├── frontend/               # Next.js (solo env + next.config)
└── vercel.json             # functions: api/index.py con includeFiles: "backend/**"
```

- NO se mueve `backend/`. `docker-compose`, `Makefile`, `railway.json`, tests → siguen válidos.
- Vercel bundlea `backend/` con la función vía `includeFiles`. `api/index.py` hace
  `sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))`.
- Si `includeFiles` + sys.path da problemas → plan C: `git mv backend/app api/app`.

- Todas las rutas del backend quedan bajo `/api/*` en el mismo dominio.
  `NEXT_PUBLIC_API_URL = /api` (same-origin → adiós CORS).
- FastAPI con `root_path="/api"` para que `/docs` y las rutas resuelvan bien.

---

## Pasos

### 1. Entrypoint Vercel (sin mover backend/)
- Crear `api/index.py`:
  ```python
  import os, sys
  sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
  from app.main import app  # noqa: E402,F401  — Vercel Python sirve este ASGI app
  ```
- Crear `api/requirements.txt` (ver paso 3).
- `backend/` NO se toca de ubicación. Nada de `git mv`.
- Doc: README gana una sección "Deploy en Vercel"; el resto sigue igual.

### 2. Imports pesados → lazy / opcionales
`ocr.py` importa `cv2, numpy, pytesseract` al tope del módulo. En serverless no van.
- Mover `import cv2 / pytesseract` DENTRO de `_preprocess()` y `run_ocr()` (único lugar que los usa; es el fallback Tesseract, no el camino de visión).
- `numpy`: lo usa `_preprocess` (return type `np.ndarray`) y quizá PIL/pdfplumber. Mantener en requirements (wheel chico) pero quitar el type hint a nivel módulo si estorba.
- `cartola.py` importa `pdfplumber` al tope → es Python puro (pdfminer.six), funciona en Vercel. Dejar.
- Envolver todo uso de `cv2`/`pytesseract` en try/except ImportError con fallback: si no están, `parse_receipt` va directo a `vision_parse` (que es lo que ya hace en prod con API key).

### 3. requirements serverless
Quitar de `api/requirements.txt`:
- `pytesseract` (necesita binario tesseract-ocr, no hay en Vercel)
- `opencv-python-headless` (se puede, pero es dead weight si el fallback no corre; quitar)
Reemplazar:
- `pdf2image==1.17.0` → `pypdfium2` (render PDF→imagen, wheel puro, sin poppler)
  - reescribir `pdf_page_to_image_bytes()` y `pdf_page_count()` con `pypdfium2`
Mantener: fastapi, uvicorn, sqlalchemy, psycopg2-binary, pydantic*, python-jose, bcrypt, passlib, pillow, pillow-heif, numpy, openai, boto3(?), httpx, pdfplumber, slowapi, python-multipart, python-dotenv.
- `boto3` → se puede quitar si vamos 100% Blob (ver paso 4). Dejar por ahora.
- Añadir: `requests` (para llamar a la API de Vercel Blob desde Python).
Mantener un `api/requirements-local.txt` con tesseract/opencv para dev local que quiera el fallback.

### 4. Storage → Vercel Blob
- Nuevo backend `"blob"` en `storage.py`:
  ```python
  def _blob_save(data, ext):
      import requests
      r = requests.put(
          f"https://blob.vercel-storage.com/{uuid.uuid4().hex}.{ext}",
          headers={"authorization": f"Bearer {settings.blob_rw_token}",
                   "x-content-type": f"image/{ext}",
                   "x-api-version": "7"},
          data=data, timeout=30)
      r.raise_for_status()
      return r.json()["url"]   # https://<store>.public.blob.vercel-storage.com/...
  ```
- `config.py`: add `blob_rw_token: str = ""` (env `BLOB_READ_WRITE_TOKEN`), `storage_backend` acepta `"blob"`.
- `main.py`: NO montar `/files` StaticFiles si backend != local. Quitar el `Path(...).mkdir` de startup salvo local.
- `save_image()`: rama `blob` antes que `s3`/`local`.
- Verificar API de Blob vigente (headers/version) al ejecutar — la API REST cambia; confirmar contra docs.
- `frontend/next.config.js`: añadir `{ protocol: "https", hostname: "**.blob.vercel-storage.com" }`.
- `api.ts` `resolveImageUrl` ya maneja URLs absolutas `http(s)` → OK.

### 5. DB → Neon
- Provisionar Neon vía Vercel Marketplace (skill `vercel:marketplace` / `vercel integration`).
  Setea `DATABASE_URL` / `POSTGRES_URL` en el proyecto Vercel automáticamente.
- Usar el connection string **pooled** de Neon (PgBouncer) para serverless.
- `database.py`: `create_engine(url, poolclass=NullPool, pool_pre_ping=True)` — en serverless
  cada invocación abre/cierra; NullPool evita fugas de conexiones. (o pool_size=1, max_overflow=0)
- `init_db()` corre en cada cold start (create_all + ALTERs idempotentes). Aceptable; opcional
  gate con env `RUN_DB_INIT=1` solo en un deploy.
- **Datos viejos (117 MB en Railway, servicio caído):** DECISIÓN DEL USUARIO —
  (a) revivir Railway 1 mes (US$5) → `pg_dump` → restore en Neon → cancelar Railway, o
  (b) empezar de cero (se pierden cuentas/transacciones/boletas históricas).

### 6. vercel.json (root) + Root Directory
- Cambiar Vercel Project → Root Directory = repo root (hoy es `frontend/`).
- `vercel.json`:
  ```json
  {
    "buildCommand": "cd frontend && npm install && npm run build",
    "outputDirectory": "frontend/.next",
    "framework": "nextjs",
    "functions": { "api/index.py": { "runtime": "@vercel/python", "maxDuration": 60 } },
    "rewrites": [{ "source": "/api/:path*", "destination": "/api/index" }]
  }
  ```
  (confirmar sintaxis exacta al ejecutar; puede requerir `builds` en vez de `functions`,
   o mover Next a root. Alternativa: dejar Root=frontend y usar un segundo proyecto solo
   para `/api` — pero eso reintroduce dos dominios. Preferir monorepo en un proyecto.)

### 7. Env vars en Vercel (Production)
Migrar desde Railway + añadir:
- `OPENAI_API_KEY` (real, la de Railway)
- `OPENAI_VISION_MODEL=gpt-5-mini`
- `JWT_SECRET` (generar uno nuevo fuerte)
- `ALLOW_PASSWORDLESS=false`  ← arreglar de paso (hoy true)
- `DATABASE_URL`  (auto Neon, pooled)
- `BLOB_READ_WRITE_TOKEN`  (auto Blob)
- `STORAGE_BACKEND=blob`
- `CORS_ORIGINS` — con same-origin casi no importa; poner el dominio Vercel igual.
- `NEXT_PUBLIC_API_URL=/api`  (frontend)

### 8. Verificación post-deploy
- `curl https://<app>.vercel.app/api/health` → `{"ok": true}`
- `curl https://<app>.vercel.app/api/docs` → 200
- Signup/login desde el front en el celu.
- Subir una boleta (foto iPhone) → parsea con gpt-5-mini → guarda imagen en Blob → aparece.
- Ver dashboard / transacciones (lee Neon).
- Cartola PDF (probar pypdfium2 con un PDF real).

---

## Riesgos
- **`vercel.json` monorepo (Next + Python en un proyecto)**: es la parte más frágil; puede
  necesitar iterar la config o mover el frontend a root. Plan B del plan: Root=frontend
  y las funciones Python en `frontend/api/` (Vercel las toma de ahí).
- **pdf2image→pypdfium2**: reescritura, testear con cartola real.
- **Cold starts** Python ~2-5s tras idle. Aceptable para uso personal.
- **Neon free**: autosuspend a los 5 min → +~1s primera query. 0.5 GB (data 117 MB, ok).
- **Conexiones**: obligatorio NullPool o pooler de Neon, si no se agotan conexiones.
- **Fallback Tesseract muere en prod** (sin cv2/tesseract) — OK, visión es primario y hay API key.

## Orden de ejecución sugerido
1. Rama `git checkout -b migracion-vercel`.
2. Pasos 1-4 (refactor de código: move, lazy imports, requirements, storage blob) — seguro,
   testeable local, no toca infra.
3. Correr tests + eval local para no romper nada.
4. Paso 5-7 (Neon, Blob, vercel.json, env) — infra, lo hace el usuario / con aprobación.
5. Deploy preview → verificar → promover a prod.
6. Decidir datos viejos (revivir Railway 1 mes para pg_dump, o fresh).
7. Apagar/borrar Railway.
