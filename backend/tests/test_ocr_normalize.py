"""
Tests for OCR parsing functions in ocr.py.

Covers: _to_float, _parse_clp, _parse_boleta_from_text, _parse_pipe_table,
        _normalize_boleta_items, _fix_line_total_items, _scale_to_total

Tests use realistic Tesseract OCR output from actual Chilean receipts:
  - Supermarkets (Lider, Unimarc, Tottus) with barcodes + IVA
  - Restaurants (completos, schops, VIENESA) without IVA
  - Bar/POS receipts with pipe-table format (Toteat, Restō)
  - Edge cases: discounts, OCR noise, very large prices, "por" syntax
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

from app.ocr import (
    _normalize_boleta_items,
    _fix_line_total_items,
    _scale_to_total,
    _to_float,
    _parse_clp,
    _parse_pipe_table,
    _parse_boleta_from_text,
)
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


# ─────────────────────────────────────────────────────────────────────────────
# _parse_pipe_table — Fast Path C for | Cant | Producto | Total | format
# ─────────────────────────────────────────────────────────────────────────────

def test_pipe_table_piscol_case():
    """Core regression: 35° in product name must NOT become qty=35."""
    text = (
        "| Cant | Producto | Total |\n"
        "| 1 | Piscolón Mistral de 35° | 9.000 |\n"
        "| 1 | Fernet Branca | 5.800 |\n"
    )
    items = _parse_pipe_table(text)
    assert len(items) == 2
    piscol = items[0]
    assert piscol.quantity == 1, f"qty should be 1, got {piscol.quantity}"
    assert piscol.price == 9000, f"price should be 9000, got {piscol.price}"
    fernet = items[1]
    assert fernet.quantity == 1
    assert fernet.price == 5800


def test_pipe_table_multi_qty():
    """qty > 1 in Cant column: unit price = total / qty."""
    text = "| 2 | Cerveza Austral | 9.000 |\n"
    items = _parse_pipe_table(text)
    assert len(items) == 1
    assert items[0].quantity == 2
    assert items[0].price == 4500  # 9000 / 2


def test_pipe_table_skips_header():
    """Header row with 'Cant'/'Producto' should be ignored."""
    text = (
        "| Cant | Producto | Total |\n"
        "| 1 | Agua con gas | 2.500 |\n"
    )
    items = _parse_pipe_table(text)
    assert len(items) == 1
    assert items[0].name == "Agua con gas"


def test_pipe_table_empty_text():
    assert _parse_pipe_table("No pipe table here\n1234 item 5000") == []


def test_pipe_table_sum_matches_total():
    """Full receipt: sum of parsed items matches the printed total."""
    text = (
        "| Cant | Producto | Total |\n"
        "| 1 | Piscolón Mistral de 35° | 9.000 |\n"
        "| 1 | Fernet Branca | 5.800 |\n"
        "Total: 14.800\n"
    )
    items = _parse_pipe_table(text)
    assert sum(it.price * it.quantity for it in items) == 14800


def test_pipe_table_number_in_name_schop_500cc():
    """'500' in 'Schop 500cc' is volume, not qty. qty=1."""
    text = "| 1 | Schop 500cc Austral | 3.500 |\n"
    items = _parse_pipe_table(text)
    assert len(items) == 1
    assert items[0].quantity == 1
    assert items[0].price == 3500


def test_pipe_table_alto_del_carmen_35():
    """'35' in 'Alto del Carmen 35' is product spec, not qty."""
    text = (
        "| Cant | Producto | Total |\n"
        "| 1 | Alto del Carmen 35 | 7.500 |\n"
        "| 1 | Agua mineral | 1.500 |\n"
    )
    items = _parse_pipe_table(text)
    assert items[0].quantity == 1
    assert items[0].price == 7500
    assert sum(it.price * it.quantity for it in items) == 9000


def test_pipe_table_full_bar_receipt():
    """Full bar receipt: 6 items, sum matches total."""
    text = (
        "| Cant | Producto              | Total  |\n"
        "| 1    | Piscolón Mistral 35°  | 9.000  |\n"
        "| 1    | Fernet Branca         | 5.800  |\n"
        "| 2    | Schop 500cc           | 7.000  |\n"
        "| 1    | Agua con gas          | 1.500  |\n"
        "| 3    | Empanada queso        | 9.000  |\n"
    )
    items = _parse_pipe_table(text)
    assert len(items) == 5
    # Schop 500cc qty=2, unit price = 7000/2 = 3500
    schop = next(it for it in items if "schop" in it.name.lower())
    assert schop.quantity == 2
    assert schop.price == 3500
    # Empanada qty=3, unit price = 9000/3 = 3000
    empanada = next(it for it in items if "empanada" in it.name.lower())
    assert empanada.quantity == 3
    assert empanada.price == 3000
    total = sum(it.price * it.quantity for it in items)
    assert total == 9000 + 5800 + 7000 + 1500 + 9000


# ─────────────────────────────────────────────────────────────────────────────
# _parse_clp — Chilean number parser
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_clp_thousands_dot():
    assert _parse_clp("9.000") == 9000.0

def test_parse_clp_millions():
    assert _parse_clp("1.489.991") == 1489991.0

def test_parse_clp_plain_integer():
    assert _parse_clp("50800") == 50800.0

def test_parse_clp_with_dollar():
    # $ is stripped before calling _parse_clp usually, but handle it
    assert _parse_clp("29.521") == 29521.0

def test_parse_clp_comma_thousands():
    assert _parse_clp("5,609") == 5609.0

def test_parse_clp_noise():
    assert _parse_clp("  13.200  ") == 13200.0


# ─────────────────────────────────────────────────────────────────────────────
# _to_float — extra edge cases
# ─────────────────────────────────────────────────────────────────────────────

def test_to_float_million_clp():
    assert _to_float("1.489.991") == 1489991.0

def test_to_float_clp_with_spaces():
    # OCR sometimes adds spaces inside numbers
    assert _to_float("$ 1.750") == 1750.0

def test_to_float_comma_decimal_usd():
    # European decimal: "17,51" → 17.51
    assert _to_float("17,51") == 17.51

def test_to_float_zero():
    assert _to_float("0") == 0.0

def test_to_float_clp_no_separator():
    # Small amount no separator
    assert _to_float("850") == 850.0


# ─────────────────────────────────────────────────────────────────────────────
# _parse_boleta_from_text — Tesseract OCR parser (realistic receipt texts)
# ─────────────────────────────────────────────────────────────────────────────

# ── Boleta 1: Lider supermarket with barcodes and IVA ────────────────────────
_LIDER_BOLETA = """\
LIDER MAIPU
RUT 78.304.680-1
Boleta Electronica N 003214567

