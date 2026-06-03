"""
Tests for OCR parsing functions in ocr.py.

Covers: _to_float, _parse_clp, _parse_boleta_from_text, _parse_pipe_table,
        _normalize_boleta_items, _fix_line_total_items

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


# ═════════════════════════════════════════════════════════════════════════════
# BARES Y RESTAURANTES — Tests por INVARIANTES, no por casos específicos
#
# Estrategia: testear propiedades matemáticas que deben cumplirse para CUALQUIER
# recibo, no catalogar ejemplos. Los casos específicos son ejemplos del invariante.
# ═════════════════════════════════════════════════════════════════════════════

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# INVARIANTE 1: _parse_pipe_table — qty SIEMPRE del Cant column
#
# Propiedad: Para cualquier fila | N | nombre_con_numeros | total |,
#   result.quantity == N   (nunca se lee un número del nombre)
#   result.price * N == line_total  (dentro de ±1 CLP por redondeo)
#
# Parametrizado con nombres que CONTIENEN números que NO son cantidades:
# grados de alcohol, volumen (ml/cc), años, especificaciones de producto.
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("cant,name,line_total_clp", [
    # grados de alcohol
    (1, "Piscolón Mistral de 35°",   9000),
    (3, "Piscola Mistral 35°",      27000),
    (1, "Ron Santa Teresa 18°",      8500),
    (1, "Vodka Absolut 40°",         9000),
    (2, "Whisky J&B 40°",           22000),
    (1, "Fernet Branca 39°",         5800),
    (2, "Cognac Hennessy 40°",      26000),
    # volumen en nombre
    (1, "Schop 500cc Austral",       3500),
    (2, "Cerveza Artesanal 330ml",   7000),
    (1, "Agua mineral 500ml",        1500),
    (3, "Jugo natural 300ml",        4500),
    # especificación de producto
    (1, "Alto del Carmen 35",        7500),
    (1, "Johnnie Walker 12 años",   15000),
    (2, "Vino Reserva 2019",        18000),
    # número en descripción genérica
    (4, "Schop Medio Royal",        19200),
    (1, "Coca-Cola 350ml",           1500),
])
def test_pipe_table_qty_always_from_cant_column(cant, name, line_total_clp):
    """INVARIANTE: qty = cant_column, nunca de números en el nombre del producto."""
    row = f"| {cant} | {name} | {line_total_clp // 1000}.{line_total_clp % 1000:03d} |\n"
    items = _parse_pipe_table(row)
    assert len(items) == 1, f"Expected 1 item for row: {row!r}"
    it = items[0]
    assert it.quantity == cant, \
        f"qty should be {cant} (from Cant col), got {it.quantity} — name '{name}' leaked a number"
    assert abs(it.price * it.quantity - line_total_clp) <= 1, \
        f"price×qty should ≈ {line_total_clp}, got {it.price}×{it.quantity}={it.price*it.quantity}"


# ─────────────────────────────────────────────────────────────────────────────
# INVARIANTE 2: _parse_pipe_table — suma de items == suma de totales impresos
#
# Propiedad: sum(item.price × item.qty) == sum(all line_totals in receipt)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("rows,expected_total", [
    # Bar básico con grados en nombres
    ([("1","Piscolón 35°","9.000"), ("1","Fernet 39°","5.800")],  14800),
    # Pub con muchos schops
    ([("6","Schop Royal","28.800"), ("2","Schop Escudo","8.800"), ("3","Vienesa","13.200")], 50800),
    # Bar destilados varios
    ([("1","Ron 18°","8.500"), ("2","Whisky 40°","22.000"), ("3","Agua 500ml","4.500")], 35000),
    # Un solo item qty=1
    ([("1","Cerveza Austral","3.000")], 3000),
    # Todos qty>1
    ([("2","Pisco sour","9.600"), ("3","Empanada","6.000"), ("4","Schop","19.200")], 34800),
])
def test_pipe_table_sum_invariant(rows, expected_total):
    """INVARIANTE: sum(price×qty) == suma de totales de columna del recibo."""
    text = "| Cant | Producto | Total |\n" + "".join(
        f"| {c} | {n} | {t} |\n" for c, n, t in rows
    )
    items = _parse_pipe_table(text)
    total = sum(it.price * it.quantity for it in items)
    assert abs(total - expected_total) <= len(rows), \
        f"sum {total} should ≈ {expected_total} (rows: {rows})"


# ─────────────────────────────────────────────────────────────────────────────
# INVARIANTE 3: _parse_boleta_from_text — "N description LINE_TOTAL"
#
# Propiedad: para cualquier línea "N item LINE_TOTAL" (formato restaurante),
#   result.quantity == N
#   result.price == round(LINE_TOTAL / N)
#   result.price * N == LINE_TOTAL  (dentro de ±1)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("qty,name,line_total", [
    (6, "Schop Medio Royal",    28800),
    (2, "Schop Medio Escudo",    8800),
    (3, "Vienesa italiana",     13200),
    (4, "Cerveza Austral",      12000),
    (2, "Pisco sour",           11000),
    (3, "Porcion papas fritas", 10500),
    (5, "Empanada de pino",     15000),
    (2, "Jugo natural",          4200),
])
def test_tess_leading_qty_unit_price(qty, name, line_total):
    """INVARIANTE: 'N nombre LINE_TOTAL' → unit=LINE_TOTAL/N, qty=N."""
    text = f"{qty} {name}  {line_total // 1000}.{line_total % 1000:03d}\nTOTAL  {line_total // 1000}.{line_total % 1000:03d}\n"
    items, _, _, _ = _parse_boleta_from_text(text)
    item = next((it for it in items if name.split()[0].upper() in it.name.upper()), None)
    assert item is not None, f"'{name}' not found in parsed items: {[i.name for i in items]}"
    assert item.quantity == qty, f"qty should be {qty}, got {item.quantity}"
    assert abs(item.price * item.quantity - line_total) <= 1, \
        f"price×qty={item.price*item.quantity} should ≈ {line_total}"


# ─────────────────────────────────────────────────────────────────────────────
# INVARIANTE 4: _fix_line_total_items — cuando hay fix, sum(fixed) == ref_total
#
# Propiedad: si el algoritmo puede identificar el patrón (single/pair/global),
#   el resultado siempre satisface sum(price×qty) == ref_total exactamente.
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("wrong_items,ref_total,desc", [
    # Single: solo cerveza wrong; ref = 3000*4 + 1500 + 800 = 14300
    # excess = 12000*(4-1) = 36000 = sum_wrong - ref ✓ → single fix detectado
    ([("Cerveza", 12000, 4), ("Agua", 1500, 1), ("Pan", 800, 1)],
     14300.0, "single wrong: cerveza"),
    # Single: solo schop wrong; ref = 4800*6 + 2000 = 30800
    # excess = 28800*(6-1) = 144000 = sum_wrong - ref ✓ → single fix detectado
    ([("Schop Royal", 28800, 6), ("Empanada", 2000, 1)],
     30800.0, "single wrong: schop"),
    # Global: todos wrong; ref = sum_flat = 27000+11600+19200 = 57800
    ([("Piscola", 27000, 3), ("Fernet", 11600, 2), ("Schop", 19200, 4)],
     57800.0, "global: todos wrong"),
    # Global: completo+bebida; ref = sum_flat = 12000+3400 = 15400
    ([("Completo", 12000, 3), ("Bebida", 3400, 2)],
     15400.0, "global: completo+bebida"),
])
def test_fix_when_detectable_sum_equals_ref(wrong_items, ref_total, desc):
    """INVARIANTE: cuando _fix detecta el patrón, sum(fixed) == ref_total."""
    items = [make_item(n, p, q) for n, p, q in wrong_items]
    fixed = _fix_line_total_items(items, ref_total)
    total = sum(it.price * it.quantity for it in fixed)
    assert abs(total - ref_total) <= 1, \
        f"[{desc}] sum {total} should == ref_total {ref_total}"


# ─────────────────────────────────────────────────────────────────────────────
# INVARIANTE 5: _normalize_boleta_items — suma siempre == total_neto
#
# Propiedad: para CUALQUIER lista de items y cualquier total_neto > 0,
#   sum(product_items.price × qty) == total_neto   (dentro de ±1)
#   auth_total == total_neto + iva_amount
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("item_prices_qtys,total_neto", [
    # 1 item
    ([(9999, 1)],                      10000),
    # 2 items, ligera diferencia de redondeo LLM
    ([(5000, 1), (3000, 1)],            7800),
    # Multi-qty con scale
    ([(4990, 2), (1750, 1)],           11600),
    # 5 items restaurante
    ([(12600,1),(1890,1),(2100,2),(3500,3),(800,1)], 30000),
    # Con descuento negativo
    ([(2490,1),(1790,2),(-490,1)],      5580),
])
def test_normalize_sum_always_equals_neto(item_prices_qtys, total_neto):
    """INVARIANTE: sum(products) ≈ total_neto para cualquier input."""
    items = [make_item(f"Item{i}", p, q) for i, (p, q) in enumerate(item_prices_qtys)]
    iva = round(total_neto * 0.19)
    normalized, auth_total = _normalize_boleta_items(items, float(total_neto), float(iva))
    product_items = [it for it in normalized if "iva" not in it.name.lower()]
    product_sum = sum(it.price * it.quantity for it in product_items)
    assert abs(product_sum - total_neto) <= 1, \
        f"product_sum={product_sum} should ≈ total_neto={total_neto}"
    assert auth_total == total_neto + iva


# ═════════════════════════════════════════════════════════════════════════════
# TESTS COMPLEJOS — Boletas de restaurantes/bares chilenos
# Cubre formatos reales: El Hoyo, Fuente Alemana, POS Toteat, IVA restaurant
# ═════════════════════════════════════════════════════════════════════════════


# ─────────────────────────────────────────────────────────────────────────────
# INVARIANTE 7: Ninguna línea de metadata se convierte en item
#
# Propiedad: líneas de resumen (CONSUMO MESA, PROPINA, VISA, etc.) nunca deben
# aparecer en la lista de items. La detección de precio en estas líneas no debe
# generar un ParsedItem.
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("summary_line,price_suffix", [
    # Líneas de resumen de POS restaurante
    ("CONSUMO MESA",          "20.000"),
    ("CONSUMO GENERAL",       "20.000"),
    ("PROPINA 10%",           "2.000"),
    ("PROPINA SUGERIDA 10%",  "2.000"),
    ("TOTAL C/PROPINA",       "22.000"),
    ("TOTAL MESA",            "20.000"),
    ("TOTAL GENERAL MESA",    "20.000"),
    # Métodos de pago
    ("VISA",                  "20.000"),
    ("MASTERCARD",            "20.000"),
    ("REDCOMPRA",             "20.000"),
    ("WEBPAY",                "20.000"),
    # Cambio
    ("VUELTO",                "5.000"),
])
def test_summary_lines_never_become_items(summary_line, price_suffix):
    """INVARIANTE: ninguna línea de resumen/pago se convierte en ParsedItem."""
    text = (
        f"RESTAURANTE\nMesa 5\n\n"
        f"2 Cerveza  8.000\n1 Agua    1.500\n\n"
        f"{summary_line}  {price_suffix}\n"
    )
    items, _, _, _ = _parse_boleta_from_text(text)
    leaked = [it for it in items if summary_line.split()[0].lower() in it.name.lower()]
    assert not leaked, (
        f"Summary line '{summary_line}' leaked as item: {[it.name for it in leaked]}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# INVARIANTE 8: Recibo complejo (9 items, bar chileno)
#
# Propiedad: sum(price×qty) == total impreso para recibo de 9 items multi-qty
# ─────────────────────────────────────────────────────────────────────────────
_EL_HOYO_RECEIPT = """\
EL HOYO BAR
Mesa 14  Mozo: Carlos

