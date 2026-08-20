---
phase: 4-gated-download-ack-justification-audit-export
plan: 4
type: execute
status: planned
workspace_url: https://field-eng-aws-gov-demo-irs-demo.cloud.databricks.us
default_catalog: irs
skill_references:
  - ~/.ai-dev-kit/repo/.claude/skills/python-dev/SKILL.md
  - ~/.ai-dev-kit/repo/.claude/skills/databricks-python-sdk/SKILL.md
wave_count: 7
---

# Phase 4: Gated download — acknowledgement, justification, audit, CSV/Excel

## Goal
Add a download feature to the `download-hub` app, available ONLY to members of the Databricks
group `efile_glance_download_users`. The flow requires a data-handling acknowledgement + a written
justification, exports the current view as CSV or Excel (with the acknowledged disclaimer riding at
the top of the file), and writes exactly one audit row to `irs.efile.download_audit` (as the app
service principal) plus an app-log line — audit-first, so no un-audited download is ever returned.

## Workspace Context (live, 2026-08-13)
- App `download-hub` ACTIVE. App SP appId (runtime identity / grant target):
  **`97898a88-5dfd-4c75-bd0b-a6279a13ea08`** (≠ oauth2_app_client_id).
- greg.skinner is a workspace admin → can create groups + apply grants.
- Groups `efile_glance_app_users` / `efile_glance_download_users` do NOT exist yet (create here).
- `irs.efile.download_audit` exists, empty, 11 columns (verified). `iam.current-user:read` scope is
  effective → OBO `me().groups` works. greg.skinner numeric id `75113935367499`.
- dev `.venv` has ONLY pytest (no fastapi/jinja2/databricks-sdk/openpyxl) → all new PURE modules must
  be stdlib-only at import time; XLSX test uses `pytest.importorskip("openpyxl")`.

## Prerequisites
- [ ] Phases 1–3 complete; branch dbx/download-hub-phase-1; CLI profile DEFAULT valid (admin).

## Skills to Read Before Executing
- `~/.ai-dev-kit/repo/.claude/skills/databricks-python-sdk/SKILL.md` — Statement Execution, params, **asyncio.to_thread**, auth.
- `~/.ai-dev-kit/repo/.claude/skills/python-dev/SKILL.md` — pure funcs, type hints, pytest.
- In-repo: src/app/main.py, auth.py, queries.py, shaping.py, templates/glance.html, base.html, resources/grants.sql, tests/test_auth.py.
- `.planning/phases/4-RESEARCH.md` — all code sketches + live findings (authoritative).
- Memory `reference_databricks_apps_obo_auth` — user-token (auth_type="pat") vs SP (plain WorkspaceClient()).

---

## LOCKED DECISIONS (executor MUST follow verbatim)

### L1 — Download gating: OBO group membership, re-checked server-side
- `can_download` = the signed-in user (OBO `current_user.me()`) belongs to a group whose display
  name == `efile_glance_download_users`. The `me()` call is an I/O boundary in main.py
  (`asyncio.to_thread`); the name-match is a PURE helper (`is_member`) in auth.py (unit-tested).
- The download panel renders ONLY when `can_download`. `POST /download` RE-CHECKS membership and
  returns 403 if not a member. **Never fail open:** unresolved membership → `can_download=False`.
- Data access stays UC-enforced: `/download` re-queries the gold table AS THE USER (OBO), so a
  member lacking SELECT still gets nothing (FR-7 = group AND table access).

### L2 — Audit write as the app SP, audit-first
- The audit INSERT runs as the app service principal via a plain `WorkspaceClient()` (no token, no
  `auth_type` — picks up injected DATABRICKS_CLIENT_ID/SECRET). Contrast the user client which passes
  an explicit token + `auth_type="pat"`. Construct the SP client once (module-level lazy singleton).
- Parameterized INSERT into `irs.efile.download_audit` (all 11 cols; `audit_id`=uuid4 string,
  `event_ts`=`current_timestamp()` in SQL — no param, `row_count`=len(exported rows),
  `acknowledged`=true). Every `StatementParameterListItem.value` is a STRING (cast row_count/bool).
- **Audit-first (NFR-5):** validate → group re-check → OBO re-query → build file → write audit
  (must reach SUCCEEDED, else HTTP 500, no file) → emit app-log line → return the file.

