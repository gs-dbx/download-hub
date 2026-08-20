---
phase: 8-generic-download-docs
plan: 8
type: execute
status: planned
workspace_url: https://field-eng-aws-gov-demo-irs-demo.cloud.databricks.us
default_catalog: irs
skill_references:
  - ~/.ai-dev-kit/repo/.claude/skills/python-dev/SKILL.md
  - ~/.ai-dev-kit/repo/.claude/skills/databricks-python-sdk/SKILL.md
wave_count: 8
---

# Phase 8: Download generalization + docs (final Milestone-2 phase)

## Goal
Retire the efile-only download and make download work on ANY configured report: export the current
filtered view from the per-user cache, gate per-report (`report_config.download_group` else the code
default), audit with `report_id`/`report_title`, migrate `download_audit`, and document config
authoring. After this, every tab is fully functional and the app is entirely config-driven.

## Workspace Context (live)
- `download_audit` has 11 columns (no report_id/title yet — added this phase). `report_config` has 2
  enabled reports (efile_glance, efile_pins), both `download_group = NULL` (→ code default). Both point
  at `irs.efile.daily_efile_glance`. App SP has MODIFY+SELECT on download_audit + SELECT on report_config.
- No new bundle resource, no new runtime dependency (openpyxl already vendored in Phase 5).

## Prerequisites
- [ ] Phase 7 complete (portal + cache + reports.py/render.py/cache.py). Branch dbx/download-hub-phase-1. CLI DEFAULT valid.

## Skills to Read Before Executing
- `~/.ai-dev-kit/repo/.claude/skills/databricks-python-sdk/SKILL.md` — asyncio.to_thread, StatementParameterListItem, OBO vs SP (no new SDK surface).
- `~/.ai-dev-kit/repo/.claude/skills/python-dev/SKILL.md` — pure funcs, type hints, pytest.
- In-repo: `src/app/main.py` (report_table cache→filter→search pattern to reuse; current efile /download to replace), `render.py` (cell_text + dual-import shim), `reports.py` (ColumnSpec), `cache.py` (apply_filters/apply_search), `auth.py` (DOWNLOAD_GROUP/is_member), `exports.py`, `audit.py`, templates `report.html`/`_download_efile.html`, `static/js/app.js`, `src/notebooks/generate_efile_glance.py`.
- `.planning/phases/8-RESEARCH.md` — full sketches + the grep-verified retire list (authoritative).

---

## LOCKED DECISIONS (executor MUST follow verbatim)

### L1 — One generic download for every report, exported from the cache
Replace the efile-only `POST /download`. Flow (audit-first preserved): read `await request.form()`
(dynamic per-filter fields); kill-switch 403; resolve report (404); effective-group `me()` membership
403; ack truthy + justification non-empty 400; validate `date` against the report's OBO date list
(400); `_ensure_snapshot` (OBO); build `selected_filters` per `report.filters` (missing → first
distinct, SAME defaulting as report_table); `apply_filters` + `apply_search(haystack_for(columns))` →
ALL matching rows (no pagination); export display columns; write audit as SP (500 on fail → no file);
`Response` attachment. Remove `DOWNLOADABLE_REPORT_IDS` + the `downloadable` context key.

### L2 — Per-report download group with code default
Pure `effective_download_group(report) -> str` in `auth.py` = `report.download_group` (stripped) or
the code default `DOWNLOAD_GROUP`. Used by BOTH `_resolve_can_download` (button) and `POST /download`
(enforcement). TYPE_CHECKING import of ReportConfig to stay cycle-free. Unit-tested.

### L3 — download_audit migration (idempotent, in the seed notebook)
Extend the audit `CREATE TABLE IF NOT EXISTS` DDL to 13 columns (add `report_id STRING`,
`report_title STRING` at the end) for fresh installs, AND add a guarded ALTER cell (Python
column-presence check → `ALTER TABLE {audit_fqn} ADD COLUMNS (...)` only for missing cols). Existing
rows → NULL. Re-running efile_seed is a no-op after first apply.

### L4 — exports.py generalized
`to_csv_bytes(columns: list[ColumnSpec], rows, disclaimer)` + `to_xlsx_bytes(columns, rows, disclaimer)`
+ `filename_for(report_id, date, fmt)`. Header = column labels; disclaimer atop. CSV cells via
`render.cell_text(row.get(c.name), c.format)` (all strings). XLSX: `int`→numeric (`_to_int`),
`pct`/`text`→`cell_text` string (preserves Phase-5 behavior). Keep the single `DISCLAIMER`.

