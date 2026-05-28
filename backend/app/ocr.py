"""
Receipt / bank-statement parsing.

Two paths, picked at runtime:

1. **Vision-first (preferred)** — send the original image straight to a
   vision-capable LLM (default: gpt-4o-mini). The model reads the image
   directly, with no Tesseract step in between, so it doesn't suffer from
   OCR garbage in tables, low-contrast iOS screenshots, or Chilean number
   formats. Triggered when `OPENAI_API_KEY` is set.

2. **Tesseract + heuristic (offline fallback)** — when no API key is set,
   we OCR with Tesseract and run a regex parser. Keeps the app usable
   without paying for an API.
"""
from __future__ import annotations

import base64
import io
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

import cv2
import numpy as np
import pytesseract
from PIL import Image

from .ai import provider as ai_provider
from .schemas import ParsedItem, ParsedReceipt


# ---------- PDF → image bytes conversion ----------
def pdf_page_to_image_bytes(pdf_bytes: bytes, page_index: int = 0, dpi: int = 150) -> bytes:
    """
    Render a single PDF page to JPEG bytes.
    Returns JPEG bytes that can be fed into parse_receipt() directly.
    """
    try:
        from pdf2image import convert_from_bytes
        images = convert_from_bytes(pdf_bytes, dpi=dpi, first_page=page_index + 1,
                                    last_page=page_index + 1, fmt="jpeg")
        if not images:
            raise ValueError("pdf2image returned no images")
        buf = io.BytesIO()
        images[0].save(buf, format="JPEG", quality=90)
        return buf.getvalue()
    except ImportError:
        raise RuntimeError("pdf2image is not installed — cannot render PDF pages")


def pdf_page_count(pdf_bytes: bytes) -> int:
    """Return the number of pages in a PDF."""
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            return len(pdf.pages)
    except Exception:
        return 1


def parse_receipt_from_pdf(
    pdf_bytes: bytes, *, db=None, user_id: int = 0
) -> "ParseResult":
    """
    Convert every page to an image and parse each one.
    Single-page PDFs work like a normal receipt.
    Multi-page PDFs return all transactions found.
    """
    n_pages = min(pdf_page_count(pdf_bytes), 20)  # cap at 20 pages
    all_txs: list[ParsedReceipt] = []
    bank_hint = ""
    account_type_hint = ""

    for i in range(n_pages):
        try:
            img_bytes = pdf_page_to_image_bytes(pdf_bytes, page_index=i)
            result = parse_receipt(img_bytes, db=db, user_id=user_id)
            all_txs.extend(result.transactions)
            if not bank_hint:
                bank_hint = result.bank_hint
            if not account_type_hint:
                account_type_hint = result.account_type_hint
        except Exception:
            continue  # skip un-parseable pages silently

    return ParseResult(
        transactions=all_txs or [ParsedReceipt(
            amount=0, date=date.today(), merchant="", category="Otros",
        )],
        bank_hint=bank_hint,
        account_type_hint=account_type_hint,
    )


@dataclass
class ParseResult:
    """Wraps the transactions + metadata the parser can infer from the image."""
    transactions: list[ParsedReceipt] = field(default_factory=list)
    bank_hint: str = ""
    account_type_hint: str = ""          # "debit" | "credit" | ""

    def __bool__(self) -> bool:
        return bool(self.transactions)


# ---------- Image preprocessing (Tesseract path) ----------
def _preprocess(image_bytes: bytes) -> np.ndarray:
    arr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        img = np.array(Image.open(io.BytesIO(image_bytes)).convert("RGB"))[:, :, ::-1]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.bilateralFilter(gray, 9, 75, 75)
    thresh = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15
    )
    return thresh


def run_ocr(image_bytes: bytes) -> str:
    processed = _preprocess(image_bytes)
    return pytesseract.image_to_string(processed, lang="eng+spa").strip()


# ---------- Number format: handles US, European, and Chilean (CLP, no decimals) ----------
def _to_float(raw: str) -> float:
    s = raw.strip()
    negative = s.startswith("-")
    s = s.lstrip("+-").replace("$", "").replace("CLP", "").replace("USD", "")
    s = s.replace(" ", "").replace("\u00a0", "")
    if not s:
        return 0.0

    n_dots = s.count(".")
    n_commas = s.count(",")

    try:
        if n_dots and n_commas:
            if s.rfind(",") > s.rfind("."):
                s = s.replace(".", "").replace(",", ".")
            else:
                s = s.replace(",", "")
        elif n_dots >= 2:
            s = s.replace(".", "")
        elif n_commas >= 2:
            s = s.replace(",", "")
        elif n_dots == 1:
            trailing = len(s) - s.rfind(".") - 1
            if trailing == 3:
                s = s.replace(".", "")
        elif n_commas == 1:
            trailing = len(s) - s.rfind(",") - 1
            if trailing == 3:
                s = s.replace(",", "")
            else:
                s = s.replace(",", ".")
        value = float(s)
    except ValueError:
        value = 0.0

    return -value if negative else value


# ---------- Heuristic parser (Tesseract fallback) ----------
_MONEY_TOKEN = re.compile(
    r"[-+]?\$?\s?\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|[-+]?\$?\s?\d+(?:[.,]\d{1,2})?"
)
_SIGNED_MONEY = re.compile(r"[+-]\s?\$?\s?\d[\d.,]*")
_AMOUNT_LINE_RE = re.compile(r"(?i)(total|amount|importe|monto)[^\d-]{0,12}(-?\$?\s?\d[\d.,]*)")
_DATE_RE = re.compile(r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})")

_STATUS_TIME = re.compile(r"^\s*\d{1,2}[:\.]\d{2}\s*(am|pm)?\s*$", re.I)
_JUNK_PATTERNS = re.compile(
    r"^\s*(\d{1,3}\s?%|wifi|wi-fi|lte|5g|4g|⏰|◉|●|\.\.\.|·)\s*$", re.I,
)

