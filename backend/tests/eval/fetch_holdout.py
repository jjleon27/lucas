"""Descarga las fotos del set holdout que no se versionan (licencia NC/ND de
Flickr, repo público) desde la URL exacta guardada en cada
expected_holdout/<name>.json (`image_fetch_url`), verificando `image_sha256`
cuando existe para que el eval mida exactamente la misma imagen.

    cd backend && python3 tests/eval/fetch_holdout.py
"""
import hashlib
import json
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    for exp_path in sorted((HERE / "expected_holdout").glob("*.json")):
        exp = json.loads(exp_path.read_text())
        url = exp.get("image_fetch_url")
        dest = HERE / "receipts_holdout" / exp["image"]
        if not url or dest.exists():
            continue
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        data = urllib.request.urlopen(req, timeout=60).read()
        want = exp.get("image_sha256")
        if want and hashlib.sha256(data).hexdigest() != want:
            print(f"  ! {dest.name}: sha256 distinto al del ground truth — se guarda igual, revisar")
        dest.write_bytes(data)
        print(f"  ok {dest.name} ({len(data) // 1024} KB)")


if __name__ == "__main__":
    main()
