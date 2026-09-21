# Configuration Reference

For the complete workspace, identity, warehouse, Unity Catalog, and
service-principal setup sequence, see [ADMIN_SETUP.md](ADMIN_SETUP.md).

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
| `ADMIN_GROUP` | `download_hub_admin_users` | Databricks group whose members get the `/admin` console |
| `DOWNLOAD_GROUP_SUFFIX` | `_dl` | Suffix appended to a report's `view_key` to derive its download group when `download_group` is unset |
| `DOWNLOADS_ENABLED` | `true` | Global kill switch (`false`/`0`/`no`/`off` disables downloads) |
| `DOWNLOAD_DISCLAIMER` | (see below) | Custom data-handling notice (optional; falls back to built-in generic) |
| `MAX_XLSX_ROWS` | `25000` | Largest Excel export; larger requests direct users to CSV |
| `APP_EXPORT_VOLUME` | (unset) | App-private `/Volumes/...` destination where every export is generated (required for downloads) |
| `MAX_SPILL_ROWS` | `1000000` | Largest CSV export allowed |
| `EXPORT_PAGE_ROWS` | `10000` | Rows processed at a time while producing a CSV |
| `EXPORT_CONCURRENCY` | `2` | Max background export generations running at once (the rest wait as `queued`) |
| `MAX_FILTER_OPTIONS` | `500` | Cap on each filter dropdown's `DISTINCT` scan (bounds page-load cost on large sources) |
| `SESSION_CACHE_TTL_SECONDS` | `45` | TTL of the per-user server-side query cache (page results + filter options); Refresh always bypasses it |

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

### Asynchronous downloads ("My downloads")

Every download is **asynchronous**. `POST /download` validates the request,
writes the immutable audit row (audit-first), records a row in the mutable
`export_jobs` table (status `queued`), and schedules a background task — it never
streams a file. The task generates the export to `APP_EXPORT_VOLUME` as the app
service principal (CSV in bounded `EXPORT_PAGE_ROWS` pages; Excel within
`MAX_XLSX_ROWS`) and flips the job to `ready` (or `failed`). Users track and
retrieve their exports on the **My downloads** page (`GET /downloads`), which
polls `GET /downloads/status`; the download modal itself also polls the single
job and reveals a Download button in place when it's ready. `EXPORT_CONCURRENCY`
bounds how many generations run at once. Each file is stored below a
collision-resistant owner key and unique job ID (`{email_slug}/{job_id}/{file}`),
so exports are user-scoped and never overwrite one another.

Because delivery is async, **`APP_EXPORT_VOLUME` is required** — without it
`POST /download` returns 503. Excel requests above `MAX_XLSX_ROWS`, or CSV above
`MAX_SPILL_ROWS`, are rejected with guidance to narrow the filters. Generated
files are pruned after 24h by the `exports_cleanup` scheduled job (see
`docs/DEPLOY.md`); expired jobs show as `expired` on the My downloads page.

**Interrupted jobs:** on app restart, jobs left `queued`/`running` are reconciled
at startup — if the file already landed on the volume the job is marked `ready`,
otherwise `failed` with a "request it again" message. A job that waits past its
OBO token's lifetime fails the same way (source reads run as the user).

### Server-side session cache

Report page reads (COUNT + rows) and the expensive filter-option `DISTINCT`
scans are cached in-process per **user** (the cache key includes the signed-in
identity, so no cross-user leak) for `SESSION_CACHE_TTL_SECONDS`. The **Refresh**
button evicts a report's cached entries and re-reads on-behalf-of the user. The
cache is per container (not shared across replicas) and is a convenience, not a
source of truth.

---

## `report_config` registry

The app reads `{APP_CATALOG}.{APP_SCHEMA}.report_config` once at startup and every ~300 seconds (TTL-cached).

### Full schema

