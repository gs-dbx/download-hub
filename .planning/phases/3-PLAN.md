---
phase: 3-interactivity-search-report-drain-filters
plan: 3
type: execute
status: planned
workspace_url: https://field-eng-aws-gov-demo-irs-demo.cloud.databricks.us
default_catalog: irs
skill_references:
  - ~/.ai-dev-kit/repo/.claude/skills/python-dev/SKILL.md
  - ~/.ai-dev-kit/repo/.claude/skills/databricks-python-sdk/SKILL.md
wave_count: 5
---

# Phase 3: Interactivity — search, report selector, DRAIN filter

## Goal
Make the three controls on the live `download-hub` app work: a client-side metric-name search,
plus a report-date `<select>` and DRAIN `<select>` that each trigger a **server round-trip** (fresh
OBO parameterized query) returning a **server-rendered `<tr>` fragment** the JS swaps into the table
body. Filters combine (AND) + preserve; search re-applies after every fragment swap.

## Workspace Context
- Extends the deployed `download-hub` app. Read-only from `irs.efile.daily_efile_glance` via OBO.
- No new UC objects, no new bundle resources, no new runtime dependency.
- Live-verified: the parameterized query `WHERE report_date = :report_date AND drain = :drain`
  (TIMESTAMP value `"2026-01-08 00:00:00"`, STRING drain) returns 17 rows on warehouse 2f225c0740dcd22b.

## Prerequisites
- [ ] Phase 2 complete & live (app renders via OBO). Branch dbx/download-hub-phase-1.
- [ ] CLI OAuth profile DEFAULT valid.

## Skills to Read Before Executing
- `~/.ai-dev-kit/repo/.claude/skills/databricks-python-sdk/SKILL.md` — Statement Execution; **`asyncio.to_thread` around every SDK call**.
- `~/.ai-dev-kit/repo/.claude/skills/python-dev/SKILL.md` — pure functions, type hints, pytest.
- In-repo (authoritative): `src/app/main.py`, `src/app/queries.py`, `src/app/shaping.py`, `src/app/auth.py`, `src/app/templates/glance.html`, `src/app/templates/base.html`, `tests/test_queries.py`.
- `.planning/phases/3-RESEARCH.md` — exact code sketches (parameterized exec, Jinja partial, JS, validators).

---

## LOCKED DECISIONS (executor MUST follow verbatim)

### L1 — Parameterized query, never string-interpolated
`build_glance_query_for_date(catalog, schema)` returns SQL with NAMED placeholders
`WHERE report_date = :report_date AND drain = :drain ORDER BY sort_order` (3-level FQN from `_fqn`).
The route binds values via `parameters=[StatementParameterListItem(name="report_date",
value=<"%Y-%m-%d %H:%M:%S">, type="TIMESTAMP"), StatementParameterListItem(name="drain",
value=<E|M|N|ALL>, type="STRING")]`. Import from `databricks.sdk.service.sql`. NO user string is
interpolated into SQL.

### L2 — Validate against allowed sets → clean HTTP 400
Before binding: `validate_drain(drain)` (∈ {E,M,N,ALL}) and `validate_report_date(report_date,
allowed)` where `allowed` is the DISTINCT report_date set (from `build_report_dates_query`,
formatted via `format_report_date`). On ValueError the `/table` route returns
`HTTPException(status_code=400, ...)`. Both validators are PURE and unit-tested.

### L3 — Server fragment, not JSON
`/table` returns a server-rendered HTML fragment of ONLY the `<tr>` rows (`_rows.html`), via
`templates.TemplateResponse(request, "_rows.html", {"rows": rows})`, `response_class=HTMLResponse`.
The initial `/` render uses the SAME partial via `{% include "_rows.html" %}` so row markup lives in
ONE place. All number/pct formatting stays in `shaping.rows_to_context` (Python), not JS.

### L4 — OBO unchanged, `_run_sql` forwards parameters
Reuse `auth.extract_user_token`, `_user_client` (with `auth_type="pat"` — the Phase-2 OBO fix,
see [[reference_databricks_apps_obo_auth]]), and `asyncio.to_thread`. Extend `_run_sql` to accept
`parameters=None` and forward it to `execute_statement` (None is safe for the existing `/` calls).
Build a fresh client per request.

