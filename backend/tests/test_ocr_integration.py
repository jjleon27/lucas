"""
Integration tests for vision_parse() reconcile logic in ocr.py.

Tests mock the LLM layer — no real OpenAI calls are made.
Covers:
  - vision_parse graceful None when AI unavailable
  - reconcile: near-exact ratio (≤2%) → no Servicio line
  - reconcile: small gap (≤25%) → Servicio/Otros line added
  - reconcile: big gap + plausible items → find better total from OCR text
  - reconcile: implausible items (all prices < 100) → items dropped
  - _items_look_plausible edge cases
  - _find_plausible_total with Chilean number format
  - Edge case: items empty but amount set
  - Edge case: amount=0 with items
  - Edge case: all items price=0
  - Edge case: single item exactly matches amount
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json
import unittest.mock as mock

# Mock heavy C-extension dependencies before importing ocr
mock.patch.dict("sys.modules", {
    "cv2": mock.MagicMock(),
    "pytesseract": mock.MagicMock(),
    "PIL": mock.MagicMock(),
    "PIL.Image": mock.MagicMock(),
}).start()

import pytest
from app.ocr import vision_parse, _items_look_plausible, _find_plausible_total
from app.schemas import ParsedItem


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_item(name, price, qty=1):
    return ParsedItem(name=name, price=price, quantity=qty)


def _make_llm_resp(amount, items, merchant="Test", date="2026-06-01",
                   total_neto=0, iva_amount=0):
    """Build a minimal JSON string that vision_parse will parse from the LLM."""
    txs = [{
        "amount": amount,
        "merchant": merchant,
        "date": date,
        "category": "Alimentación",
        "description": "",
        "is_income": False,
        "total_neto": total_neto,
        "iva_amount": iva_amount,
        "items": [
            {"name": it.name, "price": it.price, "quantity": it.quantity}
            for it in items
        ],
    }]
    return json.dumps({"transactions": txs, "currency": "CLP"})


class FakeLLMResponse:
    def __init__(self, text):
        self.text = text
        self.input_tokens = 0
        self.output_tokens = 0
        self.model = "gpt-4o"


def _patch_vision(monkeypatch, llm_json: str, ocr_text: str = ""):
    """Patch ai_provider and run_ocr so vision_parse doesn't hit real APIs."""
    import app.ocr as ocr_mod
    monkeypatch.setattr("app.ocr.ai_provider.is_available", lambda: True)
    monkeypatch.setattr(
        "app.ocr.ai_provider.vision_json",
        lambda **_kw: FakeLLMResponse(llm_json),
    )
    monkeypatch.setattr("app.ocr.run_ocr", lambda _b: ocr_text)
    # Make fast-path Tesseract always miss (conf < 0.97)
    monkeypatch.setattr(
        "app.ocr._parse_boleta_from_text",
        lambda _t: ([], 0.0, 0.0, 0.0),
    )
    monkeypatch.setattr("app.ocr._shrink_for_vision", lambda b: b)
    monkeypatch.setattr("app.ocr._detect_mime", lambda _b: "image/jpeg")


# ─────────────────────────────────────────────────────────────────────────────
# a) vision_parse with no LLM key → returns None gracefully
# ─────────────────────────────────────────────────────────────────────────────

def test_vision_parse_no_llm_key_returns_none(monkeypatch):
    """When AI provider is unavailable, vision_parse must return None (no crash)."""
    monkeypatch.setattr("app.ocr.ai_provider.is_available", lambda: False)
    result = vision_parse(b"\xff\xd8\xff")
    assert result is None, f"Expected None when LLM unavailable, got {result}"


# ─────────────────────────────────────────────────────────────────────────────
# b) reconcile: near-exact ratio (≤2%) — no Servicio line added
# ─────────────────────────────────────────────────────────────────────────────

def test_reconcile_near_exact_no_servicio(monkeypatch):
    """items_sum / amount ≈ 1.0 (within 2%) → no 'Servicio' line appended."""
    items = [make_item("Cerveza", 4900), make_item("Empanada", 2000)]
    # items_sum = 6900, amount = 7000 → ratio = 0.9857 → |ratio-1| = 0.014 ≤ 0.02
    llm = _make_llm_resp(amount=7000, items=items)
    _patch_vision(monkeypatch, llm)

    result = vision_parse(b"\xff\xd8\xff")
    assert result is not None
    tx = result.transactions[0]
    names = [it.name for it in tx.items]
    assert not any("servicio" in n.lower() or "otros" in n.lower() for n in names), \
        f"No Servicio line expected for near-exact ratio, got items: {names}"
    assert tx.amount == 7000


