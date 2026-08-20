# Configuration Reference

## Environment variables

All app configuration comes from environment variables in `src/app/app.yaml`. Set these before deploying (or edit `app.yaml`, redeploy, and restart).

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATABRICKS_WAREHOUSE_ID` | (required) | Serverless SQL warehouse to execute statements on |
| `APP_CATALOG` | `main` | Unity Catalog holding `report_config`, `download_audit` |
| `APP_SCHEMA` | `default` | Schema holding `report_config`, `download_audit` |
| `APP_NAME` | `Data Download Hub` | App title in the masthead |
| `APP_ORG_NAME` | (falls back to `APP_NAME`) | Logo `alt` text / accessibility label |
| `APP_LOGO` | `/static/img/logo.svg` | Path to logo image (must be `/static/...` for air-gap compliance) |
| `APP_VERSION` | `0.0.0` | Semantic version string (recorded in audit logs) |
| `DOWNLOADS_ENABLED` | `true` | Global kill switch (`false`/`0`/`no`/`off` disables downloads) |
| `DOWNLOAD_DISCLAIMER` | (see below) | Custom data-handling notice (optional; falls back to built-in generic) |
| `ADMIN_GROUP` | `download_hub_admin_users` | Databricks group whose members can use the `/admin` console (manage views + reports) |
| `DOWNLOAD_GROUP_SUFFIX` | `_dl` | Suffix appended to a report's `view_key` to derive its download group when no explicit `download_group` is set |
| `MAX_DOWNLOAD_ROWS` | `100000` | Max rows for a CSV export (the file is built in memory). Over it, the download returns HTTP 413 with a "narrow your filters" message. Raise only if the app container is sized up. |
| `MAX_XLSX_ROWS` | `25000` | Max rows for an Excel export (openpyxl is far heavier per cell than CSV). Over it, the user is asked to narrow filters or choose CSV. |

### How branding resolves

- **App name** — `APP_NAME` (env) → default `Data Download Hub` (fallback)
- **Org label** — `APP_ORG_NAME` (env) → `APP_NAME` (env) → default (fallback)
- **Logo** — `APP_LOGO` (env) → default `/static/img/logo.svg` (fallback). **Must be a `/static/...` path** (no CDN, no external URLs).
- **Disclaimer** — `DOWNLOAD_DISCLAIMER` (env) → built-in `exports.DEFAULT_DISCLAIMER` (fallback)

### Download disclaimer

The disclaimer is shown in the download modal AND embedded at the top of every export (CSV/XLSX).

**Default (built-in):**
```
DATA HANDLING NOTICE.

This data is for authorized use only. By downloading you acknowledge
that you understand and will comply with all applicable data-handling
policies and regulations.
```

**Custom disclaimer:**
Edit `src/app/app.yaml`:
```yaml
- name: DOWNLOAD_DISCLAIMER
  value: |
    YOUR CUSTOM NOTICE HERE.
    Multi-line OK.
    Preserved as-is in the file export.
```

Leave unset (or empty) to use the built-in default.

### Kill switch

Set `DOWNLOADS_ENABLED` to disable downloads globally:

```yaml
- name: DOWNLOADS_ENABLED
  value: "false"   # "true" re-enables
