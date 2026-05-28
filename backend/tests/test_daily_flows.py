"""
End-to-end tests for the 8 daily user flows in Lucas.

These tests use the exact same setup pattern as test_api.py:
FakeLimiter, StaticPool, models import.

Run with:
    cd /Users/kako2/Documents/lucas/backend
    python3 -m pytest tests/test_daily_flows.py -v
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
os.environ["JWT_SECRET"] = "test-daily-flows-secret"
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

@pytest.fixture(scope="module", autouse=True)
def _reset_db_override():
    """Re-apply this module's DB override so other modules' imports don't steal it."""
    saved = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = override_get_db
    yield
    if saved is not None:
        app.dependency_overrides[get_db] = saved
    else:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture(scope="module")
def client():
    """Single TestClient shared across the whole module."""
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def auth_headers(client):
    """Register a fresh daily-flows user and return JWT Authorization headers."""
    resp = client.post(
        "/auth/signup",
        json={"email": "dailyflows@test.com", "password": "DailyTest1!", "locale": "es"},
    )
    assert resp.status_code == 200, f"signup failed: {resp.text}"
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def efectivo_id(client, auth_headers):
    """Return the id of the auto-created Efectivo cash account."""
    resp = client.get("/accounts", headers=auth_headers)
    assert resp.status_code == 200, f"GET /accounts failed: {resp.text}"
    accounts = resp.json()
    efectivo = next((a for a in accounts if a["name"] == "Efectivo"), None)
    assert efectivo is not None, f"Efectivo not auto-created; accounts: {accounts}"
    return efectivo["id"]


# ════════════════════════════════════════════════════════════════════════════
# Flow 1: Add expense → balance decreases
# ════════════════════════════════════════════════════════════════════════════

