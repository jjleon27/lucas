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

import numpy as np
from PIL import Image

# cv2 (opencv) and pytesseract are only used by the offline Tesseract fallback
# path (_preprocess / run_ocr). They're imported lazily so the module still loads
# in environments without the tesseract binary (e.g. Vercel serverless), where
# the vision-LLM path is the only one that runs.

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass  # HEIC support unavailable; JPEG/PNG still work fine

from .ai import provider as ai_provider
from .config import settings
from .schemas import ParsedItem, ParsedReceipt


# ---------- PDF → image bytes conversion ----------
def pdf_page_to_image_bytes(pdf_bytes: bytes, page_index: int = 0, dpi: int = 150) -> bytes:
    """
    Render a single PDF page to JPEG bytes.
    Returns JPEG bytes that can be fed into parse_receipt() directly.

    Uses pypdfium2 (pure-wheel, no system poppler) so it works on serverless.
    """
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(pdf_bytes)
    try:
        page = pdf[page_index]
        bitmap = page.render(scale=dpi / 72.0)
        pil_img = bitmap.to_pil().convert("RGB")
        buf = io.BytesIO()
        pil_img.save(buf, format="JPEG", quality=90)
        return buf.getvalue()
    finally:
        pdf.close()


def pdf_page_count(pdf_bytes: bytes) -> int:
    """Return the number of pages in a PDF."""
    try:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(pdf_bytes)
        try:
            return len(pdf)
        finally:
            pdf.close()
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
    # Ancho/alto reales de la foto, orientada hacia arriba (post EXIF-transpose)
    # — el marco de referencia del que bbox_x0/y0/x1/y1 son %. Ver nota en
    # vision_parse(). None si no se pudo determinar (Tesseract-only fallback).
    image_width: Optional[int] = None
    image_height: Optional[int] = None

    def __bool__(self) -> bool:
        return bool(self.transactions)


# ---------- Image preprocessing (Tesseract path) ----------
def _preprocess(image_bytes: bytes) -> "np.ndarray":
    import cv2  # lazy: only the Tesseract fallback needs opencv
    arr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        img = np.array(Image.open(io.BytesIO(image_bytes)).convert("RGB"))[:, :, ::-1]

    # Downscale to max 2000px — Tesseract accuracy peaks around 300 DPI,
    # huge images just slow it down without helping.
    h, w = img.shape[:2]
    if max(h, w) > 2000:
        scale = 2000 / max(h, w)
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

    # CLAHE on L channel of LAB — lifts dark/flash-lit bar receipt photos
    # without blowing out already-bright areas.
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l_ch, a_ch, b_ch = cv2.split(lab)
    l_ch = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(l_ch)
    img = cv2.cvtColor(cv2.merge([l_ch, a_ch, b_ch]), cv2.COLOR_LAB2BGR)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Deskew: estimate rotation from dilated text contours and correct it.
    # Skips correction when angle is tiny (<0.5°) or extreme (>15°).
    try:
        blur = cv2.GaussianBlur(gray, (9, 9), 0)
        _, otsu = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (30, 5))
        dilated = cv2.dilate(otsu, kernel, iterations=2)
        coords = np.column_stack(np.where(dilated > 0))
        if len(coords) > 100:
            angle = cv2.minAreaRect(coords)[-1]
            angle = -(90 + angle) if angle < -45 else -angle
            if 0.5 < abs(angle) < 15:
                gh, gw = gray.shape
                M = cv2.getRotationMatrix2D((gw // 2, gh // 2), angle, 1.0)
                gray = cv2.warpAffine(gray, M, (gw, gh),
                                      flags=cv2.INTER_CUBIC,
                                      borderMode=cv2.BORDER_REPLICATE)
    except Exception:
        pass  # deskew is best-effort; never crash on a bad image

    gray = cv2.bilateralFilter(gray, 9, 75, 75)
    thresh = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15
    )
    return thresh


def run_ocr(image_bytes: bytes) -> str:
    """OCR via Tesseract. Returns "" if opencv/tesseract aren't available
    (e.g. serverless) — callers then fall through to the vision-LLM path."""
    try:
        import pytesseract  # lazy: needs the `tesseract` system binary
        processed = _preprocess(image_bytes)
        return pytesseract.image_to_string(processed, lang="eng+spa").strip()
    except Exception as e:  # ImportError, TesseractNotFoundError, cv2 decode errors…
        print(f"[ocr] run_ocr unavailable ({type(e).__name__}: {e}) — skipping Tesseract path")
        return ""


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
        vals = []
        for raw in _MONEY_TOKEN.findall(text):
            if len(re.sub(r"\D", "", raw)) >= 10:  # barcode / RUT / account number
                continue
            vals.append(abs(_to_float(raw)))
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
- A restaurant/bar POS receipt (Toteat, Restō, Revo, etc.) — may be a per-seat comanda
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

7a. RESTAURANT / BAR POS RECEIPT (Toteat, Restō, Revo, etc.) — CRITICAL:
   These receipts show a per-seat "comanda" with TWO totals:
     - "Total General Mesa" / "Total Mesa": the FULL TABLE bill (ALL customers combined)
     - "Consumo Cliente" / "Subtotal Comensal" / "Mi Consumo": THIS CUSTOMER'S portion
   RULE: When BOTH appear, set `amount` = "Consumo Cliente" value (not the table total).
   The table total is irrelevant for this customer's transaction.

   MODIFIER ITEMS (lines starting with "+"):
   Lines like "+Coca Zero", "+Sin hielo", "+Azúcar" are FREE add-ons/modifiers.
   They ALWAYS have price = 0. Include them as {"name": "+Coca Zero", "price": 0, "quantity": 1}.
   NEVER assign a non-zero price to a modifier item. NEVER steal the price from the next row.

   TABLE FORMAT rows (e.g. "| 1 | Producto X | 9.000 |"):
   - Each row is ONE separate item (do NOT merge identical rows)
   - The leading "1" (Cant column) is the quantity
   - Repeated identical rows = repeated individual items (e.g. 9 rows of "Cerveza" = 9 items)
   - Read EVERY row top to bottom without skipping

   "Propina Sugerida" / "Total c/propina" lines: DO NOT include in items.
   These are footer summary rows, not consumables.

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

   a3) "N description LINE_TOTAL" (restaurant/bar style: leading number, NO "x", e.g. "3 vienesa italiana 13200"):
      → quantity = N (leading integer 1–20), LINE_TOTAL = rightmost number on line
      → unit price = LINE_TOTAL / N  ← ALWAYS DIVIDE
      → Store as: {"name": description, "price": LINE_TOTAL/N, "quantity": N}
      → Example: "3 vienesa italiana 13200" → price=4400, quantity=3
      → Example: "6 schop medio royal 28800" → price=4800, quantity=6
      → Example: "3 completos por 13200" → price=4400, quantity=3  ("por" = "for", LINE_TOTAL not unit price)
      CRITICAL: Even without "x", a leading integer IS the quantity. ALWAYS divide LINE_TOTAL by it.
      CRITICAL: "N items por TOTAL" — "por" means "for (the total)", NOT "at (unit price)".
      CRITICAL: Never store LINE_TOTAL (13200, 28800, 8800) as the unit price when a leading quantity exists.

   TABLE FORMAT (| Cant | Producto | Precio |):
   The "Cant" column is the ONLY source of quantity. Numbers inside product names are specs, NOT quantities:
   "Cerveza 35°" → the "35" is alcohol degrees (quantity from Cant column, usually 1)
   "Whisky 12 años" → the "12" is a product descriptor
   NEVER treat a number embedded in the product name as the quantity.

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
   ❌ "3 vienesa italiana 13200": price MUST be 4400 (not 13200); unit=13200/3=4400
   ❌ "6 schop medio royal 28800": price MUST be 4800 (not 28800); unit=28800/6=4800
   ❌ Adding IVA to individual product prices — products are always NETO
   ❌ Skipping any product line — read ALL lines top to bottom

