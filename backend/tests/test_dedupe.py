"""
Unit tests for find_duplicate() and helpers in app/services/dedupe.py.

Uses an in-memory SQLite database so no real Postgres is needed.

Run with:
    cd backend && python -m pytest tests/test_dedupe.py -v
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import unittest.mock as _mock
_mock.patch.dict("sys.modules", {
    "cv2": _mock.MagicMock(),
    "pytesseract": _mock.MagicMock(),
    "PIL": _mock.MagicMock(),
    "PIL.Image": _mock.MagicMock(),
    "psycopg2": _mock.MagicMock(),
}).start()

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("AI_PROVIDER", "none")

import pytest
from datetime import date, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app import models
from app.services.dedupe import find_duplicate, _merchant_similar, _tokens
from app.schemas import ParsedReceipt


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture
def user(db):
    """Create a fresh user and delete it (plus its transactions) after the test."""
    u = models.User(
        email=f"test_{id(object())}@test.com",
        hashed_password="x",
        monthly_budget=0.0,
        settings={},
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    yield u
    db.query(models.Transaction).filter_by(user_id=u.id).delete()
    db.delete(u)
    db.commit()


def make_tx(db, user_id, amount=10000, merchant="LIDER", tx_date=None,
            currency="CLP", is_income=False, account_id=None):
    """Helper: create and persist a Transaction, return it."""
    t = models.Transaction(
        user_id=user_id,
        account_id=account_id,
        amount=amount,
        currency=currency,
        category="Otros",
        date=tx_date or date(2024, 6, 15),
        merchant=merchant,
        notes="",
        image_url="",
        raw_ocr="",
        is_income=is_income,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def make_proposed(amount=10000, merchant="LIDER", tx_date=None,
                  currency="CLP", is_income=False, description=""):
    return ParsedReceipt(
        amount=amount,
        date=tx_date or date(2024, 6, 15),
        merchant=merchant,
        category="Otros",
        currency=currency,
        is_income=is_income,
        description=description,
    )


# ─────────────────────────────────────────────────────────────────────────────
# _tokens()
# ─────────────────────────────────────────────────────────────────────────────

class TestTokens:
    def test_basic_split(self):
        result = _tokens("UBER EATS")
        assert "uber" in result
        assert "eats" in result

    def test_short_tokens_filtered(self):
        """Single-character tokens (and "el", "la" length-2 are OK, length-1 not)."""
        result = _tokens("a el mercado")
        assert "a" not in result  # 'a' is length 1 → filtered
        assert "el" in result     # length 2 → kept
        assert "mercado" in result

    def test_empty_string(self):
        assert _tokens("") == set()

    def test_none_like_empty(self):
        # The function uses `s or ""` so None-ish input works safely
        assert _tokens(None) == set()  # type: ignore[arg-type]

    def test_numbers_included(self):
        result = _tokens("compra 2024")
        assert "2024" in result

    def test_case_insensitive(self):
        result = _tokens("McDonalds MCDONALD")
        assert "mcdonalds" in result
        assert "mcdonald" in result


# ─────────────────────────────────────────────────────────────────────────────
# _merchant_similar()
# ─────────────────────────────────────────────────────────────────────────────

class TestMerchantSimilar:
    def test_exact_match(self):
        assert _merchant_similar("LIDER", "LIDER") is True

    def test_case_insensitive_exact(self):
        assert _merchant_similar("lider", "LIDER") is True

    def test_substring_a_in_b(self):
        assert _merchant_similar("LIDER", "SUPERMERCADO LIDER") is True

    def test_substring_b_in_a(self):
        assert _merchant_similar("SUPERMERCADO LIDER", "LIDER") is True

    def test_jaccard_above_threshold(self):
        """'LIDER SUPERMERCADO' vs 'SUPERMERCADO LIDER' → same tokens → Jaccard 1.0."""
        assert _merchant_similar("LIDER SUPERMERCADO", "SUPERMERCADO LIDER") is True

    def test_jaccard_below_threshold(self):
        """Completely different merchants → False."""
        assert _merchant_similar("UBER EATS", "BANCO SANTANDER") is False

    def test_empty_a_returns_false(self):
        assert _merchant_similar("", "LIDER") is False

    def test_empty_b_returns_false(self):
        assert _merchant_similar("LIDER", "") is False

    def test_both_empty_returns_false(self):
        assert _merchant_similar("", "") is False

    def test_partial_overlap_above_threshold(self):
        """Two tokens in common out of three → Jaccard = 2/4 = 0.5 → True."""
        # tokens a: {"pizza", "hut", "chile"}
        # tokens b: {"pizza", "hut", "delivery"}
        # inter=2, union=4 → 0.5 → True
        assert _merchant_similar("PIZZA HUT CHILE", "PIZZA HUT DELIVERY") is True

    def test_partial_overlap_below_threshold(self):
        """Only 1 token in common out of 5 distinct → Jaccard < 0.5 → False."""
        assert _merchant_similar("BANCO BCI CREDITO", "RIPLEY TARJETA PREPAGO") is False


# ─────────────────────────────────────────────────────────────────────────────
# find_duplicate()
# ─────────────────────────────────────────────────────────────────────────────

class TestFindDuplicate:
    def test_empty_db_returns_none(self, db, user):
        proposed = make_proposed()
        result = find_duplicate(db, user_id=user.id, account_id=None, proposed=proposed)
        assert result is None

    def test_exact_match_found(self, db, user):
        """Exact amount + date + merchant → duplicate detected."""
        tx = make_tx(db, user.id, amount=10000, merchant="LIDER", tx_date=date(2024, 6, 15))
        proposed = make_proposed(amount=10000, merchant="LIDER", tx_date=date(2024, 6, 15))
        result = find_duplicate(db, user_id=user.id, account_id=None, proposed=proposed)
        assert result is not None
        assert result.id == tx.id
        db.delete(tx); db.commit()

    def test_no_match_when_date_too_far(self, db, user):
        """Date diff of 3 days is outside the ±2-day window → no match."""
        tx = make_tx(db, user.id, amount=10000, merchant="LIDER", tx_date=date(2024, 6, 15))
        proposed = make_proposed(
            amount=10000, merchant="LIDER", tx_date=date(2024, 6, 15) + timedelta(days=3)
        )
        result = find_duplicate(db, user_id=user.id, account_id=None, proposed=proposed)
        assert result is None
        db.delete(tx); db.commit()

    def test_match_within_date_window(self, db, user):
        """Date diff of 2 days (boundary) is inside the window → match."""
        tx = make_tx(db, user.id, amount=10000, merchant="LIDER", tx_date=date(2024, 6, 15))
        proposed = make_proposed(
            amount=10000, merchant="LIDER", tx_date=date(2024, 6, 15) + timedelta(days=2)
        )
        result = find_duplicate(db, user_id=user.id, account_id=None, proposed=proposed)
        assert result is not None
        assert result.id == tx.id
        db.delete(tx); db.commit()

    def test_no_match_when_amount_too_different(self, db, user):
        """Amount differs by more than 0.5 CLP → no match."""
        tx = make_tx(db, user.id, amount=10000, merchant="LIDER", tx_date=date(2024, 6, 15))
        proposed = make_proposed(
            amount=10001.0, merchant="LIDER", tx_date=date(2024, 6, 15)
        )
        result = find_duplicate(db, user_id=user.id, account_id=None, proposed=proposed)
        assert result is None
        db.delete(tx); db.commit()

    def test_match_within_clp_tolerance(self, db, user):
        """Amount differs by 0.5 CLP (boundary) → still a match."""
        tx = make_tx(db, user.id, amount=10000, merchant="LIDER", tx_date=date(2024, 6, 15))
        proposed = make_proposed(
            amount=10000.5, merchant="LIDER", tx_date=date(2024, 6, 15)
        )
        result = find_duplicate(db, user_id=user.id, account_id=None, proposed=proposed)
        assert result is not None
        assert result.id == tx.id
        db.delete(tx); db.commit()

    def test_merchant_similar_triggers_match(self, db, user):
        """Jaccard-similar merchant name → still detected as duplicate."""
        tx = make_tx(db, user.id, amount=5000, merchant="SUPERMERCADO LIDER",
                     tx_date=date(2024, 6, 15))
        proposed = make_proposed(
            amount=5000, merchant="LIDER SUPERMERCADO", tx_date=date(2024, 6, 15)
        )
        result = find_duplicate(db, user_id=user.id, account_id=None, proposed=proposed)
        assert result is not None
        assert result.id == tx.id
        db.delete(tx); db.commit()

    def test_different_merchant_no_match(self, db, user):
        """Completely different merchants, same amount and date → no match."""
        tx = make_tx(db, user.id, amount=5000, merchant="BANCO SANTANDER",
                     tx_date=date(2024, 6, 15))
        proposed = make_proposed(
            amount=5000, merchant="UBER EATS", tx_date=date(2024, 6, 15)
        )
        result = find_duplicate(db, user_id=user.id, account_id=None, proposed=proposed)
        assert result is None
        db.delete(tx); db.commit()

    def test_account_id_filter_matches_same_account(self, db, user):
        """When account_id is specified and matches → match is found."""
        # We use account_id=99 directly (no FK enforcement in SQLite)
        tx = make_tx(db, user.id, amount=10000, merchant="LIDER",
                     tx_date=date(2024, 6, 15), account_id=99)
        proposed = make_proposed(amount=10000, merchant="LIDER", tx_date=date(2024, 6, 15))
        result = find_duplicate(db, user_id=user.id, account_id=99, proposed=proposed)
        assert result is not None
        assert result.id == tx.id
        db.delete(tx); db.commit()

    def test_account_id_filter_blocks_different_account(self, db, user):
        """
        Same tx on account 99; searching for duplicates on account 88 →
        no match because account_id differs.
        """
        tx = make_tx(db, user.id, amount=10000, merchant="LIDER",
                     tx_date=date(2024, 6, 15), account_id=99)
        proposed = make_proposed(amount=10000, merchant="LIDER", tx_date=date(2024, 6, 15))
        result = find_duplicate(db, user_id=user.id, account_id=88, proposed=proposed)
        assert result is None
        db.delete(tx); db.commit()

    def test_no_account_filter_sees_all_accounts(self, db, user):
        """When account_id=None, all accounts are searched."""
        tx = make_tx(db, user.id, amount=10000, merchant="LIDER",
                     tx_date=date(2024, 6, 15), account_id=77)
        proposed = make_proposed(amount=10000, merchant="LIDER", tx_date=date(2024, 6, 15))
        result = find_duplicate(db, user_id=user.id, account_id=None, proposed=proposed)
        assert result is not None
        assert result.id == tx.id
        db.delete(tx); db.commit()

    def test_is_income_flag_must_match(self, db, user):
        """An income transaction is NOT a duplicate of an expense."""
        tx = make_tx(db, user.id, amount=10000, merchant="SUELDO",
                     tx_date=date(2024, 6, 15), is_income=True)
        proposed = make_proposed(
            amount=10000, merchant="SUELDO", tx_date=date(2024, 6, 15), is_income=False
        )
        result = find_duplicate(db, user_id=user.id, account_id=None, proposed=proposed)
        assert result is None
        db.delete(tx); db.commit()

    def test_description_field_used_for_matching(self, db, user):
        """
        When the existing tx merchant matches the proposed description
        (not the proposed merchant), it should still be detected.
        """
        tx = make_tx(db, user.id, amount=8000, merchant="FALABELLA CMR",
                     tx_date=date(2024, 6, 15))
        proposed = make_proposed(
            amount=8000,
            merchant="COMPRA INTERNET",
            tx_date=date(2024, 6, 15),
            description="FALABELLA CMR",
        )
        result = find_duplicate(db, user_id=user.id, account_id=None, proposed=proposed)
        assert result is not None
        assert result.id == tx.id
        db.delete(tx); db.commit()

    def test_user_isolation(self, db, user):
        """Transactions from another user are never returned as duplicates."""
        other_user = models.User(
            email=f"other_{id(object())}@test.com",
            hashed_password="x",
            monthly_budget=0.0,
            settings={},
        )
        db.add(other_user)
        db.commit()
        db.refresh(other_user)

        tx = make_tx(db, other_user.id, amount=10000, merchant="LIDER",
                     tx_date=date(2024, 6, 15))
        proposed = make_proposed(amount=10000, merchant="LIDER", tx_date=date(2024, 6, 15))
        result = find_duplicate(db, user_id=user.id, account_id=None, proposed=proposed)
        assert result is None

        db.delete(tx)
        db.delete(other_user)
        db.commit()
