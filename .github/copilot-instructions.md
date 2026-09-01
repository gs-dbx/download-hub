# GitHub Copilot Instructions for Data Download Hub

## What is this?

The **Data Download Hub** is a configurable, server-rendered FastAPI application that serves reports from any SQL table via a clean, search-friendly web UI with per-user downloads gated by Databricks group membership.

- **100% config-driven.** New reports are added by inserting rows into the `report_config` registry table — zero code change or redeploy required.
- **Server-side SQL paging.** Filters, search, sort, and pagination are pushed into SQL and run OBO (a `COUNT(*)` + one `LIMIT`/`OFFSET` page per request) — no whole result set is ever held in the app.
- **Audit-first downloads.** Every download writes exactly one audit row *before* the file is returned; if the audit fails, the download is blocked.
- **Fully air-gapped.** All dependencies and assets ship committed in `src/app/wheelhouse/` and `static/`; no CDN, no external URLs.

## Architecture

### Request flow

1. Browser → FastAPI route
2. Extract OBO token from `X-Forwarded-Access-Token` header
3. For data reads: build a per-request `WorkspaceClient` with `auth_type="pat"` and read AS THE USER via Statement Execution API
4. For registry reads: use a cached (TTL ~300s) service-principal client
5. Server-side SQL paging: build a `COUNT(*)` + one `LIMIT`/`OFFSET` page with configured filters/search/sort pushed into SQL, run AS THE USER (`_query_report_page`); filter dropdown options come from a distinct-values query (`_report_filter_options`)
6. Render `report.html` full page or `_rows.html` fragment (JS-driven table updates)
7. Download: re-check group membership (defense in depth), validate form, run the same filtered/searched query bounded by the spill cap, write audit row (as SP), return file

### Module map — pure vs. I/O boundary

**`src/app/main.py`** — the ONLY I/O boundary (AsyncIO, SDK calls, Jinja templates, app.mount, routes)
- `index()` → redirect to first report
- `report_page()` → full page render (OBO token extract, COUNT + first page via SQL, filter options)
- `report_table()` → fragment render (COUNT + one LIMIT/OFFSET page via SQL for the active filters/search/sort)
- `download()` → export (group re-check, form validate, file build, audit write, response)
- `_run_sql()` / `_run_sql_sp_query()` / `_run_sql_sp()` → wrapped SDK calls with `asyncio.to_thread`

**Pure modules** (no SDK, no async, fully unit-testable in pytest alone):
- `config.py` — app branding helpers (`app_name`, `app_logo`, `app_org_name`, `resolve_disclaimer`); kill-switch parser (`downloads_enabled`)
- `auth.py` — token/email extraction, group membership check, effective-group resolution
- `cache.py` — pure helpers + a small `BoundedTTLCache` (used for identity/display lookups). NOTE: the display data path no longer uses an in-memory snapshot — `apply_filters`/`apply_search`/`paginate`/`SnapshotCache` remain here as tested utilities but are not the runtime read path
- `reports.py` — `ReportConfig` dataclass, parse/build generic query builders, injection validation (identifier allowlist regex)
- `exports.py` — CSV/XLSX builders, disclaimer embedding, filename generation
- `render.py` — display-column cell formatters, header builder, haystack concatenation for search
- `shaping.py` — cell formatters (int, pct, date)
- `audit.py` — audit-row builder, parameterized INSERT builder

### Caching model

- **Display reads:** NOT cached — the interactive path issues a `COUNT(*)` + one `LIMIT`/`OFFSET` page per interaction (server-side SQL paging), so nothing large is materialized. `refresh=1` simply re-runs the query.
- **Report registry:** in-process TTL tuple. Refreshed at most every 300 seconds. Admin can force immediate refresh by restarting the app.
- **Identity display:** `BoundedTTLCache` maps SCIM ids → email for the audit/change-log tabs (bounded, TTL-expiring).

### Auth model

- **OBO reads:** header `X-Forwarded-Access-Token` → `WorkspaceClient(auth_type="pat")` → runs AS THE USER
- **SP writes + registry reads:** injected `DATABRICKS_CLIENT_ID` / `SECRET` → `WorkspaceClient()` (no explicit auth) → runs AS THE APP
- **Download group check:** `current_user.me()` OBO call → check group display names against effective download group
- No fallback: missing OBO token → 401 (no CLI profile, no mock data)

### Configuration

