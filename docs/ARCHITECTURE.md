# Architecture

## Request flow

```
Browser
  ↓
FastAPI route (main.py)
  ├─ Extract OBO token from X-Forwarded-Access-Token header
  ├─ For data reads:
  │   └─ Build per-request WorkspaceClient(auth_type="pat")
  │      └─ Execute SELECT on warehouse as the user (OBO)
  │         └─ Unity Catalog enforces user's access
  │
  └─ For registry reads:
      └─ Use cached (TTL ~300s) service-principal client
         └─ Execute SELECT as the app service principal
            └─ Parse report_config rows
            └─ Sort by display_order

Per-user snapshot cache
  ├─ Key: (user_email, report_id, date)
  ├─ Columns: display_cols ∪ filter_fields (de-duplicated)
  └─ Scope: WHERE date_field = :report_date (value bound)

Apply filters / search / paginate
  ├─ Filter: WHERE field = selected_value (in-memory, no re-query)
  ├─ Search: haystack concat of searchable columns, text search
  └─ Paginate: slice rows per page_size

Render
  ├─ Full page (report.html) on first load or tab change
  ├─ Fragment (_rows.html) on filter/search/page change (HTMX)
  └─ Response headers carry totals + metadata (X-Total-Rows, etc.)

Download
  ├─ 1. Extract OBO token, email
  ├─ 2. Re-check kill switch (DOWNLOADS_ENABLED)
  ├─ 3. Resolve report from registry
  ├─ 4. Re-check group membership (server-side defense)
  ├─ 5. Validate ack + justification
  ├─ 6. Validate date against OBO date list
  ├─ 7. Get per-user snapshot (OBO read)
  ├─ 8. Apply filters + search (in-memory, ALL rows, no pagination)
  ├─ 9. Build CSV/XLSX file bytes
  ├─ 10. Write audit row (as app SP, BEFORE returning file)
  └─ 11. Return file attachment (or HTTP 500 if audit fails)
```

## Module map — Pure vs. I/O boundary

### I/O Boundary: `src/app/main.py`

The ONLY place where the SDK, templates, async/await, and HTTP semantics appear.

**Routes:**
- `GET /health` — liveness probe (no auth)
- `GET /` — redirect to the first report the user can see
- `GET /report/{report_id}` — full page render (tabs = current view; view switcher)
- `GET /report/{report_id}/table` — fragment render (filters/search/page apply)
- `POST /download` — export + audit (audit-first, size-capped)
- `GET /admin` — admin console (Report Views / Reports / System Config / Audit Log)
- `POST /admin/view` — upsert a `report_view` row (SP write)
- `POST /admin/report` — upsert a `report_config` row (SP write)
- `POST /admin/preview` — run an admin query OBO, return columns + sample rows (JSON)
- `POST /admin/config` — set the download disclaimer in `app_config` (SP write)
- `GET /admin/audit.csv` — download the audit log as CSV (SP read)

**Key functions:**
- `_user_client(token)` — build fresh per-request `WorkspaceClient` with `auth_type="pat"` (OBO reads)
- `_app_sp_client()` — cached `WorkspaceClient()` for SP reads/writes (registry, audit, admin)
- `_exec()` — wrapper around `statement_execution.execute_statement()` with `asyncio.to_thread()`; pages **all** result chunks (no truncation)
- `_run_sql()` / `_run_sql_sp_query()` / `_run_sql_sp()` — execute OBO / SP-read / SP-write
- `_load_reports()` / `_load_views()` / `_load_app_config()` — TTL-cached registry/view/config reads (SP)
- `_effective_disclaimer()` — disclaimer from `app_config`, else env, else default
- `_ensure_snapshot()` — read/cache per-user snapshot (date scope only when a date is chosen)
- `_me()` / `_readable_email()` — resolve identity once; readable email from `me().user_name`
- `_visible_reports()` / `_views_for_user()` — view visibility + switcher descriptors
- `_resolve_can_download()` — kill switch + download-group membership
- `_require_admin()` — 401/403 gate for the admin routes (membership of `ADMIN_GROUP`)

### Pure modules (no SDK, no async, fully unit-testable)

**`config.py`** — Branding & kill-switch helpers
- `app_name()` — resolve APP_NAME from env
- `app_logo()` — resolve APP_LOGO from env
- `app_org_name()` — resolve APP_ORG_NAME from env
- `downloads_enabled()` — parse DOWNLOADS_ENABLED kill-switch
- `resolve_disclaimer()` — resolve DOWNLOAD_DISCLAIMER