| Column | Type | Meaning |
|--------|------|---------|
| `report_id` | STRING | Stable registry key (bare SQL identifier, e.g. `daily_metrics`). **MERGE key.** |
| `title` | STRING | Human-facing report title (shown in the tab; recorded in audit log). |
| `kind` | STRING | `query` (default) or `volume`. Selects the report type below. |
| `source_query` | STRING | **Query reports:** a full single-statement `SELECT` (wrapped as `FROM ( … ) AS _q`). NULL for volume reports. (Replaces the retired `source_fqn`.) |
| `volume_root` | STRING | **Volume reports:** pinned UC Volume root, e.g. `/Volumes/<catalog>/<schema>/<volume>`. NULL for query reports. |
| `date_field` | STRING | Legacy compatibility column. Leave NULL; all constraints, including dates, belong in `filters_json`. |
| `columns_json` | STRING | JSON array of column objects (see below). Empty/NULL → show all query columns. |
| `filters_json` | STRING | JSON array of `{"field", "label"}` objects (see below). May be empty `[]` or NULL. |
| `order_by` | STRING | Optional column to ORDER BY results (bare identifier, or NULL for no ordering). Can be any column the `source_query` returns — including one not in `columns_json` (e.g. a hidden `sort_order`); the paging layer projects it internally so ordering by a non-displayed column resolves. |
| `display_order` | INT | Sort order among enabled reports (1 = first tab, 2 = second, etc.). |
| `enabled` | BOOLEAN | Whether the report is active (only `true` rows are shown). |
| `download_group` | STRING | Optional per-report download group. If NULL, derived from `view_key` + `DOWNLOAD_GROUP_SUFFIX` (`_dl`). |
| `view_key` | STRING | Legacy storage name for the collection key: the Databricks group granting access and the resource's collection membership. |
| `updated_at` / `updated_by` | TIMESTAMP / STRING | Bookkeeping (admin console stamps the editor's email). |

Existing registry rows with `date_field` populated are migrated in memory: the
field appears as an ordinary configured filter, and the next admin save clears
`date_field`. This avoids silently broadening older reports during rollout.

### `columns_json` format

When an administrator runs a query in the report builder, the app reads the
Databricks result-schema metadata and suggests `int` for integral columns,
`float` for decimal/floating columns, and `text` for all other types. The SQL
type appears below each column name and the suggested format remains editable.

A JSON array of column descriptors:

```json
[
  {"name": "metric_name", "label": "Metric", "format": "text"},
  {"name": "value_cy", "label": "2026", "format": "int"},
  {"name": "pct_change", "label": "% Change", "format": "pct"}
]
```

Each object has:
- **`name`** (STRING, required) — source column name (bare SQL identifier). Must be selectable from `source_query`.
- **`label`** (STRING, required) — display header and export column header.
- **`format`** (STRING, optional, default `"text"`) — display format hint:
  - `"text"` — render as-is (raw string)
  - `"int"` — thousands-separated count (e.g., `1,234,567`; Excel formatted as number)
  - `"float"` — thousands-separated fixed-decimal number (e.g., `1,234,567.50`); `"double"` is an alias
  - `"pct"` — signed one-decimal percentage (e.g., `+12.5%`, `-3.2%`; NULL → `—`)
  - Unknown values treated as `"text"`

An aggregated column instead carries `{"agg": "sum|min|avg|max|first|last", "source": "<col>", "label": ..., "format": ...}` — the app emits `AGG(source) AS alias` with a join-safe `GROUP BY` over the non-aggregated columns. See [REPORTS.md](REPORTS.md#aggregated-columns).

### `filters_json` format

A JSON array of filter descriptors:

```json
[
  {"field": "channel", "label": "Channel"},
  {"field": "quarter", "label": "Quarter"}
]
```

Each object has:
- **`field`** (STRING, required) — source column name to filter on (bare SQL identifier). **Must be selectable from `source_query`.** If the column doesn't exist, the OBO read will fail.
- **`label`** (STRING, required) — dropdown label shown on the UI.

**Important:** Every filter field MUST exist in the report query's output. Filter values are bound into the SQL `WHERE` and each field's distinct values populate its dropdown, so a field the `source_query` doesn't return breaks the read.

### Identifier allowlist

Every identifier (column name, filter field, `order_by`, `source`)  must match the regex:
```
^[A-Za-z_][A-Za-z0-9_]*$
```

Valid: `my_col`, `Col1`, `_internal`, `date`
Invalid: `my-col`, `123col`, `.col`, `col.name`

Bad identifiers raise `ValueError` at query-build time, not runtime.

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
            "SELECT * FROM main.default.my_table",
            "report_date",
            '[{"name":"col1","label":"Column 1"},{"name":"col2","label":"Count","format":"int"}]',
            '[{"field":"region","label":"Region"}]',
            "col1",
            1,
            True,
            None,
            "my_view_group",
            datetime.now()
        )
    ],
    schema="report_id STRING, title STRING, source_query STRING, date_field STRING, "
           "columns_json STRING, filters_json STRING, order_by STRING, "
           "display_order INT, enabled BOOLEAN, download_group STRING, view_key STRING, "
           "updated_at TIMESTAMP"
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

