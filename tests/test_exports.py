"""Unit tests for the pure, generic export builders in ``app.exports``.

No fastapi, no databricks.sdk, no network. The XLSX test is guarded by
``pytest.importorskip("openpyxl")`` since the dev ``.venv`` has no openpyxl
(LOCKED DECISION L4).
"""

import pytest

from app.exports import DEFAULT_DISCLAIMER, filename_for, to_csv_bytes
from app.reports import ColumnSpec

_COLUMNS = [
    ColumnSpec("metric_name", "Metric", "text"),
    ColumnSpec("value_cy", "2026", "int"),
    ColumnSpec("value_py", "2025", "int"),
    ColumnSpec("pct_change", "% Change", "pct"),
]

_ROWS = [
    {
        "metric_name": "Orders",
        "value_cy": 9827762,
        "value_py": 8100000,
        "pct_change": 21.3,
    },
    {
        "metric_name": "Revenue",
        "value_cy": 500,
        "value_py": None,
        "pct_change": None,
    },
]


def test_disclaimer_is_nontrivial_multiline():
    """DEFAULT_DISCLAIMER is a non-trivial multi-line constant (reused by the template)."""
    assert isinstance(DEFAULT_DISCLAIMER, str)
    assert len(DEFAULT_DISCLAIMER.splitlines()) >= 2
    assert len(DEFAULT_DISCLAIMER) > 80


def test_to_csv_bytes_has_disclaimer_header_and_rows():
    """CSV has leading '# ' disclaimer lines, then the label header, then rows."""
    out = to_csv_bytes(_COLUMNS, _ROWS, DEFAULT_DISCLAIMER)
    assert isinstance(out, bytes)
    text = out.decode("utf-8")
    lines = text.splitlines()
    # First disclaimer line is a leading '# '-prefixed row.
    assert lines[0].startswith("# ")
    # The label header row appears after the disclaimer block.
    assert "Metric,2026,2025,% Change" in text
    # A known data row is present with the formatted values.
    assert 'Orders,"9,827,762","8,100,000",+21.3%' in text


def test_to_csv_bytes_null_pct_renders_em_dash():
    """A NULL pct row renders as the em dash in the CSV; NULL int too."""
    text = to_csv_bytes(_COLUMNS, _ROWS, DEFAULT_DISCLAIMER).decode("utf-8")
    assert "Revenue,500,—,—" in text


def test_disclaimer_precedes_header_in_csv():
    """The disclaimer block rides ABOVE the label header."""
    text = to_csv_bytes(_COLUMNS, _ROWS, DEFAULT_DISCLAIMER).decode("utf-8")
    assert text.index("# ") < text.index("Metric,2026,2025,% Change")


@pytest.mark.parametrize(
    "report_id,date,fmt,expected",
    [
        ("daily_metrics", "2026-01-12 00:00:00", "csv", "daily_metrics_2026-01-12.csv"),
        ("daily_metrics", "2026-01-12 00:00:00", "xlsx", "daily_metrics_2026-01-12.xlsx"),
        ("sample_report", "2026-03-04", "csv", "sample_report_2026-03-04.csv"),
    ],
)
def test_filename_for_sanitizes_and_picks_extension(report_id, date, fmt, expected):
    """filename_for strips the time portion and picks the right extension."""
    assert filename_for(report_id, date, fmt) == expected


def test_filename_for_unknown_format_defaults_csv():
    """An unrecognized format falls back to the .csv extension."""
    assert filename_for("daily_metrics", "2026-01-12 00:00:00", "pdf").endswith(".csv")


def test_to_xlsx_bytes_returns_zip_bytes():
    """to_xlsx_bytes lazily imports openpyxl and returns a ZIP (b'PK...')."""
    pytest.importorskip("openpyxl")
    from app.exports import to_xlsx_bytes

    out = to_xlsx_bytes(_COLUMNS, _ROWS, DEFAULT_DISCLAIMER)
    assert isinstance(out, bytes)
    assert len(out) > 0
    assert out[:2] == b"PK"
