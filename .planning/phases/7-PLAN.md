---
phase: 7-multi-tab-portal-cache-filters-pagination
plan: 7
type: execute
status: planned
workspace_url: https://field-eng-aws-gov-demo-irs-demo.cloud.databricks.us
default_catalog: irs
skill_references:
  - ~/.ai-dev-kit/repo/.claude/skills/python-dev/SKILL.md
  - ~/.ai-dev-kit/repo/.claude/skills/databricks-python-sdk/SKILL.md
wave_count: 7
---

# Phase 7: Multi-tab portal — generic render + per-user cache + refresh + filters + pagination

## Goal
Refactor the single hardcoded page into a config-driven multi-tab portal: URL per report
(`/report/{id}`) + tab nav, generic table rendering from `report_config`, a per-user snapshot cache
(OBO), refresh + last-query time, and config-driven date selector + equality filters + substring
search + row-count pagination — all server-side over the cache. Report #1 keeps its existing
download (generic download is Phase 8).

## Workspace Context (live)
- `irs.efile.report_config` has 1 row (`efile_glance`, 4 display cols, `drain` filter, order_by
  `sort_order`). App SP has SELECT on it. Warehouse `2f225c0740dcd22b` healthy. No new UC objects,
  no bundle-resource change, no new runtime dependency this phase.

## Prerequisites
- [ ] Phase 6 complete (report_config live + reports.py). Branch dbx/download-hub-phase-1. CLI DEFAULT valid.

## Skills to Read Before Executing
- `~/.ai-dev-kit/repo/.claude/skills/databricks-python-sdk/SKILL.md` — Statement Execution; **asyncio.to_thread**; `Config(host,token,auth_type="pat")` OBO; plain `WorkspaceClient()` SP.
- `~/.ai-dev-kit/repo/.claude/skills/python-dev/SKILL.md` — pure funcs, type hints, pytest; keep cache.py/render.py SDK-free.
- In-repo: `src/app/reports.py` (engine, REUSE), `src/app/main.py` (refactor), `src/app/shaping.py` (formatters), `src/app/auth.py`, templates `{base,glance,_rows,error}.html`, `static/js/app.js`, `static/css/app.css`.
- `.planning/phases/7-RESEARCH.md` — full code sketches for cache.py/render.py/routes/templates/JS (authoritative). `.planning/phases/3-RESEARCH.md` — the `/table` fragment + param pattern being generalized.

---

## LOCKED DECISIONS (executor MUST follow verbatim)

### L1 — Snapshot query selects display ∪ filter columns (critical)
`_ensure_snapshot` builds `select_cols = dedup([c.name for c in report.columns] + [f.field for f in
report.filters])` and calls `reports.build_report_query(source_fqn, select_cols, date_field, date,
filters=None, order_by=report.order_by)`. The FILTER FIELD (e.g. `drain`) is NOT a display column but
MUST be projected or in-app filtering/distincts break. The renderer emits ONLY the display columns.

### L2 — Per-user cache is pure + in-memory, OBO reads stay in main.py
New PURE `src/app/cache.py` (stdlib only: OrderedDict/time/dataclasses): `Snapshot(columns,rows,
fetched_at)`, `make_key(user_email,report_id,date)`, `SnapshotCache` (LRU max_size=128, optional TTL;
get→move_to_end, put→evict LRU, evict), and pure `apply_filters` (equality AND; `""`=no constraint),
`apply_search` (case-insensitive substring over an injected haystack callable), `distinct_values`
(sorted, null-skip), `paginate` (size None="All"; clamp page). NO SDK import. Module singleton in
main.py: `_snapshot_cache = SnapshotCache(max_size=128)`. Keyed by user_email (X-Forwarded-User) →
only ever the user's own OBO data (no cross-user leak).

