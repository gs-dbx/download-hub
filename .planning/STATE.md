# Project State

**Status:** Milestone 1 (Phases 1-5) done. Milestone 2: Phase 6 (config layer) done; Phase 7
(multi-tab portal) code complete + deployed, app restarting. report_config has 2 reports
(efile_glance, efile_pins demo). Pending USER browser verify: Phase 7 portal (tabs/cache/refresh/
filters/pagination), plus carried Phase 4 download→audit row + Phase 5 branding/kill-switch. Open
follow-up: efile_glance_app_users UC grant (account-level federation).
**Current Phase:** 7 executed; browser verify pending. Milestone 2 remaining: Phase 8 (generic
download + docs).
**Last Updated:** 2026-08-12
**Workspace URL:** https://field-eng-aws-gov-demo-irs-demo.cloud.databricks.us
**AI Dev Kit Path:** ~/.ai-dev-kit/repo
**MCP Server:** configured (databricks-v2)

## Key Decisions (from /dbx:new-project)
- Architecture: server-rendered FastAPI + Jinja2 + vendored USWDS + vanilla JS (no npm/React).
- Auth: Databricks Apps OBO (user authorization) — UC access honored per signed-in user.
- Download gating: Databricks group membership (`efile_glance_download_users`), BEARS 1:1.
- Namespace: `irs.efile` — gold `daily_efile_glance`, audit `download_audit`.
- Exports: CSV (stdlib) + Excel (openpyxl); each carries the acknowledged disclaimer.
- Hard constraint: air-gapped target — vendor/pre-stage all deps, no CDN.

## Phase History
(none yet)

## Decisions Log
- 2026-08-13: Phase 8 executed — 7 tasks (e335599..8f67cad), 146 pass/1 skip. Generic download for
  ANY report (export filtered cached rows), effective_download_group gating, exports.py ColumnSpec-
  driven, audit +report_id/report_title (download_audit migrated to 13 cols, verified live),
  queries.py + _download_efile.html deleted, docs/REPORTS.md added. Deployed; audit migration applied;
  app restarted (SUCCEEDED). Browser download verify (both reports → audit rows) pending user.
  MILESTONE 2 COMPLETE (code + deploy). /download returns 503 on OBO-read failure (403 = membership gate).
- 2026-08-13: Phase 8 plan created — 8 tasks/waves (exports generalize → audit/cache/auth helpers →
  tests → main.py generic /download + delete queries.py → templates/_download.html + app.js → audit
  migration in seed notebook → docs → checkpoint). Research: queries.py becomes fully dead (delete
  module + test_queries). effective_download_group in auth.py; report_id/report_title added to
  download_audit via guarded ALTER. Plan authored directly from RESEARCH (planner flaky) + reviewed
  (8 waves, checkpoint, must-haves). Next: /dbx:execute-phase 8.
- 2026-08-13: Phase 8 discussed — generic download + docs. Locked: export from per-user cache
  (filters+search applied, all rows); every report downloadable (group+kill-switch gate, remove
  DOWNLOADABLE_REPORT_IDS); audit gains report_id + report_title (idempotent ALTER in seed notebook),
  drain_filter repurposed to generic applied-filters summary; single global disclaimer; fully retire
  efile-only download path (generalize exports.py to arbitrary columns, generic _download.html +
  /download, retire queries.build_glance_query_for_date + shaping.rows_to_context/METRIC_ORDER);
  add docs/REPORTS.md authoring guide. UPDATE (user): download gating is per-report —
  effective_download_group(report) = report.download_group or code default auth.DOWNLOAD_GROUP;
  same helper used for button visibility + POST /download enforcement (no schema change). Next: /dbx:plan-phase 8.
- 2026-08-13: Phase 7 executed — 6 tasks (3f44d29..d912bd4), 168 pass/1 skip. Config-driven
  multi-tab portal: pure cache.py (per-user LRU snapshot + filter/search/paginate) + render.py
  (generic ColumnSpec formatting); main.py /report/{id} + fragment endpoint + SP registry read;
  report.html/_rows.html generic; glance.html retired; efile download kept via _download_efile.html.
  Deployed; added 2nd demo report efile_pins; app restarting. Browser verify pending.
- 2026-08-13: Phase 7 plan created — 7 tasks/waves (cache.py → render.py → tests → main.py refactor
  → templates → app.js → checkpoint). Live research: report_config 1 row; CRITICAL — filter field
  drain NOT in display cols so snapshot query selects display∪filter. New pure cache.py (LRU +
  filter/search/paginate) + render.py (ColumnSpec formatting). Config-driven /report/{id} + fragment
  endpoint; per-user snapshot cache; report #1 keeps interim download. Plan authored directly from
  RESEARCH (planner agent flaky) + reviewed. Next: /dbx:execute-phase 7.
