# Architecture

## Report kinds

A `report_config` row is one of two **kinds** (the `kind` column, default `query`):

- **`query`** — reads a SQL `SELECT` (`source_query`) OBO with **server-side SQL paging**: configured filters/search/sort are pushed into SQL and each interaction runs a `COUNT(*)` (pager total) + one `LIMIT`/`OFFSET` page, so a large report never materializes in the app. Offers a gated CSV/XLSX download. Columns may be aggregated (`AGG(source)` + join-safe `GROUP BY`).
- **`volume`** — browses a pinned UC Volume root (`volume_root`) via the Files API OBO: folders-first listing, breadcrumb traversal (path-jailed to the root in `volumes.py`), and per-file gated download. Same acknowledgement + justification + audit-first write as query downloads. Routes: `GET /volume/{id}/list`, `POST /volume/{id}/download`.

Both kinds share the group-based visibility/download gating and the audit table.

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

Server-side SQL paging (per interaction, OBO)
  ├─ COUNT(*) over ( source_query [+ configured filters] ) → pager total
  ├─ One page: SELECT ... FROM ( source_query ) [WHERE filters + search]
  │            ORDER BY <sort> LIMIT <size> OFFSET <page*size>
  ├─ Values bound (date/filter/search); identifiers allowlist-validated; LIMIT/OFFSET clamped
  └─ Filter dropdowns: a distinct-values query per filter field

Render
  ├─ Full page (report.html) on first load or tab change
  ├─ Fragment (_rows.html) on filter/search/sort/page change (JS fetch)
  └─ Response headers carry totals + metadata (X-Total-Rows, etc.)

Download
  ├─ 1. Extract OBO token, email
  ├─ 2. Re-check kill switch (DOWNLOADS_ENABLED)
  ├─ 3. Resolve report from registry
  ├─ 4. Re-check group membership (server-side defense)
  ├─ 5. Validate ack + justification
  ├─ 6. Validate date against OBO date list
  ├─ 7. Count the filtered/searched result OBO
  ├─ 8. Size policy: inline vs. spill-to-volume vs. 413
  ├─ 9. Build within the direct cap, or page a large CSV to disk + volume
  ├─ 10. Write audit row (as app SP, BEFORE returning file)
  └─ 11. Return an attachment or streamed retrieval link (or HTTP 500 if audit fails)
