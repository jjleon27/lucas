"""
/bills — new bill-splitting router.

Flow:
  1. POST /bills                    → create draft
  2. POST /bills/{id}/ocr           → upload image, detect items
  3. POST /bills/{id}/participants  → add people
  4. PATCH /bills/{id}/items/{iid} / POST /bills/{id}/items  → edit items
  5. POST /bills/{id}/shares        → set who pays what fraction per item
  6. POST /bills/{id}/set-payers    → who put in money
  7. POST /bills/{id}/finalize      → create Transaction for user's share + debts

Key invariant: sum(BillItemShare.weight) == 1.0 per item.
Transaction.amount = user's share only (not full bill).
"""
from __future__ import annotations

import secrets
from datetime import date as _date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Bill, BillParticipant, BillItem, BillItemShare, BillDebt, Person, Transaction
from ..auth import get_current_user
from ..schemas import UserOut

router = APIRouter(prefix="/bills", tags=["bills"])


# ─── Pydantic schemas ───────────────────────────────────────────────────────

class BillCreate(BaseModel):
    merchant: str = ""
    date: Optional[_date] = None
    currency: str = "CLP"


class BillPatch(BaseModel):
    merchant: Optional[str] = None
    date: Optional[_date] = None
    tip_amount: Optional[float] = Field(None, ge=0)
    total_amount: Optional[float] = Field(None, ge=0)


class ParticipantAdd(BaseModel):
    person_id: int


class ItemAdd(BaseModel):
    name: str
    qty: int = Field(default=1, ge=1, le=999)
    unit_price: float = Field(ge=0)


class ItemPatch(BaseModel):
    name: Optional[str] = None
    qty: Optional[int] = Field(None, ge=1, le=999)
    unit_price: Optional[float] = Field(None, ge=0)


class ShareEntry(BaseModel):
    participant_id: int
    weight: float = Field(ge=0, le=1)


class SetSharesPayload(BaseModel):
    item_id: int
    shares: list[ShareEntry]


class PayerEntry(BaseModel):
    participant_id: int
    paid_amount: float = Field(ge=0)


class FinalizePayload(BaseModel):
    account_id: Optional[int] = None
    category: str = "Comida"


class SettleDebtPayload(BaseModel):
    pass


# ─── Helpers ────────────────────────────────────────────────────────────────

def _get_bill(bill_id: int, user_id: int, db: Session) -> Bill:
    bill = db.query(Bill).filter(Bill.id == bill_id, Bill.user_id == user_id).first()
    if not bill:
        raise HTTPException(404, "Bill not found")
    return bill


def _get_or_create_me(user_id: int, db: Session) -> Person:
    me = db.query(Person).filter(Person.user_id == user_id, Person.is_me == True).first()
    if not me:
        me = Person(user_id=user_id, name="Yo", is_me=True, color="#6366f1")
        db.add(me)
        db.flush()
    return me


def _bill_out(bill: Bill) -> dict:
    participants = [
        {
            "id": p.id,
            "person_id": p.person_id,
            "name": p.person.name,
            "color": p.person.color,
            "is_me": p.person.is_me,
            "paid_amount": p.paid_amount,
            "owes_amount": p.owes_amount,
        }
        for p in bill.participants
    ]
    items = [
        {
            "id": it.id,
            "name": it.name,
            "qty": it.qty,
            "unit_price": it.unit_price,
            "line_total": it.line_total,
            "shares": [
                {"participant_id": s.participant_id, "weight": s.weight}
                for s in it.shares
            ],
        }
        for it in bill.items
    ]
    return {
        "id": bill.id,
        "merchant": bill.merchant,
        "date": str(bill.date),
        "total_amount": bill.total_amount,
        "tip_amount": bill.tip_amount,
        "currency": bill.currency,
        "image_url": bill.image_url,
        "status": bill.status,
        "transaction_id": bill.transaction_id,
        "public_token": bill.public_token,
        "participants": participants,
        "items": items,
    }


