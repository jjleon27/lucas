"""
Voice input → structured transaction.

Flow:
  1. Browser transcribes the user's speech with Web Speech API.
  2. Frontend POSTs the transcript to /voice/parse.
  3. We LLM-extract a structured row (amount, merchant, category, account_hint...).
  4. We match account_hint against the user's real accounts.
  5. Frontend shows a confirmation card; user hits "guardar" and we POST to
     /transactions normally.

We intentionally do NOT auto-save here — the user should always confirm the
parsed transaction before it goes into their books. False positives are easy
to walk back in a list, but they poison trust fast if they just show up.
"""
import io
import logging
from datetime import date, datetime
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas, auth
from ..ai import voice as voice_ai
from ..ai import categorizer
from ..config import settings
from ..database import get_db
from ..services import dedupe

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/voice", tags=["voice"])

# Vocabulary hint for Whisper — biases recognition toward Chilean financial terms
_WHISPER_PROMPT = (
    "Lucas, gasté, pagué, compré, gamba, palo, lucas, débito, crédito, "
    "Falabella, CMR, Santander, BCI, Itaú, Ripley, Copec, efectivo, "
    "transferencia, sueldo, arriendo, supermercado, farmacia, bencina, "
    "cinco lucas, diez lucas, veinte lucas, un palo, medio palo."
)


@router.post("/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...),
    current: models.User = Depends(auth.get_current_user),
):
    """
    Transcribe audio with OpenAI Whisper.
    Accepts webm, mp4, wav, m4a (whatever MediaRecorder produces).
    Falls back gracefully if no OpenAI key is configured.
    """
    if not settings.openai_api_key:
        raise HTTPException(503, "Whisper not available (no OPENAI_API_KEY)")

    data = await file.read()
    if len(data) < 1000:
        raise HTTPException(400, "Audio too short")
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(413, "Audio too large (max 25 MB)")

    try:
        from openai import OpenAI
        client = OpenAI(api_key=settings.openai_api_key)
        audio_file = io.BytesIO(data)
        audio_file.name = file.filename or "audio.webm"
        result = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            language="es",
            prompt=_WHISPER_PROMPT,
        )
        transcript = result.text.strip()
        logger.info("whisper transcribed %d bytes → %r", len(data), transcript[:80])
        return {"transcript": transcript}
    except Exception as e:
        logger.error("whisper error: %s", e)
        raise HTTPException(500, f"Transcription failed: {e}")


@router.post("/parse", response_model=schemas.VoiceParsed)
def parse_voice(
    body: schemas.VoiceParseIn,
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    today = body.today or date.today()
    data = voice_ai.parse_voice(
        body.transcript, today=today, db=db, user_id=current.id,
    )

    # Parse date with safe fallback.
    try:
        tx_date = date.fromisoformat(data.get("date") or today.isoformat())
    except ValueError:
        tx_date = today

    # Apply user's default currency if voice didn't specify a clear one.
    user_currency = (current.settings or {}).get("currency") or "CLP"
    currency = (data.get("currency") or user_currency).upper()
    if data.get("action") != "unclear" and not data.get("currency"):
        currency = user_currency

    # Enrich category via user-learned rules + rule table (before falling to LLM).
    category = data.get("category") or "Otros"
    merchant = data.get("merchant") or ""
    if category in ("Other", "Uncategorized", "Otros", "") and merchant:
        category = categorizer.categorize(
            merchant, body.transcript, db=db, user_id=current.id,
        )

    # Resolve account_hint → actual account id.
    hint = data.get("account_hint") or ""
    suggested_account_id = dedupe.suggest_account_for_hint(
        db, current.id, hint, "",
    )
    # Second pass: try the hint as a type (e.g., "débito" → type="debit").
    if suggested_account_id is None and hint:
        type_map = {
            "débito": "debit", "debito": "debit",
            "crédito": "credit", "credito": "credit",
            "cmr": "credit",
            "efectivo": "cash",
            "ahorro": "savings", "savings": "savings",
            "wallet": "wallet", "mercado pago": "wallet",
        }
        inferred_type = type_map.get(hint.strip().lower(), "")
        suggested_account_id = dedupe.suggest_account_for_hint(
            db, current.id, "", inferred_type,
        )

    return schemas.VoiceParsed(
        action=data.get("action") or "unclear",
        amount=float(data.get("amount") or 0.0),
        currency=currency,
        category=category,
        merchant=merchant,
        date=tx_date,
        is_income=bool(data.get("is_income")),
        account_hint=hint,
        suggested_account_id=suggested_account_id,
        confidence=float(data.get("confidence") or 0.0),
        clarification=data.get("clarification") or "",
        transcript=body.transcript,
    )
