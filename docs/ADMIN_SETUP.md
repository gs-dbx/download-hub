# Databricks Administrator Setup Guide

This guide describes everything a Databricks administrator must configure for
the Data Download Hub to work. It covers workspace access, identity, the SQL
warehouse, Unity Catalog, the app service principal, report groups, volumes,
deployment, and production verification.

Use this as the primary installation checklist. [DEPLOY.md](DEPLOY.md) contains
additional CLI detail, while [PERMISSIONS.md](PERMISSIONS.md) explains the
authorization design.

## 1. Understand the three identities

The app deliberately uses different identities for different operations:

| Identity | Operations | Required access |
|---|---|---|
| Signed-in user (OBO) | Query report data, populate filters, preview admin queries, browse configured volume reports | App `CAN USE`, warehouse `CAN USE`, and UC access to the report source or browsed volume |
| App service principal | Read/write configuration and audit tables; upload and retrieve generated large exports | Warehouse resource binding, registry/audit table grants, and private export-volume grants |
| Deployment operator | Deploy/restart the app, run the seed job, create groups/resources, and apply grants | Workspace and UC administrative permissions appropriate to those actions |

The app does not elevate report reads through its service principal. A user who
cannot query a source in Databricks cannot query it through the app.

## 2. Prerequisites

Before deployment, confirm:

- The target workspace has Unity Catalog enabled.
- Users, groups, and the workspace are identity-federated as required by the
  account. UC grants cannot target workspace-local groups that UC cannot resolve.
- A running or startable SQL warehouse exists. Serverless is recommended.
- The deployment operator can deploy Databricks Asset Bundles and Apps, run a
  job, manage the app, create or use UC objects, and grant privileges.
- Databricks CLI authentication points to the intended workspace:

```bash
databricks auth profiles
databricks current-user me -p <profile>
```

The production app is air-gapped from public package/CDN dependencies: Python
wheels and front-end assets are included in the repository.

## 3. Choose the workspace resources

Record these values before editing configuration:

| Value | Example | Notes |
|---|---|---|
| Workspace host | `https://adb-...azuredatabricks.net` | Must match the CLI profile and deployment target. |
| SQL warehouse ID | `abc123...` | Use the ID, not the display name. |
| App catalog/schema | `main.download_hub` | Holds registry and audit Delta tables. |
| App name | `download-hub` | Defined by the bundle resource. |
| Private export volume | `/Volumes/main/download_hub/exports` | Optional but required to deliver CSV results above the direct-download limit. |

The report sources may live in other catalogs and schemas. Grants must cover
every object referenced by each configured `source_query`.

## 4. Create the access groups

There are three kinds of groups:

| Group | Default/example | Purpose |
|---|---|---|
| View group | `download_hub_app_users` | A report view's `view_key`; members can see reports in that view. |
| Download group | `download_hub_download_users` for the seeded report; otherwise an explicit `download_group` or derived `<view_key>_dl` | Members can see the view and download its reports. |
| Administrator group | `download_hub_admin_users` | Members can open `/admin` and mutate app configuration through the UI. |

Create these as account-level/federated groups when Unity Catalog requires it.
Add users to the smallest necessary group. Download membership does not replace
source-data privileges; downloads still query OBO as the user.

For additional views, create another view group and normally a matching `_dl`
group. The `DOWNLOAD_GROUP_SUFFIX` environment variable changes that suffix.

## 5. Grant access to the Databricks App

In the workspace UI, open **Apps → download-hub → Permissions** and grant
`CAN USE` to every view group, download group, and administrator group that must
open the app. Reserve `CAN MANAGE` for deployment/application operators.

This app-level permission is separate from group membership stored in
`report_view`/`report_config` and separate from Unity Catalog privileges. Users
need all applicable layers.

## 6. Configure the SQL warehouse

Set the same warehouse ID in:

1. `databricks.yml` → `variables.warehouse_id`; this creates the app's warehouse
   resource binding.
2. `src/app/app.yaml` → `DATABRICKS_WAREHOUSE_ID`; this tells application code
   which warehouse to call.

Grant `CAN USE` on the warehouse to:

- every view/download group whose members run report queries;
- the administrator group if admins will preview queries;
- any other user group expected to execute OBO report reads.

The bundle resource binding grants the app service principal access to the
warehouse resource. It does not replace an OBO user's own warehouse permission.

## 7. Configure the bundle and app environment

Set the target workspace and resource variables in `databricks.yml`:

```yaml
workspace:
  host: https://<workspace-host>

variables:
  catalog:
    default: main
  schema:
    default: download_hub
  warehouse_id:
    default: <warehouse-id>
  app_users_group:
    default: download_hub_app_users
  download_users_group:
    default: download_hub_download_users
```

Set the matching runtime values in `src/app/app.yaml`:

```yaml
env:
  - name: DATABRICKS_WAREHOUSE_ID
    value: "<warehouse-id>"
  - name: APP_CATALOG
    value: "main"
  - name: APP_SCHEMA
    value: "download_hub"
  - name: ADMIN_GROUP
    value: "download_hub_admin_users"
  - name: DOWNLOAD_GROUP_SUFFIX
    value: "_dl"
  - name: APP_EXPORT_VOLUME
    value: "/Volumes/main/download_hub/exports"
```

Optional settings include branding, disclaimer text, download limits, export
page size, and the global `DOWNLOADS_ENABLED` kill switch. See
[CONFIGURATION.md](CONFIGURATION.md).

Do not add `DATABRICKS_CLIENT_ID`, `DATABRICKS_CLIENT_SECRET`, user tokens, or
other secrets to source control. Databricks Apps injects the app service
principal credentials, and the Apps proxy supplies the signed-in user's OBO
token.

## 8. Deploy and initialize the schema

From the repository root:

```bash
databricks bundle validate -t <target> -p <profile>
databricks bundle deploy -t <target> -p <profile>
databricks bundle run metrics_seed -t <target> -p <profile>
databricks bundle run download_hub -t <target> -p <profile>
databricks apps get download-hub -p <profile>
```

Run `metrics_seed` before validating the UI. It creates/migrates:

- `report_config`
- `report_view`
- `app_config`
- `download_audit`
- `config_audit`
- sample `daily_metrics` data and optional demo volumes

The seed job is intended for initial/sample setup and schema migration. Review
its sample objects before using it in a production catalog.

Wait until `databricks apps get download-hub` reports the app as active. Note
the returned `service_principal_client_id`; it is the principal used in the next
step and differs between workspaces.

## 9. Grant the app service principal

Use the application/client ID returned by `databricks apps get`, enclosed in
backticks in SQL. Do not use the display name or numeric object ID.

For the app catalog/schema:

```sql
GRANT USE CATALOG ON CATALOG main TO `<app-sp-client-id>`;
GRANT USE SCHEMA ON SCHEMA main.download_hub TO `<app-sp-client-id>`;
```

Registry and audit grants:

| Object | App SP privileges | Why |
|---|---|---|
| `report_config` | `SELECT`, `MODIFY` | Load reports; admin save/delete. |
| `report_view` | `SELECT`, `MODIFY` | Load views; admin save/delete. |
| `app_config` | `SELECT`, `MODIFY` | Load/save banner, footer, and disclaimer. |
| `download_audit` | `SELECT`, `MODIFY` | Audit-first download logging and admin audit view. |
| `config_audit` | `SELECT`, `MODIFY` | Configuration change log. |

Example:

```sql
GRANT SELECT, MODIFY ON TABLE main.download_hub.report_config
  TO `<app-sp-client-id>`;
```

Apply the complete template in `resources/grants.sql`, replacing its catalog,
schema, group, source-object, and service-principal placeholders.

## 10. Grant report users access to source data

Query reports execute as the signed-in user. For each report/view, grant both
the view group and its download group the privileges required by the report's
`source_query`:

```sql
GRANT USE CATALOG ON CATALOG main TO `finance_reports`;
GRANT USE SCHEMA ON SCHEMA main.finance TO `finance_reports`;
GRANT SELECT ON TABLE main.finance.monthly_budget TO `finance_reports`;

GRANT USE CATALOG ON CATALOG main TO `finance_reports_dl`;
GRANT USE SCHEMA ON SCHEMA main.finance TO `finance_reports_dl`;
GRANT SELECT ON TABLE main.finance.monthly_budget TO `finance_reports_dl`;
```

Repeat for every catalog, schema, table, view, function, or other dependency
used by the query. Test the exact `source_query` while signed in as a typical
member; administrator ownership is not evidence that end users can run it.

Administrators who use **Run query** in `/admin` also need OBO `SELECT` access to
the sources they preview.

## 11. Configure volumes correctly

There are two distinct volume models.

### Generated large-export volume

`APP_EXPORT_VOLUME` is private application storage. Create it and grant only the
app service principal:

```sql
CREATE VOLUME IF NOT EXISTS main.download_hub.exports;
GRANT READ VOLUME, WRITE VOLUME ON VOLUME main.download_hub.exports
  TO `<app-sp-client-id>`;
```

The app queries rows OBO, stages the CSV, and uploads/retrieves the generated
file as its service principal after enforcing authorization and ownership.

Do **not** blindly grant end users or download groups access to this volume.
Configure a retention/cleanup job because successful exports use unique audit-ID
directories and are not overwritten.

### User-browsable volume reports

A `kind='volume'` report points at a configured `volume_root`. Listing and file
downloads run OBO as the signed-in user. Grant the report's view and download
groups `USE CATALOG`, `USE SCHEMA`, and `READ VOLUME` on that specific volume.
Do not grant `WRITE VOLUME` unless users need it outside this application.

## 12. Configure reports in `/admin`

An administrator needs app `CAN USE`, membership in `ADMIN_GROUP`, warehouse
`CAN USE`, and source-data access for query previews.

For a query report:

1. Create/select a view. Its `view_key` must match the Databricks view group.
2. Enter a single `SELECT` as `source_query`.
3. Choose **Run query**. The app reads result-schema metadata, displays each SQL
   type, and suggests `int`, `float`, or `text`; the admin can override it.
4. Select displayed columns, labels, formats, optional aggregation, and filters.
5. Configure dates as ordinary filters. The legacy `date_field` registry column
   is unused.
6. Set ordering, enablement, and an optional explicit download group.
7. Save, then test as a non-admin member of the intended group.

Every filter field and selected display/order column must be present in the
query output and use a bare identifier. Filter values are bound parameters.

## 13. Production verification checklist

Test with actual identities, not only the deployment operator:

- [ ] App reports active and the expected `APP_VERSION` appears in the footer.
- [ ] View-only user can open the app and sees only intended views/reports.
- [ ] Unauthorized user cannot open the app or report.
- [ ] Typical user can run the report query and populate every filter.
- [ ] Download-group user sees the button; view-only user does not.
- [ ] Direct CSV and XLSX downloads require acknowledgement and justification.
- [ ] A large CSV is saved to the private export volume and retrieved through
      the app; the user has no direct volume grant.
- [ ] A configured volume report can be browsed and downloaded by authorized
      users but not by others.
- [ ] `/admin` is available only to the administrator group.
- [ ] Admin report/view/config changes appear and create `config_audit` rows.
- [ ] Every successful download creates one `download_audit` row.
- [ ] Disabling `DOWNLOADS_ENABLED` hides and blocks downloads.
- [ ] Export-volume retention is scheduled and monitored.

Browser testing is required for OBO flows. A direct bearer-token `curl` request
does not reproduce the Databricks Apps proxy's forwarded user headers.

## 14. Troubleshooting by symptom

| Symptom | Check |
|---|---|
| User cannot open the app | App permission (`CAN USE`) and group membership. |
| Report visible but query fails | User warehouse `CAN USE`; source `USE CATALOG`, `USE SCHEMA`, and `SELECT`; configured output/filter names. |
| Admin page returns 403 | Membership in `ADMIN_GROUP` and app `CAN USE`. |
| Admin preview fails | Admin warehouse and source-data privileges. |
| Reports do not load | App SP `SELECT` on registry tables; `APP_CATALOG`/`APP_SCHEMA`; warehouse ID. |
| Admin save/config silently has no effect | App SP `MODIFY` on the corresponding registry table and `config_audit`. |
| Download is blocked by audit failure | App SP `MODIFY` on `download_audit`. |
| Large export says no volume configured | Set `APP_EXPORT_VOLUME`, restart, and verify private-volume SP grants. |
| Large export volume access denied | App SP `USE CATALOG`, `USE SCHEMA`, `READ VOLUME`, and `WRITE VOLUME`. Do not solve this with user grants. |
| UC says `PRINCIPAL_DOES_NOT_EXIST` | Create/federate the group at account level, then retry the grant. |
| First request is slow | Confirm warehouse state; serverless warehouses may cold-start. |

Useful checks:

```bash
databricks apps get download-hub -p <profile>
databricks apps logs download-hub -p <profile>
```

```sql
SHOW GRANTS ON TABLE main.download_hub.report_config;
SHOW GRANTS ON TABLE main.download_hub.download_audit;
SHOW GRANTS ON VOLUME main.download_hub.exports;
```
