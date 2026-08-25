"""Unit tests for the pure generic renderer in ``app.render``.

No fastapi, no databricks.sdk, no pyspark, no network — runs offline
(LOCKED DECISION L3).
"""

from app.render import (
    align_class,
    cell_text,
    display_rows,
    format_float,
    haystack_for,
    header_cells,
    is_numeric_format,
    pct_class,
)
from app.reports import ColumnSpec
from app.shaping import EM_DASH


# --- cell_text -----------------------------------------------------------


def test_cell_text_int_thousands():
    """int format renders thousands separators."""
    assert cell_text(1234567, "int") == "1,234,567"


def test_cell_text_int_from_string():
    """int format coerces the string the SQL API returns."""
    assert cell_text("1234567", "int") == "1,234,567"


def test_cell_text_pct_signed():
    """pct format renders a signed one-decimal percentage."""
    assert cell_text(20.0, "pct") == "+20.0%"
    assert cell_text(-5.8, "pct") == "-5.8%"


def test_cell_text_pct_none_em_dash():
    """A NULL pct renders the em dash."""
    assert cell_text(None, "pct") == EM_DASH


def test_cell_text_int_none_em_dash():
    """A NULL int renders the em dash."""
    assert cell_text(None, "int") == EM_DASH


def test_cell_text_text_passthrough():
    """text format returns the value as a string."""
    assert cell_text("Balance Due", "text") == "Balance Due"


def test_cell_text_text_none_is_empty():
    """A NULL text value renders as an empty string."""
    assert cell_text(None, "text") == ""


def test_cell_text_unknown_format_treated_as_text():
    """An unknown format falls back to string/empty behavior."""
    assert cell_text("x", "currency") == "x"
    assert cell_text(None, "currency") == ""


# --- float / double ------------------------------------------------------


def test_cell_text_float_thousands_two_decimals():
    """float format renders thousands separators + 2 decimals."""
    assert cell_text(1234567.5, "float") == "1,234,567.50"


def test_cell_text_float_from_string():
    """float format coerces the string the SQL API returns."""
    assert cell_text("1234.5", "float") == "1,234.50"


def test_cell_text_double_treated_as_float():
    """double is an alias for float."""
    assert cell_text(1234.5, "double") == "1,234.50"


def test_cell_text_float_none_em_dash():
    """A NULL float renders the em dash."""
    assert cell_text(None, "float") == EM_DASH
    assert cell_text("", "double") == EM_DASH


def test_format_float_helper_decimals():
    """format_float honors a custom decimal count and rounds."""
    assert format_float(1234.567, 3) == "1,234.567"
    assert format_float(1234.5) == "1,234.50"
    assert format_float(None) == EM_DASH


def test_align_class_float_double_right():
    """float/double columns are right-aligned like int/pct."""
    assert align_class("float") == "text-right"
    assert align_class("double") == "text-right"


def test_display_rows_float_cell_right_aligned():
    """float cells carry the text-right alignment class and formatted text."""
    cols = [ColumnSpec("amount", "Amount", "float")]
    cells = display_rows(cols, [{"amount": 9999.5}])[0]
    assert cells[0]["css"] == "text-right"
    assert cells[0]["text"] == "9,999.50"


# --- pct_class -----------------------------------------------------------


def test_pct_class_positive():
    assert pct_class(5.0) == "app-pct--pos"


def test_pct_class_negative():
    assert pct_class(-5.0) == "app-pct--neg"


def test_pct_class_none_muted():
    assert pct_class(None) == "app-pct--muted"


def test_pct_class_zero_is_neutral():
    assert pct_class(0.0) == ""


# --- align_class ---------------------------------------------------------


def test_align_class_numeric_right():
    assert align_class("int") == "text-right"
    assert align_class("pct") == "text-right"


def test_align_class_text_empty():
    assert align_class("text") == ""
    assert align_class("currency") == ""


# --- is_numeric_format ---------------------------------------------------