**`auth.py`** — Token, email, group membership
- `extract_user_token()` — read X-Forwarded-Access-Token header (raise 401 if absent)
- `extract_user_email()` — read X-Forwarded-User header (fallback to "")
- `is_member()` / `group_display_names()` — group membership from a `me()` object
- `effective_view_group()` — the report's view group (its `view_key`)
- `derive_download_group()` — `<view_key>` + suffix
- `effective_download_group()` — explicit `download_group`, else derived from `view_key`
- `can_view()` — member of the view group OR the download group
- `is_admin()` — member of `ADMIN_GROUP`

**`reports.py`** — Config model & query builders
- `parse_report_config()` / `parse_report_view()` — parse rows → `ReportConfig` / `ReportView`
- `resolve_columns()` — effective display columns (configured wins; else all query columns)
- `validate_identifier()` / `validate_query()` — allowlist-regex identifiers; single-statement query
- `build_report_query()` — parameterized SELECT wrapping the `source_query` subquery
- `build_report_dates_query()` / `build_distinct_values_query()` — date list / distinct filter values
- `build_report_config_query()` / `build_report_view_query()` — registry reads
- `build_report_config_upsert()` / `build_report_view_upsert()` — admin MERGE writes (bound params)
- `build_preview_query()` — row-limited preview of an admin query
- `build_app_config_query()` / `build_app_config_upsert()` — system-config key/value read/write
- `build_audit_log_query()` + `AUDIT_LOG_COLUMNS` — audit-log read for the admin tab/CSV

**`errors.py`** — Explicit error classification
- `friendly_error()` — map a raw DB/SDK error to concise user-facing text
- `ReportDataError` — RuntimeError subclass carrying the friendly message

**`cache.py`** — Snapshot cache & filtering
- `SnapshotCache` — bounded LRU (max 128 entries)
- `apply_filters()` — filter rows in-memory
- `apply_search()` — search haystack in-memory
- `paginate()` — slice rows per page
- `distinct_values()` — extract distinct column values
- `filters_summary()` — format filter selections for audit

**`exports.py`** — CSV/XLSX builders
- `to_csv_bytes()` — build CSV file bytes (with disclaimer header)
- `to_xlsx_bytes()` — build XLSX file bytes (with disclaimer sheet)
- `filename_for()` — generate filename (report_id + date + format)
- `DEFAULT_DISCLAIMER` — built-in generic data-handling notice

**`render.py`** — Cell formatting & display
- `display_rows()` — format rows for HTML render (apply column format hints)
- `header_cells()` — build table header descriptors
- `haystack_for()` — concatenate searchable columns into haystack

**`shaping.py`** — Format helpers
- `format_count()` — format as thousands-separated integer
- `format_pct()` — format as signed one-decimal percentage
- `format_report_date()` — format timestamp as date string

**`audit.py`** — Audit row builders
- `build_audit_row()` — construct audit record dict
- `build_audit_insert()` — build parameterized INSERT query

---

## Caching model

### Per-user snapshot cache

- **Type:** Bounded LRU (max 128 entries)
- **Key:** `(user_email, report_id, report_date)`
- **Columns:** display_cols ∪ filter_fields (de-duplicated, ordered by display_order + filter list order)
- **Scope:** `WHERE {date_field} = :report_date` (value bound)
- **Lifetime:** In-process, evicted when:
  - Cache reaches capacity (LRU evicts oldest)
  - User passes `refresh=1` query param
  - Server restarts

**Why:** Filters, search, and pagination run over the snapshot in-memory (no re-query). Every page interaction or filter change is fast. The snapshot is scoped to a single date, so it fits in memory even for large reports (typically 10k–100k rows per date).

### Report registry cache

- **Type:** In-process tuple `(timestamp, list[ReportConfig])`
- **TTL:** ~300 seconds
- **Refresh:** On every `_load_reports()` call, check age; if older than TTL, re-read from registry
- **Read identity:** App service principal (not the user)
- **Lifetime:** In-process, survives across requests; reset on server restart

**Why:** The registry is read once per app startup, then reused. If an admin edits the registry, it appears within 5 minutes (TTL) — **or immediately**, because every admin write (`/admin/*`) invalidates the caches. Restarting the app also clears them.

The **view registry** (`report_view`) and **system config** (`app_config`) are cached the same way (same TTL, same invalidation). The per-user snapshot is date-scoped only when a date is chosen; an "All dates" selection reads the whole result (no date `WHERE`).

### Registry & support tables

| Table | Read as | Written by | Purpose |
|-------|---------|-----------|---------|
| `report_config` | SP | admin (`/admin/report`) | report definitions (`source_query`, columns, filters, `view_key`, …) |
| `report_view` | SP | admin (`/admin/view`) | views (switcher): `view_key`, `title`, `display_order`, `enabled` |
| `app_config` | SP | admin (`/admin/config`) | key/value system config (e.g. `download_disclaimer`) |
| `download_audit` | SP (audit tab) | SP (each download) | one audit row per download (incl. `source_query`, `filter_summary`) |