When `download_group` is set (non-NULL, non-empty after stripping), the effective download group for that report is the value. Otherwise, it is derived from the resource collection's legacy `view_key` + `DOWNLOAD_GROUP_SUFFIX` (`_dl`).

The database retains the `report_view` table and `view_key` column for backward
compatibility. In the product and documentation they represent resource
collections and collection keys; no schema rename or data migration is needed.

---

## Worked example: a simple financial report

Name the columns explicitly (the table has additional columns like `kind`, `volume_root`, and `view_key`, so a positional `VALUES (…)` is fragile):

```sql
INSERT INTO main.default.report_config
  (report_id, title, source_query, date_field, columns_json, filters_json,
   order_by, display_order, enabled, download_group, view_key, updated_at)
VALUES (
  'monthly_budget',                                       -- report_id
  'Monthly Budget Execution',                            -- title
  'SELECT * FROM main.finance.budget_summary',           -- source_query (a full SELECT)
  NULL,                                                  -- legacy date_field (unused)
  '[
    {"name":"department","label":"Department","format":"text"},
    {"name":"budget_amt","label":"Budget","format":"int"},
    {"name":"spent_amt","label":"Spent","format":"int"},
    {"name":"pct_spent","label":"% Spent","format":"pct"}
  ]',                                                    -- columns_json
  '[
    {"field":"month_end","label":"Month end"},
    {"field":"org_unit","label":"Org Unit"},
    {"field":"cost_center","label":"Cost Center"}
  ]',                                                    -- filters_json
  'department',                                          -- order_by
  2,                                                     -- display_order (second tab)
  true,                                                  -- enabled
  'finance_budget_viewers',                              -- download_group (gate to this group)
  'finance_report_viewers',                              -- view_key (legacy name for collection access group)
  current_timestamp()                                    -- updated_at
);
```

The app will:
1. Show this report as the second tab (display_order=2)
2. Read from `main.finance.budget_summary` as the signed-in user (OBO)
3. Display columns: Department, Budget, Spent, % Spent
4. Offer filters: Org Unit, Cost Center (distinct values via an OBO query)
5. Order results by `department`
6. Gate downloads to members of the `finance_budget_viewers` Databricks group
7. Export CSV/XLSX with the disclaimer at the top

---

## Injection safety

The app enforces strict injection safety:

- **VALUES** (date, filter selections) are ALWAYS bound as `:named` parameters in the Statement Execution API — never interpolated.
- **IDENTIFIERS** (column names, filter fields, `order_by`, aggregate `source`) come from config and are validated against the allowlist regex (`^[A-Za-z_][A-Za-z0-9_]*$`) at query-build time — bad identifiers raise `ValueError` immediately.

This means:
- Bad SQL injection on filter values is impossible (bound parameters)
- Bad SQL injection on identifiers is caught early (regex validation, not runtime)
- Column names with SQL keywords or special characters must not be used (by design)

For example, a column named `select` (a SQL keyword) would fail the identifier regex and raise an error at config-parse time, not at query time — which is correct.
