"""
Advanced split tests — covers edge cases NOT in test_split_integration.py:

  - Rounding: 1 CLP / 3 people
  - Percent split that doesn't sum to 100%
  - Add item (propina/IVA) after split started
  - Edit item (PATCH /split/items)
  - Delete item (DELETE /split/items)
  - Settlement with save_to_lucas (payer=me, payer=other)
  - Security: cross-user isolation (item/person ownership)
  - start-manual dedup (same merchant/amount/date → same tx)
  - People: delete person with existing assignments
  - People: try to delete "Yo" → 400
  - Completion_pct with partial assignments
  - Full mock-OCR → split flow (upload tx with items → assign → settle)
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import unittest.mock as _mock


class _FakeRateLimitExceeded(Exception):
    pass


class _FakeLimiter:
    def __init__(self, *args, **kwargs):
        pass

    def limit(self, *args, **kwargs):
        def decorator(fn):
            return fn
        return decorator


_slowapi_mod = _mock.MagicMock()
_slowapi_mod.Limiter = _FakeLimiter

_slowapi_errors_mod = _mock.MagicMock()
_slowapi_errors_mod.RateLimitExceeded = _FakeRateLimitExceeded

_mock.patch.dict(
    "sys.modules",
    {
        "cv2": _mock.MagicMock(),
        "pytesseract": _mock.MagicMock(),
        "PIL": _mock.MagicMock(),
        "PIL.Image": _mock.MagicMock(),
        "psycopg2": _mock.MagicMock(),
        "pdf2image": _mock.MagicMock(),
        "pdfplumber": _mock.MagicMock(),
        "slowapi": _slowapi_mod,
        "slowapi.errors": _slowapi_errors_mod,
        "slowapi.util": _mock.MagicMock(),
        "slowapi._rate_limit_exceeded_handler": _mock.MagicMock(),
    },
).start()

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["JWT_SECRET"] = "split-advanced-secret"
os.environ["AI_PROVIDER"] = "none"
os.environ["ALLOW_PASSWORDLESS"] = "false"
os.environ["CORS_ORIGINS"] = "http://localhost:3000"
os.environ["STORAGE_BACKEND"] = "local"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.database import Base, get_db
from app import models as _models  # noqa: F401

TEST_ENGINE = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(TEST_ENGINE, "connect")
def _set_sqlite_pragma(dbapi_conn, _record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


TestSession = sessionmaker(bind=TEST_ENGINE, autoflush=False, autocommit=False)
Base.metadata.create_all(TEST_ENGINE)


def override_get_db():
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def _reset_db_override():
    saved = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = override_get_db
    yield
    if saved is not None:
        app.dependency_overrides[get_db] = saved
    else:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def auth_headers(client):
    resp = client.post(
        "/auth/signup",
        json={"email": "splitadv@test.com", "password": "SplitAdv1!", "locale": "es"},
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def auth_headers_b(client):
    """Second user for cross-user security tests."""
    resp = client.post(
        "/auth/signup",
        json={"email": "splitadv_b@test.com", "password": "SplitAdvB1!", "locale": "es"},
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def yo_id(client, auth_headers):
    resp = client.get("/split/me", headers=auth_headers)
    assert resp.status_code == 200
    return resp.json()["id"]


def _make_person(client, auth_headers, name, color="#ef4444"):
    resp = client.post("/split/people", json={"name": name, "color": color}, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _make_tx(client, auth_headers, amount=10000, merchant="Test", date="2026-05-01"):
    resp = client.post(
        "/split/start-manual",
        json={"merchant": merchant, "total_amount": amount, "currency": "CLP", "date": date},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["transaction_id"]


# ════════════════════════════════════════════════════════════════════════════
# 1. Rounding edge cases
# ════════════════════════════════════════════════════════════════════════════

class TestRoundingEdgeCases:
    def test_1_clp_split_3_people_remainder_goes_to_last(self, client, auth_headers, yo_id):
        """1 CLP / 3 = 0.33 each; remainder 0.01 goes to last person."""
        a = _make_person(client, auth_headers, "RoundA")
        b = _make_person(client, auth_headers, "RoundB")

        tx_id = _make_tx(client, auth_headers, amount=1, merchant="1CLP3", date="2026-05-02")
        # Get the item id
        start = client.post(f"/split/start?transaction_id={tx_id}", headers=auth_headers)
        item_id = start.json()["items"][0]["id"]

        client.post("/split/assign-item", json={
            "item_id": item_id,
            "assignees": [
                {"person_id": yo_id, "split_type": "equal"},
                {"person_id": a, "split_type": "equal"},
                {"person_id": b, "split_type": "equal"},
            ],
        }, headers=auth_headers)

        result = client.get(f"/split/result?transaction_id={tx_id}", headers=auth_headers).json()
        totals = {p["person_id"]: p["total"] for p in result["people"]}
        # Sum must equal 1 CLP exactly
        assert sum(totals.values()) == 1.0, f"Total mismatch: {totals}"
        # Each person gets at most 0.34 (rounded up for last)
        for t in totals.values():
            assert 0.33 <= t <= 0.34, f"Unexpected share: {t}"

    def test_equal_split_odd_amount_sum_exact(self, client, auth_headers, yo_id):
        """9999 CLP / 2 people = 4999.5 each — sum must be exact 9999."""
        a = _make_person(client, auth_headers, "OddA")
        tx_id = _make_tx(client, auth_headers, amount=9999, merchant="Odd9999", date="2026-05-03")
        start = client.post(f"/split/start?transaction_id={tx_id}", headers=auth_headers)
        item_id = start.json()["items"][0]["id"]

        client.post("/split/assign-item", json={
            "item_id": item_id,
            "assignees": [
                {"person_id": yo_id, "split_type": "equal"},
                {"person_id": a, "split_type": "equal"},
            ],
        }, headers=auth_headers)

        result = client.get(f"/split/result?transaction_id={tx_id}", headers=auth_headers).json()
        totals = [p["total"] for p in result["people"]]
        assert round(sum(totals), 2) == 9999.0

    def test_percent_not_summing_100_remainder_to_last(self, client, auth_headers, yo_id):
        """60% + 30% = 90%; last person gets the remaining 10% (1000 CLP)."""
        a = _make_person(client, auth_headers, "PctA")
        tx_id = _make_tx(client, auth_headers, amount=10000, merchant="Pct90", date="2026-05-04")
        start = client.post(f"/split/start?transaction_id={tx_id}", headers=auth_headers)
        item_id = start.json()["items"][0]["id"]

        client.post("/split/assign-item", json={
            "item_id": item_id,
            "assignees": [
                {"person_id": yo_id, "split_type": "percent", "value": 60},
                {"person_id": a, "split_type": "percent", "value": 30},
            ],
        }, headers=auth_headers)

        result = client.get(f"/split/result?transaction_id={tx_id}", headers=auth_headers).json()
        totals = {p["person_id"]: p["total"] for p in result["people"]}
        # Last person (a) gets 100% - 60% = 40% = 4000
        assert totals[yo_id] == 6000.0
        assert totals[a] == 4000.0
        assert sum(totals.values()) == 10000.0

    def test_amount_split_remainder_to_last(self, client, auth_headers, yo_id):
        """Exact amount split: 3000 + 3000 = 6000; last gets 10000-6000 = 4000."""
        a = _make_person(client, auth_headers, "AmtA")
        tx_id = _make_tx(client, auth_headers, amount=10000, merchant="AmtRem", date="2026-05-05")
        start = client.post(f"/split/start?transaction_id={tx_id}", headers=auth_headers)
        item_id = start.json()["items"][0]["id"]

        client.post("/split/assign-item", json={
            "item_id": item_id,
            "assignees": [
                {"person_id": yo_id, "split_type": "amount", "value": 3000},
                {"person_id": a, "split_type": "amount", "value": 3000},
            ],
        }, headers=auth_headers)

        result = client.get(f"/split/result?transaction_id={tx_id}", headers=auth_headers).json()
        totals = {p["person_id"]: p["total"] for p in result["people"]}
        assert totals[yo_id] == 3000.0
        assert totals[a] == 7000.0  # last gets remainder
        assert sum(totals.values()) == 10000.0


# ════════════════════════════════════════════════════════════════════════════
# 2. Add / Edit / Delete items in a running split
# ════════════════════════════════════════════════════════════════════════════

class TestItemMutations:
    def test_add_propina_updates_total(self, client, auth_headers, yo_id):
        """POST /split/add-item adds propina; transaction.amount increases."""
        a = _make_person(client, auth_headers, "PropA")
        tx_id = _make_tx(client, auth_headers, amount=20000, merchant="Restaurante", date="2026-05-06")

        tip_resp = client.post(
            f"/split/add-item?transaction_id={tx_id}",
            json={"name": "Propina 10%", "price": 2000, "quantity": 1},
            headers=auth_headers,
        )
        assert tip_resp.status_code == 201, tip_resp.text
        tip_item = tip_resp.json()
        assert tip_item["name"] == "Propina 10%"
        assert tip_item["line_total"] == 2000.0

        # Transaction total should now be 22000
        result = client.get(f"/split/result?transaction_id={tx_id}", headers=auth_headers).json()
        assert result["total_amount"] == 22000.0

    def test_add_iva_item_assigned_to_all(self, client, auth_headers, yo_id):
        """Add IVA item, assign equally to 2 people, check totals."""
        a = _make_person(client, auth_headers, "IvaB")
        tx_id = _make_tx(client, auth_headers, amount=10000, merchant="IVATest", date="2026-05-07")
        start = client.post(f"/split/start?transaction_id={tx_id}", headers=auth_headers)
        base_item_id = start.json()["items"][0]["id"]

        # Assign base item to yo
        client.post("/split/assign-item", json={
            "item_id": base_item_id,
            "assignees": [{"person_id": yo_id, "split_type": "equal"}],
        }, headers=auth_headers)

        # Add IVA
        iva_resp = client.post(
            f"/split/add-item?transaction_id={tx_id}",
            json={"name": "IVA 19%", "price": 1900, "quantity": 1},
            headers=auth_headers,
        )
        iva_id = iva_resp.json()["id"]

        # Assign IVA to both
        client.post("/split/assign-item", json={
            "item_id": iva_id,
            "assignees": [
                {"person_id": yo_id, "split_type": "equal"},
                {"person_id": a, "split_type": "equal"},
            ],
        }, headers=auth_headers)

        result = client.get(f"/split/result?transaction_id={tx_id}", headers=auth_headers).json()
        totals = {p["person_id"]: p["total"] for p in result["people"]}
        # yo: 10000 + 950 = 10950; a: 950
        assert totals[yo_id] == 10950.0
        assert totals[a] == 950.0
        assert result["total_amount"] == 11900.0

    def test_edit_item_price_updates_result(self, client, auth_headers, yo_id):
        """PATCH /split/items/{id} → price change propagates to result and tx.amount."""
        tx_id = _make_tx(client, auth_headers, amount=5000, merchant="EditTest", date="2026-05-08")
        start = client.post(f"/split/start?transaction_id={tx_id}", headers=auth_headers)
        item_id = start.json()["items"][0]["id"]

        patch_resp = client.patch(
            f"/split/items/{item_id}",
            json={"price": 8000},
            headers=auth_headers,
        )
        assert patch_resp.status_code == 200, patch_resp.text
        assert patch_resp.json()["line_total"] == 8000.0

        result = client.get(f"/split/result?transaction_id={tx_id}", headers=auth_headers).json()
        assert result["total_amount"] == 8000.0

    def test_edit_item_name(self, client, auth_headers, yo_id):
        """PATCH /split/items/{id} name update."""
        tx_id = _make_tx(client, auth_headers, amount=3000, merchant="NameEdit", date="2026-05-09")
        start = client.post(f"/split/start?transaction_id={tx_id}", headers=auth_headers)
        item_id = start.json()["items"][0]["id"]

        patch_resp = client.patch(
            f"/split/items/{item_id}",
            json={"name": "Renamed Item"},
            headers=auth_headers,
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["name"] == "Renamed Item"

    def test_delete_item_removes_from_result(self, client, auth_headers, yo_id):
        """DELETE /split/items/{id} → item gone, tx.amount decreases."""
        tx_id = _make_tx(client, auth_headers, amount=15000, merchant="DeleteTest", date="2026-05-10")
        start = client.post(f"/split/start?transaction_id={tx_id}", headers=auth_headers)
        item_id = start.json()["items"][0]["id"]

        # Add a second item
        extra = client.post(
            f"/split/add-item?transaction_id={tx_id}",
            json={"name": "Extra", "price": 5000, "quantity": 1},
            headers=auth_headers,
        )
        extra_id = extra.json()["id"]

        # Delete the extra item
        del_resp = client.delete(f"/split/items/{extra_id}", headers=auth_headers)
        assert del_resp.status_code == 204

        result = client.get(f"/split/result?transaction_id={tx_id}", headers=auth_headers).json()
        item_ids = [it["id"] for it in result["items"]]
        assert extra_id not in item_ids
        assert result["total_amount"] == 15000.0

    def test_edit_nonexistent_item_returns_404(self, client, auth_headers):
        resp = client.patch("/split/items/999999", json={"price": 100}, headers=auth_headers)
        assert resp.status_code == 404

    def test_delete_nonexistent_item_returns_404(self, client, auth_headers):
        resp = client.delete("/split/items/999999", headers=auth_headers)
        assert resp.status_code == 404


# ════════════════════════════════════════════════════════════════════════════
# 3. Settlement with save_to_lucas
# ════════════════════════════════════════════════════════════════════════════

class TestSettleSaveToLucas:
    def test_save_to_lucas_me_payer_sets_account(self, client, auth_headers, yo_id):
        """save_to_lucas=True, payer=me → tx.amount = my share, tx.account_id set."""
        accounts_resp = client.get("/accounts", headers=auth_headers)
        efectivo_id = next(a["id"] for a in accounts_resp.json() if a["name"] == "Efectivo")

        a = _make_person(client, auth_headers, "SaveA")
        tx_id = _make_tx(client, auth_headers, amount=10000, merchant="SaveLucas1", date="2026-05-11")
        start = client.post(f"/split/start?transaction_id={tx_id}", headers=auth_headers)
        item_id = start.json()["items"][0]["id"]

        client.post("/split/assign-item", json={
            "item_id": item_id,
            "assignees": [
                {"person_id": yo_id, "split_type": "equal"},
                {"person_id": a, "split_type": "equal"},
            ],
        }, headers=auth_headers)

        settle_resp = client.post("/split/settle", json={
            "transaction_id": tx_id,
            "payer_person_id": yo_id,
            "account_id": efectivo_id,
            "save_to_lucas": True,
        }, headers=auth_headers)
        assert settle_resp.status_code == 200, settle_resp.text
        data = settle_resp.json()
        assert data["saved_transaction_id"] == tx_id
        assert data["my_total"] == 5000.0

    def test_save_to_lucas_other_payer_adds_note(self, client, auth_headers, yo_id):
        """save_to_lucas=True, payer=otro → tx gets note 'Pagó [name]'."""
        b = _make_person(client, auth_headers, "OtherPayer")
        tx_id = _make_tx(client, auth_headers, amount=9000, merchant="OtherPaid", date="2026-05-12")
        start = client.post(f"/split/start?transaction_id={tx_id}", headers=auth_headers)
        item_id = start.json()["items"][0]["id"]

        client.post("/split/assign-item", json={
            "item_id": item_id,
            "assignees": [
                {"person_id": yo_id, "split_type": "equal"},
                {"person_id": b, "split_type": "equal"},
            ],
        }, headers=auth_headers)

        settle_resp = client.post("/split/settle", json={
            "transaction_id": tx_id,
            "payer_person_id": b,
            "save_to_lucas": True,
        }, headers=auth_headers)
        assert settle_resp.status_code == 200, settle_resp.text
        data = settle_resp.json()
        assert data["saved_transaction_id"] == tx_id
        assert data["my_total"] == 4500.0
        assert data["payer_name"] == "OtherPayer"

    def test_save_to_lucas_false_does_not_save(self, client, auth_headers, yo_id):
        """save_to_lucas=False → saved_transaction_id is None."""
        a = _make_person(client, auth_headers, "NoSaveA")
        tx_id = _make_tx(client, auth_headers, amount=6000, merchant="NoSave", date="2026-05-13")
        start = client.post(f"/split/start?transaction_id={tx_id}", headers=auth_headers)
        item_id = start.json()["items"][0]["id"]

        client.post("/split/assign-item", json={
            "item_id": item_id,
            "assignees": [
                {"person_id": yo_id, "split_type": "equal"},
                {"person_id": a, "split_type": "equal"},
            ],
        }, headers=auth_headers)

        settle_resp = client.post("/split/settle", json={
            "transaction_id": tx_id,
            "save_to_lucas": False,
        }, headers=auth_headers)
        assert settle_resp.status_code == 200
        assert settle_resp.json()["saved_transaction_id"] is None

    def test_save_to_lucas_wrong_account_returns_400(self, client, auth_headers, auth_headers_b, yo_id):
        """save_to_lucas with account belonging to other user → 400."""
        # Get user B's account
        b_accounts = client.get("/accounts", headers=auth_headers_b).json()
        b_efectivo_id = next((a["id"] for a in b_accounts if a["name"] == "Efectivo"), None)
        if b_efectivo_id is None:
            pytest.skip("User B has no Efectivo account")

        a = _make_person(client, auth_headers, "WrongAccA")
        tx_id = _make_tx(client, auth_headers, amount=5000, merchant="WrongAcc", date="2026-05-14")
        start = client.post(f"/split/start?transaction_id={tx_id}", headers=auth_headers)
        item_id = start.json()["items"][0]["id"]

        client.post("/split/assign-item", json={
            "item_id": item_id,
            "assignees": [{"person_id": yo_id, "split_type": "equal"}],
        }, headers=auth_headers)

        settle_resp = client.post("/split/settle", json={
            "transaction_id": tx_id,
            "payer_person_id": yo_id,
            "account_id": b_efectivo_id,
            "save_to_lucas": True,
        }, headers=auth_headers)
        assert settle_resp.status_code == 400


# ════════════════════════════════════════════════════════════════════════════
# 4. Security — cross-user isolation
# ════════════════════════════════════════════════════════════════════════════

class TestCrossUserSecurity:
    def test_user_b_cannot_access_user_a_result(self, client, auth_headers, auth_headers_b, yo_id):
        """User B cannot see User A's split result."""
        tx_id = _make_tx(client, auth_headers, amount=5000, merchant="PrivateA", date="2026-05-15")
        resp = client.get(f"/split/result?transaction_id={tx_id}", headers=auth_headers_b)
        assert resp.status_code == 404

    def test_user_b_cannot_settle_user_a_tx(self, client, auth_headers, auth_headers_b, yo_id):
        tx_id = _make_tx(client, auth_headers, amount=5000, merchant="PrivateSettle", date="2026-05-16")
        resp = client.post("/split/settle", json={"transaction_id": tx_id}, headers=auth_headers_b)
        assert resp.status_code == 404

    def test_user_b_cannot_assign_user_a_item(self, client, auth_headers, auth_headers_b, yo_id):
        tx_id = _make_tx(client, auth_headers, amount=4000, merchant="PrivateAssign", date="2026-05-17")
        start = client.post(f"/split/start?transaction_id={tx_id}", headers=auth_headers)
        item_id = start.json()["items"][0]["id"]

        yo_b = client.get("/split/me", headers=auth_headers_b).json()["id"]

        resp = client.post("/split/assign-item", json={
            "item_id": item_id,
            "assignees": [{"person_id": yo_b, "split_type": "equal"}],
        }, headers=auth_headers_b)
        assert resp.status_code == 404

    def test_user_a_cannot_assign_user_b_person_to_own_item(self, client, auth_headers, auth_headers_b, yo_id):
        """User A cannot use User B's person_id in their own split."""
        yo_b = client.get("/split/me", headers=auth_headers_b).json()["id"]

        tx_id = _make_tx(client, auth_headers, amount=3000, merchant="XPersonAssign", date="2026-05-18")
        start = client.post(f"/split/start?transaction_id={tx_id}", headers=auth_headers)
        item_id = start.json()["items"][0]["id"]

        resp = client.post("/split/assign-item", json={
            "item_id": item_id,
            "assignees": [{"person_id": yo_b, "split_type": "equal"}],
        }, headers=auth_headers)
        assert resp.status_code == 404

    def test_user_b_cannot_delete_user_a_person(self, client, auth_headers, auth_headers_b, yo_id):
        extra = _make_person(client, auth_headers, "APersonToProtect")
        resp = client.delete(f"/split/people/{extra}", headers=auth_headers_b)
        assert resp.status_code == 404

    def test_user_b_cannot_edit_user_a_item(self, client, auth_headers, auth_headers_b):
        tx_id = _make_tx(client, auth_headers, amount=2000, merchant="EditSec", date="2026-05-19")
        start = client.post(f"/split/start?transaction_id={tx_id}", headers=auth_headers)
        item_id = start.json()["items"][0]["id"]

        resp = client.patch(f"/split/items/{item_id}", json={"price": 1}, headers=auth_headers_b)
        assert resp.status_code == 404


