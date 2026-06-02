"""
Tests for _normalize_boleta_items() and _to_float() in ocr.py.

These cover the proportional normalization that ensures item prices always
sum to TOTAL NETO, plus IVA rounding and negative-price (discount) handling.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import unittest.mock as _mock
_mock.patch.dict("sys.modules", {
    "cv2": _mock.MagicMock(),
    "pytesseract": _mock.MagicMock(),
    "PIL": _mock.MagicMock(),
    "PIL.Image": _mock.MagicMock(),
}).start()

from app.ocr import _normalize_boleta_items, _fix_line_total_items, _scale_to_total, _to_float
from app.schemas import ParsedItem


# ─────────────────────────────────────────────────────────────────────────────
# _to_float
# ─────────────────────────────────────────────────────────────────────────────

def test_to_float_clp_no_decimals():
    assert _to_float("17.517") == 17517.0

def test_to_float_usd_with_decimals():
    # If trailing digits == 2, treat as decimal
    assert _to_float("17.51") == 17.51

def test_to_float_negative():
    assert _to_float("-3.490") == -3490.0

def test_to_float_positive_sign():
    assert _to_float("+150.000") == 150000.0

def test_to_float_empty_string():
    assert _to_float("") == 0.0

def test_to_float_dollar_sign():
    assert _to_float("$ 29.521") == 29521.0


# ─────────────────────────────────────────────────────────────────────────────
# _normalize_boleta_items — basic cases
# ─────────────────────────────────────────────────────────────────────────────

def make_item(name, price, qty=1):
    return ParsedItem(name=name, price=price, quantity=qty)


def test_normalize_items_sum_equals_neto():
    """After normalization, sum(price*qty) for product items should == total_neto."""
    items = [
        make_item("Producto A", 5000),
        make_item("Producto B", 3000),
        make_item("Producto C", 2000),
    ]
    total_neto = 9800.0  # Slightly different from 10000 (simulates LLM rounding)
    iva = round(total_neto * 0.19)

    normalized, auth_total = _normalize_boleta_items(items, total_neto, iva)

    # IVA row appended
    product_items = [it for it in normalized if "iva" not in it.name.lower()]
    iva_items = [it for it in normalized if "iva" in it.name.lower()]

    assert len(iva_items) == 1, "Should have exactly one IVA row"
    assert abs(iva_items[0].price - iva) <= 1, f"IVA row price {iva_items[0].price} should ≈ {iva}"

    product_sum = sum(it.price * it.quantity for it in product_items)
    assert abs(product_sum - total_neto) <= 1, \
        f"Product sum {product_sum} should ≈ total_neto {total_neto}"

    assert abs(auth_total - (total_neto + iva)) <= 1


def test_normalize_authoritative_total_returned():
    items = [make_item("Item A", 10000), make_item("Item B", 5000)]
    total_neto = 14000.0
    iva = 2660.0
    _, auth_total = _normalize_boleta_items(items, total_neto, iva)
    assert auth_total == total_neto + iva


def test_normalize_strips_existing_iva_row():
    """If LLM already added an IVA item, normalization removes it and re-adds the correct one."""
    items = [
        make_item("Producto A", 8000),
        make_item("IVA (19%)", 1000),   # wrong IVA amount from LLM
    ]
    total_neto = 8000.0
    correct_iva = round(total_neto * 0.19)  # 1520

    normalized, _ = _normalize_boleta_items(items, total_neto, correct_iva)
    iva_rows = [it for it in normalized if "iva" in it.name.lower()]
    assert len(iva_rows) == 1
    assert iva_rows[0].price == correct_iva, \
        f"IVA should be {correct_iva}, got {iva_rows[0].price}"


def test_normalize_with_quantity_gt_1():
    """Items with quantity > 1 are scaled correctly."""
    items = [
        make_item("POLLO", 4990, qty=2),   # line_total = 9980
        make_item("PAN",   1750, qty=1),
    ]
    # LLM sum = 9980 + 1750 = 11730
    total_neto = 11600.0  # Slight rounding difference
    iva = round(total_neto * 0.19)

    normalized, _ = _normalize_boleta_items(items, total_neto, iva)
    product_items = [it for it in normalized if "iva" not in it.name.lower()]
    product_sum = sum(it.price * it.quantity for it in product_items)
    assert abs(product_sum - total_neto) <= 1, \
        f"Product sum {product_sum} should ≈ {total_neto}"


def test_normalize_single_item():
    """Single product: its price absorbs all rounding."""
    items = [make_item("Producto Unico", 9999)]
    total_neto = 10000.0
    iva = 1900.0

    normalized, auth_total = _normalize_boleta_items(items, total_neto, iva)
    product_items = [it for it in normalized if "iva" not in it.name.lower()]
    assert len(product_items) == 1
    assert product_items[0].price == 10000


def test_normalize_empty_items_returns_unchanged():
    """No product items → return original (unchanged) with authoritative total."""
    items = []
    total_neto = 5000.0
    iva = 950.0

    normalized, auth_total = _normalize_boleta_items(items, total_neto, iva)
    assert normalized == []
    assert auth_total == total_neto + iva


def test_normalize_iva_only_items_returns_unchanged():
    """Only IVA rows (no product rows) → return original, no crash."""
    items = [make_item("IVA (19%)", 1900)]
    total_neto = 10000.0
    iva = 1900.0

    normalized, auth_total = _normalize_boleta_items(items, total_neto, iva)
    assert auth_total == total_neto + iva


def test_normalize_last_item_absorbs_rounding():
    """3 items: first two get rounded price, last absorbs remainder."""
    # 3 items, total_neto = 10001 (prime-ish, causes rounding)
    items = [
        make_item("A", 3334),
        make_item("B", 3334),
        make_item("C", 3333),
    ]
    total_neto = 10001.0
    iva = round(total_neto * 0.19)

    normalized, _ = _normalize_boleta_items(items, total_neto, iva)
    product_items = [it for it in normalized if "iva" not in it.name.lower()]
    product_sum = sum(it.price * it.quantity for it in product_items)
    assert abs(product_sum - total_neto) <= 1, \
        f"Last item should absorb rounding: sum={product_sum}, neto={total_neto}"


# ─────────────────────────────────────────────────────────────────────────────
# Quantity bug regression: LLM returns line_total as price when qty > 1
# After normalization, unit prices must be correct
# ─────────────────────────────────────────────────────────────────────────────

def test_normalize_fixes_llm_line_total_as_price():
    """
    Regression: LLM returns {price: 10000, qty: 2} meaning line_total=10000,
    but stores it as unit_price=10000 → llm_sum = 20000.
    After normalization to total_neto=10000, new price should be ~5000.
    """
    items = [make_item("Hamburguesa", 10000, qty=2)]  # LLM used line_total as price
    total_neto = 10000.0  # The actual neto (line total for this item)
    iva = 1900.0

    normalized, auth_total = _normalize_boleta_items(items, total_neto, iva)
    product_items = [it for it in normalized if "iva" not in it.name.lower()]

    assert len(product_items) == 1
    burger = product_items[0]
    # After normalization: unit_price should be ~5000, qty=2, line_total=10000
    assert burger.quantity == 2
    assert abs(burger.price * burger.quantity - total_neto) <= 1, \
        f"Line total {burger.price * burger.quantity} should ≈ {total_neto}"
    # Unit price should be roughly half of what was given (since qty=2)
    assert burger.price <= 6000, \
        f"Unit price {burger.price} seems too high after normalization (expected ~5000)"


# ─────────────────────────────────────────────────────────────────────────────
# _fix_line_total_items — real-world restaurant receipt regressions
# ─────────────────────────────────────────────────────────────────────────────

def test_fix_single_item_vienesa():
    """Regression: '3 VIENESA ITALIANA 13200' → LLM returns price=13200,qty=3.
    Only VIENESA is wrong; SCHOP items are correct. Single-item algebraic fix."""
    items = [
        make_item("VIENESA ITALIANA", 13200, qty=3),   # wrong: should be 4400
        make_item("SCHOP MEDIO ROYAL", 4800, qty=6),   # correct
        make_item("SCHOP MEDIO ESCUDO", 4400, qty=2),  # correct
    ]
    ref_total = 50800.0
    fixed = _fix_line_total_items(items, ref_total)
    assert fixed[0].price == 4400, f"VIENESA unit price should be 4400, got {fixed[0].price}"
    assert fixed[1].price == 4800, "SCHOP ROYAL should be unchanged"
    assert fixed[2].price == 4400, "SCHOP ESCUDO should be unchanged"
    assert sum(it.price * it.quantity for it in fixed) == ref_total


def test_fix_pair_two_items_wrong():
    """Two items have line-total-as-unit-price. Pair algebraic fix."""
    items = [
        make_item("Cerveza", 9600, qty=2),   # should be 4800 (9600/2)
        make_item("Papas", 6000, qty=3),     # should be 2000 (6000/3)
        make_item("Agua", 1500, qty=1),      # correct
    ]
    # correct sum: 4800*2 + 2000*3 + 1500*1 = 9600 + 6000 + 1500 = 17100
    ref_total = 17100.0
    fixed = _fix_line_total_items(items, ref_total)
    assert fixed[0].price == 4800
    assert fixed[1].price == 2000
    assert fixed[2].price == 1500
    assert sum(it.price * it.quantity for it in fixed) == ref_total


def test_fix_global_all_items_wrong():
    """All multi-qty items store line total. Global fix divides all by qty."""
    items = [
        make_item("Completo", 12000, qty=3),  # should be 4000
        make_item("Schop", 8800, qty=2),      # should be 4400
    ]
    ref_total = 20800.0  # 4000*3 + 4400*2
    fixed = _fix_line_total_items(items, ref_total)
    assert fixed[0].price == 4000
    assert fixed[1].price == 4400
    assert sum(it.price * it.quantity for it in fixed) == ref_total


def test_fix_already_correct():
    """No fix applied when sum already matches total."""
    items = [
        make_item("SCHOP MEDIO ROYAL", 4800, qty=6),
        make_item("SCHOP MEDIO ESCUDO", 4400, qty=2),
    ]
    ref_total = 37600.0  # 28800 + 8800
    fixed = _fix_line_total_items(items, ref_total)
    assert fixed[0].price == 4800
    assert fixed[1].price == 4400


# ─────────────────────────────────────────────────────────────────────────────
# _scale_to_total — final proportional normalization guarantee
# ─────────────────────────────────────────────────────────────────────────────

def test_scale_guarantees_sum_equals_total():
    """After scale_to_total, sum(price*qty) must equal ref_total when sum > ref (over-count)."""
    # cur = 5100*3 + 1200*2 = 17700; ref = 15000 → over by 18%, scale DOWN
    items = [
        make_item("Cerveza", 5100, qty=3),
        make_item("Agua", 1200, qty=2),
    ]
    ref_total = 15000.0
    scaled, amount = _scale_to_total(items, ref_total)
    assert amount == ref_total
    # ±1 CLP tolerance: unavoidable when remainder is not divisible by qty
    assert abs(sum(it.price * it.quantity for it in scaled) - ref_total) <= 1


def test_scale_no_op_when_already_correct():
    """_scale_to_total is a no-op when sum is already within 2% of ref."""
    items = [make_item("Item", 5000, qty=2)]
    ref_total = 10000.0
    scaled, amount = _scale_to_total(items, ref_total)
    assert scaled[0].price == 5000  # unchanged


def test_scale_never_inflates_prices():
    """_scale_to_total must NOT inflate when sum < ref_total (under-count case).
    The frontend 'Otros cargos' handles the gap instead."""
    items = [
        make_item("Piscolón Mistral de 35°", 9000, qty=1),
        make_item("Fernet Branca", 5800, qty=1),
    ]
    # sum = 14800, ref_total = 50000 — LLM missed many items
    scaled, amount = _scale_to_total(items, 50000.0)
    # prices must NOT be inflated
    assert scaled[0].price == 9000
    assert scaled[1].price == 5800
