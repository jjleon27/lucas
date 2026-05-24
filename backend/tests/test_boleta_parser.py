"""
Tests for _parse_boleta_from_text() and _parse_clp().

Run with:  cd backend && python -m pytest tests/test_boleta_parser.py -v

These tests are intentionally standalone — they don't need a running server,
database, or API key. They only import pure-python functions from ocr.py.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Patch heavy imports that aren't needed for unit tests
import unittest.mock as _mock
_mock.patch.dict("sys.modules", {
    "cv2": _mock.MagicMock(),
    "pytesseract": _mock.MagicMock(),
    "PIL": _mock.MagicMock(),
    "PIL.Image": _mock.MagicMock(),
}).start()

from app.ocr import _parse_boleta_from_text, _parse_clp  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────

def items_sum(items) -> float:
    return sum(it.price * it.quantity for it in items)


# ─────────────────────────────────────────────────────────────────────────────
# _parse_clp
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_clp_dot_thousands():
    assert _parse_clp("29.521") == 29521.0

def test_parse_clp_comma_thousands():
    assert _parse_clp("29,521") == 29521.0

def test_parse_clp_plain():
    assert _parse_clp("1750") == 1750.0

def test_parse_clp_dollar_prefix():
    assert _parse_clp("$ 1.750") == 1750.0

def test_parse_clp_spaces():
    assert _parse_clp("$ 29 521") == 29521.0

def test_parse_clp_large():
    assert _parse_clp("1.489.991") == 1489991.0


# ─────────────────────────────────────────────────────────────────────────────
# Lider receipt — the known ground truth
# ─────────────────────────────────────────────────────────────────────────────

LIDER_OCR_TEXT = """
LIDER
AV.PDTE.EDO.FREI MONTALVA 8301,QUILICURA
76134941-4
SUC: AV. AMERICO VESPUCIO SUR 881
SANTIAGO
Bol. Electronica: 002561252664 Caja: 0075
Fecha: 09/04/2026  Hora: 16:46:02

CANT  PRECIO UNITARIO      TOTAL NETO
CODIGO           DESC. ARTICULO

7803468001250 CT PAN PITA                $ 1.750
7800159052287 BBQ ORI 510               $ 3.390
7891515551995
2x4.990       PECHU POLLO               $ 9.980
0400005187100 MOLIDA ESPEC              $ 6.090
7802351001582 V MANZAN 500              $ 1.650
0021000026968 MAYO KR 397               $ 4.690
7800120163189 MIELYALM 330              $ 2.000
7800159001049 MOST DP 350               $ 2.390
7800159000752 KET SQS 397G              $ 3.190

TOTAL NETO $     29.521
TOTAL IVA (19%) $ 5.609
TARJETA DE CREDITO $   35.130

