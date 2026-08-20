# Phase 2 Context

**Phase:** App skeleton — FastAPI + USWDS + OBO read
**Discussed:** 2026-08-12
**Status:** ready for planning

## Locked Decisions

### Data Architecture
- **Component Type:** Databricks App (server-rendered FastAPI + Jinja2 + vendored USWDS +
  vanilla JS). Not a pipeline/job. Read-only in this phase.
- **Input (read):** `irs.efile.daily_efile_glance` (gold table created in Phase 1) — queried
  via SQL on warehouse `2f225c0740dcd22b`.
- **Output:** An HTTP UI page ("Daily E-File at a Glance") rendering the 17-metric table
  (columns: 2026 `value_cy`, 2025 `value_py`, `pct_change`).
- **No writes this phase.** The `download_audit` write path is Phase 4.

### OBO / Auth (query as the signed-in user)
- **Query mechanism:** **databricks-sdk Statement Execution API** (NOT databricks-sql-connector).
  Forward the user's `x-forwarded-access-token` into a `WorkspaceClient` and run SQL on the
  warehouse. No new dependency; lightest air-gap footprint. (Same method used to verify Phase 1.)
- **User authorization:** app runs with user authorization enabled, scope `sql` (per WORKSPACE.md).
  Data is always read AS THE USER — a user without UC SELECT on the gold table sees no data
  (satisfies FR-6). No app-service-principal fallback for data reads.
- **Local dev:** **Apps-only — NO local run.** The app always requires the OBO header; if
  `x-forwarded-access-token` is absent, respond with a clear error (do NOT fall back to a CLI
  profile or mock data). Devloop = deploy to dev Apps and test there.

### UI / USWDS
- **Design system:** vendor a **compiled USWDS 3.x dist subset** — `uswds.min.css`,
  `uswds.min.js`, `uswds-init.min.js`, required fonts, and the img sprite. No Sass build, no
  npm, no CDN. Assets fetched once now (dev machine has internet) and committed to the repo for
  offline operation. Reference them as local static files only.
- **Initial render (this phase):** most recent `report_date`, `drain='ALL'`, STATIC (no working
  filters yet). ALSO pass the full list of available `report_date` values and the DRAIN options
  (E/M/N/ALL) into the template — rendered but inert — so Phase 3 only has to wire up the
  client-side JS. Rows ordered by `sort_order` (1..17), grouped per `metric_group`.
- **No download UI this phase** (Phase 4).

### Compute & Infrastructure
- **App compute:** Databricks Apps serverless runtime (managed by the Apps platform).
- **Resource binding:** SQL warehouse `2f225c0740dcd22b` bound to the app as a resource.
- **App name:** `download-hub` (confirmed absent in workspace; created fresh). Workspace Apps
  are live and enabled (GovCloud `aws-gov.databricksapps.us`).
- **Existing resources:** none to extend. Phase 1's `efile_seed` job stays as-is.

### Code Structure
- **Framework:** FastAPI served by uvicorn; Jinja2 templates; static USWDS assets.
- **Runtime deps (this phase):** `fastapi`, `uvicorn`, `jinja2`, `databricks-sdk`. (openpyxl is
  Phase 4.) All must be vendorable/pre-stageable for air-gap.
- **app.yaml:** defines the run command (uvicorn) and declares the SQL warehouse resource +
  user-authorization config.
- **Separation for testability:** keep pure functions separate from I/O —
  (a) a query builder that produces the SQL for (report_date, drain); (b) a row-shaping function
  that turns query rows into ordered template context (mirrors Phase 1 METRICS ordering);
  (c) an OBO token-extraction helper (reads the header). The SDK call itself is the only
  side-effecting boundary.
- **Metric ordering source of truth:** reuse/mirror the canonical `METRICS` order from
  `src/efile_glance/generator.py` so UI order == data order.

### Testing & Quality
- **Unit Tests (pytest):** yes — cover the query builder (correct SQL/params, 3-level table
  name, parameterized report_date/drain), the row-shaping/context function (17 rows, correct
  order, pct_change formatting incl. NULL handling and +/- direction), and the token-extraction
  helper (present → token; absent → error). No live SQL, no pyspark, no network in unit tests.
- **Integration:** deploy to dev Apps and load the page (checkpoint) — no local integration test.
- **Data quality:** N/A (read-only); rendering must handle NULL `pct_change` gracefully.

### Deployment
- **Method:** DAB app resource added to `databricks.yml` (via `resources/*.yml`).
- **Bundle engine:** verify whether the Apps resource requires `DATABRICKS_BUNDLE_ENGINE=direct`
  (PROJECT.md flagged this as possible); planner/researcher to confirm against the live CLI.
- **Target:** dev first (`--target dev`), then staging/prod.
- **Run-As:** deploying user (Greg Skinner) for dev; app gets its own service principal identity
  on deploy (used for app compute, not for OBO data reads).

### Alerting
- **Failure Notifications:** greg.skinner@databricks.com (dev).
- **SLA:** page render + query for one snapshot within ~1s (NFR-3); no hard SLA in dev.

## Open Questions (Deferred)
- Exact USWDS 3.x patch version + precise file list to vendor → planner to pin during research
  (fetch latest stable 3.x compiled release).
- Whether the Apps resource needs `DATABRICKS_BUNDLE_ENGINE=direct` → confirm in research.
- Client-side search / report-date / DRAIN filter behavior → Phase 3.
- Download button, acknowledgement banner, justification, audit write, group gating → Phase 4.
- Applying the `efile_glance_app_users` SELECT grant + app-SP considerations → Phase 4.

## Workspace Scan Summary
- **Live CLI scan performed 2026-08-12 (auth valid).**
- Databricks Apps ENABLED; 9 apps exist on `*.aws-gov.databricksapps.us`. No `download-hub`
  app present → create fresh.
- Warehouse `2f225c0740dcd22b` HEALTHY (used to verify Phase 1).
- `irs.efile.daily_efile_glance` exists with 408 rows / 4 drains (Phase 1) — ready to read.
- `databricks apps` CLI subcommand is available for deploy/verify.
