---
phase: 2-app-skeleton-fastapi-uswds-obo-read
plan: 2
type: execute
status: planned
workspace_url: https://field-eng-aws-gov-demo-irs-demo.cloud.databricks.us
default_catalog: irs
skill_references:
  - ~/.ai-dev-kit/repo/.claude/skills/python-dev/SKILL.md
  - ~/.ai-dev-kit/repo/.claude/skills/databricks-python-sdk/SKILL.md
  - ~/.ai-dev-kit/repo/databricks-skills/databricks-app-python/SKILL.md
  - ~/.ai-dev-kit/repo/databricks-skills/asset-bundles/SKILL.md
wave_count: 7
---

# Phase 2: App skeleton — FastAPI + USWDS + OBO read

## Goal
Deploy a Databricks App (`download-hub`) — server-rendered FastAPI + Jinja2 + vendored
USWDS 3.13.0 — that reads `irs.efile.daily_efile_glance` **as the signed-in user (OBO)** and
renders the 17-metric "Daily E-File at a Glance" table for the latest `report_date` at
`drain='ALL'`. The report-date list and DRAIN options are rendered inert (Phase 3 wires the
JS). No download UI, no audit write, no group gating (all Phase 4).

## Workspace Context (live, 2026-08-12)
- **Catalog/Schema:** `irs` / `efile`
- **Gold table:** `irs.efile.daily_efile_glance` — 408 rows, drains E/M/N/ALL, `MAX(report_date)=2026-01-12 00:00:00`; `pct_change` NULL where `value_py=0`.
- **Warehouse:** `2f225c0740dcd22b` (Serverless Starter) — HEALTHY; bound to the app as a `sql_warehouse` resource with `CAN_USE`.
- **Apps:** enabled (GovCloud `*.aws-gov.databricksapps.us`); `download-hub` ABSENT → create fresh. `dev-migration-factory` is the live reference pattern (same warehouse, `user_api_scopes:["sql"]`).

## Prerequisites
- [ ] Phase 1 complete & verified — gold table readable (it is).
- [ ] `databricks.yml` has `include: [resources/*.yml]` (present since Phase 1).
- [ ] Databricks CLI OAuth profile DEFAULT authenticated (live scan succeeded 2026-08-12).
- [ ] Branch `dbx/download-hub-phase-1` checked out (continue stacking here unless told otherwise).
- [ ] Do NOT touch `dev-migration-factory` or any other existing app (reference only).

## Skills to Read Before Executing
- `~/.ai-dev-kit/repo/.claude/skills/python-dev/SKILL.md` — pure functions, type hints, Google docstrings, pytest in `./tests/`.
- `~/.ai-dev-kit/repo/.claude/skills/databricks-python-sdk/SKILL.md` — **§ Async Applications (FastAPI): wrap every SDK call in `asyncio.to_thread`**; Statement Execution API; `Config(host, token)` construction.
- `~/.ai-dev-kit/repo/databricks-skills/databricks-app-python/SKILL.md` — `app.yaml` command/env shape, DAB deploy flow, `databricks apps logs`. **IGNORE its Dash/Streamlit framework scaffold — this is plain FastAPI+Jinja2.**
- `~/.ai-dev-kit/repo/databricks-skills/asset-bundles/SKILL.md` — **§ Apps Resources**, the `../src/app` path rule, `bundle run <app_key>` to start the app.
- Reference (in-repo): `databricks-builder-app/server/services/user.py` — real `X-Forwarded-Access-Token` extraction; `src/efile_glance/generator.py` — `METRICS` ordering source of truth.

---

## LOCKED DECISIONS (executor MUST follow verbatim)

### L1 — Standard bundle engine (NOT direct)
Deploy the Apps resource with the **standard (terraform) engine**. Do NOT set
`DATABRICKS_BUNDLE_ENGINE=direct` for Phase 2 (confirmed via live `bundle schema`;
`dev-migration-factory` proves apps deploy on the standard engine). Always run
`databricks bundle validate -t dev -p DEFAULT` before deploy.