class TestFlow1ExpenseDecreasesBalance:
    def test_efectivo_starts_at_zero(self, client, auth_headers, efectivo_id):
        resp = client.get("/accounts", headers=auth_headers)
        assert resp.status_code == 200
        accounts = resp.json()
        efectivo = next(a for a in accounts if a["id"] == efectivo_id)
        assert efectivo["current_balance"] == 0.0, (
            f"Efectivo should start at 0, got {efectivo['current_balance']}"
        )

    def test_expense_decreases_balance(self, client, auth_headers, efectivo_id):
        resp = client.post(
            "/transactions",
            json={
                "amount": 10000,
                "currency": "CLP",
                "category": "Alimentación",
                "date": "2026-05-01",
                "merchant": "Almuerzo Flow1",
                "notes": "",
                "is_income": False,
                "account_id": efectivo_id,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201, f"POST /transactions failed: {resp.text}"

        accounts_resp = client.get("/accounts", headers=auth_headers)
        assert accounts_resp.status_code == 200
        accounts = accounts_resp.json()
        efectivo = next(a for a in accounts if a["id"] == efectivo_id)
        assert efectivo["current_balance"] == -10000.0, (
            f"Balance should be -10000 after expense, got {efectivo['current_balance']}"
        )


# ════════════════════════════════════════════════════════════════════════════
# Flow 2: Add income → balance increases
# ════════════════════════════════════════════════════════════════════════════

class TestFlow2IncomeIncreasesBalance:
    def test_income_increases_balance(self, client, auth_headers, efectivo_id):
        # Get the balance before this income (from Flow 1, should be -10000)
        before_resp = client.get("/accounts", headers=auth_headers)
        before_accounts = before_resp.json()
        balance_before = next(
            a["current_balance"] for a in before_accounts if a["id"] == efectivo_id
        )

        resp = client.post(
            "/transactions",
            json={
                "amount": 50000,
                "currency": "CLP",
                "category": "Sueldo",
                "date": "2026-05-02",
                "merchant": "Pago Sueldo Flow2",
                "notes": "",
                "is_income": True,
                "account_id": efectivo_id,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201, f"POST /transactions failed: {resp.text}"

        after_resp = client.get("/accounts", headers=auth_headers)
        after_accounts = after_resp.json()
        balance_after = next(
            a["current_balance"] for a in after_accounts if a["id"] == efectivo_id
        )

        assert balance_after == balance_before + 50000.0, (
            f"Balance should increase by 50000: {balance_before} + 50000 != {balance_after}"
        )


# ════════════════════════════════════════════════════════════════════════════
# Flow 3: Credit card used balance tracks correctly
# ════════════════════════════════════════════════════════════════════════════

class TestFlow3CreditCardTracksUsed:
    def test_credit_card_used_and_available(self, client, auth_headers):
        # Create a credit account
        create_resp = client.post(
            "/accounts",
            json={
                "name": "Visa Flow3",
                "bank": "BCI",
                "type": "credit",
                "currency": "CLP",
                "color": "#ef4444",
                "icon": "card",
                "credit_limit": 500000.0,
                "anchor_balance": 0.0,
            },
            headers=auth_headers,
        )
        assert create_resp.status_code == 201, f"POST /accounts failed: {create_resp.text}"
        credit_id = create_resp.json()["id"]

        # Post an expense on the credit card
        tx_resp = client.post(
            "/transactions",
            json={
                "amount": 25000,
                "currency": "CLP",
                "category": "Tecnología",
                "date": "2026-05-03",
                "merchant": "Apple Flow3",
                "notes": "",
                "is_income": False,
                "account_id": credit_id,
            },
            headers=auth_headers,
        )
        assert tx_resp.status_code == 201, f"POST /transactions failed: {tx_resp.text}"

        # Check current_used and available_credit
        accounts_resp = client.get("/accounts", headers=auth_headers)
        assert accounts_resp.status_code == 200
        accounts = accounts_resp.json()
        cc = next(a for a in accounts if a["id"] == credit_id)

        assert cc["current_used"] == 25000.0, (
            f"current_used should be 25000, got {cc['current_used']}"
        )
        assert cc["available_credit"] == 475000.0, (
            f"available_credit should be 475000, got {cc['available_credit']}"
        )


# ════════════════════════════════════════════════════════════════════════════
# Flow 4: Pending income review → confirm with account → balance updates
# ════════════════════════════════════════════════════════════════════════════

class TestFlow4PendingReviewConfirm:
    def test_confirm_pending_income_updates_balance(self, client, auth_headers, efectivo_id):
        # Get balance before confirmation
        before_resp = client.get("/accounts", headers=auth_headers)
        balance_before = next(
            a["current_balance"] for a in before_resp.json() if a["id"] == efectivo_id
        )

        # Insert a pending_review income transaction directly into DB
        db = TestSession()
        try:
            # We need the user_id — look it up by querying the account
            account = db.query(_models.Account).filter(_models.Account.id == efectivo_id).first()
            assert account is not None, "Efectivo account not found in DB"
            user_id = account.user_id

            from datetime import date
            pending_tx = _models.Transaction(
                user_id=user_id,
                account_id=None,  # no account yet
                amount=100000.0,
                currency="CLP",
                category="Transferencia",
                date=date(2026, 5, 4),
                merchant="Transferencia Flow4",
                notes="Importado desde email",
                is_income=True,
                is_transfer=False,
                image_url="",
                status="pending_review",
            )
            db.add(pending_tx)
            db.commit()
            db.refresh(pending_tx)
            tx_id = pending_tx.id
        finally:
            db.close()

        # Confirm the transaction via the email review endpoint
        review_resp = client.post(
            f"/email/review/{tx_id}",
            json={"action": "confirm", "account_id": efectivo_id},
            headers=auth_headers,
        )
        assert review_resp.status_code == 200, (
            f"POST /email/review/{tx_id} failed: {review_resp.text}"
        )
        confirmed = review_resp.json()
        assert confirmed["status"] == "confirmed", (
            f"Transaction should be confirmed, got status={confirmed['status']}"
        )
        assert confirmed["account_id"] == efectivo_id

        # GET /accounts → Efectivo balance should include the 100000 income
        after_resp = client.get("/accounts", headers=auth_headers)
        balance_after = next(
            a["current_balance"] for a in after_resp.json() if a["id"] == efectivo_id
        )
        assert balance_after == balance_before + 100000.0, (
            f"Balance should increase by 100000: {balance_before} + 100000 != {balance_after}"
        )


# ════════════════════════════════════════════════════════════════════════════
# Flow 5: Filter transactions by account
# ════════════════════════════════════════════════════════════════════════════

class TestFlow5FilterByAccount:
    def test_filter_transactions_by_account(self, client, auth_headers):
        # Create accounts A and B for this flow
        acc_a_resp = client.post(
            "/accounts",
            json={
                "name": "Account A Flow5",
                "bank": "",
                "type": "debit",
                "currency": "CLP",
                "color": "#10b981",
                "icon": "card",
                "credit_limit": 0.0,
                "anchor_balance": 0.0,
            },
            headers=auth_headers,
        )
        assert acc_a_resp.status_code == 201
        a_id = acc_a_resp.json()["id"]

        acc_b_resp = client.post(
            "/accounts",
            json={
                "name": "Account B Flow5",
                "bank": "",
                "type": "debit",
                "currency": "CLP",
                "color": "#f59e0b",
                "icon": "card",
                "credit_limit": 0.0,
                "anchor_balance": 0.0,
            },
            headers=auth_headers,
        )
        assert acc_b_resp.status_code == 201
        b_id = acc_b_resp.json()["id"]

        _TX_BASE = {
            "currency": "CLP",
            "category": "Otros",
            "notes": "",
            "is_income": False,
        }

        # 2 transactions on account A
        resp_a1 = client.post(
            "/transactions",
            json={**_TX_BASE, "amount": 1000, "date": "2026-05-05",
                  "merchant": "A1 Flow5", "account_id": a_id},
            headers=auth_headers,
        )
        assert resp_a1.status_code == 201

        resp_a2 = client.post(
            "/transactions",
            json={**_TX_BASE, "amount": 2000, "date": "2026-05-06",
                  "merchant": "A2 Flow5", "account_id": a_id},
            headers=auth_headers,
        )
        assert resp_a2.status_code == 201

        # 1 transaction on account B
        resp_b1 = client.post(
            "/transactions",
            json={**_TX_BASE, "amount": 3000, "date": "2026-05-07",
                  "merchant": "B1 Flow5", "account_id": b_id},
            headers=auth_headers,
        )
        assert resp_b1.status_code == 201

        # Filter by account A — expect 2
        filter_a = client.get(f"/transactions?account_id={a_id}", headers=auth_headers)
        assert filter_a.status_code == 200
        txs_a = filter_a.json()
        a_merchants = [tx["merchant"] for tx in txs_a]
        assert len(txs_a) == 2, f"Expected 2 txs for account A, got {len(txs_a)}: {a_merchants}"
        assert "A1 Flow5" in a_merchants
        assert "A2 Flow5" in a_merchants

        # Filter by account B — expect 1
        filter_b = client.get(f"/transactions?account_id={b_id}", headers=auth_headers)
        assert filter_b.status_code == 200
        txs_b = filter_b.json()
        assert len(txs_b) == 1, f"Expected 1 tx for account B, got {len(txs_b)}"
        assert txs_b[0]["merchant"] == "B1 Flow5"

        # No filter — all 3 merchants appear somewhere in the full list
        all_resp = client.get("/transactions", headers=auth_headers)
        assert all_resp.status_code == 200
        all_merchants = [tx["merchant"] for tx in all_resp.json()]
        assert "A1 Flow5" in all_merchants
        assert "A2 Flow5" in all_merchants
        assert "B1 Flow5" in all_merchants


# ════════════════════════════════════════════════════════════════════════════
# Flow 6: Edit transaction category (the 422 fix)
# ════════════════════════════════════════════════════════════════════════════

class TestFlow6EditTransactionCategory:
    def test_patch_category_returns_200(self, client, auth_headers):
        # Create a transaction
        create_resp = client.post(
            "/transactions",
            json={
                "amount": 5000,
                "currency": "CLP",
                "category": "Otros",
                "date": "2026-05-08",
                "merchant": "Edit Test Flow6",
                "notes": "",
                "is_income": False,
            },
            headers=auth_headers,
        )
        assert create_resp.status_code == 201, f"create failed: {create_resp.text}"
        tx_id = create_resp.json()["id"]

        # PATCH category and date
        patch_resp = client.patch(
            f"/transactions/{tx_id}",
            json={"category": "Salud", "date": "2026-05-27"},
            headers=auth_headers,
        )
        assert patch_resp.status_code == 200, (
            f"PATCH /transactions/{tx_id} returned {patch_resp.status_code}: {patch_resp.text}"
        )
        patched = patch_resp.json()
        assert patched["category"] == "Salud", (
            f"category should be 'Salud', got '{patched['category']}'"
        )


# ════════════════════════════════════════════════════════════════════════════
# Flow 7: Dashboard summary reflects transactions
# ════════════════════════════════════════════════════════════════════════════

class TestFlow7DashboardSummary:
    def test_dashboard_reflects_expenses(self, client, auth_headers):
        _TX_BASE = {
            "currency": "CLP",
            "category": "Entretenimiento",
            "notes": "",
            "is_income": False,
        }

        import datetime
        today = datetime.date.today()
        month_str = today.strftime("%Y-%m")

        # Create 3 expense transactions this month
        for i in range(3):
            # Avoid 409 duplicate guard by varying the merchant
            day = min(today.day, 28)  # clamp to valid day
            resp = client.post(
                "/transactions",
                json={**_TX_BASE, "amount": 8000 + i * 1000,
                      "date": f"{month_str}-{day:02d}",
                      "merchant": f"Dashboard Expense {i} Flow7"},
                headers=auth_headers,
            )
            assert resp.status_code == 201, f"create tx {i} failed: {resp.text}"

        dash_resp = client.get("/dashboard", headers=auth_headers)
        assert dash_resp.status_code == 200, f"GET /dashboard failed: {dash_resp.text}"
        dash = dash_resp.json()

        assert dash["total_spent"] > 0, (
            f"total_spent should be > 0, got {dash['total_spent']}"
        )
        assert isinstance(dash["by_category"], list), "by_category should be a list"
        assert len(dash["by_category"]) > 0, "by_category should not be empty"


# ════════════════════════════════════════════════════════════════════════════
# Flow 8: Boleta OCR text parsing (no image needed)
# ════════════════════════════════════════════════════════════════════════════

class TestFlow8BoletaOcrParsing:
    _BOLETA_TEXT = """\
RESTAURANTE EL BUEN SABOR
Boleta N° 12345
2x Lomo completo      $14.000
COCA COLA             $2.000
TOTAL NETO            $13.445
IVA 19%               $2.555
TOTAL                 $16.000
"""

    def test_parse_boleta_items(self):
        from app.ocr import _parse_boleta_from_text

        items, total_neto, iva_amount, confidence = _parse_boleta_from_text(
            self._BOLETA_TEXT
        )

        # Find Lomo completo
        lomo = next(
            (it for it in items if "lomo" in it.name.lower()),
            None,
        )
        assert lomo is not None, f"'Lomo completo' item not found; items={items}"
        assert lomo.quantity == 2, f"Lomo quantity should be 2, got {lomo.quantity}"
        assert lomo.price == 7000.0, (
            f"Lomo unit price should be 7000 (14000/2), got {lomo.price}"
        )

        # Find Coca Cola
        coca = next(
            (it for it in items if "coca" in it.name.lower()),
            None,
        )
        assert coca is not None, f"'Coca Cola' item not found; items={items}"
        assert coca.quantity == 1, f"Coca Cola quantity should be 1, got {coca.quantity}"
        assert coca.price == 2000.0, f"Coca Cola price should be 2000, got {coca.price}"

        # Totals
        assert total_neto == pytest.approx(13445.0, abs=1.0), (
            f"total_neto should be ~13445, got {total_neto}"
        )
        assert iva_amount == pytest.approx(2555.0, abs=1.0), (
            f"iva_amount should be ~2555, got {iva_amount}"
        )