### L5 — Minimal vanilla JS, local static only
`src/app/static/js/app.js` (no framework/npm): search `input` → show/hide `<tr>` by
`data-metric-name` substring (case-insensitive); report-date/DRAIN `change` → fetch
`/table?report_date=<encodeURIComponent>&drain=<encodeURIComponent>`, `tbody.innerHTML = await
resp.text()`, then re-apply search. Both selects always send BOTH current values (combine + preserve).
Referenced as `<script src="/static/js/app.js" defer>` — LOCAL path only, no CDN.

### L6 — report_date string is the linchpin
The `<select>` option value, `format_report_date` output, and the bound TIMESTAMP param string MUST
be byte-identical (`"%Y-%m-%d %H:%M:%S"`). JS passes it verbatim via `encodeURIComponent` — no
reformatting.

---

## Wave 1: queries.py — parameterized builder + validators (pure)

<task type="auto">
  <name>Add build_glance_query_for_date, validate_drain, validate_report_date</name>
  <wave>1</wave>
  <files>src/app/queries.py</files>
  <read_first>
    - ~/.ai-dev-kit/repo/.claude/skills/python-dev/SKILL.md (pure funcs, type hints, docstrings, ValueError)
    - src/app/queries.py (existing builders + VALID_DRAINS + _fqn to reuse)
    - .planning/phases/3-RESEARCH.md (§4 validation helpers + builder sketch)
  </read_first>
  <action>
    Add to queries.py (keep all existing functions). Reuse VALID_DRAINS and _fqn.
    - `build_glance_query_for_date(catalog: str, schema: str) -> str` — returns
      "SELECT metric_name, metric_group, sort_order, value_cy, value_py, pct_change FROM {fqn}
       WHERE report_date = :report_date AND drain = :drain ORDER BY sort_order" (named placeholders;
      NO interpolation of date/drain).
    - `validate_drain(drain: str) -> str` — return drain if in VALID_DRAINS else raise ValueError.
    - `validate_report_date(report_date: str, allowed) -> str` — return report_date if in `allowed`
      (set/tuple of formatted strings) else raise ValueError.
    All typed, Google docstrings, pure (no SDK/network import).
  </action>
  <verify>cd /Users/greg.skinner/Documents/IRS/download_hub && .venv/bin/python -m py_compile src/app/queries.py && PYTHONPATH=src .venv/bin/python -c "from app.queries import build_glance_query_for_date, validate_drain, validate_report_date; print(':report_date' in build_glance_query_for_date('irs','efile'), validate_drain('ALL'), validate_report_date('2026-01-12 00:00:00', {'2026-01-12 00:00:00'}))"</verify>
  <acceptance_criteria>
    - build_glance_query_for_date has 3-level FQN, named :report_date and :drain placeholders, ORDER BY sort_order, and NO f-string interpolation of date/drain values.
    - validate_drain accepts E/M/N/ALL and raises ValueError otherwise.
    - validate_report_date accepts an in-set value and raises ValueError for an out-of-set value.
    - No SDK/fastapi/network import in queries.py.
  </acceptance_criteria>
</task>

---

## Wave 2: main.py — parameters plumbing + /table route

