# Phase 8 Research — Download generalization + docs

**Date:** 2026-08-13
**MCP Available:** no (browser OAuth; unusable)
**CLI Available:** yes (profile DEFAULT) — live reads via Statement Execution API on warehouse `2f225c0740dcd22b`

## Scope recap

Retire the efile-only `POST /download` path and make download work on ANY configured
report: export from the per-user cached snapshot (Phase 7), gate per-report (config
`download_group` else code default), audit with report identity + applied-filters summary,
one idempotent `download_audit` migration in the seed notebook, then docs. App-side only;
no new bundle resource, no new runtime dep (openpyxl already present from Phase 5).

---

## Live Findings (confirmed, no writes)

**`DESCRIBE TABLE irs.efile.download_audit`** — exactly **11 columns**, NO `report_id`/`report_title`:

```
audit_id STRING, event_ts TIMESTAMP, user_email STRING, report_date TIMESTAMP,
drain_filter STRING, search_filter STRING, row_count BIGINT, export_format STRING,
justification STRING, acknowledged BOOLEAN, app_version STRING
```

**`report_config`** — 2 enabled rows, both `download_group = NULL` (so both resolve to the
code default this phase):

| report_id | title | source_fqn | date_field | order_by | display_order | enabled | download_group |
|---|---|---|---|---|---|---|---|
| efile_glance | Daily E-File at a Glance | irs.efile.daily_efile_glance | report_date | sort_order | 1 | true | NULL |
| efile_pins | E-File PIN Volumes | irs.efile.daily_efile_glance | report_date | sort_order | 2 | true | NULL |

Both reports point at the SAME source table (`daily_efile_glance`) — they differ only in
which columns/filters they project. Good news: the generic download path already reads that
table OBO via the Phase-7 cache, so no source-access surprises.

---

## Current Download Path (what changes)

`POST /download` in `main.py` (lines 707–876) is entirely efile-specific and must be replaced:

- Fixed form fields `report_date`, `drain`, `search` → generic `report_id`, `date`,
  per-filter fields, `search`.
- Re-queries the gold table with `queries.build_glance_query_for_date` + `validate_drain` +
  `validate_report_date` + `queries.build_report_dates_query` → replace with cache-based export.
- `rows_to_context(...)` shaping + hand-written metric-name search filter → replace with
  `cache.apply_filters` + `cache.apply_search(haystack_for(report.columns))` over the snapshot.
- `to_csv_bytes(rows, ...)` / `to_xlsx_bytes(rows, ...)` (efile 4-column) → generalized signatures.
- `filename_for(report_date, drain, fmt)` → `filename_for(report_id, date, fmt)`.
- `build_audit_row(... drain_filter=drain ...)` → adds `report_id`/`report_title`, `drain_filter`
  becomes the applied-filters summary.
- `_resolve_can_download` gate on `DOWNLOADABLE_REPORT_IDS` → gate on `effective_download_group`.

Audit-first ordering, kill-switch-first, and the `me()` membership re-check all stay.

---

## Audit Migration (exact SQL + placement)

Databricks Delta supports `ALTER TABLE ... ADD COLUMNS (...)`. It also supports
`ADD COLUMN[S] IF NOT EXISTS`, but the **safest fully-idempotent form in a notebook** is a
Python column-presence guard (works on every DBR, and avoids partial-add ambiguity when only
one of two columns is missing). Existing rows get NULL automatically.

**Placement:** a NEW `# COMMAND ----------` cell in `src/notebooks/generate_efile_glance.py`
immediately AFTER the audit `CREATE TABLE IF NOT EXISTS` cell (after line 120, before the gold
build at line 122). Also update the CREATE DDL itself for fresh installs.

1) Extend the CREATE DDL (lines 106–118) — add the two columns at the end so fresh installs
   land with 13 columns:

```sql
CREATE TABLE IF NOT EXISTS {audit_fqn} (
  audit_id STRING, event_ts TIMESTAMP, user_email STRING, report_date TIMESTAMP,
  drain_filter STRING, search_filter STRING, row_count BIGINT, export_format STRING,
  justification STRING, acknowledged BOOLEAN, app_version STRING,
  report_id STRING, report_title STRING
) USING DELTA
```

2) New guarded migration cell (idempotent for pre-existing 11-col tables):

```python
existing = {f.name for f in spark.table(audit_fqn).schema.fields}
to_add = [(c, "STRING") for c in ("report_id", "report_title") if c not in existing]
if to_add:
    cols = ", ".join(f"{name} {typ}" for name, typ in to_add)
    spark.sql(f"ALTER TABLE {audit_fqn} ADD COLUMNS ({cols})")
    print(f"added audit columns: {[c for c, _ in to_add]}")
else:
    print("audit columns report_id/report_title already present")
```

Deploy step: run `efile_seed` after app redeploy to apply the migration to the live table.

---

## exports.py Generalization

New pure signatures (drop the `_HEADER` constant + all `metric_name`/`value_cy` key access):

```python
def to_csv_bytes(columns: list[ColumnSpec], rows: list[dict], disclaimer: str) -> bytes
def to_xlsx_bytes(columns: list[ColumnSpec], rows: list[dict], disclaimer: str) -> bytes
def filename_for(report_id: str, date: str, fmt: str) -> str
```

- Import `ColumnSpec` + `cell_text`/`_to_int` from `render`/`shaping` using the same dual-import
  shim `render.py` already uses (flat `from reports import ...` / fallback `from app.reports ...`).
- Header row = `[c.label for c in columns]`.
- Keep `DISCLAIMER` unchanged (single source of truth; still the modal ack text).
- `filename_for`: `f"{report_id}_{date_part}.{ext}"` where `date_part` strips the time
  (`date.split(" ")[0]`) and `ext` is `xlsx` for `"xlsx"` else `csv`. Example:
  `efile_glance_2026-01-12.csv`.

**CSV cells:** `render.cell_text(row.get(c.name), c.format)` for EVERY column (all strings).
This preserves the current CSV exactly: an `int` column renders `"9,827,762"` (was
`value_cy_fmt`), a `pct` column renders `"+21.3%"`/`"—"` (was `pct_fmt`).

**XLSX numeric decision (RECOMMENDED — preserve Phase-5 behavior):** per-column,
- `c.format == "int"` → write a numeric cell: `_to_int(row.get(c.name))` (real Excel number,
  sortable/summable; matches current `value_cy`/`value_py` numeric cells).
- `c.format` in `("pct", "text")` / unknown → write `cell_text(...)` string (so `"—"` and the
  signed `%` match the screen; a raw pct float would drop the sign/format).

Rejected alternative "all cells as `cell_text` strings in xlsx too": simpler but regresses
Phase-5 (counts stop being real numbers in Excel) — not worth it. The `int→numeric,
pct/text→string` split is the minimal generalization of today's behavior.

**Test impact (`tests/test_exports.py` rewrite to generic):** build a small
`columns = [ColumnSpec("metric_name","Metric","text"), ColumnSpec("value_cy","2026","int"),
ColumnSpec("pct_change","% Change","pct")]` + row dicts keyed by `name`; assert the label
header line, a known formatted row, NULL-pct → `—`, disclaimer-before-header, `filename_for`
now `{report_id}_{date}.{ext}`, and xlsx still `pytest.importorskip("openpyxl")` returning
`b"PK"`. The current `filename_for` drain-based parametrize cases all change.

---

## audit.py Changes

`build_audit_row(...)` — add two keyword args and two dict keys:

- new params `report_id: str`, `report_title: str`; add `"report_id"`, `"report_title"` to the
  returned dict.
- `drain_filter` keeps its NAME (no column rename → no disruptive migration) but its VALUE is
  now the **applied-filters summary string** (see below). `search_filter` unchanged.

