"""CRUD for transactions + receipt items."""
from datetime import date as date_type, datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas, auth
from ..ai import categorizer
from ..database import get_db
from ..services import accounts as account_svc

router = APIRouter(prefix="/transactions", tags=["transactions"])


@router.get("", response_model=list[schemas.TransactionOut])
def list_transactions(
    limit: int = 100,
    offset: int = 0,
    account_id: int | None = None,
    pending_transfers: bool = False,
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    q = (
        db.query(models.Transaction)
        .filter(models.Transaction.user_id == current.id)
    )
    if account_id is not None:
        q = q.filter(models.Transaction.account_id == account_id)
    q = q.order_by(models.Transaction.date.desc(), models.Transaction.id.desc())

    if pending_transfers:
        # Everything unlinked; the CC-payment filter below is Python-side
        # because portable regex across SQLite/Postgres is a pain.
        rows = (
            q.filter(models.Transaction.linked_transaction_id.is_(None)).all()
        )
        pending = [
            r for r in rows
            if r.is_transfer or account_svc.looks_like_cc_payment(r.merchant)
        ]
        return pending[offset:offset + min(limit, 500)]

    return q.offset(offset).limit(min(limit, 500)).all()


@router.post("", response_model=schemas.TransactionOut, status_code=201)
def create_transaction(
    payload: schemas.TransactionCreate,
    image_url: str = "",          # query param — NOT a body field
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    # ── Exact-duplicate guard (architecture fix #5) ─────────────────────────
    # Block double-submissions: same user + date + amount + merchant created
    # within the last 60 seconds. Catches accidental double-taps / retries.
    _sixty_ago = datetime.utcnow() - timedelta(seconds=60)
    _dupe = db.query(models.Transaction).filter(
        models.Transaction.user_id == current.id,
        models.Transaction.date == payload.date,
        models.Transaction.amount == payload.amount,
        models.Transaction.is_income == payload.is_income,
        models.Transaction.merchant == (payload.merchant or ""),
        models.Transaction.created_at >= _sixty_ago,
    ).first()
    if _dupe:
        raise HTTPException(
            409,
            {"detail": "duplicate_transaction", "existing_id": _dupe.id},
        )

    # Validate account belongs to user, if provided
    if payload.account_id is not None:
        owns = db.query(models.Account).filter(
            models.Account.id == payload.account_id,
            models.Account.user_id == current.id,
        ).first()
        if not owns:
            raise HTTPException(400, "account_id does not belong to this user")

    # "Pago Tarjeta" is always a transfer — never counts as a monthly expense.
    is_transfer = bool(payload.is_transfer) or payload.category == "Pago Tarjeta"

    tx = models.Transaction(
        user_id=current.id,
        account_id=payload.account_id,
        amount=payload.amount,
        currency=payload.currency,
        category=payload.category,
        date=payload.date,
        merchant=payload.merchant,
        notes=payload.notes,
        is_income=payload.is_income,
        is_transfer=is_transfer,
        image_url=image_url,
    )
    db.add(tx)
    db.flush()  # assign id
    for it in (payload.items or []):
        db.add(models.ReceiptItem(
            transaction_id=tx.id,
            name=it.name, price=it.price, quantity=it.quantity,
        ))
    db.commit()
    db.refresh(tx)

    # Try to auto-link as a transfer if it looks like a credit-card payment.
    account_svc.reconcile_new_transaction(db, current.id, tx)
    db.refresh(tx)
    return tx


@router.patch("/{tx_id}", response_model=schemas.TransactionOut)
@router.post("/edit", response_model=schemas.TransactionOut)  # spec-friendly alias
def update_transaction(
    payload: schemas.TransactionUpdate,
    tx_id: int | None = None,
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    if tx_id is None:
        raise HTTPException(400, "tx_id required")
    tx = db.query(models.Transaction).filter(
        models.Transaction.id == tx_id, models.Transaction.user_id == current.id
    ).first()
    if not tx:
        raise HTTPException(404, "Transaction not found")

    patch = payload.model_dump(exclude_none=True)
    if "amount" in patch and patch["amount"] <= 0:
        raise HTTPException(400, "amount must be greater than 0")
    # If the user is changing the category manually, remember it so next time
    # the same merchant auto-categorises without an LLM call.
    category_changed = "category" in patch and patch["category"] != tx.category

    for f, v in patch.items():
        setattr(tx, f, v)
    # Ensure Pago Tarjeta is always treated as a transfer.
    if tx.category == "Pago Tarjeta":
        tx.is_transfer = True
    db.commit()
    db.refresh(tx)

    if category_changed and tx.merchant:
        categorizer.remember_correction(db, current.id, tx.merchant, tx.category)
    return tx


@router.post("/transfer", response_model=list[schemas.TransactionOut], status_code=201)
def create_own_transfer(
    payload: schemas.OwnTransferCreate,
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    """Create an internal transfer between two of the user's own accounts."""
    if payload.from_account_id == payload.to_account_id:
        raise HTTPException(400, "from and to accounts must be different")
    from_acc = db.query(models.Account).filter(
        models.Account.id == payload.from_account_id,
        models.Account.user_id == current.id,
    ).first()
    if not from_acc:
        raise HTTPException(400, "from_account_id does not belong to this user")
    to_acc = db.query(models.Account).filter(
        models.Account.id == payload.to_account_id,
        models.Account.user_id == current.id,
    ).first()
    if not to_acc:
        raise HTTPException(400, "to_account_id does not belong to this user")

    currency = payload.currency or from_acc.currency or "CLP"
    shared = dict(
        user_id=current.id,
        amount=payload.amount,
        currency=currency,
        category="Transferencia",
        date=payload.date,
        merchant=payload.merchant,
        notes=payload.notes,
        is_transfer=True,
        image_url="",
    )
    tx_out = models.Transaction(**shared, account_id=payload.from_account_id, is_income=False)
    tx_in  = models.Transaction(**shared, account_id=payload.to_account_id,   is_income=True)
    db.add(tx_out)
    db.add(tx_in)
    db.flush()
    account_svc.link_as_transfer(db, tx_out, tx_in)
    db.commit()
    db.refresh(tx_out)
    db.refresh(tx_in)
    return [tx_out, tx_in]


@router.delete("/{tx_id}", status_code=204)
def delete_transaction(
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
        linked = db.query(models.Transaction).filter(
            models.Transaction.id == tx.linked_transaction_id,
            models.Transaction.user_id == current.id,
        ).first()
        if linked:
            linked.linked_transaction_id = None
            db.flush()
            db.delete(linked)
    tx.linked_transaction_id = None
    db.flush()
    db.delete(tx)
    db.commit()
