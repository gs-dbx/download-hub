# Authoring reports in `report_config`

The app is a **config-driven multi-report portal**. Every tab, its columns, its filters, its date selector, and its gated download are defined by one row in the `{APP_CATALOG}.{APP_SCHEMA}.report_config` registry — no code change or redeploy is needed to add, reorder, enable, or disable a report.

## How the registry is read

- The app reads `report_config` **as the app service principal** (not as the user) and TTL-caches the parsed rows in-process for ~300 seconds. A newly MERGE'd or edited row therefore appears within ~5 minutes without a redeploy (restart the app to pick it up immediately).
- Only rows with `enabled = true` are shown; they are ordered by `display_order`.
- The report's **data** is still read **as the signed-in user (OBO)** — Unity Catalog enforces the user's own access (SELECT on the `source_query`'s tables for a query report; READ VOLUME on the `volume_root` for a volume report). The registry read and the data read are separate identities.

## Report kinds: `query` vs `volume`

A report is one of two **kinds** (the `kind` column; default `query`):

- **`query`** — the report runs a SQL `SELECT` (`source_query`) and renders the rows as a filterable, sortable, downloadable table.
- **`volume`** — the report browses a single pinned **Unity Catalog Volume** root (`volume_root`): folders first, then files, with breadcrumb traversal into subfolders. Metadata + gated download only (no inline preview). Listing and downloads run **as the signed-in user (OBO)**, so UC `READ VOLUME` grants enforce per-user; downloads use the same acknowledgement + justification + audit-first flow as query reports. Served by `GET /volume/{report_id}/list?path=<root-relative>` (folder fragment) and `POST /volume/{report_id}/download`.

## Row shape

| Column | Type | Meaning |
| --- | --- | --- |
| `report_id` | STRING | Stable registry key (a bare identifier, e.g. `daily_metrics`). The MERGE key. |
| `title` | STRING | Human-facing tab/report title. Recorded in the audit row. |
| `kind` | STRING | `query` (default) or `volume`. |
| `source_query` | STRING | **Query reports:** a full single-statement `SELECT` the app wraps as `FROM ( … ) AS _q` and layers filters / ORDER BY on. `NULL` for volume reports. |
| `volume_root` | STRING | **Volume reports:** the pinned root path (`/Volumes/<catalog>/<schema>/<volume>[/subpath]`); users browse it and its subfolders, jailed to the root. `NULL` for query reports. |
| `date_field` | STRING | Legacy compatibility column. Leave NULL; configure date columns in `filters_json`. |
| `columns_json` | STRING | JSON array of display columns (see below). Empty/NULL → show all query columns. |
| `filters_json` | STRING | JSON array of filter dropdowns (may be empty/omitted). |
| `order_by` | STRING | Optional column to `ORDER BY` (or `NULL` for no ordering). |
| `display_order` | INT | Sort order among enabled reports (1 = first tab). |
| `enabled` | BOOLEAN | Whether the report is active. |
| `download_group` | STRING | Optional per-report download group (`NULL` → derived from `view_key` + suffix). |
| `view_key` | STRING | The Databricks group that grants VIEW access to the report (also names its view/tab). |
| `updated_at` / `updated_by` | TIMESTAMP / STRING | Bookkeeping (the admin console stamps the editor's email). |

### `columns_json`

A JSON array of column objects (query reports). Empty/omitted → every column the query returns is shown.

```json
[
  {"name": "metric_name", "label": "Metric", "format": "text"},
  {"name": "value_cy",    "label": "2026",   "format": "int"},
  {"name": "pct_change",  "label": "% Change","format": "pct"}
]
```

- `name` — the source column (a bare SQL identifier).
- `label` — the header shown on screen and used as the export header.
- `format` — one of `int` (thousands-separated count; numeric in Excel),
  `float` (thousands-separated fixed-decimal number; `double` is an alias),
  `pct` (signed one-decimal percentage; `—` when NULL), or `text` (raw string).
  Unknown values are tolerated and treated as `text`.

#### Aggregated columns

A column may apply an aggregation function to a source column. The app injects it
as `AGG(source) AS alias` and adds a **join-safe `GROUP BY`** over every
non-aggregated selected/filtered column:

```json
[
  {"name": "channel", "label": "Channel", "format": "text"},
  {"agg": "sum", "source": "revenue", "label": "Total Revenue", "format": "float"}
]
```

- `agg` — one of `sum`, `min`, `avg`, `max`, `first`, `last`.
- `source` — the column the function reads. The output alias is `name` when given, else derived as `{source}_{agg}`.
- Every non-aggregated display/filter column is grouped (no bare ungrouped column is emitted). `first`/`last` are non-deterministic without an `order_by`.

The admin console's column builder exposes an **Agg** dropdown per column that serializes into this shape.

### `filters_json`

A JSON array of `{"field", "label"}` objects:

```json
[{"field": "channel", "label": "Channel"}]
```

Each filter renders a dropdown whose options are the distinct values of `field`
(from an OBO distinct-values query); every filter has an "All" option (no constraint).
The selected value is bound into the SQL `WHERE`.

> **The filter `field` must exist in the query output.** Filter values are bound
> into the SQL `WHERE`, so a filter field that is not a real column the
> `source_query` returns will break the read. Filter fields do not need to appear
> in `columns_json`, but they must be selectable from the query.

## Configuring a volume report

Set `kind = 'volume'` and `volume_root` to a single pinned UC Volume path; leave
`source_query`, `columns_json`, and `filters_json` NULL/empty (`date_field` is a
legacy column and remains NULL):

```sql
MERGE INTO <catalog>.<schema>.report_config t
USING (SELECT 'sample_docs' AS report_id) s ON t.report_id = s.report_id
WHEN NOT MATCHED THEN INSERT
  (report_id, title, kind, volume_root, display_order, enabled, view_key,
   updated_at, updated_by)
VALUES
  ('sample_docs', 'Sample Documents', 'volume',
   '/Volumes/<catalog>/<schema>/sample_docs', 2, true, '<view_group>',
   current_timestamp(), 'seed');
```

Users who are members of `view_key` (or its download group) see the report; they
browse folders/subfolders under the root (jailed to it) and download individual
files. Grant the groups `READ VOLUME` on the root — see [PERMISSIONS.md](PERMISSIONS.md).

## Adding or updating a report

Add/update rows with an **idempotent MERGE on `report_id`** in the seed notebook (`src/notebooks/generate_daily_metrics.py`) so reruns never overwrite live edits or duplicate rows:

```python
from delta.tables import DeltaTable

DeltaTable.forName(spark, f"{schema_fqn}.report_config").alias("t").merge(
    new_rows_df.alias("s"), "t.report_id = s.report_id"
).whenMatchedUpdateAll().whenNotMatchedInsertAll().execute()
```

Enable/disable a report by toggling `enabled`; reorder tabs via `display_order`.

### Admin console

Members of the admin group (env `ADMIN_GROUP`) get an `/admin` console to manage
the registry without touching the notebook:

- **Add / edit** a report via a query-preview + column-builder (pick columns, set
  labels/formats, choose an **Agg** function per column, mark filters).
- **Delete** a report (`POST /admin/report/delete`; confirmed, SP write, audited).
- A **Back to reports** link returns to the report pages.

On each query report's page, a **View SQL** disclosure shows the exact
`source_query` being run (accessible, copyable) so users can see what produced the
table.

## Download applies to every report

Download is generic: any report gets a group-gated download that exports the **current filtered on-screen view** with the data-handling disclaimer at the top. Direct results are capped; large CSV results are fetched OBO in bounded pages and delivered through the configured export volume.

- Gating: `downloads_enabled(...) AND is_member(me(), effective_download_group(report))`.
- `effective_download_group(report)` = the report's `download_group` when set (stripped), else the code default `auth.DOWNLOAD_GROUP` (`download_hub_download_users`). Set `download_group` to gate a specific report to a different Databricks group.
- Each download writes exactly one audit row to `{APP_CATALOG}.{APP_SCHEMA}.download_audit` (audit-first) carrying `report_id`/`report_title` and an applied-filters summary. See [PERMISSIONS.md](PERMISSIONS.md).

## Injection safety

- **VALUES** (the selected filter values, including dates) are ALWAYS bound as
  `:named` Statement Execution parameters — never interpolated into SQL.
- **IDENTIFIERS** (column names, filter fields, `order_by`, each dotted part of
  `source_fqn`) come from admin-authored config and cannot be bound, so each is
  validated against a strict allowlist (`^[A-Za-z_][A-Za-z0-9_]*$`) at
  query-build time; a bad identifier raises `ValueError` rather than reaching the
  warehouse.