### L3 — Generic rendering is pure (render.py) reusing shaping formatters
New PURE `src/app/render.py`: `cell_text(value,fmt)` (int→format_count, pct→format_pct, else str/""),
`pct_class` (pos/neg/muted), `align_class` (right for int/pct), `header_cells(columns)`,
`display_rows(columns, rows)` → list of cell dicts `{text,css}` (first cell is the row header;
pct cells get the sign color class), `haystack_for(columns)` → callable joining display text for
search. Reuses `shaping.format_count/format_pct/_to_int/_to_float/EM_DASH`.

### L4 — Routing
`GET /` → redirect to first enabled report. `GET /report/{report_id}` full page (nav tabs, date
selector, filters, search, size selector, table page-1, refresh, last-updated; 404 error.html if
report absent/disabled; 401 error.html if no OBO token; if the OBO read is UC-denied → render the
page with an EMPTY table + "no data / you may not have access" notice, tabs still shown).
`GET /report/{report_id}/table?date=&<field>=&q=&page=&size=&refresh=` → server-rendered `_rows.html`
fragment from the cache; totals + fetched_at returned as `X-Total-Rows`/`X-Total-Pages`/`X-Page`/
`X-Fetched-At` headers; missing token/UC-denied → a bare inline `<tr>` message (never error.html into
a tbody). `refresh=1` → `cache.evict(key)` then reload OBO (re-stamp fetched_at). Keep `/health`.

### L5 — Registry read as app SP, TTL-cached
`_load_reports()` runs `reports.build_report_config_query(catalog,schema)` via a NEW SP **read**
variant of the SQL helper (refactor `_run_sql`/`_run_sql_sp` to share `_exec(client,sql,params)->
(cols,rows)`), parses via `reports.parse_report_config`, sorts by display_order, in-process TTL-cached
(~300s). Values bound as params; identifiers validated in reports.py.

### L6 — Filters default to first distinct value; download interim-gated
Each filter defaults to its FIRST distinct value from the cached snapshot (reproduces efile drain=ALL
→ 17 rows). Distincts derived from the cached snapshot (`cache.distinct_values`), not an extra query.
Download button gated by `DOWNLOADABLE_REPORT_IDS = frozenset({"efile_glance"})` AND downloads_enabled
AND is_member — via a `_download_efile.html` partial included only for report #1; the generic
renderer never references download. `POST /download` + its queries.py/exports/audit deps stay
UNCHANGED (Phase 8 generalizes).

### L7 — No regressions / air-gap
report_date string equality linchpin preserved (build_report_query binds TIMESTAMP; JS passes the
select value verbatim). Search is now server-side over the cache (debounce 250ms in JS). No new
runtime dependency, no CDN/external URL, no bundle/UC change. Retire `build_glance_query` +
`DRAIN_OPTIONS` + `glance.html` once the generic path works.

---

## Wave 1: pure cache.py

<task type="auto">
  <name>Implement src/app/cache.py (Snapshot, SnapshotCache LRU/TTL, filter/search/paginate/distinct)</name>
  <wave>1</wave>
  <files>src/app/cache.py</files>
  <read_first>
    - ~/.ai-dev-kit/repo/.claude/skills/python-dev/SKILL.md
    - .planning/phases/7-RESEARCH.md (Per-User Cache sketch — copy signatures/bodies)
  </read_first>
  <action>
    Write PURE src/app/cache.py (stdlib only: collections.OrderedDict, time, dataclasses, typing).
    NO fastapi/databricks/pyspark import. Implement per RESEARCH: `Snapshot` dataclass; `Key` +
    `make_key`; `SnapshotCache(max_size=128, ttl_seconds=None)` with get (TTL expiry + move_to_end),
    put (append + evict LRU beyond max_size), evict, __len__; module functions `apply_filters(rows,
    filters)` (equality AND, ""=no constraint, compare str(r.get(field,""))), `apply_search(rows,
    query, haystack)` (case-insensitive substring; empty q → all), `distinct_values(rows, field)`
    (sorted set of non-null str values), `paginate(rows, page, size)` → (page_rows, total, total_pages)
    with size None/<=0 = "All" (1 page) and page clamped. Typed + Google docstrings.
  </action>
  <verify>cd /Users/greg.skinner/Documents/IRS/download_hub && .venv/bin/python -m py_compile src/app/cache.py && PYTHONPATH=src .venv/bin/python -c "
