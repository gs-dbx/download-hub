"""FastAPI entry point for the config-driven multi-tab download portal.

This is the ONLY I/O boundary. It extracts the signed-in user's OBO token from
the ``X-Forwarded-Access-Token`` header (LOCKED DECISION L3), builds a fresh
``WorkspaceClient`` per request from that token, and runs each report's source
read AS THE USER on the bound SQL warehouse via the Statement Execution API. The
report registry (``main.default.report_config``) is read AS THE APP SERVICE
PRINCIPAL and TTL-cached in-process (LOCKED DECISION L5). Because the SDK is
fully synchronous, every SDK call inside an ``async def`` route is wrapped in
``asyncio.to_thread`` so it never blocks the event loop.

Per-user snapshots are cached in-process (LOCKED DECISION L2): a date-scoped read
of ``display ∪ filter`` columns is fetched once, then filter/search/pagination run
server-side over the cached rows (no re-query). The cache is keyed by
``(user_email, report_id, date)`` so it only ever holds a user's own OBO data.

All configuration comes from environment variables (no hardcoded host/token/
warehouse). ``DATABRICKS_HOST`` and the app service-principal credentials are
auto-injected by the Databricks Apps runtime.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config
from databricks.sdk.service.sql import StatementParameterListItem, StatementState
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from audit import build_audit_insert, build_audit_row
from auth import (
    effective_download_group,
    extract_user_email,
    extract_user_token,
    is_member,
)
from cache import (
    Snapshot,
    SnapshotCache,
    apply_filters,
    apply_search,
    distinct_values,
    filters_summary,
    make_key,
    paginate,
)
from config import (
    app_logo,
    app_name,
    app_org_name,
    downloads_enabled,
    resolve_disclaimer,
)
from exports import DEFAULT_DISCLAIMER, filename_for, to_csv_bytes, to_xlsx_bytes
from render import display_rows, haystack_for, header_cells
from reports import (
    ReportConfig,
    build_report_config_query,
    build_report_query,
    parse_report_config,
)
from reports import build_report_dates_query as build_report_dates_query_generic
from shaping import format_report_date

# App root — the Apps runtime runs from src/app/; resolve paths relative to this
# file so static/templates load regardless of the current working directory.
_BASE_DIR = Path(__file__).resolve().parent
_STATIC_DIR = _BASE_DIR / "static"
_TEMPLATES_DIR = _BASE_DIR / "templates"

# Brand config resolved once at import from the environment (generic defaults in
# config.py). Exposed as Jinja globals so every template — including error.html,
# which routes may render without a full context — can render the masthead.
_APP_NAME = app_name(os.environ.get("APP_NAME"))
_APP_LOGO = app_logo(os.environ.get("APP_LOGO"))
_APP_ORG_NAME = app_org_name(os.environ.get("APP_ORG_NAME"), _APP_NAME)
# Effective acknowledgement text: DOWNLOAD_DISCLAIMER env override, else default.
_DISCLAIMER = resolve_disclaimer(os.environ.get("DOWNLOAD_DISCLAIMER"), DEFAULT_DISCLAIMER)

app = FastAPI(title=_APP_NAME)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
templates.env.globals.update(
    app_name=_APP_NAME, app_logo=_APP_LOGO, app_org_name=_APP_ORG_NAME
)

# Per-user snapshot cache (LOCKED DECISION L2). Bounded LRU; refresh evicts. Each
# key is (user_email, report_id, date) so no cross-user data ever mixes.
_snapshot_cache = SnapshotCache(max_size=128)

# Report registry TTL cache (LOCKED DECISION L5): parsed ReportConfigs read as the
# app SP, refreshed at most every _REPORTS_TTL seconds so a MERGE'd row appears
# without a redeploy.
_reports_cache: tuple[float, list[ReportConfig]] | None = None
_REPORTS_TTL: float = 300.0

# Fragment page-size options; "all" -> None ("All", one page).
_PAGE_SIZES: dict[str, int | None] = {"25": 25, "50": 50, "100": 100, "all": None}
_DEFAULT_PAGE_SIZE: int = 25


def _env(name: str, default: str | None = None) -> str:
    """Read a required environment variable.

    Args:
        name: Environment variable name.
        default: Optional default; if ``None`` the variable is required.

    Returns:
        The variable's value.

    Raises:
        RuntimeError: If the variable is unset and no default was provided.
    """
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(f"required environment variable {name} is not set")
    return value


def _user_client(token: str) -> WorkspaceClient:
    """Build a fresh per-request WorkspaceClient authenticated as the user.

    Args:
        token: The user's OBO OAuth access token.

    Returns:
        A ``WorkspaceClient`` whose calls run as the signed-in user (OBO). Never
        cache this across requests — the token is short-lived.
    """
    # auth_type="pat" pins the SDK to the user's bearer token. Without it, the
    # Databricks Apps runtime's injected DATABRICKS_CLIENT_ID/SECRET (the app
    # service principal's OAuth creds) are auto-detected alongside the token, and
    # the SDK refuses to initialize ("more than one authorization method
    # configured: oauth and pat"). Pinning pat makes reads run AS THE USER (OBO).
    return WorkspaceClient(
        config=Config(
            host=os.environ["DATABRICKS_HOST"], token=token, auth_type="pat"
        )
    )


# Module-level lazy singleton for the app service-principal client (LOCKED
# DECISION L2). The SP OAuth creds are long-lived (unlike the per-request user
# token), so a single client is reused for the audit INSERT and registry read.
_sp_client: WorkspaceClient | None = None


def _app_sp_client() -> WorkspaceClient:
    """Return the WorkspaceClient authenticated as the app service principal.

    A plain ``WorkspaceClient()`` with NO explicit config and NO ``auth_type``
    auto-detects the Databricks Apps runtime's injected ``DATABRICKS_HOST`` +
    ``DATABRICKS_CLIENT_ID`` + ``DATABRICKS_CLIENT_SECRET`` and authenticates as
    the app SP (LOCKED DECISION L2 — contrast the user client, which passes an
    explicit token + ``auth_type="pat"``). Constructed once and cached.

    Returns:
        The shared service-principal ``WorkspaceClient``.
    """
    global _sp_client
    if _sp_client is None:
        _sp_client = WorkspaceClient()
    return _sp_client


async def _exec(
    client: WorkspaceClient,
    sql: str,
    parameters: list[StatementParameterListItem] | None = None,
) -> tuple[list[str], list[list]]:
    """Execute a SQL statement on the bound warehouse and return (columns, rows).

    Shared body for the OBO and SP execution helpers. Wraps the synchronous
    Statement Execution call in ``asyncio.to_thread`` so the event loop is never
    blocked (LOCKED DECISIONS L3/L4).

    Args:
        client: The authenticated ``WorkspaceClient`` (user OBO or app SP).
        sql: The SQL statement to run.
        parameters: Optional named-parameter bindings for placeholders in ``sql``.

    Returns:
        A tuple of (column names, row data). Empty rows -> empty list.

    Raises:
        RuntimeError: If the statement does not reach the SUCCEEDED state.
    """
    resp = await asyncio.to_thread(
        client.statement_execution.execute_statement,
        warehouse_id=_env("DATABRICKS_WAREHOUSE_ID"),
        statement=sql,
        parameters=parameters,
        wait_timeout="30s",
    )
    if resp.status is None or resp.status.state != StatementState.SUCCEEDED:
        detail = ""
        if resp.status is not None and resp.status.error is not None:
            detail = f": {resp.status.error.message}"
        state = resp.status.state if resp.status else "UNKNOWN"
        raise RuntimeError(f"statement did not succeed (state={state}){detail}")

    columns: list[str] = []
    if resp.manifest is not None and resp.manifest.schema is not None:
        columns = [c.name for c in (resp.manifest.schema.columns or [])]
    data: list[list] = []
    if resp.result is not None and resp.result.data_array is not None:
        data = resp.result.data_array
    return columns, data


async def _run_sql(
    token: str,
    sql: str,
    parameters: list[StatementParameterListItem] | None = None,
) -> tuple[list[str], list[list]]:
    """Execute a SQL statement AS THE USER (OBO) and return (columns, rows).

    Args:
        token: The user's OBO access token.
        sql: The SQL statement to run.
        parameters: Optional named-parameter bindings.

    Returns:
        A tuple of (column names, row data).

    Raises:
        RuntimeError: If the statement does not reach the SUCCEEDED state.
    """
    return await _exec(_user_client(token), sql, parameters)


async def _run_sql_sp_query(
    sql: str,
    parameters: list[StatementParameterListItem] | None = None,
) -> tuple[list[str], list[list]]:
    """Execute a SQL READ AS THE APP SERVICE PRINCIPAL and return (columns, rows).

    Used for the report-registry read (LOCKED DECISION L5): the app reads
    the report_config registry as itself via :func:`_app_sp_client`.

    Args:
        sql: The SQL statement to run.
        parameters: Optional named-parameter bindings.

    Returns:
        A tuple of (column names, row data).

    Raises:
        RuntimeError: If the statement does not reach the SUCCEEDED state.
    """
    return await _exec(_app_sp_client(), sql, parameters)


async def _run_sql_sp(
    sql: str,
    parameters: list[StatementParameterListItem] | None = None,
) -> None:
    """Execute a SQL statement AS THE APP SERVICE PRINCIPAL (LOCKED DECISION L2).

    Used for the audit INSERT: the app writes the download_audit table as
    itself (not the user) via :func:`_app_sp_client`.

    Args:
        sql: The SQL statement to run (a parameterized INSERT).
        parameters: Named-parameter bindings for the placeholders in ``sql``.

    Raises:
        RuntimeError: If the statement does not reach the SUCCEEDED state
            (audit-first: the caller turns this into HTTP 500, no file).
    """
    await _exec(_app_sp_client(), sql, parameters)


def _to_sdk_params(
    params: list[dict] | None,
) -> list[StatementParameterListItem] | None:
    """Map ``{"name","value","type"}`` dicts to SDK StatementParameterListItems.

    Mirrors the audit path so every VALUE that hits the DB is a bound param.

    Args:
        params: A list of ``{"name","value","type"}`` dicts, or ``None``.

    Returns:
        The list of ``StatementParameterListItem``, or ``None`` if empty.
    """
    if not params:
        return None
    return [StatementParameterListItem(**p) for p in params]


def _dedup(items: list[str]) -> list[str]:
    """Return ``items`` with duplicates removed, order preserved.

    Args:
        items: The items to de-duplicate.

    Returns:
        A new list with the first occurrence of each item, in order.
    """
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _fmt_ts(ts: float) -> str:
    """Format an epoch timestamp as a local ``%Y-%m-%d %H:%M:%S`` string.

    Args:
        ts: An epoch time (``time.time()``).

    Returns:
        The formatted local-time string (feeds the "Last updated" label).
    """
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _coerce_page(raw: str | None) -> int:
    """Coerce a raw page query value to a 1-based page number.

    Args:
        raw: The raw ``page`` query value (or ``None``).

    Returns:
        The parsed page (``>= 1``); defaults to ``1`` on bad/absent input.
    """
    try:
        return max(1, int(raw))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 1


async def _load_reports() -> list[ReportConfig]:
    """Return the enabled report configs (SP registry read), TTL-cached.

    Reads the report_config registry AS THE APP SERVICE PRINCIPAL (LOCKED
    DECISION L5), parses each row via :func:`reports.parse_report_config`, sorts
    by ``display_order``, and caches the result for ``_REPORTS_TTL`` seconds.

    Returns:
        The enabled :class:`ReportConfig` list, sorted by ``display_order``.

    Raises:
        RuntimeError: If the registry read does not succeed.
    """
    global _reports_cache
    now = time.monotonic()
    if _reports_cache is not None and now - _reports_cache[0] < _REPORTS_TTL:
        return _reports_cache[1]
    sql = build_report_config_query(_env("APP_CATALOG"), _env("APP_SCHEMA"))
    cols, data = await _run_sql_sp_query(sql)
    configs = [parse_report_config(dict(zip(cols, row))) for row in data]
    configs.sort(key=lambda c: c.display_order)
    _reports_cache = (now, configs)
    return configs


async def _ensure_snapshot(
    token: str,
    email: str,
    report: ReportConfig,
    date: str,
    *,
    refresh: bool = False,
) -> Snapshot:
    """Return the cached per-user snapshot for a (report, date), reading OBO on miss.

    The snapshot selects ``dedup(display columns ∪ filter fields)`` (LOCKED
    DECISION L1 — the filter field MUST be projected or in-app filtering breaks),
    date-scoped only (``filters=None``), ordered by ``order_by``. On ``refresh``
    the key is evicted first so the snapshot is re-read OBO and re-stamped.

    Args:
        token: The user's OBO access token.
        email: The user's email (cache key component).
        report: The report whose source is read.
        date: The formatted report_date to scope by.
        refresh: When ``True``, evict any cached snapshot before reading.

    Returns:
        The cached (or freshly-read) :class:`Snapshot`.

    Raises:
        RuntimeError: If the OBO read does not succeed (UC-denied, etc.).
    """
    key = make_key(email, report.report_id, date)
    if refresh:
        _snapshot_cache.evict(key)
    snap = _snapshot_cache.get(key)
    if snap is not None:
        return snap
    select_cols = _dedup(
        [c.name for c in report.columns] + [f.field for f in report.filters]
    )
    sql, params = build_report_query(
        report.source_fqn,
        select_cols,
        report.date_field,
        date,
        filters=None,
        order_by=report.order_by,
    )
    cols, data = await _run_sql(token, sql, _to_sdk_params(params))
    rows = [dict(zip(cols, row)) for row in data]
    snap = Snapshot(columns=cols, rows=rows, fetched_at=time.time())
    _snapshot_cache.put(key, snap)
    return snap


def _nav_reports(configs: list[ReportConfig]) -> list[dict]:
    """Build the tab-nav descriptor list from the enabled report configs.

    Args:
        configs: The enabled reports (already sorted).

    Returns:
        A list of ``{"report_id", "title"}`` dicts for the tab nav.
    """
    return [{"report_id": c.report_id, "title": c.title} for c in configs]


async def _resolve_can_download(token: str, report: ReportConfig) -> bool:
    """Return whether the download button should be shown for a report.

    Gated by the global kill switch AND membership of the report's effective
    download group (``effective_download_group`` — the per-report group when set,
    else the code default; LOCKED DECISIONS L1/L2). The ``me()`` OBO call is an
    I/O boundary (``asyncio.to_thread``); any failure degrades to ``False``.

    Args:
        token: The user's OBO access token.
        report: The active report.

    Returns:
        ``True`` only if downloads are enabled and the user is a member of the
        report's effective download group; ``False`` otherwise.
    """
    if not downloads_enabled(os.environ.get("DOWNLOADS_ENABLED")):
        return False
    try:
        me_user = await asyncio.to_thread(_user_client(token).current_user.me)
        return is_member(me_user, effective_download_group(report))
    except Exception:  # noqa: BLE001 - degrade safe; unresolved membership -> False
        return False


@app.get("/health")
async def health() -> dict:
    """Lightweight liveness probe (no auth).

    Returns:
        A small status dict.
    """
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> Response:
    """Redirect to the first enabled report (LOCKED DECISION L4).

    Args:
        request: The incoming request.

    Returns:
        A 307 redirect to ``/report/{first_report_id}``, or a 404 error page if
        no reports are configured.
    """
    configs = await _load_reports()
    if not configs:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"message": "No reports are configured.", "nav_reports": [], "active_report_id": ""},
            status_code=404,
        )
    return RedirectResponse(f"/report/{configs[0].report_id}", status_code=307)


@app.get("/report/{report_id}", response_class=HTMLResponse)
async def report_page(request: Request, report_id: str) -> HTMLResponse:
    """Render the full report page: tabs, toolbar, filters, table page 1.

    Args:
        request: The incoming request (its headers carry the OBO token/email).
        report_id: The report registry key from the URL path.

    Returns:
        The rendered ``report.html`` page. A 404 error page if the report is
        absent/disabled; a 401 error page if the OBO token is missing; a page
        with an empty table + no-access notice if the source is UC-denied.
    """
    configs = await _load_reports()
    nav_reports = _nav_reports(configs)
    report = next(
        (c for c in configs if c.report_id == report_id and c.enabled), None
    )
    if report is None:
        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "message": f"Report {report_id!r} was not found.",
                "nav_reports": nav_reports,
                "active_report_id": "",
            },
            status_code=404,
        )

    try:
        token = extract_user_token(request.headers)
    except PermissionError as exc:
        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "message": str(exc),
                "nav_reports": nav_reports,
                "active_report_id": report_id,
            },
            status_code=401,
        )
    email = extract_user_email(request.headers)
    app_version = _env("APP_VERSION", "0.0.0")

    headers = header_cells(report.columns)

    # Resolve the date list OBO; a UC-denied read renders an empty table + notice.
    no_access = False
    dates: list[str] = []
    try:
        _dcols, date_rows = await _run_sql(
            token,
            build_report_dates_query_generic(report.source_fqn, report.date_field),
        )
        dates = [format_report_date(r[0]) for r in date_rows]
    except RuntimeError:
        no_access = True

    q_date = request.query_params.get("date")
    selected_date = q_date if q_date in dates else (dates[0] if dates else "")

    filter_options: dict[str, list[str]] = {f.field: [] for f in report.filters}
    selected_filters: dict[str, str] = {f.field: "" for f in report.filters}
    cells: list[list[dict]] = []
    total_rows = 0
    total_pages = 1
    fetched_at = ""

    snap: Snapshot | None = None
    if not no_access and selected_date:
        try:
            snap = await _ensure_snapshot(token, email, report, selected_date)
        except RuntimeError:
            no_access = True

    if snap is not None:
        # Distincts + defaults come from the cached snapshot (LOCKED DECISION L6):
        # each filter defaults to its FIRST distinct value (e.g. channel=ALL).
        for f in report.filters:
            opts = distinct_values(snap.rows, f.field)
            filter_options[f.field] = opts
            selected_filters[f.field] = opts[0] if opts else ""
        filtered = apply_filters(snap.rows, selected_filters)
        searched = apply_search(filtered, "", haystack_for(report.columns))
        page_rows, total_rows, total_pages = paginate(
            searched, 1, _DEFAULT_PAGE_SIZE
        )
        cells = display_rows(report.columns, page_rows)
        fetched_at = _fmt_ts(snap.fetched_at)

    can_download = await _resolve_can_download(token, report)

    return templates.TemplateResponse(
        request,
        "report.html",
        {
            "nav_reports": nav_reports,
            "active_report_id": report.report_id,
            "report": report,
            "dates": dates,
            "selected_date": selected_date,
            "filter_options": filter_options,
            "selected_filters": selected_filters,
            "columns": headers,
            "rows": cells,
            "page": 1,
            "size": _DEFAULT_PAGE_SIZE,
            "total_rows": total_rows,
            "total_pages": total_pages,
            "fetched_at": fetched_at,
            "no_access": no_access,
            "can_download": can_download,
            "disclaimer": _DISCLAIMER,
            "app_version": app_version,
        },
    )


@app.get("/report/{report_id}/table", response_class=HTMLResponse)
async def report_table(request: Request, report_id: str) -> HTMLResponse:
    """Return the ``_rows.html`` table-body fragment for a report selection.

    Applies ``filter -> search -> paginate`` server-side over the cached
    snapshot (NO DB re-query). Totals + fetch time are returned as
    ``X-Total-Rows``/``X-Total-Pages``/``X-Page``/``X-Fetched-At`` headers so the
    JS can redraw the pager + "Last updated" label without polluting the markup.

    Args:
        request: The incoming request (headers carry the OBO token/email; query
            params carry ``date``, each filter ``field``, ``q``, ``page``,
            ``size``, and ``refresh``).
        report_id: The report registry key from the URL path.

    Returns:
        An HTML fragment of the ``<tr>`` rows for the current page. A missing
        token or UC-denied read yields a bare inline ``<tr>`` message (never
        ``error.html`` into a ``<tbody>``).

    Raises:
        HTTPException: 404 if the report is absent/disabled; 400 if ``date`` is
            out of the allowed set.
    """
    configs = await _load_reports()
    report = next(
        (c for c in configs if c.report_id == report_id and c.enabled), None
    )
    if report is None:
        raise HTTPException(status_code=404, detail=f"report {report_id!r} not found")

    colspan = max(1, len(report.columns))
    try:
        token = extract_user_token(request.headers)
    except PermissionError:
        # Fragment swaps into <tbody>; return a bare inline row, not error.html.
        return HTMLResponse(
            f'<tr><td colspan="{colspan}">Session expired — reload the page.</td></tr>',
            status_code=401,
        )
    email = extract_user_email(request.headers)

    # Validate the requested date against the OBO date list (mirrors validate).
    try:
        _dcols, date_rows = await _run_sql(
            token,
            build_report_dates_query_generic(report.source_fqn, report.date_field),
        )
    except RuntimeError:
        return HTMLResponse(
            f'<tr><td colspan="{colspan}">No data — you may not have access to this report.</td></tr>'
        )
    allowed = {format_report_date(r[0]) for r in date_rows}
    date = request.query_params.get("date", "")
    if date not in allowed:
        raise HTTPException(status_code=400, detail=f"invalid date {date!r}")

    # Only known filter fields are honored; unknown query keys are ignored.
    selected_filters: dict[str, str] = {}
    for f in report.filters:
        val = request.query_params.get(f.field)
        if val is not None:
            selected_filters[f.field] = val

    q = request.query_params.get("q", "")
    page = _coerce_page(request.query_params.get("page"))
    size = _PAGE_SIZES.get(
        (request.query_params.get("size") or "").lower(), _DEFAULT_PAGE_SIZE
    )
    refresh = request.query_params.get("refresh") == "1"

    try:
        snap = await _ensure_snapshot(token, email, report, date, refresh=refresh)
    except RuntimeError:
        return HTMLResponse(
            f'<tr><td colspan="{colspan}">No data — you may not have access to this report.</td></tr>'
        )

    # Any filter not supplied defaults to its first distinct value.
    for f in report.filters:
        if f.field not in selected_filters:
            opts = distinct_values(snap.rows, f.field)
            selected_filters[f.field] = opts[0] if opts else ""

    filtered = apply_filters(snap.rows, selected_filters)
    searched = apply_search(filtered, q, haystack_for(report.columns))
    page_rows, total_rows, total_pages = paginate(searched, page, size)
    cells = display_rows(report.columns, page_rows)

    resp = templates.TemplateResponse(request, "_rows.html", {"rows": cells})
    resp.headers["X-Total-Rows"] = str(total_rows)
    resp.headers["X-Total-Pages"] = str(total_pages)
    resp.headers["X-Page"] = str(max(1, min(page, total_pages)))
    resp.headers["X-Fetched-At"] = _fmt_ts(snap.fetched_at)
    return resp


# Acknowledgement checkbox truthy values (an unchecked HTML checkbox sends no
# field; a checked one sends value="true").
_ACK_TRUTHY: frozenset[str] = frozenset({"true", "on", "1", "yes"})


@app.post("/download")
async def download(request: Request) -> Response:
    """Export the current filtered view of ANY report — gated, acknowledged, audited.

    Works on any configured report (LOCKED DECISION L1): the per-filter form
    fields are dynamic, so the raw form is read via ``await request.form()``.
    Flow (audit-first): validate the OBO token (401) -> kill switch (403) ->
    resolve the report (404) -> re-check membership of the report's effective
    download group (403; never trust the hidden UI; degrade-safe deny) ->
    acknowledgement + justification (400) -> validate ``date`` against the
    report's OBO date list (400) -> read/reuse the per-user OBO snapshot ->
    default any absent filter to its first distinct value (SAME defaulting as
    the table) -> ``apply_filters`` + ``apply_search`` over the cached rows (ALL
    matching rows, no pagination) -> build the file for the report's display
    columns -> write EXACTLY ONE audit row as the app SP (must reach SUCCEEDED,
    else 500 and NO file) -> emit an app-log line -> return the attachment.

    Args:
        request: The incoming request. Headers carry the OBO token + email; the
            form carries ``report_id``, ``date``, ``search``, ``acknowledged``,
            ``justification``, ``format``, and one field per report filter.

    Returns:
        A ``Response`` with the file bytes and a download ``Content-Disposition``.

    Raises:
        HTTPException: 401 (no OBO token), 403 (kill switch or not a member of
            the report's download group), 404 (unknown/disabled report), 400
            (missing ack/justification or invalid date), 500 (audit write
            failed — no file), 503 (source unreadable as the user).
    """
    # 1) OBO token + best-effort email for the audit row.
    try:
        token = extract_user_token(request.headers)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    email = extract_user_email(request.headers)

    form = await request.form()

    # 1b) Global kill switch (LOCKED DECISION L1): if downloads are disabled,
    # 403 regardless of group membership — checked before report resolution and
    # the membership re-check so the feature is off for everyone when toggled off.
    if not downloads_enabled(os.environ.get("DOWNLOADS_ENABLED")):
        raise HTTPException(
            status_code=403,
            detail="Downloads are temporarily disabled.",
        )

    # 2) Resolve the requested report (404 if absent/disabled).
    report_id = str(form.get("report_id", ""))
    configs = await _load_reports()
    report = next(
        (c for c in configs if c.report_id == report_id and c.enabled), None
    )
    if report is None:
        raise HTTPException(status_code=404, detail=f"report {report_id!r} not found")

    # 3) Server-side group re-check against the report's effective download group
    # (defense in depth). Never fail open.
    group = effective_download_group(report)
    member = False
    try:
        me_user = await asyncio.to_thread(_user_client(token).current_user.me)
        member = is_member(me_user, group)
    except Exception:  # noqa: BLE001 - degrade safe; unresolved membership -> deny
        member = False
    if not member:
        raise HTTPException(
            status_code=403,
            detail="You are not authorized to download this data.",
        )

    # 4) Validate acknowledgement + justification.
    acknowledged = str(form.get("acknowledged", ""))
    justification = str(form.get("justification", ""))
    if (
        acknowledged.strip().lower() not in _ACK_TRUTHY
        or not justification.strip()
    ):
        raise HTTPException(
            status_code=400,
            detail="Acknowledgement and a justification are both required.",
        )

    # 5) Validate the requested date against the report's OBO date list.
    try:
        _dcols, date_rows = await _run_sql(
            token,
            build_report_dates_query_generic(report.source_fqn, report.date_field),
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Unable to read the report as your user: {exc}",
        ) from exc
    allowed = {format_report_date(r[0]) for r in date_rows}
    date = str(form.get("date", ""))
    if date not in allowed:
        raise HTTPException(status_code=400, detail=f"invalid date {date!r}")

    # 6) Read/reuse the per-user OBO snapshot for (report, date).
    try:
        snap = await _ensure_snapshot(token, email, report, date)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Unable to read the report as your user: {exc}",
        ) from exc

    # 7) Build selected_filters from the form; any absent filter defaults to its
    # first distinct value (SAME defaulting as report_table -> matches the screen).
    selected_filters: dict[str, str] = {}
    for f in report.filters:
        val = form.get(f.field)
        if val is not None:
            selected_filters[f.field] = str(val)
        else:
            opts = distinct_values(snap.rows, f.field)
            selected_filters[f.field] = opts[0] if opts else ""

    search = str(form.get("search", ""))
    filtered = apply_filters(snap.rows, selected_filters)
    searched = apply_search(filtered, search, haystack_for(report.columns))

    # 8) Build the file bytes + media type by format (ALL matching rows).
    fmt = "xlsx" if str(form.get("format", "csv")) == "xlsx" else "csv"
    if fmt == "xlsx":
        file_bytes = to_xlsx_bytes(report.columns, searched, _DISCLAIMER)
        media_type = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    else:
        file_bytes = to_csv_bytes(report.columns, searched, _DISCLAIMER)
        media_type = "text/csv"

    # 9) AUDIT-FIRST (NFR-5): write exactly one audit row as the app SP; the
    # INSERT must reach SUCCEEDED before the file is returned, else HTTP 500.
    catalog = _env("APP_CATALOG")
    schema = _env("APP_SCHEMA")
    app_version = _env("APP_VERSION", "0.0.0")
    summary = filters_summary(selected_filters)
    audit_row = build_audit_row(
        user_email=email,
        report_date=date,
        filter_summary=summary,
        search_filter=search,
        row_count=len(searched),
        export_format=fmt,
        justification=justification,
        app_version=app_version,
        report_id=report.report_id,
        report_title=report.title,
    )
    sql, param_dicts = build_audit_insert(catalog, schema, audit_row)
    audit_params = [StatementParameterListItem(**d) for d in param_dicts]
    try:
        await _run_sql_sp(sql, audit_params)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Download blocked: audit write failed ({exc}).",
        ) from exc

    # App-log line so the download event surfaces in `databricks apps logs`.
    print(
        f"[download-hub] download audited: user={email!r} "
        f"report_id={report.report_id!r} audit_id={audit_row['audit_id']} "
        f"rows={len(searched)} format={fmt} date={date!r}"
    )

    # 10) Return the attachment.
    fname = filename_for(report.report_id, date, fmt)
    return Response(
        content=file_bytes,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
