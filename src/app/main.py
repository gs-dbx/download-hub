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
import datetime
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
    parse_scim_user_id,
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
    sort_rows,
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
from render import display_rows, haystack_for, header_cells, is_numeric_format
from reports import (
    AUDIT_LOG_COLUMNS,
    CONFIG_AUDIT_COLUMNS,
    ColumnSpec,
    ReportConfig,
    ReportView,
    build_app_config_query,
    build_app_config_upsert,
    build_audit_log_query,
    build_config_audit_analytics_query,
    build_config_audit_insert,
    build_config_audit_query,
    build_config_audit_row,
    build_preview_query,
    build_report_config_query,
    build_report_config_delete,
    build_report_config_upsert,
    build_report_query,
    build_report_view_delete,
    build_report_view_query,
    build_report_view_upsert,
    parse_report_config,
    parse_report_view,
    resolve_columns,
    split_columns,
)
from reports import build_report_dates_query as build_report_dates_query_generic
from shaping import format_report_date
from volumes import (
    breadcrumbs as _vol_breadcrumbs,
    download_file as _vol_download_file,
    friendly_volume_error,
    list_dir as _vol_list_dir,
    upload_file as _vol_upload_file,
)

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

# Over the direct cap but within the spill cap, an export is written to the
# export volume (APP_EXPORT_VOLUME, a "/Volumes/…" root) as the user (OBO) and
# retrieved via GET /download/retrieve, so the app never streams a huge file in
# one blocking response. Unset APP_EXPORT_VOLUME -> no spill (413 as before).
_MAX_SPILL_ROWS = _int_env("MAX_SPILL_ROWS", 1_000_000)
_MAX_XLSX_SPILL_ROWS = _int_env("MAX_XLSX_SPILL_ROWS", 200_000)
_EXPORT_VOLUME = (os.environ.get("APP_EXPORT_VOLUME") or "").strip()

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


# Resolve a stored raw forwarded numeric SCIM id ("<user_id>@<workspace_id>") to
# the user's email for display (see auth.parse_scim_user_id). Anything else (a
# real email/name) passes through unchanged. Cached per user_id with a TTL.
_email_display_cache: dict[str, tuple[float, str]] = {}
_EMAIL_DISPLAY_TTL = 3600.0


def _display_email(value: str) -> str:
    """Resolve a stored numeric SCIM id to the user's email for display.

    If ``value`` is the numeric ``<user_id>@<workspace_id>`` form, look up the
    SCIM user by id (as the app SP) and return ``user_name``/``display_name``;
    otherwise return ``value`` unchanged (already a readable email/name). Cached
    per user_id with a TTL; degrade-safe — any SCIM error returns the original
    value (never raises), so the audit tabs always render.

    Args:
        value: The stored identity string (audit ``user_email`` / change-log
            ``actor_email``).

    Returns:
        A readable email/name when resolvable, else ``value`` unchanged.
    """
    user_id = parse_scim_user_id(value)
    if not user_id:
        return value
    now = time.time()
    hit = _email_display_cache.get(user_id)
    if hit and (now - hit[0]) < _EMAIL_DISPLAY_TTL:
        return hit[1]
    resolved = value
    try:
        u = _app_sp_client().users.get(id=user_id)
        resolved = getattr(u, "user_name", None) or getattr(u, "display_name", None) or value
    except Exception as exc:  # noqa: BLE001 - display-only; never break the page
        print(f"[download-hub] identity resolve failed for {user_id!r}: {exc}")
        resolved = value
    _email_display_cache[user_id] = (now, str(resolved))
    return str(resolved)


async def _resolve_identity_map(values: set[str]) -> dict[str, str]:
    """Resolve a set of stored identities to display emails off the event loop.

    Batches the (blocking) SCIM lookups into one worker thread; the per-id cache
    in :func:`_display_email` means each distinct id is fetched at most once per
    TTL across requests.
    """
    if not values:
        return {}
    return await asyncio.to_thread(lambda: {v: _display_email(v) for v in values})


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