All config comes from environment variables (app.yaml):
- `DATABRICKS_WAREHOUSE_ID` — warehouse to execute statements on
- `APP_CATALOG`, `APP_SCHEMA` — Unity Catalog location of `report_config`, `download_audit`
- `APP_NAME`, `APP_ORG_NAME`, `APP_LOGO` — branding (white-label by config)
- `DOWNLOADS_ENABLED` — global kill switch (false/0/no/off → disabled; default true)
- `DOWNLOAD_DISCLAIMER` — custom data-handling notice (optional; falls back to exports.DEFAULT_DISCLAIMER)
- `APP_VERSION` — semantic version (for audit logging)

### Offline / air-gap

- **All wheels committed** in `src/app/wheelhouse/` (linux/CPython-3.11)
- **All front-end assets local** — USWDS, color overlay, logo, app.js
- **No CDN ever.** Every link in `templates/` and `static/` is a `/static/...` path
- **Guard test** (`tests/test_branding_guards.py`) fails if any external URL appears in authored templates/CSS/JS
- **Install:** `pip install --no-index --find-links src/app/wheelhouse -r requirements.lock`

## How to write good code

### Keep logic pure and I/O at the boundary

**Good:** A pure function in `cache.py`:
```python
def apply_filters(rows: list[dict], selected_filters: dict[str, str]) -> list[dict]:
    """Return rows matching all selected filters."""
    for field, value in selected_filters.items():
        if value:
            rows = [r for r in rows if r.get(field) == value]
    return rows
```

**Bad:** Calling the SDK inside the function — put that in `main.py` only.

### Validate identifiers strictly; bind values always

**Good** (from `reports.py`):
```python
# Configured filter identifiers use the allowlist; selected values are bound.
field = validate_identifier(filter_field)
params.append({"name": "flt_region", "value": selected_value, "type": "STRING"})
sql += f" WHERE {field} = :flt_region"
```

### Test without the SDK

Every module except `main.py` is importable in a pytest-only `.venv` (no SDK, no Databricks):
```bash
cd download_hub
python -m venv .venv-test
. .venv-test/bin/activate
pip install pytest
PYTHONPATH=src python -m pytest tests/ -q
```

The test suite is fast and runs offline.

## How to add a new report

**No code change. Add one row to `report_config` — that's it.**

1. Insert/MERGE a row into `{APP_CATALOG}.{APP_SCHEMA}.report_config`:

```sql
INSERT INTO main.default.report_config VALUES (
  'new_report',                          -- report_id (stable key, bare identifier)
  'New Report Title',                    -- title (user-facing)
  'main.default.my_source_table',        -- source_fqn (1–3 dotted parts, each bare identifier)
  NULL,                                  -- legacy date_field (unused)
  '[{"name":"col1","label":"Column 1","format":"text"},{"name":"col2","label":"Count","format":"int"}]',  -- columns_json
  '[{"field":"report_date","label":"Report date"},{"field":"region","label":"Region"}]',
  'col1',                                -- order_by (optional; NULL for no sort)
  2,                                     -- display_order
  true,                                  -- enabled
  NULL,                                  -- download_group (NULL → code default; set to a Databricks group name to gate to that group)
  current_timestamp()                    -- updated_at
)
```

The app picks it up within ~5 minutes (TTL refresh). Restart to pick it up immediately.

### Column JSON format

```json
[
  {"name": "column_name", "label": "Display Name", "format": "text"},
  {"name": "count_col", "label": "Count", "format": "int"},
  {"name": "pct_col", "label": "% Change", "format": "pct"}
]
```

- `name` — source column (bare SQL identifier)
- `label` — header + export header
- `format` — `text` (raw), `int` (thousands-separated), or `pct` (signed 1-decimal %; NULL → `—`)

### Filter JSON format

```json
[
  {"field": "region", "label": "Region"},
  {"field": "quarter", "label": "Quarter"}
]
```

- `field` — source column to filter on (must exist in the report's query output; it's bound into the SQL `WHERE`, and its distinct values populate the dropdown)
- `label` — dropdown label

**Important:** Every filter field MUST exist on `source_fqn` or the OBO read will fail.

## How to rebrand (5 minutes)

Edit `src/app/app.yaml` — no code change:

```yaml
env:
  - name: APP_NAME
    value: "Your App Name"
  - name: APP_ORG_NAME
    value: "Your Organization"
  - name: APP_LOGO
    value: "/static/img/your-logo.svg"   # must be a /static path (no CDN)
  - name: DOWNLOADS_ENABLED
    value: "true"
  - name: DOWNLOAD_DISCLAIMER
    value: |
      Your custom data-handling notice.
      Multi-line OK.
```

Then redeploy:
```bash
databricks bundle deploy -t dev
databricks bundle run download_hub -t dev
```

