# Phase 8 Context

**Phase:** Download generalization + docs (final Milestone-2 phase)
**Discussed:** 2026-08-13
**Status:** ready for planning

Make download work on ANY configured report (one generic path, retiring the efile-only one),
export from the per-user cache, audit with report identity, then refresh the docs.

## Locked Decisions

### Generic download flow
- **Every report** shows the Download button when `downloads_enabled(...) AND is_member(me(),
  effective_group)` — where **`effective_group = report.download_group or auth.DOWNLOAD_GROUP`**
  (per-report group when set in `report_config`, else the global default defined IN CODE). Remove
  `DOWNLOADABLE_REPORT_IDS` and the report-#1 special-case entirely.
- **Export source = the per-user cached snapshot** (Phase 7 cache). On `POST /download`: resolve the
  report config, ensure the `(user, report, date)` snapshot is cached (OBO), apply the CURRENT
  filters + search (`cache.apply_filters` / `cache.apply_search`), export ALL matching rows (ignore
  pagination). Matches exactly what's on screen; OBO already enforced at snapshot load.
- **Columns exported** = the report's DISPLAY columns (labels as headers), formatted via the generic
  formatter (int/pct/text). Filename `daily_efile_glance`-style → generic `{report_id}_{date}.{ext}`.
- CSV (stdlib) + Excel (openpyxl); the single global `DISCLAIMER` rides atop every export and is the
  modal's acknowledgement text.
- **Audit-first preserved:** validate → group re-check (403) + kill-switch (403) → build file →
  write audit as the app SP → return attachment; audit-write failure → HTTP 500, no file.

### Validation / gating
- Require `acknowledged` truthy AND non-empty `justification` (400 otherwise). Re-check the report's
  **effective download group** + kill switch server-side (403). The effective group is
  `report.download_group` when the `report_config` row sets it, else the global default constant in
  code (`auth.DOWNLOAD_GROUP` = `efile_glance_download_users`). Both `/report/{id}` (button visibility)
  and `POST /download` (server enforcement) use the SAME effective-group resolution — a small pure
  helper `effective_download_group(report) -> str`. (Both current reports leave `download_group` NULL,
  so they use the global default; the mechanism is now per-report for future reports/BEARS mappings.)

### download_audit migration (add report identity)
- Add **`report_id STRING`** and **`report_title STRING`** to `irs.efile.download_audit`.
  Migration is idempotent in the seed notebook: `ALTER TABLE ... ADD COLUMNS (report_id STRING,
  report_title STRING)` guarded so re-runs are safe (add-if-absent), AND update the `CREATE TABLE IF
  NOT EXISTS` DDL to include them for fresh installs. Existing rows get NULL.
- **`drain_filter` column is repurposed to a generic applied-filters summary** (e.g. `drain=ALL` for
  efile, or `field=val; field2=val2` generally) — no rename (avoids a disruptive migration);
  documented as "applied filters summary". `search_filter` holds the search text (unchanged).

### Code generalization
- **`exports.py`:** generalize `to_csv_bytes` / `to_xlsx_bytes` to accept the report's display
  columns (ColumnSpec: label + format) + generic row dicts, building headers from labels and cells
  via the generic formatter (reuse `render.cell_text`). Keep `DISCLAIMER`; `filename_for(report_id,
  date, fmt)`. Drop the hardcoded efile 4-column assumption.
- **`audit.py`:** extend `build_audit_row` / `build_audit_insert` with `report_id` + `report_title`
  (INSERT columns + params); `drain_filter` value = the applied-filters summary string.
- **`main.py`:** replace the efile-only `POST /download` with the generic one (form: `report_id`,
  `date`, one field per filter, `search`, `acknowledged`, `justification`, `format`); export from
  cache; SP audit write with report_id/title. Remove `DOWNLOADABLE_REPORT_IDS`; `can_download` no
  longer report-scoped. Retire the now-unused efile paths: `queries.build_glance_query_for_date`,
  `shaping.rows_to_context` / `METRIC_ORDER` / `_REQUIRED_COLUMNS` (delete if nothing else imports
  them; keep `shaping.format_count/format_pct/_to_int/_to_float/EM_DASH` — used by render/exports).
- **Templates:** replace `_download_efile.html` with a generic `_download.html` (ack + justification
  + format + hidden `report_id`/`date`/per-filter/`search`); `report.html` includes it for every
  report when `can_download`.
- **`app.js`:** generalize the download hidden-field sync to set `report_id`, `date`, every filter
  field, and `search` from the live controls (for all reports, not just efile's drain).

### Docs
- Add **`docs/REPORTS.md`** — config-table authoring guide: how to add a report (INSERT/MERGE into
  `irs.efile.report_config`), the `columns_json` / `filters_json` format, `date_field`/`order_by`,
  enabling/ordering tabs, and that download applies to all reports (group-gated).
- Update `README.md` (multi-report portal + generic download), `docs/PERMISSIONS.md` (download now
  any report; audit carries report_id/title), `docs/OFFLINE.md` unchanged unless deps change (they
  don't — openpyxl already present).

### Component / targets / testing / deploy
- App-side + one idempotent audit-table migration (in the seed notebook). No new bundle resource,
  no new runtime dependency (openpyxl already added in Phase 5).
- **UC targets:** READ each report source (OBO, via cache) + `report_config` (SP); WRITE
  `irs.efile.download_audit` (app SP, now incl. report_id/title).
- **Testing (pytest, offline):** generalized exports (CSV/XLSX with arbitrary columns + disclaimer +
  a known row; NULL pct); audit builder with report_id/title + filters-summary; the filters-summary
  formatter. XLSX test still `pytest.importorskip("openpyxl")`. Live download→audit-row verified at
  the checkpoint (both reports).
- **Deploy:** app redeploy + restart; run `efile_seed` to apply the audit migration; checkpoint
  verifies a download on BOTH reports writes an audit row with the correct report_id/title.

## Open Questions (Deferred)
- Per-report disclaimer → future (single global for now).
- In-app admin UI for report_config → future.

## Update (per user, 2026-08-13)
- Download gating is now **per-report with a code default**: `report_config.download_group` is
  honored when set, else falls back to the global `auth.DOWNLOAD_GROUP` in code. Wire it via a pure
  `effective_download_group(report)` helper used by BOTH the button-visibility check and the
  `POST /download` server enforcement. Unit-test the helper (set → returns it; NULL/empty → global).
  No schema change (column already exists on report_config + ReportConfig).

## Workspace Scan Summary
- `report_config` has 2 reports (efile_glance, efile_pins). `download_audit` exists with 11 columns
  (no report_id/title yet — added this phase). App SP has MODIFY+SELECT on download_audit + SELECT on
  report_config. App `download-hub` ACTIVE on Phase 7 code. Warehouse `2f225c0740dcd22b` healthy.