---

## Auth model

### On-behalf-of-user (OBO) reads

1. Databricks Apps runtime injects the user's OAuth access token in the `X-Forwarded-Access-Token` request header.
2. App extracts the token: `token = request.headers["X-Forwarded-Access-Token"]`
3. App builds a per-request `WorkspaceClient(config=Config(token=token, auth_type="pat"))`
4. Every data read (report source, date list) runs through this client → executes AS THE USER on the warehouse
5. Unity Catalog enforces the user's own SELECT access
6. **No fallback:** if the header is absent, the request fails with 401 (no CLI profile, no mock data)

**Why `auth_type="pat"` is required:**
The Databricks Apps runtime also injects the app service principal's OAuth credentials (`DATABRICKS_CLIENT_ID`, `DATABRICKS_SECRET`) into the environment. The SDK would see both the token and the SP creds and refuse to initialize with "more than one authorization method configured". Pinning `auth_type="pat"` forces the SDK to use the user's token, giving OBO.

### Service-principal reads & writes

1. App builds a single cached `WorkspaceClient()` with NO explicit config and NO `auth_type` parameter
2. SDK auto-detects the Databricks Apps runtime's injected `DATABRICKS_HOST`, `DATABRICKS_CLIENT_ID`, `DATABRICKS_SECRET`
3. All registry reads and audit writes run as the app service principal
4. Registry read is TTL-cached; audit write is audit-first (immediate, synchronous)

**Why separate identities:** The registry and audit table are owned by the app; the data tables are owned by the business. Splitting identities lets us grant the app minimal permissions: SELECT on report_config + INSERT on download_audit; the app grants no direct data access (all data reads are OBO).

### Download gating

Download is allowed only when BOTH conditions are true:

1. **Kill switch:** `downloads_enabled(DOWNLOADS_ENABLED)` is true (default true; false for `false`/`0`/`no`/`off`/empty)
2. **Group membership:** user is a member of the report's **effective download group**
   - If `report.download_group` is set (non-NULL, non-empty after strip), that's the effective group
   - Otherwise it is **derived** as `<view_key>` + `DOWNLOAD_GROUP_SUFFIX` (default `_dl`)

The membership check is re-done server-side on every `POST /download` (defense in depth). The UI panel is never trusted on its own.

**View visibility** is a separate, broader gate: a report's tab is shown when the user is a member of its view group (`view_key`) **OR** its download group (`can_view`). Admin routes are gated by membership of `ADMIN_GROUP` (`_require_admin`), and admin writes run as the app service principal.

**Export size caps:** downloads are built in memory, so they are capped by `MAX_DOWNLOAD_ROWS` (CSV, default 100000) and `MAX_XLSX_ROWS` (default 25000); over the cap the download returns HTTP 413 asking the user to narrow filters or choose CSV.

---

## Query safety

### Values: always bound

All user-provided values (date, filter selections, search text) are ALWAYS bound as `:named` parameters via the Statement Execution API.

Example:
```python
sql = "SELECT ... WHERE report_date = :report_date AND channel = :flt_channel"
params = [
    {"name": "report_date", "value": "2026-08-14", "type": "TIMESTAMP"},
    {"name": "flt_channel", "value": "ALL", "type": "STRING"}
]
client.statement_execution.execute_statement(statement=sql, parameters=params, ...)
```

No string interpolation, no SQL injection possible.

### Identifiers: validated then interpolated

All identifiers (column names, filter fields, `order_by`) come from admin-authored config and must be validated before interpolation. The `source_query` is admin-authored SQL: it is validated to be a single statement and wrapped as a subquery, but not otherwise parsed.

Example:
```python
# Identifiers from config — validated against the allowlist regex
col = validate_identifier(column_name)      # raises ValueError if bad
# source_query — validated single statement, wrapped as a subquery
src = f"( {validate_query(source_query)} ) AS _q"
sql = f"SELECT {col} FROM {src}"
```

The allowlist regex is strict: `^[A-Za-z_][A-Za-z0-9_]*$`. No hyphens, no leading digits, no special characters. A bad identifier is caught immediately, raising `ValueError` at query-build time (not runtime).

---

## Audit-first

Every download follows this order:

1. Validate the OBO token (401)
2. Check kill switch (403)
3. Resolve the report (404)
4. Re-check group membership (403, server-side)
5. Validate ack + justification (400)
6. Validate date (400)
7. Get per-user snapshot (503 if OBO read fails)
8. Apply filters + search
9. Build file bytes (CSV/XLSX)
10. **Write exactly one audit row as the app SP** (must SUCCEED before returning file)
11. Return file attachment

