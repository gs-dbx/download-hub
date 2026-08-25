"""Pure config model + generic parameterized query builders for report_config.

A stdlib-only, side-effect-free module (``re`` / ``json`` / ``dataclasses`` — NO
fastapi/databricks/pyspark import) so it is importable in the pytest-only dev
``.venv``. It is generic and config-driven, reading its shape from the
``report_config`` registry.

QUERY MODEL: a report is defined by a full SELECT (``source_query``), not a bare
table. The app wraps that query as a subquery and layers on the optional
date scope, filter equality, and ORDER BY:

    SELECT <cols> FROM ( <source_query> ) AS _q
    [WHERE <date_field> = :report_date]
    [AND <filter> = :flt_<filter> ...]
    [ORDER BY <order_by>]

Displayed columns DEFAULT to every column the query returns; they are narrowed
only when ``columns_json`` is configured (then only the configured columns show,
in configured order, with configured labels/formats). ``date_field`` and
``filters_json`` are both optional — absent ``date_field`` means no date scope
(all rows/dates show); absent ``filters_json`` means no filter dropdowns.

Injection rule: VALUES (the selected ``report_date`` and filter selections) are
ONLY ever bound as ``:named`` params via ``{"name","value","type"}`` dicts
(mirrors ``audit.py``) — never interpolated. IDENTIFIERS (column names, filter
fields, ``order_by``) come from admin-authored config and cannot be bound, so
every identifier is validated against a strict allowlist regex and interpolated
only if it passes. The ``source_query`` is admin-authored SQL: it is validated
to be a single statement (:func:`validate_query`) and wrapped as a subquery, but
its inner text is not otherwise parsed — treat write access to ``report_config``
as trusted (the app reads it as its own service principal).

The config parser (:func:`parse_report_config`) is tolerant of unknown ``format``
values (default ``"text"``) and raises ``ValueError`` only on malformed JSON;
identifier safety is enforced at query-build time, not at parse time.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass

# Extensible display formats. Unknown values are tolerated by the parser
# (default "text"); this set documents the currently understood formats.
# "float" is a numeric decimal format (right-aligned, decimals); "double" is
# accepted as an alias and normalized to "float".
VALID_FORMATS: frozenset[str] = frozenset({"int", "pct", "text", "float"})

# Numeric display formats — sorted by parsed value (not lexically) in the UI.
NUMERIC_FORMATS: frozenset[str] = frozenset({"int", "pct", "float"})

# Aggregation functions a display column may apply to a source column. Injected
# into the report SELECT with a GROUP BY over every non-aggregated column.
VALID_AGGS: frozenset[str] = frozenset({"sum", "min", "avg", "max", "first", "last"})


def normalize_format(raw: object) -> str:
    """Return a display format token, mapping ``"double"`` -> ``"float"``.

    Unknown values pass through (the renderer treats anything outside
    :data:`VALID_FORMATS` as ``"text"``); a missing/blank value defaults to
    ``"text"``.

    Args:
        raw: The raw ``format`` cell from ``columns_json`` (any type).

    Returns:
        The normalized format token.
    """
    f = (str(raw).strip().lower()) if raw is not None else ""
    if not f:
        return "text"
    return "float" if f == "double" else f


def normalize_agg(raw: object) -> str:
    """Return a validated aggregation token, or ``""`` for a plain column.

    Args:
        raw: The raw ``agg`` cell (any type; ``None``/blank -> plain column).

    Returns:
        One of :data:`VALID_AGGS`, or ``""`` (no aggregation).

    Raises:
        ValueError: If ``raw`` is a non-blank value that is not a known agg.
    """
    a = (str(raw).strip().lower()) if raw is not None else ""
    if not a:
        return ""
    if a not in VALID_AGGS:
        raise ValueError(
            f"invalid aggregation {raw!r} (must be one of {sorted(VALID_AGGS)})"
        )
    return a

# The two report kinds. A "query" report reads a full SELECT (``source_query``);
# a "volume" report browses/downloads files under a pinned UC Volume root
# (``volume_root``). Parsing is tolerant: a missing/blank ``kind`` means "query"
# so every pre-existing row parses unchanged.
VALID_KINDS: frozenset[str] = frozenset({"query", "volume"})

# A bare SQL identifier: leading letter/underscore, then letters/digits/underscores.
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Subquery alias for the wrapped source_query. Bare identifier; never user-set.
_SUBQUERY_ALIAS = "_q"


@dataclass(frozen=True)
class ColumnSpec:
    """A single display column in a report.

    Attributes:
        name: The column's OUTPUT name — the key in each snapshot row and the
            click-to-sort key (a bare SQL identifier). For a plain column this is
            also the source column; for an aggregated column it is the SELECT
            alias (``AGG(source) AS name``), so all downstream rendering /
            filtering / sorting is unchanged.
        label: The human-facing column header.
        format: Display format hint; one of ``VALID_FORMATS`` (extensible;
            unknown values are tolerated and treated as ``"text"``).
        agg: Optional aggregation applied to ``source`` (one of ``VALID_AGGS``);
            ``""`` (default) means a plain, non-aggregated column.
        source: The column the aggregation reads FROM (a bare SQL identifier).
            Only meaningful when ``agg`` is set; defaults to ``name`` for a plain
            column.
    """

    name: str
    label: str
    format: str = "text"
    agg: str = ""
    source: str = ""


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
        kind: The report kind — ``"query"`` (default) reads ``source_query``;
            ``"volume"`` browses/downloads files under ``volume_root``. Shared
            view/download gating (``view_key`` / ``download_group``) applies to
            both kinds.
        volume_root: For a ``"volume"`` report, the single pinned UC Volume root
            path (``/Volumes/<catalog>/<schema>/<volume>[/subpath]``) users may
            browse below; empty for a ``"query"`` report.
        source_query: The full SELECT a ``"query"`` report reads. Wrapped as a
            subquery by the query builders; validated as a single statement.
            Empty for a ``"volume"`` report.
        date_field: Optional date column to scope by (``None`` = no date scope,
            so all rows/dates show). Must exist in the query result when set.
        columns: Configured display columns. EMPTY means "show every column the
            query returns" (defaults resolved at read time by
            :func:`resolve_columns`).
        filters: Ordered filter dropdowns (may be empty).
        order_by: Optional ORDER BY column name (``None`` for no ordering).
        display_order: Sort order among enabled reports within a view.
        enabled: Whether the report is active.
        download_group: Optional explicit per-report download group. ``None``
            means "derive from ``view_key`` by naming convention" (see
            ``auth.effective_download_group``).
        view_key: The view this report belongs to (also the Databricks group that
            grants view access). ``None`` uses the default view.
        updated_by: Email of the admin who last wrote the row (bookkeeping).
    """

    report_id: str
    title: str
    source_query: str
    date_field: str | None
    columns: list[ColumnSpec]
    filters: list[FilterSpec]
    order_by: str | None
    display_order: int
    enabled: bool
    download_group: str | None = None
    view_key: str | None = None
    updated_by: str | None = None
    kind: str = "query"
    volume_root: str = ""