<task type="auto">
  <name>Extend _run_sql for parameters and add the GET /table route</name>
  <wave>2</wave>
  <files>src/app/main.py</files>
  <read_first>
    - ~/.ai-dev-kit/repo/.claude/skills/databricks-python-sdk/SKILL.md (Statement Execution params; asyncio.to_thread)
    - src/app/main.py (existing _run_sql, _user_client, / route, _env, DRAIN_OPTIONS)
    - src/app/queries.py (new builder + validators), src/app/shaping.py (rows_to_context, format_report_date)
    - .planning/phases/3-RESEARCH.md (§1 param exec, §2 endpoint sketch, _run_sql signature change)
  </read_first>
  <action>
    - Import `StatementParameterListItem` from databricks.sdk.service.sql (same module as StatementState).
    - Extend `_run_sql(token, sql, parameters=None)` to forward `parameters=parameters` to
      execute_statement (None safe for existing calls). Update the existing `/` calls to pass no params
      (default) — no behavior change.
    - Add `GET /table` (response_class=HTMLResponse), signature `(request, report_date: str, drain: str)`:
      1. extract token via auth.extract_user_token(request.headers); on PermissionError return a small
         inline HTML error row (NOT the full error.html page — it swaps into <tbody>) with an
         appropriate status.
      2. Read catalog/schema from _env. Fetch the DISTINCT report_dates (build_report_dates_query,
         shape to formatted strings via format_report_date) to form the `allowed` set.
      3. validate_drain(drain) and validate_report_date(report_date, allowed); on ValueError raise
         HTTPException(status_code=400, detail=...).
      4. Run build_glance_query_for_date(catalog, schema) with parameters=[StatementParameterListItem(
         name="report_date", value=report_date, type="TIMESTAMP"), StatementParameterListItem(
         name="drain", value=drain, type="STRING")] via _run_sql (asyncio.to_thread inside).
      5. rows = shaping.rows_to_context(cols, data); return templates.TemplateResponse(request,
         "_rows.html", {"rows": rows}).
    - Keep `/`, `/health`, error.html usage intact. No hardcoded host/token/warehouse (env only).
  </action>
  <verify>cd /Users/greg.skinner/Documents/IRS/download_hub && .venv/bin/python -m py_compile src/app/main.py && grep -c "parameters=" src/app/main.py && grep -c "/table" src/app/main.py</verify>
  <acceptance_criteria>
    - main.py compiles; imports StatementParameterListItem; _run_sql forwards parameters.
    - GET /table validates drain + report_date (400 on bad input), runs the parameterized query as the user (asyncio.to_thread), and returns the _rows.html fragment (HTMLResponse).
    - Missing-token path on /table degrades to a small inline message, not a full-page swap.
    - Existing / and /health routes unchanged in behavior; no hardcoded host/token/warehouse.
  </acceptance_criteria>
</task>

---

## Wave 3: templates + vanilla JS

<task type="auto">
  <name>Extract _rows.html, wire tbody id + include, add app.js</name>
  <wave>3</wave>
  <files>src/app/templates/_rows.html, src/app/templates/glance.html, src/app/templates/base.html, src/app/static/js/app.js</files>
  <read_first>
    - src/app/templates/glance.html (current inline row loop + control ids/data-roles)
    - src/app/templates/base.html (where to add the script tag)
    - .planning/phases/3-RESEARCH.md (§2 partial, §3 vanilla JS)
  </read_first>
  <action>
    - Create templates/_rows.html: the `<tr>` loop ONLY (no table/tbody wrapper), each row
      `<tr data-metric-group=.. data-metric-name=..>` with metric_name (th scope=row), value_cy_fmt,
      value_py_fmt, pct_fmt cells — copied verbatim from glance.html's current loop.
    - Edit glance.html: give the `<tbody>` `id="glance-tbody"` and replace the inline `{% for %}` with
      `{% include "_rows.html" %}`. Leave the search input / selects and their ids/data-roles intact.
    - Edit base.html: add `<script src="/static/js/app.js" defer></script>` before `</body>` (LOCAL path).
    - Create static/js/app.js per RESEARCH §3: search show/hide by data-metric-name; report-date/DRAIN
      change → fetch /table (encodeURIComponent both values) → set tbody.innerHTML → re-apply search.
      No framework, no CDN, no external fetch beyond the same-origin /table.
  </action>
  <verify>cd /Users/greg.skinner/Documents/IRS/download_hub && test -f src/app/templates/_rows.html && grep -q "glance-tbody" src/app/templates/glance.html && grep -q "_rows.html" src/app/templates/glance.html && grep -q "/static/js/app.js" src/app/templates/base.html && ! grep -RniE "unpkg|cdn|https?://" src/app/static/js/app.js && echo ok</verify>
  <acceptance_criteria>
    - _rows.html contains only the <tr> loop; glance.html includes it and its tbody has id glance-tbody.
    - base.html references /static/js/app.js (local, defer); no CDN/external URL in app.js.
    - app.js: search filters by data-metric-name; selects fetch /table with both values and swap tbody; search re-applied after swap.
    - Row markup exists in exactly one place (_rows.html) — no duplicated <tr> loop in glance.html.
  </acceptance_criteria>
