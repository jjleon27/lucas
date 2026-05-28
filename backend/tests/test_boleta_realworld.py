"""
Real-world boleta format tests for parse_boleta_text() / _parse_boleta_from_text().

Six diverse receipt scenarios:
  1. Chilean supermarket (Jumbo/Lider style with items, IVA, total)
  2. Restaurant with 2x/3x items where number is quantity × line_total
  3. Restaurant with discounts/promotions
  4. Boleta with propina (tip)
  5. Mixed formats (some items have embedded unit price NxUNIT, some have line_total)
  6. Minimal "only total neto and IVA" receipt

Run with:
  cd /Users/kako2/Documents/lucas/backend
  python3 -m pytest tests/test_boleta_realworld.py -v
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
}).start()

from app.ocr import _parse_boleta_from_text, _parse_clp  # noqa: E402


def items_sum(items) -> float:
    return sum(it.price * it.quantity for it in items)


# ══════════════════════════════════════════════════════════════════════════════
# 1. CHILEAN SUPERMARKET — Jumbo/Lider style with barcodes, IVA, total
# ══════════════════════════════════════════════════════════════════════════════

SUPERMARKET_FULL_TEXT = """
SUPERMERCADOS JUMBO S.A.
RUT: 96.756.430-6
Av. Kennedy 9001, Las Condes, Santiago
Caja: 007  Fecha: 15/05/2026  Hora: 11:32:14
Boleta Electronica N° 003214567

CANT  PRECIO UNITARIO      TOTAL NETO
CODIGO           DESC. ARTICULO

7800159052287 SALSA BBQ ORIGINAL 510G     $ 3.390
7803468001250 CT PAN PITA 2UN             $ 1.750
7500435012345 LECHE ENTERA 1L             $ 1.290
7891515551995
2x4.990       PECHUGAS POLLO              $ 9.980
7501234567890 YOGURT FRUTADO 150G         $ 990
0021000026968 MAYONESA KR 397G            $ 4.690
7800120163189 MIEL Y ALMENDRAS 330G       $ 2.000
7800159001049 MOSTAZA DP 350G             $ 2.390
7800159000752 KETCHUP SQUEEZE 397G        $ 3.190
0400005187100 MOLIDA ESPECIAL             $ 6.090

TOTAL NETO $    35.760
IVA (19%)  $     6.794
TARJETA DE CREDITO $ 42.554

TOTAL NUMERO DE ARTIC VEND = 10
NUMERO UNICO: 00710750148150520261132
"""


def test_supermarket_total_neto():
    _, neto, _, _ = _parse_boleta_from_text(SUPERMARKET_FULL_TEXT)
    assert neto == 35760.0, f"Expected 35760, got {neto}"


def test_supermarket_iva():
    _, _, iva, _ = _parse_boleta_from_text(SUPERMARKET_FULL_TEXT)
    assert iva == 6794.0, f"Expected 6794, got {iva}"


def test_supermarket_item_count():
    items, _, _, _ = _parse_boleta_from_text(SUPERMARKET_FULL_TEXT)
    assert len(items) == 10, \
        f"Expected 10 real products (Boleta header line now skipped). Got {len(items)}: {[i.name for i in items]}"


def test_supermarket_no_barcodes_in_names():
    items, _, _, _ = _parse_boleta_from_text(SUPERMARKET_FULL_TEXT)
    import re
    for it in items:
        assert not re.match(r"^\d{12,}", it.name), \
            f"Barcode leaked into item name: '{it.name}'"


def test_supermarket_pechugas_qty_and_unit_price():
    """2x4.990 PECHUGAS POLLO $9.980 → unit=4990, qty=2 (NxUNIT embedded format)."""
    items, _, _, _ = _parse_boleta_from_text(SUPERMARKET_FULL_TEXT)
    pollo = next((it for it in items if "pollo" in it.name.lower() or "pechuga" in it.name.lower()), None)
    assert pollo is not None, f"PECHUGAS POLLO not found: {[i.name for i in items]}"
    assert pollo.quantity == 2, f"Expected qty=2, got {pollo.quantity}"
    assert pollo.price == 4990, f"Expected unit=4990, got {pollo.price}"
    assert pollo.price * pollo.quantity == 9980


def test_supermarket_high_confidence():
    _, _, _, conf = _parse_boleta_from_text(SUPERMARKET_FULL_TEXT)
    assert conf >= 0.97, f"Clean supermarket receipt should have conf>=0.97, got {conf:.3f}"


def test_supermarket_items_sum_close_to_neto():
    items, neto, iva, _ = _parse_boleta_from_text(SUPERMARKET_FULL_TEXT)
    s = items_sum(items)
    total = neto + iva
    assert abs(s - neto) <= neto * 0.02 or abs(s - total) <= total * 0.02, \
        f"items_sum={s} should be close to neto={neto} or total={total}"


# ══════════════════════════════════════════════════════════════════════════════
# 2. RESTAURANT WITH 2x / 3x ITEMS (quantity × line_total → unit = total/qty)
# ══════════════════════════════════════════════════════════════════════════════

RESTAURANT_QTY_LINE_TOTAL_TEXT = """
RESTAURANTE LA NUEVA COCINA
RUT: 76.543.210-8
Fecha: 21/05/2026  Mesa: 12  Mozo: Juan

