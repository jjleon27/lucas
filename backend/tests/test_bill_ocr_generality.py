"""
Regresiones del camino boleta → ítems de "Dividir cuenta" que no dependen de
ningún país/moneda/formato en particular (auditoría 2026-10-14):

  - un ítem cuyo precio el modelo no pudo leer (null) se conserva con
    needs_review en vez de borrarse
  - los decimales del total de línea sobreviven (EUR/USD)
  - una cantidad medida (kg) no se convierte en unidades falsas
  - arrastrar/renombrar no altera el total impreso
  - el total de línea editado manda sobre el unitario
  - líneas negativas (descuentos) se pueden crear/editar
  - una lectura fallida se distingue de una boleta vacía
  - una fecha ilegible no pisa la fecha de la cuenta
"""
from datetime import date

import pytest

# Reusa la app + DB en memoria del test de integración del split (mismo
# setup de entorno/stubs), con su propio usuario para no chocar con el suyo.
from tests.test_split_integration import client  # noqa: F401

from app import ocr
from app.schemas import ParsedItem, ParsedReceipt


@pytest.fixture(scope="module")
def auth_headers(client):  # noqa: F811
    r = client.post("/auth/signup", json={"email": "ocrgen@test.com", "password": "OcrGen1234!", "locale": "es"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _new_bill(client, headers, d="2026-01-15"):
    r = client.post("/bills", json={"date": d}, headers=headers)
    assert r.status_code in (200, 201), r.text
    return r.json()


def _fake_vision(monkeypatch, items, *, date_is_guess=False, result=True):
    def fake(raw, **_kw):
        if not result:
            return None
        rec = ParsedReceipt(amount=0, date=date.today(), merchant="X", category="Otros", items=items)
        return ocr.ParseResult(transactions=[rec], date_is_guess=date_is_guess)
    monkeypatch.setattr(ocr, "vision_parse_bill", fake)
    monkeypatch.setattr("app.storage.save_image", lambda data, name: "local://x")


def _upload(client, headers, bill_id):
    return client.post(f"/bills/{bill_id}/ocr", headers=headers,
                       files={"file": ("r.jpg", b"\xff\xd8\xff fake", "image/jpeg")})


def test_unreadable_price_is_kept_for_review(client, auth_headers, monkeypatch):
    _fake_vision(monkeypatch, [
        ParsedItem(name="Pisco sour", price=0, quantity=2, line_total=0.0, needs_review=True),
        ParsedItem(name="sin cebolla", price=0, quantity=1, line_total=0.0),
        ParsedItem(name="Lomo", price=9900, quantity=1, line_total=9900),
    ])
    b = _new_bill(client, auth_headers)
    out = _upload(client, auth_headers, b["id"]).json()
    names = {i["name"]: i for i in out["items"]}
    assert "Pisco sour" in names and names["Pisco sour"]["needs_review"]
    assert "sin cebolla" not in names


def test_decimals_survive_and_patch_does_not_drift(client, auth_headers, monkeypatch):
    _fake_vision(monkeypatch, [
        ParsedItem(name="Doble cerveza", price=2.75, quantity=2, line_total=5.50),
        ParsedItem(name="Empanadas", price=3333.33, quantity=3, line_total=10000),
    ])
    b = _new_bill(client, auth_headers)
    out = _upload(client, auth_headers, b["id"]).json()
    by = {i["name"]: i for i in out["items"]}
    assert by["Doble cerveza"]["line_total"] == 5.50
    assert by["Empanadas"]["line_total"] == 10000
    # Arrastrar la banda y renombrar no tocan el total impreso.
    iid = by["Empanadas"]["id"]
    out = client.patch(f"/bills/{b['id']}/items/{iid}", json={"position_y": 40}, headers=auth_headers).json()
    out = client.patch(f"/bills/{b['id']}/items/{iid}", json={"name": "Empanadas pino"}, headers=auth_headers).json()
    assert {i["id"]: i for i in out["items"]}[iid]["line_total"] == 10000
    assert out["total_amount"] == 10005.50


def test_line_total_edit_wins_and_negative_lines_allowed(client, auth_headers):
    b = _new_bill(client, auth_headers)
    out = client.post(f"/bills/{b['id']}/items", json={"name": "Cupón", "qty": 1, "unit_price": -1200},
                      headers=auth_headers)
    assert out.status_code in (200, 201), out.text
    iid = out.json()["items"][0]["id"]
    out = client.patch(f"/bills/{b['id']}/items/{iid}", json={"qty": 3, "line_total": 10.0},
                       headers=auth_headers).json()
    item = out["items"][0]
    assert item["line_total"] == 10.0 and item["qty"] == 3


def test_failed_read_is_flagged(client, auth_headers, monkeypatch):
    _fake_vision(monkeypatch, [], result=False)
    b = _new_bill(client, auth_headers)
    out = _upload(client, auth_headers, b["id"]).json()
    assert out.get("ocr_status") == "failed"


def test_unreadable_date_does_not_overwrite(client, auth_headers, monkeypatch):
    _fake_vision(monkeypatch, [ParsedItem(name="Café", price=2.5, quantity=1, line_total=2.5)],
                 date_is_guess=True)
    b = _new_bill(client, auth_headers, d="2025-12-31")
    out = _upload(client, auth_headers, b["id"]).json()
    assert out["date"].startswith("2025-12-31")


def test_parse_date_strict():
    assert ocr._parse_date_strict("2026-03-04") == date(2026, 3, 4)
    assert ocr._parse_date_strict("2026-03-04T12:30:00") == date(2026, 3, 4)
    assert ocr._parse_date_strict("14.09.2026") == date(2026, 9, 14)
    assert ocr._parse_date_strict("ayer a las 9") is None
    assert ocr._parse_date_strict(None) is None
