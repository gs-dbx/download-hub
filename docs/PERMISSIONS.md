# Permissions & access model

How the **Data Download Hub** app authorizes reads, gates downloads, and audits them. Three layers: OBO reads (Unity Catalog enforced), download gating (per-report group + kill switch), and the audit write (app service principal).

## On-behalf-of-user (OBO) reads

- The Databricks Apps runtime forwards the signed-in user's OAuth access token in the `X-Forwarded-Access-Token` request header (and their email in `X-Forwarded-User`).
- For every request, the app builds a **fresh per-request** `WorkspaceClient` from that token with `auth_type="pat"`. Pinning `auth_type="pat"` is required: the runtime also injects the app service principal's OAuth creds (`DATABRICKS_CLIENT_ID` / `DATABRICKS_CLIENT_SECRET`), and without pinning the SDK refuses to initialize with "more than one authorization method configured".
- Each report's source read runs **as the user** on the bound SQL warehouse, so Unity Catalog enforces the user's own SELECT access. The app grants no data access of its own for reads. (The `report_config` registry is read separately, as the app service principal.)
- There is **no fallback**: if the OBO header is absent, the request is rejected (401) — no CLI profile, no mock data.

## Volume reports (OBO file access)

A `kind = 'volume'` report browses a pinned UC Volume root (`volume_root`). Both
the folder listing and the file download run **as the signed-in user (OBO)** via
the Files API, so Unity Catalog enforces the user's own access:

- Grant the report's **view group** (and its download group) **`READ VOLUME`** on
  the volume so members can list + download:
  `GRANT READ VOLUME ON VOLUME <catalog>.<schema>.<volume> TO \`<group>\`;`
- Every browsed path is **root-relative and path-jailed** server-side (a `..`,
  absolute, or sibling-prefix escape is rejected with a 400 before any Files-API
  call) — the pinned root is the hard boundary.
- Downloads reuse the same gate + acknowledgement + justification + audit-first
  write as query downloads (the audit row records the file path and byte size).

## Download gating

Download is generic — it applies to **every** report and exports the current filtered on-screen view. Direct results are capped; large CSV results are fetched OBO in bounded pages and delivered through the configured export volume. It is allowed only when **both** conditions hold, re-checked server-side on every `POST /download`:

1. **Kill switch:** `downloads_enabled(DOWNLOADS_ENABLED)` is true (default true; false for `false`/`0`/`no`/`off`/empty). When off, `POST /download` returns 403 "Downloads are temporarily disabled." and the UI panel is hidden — independent of group membership.
2. **Group membership:** the user is a member of the report's **effective download group**, determined from `current_user.me()` group display names.

`can_download = downloads_enabled(...) AND is_member(me(), effective_download_group(report))`.
`effective_download_group(report)` is the report's `report_config.download_group` when set (stripped), else the code default `auth.DOWNLOAD_GROUP` (`download_hub_download_users`) — so a report can be gated to its own Databricks group, otherwise it falls back to the single default. The **same** helper drives both button visibility and server enforcement.

The gate **never fails open**: any error resolving membership degrades to "not allowed" (panel hidden, download denied). The hidden UI panel is never trusted — membership is always re-checked on the server before a file is produced.

## Audit (audit-first)

- On each allowed download the app writes **exactly one** row to `{APP_CATALOG}.{APP_SCHEMA}.download_audit` — capturing only what Databricks does not natively log, i.e. that a download occurred: `report_id`/`report_title`, user email, report_date, applied-filters summary (e.g., `region=ALL, quarter=Q3`), search filter, row count, export format (CSV/XLSX), justification, app version, and timestamp.
- The audit INSERT runs **as the app service principal**, via the default `WorkspaceClient()` that auto-detects the runtime-injected SP credentials — not as the user.
- **Audit-first:** the INSERT must reach `SUCCEEDED` *before* the file is returned. If it fails, the download is blocked (HTTP 500, no file). An app-log line is also emitted so the event surfaces in `databricks apps logs`.

## Group-based access control

Two Databricks groups gate the app:

| Group | Purpose |
| --- | --- |
| `download_hub_app_users` | app access — SELECT on `{APP_CATALOG}.{APP_SCHEMA}.report_config` and report source tables via OBO |
| `download_hub_download_users` | the gated download feature (can export data) |

Access is granted by adding a user to the corresponding group (see [DEPLOY.md](DEPLOY.md) §5). App-users can view any report their user account has UC SELECT access to; download-users additionally clear the download gate and can export data (subject to UC access control on the source table).

A report can optionally override the default download group via its `report_config.download_group` column — setting this to a different Databricks group name gates downloads for that specific report to only members of that group.

## Known follow-up — account-level federation

The `download_hub_app_users` SELECT grants in `resources/grants.sql` (on `USE CATALOG`, `USE SCHEMA`, and `SELECT ON TABLE`) require the group to be resolvable by Unity Catalog. Under **account-level identity federation**, a group that exists only as a workspace-level SCIM group may not be UC-resolvable, and those grants may fail with `PRINCIPAL_DOES_NOT_EXIST`.

**Remediation:** create or federate `download_hub_app_users` at the **account level** (so it resolves in Unity Catalog), then re-run the three app-users GRANTs from `resources/grants.sql` via the Statement Execution API. The app service principal grants (the audit write) are independent and are unaffected.
