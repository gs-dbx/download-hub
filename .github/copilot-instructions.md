# GitHub Copilot / AI Instructions — Data Download Hub

Guidance for AI coding assistants (GitHub Copilot in VS Code, Cursor, Claude, etc.)
working in this repo. Read this first; it reflects the current architecture.

## What this app is

A **configurable, server-rendered FastAPI** app that serves read-only reports from
Databricks SQL with per-user, group-gated downloads. **No React/npm** — Jinja2
templates + vendored USWDS + a little vanilla JS. Deployed as a **Databricks App**
via a DAB bundle (standard engine).

- **Config-driven.** Reports and views are rows in Unity Catalog registry tables
  (`report_config`, `report_view`) — an admin adds/edits them with **no code change
  or redeploy**.
- **A report = a full query.** Each report stores a `source_query` (a single
  `SELECT`); the app wraps it as a subquery and layers date scope, filters, and
  ORDER BY on top. Displayed columns default to every column the query returns.
- **Views = tab sets gated by groups.** Each report belongs to a `view_key` (a
  Databricks group). Users see the views whose group they belong to and get a
  switcher when they have more than one.
- **Per-user OBO reads + snapshot cache.** Data is read AS THE SIGNED-IN USER; a
  date-scoped snapshot is cached in-process, and filter/search/paginate run over it.
- **Audit-first downloads.** Every download writes exactly one audit row (as the app
  service principal) *before* the file is returned; if the audit write fails, the
  download is blocked.
- **Admin console** at `/admin` (admin-group gated) manages views, reports (with a
  live query builder), the disclaimer, and the audit log.
- **Air-gapped.** All wheels + assets are committed locally; no CDN, no external URLs.

## Golden rule: pure logic, I/O only at the boundary

`src/app/main.py` is the **ONLY** file allowed to do I/O (SDK calls, async, Jinja,
routes, `app.mount`). **Every other module is pure** — no SDK, no network, no async —
so it is unit-testable in a pytest-only venv. When you add logic: put it in a pure
module, write a test, then wire it into `main.py` at the boundary.

### Module map

**`src/app/main.py`** — I/O boundary. Routes:
- `GET /health` — liveness (no auth)
- `GET /` — redirect to the first report VISIBLE to the user
- `GET /report/{id}` — full report page (tabs for the active view, switcher, toolbar, table page 1)
- `GET /report/{id}/table` — `_rows.html` fragment (filter/search/paginate over the cached snapshot; totals via `X-*` headers)
- `POST /download` — export the current filtered view (gated, acknowledged, size-capped, audit-first)
- `GET /admin` — admin console (admin group only)
- `POST /admin/view` — upsert a `report_view` row (SP write)
- `POST /admin/report` — upsert a `report_config` row (SP write)
- `POST /admin/preview` — run a query OBO, return `{columns, rows}` for the builder
- `POST /admin/config` — set the download disclaimer (SP write to `app_config`)
- `GET /admin/audit.csv` — download the audit log as CSV (SP read)
- Helpers: `_run_sql` (OBO), `_run_sql_sp_query`/`_run_sql_sp` (SP), `_exec` (wraps SDK, pages ALL result chunks), `_me`, `_readable_email`, `_load_reports`/`_load_views`/`_load_app_config` (TTL caches), `_ensure_snapshot`, `_effective_disclaimer`.