def normalize_kind(raw: object) -> str:
    """Return a valid report kind, defaulting a missing/blank value to ``"query"``.

    Args:
        raw: The raw ``kind`` cell (any type; ``None``/blank tolerated).

    Returns:
        ``"query"`` or ``"volume"``.

    Raises:
        ValueError: If ``raw`` is a non-blank value that is not a known kind.
    """
    k = (str(raw).strip().lower()) if raw is not None else ""
    if not k:
        return "query"
    if k not in VALID_KINDS:
        raise ValueError(f"invalid report kind {raw!r} (must be one of {sorted(VALID_KINDS)})")
    return k


def validate_volume_root(root: str) -> str:
    """Validate a report's pinned UC Volume root and return it normalized.

    A volume report browses only at/under this single root, so it must be a
    Unity Catalog Volumes path — ``/Volumes/<catalog>/<schema>/<volume>`` with an
    optional deeper subpath. The catalog/schema/volume segments must be bare SQL
    identifiers; no segment may be ``..`` (path-escape). Deeper subpath segments
    (folder names) are returned as-is — runtime path-jailing (in ``volumes.py``)
    still re-anchors every browse to this root.

    Args:
        root: The candidate ``volume_root`` value.

    Returns:
        The normalized root (stripped, no trailing slash).

    Raises:
        ValueError: If ``root`` is empty, not under ``/Volumes/``, lacks the
            catalog/schema/volume segments, contains ``..``, or has a
            non-identifier catalog/schema/volume segment.
    """
    r = (root or "").strip().rstrip("/")
    if not r:
        raise ValueError("volume_root is required for a volume report")
    prefix = "/Volumes/"
    if not r.startswith(prefix):
        raise ValueError(f"volume_root must start with '/Volumes/' (got {root!r})")
    parts = [p for p in r[len(prefix):].split("/") if p]
    if ".." in parts:
        raise ValueError(f"volume_root must not contain '..' (got {root!r})")
    if len(parts) < 3:
        raise ValueError(
            f"volume_root must name catalog/schema/volume under /Volumes/ (got {root!r})"
        )
    for seg in parts[:3]:
        validate_identifier(seg)
    return r


def _parse_column_spec(c: dict) -> ColumnSpec:
    """Parse one ``columns_json`` object into a :class:`ColumnSpec`.

    Tolerant (like the rest of the parser): the aggregation token is validated
    (unknown -> ValueError) but identifier safety is deferred to build time. For
    an aggregated column, ``source`` is the column the function reads and ``name``
    is the OUTPUT alias; if ``name`` is omitted it is derived as
    ``f"{source}_{agg}"`` so the admin only has to pick a source + function.

    Args:
        c: A ``{"name","label","format","agg","source"}`` object (agg/source
            optional).

    Returns:
        The parsed :class:`ColumnSpec`.

    Raises:
        ValueError: If ``agg`` is a non-blank unknown value.
    """
    agg = normalize_agg(c.get("agg"))
    source = (c.get("source") or "").strip()
    name = (c.get("name") or "").strip()
    if agg:
        # Aggregated column: source is required conceptually; fall back to name
        # when only one of the two is given so the parser stays tolerant.
        source = source or name
        name = name or (f"{source}_{agg}" if source else "")
    else:
        source = source or name
    return ColumnSpec(
        name=name,
        label=c.get("label", name),
        format=normalize_format(c.get("format")),
        agg=agg,
        source=source,
    )


