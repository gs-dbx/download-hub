# URL and Route Reference

The app is mounted at the origin root. Replace `https://<app-host>` in the
examples with the Databricks App URL. All routes are same-origin and require the
Apps runtime to supply the signed-in user's forwarded identity headers unless a
route is explicitly described as a health endpoint.

## User-facing pages

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | Redirect to the first report visible to the signed-in user. |
| `GET` | `/report/{report_id}` | Render a query report and its initial result page. |
| `GET` | `/admin` | Render the administration console; admin-group membership is required. |

A report URL can preserve the current view in its query string:

```text
https://<app-host>/report/daily_metrics?date=2026-08-31&q=revenue&page=2&size=50&sort=amount&dir=desc&region=west
```

Supported report parameters are:

| Parameter | Meaning |
|---|---|
| `date` | Selected report date; blank means all dates. |
| `q` | Case-insensitive search text. |
| `page` | One-based page number. |
| `size` | `25`, `50`, `100`, or `all` (internally capped). |
| `sort` | Configured output-column name. |
| `dir` | `asc` or `desc`. |
| `{filter_field}` | One parameter for each filter configured for the report. |

## Query-report endpoints

| Method | Path | Input/output |
|---|---|---|
| `GET` | `/report/{report_id}/table` | Returns table-row HTML for the report parameters above. Pagination metadata is returned in `X-Total-Rows`, `X-Total-Pages`, `X-Page`, and `X-Fetched-At`. |
| `GET` | `/report/{report_id}/sql` | Returns JSON containing the effective SQL for the current date, filters, and sort. Requires report visibility. |
| `POST` | `/download` | Creates a gated and audited CSV or XLSX export from form data. |

`POST /download` accepts `report_id`, `date`, `search`, `acknowledged`,
`justification`, `format` (`csv` or `xlsx`), and each configured filter field.
A direct result is returned as an attachment. A large CSV is staged in the
app-private export volume and returns JSON containing a user-scoped retrieval
path:

```json
{
  "spilled": true,
  "rows": 250000,
  "filename": "daily_metrics_2026-08-31.csv",
  "retrieve_path": "owner-hash/audit-id/daily_metrics_2026-08-31.csv"
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
| `POST` | `/admin/view` | Create or update a report view. |
| `POST` | `/admin/view/delete` | Delete the view identified by `view_key`. |
| `POST` | `/admin/config` | Save disclaimer, banner, and footer settings. |
| `GET` | `/admin/audit.csv` | Download the recent audit log as CSV. |

These are application endpoints, not a stable external management API. Use the
administration UI unless automating against the current implementation is an
intentional maintenance decision.

## Health endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Lightweight process health response. |
| `GET` | `/health/warehouse` | JSON warehouse status used by the UI badge. |

## Static assets

Authored assets are served below `/static/`, including `/static/css/`,
`/static/js/`, `/static/img/`, and `/static/uswds/`. The app intentionally uses
no external CDN URLs.

