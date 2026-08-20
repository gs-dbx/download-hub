# Phase 7 Context

**Phase:** Multi-tab portal — generic render + per-user cache + refresh + filters + pagination
**Discussed:** 2026-08-13
**Status:** ready for planning

Phase 7 turns the single hardcoded page into a **display-complete config-driven portal**. It
absorbs the originally-separate "filters + pagination" phase (they're entangled with caching +
generic rendering). Download generalization moves to Phase 8.

## Locked Decisions

### Component type & scope
- App-side generalization of `download-hub` (FastAPI + Jinja2 + USWDS + vanilla JS). No pipeline.
- Delivers, for ALL configured reports: URL-per-report routing + tab nav, generic table rendering
  from config, per-user cache, refresh, last-query time, a config-driven date selector, equality
  filter dropdowns, substring search, and row-count pagination.
- **Retires the efile-specific rendering path** — report #1 renders via the generic config path.

### Routing & tabs
- **URL per report:** `/report/{report_id}` server-renders that report. A nav bar lists enabled
  reports (ordered by `display_order`), active tab highlighted. `/` redirects to the first enabled
  report. Bookmarkable; full-page load on tab switch (cheap; per-user cache makes revisits fast).
- The report registry is read by the **app SP** (`reports.build_report_config_query` via a plain
  `WorkspaceClient()`), parsed with `reports.parse_report_config`.

### Per-user cache (the core mechanic)
- **Key:** `(user_email, report_id, selected_date)` → `{columns, rows, fetched_at}`. `user_email`
  from `X-Forwarded-User`. In-memory (app process), bounded (LRU + size cap; optional TTL). Per-user
  so it only ever holds that user's own OBO-authorized data (no cross-user leak). Multi-replica note:
  a cache miss on another replica just re-queries — correctness unaffected.
- **Load:** on a report page, resolve the selected date (default = latest via
  `build_report_dates_query`); if the snapshot isn't cached, run `build_report_query` **as the user
  (OBO)** for that date (all configured columns, no filters → the full snapshot), cache it with
  `fetched_at = now`.
- **Serve:** search / equality filters / pagination all operate **server-side over the cached
  snapshot** (in-memory, no DB re-query). Download (Phase 8) exports the full filtered set from cache.
- **Refresh:** a refresh control evicts + reloads the current (user, report, date) snapshot and
  re-stamps `fetched_at`. Show `fetched_at` ("Last updated …") per report page.

### Rendering (generic, from ColumnSpec)
- Columns, headers, and order come from `columns_json` (ColumnSpec name/label/format). Formats:
  `int` → thousands-separated, `pct` → signed + 1-decimal + green/red/muted coloring (reuses the
  Milestone-1 look), `text` → plain; unknown → text. Report #1's CY/PY/%-change is reproduced purely
  from its config row.

### Filters, date, search, pagination (config-driven, server-side over cache)
- **Date selector:** distinct values of `date_field` (newest first); selecting one loads/caches that
  snapshot. **Filters:** one single-select dropdown per `filters_json` entry, options = distinct
  values of that field (via `build_distinct_values_query` as the user, scoped to the selected date);
  equality, combine with AND. **Search:** case-insensitive substring across the displayed columns'
  rendered text. **Pagination:** row-count selector (25 / 50 / 100 / All) for display.
- A generic per-report **table-fragment endpoint** (e.g. `GET /report/{id}/table?date=&<filters>=&q=&
  page=&size=`) reads the cached snapshot, applies filters+search, paginates, and returns the rendered
  `<tr>` rows; vanilla JS wires the controls to it (generalizes the Milestone-1 `/table` pattern).
  Injection-safe: identifiers come from validated config, values are bound/compared in-app.

### Access / tab visibility
- **Show all enabled tabs to everyone**; a tab whose source the user can't read (OBO) renders empty
  with a clear "no data / you may not have access" notice. UC still fully enforces access (they get
  no rows). No per-tab access probing.

### Download during Phase 7 (interim)
- Report #1 **keeps its existing working download** (unchanged flow). The download button shows only
  on reports flagged downloadable (report #1 now). **Generic download for any report (export the
  filtered cached set + audit with report_id) = Phase 8.** `queries.py` + the current `/download`
  stay until Phase 8 generalizes them.

### UC targets
- Reads only: `irs.efile.report_config` (app SP) + each report's `source_fqn` (per-user OBO). No new
  tables, no new bundle resource.

### Testing
- Pure pytest (offline): cache key + eviction/LRU logic; the pure filter/search/paginate functions
  over an in-memory rowset (equality AND, substring across columns, page slicing, "All"); the render
  context builder (ColumnSpec → formatted cells incl. NULL/pct/int); report selection/ordering from a
  parsed config list. SDK/OBO reads (me(), OBO query, SP registry read) are the I/O boundary — verified
  at the live checkpoint (browser), not unit-tested.

### Deployment
- App redeploy (standard engine) + restart. Serverless. Checkpoint on dev.
- Optional verification aid: add a 2nd demo `report_config` row (e.g. same gold source, different
  column subset/title) to visibly prove multi-tab + per-tab cache/filters, then leave or remove it.

## Open Questions (Deferred)
- Generic download for any report + `download_audit` report_id column → Phase 8.
- Config-table authoring UX / admin UI → future (rows added via MERGE/INSERT for now).
- Cross-replica shared cache → out of scope (per-user per-process is sufficient; miss = re-query).

## Workspace Scan Summary
- `irs.efile.report_config` live with report #1 (Phase 6); `daily_efile_glance` populated; app SP has
  SELECT on report_config. App `download-hub` ACTIVE. Warehouse `2f225c0740dcd22b` healthy.