### L2 — `resources/app.yml` (mirror the live dev-migration-factory pattern)
```yaml
resources:
  apps:
    download_hub:
      name: download-hub
      description: "Daily E-File at a Glance (IRS OCFO) — server-rendered FastAPI + USWDS"
      source_code_path: ../src/app          # relative to resources/
      user_api_scopes:
        - sql                               # OBO scope: query gold table AS THE USER
      resources:
        - name: efile_warehouse
          description: "Serverless SQL warehouse for OBO gold-table reads"
          sql_warehouse:
            id: ${var.warehouse_id}         # 2f225c0740dcd22b
            permission: CAN_USE
```
App `name` must match `^[a-z0-9-]+$`. If dev-mode name-prefixing yields an invalid/duplicate
name at `bundle validate`, pin `name: download-hub-${bundle.target}` OR add `presets:
name_prefix: ""` on the dev target — decide at validate time, document what was done.

### L3 — OBO: read AS THE USER, no fallback
- Extract the user token from the **`X-Forwarded-Access-Token`** header (case-insensitive).
  If ABSENT → return a clear error (HTTP 401/403 + friendly page). NO CLI-profile fallback,
  NO mock data.
- Build a fresh client per request: `WorkspaceClient(config=Config(host=os.environ["DATABRICKS_HOST"], token=user_token))`. Never cache a client across requests/users (token is short-lived).
- Every SDK/Statement-Execution call inside an `async def` route MUST be wrapped in
  `await asyncio.to_thread(...)` (SDK is fully synchronous). Consistency required.
- Data is always read as the user → a user without UC SELECT gets an error/empty result
  (satisfies FR-6 with no app-side gate).

### L4 — Query + rendering rules
- Main query (verified live to return 17 rows):
  ```sql
  SELECT metric_name, metric_group, sort_order, value_cy, value_py, pct_change
  FROM irs.efile.daily_efile_glance
  WHERE report_date = (SELECT MAX(report_date) FROM irs.efile.daily_efile_glance)
    AND drain = 'ALL'
  ORDER BY sort_order;
  ```
  Catalog/schema come from env (`EFILE_CATALOG`/`EFILE_SCHEMA`), not hardcoded. `drain` is a
  fixed enum (E/M/N/ALL) validated by the caller — never free user text (Phase 3 will validate too).
- Selector data (rendered INERT this phase): `SELECT DISTINCT report_date ... ORDER BY report_date DESC`; DRAIN options are the fixed enum {ALL, E, M, N} (no query needed). `report_date` displays as `%Y-%m-%d %H:%M:%S` (e.g. `2026-01-12 00:00:00`) per FR-4.
- `pct_change` NULL (where `value_py=0`) renders as an em dash `—` — never `None`/crash.
  Non-null renders with sign + one decimal (e.g. `+20.0%`, `-5.8%`). `value_cy`/`value_py`
  render with thousands separators.
- UI row order MUST equal the canonical `METRICS` order from `src/efile_glance/generator.py`.

### L5 — USWDS 3.13.0 vendored, local-only
Vendor the compiled USWDS 3.13.0 dist subset into `src/app/static/uswds/{css,js,fonts,img}/`,
preserving that sibling layout (the CSS references `../fonts/*` and `../img/*`). Reference ONLY
local static paths — NO CDN/unpkg links anywhere in HTML.

### L6 — Runtime deps + METRIC_ORDER self-containment
- `src/app/requirements.txt` = exactly `fastapi`, `uvicorn` (plain, not `[standard]`), `jinja2`,
  `databricks-sdk`. NO `openpyxl` (Phase 4). pytest stays in `requirements-dev.txt` only.
- The app deploy root (`src/app/`) must be self-contained. Mirror `generator.METRICS` order into a
  `METRIC_ORDER` constant in `src/app/shaping.py`, and add a guard unit test asserting it equals
  `efile_glance.generator.METRICS` order (import generator only in the test, not in app runtime).