# ════════════════════════════════════════════════════════════════════════════
# 5. start-manual deduplication
# ════════════════════════════════════════════════════════════════════════════

class TestStartManualDedup:
    def test_same_merchant_amount_date_returns_same_tx(self, client, auth_headers):
        resp1 = client.post("/split/start-manual", json={
            "merchant": "DupTest", "total_amount": 7777, "currency": "CLP", "date": "2026-05-20",
        }, headers=auth_headers)
        assert resp1.status_code == 201
        tx1 = resp1.json()["transaction_id"]

        resp2 = client.post("/split/start-manual", json={
            "merchant": "DupTest", "total_amount": 7777, "currency": "CLP", "date": "2026-05-20",
        }, headers=auth_headers)
        tx2 = resp2.json()["transaction_id"]
        assert tx1 == tx2, "Same merchant/amount/date should return same transaction"

    def test_different_amount_creates_new_tx(self, client, auth_headers):
        resp1 = client.post("/split/start-manual", json={
            "merchant": "DiffAmount", "total_amount": 1000, "currency": "CLP", "date": "2026-05-21",
        }, headers=auth_headers)
        tx1 = resp1.json()["transaction_id"]

        resp2 = client.post("/split/start-manual", json={
            "merchant": "DiffAmount", "total_amount": 2000, "currency": "CLP", "date": "2026-05-21",
        }, headers=auth_headers)
        tx2 = resp2.json()["transaction_id"]
        assert tx1 != tx2

    def test_different_date_creates_new_tx(self, client, auth_headers):
        """Dates > 2 days apart are NOT duplicates (dedup window is ±2 days)."""
        resp1 = client.post("/split/start-manual", json={
            "merchant": "DiffDate", "total_amount": 5000, "currency": "CLP", "date": "2026-05-22",
        }, headers=auth_headers)
        tx1 = resp1.json()["transaction_id"]

        resp2 = client.post("/split/start-manual", json={
            "merchant": "DiffDate", "total_amount": 5000, "currency": "CLP", "date": "2026-05-28",
        }, headers=auth_headers)
        tx2 = resp2.json()["transaction_id"]
        assert tx1 != tx2