### L3 — Validation
- `acknowledged` truthy (`in {"true","on","1","yes"}` after strip/lower) AND `justification.strip()`
  non-empty → else HTTP 400. Also `validate_drain(drain)` + `validate_report_date(report_date,
  allowed)` (allowed = distinct report_dates). All server-side.

### L4 — Exports + disclaimer, single source of truth
- New PURE `exports.py`: `DISCLAIMER` constant (the exact acknowledged text — ALSO passed to the
  template so the checkbox label == the file's embedded text), `filename_for(report_date, drain,
  fmt)`, `to_csv_bytes(rows, disclaimer)` (stdlib csv), `to_xlsx_bytes(rows, disclaimer)` (lazy
  `import openpyxl` inside the fn). Columns: Metric, 2026 (value_cy), 2025 (value_py), % Change
  (pct_fmt; `—` for NULL). Disclaimer rides at the TOP (CSV: leading `# ` rows then blank then
  header; XLSX: merged wrapped top rows then blank then bold header). XLSX puts raw ints for
  CY/PY (numeric cells), pct_fmt as the display string.
- The export applies the same metric-name `search` substring filter the user sees (server re-applies
  it to the re-queried rows so the file matches the on-screen view).

### L5 — Dependencies
- Add `openpyxl` AND `python-multipart` to `src/app/requirements.txt` (multipart is REQUIRED for
  FastAPI form parsing — missing it → 500 on POST). Keep CSV on stdlib. No other new deps.

### L6 — Grants target the appId
- In `resources/grants.sql`, grant to the SP by its **appId** `97898a88-5dfd-4c75-bd0b-a6279a13ea08`
  (backtick-quoted), NOT numeric id/name/oauth2 id:
  `GRANT USE CATALOG ON CATALOG irs`, `GRANT USE SCHEMA ON SCHEMA irs.efile`,
  `GRANT MODIFY ON TABLE irs.efile.download_audit`, `GRANT SELECT ON TABLE irs.efile.download_audit`.
  Also apply the Phase-1 `efile_glance_app_users` SELECT lines (group now exists).

### L7 — report_date string linchpin (carried from Phase 3)
- Hidden `report_date` field value == select value == bound TIMESTAMP string `"%Y-%m-%d %H:%M:%S"`,
  passed verbatim. JS keeps the hidden `report_date`/`drain`/`search` fields in sync with the live
  controls so the export matches what's on screen.

---

## Wave 1: pure exports.py + audit.py

<task type="auto">
  <name>Add pure exports.py (CSV/XLSX + DISCLAIMER) and audit.py (row + INSERT builder)</name>
  <wave>1</wave>
  <files>src/app/exports.py, src/app/audit.py</files>
  <read_first>
    - ~/.ai-dev-kit/repo/.claude/skills/python-dev/SKILL.md
    - .planning/phases/4-RESEARCH.md (Exports + Audit Write sketches — copy them)
    - src/app/shaping.py (the row dict shape exports consume)
  </read_first>
  <action>
    exports.py (PURE, stdlib at import; openpyxl imported lazily inside to_xlsx_bytes):
    - `DISCLAIMER` — a clear multi-line data-handling warning the user acknowledges (e.g. sensitivity,
      protect the data, authorized use only, do not redistribute). This exact text is embedded in
      every export AND shown as the checkbox label.
    - `filename_for(report_date: str, drain: str, fmt: str) -> str` → daily_efile_glance_<date>_<drain>.<ext>
      (date portion sanitized: strip time/colons/spaces).
    - `to_csv_bytes(rows, disclaimer) -> bytes` — leading `# `-prefixed disclaimer rows, blank row,
      header [Metric,2026,2025,% Change], then value_cy_fmt/value_py_fmt/pct_fmt.
    - `to_xlsx_bytes(rows, disclaimer) -> bytes` — lazy `from openpyxl import Workbook`; disclaimer as
      merged wrapped italic top rows, blank spacer, bold header, then rows (raw int value_cy/value_py
      numeric cells, pct_fmt string). Save to io.BytesIO.
    audit.py (PURE, stdlib only):
    - `build_audit_row(*, user_email, report_date, drain_filter, search_filter, row_count,
      export_format, justification, app_version, acknowledged=True, audit_id=None) -> dict` (audit_id
      defaults to uuid4 str; search_filter defaults to "").
    - `build_audit_insert(catalog, schema, row) -> tuple[str, list[dict]]` — parameterized INSERT into
      {catalog}.{schema}.download_audit (11 cols; event_ts=current_timestamp() no param; other values
      as :named params; every value a STRING incl. str(row_count) and "true"/"false"). Return
      (sql, params) where params are {"name","value","type"} dicts (main.py maps to
      StatementParameterListItem). NO SDK import.
  </action>
  <verify>cd /Users/greg.skinner/Documents/IRS/download_hub && .venv/bin/python -m py_compile src/app/exports.py src/app/audit.py && PYTHONPATH=src .venv/bin/python -c "from app.exports import to_csv_bytes, DISCLAIMER, filename_for; from app.audit import build_audit_row, build_audit_insert; b=to_csv_bytes([{'metric_name':'X','value_cy_fmt':'1','value_py_fmt':'2','pct_fmt':'—'}], DISCLAIMER); print(b'# ' in b, filename_for('2026-01-12 00:00:00','ALL','csv')); s,p=build_audit_insert('irs','efile',build_audit_row(user_email='a@b.c',report_date='2026-01-12 00:00:00',drain_filter='ALL',search_filter='',row_count=17,export_format='csv',justification='j',app_version='0.4.0')); print('current_timestamp()' in s, len(p))"</verify>
  <acceptance_criteria>
    - exports.py + audit.py compile and import with ONLY stdlib (no fastapi/databricks-sdk/openpyxl at module scope).
    - to_csv_bytes output contains the disclaimer (leading '# ' lines) then the Metric/2026/2025/% Change header then rows; NULL pct renders as —.
    - to_xlsx_bytes imports openpyxl lazily (inside the fn) and returns non-empty bytes when openpyxl is present.
    - DISCLAIMER is a non-trivial multi-line constant reused by the template.
    - build_audit_row has all fields (uuid audit_id default, search_filter default ""); build_audit_insert returns a 3-level FQN INSERT with :named params, current_timestamp() for event_ts, and every param value a string (row_count/acknowledged cast).
  </acceptance_criteria>
