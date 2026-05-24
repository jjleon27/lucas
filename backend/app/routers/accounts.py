"""
CRUD for Accounts + endpoints for transfer linking.

Each user can have many accounts (Santander débito, CMR Falabella crédito,
Banco de Chile crédito, etc.). Every transaction can optionally be associated
with one account.
"""
from datetime import date as date_type
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from .. import models, schemas, auth, storage
from ..database import get_db
from ..services import accounts as account_svc

router = APIRouter(prefix="/accounts", tags=["accounts"])


def _to_out(db: Session, acc: models.Account) -> schemas.AccountOut:
    bal = account_svc.compute_account_balance(db, acc)
    out = schemas.AccountOut.model_validate(acc)
    out.current_balance = bal["current_balance"]
    out.current_used = bal["current_used"]
    out.available_credit = bal["available_credit"]
    return out


@router.get("", response_model=list[schemas.AccountOut])
def list_accounts(
    include_archived: bool = False,
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(models.Account).filter(models.Account.user_id == current.id)
    if not include_archived:
        q = q.filter(models.Account.archived.is_(False))
    accs = q.order_by(models.Account.created_at.asc()).all()
    return [_to_out(db, a) for a in accs]


@router.post("", response_model=schemas.AccountOut, status_code=201)
def create_account(
    payload: schemas.AccountCreate,
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    if payload.type not in schemas.ACCOUNT_TYPES:
        raise HTTPException(400, f"type must be one of {schemas.ACCOUNT_TYPES}")
    acc = models.Account(
        user_id=current.id,
        name=payload.name.strip()[:80],
        bank=(payload.bank or "").strip()[:80],
        type=payload.type,
        currency=payload.currency or "CLP",
        color=payload.color or "#6366f1",
        icon=payload.icon or "card",
        credit_limit=float(payload.credit_limit or 0.0),
        anchor_date=payload.anchor_date,
        anchor_balance=float(payload.anchor_balance or 0.0),
    )
    db.add(acc)
    db.commit()
    db.refresh(acc)
    return _to_out(db, acc)


@router.patch("/{acc_id}", response_model=schemas.AccountOut)
def update_account(
    acc_id: int,
    payload: schemas.AccountUpdate,
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    acc = db.query(models.Account).filter(
        models.Account.id == acc_id, models.Account.user_id == current.id
    ).first()
    if not acc:
        raise HTTPException(404, "Account not found")
    for f, v in payload.model_dump(exclude_none=True).items():
        if f == "type" and v not in schemas.ACCOUNT_TYPES:
            raise HTTPException(400, f"type must be one of {schemas.ACCOUNT_TYPES}")
        setattr(acc, f, v)
    db.commit()
    db.refresh(acc)
    return _to_out(db, acc)


@router.delete("/{acc_id}", status_code=204)
def delete_account(
    acc_id: int,
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    acc = db.query(models.Account).filter(
        models.Account.id == acc_id, models.Account.user_id == current.id
    ).first()
    if not acc:
        raise HTTPException(404, "Account not found")
    # Detach transactions instead of deleting them.
    db.query(models.Transaction).filter(models.Transaction.account_id == acc_id).update(
        {models.Transaction.account_id: None}
    )
    db.delete(acc)
    db.commit()


# ---------- Card image upload ----------
@router.post("/{acc_id}/card-image", response_model=schemas.AccountOut)
async def upload_card_image(
    acc_id: int,
    file: UploadFile = File(...),
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    """Upload a photo of the physical/virtual card. Stored and URL saved on the account."""
    acc = db.query(models.Account).filter(
        models.Account.id == acc_id, models.Account.user_id == current.id
    ).first()
    if not acc:
        raise HTTPException(404, "Account not found")

    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(413, "Imagen muy grande (máx 10 MB)")

    url = storage.save_image(data, file.filename or f"card_{acc_id}.jpg")
    acc.card_image_url = url
    db.commit()
    db.refresh(acc)
    return _to_out(db, acc)


# ---------- Reconciliation ----------
@router.post("/{acc_id}/reconcile", response_model=schemas.ReconcileOut)
def reconcile_account(
    acc_id: int,
    payload: schemas.ReconcileIn,
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    """
    Re-anchor an account to match what the bank says right now.

    The app's computed balance can drift from reality when:
      - A transaction was missed (cash expense, transfer, interest charge).
      - An OCR parse rounded or double-counted.
      - A recurring cargo wasn't categorised or linked.

    Rather than hunting down the missing rows, the user gives us the bank's
    current balance and we "snap" the anchor so that, from this moment on,
    live_balance = expected.
    """
    acc = db.query(models.Account).filter(
        models.Account.id == acc_id, models.Account.user_id == current.id
    ).first()
    if not acc:
        raise HTTPException(404, "Account not found")

    as_of = payload.as_of_date or date_type.today()

    # What did the app think the balance was, right now? (Before we snap.)
    bal = account_svc.compute_account_balance(db, acc)
    current_app_balance = (
        bal["current_used"] if acc.type == "credit" else bal["current_balance"]
    )
    drift = round(payload.expected_balance - current_app_balance, 2)

    previous_anchor_balance = float(acc.anchor_balance)
    previous_anchor_date = acc.anchor_date

    # Snap: set a fresh anchor on the given date. From now on the live balance
    # formula starts from `expected` and adds transactions dated >= as_of.
    acc.anchor_date = as_of
    acc.anchor_balance = float(payload.expected_balance)
    db.commit()
    db.refresh(acc)

    return schemas.ReconcileOut(
        account_id=acc.id,
        previous_anchor_balance=previous_anchor_balance,
        previous_anchor_date=previous_anchor_date,
        new_anchor_balance=acc.anchor_balance,
        new_anchor_date=acc.anchor_date,
        drift=drift,
    )


# ---------- Transfer linking ----------
@router.get("/transfer/suggest/{tx_id}", response_model=list[schemas.TransactionOut])
def suggest_transfer_match(
    tx_id: int,
    window_days: int = 10,
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    """
    Return up to 10 candidate sibling transactions for manually linking
    a pending CC payment / transfer. The search is wider than the auto-link
    (default ±10 days, looser amount tolerance) because the user is here
    precisely because auto-link didn't fire.
    """
    from datetime import timedelta

    tx = db.query(models.Transaction).filter(
        models.Transaction.id == tx_id,
        models.Transaction.user_id == current.id,
    ).first()
    if not tx:
        raise HTTPException(404, "Transaction not found")
    if tx.linked_transaction_id:
        return []

    lo = tx.date - timedelta(days=window_days)
    hi = tx.date + timedelta(days=window_days)
    if (tx.currency or "CLP").upper() == "CLP":
        amt_lo, amt_hi = tx.amount - 1.0, tx.amount + 1.0
    else:
        tol = max(tx.amount * 0.02, 0.05)
        amt_lo, amt_hi = tx.amount - tol, tx.amount + tol

    rows = (
        db.query(models.Transaction)
        .filter(
            models.Transaction.user_id == current.id,
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
    # Sort by proximity to tx.date in Python — portable across SQLite/Postgres.
    rows.sort(key=lambda r: abs((r.date - tx.date).days))
    return rows[:10]


@router.post("/transfer/link", status_code=204)
def link_transfer(
    payload: schemas.LinkTransferIn,
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    """Manually link two transactions as an internal transfer."""
    a = db.query(models.Transaction).filter(
        models.Transaction.id == payload.a_id, models.Transaction.user_id == current.id
    ).first()
    b = db.query(models.Transaction).filter(
        models.Transaction.id == payload.b_id, models.Transaction.user_id == current.id
    ).first()
    if not a or not b:
        raise HTTPException(404, "One or both transactions not found")
    account_svc.link_as_transfer(db, a, b)
    db.commit()


@router.post("/transfer/unlink/{tx_id}", status_code=204)
def unlink_transfer(
    tx_id: int,
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    tx = db.query(models.Transaction).filter(
        models.Transaction.id == tx_id, models.Transaction.user_id == current.id
    ).first()
    if not tx:
        raise HTTPException(404, "Transaction not found")
    if tx.linked_transaction_id:
        other = db.query(models.Transaction).filter(
            models.Transaction.id == tx.linked_transaction_id
        ).first()
        if other:
            other.linked_transaction_id = None
            other.is_transfer = False
    tx.linked_transaction_id = None
    tx.is_transfer = False
    db.commit()