---

## Wave 1: Foundation — app directory + runtime requirements

<task type="auto">
  <name>Scaffold src/app package and runtime requirements.txt</name>
  <wave>1</wave>
  <files>src/app/__init__.py, src/app/requirements.txt, src/app/static/.gitkeep, src/app/templates/.gitkeep</files>
  <read_first>
    - ~/.ai-dev-kit/repo/databricks-skills/databricks-app-python/SKILL.md (app source dir + requirements.txt install-at-deploy)
    - .planning/phases/2-RESEARCH.md (directory structure section)
  </read_first>
  <action>
    Create the app source skeleton (paths relative to bundle root /Users/greg.skinner/Documents/IRS/download_hub):
    - src/app/__init__.py : one-line docstring package marker.
    - src/app/requirements.txt : EXACTLY these four lines and nothing else (LOCKED L6):
      fastapi
      uvicorn
      jinja2
      databricks-sdk
      (uvicorn PLAIN — not uvicorn[standard]; NO openpyxl.)
    - src/app/static/.gitkeep and src/app/templates/.gitkeep : placeholders so dirs exist before later waves.
    Do NOT create app.yaml, main.py, or templates yet (later waves). Do NOT modify databricks.yml or requirements-dev.txt (requirements-dev.txt already has pytest from Phase 1).
  </action>
  <verify>ls src/app/__init__.py src/app/requirements.txt src/app/static/.gitkeep src/app/templates/.gitkeep && cat src/app/requirements.txt</verify>
  <acceptance_criteria>
    - All four files exist.
    - requirements.txt has exactly fastapi / uvicorn / jinja2 / databricks-sdk (no openpyxl, no uvicorn[standard], no pytest).
    - No CDN URLs, no secrets, no hardcoded workspace host in any created file.
  </acceptance_criteria>
</task>

---

## Wave 2: Vendor USWDS 3.13.0 assets