# ════════════════════════════════════════════════════════════════════════════
# 6. People management edge cases
# ════════════════════════════════════════════════════════════════════════════

class TestPeopleManagement:
    def test_cannot_delete_yo(self, client, auth_headers, yo_id):
        resp = client.delete(f"/split/people/{yo_id}", headers=auth_headers)
        assert resp.status_code == 400
        assert "yourself" in resp.json()["detail"].lower()

    def test_delete_person_with_assignments(self, client, auth_headers, yo_id):
        """Deleting a person who has item assignments should work (cascade)."""
        person = _make_person(client, auth_headers, "DeleteMe")
        tx_id = _make_tx(client, auth_headers, amount=4000, merchant="CascadeDel", date="2026-05-24")
        start = client.post(f"/split/start?transaction_id={tx_id}", headers=auth_headers)
        item_id = start.json()["items"][0]["id"]

        client.post("/split/assign-item", json={
            "item_id": item_id,
            "assignees": [{"person_id": person, "split_type": "equal"}],
        }, headers=auth_headers)

        # Deleting the person — should succeed without 500
        del_resp = client.delete(f"/split/people/{person}", headers=auth_headers)
        assert del_resp.status_code == 204

        # Result should now show item as unassigned
        result = client.get(f"/split/result?transaction_id={tx_id}", headers=auth_headers).json()
        assert result["people"] == []
        assert result["unassigned_total"] == 4000.0

    def test_delete_nonexistent_person_returns_404(self, client, auth_headers):
        resp = client.delete("/split/people/999999", headers=auth_headers)
        assert resp.status_code == 404

    def test_list_people_yo_is_first(self, client, auth_headers, yo_id):
        """GET /split/people always returns 'Yo' as first entry."""
        _make_person(client, auth_headers, "ZZZLast")
        people = client.get("/split/people", headers=auth_headers).json()
        assert people[0]["is_me"] is True

    def test_create_person_auto_color_from_palette(self, client, auth_headers):
        """Person created without specifying color gets a palette color."""
        resp = client.post("/split/people", json={"name": "PaletteTest"}, headers=auth_headers)
        assert resp.status_code == 201
        assert resp.json()["color"].startswith("#")