`build_audit_insert(...)` — INSERT is now **13 columns** (11 + report_id + report_title),
**12 bound params** (`event_ts` still `current_timestamp()`):

```sql
INSERT INTO {catalog}.{schema}.download_audit
  (audit_id, event_ts, user_email, report_date, drain_filter, search_filter,
   row_count, export_format, justification, acknowledged, app_version,
   report_id, report_title)
VALUES (:audit_id, current_timestamp(), :user_email, :report_date, :drain_filter,
   :search_filter, :row_count, :export_format, :justification, :acknowledged,
   :app_version, :report_id, :report_title)
```

Both new params `{"name","value","type":"STRING"}`. All values remain strings.

**Applied-filters summary format (RECOMMENDED):** built in `main.py` from the selected
non-empty filters, sorted by field, joined `"; "`:

```python
summary = "; ".join(f"{k}={v}" for k, v in sorted(selected_filters.items()) if v)
# e.g. "drain=ALL"  (efile);  ""  when no filters are active
```

Empty string when no active filters (never NULL). Recommend a tiny pure helper
`filters_summary(selected: dict[str, str]) -> str` (place in `cache.py` or `render.py` — both
already imported; `cache.py` is the natural home since it owns filter semantics) so it is unit-
testable per the CONTEXT testing note. The efile single-filter default (first distinct) means
this typically records `drain=<first distinct value>`.

**Test impact (`tests/test_audit.py`):** `_KWARGS` gains `report_id`/`report_title`; the
"all logical fields" set gains those two; INSERT assertions expect `:report_id`/`:report_title`
present and **12** bound params (was 10); add a `filters_summary` test if the helper lands here.

---

## effective_download_group Helper

**Location: `auth.py`** (it owns `DOWNLOAD_GROUP` and all gating helpers; `reports.py` stays
free of any auth dependency). It only reads `report.download_group` (attribute access), so it
needs `ReportConfig` for typing only — use a `TYPE_CHECKING` import to keep `auth.py`
import-light and cycle-free (auth does not runtime-import reports).

```python
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from reports import ReportConfig  # (dual-shim if needed for the annotation)

def effective_download_group(report: "ReportConfig") -> str:
    """Per-report download group when set, else the code default DOWNLOAD_GROUP.
    Treats None / whitespace-only as unset."""
    return (getattr(report, "download_group", None) or "").strip() or DOWNLOAD_GROUP
```

Used by BOTH `_resolve_can_download` (button visibility) and `POST /download` (server
enforcement). Unit test in `tests/test_auth.py`: set → returns it; `None`/`""`/`"  "` → global.

---

## main.py — Generic POST /download Flow

The per-filter form fields are DYNAMIC (each report has different filters), which FastAPI's
`Form(...)` cannot declare statically. **Read the raw form** (`form = await request.form()`) and
pull fields by name — mirroring how `report_table` reads `request.query_params` per `f.field`.

Signature: `async def download(request: Request) -> Response:` (drop the `Form(...)` params).

Flow (audit-first preserved):
1. `token = extract_user_token(headers)` (401), `email = extract_user_email(headers)`.
2. Kill switch: `if not downloads_enabled(...)` → 403 (before membership, unchanged).
3. `configs = await _load_reports()`; `report = next(... report_id == form["report_id"] and enabled ...)`
   → 404 if absent/disabled.
4. `group = effective_download_group(report)`; `me()` OBO via `asyncio.to_thread`;
   `is_member(me_user, group)` → 403 if not (degrade-safe: any exception → deny).
5. Validate `acknowledged` truthy (`_ACK_TRUTHY`) AND `justification` non-empty → 400.
6. Validate `date`: fetch the report's OBO date list via
   `build_report_dates_query_generic(report.source_fqn, report.date_field)` →
   `{format_report_date(r[0]) ...}`; RuntimeError → 403; `date not in allowed` → 400.