def parse_report_config(row: dict) -> ReportConfig:
    """Parse a ``report_config`` row dict into a :class:`ReportConfig`.

    ``columns_json`` and ``filters_json`` are JSON arrays of objects (both
    optional). Unknown ``format`` values on columns are tolerated (default
    ``"text"``); an empty/absent ``columns_json`` yields an empty ``columns``
    list, which the app treats as "show every column the query returns".
    Identifier / query safety is NOT enforced here — that happens at query-build
    time.

    A ``"volume"`` report (``kind == "volume"``) needs a valid ``volume_root``
    but no ``source_query`` / ``date_field`` / ``columns_json`` / ``filters_json``
    (all optional); a ``"query"`` report needs a non-empty ``source_query``. A
    missing/blank ``kind`` parses as ``"query"`` so pre-existing rows are
    unchanged.

    Args:
        row: A row dict with keys ``report_id``, ``title``, ``display_order``,
            ``enabled``; ``kind`` (optional, default ``"query"``); for a query
            report ``source_query`` (required) plus ``date_field`` /
            ``columns_json`` / ``filters_json`` / ``order_by`` (optional); for a
            volume report ``volume_root`` (required); and ``download_group`` /
            ``view_key`` / ``updated_by`` (optional, both kinds).

    Returns:
        The parsed :class:`ReportConfig`.

    Raises:
        ValueError: If ``kind`` is an unknown value; if ``columns_json`` or
            ``filters_json`` is malformed JSON; if a volume report has an invalid
            ``volume_root``; or if a query report has an empty ``source_query``.
    """
    try:
        raw_cols = json.loads(row.get("columns_json") or "[]")
        raw_filters = json.loads(row.get("filters_json") or "[]")
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"malformed report_config JSON for {row.get('report_id')!r}: {exc}"
        ) from exc

    columns = [_parse_column_spec(c) for c in raw_cols]
    filters = [FilterSpec(field=f["field"], label=f["label"]) for f in raw_filters]
    date_field = (row.get("date_field") or "").strip() or None

    kind = normalize_kind(row.get("kind"))
    source_query = row.get("source_query") or ""
    volume_root = (row.get("volume_root") or "").strip()
    if kind == "volume":
        volume_root = validate_volume_root(volume_root)  # normalized; raises if bad
    elif not source_query.strip():
        raise ValueError(
            f"query report {row.get('report_id')!r} requires a non-empty source_query"
        )

    return ReportConfig(
        report_id=row["report_id"],
        title=row["title"],
        source_query=source_query,
        date_field=date_field,
        columns=columns,
        filters=filters,
        order_by=row.get("order_by"),
        display_order=int(row["display_order"]),
        enabled=bool(row["enabled"]),
        download_group=row.get("download_group"),
        view_key=(row.get("view_key") or "").strip() or None,
        updated_by=row.get("updated_by"),
        kind=kind,
        volume_root=volume_root,
    )


def resolve_columns(
    configured: list[ColumnSpec], result_columns: list[str]
) -> list[ColumnSpec]:
    """Return the effective display columns for a query result.

    When ``configured`` is non-empty it wins verbatim (order, labels, formats);
    the display is narrowed to exactly those columns. When it is empty, every
    column the query returned becomes a text column labelled by its own name —
    so a report with no ``columns_json`` shows all fields by default.

    Args:
        configured: The report's configured columns (may be empty).
        result_columns: The column names the query actually returned.

    Returns:
        The effective ordered :class:`ColumnSpec` list.
    """
    if configured:
        return configured
    return [ColumnSpec(name=c, label=c, format="text") for c in result_columns]


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


def validate_query(query: str) -> str:
    """Return the source query as a single trimmed SELECT, or raise ValueError.

    The query is admin-authored SQL wrapped as a subquery; this guard keeps it a
    single statement (so it composes into ``FROM ( ... ) AS _q``) rather than
    parsing it. It strips surrounding whitespace and a single trailing
    semicolon, rejects an empty query, and rejects any remaining ``;`` (which
    would break out of the subquery / smuggle a second statement).

    Args:
        query: The candidate ``source_query`` SQL.

    Returns:
        The cleaned single-statement query (no trailing semicolon).

    Raises:
        ValueError: If the query is empty or contains multiple statements.
    """
    q = (query or "").strip().rstrip(";").strip()
    if not q:
        raise ValueError("source_query must be a non-empty SQL statement")
    if ";" in q:
        raise ValueError("source_query must be a single SQL statement (no ';')")
    return q


def _wrapped_source(source_query: str) -> str:
    """Return the validated source query wrapped as an aliased subquery.

    Args:
        source_query: The admin-authored SELECT.

    Returns:
        ``"( <validated query> ) AS _q"`` for use in a ``FROM`` clause.
    """
    return f"( {validate_query(source_query)} ) AS {_SUBQUERY_ALIAS}"


def split_columns(columns: list[ColumnSpec]) -> tuple[list[str], list[dict]]:
    """Split display columns into (plain select names, aggregate specs).

    Helper for the integrator: pass a report's :class:`ColumnSpec` list and get
    back the two arguments :func:`build_report_query` needs — the plain (non-agg)
    column names to select + GROUP BY, and the aggregate specs to inject.

    Args:
        columns: The report's ordered display columns.

    Returns:
        A tuple ``(plain_names, aggregates)`` where ``aggregates`` is a list of
        ``{"func","source","output"}`` dicts (``output`` is the column's ``name``,
        the snapshot key the aggregate value lands under).
    """
    plain: list[str] = []
    aggregates: list[dict] = []
    for c in columns:
        if c.agg:
            aggregates.append(
                {"func": c.agg, "source": c.source or c.name, "output": c.name}
            )
        else:
            plain.append(c.name)
    return plain, aggregates


