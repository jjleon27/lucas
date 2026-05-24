"""
Smart categorization.

Strategy (cheap → expensive):
  1. User's own learned rules — every time the user corrects a merchant's
     category we remember it. Next time same merchant → zero LLM cost.
  2. Built-in keyword rules — handles 60-70% of generic LatAm + US merchants.
  3. LLM fallback — only when the first two layers fail.

This layered approach is what keeps costo_LLM / usuario_activo_mensual low.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from .. import models
from . import provider

DEFAULT_CATEGORIES = [
    "Alimentación", "Supermercado", "Transporte", "Compras",
    "Entretenimiento", "Bares y Salidas", "Cuentas y Servicios",
    "Salud", "Viajes", "Suscripciones", "Tecnología",
    "Educación", "Hogar", "Ropa", "Ingresos", "Transferencia",
    "Inversión", "Seguros", "Otros",
]

# Lowercased substring match. Grown to cover Chile / Brazil / Mexico / Argentina
# plus big global brands. Order inside each list doesn't matter.
_RULES: dict[str, list[str]] = {
    "Alimentación": [
        # Global
        "restaurant", "restaurante", "cafe", "café", "coffee", "burger", "pizza",
        "sushi", "mcdonald", "starbucks", "kfc", "subway", "dunkin", "chipotle",
        "domino", "tacos", "pollo",
        # Delivery
        "uber eats", "ubereats", "rappi", "pedidos ya", "pedidosya", "doordash",
        "grubhub", "ifood", "cornershop", "justo",
        # LatAm chains
        "doggis", "schop", "telepizza", "papa john", "juan valdez", "havanna",
        "bembos", "cinnabon", "benihana", "la cabrera", "guacamole",
    ],
    "Supermercado": [
        "supermarket", "supermercado", "grocery",
        # Chile
        "jumbo", "lider", "líder", "santa isabel", "unimarc", "tottus", "ekono",
        "acuenta", "a cuenta",
        # Brazil
        "pão de açúcar", "extra", "carrefour", "assai", "atacadão",
        # Mexico / Argentina / Peru
        "soriana", "chedraui", "la comer", "disco", "coto", "jumbo argentina",
        "wong", "plaza vea", "metro peru",
        # US / Global
        "walmart", "target", "whole foods", "trader joe", "costco", "safeway",
        "kroger", "aldi", "tesco",
    ],
    "Transporte": [
        # Rideshare
        "uber", "lyft", "cabify", "didi", "beat", "in drive", "indrive", "bolt",
        # Public transit
        "metro", "subway", "bus", "transantiago", "bip", "tarjeta bip",
        "metrobus", "sube", "bilhete único",
        # Taxis
        "taxi", "radiotaxi",
        # Fuel
        "gasolina", "combustible", "posto", "shell", "copec", "petrobras",
        "ypf", "pemex", "texaco", "esso", "chevron", "petrobrás", "enex",
        # Tolls / parking
        "autopista", "tag", "tev", "estacionamiento", "parking",
    ],
    "Compras": [
        "amazon", "mercadolibre", "mercado libre", "mercadolivre", "aliexpress",
        "shein", "shopee", "temu",
        # Department / fashion
        "falabella", "ripley", "paris", "hites", "la polar", "zara", "h&m",
        "nike", "adidas", "sears", "liverpool", "renner", "c&a", "riachuelo",
        "mango", "uniqlo", "decathlon",
        # Home / hardware
        "sodimac", "easy", "homecenter", "home depot", "ikea",
    ],
    "Entretenimiento": [
        "cinema", "cinemark", "hoyts", "cineplanet", "cinepolis", "movie",
        "cine", "concert", "concierto", "ticketmaster", "puntoticket",
        "steam", "playstation", "xbox", "nintendo", "gaming",
        "teatro", "museum", "museo", "parque", "festival",
    ],
    "Bares y Salidas": [
        "bar", "pub", "discoteca", "disco", "boliche", "club nocturno",
        "cerveceria", "cervecería", "cervezas", "pisco", "cocktail",
        "karaoke", "casino",
    ],
    "Suscripciones": [
        "netflix", "spotify", "disney", "disney+", "hbo", "max", "prime video",
        "youtube premium", "apple tv", "paramount", "crunchyroll", "deezer",
        "subscription", "suscripción", "suscripcion", "assinatura",
        "monthly plan", "chatgpt", "openai", "notion", "dropbox", "icloud",
        "google one", "microsoft 365", "office 365", "github",
    ],
    "Cuentas y Servicios": [
        # Utilities
        "electric", "electricidad", "enel", "cge", "chilectra", "light", "eletricidade",
        "water", "agua", "aguas andinas", "essbio", "sabesp",
        "gas", "metrogas", "lipigas", "gas natural",
        # Internet / telco
        "internet", "vtr", "mundo pacífico", "gtd", "fibra",
        "movistar", "entel", "claro", "wom", "at&t", "t-mobile", "verizon",
        "tim", "vivo", "oi", "personal", "antel",
    ],
    "Salud": [
        "farmacia", "pharmacy", "cvs", "walgreens", "cruz verde", "ahumada",
        "salcobrand", "dr simi", "pague menos", "drogasil",
        "clinic", "clinica", "clínica", "hospital", "isapre", "fonasa",
        "banmédica", "consalud", "colmena", "médico", "medico", "dentista",
        "laboratorio",
    ],
    "Viajes": [
        "airline", "aerolínea", "latam", "jetsmart", "sky airline", "gol",
        "azul", "american airlines", "delta", "united", "copa", "avianca",
        "aeromexico", "iberia", "airbnb", "booking", "expedia", "despegar",
        "kayak", "hotel", "hostal", "hostel", "trivago",
    ],
}


def _rule_based(merchant: str, text: str) -> str | None:
    haystack = f"{merchant} {text}".lower()
    for cat, keywords in _RULES.items():
        if any(k in haystack for k in keywords):
            return cat
    return None


def _user_learned(db: Optional[Session], user_id: Optional[int], merchant: str) -> str | None:
    """Check if the user has taught us this merchant before."""
    if db is None or user_id is None or not merchant:
        return None
    key = merchant.strip().lower()
    if not key:
        return None
    rule = (
        db.query(models.MerchantCategoryRule)
        .filter(
            models.MerchantCategoryRule.user_id == user_id,
            models.MerchantCategoryRule.merchant_key == key,
        )
        .first()
    )
    return rule.category if rule else None


def _llm_categorize(
    merchant: str, raw_text: str,
    db: Optional[Session] = None, user_id: Optional[int] = None,
) -> str | None:
    if not provider.is_available():
        return None
    resp = provider.chat_completion(
        [
            {
                "role": "system",
                "content": (
                    "Classify the transaction into EXACTLY ONE of these categories: "
                    + ", ".join(DEFAULT_CATEGORIES)
                    + ". Reply with only the category word."
                ),
            },
            {
                "role": "user",
                "content": f"Merchant: {merchant}\nReceipt:\n{raw_text[:1500]}",
            },
        ],
        temperature=0,
        max_tokens=10,
        purpose="categorize",
        user_id=user_id,
        db=db,
    )
    if resp is None:
        return None
    guess = resp.text.strip().strip(".").strip('"')
    return guess if guess in DEFAULT_CATEGORIES else None


def categorize(
    merchant: str, raw_text: str = "",
    *, db: Optional[Session] = None, user_id: Optional[int] = None,
) -> str:
    """
    Order matters: cheap → expensive.
      1. User's learned rules (free, instant)
      2. Built-in keyword rules (free, instant)
      3. LLM (paid, ~100ms)
    """
    return (
        _user_learned(db, user_id, merchant)
        or _rule_based(merchant, raw_text)
        or _llm_categorize(merchant, raw_text, db=db, user_id=user_id)
        or "Otros"
    )


def remember_correction(db: Session, user_id: int, merchant: str, category: str) -> None:
    """
    Called whenever the user sets/changes a transaction's category manually.
    Upserts a MerchantCategoryRule so next time same merchant → same category,
    no LLM call needed.
    """
    key = (merchant or "").strip().lower()
    if not key or not category:
        return
    rule = (
        db.query(models.MerchantCategoryRule)
        .filter(
            models.MerchantCategoryRule.user_id == user_id,
            models.MerchantCategoryRule.merchant_key == key,
        )
        .first()
    )
    if rule:
        rule.category = category
        rule.hits += 1
    else:
        db.add(models.MerchantCategoryRule(
            user_id=user_id,
            merchant_key=key,
            category=category,
            hits=1,
        ))
    db.commit()