from app.cache import SnapshotCache, Snapshot, make_key, apply_filters, apply_search, distinct_values, paginate
c=SnapshotCache(max_size=2)
c.put(make_key('u','r','d1'), Snapshot(['a'],[{'a':'1'}],0.0)); c.put(make_key('u','r','d2'),Snapshot(['a'],[],0.0)); c.put(make_key('u','r','d3'),Snapshot(['a'],[],0.0))
print('len<=2:', len(c)==2, 'lru-evicted:', c.get(make_key('u','r','d1')) is None)
rows=[{'drain':'ALL','metric_name':'X'},{'drain':'E','metric_name':'Y'}]
print(len(apply_filters(rows,{'drain':'ALL'}))==1, len(apply_filters(rows,{'drain':''}))==2, distinct_values(rows,'drain'), apply_search(rows,'x',lambda r:r['metric_name'])[0]['metric_name'], paginate(list(range(10)),2,3))"</verify>
  <acceptance_criteria>
    - No SDK/fastapi/pyspark import; stdlib only.
    - SnapshotCache: LRU eviction beyond max_size, get moves to MRU, TTL expiry when set, evict removes.
    - apply_filters equality-AND with ""=no-constraint; apply_search case-insensitive substring over haystack; distinct_values sorted + null-skip; paginate returns (rows,total,pages) with "All" (size None) and page clamping.
  </acceptance_criteria>
</task>

---

## Wave 2: pure render.py

<task type="auto">
  <name>Implement src/app/render.py (generic cell/formatting from ColumnSpec)</name>
  <wave>2</wave>
  <files>src/app/render.py</files>
  <read_first>
    - src/app/shaping.py (format_count/format_pct/_to_int/_to_float/EM_DASH to reuse)
    - src/app/reports.py (ColumnSpec)
    - .planning/phases/7-RESEARCH.md (Generic Rendering sketch)
  </read_first>
  <action>
    Write PURE src/app/render.py (may import reports.ColumnSpec + shaping formatters; NO SDK/fastapi).
    Implement per RESEARCH: `cell_text(value,fmt)` (int→format_count(_to_int), pct→format_pct(_to_float),
    text/unknown→"" if None else str), `pct_class(value)` (None→irs-pct--muted, >0→irs-pct--pos,
    <0→irs-pct--neg, else ""), `align_class(fmt)` ("text-right" for int/pct), `header_cells(columns)`
    → [{label,align}], `display_rows(columns, rows)` → list of row cell-lists [{text,css}] (pct cells
    append pct_class), `haystack_for(columns)` → callable(row)->joined display text. Typed + docstrings.
  </action>
  <verify>cd /Users/greg.skinner/Documents/IRS/download_hub && .venv/bin/python -m py_compile src/app/render.py && PYTHONPATH=src .venv/bin/python -c "
from app.render import cell_text, pct_class, align_class, display_rows, haystack_for
from app.reports import ColumnSpec
print(cell_text(1234567,'int'), cell_text(20.0,'pct'), cell_text(None,'pct'), pct_class(-5.0), align_class('text' ))
cols=[ColumnSpec('metric_name','Metric','text'),ColumnSpec('pct_change','% Change','pct')]
r=[{'metric_name':'X','pct_change':-5.8}]
dr=display_rows(cols,r); print(dr[0][0]['text'], dr[0][1]['text'], 'irs-pct--neg' in dr[0][1]['css'])
print(haystack_for(cols)(r[0]))"</verify>
  <acceptance_criteria>
    - No SDK/fastapi import.
    - cell_text: int→thousands, pct→signed %, None(pct)→em dash, text→str/"" ; pct_class pos/neg/muted; align_class right for int/pct.
    - display_rows aligns cells to ColumnSpec order, first cell is the row header text, pct cells carry the sign color css; haystack_for joins display text.
  </acceptance_criteria>