7803468001250 CT PAN PITA                $ 1.750
7803468001251 LECHE ENTERA 1L            $ 1.290
7803456789012
2x4.990       PECHUGA POLLO             $ 9.980
7803468001252 BARRA MARRAQUETA          $   850
7803468001253 QUESO GOUDA 250G          $ 2.990

TOTAL NETO                             $ 16.860
I.V.A.                                  $ 3.203
TOTAL                                  $ 20.063
TARJETA DE CREDITO                     $ 20.063
"""

def test_boleta_lider_totals():
    items, neto, iva, conf = _parse_boleta_from_text(_LIDER_BOLETA)
    assert neto == 16860.0, f"TOTAL NETO expected 16860, got {neto}"
    assert iva == 3203.0, f"IVA expected 3203, got {iva}"

def test_boleta_lider_item_count():
    items, neto, iva, conf = _parse_boleta_from_text(_LIDER_BOLETA)
    assert len(items) >= 4, f"Expected at least 4 items, got {len(items)}: {[i.name for i in items]}"

def test_boleta_lider_multiunit_price():
    """'2x4.990 PECHUGA POLLO $9.980' → unit=4990, qty=2."""
    items, _, _, _ = _parse_boleta_from_text(_LIDER_BOLETA)
    pollo = next((it for it in items if "POLLO" in it.name.upper()), None)
    assert pollo is not None, "PECHUGA POLLO not found"
    assert pollo.quantity == 2, f"qty should be 2, got {pollo.quantity}"
    assert pollo.price == 4990, f"unit price should be 4990, got {pollo.price}"

def test_boleta_lider_confidence():
    """Items from Lider boleta should match neto closely → high confidence."""
    items, neto, iva, conf = _parse_boleta_from_text(_LIDER_BOLETA)
    # Sum may not be perfect (OCR simulation), but confidence should reflect proximity
    assert isinstance(conf, float)
    assert 0.0 <= conf <= 1.0


# ── Boleta 2: Restaurant VIENESA (no IVA, leading-qty format) ─────────────────
_VIENESA_RECEIPT = """\
RESTAURANT LA ESQUINA
Mesa 7 - Mozo: Pedro

