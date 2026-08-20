# Phase 3 Context

**Phase:** Interactivity — search, report selector, DRAIN filter
**Discussed:** 2026-08-13
**Status:** ready for planning

## Locked Decisions

### Component Type
- Enhancement to the existing Databricks App `download-hub` (server-rendered FastAPI + Jinja2 +
  vendored USWDS + vanilla JS). No pipeline, no job, no new UC objects.

### Data-loading strategy — SERVER ROUND-TRIP per report-date/DRAIN
- The `/` route stays as Phase 2: renders the default snapshot server-side (latest `report_date`,
  `drain='ALL'`) via the user's OBO token.
- **Add a FastAPI endpoint** (e.g. `GET /table?report_date=...&drain=...`) that:
  - extracts the OBO token (reuse `auth.extract_user_token`),
  - runs a fresh OBO query for the requested `(report_date, drain)`,
  - returns a **server-rendered HTML table-body fragment** (a Jinja partial, e.g. `_rows.html`),
    NOT JSON. This keeps all number/pct formatting in Python (`shaping.py`) and the client JS tiny.
- The report-date `<select>` and DRAIN `<select>` fire this fetch on change; JS swaps the returned
  fragment into the table body (`innerHTML`).
- **Search stays client-side:** filters the currently-displayed rows in-browser (no server call).

### Filter behavior
- **Combine (AND) + preserve.** The server fetch always sends BOTH the current report_date AND the
  current drain (AND in the SQL WHERE). Changing one control preserves the other's selection.
- After a fetch replaces the table body, the client **re-applies the current search text** to the
  new rows (so search survives report-date/DRAIN changes).
- **Search scope:** case-insensitive substring match on **metric name only** (the row label).

### UC targets & query
- Read-only from `irs.efile.daily_efile_glance` via OBO (same as Phase 2). No writes.
- New query shape: parameterized by a SPECIFIC report_date (not just MAX):
  `... WHERE report_date = :report_date AND drain = :drain ORDER BY sort_order`.
  - `report_date` and `drain` are **validated against the known allowed sets** (the distinct
    report_dates fetched at page load; drain ∈ {E,M,N,ALL}) and passed as **SQL parameters**
    (Statement Execution `parameters=[...]`) — never string-interpolated (no injection).
  - DRAIN options remain the fixed enum {ALL, E, M, N}.

### Code structure (extend Phase 2 modules)
- `queries.py`: add/adjust a builder for the specific-report_date + drain query (parameterized).
  Keep the Phase 2 "latest + ALL" builder for the initial `/` render.
- `shaping.py`: reuse `rows_to_context` / formatting unchanged.
- `templates/`: extract the table rows into a partial `_rows.html`; `glance.html` includes it for
  the initial render, and the `/table` endpoint renders `_rows.html` alone for fragment swaps.
- `main.py`: add the `/table` route (OBO, `asyncio.to_thread`, `auth_type="pat"` per
  [[reference_databricks_apps_obo_auth]]).
- `static/`: a small `app.js` (vanilla) — wire the two selects to fetch `/table` and swap the body;
  wire the search box to show/hide rows by metric name. No framework, no npm.

### Compute & Infrastructure
- App serverless runtime (unchanged). Warehouse `2f225c0740dcd22b` (unchanged, CAN_USE).
- **Existing resources:** reuse `resources/app.yml` (the `download-hub` app) — no new resource.
  The Phase 1 `efile_seed` job untouched.

### Testing & Quality
- **Unit (pytest):** the parameterized query builder (correct 3-level FQN, `:report_date`/`:drain`
  params, ORDER BY sort_order), and the validation helpers (report_date/drain accepted only when in
  the allowed set; rejected otherwise). Reuse existing shaping tests. No pyspark/network in tests.
- **JS filtering** (search show/hide, select→fetch→swap, search re-apply): **verified manually** at
  the checkpoint (no JS unit harness — air-gap, no npm). Keep JS minimal and behavior-obvious.
- No new runtime dependency (no openpyxl yet — Phase 4).

### Deployment
- Redeploy the app via DAB standard engine (`databricks bundle deploy --target dev`) + restart the
  app (`databricks bundle run download_hub`). Note: app deploys are slow (fresh venv build).
- Run-as deploying user (Greg) for dev.

### Alerting
- greg.skinner@databricks.com (dev). Render/fetch target ~1s (NFR-3).

## Open Questions (Deferred)
- Whether the `/table` fetch should show a lightweight loading indicator during the round-trip →
  planner's discretion (nice-to-have; keep minimal).
- Download button + acknowledgement + justification + audit + group gating + CSV/Excel → Phase 4
  (the download will export the CURRENT filtered view; audit logs drain_filter + search_filter).

## Workspace Scan Summary
- No live scan needed — Phase 3 changes are app-side only. Confirmed context from Phase 2:
  `download-hub` app is deployed and ACTIVE on dev, renders via OBO; gold table has 6 report_dates
  and drains E/M/N/ALL to drive the selectors.