<task type="auto">
  <name>Fetch and commit vendored USWDS 3.13.0 dist subset</name>
  <wave>2</wave>
  <files>src/app/static/uswds/css/**, src/app/static/uswds/js/**, src/app/static/uswds/fonts/**, src/app/static/uswds/img/**</files>
  <read_first>
    - .planning/phases/2-RESEARCH.md (USWDS vendoring — pinned specifics + fetch commands)
  </read_first>
  <action>
    Download USWDS 3.13.0 compiled dist ONCE (dev machine has internet) and commit the files
    (LOCKED L5). Preserve the css/js/fonts/img sibling layout. Run from src/app/static:
      curl -L -o /tmp/uswds.tgz https://github.com/uswds/uswds/releases/download/v3.13.0/uswds-uswds-3.13.0.tgz
      tar -xzf /tmp/uswds.tgz -C /tmp
      mkdir -p uswds
      cp -r /tmp/package/dist/css   uswds/css
      cp -r /tmp/package/dist/js    uswds/js
      cp -r /tmp/package/dist/fonts uswds/fonts
      cp -r /tmp/package/dist/img   uswds/img
      rm -rf /tmp/package /tmp/uswds.tgz
    Verify uswds/css/uswds.min.css, uswds/js/uswds-init.min.js, uswds/js/uswds.min.js exist and
    fonts/ + img/ are non-empty. If the GitHub release asset URL 404s, fall back to the npm tarball
    (https://registry.npmjs.org/@uswds/uswds/-/uswds-3.13.0.tgz) — same package/dist/ layout — and
    note the source used. `git add src/app/static/uswds` (these are committed vendored assets).
  </action>
  <verify>ls -1 src/app/static/uswds/css/uswds.min.css src/app/static/uswds/js/uswds-init.min.js src/app/static/uswds/js/uswds.min.js && ls src/app/static/uswds/fonts | head -1 && ls src/app/static/uswds/img | head -1</verify>
  <acceptance_criteria>
    - uswds/css/uswds.min.css, uswds/js/uswds-init.min.js, uswds/js/uswds.min.js all present.
    - uswds/fonts/ and uswds/img/ are non-empty (fonts + sprite/usa-icons).
    - Version is 3.13.0 (note the download source in the commit message).
    - No CDN references introduced anywhere; assets are local files under src/app/static/uswds/.
  </acceptance_criteria>
</task>

---

## Wave 3: Core pure logic (Spark-free, unit-testable)

<task type="auto">
  <name>Implement pure queries.py, shaping.py, auth.py</name>
  <wave>3</wave>
  <files>src/app/queries.py, src/app/shaping.py, src/app/auth.py</files>
  <read_first>
    - ~/.ai-dev-kit/repo/.claude/skills/python-dev/SKILL.md (pure functions, type hints, Google docstrings, specific exceptions)
    - src/efile_glance/generator.py (METRICS = canonical ordering to mirror)
    - .planning/phases/2-PLAN.md (LOCKED DECISIONS L3/L4/L6)
    - .planning/phases/2-RESEARCH.md (code sketches for auth/queries/shaping)
  </read_first>
  <action>
    Write three PURE modules — NO fastapi, NO databricks.sdk, NO network at import/call time
    (so pytest runs offline). Stdlib + typing only.

    queries.py:
    - `build_glance_query(catalog: str, schema: str, drain: str = "ALL") -> str` — returns the
      LOCKED L4 SQL: SELECT metric_name, metric_group, sort_order, value_cy, value_py, pct_change
      FROM {catalog}.{schema}.daily_efile_glance WHERE report_date = (SELECT MAX(report_date) FROM
      same-fqn) AND drain = '{drain}' ORDER BY sort_order. Validate drain in {E,M,N,ALL} and raise
      ValueError otherwise (drain is a fixed enum, never free text).
    - `build_report_dates_query(catalog: str, schema: str) -> str` — SELECT DISTINCT report_date
      ... ORDER BY report_date DESC (for the inert selector).

    shaping.py:
    - `METRIC_ORDER: tuple[str, ...]` — the 17 metric_names in canonical order, mirroring
      generator.METRICS (LOCKED L6). Guard-tested in Wave 5.
    - `format_pct(pct: float | None) -> str` — None → "—"; else sign + one decimal + "%"
      (e.g. 20.0 → "+20.0%", -5.8 → "-5.8%", 0.0 → "0.0%").
    - `format_count(n: int | None) -> str` — thousands separators (e.g. 9827762 → "9,827,762");
      None → "—".
    - `format_report_date(ts) -> str` — "%Y-%m-%d %H:%M:%S"; accept datetime OR the string the
      Statement Execution API returns and normalize to that format.
    - `rows_to_context(columns: list[str], data_array: list[list]) -> list[dict]` — map each
      SQL row (columns from resp.manifest) into a dict {metric_name, metric_group, sort_order,
      value_cy, value_py, value_cy_fmt, value_py_fmt, pct_change, pct_fmt}; return rows sorted by
      sort_order. Handle NULL pct_change → "—" via format_pct. Do NOT assume column order — index
      by the columns list.

    auth.py:
    - `USER_TOKEN_HEADER = "x-forwarded-access-token"`.
    - `extract_user_token(headers) -> str` — accept a mapping/Headers-like object with a
      case-insensitive `.get`; return the token; raise PermissionError with a clear message if
      absent/empty (LOCKED L3). No SDK import.
    All functions typed with Google-style docstrings; small, single-responsibility; no side effects.
  </action>
  <verify>cd /Users/greg.skinner/Documents/IRS/download_hub && .venv/bin/python -m py_compile src/app/queries.py src/app/shaping.py src/app/auth.py && PYTHONPATH=src .venv/bin/python -c "from app.queries import build_glance_query; from app.shaping import format_pct, METRIC_ORDER; from app.auth import extract_user_token; print(len(METRIC_ORDER), format_pct(None), format_pct(20.0), build_glance_query('irs','efile','ALL')[:40])"</verify>
  <acceptance_criteria>
    - py_compile passes; the import line prints `17 — +20.0% SELECT metric_name, metric_group, sort_ord` (or equivalent).
    - No `import fastapi`, `import databricks`, or network call anywhere in queries.py/shaping.py/auth.py.
    - build_glance_query produces a 3-level FQN, the MAX(report_date) subquery, drain filter, ORDER BY sort_order; invalid drain raises ValueError.
    - format_pct(None) == "—"; format_pct(20.0) == "+20.0%"; format_pct(-5.8) == "-5.8%".
    - rows_to_context indexes by the columns list (not positional assumption) and returns rows in sort_order.
    - extract_user_token returns the token when present and raises PermissionError when absent/empty.
    - METRIC_ORDER has 17 entries.
  </acceptance_criteria>
</task>

---

## Wave 4: FastAPI app + Jinja2 templates + app.yaml

<task type="auto">
  <name>Write main.py, base.html, glance.html, and app.yaml</name>
  <wave>4</wave>
  <files>src/app/main.py, src/app/templates/base.html, src/app/templates/glance.html, src/app/app.yaml</files>
  <read_first>
    - ~/.ai-dev-kit/repo/.claude/skills/databricks-python-sdk/SKILL.md (§ Async FastAPI: asyncio.to_thread; Statement Execution; Config(host, token))
    - ~/.ai-dev-kit/repo/databricks-skills/databricks-app-python/SKILL.md (app.yaml command/env shape)
    - databricks-builder-app/server/services/user.py (real X-Forwarded-Access-Token extraction)
    - src/app/queries.py, src/app/shaping.py, src/app/auth.py (the pure functions to call)
    - .planning/phases/2-PLAN.md (LOCKED L2/L3/L4/L5)
    - .planning/phases/2-RESEARCH.md (main.py OBO sketch, static mount, template wiring)
  </read_first>
  <action>
    main.py (the ONLY I/O boundary):
    - Create the FastAPI app; mount static: `app.mount("/static", StaticFiles(directory="static"), name="static")`; `Jinja2Templates(directory="templates")`. Paths are relative to the app root (the Apps runtime runs from src/app/).
    - Read config from env: `EFILE_CATALOG`, `EFILE_SCHEMA`, `DATABRICKS_WAREHOUSE_ID`, `APP_VERSION`; `DATABRICKS_HOST` is auto-injected by the Apps runtime.
    - GET `/`: extract the user token via auth.extract_user_token(request.headers). On PermissionError → render a friendly error page (or HTTPException 401) explaining user-authorization is required. Build a fresh `WorkspaceClient(config=Config(host=os.environ["DATABRICKS_HOST"], token=token))` per request. Run build_glance_query(...) AND build_report_dates_query(...) via statement_execution.execute_statement (warehouse_id from env, wait_timeout="30s") — EACH wrapped in `await asyncio.to_thread(...)`. Check resp.status.state == SUCCEEDED. Pass through shaping.rows_to_context and shaping.format_report_date. Render glance.html with: rows, report_dates (list, inert), drain_options=["ALL","E","M","N"], selected_report_date, selected_drain="ALL", app_version.
    - GET `/health`: return {"status":"ok"} (no auth) for a lightweight liveness check.
    - Do NOT cache the client. Do NOT add a CLI/mock fallback. No hardcoded host/token/warehouse (env only).

    base.html (USWDS scaffold, local assets only — LOCKED L5):
    - `<link rel="stylesheet" href="/static/uswds/css/uswds.min.css">` and
      `<script src="/static/uswds/js/uswds-init.min.js"></script>` in <head>;
      `<script src="/static/uswds/js/uswds.min.js" defer></script>` before </body>.
    - A USWDS gov banner (usa-banner) header and a simple footer. `{% block content %}{% endblock %}`.
    - NO CDN/unpkg/external URLs.

    glance.html (extends base.html):
    - Page title "Daily E-File at a Glance".
    - A search input, a report-date <select> (options from report_dates, formatted %Y-%m-%d %H:%M:%S,
      selected = latest), and a DRAIN <select> (ALL/E/M/N, selected=ALL) — all rendered but INERT
      (no JS behavior this phase; Phase 3 wires them). Give them stable ids/data-attributes so Phase 3 can hook on.
    - A USWDS `usa-table` with columns: Metric | 2026 | 2025 | % Change. One row per context row in
      sort_order; render value_cy_fmt, value_py_fmt, pct_fmt (— for NULL). Optionally group headers by metric_group.

    app.yaml (at src/app/app.yaml — LOCKED L2 sibling of source):
    ```
    command:
      - "uvicorn"
      - "main:app"
      - "--host"
      - "0.0.0.0"
      - "--port"
      - "8000"
    env:
      - name: DATABRICKS_WAREHOUSE_ID
        value: "2f225c0740dcd22b"
      - name: EFILE_CATALOG
        value: "irs"
      - name: EFILE_SCHEMA
        value: "efile"
      - name: APP_VERSION
        value: "0.2.0"
    ```
    Do NOT put DATABRICKS_HOST or SP OAuth vars in app.yaml (auto-injected).
  </action>
  <verify>cd /Users/greg.skinner/Documents/IRS/download_hub && .venv/bin/python -m py_compile src/app/main.py && grep -c "asyncio.to_thread" src/app/main.py && ! grep -RniE "unpkg|cdn|https?://[^\"' ]*uswds" src/app/templates src/app/static/uswds/*.css 2>/dev/null && echo "no-cdn-ok"</verify>
  <acceptance_criteria>
    - main.py compiles; imports the pure modules; extracts the OBO token from X-Forwarded-Access-Token; builds WorkspaceClient(Config(host, token)) per request; every execute_statement call is wrapped in asyncio.to_thread (grep count >= 1, and covers both queries).
    - Missing header path returns a clear error (no fallback, no mock).
    - No hardcoded workspace host/token/warehouse in main.py (env only); warehouse in app.yaml env is acceptable config.
    - base.html references ONLY /static/uswds/... local assets; NO CDN/external URLs in templates.
    - glance.html renders 17 rows in sort_order with — for NULL pct_change, plus inert search/report-date/DRAIN controls.
    - app.yaml has the uvicorn command and the four env vars; no DATABRICKS_HOST/secret in it.
  </acceptance_criteria>
</task>

---

## Wave 5: Unit tests for the pure logic

<task type="auto">
  <name>Write pytest tests for queries, shaping, auth</name>
  <wave>5</wave>
  <files>tests/test_queries.py, tests/test_shaping.py, tests/test_auth.py</files>
  <read_first>
    - ~/.ai-dev-kit/repo/.claude/skills/python-dev/SKILL.md (pytest-only, tests/ layout)
    - src/app/queries.py, src/app/shaping.py, src/app/auth.py (modules under test)
    - src/efile_glance/generator.py (METRICS — for the guard test)
    - .planning/phases/2-PLAN.md (LOCKED L4/L6 invariants)
  </read_first>
  <action>
    Write pytest tests (no fastapi, no databricks.sdk, no network). Add `src` to sys.path (or rely on
    PYTHONPATH=src). Cover:
    test_queries.py:
    - build_glance_query returns 3-level FQN irs.efile.daily_efile_glance, contains the MAX(report_date)
      subquery, `drain = 'ALL'`, and `ORDER BY sort_order`.
    - invalid drain (e.g. "X") raises ValueError; each of E/M/N/ALL is accepted.
    - build_report_dates_query selects DISTINCT report_date ordered DESC.
    test_shaping.py:
    - format_pct: None → "—"; 20.0 → "+20.0%"; -5.8 → "-5.8%"; 0.0 → "0.0%".
    - format_count: 9827762 → "9,827,762"; None → "—".
    - format_report_date: datetime(2026,1,12) → "2026-01-12 00:00:00"; and a string input normalizes.
    - rows_to_context: given columns + a small data_array (unordered sort_order, one NULL pct_change),
      returns rows sorted by sort_order with pct_fmt "—" for the NULL and correct value_*_fmt; indexes
      by columns (shuffle the column order in one case to prove it's not positional).
    - GUARD: METRIC_ORDER == tuple(m.metric_name for m in generator.METRICS in order) — i.e. app UI order
      equals data order (LOCKED L6). Import efile_glance.generator ONLY here.
    test_auth.py:
    - extract_user_token: header present (any case) → token; absent/empty → raises PermissionError.
    No pyspark, no fastapi import.
  </action>
  <verify>cd /Users/greg.skinner/Documents/IRS/download_hub && PYTHONPATH=src .venv/bin/python -m pytest tests/ -v</verify>
  <acceptance_criteria>
    - All tests pass (Phase 1's 9 generator tests still pass too).
    - The METRIC_ORDER guard test passes (UI order == generator.METRICS order).
    - A shaping test proves NULL pct_change → "—" and column-order independence.
    - An auth test covers present and absent header.
    - No pyspark/fastapi/databricks.sdk import and no network in tests.
  </acceptance_criteria>
</task>

---

## Wave 6: DAB app resource

<task type="auto">
  <name>Add resources/app.yml (download-hub app + warehouse binding)</name>
  <wave>6</wave>
  <files>resources/app.yml</files>
  <read_first>
    - ~/.ai-dev-kit/repo/databricks-skills/asset-bundles/SKILL.md (§ Apps Resources; ../src/app path; bundle run to start)
    - .planning/phases/2-RESEARCH.md (dev-migration-factory live pattern + dev-mode naming caveat)
    - .planning/WORKSPACE.md (warehouse_id var, app naming)
    - /Users/greg.skinner/Documents/IRS/download_hub/databricks.yml (existing; include: resources/*.yml already present)
  </read_first>
  <action>
    Create resources/app.yml EXACTLY per LOCKED L2: resources.apps.download_hub with name `download-hub`,
    description, source_code_path `../src/app`, user_api_scopes [sql], and a sql_warehouse resource
    (name efile_warehouse) bound to ${var.warehouse_id} with permission CAN_USE. Do NOT modify
    databricks.yml (include already covers resources/*.yml), and do NOT add DATABRICKS_BUNDLE_ENGINE=direct.
    Run `databricks bundle validate -t dev -p DEFAULT`. If dev-mode name-prefixing makes the app name
    invalid/duplicate, apply the LOCKED L2 remedy (name: download-hub-${bundle.target} OR presets:
    name_prefix: "" on dev) and document which was used in the commit message.
  </action>
  <verify>cd /Users/greg.skinner/Documents/IRS/download_hub && databricks bundle validate -t dev -p DEFAULT</verify>
  <acceptance_criteria>
    - `databricks bundle validate -t dev` passes (Validation OK).
    - resources/app.yml defines resources.apps.download_hub with source_code_path ../src/app, user_api_scopes [sql], and a sql_warehouse resource on ${var.warehouse_id} with CAN_USE.
    - databricks.yml unchanged (bundle.name download_hub, targets intact); no direct-engine env var added.
    - Phase 1 job efile_seed still present and valid in the bundle.
  </acceptance_criteria>
</task>

---

## Checkpoint: deploy app to dev and verify render

<task type="checkpoint:human">
  <name>Deploy download-hub to dev, start it, verify the table renders via OBO</name>
  <wave>7</wave>
  <action>
    From /Users/greg.skinner/Documents/IRS/download_hub (branch dbx/download-hub-phase-1):
    1. PYTHONPATH=src .venv/bin/python -m pytest tests/ -v      # all app + generator tests pass
    2. databricks bundle validate -t dev -p DEFAULT
    3. databricks bundle deploy --target dev -p DEFAULT          # standard engine (LOCKED L1)
    4. databricks bundle run download_hub -t dev -p DEFAULT      # start the app (or `databricks apps deploy`/`run`)
       — if `bundle run` on an app key isn't supported by the CLI version, use
         `databricks apps deploy download-hub --source-code-path <workspace path>` per the app-python skill.
    5. databricks apps get download-hub -p DEFAULT               # confirm ComputeStatus ACTIVE / DeploymentStatus SUCCEEDED, capture the app URL
    6. Open the app URL in a browser (signed in as greg.skinner) and confirm:
       - The USWDS-styled "Daily E-File at a Glance" page loads (banner + styled usa-table).
       - The table shows 17 rows for report_date 2026-01-12 00:00:00 at drain=ALL, columns 2026 / 2025 / % Change.
       - NULL pct_change renders as — (if any on the latest date; otherwise verified by unit test).
       - The search box + report-date select + DRAIN select are visible but inert (Phase 3 wires them).
    7. If the page errors on the OBO header, check `databricks apps logs download-hub` and confirm
       user_api_scopes [sql] took effect (effective scopes include sql).
    Note: applying the efile_glance_app_users SELECT grant is Phase 4; in dev the signed-in developer
    (Greg, table owner) already has SELECT, so the OBO read succeeds without the grant.
  </action>
  <acceptance_criteria>
    - pytest: all tests pass.
    - bundle validate + deploy --target dev complete without errors (standard engine).
    - `databricks apps get download-hub` shows the app deployed and ACTIVE with a reachable URL.
    - The app page renders the 17-row table for the latest report_date at drain=ALL in USWDS styling, read via the signed-in user's OBO token.
    - Inert search/report-date/DRAIN controls are present (no working filtering yet — that's Phase 3).
  </acceptance_criteria>
</task>

---

## Must-Haves

```yaml
truths:
  - App deploys on the STANDARD bundle engine (no DATABRICKS_BUNDLE_ENGINE=direct).
  - Data is read AS THE USER via X-Forwarded-Access-Token → WorkspaceClient(Config(host, token)); no CLI/mock fallback (FR-6).
  - Every SDK/Statement-Execution call in an async route is wrapped in asyncio.to_thread; a fresh client per request.
  - Query = latest report_date + drain='ALL', ORDER BY sort_order (3-level irs.efile.daily_efile_glance); catalog/schema from env.
  - pct_change NULL → em dash "—"; non-null → sign + 1 decimal; counts thousands-separated.
  - USWDS 3.13.0 vendored locally under src/app/static/uswds/{css,js,fonts,img}; NO CDN links anywhere.
  - Runtime deps = fastapi, uvicorn (plain), jinja2, databricks-sdk (NO openpyxl this phase).
  - UI row order == generator.METRICS order (METRIC_ORDER guard test).
  - Report-date + DRAIN selectors rendered inert this phase (Phase 3 wires JS).
  - No download UI / audit write / group gating (Phase 4).

artifacts:
  - src/app/__init__.py
  - src/app/requirements.txt        (fastapi, uvicorn, jinja2, databricks-sdk)
  - src/app/static/uswds/{css,js,fonts,img}/   (USWDS 3.13.0 vendored)
  - src/app/queries.py              (pure)
  - src/app/shaping.py              (pure; METRIC_ORDER)
  - src/app/auth.py                 (pure; X-Forwarded-Access-Token)
  - src/app/main.py                 (FastAPI; OBO I/O boundary)
  - src/app/templates/base.html     (USWDS scaffold, local assets)
  - src/app/templates/glance.html   (17-row usa-table + inert selectors)
  - src/app/app.yaml                (uvicorn command + env)
  - tests/test_queries.py, tests/test_shaping.py, tests/test_auth.py
  - resources/app.yml               (resources.apps.download_hub + sql_warehouse CAN_USE)

uc_targets:
  - irs.efile.daily_efile_glance    (READ ONLY, via OBO user token)
```
