# Roadmap

Project: **download_hub — "Daily E-File at a Glance"**
Status: draft for review

Phases are ordered so each builds on a working, testable predecessor. Data first,
then read-only app, then the gated download flow, then packaging for the air-gapped target.

---

## Phase 1 — Foundation: schema, gold table & synthetic data
**Goal:** `irs.efile` exists with a populated, realistic gold table and empty audit table.
- Create `irs.efile` schema; create `daily_efile_glance` (gold) and `download_audit` tables.
- Build the synthetic data generator: 17 metrics × N daily `report_date` snapshots ×
  DRAIN {E,M,N}, with 2026/2025 values and precomputed `pct_change`.
- DAB scaffold (`databricks.yml`) with bundle `download_hub`, dev/staging/prod targets.
- Unit tests for generator output shape and value sanity.
**Maps to:** FR-1, plus schema for FR-9. **Verify:** table row counts, distinct
report_dates, all DRAIN values, all 17 metrics present.

## Phase 2 — App skeleton: FastAPI + USWDS + OBO read
**Goal:** A deployed Databricks App that renders the table for the signed-in user via OBO.
- FastAPI app, Jinja2 templates, vendored USWDS static assets, `app.yaml`.
- OBO/user-authorization wired so the gold table is queried as the signed-in user.
- Render the 17-row table (2026 / 2025 / % change) for a default report_date.
- DAB app resource + serverless SQL warehouse binding; deploy to dev.
- Unit tests for row ordering, % change formatting, and query-as-user path.
**Maps to:** FR-2, FR-6, NFR-6. **Verify:** app loads in dev; a user without SELECT sees
no data.

## Phase 3 — Interactivity: search, report selector, DRAIN filter
**Goal:** All three client-side filters trim the table correctly and fast.
- Vanilla-JS real-time search filter over visible rows.
- Report-date selector (formatted `YYYY-MM-DD 00:00:00`) that loads the chosen snapshot.
- DRAIN E/M/N filter.
- Tests for filter logic (unit-level for any server support; documented manual checks for JS).
**Maps to:** FR-3, FR-4, FR-5, NFR-3.

## Phase 4 — Gated download with acknowledgement, justification & audit
**Goal:** Only entitled users can download, and every download is acknowledged, justified,
and audited.
- Group-membership check (`efile_glance_download_users`) → conditionally show download UI.
- Acknowledgement banner + mandatory justification input; block download until both satisfied.
- CSV (stdlib) and Excel (openpyxl) export, each with the acknowledged disclaimer at top.
- Write one `download_audit` row per download + app-log line; handle audit-write failure.
- Tests for permission gating, audit-row contents, disclaimer presence, format correctness.
**Maps to:** FR-7, FR-8, FR-9, FR-10, FR-11, NFR-4, NFR-5.

## Phase 5 — Air-gap packaging, groups & documentation
**Goal:** Reproducible in the offline target environment, with docs and the group/BEARS mapping.
- Pin & vendor/pre-stage all Python deps; document the offline install procedure.
- Confirm USWDS assets fully committed (no CDN references anywhere).
- Define and document the two Databricks groups and their 1:1 BEARS entitlement mapping.
- README, run/deploy guide, permission-model note, offline-dependency note.
- End-to-end verify on staging.
**Maps to:** FR-11, FR-12, NFR-1, NFR-2. **Verify:** clean deploy with no network fetches.

---

### Notes
- ABAC is intentionally deferred; the permission check in Phase 4 is structured so an
  attribute/entitlement source could replace group lookup later without a UI rewrite.
- No bronze/silver layers — synthetic data is generated directly into gold.

---

# Milestone 2 — Configurable multi-report portal

Turns the single hardcoded page into a config-driven, multi-tab reporting portal.
Locked decisions (2026-08-13):
- **Config:** UC config table `irs.efile.report_config` — one row per report:
  source view/table (3-level), display columns (+labels/format), filter field(s),
  date field, title (→ tab name). Add a report by inserting a row (no redeploy).
  The tab registry is read by the app service principal (app metadata, not user data).
- **Access:** per-user **OBO** reads of each report's source (UC-enforced per user, as
  Milestone 1). NO shared cache. A user sees all configured tabs; a tab with no
  underlying access renders empty/"no access".
- **Cache:** **per-user session cache** — query the report once as the user, hold the
  full result for their session, and serve filter/paginate/download from it. Refresh
  re-runs the query and updates that user's cache; show the user's last-query time.
- **Pagination:** row-count selector (e.g. 25/50/100/All) for DISPLAY only.
- **Filtering:** on the cached full dataset (app-side), not client-only, not per-page.
- **Download:** full matching set (current filters applied, all pages), gated by the
  single global download group; reuse the acknowledgement + justification + audit flow.
  Audit gains a report identifier (which report was exported).
- E-File at a Glance = configured report #1.

## Phase 6 — Report config layer + generic OBO data access
- Create `irs.efile.report_config` (+ seed row for E-File at a Glance).
- Config loader (read registry as app SP); a generic per-user OBO query builder for an
  arbitrary report (selected columns, date field, filter predicates), parameterized.
- Pure, unit-tested config model + query builder. No UI change yet.

## Phase 7 — Multi-tab portal (render + per-user cache + refresh + filters + pagination)
- URL-per-report routing (`/report/{id}`) + tab nav (registry read as app SP); generic table
  rendering from `columns_json` (int/pct/text formats); retire the efile-specific path.
- Per-user cache keyed by (user, report, selected date); refresh reloads + re-stamps; show
  last-query time per tab.
- Config-driven date selector + equality filter dropdowns (distinct values, OBO, scoped to date)
  + substring search + row-count pagination — all server-side over the cached snapshot via a
  generic per-report table-fragment endpoint. Show all tabs; graceful empty on no-access.
- (Absorbed the originally-separate filters/pagination phase — they're entangled with caching +
  generic rendering.) Report #1 keeps its existing download until Phase 8.

## Phase 8 — Download generalization + docs
- Generalize download to ANY report: export the full filtered set from the per-user cache; reuse
  the acknowledgement + justification + audit-first flow; extend `download_audit` with a report id;
  single global download group; retire the report-#1-specific `/download`/`queries.py` path.
- Update README/DEPLOY/PERMISSIONS/OFFLINE + a config-table authoring guide (how to add a report).
