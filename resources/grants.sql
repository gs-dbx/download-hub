-- Unity Catalog grants for the "Data Download Hub" app (Phase 4 scope).
--
-- Two grantees:
--   1. Group `download_hub_app_users` — READ the gold table via the app's
--      on-behalf-of-user (OBO) authorization. These SELECT lines were authored in
--      Phase 1 but deferred because the group did not exist; the group is created
--      in the Phase 4 checkpoint, so they now apply.
--   2. The app SERVICE PRINCIPAL — writes exactly one audit row per gated download
--      to main.default.download_audit as itself (not the user). In Unity Catalog a
--      service principal is named in GRANT by its APPLICATION ID (client_id),
--      backtick-quoted. The app's runtime identity (injected DATABRICKS_CLIENT_ID)
--      is service_principal_client_id = <APP_SERVICE_PRINCIPAL_CLIENT_ID> — NOT
--      the numeric id, NOT the display name, NOT oauth2_app_client_id. MODIFY
--      covers INSERT; SELECT lets the checkpoint verify the row landed.
--
-- APPLICATION: These statements are NOT applied at authoring time. Apply them at
-- the Phase 4 checkpoint (after the groups are created and the app is deployed)
-- via the Statement Execution API on warehouse <WAREHOUSE_ID>, statement by
-- statement, e.g.:
--   databricks api post /api/2.0/sql/statements -p DEFAULT --json \
--     '{"warehouse_id":"<WAREHOUSE_ID>","statement":"<each GRANT>","wait_timeout":"30s"}'
--
-- NOTE: Replace <APP_SERVICE_PRINCIPAL_CLIENT_ID> with the app's client ID from:
--   databricks apps get download-hub
-- and <WAREHOUSE_ID> with your SQL warehouse ID.

-- Group `download_hub_app_users` — read the gold table (OBO).
GRANT USE CATALOG ON CATALOG main TO `download_hub_app_users`;
GRANT USE SCHEMA ON SCHEMA main.default TO `download_hub_app_users`;
GRANT SELECT ON TABLE main.default.daily_metrics TO `download_hub_app_users`;

-- App service principal (client_id <APP_SERVICE_PRINCIPAL_CLIENT_ID>) — write the
-- audit row as itself (audit-first). MODIFY covers INSERT; SELECT is for verify.
GRANT USE CATALOG ON CATALOG main TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;
GRANT USE SCHEMA ON SCHEMA main.default TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;
GRANT MODIFY ON TABLE main.default.download_audit TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;
GRANT SELECT ON TABLE main.default.download_audit TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;

-- App service principal reads the report registry (metadata, not user data).
-- End users are NOT granted report_config — per-user OBO still governs report DATA.
GRANT SELECT ON TABLE main.default.report_config TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;
