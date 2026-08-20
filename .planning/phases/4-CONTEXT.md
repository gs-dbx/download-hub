# Phase 4 Context

**Phase:** Gated download — acknowledgement, justification, audit, CSV/Excel
**Discussed:** 2026-08-13 (decisions made autonomously per user instruction; best-guess, documented)
**Status:** ready for planning

## Locked Decisions (best-guess — flagged for user review at wake-up)

### Component Type
- Enhancement to the deployed `download-hub` FastAPI app. Adds the download feature + a first WRITE
  path (audit). No pipeline/job.

### UC targets
- READ (OBO, as user): `irs.efile.daily_efile_glance` (unchanged).
- WRITE (as app service principal): `irs.efile.download_audit` — one INSERT per download.

### Download gating — Databricks group membership, checked via OBO
- The download control/endpoints are available ONLY to users in the group
  **`efile_glance_download_users`** (maps 1:1 to a BEARS entitlement).
- **Membership check mechanism:** use the signed-in user's OBO token →
  `w.current_user.me().groups` and test whether any group's display name equals
  `efile_glance_download_users`. Uses the auto-added `iam.current-user:read` scope — no admin SCIM
  call, no service principal needed for the check. Result passed to the template as `can_download`.
- **Defense in depth:** the download endpoints RE-CHECK membership server-side (never trust the
  hidden UI). A non-member hitting the endpoint directly gets HTTP 403.
- **Data access is still UC-enforced separately:** the export re-queries the gold table via the
  user's OBO token, so a user who is in the download group but lacks SELECT still gets nothing
  (both conditions required, per FR-7).

### Groups (setup) — created this phase
- Create Databricks groups `efile_glance_app_users` and `efile_glance_download_users` if absent
  (needs workspace admin). Add the deploying user (greg.skinner) to `efile_glance_download_users`
  so the feature is demoable end-to-end. If group creation requires admin the executor lacks,
  document it and the gating degrades safely to "no download shown" (Greg can create later).
- Apply the Phase-1 `resources/grants.sql` SELECT grant to `efile_glance_app_users` now that the
  group exists.

### Audit write — as the app service principal (always lands)
- The audit INSERT runs as the **app service principal** (default injected OAuth creds:
  `WorkspaceClient()` with no token, per [[reference_databricks_apps_obo_auth]]), NOT as the user —
  so the audit row is recorded regardless of the user's own table grants. This is the one place the
  app acts as itself, not the user.
- Requires a UC grant: the app SP gets `USE CATALOG irs`, `USE SCHEMA irs.efile`,
  `MODIFY` + `SELECT` on `irs.efile.download_audit`. The SP name/appId is read from
  `databricks apps get download-hub` after deploy.
- One row per download with all 11 columns (audit_id uuid, event_ts, user_email from
  `current_user.me()`/`X-Forwarded-User`, report_date, drain_filter, search_filter, row_count,
  export_format, justification, acknowledged=true, app_version). Also emit an app-log line
  (`logging`/print) so the event appears in `databricks apps logs` (FR-9: only non-natively-logged
  events — the fact a download occurred).
- **Audit-first ordering:** write the audit row BEFORE streaming the file; if the audit write fails,
  the download is refused (HTTP 500) — no un-audited downloads (NFR-5).

### Download flow / UX
- A download panel on the glance page, rendered ONLY when `can_download` is true. It contains:
  a data-handling **acknowledgement checkbox**, a **justification** textarea (required, freetext),
  and a **format** selector (CSV / Excel). It carries the current `report_date`, `drain`, and
  `search` as hidden fields (the view being exported).
- Submit is a **POST `/download`** (form-encoded). Server validates: `acknowledged` is true AND
  `justification` is non-empty (not just whitespace) → else re-render/return 400 with a message.
  Re-check group membership (403 if not). Re-run the OBO query for (report_date, drain), apply the
  metric-name search filter server-side to match what the user sees, build the file, write audit,
  return the file as an attachment (`Content-Disposition`).
- Filename e.g. `daily_efile_glance_<report_date>_<drain>.csv|xlsx`.

### Export formats + disclaimer
- **CSV** via stdlib `csv` (io.StringIO). **Excel (.xlsx)** via **openpyxl** (added to
  `src/app/requirements.txt` this phase — pure-Python, vendorable).
- Each export carries, at the TOP, the exact disclaimer text the user acknowledged (a module
  constant `DISCLAIMER`). CSV: disclaimer as leading comment/quoted lines before the header row.
  Excel: disclaimer in the first rows above the table header (merged/wrapped).
- Columns exported: Metric, 2026 (value_cy), 2025 (value_py), % Change (pct_change; "—"/blank for NULL).

### Code structure (extend the app)
- `auth.py`: add `extract_user_email(headers)` (from `X-Forwarded-User`) + a group-membership helper
  signature (the actual `me()` call is an I/O boundary in main.py).
- New `exports.py` (PURE): `to_csv_bytes(rows, disclaimer) -> bytes`, `to_xlsx_bytes(rows,
  disclaimer) -> bytes`, `DISCLAIMER` constant, filename helper. Unit-testable without the app
  (openpyxl import is fine in tests if installed; otherwise guard). Keep the disclaimer prepend logic here.
- New `audit.py` (PURE builder): `build_audit_row(...) -> dict` and `build_insert(...)` returning
  a parameterized INSERT statement + params for `download_audit` (mirrors the queries.py param style).
- `main.py`: add `can_download` to the `/` context (via a `me()` check); add `POST /download`
  (validate → re-check group → OBO read → filter → build file → SP audit insert → stream file).
- `templates/glance.html`: add the conditional download panel (acknowledgement + justification +
  format), USWDS-styled.
- `resources/grants.sql`: extend with the app-SP audit grants + apply the app-users SELECT.

### Testing
- Unit (pytest, offline): exports (CSV/XLSX bytes contain the disclaimer + header + a known row;
  NULL pct handling), audit row/INSERT builder (11 fields, parameterized, 3-level FQN), the
  validation logic (acknowledged must be true, justification non-empty), and the group-name match
  helper. openpyxl-dependent test skips cleanly if the pkg isn't in the dev venv.
- Live/manual (checkpoint): as a download-group member, complete the flow → file downloads with
  disclaimer; audit row appears in `irs.efile.download_audit`; app log shows the event. As a
  non-member, the panel is hidden and POST /download → 403.

### Deployment
- Redeploy app via DAB standard engine + restart (slow venv build; now also installs openpyxl).
- Group creation + grants applied via CLI against dev (admin permitting).

### Alerting
- greg.skinner@databricks.com (dev).

## Open Questions (resolved as best-guess; review at wake-up)
- Group-membership via `current_user.me().groups` — assumed the OBO token + iam.current-user:read
  scope returns the user's groups. RESEARCH must verify the `.groups` shape and that scope suffices;
  fallback = app SP calls SCIM Groups API (needs SP group-read permission).
- Whether the executor has admin to create groups / grant to the SP — RESEARCH checks
  `databricks current-user me` + attempts; if not admin, document and defer group creation to Greg.
- openpyxl availability in the Apps runtime image — added to requirements.txt regardless.

## Workspace Scan Summary
- From prior phases: `download-hub` app ACTIVE on dev; `irs.efile.download_audit` exists (empty, 11
  cols incl. justification); groups `efile_glance_*` ABSENT as of Phase 1 (to be created here);
  warehouse 2f225c0740dcd22b healthy.
