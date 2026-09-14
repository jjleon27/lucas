#!/usr/bin/env python3
"""
Eval liviano del matching de posición (Tesseract) — reusa las fotos y el
ground-truth de nombres que YA existen en backend/tests/eval/ (no hace falta
etiquetar nada nuevo: los nombres de ítem del ground-truth de visión son
exactamente lo que este servicio recibe como `items`).

Dos cosas se miden, ambas gratis (sin LLM):

1. Matching (`count` = cuántos ítems logró emparejar con una línea real de la
   foto) — el mismo criterio que ya usa el código internamente para elegir
   entre escala/contraste/variantes. Sirve para verificar, antes de aceptar
   cualquier cambio a este servicio, que no bajó el % de match en el set de
   boletas rectas ya validado.

2. Geometría de las cajas de color (`check_geometry_quality`) — 100%
   ejecutable en código, sin que un humano mire cada foto: ¿algún segmento
   se solapa con el de otro ítem? (`overlaps`, debería ser SIEMPRE 0 después
   de `_clamp_adjacent_overlap` en app/main.py — esto es casi un assert de
   regresión de ese fix, no un umbral a ajustar) ¿algún alto de línea es
   absurdo comparado con la mediana de esa misma foto? (`height_outliers`,
   umbral K) ¿algún ítem quedó fuera de orden de lectura? (`order_violations`,
   tolerancia TOL). K y TOL se calibran corriendo esto mismo sobre el set
   oficial (ya validado a mano) — ver `--calibrate`.

USO (desde services/ocr_position/):

    PY=/Library/Frameworks/Python.framework/Versions/3.13/bin/python3
    $PY tests/eval_position.py
    $PY tests/eval_position.py --receipts otra_carpeta/  # ej. rotadas sintéticas
    $PY tests/eval_position.py --calibrate               # fija K/TOL desde el set oficial
    $PY tests/eval_position.py --receipts /Users/kako2/Downloads/Boletas \
        --expected backend/tests/eval/expected_geo_only \
        --alias backend/tests/eval/expected_geo_only/_alias.json
"""
from __future__ import annotations

import argparse
import base64
import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from app.main import position, PositionRequest  # noqa: E402

BACKEND_EVAL = HERE.parent.parent.parent / "backend" / "tests" / "eval"

# Calibrados 2026-09-14 corriendo --calibrate sobre las 9 fixtures oficiales
# (ver docs/PLAN_eval_split_v1.md §2.3) — no adivinados. Reflejan el margen
# real observado en boletas YA validadas a mano, con margen extra (no el
# límite exacto) para no marcar ruido normal como outlier.
DEFAULT_K = 1.95         # alto de línea > K * mediana de esa misma foto = outlier
DEFAULT_TOL = 0.5        # puntos porcentuales de tolerancia para orden de lectura


def _load_items(expected_path: Path) -> list[str]:
    data = json.loads(expected_path.read_text())
    rows = data.get("items", data) if isinstance(data, dict) else data
    return [row["name"] for row in rows]


def check_geometry_quality(items_out: list, k: float = DEFAULT_K, tol: float = DEFAULT_TOL) -> dict:
    """100% geométrico — solo usa los números que ya devuelve /position, no
    depende de mirar la foto. `items_out` es la lista de ItemPosition en el
    MISMO orden de lectura que se mandó como `items` (invariante ya
    garantizado por `_match_items_to_lines`, forward-only)."""
    matched = [it for it in items_out if it.position_y is not None
               and it.bbox_y0 is not None and it.bbox_y1 is not None]
    heights = [it.bbox_y1 - it.bbox_y0 for it in matched]
    median_h = statistics.median(heights) if heights else 0.0

    overlaps = 0
    for a, b in zip(matched, matched[1:]):
        if a.bbox_y1 > b.bbox_y0 + 1e-6:
            overlaps += 1

    height_outliers = sum(1 for h in heights if h <= 0 or (median_h > 0 and h > k * median_h))

    order_violations = 0
    prev_max = None
    for it in matched:
        if prev_max is not None and it.position_y < prev_max - tol:
            order_violations += 1
        prev_max = it.position_y if prev_max is None else max(prev_max, it.position_y)

    return {
        "match_rate": len(matched) / len(items_out) if items_out else 0.0,
        "n_matched": len(matched), "n_total": len(items_out),
        "overlaps": overlaps, "height_outliers": height_outliers,
        "order_violations": order_violations, "median_h": round(median_h, 2),
    }


