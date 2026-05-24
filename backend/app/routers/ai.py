"""
AI introspection endpoints.

  GET /ai/status   → which provider is live (no secrets returned)
  GET /ai/usage    → this user's token + USD spend this month
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models, auth
from ..ai import provider
from ..database import get_db
from ..services import ai_usage

router = APIRouter(prefix="/ai", tags=["ai"])


@router.get("/status")
def status():
    return {
        "available": provider.is_available(),
        "provider": provider.active_provider_name(),
    }


@router.get("/usage")
def usage(
    month: str | None = None,
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    return ai_usage.monthly_summary(db, current.id, month=month)
