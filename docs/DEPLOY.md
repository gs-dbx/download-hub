# Deploy

For the complete Databricks-side configuration checklist—including app access,
OBO warehouse permissions, Unity Catalog grants, administrator privileges, and
the two different volume security models—start with
[ADMIN_SETUP.md](ADMIN_SETUP.md).

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

The `metrics_seed` job creates the `report_config` and `download_audit` tables and populates `daily_metrics` with sample data:

```bash
databricks bundle run metrics_seed --target dev
```

This runs the seed notebook once. The notebook is idempotent (uses `CREATE TABLE IF NOT EXISTS` and table overwrites), so rerunning is safe.

## 4. Start / restart the app

```bash
databricks bundle run download_hub --target dev
databricks apps get download-hub   # expect ACTIVE / SUCCEEDED
```

**First start note:** the first start (and any start after a `requirements.txt` change) rebuilds the Python virtualenv from `src/app/requirements.txt` inside the Apps runtime; this can take several minutes before the app reports ACTIVE. Subsequent restarts with unchanged requirements are fast.

**Same bundle name = update, not duplicate.** Deploying with the same `bundle.name` (`download_hub`) + target + user updates the *existing* `download-hub` app in place — you can deploy from a fresh checkout or a different working directory and it still targets the same workspace deployment (state lives in the workspace under `~/.bundle/download_hub/<target>/`, not in your local dir).

### Verifying a deploy actually landed

`databricks bundle run` deploys the app from the **synced bundle files**, and a restart can take minutes. Don't assume it's live — verify the served version. Bump `APP_VERSION` in `src/app/app.yaml` before deploying, then after `bundle run` completes, confirm the app is serving the new build:

```bash
# App version is rendered in the page footer / admin page.
TOKEN=$(databricks auth token -p DEFAULT | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://<your-app-host>/admin" | grep -o "App version[^<]*"
```

> **Gotcha — don't revert config early.** If you keep the committed `databricks.yml` / `app.yaml` as generic placeholders and inject real values (host, warehouse, catalog/schema, export volume) only at deploy time, do **not** `git checkout` those files until *after* the served version confirms the new build. `bundle run` serves whatever was last synced; reverting before the run completes re-syncs the placeholders and leaves the app stuck on the prior version.

> **OBO pages can't be smoke-tested with a bearer token.** A direct `Authorization: Bearer` request does **not** reproduce the Apps proxy's `X-Forwarded-Access-Token` (on-behalf-of-user) flow, so OBO-gated pages/endpoints answer as a *different* identity and may return a spurious 403. Server-side verification is limited to the served version + markup presence; the real reports / volume-browse / admin / download click-through must be done in a browser signed in as a member of the relevant group.

## 5. Create the two groups

Two Databricks groups gate the app. Create them via the UI or API:

- `download_hub_app_users` — basic app access (can view reports as their own user via OBO)
- `download_hub_download_users` — the gated download entitlement (can export data)

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

> **Known follow-up (account-level federation):** The three `download_hub_app_users` SELECT grants require the group to be resolvable in Unity Catalog. Under account-level identity federation, a group that exists only as a workspace-level SCIM group may not resolve. If a grant fails with `PRINCIPAL_DOES_NOT_EXIST`, see [PERMISSIONS.md](PERMISSIONS.md) — federate the group at the account level first, then re-run the three app-users GRANTs. The app service principal grants (audit write) are unaffected.

### The app service principal must be able to WRITE every registry/audit table

The app SP reads the registry and **writes** several tables (downloads, admin edits, config, change log). It needs `SELECT` **and** `MODIFY` on **all** of these — miss one and the corresponding feature fails, often **silently** (the write errors are swallowed so the page still renders):

| Table | SP needs | Used by | If missing |
|-------|----------|---------|------------|
| `report_config` | SELECT, MODIFY | registry + admin save/delete report | admin resource edits fail |
| `report_view` | SELECT, MODIFY | resource collection switcher + admin save/delete collection | admin collection edits fail |
| `app_config` | SELECT, MODIFY | System Config (disclaimer) | disclaimer save silently no-ops |
| `download_audit` | SELECT, MODIFY | audit-first download logging | **downloads blocked** (audit-first HTTP 500) |
| `config_audit` | SELECT, MODIFY | admin **Change Log** | change log **silently empty** |

`resources/grants.sql` includes all of these — apply the whole file. If the admin Change Log stays empty or the disclaimer won't save after a mutation, check for a missing `config_audit` / `app_config` MODIFY grant first (it was a real gotcha):

```bash
databricks api post /api/2.0/sql/statements --json '{
  "warehouse_id": "<YOUR-WAREHOUSE-ID>",
  "statement": "SHOW GRANTS ON TABLE <catalog>.<schema>.config_audit",
  "wait_timeout": "30s"
}'
# Expect the app SP client id with MODIFY + SELECT.
```

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
  - name: APP_EXPORT_VOLUME
    value: "/Volumes/<catalog>/<schema>/<volume>"
```

For large-result delivery, create that volume first and grant only the app
service principal `READ VOLUME` and `WRITE VOLUME`, plus catalog/schema usage.
Do not grant end-user download groups direct volume access; the app performs
ownership and authorization checks before proxying retrieval. Verify upload and
retrieval while signed in as a member of each group. Configure a scheduled
retention policy or cleanup job for the export
volume; each successful large export uses a unique audit-ID directory so it is
never silently overwritten.

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