</task>

---

## Wave 3: unit tests for cache + render

<task type="auto">
  <name>Write tests/test_cache.py and tests/test_render.py</name>
  <wave>3</wave>
  <files>tests/test_cache.py, tests/test_render.py</files>
  <read_first>
    - src/app/cache.py, src/app/render.py (modules under test)
    - tests/test_reports.py (style)
  </read_first>
  <action>
    test_cache.py: LRU eviction beyond max_size; get() moves to MRU (so a subsequently-added item
    evicts the other); TTL expiry (ttl_seconds small, monkeypatch/skip if flaky — use fetched_at in the
    past); evict removes; apply_filters (equality AND across 2 fields, ""=no constraint, missing field);
    apply_search (substring hit/miss, empty q returns all, case-insensitive via a haystack lambda);
    distinct_values (sorted, dedup, null-skip); paginate (page slicing, clamp beyond last page, size
    None="All" single page, size 0 = All).
    test_render.py: cell_text int/pct/text/unknown/None; pct_class pos/neg/muted(None)/zero;
    align_class; header_cells labels+align; display_rows (cells match ColumnSpec order, first cell header,
    pct css by sign, NULL pct → em dash + muted); haystack_for joins.
  </action>
  <verify>cd /Users/greg.skinner/Documents/IRS/download_hub && PYTHONPATH=src .venv/bin/python -m pytest tests/ -q</verify>
  <acceptance_criteria>
    - All tests pass (prior 129 + new). No pyspark/fastapi/databricks import.
    - Cache LRU + TTL + filter/search/paginate/distinct covered; render cell/pct/align/display_rows/haystack covered incl. NULL-pct em dash + muted class.
  </acceptance_criteria>
</task>

---

## Wave 4: main.py refactor (config-driven routes + cache wiring)