7. IGNORE UI CHROME:
   Status-bar clock ("12:29"), battery %, WiFi/5G/LTE, nav tabs ("Inicio",
   "Cuentas", "Resumen", "Subir"), bottom tab bars, hamburger labels.

8. EXTRACT EVERY VISIBLE TRANSACTION. Do not dedupe here — the caller will
   handle cross-upload deduplication. If a row is partially cut off, skip it.

Return strict JSON — no markdown, no commentary.
"""

# Prompt chain-of-thought de 3 pasos. La clave: pedir qty, unit_price y
# line_total como campos separados por item, forzando al modelo a exponer
# la relación en vez de calcularla en silencio (que es donde se confunde).
_RECEIPT_PROMPT = """Eres un experto en recibos y boletas chilenas. Lee la imagen y extrae cada item.

ORIENTACIÓN: La imagen puede estar rotada. El texto se lee de izquierda a derecha, precios a la derecha.

MONEDA:
- Por defecto es CLP (Chile): los puntos son separadores de miles ("9.000" = nueve mil), sin decimales.
- Si el recibo es de otro país (dirección/teléfono/NIF español, "IVA INCLUIDO", símbolo €, o
  montos con 2 decimales tipo "26,50" / "26.50"), usa esa moneda ("EUR", "USD", etc.) y CONSERVA
  los decimales. NO conviertas a CLP. NO multipliques por 1000.

REGLAS DE NÚMEROS (CLP):
- Los puntos son separadores de miles: "9.000" = nueve mil pesos.
- El número entero ANTES del nombre es la CANTIDAD. Si no hay número, qty=1.
- `line_total` = el número IMPRESO al final de cada línea, TAL CUAL. NO lo modifiques,
  NO le quites IVA, NO lo escales. Si dice "$ 2.150", line_total = 2150.
- Ejemplos: "6 Schop Escudo 26.400" → qty=6, line_total=26400
             "2x4.990 FILE POLLO 9.980" → qty=2, line_total=9980
             "CHOCO 160  $ 2.150" → qty=1, line_total=2150

VERIFICACIÓN: la suma de todos los line_total debe coincidir con el TOTAL final
cobrado ("TARJETA DE CREDITO" / "EFECTIVO" / "TOTAL"), NO con el "TOTAL NETO".
En una boleta chilena de supermercado los precios de cada línea YA INCLUYEN IVA.

BOLETA FISCAL (SII) vs comanda:
- Si aparecen impresas líneas "TOTAL NETO" e "IVA (19%)", SIEMPRE cópialas EXACTAS
  en total_neto e iva_amount. Son datos útiles pero NO cambian los line_total ni
  el amount (los line_total ya incluyen IVA).
- Si es comanda de bar/restaurante sin esas etiquetas → deja ambos en null.

AMOUNT — el monto realmente cobrado:
- Usa el número de la línea rotulada exactamente "TOTAL" / "A PAGAR" / "TARJETA" / "EFECTIVO" /
  "CONSUMO CLIENTE" (la última si hay varias). Nunca un "SUBTOTAL" ni un "TOTAL NETO".
- PROPINA SUGERIDA: si hay una línea "Propina sugerida"/"Prop. sugerida 10%" y además una línea
  "TOTAL + PROPINA" / "Total c/propina", esa propina NO se cobra: usa el "TOTAL" simple (sin propina).
- Pero si la propina YA está sumada dentro de la línea final "TOTAL" (p.ej. SUBTOTAL 10.000 +
  PROPINA 1.000 y luego "TOTAL 11.000"), entonces amount = 11.000 (el TOTAL tal cual).

FECHA: si la fecha no se lee con certeza (dígitos borrosos), devuelve null. NO adivines.

RECUADRO (bbox) — MUY IMPORTANTE, léelo con cuidado:
Por cada ítem necesitamos solo su posición vertical en la boleta (el resaltado
en la app cubre todo el ancho, no hace falta el ancho del bloque):
  "bbox_y0" (borde superior, justo arriba del texto de esa línea) y "bbox_y1"
  (borde inferior, justo debajo) — ambos 0-100, % del alto de la imagen, con
  un decimal.
Reglas:
- Cada bbox_y0/y1 debe ser AJUSTADO a esa única línea — no debe tapar la línea
  de arriba ni la de abajo, ni el código de barras si lo hay.
- y1 > y0 siempre. Sigue el orden natural de lectura de arriba hacia abajo.
- ANTES de responder, revisa mentalmente cada bbox_y0/y1 contra la imagen: ¿tapa
  esa línea completa y NADA más? Si dudas, prefiere un rango más angosto (que no
  invada líneas vecinas) antes que uno más ancho.
- Es una estimación visual — no hace falta exactitud de píxel, pero sí que quede
  sobre la línea correcta y no se encime con otras.

CATEGORÍA:
- "Bares y Salidas": schops, cervezas, fernet, tragos, pub/bar
- "Alimentación": restaurantes, delivery, cafeterías
- "Supermercado": Lider, Jumbo, Tottus, Unimarc
- "Transporte": Uber, Cabify, DiDi, Copec
- "Salud": farmacias, clínicas
- "Suscripciones": Netflix, Spotify, Apple
- "Compras": Falabella, Ripley, Sodimac
- "Cuentas y Servicios": agua, luz, gas, internet
- "Otros": si no encaja

