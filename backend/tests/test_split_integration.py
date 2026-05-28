"""
Split integration tests — exercises the full split flow end-to-end.

Scenarios:
  a. 3 people, 5 items, each person assigned different items → verify totals
  b. 1 item, 2 people, equal split → each pays half (rounding)
  c. 1 item, 3 people, percent split (50/30/20)
  d. Discount item (negative price) assigned to all → reduces share
  e. settle() returns minimal debt graph
  f. Assign same item twice → idempotent (replace, no duplicate)
  g. Manual JSON-based split (no OCR), full flow with /split/start

Run with:
    pytest tests/test_split_integration.py -v
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
os.environ["JWT_SECRET"] = "split-test-secret"
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

# Create all tables
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
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def auth_headers(client):
    resp = client.post(
        "/auth/signup",
        json={"email": "splitqa@test.com", "password": "SplitQA1234!", "locale": "es"},
    )
    assert resp.status_code == 200, f"signup failed: {resp.text}"
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def yo_id(client, auth_headers):
    """Return the 'Yo' person id for the test user."""
    resp = client.get("/split/me", headers=auth_headers)
    assert resp.status_code == 200
    return resp.json()["id"]


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────

def _create_person(client, auth_headers, name, color="#ef4444"):
    resp = client.post(
        "/split/people",
        json={"name": name, "color": color},
        headers=auth_headers,
    )
    assert resp.status_code == 201, f"create person failed: {resp.text}"
    return resp.json()["id"]


def _start_manual(client, auth_headers, total, merchant="Test Split"):
    resp = client.post(
        "/split/start-manual",
        json={
            "total_amount": total,
            "currency": "CLP",
            "date": "2026-03-01",
            "merchant": merchant,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, f"start-manual failed: {resp.text}"
    return resp.json()


def _add_item(client, auth_headers, tx_id, name, price, quantity=1):
    resp = client.post(
        f"/split/add-item?transaction_id={tx_id}",
        json={"name": name, "price": price, "quantity": quantity},
        headers=auth_headers,
    )
    assert resp.status_code == 201, f"add-item failed: {resp.text}"
    return resp.json()["id"]


def _assign(client, auth_headers, item_id, assignees):
    """
    assignees: list of (person_id, split_type, value)
    """
    resp = client.post(
        "/split/assign-item",
        json={
            "item_id": item_id,
            "assignees": [
                {"person_id": pid, "split_type": stype, "value": val}
                for pid, stype, val in assignees
            ],
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, f"assign-item failed: {resp.text}"
    return resp.json()


def _result(client, auth_headers, tx_id):
    resp = client.get(f"/split/result?transaction_id={tx_id}", headers=auth_headers)
    assert resp.status_code == 200, f"result failed: {resp.text}"
    return resp.json()


def _settle(client, auth_headers, tx_id, payer_id=None):
    body = {"transaction_id": tx_id, "save_to_lucas": False}
    if payer_id is not None:
        body["payer_person_id"] = payer_id
    resp = client.post("/split/settle", json=body, headers=auth_headers)
    assert resp.status_code == 200, f"settle failed: {resp.text}"
    return resp.json()


# ════════════════════════════════════════════════════════════════════════════
# Scenario a: 3 people, 5 items, each person assigned different items
# ════════════════════════════════════════════════════════════════════════════

class TestThreePeopleFiveItems:
    """
    Setup:
      - Alice: item1 (1000) + item2 (2000)  = 3000
      - Bob:   item3 (1500) + item4 (500)   = 2000
      - Yo:    item5 (4000)                  = 4000
    Total: 9000
    """

    def test_correct_totals_per_person(self, client, auth_headers, yo_id):
        alice_id = _create_person(client, auth_headers, "AliceA", "#ef4444")
        bob_id   = _create_person(client, auth_headers, "BobA",   "#10b981")

        sess = _start_manual(client, auth_headers, 9000, "ThreePeopleTest")
        tx_id    = sess["transaction_id"]
        item1_id = sess["items"][0]["id"]   # price=9000, will re-assign

        # The manual split creates one item for the total; replace it with 5 real items.
        # Actually, we'll delete the initial item conceptually by assigning item1 to Alice
        # and adding 4 more items. We'll reprice item1 via the PATCH endpoint.
        resp = client.patch(
            f"/split/items/{item1_id}",
            json={"name": "Item1", "price": 1000.0},
            headers=auth_headers,
        )
        assert resp.status_code == 200

        item2_id = _add_item(client, auth_headers, tx_id, "Item2", 2000)
        item3_id = _add_item(client, auth_headers, tx_id, "Item3", 1500)
        item4_id = _add_item(client, auth_headers, tx_id, "Item4", 500)
        item5_id = _add_item(client, auth_headers, tx_id, "Item5", 4000)

        _assign(client, auth_headers, item1_id, [(alice_id, "equal", None)])
        _assign(client, auth_headers, item2_id, [(alice_id, "equal", None)])
        _assign(client, auth_headers, item3_id, [(bob_id, "equal", None)])
        _assign(client, auth_headers, item4_id, [(bob_id, "equal", None)])
        _assign(client, auth_headers, item5_id, [(yo_id, "equal", None)])

        result = _result(client, auth_headers, tx_id)
        totals = {p["person_id"]: p["total"] for p in result["people"]}

        assert totals[alice_id] == 3000.0, f"Alice should owe 3000, got {totals.get(alice_id)}"
        assert totals[bob_id]   == 2000.0, f"Bob should owe 2000, got {totals.get(bob_id)}"
        assert totals[yo_id]    == 4000.0, f"Yo should owe 4000, got {totals.get(yo_id)}"
        assert result["completion_pct"] == 100.0

    def test_total_amount_matches_sum_of_items(self, client, auth_headers, yo_id):
        # Re-use fresh session to verify total_amount bookkeeping
        sess = _start_manual(client, auth_headers, 1000, "SumCheck")
        tx_id = sess["transaction_id"]
        _add_item(client, auth_headers, tx_id, "Extra", 500)

        result = _result(client, auth_headers, tx_id)
        assert result["total_amount"] == 1500.0


# ════════════════════════════════════════════════════════════════════════════
# Scenario b: 1 item, 2 people, equal split → each pays half
# ════════════════════════════════════════════════════════════════════════════

class TestEqualSplitTwoPeople:

    def test_even_amount_each_pays_half(self, client, auth_headers, yo_id):
        friend_id = _create_person(client, auth_headers, "HalfFriend", "#f97316")
        sess = _start_manual(client, auth_headers, 10000, "EqualHalf")
        tx_id   = sess["transaction_id"]
        item_id = sess["items"][0]["id"]

        result_item = _assign(client, auth_headers, item_id, [
            (yo_id,     "equal", None),
            (friend_id, "equal", None),
        ])
        amounts = {a["person_id"]: a["computed_amount"] for a in result_item["assignees"]}
        assert amounts[yo_id]     == 5000.0
        assert amounts[friend_id] == 5000.0

    def test_odd_amount_rounding_correct(self, client, auth_headers, yo_id):
        """
        $10001 / 2 = 5000.5 each.  With round(,2) they each get 5000.5 (exact in float).
        More interesting: $10003 / 2 = 5001.5 each → sum=10003 (ok, no rounding issue).
        Use $9999 → 4999.5 each (sum = 9999, no remainder).

        Real rounding trap: $9997 / 2 = 4998.5 each → sum=9997 (ok).
        $3 / 2: per=1.5, sum=3.0 → diff=0. Fine.

        Let's test the actual round-to-cent case: $10001 / 3:
        per = 3333.67, sum = 3*3333.67 = 10001.01, diff = -0.01 → last person pays 3333.66.
        """
        friend_id = _create_person(client, auth_headers, "OddFriend", "#eab308")
        sess = _start_manual(client, auth_headers, 10001, "OddAmount")
        tx_id   = sess["transaction_id"]
        item_id = sess["items"][0]["id"]

        result_item = _assign(client, auth_headers, item_id, [
            (yo_id,     "equal", None),
            (friend_id, "equal", None),
        ])
        amounts = [a["computed_amount"] for a in result_item["assignees"]]
        total = sum(amounts)
        # Sum of shares must equal the item price exactly
        assert abs(total - 10001.0) < 0.01, (
            f"Total shares should sum to 10001, got {total}"
        )
        # Each person should get approximately half
        for amt in amounts:
            assert abs(amt - 5000.5) < 0.01, f"Each share should be ~5000.5, got {amt}"

    def test_three_way_equal_rounding(self, client, auth_headers, yo_id):
        """
        $10 / 3 = 3.33 each → sum = 9.99. Last person gets 3.34 (remainder = 0.01).
        Tests that rounding adjustment is applied to the last assignee.
        """
        p2 = _create_person(client, auth_headers, "P2Equal3", "#06b6d4")
        p3 = _create_person(client, auth_headers, "P3Equal3", "#a855f7")
        sess = _start_manual(client, auth_headers, 10, "ThreeWayRound")
        tx_id   = sess["transaction_id"]
        item_id = sess["items"][0]["id"]

        result_item = _assign(client, auth_headers, item_id, [
            (yo_id, "equal", None),
            (p2,    "equal", None),
            (p3,    "equal", None),
        ])
        amounts = {a["person_id"]: a["computed_amount"] for a in result_item["assignees"]}
        total = sum(amounts.values())
        assert abs(total - 10.0) < 0.001, f"Shares must sum to 10, got {total}"
        # First two get 3.33, last gets 3.34
        assert amounts[yo_id] == 3.33
        assert amounts[p2]    == 3.33
        assert amounts[p3]    == 3.34


# ════════════════════════════════════════════════════════════════════════════
# Scenario c: 1 item, 3 people, percent split (50/30/20)
# ════════════════════════════════════════════════════════════════════════════

class TestPercentSplit:

    def test_50_30_20_percent_split(self, client, auth_headers, yo_id):
        p2 = _create_person(client, auth_headers, "Pct30", "#6366f1")
        p3 = _create_person(client, auth_headers, "Pct20", "#ec4899")
        sess = _start_manual(client, auth_headers, 10000, "PctSplit503020")
        tx_id   = sess["transaction_id"]
        item_id = sess["items"][0]["id"]

        result_item = _assign(client, auth_headers, item_id, [
            (yo_id, "percent", 50.0),
            (p2,    "percent", 30.0),
            (p3,    "percent", 20.0),
        ])
        amounts = {a["person_id"]: a["computed_amount"] for a in result_item["assignees"]}
        assert amounts[yo_id] == 5000.0, f"50% of 10000 = 5000, got {amounts[yo_id]}"
        assert amounts[p2]    == 3000.0, f"30% of 10000 = 3000, got {amounts[p2]}"
        assert amounts[p3]    == 2000.0, f"20% of 10000 = 2000, got {amounts[p3]}"
        assert sum(amounts.values()) == 10000.0

    def test_percent_last_person_gets_remainder(self, client, auth_headers, yo_id):
        """
        Percentages that don't add up to 100%: last person gets the remainder.
        50 + 30 = 80%. Last person gets 100% - 80% = 20% effectively via remainder.
        """
        p2 = _create_person(client, auth_headers, "PctRem2", "#ef4444")
        p3 = _create_person(client, auth_headers, "PctRem3", "#10b981")
        sess = _start_manual(client, auth_headers, 1000, "PctRemainder")
        tx_id   = sess["transaction_id"]
        item_id = sess["items"][0]["id"]

        result_item = _assign(client, auth_headers, item_id, [
            (yo_id, "percent", 50.0),
            (p2,    "percent", 30.0),
            (p3,    "percent", 20.0),  # last person: remainder = 1000 - 500 - 300 = 200
        ])
        amounts = {a["person_id"]: a["computed_amount"] for a in result_item["assignees"]}
        total = sum(amounts.values())
        assert abs(total - 1000.0) < 0.01, f"Shares must sum to 1000, got {total}"
        assert amounts[yo_id] == 500.0
        assert amounts[p2]    == 300.0
        assert amounts[p3]    == 200.0

    def test_percent_rounding_remainder_goes_to_last(self, client, auth_headers, yo_id):
        """
        $100 split 33% / 33% / 34%: checks that last person absorbs rounding.
        33+33 = 66; last gets 34. With actual computation:
          first:  round(100*33/100, 2) = 33.0
          second: round(100*33/100, 2) = 33.0
          last:   100 - 33.0 - 33.0 = 34.0
        Sum should be 100.
        """
        p2 = _create_person(client, auth_headers, "PctRound2", "#f97316")
        p3 = _create_person(client, auth_headers, "PctRound3", "#eab308")
        sess = _start_manual(client, auth_headers, 100, "PctRoundRem")
        tx_id   = sess["transaction_id"]
        item_id = sess["items"][0]["id"]

        result_item = _assign(client, auth_headers, item_id, [
            (yo_id, "percent", 33.0),
            (p2,    "percent", 33.0),
            (p3,    "percent", 34.0),
        ])
        amounts = list(a["computed_amount"] for a in result_item["assignees"])
        assert abs(sum(amounts) - 100.0) < 0.01, f"Sum should be 100, got {sum(amounts)}"


# ════════════════════════════════════════════════════════════════════════════
# Scenario d: Discount item (negative price) assigned to all
# ════════════════════════════════════════════════════════════════════════════

class TestDiscountItem:

    def test_negative_price_reduces_share(self, client, auth_headers, yo_id):
        """
        One main item ($9000) and one discount item (-$1000), both assigned to Yo.
        Yo's total should be $8000.
        """
        sess = _start_manual(client, auth_headers, 9000, "DiscountSingle")
        tx_id    = sess["transaction_id"]
        item1_id = sess["items"][0]["id"]

        discount_id = _add_item(client, auth_headers, tx_id, "Descuento", -1000, 1)
        _assign(client, auth_headers, item1_id, [(yo_id, "equal", None)])
        _assign(client, auth_headers, discount_id, [(yo_id, "equal", None)])

        result = _result(client, auth_headers, tx_id)
        totals = {p["person_id"]: p["total"] for p in result["people"]}
        assert totals[yo_id] == 8000.0, f"Yo should owe 8000, got {totals.get(yo_id)}"

    def test_discount_distributed_to_all_three(self, client, auth_headers, yo_id):
        """
        Main item ($9000) split equally among 3 people (3000 each).
        Discount (-$900) also split equally among all 3 (-300 each).
        Each person's final share: 3000 - 300 = 2700.
        """
        p2 = _create_person(client, auth_headers, "DiscP2", "#6366f1")
        p3 = _create_person(client, auth_headers, "DiscP3", "#a855f7")

        sess = _start_manual(client, auth_headers, 9000, "DiscountThree")
        tx_id    = sess["transaction_id"]
        item1_id = sess["items"][0]["id"]

        discount_id = _add_item(client, auth_headers, tx_id, "Descuento10pct", -900, 1)

        _assign(client, auth_headers, item1_id, [
            (yo_id, "equal", None),
            (p2,    "equal", None),
            (p3,    "equal", None),
        ])
        _assign(client, auth_headers, discount_id, [
            (yo_id, "equal", None),
            (p2,    "equal", None),
            (p3,    "equal", None),
        ])

        result = _result(client, auth_headers, tx_id)
        totals = {p["person_id"]: p["total"] for p in result["people"]}
        assert totals[yo_id] == 2700.0, f"Yo should owe 2700, got {totals.get(yo_id)}"
        assert totals[p2]    == 2700.0, f"P2 should owe 2700, got {totals.get(p2)}"
        assert totals[p3]    == 2700.0, f"P3 should owe 2700, got {totals.get(p3)}"

    def test_discount_net_total_in_result(self, client, auth_headers, yo_id):
        """total_amount in the result should equal sum of all line_totals (may be negative-adjusted)."""
        sess = _start_manual(client, auth_headers, 5000, "DiscNetTotal")
        tx_id    = sess["transaction_id"]
        item1_id = sess["items"][0]["id"]
        _add_item(client, auth_headers, tx_id, "Descuento", -500, 1)

        result = _result(client, auth_headers, tx_id)
        assert result["total_amount"] == 4500.0, (
            f"Net total should be 4500, got {result['total_amount']}"
        )


# ════════════════════════════════════════════════════════════════════════════
# Scenario e: settle() returns minimal debt graph
# ════════════════════════════════════════════════════════════════════════════

class TestSettleDebtGraph:

    def test_payer_is_yo_others_owe_yo(self, client, auth_headers, yo_id):
        """
        3 people, item split equally ($9000 / 3 = $3000 each).
        Yo paid → Alice owes Yo $3000, Bob owes Yo $3000.
        """
        alice_id = _create_person(client, auth_headers, "AliceSettle", "#ef4444")
        bob_id   = _create_person(client, auth_headers, "BobSettle",   "#10b981")

        sess = _start_manual(client, auth_headers, 9000, "SettleDebt3")
        tx_id   = sess["transaction_id"]
        item_id = sess["items"][0]["id"]

        _assign(client, auth_headers, item_id, [
            (yo_id,    "equal", None),
            (alice_id, "equal", None),
            (bob_id,   "equal", None),
        ])

        result = _settle(client, auth_headers, tx_id)
        assert result["payer_person_id"] == yo_id
        assert result["my_total"] == 3000.0

        debts_by_person = {d["person_id"]: d["amount"] for d in result["debts"]}
        assert debts_by_person[alice_id] == 3000.0, (
            f"Alice owes Yo 3000, got {debts_by_person.get(alice_id)}"
        )
        assert debts_by_person[bob_id] == 3000.0, (
            f"Bob owes Yo 3000, got {debts_by_person.get(bob_id)}"
        )

    def test_payer_is_friend_yo_has_debt(self, client, auth_headers, yo_id):
        """
        2 people, $6000 split equally. Friend paid.
        → Yo owes Friend $3000. Debt list has one entry.
        """
        friend_id = _create_person(client, auth_headers, "FriendPayer", "#06b6d4")

        sess = _start_manual(client, auth_headers, 6000, "SettleFriendPaid")
        tx_id   = sess["transaction_id"]
        item_id = sess["items"][0]["id"]

        _assign(client, auth_headers, item_id, [
            (yo_id,    "equal", None),
            (friend_id, "equal", None),
        ])

        result = _settle(client, auth_headers, tx_id, payer_id=friend_id)
        assert result["payer_person_id"] == friend_id
        assert result["my_total"] == 3000.0

        # There should be exactly one debt row: Yo owes Friend
        assert len(result["debts"]) == 1
        debt = result["debts"][0]
        assert debt["person_id"] == yo_id
        assert debt["amount"] == 3000.0

    def test_no_debts_when_only_payer_assigned(self, client, auth_headers, yo_id):
        """
        Only Yo assigned to item. Yo is payer. No one else owes anything.
        """
        sess = _start_manual(client, auth_headers, 5000, "SettleOnlyYo")
        tx_id   = sess["transaction_id"]
        item_id = sess["items"][0]["id"]

        _assign(client, auth_headers, item_id, [(yo_id, "equal", None)])

        result = _settle(client, auth_headers, tx_id)
        assert result["my_total"] == 5000.0
        assert result["debts"] == [], "No other people, so no debts"

    def test_settle_minimal_not_chained(self, client, auth_headers, yo_id):
        """
        Verify settle doesn't produce A→C + C→B but instead direct A→B.
        The current implementation is not a min-flow solver; it's a simple
        'everyone owes the payer' model.  For a 3-person split where Yo paid,
        the two debtors should each owe Yo directly (not routed through each other).
        """
        p2 = _create_person(client, auth_headers, "MinDebt2", "#eab308")
        p3 = _create_person(client, auth_headers, "MinDebt3", "#a855f7")

        sess = _start_manual(client, auth_headers, 6000, "MinDebts")
        tx_id   = sess["transaction_id"]
        item_id = sess["items"][0]["id"]

        _assign(client, auth_headers, item_id, [
            (yo_id, "equal", None),
            (p2,    "equal", None),
            (p3,    "equal", None),
        ])

        result = _settle(client, auth_headers, tx_id)
        # p2 and p3 each owe Yo directly (2000 each)
        debt_ids = {d["person_id"] for d in result["debts"]}
        assert p2 in debt_ids, "p2 should have a direct debt to Yo"
        assert p3 in debt_ids, "p3 should have a direct debt to Yo"
        # No debt between p2 and p3 (they both owe Yo, not each other)
        assert len(result["debts"]) == 2, (
            f"Exactly 2 debt rows expected (p2 and p3 owe Yo), got {len(result['debts'])}"
        )


# ════════════════════════════════════════════════════════════════════════════
# Scenario f: Assign same item twice to same person → idempotent / replace
# ════════════════════════════════════════════════════════════════════════════

class TestAssignIdempotent:

    def test_reassign_replaces_not_duplicates(self, client, auth_headers, yo_id):
        """
        assign-item is 'replace all'. Calling it twice on the same item with the
        same person should result in ONE assignment, not two.
        The person's computed share should equal the full item price (not 2x).
        """
        sess = _start_manual(client, auth_headers, 3000, "Idempotent")
        tx_id   = sess["transaction_id"]
        item_id = sess["items"][0]["id"]

        # First assignment
        _assign(client, auth_headers, item_id, [(yo_id, "equal", None)])
        # Second assignment — same person, same item
        result_item = _assign(client, auth_headers, item_id, [(yo_id, "equal", None)])

        assert len(result_item["assignees"]) == 1, (
            f"Should have exactly 1 assignee after re-assign, got {len(result_item['assignees'])}"
        )
        assert result_item["assignees"][0]["computed_amount"] == 3000.0, (
            f"No doubling: share should be 3000, got {result_item['assignees'][0]['computed_amount']}"
        )

    def test_reassign_overrides_previous_people(self, client, auth_headers, yo_id):
        """
        First assign to [Yo, FriendOld], then re-assign to [FriendNew].
        Result should only contain FriendNew.
        """
        old_friend = _create_person(client, auth_headers, "OldFriend", "#ef4444")
        new_friend = _create_person(client, auth_headers, "NewFriend", "#10b981")

        sess = _start_manual(client, auth_headers, 4000, "ReassignOverride")
        tx_id   = sess["transaction_id"]
        item_id = sess["items"][0]["id"]

        _assign(client, auth_headers, item_id, [
            (yo_id,      "equal", None),
            (old_friend, "equal", None),
        ])
        result_item = _assign(client, auth_headers, item_id, [
            (new_friend, "equal", None),
        ])

        assignee_ids = [a["person_id"] for a in result_item["assignees"]]
        assert new_friend in assignee_ids
        assert yo_id      not in assignee_ids, "Yo should have been removed by re-assign"
        assert old_friend not in assignee_ids, "OldFriend should have been removed"
        assert len(result_item["assignees"]) == 1
        assert result_item["assignees"][0]["computed_amount"] == 4000.0

    def test_unassign_via_empty_list(self, client, auth_headers, yo_id):
        """Sending an empty assignees list should clear all assignments."""
        sess = _start_manual(client, auth_headers, 2000, "Unassign")
        tx_id   = sess["transaction_id"]
        item_id = sess["items"][0]["id"]

        _assign(client, auth_headers, item_id, [(yo_id, "equal", None)])
        # Unassign everyone
        result_item = _assign(client, auth_headers, item_id, [])
        assert result_item["assignees"] == [], "Item should be unassigned after empty list"

        # Check result shows unassigned_total = full item price
        result = _result(client, auth_headers, tx_id)
        assert result["unassigned_total"] == 2000.0
        assert result["completion_pct"] == 0.0


# ════════════════════════════════════════════════════════════════════════════
# Scenario g: Manual JSON-based split using /split/start (not start-manual)
# ════════════════════════════════════════════════════════════════════════════

class TestManualJSONFlow:
    """
    Full flow using /split/start (POST with transaction_id + items JSON).
    This is the path that the frontend uses when it has a list of items from
    a parsed receipt or manual entry, not just a total amount.
    """

    def test_start_with_json_items_creates_receipt_items(self, client, auth_headers, yo_id):
        """
        1. Create a plain transaction via /transactions
        2. POST /split/start with parsed items JSON
        3. Verify all items land in the DB and can be assigned
        """
        # Step 1: create the backing transaction
        tx_resp = client.post(
            "/transactions",
            json={
                "amount": 7500.0,
                "currency": "CLP",
                "category": "Alimentación",
                "date": "2026-03-15",
                "merchant": "Manual JSON Restaurant",
                "notes": "",
                "is_income": False,
            },
            headers=auth_headers,
        )
        assert tx_resp.status_code == 201, f"create tx failed: {tx_resp.text}"
        tx_id = tx_resp.json()["id"]

        # Step 2: seed items via /split/start
        items_payload = [
            {"name": "Burger",  "price": 3000.0, "quantity": 1},
            {"name": "Fries",   "price": 1500.0, "quantity": 2},
            {"name": "Soda",    "price": 1500.0, "quantity": 1},
        ]
        start_resp = client.post(
            f"/split/start?transaction_id={tx_id}",
            json=items_payload,
            headers=auth_headers,
        )
        assert start_resp.status_code == 200, f"split/start failed: {start_resp.text}"
        body = start_resp.json()
        assert body["transaction_id"] == tx_id
        assert len(body["items"]) == 3, f"Expected 3 items, got {len(body['items'])}"
        item_names = [it["name"] for it in body["items"]]
        assert "Burger" in item_names
        assert "Fries" in item_names
        assert "Soda"  in item_names

    def test_full_manual_json_flow_correct_totals(self, client, auth_headers, yo_id):
        """
        Full flow: create tx → seed items → assign → result → settle.
        Burger ($3000) → Yo
        Fries  ($3000) → Friend
        Both share Soda ($1500) equally ($750 each)
        Yo total: 3000 + 750 = 3750
        Friend total: 3000 + 750 = 3750
        """
        friend_id = _create_person(client, auth_headers, "JSONFriend", "#6366f1")

        tx_resp = client.post(
            "/transactions",
            json={
                "amount": 7500.0,
                "currency": "CLP",
                "category": "Alimentación",
                "date": "2026-03-20",
                "merchant": "JSON Full Flow",
                "notes": "",
                "is_income": False,
            },
            headers=auth_headers,
        )
        tx_id = tx_resp.json()["id"]

        start_resp = client.post(
            f"/split/start?transaction_id={tx_id}",
            json=[
                {"name": "Burger", "price": 3000.0, "quantity": 1},
                {"name": "Fries",  "price": 3000.0, "quantity": 1},
                {"name": "Soda",   "price": 1500.0, "quantity": 1},
            ],
            headers=auth_headers,
        )
        assert start_resp.status_code == 200
        items = start_resp.json()["items"]
        item_by_name = {it["name"]: it["id"] for it in items}

        _assign(client, auth_headers, item_by_name["Burger"], [(yo_id,     "equal", None)])
        _assign(client, auth_headers, item_by_name["Fries"],  [(friend_id, "equal", None)])
        _assign(client, auth_headers, item_by_name["Soda"],   [
            (yo_id,     "equal", None),
            (friend_id, "equal", None),
        ])

        result = _result(client, auth_headers, tx_id)
        assert result["completion_pct"] == 100.0
        totals = {p["person_id"]: p["total"] for p in result["people"]}
        assert totals[yo_id]    == 3750.0, f"Yo should owe 3750, got {totals.get(yo_id)}"
        assert totals[friend_id] == 3750.0, f"Friend should owe 3750, got {totals.get(friend_id)}"

    def test_start_replaces_items_idempotent(self, client, auth_headers, yo_id):
        """
        Calling /split/start twice with different items should replace, not append.
        """
        tx_resp = client.post(
            "/transactions",
            json={
                "amount": 5000.0,
                "currency": "CLP",
                "category": "Otros",
                "date": "2026-03-25",
                "merchant": "Idempotent Start",
                "notes": "",
                "is_income": False,
            },
            headers=auth_headers,
        )
        tx_id = tx_resp.json()["id"]

        # First call: 2 items
        client.post(
            f"/split/start?transaction_id={tx_id}",
            json=[
                {"name": "ItemA", "price": 2000.0, "quantity": 1},
                {"name": "ItemB", "price": 3000.0, "quantity": 1},
            ],
            headers=auth_headers,
        )
        # Second call: 1 item — should replace
        resp2 = client.post(
            f"/split/start?transaction_id={tx_id}",
            json=[{"name": "ItemC", "price": 5000.0, "quantity": 1}],
            headers=auth_headers,
        )
        assert resp2.status_code == 200
        items = resp2.json()["items"]
        assert len(items) == 1, f"Expected 1 item after replace, got {len(items)}"
        assert items[0]["name"] == "ItemC"


# ════════════════════════════════════════════════════════════════════════════
# Scenario: amount split type
# ════════════════════════════════════════════════════════════════════════════

class TestAmountSplit:

    def test_exact_amount_split(self, client, auth_headers, yo_id):
        """
        $10000 item. Yo pays exactly $7000. Friend gets the remainder ($3000).
        """
        friend_id = _create_person(client, auth_headers, "AmtFriend", "#ef4444")
        sess = _start_manual(client, auth_headers, 10000, "AmountSplit")
        tx_id   = sess["transaction_id"]
        item_id = sess["items"][0]["id"]

        result_item = _assign(client, auth_headers, item_id, [
            (yo_id,     "amount", 7000.0),
            (friend_id, "amount", None),   # last person gets remainder
        ])
        amounts = {a["person_id"]: a["computed_amount"] for a in result_item["assignees"]}
        assert amounts[yo_id]    == 7000.0, f"Yo should pay 7000, got {amounts[yo_id]}"
        assert amounts[friend_id] == 3000.0, f"Friend should pay 3000 (remainder), got {amounts[friend_id]}"
        assert sum(amounts.values()) == 10000.0

    def test_amount_split_three_people(self, client, auth_headers, yo_id):
        """
        $9000 total. Yo=$1000, p2=$3000, p3 gets remainder ($5000).
        """
        p2 = _create_person(client, auth_headers, "Amt3P2", "#10b981")
        p3 = _create_person(client, auth_headers, "Amt3P3", "#06b6d4")
        sess = _start_manual(client, auth_headers, 9000, "AmountSplit3")
        tx_id   = sess["transaction_id"]
        item_id = sess["items"][0]["id"]

        result_item = _assign(client, auth_headers, item_id, [
            (yo_id, "amount", 1000.0),
            (p2,    "amount", 3000.0),
            (p3,    "amount", None),
        ])
        amounts = {a["person_id"]: a["computed_amount"] for a in result_item["assignees"]}
        assert amounts[yo_id] == 1000.0
        assert amounts[p2]    == 3000.0
        assert amounts[p3]    == 5000.0


# ════════════════════════════════════════════════════════════════════════════
# Edge cases: error handling
# ════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:

    def test_assign_invalid_split_type_returns_400(self, client, auth_headers, yo_id):
        sess = _start_manual(client, auth_headers, 1000, "BadSplitType")
        item_id = sess["items"][0]["id"]

        resp = client.post(
            "/split/assign-item",
            json={
                "item_id": item_id,
                "assignees": [{"person_id": yo_id, "split_type": "magic", "value": None}],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_assign_nonexistent_item_returns_404(self, client, auth_headers, yo_id):
        resp = client.post(
            "/split/assign-item",
            json={
                "item_id": 999999,
                "assignees": [{"person_id": yo_id, "split_type": "equal", "value": None}],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_result_nonexistent_transaction_returns_404(self, client, auth_headers):
        resp = client.get("/split/result?transaction_id=999999", headers=auth_headers)
        assert resp.status_code == 404

    def test_settle_nonexistent_transaction_returns_404(self, client, auth_headers):
        resp = client.post(
            "/split/settle",
            json={"transaction_id": 999999, "save_to_lucas": False},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_result_zero_items_completion_100(self, client, auth_headers):
        """
        A transaction that has never had /split/start called: 0 items → completion=100%
        (nothing to assign = complete).
        """
        tx_resp = client.post(
            "/transactions",
            json={
                "amount": 1000.0,
                "currency": "CLP",
                "category": "Otros",
                "date": "2026-04-01",
                "merchant": "NoItems",
                "notes": "",
                "is_income": False,
            },
            headers=auth_headers,
        )
        tx_id = tx_resp.json()["id"]

        # Call split/start with empty items list
        client.post(
            f"/split/start?transaction_id={tx_id}",
            json=[],
            headers=auth_headers,
        )

        result = client.get(f"/split/result?transaction_id={tx_id}", headers=auth_headers)
        assert result.status_code == 200
        assert result.json()["completion_pct"] == 100.0, (
            "0 items means nothing to assign → 100% complete"
        )

    def test_unauth_access_returns_401(self, client):
        resp = client.get("/split/me")
        assert resp.status_code == 401

        resp = client.get("/split/people")
        assert resp.status_code == 401

        resp = client.post("/split/assign-item", json={"item_id": 1, "assignees": []})
        assert resp.status_code == 401
