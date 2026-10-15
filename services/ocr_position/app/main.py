"""
Servicio interno AISLADO — solo calcula la posición y el tamaño real de cada
ítem de una boleta dentro de su foto. Usa PP-OCR (RapidOCR + onnxruntime,
Apache-2.0, modelos incluidos en el paquete de PyPI: detector DBNet que da
cajas por línea robustas a fotos chicas, inclinadas o con luz despareja) y
alinea por ORDEN, de forma global, la lista de ítems que ya leyó el modelo de
IA (backend/app/ocr.py, vision_parse_bill — esa lectura de nombres/precios no
depende para nada de este servicio) contra las filas de texto de la foto.

No es público: Vercel solo deja llamarlo desde el backend principal vía un
"binding" (ver vercel.json) — nunca desde internet. Si este servicio falla,
tarda, o no encuentra un match confiable para algún ítem, esa posición queda
en null y el frontend cae al reparto parejo (`defaultBandPct` en
frontend/src/app/split/page.tsx) — nunca bloquea ni rompe la subida.

Historia (auditoría 2026-10-14, docs/SESSION_STATE.md): antes era Tesseract
con un torneo de escalas + CLAHE + enderezado y un emparejamiento codicioso
hacia adelante. Medido en el eval del repo, PP-OCR en UNA sola pasada lee
nombre+precio del mismo ítem en ~70-80 % de los ítems contra 18-40 % de
Tesseract, y no gana nada con reescalar ni con variantes de contraste (ver
services/ocr_position/tests/bench_position.py) — por eso no hay variantes.
"""
from __future__ import annotations

import base64
import io
import math
import os
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from difflib import SequenceMatcher
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel
from PIL import Image

# Hilos de onnxruntime: nunca más que los núcleos reales (con más, los hilos
# compiten por CPU y la latencia se dispara — misma lección que con el
# OpenMP de Tesseract). OCR_THREADS permite ajustarlo sin tocar código.
_THREADS = max(1, min(int(os.environ.get("OCR_THREADS") or 4), os.cpu_count() or 1))
os.environ.setdefault("OMP_NUM_THREADS", str(_THREADS))

# Tope de reloj de TODO el pedido, desde que llega. El backend corta a los 7 s
# (ocr._populate_positions) y hay que dejar margen de red. La inferencia de
# onnxruntime no se puede interrumpir, así que el tope real lo da el tamaño de
# la imagen (`_MAX_SIDE`): esto solo evita seguir esperando un turno que ya no
# llega a tiempo cuando hay pedidos concurrentes.
_REQUEST_BUDGET_S = 5.5
# Lado máximo con el que se corre el OCR (el backend ya manda ≤2000 px). Más
# grande no lee mejor y la latencia crece con el área.
_MAX_SIDE = int(os.environ.get("OCR_MAX_SIDE") or 2000)
_DET_MAX_SIDE = int(os.environ.get("OCR_DET_MAX_SIDE") or 1280)

app = FastAPI()


class PositionRequest(BaseModel):
    image_b64: str        # foto ya orientada hacia arriba, base64 sin el prefijo "data:...,"
    items: list[str]      # nombres de ítem en el mismo orden en que aparecen en la boleta
    # Opcional: total de cada ítem según la lectura del modelo (mismo orden que
    # `items`). Si viene, una fila que además contiene ese número suma un bono
    # chico en el alineamiento — desempata ítems de nombre parecido. Nunca es
    # requisito (el OCR puede no leer el precio).
    prices: Optional[list[Optional[float]]] = None


class Segment(BaseModel):
    x0: float
    x1: float
    # Alto REAL (0-100 = % del alto de la foto) de las cajas de texto de las
    # que vino ESTE tramo — no el del ítem completo: nombre y precio de una
    # misma fila física pueden quedar a alturas distintas (boleta doblada o
    # foto inclinada) y cada tramo se dibuja sobre su propio texto.
    y0: float
    y1: float


class ItemPosition(BaseModel):
    name: str
    position_y: Optional[float] = None  # 0-100 (centro vertical), o null si no hay match confiable
    # Rango vertical que cubre TODOS los segmentos del ítem (0-100). Se
    # mantiene como fallback/compat; el frontend prefiere el y0/y1 de cada
    # `Segment`.
    bbox_y0: Optional[float] = None
    bbox_y1: Optional[float] = None
    # Uno o más tramos horizontales REALES (0-100 = % del ancho de la foto):
    # nombre+cantidad por un lado, precio por otro, cuando hay un hueco en
    # blanco grande entre columnas — la banda se dibuja solo sobre el texto.
    segments: list[Segment] = []


