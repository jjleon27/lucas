"""
Servicio interno AISLADO — solo calcula la posición vertical aproximada de
cada ítem de una boleta dentro de su foto, usando Tesseract (OCR clásico,
gratis, con cajas de texto reales por línea) emparejado por ORDEN contra la
lista de ítems que ya leyó bien el modelo de IA (ver backend/app/ocr.py,
vision_parse_bill — esa lectura de nombres/precios no depende para nada de
este servicio).

No es público: Vercel solo deja llamarlo desde el backend principal vía un
"binding" (ver vercel.json) — nunca desde internet. Si este servicio falla,
tarda, o no encuentra un match confiable para algún ítem, esa posición queda
en null y el frontend cae al reparto parejo que ya existía antes (ver
`defaultBandPct` en frontend/src/app/split/page.tsx) — nunca bloquea ni
rompe la subida de una boleta.
"""
from __future__ import annotations

import base64
import io
from difflib import SequenceMatcher
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel
from PIL import Image
import pytesseract
from pytesseract import Output

app = FastAPI()


class PositionRequest(BaseModel):
    image_b64: str        # foto ya orientada hacia arriba, base64 sin el prefijo "data:...,"
    items: list[str]      # nombres de ítem en el mismo orden en que aparecen en la boleta


class ItemPosition(BaseModel):
    name: str
    position_y: Optional[float] = None  # 0-100 (centro vertical), o null si no hay match confiable
    # Caja vertical REAL de la línea de texto emparejada (0-100 = % del alto
    # de la foto) — permite que la banda de color en el frontend se dibuje
    # del alto exacto del texto (nombre+valor) en vez de una altura inventada.
    bbox_y0: Optional[float] = None
    bbox_y1: Optional[float] = None


class PositionResponse(BaseModel):
    items: list[ItemPosition]


def _tesseract_lines(img: Image.Image) -> list[dict]:
    """OCR con Tesseract → lista de líneas en orden de lectura (arriba a
    abajo), cada una con su texto y su caja vertical real (% del alto de la
    foto). Agrupa palabras en líneas usando block_num/par_num/line_num, que
    pytesseract ya calcula."""
    w, h = img.size
    data = pytesseract.image_to_data(img, lang="spa", output_type=Output.DICT)
    lines: dict[tuple[int, int, int], dict] = {}
    for i in range(len(data["text"])):
        txt = data["text"][i].strip()
        if not txt:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        top, height = data["top"][i], data["height"][i]
        entry = lines.setdefault(key, {"words": [], "top": top, "bottom": top + height})
        entry["words"].append(txt)
        entry["top"] = min(entry["top"], top)
        entry["bottom"] = max(entry["bottom"], top + height)
    ordered = sorted(lines.values(), key=lambda e: e["top"])
    return [
        {"text": " ".join(e["words"]), "y0": e["top"] / h * 100, "y1": e["bottom"] / h * 100}
        for e in ordered
    ]


def _match_items_to_lines(items: list[str], lines: list[dict]) -> list[Optional[tuple[float, float]]]:
    """Empareja cada ítem (en el orden en que vienen) con la mejor línea de
    Tesseract disponible, buscando siempre HACIA ADELANTE desde la última
    línea ya usada — nunca hacia atrás, así ítems con nombres repetidos
    (ej. 7x "Promo Alto del Carmen") no terminan todos apuntando a la misma
    línea de texto. La búsqueda no tiene límite de cuántas líneas puede
    saltar — una boleta real trae fácil 15-20 líneas de encabezado (fecha,
    dirección, "CANT/PRECIO/CODIGO", etc.) antes del primer ítem, así que
    una ventana angosta nunca llega a él. Lo que evita un match espurio
    lejano no es un límite de distancia, es el umbral de similitud.

    Devuelve, por ítem, (y0, y1) de la línea emparejada, o None si no hubo
    match confiable."""
    MIN_SIMILARITY = 0.35
    results: list[Optional[tuple[float, float]]] = []
    last_idx = -1
    for name in items:
        name_norm = name.strip().lower()
        best_ratio, best_idx = 0.0, -1
        for idx in range(last_idx + 1, len(lines)):
            ratio = SequenceMatcher(None, name_norm, lines[idx]["text"].strip().lower()).ratio()
            if ratio > best_ratio:
                best_ratio, best_idx = ratio, idx
        if best_idx >= 0 and best_ratio >= MIN_SIMILARITY:
            results.append((round(lines[best_idx]["y0"], 1), round(lines[best_idx]["y1"], 1)))
            last_idx = best_idx
        else:
            results.append(None)
    return results


@app.post("/position", response_model=PositionResponse)
def position(req: PositionRequest) -> PositionResponse:
    matches: list[Optional[tuple[float, float]]]
    try:
        img_bytes = base64.b64decode(req.image_b64)
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        lines = _tesseract_lines(img)
        matches = _match_items_to_lines(req.items, lines)
    except Exception:
        matches = [None] * len(req.items)
    items_out: list[ItemPosition] = []
    for name, m in zip(req.items, matches):
        if m is None:
            items_out.append(ItemPosition(name=name))
        else:
            y0, y1 = m
            items_out.append(ItemPosition(name=name, position_y=round((y0 + y1) / 2, 1), bbox_y0=y0, bbox_y1=y1))
    return PositionResponse(items=items_out)


@app.get("/health")
def health():
    return {"ok": True}
