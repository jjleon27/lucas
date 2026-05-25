"""
Cartola (bank statement PDF) import & reconciliation.

Flow:
  POST /cartola/upload  → returns a CartolaReport:
        - list of parsed transactions with `dupe_of` marked
        - suggested_account_id (based on bank + card type in the header)
        - app vs cartola closing-balance drift (if account is known)

  POST /cartola/commit  → user confirms which transactions to save and
        which account they belong to. Optionally re-anchors the account's
        balance to the cartola's closing_balance.

This reuses services/dedupe.py (same dup logic as screenshots) and
services/accounts.py (balance computation).
"""
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.orm import Session
from datetime import date

from .. import models, schemas, auth, storage, cartola as cartola_svc
from ..ai import categorizer
from ..database import get_db
from ..services import dedupe, accounts as accounts_svc


router = APIRouter(prefix="/cartola", tags=["cartola"])


def _enrich(tx: schemas.ParsedReceipt, *, db: Session, user_id: int) -> schemas.ParsedReceipt:
    if tx.is_cc_payment:
        tx.category = "Transferencia"
        tx.is_income = False
        return tx
    if tx.category in ("Uncategorized", "Other", "Otros", ""):
        tx.category = categorizer.categorize(
            tx.merchant, tx.raw_text or tx.description or "", db=db, user_id=user_id,
        )
    return tx


@router.post("/upload", response_model=schemas.CartolaReport)
async def upload_cartola(
    file: UploadFile = File(...),
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    """Parse a cartola PDF, dedupe against existing transactions, report drift."""
    ct = (file.content_type or "").lower()
    name = (file.filename or "").lower()
    if not (ct == "application/pdf" or name.endswith(".pdf")):
        raise HTTPException(400, "Only PDF cartolas are supported here")

    data = await file.read()
    if len(data) > 30 * 1024 * 1024:
        raise HTTPException(413, "PDF too large (max 30 MB)")

    # Save the file so the user can re-view it from history if needed.
    storage.save_image(data, file.filename or "cartola.pdf")

    pr = cartola_svc.parse_cartola(data, db=db, user_id=current.id)

    user_currency = (current.settings or {}).get("currency") or "CLP"
    for t in pr.transactions:
        if not t.currency:
            t.currency = user_currency
        _enrich(t, db=db, user_id=current.id)

    # Best-guess the target account using the bank + type + last4 from the header.
    suggested_account_id = dedupe.suggest_account_for_hint(
        db, current.id, pr.account_info.bank, pr.account_info.type,
    )
    if suggested_account_id is None and pr.account_info.last4:
        # Fallback: match by last4 appearing in the account name.
        for a in (
            db.query(models.Account)
            .filter(models.Account.user_id == current.id, models.Account.archived.is_(False))
            .all()
        ):
            if pr.account_info.last4 in (a.name or ""):
                suggested_account_id = a.id
                break

    # Duplicate check — for each extracted row, see if the DB already has it.
    new_count = 0
    dup_count = 0
    for t in pr.transactions:
        dup = dedupe.find_duplicate(
            db, user_id=current.id,
            account_id=suggested_account_id, proposed=t,
        )
        if dup is not None:
            t.dupe_of = dup.id
            dup_count += 1
        else:
            new_count += 1

    # Compute the drift: cartola closing balance vs. app-computed balance for
    # the suggested account. If they differ, the commit step can offer to
    # re-anchor the account.
    app_balance: float | None = None
    drift: float | None = None
    if suggested_account_id is not None:
        acc = db.get(models.Account, suggested_account_id)
        if acc is not None:
            balances = accounts_svc.compute_account_balance(db, acc)
            if acc.type == "credit":
                app_balance = balances["current_used"]
            else:
                app_balance = balances["current_balance"]
            if pr.closing_balance is not None and app_balance is not None:
                drift = round(pr.closing_balance - app_balance, 2)

    return schemas.CartolaReport(
        bank=pr.account_info.bank,
        account_type=pr.account_info.type,
        last4=pr.account_info.last4,
        currency=pr.account_info.currency or user_currency,
        period_from=pr.period_from,
        period_to=pr.period_to,
        opening_balance=pr.opening_balance,
        closing_balance=pr.closing_balance,
        transactions=pr.transactions,
        new_count=new_count,
        duplicate_count=dup_count,
        suggested_account_id=suggested_account_id,
        app_balance=app_balance,
        drift=drift,
    )


@router.post("/commit", response_model=schemas.CartolaCommitOut)
def commit_cartola(
    body: schemas.CartolaCommitIn,
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    """
    User confirmed which transactions to save. We:
      - Skip any row with `dupe_of` set (user can override by clearing it
        before POSTing — frontend's call).
      - Save the rest to the chosen account.
      - Optionally re-anchor the account to the cartola's closing_balance.
    """
    acc = (
        db.query(models.Account)
        .filter(models.Account.id == body.account_id, models.Account.user_id == current.id)
        .first()
    )
    if acc is None:
        raise HTTPException(404, "Account not found")

    saved = 0
    skipped = 0
    for t in body.transactions:
        if t.dupe_of is not None:
            skipped += 1
            continue
        tx = models.Transaction(
            user_id=current.id,
            account_id=acc.id,
            amount=abs(t.amount),
            currency=(t.currency or acc.currency).upper(),
            category=t.category or "Other",
            date=t.date,
            merchant=t.merchant or "",
            notes=t.description or "",
            is_income=bool(t.is_income),
            raw_ocr=t.raw_text or "",
            is_transfer=bool(t.is_cc_payment),
        )
        db.add(tx)
        db.flush()
        accounts_svc.reconcile_new_transaction(db, current.id, tx)
        saved += 1

    drift: float | None = None
    if body.reconcile_to_closing_balance and body.closing_balance is not None:
        # Compute what the app would show AFTER saving, then re-anchor so the
        # live balance exactly equals the cartola's closing balance.
        db.flush()  # so the new rows count toward the computed balance below
        balances = accounts_svc.compute_account_balance(db, acc)
        current_after = (
            balances["current_used"] if acc.type == "credit"
            else balances["current_balance"]
        )
        drift = round(body.closing_balance - current_after, 2)
        acc.anchor_date = date.today()
        # For credit: anchor_balance is "what you owe". For others: cash on hand.
        acc.anchor_balance = float(body.closing_balance)
        db.add(acc)

    db.commit()
    return schemas.CartolaCommitOut(saved_count=saved, skipped_count=skipped, drift=drift)