```

Effect:
- UI panel hidden (no download button)
- `POST /download` returns 403 "Downloads are temporarily disabled"
- Independent of group membership — applies to everyone

Re-enable by setting back to `"true"` (or omitting it, defaults to `"true"`).

---

## `report_config` registry

The app reads `{APP_CATALOG}.{APP_SCHEMA}.report_config` once at startup and every ~300 seconds (TTL-cached).

> **Query model.** A report is defined by a full `source_query` (a single `SELECT`), not a bare table. Displayed columns default to every column the query returns; configure `columns_json` only to narrow/relabel/format them. `date_field` and `filters_json` are both optional — omit `date_field` to show all rows with no date selector.

> **Views & access.** Each report also has a **`view_key`** — the view (tab set) it belongs to, which is also the Databricks group that grants view access. A user sees a report if they belong to its `view_key` group **OR** its download group; the download group is `view_key` + `DOWNLOAD_GROUP_SUFFIX` (`_dl`) unless `download_group` is set explicitly. Views are titled/ordered in the **`report_view`** registry (`view_key`, `title`, `display_order`, `enabled`); the `title` shows in the view switcher. A user in more than one view sees a switcher at the top. Admins (members of `ADMIN_GROUP`) manage both registries at **`/admin`** — including a query builder that runs a query and lets them pick the display columns and filters.

### `report_view` registry

| Column | Type | Meaning |
|--------|------|---------|
| `view_key` | STRING | Stable key AND the Databricks group that grants view access (bare identifier). Reports reference it via `view_key`. **MERGE key.** |
| `title` | STRING | Label shown in the view switcher pulldown. |
| `display_order` | INT | Order among views in the switcher. |
| `enabled` | BOOLEAN | Whether the view is active. |
| `updated_at` / `updated_by` | TIMESTAMP / STRING | Bookkeeping (the admin console stamps the editor's email). |

To add a user to a view: add them to the `view_key` Databricks group (and to `<view_key>_dl` to let them download). No app change needed.

### Full schema

| Column | Type | Meaning |
|--------|------|---------|
| `report_id` | STRING | Stable registry key (bare SQL identifier, e.g. `daily_metrics`). **MERGE key.** |
| `title` | STRING | Human-facing report title (shown in the tab; recorded in audit log). |
| `source_query` | STRING | The full `SELECT` the report reads (a single statement; no trailing `;`). The app wraps it as a subquery: `SELECT <cols> FROM ( <source_query> ) AS _q [WHERE …] [ORDER BY …]`. Can be a plain `SELECT * FROM catalog.schema.table` or an arbitrary join/aggregate. |
| `date_field` | STRING | **Optional.** Column name for the date scope (must exist in the query result; a TIMESTAMP/DATE). When set, a date selector is shown and the query is scoped to the picked date. **NULL/empty → no date selector; all rows/dates show.** |
| `columns_json` | STRING | **Optional.** JSON array of `{"name", "label", "format"}` objects (see below). **Empty `[]` or NULL → every column the query returns is shown** (labelled by its own name, `text` format). When set, only the configured columns show, in configured order. |
| `filters_json` | STRING | JSON array of `{"field", "label"}` objects (see below). May be empty `[]` or NULL (→ no filter dropdowns). |
| `order_by` | STRING | Optional column to ORDER BY results (bare identifier, or NULL for no ordering). |
| `display_order` | INT | Sort order among enabled reports (1 = first tab, 2 = second, etc.). |
| `enabled` | BOOLEAN | Whether the report is active (only `true` rows are shown). |
| `download_group` | STRING | Optional explicit per-report download group. If NULL, it is **derived** from `view_key` + `DOWNLOAD_GROUP_SUFFIX` (`_dl`). Set to a Databricks group name to override. |
| `view_key` | STRING | The view (tab set) this report belongs to — also the Databricks group granting view access. References `report_view.view_key`. |
| `updated_at` / `updated_by` | TIMESTAMP / STRING | Bookkeeping (row last modified; admin console stamps the editor's email). |

### `columns_json` format

A JSON array of column descriptors:

```json
[
  {"name": "metric_name", "label": "Metric", "format": "text"},
  {"name": "value_cy", "label": "2026", "format": "int"},
  {"name": "pct_change", "label": "% Change", "format": "pct"}
]
```

Each object has:
- **`name`** (STRING, required) — source column name (bare SQL identifier). Must be a column returned by `source_query`.
- **`label`** (STRING, required) — display header and export column header.
- **`format`** (STRING, optional, default `"text"`) — display format hint:
  - `"text"` — render as-is (raw string)
  - `"int"` — thousands-separated count (e.g., `1,234,567`; Excel formatted as number)
  - `"pct"` — signed one-decimal percentage (e.g., `+12.5%`, `-3.2%`; NULL → `—`)
  - Unknown values treated as `"text"`

### `filters_json` format

A JSON array of filter descriptors:

```json
[
  {"field": "channel", "label": "Channel"},
  {"field": "quarter", "label": "Quarter"}
]
```

Each object has:
- **`field`** (STRING, required) — column name to filter on (bare SQL identifier). **Must be a column returned by `source_query`.** If the column doesn't exist, the OBO read will fail.
- **`label`** (STRING, required) — dropdown label shown on the UI.

**Important:** Every filter field MUST be a column the query returns. When `columns_json` is configured, the per-user snapshot SELECT projects `display_columns ∪ filter_fields`; when it is empty the snapshot projects `*`, so any returned column is filterable.

### Identifier allowlist

Every identifier (column name, filter field, `order_by`) must match the regex:
```
^[A-Za-z_][A-Za-z0-9_]*$
```

Valid: `my_col`, `Col1`, `_internal`, `date`
Invalid: `my-col`, `123col`, `.col`, `col.name`

Bad identifiers raise `ValueError` at query-build time, not runtime. The `source_query` itself is admin-authored SQL: it is validated to be a single statement (no embedded `;`) and wrapped as a subquery, but its inner text is not otherwise parsed — treat write access to `report_config` as trusted.

---

## Adding or updating a report

Use an **idempotent MERGE** on `report_id` so reruns never duplicate rows or overwrite live edits:

```python
from delta.tables import DeltaTable

