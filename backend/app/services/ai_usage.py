"""
AI usage / cost tracking.

Every call into ai.provider is logged here. The dashboard + /ai/usage endpoint
read from this to show users (and us) exactly how much LLM they're burning.

Prices are rough but realistic (USD per 1M tokens, mid-2026 levels). Tune as
providers change pricing. Missing model → fallback to "unknown" so we still
show the token count even if we can't price it.
"""
from __future__ import annotations

from datetime import date, datetime
from sqlalchemy.orm import Session
from sqlalchemy import func

from .. import models


# USD per 1M tokens. (input, output)
_PRICES: dict[str, tuple[float, float]] = {
    # OpenAI
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o":      (2.50, 10.00),
    "gpt-4-turbo": (10.00, 30.00),
    # Anthropic
    "claude-haiku-4-5-20251001": (0.80, 4.00),
    "claude-sonnet-4-6":          (3.00, 15.00),
    "claude-opus-4-6":            (15.00, 75.00),
    # Google
    "gemini-1.5-flash": (0.075, 0.30),
    "gemini-1.5-pro":   (1.25, 5.00),
}


def price_for(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    key = model.lower()
    price_in, price_out = _PRICES.get(key, (0.0, 0.0))
    return (prompt_tokens * price_in + completion_tokens * price_out) / 1_000_000


def record(
    db: Session,
    *,
    user_id: int,
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    purpose: str,
) -> None:
    row = models.AiUsage(
        user_id=user_id,
        provider=provider,
        model=model,
        purpose=purpose,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    db.add(row)
    db.commit()


def monthly_summary(db: Session, user_id: int, month: str | None = None) -> dict:
    """Tokens + estimated USD cost for the current month (or a given YYYY-MM)."""
    month = month or date.today().strftime("%Y-%m")
    start = datetime.strptime(month + "-01", "%Y-%m-%d")
    rows = (
        db.query(
            models.AiUsage.provider,
            models.AiUsage.model,
            models.AiUsage.purpose,
            func.sum(models.AiUsage.prompt_tokens).label("pt"),
            func.sum(models.AiUsage.completion_tokens).label("ct"),
            func.count(models.AiUsage.id).label("calls"),
        )
        .filter(
            models.AiUsage.user_id == user_id,
            models.AiUsage.created_at >= start,
        )
        .group_by(
            models.AiUsage.provider, models.AiUsage.model, models.AiUsage.purpose,
        )
        .all()
    )
    total_cost = 0.0
    total_tokens = 0
    by_purpose: dict[str, dict] = {}
    breakdown = []
    for provider, model, purpose, pt, ct, calls in rows:
        cost = price_for(model, pt or 0, ct or 0)
        total_cost += cost
        total_tokens += (pt or 0) + (ct or 0)
        by_purpose.setdefault(purpose, {"tokens": 0, "cost_usd": 0.0, "calls": 0})
        by_purpose[purpose]["tokens"] += (pt or 0) + (ct or 0)
        by_purpose[purpose]["cost_usd"] += cost
        by_purpose[purpose]["calls"] += calls
        breakdown.append({
            "provider": provider, "model": model, "purpose": purpose,
            "prompt_tokens": int(pt or 0), "completion_tokens": int(ct or 0),
            "calls": int(calls), "cost_usd": round(cost, 6),
        })
    return {
        "month": month,
        "total_tokens": int(total_tokens),
        "total_cost_usd": round(total_cost, 6),
        "by_purpose": by_purpose,
        "breakdown": breakdown,
    }