DEVUELVE SOLO ESTE JSON (sin markdown):
{
  "currency": "CLP",   // o "EUR" / "USD" / etc. según el recibo (ver MONEDA)
  "total_neto": número o null,
  "iva_amount": número o null,
  "transactions": [
    {
      "amount": número,
      "date": "YYYY-MM-DD" o null,
      "merchant": "nombre del local",
      "category": "categoría",
      "is_income": false,
      "items": [
        {"name": "nombre", "quantity": qty, "line_total": line_total,
         "bbox_y0": 0_a_100, "bbox_y1": 0_a_100}
      ]
    }
  ]
}
"""

# Stage-2 prompt (texto → JSON): mismo prompt pero adaptado para texto pre-transcrito.
_RECEIPT_TEXT_PROMPT = """Eres un experto leyendo boletas, comandas y recibos chilenos. Te paso el texto transcrito de un recibo. Extrae cada item con su cantidad y precio unitario.

REGLAS CRÍTICAS sobre números en Chile:
- Los puntos son separadores de miles. "9.000" son nueve mil pesos, NO nueve.
- Cuando hay un número entero antes del nombre de un item (ej: "6 Schop", "3 Vienesa Italiana"), ese número es la CANTIDAD.
- El número a la derecha de cada línea es el TOTAL de esa línea (cantidad × precio unitario).
- El precio que debes guardar es el UNITARIO = total_de_la_línea ÷ cantidad.
- Ejemplo: "6 Schop Escudo  26.400" → quantity=6, price=4400 (porque 26400/6=4400).
- Ejemplo: "2x4.990 Pechu Pollo 9.980" → quantity=2, price=4990 (el unitario va embebido tras la "x").
- Si NO hay número antes del nombre, la cantidad es 1.
- Números embebidos en el nombre como "35°", "500cc", "12 años" NO son cantidad.
- Modificadores con "+" (ej: "+Sin hielo") son gratis: price=0, quantity=1.

MONEDA:
- Por defecto CLP (Chile): puntos = separadores de miles, sin decimales.
- Si el recibo es de otro país (NIF/dirección españoles, "IVA INCLUIDO", símbolo €, o
  montos con 2 decimales tipo "26,50"), usa esa moneda ("EUR", "USD"...) y CONSERVA los
  decimales. NO conviertas a CLP. NO multipliques por 1000.

BOLETA FISCAL vs comanda/POS:
- Si el texto muestra líneas "TOTAL NETO" e "IVA" explícitas, llena total_neto e iva_amount.
- Si es comanda de bar/restaurante o ticket POS sin esas etiquetas, deja ambos en null.

AMOUNT — el monto realmente cobrado:
- El número de la línea rotulada "TOTAL" / "A PAGAR" / "TARJETA" / "EFECTIVO" /
  "CONSUMO CLIENTE" (la última si hay varias). Nunca un "SUBTOTAL" ni "TOTAL NETO".
- PROPINA SUGERIDA: si hay una línea "Propina sugerida" y aparte un "TOTAL + PROPINA",
  esa propina NO se cobra → usa el "TOTAL" simple.
- Si la propina YA está sumada dentro de la línea final "TOTAL", usa ese TOTAL tal cual.

FECHA: si no se lee con certeza, devuelve null. NO adivines.

CATEGORÍA:
- "Bares y Salidas": schops, cervezas, piscos, fernet, tragos.
- "Alimentación": restaurantes, delivery comida, cafeterías.
- "Supermercado": Lider, Jumbo, Tottus, Unimarc, Santa Isabel.
- "Transporte": Uber, Cabify, DiDi, Metro, Copec.
- "Salud": farmacias, clínicas.
- "Suscripciones": Netflix, Spotify, Disney.
- "Compras": Falabella, Ripley, Paris, Sodimac.
- "Cuentas y Servicios": agua, luz, gas, internet.
- "Otros" si no encaja.

DEVUELVE SOLO ESTE JSON (sin markdown):
{
  "currency": "CLP",   // o "EUR" / "USD" / etc. según el recibo (ver MONEDA)
  "total_neto": número o null,
  "iva_amount": número o null,
  "transactions": [
    {
      "amount": número,
      "date": "YYYY-MM-DD" o null,
      "merchant": "nombre del local" o "",
      "category": "una de las categorías de arriba",
      "is_income": false,
      "items": [
        {"name": "nombre", "price": precio_unitario, "quantity": cantidad}
      ]
    }
  ]
}
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


def _resize_for_vision(img: "Image.Image", max_side: int = 2800) -> "Image.Image":
    """Downscale so the API call is cheap & fast — cuts base64 payload/upload
    time (OpenAI caps `detail:high` to ~2048px server-side anyway, so this
    doesn't reduce tile count much, but it does cut how long the image takes
    to reach them).

    Caps the longer side at `max_side` px and total pixels at ~8M. Phone photos
    of rotated paper receipts have fine-print qty digits ~30px tall that get
    mangled at lower resolutions — 2800px keeps them legible to the model.
    """
    w, h = img.size
    scale = min(1.0, max_side / max(w, h))
    # Also cap total pixel area (~8M px ≈ 2828×2828)
    area_scale = min(1.0, (8_000_000 / (w * h)) ** 0.5)
    scale = min(scale, area_scale)
    if scale < 1.0:
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    return img


