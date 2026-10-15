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


def test_parse_date_strict_with_time_and_month_names():
    y = date.today().year
    assert ocr._parse_date_strict(f"03-01-{y} 16:03:42") == date(y, 1, 3)
    assert ocr._parse_date_strict(f"03-JAN-{y}") == date(y, 1, 3)
    assert ocr._parse_date_strict(f"3 ene {y}") == date(y, 1, 3)
    assert ocr._parse_date_strict(f"03/Ene/{y}") == date(y, 1, 3)
    assert ocr._parse_date_strict(f"3 de enero de {y}") == date(y, 1, 3)
    assert ocr._parse_date_strict(f"31-feb-{y}") is None


def _res(items, pt, charges, currency="CLP"):
    rec = ParsedReceipt(amount=0, date=date.today(), merchant="X", category="Otros", items=[
        ParsedItem(name=f"i{k}", price=v, quantity=1, line_total=float(v)) for k, v in enumerate(items)
    ])
    return ocr.ParseResult(transactions=[rec], currency=currency, printed_total=pt, charges=charges)


def test_effective_charges_only_when_arithmetic_proves_them():
    tax = {"label": "Tax", "kind": "tax", "amount": 3.6}
    tip = {"label": "Tip", "kind": "tip", "amount": 8.0}
    # impuesto + propina agregados aparte y el total los incluye -> ambos
    assert ocr.effective_charges(_res([30, 10], 51.6, [tax, tip], "USD")) == [tax, tip]
    # solo el impuesto cuadra -> solo el impuesto
    assert ocr.effective_charges(_res([30, 10], 43.6, [tax, tip], "USD")) == [tax]
    # IVA incluido (ítems ya suman el total) -> nada
    iva = {"label": "IVA", "kind": "tax", "amount": 1900}
    assert ocr.effective_charges(_res([8000, 3900], 11900, [iva])) == []
    # nada cuadra (ítem mal leído) -> no sumar a ciegas
    assert ocr.effective_charges(_res([8000, 3000], 11900, [iva])) == []


def test_effective_charges_without_printed_total_never_adds_tax():
    iva = {"label": "IVA", "kind": "tax", "amount": 1900}
    tip = {"label": "Propina", "kind": "tip", "amount": 1190}
    assert ocr.effective_charges(_res([11900], None, [iva])) == []
    assert ocr.effective_charges(_res([11900], None, [iva, tip])) == [tip]


def test_effective_charges_never_more_than_one_tip():
    tips = [{"label": f"Propina {p}%", "kind": "tip", "amount": a} for p, a in ((10, 1910), (12, 2292), (15, 2865))]
    # opciones sugeridas sin total que las pruebe -> ninguna
    assert ocr.effective_charges(_res([19100], None, tips)) == []
    # el total impreso incluye una de ellas -> solo esa
    assert ocr.effective_charges(_res([19100], 21392, tips)) == [tips[1]]
    # el total no incluye ninguna -> ninguna
    assert ocr.effective_charges(_res([19100], 19100, tips)) == []


def test_charges_currency_and_proportional_finalize(client, auth_headers, monkeypatch):
    """Boleta tipo EE.UU.: impuesto sumado aparte + propina cobrada. La moneda
    detectada reemplaza el default, el impuesto queda como cargo aparte y al
    finalizar se reparte proporcional al consumo (como la propina)."""
    def fake(raw, **_kw):
        rec = ParsedReceipt(amount=0, date=date.today(), merchant="Diner", category="Otros", items=[
            ParsedItem(name="Burger", price=30, quantity=1, line_total=30.0),
            ParsedItem(name="Salad", price=10, quantity=1, line_total=10.0),
        ])
        return ocr.ParseResult(transactions=[rec], currency="USD", printed_total=51.6, charges=[
            {"label": "Sales Tax", "kind": "tax", "amount": 3.6},
            {"label": "Gratuity", "kind": "tip", "amount": 8.0},
        ])
    monkeypatch.setattr(ocr, "vision_parse_bill", fake)
    monkeypatch.setattr("app.storage.save_image", lambda data, name: "local://x")
    b = _new_bill(client, auth_headers)
    out = _upload(client, auth_headers, b["id"]).json()
    assert out["currency"] == "USD"
    assert out["extra_amount"] == 3.6 and out["tip_amount"] == 8.0
    assert out["printed_total"] == 51.6 and out["total_amount"] == 51.6

    # Yo (ya participante) come el Burger; otra persona la Salad. Pago yo.
    other = client.post("/split/people", json={"name": "Ana"}, headers=auth_headers).json()
    out = client.post(f"/bills/{b['id']}/participants", json={"person_id": other["id"]}, headers=auth_headers).json()
    me = next(p for p in out["participants"] if p["is_me"])
    ana = next(p for p in out["participants"] if not p["is_me"])
    items = {i["name"]: i["id"] for i in out["items"]}
    for name, pid in (("Burger", me["id"]), ("Salad", ana["id"])):
        r = client.post(f"/bills/{b['id']}/shares",
                        json={"item_id": items[name], "shares": [{"participant_id": pid, "weight": 1}]},
                        headers=auth_headers)
        assert r.status_code == 200, r.text
    r = client.post(f"/bills/{b['id']}/set-payers", json=[{"participant_id": me["id"], "paid_amount": 51.6}],
                    headers=auth_headers)
    assert r.status_code == 200, r.text
    r = client.post(f"/bills/{b['id']}/finalize", json={"save_to_expense": False}, headers=auth_headers)
    assert r.status_code == 200, r.text
    owes = {p["is_me"]: p["owes_amount"] for p in r.json()["participants"]}
    # 30/40 y 10/40 de (3.6 + 8.0)
    assert owes[True] == 38.7 and owes[False] == 12.9


def test_tip_and_extra_patch_recalc_total(client, auth_headers):
    b = _new_bill(client, auth_headers)
    client.post(f"/bills/{b['id']}/items", json={"name": "Lomo", "qty": 1, "unit_price": 10000}, headers=auth_headers)
    out = client.patch(f"/bills/{b['id']}", json={"tip_amount": 1000}, headers=auth_headers).json()
    assert out["total_amount"] == 11000
    out = client.patch(f"/bills/{b['id']}", json={"extra_amount": 500}, headers=auth_headers).json()
    assert out["total_amount"] == 11500


def test_charges_already_included_are_not_double_charged(client, auth_headers, monkeypatch):
    """Boleta con IVA incluido: el modelo reporta el IVA como 'cargo', pero los
    ítems ya suman el total impreso -> no se suma de nuevo."""
    def fake(raw, **_kw):
        rec = ParsedReceipt(amount=0, date=date.today(), merchant="Bar", category="Otros", items=[
            ParsedItem(name="Schop", price=4000, quantity=2, line_total=8000.0),
            ParsedItem(name="Papas", price=3900, quantity=1, line_total=3900.0),
        ])
        return ocr.ParseResult(transactions=[rec], currency="CLP", printed_total=11900,
                               charges=[{"label": "IVA 19%", "kind": "tax", "amount": 1900.0}])
    monkeypatch.setattr(ocr, "vision_parse_bill", fake)
    monkeypatch.setattr("app.storage.save_image", lambda data, name: "local://x")
    b = _new_bill(client, auth_headers)
    out = _upload(client, auth_headers, b["id"]).json()
    assert out["extra_amount"] == 0 and out["total_amount"] == 11900
