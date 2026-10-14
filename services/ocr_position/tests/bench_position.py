"""Benchmark del servicio de posición (ítems ubicados + tiempo por boleta) sobre
main, rotadas y, opcional, el holdout ya preparado a 2000px (carpeta como argumento).
    python3 -I services/ocr_position/tests/bench_position.py [carpeta_holdout_2000px]"""
import sys, time, json, base64, glob, os
from pathlib import Path
R=Path(__file__).resolve().parents[3]; S=Path(sys.argv[1]) if len(sys.argv) > 1 else None
sys.path.insert(0,str(R/"services/ocr_position"))
import app.main as M
E=R/"backend/tests/eval"
sets={
 "main":[(p, E/"expected"/(p.stem+".json")) for p in sorted((E/"receipts").glob("*")) if (E/"expected"/(p.stem+".json")).exists()],
 "rot":[(p, E/"expected_synthetic_rotated"/(p.stem+".json")) for p in sorted((E/"receipts_synthetic_rotated").glob("*"))],
 "hold":[(p, E/"expected_holdout"/(p.stem+".json")) for p in sorted((S.glob("*.jpg") if S else []))],
}
for name, pairs in sets.items():
    m=t=0; worst=0; slow=0
    for img, exp in pairs:
        d=json.loads(exp.read_text()); items=[r["name"] for r in (d.get("items", d) if isinstance(d, dict) else d)]
        if not items: continue
        b=img.read_bytes()
        t0=time.time()
        r=M.position(M.PositionRequest(image_b64=base64.b64encode(b).decode(), items=items))
        dt=time.time()-t0
        n=sum(1 for i in r.items if i.position_y is not None)
        m+=n; t+=len(items); worst=max(worst,dt); slow+= dt>7
        print(f"  {name:4} {img.stem[:34]:34} {n:3}/{len(items):<3} {dt:5.2f}s", flush=True)
    print(f"== {name}: {m}/{t} = {100*m/t:.1f}%  worst {worst:.2f}s  >7s: {slow}", flush=True)
