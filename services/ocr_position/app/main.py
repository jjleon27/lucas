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


class Segment(BaseModel):
    x0: float
    x1: float
    # Alto REAL (0-100 = % del alto de la foto) de la línea Tesseract de la
    # que vino ESTE tramo en particular — no el del ítem completo. Nombre y
    # precio de una misma fila física suelen caer en líneas Tesseract con
    # alturas ligeramente distintas (fuentes con descendentes, boleta
    # doblada/arrugada); si el frontend usara un solo alto compartido por
    # ítem, el tramo del precio podría dibujarse desalineado del texto real.
    y0: float
    y1: float


class ItemPosition(BaseModel):
    name: str
    position_y: Optional[float] = None  # 0-100 (centro vertical), o null si no hay match confiable
    # Alto REAL de la línea de texto emparejada (0-100 = % del alto de la foto).
    # Se mantiene como fallback/compat — cubre TODOS los segmentos del ítem
    # (min/max); el frontend debe preferir el y0/y1 propio de cada `Segment`.
    bbox_y0: Optional[float] = None
    bbox_y1: Optional[float] = None
    # Uno o más tramos horizontales REALES (0-100 = % del ancho de la foto) —
    # nombre+cantidad por un lado, precio por otro, cuando hay un hueco en
    # blanco grande entre columnas (no un solo tramo que sombrearía también
    # ese hueco, como una barra sólida). Permite que la banda de color en el
    # frontend se dibuje solo sobre el texto real, en tantos bloques como
    # haga falta — cada uno con su propia altura (ver `Segment.y0/y1`).
    segments: list[Segment] = []


class PositionResponse(BaseModel):
    items: list[ItemPosition]
    # Texto crudo (en orden de lectura) de la variante de escala/contraste
    # que ganó el matching — se usa en backend/app/ocr.py para verificar,
    # gratis (ya se pagó este OCR igual), si un nombre/precio que dijo el
    # modelo de visión realmente aparece en la foto o probablemente lo
    # inventó. No afecta nada del posicionamiento — es solo un subproducto
    # que ya se calculaba y se estaba descartando.
    ocr_lines: list[str] = []


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
            "word_spans": [],  # (x0%, x1%) de CADA palabra — para separar en bloques (ver _row_segments)
        })
        entry["words"].append(txt)
        entry["top"] = min(entry["top"], top)
        entry["bottom"] = max(entry["bottom"], top + height)
        entry["left"] = min(entry["left"], left)
        entry["right"] = max(entry["right"], left + width)
        entry["word_spans"].append((left / w * 100, (left + width) / w * 100))
    ordered = sorted(lines.values(), key=lambda e: e["top"])
    return [
        {
            "text": " ".join(e["words"]),
            "y0": e["top"] / h * 100, "y1": e["bottom"] / h * 100,
            "x0": e["left"] / w * 100, "x1": e["right"] / w * 100,
            "word_spans": e["word_spans"],
        }
        for e in ordered
    ]


_ROW_OVERLAP_MIN = 0.6  # fracción mínima de traslape vertical para considerar "misma fila"
_ROW_SEARCH_WINDOW = 6  # cuántas líneas mirar a cada lado (ordenadas por altura)


_WORD_CLUSTER_GAP = 8.0  # % del ancho de la foto — huecos más chicos se fusionan (espacio normal entre palabras); más grandes quedan separados (columna en blanco entre nombre y precio)