async def _log_config_audit(
    actor_email: str,
    entity_type: str,
    entity_key: str,
    action: str,
    summary: str,
    payload_json: str,
) -> None:
    """Write one config-audit row as the app SP (best-effort, non-fatal).

    Logs every admin mutation (report/view/config upsert) for compliance and
    analytics. If the audit write fails or the table is missing, the error is
    logged and the caller is NOT failed — audit logging is secondary to the
    admin action itself.

    Args:
        actor_email: The admin's email.
        entity_type: The entity type (``report_config``, ``report_view``,
            ``app_config``).
        entity_key: The entity identifier.
        action: The action taken (``upsert`` or ``set``).
        summary: Short human label.
        payload_json: Compact JSON of the written values.
    """
    try:
        row = build_config_audit_row(
            actor_email=actor_email,
            entity_type=entity_type,
            entity_key=entity_key,
            action=action,
            summary=summary,
            payload_json=payload_json,
            app_version=_env("APP_VERSION", "0.0.0"),
        )
        sql, param_dicts = build_config_audit_insert(
            _env("APP_CATALOG"), _env("APP_SCHEMA"), row
        )
        await _run_sql_sp(sql, [StatementParameterListItem(**d) for d in param_dicts])
    except Exception as exc:  # noqa: BLE001 - log and swallow
        print(
            f"[download-hub] config_audit write failed (non-fatal): "
            f"{entity_type}/{entity_key}: {exc}"
        )


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
    # When any column is an aggregate, split into plain (grouped) + aggregate
    # projections; filter fields must also be grouped so in-memory filtering over
    # the snapshot still works (LOCKED: aggregation is join-safe — every non-agg
    # selected/filtered column lands in GROUP BY, see reports.build_report_query).
    select_cols: list[str] | None = None
    aggregates: list[dict] | None = None
    if report.columns:
        plain, aggs = split_columns(report.columns)
        aggregates = aggs or None
        select_cols = _dedup(plain + [f.field for f in report.filters])
    # Under aggregation, ORDER BY is only valid on a grouped (plain) column or an
    # aggregate output alias — otherwise the query errors ("not in GROUP BY").
    # Drop an unsafe order_by (client-side click-to-sort still applies).
    order_by = report.order_by
    if aggregates and order_by:
        _agg_outputs = {a["output"] for a in aggregates}
        if order_by not in (select_cols or []) and order_by not in _agg_outputs:
            order_by = None
    # Scope by date only when the report is date-scoped AND a specific date is
    # chosen; an empty date is the "All dates" sentinel (no WHERE on the date).
    scope = bool(report.date_field and date)
    sql, params = build_report_query(
        report.source_query,
        select_cols,
        report.date_field if scope else None,
        date if scope else None,
        filters=None,
        order_by=order_by,
        aggregates=aggregates,
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


# ---------------------------------------------------------------------------
# Volume-report presentation helpers (pure adapters over volumes.list_dir /
# volumes.breadcrumbs → the exact context keys volume.html / _volume_rows.html
# expect). Kept here at the I/O boundary; volumes.py stays SDK-thin.
# ---------------------------------------------------------------------------

_VOLUME_ROOT_LABEL: str = "Home"


def _email_slug(email: str) -> str:
    """Path-safe per-user folder name for spilled exports (keeps alnum . _ -)."""
    safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in (email or ""))
    return safe.strip("._-") or "unknown"


def _inline_sql(sql: str, params: list[dict]) -> str:
    """Inline bound param VALUES into a parameterized SQL string for display.

    The app runs SQL with ``:named`` placeholders + bound params; for the "SQL"
    modal we show a human-readable, copy-runnable form with the values inlined as
    quoted literals. Longest names first so ``:flt_ab`` isn't half-matched by
    ``:flt_a``. Display-only (HTML-escaped by Jinja/JSON) — never executed.
    """
    out = sql
    for p in sorted(params, key=lambda d: len(d["name"]), reverse=True):
        literal = "'" + str(p["value"]).replace("'", "''") + "'"
        out = out.replace(":" + p["name"], literal)
    return out


def _human_size(n: object) -> str:
    """Format a byte count as a short human-readable string (``""`` if unknown)."""
    try:
        size = float(n)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    if size < 0:
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


