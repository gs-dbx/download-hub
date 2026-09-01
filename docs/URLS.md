# URL and Route Reference

The app is mounted at the origin root. Replace `https://<app-host>` in the
examples with the Databricks App URL. All routes are same-origin and require the
Apps runtime to supply the signed-in user's forwarded identity headers unless a
route is explicitly described as a health endpoint.

## User-facing pages

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | Redirect to the first report visible to the signed-in user. |
| `GET` | `/collection/{collection_key}` | Redirect to the first visible resource in a specific resource collection. |
| `GET` | `/report/{report_id}` | Render a query report and its initial result page. |
| `GET` | `/admin` | Render the administration console; admin-group membership is required. |

Use a resource collection URL when sharing a collection rather than a specific
resource:

```text
https://<app-host>/collection/finance_users
```

The collection key is stable: the route redirects each signed-in user to the
first resource they can access in that collection. It returns `404` if the
collection does not exist, `403` if the collection exists but the user cannot
access any of its resources, and `401` when the forwarded identity token is
missing. Collection keys are bare identifiers (letters, digits, and
underscores; no leading digit).

A report URL can preserve the current filtered results in its query string:

```text
https://<app-host>/report/daily_metrics?q=revenue&page=2&size=50&sort=amount&dir=desc&region=west
```

Supported report parameters are:

| Parameter | Meaning |
|---|---|
| `q` | Case-insensitive search text. |
| `page` | One-based page number. |
| `size` | `25`, `50`, `100`, or `all` (internally capped). |
| `sort` | Configured output-column name. |
| `dir` | `asc` or `desc`. |
| `{filter_field}` | One parameter for each filter configured for the report. |

### Filter-field parameters

`{filter_field}` is a placeholder, not a literal parameter name. Each report
defines zero or more filters in its `filters_json` configuration. The `field`
from each configured filter becomes the query-string key and download form-field
name.

For example, this configuration:

```json
[
  {"field": "region", "label": "Region"},
  {"field": "business_unit", "label": "Business unit"}
]
```

creates the parameters `region` and `business_unit`:

```text
GET /report/sales?region=west&business_unit=consumer
GET /report/sales/table?region=west&business_unit=consumer&page=1&size=50
GET /report/sales/sql?region=west&business_unit=consumer
```

Filter behavior:

- A non-empty value adds an equality condition for that field. Multiple filter
  fields are combined with `AND`; the example selects rows where `region =
  'west'` and `business_unit = 'consumer'`.
- Omitting a configured field, or sending it with an empty value such as
  `region=`, means no constraint for that field (the “all values” state).
- Values must be URL-encoded. For example, `business_unit=Research%20%26%20Development`
  represents `Research & Development`. Use a standard URL/query-string builder
  instead of concatenating user input.
- Matching uses the data source's SQL equality semantics, including its type,
  case, and collation behavior. This is different from `q`, which performs a
  case-insensitive contains search across displayed columns.
- Filter values are sent to Databricks SQL as bound parameters; they are never
  interpolated into SQL. The configured field name is validated as a bare SQL
  identifier and must be present in the report query output.
- Unknown query-string keys are ignored. A caller cannot introduce an arbitrary
  filter column by adding a new parameter; only fields in `filters_json` apply.
- Each filter dropdown obtains its choices independently from the distinct,
  non-null values of its configured field.
- Changing filters resets the browser UI to page 1. API-like callers should also
  set `page=1` when changing a filter to avoid requesting a now-empty later page.

The export route uses the same configured field names, but it is a form `POST`
rather than a query string. For example:

```text
report_id=sales
search=revenue
region=west
business_unit=consumer
format=csv
acknowledged=true
justification=Quarterly analysis
```

Only the report's configured filters are read. Missing or empty filter form
fields mean no constraint, matching the report-page behavior. The browser keeps
these hidden export fields synchronized with the visible filter controls so the
download represents the current view.

## Query-report endpoints

| Method | Path | Input/output |
|---|---|---|
| `GET` | `/report/{report_id}/table` | Returns table-row HTML for the report parameters above. Pagination metadata is returned in `X-Total-Rows`, `X-Total-Pages`, `X-Page`, and `X-Fetched-At`. |
| `GET` | `/report/{report_id}/sql` | Returns JSON containing the effective SQL for the current filters and sort. Requires resource visibility. |
| `POST` | `/download` | Creates a gated and audited CSV or XLSX export from form data. |

`POST /download` accepts `report_id`, `search`, `acknowledged`,
`justification`, `format` (`csv` or `xlsx`), and each configured filter field.
A direct result is returned as an attachment. A large CSV is staged in the
app-private export volume and returns JSON containing a user-scoped retrieval
path:

```json
{
  "spilled": true,
  "rows": 250000,
  "filename": "daily_metrics.csv",
  "retrieve_path": "owner-hash/audit-id/daily_metrics.csv"
}
```

The browser then requests:

```text
GET /download/retrieve?path=<URL-encoded-retrieve_path>
```

The retrieval endpoint verifies the signed-in identity and owner namespace,
then streams the object from the app-private volume. Clients must treat
`retrieve_path` as opaque; its internal layout may change.

## Volume-report endpoints

Volume reports expose files from the report's configured `volume_root`. Unlike
the private generated-export volume, these reads run on behalf of the user and
therefore follow that user's Unity Catalog privileges.

| Method | Path | Input/output |
|---|---|---|
| `GET` | `/volume/{report_id}/list?path=<relative_path>` | Returns folder-listing HTML. `path` is relative to the configured root; blank means root. The canonical path is returned in `X-Volume-Path`. |
| `POST` | `/volume/{report_id}/download` | Returns one audited file attachment. Form fields are `path`, `acknowledged`, and `justification`. |

All volume paths are normalized and jailed beneath the configured volume root.

## Administration endpoints

Every administration route requires membership in `ADMIN_GROUP`. Mutation
routes accept browser form data and return JSON or a redirect as appropriate.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/admin/preview` | Validate and preview a proposed query. |
| `POST` | `/admin/report` | Create or update a report configuration. |
| `POST` | `/admin/report/delete` | Delete the report identified by `report_id`. |
| `POST` | `/admin/view` | Create or update a resource collection. |
| `POST` | `/admin/view/delete` | Delete the resource collection identified by its legacy `view_key`. |
| `POST` | `/admin/config` | Save disclaimer, banner, and footer settings. |
| `GET` | `/admin/audit.csv` | Download the recent audit log as CSV. |

These are application endpoints, not a stable external management API. Use the
administration UI unless automating against the current implementation is an
intentional maintenance decision.

The `/admin/view*` route names and `view_key` form field are retained for
backward compatibility. Product terminology calls these resource collections
and collection keys.

## Health endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Lightweight process health response. |
| `GET` | `/health/warehouse` | JSON warehouse status used by the UI badge. |

## Static assets

Authored assets are served below `/static/`, including `/static/css/`,
`/static/js/`, `/static/img/`, and `/static/uswds/`. The app intentionally uses
no external CDN URLs.