</task>

---

## Wave 2: auth.py — email + group-membership helpers (pure)

<task type="auto">
  <name>Add extract_user_email, group_display_names, is_member, DOWNLOAD_GROUP</name>
  <wave>2</wave>
  <files>src/app/auth.py</files>
  <read_first>
    - src/app/auth.py (existing extract_user_token + _get_case_insensitive to reuse)
    - .planning/phases/4-RESEARCH.md (Group-Membership Approach — pure helpers)
  </read_first>
  <action>
    Extend auth.py (keep extract_user_token). All pure, no SDK/starlette import:
    - `DOWNLOAD_GROUP = "efile_glance_download_users"`.
    - `extract_user_email(headers) -> str` — read `x-forwarded-user` case-insensitively (reuse
      _get_case_insensitive); return "" if absent (email is best-effort for audit, not an auth gate).
    - `group_display_names(me_user) -> list[str]` — return [g.display for g in (me_user.groups or [])
      if g.display]; tolerate missing attrs via getattr.
    - `is_member(me_user, group_display) -> bool` — group_display in group_display_names(me_user).
  </action>
  <verify>.venv/bin/python -m py_compile src/app/auth.py && PYTHONPATH=src .venv/bin/python -c "
from types import SimpleNamespace as N
from app.auth import is_member, group_display_names, extract_user_email, DOWNLOAD_GROUP
u=N(groups=[N(display='efile_glance_download_users'), N(display='users')])
print(is_member(u, DOWNLOAD_GROUP), is_member(N(groups=[]), DOWNLOAD_GROUP), extract_user_email({'X-Forwarded-User':'a@b.c'}))"</verify>
  <acceptance_criteria>
    - auth.py imports with stdlib/typing only (no SDK/starlette).
    - is_member returns True when a group's .display matches, False for empty/no groups.
    - group_display_names tolerates missing .groups/.display via getattr.
    - extract_user_email reads x-forwarded-user case-insensitively, "" when absent.
    - DOWNLOAD_GROUP == "efile_glance_download_users".
  </acceptance_criteria>
</task>

---

## Wave 3: main.py — can_download on /, POST /download, SP client