new_rows_df = spark.createDataFrame(
    [
        (
            "my_report",
            "My Report",
            "SELECT * FROM main.default.my_table",  # source_query (a full SELECT)
            "report_date",                          # date_field (or None for no date scope)
            '[{"name":"col1","label":"Column 1"},{"name":"col2","label":"Count","format":"int"}]',
            '[{"field":"region","label":"Region"}]',
            "col1",
            1,
            True,
            None,
            datetime.now()
        )
    ],
    schema="report_id STRING, title STRING, source_query STRING, date_field STRING, "
           "columns_json STRING, filters_json STRING, order_by STRING, "
           "display_order INT, enabled BOOLEAN, download_group STRING, updated_at TIMESTAMP"
)

DeltaTable.forName(spark, f"{APP_CATALOG}.{APP_SCHEMA}.report_config").alias("t").merge(
    new_rows_df.alias("s"), "t.report_id = s.report_id"
).whenMatchedUpdateAll().whenNotMatchedInsertAll().execute()
```

Or use SQL directly:

```sql
MERGE INTO main.default.report_config t
USING (
  SELECT 'my_report' as report_id, 'My Report' as title, ...
) s
ON t.report_id = s.report_id
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *
```

### Enable / disable a report

Toggle the `enabled` column:
```sql
UPDATE main.default.report_config SET enabled = false WHERE report_id = 'old_report';
UPDATE main.default.report_config SET enabled = true WHERE report_id = 'my_report';
```

### Reorder reports

Change `display_order`:
```sql
UPDATE main.default.report_config SET display_order = 1 WHERE report_id = 'my_report';
UPDATE main.default.report_config SET display_order = 2 WHERE report_id = 'other_report';
```

### Per-report download gating

Set `download_group` to gate downloads to a specific Databricks group:

```sql
UPDATE main.default.report_config
SET download_group = 'my_custom_group'
WHERE report_id = 'my_report';
```

When `download_group` is set (non-NULL, non-empty after stripping), the effective download group for that report is the value. Otherwise, it falls back to the code default (`download_hub_download_users`).

---

## Worked example: a simple financial report

```sql
INSERT INTO main.default.report_config VALUES (
  'monthly_budget',                                       -- report_id
  'Monthly Budget Execution',                            -- title
  'SELECT * FROM main.finance.budget_summary',           -- source_query (a full SELECT)
  'month_end',                                           -- date_field (a column the query returns; NULL for none)
  '[
    {"name":"department","label":"Department","format":"text"},
    {"name":"budget_amt","label":"Budget","format":"int"},
    {"name":"spent_amt","label":"Spent","format":"int"},
    {"name":"pct_spent","label":"% Spent","format":"pct"}
  ]',                                                    -- columns_json
  '[
    {"field":"org_unit","label":"Org Unit"},
    {"field":"cost_center","label":"Cost Center"}
  ]',                                                    -- filters_json
  'department',                                          -- order_by
  2,                                                     -- display_order (second tab)
  true,                                                  -- enabled
  'finance_budget_viewers',                              -- download_group (gate to this group)
  current_timestamp()                                    -- updated_at
);
```

The app will:
1. Show this report as the second tab (display_order=2)
2. Read from `main.finance.budget_summary` as the signed-in user (OBO)
3. Display columns: Department, Budget, Spent, % Spent
4. Offer filters: Org Unit, Cost Center (distinct values from the snapshot)
5. Order results by `department`
6. Gate downloads to members of the `finance_budget_viewers` Databricks group
7. Export CSV/XLSX with the disclaimer at the top

---

## Injection safety

The app enforces strict injection safety:

- **VALUES** (date, filter selections) are ALWAYS bound as `:named` parameters in the Statement Execution API — never interpolated.
- **IDENTIFIERS** (column names, filter fields, `order_by`) come from config and are validated against the allowlist regex (`^[A-Za-z_][A-Za-z0-9_]*$`) at query-build time — bad identifiers raise `ValueError` immediately. The `source_query` is validated to be a single statement and wrapped as a subquery.

This means:
- Bad SQL injection on filter values is impossible (bound parameters)
- Bad SQL injection on identifiers is caught early (regex validation, not runtime)
- Column names with SQL keywords or special characters must not be used (by design)

For example, a column named `select` (a SQL keyword) would fail the identifier regex and raise an error at config-parse time, not at query time — which is correct.
