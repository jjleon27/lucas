"""Benchmark del servicio de posición: ítems UBICADOS, ítems VERIFICADOS y
latencia por set (main, rotadas sintéticas, holdout).

  ubicado    = el servicio devolvió una banda para el ítem.
  verificado = esa banda (bbox_y0..bbox_y1 ± ½ alto) contiene una caja de
               texto que trae el `line_total` del ítem (en dígitos, sin
               separadores). Las cajas de referencia son las MISMAS para
               cualquier versión del servicio: unión de RapidOCR (foto
               preparada) y Tesseract a 2000 px (si está instalado), en caché.
               Es una aproximación (no hay coordenadas etiquetadas a mano).

Las fotos se preparan igual que el backend (`_prep_for_position`): EXIF,
lado máximo 2000 px, JPEG q95. Holdout: correr antes
`python3 backend/tests/eval/fetch_holdout.py`.

    python3 services/ocr_position/tests/bench_position.py [--sets main,rot,hold]
        [--service DIR]   # otra copia del servicio (p. ej. la versión anterior) para comparar
        [--prices]        # mandar también el line_total de cada ítem (campo opcional `prices`)
        [--repeat N]      # latencia = mínimo de N corridas por boleta (si la máquina está compartida)
"""
import argparse
import base64
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image, ImageEnhance, ImageOps

R = Path(__file__).resolve().parents[3]
E = R / "backend/tests/eval"
SETS = {
    "main": (E / "receipts", E / "expected"),
    "rot": (E / "receipts_synthetic_rotated", E / "expected_synthetic_rotated"),
    "hold": (E / "receipts_holdout", E / "expected_holdout"),
}

ap = argparse.ArgumentParser()
ap.add_argument("--sets", default="main,rot,hold")
ap.add_argument("--service", default=str(R / "services/ocr_position"))
ap.add_argument("--prices", action="store_true")
ap.add_argument("--cache", default=os.path.expanduser("~/.cache/ocr_position_bench"))
ap.add_argument("--only", default="", help="subcadena del nombre de archivo")
ap.add_argument("--repeat", type=int, default=1, help="repetir cada boleta N veces y quedarse con el MENOR tiempo (aísla ruido de otros procesos en la máquina)")
args = ap.parse_args()
sys.path.insert(0, args.service)
import app.main as M  # noqa: E402

CACHE = Path(args.cache)
CACHE.mkdir(parents=True, exist_ok=True)


