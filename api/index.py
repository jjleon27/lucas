"""
Vercel Python Function entrypoint for the Lucas FastAPI backend.

The backend code lives unchanged in ../backend/app. Vercel bundles that tree via
`includeFiles` in vercel.json; here we put it on sys.path and expose the ASGI
`app`, which the Vercel Python runtime serves directly.

Vercel routes every /api/* request here (see vercel.json rewrites). We mount the
FastAPI app under "/api" with a Starlette Mount so the prefix is stripped before
FastAPI routing and OpenAPI/docs URLs are generated with the right root_path.

Vercel's serverless runtime does NOT run the ASGI lifespan / FastAPI startup
events, so we call init_db() here at import time (once per cold start;
create_all + the ALTERs are idempotent).
"""
import os
import sys

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from starlette.applications import Starlette  # noqa: E402
from starlette.routing import Mount  # noqa: E402

from app.main import app as _fastapi_app  # noqa: E402

try:
    from app.database import init_db

    init_db()
except Exception as _e:  # don't let a transient DB blip kill the whole function
    print(f"[api/index] init_db() failed: {type(_e).__name__}: {_e}")

app = Starlette(routes=[Mount("/api", app=_fastapi_app)])
