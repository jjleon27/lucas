"""
Rule-based alert engine. Given the summary from predictor.summarize(), emit
short, human-friendly messages the UI can surface.
Alerts are in Spanish (neutral/Chilean) — avoid Argentine voseo.
"""
from __future__ import annotations


def _fmt(value: float, decimals: int = 0) -> str:
    """Format number with comma thousands separator (no currency symbol)."""
    return f"{value:,.{decimals}f}"


def build_alerts(summary: dict) -> list[str]:
    alerts: list[str] = []

    budget = summary["monthly_budget"] or 0
    spent = summary["total_spent"]
    projected = summary["predicted_end_of_month"]
    status = summary["status"]
    daily_safe = summary["daily_safe_spend"]
    currency = summary.get("currency", "")

    sym = f"{currency} " if currency else "$"

    if budget > 0:
        pct = spent / budget * 100
        if status == "danger":
            over = projected - budget
            alerts.append(
                f"⚠️ A este ritmo vas a pasarte del presupuesto en {sym}{_fmt(over, 0)} — "
                f"tu gasto diario seguro es {sym}{_fmt(daily_safe, 0)} por día."
            )
        elif status == "warning":
            alerts.append(
                f"🟡 Llevas el {pct:.0f}% del presupuesto — modera un poco para "
                f"mantenerte en {sym}{_fmt(daily_safe, 0)}/día."
            )
        else:
            alerts.append(
                f"🟢 Vas bien — con {sym}{_fmt(daily_safe, 0)}/día te cuadra el mes."
            )

    # Category-specific nudges
    by_cat = summary.get("by_category", [])
    if by_cat and spent > 0:
        top = by_cat[0]
        share = top["total"] / spent
        if share >= 0.4 and top["category"] in {"Alimentación", "Food", "Entretenimiento", "Entertainment", "Compras", "Shopping"}:
            alerts.append(
                f"🍔 {top['category']} es el {share*100:.0f}% de este mes — "
                f"puede valer la pena revisarlo."
            )

    return alerts
