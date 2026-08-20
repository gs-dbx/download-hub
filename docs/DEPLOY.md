# Deploy

Deploying the **Data Download Hub** app to a Databricks workspace. Uses the Databricks CLI and the Databricks Asset Bundle in this repo. The bundle name is `download_hub`; the deployed app is named `download-hub`.

> No secrets or tokens appear in this repo. Authentication is the CLI's OAuth profile and the runtime-injected app service-principal credentials.

## Prerequisites

- A Databricks workspace (AWS or Azure)
- A serverless SQL warehouse (the app executes queries on this warehouse)
- Databricks CLI configured with a profile (default: `DEFAULT`)
- Unity Catalog enabled in the workspace
- The ability to create Databricks groups and apply UC grants

## 1. Configure the bundle

Edit `databricks.yml` to set your workspace and defaults:

```yaml
workspace:
  host: https://your-workspace.cloud.databricks.com

variables:
  catalog:
    default: "main"        # Catalog for report_config + download_audit
  schema:
    default: "default"     # Schema for report_config + download_audit
  warehouse_id:
    default: "<YOUR-WAREHOUSE-ID>"   # Serverless SQL warehouse
  app_users_group:
    default: "download_hub_app_users"
  download_users_group:
    default: "download_hub_download_users"
```

## 2. Validate + deploy the bundle

The app resource uses the **standard** bundle engine (no `genie_spaces` or other direct-engine-only resource is involved):

```bash
cd /path/to/repo root (monorepo directory)
databricks bundle validate -t dev
databricks bundle deploy   --target dev
```

This syncs `src/app/` to the workspace and creates/updates the `download-hub` app resource (`resources/app.yml`), including its OBO `sql` user scope and the CAN_USE binding to your warehouse.

## 3. Create the data tables and seed sample data

The `metrics_seed` job creates the registry + support tables — `report_config`, `report_view`, `app_config`, and `download_audit` — and populates `daily_metrics` with sample data:

```bash
databricks bundle run metrics_seed --target dev
```

This runs the seed notebook once. The notebook is idempotent (`CREATE TABLE IF NOT EXISTS`, table overwrites, and column-add migrations), so rerunning is safe. Its migrations also upgrade older installs in place — adding `report_config.view_key`/`updated_by`, `download_audit.source_query`, and `download_audit.filter_summary` (backfilled from the legacy `drain_filter`).

## 4. Start / restart the app

```bash
databricks bundle run download_hub --target dev
databricks apps get download-hub   # expect ACTIVE / SUCCEEDED
```

**First start note:** the first start (and any start after a `requirements.txt` change) rebuilds the Python virtualenv from `src/app/requirements.txt` inside the Apps runtime; this can take several minutes before the app reports ACTIVE. Subsequent restarts with unchanged requirements are fast.

## 5. Create the groups

Access is group-based, with three roles (see [PERMISSIONS.md](PERMISSIONS.md)):

- **View group — one per view.** A view's `view_key` (in `report_view`) *is* a Databricks group; members see that view's report tabs. Create one group per view you define (e.g. `efile_ops`, `efile_exec`).
- **Download group.** `<view_key>` + `DOWNLOAD_GROUP_SUFFIX` (default `_dl`), e.g. `efile_ops_dl` — or an explicit `report_config.download_group`. Members can export that view's reports. (A member of the view group only can view but not download.)
- **Admin group.** `ADMIN_GROUP` (default `download_hub_admin_users`) — members can use the `/admin` console to manage views, reports, the disclaimer, and the audit log.

Add members via the Databricks UI (Groups → Members) or SCIM API:

```bash
# Example: add a user to download_hub_download_users (replace <GROUP-ID> and <USER-ID>)
databricks api patch /api/2.0/preview/scim/v2/Groups/<GROUP-ID> --json '{
  "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
  "Operations": [{"op": "add", "path": "members", "value": [{"value": "<USER-ID>"}]}]
}'
```

Membership is re-checked server-side on every `POST /download` via `current_user.me()` — the UI panel is never trusted on its own.

## 6. Apply the Unity Catalog grants

`resources/grants.sql` holds the grants for the two grantees (app-users group + app service principal). After deploying, get the app's service principal ID and update `grants.sql`:

```bash
# Get the app's service principal client ID
databricks apps get download-hub

# In the output, note "service_principal_client_id"
# Replace all occurrences of <APP-SERVICE-PRINCIPAL-CLIENT-ID> in resources/grants.sql with this value
```

Then apply the grants statement by statement via the Statement Execution API:

```bash
# For each GRANT statement in resources/grants.sql:
databricks api post /api/2.0/sql/statements --json '{
  "warehouse_id": "<YOUR-WAREHOUSE-ID>",
  "statement": "<one GRANT from resources/grants.sql>",
  "wait_timeout": "30s"
}'
```

Or run them all at once (if your warehouse supports it):
```bash
cat resources/grants.sql | databricks sql --warehouse-id <YOUR-WAREHOUSE-ID>
```

### App service-principal grants for the admin console

The `/admin` console writes as the app service principal, so the SP needs write access to the registry tables plus read access to the audit table (replace `<APP-SP-CLIENT-ID>`):

```sql
GRANT MODIFY ON TABLE <catalog>.<schema>.report_config TO `<APP-SP-CLIENT-ID>`;
GRANT MODIFY ON TABLE <catalog>.<schema>.report_view   TO `<APP-SP-CLIENT-ID>`;
GRANT MODIFY ON TABLE <catalog>.<schema>.app_config     TO `<APP-SP-CLIENT-ID>`;
GRANT SELECT ON TABLE <catalog>.<schema>.download_audit TO `<APP-SP-CLIENT-ID>`;  -- audit log tab/export
```

(The SP already reads `report_config`/`report_view`/`app_config` and inserts into `download_audit` as part of normal operation; `MODIFY` additionally lets admins create/edit rows, and `SELECT` on `download_audit` powers the Audit Log tab.)

> **Known follow-up (account-level federation):** The three `download_hub_app_users` SELECT grants require the group to be resolvable in Unity Catalog. Under account-level identity federation, a group that exists only as a workspace-level SCIM group may not resolve. If a grant fails with `PRINCIPAL_DOES_NOT_EXIST`, see [PERMISSIONS.md](PERMISSIONS.md) — federate the group at the account level first, then re-run the three app-users GRANTs. The app service principal grants (audit write) are unaffected.

## 7. Configure & rebrand (optional)

Edit `src/app/app.yaml` to customize branding and settings:

```yaml
env:
  - name: APP_NAME
    value: "Your App Name"
  - name: APP_ORG_NAME
    value: "Your Organization"
  - name: APP_LOGO
    value: "/static/img/logo.svg"
  - name: DOWNLOADS_ENABLED
    value: "true"   # false/0/no/off disables downloads
  - name: ADMIN_GROUP
    value: "download_hub_admin_users"   # members can use /admin
  - name: DOWNLOAD_GROUP_SUFFIX
    value: "_dl"                        # download group = <view_key> + suffix
  - name: MAX_DOWNLOAD_ROWS
    value: "100000"                     # CSV export cap; over it → HTTP 413
  - name: MAX_XLSX_ROWS
    value: "25000"                      # Excel export cap (openpyxl is heavier)
```

> The download disclaimer is normally edited in the admin **System Config** tab (stored in `app_config`); `DOWNLOAD_DISCLAIMER` in `app.yaml` remains a fallback when the table has no value.

Then redeploy and restart:

```bash
databricks bundle deploy --target dev
databricks bundle run download_hub --target dev
```

## 8. Global kill switch — `DOWNLOADS_ENABLED`

Downloads default to enabled. To globally disable downloads (panel hidden + `POST /download` returns 403, independent of group membership), set `DOWNLOADS_ENABLED` to a falsey value:

```yaml
- name: DOWNLOADS_ENABLED
  value: "false"     # "true" re-enables downloads
```

Redeploy and restart to apply.

## 9. Staging / prod promotion

The bundle defines `dev`, `staging`, and `prod` targets in `databricks.yml`. Promote by re-running the same validate/deploy/seed/run steps against the target (`-t staging` / `-t prod`), after creating the two groups and applying `resources/grants.sql` in that environment.

Example for staging:
```bash
databricks bundle validate -t staging
databricks bundle deploy   -t staging
databricks bundle run metrics_seed -t staging
databricks bundle run download_hub -t staging
```

Then repeat steps 5–6 (create groups, apply grants) in the staging environment.
