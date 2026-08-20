# Phase 3 Research — Interactivity (search, report-date, DRAIN)

**Date:** 2026-08-12
**Phase:** 3 — wire the search / report-date / DRAIN controls
**Domain:** Databricks App (server-rendered FastAPI + Jinja2 + vanilla JS) — extends the ALREADY-DEPLOYED `download-hub` app
**MCP Available:** databricks-v2 present but NOT usable (browser OAuth) — used CLI (profile DEFAULT)
**CLI Available:** yes — live param-query check SUCCEEDED
**Scope note:** NO new workspace resources, NO new UC objects. Pure app-code extension. No live workspace discovery needed beyond the one param-shape sanity check below.

---

## Current App Code (what exists to extend)

All under `src/app/`. Tests under `tests/` import via the `app.*` package path (a conftest/path shim puts `src/` on `sys.path`), while the app itself runs from `src/app/` with FLAT imports (`from queries import ...`) — keep both conventions intact.

### `main.py` — the sole I/O boundary (reuse, add one route)
- `_user_client(token)` builds a per-request `WorkspaceClient(config=Config(host=..., token=token, auth_type="pat"))`. **The `auth_type="pat"` pin is the Phase-2 fix** that stops the SDK from tripping "more than one authorization method configured" when the Apps runtime injects the app SP's OAuth env vars. Reuse verbatim.
- `_run_sql(token, sql) -> (columns, data)` wraps `statement_execution.execute_statement(warehouse_id, statement, wait_timeout="30s")` in `asyncio.to_thread`, checks `StatementState.SUCCEEDED`, returns `resp.manifest.schema.columns` names + `resp.result.data_array`. **It does NOT currently forward `parameters=` — Phase 3 must add that arg** (see sketch).
- `/` route: extracts token, runs `build_glance_query(catalog, schema, "ALL")` + `build_report_dates_query(...)`, shapes via `rows_to_context`, renders `glance.html` with context keys: `rows`, `report_dates`, `drain_options`, `selected_report_date`, `selected_drain`, `app_version`.
- Module constant `DRAIN_OPTIONS = ["ALL", "E", "M", "N"]`. `_env()` helper reads env vars (`EFILE_CATALOG`, `EFILE_SCHEMA`, `DATABRICKS_WAREHOUSE_ID`, `APP_VERSION`).
- `/health` route exists. `error.html` is used for 401 (missing token) and 403 (UC read failure).

### `queries.py` — pure SQL builders (reuse + ADD)
- `VALID_DRAINS = ("E", "M", "N", "ALL")`, `_fqn(catalog, schema)` (rejects empty), `_TABLE = "daily_efile_glance"`.
- `build_glance_query(catalog, schema, drain="ALL")` — the initial-render "latest report_date via MAX subquery" builder. drain is enum-validated then string-interpolated (safe). **KEEP for the `/` route.**
- `build_report_dates_query(catalog, schema)` — `SELECT DISTINCT report_date ... ORDER BY report_date DESC`.
- **Add here:** a specific-report_date parameterized builder + validation helpers (below).

### `shaping.py` — pure formatting (reuse UNCHANGED)
- `rows_to_context(columns, data_array) -> list[dict]` indexes by column name (order-independent), returns dicts with `metric_name`, `metric_group`, `sort_order`, `value_cy`, `value_py`, `value_cy_fmt`, `value_py_fmt`, `pct_change`, `pct_fmt`, sorted by `sort_order`. NULL pct → em dash `—`.
- `format_report_date(ts)` → `"%Y-%m-%d %H:%M:%S"` (accepts datetime or str; strips trailing `Z`). This is the exact string format that feeds the `<select>` option values — and therefore the exact string that must round-trip back as the `report_date` query param.
- `format_pct`, `format_count`, `METRIC_ORDER`, `EM_DASH`. No changes needed.

### `auth.py` — `extract_user_token(headers) -> str` (reuse UNCHANGED)
- Reads `x-forwarded-access-token` case-insensitively; raises `PermissionError` if absent. Reuse for the new `/table` route.

### Templates
- `base.html` — USWDS `<head>` (`uswds-init.min.js`) + banner/header/footer + `uswds.min.js defer` before `</body>`. **No `app.js` is referenced yet — Phase 3 adds a `<script src="/static/js/app.js" defer>` tag here (or in glance.html).**
- `glance.html` — extends base; renders the search `<input id="glance-search" data-role="glance-search">`, `<select id="glance-report-date" data-role="glance-report-date">` (options = `report_dates`), `<select id="glance-drain" data-role="glance-drain">` (options = `drain_options`), and the `<table id="glance-table">` whose `<tbody>` currently loops `{% for row in rows %}` emitting `<tr data-metric-group=.. data-metric-name=..>`. **The `<tbody>` has NO id yet — add one (`id="glance-tbody"`) and extract the `<tr>` loop into `_rows.html`.** The controls already carry stable ids/data-roles the JS can hook — they were rendered "inert" in Phase 2 precisely so Phase 3 only adds behavior.