7. `snap = await _ensure_snapshot(token, email, report, date)` (OBO); RuntimeError → 403.
8. Build `selected_filters` from the form per `f.field`; any missing filter defaults to its
   first distinct value (`distinct_values(snap.rows, f.field)[0]`) — SAME defaulting as
   `report_table`, so the export matches the on-screen default view. Then
   `filtered = apply_filters(snap.rows, selected_filters)`;
   `searched = apply_search(filtered, search, haystack_for(report.columns))`. Export ALL matching
   rows (no pagination).
9. Export display columns: `fmt = "xlsx" if form.get("format")=="xlsx" else "csv"`;
   `to_xlsx_bytes(report.columns, searched, DISCLAIMER)` / `to_csv_bytes(...)` + media type.
10. `summary = filters_summary(selected_filters)`;
    `build_audit_row(user_email=email, report_date=date, drain_filter=summary,
    search_filter=search, row_count=len(searched), export_format=fmt, justification=...,
    app_version=..., report_id=report.report_id, report_title=report.title)`;
    `build_audit_insert(catalog, schema, row)` → `_run_sql_sp(sql, params)` (RuntimeError → 500,
    NO file).
11. `print("[download-hub] download audited: user=... report_id=... rows=... format=... date=...")`.
12. `Response(content=..., media_type=..., Content-Disposition attachment;
    filename="{filename_for(report.report_id, date, fmt)}")`.

Remove `DOWNLOADABLE_REPORT_IDS` (line 94), the `downloadable` context key (line 598), and the
efile imports from `queries`/`shaping` (see Retire list). Update `_resolve_can_download` to use
`effective_download_group(report)` and drop the `report_id not in DOWNLOADABLE_REPORT_IDS`
short-circuit (keep kill switch + `me()` membership + degrade-safe False).

**report.html → download-form context:** `report_page` already passes `report`,
`selected_date`, `selected_filters`, `disclaimer`, `can_download`. The generic
`_download.html` reads `report.report_id`, `selected_date`, and `selected_filters[f.field]`
for each `f in report.filters` — no NEW context needed beyond removing `downloadable`.

---

## Templates / JS

**New `src/app/templates/_download.html`** (replaces `_download_efile.html`) — identical modal
shell + disclaimer + ack checkbox + justification + CSV/XLSX radios, but hidden fields become
generic:

```html
<input type="hidden" name="report_id" id="download-report-id" value="{{ report.report_id }}">
<input type="hidden" name="date" id="download-date" value="{{ selected_date }}">
<input type="hidden" name="search" id="download-search" value="">
{% for f in report.filters %}
<input type="hidden" name="{{ f.field }}" data-field="{{ f.field }}"
       id="download-filter-{{ f.field }}" value="{{ selected_filters[f.field] }}">
{% endfor %}
```

(Note: pick form field name `date` to match the `/table` endpoint's query param; keep it
consistent between the hidden input, app.js, and the route form read.)

**`report.html`:** change BOTH guards from
`{% if can_download and report.report_id in downloadable %}` to `{% if can_download %}` (button
block ~line 62 and the include ~line 109), and change the include to `_download.html`.