If step 10 fails for ANY reason (permission denied, table missing, network error, etc.), the download is blocked (HTTP 500, no file). This is audit-first and non-negotiable: no audit row = no file.

An app-log line is also emitted (`databricks apps logs`) so the event surfaces in monitoring.

---

## Offline / air-gap

All production dependencies and assets are committed to the repo:

| Area | Files |
|------|-------|
| **Wheels** | `src/app/wheelhouse/` (linux/CPython-3.11, ~40 packages) |
| **Front-end** | `src/app/static/uswds/` (USWDS CSS/JS/fonts), `static/css/app.css`, `static/js/app.js`, `static/img/logo.svg` |
| **Config** | `src/app/requirements.txt` (points to `./wheelhouse` with `--no-index --find-links`) |

**Install offline:**
```bash
pip install --no-index --find-links src/app/wheelhouse -r requirements.lock
```

No PyPI contact, no mirror needed.

**Guard:** `tests/test_branding_guards.py` fails if any external URL (https://, //, cdn., unpkg.) appears in authored templates/CSS/JS. This is a hard rule enforced by CI.

**Asset cache-busting:** `app.css`, `app.js`, and `admin.js` are referenced with `?v=<hash>`, where `<hash>` is a content hash of those files computed at startup. A redeploy that changes them changes the query string, so browsers refetch automatically — no manual version bump. A whole-page copy/paste deterrent (CSS `user-select:none` + JS blocking copy/cut/contextmenu/dragstart, form inputs exempt) is also applied client-side; it is a deterrent only.

---

## ASCII diagram — Request flow

```
User's browser                     Databricks workspace
├─ GET /report/daily_metrics
│  └─ Databricks Apps runtime
│     └─ HTTP header: X-Forwarded-Access-Token = <user-oauth-token>
│        ↓
│        FastAPI main.py (route: GET /report/{report_id})
│        ├─ Extract token from header
│        ├─ Load report_config (SP client, TTL-cached)
│        │  └─ SQL warehouse (as app SP): SELECT * FROM report_config
│        ├─ Read dates (OBO user client)
│        │  └─ SQL warehouse (as user): SELECT DISTINCT date FROM table
│        ├─ Ensure per-user snapshot
│        │  └─ SQL warehouse (as user): SELECT cols ∪ filters FROM table WHERE date = :date
│        │     → cached in _snapshot_cache[(user, report, date)]
│        ├─ Apply filters in-memory (no re-query)
│        ├─ Render report.html full page
│        └─ Return HTML
│           ↓
│        <tr>, <td> cells, filters dropdown, date selector
│           ↓
│        User selects filter / types search → GET /report/daily_metrics/table?...
│           └─ FastAPI main.py (route: GET /report/{report_id}/table)
│              ├─ Extract token
│              ├─ Validate requested date
│              ├─ Get per-user snapshot (cache hit)
│              ├─ Apply filter/search/pagination in-memory
│              ├─ Render _rows.html fragment
│              └─ Return HTML (via HTMX, swap into <tbody>)
│
├─ POST /download (form submit)
│  └─ FastAPI main.py (route: POST /download)
│     ├─ Extract token + email
│     ├─ Check kill switch
│     ├─ Resolve report
│     ├─ Re-check group membership (OBO me() call)
│     ├─ Validate ack + justification
│     ├─ Get per-user snapshot (cache hit)
│     ├─ Apply filters/search (all rows, no pagination)
│     ├─ Build CSV/XLSX bytes
│     ├─ Write audit row
│     │  └─ SQL warehouse (as app SP): INSERT INTO download_audit VALUES (...)
│     └─ Return file attachment
│        ↓
│     downloads/daily_metrics_2026-08-14.csv
```

---

## Decisions (locked)

| Decision | Rationale |
|----------|-----------|
| OBO reads as user | Enforces UC access control; prevents privilege escalation |
| Per-user snapshot cache | Filters/search/paging are fast in-memory; no re-query per interaction |
| Report registry TTL-cached | Config changes appear within 5 min; app startup is fast |
| Audit-first | No audit row = no file; ensures every download is logged before export |
| Config-driven reports | Zero code change to add/edit/reorder reports; registry is the source of truth |
| Air-gap (no CDN) | Required for deployment in restricted networks; all assets committed |
| Identifier validation (allowlist regex) | SQL injection protection; catches config errors early, not at runtime |
| Value binding (params, not interpolation) | SQL injection protection; parameters are safe |