### Static / tests
- `static/uswds/{css,js,fonts,img}/` vendored. **No `static/js/` and no `app.js` exist yet.** No `_rows.html` exists yet.
- `tests/`: `test_queries.py`, `test_shaping.py`, `test_auth.py`, `test_generator.py` all present and passing-style. `test_queries.py` asserts on substrings of the SQL string — mirror that style for the new builder + validators.

---

## Relevant Skill Patterns

### 1. Parameterized Statement Execution (databricks-sdk) — EXACT shape

The SDK skill does not spell out parameter binding, but the shape is stable. Import the parameter item and pass a `parameters=[...]` list to `execute_statement`:

```python
from databricks.sdk.service.sql import StatementParameterListItem

resp = await asyncio.to_thread(
    client.statement_execution.execute_statement,
    warehouse_id=os.environ["DATABRICKS_WAREHOUSE_ID"],
    statement=(
        "SELECT metric_name, metric_group, sort_order, value_cy, value_py, pct_change "
        f"FROM {fqn} "
        "WHERE report_date = :report_date AND drain = :drain "
        "ORDER BY sort_order"
    ),
    parameters=[
        StatementParameterListItem(
            name="report_date", value="2026-01-08 00:00:00", type="TIMESTAMP"
        ),
        StatementParameterListItem(name="drain", value="E", type="STRING"),
    ],
    wait_timeout="30s",
)
```

Confirmed facts:
- Placeholders in SQL are **named**, `:report_date` / `:drain` (leading colon, no quotes around them in the SQL — the binder handles quoting/casting).
- `StatementParameterListItem` lives in `databricks.sdk.service.sql` (same module as `StatementState`, already imported by `main.py`). Fields: `name` (no colon), `value` (always a **string**), `type` (SQL type name as a string).
- `type="TIMESTAMP"` with a `value` string formatted `"%Y-%m-%d %H:%M:%S"` (e.g. `"2026-01-08 00:00:00"`) — **verified working live** (see next section). This is exactly the string `shaping.format_report_date` emits, so the `<select>` option value round-trips with no reformatting.
- `type="STRING"` for `drain` (values `E`/`M`/`N`/`ALL`; `ALL` is a materialized drain value in the table, so no special-casing — it binds like any other string).
- Could not `import databricks.sdk` in the local `.venv` (SDK is only present in the Apps runtime image, not this dev venv), so the class signature was not introspected here. The class name + module + field names are stable across recent SDK versions and the REST payload shape is confirmed live — this is the safe, canonical form. If a future SDK rename ever bites, the equivalent raw REST body is `{"parameters":[{"name":..,"value":..,"type":..}]}` (proven below).

### 2. FastAPI Jinja partial render (render `_rows.html` alone)

Extract the `<tr>` loop into `templates/_rows.html` so the row markup lives in ONE place:

```jinja
{# _rows.html — the <tr> rows ONLY, no <table>/<tbody> wrapper #}
{% for row in rows %}
<tr data-metric-group="{{ row.metric_group }}" data-metric-name="{{ row.metric_name }}">
  <th scope="row">{{ row.metric_name }}</th>
  <td class="text-right">{{ row.value_cy_fmt }}</td>
  <td class="text-right">{{ row.value_py_fmt }}</td>
  <td class="text-right">{{ row.pct_fmt }}</td>
</tr>
{% endfor %}
```

`glance.html` includes it for the initial render (give the tbody a stable id):
```jinja
<tbody id="glance-tbody">
  {% include "_rows.html" %}
</tbody>
```

The new endpoint renders the fragment alone and returns HTML (NOT JSON):
```python
@app.get("/table", response_class=HTMLResponse)
async def table(request: Request, report_date: str, drain: str) -> HTMLResponse:
    ...  # extract token, validate params, run parameterized SQL, shape rows
    return templates.TemplateResponse(request, "_rows.html", {"rows": rows})
```
Endpoint contract: `GET /table?report_date=<%Y-%m-%d %H:%M:%S>&drain=<E|M|N|ALL>` → `text/html` body containing just the `<tr>` rows for that (report_date, drain). Note the Starlette signature order: `TemplateResponse(request, name, context)` — matches how `main.py` already calls it (request-first form). Keep the same `error.html` 401/403 handling if you want the fragment path to degrade gracefully, though for a fragment a bare error message row is fine.

### 3. Vanilla JS — tiny, dependency-free, LOCAL static path

Serve from `static/js/app.js` and reference as a LOCAL path (air-gap: no CDN, no unpkg). Add to `base.html` (or `glance.html`) before `</body>`:
```html
<script src="/static/js/app.js" defer></script>
```

