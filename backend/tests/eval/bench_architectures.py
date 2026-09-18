"""
Fase 2 del plan (docs/PLAN_split_v3.md cont. 36): compara arquitecturas
candidatas de lectura de boleta contra las 28 fotos verificadas
(expected/*.json), reusando el MISMO scoring que ya usa run_eval.py
(score_one_bill) para que los números sean comparables entre sí.

Arquitecturas:
  A = ya cubierta por run_eval.py --pipeline bill (no se repite acá)
  B = SPLIT lab: 1 llamada, prompt de línea fija, CON racing (2x paralelo)
  C = igual que B pero SIN racing (una sola llamada)
  D = 2 llamadas (como A) pero con racing en la primera (la cara)

No usa vision_parse_bill ni bills.py — llama directo al modelo, igual que
split_lab.py, para no arriesgar el pipeline real mientras se mide.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config import settings
from app.ocr import _prep_receipt_image
from app.routers.split_lab import _LAB_PROMPT, _ITEM_LINE_RE, _TOTAL_LINE_RE, _parse_clp_number, _race_streams
from tests.eval.run_eval import score_one_bill

EVAL_DIR = Path(__file__).parent
EXPECTED_DIR = EVAL_DIR / "expected"
RECEIPTS_DIR = EVAL_DIR / "receipts"

_REFORMAT_PROMPT = """Convierte el texto que te paso (ya correcto, no cambies ningún valor) a líneas con este formato exacto, una por PRODUCTO, sin encabezado ni texto extra:
cantidad|nombre|total_de_esa_línea_sin_signos_de_pesos_ni_puntos

NO incluyas como si fuera un producto la línea de "Total"/"Consumo"/subtotal
— esa NO es un ítem, va aparte en AMOUNT más abajo. Si una línea no trae
cantidad explícita (p.ej. un descuento), usa 1.

