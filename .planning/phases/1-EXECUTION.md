# Phase 1 Execution Summary

**Date:** 2026-08-12
**Status:** COMPLETE — code + live checkpoint verified on dev
**Branch:** dbx/download-hub-phase-1

## Completed Tasks
| Task | Wave | Commit | Status |
|------|------|--------|--------|
| Create package/test scaffolding and dev requirements | 1 | 8f698da | PASS |
| Implement pure synthetic-data generator (generator.py) | 2 | 81c26d6 | PASS |
| Write serverless notebook driver generate_efile_glance.py | 3 | 9ba5a4b | PASS |
| Write pytest unit tests for generator.py | 4 | 9d363b3 | PASS |
| Add resources/seed_job.yml and wire include: into databricks.yml | 5 | 1787a23 | PASS |
| Author UC grants for irs.efile schema and gold table | 6 | c618288 | PASS (application deferred to Phase 4 — group absent) |
| Deploy to dev, run seed job, verify tables and grants | 7 | — | PASS (live on dev 2026-08-12) |

## Checkpoint Verification (live, dev)
- `bundle deploy --target dev`: SUCCESS. `bundle run efile_seed`: run 1112265644594918 TERMINATED SUCCESS.
- `irs.efile.daily_efile_glance`: **408 rows**, exactly 4 drains (ALL/E/M/N = 102 each = 17 metrics × 6 dates).
- ALL-slice spot check (2026-01-08, "Total Accepted (combined)"): ALL cy=9,827,762 = E(2,969,390)+M(3,229,616)+N(3,628,756); ALL py=10,437,452 = sum; pct_change **−5.8 recomputed from sums** (≠ −5.6 average of per-drain). CONFIRMED.
- NULL pct_change branch materialized: 4 rows (forced value_py=0 case, E/M/N/ALL).
- `irs.efile.download_audit`: exists, **0 rows**, all 11 columns incl. `justification STRING`.
- UC grants: `efile_glance_app_users` group does NOT exist in workspace → grants.sql committed, application deferred to Phase 4 (per plan allowance).

## Deviations
- **Environment (verify only):** `python` is not on PATH (only `python3` 3.14.3), and the OS
  Python is PEP-668 externally-managed so `pip install pytest` is blocked. Executor created a
  project virtualenv at `.venv/` and ran `.venv/bin/python -m pytest`. `.venv/` and
  `__pycache__/` are left untracked (not committed). No source-code deviation.
- **value_py==0 branch (within plan latitude):** to deterministically exercise the NULL
  pct_change path, metric "Online Accepted (amended)" is forced to value_py=0 on the first
  report date (2026-01-05) for E/M/N (and therefore ALL). Otherwise value_py is a seeded
  ratio of value_cy in [0.85, 1.25], keeping pct_change ~ -15%..+25%.
- **Wave 6 grant application deferred:** grants.sql is authored and committed, but the GRANT
  statements are NOT applied yet (schema/tables don't exist until the checkpoint deploy+run).
  A header comment in grants.sql documents this; allowed by the task's acceptance criteria.

## Issues Encountered
- None blocking. bundle validate passes; grants deferred by design (tables not yet created).

## Test Results
- Unit tests: **9 passed, 0 failed** (`.venv/bin/python -m pytest tests/`).
- Bundle validation (`databricks bundle validate -t dev -p DEFAULT`): **PASS**.
- Anti-pattern scan: no `import dlt`, no bare `except:`, no hardcoded workspace URL/token in
  Python/YAML (workspace.host in databricks.yml is standard DAB config).

## Files Created/Modified
- src/efile_glance/__init__.py
- src/efile_glance/generator.py
- src/notebooks/.gitkeep
- src/notebooks/generate_efile_glance.py
- tests/__init__.py
- tests/test_generator.py
- requirements-dev.txt
- resources/seed_job.yml
- resources/grants.sql
- databricks.yml (added `include: resources/*.yml`)

## Remaining: Wave 7 Checkpoint (human — live GovCloud actions)
Run from repo root (branch dbx/download-hub-phase-1):
1. `.venv/bin/python -m pytest tests/ -v`
2. `databricks bundle validate -t dev -p DEFAULT`
3. `databricks bundle deploy --target dev -p DEFAULT`
4. `databricks bundle run efile_seed -t dev -p DEFAULT`
5. Verify: `SELECT drain, COUNT(*) FROM irs.efile.daily_efile_glance GROUP BY drain` → 4 drains, 408 total
6. Spot-check ALL-slice pct_change for one (report_date, metric)
7. `DESCRIBE TABLE irs.efile.download_audit` (11 cols incl. justification) + `SELECT COUNT(*)` = 0
8. Apply resources/grants.sql if the efile_glance_app_users group exists
