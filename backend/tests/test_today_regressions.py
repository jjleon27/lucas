"""
Regression tests for 8 bug fixes made on 2026-05-27.

Run with:
    cd /Users/kako2/Documents/lucas/backend
    python3 -m pytest tests/test_today_regressions.py -v
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
os.environ["JWT_SECRET"] = "test-secret-regression"
os.environ["AI_PROVIDER"] = "none"
os.environ["ALLOW_PASSWORDLESS"] = "false"
os.environ["CORS_ORIGINS"] = "http://localhost:3000"
os.environ["STORAGE_BACKEND"] = "local"

import pytest
from datetime import date
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from pydantic import ValidationError

# Must import after env vars are set
from app.main import app
from app.database import Base, get_db
from app import models as _models  # noqa: F401 — registers all ORM models with Base.metadata

# ── SQLite in-memory with StaticPool so ALL connections share the same DB ────
APP_ENGINE = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(APP_ENGINE, "connect")
def _set_sqlite_pragma(dbapi_conn, _record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


TestSession = sessionmaker(bind=APP_ENGINE, autoflush=False, autocommit=False)

# Create all tables
Base.metadata.create_all(APP_ENGINE)


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
    with TestClient(app) as c:
        yield c


def _signup(client, email, password="Test1234!"):
    """Helper: sign up a user and return (token, headers)."""
    resp = client.post(
        "/auth/signup",
        json={"email": email, "password": password, "locale": "es"},
    )
    assert resp.status_code == 200, f"signup failed: {resp.text}"
    token = resp.json()["access_token"]
    return token, {"Authorization": f"Bearer {token}"}


def _login(client, email, password="Test1234!"):
    """Helper: log in a user and return (token, headers)."""
    resp = client.post(
        "/auth/login",
        data={"username": email, "password": password},
    )
    assert resp.status_code == 200, f"login failed: {resp.text}"
    token = resp.json()["access_token"]
    return token, {"Authorization": f"Bearer {token}"}


# ═══════════════════════════════════════════════════════════════════════════
# Bug 1 — 422 on PATCH /transactions/{id} with date field
# TransactionUpdate.date was resolving to NoneType due to field/type shadowing.
# Fix: _date = date alias in schemas.py.
# ═══════════════════════════════════════════════════════════════════════════

class TestPatchTransactionWithDate:
    def test_patch_transaction_with_date_returns_200(self, client):
        """PATCH /transactions/{id} sending category + date must return 200, not 422."""
        _, headers = _signup(client, "patch_date_test@regression.com")

        # Create a transaction first
        create_resp = client.post(
            "/transactions",
            json={
                "amount": 10000.0,
                "currency": "CLP",
                "category": "Alimentación",
                "date": "2026-05-01",
                "merchant": "Test Merchant",
                "notes": "",
                "is_income": False,
            },
            headers=headers,
        )
        assert create_resp.status_code == 201, f"create failed: {create_resp.text}"
        tx_id = create_resp.json()["id"]

        # PATCH with both category and date — this was 422 before the fix
        patch_resp = client.patch(
            f"/transactions/{tx_id}",
            json={"category": "Salud", "date": "2026-05-27"},
            headers=headers,
        )
        assert patch_resp.status_code == 200, (
            f"Expected 200 on PATCH with date, got {patch_resp.status_code}: {patch_resp.text}"
        )
        body = patch_resp.json()
        assert body["category"] == "Salud"
        assert body["date"] == "2026-05-27"


# ═══════════════════════════════════════════════════════════════════════════
# Bug 2 — Efectivo account auto-created on signup
# ═══════════════════════════════════════════════════════════════════════════

class TestEfectivoCreatedOnSignup:
    def test_signup_creates_efectivo_account(self, client):
        """After POST /auth/signup, GET /accounts must contain exactly one cash account named Efectivo."""
        _, headers = _signup(client, "efectivo_signup@regression.com")

        resp = client.get("/accounts", headers=headers)
        assert resp.status_code == 200, f"GET /accounts failed: {resp.text}"
        accounts = resp.json()

        cash_accounts = [a for a in accounts if a["type"] == "cash"]
        assert len(cash_accounts) == 1, (
            f"Expected exactly 1 cash account after signup, got {len(cash_accounts)}: {cash_accounts}"
        )
        assert cash_accounts[0]["name"] == "Efectivo", (
            f"Cash account should be named 'Efectivo', got '{cash_accounts[0]['name']}'"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Bug 3 — Efectivo created on login for existing users who didn't have it
# ═══════════════════════════════════════════════════════════════════════════

class TestEfectivoCreatedOnLoginForExistingUser:
    def test_login_creates_efectivo_for_legacy_user(self, client):
        """
        Simulate a legacy user: insert via raw DB (no Efectivo), then login.
        After login, Efectivo must appear.
        """
        import app.auth as _auth

        # Insert user directly without going through signup (no Efectivo created)
        db = TestSession()
        try:
            legacy_email = "legacy_no_efectivo@regression.com"
            # Ensure the user doesn't already exist
            existing = db.query(_models.User).filter(_models.User.email == legacy_email).first()
            if not existing:
                user = _models.User(
                    email=legacy_email,
                    hashed_password=_auth.hash_password("Test1234!"),
                    auth_provider="password",
                    monthly_budget=0.0,
                    settings={"currency": "CLP", "locale": "es"},
                    email_token=None,
                )
                db.add(user)
                db.commit()
                db.refresh(user)
                user_id = user.id
            else:
                user_id = existing.id
        finally:
            db.close()

        # Verify no cash account exists yet (only relevant if we just created the user)
        db2 = TestSession()
        try:
            cash_before = db2.query(_models.Account).filter(
                _models.Account.user_id == user_id,
                _models.Account.type == "cash",
            ).count()
        finally:
            db2.close()

        assert cash_before == 0, (
            "Precondition: legacy user should have no cash account before login"
        )

        # Now login — this should trigger _ensure_efectivo_account
        resp = client.post(
            "/auth/login",
            data={"username": legacy_email, "password": "Test1234!"},
        )
        assert resp.status_code == 200, f"login failed: {resp.text}"

        token = resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        accounts_resp = client.get("/accounts", headers=headers)
        assert accounts_resp.status_code == 200
        accounts = accounts_resp.json()

        cash_accounts = [a for a in accounts if a["type"] == "cash"]
        assert len(cash_accounts) >= 1, (
            f"Efectivo should appear after login for legacy user, got cash accounts: {cash_accounts}"
        )
        assert cash_accounts[0]["name"] == "Efectivo"


# ═══════════════════════════════════════════════════════════════════════════
# Bug 4 — Efectivo not duplicated on multiple logins
# ═══════════════════════════════════════════════════════════════════════════

class TestEfectivoNotDuplicatedOnMultipleLogins:
    def test_multiple_logins_dont_duplicate_efectivo(self, client):
        """Login 3 times → still exactly 1 cash account."""
        email = "multi_login@regression.com"
        _signup(client, email)

        # Login 3 more times
        for i in range(3):
            resp = client.post(
                "/auth/login",
                data={"username": email, "password": "Test1234!"},
            )
            assert resp.status_code == 200, f"login {i+1} failed: {resp.text}"

        # Check accounts
        _, headers = _login(client, email)
        accounts_resp = client.get("/accounts", headers=headers)
        assert accounts_resp.status_code == 200
        accounts = accounts_resp.json()

        cash_accounts = [a for a in accounts if a["type"] == "cash"]
        assert len(cash_accounts) == 1, (
            f"Expected exactly 1 cash account after 3 logins, got {len(cash_accounts)}: {cash_accounts}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Bug 5 — Review confirm with account_id sets transaction.account_id
# ═══════════════════════════════════════════════════════════════════════════

class TestReviewConfirmSetsAccountId:
    def test_confirm_review_sets_account_id(self, client):
        """
        POST /email/review/{tx_id} with action=confirm + account_id must
        set transaction.account_id to the given value.
        """
        import app.auth as _auth
        import secrets

        email = "review_confirm@regression.com"
        _, headers = _signup(client, email)

        # Get user id from /auth/me
        me_resp = client.get("/auth/me", headers=headers)
        user_id = me_resp.json()["id"]

        # Create an account to assign
        acc_resp = client.post(
            "/accounts",
            json={
                "name": "BCI Débito",
                "bank": "BCI",
                "type": "debit",
                "currency": "CLP",
                "color": "#6366f1",
                "icon": "card",
                "credit_limit": 0.0,
                "anchor_balance": 0.0,
            },
            headers=headers,
        )
        assert acc_resp.status_code == 201, f"create account failed: {acc_resp.text}"
        account_id = acc_resp.json()["id"]

        # Insert a pending_review transaction directly in the DB
        db = TestSession()
        try:
            tx = _models.Transaction(
                user_id=user_id,
                account_id=None,
                amount=25000.0,
                currency="CLP",
                category="Alimentación",
                date=date(2026, 5, 27),
                merchant="Banco Email Import",
                notes="Importado desde email",
                is_income=False,
                is_transfer=False,
                image_url="",
                status="pending_review",
            )
            db.add(tx)
            db.commit()
            db.refresh(tx)
            tx_id = tx.id
        finally:
            db.close()

        # Confirm via the review endpoint with account_id
        review_resp = client.post(
            f"/email/review/{tx_id}",
            json={"action": "confirm", "account_id": account_id},
            headers=headers,
        )
        assert review_resp.status_code == 200, (
            f"POST /email/review/{tx_id} failed: {review_resp.text}"
        )
        body = review_resp.json()
        assert body["status"] == "confirmed", (
            f"Transaction should be confirmed, got status: {body['status']}"
        )
        assert body["account_id"] == account_id, (
            f"account_id should be {account_id}, got {body['account_id']}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Bug 6 — FixedItem schema accepts day and is_income fields
# ═══════════════════════════════════════════════════════════════════════════

class TestFixedItemSchema:
    def test_fixed_item_with_day_and_is_income(self):
        """FixedItem(name, amount, day, is_income) parses correctly."""
        from app.schemas import FixedItem

        item = FixedItem(name="Arriendo", amount=500000, day=5, is_income=False)
        assert item.name == "Arriendo"
        assert item.amount == 500000.0
        assert item.day == 5
        assert item.is_income is False

    def test_fixed_item_income(self):
        """FixedItem with is_income=True parses correctly."""
        from app.schemas import FixedItem

        item = FixedItem(name="Sueldo", amount=1500000, day=28, is_income=True)
        assert item.name == "Sueldo"
        assert item.amount == 1500000.0
        assert item.day == 28
        assert item.is_income is True

    def test_fixed_item_defaults(self):
        """FixedItem defaults: day=1, is_income=False when omitted."""
        from app.schemas import FixedItem

        item = FixedItem(name="Internet", amount=25000)
        assert item.day == 1
        assert item.is_income is False


# ═══════════════════════════════════════════════════════════════════════════
# Bug 7 — TransactionUpdate date is Optional[date] not NoneType
# ═══════════════════════════════════════════════════════════════════════════

class TestTransactionUpdateDateField:
    def test_transaction_update_with_date_parses(self):
        """TransactionUpdate(category='Salud', date='2026-05-27') must not raise."""
        from app.schemas import TransactionUpdate

        update = TransactionUpdate(category="Salud", date="2026-05-27")
        assert update.category == "Salud"
        assert update.date == date(2026, 5, 27)

    def test_transaction_update_without_date_parses(self):
        """TransactionUpdate(category='Salud') with no date must succeed, date=None."""
        from app.schemas import TransactionUpdate

        update = TransactionUpdate(category="Salud")
        assert update.category == "Salud"
        assert update.date is None

    def test_transaction_update_date_type_is_correct(self):
        """The date field must be a real date instance, not NoneType."""
        from app.schemas import TransactionUpdate
        from datetime import date as date_type

        update = TransactionUpdate(date="2026-05-27")
        assert isinstance(update.date, date_type), (
            f"Expected date instance, got {type(update.date)}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Bug 8 — GET /transactions?account_id=X filters by account_id
# ═══════════════════════════════════════════════════════════════════════════

class TestListTransactionsByAccountId:
    def test_filter_by_account_id_returns_only_matching_transactions(self, client):
        """
        Create 2 transactions: one with account_id=A, one with account_id=B.
        GET /transactions?account_id=A must return only the first one.
        """
        _, headers = _signup(client, "account_filter@regression.com")

        # Create two accounts
        acc_a_resp = client.post(
            "/accounts",
            json={
                "name": "Cuenta A",
                "bank": "BancoA",
                "type": "debit",
                "currency": "CLP",
                "color": "#ef4444",
                "icon": "card",
                "credit_limit": 0.0,
                "anchor_balance": 0.0,
            },
            headers=headers,
        )
        assert acc_a_resp.status_code == 201
        account_a_id = acc_a_resp.json()["id"]

        acc_b_resp = client.post(
            "/accounts",
            json={
                "name": "Cuenta B",
                "bank": "BancoB",
                "type": "debit",
                "currency": "CLP",
                "color": "#3b82f6",
                "icon": "card",
                "credit_limit": 0.0,
                "anchor_balance": 0.0,
            },
            headers=headers,
        )
        assert acc_b_resp.status_code == 201
        account_b_id = acc_b_resp.json()["id"]

        # Create transaction in account A
        tx_a_resp = client.post(
            "/transactions",
            json={
                "amount": 5000.0,
                "currency": "CLP",
                "category": "Alimentación",
                "date": "2026-05-01",
                "merchant": "Merchant A",
                "notes": "",
                "is_income": False,
                "account_id": account_a_id,
            },
            headers=headers,
        )
        assert tx_a_resp.status_code == 201
        tx_a_id = tx_a_resp.json()["id"]

        # Create transaction in account B
        tx_b_resp = client.post(
            "/transactions",
            json={
                "amount": 8000.0,
                "currency": "CLP",
                "category": "Transporte",
                "date": "2026-05-02",
                "merchant": "Merchant B",
                "notes": "",
                "is_income": False,
                "account_id": account_b_id,
            },
            headers=headers,
        )
        assert tx_b_resp.status_code == 201
        tx_b_id = tx_b_resp.json()["id"]

        # GET /transactions?account_id=A — must return only tx_a
        resp = client.get(f"/transactions?account_id={account_a_id}", headers=headers)
        assert resp.status_code == 200, f"GET /transactions?account_id failed: {resp.text}"
        txs = resp.json()
        ids = [tx["id"] for tx in txs]

        assert tx_a_id in ids, (
            f"Transaction A (id={tx_a_id}) should appear in account_id={account_a_id} filter result"
        )
        assert tx_b_id not in ids, (
            f"Transaction B (id={tx_b_id}) should NOT appear in account_id={account_a_id} filter result"
        )
