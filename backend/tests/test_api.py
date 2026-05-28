"""
Integration tests using FastAPI TestClient + SQLite in-memory.
These tests exercise the real route handlers, auth middleware, and service layer.

Run with:
    pytest tests/test_api.py -v
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ── Stub out heavy optional dependencies before any app code loads ──────────
import unittest.mock as _mock


# ── Proper slowapi stubs so @limiter.limit() acts as a passthrough ──────────
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

# ── Environment variables must be set before importing app modules ──────────
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["JWT_SECRET"] = "test-secret-for-integration-tests"
os.environ["AI_PROVIDER"] = "none"
os.environ["ALLOW_PASSWORDLESS"] = "false"
os.environ["CORS_ORIGINS"] = "http://localhost:3000"
os.environ["STORAGE_BACKEND"] = "local"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Must import after env vars are set
from app.main import app
from app.database import Base, get_db
from app import models as _models  # noqa: F401 — registers all ORM models with Base.metadata

# ── SQLite in-memory with StaticPool so ALL connections share the same DB ────
# Without StaticPool, each connect() call gets a fresh empty database.
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

# Create all tables (models are registered via the import above)
Base.metadata.create_all(TEST_ENGINE)


def override_get_db():
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    """Single TestClient shared across the whole module."""
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def auth_headers(client):
    """
    Register a fresh user and return JWT Authorization headers.
    Used by every split / protected endpoint test.
    """
    resp = client.post(
        "/auth/signup",
        json={"email": "integration@test.com", "password": "Test1234!", "locale": "es"},
    )
    assert resp.status_code == 200, f"signup failed: {resp.text}"
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# ════════════════════════════════════════════════════════════════════════════
# Auth tests
# ════════════════════════════════════════════════════════════════════════════

class TestAuthSignup:
    def test_signup_new_user(self, client):
        resp = client.post(
            "/auth/signup",
            json={"email": "newuser@example.com", "password": "Secure123!", "locale": "es"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert "access_token" in body, "Response should contain access_token"
        assert body["token_type"] == "bearer"
        assert "user" in body
        assert body["user"]["email"] == "newuser@example.com"

    def test_signup_duplicate_email_returns_400(self, client):
        # First signup
        client.post(
            "/auth/signup",
            json={"email": "dupe@example.com", "password": "First1234!", "locale": "es"},
        )
        # Second signup with same email and a password → should be rejected
        resp = client.post(
            "/auth/signup",
            json={"email": "dupe@example.com", "password": "Second1234!", "locale": "es"},
        )
        assert resp.status_code == 400, (
            f"Expected 400 for duplicate email, got {resp.status_code}: {resp.text}"
        )

    def test_signup_seeds_clp_currency_for_es_locale(self, client):
        resp = client.post(
            "/auth/signup",
            json={"email": "chilean@example.com", "password": "Test1234!", "locale": "es"},
        )
        assert resp.status_code == 200
        user_settings = resp.json()["user"]["settings"]
        assert user_settings.get("currency") == "CLP", (
            "Spanish locale should default to CLP currency"
        )


class TestAuthLogin:
    def test_login_valid_credentials(self, client):
        # Register first
        client.post(
            "/auth/signup",
            json={"email": "logintest@example.com", "password": "Login1234!", "locale": "es"},
        )
        # Login via form encoding (OAuth2PasswordRequestForm)
        resp = client.post(
            "/auth/login",
            data={"username": "logintest@example.com", "password": "Login1234!"},
        )
        assert resp.status_code == 200, f"Login failed: {resp.text}"
        body = resp.json()
        assert "access_token" in body, "Login should return access_token"
        assert body["user"]["email"] == "logintest@example.com"

    def test_login_wrong_password_returns_401(self, client):
        client.post(
            "/auth/signup",
            json={"email": "wrongpw@example.com", "password": "Correct1234!", "locale": "es"},
        )
        resp = client.post(
            "/auth/login",
            data={"username": "wrongpw@example.com", "password": "WrongPassword!"},
        )
        assert resp.status_code == 401, (
            f"Expected 401 for wrong password, got {resp.status_code}"
        )

    def test_login_unknown_email_returns_401(self, client):
        resp = client.post(
            "/auth/login",
            data={"username": "nobody@example.com", "password": "Test1234!"},
        )
        assert resp.status_code == 401, (
            f"Expected 401 for unknown email, got {resp.status_code}"
        )


class TestAuthMe:
    def test_get_me_with_token(self, client, auth_headers):
        resp = client.get("/auth/me", headers=auth_headers)
        assert resp.status_code == 200, f"GET /auth/me failed: {resp.text}"
        body = resp.json()
        assert body["email"] == "integration@test.com"
        assert "id" in body
        assert "monthly_budget" in body

    def test_get_me_without_token_returns_401(self, client):
        resp = client.get("/auth/me")
        assert resp.status_code == 401, (
            f"Expected 401 without token, got {resp.status_code}"
        )

    def test_get_me_invalid_token_returns_401(self, client):
        resp = client.get("/auth/me", headers={"Authorization": "Bearer totally-fake-token"})
        assert resp.status_code == 401, (
            f"Expected 401 for invalid token, got {resp.status_code}"
        )

    def test_patch_me_updates_budget(self, client, auth_headers):
        resp = client.patch(
            "/auth/me",
            json={"monthly_budget": 500000.0},
            headers=auth_headers,
        )
        assert resp.status_code == 200, f"PATCH /auth/me failed: {resp.text}"
        body = resp.json()
        assert body["monthly_budget"] == 500000.0, (
            f"monthly_budget should be updated to 500000, got {body['monthly_budget']}"
        )

    def test_patch_me_updates_settings(self, client, auth_headers):
        resp = client.patch(
            "/auth/me",
            json={"settings": {"currency": "USD"}},
            headers=auth_headers,
        )
        assert resp.status_code == 200, f"PATCH /auth/me settings failed: {resp.text}"
        # Settings should be merged, not replaced
        assert resp.json()["settings"]["currency"] == "USD"


# ════════════════════════════════════════════════════════════════════════════
# Split tests
# ════════════════════════════════════════════════════════════════════════════

class TestSplitPeople:
    def test_get_me_person_creates_yo(self, client, auth_headers):
        resp = client.get("/split/me", headers=auth_headers)
        assert resp.status_code == 200, f"GET /split/me failed: {resp.text}"
        body = resp.json()
        assert body["name"] == "Yo", f"Expected 'Yo', got '{body['name']}'"
        assert body["is_me"] is True, "GET /split/me should return is_me=True"
        assert "id" in body

    def test_get_me_person_idempotent(self, client, auth_headers):
        """Calling /split/me twice should return the same person."""
        r1 = client.get("/split/me", headers=auth_headers)
        r2 = client.get("/split/me", headers=auth_headers)
        assert r1.json()["id"] == r2.json()["id"], "Yo person should be created only once"

    def test_create_person(self, client, auth_headers):
        resp = client.post(
            "/split/people",
            json={"name": "María", "color": "#ef4444"},
            headers=auth_headers,
        )
        assert resp.status_code == 201, f"POST /split/people failed: {resp.text}"
        body = resp.json()
        assert body["name"] == "María"
        assert body["color"] == "#ef4444"
        assert body["is_me"] is False, "Manually created person should not be is_me"

    def test_list_people_includes_yo_first(self, client, auth_headers):
        # Ensure Yo exists
        client.get("/split/me", headers=auth_headers)
        resp = client.get("/split/people", headers=auth_headers)
        assert resp.status_code == 200, f"GET /split/people failed: {resp.text}"
        people = resp.json()
        assert len(people) >= 1, "Should have at least Yo"
        assert people[0]["is_me"] is True, "First person in list should be Yo (is_me=True)"

    def test_delete_person(self, client, auth_headers):
        # Create a throwaway person
        create_resp = client.post(
            "/split/people",
            json={"name": "Throwaway", "color": "#10b981"},
            headers=auth_headers,
        )
        assert create_resp.status_code == 201
        person_id = create_resp.json()["id"]

        # Delete them
        del_resp = client.delete(f"/split/people/{person_id}", headers=auth_headers)
        assert del_resp.status_code == 204, (
            f"DELETE /split/people/{person_id} failed: {del_resp.text}"
        )

        # Verify no longer in list
        list_resp = client.get("/split/people", headers=auth_headers)
        ids = [p["id"] for p in list_resp.json()]
        assert person_id not in ids, "Deleted person should not appear in list"

    def test_cannot_delete_yo(self, client, auth_headers):
        me_resp = client.get("/split/me", headers=auth_headers)
        me_id = me_resp.json()["id"]

        del_resp = client.delete(f"/split/people/{me_id}", headers=auth_headers)
        assert del_resp.status_code == 400, (
            f"Expected 400 when deleting Yo, got {del_resp.status_code}: {del_resp.text}"
        )

    def test_delete_nonexistent_person_returns_404(self, client, auth_headers):
        resp = client.delete("/split/people/999999", headers=auth_headers)
        assert resp.status_code == 404, (
            f"Expected 404 for missing person, got {resp.status_code}"
        )


class TestSplitSession:
    """Tests around start-manual → assign-item → result → settle flow."""

    _MANUAL_PAYLOAD = {
        "total_amount": 10000,
        "currency": "CLP",
        "date": "2026-01-15",
        "merchant": "Test Restaurant",
    }

    def test_start_manual_split(self, client, auth_headers):
        resp = client.post(
            "/split/start-manual",
            json=self._MANUAL_PAYLOAD,
            headers=auth_headers,
        )
        assert resp.status_code == 201, f"POST /split/start-manual failed: {resp.text}"
        body = resp.json()
        assert "transaction_id" in body, "Response should contain transaction_id"
        assert "items" in body
        assert len(body["items"]) == 1, "Manual split should create exactly one item"
        item = body["items"][0]
        assert item["price"] == 10000
        assert item["name"] == "Test Restaurant"

    def test_split_result_empty_people(self, client, auth_headers):
        """A fresh split with unassigned items should have completion_pct=0, not 100."""
        start_resp = client.post(
            "/split/start-manual",
            json={**self._MANUAL_PAYLOAD, "merchant": "Empty Split Test"},
            headers=auth_headers,
        )
        assert start_resp.status_code == 201
        tx_id = start_resp.json()["transaction_id"]

        result_resp = client.get(
            f"/split/result?transaction_id={tx_id}",
            headers=auth_headers,
        )
        assert result_resp.status_code == 200, f"GET /split/result failed: {result_resp.text}"
        body = result_resp.json()
        assert body["transaction_id"] == tx_id
        assert body["completion_pct"] == 0.0, (
            f"Unassigned split should have 0% completion, got {body['completion_pct']}"
        )
        assert body["total_amount"] == 10000.0
        assert body["unassigned_total"] == 10000.0
        assert body["people"] == [], "No person totals when nothing is assigned"

    def test_assign_item_and_check_result(self, client, auth_headers):
        """Create a split, assign the item to Yo, verify result shows Yo's total."""
        # Get Yo's person id
        me_resp = client.get("/split/me", headers=auth_headers)
        yo_id = me_resp.json()["id"]

        # Create a split
        start_resp = client.post(
            "/split/start-manual",
            json={**self._MANUAL_PAYLOAD, "merchant": "Assign Test"},
            headers=auth_headers,
        )
        assert start_resp.status_code == 201
        body = start_resp.json()
        tx_id = body["transaction_id"]
        item_id = body["items"][0]["id"]

        # Assign item to Yo
        assign_resp = client.post(
            "/split/assign-item",
            json={
                "item_id": item_id,
                "assignees": [{"person_id": yo_id, "split_type": "equal", "value": None}],
            },
            headers=auth_headers,
        )
        assert assign_resp.status_code == 200, (
            f"POST /split/assign-item failed: {assign_resp.text}"
        )
        assigned_item = assign_resp.json()
        assert len(assigned_item["assignees"]) == 1
        assert assigned_item["assignees"][0]["person_id"] == yo_id

        # Check result
        result_resp = client.get(
            f"/split/result?transaction_id={tx_id}",
            headers=auth_headers,
        )
        assert result_resp.status_code == 200
        result = result_resp.json()
        assert result["completion_pct"] == 100.0, (
            f"All items assigned → completion should be 100%, got {result['completion_pct']}"
        )
        assert len(result["people"]) == 1
        assert result["people"][0]["person_id"] == yo_id
        assert result["people"][0]["total"] == 10000.0

    def test_assign_item_to_multiple_people_equal_split(self, client, auth_headers):
        """Two people share an item equally — each gets half."""
        me_resp = client.get("/split/me", headers=auth_headers)
        yo_id = me_resp.json()["id"]

        friend_resp = client.post(
            "/split/people",
            json={"name": "AmigoDividir", "color": "#f97316"},
            headers=auth_headers,
        )
        friend_id = friend_resp.json()["id"]

        start_resp = client.post(
            "/split/start-manual",
            json={**self._MANUAL_PAYLOAD, "total_amount": 20000, "merchant": "Equal Split Test"},
            headers=auth_headers,
        )
        tx_id = start_resp.json()["transaction_id"]
        item_id = start_resp.json()["items"][0]["id"]

        assign_resp = client.post(
            "/split/assign-item",
            json={
                "item_id": item_id,
                "assignees": [
                    {"person_id": yo_id, "split_type": "equal", "value": None},
                    {"person_id": friend_id, "split_type": "equal", "value": None},
                ],
            },
            headers=auth_headers,
        )
        assert assign_resp.status_code == 200
        assignees = assign_resp.json()["assignees"]
        totals = {a["person_id"]: a["computed_amount"] for a in assignees}
        assert totals[yo_id] == 10000.0, f"Yo should owe 10000, got {totals[yo_id]}"
        assert totals[friend_id] == 10000.0, f"Friend should owe 10000, got {totals[friend_id]}"

    def test_add_discount_item_negative_price(self, client, auth_headers):
        """Adding an item with negative price (discount) reduces the transaction total."""
        start_resp = client.post(
            "/split/start-manual",
            json={**self._MANUAL_PAYLOAD, "total_amount": 10000, "merchant": "Descuento Test"},
            headers=auth_headers,
        )
        assert start_resp.status_code == 201
        tx_id = start_resp.json()["transaction_id"]

        add_resp = client.post(
            f"/split/add-item?transaction_id={tx_id}",
            json={"name": "Descuento 10%", "price": -1000, "quantity": 1},
            headers=auth_headers,
        )
        assert add_resp.status_code == 201, (
            f"POST /split/add-item failed: {add_resp.text}"
        )
        item_out = add_resp.json()
        assert item_out["price"] == -1000.0
        assert item_out["line_total"] == -1000.0

        # Result should have two items, net total = 9000
        result_resp = client.get(
            f"/split/result?transaction_id={tx_id}",
            headers=auth_headers,
        )
        assert result_resp.status_code == 200
        result = result_resp.json()
        assert len(result["items"]) == 2, "Should have original item + discount item"
        assert result["total_amount"] == 9000.0, (
            f"Net total after discount should be 9000, got {result['total_amount']}"
        )

    def test_delete_split_item(self, client, auth_headers):
        """Deleting a split item removes it from the result."""
        start_resp = client.post(
            "/split/start-manual",
            json={**self._MANUAL_PAYLOAD, "merchant": "Delete Item Test"},
            headers=auth_headers,
        )
        assert start_resp.status_code == 201
        tx_id = start_resp.json()["transaction_id"]
        item_id = start_resp.json()["items"][0]["id"]

        # Add a second item so we can delete one without emptying the split
        add_resp = client.post(
            f"/split/add-item?transaction_id={tx_id}",
            json={"name": "Extra Item", "price": 2000, "quantity": 1},
            headers=auth_headers,
        )
        assert add_resp.status_code == 201
        extra_id = add_resp.json()["id"]

        # Delete the extra item
        del_resp = client.delete(f"/split/items/{extra_id}", headers=auth_headers)
        assert del_resp.status_code == 204, (
            f"DELETE /split/items/{extra_id} failed: {del_resp.text}"
        )

        # Verify only original item remains
        result_resp = client.get(
            f"/split/result?transaction_id={tx_id}",
            headers=auth_headers,
        )
        assert result_resp.status_code == 200
        item_ids = [it["id"] for it in result_resp.json()["items"]]
        assert extra_id not in item_ids, "Deleted item should not appear in result"
        assert item_id in item_ids, "Original item should still be present"

    def test_delete_nonexistent_split_item_returns_404(self, client, auth_headers):
        resp = client.delete("/split/items/999999", headers=auth_headers)
        assert resp.status_code == 404, (
            f"Expected 404 for missing item, got {resp.status_code}"
        )

    def test_assign_item_invalid_split_type_returns_400(self, client, auth_headers):
        me_resp = client.get("/split/me", headers=auth_headers)
        yo_id = me_resp.json()["id"]

        start_resp = client.post(
            "/split/start-manual",
            json={**self._MANUAL_PAYLOAD, "merchant": "Bad Split Type Test"},
            headers=auth_headers,
        )
        item_id = start_resp.json()["items"][0]["id"]

        resp = client.post(
            "/split/assign-item",
            json={
                "item_id": item_id,
                "assignees": [{"person_id": yo_id, "split_type": "magic", "value": None}],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 400, (
            f"Expected 400 for invalid split_type, got {resp.status_code}"
        )

    def test_settle_returns_debts(self, client, auth_headers):
        """settle endpoint should return payer info and debt rows."""
        me_resp = client.get("/split/me", headers=auth_headers)
        yo_id = me_resp.json()["id"]

        friend_resp = client.post(
            "/split/people",
            json={"name": "SettleTestFriend", "color": "#06b6d4"},
            headers=auth_headers,
        )
        friend_id = friend_resp.json()["id"]

        start_resp = client.post(
            "/split/start-manual",
            json={**self._MANUAL_PAYLOAD, "total_amount": 6000, "merchant": "Settle Test"},
            headers=auth_headers,
        )
        tx_id = start_resp.json()["transaction_id"]
        item_id = start_resp.json()["items"][0]["id"]

        client.post(
            "/split/assign-item",
            json={
                "item_id": item_id,
                "assignees": [
                    {"person_id": yo_id, "split_type": "equal", "value": None},
                    {"person_id": friend_id, "split_type": "equal", "value": None},
                ],
            },
            headers=auth_headers,
        )

        settle_resp = client.post(
            "/split/settle",
            json={"transaction_id": tx_id, "save_to_lucas": False},
            headers=auth_headers,
        )
        assert settle_resp.status_code == 200, (
            f"POST /split/settle failed: {settle_resp.text}"
        )
        body = settle_resp.json()
        assert "payer_person_id" in body
        assert "debts" in body
        assert body["my_total"] == 3000.0, (
            f"Yo's share should be 3000 (half of 6000), got {body['my_total']}"
        )


# ════════════════════════════════════════════════════════════════════════════
# Transaction tests
# ════════════════════════════════════════════════════════════════════════════

class TestTransactions:
    _TX_PAYLOAD = {
        "amount": 15000.0,
        "currency": "CLP",
        "category": "Alimentación",
        "date": "2026-01-20",
        "merchant": "Supermercado Lider",
        "notes": "Compra semanal",
        "is_income": False,
    }

    def test_create_transaction(self, client, auth_headers):
        resp = client.post(
            "/transactions",
            json=self._TX_PAYLOAD,
            headers=auth_headers,
        )
        assert resp.status_code == 201, f"POST /transactions failed: {resp.text}"
        body = resp.json()
        assert body["amount"] == 15000.0
        assert body["merchant"] == "Supermercado Lider"
        assert body["category"] == "Alimentación"
        assert body["is_income"] is False
        assert "id" in body
        assert "created_at" in body

    def test_list_transactions_returns_user_transactions(self, client, auth_headers):
        # Create a known transaction
        client.post("/transactions", json=self._TX_PAYLOAD, headers=auth_headers)

        resp = client.get("/transactions", headers=auth_headers)
        assert resp.status_code == 200, f"GET /transactions failed: {resp.text}"
        transactions = resp.json()
        assert isinstance(transactions, list), "Should return a list"
        assert len(transactions) >= 1, "Should have at least one transaction"
        # All returned transactions belong to the authenticated user (enforced by the router)
        merchants = [tx["merchant"] for tx in transactions]
        assert "Supermercado Lider" in merchants

    def test_list_transactions_unauthenticated_returns_401(self, client):
        resp = client.get("/transactions")
        assert resp.status_code == 401

    def test_create_income_transaction(self, client, auth_headers):
        resp = client.post(
            "/transactions",
            json={**self._TX_PAYLOAD, "amount": 800000.0, "is_income": True, "merchant": "Sueldo"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["is_income"] is True

    def test_delete_transaction(self, client, auth_headers):
        create_resp = client.post(
            "/transactions",
            json={**self._TX_PAYLOAD, "merchant": "ToDelete"},
            headers=auth_headers,
        )
        assert create_resp.status_code == 201
        tx_id = create_resp.json()["id"]

        del_resp = client.delete(f"/transactions/{tx_id}", headers=auth_headers)
        assert del_resp.status_code == 204, (
            f"DELETE /transactions/{tx_id} failed: {del_resp.text}"
        )

        # Verify it no longer appears
        list_resp = client.get("/transactions", headers=auth_headers)
        ids = [tx["id"] for tx in list_resp.json()]
        assert tx_id not in ids, "Deleted transaction should not appear in list"

    def test_delete_nonexistent_transaction_returns_404(self, client, auth_headers):
        resp = client.delete("/transactions/999999", headers=auth_headers)
        assert resp.status_code == 404

    def test_duplicate_transaction_within_60s_returns_409(self, client, auth_headers):
        """Same user + date + amount + merchant + is_income within 60 s → 409."""
        payload = {**self._TX_PAYLOAD, "merchant": "DupeMerchant", "date": "2026-02-01"}
        r1 = client.post("/transactions", json=payload, headers=auth_headers)
        assert r1.status_code == 201
        r2 = client.post("/transactions", json=payload, headers=auth_headers)
        assert r2.status_code == 409, (
            f"Expected 409 for duplicate, got {r2.status_code}: {r2.text}"
        )
        assert r2.json()["detail"]["detail"] == "duplicate_transaction"


# ════════════════════════════════════════════════════════════════════════════
# Account tests
# ════════════════════════════════════════════════════════════════════════════

class TestAccounts:
    _ACCOUNT_PAYLOAD = {
        "name": "Santander Débito",
        "bank": "Santander",
        "type": "debit",
        "currency": "CLP",
        "color": "#6366f1",
        "icon": "card",
        "credit_limit": 0.0,
        "anchor_balance": 0.0,
    }

    def test_create_account(self, client, auth_headers):
        resp = client.post(
            "/accounts",
            json=self._ACCOUNT_PAYLOAD,
            headers=auth_headers,
        )
        assert resp.status_code == 201, f"POST /accounts failed: {resp.text}"
        body = resp.json()
        assert body["name"] == "Santander Débito"
        assert body["bank"] == "Santander"
        assert body["type"] == "debit"
        assert "id" in body
        assert "current_balance" in body
        assert "current_used" in body
        assert "available_credit" in body

    def test_create_credit_account(self, client, auth_headers):
        resp = client.post(
            "/accounts",
            json={
                **self._ACCOUNT_PAYLOAD,
                "name": "CMR Falabella",
                "bank": "Falabella",
                "type": "credit",
                "credit_limit": 500000.0,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201, f"POST /accounts (credit) failed: {resp.text}"
        body = resp.json()
        assert body["type"] == "credit"
        assert body["credit_limit"] == 500000.0
        assert body["available_credit"] == 500000.0, (
            "Available credit should equal limit when no expenses are posted"
        )

    def test_create_account_invalid_type_returns_400(self, client, auth_headers):
        resp = client.post(
            "/accounts",
            json={**self._ACCOUNT_PAYLOAD, "type": "bitcoin_wallet"},
            headers=auth_headers,
        )
        assert resp.status_code == 400, (
            f"Expected 400 for invalid account type, got {resp.status_code}"
        )

    def test_list_accounts(self, client, auth_headers):
        # Ensure at least one account exists
        client.post("/accounts", json=self._ACCOUNT_PAYLOAD, headers=auth_headers)

        resp = client.get("/accounts", headers=auth_headers)
        assert resp.status_code == 200, f"GET /accounts failed: {resp.text}"
        accounts = resp.json()
        assert isinstance(accounts, list)
        assert len(accounts) >= 1
        names = [a["name"] for a in accounts]
        assert "Santander Débito" in names

    def test_list_accounts_unauthenticated_returns_401(self, client):
        resp = client.get("/accounts")
        assert resp.status_code == 401

    def test_update_account(self, client, auth_headers):
        create_resp = client.post(
            "/accounts",
            json={**self._ACCOUNT_PAYLOAD, "name": "PatchMe Bank"},
            headers=auth_headers,
        )
        assert create_resp.status_code == 201
        acc_id = create_resp.json()["id"]

        patch_resp = client.patch(
            f"/accounts/{acc_id}",
            json={"name": "Patched Bank", "anchor_balance": 100000.0},
            headers=auth_headers,
        )
        assert patch_resp.status_code == 200, (
            f"PATCH /accounts/{acc_id} failed: {patch_resp.text}"
        )
        assert patch_resp.json()["name"] == "Patched Bank"
        assert patch_resp.json()["anchor_balance"] == 100000.0

    def test_delete_account(self, client, auth_headers):
        create_resp = client.post(
            "/accounts",
            json={**self._ACCOUNT_PAYLOAD, "name": "ToDeleteAccount"},
            headers=auth_headers,
        )
        assert create_resp.status_code == 201
        acc_id = create_resp.json()["id"]

        del_resp = client.delete(f"/accounts/{acc_id}", headers=auth_headers)
        assert del_resp.status_code == 204, (
            f"DELETE /accounts/{acc_id} failed: {del_resp.text}"
        )

        list_resp = client.get("/accounts", headers=auth_headers)
        ids = [a["id"] for a in list_resp.json()]
        assert acc_id not in ids, "Deleted account should not appear in list"

    def test_delete_nonexistent_account_returns_404(self, client, auth_headers):
        resp = client.delete("/accounts/999999", headers=auth_headers)
        assert resp.status_code == 404

    def test_delete_account_detaches_transactions(self, client, auth_headers):
        """Deleting an account should not delete its transactions — just detach them."""
        acc_resp = client.post(
            "/accounts",
            json={**self._ACCOUNT_PAYLOAD, "name": "AccountWithTx"},
            headers=auth_headers,
        )
        acc_id = acc_resp.json()["id"]

        tx_resp = client.post(
            "/transactions",
            json={
                "amount": 5000.0,
                "currency": "CLP",
                "category": "Otros",
                "date": "2026-01-10",
                "merchant": "AccountTxTest",
                "notes": "",
                "is_income": False,
                "account_id": acc_id,
            },
            headers=auth_headers,
        )
        assert tx_resp.status_code == 201
        tx_id = tx_resp.json()["id"]

        # Delete the account
        client.delete(f"/accounts/{acc_id}", headers=auth_headers)

        # Transaction should still exist but with no account
        list_resp = client.get("/transactions", headers=auth_headers)
        tx_ids = [tx["id"] for tx in list_resp.json()]
        assert tx_id in tx_ids, "Transaction should survive account deletion"
        tx_data = next(tx for tx in list_resp.json() if tx["id"] == tx_id)
        assert tx_data["account_id"] is None, "account_id should be null after account deletion"


# ════════════════════════════════════════════════════════════════════════════
# Health check
# ════════════════════════════════════════════════════════════════════════════

class TestHealth:
    def test_health_endpoint(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["service"] == "lucas"