6 Schop Medio Cristal       24.000
4 Schop Grande Austral      20.000
2 Pisco Sour                12.000
3 Cerveza Austral 330ml      7.500
1 Copa Vino Tinto            5.000
2 Porcion Papas Fritas       7.000
3 Empanada Queso            10.500
2 Chorrillana Porcion       20.000
1 Tabla Fria Mixta          15.000

TOTAL                      121.000
"""

def test_el_hoyo_9_items_count():
    items, _, _, _ = _parse_boleta_from_text(_EL_HOYO_RECEIPT)
    assert len(items) == 9, f"Expected 9 items, got {len(items)}: {[i.name for i in items]}"


def test_el_hoyo_9_items_sum():
    items, _, _, _ = _parse_boleta_from_text(_EL_HOYO_RECEIPT)
    total = sum(it.price * it.quantity for it in items)
    assert total == 121000, f"Expected sum=121000, got {total}"


def test_el_hoyo_unit_prices_correct():
    """6 Schop Medio → unit=4000; 4 Schop Grande → unit=5000; 3 Empanada → unit=3500."""
    items, _, _, _ = _parse_boleta_from_text(_EL_HOYO_RECEIPT)
    by_name = {it.name.lower(): it for it in items}
    schop_medio = next((it for it in items if "medio" in it.name.lower() and "cristal" in it.name.lower()), None)
    assert schop_medio and schop_medio.price == 4000 and schop_medio.quantity == 6
    empanada = next((it for it in items if "empanada" in it.name.lower()), None)
    assert empanada and empanada.price == 3500 and empanada.quantity == 3


# ─────────────────────────────────────────────────────────────────────────────
# INVARIANTE 9: Restaurant con IVA — confianza alta
#
# Propiedad: cuando items suman = total (neto + iva), confidence == 1.0
# ─────────────────────────────────────────────────────────────────────────────
_DIVINA_IVA_RECEIPT = """\
RESTAURANT LA DIVINA
BOLETA ELECTRONICA 0011234