class PositionResponse(BaseModel):
    items: list[ItemPosition]
    # Texto crudo de cada fila, en orden de lectura (subproducto del OCR).
    ocr_lines: list[str] = []


# ---------------------------------------------------------------- motor OCR

_engine = None
_engine_init_lock = threading.Lock()
# Una inferencia a la vez: cada una ya usa todos los núcleos; dos en paralelo
# solo se pelearían la CPU y terminarían las dos tarde.
_worker = ThreadPoolExecutor(max_workers=1)


def _get_engine():
    global _engine
    if _engine is None:
        with _engine_init_lock:
            if _engine is None:
                from rapidocr import RapidOCR
                _engine = RapidOCR(params={
                    "Global.log_level": "error",
                    # El backend ya manda la foto derecha (EXIF + giro manual
                    # del usuario): el clasificador de 180° por línea solo
                    # suma tiempo.
                    "Global.use_cls": False,
                    "Global.max_side_len": _MAX_SIDE,
                    "EngineConfig.onnxruntime.intra_op_num_threads": _THREADS,
                    "EngineConfig.onnxruntime.inter_op_num_threads": 1,
                    # El detector corre con el lado mayor ≤ _DET_MAX_SIDE (el
                    # reconocedor igual lee los recortes a resolución completa).
                    # Medido: igual de preciso que a 2000 px y ~40 % más rápido
                    # en fotos de celular grandes.
                    "Det.limit_type": "max",
                    "Det.limit_side_len": _DET_MAX_SIDE,
                })
    return _engine


@app.on_event("startup")
def _load_models() -> None:
    """Carga los modelos UNA vez al arrancar el contenedor (y hace una
    inferencia de calentamiento) — el primer pedido real no paga ese costo."""
    try:
        import numpy as np
        _get_engine()(np.full((64, 256, 3), 255, dtype=np.uint8))
    except Exception as exc:  # noqa: BLE001 — nunca impedir que el servicio levante
        print(f"[ocr_position] no se pudo precargar el OCR: {exc}")


def _ocr_boxes(img: Image.Image) -> list[dict]:
    """Corre PP-OCR sobre la foto → cajas de texto (una por línea detectada)
    con su texto y su cuadrilátero en píxeles."""
    import numpy as np
    arr = np.array(img.convert("RGB"))[:, :, ::-1]  # RGB → BGR (lo que espera RapidOCR)
    res = _get_engine()(arr)
    if res is None or res.boxes is None or res.txts is None:
        return []
    out = []
    for quad, txt in zip(res.boxes, res.txts):
        txt = (txt or "").strip()
        if txt:
            out.append({"text": txt, "quad": [(float(x), float(y)) for x, y in quad]})
    return out


# ---------------------------------------------------------------- texto

def _fold(s: str) -> str:
    """Minúsculas sin tildes ni marcas (NFKD + casefold): 'Café' == 'CAFE'."""
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).casefold()


def _script(c: str) -> str:
    """Escritura de una letra ('LATIN', 'CJK', 'CYRILLIC', ...) según su
    nombre Unicode — aproximación suficiente sin dependencias."""
    try:
        return unicodedata.name(c).split(" ")[0]
    except ValueError:
        return ""


def _script_filter(items: list[str]):
    """El reconocedor es multilingüe y a veces cuela una letra de otra
    escritura (p. ej. un carácter chino en una boleta en español). Se
    descartan las letras cuya escritura no aparece en NINGÚN nombre de ítem —
    regla general: en una boleta japonesa los ítems traen kanji y se
    conservan."""
    scripts = {_script(c) for name in items for c in _fold(name) if c.isalpha()}
    if not scripts:
        return lambda s: s

    def keep(s: str) -> str:
        return "".join(c for c in s if not c.isalpha() or _script(_fold(c)[:1] or c) in scripts)
    return keep


def _letters(s: str) -> str:
    """Solo los tokens que tienen alguna letra (descarta cantidades, precios
    en cualquier formato, códigos de artículo) — independiente de idioma y
    moneda. Así 'te' vs '1 TE 1.500' compara 'te' con 'te'."""
    toks = [t for t in _fold(s).split() if any(c.isalpha() for c in t)]
    return " ".join(re.sub(r"[\W_]+", "", t) for t in toks).strip()


