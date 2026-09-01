-- Unity Catalog grants for the "Data Download Hub" app.
--
-- ACCESS MODEL (see docs/PERMISSIONS.md):
--   * A report belongs to a VIEW, keyed by `view_key` — which is ALSO the
--     Databricks group that grants VIEW access. A user SEES a report if they are
--     a member of its view group OR its download group.
--   * A report's DOWNLOAD group is the explicit `download_group`, else it is
--     derived as `<view_key>` + DOWNLOAD_GROUP_SUFFIX (default `_dl`).
--   * ADMINS are members of `download_hub_admin_users` (env ADMIN_GROUP). Admin
--     WRITES run as the app SERVICE PRINCIPAL, so admins need NO direct UC grant
--     on the registry — group membership alone gates the /admin console.
--
-- WHO NEEDS WHAT
--   * View + download group members READ report DATA on-behalf-of themselves
--     (OBO), so each such group needs USE CATALOG/SCHEMA + SELECT on the tables
--     that its reports' `source_query` reads. Repeat the block below per view.
--   * The app SERVICE PRINCIPAL reads the registry tables and writes the audit +
--     admin registry rows AS ITSELF. In Unity Catalog a service principal is
--     named in GRANT by its APPLICATION ID (client_id), backtick-quoted — NOT the
--     numeric id, NOT the display name. Get it from `databricks apps get
--     download-hub` (service_principal_client_id).
--
-- APPLICATION: these are NOT applied at authoring time. After the groups are
-- created and the app is deployed, apply each statement via the Statement
-- Execution API, e.g.:
--   databricks api post /api/2.0/sql/statements -p DEFAULT --json \
--     '{"warehouse_id":"<WAREHOUSE_ID>","statement":"<each GRANT>","wait_timeout":"30s"}'
--
-- Replace <APP_SERVICE_PRINCIPAL_CLIENT_ID>, <WAREHOUSE_ID>, and the
-- <view_key> / <source_table> placeholders for your environment. `main.default`
-- is the generic catalog.schema — swap for your APP_CATALOG.APP_SCHEMA.

-- ---------------------------------------------------------------------------
-- Per-view DATA access (OBO). Repeat this block for EACH view, substituting the
-- view's key and the table(s) its reports read. Both the view group and its
-- derived download group need SELECT on the data (download-group members also
-- view). Example view_key = `download_hub_app_users` (the default seed view).
-- ---------------------------------------------------------------------------
GRANT USE CATALOG ON CATALOG main TO `download_hub_app_users`;
GRANT USE SCHEMA ON SCHEMA main.default TO `download_hub_app_users`;
GRANT SELECT ON TABLE main.default.daily_metrics TO `download_hub_app_users`;

GRANT USE CATALOG ON CATALOG main TO `download_hub_download_users`;
GRANT USE SCHEMA ON SCHEMA main.default TO `download_hub_download_users`;
GRANT SELECT ON TABLE main.default.daily_metrics TO `download_hub_download_users`;

-- Large CSV delivery (only when APP_EXPORT_VOLUME is configured). Keep the
-- volume private: grant only the app SP, not end-user download groups.
-- GRANT USE CATALOG ON CATALOG <catalog>
--   TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;
-- GRANT USE SCHEMA ON SCHEMA <catalog>.<schema>
--   TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;
-- GRANT READ VOLUME, WRITE VOLUME ON VOLUME <catalog>.<schema>.<export_volume>
--   TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;

-- ---------------------------------------------------------------------------
-- App SERVICE PRINCIPAL. Reads the registry (report_config, report_view,
-- app_config); writes the audit row and — for the /admin console — upserts the
-- registry + config rows. MODIFY covers INSERT/UPDATE/MERGE; SELECT lets the app
-- read them back.
-- ---------------------------------------------------------------------------
GRANT USE CATALOG ON CATALOG main TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;
GRANT USE SCHEMA ON SCHEMA main.default TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;

-- Audit tables: write (audit-first) + read (Audit Log tab / CSV export).
GRANT MODIFY ON TABLE main.default.download_audit TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;
GRANT SELECT ON TABLE main.default.download_audit TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;
GRANT MODIFY ON TABLE main.default.config_audit   TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;
GRANT SELECT ON TABLE main.default.config_audit   TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;

-- Registry tables: read (report/view/config load) + write (admin console upserts).
GRANT SELECT ON TABLE main.default.report_config TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;
GRANT MODIFY ON TABLE main.default.report_config TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;
GRANT SELECT ON TABLE main.default.report_view   TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;
GRANT MODIFY ON TABLE main.default.report_view   TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;
GRANT SELECT ON TABLE main.default.app_config     TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;
GRANT MODIFY ON TABLE main.default.app_config     TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;

-- NOTE: End users are NEVER granted the registry tables — per-user OBO governs
-- report DATA; the registry is metadata the app reads as the service principal.
-- Admins need only membership of `download_hub_admin_users`; their /admin writes
-- run as the service principal above, so no per-admin UC grant is required.
