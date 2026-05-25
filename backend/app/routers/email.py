"""
Email inbound webhook — receives forwarded bank notification emails and creates
pending_review transactions automatically.

Setup:
  1. Lucas generates a unique address: lucas-TOKEN@{EMAIL_DOMAIN}
  2. User adds a Gmail filter: From:(banco OR notificacion OR pagos)
     → Forward to that address.
  3. Mailgun / SendGrid Inbound Parse POSTs to POST /email/inbound.
  4. We parse the email, create a Transaction with status="pending_review".

Configure EMAIL_DOMAIN in .env (e.g. lucas.jjleon.com).
For local dev / testing, you can POST to /email/inbound directly with JSON.
"""
from __future__ import annotations

import logging
import os
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import models, schemas, auth
from ..ai.email_parser import parse_email
from ..database import get_db
from ..services import accounts as account_svc, dedupe as dedupe_svc

# Domain used for the per-user forwarding addresses.
# Set EMAIL_DOMAIN=lucas.jjleon.com in .env
EMAIL_DOMAIN = os.getenv("EMAIL_DOMAIN", "notify.lucasapp.com")

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/email", tags=["email"])


# ── Helper: resolve user from to-address token ────────────────────────────────
def _user_from_token(db: Session, token: str) -> models.User | None:
    """Look up user by their personal email_token (from the To: address)."""
    return db.query(models.User).filter(models.User.email_token == token).first()


# Maps substrings found in a sender email domain → canonical bank name (lowercase for matching)
_SENDER_BANK_MAP = {
    "falabella": "falabella",
    "bancofalabella": "falabella",
    "santander": "santander",
    "bancochile": "banco de chile",
    "bci": "bci",
    "itau": "itaú",
    "scotiabank": "scotiabank",
    "bancoestado": "bancoestado",
    "security": "security",
    "ripley": "ripley",
    "mach": "mach",
}


def _bank_from_sender(from_addr: str) -> str | None:
    """Extract a canonical bank name from the sender's email address."""
    addr = from_addr.lower()
    for key, bank in _SENDER_BANK_MAP.items():
        if key in addr:
            return bank
    return None


def _extract_token_from_address(address: str) -> str | None:
    """
    'lucas-abc123@notify.lucasapp.com' → 'abc123'
    Accepts bare tokens too (for testing).
    """
    import re
    m = re.search(r"lucas-([A-Za-z0-9_\-]+)@", address)
    if m:
        return m.group(1)
    # Also accept just the token directly
    if re.fullmatch(r"[A-Za-z0-9_\-]{8,}", address.strip()):
        return address.strip()
    return None


