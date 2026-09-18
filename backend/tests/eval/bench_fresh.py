"""
Benchmark "desde cero" (2026-09-18, pedido explícito: "como si este proyecto
no existiera") — 3 candidatos investigados en GitHub/papers/docs oficiales
SIN mirar el resto de este pipeline, probando otros PROVEEDORES de modelo
por completo, no solo otro prompt para el mismo modelo:

  GEMINI    = Google Gemini (Interactions API, Structured Outputs vía Pydantic)
  CLAUDE    = Anthropic Claude (messages.parse, Structured Outputs vía Pydantic)
  CONSENSUS = ambos en paralelo, se usa el que reconcilia mejor consigo mismo
              (patrón real de la literatura: CE-OCR / consensus entropy,
              arXiv:2504.11101, comparar SALIDAS DE MODELOS DISTINTOS en vez
              de re-preguntarle al mismo modelo dos veces)

Reusa SOLO el scoring (score_one_bill) para que los números sean
comparables con el resto de arquitecturas ya medidas -- todo lo demás
(prompt, schema, llamada) se escribió de cero.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pydantic import BaseModel, Field

from app.config import settings
from app.ocr import _prep_receipt_image
from tests.eval.run_eval import score_one_bill

EVAL_DIR = Path(__file__).parent
EXPECTED_DIR = EVAL_DIR / "expected"
RECEIPTS_DIR = EVAL_DIR / "receipts"


class ReceiptItem(BaseModel):
    name: str = Field(description="Nombre del ítem tal como aparece impreso")
    quantity: float = Field(description="Cantidad de unidades (1 si no se indica)")
    line_total: float = Field(description="Precio TOTAL de esa línea completa, ya multiplicado por la cantidad si corresponde -- no el precio unitario")


class Receipt(BaseModel):
    merchant: str = Field(description="Nombre del local, vacío si no aparece")
    amount: float = Field(description="Total del consumo, SIN la propina sugerida si la hay")
    items: list[ReceiptItem] = Field(description="Cada ítem de consumo en el mismo orden en que aparece impreso -- NO incluir la línea de total/subtotal/propina como ítem")


_PROMPT = (
    "Lee esta boleta/comanda de restaurante o supermercado chileno y extrae "
    "cada ítem de consumo (nombre, cantidad, precio total de esa línea) en "
    "el mismo orden en que aparecen impresos, con la máxima precisión "
    "posible. Si la boleta muestra cantidad y precio unitario por separado "
    "del total de línea, multiplicalos para dar line_total -- nunca "
    "confundas el precio unitario con el total de la línea."
)


def read_gemini(image_bytes: bytes) -> dict:
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=settings.google_api_key)
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            _PROMPT,
            types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
        ],
        config={
            "response_mime_type": "application/json",
            "response_schema": Receipt,
        },
    )
    return json.loads(resp.text)


def read_claude(image_bytes: bytes) -> dict:
    import base64
    import anthropic
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    resp = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                {"type": "text", "text": _PROMPT + "\n\nDevuelve SOLO un JSON con esta forma exacta: "
                 '{"merchant": str, "amount": number, "items": [{"name": str, "quantity": number, "line_total": number}]}'
                 " -- sin texto antes ni después, sin markdown."},
            ],
        }],
    )
    text = resp.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text)


def _reconciliation_error(parsed: dict) -> float:
    items_sum = sum(it.get("line_total", 0) or 0 for it in parsed.get("items", []))
    amt = parsed.get("amount") or 0
    if amt <= 0:
        return float("inf")
    return abs(items_sum - amt) / amt


def read_consensus(image_bytes: bytes) -> dict:
    """CE-OCR: llama a 2 proveedores DISTINTOS en paralelo (no el mismo
    modelo dos veces) y usa el que reconcilia mejor internamente (su propia
    suma de ítems vs. su propio total declarado) -- señal barata de cuál de
    los dos probablemente leyó bien, sin necesitar ground-truth."""
    import queue
    import threading
    q: "queue.Queue" = queue.Queue()

    def worker(name, fn):
        try:
            q.put((name, fn(image_bytes)))
        except Exception as exc:  # noqa: BLE001
            q.put((name, exc))

    threads = [
        threading.Thread(target=worker, args=("gemini", read_gemini), daemon=True),
        threading.Thread(target=worker, args=("claude", read_claude), daemon=True),
    ]
    for t in threads:
        t.start()
    results = {}
    for _ in threads:
        name, val = q.get()
        results[name] = val
    candidates = {k: v for k, v in results.items() if not isinstance(v, Exception)}
    if not candidates:
        raise list(results.values())[0]
    if len(candidates) == 1:
        return next(iter(candidates.values()))
    best_name = min(candidates, key=lambda k: _reconciliation_error(candidates[k]))
    return candidates[best_name]


CANDIDATES = {"GEMINI": read_gemini, "CLAUDE": read_claude, "CONSENSUS": read_consensus}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--which", choices=list(CANDIDATES), required=True)
    args = ap.parse_args()
    fn = CANDIDATES[args.which]

    stems = sorted(p.stem for p in EXPECTED_DIR.glob("*.json"))
    pcts, times, rows = [], [], []
    print(f"\n  {args.which}  imagenes={len(stems)}\n")
    for stem in stems:
        exp = json.loads((EXPECTED_DIR / f"{stem}.json").read_text())
        img_candidates = list(RECEIPTS_DIR.glob(f"{stem}.*"))
        if not img_candidates:
            continue
        raw_bytes = img_candidates[0].read_bytes()
        # mismo prep que el resto del proyecto (rotación EXIF + resize a JPEG) para no confundir "el modelo es peor" con "le mandé una foto rara"
        data_url, _, _, jpeg_bytes = _prep_receipt_image(raw_bytes)
        t0 = time.time()
        try:
            parsed = fn(jpeg_bytes)
        except Exception as exc:  # noqa: BLE001
            print(f"  !! {stem}: EXC {exc}")
            continue
        elapsed = time.time() - t0
        parsed["items"] = [
            {"name": it["name"], "quantity": it.get("quantity") or 1, "line_total": it.get("line_total") or 0,
             "price": it.get("line_total") or 0, "needs_review": False}
            for it in parsed.get("items", [])
        ]
        times.append(elapsed)
        result = score_one_bill(parsed, exp)
        pcts.append(result["pct"])
        rows.append({"stem": stem, "pct": result["pct"], "elapsed": elapsed})
        mark = "✓" if result["pct"] >= 80 else ("~" if result["pct"] >= 60 else "✗")
        fails = [f"{k}[{c['detail']}]" for k, c in result["checks"].items() if c["applicable"] and not c["ok"]]
        print(f"  {mark} {stem:28s} {result['pct']:5.1f}%  {elapsed:5.1f}s  " + ("  ".join(fails[:2]) if fails else "todo ok"))

    overall = sum(pcts) / len(pcts) if pcts else 0.0
    ts = sorted(times)
    print(f"\n  OVERALL {args.which}: {overall:.1f}%   ({len(pcts)}/{len(stems)})  "
          f"p50={ts[len(ts)//2]:.1f}s  max={max(times):.1f}s  mean={sum(times)/len(times):.1f}s\n")
    (EVAL_DIR / "results" / f"bench_fresh_{args.which}.json").write_text(
        json.dumps({"which": args.which, "overall": round(overall, 1), "rows": rows}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