def test_is_numeric_format_includes_float_double():
    """float/double are numeric so click-to-sort compares them by value, not text."""
    assert is_numeric_format("int") is True
    assert is_numeric_format("pct") is True
    assert is_numeric_format("float") is True
    assert is_numeric_format("double") is True


def test_is_numeric_format_text_false():
    assert is_numeric_format("text") is False
    assert is_numeric_format("") is False
    assert is_numeric_format("currency") is False


def test_is_numeric_format_matches_align_class():
    """Alignment and sort share one source of truth — they must never diverge."""
    for fmt in ("int", "pct", "float", "double", "text", "currency", ""):
        assert (align_class(fmt) == "text-right") == is_numeric_format(fmt)


# --- header_cells --------------------------------------------------------


def test_header_cells_labels_and_align():
    """Header cells carry each column's label and alignment."""
    cols = [
        ColumnSpec("metric_name", "Metric", "text"),
        ColumnSpec("value_cy", "2026", "int"),
        ColumnSpec("pct_change", "% Change", "pct"),
    ]
    headers = header_cells(cols)
    assert [h["label"] for h in headers] == ["Metric", "2026", "% Change"]
    assert [h["align"] for h in headers] == ["", "text-right", "text-right"]


# --- display_rows --------------------------------------------------------


def _seed_cols() -> list[ColumnSpec]:
    return [
        ColumnSpec("metric_name", "Metric", "text"),
        ColumnSpec("value_cy", "2026", "int"),
        ColumnSpec("value_py", "2025", "int"),
        ColumnSpec("pct_change", "% Change", "pct"),
    ]


def test_display_rows_cells_match_columnspec_order():
    """Cells are produced in ColumnSpec order with the first as the row header."""
    cols = _seed_cols()
    rows = [
        {"metric_name": "ERO", "value_cy": 100, "value_py": 80, "pct_change": 25.0}
    ]
    dr = display_rows(cols, rows)
    assert len(dr) == 1
    cells = dr[0]
    assert [c["text"] for c in cells] == ["ERO", "100", "80", "+25.0%"]


def test_display_rows_pct_css_by_sign():
    """A positive pct cell carries the pos class + right alignment."""
    cols = _seed_cols()
    rows = [{"metric_name": "X", "value_cy": 1, "value_py": 1, "pct_change": 25.0}]
    cells = display_rows(cols, rows)[0]
    assert "app-pct--pos" in cells[3]["css"]
    assert "text-right" in cells[3]["css"]


def test_display_rows_pct_negative_css():
    cols = _seed_cols()
    rows = [{"metric_name": "X", "value_cy": 1, "value_py": 1, "pct_change": -5.8}]
    cells = display_rows(cols, rows)[0]
    assert "app-pct--neg" in cells[3]["css"]


def test_display_rows_null_pct_em_dash_and_muted():
    """A NULL pct cell renders the em dash and the muted class."""
    cols = _seed_cols()
    rows = [{"metric_name": "X", "value_cy": 1, "value_py": 0, "pct_change": None}]
    cells = display_rows(cols, rows)[0]
    assert cells[3]["text"] == EM_DASH
    assert "app-pct--muted" in cells[3]["css"]


def test_display_rows_int_cell_right_aligned():
    """int cells carry the text-right alignment class."""
    cols = _seed_cols()
    rows = [{"metric_name": "X", "value_cy": 5, "value_py": 5, "pct_change": 0.0}]
    cells = display_rows(cols, rows)[0]
    assert cells[1]["css"] == "text-right"


# --- haystack_for --------------------------------------------------------


def test_haystack_for_joins_display_text():
    """The haystack joins the rendered display text of each column."""
    cols = _seed_cols()
    row = {"metric_name": "ERO", "value_cy": 100, "value_py": 80, "pct_change": 25.0}
    text = haystack_for(cols)(row)
    assert "ERO" in text
    assert "100" in text
    assert "+25.0%" in text