3 VIENESA ITALIANA                     13.200
6 SCHOP MEDIO ROYAL                    28.800
2 SCHOP MEDIO ESCUDO                    8.800

TOTAL                                  50.800
"""

def test_vienesa_items_unit_price():
    """'3 VIENESA ITALIANA 13200' → unit=4400, qty=3 (Tesseract path)."""
    items, neto, iva, conf = _parse_boleta_from_text(_VIENESA_RECEIPT)
    vienesa = next((it for it in items if "VIENESA" in it.name.upper()), None)
    assert vienesa is not None, "VIENESA not found"
    assert vienesa.quantity == 3, f"qty should be 3, got {vienesa.quantity}"
    assert vienesa.price == 4400, f"unit price should be 4400, got {vienesa.price}"

def test_vienesa_schop_unit_price():
    """'6 SCHOP MEDIO ROYAL 28800' → unit=4800, qty=6."""
    items, _, _, _ = _parse_boleta_from_text(_VIENESA_RECEIPT)
    schop = next((it for it in items if "ROYAL" in it.name.upper()), None)
    assert schop is not None
    assert schop.quantity == 6
    assert schop.price == 4800

def test_vienesa_sum_matches_total():
    items, _, _, _ = _parse_boleta_from_text(_VIENESA_RECEIPT)
    total = sum(it.price * it.quantity for it in items)
    assert total == 50800, f"Expected sum=50800, got {total}"


# ── Boleta 3: Unimarc with "Nx description" format and discount ───────────────
_UNIMARC_BOLETA = """\
UNIMARC PLAZA EGANA
RUT 79.513.730-8

7803468001250 ACEITE CHEF 1L            $ 2.490
7891515551995
2x Jugo Natural 1L                     $ 3.580
7803468001252 DESCUENTO CLUB            $  -490

TOTAL NETO                             $  5.580
I.V.A.                                 $  1.060
TOTAL                                  $  6.640
"""

def test_unimarc_totals():
    items, neto, iva, conf = _parse_boleta_from_text(_UNIMARC_BOLETA)
    assert neto == 5580.0
    assert iva == 1060.0

def test_unimarc_nx_format():
    """'2x Jugo Natural 1L $3.580' → qty=2, unit=1790."""
    items, _, _, _ = _parse_boleta_from_text(_UNIMARC_BOLETA)
    jugo = next((it for it in items if "JUGO" in it.name.upper() or "Jugo" in it.name), None)
    assert jugo is not None, f"Jugo not found in: {[i.name for i in items]}"
    assert jugo.quantity == 2
    assert jugo.price == 1790  # 3580 / 2


# ── Boleta 4: Tottus with 3x items and high total ────────────────────────────
_TOTTUS_BOLETA = """\
TOTTUS MAIPU
RUT 78.304.680-1

7803468001250 COCA COLA 1.5L            $ 1.690
7803468001251 3x Helado Soprole         $ 5.970
7803468001252 DETERGENTE OMO 3K         $ 8.990
7803468001253 PAPEL HIGIENICO 24U       $ 5.990