**Pure modules** (no SDK/async/network — test in pytest alone):
- `reports.py` — `ReportConfig`/`ReportView`/`ColumnSpec`/`FilterSpec` dataclasses; `parse_report_config`/`parse_report_view`; `resolve_columns` (configured cols win, else all query cols); query builders (`build_report_query`, `build_report_dates_query`, `build_distinct_values_query`, `build_preview_query`); registry SELECTs (`build_report_config_query`, `build_report_view_query`, `build_app_config_query`, `build_audit_log_query` + `AUDIT_LOG_COLUMNS`); admin upserts (`build_report_config_upsert`, `build_report_view_upsert`, `build_app_config_upsert`); validators (`validate_identifier`, `validate_query`, `validate_fqn`).
- `auth.py` — token/email extraction; `group_display_names`/`is_member`; `effective_view_group`; `derive_download_group`; `effective_download_group`; `can_view` (view group OR download group); `is_admin`. Constants: `DOWNLOAD_GROUP`, `DEFAULT_DOWNLOAD_SUFFIX` (`_dl`), `ADMIN_GROUP`.
- `cache.py` — `SnapshotCache` (bounded LRU), `apply_filters`/`apply_search`/`paginate`/`distinct_values`/`filters_summary`.
- `errors.py` — `friendly_error(raw)` maps raw DB/SDK errors (missing table/column, permission denied, warehouse down, syntax, timeout) to concise user-facing text; `ReportDataError(RuntimeError)`.
- `render.py` — cell/header formatters, search haystack, from `ColumnSpec`.
- `shaping.py` — count/percent/date formatters.
- `exports.py` — CSV/XLSX builders with the disclaimer at the top; filename builder.
- `audit.py` — audit-row builder + parameterized INSERT builder.
- `config.py` — branding helpers + `downloads_enabled` kill-switch parser.

**Front-end** (`src/app/static/`, `src/app/templates/`):
- `templates/base.html` — masthead, tab row (tabs left; **view switcher + Admin top-right**), footer; loads `app.css`/`app.js` with `?v=<hash>` cache-busting.
- `templates/report.html`, `_rows.html`, `_download.html`, `admin.html`, `error.html`.
- `static/js/app.js` — report interactivity (fragment fetch, pager, spinner, fetch-based download); the whole-page copy/paste deterrent; the view switcher navigation.
- `static/js/admin.js` — admin tabs, the query-preview column/filter picker, form submits.
- `static/css/app.css` — local color overlay + layout + spinners.

## Data model (Unity Catalog, in `{APP_CATALOG}.{APP_SCHEMA}`)

- **`report_config`** — one row per report: `report_id`, `title`, `source_query`,
  `date_field` (opt), `columns_json` (opt), `filters_json` (opt), `order_by` (opt),
  `display_order`, `enabled`, `download_group` (opt), `view_key`, `updated_at`,
  `updated_by`. (`source_fqn` was **removed** — use `source_query`.)
- **`report_view`** — one row per view (switcher entry): `view_key` (= the Databricks
  group granting view access, and the URL key), `title` (switcher label),
  `display_order`, `enabled`, `updated_at`, `updated_by`.
- **`app_config`** — key/value: `config_key`, `config_value`, `updated_at`,
  `updated_by`. Currently holds `download_disclaimer`.
- **`download_audit`** — one row per download: `audit_id`, `event_ts`, `user_email`,
  `report_date`, `filter_summary`, `search_filter`, `row_count`, `export_format`,
  `justification`, `acknowledged`, `app_version`, `report_id`, `report_title`,
  `source_query`.

The seed notebook `src/notebooks/generate_daily_metrics.py` creates all of these and
carries idempotent migrations (adds `source_query`/`view_key`/`updated_by`; adds
`filter_summary` and backfills it from the legacy `drain_filter`; adds
`app_config`/`report_view`).

## Access model (views + downloads + admin)

- A report belongs to a **view** (`view_key`), which is also the Databricks group
  that grants VIEW access.
- A user **sees** a report if they are a member of its **view group OR its download
  group** (`can_view`) — so download-group members always see what they can export.
- A report's **download group** = explicit `download_group` if set, else derived as
  `view_key` + `DOWNLOAD_GROUP_SUFFIX` (default `_dl`). E.g. view `efile_ops` →
  download group `efile_ops_dl`.
- **Downloads** require membership of the download group AND the global kill switch on.
- **Admins** (members of `ADMIN_GROUP`, default `download_hub_admin_users`) use
  `/admin`. Admin **writes run as the app service principal** — the SP needs UC
  `MODIFY` on `report_config`, `report_view`, `app_config` and `SELECT` on
  `download_audit`.
