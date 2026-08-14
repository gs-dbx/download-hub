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

## How to run tests

```bash
cd download_hub
PYTHONPATH=src python -m pytest -q
```

Must pass: 146 passed, 1 skipped. All modules except `main.py` are testable without the SDK.

## How to add a report (no code change)

Insert a row in `{APP_CATALOG}.{APP_SCHEMA}.report_config`:

```sql
INSERT INTO main.default.report_config VALUES (
  'report_id', 'Title', 'main.default.table', 'date_col',
  '[{"name":"col","label":"Label","format":"text"}]',
  '[]', NULL, 1, true, NULL, current_timestamp()
)
```

The app picks it up within 5 minutes (TTL refresh). See `.github/copilot-instructions.md` and `docs/CONFIGURATION.md` for details.

## Full reference

- **`.github/copilot-instructions.md`** — Copilot-specific guide (architecture, patterns, rules, gotchas)
- **`docs/ARCHITECTURE.md`** — Request flow, module map, caching, auth
- **`docs/CONFIGURATION.md`** — Env vars, `report_config` schema, JSON formats
- **`docs/DEPLOY.md`** — Bundle validate/deploy/run, groups, grants
- **`README.md`** — What it is, quick start, stack

Start with `.github/copilot-instructions.md` when working on code. It covers the boundary rule, pure-vs-IO pattern, test command, report-add workflow, and hard constraints.
