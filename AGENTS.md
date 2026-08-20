# AI Agents & Cursor Guide

This app is designed for AI-assisted development (GitHub Copilot, Cursor, Claude Code, etc.).

## Key boundaries

**I/O boundary: `src/app/main.py` only.** All SDK calls, templates, and async/await live here. Everything else is pure Python — no side effects, no network, fully testable offline.

When you write new logic, keep it **pure**: put it in a module, write a unit test (no SDK required), then wire it into `main.py` at the I/O boundary.

Example: if you need a new cell formatter, add it to `shaping.py`, test it with `pytest`, then call it from `render.py` (also pure). Both are tested without the SDK.

## Hard constraints

1. **Air-gap: no external URLs in authored front-end.** Every link in `templates/`, `static/css/`, `static/js/` must be a `/static/...` path. The guard test `tests/test_branding_guards.py` fails if any external URL appears (https://, //, cdn., unpkg.). This is non-negotiable.

2. **Never commit secrets.** All credentials come from env (`DATABRICKS_HOST`, `DATABRICKS_CLIENT_ID`, `DATABRICKS_SECRET`) or headers (`X-Forwarded-Access-Token`).

3. **Keep the wheelhouse offline-install intact.** `src/app/wheelhouse/` is the source for offline installs. Don't add new wheels that break offline-install or require an index lookup. Refresh via `scripts/build_wheelhouse.sh` after bumping `requirements.txt`.

4. **Match existing code style:** 2-space indent in `src/app/`, type hints, module-level docstrings, test everything that's not `main.py`.

## Key features to know about

- **Query-based reports.** `report_config.source_query` is a full `SELECT` (wrapped as a subquery); displayed columns default to all query columns unless `columns_json` narrows them. `date_field`/`filters_json` are optional.
- **Views + switcher.** `report_view` (view_key/title/order) groups reports; a report's `view_key` is the Databricks group granting view access. Members of >1 view get a view switcher.
- **Admin console at `/admin`** (gated by `ADMIN_GROUP`; writes as the app service principal): tabs for **Report Views**, **Reports** (with a run-a-query column/filter builder), **System Config** (the download disclaimer, stored in the `app_config` table), and **Audit Log** (with CSV export).
- **Tables:** `report_config`, `report_view`, `app_config`, `download_audit` — all created + migrated idempotently by `src/notebooks/generate_daily_metrics.py`.

## How to run tests

```bash
cd download_hub
PYTHONPATH=src python -m pytest -q
```

Must pass: 197 passed, 1 skipped. All modules except `main.py` are testable without the SDK.

## How to add a report (no code change)

Prefer the **admin console** (`/admin` → Reports → run a query → pick columns/filters → save). Or insert a row in `{APP_CATALOG}.{APP_SCHEMA}.report_config`:

```sql
INSERT INTO main.default.report_config
  (report_id, title, source_query, date_field, columns_json, filters_json,
   order_by, display_order, enabled, download_group, view_key, updated_at, updated_by)
VALUES (
  'report_id', 'Title', 'SELECT * FROM main.default.table', 'date_col',
  '[{"name":"col","label":"Label","format":"text"}]',
  '[]', NULL, 1, true, NULL, 'view_group', current_timestamp(), 'you@example.com'
)
```

`source_query` is a full `SELECT` (wrapped as a subquery). `date_col` and the
`columns_json` array are optional — leave `date_field` NULL for no date scope,
and leave `columns_json` empty (`'[]'`) to show every column the query returns.
`view_key` is the Databricks group that grants view access (the download group is
derived as `<view_key>` + `DOWNLOAD_GROUP_SUFFIX` unless `download_group` is set).

The app picks it up within 5 minutes (TTL refresh). See `.github/copilot-instructions.md` and `docs/CONFIGURATION.md` for details.

## Full reference

- **`.github/copilot-instructions.md`** — Copilot-specific guide (architecture, patterns, rules, gotchas)
- **`docs/ARCHITECTURE.md`** — Request flow, module map, caching, auth
- **`docs/CONFIGURATION.md`** — Env vars, `report_config` schema, JSON formats
- **`docs/DEPLOY.md`** — Bundle validate/deploy/run, groups, grants
- **`README.md`** — What it is, quick start, stack

Start with `.github/copilot-instructions.md` when working on code. It covers the boundary rule, pure-vs-IO pattern, test command, report-add workflow, and hard constraints.
