#!/usr/bin/env python3
"""
Eval liviano del matching de posición (Tesseract) — reusa las fotos y el
ground-truth de nombres que YA existen en backend/tests/eval/ (no hace falta
etiquetar nada nuevo: los nombres de ítem del ground-truth de visión son
exactamente lo que este servicio recibe como `items`).

No mide coordenadas exactas — el criterio es el MISMO que ya usa el código
internamente para elegir entre escala/contraste/variantes (`count` = cuántos
ítems logró emparejar con una línea real de la foto). Sirve para verificar,
antes de aceptar cualquier cambio a este servicio, que no bajó el % de match
en el set de boletas rectas ya validado.

USO (desde services/ocr_position/):

    PY=/Library/Frameworks/Python.framework/Versions/3.13/bin/python3
    $PY tests/eval_position.py
    $PY tests/eval_position.py --receipts otra_carpeta/  # ej. rotadas sintéticas
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from app.main import position, PositionRequest  # noqa: E402

BACKEND_EVAL = HERE.parent.parent.parent / "backend" / "tests" / "eval"


def _load_items(expected_path: Path) -> list[str]:
    data = json.loads(expected_path.read_text())
    rows = data.get("items", data) if isinstance(data, dict) else data
    return [row["name"] for row in rows]


def run(receipts_dir: Path, expected_dir: Path) -> tuple[int, int]:
    total_matched = total_items = 0
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
        matched = sum(1 for it in resp.items if it.position_y is not None)
        total_matched += matched
        total_items += len(items)
        flag = "OK" if matched == len(items) else "!!"
        print(f"  [{flag}] {receipt_path.stem}: {matched}/{len(items)}")
    return total_matched, total_items


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipts", default=str(BACKEND_EVAL / "receipts"))
    parser.add_argument("--expected", default=str(BACKEND_EVAL / "expected"))
    args = parser.parse_args()
    matched, total = run(Path(args.receipts), Path(args.expected))
    pct = (matched / total * 100) if total else 0.0
    print(f"\nTOTAL: {matched}/{total} ({pct:.1f}%)")