def _human_modified(v: object) -> str:
    """Format a Files-API ``last_modified`` value as a readable UTC timestamp.

    Tolerates epoch milliseconds (int/float), epoch seconds, or an
    already-formatted string; returns ``""`` when absent/unparseable.
    """
    if v is None or v == "":
        return ""
    if isinstance(v, (int, float)):
        secs = v / 1000.0 if v > 1e11 else float(v)  # ms vs s heuristic
        try:
            return datetime.datetime.utcfromtimestamp(secs).strftime("%Y-%m-%d %H:%M")
        except (OverflowError, OSError, ValueError):
            return ""
    return str(v)


def _volume_entries(listing: dict) -> list[dict]:
    """Flatten ``volumes.list_dir`` output into the ordered ``entries`` list.

    Folders first (already name-sorted by ``list_dir``), then files, each shaped
    to the ``{kind, name, subpath, size, modified}`` contract of _volume_rows.html.
    """
    entries: list[dict] = []
    for d in listing.get("folders", []):
        entries.append(
            {"kind": "folder", "name": d["name"], "subpath": d["subpath"],
             "size": "", "modified": ""}
        )
    for f in listing.get("files", []):
        entries.append(
            {
                "kind": "file",
                "name": f["name"],
                "subpath": f["subpath"],
                "size": _human_size(f.get("size_bytes")),
                "modified": _human_modified(f.get("modified")),
            }
        )
    return entries


def _volume_crumbs(volume_root: str, subpath: str) -> list[dict]:
    """Build breadcrumb dicts ``[{name, subpath}]`` (first = root labeled Home).

    Delegates path segmentation + jail validation to ``volumes.breadcrumbs`` and
    relabels the root crumb to :data:`_VOLUME_ROOT_LABEL` to match the JS, which
    rebuilds the trail from ``data-volume-root-label``.
    """
    crumbs = _vol_breadcrumbs(volume_root, subpath)
    out: list[dict] = []
    for i, c in enumerate(crumbs):
        name = _VOLUME_ROOT_LABEL if i == 0 else c["label"]
        out.append({"name": name, "subpath": c["subpath"]})
    return out


@app.get("/health")
async def health() -> dict:
    """Lightweight liveness probe (no auth).

    Returns:
        A small status dict.
    """
    return {"status": "ok"}