# ════════════════════════════════════════════════════════════════════════════
# 7. Completion percentage with partial assignments
# ════════════════════════════════════════════════════════════════════════════

class TestCompletionPercentage:
    def test_3_items_1_assigned_33pct(self, client, auth_headers, yo_id):
        tx_id = _make_tx(client, auth_headers, amount=3000, merchant="Partial33", date="2026-05-25")
        # Seed with 3 items
        items_in = [
            {"name": "A", "price": 1000, "quantity": 1},
            {"name": "B", "price": 1000, "quantity": 1},
            {"name": "C", "price": 1000, "quantity": 1},
        ]
        start = client.post(
            f"/split/start?transaction_id={tx_id}",
            json=items_in,
            headers=auth_headers,
        )
        item_ids = [it["id"] for it in start.json()["items"]]

        # Assign only the first item
        client.post("/split/assign-item", json={
            "item_id": item_ids[0],
            "assignees": [{"person_id": yo_id, "split_type": "equal"}],
        }, headers=auth_headers)

        result = client.get(f"/split/result?transaction_id={tx_id}", headers=auth_headers).json()
        assert result["completion_pct"] == pytest.approx(33.3, abs=0.2)
        assert result["unassigned_total"] == 2000.0

    def test_all_assigned_100pct(self, client, auth_headers, yo_id):
        tx_id = _make_tx(client, auth_headers, amount=2000, merchant="Full100", date="2026-05-26")
        items_in = [
            {"name": "X", "price": 1000, "quantity": 1},
            {"name": "Y", "price": 1000, "quantity": 1},
        ]
        start = client.post(
            f"/split/start?transaction_id={tx_id}",
            json=items_in,
            headers=auth_headers,
        )
        for it in start.json()["items"]:
            client.post("/split/assign-item", json={
                "item_id": it["id"],
                "assignees": [{"person_id": yo_id, "split_type": "equal"}],
            }, headers=auth_headers)

        result = client.get(f"/split/result?transaction_id={tx_id}", headers=auth_headers).json()
        assert result["completion_pct"] == 100.0
        assert result["unassigned_total"] == 0.0

    def test_none_assigned_0pct(self, client, auth_headers, yo_id):
        tx_id = _make_tx(client, auth_headers, amount=5000, merchant="Zero0", date="2026-05-27")
        items_in = [{"name": "Unassigned", "price": 5000, "quantity": 1}]
        client.post(
            f"/split/start?transaction_id={tx_id}",
            json=items_in,
            headers=auth_headers,
        )

        result = client.get(f"/split/result?transaction_id={tx_id}", headers=auth_headers).json()
        assert result["completion_pct"] == 0.0
        assert result["unassigned_total"] == 5000.0
        assert result["people"] == []