def _cluster_word_spans(word_spans: list[tuple[float, float, float, float]], gap: float) -> list[dict]:
    """Junta palabras (x0,x1,y0,y1 — cada una con el alto REAL de la línea
    Tesseract de la que vino) en bloques contiguos por X — separa en
    bloques DISTINTOS solo cuando el hueco entre una palabra y la siguiente
    supera `gap`. Medido en boletas reales: el espacio normal entre
    palabras de un mismo bloque es ~3-5% del ancho de la foto; el hueco en
    blanco entre la columna de nombre y la de precio es ~40% — hay margen
    de sobra para separarlos con un solo umbral fijo, sin depender del
    idioma/formato.

    Cada bloque resultante lleva su PROPIO y0/y1 (min/max de las líneas que
    aportaron sus palabras) — no el de la línea base del ítem. Necesario
    porque nombre y precio de una misma fila física suelen venir de líneas
    Tesseract con alturas ligeramente distintas (por descendentes de
    fuente, o mucho más marcado cuando la boleta está doblada/arrugada);
    si todos los bloques comparten un solo y0/y1, el del precio puede
    quedar dibujado en la altura del nombre, desalineado del texto real."""
    if not word_spans:
        return []
    spans = sorted(word_spans)
    clusters = [[spans[0][0], spans[0][1], spans[0][2], spans[0][3]]]
    for x0, x1, y0, y1 in spans[1:]:
        if x0 - clusters[-1][1] <= gap:
            c = clusters[-1]
            c[1] = max(c[1], x1)
            c[2] = min(c[2], y0)
            c[3] = max(c[3], y1)
        else:
            clusters.append([x0, x1, y0, y1])
    return [{"x0": c[0], "x1": c[1], "y0": c[2], "y1": c[3]} for c in clusters]


def _row_segments(lines: list[dict], idx: int) -> dict:
    """Tesseract a veces separa la cantidad y/o el precio del nombre del
    ítem en bloques de texto DISTINTOS (líneas separadas) cuando hay mucho
    espacio en blanco entre columnas — el nombre matchea bien, pero antes
    la caja quedaba angosta, sin la cantidad ni el valor. Se junta el texto
    de toda la fila física: se busca, cerca en el orden de lectura,
    cualquier otra línea que comparta CASI LA MISMA ALTURA en la foto
    (traslape vertical ≥60%).

    A diferencia de un solo bbox ancho (que sombrearía TAMBIÉN el hueco en
    blanco de por medio, como una barra sólida gigante), se agrupan las
    palabras individuales en BLOQUES separados por hueco real
    (`_cluster_word_spans`) — así la banda de color en el frontend sombrea
    literalmente el texto (nombre+cantidad por un lado, precio por otro),
    no el espacio vacío entre columnas. Es geométrico — no depende del
    idioma, mayúsculas, largo del nombre/valor, ni de si hay descuento u
    otro formato — así generaliza a cualquier boleta sin reglas por caso."""
    base = lines[idx]
    word_spans = [(x0, x1, base["y0"], base["y1"]) for x0, x1 in base["word_spans"]]
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
            word_spans.extend((x0, x1, other["y0"], other["y1"]) for x0, x1 in other["word_spans"])
    segments = _cluster_word_spans(word_spans, _WORD_CLUSTER_GAP)
    return {**base, "segments": segments}


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
            results.append(_row_segments(lines, best_idx))
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


_SKEW_MIN_DEGREES = 3.0  # bajo esto no vale la pena enderezar — ruido normal de detección en fotos ya derechas


