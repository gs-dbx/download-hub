# Phase 4 Execution Summary

**Date:** 2026-08-13
**Status:** COMPLETE (code + groups + SP grants + deploy + app up & healthy) — browser download click-through pending user
**Branch:** dbx/download-hub-phase-1

## Completed Tasks
| Task | Wave | Commit | Status |
|------|------|--------|--------|
| Pure exports.py (CSV/XLSX + DISCLAIMER) + audit.py | 1 | d320956 | PASS |
| auth.py email + group-membership helpers | 2 | 302c0f4 | PASS |
| main.py can_download on / + POST /download + SP client | 3 | 5d02742 | PASS |
| glance.html download panel + app.js hidden-field sync | 4 | f2e0899 | PASS |
| tests for exports + audit; extend auth tests | 5 | 7025ab7 | PASS |
| requirements (+openpyxl, +python-multipart) + grants.sql | 6 | 0640a84 | PASS |
| Checkpoint: groups, grants, deploy, verify | 7 | — | IN PROGRESS (see below) |

## Test Results
- Unit tests: **66 passed, 1 skipped** (XLSX test skips — openpyxl absent from dev venv, by design).
- Bundle validate: PASS. Anti-patterns: none.

## Checkpoint (live, dev) — progress
- Groups CREATED: `efile_glance_app_users` (id 2120470953002429), `efile_glance_download_users`
  (id 2123868542399307). greg.skinner (75113935367499) ADDED to the download group (verified members).
- Grants applied via Statement Execution API:
  - **SP appId `97898a88-...` grants: ALL SUCCEEDED** — USE CATALOG irs, USE SCHEMA irs.efile,
    MODIFY + SELECT on irs.efile.download_audit. (Audit writes are unblocked.)
  - **`efile_glance_app_users` grants: FAILED (PRINCIPAL_DOES_NOT_EXIST).** This GovCloud workspace
    uses account-level identity federation for UC; the SCIM-created workspace-local group is not
    resolvable by UC. NON-BLOCKING for the demo: (a) download-group membership check uses the app's
    me() SCIM call (works; Greg is a member); (b) Greg OWNS the tables so his OBO read succeeds;
    (c) the app-users UC grant only matters for OTHER non-owner readers.
    **Follow-up for Greg (account admin):** create/federate `efile_glance_app_users` at ACCOUNT level,
    then re-run the 3 app-users GRANTs in resources/grants.sql. Same class as the Phase-1 deferral.
- Deploy: `bundle deploy --target dev` SUCCESS. App restart SUCCEEDED — deployment SUCCEEDED,
  uvicorn running, python-multipart 0.0.30 installed, no startup errors, 302 (SSO) on GET /.
  App is healthy and serving the Phase 4 code. download_audit still 0 rows (no download performed yet).

## Remaining (needs SSO browser — user)
- As greg.skinner (now in the download group): confirm the download panel appears; acknowledge +
  justification + choose CSV then Excel → files download with the disclaimer at the top.
- Verify exactly one row per download in irs.efile.download_audit (justification, acknowledged=true,
  row_count, format) and an app-log line. (I will run the audit-count SQL check after the restart.)

## Files Created/Modified
- src/app/exports.py (new), src/app/audit.py (new)
- src/app/auth.py, src/app/main.py, src/app/templates/glance.html, src/app/static/js/app.js
- src/app/requirements.txt (+openpyxl, +python-multipart)
- resources/grants.sql (SP appId grants + app-users SELECT)
- tests/test_exports.py (new), tests/test_audit.py (new), tests/test_auth.py (extended)