# ─────────────────────────────────────────────────────────────────────────────
# c) reconcile: small gap (≤25%) → Servicio/Otros line added
# ─────────────────────────────────────────────────────────────────────────────

def test_reconcile_small_gap_adds_servicio(monkeypatch):
    """items_sum / amount = 0.83 (gap ~17%) → Servicio / Otros line added."""
    items = [make_item("Piscola", 5000), make_item("Cerveza", 4000)]
    # items_sum = 9000, amount = 10900 → ratio = 0.826 → gap = 0.174 ≤ 0.25
    # delta = 10900 - 9000 = 1900 ≥ 100 → add Servicio
    llm = _make_llm_resp(amount=10900, items=items)
    _patch_vision(monkeypatch, llm)

    result = vision_parse(b"\xff\xd8\xff")
    assert result is not None
    tx = result.transactions[0]
    names = [it.name for it in tx.items]
    assert any("servicio" in n.lower() or "otros" in n.lower() for n in names), \
        f"Expected 'Servicio / Otros' line, got items: {names}"
    # Total items sum should now match amount
    total = sum(it.price * it.quantity for it in tx.items)
    assert total == 10900, f"After adding Servicio, sum should equal amount. Got {total}"


def test_reconcile_small_gap_discount_when_items_over(monkeypatch):
    """items_sum > amount by ≤25% → Descuento line (negative delta)."""
    items = [make_item("Hamburguesa", 8000), make_item("Papas", 3500)]
    # items_sum = 11500, amount = 10000 → ratio = 1.15 → gap = 0.15 ≤ 0.25
    # delta = 10000 - 11500 = -1500 → add Descuento
    llm = _make_llm_resp(amount=10000, items=items)
    _patch_vision(monkeypatch, llm)

    result = vision_parse(b"\xff\xd8\xff")
    assert result is not None
    tx = result.transactions[0]
    names = [it.name for it in tx.items]
    assert any("descuento" in n.lower() for n in names), \
        f"Expected 'Descuento' line for items > amount, got: {names}"


def test_reconcile_gap_delta_below_100_not_added(monkeypatch):
    """Delta < 100 CLP → gap too small to add a Servicio line (noise)."""
    items = [make_item("Café", 2000), make_item("Agua", 1000)]
    # items_sum = 3000, amount = 3080 → ratio = 0.974, delta = 80 < 100 → no Servicio
    llm = _make_llm_resp(amount=3080, items=items)
    _patch_vision(monkeypatch, llm)

    result = vision_parse(b"\xff\xd8\xff")
    assert result is not None
    tx = result.transactions[0]
    names = [it.name for it in tx.items]
    assert not any("servicio" in n.lower() for n in names), \
        f"Delta < 100 should not add Servicio, got: {names}"


# ─────────────────────────────────────────────────────────────────────────────
# d) reconcile: big gap + plausible items → uses OCR text to find better total
# ─────────────────────────────────────────────────────────────────────────────

def test_reconcile_big_gap_finds_total_from_ocr(monkeypatch):
    """LLM ratio=0.22 (bad total), items plausible → scan OCR and fix total."""
    items = [
        make_item("Piscolón Mistral 35°", 9000, 6),
        make_item("Fernet Branca", 5800, 3),
        make_item("Promo Piscola", 8000, 2),
        make_item("Alto del Carmen 35", 4500, 1),
    ]
    # items_sum = 54000 + 17400 + 16000 + 4500 = 91900
    # LLM gave a bad total (multi-customer sub-total)
    llm = _make_llm_resp(amount=414270, items=items)
    # OCR text contains the real total "127.900"
    ocr_text = "TOTAL  127.900\nSUBTOTAL  91.900\nPROPINA  36.000"
    _patch_vision(monkeypatch, llm, ocr_text=ocr_text)

    result = vision_parse(b"\xff\xd8\xff")
    assert result is not None
    tx = result.transactions[0]
    assert tx.amount == 127900, f"Expected fixed total=127900, got {tx.amount}"
    # gap=36000 = 4×9000 (Piscolón price) → gap recovery adds 4 more Piscolón
    piscolon = next((it for it in tx.items if "Piscol" in it.name), None)
    assert piscolon is not None, "Piscolón item should be present"
    assert piscolon.quantity == 10, f"Gap recovery should restore to 10×, got {piscolon.quantity}"
    # No 'Otros cargos' because gap was fully recovered
    names = [it.name for it in tx.items]
    assert not any("otros" in n.lower() for n in names), f"No Otros cargos expected, got: {names}"