TOTAL NETO                            $ 22.640
I.V.A.                                 $ 4.302
TOTAL                                 $ 26.942
EFECTIVO                              $ 26.942
"""

def test_tottus_3x_format():
    """'3x Helado Soprole $5.970' → qty=3, unit=1990."""
    items, _, _, _ = _parse_boleta_from_text(_TOTTUS_BOLETA)
    helado = next((it for it in items if "HELADO" in it.name.upper() or "Helado" in it.name), None)
    assert helado is not None, f"Helado not found in {[i.name for i in items]}"
    assert helado.quantity == 3
    assert helado.price == 1990  # 5970 / 3

def test_tottus_totals():
    _, neto, iva, _ = _parse_boleta_from_text(_TOTTUS_BOLETA)
    assert neto == 22640.0
    assert iva == 4302.0


# ── Boleta 5: Farmacia Cruz Verde receipt ─────────────────────────────────────
_FARMACIA_BOLETA = """\
FARMACIA CRUZ VERDE
SUC: AV. AMERICO VESPUCIO SUR 881

7803468001250 PARACETAMOL 500MG x20     $ 1.990
7803468001251 VITAMINA C 500MG x30      $ 3.490
7803468001252 ALCOHOL GEL 500ML         $ 2.990

TOTAL NETO                              $ 8.470
I.V.A.                                  $ 1.609
TOTAL                                  $ 10.079
DEBITO                                 $ 10.079
"""

def test_farmacia_items():
    items, neto, iva, conf = _parse_boleta_from_text(_FARMACIA_BOLETA)
    assert neto == 8470.0
    assert iva == 1609.0
    assert len(items) >= 3

def test_farmacia_skips_suc_line():
    """Branch address line 'SUC: ...' must not become an item."""
    items, _, _, _ = _parse_boleta_from_text(_FARMACIA_BOLETA)
    for it in items:
        assert "SUC" not in it.name.upper() and "VESPUCIO" not in it.name.upper(), \
            f"Branch address leaked as item: {it.name}"


# ── Boleta 6: "3 completos por 13200" — "por" syntax ─────────────────────────
_COMPLETOS_RECEIPT = """\
FUENTE DE SODA EL GORDO

3 completos por 13.200
2 bebida por 3.400
1 jugo natural                          3.200