def _similarity(name: str, text: str) -> float:
    """Parecido entre el nombre de un ítem y el texto de una fila, 0-1.
    Toma el mejor entre la razón global y la COBERTURA del texto más corto
    dentro del más largo (el modelo suele expandir abreviaturas — 'CERV
    KUNST' vs 'Cerveza Kunstmann' — o la fila trae palabras de más), con una
    penalización suave cuando uno es mucho más corto que el otro."""
    a, b = name, text
    if not a or not b:
        return 0.0
    r = SequenceMatcher(None, a, b, autojunk=False).ratio()
    s, l = (a, b) if len(a) <= len(b) else (b, a)
    if len(s) >= 3:
        matched = sum(m.size for m in SequenceMatcher(None, s, l, autojunk=False).get_matching_blocks())
        r = max(r, 0.9 * matched / len(s) * min(1.0, (len(s) / len(l)) ** 0.25))
    return r


def _numbers(text: str) -> set[str]:
    """Todos los números de la fila como cadenas de dígitos, sin separadores
    de miles/decimales ('1.500', '1,500', '15.00' → '1500')."""
    out = set()
    for m in re.finditer(r"\d[\d.,' ]*\d|\d", text):
        d = re.sub(r"\D", "", m.group())
        out.add(d.lstrip("0") or "0")
        for part in m.group().split():
            dd = re.sub(r"\D", "", part)
            if dd:
                out.add(dd.lstrip("0") or "0")
    return out


def _price_keys(v: Optional[float]) -> set[str]:
    """Formas en dígitos en que puede aparecer impreso un monto (entero o con
    2 decimales), sin depender de la moneda ni del separador."""
    if v is None:
        return set()
    try:
        a = abs(float(v))
    except (TypeError, ValueError):
        return set()
    if not math.isfinite(a) or a == 0:
        return set()
    keys = {f"{a:.2f}".replace(".", "").lstrip("0")}
    if a == int(a):
        keys.add(str(int(a)))
    return keys


# ---------------------------------------------------------------- filas

