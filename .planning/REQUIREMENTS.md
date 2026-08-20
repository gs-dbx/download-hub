# Requirements

Project: **download_hub — "Daily E-File at a Glance"**
Status: draft for review

## Functional

**FR-1 — Synthetic gold table.** Generate `irs.efile.daily_efile_glance` with all 17
displayed metrics, current-year (2026) and prior-year (2025) values, precomputed
`pct_change`, across multiple daily `report_date` snapshots and all three DRAIN values
(`E`, `M`, `N`). Values reflect production-realistic IRS e-file magnitudes.

**FR-2 — App renders the table.** A Databricks App displays "Daily E-File at a Glance"
as a clean USWDS-styled table: rows = the 17 metrics in fixed order; columns = 2026,
2025, and % change (with directional formatting).

**FR-3 — Real-time search filter.** A text input trims the visible table rows in real
time (client-side) as the user types, without a server round-trip.

**FR-4 — Report selector.** A control lists all available `report_date` snapshots
formatted as `2026-01-08 00:00:00`; selecting one loads that snapshot's data.

**FR-5 — DRAIN filter.** A DRAIN control offers `E`, `M`, `N` and filters the table to
the selected value(s).

**FR-6 — OBO data access.** The app queries the gold table on behalf of the signed-in
user; a user without UC SELECT on `irs.efile.daily_efile_glance` sees no data. No app-side
access shortcut bypasses Unity Catalog.

**FR-7 — Group-gated download.** The download option is presented ONLY to users who are
members of the download group (`efile_glance_download_users`) AND can read the table.
Users lacking the entitlement never see the download control.

**FR-8 — Download acknowledgement + justification.** Initiating a download requires the
user to (a) acknowledge a data-handling warning banner and (b) enter a free-text
justification. Both are mandatory before the file is produced.

**FR-9 — Audit logging.** Each download writes exactly one row to `irs.efile.download_audit`
(user, timestamp, report_date, filters applied, row count, format, justification,
acknowledgement, app version) and emits a corresponding app-log line. Only
non-natively-logged events are recorded (the fact that a download occurred).

**FR-10 — Export formats with disclaimer.** Downloads are available as CSV and Excel
(.xlsx). Each exported file carries, at the top, the disclaimer text the user just
acknowledged.

**FR-11 — Permission model tied to BEARS.** App access and download entitlement are each
backed by a Databricks group that maps 1:1 to a BEARS entitlement
(`efile_glance_app_users`, `efile_glance_download_users`), documented for the target env.

**FR-12 — Documentation.** Provide a README, a run/deploy guide, a permission-model note,
and an offline-dependency (vendoring) note.

## Non-Functional

**NFR-1 — Offline operation.** The app and data generator run with only vendored /
pre-staged dependencies; no runtime access to public Python or npm repositories. USWDS
assets are committed to the repo. Dependency list is documented for pre-staging.

**NFR-2 — Minimal dependency surface.** Runtime Python deps limited to
`fastapi`, `uvicorn`, `jinja2`, `databricks-sdk`, `openpyxl` (plus their transitive deps).
No front-end build toolchain (no node/npm).

**NFR-3 — Performance.** Table renders and client-side filters respond within ~1s for a
single report_date snapshot (all DRAIN values). Downloads stream without blocking the UI.

**NFR-4 — Governance.** Access enforced by Unity Catalog (OBO) for data and by Databricks
groups for download. Audit table is append-only; app SP has write on audit table only.

**NFR-5 — Reliability.** Deploys reproducibly via DAB to dev/staging/prod. Audit write
failure blocks/flags the download rather than silently dropping the record.

**NFR-6 — Federal design compliance.** UI follows USWDS component and accessibility
conventions (semantic markup, keyboard-navigable controls, sufficient contrast).

## Out of Scope

- Additional tabs beyond "Daily E-File at a Glance".
- Real e-file ingestion or bronze/silver layers (synthetic gold only).
- React/SPA front end.
- Full ABAC (group-based gating now; ABAC is a documented future option).
- Live BEARS API integration (entitlement modeled as a Databricks group).
- Editing/write-back of displayed data.