<task type="auto">
  <name>Wire group check into / and add the POST /download route</name>
  <wave>3</wave>
  <files>src/app/main.py</files>
  <read_first>
    - ~/.ai-dev-kit/repo/.claude/skills/databricks-python-sdk/SKILL.md (asyncio.to_thread, Statement Execution, auth)
    - src/app/main.py (existing routes, _run_sql, _user_client, _env)
    - src/app/exports.py, src/app/audit.py, src/app/auth.py, src/app/queries.py, src/app/shaping.py
    - .planning/phases/4-RESEARCH.md (FastAPI Download+Form; SP client; audit-first ordering)
  </read_first>
  <action>
    - Imports: `Form` from fastapi, `Response` from fastapi.responses; from auth add
      extract_user_email/is_member/DOWNLOAD_GROUP/group_display_names; from audit build_audit_insert
      (+ build_audit_row); from exports to_csv_bytes/to_xlsx_bytes/DISCLAIMER/filename_for.
    - `_app_sp_client()` — lazy module-singleton `WorkspaceClient()` (no token, no auth_type — SP OAuth).
    - `/` route: after the OBO read, compute `me_user = await asyncio.to_thread(_user_client(token).current_user.me)`
      then `can_download = is_member(me_user, DOWNLOAD_GROUP)`; wrap in try/except so a me() failure →
      can_download=False (never fail open, never break the page). Pass `can_download` and `disclaimer=DISCLAIMER`
      into the glance.html context.
    - `POST /download` (Form params: acknowledged="", justification="", format="csv", report_date=...,
      drain=..., search=""):
      1. token=extract_user_token(headers); email=extract_user_email(headers).
      2. me_user via asyncio.to_thread; if not is_member → HTTPException(403).
      3. validate acknowledged truthy AND justification.strip() != "" (else 400); validate_drain(drain);
         fetch distinct report_dates (build_report_dates_query, format) → validate_report_date (400).
      4. OBO re-query via build_glance_query_for_date + StatementParameterListItem[report_date TIMESTAMP,
         drain STRING] → rows_to_context.
      5. Apply metric-name `search` substring filter (case-insensitive) to the rows server-side.
      6. Build bytes: csv → to_csv_bytes, xlsx → to_xlsx_bytes; media types text/csv resp.
         application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.
      7. AUDIT-FIRST: row=build_audit_row(... row_count=len(filtered rows), export_format=format ...);
         sql,params=build_audit_insert(catalog,schema,row); run via SP client statement_execution
         (asyncio.to_thread, map params→StatementParameterListItem, warehouse from env); if not SUCCEEDED
         → HTTPException(500). Emit an app-log line (print/logging) with user_email+audit_id+row_count.
      8. return Response(content=bytes, media_type=..., headers={"Content-Disposition": f'attachment; filename="{filename_for(...)}"'}).
    - No hardcoded host/token/warehouse (env only). Keep /, /table, /health, error.html intact.
  </action>
  <verify>cd /Users/greg.skinner/Documents/IRS/download_hub && .venv/bin/python -m py_compile src/app/main.py && grep -c "can_download" src/app/main.py && grep -c "/download" src/app/main.py && grep -c "_app_sp_client" src/app/main.py</verify>
  <acceptance_criteria>
    - main.py compiles; / passes can_download + disclaimer to the template; me() failure degrades to can_download=False (page still renders).
    - POST /download: 403 non-member; 400 on missing ack/justification or bad drain/report_date; audit-first (SP INSERT must SUCCEED before file; else 500); returns Response attachment with correct media type + filename.
    - Audit INSERT runs via _app_sp_client() (plain WorkspaceClient, no auth_type); OBO re-query runs via the user client.
    - App-log line emitted on each successful download; no hardcoded host/token/warehouse.
  </acceptance_criteria>
</task>

---

## Wave 4: template download panel

<task type="auto">
  <name>Add the conditional download panel to glance.html + sync hidden fields in app.js</name>
  <wave>4</wave>
  <files>src/app/templates/glance.html, src/app/static/js/app.js</files>
  <read_first>
    - src/app/templates/glance.html (content block, control ids)
    - src/app/static/js/app.js (existing select/search wiring)
    - .planning/phases/4-RESEARCH.md (template panel sketch; hidden-field sync note)
  </read_first>
  <action>
    - glance.html: add `{% if can_download %}` ... `{% endif %}` panel (USWDS form, method=post
      action=/download): hidden report_date (=selected_report_date), drain (=selected_drain), search
      (empty, JS-synced); acknowledgement checkbox name=acknowledged value="true" required with the
      `{{ disclaimer }}` text as its label; justification textarea name=justification required; format
      radios csv/xlsx (csv checked); submit button. Place it below the table.
    - app.js: keep existing behavior; ADD keeping the panel's hidden inputs in sync — on report-date/
      DRAIN change set the hidden report_date/drain to the selects' current values; on search input set
      the hidden search to the box value. Guard for the panel being absent (non-members). No CDN.
  </action>
  <verify>cd /Users/greg.skinner/Documents/IRS/download_hub && grep -q "can_download" src/app/templates/glance.html && grep -q 'name="justification"' src/app/templates/glance.html && grep -q 'action="/download"' src/app/templates/glance.html && ! grep -RniE "unpkg|jsdelivr|https?://" src/app/static/js/app.js && echo ok</verify>
  <acceptance_criteria>
    - Panel is wrapped in {% if can_download %} and contains ack checkbox (value="true", required), justification textarea (required), csv/xlsx format radios, submit, and hidden report_date/drain/search.
    - The checkbox label renders the {{ disclaimer }} text (single source of truth with exports.DISCLAIMER).
    - app.js keeps hidden report_date/drain/search synced with the live controls and guards when the panel is absent; no external URLs.
  </acceptance_criteria>
