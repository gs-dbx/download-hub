# PROJECT.md — Databricks Project Context

<!-- This file is the authoritative source of project intent. All agents read it. -->

## Project Overview

**Name:** download_hub ("Daily E-File at a Glance")
**Type:** Databricks App (server-rendered FastAPI) + synthetic gold data pipeline
**Workspace:** See `.planning/WORKSPACE.md`
**Created:** 2026-08-12
**Owner:** Greg Skinner

## What We're Building

A Databricks App for the IRS that displays daily e-file summary statistics in a clean,
GSA/USWDS-styled tabular UI ("Daily E-File at a Glance"). Users authenticate with
on-behalf-of-user (OBO) authorization so their existing Unity Catalog access to the
underlying data is honored. The app reads a synthetic gold table and lets authorized
users download the displayed data (CSV or Excel) only after acknowledging a data-handling
warning banner and entering a written justification — both of which are logged to a
dedicated audit table and the app logs. Download is gated by Databricks group membership
that maps 1:1 to a BEARS entitlement.

## Data Architecture

```
Source(s):         Synthetic seed generator (this repo) — no external ingestion
Pipeline Type:     Batch (synthetic data generation → Delta gold table)
Target Table(s):   irs.efile.daily_efile_glance  (gold, displayed by app)
                   irs.efile.download_audit       (app-written audit log)
Medallion Layers:  Direct-to-gold (synthetic; no bronze/silver for this project)
```

### Gold table shape (`irs.efile.daily_efile_glance`)

One row per (report_date, drain, metric). Columns:

| Column         | Type      | Notes |
|----------------|-----------|-------|
| report_date    | TIMESTAMP | Daily run, displayed as `2026-01-08 00:00:00`; drives the report selector |
| drain          | STRING    | One of `E`, `M`, `N`; drives the DRAIN filter |
| metric_name    | STRING    | Display label for the row (see metric list below) |
| metric_group   | STRING    | `original` \| `amended` \| `combined` \| `pin` \| `other` (ordering/grouping) |
| sort_order     | INT       | Fixed display order of rows |
| value_cy       | BIGINT    | Current-year value (2026 column) |
| value_py       | BIGINT    | Prior-year value (2025 column) |
| pct_change     | DOUBLE    | % change, current vs prior (stored, precomputed) |

**Displayed rows (metric_name, in order):** PY Filed In 2026; ERO Accepted (original);
Online Accepted (original); Total Accepted (original); ERO Accepted (amended);
Online Accepted (amended); Total Accepted (amended); ERO Accepted (combined);
Online Accepted (combined); Total Accepted (combined); Balance Due;
ERO Self Select PIN Return Vol; ERO PIN Return Vol; Online PIN Return Vol; ERO; Online;
ERO Self-Select PIN Total.

### Audit table shape (`irs.efile.download_audit`)

Captures only what Databricks does NOT natively log — i.e., that a download occurred.

| Column           | Type      | Notes |
|------------------|-----------|-------|
| audit_id         | STRING    | UUID |
| event_ts         | TIMESTAMP | When the download was requested |
| user_email       | STRING    | Signed-in user (from OBO identity) |
| report_date      | TIMESTAMP | Which report snapshot was downloaded |
| drain_filter     | STRING    | DRAIN filter value(s) applied at download time |
| search_filter    | STRING    | Free-text search filter applied at download time |
| row_count        | BIGINT    | Rows in the exported file |
| export_format    | STRING    | `csv` \| `xlsx` |
| justification    | STRING    | User-entered justification (required) |
| acknowledged     | BOOLEAN   | Banner acknowledgement (must be true) |
| app_version      | STRING    | App build/version for traceability |

## Primary Stakeholders

- **Consumer:** IRS analysts/leadership viewing daily e-file volumes; a subset with the
  download entitlement export the data.
- **Data Owner:** IRS (synthetic stand-in for real e-file processing statistics).
- **Technical Owner:** Greg Skinner / Field Engineering.

## Technology Choices

<!-- Decisions already made — agents must respect these and not re-ask -->

