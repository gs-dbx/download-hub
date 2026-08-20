# Phase 7 Research — Config-driven multi-tab portal

**Date:** 2026-08-13
**Phase:** 7 — generalize the single hardcoded page into a config-driven multi-tab portal
**Domain:** Databricks App (server-rendered FastAPI + Jinja2 + vendored USWDS + vanilla JS). App-side ONLY.
**MCP Available:** databricks-v2 present but NOT usable (browser OAuth) — used CLI (profile DEFAULT) + Statement Execution API
**CLI Available:** yes — live `report_config` read + distinct-drain read SUCCEEDED
**Scope:** NO new UC objects, NO bundle-resource change, NO new runtime dependency. Pure app-code refactor.

---

## Live Findings

Read `irs.efile.report_config` live (warehouse `2f225c0740dcd22b`). **Exactly ONE row today** (Phase 6):

| field | value |
|---|---|
| report_id | `efile_glance` |
| title | `Daily E-File at a Glance` |
| source_fqn | `irs.efile.daily_efile_glance` |
| date_field | `report_date` |
| columns_json | `[{name:metric_name,label:Metric,format:text},{name:value_cy,label:2026,format:int},{name:value_py,label:2025,format:int},{name:pct_change,label:% Change,format:pct}]` |
| filters_json | `[{field:drain,label:DRAIN}]` |
| order_by | `sort_order` |
| display_order | `1` |
| enabled | `true` |
| download_group | `null` (uses the global `efile_glance_download_users`) |

**CRITICAL FINDING — the filter field is NOT among the display columns.** `columns_json` lists 4 display
columns; `drain` (the filter field) and `sort_order` (the order_by) are NOT in that list. So the cached
snapshot query MUST select **`display columns ∪ filter fields`** or in-app drain filtering is impossible
(the row dicts would not carry `drain`). `order_by` may reference a column outside the SELECT (SQL allows
`ORDER BY sort_order` without selecting it), so only the filter fields need to be added to the projection.
→ The load query passes `columns = dedup(display_col_names + filter_field_names)` to
`reports.build_report_query`; the renderer emits ONLY the display columns.

Distinct `drain` values (live): `['ALL', 'E', 'M', 'N']` — alphabetical order puts `ALL` first, so a filter
default of "first distinct value" reproduces today's default (drain=ALL → 17 rows) with no special-casing.

Snapshot size note: a date-scoped read with no drain filter returns **all 4 drains × 17 = 68 rows** for a
report_date. The in-app equality filter (drain=ALL) narrows to 17. This is correct and is exactly how the
generic filter mechanism generalizes Phase 3's server round-trip DRAIN into an in-memory filter.

**pct color CSS already exists but is currently UNUSED in markup.** `app.css` defines `.irs-pct--pos`
(`--irs-pos #1a7f37`) and `.irs-pct--neg` (`--irs-neg #b21d1d`) with `font-weight:600`, but the current
`_rows.html` renders `<td class="text-right">{{ row.pct_fmt }}</td>` with **no** color class. The generic
renderer should finally apply these classes by pct sign (pos/neg/muted em-dash) — this is the intended
Milestone-1 look, now wired.

**2nd demo report (checkpoint aid — do NOT add now):** recommend a second config row over the SAME gold
source, different column subset + title, to visibly prove multi-tab + per-tab cache/filters. Concrete
suggestion, added via MERGE **at the checkpoint** (not by the executor during the build):

```sql
MERGE INTO irs.efile.report_config t
USING (SELECT 'efile_pins' AS report_id) s ON t.report_id = s.report_id
WHEN NOT MATCHED THEN INSERT (report_id, title, source_fqn, date_field, columns_json, filters_json,
  order_by, display_order, enabled, download_group)
VALUES ('efile_pins', 'E-File PIN Volumes', 'irs.efile.daily_efile_glance', 'report_date',
  '[{"name":"metric_name","label":"Metric","format":"text"},{"name":"value_cy","label":"2026","format":"int"}]',
  '[{"field":"drain","label":"DRAIN"}]', 'sort_order', 2, true, NULL);
```

