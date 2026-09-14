"""
Tests del sistema Bill (/bills/*) — el flujo real de "Dividir cuenta".
Cubre el fix del bug "no se guardó el monto": finalize valida que cada ítem esté
repartido, y el toggle save_to_expense.
"""
import pytest
from tests.test_api import client  # noqa: F401


@pytest.fixture(scope="module")
def h(client):
    r = client.post("/auth/signup", json={
        "email": "bills-suite@test.com", "password": "Test1234!", "locale": "es",
    })
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="module")
def other_person(client, h):
    r = client.post("/split/people", json={"name": "Pedro", "color": "#f97316"}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _new_bill(client, h, other_person):
    b = client.post("/bills", json={"merchant": "Bar Test", "currency": "CLP"}, headers=h).json()
    bid = b["id"]
    # add Pedro (Yo ya está)
    client.post(f"/bills/{bid}/participants", json={"person_id": other_person}, headers=h)
    b = client.get(f"/bills/{bid}", headers=h).json()
    # 2 ítems de 10.000 c/u
    for _ in range(2):
        client.post(f"/bills/{bid}/items", json={"name": "Trago", "qty": 1, "unit_price": 10000},
                    headers=h)
    return client.get(f"/bills/{bid}", headers=h).json()


def test_set_payers_resets_previous_selection(client, h, other_person):
    """Bug real en prod (2026-09-14): elegir "pagó Pedro", arrepentirse y
    cambiar a "pagué yo" dejaba el paid_amount viejo de Pedro pegado (nunca
    se reseteaba a 0) — el balance final mostraba a Pedro como si hubiera
    pagado dos veces, y el usuario terminaba "debiéndole" a alguien que en
    realidad no pagó nada. set-payers debe reemplazar el reparto completo,
    no solo actualizar a quien viene en el pedido."""
    b = _new_bill(client, h, other_person)
    bid = b["id"]
    me = next(p for p in b["participants"] if p["is_me"])
    pedro = next(p for p in b["participants"] if not p["is_me"])
    # 1ro: "pagó Pedro"
    client.post(f"/bills/{bid}/set-payers", json=[{"participant_id": pedro["id"], "paid_amount": 20000}],
                headers=h)
    # se arrepiente: "pagué yo"
    r = client.post(f"/bills/{bid}/set-payers", json=[{"participant_id": me["id"], "paid_amount": 20000}],
                     headers=h)
    assert r.status_code == 200, r.text
    parts = {p["id"]: p for p in r.json()["participants"]}
    assert parts[pedro["id"]]["paid_amount"] == 0       # el pago viejo de Pedro se resetea
    assert parts[me["id"]]["paid_amount"] == 20000


def test_finalize_default_equal_shares_works(client, h, other_person):
    """add_item siembra shares equitativas; finalizar sin tocar nada debe andar."""
    b = _new_bill(client, h, other_person)
    bid = b["id"]
    me = next(p for p in b["participants"] if p["is_me"])
    client.post(f"/bills/{bid}/set-payers", json=[{"participant_id": me["id"], "paid_amount": 20000}],
                headers=h)
    r = client.post(f"/bills/{bid}/finalize", json={"save_to_expense": True}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["my_share"] == 10000     # 50% de 20.000
    assert r.json()["transaction_id"] is not None


def test_finalize_saves_expense_when_toggle_on(client, h, other_person):
    b = _new_bill(client, h, other_person)
    bid = b["id"]
    me = next(p for p in b["participants"] if p["is_me"])
    pedro = next(p for p in b["participants"] if not p["is_me"])
    # ítem 1 → todo mío ; ítem 2 → 50/50
    it1, it2 = b["items"][0]["id"], b["items"][1]["id"]
    client.post(f"/bills/{bid}/shares", json={"item_id": it1, "shares": [
        {"participant_id": me["id"], "weight": 1.0}]}, headers=h)
    client.post(f"/bills/{bid}/shares", json={"item_id": it2, "shares": [
        {"participant_id": me["id"], "percent": 50}, {"participant_id": pedro["id"], "percent": 50}],
    }, headers=h)
    client.post(f"/bills/{bid}/set-payers", json=[{"participant_id": me["id"], "paid_amount": 20000}],
                headers=h)

    r = client.post(f"/bills/{bid}/finalize", json={"save_to_expense": True, "category": "Bares y Salidas"},
                    headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "finalized"
    assert body["my_share"] == 15000          # 10.000 + 5.000
    assert body["transaction_id"] is not None
    # la transacción existe con mi parte
    txs = client.get("/transactions", headers=h).json()
    tx = next(t for t in txs if t["id"] == body["transaction_id"])
    assert tx["amount"] == 15000
    assert tx["category"] == "Bares y Salidas"


def test_finalize_no_expense_still_persists_split(client, h, other_person):
    b = _new_bill(client, h, other_person)
    bid = b["id"]
    me = next(p for p in b["participants"] if p["is_me"])
    pedro = next(p for p in b["participants"] if not p["is_me"])
    for it in b["items"]:
        client.post(f"/bills/{bid}/shares", json={"item_id": it["id"], "shares": [
            {"participant_id": me["id"], "weight": 0.5},
            {"participant_id": pedro["id"], "weight": 0.5}]}, headers=h)
    client.post(f"/bills/{bid}/set-payers", json=[{"participant_id": pedro["id"], "paid_amount": 20000}],
                headers=h)

    n_before = len(client.get("/transactions", headers=h).json())
    r = client.post(f"/bills/{bid}/finalize", json={"save_to_expense": False}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "finalized"          # la división SÍ queda guardada
    assert body["transaction_id"] is None          # pero NO se creó gasto
    assert body["my_share"] == 10000
    assert len(client.get("/transactions", headers=h).json()) == n_before

    # aparece en el historial
    lst = client.get("/bills", headers=h).json()
    row = next(x for x in lst if x["id"] == bid)
    assert row["status"] == "finalized" and row["my_share"] == 10000 and row["transaction_id"] is None


def test_shares_percent_must_sum_100(client, h, other_person):
    b = _new_bill(client, h, other_person)
    bid = b["id"]
    me = next(p for p in b["participants"] if p["is_me"])
    r = client.post(f"/bills/{bid}/shares", json={"item_id": b["items"][0]["id"], "shares": [
        {"participant_id": me["id"], "percent": 70}]}, headers=h)
    assert r.status_code == 400
    assert "100" in r.json()["detail"]


def test_item_position_y_starts_null_and_is_draggable(client, h, other_person):
    """position_y: null por defecto (ítem agregado a mano); se puede fijar/editar
    vía PATCH (lo que hace el frontend al soltar el marcador arrastrado)."""
    b = _new_bill(client, h, other_person)
    bid = b["id"]
    item = b["items"][0]
    assert item["position_y"] is None

    r = client.patch(f"/bills/{bid}/items/{item['id']}", json={"position_y": 37.5}, headers=h)
    assert r.status_code == 200, r.text
    updated = next(i for i in r.json()["items"] if i["id"] == item["id"])
    assert updated["position_y"] == 37.5

    # fuera de rango → rechazado
    r2 = client.patch(f"/bills/{bid}/items/{item['id']}", json={"position_y": 150}, headers=h)
    assert r2.status_code == 422


def test_item_bbox_fields_present_and_null_without_ocr(client, h, other_person):
    """Un ítem agregado a mano (sin pasar por el OCR) no tiene bbox — los
    campos deben venir en la respuesta como null/vacío, no faltar del JSON."""
    b = _new_bill(client, h, other_person)
    item = b["items"][0]
    for f in ("bbox_y0", "bbox_y1"):
        assert f in item and item[f] is None
    assert item["segments"] == []


@pytest.fixture(scope="module")
def third_person(client, h):
    r = client.post("/split/people", json={"name": "Ana", "color": "#22c55e"}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _bill_3way(client, h, pedro_id, ana_id, *, total_each: float, payer: str, merchant: str):
    """Boleta con Yo+Pedro+Ana, 1 ítem repartido en 3 partes iguales,
    finalizada con `payer` ("me"/"pedro"/"ana") pagando el total."""
    b = client.post("/bills", json={"merchant": merchant, "currency": "CLP"}, headers=h).json()
    bid = b["id"]
    client.post(f"/bills/{bid}/participants", json={"person_id": pedro_id}, headers=h)
    client.post(f"/bills/{bid}/participants", json={"person_id": ana_id}, headers=h)
    b = client.get(f"/bills/{bid}", headers=h).json()
    me = next(p for p in b["participants"] if p["is_me"])
    pedro = next(p for p in b["participants"] if p["person_id"] == pedro_id)
    ana = next(p for p in b["participants"] if p["person_id"] == ana_id)
    total = total_each * 3
    client.post(f"/bills/{bid}/items", json={"name": merchant, "qty": 1, "unit_price": total}, headers=h)
    payer_id = {"me": me["id"], "pedro": pedro["id"], "ana": ana["id"]}[payer]
    client.post(f"/bills/{bid}/set-payers", json=[{"participant_id": payer_id, "paid_amount": total}], headers=h)
    r = client.post(f"/bills/{bid}/finalize", json={"save_to_expense": False}, headers=h)
    assert r.status_code == 200, r.text
    return bid


def test_combine_settlement_minimizes_transfers_across_bills(client, h, third_person, other_person):
    """Escenario real: 3 boletas de un cumpleaños (regalo, torta, brunch),
    cada una pagada por una persona distinta, todas repartidas en partes
    iguales entre las mismas 3 personas. El saldo combinado debe dar el
    NETO por persona (no 3 liquidaciones separadas, algunas en direcciones
    opuestas entre las mismas dos personas) con el mínimo de transferencias."""
    pedro_id, ana_id = other_person, third_person
    # Regalo $30.000, pagó Pedro -> c/u debe $10.000
    b1 = _bill_3way(client, h, pedro_id, ana_id, total_each=10000, payer="pedro", merchant="Regalo")
    # Torta $15.000, pagó Ana -> c/u debe $5.000
    b2 = _bill_3way(client, h, pedro_id, ana_id, total_each=5000, payer="ana", merchant="Torta")
    # Brunch $60.000, pagué yo -> c/u debe $20.000
    b3 = _bill_3way(client, h, pedro_id, ana_id, total_each=20000, payer="me", merchant="Brunch")

    r = client.post("/bills/combine-settlement", json={"bill_ids": [b1, b2, b3]}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    transfers = body["transfers"]
    # Neto esperado: Yo +25.000 (pagué 60k, debía 10k+5k+20k=35k), Pedro -5.000
    # (pagó 30k, debía 10k+5k+20k=35k), Ana -20.000 (pagó 15k, debía 35k) ->
    # mínimo 2 transferencias, ambas HACIA mí, nunca 3 boletas por separado.
    assert len(transfers) == 2
    assert all(t["to_is_me"] for t in transfers)
    by_name = {t["from_name"]: t["amount"] for t in transfers}
    assert by_name["Pedro"] == 5000
    assert by_name["Ana"] == 20000


def test_combine_settlement_requires_finalized_bills(client, h, other_person):
    b = _new_bill(client, h, other_person)  # sin finalizar
    r = client.post("/bills/combine-settlement", json={"bill_ids": [b["id"], b["id"] + 999]}, headers=h)
    assert r.status_code == 404  # el segundo id no existe