### L5 — audit.py + filters summary
`build_audit_row`/`build_audit_insert` gain `report_id` + `report_title` (INSERT 13 cols / 12 bound
params; event_ts stays `current_timestamp()`; all param values strings). `drain_filter` keeps its NAME
but its VALUE is the applied-filters summary. Pure `filters_summary(selected: dict[str,str]) -> str` in
`cache.py` → `"; ".join(f"{k}={v}" for k,v in sorted(...) if v)` (""=none). Unit-tested.

### L6 — Retire the efile path (grep-verified)
Delete `src/app/queries.py` + `tests/test_queries.py` (whole module dead once the efile route is
gone); delete `shaping.rows_to_context` / `METRIC_ORDER` / `_REQUIRED_COLUMNS` (prune test_shaping);
delete `templates/_download_efile.html` + `DOWNLOADABLE_REPORT_IDS`. KEEP shaping `format_count`/
`format_pct`/`_to_int`/`_to_float`/`EM_DASH`/`format_report_date`. Do NOT break `POST /download` — only
remove after the generic path is in.

### L7 — Templates/JS generic; air-gap
New `_download.html` (generic hidden report_id/date/per-filter/search + ack/justification/format);
`report.html` guards become `{% if can_download %}` and include `_download.html`. `app.js`
`syncDownloadFields` loops filters (set date/search/each filter hidden field), guarded/no-op when
absent. Form field name `date` consistent across hidden input / app.js / route. No CDN/external URLs;
no new runtime dependency.

---

## Wave 1: generalize exports.py

<task type="auto">
  <name>Make exports.py ColumnSpec-driven (generic CSV/XLSX + filename_for)</name>
  <wave>1</wave>
  <files>src/app/exports.py</files>
  <read_first>
    - src/app/exports.py (current efile-specific version), src/app/render.py (cell_text + dual-import shim), src/app/reports.py (ColumnSpec), src/app/shaping.py (_to_int)
    - .planning/phases/8-RESEARCH.md (exports.py Generalization)
  </read_first>
  <action>
    Rewrite exports.py signatures per L4/RESEARCH: `to_csv_bytes(columns: list[ColumnSpec], rows:
    list[dict], disclaimer: str) -> bytes` (header = [c.label], cells render.cell_text(row.get(c.name),
    c.format), disclaimer as leading "# " rows then blank then header — keep the Phase-5 layout);
    `to_xlsx_bytes(columns, rows, disclaimer)` (disclaimer merged top rows, bold header from labels,
    then per-column cells: int→_to_int numeric, pct/text→cell_text string; lazy `import openpyxl`);
    `filename_for(report_id, date, fmt)` → f"{report_id}_{date.split(' ')[0]}.{ 'xlsx' if fmt=='xlsx'
    else 'csv' }". Keep DISCLAIMER unchanged. Use the same dual-import shim render.py uses (flat +
    app.* fallback). Drop the efile _HEADER + metric_name/value_cy key access. No SDK/fastapi import.
  </action>
  <verify>cd /Users/greg.skinner/Documents/IRS/download_hub && .venv/bin/python -m py_compile src/app/exports.py && PYTHONPATH=src .venv/bin/python -c "
from app.exports import to_csv_bytes, filename_for, DISCLAIMER
from app.reports import ColumnSpec
cols=[ColumnSpec('metric_name','Metric','text'),ColumnSpec('value_cy','2026','int'),ColumnSpec('pct_change','% Change','pct')]
rows=[{'metric_name':'X','value_cy':9827762,'pct_change':None}]
b=to_csv_bytes(cols,rows,DISCLAIMER); print(b'Metric,2026,% Change' in b or b'Metric' in b, b'9,827,762' in b, b'\xe2\x80\x94' in b, filename_for('efile_glance','2026-01-12 00:00:00','csv'))"</verify>
  <acceptance_criteria>
    - No SDK/fastapi import; openpyxl lazy inside to_xlsx_bytes.
    - to_csv_bytes header = column labels, disclaimer atop, cells formatted via cell_text (int thousands, NULL pct → em dash); to_xlsx_bytes int→numeric, pct/text→string.
    - filename_for('efile_glance','2026-01-12 00:00:00','csv') == 'efile_glance_2026-01-12.csv'; 'xlsx' → .xlsx.
    - DISCLAIMER unchanged.
  </acceptance_criteria>
</task>

---

