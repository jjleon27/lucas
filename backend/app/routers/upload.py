"""
Image upload + OCR processing.

POST /upload   → stores the image, runs OCR+parse, returns ParsedUpload.
                 - transactions is always a list (1 for a receipt, N for a
                   bank statement).
                 - suggested_account_id is populated when the parser can
                   guess which of the user's accounts this image belongs to
                   (based on bank name + card type shown in the header).
                 - each tx.dupe_of is populated when we find an existing
                   row in the DB that looks like the same movement — the
                   frontend then asks the user to confirm before re-adding.
POST /process  → re-parse a file without persisting it (used for "try again").
"""
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas, auth, ocr, storage
from ..ai import categorizer
from ..database import get_db
from ..services import dedupe

router = APIRouter(tags=["upload"])


def _enrich(tx: schemas.ParsedReceipt, *, db: Session, user_id: int) -> schemas.ParsedReceipt:
    """
    Fill in category + normalise the row.

    Special cases:
      - Credit-card payments ("PAGO TARJETA CMR", "ABONO", etc.) are NOT
        expenses and NOT income — they're internal transfers. We force
        category="Other" and is_income=False so they don't pollute the
        dashboard. The UI shows a badge prompting the user to link the
        matching -$ row from their debit account.
    """
    if tx.is_cc_payment:
        tx.category = "Transferencia"
        tx.is_income = False
        return tx
    if tx.category in ("Uncategorized", "Other", "Otros", ""):
        tx.category = categorizer.categorize(
            tx.merchant, tx.raw_text or tx.description or "", db=db, user_id=user_id,
        )
    return tx


def _apply_currency_default(
    txs: list[schemas.ParsedReceipt], user_currency: str,
) -> None:
    """Don't trust the OCR's currency guess — default to the user's preferred."""
    for t in txs:
        if not t.currency:
            t.currency = user_currency
        elif t.currency.upper() == "USD" and user_currency != "USD":
            # Common mistake: Chilean amounts like $17.517 get read as USD.
            t.currency = user_currency


@router.post("/upload", response_model=schemas.ParsedUpload)
async def upload_image(
    file: UploadFile = File(...),
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    """
    Parse an image and return the proposed transactions.

    The frontend then lets the user:
      1. Confirm / change the auto-suggested account.
      2. Decide what to do with each duplicate (skip / add anyway).
      3. Save.
    """
    content_type = (file.content_type or "").lower()
    is_pdf = content_type == "application/pdf" or (file.filename or "").lower().endswith(".pdf")
    if not (content_type.startswith("image/") or is_pdf):
        raise HTTPException(400, "Solo se aceptan imágenes o PDFs")
    data = await file.read()
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(413, "Archivo muy grande (máx. 25 MB)")

    # For PDFs, render to image(s) and parse; images go through normal path.
    filename = file.filename or ("upload.pdf" if is_pdf else "upload.png")
    image_url = storage.save_image(data, filename)

    if is_pdf:
        pr = ocr.parse_receipt_from_pdf(data, db=db, user_id=current.id)
    else:
        pr = ocr.parse_receipt(data, db=db, user_id=current.id)

    user_currency = (current.settings or {}).get("currency") or "CLP"
    transactions = [_enrich(t, db=db, user_id=current.id) for t in pr.transactions]
    _apply_currency_default(transactions, user_currency)

    # Best guess at which of the user's accounts this image belongs to.
    suggested_account_id = dedupe.suggest_account_for_hint(
        db, current.id, pr.bank_hint, pr.account_type_hint,
    )

    # Duplicate check — for each proposed tx, see if a matching row already
    # exists in the DB (same user, ±2 days, amount within tolerance, similar
    # merchant). This is critical: the user might upload today's screenshot,
    # then tomorrow's, which still shows yesterday's rows. We don't want to
    # add the same movement twice.
    for t in transactions:
        dup = dedupe.find_duplicate(
            db, user_id=current.id,
            account_id=suggested_account_id, proposed=t,
        )
        if dup is not None:
            t.dupe_of = dup.id

    return schemas.ParsedUpload(
        type="list" if len(transactions) > 1 else "single",
        image_url=image_url,
        currency=user_currency,
        transactions=transactions,
        raw_text=transactions[0].raw_text if transactions else "",
        suggested_account_id=suggested_account_id,
    )


@router.post("/process", response_model=schemas.ParsedUpload)
async def process_image(
    file: UploadFile = File(...),
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    """Re-parse without persisting. Used for 'try again' on a bad parse."""
    data = await file.read()
    is_pdf = (file.content_type or "").lower() == "application/pdf" or \
             (file.filename or "").lower().endswith(".pdf")
    if is_pdf:
        pr = ocr.parse_receipt_from_pdf(data, db=db, user_id=current.id)
    else:
        pr = ocr.parse_receipt(data, db=db, user_id=current.id)

    user_currency = (current.settings or {}).get("currency") or "CLP"
    transactions = [_enrich(t, db=db, user_id=current.id) for t in pr.transactions]
    _apply_currency_default(transactions, user_currency)

    suggested_account_id = dedupe.suggest_account_for_hint(
        db, current.id, pr.bank_hint, pr.account_type_hint,
    )
    for t in transactions:
        dup = dedupe.find_duplicate(
            db, user_id=current.id,
            account_id=suggested_account_id, proposed=t,
        )
        if dup is not None:
            t.dupe_of = dup.id

    return schemas.ParsedUpload(
        type="list" if len(transactions) > 1 else "single",
        image_url="",
        currency=user_currency,
        transactions=transactions,
        raw_text=transactions[0].raw_text if transactions else "",
        suggested_account_id=suggested_account_id,
    )