def test_reconcile_big_gap_no_ocr_total_uses_items_sum(monkeypatch):
    """Big gap + plausible items but no better total in OCR → fallback to items_sum."""
    items = [
        make_item("Pizza Familiar", 12000, 2),
        make_item("Cerveza Austral", 3500, 3),
        make_item("Ensalada César", 7000, 1),
    ]
    # items_sum = 24000 + 10500 + 7000 = 41500
    # LLM gave a wildly wrong total, OCR has nothing useful
    llm = _make_llm_resp(amount=250000, items=items)
    ocr_text = "PIZZA FAMILIAR  24000\nCERVEZA  10500"  # no numbers in [1.01×,1.6×] range
    _patch_vision(monkeypatch, llm, ocr_text=ocr_text)

    result = vision_parse(b"\xff\xd8\xff")
    assert result is not None
    tx = result.transactions[0]
    assert tx.amount == 41500.0, f"Expected fallback to items_sum=41500, got {tx.amount}"


# ─────────────────────────────────────────────────────────────────────────────
# e) reconcile: implausible items (all prices < 100) → items dropped
# ─────────────────────────────────────────────────────────────────────────────

def test_reconcile_implausible_items_dropped(monkeypatch):
    """Items with tiny prices (avg < 200 CLP) and big ratio gap → items dropped."""
    items = [
        make_item("Item A", 50),
        make_item("Item B", 80),
        make_item("Item C", 30),
    ]
    # items_sum = 160, amount = 50000 → ratio = 0.0032, huge gap
    # _items_look_plausible: items_sum < 500 → False → drop items
    llm = _make_llm_resp(amount=50000, items=items)
    _patch_vision(monkeypatch, llm)

    result = vision_parse(b"\xff\xd8\xff")
    assert result is not None
    tx = result.transactions[0]
    assert tx.items == [], f"Implausible items (sum<500) should be dropped, got: {tx.items}"
    assert tx.amount == 50000


def test_reconcile_implausible_items_price_per_item_too_low(monkeypatch):
    """avg price/item < 200 → not plausible even if sum ≥ 500."""
    # items_sum = 900, avg_per_real = 900/5 = 180 < 200 → not plausible
    # BUT items_sum < 500 fails first, so use 5 items at 180 each → sum=900
    items = [make_item(f"Item{i}", 180) for i in range(5)]
    llm = _make_llm_resp(amount=90000, items=items)
    _patch_vision(monkeypatch, llm)

    result = vision_parse(b"\xff\xd8\xff")
    assert result is not None
    tx = result.transactions[0]
    # _items_look_plausible: items_sum/len(real) = 900/5 = 180 < 200 → False → drop
    assert tx.items == [], f"avg price/item=180<200 → should drop items, got: {tx.items}"


# ─────────────────────────────────────────────────────────────────────────────
# f) _items_look_plausible — direct unit tests
# ─────────────────────────────────────────────────────────────────────────────

def test_items_look_plausible_too_few_items():
    """< 2 items → not plausible (single item with gap likely has wrong unit price)."""
    assert _items_look_plausible([make_item("Solo", 5000)], 5000) is False


def test_items_look_plausible_sum_too_small():
    """items_sum < 500 → not plausible (garbage OCR noise)."""
    items = [make_item("A", 100), make_item("B", 100)]
    assert _items_look_plausible(items, 200) is False


def test_items_look_plausible_price_too_high():
    """Any price > 500_000 → not plausible (scaling artifact)."""
    items = [make_item("Cena", 600_000), make_item("Vino", 8000)]
    assert _items_look_plausible(items, 608_000) is False


def test_items_look_plausible_avg_too_low():
    """items_sum / len(real_items) < 200 → not plausible."""
    # 4 items, each 150 → avg = 150 < 200
    items = [make_item(f"X{i}", 150) for i in range(4)]
    assert _items_look_plausible(items, 600) is False


def test_items_look_plausible_realistic_restaurant():
    """Typical Chilean restaurant: 4 items avg ~3000 → plausible."""
    items = [
        make_item("Cerveza", 3000),
        make_item("Empanada", 2500),
        make_item("Pisco Sour", 4000),
        make_item("Agua", 1500),
    ]
    assert _items_look_plausible(items, 11000) is True


