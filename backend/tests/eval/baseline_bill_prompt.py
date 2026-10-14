"""Prompt + schema de lectura de boleta tal como estaban en 0e5d21e (antes de
la revisión agnóstica de país/moneda, 2026-10-14) — para A/B con
`run_eval.py --variant baseline`. No se usa en producción."""

_BILL_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "merchant": {"type": "string", "description": "Nombre del local, o cadena vacía si no aparece"},
        "date": {"type": ["string", "null"], "description": "Fecha de la boleta si aparece, o null"},
        "printed_subtotal": {
            "type": ["number", "null"],
            "description": "El SUBTOTAL o TOTAL impreso en la boleta, o null si no aparece uno claro. Se usa solo como referencia, no reemplaza a 'amount'.",
        },
        "amount": {"type": "number", "description": "Total del consumo (SIN la propina sugerida si la hay)"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Nombre del ítem tal como aparece impreso"},
                    "quantity": {"type": ["number", "null"], "description": "Cantidad de unidades, o null si genuinamente no se puede determinar — NO ADIVINES, usa 1 solo si es razonablemente obvio que es 1."},
                    "line_total": {"type": ["number", "null"], "description": "Precio TOTAL de ESA línea completa (cantidad × precio unitario YA multiplicado), no el precio unitario. Si la boleta muestra 'cantidad x precio_unitario' por separado, multiplícalos vos. Si genuinamente no se puede leer el valor con confianza, usa null — NO INVENTES un número."},
                },
                "required": ["name", "quantity", "line_total"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["merchant", "date", "printed_subtotal", "amount", "items"],
    "additionalProperties": False,
}

_BILL_PROMPT = (
    "Lee esta boleta/comanda chilena y extrae cada ítem de consumo en el mismo "
    "orden en que aparecen impresos, con máxima precisión. No incluyas la línea "
    "de total/subtotal/propina como si fuera un ítem — pero SÍ copiá el "
    "subtotal impreso (si hay uno claro) en 'printed_subtotal'.\n\n"
    "REGLA CRÍTICA sobre cantidad y precio: muchas boletas de supermercado "
    "muestran 'CANTIDAD x PRECIO UNITARIO' en una línea separada del nombre "
    "del producto. NUNCA confundas el precio UNITARIO con el TOTAL de la "
    "línea — si no hay un total de línea ya multiplicado impreso, multiplicá "
    "vos cantidad × precio unitario para dar line_total. Si una boleta repite "
    "la MISMA línea de producto varias veces seguidas (mismo código de barras, "
    "mismo precio), sumá esas repeticiones en una sola entrada con quantity=N "
    "y line_total=la suma de todas — no las repitas como entradas separadas.\n\n"
    "Si genuinamente no podés leer un número con confianza real (foto "
    "borrosa, tapada, muy chica), usá null en ese campo — NUNCA inventes un "
    "valor solo para completar el JSON."
)