# ── Inbound webhook (SendGrid Inbound Parse format) ───────────────────────────
@router.post("/inbound", status_code=200)
async def email_inbound(request: Request, db: Session = Depends(get_db)):
    """
    Accepts either:
      - multipart/form-data (SendGrid Inbound Parse)
      - application/json  (testing / other providers)
    """
    ct = request.headers.get("content-type", "")

    if "multipart" in ct or "form" in ct:
        form = await request.form()
        to_addr = form.get("to", "") or form.get("envelope", "")
        from_addr = form.get("from", "")
        subject = form.get("subject", "")
        body_text = form.get("text", "")
        body_html = form.get("html", "")
    else:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "Unrecognised content type")
        to_addr = body.get("to", "")
        from_addr = body.get("from", "")
        subject = body.get("subject", "")
        body_text = body.get("text", "")
        body_html = body.get("html", "")

    # Resolve user from token in the To: address
    token = _extract_token_from_address(to_addr)
    if not token:
        logger.warning("email_inbound: no token in to=%s", to_addr)
        return {"ok": False, "reason": "no_token"}

    user = _user_from_token(db, token)
    if not user:
        logger.warning("email_inbound: unknown token %s", token)
        return {"ok": False, "reason": "unknown_token"}

    # Parse email
    parsed = parse_email(
        db, user.id,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
    )
    if not parsed:
        return {"ok": True, "action": "skipped", "reason": "not_a_transaction"}

    # Try to resolve source account by card hint, then by sender bank
    account_id: int | None = None
    card_last4 = parsed.get("card_last4", "")
    if card_last4:
        acc = db.query(models.Account).filter(
            models.Account.user_id == user.id,
            models.Account.name.ilike(f"%{card_last4}%"),
            models.Account.archived.is_(False),
        ).first()
        if acc:
            account_id = acc.id

    # For CC payments: if card hint didn't work, use the sender's bank domain
    # to find the source debit account (e.g. email from @bancofalabella.com → Falabella debit)
    sender_bank = _bank_from_sender(from_addr)

    amount = float(parsed["amount"])
    currency = parsed.get("currency", "CLP")
    tx_date = date.fromisoformat(parsed["date"])
    category = parsed.get("category", "Otros")
    merchant = parsed.get("merchant", "")

    # Duplicate check
    from ..schemas import ParsedReceipt
    proposal = ParsedReceipt(
        amount=amount,
        date=tx_date,
        merchant=merchant,
        category=category,
        currency=currency,
        is_income=bool(parsed.get("is_income", False)),
    )
    existing = dedupe_svc.find_duplicate(db, user_id=user.id, account_id=account_id, proposed=proposal)
    if existing:
        logger.info("email_inbound: dupe of tx#%d for user#%d", existing.id, user.id)
        return {"ok": True, "action": "duplicate", "existing_id": existing.id}

    # ── CC payment: create both sides if we can identify the credit card ─────
    if parsed.get("is_cc_payment"):
        cc_name = parsed.get("cc_name", "").lower().strip()
        cc_account = None

        # Find target credit card by cc_name from email body
        all_credit = db.query(models.Account).filter(
            models.Account.user_id == user.id,
            models.Account.type == "credit",
            models.Account.archived.is_(False),
        ).all()
        if cc_name:
            for a in all_credit:
                if cc_name in a.name.lower() or a.name.lower() in cc_name:
                    cc_account = a
                    break
        # Fallback: match credit account by sender bank
        if not cc_account and sender_bank:
            for a in all_credit:
                if sender_bank in a.name.lower() or sender_bank in (a.bank or "").lower():
                    cc_account = a
                    break

        # Find source debit account by sender bank (if not found via card_last4)
        if not account_id and sender_bank:
            debit_accounts = db.query(models.Account).filter(
                models.Account.user_id == user.id,
                models.Account.type.in_(["debit", "savings", "wallet", "cash"]),
                models.Account.archived.is_(False),
            ).all()
            for a in debit_accounts:
                if sender_bank in a.name.lower() or sender_bank in (a.bank or "").lower():
                    account_id = a.id
                    break

        debit_merchant = f"Pago tarjeta {parsed.get('cc_name', '').title()}".strip()
        # Only auto-confirm if BOTH the source debit account AND target CC are identified.
        # If either is missing, leave pending_review so the user can assign both sides.
        fully_resolved = cc_account is not None and account_id is not None
        debit_status = "confirmed" if fully_resolved else "pending_review"

        debit_tx = models.Transaction(
            user_id=user.id,
            account_id=account_id,
            amount=amount,
            currency=currency,
            category="Transferencia",
            date=tx_date,
            merchant=debit_merchant or merchant or "Pago tarjeta",
            notes=f"Importado desde email: {subject[:120]}",
            is_income=False,
            is_transfer=True,
            image_url="",
            status=debit_status,
        )
        db.add(debit_tx)
        db.flush()

        if fully_resolved:
            credit_tx = models.Transaction(
                user_id=user.id,
                account_id=cc_account.id,
                amount=amount,
                currency=currency,
                category="Transferencia",
                date=tx_date,
                merchant="Pago recibido",
                notes=f"Pago desde email: {subject[:120]}",
                is_income=True,
                is_transfer=True,
                linked_transaction_id=debit_tx.id,
                image_url="",
                status="confirmed",
            )
            db.add(credit_tx)
            db.flush()
            debit_tx.linked_transaction_id = credit_tx.id
            db.commit()
            logger.info(
                "email_inbound: CC payment auto-linked debit#%d ↔ credit#%d ($%.0f %s → %s)",
                debit_tx.id, credit_tx.id, amount, currency, cc_account.name,
            )
            return {"ok": True, "action": "cc_payment_linked",
                    "debit_tx_id": debit_tx.id, "credit_tx_id": credit_tx.id,
                    "cc_account": cc_account.name}
        else:
            db.commit()
            logger.info(
                "email_inbound: CC payment pending review tx#%d ($%.0f, cc_name=%s, account_id=%s)",
                debit_tx.id, amount, cc_name, account_id,
            )
            return {"ok": True, "action": "cc_payment_pending_review",
                    "transaction_id": debit_tx.id, "cc_name": cc_name}

    # ── Regular transaction ───────────────────────────────────────────────────
    tx = models.Transaction(
        user_id=user.id,
        account_id=account_id,
        amount=amount,
        currency=currency,
        category=category,
        date=tx_date,
        merchant=merchant,
        notes=f"Importado desde email: {subject[:120]}",
        is_income=bool(parsed.get("is_income", False)),
        is_transfer=False,
        image_url="",
        status="pending_review",
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)

    logger.info(
        "email_inbound: created pending tx#%d ($%.0f %s) for user#%d",
        tx.id, tx.amount, tx.merchant, user.id,
    )
    return {"ok": True, "action": "created", "transaction_id": tx.id}