- Identity for the audit row / `updated_by` is the readable email from
  `me().user_name` (the `X-Forwarded-User` header is a numeric id on some workspaces).

## Auth (OBO vs SP)

- **OBO reads** (report data, dates, admin query preview): header
  `X-Forwarded-Access-Token` → `WorkspaceClient(config=Config(host=..., token=...,
  auth_type="pat"))` → runs AS THE USER. `auth_type="pat"` is **mandatory** — the
  Apps runtime also injects the SP creds, and without pinning `pat` the SDK refuses
  to initialize ("more than one authorization method configured").
- **SP** (registry reads, admin writes, audit insert): plain `WorkspaceClient()` →
  auto-detects the injected `DATABRICKS_CLIENT_ID`/`SECRET` → runs AS THE APP.
- No fallback: missing OBO token → 401 (no CLI profile, no mock data).

## Caching

- **Snapshot cache** — bounded LRU (max 128). Key `(user_email, report_id, date)`.
  Evicted on `refresh=1`. Scoped by `date_field` when set + a specific date; empty
  date ("All dates") reads all rows.
- **Registry caches** — `report_config`, `report_view`, `app_config` each TTL-cached
  (~300s). Admin writes call `_invalidate_registry()` so changes show immediately.

## SQL safety

- **VALUES are always bound** as `:named` params (`{"name","value","type"}` dicts) —
  never interpolated. Filter/date selections, admin field values, audit values.
- **IDENTIFIERS** (column names, filter fields, `order_by`, `view_key`, `report_id`,
  config keys) are validated against `^[A-Za-z_][A-Za-z0-9_]*$` then interpolated.
- **`source_query`** is admin-authored SQL: validated to be a single statement (no
  embedded `;`) via `validate_query` and wrapped as `( ... ) AS _q`; not otherwise
  parsed. Treat write access to `report_config` as trusted (SP-only via `/admin`).

```python
# GOOD — identifier validated, value bound
col = validate_identifier(column_name)
src = f"( {validate_query(source_query)} ) AS _q"
sql = f"SELECT {col} FROM {src} WHERE {validate_identifier(date_field)} = :report_date"
params.append({"name": "report_date", "value": report_date, "type": "TIMESTAMP"})
```

## Downloads

- Built **in memory** and **size-capped**: `MAX_DOWNLOAD_ROWS` (CSV, default 100000)
  and `MAX_XLSX_ROWS` (default 25000, openpyxl is heavier). Over the cap → HTTP 413
  with a "narrow your filters / use CSV" message (no streaming; no new deps).
- The client submits the download via `fetch` (app.js) so it can show a spinner **on
  the Download button** (the modal overlay hides the table spinner) and surface
  server errors in the modal.
- Audit-first: the audit INSERT must reach SUCCEEDED before the file returns, else
  HTTP 500 and no file. "All dates" (empty date) is stored as `NULL`
  (`CAST(NULLIF(:report_date,'') AS TIMESTAMP)`).

## Environment (all in `src/app/app.yaml`)

| Var | Default | Purpose |
|-----|---------|---------|
| `DATABRICKS_WAREHOUSE_ID` | (required) | Warehouse for statement execution |
| `APP_CATALOG` / `APP_SCHEMA` | `main` / `default` | UC location of the registry + audit tables |
| `APP_NAME` / `APP_ORG_NAME` / `APP_LOGO` | generic | Branding (white-label, `/static` logo) |
| `APP_VERSION` | `0.0.0` | Version string (audit + footer) |
| `DOWNLOADS_ENABLED` | `true` | Global kill switch |
| `DOWNLOAD_DISCLAIMER` | built-in | Fallback disclaimer when `app_config` has none |
| `ADMIN_GROUP` | `download_hub_admin_users` | Admin console group |
| `DOWNLOAD_GROUP_SUFFIX` | `_dl` | Suffix to derive a report's download group from its `view_key` |
| `MAX_DOWNLOAD_ROWS` | `100000` | CSV export row cap |
| `MAX_XLSX_ROWS` | `25000` | Excel export row cap |