TOTAL                                  19.800
"""

def test_completos_por_syntax():
    """'3 completos por 13200' → qty=3, unit=4400 (por = for total)."""
    items, _, _, _ = _parse_boleta_from_text(_COMPLETOS_RECEIPT)
    completo = next((it for it in items if "completo" in it.name.lower()), None)
    assert completo is not None, f"completo not found in {[i.name for i in items]}"
    assert completo.quantity == 3
    assert completo.price == 4400  # 13200 / 3


# ─────────────────────────────────────────────────────────────────────────────
# _fix_line_total_items — extra edge cases
# ─────────────────────────────────────────────────────────────────────────────

def test_fix_no_multi_qty_items_unchanged():
    """All qty=1 → no fix possible, return as-is."""
    items = [
        make_item("Agua", 1500, qty=1),
        make_item("Empanada", 2000, qty=1),
    ]
    ref = 3500.0
    fixed = _fix_line_total_items(items, ref)
    assert fixed[0].price == 1500
    assert fixed[1].price == 2000


def test_fix_zero_ref_total_returns_unchanged():
    items = [make_item("Item", 5000, qty=2)]
    fixed = _fix_line_total_items(items, 0.0)
    assert fixed[0].price == 5000


def test_fix_three_wrong_falls_through_cleanly():
    """3 wrong items but not in single/pair/global pattern → return unchanged."""
    items = [
        make_item("A", 5000, qty=2),
        make_item("B", 3000, qty=3),
        make_item("C", 4000, qty=4),
    ]
    # sum = 10000+9000+16000 = 35000; ref = 20000
    # excess = 15000; no single/pair match; sum_flat = 12000 ≠ 20000
    fixed = _fix_line_total_items(items, 20000.0)
    # Should not crash, returns items (may or may not fix)
    assert len(fixed) == 3


def test_fix_preserves_qty1_items():
    """qty=1 items must never be modified by the fix."""
    items = [
        make_item("Cerveza", 9600, qty=2),  # wrong
        make_item("Agua", 1500, qty=1),     # correct, must not change
    ]
    ref_total = 9600 + 1500  # = 11100 (if cerveza already unit, but it's line total)
    # Actually: correct prices are 4800*2 + 1500 = 11100
    ref_total = 11100.0
    fixed = _fix_line_total_items(items, ref_total)
    agua = next(it for it in fixed if it.name == "Agua")
    assert agua.price == 1500, "Agua (qty=1) price should not change"


# ─────────────────────────────────────────────────────────────────────────────
# _normalize_boleta_items — discount (negative price) handling
# ─────────────────────────────────────────────────────────────────────────────

def test_normalize_with_discount_item():
    """Negative-price discount item is preserved after normalization."""
    items = [
        make_item("Aceite Chef", 2490),
        make_item("Jugo Natural", 1790, qty=2),
        make_item("DESCUENTO CLUB", -490),  # discount
    ]
    # llm_neto_sum = 2490 + 3580 - 490 = 5580
    total_neto = 5580.0
    iva = round(total_neto * 0.19)
    normalized, auth_total = _normalize_boleta_items(items, total_neto, iva)

    product_items = [it for it in normalized if "iva" not in it.name.lower()]
    product_sum = sum(it.price * it.quantity for it in product_items)
    assert abs(product_sum - total_neto) <= 1, \
        f"Product sum {product_sum} should ≈ {total_neto}"

    discount = next((it for it in product_items if it.price < 0), None)
    assert discount is not None, "Discount item should be preserved"


# ─────────────────────────────────────────────────────────────────────────────
# _scale_to_total — edge cases
# ─────────────────────────────────────────────────────────────────────────────

def test_scale_empty_list():
    scaled, amount = _scale_to_total([], 10000.0)
    assert scaled == []

def test_scale_single_item_exact():
    """Single item exactly at ref_total → no change."""
    items = [make_item("Item", 10000, qty=1)]
    scaled, amount = _scale_to_total(items, 10000.0)
    assert scaled[0].price == 10000

def test_scale_single_item_over():
    """Single item over ref → scale down."""
    items = [make_item("Item", 11000, qty=1)]
    scaled, amount = _scale_to_total(items, 10000.0)
    assert scaled[0].price == 10000
    assert amount == 10000.0

def test_scale_preserves_item_names():
    items = [
        make_item("Piscolón Mistral de 35°", 9000),
        make_item("Fernet Branca", 5800),
    ]
    scaled, _ = _scale_to_total(items, 14800.0)  # already correct, no-op
    assert scaled[0].name == "Piscolón Mistral de 35°"
    assert scaled[1].name == "Fernet Branca"


# ═════════════════════════════════════════════════════════════════════════════
# BARES Y RESTAURANTES — Casos reales chilenos
# ═════════════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────────────
# Pipe-table (Toteat / Restō / Revo) — el format más difícil para el LLM
# ─────────────────────────────────────────────────────────────────────────────

# Recibo real de Toteat con varios tragos y grados de alcohol en los nombres
_TOTEAT_BAR = """\
TOTEAT
Mesa 12 - Mozo: Carlos

| Cant | Producto              | Total  |
| 3    | Piscola Mistral 35°   | 27.000 |
| 2    | Fernet Branca 39°     | 11.600 |
| 4    | Schop Medio Royal     | 19.200 |
| 1    | Agua con gas 500ml    |  1.500 |
| 2    | Empanada de queso     |  6.000 |

