"""
Bill-split endpoints — v2.

Flow:
  GET  /split/me              → get (or create) the "Yo" person for this user
  GET  /split/people          → list all people for this user
  POST /split/people          → add a person
  DELETE /split/people/{id}   → remove a person (cannot remove is_me)

  POST /split/start           → create transaction + items from parsed receipt
  POST /split/start-manual    → create transaction + single item from total amount
  POST /split/assign-item     → set all assignees (+ split rules) for one item
  GET  /split/result          → compute per-person totals + item breakdown
  POST /split/settle          → compute settlement; optionally create a Lucas transaction

Legacy (kept for backward compat, not used by the new UI):
  POST /split/assign          → single-assign an item to one person
"""
from __future__ import annotations

from datetime import date as date_type
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas, auth
from ..database import get_db
from ..services import accounts as account_svc, dedupe as dedupe_svc

router = APIRouter(prefix="/split", tags=["split"])

PALETTE = [
    "#ef4444", "#f97316", "#eab308", "#10b981",
    "#06b6d4", "#6366f1", "#a855f7", "#ec4899",
]


# ─────────────────────────────────────────────────────────────
# Helper: compute each person's share of one item
# ─────────────────────────────────────────────────────────────

def _compute_shares(
    line_total: float,
    assignees: list[models.ItemAssignment],
) -> dict[int, float]:
    """
    Returns {person_id: amount} for all assignees of one item.

    Rules:
      equal   → line_total / N for each person
      percent → line_total * (value/100) for each; last person gets the remainder
      amount  → person pays exactly value; last person pays line_total - sum(others)
    """
    if not assignees:
        return {}

    n = len(assignees)
    shares: dict[int, float] = {}

    # Detect mode from first assignee (all same type per item enforced by frontend)
    mode = assignees[0].split_type if assignees else "equal"

    if mode == "equal":
        per = round(line_total / n, 2)
        for a in assignees:
            shares[a.person_id] = per
        # Adjust last person for rounding cents
        total_assigned = sum(shares.values())
        diff = round(line_total - total_assigned, 2)
        if diff and assignees:
            shares[assignees[-1].person_id] += diff

    elif mode == "percent":
        allocated = 0.0
        for i, a in enumerate(assignees):
            pct = float(a.value or 0.0)
            amt = round(line_total * pct / 100.0, 2)
            if i == len(assignees) - 1:
                amt = round(line_total - allocated, 2)
            shares[a.person_id] = amt
            allocated += amt

    elif mode == "amount":
        allocated = 0.0
        for i, a in enumerate(assignees):
            if i == len(assignees) - 1:
                amt = round(line_total - allocated, 2)
            else:
                amt = round(float(a.value or 0.0), 2)
            shares[a.person_id] = amt
            allocated += amt

    return shares


def _item_v2_out(item: models.ReceiptItem) -> schemas.ReceiptItemV2Out:
    line_total = round(item.price * (item.quantity or 1), 2)
    shares = _compute_shares(line_total, item.assignments)
    assignees_out = [
        schemas.AssigneeOut(
            person_id=a.person_id,
            person_name=a.person.name,
            person_color=a.person.color,
            split_type=a.split_type,
            value=a.value,
            computed_amount=shares.get(a.person_id, 0.0),
        )
        for a in item.assignments
    ]
    return schemas.ReceiptItemV2Out(
        id=item.id,
        name=item.name,
        price=item.price,
        quantity=item.quantity,
        line_total=line_total,
        assignees=assignees_out,
    )


# ─────────────────────────────────────────────────────────────
# "Yo" — the app user's own person
# ─────────────────────────────────────────────────────────────

def _get_or_create_me(db: Session, user_id: int) -> models.Person:
    me = db.query(models.Person).filter(
        models.Person.user_id == user_id,
        models.Person.is_me.is_(True),
    ).first()
    if not me:
        me = models.Person(user_id=user_id, name="Yo", color="#6366f1", is_me=True)
        db.add(me)
        db.commit()
        db.refresh(me)
    return me