| Concern | Choice | Reason |
|---------|--------|--------|
| App architecture | Server-rendered FastAPI + Jinja2 + vendored USWDS + vanilla JS | Meets all download/audit/branding reqs with minimal deps and NO npm build chain — easiest to reproduce in an air-gapped environment |
| Front-end framework | None (server-rendered HTML) | Real-time search/DRAIN/report filters run client-side in vanilla JS on loaded data; avoids React/node toolchain |
| Design system | USWDS (GSA U.S. Web Design System), vendored as static CSS/JS | Federal look/feel; no CDN — assets committed to repo for offline operation |
| Auth | Databricks Apps OBO (user authorization) | Underlying UC data access of the signed-in user is honored automatically |
| Download gating | Databricks group membership (`efile_glance_download_users`) | Maps 1:1 to a BEARS entitlement; simple, auditable, works today (ABAC deferred) |
| Data access | Query gold table as the user via SQL warehouse (OBO) | If user lacks SELECT they see nothing — access enforced by UC, not app logic |
| Audit writes | App service principal writes `download_audit` | Only non-natively-logged events (a download occurred) are recorded |
| Export formats | CSV (stdlib) + Excel (openpyxl) | openpyxl is pure-Python and vendors cleanly offline |
| Synthetic data | Generated by this repo into `irs.efile.daily_efile_glance` | No external source; realistic IRS magnitudes |
| Compute | Serverless (Apps runtime + serverless SQL warehouse) | No cluster management; shared warehouse `2f225c0740dcd22b` |
| Deployment | Databricks Asset Bundle (`download_hub`) | IaC; consistent dev/staging/prod |
| Testing | pytest (unit) for data-shaping, filters, audit, permission logic | Air-gapped-friendly; no live-cluster dependency in unit tests |
| Unity Catalog | Enabled (`irs.efile`) | Governs data access and OBO enforcement |

## Applied AI Dev Kit Skills

<!-- Skills that apply to this project — agents should always read these -->
- `databricks-python-sdk` — SDK patterns for all Python code (auth, SQL, groups, apps)
- `python-dev` — Python quality standards
- `databricks-apps` (fe-databricks-tools) — app.yaml, OBO/user-authorization, resources, deploy
- `asset-bundles` — DAB structure for the app + jobs + schema
- `databricks-data-generation` — synthetic gold-table seed generation
- `databricks-authentication` — OAuth / OBO auth flows

## Constraints

<!-- Hard limits agents must never violate -->
- **Offline / air-gapped target:** The runtime environment has NO access to public Python
  or npm repositories. Every dependency must be vendorable/committed. Prefer stdlib; add
  packages only when necessary and record them so they can be pre-staged.
- **Minimal dependencies:** Keep the dependency surface as small as possible.
- **Compute Policy:** Serverless only; shared warehouse `2f225c0740dcd22b`.
- **Data classification:** Synthetic data only — but the app must behave as if data is
  sensitive (download warning banner, justification, audit trail).
- **Download enforcement:** A user must have BOTH UC SELECT on the gold table (via OBO)
  AND membership in the download group before the download option is even presented.
- **Audit minimalism:** Log only events Databricks does not natively capture (the fact
  that a download occurred, with its justification/acknowledgement).
- **GovCloud:** Workspace is Databricks on AWS GovCloud.

## Out of Scope

<!-- Explicit exclusions to prevent scope creep -->
- Additional tabs beyond "Daily E-File at a Glance" (single tab for this project).
- Real e-file data ingestion / bronze/silver pipelines (synthetic gold only).
- React/SPA front end (explicitly rejected in favor of server-rendered).
- Full ABAC implementation (group-based gating now; ABAC noted as future option).
- Live BEARS integration (we model the entitlement as a Databricks group 1:1).
- Write-back / editing of the displayed data.

## Success Definition

<!-- How do we know this project is done? -->
- [ ] `irs.efile.daily_efile_glance` is populated with synthetic data across 2026 & 2025,
      multiple daily `report_date` snapshots, and all three DRAIN values.
- [ ] App deploys via DAB and renders the "Daily E-File at a Glance" table in USWDS styling.
- [ ] Real-time search filter, report-date selector, and DRAIN (E/M/N) filter all work
      client-side and correctly trim the displayed table.
- [ ] A user without UC SELECT on the gold table sees no data (OBO enforced).
- [ ] The download option appears ONLY for users in the download group; others never see it.
- [ ] Download flow requires banner acknowledgement + justification, writes one
      `download_audit` row and an app-log line, and produces a CSV/Excel file whose top
      carries the acknowledged disclaimer text.
- [ ] Runs with only vendored/pre-staged dependencies (no internet package access).
- [ ] Basic documentation (README + run/deploy guide + permission model notes) exists.

## Evolution Rules

- New requirements → add to REQUIREMENTS.md, do NOT edit this file
- Architecture changes → update Technology Choices table and note the reason
- Never delete constraints — mark as "lifted: <date>" if removed
