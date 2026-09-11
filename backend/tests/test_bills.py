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