## Hard rules — don't break these

### Air-gap: no external URLs in authored front-end

Every link in `templates/`, `static/css/`, and `static/js/` must be a local `/static/...` path. The guard test (`tests/test_branding_guards.py`) fails if any external URL appears (e.g., `https://`, `//`, `cdn.`, `unpkg.`). This is non-negotiable for air-gap compliance.

**Bad:**
```html
<link rel="stylesheet" href="https://unpkg.com/uswds@3.0.0/dist/css/uswds.min.css">
```

**Good:**
```html
<link rel="stylesheet" href="/static/uswds/css/uswds.min.css">
```

### No secrets in code

Credentials come from env (`DATABRICKS_HOST`, `DATABRICKS_CLIENT_ID`, `DATABRICKS_SECRET`) or headers (`X-Forwarded-Access-Token`). Never commit tokens, passwords, or API keys.

### Keep wheelhouse offline-install intact

The committed `src/app/wheelhouse/` directory is the source for offline installs. Do NOT add wheels that require internet access or external index lookups. Run `scripts/build_wheelhouse.sh` to refresh after updating `requirements.txt`.

### Match existing code style

- **Indentation:** 2 spaces in `src/app/` (FastAPI modules)
- **Line length:** ~100 characters (visible in multi-line calls)
- **Imports:** group by stdlib, third-party, local; alphabetize within groups
- **Type hints:** use them (functions should have return type hints)
- **Docstrings:** module-level (describe purpose), function-level (Args, Returns, Raises)
- **Tests:** pure functions only; no SDK, no network; use pytest fixtures

### Keep tests green

```bash
cd download_hub
PYTHONPATH=src .venv/bin/python -m pytest -q
```

Current baseline: 323 passed, 1 skipped. Every code change must maintain or improve this. The branding guard test ensures no external URLs leak into committed templates/CSS/JS.

## Gotchas

1. **`asyncio.to_thread` required for SDK calls in async routes.** The Databricks SDK is fully synchronous; inside an `async def` route, wrap every SDK call with `await asyncio.to_thread(...)` so it doesn't block the event loop.

2. **OBO token pinning: `auth_type="pat"` is mandatory.** The Apps runtime injects both the user's OAuth token (in the header) AND the app SP's credentials (in the environment). Without `auth_type="pat"`, the SDK sees both and refuses to initialize. Pinning `pat` forces the user's token to win, giving you OBO.

3. **Server-side SQL paging — no display cache.** Each interaction runs a `COUNT(*)` + one `LIMIT`/`OFFSET` page in SQL as the user, so a huge report never materializes. Deep pages pay `OFFSET` rescans (keyset pagination is a future optimization) and `ORDER BY` needs a unique tiebreaker for perfectly stable page boundaries.

4. **Filter/search/sort must reference real output columns.** Filters bind into the SQL `WHERE`, search spans the displayed text columns, and sort orders by the clicked column — all identifiers are allowlist-validated. If a filter field isn't in the report's query output, the OBO read fails with "column not found".

5. **Every identifier is validated; bad config raises ValueError at query-build time, not runtime.** A typo in column names or filter fields is caught early by the regex validator, which is good — no silent NULL results.

6. **The identifier allowlist is strict: `^[A-Za-z_][A-Za-z0-9_]*$`.** No hyphens or dots. Use this for column names, filter fields, and `order_by`.

7. **Audit writes are synchronous blocks.** If an audit insert fails for ANY reason, the download is blocked (HTTP 500, no file). This is audit-first and non-negotiable.

8. **The report registry is TTL-cached for ~300s.** After editing `report_config`, the app picks up changes within 5 minutes. To pick up changes immediately, restart the app.

## Testing

```bash
cd download_hub

# Create a minimal test venv (pytest + typing-extensions only; no SDK needed)
python3.11 -m venv .venv-test
source .venv-test/bin/activate
pip install pytest typing-extensions

# Run tests (pure modules only)
PYTHONPATH=src python -m pytest tests/ -q
```

Tests cover config parsing, injection validation, filter/search/pagination, export builders, auth helpers, and the guard rules (no external URLs).

## Deploying

```bash
cd /path/to/download_hub   # the bundle root (the directory containing databricks.yml)

# Validate
databricks bundle validate -t dev

# Deploy app + resources
databricks bundle deploy --target dev

# Seed the daily_metrics table + download_audit (run the seed job)
databricks bundle run metrics_seed --target dev

# Start the app
databricks bundle run download_hub --target dev
```

See `docs/DEPLOY.md` for full details (groups, grants, kill switch, staging promotion).
