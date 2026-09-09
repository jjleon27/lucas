"""
Tests del router de Proyectos (/projects): CRUD + summary + asociación de
transacciones. Reusa la infra de test_api.py (sqlite in-memory + TestClient).
"""
import pytest
from tests.test_api import client  # noqa: F401  (module-scoped TestClient fixture)


@pytest.fixture(scope="module")
def auth_headers(client):
    """Usuario propio de este módulo (email único para no chocar con test_api)."""
    r = client.post("/auth/signup", json={
        "email": "projects-suite@test.com", "password": "Test1234!", "locale": "es",
    })
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _mk_project(client, headers, **kw):
    body = {"name": "Viaje BsAs", "budget": 500000, **kw}
    r = client.post("/projects", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def test_create_and_list_project(client, auth_headers):
    p = _mk_project(client, auth_headers, name="Remodelación cocina", budget=1200000)
    assert p["name"] == "Remodelación cocina"
    assert p["budget"] == 1200000
    assert p["spent"] == 0
    assert p["tx_count"] == 0
    assert p["archived"] is False

    r = client.get("/projects", headers=auth_headers)
    assert r.status_code == 200
    assert any(x["id"] == p["id"] for x in r.json())


def test_create_rejects_empty_name(client, auth_headers):
    r = client.post("/projects", json={"name": "   "}, headers=auth_headers)
    assert r.status_code == 422


def test_update_and_archive(client, auth_headers):
    p = _mk_project(client, auth_headers)
    r = client.patch(f"/projects/{p['id']}", json={"budget": 900000, "archived": True},
                     headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["budget"] == 900000 and r.json()["archived"] is True
    # archivado no aparece por defecto
    ids = [x["id"] for x in client.get("/projects", headers=auth_headers).json()]
    assert p["id"] not in ids
    ids_all = [x["id"] for x in client.get("/projects?include_archived=true",
                                           headers=auth_headers).json()]
    assert p["id"] in ids_all


def test_transaction_associates_and_summary(client, auth_headers):
    p = _mk_project(client, auth_headers, name="Proyecto gasto", budget=100000)
    # dos gastos + un ingreso (el ingreso NO cuenta como gasto)
    for amt, cat, inc in [(30000, "Alimentación", False),
                          (20000, "Transporte", False),
                          (5000, "Ingresos", True)]:
        r = client.post("/transactions", json={
            "amount": amt, "category": cat, "date": "2026-09-01",
            "merchant": "x", "is_income": inc, "project_id": p["id"],
        }, headers=auth_headers)
        assert r.status_code in (200, 201), r.text
        assert r.json()["project_id"] == p["id"]

    s = client.get(f"/projects/{p['id']}/summary", headers=auth_headers).json()
    assert s["spent"] == 50000                 # 30k + 20k, sin el ingreso
    assert s["remaining"] == 50000             # 100k - 50k
    assert round(s["pct_used"], 2) == 0.5
    assert s["tx_count"] == 3
    cats = {c["category"]: c["amount"] for c in s["by_category"]}
    assert cats["Alimentación"] == 30000 and cats["Transporte"] == 20000

    txs = client.get(f"/projects/{p['id']}/transactions", headers=auth_headers).json()
    assert len(txs) == 3


def test_summary_no_budget(client, auth_headers):
    p = _mk_project(client, auth_headers, name="Sin presupuesto", budget=0)
    s = client.get(f"/projects/{p['id']}/summary", headers=auth_headers).json()
    assert s["remaining"] == 0 and s["pct_used"] == 0


def test_delete_project_keeps_transactions(client, auth_headers):
    p = _mk_project(client, auth_headers, name="A borrar")
    r = client.post("/transactions", json={
        "amount": 1000, "category": "Otros", "date": "2026-09-01",
        "merchant": "y", "project_id": p["id"],
    }, headers=auth_headers)
    tx_id = r.json()["id"]
    assert client.delete(f"/projects/{p['id']}", headers=auth_headers).status_code == 204
    assert client.get(f"/projects/{p['id']}", headers=auth_headers).status_code == 404
    # la transacción sigue existiendo, sin proyecto
    tx = client.get("/transactions", headers=auth_headers).json()
    row = next(t for t in tx if t["id"] == tx_id)
    assert row["project_id"] is None


def test_cross_user_project_is_404(client):
    r = client.post("/auth/signup", json={"email": "other-proj@test.com",
                                          "password": "Test1234!", "locale": "es"})
    h2 = {"Authorization": f"Bearer {r.json()['access_token']}"}
    p = _mk_project(client, h2, name="De otro user")

    r = client.post("/auth/signup", json={"email": "victim-proj@test.com",
                                          "password": "Test1234!", "locale": "es"})
    h1 = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert client.get(f"/projects/{p['id']}", headers=h1).status_code == 404
    assert client.post("/transactions", json={
        "amount": 1, "category": "Otros", "date": "2026-09-01",
        "merchant": "z", "project_id": p["id"],
    }, headers=h1).status_code == 400