def build_report_query(
    source_query: str,
    columns: list[str] | None = None,
    date_field: str | None = None,
    report_date: str | None = None,
    filters: dict[str, str] | None = None,
    order_by: str | None = None,
    aggregates: list[dict] | None = None,
) -> tuple[str, list[dict]]:
    """Build a parameterized report SELECT over the wrapped source query.

    Produces ``SELECT <cols> FROM ( <source_query> ) AS _q
    [WHERE <date_field> = :report_date] [AND <f> = :flt_<f> ...]
    [GROUP BY <plain cols>] [ORDER BY <order_by>]``. Every identifier is
    validated (allowlist regex) then interpolated; every VALUE is bound as a
    param (``report_date`` as ``TIMESTAMP``, filter values as ``STRING``).

    Aggregation: when ``aggregates`` is given, each spec adds
    ``FUNC(source) AS output`` to the SELECT and — because you cannot mix bare
    columns with an aggregate — a ``GROUP BY`` over EVERY plain ``columns`` entry
    is emitted (the join-safe rule: every non-aggregated selected column must be
    grouped). If ``columns`` is empty and ``aggregates`` is set, the result is a
    single aggregate row (no GROUP BY). ``first``/``last`` are order-sensitive:
    with no ``order_by`` on the report their result is not deterministic (the
    aggregate is still emitted; the caller chooses an ORDER BY to pin it).

    Args:
        source_query: The report's full SELECT (wrapped as a subquery).
        columns: The plain (non-aggregated) column names to select; also the
            GROUP BY set when ``aggregates`` is present. ``None``/empty with no
            aggregates selects ``*``.
        date_field: Optional date column to scope by (WHERE, before GROUP BY).
            When set, ``report_date`` must also be given.
        report_date: The bound ``report_date`` VALUE (never interpolated); used
            only when ``date_field`` is set.
        filters: Optional ``field -> selected value`` map; values are bound.
        order_by: Optional column name (or aggregate output alias) to order by.
        aggregates: Optional list of ``{"func","source","output"}`` dicts (see
            :func:`split_columns`).

    Returns:
        A tuple ``(sql, params)`` where ``params`` is a list of
        ``{"name","value","type"}`` dicts (every ``value`` a string).

    Raises:
        ValueError: If any identifier is invalid, an aggregate ``func`` is not in
            :data:`VALID_AGGS`, the source query is invalid, or ``date_field`` is
            given without ``report_date``.
    """
    source = _wrapped_source(source_query)
    plain = [validate_identifier(c) for c in (columns or [])]

    agg_exprs: list[str] = []
    for a in (aggregates or []):
        func = normalize_agg(a.get("func"))
        if not func:
            raise ValueError("an aggregate column requires a function")
        src = validate_identifier(a.get("source") or "")
        out = validate_identifier(a.get("output") or "")
        agg_exprs.append(f"{func.upper()}({src}) AS {out}")

    select_parts = plain + agg_exprs
    col_list = ", ".join(select_parts) if select_parts else "*"
    sql = f"SELECT {col_list} FROM {source}"
    params: list[dict] = []

    where: list[str] = []
    if date_field:
        if report_date is None:
            raise ValueError("report_date is required when date_field is set")
        df = validate_identifier(date_field)
        where.append(f"{df} = :report_date")
        params.append(
            {"name": "report_date", "value": report_date, "type": "TIMESTAMP"}
        )
    for field, value in (filters or {}).items():
        f = validate_identifier(field)
        where.append(f"{f} = :flt_{f}")
        params.append({"name": f"flt_{f}", "value": value, "type": "STRING"})
    if where:
        sql += " WHERE " + " AND ".join(where)

    # Aggregation requires grouping every non-aggregated selected column (never
    # mix a bare column with an aggregate); no plain columns => single-row agg.
    if agg_exprs and plain:
        sql += " GROUP BY " + ", ".join(plain)

    if order_by:
        sql += f" ORDER BY {validate_identifier(order_by)}"
    return sql, params


def build_report_dates_query(source_query: str, date_field: str) -> str:
    """Build the SQL that lists DISTINCT date values, newest first.

    Feeds the report-date selector. No bound params (identifiers only).

    Args:
        source_query: The report's full SELECT (wrapped as a subquery).
        date_field: The date column to list.

    Returns:
        A single SQL statement string.

    Raises:
        ValueError: If ``source_query`` or ``date_field`` is invalid.
    """
    source = _wrapped_source(source_query)
    df = validate_identifier(date_field)
    return f"SELECT DISTINCT {df} FROM {source} ORDER BY {df} DESC"


def build_distinct_values_query(
    source_query: str,
    field: str,
    date_field: str | None = None,
    report_date: str | None = None,
) -> tuple[str, list[dict]]:
    """Build the SQL for DISTINCT values of a filter field, optionally date-scoped.

    Feeds a filter dropdown. If both ``date_field`` and ``report_date`` are
    given, adds ``WHERE <date_field> = :report_date`` (the date VALUE is bound,
    never interpolated).

    Args:
        source_query: The report's full SELECT (wrapped as a subquery).
        field: The filter column to list distinct values of.
        date_field: Optional date column to scope by.
        report_date: Optional bound date VALUE (used only with ``date_field``).

    Returns:
        A tuple ``(sql, params)`` where ``params`` is empty unless date-scoped.

    Raises:
        ValueError: If any identifier or the source query is invalid.
    """
    source = _wrapped_source(source_query)
    fld = validate_identifier(field)
    sql = f"SELECT DISTINCT {fld} FROM {source}"
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

    Run as the app service principal. The identifiers here are fixed (the
    registry column names and table), so no per-field validation is needed
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
        "SELECT report_id, title, kind, source_query, volume_root, date_field, "
        "columns_json, filters_json, order_by, display_order, enabled, "
        "download_group, view_key, updated_by "
        f"FROM {catalog}.{schema}.report_config WHERE enabled = true ORDER BY display_order"
    )


# --- Views (the switcher registry) ---------------------------------------


