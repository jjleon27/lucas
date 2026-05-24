"""
Voice-transcript → structured transaction.

The frontend uses the browser's Web Speech API to transcribe the user's
speech locally (free, works offline for many locales including es-CL).
It then sends the transcript here, where the LLM extracts:

  {
    "action": "add_expense" | "add_income" | "unclear",
    "amount": float,
    "currency": "CLP" | "USD" | ...,
    "merchant": str,
    "category": str,
    "is_income": bool,
    "date": "YYYY-MM-DD",
    "account_hint": str,       # "débito", "efectivo", "CMR", "Santander"
    "confidence": 0..1,
    "clarification": str       # populated when action == "unclear"
  }

The router then runs account_hint through dedupe.suggest_account_for_hint
to find the matching account id from the user's real accounts.
"""
from __future__ import annotations

import json
import re
from datetime import date
from typing import Optional

from ..ai import provider as ai_provider


_SYSTEM_PROMPT = """You turn a single spoken sentence (usually Spanish, Chilean accent)
into a structured personal-finance transaction.

Return strict JSON with this shape:
{
  "action": "add_expense" | "add_income" | "unclear",
  "amount": number,
  "currency": "CLP" | "USD" | "BRL" | "MXN" | "ARS",
  "merchant": string,
  "category": "Food & Dining" | "Transport" | "Shopping" | "Bills" | "Entertainment" | "Health" | "Travel" | "Housing" | "Salary" | "Freelance" | "Gift" | "Other",
  "is_income": boolean,
  "date": "YYYY-MM-DD",
  "account_hint": string,
  "confidence": number,
  "clarification": string
}

RULES:
1. AMOUNTS in Chilean slang:
   - "luca" / "lucas" = 1000 CLP each. "5 lucas" = 5000, "cien lucas" = 100000.
   - "palo" = 1,000,000 CLP. "un palo" = 1000000.
   - "gamba" / "quina" = 100 CLP.
   - "diez mil", "veinte mil" → plain numbers.
   - If the user says "pesos" assume CLP (unless they said dólares / reales / etc).
2. DIRECTION:
   - "gasté", "pagué", "compré", "me sacaron" → action=add_expense, is_income=false.
   - "me pagaron", "recibí", "me ingresó", "me transfirieron" → action=add_income, is_income=true.
3. ACCOUNT HINT:
   - Listen for "con el débito", "con la CMR", "con Santander", "en efectivo", "con la credit", "con la Falabella", "en la cuenta vista"…
   - Put the short hint (1-3 words) in account_hint. Leave empty if unclear.
4. DATE:
   - Default to today (passed in the user message as "Hoy es YYYY-MM-DD").
   - "ayer" → yesterday. "anteayer" / "antes de ayer" → 2 days ago.
   - "el lunes pasado", "hace una semana", etc. → best-guess a real date.
5. CATEGORY:
   - Food & Dining: supermarkets, restaurants, delivery, food.
   - Transport: Uber, Cabify, Metro, gasolina, bencina, estacionamiento.
   - Bills: telefonía (Movistar, Entel, WOM, Claro), luz, agua, gas, internet, Netflix, Spotify.
   - Shopping: ropa, tecnología, amazon, Falabella, MercadoLibre.
   - Health: farmacia (Cruz Verde, Ahumada, Salcobrand), médico, clínica.
   - Entertainment: cine, bar, conciertos, videojuegos.
   - Housing: arriendo, expensas, condominio.
   - Salary: sueldo, pago mensual del trabajo.
   - Freelance: "me pagaron por el proyecto", consultoría, pega puntual.
   - If genuinely unclear, use "Other".
6. UNCLEAR:
   - If the sentence is gibberish, a question, or missing an amount AND you can't
     guess it, set action="unclear" and put a one-sentence follow-up question
     (in the user's language) in "clarification", e.g. "¿Cuánto gastaste?".
7. CONFIDENCE: 0..1. 1.0 = crystal clear. <0.6 → frontend will ask for confirmation.

Return ONLY the JSON object, no prose, no markdown.
"""


# Fast heuristic so we don't waste LLM calls on obviously empty input.
_MONEY_HINT_RE = re.compile(r"(\d|luca|palo|peso|dólar|gamba|quina|mil|millón|millon)", re.I)


def _fallback_unclear(transcript: str, today: date) -> dict:
    return {
        "action": "unclear",
        "amount": 0.0,
        "currency": "CLP",
        "merchant": "",
        "category": "Other",
        "is_income": False,
        "date": today.isoformat(),
        "account_hint": "",
        "confidence": 0.0,
        "clarification": "No entendí bien. ¿Puedes repetir cuánto y en qué gastaste?",
        "transcript": transcript,
    }


def parse_voice(
    transcript: str,
    today: Optional[date] = None,
    *,
    db=None,
    user_id: Optional[int] = None,
) -> dict:
    """
    Turn a spoken sentence into a structured transaction dict. Never raises —
    on failure returns action="unclear" with a clarification question.
    """
    transcript = (transcript or "").strip()
    today = today or date.today()
    if not transcript:
        return _fallback_unclear(transcript, today)
    if not _MONEY_HINT_RE.search(transcript) and len(transcript) < 5:
        return _fallback_unclear(transcript, today)
    if not ai_provider.is_available():
        return _fallback_unclear(transcript, today)

    user_msg = f"Hoy es {today.isoformat()}.\nFrase del usuario: {transcript}"

    resp = ai_provider.chat_completion(
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.0,
        purpose="voice",
        user_id=user_id,
        db=db,
    )
    if resp is None or not (resp.text or "").strip():
        return _fallback_unclear(transcript, today)

    raw = resp.text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        a, b = raw.find("{"), raw.rfind("}")
        if a == -1 or b <= a:
            return _fallback_unclear(transcript, today)
        try:
            data = json.loads(raw[a : b + 1])
        except json.JSONDecodeError:
            return _fallback_unclear(transcript, today)

    # Sanity-check fields.
    data.setdefault("action", "unclear")
    data.setdefault("amount", 0.0)
    data.setdefault("currency", "CLP")
    data.setdefault("merchant", "")
    data.setdefault("category", "Other")
    data.setdefault("is_income", data["action"] == "add_income")
    data.setdefault("date", today.isoformat())
    data.setdefault("account_hint", "")
    data.setdefault("confidence", 0.5)
    data.setdefault("clarification", "")
    data["transcript"] = transcript

    # Coerce types defensively.
    try:
        data["amount"] = float(data["amount"])
    except (TypeError, ValueError):
        data["amount"] = 0.0
    try:
        data["confidence"] = float(data["confidence"])
    except (TypeError, ValueError):
        data["confidence"] = 0.5

    # If the action claims expense but amount is 0 we can't use this row.
    if data["action"] != "unclear" and data["amount"] <= 0:
        data["action"] = "unclear"
        data["clarification"] = data.get("clarification") or "No te escuché el monto. ¿Cuánto fue?"

    return data
