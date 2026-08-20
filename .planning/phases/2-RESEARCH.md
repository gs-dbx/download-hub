# Phase 2 Research

**Date:** 2026-08-12
**Phase:** App skeleton — server-rendered FastAPI + Jinja2 + vendored USWDS + OBO read of `irs.efile.daily_efile_glance`
**Domain:** Databricks Apps (Python/FastAPI) + on-behalf-of-user (OBO) auth + UC read via Statement Execution API + Asset Bundles
**MCP Available:** databricks-v2 present but **NOT usable here** (requires browser OAuth) — used **CLI (profile DEFAULT)** instead
**CLI Available:** yes — **live scan SUCCEEDED** (CLI v0.299.2, `databricks ... -p DEFAULT` returned live data)

---

## Workspace State (live findings)

All values below are observed live on 2026-08-12, not assumed.

**Workspace URL:** https://field-eng-aws-gov-demo-irs-demo.cloud.databricks.us (AWS GovCloud; Apps domain `*.aws-gov.databricksapps.us`)
**Default Catalog / Schema:** `irs` / `efile`
**SQL Warehouse:** `2f225c0740dcd22b` "Serverless Starter Warehouse" — HEALTHY, serverless.

### Gold table `irs.efile.daily_efile_glance` — CONFIRMED READY (live `DESCRIBE` + `GROUP BY`)
Columns (exact live types):

| Column | Type |
|---|---|
| report_date | timestamp |
| drain | string |
| metric_name | string |
| metric_group | string |
| sort_order | int |
| value_cy | bigint |
| value_py | bigint |
| pct_change | double |

Live row distribution: **408 rows = 4 drains × 102**, where `102 = 17 metrics × 6 report_dates`.
- drains present: `E`, `M`, `N`, **`ALL`** (ALL is materialized — the value the app renders this phase).
- `MAX(report_date)` = **`2026-01-12 00:00:00`** (six snapshots: 2026-01-05,06,07,08,09,12).
- `pct_change` is `NULL` where `value_py = 0` (Phase 1 LOCKED DECISION L3) — **the template must render NULL gracefully** (e.g. show `—`).

### Existing Apps in workspace (9; `download-hub` ABSENT → create fresh)
`agent-testai-gen`, `dev-migration-factory`, `executor-log-viewer`, `irs-cio-status-entry`, `lakemeter`, `search-demo`, `sow-clin-assistant`, `sow-clin-orchestrator`, `sow-clin-ui`.

### GOLD REFERENCE APP — `dev-migration-factory` (matches our exact OBO+warehouse pattern)
Live `databricks apps get dev-migration-factory` shows precisely the shape Phase 2 needs:
- `user_api_scopes: ["sql"]`  → this is how the app requests OBO scope `sql`.
- `effective_user_api_scopes: ["iam.access-control:read", "iam.current-user:read", "sql"]` (the two `iam.*` scopes are auto-added by the platform).
- A `resources` entry: `{"name": "reconciliation_warehouse", "sql_warehouse": {"id": "2f225c0740dcd22b", "permission": "CAN_USE"}}` — **same warehouse we bind**.
- It was deployed via DAB (its `source_code_path` is under `.../.bundle/fe-migration-factory/dev/files/...`) → **live proof that apps deploy through the standard bundle workflow**.

