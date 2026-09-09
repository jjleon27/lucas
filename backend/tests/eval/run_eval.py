#!/usr/bin/env python3
"""
Eval harness para el pipeline de OCR de boletas (ocr.parse_receipt).

A diferencia de tests/test_boleta_*.py (que solo prueban el parser regex de
respaldo con texto inventado), esto corre el pipeline REAL de vision contra
fotos reales de boletas/comandas y lo puntua contra un ground-truth escrito
a mano en eval/expected/*.json.

Hace llamadas reales al LLM => cuesta plata y tarda. No es un test de pytest.

USO (siempre desde backend/):

    cd backend
    PY=/Library/Frameworks/Python.framework/Versions/3.13/bin/python3

    # baseline con el modelo/proveedor configurado en .env
    $PY tests/eval/run_eval.py

    # probar otro modelo de vision de OpenAI
    $PY tests/eval/run_eval.py --vision-model gpt-4o
    $PY tests/eval/run_eval.py --vision-model gpt-5-mini

    # probar otro proveedor (requiere que el provider implemente vision_json;
    # hoy solo OpenAIProvider lo hace -- ver NOTA abajo)
    $PY tests/eval/run_eval.py --provider gemini --vision-model gemini-2.5-flash

    # una sola imagen, con volcado del JSON parseado
    $PY tests/eval/run_eval.py --only ponzano_madrid --dump

    # comparar dos corridas guardadas
    $PY tests/eval/run_eval.py --compare tests/eval/results/A.json tests/eval/results/B.json

NOTA: para A/B con Claude/Gemini hay que agregarles un metodo vision_json en
app/ai/provider.py (hoy solo OpenAIProvider lo tiene; con otro provider el
pipeline cae al fallback Tesseract y el eval dara scores basura).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent.parent            # .../backend
RECEIPTS = HERE / "receipts"
EXPECTED = HERE / "expected"
RESULTS = HERE / "results"

sys.path.insert(0, str(BACKEND))


# ── scoring config ────────────────────────────────────────────────────────────
WEIGHTS = {
    "currency": 1.0,
    "merchant": 1.0,
    "amount": 3.0,
    "neto_iva": 1.0,
    "item_count": 2.0,
    "items_subtotal": 2.0,
    "items_lines": 3.0,
    "date": 1.0,
}


def _norm(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.lower().split())


def _amount_tol(expected: float, currency: str) -> float:
    if (currency or "CLP").upper() == "CLP":
        return max(round(expected * 0.02), 200)
    return max(expected * 0.02, 0.5)


def score_one(parsed: dict, exp: dict) -> dict:
    """parsed: {currency, merchant, amount, total_neto, iva_amount, date, items:[{name,price,quantity}]}
    Returns {checks: {name: {applicable, ok, weight, detail}}, pct}
    """
    checks: dict[str, dict] = {}

    def put(name, applicable, ok, detail=""):
        checks[name] = {
            "applicable": applicable,
            "ok": bool(ok),
            "weight": WEIGHTS[name],
            "detail": detail,
        }

    cur_exp = (exp.get("currency") or "CLP").upper()
    cur_got = (parsed.get("currency") or "").upper()
    put("currency", True, cur_got == cur_exp, f"exp {cur_exp} / got {cur_got or '-'}")

    mc = exp.get("merchant_contains")
    if mc:
        got_m = _norm(parsed.get("merchant") or "")
        put("merchant", True, _norm(mc) in got_m, f"exp ~'{mc}' / got '{parsed.get('merchant') or '-'}'")
    else:
        put("merchant", False, True, "sin nombre de local en la boleta")

    amt_exp = float(exp["amount"])
    amt_got = float(parsed.get("amount") or 0)
    tol = _amount_tol(amt_exp, cur_exp)
    put("amount", True, abs(amt_got - amt_exp) <= tol,
        f"exp {amt_exp:g} +-{tol:g} / got {amt_got:g}")

    neto_exp, iva_exp = exp.get("total_neto"), exp.get("iva_amount")
    neto_got = float(parsed.get("total_neto") or 0)
    iva_got = float(parsed.get("iva_amount") or 0)
    if neto_exp is None and iva_exp is None:
        ok = neto_got == 0 and iva_got == 0
        put("neto_iva", True, ok,
            f"exp sin desglose IVA / got neto={neto_got:g} iva={iva_got:g}")
    else:
        ok = (abs(neto_got - float(neto_exp)) <= max(float(neto_exp) * 0.02, 200)
              and abs(iva_got - float(iva_exp)) <= max(float(iva_exp) * 0.02, 200))
        put("neto_iva", True, ok,
            f"exp neto={neto_exp} iva={iva_exp} / got neto={neto_got:g} iva={iva_got:g}")

    ic_exp = exp.get("item_count")
    ic_max = exp.get("item_count_max", ic_exp)
    items = parsed.get("items") or []
    ic_got = len(items)
    if ic_exp is not None:
        if ic_got == ic_exp or (ic_max is not None and ic_exp <= ic_got <= ic_max):
            partial = 1.0
        elif abs(ic_got - ic_exp) <= 1:
            partial = 0.5
        else:
            partial = 0.0
        checks["item_count"] = {
            "applicable": True, "ok": partial >= 1.0, "partial": partial,
            "weight": WEIGHTS["item_count"], "detail": f"exp {ic_exp}{'..'+str(ic_max) if ic_max and ic_max != ic_exp else ''} / got {ic_got}",
        }
    else:
        put("item_count", False, True, "")

    # per-line: cuantas lineas del ground-truth (con line_total conocido) se
    # leyeron bien, matcheando por nombre difuso y verificando el total de linea.
    exp_lines = [it for it in (exp.get("items") or []) if it.get("line_total") is not None]
    if exp_lines and not exp.get("items_lenient"):
        from difflib import SequenceMatcher
        pool = list(items)
        hits = 0
        details = []
        for el in exp_lines:
            en = _norm(el["name"])
            best, bi = 0.0, -1
            for i, pi in enumerate(pool):
                r = SequenceMatcher(None, en, _norm(pi.get("name", ""))).ratio()
                if r > best:
                    best, bi = r, i
            if bi >= 0 and best >= 0.5:
                pi = pool.pop(bi)
                lt_got = float(pi.get("price") or 0) * int(pi.get("quantity") or 1)
                lt_exp = float(el["line_total"])
                if abs(lt_got - lt_exp) <= max(lt_exp * 0.05, 50):
                    hits += 1
                else:
                    details.append(f"{el['name']}: exp {lt_exp:g} got {lt_got:g}")
            else:
                details.append(f"{el['name']}: sin match")
        frac = hits / len(exp_lines)
        checks["items_lines"] = {
            "applicable": True, "ok": frac >= 0.999, "partial": round(frac, 3),
            "weight": WEIGHTS["items_lines"],
            "detail": f"{hits}/{len(exp_lines)} lineas ok" + (" | " + "; ".join(details[:4]) if details else ""),
        }
    else:
        put("items_lines", False, True, "lenient / sin line_total en ground-truth")

    sub_exp = exp.get("items_subtotal")
    if not exp.get("items_lenient") and sub_exp is not None:
        s = 0.0
        for it in items:
            try:
                s += float(it.get("price") or 0) * int(it.get("quantity") or 1)
            except (TypeError, ValueError):
                pass
        stol = max(float(sub_exp) * 0.05, _amount_tol(float(sub_exp), cur_exp))
        put("items_subtotal", True, abs(s - float(sub_exp)) <= stol,
            f"exp {sub_exp:g} +-{stol:g} / got {s:g}")
    else:
        put("items_subtotal", False, True, "lenient / sin subtotal esperado")

    d_exp = exp.get("date")
    if d_exp:
        d_got = parsed.get("date") or ""
        put("date", True, str(d_got) == str(d_exp), f"exp {d_exp} / got {d_got or '-'}")
    else:
        put("date", False, True, "")

    num = den = 0.0
    for c in checks.values():
        if not c["applicable"]:
            continue
        w = c["weight"]
        den += w
        num += w * c.get("partial", 1.0 if c["ok"] else 0.0)
    pct = (num / den * 100) if den else 0.0
    return {"checks": checks, "pct": round(pct, 1)}


# ── running the pipeline ──────────────────────────────────────────────────────
def run_pipeline(img_path: Path):
    """Returns (parsed_dict, raw_result, elapsed_s)."""
    from app import ocr  # imported late so --vision-model / --provider take effect

    data = img_path.read_bytes()
    t0 = time.time()
    res = ocr.parse_receipt(data, db=None, user_id=None)
    dt = time.time() - t0

    if not res or not res.transactions:
        return None, res, dt

    tx = res.transactions[0]
    parsed = {
        "currency": getattr(tx, "currency", "") or "",
        "merchant": getattr(tx, "merchant", "") or "",
        "amount": float(getattr(tx, "amount", 0) or 0),
        "total_neto": None,
        "iva_amount": None,
        "date": getattr(tx, "date", None).isoformat() if getattr(tx, "date", None) else "",
        "is_income": bool(getattr(tx, "is_income", False)),
        "category": getattr(tx, "category", "") or "",
        "items": [
            {"name": it.name, "price": float(it.price or 0), "quantity": int(it.quantity or 1)}
            for it in (getattr(tx, "items", []) or [])
        ],
    }
    # neto/iva no viven en ParsedReceipt; se infieren de una linea "IVA (19%)".
    for it in parsed["items"]:
        if "iva" in _norm(it["name"]):
            parsed["iva_amount"] = it["price"] * it["quantity"]
            parsed["total_neto"] = parsed["amount"] - parsed["iva_amount"]
    return parsed, res, dt


def cmd_run(args):
    import app.config as _cfg
    if args.provider:
        _cfg.settings.ai_provider = args.provider
    if args.vision_model:
        _cfg.settings.openai_vision_model = args.vision_model
        _cfg.settings.google_model = args.vision_model
        _cfg.settings.anthropic_model = args.vision_model

    from app.ai import provider as _prov
    prov_name = _prov.active_provider_name()
    vmodel = _cfg.settings.openai_vision_model

    names = sorted(p.stem for p in EXPECTED.glob("*.json"))
    if args.only:
        wanted = set(args.only)
        names = [n for n in names if n in wanted or any(w in n for w in wanted)]
        if not names:
            sys.exit(f"--only no coincide con nada. Disponibles: {[p.stem for p in EXPECTED.glob('*.json')]}")

    print(f"\n  provider={prov_name}  vision_model={vmodel}  imagenes={len(names)}\n")
    rows = []
    per_check_tot: dict[str, list] = {}

    for name in names:
        exp = json.loads((EXPECTED / name).with_suffix(".json").read_text())
        img = RECEIPTS / exp["image"]
        if not img.exists():
            cand = list(RECEIPTS.glob(name + ".*"))
            if cand:
                img = cand[0]
        try:
            parsed, raw, dt = run_pipeline(img)
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ {name:26s} ERROR: {e}")
            traceback.print_exc()
            rows.append({"name": name, "pct": 0.0, "error": str(e), "elapsed": 0})
            continue

        if parsed is None:
            print(f"  ✗ {name:26s}   0.0%   (pipeline no devolvio transacciones en {dt:.1f}s)")
            rows.append({"name": name, "pct": 0.0, "error": "no transactions", "elapsed": round(dt, 1)})
            continue

        sc = score_one(parsed, exp)
        mark = "✓" if sc["pct"] >= 80 else ("~" if sc["pct"] >= 60 else "✗")
        fails = [f"{k}[{v['detail']}]" for k, v in sc["checks"].items()
                 if v["applicable"] and not v["ok"] and v.get("partial", 0) < 1.0]
        print(f"  {mark} {name:26s} {sc['pct']:5.1f}%   {dt:4.1f}s   " + ("  ".join(fails) if fails else "todo ok"))
        if args.dump:
            print("      parsed:", json.dumps(parsed, ensure_ascii=False))
        rows.append({"name": name, "pct": sc["pct"], "elapsed": round(dt, 1),
                     "checks": {k: {"ok": v["ok"], "applicable": v["applicable"],
                                    "partial": v.get("partial"), "detail": v["detail"]}
                                for k, v in sc["checks"].items()},
                     "parsed": parsed})
        for k, v in sc["checks"].items():
            if v["applicable"]:
                per_check_tot.setdefault(k, []).append(v.get("partial", 1.0 if v["ok"] else 0.0))

    scored = [r for r in rows if "error" not in r]
    overall = round(sum(r["pct"] for r in scored) / len(scored), 1) if scored else 0.0
    print(f"\n  OVERALL: {overall}%   ({len(scored)}/{len(rows)} imagenes puntuadas)")
    print("  por check:  " + "   ".join(
        f"{k}={round(sum(v)/len(v)*100)}%" for k, v in sorted(per_check_tot.items())))

    RESULTS.mkdir(exist_ok=True)
    ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = RESULTS / f"{ts}_{prov_name}_{vmodel.replace('/', '-')}.json"
    out.write_text(json.dumps({
        "timestamp": ts, "provider": prov_name, "vision_model": vmodel,
        "overall": overall, "per_check": {k: round(sum(v) / len(v) * 100, 1)
                                          for k, v in per_check_tot.items()},
        "rows": rows,
    }, ensure_ascii=False, indent=2))
    print(f"  guardado -> {out.relative_to(BACKEND)}\n")


def cmd_compare(args):
    a = json.loads(Path(args.compare[0]).read_text())
    b = json.loads(Path(args.compare[1]).read_text())
    ra = {r["name"]: r for r in a["rows"]}
    rb = {r["name"]: r for r in b["rows"]}
    la = f"{a['provider']}/{a['vision_model']}"
    lb = f"{b['provider']}/{b['vision_model']}"
    print(f"\n  A = {la}   ({a['overall']}%)")
    print(f"  B = {lb}   ({b['overall']}%)\n")
    print(f"  {'imagen':26s} {'A':>7s} {'B':>7s} {'Δ':>7s}")
    for name in sorted(set(ra) | set(rb)):
        pa = ra.get(name, {}).get("pct", 0.0)
        pb = rb.get(name, {}).get("pct", 0.0)
        d = pb - pa
        flag = "  <<" if d <= -10 else ("  >>" if d >= 10 else "")
        print(f"  {name:26s} {pa:6.1f}% {pb:6.1f}% {d:+6.1f}{flag}")
    print(f"\n  OVERALL  A={a['overall']}%   B={b['overall']}%   Δ={b['overall'] - a['overall']:+.1f}\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", help="openai | anthropic | gemini (override AI_PROVIDER)")
    ap.add_argument("--vision-model", help="override modelo de vision (p.ej. gpt-4o, gpt-5-mini, gemini-2.5-flash)")
    ap.add_argument("--only", nargs="+", metavar="NAME", help="correr solo estas imagenes (match por substring)")
    ap.add_argument("--dump", action="store_true", help="imprimir el JSON parseado de cada imagen")
    ap.add_argument("--compare", nargs=2, metavar=("A.json", "B.json"), help="comparar dos result files")
    args = ap.parse_args()
    if args.compare:
        cmd_compare(args)
    else:
        cmd_run(args)


if __name__ == "__main__":
    main()