## Wave 2: audit.py (report identity) + filters_summary + effective_download_group

<task type="auto">
  <name>Extend audit.py with report_id/title, add filters_summary (cache.py) + effective_download_group (auth.py)</name>
  <wave>2</wave>
  <files>src/app/audit.py, src/app/cache.py, src/app/auth.py</files>
  <read_first>
    - src/app/audit.py (build_audit_row/insert), src/app/cache.py (filter helpers), src/app/auth.py (DOWNLOAD_GROUP)
    - .planning/phases/8-RESEARCH.md (audit.py Changes, filters summary, effective_download_group)
  </read_first>
  <action>
    - audit.py: add params `report_id`, `report_title` to build_audit_row (+ dict keys); build_audit_insert
      INSERT now 13 columns (…, report_id, report_title) / 12 bound params (event_ts = current_timestamp()),
      new params typed STRING; `drain_filter` value stays as-passed (now the filters summary). All values strings.
    - cache.py: add pure `filters_summary(selected: dict[str, str]) -> str` = "; ".join(f"{k}={v}" for k,v
      in sorted(selected.items()) if v) (""=no active filters).
    - auth.py: add pure `effective_download_group(report) -> str` = (report.download_group or "").strip()
      or DOWNLOAD_GROUP; use `from __future__ import annotations` + TYPE_CHECKING import of ReportConfig
      (no runtime import of reports → no cycle).
  </action>
  <verify>cd /Users/greg.skinner/Documents/IRS/download_hub && .venv/bin/python -m py_compile src/app/audit.py src/app/cache.py src/app/auth.py && PYTHONPATH=src .venv/bin/python -c "
from app.audit import build_audit_row, build_audit_insert
from app.cache import filters_summary
from app.auth import effective_download_group, DOWNLOAD_GROUP
from types import SimpleNamespace as N
r=build_audit_row(user_email='a@b.c',report_date='2026-01-12 00:00:00',drain_filter='drain=ALL',search_filter='',row_count=17,export_format='csv',justification='j',app_version='0.8.0',report_id='efile_glance',report_title='Daily E-File at a Glance')
sql,p=build_audit_insert('irs','efile',r); print(':report_id' in sql, ':report_title' in sql, len(p))
print(filters_summary({'drain':'ALL','z':''}))
print(effective_download_group(N(download_group=None))==DOWNLOAD_GROUP, effective_download_group(N(download_group='grp_x'))=='grp_x')"</verify>
  <acceptance_criteria>
    - build_audit_row has report_id/report_title; build_audit_insert INSERT lists 13 columns incl. report_id/report_title with :named params (12 bound), event_ts=current_timestamp(); all values strings.
    - filters_summary sorts, joins "k=v; ...", drops empty values, "" when none.
    - effective_download_group returns the group when set (stripped), else DOWNLOAD_GROUP for None/""/whitespace; auth.py has no runtime import of reports.
  </acceptance_criteria>
</task>

---

## Wave 3: tests (exports/audit/auth updated; prune shaping; drop queries tests)

<task type="auto">
  <name>Rewrite test_exports; extend test_audit + test_auth; prune test_shaping; delete test_queries</name>
  <wave>3</wave>
  <files>tests/test_exports.py, tests/test_audit.py, tests/test_auth.py, tests/test_shaping.py, tests/test_queries.py</files>
  <read_first>
    - src/app/exports.py, src/app/audit.py, src/app/cache.py, src/app/auth.py (updated modules)
    - .planning/phases/8-RESEARCH.md (Test impact notes)
  </read_first>
  <action>
    - test_exports.py: rewrite to the generic signatures — columns=[ColumnSpec(...)] + row dicts keyed by
      name; assert label header line, a known int cell "9,827,762", NULL pct → "—", disclaimer-before-header,
      filename_for now '{report_id}_{date}.{ext}', xlsx still pytest.importorskip("openpyxl") → b"PK".
    - test_audit.py: add report_id/report_title to the kwargs + "all fields" set; assert INSERT contains
      :report_id/:report_title and 12 bound params; add a filters_summary test (import from cache).
    - test_auth.py: add effective_download_group cases (set → returns; None/""/"  " → DOWNLOAD_GROUP) using a stub with .download_group.
    - test_shaping.py: DELETE the rows_to_context / METRIC_ORDER tests; KEEP format_count/format_pct/_to_int/_to_float/format_report_date tests.
    - DELETE tests/test_queries.py (the module is removed in Wave 4; drop its tests now so the suite stays green).
  </action>
  <verify>cd /Users/greg.skinner/Documents/IRS/download_hub && PYTHONPATH=src .venv/bin/python -m pytest tests/ -q</verify>
  <acceptance_criteria>
    - All tests pass. test_exports uses the generic ColumnSpec signatures; test_audit asserts report_id/title + 12 params; test_auth covers effective_download_group; filters_summary tested.
    - test_shaping keeps only the formatter tests (no rows_to_context/METRIC_ORDER); test_queries.py deleted.
  </acceptance_criteria>
