"""
Cartola (bank statement) PDF parsing.

Supports:
  - Text PDFs (Santander, BCI, Falabella, Itaú, BancoEstado) — parsed with
    pdfplumber, then fed to the LLM for structured extraction. Chilean
    cartola layouts vary a lot between banks, so asking the LLM to do the
    final structuring is pragmatic and much more robust than per-bank regex.
  - Scanned PDFs (photo/image only) — we render each page to PNG and use
    the same vision_parse() path as the screenshot uploader.

What we extract from a cartola:
  - account_info  : {bank, type, last4, currency, holder_name}
  - opening_balance / closing_balance
  - period        : {from_date, to_date}
  - transactions  : [ParsedReceipt, ...]

The consumer (routers/cartola.py) then runs each transaction through
dedupe.find_duplicate() to figure out what's new vs. already in the DB.
"""
from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import pdfplumber
from PIL import Image

from .ai import provider as ai_provider
from .ocr import vision_parse
from .schemas import ParsedReceipt


# ---------- Output ----------
@dataclass
class CartolaAccountInfo:
    bank: str = ""
    type: str = ""          # "debit" | "credit" | "savings" | ""
    last4: str = ""         # last 4 digits of the account/card
    currency: str = "CLP"
    holder_name: str = ""


@dataclass
class CartolaParseResult:
    transactions: list[ParsedReceipt] = field(default_factory=list)
    account_info: CartolaAccountInfo = field(default_factory=CartolaAccountInfo)
    opening_balance: Optional[float] = None
    closing_balance: Optional[float] = None
    period_from: Optional[date] = None
    period_to: Optional[date] = None
    raw_text: str = ""

    def __bool__(self) -> bool:
        return bool(self.transactions)


# ---------- Extraction ----------
def _extract_text(pdf_bytes: bytes) -> str:
    """
    Pull all text from the PDF. If there's no text layer (scanned PDF),
    returns empty string and we'll fall through to the image path.
    """
    parts: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            t = page.extract_text() or ""
            if t.strip():
                parts.append(t)
    return "\n\n=== PAGE ===\n\n".join(parts).strip()


def _render_pages_as_images(pdf_bytes: bytes, max_pages: int = 10) -> list[bytes]:
    """
    For scanned PDFs: render each page to PNG so vision_parse() can read it.
    Cap at max_pages because sending 30 pages to GPT-4o-mini gets pricey and
    usually the first few pages have the movement table.
    """
    images: list[bytes] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages[:max_pages]:
            pil_img: Image.Image = page.to_image(resolution=200).original
            buf = io.BytesIO()
            pil_img.save(buf, format="PNG")
            images.append(buf.getvalue())
    return images


# ---------- LLM structured extraction ----------
_SYSTEM_PROMPT = """You are an expert parser of Chilean/Latin-American bank statements (cartolas).
The user gives you the raw text of a cartola PDF. Extract a structured JSON with:

{
  "account_info": {
    "bank": "Santander" | "BCI" | "Falabella" | "BancoEstado" | "Itaú" | ...,
    "type": "debit" | "credit" | "savings",
    "last4": "1234",
    "currency": "CLP",
    "holder_name": "..."
  },
  "opening_balance": number or null,
  "closing_balance": number or null,
  "period_from": "YYYY-MM-DD" or null,
  "period_to": "YYYY-MM-DD" or null,
  "transactions": [
    {
      "date": "YYYY-MM-DD",
      "amount": number (positive),
      "currency": "CLP" | "USD" | ...,
      "merchant": "cleaned merchant name",
      "description": "full original description",
      "category": "Food & Dining" | "Transport" | "Shopping" | "Bills" | "Entertainment" | "Health" | "Travel" | "Housing" | "Other",
      "is_income": true | false,
      "is_cc_payment": true | false,
      "cuota_actual": number or null,
      "cuotas_total": number or null
    }, ...
  ]
}

CRITICAL RULES:
1. AMOUNTS. Chilean formats: "$17.517" is 17517 CLP (dot is thousands sep), "$1.234,50" is 1234.5. Never interpret "." as decimal for CLP. For USD/EUR the "." is decimal.
2. CHARGES vs. CREDITS. Cartolas usually have two columns: "Cargos"/"Débito" (money out → expense, is_income=false) and "Abonos"/"Crédito" (money in → income, is_income=true). The amount is always positive; the sign comes from which column it lives in.
3. CC PAYMENTS. "PAGO TARJETA", "PAGO TC", "ABONO TRANSF" on a credit card = is_cc_payment: true. These are transfers, NOT income. Set is_income=false AND is_cc_payment=true.
4. MERCHANT CLEANUP. Strip "COMPRA ", "WEB ", "POS ", city names, and trailing ref codes. PRESERVE discriminators: "MOVISTAR PAY" stays "Movistar Pay", "UBER *TRIP" stays "Uber". Never over-strip to a one-word brand if there's meaningful detail.
5. CUOTAS. If you see "1/6", "Cuota 1 de 6", set cuota_actual=1 and cuotas_total=6. Use the "Monto total" when present, not the per-cuota amount.
6. DEDUPE HINT. Don't fabricate rows. If a movement appears on two pages (paginated), include it once.
7. DATES. Chilean dates are dd/mm/yyyy. Never assume US-style.
8. SKIP summaries, interest accruals, and totals rows that aren't actual movements.

Return ONLY the JSON object. No markdown, no prose.
"""