# ════════════════════════════════════════════════════════════════════════════
# 8. Full flow: transaction with items → assign → settle (simulates OCR upload)
# ════════════════════════════════════════════════════════════════════════════

class TestFullReceiptFlow:
    def test_supermarket_receipt_3_people(self, client, auth_headers, yo_id):
        """
        Simulates: boleta supermercado subida, items parseados, 3 personas,
        cada una consume items distintos, settle verifica deudas mínimas.
        """
        ana = _make_person(client, auth_headers, "Ana")
        pedro = _make_person(client, auth_headers, "Pedro")

        # Simulate an existing transaction created after OCR (e.g. from /upload)
        tx_resp = client.post("/split/start-manual", json={
            "merchant": "Supermercado Santa Isabel",
            "total_amount": 27500,
            "currency": "CLP",
            "date": "2026-05-01",
        }, headers=auth_headers)
        assert tx_resp.status_code == 201
        tx_id = tx_resp.json()["transaction_id"]

        # Seed with parsed receipt items
        items_payload = [
            {"name": "Leche 1L x2", "price": 2500, "quantity": 2},
            {"name": "Pan Integral", "price": 1500, "quantity": 1},
            {"name": "Jugo Natural", "price": 3500, "quantity": 1},
            {"name": "Queso Gouda", "price": 4500, "quantity": 1},
            {"name": "Yogur Pack x4", "price": 3000, "quantity": 1},
            {"name": "Propina", "price": 1000, "quantity": 1},  # manual add
        ]
        start_resp = client.post(
            f"/split/start?transaction_id={tx_id}",
            json=items_payload,
            headers=auth_headers,
        )
        items = start_resp.json()["items"]
        # tx total after seeding (items replace single item): re-fetch
        result_before = client.get(f"/split/result?transaction_id={tx_id}", headers=auth_headers).json()
        # Items: 5000 + 1500 + 3500 + 4500 + 3000 + 1000 = 18500
        assert result_before["total_amount"] == 18500.0

        by_name = {it["name"]: it["id"] for it in items}

        # Ana: Leche + Pan
        client.post("/split/assign-item", json={
            "item_id": by_name["Leche 1L x2"],
            "assignees": [{"person_id": ana, "split_type": "equal"}],
        }, headers=auth_headers)
        client.post("/split/assign-item", json={
            "item_id": by_name["Pan Integral"],
            "assignees": [{"person_id": ana, "split_type": "equal"}],
        }, headers=auth_headers)

        # Pedro: Jugo + Queso
        client.post("/split/assign-item", json={
            "item_id": by_name["Jugo Natural"],
            "assignees": [{"person_id": pedro, "split_type": "equal"}],
        }, headers=auth_headers)
        client.post("/split/assign-item", json={
            "item_id": by_name["Queso Gouda"],
            "assignees": [{"person_id": pedro, "split_type": "equal"}],
        }, headers=auth_headers)

        # Yo: Yogur + Propina split equally with Ana
        client.post("/split/assign-item", json={
            "item_id": by_name["Yogur Pack x4"],
            "assignees": [{"person_id": yo_id, "split_type": "equal"}],
        }, headers=auth_headers)
        client.post("/split/assign-item", json={
            "item_id": by_name["Propina"],
            "assignees": [
                {"person_id": yo_id, "split_type": "equal"},
                {"person_id": ana, "split_type": "equal"},
            ],
        }, headers=auth_headers)

        result = client.get(f"/split/result?transaction_id={tx_id}", headers=auth_headers).json()
        assert result["completion_pct"] == 100.0
        assert result["unassigned_total"] == 0.0

        totals = {p["person_id"]: p["total"] for p in result["people"]}
        # Ana: 5000 + 1500 + 500 = 7000
        # Pedro: 3500 + 4500 = 8000
        # Yo: 3000 + 500 = 3500
        assert totals[ana] == 7000.0
        assert totals[pedro] == 8000.0
        assert totals[yo_id] == 3500.0
        assert sum(totals.values()) == 18500.0

        # Settle: yo paid for everyone
        accounts_resp = client.get("/accounts", headers=auth_headers)
        efectivo_id = next(a["id"] for a in accounts_resp.json() if a["name"] == "Efectivo")

        settle_resp = client.post("/split/settle", json={
            "transaction_id": tx_id,
            "payer_person_id": yo_id,
            "account_id": efectivo_id,
            "save_to_lucas": True,
        }, headers=auth_headers)
        assert settle_resp.status_code == 200
        settle = settle_resp.json()

        debts = {d["person_id"]: d["amount"] for d in settle["debts"]}
        assert debts[ana] == 7000.0
        assert debts[pedro] == 8000.0
        assert settle["my_total"] == 3500.0

    def test_restaurant_percent_tip_split(self, client, auth_headers, yo_id):
        """
        Restaurante: platos distintos + propina 10% dividida en porcentaje.
        """
        c1 = _make_person(client, auth_headers, "Carmen")
        c2 = _make_person(client, auth_headers, "Luis")

        tx_resp = client.post("/split/start-manual", json={
            "merchant": "Restaurante El Buen Sabor",
            "total_amount": 44000,
            "currency": "CLP",
            "date": "2026-05-02",
        }, headers=auth_headers)
        tx_id = tx_resp.json()["transaction_id"]

        items_payload = [
            {"name": "Lomo a lo pobre", "price": 14000, "quantity": 1},
            {"name": "Pastel de choclo", "price": 12000, "quantity": 1},
            {"name": "Cazuela de vacuno", "price": 11000, "quantity": 1},
            {"name": "Propina 10%", "price": 3700, "quantity": 1},
        ]
        start = client.post(
            f"/split/start?transaction_id={tx_id}",
            json=items_payload,
            headers=auth_headers,
        )
        items = start.json()["items"]
        by_name = {it["name"]: it["id"] for it in items}

        # Yo: Lomo
        client.post("/split/assign-item", json={
            "item_id": by_name["Lomo a lo pobre"],
            "assignees": [{"person_id": yo_id, "split_type": "equal"}],
        }, headers=auth_headers)
        # Carmen: Pastel
        client.post("/split/assign-item", json={
            "item_id": by_name["Pastel de choclo"],
            "assignees": [{"person_id": c1, "split_type": "equal"}],
        }, headers=auth_headers)
        # Luis: Cazuela
        client.post("/split/assign-item", json={
            "item_id": by_name["Cazuela de vacuno"],
            "assignees": [{"person_id": c2, "split_type": "equal"}],
        }, headers=auth_headers)
        # Propina: split by % proportional to dish price
        # yo 14k/37k ≈ 37.8%, carmen 12k/37k ≈ 32.4%, luis 11k/37k ≈ 29.7% (last gets remainder)
        client.post("/split/assign-item", json={
            "item_id": by_name["Propina 10%"],
            "assignees": [
                {"person_id": yo_id, "split_type": "percent", "value": 37},
                {"person_id": c1, "split_type": "percent", "value": 32},
                {"person_id": c2, "split_type": "percent", "value": 31},
            ],
        }, headers=auth_headers)

        result = client.get(f"/split/result?transaction_id={tx_id}", headers=auth_headers).json()
        totals = {p["person_id"]: p["total"] for p in result["people"]}
        assert result["completion_pct"] == 100.0
        # Sum must equal total_amount
        assert round(sum(totals.values()), 2) == result["total_amount"]
        # Each person pays their dish + their propina share
        tip = 3700
        assert totals[yo_id] == 14000 + round(tip * 0.37, 2)
        assert totals[c1] == 12000 + round(tip * 0.32, 2)
        # Luis gets remainder of tip
        luis_tip = round(tip - round(tip * 0.37, 2) - round(tip * 0.32, 2), 2)
        assert totals[c2] == 11000 + luis_tip

    def test_discount_item_reduces_all_shares(self, client, auth_headers, yo_id):
        """
        Boleta con descuento (-1000 CLP) asignado a todos por igual.
        El total neto es correcto y el descuento reduce las deudas.
        """
        d1 = _make_person(client, auth_headers, "DiscA")
        tx_id = _make_tx(client, auth_headers, amount=9000, merchant="ConDescuento", date="2026-05-03")

        items_payload = [
            {"name": "Producto A", "price": 5000, "quantity": 1},
            {"name": "Producto B", "price": 5000, "quantity": 1},
            {"name": "Descuento club", "price": -1000, "quantity": 1},
        ]
        start = client.post(
            f"/split/start?transaction_id={tx_id}",
            json=items_payload,
            headers=auth_headers,
        )
        items = start.json()["items"]
        by_name = {it["name"]: it["id"] for it in items}

        client.post("/split/assign-item", json={
            "item_id": by_name["Producto A"],
            "assignees": [{"person_id": yo_id, "split_type": "equal"}],
        }, headers=auth_headers)
        client.post("/split/assign-item", json={
            "item_id": by_name["Producto B"],
            "assignees": [{"person_id": d1, "split_type": "equal"}],
        }, headers=auth_headers)
        client.post("/split/assign-item", json={
            "item_id": by_name["Descuento club"],
            "assignees": [
                {"person_id": yo_id, "split_type": "equal"},
                {"person_id": d1, "split_type": "equal"},
            ],
        }, headers=auth_headers)

        result = client.get(f"/split/result?transaction_id={tx_id}", headers=auth_headers).json()
        totals = {p["person_id"]: p["total"] for p in result["people"]}
        # yo: 5000 - 500 = 4500; d1: 5000 - 500 = 4500
        assert totals[yo_id] == 4500.0
        assert totals[d1] == 4500.0
        assert result["total_amount"] == 9000.0


