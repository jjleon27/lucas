"""
FastAPI entrypoint. Wires together routers, CORS, static file serving for
uploaded images, and DB init.
"""
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import settings
from .database import init_db
from .routers import auth as auth_router
from .routers import upload as upload_router
from .routers import transactions as tx_router
from .routers import split as split_router
from .routers import bills as bills_router
from .routers import dashboard as dash_router
from .routers import accounts as accounts_router
from .routers import ai as ai_router
from .routers import cartola as cartola_router
from .routers import voice as voice_router
from .routers import email as email_router
from .routers import chat as chat_router
from .rate_limit import limiter
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

app = FastAPI(
    title="LUCAS API",
    description="Screenshot-first AI personal finance assistant",
    version="0.1.0",
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_origin_regex=r"https://.*\.(vercel\.app|railway\.app|up\.railway\.app)",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    _security_checks()
    init_db()
    # Make sure the local uploads directory exists before we try to mount it.
    if settings.storage_backend == "local":
        Path(settings.local_storage_dir).mkdir(parents=True, exist_ok=True)


def _security_checks() -> None:
    log = logging.getLogger("lucas.security")
    if settings.jwt_secret == "dev-secret-change-me":
        log.critical(
            "JWT_SECRET is set to the insecure default. "
            "Set JWT_SECRET to a random secret in production."
        )
    if settings.allow_passwordless:
        log.warning(
            "ALLOW_PASSWORDLESS=True — anyone can log in with just an email. "
            "Set ALLOW_PASSWORDLESS=False in production."
        )


# Serve uploaded receipts back to the frontend when using local storage.
if settings.storage_backend == "local":
    Path(settings.local_storage_dir).mkdir(parents=True, exist_ok=True)
    app.mount("/files", StaticFiles(directory=settings.local_storage_dir), name="files")


@app.get("/health")
def health():
    return {"ok": True, "service": "lucas", "version": app.version}


app.include_router(auth_router.router)
app.include_router(upload_router.router)
app.include_router(tx_router.router)
app.include_router(split_router.router)
app.include_router(bills_router.router)
app.include_router(dash_router.router)
app.include_router(accounts_router.router)
app.include_router(ai_router.router)
app.include_router(cartola_router.router)
app.include_router(voice_router.router)
app.include_router(email_router.router)
app.include_router(chat_router.router)
