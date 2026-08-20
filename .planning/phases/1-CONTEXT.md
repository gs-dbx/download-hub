# Phase 1 Context

**Phase:** Foundation — schema, gold table & synthetic data
**Discussed:** 2026-08-12
**Status:** ready for planning

## Locked Decisions

### Data Architecture
- **Pipeline Type:** Batch synthetic-data generation (no ingestion, no DLT). A Databricks
  Job runs a notebook that builds a Spark DataFrame and writes Delta.
- **Source:** None external — data is synthesized in-notebook.
- **Targets (Unity Catalog):**
  - `irs.efile` — schema (create if not exists)
  - `irs.efile.daily_efile_glance` — gold table displayed by the app
  - `irs.efile.download_audit` — audit table (created here, written by the app in Phase 4)
- **Pattern:** Direct-to-gold. No bronze/silver.

### Gold table content decisions
- **Snapshot scale:** 5–7 representative daily `report_date` values (e.g., a handful of
  business days in early 2026), formatted `YYYY-MM-DD 00:00:00`. Keeps the report selector
  small and demo-friendly.
- **Metrics:** All 17 metrics per (report_date, drain), in fixed `sort_order` (see PROJECT.md
  → Gold table shape for the exact ordered list and `metric_group` values).
- **DRAIN values:** `E`, `M`, `N` as three independent slices, each carrying the full
  17-metric set. **Plus** a materialized `drain = 'ALL'` slice = element-wise sum of E+M+N
  per metric per report_date, with its own precomputed `pct_change`. The app filter offers
  E / M / N / All; selecting a value simply filters `drain`.
- **Columns:** `report_date` TIMESTAMP, `drain` STRING (E|M|N|ALL), `metric_name` STRING,
  `metric_group` STRING (original|amended|combined|pin|other), `sort_order` INT,
  `value_cy` BIGINT (2026), `value_py` BIGINT (2025), `pct_change` DOUBLE (precomputed,
  current vs prior). Values reflect production-realistic IRS e-file magnitudes.
- **Idempotency:** Generator overwrites the gold table on each run (deterministic seed so
  reruns are stable).

### Audit table shape (`irs.efile.download_audit`) — created in Phase 1, written in Phase 4
- Columns: `audit_id` STRING, `event_ts` TIMESTAMP, `user_email` STRING,
  `report_date` TIMESTAMP, `drain_filter` STRING, `search_filter` STRING,
  `row_count` BIGINT, `export_format` STRING (csv|xlsx), **`justification` STRING**,
  `acknowledged` BOOLEAN, `app_version` STRING.
- **Justification storage decision:** the user-entered freetext justification is a single
  `justification` STRING column on the one audit row written per download — NOT a separate
  table or file. One place to answer "who downloaded what, and why."
- Append-only. Created empty in this phase.

### Compute & Infrastructure
- **Compute:** Serverless (job runs on serverless; no cluster/policy).
- **Trigger:** On-demand (`databricks bundle run`), manual. No schedule needed for synthetic seed.
- **Existing Resources:** None to extend. `irs.efile` is new (per WORKSPACE.md). `irs.ocfo`
  and `irs.demo` are separate projects — do not touch.

### Code Structure
- **Task Type:** Notebook task in a DAB-defined Job (`download_hub` bundle).
- **Generation approach:** Spark DataFrame built in the notebook → `.write.format("delta")
  .mode("overwrite").saveAsTable("irs.efile.daily_efile_glance")`. Uses only built-in
  Spark — no external packages (honors air-gap constraint).
- **DDL:** Create schema + audit table via SQL/`spark.sql` in the same or a companion
  notebook cell (audit table via explicit `CREATE TABLE IF NOT EXISTS` with typed columns).
- **Parameters:** Job params for `catalog` (default `irs`) and `schema` (default `efile`)
  so targets are not hardcoded.
- **Shared Utilities:** None yet; the metric list + ordering should live in a single Python
  dict/list in the notebook so Phase 2 can mirror ordering.

### Testing & Quality
- **Unit Tests:** Yes (pytest). Test the pure data-generation function(s) that produce the
  rows: correct 17 metrics present, all DRAIN values incl. `ALL`, `ALL` == sum(E+M+N),
  `pct_change` math correct, `sort_order` complete/unique per (report_date, drain),
  no nulls in key columns. Keep generation logic in an importable pure function so it can be
  unit-tested without Spark.
- **Integration Tests:** No (deferred). Live table verification is a manual/`verify-work`
  step against dev.
- **Data Quality Rules:** value_cy/value_py >= 0; pct_change = round((cy-py)/py*100, 1)
  with py=0 handled (null or 0). ALL slice equals sum of E+M+N.

### Deployment
- **Bundle Target:** dev first (`--target dev`), then staging/prod.
- **Bundle Engine:** use `DATABRICKS_BUNDLE_ENGINE=direct` if any later resource requires it
  (app resource in Phase 2); the seed job alone does not.
- **Run-As:** deploying user (Greg Skinner) for dev.
- **Secrets Scope:** none needed this phase.

### Alerting
- **Failure Notifications:** greg.skinner@databricks.com (dev only; seed job is on-demand).
- **SLA:** none — on-demand synthetic seed; expected to run in well under a minute.

## Open Questions (Deferred)
- Databricks group definitions (`efile_glance_app_users`, `efile_glance_download_users`)
  and their BEARS 1:1 mapping → Phase 4/5.
- OBO / user-authorization wiring and app SP write grant on `download_audit` → Phase 2/4.
- Whether `report_date` values should be a fixed hand-picked list vs. generated relative to
  a run date → planner to pick concrete dates (early-2026 business days) unless told otherwise.

## Workspace Scan Summary (if performed)
- **Skipped** — Databricks CLI OAuth token for profile DEFAULT was expired at discussion time
  (`databricks auth login --profile DEFAULT` needed). No live scan performed.
- Expectation from WORKSPACE.md: `irs.efile` does not yet exist; warehouse
  `2f225c0740dcd22b` (Serverless Starter) is the shared SQL warehouse. Planner should verify
  live before creating, once auth is refreshed.
