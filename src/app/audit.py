"""Pure audit-row and audit-INSERT builders for the download-hub app.

Every gated download writes exactly one row to ``main.default.download_audit`` as
the app service principal (LOCKED DECISION L2). This module is stdlib-only (no
SDK import): :func:`build_audit_row` assembles the logical row, and
:func:`build_audit_insert` returns a parameterized INSERT ``(sql, params)`` where
``params`` are plain ``{"name","value","type"}`` dicts. ``main.py`` (the sole I/O
boundary) maps those dicts to ``StatementParameterListItem`` — so this module is
importable in the pytest-only dev ``.venv``.

Every parameter value is a STRING (the Statement Execution API requires it), so
``row_count`` is ``str(...)`` and ``acknowledged`` is ``"true"``/``"false"``.
``event_ts`` is set server-side via SQL ``current_timestamp()`` (no bound param).
"""

from __future__ import annotations

import uuid


def build_audit_row(
    *,
    user_email: str,
    report_date: str,
    filter_summary: str,
    search_filter: str,
    row_count: int,
    export_format: str,
    justification: str,
    app_version: str,
    report_id: str,
    report_title: str,
    source_query: str = "",
    acknowledged: bool = True,
    audit_id: str | None = None,
) -> dict:
    """Assemble the logical audit row for one download.

    Args:
        user_email: The signed-in user's email (best-effort; ``""`` if absent).
        report_date: The exported report_date (``"%Y-%m-%d %H:%M:%S"``).
        filter_summary: The applied-filters summary (``"field=value; ..."``; ``""``
            when no filters are active). The column keeps its legacy name.
        search_filter: The search substring (``""`` when none).
        row_count: Number of rows in the exported file.
        export_format: ``"csv"`` or ``"xlsx"``.
        justification: The user's written justification (required, non-empty).
        app_version: The running app version string.
        report_id: The report registry key (e.g. ``daily_metrics``).
        report_title: The report's human-facing title.
        source_query: The report's configured ``source_query`` (the SQL that
            defined the exported data set). Recorded for audit transparency.
        acknowledged: Whether the data-handling notice was acknowledged
            (always ``True`` for a valid download).
        audit_id: Optional explicit audit id; defaults to a fresh ``uuid4``.

    Returns:
        A dict with all 13 logical audit fields (``event_ts`` is added by the
        SQL builder via ``current_timestamp()``).
    """
    return {
        "audit_id": audit_id or str(uuid.uuid4()),
        "user_email": user_email,
        "report_date": report_date,
        "filter_summary": filter_summary,
        "search_filter": search_filter or "",
        "row_count": int(row_count),
        "export_format": export_format,
        "justification": justification,
        "acknowledged": bool(acknowledged),
        "app_version": app_version,
        "report_id": report_id,
        "report_title": report_title,
        "source_query": source_query or "",
    }


def build_audit_insert(
    catalog: str, schema: str, row: dict
) -> tuple[str, list[dict]]:
    """Build the parameterized INSERT for one audit row.

    Args:
        catalog: Unity Catalog catalog name (e.g. ``main``).
        schema: Schema name (e.g. ``default``).
        row: A row dict from :func:`build_audit_row`.

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

    fqn = f"{catalog}.{schema}.download_audit"
    # report_date is a retired compatibility field. New exports bind an empty
    # string so the existing audit schema stores NULL.
    sql = (
        f"INSERT INTO {fqn} "
        "(audit_id, event_ts, user_email, report_date, filter_summary, search_filter, "
        "row_count, export_format, justification, acknowledged, app_version, "
        "report_id, report_title, source_query) "
        "VALUES (:audit_id, current_timestamp(), :user_email, "
        "CAST(NULLIF(:report_date, '') AS TIMESTAMP), "
        ":filter_summary, :search_filter, :row_count, :export_format, :justification, "
        ":acknowledged, :app_version, :report_id, :report_title, :source_query)"
    )
    params = [
        {"name": "audit_id", "value": row["audit_id"], "type": "STRING"},
        {"name": "user_email", "value": row["user_email"], "type": "STRING"},
        {"name": "report_date", "value": row["report_date"], "type": "STRING"},
        {"name": "filter_summary", "value": row["filter_summary"], "type": "STRING"},
        {"name": "search_filter", "value": row["search_filter"], "type": "STRING"},
        {"name": "row_count", "value": str(row["row_count"]), "type": "BIGINT"},
        {"name": "export_format", "value": row["export_format"], "type": "STRING"},
        {"name": "justification", "value": row["justification"], "type": "STRING"},
        {
            "name": "acknowledged",
            "value": str(bool(row["acknowledged"])).lower(),
            "type": "BOOLEAN",
        },
        {"name": "app_version", "value": row["app_version"], "type": "STRING"},
        {"name": "report_id", "value": row["report_id"], "type": "STRING"},
        {"name": "report_title", "value": row["report_title"], "type": "STRING"},
        {"name": "source_query", "value": row.get("source_query", ""), "type": "STRING"},
    ]
    return sql, params