@dataclass(frozen=True)
class ReportView:
    """A parsed ``report_view`` row — one entry in the view switcher.

    Attributes:
        view_key: Stable key AND the Databricks group that grants view access
            (a bare SQL identifier). Reports reference it via ``view_key``.
        title: The human-facing label shown in the view switcher pulldown.
        display_order: Sort order among views.
        enabled: Whether the view is active.
        updated_by: Email of the admin who last wrote the row (bookkeeping).
    """

    view_key: str
    title: str
    display_order: int
    enabled: bool
    updated_by: str | None = None


def parse_report_view(row: dict) -> ReportView:
    """Parse a ``report_view`` row dict into a :class:`ReportView`.

    Args:
        row: A row dict with keys ``view_key``, ``title``, ``display_order``,
            ``enabled``, and ``updated_by`` (optional).

    Returns:
        The parsed :class:`ReportView`.
    """
    return ReportView(
        view_key=row["view_key"],
        title=row["title"],
        display_order=int(row["display_order"]),
        enabled=bool(row["enabled"]),
        updated_by=row.get("updated_by"),
    )


def build_report_view_query(catalog: str, schema: str) -> str:
    """Build the fixed-identifier SELECT of the enabled view registry.

    Run as the app service principal (mirrors :func:`build_report_config_query`).

    Args:
        catalog: Unity Catalog catalog name.
        schema: Schema name.

    Returns:
        A SQL statement selecting enabled views ordered by ``display_order``.

    Raises:
        ValueError: If ``catalog`` or ``schema`` is empty.
    """
    if not catalog or not schema:
        raise ValueError("catalog and schema are required and must be non-empty")
    return (
        "SELECT view_key, title, display_order, enabled, updated_by "
        f"FROM {catalog}.{schema}.report_view WHERE enabled = true ORDER BY display_order"
    )


# --- Admin write builders (parameterized upserts) ------------------------
#
# Values are ALWAYS bound as :named params (mirrors audit.py); identifiers in
# the payload (report_id, view_key, date_field, order_by, column names, filter
# fields) are validated against the allowlist BEFORE the upsert is built, and
# the source_query is validated as a single statement. A bad value raises
# ValueError, which the admin route turns into an HTTP 400 — nothing unsafe ever
# reaches the warehouse.


def _validate_column_specs(columns_json: str) -> None:
    """Validate that every column name in ``columns_json`` is a bare identifier.

    Args:
        columns_json: The JSON array string of ``{"name","label","format"}``.

    Raises:
        ValueError: On malformed JSON or a non-identifier column name.
    """
    try:
        raw = json.loads(columns_json or "[]")
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed columns_json: {exc}") from exc
    for c in raw:
        agg = normalize_agg(c.get("agg"))  # raises on an unknown agg function
        validate_identifier(c["name"])
        if agg:
            # An aggregated column reads FROM `source`; require + validate it.
            source = (c.get("source") or "").strip()
            if not source:
                raise ValueError(
                    f"aggregated column {c['name']!r} requires a 'source' column"
                )
            validate_identifier(source)


def _validate_filter_specs(filters_json: str) -> None:
    """Validate that every filter field in ``filters_json`` is a bare identifier.

    Args:
        filters_json: The JSON array string of ``{"field","label"}``.

    Raises:
        ValueError: On malformed JSON or a non-identifier filter field.
    """
    try:
        raw = json.loads(filters_json or "[]")
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed filters_json: {exc}") from exc
    for f in raw:
        validate_identifier(f["field"])