def run(receipts_dir: Path, expected_dir: Path, alias: dict[str, str] | None = None,
        geometry: bool = False, k: float = DEFAULT_K, tol: float = DEFAULT_TOL) -> dict:
    total_matched = total_items = 0
    rows = []
    for receipt_path in sorted(receipts_dir.glob("*")):
        if receipt_path.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
            continue
        stem = alias.get(receipt_path.stem, receipt_path.stem) if alias else receipt_path.stem
        expected_path = expected_dir / f"{stem}.json"
        if not expected_path.exists():
            continue
        items = _load_items(expected_path)
        if not items:
            continue
        b64 = base64.b64encode(receipt_path.read_bytes()).decode("ascii")
        resp = position(PositionRequest(image_b64=b64, items=items))
        matched = sum(1 for it in resp.items if it.position_y is not None)
        total_matched += matched
        total_items += len(items)
        flag = "OK" if matched == len(items) else "!!"
        line = f"  [{flag}] {receipt_path.stem}: {matched}/{len(items)}"
        geo = None
        if geometry:
            geo = check_geometry_quality(resp.items, k=k, tol=tol)
            gflag = "GEO-OK" if geo["overlaps"] == 0 else "GEO-!!! OVERLAP"
            line += (f"   [{gflag}] outliers={geo['height_outliers']} "
                     f"order_viol={geo['order_violations']} median_h={geo['median_h']}")
        print(line)
        rows.append({"name": receipt_path.stem, "matched": matched, "total": len(items), "geo": geo})
    return {"total_matched": total_matched, "total_items": total_items, "rows": rows}


def calibrate(receipts_dir: Path, expected_dir: Path) -> None:
    """Corre check_geometry_quality (sin capar K/TOL) sobre el set oficial ya
    validado a mano, para fijar K/TOL con datos reales en vez de adivinarlos
    (ver docs/PLAN_eval_split_v1.md §2.3)."""
    print("--- calibración: alturas y order_violations crudos en el set oficial ---")
    all_ratios = []
    for receipt_path in sorted(receipts_dir.glob("*")):
        if receipt_path.suffix.lower() not in (".jpg", ".jpeg", ".png"):
            continue
        expected_path = expected_dir / f"{receipt_path.stem}.json"
        if not expected_path.exists():
            continue
        items = _load_items(expected_path)
        if not items:
            continue
        b64 = base64.b64encode(receipt_path.read_bytes()).decode("ascii")
        resp = position(PositionRequest(image_b64=b64, items=items))
        matched = [it for it in resp.items if it.position_y is not None
                   and it.bbox_y0 is not None and it.bbox_y1 is not None]
        heights = [it.bbox_y1 - it.bbox_y0 for it in matched]
        if not heights:
            continue
        med = statistics.median(heights)
        ratios = [h / med for h in heights if med > 0]
        all_ratios.extend(ratios)
        print(f"  {receipt_path.stem:26s} median_h={med:.2f}  max_ratio={max(ratios):.2f}")
    if all_ratios:
        all_ratios.sort()
        p95 = all_ratios[int(len(all_ratios) * 0.95)]
        print(f"\n  max ratio observado en TODO el set oficial: {max(all_ratios):.2f}")
        print(f"  p95 ratio: {p95:.2f}")
        print(f"  K sugerido (max observado + margen ~20%): {max(all_ratios) * 1.2:.2f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipts", default=str(BACKEND_EVAL / "receipts"))
    parser.add_argument("--expected", default=str(BACKEND_EVAL / "expected"))
    parser.add_argument("--alias", default=None, help="JSON {stem_foto: stem_fixture} para reusar ground-truth de otra foto del mismo local")
    parser.add_argument("--geometry", action="store_true", help="además del matching, chequear overlaps/outliers/orden")
    parser.add_argument("--calibrate", action="store_true", help="medir K/TOL desde el set oficial en vez de correr el eval normal")
    parser.add_argument("--k", type=float, default=DEFAULT_K)
    parser.add_argument("--tol", type=float, default=DEFAULT_TOL)
    args = parser.parse_args()

    if args.calibrate:
        calibrate(Path(args.receipts), Path(args.expected))
        sys.exit(0)

    alias = json.loads(Path(args.alias).read_text()) if args.alias else None
    result = run(Path(args.receipts), Path(args.expected), alias=alias,
                 geometry=args.geometry, k=args.k, tol=args.tol)
    matched, total = result["total_matched"], result["total_items"]
    pct = (matched / total * 100) if total else 0.0
    print(f"\nTOTAL: {matched}/{total} ({pct:.1f}%)")
    if args.geometry:
        total_overlaps = sum((r["geo"] or {}).get("overlaps", 0) for r in result["rows"])
        print(f"OVERLAPS TOTALES: {total_overlaps}" + ("  <<< HAY QUE INVESTIGAR" if total_overlaps else "  (ok)"))