def test_items_look_plausible_zero_price_items_ignored():
    """Items with price=0 don't count toward avg (free add-ons like '+Coca Zero')."""
    items = [
        make_item("Piscola", 5000),
        make_item("Fernet", 4800),
        make_item("+Coca Zero", 0),
        make_item("+Hielo", 0),
    ]
    # real = 2 items (price>0), items_sum = 9800, avg = 9800/2 = 4900 ≥ 200
    assert _items_look_plausible(items, 9800) is True


def test_items_look_plausible_all_zero_prices():
    """All items price=0 → real=[] → not plausible."""
    items = [make_item("Free A", 0), make_item("Free B", 0)]
    assert _items_look_plausible(items, 0) is False


# ─────────────────────────────────────────────────────────────────────────────
# g) _find_plausible_total — with Chilean number format "127.900"
# ─────────────────────────────────────────────────────────────────────────────

def test_find_plausible_total_finds_correct_value():
    """OCR text contains '127.900' which is 1.01× < 127900 < 1.6× 91900."""
    ocr_text = "SUBTOTAL  91.900\nPROPINA  36.000\nTOTAL  127.900"
    result = _find_plausible_total(ocr_text, 91900.0)
    assert result == 127900, f"Expected 127900, got {result}"


def test_find_plausible_total_no_candidates():
    """No number in OCR is in [items_sum*1.01, items_sum*1.6] → None."""
    ocr_text = "TOTAL 50.000\nSUBTOTAL 30.000"
    # items_sum=91900 → range [92819, 147040]: neither 50000 nor 30000 qualify
    result = _find_plausible_total(ocr_text, 91900.0)
    assert result is None


def test_find_plausible_total_picks_closest():
    """When multiple candidates exist, picks the one closest to items_sum."""
    # items_sum = 50000 → range [50500, 80000]
    # candidates: 55000, 75000 → closest = 55000
    ocr_text = "CONSUMO  50.000\nCON SERVICIO  55.000\nCON PROPINA  75.000"
    result = _find_plausible_total(ocr_text, 50000.0)
    assert result == 55000, f"Expected closest candidate 55000, got {result}"


def test_find_plausible_total_sum_too_small():
    """items_sum < 500 → None immediately (guard for garbage input)."""
    result = _find_plausible_total("TOTAL 1.000", 400.0)
    assert result is None


def test_find_plausible_total_empty_ocr():
    """Empty OCR text → None gracefully."""
    result = _find_plausible_total("", 50000.0)
    assert result is None


def test_find_plausible_total_exact_match_excluded():
    """Value exactly equal to items_sum is NOT in range (needs items_sum*1.01)."""
    ocr_text = "SUBTOTAL  50.000\nTOTAL  50.000"
    result = _find_plausible_total(ocr_text, 50000.0)
    # 50000 is NOT > 50000*1.01 = 50500 → no candidate
    assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# Edge cases in reconcile branch logic
# ─────────────────────────────────────────────────────────────────────────────

def test_reconcile_no_items_but_amount_set(monkeypatch):
    """LLM returns no items but amount > 0 → transaction preserved with empty items."""
    llm = _make_llm_resp(amount=25000, items=[])
    _patch_vision(monkeypatch, llm)

    result = vision_parse(b"\xff\xd8\xff")
    assert result is not None
    tx = result.transactions[0]
    assert tx.amount == 25000
    assert tx.items == [], "Empty items should be preserved as-is"


def test_reconcile_amount_zero_with_items(monkeypatch):
    """LLM returns amount=0 with items → items_sum > 0 but ratio division skipped
    because raw_amount=0 (condition: items and raw_amount > 0 is False)."""
    items = [make_item("Cerveza", 3000), make_item("Empanada", 2000)]
    llm = _make_llm_resp(amount=0, items=items)
    _patch_vision(monkeypatch, llm)

    result = vision_parse(b"\xff\xd8\xff")
    assert result is not None
    tx = result.transactions[0]
    # No reconcile attempted when amount=0 — items survive untouched
    assert tx.amount == 0
    assert len(tx.items) == 2, f"Items should be unchanged when amount=0, got {tx.items}"


