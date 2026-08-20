# WORKSPACE.md — Databricks Workspace Configuration

This file captures workspace-specific configuration. Committed to .planning/ and referenced by all dbx agents.
Update with `/dbx:workspace-scan` to refresh live state.

---

## Connection

```yaml
workspace_url: https://field-eng-aws-gov-demo-irs-demo.cloud.databricks.us
auth_method: oauth  # Databricks CLI OAuth, profile DEFAULT in ~/.databrickscfg
mcp_server_configured: true  # databricks-v2 MCP server available in Claude Code
```

## Unity Catalog

```yaml
unity_catalog_enabled: true
default_catalog: irs
default_schema: efile
# Full namespace for tables: irs.efile.<table>
# Gold table:  irs.efile.daily_efile_glance
# Audit table: irs.efile.download_audit
```

## Compute

```yaml
default_warehouse_id: 2f225c0740dcd22b  # Serverless Starter Warehouse (shared)
default_warehouse_name: Serverless Starter Warehouse
serverless_enabled: true
default_cluster_policy:  # none — serverless only
```

## Asset Bundles

```yaml
bundle_enabled: true
bundle_targets:
  - dev
  - staging
  - prod
bundle_name: download_hub
```

## Volumes (Unity Catalog)

```yaml
# Optional staging volume for USWDS vendored assets / synthetic seed generation
primary_volume: /Volumes/irs/efile/app_assets
```

## Feature Flags

```yaml
features:
  serverless_jobs: true
  serverless_pipelines: true
  liquid_clustering: true
  predictive_optimization: false
  ai_functions: false
  vector_search: false
  model_serving: false
  databricks_apps: true   # PRIMARY deployment surface for this project
  lakebase: false
```

## Databricks Apps

```yaml
app_name: download-hub
app_runtime: python
# On-behalf-of-user (OBO) authorization so UC permissions of the signed-in
# user are honored when the app queries the gold table.
user_authorization_enabled: true
user_authorization_scopes:
  - sql   # query gold table as the user via SQL warehouse
# Service principal (app identity) used for: writing download_audit rows,
# reading group membership for download gating.
app_resources:
  - type: sql-warehouse
    id: 2f225c0740dcd22b
```

## Access Groups (download gating — BEARS entitlement 1:1)

```yaml
group_app_users: efile_glance_app_users        # basic app access
group_download_users: efile_glance_download_users  # gated download entitlement
# Each Databricks group maps 1:1 to a BEARS entitlement in the target environment.
```

## Existing Resources (auto-populated by /dbx:workspace-scan)

```yaml
existing_jobs: []
existing_pipelines: []
existing_schemas:
  - irs.ocfo   # OCFO Genie synthetic data (separate project)
  - irs.demo   # existing contract data
  # irs.efile  — TO BE CREATED by this project
```

## Team & Governance

```yaml
team_email: greg.skinner@databricks.com
service_principal:  # created by DAB app deployment (download-hub app identity)
data_steward:
notification_emails:
  - greg.skinner@databricks.com
```

## AI Dev Kit

```yaml
ai_dev_kit_path: ~/.ai-dev-kit/repo
```

---

*Last scanned: NEVER — run `/dbx:workspace-scan` to populate live values*
