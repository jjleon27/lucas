"""
Unit tests for _compute_shares() in app/routers/split.py.

These tests are intentionally standalone — no server, database, or network
needed. Run with:
    cd backend && python -m pytest tests/test_split_logic.py -v
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

from app.routers.split import _compute_shares  # noqa: E402
from types import SimpleNamespace


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_a(pid, split_type="equal", value=None):
    """Create a mock ItemAssignment-like object."""
    return SimpleNamespace(person_id=pid, split_type=split_type, value=value)


def total_assigned(shares: dict) -> float:
    return round(sum(shares.values()), 2)


# ─────────────────────────────────────────────────────────────────────────────
# EQUAL split
# ─────────────────────────────────────────────────────────────────────────────

class TestEqualSplit:
    def test_two_people_even_split(self):
        """10000 / 2 people → each gets 5000."""
        assignees = [make_a(1), make_a(2)]
        result = _compute_shares(10000, assignees)
        assert result[1] == 5000.0
        assert result[2] == 5000.0

    def test_two_people_total_preserved(self):
        """Sum of shares must equal line_total within ±1."""
        assignees = [make_a(1), make_a(2)]
        result = _compute_shares(10000, assignees)
        assert abs(total_assigned(result) - 10000) <= 1

    def test_three_people_rounding_applied_to_last(self):
        """
        10000 / 3 = 3333.33 each. After rounding to cents:
        first two get 3333.33, last gets the rounding remainder so the sum
        is exactly 10000.
        """
        assignees = [make_a(1), make_a(2), make_a(3)]
        result = _compute_shares(10000, assignees)
        assert abs(total_assigned(result) - 10000) <= 1
        # The last person absorbs the rounding diff
        total = result[1] + result[2] + result[3]
        assert round(total, 2) == 10000.0

    def test_three_people_two_equal_thirds(self):
        """First two people get equal shares; last may differ by ≤1."""
        assignees = [make_a(1), make_a(2), make_a(3)]
        result = _compute_shares(10000, assignees)
        assert result[1] == result[2]

    def test_one_person_gets_everything(self):
        """Single assignee in equal mode gets the full amount."""
        assignees = [make_a(42)]
        result = _compute_shares(10000, assignees)
        assert result[42] == 10000.0

    def test_empty_list_returns_empty_dict(self):
        """No assignees → empty result (no crash)."""
        result = _compute_shares(10000, [])
        assert result == {}

    def test_negative_line_total_two_people(self):
        """Discount of -5000 split equally among 2 → each -2500."""
        assignees = [make_a(1), make_a(2)]
        result = _compute_shares(-5000, assignees)
        assert result[1] == -2500.0
        assert result[2] == -2500.0

    def test_negative_total_preserved(self):
        """Sum of shares on a discount must equal the negative line_total."""
        assignees = [make_a(1), make_a(2), make_a(3)]
        result = _compute_shares(-9000, assignees)
        assert abs(total_assigned(result) - (-9000)) <= 1

    def test_zero_line_total(self):
        """Zero amount → everyone gets 0."""
        assignees = [make_a(1), make_a(2)]
        result = _compute_shares(0, assignees)
        assert result[1] == 0.0
        assert result[2] == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# PERCENT split
# ─────────────────────────────────────────────────────────────────────────────

class TestPercentSplit:
    def test_two_people_explicit_and_remainder(self):
        """
        Person A = 70%, person B has no explicit pct (is the last) →
        B gets the remainder = 30%.
        """
        assignees = [make_a(1, "percent", 70), make_a(2, "percent", None)]
        result = _compute_shares(10000, assignees)
        assert result[1] == 7000.0
        assert result[2] == 3000.0

    def test_two_people_total_preserved(self):
        assignees = [make_a(1, "percent", 40), make_a(2, "percent", None)]
        result = _compute_shares(10000, assignees)
        assert abs(total_assigned(result) - 10000) <= 1

    def test_three_people_percent_split(self):
        """60% + 30% + remainder(10%) among 3 people."""
        assignees = [
            make_a(1, "percent", 60),
            make_a(2, "percent", 30),
            make_a(3, "percent", None),
        ]
        result = _compute_shares(10000, assignees)
        assert result[1] == 6000.0
        assert result[2] == 3000.0
        assert result[3] == 1000.0

    def test_three_people_total_preserved(self):
        assignees = [
            make_a(1, "percent", 33),
            make_a(2, "percent", 33),
            make_a(3, "percent", None),
        ]
        result = _compute_shares(10000, assignees)
        assert abs(total_assigned(result) - 10000) <= 1

    def test_last_person_absorbs_rounding(self):
        """Last person always gets line_total minus everything else."""
        assignees = [make_a(1, "percent", 33.33), make_a(2, "percent", None)]
        result = _compute_shares(10000, assignees)
        assert abs(total_assigned(result) - 10000) <= 1


# ─────────────────────────────────────────────────────────────────────────────
# AMOUNT split
# ─────────────────────────────────────────────────────────────────────────────

class TestAmountSplit:
    def test_two_people_fixed_and_remainder(self):
        """A=3000 fixed; B gets 10000 - 3000 = 7000."""
        assignees = [make_a(1, "amount", 3000), make_a(2, "amount", None)]
        result = _compute_shares(10000, assignees)
        assert result[1] == 3000.0
        assert result[2] == 7000.0

    def test_two_people_total_preserved(self):
        assignees = [make_a(1, "amount", 4500), make_a(2, "amount", None)]
        result = _compute_shares(10000, assignees)
        assert abs(total_assigned(result) - 10000) <= 1

    def test_three_people_two_fixed_one_remainder(self):
        """A=2000, B=3000, C gets 10000 - 2000 - 3000 = 5000."""
        assignees = [
            make_a(1, "amount", 2000),
            make_a(2, "amount", 3000),
            make_a(3, "amount", None),
        ]
        result = _compute_shares(10000, assignees)
        assert result[1] == 2000.0
        assert result[2] == 3000.0
        assert result[3] == 5000.0

    def test_three_people_total_preserved(self):
        assignees = [
            make_a(1, "amount", 1111),
            make_a(2, "amount", 2222),
            make_a(3, "amount", None),
        ]
        result = _compute_shares(10000, assignees)
        assert abs(total_assigned(result) - 10000) <= 1

    def test_fixed_amount_zero(self):
        """Amount=0 is valid; last person gets everything."""
        assignees = [make_a(1, "amount", 0), make_a(2, "amount", None)]
        result = _compute_shares(5000, assignees)
        assert result[1] == 0.0
        assert result[2] == 5000.0


# ─────────────────────────────────────────────────────────────────────────────
# Invariant: sum always equals line_total within ±1 CLP
# ─────────────────────────────────────────────────────────────────────────────

class TestSumInvariant:
    """The total of all shares must always equal line_total (within ±1 CLP)."""

    def test_equal_large_odd_amount(self):
        assignees = [make_a(i) for i in range(7)]
        result = _compute_shares(99999, assignees)
        assert abs(total_assigned(result) - 99999) <= 1

    def test_percent_large_split(self):
        assignees = [make_a(1, "percent", 17), make_a(2, "percent", 33), make_a(3, "percent", None)]
        result = _compute_shares(50000, assignees)
        assert abs(total_assigned(result) - 50000) <= 1

    def test_amount_large_split(self):
        assignees = [make_a(1, "amount", 12345), make_a(2, "amount", 11111), make_a(3, "amount", None)]
        result = _compute_shares(50000, assignees)
        assert abs(total_assigned(result) - 50000) <= 1
