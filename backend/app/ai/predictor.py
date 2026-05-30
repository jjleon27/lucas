"""
Monthly prediction + daily safe-spend calculator.

Architecture (variable-income aware):
  - Users may have irregular income (freelancers, performers, contractors, etc.)
  - We separate two modes:
      * "Real" mode:  based only on income already received this month.
        safe_spend_actual = (income_actual - spent) / days_remaining
        Conservative: only spends what's confirmed in the bank.
      * "Projected" mode: based on the user's income target for the month.
        safe_spend_projected = (income_target - spent) / days_remaining
        Optimistic: assumes the rest of the projected income will come in.
  - income_target comes from user.settings["income_target"], falling back to
    monthly_budget, falling back to the historical 3-month average.
  - historical_avg_income: average total monthly income over last 3 full months.
    Surfaces in the UI as a suggestion when the user sets their income target.

Spend projection (status/danger zone):
  - Blended linear + trailing-30d projection of spending, compared against
    income_target (preferred) or monthly_budget as the upper limit.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta

from sqlalchemy import func, extract
from sqlalchemy.orm import Session

from .. import models


def _month_bounds(today: date) -> tuple[date, date, int]:
    first = today.replace(day=1)
    last_day = monthrange(today.year, today.month)[1]
    last = today.replace(day=last_day)
    return first, last, last_day


def _sum(db: Session, user_id: int, start: date, end: date, income: bool) -> float:
    """Sum monetary movements EXCLUDING transfers and pending_review transactions."""
    q = db.query(func.coalesce(func.sum(models.Transaction.amount), 0.0)).filter(
        models.Transaction.user_id == user_id,
        models.Transaction.date >= start,
        models.Transaction.date <= end,
        models.Transaction.is_income.is_(income),
        models.Transaction.is_transfer.is_(False),
        # Exclude pending_review: user hasn't confirmed them yet
        models.Transaction.status != "pending_review",
    )
    return float(q.scalar() or 0.0)


def _trailing_avg_daily(db: Session, user_id: int, today: date, window_days: int = 30) -> float:
    start = today - timedelta(days=window_days)
    total = _sum(db, user_id, start, today, income=False)
    return total / max(window_days, 1)


def _avg_monthly_income(db: Session, user_id: int, today: date, months: int = 3) -> float:
    """
    Average total income per full calendar month over the last `months` months.
    Returns 0.0 if no history.
    """
    totals: list[float] = []
    for i in range(1, months + 1):
        # Walk back month by month
        y = today.year
        m = today.month - i
        while m <= 0:
            m += 12
            y -= 1
        first = date(y, m, 1)
        last = date(y, m, monthrange(y, m)[1])
        t = _sum(db, user_id, first, last, income=True)
        if t > 0:
            totals.append(t)
    return round(sum(totals) / len(totals), 2) if totals else 0.0


def summarize(db: Session, user: models.User, today: date | None = None) -> dict:
    today = today or date.today()
    first, last, days_in_month = _month_bounds(today)
    days_elapsed = (today - first).days + 1
    days_remaining = days_in_month - days_elapsed  # may be 0 on last day

    # ── Actual numbers (transactions already in the DB) ──────────────────────
    spent = _sum(db, user.id, first, today, income=False)
    income_actual = _sum(db, user.id, first, today, income=True)

    # ── User settings ─────────────────────────────────────────────────────────
    settings = user.settings or {}
    income_target: float = float(settings.get("income_target") or 0)

    # Fixed monthly expenses (rent, phone, gym, subscriptions…)
    raw_fixed: list = settings.get("fixed_expenses") or []
    fixed_expenses = [
        {
            "name": str(fe.get("name", "")),
            "amount": float(fe.get("amount", 0)),
            "day": int(fe["day"]) if fe.get("day") is not None else 1,
        }
        for fe in raw_fixed if isinstance(fe, dict)
    ]
    fixed_total = sum(fe["amount"] for fe in fixed_expenses)

    # Fixed monthly incomes (salary, pension, rental income…)
    raw_fixed_incomes: list = settings.get("fixed_incomes") or []
    fixed_incomes = [
        {
            "name": str(fi.get("name", "")),
            "amount": float(fi.get("amount", 0)),
            "day": int(fi["day"]) if fi.get("day") is not None else 1,
        }
        for fi in raw_fixed_incomes if isinstance(fi, dict)
    ]

    # Historical average — always computed (shown as a hint in the UI)
    historical_avg_income = _avg_monthly_income(db, user.id, today, months=3)

    # Resolve income_target: user setting → monthly_budget → historical average
    if income_target <= 0:
        income_target = float(user.monthly_budget or 0)
    if income_target <= 0 and historical_avg_income > 0:
        income_target = historical_avg_income

    # ── Spend projection ─────────────────────────────────────────────────────
    linear = (spent / days_elapsed) * days_in_month if days_elapsed else spent
    trailing = _trailing_avg_daily(db, user.id, today) * days_in_month
    w = min(days_elapsed / 15, 1.0)
    projected_spend = w * linear + (1 - w) * trailing

    # ── Variable budget (what's left after fixed costs) ───────────────────────
    # variable_budget = income_target - fixed_total
    # safe_spend looks at variable budget vs actual spending
    variable_budget = max(income_target - fixed_total, 0.0)

    # ── Legacy / backward-compat budget ──────────────────────────────────────
    budget = float(user.monthly_budget or 0.0)
    effective_limit = income_target or budget
    remaining_budget = max(effective_limit - spent, 0.0)

    # ── Safe daily spend ──────────────────────────────────────────────────────
    dr = max(days_remaining, 1)

    # Conservative: only what's actually arrived minus what's spent
    available_actual = max(income_actual - fixed_total - spent, 0.0)
    safe_spend_actual = available_actual / dr

    # Optimistic: assumes full income_target will arrive
    available_projected = max(variable_budget - spent, 0.0)
    safe_spend_projected = available_projected / dr

    # Legacy field → use projected if target is set, else raw budget
    daily_safe = safe_spend_projected if income_target > 0 else (remaining_budget / dr)

    # ── Status (danger zone based on variable budget) ─────────────────────────
    # Compare projected spending against the variable envelope
    spend_ceiling = variable_budget if variable_budget > 0 else effective_limit
    if spend_ceiling <= 0:
        status = "good"
    elif projected_spend <= spend_ceiling:
        status = "good"
    elif projected_spend <= spend_ceiling * 1.2:
        status = "warning"
    else:
        status = "danger"

    # ── Category breakdown ────────────────────────────────────────────────────
    by_cat_q = (
        db.query(models.Transaction.category, func.sum(models.Transaction.amount))
        .filter(
            models.Transaction.user_id == user.id,
            models.Transaction.date >= first,
            models.Transaction.date <= today,
            models.Transaction.is_income.is_(False),
            models.Transaction.is_transfer.is_(False),
        )
        .group_by(models.Transaction.category)
        .order_by(func.sum(models.Transaction.amount).desc())
        .all()
    )
    by_category = [{"category": c or "Other", "total": float(t)} for c, t in by_cat_q]

    return {
        # ── Legacy / existing ──
        "month": today.strftime("%Y-%m"),
        "monthly_budget": budget,
        "total_spent": spent,
        "total_income": income_actual,
        "remaining": remaining_budget,
        "daily_safe_spend": round(daily_safe, 2),
        "predicted_end_of_month": round(projected_spend, 2),
        "status": status,
        "by_category": by_category,
        # ── Income-aware fields ──
        "income_actual": round(income_actual, 2),
        "income_target": round(income_target, 2),
        "historical_avg_income": round(historical_avg_income, 2),
        "safe_spend_actual": round(safe_spend_actual, 2),
        "safe_spend_projected": round(safe_spend_projected, 2),
        "days_remaining": days_remaining,
        "days_elapsed": days_elapsed,
        "days_in_month": days_in_month,
        # ── Fixed vs variable budget ──
        "fixed_expenses": fixed_expenses,
        "fixed_incomes": fixed_incomes,
        "fixed_total": round(fixed_total, 2),
        "variable_budget": round(variable_budget, 2),
    }
