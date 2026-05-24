"""
Chat interface. The user can type (or speak, via the frontend's Web Speech API)
questions like "how much did I spend on Uber this month?" LUCAS answers them by
calling a small set of read-only tools against the user's own data.

Architecture:
  1. Build a compact snapshot of the user's finances (last 90d).
  2. Send it to the LLM (via ai.provider → OpenAI / Anthropic / Gemini).
  3. If no provider is configured, fall back to a raw snapshot so the chat
     still "works" in local dev.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable

from sqlalchemy.orm import Session

from .. import models
from . import predictor, provider


def _build_context(db: Session, user: models.User) -> str:
    today = date.today()
    summary = predictor.summarize(db, user, today)

    since = today - timedelta(days=90)
    recent = (
        db.query(models.Transaction)
        .filter(
            models.Transaction.user_id == user.id,
            models.Transaction.date >= since,
        )
        .order_by(models.Transaction.date.desc())
        .limit(200)
        .all()
    )
    lines = [
        f"- {t.date.isoformat()} | {t.category} | {t.merchant or '—'} | "
        f"{'+' if t.is_income else '-'}${t.amount:,.2f}"
        for t in recent
    ]
    cats = ", ".join(f"{c['category']} ${c['total']:,.0f}" for c in summary["by_category"][:6])

    return (
        f"User monthly budget: ${summary['monthly_budget']:,.0f}\n"
        f"Month: {summary['month']} | Spent: ${summary['total_spent']:,.2f} | "
        f"Income: ${summary['total_income']:,.2f} | Projected EOM: "
        f"${summary['predicted_end_of_month']:,.2f} ({summary['status']})\n"
        f"Safe daily spend: ${summary['daily_safe_spend']:,.2f}\n"
        f"Category totals this month: {cats}\n\n"
        f"Recent transactions (last 90d, newest first):\n" + "\n".join(lines)
    )


def _fallback_answer(context: str) -> str:
    return (
        "💡 Lucas está funcionando sin una clave de IA. Aquí tienes un resumen de tus datos:\n\n"
        + context[:1200]
        + "\n\nAgrega una clave API (OPENAI_API_KEY, ANTHROPIC_API_KEY o GOOGLE_API_KEY) "
        "en backend/.env para activar el chat con IA."
    )


SYSTEM_PROMPT = """Eres Lucas, un asistente financiero personal amigable y directo.
Tienes acceso de solo lectura a las transacciones recientes del usuario (en el CONTEXT).

IDIOMA: SIEMPRE responde en español chileno. Nunca respondas en inglés, sin importar
en qué idioma te escriban. Si el usuario escribe en inglés, respóndele en español igual.

TONO: Cálido, directo, con sabor chileno — casual pero no vulgar. Usa "tú" (nunca "vos").
Frases naturales: "puedes", "tienes", "quieres", "al tiro", "a la rápida", "bacán".
Usa "lucas" para referirte a plata cuando encaje naturalmente.

RESPUESTAS:
- 1 a 3 párrafos cortos. Usa los números reales del usuario, nunca inventes datos.
- Moneda por defecto: CLP (pesos chilenos). Formatea los montos con punto de miles: $17.500.
- Si la respuesta requiere datos que no ves, dilo y sugiere subir la boleta correspondiente.
- No uses markdown pesado — máximo algo de **negrita** para énfasis.
"""


ACTION_SYSTEM = """Al final de tu respuesta, en una línea nueva, agrega exactamente:
ACTION:{"type":"<type>","data":{...}}

Donde <type> es uno de:
  "add_transaction" — el usuario quiere registrar un gasto o ingreso.
    data: {amount, currency, merchant, category, date (YYYY-MM-DD), is_income}
  "start_split" — el usuario quiere dividir una cuenta.
    data: {amount, currency, merchant, date (YYYY-MM-DD)}
  "navigate" — el usuario quiere ir a otra sección.
    data: {url}  ej: "/upload", "/accounts", "/transactions", "/split"
  "null" — ninguna acción específica detectada (preguntas, resúmenes, etc.)
    Usar: ACTION:{"type":"null"}

IMPORTANTE: Siempre incluye la línea ACTION, incluso para preguntas. Nunca la omitas.
Deduce montos del mensaje del usuario. Moneda por defecto: CLP.
Categorías válidas: Alimentación, Supermercado, Transporte, Compras, Entretenimiento,
Bares y Salidas, Cuentas y Servicios, Salud, Viajes, Suscripciones, Tecnología,
Educación, Hogar, Ropa, Ingresos, Transferencia, Inversión, Seguros, Otros.
"""


def answer_with_action(
    db: Session, user: models.User, question: str, history: Iterable[dict] = ()
) -> str:
    """Like answer() but appends an ACTION: line for intent detection."""
    context = _build_context(db, user)
    if not provider.is_available():
        return _fallback_answer(context) + "\nACTION:{\"type\":\"null\"}"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": ACTION_SYSTEM},
        {"role": "system", "content": f"CONTEXT:\n{context}"},
        *[{"role": m["role"], "content": m["content"]} for m in history],
        {"role": "user", "content": question},
    ]
    resp = provider.chat_completion(
        messages,
        temperature=0.3,
        purpose="chat",
        user_id=user.id,
        db=db,
    )
    if resp is None:
        return "Lo siento, no pude conectarme al servicio de IA. Intenta de nuevo.\nACTION:{\"type\":\"null\"}"
    return resp.text


def answer(db: Session, user: models.User, question: str, history: Iterable[dict] = ()) -> str:
    context = _build_context(db, user)
    if not provider.is_available():
        return _fallback_answer(context)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": f"CONTEXT:\n{context}"},
        *[{"role": m["role"], "content": m["content"]} for m in history],
        {"role": "user", "content": question},
    ]
    resp = provider.chat_completion(
        messages,
        temperature=0.3,
        purpose="chat",
        user_id=user.id,
        db=db,
    )
    if resp is None:
        return "Sorry, I hit a snag reaching the AI service. Try again in a moment."
    return resp.text