def test_reconcile_all_items_price_zero_with_amount(monkeypatch):
    """All items price=0 → items_sum=0, ratio computation skipped (guard: items_sum > 0)."""
    items = [make_item("+Add-on A", 0), make_item("+Add-on B", 0)]
    llm = _make_llm_resp(amount=15000, items=items)
    _patch_vision(monkeypatch, llm)

    result = vision_parse(b"\xff\xd8\xff")
    assert result is not None
    tx = result.transactions[0]
    # items_sum = 0, guard `if items_sum > 0` prevents division → items kept, amount kept
    assert tx.amount == 15000
    # Items are kept untouched (no crash)
    assert len(tx.items) == 2


def test_reconcile_single_item_exactly_matches_amount(monkeypatch):
    """Single item price == amount → ratio == 1.0 exactly → near-exact path, no Servicio."""
    items = [make_item("Cena completa", 45000)]
    llm = _make_llm_resp(amount=45000, items=items)
    _patch_vision(monkeypatch, llm)

    result = vision_parse(b"\xff\xd8\xff")
    assert result is not None
    tx = result.transactions[0]
    # NOTE: _items_look_plausible requires len >= 2, but reconcile checks ratio FIRST.
    # ratio = 1.0 → near-exact branch → pass through unchanged.
    assert tx.amount == 45000
    names = [it.name for it in tx.items]
    assert not any("servicio" in n.lower() for n in names), \
        f"Exact match should not add Servicio, got: {names}"


def test_reconcile_single_item_big_ratio_gap_not_plausible(monkeypatch):
    """Single item with big gap → _items_look_plausible(len<2) → False → items dropped."""
    items = [make_item("Servicio Premium", 5000)]
    # items_sum=5000, amount=50000 → ratio=0.1, big gap
    # _items_look_plausible: len < 2 → False → drop items
    llm = _make_llm_resp(amount=50000, items=items)
    _patch_vision(monkeypatch, llm)

    result = vision_parse(b"\xff\xd8\xff")
    assert result is not None
    tx = result.transactions[0]
    assert tx.items == [], \
        f"Single item with big gap should be dropped (not plausible), got: {tx.items}"
    assert tx.amount == 50000


# ─────────────────────────────────────────────────────────────────────────────
# IVA boleta path: total_neto + iva_amount override reconcile
# ─────────────────────────────────────────────────────────────────────────────

def test_iva_boleta_path_uses_neto_iva(monkeypatch):
    """When LLM returns total_neto + iva_amount, uses _normalize_boleta_items path."""
    items = [make_item("LECHE ENTERA", 1290), make_item("PAN PITA", 1750)]
    total_neto = 3040.0
    iva = round(total_neto * 0.19)  # 578
    llm = _make_llm_resp(
        amount=total_neto + iva,  # 3618
        items=items,
        total_neto=total_neto,
        iva_amount=iva,
    )
    _patch_vision(monkeypatch, llm)

    result = vision_parse(b"\xff\xd8\xff")
    assert result is not None
    tx = result.transactions[0]
    # Should have IVA row appended
    iva_items = [it for it in tx.items if "iva" in it.name.lower()]
    assert len(iva_items) == 1, f"Expected 1 IVA row, got: {[it.name for it in tx.items]}"
    assert iva_items[0].price == iva


# ─────────────────────────────────────────────────────────────────────────────
# LLM returns None / malformed JSON
# ─────────────────────────────────────────────────────────────────────────────

def test_vision_parse_llm_returns_none(monkeypatch):
    """If vision_json returns None → vision_parse returns None gracefully."""
    monkeypatch.setattr("app.ocr.ai_provider.is_available", lambda: True)
    monkeypatch.setattr("app.ocr.ai_provider.vision_json", lambda **_kw: None)
    monkeypatch.setattr("app.ocr.run_ocr", lambda _b: "")
    monkeypatch.setattr("app.ocr._parse_boleta_from_text", lambda _t: ([], 0.0, 0.0, 0.0))
    monkeypatch.setattr("app.ocr._shrink_for_vision", lambda b: b)
    monkeypatch.setattr("app.ocr._detect_mime", lambda _b: "image/jpeg")

    result = vision_parse(b"\xff\xd8\xff")
    assert result is None


def test_vision_parse_empty_transactions_returns_none(monkeypatch):
    """LLM returns empty transactions list → vision_parse returns None."""
    llm = json.dumps({"transactions": [], "currency": "CLP"})
    _patch_vision(monkeypatch, llm)

    result = vision_parse(b"\xff\xd8\xff")
    assert result is None, f"Empty transactions should return None, got {result}"