Customer-specific values (host, warehouse, catalog/schema) are **genericized in git**
and supplied at deploy time — don't commit real workspace identifiers.

## Adding / editing a report

Preferred: the **admin console** (`/admin` → Reports → paste a query → "Run query &
pick columns" → choose display columns + filters → Save). It validates + writes via
bound params.

By SQL (idempotent MERGE on `report_id`):
```sql
MERGE INTO main.default.report_config t
USING (SELECT 'my_report' AS report_id) s ON t.report_id = s.report_id
WHEN NOT MATCHED THEN INSERT (report_id, title, source_query, date_field, columns_json,
  filters_json, order_by, display_order, enabled, download_group, view_key, updated_at, updated_by)
VALUES ('my_report', 'My Report', 'SELECT * FROM main.default.my_table', 'report_date',
  '[{"name":"col1","label":"Column 1","format":"text"}]', '[{"field":"region","label":"Region"}]',
  'col1', 1, true, NULL, 'my_view_group', current_timestamp(), 'seed');
```
- `columns_json` empty `'[]'` → show every column the query returns.
- `date_field` NULL → no date selector (all rows).
- `download_group` NULL → derived from `view_key` + `_dl`.
- `view_key` must exist in `report_view` for a titled switcher entry (unlisted keys
  fall back to the key as the label).

`format`: `text` (raw), `int` (thousands-separated), `pct` (signed 1-decimal; NULL → `—`).

## Hard rules

1. **Air-gap — no external URLs** in `templates/`, `static/css/`, `static/js/`. Every
   link is a `/static/...` path. `tests/test_branding_guards.py` fails on any
   `https://`, `//`, `cdn.`, `unpkg.`. Non-negotiable.
2. **No secrets in code.** Creds come from env / headers only.
3. **Keep the wheelhouse offline-install intact.** Don't add wheels that need an
   index; refresh with `scripts/build_wheelhouse.sh`.
4. **I/O only in `main.py`.** New logic goes in a pure module + a test.
5. **Match style:** 2-space indent in `src/app/`, type hints, module + function
   docstrings (Args/Returns/Raises).

## Gotchas

1. `asyncio.to_thread` wraps every SDK call in async routes (SDK is synchronous).
2. `auth_type="pat"` on the OBO client is mandatory (see Auth).
3. Filter fields must be columns the query returns (snapshot projects display ∪ filter
   cols when `columns_json` is set, else `*`).
4. Identifier validation happens at query-build time → a bad `view_key`/`date_field`/
   column raises `ValueError` (→ HTTP 400 in admin), not a silent NULL.
5. Audit is a synchronous block — a failed audit insert blocks the download (500).
6. Registry is TTL-cached ~300s; admin writes invalidate it; otherwise restart to
   pick up manual table edits.
7. Result reads page **all** chunks — don't assume `resp.result.data_array` is the
   whole result (it's only the first chunk).
8. Static assets are cache-busted by a **content hash** (`?v=<hash>`); don't
   hand-manage a version query string.

## Test & deploy

```bash
cd download_hub
PYTHONPATH=src python -m pytest -q          # baseline: 197 passed, 1 skipped
```
All modules except `main.py` run offline (no SDK). See `docs/` for the rest:
`ARCHITECTURE.md`, `CONFIGURATION.md` (env + registry schemas), `REPORTS.md`,
`PERMISSIONS.md` (groups + grants), `DEPLOY.md`, `OFFLINE.md`, `MIGRATION.md`.

```bash
databricks bundle validate -t dev
databricks bundle deploy   --target dev
databricks bundle run      download_hub --target dev   # start/restart the app
```