def _shrink_for_vision(image_bytes: bytes, max_side: int = 2800) -> bytes:
    """Bytes-in/bytes-out wrapper around `_resize_for_vision` for callers that
    don't need to keep the PIL Image around (EXIF-transposes + re-encodes)."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img = img.convert("RGB")
        try:
            from PIL import ImageOps
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass
        img = _resize_for_vision(img, max_side=max_side)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=92, optimize=True)
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
    # POS restaurant receipt summary lines (Toteat, etc.)
    r"CONSUMO\s+CLIENTE|CONSUMO\s+MESA|CONSUMO\s+GENERAL|"
    r"TOTAL\s+GENERAL\s+MESA|TOTAL\s+MESA|"
    r"\bPROPINA\b|TOTAL\s+C[/\\]PROPINA|COMENSAL\b|COMENSALES\b|"
    r"CAMARERO\b|COMANDA\b|TOTEAT|RESTAU?RANT|"
    # Payment method and change lines
    r"TARJETA\s+DE|EFECTIVO|\bDEBITO\b|\bCREDITO\b|"
    r"\bVISA\b|\bMASTERCARD\b|\bAMEX\b|\bREDCOMPRA\b|\bWEBPAY\b|\bTRANSBANK\b|\bVUELTO\b|"
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
# Restaurant-style: leading qty (1-99) + space + word (no x): "3 vienesa italiana"
_QTY_NUM_DESC_RE = re.compile(r"^([1-9][0-9]?)\s+([A-Za-záéíóúñÁÉÍÓÚÑ].*)")

# Pure barcode line (12-14 digits, nothing else)
_BARCODE_ONLY_RE = re.compile(r"^\d{12,14}$")

# Pipe-table rows: "| 1 | Producto X | 9.000 |"
_PIPE_TABLE_ROW_RE = re.compile(r"\|\s*(\d{1,2})\s*\|(.+?)\|\s*([0-9.,]{3,12})\s*\|?")
_PIPE_TABLE_HEADER_RE = re.compile(
    r"\b(?:cant|cantidad|producto|descripci[oó]n|precio|total)\b", re.IGNORECASE
)


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
    # Normalize non-standard whitespace from some POS/scanner outputs
    text = re.sub(r"\t+", "  ", text)
    text = re.sub(r"[   ]+", " ", text)

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
                    # Restaurant style: "3 vienesa italiana" (no x, leading qty 1-99)
                    m_qty_num = _QTY_NUM_DESC_RE.match(desc_clean)
                    if m_qty_num and price >= 100:
                        qty = min(int(m_qty_num.group(1)), 50)
                        name = _clean_item_name(m_qty_num.group(2))
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

        # Restaurant style: "3 vienesa italiana" (no x, leading qty 1-99)
        m_qty_num = _QTY_NUM_DESC_RE.match(desc_no_barcode)
        if m_qty_num and price >= 100:
            qty = min(int(m_qty_num.group(1)), 50)  # clamp: >50 likely OCR mis-parse
            name = _clean_item_name(m_qty_num.group(2))
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


def _parse_pipe_table(text: str) -> list[ParsedItem]:
    """
    Parse pipe-table format: | Cant | Producto | Total |

    The Cant column is the ONLY source of quantity. Numbers inside product
    names (e.g. '35°' in 'Cerveza 35°') are NOT quantities.
    """
    items: list[ParsedItem] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or _PIPE_TABLE_HEADER_RE.search(line):
            continue
        m = _PIPE_TABLE_ROW_RE.search(line)
        if not m:
            continue
        qty = int(m.group(1))
        name = _clean_item_name(m.group(2).strip())
        line_total = _parse_clp(m.group(3).strip())
        if not name or line_total < 100:
            continue
        unit_price = round(line_total / qty) if qty > 1 else int(line_total)
        if unit_price >= 100:
            items.append(ParsedItem(name=name, price=unit_price, quantity=qty))
    return items


def _extract_boleta_totals(image_bytes: bytes, text: Optional[str] = None) -> dict:
    """
    Deterministically extract TOTAL NETO, IVA, and final TOTAL from a Chilean
    boleta/receipt using line-by-line regex.

    If `text` is provided (pre-computed OCR output), skips Tesseract entirely —
    this avoids a second Tesseract pass when the caller already has the text.
    """
    result: dict = {}
    if text is None:
        try:
            import cv2  # lazy — Tesseract fallback only
            import pytesseract
            img = Image.open(io.BytesIO(image_bytes)).convert("L")
            w, h = img.size
            if max(w, h) < 1200:
                scale = 1200 / max(w, h)
                img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            import numpy as _np
            arr = _np.array(img)
            _, arr = cv2.threshold(arr, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            img_proc = Image.fromarray(arr)
            text = pytesseract.image_to_string(img_proc, lang="spa", config="--psm 6 --oem 3")
        except Exception as exc:
            print(f"[ocr] _extract_boleta_totals: tesseract unavailable — {exc}")
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
    consumo_cliente = 0.0  # POS per-seat total (overrides "Total General Mesa")
    total_general_mesa = 0.0

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

        elif re.search(r"CONSUMO\s+CLIENTE|SUBTOTAL\s+COMENSAL|MI\s+CONSUMO", stripped, re.IGNORECASE):
            v = last_number_on_line(stripped)
            if v > 0:
                consumo_cliente = v

        elif re.search(r"TOTAL\s+GENERAL\s+MESA|TOTAL\s+MESA", stripped, re.IGNORECASE):
            v = last_number_on_line(stripped)
            if v > 0:
                total_general_mesa = v

        elif "total" not in result and re.search(
            r"TARJETA\s+DE\s+CR|TARJETA\s+D[EI]\s+D[EÉ]|EFECTIVO|^TOTAL\b"
            r"|A\s+PAGAR|^PAGAR\b|MONTO\s+TOTAL|TOTAL\s+A\s+PAGAR",
            stripped, re.IGNORECASE | re.MULTILINE
        ):
            v = last_number_on_line(stripped)
            if v > 0:
                result["total"] = v

    # For POS receipts: use "Consumo Cliente" as the amount (this customer's share)
    if consumo_cliente > 0:
        result["total"] = consumo_cliente
        result["is_pos_per_seat"] = True  # flag for downstream logic
    if total_general_mesa > 0:
        result["total_general_mesa"] = total_general_mesa

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


def _items_look_plausible(items: list, items_sum: float) -> bool:
    """True when items appear to have real CLP restaurant prices (not scaled garbage).

    Requires ≥ 2 items: a single item with a large total gap more likely has a wrong
    unit price than a wrong total, so we let the drop-items path handle that.
    """
    if len(items) < 2 or items_sum < 500:
        return False
    real = [it for it in items if it.price > 0]
    if not real:
        return False
    if any(it.price > 500_000 for it in real):
        return False
    return (items_sum / len(real)) >= 200


def _find_plausible_total(ocr_text: str, items_sum: float) -> Optional[int]:
    """Scan OCR text for a number that could be the real receipt total (items_sum + service).

    Looks for values in [items_sum*1.01, items_sum*2.0] — covers service charges and
    cover charges at upscale venues. Returns the candidate closest to items_sum.
    """
    if not ocr_text or items_sum < 500:
        return None
    candidates: list[int] = []
    for m in re.finditer(r'\b(\d{1,3}(?:[.,]\d{3})+)\b', ocr_text):
        raw = m.group(1).replace('.', '').replace(',', '')
        try:
            n = int(raw)
        except ValueError:
            continue
        if items_sum * 1.01 <= n <= items_sum * 2.0:
            candidates.append(n)
    if not candidates:
        return None
    return min(candidates, key=lambda n: abs(n - items_sum))



def _prep_receipt_image(image_bytes: bytes) -> tuple[str, Optional[int], Optional[int], bytes]:
    """Normaliza una foto de boleta para mandarla a un modelo de visión:
    corrige rotación EXIF, redimensiona (sube menos pesado, sin perder los
    ~2048px que OpenAI usa igual internamente para detail:high), sube
    contraste/nitidez (fix legibilidad de cantidades) y codifica a data URL.

    Devuelve (data_url, upright_w, upright_h, send_bytes) — upright_w/h son
    las dimensiones reales orientadas hacia arriba (post EXIF-transpose), el
    marco de referencia del que son % los bbox_* que devuelve el modelo;
    None si no se pudo determinar (se usa el fallback de bytes originales).
    `send_bytes` son los bytes JPEG realmente enviados (o los originales si
    el preprocesamiento falló) — algunos callers los necesitan para leer las
    dimensiones reales del header (fallback de vision_transcribe).
    Compartido por `vision_parse` (prompt JSON completo, boletas + cartolas)
    y `vision_parse_bill` (prompt de texto liviano, solo para split de
    cuentas) — mismo preprocesamiento, prompts/parsers separados.
    """
    upright_w: Optional[int] = None
    upright_h: Optional[int] = None
    try:
        from PIL import ImageEnhance, ImageOps
        img_pil = Image.open(io.BytesIO(image_bytes))
        img_pil = ImageOps.exif_transpose(img_pil)  # fix iPhone rotation
        img_pil = img_pil.convert("RGB")
        upright_w, upright_h = img_pil.size
        img_pil = _resize_for_vision(img_pil, max_side=2000)
        img_pil = ImageEnhance.Contrast(img_pil).enhance(1.8)
        img_pil = ImageEnhance.Sharpness(img_pil).enhance(2.0)
        buf = io.BytesIO()
        img_pil.save(buf, format="JPEG", quality=95)
        send_bytes = buf.getvalue()
    except Exception:
        send_bytes = image_bytes
    b64 = base64.b64encode(send_bytes).decode("ascii")
    return f"data:image/jpeg;base64,{b64}", upright_w, upright_h, send_bytes


def vision_parse(
    image_bytes: bytes, *, db=None, user_id=None,
) -> Optional[ParseResult]:
    """Send the image straight to GPT-4o. No Tesseract grounding.

    Fast path only: clean IVA boleta where Tesseract conf ≥ 0.97 (free + instant).
    Everything else goes directly to GPT-4o — the model reads the image as-is,
    just like ChatGPT does, with no pre-processing interference.
    """
    if not ai_provider.is_available():
        return None
    try:
        # ── FAST PATH: clean IVA boleta (Tesseract free, works on crisp printouts) ──
        raw_ocr_text = run_ocr(image_bytes)
        tess_items, tess_neto, tess_iva, tess_conf = _parse_boleta_from_text(raw_ocr_text)

        if tess_conf >= 0.97 and tess_items and tess_neto and tess_iva:
            print(f"[ocr] Fast path: IVA boleta (conf={tess_conf:.2f}), skipping LLM")
            items_fp = list(tess_items)
            items_fp.append(ParsedItem(name="IVA (19%)", price=round(tess_iva), quantity=1))
            d_m = _DATE_RE.search(raw_ocr_text)
            sd_m = _SPANISH_DATE_RE.search(raw_ocr_text)
            fp_date = (_parse_spanish_date(sd_m) if sd_m
                       else (_parse_date(d_m.group(1)) if d_m else date.today()))
            fp_merchant = next(
                (ln.strip() for ln in raw_ocr_text.splitlines()
                 if ln.strip() and not _is_junk_merchant(ln.strip())
                 and not _MONEY_TOKEN.fullmatch(ln.strip()) and len(ln.strip()) > 2),
                "",
            )
            return ParseResult(transactions=[ParsedReceipt(
                amount=float(round(tess_neto + tess_iva)), is_income=False, date=fp_date,
                merchant=fp_merchant, description="", category="Supermercado",
                currency="CLP", items=items_fp,
            )])

        # ── VISION-FIRST: send image directly to the model (one pass) ──────────
        # The two-stage pipeline (transcribe→parse-text) used to be the default
        # but it actively HURTS accuracy: column alignment / right-side prices /
        # spatial layout are all lost in the intermediate text transcription,
        # which is why "ChatGPT con la misma imagen" used to read items Lucas
        # could not. Single-pass vision_json with the receipt prompt is what
        # ChatGPT itself does behind the scenes, so we match that.
        data_url, upright_w, upright_h, send_bytes = _prep_receipt_image(image_bytes)
        mime = "image/jpeg"

        def _call_vision(user_msg: str, *, model: str | None = None) -> str:
            """Send the image + a user message to the vision model and return raw text."""
            try:
                _resp = ai_provider.vision_json(
                    system_prompt=_RECEIPT_PROMPT,
                    user_text=user_msg,
                    image_data_url=data_url,
                    model=model,
                    temperature=0.0,
                    purpose="parse",
                    user_id=user_id,
                    db=db,
                )
                if _resp and _resp.text:
                    return _resp.text
            except Exception as _exc:  # noqa: BLE001
                print(f"[ocr] vision_json failed: {_exc}")
            return ""

        raw_json_text = _call_vision("Parsea esta boleta/recibo y devuelve SOLO el JSON.")

        # Fallback: two-stage transcribe→parse-text only if direct vision failed
        if not raw_json_text:
            print("[ocr] vision_json empty, falling back to two-stage transcribe→parse")
            try:
                import struct as _struct
                _img_long = 0
                if mime == "image/jpeg":
                    i = 2
                    while i < len(send_bytes) - 8:
                        if send_bytes[i] == 0xFF and send_bytes[i+1] in (0xC0, 0xC2):
                            _h, _w = _struct.unpack_from(">HH", send_bytes, i+5)
                            _img_long = max(_h, _w)
                            break
                        seg_len = _struct.unpack_from(">H", send_bytes, i+2)[0]
                        i += 2 + seg_len
                elif mime == "image/png":
                    _w, _h = _struct.unpack_from(">II", send_bytes, 16)
                    _img_long = max(_w, _h)
            except Exception:
                _img_long = 0

            t1 = ai_provider.vision_transcribe(
                data_url,
                purpose="transcribe",
                user_id=user_id,
                db=db,
                image_long_side=_img_long,
            )
            if t1 and t1.text:
                _needs_full = (
                    "Total General Mesa" in t1.text
                    or t1.text.upper().count("DCTO") + t1.text.upper().count("PROMO") > 1
                    or t1.text.count("\n") > 25
                )
                _stage2_model = settings.openai_vision_model if _needs_full else settings.openai_model
                t2 = ai_provider.chat_completion(
                    messages=[
                        {"role": "system", "content": _RECEIPT_TEXT_PROMPT},
                        {"role": "user", "content": f"Texto del recibo:\n{t1.text}"},
                    ],
                    model=_stage2_model,
                    temperature=0.0,
                    purpose="parse_text",
                    user_id=user_id,
                    db=db,
                )
                if t2 and t2.text:
                    _raw = t2.text.strip()
                    _m = re.search(r'```json\s*([\s\S]+?)\s*```', _raw)
                    if _m:
                        raw_json_text = _m.group(1)
                    elif _raw.startswith("{"):
                        raw_json_text = _raw
                    else:
                        _s, _e = _raw.find("{"), _raw.rfind("}") + 1
                        raw_json_text = _raw[_s:_e] if _s != -1 and _e > _s else ""

        if not raw_json_text:
            return None

        def _parse_items_from_tx(tx: dict) -> list[ParsedItem]:
            """Convert JSON items to ParsedItem. Primary source: line_total ÷ qty.
            Falls back to price field for backwards-compat with old schema.
            """
            out_items: list[ParsedItem] = []

            for it in tx.get("items", []) or []:
                name = it.get("name")
                if not name:
                    continue
                try:
                    qty = int(it.get("quantity") or 1)
                except (TypeError, ValueError):
                    qty = 1
                if qty < 1:
                    qty = 1
                try:
                    line_total = float(it.get("line_total") or 0)
                except (TypeError, ValueError):
                    line_total = 0.0
                try:
                    price_raw = float(it.get("price") or 0)
                except (TypeError, ValueError):
                    price_raw = 0.0

                if line_total > 0:
                    price = round(line_total / qty)
                elif price_raw > 0:
                    price = price_raw
                else:
                    price = 0.0

                def _pct(key: str) -> Optional[float]:
                    try:
                        v = it.get(key)
                        return max(0.0, min(100.0, float(v))) if v is not None else None
                    except (TypeError, ValueError):
                        return None

                by0, by1 = _pct("bbox_y0"), _pct("bbox_y1")
                if by0 is None or by1 is None or by1 <= by0:
                    by0 = by1 = None
                # El resaltado en la app va a todo el ancho de la foto (ver
                # frontend), así que ya no le pedimos al modelo el ancho del
                # bloque de ítems (bbox_x0/x1) — solo el alto por ítem.
                position_y = round((by0 + by1) / 2, 1) if by0 is not None else None

                out_items.append(ParsedItem(
                    name=str(name), price=price, quantity=qty, position_y=position_y,
                    bbox_y0=by0, bbox_y1=by1,
                ))
            return out_items

        def _parse_payload(raw_text: str):
            """Parse the model's JSON envelope. Returns (data, txs) or (None, [])."""
            try:
                _data = json.loads(raw_text)
            except Exception:
                return None, []
            return _data, _data.get("transactions", []) or []

        data, txs = _parse_payload(raw_json_text)
        if data is None:
            return None

        # One retry on mismatch: if items exist but their sum is off from the
        # printed total, escalate to the careful/slow model (openai_vision_model
        # is the fast default; a single missing/misread line on a long receipt
        # — e.g. a 21-item bar tab with several repeated "Promo X" rows — can be
        # a small % of the total, easy to miss with a tight threshold, and the
        # fast model re-asking ITSELF rarely catches its own mistake, so the
        # retry uses a different, more careful model rather than repeating the
        # same question to the same model. Threshold lowered 10%→6% so a single
        # missed item on a long, multi-item receipt is more likely to trigger it.
        if txs:
            _first = txs[0]
            _items_preview = _parse_items_from_tx(_first)
            _amount_preview = abs(float(_first.get("amount") or 0))
            if _items_preview and _amount_preview > 0:
                _items_sum_preview = sum(i.price * i.quantity for i in _items_preview)
                if _items_sum_preview > 0:
                    _diff = abs(_items_sum_preview - _amount_preview)
                    if _diff / _amount_preview > 0.06:
                        print(
                            f"[ocr] retry (escalating to {settings.openai_vision_model_fallback}): "
                            f"items sum {int(_items_sum_preview)} vs total {int(_amount_preview)} "
                            f"(diff={int(_diff)})"
                        )
                        _retry_msg = (
                            f"Tu extracción anterior no cuadra: items suman {int(_items_sum_preview)} "
                            f"pero el total impreso es {int(_amount_preview)}. "
                            f"Diferencia: {int(_diff)}. Vuelve a leer la imagen con cuidado — probablemente "
                            "falta un ítem completo (revisa líneas repetidas o promociones que se parecen "
                            "entre sí) o una cantidad/precio está mal. Devuelve el mismo JSON corregido, "
                            "con TODOS los ítems de la boleta."
                        )
                        _retry_text = _call_vision(
                            _retry_msg, model=settings.openai_vision_model_fallback
                        )
                        if _retry_text:
                            _retry_data, _retry_txs = _parse_payload(_retry_text)
                            if _retry_data is not None and _retry_txs:
                                data, txs = _retry_data, _retry_txs

        currency = data.get("currency") or "CLP"
        bank_hint = data.get("bank_hint") or ""
        account_type_hint = data.get("account_type_hint") or ""

        # total_neto and iva_amount live at the top level of the schema, not per-transaction
        top_neto = float(data.get("total_neto") or 0)
        top_iva = float(data.get("iva_amount") or 0)

        out: list[ParsedReceipt] = []
        for t in txs:
            raw_amount = abs(float(t.get("amount") or 0))
            items = _parse_items_from_tx(t)

            # Accept neto/iva from per-transaction field (some models put it there) or top level
            llm_neto = float(t.get("total_neto") or 0) or top_neto
            llm_iva = float(t.get("iva_amount") or 0) or top_iva
            if llm_neto > 0 and llm_iva > 0:
                # Chilean BOLETA: the printed per-line prices already INCLUDE IVA and
                # sum to the charged total. "TOTAL NETO" / "IVA" at the bottom are
                # just the mandatory tax breakdown — they must NOT be used to rescale
                # the line prices. Rescaling correct prices to TOTAL NETO is what
                # produced the "$2.150 → $1.765" bug when splitting a bill.
                _neto_iva = float(round(llm_neto + llm_iva))
                raw_amount = _neto_iva
                _isum = sum(it.price * it.quantity for it in items)
                if items and _isum > 0:
                    _off_final = abs(_isum - _neto_iva) / _neto_iva
                    _off_neto = abs(_isum - llm_neto) / max(llm_neto, 1)
                    if _off_final <= 0.15:
                        # items are IVA-inclusive and close enough → trust them as
                        # printed (a small gap is usually a fumbled discount line,
                        # not systematically wrong prices). No IVA row.
                        pass
                    elif _off_neto <= 0.06:
                        # items are NETO prices → keep them, add the printed IVA row
                        items.append(ParsedItem(name="IVA (19%)", price=round(llm_iva), quantity=1))
                    else:
                        # genuinely garbage (e.g. barcodes read as prices) → drop the
                        # line items rather than show corrupted amounts for a split
                        print(f"[ocr] boleta items unusable (sum={int(_isum)} vs "
                              f"neto+iva={int(_neto_iva)}) — dropping items")
                        items = []
            else:
                # Simple validation: if items exist and sum is wildly off, drop them.
                # Don't try to algebraically "fix" — that path caused regressions.
                items_sum = sum(it.price * it.quantity for it in items)
                if items and raw_amount > 0:
                    ratio = items_sum / raw_amount
                    if ratio < 0.5 or ratio > 2.0:
                        print(
                            f"[ocr] reconcile: items_sum={int(items_sum)} vs total={int(raw_amount)} "
                            f"(ratio={ratio:.2f}) — dropping items"
                        )
                        items = []
                    # else: trust the model, minor discrepancies are normal

            ca = t.get("cuota_actual")
            ct = t.get("cuotas_total")
            try:
                ca = int(ca) if ca is not None else None
                ct = int(ct) if ct is not None else None
            except (TypeError, ValueError):
                ca, ct = None, None

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
            image_width=upright_w,
            image_height=upright_h,
        )
    except Exception as e:  # noqa: BLE001
        print(f"[ocr] vision_parse failed: {e}")
        import traceback; traceback.print_exc()
        return None