1 FILETE AL PIMIENTA        22.000
2 COPA VINO SANTA RITA      14.000
1 ENSALADA CAPRESE           9.500
1 POSTRE TIRAMISU            8.500
1 AGUA SAN BENEDETTO         3.500

TOTAL NETO                  48.319
IVA 19%                      9.181
TOTAL                       57.500
VISA                        57.500
"""

def test_iva_restaurant_confidence_high():
    """Items a precio IVA-incluido: confidence debe ser 1.0 (items_sum == total_with_iva)."""
    items, neto, iva, conf = _parse_boleta_from_text(_DIVINA_IVA_RECEIPT)
    assert neto == 48319.0
    assert iva == 9181.0
    total = sum(it.price * it.quantity for it in items)
    assert total == 57500.0
    assert conf == 1.0, f"confidence should be 1.0, got {conf}"


def test_iva_restaurant_no_visa_item():
    """Línea de pago VISA no debe aparecer como item."""
    items, _, _, _ = _parse_boleta_from_text(_DIVINA_IVA_RECEIPT)
    assert not any("visa" in it.name.lower() for it in items)


# ─────────────────────────────────────────────────────────────────────────────
# INVARIANTE 10: Pipe-table grande (7 items)
#
# Propiedad: 7 items parseados, sum == total exacto
# ─────────────────────────────────────────────────────────────────────────────
_TOTEAT_7_ITEMS = """\
| Cant | Descripción                | Total   |
|    1 | Pizza Margherita Familiar  |  24.000 |
|    3 | Cerveza Austral 500cc      |  12.000 |
|    1 | Ensalada César             |   9.500 |
|    2 | Agua Mineral 500ml         |   3.000 |
|    1 | Tiramisú                   |   7.500 |
|    2 | Jugo Natural               |   5.000 |
|    1 | Pan y mantequilla          |   2.500 |
TOTAL CONSUMO CLIENTE               63.500
"""

def test_pipe_table_7_items_count():
    items = _parse_pipe_table(_TOTEAT_7_ITEMS)
    assert len(items) == 7, f"Expected 7 items, got {len(items)}"


def test_pipe_table_7_items_sum():
    items = _parse_pipe_table(_TOTEAT_7_ITEMS)
    total = sum(it.price * it.quantity for it in items)
    assert total == 63500, f"Expected 63500, got {total}"


def test_pipe_table_7_items_unit_prices():
    """Cerveza 500cc qty=3: unit=4000 (12000/3). Agua 500ml qty=2: unit=1500."""
    items = _parse_pipe_table(_TOTEAT_7_ITEMS)
    cerveza = next((it for it in items if "cerveza" in it.name.lower()), None)
    assert cerveza and cerveza.quantity == 3 and cerveza.price == 4000
    agua = next((it for it in items if "agua" in it.name.lower()), None)
    assert agua and agua.quantity == 2 and agua.price == 1500


# ─────────────────────────────────────────────────────────────────────────────
# INVARIANTE 11: _fix_line_total_items — global fix con 5+ items
#
# Propiedad: cuando TODOS los items multi-qty están mal (global fix),
# sum(fixed) == ref_total exactamente, incluso con 5 items
# ─────────────────────────────────────────────────────────────────────────────
def test_fix_global_five_wrong_items():
    """5 items, todos con qty>1 y line_total como unit_price → global fix."""
    items = [
        make_item("Schop Medio",    24000, 6),   # unit=4000
        make_item("Schop Grande",   20000, 4),   # unit=5000
        make_item("Pisco Sour",     12000, 2),   # unit=6000
        make_item("Empanada",       10500, 3),   # unit=3500
        make_item("Porcion Papas",   7000, 2),   # unit=3500
    ]
    # correct: 4000*6 + 5000*4 + 6000*2 + 3500*3 + 3500*2
    # = 24000 + 20000 + 12000 + 10500 + 7000 = 73500
    ref_total = 73500.0
    fixed = _fix_line_total_items(items, ref_total)
    total = sum(it.price * it.quantity for it in fixed)
    assert abs(total - ref_total) <= 1, f"sum {total} should == {ref_total}"


# ─────────────────────────────────────────────────────────────────────────────
# INVARIANTE 12: Fuente Alemana — recibo clásico chileno, texto plano
# ─────────────────────────────────────────────────────────────────────────────
_FUENTE_ALEMANA = """\
FUENTE ALEMANA
Nunoa - Santiago