This proves the generic engine with a different column subset and NO download button (not in the
interim-downloadable set), sharing the same source so no new data is needed. Leave or remove after the demo.

---

## Current Code Map (routes / templates / JS to change)

App runs from `src/app/` with FLAT imports (`from queries import ...`); tests import via the `app.*`
package (`src/app/__init__.py` exists, `src/` on sys.path). New modules follow the same dual convention.

### `main.py` — the sole I/O boundary (heavy refactor)
- KEEP verbatim: `_env`, `_user_client(token)` (the `auth_type="pat"` OBO pin), `_app_sp_client()`,
  `_run_sql(token, sql, parameters=None)`, `_run_sql_sp(sql, parameters)`, `/health`.
- REPLACE: `/` (currently renders glance.html directly) → redirect to first enabled report.
- ADD: `GET /report/{report_id}` (full page) and `GET /report/{report_id}/table` (fragment).
- RETIRE (after generic path works): the glance-specific `/` body, imports of `build_glance_query` /
  `build_glance_query_for_date` / `validate_drain` / `validate_report_date`, and `DRAIN_OPTIONS`.
- KEEP for Phase 7 (interim): `POST /download` and its `queries.py`/`shaping.rows_to_context` deps —
  UNCHANGED (efile-only). Phase 8 generalizes.

### `reports.py` — the generic engine (REUSE UNCHANGED, it is the foundation)
- `parse_report_config(row)`, `build_report_config_query(catalog, schema)` (SP registry read),
  `build_report_query(source_fqn, columns, date_field, report_date, filters, order_by)` (parameterized,
  identifier-validated), `build_report_dates_query(source_fqn, date_field)`,
  `build_distinct_values_query(...)`, `ColumnSpec/FilterSpec/ReportConfig`, `validate_identifier/validate_fqn`.

### Templates
- `base.html` — add **tab nav** (a `usa` nav / button-group of links to `/report/{id}`, active highlighted)
  driven by a `nav_reports` context list + `active_report_id`. Also add a per-report data hook for JS.
- `glance.html` → **replace with generic `report.html`** (title/hero from config, generic toolbar, generic
  table, last-updated stamp, refresh button, optional efile download include).
- `_rows.html` → **generalize** to `(columns, rows)` of pre-rendered generic cells (loop columns × cells).
- `error.html` — reuse unchanged.
- The efile download modal (currently inline in glance.html) → move to a partial `_download_efile.html`
  included by `report.html` only when `report_id == "efile_glance" and can_download` (keeps the generic
  renderer clean; Phase 8 replaces with a generic download).

### JS / CSS
- `static/js/app.js` — generalize from hardcoded `glance-*` ids to config-driven controls wired to the
  fragment endpoint (search debounce, date/filter/size selects, refresh, pager). Keep the efile hidden-field
  sync for report #1.
- `app.css` — reuse; `.irs-pct--pos/--neg` now actually emitted by the renderer.

### Reusable pure modules (unchanged): `auth.py`, `config.py`, `audit.py`, `exports.py`, `shaping.py`
(shaping's `format_count`/`format_pct`/`EM_DASH` are reused by the generic formatter; its efile-specific
`rows_to_context`/`METRIC_ORDER`/`_REQUIRED_COLUMNS` stay only for the interim `/download`).

---

## Routing & Refactor Plan

