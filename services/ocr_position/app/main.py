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
from concurrent.futures import ThreadPoolExecutor
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
    # --oem 1 = LSTM-only explícito. El paquete `tesseract-ocr-spa` que
    # instala el Dockerfile solo trae datos del motor LSTM (no legacy), así
    # que el modo automático (default) ya resuelve a esto en la práctica —
    # se deja explícito para no depender de eso si algún día cambia la
    # imagen base o el paquete. No cambia el resultado.
    data = pytesseract.image_to_data(img, lang="spa", output_type=Output.DICT, config="--oem 1")
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


_ROW_OVERLAP_MIN = 0.6  # fracción mínima de traslape vertical para considerar "misma fila"
_ROW_SEARCH_WINDOW = 6  # cuántas líneas mirar a cada lado (ordenadas por altura)


def _expand_to_full_row(lines: list[dict], idx: int) -> dict:
    """Tesseract a veces separa la cantidad y/o el precio del nombre del
    ítem en bloques de texto DISTINTOS cuando hay mucho espacio en blanco
    entre columnas (común en boletas con columnas bien separadas: cantidad
    a la izquierda, nombre al medio, precio pegado al borde derecho) — el
    nombre matchea bien, pero la caja queda angosta, sin la cantidad ni el
    valor. Se agranda el ancho para cubrir toda la fila física: se busca,
    cerca en el orden de lectura, cualquier otra línea que comparta CASI LA
    MISMA ALTURA en la foto (traslape vertical ≥60%) y se une su ancho.
    Es geométrico — no depende del idioma, mayúsculas, largo del nombre/
    valor, ni de si hay descuento u otro formato — así generaliza a
    cualquier boleta sin reglas por caso."""
    base = lines[idx]
    x0, x1 = base["x0"], base["x1"]
    lo = max(0, idx - _ROW_SEARCH_WINDOW)
    hi = min(len(lines), idx + _ROW_SEARCH_WINDOW + 1)
    for j in range(lo, hi):
        if j == idx:
            continue
        other = lines[j]
        top = max(base["y0"], other["y0"])
        bottom = min(base["y1"], other["y1"])
        inter = max(0.0, bottom - top)
        union = max(base["y1"], other["y1"]) - min(base["y0"], other["y0"])
        if union > 0 and inter / union >= _ROW_OVERLAP_MIN:
            x0 = min(x0, other["x0"])
            x1 = max(x1, other["x1"])
    return {**base, "x0": x0, "x1": x1}


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
            results.append(_expand_to_full_row(lines, best_idx))
            last_idx = best_idx
        else:
            results.append(None)
    return results


def _standard_variant(img: Image.Image) -> Image.Image:
    """Contraste + nitidez fijos — la mejora que ya se venía usando (y
    probando bien) para casi todas las boletas. Es la variante BASE que se
    prueba primero (más barata, ya validada); CLAHE es solo un escalón
    extra para cuando esta no alcanza."""
    from PIL import ImageEnhance
    img = ImageEnhance.Contrast(img).enhance(1.8)
    img = ImageEnhance.Sharpness(img).enhance(2.0)
    return img


def _clahe_variant(img: Image.Image) -> Optional[Image.Image]:
    """Versión con contraste LOCAL realzado (CLAHE sobre el canal L de LAB,
    + la misma nitidez de la variante estándar) — ayuda en fotos muy
    claras/lavadas o muy oscuras donde el contraste fijo no alcanza. No
    siempre ayuda más que el fijo (se probó en un set de boletas reales:
    mejora las fotos difíciles pero empeora otras que ya leían bien con el
    fijo) — por eso solo se prueba como ESCALÓN, nunca como primera opción.
    None si cv2 no está disponible (nunca debe romper el resto del
    servicio)."""
    try:
        import cv2
        import numpy as np
        from PIL import ImageEnhance
        arr = np.array(img)
        lab = cv2.cvtColor(arr, cv2.COLOR_RGB2LAB)
        l_ch, a_ch, b_ch = cv2.split(lab)
        l_ch = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(l_ch)
        arr = cv2.cvtColor(cv2.merge([l_ch, a_ch, b_ch]), cv2.COLOR_LAB2RGB)
        out = Image.fromarray(arr)
        return ImageEnhance.Sharpness(out).enhance(2.0)
    except Exception:
        return None


def _run_scale(base_img: Image.Image, items: list[str], scale: float) -> tuple[list[Optional[dict]], int]:
    w, h = base_img.size
    im = base_img if scale == 1.0 else base_img.resize(
        (max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS,
    )
    matches = _match_items_to_lines(items, _tesseract_lines(im))
    return matches, sum(1 for m in matches if m is not None)


def _best_of(results: list[tuple[list[Optional[dict]], int]], start: tuple[list[Optional[dict]], int]) -> tuple[list[Optional[dict]], int]:
    """Se queda con el mejor resultado — mismo criterio (`>` estricto,
    primero gana en empate) que si se hubiera recorrido `results` en orden
    de forma secuencial, sin importar en qué orden TERMINARON de calcularse
    (paralelo o no) — así paralelizar nunca cambia cuál gana."""
    matches, count = start
    for m, c in results:
        if c > count:
            count, matches = c, m
    return matches, count


def _match_best_effort(items: list[str], img: Image.Image) -> list[Optional[dict]]:
    """Corre el OCR + emparejamiento a varias escalas (ver
    `_candidate_scales`) con la variante de contraste ESTÁNDAR (ya probada)
    y, solo si con eso no alcanza a emparejar todos los ítems, escala a
    probar de nuevo con contraste realzado (CLAHE) — se queda con la
    combinación de escala+contraste que efectivamente logra emparejar más
    ítems con confianza. Así se adapta sola a la calidad y tamaño real de
    CADA foto en vez de asumir una resolución o un contraste que funcione
    siempre (no existe: probado en vivo que una resolución o un contraste
    fijo arregla una boleta y rompe otra que ya estaba bien).

    La escala más barata (1.0, sin reescalar) SIEMPRE se corre sola primero
    — si con eso ya emparejó todo (el caso común: 7 de 9 boletas del set de
    prueba), se corta ahí, exactamente 1 sola llamada a Tesseract, igual de
    rápido que si no existiera ninguna otra escala/variante. Solo en el
    caso difícil (no alcanzó el 100%) se lanzan en PARALELO el resto de
    escalas estándar + todas las escalas de CLAHE — como esas llamadas son
    independientes entre sí y ya de por sí se iban a correr todas en serie
    (nunca había early-stop en este caso), paralelizarlas no cambia qué
    resultado gana (ver `_best_of`), solo el tiempo de reloj: el peor caso
    pasa de la SUMA de todas las llamadas a la más LENTA de ellas."""
    std_img = _standard_variant(img)
    w, h = std_img.size
    scales = _candidate_scales(max(w, h))

    matches, count = _run_scale(std_img, items, scales[0])
    if count == len(items) or len(scales) == 1:
        return matches

    enhanced = _clahe_variant(img)
    with ThreadPoolExecutor(max_workers=8) as ex:
        std_futures = [ex.submit(_run_scale, std_img, items, s) for s in scales[1:]]
        clahe_futures = [ex.submit(_run_scale, enhanced, items, s) for s in scales] if enhanced is not None else []
        matches, count = _best_of([f.result() for f in std_futures], (matches, count))
        if clahe_futures:
            clahe_matches, clahe_count = _best_of([f.result() for f in clahe_futures[1:]], clahe_futures[0].result())
            if clahe_count > count:  # CLAHE gana SOLO si mejora estrictamente — misma regla de siempre
                matches, count = clahe_matches, clahe_count
    return matches


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