@app.get("/health/warehouse")
async def warehouse_health() -> dict:
    """Report the app's SQL warehouse state so the UI can show an online badge.

    Serverless warehouses auto-suspend; a cold start adds latency, so the UI
    surfaces the live state (and warns when stopped/starting). Read as the app SP
    (which has CAN_USE on the bound warehouse). Any failure is reported as
    ``unknown`` rather than raising, so the badge degrades gracefully.

    Returns:
        ``{"state": "<RAW>", "status": "running|starting|stopped|unknown",
           "label": "<human text>"}``.
    """
    wid = (os.environ.get("DATABRICKS_WAREHOUSE_ID") or "").strip()
    if not wid:
        return {"state": "UNSET", "status": "unknown", "label": "Warehouse not configured"}
    try:
        wh = await asyncio.to_thread(_app_sp_client().warehouses.get, wid)
        raw = str(getattr(wh.state, "value", wh.state) or "UNKNOWN").upper()
    except Exception as exc:  # noqa: BLE001 - badge must never break the page
        print(f"[download-hub] warehouse health check failed: {exc}")
        return {"state": "UNKNOWN", "status": "unknown", "label": "Warehouse status unavailable"}
    if raw in ("RUNNING",):
        return {"state": raw, "status": "running", "label": "Warehouse online"}
    if raw in ("STARTING",):
        return {"state": raw, "status": "starting", "label": "Warehouse starting…"}
    if raw in ("STOPPED", "STOPPING", "DELETED", "DELETING"):
        return {"state": raw, "status": "stopped", "label": "Warehouse offline (first query will start it)"}
    return {"state": raw, "status": "unknown", "label": f"Warehouse: {raw.title()}"}


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

    # Volume reports browse a UC volume (single pinned root, traverse below,
    # metadata + download only, OBO reads) instead of querying a table — dispatch
    # to the volume page and skip all the query/snapshot machinery below.
    if report.kind == "volume":
        current_path = request.query_params.get("path", "") or ""
        can_download = _resolve_can_download(me_user, report)
        entries: list[dict] = []
        try:
            listing = await asyncio.to_thread(
                _vol_list_dir, _user_client(token), report.volume_root, current_path
            )
            entries = _volume_entries(listing)
        except ValueError:
            # Escaping/invalid subpath from a hand-edited URL — fall back to root.
            current_path = ""
            try:
                listing = await asyncio.to_thread(
                    _vol_list_dir, _user_client(token), report.volume_root, ""
                )
                entries = _volume_entries(listing)
            except Exception as exc:  # noqa: BLE001 - surfaced via log; empty state
                print(f"[download-hub] volume list failed report_id={report.report_id!r}: {exc}")
        except Exception as exc:  # noqa: BLE001 - surfaced via log; empty state
            print(f"[download-hub] volume list failed report_id={report.report_id!r}: {exc}")
        return templates.TemplateResponse(
            request,
            "volume.html",
            {
                "nav_reports": nav_reports,
                "active_report_id": report.report_id,
                "report": report,
                "current_path": current_path,
                "root_label": _VOLUME_ROOT_LABEL,
                "breadcrumbs": _volume_crumbs(report.volume_root, current_path),
                "entries": entries,
                "can_download": can_download,
                "disclaimer": await _effective_disclaimer(),
                "app_version": app_version,
                "view_switcher": view_switcher,
                "active_view_key": active_view,
                "user_is_admin": user_is_admin,
            },
        )

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
    # Optional click-to-sort: only a known display column is honored; numeric
    # columns (int/pct/float/double) sort by value, others as text. Unknown key
    # -> no sort. is_numeric_format is the shared source of truth with alignment.
    sort_key = request.query_params.get("sort", "")
    sort_dir = request.query_params.get("dir", "asc")
    if sort_key:
        col = next((c for c in columns if c.name == sort_key), None)
        if col is not None:
            searched = sort_rows(
                searched,
                sort_key,
                sort_dir if sort_dir in ("asc", "desc") else "asc",
                numeric=is_numeric_format(col.format),
            )
    page_rows, total_rows, total_pages = paginate(searched, page, size)
    cells = display_rows(columns, page_rows)

    resp = templates.TemplateResponse(request, "_rows.html", {"rows": cells})
    resp.headers["X-Total-Rows"] = str(total_rows)
    resp.headers["X-Total-Pages"] = str(total_pages)
    resp.headers["X-Page"] = str(max(1, min(page, total_pages)))
    resp.headers["X-Fetched-At"] = _fmt_ts(snap.fetched_at)
    return resp


