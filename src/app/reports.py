"""Pure config model + generic parameterized query builders for report_config.

This is the Milestone-2 foundation (Phase 6): a stdlib-only, side-effect-free
module (``re`` / ``json`` / ``dataclasses`` — NO fastapi/databricks/pyspark
import) so it is importable in the pytest-only dev ``.venv``. It is deliberately
separate from ``queries.py`` (which stays report-specific for Milestone 1); this
module is generic and config-driven, reading its shape from the
``main.default.report_config`` registry.

Injection rule (LOCKED DECISION L2): VALUES (the selected ``report_date`` and
filter selections) are ONLY ever bound as ``:named`` params via
``{"name","value","type"}`` dicts (mirrors ``audit.py``) — never interpolated.
IDENTIFIERS (column names, filter fields, ``order_by``, each dotted part of
``source_fqn``) come from admin-authored config and cannot be bound, so every
identifier is validated against a strict allowlist regex and interpolated only
if it passes; a bad identifier raises ``ValueError``.

The config parser (:func:`parse_report_config`) is tolerant of unknown ``format``
values (default ``"text"``) and raises ``ValueError`` only on malformed JSON;
identifier safety is enforced at query-build time, not at parse time.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

# Extensible display formats. Unknown values are tolerated by the parser
# (default "text"); this set documents the currently understood formats.
VALID_FORMATS: frozenset[str] = frozenset({"int", "pct", "text"})

# A bare SQL identifier: leading letter/underscore, then letters/digits/underscores.
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class ColumnSpec:
    """A single display column in a report.

    Attributes:
        name: The source column name (a bare SQL identifier).
        label: The human-facing column header.
        format: Display format hint; one of ``VALID_FORMATS`` (extensible;
            unknown values are tolerated and treated as ``"text"``).
    """

    name: str
    label: str
    format: str = "text"


@dataclass(frozen=True)
class FilterSpec:
    """A single filter dropdown in a report.

    Attributes:
        field: The source column name to filter on (a bare SQL identifier).
        label: The human-facing filter label.
    """

    field: str
    label: str


@dataclass(frozen=True)
class ReportConfig:
    """A parsed ``report_config`` row.

    Attributes:
        report_id: Stable registry key.
        title: Human-facing report title.
        source_fqn: The Unity Catalog table the report reads (1-3 dotted parts).
        date_field: The date column the report is scoped by.
        columns: Ordered display columns.
        filters: Ordered filter dropdowns (may be empty).
        order_by: Optional ORDER BY column name (``None`` for no ordering).
        display_order: Sort order among enabled reports.
        enabled: Whether the report is active.
        download_group: Optional per-report download group (RESERVED; ``None``
            uses the global group).
    """

    report_id: str
    title: str
    source_fqn: str
    date_field: str
    columns: list[ColumnSpec]
    filters: list[FilterSpec]
    order_by: str | None
    display_order: int
    enabled: bool
    download_group: str | None = None


def parse_report_config(row: dict) -> ReportConfig:
    """Parse a ``report_config`` row dict into a :class:`ReportConfig`.

    ``columns_json`` and ``filters_json`` are JSON arrays of objects. Unknown
    ``format`` values on columns are tolerated (default ``"text"``); identifier
    safety is NOT enforced here — that happens at query-build time.

    Args:
        row: A row dict with keys ``report_id``, ``title``, ``source_fqn``,
            ``date_field``, ``columns_json``, ``filters_json`` (optional),
            ``order_by`` (optional), ``display_order``, ``enabled``, and
            ``download_group`` (optional).

    Returns:
        The parsed :class:`ReportConfig`.

    Raises:
        ValueError: If ``columns_json`` or ``filters_json`` is malformed JSON
            (wraps :class:`json.JSONDecodeError` with the ``report_id``).
    """
    try:
        raw_cols = json.loads(row["columns_json"])
        raw_filters = json.loads(row.get("filters_json") or "[]")
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"malformed report_config JSON for {row.get('report_id')!r}: {exc}"
        ) from exc

    columns = [
        ColumnSpec(name=c["name"], label=c["label"], format=c.get("format", "text"))
        for c in raw_cols
    ]
    filters = [FilterSpec(field=f["field"], label=f["label"]) for f in raw_filters]
    return ReportConfig(
        report_id=row["report_id"],
        title=row["title"],
        source_fqn=row["source_fqn"],
        date_field=row["date_field"],
        columns=columns,
        filters=filters,
        order_by=row.get("order_by"),
        display_order=int(row["display_order"]),
        enabled=bool(row["enabled"]),
        download_group=row.get("download_group"),
    )


def validate_identifier(name: str) -> str:
    """Return ``name`` if it is a bare SQL identifier, else raise ValueError.

    Enforces the allowlist ``^[A-Za-z_][A-Za-z0-9_]*$`` on any identifier that
    will be interpolated into SQL (columns, filter fields, ``order_by``, each
    dotted part of a ``source_fqn``).

    Args:
        name: The candidate identifier.

    Returns:
        The validated ``name`` unchanged.

    Raises:
        ValueError: If ``name`` is empty or not a bare SQL identifier.
    """
    if not name or not _IDENT_RE.match(name):
        raise ValueError(f"invalid identifier {name!r}")
    return name


def validate_fqn(fqn: str) -> str:
    """Validate a dotted table name; each part must be a bare identifier.

    Accepts 1-3 dotted parts (a 3-level FQN is expected, e.g.
    ``main.default.daily_metrics``); validates each part with
    :func:`validate_identifier` and returns the re-joined FQN.

    Args:
        fqn: The candidate fully-qualified name.

    Returns:
        The validated FQN, re-joined from its validated parts.

    Raises:
        ValueError: If ``fqn`` does not have 1-3 parts, or any part is not a
            bare SQL identifier.
    """
    parts = fqn.split(".")
    if not 1 <= len(parts) <= 3:
        raise ValueError(f"invalid source_fqn {fqn!r}")
    return ".".join(validate_identifier(p) for p in parts)


def build_report_query(
    source_fqn: str,
    columns: list[str],
    date_field: str,
    report_date: str,
    filters: dict[str, str] | None = None,
    order_by: str | None = None,
) -> tuple[str, list[dict]]:
    """Build a parameterized report SELECT and its bound params.

    Produces ``SELECT <cols> FROM <fqn> WHERE <date_field> = :report_date
    [AND <f> = :flt_<f> ...] [ORDER BY <order_by>]``. Every identifier is
    validated (allowlist regex) then interpolated; every VALUE is bound as a
    param (``report_date`` as ``TIMESTAMP``, filter values as ``STRING``).

    Args:
        source_fqn: The table to read (1-3 dotted parts).
        columns: The column names to select (at least one).
        date_field: The date column to scope by.
        report_date: The bound ``report_date`` VALUE (never interpolated).
        filters: Optional ``field -> selected value`` map; values are bound.
        order_by: Optional column name to order by.

    Returns:
        A tuple ``(sql, params)`` where ``params`` is a list of
        ``{"name","value","type"}`` dicts (every ``value`` a string).

    Raises:
        ValueError: If any identifier is invalid, or ``columns`` is empty.
    """
    fqn = validate_fqn(source_fqn)
    date_field = validate_identifier(date_field)
    if not columns:
        raise ValueError("columns must be non-empty")
    col_list = ", ".join(validate_identifier(c) for c in columns)

    sql = f"SELECT {col_list} FROM {fqn} WHERE {date_field} = :report_date"
    params: list[dict] = [
        {"name": "report_date", "value": report_date, "type": "TIMESTAMP"}
    ]

    for field, value in (filters or {}).items():
        f = validate_identifier(field)
        sql += f" AND {f} = :flt_{f}"
        params.append({"name": f"flt_{f}", "value": value, "type": "STRING"})

    if order_by:
        sql += f" ORDER BY {validate_identifier(order_by)}"
    return sql, params


def build_report_dates_query(source_fqn: str, date_field: str) -> str:
    """Build the SQL that lists DISTINCT date values, newest first.

    Feeds the report-date selector. No bound params (identifiers only).

    Args:
        source_fqn: The table to read (1-3 dotted parts).
        date_field: The date column to list.

    Returns:
        A single SQL statement string.

    Raises:
        ValueError: If ``source_fqn`` or ``date_field`` is invalid.
    """
    fqn = validate_fqn(source_fqn)
    df = validate_identifier(date_field)
    return f"SELECT DISTINCT {df} FROM {fqn} ORDER BY {df} DESC"


def build_distinct_values_query(
    source_fqn: str,
    field: str,
    date_field: str | None = None,
    report_date: str | None = None,
) -> tuple[str, list[dict]]:
    """Build the SQL for DISTINCT values of a filter field, optionally date-scoped.

    Feeds a filter dropdown. If both ``date_field`` and ``report_date`` are
    given, adds ``WHERE <date_field> = :report_date`` (the date VALUE is bound,
    never interpolated).

    Args:
        source_fqn: The table to read (1-3 dotted parts).
        field: The filter column to list distinct values of.
        date_field: Optional date column to scope by.
        report_date: Optional bound date VALUE (used only with ``date_field``).

    Returns:
        A tuple ``(sql, params)`` where ``params`` is empty unless date-scoped.

    Raises:
        ValueError: If any identifier is invalid.
    """
    fqn = validate_fqn(source_fqn)
    fld = validate_identifier(field)
    sql = f"SELECT DISTINCT {fld} FROM {fqn}"
    params: list[dict] = []
    if date_field and report_date is not None:
        dfld = validate_identifier(date_field)
        sql += f" WHERE {dfld} = :report_date"
        params.append(
            {"name": "report_date", "value": report_date, "type": "TIMESTAMP"}
        )
    sql += f" ORDER BY {fld}"
    return sql, params


def build_report_config_query(catalog: str, schema: str) -> str:
    """Build the fixed-identifier SELECT of the enabled report registry.

    Run as the app service principal (Phase 7). The identifiers here are fixed
    (the registry column names and table), so no per-field validation is needed
    beyond the non-empty catalog/schema guard.

    Args:
        catalog: Unity Catalog catalog name (e.g. ``main``).
        schema: Schema name (e.g. ``default``).

    Returns:
        A single SQL statement string selecting enabled rows ordered by
        ``display_order``.

    Raises:
        ValueError: If ``catalog`` or ``schema`` is empty.
    """
    if not catalog or not schema:
        raise ValueError("catalog and schema are required and must be non-empty")
    return (
        "SELECT report_id, title, source_fqn, date_field, columns_json, filters_json, "
        "order_by, display_order, enabled, download_group "
        f"FROM {catalog}.{schema}.report_config WHERE enabled = true ORDER BY display_order"
    )