# ---------- Bill-split OCR: lectura libre + reformateo barato ----------
# `vision_parse` (arriba) usa un prompt JSON con MUCHOS campos (bbox por ítem,
# IVA/neto, categoría, cuotas, bank_hint — necesarios para /upload, que también
# lee cartolas bancarias multi-transacción). Para el split de cuentas (siempre
# UNA boleta con ítems) el pipeline pasó por 3 etapas hoy (2026-09-11):
#   1. JSON completo → el modelo se saltaba ítems en boletas con líneas
#      repetidas (boleta real "Bar Autóctono", 17 ítems, 7 líneas casi
#      idénticas) y era más lento.
#   2. Texto plano pero con un formato fijo "cantidad | nombre | total" →
#      mejor, pero SIGUE siendo una restricción de formato: en otra boleta
#      real ("Bar La Providencia", columna de precios impresa desalineada de
#      los nombres) el modelo confundía qué precio iba con qué ítem. Se
#      probó pedirle explícitamente que emparejara por orden, que verificara
#      su propia suma — nada lo arregló.
#   3. **Lectura totalmente libre** (sin pedirle NINGÚN formato — el modelo
#      responde como quiera: tabla markdown, lista con viñetas, numerada,
#      lo que le salga) — verificado 3/3 exacto en la misma boleta donde
#      el formato fijo fallaba. Pedirle CUALQUIER estructura, por mínima
#      que parezca, le cuesta precisión en boletas visualmente difíciles.
# El costo de la lectura libre: la respuesta no es parseable directo (cada
# llamada elige un formato distinto). Se resuelve con un SEGUNDO paso barato
# — sin ver la foto, solo texto — que reordena la respuesta ya correcta a
# nuestro formato fijo (`_REFORMAT_PROMPT_BILL`, `gpt-4.1-nano`, ~2-3s,
# costo mínimo porque no es una llamada de visión). Este segundo paso nunca
# lee la boleta ni decide valores — solo reformatea texto que ya está bien,
# así que no puede reintroducir el problema de la etapa 2.
#
# NO agregar restricciones de formato al prompt de LECTURA (`_RECEIPT_PROMPT_BILL`)
# sin volver a probar contra una boleta difícil real primero — ver
# docs/PLAN_split_v3.md para el historial completo de intentos.
#
# CATEGORY y CURRENCY no se piden: no se usan en ningún lado del flujo de
# bill-split (bill.total_amount sale de sumar BillItem.line_total, no de
# esto; Bill no tiene columna category). AMOUNT sí se mantiene — es la única
# señal que se usa para decidir si hay que reintentar (ver más abajo).
#
# Por eso el split usa su propio prompt/parser/función — no comparte
# `_RECEIPT_PROMPT`/`vision_parse` con /upload, así no se toca su manejo de
# cartolas/cuotas/bank_hint (que sigue igual, sin cambios en este commit).
_RECEIPT_PROMPT_BILL = (
    "Dame en texto esta boleta, con el nombre del local, la fecha, los "
    "items en ese orden con su cantidad y valor, y el total del consumo "
    "(sin la propina sugerida si la hay)."
)