@app.get("/report/{report_id}/sql")
async def report_sql(request: Request, report_id: str) -> Response:
    """Return the effective SQL for the current view (query reports only).

    Reflects the on-screen state: date scope + the active filters (as WHERE
    clauses) + sort + aggregation, with bound values inlined for readability.
    Note the app applies filters/search in-memory over the snapshot, so this is
    the *representative* query for the view, not byte-for-byte what executed.
    Gated by ``can_view`` so SQL isn't leaked to non-viewers.

    Raises:
        HTTPException: 401 (no token), 404 (missing/volume report), 403 (denied).
    """
    try:
        configs = await _load_reports()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    report = next((c for c in configs if c.report_id == report_id and c.enabled), None)
    if report is None or report.kind != "query":
        raise HTTPException(status_code=404, detail="no SQL for this report")
    try:
        token = extract_user_token(request.headers)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    me_user = await _me(token)
    if me_user is None or not can_view(me_user, report, _DL_SUFFIX):
        raise HTTPException(status_code=403, detail="You do not have access to this report.")

    # Rebuild the view's query WITH the active filters (unlike the snapshot read,
    # which filters in-memory) so the displayed SQL includes them.
    plain, aggs = split_columns(report.columns) if report.columns else ([], [])
    select_cols = _dedup(plain + [f.field for f in report.filters]) if report.columns else None
    filters = {
        f.field: v
        for f in report.filters
        if (v := request.query_params.get(f.field, "")).strip()
    }
    date = request.query_params.get("date", "")
    scope = bool(report.date_field and date)
    order_by = report.order_by
    if aggs and order_by:
        _outs = {a["output"] for a in aggs}
        if order_by not in (select_cols or []) and order_by not in _outs:
            order_by = None
    try:
        sql, params = build_report_query(
            report.source_query,
            select_cols,
            report.date_field if scope else None,
            date if scope else None,
            filters=filters or None,
            order_by=order_by,
            aggregates=(aggs or None),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({"sql": _inline_sql(sql, params)})


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

    # 8) Size policy. At/under the direct cap the file downloads inline. Over it
    # but within the spill cap, it's written to the export volume (OBO) and
    # retrieved separately, so the app never blocks on one huge response. Beyond
    # the spill cap — or with no export volume configured — a 413 asks to narrow.
    fmt = "xlsx" if str(form.get("format", "csv")) == "xlsx" else "csv"
    n = len(searched)
    direct_cap = _MAX_XLSX_ROWS if fmt == "xlsx" else _MAX_DOWNLOAD_ROWS
    spill_cap = _MAX_XLSX_SPILL_ROWS if fmt == "xlsx" else _MAX_SPILL_ROWS
    _csv_hint = " Or choose CSV." if fmt == "xlsx" else ""
    spill = n > direct_cap
    if spill and not _EXPORT_VOLUME:
        raise HTTPException(
            status_code=413,
            detail=(
                f"This export has {n:,} rows, above the {direct_cap:,}-row direct "
                f"download limit, and no export volume is configured. Narrow the "
                f"date or filters.{_csv_hint}"
            ),
        )
    if spill and n > spill_cap:
        raise HTTPException(
            status_code=413,
            detail=(
                f"This export has {n:,} rows, above the {spill_cap:,}-row export "
                f"limit even for volume delivery. Narrow the date or filters.{_csv_hint}"
            ),
        )

    # Build the file bytes (both paths).
    disclaimer = await _effective_disclaimer()
    if fmt == "xlsx":
        file_bytes = to_xlsx_bytes(columns, searched, disclaimer)
        media_type = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    else:
        file_bytes = to_csv_bytes(columns, searched, disclaimer)
        media_type = "text/csv"
    fname = filename_for(report.report_id, date, fmt)

    # 9) AUDIT-FIRST (NFR-5): write exactly one audit row as the app SP; the
    # INSERT must reach SUCCEEDED before the file is delivered (inline OR spilled
    # to the volume), else HTTP 500. The delivery mode is recorded in the summary.
    catalog = _env("APP_CATALOG")
    schema = _env("APP_SCHEMA")
    app_version = _env("APP_VERSION", "0.0.0")
    summary = filters_summary(selected_filters)
    if spill:
        summary = (summary + "; " if summary else "") + "delivery=volume"
    audit_row = build_audit_row(
        user_email=email,
        report_date=date,
        filter_summary=summary,
        search_filter=search,
        row_count=n,
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

    # 10a) Inline delivery (at/under the direct cap): return the attachment.
    if not spill:
        print(
            f"[download-hub] download audited: user={email!r} "
            f"report_id={report.report_id!r} audit_id={audit_row['audit_id']} "
            f"rows={n} format={fmt} date={date!r}"
        )
        return Response(
            content=file_bytes,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )

    # 10b) Spill delivery: write the file to the export volume as the user (OBO),
    # under their own per-email subfolder, then tell the UI it's ready + how to
    # retrieve it (GET /download/retrieve?path=…). Audit already succeeded above.
    subpath = f"{_email_slug(email)}/{fname}"
    try:
        vol_path = await asyncio.to_thread(
            _vol_upload_file, _user_client(token), _EXPORT_VOLUME, subpath, file_bytes
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - map SDK/Files errors to friendly text
        raise HTTPException(status_code=502, detail=friendly_volume_error(exc)) from exc
    print(
        f"[download-hub] export spilled to volume: user={email!r} "
        f"report_id={report.report_id!r} audit_id={audit_row['audit_id']} "
        f"rows={n} format={fmt} path={vol_path!r}"
    )
    return JSONResponse(
        {
            "spilled": True,
            "rows": n,
            "filename": fname,
            "volume_path": vol_path,
            "retrieve_path": subpath,
            "message": (
                f"Your export has {n:,} rows — too large to download directly, so "
                f"it was saved to the exports volume. It's ready to download now."
            ),
        }
    )


@app.get("/download/retrieve")
async def download_retrieve(request: Request, path: str) -> Response:
    """Stream a previously-spilled over-cap export from the export volume (OBO).

    ``path`` is the root-relative subpath returned by ``POST /download`` when it
    spills. Access is scoped to the requesting user's own ``{email_slug}/…``
    subfolder (defense in depth on top of the volumes.py path-jail), so users can
    only retrieve their own exports. No re-audit — the spill was already audited.

    Raises:
        HTTPException: 401 (no OBO token), 404 (no export volume), 403 (not the
            owner's path), 400 (path escapes root), 404 (file gone/unreadable).
    """
    try:
        token = extract_user_token(request.headers)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    if not _EXPORT_VOLUME:
        raise HTTPException(status_code=404, detail="No export volume is configured.")
    email = extract_user_email(request.headers)
    me_user = await _me(token)
    if me_user is not None:
        email = _readable_email(me_user, email)
    slug = _email_slug(email)
    norm = (path or "").strip().lstrip("/")
    if norm != slug and not norm.startswith(slug + "/"):
        raise HTTPException(status_code=403, detail="You can only retrieve your own exports.")
    try:
        data, fname = await asyncio.to_thread(
            _vol_download_file, _user_client(token), _EXPORT_VOLUME, norm
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - map SDK/Files errors to friendly text
        raise HTTPException(status_code=404, detail=friendly_volume_error(exc)) from exc
    if fname.endswith(".csv"):
        media_type = "text/csv"
    elif fname.endswith(".xlsx"):
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        media_type = "application/octet-stream"
    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ======================================================================
# Volume reports (browse a single-root UC volume; metadata + download only)
# ======================================================================
# Reads (list + download) run OBO so UC READ VOLUME grants enforce per-user; the
# ack+justification audit row is written as the app SP (audit-first), exactly
# like the query /download path. All browsed paths are root-relative and
# path-jailed in volumes.py (a ValueError there is a 400).


async def _resolve_volume_report(report_id: str) -> "ReportConfig":
    """Resolve an enabled volume-kind report or raise the right HTTPException.

    Raises:
        HTTPException: 503 if the registry is unreadable, 404 if the report is
            absent/disabled, 400 if it is not a volume report.
    """
    try:
        configs = await _load_reports()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    report = next(
        (c for c in configs if c.report_id == report_id and c.enabled), None
    )
    if report is None:
        raise HTTPException(status_code=404, detail=f"report {report_id!r} not found")
    if report.kind != "volume":
        raise HTTPException(status_code=400, detail="not a volume report")
    return report


@app.get("/volume/{report_id}/list", response_class=HTMLResponse)
async def volume_list(request: Request, report_id: str) -> HTMLResponse:
    """Return the folder-listing fragment for a volume report (OBO).

    Lists ``?path=`` (root-relative; ``""`` = root) under the report's pinned
    root as the signed-in user. Renders ``_volume_rows.html`` and sets
    ``X-Volume-Path`` to the resolved subpath so the client re-syncs the crumbs
    and the address bar.

    Raises:
        HTTPException: 401 (no OBO token), 403 (not allowed to view the report),
            404/400 (bad report), 400 (path escapes the root), 503 (read failed).
    """
    report = await _resolve_volume_report(report_id)
    try:
        token = extract_user_token(request.headers)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    me_user = await _me(token)
    if me_user is None or not can_view(me_user, report, _DL_SUFFIX):
        raise HTTPException(status_code=403, detail="You do not have access to this report.")

    subpath = request.query_params.get("path", "") or ""
    can_download = _resolve_can_download(me_user, report)
    try:
        listing = await asyncio.to_thread(
            _vol_list_dir, _user_client(token), report.volume_root, subpath
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - map SDK/Files errors to friendly text
        raise HTTPException(status_code=503, detail=friendly_volume_error(exc)) from exc

    resp = templates.TemplateResponse(
        request,
        "_volume_rows.html",
        {"entries": _volume_entries(listing), "can_download": can_download},
    )
    resp.headers["X-Volume-Path"] = subpath
    return resp


@app.post("/volume/{report_id}/download")
async def volume_download(request: Request, report_id: str) -> Response:
    """Download one file from a volume report — gated, acknowledged, audited (OBO).

    Flow mirrors ``POST /download`` (audit-first): validate OBO token (401) ->
    kill switch (403) -> resolve volume report (404/400) -> re-check the report's
    effective download group (403) -> acknowledgement + justification (400) ->
    resolve+jail the file path (400 on escape) -> read the bytes OBO -> write
    exactly one audit row as the app SP (500 if it fails, no file) -> return the
    attachment. The audit row records the file's root-relative path (in the
    filter-summary column) and byte size (in row_count).
    """
    try:
        token = extract_user_token(request.headers)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    email = extract_user_email(request.headers)

    if not downloads_enabled(os.environ.get("DOWNLOADS_ENABLED")):
        raise HTTPException(status_code=403, detail="Downloads are temporarily disabled.")

    report = await _resolve_volume_report(report_id)

    group = effective_download_group(report, _DL_SUFFIX)
    me_user = await _me(token)
    if me_user is None or not is_member(me_user, group):
        raise HTTPException(
            status_code=403, detail="You are not authorized to download this file."
        )
    email = _readable_email(me_user, email)

    form = await request.form()
    acknowledged = str(form.get("acknowledged", ""))
    justification = str(form.get("justification", ""))
    if acknowledged.strip().lower() not in _ACK_TRUTHY or not justification.strip():
        raise HTTPException(
            status_code=400,
            detail="Acknowledgement and a justification are both required.",
        )

    subpath = str(form.get("path", ""))
    try:
        file_bytes, fname = await asyncio.to_thread(
            _vol_download_file, _user_client(token), report.volume_root, subpath
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - map SDK/Files errors to friendly text
        raise HTTPException(status_code=502, detail=friendly_volume_error(exc)) from exc

    # AUDIT-FIRST: exactly one row as the app SP; must SUCCEED before the file is
    # returned. Reuse the download_audit schema — the file's root-relative path
    # goes in the filter-summary column, byte size in row_count, format "file".
    catalog = _env("APP_CATALOG")
    schema = _env("APP_SCHEMA")
    app_version = _env("APP_VERSION", "0.0.0")
    audit_row = build_audit_row(
        user_email=email,
        report_date="",
        filter_summary=f"volume_file={subpath}",
        search_filter="",
        row_count=len(file_bytes),
        export_format="file",
        justification=justification,
        app_version=app_version,
        report_id=report.report_id,
        report_title=report.title,
        source_query=report.volume_root,
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

    print(
        f"[download-hub] volume download audited: user={email!r} "
        f"report_id={report.report_id!r} audit_id={audit_row['audit_id']} "
        f"path={subpath!r} bytes={len(file_bytes)}"
    )

    return Response(
        content=file_bytes,
        media_type="application/octet-stream",
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
    # Change log: recent admin mutations (SP read; tolerate a missing table).
    config_audit_columns = list(CONFIG_AUDIT_COLUMNS)
    config_audit_rows: list[list[str]] = []
    config_audit_analytics: list[list[str]] = []
    try:
        ccols, cdata = await _run_sql_sp_query(
            build_config_audit_query(_env("APP_CATALOG"), _env("APP_SCHEMA"), 200)
        )
        cidx = {c: i for i, c in enumerate(ccols)}
        config_audit_rows = [
            ["" if row[cidx[c]] is None else str(row[cidx[c]]) for c in config_audit_columns]
            for row in cdata
        ]
        # Analytics: recent mutations by entity_type + action
        acols, adata = await _run_sql_sp_query(
            build_config_audit_analytics_query(_env("APP_CATALOG"), _env("APP_SCHEMA"), 30)
        )
        aidx = {c: i for i, c in enumerate(acols)}
        config_audit_analytics = [
            [
                row[aidx.get("entity_type", 0)] or "",
                row[aidx.get("action", 1)] or "",
                str(row[aidx.get("n", 2)] or 0),
            ]
            for row in adata
        ]
    except RuntimeError:
        config_audit_rows = []  # no config_audit table / unreadable -> empty tab

    # Show emails, not the raw numeric SCIM id, in both logs. Resolve the DISTINCT
    # ids once (cached), then remap the identity column in each row.
    _uidx = audit_columns.index("user_email") if "user_email" in audit_columns else -1
    _cidx = (
        config_audit_columns.index("actor_email")
        if "actor_email" in config_audit_columns
        else -1
    )
    _ids: set[str] = set()
    if _uidx >= 0:
        _ids |= {r[_uidx] for r in audit_rows if r[_uidx]}
    if _cidx >= 0:
        _ids |= {r[_cidx] for r in config_audit_rows if r[_cidx]}
    _emap = await _resolve_identity_map(_ids)
    if _uidx >= 0:
        for r in audit_rows:
            r[_uidx] = _emap.get(r[_uidx], r[_uidx])
    if _cidx >= 0:
        for r in config_audit_rows:
            r[_cidx] = _emap.get(r[_cidx], r[_cidx])

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
            "config_audit_columns": config_audit_columns,
            "config_audit_rows": config_audit_rows,
            "config_audit_analytics": config_audit_analytics,
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
        "kind": str(form.get("kind", "query")).strip() or "query",
        "volume_root": str(form.get("volume_root", "")).strip(),
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
    # Log the mutation (non-fatal)
    payload = {k: v for k, v in row.items() if k != "updated_by"}
    await _log_config_audit(
        actor_email=email,
        entity_type="report_config",
        entity_key=row["report_id"],
        action="upsert",
        summary=row["title"],
        payload_json=json.dumps(payload),
    )
    return JSONResponse({"ok": True, "report_id": row["report_id"]})


@app.post("/admin/report/delete")
async def admin_delete_report(request: Request) -> Response:
    """Delete one ``report_config`` row (admin only; SP write).

    The admin console posts this as a full-page form submit (not fetch), so on
    success it redirects back to ``/admin`` (303). Mirrors ``admin_save_report``:
    admin-gated, SP write, registry cache invalidated, mutation audited.

    Args:
        request: The incoming request; form carries ``report_id``.

    Returns:
        A 303 redirect to ``/admin`` on success; a JSON ``{"error": ...}`` with
        400 (bad id) / 503 (write failed).
    """
    _token, email, _me_user = await _require_admin(request)
    form = await request.form()
    report_id = str(form.get("report_id", "")).strip()
    try:
        sql, param_dicts = build_report_config_delete(
            _env("APP_CATALOG"), _env("APP_SCHEMA"), report_id
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    try:
        await _run_sql_sp(sql, [StatementParameterListItem(**d) for d in param_dicts])
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    _invalidate_registry()
    print(f"[download-hub] admin deleted report_id={report_id!r} by={email!r}")
    await _log_config_audit(
        actor_email=email,
        entity_type="report_config",
        entity_key=report_id,
        action="delete",
        summary="",
        payload_json="{}",
    )
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/view/delete")
async def admin_delete_view(request: Request) -> Response:
    """Delete one ``report_view`` row (admin only; SP write).

    Full-page form submit → 303 redirect to ``/admin`` on success. Mirrors
    ``admin_delete_report``: admin-gated, SP write, registry cache invalidated,
    mutation audited. Deleting a view does not touch reports bound to it (they
    fall back to their ``view_key`` label until reassigned/removed).

    Args:
        request: The incoming request; form carries ``view_key``.

    Returns:
        A 303 redirect to ``/admin`` on success; a JSON ``{"error": ...}`` with
        400 (bad key) / 503 (write failed).
    """
    _token, email, _me_user = await _require_admin(request)
    form = await request.form()
    view_key = str(form.get("view_key", "")).strip()
    try:
        sql, param_dicts = build_report_view_delete(
            _env("APP_CATALOG"), _env("APP_SCHEMA"), view_key
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    try:
        await _run_sql_sp(sql, [StatementParameterListItem(**d) for d in param_dicts])
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    _invalidate_registry()
    print(f"[download-hub] admin deleted view_key={view_key!r} by={email!r}")
    await _log_config_audit(
        actor_email=email,
        entity_type="report_view",
        entity_key=view_key,
        action="delete",
        summary="",
        payload_json="{}",
    )
    return RedirectResponse("/admin", status_code=303)


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
    # Log the mutation (non-fatal)
    payload = {k: v for k, v in row.items() if k != "updated_by"}
    await _log_config_audit(
        actor_email=email,
        entity_type="report_view",
        entity_key=row["view_key"],
        action="upsert",
        summary=row["title"],
        payload_json=json.dumps(payload),
    )
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
    # Log the mutation (non-fatal)
    await _log_config_audit(
        actor_email=email,
        entity_type="app_config",
        entity_key="download_disclaimer",
        action="set",
        summary="download_disclaimer",
        payload_json=json.dumps({"download_disclaimer": disclaimer}),
    )
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
