"""Dashboard summary + chat endpoint."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import models, schemas, auth
from ..ai import predictor, alerts, chat as chat_ai
from ..database import get_db
from ..services import accounts as account_svc

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", response_model=schemas.DashboardOut)
def dashboard(
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    summary = predictor.summarize(db, current)
    # Pass the user's preferred currency so alert messages can format correctly
    summary["currency"] = (current.settings or {}).get("currency", "")
    summary["alerts"] = alerts.build_alerts(summary)

    # Per-account snapshot
    accs = (
        db.query(models.Account)
        .filter(models.Account.user_id == current.id, models.Account.archived.is_(False))
        .order_by(models.Account.created_at.asc())
        .all()
    )
    account_summaries = []
    for a in accs:
        bal = account_svc.compute_account_balance(db, a)
        account_summaries.append({
            "id": a.id,
            "name": a.name,
            "bank": a.bank,
            "type": a.type,
            "color": a.color,
            "currency": a.currency,
            "current_balance": bal["current_balance"],
            "current_used": bal["current_used"],
            "credit_limit": a.credit_limit,
            "available_credit": bal["available_credit"],
        })
    summary["accounts"] = account_summaries
    summary["pending_transfers"] = account_svc.count_pending_cc_payments(db, current.id)

    # Count transactions waiting for user review (from email import)
    summary["pending_review_count"] = (
        db.query(models.Transaction)
        .filter(
            models.Transaction.user_id == current.id,
            models.Transaction.status == "pending_review",
        )
        .count()
    )
    return summary


# ---------- Chat ----------
class ChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class ChatIn(BaseModel):
    message: str
    history: list[ChatMessage] = []


class ChatOut(BaseModel):
    reply: str


@router.post("/chat", response_model=ChatOut)
def chat_endpoint(
    payload: ChatIn,
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    reply = chat_ai.answer(
        db, current, payload.message,
        history=[m.model_dump() for m in payload.history[-8:]],
    )
    return ChatOut(reply=reply)


# ---------- Action chat (FAB) ----------
class ActionOut(BaseModel):
    reply: str
    action_type: str | None = None   # "add_transaction" | "navigate" | "start_split" | None
    action_data: dict | None = None  # parsed from LLM response


@router.post("/chat/action", response_model=ActionOut)
def chat_action_endpoint(
    payload: ChatIn,
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    """
    Extended chat for the Lucas FAB — same as /chat but the LLM also detects
    intent and returns a structured action so the frontend can offer a one-tap
    confirmation (e.g. "Guardar gasto $5.000 en Almuerzo").
    """
    import json as _json
    from datetime import date as _date

    reply_raw = chat_ai.answer_with_action(
        db, current, payload.message,
        history=[m.model_dump() for m in payload.history[-6:]],
    )

    # The model appends "ACTION:{...}" at the very end. Split it out.
    action_type = None
    action_data = None
    reply_text = reply_raw

    marker = "\nACTION:"
    if marker in reply_raw:
        parts = reply_raw.rsplit(marker, 1)
        reply_text = parts[0].strip()
        try:
            obj = _json.loads(parts[1].strip())
            if obj and obj.get("type") and obj["type"] != "null":
                action_type = obj["type"]
                action_data = obj.get("data") or {}
                # Inject today's date if missing
                if action_type in ("add_transaction", "start_split"):
                    action_data.setdefault("date", _date.today().isoformat())
                    action_data.setdefault("currency", "CLP")
        except Exception:
            pass  # malformed JSON — just show the reply

    return ActionOut(reply=reply_text, action_type=action_type, action_data=action_data)