</task>

---

## Wave 4: main.py — generic POST /download + retire queries.py

<task type="auto">
  <name>Replace efile /download with generic; effective-group gating; delete queries.py</name>
  <wave>4</wave>
  <files>src/app/main.py, src/app/queries.py, src/app/shaping.py</files>
  <read_first>
    - src/app/main.py (report_table cache pattern + current /download to replace + _resolve_can_download)
    - src/app/exports.py, audit.py, cache.py, auth.py, reports.py, render.py (the pieces to wire)
    - .planning/phases/8-RESEARCH.md (main.py Generic POST /download Flow + Retire list)
  </read_first>
  <action>
    Rewrite POST /download per L1/RESEARCH: `async def download(request)`; read `await request.form()`;
    kill-switch 403; _load_reports → find report (404); group=effective_download_group(report); me() OBO
    (asyncio.to_thread) + is_member 403 (degrade-safe deny); ack+justification 400; validate date vs the
    report's OBO date list (build_report_dates_query in reports.py, 400 if out-of-set); _ensure_snapshot
    (OBO); selected_filters per report.filters (missing → distinct_values(snap.rows,f.field)[0]);
    apply_filters + apply_search(haystack_for(report.columns)); export report.columns via to_csv_bytes/
    to_xlsx_bytes; summary=filters_summary(selected_filters); build_audit_row(...report_id/title,
    drain_filter=summary,search_filter=search,row_count=len,...)+build_audit_insert → _run_sql_sp (500 → no
    file); app-log print; Response attachment (filename_for). Update _resolve_can_download to use
    effective_download_group (drop the DOWNLOADABLE_REPORT_IDS short-circuit); remove DOWNLOADABLE_REPORT_IDS
    + `downloadable` context key. Remove imports from queries + shaping.rows_to_context.
    THEN delete src/app/queries.py (nothing imports it now) and remove shaping.rows_to_context/METRIC_ORDER/
    _REQUIRED_COLUMNS from shaping.py (keep the formatters). Do NOT touch /report/{id}, /report/{id}/table, /health.
  </action>
  <verify>.venv/bin/python -m py_compile src/app/main.py src/app/shaping.py && ! test -f src/app/queries.py && ! grep -q "DOWNLOADABLE_REPORT_IDS" src/app/main.py && ! grep -rn "import queries\|from queries\|rows_to_context" src/app --include=*.py && PYTHONPATH=src .venv/bin/python -m pytest tests/ -q 2>&1 | tail -1</verify>
  <acceptance_criteria>
    - main.py compiles; POST /download reads request.form(), gates kill-switch→report(404)→effective-group me() (403)→ack/justification(400)→date(400), exports the filtered cached rows for report.columns, audit-first SP write (500 on fail), Response attachment.
    - DOWNLOADABLE_REPORT_IDS + downloadable context removed; _resolve_can_download uses effective_download_group.
    - queries.py deleted; no import of queries or shaping.rows_to_context anywhere; shaping keeps its formatters; pytest green.
  </acceptance_criteria>
</task>

---

## Wave 5: templates + JS (generic download)

