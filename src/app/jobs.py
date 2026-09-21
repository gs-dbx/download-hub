"""Pure builders + helpers for the asynchronous export-job pipeline.

Downloads are queued: ``POST /download`` writes an immutable audit row (as
today) and then a MUTABLE ``export_jobs`` row that tracks the background
generation of the file to the exports UC volume. This module is the pure,
stdlib-only counterpart to ``audit.py`` — it never imports the SDK, so it is
unit-testable in the pytest-only dev ``.venv``. ``main.py`` (the sole I/O
boundary) maps the ``{"name","value","type"}`` param dicts returned here to
``StatementParameterListItem`` and runs the statements as the app service
principal.

Design mirrors ``audit.py`` / ``build_config_audit_*`` in ``reports.py``:
:func:`build_export_job_row` assembles the logical row; the ``build_*`` functions
return parameterized ``(sql, params)`` with ``:named`` placeholders; timestamps
are set server-side via ``current_timestamp()`` (no bound param). Every bound
value is a STRING except ``row_count`` (BIGINT). ``main.py`` never trusts a value
from these rows as SQL.

Two tables, deliberately separate: ``download_audit`` stays append-only for
compliance; ``export_jobs`` is the mutable status store (one row per queued
export, referencing its audit row by ``audit_id``).
"""

from __future__ import annotations

import hashlib
import uuid

# ---- Status vocabulary ------------------------------------------------------

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_READY = "ready"
STATUS_FAILED = "failed"
STATUS_EXPIRED = "expired"

# The legal status set (a job's lifecycle). Anything else normalizes to queued.
JOB_STATUSES: frozenset[str] = frozenset(
    {STATUS_QUEUED, STATUS_RUNNING, STATUS_READY, STATUS_FAILED, STATUS_EXPIRED}
)
# Statuses that mean "still working" (drives page polling + restart reconcile).
_ACTIVE_STATUSES: frozenset[str] = frozenset({STATUS_QUEUED, STATUS_RUNNING})
# Statuses that mean "no more work will happen".
_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {STATUS_READY, STATUS_FAILED, STATUS_EXPIRED}
)

# Ordered columns for SELECTs (also the parse_export_job_row contract).
JOB_COLUMNS: tuple[str, ...] = (
    "job_id",
    "audit_id",
    "created_ts",
    "updated_ts",
    "user_email",
    "email_slug",
    "report_id",
    "report_title",
    "export_format",
    "row_count",
    "status",
    "retrieve_path",
    "message",
    "fingerprint",
)

# Human labels for each status (shown on the My downloads page).
_STATUS_LABELS: dict[str, str] = {
    STATUS_QUEUED: "Queued",
    STATUS_RUNNING: "Preparing…",
    STATUS_READY: "Ready",
    STATUS_FAILED: "Failed",
    STATUS_EXPIRED: "Expired",
}

_TABLE = "export_jobs"


def normalize_job_status(raw: object) -> str:
    """Return a legal job status, defaulting unknown/blank input to ``queued``.

    Args:
        raw: Any candidate status value.

    Returns:
        One of :data:`JOB_STATUSES`; :data:`STATUS_QUEUED` for anything else.
    """
    s = str(raw or "").strip().lower()
    return s if s in JOB_STATUSES else STATUS_QUEUED