<task type="auto">
  <name>Refactor main.py to config-driven /report routes + per-user snapshot cache</name>
  <wave>4</wave>
  <files>src/app/main.py</files>
  <read_first>
    - ~/.ai-dev-kit/repo/.claude/skills/databricks-python-sdk/SKILL.md (asyncio.to_thread, Statement Execution, SP vs OBO client)
    - src/app/main.py (current routes/helpers to keep vs replace)
    - src/app/reports.py, src/app/cache.py, src/app/render.py, src/app/auth.py, src/app/config.py, src/app/shaping.py
    - .planning/phases/7-RESEARCH.md (Routing & Refactor Plan + _ensure_snapshot + _load_reports sketches)
  </read_first>
  <action>
    KEEP: _env, _user_client (auth_type="pat"), _app_sp_client, /health, and POST /download UNCHANGED
    (interim efile-only, with its queries.py/exports/audit deps). Refactor the SQL helpers to share
    `_exec(client, sql, params) -> (cols, rows)`; keep `_run_sql(token,...)` (OBO) + add `_run_sql_sp_query(sql,
    params=None) -> (cols, rows)` (SP read) alongside the existing `_run_sql_sp` audit-insert.
    ADD:
    - module singletons: `_snapshot_cache = SnapshotCache(max_size=128)`; `_reports_cache` (TTL ~300s);
      `DOWNLOADABLE_REPORT_IDS = frozenset({"efile_glance"})`; `_to_sdk_params(params)` (dict→StatementParameterListItem, mirror the audit path).
    - `_load_reports()` (SP read via build_report_config_query → parse_report_config → sort display_order → TTL cache).
    - `_ensure_snapshot(token,email,report,date,*,refresh=False)` per RESEARCH L1: select_cols = dedup(display+filter),
      build_report_query OBO (filters=None, order_by), Snapshot(cols,rows,time.time()), cache put; refresh→evict first.
    - `GET /` → redirect to first enabled report (307); no enabled → error.html.
    - `GET /report/{report_id}` → find report (404 error.html if absent/disabled); extract token (401) + email;
      resolve dates OBO (UC-denied → render report.html with empty rows + no-access notice, tabs shown);
      selected_date from ?date if in list else latest; ensure snapshot; per-filter distincts from cache;
      defaults each filter = first distinct; page1 via apply_filters→apply_search→paginate (size 25);
      display cells via render.display_rows/header_cells; can_download = downloads_enabled AND report_id in
      DOWNLOADABLE_REPORT_IDS AND is_member(me()) (me() via asyncio.to_thread, degrade False); render report.html
      with nav_reports/active_report_id/report/dates/selected_date/filter_options/selected_filters/columns(headers)/
      rows(cells)/page/size/total_rows/total_pages/fetched_at/can_download (+ efile download context).
    - `GET /report/{report_id}/table` → find report (404); token missing → bare inline <tr> message; validate
      date against OBO date list (400) + filter keys against report.filters; coerce page/size (25/50/100/all);
      refresh=1 → evict; ensure snapshot; apply_filters→apply_search→paginate; return _rows.html fragment with
      headers X-Total-Rows/X-Total-Pages/X-Page/X-Fetched-At.
    RETIRE: build_glance_query import/usage + DRAIN_OPTIONS (keep build_glance_query_for_date/validate_drain/
    validate_report_date/build_report_dates_query only where POST /download still needs them). No hardcoded host/token/warehouse.
  </action>
  <verify>.venv/bin/python -m py_compile src/app/main.py && grep -c "report/{report_id}" src/app/main.py && grep -c "_ensure_snapshot\|_load_reports\|_snapshot_cache" src/app/main.py && ! grep -q "DRAIN_OPTIONS" src/app/main.py && echo ok</verify>
  <acceptance_criteria>
    - Compiles. `/` redirects to first enabled report; `GET /report/{id}` + `GET /report/{id}/table` exist; `/health` + `POST /download` intact.
    - _ensure_snapshot selects dedup(display+filter) cols, reads OBO via asyncio.to_thread, caches Snapshot; refresh evicts+reloads.
    - _load_reports reads registry as the SP (read variant), parses+sorts, TTL-cached. can_download gated by downloadable set + downloads_enabled + is_member.
    - Fragment returns _rows.html + X-Total-Rows/X-Total-Pages/X-Page/X-Fetched-At headers; missing token → inline <tr>, not error.html.
    - No DRAIN_OPTIONS / build_glance_query in the render path; no hardcoded host/token/warehouse.
  </acceptance_criteria>
</task>

---

## Wave 5: templates (generic report page + tab nav + fragment + efile download partial)