```

## Module map — Pure vs. I/O boundary

### I/O Boundary: `src/app/main.py`

The ONLY place where the SDK, templates, async/await, and HTTP semantics appear.

**Routes:**
- `GET /health` — liveness probe (no auth)
- `GET /` — redirect to first enabled report
- `GET /report/{report_id}` — full page render
- `GET /report/{report_id}/table` — fragment render (filters/search/page apply)
- `POST /download` — export + audit (audit-first)

**Key functions:**
- `_user_client(token)` — build fresh per-request `WorkspaceClient` with `auth_type="pat"` (OBO reads)
- `_app_sp_client()` — cached `WorkspaceClient()` for SP reads/writes (registry, audit)
- `_exec()` — wrapper around `statement_execution.execute_statement()` with `asyncio.to_thread()`
- `_run_sql()` — execute OBO (as user)
- `_run_sql_sp_query()` — execute as SP (registry read)
- `_run_sql_sp()` — execute as SP (audit write)
- `_load_reports()` — read report registry, parse configs, TTL-cache
- `_query_report_page()` — run `COUNT(*)` + one `LIMIT`/`OFFSET` page OBO (server-side paging)
- `_query_report_rows()` — fetch a bounded page without repeating the count (large CSV staging)
- `_report_filter_options()` — distinct values per filter field (OBO)
- `_resolve_can_download()` — check kill switch + group membership

### Pure modules (no SDK, no async, fully unit-testable)

**`config.py`** — Branding & kill-switch helpers
- `app_name()` — resolve APP_NAME from env
- `app_logo()` — resolve APP_LOGO from env
- `app_org_name()` — resolve APP_ORG_NAME from env
- `downloads_enabled()` — parse DOWNLOADS_ENABLED kill-switch
- `resolve_disclaimer()` — resolve DOWNLOAD_DISCLAIMER

**`auth.py`** — Token, email, group membership
- `extract_user_token()` — read X-Forwarded-Access-Token header (raise 401 if absent)
- `extract_user_email()` — read X-Forwarded-User header (fallback to "unknown")
- `is_member()` — check if user is in a Databricks group
- `effective_download_group()` — resolve report's download_group or fall back to default

**`reports.py`** — Config model & query builders
- `parse_report_config()` — parse row dict → ReportConfig dataclass (kind, source_query, volume_root, columns incl. aggregates)
- `validate_identifier()` — check identifier against allowlist regex
- `validate_volume_root()` — validate a volume report's pinned `/Volumes/…` root
- `normalize_kind()` / `normalize_format()` / `normalize_agg()` — validate the enums (query|volume; text/int/float/pct; sum/min/avg/max/first/last)
- `split_columns()` — split display columns into plain (grouped) + aggregate projections
- `build_report_query()` — build parameterized SELECT (optional `aggregates` → `AGG(source)` + join-safe `GROUP BY`)
- `build_report_config_query()` / `build_report_config_upsert()` / `build_report_config_delete()` — registry read/write/delete builders

**`cache.py`** — Pure helpers + small caches
- `BoundedTTLCache` — bounded, TTL-expiring cache (used for SCIM-id → email display lookups)
- `filters_summary()` — format filter selections for audit
- `SnapshotCache` / `apply_filters()` / `apply_search()` / `paginate()` — tested pure utilities retained here, but NOT the runtime data path (the display path is server-side SQL paging)

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
- `format_int()` — format as thousands-separated int
- `format_pct()` — format as signed one-decimal percentage

**`audit.py`** — Audit row builders
- `build_audit_row()` — construct audit record dict (filter_summary, source_query, report_id/title)
- `build_audit_insert()` — build parameterized INSERT query

**`volumes.py`** — Volume browsing (path-jail + OBO Files API)
- `resolve_within_root()` — the security boundary: jail a root-relative subpath to the pinned root (reject `..`/absolute/backslash/sibling-prefix escapes)
- `breadcrumbs()` — build the root→current crumb trail
- `list_dir()` — OBO folder listing (folders + files with size/modified), root-relative subpaths
- `download_file()` — OBO file read (refuses directories); returns (bytes, filename)
- `friendly_volume_error()` — map Files-API errors to concise user text

**`errors.py`** — User-facing error classification
- `friendly_error()` — map a raw DB/SDK error to a concise message + safe `(Details: …)` excerpt (genuine authorization phrases only → permission message)
- `ReportDataError` — RuntimeError carrying already-friendly text

---

## Caching model

### Display reads — server-side SQL paging (no cache)

The interactive path does **not** cache result rows. Each interaction (load, filter,
search, sort, page) runs, OBO:

- a `COUNT(*)` over the report query (with active filters/search) for the pager total, and
- one page — `SELECT ... FROM ( source_query ) [WHERE …] ORDER BY <sort> LIMIT <size> OFFSET <page*size>`.

`refresh=1` simply re-runs the query. Because only one page is ever fetched, a
multi-million-row report never materializes in the app. Trade-offs to know:
deep `OFFSET` pays a rescan (keyset pagination is a future optimization); `ORDER BY`
benefits from a unique tiebreaker for perfectly stable page boundaries; and a
`COUNT(*)` runs per interaction (cache/approx-count is a possible optimization on
very large sources).

### Report registry cache

- **Type:** In-process tuple `(timestamp, list[ReportConfig])`
- **TTL:** ~300 seconds
- **Refresh:** On every `_load_reports()` call, check age; if older than TTL, re-read from registry
- **Read identity:** App service principal (not the user)
- **Lifetime:** In-process, survives across requests; reset on server restart

**Why:** The registry is read once per app startup, then reused. If an admin edits the registry, it appears within 5 minutes (TTL). This avoids repeated registry SELECTs while keeping the app responsive to config changes. Restart the app to pick up changes immediately.

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
   - Otherwise, derive `<view_key><DOWNLOAD_GROUP_SUFFIX>` (default suffix `_dl`)

The membership check is re-done server-side on every `POST /download` (defense in depth). The UI panel is never trusted on its own.

---

## Query safety

### Values: always bound

All user-provided filter selections and search text are ALWAYS bound as `:named` parameters via the Statement Execution API.

Example:
```python
sql = "SELECT ... WHERE report_date = :flt_report_date AND channel = :flt_channel"
params = [
    {"name": "flt_report_date", "value": "2026-08-14", "type": "STRING"},
    {"name": "flt_channel", "value": "ALL", "type": "STRING"}
]
client.statement_execution.execute_statement(statement=sql, parameters=params, ...)
```

No string interpolation, no SQL injection possible.

### Identifiers: validated then interpolated

All identifiers (column names, filter fields, `order_by`, dotted parts of `source_fqn`) come from admin-authored config and must be validated before interpolation.

Example:
```python
# Identifier from config — validated against allowlist regex
fqn = validate_fqn(source_fqn)  # raises ValueError if bad
# Re-joined and safe to interpolate
sql = f"SELECT ... FROM {fqn}"
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
7. Count the filtered/searched result OBO before retrieving it (503 if the OBO read fails)
8. Size policy (inline / spill-to-volume / 413)
9. Build a direct file within its cap, or page a large CSV to temporary disk and upload it to the configured volume as a stream
10. **Write exactly one audit row as the app SP** (must SUCCEED before returning file)
11. Return the attachment or a user-scoped link that streams the saved file

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
│        ├─ Read dates + filter options (OBO user client)
│        │  └─ SQL warehouse (as user): SELECT DISTINCT … per filter field
│        ├─ COUNT + first page (OBO user client)
│        │  └─ SQL warehouse (as user): COUNT(*) + SELECT … LIMIT :size OFFSET 0
│        ├─ Render report.html full page
│        └─ Return HTML
│           ↓
│        <tr>, <td> cells, filters dropdown, date selector
│           ↓
│        User selects filter / types search → GET /report/daily_metrics/table?...
│           └─ FastAPI main.py (route: GET /report/{report_id}/table)
│              ├─ Extract token
│              ├─ Validate requested date
│              ├─ COUNT + one page via SQL (active filters/search/sort pushed down, OBO)
│              ├─ Render _rows.html fragment
│              └─ Return HTML (JS fetch swaps into <tbody>)
│
├─ POST /download (form submit)
│  └─ FastAPI main.py (route: POST /download)
│     ├─ Extract token + email
│     ├─ Check kill switch
│     ├─ Resolve report
│     ├─ Re-check group membership (OBO me() call)
│     ├─ Validate ack + justification
│     ├─ Count the filtered/searched result OBO
│     ├─ Build directly or page a large CSV to the export volume
│     ├─ Write audit row
│     │  └─ SQL warehouse (as app SP): INSERT INTO download_audit VALUES (...)
│     └─ Return file attachment or user-scoped retrieval link
│        ↓
│     downloads/daily_metrics_2026-08-14.csv
```

---

## Decisions (locked)

| Decision | Rationale |
|----------|-----------|
| OBO reads as user | Enforces UC access control; prevents privilege escalation |
| Server-side SQL paging | Filter/search/sort/paginate pushed into SQL (COUNT + LIMIT/OFFSET per request); a large report never materializes in the app |
| Report registry TTL-cached | Config changes appear within 5 min; app startup is fast |
| Audit-first | No audit row = no file; ensures every download is logged before export |
| Config-driven reports | Zero code change to add/edit/reorder reports; registry is the source of truth |
| Air-gap (no CDN) | Required for deployment in restricted networks; all assets committed |
| Identifier validation (allowlist regex) | SQL injection protection; catches config errors early, not at runtime |
| Value binding (params, not interpolation) | SQL injection protection; parameters are safe |