### DEFERRED ITEM #1 — RESOLVED: DAB Apps resource does NOT require `DATABRICKS_BUNDLE_ENGINE=direct`
Confirmed against the live `databricks bundle schema` (CLI v0.299.2):
- `resources.apps.<key>` is a **first-class, standard-engine resource** (`resources.App` in the schema, ref'd from `config.Resources`). It supports `name`, `description`, `source_code_path`, `user_api_scopes` (`slice/string`), `resources` (`slice apps.AppResource`), and an inline `config` block (`command` + `env`).
- `apps.AppResource.sql_warehouse` = `{ id, permission }` where permission enum = `CAN_MANAGE | CAN_USE | IS_OWNER`.
- Apps are backed by the Terraform provider (`databricks_app`), so the **default (terraform) engine deploys them** — unlike `genie_spaces` (which is why the repo's `databricks.yml` header mentions direct engine). **Recommendation: deploy with the standard engine.** Keep `DATABRICKS_BUNDLE_ENGINE=direct` only as a fallback if a future genie/other-resource is added or if `bundle validate` errors on the installed provider version.

### DEFERRED ITEM #2 — RESOLVED: concrete real-world app.yaml/bundle pattern
`dev-migration-factory` (live) is the concrete example (see above). The app resource carries `user_api_scopes:["sql"]` + a `sql_warehouse` resource on `2f225c0740dcd22b` with `CAN_USE` — copy this verbatim.

---

## Relevant Skill Patterns

### Neither app skill is a perfect fit — this is a hybrid (documented deviation)
- `databricks-app-python/SKILL.md` covers **Dash / Streamlit only** (no FastAPI/Jinja2). It contributes the **app.yaml `command:`/`env:` shape, DAB-vs-CLI deploy flow, `databricks apps logs` troubleshooting, and `Config()`-based SDK auth**.
- `databricks-app-apx/SKILL.md` uses **FastAPI but bolted to a React/bun/shadcn/orval toolchain** — explicitly rejected by PROJECT.md (server-rendered, no npm). Do NOT run `apx init`, shadcn, or the OpenAPI client generator. Borrow only its **FastAPI router/type-hint discipline** and `HTTPException` error style.
- **Conclusion for the planner:** treat this as a **plain FastAPI + Jinja2** app. Use the two app skills for `app.yaml`/deploy mechanics only; ignore their framework scaffolds.

### From `asset-bundles/SKILL.md` (+ live schema)
- Apps in DABs are minimal: `resources.apps.<key>` with `name`, `description`, `source_code_path` — env/command live in `app.yaml` in the source dir.
- **Path resolution:** `source_code_path` in `resources/*.yml` is relative to `resources/` → use `../src/app`.
- **Apps must be started after deploy:** `databricks bundle run <app_key> -t dev`.
- **Dev-mode naming caveat:** `mode: development` prepends a prefix to resource names; app `name` must match `^[a-z0-9-]+$`. Set the app name explicitly and `bundle validate` before deploy (see Risks).

### From `databricks-python-sdk/SKILL.md` — CRITICAL for FastAPI
- **The SDK is fully synchronous.** Every `WorkspaceClient` / `statement_execution` call inside an `async def` route MUST be wrapped in `await asyncio.to_thread(...)` or it blocks the event loop.
- `Config(host=..., token=...)` builds a client from an explicit token (this is the OBO construction).
- Statement Execution: `w.statement_execution.execute_statement(warehouse_id=..., statement=..., wait_timeout="30s")`; results at `resp.result.data_array` (list of row-lists), columns at `resp.manifest.schema.columns`; check `resp.status.state == StatementState.SUCCEEDED`.

### From `python-dev/SKILL.md`
- Pure, type-hinted, Google-docstring functions; pytest in `./tests/` with `__init__.py`; the query builder, row-shaping, and token-extraction functions must be pure/importable with **no network or SDK import at call time** so unit tests run offline.

### OBO mechanism — CONFIRMED from `databricks-builder-app/server/services/user.py`
This in-repo FastAPI app documents the exact Databricks Apps header contract (file docstring, line 5): **"Access token is available in the `X-Forwarded-Access-Token` header."** It also uses `X-Forwarded-User` for the signed-in email. Header lookups are case-insensitive via Starlette's `request.headers.get(...)`.

---

## Recommended Approach

### Overall
Add a single DAB **`apps.download_hub`** resource (source dir `src/app/`) to the existing bundle. The FastAPI app has one GET route (`/`) that: (1) extracts the user's OBO token from `X-Forwarded-Access-Token`, (2) builds a `WorkspaceClient` from that token, (3) runs the "latest report_date + drain=ALL" SQL on warehouse `2f225c0740dcd22b` via Statement Execution (wrapped in `asyncio.to_thread`), (4) shapes rows into ordered template context mirroring Phase 1's `METRICS`, (5) renders a USWDS-styled Jinja2 table. Data is **always** read as the user — no SP fallback for reads (FR-6). If the header is absent → return a clear error (no CLI/mock fallback), per 2-CONTEXT.

### Directory structure to create
```
download_hub/
├── databricks.yml                      # EXISTS — add nothing structural; `include: [resources/*.yml]` already present
├── resources/
│   ├── seed_job.yml                    # EXISTS (Phase 1)
│   └── app.yml                         # NEW — resources.apps.download_hub
├── src/
│   ├── efile_glance/generator.py       # EXISTS — METRICS is the ordering source of truth (import/mirror; do NOT duplicate)
│   └── app/                            # NEW — the Databricks App source root
│       ├── app.yaml                    # run command + env
│       ├── requirements.txt            # fastapi, uvicorn, jinja2, databricks-sdk
│       ├── main.py                     # FastAPI app + StaticFiles mount + route
│       ├── queries.py                  # PURE: build_glance_query(report_date|None, drain) -> str
│       ├── shaping.py                  # PURE: rows_to_context(data_array, columns) -> ordered list[dict] + pct fmt
│       ├── auth.py                     # PURE-ish: extract_user_token(headers) -> str (raises if absent)
│       ├── templates/
│       │   ├── base.html               # USWDS <head> + banner + footer scaffold
│       │   └── glance.html             # the 17-row table + inert report/DRAIN selectors
│       └── static/uswds/{css,js,fonts,img}/   # vendored USWDS 3.13.0 dist subset (committed)
└── tests/
    ├── __init__.py                     # EXISTS
    ├── test_generator.py               # EXISTS
    ├── test_queries.py                 # NEW
    ├── test_shaping.py                 # NEW
    └── test_auth.py                    # NEW
```
Note: `src/app/` must be self-contained at deploy time (the Apps runtime installs `requirements.txt` and runs from this dir). To reuse `METRICS` from `src/efile_glance/generator.py`, either (a) copy the canonical `METRICS` ordering into `shaping.py` as a module constant (simplest for the app's isolated deploy root), or (b) confirm `src/efile_glance` is synced into the bundle and importable. **Recommend (a): a small `METRIC_ORDER` constant in `shaping.py` that mirrors generator.py, unit-tested to equal generator's `METRICS` order** — keeps the app deploy root self-contained while guaranteeing UI order == data order.

### `resources/app.yml` (copy the live dev-migration-factory pattern)
```yaml
resources:
  apps:
    download_hub:
      name: download-hub                       # DNS-safe; absent in workspace (confirmed). See dev-mode caveat.
      description: "Daily E-File at a Glance (IRS OCFO) — server-rendered FastAPI + USWDS"
      source_code_path: ../src/app             # relative to resources/
      user_api_scopes:
        - sql                                  # OBO scope: query the gold table AS THE USER
      resources:
        - name: efile_warehouse
          description: "Serverless SQL warehouse for OBO gold-table reads"
          sql_warehouse:
            id: ${var.warehouse_id}            # 2f225c0740dcd22b
            permission: CAN_USE
```

### `src/app/app.yaml`
```yaml
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
Notes: The Apps runtime listens on the port the platform assigns; USWDS best practice is to read `os.environ.get("DATABRICKS_APP_PORT", "8000")` — but the app.yaml `command` port must match. Simplest: hardcode `8000` in both, OR omit `--port` and let uvicorn default to 8000. `DATABRICKS_HOST` and the SP OAuth env vars are auto-injected by the Apps runtime; **do not** put them in app.yaml.

### OBO code sketch (the only side-effecting boundary)
```python
# auth.py  (PURE — unit-testable, no SDK/network)
from starlette.datastructures import Headers

USER_TOKEN_HEADER = "x-forwarded-access-token"

def extract_user_token(headers: Headers) -> str:
    """Return the signed-in user's OBO access token, or raise if absent."""
    token = headers.get(USER_TOKEN_HEADER)
    if not token:
        raise PermissionError(
            "Missing X-Forwarded-Access-Token; app must run with user "
            "authorization (scope 'sql') enabled. No fallback."
        )
    return token

# queries.py  (PURE)
def build_glance_query(catalog: str, schema: str, drain: str = "ALL") -> str:
    """SQL for the latest report_date at the given drain, ordered by sort_order."""
    fqn = f"{catalog}.{schema}.daily_efile_glance"
    return (
        "SELECT metric_name, metric_group, sort_order, value_cy, value_py, pct_change "
        f"FROM {fqn} "
        f"WHERE report_date = (SELECT MAX(report_date) FROM {fqn}) "
        f"AND drain = '{drain}' "
        "ORDER BY sort_order"
    )
    # drain is a fixed enum (E/M/N/ALL) validated by the caller — not free user text.

# main.py  (I/O boundary — wrap SDK in asyncio.to_thread)
import asyncio, os
from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config

def _user_client(token: str) -> WorkspaceClient:
    return WorkspaceClient(config=Config(host=os.environ["DATABRICKS_HOST"], token=token))

async def _run_sql(token: str, sql: str):
    w = _user_client(token)
    resp = await asyncio.to_thread(
        w.statement_execution.execute_statement,
        warehouse_id=os.environ["DATABRICKS_WAREHOUSE_ID"],
        statement=sql,
        wait_timeout="30s",
    )
    cols = [c.name for c in resp.manifest.schema.columns]
    data = resp.result.data_array or []
    return cols, data
```
Why `Config(host, token)`: the forwarded value is a valid OAuth access token; the SDK sends it as `Authorization: Bearer <token>`, so UC evaluates the query with the **user's** grants. A user lacking `SELECT` gets a permission error / empty result → satisfies FR-6 with no app-side gate.

### Exact query for "latest report_date + drain=ALL" (verified live to return 17 rows)
```sql
SELECT metric_name, metric_group, sort_order, value_cy, value_py, pct_change
FROM irs.efile.daily_efile_glance
WHERE report_date = (SELECT MAX(report_date) FROM irs.efile.daily_efile_glance)
  AND drain = 'ALL'
ORDER BY sort_order;
```
Also fetch the selector lists (rendered but inert this phase):
```sql
SELECT DISTINCT report_date FROM irs.efile.daily_efile_glance ORDER BY report_date DESC;
-- DRAIN options are the fixed enum E, M, N, ALL (no query needed).
```
`report_date` display format required by FR-4 is `2026-01-08 00:00:00` (i.e. `%Y-%m-%d %H:%M:%S`).

### USWDS vendoring — pinned specifics
- **Version: USWDS 3.13.0** (latest stable 3.x, released 2025-05-23; confirmed via GitHub releases API). Release asset = `uswds-uswds-3.13.0.tgz` (the npm package tarball; its `package/dist/` is the compiled distribution — no Sass build needed).
- **Minimal file set to vendor** (preserve the `css/js/fonts/img` sibling layout — `uswds.min.css` references `../fonts/*` and `../img/*` by relative path, so the directory structure MUST be kept):
  - `dist/css/uswds.min.css`
  - `dist/js/uswds-init.min.js`  (load in `<head>`)
  - `dist/js/uswds.min.js`       (load before `</body>`, `defer`)
  - `dist/fonts/`                (whole dir — `uswds.min.css` `@font-face` points here; Public Sans is primary)
  - `dist/img/`                  (whole dir — includes `sprite.svg` and `usa-icons/`; CSS background images live here)
- **Target repo location:** `src/app/static/uswds/{css,js,fonts,img}/` — reference ONLY as local static paths (air-gap rule: NO CDN, no unpkg links in HTML).
- **Fetch commands (run once now on the internet-connected dev machine; then commit the files):**
  ```bash
  cd src/app/static
  curl -L -o /tmp/uswds.tgz https://github.com/uswds/uswds/releases/download/v3.13.0/uswds-uswds-3.13.0.tgz
  tar -xzf /tmp/uswds.tgz -C /tmp
  mkdir -p uswds
  cp -r /tmp/package/dist/css   uswds/css
  cp -r /tmp/package/dist/js    uswds/js
  cp -r /tmp/package/dist/fonts uswds/fonts
  cp -r /tmp/package/dist/img   uswds/img
  rm -rf /tmp/package /tmp/uswds.tgz
  ```
- **Template head/foot wiring:**
  ```html
  <link rel="stylesheet" href="/static/uswds/css/uswds.min.css">
  <script src="/static/uswds/js/uswds-init.min.js"></script>   <!-- in <head> -->
  ...
  <script src="/static/uswds/js/uswds.min.js" defer></script>  <!-- before </body> -->
  ```
- **FastAPI static mount:** `app.mount("/static", StaticFiles(directory="static"), name="static")` and `Jinja2Templates(directory="templates")`.

### Functions that must be pure & unit-testable (per 2-CONTEXT)
| Function | Purity | Tests |
|---|---|---|
| `build_glance_query(catalog, schema, drain)` | pure str builder | 3-level FQN, subquery for MAX(report_date), `drain='ALL'`, `ORDER BY sort_order` |
| `rows_to_context(cols, data_array)` → ordered list[dict] | pure | 17 rows in sort_order 1..17; pct formatting (+/- sign, 1 decimal); **NULL pct_change → `—`**; value thousands-formatting |
| `extract_user_token(headers)` | pure | present → token; absent → raises PermissionError |
| `METRIC_ORDER` constant in shaping.py | data | equals `efile_glance.generator.METRICS` order (guard test) |
No live SQL / SDK / network in unit tests.

---

## Runtime Dependencies (`src/app/requirements.txt`)
Pin loosely; all must be pre-stageable for the air-gapped target (document for the ops mirror):
```
fastapi
uvicorn        # plain, NOT uvicorn[standard] — avoids uvloop/httptools/watchfiles transitive weight
jinja2
databricks-sdk
```
**Transitive air-gap notes:**
- `fastapi` → `starlette`, `pydantic`, `pydantic-core` (Rust wheel — must have the correct platform wheel pre-staged), `typing-extensions`, `annotated-types`.
- `jinja2` → `MarkupSafe` (C-ext wheel).
- `uvicorn` (plain) → `click`, `h11` only.
- `databricks-sdk` → `requests`, `google-auth`, etc. (usually already present in the Apps base image).
- `openpyxl` is **Phase 4**, not this phase.
The Apps runtime installs `requirements.txt` at deploy from the workspace's package source; the strict air-gap constraint governs the eventual target environment — record the full transitive closure in the vendoring note (FR-12 / NFR-1).

---

## Existing Resources (Reuse vs Create)
| Resource | Status | Action |
|---|---|---|
| `download_hub` bundle (`databricks.yml`) | EXISTS w/ `include: resources/*.yml` | **Reuse** — add `resources/app.yml` |
| `resources/seed_job.yml` (Phase 1 job) | EXISTS | **Leave as-is** |
| `irs.efile.daily_efile_glance` | EXISTS, 408 rows, drain ALL present | **Read only** (OBO) |
| `download-hub` app | ABSENT (confirmed live) | **Create** via `resources/app.yml` |
| Warehouse `2f225c0740dcd22b` | HEALTHY | **Bind** as `sql_warehouse` app resource (CAN_USE) |
| `dev-migration-factory` app | EXISTS (other project) | **Reference pattern only — do NOT touch** |
| `download_audit` write, group gating, grants | — | **DEFERRED to Phase 4** (do not add this phase) |

---

## Recommended References (for the executor to read before coding)
- `~/.ai-dev-kit/repo/.claude/skills/python-dev/SKILL.md` — pure functions, type hints, pytest in `./tests/`.
- `~/.ai-dev-kit/repo/.claude/skills/databricks-python-sdk/SKILL.md` — **§ CRITICAL: Async Applications (FastAPI)** (`asyncio.to_thread`), Statement Execution, `Config(host, token)`.
- `~/.ai-dev-kit/repo/databricks-skills/databricks-app-python/SKILL.md` — `app.yaml` command/env shape, DAB-vs-CLI deploy, `databricks apps logs` troubleshooting. (Ignore Dash/Streamlit framework specifics.)
- `~/.ai-dev-kit/repo/databricks-skills/asset-bundles/SKILL.md` — **§ Apps Resources**, `../src/app` path rule, `bundle run` to start the app.
- `~/.ai-dev-kit/repo/databricks-builder-app/server/services/user.py` — real in-repo FastAPI example of `X-Forwarded-Access-Token` / `X-Forwarded-User` extraction.
- In-repo: `src/efile_glance/generator.py` (`METRICS` = ordering source of truth), `databricks.yml`, `resources/seed_job.yml` (naming/style to match).

---

## Risks / Notes
- **Dev-mode app naming (validate before deploy):** `mode: development` may prefix resource names; app `name` must match `^[a-z0-9-]+$` and be reasonably short/unique. Set `name: download-hub` explicitly and run `databricks bundle validate -t dev` first. If the prefix produces an invalid/duplicate name, pin `name: download-hub-${bundle.target}` or add a `presets: name_prefix: ""` override on the dev target.
- **Bundle engine:** standard (terraform) engine deploys apps — `DATABRICKS_BUNDLE_ENGINE=direct` is NOT required (confirmed via live schema; dev-migration-factory proves it). The `databricks.yml` header comment about direct engine is legacy/aspirational; do not adopt it for this phase.
- **`asyncio.to_thread` is mandatory** around every SDK call in async routes — the SDK is fully synchronous and will otherwise block the event loop (fails NFR-3 ~1s render). If preferred, define the route as `def` (sync) and let FastAPI run it in the threadpool — but be consistent.
- **OBO token is short-lived** — always construct a fresh `WorkspaceClient` per request from the current header token; never cache the client across requests/users.
- **NULL `pct_change`** (Phase 1 L3, `value_py=0`, live-present on the first report date for "Online Accepted (amended)") must render as `—`, not `None`/crash — covered by a shaping unit test.
- **METRICS duplication risk:** the app deploy root (`src/app/`) should be self-contained, so mirror `generator.METRICS` order into `shaping.py` and add a guard test asserting equality — otherwise UI order could silently drift from data order.
- **`DATABRICKS_HOST` at runtime:** injected by the Apps platform; `os.environ["DATABRICKS_HOST"]` is safe inside the app but absent in local pytest — keep the client construction out of pure-function tests.
- **MCP unusable here:** databricks-v2 MCP needs interactive browser OAuth; all live checks used the CLI + Statement Execution API (`databricks api post /api/2.0/sql/statements -p DEFAULT`). Executor should do the same for any live verification.
- **No download UI, no audit write, no group gating this phase** — all Phase 4. Rendering the report/DRAIN selectors as inert markup is in-scope so Phase 3 only wires client-side JS.