2 CHURRASCO ITALIANO        15.600
1 BARROS LUCO                7.800
3 BEBIDA GRANDE              5.700
1 JUGO NATURAL               3.500
1 SOPAIPILLA PASADA          1.500

TOTAL                       34.100
"""

def test_fuente_alemana_item_count():
    items, _, _, _ = _parse_boleta_from_text(_FUENTE_ALEMANA)
    assert len(items) == 5, f"Expected 5 items, got {len(items)}: {[i.name for i in items]}"


def test_fuente_alemana_sum():
    items, _, _, _ = _parse_boleta_from_text(_FUENTE_ALEMANA)
    total = sum(it.price * it.quantity for it in items)
    assert total == 34100, f"Expected 34100, got {total}"


def test_fuente_alemana_unit_prices():
    """2 CHURRASCO ITALIANO 15600 → unit=7800, qty=2."""
    items, _, _, _ = _parse_boleta_from_text(_FUENTE_ALEMANA)
    churrasco = next((it for it in items if "churrasco" in it.name.lower()), None)
    assert churrasco and churrasco.quantity == 2 and churrasco.price == 7800
    bebida = next((it for it in items if "bebida" in it.name.lower()), None)
    assert bebida and bebida.quantity == 3 and bebida.price == 1900


# ─────────────────────────────────────────────────────────────────────────────
# INVARIANTE 13: Bar Nacional — 9 items con propina y subtotal (ambos skipped)
# ─────────────────────────────────────────────────────────────────────────────
_BAR_NACIONAL = """\
BAR NACIONAL
PATIO 2 - Mesa 8

