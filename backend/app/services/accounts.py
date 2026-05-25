"""
Account balance computation + transfer detection.

Live balance for a debit / savings / wallet account:
    balance = anchor_balance
              + sum(income transactions on this account, since anchor_date)
              − sum(expense transactions on this account, since anchor_date)

For a credit card:
    used    = anchor_balance     # we use anchor_balance as "what you owe"
              + sum(expenses on the card, since anchor_date)
              − sum(income on the card, since anchor_date)   # i.e. payments received

If anchor_date is None we treat all transactions as "since the beginning".
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable

from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models


# Heuristic: rows whose merchant matches these are likely a credit-card payment.
import re

_CC_PAYMENT_RE = re.compile(
    r"(?i)\b(pago\s*tarjeta|pago\s*recibido|pago\s*cmr|pago\s*falabella|"
    r"pago\s*credit|pago\s*tc|abono\s*tarjeta|abono\s*cuenta|"
    r"transferencia\s*recibida|transferencia\s*enviada|"
    r"credit\s*card\s*payment|cc\s*payment|payment\s*received)\b"
)


def looks_like_cc_payment(merchant: str) -> bool:
    return bool(_CC_PAYMENT_RE.search(merchant or ""))


def _filter_since(q, account_id: int, since: date | None):
    q = q.filter(
        models.Transaction.account_id == account_id,
        models.Transaction.status == "confirmed",
    )
    if since:
        q = q.filter(models.Transaction.date >= since)
    return q


def compute_account_balance(db: Session, account: models.Account) -> dict:
    """
    Returns a dict with `current_balance`, `current_used`, `available_credit`
    depending on the account type.
    """
    since = account.anchor_date
    income_q = _filter_since(
        db.query(func.coalesce(func.sum(models.Transaction.amount), 0.0))
          .filter(models.Transaction.is_income.is_(True)),
        account.id, since,
    )
    expense_q = _filter_since(
        db.query(func.coalesce(func.sum(models.Transaction.amount), 0.0))
          .filter(models.Transaction.is_income.is_(False)),
        account.id, since,
    )
    income = float(income_q.scalar() or 0.0)
    expense = float(expense_q.scalar() or 0.0)

    out = {"current_balance": 0.0, "current_used": 0.0, "available_credit": 0.0}

    if account.type == "credit":
        used = float(account.anchor_balance) + expense - income
        used = max(used, 0.0)  # never negative
        out["current_used"] = round(used, 2)
        out["available_credit"] = round(max(account.credit_limit - used, 0.0), 2)
    else:
        # debit / savings / wallet / cash
        balance = float(account.anchor_balance) + income - expense
        out["current_balance"] = round(balance, 2)

    return out


def find_transfer_match(
    db: Session, user_id: int, tx: models.Transaction, window_days: int = 4,
) -> models.Transaction | None:
    """
    Given a transaction that *looks like* a credit-card payment, find a sibling
    transaction on a different account with the opposite is_income, the same
    absolute amount, within ±window_days. Returns the match or None.

    Match criteria:
      - same user
      - different account
      - opposite is_income (one side spends, the other receives)
      - amount within ±0.5 (CLP) or ±0.5% (other currencies)
      - date within ±window_days
      - candidate is not already linked
      - the merchant of at least one of the two looks like a CC payment
    """
    if not tx.amount or not tx.account_id:
        return None
    # Trigger when either the merchant clearly looks like a CC payment, OR
    # the caller already flagged it as is_transfer (e.g. the parser saw
    # is_cc_payment: true in the screenshot / cartola header).
    if not (looks_like_cc_payment(tx.merchant) or tx.is_transfer):
        return None

    lo = tx.date - timedelta(days=window_days)
    hi = tx.date + timedelta(days=window_days)

    if tx.currency.upper() == "CLP":
        amt_lo, amt_hi = tx.amount - 0.5, tx.amount + 0.5
    else:
        tol = max(tx.amount * 0.005, 0.01)
        amt_lo, amt_hi = tx.amount - tol, tx.amount + tol

    candidates = (
        db.query(models.Transaction)
        .filter(
            models.Transaction.user_id == user_id,
            models.Transaction.id != tx.id,
            models.Transaction.account_id.isnot(None),
            models.Transaction.account_id != tx.account_id,
            models.Transaction.is_income.is_(not tx.is_income),
            models.Transaction.amount >= amt_lo,
            models.Transaction.amount <= amt_hi,
            models.Transaction.date >= lo,
            models.Transaction.date <= hi,
            models.Transaction.linked_transaction_id.is_(None),
        )
        .all()
    )
    if not candidates:
        return None
    # Sort by proximity in Python — portable across SQLite (tests) and Postgres.
    candidates.sort(key=lambda r: abs((r.date - tx.date).days))
    return candidates[0]


def link_as_transfer(db: Session, a: models.Transaction, b: models.Transaction) -> None:
    """Mark two transactions as a single internal transfer."""
    a.is_transfer = True
    b.is_transfer = True
    a.linked_transaction_id = b.id
    b.linked_transaction_id = a.id
    db.add(a)
    db.add(b)
    db.flush()


def reconcile_new_transaction(
    db: Session, user_id: int, tx: models.Transaction,
) -> models.Transaction | None:
    """
    Try to auto-link `tx` as the other half of a transfer pair.
    Returns the linked counterpart if a link was made, else None.
    """
    if tx.is_transfer or tx.linked_transaction_id:
        return None
    match = find_transfer_match(db, user_id, tx)
    if match:
        link_as_transfer(db, tx, match)
        db.commit()
        return match
    return None


def count_pending_cc_payments(db: Session, user_id: int) -> int:
    """
    Unlinked transactions that should appear in the pending-transfers list:
    those with is_transfer=True (parser-flagged but auto-link missed) OR
    whose merchant matches the CC-payment heuristic.
    Must match the filter logic in routers/transactions.py pending_transfers branch.
    """
    rows = (
        db.query(models.Transaction)
        .filter(
            models.Transaction.user_id == user_id,
            models.Transaction.linked_transaction_id.is_(None),
        )
        .all()
    )
    return sum(1 for r in rows if r.is_transfer or looks_like_cc_payment(r.merchant))