TOTAL NUMERO DE ARTIC VEND = 10
NRO DE ORDEN 400001974070
COMPROBANTE DE VENTA TARJETA DE CREDITO
597055555530 - Local 71
09/04/2026
************ 44  VI
TOTAL    35.130
COD AUTORIZACION: 422561
NUMERO UNICO : 00710750148090420261646 02
"""

def test_lider_total_neto():
    _, neto, _, _ = _parse_boleta_from_text(LIDER_OCR_TEXT)
    assert neto == 29521.0, f"Expected 29521, got {neto}"

def test_lider_iva():
    _, _, iva, _ = _parse_boleta_from_text(LIDER_OCR_TEXT)
    assert iva == 5609.0, f"Expected 5609, got {iva}"

def test_lider_item_count():
    items, _, _, _ = _parse_boleta_from_text(LIDER_OCR_TEXT)
    # 9 products (no IVA row — that's added separately)
    assert len(items) == 9, f"Expected 9 items, got {len(items)}: {[i.name for i in items]}"

def test_lider_items_sum_equals_neto():
    items, neto, _, _ = _parse_boleta_from_text(LIDER_OCR_TEXT)
    s = items_sum(items)
    assert abs(s - neto) <= neto * 0.01, f"items_sum={s} should ≈ neto={neto}"

def test_lider_confidence_high():
    _, _, _, conf = _parse_boleta_from_text(LIDER_OCR_TEXT)
    assert conf >= 0.97, f"Confidence should be ≥0.97 for clean receipt, got {conf:.3f}"

def test_lider_pan_pita():
    items, _, _, _ = _parse_boleta_from_text(LIDER_OCR_TEXT)
    names = [it.name.upper() for it in items]
    assert any("PAN" in n and "PITA" in n for n in names), f"Missing Pan Pita: {names}"

def test_lider_pechu_pollo_quantity():
    items, _, _, _ = _parse_boleta_from_text(LIDER_OCR_TEXT)
    pollo = next((it for it in items if "POLLO" in it.name.upper()), None)
    assert pollo is not None, "Missing PECHU POLLO"
    assert pollo.quantity == 2, f"Expected qty=2, got {pollo.quantity}"
    assert pollo.price == 4990, f"Expected unit price 4990, got {pollo.price}"

def test_lider_mayo_not_missing():
    items, _, _, _ = _parse_boleta_from_text(LIDER_OCR_TEXT)
    names = [it.name.upper() for it in items]
    assert any("MAYO" in n for n in names), f"MAYO KR 397 missing: {names}"

def test_lider_no_barcode_in_names():
    items, _, _, _ = _parse_boleta_from_text(LIDER_OCR_TEXT)
    for it in items:
        # No item name should start with a 12+ digit number
        import re
        assert not re.match(r"^\d{12,}", it.name), \
            f"Barcode leaked into item name: '{it.name}'"

def test_lider_no_price_above_total():
    items, neto, _, _ = _parse_boleta_from_text(LIDER_OCR_TEXT)
    for it in items:
        assert it.price <= neto, \
            f"Item '{it.name}' price {it.price} > TOTAL NETO {neto}"


# ─────────────────────────────────────────────────────────────────────────────
# Restaurant receipt (no barcodes, no TOTAL NETO breakdown)
# ─────────────────────────────────────────────────────────────────────────────

RESTAURANT_OCR_TEXT = """
RESTAURANTE EL RINCON
RUT: 76.123.456-7
Fecha: 10/04/2026  Mesa: 5

Hamburguesa clasica           $ 8.500
Papas fritas                  $ 3.500
Coca-Cola 500ml               $ 2.500
Agua mineral                  $ 1.500

SUBTOTAL                      $ 16.000
PROPINA (10%)                 $ 1.600
TOTAL                         $ 17.600
"""

def test_restaurant_no_crash():
    items, neto, iva, conf = _parse_boleta_from_text(RESTAURANT_OCR_TEXT)
    # Should not crash; might return 0 confidence (no TOTAL NETO row)
    assert isinstance(items, list)
    assert isinstance(conf, float)

def test_restaurant_total_zero_neto():
    # Restaurants don't always have TOTAL NETO — that's fine
    _, neto, _, _ = _parse_boleta_from_text(RESTAURANT_OCR_TEXT)
    # Could be 0 (no TOTAL NETO row) — acceptable
    assert neto >= 0


# ─────────────────────────────────────────────────────────────────────────────
# Bank screenshot — should return empty items and zero confidence
# ─────────────────────────────────────────────────────────────────────────────

BANK_SCREENSHOT_TEXT = """
Santander
Cuenta Corriente
**** **** **** 4521

Movimientos

COMPRA MOVISTAR PAY SIMSCV     -$17.517
COMPRA UBER*EATS               -$15.990
COMPRA NETFLIX.COM             -$8.990
ABONO TRANSFERENCIA            +$150.000