<task type="auto">
  <name>Add report.html + _download_efile.html, generalize _rows.html + base.html tab nav</name>
  <wave>5</wave>
  <files>src/app/templates/report.html, src/app/templates/_download_efile.html, src/app/templates/_rows.html, src/app/templates/base.html, src/app/static/css/app.css</files>
  <read_first>
    - src/app/templates/glance.html (source of the toolbar/table/modal markup to generalize/move)
    - src/app/templates/base.html, _rows.html
    - .planning/phases/7-RESEARCH.md (Generic Rendering template + tab nav sketches)
  </read_first>
  <action>
    - base.html: add a tab-nav block (loop nav_reports → <a href="/report/{id}"> with active class), defaulting
      `nav_reports|default([])` + `active_report_id|default('')` so error.html still renders. Keep masthead/banner/footer + app.css/app.js includes.
    - _rows.html: generalize to loop pre-rendered `rows` (list of cell lists {text,css}); first cell `<th scope="row" class=..>`, rest `<td class=..>`. No efile-specific columns.
    - report.html (replaces glance.html; keep the filename glance.html deletable): extends base; hero title from report.title; a toolbar with search input (data-role=report-search), date select (data-role=report-date), one select per filter (data-role=report-filter data-field=..), row-count select (25/50/100/All, data-role=report-size), refresh button (data-role=report-refresh), and a "Last updated {{ fetched_at }}" label (data-role=report-updated); the container carries data-report-id. thead loops header_cells (label+align); tbody id=report-tbody includes _rows.html; a pager container (data-role=report-pager). Empty/no-access notice when rows is empty. Include `_download_efile.html` only `{% if can_download and report.report_id in downloadable %}`.
    - _download_efile.html: the efile download modal moved verbatim from glance.html (ack + justification + format + hidden report_date/drain/search; posts to /download). Unchanged behavior.
    - app.css: add `.irs-tabs`/`.irs-tab`/`.irs-tab--active` (simple underline/active styling) + `.irs-pct--muted{color:var(--irs-muted)}`. No CDN.
    - Delete glance.html once report.html is in place (or leave unreferenced; prefer delete to avoid confusion).
  </action>
  <verify>cd /Users/greg.skinner/Documents/IRS/download_hub && test -f src/app/templates/report.html && test -f src/app/templates/_download_efile.html && grep -q "nav_reports" src/app/templates/base.html && grep -q "report-tbody" src/app/templates/report.html && grep -q "data-report-id" src/app/templates/report.html && ! grep -rniE "unpkg|jsdelivr|https?://[^\"' )]+" src/app/templates src/app/static/css && echo ok</verify>
  <acceptance_criteria>
    - base.html renders tab nav from nav_reports (active highlighted), defaults empty for error.html.
    - report.html: generic toolbar (search/date/filters/size/refresh + last-updated), generic thead/tbody (via header_cells + _rows.html include), pager container, data-report-id + data-role hooks; empty/no-access notice; efile download partial included only for report #1 + can_download.
    - _rows.html is generic (cell lists); _download_efile.html holds the (unchanged) efile modal.
    - No CDN/external URLs; app.css has tab + muted-pct styles.
  </acceptance_criteria>
</task>

---

## Wave 6: generalize app.js

<task type="auto">
  <name>Generalize static/js/app.js to config-driven controls + fragment endpoint</name>
  <wave>6</wave>
  <files>src/app/static/js/app.js</files>
  <read_first>
    - src/app/static/js/app.js (current glance-specific wiring)
    - .planning/phases/7-RESEARCH.md (JS Plan)
  </read_first>
  <action>
    Rewrite app.js (vanilla, local only, no CDN) per RESEARCH: read report_id + control refs from data hooks
    (data-report-id; data-role report-search/report-date/report-filter[data-field]/report-size/report-refresh/
    report-pager/report-updated). buildQuery() collects date + each filter (field=value) + q + page + size.
    refreshFragment(): fetch `/report/{id}/table?`+qs (Accept text/html) → set report-tbody innerHTML → read
    X-Total-Rows/X-Total-Pages/X-Page/X-Fetched-At headers to redraw pager + "Last updated" label → syncDownloadFields().
    Events: date/filter/size change → page=1 + fetch; search input → debounce 250ms → page=1 + fetch; pager click →
    set page + fetch; refresh click → fetch with refresh=1. syncDownloadFields(): guarded (absent for non-efile/
    non-members) — keep the efile download form's hidden report_date/drain/search synced (drain from the filter
    select with data-field="drain"). All controls no-op gracefully if absent.
  </action>
  <verify>cd /Users/greg.skinner/Documents/IRS/download_hub && grep -q "report-tbody\|/report/" src/app/static/js/app.js && grep -q "refresh=1\|refresh" src/app/static/js/app.js && ! grep -rniE "unpkg|jsdelivr|cdnjs|https?://" src/app/static/js/app.js && echo ok</verify>
  <acceptance_criteria>
    - app.js wires date/filter/size/search(debounced)/pager/refresh to `/report/{id}/table`, swaps report-tbody, and updates pager + last-updated from response headers.
    - Refresh sends refresh=1; download hidden-field sync guarded for report #1; graceful no-op when controls absent; no CDN/external URLs.
  </acceptance_criteria>