</task>

---

## Wave 5: unit tests

<task type="auto">
  <name>Add tests for exports + audit; extend auth tests</name>
  <wave>5</wave>
  <files>tests/test_exports.py, tests/test_audit.py, tests/test_auth.py</files>
  <read_first>
    - tests/test_auth.py, tests/test_queries.py (style to mirror)
    - src/app/exports.py, src/app/audit.py, src/app/auth.py
  </read_first>
  <action>
    test_exports.py: to_csv_bytes contains disclaimer lines + header + a known row; NULL pct → "—";
      filename_for sanitizes date and picks the extension; XLSX test guarded by
      `import pytest; openpyxl = pytest.importorskip("openpyxl")` then to_xlsx_bytes returns non-empty
      bytes starting with the zip magic (b"PK").
    test_audit.py: build_audit_row has 11 logical fields + uuid audit_id (valid uuid, default differs
      per call) + search_filter default ""; build_audit_insert → 3-level FQN, :named placeholders for
      all bound cols, current_timestamp() for event_ts (no param), every param value is a str
      (row_count "17", acknowledged "true"/"false"), correct param count.
    test_auth.py (extend): is_member true/false with a stub .groups[].display; group_display_names
      tolerates missing attrs; extract_user_email present/absent.
  </action>
  <verify>cd /Users/greg.skinner/Documents/IRS/download_hub && PYTHONPATH=src .venv/bin/python -m pytest tests/ -q</verify>
  <acceptance_criteria>
    - All tests pass (prior 43 + new). XLSX test skips cleanly if openpyxl absent, else asserts b"PK" prefix.
    - Audit test asserts current_timestamp() (not a bound param) for event_ts and all-string param values.
    - Auth test covers is_member true/false + extract_user_email present/absent.
  </acceptance_criteria>
</task>

---

## Wave 6: deps + grants

<task type="auto">
  <name>Add openpyxl + python-multipart; extend grants.sql with SP + app-users grants</name>
  <wave>6</wave>
  <files>src/app/requirements.txt, resources/grants.sql</files>
  <read_first>
    - src/app/requirements.txt (current 4 deps)
    - resources/grants.sql (Phase-1 app-users grants + deferral note)
    - .planning/phases/4-RESEARCH.md (grants targeting the appId)
  </read_first>
  <action>
    - requirements.txt: append `openpyxl` and `python-multipart` (keep fastapi/uvicorn/jinja2/databricks-sdk).
    - grants.sql: uncomment/keep the app-users SELECT lines (groups now created this phase), and ADD the
      4 SP grants to appId `97898a88-5dfd-4c75-bd0b-a6279a13ea08` (backtick-quoted): USE CATALOG irs,
      USE SCHEMA irs.efile, MODIFY on irs.efile.download_audit, SELECT on irs.efile.download_audit.
      Update the header comment (groups now exist; SP grant now included). 3-level names throughout.
  </action>
  <verify>cd /Users/greg.skinner/Documents/IRS/download_hub && grep -q openpyxl src/app/requirements.txt && grep -q python-multipart src/app/requirements.txt && grep -q "97898a88-5dfd-4c75-bd0b-a6279a13ea08" resources/grants.sql && grep -c "GRANT" resources/grants.sql</verify>
  <acceptance_criteria>
    - requirements.txt has openpyxl + python-multipart added (originals intact).
    - grants.sql has the 4 SP grants to the appId (MODIFY+SELECT on download_audit, USE CATALOG/SCHEMA) plus the app-users SELECT lines; all 3-level; no grant to numeric id/name/oauth2 id.
  </acceptance_criteria>