# ════════════════════════════════════════════════════════════════════════════
# 9. Quantity > 1 edge cases
# ════════════════════════════════════════════════════════════════════════════

class TestQuantityItems:
    def test_quantity_2_item_line_total_correct(self, client, auth_headers, yo_id):
        """item with price=3000, quantity=2 → line_total=6000."""
        tx_id = _make_tx(client, auth_headers, amount=6000, merchant="Qty2", date="2026-05-28")
        items_payload = [{"name": "Bebida x2", "price": 3000, "quantity": 2}]
        start = client.post(
            f"/split/start?transaction_id={tx_id}",
            json=items_payload,
            headers=auth_headers,
        )
        item = start.json()["items"][0]
        assert item["line_total"] == 6000.0

    def test_quantity_2_equal_split_2_people(self, client, auth_headers, yo_id):
        """quantity=2 item, equal split 2 people → each pays 3000."""
        a = _make_person(client, auth_headers, "QtyA")
        tx_id = _make_tx(client, auth_headers, amount=6000, merchant="Qty2Split", date="2026-05-28")
        items_payload = [{"name": "Pizza x2", "price": 3000, "quantity": 2}]
        start = client.post(
            f"/split/start?transaction_id={tx_id}",
            json=items_payload,
            headers=auth_headers,
        )
        item_id = start.json()["items"][0]["id"]

        client.post("/split/assign-item", json={
            "item_id": item_id,
            "assignees": [
                {"person_id": yo_id, "split_type": "equal"},
                {"person_id": a, "split_type": "equal"},
            ],
        }, headers=auth_headers)

        result = client.get(f"/split/result?transaction_id={tx_id}", headers=auth_headers).json()
        totals = {p["person_id"]: p["total"] for p in result["people"]}
        assert totals[yo_id] == 3000.0
        assert totals[a] == 3000.0

    def test_edit_quantity_updates_line_total(self, client, auth_headers, yo_id):
        tx_id = _make_tx(client, auth_headers, amount=3000, merchant="QtyEdit", date="2026-05-28")
        items_payload = [{"name": "Café", "price": 1500, "quantity": 2}]
        start = client.post(
            f"/split/start?transaction_id={tx_id}",
            json=items_payload,
            headers=auth_headers,
        )
        item_id = start.json()["items"][0]["id"]

        # Change quantity from 2 to 3
        patch_resp = client.patch(f"/split/items/{item_id}", json={"quantity": 3}, headers=auth_headers)
        assert patch_resp.status_code == 200
        assert patch_resp.json()["line_total"] == 4500.0