def _skew(boxes: list[dict]) -> float:
    """Inclinación (radianes) del texto de la foto: mediana del ángulo del
    borde superior de las cajas alargadas. DBNet devuelve cuadriláteros que
    siguen al texto, así que esto sale gratis y sin otra pasada."""
    angles = []
    for b in boxes:
        (x0, y0), (x1, y1) = b["quad"][0], b["quad"][1]
        w = math.hypot(x1 - x0, y1 - y0)
        h = math.hypot(b["quad"][3][0] - x0, b["quad"][3][1] - y0)
        if w > 2 * h and x1 > x0:
            angles.append(math.atan2(y1 - y0, x1 - x0))
    angles.sort()
    return angles[len(angles) // 2] if angles else 0.0


def _rows(boxes: list[dict], w: int, h: int) -> list[dict]:
    """Agrupa las cajas en FILAS físicas (nombre, cantidad y precio de un
    mismo renglón suelen ser cajas separadas por el hueco entre columnas).
    Se agrupa por el centro vertical en el marco ENDEREZADO (se deshace la
    inclinación medida en `_skew`), así una foto torcida no mezcla
    renglones. Cada fila guarda el tramo de cada caja (en % de la foto
    original) para dibujar los segmentos."""
    ang = _skew(boxes)
    cos, sin = math.cos(ang), math.sin(ang)
    prepared = []
    for b in boxes:
        xs = [p[0] for p in b["quad"]]
        ys = [p[1] for p in b["quad"]]
        cx, cy = sum(xs) / 4, sum(ys) / 4
        q = b["quad"]
        bh = (math.hypot(q[3][0] - q[0][0], q[3][1] - q[0][1]) + math.hypot(q[2][0] - q[1][0], q[2][1] - q[1][1])) / 2
        prepared.append({
            "text": b["text"],
            "c": -cx * sin + cy * cos,     # centro vertical sin inclinación
            "u": cx * cos + cy * sin,      # posición horizontal sin inclinación
            "h": max(bh, 1.0),
            "span": (min(xs) / w * 100, max(xs) / w * 100, min(ys) / h * 100, max(ys) / h * 100),
        })
    prepared.sort(key=lambda p: p["c"])
    rows: list[dict] = []
    for p in prepared:
        # La fila más cercana entre las últimas; misma fila si los centros
        # difieren menos de medio alto de la caja MÁS BAJA — una caja mal
        # detectada que abarca dos renglones no puede fusionarlos.
        near = min(rows[-4:], key=lambda r: abs(r["c"] - p["c"]), default=None)
        if near is not None and abs(near["c"] - p["c"]) < 0.5 * min(near["h"], p["h"]):
            near["parts"].append(p)
            near["c"] = sum(x["c"] for x in near["parts"]) / len(near["parts"])
            near["h"] = min(near["h"], p["h"])
        else:
            rows.append({"c": p["c"], "h": p["h"], "parts": [p]})
    rows.sort(key=lambda r: r["c"])
    out = []
    for r in rows:
        parts = sorted(r["parts"], key=lambda p: p["u"])
        spans = [p["span"] for p in parts]
        out.append({
            "text": " ".join(p["text"] for p in parts),
            "y0": min(s[2] for s in spans), "y1": max(s[3] for s in spans),
            "spans": spans,
        })
    return out


_SEGMENT_GAP = 8.0  # % del ancho — un hueco mayor entre cajas de la misma fila separa tramos (columna en blanco entre nombre y precio)


def _segments(spans: list[tuple[float, float, float, float]]) -> list[dict]:
    """Junta las cajas de una fila en tramos contiguos por X; separa solo
    cuando el hueco supera `_SEGMENT_GAP`. Cada tramo lleva su propio y0/y1."""
    spans = sorted(spans)
    out = [list(spans[0])]
    for x0, x1, y0, y1 in spans[1:]:
        c = out[-1]
        if x0 - c[1] <= _SEGMENT_GAP:
            c[1], c[2], c[3] = max(c[1], x1), min(c[2], y0), max(c[3], y1)
        else:
            out.append([x0, x1, y0, y1])
    return [{"x0": c[0], "x1": c[1], "y0": c[2], "y1": c[3]} for c in out]


# ---------------------------------------------------------------- alineación

_MIN_SIM = 0.45      # parecido mínimo para aceptar una fila (sobre tokens con letras, sin tildes)
_PRICE_BONUS = 0.15  # bono si la fila (o la siguiente) trae el total del ítem — solo si ya se parecen
_PRICE_FLOOR = 0.30  # el bono nunca convierte en match una fila que no se parece al nombre
# Nombre ilegible (foto gastada, escrita a mano, otro alfabeto) pero la fila
# trae EXACTAMENTE el total del ítem (≥3 dígitos, para no anclar en un "1"):
# cuenta como un match débil — el orden global evita confundirlo con totales.
_PRICE_ONLY = 0.05


def _align(items: list[str], rows: list[dict], prices: Optional[list] = None) -> list[Optional[int]]:
    """Alineación GLOBAL monótona (programación dinámica tipo
    Needleman-Wunsch): asigna a cada ítem a lo más una fila, respetando el
    orden de lectura, maximizando Σ(parecido − umbral). Los ítems pueden
    quedar sin fila y las filas sin ítem (encabezados, 'cantidad × precio',
    descuentos, totales). A diferencia del emparejamiento codicioso, un ítem
    mal emparejado no arrastra a todos los siguientes, y los nombres
    repetidos caen en filas sucesivas."""
    n, m = len(items), len(rows)
    if not n or not m:
        return [None] * n
    names = [_letters(t) or _fold(t).strip() for t in items]
    texts = [_letters(r["text"]) for r in rows]
    nums = [_numbers(r["text"]) for r in rows]
    gain = [[0.0] * m for _ in range(n)]
    for i in range(n):
        pk = _price_keys(prices[i]) if prices and i < len(prices) else set()
        for j in range(m):
            s = _similarity(names[i], texts[j])
            if pk and s >= _PRICE_FLOOR and (pk & nums[j] or (j + 1 < m and pk & nums[j + 1] and not texts[j + 1])):
                s += _PRICE_BONUS
            elif pk and s < _MIN_SIM and any(len(k) >= 3 for k in pk & nums[j]):
                s = _MIN_SIM + _PRICE_ONLY  # nombre ilegible, pero la fila trae el total exacto
            gain[i][j] = s - _MIN_SIM
    best = [[0.0] * (m + 1) for _ in range(n + 1)]
    back = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        bi, bp, gi = best[i], best[i - 1], gain[i - 1]
        for j in range(m + 1):
            v, b = bp[j], 1                       # ítem i sin fila
            if j and bi[j - 1] > v:
                v, b = bi[j - 1], 2               # fila j sin ítem
            if j and gi[j - 1] > 0 and bp[j - 1] + gi[j - 1] > v:
                v, b = bp[j - 1] + gi[j - 1], 3   # ítem i ↔ fila j
            bi[j], back[i][j] = v, b
    out: list[Optional[int]] = [None] * n
    i, j = n, m
    while i > 0:
        b = back[i][j]
        if b == 3:
            out[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif b == 2:
            j -= 1
        else:
            i -= 1
    return out


def _locate(items: list[str], img: Image.Image, prices: Optional[list] = None) -> tuple[list[Optional[dict]], list[dict]]:
    """OCR + filas + alineación → por ítem, la fila asignada con sus tramos
    (o None), y todas las filas (para `ocr_lines`)."""
    w, h = img.size
    boxes = _ocr_boxes(img)
    keep = _script_filter(items)
    for b in boxes:
        b["text"] = keep(b["text"]).strip()
    rows = _rows([b for b in boxes if b["text"]], w, h)
    matches = []
    for k in _align(items, rows, prices):
        if k is None:
            matches.append(None)
        else:
            r = rows[k]
            matches.append({**r, "segments": _segments(r["spans"])})
    return matches, rows


@app.post("/position", response_model=PositionResponse)
def position(req: PositionRequest) -> PositionResponse:
    started = time.time()
    matches: list[Optional[dict]] = [None] * len(req.items)
    rows: list[dict] = []
    try:
        img = Image.open(io.BytesIO(base64.b64decode(req.image_b64))).convert("RGB")
        if max(img.size) > _MAX_SIDE:
            s = _MAX_SIDE / max(img.size)
            img = img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))), Image.LANCZOS)
        # Se espera el resultado solo mientras quede presupuesto: si no llega
        # (foto enorme, CPU lenta u otro pedido en curso) se responde sin
        # posiciones — el frontend cae al reparto parejo — en vez de dejar
        # que el backend agote sus 7 s. Si ni empezó, se cancela.
        fut = _worker.submit(_locate, req.items, img, req.prices)
        try:
            matches, rows = fut.result(timeout=max(0.0, _REQUEST_BUDGET_S - (time.time() - started)))
        except FuturesTimeout:
            fut.cancel()
            print(f"[ocr_position] presupuesto de {_REQUEST_BUDGET_S}s agotado — sin posiciones para este pedido")
    except Exception as exc:  # noqa: BLE001 — best-effort, nunca rompe la subida
        print(f"[ocr_position] error: {exc}")
    items_out: list[ItemPosition] = []
    for name, m in zip(req.items, matches):
        if m is None:
            items_out.append(ItemPosition(name=name))
            continue
        segs = m["segments"]
        items_out.append(ItemPosition(
            name=name,
            position_y=round((m["y0"] + m["y1"]) / 2, 1),
            bbox_y0=round(min(s["y0"] for s in segs), 1),
            bbox_y1=round(max(s["y1"] for s in segs), 1),
            segments=[Segment(x0=round(s["x0"], 1), x1=round(s["x1"], 1), y0=round(s["y0"], 1), y1=round(s["y1"], 1)) for s in segs],
        ))
    _clamp_adjacent_overlap(items_out)
    return PositionResponse(items=items_out, ocr_lines=[r["text"] for r in rows])


