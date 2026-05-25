"""
Chilean bank notification email parser.

Banks like Banco de Chile, Santander, BCI, Scotiabank, Itaú, Falabella (CMR),
Ripley, MACH, and Copec send push-notification emails for every card charge.
We parse the plain-text / HTML body to extract: amount, merchant, currency,
date, account hint, and whether it's a charge (expense) or credit.

The parser tries regex first (fast, zero cost); falls back to the configured
LLM only when the heuristics can't extract a confident result.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Optional

from ..ai.provider import chat_completion

logger = logging.getLogger(__name__)

# ── Quick-exit: obviously not a transaction email ─────────────────────────────
_SKIP_SUBJECTS = re.compile(
    r"(bienvenid|welcome|verif|confirma|contraseña|password|newsletter"
    r"|suscripci|unsubscribe|publicidad|promocion|oferta|descuento"
    r"|estado de cuenta|cartola|resumen mensual)",
    re.I,
)

# ── Amount patterns for Chilean banks ─────────────────────────────────────────
# Matches: $1.234.567, $1.234, 1234, 1.234, CLP 1.234
_AMOUNT_RE = re.compile(
    r"(?:CLP\s*|clp\s*|\$\s?)"
    r"([\d]{1,3}(?:[.,]\d{3})*)"
    r"(?:[.,](\d{2}))?"
    r"(?!\d)",
    re.I,
)

# ── Date patterns ─────────────────────────────────────────────────────────────
_DATE_RE = re.compile(
    r"(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{2,4})"
    r"|(\d{4})[/\-\.](\d{1,2})[/\-\.](\d{1,2})",
)

# ── Merchant patterns ─────────────────────────────────────────────────────────
_MERCHANT_RE = re.compile(
    r"(?:comercio|merchant|establecimiento|nombre|local|tienda)\s*[:\-]\s*"
    r"([A-ZÁÉÍÓÚÑÜ][^\n\r,|]{2,60})",
    re.I,
)

# ── Card hint ─────────────────────────────────────────────────────────────────
_CARD_RE = re.compile(
    r"(?:tarjeta|card|cuenta)\s+(?:terminada en|ending|nro\.?|n°)?\s*[*xX]{0,4}(\d{4})",
    re.I,
)

# ── Income vs expense signals ─────────────────────────────────────────────────
_INCOME_RE = re.compile(
    r"(abono|dep[oó]sito|transferencia\s+recibida|cr[eé]dito\s+en\s+cuenta"
    r"|pago\s+recibido|ingreso|acreditad)",
    re.I,
)
_EXPENSE_RE = re.compile(
    r"(compra|cargo|cobro|d[eé]bito|consumo|pago\s+realizado|transacci[oó]n"
    r"|purchase|charge|utilizaci[oó]n)",
    re.I,
)

# ── Credit-card payment signals ────────────────────────────────────────────────
# These match emails describing a payment FROM a debit account TO a credit card.
_CC_PAYMENT_RE_EMAIL = re.compile(
    r"(?i)\b(pago\s*(?:de\s*)?(?:su\s*|tu\s*)?tarjeta\s*(?:de\s*cr[eé]dito)?"
    r"|pago\s*tc|abono\s*a?\s*tarjeta|transferencia\s*(?:a|para|hacia)\s*tarjeta"
    r"|pago\s*cmr|pago\s*falabella|pago\s*ripley|pago\s*l[ií]der)\b",
)
_CC_NAME_RE = re.compile(
    r"(?i)\b(cmr|falabella|ripley|paris|l[ií]der|santander|bci|"
    r"banco\s*de\s*chile|bancoestado|it[aá]u|scotiabank|mach|security|"
    r"mercado\s*pago)\b",
)


def _parse_amount_str(raw: str) -> float:
    """'1.234.567' or '1,234,567' → 1234567.0"""
    clean = raw.replace(".", "").replace(",", "")
    return float(clean)


def _extract_heuristic(subject: str, body: str) -> Optional[dict]:
    """Fast regex extraction. Returns dict or None."""
    text = f"{subject}\n{body}"

    # Amount
    amounts = []
    for m in _AMOUNT_RE.finditer(text):
        try:
            val = _parse_amount_str(m.group(1))
            if 100 <= val <= 100_000_000:
                amounts.append(val)
        except ValueError:
            pass

    if not amounts:
        return None

    amount = max(amounts)

    # Date
    tx_date = date.today()
    dm = _DATE_RE.search(text)
    if dm:
        try:
            if dm.group(1):
                d, mo, y = int(dm.group(1)), int(dm.group(2)), int(dm.group(3))
                if y < 100:
                    y += 2000
            else:
                y, mo, d = int(dm.group(4)), int(dm.group(5)), int(dm.group(6))
            tx_date = date(y, mo, d)
        except (ValueError, TypeError):
            pass

    # Merchant
    merchant = ""
    mm = _MERCHANT_RE.search(text)
    if mm:
        merchant = mm.group(1).strip().title()

    # Income vs expense
    is_income = bool(_INCOME_RE.search(text)) and not bool(_EXPENSE_RE.search(text))

    # Card hint
    card_hint = ""
    cm = _CARD_RE.search(text)
    if cm:
        card_hint = cm.group(1)

    # CC payment detection
    is_cc_payment = bool(_CC_PAYMENT_RE_EMAIL.search(text))
    cc_name = ""
    if is_cc_payment:
        nm = _CC_NAME_RE.search(text)
        if nm:
            cc_name = nm.group(0).strip().title()

    return {
        "amount": amount,
        "date": tx_date.isoformat(),
        "merchant": merchant,
        "is_income": is_income,
        "currency": "CLP",
        "card_last4": card_hint,
        "category": "Transferencia" if is_cc_payment else "Otros",
        "is_cc_payment": is_cc_payment,
        "cc_name": cc_name,
    }


_SYSTEM_PROMPT = """\
You are a financial data extractor for Chilean bank notification emails.
Extract a single JSON object (no markdown fences) with exactly these fields:

{
  "amount": <number — always positive, in CLP>,
  "date": <"YYYY-MM-DD" — use today if not found>,
  "merchant": <string — store/company name, empty string if unknown>,
  "is_income": <boolean — true only for abonos/depósitos/transfers received>,
  "currency": "CLP",
  "card_last4": <string — last 4 digits of card number, empty if unknown>,
  "category": <one of: Alimentación, Supermercado, Transporte, Entretenimiento, \
Bares y Salidas, Suscripciones, Cuentas y Servicios, Salud, Compras, Viajes, Transferencia, Otros>,
  "is_cc_payment": <boolean — true if this is a payment FROM a debit/savings account TO a credit card>,
  "cc_name": <string — name of the credit card being paid (e.g. "CMR", "Falabella", "Ripley"), empty if unknown or not a CC payment>
}

If this email is NOT a transaction notification (marketing, welcome, verification, etc.),
return {"skip": true}.

Respond with JSON only, no explanation.
"""


def parse_email(
    db,
    user_id: int,
    subject: str,
    body_text: str,
    body_html: str = "",
) -> Optional[dict]:
    """
    Parse a bank notification email into a transaction dict.
    Returns None if the email is not a transaction notification.

    Result keys: amount, date, merchant, is_income, currency, card_last4, category
    """
    # Quick-exit for non-transaction emails
    if _SKIP_SUBJECTS.search(subject):
        logger.debug("Skipping non-transaction email: %s", subject[:80])
        return None

    # Prefer plain text; strip HTML tags otherwise
    body = body_text or re.sub(r"<[^>]+>", " ", body_html)
    body = re.sub(r"\s+", " ", body).strip()

    if len(body) < 20:
        return None

    # Try heuristic first (free, instant)
    result = _extract_heuristic(subject, body)
    if result and result.get("merchant"):
        return result

    # Fall back to LLM
    user_msg = f"Subject: {subject}\n\n{body[:2000]}"
    resp = chat_completion(
        [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        model="gpt-4o-mini",
        temperature=0,
        max_tokens=256,
        purpose="email_parse",
        user_id=user_id,
        db=db,
    )

    if resp is None:
        # No LLM configured — return heuristic result even without merchant
        return result

    try:
        data = json.loads(resp.text)
        if data.get("skip"):
            return None
        if not data.get("date"):
            data["date"] = date.today().isoformat()
        return data
    except (json.JSONDecodeError, AttributeError) as e:
        logger.warning("Email parse JSON error: %s | text: %s", e, getattr(resp, "text", "")[:200])
        return result
