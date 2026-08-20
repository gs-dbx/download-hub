# Authoring reports in `report_config`

The app is a **config-driven multi-report portal**. Every tab, its columns, its filters, its date selector, and its gated download are defined by one row in the `{APP_CATALOG}.{APP_SCHEMA}.report_config` registry — no code change or redeploy is needed to add, reorder, enable, or disable a report.

## How the registry is read

- The app reads `report_config` **as the app service principal** (not as the user) and TTL-caches the parsed rows in-process for ~300 seconds. A newly MERGE'd or edited row therefore appears within ~5 minutes without a redeploy (restart the app to pick it up immediately).
- Only rows with `enabled = true` are shown; they are ordered by `display_order`.
- The report's **data** is still read **as the signed-in user (OBO)** — Unity Catalog enforces the user's own SELECT on whatever the `source_query` reads. The registry read and the data read are separate identities.

## Row shape

| Column | Type | Meaning |
| --- | --- | --- |
| `report_id` | STRING | Stable registry key (a bare identifier, e.g. `daily_metrics`). The MERGE key. |
| `title` | STRING | Human-facing tab/report title. Recorded in the audit row. |
| `source_query` | STRING | The full `SELECT` the report reads (a single statement; wrapped as a subquery `FROM ( … ) AS _q`). |
| `date_field` | STRING | Optional date/timestamp column to scope by (drives the date selector). `NULL`/empty → no date selector; all rows show. |
| `columns_json` | STRING | JSON array of display columns (see below). Empty/`NULL` → show every column the query returns. |
| `filters_json` | STRING | JSON array of filter dropdowns (may be empty/omitted). |
| `order_by` | STRING | Optional column to `ORDER BY` (or `NULL` for no ordering). |
| `display_order` | INT | Sort order among enabled reports (1 = first tab). |
| `enabled` | BOOLEAN | Whether the report is active. |
| `download_group` | STRING | Optional per-report download group (`NULL` → code default). |
| `updated_at` | TIMESTAMP | Bookkeeping. |

### `columns_json`

A JSON array of `{"name", "label", "format"}` objects:

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
  `pct` (signed one-decimal percentage; `—` when NULL), or `text` (raw string).
  Unknown values are tolerated and treated as `text`.

### `filters_json`

A JSON array of `{"field", "label"}` objects:

```json
[{"field": "channel", "label": "Channel"}]
```

Each filter renders a dropdown whose options are the distinct values of `field`
in the current date's snapshot; it defaults to the first distinct value.

> **The filter `field` must be a column the query returns.** When `columns_json`
> is set, the per-user snapshot selects `display columns ∪ filter fields`, so a
> filter field that is not returned by `source_query` will break the read. When
> `columns_json` is empty the snapshot selects `*`, so any returned column is
> filterable. Filter fields do not need to appear in `columns_json`.

## Adding or updating a report

Add/update rows with an **idempotent MERGE on `report_id`** in the seed notebook (`src/notebooks/generate_daily_metrics.py`) so reruns never overwrite live edits or duplicate rows:

```python
from delta.tables import DeltaTable

DeltaTable.forName(spark, f"{schema_fqn}.report_config").alias("t").merge(
    new_rows_df.alias("s"), "t.report_id = s.report_id"
).whenMatchedUpdateAll().whenNotMatchedInsertAll().execute()
```

Enable/disable a report by toggling `enabled`; reorder tabs via `display_order`.

## Download applies to every report

Download is generic: any report gets a group-gated download that exports the **current filtered on-screen view** (all matching rows, no pagination) from the per-user cache, with the data-handling disclaimer at the top of the file.

- Gating: `downloads_enabled(...) AND is_member(me(), effective_download_group(report))`.
- `effective_download_group(report)` = the report's `download_group` when set (stripped), else the code default `auth.DOWNLOAD_GROUP` (`download_hub_download_users`). Set `download_group` to gate a specific report to a different Databricks group.
- Each download writes exactly one audit row to `{APP_CATALOG}.{APP_SCHEMA}.download_audit` (audit-first) carrying `report_id`/`report_title` and an applied-filters summary. See [PERMISSIONS.md](PERMISSIONS.md).

## Injection safety

- **VALUES** (the selected date and filter selections) are ALWAYS bound as
  `:named` Statement Execution parameters — never interpolated into SQL.
- **IDENTIFIERS** (column names, filter fields, `order_by`) come from
  admin-authored config and cannot be bound, so each is validated against a
  strict allowlist (`^[A-Za-z_][A-Za-z0-9_]*$`) at query-build time; a bad
  identifier raises `ValueError` rather than reaching the warehouse.
- The **`source_query`** is admin-authored SQL, validated to be a single
  statement (no embedded `;`) and wrapped as a subquery. Treat write access to
  `report_config` as trusted.