def build_report_config_upsert(
    catalog: str, schema: str, row: dict
) -> tuple[str, list[dict]]:
    """Build a parameterized MERGE that inserts/updates one ``report_config`` row.

    Every identifier in ``row`` is validated (allowlist / single-statement query)
    before the SQL is built; every value is bound as a ``:named`` param. Optional
    fields (``date_field``, ``order_by``, ``download_group``) store ``NULL`` when
    blank (via ``NULLIF``). ``updated_at`` is stamped server-side.

    Args:
        catalog: Unity Catalog catalog name.
        schema: Schema name.
        row: A dict with keys ``report_id``, ``title``, ``kind`` (optional,
            default ``"query"``), ``source_query`` (query kind), ``volume_root``
            (volume kind), ``date_field``, ``columns_json``, ``filters_json``,
            ``order_by``, ``display_order``, ``enabled``, ``download_group``,
            ``view_key``, ``updated_by``. A volume-kind row skips the
            query/column/filter validation and stores its ``volume_root``.

    Returns:
        A tuple ``(sql, params)`` — ``params`` are ``{"name","value","type"}``
        dicts (every ``value`` a string).

    Raises:
        ValueError: If ``catalog``/``schema`` is empty or any identifier / query
            is invalid.
    """
    if not catalog or not schema:
        raise ValueError("catalog and schema are required and must be non-empty")
    report_id = validate_identifier(row["report_id"])
    view_key = validate_identifier(row["view_key"])
    kind = normalize_kind(row.get("kind"))
    if kind == "volume":
        # A volume report has no query/columns/filters/date/order to validate.
        volume_root = validate_volume_root(row.get("volume_root") or "")
    else:
        volume_root = ""
        validate_query(row["source_query"])
        if (row.get("date_field") or "").strip():
            validate_identifier(row["date_field"].strip())
        if (row.get("order_by") or "").strip():
            validate_identifier(row["order_by"].strip())
        _validate_column_specs(row.get("columns_json") or "[]")
        _validate_filter_specs(row.get("filters_json") or "[]")

    fqn = f"{catalog}.{schema}.report_config"
    set_cols = (
        "title=:title, kind=:kind, source_query=NULLIF(:source_query,''), "
        "volume_root=NULLIF(:volume_root,''), "
        "date_field=NULLIF(:date_field,''), columns_json=:columns_json, "
        "filters_json=:filters_json, order_by=NULLIF(:order_by,''), "
        "display_order=CAST(:display_order AS INT), "
        "enabled=CAST(:enabled AS BOOLEAN), "
        "download_group=NULLIF(:download_group,''), view_key=:view_key, "
        "updated_at=current_timestamp(), updated_by=:updated_by"
    )
    insert_cols = (
        "report_id, title, kind, source_query, volume_root, date_field, "
        "columns_json, filters_json, order_by, display_order, enabled, "
        "download_group, view_key, updated_at, updated_by"
    )
    insert_vals = (
        ":report_id, :title, :kind, NULLIF(:source_query,''), "
        "NULLIF(:volume_root,''), NULLIF(:date_field,''), :columns_json, "
        ":filters_json, NULLIF(:order_by,''), CAST(:display_order AS INT), "
        "CAST(:enabled AS BOOLEAN), NULLIF(:download_group,''), :view_key, "
        "current_timestamp(), :updated_by"
    )
    sql = (
        f"MERGE INTO {fqn} t USING (SELECT :report_id AS report_id) s "
        "ON t.report_id = s.report_id "
        f"WHEN MATCHED THEN UPDATE SET {set_cols} "
        f"WHEN NOT MATCHED THEN INSERT ({insert_cols}) VALUES ({insert_vals})"
    )
    params = [
        {"name": "report_id", "value": report_id, "type": "STRING"},
        {"name": "title", "value": str(row.get("title") or ""), "type": "STRING"},
        {"name": "kind", "value": kind, "type": "STRING"},
        {"name": "source_query", "value": str(row.get("source_query") or ""), "type": "STRING"},
        {"name": "volume_root", "value": volume_root, "type": "STRING"},
        {"name": "date_field", "value": str(row.get("date_field") or ""), "type": "STRING"},
        {"name": "columns_json", "value": str(row.get("columns_json") or "[]"), "type": "STRING"},
        {"name": "filters_json", "value": str(row.get("filters_json") or "[]"), "type": "STRING"},
        {"name": "order_by", "value": str(row.get("order_by") or ""), "type": "STRING"},
        {"name": "display_order", "value": str(int(row.get("display_order") or 1)), "type": "STRING"},
        {"name": "enabled", "value": str(bool(row.get("enabled", True))).lower(), "type": "STRING"},
        {"name": "download_group", "value": str(row.get("download_group") or ""), "type": "STRING"},
        {"name": "view_key", "value": view_key, "type": "STRING"},
        {"name": "updated_by", "value": str(row.get("updated_by") or ""), "type": "STRING"},
    ]
    return sql, params


def build_report_config_delete(
    catalog: str, schema: str, report_id: str
) -> tuple[str, list[dict]]:
    """Build a parameterized DELETE that removes one ``report_config`` row.

    The report_id is validated (bare identifier) and bound as a ``:named`` param;
    nothing is interpolated. Mirrors the upsert builders' ``(sql, params)`` shape.

    Args:
        catalog: Unity Catalog catalog name.
        schema: Schema name.
        report_id: The registry key of the report to delete.

    Returns:
        A tuple ``(sql, params)`` where ``params`` is a single
        ``{"name","value","type"}`` dict for ``report_id``.

    Raises:
        ValueError: If ``catalog``/``schema`` is empty or ``report_id`` is not a
            bare identifier.
    """
    if not catalog or not schema:
        raise ValueError("catalog and schema are required and must be non-empty")
    rid = validate_identifier(report_id)
    fqn = f"{catalog}.{schema}.report_config"
    sql = f"DELETE FROM {fqn} WHERE report_id = :report_id"
    params = [{"name": "report_id", "value": rid, "type": "STRING"}]
    return sql, params


def build_report_view_delete(
    catalog: str, schema: str, view_key: str
) -> tuple[str, list[dict]]:
    """Build a parameterized DELETE that removes one ``report_view`` row.

    The view_key is validated (bare identifier) and bound as a ``:named`` param;
    nothing is interpolated. Mirrors :func:`build_report_config_delete`.

    Args:
        catalog: Unity Catalog catalog name.
        schema: Schema name.
        view_key: The key of the view to delete.

    Returns:
        A tuple ``(sql, params)`` where ``params`` is a single
        ``{"name","value","type"}`` dict for ``view_key``.

    Raises:
        ValueError: If ``catalog``/``schema`` is empty or ``view_key`` is not a
            bare identifier.
    """
    if not catalog or not schema:
        raise ValueError("catalog and schema are required and must be non-empty")
    vk = validate_identifier(view_key)
    fqn = f"{catalog}.{schema}.report_view"
    sql = f"DELETE FROM {fqn} WHERE view_key = :view_key"
    params = [{"name": "view_key", "value": vk, "type": "STRING"}]
    return sql, params


