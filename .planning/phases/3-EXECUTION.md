# Phase 3 Execution Summary

**Date:** 2026-08-13
**Status:** COMPLETE (code + deploy) — live browser filter click-through pending user eyeball
**Branch:** dbx/download-hub-phase-1

## Completed Tasks
| Task | Wave | Commit | Status |
|------|------|--------|--------|
| Add build_glance_query_for_date + validate_drain/validate_report_date | 1 | 3081518 | PASS |
| Extend _run_sql for parameters + add GET /table route | 2 | 9e9c1a3 | PASS |
| Extract _rows.html, wire tbody id + include, add app.js | 3 | bfc6eec | PASS |
| Extend tests for parameterized builder + validators | 4 | 31785a3 | PASS |
| Deploy + verify filters live | 5 | — | deploy SUCCEEDED; browser click-through pending |

## Test Results
- Unit tests: **43 passed** (30 prior + 13 new). Bundle validate: PASS. Anti-patterns: none.
- Parameterized query live-verified in research (17 rows; TIMESTAMP+STRING params bind correctly).

## Live Checkpoint (dev)
- `bundle deploy --target dev` + `bundle run download_hub`: app redeployed, compute ACTIVE,
  active_deployment SUCCEEDED. (A trailing CLI status-poll timeout was cosmetic — final state SUCCEEDED.)
- Logs after restart: `GET / 200`, `GET /static/js/app.js 200` (new Phase 3 JS served), all USWDS assets 200.
- PENDING (needs SSO browser session — user): confirm changing report-date/DRAIN swaps the 17 rows,
  search trims live and persists across a report-date/DRAIN change, and `/table` returns 400 for a
  bogus report_date. Automated agent cannot drive the SSO-gated UI.

## Deviations
- Wave 3: reworded an app.js comment ("no remote assets") so the anti-CDN grep didn't match the
  literal word "CDN" in a comment. No behavioral impact.
- databricks-sdk not installed in dev .venv (only in Apps runtime) → main.py verified via py_compile
  + grep, not import (expected, per research).

## Files Created/Modified
- src/app/queries.py (build_glance_query_for_date, validate_drain, validate_report_date)
- src/app/main.py (_run_sql parameters, GET /table)
- src/app/templates/_rows.html (new), glance.html (tbody id + include), base.html (app.js tag)
- src/app/static/js/app.js (new)
- tests/test_queries.py (extended)