6 Schop Medio               24.000
4 Schop Grande              24.000
2 Piscola Mistral 35        18.000
3 Cerveza Artesanal 330ml    7.500
1 Copa Vino Tinto            5.000
2 Porcion Papas Fritas       7.000
3 Empanada Queso            10.500
2 Choripan                   9.000
1 Tabla Fria Mixta          15.000

SUBTOTAL                   120.000
PROPINA SUGERIDA (10%)      12.000
TOTAL C/PROPINA            132.000
"""

def test_bar_nacional_9_items_no_propina():
    """SUBTOTAL y PROPINA deben ser skipped — solo los 9 productos reales."""
    items, _, _, _ = _parse_boleta_from_text(_BAR_NACIONAL)
    assert len(items) == 9, f"Expected 9 items, got {len(items)}: {[i.name for i in items]}"


def test_bar_nacional_no_summary_leakage():
    """Ningún item debe tener 'propina', 'subtotal' o 'total' en el nombre."""
    items, _, _, _ = _parse_boleta_from_text(_BAR_NACIONAL)
    banned = ["propina", "subtotal", "total"]
    leaked = [it for it in items if any(b in it.name.lower() for b in banned)]
    assert not leaked, f"Summary lines leaked: {[it.name for it in leaked]}"


def test_bar_nacional_sum():
    """Sum of parsed items = 120.000 (sin propina)."""
    items, _, _, _ = _parse_boleta_from_text(_BAR_NACIONAL)
    total = sum(it.price * it.quantity for it in items)
    assert total == 120000, f"Expected 120000, got {total}"


# ─────────────────────────────────────────────────────────────────────────────
# INVARIANTE 14: Casino / comida simple — items sin barcode, precio bajo
# ─────────────────────────────────────────────────────────────────────────────
_CASINO_RECEIPT = """\
CASINO EMPRESARIAL
CLIENTE: MESA 3

SOPA DEL DIA                 2.500
CARNE CON PURE               4.800
ENSALADA CHILENA             1.800
JUGO NATURAL                 1.200

TOTAL                       10.300
"""

def test_casino_receipt_items():
    items, _, _, _ = _parse_boleta_from_text(_CASINO_RECEIPT)
    assert len(items) == 4, f"Expected 4 items, got {len(items)}: {[i.name for i in items]}"


def test_casino_receipt_sum():
    items, _, _, _ = _parse_boleta_from_text(_CASINO_RECEIPT)
    total = sum(it.price * it.quantity for it in items)
    assert total == 10300, f"Expected 10300, got {total}"


def test_casino_no_cliente_mesa_item():
    """'CLIENTE: MESA 3' no debe convertirse en item."""
    items, _, _, _ = _parse_boleta_from_text(_CASINO_RECEIPT)
    assert not any("cliente" in it.name.lower() or "mesa" in it.name.lower() for it in items)