_REFORMAT_PROMPT_BILL = """Convierte el texto que te paso (ya correcto, no cambies ningún valor) a líneas con este formato exacto, una por PRODUCTO, sin encabezado ni texto extra:
cantidad|nombre|total_de_esa_línea_sin_signos_de_pesos_ni_puntos

NO incluyas como si fuera un producto la línea de "Total"/"Consumo"/subtotal
— esa NO es un ítem, va aparte en AMOUNT más abajo. Si una línea no trae
cantidad explícita (p.ej. un descuento), usa 1.

Al final agrega estas 3 líneas con los datos reales que encuentres en el texto:
MERCHANT: nombre del local que aparece en el texto (o vacío si no aparece)
DATE: fecha que aparece en el texto, o null si no aparece
AMOUNT: el consumo/total (número, sin la propina sugerida)"""


def _parse_bill_text(raw_text: str) -> Optional[dict]:
    """Parsea la respuesta de texto plano de `_RECEIPT_PROMPT_BILL` a un dict
    {merchant, date, amount, items: [{name, quantity, line_total}]}.

    El prompt es deliberadamente mínimo (ver comentario arriba) y no le fuerza
    un formato rígido al modelo más allá de "cantidad | nombre | total" por
    ítem — en la práctica el header (MERCHANT/DATE/AMOUNT) a veces sale en
    líneas separadas y a veces en una sola línea separado por comas, así que
    esos 3 campos se buscan con una regex sobre el texto completo (no por
    posición de línea) en vez de asumir una estructura exacta. Cualquier
    línea con 2+ "|" se trata como ítem. Devuelve None si no se pudo extraer
    nada útil (ni AMOUNT ni ningún ítem)."""
    import re as _re

    out: dict = {"merchant": "", "date": None, "amount": 0.0, "items": []}

    # El nombre del local puede traer comas de verdad (una dirección), así
    # que el límite de captura no es la coma — es la siguiente etiqueta
    # conocida (DATE:/AMOUNT:) o el final del texto. Si el modelo llegó a
    # juntar MERCHANT y DATE en una sola línea sin nombre real, esto corta
    # justo ahí en vez de comerse "DATE: ..." como si fuera el nombre.
    m = _re.search(r"MERCHANT:\s*([^\n]*?)(?=\s*(?:DATE:|AMOUNT:|$))", raw_text, _re.IGNORECASE)
    if m:
        out["merchant"] = m.group(1).strip()[:200]
    m = _re.search(r"DATE:\s*([^,\n]+)", raw_text, _re.IGNORECASE)
    if m:
        v = m.group(1).strip()
        out["date"] = None if v.lower() in ("null", "none", "") else v
    m = _re.search(r"AMOUNT:\s*([\d.,]+)", raw_text, _re.IGNORECASE)
    if m:
        out["amount"] = _to_float(m.group(1))

    for line in raw_text.splitlines():
        s = line.strip()
        parts = s.split("|")
        if len(parts) < 3:
            continue
        qty_raw = parts[0].strip()
        try:
            # Ojo: la boleta suele imprimir la cantidad como "1.00"/"2.00"
            # (con decimales) — hay que parsearla como número real, NO sacar
            # solo los dígitos con regex (eso convierte "1.00" en "100": el
            # punto desaparece y el "00" se pega al "1"). Bug real visto en
            # prod: "1.00 Frutilla Spritz" terminó guardado como cantidad=100.
            # Se reusa _to_float (ya sabe distinguir miles de decimales, y ya
            # interpreta un "-" como negativo) en vez de un parseo nuevo.
            qty = int(round(_to_float(qty_raw))) or 1
        except Exception:
            qty = 1
        # Cantidad siempre positiva y acotada — mismo límite (999) que ya usan
        # los ítems agregados a mano (ItemAdd/ItemPatch). Esto también cubre,
        # de forma general, cualquier línea donde el modelo puso un valor
        # negativo en la columna de cantidad (p.ej. una línea de descuento
        # "-990 | Descuento | -990"): _to_float ya lo lee como -990, y este
        # límite lo lleva a 1 igual que a cualquier otra cantidad inválida —
        # sin necesitar un caso especial para "empieza con -".
        qty = max(1, min(qty, 999))
        name = parts[1].strip()
        if not name:
            continue
        line_total = _to_float(parts[2])
        out["items"].append({"name": name[:200], "quantity": qty, "line_total": line_total})

    if out["amount"] <= 0 and not out["items"]:
        return None
    return out