# ── Review queue endpoints ─────────────────────────────────────────────────────
@router.get("/pending", response_model=list[schemas.TransactionOut])
def list_pending(
    limit: int = 50,
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    """Return all pending_review transactions for the current user, oldest first."""
    return (
        db.query(models.Transaction)
        .filter(
            models.Transaction.user_id == current.id,
            models.Transaction.status == "pending_review",
        )
        .order_by(models.Transaction.date.asc(), models.Transaction.id.asc())
        .limit(limit)
        .all()
    )


@router.post("/review/{tx_id}", response_model=schemas.TransactionOut)
def review_transaction(
    tx_id: int,
    payload: schemas.TransactionReviewAction,
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    """
    Act on a pending_review transaction:
      - confirm    → set status="confirmed", optionally update fields
      - skip       → leave as pending (just skip for now)
      - not_expense → delete the transaction
      - pending    → mark as Por Cobrar (keep pending, tag in notes)
    """
    from ..ai import categorizer

    tx = db.query(models.Transaction).filter(
        models.Transaction.id == tx_id,
        models.Transaction.user_id == current.id,
    ).first()
    if not tx:
        raise HTTPException(404, "Transaction not found")

    action = payload.action

    if action == "not_expense":
        db.delete(tx)
        db.commit()
        return tx  # return last known state

    if action == "skip":
        # Don't change anything — client just moves to the next item
        return tx

    if action == "pending":
        # "Por Cobrar" — stays pending_review but tagged
        tx.notes = (tx.notes or "") + " [Por Cobrar]"
        db.commit()
        db.refresh(tx)
        return tx

    if action == "confirm":
        if payload.category:
            tx.category = payload.category
        if payload.merchant:
            tx.merchant = payload.merchant
        if payload.amount is not None:
            tx.amount = payload.amount
        tx.status = "confirmed"
        db.commit()
        db.refresh(tx)

        if payload.remember and tx.merchant and tx.category:
            categorizer.remember_correction(db, current.id, tx.merchant, tx.category)

        account_svc.reconcile_new_transaction(db, current.id, tx)
        db.refresh(tx)
        return tx

    if action == "confirm_cc_payment":
        # User manually selected which credit card this payment went to
        # and optionally which source account it came from.
        if not payload.target_account_id:
            raise HTTPException(400, "target_account_id required for confirm_cc_payment")

        cc_account = db.query(models.Account).filter(
            models.Account.id == payload.target_account_id,
            models.Account.user_id == current.id,
            models.Account.type == "credit",
        ).first()
        if not cc_account:
            raise HTTPException(404, "Credit card account not found")

        # Assign source debit account if provided
        if payload.source_account_id:
            tx.account_id = payload.source_account_id

        credit_tx = models.Transaction(
            user_id=current.id,
            account_id=cc_account.id,
            amount=tx.amount,
            currency=tx.currency,
            category="Transferencia",
            date=tx.date,
            merchant="Pago recibido",
            notes=f"Enlazado con tx#{tx.id}",
            is_income=True,
            is_transfer=True,
            linked_transaction_id=tx.id,
            image_url="",
            status="confirmed",
        )
        db.add(credit_tx)
        db.flush()

        tx.linked_transaction_id = credit_tx.id
        tx.is_transfer = True
        tx.status = "confirmed"
        db.commit()
        db.refresh(tx)
        account_svc.reconcile_new_transaction(db, current.id, tx)
        db.refresh(tx)
        return tx

    raise HTTPException(400, f"Unknown action: {action}")


@router.get("/forwarding-address")
def forwarding_address(
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    """Return the user's personal forwarding address, generating a token if needed."""
    import secrets
    if not current.email_token:
        # Lazy token generation for old users
        while True:
            token = secrets.token_urlsafe(9)
            if not db.query(models.User).filter(models.User.email_token == token).first():
                break
        current.email_token = token
        db.commit()
        db.refresh(current)

    forwarding_email = f"lucas-{current.email_token}@{EMAIL_DOMAIN}"
    return {
        "email": forwarding_email,
        "token": current.email_token,
        "instructions": (
            "Agrega un filtro en Gmail:\n"
            "  De: (banco OR notificaci OR cobro OR pago OR tarjeta)\n"
            f"  → Reenviar a: {forwarding_email}\n\n"
            "Cada email de tu banco se convertirá automáticamente en un gasto "
            "pendiente de revisar en Lucas."
        ),
    }
