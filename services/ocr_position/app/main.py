"""
Servicio interno AISLADO — solo calcula la posición y el tamaño real de cada
ítem de una boleta dentro de su foto, usando Tesseract (OCR clásico, gratis,
con cajas de texto reales por línea) emparejado por ORDEN contra la lista de
ítems que ya leyó bien el modelo de IA (ver backend/app/ocr.py,
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

# La resolución que Tesseract necesita para leer letra chica de boleta con
# fiabilidad VARÍA de foto en foto (calidad de cámara, compresión, tamaño de
# letra real, cuánto se recortó) — no hay un tamaño "bueno" único. Probado
# en vivo: una foto de 253×450px sin agrandar da GARABATO total ("Una Vara
# za" en vez de "Coca Cola Light"), pero agrandarla de más una foto que YA
# venía a buena resolución (1200×1600) también la EMPEORA (introduce
# borroneo que confunde a Tesseract en líneas repetidas parecidas). Por eso
# no se asume ningún tamaño fijo: se prueba el OCR a varias escalas
# calculadas a partir del tamaño REAL de esta foto en particular, y se usa
# la que efectivamente logra emparejar más ítems — nunca se adivina.
_CANDIDATE_TARGET_LONG_SIDES = [0, 1300, 2000, 2800]  # 0 = tamaño original, sin tocar
_MAX_UPSCALE = 4.0  # tope: una foto MUY chica no gana texto real de la nada


class PositionRequest(BaseModel):
    image_b64: str        # foto ya orientada hacia arriba, base64 sin el prefijo "data:...,"
    items: list[str]      # nombres de ítem en el mismo orden en que aparecen en la boleta


class ItemPosition(BaseModel):
    name: str
    position_y: Optional[float] = None  # 0-100 (centro vertical), o null si no hay match confiable
    # Caja REAL de la línea de texto emparejada (0-100 = % del alto/ancho de
    # la foto) — permite que la banda de color en el frontend se dibuje del
    # tamaño exacto del texto (nombre+valor) en vez de un tamaño inventado o
    # siempre a todo el ancho de la foto.
    bbox_x0: Optional[float] = None
    bbox_y0: Optional[float] = None
    bbox_x1: Optional[float] = None
    bbox_y1: Optional[float] = None


class PositionResponse(BaseModel):
    items: list[ItemPosition]


def _candidate_scales(long_side: int) -> list[float]:
    """Escalas a probar para ESTA foto en particular, de la más barata (1.0,
    tamaño original) a la más agrandada — calculadas, no fijas."""
    if long_side <= 0:
        return [1.0]
    scales = {1.0}
    for target in _CANDIDATE_TARGET_LONG_SIDES[1:]:
        scales.add(min(_MAX_UPSCALE, target / long_side))
    return sorted(s for s in scales if s >= 1.0)


def _tesseract_lines(img: Image.Image) -> list[dict]:
    """OCR con Tesseract sobre esta imagen TAL CUAL (sin reescalar) → lista
    de líneas en orden de lectura (arriba a abajo), cada una con su texto y
    su caja REAL (top/bottom/left/right, % del alto/ancho de la imagen que
    se le pasó). Agrupa palabras en líneas usando block_num/par_num/
    line_num, que pytesseract ya calcula."""
    w, h = img.size
    data = pytesseract.image_to_data(img, lang="spa", output_type=Output.DICT)
    lines: dict[tuple[int, int, int], dict] = {}
    for i in range(len(data["text"])):
        txt = data["text"][i].strip()
        if not txt:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        top, height = data["top"][i], data["height"][i]
        left, width = data["left"][i], data["width"][i]
        entry = lines.setdefault(key, {
            "words": [], "top": top, "bottom": top + height, "left": left, "right": left + width,
        })
        entry["words"].append(txt)
        entry["top"] = min(entry["top"], top)
        entry["bottom"] = max(entry["bottom"], top + height)
        entry["left"] = min(entry["left"], left)
        entry["right"] = max(entry["right"], left + width)
    ordered = sorted(lines.values(), key=lambda e: e["top"])
    return [
        {
            "text": " ".join(e["words"]),
            "y0": e["top"] / h * 100, "y1": e["bottom"] / h * 100,
            "x0": e["left"] / w * 100, "x1": e["right"] / w * 100,
        }
        for e in ordered
    ]


def _match_items_to_lines(items: list[str], lines: list[dict]) -> list[Optional[dict]]:
    """Empareja cada ítem (en el orden en que vienen) con la mejor línea de
    Tesseract disponible, buscando siempre HACIA ADELANTE desde la última
    línea ya usada — nunca hacia atrás, así ítems con nombres repetidos (ej.
    7x "Promo Alto del Carmen") no terminan todos apuntando a la misma línea
    de texto. Entre candidatos EMPATADOS en similitud (frecuente en ítems
    repetidos), gana el más cercano — la comparación es con `>` estricto, no
    `>=`, a propósito. La búsqueda no tiene límite de cuántas líneas puede
    saltar — una boleta real trae fácil 15-20 líneas de encabezado antes del
    primer ítem, así que una ventana angosta nunca llega a él."""
    MIN_SIMILARITY = 0.35
    results: list[Optional[dict]] = []
    last_idx = -1
    for name in items:
        name_norm = name.strip().lower()
        best_ratio, best_idx = 0.0, -1
        for idx in range(last_idx + 1, len(lines)):
            ratio = SequenceMatcher(None, name_norm, lines[idx]["text"].strip().lower()).ratio()
            if ratio > best_ratio:
                best_ratio, best_idx = ratio, idx
        if best_idx >= 0 and best_ratio >= MIN_SIMILARITY:
            results.append(lines[best_idx])
            last_idx = best_idx
        else:
            results.append(None)
    return results


def _match_best_effort(items: list[str], img: Image.Image) -> list[Optional[dict]]:
    """Corre el OCR + emparejamiento a varias escalas (ver
    `_candidate_scales`) y se queda con la que efectivamente logra
    emparejar más ítems con confianza — así se adapta sola a la calidad y
    tamaño real de CADA foto en vez de asumir una resolución que funcione
    siempre (no existe: se probó y una misma resolución fija arreglaba una
    boleta chica pero rompía una boleta que ya estaba bien de tamaño)."""
    w, h = img.size
    best_matches: list[Optional[dict]] = [None] * len(items)
    best_count = -1
    for scale in _candidate_scales(max(w, h)):
        im = img if scale == 1.0 else img.resize(
            (max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS,
        )
        matches = _match_items_to_lines(items, _tesseract_lines(im))
        count = sum(1 for m in matches if m is not None)
        if count > best_count:
            best_count, best_matches = count, matches
        if best_count == len(items):
            break  # ya emparejó todo — no vale la pena seguir probando escalas más caras
    return best_matches


@app.post("/position", response_model=PositionResponse)
def position(req: PositionRequest) -> PositionResponse:
    matches: list[Optional[dict]]
    try:
        img_bytes = base64.b64decode(req.image_b64)
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        matches = _match_best_effort(req.items, img)
    except Exception:
        matches = [None] * len(req.items)
    items_out: list[ItemPosition] = []
    for name, m in zip(req.items, matches):
        if m is None:
            items_out.append(ItemPosition(name=name))
        else:
            items_out.append(ItemPosition(
                name=name,
                position_y=round((m["y0"] + m["y1"]) / 2, 1),
                bbox_x0=round(m["x0"], 1), bbox_x1=round(m["x1"], 1),
                bbox_y0=round(m["y0"], 1), bbox_y1=round(m["y1"], 1),
            ))
    return PositionResponse(items=items_out)


@app.get("/health")
def health():
    return {"ok": True}