def vision_parse_bill(
    image_bytes: bytes, *, db=None, user_id=None,
) -> Optional[ParseResult]:
    """Igual que `vision_parse` pero solo para el split de cuentas (una boleta,
    nunca una cartola con varias transacciones). Dos pasos (ver comentario
    completo junto a `_RECEIPT_PROMPT_BILL` arriba):
      1. Lectura LIBRE de la foto — sin pedirle ningún formato — es lo que
         midió mejor precisión en boletas visualmente difíciles.
      2. Reformateo barato (texto, sin ver la foto, `gpt-4.1-nano`) de esa
         respuesta ya correcta a nuestro formato fijo parseable. Nunca lee la
         boleta ni decide valores, solo reordena texto — no puede reintroducir
         el problema de precisión que tenía pedir un formato en el paso 1.

    Mismo reintento-por-descuadre que `vision_parse`: si la suma de ítems no
    cuadra con AMOUNT (umbral 6%), reintenta el par lectura+reformateo con
    `openai_vision_model_fallback` antes de rendirse — un ítem faltante en una
    división de cuentas real es plata mal repartida entre amigos, así que esto
    se mantiene sin importar cuán confiable midió el modelo rápido en pruebas.

    No pide bbox por ítem (position_y/bbox_* quedan None) — el frontend ya
    tiene un fallback de posición pareja por índice (`defaultBandPct`) más
    arrastre manual para corregir, y pedir bbox fue justamente una de las
    cosas que hacía más lento y menos preciso al modelo rápido en las pruebas.
    """
    if not ai_provider.is_available():
        return None
    try:
        data_url, upright_w, upright_h, _ = _prep_receipt_image(image_bytes)

        def _read(model: str) -> str:
            """Paso 1: lectura libre de la foto, sin pedirle formato."""
            try:
                resp = ai_provider.vision_text(
                    system_prompt=_RECEIPT_PROMPT_BILL,
                    user_text="Lee la boleta.",
                    image_data_url=data_url,
                    model=model,
                    temperature=0.0,
                    purpose="parse_bill",
                    user_id=user_id,
                    db=db,
                )
                return resp.text if resp and resp.text else ""
            except Exception as _exc:  # noqa: BLE001
                print(f"[ocr] vision_text (bill) failed: {_exc}")
                return ""

        def _reformat(free_text: str) -> str:
            """Paso 2: reordena la lectura libre (ya correcta) a nuestro
            formato fijo — texto plano, no ve la imagen, barato y rápido."""
            try:
                resp = ai_provider.chat_completion(
                    messages=[
                        {"role": "system", "content": _REFORMAT_PROMPT_BILL},
                        {"role": "user", "content": free_text},
                    ],
                    model="gpt-4.1-mini",  # nano confundía la línea de "Total" con un ítem más (visto en pruebas 2026-09-11) — mini es igual de barato/rápido para texto puro (no ve la foto) y no tuvo ese problema
                    temperature=0.0,
                    purpose="parse_bill_reformat",
                    user_id=user_id,
                    db=db,
                )
                return resp.text if resp and resp.text else ""
            except Exception as _exc:  # noqa: BLE001
                print(f"[ocr] chat_completion (reformat bill) failed: {_exc}")
                return ""

        def _read_and_structure(model: str) -> Optional[dict]:
            free_text = _read(model)
            if not free_text:
                return None
            structured = _reformat(free_text)
            return _parse_bill_text(structured) if structured else None

        parsed = _read_and_structure(settings.openai_vision_model_bill)
        if parsed is None:
            print("[ocr] vision_parse_bill: respuesta vacía/no parseable")
            return None

        # line_total ya es el total de ESA línea (el prompt pide "valor total
        # de esa línea", no precio unitario) — NO multiplicar por quantity de
        # nuevo, ya la incluye. (Bug real encontrado en pruebas 2026-09-11:
        # duplicaba la cantidad acá, inflaba la suma y disparaba
        # reescalamientos innecesarios en boletas que en realidad leía bien.)
        items_sum = sum(it["line_total"] for it in parsed["items"])
        amount = float(parsed["amount"] or 0)
        if parsed["items"] and amount > 0 and items_sum > 0:
            diff = abs(items_sum - amount)
            if diff / amount > 0.06:
                print(f"[ocr] retry bill (escalando a {settings.openai_vision_model_fallback}): "
                      f"items sum {int(items_sum)} vs amount {int(amount)} (diff={int(diff)})")
                parsed2 = _read_and_structure(settings.openai_vision_model_fallback)
                if parsed2 is not None:
                    parsed = parsed2

        items = [
            ParsedItem(
                name=it["name"],
                price=round(it["line_total"] / it["quantity"]) if it["quantity"] else it["line_total"],
                quantity=it["quantity"],
                line_total=it["line_total"],  # total real leído — bills.py lo usa tal cual, no qty*price
            )
            for it in parsed["items"]
        ]
        try:
            parsed_date = _parse_date(parsed["date"]) if parsed["date"] else date.today()
        except Exception:
            parsed_date = date.today()

        receipt = ParsedReceipt(
            amount=float(parsed["amount"] or sum(i.price * i.quantity for i in items)),
            date=parsed_date,
            merchant=parsed["merchant"] or "",
            category="Otros",  # no se usa en el flujo de bill-split (ver comentario del prompt)
            currency="CLP",    # ídem — bill.currency se fija al crear la boleta, no desde OCR
            is_income=False,
            items=items,
        )
        return ParseResult(transactions=[receipt], image_width=upright_w, image_height=upright_h)
    except Exception as e:  # noqa: BLE001
        print(f"[ocr] vision_parse_bill failed: {e}")
        import traceback; traceback.print_exc()
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
