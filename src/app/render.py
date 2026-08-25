"""Pure generic cell/formatting from a report's ``ColumnSpec`` (LOCKED DECISION L3).

Turns raw snapshot row dicts into pre-formatted display cells so the template
stays dumb. May import ``reports.ColumnSpec`` and reuse the ``shaping`` formatters
(``format_count`` / ``format_pct`` / ``_to_int`` / ``_to_float`` / ``EM_DASH``);
it has NO SDK/fastapi/network import, so it is unit-testable offline.

Formats:
  * ``int``            -> thousands-separated count (``format_count``)
  * ``float``/``double`` -> thousands-separated fixed-decimal number (``format_float``)
  * ``pct``            -> signed one-decimal percentage (``format_pct``), colored by sign
  * ``text`` / unknown -> the raw value as a string (``""`` for ``None``)
"""

from __future__ import annotations

# Dual-convention imports: the Apps runtime runs from ``src/app/`` with flat
# imports; the pytest suite imports this module as ``app.render`` with ``src`` on
# the path. Try the flat form first, fall back to the package form.
try:  # pragma: no cover - import shim
    from reports import ColumnSpec
    from shaping import EM_DASH, _to_float, _to_int, format_count, format_pct
except ImportError:  # pragma: no cover - import shim
    from app.reports import ColumnSpec
    from app.shaping import EM_DASH, _to_float, _to_int, format_count, format_pct

__all__ = [
    "cell_text",
    "pct_class",
    "align_class",
    "header_cells",
    "display_rows",
    "haystack_for",
    "format_float",
    "EM_DASH",
]

# Numeric formats that render right-aligned with tabular figures.
_NUMERIC_FORMATS = ("int", "pct", "float", "double")

# Default decimal places for the ``float``/``double`` display format.
_FLOAT_DECIMALS = 2


def format_float(x: float | None, decimals: int = _FLOAT_DECIMALS) -> str:
    """Format a float with thousands separators and fixed decimal places.

    Args:
        x: The float value, or ``None``.
        decimals: Number of decimal places to show (default 2).

    Returns:
        ``"—"`` when ``x`` is ``None``; otherwise the value with commas and
        ``decimals`` places (e.g. ``1234.5`` -> ``"1,234.50"``).
    """
    if x is None:
        return EM_DASH
    return f"{x:,.{decimals}f}"


def cell_text(value: object, fmt: str) -> str:
    """Format a raw cell value for display according to its column format.

    Args:
        value: The raw scalar the SQL API returned (``str``, number, or ``None``).
        fmt: The column format hint (``"int"``, ``"float"``/``"double"``, ``"pct"``,
            ``"text"``/unknown).

    Returns:
        ``format_count`` for ``int``, ``format_float`` for ``float``/``double``,
        ``format_pct`` for ``pct``; otherwise the value as a string (``""`` when
        ``None``).
    """
    if fmt == "int":
        return format_count(_to_int(value))
    if fmt in ("float", "double"):
        return format_float(_to_float(value))
    if fmt == "pct":
        return format_pct(_to_float(value))
    return "" if value is None else str(value)


def pct_class(value: object) -> str:
    """Return the sign-color CSS class for a percent-change value.

    Args:
        value: The raw percent value (``str``, number, or ``None``).

    Returns:
        ``"app-pct--muted"`` for ``None`` (em dash), ``"app-pct--pos"`` when
        positive, ``"app-pct--neg"`` when negative, ``""`` for exactly zero.
    """
    v = _to_float(value)
    if v is None:
        return "app-pct--muted"
    if v > 0:
        return "app-pct--pos"
    if v < 0:
        return "app-pct--neg"
    return ""


def align_class(fmt: str) -> str:
    """Return the alignment CSS class for a column format.

    Args:
        fmt: The column format hint.

    Returns:
        ``"text-right"`` for numeric formats (``int``/``float``/``double``/``pct``),
        else ``""``.
    """
    return "text-right" if fmt in _NUMERIC_FORMATS else ""


def header_cells(columns: list[ColumnSpec]) -> list[dict]:
    """Build the header-cell descriptors for a report's columns.

    Args:
        columns: The report's ordered display columns.

    Returns:
        A list of ``{"name", "label", "align"}`` dicts aligned to ``columns``
        order. ``name`` is the source column (used as the click-to-sort key).
    """
    return [
        {"name": c.name, "label": c.label, "align": align_class(c.format)}
        for c in columns
    ]


def display_rows(columns: list[ColumnSpec], rows: list[dict]) -> list[list[dict]]:
    """Build pre-rendered display cells for each row, aligned to ``columns``.

    The first cell of each row is the row header; ``pct`` cells carry the
    sign-color class in addition to their alignment class.

    Args:
        columns: The report's ordered display columns.
        rows: The snapshot rows (dicts keyed by column name).

    Returns:
        A list of rows, each a list of ``{"text", "css"}`` cell dicts in
        ``columns`` order.
    """
    out: list[list[dict]] = []
    for r in rows:
        cells: list[dict] = []
        for c in columns:
            css = align_class(c.format)
            if c.format == "pct":
                css = (css + " " + pct_class(r.get(c.name))).strip()
            cells.append({"text": cell_text(r.get(c.name), c.format), "css": css})
        out.append(cells)
    return out


def haystack_for(columns: list[ColumnSpec]):
    """Return a callable joining a row's rendered display text (for search).

    Args:
        columns: The report's display columns whose rendered text is searched.

    Returns:
        A callable ``(row) -> str`` joining the ``cell_text`` of each column.
    """
    return lambda r: " ".join(cell_text(r.get(c.name), c.format) for c in columns)