_SPANISH_MONTHS = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "setiembre", "octubre",
    "noviembre", "diciembre",
)
_SPANISH_WEEKDAYS = (
    "lunes", "martes", "miércoles", "miercoles", "jueves",
    "viernes", "sábado", "sabado", "domingo",
)
_MONTH_NUM = {m: (i + 1) for i, m in enumerate(
    ("enero", "febrero", "marzo", "abril", "mayo", "junio",
     "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre")
)}
_MONTH_NUM["setiembre"] = 9

_SPANISH_DATE_RE = re.compile(
    r"(?:(?:" + "|".join(_SPANISH_WEEKDAYS) + r")\s+)?"
    r"(\d{1,2})\s+de\s+(" + "|".join(_SPANISH_MONTHS) + r")"
    r"(?:\s+de)?\s+(\d{2,4})",
    re.I,
)


def _parse_date(s: str) -> date:
    for fmt in (
        "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y",
        "%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y",
        "%Y-%m-%d", "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return date.today()


def _parse_spanish_date(m: re.Match) -> date:
    try:
        d = int(m.group(1))
        mo = _MONTH_NUM.get(m.group(2).lower(), 0)
        y = int(m.group(3))
        if y < 100:
            y += 2000
        if mo:
            return date(y, mo, d)
    except (ValueError, IndexError):
        pass
    return date.today()


def _is_junk_merchant(s: str) -> bool:
    if not s:
        return True
    s2 = s.strip()
    if len(s2) < 2:
        return True
    if _STATUS_TIME.match(s2):
        return True
    if _JUNK_PATTERNS.match(s2):
        return True
    if re.fullmatch(r"[\d\s\.\,\-\$\+:]+", s2):
        return True
    return False


def _parse_signed_statement(text: str) -> list[ParsedReceipt]:
    out: list[ParsedReceipt] = []
    current_date = date.today()
    pending_label: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            pending_label = None
            continue

        sd = _SPANISH_DATE_RE.search(line)
        if sd:
            current_date = _parse_spanish_date(sd)
            pending_label = None
            continue
        d = _DATE_RE.search(line)
        if d and not _SIGNED_MONEY.search(line):
            current_date = _parse_date(d.group(1))
            pending_label = None
            continue

        money_m = _SIGNED_MONEY.search(line)
        if money_m:
            amount = _to_float(money_m.group(0))
            if amount == 0:
                continue
            merchant = line[: money_m.start()].strip(" -\t|·.")
            merchant = re.sub(r"\s{2,}", " ", merchant)
            if _is_junk_merchant(merchant) and pending_label:
                merchant = pending_label
            merchant = merchant[:80] or "Transacción"

            is_income = amount > 0
            out.append(ParsedReceipt(
                amount=abs(amount),
                date=current_date,
                merchant=merchant,
                category="Otros",
                is_income=is_income,
                items=[],
                raw_text=line,
            ))
            pending_label = None
        else:
            if not _is_junk_merchant(line):
                pending_label = line[:80]
    return out


def heuristic_parse(text: str) -> list[ParsedReceipt]:
    signed_rows = _parse_signed_statement(text)
    if len(signed_rows) >= 2:
        return signed_rows

    m = _AMOUNT_LINE_RE.search(text)
    if m:
        amount = abs(_to_float(m.group(2)))
    else:
        vals = [abs(_to_float(x)) for x in _MONEY_TOKEN.findall(text)]
        amount = max(vals) if vals else 0.0

    d = _DATE_RE.search(text)
    sd = _SPANISH_DATE_RE.search(text)
    if sd:
        parsed_date = _parse_spanish_date(sd)
    elif d:
        parsed_date = _parse_date(d.group(1))
    else:
        parsed_date = date.today()

    merchant = ""
    for line in text.splitlines():
        line = line.strip()
        if not line or _is_junk_merchant(line):
            continue
        if _MONEY_TOKEN.fullmatch(line):
            continue
        if len(line) > 2:
            merchant = line[:80]
            break

    return [ParsedReceipt(
        amount=amount,
        date=parsed_date,
        merchant=merchant,
        category="Otros",
        items=[],
        raw_text=text,
    )]


# ---------- Vision parser (preferred path) ----------
_SYSTEM_PROMPT = """You are a vision-based parser for personal-finance screenshots.

The image is one of:
- A receipt / boleta (Lider, Jumbo, Tottus, restaurantes, etc.)
- A bank app screenshot (Santander, BancoEstado, BCI, Banco de Chile, Itaú,
  BBVA, Mercado Pago, CMR Falabella, etc.) showing one or many movements
- A credit-card statement (lista de transacciones del mes)
- A payment confirmation / notification

Return ONLY a JSON object shaped EXACTLY like:

{
  "type": "single" | "list",
  "currency": "CLP" | "USD" | "EUR" | "BRL" | "ARS" | "MXN" | "PEN" | "COP",
  "bank_hint": string,          // "Santander" | "BCI" | "CMR Falabella" | "" — used to auto-pick an account
  "account_type_hint": string,  // "debit" | "credit" | "" — is this a debit/checking or credit card statement?
  "total_neto": number | null,  // BOLETAS ONLY: the exact printed "TOTAL NETO" value (e.g. 29521). null for bank statements.
  "iva_amount": number | null,  // BOLETAS ONLY: the exact printed IVA value (e.g. 5609). null for bank statements.
  "transactions": [
    {
      "amount": number,             // absolute value of the FULL charge, not of a single installment
      "is_income": boolean,         // true only for refunds, deposits, CC payments received
      "date": "YYYY-MM-DD",
      "description": string,        // full original row text, e.g. "COMPRA MOVISTAR PAY SIMSCV"
      "merchant": string,           // cleaned name that keeps enough detail to be unique. e.g. "Movistar Pay"
      "category": "Alimentación" | "Supermercado" | "Transporte" | "Compras" | "Entretenimiento" | "Bares y Salidas" | "Cuentas y Servicios" | "Salud" | "Viajes" | "Suscripciones" | "Tecnología" | "Educación" | "Hogar" | "Ropa" | "Ingresos" | "Transferencia" | "Inversión" | "Seguros" | "Otros",
      "cuota_actual": integer|null, // installment number, e.g. 1 in "01/06"
      "cuotas_total": integer|null, // total installments, e.g. 6 in "01/06"
      "is_cc_payment": boolean,     // TRUE for rows like "PAGO TARJETA", "PAGO RECIBIDO", "ABONO" — money received by a credit card
      "items": [{"name": string, "price": number, "quantity": integer}]
    }
  ]
}

CRITICAL RULES:

1. MERCHANT CLEANUP (do NOT over-strip):
   - Keep enough info to distinguish similar merchants. Do NOT reduce
     "COMPRA MOVISTAR PAY SIMSCV" to just "Movistar" — return "Movistar Pay"
     (drop the leading verb "COMPRA" and the SKU code "SIMSCV", keep the
     distinctive brand name).
   - "UBER *EATS" → "Uber Eats", not "Uber".
   - "MERPAGO*LIDER" or "MP LIDER" → "Lider (Mercado Pago)".
   - Preserve locations if present and distinctive (e.g. "Starbucks Providencia").
   - Always return the untouched original in `description`.

2. AMOUNT vs INSTALLMENT:
   - Chilean CC statements often show two amount columns: "Monto total" and
     "Cuota a pagar". ALWAYS use "Monto total" as `amount` (what was actually
     spent). Set cuota_actual / cuotas_total from the "Cuotas" column (e.g.
     "03/06" → cuota_actual=3, cuotas_total=6). For single-payment rows (01/01)
     set cuota_actual=1, cuotas_total=1.

3. NUMBER FORMAT:
   - CLP NEVER uses decimals. "$17.517" means 17 517, not 17.51. "$1.489.991"
     means 1 489 991. If amounts have no decimals AND you see Spanish text or a
     Chilean bank name, currency is CLP.
   - Only return USD when you actually see "US$", "USD", or amounts with two
     decimal places in a dollar context.

4. SIGN CONVENTION:
   - Debit/checking account: "-$X" = expense (is_income=false); "+$X" = income.
   - Credit-card statement: normal purchases are expenses even if shown
     positive. Rows whose description contains "PAGO TARJETA", "PAGO
     RECIBIDO", "PAGO CMR", "ABONO", "ABONO TARJETA", or similar = is_income
     TRUE **and** is_cc_payment TRUE.
   - Refunds (negative purchase on a CC statement, or "DEVOLUCION") =
     is_income TRUE, is_cc_payment FALSE.

5. BANK / ACCOUNT HINTS:
   - If you can identify the bank and whether it's a debit or credit statement,
     fill `bank_hint` and `account_type_hint`. A statement with columns like
     "Cuotas" / "Cuota a pagar" is almost always a credit card.
   - Chilean cues: "CMR" / "Falabella" → credit card; "BancoEstado CuentaRUT" →
     debit; "Mercado Pago" → wallet.

6. CATEGORISATION (use Spanish names):
   Líder/Jumbo/Tottus/Unimarc/Santa Isabel/Ekono → Supermercado
   Uber/Cabify/DiDi/Beat/Metro/Copec/Shell/Enex → Transporte
   Netflix/Spotify/Disney/Apple/Google One → Suscripciones
   Farmacias Ahumada/Cruz Verde/Salcobrand → Salud
   Falabella/Ripley/Paris/Sodimac/Easy → Compras
   McDonald/KFC/Starbucks/restaurants/delivery → Alimentación
   Bars/pubs/discos/cervecería → Bares y Salidas
   Aguas/Enel/Movistar/Entel/Claro/WOM → Cuentas y Servicios
   CC payments (is_cc_payment=true) → category "Transferencia" — not real expenses.

7b. RECEIPT / BOLETA LINE ITEMS — Chilean supermarket rules (CRITICAL):
   When you see a receipt/boleta with individual product lines, put ALL items
   in the `items` array of a SINGLE transaction. Never create separate
   transactions per line.

   STEP 1 — READ EVERY PRODUCT LINE (do not skip any):
   Each line has: [barcode/code] [description] [price or NxPrice]
   The rightmost number on the line is the LINE TOTAL for that item.

   QUANTITY RULES — two sub-cases, DO NOT confuse them:

   a) "NxUNIT_PRICE" embedded in line (number immediately after x, e.g. "2x4.990", "3 x 2.990"):
      → quantity = N, unit neto price = the number right after x (UNIT_PRICE)
      → LINE_TOTAL = N × UNIT_PRICE = rightmost number on the line (verify!)
      → Store as: {"name": description, "price": UNIT_PRICE, "quantity": N}
      → Example: "2x4.990 PECHU POLLO $ 9.980" → price=4990, quantity=2

   a2) "Nx description LINE_TOTAL" (only quantity prefix, no unit embedded, e.g. "2x Hamburguesa $10.000"):
      → quantity = N, LINE_TOTAL = rightmost number on line
      → unit price = LINE_TOTAL / N  ← ALWAYS DIVIDE
      → Store as: {"name": description, "price": LINE_TOTAL/N, "quantity": N}
      → Example: "2x Hamburguesa $10.000" → price=5000, quantity=2
      CRITICAL: Never store LINE_TOTAL as price when quantity > 1.

   b) All other lines: quantity = 1, price = the printed number (= line total)
      → Store as: {"name": description, "price": <amount>, "quantity": 1}

   STEP 2 — VERIFY your item list:
   Add up all your line totals (price × quantity for each item).
   This sum MUST equal the "TOTAL NETO" printed at the bottom.
   If it doesn't match, re-read the items — you likely missed a line or
   misread a price.

   STEP 3 — ADD IVA and any extras:
   If the receipt shows an explicit "IVA" or "I.V.A." line with an amount:
     Add it: {"name": "IVA (19%)", "price": <exact_iva_amount>, "quantity": 1}
   If the receipt shows "Propina" or "Tip":
     Add it: {"name": "Propina", "price": <propina_amount>, "quantity": 1}

   STEP 4 — SET THE TRANSACTION AMOUNT:
   `amount` = TOTAL CON IVA = the final amount charged (TARJETA DE CRÉDITO /
   EFECTIVO / TOTAL row). NEVER use Total Neto as `amount`.

   FINAL CHECK: sum(price × quantity for ALL items including IVA row) MUST equal `amount`.
   If not, adjust — most likely the IVA item is missing or wrong.

   ALL product item prices are NETO (before IVA). The IVA row is the ONLY
   place where IVA appears.

8. CHILEAN IVA (TAX) — Rules from SII:
   Chilean boletas show THREE summary rows at the bottom (read these first — they
   are your ground truth and are more reliable than individual line prices):

     TOTAL NETO   → set `total_neto` to this exact printed value (e.g. 29521)
     IVA (19%)    → set `iva_amount` to this exact printed value (e.g. 5609)
     TOTAL / TARJETA DE CRÉDITO / EFECTIVO → set `amount` to this value (e.g. 35130)

   Product lines show NETO unit prices. `amount` MUST equal total_neto + iva_amount.

   BARCODE WARNING: Each product line starts with a long barcode (12-13 digits,
   e.g. "7803468001250"). The price is the SHORT number at the RIGHT end of the
   line (e.g. "$ 1.750"). NEVER confuse a barcode digit sequence with a price.

   COMMON MISTAKES TO AVOID:
   ❌ Reading the barcode as a price — barcodes are 12-13 digits; prices are 3-5 digits
   ❌ Setting `amount` = Total Neto instead of the final charged total
   ❌ Misreading NxPrice: "2x4.990" means qty=2, unit=4990, total=9980
   ❌ Using LINE_TOTAL as unit price when quantity>1 — always store unit price, not line total
   ❌ "2x Hamburguesa $10.000": price MUST be 5000 (not 10000); unit=10000/2=5000
   ❌ Adding IVA to individual product prices — products are always NETO
   ❌ Skipping any product line — read ALL lines top to bottom

7. IGNORE UI CHROME:
   Status-bar clock ("12:29"), battery %, WiFi/5G/LTE, nav tabs ("Inicio",
   "Cuentas", "Resumen", "Subir"), bottom tab bars, hamburger labels.

8. EXTRACT EVERY VISIBLE TRANSACTION. Do not dedupe here — the caller will
   handle cross-upload deduplication. If a row is partially cut off, skip it.

Return strict JSON — no markdown, no commentary.
"""


def _detect_mime(image_bytes: bytes) -> str:
    if image_bytes[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if image_bytes[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def _shrink_for_vision(image_bytes: bytes, max_side: int = 1600) -> bytes:
    """Downscale large screenshots so the API call is cheap & fast."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img = img.convert("RGB")
        w, h = img.size
        if max(w, h) <= max_side:
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=88, optimize=True)
            return buf.getvalue()
        scale = max_side / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=88, optimize=True)
        return buf.getvalue()
    except Exception:
        return image_bytes


# ═══════════════════════════════════════════════════════════════════════════════
# BOLETA ITEM PARSER  (Tesseract-first, industry-standard architecture)
# ═══════════════════════════════════════════════════════════════════════════════
#
# How professional services (Veryfi, Mindee, Taggun) handle supermarket receipts:
#
#   1. Tesseract for STRUCTURED data (prices, amounts, barcodes)
#      — deterministic, exact, no hallucinations, fast.
#   2. LLM / ML for SEMANTIC data (merchant name, category, date context).
#   3. Ground-truth anchoring: TOTAL NETO + IVA are always present on Chilean
#      boletas and override any other estimate.
#   4. Confidence score: if items_sum ≈ TOTAL_NETO we trust Tesseract items
#      directly and skip LLM item parsing entirely.
#
# Chilean boleta item line formats:
#   A) "7803468001250 CT PAN PITA                $ 1.750"
#      ^13-digit barcode^  ^description^           ^price^
#   B) "7891515551995"          ← barcode-only line (multi-quantity item)
#      "2x4.990       PECHU POLLO               $ 9.980"
#      ^qty x unit^   ^description^               ^line total^
#   C) Restaurant / bar (no barcodes):
#      "Hamburguesa clásica                       $ 8.500"
# ═══════════════════════════════════════════════════════════════════════════════

# Lines that are never product lines (summary/header/footer)
_SKIP_BOLETA_LINE = re.compile(
    # Totals and tax lines (handle noisy OCR: 0↔O, 3↔E)
    r"T[O0]TAL\s+N[E3]T[O0]|T[O0]TAL\s+[I1]VA|I\.?V\.?A|"
    # Any line starting with TOTAL or SUBTOTAL (footer summaries)
    r"^\s*T[O0]TAL\b|^\s*SUBTOTAL\b|"
    # Payment method lines
    r"TARJETA\s+DE|EFECTIVO|\bDEBITO\b|\bCREDITO\b|"
    # Branch/address lines like "SUC: AV. AMERICO VESPUCIO SUR 881"
    r"\bSUC[\s:]|"
    # Boleta/document header lines like "Boleta Electronica N° 003214567"
    r"BOL[^\s]*\s+[EE]L[EE]CTR|^\s*BOL\.|"
    # Column headers and receipt boilerplate
    r"CANT\b|PRECIO\s+UNIT|CODIGO|DESC[^\s]*\s+ARTICULO|"
    r"NUMERO\s+UNICO|TIMBRE|COMPROBANTE|BIENVENIDO|MI\s+CLUB|"
    r"AUTORIZACION|SII\s+RES|NRO\s+DE\s+OR|VERIFIQUE|PRECIOS\s+BAJOS|"
    r"TARJETA\s+D[EI]\s+D|^\*{2,}|^={3,}|^-{3,}",
    re.IGNORECASE,
)

# Chilean CLP number at end of line: "$ 1.750" or "1.750" or "1,750"
_CLP_PRICE_RE = re.compile(r"\$?\s*([\d]{1,3}(?:[.,]\d{3})*)\s*$")

# Multi-quantity pattern: "2x4.990" or "2 x 4.990" or "2X4990" (unit price embedded after x)
_QTY_X_UNIT_RE = re.compile(r"^(\d+)\s*[xX]\s*([\d.,]+)")
# Multi-quantity with description only: "2x Descripción" (no unit price after x, only text)
_QTY_X_DESC_RE = re.compile(r"^(\d+)\s*[xX]\s+([^\d].*)")

# Pure barcode line (12-14 digits, nothing else)
_BARCODE_ONLY_RE = re.compile(r"^\d{12,14}$")


def _parse_clp(s: str) -> float:
    """
    Convert Chilean number string to float.
    '$29.521' → 29521.0   (dot = thousands separator, no decimals)
    '5,609'   → 5609.0
    Handles OCR garbage like '$  29 521' or '29.521,00'.
    """
    s = re.sub(r"[^\d.,]", "", s.strip())
    # Detect format: if there's a dot followed by exactly 3 digits at the end → CLP thousands
    if re.search(r"\.\d{3}$", s):
        s = s.replace(".", "").replace(",", "")
    elif re.search(r",\d{3}$", s):
        s = s.replace(",", "")
    else:
        s = s.replace(",", "").replace(".", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _clean_item_name(raw: str) -> str:
    """Normalize an OCR-extracted product description."""
    s = raw.strip()
    # Drop leading garbage (non-alphanumeric that isn't part of a word)
    s = re.sub(r"^[^A-Za-záéíóúñÁÉÍÓÚÑ0-9]+", "", s)
    # Drop trailing non-alphanumeric (except %, g, ml, kg, L suffixes)
    s = re.sub(r"[^A-Za-záéíóúñÁÉÍÓÚÑ0-9%]+$", "", s)
    # Collapse multiple spaces
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()


def _price_at_end(line: str) -> float:
    """Extract the Chilean CLP price at the rightmost end of a text line."""
    m = _CLP_PRICE_RE.search(line)
    if m:
        v = _parse_clp(m.group(1))
        if v >= 100:        # anything below 100 CLP is likely noise
            return v
    return 0.0


def _parse_boleta_from_text(text: str) -> tuple[list, float, float, float]:
    """
    Parse a Chilean boleta from raw Tesseract text.

    Returns: (items, total_neto, iva_amount, confidence)
      items      — list[ParsedItem] with EXACT prices from the receipt text
      total_neto — TOTAL NETO printed on the receipt (0 if not found)
      iva_amount — IVA amount (computed as 19% of neto if not found explicitly)
      confidence — 0.0–1.0: how closely items_sum matches total_neto
                   ≥0.97 means we can trust Tesseract items directly

    Handles ALL Chilean boleta formats:
      - Supermarkets (Lider, Jumbo, Tottus, Unimarc, Santa Isabel)
      - Pharmacies (Cruz Verde, Salcobrand, Ahumada)
      - Restaurants and bars (no barcode prefix, plain description + price)
      - Multi-quantity lines: "2x4.990 PECHU POLLO $ 9.980"
    """
    lines = [ln.strip() for ln in text.replace("\r", "").split("\n") if ln.strip()]

    # ── Pass 1: extract ground-truth totals ──────────────────────────────────
    total_neto = 0.0
    iva_amount = 0.0
    for ln in lines:
        if re.search(r"T[O0]TAL\s+N[E3]T[O0]", ln, re.IGNORECASE) and total_neto == 0:
            v = _price_at_end(ln)
            if v > 0:
                total_neto = v
        elif re.search(r"\b[I1]\.?V\.?A\b", ln, re.IGNORECASE) and iva_amount == 0:
            v = _price_at_end(ln)
            # Reject the '19' from "(19%)" — IVA amount must be > 100 CLP
            if v > 100:
                iva_amount = v

    # IVA = 19% of NETO is a legal requirement in Chile → safe fallback
    if total_neto > 0 and iva_amount == 0:
        iva_amount = round(total_neto * 0.19)

    # ── Pass 2: parse item lines ──────────────────────────────────────────────
    items: list = []
    pending_barcode_line = False  # True when previous line was a barcode-only line

    for ln in lines:
        # Skip summary/header/footer rows
        if _SKIP_BOLETA_LINE.search(ln):
            pending_barcode_line = False
            continue

        # Pure barcode line — next line is the description+price for this item
        if _BARCODE_ONLY_RE.match(ln):
            pending_barcode_line = True
            continue

        price = _price_at_end(ln)
        if price <= 0:
            pending_barcode_line = False
            continue

        # Remove the price token from the end to isolate the description
        # Strip trailing "$ X.XXX" or just "X.XXX"
        desc_part = re.sub(r"\$?\s*[\d]{1,3}(?:[.,]\d{3})*\s*$", "", ln).strip()

        if pending_barcode_line:
            # ── Format B: barcode was on previous line ────────────────────────
            # Remaining part: "[NxUNIT] description" or "Nx description" or just "description"
            m_qty = _QTY_X_UNIT_RE.match(desc_part)
            if m_qty:
                qty = int(m_qty.group(1))
                unit_price = _parse_clp(m_qty.group(2))
                name_raw = desc_part[m_qty.end():].strip()
                name = _clean_item_name(name_raw)
                if unit_price >= 100 and name:
                    items.append(ParsedItem(name=name, price=unit_price, quantity=qty))
            else:
                # Remove leading barcode-like prefix if any slipped in
                desc_clean = re.sub(r"^\d{6,14}\s*", "", desc_part)
                # Check for "Nx Description" where price is the line total
                m_qty_desc = _QTY_X_DESC_RE.match(desc_clean)
                if m_qty_desc and price >= 100:
                    qty = int(m_qty_desc.group(1))
                    name = _clean_item_name(m_qty_desc.group(2))
                    unit_price = round(price / qty) if qty >= 2 else price
                    if unit_price >= 100 and name:
                        items.append(ParsedItem(name=name, price=unit_price, quantity=qty))
                else:
                    name = _clean_item_name(desc_clean)
                    if name and price >= 100:
                        items.append(ParsedItem(name=name, price=price, quantity=1))
            pending_barcode_line = False
            continue

        # ── Format A: barcode + description + price on ONE line ──────────────
        # Strip leading 12-14 digit barcode
        desc_no_barcode = re.sub(r"^\d{12,14}\s+", "", desc_part)

        # Check for inline multi-quantity "2x4.990 description" (unit price embedded)
        m_qty = _QTY_X_UNIT_RE.match(desc_no_barcode)
        if m_qty:
            qty = int(m_qty.group(1))
            unit_price = _parse_clp(m_qty.group(2))
            name = _clean_item_name(desc_no_barcode[m_qty.end():])
            if unit_price >= 100 and name:
                items.append(ParsedItem(name=name, price=unit_price, quantity=qty))
            continue

        # Check for "2x Description" where price is the line total (unit price = total / qty)
        m_qty_desc = _QTY_X_DESC_RE.match(desc_no_barcode)
        if m_qty_desc and price >= 100:
            qty = int(m_qty_desc.group(1))
            name = _clean_item_name(m_qty_desc.group(2))
            unit_price = round(price / qty) if qty >= 2 else price
            if unit_price >= 100 and name:
                items.append(ParsedItem(name=name, price=unit_price, quantity=qty))
                continue

        name = _clean_item_name(desc_no_barcode)
        if name and len(name) >= 2 and price >= 100:
            items.append(ParsedItem(name=name, price=price, quantity=1))

    # ── Pass 3: confidence score ─────────────────────────────────────────────
    confidence = 0.0
    if total_neto > 0 and items:
        items_sum_val = sum(it.price * it.quantity for it in items)
        # Chilean boletas may list items at neto prices (sum → total_neto) or at
        # IVA-inclusive prices (sum → total_neto + iva). Compare against whichever
        # total is closest so confidence stays high in both receipt formats.
        total_with_iva = total_neto + (iva_amount or round(total_neto * 0.19))
        ratio_neto = items_sum_val / total_neto
        ratio_iva = items_sum_val / total_with_iva if total_with_iva > 0 else float("inf")
        best_ratio = ratio_neto if abs(1 - ratio_neto) < abs(1 - ratio_iva) else ratio_iva
        confidence = max(0.0, 1.0 - abs(1.0 - best_ratio))

    return items, total_neto, iva_amount, confidence


def _extract_boleta_totals(image_bytes: bytes) -> dict:
    """
    Deterministically extract TOTAL NETO, IVA, and final TOTAL from a Chilean
    boleta/receipt using Tesseract + line-by-line regex.

    Works for ALL receipt types: supermarkets (Lider, Jumbo, Tottus, Unimarc),
    restaurants, bars, pharmacies, and any other Chilean boleta.

    Returns a dict with float keys: 'total_neto', 'iva_amount', 'total'.
    Empty dict = no boleta-style totals found (e.g. a bank screenshot).

    Strategy:
    ─────────
    1. Run Tesseract on the FULL image (not just bottom crop) — receipt layouts
       vary widely; totals can appear anywhere in the lower half.
    2. Scan line by line looking for TOTAL NETO / IVA / payment method rows.
    3. Sanity-check the extracted IVA against 19% of TOTAL NETO (legal requirement
       in Chile — if it doesn't match, fall back to computing IVA = neto × 0.19).
    """
    result: dict = {}
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("L")  # grayscale → better OCR
        # Scale up small images for better character recognition
        w, h = img.size
        if max(w, h) < 1200:
            scale = 1200 / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        # Binarise to improve contrast (Tesseract loves clean black-on-white)
        import numpy as _np
        arr = _np.array(img)
        _, arr = cv2.threshold(arr, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        img_proc = Image.fromarray(arr)
        text = pytesseract.image_to_string(img_proc, lang="spa", config="--psm 6 --oem 3")
    except Exception as exc:
        print(f"[ocr] _extract_boleta_totals: tesseract failed — {exc}")
        return result

    def last_number_on_line(line: str) -> float:
        """Return the rightmost CLP-style number (≥100) on a text line."""
        nums = re.findall(r"[\d.,]{3,12}", line)
        for n in reversed(nums):
            v = _parse_clp(n)
            if v >= 100:  # ignore noise like '19' from '(19%)'
                return v
        return 0.0

    # Line-by-line scan — first hit wins for each key
    for line in text.replace("\r", "").split("\n"):
        stripped = line.strip()
        if not stripped:
            continue

        if "total_neto" not in result and re.search(r"TOTAL\s*NETO", stripped, re.IGNORECASE):
            v = last_number_on_line(stripped)
            if v > 0:
                result["total_neto"] = v

        elif "iva_amount" not in result and re.search(r"\bI\.?V\.?A\b", stripped, re.IGNORECASE):
            v = last_number_on_line(stripped)
            if v > 0:
                result["iva_amount"] = v

        elif "total" not in result and re.search(
            r"TARJETA\s+DE\s+CR|TARJETA\s+D[EI]\s+D[EÉ]|EFECTIVO|^TOTAL\b",
            stripped, re.IGNORECASE | re.MULTILINE
        ):
            v = last_number_on_line(stripped)
            if v > 0:
                result["total"] = v

    # ── Sanity checks ────────────────────────────────────────────────────────
    # IVA = exactly 19% of TOTAL NETO is a Chilean SII legal requirement.
    # If Tesseract garbled the IVA line, compute it — it's not an estimate.
    if "total_neto" in result:
        computed_iva = round(result["total_neto"] * 0.19)
        if "iva_amount" not in result:
            result["iva_amount"] = computed_iva
        else:
            # If extracted IVA is more than 5% off the expected 19%, it's noise
            ratio = result["iva_amount"] / result["total_neto"]
            if abs(ratio - 0.19) > 0.05:
                result["iva_amount"] = computed_iva

    # Derive final total if missing
    if "total_neto" in result and "iva_amount" in result and "total" not in result:
        result["total"] = result["total_neto"] + result["iva_amount"]

    # Cross-check: final total should equal neto + iva (±2% for rounding)
    if "total" in result and "total_neto" in result and "iva_amount" in result:
        expected = result["total_neto"] + result["iva_amount"]
        if abs(result["total"] - expected) / max(expected, 1) > 0.02:
            result["total"] = expected  # trust arithmetic over OCR

    if result:
        print(f"[ocr] boleta ground-truth totals (tesseract): {result}")
    return result


def _normalize_boleta_items(
    items: list[ParsedItem],
    total_neto: float,
    iva_amount: float,
) -> tuple[list[ParsedItem], float]:
    """
    Ground-truth normalization for Chilean supermarket boletas.

    The LLM's individual item prices are best-effort (it can confuse barcodes
    with prices). But the printed SUMMARY rows (TOTAL NETO, IVA) are large,
    unambiguous, and reliably read. This function:

      1. Strips any IVA/tax rows the LLM may have added to `items`.
      2. Proportionally scales product neto prices so their sum == `total_neto`.
         The last item absorbs any rounding remainder (±1 CLP).
      3. Appends an authoritative IVA item with the exact printed `iva_amount`.
      4. Returns the normalised items and the authoritative total
         (total_neto + iva_amount).

    If the LLM gave no product items, items are returned unchanged.
    """
    # Step 1: separate product rows from IVA rows
    iva_names = {"iva", "i.v.a.", "i.v.a", "tax", "impuesto", "iva (19%)"}
    product_items = [it for it in items if it.name.strip().lower() not in iva_names]

    if not product_items:
        # Nothing to normalise — return original list with authoritative total
        return items, total_neto + iva_amount

    # Step 2: scale product prices to match total_neto
    llm_neto = sum(it.price * it.quantity for it in product_items)
    if llm_neto > 0:
        scale = total_neto / llm_neto
        normalised: list[ParsedItem] = []
        running = 0.0
        for i, it in enumerate(product_items):
            if i < len(product_items) - 1:
                new_price = round(it.price * scale)
                normalised.append(ParsedItem(name=it.name, price=new_price, quantity=it.quantity))
                running += new_price * it.quantity
            else:
                # Last item absorbs rounding remainder
                remainder = round(total_neto - running)
                unit_price = round(remainder / it.quantity) if it.quantity > 1 else remainder
                normalised.append(ParsedItem(name=it.name, price=unit_price, quantity=it.quantity))
    else:
        normalised = product_items

    # Step 3: append authoritative IVA row
    normalised.append(ParsedItem(name="IVA (19%)", price=round(iva_amount), quantity=1))

    # Step 4: authoritative total
    auth_total = total_neto + iva_amount
    return normalised, auth_total


def vision_parse(
    image_bytes: bytes, *, db=None, user_id=None,
) -> Optional[ParseResult]:
    """Send the image straight to a vision LLM and parse the JSON reply.

    Architecture — three layers of protection against wrong totals:

    Layer 1 (pre-LLM): Run Tesseract on the bottom of the image and regex-extract
        TOTAL NETO / IVA / TOTAL. These are deterministic — no ML involved.
        Inject them into the LLM prompt as explicit constraints.

    Layer 2 (LLM response): The LLM also reads total_neto and iva_amount from
        the JSON schema. We prefer Tesseract values when both are available.

    Layer 3 (post-LLM): Always override raw_amount with total_neto + iva_amount
        when those ground-truth values exist — even if the LLM returned a
        completely wrong amount (e.g. misread a barcode or QR code as a price).
    """
    if not ai_provider.is_available():
        return None
    try:
        compact = _shrink_for_vision(image_bytes)
        mime = _detect_mime(compact)
        b64 = base64.b64encode(compact).decode("ascii")
        data_url = f"data:{mime};base64,{b64}"

        # ═══════════════════════════════════════════════════════════════════
        # LAYER 1 — Tesseract structured parse (deterministic, exact prices)
        # ═══════════════════════════════════════════════════════════════════
        # For clean printed/digital receipts, Tesseract+regex gives EXACT item
        # prices because the text structure is deterministic. The LLM is used
        # for semantics (merchant, category, date) and as fallback for photos.
        raw_ocr_text = run_ocr(image_bytes)
        tess_items, tess_neto, tess_iva, tess_conf = _parse_boleta_from_text(raw_ocr_text)

        print(f"[ocr] Tesseract boleta parse: {len(tess_items)} items, "
              f"neto={tess_neto}, iva={tess_iva}, confidence={tess_conf:.2f}")

        # Also extract totals via the dedicated Tesseract scan (higher-res crop)
        ocr_totals = _extract_boleta_totals(image_bytes)

        # Prefer _extract_boleta_totals values for totals (it uses better preprocessing)
        gt_neto = ocr_totals.get("total_neto") or tess_neto
        gt_iva  = ocr_totals.get("iva_amount") or tess_iva

        # Build grounding text for the LLM prompt
        grounding_text = ""
        if gt_neto and gt_iva:
            tn = int(gt_neto)
            iva = int(gt_iva)
            tot = int(gt_neto + gt_iva)
            grounding_text = (
                f"\n\nGROUNDING (verified from receipt text — use EXACTLY):\n"
                f"  total_neto={tn}, iva_amount={iva}, amount={tot}"
            )
            if tess_conf >= 0.97 and tess_items:
                # Tesseract items are exact — tell the LLM not to re-parse items
                item_lines = "\n".join(
                    f"  {it.quantity}x {it.name} = {int(it.price * it.quantity)}"
                    for it in tess_items
                )
                grounding_text += (
                    f"\n\nITEMS (already parsed from receipt text, DO NOT re-read):\n"
                    f"{item_lines}\n"
                    f"Use these items exactly in your JSON `items` array."
                )

        user_text = (
            "Parse this receipt/screenshot. Return strict JSON.\n"
            "If this is a supermarket receipt (boleta): read EVERY product line "
            "and set amount = the final total charged."
            + grounding_text
        )

        resp = ai_provider.vision_json(
            system_prompt=_SYSTEM_PROMPT,
            user_text=user_text,
            image_data_url=data_url,
            temperature=0.0,
            purpose="parse",
            user_id=user_id,
            db=db,
        )
        if resp is None:
            return None
        data = json.loads(resp.text)
        txs = data.get("transactions", [])
        currency = data.get("currency") or "CLP"
        bank_hint = data.get("bank_hint") or ""
        account_type_hint = data.get("account_type_hint") or ""

        # ═══════════════════════════════════════════════════════════════════
        # LAYER 2 — Determine ground-truth totals
        # ═══════════════════════════════════════════════════════════════════
        try:
            llm_total_neto = float(data.get("total_neto") or 0)
        except (TypeError, ValueError):
            llm_total_neto = 0.0
        try:
            llm_iva_amount = float(data.get("iva_amount") or 0)
        except (TypeError, ValueError):
            llm_iva_amount = 0.0

        # LLM boleta values must pass 19% ratio check (otherwise it read a
        # barcode / SII code / QR number as total_neto, producing wrong totals)
        llm_values_valid = (
            llm_total_neto > 0
            and llm_iva_amount > 0
            and abs(llm_iva_amount / llm_total_neto - 0.19) < 0.05
        )

        # Priority: _extract_boleta_totals (best preprocessing) > Tesseract text
        # parse > validated LLM values > nothing
        ocr_neto = ocr_totals.get("total_neto", 0.0) or gt_neto
        ocr_iva  = ocr_totals.get("iva_amount", 0.0) or gt_iva

        if ocr_neto > 0 and ocr_iva > 0:
            total_neto_val = ocr_neto
            iva_amount_val = ocr_iva
        elif llm_values_valid:
            total_neto_val = llm_total_neto
            iva_amount_val = llm_iva_amount
        else:
            total_neto_val = 0.0
            iva_amount_val = 0.0

        out: list[ParsedReceipt] = []
        for t in txs:
            ca = t.get("cuota_actual")
            ct = t.get("cuotas_total")
            try:
                ca = int(ca) if ca is not None else None
                ct = int(ct) if ct is not None else None
            except (TypeError, ValueError):
                ca, ct = None, None

            raw_amount = abs(float(t.get("amount") or 0))

            # ═══════════════════════════════════════════════════════════════
            # LAYER 3 — Choose the best item source
            #
            # Priority order (best → worst):
            #   A) Tesseract items with confidence ≥ 0.97 → exact prices
            #   B) Tesseract items with confidence ≥ 0.80 → good prices,
            #      small normalization to close the gap
            #   C) LLM items + proportional normalization (fallback)
            #   D) No items (bank statements, non-boleta receipts)
            # ═══════════════════════════════════════════════════════════════
            if tess_conf >= 0.97 and tess_items:
                # ── A: Tesseract exact ────────────────────────────────────
                # Prices are directly from text — no scaling needed.
                # Append IVA row so the split screen shows the tax correctly.
                items = list(tess_items)
                if total_neto_val > 0 and iva_amount_val > 0:
                    items.append(ParsedItem(
                        name="IVA (19%)",
                        price=round(iva_amount_val),
                        quantity=1,
                    ))
                    raw_amount = float(round(total_neto_val + iva_amount_val))
                else:
                    raw_amount = float(sum(it.price * it.quantity for it in items))

            elif tess_conf >= 0.80 and tess_items and total_neto_val > 0 and iva_amount_val > 0:
                # ── B: Tesseract good but small rounding gap — light normalize
                items, raw_amount = _normalize_boleta_items(
                    tess_items, total_neto_val, iva_amount_val
                )

            else:
                # ── C / D: Fall back to LLM items ────────────────────────
                items = [ParsedItem(**it) for it in t.get("items", []) if it.get("name")]

                if total_neto_val > 0 and iva_amount_val > 0:
                    auth_total = round(total_neto_val + iva_amount_val)
                    if items:
                        # Proportional normalization — fixes total, best-effort items
                        items, raw_amount = _normalize_boleta_items(
                            items, total_neto_val, iva_amount_val
                        )
                    else:
                        raw_amount = float(auth_total)

            out.append(ParsedReceipt(
                amount=raw_amount,
                is_income=bool(t.get("is_income", False)),
                date=_parse_date(t.get("date", "")) if t.get("date") else date.today(),
                merchant=str(t.get("merchant", ""))[:200],
                description=str(t.get("description", ""))[:255],
                category=str(t.get("category") or "Other"),
                currency=currency,
                is_cc_payment=bool(t.get("is_cc_payment", False)),
                cuota_actual=ca,
                cuotas_total=ct,
                items=items,
                raw_text="",
            ))
        if not out:
            return None
        return ParseResult(
            transactions=out,
            bank_hint=str(bank_hint)[:80],
            account_type_hint=str(account_type_hint)[:16],
        )
    except Exception as e:  # noqa: BLE001
        print(f"[ocr] vision_parse failed, falling back to Tesseract: {e}")
        return None


# ---------- Public entry point ----------
def parse_receipt(
    image_bytes: bytes, *, db=None, user_id: Optional[int] = None,
) -> ParseResult:
    """
    Parse an image into a ParseResult (transactions + bank/account hints).

    Strategy:
      1. Try vision (LLM reads the image directly) — most accurate.
      2. Fall back to Tesseract + heuristic if no API key or vision fails.
    """
    vr = vision_parse(image_bytes, db=db, user_id=user_id)
    if vr:
        return vr

    text = run_ocr(image_bytes)
    txs = heuristic_parse(text)
    if txs:
        txs[0].raw_text = text
    return ParseResult(transactions=txs)
