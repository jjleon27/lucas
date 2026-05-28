"""
Chat endpoints — natural-language Q&A against the user's own transaction data.

POST /chat         → plain answer (used by the chat page)
POST /chat/action  → answer + structured intent (used by the FAB / voice flow)
"""
import json
import re

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import auth, models
from ..ai import chat as chat_ai
from ..database import get_db

router = APIRouter(prefix="/chat", tags=["chat"])

_ACTION_RE = re.compile(r"\nACTION:(\{.*?\})\s*$", re.DOTALL)


class ChatRequest(BaseModel):
    message: str
    history: list[dict] = []


class ChatResponse(BaseModel):
    reply: str


class ActionResponse(BaseModel):
    reply: str
    action_type: str | None = None
    action_data: dict | None = None


@router.post("", response_model=ChatResponse)
def chat_endpoint(
    payload: ChatRequest,
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    reply = chat_ai.answer(db, current, payload.message, payload.history)
    return ChatResponse(reply=reply)


@router.post("/action", response_model=ActionResponse)
def chat_action_endpoint(
    payload: ChatRequest,
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    raw = chat_ai.answer_with_action(db, current, payload.message, payload.history)

    reply = raw
    action_type: str | None = "null"
    action_data: dict | None = None

    m = _ACTION_RE.search(raw)
    if m:
        reply = raw[: m.start()].strip()
        try:
            obj = json.loads(m.group(1))
            action_type = obj.get("type") or "null"
            action_data = obj.get("data") or None
        except (json.JSONDecodeError, ValueError):
            pass

    return ActionResponse(reply=reply, action_type=action_type, action_data=action_data)