**`static/js/app.js`:** generalize `syncDownloadFields` (currently drain-specific, lines 33–52,
127, 166). Replace the `dlDrain`/drain lookup with:
- set `#download-report-id` once (or leave the server value — it's static; safe to skip),
- `#download-date` ← `dateEl.value`, `#download-search` ← `searchEl.value`,
- loop `filterEls`: for each live filter select, find the hidden input by `data-field` (or
  `document.getElementById("download-filter-" + field)`) and copy `.value`.
Keep it guarded/no-op when the panel is absent (non-members). Still call it after each
`refreshFragment` and once on load.

---

## Retire list (grep-verified)

After the generic path lands, these become unused. Grep confirms the ONLY remaining consumers
are `main.py`'s efile download route (being replaced) and the corresponding tests:

| Symbol / file | Remaining consumers today | Action |
|---|---|---|
| `queries.build_glance_query_for_date` | main.py:814, test_queries.py | delete fn |
| `queries.build_glance_query` | test_queries.py only (already dead in app) | delete fn |
| `queries.validate_drain` | main.py:799, test_queries.py | delete fn |
| `queries.validate_report_date` | main.py:800, test_queries.py | delete fn |
| `queries.build_report_dates_query` | main.py:790 (efile route only) | delete fn |
| **`src/app/queries.py` (whole module)** | after the above, nothing imports it | **delete module + `tests/test_queries.py`** |
| `shaping.rows_to_context` | main.py:822, test_shaping.py, exports docstrings | delete fn |
| `shaping.METRIC_ORDER` | test_shaping.py guard only | delete const |
| `shaping._REQUIRED_COLUMNS` | only inside `rows_to_context` | delete (with fn) |
| `main.DOWNLOADABLE_REPORT_IDS` + `downloadable` context | main.py + report.html + _download_efile.html | delete |
| `templates/_download_efile.html` | report.html include (being repointed) | delete |

**KEEP in `shaping.py`:** `format_count`, `format_pct`, `_to_int`, `_to_float`, `EM_DASH`,
`format_report_date` — used by `render.py`, `exports.py`, and `main.py` (`format_report_date`).
Prune `tests/test_shaping.py` to drop the `rows_to_context`/`METRIC_ORDER` tests, keeping the
formatter tests.

Note: retiring the whole `queries.py` module is the clean end state (verified nothing else
imports it once the efile route is gone). CONTEXT only explicitly named
`build_glance_query_for_date`; the planner may keep the harmless remainder, but deleting the
module + `test_queries.py` is low-risk (pure, import-safe) and removes dead code.

---

## Docs Outline

**New `docs/REPORTS.md` — "Authoring reports in `report_config`":**
- What `report_config` is (the registry the app reads as the SP; TTL-cached 300s → new rows
  appear without redeploy).
- Row shape: `report_id`, `title`, `source_fqn` (1–3-part UC name, each part a bare identifier),
  `date_field`, `columns_json`, `filters_json`, `order_by`, `display_order`, `enabled`,
  `download_group`, `updated_at`.
- `columns_json` format: JSON array of `{"name","label","format"}` where `format ∈
  {int, pct, text}` (unknown → text). `filters_json`: array of `{"field","label"}`. Note the
  filter field MUST also be projectable (snapshot selects `display ∪ filter` columns).
- How to add/update a report: idempotent `DeltaTable` MERGE on `report_id` in the seed notebook
  (never overwrite); enabling/ordering via `enabled` + `display_order`.
- **Download applies to every report**, group-gated: set `download_group` for a per-report
  group, else the code default `auth.DOWNLOAD_GROUP` (`efile_glance_download_users`). Mention
  the future BEARS 1:1 mapping hook.
- Injection note: VALUES are always bound params; identifiers are allowlist-validated at
  query-build time.

**`README.md` updates:** reframe from single "at a glance" table to a config-driven
multi-report portal; download is generic (any report, group-gated) exporting the on-screen
view from the per-user cache; audit now carries `report_id`/`report_title`; fix the repo-layout
lines (`templates/` list still says `glance.html`; note `report.html`/`_download.html`,
`reports.py`/`render.py`/`cache.py`; `queries.py` removed).

**`docs/PERMISSIONS.md` updates:** "Download gating" section — `can_download =
downloads_enabled(...) AND is_member(me(), effective_download_group(report))`; per-report group
with code default; audit row now includes `report_id`/`report_title` and `drain_filter` holds
an applied-filters summary. Grants unchanged (SP already has MODIFY+SELECT on `download_audit`;
the ALTER runs in the seed notebook as the notebook runner, not the app SP).

**`docs/OFFLINE.md`:** unchanged (no new deps — openpyxl already vendored in Phase 5).

---

## Files to Add / Modify / Delete

**Add**
- `docs/REPORTS.md`
- `src/app/templates/_download.html`

**Modify**
- `src/app/main.py` — generic `POST /download`; drop `DOWNLOADABLE_REPORT_IDS`/`downloadable`;
  update imports (remove `queries`, remove `rows_to_context`); `_resolve_can_download` →
  `effective_download_group`.
- `src/app/exports.py` — generic `to_csv_bytes`/`to_xlsx_bytes`/`filename_for`.
- `src/app/audit.py` — add `report_id`/`report_title`; `drain_filter` = filters summary.
- `src/app/auth.py` — add `effective_download_group`.
- `src/app/cache.py` (or render.py) — add `filters_summary` helper (recommended).
- `src/app/templates/report.html` — guard `{% if can_download %}`; include `_download.html`.
- `src/app/static/js/app.js` — generalize `syncDownloadFields`.
- `src/notebooks/generate_efile_glance.py` — extend audit CREATE DDL + add guarded ALTER cell.
- `README.md`, `docs/PERMISSIONS.md`.
- `tests/test_exports.py`, `tests/test_audit.py`, `tests/test_auth.py`, `tests/test_shaping.py`
  (prune).

**Delete**
- `src/app/templates/_download_efile.html`
- `src/app/queries.py` + `tests/test_queries.py` (once nothing imports queries)

---

## Recommended References for the Executor

- `~/.ai-dev-kit/repo/.claude/skills/python-dev/SKILL.md`
- `~/.ai-dev-kit/repo/.claude/skills/databricks-python-sdk/SKILL.md` — the established patterns
  are already in `main.py`: sync SDK wrapped in `asyncio.to_thread`, `WorkspaceClient` OBO
  (`auth_type="pat"`) vs default SP client, `StatementParameterListItem` bound params. No new
  SDK surface for this phase.
- In-repo prior art: `src/app/main.py` `report_table` route (the exact
  cache→filter→search pattern the generic download reuses); `src/app/render.py` (dual-import
  shim + `cell_text`); `src/app/reports.py` (`ColumnSpec`, allowlist identifier validation).

---

## Risks / Notes

- **Dynamic form fields:** must read `await request.form()` for per-filter fields — do NOT try to
  declare them as `Form(...)`. Pull `report_id`/`date`/`search`/`acknowledged`/`justification`/
  `format` from the same mapping for consistency.
- **Filter defaulting must match the screen:** `report_table` defaults any absent filter to its
  first distinct snapshot value. The download route MUST apply the identical defaulting so the
  exported rows equal the on-screen view (the CONTEXT "matches exactly what's on screen"
  guarantee). app.js syncs the hidden fields, but keep the server-side default as the safety net.
- **`drain_filter` semantic reuse:** column keeps its name but now stores a generic
  `"field=value; ..."` summary. Documented in PERMISSIONS/REPORTS; both current reports will
  typically log `drain=<first distinct>` since drain is their only filter.
- **XLSX numeric split** depends on `c.format` being accurate in `columns_json` (int vs text).
  Both live reports already tag `value_cy`/`value_py` as `int` and `pct_change` as `pct`.
- **Migration idempotency:** the Python column-presence guard is safe on the live 11-col table
  and on fresh 13-col installs alike; re-running `efile_seed` is a no-op after the first apply.
- **Both reports share `daily_efile_glance`** — verifying the checkpoint on BOTH reports
  exercises the generic path (different column sets) against the same source, so distinct
  `report_id`/`report_title` in the audit rows is the key thing to confirm.
- **`_load_reports` TTL (300s):** if `download_group` is ever set on a report, allow up to 5
  minutes (or restart) for the app to pick it up. Not relevant this phase (both NULL) but note
  it in REPORTS.md.
