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


class PositionResponse(BaseModel):
    items: list[ItemPosition]


def _tesseract_lines(img: Image.Image) -> list[tuple[str, float]]:
    """OCR con Tesseract → lista de (texto_de_la_línea, y_centro_%) en orden
    de lectura (arriba a abajo). Agrupa palabras en líneas usando
    block_num/par_num/line_num, que pytesseract ya calcula."""
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
        (" ".join(e["words"]), ((e["top"] + e["bottom"]) / 2) / h * 100)
        for e in ordered
    ]


def _match_items_to_lines(items: list[str], lines: list[tuple[str, float]]) -> list[Optional[float]]:
    """Empareja cada ítem (en el orden en que vienen) con la mejor línea de
    Tesseract disponible, buscando siempre HACIA ADELANTE desde la última
    línea ya usada — nunca hacia atrás, así ítems con nombres repetidos
    (ej. 7x "Promo Alto del Carmen") no terminan todos apuntando a la misma
    línea de texto. La búsqueda no tiene límite de cuántas líneas puede
    saltar — una boleta real trae fácil 15-20 líneas de encabezado (fecha,
    dirección, "CANT/PRECIO/CODIGO", etc.) antes del primer ítem, así que
    una ventana angosta nunca llega a él. Lo que evita un match espurio
    lejano no es un límite de distancia, es el umbral de similitud."""
    MIN_SIMILARITY = 0.35
    results: list[Optional[float]] = []
    last_idx = -1
    for name in items:
        name_norm = name.strip().lower()
        best_ratio, best_idx = 0.0, -1
        for idx in range(last_idx + 1, len(lines)):
            ratio = SequenceMatcher(None, name_norm, lines[idx][0].strip().lower()).ratio()
            if ratio > best_ratio:
                best_ratio, best_idx = ratio, idx
        if best_idx >= 0 and best_ratio >= MIN_SIMILARITY:
            results.append(round(lines[best_idx][1], 1))
            last_idx = best_idx
        else:
            results.append(None)
    return results


@app.post("/position", response_model=PositionResponse)
def position(req: PositionRequest) -> PositionResponse:
    positions: list[Optional[float]]
    try:
        img_bytes = base64.b64decode(req.image_b64)
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        lines = _tesseract_lines(img)
        positions = _match_items_to_lines(req.items, lines)
    except Exception:
        positions = [None] * len(req.items)
    return PositionResponse(items=[
        ItemPosition(name=n, position_y=p) for n, p in zip(req.items, positions)
    ])


@app.get("/health")
def health():
    return {"ok": True}