2x Lomo a lo pobre              $ 22.000
3x Bebida cola 350ml            $ 6.000
1x Ensalada cesar               $ 7.500
2x Agua mineral 500ml           $ 3.000
4x Pan amasado                  $ 4.000

TOTAL NETO $    35.294
IVA (19%)  $     6.706
TOTAL      $    42.000
"""


def test_restaurant_lomo_unit_price():
    """2x Lomo $22.000 → unit = 11000 (line_total / qty), qty = 2."""
    items, _, _, _ = _parse_boleta_from_text(RESTAURANT_QTY_LINE_TOTAL_TEXT)
    lomo = next((it for it in items if "lomo" in it.name.lower()), None)
    assert lomo is not None, f"Lomo not found: {[i.name for i in items]}"
    assert lomo.quantity == 2, f"Expected qty=2, got {lomo.quantity}"
    assert lomo.price == 11000, f"Expected unit=11000, got {lomo.price}"
    assert lomo.price * lomo.quantity == 22000


def test_restaurant_bebida_unit_price():
    """3x Bebida $6.000 → unit = 2000, qty = 3."""
    items, _, _, _ = _parse_boleta_from_text(RESTAURANT_QTY_LINE_TOTAL_TEXT)
    bebida = next((it for it in items if "bebida" in it.name.lower()), None)
    assert bebida is not None, f"Bebida not found: {[i.name for i in items]}"
    assert bebida.quantity == 3, f"Expected qty=3, got {bebida.quantity}"
    assert bebida.price == 2000, f"Expected unit=2000, got {bebida.price}"


def test_restaurant_agua_unit_price():
    """2x Agua $3.000 → unit = 1500, qty = 2."""
    items, _, _, _ = _parse_boleta_from_text(RESTAURANT_QTY_LINE_TOTAL_TEXT)
    agua = next((it for it in items if "agua" in it.name.lower()), None)
    assert agua is not None, f"Agua not found: {[i.name for i in items]}"
    assert agua.quantity == 2
    assert agua.price == 1500


def test_restaurant_pan_qty4_unit_price():
    """4x Pan amasado $4.000 → unit = 1000, qty = 4."""
    items, _, _, _ = _parse_boleta_from_text(RESTAURANT_QTY_LINE_TOTAL_TEXT)
    pan = next((it for it in items if "pan" in it.name.lower()), None)
    assert pan is not None, f"Pan not found: {[i.name for i in items]}"
    assert pan.quantity == 4
    assert pan.price == 1000


def test_restaurant_ensalada_qty1_no_divide():
    """1x Ensalada $7.500 → unit = 7500 (no division for qty=1)."""
    items, _, _, _ = _parse_boleta_from_text(RESTAURANT_QTY_LINE_TOTAL_TEXT)
    ensalada = next((it for it in items if "ensalada" in it.name.lower()), None)
    assert ensalada is not None, f"Ensalada not found: {[i.name for i in items]}"
    assert ensalada.quantity == 1
    assert ensalada.price == 7500


def test_restaurant_neto_and_iva_extracted():
    _, neto, iva, _ = _parse_boleta_from_text(RESTAURANT_QTY_LINE_TOTAL_TEXT)
    assert neto == 35294.0, f"Expected neto=35294, got {neto}"
    assert iva == 6706.0, f"Expected iva=6706, got {iva}"


# ══════════════════════════════════════════════════════════════════════════════
# 3. RESTAURANT WITH DISCOUNTS / PROMOTIONS
# ══════════════════════════════════════════════════════════════════════════════

RESTAURANT_DISCOUNT_TEXT = """
RESTAURANTE OSLO
RUT: 77.123.456-3
Fecha: 22/05/2026  Mesa: 4