def _recalc_total(bill: Bill) -> None:
    """Recompute bill.total_amount from sum of item line_totals + tip."""
    items_sum = sum(it.line_total for it in bill.items)
    bill.total_amount = round(items_sum + bill.tip_amount, 2)


def _assign_equal(bill: Bill, db: Session) -> None:
    """Assign all items equally among all participants (replaces existing shares)."""
    n = len(bill.participants)
    if n == 0:
        return
    weight = round(1.0 / n, 10)
    for item in bill.items:
        db.query(BillItemShare).filter(BillItemShare.item_id == item.id).delete()
        for p in bill.participants:
            db.add(BillItemShare(item_id=item.id, participant_id=p.id, weight=weight))


def _simplify_debts(balances: dict[int, float]) -> list[tuple[int, int, float]]:
    """Greedy debt simplification. Returns list of (from_id, to_id, amount)."""
    creditors = sorted([(pid, amt) for pid, amt in balances.items() if amt > 0.5], key=lambda x: -x[1])
    debtors = sorted([(pid, -amt) for pid, amt in balances.items() if amt < -0.5], key=lambda x: -x[1])
    transfers = []
    ci, di = 0, 0
    while ci < len(creditors) and di < len(debtors):
        cid, camt = creditors[ci]
        did, damt = debtors[di]
        t = min(camt, damt)
        transfers.append((did, cid, round(t, 2)))
        creditors[ci] = (cid, camt - t)
        debtors[di] = (did, damt - t)
        if creditors[ci][1] < 0.5:
            ci += 1
        if debtors[di][1] < 0.5:
            di += 1
    return transfers


# ─── Endpoints ──────────────────────────────────────────────────────────────