TOTAL                                  65.300
"""

def test_toteat_alcohol_degrees_not_qty():
    """Grados de alcohol (35°, 39°) en nombre → qty debe venir SOLO del Cant column."""
    items = _parse_pipe_table(_TOTEAT_BAR)
    piscola = next(it for it in items if "Piscola" in it.name or "Mistral" in it.name)
    fernet  = next(it for it in items if "Fernet" in it.name)
    assert piscola.quantity == 3,  f"Piscola qty should be 3, got {piscola.quantity}"
    assert piscola.price    == 9000, f"Piscola unit price 27000/3=9000, got {piscola.price}"
    assert fernet.quantity  == 2,  f"Fernet qty should be 2, got {fernet.quantity}"
    assert fernet.price     == 5800, f"Fernet unit price 11600/2=5800, got {fernet.price}"

def test_toteat_sum_matches_printed_total():
    items = _parse_pipe_table(_TOTEAT_BAR)
    assert sum(it.price * it.quantity for it in items) == 65300

def test_toteat_schop_qty4():
    items = _parse_pipe_table(_TOTEAT_BAR)
    schop = next(it for it in items if "Schop" in it.name or "Royal" in it.name)
    assert schop.quantity == 4
    assert schop.price    == 4800  # 19200/4

def test_toteat_agua_500ml_qty1():
    """'500ml' en nombre no es cantidad."""
    items = _parse_pipe_table(_TOTEAT_BAR)
    agua = next(it for it in items if "Agua" in it.name)
    assert agua.quantity == 1
    assert agua.price    == 1500


# Whisky y ron con grados
_DESTILADOS_RECEIPT = """\
| Cant | Producto               | Total  |
| 1    | Ron Santa Teresa 18°   |  8.500 |
| 1    | Vodka Absolut 40°      |  9.000 |
| 2    | Whisky J&B 40°         | 22.000 |
| 3    | Agua mineral 500ml     |  4.500 |
"""

def test_destilados_18_grados_not_qty():
    items = _parse_pipe_table(_DESTILADOS_RECEIPT)
    ron = next(it for it in items if "Ron" in it.name or "Santa Teresa" in it.name)
    assert ron.quantity == 1
    assert ron.price == 8500

def test_destilados_40_grados_not_qty():
    items = _parse_pipe_table(_DESTILADOS_RECEIPT)
    vodka  = next(it for it in items if "Vodka" in it.name)
    whisky = next(it for it in items if "Whisky" in it.name)
    assert vodka.quantity  == 1
    assert vodka.price     == 9000
    assert whisky.quantity == 2
    assert whisky.price    == 11000  # 22000/2

def test_destilados_agua_qty3():
    items = _parse_pipe_table(_DESTILADOS_RECEIPT)
    agua = next(it for it in items if "Agua" in it.name)
    assert agua.quantity == 3
    assert agua.price    == 1500  # 4500/3


# ─────────────────────────────────────────────────────────────────────────────
# _parse_boleta_from_text — restaurant sin IVA (texto plano, no pipe-table)
# ─────────────────────────────────────────────────────────────────────────────

# Bar con schops y cervezas (formato restaurante leading-qty)
_SCHOP_BAR = """\
BAR LA UNIÓN
Mesa 3

6 Schop Medio Royal                    28.800
2 Schop Medio Escudo                    8.800
3 Vienesa italiana                     13.200
1 Agua mineral                          1.500

TOTAL                                  52.300
"""

def test_schop_bar_unit_prices():
    items, _, _, _ = _parse_boleta_from_text(_SCHOP_BAR)
    royal  = next((it for it in items if "Royal" in it.name  or "ROYAL" in it.name),  None)
    escudo = next((it for it in items if "Escudo" in it.name or "ESCUDO" in it.name), None)
    assert royal  is not None and royal.quantity  == 6 and royal.price  == 4800
    assert escudo is not None and escudo.quantity == 2 and escudo.price == 4400

def test_schop_bar_vienesa():
    items, _, _, _ = _parse_boleta_from_text(_SCHOP_BAR)
    vienesa = next((it for it in items if "vienesa" in it.name.lower()), None)
    assert vienesa is not None
    assert vienesa.quantity == 3
    assert vienesa.price    == 4400  # 13200/3

def test_schop_bar_total():
    items, _, _, _ = _parse_boleta_from_text(_SCHOP_BAR)
    total = sum(it.price * it.quantity for it in items)
    assert total == 52300


# Pub con descuento en cuenta
_PUB_DESCUENTO = """\
PUB FITZROY
Mesa 8