Minimal behavior:
```javascript
// app.js — no framework, no npm
(function () {
  var search = document.getElementById("glance-search");
  var dateSel = document.getElementById("glance-report-date");
  var drainSel = document.getElementById("glance-drain");
  var tbody = document.getElementById("glance-tbody");

  function applySearch() {
    var q = (search.value || "").trim().toLowerCase();
    var rows = tbody.querySelectorAll("tr");
    rows.forEach(function (tr) {
      var name = (tr.getAttribute("data-metric-name") || "").toLowerCase();
      tr.style.display = name.indexOf(q) === -1 ? "none" : "";
    });
  }

  async function refreshTable() {
    var url = "/table?report_date=" + encodeURIComponent(dateSel.value) +
              "&drain=" + encodeURIComponent(drainSel.value);
    var resp = await fetch(url, { headers: { "Accept": "text/html" } });
    tbody.innerHTML = await resp.text();   // swap the server-rendered fragment
    applySearch();                          // re-apply current search to new rows
  }

  if (search) search.addEventListener("input", applySearch);
  if (dateSel) dateSel.addEventListener("change", refreshTable);
  if (drainSel) drainSel.addEventListener("change", refreshTable);
})();
```
Key points: (a) both selects send BOTH current values (filters combine AND + preserve — changing one keeps the other); (b) after every fragment swap, `applySearch()` re-runs so the search text survives report-date/DRAIN changes; (c) search is pure client-side show/hide by `data-metric-name` substring — no server call. Optional loading indicator (deferred, planner discretion) can toggle a class on the table around the `await`.

### 4. Validation helpers (PURE, unit-testable — put in `queries.py`)

Validate against the allowed sets, reject with a clear message the route turns into HTTP 400. Keep them pure so `test_queries.py` covers them offline:
```python
def validate_drain(drain: str) -> str:
    """Return drain if in the fixed enum, else raise ValueError."""
    if drain not in VALID_DRAINS:
        raise ValueError(f"invalid drain {drain!r}; must be one of {VALID_DRAINS}")
    return drain

def validate_report_date(report_date: str, allowed: set[str] | tuple[str, ...]) -> str:
    """Return report_date if it is in the known allowed set, else raise ValueError."""
    if report_date not in allowed:
        raise ValueError(f"invalid report_date {report_date!r}; not in allowed set")
    return report_date

def build_glance_query_for_date(catalog: str, schema: str) -> str:
    """Parameterized SQL: WHERE report_date = :report_date AND drain = :drain."""
    fqn = _fqn(catalog, schema)
    return (
        "SELECT metric_name, metric_group, sort_order, value_cy, value_py, pct_change "
        f"FROM {fqn} "
        "WHERE report_date = :report_date AND drain = :drain "
        "ORDER BY sort_order"
    )
```
The `allowed` report_date set is the DISTINCT list `build_report_dates_query` already returns (formatted via `format_report_date`). The `/table` route should re-fetch (or the planner may cache within the request) that distinct set to validate against, then reject unknown values with `HTTPException(status_code=400, ...)`. Note `build_glance_query_for_date` takes NO user string interpolation — the specific date/drain arrive only as bound parameters, so it is inherently injection-safe (the enum validation on drain is defense-in-depth + gives a clean 400 rather than an empty result).

### From `python-dev/SKILL.md`
Pure, type-hinted, Google-docstring functions; pytest in `./tests/`; the new builder + validators must import and run with NO network/SDK — mirror the existing `test_queries.py` substring-assertion style.

---

## Live Param-Query Check (result)

Ran the exact CLI Statement Execution call from the task (warehouse `2f225c0740dcd22b`, `report_date=2026-01-08 00:00:00` TIMESTAMP, `drain=E` STRING):

```
state:      SUCCEEDED
row_count:  17
first rows: ['PY Filed In 2026','1','2718790','2178682','24.8']
            ['ERO Accepted (original)','2','1674811','1927846','-13.1']
            ['Online Accepted (original)','3','1158650','15.4' ...]
error:      None
```

**Result: PASS.** Returns exactly **17 rows**. The `TIMESTAMP` param type with the `"%Y-%m-%d %H:%M:%S"` value string bound correctly (no cast error), and `STRING` drain matched. This validates the exact `parameters=[...]` shape the app will use, and confirms `shaping.format_report_date`'s output string is directly usable as the bound `report_date` value (no reformatting needed between the `<select>` and the SQL binder). Numeric columns come back as strings (e.g. `'2718790'`) — already handled by `shaping._to_int` / `_to_float`.

---

## Recommended Approach (files to add / modify)

