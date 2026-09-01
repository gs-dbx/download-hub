# Data Download Hub

A **configurable, server-rendered SQL report download app** with per-user filters, search, pagination, and audited downloads — all driven by a simple registry table in your data warehouse.

**Key point:** Add or update reports by editing a single registry row. No code change. No redeploy. The app is white-labeled by environment variables and runs fully offline (no CDN, no external dependencies).

## What it does

- **Config-driven tabs.** Each enabled row in `{APP_CATALOG}.{APP_SCHEMA}.report_config` becomes a report tab. The report's columns, filters, query/volume source, date field, and display order are all defined by config — no code change needed.
- **Two report kinds.** A `query` report renders a SQL `SELECT` as a filterable table; a `volume` report browses a pinned Unity Catalog **Volume** (folders + subfolders, breadcrumbs) with metadata + gated file download. Both read on-behalf-of-user.
- **Aggregations & formats.** Columns can apply `Sum/Min/Avg/Max/First/Last` (injected as `AGG(source)` with a join-safe `GROUP BY`), and render as `int`, `float`/`double`, `pct`, or `text`.
- **Server-side SQL paging.** Filters, search, click-to-sort, and pagination are all pushed into SQL and run on-behalf-of-user: each interaction issues a `COUNT(*)` (pager total) plus one `LIMIT`/`OFFSET` page, so even a multi-million-row report never materializes in the app. Every interaction fetches a fresh HTML fragment; filter/sort state is deep-linkable in the URL.
- **Gated audited downloads.** Members of a report's download group can export the current filtered view (CSV or XLSX) with a required data-handling acknowledgement and justification. Every download writes one audit row to `{APP_CATALOG}.{APP_SCHEMA}.download_audit` before the file is returned — if the audit fails, the download is blocked.
- **Admin console.** Members of `ADMIN_GROUP` manage reports/views at `/admin` (query preview, column builder with per-column aggregation, add/**delete** reports, disclaimer, audit log). Each report page can reveal the exact SQL it runs ("View SQL").
- **On-behalf-of-user (OBO) reads.** Each user's queries run AS THAT USER on the bound SQL warehouse; Unity Catalog enforces their own data access. No fallback, no mock data, no privilege escalation.
- **Kill switch.** Set `DOWNLOADS_ENABLED=false` in the app config to globally disable downloads (UI hidden, endpoint returns 403) — independent of group membership.
- **Fully offline.** All Python wheels (`src/app/wheelhouse/`), front-end assets (USWDS, CSS, JS, logo), and configuration are committed. Install with `pip install --no-index --find-links src/app/wheelhouse -r requirements.lock` — no internet, no package index.

## Stack

- **Backend:** FastAPI 0.115+, Jinja2 templates, pure Python modules (stdlib only, fully testable offline)
- **Frontend:** Vanilla JS, USWDS 3.x (vendored locally), no npm, no framework
- **Auth:** Databricks Apps OBO (user's OAuth token in `X-Forwarded-Access-Token` header)
- **Data:** Databricks SQL (serverless), Unity Catalog (per-user access control), Delta Lake
- **Deployment:** Databricks Asset Bundle (DAB), Python 3.11
- **Offline deps:** Committed wheels at `src/app/wheelhouse/`

## Repository map

```
src/app/                FastAPI app + dependencies
  main.py               Routes (/, /report/{id}, /report/{id}/table, /download, /health)
  config.py, auth.py, reports.py, exports.py, render.py, shaping.py, cache.py, audit.py
  templates/            HTML templates (base, report, error, fragments)
  static/               USWDS 3.x, CSS, JS, logo (all local, no CDN)
  app.yaml              App runtime config (env vars for white-labeling)
  requirements.txt      Python dependencies (with --no-index, --find-links directives)
  wheelhouse/           Committed wheels (linux/CPython-3.11)

src/sample_report/      Pure synthetic-data generator (no Spark, no network)
src/notebooks/          Databricks notebook: seed `daily_metrics` + `download_audit`

resources/              Databricks Asset Bundle resources
  app.yml               Apps resource definition (OBO scope, warehouse binding)
  seed_job.yml          Job to seed the daily_metrics + audit tables
  grants.sql            Unity Catalog grants

databricks.yml          Databricks Asset Bundle manifest (catalog, schema, warehouse, groups)
tests/                  pytest unit tests (pure functions, no SDK required, 323 passed)
docs/
  ADMIN_SETUP.md        Complete Databricks administrator setup checklist
  ARCHITECTURE.md       Request flow, module map, caching, auth model
  CONFIGURATION.md      Environment variables, report_config schema, worked example
  DEPLOY.md             Bundle validate/deploy/run, groups, grants, kill switch
  URLS.md               User-facing URLs, internal routes, parameters, responses
  PERMISSIONS.md        OBO model, download gating, audit, account-level federation
  OFFLINE.md            Air-gap packaging, wheelhouse, local assets
  MIGRATION.md          Deploying to another environment
.github/
  copilot-instructions.md  GitHub Copilot instructions (code boundaries, patterns, rules)
```

## Quick start

Databricks administrators should begin with
**[docs/ADMIN_SETUP.md](docs/ADMIN_SETUP.md)**. It covers every workspace,
identity, warehouse, Unity Catalog, group, service-principal, and volume setting
required before end users can use the app.

### 1. Run the tests

```bash
cd download_hub
PYTHONPATH=src python -m pytest -q
```

Expected: 323 passed, 1 skipped.

### 2. Deploy to Databricks Apps

Edit `databricks.yml` to set your workspace host and IDs:

```yaml
workspace:
  host: https://your-workspace.cloud.databricks.com

variables:
  warehouse_id:
    default: "your-warehouse-id"
```

Then deploy:

```bash
# Validate the bundle
databricks bundle validate -t dev

# Deploy app + resources
databricks bundle deploy --target dev

# Seed the sample data (daily_metrics + download_audit tables)
databricks bundle run metrics_seed --target dev

# Start the app
databricks bundle run download_hub --target dev

# Check app status
databricks apps get download-hub
```

### 3. Set up groups and grants

Create two Databricks groups (via the UI or SCIM) and add members:

- `download_hub_app_users` — basic app access
- `download_hub_download_users` — gated download entitlement

Apply Unity Catalog grants from `resources/grants.sql` to each group and the app service principal. See `docs/DEPLOY.md` for details.

### 4. Add your own report

Use the `/admin` console (recommended — query builder with OBO preview), or insert a row into `{APP_CATALOG}.{APP_SCHEMA}.report_config` directly. A report is a full `SELECT` (`source_query`); columns and filters are optional:

```sql
INSERT INTO main.default.report_config VALUES (
  'my_report', 'My Report',
  'SELECT report_date, col1, col2 FROM main.default.my_table',  -- source_query (full SELECT)
  NULL,                                                          -- legacy date_field (unused)
  '[{"name":"col2","label":"Count","format":"int"}]',            -- columns_json (optional; empty = all cols)
  '[{"field":"report_date","label":"Report date"},
    {"field":"col1","label":"Column 1"}]',                       -- filters_json (optional)
  NULL, 1, true,                                                 -- order_by, display_order, enabled
  NULL, 'my_view', 'query', '',                                  -- download_group, view_key, kind, volume_root
  current_timestamp(), 'admin@org'
)
```

The app picks up the row within ~5 minutes (registry TTL). See `docs/CONFIGURATION.md` and `docs/REPORTS.md` for the full 15-column schema.

## Configure & rebrand (5 minutes)

Edit `src/app/app.yaml`:

```yaml
env:
  - name: APP_NAME
    value: "My App Name"                 # Masthead title
  - name: APP_ORG_NAME
    value: "My Organization"             # Logo alt text
  - name: APP_LOGO
    value: "/static/img/logo.svg"        # Must be /static path (no CDN)
  - name: DATABRICKS_WAREHOUSE_ID
    value: "your-warehouse-id"
  - name: APP_CATALOG
    value: "main"                        # Catalog holding report_config
  - name: APP_SCHEMA
    value: "default"                     # Schema holding report_config
  - name: APP_VERSION
    value: "1.0.0"
  - name: DOWNLOADS_ENABLED
    value: "true"                        # false/0/no/off → downloads disabled
  - name: DOWNLOAD_DISCLAIMER
    value: |
      Your data-handling notice here.
      Multi-line OK.
```

Redeploy and restart:

```bash
databricks bundle deploy --target dev
databricks bundle run download_hub --target dev
```

## Architecture & design decisions

See `docs/ARCHITECTURE.md` for the full request flow, module map (pure vs. I/O boundary), caching model, and auth model.

## Adding reports (no code change)

See `docs/REPORTS.md` and `docs/CONFIGURATION.md` for the `report_config` schema and JSON format for columns and filters.

## Deployment & operations

- **`docs/DEPLOY.md`** — bundle validate/deploy/run, creating groups, applying grants, kill switch, staging promotion
- **`docs/PERMISSIONS.md`** — OBO reads, download gating, audit-first, group resolution
- **`docs/OFFLINE.md`** — air-gap packaging, vendored assets, wheelhouse refresh
- **`docs/MIGRATION.md`** — deploying to another environment
- **`docs/URLS.md`** — URL structure, route parameters, and endpoint behavior
- **`docs/ADMIN_SETUP.md`** — comprehensive Databricks administrator setup
- **`.github/copilot-instructions.md`** — GitHub Copilot guide (code boundaries, patterns, rules)

## Key decisions (locked)

1. **OBO reads as the user.** Every data read runs as the signed-in user; Unity Catalog enforces access. No privilege escalation.
2. **Audit-first.** Downloads require a successful audit write to the warehouse before the file is returned.
3. **Server-side SQL paging.** Filters, search, sort, and pagination are pushed into SQL (a `COUNT(*)` + one `LIMIT`/`OFFSET` page per request, run OBO) — the app never holds a whole result set for display. Every identifier is allowlist-validated and every value is a bound parameter.
4. **Config-driven reports.** All report metadata (columns, filters, source table, ordering) comes from the registry table — zero code change to add a report.
5. **Air-gap.** No CDN, no external URLs in authored templates/CSS/JS. All assets committed locally.

## Tests

All modules except `main.py` are pure and fully testable without the Databricks SDK:

```bash
cd download_hub
PYTHONPATH=src python -m pytest tests/ -q
```

Coverage includes config parsing, injection validation, filter/search/pagination, export builders, auth helpers, and guard rules (no external URLs).

## License

Apache 2.0 or per your organization's policy.