def build_report_view_upsert(
    catalog: str, schema: str, row: dict
) -> tuple[str, list[dict]]:
    """Build a parameterized MERGE that inserts/updates one ``report_view`` row.

    Args:
        catalog: Unity Catalog catalog name.
        schema: Schema name.
        row: A dict with keys ``view_key``, ``title``, ``display_order``,
            ``enabled``, ``updated_by``.

    Returns:
        A tuple ``(sql, params)`` — ``params`` are ``{"name","value","type"}``
        dicts (every ``value`` a string).

    Raises:
        ValueError: If ``catalog``/``schema`` is empty or ``view_key`` is not a
            bare identifier.
    """
    if not catalog or not schema:
        raise ValueError("catalog and schema are required and must be non-empty")
    view_key = validate_identifier(row["view_key"])

    fqn = f"{catalog}.{schema}.report_view"
    set_cols = (
        "title=:title, display_order=CAST(:display_order AS INT), "
        "enabled=CAST(:enabled AS BOOLEAN), updated_at=current_timestamp(), "
        "updated_by=:updated_by"
    )
    sql = (
        f"MERGE INTO {fqn} t USING (SELECT :view_key AS view_key) s "
        "ON t.view_key = s.view_key "
        f"WHEN MATCHED THEN UPDATE SET {set_cols} "
        "WHEN NOT MATCHED THEN INSERT "
        "(view_key, title, display_order, enabled, updated_at, updated_by) "
        "VALUES (:view_key, :title, CAST(:display_order AS INT), "
        "CAST(:enabled AS BOOLEAN), current_timestamp(), :updated_by)"
    )
    params = [
        {"name": "view_key", "value": view_key, "type": "STRING"},
        {"name": "title", "value": str(row.get("title") or ""), "type": "STRING"},
        {"name": "display_order", "value": str(int(row.get("display_order") or 1)), "type": "STRING"},
        {"name": "enabled", "value": str(bool(row.get("enabled", True))).lower(), "type": "STRING"},
        {"name": "updated_by", "value": str(row.get("updated_by") or ""), "type": "STRING"},
    ]
    return sql, params


def build_preview_query(source_query: str, limit: int = 50) -> str:
    """Build a row-limited preview of a source query (admin query builder).

    Wraps the validated single-statement query as a subquery and applies a
    ``LIMIT`` so the admin can inspect the returned columns + a sample of rows
    before saving a report. The limit is a validated small int (never bound —
    it is interpolated after ``int()`` coercion + clamp).

    Args:
        source_query: The admin-entered SELECT.
        limit: Max rows to return (clamped to 1..1000).

    Returns:
        A single SQL statement string.

    Raises:
        ValueError: If ``source_query`` is empty or multi-statement.
    """
    n = max(1, min(int(limit), 1000))
    return f"SELECT * FROM {_wrapped_source(source_query)} LIMIT {n}"


# --- System config (key/value) + audit log ------------------------------


def build_app_config_query(catalog: str, schema: str) -> str:
    """Build the SELECT of the key/value app-config registry (SP read).

    Args:
        catalog: Unity Catalog catalog name.
        schema: Schema name.

    Returns:
        A SQL statement selecting all config key/value pairs.

    Raises:
        ValueError: If ``catalog`` or ``schema`` is empty.
    """
    if not catalog or not schema:
        raise ValueError("catalog and schema are required and must be non-empty")
    return f"SELECT config_key, config_value FROM {catalog}.{schema}.app_config"


def build_app_config_upsert(
    catalog: str, schema: str, key: str, value: str, updated_by: str
) -> tuple[str, list[dict]]:
    """Build a parameterized MERGE that sets one app-config key (SP write).

    Args:
        catalog: Unity Catalog catalog name.
        schema: Schema name.
        key: The config key (a bare identifier, e.g. ``download_disclaimer``).
        value: The config value (bound as a STRING param).
        updated_by: Email of the admin who set it.

    Returns:
        A tuple ``(sql, params)`` — ``params`` are ``{"name","value","type"}`` dicts.

    Raises:
        ValueError: If ``catalog``/``schema`` is empty or ``key`` is not a bare
            identifier.
    """
    if not catalog or not schema:
        raise ValueError("catalog and schema are required and must be non-empty")
    key = validate_identifier(key)
    fqn = f"{catalog}.{schema}.app_config"
    sql = (
        f"MERGE INTO {fqn} t USING (SELECT :config_key AS config_key) s "
        "ON t.config_key = s.config_key "
        "WHEN MATCHED THEN UPDATE SET config_value=:config_value, "
        "updated_at=current_timestamp(), updated_by=:updated_by "
        "WHEN NOT MATCHED THEN INSERT (config_key, config_value, updated_at, updated_by) "
        "VALUES (:config_key, :config_value, current_timestamp(), :updated_by)"
    )
    params = [
        {"name": "config_key", "value": key, "type": "STRING"},
        {"name": "config_value", "value": value, "type": "STRING"},
        {"name": "updated_by", "value": updated_by, "type": "STRING"},
    ]
    return sql, params


# The audit-log columns shown in the admin console + exported (fixed order).
AUDIT_LOG_COLUMNS: tuple[str, ...] = (
    "event_ts",
    "user_email",
    "report_id",
    "report_title",
    "report_date",
    "filter_summary",
    "search_filter",
    "row_count",
    "export_format",
    "justification",
    "app_version",
    "source_query",
)


def build_audit_log_query(catalog: str, schema: str, limit: int = 200) -> str:
    """Build the SELECT of recent audit rows, newest first (SP read).

    Args:
        catalog: Unity Catalog catalog name.
        schema: Schema name.
        limit: Max rows (clamped to 1..5000).

    Returns:
        A SQL statement selecting :data:`AUDIT_LOG_COLUMNS` ordered by
        ``event_ts`` descending.

    Raises:
        ValueError: If ``catalog`` or ``schema`` is empty.
    """
    if not catalog or not schema:
        raise ValueError("catalog and schema are required and must be non-empty")
    n = max(1, min(int(limit), 5000))
    cols = ", ".join(AUDIT_LOG_COLUMNS)
    return (
        f"SELECT {cols} FROM {catalog}.{schema}.download_audit "
        f"ORDER BY event_ts DESC LIMIT {n}"
    )