@router.post("", status_code=201)
def create_bill(
    payload: BillCreate,
    current: UserOut = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    me = _get_or_create_me(current.id, db)
    bill = Bill(
        user_id=current.id,
        merchant=payload.merchant,
        date=payload.date or _date.today(),
        currency=payload.currency,
    )
    db.add(bill)
    db.flush()
    # Auto-add "Yo" as first participant
    db.add(BillParticipant(bill_id=bill.id, person_id=me.id))
    db.commit()
    db.refresh(bill)
    return _bill_out(bill)


@router.get("/{bill_id}")
def get_bill(
    bill_id: int,
    current: UserOut = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _bill_out(_get_bill(bill_id, current.id, db))


@router.get("")
def list_bills(
    current: UserOut = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    bills = (
        db.query(Bill)
        .filter(Bill.user_id == current.id)
        .order_by(Bill.created_at.desc())
        .limit(50)
        .all()
    )
    return [
        {
            "id": b.id,
            "merchant": b.merchant,
            "date": str(b.date),
            "total_amount": b.total_amount,
            "status": b.status,
            "participants": len(b.participants),
        }
        for b in bills
    ]


@router.patch("/{bill_id}")
def patch_bill(
    bill_id: int,
    payload: BillPatch,
    current: UserOut = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    bill = _get_bill(bill_id, current.id, db)
    if payload.merchant is not None:
        bill.merchant = payload.merchant
    if payload.date is not None:
        bill.date = payload.date
    if payload.tip_amount is not None:
        bill.tip_amount = payload.tip_amount
        _recalc_total(bill)
    if payload.total_amount is not None:
        bill.total_amount = payload.total_amount
    db.commit()
    db.refresh(bill)
    return _bill_out(bill)


@router.delete("/{bill_id}", status_code=204)
def delete_bill(
    bill_id: int,
    current: UserOut = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    bill = _get_bill(bill_id, current.id, db)
    db.delete(bill)
    db.commit()


# ── OCR ──────────────────────────────────────────────────────────────────────

@router.post("/{bill_id}/ocr")
async def bill_ocr(
    bill_id: int,
    file: UploadFile = File(...),
    current: UserOut = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Upload receipt image → run OCR → populate bill items. Replaces existing items."""
    from ..ocr import vision_parse
    from .. import storage

    bill = _get_bill(bill_id, current.id, db)
    raw = await file.read()
    filename = file.filename or "receipt.jpg"
    image_url = storage.save_image(raw, filename)
    bill.image_url = image_url

    parsed = vision_parse(raw, user_id=current.id, db=db)
    if parsed is None or not parsed.transactions:
        db.commit()
        return _bill_out(bill)

    # Pick best transaction from OCR (single receipt → 1 tx with items)
    tx_data = parsed.transactions[0]
    if tx_data.merchant and not bill.merchant:
        bill.merchant = tx_data.merchant
    if tx_data.date:
        bill.date = tx_data.date

    # Replace items
    db.query(BillItem).filter(BillItem.bill_id == bill.id).delete()

    for it in (tx_data.items or []):
        qty = max(int(it.quantity or 1), 1)
        unit_price = round(float(it.price or 0), 2)
        line_total = round(qty * unit_price, 2)
        if line_total == 0:
            continue
        db.add(BillItem(
            bill_id=bill.id,
            name=it.name,
            qty=qty,
            unit_price=unit_price,
            line_total=line_total,
        ))

    db.flush()
    _recalc_total(bill)

    # Auto-assign equally if participants exist
    if bill.participants:
        _assign_equal(bill, db)

    db.commit()
    db.refresh(bill)
    return _bill_out(bill)


# ── Participants ──────────────────────────────────────────────────────────────

@router.post("/{bill_id}/participants", status_code=201)
def add_participant(
    bill_id: int,
    payload: ParticipantAdd,
    current: UserOut = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    bill = _get_bill(bill_id, current.id, db)
    person = db.query(Person).filter(Person.id == payload.person_id, Person.user_id == current.id).first()
    if not person:
        raise HTTPException(404, "Person not found")
    already = db.query(BillParticipant).filter(
        BillParticipant.bill_id == bill_id,
        BillParticipant.person_id == payload.person_id,
    ).first()
    if already:
        return _bill_out(bill)
    db.add(BillParticipant(bill_id=bill_id, person_id=payload.person_id))
    db.flush()
    # Re-assign equal now that there's a new participant
    _assign_equal(bill, db)
    db.commit()
    db.refresh(bill)
    return _bill_out(bill)


@router.delete("/{bill_id}/participants/{participant_id}", status_code=200)
def remove_participant(
    bill_id: int,
    participant_id: int,
    current: UserOut = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    bill = _get_bill(bill_id, current.id, db)
    p = db.query(BillParticipant).filter(
        BillParticipant.id == participant_id,
        BillParticipant.bill_id == bill_id,
    ).first()
    if not p:
        raise HTTPException(404, "Participant not found")
    if p.person.is_me:
        raise HTTPException(400, "Cannot remove yourself from the bill")
    db.delete(p)
    db.flush()
    _assign_equal(bill, db)
    db.commit()
    db.refresh(bill)
    return _bill_out(bill)


# ── Items ─────────────────────────────────────────────────────────────────────

@router.post("/{bill_id}/items", status_code=201)
def add_item(
    bill_id: int,
    payload: ItemAdd,
    current: UserOut = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    bill = _get_bill(bill_id, current.id, db)
    line_total = round(payload.qty * payload.unit_price, 2)
    item = BillItem(bill_id=bill_id, name=payload.name, qty=payload.qty,
                    unit_price=payload.unit_price, line_total=line_total)
    db.add(item)
    db.flush()
    if bill.participants:
        n = len(bill.participants)
        w = round(1.0 / n, 10)
        for p in bill.participants:
            db.add(BillItemShare(item_id=item.id, participant_id=p.id, weight=w))
    _recalc_total(bill)
    db.commit()
    db.refresh(bill)
    return _bill_out(bill)


@router.patch("/{bill_id}/items/{item_id}")
def patch_item(
    bill_id: int,
    item_id: int,
    payload: ItemPatch,
    current: UserOut = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    bill = _get_bill(bill_id, current.id, db)
    item = db.query(BillItem).filter(BillItem.id == item_id, BillItem.bill_id == bill_id).first()
    if not item:
        raise HTTPException(404, "Item not found")
    if payload.name is not None:
        item.name = payload.name
    if payload.qty is not None:
        item.qty = payload.qty
    if payload.unit_price is not None:
        item.unit_price = payload.unit_price
    item.line_total = round(item.qty * item.unit_price, 2)
    _recalc_total(bill)
    db.commit()
    db.refresh(bill)
    return _bill_out(bill)


@router.delete("/{bill_id}/items/{item_id}", status_code=200)
def delete_item(
    bill_id: int,
    item_id: int,
    current: UserOut = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    bill = _get_bill(bill_id, current.id, db)
    item = db.query(BillItem).filter(BillItem.id == item_id, BillItem.bill_id == bill_id).first()
    if not item:
        raise HTTPException(404, "Item not found")
    db.delete(item)
    db.flush()
    _recalc_total(bill)
    db.commit()
    db.refresh(bill)
    return _bill_out(bill)


# ── Shares ────────────────────────────────────────────────────────────────────

@router.post("/{bill_id}/shares")
def set_shares(
    bill_id: int,
    payload: SetSharesPayload,
    current: UserOut = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Set fractional shares for one item. Weights must sum to ~1.0."""
    bill = _get_bill(bill_id, current.id, db)
    item = db.query(BillItem).filter(BillItem.id == payload.item_id, BillItem.bill_id == bill_id).first()
    if not item:
        raise HTTPException(404, "Item not found")

    total_weight = sum(s.weight for s in payload.shares)
    if abs(total_weight - 1.0) > 0.01:
        raise HTTPException(400, f"Weights must sum to 1.0, got {total_weight:.3f}")

    # Validate participants belong to this bill
    valid_pids = {p.id for p in bill.participants}
    for s in payload.shares:
        if s.participant_id not in valid_pids:
            raise HTTPException(400, f"Participant {s.participant_id} not in bill")

    db.query(BillItemShare).filter(BillItemShare.item_id == item.id).delete()
    for s in payload.shares:
        db.add(BillItemShare(item_id=item.id, participant_id=s.participant_id, weight=s.weight))

    db.commit()
    db.refresh(bill)
    return _bill_out(bill)


@router.post("/{bill_id}/assign-equal")
def assign_all_equal(
    bill_id: int,
    current: UserOut = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Reset all shares to equal split among all participants."""
    bill = _get_bill(bill_id, current.id, db)
    if not bill.participants:
        raise HTTPException(400, "No participants")
    _assign_equal(bill, db)
    db.commit()
    db.refresh(bill)
    return _bill_out(bill)


# ── Payers ────────────────────────────────────────────────────────────────────

@router.post("/{bill_id}/set-payers")
def set_payers(
    bill_id: int,
    payload: list[PayerEntry],
    current: UserOut = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Record who actually paid and how much."""
    bill = _get_bill(bill_id, current.id, db)
    valid_pids = {p.id: p for p in bill.participants}
    for entry in payload:
        if entry.participant_id not in valid_pids:
            raise HTTPException(400, f"Participant {entry.participant_id} not in bill")
        valid_pids[entry.participant_id].paid_amount = entry.paid_amount
    db.commit()
    db.refresh(bill)
    return _bill_out(bill)


# ── Finalize ──────────────────────────────────────────────────────────────────

@router.post("/{bill_id}/finalize")
def finalize_bill(
    bill_id: int,
    payload: FinalizePayload,
    current: UserOut = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    1. Compute each participant's owed amount from shares.
    2. Compute net balances (paid - owed) per participant.
    3. Simplify debts.
    4. Create ONE Transaction for the user's personal share.
    5. Mark bill as finalized.
    """
    bill = _get_bill(bill_id, current.id, db)
    if bill.status == "finalized":
        raise HTTPException(400, "Bill already finalized")
    if not bill.participants:
        raise HTTPException(400, "No participants")
    if not bill.items:
        raise HTTPException(400, "No items")

    # Step 1: compute owes_amount for each participant from shares
    for p in bill.participants:
        p.owes_amount = 0.0

    for item in bill.items:
        for share in item.shares:
            share.participant.owes_amount = round(
                share.participant.owes_amount + item.line_total * share.weight, 2
            )

    # Add tip proportionally to owes_amount
    if bill.tip_amount > 0:
        total_owed = sum(p.owes_amount for p in bill.participants)
        if total_owed > 0:
            remaining_tip = bill.tip_amount
            for i, p in enumerate(bill.participants):
                if i == len(bill.participants) - 1:
                    tip_share = remaining_tip
                else:
                    tip_share = round(p.owes_amount / total_owed * bill.tip_amount, 2)
                    remaining_tip -= tip_share
                p.owes_amount = round(p.owes_amount + tip_share, 2)

    # Step 2: net balance = paid - owed
    balances: dict[int, float] = {
        p.id: round(p.paid_amount - p.owes_amount, 2)
        for p in bill.participants
    }

    # Step 3: simplify debts
    db.query(BillDebt).filter(BillDebt.bill_id == bill_id).delete()
    for from_id, to_id, amount in _simplify_debts(balances):
        db.add(BillDebt(
            bill_id=bill_id,
            from_participant_id=from_id,
            to_participant_id=to_id,
            amount=amount,
        ))

    # Step 4: find "me" participant and create Transaction for my share
    me_participant = next((p for p in bill.participants if p.person.is_me), None)
    if me_participant:
        my_share = round(me_participant.owes_amount, 2)
        if my_share > 0:
            from ..services import account_svc
            tx = Transaction(
                user_id=current.id,
                account_id=payload.account_id,
                amount=my_share,
                currency=bill.currency,
                category=payload.category,
                date=bill.date,
                merchant=bill.merchant,
                notes=f"Split {len(bill.participants)} personas",
                image_url=bill.image_url,
                is_income=False,
            )
            db.add(tx)
            db.flush()
            bill.transaction_id = tx.id
            # Trigger transfer-matching / reconciliation for the new transaction
            if payload.account_id:
                try:
                    from ..services.accounts import reconcile_new_transaction
                    reconcile_new_transaction(db, tx)
                except Exception:
                    pass

    # Step 5: generate public token for sharing
    if not bill.public_token:
        bill.public_token = secrets.token_urlsafe(16)

    bill.status = "finalized"
    db.commit()
    db.refresh(bill)
    return _bill_out(bill)


# ── Public share ──────────────────────────────────────────────────────────────

@router.get("/{bill_id}/public/{token}")
def get_bill_public(
    bill_id: int,
    token: str,
    db: Session = Depends(get_db),
):
    """No auth — shareable link for participants to see their share."""
    bill = db.query(Bill).filter(Bill.id == bill_id, Bill.public_token == token).first()
    if not bill:
        raise HTTPException(404, "Not found")
    return _bill_out(bill)


# ── Settle debt ───────────────────────────────────────────────────────────────

@router.patch("/{bill_id}/debts/{debt_id}/settle")
def settle_debt(
    bill_id: int,
    debt_id: int,
    current: UserOut = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    bill = _get_bill(bill_id, current.id, db)
    debt = db.query(BillDebt).filter(BillDebt.id == debt_id, BillDebt.bill_id == bill_id).first()
    if not debt:
        raise HTTPException(404, "Debt not found")
    debt.status = "settled"
    debt.settled_at = datetime.utcnow()
    db.commit()
    return {"ok": True}
