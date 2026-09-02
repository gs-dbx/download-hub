# Data Download Hub Administrator and User Guide

This guide explains why the Data Download Hub exists, how people use it, how
Databricks enforces access, what is audited, and how administrators install and
onboard resources. Use it with the detailed [setup checklist](ADMIN_SETUP.md).

## Why the app exists

Organizations often need to let people find, filter, and download governed data
without requiring notebooks, SQL editors, or broad storage access. The Hub is a
controlled front door that leaves authorization with Databricks:

- users see resources assigned to their resource collections;
- reads run on behalf of the signed-in user (OBO), so Unity Catalog remains the
  final authority;
- downloads require a separate entitlement, acknowledgement, and justification;
- released downloads are audit-first;
- large query exports are staged in app-private storage and streamed safely;
- administrators add resources through configuration rather than an app release.

The app is not an authorization bypass, a replacement for Unity Catalog, or a
general file browser. It is a governed presentation and delivery layer.

## Concepts

| Term | Meaning |
|---|---|
| Resource | A query-backed result or pinned Unity Catalog volume folder. Legacy code may call it a report. |
| Resource collection | An ordered group of resources. Its collection key is also its Databricks access-group name. |
| Collection access group | Members may discover and open that collection, subject to OBO data privileges. |
| Download group | Members may see the collection and request downloads. It is explicit or derived as `<collection_key>_dl`. |
| Administrator group | Members may open `/admin`; this alone grants no source-data access. |
| OBO | Databricks executes the data operation using the signed-in user's forwarded OAuth token. |
| App service principal | App identity used for registry/audit writes and private export storage, not to elevate user reads. |

For compatibility, storage retains `report_config`, `report_view`, and
`view_key`, while admin endpoints retain `/admin/report` and `/admin/view`.

## Access control and OBO

A user reaches data only when every applicable layer allows it:

1. The user has `CAN USE` on the Databricks App.
2. The user belongs to the collection access group or its download group.
3. Query users have `CAN USE` on the configured SQL warehouse.
4. The user has required Unity Catalog privileges: normally `USE CATALOG`, `USE
   SCHEMA`, and `SELECT`, `EXECUTE`, or `READ VOLUME`.
5. Downloads additionally require download-group membership and
   `DOWNLOADS_ENABLED=true`.

Failure never makes the app fall back to its service principal. Administrators
need the same OBO source privileges when using **Run query**.

For query resources, filters, search, sorting, count, and pagination run in SQL
as the user. Values are bound parameters and identifiers are validated. For
browsable volume resources, listing and reads run OBO and remain jailed beneath
the configured `volume_root`.

`APP_EXPORT_VOLUME` is different: it is private working storage for generated
large CSVs. Only the app service principal needs `READ VOLUME` and `WRITE
VOLUME`. Never blindly grant end users access. Retrieval verifies the user's
identity and owner-scoped path before streaming the object.

## Auditing

The app maintains two audit trails.

### Download audit

Downloads are fail-closed and audit-first. Before returning a query export or
volume file, the app records actor, time, resource, selected filters, format,
acknowledgement, justification, app version, and source details. If the
`download_audit` insert fails, no data is released. Large exports use the same
audit identifier in their private storage path.

### Administrative change audit

Every persistent admin-console mutation calls the `config_audit` writer:

| Operation | Recorded event |
|---|---|
| Create/update resource | `report_config` / `create` or `update`, including submitted configuration |
| Delete resource | `report_config` / `delete` |
| Create/update resource collection | `report_view` / `create` or `update`, including submitted configuration |
| Delete resource collection | `report_view` / `delete` |
| Change disclaimer, banner, or footer | One `app_config` / `set` event per changed key |

Query preview, opening admin, and reading/exporting logs are not mutations.

Configuration auditing is currently best-effort: a registry change is not
rolled back if `config_audit` is unavailable. Operators must grant the app
service principal `SELECT, MODIFY`, monitor for `config_audit write failed`, and
reconcile registry `updated_at`/`updated_by` fields against the change log.
Download auditing remains strictly fail-closed.

The admin **Change log** shows configuration events; **Audit Log** shows
downloads. Apply organizational retention to the Delta tables and separately
clean generated export objects.

## End-user guide

Open the Databricks App URL. Use the resource collection selector and resource
rail to navigate. Share a collection with:

```text
https://<app-host>/collection/<collection_key>
```