Saldo disponible: $234.503
"""

def test_bank_no_neto():
    _, neto, _, _ = _parse_boleta_from_text(BANK_SCREENSHOT_TEXT)
    assert neto == 0.0, f"Bank screenshot should have neto=0, got {neto}"

def test_bank_low_confidence():
    _, _, _, conf = _parse_boleta_from_text(BANK_SCREENSHOT_TEXT)
    assert conf == 0.0, f"Bank screenshot confidence should be 0, got {conf}"


# ─────────────────────────────────────────────────────────────────────────────
# Jumbo receipt (different supermarket, same format)
# ─────────────────────────────────────────────────────────────────────────────

JUMBO_OCR_TEXT = """
JUMBO
SUPERMERCADO JUMBO S.A.
Av. Kennedy 9001, Vitacura
Fecha: 08/04/2026  Caja: 012

7500435012345 LECHE ENTERA 1L            $ 1.290
7501234567890 YOGURT FRUTADO             $ 990
7509876543210 PAN MOLDE 550G             $ 2.190
7512345678901 QUESO GAUDA 200G           $ 3.490

TOTAL NETO $    6.462
IVA (19%) $     1.228
EFECTIVO $      7.690
"""

def test_jumbo_total_neto():
    _, neto, _, _ = _parse_boleta_from_text(JUMBO_OCR_TEXT)
    assert neto == 6462.0, f"Expected 6462, got {neto}"

def test_jumbo_iva():
    _, _, iva, _ = _parse_boleta_from_text(JUMBO_OCR_TEXT)
    assert iva == 1228.0, f"Expected 1228, got {iva}"

def test_jumbo_item_count():
    items, _, _, _ = _parse_boleta_from_text(JUMBO_OCR_TEXT)
    assert len(items) == 4, f"Expected 4 items, got {len(items)}"

def test_jumbo_confidence():
    _, _, _, conf = _parse_boleta_from_text(JUMBO_OCR_TEXT)
    assert conf >= 0.97, f"Clean Jumbo receipt should have high confidence, got {conf:.3f}"


# ─────────────────────────────────────────────────────────────────────────────
# OCR noise variations — the parser must be resilient
# ─────────────────────────────────────────────────────────────────────────────

LIDER_NOISY_TEXT = """
LlDER
AV.PDTE.EDO.FREl MONTALVA 8301

7803468001250 CT PAN PlTA               $ 1.750
7800159052287 BBQ 0Rl 510               $ 3.390
7891515551995
2x4.990       PECHU P0LL0               $ 9.980
0400005187100 M0LIDA ESPEC              $ 6.090
7802351001582 V MANZAN 500              $ 1.650
0021000026968 MAY0 KR 397               $ 4.690
7800120163189 MIELYALM 330              $ 2.000
7800159001049 M0ST DP 350               $ 2.390
7800159000752 KET SQS 397G              $ 3.190

T0TAL NET0 $    29.521
T0TAL IVA (19%) $ 5.609
TARJETA DE CREDIT0 $ 35.130
"""

def test_noisy_lider_still_finds_neto():
    _, neto, _, _ = _parse_boleta_from_text(LIDER_NOISY_TEXT)
    # Even with 0→O and I→l substitutions, TOTAL NETO should be found
    assert neto > 0, "Should find TOTAL NETO even with OCR noise"

def test_noisy_lider_items_present():
    items, _, _, _ = _parse_boleta_from_text(LIDER_NOISY_TEXT)
    assert len(items) >= 7, f"Should find most items even with noise, got {len(items)}"


# ─────────────────────────────────────────────────────────────────────────────
# Farmacia receipt
# ─────────────────────────────────────────────────────────────────────────────

FARMACIA_OCR_TEXT = """
FARMACIAS CRUZ VERDE
Av. Providencia 1234, Santiago
RUT: 96.xxx.xxx-x
Fecha: 07/04/2026

7800123456789 PARACETAMOL 500MG x20     $ 2.990
7800987654321 IBUPROFENO 400MG x10      $ 4.490
7800111222333 VITAMINA C 1G x30         $ 5.990

TOTAL NETO $   11.340
IVA (19%)  $    2.155
DEBITO     $   13.495
"""

def test_farmacia_items():
    items, neto, _, conf = _parse_boleta_from_text(FARMACIA_OCR_TEXT)
    assert len(items) == 3
    assert neto == 11340.0
    assert conf >= 0.97