def prep(path: Path) -> bytes:
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    w, h = img.size
    s = min(1.0, 2000 / max(w, h), (8_000_000 / (w * h)) ** 0.5)
    if s < 1.0:
        img = img.resize((round(w * s), round(h * s)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def nums(text: str) -> set:
    out = set()
    for m in re.finditer(r"\d[\d.,' ]*\d|\d", text):
        d = re.sub(r"\D", "", m.group())
        out.update({d, d.lstrip("0") or "0"})
        for part in m.group().split():
            dd = re.sub(r"\D", "", part)
            if dd:
                out.add(dd)
    return out


def price_keys(v) -> set:
    a = abs(float(v))
    if a == 0:
        return {"0"}
    ks = {f"{a:.2f}".replace(".", "")}
    if a == int(a):
        ks.add(str(int(a)))
    return ks


_ref_ocr = None


def reference(data: bytes) -> list:
    """[(nums, y_centro_%)] — unión RapidOCR + Tesseract@2000px, cacheada."""
    key = hashlib.sha1(data).hexdigest()
    f = CACHE / f"{key}.json"
    if f.exists():
        return [(set(n), c) for n, c in json.loads(f.read_text())]
    global _ref_ocr
    import numpy as np
    img = Image.open(io.BytesIO(data)).convert("RGB")
    W, H = img.size
    ref = []
    if _ref_ocr is None:
        from rapidocr import RapidOCR
        _ref_ocr = RapidOCR(params={"Global.log_level": "error"})
    r = _ref_ocr(np.array(img)[:, :, ::-1])
    for quad, t in zip(r.boxes if r.boxes is not None else [], r.txts or []):
        ys = [p[1] for p in quad]
        ref.append((sorted(nums(t)), float(min(ys) + max(ys)) / 2 / H * 100))
    try:
        s = 2000 / max(W, H)
        g = img.resize((round(W * s), round(H * s)), Image.LANCZOS).convert("L")
        g = ImageEnhance.Sharpness(ImageEnhance.Contrast(g).enhance(1.8)).enhance(2.0)
        buf = io.BytesIO()
        g.save(buf, "PNG")
        tsv = subprocess.run(["tesseract", "stdin", "stdout", "-l", "spa", "--oem", "1", "tsv"], input=buf.getvalue(),
                             capture_output=True, env=dict(os.environ, OMP_THREAD_LIMIT="4"), timeout=60).stdout.decode("utf8", "replace")
        H2 = g.size[1]
        for ln in tsv.splitlines()[1:]:
            c = ln.split("\t")
            if len(c) >= 12 and c[0] == "5" and c[11].strip():
                t, hh = int(c[7]), int(c[9])
                ref.append((sorted(nums(c[11])), (t + hh / 2) / H2 * 100))
    except Exception:
        pass  # sin tesseract: referencia solo RapidOCR
    f.write_text(json.dumps(ref))
    return [(set(n), c) for n, c in ref]


def load_items(exp: Path) -> list:
    d = json.loads(exp.read_text())
    rows = d.get("items", d) if isinstance(d, dict) else d
    return [r for r in rows if r.get("name") and r.get("line_total") is not None]  # igual que la referencia de la auditoría


M.position(M.PositionRequest(image_b64=base64.b64encode(prep(next(iter(sorted(SETS["main"][0].glob("*.jpg")))))).decode(), items=["x"]))  # calentamiento (modelos)
summary = []
for name in args.sets.split(","):
    rd, ed = SETS[name]
    n_items = n_loc = n_ver = n_infl = 0
    lat = []
    for img in sorted(rd.glob("*")):
        exp = ed / (img.stem + ".json")
        if img.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp") or not exp.exists() or args.only not in img.stem:
            continue
        items = load_items(exp)
        if not items:
            continue
        data = prep(img)
        ref = reference(data)
        req = dict(image_b64=base64.b64encode(data).decode(), items=[i["name"] for i in items])
        if args.prices:
            req["prices"] = [i.get("line_total") for i in items]
        dt = float("inf")
        for _ in range(max(1, args.repeat)):
            t0 = time.time()
            r = M.position(M.PositionRequest(**req))
            dt = min(dt, time.time() - t0)
        lat.append(dt)
        loc = ver = 0
        for it, out in zip(items, r.items):
            if out.position_y is None:
                continue
            loc += 1
            pk = price_keys(it["line_total"])
            hgt = max(out.bbox_y1 - out.bbox_y0, 0.5)
            lo, hi = out.bbox_y0 - 0.5 * hgt, out.bbox_y1 + 0.5 * hgt
            ver += any(lo <= c <= hi and (pk & ns) for ns, c in ref)
        # Banda inflada: alto de la banda > 2× la mediana de las bandas de esa boleta.
        hs = sorted(o.bbox_y1 - o.bbox_y0 for o in r.items if o.position_y is not None)
        infl = sum(1 for x in hs if hs and x > 2 * hs[(len(hs) - 1) // 2])
        n_items += len(items); n_loc += loc; n_ver += ver; n_infl += infl
        print(f"  {name:4} {img.stem[:34]:34} n {len(items):3} loc {loc:3} ver {ver:3} {dt:5.2f}s infl {infl}", flush=True)
    if not lat:
        print(f"== {name}: sin fotos")
        continue
    lat.sort()
    line = (f"== {name}: items {n_items}  located {100 * n_loc / n_items:.1f}%  verified {100 * n_ver / n_items:.1f}%  "
            f"median {lat[len(lat) // 2]:.2f}s  worst {lat[-1]:.2f}s  bandas>2x {n_infl}")
    print(line, flush=True)
    summary.append(line)
print("\n".join(summary))