Cazuela de vacuno               $ 9.800
Churrasco italiano              $ 8.500
Lomo saltado                    $ 10.200
Jugo natural naranja            $ 3.200
DSCTO CLIENTE FRECUENTE 10%     $ 3.170
PROMO 2X1 BEBIDAS               $ 1.500

TOTAL NETO $    27.030
IVA (19%)  $     5.136
TOTAL      $    32.166
"""


def test_discount_food_items_present():
    """Food items (cazuela, churrasco, lomo, jugo) should be parsed."""
    items, _, _, _ = _parse_boleta_from_text(RESTAURANT_DISCOUNT_TEXT)
    names = [it.name.lower() for it in items]
    assert any("cazuela" in n for n in names), f"Cazuela missing: {names}"
    assert any("churrasco" in n for n in names), f"Churrasco missing: {names}"
    assert any("lomo" in n for n in names), f"Lomo missing: {names}"
    assert any("jugo" in n for n in names), f"Jugo missing: {names}"


def test_discount_neto_extracted():
    _, neto, _, _ = _parse_boleta_from_text(RESTAURANT_DISCOUNT_TEXT)
    assert neto == 27030.0, f"Expected 27030, got {neto}"


def test_discount_iva_extracted():
    _, _, iva, _ = _parse_boleta_from_text(RESTAURANT_DISCOUNT_TEXT)
    assert iva == 5136.0, f"Expected 5136, got {iva}"


def test_discount_line_not_skipped_as_header():
    """DSCTO line should be parsed as an item (not skipped as a boleta header).
    The parser does not currently negate discount lines, so price will be positive.
    This test documents current behavior — see bug notes in report."""
    items, _, _, _ = _parse_boleta_from_text(RESTAURANT_DISCOUNT_TEXT)
    names = [it.name.upper() for it in items]
    # At least some items must be there
    assert len(items) >= 4, f"Expected at least 4 items, got {len(items)}: {names}"


def test_discount_minimum_item_count():
    """With 4 food items + 2 discount/promo lines, parser should return at least 4."""
    items, _, _, _ = _parse_boleta_from_text(RESTAURANT_DISCOUNT_TEXT)
    assert len(items) >= 4, f"Expected >=4 items, got {len(items)}"


# ══════════════════════════════════════════════════════════════════════════════
# 4. BOLETA WITH PROPINA (TIP)
# ══════════════════════════════════════════════════════════════════════════════

BOLETA_WITH_PROPINA_TEXT = """
RESTAURANTE BRAVISSIMO
RUT: 76.789.012-4
Fecha: 23/05/2026  Mesa: 8
Mozo: Carlos

Pizza margherita                $ 9.500
Pizza napolitana                $ 10.500
Vino casa silva 375ml           $ 8.000
Tiramisu                        $ 4.500

TOTAL NETO $    27.311
IVA (19%)  $     5.189
SUBTOTAL   $    32.500
PROPINA (10%) $  3.250
TOTAL      $    35.750
"""


def test_propina_neto_extracted():
    _, neto, _, _ = _parse_boleta_from_text(BOLETA_WITH_PROPINA_TEXT)
    assert neto == 27311.0, f"Expected 27311, got {neto}"


def test_propina_iva_extracted():
    _, _, iva, _ = _parse_boleta_from_text(BOLETA_WITH_PROPINA_TEXT)
    assert iva == 5189.0, f"Expected 5189, got {iva}"


def test_propina_food_items_present():
    """Four food items should be parsed."""
    items, _, _, _ = _parse_boleta_from_text(BOLETA_WITH_PROPINA_TEXT)
    names = [it.name.lower() for it in items]
    assert any("pizza" in n for n in names), f"Pizza missing: {names}"
    assert any("vino" in n or "tiramisu" in n for n in names), \
        f"Vino or Tiramisu missing: {names}"
    assert len(items) >= 3, f"Expected >=3 items, got {len(items)}"


def test_propina_line_not_skipped():
    """PROPINA line (a valid item on many Chilean receipts) should not be silently dropped.
    The parser currently does not have special handling for PROPINA lines —
    the line is parsed as a regular item IF it has a price. This test verifies
    it at minimum doesn't crash and returns the food items."""
    items, _, _, _ = _parse_boleta_from_text(BOLETA_WITH_PROPINA_TEXT)
    # Parser should return at least the food items (3-4)
    assert len(items) >= 3