</task>

---

## Checkpoint: deploy + verify multi-tab portal

<task type="checkpoint:human">
  <name>Deploy, verify tabs/cache/refresh/filters/pagination; report #1 download still works</name>
  <wave>7</wave>
  <action>
    From repo root (branch dbx/download-hub-phase-1):
    1. PYTHONPATH=src .venv/bin/python -m pytest tests/ -q
    2. databricks bundle validate -t dev -p DEFAULT && databricks bundle deploy --target dev -p DEFAULT
    3. databricks bundle run download_hub -t dev -p DEFAULT   (slow venv rebuild)
    4. databricks apps get download-hub -p DEFAULT → ACTIVE/SUCCEEDED
    5. (Optional multi-tab proof) MERGE a 2nd demo report row (efile_pins, see 7-RESEARCH §Live Findings) via
       the Statement Execution API, then restart the app (or wait for the registry TTL) so it appears.
    6. Browser (as greg.skinner): `/` redirects to the E-File tab; the tab nav shows report(s); the table renders
       generically (2026/2025/% Change with green/red % coloring) for report_date 2026-01-12 / drain=ALL (17 rows);
       changing DRAIN/date swaps rows (server round-trip over cache); typing in search trims + composes with the
       filter + survives paging; the row-count selector pages; Refresh updates the "Last updated" time; a second
       load of the same tab is fast (cache hit). The E-File tab still shows the Download button → modal → CSV/Excel
       downloads (interim flow intact); I will confirm the audit row lands.
    7. If a tab's source is unreadable for a test user, it shows the empty/no-access notice (tabs still listed).
  </action>
  <acceptance_criteria>
    - pytest passes; bundle validate+deploy succeed; app ACTIVE.
    - / redirects to the first report; generic table renders with correct formatting/coloring; DRAIN/date/search/
      pagination all work server-side over the per-user cache; Refresh re-queries + updates last-updated; revisits are cache-fast.
    - Report #1 download still functions (modal → CSV/Excel, audit row written). 2nd report (if added) appears as a tab and renders with its own column subset + no download.
  </acceptance_criteria>
</task>

---

## Must-Haves
```yaml
truths:
  - Snapshot query selects display ∪ filter columns (filter field projected); renderer emits only display columns.
  - Per-user cache (cache.py) is pure + in-memory LRU, keyed (user_email, report_id, date); only holds the user's own OBO data; refresh evicts+reloads; filter/search/paginate run server-side over the cache (no DB re-query).
  - Generic rendering (render.py) from ColumnSpec (int/pct/text; pct green/red/muted); report #1 reproduced from its config row.
  - Routing: / → first report; /report/{id} full page; /report/{id}/table fragment (+ X-Total-Rows/X-Total-Pages/X-Page/X-Fetched-At headers); registry read as app SP, TTL-cached.
  - Filters default to first distinct value (efile drain=ALL); tabs all shown, no-access renders empty notice.
  - Report #1 keeps its existing download (DOWNLOADABLE_REPORT_IDS gate + _download_efile.html partial); POST /download unchanged (Phase 8 generalizes).
  - No new UC object, no bundle-resource change, no new runtime dependency, no CDN.
artifacts:
  - src/app/cache.py, src/app/render.py (pure)
  - src/app/main.py (config-driven routes + cache + SP read variant)
  - src/app/templates/report.html, _download_efile.html, _rows.html (generic), base.html (tab nav)
  - src/app/static/js/app.js (generalized), src/app/static/css/app.css (tabs + muted-pct)
  - tests/test_cache.py, tests/test_render.py
uc_targets:
  - irs.efile.report_config (READ as app SP) ; each report source_fqn (READ per-user OBO) — no writes, no new tables
```