The recipient still needs every permission layer; a URL grants nothing.

For query results, select filters, search displayed columns, sort by a heading,
and use paging. Blank filters mean all permitted values. URL state is
bookmarkable. **View SQL** shows the effective query for transparency.

If authorized, choose **Download**, select CSV or XLSX, acknowledge the handling
statement, and provide a meaningful business justification. The export reflects
current filters and search. Small files return directly; large CSVs are fetched
in bounded pages, staged privately, and streamed through the app. XLSX remains
subject to the configured direct-result cap. Volume resources use the same
acknowledgement and justification control for files.

| Symptom | Action |
|---|---|
| No accessible resources | Ask the collection owner to verify app permission and group membership. |
| Resource visible but query denied | Ask the data owner to verify warehouse and UC source grants. |
| Download button absent | Request the separate download entitlement if justified. |
| Download audit failed | Contact the operator; the app correctly withheld the file. |
| Warehouse starting | Wait for the cold start, then retry. |
| Large export fails | Operator checks `APP_EXPORT_VOLUME`, app-SP grants, capacity, and cleanup. |

Never solve a source-access issue by granting users the private export volume.

## Initial setup

Follow [ADMIN_SETUP.md](ADMIN_SETUP.md) for commands and SQL. In summary:

1. Confirm Unity Catalog, identity federation, a SQL warehouse, bundle access,
   and an authorized deployment operator.
2. Choose the app catalog/schema, warehouse, groups, and private export volume.
3. Create administrator, initial collection access, and download groups.
4. Grant app and warehouse `CAN USE` as applicable.
5. Configure matching values in `databricks.yml` and `src/app/app.yaml`.
6. Validate/deploy, run schema initialization, and start the app.
7. Obtain the app service-principal ID and grant registry, audit, and private
   export-volume access—never broad source access for user reads.
8. Grant user groups least-privilege source access.
9. Configure the first collection and resource in `/admin`.
10. Test as admin, collection-only, download, and unauthorized users, including
    a deliberately large export.
11. Configure audit retention, export cleanup, monitoring, ownership, and
    periodic access reviews.

## Onboard a new resource

Treat onboarding as a governance and access change, not only a UI change.

### Intake and design

1. Record business owner, data owner, purpose, audience, classification,
   expected row/export size, refresh expectations, and support contact.
2. Choose query or pinned-volume resource type.
3. Reuse a collection only when audience and handling rules match; otherwise
   create a collection access group and normally its `_dl` group.
4. Decide separately whether downloads are permitted.

### Databricks configuration

1. Create/federate new groups and grant them app `CAN USE`.
2. For queries, grant relevant groups warehouse `CAN USE` and least-privilege UC
   access to every `source_query` dependency.
3. For browsable volumes, grant `USE CATALOG`, `USE SCHEMA`, and `READ VOLUME`
   only on the source volume—not on the private export volume.
4. Test the exact query or path as representative non-admin users.

### Configure and accept

1. Create/select the collection; its key must exactly match its access group.
2. For a query, enter one `SELECT`, run it, review inferred SQL types, and set
   columns, labels, formats, aggregations, filters, and order. Dates are ordinary
   selected filters.
3. For a volume, pin a `/Volumes/<catalog>/<schema>/<volume>/...` root.
4. Override the download group only when `<collection_key>_dl` is unsuitable.
5. Save disabled when practical, check `config_audit`, validate, then enable.
6. Verify collection-only, download, and unauthorized identities; filters,
   empty results, errors, direct downloads, large CSV, and stable collection URL.
7. Record owner, review date, retention expectations, and support path.

## Change and retirement

- Review source schema changes before changing columns or filters.
- Disable a resource immediately if its source or authorization is uncertain.
- Revoke access through groups and UC grants, not only by hiding a resource.
- Preserve audit records per policy and clean export objects independently.
- Reassign/remove resources before deleting a collection; collection deletion
  does not automatically delete attached resources.

Periodically review memberships, app/warehouse/source grants, admins, audit
health, private-volume growth, stale resources, and owner attestations. Test the
global download kill switch and recovery procedure before an incident.

See [ARCHITECTURE.md](ARCHITECTURE.md), [PERMISSIONS.md](PERMISSIONS.md),
[CONFIGURATION.md](CONFIGURATION.md), and [URLS.md](URLS.md) for reference detail.