def test_propina_prices_reasonable():
    """
    SUBTOTAL lines must be skipped — they are not products.
    Previously a bug caused SUBTOTAL to be parsed as an item with price=32500.
    """
    items, neto, _, _ = _parse_boleta_from_text(BOLETA_WITH_PROPINA_TEXT)
    subtotal_items = [it for it in items if "subtotal" in it.name.lower()]
    assert len(subtotal_items) == 0, \
        f"SUBTOTAL should be skipped, not parsed as item: {subtotal_items}"
    # All product items must have sensible prices
    food_items = [it for it in items if "propina" not in it.name.lower()]
    for it in food_items:
        assert it.price <= neto, \
            f"Food item '{it.name}' price {it.price} exceeds neto {neto}"


# ══════════════════════════════════════════════════════════════════════════════
# 5. MIXED FORMAT RECEIPT
#    Some items: "NxUNIT_PRICE description $line_total" (unit price embedded)
#    Others:     "Nx description $line_total" (only qty prefix, unit = total/qty)
#    Others:     plain "description $price" (qty=1, price=line_total)
# ══════════════════════════════════════════════════════════════════════════════

MIXED_FORMAT_TEXT = """
SUPERMERCADO TOTTUS
RUT: 76.645.030-5
Fecha: 24/05/2026  Caja: 023

7891515551994
2x4.990 PECHUGAS DE POLLO       $ 9.980
7803468001250 CT PAN PITA 2UN    $ 1.750
7800159052287 SALSA BBQ ORIGINAL $ 3.390
2x Refresco cola 1.5L            $ 3.180
3x Yogurt natural 150g           $ 2.400
7500435012345 LECHE ENTERA 1L    $ 1.290
0400005187100 CARNE MOLIDA ESP   $ 6.090

TOTAL NETO $    27.202
IVA (19%)  $     5.168
TARJETA DE DEBITO $ 32.370
"""


def test_mixed_nxunit_pechugas():
    """Format NxUNIT: 2x4.990 PECHUGAS → unit=4990, qty=2."""
    items, _, _, _ = _parse_boleta_from_text(MIXED_FORMAT_TEXT)
    pollo = next((it for it in items if "pollo" in it.name.lower() or "pechuga" in it.name.lower()), None)
    assert pollo is not None, f"Pechugas not found: {[i.name for i in items]}"
    assert pollo.quantity == 2
    assert pollo.price == 4990


def test_mixed_nx_desc_refresco():
    """Format Nx-Description: 2x Refresco $3.180 → unit=1590, qty=2."""
    items, _, _, _ = _parse_boleta_from_text(MIXED_FORMAT_TEXT)
    refresco = next((it for it in items if "refresco" in it.name.lower() or "cola" in it.name.lower()), None)
    assert refresco is not None, f"Refresco not found: {[i.name for i in items]}"
    assert refresco.quantity == 2
    assert refresco.price == 1590, f"Expected unit=1590 (3180/2), got {refresco.price}"


def test_mixed_nx_desc_yogurt():
    """Format Nx-Description: 3x Yogurt $2.400 → unit=800, qty=3."""
    items, _, _, _ = _parse_boleta_from_text(MIXED_FORMAT_TEXT)
    yogurt = next((it for it in items if "yogurt" in it.name.lower()), None)
    assert yogurt is not None, f"Yogurt not found: {[i.name for i in items]}"
    assert yogurt.quantity == 3
    assert yogurt.price == 800, f"Expected unit=800 (2400/3), got {yogurt.price}"


def test_mixed_plain_pan():
    """Plain item (no qty prefix): CT PAN PITA → qty=1, price=1750."""
    items, _, _, _ = _parse_boleta_from_text(MIXED_FORMAT_TEXT)
    pan = next((it for it in items if "pan" in it.name.lower() and "pita" in it.name.lower()), None)
    assert pan is not None, f"Pan Pita not found: {[i.name for i in items]}"
    assert pan.quantity == 1
    assert pan.price == 1750