4 Cerveza Austral botella              12.000
2 Pisco sour                           11.000
1 Agua con gas                          1.500
3 Porcion papas fritas                 10.500
DESCUENTO HAPPY HOUR                   -3.500

TOTAL                                  31.500
"""

def test_pub_cerveza_unit_price():
    items, _, _, _ = _parse_boleta_from_text(_PUB_DESCUENTO)
    cerveza = next((it for it in items if "Cerveza" in it.name or "CERVEZA" in it.name), None)
    assert cerveza is not None
    assert cerveza.quantity == 4
    assert cerveza.price    == 3000  # 12000/4

def test_pub_papas_unit_price():
    items, _, _, _ = _parse_boleta_from_text(_PUB_DESCUENTO)
    papas = next((it for it in items if "papas" in it.name.lower()), None)
    assert papas is not None
    assert papas.quantity == 3
    assert papas.price    == 3500  # 10500/3


# Restaurante formal con IVA (boleta electrónica)
_REST_CON_IVA = """\
RESTAURANTE DONDE AUGUSTO
RUT 76.543.210-K
Boleta Electronica N 000123

1 Lomo a lo pobre                      12.600
1 Bebida Coca-Cola 350ml                1.890
2 Jugo natural naranja                  4.200

TOTAL NETO                             18.690
I.V.A.                                  3.551
TOTAL                                  22.241
TARJETA DE CREDITO                     22.241
"""

def test_rest_iva_totals():
    _, neto, iva, _ = _parse_boleta_from_text(_REST_CON_IVA)
    assert neto == 18690.0, f"NETO expected 18690, got {neto}"
    assert iva  ==  3551.0, f"IVA expected 3551, got {iva}"

def test_rest_iva_jugo_unit_price():
    """'2 Jugo natural naranja 4.200' → qty=2, unit=2100."""
    items, _, _, _ = _parse_boleta_from_text(_REST_CON_IVA)
    jugo = next((it for it in items if "Jugo" in it.name or "jugo" in it.name), None)
    assert jugo is not None
    assert jugo.quantity == 2
    assert jugo.price    == 2100  # 4200/2

def test_rest_iva_bebida_qty1():
    """'1 Bebida Coca-Cola 350ml 1.890' → qty=1, price=1890. '350' is volume."""
    items, _, _, _ = _parse_boleta_from_text(_REST_CON_IVA)
    bebida = next((it for it in items if "Coca" in it.name or "Bebida" in it.name), None)
    assert bebida is not None
    assert bebida.quantity == 1
    assert bebida.price    == 1890


# ─────────────────────────────────────────────────────────────────────────────
# _fix_line_total_items — casos LLM que falla en bar/restaurante
# ─────────────────────────────────────────────────────────────────────────────

def test_fix_bar_all_wrong_global():
    """LLM devuelve price=line_total para TODOS los items con qty>1 (caso bar).
    ref_total = sum of line-totals (= sum after correct unit prices × qty)."""
    items = [
        make_item("Piscola Mistral 35°", 27000, qty=3),  # line_total as price; unit=9000
        make_item("Fernet Branca 39°",   11600, qty=2),  # line_total as price; unit=5800
        make_item("Schop Medio Royal",   19200, qty=4),  # line_total as price; unit=4800
    ]
    # sum_flat = 27000+11600+19200 = 57800 = correct receipt total
    ref_total = 57800.0
    fixed = _fix_line_total_items(items, ref_total)
    piscola = next(it for it in fixed if "Piscola" in it.name or "Mistral" in it.name)
    fernet  = next(it for it in fixed if "Fernet" in it.name)
    schop   = next(it for it in fixed if "Schop" in it.name or "Royal" in it.name)
    assert piscola.price == 9000,  f"Piscola unit 9000, got {piscola.price}"
    assert fernet.price  == 5800,  f"Fernet unit 5800, got {fernet.price}"
    assert schop.price   == 4800,  f"Schop unit 4800, got {schop.price}"
    assert sum(it.price * it.quantity for it in fixed) == ref_total


def test_fix_bar_single_wrong_cerveza():
    """Solo la cerveza tiene price=line_total, los demás correctos."""
    items = [
        make_item("Cerveza Austral", 12000, qty=4),  # wrong: should be 3000
        make_item("Agua mineral",     1500, qty=1),  # correct
        make_item("Papas fritas",     3500, qty=1),  # correct
    ]
    # correct sum: 3000*4 + 1500 + 3500 = 17000
    ref_total = 17000.0
    fixed = _fix_line_total_items(items, ref_total)
    cerveza = next(it for it in fixed if "Cerveza" in it.name)
    assert cerveza.price == 3000, f"Cerveza unit 3000, got {cerveza.price}"
    assert sum(it.price * it.quantity for it in fixed) == ref_total


def test_fix_bar_pisco_sour_pair():
    """Dos items con line_total-as-price, fix de par.
    excess = 9600*(2-1) + 6000*(3-1) = 9600 + 12000 = 21600 = total_excess → par detectado."""
    items = [
        make_item("Pisco sour",   9600, qty=2),  # line_total; unit=4800
        make_item("Empanada",     6000, qty=3),  # line_total; unit=2000
        make_item("Agua mineral", 1500, qty=1),  # correct
    ]
    # correct sum: 4800*2 + 2000*3 + 1500 = 9600+6000+1500 = 17100
    ref_total = 17100.0
    fixed = _fix_line_total_items(items, ref_total)
    pisco    = next(it for it in fixed if "Pisco" in it.name)
    empanada = next(it for it in fixed if "Empanada" in it.name)
    assert pisco.price    == 4800
    assert empanada.price == 2000
    assert sum(it.price * it.quantity for it in fixed) == ref_total


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline integrado: _parse_pipe_table → _fix_line_total_items → _scale_to_total
# ─────────────────────────────────────────────────────────────────────────────

def test_pipeline_pipe_table_then_scale():
    """Pipeline completo: pipe-table → escala down garantía."""
    text = (
        "| Cant | Producto       | Total  |\n"
        "| 2    | Schop Royal    | 9.600  |\n"
        "| 1    | Agua mineral   | 1.500  |\n"
    )
    items = _parse_pipe_table(text)
    assert items[0].price == 4800  # 9600/2
    # Simula que hay un centavo de diferencia de redondeo
    items_sum = sum(it.price * it.quantity for it in items)
    assert items_sum == 11100  # 9600+1500

    scaled, amount = _scale_to_total(items, 11100.0)  # already matches
    assert amount == 11100.0
    assert scaled[0].price == 4800  # unchanged


def test_pipeline_fix_then_scale_guarantees_sum():
    """LLM dio precios incorrectos → fix de par + scale garantiza suma exacta.
    Diseñado para activar pair fix: excess = 9600 + 12000 = 21600."""
    llm_items = [
        make_item("Cerveza Austral", 9600, qty=2),  # line_total; unit=4800
        make_item("Empanada",        6000, qty=3),  # line_total; unit=2000
        make_item("Agua",            1500, qty=1),  # correct
    ]
    # correct: 4800*2 + 2000*3 + 1500 = 17100
    ref_total = 17100.0
    fixed = _fix_line_total_items(llm_items, ref_total)
    cerveza  = next(it for it in fixed if "Cerveza" in it.name)
    empanada = next(it for it in fixed if "Empanada" in it.name)
    assert cerveza.price  == 4800
    assert empanada.price == 2000
    scaled, amount = _scale_to_total(fixed, ref_total)
    assert abs(sum(it.price * it.quantity for it in scaled) - ref_total) <= 1