<task type="auto">
  <name>Add _download.html, repoint report.html, generalize app.js, delete _download_efile.html</name>
  <wave>5</wave>
  <files>src/app/templates/_download.html, src/app/templates/report.html, src/app/static/js/app.js, src/app/templates/_download_efile.html</files>
  <read_first>
    - src/app/templates/_download_efile.html (modal shell to generalize), report.html (guards + include), static/js/app.js (syncDownloadFields)
    - .planning/phases/8-RESEARCH.md (Templates / JS)
  </read_first>
  <action>
    - Create _download.html from _download_efile.html: same modal (disclaimer, ack checkbox, justification,
      CSV/XLSX radios, submit) but hidden fields generic: report_id (report.report_id), date (selected_date),
      search, and one hidden input per report.filters ({{ f.field }} = selected_filters[f.field], with
      data-field). Form posts to /download.
    - report.html: change both download guards to `{% if can_download %}` and include `_download.html`
      (remove the `report.report_id in downloadable` condition).
    - app.js: generalize syncDownloadFields — set #download-date ← date select, #download-search ← search,
      and loop each live filter select setting its hidden input (by data-field / id download-filter-<field>);
      guarded/no-op when the panel is absent; keep calling it after refreshFragment + on load.
    - Delete templates/_download_efile.html (git rm).
  </action>
  <verify>test -f src/app/templates/_download.html && ! test -f src/app/templates/_download_efile.html && grep -q "_download.html" src/app/templates/report.html && ! grep -q "downloadable" src/app/templates/report.html && grep -q "download-date\|download-report-id" src/app/templates/_download.html && ! grep -rniE "unpkg|jsdelivr|https?://[^\"' )]+" src/app/templates src/app/static/js && echo ok</verify>
  <acceptance_criteria>
    - _download.html has generic hidden report_id/date/search + one hidden input per filter; report.html includes it under `{% if can_download %}` (no downloadable ref); _download_efile.html deleted.
    - app.js syncDownloadFields loops filters (date/search/each filter), guarded; no CDN/external URLs.
  </acceptance_criteria>
</task>

---

## Wave 6: download_audit migration in the seed notebook

<task type="auto">
  <name>Extend audit CREATE DDL to 13 cols + add guarded ALTER migration cell</name>
  <wave>6</wave>
  <files>src/notebooks/generate_efile_glance.py</files>
  <read_first>
    - src/notebooks/generate_efile_glance.py (audit CREATE cell)
    - .planning/phases/8-RESEARCH.md (Audit Migration exact SQL + placement)
  </read_first>
  <action>
    - Extend the audit CREATE TABLE IF NOT EXISTS DDL to add `report_id STRING, report_title STRING` at the
      end (13 cols total) for fresh installs.
    - Add a NEW `# COMMAND ----------` cell immediately after the audit CREATE cell: Python column-presence
      guard — `existing = {f.name for f in spark.table(audit_fqn).schema.fields}`; `to_add = [(c,"STRING")
      for c in ("report_id","report_title") if c not in existing]`; if to_add → `spark.sql(f"ALTER TABLE
      {audit_fqn} ADD COLUMNS (...)")` + print; else print already-present. Idempotent; existing rows → NULL.
      Do NOT touch the gold write or report_config sections.
  </action>
  <verify>cd /Users/greg.skinner/Documents/IRS/download_hub && .venv/bin/python -m py_compile src/notebooks/generate_efile_glance.py && grep -q "report_title STRING" src/notebooks/generate_efile_glance.py && grep -q "ADD COLUMNS" src/notebooks/generate_efile_glance.py && echo ok</verify>
  <acceptance_criteria>
    - Notebook compiles; audit CREATE DDL includes report_id + report_title; a guarded ALTER cell adds only missing cols (idempotent). Gold + report_config sections untouched.
  </acceptance_criteria>
</task>

---

## Wave 7: docs

<task type="auto">
  <name>Add docs/REPORTS.md; update README + docs/PERMISSIONS.md</name>
  <wave>7</wave>
  <files>docs/REPORTS.md, README.md, docs/PERMISSIONS.md</files>
  <read_first>
    - .planning/phases/8-RESEARCH.md (Docs Outline)
    - README.md, docs/PERMISSIONS.md (current), resources/grants.sql
  </read_first>
  <action>
    - docs/REPORTS.md (new): how to author report_config rows — row shape, columns_json/filters_json format
      (name/label/format int|pct|text; filter {field,label}; filter field must be projectable),
      date_field/order_by/display_order/enabled, download_group (per-report else code default
      auth.DOWNLOAD_GROUP), idempotent MERGE-on-report_id add/update (never overwrite), TTL ~300s note,
      injection note (values bound, identifiers allowlist-validated).
    - README.md: reframe as a config-driven multi-report portal; generic group-gated download exporting the
      on-screen view from the per-user cache; audit carries report_id/report_title; fix repo-layout lines
      (report.html/_download.html; reports.py/render.py/cache.py; queries.py removed).
    - docs/PERMISSIONS.md: download gating = downloads_enabled AND is_member(me(), effective_download_group(report));
      per-report group with code default; audit adds report_id/report_title and drain_filter = applied-filters
      summary. No secrets.
  </action>
  <verify>cd /Users/greg.skinner/Documents/IRS/download_hub && test -f docs/REPORTS.md && grep -qi "download_group\|columns_json" docs/REPORTS.md && grep -qi "effective_download_group\|report_id" docs/PERMISSIONS.md && echo ok</verify>
  <acceptance_criteria>
    - docs/REPORTS.md explains authoring a report_config row (columns_json/filters_json/date_field/download_group + MERGE + injection note).
    - README reframed to the multi-report portal (queries.py removed, new modules listed); PERMISSIONS updated for effective group + report_id/title audit. No secrets.
  </acceptance_criteria>