@router.get("/me", response_model=schemas.PersonOut)
def get_me(
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    """Return (creating if needed) the 'Yo' person for this user."""
    return _get_or_create_me(db, current.id)


# ─────────────────────────────────────────────────────────────
# People management
# ─────────────────────────────────────────────────────────────

@router.get("/people", response_model=list[schemas.PersonOut])
def list_people(
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    # "Yo" first, then alphabetical
    people = (
        db.query(models.Person)
        .filter(models.Person.user_id == current.id)
        .all()
    )
    people.sort(key=lambda p: (not p.is_me, p.name.lower()))
    return people


@router.post("/people", response_model=schemas.PersonOut, status_code=201)
def create_person(
    payload: schemas.PersonCreate,
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    # Auto-assign next palette color if no color given (frontend always sends one)
    existing_count = db.query(models.Person).filter(
        models.Person.user_id == current.id
    ).count()
    color = payload.color or PALETTE[existing_count % len(PALETTE)]
    p = models.Person(user_id=current.id, name=payload.name.strip()[:80], color=color)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@router.delete("/people/{person_id}", status_code=204)
def delete_person(
    person_id: int,
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    p = db.query(models.Person).filter(
        models.Person.id == person_id, models.Person.user_id == current.id
    ).first()
    if not p:
        raise HTTPException(404, "Person not found")
    if p.is_me:
        raise HTTPException(400, "Cannot remove yourself from splits")
    db.delete(p)
    db.commit()


# ─────────────────────────────────────────────────────────────
# Split session — start
# ─────────────────────────────────────────────────────────────

@router.post("/start")
def start_split(
    transaction_id: int,
    items: list[schemas.ParsedItem] | None = None,
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    """Seed ReceiptItems for an existing transaction (idempotent)."""
    tx = db.query(models.Transaction).filter(
        models.Transaction.id == transaction_id, models.Transaction.user_id == current.id
    ).first()
    if not tx:
        raise HTTPException(404, "Transaction not found")

    if items is not None:
        db.query(models.ReceiptItem).filter(
            models.ReceiptItem.transaction_id == tx.id
        ).delete()
        for it in items:
            db.add(models.ReceiptItem(
                transaction_id=tx.id,
                name=it.name, price=it.price, quantity=it.quantity,
            ))
        db.commit()

    rows = db.query(models.ReceiptItem).filter(
        models.ReceiptItem.transaction_id == tx.id
    ).all()
    return {
        "transaction_id": tx.id,
        "items": [_item_v2_out(r) for r in rows],
    }


@router.post("/start-manual", status_code=201)
def start_manual(
    payload: schemas.ManualSplitIn,
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    """
    Create a transaction + single ReceiptItem from a manually entered total.
    Used when the user doesn't have a receipt photo but wants to split a bill.
    """
    proposal = schemas.ParsedReceipt(
        amount=payload.total_amount,
        date=payload.date,
        merchant=payload.merchant or "División de cuenta",
        category="Dividido",
        currency=payload.currency,
        is_income=False,
    )
    existing = dedupe_svc.find_duplicate(db, user_id=current.id, account_id=payload.account_id, proposed=proposal)
    if existing:
        return {"transaction_id": existing.id, "items": []}

    tx = models.Transaction(
        user_id=current.id,
        account_id=payload.account_id,
        amount=payload.total_amount,
        currency=payload.currency,
        category="Dividido",
        date=payload.date,
        merchant=payload.merchant or "División de cuenta",
        notes="",
        is_income=False,
    )
    db.add(tx)
    db.flush()
    account_svc.reconcile_new_transaction(db, current.id, tx)
    item = models.ReceiptItem(
        transaction_id=tx.id,
        name=payload.merchant or "Total",
        price=payload.total_amount,
        quantity=1,
    )
    db.add(item)
    db.commit()
    db.refresh(tx)
    db.refresh(item)
    return {
        "transaction_id": tx.id,
        "items": [_item_v2_out(item)],
    }


# ─────────────────────────────────────────────────────────────
# Add a single extra item (e.g. IVA, propina) to a split
# ─────────────────────────────────────────────────────────────

@router.post("/add-item", status_code=201)
def add_split_item(
    transaction_id: int,
    payload: schemas.ParsedItem,
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    """
    Append a single item (propina, IVA, descuento) to an existing split
    without touching existing assignments.
    """
    tx = db.query(models.Transaction).filter(
        models.Transaction.id == transaction_id, models.Transaction.user_id == current.id
    ).first()
    if not tx:
        raise HTTPException(404, "Transaction not found")

    item = models.ReceiptItem(
        transaction_id=tx.id,
        name=payload.name,
        price=payload.price,
        quantity=payload.quantity,
    )
    db.add(item)
    # Update the transaction amount to include the new item
    tx.amount = round(tx.amount + payload.price * payload.quantity, 2)
    db.commit()
    db.refresh(item)
    return _item_v2_out(item)


@router.patch("/items/{item_id}", status_code=200)
def update_split_item(
    item_id: int,
    payload: dict,
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    """Edit a split item's name and/or price."""
    item = (
        db.query(models.ReceiptItem)
        .join(models.Transaction, models.ReceiptItem.transaction_id == models.Transaction.id)
        .filter(models.ReceiptItem.id == item_id, models.Transaction.user_id == current.id)
        .first()
    )
    if not item:
        raise HTTPException(404, "Item not found")
    old_total = item.price * item.quantity
    if "name" in payload:
        item.name = str(payload["name"])
    if "price" in payload:
        item.price = float(payload["price"])
    if "quantity" in payload:
        item.quantity = int(payload["quantity"])
    delta = item.price * item.quantity - old_total
    tx = db.query(models.Transaction).filter(models.Transaction.id == item.transaction_id).first()
    if tx:
        tx.amount = round(tx.amount + delta, 2)
    db.commit()
    db.refresh(item)
    return _item_v2_out(item)


@router.delete("/items/{item_id}", status_code=204)
def delete_split_item(
    item_id: int,
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    """Remove an item from a split."""
    item = (
        db.query(models.ReceiptItem)
        .join(models.Transaction, models.ReceiptItem.transaction_id == models.Transaction.id)
        .filter(models.ReceiptItem.id == item_id, models.Transaction.user_id == current.id)
        .first()
    )
    if not item:
        raise HTTPException(404, "Item not found")
    tx = db.query(models.Transaction).filter(models.Transaction.id == item.transaction_id).first()
    if tx:
        tx.amount = round(tx.amount - item.price * item.quantity, 2)
    db.delete(item)
    db.commit()


# ─────────────────────────────────────────────────────────────
# Assignment
# ─────────────────────────────────────────────────────────────

@router.post("/assign-item")
def assign_item_v2(
    payload: schemas.ItemAssignIn,
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    """
    Replace ALL assignees for one item.
    Send an empty `assignees` list to unassign everyone.
    """
    item = (
        db.query(models.ReceiptItem)
        .join(models.Transaction, models.ReceiptItem.transaction_id == models.Transaction.id)
        .filter(
            models.ReceiptItem.id == payload.item_id,
            models.Transaction.user_id == current.id,
        )
        .first()
    )
    if not item:
        raise HTTPException(404, "Item not found")

    # Validate split types and ownership
    for a in payload.assignees:
        if a.split_type not in schemas.SPLIT_TYPES:
            raise HTTPException(400, f"split_type must be one of {schemas.SPLIT_TYPES}")
        person = db.query(models.Person).filter(
            models.Person.id == a.person_id,
            models.Person.user_id == current.id,
        ).first()
        if not person:
            raise HTTPException(404, f"Person {a.person_id} not found")

    # Replace existing assignments
    db.query(models.ItemAssignment).filter(
        models.ItemAssignment.item_id == item.id
    ).delete()
    for a in payload.assignees:
        db.add(models.ItemAssignment(
            item_id=item.id,
            person_id=a.person_id,
            split_type=a.split_type,
            value=a.value,
        ))
    db.commit()
    db.refresh(item)
    return _item_v2_out(item)


# ─────────────────────────────────────────────────────────────
# Result
# ─────────────────────────────────────────────────────────────

@router.get("/result", response_model=schemas.SplitResultV2Out)
def split_result(
    transaction_id: int,
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    tx = db.query(models.Transaction).filter(
        models.Transaction.id == transaction_id, models.Transaction.user_id == current.id
    ).first()
    if not tx:
        raise HTTPException(404, "Transaction not found")

    items = db.query(models.ReceiptItem).filter(
        models.ReceiptItem.transaction_id == tx.id
    ).all()
    people_map = {
        p.id: p for p in db.query(models.Person).filter(
            models.Person.user_id == current.id
        ).all()
    }

    person_totals: dict[int, float] = {}
    unassigned_total = 0.0
    assigned_item_count = 0
    items_out: list[schemas.ReceiptItemV2Out] = []

    for item in items:
        line_total = round(item.price * (item.quantity or 1), 2)
        item_out = _item_v2_out(item)
        items_out.append(item_out)

        if item.assignments:
            shares = _compute_shares(line_total, item.assignments)
            for pid, amt in shares.items():
                person_totals[pid] = round(person_totals.get(pid, 0.0) + amt, 2)
            assigned_item_count += 1
        else:
            unassigned_total = round(unassigned_total + line_total, 2)

    total_amount = round(sum(
        item.price * (item.quantity or 1) for item in items
    ), 2)
    completion = (assigned_item_count / len(items) * 100) if items else 100.0

    people_results = [
        schemas.SplitPersonResult(
            person_id=pid,
            person_name=people_map[pid].name if pid in people_map else f"Person {pid}",
            person_color=people_map[pid].color if pid in people_map else "#6366f1",
            is_me=people_map[pid].is_me if pid in people_map else False,
            total=total,
        )
        for pid, total in sorted(person_totals.items(), key=lambda x: -x[1])
    ]

    return schemas.SplitResultV2Out(
        transaction_id=tx.id,
        total_amount=total_amount,
        completion_pct=round(completion, 1),
        unassigned_total=unassigned_total,
        items=items_out,
        people=people_results,
    )


# ─────────────────────────────────────────────────────────────
# Settlement
# ─────────────────────────────────────────────────────────────

@router.post("/settle", response_model=schemas.SettleOut)
def settle(
    payload: schemas.SettleIn,
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    """
    Compute who owes who.
    Optionally create a Lucas transaction (debit from account) if the user paid.
    """
    tx = db.query(models.Transaction).filter(
        models.Transaction.id == payload.transaction_id,
        models.Transaction.user_id == current.id,
    ).first()
    if not tx:
        raise HTTPException(404, "Transaction not found")

    items = db.query(models.ReceiptItem).filter(
        models.ReceiptItem.transaction_id == tx.id
    ).all()
    people_map = {
        p.id: p for p in db.query(models.Person).filter(
            models.Person.user_id == current.id
        ).all()
    }

    # --- Compute per-person totals ---
    person_totals: dict[int, float] = {}
    for item in items:
        line_total = round(item.price * (item.quantity or 1), 2)
        if item.assignments:
            shares = _compute_shares(line_total, item.assignments)
            for pid, amt in shares.items():
                person_totals[pid] = round(person_totals.get(pid, 0.0) + amt, 2)

    # --- Identify "me" ---
    me_person = db.query(models.Person).filter(
        models.Person.user_id == current.id,
        models.Person.is_me.is_(True),
    ).first()
    me_id = me_person.id if me_person else None
    my_total = round(person_totals.get(me_id, 0.0) if me_id else 0.0, 2)

    # --- Identify payer ---
    payer_id = payload.payer_person_id
    if payer_id is None:
        payer_id = me_id  # "me" is paying
    if payer_id is None:
        raise HTTPException(400, "No 'Yo' person found — call GET /split/me first")

    payer = people_map.get(payer_id)
    if not payer:
        raise HTTPException(404, "Payer person not found")

    # --- Build debt rows ---
    # For each non-payer person: positive = they owe the payer
    debts: list[schemas.SettleDebtRow] = []
    for pid, total in person_totals.items():
        if pid == payer_id:
            continue
        person = people_map.get(pid)
        debts.append(schemas.SettleDebtRow(
            person_id=pid,
            person_name=person.name if person else f"Person {pid}",
            person_color=person.color if person else "#6366f1",
            is_me=(pid == me_id),
            amount=round(total, 2),
        ))
    debts.sort(key=lambda d: -d.amount)

    # --- Optionally save user's share to Lucas ---
    saved_tx_id: Optional[int] = None
    if payload.save_to_lucas and me_id is not None:
        my_share = round(person_totals.get(me_id, 0.0), 2)
        if my_share > 0:
            # Validate account ownership if provided
            if payload.account_id:
                owns = db.query(models.Account).filter(
                    models.Account.id == payload.account_id,
                    models.Account.user_id == current.id,
                ).first()
                if not owns:
                    raise HTTPException(400, "account_id does not belong to this user")

            # Update the existing "Dividido" transaction:
            # - amount = user's actual share (not the full receipt)
            # - account_id = selected account, only if user is the one who paid
            tx.amount = my_share
            tx.is_transfer = False
            if payer_id == me_id and payload.account_id:
                tx.account_id = payload.account_id
            elif payer_id != me_id:
                # Someone else paid — add a note so user knows who to reimburse
                payer_name_str = payer.name if payer else "otra persona"
                tx.notes = (tx.notes or "") + f" | Pagó {payer_name_str}"
            db.commit()
            saved_tx_id = tx.id

    return schemas.SettleOut(
        payer_person_id=payer_id,
        payer_name=payer.name,
        my_total=my_total,
        debts=debts,
        saved_transaction_id=saved_tx_id,
    )


# ─────────────────────────────────────────────────────────────
# Legacy single-assign (kept so old clients don't break)
# ─────────────────────────────────────────────────────────────

@router.post("/assign")
def assign_item_legacy(
    payload: schemas.AssignItemIn,
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    item = (
        db.query(models.ReceiptItem)
        .join(models.Transaction, models.ReceiptItem.transaction_id == models.Transaction.id)
        .filter(
            models.ReceiptItem.id == payload.item_id,
            models.Transaction.user_id == current.id,
        )
        .first()
    )
    if not item:
        raise HTTPException(404, "Item not found")

    if payload.person_id is not None:
        owner = db.query(models.Person).filter(
            models.Person.id == payload.person_id, models.Person.user_id == current.id
        ).first()
        if not owner:
            raise HTTPException(404, "Person not found")

    item.assigned_to = payload.person_id
    db.commit()
    return {"ok": True, "item_id": item.id, "assigned_to": item.assigned_to}