# --- Config audit (admin mutation log) -----------------------------------------

# The config-audit columns shown in the admin console (fixed order).
CONFIG_AUDIT_COLUMNS: tuple[str, ...] = (
    "event_ts",
    "actor_email",
    "entity_type",
    "entity_key",
    "action",
    "summary",
    "payload_json",
    "app_version",
)


def build_config_audit_row(
    actor_email: str,
    entity_type: str,
    entity_key: str,
    action: str,
    summary: str,
    payload_json: str,
    app_version: str,
    audit_id: str | None = None,
) -> dict:
    """Assemble the logical config-audit row for one admin mutation.

    Args:
        actor_email: The admin's email (best-effort).
        entity_type: The entity type (``report_config``, ``report_view``, or
            ``app_config``).
        entity_key: The entity identifier (report_id, view_key, or config_key).
        action: The action taken (``upsert`` for reports/views, ``set`` for config).
        summary: Short human label (e.g. report title, config key, or view title).
        payload_json: Compact JSON of the written values (full row minus updated_by
            for reports/views; ``{"key": "value"}`` for config).
        app_version: The running app version string.
        audit_id: Optional explicit audit id; defaults to a fresh ``uuid4``.

    Returns:
        A dict with all config-audit logical fields (``event_ts`` is added by the
        SQL builder via ``current_timestamp()``).
    """
    return {
        "audit_id": audit_id or str(uuid.uuid4()),
        "actor_email": actor_email,
        "entity_type": entity_type,
        "entity_key": entity_key,
        "action": action,
        "summary": summary,
        "payload_json": payload_json,
        "app_version": app_version,
    }


def build_config_audit_insert(
    catalog: str, schema: str, row: dict
) -> tuple[str, list[dict]]:
    """Build the parameterized INSERT for one config-audit row.

    Args:
        catalog: Unity Catalog catalog name (e.g. ``main``).
        schema: Schema name (e.g. ``default``).
        row: A row dict from :func:`build_config_audit_row`.

    Returns:
        A tuple ``(sql, params)`` where ``sql`` is a 3-level FQN INSERT using
        ``:named`` placeholders (``event_ts`` uses ``current_timestamp()`` with
        no bound param) and ``params`` is a list of ``{"name","value","type"}``
        dicts — every ``value`` a string. ``main.py`` maps these to
        ``StatementParameterListItem``.

    Raises:
        ValueError: If ``catalog`` or ``schema`` is empty.
    """
    if not catalog or not schema:
        raise ValueError("catalog and schema are required and must be non-empty")

    fqn = f"{catalog}.{schema}.config_audit"
    sql = (
        f"INSERT INTO {fqn} "
        "(audit_id, event_ts, actor_email, entity_type, entity_key, action, "
        "summary, payload_json, app_version) "
        "VALUES (:audit_id, current_timestamp(), :actor_email, :entity_type, "
        ":entity_key, :action, :summary, :payload_json, :app_version)"
    )
    params = [
        {"name": "audit_id", "value": row["audit_id"], "type": "STRING"},
        {"name": "actor_email", "value": row["actor_email"], "type": "STRING"},
        {"name": "entity_type", "value": row["entity_type"], "type": "STRING"},
        {"name": "entity_key", "value": row["entity_key"], "type": "STRING"},
        {"name": "action", "value": row["action"], "type": "STRING"},
        {"name": "summary", "value": row["summary"], "type": "STRING"},
        {"name": "payload_json", "value": row["payload_json"], "type": "STRING"},
        {"name": "app_version", "value": row["app_version"], "type": "STRING"},
    ]
    return sql, params


def build_config_audit_query(catalog: str, schema: str, limit: int = 200) -> str:
    """Build the SELECT of recent config-audit rows, newest first (SP read).

    Args:
        catalog: Unity Catalog catalog name.
        schema: Schema name.
        limit: Max rows (clamped to 1..5000).

    Returns:
        A SQL statement selecting :data:`CONFIG_AUDIT_COLUMNS` ordered by
        ``event_ts`` descending.

    Raises:
        ValueError: If ``catalog`` or ``schema`` is empty.
    """
    if not catalog or not schema:
        raise ValueError("catalog and schema are required and must be non-empty")
    n = max(1, min(int(limit), 5000))
    cols = ", ".join(CONFIG_AUDIT_COLUMNS)
    return (
        f"SELECT {cols} FROM {catalog}.{schema}.config_audit "
        f"ORDER BY event_ts DESC LIMIT {n}"
    )


def build_config_audit_analytics_query(
    catalog: str, schema: str, days: int = 30
) -> str:
    """Build the SELECT of config-audit aggregates over a date range (SP read).

    Returns a small aggregate: counts grouped by entity_type + action over the
    last N days, ordered by count descending. This feeds the Change Log analytics
    summary.

    Args:
        catalog: Unity Catalog catalog name.
        schema: Schema name.
        days: Number of days to look back (clamped to 1..365).

    Returns:
        A SQL statement selecting entity_type, action, count(*) as n, ordered
        by n descending.

    Raises:
        ValueError: If ``catalog`` or ``schema`` is empty.
    """
    if not catalog or not schema:
        raise ValueError("catalog and schema are required and must be non-empty")
    d = max(1, min(int(days), 365))
    fqn = f"{catalog}.{schema}.config_audit"
    return (
        f"SELECT entity_type, action, COUNT(*) AS n FROM {fqn} "
        f"WHERE event_ts >= CURRENT_TIMESTAMP() - INTERVAL {d} DAYS "
        "GROUP BY entity_type, action ORDER BY n DESC"
    )
