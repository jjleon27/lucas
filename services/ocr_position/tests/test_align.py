"""Tests rápidos (sin OCR) de la alineación ítems ↔ filas.

    cd services/ocr_position && python -m pytest -q tests/test_align.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import ItemPosition, Segment, _align, _clamp_adjacent_overlap, _rows, _script_filter  # noqa: E402


def rows(*texts):
    return [{"text": t} for t in texts]


def test_one_bad_item_does_not_drag_the_rest():
    # Con el emparejamiento codicioso, 'Cerveza' saltaba al pie 'TOTAL CERVEZA'
    # y todos los siguientes quedaban sin fila.
    r = rows("BAR X", "1 CERVEZA SIN ALCOHOL 2.500", "1 PAPAS 3.000", "1 BEBIDA 1.500", "TOTAL CERVEZA 7.000")
    assert _align(["Cerveza sin alcohol", "Papas", "Bebida"], r) == [1, 2, 3]


def test_accents_and_numbers_do_not_dilute():
    r = rows("MESA 4", "1 TE 1.500", "2 CAFE 3.000")
    assert _align(["Té", "Café"], r) == [1, 2]


def test_repeated_names_take_successive_rows():
    r = rows("PROMO PISCO 6.400", "PROMO PISCO 6.400", "PROMO PISCO 6.400", "TOTAL 19.200")
    assert _align(["Promo Pisco"] * 3, r) == [0, 1, 2]


def test_price_anchor_when_name_is_unreadable():
    r = rows("お会計票", "xq 820", "zz 710", "合計 1530")
    assert _align(["katsudon", "ramen"], r, prices=[820, 710]) == [1, 2]
    assert _align(["katsudon", "ramen"], r) == [None, None]


def test_script_filter_drops_stray_letters_of_other_scripts():
    keep = _script_filter(["Leche Entera"])
    assert keep("LECHE 中ENTERA 1.960") == "LECHE ENTERA 1.960"
    assert _script_filter(["カツ丼"])("カツ丼 820") == "カツ丼 820"


def test_rows_follow_tilt():
    # Dos renglones inclinados ~10°: nombre a la izquierda, precio más abajo a la derecha.
    def box(text, x, y, w=100, h=10, slope=0.18):
        return {"text": text, "quad": [(x, y), (x + w, y + w * slope), (x + w, y + w * slope + h), (x, y + h)]}
    boxes = [box("COCA COLA", 0, 0), box("1.500", 300, 54), box("PAPAS FRITAS", 0, 15), box("3.000", 300, 69)]
    assert [r["text"] for r in _rows(boxes, 400, 200)] == ["COCA COLA 1.500", "PAPAS FRITAS 3.000"]


def test_clamp_only_same_column():
    a = ItemPosition(name="a", position_y=10, bbox_y0=8, bbox_y1=14,
                     segments=[Segment(x0=0, x1=40, y0=8, y1=12), Segment(x0=70, x1=90, y0=10, y1=14)])
    b = ItemPosition(name="b", position_y=13, bbox_y0=11, bbox_y1=16,
                     segments=[Segment(x0=0, x1=40, y0=11, y1=15), Segment(x0=70, x1=90, y0=13, y1=16)])
    _clamp_adjacent_overlap([a, b])
    assert a.segments[0].y1 <= b.segments[0].y0  # misma columna: sin traslape
    assert a.segments[1].y1 <= b.segments[1].y0
    assert a.bbox_y1 == max(s.y1 for s in a.segments)


def test_short_name_does_not_match_header_by_scattered_letters():
    r = rows("SERVICIOS TURISTICOS", "1 SITIO 4.000-")
    assert _align(["Sitio"], r, prices=[4000]) == [1]
    assert _align(["Sitio"], rows("SERVICIOS TURISTICOS", "garabato")) == [None]


def test_weak_name_without_price_evidence_stays_null():
    # Se conoce el total (12.500) y ninguna fila lo trae: un parecido mediano no basta.
    r = rows("CAJA 3 MESA 4", "LOMO A LO POBRE 9.900")
    assert _align(["Lomo vetado"], r, prices=[12500]) == [None]
    assert _align(["Lomo a lo pobre"], r, prices=[12500]) == [1]


def test_signed_amounts_anchor_discounts():
    r = rows("LECHE 1.990", "DESCUENTO 1.017-", "PAN 990", "DESCUENTO -500")
    assert _align(["Leche", "Descuento leche", "Pan", "Descuento pan"], r,
                  prices=[1990, -1017, 990, -500]) == [0, 1, 2, 3]