def _llm_structure(text: str, *, db=None, user_id: Optional[int] = None) -> Optional[dict]:
    """Ask the LLM to turn raw cartola text into structured JSON."""
    if not ai_provider.is_available():
        return None
    try:
        resp = ai_provider.chat_completion(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            temperature=0.0,
            purpose="parse",
            user_id=user_id,
            db=db,
        )
        if resp is None:
            return None
    except Exception as e:  # noqa: BLE001
        print(f"[cartola] llm structure failed: {e}")
        return None
    raw = (resp.text or "").strip()
    if not raw:
        return None
    # Some providers wrap the JSON in fenced code blocks.
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Try to recover the outermost JSON object.
        a, b = raw.find("{"), raw.rfind("}")
        if a != -1 and b > a:
            try:
                return json.loads(raw[a : b + 1])
            except json.JSONDecodeError:
                return None
    return None


# ---------- Parsing helpers ----------
def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _row_to_parsed(row: dict) -> Optional[ParsedReceipt]:
    d = _parse_date(row.get("date"))
    if d is None:
        return None
    try:
        amount = float(row.get("amount") or 0.0)
    except (TypeError, ValueError):
        return None
    if amount <= 0:
        return None
    return ParsedReceipt(
        amount=amount,
        date=d,
        merchant=(row.get("merchant") or "").strip(),
        category=row.get("category") or "Other",
        currency=(row.get("currency") or "CLP").upper(),
        is_income=bool(row.get("is_income")),
        items=[],
        raw_text="",
        description=row.get("description") or row.get("merchant") or "",
        cuota_actual=row.get("cuota_actual"),
        cuotas_total=row.get("cuotas_total"),
        is_cc_payment=bool(row.get("is_cc_payment")),
    )


def _build_result(data: dict, raw_text: str) -> CartolaParseResult:
    info = data.get("account_info") or {}
    period = data
    rows = data.get("transactions") or []
    txs = [p for r in rows if (p := _row_to_parsed(r)) is not None]

    result = CartolaParseResult(
        transactions=txs,
        account_info=CartolaAccountInfo(
            bank=(info.get("bank") or "").strip(),
            type=(info.get("type") or "").strip().lower(),
            last4=(info.get("last4") or "").strip(),
            currency=(info.get("currency") or "CLP").upper(),
            holder_name=(info.get("holder_name") or "").strip(),
        ),
        opening_balance=_to_float(data.get("opening_balance")),
        closing_balance=_to_float(data.get("closing_balance")),
        period_from=_parse_date(period.get("period_from")),
        period_to=_parse_date(period.get("period_to")),
        raw_text=raw_text,
    )
    return result


def _to_float(x) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ---------- Public entry point ----------
def parse_cartola(
    pdf_bytes: bytes, *, db=None, user_id: Optional[int] = None,
) -> CartolaParseResult:
    """
    Parse a cartola PDF. Strategy:
      1. Extract text with pdfplumber → feed to LLM → structured JSON.
      2. If the PDF has no text layer (scanned), render pages as images and
         call vision_parse() per page, then merge.
    """
    text = _extract_text(pdf_bytes)

    if text:
        data = _llm_structure(text, db=db, user_id=user_id)
        if data:
            return _build_result(data, raw_text=text)

    # Fallback: image/scanned PDF. Run each rendered page through
    # the vision parser (same path used for screenshots) and merge.
    images = _render_pages_as_images(pdf_bytes)
    merged: list[ParsedReceipt] = []
    bank_hint = ""
    account_type_hint = ""
    for img in images:
        vr = vision_parse(img, db=db, user_id=user_id)
        if not vr:
            continue
        merged.extend(vr.transactions)
        bank_hint = bank_hint or vr.bank_hint
        account_type_hint = account_type_hint or vr.account_type_hint

    return CartolaParseResult(
        transactions=merged,
        account_info=CartolaAccountInfo(
            bank=bank_hint,
            type=account_type_hint,
        ),
        raw_text=text,
    )