Al final agrega estas 3 líneas con los datos reales que encuentres en el texto:
MERCHANT: nombre del local que aparece en el texto (o vacío si no aparece)
DATE: fecha que aparece en el texto, o null si no aparece
AMOUNT: el consumo/total (número, sin la propina sugerida)"""

_READ_PROMPT_FREE = (
    "Dame en texto esta boleta, con el nombre del local, la fecha, los "
    "items en ese orden con su cantidad y valor, y el total del consumo "
    "(sin la propina sugerida si la hay)."
)


def _parse_lines_to_items(text: str) -> tuple[list[dict], float | None]:
    items = []
    amount = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m_total = _TOTAL_LINE_RE.match(line)
        if m_total:
            amount = _parse_clp_number(m_total.group(1))
            continue
        m = _ITEM_LINE_RE.match(line)
        if not m:
            continue
        qty_raw, name, value_raw = m.groups()
        items.append({
            "name": name.strip(), "quantity": _parse_clp_number(qty_raw) or 1,
            "line_total": _parse_clp_number(value_raw), "price": _parse_clp_number(value_raw),
            "needs_review": False,
        })
    return items, amount


def _call_single(client, model: str, data_url: str, prompt: str) -> str:
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": [
                {"type": "text", "text": "Lee la boleta."},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]},
        ],
        reasoning_effort="low",
    )
    return (resp.choices[0].message.content or "").strip()


def arch_b(client, data_url: str) -> tuple[dict, float]:
    """1 llamada, prompt de línea fija, CON racing."""
    t0 = time.time()
    buf = ""
    for delta in _race_streams(client, model=settings.openai_vision_model_bill, messages=[
        {"role": "system", "content": _LAB_PROMPT},
        {"role": "user", "content": [
            {"type": "text", "text": "Lee la boleta."},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]},
    ]):
        buf += delta
    items, amount = _parse_lines_to_items(buf)
    return {"amount": amount, "items": items}, time.time() - t0


def arch_c(client, data_url: str) -> tuple[dict, float]:
    """1 llamada, prompt de línea fija, SIN racing."""
    t0 = time.time()
    text = _call_single(client, settings.openai_vision_model_bill, data_url, _LAB_PROMPT)
    items, amount = _parse_lines_to_items(text)
    return {"amount": amount, "items": items}, time.time() - t0


def arch_d(client, data_url: str) -> tuple[dict, float]:
    """2 llamadas (lectura libre + reformateo), CON racing en la 1ra."""
    t0 = time.time()
    buf = ""
    for delta in _race_streams(client, model=settings.openai_vision_model_bill, messages=[
        {"role": "system", "content": _READ_PROMPT_FREE},
        {"role": "user", "content": [
            {"type": "text", "text": "Lee la boleta."},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]},
    ]):
        buf += delta
    reformat = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "system", "content": _REFORMAT_PROMPT}, {"role": "user", "content": buf}],
        temperature=0.0,
    )
    reformatted = (reformat.choices[0].message.content or "").strip()
    from app.ocr import _parse_bill_text
    parsed = _parse_bill_text(reformatted) or {"amount": 0, "items": []}
    items = [{"name": it["name"], "quantity": it["quantity"], "line_total": it["line_total"],
              "price": it["line_total"], "needs_review": False} for it in parsed.get("items", [])]
    return {"amount": parsed.get("amount"), "items": items}, time.time() - t0


_SCHEMA_E = {
    "type": "object",
    "properties": {
        "merchant": {"type": "string", "description": "Nombre del local, o cadena vacía si no aparece"},
        "amount": {"type": "number", "description": "Total del consumo (número, SIN la propina sugerida si la hay)"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Nombre del ítem tal como aparece impreso"},
                    "quantity": {"type": "number", "description": "Cantidad de ese ítem (1 si no se indica explícitamente)"},
                    "line_total": {"type": "number", "description": "Precio TOTAL de esa línea (no precio unitario), como número sin separadores de miles ni símbolo de moneda"},
                },
                "required": ["name", "quantity", "line_total"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["merchant", "amount", "items"],
    "additionalProperties": False,
}

_PROMPT_E = (
    "Lee esta boleta/comanda y extrae cada ítem de consumo (nombre, cantidad, "
    "precio total de esa línea) en el mismo orden en que aparecen impresos. "
    "No incluyas la línea de total/subtotal/propina como si fuera un ítem. "
    "line_total es el número tal como está impreso en esa línea de la boleta "
    "(no inventes ni conviertas moneda) — no lo confundas con el precio unitario "
    "si la boleta muestra cantidad × precio unitario por separado."
)


def arch_e(client, data_url: str) -> tuple[dict, float]:
    """Responses API + Structured Outputs (json_schema estricto) — 1 llamada,
    sin parseo de texto propio: el SDK garantiza JSON válido contra el schema.
    Arquitectura nueva, investigada en developers.openai.com/api/docs/guides/
    structured-outputs + images-vision (2026-09-18), sin mirar el resto del
    pipeline existente al diseñarla."""
    t0 = time.time()
    resp = client.responses.create(
        model=settings.openai_vision_model_bill,
        input=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": _PROMPT_E},
                {"type": "input_image", "image_url": data_url, "detail": "high"},
            ],
        }],
        text={"format": {"type": "json_schema", "name": "receipt", "strict": True, "schema": _SCHEMA_E}},
        reasoning={"effort": "low"},
    )
    raw = json.loads(resp.output_text)
    items = [{"name": it["name"], "quantity": it.get("quantity") or 1, "line_total": it["line_total"],
              "price": it["line_total"], "needs_review": False} for it in raw.get("items", [])]
    return {"amount": raw.get("amount"), "items": items}, time.time() - t0


def arch_f(client, data_url: str) -> tuple[dict, float]:
    """Arquitectura E (Structured Outputs) + racing (2 copias en paralelo,
    gana la que responda primero) — combina la mejor precisión (E) con la
    técnica real de tail latency (myhoai.com, ya validada en B)."""
    import queue
    import threading

    t0 = time.time()
    q: "queue.Queue" = queue.Queue()

    def worker(idx: int):
        try:
            resp = client.responses.create(
                model=settings.openai_vision_model_bill,
                input=[{
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": _PROMPT_E},
                        {"type": "input_image", "image_url": data_url, "detail": "high"},
                    ],
                }],
                text={"format": {"type": "json_schema", "name": "receipt", "strict": True, "schema": _SCHEMA_E}},
                reasoning={"effort": "low"},
            )
            q.put((idx, resp.output_text))
        except Exception as exc:  # noqa: BLE001
            q.put((idx, exc))

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(2)]
    for th in threads:
        th.start()
    idx, result = q.get()
    while isinstance(result, Exception):
        idx, result = q.get()  # la primera copia falló -- esperar a la otra
    raw = json.loads(result)
    items = [{"name": it["name"], "quantity": it.get("quantity") or 1, "line_total": it["line_total"],
              "price": it["line_total"], "needs_review": False} for it in raw.get("items", [])]
    return {"amount": raw.get("amount"), "items": items}, time.time() - t0


_SCHEMA_G = {
    "type": "object",
    "properties": {
        "merchant": {"type": "string"},
        "printed_subtotal": {
            "type": ["number", "null"],
            "description": "El SUBTOTAL o TOTAL impreso en la boleta (para verificar la suma después), o null si no aparece uno claro.",
        },
        "amount": {"type": "number", "description": "Total del consumo (SIN la propina sugerida si la hay)"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "quantity": {"type": ["number", "null"], "description": "Cantidad de unidades, o null si genuinamente no se puede determinar — NO ADIVINES, usa 1 solo si es razonablemente obvio que es 1."},
                    "line_total": {"type": ["number", "null"], "description": "Precio TOTAL de ESA línea completa (cantidad × precio unitario YA multiplicado), no el precio unitario. Si la boleta muestra 'cantidad x precio_unitario' por separado, multiplícalos vos. Si genuinamente no se puede leer el valor con confianza, usa null — NO INVENTES un número."},
                },
                "required": ["name", "quantity", "line_total"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["merchant", "printed_subtotal", "amount", "items"],
    "additionalProperties": False,
}

_PROMPT_G = (
    "Lee esta boleta/comanda chilena y extrae cada ítem de consumo en el mismo "
    "orden en que aparecen impresos, con máxima precisión. No incluyas la línea "
    "de total/subtotal/propina como si fuera un ítem — pero SÍ copiá el "
    "subtotal impreso (si hay uno claro) en 'printed_subtotal' para poder "
    "verificar la suma después.\n\n"
    "REGLA CRÍTICA sobre cantidad y precio: muchas boletas de supermercado "
    "muestran 'CANTIDAD x PRECIO UNITARIO' en una línea separada del nombre "
    "del producto (ej. '6 x $1.550' seguido de un total de línea ya "
    "multiplicado, o a veces SIN el total ya multiplicado — en ese caso "
    "vos tenés que multiplicar cantidad × precio unitario para dar "
    "line_total). NUNCA confundas el precio UNITARIO con el TOTAL de la línea. "
    "Si una boleta repite la MISMA línea de producto varias veces seguidas "
    "(mismo código de barras, mismo precio), sumá esas repeticiones en una "
    "sola entrada con quantity=N y line_total=la suma de todas — no las "
    "repitas como entradas separadas.\n\n"
    "Si genuinamente no podés leer un número con confianza real (foto "
    "borrosa, tapada, muy chica), usá null en ese campo — NUNCA inventes un "
    "valor solo para completar el JSON."
)


def arch_g(client, data_url: str) -> tuple[dict, float]:
    """Arquitectura nueva, DE CERO, basada en técnicas reales de GitHub/blogs
    de práctica (no mirando el resto del pipeline al diseñarla — ver
    docs/PLAN_split_v3.md cont. 37 para las fuentes):
    - daddaops.com/blog/llm-receipt-parser: "Use null, do not guess" (evita
      alucinar datos faltantes) + guía explícita sobre cantidad×precio
      unitario vs. total de línea (el problema de raíz detrás de las peores
      fallas de este benchmark: jumbo_cencosud, lider_rancagua/pajaritos/2007
      leen el precio UNITARIO como si fuera el total).
    - brexhq/prompt-engineering: pedir el subtotal impreso aparte, para
      poder verificar la suma después (chain-of-thought aritmético
      implícito, sin gastar una llamada extra).
    - Structured Outputs (igual que E/F, ya validado como el formato más
      preciso de este benchmark) + racing (ya validado en D/F como la
      técnica real para la cola de latencia).
    Combina TODO lo aprendido en un solo diseño, no solo lo más nuevo."""
    import queue
    import threading

    t0 = time.time()
    q: "queue.Queue" = queue.Queue()

    def worker(idx: int):
        try:
            resp = client.responses.create(
                model=settings.openai_vision_model_bill,
                input=[{
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": _PROMPT_G},
                        {"type": "input_image", "image_url": data_url, "detail": "high"},
                    ],
                }],
                text={"format": {"type": "json_schema", "name": "receipt", "strict": True, "schema": _SCHEMA_G}},
                reasoning={"effort": "low"},
            )
            q.put((idx, resp.output_text))
        except Exception as exc:  # noqa: BLE001
            q.put((idx, exc))

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(2)]
    for th in threads:
        th.start()
    idx, result = q.get()
    while isinstance(result, Exception):
        idx, result = q.get()
    raw = json.loads(result)

    items = []
    for it in raw.get("items", []):
        lt = it.get("line_total")
        qty = it.get("quantity") or 1
        # needs_review SOLO cuando el propio modelo devolvió null (dijo "no
        # puedo leer esto con confianza") -- NO cuando la suma total no
        # cuadra. Ya se probó (cont. 29, misma sesión) que "marcar TODO
        # sospechoso" cuando algo agregado no cuadra genera demasiado ruido
        # (la mayoría de los ítems SÍ estaban bien) -- mismo error, no
        # repetirlo acá solo porque la señal esta vez es aritmética en vez
        # de un cruce con Tesseract.
        items.append({"name": it["name"], "quantity": qty, "line_total": lt if lt is not None else 0,
                       "price": lt if lt is not None else 0, "needs_review": lt is None})

    return {"amount": raw.get("amount"), "items": items}, time.time() - t0


def _tesseract_raw_text(image_bytes: bytes) -> str:
    """OCR clásico crudo, sin estructura -- solo para darle al modelo de
    visión un texto de referencia con los caracteres/dígitos exactos que
    Tesseract detectó, sin pedirle que lo interprete (eso lo sigue haciendo
    el modelo de visión, mirando la imagen para entender qué número
    pertenece a qué ítem)."""
    import io
    import pytesseract
    from PIL import Image
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size
    scale = min(4.0, max(1.0, 2000 / max(w, h)))
    if scale != 1.0:
        img = img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    return pytesseract.image_to_string(img, lang="spa", config="--oem 1")


_PROMPT_H = (
    "Lee esta boleta/comanda chilena y extrae cada ítem de consumo en el mismo "
    "orden en que aparecen impresos, con máxima precisión.\n\n"
    "Además de mirar la foto, te paso abajo el texto CRUDO que un OCR clásico "
    "(Tesseract) ya extrajo de la MISMA foto. Ese texto puede tener errores de "
    "layout (líneas mezcladas, columnas pegadas) y NO tiene que ser la fuente "
    "de los nombres de producto — para eso confiá en lo que VOS ves en la "
    "imagen. Pero para los NÚMEROS (cantidades, precios) usalo como referencia "
    "cruzada: si un número que ves en la imagen no aparece en ninguna forma "
    "reconocible en este texto OCR, es una señal de que podrías estar "
    "alucinando ese dígito — revisá la imagen de nuevo con más cuidado antes "
    "de darlo por bueno.\n\n"
    "No incluyas la línea de total/subtotal/propina como si fuera un ítem. "
    "Si una boleta muestra 'CANTIDAD x PRECIO UNITARIO' por separado del "
    "total de línea, multiplicá vos cantidad × precio unitario para dar "
    "line_total — NUNCA confundas el precio unitario con el total de línea. "
    "Si genuinamente no podés leer un número con confianza real, usá null "
    "— NUNCA inventes un valor solo para completar el JSON.\n\n"
    "--- TEXTO OCR (Tesseract, referencia para dígitos) ---\n{ocr_text}"
)


def arch_h(client, data_url: str, image_bytes: bytes) -> tuple[dict, float]:
    """Arquitectura nueva, DE CERO, basada en un patrón real y documentado
    (GitHub hoyla/fusion-ocr + literatura de hallucination en VLMs, ver
    docs/PLAN_split_v3.md cont. 37): OCR clásico (Tesseract, YA lo tenemos
    corriendo gratis para el matching de posición) como capa determinística
    anti-alucinación -- el modelo de visión sigue decidiendo QUÉ es cada
    ítem (para eso la imagen es mejor que Tesseract), pero se le da el
    texto de Tesseract como referencia cruzada para los DÍGITOS exactos,
    en vez de confiar ciegamente en lo que 've' en la imagen. Un solo
    llamado de visión (no dos), + racing + Structured Outputs (igual que
    F/G, ya validados)."""
    import queue
    import threading

    t0 = time.time()
    try:
        ocr_text = _tesseract_raw_text(image_bytes)
    except Exception:  # noqa: BLE001
        ocr_text = "(no disponible)"
    prompt = _PROMPT_H.format(ocr_text=ocr_text[:3000])

    q: "queue.Queue" = queue.Queue()

    def worker(idx: int):
        try:
            resp = client.responses.create(
                model=settings.openai_vision_model_bill,
                input=[{
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_image", "image_url": data_url, "detail": "high"},
                    ],
                }],
                text={"format": {"type": "json_schema", "name": "receipt", "strict": True, "schema": _SCHEMA_G}},
                reasoning={"effort": "low"},
            )
            q.put((idx, resp.output_text))
        except Exception as exc:  # noqa: BLE001
            q.put((idx, exc))

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(2)]
    for th in threads:
        th.start()
    idx, result = q.get()
    while isinstance(result, Exception):
        idx, result = q.get()
    raw = json.loads(result)
    items = [{"name": it["name"], "quantity": it.get("quantity") or 1,
              "line_total": it["line_total"] if it.get("line_total") is not None else 0,
              "price": it["line_total"] if it.get("line_total") is not None else 0,
              "needs_review": it.get("line_total") is None} for it in raw.get("items", [])]
    return {"amount": raw.get("amount"), "items": items}, time.time() - t0




def arch_i(client, data_url: str) -> tuple[dict, float]:
    """Igual que G pero con reasoning effort 'none' en vez de 'low' --
    hallazgo real (2026-09-18): con Structured Outputs (null como escape
    de incertidumbre) 'none' midió 5.5s vs 24.6s en la boleta mas lenta
    conocida, sin perder precision ahi -- lo contrario de lo que se sabia
    del pipeline viejo de texto libre (donde 'none' SI alucinaba). Se
    prueba contra las 28 antes de creerlo."""
    import queue
    import threading

    t0 = time.time()
    q: "queue.Queue" = queue.Queue()

    def worker(idx: int):
        try:
            resp = client.responses.create(
                model=settings.openai_vision_model_bill,
                input=[{
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": _PROMPT_G},
                        {"type": "input_image", "image_url": data_url, "detail": "high"},
                    ],
                }],
                text={"format": {"type": "json_schema", "name": "receipt", "strict": True, "schema": _SCHEMA_G}},
                reasoning={"effort": "none"},
            )
            q.put((idx, resp.output_text))
        except Exception as exc:  # noqa: BLE001
            q.put((idx, exc))

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(2)]
    for th in threads:
        th.start()
    idx, result = q.get()
    while isinstance(result, Exception):
        idx, result = q.get()
    raw = json.loads(result)
    items = []
    for it in raw.get("items", []):
        lt = it.get("line_total")
        qty = it.get("quantity") or 1
        items.append({"name": it["name"], "quantity": qty, "line_total": lt if lt is not None else 0,
                       "price": lt if lt is not None else 0, "needs_review": lt is None})
    return {"amount": raw.get("amount"), "items": items}, time.time() - t0




def _reconciliation_err(raw: dict) -> float:
    items_sum = sum((it.get("line_total") or 0) for it in raw.get("items", []))
    sub = raw.get("printed_subtotal")
    if not sub:
        return float("inf")
    return abs(items_sum - sub) / max(sub, 1)


def arch_j(client, data_url: str) -> tuple[dict, float]:
    """'none' + 'low' en paralelo (NO 2 copias iguales) -- se queda con la
    que reconcilie mejor (suma de items vs su propio printed_subtotal), no
    con la mas rapida a ciegas. Si 'none' reconcilia bien, se usa su
    velocidad; si no, cae a 'low' sin esperar mas de lo que 'low' ya
    tardaba solo. Hallazgo que motiva esto: 'none' solo (arch I) dio
    78.0% -- mas rapido pero pierde precision real en casos que 'low'
    tenia perfectos -- probar si la reconciliacion puede rescatar esos
    casos sin pagar el costo completo de 'low' siempre."""
    import queue
    import threading

    t0 = time.time()
    q: "queue.Queue" = queue.Queue()

    def worker(idx: int, effort: str):
        try:
            resp = client.responses.create(
                model=settings.openai_vision_model_bill,
                input=[{
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": _PROMPT_G},
                        {"type": "input_image", "image_url": data_url, "detail": "high"},
                    ],
                }],
                text={"format": {"type": "json_schema", "name": "receipt", "strict": True, "schema": _SCHEMA_G}},
                reasoning={"effort": effort},
            )
            q.put((idx, effort, resp.output_text))
        except Exception as exc:  # noqa: BLE001
            q.put((idx, effort, exc))

    threads = [
        threading.Thread(target=worker, args=(0, "none"), daemon=True),
        threading.Thread(target=worker, args=(1, "low"), daemon=True),
    ]
    for th in threads:
        th.start()

    results = {}
    # espera a "none" primero (rapido) y decide: si reconcilia bien, listo;
    # si no, espera a que "low" termine (ya viene corriendo en paralelo,
    # no se pierde tiempo extra por esperar secuencial).
    for _ in range(2):
        idx, effort, val = q.get()
        results[effort] = val
        if effort == "none" and not isinstance(val, Exception):
            raw = json.loads(val)
            if _reconciliation_err(raw) <= 0.02:
                break  # "none" reconcilia -> no hace falta esperar a "low"

    chosen = None
    for effort in ("none", "low"):
        val = results.get(effort)
        if val is None or isinstance(val, Exception):
            continue
        raw = json.loads(val) if isinstance(val, str) else val
        if chosen is None or _reconciliation_err(raw) < _reconciliation_err(chosen):
            chosen = raw
    raw = chosen or {"items": [], "amount": 0}

    items = []
    for it in raw.get("items", []):
        lt = it.get("line_total")
        qty = it.get("quantity") or 1
        items.append({"name": it["name"], "quantity": qty, "line_total": lt if lt is not None else 0,
                       "price": lt if lt is not None else 0, "needs_review": lt is None})
    return {"amount": raw.get("amount"), "items": items}, time.time() - t0


ARCHS = {"B": arch_b, "C": arch_c, "D": arch_d, "E": arch_e, "F": arch_f, "G": arch_g, "I": arch_i, "J": arch_j}
ARCHS_NEED_BYTES = {"H": arch_h}

# fotos duplicadas -> mismo fixture que otra ya escrita
ALIASES = {
    "images-10": "danes_vitacura", "images-11": "dondewilly_vinadelmar",
    "images-12": "cuenta_valeria", "images-17": "barlaprovidencia",
    "images-20": "mistura_del_peru", "images-21": "cuenta_valeria",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arch", choices=list(ARCHS) + list(ARCHS_NEED_BYTES), required=True)
    args = ap.parse_args()

    from openai import OpenAI
    client = OpenAI(api_key=settings.openai_api_key, timeout=60.0)
    needs_bytes = args.arch in ARCHS_NEED_BYTES
    fn = ARCHS_NEED_BYTES[args.arch] if needs_bytes else ARCHS[args.arch]

    stems = sorted(p.stem for p in EXPECTED_DIR.glob("*.json"))
    pcts = []
    times = []
    rows = []
    print(f"\n  arquitectura {args.arch}  imagenes={len(stems)}\n")
    for stem in stems:
        exp = json.loads((EXPECTED_DIR / f"{stem}.json").read_text())
        img_candidates = list(RECEIPTS_DIR.glob(f"{stem}.*"))
        if not img_candidates:
            print(f"  ?? {stem}: sin foto en receipts/")
            continue
        img_bytes = img_candidates[0].read_bytes()
        data_url, _, _, _ = _prep_receipt_image(img_bytes)
        try:
            parsed, elapsed = fn(client, data_url, img_bytes) if needs_bytes else fn(client, data_url)
        except Exception as exc:  # noqa: BLE001
            print(f"  !! {stem}: EXC {exc}")
            continue
        times.append(elapsed)
        result = score_one_bill(parsed, exp)
        pcts.append(result["pct"])
        rows.append({"stem": stem, "pct": result["pct"], "elapsed": elapsed})
        mark = "✓" if result["pct"] >= 80 else ("~" if result["pct"] >= 60 else "✗")
        fails = [f"{k}[{c['detail']}]" for k, c in result["checks"].items()
                 if c["applicable"] and not c["ok"]]
        print(f"  {mark} {stem:28s} {result['pct']:5.1f}%  {elapsed:5.1f}s  " + ("  ".join(fails[:2]) if fails else "todo ok"))

    overall = sum(pcts) / len(pcts) if pcts else 0.0
    times_sorted = sorted(times)
    print(f"\n  OVERALL arch {args.arch}: {overall:.1f}%   ({len(pcts)}/{len(stems)} imagenes)  "
          f"p50={times_sorted[len(times_sorted)//2]:.1f}s  "
          f"p95={times_sorted[int(len(times_sorted)*0.95)]:.1f}s  "
          f"max={max(times):.1f}s  mean={sum(times)/len(times):.1f}s\n")

    out_path = EVAL_DIR / "results" / f"bench_arch_{args.arch}.json"
    out_path.write_text(json.dumps({"arch": args.arch, "overall": round(overall, 1), "rows": rows}, indent=2, ensure_ascii=False))
    print(f"  guardado -> {out_path}")


if __name__ == "__main__":
    main()