### Add
| File | Purpose |
|---|---|
| `src/app/templates/_rows.html` | The `<tr>` rows fragment (single source of row markup). |
| `src/app/static/js/app.js` | Vanilla JS: search show/hide + select→fetch `/table`→swap `<tbody>`→re-apply search. |
| `tests/test_queries.py` (extend) | Tests for `build_glance_query_for_date` (FQN, `:report_date`/`:drain`, `ORDER BY sort_order`), `validate_drain`, `validate_report_date` (accept in-set, reject out-of-set). |

### Modify
| File | Change |
|---|---|
| `src/app/queries.py` | Add `build_glance_query_for_date`, `validate_drain`, `validate_report_date`. Keep existing builders. |
| `src/app/main.py` | (a) import `StatementParameterListItem`; (b) extend `_run_sql` to accept optional `parameters=None` and forward it to `execute_statement`; (c) add `GET /table?report_date&drain` route — extract token (reuse `extract_user_token`), fetch/validate against distinct report_dates + `VALID_DRAINS` (400 on bad input), run `build_glance_query_for_date` with the two bound params, `rows_to_context`, return `_rows.html` fragment. |
| `src/app/templates/glance.html` | Give `<tbody>` an id (`glance-tbody`); replace the inline `{% for %}` with `{% include "_rows.html" %}`. |
| `src/app/templates/base.html` (or glance.html) | Add `<script src="/static/js/app.js" defer></script>` before `</body>`. |

### `_run_sql` signature change sketch
```python
async def _run_sql(token, sql, parameters=None):
    resp = await asyncio.to_thread(
        client.statement_execution.execute_statement,
        warehouse_id=_env("DATABRICKS_WAREHOUSE_ID"),
        statement=sql,
        parameters=parameters,          # None for the existing MAX/dates queries
        wait_timeout="30s",
    )
    ...
```
`parameters=None` is accepted by the SDK/REST (equivalent to omitting it), so the existing `/` calls are unaffected.

### Reuse unchanged
- `auth.extract_user_token`, `shaping.rows_to_context` / `format_report_date` / `format_pct` / `format_count`, the `_user_client` `auth_type="pat"` OBO client, `error.html`, `DRAIN_OPTIONS`, the vendored USWDS assets, `resources/app.yml` (no new resource), the Phase 1 `efile_seed` job (untouched).

### Deploy (unchanged mechanics)
`databricks bundle deploy --target dev` then `databricks bundle run download_hub` (app redeploys are slow — fresh venv build). Manual checkpoint verifies JS behavior (no JS unit harness — air-gap, no npm).

---

## Recommended References (for the executor)
- `~/.ai-dev-kit/repo/.claude/skills/databricks-python-sdk/SKILL.md` — Statement Execution + the CRITICAL `asyncio.to_thread` rule for FastAPI (the SDK is fully synchronous).
- `~/.ai-dev-kit/repo/.claude/skills/python-dev/SKILL.md` — pure functions, type hints, pytest in `./tests/`.
- In-repo (authoritative — read before coding): `src/app/main.py`, `src/app/queries.py`, `src/app/shaping.py`, `src/app/auth.py`, `src/app/templates/glance.html`, `src/app/templates/base.html`, `tests/test_queries.py`.
- `.planning/phases/2-RESEARCH.md` — the OBO / `auth_type="pat"` / Statement-Execution patterns this phase reuses.

---

## Risks / Notes
- **`report_date` string equality is the linchpin.** The `<select>` option value, the `format_report_date` output, and the bound `TIMESTAMP` param string must all be identical (`"%Y-%m-%d %H:%M:%S"`). Confirmed live that this exact string binds. Do NOT let the browser or any JS reformat the value — pass it through with `encodeURIComponent` verbatim.
- **Validate before binding, for a clean 400.** An out-of-set report_date would otherwise bind fine and just return 0 rows (silent empty table). `validate_report_date` against the DISTINCT set gives an explicit 400 instead — better UX and a testable pure function.
- **`_run_sql` must forward `parameters`.** Easy to miss; without it the `:report_date`/`:drain` placeholders raise "parameter not bound". Add the arg and pass `parameters=None` on the existing calls.
- **SDK class not locally importable** (dev `.venv` lacks `databricks-sdk`; it lives in the Apps runtime). Guidance rests on the confirmed live REST payload + stable class name `databricks.sdk.service.sql.StatementParameterListItem`. If `bundle`/runtime ever errors on the import, the raw-dict fallback is proven.
- **Fragment error handling:** `/table` failures (missing token / UC denied) — return a small inline message; a full `error.html` page swapped into `<tbody>` would render malformed. Planner discretion; keep it minimal.
- **`glance.html` DRY:** after extracting `_rows.html`, the initial page and the fragment share identical row markup — any future column change edits ONE file.
- **No new runtime dependency** this phase (openpyxl/download/audit/group-gating are all Phase 4). Search stays client-side; only report-date/DRAIN hit the server.
