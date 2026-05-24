"""Signup / login endpoints. Returns a JWT + the user profile."""
import secrets
import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from .. import models, schemas, auth
from ..config import settings
from ..database import get_db


def _generate_email_token(db: Session) -> str:
    """Generate a unique 12-char URL-safe token for the user's forwarding address."""
    while True:
        token = secrets.token_urlsafe(9)  # 12 base64 chars
        if not db.query(models.User).filter(models.User.email_token == token).first():
            return token

router = APIRouter(prefix="/auth", tags=["auth"])


_LOCALE_DEFAULT_CURRENCY = {
    "es": "CLP",   # Chilean default for Spanish speakers
    "pt": "BRL",
    "en": "USD",
}


def _defaults_for(locale: str | None) -> dict:
    loc = (locale or "es").lower()[:2]
    return {
        "currency": _LOCALE_DEFAULT_CURRENCY.get(loc, "USD"),
        "locale": loc if loc in _LOCALE_DEFAULT_CURRENCY else "es",
    }


def _get_or_create_user(
    db: Session, email: str, provider: str, locale: str | None = None
) -> models.User:
    """Find the user by email, or create a new passwordless one."""
    email = email.strip().lower()
    user = db.query(models.User).filter(models.User.email == email).first()
    if user:
        return user
    user = models.User(
        email=email,
        hashed_password=None,
        auth_provider=provider,
        monthly_budget=0.0,
        settings=_defaults_for(locale),
        email_token=_generate_email_token(db),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _token_response(user: models.User) -> schemas.TokenOut:
    token = auth.create_access_token(user.id)
    return schemas.TokenOut(access_token=token, user=schemas.UserOut.model_validate(user))


@router.post("/signup", response_model=schemas.TokenOut)
def signup(payload: schemas.UserCreate, db: Session = Depends(get_db)):
    if db.query(models.User).filter(models.User.email == payload.email).first():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Email already registered")
    user = models.User(
        email=payload.email,
        hashed_password=auth.hash_password(payload.password),
        monthly_budget=0.0,
        settings=_defaults_for(payload.locale),
        email_token=_generate_email_token(db),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    token = auth.create_access_token(user.id)
    return schemas.TokenOut(access_token=token, user=schemas.UserOut.model_validate(user))


@router.post("/login", response_model=schemas.TokenOut)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """OAuth2PasswordRequestForm reads `username` + `password`; we treat username=email."""
    user = db.query(models.User).filter(models.User.email == form.username).first()
    if not user or not auth.verify_password(form.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    token = auth.create_access_token(user.id)
    return schemas.TokenOut(access_token=token, user=schemas.UserOut.model_validate(user))


# -------------------------------------------------------------------
# Fast-entry options — reduce friction at sign-up.
# -------------------------------------------------------------------

class QuickLoginIn(BaseModel):
    email: EmailStr
    locale: str | None = None


@router.post("/quick", response_model=schemas.TokenOut)
def quick_login(payload: QuickLoginIn, db: Session = Depends(get_db)):
    """
    Passwordless sign-in by email. Creates an account if one doesn't exist.
    Intended for local dev and demos. For production, replace with a magic-link
    email flow (send a signed URL to the inbox, verify on click).
    """
    if not settings.allow_passwordless:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Passwordless login is disabled")
    user = _get_or_create_user(db, payload.email, provider="email", locale=payload.locale)
    return _token_response(user)


class GoogleLoginIn(BaseModel):
    credential: str   # the JWT id_token returned by Google Identity Services on the frontend
    locale: str | None = None


@router.post("/google", response_model=schemas.TokenOut)
def google_login(payload: GoogleLoginIn, db: Session = Depends(get_db)):
    """
    Verify a Google ID token and log the user in (creating an account if needed).
    The frontend obtains `credential` via Google Identity Services (one-tap / button).
    """
    if not settings.google_client_id:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Google sign-in is not configured. Set GOOGLE_CLIENT_ID in backend/.env.",
        )
    # Verify the token against Google's public keys.
    try:
        r = httpx.get(
            "https://oauth2.googleapis.com/tokeninfo",
            params={"id_token": payload.credential},
            timeout=6.0,
        )
        r.raise_for_status()
        info = r.json()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid Google token: {e}")

    if info.get("aud") != settings.google_client_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token audience mismatch")
    email = info.get("email")
    if not email or info.get("email_verified") not in ("true", True):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Google account email not verified")

    user = _get_or_create_user(db, email, provider="google", locale=payload.locale)
    return _token_response(user)


@router.get("/me", response_model=schemas.UserOut)
def me(current: models.User = Depends(auth.get_current_user)):
    return current


@router.patch("/me", response_model=schemas.UserOut)
def update_me(
    payload: dict,
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    """Update budget, settings, email, or password."""
    if "email" in payload:
        new_email = str(payload["email"]).strip().lower()
        taken = db.query(models.User).filter(
            models.User.email == new_email, models.User.id != current.id
        ).first()
        if taken:
            raise HTTPException(status.HTTP_409_CONFLICT, "Email already in use")
        current.email = new_email
    if "password" in payload and payload["password"]:
        current.hashed_password = auth.hash_password(str(payload["password"]))
    if "monthly_budget" in payload:
        current.monthly_budget = float(payload["monthly_budget"])
    if "settings" in payload and isinstance(payload["settings"], dict):
        try:
            merged_raw = {**(current.settings or {}), **payload["settings"]}
            validated = schemas.UserSettings(**merged_raw)
            current.settings = validated.model_dump()
        except Exception:
            current.settings = {**(current.settings or {}), **payload["settings"]}
    if not current.email_token:
        current.email_token = _generate_email_token(db)
    db.commit()
    db.refresh(current)
    return current