Registry loading helper (SP, in-process TTL cache so a MERGE'd 2nd row is picked up without redeploy):

```python
# main.py
_reports_cache: tuple[float, list[ReportConfig]] | None = None
_REPORTS_TTL = 300.0  # seconds

async def _load_reports() -> list[ReportConfig]:
    """Return enabled ReportConfigs (SP registry read), TTL-cached in-process."""
    global _reports_cache
    now = time.monotonic()
    if _reports_cache and now - _reports_cache[0] < _REPORTS_TTL:
        return _reports_cache[1]
    sql = build_report_config_query(_env("EFILE_CATALOG"), _env("EFILE_SCHEMA"))
    cols, data = await _run_sql_sp_query(sql)          # SP read (see note)
    configs = [parse_report_config(dict(zip(cols, row))) for row in data]
    configs.sort(key=lambda c: c.display_order)
    _reports_cache = (now, configs)
    return configs
```

Note: `_run_sql_sp` today returns `None` (audit INSERT only). Add a small SP **read** variant that returns
`(columns, rows)` — same body as `_run_sql` but using `_app_sp_client()` instead of `_user_client(token)`.
Suggest refactoring both `_run_sql`/`_run_sql_sp` to share a private `_exec(client, sql, params) -> (cols, rows)`.

Routes:

- **`GET /` → redirect.** `configs = await _load_reports()`; `RedirectResponse(f"/report/{configs[0].report_id}", status_code=307)` (302/307 both fine; 307 preserves method — GET here so either). If no enabled reports → error.html.

- **`GET /report/{report_id}` (full page):**
  1. `configs = await _load_reports()`; find `report_id` in configs; if absent/disabled → error.html 404.
  2. `token = extract_user_token(request.headers)` (401 error.html if absent); `email = extract_user_email(...)`.
  3. Resolve date: fetch date list `build_report_dates_query(report.source_fqn, report.date_field)` (OBO). If the OBO read raises (no UC access) → render report.html with an **empty table + "no data / you may not have access" notice** (per CONTEXT access model), still showing all tabs. `selected_date = query "date" param if in the list else list[0]`.
  4. Ensure snapshot cached (see Per-User Cache): key `(email, report_id, selected_date)`; on miss run `build_report_query(source_fqn, dedup(display+filter cols), date_field, selected_date, filters=None, order_by)` OBO, store `Snapshot(columns, rows, fetched_at=time.time())`.
  5. Derive per-filter distinct option lists from the cached snapshot (`cache.distinct_values(rows, field)`); build display page 1 via `filter → search → paginate` (defaults: each filter = its first distinct value, q="", page=1, size=25).
  6. Download gating: `can_download = downloads_enabled(...) AND report.report_id in DOWNLOADABLE_REPORT_IDS AND is_member(me(), DOWNLOAD_GROUP)` (me() via asyncio.to_thread; degrade-safe to False).
  7. Render `report.html` with: `nav_reports` (all enabled `{report_id,title}`), `active_report_id`, `report` (title, columns, filters), `dates`, `selected_date`, `filter_options` (field→values), `selected_filters`, `columns` (headers), `rows` (page-1 cells), `page/size/total_rows/total_pages`, `fetched_at`, `can_download`, and (efile only) the download context.

- **`GET /report/{report_id}/table?date=&<field>=&q=&page=&size=` (fragment):**
  1. `configs = await _load_reports()`; find report (else 404). `token = extract_user_token(...)` → on missing token return a bare inline `<tr>` message (matches Phase 3 pattern, never error.html into a tbody).
  2. Validate `date` against the OBO date list (400 if out-of-set — mirrors `validate_report_date`). Validate each `<field>` query key against `report.filters` field names (ignore unknown keys). Coerce `page`/`size` (`size` in {25,50,100} or "all").
  3. Ensure snapshot cached (same as full page). Apply `apply_filters → apply_search → paginate` over the cached rows (NO DB re-query).
  4. Build generic display cells for the page; return `_rows.html` fragment. Set response headers `X-Total-Rows`, `X-Page`, `X-Total-Pages` so JS can render the pager without polluting the row markup.

- **`GET /health`** — keep unchanged.

- **Refresh:** the fragment endpoint accepts `&refresh=1`; when present, `cache.evict(key)` before the ensure-cached step so the snapshot is re-read OBO and `fetched_at` re-stamped. The new `fetched_at` is returned in an `X-Fetched-At` header for the JS to update the "Last updated" label. (Full-page refresh also works via a normal reload since the snapshot is re-fetched only on miss — so refresh must go through the evict path.)

- **Download gating without special-casing the renderer:** a module constant
  `DOWNLOADABLE_REPORT_IDS: frozenset[str] = frozenset({"efile_glance"})` (interim; Phase 8 replaces with a
  config-driven flag). `report.html` does `{% if can_download and report.report_id in downloadable %}{% include "_download_efile.html" %}{% endif %}`. The generic table/toolbar never reference download.

---

## Per-User Cache (module + pure functions + sketches)

New PURE module **`src/app/cache.py`** (stdlib only: `collections.OrderedDict`, `time`, `dataclasses`). The
OBO/SP reads stay in `main.py`; cache.py holds only the data structure + pure row operations, so it is fully
unit-testable offline.

```python
from __future__ import annotations
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable
import time

@dataclass
class Snapshot:
    columns: list[str]          # all SELECTed cols (display ∪ filter fields)
    rows: list[dict]            # each row: {col_name: raw_value (str|None, as SQL API returns)}
    fetched_at: float           # time.time() at fetch

Key = tuple[str, str, str]      # (user_email, report_id, selected_date)

def make_key(user_email: str, report_id: str, selected_date: str) -> Key:
    return (user_email, report_id, selected_date)

class SnapshotCache:
    """In-process, per-user LRU snapshot cache. Bounded by max_size; optional TTL."""
    def __init__(self, max_size: int = 128, ttl_seconds: float | None = None) -> None:
        self._store: "OrderedDict[Key, Snapshot]" = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl_seconds

    def get(self, key: Key) -> Snapshot | None:
        snap = self._store.get(key)
        if snap is None:
            return None
        if self._ttl is not None and (time.time() - snap.fetched_at) > self._ttl:
            del self._store[key]                 # expired
            return None
        self._store.move_to_end(key)             # mark MRU
        return snap

    def put(self, key: Key, snap: Snapshot) -> None:
        self._store[key] = snap
        self._store.move_to_end(key)
        while len(self._store) > self._max_size:
            self._store.popitem(last=False)      # evict LRU

    def evict(self, key: Key) -> None:
        self._store.pop(key, None)               # refresh / logout

    def __len__(self) -> int:
        return len(self._store)
```

Pure row operations (module-level in cache.py — decoupled from `reports.ColumnSpec` so they stay stdlib-pure;
the display-text extraction is injected as a callable built in `render.py`):

```python
def apply_filters(rows: list[dict], filters: dict[str, str]) -> list[dict]:
    """Equality AND over raw string values. Empty/absent selection => no constraint."""
    out = rows
    for field, value in filters.items():
        if value == "":                          # sentinel: no constraint on this field
            continue
        out = [r for r in out if str(r.get(field, "")) == value]
    return out

def apply_search(rows: list[dict], query: str, haystack: Callable[[dict], str]) -> list[dict]:
    """Case-insensitive substring across the rendered display text of each row."""
    q = query.strip().lower()
    if not q:
        return rows
    return [r for r in rows if q in haystack(r).lower()]

def distinct_values(rows: list[dict], field: str) -> list[str]:
    """Sorted distinct non-null string values of `field` in the snapshot (feeds a dropdown)."""
    seen = {str(r[field]) for r in rows if r.get(field) is not None}
    return sorted(seen)

def paginate(rows: list[dict], page: int, size: int | None) -> tuple[list[dict], int, int]:
    """Return (page_rows, total_rows, total_pages). size=None => 'All' (one page)."""
    total = len(rows)
    if size is None or size <= 0:
        return rows, total, 1
    total_pages = max(1, (total + size - 1) // size)
    page = max(1, min(page, total_pages))
    start = (page - 1) * size
    return rows[start:start + size], total, total_pages
```

Module-level singleton in `main.py`: `_snapshot_cache = SnapshotCache(max_size=128, ttl_seconds=None)` (rely
on refresh + LRU; TTL optional). Per-user keying guarantees the cache only ever holds a user's own
OBO-authorized rows (no cross-user leak). Multi-replica: a miss on another replica just re-queries OBO —
correctness unaffected (documented non-goal).

Ensure-cached helper in main.py (the I/O boundary):

```python
async def _ensure_snapshot(token, email, report: ReportConfig, date: str, *, refresh: bool=False) -> Snapshot:
    key = make_key(email, report.report_id, date)
    if refresh:
        _snapshot_cache.evict(key)
    snap = _snapshot_cache.get(key)
    if snap is not None:
        return snap
    select_cols = _dedup([c.name for c in report.columns] + [f.field for f in report.filters])
    sql, params = build_report_query(report.source_fqn, select_cols, report.date_field, date,
                                     filters=None, order_by=report.order_by)
    cols, data = await _run_sql(token, sql, _to_sdk_params(params))
    rows = [dict(zip(cols, row)) for row in data]
    snap = Snapshot(columns=cols, rows=rows, fetched_at=time.time())
    _snapshot_cache.put(key, snap)
    return snap
```

(`_to_sdk_params` maps the `{"name","value","type"}` dicts to `StatementParameterListItem`, same as the audit path.)

---

## Generic Rendering (template + formatter)

New PURE module **`src/app/render.py`** (may import `reports.ColumnSpec` + reuse `shaping.format_count`/
`format_pct`/`_to_int`/`_to_float`/`EM_DASH`). Turns raw row dicts into pre-formatted cells so the template
stays dumb.

```python
from reports import ColumnSpec
from shaping import format_count, format_pct, EM_DASH, _to_int, _to_float

def cell_text(value, fmt: str) -> str:
    if fmt == "int":  return format_count(_to_int(value))
    if fmt == "pct":  return format_pct(_to_float(value))
    return "" if value is None else str(value)          # text / unknown

def pct_class(value) -> str:
    v = _to_float(value)
    if v is None:  return "irs-pct--muted"              # em-dash
    if v > 0:      return "irs-pct--pos"
    if v < 0:      return "irs-pct--neg"
    return ""

def align_class(fmt: str) -> str:
    return "text-right" if fmt in ("int", "pct") else ""

def header_cells(columns: list[ColumnSpec]) -> list[dict]:
    return [{"label": c.label, "align": align_class(c.format)} for c in columns]

def display_rows(columns: list[ColumnSpec], rows: list[dict]) -> list[list[dict]]:
    out = []
    for r in rows:
        cells = []
        for c in columns:
            css = align_class(c.format)
            if c.format == "pct":
                css = (css + " " + pct_class(r.get(c.name))).strip()
            cells.append({"text": cell_text(r.get(c.name), c.format), "css": css})
        out.append(cells)
    return out

def haystack_for(columns: list[ColumnSpec]):
    """Return a callable(row)->str joining the rendered display-text of each row (for search)."""
    return lambda r: " ".join(cell_text(r.get(c.name), c.format) for c in columns)
```

`_rows.html` (generalized — cells pre-rendered, template just loops):
```jinja
{% for row in rows %}
<tr>
  {% for cell in row %}
  {% if loop.first %}<th scope="row" class="{{ cell.css }}">{{ cell.text }}</th>
  {% else %}<td class="{{ cell.css }}">{{ cell.text }}</td>{% endif %}
  {% endfor %}
</tr>
{% endfor %}
```

`report.html` thead loops `columns` (header_cells: label + align). Tab nav in `base.html`:
```jinja
<nav class="irs-tabs" aria-label="Reports">
  {% for r in nav_reports %}
  <a class="irs-tab {% if r.report_id == active_report_id %}irs-tab--active{% endif %}"
     href="/report/{{ r.report_id }}">{{ r.title }}</a>
  {% endfor %}
</nav>
```
(`nav_reports`/`active_report_id` default to empty via `{{ nav_reports|default([]) }}` so error.html still renders.)

Confirmed: `.irs-pct--pos`/`.irs-pct--neg` already in `app.css` (add a muted rule `.irs-pct--muted{color:var(--irs-muted)}`
if desired). Report #1's CY/PY/%-change look is reproduced purely from its config row + these classes.

---

## Filters / Date / Search / Pagination flow (server-side over cache)

1. **Snapshot load (once per (user,report,date)):** OBO `build_report_query` selecting
   `display ∪ filter` columns, date-scoped only (`filters=None`), `ORDER BY order_by`. Cache it.
2. **Distinct filter values:** derive from the cached snapshot via `cache.distinct_values(rows, field)` —
   this avoids an extra OBO round-trip AND guarantees the options match the snapshot. (`reports.build_distinct_values_query`
   remains available as a fallback for a filter field you might choose NOT to project, but the recommended
   path projects filter fields and derives distincts from cache.)
3. **Filter:** `apply_filters(rows, selected_filters)` — equality AND; each filter default = its first
   distinct value (reproduces drain=ALL default). Empty string means "no constraint" (reserved if a planner
   adds an "(All)" sentinel option — see Risks).
4. **Search:** `apply_search(filtered, q, haystack_for(display_columns))` — case-insensitive substring over
   the rendered display-text of the DISPLAY columns only.
5. **Paginate:** `paginate(searched, page, size)` — size 25/50/100/None("All"); returns page rows + totals.
6. **Render:** `render.display_rows(display_columns, page_rows)` → `_rows.html`. Totals go out as response
   headers for the JS pager.

Identifiers (columns, filter fields, order_by, fqn parts) are validated by `reports.validate_identifier`/
`validate_fqn` at query-build time; the date VALUE and filter VALUES that hit the DB are bound params. The
in-app filter/search compares plain strings — no SQL, no injection surface.

---

## Reuse vs Retire

| Item | Action | Notes |
|---|---|---|
| `reports.py` (whole engine) | **REUSE** | The Phase-6 foundation; the load/date/distinct/registry builders. |
| `auth.py` (`extract_user_token/email`, `is_member`, `DOWNLOAD_GROUP`, `group_display_names`) | **REUSE** | Unchanged; used by every route. |
| `config.py` (`downloads_enabled`) | **REUSE** | Kill switch for interim download gating. |
| `shaping.format_count/format_pct/_to_int/_to_float/EM_DASH` | **REUSE** | Wrapped by `render.py`'s generic formatter. |
| `shaping.rows_to_context/METRIC_ORDER/_REQUIRED_COLUMNS` | **KEEP (interim)** | Only feeds `POST /download`; retire in Phase 8. |
| `exports.py`, `audit.py` | **KEEP (interim)** | Feed `POST /download`; Phase 8 generalizes (report_id in audit). |
| `_app_sp_client()` | **REUSE** | Registry read (SP). Add an SP **read** variant returning (cols, rows). |
| `_user_client` / `_run_sql` / `asyncio.to_thread` | **REUSE** | OBO snapshot reads. |
| `queries.py` (`build_glance_query*`, `validate_drain`, `validate_report_date`) | **RETIRE after generic path works** | Keep only what `POST /download` still imports until Phase 8 (`build_glance_query_for_date`, `build_report_dates_query`, `validate_drain`, `validate_report_date`, `_fqn`). Drop `build_glance_query` (the MAX-subquery initial render) once `/` redirects. |
| `DRAIN_OPTIONS` constant | **RETIRE** | Drain is now a config-driven filter with distincts from the snapshot. |
| `glance.html` | **RETIRE → `report.html`** | Generic page. |
| glance-specific `_rows.html` | **REPLACE** | Generic `(columns, rows)` cells. |
| inline download modal in glance.html | **MOVE → `_download_efile.html`** | Included only for report #1 + can_download. |
| `POST /download` route | **KEEP UNCHANGED (interim)** | efile-only; Phase 8 makes it generic. |
| bundle `resources/app.yml`, `src/app/app.yaml` | **NO CHANGE** | No new resource, no new env, no new UC object. |
| `requirements` | **NO CHANGE** | No new runtime dependency (cache/render are stdlib + existing). |

---

## JS Plan (`static/js/app.js`, local only — no CDN)

Generalize from hardcoded `glance-*` ids to config-driven controls that all POST-back through the fragment endpoint:

- Read `report_id` and control refs from stable data hooks: table container `data-report-id`; search input
  `data-role="report-search"`; date select `data-role="report-date"`; each filter select
  `data-role="report-filter"` + `data-field`; row-count select `data-role="report-size"`; refresh button
  `data-role="report-refresh"`; pager container `data-role="report-pager"`; last-updated label `data-role="report-updated"`.
- `buildQuery()` collects date + every filter (`<field>=value`) + `q` + `page` + `size` (`size="all"` → omit or send `all`).
- `refreshFragment(extra)` → `fetch("/report/"+reportId+"/table?"+qs, {headers:{Accept:"text/html"}})`; swap
  `tbody.innerHTML`; read `X-Total-Rows`/`X-Total-Pages`/`X-Page`/`X-Fetched-At` headers to redraw the pager +
  "Last updated" label; then `syncDownloadFields()` (report #1 only).
- Events: date/filter/size `change` → reset page=1, fetch. Search `input` → **debounce 250 ms** → page=1, fetch
  (server-side now, unlike Phase 3's client-side show/hide). Pager click → set page, fetch. Refresh click →
  fetch with `refresh=1` (server evicts + re-reads, re-stamps fetched_at).
- `syncDownloadFields()`: guarded (fields absent for non-efile / non-members) — keep hidden `report_date`,
  `drain`, `search` in the efile download form synced to the live controls (drain read from the filter select
  whose `data-field="drain"`), so the interim export still matches the on-screen view.
- No framework, no npm, no remote assets (air-gap). Vanilla `fetch` + DOM.

---

## Files to Add / Modify

### Add
| File | Purpose |
|---|---|
| `src/app/cache.py` | PURE: `Snapshot`, `make_key`, `SnapshotCache` (LRU + optional TTL), `apply_filters`, `apply_search`, `distinct_values`, `paginate`. |
| `src/app/render.py` | PURE: generic `cell_text`, `pct_class`, `align_class`, `header_cells`, `display_rows`, `haystack_for` (reuses shaping formatters). |
| `src/app/templates/report.html` | Generic report page (replaces glance.html): hero/title, toolbar (search/date/filters/size), last-updated, refresh, generic table, optional download include. |
| `src/app/templates/_download_efile.html` | The efile-only download modal (moved out of glance.html), included for report #1 + can_download. |
| `tests/test_cache.py` | LRU eviction + MRU on get + TTL expiry + `apply_filters` (equality AND) + `apply_search` (substring over haystack) + `paginate` (page slicing, "All", clamp) + `distinct_values` (sorted, null-skip) + `make_key`. |
| `tests/test_render.py` | `cell_text` (int/pct/text/unknown/None), `pct_class` (pos/neg/muted), `align_class`, `display_rows` (cells aligned to ColumnSpec incl. pct css), `haystack_for`. |

### Modify
| File | Change |
|---|---|
| `src/app/main.py` | Add `_load_reports` (TTL cache, SP read), SP read variant of `_run_sql`, `_ensure_snapshot`, `_to_sdk_params`, module `_snapshot_cache`, `DOWNLOADABLE_REPORT_IDS`. Replace `/` with redirect. Add `GET /report/{report_id}` + `GET /report/{report_id}/table`. Keep `/health`, keep `POST /download` unchanged. Drop `build_glance_query`/`DRAIN_OPTIONS` imports/usage. |
| `src/app/templates/base.html` | Add tab-nav block (`nav_reports` + `active_report_id`), defaulting empty so error.html renders. |
| `src/app/templates/_rows.html` | Generalize to loop pre-rendered `rows` (list of cell lists); first cell as `<th scope="row">`. |
| `src/app/static/js/app.js` | Generalize to config-driven controls → fragment endpoint (debounced search, filters, size, pager, refresh, header-driven pager/updated label; keep efile download-field sync). |
| `src/app/static/css/app.css` | (Optional) add `.irs-pct--muted` + `.irs-tabs`/`.irs-tab`/`.irs-tab--active` styles. |
| `src/app/queries.py` | Trim to only what `POST /download` still uses (interim). Remove `build_glance_query` once `/` no longer calls it. |
| `tests/test_reports.py` | Extend with report selection/ordering-from-parsed-config-list assertions if not already covered (display_order sort, first-enabled pick). |

### Deploy
`databricks bundle deploy --target dev` then `databricks bundle run download_hub` (standard engine; app
redeploy rebuilds the venv — slow). Manual browser checkpoint on dev (OBO/me()/SP reads are the I/O boundary,
not unit-tested). Optionally MERGE the 2nd demo row at the checkpoint to prove multi-tab.

---

## Recommended References (executor should read before coding)
- `~/.ai-dev-kit/repo/.claude/skills/databricks-python-sdk/SKILL.md` — Statement Execution + the CRITICAL
  `asyncio.to_thread` rule (SDK is fully synchronous); `Config(host, token, auth_type="pat")` OBO construction.
- `~/.ai-dev-kit/repo/.claude/skills/python-dev/SKILL.md` — pure, type-hinted, Google-docstring functions;
  pytest in `./tests/`; keep cache.py/render.py import-clean (no SDK/network) so tests run offline.
- In-repo (authoritative): `src/app/reports.py` (the engine — reuse), `src/app/main.py`, `src/app/shaping.py`
  (formatters to reuse), `src/app/auth.py`, `src/app/templates/{base,glance,_rows,error}.html`,
  `src/app/static/js/app.js`, `src/app/static/css/app.css`.
- `.planning/phases/2-RESEARCH.md` — OBO / `auth_type="pat"` / Statement-Execution patterns.
- `.planning/phases/3-RESEARCH.md` — the `/table` fragment + `StatementParameterListItem` binding shape being generalized.
- `.planning/phases/7-CONTEXT.md` — the locked decisions this build implements.

---

## Risks / Notes
- **Filter field must be projected into the snapshot.** report #1's `drain` is NOT a display column; the
  load query MUST select `display ∪ filter` columns or in-app filtering/distincts break. This is the single
  most important implementation detail — encode it in `_ensure_snapshot`'s `select_cols` and unit-test that
  `build_report_query` receives the union.
- **Default filter selection vs. an "(All)" sentinel.** Recommended: default each filter to its FIRST
  distinct value (for drain that's `ALL` alphabetically → reproduces today's 17-row default). An "(All)/no
  filter" sentinel would, for efile, show all 4 drains at once (68 rows, duplicated metric names) — undesirable.
  If the planner wants a sentinel for future reports, `apply_filters` already treats `value == ""` as "no
  constraint"; wire the sentinel option value to `""`. Keep report #1 defaulting to a real drain value.
- **Distinct-from-cache vs. `build_distinct_values_query`.** Deriving distincts from the cached snapshot (the
  recommended path) removes an OBO round-trip and stays consistent with the snapshot. Only fall back to
  `build_distinct_values_query` if a filter field is deliberately not projected.
- **Registry TTL vs. checkpoint MERGE.** `_load_reports` TTL-caches parsed configs; a 2nd row added by MERGE
  appears after the TTL (≤300 s) or an app restart. For an immediate demo, restart the app or set a short TTL.
- **Search semantics changed from Phase 3.** Phase 3 searched client-side (show/hide DOM rows); Phase 7
  searches server-side over the cached snapshot so it composes with pagination (you can't paginate a
  client-hidden set correctly). Debounce the input to avoid a fetch per keystroke.
- **Pager needs totals.** `_rows.html` returns rows only; pass `X-Total-Rows`/`X-Total-Pages`/`X-Page` (and
  `X-Fetched-At` for the updated-label) as response headers so the fragment stays pure markup.
- **`report_date` string equality is still the linchpin** (2/3-RESEARCH): the date `<select>` value,
  `format_report_date` output, and the bound TIMESTAMP param must be identical `"%Y-%m-%d %H:%M:%S"`. The
  generic path reuses `reports.build_report_query` (binds TIMESTAMP) — do not let JS reformat the value.
- **Fragment error handling:** missing token / UC-denied inside the fragment → return a bare inline
  `<tr><td colspan=..>` message, never `error.html` (which would render malformed inside a `<tbody>`).
- **Access model:** show all enabled tabs to everyone; a report the user can't read OBO renders an empty
  table + "no data / you may not have access" notice. No per-tab access probing — UC enforces (empty rows).
- **SDK not importable in the dev `.venv`** (lives in the Apps runtime); keep cache.py/render.py SDK-free so
  their tests run offline. Live OBO/SP behavior is verified at the browser checkpoint.
- **No new runtime dependency, no UC object, no bundle change** — enforced by CONTEXT. `openpyxl`/generic
  download/audit-report_id are Phase 8.
