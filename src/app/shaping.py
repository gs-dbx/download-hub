"""Pure count / percentage / date formatters for the report portal.

Spark-free, SDK-free, network-free — unit-testable offline. Reused by
``render.py`` (cell formatting), ``exports.py`` (CSV/XLSX cells), and ``main.py``
(``format_report_date``). The report-specific row shaping and metric-order
constant were retired in Phase 8 (LOCKED DECISION L6).
"""

from __future__ import annotations

import datetime
from typing import Any

# Rendered wherever a value is NULL/absent.
EM_DASH: str = "—"


def _to_int(value: Any) -> int | None:
    """Coerce a scalar (or the string a SQL API returns) to int, or None.

    Args:
        value: An int, numeric string, ``None``, or empty string.

    Returns:
        The integer value, or ``None`` for ``None``/empty input.
    """
    if value is None or value == "":
        return None
    return int(value)


def _to_float(value: Any) -> float | None:
    """Coerce a scalar (or the string a SQL API returns) to float, or None.

    Args:
        value: A float, numeric string, ``None``, or empty string.

    Returns:
        The float value, or ``None`` for ``None``/empty input.
    """
    if value is None or value == "":
        return None
    return float(value)


def format_pct(pct: float | None) -> str:
    """Format a percent change with sign and one decimal place.

    Args:
        pct: The percent change, or ``None`` (e.g. prior-year value was 0).

    Returns:
        ``"—"`` (em dash) when ``pct`` is ``None``; otherwise a signed,
        one-decimal percentage (e.g. ``"+20.0%"``, ``"-5.8%"``, ``"0.0%"``).
        Zero renders without a sign.
    """
    if pct is None:
        return EM_DASH
    if pct > 0:
        return f"+{pct:.1f}%"
    return f"{pct:.1f}%"


def format_count(n: int | None) -> str:
    """Format an integer count with thousands separators.

    Args:
        n: The count, or ``None``.

    Returns:
        ``"—"`` when ``n`` is ``None``; otherwise the value with commas
        (e.g. ``9827762`` -> ``"9,827,762"``).
    """
    if n is None:
        return EM_DASH
    return f"{n:,}"


def format_report_date(ts: datetime.datetime | str) -> str:
    """Normalize a report_date to the ``%Y-%m-%d %H:%M:%S`` display format.

    Accepts a ``datetime`` or the string the Statement Execution API returns
    (e.g. ``"2026-01-12 00:00:00"`` or an ISO string, possibly with a trailing
    ``Z``) and normalizes it (FR-4).

    Args:
        ts: A ``datetime`` or timestamp string.

    Returns:
        The formatted string ``"%Y-%m-%d %H:%M:%S"``. If a string cannot be
        parsed it is returned stripped, unchanged.

    Raises:
        TypeError: If ``ts`` is neither a ``datetime`` nor a ``str``.
    """
    if isinstance(ts, datetime.datetime):
        dt = ts
    elif isinstance(ts, str):
        s = ts.strip()
        if s.endswith("Z"):
            s = s[:-1]
        try:
            dt = datetime.datetime.fromisoformat(s)
        except ValueError:
            return s
    else:
        raise TypeError(f"unsupported report_date type: {type(ts).__name__}")
    return dt.strftime("%Y-%m-%d %H:%M:%S")
