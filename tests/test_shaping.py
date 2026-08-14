"""Unit tests for the pure formatters in ``app.shaping``.

No fastapi, no databricks.sdk, no network. The row-shaping (rows_to_context /
METRIC_ORDER) was retired in Phase 8 (LOCKED DECISION L6); only the formatters
(``format_count`` / ``format_pct`` / ``format_report_date`` and the ``_to_*``
coercers, exercised indirectly) remain.
"""

import datetime

from app.shaping import format_count, format_pct, format_report_date


def test_format_pct_none_is_em_dash():
    """NULL pct_change renders as an em dash, never None/crash (LOCKED L4)."""
    assert format_pct(None) == "—"


def test_format_pct_signs_and_decimal():
    """Non-null pct renders with sign + one decimal; zero has no sign."""
    assert format_pct(20.0) == "+20.0%"
    assert format_pct(-5.8) == "-5.8%"
    assert format_pct(0.0) == "0.0%"


def test_format_count():
    """Counts use thousands separators; None -> em dash."""
    assert format_count(9827762) == "9,827,762"
    assert format_count(None) == "—"


def test_format_report_date_datetime():
    """A datetime is formatted as %Y-%m-%d %H:%M:%S."""
    assert format_report_date(datetime.datetime(2026, 1, 12)) == "2026-01-12 00:00:00"


def test_format_report_date_string_normalizes():
    """A timestamp string (with/without trailing Z) normalizes to the same format."""
    assert format_report_date("2026-01-12 00:00:00") == "2026-01-12 00:00:00"
    assert format_report_date("2026-01-12T00:00:00Z") == "2026-01-12 00:00:00"