def export_job_fingerprint(
    user_email: str,
    report_id: str,
    export_format: str,
    filter_summary: str,
    search: str,
) -> str:
    """Return a deterministic dedupe key for an export request.

    Two submissions with the same user, report, format, filters, and search
    produce the same fingerprint, so a double-submit can be collapsed onto one
    job. The written justification is intentionally EXCLUDED so re-wording it
    does not defeat dedupe.

    Args:
        user_email: The signed-in user's email/identity.
        report_id: The report registry key.
        export_format: ``"csv"`` or ``"xlsx"``.
        filter_summary: The stable applied-filters summary (``"field=value; ..."``).
        search: The free-text search string.

    Returns:
        A hex sha256 digest string.
    """
    raw = "\x1f".join(
        [
            user_email or "",
            report_id or "",
            (export_format or "").lower(),
            filter_summary or "",
            search or "",
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def export_retrieve_subpath(email_slug: str, job_id: str, filename: str) -> str:
    """Return the root-relative volume subpath for a job's generated file.

    Shape ``{email_slug}/{job_id}/{filename}`` — the ``email_slug`` prefix is the
    per-user ownership boundary (matching the ``/download/retrieve`` scope check),
    and the ``job_id`` directory prevents concurrent/repeat exports from
    colliding while preserving the clean filename.

    Args:
        email_slug: The owner's collision-resistant slug (from ``main._email_slug``).
        job_id: The export job id.
        filename: The clean export filename (e.g. ``daily_metrics.csv``).

    Returns:
        The ``{slug}/{job_id}/{filename}`` root-relative subpath.
    """
    return f"{email_slug}/{job_id}/{filename}"


def build_export_job_row(
    *,
    audit_id: str,
    user_email: str,
    email_slug: str,
    report_id: str,
    report_title: str,
    export_format: str,
    row_count: int,
    retrieve_path: str,
    fingerprint: str,
    status: str = STATUS_QUEUED,
    message: str = "",
    job_id: str | None = None,
) -> dict:
    """Assemble the logical ``export_jobs`` row for one queued export.

    Args:
        audit_id: The id of the immutable audit row written first (audit-first).
        user_email: The signed-in user's readable email.
        email_slug: The owner slug (ownership/scoping key; matches the volume
            subfolder and the retrieve scope check).
        report_id: The report registry key.
        report_title: The report's human-facing title.
        export_format: ``"csv"`` or ``"xlsx"``.
        row_count: The row count established at enqueue.
        retrieve_path: The root-relative volume subpath the file will be written
            to (see :func:`export_retrieve_subpath`).
        fingerprint: The dedupe key (see :func:`export_job_fingerprint`).
        status: Initial status (defaults to ``queued``; normalized).
        message: Optional info/error text (blank at enqueue).
        job_id: Optional explicit job id; defaults to a fresh ``uuid4``.

    Returns:
        A dict with all logical job fields (``created_ts``/``updated_ts`` are
        added by the SQL builder via ``current_timestamp()``).
    """
    return {
        "job_id": job_id or str(uuid.uuid4()),
        "audit_id": audit_id,
        "user_email": user_email or "",
        "email_slug": email_slug or "",
        "report_id": report_id,
        "report_title": report_title,
        "export_format": (export_format or "").lower(),
        "row_count": int(row_count),
        "status": normalize_job_status(status),
        "retrieve_path": retrieve_path or "",
        "message": message or "",
        "fingerprint": fingerprint or "",
    }


def build_export_job_insert(
    catalog: str, schema: str, row: dict
) -> tuple[str, list[dict]]:
    """Build the parameterized INSERT for one ``export_jobs`` row.

    Args:
        catalog: Unity Catalog catalog name.
        schema: Schema name.
        row: A row dict from :func:`build_export_job_row`.

    Returns:
        A tuple ``(sql, params)`` — a 3-level FQN INSERT with ``:named``
        placeholders (``created_ts``/``updated_ts`` use ``current_timestamp()``)
        and a list of ``{"name","value","type"}`` dicts.

    Raises:
        ValueError: If ``catalog`` or ``schema`` is empty.
    """
    if not catalog or not schema:
        raise ValueError("catalog and schema are required and must be non-empty")
    fqn = f"{catalog}.{schema}.{_TABLE}"
    sql = (
        f"INSERT INTO {fqn} "
        "(job_id, audit_id, created_ts, updated_ts, user_email, email_slug, "
        "report_id, report_title, export_format, row_count, status, retrieve_path, "
        "message, fingerprint) "
        "VALUES (:job_id, :audit_id, current_timestamp(), current_timestamp(), "
        ":user_email, :email_slug, :report_id, :report_title, :export_format, "
        ":row_count, :status, :retrieve_path, :message, :fingerprint)"
    )
    params = [
        {"name": "job_id", "value": row["job_id"], "type": "STRING"},
        {"name": "audit_id", "value": row["audit_id"], "type": "STRING"},
        {"name": "user_email", "value": row["user_email"], "type": "STRING"},
        {"name": "email_slug", "value": row["email_slug"], "type": "STRING"},
        {"name": "report_id", "value": row["report_id"], "type": "STRING"},
        {"name": "report_title", "value": row["report_title"], "type": "STRING"},
        {"name": "export_format", "value": row["export_format"], "type": "STRING"},
        {"name": "row_count", "value": str(row["row_count"]), "type": "BIGINT"},
        {"name": "status", "value": normalize_job_status(row["status"]), "type": "STRING"},
        {"name": "retrieve_path", "value": row["retrieve_path"], "type": "STRING"},
        {"name": "message", "value": row["message"], "type": "STRING"},
        {"name": "fingerprint", "value": row["fingerprint"], "type": "STRING"},
    ]
    return sql, params


def build_export_job_status_update(
    catalog: str,
    schema: str,
    *,
    job_id: str,
    status: str,
    message: str = "",
    retrieve_path: str | None = None,
) -> tuple[str, list[dict]]:
    """Build the parameterized UPDATE that transitions one job's status.

    Always sets ``status``, ``message``, and ``updated_ts=current_timestamp()``.
    ``retrieve_path`` is set only when provided (so a ``running`` transition does
    not blank it). ``job_id`` is the bound WHERE key.

    Args:
        catalog: Unity Catalog catalog name.
        schema: Schema name.
        job_id: The job to update.
        status: The new status (normalized).
        message: Info/error text (blank clears it).
        retrieve_path: When given, also set the file subpath.

    Returns:
        A tuple ``(sql, params)``.

    Raises:
        ValueError: If ``catalog`` or ``schema`` is empty.
    """
    if not catalog or not schema:
        raise ValueError("catalog and schema are required and must be non-empty")
    fqn = f"{catalog}.{schema}.{_TABLE}"
    sets = ["status = :status", "message = :message", "updated_ts = current_timestamp()"]
    params = [
        {"name": "status", "value": normalize_job_status(status), "type": "STRING"},
        {"name": "message", "value": message or "", "type": "STRING"},
        {"name": "job_id", "value": job_id, "type": "STRING"},
    ]
    if retrieve_path is not None:
        sets.insert(2, "retrieve_path = :retrieve_path")
        params.append(
            {"name": "retrieve_path", "value": retrieve_path, "type": "STRING"}
        )
    sql = f"UPDATE {fqn} SET {', '.join(sets)} WHERE job_id = :job_id"
    return sql, params


def build_export_jobs_query(
    catalog: str, schema: str, email_slug: str, limit: int = 50
) -> tuple[str, list[dict]]:
    """Build the SELECT of one user's export jobs, newest first (SP read).

    The ``email_slug`` is a BOUND parameter — it is the per-user scoping boundary
    for the My downloads page, so a user only ever sees their own jobs.

    Args:
        catalog: Unity Catalog catalog name.
        schema: Schema name.
        email_slug: The owner slug to scope to.
        limit: Max rows (clamped to 1..500).

    Returns:
        A tuple ``(sql, params)``.

    Raises:
        ValueError: If ``catalog`` or ``schema`` is empty.
    """
    if not catalog or not schema:
        raise ValueError("catalog and schema are required and must be non-empty")
    n = max(1, min(int(limit), 500))
    cols = ", ".join(JOB_COLUMNS)
    sql = (
        f"SELECT {cols} FROM {catalog}.{schema}.{_TABLE} "
        f"WHERE email_slug = :email_slug ORDER BY created_ts DESC LIMIT {n}"
    )
    return sql, [{"name": "email_slug", "value": email_slug or "", "type": "STRING"}]


def build_export_job_by_id_query(
    catalog: str, schema: str, job_id: str
) -> tuple[str, list[dict]]:
    """Build the single-row SELECT for one job by id (retrieve + reconcile).

    Args:
        catalog: Unity Catalog catalog name.
        schema: Schema name.
        job_id: The job to look up.

    Returns:
        A tuple ``(sql, params)``.

    Raises:
        ValueError: If ``catalog`` or ``schema`` is empty.
    """
    if not catalog or not schema:
        raise ValueError("catalog and schema are required and must be non-empty")
    cols = ", ".join(JOB_COLUMNS)
    sql = (
        f"SELECT {cols} FROM {catalog}.{schema}.{_TABLE} "
        "WHERE job_id = :job_id LIMIT 1"
    )
    return sql, [{"name": "job_id", "value": job_id or "", "type": "STRING"}]


def build_active_export_job_query(
    catalog: str, schema: str, email_slug: str, fingerprint: str
) -> tuple[str, list[dict]]:
    """Build the dedupe lookup for an in-flight job matching a fingerprint.

    Returns the most recent still-active (``queued``/``running``) job for this
    owner + fingerprint, so a repeat submit reuses it instead of enqueuing a
    duplicate.

    Args:
        catalog: Unity Catalog catalog name.
        schema: Schema name.
        email_slug: The owner slug.
        fingerprint: The dedupe key (see :func:`export_job_fingerprint`).

    Returns:
        A tuple ``(sql, params)``.

    Raises:
        ValueError: If ``catalog`` or ``schema`` is empty.
    """
    if not catalog or not schema:
        raise ValueError("catalog and schema are required and must be non-empty")
    cols = ", ".join(JOB_COLUMNS)
    sql = (
        f"SELECT {cols} FROM {catalog}.{schema}.{_TABLE} "
        "WHERE email_slug = :email_slug AND fingerprint = :fingerprint "
        f"AND status IN ('{STATUS_QUEUED}', '{STATUS_RUNNING}') "
        "ORDER BY created_ts DESC LIMIT 1"
    )
    params = [
        {"name": "email_slug", "value": email_slug or "", "type": "STRING"},
        {"name": "fingerprint", "value": fingerprint or "", "type": "STRING"},
    ]
    return sql, params


def build_export_jobs_reconcile_query(catalog: str, schema: str) -> str:
    """Build the SELECT of not-yet-finished jobs for startup reconciliation.

    Args:
        catalog: Unity Catalog catalog name.
        schema: Schema name.

    Returns:
        A SQL statement selecting ``job_id, retrieve_path, status`` for rows
        still ``queued`` or ``running`` (interrupted by a restart).

    Raises:
        ValueError: If ``catalog`` or ``schema`` is empty.
    """
    if not catalog or not schema:
        raise ValueError("catalog and schema are required and must be non-empty")
    return (
        f"SELECT job_id, retrieve_path, status FROM {catalog}.{schema}.{_TABLE} "
        f"WHERE status IN ('{STATUS_QUEUED}', '{STATUS_RUNNING}')"
    )


def build_export_jobs_expire_update(
    catalog: str, schema: str, ttl_hours: int = 24
) -> str:
    """Build the UPDATE that marks jobs older than the TTL as ``expired``.

    Run by the scheduled cleanup job after it prunes the volume files. Skips rows
    already ``expired``. The interval is a validated positive int interpolated
    directly (SQL ``INTERVAL`` does not take a bound parameter).

    Args:
        catalog: Unity Catalog catalog name.
        schema: Schema name.
        ttl_hours: Retention window in hours (clamped to 1..8760).

    Returns:
        A SQL UPDATE statement.

    Raises:
        ValueError: If ``catalog`` or ``schema`` is empty.
    """
    if not catalog or not schema:
        raise ValueError("catalog and schema are required and must be non-empty")
    n = max(1, min(int(ttl_hours), 8760))
    return (
        f"UPDATE {catalog}.{schema}.{_TABLE} "
        f"SET status = '{STATUS_EXPIRED}', updated_ts = current_timestamp() "
        f"WHERE status <> '{STATUS_EXPIRED}' "
        f"AND created_ts < current_timestamp() - INTERVAL {n} HOURS"
    )


def reconcile_job_status(status: str, file_exists: bool) -> tuple[str, str]:
    """Decide an interrupted job's post-restart status (pure).

    On restart, in-memory generation tasks are gone. A ``queued``/``running`` job
    is reconciled by checking whether its file already landed on the volume:
    present → it actually finished, mark ``ready``; absent → it was interrupted,
    mark ``failed`` with a re-request message. Terminal statuses are returned
    unchanged (no transition).

    Args:
        status: The job's persisted status.
        file_exists: Whether the job's file exists on the exports volume.

    Returns:
        ``(new_status, message)`` — ``message`` is non-empty only for the failed
        case. ``new_status == status`` means "no change".
    """
    s = normalize_job_status(status)
    if s not in _ACTIVE_STATUSES:
        return s, ""
    if file_exists:
        return STATUS_READY, ""
    return (
        STATUS_FAILED,
        "This download was interrupted by an app restart. Please request it again.",
    )


def parse_export_job_row(cols: list[str], values: list) -> dict:
    """Map a Statement Execution ``(columns, row)`` pair to a job dict.

    Args:
        cols: The column names returned by the query.
        values: The row's scalar values (aligned to ``cols``).

    Returns:
        A ``{column: value}`` dict (missing columns default to ``""``).
    """
    row = dict(zip(cols, values))
    return {c: row.get(c, "") for c in JOB_COLUMNS}


def _fmt_job_ts(value: object) -> str:
    """Format a job timestamp for display (``"YYYY-MM-DD HH:MM:SS"``; degrade-safe)."""
    s = str(value or "").strip()
    if not s:
        return ""
    s = s.replace("T", " ")
    # Trim fractional seconds / timezone suffix to the second.
    return s[:19]


def job_view_model(row: dict) -> dict:
    """Shape one job row for the template / status JSON (pure presentation).

    Keeps all presentation logic testable and out of ``main.py``/Jinja.

    Args:
        row: A parsed job dict (see :func:`parse_export_job_row`).

    Returns:
        A dict with the raw fields plus derived display fields: ``status_label``,
        ``is_ready``/``is_terminal``/``is_active`` flags, ``created_display``,
        ``row_count`` (int), and ``retrieve_href`` (only for ready jobs).
    """
    status = normalize_job_status(row.get("status"))
    is_ready = status == STATUS_READY
    try:
        row_count = int(row.get("row_count") or 0)
    except (TypeError, ValueError):
        row_count = 0
    job_id = str(row.get("job_id") or "")
    return {
        "job_id": job_id,
        "report_id": str(row.get("report_id") or ""),
        "report_title": str(row.get("report_title") or ""),
        "export_format": str(row.get("export_format") or ""),
        "row_count": row_count,
        "status": status,
        "status_label": _STATUS_LABELS.get(status, status.title()),
        "message": str(row.get("message") or ""),
        "created_display": _fmt_job_ts(row.get("created_ts")),
        "is_ready": is_ready,
        "is_active": status in _ACTIVE_STATUSES,
        "is_terminal": status in _TERMINAL_STATUSES,
        # Local retrieval link (air-gap safe); only meaningful once ready.
        "retrieve_href": f"/download/retrieve?job_id={job_id}" if is_ready else "",
    }


def _to_epoch_seconds(modified: object) -> float | None:
    """Coerce a Files-API ``modified`` value to epoch seconds (ms heuristic)."""
    if modified is None or modified == "":
        return None
    try:
        v = float(modified)
    except (TypeError, ValueError):
        return None
    # Values above ~1e11 are epoch milliseconds; scale to seconds.
    return v / 1000.0 if v > 1e11 else v


def select_expired_files(
    entries: list[dict], now_epoch: float, ttl_hours: int = 24
) -> list[str]:
    """Return the paths of files older than the TTL (pure; drives volume cleanup).

    Args:
        entries: File descriptors ``[{"path", "modified"}]`` where ``modified`` is
            epoch seconds or milliseconds (as the Files API returns).
        now_epoch: The current time in epoch seconds.
        ttl_hours: Retention window in hours (clamped to >= 1).

    Returns:
        The ``path`` of every entry strictly older than ``now - ttl_hours``
        (entries with an unparseable/absent ``modified`` are skipped — never
        deleted on a bad timestamp).
    """
    cutoff = float(now_epoch) - max(1, int(ttl_hours)) * 3600.0
    doomed: list[str] = []
    for e in entries:
        secs = _to_epoch_seconds(e.get("modified"))
        if secs is not None and secs < cutoff and e.get("path"):
            doomed.append(str(e["path"]))
    return doomed
