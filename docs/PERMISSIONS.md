# Permissions & access model

How the **Data Download Hub** app authorizes reads, gates views and downloads, and audits them. Layers: OBO reads (Unity Catalog enforced), **view visibility** (view group ∪ download group), download gating (download group + kill switch), the admin console (admin group), and the audit write (app service principal).

## Views, download, and admin groups

Access is driven by Databricks group membership across three roles:

| Role | Group | Grants |
| --- | --- | --- |
| **View** | a view's `view_key` (each view in `report_view` is named by, and *is*, a Databricks group) | see that view's report tabs |
| **Download** | explicit `report_config.download_group`, else derived **`<view_key>` + `DOWNLOAD_GROUP_SUFFIX`** (default `_dl`) | export that view's reports |
| **Admin** | `ADMIN_GROUP` (default `download_hub_admin_users`) | use the `/admin` console |

- **A user sees a report if they belong to its view group OR its download group** — so download-group members always see what they can export (`can_view = is_member(view_group) OR is_member(download_group)`).
- A user in more than one view gets a **view switcher** to change views.
- The download group is derived from the view key by naming convention unless a report sets an explicit `download_group`. Example: view `efile_ops` → download group `efile_ops_dl`.

## On-behalf-of-user (OBO) reads

- The Databricks Apps runtime forwards the signed-in user's OAuth access token in the `X-Forwarded-Access-Token` request header (and their email in `X-Forwarded-User`).
- For every request, the app builds a **fresh per-request** `WorkspaceClient` from that token with `auth_type="pat"`. Pinning `auth_type="pat"` is required: the runtime also injects the app service principal's OAuth creds (`DATABRICKS_CLIENT_ID` / `DATABRICKS_CLIENT_SECRET`), and without pinning the SDK refuses to initialize with "more than one authorization method configured".
- Each report's source read runs **as the user** on the bound SQL warehouse, so Unity Catalog enforces the user's own SELECT access. The app grants no data access of its own for reads. (The `report_config` registry is read separately, as the app service principal.)
- There is **no fallback**: if the OBO header is absent, the request is rejected (401) — no CLI profile, no mock data.

## Download gating

Download is generic — it applies to **every** report and exports the current filtered on-screen view from the per-user cache. It is allowed only when **both** conditions hold, re-checked server-side on every `POST /download`:

1. **Kill switch:** `downloads_enabled(DOWNLOADS_ENABLED)` is true (default true; false for `false`/`0`/`no`/`off`/empty). When off, `POST /download` returns 403 "Downloads are temporarily disabled." and the UI panel is hidden — independent of group membership.
2. **Group membership:** the user is a member of the report's **effective download group**, determined from `current_user.me()` group display names.

`can_download = downloads_enabled(...) AND is_member(me(), effective_download_group(report))`.
`effective_download_group(report)` is the report's `report_config.download_group` when set (stripped), else **derived** as `<view_key>` + `DOWNLOAD_GROUP_SUFFIX` (default `_dl`) — so a report can be gated to an explicit Databricks group, otherwise it follows the view's naming convention. The **same** helper drives both button visibility and server enforcement.

The gate **never fails open**: any error resolving membership degrades to "not allowed" (panel hidden, download denied). The hidden UI panel is never trusted — membership is always re-checked on the server before a file is produced.

## Audit (audit-first)

- On each allowed download the app writes **exactly one** row to `{APP_CATALOG}.{APP_SCHEMA}.download_audit` — capturing only what Databricks does not natively log, i.e. that a download occurred: `report_id`/`report_title`, the report's defining **`source_query`**, user email, report_date (NULL for an "All dates" export), applied-filters summary (`filter_summary`, e.g. `region=ALL; quarter=Q3`), search filter, row count, export format (CSV/XLSX), justification, app version, and timestamp.
- The **user email is the readable address** resolved from `me().user_name` (falling back to display name, then the `X-Forwarded-User` header) — not the numeric forwarded user id.
- The audit log is reviewable in the admin **Audit Log** tab and exportable via `GET /admin/audit.csv`.
- The audit INSERT runs **as the app service principal**, via the default `WorkspaceClient()` that auto-detects the runtime-injected SP credentials — not as the user.
- **Audit-first:** the INSERT must reach `SUCCEEDED` *before* the file is returned. If it fails, the download is blocked (HTTP 500, no file). An app-log line is also emitted so the event surfaces in `databricks apps logs`.

## Admin console (app service principal writes)

The `/admin` console (Report Views, Reports, System Config, Audit Log) is gated by membership of `ADMIN_GROUP`, re-checked server-side on every admin route. Admin **writes run as the app service principal** (mirroring the audit write), so the SP needs Unity Catalog `MODIFY` on `report_config`, `report_view`, and `app_config`, plus `SELECT` on `download_audit` for the Audit Log tab/export. Query **previews** in the Reports builder run **OBO** (the admin only previews data they can read). See [DEPLOY.md](DEPLOY.md) §6.

## Granting access

Add users to the relevant Databricks groups (see the roles table above and [DEPLOY.md](DEPLOY.md) §5):

- add to a view's `view_key` group to grant **view** access to that view's tabs;
- add to `<view_key>_dl` (or the explicit `download_group`) to also grant **download**;
- add to `ADMIN_GROUP` to grant **admin** console access.

All data reads remain OBO, so a user additionally needs their own Unity Catalog `SELECT` on whatever the report's `source_query` reads.

## Known follow-up — account-level federation

The `download_hub_app_users` SELECT grants in `resources/grants.sql` (on `USE CATALOG`, `USE SCHEMA`, and `SELECT ON TABLE`) require the group to be resolvable by Unity Catalog. Under **account-level identity federation**, a group that exists only as a workspace-level SCIM group may not be UC-resolvable, and those grants may fail with `PRINCIPAL_DOES_NOT_EXIST`.

**Remediation:** create or federate `download_hub_app_users` at the **account level** (so it resolves in Unity Catalog), then re-run the three app-users GRANTs from `resources/grants.sql` via the Statement Execution API. The app service principal grants (the audit write) are independent and are unaffected.
