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
import hashlib
import json
import os
import time
from pathlib import Path

from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config
from databricks.sdk.service.sql import StatementParameterListItem, StatementState
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from audit import build_audit_insert, build_audit_row
from auth import (
    ADMIN_GROUP,
    DEFAULT_DOWNLOAD_SUFFIX,
    can_view,
    effective_download_group,
    effective_view_group,
    extract_user_email,
    extract_user_token,
    is_admin,
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
from errors import ReportDataError, friendly_error
from exports import DEFAULT_DISCLAIMER, filename_for, to_csv_bytes, to_xlsx_bytes
from render import display_rows, haystack_for, header_cells
from reports import (
    AUDIT_LOG_COLUMNS,
    ColumnSpec,
    ReportConfig,
    ReportView,
    build_app_config_query,
    build_app_config_upsert,
    build_audit_log_query,
    build_preview_query,
    build_report_config_query,
    build_report_config_upsert,
    build_report_query,
    build_report_view_query,
    build_report_view_upsert,
    parse_report_config,
    parse_report_view,
    resolve_columns,
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
# Admin group + download-group naming suffix (env-overridable; see auth.py).
_ADMIN_GROUP = (os.environ.get("ADMIN_GROUP") or "").strip() or ADMIN_GROUP
_DL_SUFFIX = (os.environ.get("DOWNLOAD_GROUP_SUFFIX") or "").strip() or DEFAULT_DOWNLOAD_SUFFIX


def _int_env(name: str, default: int) -> int:
    """Read a positive int env var, falling back to ``default`` on bad/absent input."""
    try:
        v = int(os.environ.get(name, ""))
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


# Export size guards. The whole file is built in memory in the (small) app
# container, so these defaults are chosen to stay comfortably within a typical
# ~1-2 GB container: CSV is cheap (stdlib), XLSX via openpyxl is much heavier
# per cell, so it gets a lower cap. Both env-overridable — raise them only if
# the app container is sized up. Over the cap the user gets a clear "narrow your
# filters" message instead of an out-of-memory crash.
_MAX_DOWNLOAD_ROWS = _int_env("MAX_DOWNLOAD_ROWS", 100_000)
_MAX_XLSX_ROWS = _int_env("MAX_XLSX_ROWS", 25_000)

app = FastAPI(title=_APP_NAME)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
# Asset cache-buster: a short content hash of the authored CSS/JS computed at
# startup and appended as ?v=... to those /static assets. Because it is derived
# from file CONTENT (not APP_VERSION), any change to the sheets/scripts changes
# the query string and forces browsers to refetch — no manual version bump.
def _compute_asset_version() -> str:
    """Return a 12-char content hash of the authored CSS/JS (or a fallback)."""
    h = hashlib.sha256()
    for rel in ("static/css/app.css", "static/js/app.js", "static/js/admin.js"):
        try:
            h.update((_BASE_DIR / rel).read_bytes())
        except OSError:  # pragma: no cover - missing file falls back gracefully
            continue
    return h.hexdigest()[:12]


_ASSET_VERSION = _compute_asset_version()
templates.env.globals.update(
    app_name=_APP_NAME,
    app_logo=_APP_LOGO,
    app_org_name=_APP_ORG_NAME,
    asset_version=_ASSET_VERSION,
)

# Per-user snapshot cache (LOCKED DECISION L2). Bounded LRU; refresh evicts. Each
# key is (user_email, report_id, date) so no cross-user data ever mixes.
_snapshot_cache = SnapshotCache(max_size=128)

# Report registry TTL cache (LOCKED DECISION L5): parsed ReportConfigs read as the
# app SP, refreshed at most every _REPORTS_TTL seconds so a MERGE'd row appears
# without a redeploy.
_reports_cache: tuple[float, list[ReportConfig]] | None = None
_REPORTS_TTL: float = 300.0

# View registry TTL cache (parsed ReportViews read as the app SP), same TTL.
_views_cache: tuple[float, list[ReportView]] | None = None

# App key/value config TTL cache (e.g. the admin-set download disclaimer).
_config_cache: tuple[float, dict[str, str]] | None = None

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
        ReportDataError: If the warehouse is unset, the SDK call itself fails
            (network/warehouse/permission), or the statement does not reach the
            SUCCEEDED state. The message is already user-facing
            (:func:`errors.friendly_error`); it subclasses ``RuntimeError`` so
            existing ``except RuntimeError`` handlers still catch it.
    """
    try:
        warehouse_id = _env("DATABRICKS_WAREHOUSE_ID")
    except RuntimeError as exc:
        raise ReportDataError(
            "The SQL warehouse for this app is not configured. Contact an "
            "administrator."
        ) from exc
    try:
        resp = await asyncio.to_thread(
            client.statement_execution.execute_statement,
            warehouse_id=warehouse_id,
            statement=sql,
            parameters=parameters,
            wait_timeout="30s",
        )
    except Exception as exc:  # noqa: BLE001 - normalize any SDK/transport error
        raise ReportDataError(friendly_error(str(exc))) from exc
    if resp.status is None or resp.status.state != StatementState.SUCCEEDED:
        detail = ""
        if resp.status is not None and resp.status.error is not None:
            detail = resp.status.error.message or ""
        raise ReportDataError(friendly_error(detail))

    columns: list[str] = []
    if resp.manifest is not None and resp.manifest.schema is not None:
        columns = [c.name for c in (resp.manifest.schema.columns or [])]

    # Collect ALL result chunks, not just the first. The inline result carries
    # only chunk 0 (~a few MB); larger results are paged. Without following
    # next_chunk_index the read would silently truncate large tables.
    data: list[list] = []
    result = resp.result
    statement_id = resp.statement_id
    while result is not None:
        if result.data_array:
            data.extend(result.data_array)
        next_index = getattr(result, "next_chunk_index", None)
        if next_index is None or statement_id is None:
            break
        try:
            result = await asyncio.to_thread(
                client.statement_execution.get_statement_result_chunk_n,
                statement_id,
                next_index,
            )
        except Exception as exc:  # noqa: BLE001 - normalize transport error
            raise ReportDataError(friendly_error(str(exc))) from exc
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


async def _load_views() -> list[ReportView]:
    """Return the enabled views (SP registry read), TTL-cached.

    Reads the ``report_view`` registry AS THE APP SERVICE PRINCIPAL, parses each
    row, sorts by ``display_order``, and caches for ``_REPORTS_TTL`` seconds. A
    missing ``report_view`` table is tolerated (returns ``[]``) so an install
    that has reports but no views still renders (view labels fall back to the
    ``view_key``).

    Returns:
        The enabled :class:`ReportView` list, sorted by ``display_order`` (empty
        if the table is absent).
    """
    global _views_cache
    now = time.monotonic()
    if _views_cache is not None and now - _views_cache[0] < _REPORTS_TTL:
        return _views_cache[1]
    sql = build_report_view_query(_env("APP_CATALOG"), _env("APP_SCHEMA"))
    try:
        cols, data = await _run_sql_sp_query(sql)
    except RuntimeError:
        views: list[ReportView] = []  # no report_view table -> derive from keys
    else:
        views = [parse_report_view(dict(zip(cols, row))) for row in data]
        views.sort(key=lambda v: v.display_order)
    _views_cache = (now, views)
    return views


def _invalidate_registry() -> None:
    """Drop the reports + views + config caches so an admin write shows now."""
    global _reports_cache, _views_cache, _config_cache
    _reports_cache = None
    _views_cache = None
    _config_cache = None


async def _load_app_config() -> dict[str, str]:
    """Return the app key/value config (SP read), TTL-cached.

    Tolerates a missing ``app_config`` table (returns ``{}``) so an install that
    predates System Config still runs (values fall back to env/defaults).

    Returns:
        A ``{config_key: config_value}`` dict.
    """
    global _config_cache
    now = time.monotonic()
    if _config_cache is not None and now - _config_cache[0] < _REPORTS_TTL:
        return _config_cache[1]
    sql = build_app_config_query(_env("APP_CATALOG"), _env("APP_SCHEMA"))
    try:
        cols, data = await _run_sql_sp_query(sql)
    except RuntimeError:
        cfg: dict[str, str] = {}
    else:
        idx = {c: i for i, c in enumerate(cols)}
        cfg = {
            row[idx["config_key"]]: (row[idx["config_value"]] or "")
            for row in data
            if row[idx["config_key"]] is not None
        }
    _config_cache = (now, cfg)
    return cfg


async def _effective_disclaimer() -> str:
    """Return the download disclaimer: admin-set config value, else env/default.

    Returns:
        The ``download_disclaimer`` app-config value if non-empty, else the
        env/built-in fallback (``_DISCLAIMER``).
    """
    cfg = await _load_app_config()
    return (cfg.get("download_disclaimer") or "").strip() or _DISCLAIMER


def _readable_email(me_user, header_email: str) -> str:
    """Return the user's readable email/name, not the numeric forwarded id.

    On some workspaces (e.g. GovCloud) the ``X-Forwarded-User`` header is a
    numeric SCIM id (``<user_id>@<workspace_id>``), which is unreadable in the
    audit log. The SCIM ``me()`` object carries the real ``user_name`` (email)
    and ``display_name``; prefer those, falling back to the header value.

    Args:
        me_user: The user's ``me()`` object (or ``None``).
        header_email: The ``X-Forwarded-User`` header value (fallback).

    Returns:
        The best readable identifier for the user.
    """
    for attr in ("user_name", "display_name"):
        val = getattr(me_user, attr, None) if me_user is not None else None
        if val:
            return str(val)
    return header_email or ""


async def _me(token: str):
    """Return the OBO ``current_user.me()`` User, or ``None`` on failure.

    Single I/O point for identity (groups, admin, download eligibility) so a
    request resolves the user once. Degrades safe: any error yields ``None``
    (treated as "no groups / not admin").

    Args:
        token: The user's OBO access token.

    Returns:
        The SCIM ``User`` object, or ``None``.
    """
    try:
        return await asyncio.to_thread(_user_client(token).current_user.me)
    except Exception:  # noqa: BLE001 - degrade safe
        return None


def _visible_reports(
    configs: list[ReportConfig], me_user
) -> list[ReportConfig]:
    """Return the reports the user may SEE (view group OR download group).

    Args:
        configs: All enabled reports (sorted).
        me_user: The user's ``me()`` object (or ``None`` -> nothing visible).

    Returns:
        The subset the user can view, preserving ``display_order``.
    """
    if me_user is None:
        return []
    return [c for c in configs if can_view(me_user, c, _DL_SUFFIX)]


def _views_for_user(
    views: list[ReportView], visible: list[ReportConfig]
) -> list[dict]:
    """Build the ordered view-switcher descriptors from the user's visible reports.

    A view appears only if the user can see at least one report in it. Views
    present in the ``report_view`` registry keep their title + order; a
    ``view_key`` that appears on a report but not in the registry is synthesized
    with its key as the title, appended after the registered views.

    Args:
        views: The enabled :class:`ReportView` registry rows (sorted).
        visible: The reports the user may see.

    Returns:
        A list of ``{"view_key", "title", "report_id"}`` dicts in switcher order,
        where ``report_id`` is the first visible report in the view (the switcher
        link target).
    """
    # First visible report per view key (visible is already in display_order).
    first_report: dict[str, str] = {}
    present: list[str] = []
    for c in visible:
        vk = effective_view_group(c)
        if vk not in first_report:
            first_report[vk] = c.report_id
            present.append(vk)
    ordered: list[dict] = []
    seen: set[str] = set()
    for v in views:  # registered views first, in display_order
        if v.view_key in present:
            ordered.append(
                {"view_key": v.view_key, "title": v.title, "report_id": first_report[v.view_key]}
            )
            seen.add(v.view_key)
    for vk in present:  # synthesized (unregistered) views after, key as title
        if vk not in seen:
            ordered.append({"view_key": vk, "title": vk, "report_id": first_report[vk]})
    return ordered


def _reports_in_view(
    visible: list[ReportConfig], view_key: str
) -> list[ReportConfig]:
    """Return the user's visible reports that belong to ``view_key`` (ordered).

    Args:
        visible: The reports the user may see.
        view_key: The active view's key.

    Returns:
        The visible reports whose effective view group is ``view_key``.
    """
    return [c for c in visible if effective_view_group(c) == view_key]


async def _ensure_snapshot(
    token: str,
    email: str,
    report: ReportConfig,
    date: str,
    *,
    refresh: bool = False,
) -> Snapshot:
    """Return the cached per-user snapshot for a (report, date), reading OBO on miss.

    When the report configures display columns, the snapshot selects
    ``dedup(display columns ∪ filter fields)`` (the filter field MUST be
    projected or in-app filtering breaks); when it does not, the snapshot selects
    ``*`` so every column the query returns is available for display and search.
    It is date-scoped only when the report has a ``date_field`` (``date`` is
    then bound), ordered by ``order_by``. On ``refresh`` the key is evicted first
    so the snapshot is re-read OBO and re-stamped.

    Args:
        token: The user's OBO access token.
        email: The user's email (cache key component).
        report: The report whose source is read.
        date: The formatted report_date to scope by (``""`` when the report has
            no ``date_field``).
        refresh: When ``True``, evict any cached snapshot before reading.

    Returns:
        The cached (or freshly-read) :class:`Snapshot`.

    Raises:
        ReportDataError: If the OBO read does not succeed (UC-denied, missing
            table/column, etc.).
    """
    key = make_key(email, report.report_id, date)
    if refresh:
        _snapshot_cache.evict(key)
    snap = _snapshot_cache.get(key)
    if snap is not None:
        return snap
    # Empty configured columns -> select * (all query columns show by default).
    select_cols: list[str] | None = None
    if report.columns:
        select_cols = _dedup(
            [c.name for c in report.columns] + [f.field for f in report.filters]
        )
    # Scope by date only when the report is date-scoped AND a specific date is
    # chosen; an empty date is the "All dates" sentinel (no WHERE on the date).
    scope = bool(report.date_field and date)
    sql, params = build_report_query(
        report.source_query,
        select_cols,
        report.date_field if scope else None,
        date if scope else None,
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


def _effective_columns(
    report: ReportConfig, snap: Snapshot | None
) -> list[ColumnSpec]:
    """Return the columns to render for a report, given a snapshot (or none).

    Delegates to :func:`reports.resolve_columns`: the configured columns win when
    present; otherwise every column the query returned becomes a text column
    (labelled by its own name). Falls back to the configured columns when there
    is no snapshot (unreadable source), so headers still render if configured.

    Args:
        report: The active report.
        snap: The per-user snapshot, or ``None`` when the source is unreadable.

    Returns:
        The effective ordered :class:`ColumnSpec` list.
    """
    result_columns = snap.columns if snap is not None else []
    return resolve_columns(report.columns, result_columns)


def _resolve_can_download(me_user, report: ReportConfig) -> bool:
    """Return whether the download button should be shown for a report.

    Gated by the global kill switch AND membership of the report's effective
    download group (``effective_download_group`` — the explicit per-report group
    when set, else derived from ``view_key`` + suffix). Pure over an
    already-resolved ``me()`` object (no extra I/O); ``None`` -> ``False``.

    Args:
        me_user: The user's ``me()`` object (or ``None``).
        report: The active report.

    Returns:
        ``True`` only if downloads are enabled and the user is a member of the
        report's effective download group; ``False`` otherwise.
    """
    if not downloads_enabled(os.environ.get("DOWNLOADS_ENABLED")):
        return False
    if me_user is None:
        return False
    return is_member(me_user, effective_download_group(report, _DL_SUFFIX))


@app.get("/health")
async def health() -> dict:
    """Lightweight liveness probe (no auth).

    Returns:
        A small status dict.
    """
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> Response:
    """Redirect to the first report of the user's first accessible view.

    Args:
        request: The incoming request.

    Returns:
        A 307 redirect to the landing report; a 404 error page if no reports are
        configured; a 403 error page if none are visible to the user; a 503
        error page if the registry is unreadable.
    """
    try:
        configs = await _load_reports()
    except RuntimeError as exc:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"message": str(exc), "nav_reports": [], "active_report_id": ""},
            status_code=503,
        )
    if not configs:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"message": "No reports are configured.", "nav_reports": [], "active_report_id": ""},
            status_code=404,
        )
    # Without a token we cannot resolve visibility; hand off to the first report
    # (report_page renders the 401). Otherwise land on the first VISIBLE report.
    try:
        token = extract_user_token(request.headers)
    except PermissionError:
        return RedirectResponse(f"/report/{configs[0].report_id}", status_code=307)
    me_user = await _me(token)
    visible = _visible_reports(configs, me_user)
    if not visible:
        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "message": "You do not have access to any reports. Ask an "
                "administrator to add you to a view group.",
                "nav_reports": [],
                "active_report_id": "",
            },
            status_code=403,
        )
    return RedirectResponse(f"/report/{visible[0].report_id}", status_code=307)


@app.get("/report/{report_id}", response_class=HTMLResponse)
async def report_page(request: Request, report_id: str) -> HTMLResponse:
    """Render the full report page: tabs, toolbar, filters, table page 1.

    Args:
        request: The incoming request (its headers carry the OBO token/email).
        report_id: The report registry key from the URL path.

    Returns:
        The rendered ``report.html`` page. A 404 error page if the report is
        absent/disabled; a 401 error page if the OBO token is missing; a 503
        error page if the registry is unreadable; a page with an empty table +
        an explicit notice if the source is unreadable.
    """
    try:
        configs = await _load_reports()
        views = await _load_views()
    except RuntimeError as exc:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"message": str(exc), "nav_reports": [], "active_report_id": ""},
            status_code=503,
        )
    report = next(
        (c for c in configs if c.report_id == report_id and c.enabled), None
    )
    if report is None:
        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "message": f"Report {report_id!r} was not found.",
                "nav_reports": [],
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
                "nav_reports": [],
                "active_report_id": report_id,
            },
            status_code=401,
        )
    email = extract_user_email(request.headers)
    app_version = _env("APP_VERSION", "0.0.0")

    # Resolve identity once (groups drive visibility, download, admin).
    me_user = await _me(token)
    visible = _visible_reports(configs, me_user)
    if report not in visible:
        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "message": "You do not have access to this report.",
                "nav_reports": [],
                "active_report_id": "",
            },
            status_code=403,
        )
    # Tabs = the current view's visible reports; switcher = all accessible views.
    active_view = effective_view_group(report)
    nav_reports = _nav_reports(_reports_in_view(visible, active_view))
    view_switcher = _views_for_user(views, visible)
    user_is_admin = is_admin(me_user, _ADMIN_GROUP) if me_user is not None else False

    # Resolve the date list OBO ONLY when the report is date-scoped; an
    # unreadable source records an explicit notice (rendered in the empty state).
    notice = ""
    dates: list[str] = []
    if report.date_field:
        try:
            _dcols, date_rows = await _run_sql(
                token,
                build_report_dates_query_generic(
                    report.source_query, report.date_field
                ),
            )
            dates = [format_report_date(r[0]) for r in date_rows]
        except RuntimeError as exc:
            notice = str(exc)

    # ``""`` is the "All dates" sentinel (valid alongside the distinct dates).
    q_date = request.query_params.get("date")
    if q_date is not None and (q_date == "" or q_date in dates):
        selected_date = q_date
    else:
        selected_date = dates[0] if dates else ""

    filter_options: dict[str, list[str]] = {f.field: [] for f in report.filters}
    selected_filters: dict[str, str] = {f.field: "" for f in report.filters}
    cells: list[list[dict]] = []
    total_rows = 0
    total_pages = 1
    fetched_at = ""

    # Read the snapshot whenever the source is reachable (date scope, if any, is
    # applied inside _ensure_snapshot; an empty selected_date reads all dates).
    snap: Snapshot | None = None
    if not notice:
        try:
            snap = await _ensure_snapshot(token, email, report, selected_date)
        except RuntimeError as exc:
            notice = str(exc)

    columns = _effective_columns(report, snap)
    headers = header_cells(columns)

    if snap is not None:
        # Distinct values feed each filter dropdown; every filter defaults to
        # "All" (no constraint). The template prepends the "All" option.
        for f in report.filters:
            filter_options[f.field] = distinct_values(snap.rows, f.field)
            selected_filters[f.field] = ""
        filtered = apply_filters(snap.rows, selected_filters)
        searched = apply_search(filtered, "", haystack_for(columns))
        page_rows, total_rows, total_pages = paginate(
            searched, 1, _DEFAULT_PAGE_SIZE
        )
        cells = display_rows(columns, page_rows)
        fetched_at = _fmt_ts(snap.fetched_at)

    can_download = _resolve_can_download(me_user, report)
    disclaimer = await _effective_disclaimer()

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
            "notice": notice,
            "can_download": can_download,
            "disclaimer": disclaimer,
            "app_version": app_version,
            "view_switcher": view_switcher,
            "active_view_key": active_view,
            "user_is_admin": user_is_admin,
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
    try:
        configs = await _load_reports()
    except RuntimeError as exc:
        return HTMLResponse(
            f'<tr><td colspan="1">{str(exc)}</td></tr>', status_code=503
        )
    report = next(
        (c for c in configs if c.report_id == report_id and c.enabled), None
    )
    if report is None:
        raise HTTPException(status_code=404, detail=f"report {report_id!r} not found")

    # Provisional colspan for pre-snapshot inline messages; recomputed for rows.
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

    # Visibility re-check (defense in depth): the caller must belong to the
    # report's view group or download group.
    me_user = await _me(token)
    if me_user is None or not can_view(me_user, report, _DL_SUFFIX):
        return HTMLResponse(
            f'<tr><td colspan="{colspan}">You do not have access to this report.</td></tr>',
            status_code=403,
        )

    # Validate the requested date against the OBO date list, but only for a
    # date-scoped report; an undated report ignores the date param entirely.
    date = request.query_params.get("date", "")
    if report.date_field:
        try:
            _dcols, date_rows = await _run_sql(
                token,
                build_report_dates_query_generic(
                    report.source_query, report.date_field
                ),
            )
        except RuntimeError as exc:
            return HTMLResponse(f'<tr><td colspan="{colspan}">{str(exc)}</td></tr>')
        allowed = {format_report_date(r[0]) for r in date_rows}
        # "" is the "All dates" sentinel (no date scope); any other value must be
        # a known date.
        if date != "" and date not in allowed:
            raise HTTPException(status_code=400, detail=f"invalid date {date!r}")
    else:
        date = ""

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
    except RuntimeError as exc:
        return HTMLResponse(f'<tr><td colspan="{colspan}">{str(exc)}</td></tr>')

    columns = _effective_columns(report, snap)

    # Any filter not supplied defaults to "All" (no constraint).
    for f in report.filters:
        if f.field not in selected_filters:
            selected_filters[f.field] = ""

    filtered = apply_filters(snap.rows, selected_filters)
    searched = apply_search(filtered, q, haystack_for(columns))
    page_rows, total_rows, total_pages = paginate(searched, page, size)
    cells = display_rows(columns, page_rows)

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
    try:
        configs = await _load_reports()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    report = next(
        (c for c in configs if c.report_id == report_id and c.enabled), None
    )
    if report is None:
        raise HTTPException(status_code=404, detail=f"report {report_id!r} not found")

    # 3) Server-side group re-check against the report's effective download group
    # (explicit, else derived from view_key + suffix). Defense in depth; never
    # fail open.
    group = effective_download_group(report, _DL_SUFFIX)
    me_user = await _me(token)
    if me_user is None or not is_member(me_user, group):
        raise HTTPException(
            status_code=403,
            detail="You are not authorized to download this data.",
        )
    # Prefer the readable email over the numeric X-Forwarded-User id for the audit.
    email = _readable_email(me_user, email)

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

    # 5) Validate the requested date against the report's OBO date list — only
    # for a date-scoped report; an undated report exports its full result.
    date = str(form.get("date", ""))
    if report.date_field:
        try:
            _dcols, date_rows = await _run_sql(
                token,
                build_report_dates_query_generic(
                    report.source_query, report.date_field
                ),
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        allowed = {format_report_date(r[0]) for r in date_rows}
        # "" is the "All dates" sentinel (export every date); else a known date.
        if date != "" and date not in allowed:
            raise HTTPException(status_code=400, detail=f"invalid date {date!r}")
    else:
        date = ""

    # 6) Read/reuse the per-user OBO snapshot for (report, date).
    try:
        snap = await _ensure_snapshot(token, email, report, date)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    columns = _effective_columns(report, snap)

    # 7) Build selected_filters from the form; any absent filter defaults to
    # "All" (no constraint) — SAME defaulting as report_table -> matches screen.
    selected_filters: dict[str, str] = {}
    for f in report.filters:
        val = form.get(f.field)
        selected_filters[f.field] = str(val) if val is not None else ""

    search = str(form.get("search", ""))
    filtered = apply_filters(snap.rows, selected_filters)
    searched = apply_search(filtered, search, haystack_for(columns))

    # 8) Guard export size (the file is built in memory). CSV has a generous cap;
    # XLSX is far heavier to build so it has a lower cap — over it, ask for CSV.
    fmt = "xlsx" if str(form.get("format", "csv")) == "xlsx" else "csv"
    n = len(searched)
    if fmt == "xlsx" and n > _MAX_XLSX_ROWS:
        raise HTTPException(
            status_code=413,
            detail=(
                f"This export has {n:,} rows, above the Excel limit of "
                f"{_MAX_XLSX_ROWS:,}. Narrow the filters or choose CSV."
            ),
        )
    if n > _MAX_DOWNLOAD_ROWS:
        raise HTTPException(
            status_code=413,
            detail=(
                f"This export has {n:,} rows, above the {_MAX_DOWNLOAD_ROWS:,}-row "
                "download limit. Narrow the date or filters."
            ),
        )

    disclaimer = await _effective_disclaimer()
    if fmt == "xlsx":
        file_bytes = to_xlsx_bytes(columns, searched, disclaimer)
        media_type = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    else:
        file_bytes = to_csv_bytes(columns, searched, disclaimer)
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
        source_query=report.source_query,
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


# ======================================================================
# Admin console (report + view registry management)
# ======================================================================
# Gated by membership of the admin group (env ADMIN_GROUP). Reads run OBO for
# the query PREVIEW (the admin only previews data they can access); WRITES run
# as the app service principal (which owns the registry tables), mirroring the
# audit-write identity. Every write invalidates the TTL caches so changes show
# immediately.


async def _require_admin(request: Request):
    """Return the admin's ``(token, email, me_user)`` or raise 401/403.

    Args:
        request: The incoming request (headers carry the OBO token/email).

    Returns:
        A tuple ``(token, email, me_user)`` for an authorized admin.

    Raises:
        HTTPException: 401 if the OBO token is missing; 403 if the user is not a
            member of the admin group.
    """
    try:
        token = extract_user_token(request.headers)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    email = extract_user_email(request.headers)
    me_user = await _me(token)
    if me_user is None or not is_admin(me_user, _ADMIN_GROUP):
        raise HTTPException(
            status_code=403,
            detail="Admin access required (membership of the admin group).",
        )
    return token, _readable_email(me_user, email), me_user


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request) -> Response:
    """Render the admin console: manage views and reports.

    Args:
        request: The incoming request.

    Returns:
        The rendered ``admin.html`` page, or an ``error.html`` page (401 no
        token / 403 not an admin / 503 registry unreadable).
    """
    try:
        token = extract_user_token(request.headers)
    except PermissionError as exc:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"message": str(exc), "nav_reports": [], "active_report_id": ""},
            status_code=401,
        )
    me_user = await _me(token)
    if me_user is None or not is_admin(me_user, _ADMIN_GROUP):
        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "message": "Admin access required. Ask an administrator to add "
                "you to the admin group.",
                "nav_reports": [],
                "active_report_id": "",
            },
            status_code=403,
        )
    try:
        configs = await _load_reports()
        views = await _load_views()
    except RuntimeError as exc:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"message": str(exc), "nav_reports": [], "active_report_id": ""},
            status_code=503,
        )
    # System Config: current effective disclaimer for the editor.
    current_disclaimer = await _effective_disclaimer()
    # Audit Log: recent downloads (SP read; tolerate a missing table).
    audit_columns = list(AUDIT_LOG_COLUMNS)
    audit_rows: list[list[str]] = []
    try:
        acols, adata = await _run_sql_sp_query(
            build_audit_log_query(_env("APP_CATALOG"), _env("APP_SCHEMA"), 200)
        )
        aidx = {c: i for i, c in enumerate(acols)}
        audit_rows = [
            ["" if row[aidx[c]] is None else str(row[aidx[c]]) for c in audit_columns]
            for row in adata
        ]
    except RuntimeError:
        audit_rows = []  # no audit table / unreadable -> empty tab
    # Serializable report rows for the edit form (re-serialize columns/filters to
    # the JSON the builder expects; ReportConfig only keeps parsed specs).
    reports_admin = [
        {
            "report_id": c.report_id,
            "title": c.title,
            "source_query": c.source_query,
            "date_field": c.date_field or "",
            "order_by": c.order_by or "",
            "display_order": c.display_order,
            "enabled": c.enabled,
            "download_group": c.download_group or "",
            "view_key": c.view_key or "",
            "columns_json": json.dumps(
                [{"name": col.name, "label": col.label, "format": col.format} for col in c.columns]
            ),
            "filters_json": json.dumps(
                [{"field": f.field, "label": f.label} for f in c.filters]
            ),
            "effective_download_group": effective_download_group(c, _DL_SUFFIX),
        }
        for c in configs
    ]
    return templates.TemplateResponse(
        request,
        "admin.html",
        {
            "nav_reports": [],
            "active_report_id": "",
            "reports": reports_admin,
            "views": views,
            "dl_suffix": _DL_SUFFIX,
            "current_disclaimer": current_disclaimer,
            "audit_columns": audit_columns,
            "audit_rows": audit_rows,
            "user_is_admin": True,
            "app_version": _env("APP_VERSION", "0.0.0"),
        },
    )


@app.post("/admin/preview")
async def admin_preview(request: Request) -> Response:
    """Run an admin's source query (OBO) and return its columns + sample rows.

    Feeds the report builder's column/filter picker. The query is validated as a
    single statement and wrapped as a subquery with a small ``LIMIT``. Runs AS
    THE ADMIN (OBO) so only data the admin can read is previewed.

    Args:
        request: The incoming request; form carries ``source_query`` (and an
            optional ``limit``).

    Returns:
        A JSON ``{"columns": [...], "rows": [[...], ...]}`` payload, or
        ``{"error": "..."}`` with a 400/503 status.
    """
    token, _email, _me_user = await _require_admin(request)
    form = await request.form()
    source_query = str(form.get("source_query", ""))
    try:
        limit = int(str(form.get("limit", "50")))
    except (TypeError, ValueError):
        limit = 50
    try:
        sql = build_preview_query(source_query, limit)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    try:
        cols, data = await _run_sql(token, sql)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    # Stringify sample cells for safe JSON transport (numbers come back as str).
    rows = [["" if v is None else str(v) for v in row] for row in data]
    return JSONResponse({"columns": cols, "rows": rows})


@app.post("/admin/report")
async def admin_save_report(request: Request) -> Response:
    """Insert/update one ``report_config`` row (admin only; SP write).

    Args:
        request: The incoming request; form carries the full report row
            (``report_id``, ``title``, ``source_query``, ``date_field``,
            ``columns_json``, ``filters_json``, ``order_by``, ``display_order``,
            ``enabled``, ``download_group``, ``view_key``).

    Returns:
        A JSON ``{"ok": true}`` on success, or ``{"error": "..."}`` (400 invalid
        config / 503 write failed).
    """
    _token, email, _me_user = await _require_admin(request)
    form = await request.form()
    row = {
        "report_id": str(form.get("report_id", "")).strip(),
        "title": str(form.get("title", "")).strip(),
        "source_query": str(form.get("source_query", "")),
        "date_field": str(form.get("date_field", "")).strip(),
        "columns_json": str(form.get("columns_json", "") or "[]"),
        "filters_json": str(form.get("filters_json", "") or "[]"),
        "order_by": str(form.get("order_by", "")).strip(),
        "display_order": str(form.get("display_order", "1")),
        "enabled": str(form.get("enabled", "true")).strip().lower()
        in {"true", "on", "1", "yes"},
        "download_group": str(form.get("download_group", "")).strip(),
        "view_key": str(form.get("view_key", "")).strip(),
        "updated_by": email,
    }
    try:
        sql, param_dicts = build_report_config_upsert(
            _env("APP_CATALOG"), _env("APP_SCHEMA"), row
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    try:
        await _run_sql_sp(sql, [StatementParameterListItem(**d) for d in param_dicts])
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    _invalidate_registry()
    print(f"[download-hub] admin saved report_id={row['report_id']!r} by={email!r}")
    return JSONResponse({"ok": True, "report_id": row["report_id"]})


@app.post("/admin/view")
async def admin_save_view(request: Request) -> Response:
    """Insert/update one ``report_view`` row (admin only; SP write).

    Args:
        request: The incoming request; form carries ``view_key``, ``title``,
            ``display_order``, ``enabled``.

    Returns:
        A JSON ``{"ok": true}`` on success, or ``{"error": "..."}`` (400 invalid
        / 503 write failed).
    """
    _token, email, _me_user = await _require_admin(request)
    form = await request.form()
    row = {
        "view_key": str(form.get("view_key", "")).strip(),
        "title": str(form.get("title", "")).strip(),
        "display_order": str(form.get("display_order", "1")),
        "enabled": str(form.get("enabled", "true")).strip().lower()
        in {"true", "on", "1", "yes"},
        "updated_by": email,
    }
    try:
        sql, param_dicts = build_report_view_upsert(
            _env("APP_CATALOG"), _env("APP_SCHEMA"), row
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    try:
        await _run_sql_sp(sql, [StatementParameterListItem(**d) for d in param_dicts])
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    _invalidate_registry()
    print(f"[download-hub] admin saved view_key={row['view_key']!r} by={email!r}")
    return JSONResponse({"ok": True, "view_key": row["view_key"]})


@app.post("/admin/config")
async def admin_save_config(request: Request) -> Response:
    """Set the download disclaimer (System Config tab; admin only; SP write).

    Args:
        request: The incoming request; form carries ``disclaimer``.

    Returns:
        A JSON ``{"ok": true}`` on success, or ``{"error": "..."}`` (503).
    """
    _token, email, _me_user = await _require_admin(request)
    form = await request.form()
    disclaimer = str(form.get("disclaimer", ""))
    try:
        sql, param_dicts = build_app_config_upsert(
            _env("APP_CATALOG"), _env("APP_SCHEMA"),
            "download_disclaimer", disclaimer, email,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    try:
        await _run_sql_sp(sql, [StatementParameterListItem(**d) for d in param_dicts])
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    _invalidate_registry()
    print(f"[download-hub] admin updated disclaimer by={email!r}")
    return JSONResponse({"ok": True})


@app.get("/admin/audit.csv")
async def admin_audit_csv(request: Request) -> Response:
    """Download the audit log as CSV (Audit Log tab; admin only; SP read).

    Args:
        request: The incoming request.

    Returns:
        A CSV attachment of recent audit rows, or an ``error.html`` page
        (401/403) if the caller is not an authorized admin.
    """
    try:
        _token, _email, _me_user = await _require_admin(request)
    except HTTPException as exc:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"message": exc.detail, "nav_reports": [], "active_report_id": ""},
            status_code=exc.status_code,
        )
    try:
        cols, data = await _run_sql_sp_query(
            build_audit_log_query(_env("APP_CATALOG"), _env("APP_SCHEMA"), 5000)
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    # Reuse the pure CSV writer: one text column per audit field, no disclaimer.
    header_cols = [ColumnSpec(name=c, label=c, format="text") for c in cols]
    rows = [dict(zip(cols, r)) for r in data]
    csv_bytes = to_csv_bytes(header_cols, rows, "")
    fname = f"download_audit_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