</task>

---

## Checkpoint: create groups, apply grants, deploy, verify download + audit

<task type="checkpoint:human">
  <name>Create groups + grants, deploy, verify gated download writes one audit row</name>
  <wave>7</wave>
  <action>
    From repo root (branch dbx/download-hub-phase-1); greg.skinner is admin.
    1. PYTHONPATH=src .venv/bin/python -m pytest tests/ -q
    2. Create groups + add self to download group:
       databricks groups create --display-name efile_glance_app_users -p DEFAULT
       databricks groups create --display-name efile_glance_download_users -p DEFAULT
       # get the download group id, then add greg.skinner (id 75113935367499) via groups patch (SCIM add members)
    3. Apply resources/grants.sql via Statement Execution API (warehouse 2f225c0740dcd22b), statement by
       statement: databricks api post /api/2.0/sql/statements -p DEFAULT --json '{"warehouse_id":"2f225c0740dcd22b","statement":"<GRANT ...>","wait_timeout":"30s"}'
    4. databricks bundle validate -t dev -p DEFAULT && databricks bundle deploy --target dev -p DEFAULT
    5. databricks bundle run download_hub -t dev -p DEFAULT   (slow — venv now builds openpyxl + python-multipart)
    6. databricks apps get download-hub -p DEFAULT  → ACTIVE/SUCCEEDED
    7. Browser (as greg.skinner, now in the download group): the download panel appears; check the
       acknowledgement, type a justification, choose CSV then Excel, Download → file downloads with the
       disclaimer at the top and the current view's rows.
    8. Verify audit: databricks api post /api/2.0/sql/statements -p DEFAULT --json '{"warehouse_id":"2f225c0740dcd22b","statement":"SELECT user_email, report_date, drain_filter, row_count, export_format, justification, acknowledged FROM irs.efile.download_audit ORDER BY event_ts DESC","wait_timeout":"30s"}'  → one row per download, justification populated, acknowledged=true.
    9. Confirm the app log shows the download event (databricks apps logs download-hub).
    10. Negative check (optional): a user NOT in the group sees no panel and POST /download → 403.
  </action>
  <acceptance_criteria>
    - Groups exist; greg.skinner in efile_glance_download_users; grants applied (SP + app-users) without error.
    - App deploys/starts ACTIVE. Panel visible to the member; CSV and Excel both download with the disclaimer at the top and rows matching the on-screen view.
    - Each download writes exactly one row to irs.efile.download_audit (justification + acknowledged=true + correct row_count/format) and an app-log line; audit-first (no file if the INSERT fails).
    - Non-member path: panel hidden and /download → 403.
  </acceptance_criteria>
</task>

---

## Must-Haves
```yaml
truths:
  - Download gated by OBO group membership (efile_glance_download_users), re-checked server-side (403); never fail open.
  - Data still UC-enforced: /download re-queries AS THE USER (OBO) — group AND SELECT both required (FR-7).
  - Audit row written as the app SP (plain WorkspaceClient()) audit-first; on INSERT failure return 500, no file (NFR-5).
  - Acknowledgement (true) + non-empty justification required (else 400); one audit row + app-log line per download.
  - Disclaimer is one constant (exports.DISCLAIMER) used as the checkbox label AND embedded at the top of every file.
  - CSV (stdlib) + Excel (openpyxl); export matches the on-screen view (report_date + drain + metric-name search).
  - openpyxl + python-multipart added to requirements; SP grants target appId 97898a88-...; all UC refs 3-level.
artifacts:
  - src/app/exports.py, src/app/audit.py (pure)
  - src/app/auth.py (extract_user_email, is_member, group_display_names, DOWNLOAD_GROUP)
  - src/app/main.py (can_download on /, POST /download, _app_sp_client)
  - src/app/templates/glance.html (conditional download panel), src/app/static/js/app.js (hidden-field sync)
  - src/app/requirements.txt (+openpyxl, +python-multipart)
  - resources/grants.sql (SP appId grants + app-users SELECT)
  - tests/test_exports.py, tests/test_audit.py, tests/test_auth.py (extended)
uc_targets:
  - irs.efile.daily_efile_glance (READ, OBO)
  - irs.efile.download_audit (WRITE as app SP; one row per download)
groups:
  - efile_glance_app_users (created; SELECT on gold)
  - efile_glance_download_users (created; gates download; greg.skinner added)
```