def _hough_skew_angle(img: Image.Image, max_angle: float = 25.0) -> float:
    """Estimación GRUESA del ángulo (grados) en que el texto de la foto está
    inclinado respecto al marco — 0 si está derecha. Usa líneas de Hough
    sobre bordes (Canny + HoughLinesP), NO Tesseract (gratis, corre siempre
    que hace falta sin sumar otra llamada a OCR): cada línea recta detectada
    (borde de renglón de texto, línea divisoria de la boleta, etc.) aporta
    su propio ángulo; se toma la MEDIANA de las que caen cerca de la
    horizontal — robusta a outliers (un borde de mesa en diagonal, un logo)
    porque en una boleta real la enorme mayoría de líneas rectas son
    renglones de texto, todos alineados entre sí. Precisa a ~1-3°, no más
    (ver `_detect_skew_angle` para el refinamiento fino)."""
    import cv2
    import numpy as np
    w, h = img.size
    scale = min(1.0, 1000 / max(w, h))
    small = img.resize((max(1, round(w * scale)), max(1, round(h * scale)))) if scale < 1.0 else img
    gray = cv2.cvtColor(np.array(small.convert("RGB")), cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    lines = cv2.HoughLinesP(
        edges, 1, np.pi / 360, threshold=80,
        minLineLength=max(1, w * scale * 0.05), maxLineGap=8,
    )
    if lines is None:
        return 0.0
    angles = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        if x2 == x1:
            continue
        angle = float(np.degrees(np.arctan2(y2 - y1, x2 - x1)))
        if angle > 90:
            angle -= 180
        if angle < -90:
            angle += 180
        if abs(angle) <= max_angle:
            angles.append(angle)
    return float(np.median(angles)) if angles else 0.0


def _refine_skew_angle(img: Image.Image, coarse: float, span: float = 2.5, step: float = 0.1) -> float:
    """Afina la estimación gruesa de Hough con projection profile — para
    cada ángulo candidato en una ventana ANGOSTA alrededor de `coarse`,
    rota una versión binarizada de la foto y mide qué tan "picuda" (alta
    varianza) queda la suma de píxeles de texto por fila: con texto
    perfectamente horizontal, cada renglón cae en una banda angosta de
    alta densidad separada por blancos, maximizando la varianza. Es el
    método más citado para esto, pero probado en vivo en boletas reales
    queda dominado por la SILUETA del recibo (no el texto) a partir de
    ~8° de búsqueda — por eso acá se usa solo para REFINAR una ventana
    angosta (±2.5° por defecto) alrededor de lo que ya entregó Hough,
    donde ese problema no aparece, en vez de para la búsqueda completa."""
    import cv2
    import numpy as np
    w, h = img.size
    scale = min(1.0, 700 / max(w, h))
    small = img.resize((max(1, round(w * scale)), max(1, round(h * scale)))) if scale < 1.0 else img
    gray = cv2.cvtColor(np.array(small.convert("RGB")), cv2.COLOR_RGB2GRAY)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    bh, bw = binary.shape
    diag = int(np.ceil(np.hypot(bw, bh)))
    canvas = np.zeros((diag, diag), dtype=np.uint8)
    oy, ox = (diag - bh) // 2, (diag - bw) // 2
    canvas[oy:oy + bh, ox:ox + bw] = binary

    def score(angle: float) -> float:
        M = cv2.getRotationMatrix2D((diag / 2, diag / 2), angle, 1.0)
        rotated = cv2.warpAffine(canvas, M, (diag, diag), flags=cv2.INTER_NEAREST, borderValue=0)
        profile = rotated.sum(axis=1).astype(np.float64)
        return float(np.var(profile))

    candidates = np.arange(coarse - span, coarse + span + step, step)
    scores = [score(a) for a in candidates]
    return float(candidates[int(np.argmax(scores))])


def _detect_skew_angle(img: Image.Image) -> float:
    """Ángulo (grados) en que el texto de la foto está inclinado respecto
    al marco — 0 si está derecha o no se detectó nada confiable. Combina
    las dos técnicas anteriores: Hough para una estimación gruesa robusta
    (inmune a la silueta del recibo, pero con ~1-3° de error — insuficiente
    en boletas con mucho texto apretado, donde ese margen ya tuerce el
    emparejamiento), refinada por projection profile en una ventana angosta
    alrededor de esa estimación (ahí SÍ es preciso, porque a esa escala no
    entra en juego el problema de la silueta). Nunca debe romper el resto
    del servicio — 0.0 si cv2 no está disponible o algo falla."""
    try:
        coarse = _hough_skew_angle(img)
        if abs(coarse) < 0.5:  # ya está prácticamente derecha — no vale la pena refinar
            return coarse
        return _refine_skew_angle(img, coarse)
    except Exception:
        return 0.0


def _rotate_expand(img: Image.Image, angle: float):
    """Rota la imagen ENTERA sin recortar contenido (equivalente a
    `Image.rotate(angle, expand=True)` de PIL) pero calculado con cv2 para
    quedarse con la matriz afín exacta — se necesita después para deshacer
    la rotación de las coordenadas que devuelve Tesseract sobre esta imagen
    (ver `_unrotate_lines`). Devuelve la imagen rotada, la matriz afín
    ORIGINAL→ROTADA, y los tamaños (ancho, alto) de ambas."""
    import cv2
    import numpy as np
    arr = np.array(img.convert("RGB"))
    h, w = arr.shape[:2]
    center = (w / 2.0, h / 2.0)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    cos, sin = abs(M[0, 0]), abs(M[0, 1])
    new_w = int(round(h * sin + w * cos))
    new_h = int(round(h * cos + w * sin))
    M[0, 2] += new_w / 2.0 - center[0]
    M[1, 2] += new_h / 2.0 - center[1]
    rotated = cv2.warpAffine(arr, M, (new_w, new_h), borderValue=(255, 255, 255))
    return Image.fromarray(rotated), M, (w, h), (new_w, new_h)


def _unrotate_lines(lines: list[dict], M, orig_size: tuple[int, int], rot_size: tuple[int, int]) -> list[dict]:
    """Convierte x0/x1/y0/y1/word_spans de cada línea — calculados por
    Tesseract sobre la imagen ENDEREZADA para leer mejor — de vuelta a %
    de la foto ORIGINAL, que es el marco de referencia que espera el resto
    del pipeline (y el frontend, que dibuja sobre la foto sin rotar).
    Deshace la rotación aplicando la transformación afín inversa a las 4
    esquinas de cada caja y tomando el rectángulo alineado a los ejes que
    las contiene. Ese rectángulo queda un poco más ancho que el texto real
    (inevitable: un rectángulo sin rotar no puede calzar exacto sobre texto
    en ángulo) — mismo trade-off que ya acepta el resto del sistema al
    dibujar recuadros sin rotación."""
    import cv2
    import numpy as np
    Minv = cv2.invertAffineTransform(M)
    ow, oh = orig_size
    rw, rh = rot_size

    def unrotate_box(x0pct, x1pct, y0pct, y1pct):
        corners = np.array([
            [x0pct / 100 * rw, y0pct / 100 * rh], [x1pct / 100 * rw, y0pct / 100 * rh],
            [x0pct / 100 * rw, y1pct / 100 * rh], [x1pct / 100 * rw, y1pct / 100 * rh],
        ])
        pts = np.hstack([corners, np.ones((4, 1))]) @ Minv.T
        return (
            pts[:, 0].min() / ow * 100, pts[:, 0].max() / ow * 100,
            pts[:, 1].min() / oh * 100, pts[:, 1].max() / oh * 100,
        )

    out = []
    for line in lines:
        x0, x1, y0, y1 = unrotate_box(line["x0"], line["x1"], line["y0"], line["y1"])
        word_spans = []
        for wx0, wx1 in line["word_spans"]:
            wx0o, wx1o, _, _ = unrotate_box(wx0, wx1, line["y0"], line["y1"])
            word_spans.append((wx0o, wx1o))
        out.append({**line, "x0": x0, "x1": x1, "y0": y0, "y1": y1, "word_spans": word_spans})
    return out


def _run_scale(
    base_img: Image.Image, items: list[str], scale: float,
    transform: Optional[tuple] = None,
) -> tuple[list[Optional[dict]], int, list[dict]]:
    w, h = base_img.size
    im = base_img if scale == 1.0 else base_img.resize(
        (max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS,
    )
    lines = _tesseract_lines(im)
    if transform is not None:
        M, orig_size = transform
        lines = _unrotate_lines(lines, M, orig_size, im.size)
    matches = _match_items_to_lines(items, lines)
    return matches, sum(1 for m in matches if m is not None), lines


def _best_of(
    results: list[tuple[list[Optional[dict]], int, list[dict]]],
    start: tuple[list[Optional[dict]], int, list[dict]],
) -> tuple[list[Optional[dict]], int, list[dict]]:
    """Se queda con el mejor resultado — mismo criterio (`>` estricto,
    primero gana en empate) que si se hubiera recorrido `results` en orden
    de forma secuencial, sin importar en qué orden TERMINARON de calcularse
    (paralelo o no) — así paralelizar nunca cambia cuál gana. Arrastra
    también las líneas de OCR de la variante ganadora (`ocr_lines` en la
    respuesta)."""
    matches, count, lines = start
    for m, c, l in results:
        if c > count:
            count, matches, lines = c, m, l
    return matches, count, lines


def _match_best_effort(items: list[str], img: Image.Image) -> tuple[list[Optional[dict]], list[dict]]:
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
    pasa de la SUMA de todas las llamadas a la más LENTA de ellas.

    También en ese mismo caso difícil (nunca en el camino rápido) se prueba
    una variante con la foto ENDEREZADA: una foto sacada en ángulo (no solo
    boleta doblada — el cuadro entero inclinado) hace que Tesseract agrupe
    mal las líneas de texto (su propio análisis de layout asume texto
    aprox. horizontal), lo que rompe tanto el armado de "misma fila
    física" (`_row_segments`) como el orden de lectura que asume el
    emparejamiento — ninguna heurística downstream puede recuperar texto
    que Tesseract ya agrupó mal en el paso anterior. Se detecta el ángulo
    (`_detect_skew_angle`, sin costo de OCR) y, si supera el umbral mínimo,
    se prueba TAMBIÉN esa variante — compite en el mismo torneo que las
    demás (`_best_of`, gana solo si empareja estrictamente más ítems),
    nunca se asume que enderezar ayuda."""
    std_img = _standard_variant(img)
    w, h = std_img.size
    scales = _candidate_scales(max(w, h))

    matches, count, lines = _run_scale(std_img, items, scales[0])
    if count == len(items) or len(scales) == 1:
        return matches, lines

    enhanced = _clahe_variant(img)

    skew_angle = _detect_skew_angle(std_img)
    deskew_transform = None
    deskewed_std = deskewed_enhanced = None
    if abs(skew_angle) >= _SKEW_MIN_DEGREES:
        deskewed_std, M, orig_size, _ = _rotate_expand(std_img, skew_angle)
        deskew_transform = (M, orig_size)
        if enhanced is not None:
            deskewed_enhanced, _, _, _ = _rotate_expand(enhanced, skew_angle)

    with ThreadPoolExecutor(max_workers=8) as ex:
        std_futures = [ex.submit(_run_scale, std_img, items, s) for s in scales[1:]]
        clahe_futures = [ex.submit(_run_scale, enhanced, items, s) for s in scales] if enhanced is not None else []
        deskew_futures = []
        if deskewed_std is not None:
            deskew_futures.append(ex.submit(_run_scale, deskewed_std, items, 1.0, deskew_transform))
        if deskewed_enhanced is not None:
            deskew_futures.append(ex.submit(_run_scale, deskewed_enhanced, items, 1.0, deskew_transform))

        matches, count, lines = _best_of([f.result() for f in std_futures], (matches, count, lines))
        if clahe_futures:
            clahe_matches, clahe_count, clahe_lines = _best_of([f.result() for f in clahe_futures[1:]], clahe_futures[0].result())
            if clahe_count > count:  # CLAHE gana SOLO si mejora estrictamente — misma regla de siempre
                matches, count, lines = clahe_matches, clahe_count, clahe_lines
        if deskew_futures:
            dm, dc, dl = deskew_futures[0].result()
            for f in deskew_futures[1:]:
                m2, c2, l2 = f.result()
                if c2 > dc:
                    dm, dc, dl = m2, c2, l2
            if dc > count:  # enderezar gana SOLO si mejora estrictamente — misma regla de siempre
                matches, count, lines = dm, dc, dl
    return matches, lines


@app.post("/position", response_model=PositionResponse)
def position(req: PositionRequest) -> PositionResponse:
    matches: list[Optional[dict]]
    lines: list[dict] = []
    try:
        img_bytes = base64.b64decode(req.image_b64)
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        matches, lines = _match_best_effort(req.items, img)
    except Exception:
        matches = [None] * len(req.items)
    items_out: list[ItemPosition] = []
    for name, m in zip(req.items, matches):
        if m is None:
            items_out.append(ItemPosition(name=name))
        else:
            segs = m["segments"]
            # bbox_y0/y1 a nivel ítem = rango que cubre TODOS sus segmentos —
            # se mantiene solo como fallback/compat; el frontend debe preferir
            # el y0/y1 propio de cada segmento (ver `Segment`).
            bbox_y0 = min((s["y0"] for s in segs), default=m["y0"])
            bbox_y1 = max((s["y1"] for s in segs), default=m["y1"])
            items_out.append(ItemPosition(
                name=name,
                position_y=round((m["y0"] + m["y1"]) / 2, 1),
                bbox_y0=round(bbox_y0, 1), bbox_y1=round(bbox_y1, 1),
                segments=[
                    Segment(x0=round(s["x0"], 1), x1=round(s["x1"], 1), y0=round(s["y0"], 1), y1=round(s["y1"], 1))
                    for s in segs
                ],
            ))
    _clamp_adjacent_overlap(items_out)
    return PositionResponse(items=items_out, ocr_lines=[l["text"] for l in lines])


def _clamp_adjacent_overlap(items_out: list[ItemPosition]) -> None:
    """Tesseract a veces devuelve la altura de UNA línea inflada — casi el
    doble de lo normal — cuando esa línea puntual está borrosa/con poco
    contraste y su propio análisis de layout fusiona la fila con parte de
    la de arriba o abajo (visto en vivo: una boleta con mediana de altura
    de línea 2.7% dio una línea puntual de 4.5%, justo la que se veía
    invadiendo al ítem vecino). No es un problema del agrupado de fila
    (`_row_segments`) ni de escala/contraste — es la caja que Tesseract
    mismo calculó para esa línea en particular.

    En vez de adivinar cuál de los dos bordes de esa línea está mal (no
    hay forma de saberlo de antemano, y varía de foto en foto), se acota
    GEOMÉTRICAMENTE después de emparejar: ningún ítem puede extender su
    bbox/segmentos más allá del punto medio hacia el ítem SIGUIENTE en el
    orden de lectura (mismo orden que ya usa `_match_items_to_lines`,
    siempre hacia adelante) — así, sea cual sea la causa real de una caja
    inflada, nunca termina pisando el espacio del ítem de al lado. Solo
    actúa cuando hay traslape real entre dos ítems consecutivos con match;
    ítems sin match (bbox None) o ya separados quedan intactos."""
    for i in range(len(items_out) - 1):
        a, b = items_out[i], items_out[i + 1]
        if a.position_y is None or b.position_y is None:
            continue
        if a.bbox_y1 is None or b.bbox_y0 is None or a.bbox_y1 <= b.bbox_y0:
            continue  # sin traslape, no hay nada que acotar
        midpoint = round((a.position_y + b.position_y) / 2, 1)
        if a.bbox_y1 > midpoint:
            a.bbox_y1 = midpoint
            for seg in a.segments:
                seg.y1 = min(seg.y1, midpoint)
                seg.y0 = min(seg.y0, seg.y1)
        if b.bbox_y0 < midpoint:
            b.bbox_y0 = midpoint
            for seg in b.segments:
                seg.y0 = max(seg.y0, midpoint)
                seg.y1 = max(seg.y1, seg.y0)


@app.get("/health")
def health():
    return {"ok": True}
