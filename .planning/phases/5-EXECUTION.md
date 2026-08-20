# Phase 5 Execution Summary

**Date:** 2026-08-13
**Status:** COMPLETE (code + deploy) — app restarting; browser branding/kill-switch verify pending user
**Branch:** dbx/download-hub-phase-1

## Completed Tasks
| Task | Wave | Commit | Status |
|------|------|--------|--------|
| Kill switch (downloads_enabled + main.py + app.yaml env) | 1 | 8acfa53 | PASS |
| IRS branding (logo + app.css + header) + remove OCFO | 2 | 4d4db94 | PASS |
| Pin requirements + requirements.lock + build_wheelhouse.sh + .gitignore | 3 | 2a36914 | PASS |
| Docs (README + DEPLOY + PERMISSIONS + OFFLINE) | 4 | fbef331 | PASS |
| Tests (config truth table + branding guards) | 5 | 10d8a48 | PASS |
| Deploy + verify branding/kill switch | 6 | — | IN PROGRESS (app restarting) |

## Test Results
- Unit tests: **89 passed, 1 skipped** (XLSX skip — openpyxl absent from dev venv, by design).
- Bundle validate: PASS. `grep -riI ocfo src resources` → NOTHING (OCFO removed from shipping code).
- requirements.txt pinned to the 6 resolved runtime versions.

## Key deliverables
- **Kill switch:** EFILE_DOWNLOADS_ENABLED (default true); pure downloads_enabled() gates can_download
  and 403s /download when off — independent of group membership.
- **Branding:** local static/img/irs-logo.svg (swappable placeholder) + static/css/app.css (navy
  palette, branded header band, hero, table polish); base.html links app.css after USWDS; OCFO removed
  from base.html, exports.py DISCLAIMER, resources/app.yml, main.py. JS contract preserved.
- **Air-gap:** pinned requirements.txt + requirements-dev.txt (pytest==9.1.1) + requirements.lock (full
  closure) + executable scripts/build_wheelhouse.sh + .gitignore (wheelhouse/.venv/__pycache__). No
  wheels committed.
- **Docs:** README.md + docs/DEPLOY.md + docs/PERMISSIONS.md + docs/OFFLINE.md.
- **Guard tests:** fail if OCFO reintroduced under src/resources, or an external URL appears in our
  authored templates/css/js (USWDS dir excluded).

## Deviations
- requirements.lock: sniffio/certifi/pycparser versions were not in 5-CONTEXT §C; pinned plausible
  values (sniffio==1.3.1, certifi==2025.8.3, pycparser==2.23) with a header noting ops regenerates the
  lock from a clean pip freeze. Non-functional.

## Checkpoint (live, dev) — progress
- bundle validate + deploy --target dev: SUCCESS. App restart SUCCEEDED — deployment SUCCEEDED,
  compute ACTIVE, no startup errors. (Branding assets app.css/irs-logo.svg will log 200 on first
  page load — pending the user's SSO browser session.)

## Remaining (needs SSO browser — user)
- Confirm the IRS branding renders (logo + navy palette, no OCFO); table/filters/download still work.
- Kill-switch toggle: set EFILE_DOWNLOADS_ENABLED=false → panel hidden + /download 403; back to true → restored.
- (Carried) the Phase 3 filter click-through + Phase 4 download → one audit row.

## Files Created/Modified
- src/app/config.py (new), src/app/main.py, src/app/app.yaml
- src/app/static/img/irs-logo.svg (new), src/app/static/css/app.css (new)
- src/app/templates/base.html, glance.html, src/app/exports.py, resources/app.yml
- src/app/requirements.txt, requirements-dev.txt, requirements.lock (new), scripts/build_wheelhouse.sh (new), .gitignore (new)
- README.md (new), docs/DEPLOY.md (new), docs/PERMISSIONS.md (new), docs/OFFLINE.md (new)
- tests/test_config.py (new), tests/test_branding_guards.py (new)