</task>

---

## Wave 4: unit tests

<task type="auto">
  <name>Extend tests for the parameterized builder + validators</name>
  <wave>4</wave>
  <files>tests/test_queries.py</files>
  <read_first>
    - tests/test_queries.py (existing substring-assertion style to mirror)
    - src/app/queries.py (functions under test)
    - .planning/phases/3-PLAN.md (LOCKED L1/L2)
  </read_first>
  <action>
    Add tests (no network/SDK):
    - build_glance_query_for_date: contains irs.efile.daily_efile_glance (3-level), ":report_date",
      ":drain", "ORDER BY sort_order"; and asserts NO literal date/drain value is interpolated
      (e.g. "2026-" not in the string, "'ALL'" not in the string).
    - validate_drain: E/M/N/ALL pass; "X" and "" raise ValueError.
    - validate_report_date: in-set value returns it; out-of-set raises ValueError.
    Keep existing tests intact.
  </action>
  <verify>cd /Users/greg.skinner/Documents/IRS/download_hub && PYTHONPATH=src .venv/bin/python -m pytest tests/ -q</verify>
  <acceptance_criteria>
    - All tests pass (prior 30 + the new ones).
    - A test asserts build_glance_query_for_date does NOT interpolate a literal date/drain (injection-safety).
    - validate_drain / validate_report_date accept-and-reject cases covered.
  </acceptance_criteria>
</task>

---

## Checkpoint: deploy + verify filters live

<task type="checkpoint:human">
  <name>Redeploy app, verify search/report-date/DRAIN behavior</name>
  <wave>5</wave>
  <action>
    From repo root (branch dbx/download-hub-phase-1):
    1. PYTHONPATH=src .venv/bin/python -m pytest tests/ -q
    2. databricks bundle validate -t dev -p DEFAULT
    3. databricks bundle deploy --target dev -p DEFAULT
    4. databricks bundle run download_hub -t dev -p DEFAULT   (app redeploy is slow — fresh venv)
    5. databricks apps get download-hub -p DEFAULT   → ACTIVE / SUCCEEDED
    6. Confirm via app logs that GET /table returns 200 for a selection (and 400 for a bogus report_date).
       Browser check (as greg.skinner): changing report-date or DRAIN swaps the 17 rows; typing in
       search trims rows live and survives a report-date/DRAIN change; combos apply together.
  </action>
  <acceptance_criteria>
    - pytest passes; bundle validate + deploy succeed (standard engine).
    - App ACTIVE; GET /table returns 200 for valid params and 400 for an out-of-set report_date.
    - Search trims rows client-side and persists across report-date/DRAIN changes; report-date/DRAIN each re-query and swap the fragment.
  </acceptance_criteria>
</task>

---

## Must-Haves
```yaml
truths:
  - report-date/DRAIN do a server round-trip returning a Jinja _rows.html fragment; search is client-side (metric name).
  - SQL is parameterized (:report_date TIMESTAMP, :drain STRING) — no interpolation; validated against allowed sets (400 on bad input).
  - Row markup lives only in _rows.html (glance.html includes it; /table renders it alone).
  - OBO unchanged: extract_user_token + auth_type="pat" client + asyncio.to_thread; _run_sql forwards parameters.
  - app.js is vanilla, served local (/static/js/app.js), no CDN; filters combine (AND) + preserve; search re-applied after swap.
  - No new UC objects, no new bundle resource, no new runtime dependency.
artifacts:
  - src/app/queries.py (build_glance_query_for_date, validate_drain, validate_report_date)
  - src/app/main.py (_run_sql parameters, GET /table)
  - src/app/templates/_rows.html, glance.html (tbody id + include), base.html (app.js tag)
  - src/app/static/js/app.js
  - tests/test_queries.py (extended)
uc_targets:
  - irs.efile.daily_efile_glance (READ ONLY via OBO, parameterized)
```