def _clamp_adjacent_overlap(items_out: list[ItemPosition]) -> None:
    """Ningún tramo puede pasar del punto medio hacia un tramo del ítem
    SIGUIENTE (mismo orden de lectura) que esté en la misma columna: una caja
    inflada nunca pisa al vecino. Solo se comparan tramos que se solapan en
    X — en una foto inclinada el precio de un ítem y el nombre del siguiente
    pueden compartir alturas sin tocarse, y recortarlos deformaría la banda.
    El bbox del ítem se recalcula desde sus tramos."""
    for i in range(len(items_out) - 1):
        a, b = items_out[i], items_out[i + 1]
        if a.position_y is None or b.position_y is None:
            continue
        for sa in a.segments:
            for sb in b.segments:
                if min(sa.x1, sb.x1) <= max(sa.x0, sb.x0) or sa.y1 <= sb.y0:
                    continue
                mid = round(((sa.y0 + sa.y1) / 2 + (sb.y0 + sb.y1) / 2) / 2, 1)
                sa.y1 = max(sa.y0, min(sa.y1, mid))
                sb.y0 = min(sb.y1, max(sb.y0, mid))
    for it in items_out:
        if it.segments:
            it.bbox_y0 = min(sg.y0 for sg in it.segments)
            it.bbox_y1 = max(sg.y1 for sg in it.segments)


@app.get("/health")
def health():
    return {"ok": True}
