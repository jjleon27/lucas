"""
Duplicate detection for uploaded transactions.

Scenario: user uploads today's bank-app screenshot. Tomorrow uploads another,
but yesterday's transactions are still visible in the feed. We must NOT add
them twice.

Heuristic (safe default):
  A proposed transaction is a duplicate of an existing DB row when, for the
  same user (and same account if the account is known):
    - abs(amount) matches within tolerance (CLP: ±0.5; others: ±0.5%)
    - date is within ±2 days
    - is_income flag matches
    - merchant strings overlap (Jaccard on lowercased tokens ≥ 0.5)
      OR either description is a substring of the other

We return a POTENTIAL match (the ID), not a hard block — the UI asks the
user to confirm. False positives are cheap, false negatives are expensive.
"""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Optional

from sqlalchemy.orm import Session

from .. import models, schemas


_TOKEN_RE = re.compile(r"[a-z0-9áéíóúñü]+", re.I)


def _tokens(s: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(s or "") if len(t) >= 2}


def _merchant_similar(a: str, b: str) -> bool:
    a = (a or "").strip().lower()
    b = (b or "").strip().lower()
    if not a or not b:
        return False
    if a == b:
        return True
    if a in b or b in a:
        return True
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    inter = len(ta & tb)
    union = len(ta | tb)
    return union > 0 and inter / union >= 0.5


def find_duplicate(
    db: Session,
    *,
    user_id: int,
    account_id: Optional[int],
    proposed: schemas.ParsedReceipt,
) -> Optional[models.Transaction]:
    """Returns an existing tx that's likely the same as the one proposed."""
    q = db.query(models.Transaction).filter(
        models.Transaction.user_id == user_id,
        models.Transaction.is_income.is_(proposed.is_income),
        models.Transaction.date >= proposed.date - timedelta(days=2),
        models.Transaction.date <= proposed.date + timedelta(days=2),
    )
    if account_id is not None:
        # When we know the account, only consider rows on the same account.
        q = q.filter(models.Transaction.account_id == account_id)

    # Amount tolerance
    amt = abs(proposed.amount)
    if (proposed.currency or "").upper() == "CLP":
        tol = 0.5
    else:
        tol = max(amt * 0.005, 0.01)
    q = q.filter(
        models.Transaction.amount >= amt - tol,
        models.Transaction.amount <= amt + tol,
    )

    for cand in q.all():
        if _merchant_similar(cand.merchant, proposed.merchant) or \
           _merchant_similar(cand.merchant, proposed.description) or \
           _merchant_similar(cand.notes, proposed.description):
            return cand
    return None


def suggest_account_for_hint(
    db: Session, user_id: int, bank_hint: str, account_type_hint: str,
) -> Optional[int]:
    """Best-guess which of the user's accounts this image belongs to."""
    if not bank_hint and not account_type_hint:
        return None
    q = db.query(models.Account).filter(
        models.Account.user_id == user_id,
        models.Account.archived.is_(False),
    )
    candidates = q.all()
    if not candidates:
        return None

    bh = (bank_hint or "").strip().lower()
    th = (account_type_hint or "").strip().lower()

    # Score: +2 if bank name matches, +1 if type matches.
    scored: list[tuple[int, models.Account]] = []
    for a in candidates:
        score = 0
        if bh and bh in (a.bank or "").lower():
            score += 2
        if bh and bh in (a.name or "").lower():
            score += 2
        if th and th == (a.type or "").lower():
            score += 1
        if score > 0:
            scored.append((score, a))

    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1].id