</task>

---

## Checkpoint: deploy, migrate, verify generic download on BOTH reports

<task type="checkpoint:human">
  <name>Deploy + run seed (migration) + verify download on both reports writes audit with report_id/title</name>
  <wave>8</wave>
  <action>
    From repo root (branch dbx/download-hub-phase-1):
    1. PYTHONPATH=src .venv/bin/python -m pytest tests/ -q
    2. databricks bundle validate -t dev -p DEFAULT && databricks bundle deploy --target dev -p DEFAULT
    3. databricks bundle run efile_seed -t dev -p DEFAULT   (applies the download_audit ALTER migration)
    4. Confirm migration: databricks api post /api/2.0/sql/statements -p DEFAULT --json
       '{"warehouse_id":"2f225c0740dcd22b","statement":"DESCRIBE TABLE irs.efile.download_audit","wait_timeout":"30s"}'
       → 13 columns incl. report_id, report_title.
    5. databricks bundle run download_hub -t dev -p DEFAULT   (slow venv rebuild) → apps get download-hub → ACTIVE/SUCCEEDED.
    6. Browser (as greg.skinner, in the download group): BOTH tabs now show a Download button. Download from
       E-File glance (CSV) and from E-File PIN Volumes (Excel), each with ack + justification.
    7. Verify audit: SELECT report_id, report_title, drain_filter, search_filter, row_count, export_format,
       acknowledged FROM irs.efile.download_audit ORDER BY event_ts DESC → one row per download with the
       correct report_id/report_title (efile_glance / "Daily E-File at a Glance" and efile_pins / "E-File PIN
       Volumes"), drain_filter = applied-filters summary (e.g. drain=ALL), acknowledged=true.
    8. Negative: a non-member sees no Download button on either tab; POST /download → 403.
  </action>
  <acceptance_criteria>
    - pytest passes; deploy+seed+app-run succeed; download_audit has 13 columns (report_id/report_title).
    - Both reports expose a working Download (CSV+Excel) with the disclaimer atop; each writes exactly one audit row with the correct report_id/report_title + filters summary + acknowledged=true (audit-first).
    - Non-member: no button + /download 403. Kill switch still disables downloads on all tabs.
  </acceptance_criteria>
</task>

---

## Must-Haves
```yaml
truths:
  - One generic POST /download for every report; exports the current filtered view from the per-user cache (all matching rows, no pagination); efile-only path fully retired (queries.py deleted).
  - Download gated by effective_download_group(report) = report.download_group or code default auth.DOWNLOAD_GROUP; same helper for button visibility + server enforcement; audit-first (500 on audit fail, no file).
  - exports.py is ColumnSpec-driven (labels + int/pct/text; xlsx int→numeric); single global DISCLAIMER atop every file; filename_for(report_id, date, fmt).
  - download_audit gains report_id + report_title (idempotent ALTER in seed notebook + DDL); drain_filter repurposed to the applied-filters summary; audit writes as the app SP.
  - Templates: generic _download.html on every report (can_download); app.js syncs report_id/date/search/per-filter hidden fields. No CDN, no new runtime dependency, no new bundle resource.
  - docs/REPORTS.md authoring guide added; README/PERMISSIONS updated.
artifacts:
  - src/app/exports.py, audit.py, cache.py (filters_summary), auth.py (effective_download_group), main.py (generic /download)
  - src/app/templates/_download.html, report.html ; src/app/static/js/app.js
  - src/notebooks/generate_efile_glance.py (audit migration)
  - docs/REPORTS.md, README.md, docs/PERMISSIONS.md
  - tests/test_exports.py, test_audit.py, test_auth.py, test_shaping.py (pruned)
  - DELETED: src/app/queries.py, tests/test_queries.py, src/app/templates/_download_efile.html
uc_targets:
  - irs.efile.download_audit (WRITE app SP; +report_id/report_title) ; report sources (READ OBO via cache) ; report_config (READ SP)
```