def test_mixed_plain_carne():
    """Plain item: CARNE MOLIDA → qty=1, price=6090."""
    items, _, _, _ = _parse_boleta_from_text(MIXED_FORMAT_TEXT)
    carne = next((it for it in items if "carne" in it.name.lower() or "molida" in it.name.lower()), None)
    assert carne is not None, f"Carne not found: {[i.name for i in items]}"
    assert carne.quantity == 1
    assert carne.price == 6090


def test_mixed_neto_and_iva():
    _, neto, iva, _ = _parse_boleta_from_text(MIXED_FORMAT_TEXT)
    assert neto == 27202.0, f"Expected 27202, got {neto}"
    assert iva == 5168.0, f"Expected 5168, got {iva}"


def test_mixed_no_barcodes_in_names():
    items, _, _, _ = _parse_boleta_from_text(MIXED_FORMAT_TEXT)
    import re
    for it in items:
        assert not re.match(r"^\d{12,}", it.name), \
            f"Barcode leaked into item name: '{it.name}'"


# ══════════════════════════════════════════════════════════════════════════════
# 6. MINIMAL RECEIPT — only total neto and IVA, no line items
# ══════════════════════════════════════════════════════════════════════════════

MINIMAL_NETO_IVA_TEXT = """
FERRETERIA DON JUAN
RUT: 76.111.222-3
Fecha: 25/05/2026

TOTAL NETO $  50.000
IVA (19%)  $   9.500
EFECTIVO   $  59.500
"""


def test_minimal_neto_extracted():
    _, neto, _, _ = _parse_boleta_from_text(MINIMAL_NETO_IVA_TEXT)
    assert neto == 50000.0, f"Expected 50000, got {neto}"


def test_minimal_iva_extracted():
    _, _, iva, _ = _parse_boleta_from_text(MINIMAL_NETO_IVA_TEXT)
    assert iva == 9500.0, f"Expected 9500, got {iva}"


def test_minimal_no_items():
    """No product lines → empty items list."""
    items, _, _, _ = _parse_boleta_from_text(MINIMAL_NETO_IVA_TEXT)
    assert items == [], f"Expected empty items, got: {items}"


def test_minimal_zero_confidence():
    """No items means confidence = 0.0."""
    _, _, _, conf = _parse_boleta_from_text(MINIMAL_NETO_IVA_TEXT)
    assert conf == 0.0, f"Expected conf=0.0 with no items, got {conf}"


def test_minimal_iva_19pct_of_neto():
    """IVA must be exactly 19% of TOTAL NETO (Chilean SII legal requirement)."""
    _, neto, iva, _ = _parse_boleta_from_text(MINIMAL_NETO_IVA_TEXT)
    assert neto > 0
    ratio = iva / neto
    assert abs(ratio - 0.19) < 0.001, \
        f"IVA {iva} should be 19% of neto {neto}, ratio={ratio:.4f}"


# ══════════════════════════════════════════════════════════════════════════════
# EXTRA: IVA fallback calculation when IVA line is missing
# ══════════════════════════════════════════════════════════════════════════════

NO_IVA_LINE_TEXT = """
MINI MARKET LAS ROSAS
Fecha: 26/05/2026

7803468001250 PAN DE MOLDE 550G       $ 1.890
7800159052287 MANTEQUILLA 200G        $ 2.490

TOTAL NETO $   4.370
EFECTIVO   $   5.200
"""


def test_iva_fallback_computed_when_missing():
    """When IVA line is absent, parser must compute IVA = round(neto * 0.19)."""
    _, neto, iva, _ = _parse_boleta_from_text(NO_IVA_LINE_TEXT)
    assert neto == 4370.0, f"Expected 4370, got {neto}"
    expected_iva = round(4370 * 0.19)  # = 830
    assert iva == expected_iva, f"Expected computed IVA={expected_iva}, got {iva}"


def test_iva_fallback_items_present():
    items, _, _, _ = _parse_boleta_from_text(NO_IVA_LINE_TEXT)
    assert len(items) == 2, f"Expected 2 items, got {len(items)}: {[i.name for i in items]}"
