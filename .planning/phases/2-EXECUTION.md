# Phase 2 Execution Summary

**Date:** 2026-08-12
**Status:** COMPLETE — code + live checkpoint verified (app renders via OBO on dev)
**Branch:** dbx/download-hub-phase-1

## Completed Tasks
| Task | Wave | Commit | Status |
|------|------|--------|--------|
| Scaffold src/app package and runtime requirements.txt | 1 | 993f272 | PASS |
| Fetch and commit vendored USWDS 3.13.0 dist subset | 2 | 5cebb01 | PASS |
| Implement pure queries.py, shaping.py, auth.py | 3 | d89b504 | PASS |
| Write main.py, base.html, glance.html, app.yaml (+error.html) | 4 | e881ebd | PASS |
| Write pytest tests for queries, shaping, auth | 5 | 0aac2fd | PASS |
| Add resources/app.yml (download-hub app + warehouse binding) | 6 | 15d6e14 | PASS |
| Fix OBO client auth conflict (pin auth_type=pat) | 4* | 17462c8 | PASS (checkpoint bugfix) |
| Deploy app to dev, start, verify render via OBO | 7 | — | PASS (live on dev 2026-08-13) |

## Checkpoint Verification (live, dev)
- `bundle deploy --target dev` (standard engine): SUCCESS. App `download-hub` created.
- App URL: https://download-hub-2460574726701099.aws-gov.databricksapps.us — compute ACTIVE, active_deployment SUCCEEDED.
- **Bug found & fixed during checkpoint:** first render 500'd with
  `ValueError: more than one authorization method configured: oauth and pat`. The Apps runtime
  injects the app SP's DATABRICKS_CLIENT_ID/SECRET; `Config(host, token)` auto-detected those
  alongside the user token. Fix (commit 17462c8): pin `auth_type="pat"` so the SDK uses only the
  user's OBO bearer token (Config._validate short-circuits when auth_type is set). Redeployed.
- After fix: logs show `GET / HTTP/1.1 200 OK` plus all `/static/uswds/...` assets (css/js/fonts/
  icon sprite) 200 — page renders via OBO as the signed-in user. No auth errors.

## Deviations
- **Wave 2 source:** GitHub release tarball (`uswds-uswds-3.13.0.tgz`) succeeded; npm fallback not
  needed. Verified package.json version 3.13.0. 2599 vendored files under src/app/static/uswds/.
- **Wave 4 extra file (error.html):** added `src/app/templates/error.html` (extends base.html,
  local assets only) so the missing-OBO-header (401) and gold-read-failure (403) paths render a
  friendly USWDS page instead of a bare HTTPException. This is the "friendly error page" the task
  permitted — a strict addition to the listed file set. Committed with Wave 4.
- **Wave 5 guard test:** `generator.METRICS` is a list of tuples `(name, group, sort_order, base)`,
  not objects, so the guard compares `METRIC_ORDER == tuple(m[0] for m in generator.METRICS)`.
  Same invariant (UI order == data order); passes.
- **App name:** Databricks apps are NOT dev-mode name-prefixed, so `name: download-hub` needed no
  remedy (L2 fallback unused).

## Issues Encountered
- None blocking. Standard bundle engine validates the Apps resource (L1 confirmed in practice).

## Test Results
- Unit tests: **30 passed, 0 failed** (21 new for queries/shaping/auth + 9 Phase 1 generator).
- Bundle validation (`databricks bundle validate -t dev -p DEFAULT`): **PASS**.
- Anti-pattern scan: no `import dlt`, no bare `except:`, no hardcoded workspace host in Python,
  no CDN/external URLs in templates. Pure modules (queries/shaping/auth) import no
  fastapi/databricks/pyspark.

## Files Created/Modified
- src/app/__init__.py, src/app/requirements.txt
- src/app/static/uswds/{css,js,fonts,img}/  (USWDS 3.13.0 vendored)
- src/app/queries.py, src/app/shaping.py, src/app/auth.py
- src/app/main.py
- src/app/templates/base.html, glance.html, error.html
- src/app/app.yaml
- tests/test_queries.py, tests/test_shaping.py, tests/test_auth.py
- resources/app.yml

## Remaining: Wave 7 Checkpoint (human — live GovCloud deploy)
From repo root (branch dbx/download-hub-phase-1):
1. `PYTHONPATH=src .venv/bin/python -m pytest tests/ -v`
2. `databricks bundle validate -t dev -p DEFAULT`
3. `databricks bundle deploy --target dev -p DEFAULT`  (standard engine)
4. Start the app: `databricks bundle run download_hub -t dev -p DEFAULT`
   (or `databricks apps deploy download-hub ...` if the CLI version needs it)
5. `databricks apps get download-hub -p DEFAULT`  → confirm ACTIVE/SUCCEEDED, capture URL
6. Open the URL (as greg.skinner): confirm USWDS "Daily E-File at a Glance" renders the 17-row
   table for 2026-01-12 00:00:00 / drain=ALL; inert search/report-date/DRAIN controls present.
7. On OBO errors, check `databricks apps logs download-hub`; confirm effective scopes include sql.
Note: efile_glance_app_users SELECT grant is Phase 4; in dev the owner (Greg) already has SELECT.