- 2026-08-13: Phase 7 discussed — multi-tab portal. Locked: URL-per-report (/report/{id}) + tab nav
  (registry read as app SP); per-user cache keyed (user,report,selected_date), in-memory bounded,
  refresh reloads + last-query time; generic render from ColumnSpec (int/pct/text); config-driven
  date selector + equality filter dropdowns + substring search + row-count pagination, all
  server-side over the cached snapshot via a generic table-fragment endpoint; show all tabs, graceful
  empty on no-access. Phase 7 ABSORBS filters+pagination (roadmap renumbered: Phase 8 = download
  generalization + docs). Report #1 keeps existing download until Phase 8. Next: /dbx:plan-phase 7.
- 2026-08-13: Phase 6 executed + live-verified — 4 tasks (603a2b4..ab65cde), 129 pass/1 skip.
  irs.efile.report_config created + seeded (report #1 efile_glance, valid JSON), idempotent MERGE
  confirmed (re-run = still 1 row), SP SELECT grant applied. src/app/reports.py pure query builder +
  config parser shipped. Backend-only; no UI change (Phase 7 wires it).
- 2026-08-13: Phase 6 plan created — 5 tasks/waves (pure reports.py → tests → seed-notebook
  report_config create+MERGE → grant → checkpoint). Live research: report_config absent, SP has
  catalog/schema USE. New pure module src/app/reports.py (model+validators+builders); DeltaTable
  MERGE (json.dumps, no overwrite); SP SELECT grant. Plan authored directly from RESEARCH (planner
  agent flaky) + reviewed. Next: /dbx:execute-phase 6.
- 2026-08-13: Phase 6 discussed — config layer. Locked: irs.efile.report_config schema
  (report_id/title/source_fqn/date_field/columns_json/filters_json/order_by/display_order/enabled/
  download_group/updated_at); list config as JSON string columns; column config = name+label+format
  (int/pct/text); filters equality single-select; create+seed via extended efile_seed notebook
  (CREATE IF NOT EXISTS + MERGE report #1); pure generic query builder with strict identifier
  validation (values bound as params, identifiers validated+interpolated); app SP reads registry +
  needs SELECT grant. Backend-only (no UI). Next: /dbx:plan-phase 6.
- 2026-08-13: Milestone 2 (configurable multi-report portal) scoped. Locked: UC config table
  irs.efile.report_config (one row per report); per-user OBO reads + PER-USER session cache (NO
  shared cache — user chose "use underlying permissions" over shared caching); tabs; row-count
  pagination (display only); filter on cached full dataset; download = full filtered set gated by
  single global download group (reuse ack/justification/audit; audit gains report id). Phases 6-9
  in ROADMAP. Existing E-File glance = configured report #1. Next: /dbx:discuss-phase 6.
- 2026-08-13: Phase 5 executed — 5 tasks (8acfa53..10d8a48), 89 pass/1 skip. Kill switch
  (EFILE_DOWNLOADS_ENABLED), IRS branding (local logo + app.css navy palette, OCFO removed from all
  shipping code), pinned deps + requirements.lock + build_wheelhouse.sh, docs (README + DEPLOY +
  PERMISSIONS + OFFLINE), guard tests. Deployed; app restarting. Browser verify pending (branding,
  kill-switch toggle). ROADMAP fully built.
- 2026-08-13: Phase 4 verified — PASS (see 4-VERIFICATION.md). All 6 code waves pass on inspection;
  66 pass/1 skip; bundle validate OK; anti-patterns clean. Live: app RUNNING/SUCCEEDED, both groups
  exist + greg a member of download group, all 4 SP grants verified, download_audit=0 (expected).
  FR-7..FR-11 + NFR-4/NFR-5 covered. SKIP (browser SSO): end-to-end download + resulting audit row.
  Non-blocking follow-up: efile_glance_app_users UC grant (account-level federation).
- 2026-08-13: Phase 4 executed — 6 tasks (d320956..0640a84), 66 pass/1 skip. Gated download:
  OBO group check (efile_glance_download_users via me()), ack+justification, CSV/Excel with
  disclaimer, audit-first INSERT as app SP. Groups created; greg added to download group; SP
  appId grants SUCCEEDED. app_users UC grant FAILED (PRINCIPAL_DOES_NOT_EXIST — GovCloud
  account-level federation; SCIM workspace group not UC-resolvable). NON-BLOCKING (Greg owns
  tables; membership check uses SCIM me()). FOLLOW-UP: create efile_glance_app_users at ACCOUNT
  level + re-run its 3 grants. Deployed; app restarting; browser download verify pending.
- 2026-08-13: Phase 3 executed + deployed — 4 tasks (3081518..31785a3), 43 tests pass. /table
  endpoint (parameterized OBO query → _rows.html fragment) + vanilla app.js (search + select-swap).
  Deploy SUCCEEDED, app.js served 200. Browser filter click-through pending user (SSO-gated).
  Proceeding autonomously to Phase 4 per user's "go to bed" instruction (best-guess on questions).
- 2026-08-13: Phase 3 discussed — interactive filters. Locked: SERVER round-trip per
  report-date/DRAIN (new /table endpoint returns a Jinja HTML row-fragment; OBO per request);
  search client-side on metric name; filters combine (AND) + preserve; report_date/drain
  validated against allowed sets + passed as SQL params (no injection). Reuse download-hub app;
  no new deps. Sequencing: user chose filters-first (Phase 3) then download (Phase 4).
- 2026-08-13: Phase 2 checkpoint verified LIVE on dev — app download-hub deployed (standard
  engine), URL active, renders 17-row table via OBO (GET / 200, USWDS assets 200). Checkpoint
  bugfix 17462c8: pin auth_type=pat (Apps runtime injects SP OAuth env → conflicted with user
  token). Phase 2 COMPLETE. IMPORTANT PATTERN for future Apps OBO code: always pass
  auth_type="pat" when building a user-token WorkspaceClient inside a Databricks App.
- 2026-08-12: Phase 2 executed (code) — 6 tasks on branch dbx/download-hub-phase-1
  (993f272..15d6e14). Unit tests 30/30; bundle validate PASS; no anti-patterns. USWDS 3.13.0
  vendored from GitHub release. Added error.html (friendly OBO/read-error page). Wave 7 live
  app-deploy checkpoint PENDING.
- 2026-08-12: Phase 2 plan created — 7 tasks across 7 waves (app scaffold → USWDS vendoring →
  pure logic → FastAPI+templates+app.yaml → unit tests → DAB app resource → deploy checkpoint).
  Live research: yes (Apps standard-engine confirmed, dev-migration-factory reference pattern,
  USWDS 3.13.0 pinned, target query verified). Skill refs: python-dev, databricks-python-sdk,
  databricks-app-python, asset-bundles. NOTE: dbx-planner agent hit repeated API errors mid-write;
  plan authored directly from RESEARCH.md and reviewed (skill paths + structure verified).
- 2026-08-12: Phase 2 discussed — App skeleton. Locked: OBO reads via databricks-sdk Statement
  Execution API (user's x-forwarded-access-token, no SP fallback); vendored compiled USWDS 3.x
  dist subset; initial render = latest report_date + drain=ALL static (report_date list + DRAIN
  options passed inert for Phase 3); Apps-only (no local run). Live scan: Apps enabled, no
  download-hub app yet, gold table ready (408 rows).
- 2026-08-12: Phase 1 verified — PASS. All 6 code tasks + live checkpoint pass, 0 blocking
  gaps. Static checks clean (9/9 pytest, bundle validate OK, no anti-patterns). FR-1 and the
  FR-9 audit table satisfied. Grant application + app SP write are by-design Phase 4 deferrals.
  Ready for Phase 2 (app skeleton).
- 2026-08-12: Phase 1 executed — 6 tasks on branch dbx/download-hub-phase-1
  (8f698da..c618288). Unit tests 9/9; bundle validate PASS. Checkpoint verified LIVE on dev:
  deploy+run SUCCESS, irs.efile.daily_efile_glance = 408 rows/4 drains, ALL-slice pct_change
  recomputed correctly, download_audit created empty with justification col. Group
  efile_glance_app_users absent → grant application deferred to Phase 4.
- 2026-08-12: Phase 1 plan created — 7 tasks across 7 waves (foundation → generator →
  notebook driver → tests → bundle config → UC grants → checkpoint).
  Live research: yes (irs.efile absent, warehouse 2f225c0740dcd22b HEALTHY, no conflicts).
  Skill refs: python-dev, databricks-python-sdk, asset-bundles, synthetic-data-generation,
  databricks-unity-catalog.
- 2026-08-12: Project initialized via /dbx:new-project.
- 2026-08-12: AI/BI Dashboard rejected — cannot force acknowledgement/justification,
  write custom audit rows, gate download by group, or apply USWDS branding.
- 2026-08-12: React+FastAPI rejected in favor of server-rendered FastAPI to minimize
  the dependency/build surface for the air-gapped target environment.
